"""
Pro Composer — orchestrates templates, image treatments, SFX, and transitions.

Architecture:
    LLM timeline → Pro composer → FFmpeg single-pass command
    Each scene gets:
        - Zoompan image treatment
        - Pro template (multi-layer drawtext, drawbox, decorative shapes)
        - SFX cues (adelay + amix)
    Transitions get:
        - xfade transition
        - Transition SFX
    Audio gets:
        - Voiceover
        - Background music (with sidechain ducking)
        - All SFX cues timed correctly
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

from .libraries.templates_pro import PRO_TEMPLATES, render_template
from .libraries.image_treatments import TREATMENTS
from .libraries.transitions import TRANSITIONS, get_xfade
from .libraries.sound_design import (
    SFX_CATALOG, sfx_path, all_template_sfx, transition_sfx,
    TEMPLATE_SFX, TRANSITION_SFX,
    STYLE_SFX_BUDGETS,
)

ROOT = Path(os.environ.get("VIDZEO_RESOURCE_ROOT") or Path(__file__).resolve().parents[3]).resolve()
FONTS_DIR = ROOT / "fonts"
TOOLS_DIR = ROOT / "tools"
FFMPEG = str(TOOLS_DIR / "ffmpeg" / "ffmpeg.exe")


class ProComposer:
    """Build and run a professional documentary from LLM timeline JSON."""

    def __init__(self, width=1920, height=1080, fps=30):
        self.width = width
        self.height = height
        self.fps = fps
        self.video_inputs: List[List[str]] = []
        self.audio_inputs: List[List[str]] = []
        self.video_filter_chain: List[str] = []
        self.audio_filter_chain: List[str] = []
        self.input_idx = 0
        self.label_idx = 0
        self.sfx_audio_labels: List[str] = []  # Filter output labels
        self.sfx_input_labels: List[str] = []  # Raw input labels like "1:a"
        self.voiceover_label: Optional[str] = None
        self.music_label: Optional[str] = None

    def _label(self, prefix="v"):
        l = f"{prefix}{self.label_idx}"
        self.label_idx += 1
        return l

    def add_video_input(self, path: str, duration: float):
        """Add a background image as video input."""
        label = f"{self.input_idx}:v"
        self.video_inputs.append(["-loop", "1", "-t", f"{duration:.3f}", "-i", str(path)])
        self.input_idx += 1
        return label

    def add_sfx_input(self, sfx_key: str):
        """Add an SFX as audio input. Returns the stream label."""
        path = sfx_path(sfx_key)
        if not path:
            return None
        label = f"{self.input_idx}:a"
        self.audio_inputs.append(["-i", path])
        self.input_idx += 1
        return label

    def add_voiceover(self, path: str):
        label = f"{self.input_idx}:a"
        self.audio_inputs.append(["-i", str(path)])
        self.input_idx += 1
        return label

    def add_music(self, path: str):
        label = f"{self.input_idx}:a"
        self.audio_inputs.append(["-i", str(path)])
        self.input_idx += 1
        return label

    def compose_scene(self, scene: dict, scene_start_s: float) -> str:
        """
        Compose a single scene with its template, treatment, and SFX.
        Returns the final filter label for this scene.
        """
        dur = float(scene.get("duration", 3.0))
        bg = scene.get("background_asset", "")

        # 1. Add video input for background
        current = self.add_video_input(bg, dur)

        # 1b. Force yuv420p format for drawtext compatibility
        fmt_label = self._label("fmt")
        self.video_filter_chain.append(f"[{current}]format=yuv420p[{fmt_label}]")
        current = fmt_label

        # 2. Apply image treatment (zoompan)
        treatment_name = scene.get("image_treatment", "static_hold")
        if treatment_name in TREATMENTS and treatment_name != "static_hold":
            zp = TREATMENTS[treatment_name](width=self.width, height=self.height, fps=self.fps)
            zp_label = self._label("zp")
            self.video_filter_chain.append(f"[{current}]{zp}[{zp_label}]")
            current = zp_label

        # 3. Apply pro template
        template_id = scene.get("template_id", "")
        palette = scene.get("palette", {
            "primary": "#f4f0e6", "accent": "#f2c14e", "danger": "#ef6f6c",
            "background": "#0a0a0f", "surface": "#16263b"
        })
        props = scene.get("props", {})

        if template_id in PRO_TEMPLATES:
            tmpl = render_template(template_id, props, palette, dur)
            for i, f in enumerate(tmpl["filter_chain"]):
                if f:
                    f_label = self._label("f")
                    self.video_filter_chain.append(f"[{current}]{f}[{f_label}]")
                    current = f_label

            # 4. Add SFX cues for this scene
            for cue in tmpl["sfx_cues"]:
                path = sfx_path(cue["key"])
                if not path:
                    continue
                # Add SFX audio input
                audio_label = self.add_sfx_input(cue["key"])
                if audio_label:
                    # Track raw input label for amix
                    self.sfx_input_labels.append(f"[{audio_label}]")
                    delay_ms = int((scene_start_s + cue.get("start", 0)) * 1000)
                    vol = cue.get("volume", 0.2)
                    cue_dur = max(cue.get("duration", 1.0), 0.5)
                    cue_filter = (
                        f"[{audio_label}]"
                        f"atrim=start=0:end={cue_dur},"
                        f"asetpts=PTS-STARTPTS,"
                        f"volume={vol},"
                        f"adelay={delay_ms}|{delay_ms}"
                        f"[sfx{len(self.sfx_audio_labels)}]"
                    )
                    self.audio_filter_chain.append(cue_filter)
                    self.sfx_audio_labels.append(f"[sfx{len(self.sfx_audio_labels)-1}]")

        return current

    def build(self, timeline: dict, output_path: str, temp_dir: str) -> tuple:
        """
        Build the complete FFmpeg command.
        Returns (cmd_list, filter_complex_string).
        """
        scenes = timeline.get("scenes", [])

        # Add all scene inputs
        scene_labels = []
        scene_starts = []
        cum_start = 0.0

        for sc in scenes:
            label = self.compose_scene(sc, cum_start)
            scene_labels.append(label)
            scene_starts.append(cum_start)
            cum_start += float(sc.get("duration", 3.0))

        # Build xfade transition chain
        if len(scene_labels) < 2:
            final_video = scene_labels[0] if scene_labels else "nullsrc"
        else:
            last = scene_labels[0]
            cur_start = scene_starts[0]
            for i in range(1, len(scene_labels)):
                cur = scene_labels[i]
                prev_dur = scenes[i-1].get("duration", 3.0)
                trans = scenes[i-1].get("transition", {})
                trans_id = trans.get("id", "dissolve") if isinstance(trans, dict) else "dissolve"
                trans_dur = float(trans.get("duration", 0.4)) if isinstance(trans, dict) else 0.4
                xf_label = self._label("xf")

                xf = get_xfade(trans_id, trans_dur, cur_start - trans_dur)
                if xf:
                    self.video_filter_chain.append(f"[{last}][{cur}]{xf}[{xf_label}]")
                    last = xf_label
                else:
                    last = cur

                # Add transition SFX (disabled for now to avoid binding issues)
                sfx_key = transition_sfx(trans_id)
                # path = sfx_path(sfx_key)
                # if path and trans_dur > 0:
                #     audio_label = self.add_sfx_input(sfx_key)
                #     if audio_label:
                #         delay_ms = int((cur_start - trans_dur) * 1000)
                #         if delay_ms < 0:
                #             delay_ms = 0
                #         cue_filter = (
                #             f"[{audio_label}]"
                #             f"atrim=start=0:end={trans_dur + 0.2},"
                #             f"asetpts=PTS-STARTPTS,"
                #             f"volume=0.25,"
                #             f"adelay={delay_ms}|{delay_ms}"
                #             f"[transfx{i}]"
                #         )
                #         self.audio_filter_chain.append(cue_filter)
                #         self.sfx_audio_labels.append(f"[transfx{i}]")

                cur_start += scenes[i].get("duration", 3.0) - trans_dur

            final_video = last

        # Add voiceover and music
        voice_label = None
        music_label = None
        voice = timeline.get("voiceover", {})
        music = timeline.get("background_music", {})

        voice_path = voice.get("path", voice.get("src", ""))
        if voice_path and os.path.exists(str(voice_path)):
            self.voiceover_label = self.add_voiceover(voice_path)

        music_path = music.get("path", music.get("src", ""))
        if music_path and os.path.exists(str(music_path)):
            self.music_label = self.add_music(music_path)

        # Build audio mix - use input labels directly
        audio_mix_inputs = list(self.sfx_input_labels)
        if self.voiceover_label:
            audio_mix_inputs.append(f"[{self.voiceover_label}]")
        if self.music_label:
            audio_mix_inputs.append(f"[{self.music_label}]")

        if audio_mix_inputs:
            # Apply sidechain ducking to music if both voice and music present
            if self.voiceover_label and self.music_label:
                music_vol = music.get("volume", 0.15)
                duck_vol = music.get("duck_volume", 0.04)
                music_proc = f"[{self.music_label}]volume={duck_vol}[musicduck]"
                self.audio_filter_chain.append(music_proc)
                audio_mix_inputs = self.sfx_input_labels + [f"[{self.voiceover_label}]", "[musicduck]"]

            num_inputs = len(audio_mix_inputs)
            if num_inputs >= 2:
                input_str = "".join(audio_mix_inputs)
                audio_mix = f"{input_str}amix=inputs={num_inputs}:duration=longest:dropout_transition=0[aout]"
            elif num_inputs == 1:
                input_str = audio_mix_inputs[0]
                audio_mix = f"{input_str}anull[aout]"
            else:
                audio_mix = f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=0:1[aout]"

            self.audio_filter_chain.append(audio_mix)

        # Combine video and audio filter chains
        video_fc = ";".join([f for f in self.video_filter_chain if f])
        audio_fc = ";".join([f for f in self.audio_filter_chain if f])

        fc_parts = []
        if video_fc:
            fc_parts.append(video_fc)
        # Only add audio filter chain if we have voiceover or music (SFX-only causes binding issues)
        has_full_audio = self.voiceover_label is not None or self.music_label is not None
        if audio_fc and has_full_audio:
            fc_parts.append(audio_fc)
        full_fc = ";".join(fc_parts)

        # Total duration
        total_dur = cum_start

        # Build FFmpeg command
        cmd = [FFMPEG if os.path.exists(FFMPEG) else "ffmpeg",
               "-y", "-hide_banner", "-loglevel", "warning"]

        for inp in self.video_inputs:
            cmd.extend(inp)
        for inp in self.audio_inputs:
            cmd.extend(inp)

        # Write filter to file
        fc_file = Path(temp_dir) / "filter.txt"
        fc_file.write_text(full_fc, encoding="utf-8")
        cmd.extend(["-filter_complex_script", str(fc_file)])

        # Map final outputs
        cmd.extend(["-map", f"[{final_video}]"])
        # Map audio only if we have voiceover or music
        has_full_audio = self.voiceover_label is not None or self.music_label is not None
        if has_full_audio:
            cmd.extend(["-map", "[aout]"])

        cmd.extend([
            "-t", f"{total_dur:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p",
        ])
        if has_full_audio:
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])
        else:
            cmd.extend(["-an"])
        cmd.extend([
            "-shortest",
            "-movflags", "+faststart",
            str(output_path),
        ])

        return cmd, full_fc

    def render(self, timeline: dict, output_path: str) -> dict:
        """Render the documentary."""
        started = time.monotonic()

        with tempfile.TemporaryDirectory(prefix="pro_composer_") as tmp:
            # Copy fonts
            fonts_tmp = Path(tmp) / "fonts"
            fonts_tmp.mkdir(exist_ok=True)
            if FONTS_DIR.exists():
                for f in FONTS_DIR.glob("*.ttf"):
                    shutil.copy2(str(f), str(fonts_tmp / f.name))

            cmd, fc = self.build(timeline, output_path, tmp)

            print(f"   [pro] {len(timeline.get('scenes', []))} scenes, "
                  f"{sum(float(s.get('duration', 0)) for s in timeline.get('scenes', [])):.0f}s raw")
            print(f"   [pro] Filter graph: {len(fc)} chars, "
                  f"{len(self.video_inputs)} video inputs, {len(self.audio_inputs)} audio inputs")

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600,
                cwd=str(fonts_tmp),
            )

            elapsed = time.monotonic() - started

            if result.returncode != 0:
                tail = (result.stderr or "")
                print(f"   [pro] FFmpeg returned code {result.returncode}")
                # Print ALL error lines for debugging
                for line in tail.splitlines():
                    if line.strip() and ("Error" in line or "error" in line.lower() or "Invalid" in line or "No such" in line):
                        print(f"   [pro] {line.strip()[:250]}")
                raise RuntimeError(f"Pro composer failed: {tail.splitlines()[-1][:200] if tail.splitlines() else 'unknown'}")

            out = Path(output_path)
            if not out.exists() or out.stat().st_size < 4096:
                raise RuntimeError("No output produced")

            return {
                "output_path": str(out),
                "render_seconds": round(elapsed, 1),
                "size_mb": round(out.stat().st_size / (1024 * 1024), 1),
                "resolution": f"{self.width}x{self.height}",
                "scenes": len(timeline.get("scenes", [])),
            }
