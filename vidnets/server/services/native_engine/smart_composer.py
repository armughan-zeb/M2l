"""
Smart Composer — LLM output → complete FFmpeg filter_complex command.

The LLM generates scene-level editing decisions. The composer resolves these
against the libraries (templates, animations, treatments, transitions, overlays)
and produces a single FFmpeg command for one-pass rendering.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .libraries.text_animations import ANIMATIONS, combo_animation
from .libraries.text_templates import TEMPLATES, TextLayer, TextTemplate
from .libraries.image_treatments import TREATMENTS
from .libraries.transitions import TRANSITIONS, get_xfade
from .libraries.overlays import registered_overlays, get_overlay

ROOT = Path(os.environ.get("VIDZEO_RESOURCE_ROOT") or Path(__file__).resolve().parents[3]).resolve()
FONTS_DIR = ROOT / "fonts"
TOOLS_DIR = ROOT / "tools"
FFMPEG = str(TOOLS_DIR / "ffmpeg" / "ffmpeg.exe")


class SmartComposer:
    """Compile LLM editing decisions into an FFmpeg command."""

    def __init__(self, width=1920, height=1080, fps=30):
        self.width = width
        self.height = height
        self.fps = fps
        self.inputs: List[List[str]] = []
        self.filter_parts: List[str] = []
        self.input_idx = 0
        self.label_idx = 0

    def _label(self, prefix="v") -> str:
        l = f"{prefix}{self.label_idx}"
        self.label_idx += 1
        return l

    def add_image(self, path: str, duration: float) -> str:
        """Register an image input, return its stream label."""
        label = f"{self.input_idx}:v"
        self.inputs.append(["-loop", "1", "-t", f"{duration:.3f}", "-i", str(path)])
        self.input_idx += 1
        return label

    def compose_scene(self, scene: dict, temp_dir: str) -> str:
        """Compose one scene. Returns the output filter label."""
        dur = float(scene.get("duration", 3.0))
        bg = scene.get("background_asset", scene.get("media_src", ""))

        # A missing background used to become a solid-color ``bg_*.png``
        # plate.  That is indistinguishable from a blank text card in the
        # finished video, so native composition now fails clearly and lets the
        # caller retry media resolution instead.
        if not bg or not Path(bg).exists():
            raise FileNotFoundError(
                f"Scene background media is missing or unreadable: {bg or '<empty>'}"
            )

        label = self.add_image(bg, dur)
        current = label

        # ── Image treatment ──
        treatment_name = scene.get("image_treatment", "static_hold")
        if treatment_name in TREATMENTS and treatment_name != "static_hold":
            zp = TREATMENTS[treatment_name](width=self.width, height=self.height, fps=self.fps)
            zp_label = self._label("zp")
            self.filter_parts.append(f"[{current}]{zp}[{zp_label}]")
            current = zp_label

        # ── Pro-style accent bars (Vox design) ──
        palette = scene.get("palette", {"primary": "#f4f0e6", "accent": "#f2c14e", "danger": "#ef6f6c", "background": "#0b1320"})
        accent = palette.get("accent", "#f2c14e")
        # Top thin accent bar (sweeps in)
        top_bar = self._label("bar")
        self.filter_parts.append(
            f"[{current}]drawbox=x=0:y=ih-4:w='iw*min(1\\, t*3)':h=4:color={accent}@1.0:t=fill[{top_bar}]"
        )
        current = top_bar

        # ── Text template ──
        template_id = scene.get("template_id", "")
        template_props = scene.get("props", {})
        palette = scene.get("palette", {})

        if template_id and template_id in TEMPLATES:
            tmpl = TEMPLATES[template_id]
            anim_name = scene.get("animation", tmpl.animation)

            for i, layer in enumerate(tmpl.layers):
                # Resolve template variables
                resolved_color = layer.color
                for color_key, color_val in palette.items():
                    resolved_color = resolved_color.replace("{" + color_key + "}", color_val)
                
                text = layer.text.format(
                    **{k: str(v) for k, v in template_props.items()},
                )
                # Also resolve palette variables in the text itself
                for k, v in palette.items():
                    text = text.replace("{" + k + "}", v)

                # Escape for FFmpeg: colons, single quotes, percent signs, commas in expressions
                text = text.replace(":", "\\:").replace("%", "\\%")
                # Remove single quotes — they break FFmpeg drawtext parsing
                text = text.replace("'", "").replace("\u2019", "'")

                # Build drawtext filter
                parts = [
                    f"text='{text}'",
                    f"fontfile='{layer.font_file}'",
                    f"fontsize={layer.font_size}",
                    f"fontcolor={resolved_color}",
                ]

                # Apply animation to x/y only (alpha not supported in drawtext expressions)
                if anim_name in ANIMATIONS:
                    x_expr, y_expr, _ = ANIMATIONS[anim_name]()
                    parts.append(f"x={x_expr}")
                    parts.append(f"y={y_expr}")
                else:
                    parts.append(f"x={layer.x}")
                    parts.append(f"y={layer.y}")

                if layer.shadow:
                    parts.append("shadowcolor=black:shadowx=3:shadowy=3")
                if layer.box:
                    resolved_box = layer.box_color
                    for color_key, color_val in palette.items():
                        resolved_box = resolved_box.replace("{" + color_key + "}", color_val)
                    parts.append(f"box=1:boxcolor={resolved_box}:boxborderw={layer.box_padding}")

                dt = "drawtext=" + ":".join(parts)
                dt_label = self._label("dt")
                self.filter_parts.append(f"[{current}]{dt}[{dt_label}]")
                current = dt_label

        # ── Overlays ──
        overlays = scene.get("overlays", [])
        for ov_name in overlays:
            if ov_name in registered_overlays:
                ov_filter = get_overlay(ov_name)
                if ov_filter:
                    ov_label = self._label("ov")
                    self.filter_parts.append(f"[{current}]{ov_filter}[{ov_label}]")
                    current = ov_label

        return current

    def build(self, timeline: dict, output_path: str, temp_dir: str) -> Tuple[List[str], str]:
        """Build complete FFmpeg command from timeline JSON."""
        scenes = timeline.get("scenes", [])

        # Compose all scenes
        scene_labels = []
        for sc in scenes:
            label = self.compose_scene(sc, temp_dir)
            scene_labels.append((label, float(sc.get("duration", 3.0))))

        # Build xfade chain
        last_label = scene_labels[0][0]
        cum_offset = scene_labels[0][1]

        for i in range(1, len(scene_labels)):
            prev_label = last_label
            cur_label, cur_dur = scene_labels[i]
            prev_dur = scene_labels[i - 1][1]

            trans = scenes[i - 1].get("transition", {})
            trans_id = trans.get("id", "dissolve") if isinstance(trans, dict) else trans
            trans_dur = float(trans.get("duration", 0.5)) if isinstance(trans, dict) else 0.5

            xf_label = self._label("xf")

            xf = get_xfade(trans_id, trans_dur, cum_offset - trans_dur)
            if xf:
                self.filter_parts.append(
                    f"[{prev_label}][{cur_label}]{xf}[{xf_label}]"
                )
                last_label = xf_label
            else:
                # Cut — just use the next scene directly
                last_label = cur_label

            cum_offset += cur_dur - trans_dur

        fc = ";".join(self.filter_parts)

        # Build FFmpeg command
        cmd = [FFMPEG if os.path.exists(FFMPEG) else "ffmpeg",
               "-y", "-hide_banner", "-loglevel", "warning"]

        for inp in self.inputs:
            cmd.extend(inp)

        # Copy fonts to temp dir for FFmpeg
        fonts_tmp = Path(temp_dir) / "fonts"
        fonts_tmp.mkdir(exist_ok=True)
        if FONTS_DIR.exists():
            for f in FONTS_DIR.glob("*.ttf"):
                shutil.copy2(str(f), str(fonts_tmp / f.name))

        fc_path = Path(temp_dir) / "filter_complex.txt"
        fc_path.write_text(fc, encoding="utf-8")
        cmd.extend(["-filter_complex_script", str(fc_path)])
        cmd.extend(["-map", f"[{last_label}]"])

        # Audio
        voice = timeline.get("voiceover", {})
        voice_path = voice.get("path", voice.get("src", ""))
        if voice_path and os.path.exists(str(voice_path)):
            cmd.extend(["-i", str(voice_path)])
            cmd.extend(["-map", f"{self.input_idx}:a"])
            cmd.extend(["-c:a", "aac", "-b:a", "128k"])

        music = timeline.get("background_music", {})
        music_path = music.get("path", music.get("src", ""))
        if music_path and os.path.exists(str(music_path)):
            cmd.extend(["-i", str(music_path)])
            cmd.extend(["-map", f"{self.input_idx + 1}:a"])

        total_dur = cum_offset
        cmd.extend([
            "-t", f"{total_dur:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(output_path),
        ])

        return cmd, fc

    def render(self, timeline: dict, output_path: str) -> dict:
        """Render a complete documentary."""
        started = time.monotonic()
        output_path = str(Path(output_path).resolve())

        with tempfile.TemporaryDirectory(prefix="smart_composer_") as tmp:
            cmd, fc = self.build(timeline, output_path, tmp)

            print(f"   [smart] {len(timeline.get('scenes', []))} scenes, "
                  f"{sum(float(s.get('duration', 0)) for s in timeline.get('scenes', [])):.0f}s")
            print(f"   [smart] Filter graph: {len(fc)} chars, {len(self.inputs)} inputs")

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600,
                cwd=str(Path(tmp) / "fonts"),
            )

            elapsed = time.monotonic() - started

            if result.returncode != 0:
                tail = (result.stderr or "")
                # Print full error for debug
                for line in tail.splitlines():
                    if "Error" in line or "error" in line.lower() or "No such" in line:
                        print(f"   [smart] {line.strip()[:200]}")
                raise RuntimeError(f"Smart composer failed: {tail.splitlines()[-1][:200] if tail.splitlines() else 'unknown error'}")

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
