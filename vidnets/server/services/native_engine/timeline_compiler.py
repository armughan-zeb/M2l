"""
Timeline compiler — converts LLM documentary JSON into FFmpeg filter_complex commands.

Input: structured timeline JSON from LLM
Output: FFmpeg command with filter_complex for one-pass rendering
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .component_manifest import COMPONENTS, PERFORMANCE_LIMITS, get_component

ROOT = Path(os.environ.get("VIDZEO_RESOURCE_ROOT") or Path(__file__).resolve().parents[3]).resolve()
TOOLS_DIR = ROOT / "tools"
FONTS_DIR = ROOT / "fonts"
FFMPEG = str(TOOLS_DIR / "ffmpeg" / "ffmpeg.exe")


def _ffmpeg() -> str:
    return FFMPEG if Path(FFMPEG).exists() else "ffmpeg"


# ═══════════════════════════════════════════════════════════════════
# FFmpeg filter snippet generators per component
# ═══════════════════════════════════════════════════════════════════

def _escape_drawtext(text: str) -> str:
    """Escape text for FFmpeg drawtext filter."""
    return text.replace(":", "\\:").replace("'", "\\'").replace("%", "\\\\%")


def _font_path(font_file: str) -> str:
    """Return just the font filename — FFmpeg on Windows can't handle absolute paths with colons.
    The renderer will set CWD to the fonts directory before running FFmpeg."""
    return font_file


def _drawtext_filter(
    text: str,
    font_file: str = "Poppins-Bold.ttf",
    font_size: int = 42,
    color: str = "#ffffff",
    x: str = "(w-text_w)/2",
    y: str = "(h-text_h)/2",
    alpha: str = "1",
    shadow: bool = True,
    border: bool = False,
    box: bool = True,
    box_color: str = "0x0a0f1d@0.88",
    box_border_w: int = 14,
    anim: str = "none",
    start_t: str = "0",
    enable: str = "",
    tag: str = "",
) -> str:
    """Generate a drawtext filter snippet."""
    font = _font_path(font_file)
    parts = [
        f"text='{_escape_drawtext(text)}'",
        f"fontfile='{font}'",
        f"fontsize={font_size}",
        f"fontcolor={color}",
        f"x={x}",
        f"y={y}",
    ]

    if box:
        parts.append(f"box=1:boxcolor={box_color}:boxborderw={box_border_w}")
    if shadow:
        parts.append("shadowcolor=black@0.72:shadowx=2:shadowy=2")
    if border:
        parts.append("bordercolor=black:borderw=2")
    if alpha != "1":
        parts.append(f"alpha='{alpha}'")

    # Animation: simple — just use fixed font size for now
    # FFmpeg's drawtext fontsize doesn't support expressions in this build
    if anim and anim != "none":
        pass  # Animation support removed — fontsize_expr not available

    if enable:
        parts.append(f"enable='{enable}'")

    return "drawtext=" + ":".join(parts)


def _counter_frames(
    start_value: float,
    end_value: float,
    duration: float,
    fps: int = 30,
    prefix: str = "",
    suffix: str = "",
    decimals: int = 0,
) -> List[str]:
    """Generate per-frame counter text for drawtext."""
    total_frames = max(1, int(duration * fps))
    frames = []
    for f in range(total_frames):
        t = min(1.0, f / max(1, total_frames - 1))
        # Ease-out
        eased = 1 - (1 - t) ** 3 if t < 1 else 1
        value = start_value + (end_value - start_value) * eased
        if decimals > 0:
            text = f"{prefix}{value:,.{decimals}f}{suffix}"
        else:
            text = f"{prefix}{int(round(value)):,}{suffix}"
        frames.append(text)
    return frames


# ═══════════════════════════════════════════════════════════════════
# Core compiler
# ═══════════════════════════════════════════════════════════════════

class TimelineCompiler:
    """Compile LLM documentary JSON into FFmpeg filter_complex."""

    def __init__(self, width: int = 1920, height: int = 1080, fps: int = 30):
        self.width = width
        self.height = height
        self.fps = fps
        self.inputs: List[str] = []
        self.filter_parts: List[str] = []
        self.audio_filters: List[str] = []
        self.input_index = 0
        self.filter_label_idx = 0
        self._scene_counter_texts: Dict[str, List[str]] = {}

    def _next_label(self, prefix: str = "v") -> str:
        label = f"{prefix}{self.filter_label_idx}"
        self.filter_label_idx += 1
        return label

    def add_media_input(self, path: str, duration: float = 2.0) -> str:
        """Add a media file (image/video) as input. Returns its stream label."""
        label = f"{self.input_index}:v"
        self.inputs.append(["-loop", "1", "-t", f"{duration:.3f}", "-i", path])
        self.input_index += 1
        return label

    def compose_scene(
        self,
        scene: Dict[str, Any],
        scene_index: int,
        temp_dir: str,
    ) -> Optional[str]:
        """Compose a single scene. Returns the output filter label."""
        duration = float(scene.get("duration", 3.0))
        components = scene.get("components", [])

        # Validate performance cost
        total_cost = 0
        for comp_id in components:
            c = get_component(comp_id) if isinstance(comp_id, str) else comp_id
            if c and isinstance(c, dict):
                total_cost += c.get("performance_cost", 1)

        if total_cost > PERFORMANCE_LIMITS["max_scene_cost"]:
            print(f"   [native] Scene {scene_index}: cost {total_cost} exceeds limit, scaling back")
            # Drop heaviest components
            components = [c for c in components if get_component(c)["performance_cost"] < 4]

        # Add background image/video
        bg_src = scene.get("background_asset", scene.get("media_src", ""))
        if not bg_src or not os.path.exists(str(bg_src)):
            # Never hide a retrieval failure behind a solid-color plate.  The
            # media contract must provide a real image/video before FFmpeg is
            # invoked, otherwise the caller can retry and report the scene.
            raise FileNotFoundError(
                f"Scene {scene_index + 1} background media is missing or unreadable: "
                f"{bg_src or '<empty>'}"
            )

        bg_label = self.add_media_input(bg_src, duration)
        current_label = bg_label

        # Apply image treatment
        treatment = scene.get("image_treatment", "static_hold")
        if treatment == "slow_push_in":
            z = scene.get("zoom", 1.08)
            zoom_label = self._next_label("zp")
            self.filter_parts.append(
                f"[{current_label}]zoompan=z='min(zoom+0.0004,{z})':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={self.width}x{self.height}:fps={self.fps}[{zoom_label}]"
            )
            current_label = zoom_label
        elif treatment in ("pan_left", "pan_right", "slow_pull_out"):
            z = 1.10 if treatment == "slow_pull_out" else 1.05
            x_start = "0" if treatment == "pan_right" else "iw-iw/zoom"
            zoom_label = self._next_label("zp")
            self.filter_parts.append(
                f"[{current_label}]zoompan=z='{z}':x='{x_start}+1*t':d=1:s={self.width}x{self.height}:fps={self.fps}[{zoom_label}]"
            )
            current_label = zoom_label
        elif treatment not in ("static_hold", ""):
            # Scale to fit
            scale_label = self._next_label("sc")
            self.filter_parts.append(
                f"[{current_label}]scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[{scale_label}]"
            )
            current_label = scale_label

        # Apply text components via drawtext
        text_layers = []
        for comp_ref in components:
            if isinstance(comp_ref, str):
                comp_id = comp_ref
                comp_props = scene.get("text", {}) or {}
            else:
                comp_id = comp_ref.get("id", "")
                comp_props = {k: v for k, v in comp_ref.items() if k != "id"}

            c = get_component(comp_id)
            if not c:
                continue

            if c["render_mode"] in ("drawtext", "drawtext+counter"):
                dt_label = self._next_label("dt")

                if "counter" in c["render_mode"]:
                    value = float(comp_props.get("value", 0))
                    prefix = comp_props.get("prefix", "$")
                    unit = comp_props.get("unit", "")
                    label_text = comp_props.get("label", "")

                    # Main counter
                    dt_filter = _drawtext_filter(
                        text="0",
                        font_file=comp_props.get("font_file", "Poppins-Bold.ttf"),
                        font_size=int(comp_props.get("font_size", 120)),
                        color=comp_props.get("color", "#f2c14e"),
                        y="(h-text_h)/2-30",
                        shadow=True,
                        tag=f"s{scene_index}",
                    )
                    self.filter_parts.append(f"[{current_label}]{dt_filter}[{dt_label}]")
                    current_label = dt_label

                    # Label below
                    if label_text:
                        lb_label = self._next_label("dt")
                        dt_filter2 = _drawtext_filter(
                            text=label_text,
                            font_file="OpenSans-Bold.ttf",
                            font_size=28,
                            color=comp_props.get("label_color", "#f4f0e6"),
                            y="(h-text_h)/2+80",
                            shadow=False,
                        )
                        self.filter_parts.append(f"[{current_label}]{dt_filter2}[{lb_label}]")
                        current_label = lb_label
                else:
                    text = comp_props.get("text", comp_props.get("title", comp_props.get("date", "")))
                    font_file = comp_props.get("font_file", "Poppins-Bold.ttf")
                    font_size = int(comp_props.get("font_size", 88))
                    color = comp_props.get("color", "#f4f0e6")
                    anim = comp_props.get("anim", "scale_pop")
                    kicker = comp_props.get("kicker", "")

                    # Kicker line
                    if kicker:
                        k_label = self._next_label("dt")
                        k_filter = _drawtext_filter(
                            text=kicker,
                            font_file="OpenSans-Bold.ttf",
                            font_size=24,
                            color=comp_props.get("accent_color", "#f2c14e"),
                            y="(h-text_h)/2-100",
                            shadow=False,
                        )
                        self.filter_parts.append(f"[{current_label}]{k_filter}[{k_label}]")
                        current_label = k_label

                    # Main text
                    dt_filter = _drawtext_filter(
                        text=text,
                        font_file=font_file,
                        font_size=font_size,
                        color=color,
                        anim=anim,
                        shadow=True,
                    )
                    self.filter_parts.append(f"[{current_label}]{dt_filter}[{dt_label}]")
                    current_label = dt_label

                    # Detail/subtitle
                    detail = comp_props.get("detail", "")
                    if detail:
                        d_label = self._next_label("dt")
                        d_filter = _drawtext_filter(
                            text=detail[:80],
                            font_file="OpenSans-Bold.ttf",
                            font_size=22,
                            color=f"{color}@0.8",
                            y="(h-text_h)/2+100",
                            shadow=False,
                        )
                        self.filter_parts.append(f"[{current_label}]{d_filter}[{d_label}]")
                        current_label = d_label

        return current_label

    def build_filter_complex(self) -> str:
        """Return the complete filter_complex string."""
        return ";".join(self.filter_parts)

    def build_command(
        self,
        output_path: str,
        voiceover_path: str = "",
        music_path: str = "",
        preset: str = "veryfast",
        crf: int = 23,
    ) -> List[str]:
        """Build the complete FFmpeg command."""
        cmd = [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "warning", "-nostdin"]

        # All media inputs
        for inp in self.inputs:
            # Parse the input string
            cmd.extend(inp.split(" ", 1))

        # Voiceover
        if voiceover_path and os.path.exists(voiceover_path):
            cmd.extend(["-i", voiceover_path])

        # Music
        if music_path and os.path.exists(music_path):
            cmd.extend(["-i", music_path])

        # Filter complex
        fc = self.build_filter_complex()
        if fc:
            cmd.extend(["-filter_complex", fc])

        # Output
        cmd.extend([
            "-t", "60",  # placeholder — should match total duration
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path),
        ])

        return cmd


# ═══════════════════════════════════════════════════════════════════
# Transition engine
# ═══════════════════════════════════════════════════════════════════

XFADE_MAP = {
    "dip_black": "fadeblack",
    "slide_left": "wiperight",
    "slide_right": "wipeleft",
    "slide_up": "wipeup",
    "slide_down": "wipedown",
    "flash_cut": "fadeblack",
    "dissolve": "fade",
}


def xfade_filter(transition_id: str, duration: float, offset: float) -> str:
    """Generate an xfade filter snippet."""
    xf_name = XFADE_MAP.get(transition_id, "fade")
    return f"xfade=transition={xf_name}:duration={duration:.3f}:offset={offset:.3f}"


# ═══════════════════════════════════════════════════════════════════
# Audio engine
# ═══════════════════════════════════════════════════════════════════

def audio_mix_filter(
    voice_input: str,
    music_input: str,
    music_volume: float = 0.15,
    duck_volume: float = 0.04,
) -> str:
    """Generate sidechain audio ducking filter."""
    return (
        f"[{music_input}]volume={music_volume}[mvol];"
        f"[{voice_input}]volume=1.0[vvol];"
        f"[mvol][vvol]amix=inputs=2:duration=first:dropout_transition=2,volume=1.2"
    )
