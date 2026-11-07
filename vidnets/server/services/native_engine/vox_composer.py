"""Vox-style documentary composer — 35+ pro templates with Vox-style design.

Uses vox_templates.py for template definitions (35+ templates).
All templates use robust position-based animations from vox_animations.py.
"""

from __future__ import annotations

import json, os, shutil, subprocess, tempfile, time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(os.environ.get("VIDZEO_RESOURCE_ROOT") or Path(__file__).resolve().parents[3]).resolve()
DATA_ROOT = Path(os.environ.get("VIDZEO_ROOT") or ROOT).resolve()
FONTS_DIR = ROOT / "fonts"
TOOLS_DIR = ROOT / "tools"
FFMPEG = str(TOOLS_DIR / "ffmpeg" / "ffmpeg.exe")
OUTPUT_DIR = Path(os.environ.get("VIDZEO_OUTPUT_DIR") or DATA_ROOT / "output").resolve()

# Import the new template system
from .libraries.vox_templates import VOX_TEMPLATES
from .libraries.vox_animations import bar_sweep_x, bar_grow_y, bar_static, overlay_rect
from .libraries.image_treatments import TREATMENTS
from .libraries.transitions import get_xfade


class VoxComposer:
    """Single-pass FFmpeg documentary composer with Vox-style templates."""

    def __init__(self, width=1920, height=1080, fps=30):
        self.width = width
        self.height = height
        self.fps = fps
        self.inputs: List[List[str]] = []
        self.filter_parts: List[str] = []
        self.input_idx = 0
        self.label_idx = 0

    def _label(self, prefix="v"):
        l = f"{prefix}{self.label_idx}"
        self.label_idx += 1
        return l

    def add_image(self, path: str, duration: float) -> str:
        label = f"{self.input_idx}:v"
        self.inputs.append(["-loop", "1", "-t", f"{duration:.3f}", "-i", str(path)])
        self.input_idx += 1
        return label

    def compose_scene(self, scene: dict):
        dur = float(scene.get("duration", 3.0))
        bg = scene.get("background_asset", "")
        tid = scene.get("template_id", "")
        props = scene.get("props", {})
        palette = scene.get("palette", {
            "primary": "#f4f0e6", "accent": "#d62e3c", "danger": "#ef6f6c",
            "background": "#0a0a0f", "surface": "#16263b",
        })
        treatment_name = scene.get("image_treatment", "static_hold")

        if not bg or not Path(bg).exists():
            raise FileNotFoundError(
                f"Scene background media is missing or unreadable: {bg or '<empty>'}"
            )

        current = self.add_image(bg, dur)

        # Image treatment
        if treatment_name in TREATMENTS and treatment_name != "static_hold":
            zp = TREATMENTS[treatment_name](width=self.width, height=self.height, fps=self.fps)
            zp_label = self._label("zp")
            self.filter_parts.append(f"[{current}]{zp}[{zp_label}]")
            current = zp_label

        # Format conversion for drawtext
        fmt_label = self._label("fmt")
        self.filter_parts.append(f"[{current}]format=yuv420p[{fmt_label}]")
        current = fmt_label

        # Color grade
        grade_label = self._label("gr")
        self.filter_parts.append(
            f"[{current}]eq=contrast=1.1:saturation=0.9:brightness=-0.05[{grade_label}]"
        )
        current = grade_label

        # Apply template chains (new VOX_TEMPLATES system)
        if tid in VOX_TEMPLATES:
            tmpl = VOX_TEMPLATES[tid](palette, props)
            for chain in tmpl["chains"]:
                if chain:
                    chain_label = self._label("f")
                    self.filter_parts.append(f"[{current}]{chain}[{chain_label}]")
                    current = chain_label

        return current

    def build(self, timeline: dict, output_path: str, temp_dir: str):
        scenes = timeline.get("scenes", [])
        scene_labels = []

        for sc in scenes:
            label = self.compose_scene(sc)
            scene_labels.append((label, float(sc.get("duration", 3.0))))

        last_label = scene_labels[0][0]
        cum_offset = scene_labels[0][1]

        for i in range(1, len(scene_labels)):
            prev_label = last_label
            cur_label, cur_dur = scene_labels[i]
            trans = scenes[i - 1].get("transition", {})
            trans_id = trans.get("id", "dissolve") if isinstance(trans, dict) else "dissolve"
            trans_dur = float(trans.get("duration", 0.4)) if isinstance(trans, dict) else 0.4

            xf_label = self._label("xf")
            xf = get_xfade(trans_id, trans_dur, cum_offset - trans_dur)
            if xf:
                self.filter_parts.append(f"[{prev_label}][{cur_label}]{xf}[{xf_label}]")
                last_label = xf_label
            else:
                last_label = cur_label
            cum_offset += cur_dur - trans_dur

        fc = ";".join([f for f in self.filter_parts if f])
        fc_file = Path(temp_dir) / "filter.txt"
        fc_file.write_text(fc, encoding="utf-8")

        cmd = [FFMPEG if os.path.exists(FFMPEG) else "ffmpeg",
               "-y", "-hide_banner", "-loglevel", "warning"]
        for inp in self.inputs:
            cmd.extend(inp)
        cmd.extend(["-filter_complex_script", str(fc_file)])
        cmd.extend(["-map", f"[{last_label}]"])
        cmd.extend(["-t", f"{cum_offset:.3f}"])
        cmd.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                     "-pix_fmt", "yuv420p", "-an",
                     "-movflags", "+faststart",
                     str(output_path)])

        return cmd, fc

    def render(self, timeline: dict, output_path: str) -> dict:
        started = time.monotonic()
        output_path = str(Path(output_path).resolve())
        scenes = timeline.get("scenes", [])
        has_transitions = any(
            s.get("transition", {}).get("duration", 0) > 0
            for s in scenes[:-1]
        )

        with tempfile.TemporaryDirectory(prefix="vox_") as tmp:
            fonts_tmp = Path(tmp) / "fonts"
            fonts_tmp.mkdir(exist_ok=True)
            if FONTS_DIR.exists():
                for f in FONTS_DIR.glob("*.ttf"):
                    shutil.copy2(str(f), str(fonts_tmp / f.name))

            if len(scenes) > 1 and not has_transitions:
                # Render each scene individually, concat temp files
                temp_files = []
                for i, sc in enumerate(scenes):
                    seg_path = Path(tmp) / f"seg_{i:03d}.mp4"
                    single = {"scenes": [sc]}
                    self.__init__(self.width, self.height, self.fps)
                    cmd, fc = self.build(single, str(seg_path), tmp)
                    result = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=120,
                        cwd=str(fonts_tmp),
                    )
                    if result.returncode != 0 or not seg_path.exists():
                        raise RuntimeError(f"Segment {i} failed")
                    temp_files.append(str(seg_path))

                # Concat with FFmpeg
                concat_list = Path(tmp) / "concat.txt"
                concat_list.write_text(
                    "\n".join(f"file '{f}'" for f in temp_files),
                    encoding="utf-8",
                )
                concat_cmd = [
                    FFMPEG if os.path.exists(FFMPEG) else "ffmpeg",
                    "-y", "-hide_banner", "-loglevel", "warning",
                    "-f", "concat", "-safe", "0", "-i", str(concat_list),
                    "-c", "copy", str(output_path),
                ]
                result = subprocess.run(concat_cmd, capture_output=True, text=True, timeout=60)
                if result.returncode != 0:
                    raise RuntimeError("Concat failed")
            else:
                # Single scene or has transitions — use filter_complex
                cmd, fc = self.build(timeline, output_path, tmp)

                print(f"   [vox] {len(scenes)} scenes, {len(fc)} chars filter")
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=600,
                    cwd=str(fonts_tmp),
                )
                if result.returncode != 0:
                    for line in (result.stderr or "").splitlines():
                        if "Error" in line or "error" in line.lower() or "No such" in line:
                            print(f"   [vox] {line.strip()[:200]}")
                    raise RuntimeError(f"Vox composer failed")

            elapsed = time.monotonic() - started
            out = Path(output_path)
            return {
                "output_path": str(out),
                "render_seconds": round(elapsed, 1),
                "size_mb": round(out.stat().st_size / (1024 * 1024), 1),
                "resolution": f"{self.width}x{self.height}",
                "scenes": len(timeline.get("scenes", [])),
            }
