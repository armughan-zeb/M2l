"""
Native Documentary Composition Engine — renderer, validator, cache, proxy.

Replaces Remotion with FFmpeg filter_complex.
LLM generates structured JSON → Python compiles to FFmpeg → one-pass render.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .component_manifest import (
    COMPONENTS,
    PERFORMANCE_LIMITS,
    get_component,
    get_llm_component_catalog,
)
from .timeline_compiler import TimelineCompiler, _ffmpeg

RESOURCE_ROOT = Path(os.environ.get("VIDZEO_RESOURCE_ROOT") or Path(__file__).resolve().parents[3]).resolve()
ROOT = Path(os.environ.get("VIDZEO_ROOT") or RESOURCE_ROOT).resolve()
OUTPUT_DIR = Path(os.environ.get("VIDZEO_OUTPUT_DIR") or ROOT / "output").resolve()
CACHE_DIR = ROOT / "cache" / "native"
PROXY_DIR = ROOT / "cache" / "proxies"


# ═══════════════════════════════════════════════════════════════════
# Validator
# ═══════════════════════════════════════════════════════════════════

def validate_timeline(timeline: Dict[str, Any]) -> List[str]:
    """Validate a timeline JSON against performance and schema rules.
    Returns list of warnings (empty = valid)."""
    warnings = []

    scenes = timeline.get("scenes", [])
    if not scenes:
        warnings.append("No scenes in timeline")
        return warnings

    valid_ids = set(COMPONENTS.keys())

    for i, scene in enumerate(scenes):
        components = scene.get("components", [])
        total_cost = 0
        overlay_count = 0
        text_layer_count = 0
        blur_count = 0
        heavy_count = 0

        for comp_ref in components:
            if isinstance(comp_ref, str):
                comp_id = comp_ref
            else:
                comp_id = comp_ref.get("id", "")

            if comp_id not in valid_ids:
                warnings.append(f"Scene {i}: unknown component '{comp_id}'")
                continue

            c = get_component(comp_id)
            cost = c.get("performance_cost", 1)
            total_cost += cost

            if c["render_mode"] in ("overlay", "drawtext+overlay"):
                overlay_count += 1
            if "drawtext" in c["render_mode"]:
                text_layer_count += 1
            if c["render_mode"] == "geq":
                blur_count += 1
            if cost >= 4:
                heavy_count += 1

        if total_cost > PERFORMANCE_LIMITS["max_scene_cost"]:
            warnings.append(f"Scene {i}: total cost {total_cost} exceeds max {PERFORMANCE_LIMITS['max_scene_cost']}")
        if overlay_count > PERFORMANCE_LIMITS["max_simultaneous_overlays"]:
            warnings.append(f"Scene {i}: {overlay_count} overlays exceeds max {PERFORMANCE_LIMITS['max_simultaneous_overlays']}")
        if text_layer_count > PERFORMANCE_LIMITS["max_animated_text_layers"]:
            warnings.append(f"Scene {i}: {text_layer_count} text layers exceeds max {PERFORMANCE_LIMITS['max_animated_text_layers']}")

    return warnings


# ═══════════════════════════════════════════════════════════════════
# Cache
# ═══════════════════════════════════════════════════════════════════

def cache_key(*parts: Any) -> str:
    """Deterministic cache key from component ID + parameters."""
    payload = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════════
# Proxy generator
# ═══════════════════════════════════════════════════════════════════

def generate_proxy(media_path: str, proxy_path: Optional[str] = None) -> str:
    """Generate a 540p proxy for editing/preview."""
    PROXY_DIR.mkdir(parents=True, exist_ok=True)
    src = Path(media_path)
    if proxy_path is None:
        proxy_path = str(PROXY_DIR / f"{src.stem}_proxy.mp4")
    else:
        proxy_path = str(Path(proxy_path))

    if Path(proxy_path).exists():
        return proxy_path

    ffmpeg = _ffmpeg()
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-vf", "scale=960:540:force_original_aspect_ratio=decrease",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-an",
        "-movflags", "+faststart",
        proxy_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=120)
    return proxy_path


# ═══════════════════════════════════════════════════════════════════
# Renderer
# ═══════════════════════════════════════════════════════════════════

def render_documentary(
    timeline: Dict[str, Any],
    output_path: str,
    mode: str = "final",
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> Dict[str, Any]:
    """Render a documentary from a structured timeline JSON.

    Args:
        timeline: LLM-generated timeline JSON with scenes, components, audio.
        output_path: Output MP4 path.
        mode: 'preview' (540p fast) or 'final' (1080p quality).
        progress_callback: Optional progress reporter.
    """
    started = time.monotonic()

    # Validate
    warnings = validate_timeline(timeline)
    if warnings:
        print(f"   [native] {len(warnings)} warnings:")
        for w in warnings[:5]:
            print(f"     - {w}")

    # Setup
    is_preview = mode == "preview"
    width = 960 if is_preview else 1920
    height = 540 if is_preview else 1080
    fps = 24 if is_preview else 30

    scenes = timeline.get("scenes", [])
    voiceover = timeline.get("voiceover", {})
    music = timeline.get("background_music", {})

    voice_path = voiceover.get("path", voiceover.get("src", ""))
    music_path = music.get("path", music.get("src", ""))

    # Build command
    compiler = TimelineCompiler(width=width, height=height, fps=fps)

    with tempfile.TemporaryDirectory(prefix="native_doc_") as tmp:
        scene_outputs = []

        for i, scene in enumerate(scenes):
            if progress_callback:
                progress_callback((i + 1) / len(scenes) * 0.7, f"Composing scene {i+1}/{len(scenes)}")

            label = compiler.compose_scene(scene, i, tmp)
            if label:
                scene_outputs.append(label)

        if not scene_outputs:
            raise ValueError("No scenes were composed")

        if progress_callback:
            progress_callback(0.75, "Building filter graph")

        # Build xfade chain for transitions
        last_label = scene_outputs[0]
        for i in range(1, len(scene_outputs)):
            next_label = scene_outputs[i]
            scene = scenes[i - 1]
            transition = scene.get("transition", {})
            transition_id = transition.get("id", "dip_black") if isinstance(transition, dict) else transition
            duration = float(transition.get("duration", 0.3)) if isinstance(transition, dict) else 0.3

            offset = sum(float(s.get("duration", 3.0)) for s in scenes[:i])

            from .timeline_compiler import xfade_filter
            xf = xfade_filter(transition_id, duration, offset)
            xf_label = compiler._next_label("xf")
            compiler.filter_parts.append(
                f"[{last_label}][{next_label}]{xf}[{xf_label}]"
            )
            last_label = xf_label

        # Build FFmpeg command
        fc = compiler.build_filter_complex()

        cmd = [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "warning", "-nostdin"]

        # Add media inputs (now stored as argument lists)
        for inp in compiler.inputs:
            cmd.extend(inp)

        if voice_path:
            cmd.extend(["-i", voice_path])

        if music_path:
            cmd.extend(["-i", music_path])

        fc_dir = None
        if fc:
            # FFmpeg on Windows: need fonts in CWD for drawtext to find them
            # Copy fonts to a temp dir and set CWD there
            import shutil
            fc_dir = Path(tempfile.gettempdir()) / f"native_fc_{os.getpid()}"
            fc_dir.mkdir(parents=True, exist_ok=True)
            fonts_src = ROOT / "fonts"
            if fonts_src.exists():
                for font in fonts_src.glob("*.ttf"):
                    shutil.copy2(str(font), str(fc_dir / font.name))
            cmd.extend(["-filter_complex", fc])

        # Map video output
        cmd.extend(["-map", f"[{last_label}]"])

        # Map audio if present
        vo_idx = compiler.input_index
        if voice_path:
            cmd.extend(["-map", f"{vo_idx}:a"])
        if music_path:
            cmd.extend(["-map", f"{vo_idx + 1}:a"])

        total_duration = sum(float(s.get("duration", 3.0)) for s in scenes) + 2

        preset = "ultrafast" if is_preview else "veryfast"
        crf = 28 if is_preview else 23

        cmd.extend([
            "-t", f"{total_duration:.3f}",
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-pix_fmt", "yuv420p",
        ])
        if voice_path:
            cmd.extend(["-c:a", "aac", "-b:a", "96k" if is_preview else "128k"])
        cmd.extend(["-movflags", "+faststart", str(output_path)])

        if progress_callback:
            progress_callback(0.80, "Rendering final video...")

        print(f"   [native] Running FFmpeg ({'preview' if is_preview else 'final'}, {width}x{height})...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                                cwd=str(fc_dir) if fc_dir else None)

        elapsed = time.monotonic() - started

        if result.returncode != 0:
            tail = (result.stderr or "")[-500:]
            print(f"   [native] FAILED after {elapsed:.0f}s")
            print(f"   {tail[:300]}")
            raise RuntimeError(f"Native render failed: {tail[:200]}")

        output = Path(output_path)
        if not output.exists() or output.stat().st_size < 4096:
            raise RuntimeError(f"Native render produced no output")

        size_mb = output.stat().st_size / (1024 * 1024)

        if progress_callback:
            progress_callback(1.0, "Complete")

        print(f"   [native] Complete in {elapsed:.0f}s ({size_mb:.1f} MB) → {output_path}")

        return {
            "output_path": str(output),
            "render_seconds": round(elapsed, 1),
            "size_mb": round(size_mb, 1),
            "resolution": f"{width}x{height}",
            "mode": mode,
            "warnings": len(warnings),
        }
