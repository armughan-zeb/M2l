"""CPU-friendly frame extraction for the Visual Intelligence Pipeline.

Implements the VidRush-style scene-aware sampling strategy:
  - short scene (<5s)  : start, middle, end              -> 3 frames
  - normal scene (5-20s): 0/25/50/75/100%               -> 5 frames
  - long scene (20+s):  evenly spaced, capped at 8 frames

Frames are extracted at low resolution (480px long edge) to keep memory
and Qwen payload tiny on low-end CPUs.  All frames are written to a temp
directory that the caller is responsible for cleaning up.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple


def _ffmpeg_bin(name: str) -> str:
    local = Path(__file__).resolve().parents[3] / "tools" / "ffmpeg" / f"{name}.exe"
    if local.exists():
        return str(local)
    found = shutil.which(name)
    return found or name


def _run(args: List[str], timeout: int = 30) -> bool:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.returncode == 0
    except Exception:
        return False


def _video_duration(path: str) -> float:
    try:
        proc = subprocess.run(
            [
                _ffmpeg_bin("ffprobe"), "-v", "error",
                "-show_entries", "format=duration",
                "-of", "csv=p=0", path,
            ],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return float(proc.stdout.strip()) if proc.returncode == 0 else 0.0
    except Exception:
        return 0.0


def _scene_split_timestamps(duration: float) -> List[float]:
    """Return the sampling timestamp strategy for a clip of ``duration`` seconds."""
    if duration <= 0:
        return []
    if duration < 5.0:
        # short scene (3 frames) — start, middle, end, skipping edges
        return [duration * 0.1, duration * 0.5, duration * 0.9]
    if duration <= 20.0:
        # normal scene (5 frames) — 0/25/50/75/100% with 5% edge pad
        return [duration * p for p in (0.05, 0.25, 0.50, 0.75, 0.95)]
    # long scene — evenly spaced, 8 frames. Always skip the very first 5%
    # to avoid grabbing a black/intro frame at timestamp 0, which FFmpeg
    # routinely does on freshly-cut clips.
    n = 8
    pad = 0.05
    usable = 1.0 - 2 * pad
    return [duration * (pad + usable * (i / (n - 1))) for i in range(n)]


def extract_clip_frames(
    clip_path: str,
    *,
    out_dir: Optional[str] = None,
    long_edge: int = 480,
    max_frames: int = 8,
    render_quality: bool = False,
) -> Tuple[List[str], float]:
    """Extract a chronological sequence of JPEG frames from ``clip_path``.

    Returns ``(frame_paths, duration)``. When ``render_quality=True``, frames
    are preserved at full native resolution with high JPEG quality (-q:v 2)
    suitable for the final video render. Otherwise, frames are downscaled
    (e.g., 480px) to keep vision model payloads small and fast.
    """
    if not clip_path or not os.path.exists(clip_path):
        return [], 0.0
    # All samples share one wall budget. Previously four failed decodes each
    # received 20 seconds, before every overlay/vision/salvage pass repeated it.
    from ..job_context import remaining_seconds, check_job_active
    deadline = time.monotonic() + min(35.0, remaining_seconds(35.0))
    check_job_active()
    duration = _video_duration(clip_path)
    timestamps = _scene_split_timestamps(duration)
    if not timestamps:
        # No duration — try a single mid-extract at 1s
        timestamps = [1.0]
    if max_frames == 1 and duration > 0:
        timestamps = [duration * 0.5]
    if max_frames and len(timestamps) > max_frames:
        # Keep first, last and evenly spaced middle frames
        keep = []
        step = (len(timestamps) - 1) / max(1, max_frames - 1)
        for i in range(max_frames):
            keep.append(timestamps[min(len(timestamps) - 1, int(round(i * step)))])
        # Dedupe while preserving order
        seen = set()
        timestamps = [t for t in keep if not (t in seen or seen.add(t))]
    if out_dir is None:
        out_dir = tempfile.mkdtemp(prefix="vip_frames_")
    os.makedirs(out_dir, exist_ok=True)
    ffmpeg = _ffmpeg_bin("ffmpeg")
    frames: List[str] = []
    use_high_res = render_quality or long_edge >= 1080 or long_edge <= 0
    vf_args = [] if use_high_res else ["-vf", f"scale={long_edge}:-2"]
    q_val = "2" if use_high_res else "4"
    for idx, ts in enumerate(timestamps):
        check_job_active()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        out_path = os.path.join(out_dir, f"frame_{idx:02d}.jpg")
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-threads", "1", "-filter_threads", "1",
            "-ss", f"{max(0.0, float(ts)):.3f}",
            "-i", clip_path,
            "-frames:v", "1",
        ]
        cmd.extend(vf_args)
        cmd.extend(["-q:v", q_val, out_path])
        ok = _run(cmd, timeout=min(20.0, remaining))
        if ok and os.path.exists(out_path) and os.path.getsize(out_path) > 512:
            frames.append(out_path)
    return frames, duration


def cleanup_frames(frames: List[str]) -> None:
    """Delete extracted frame files and their shared parent temp directory."""
    if not frames:
        return
    parent = os.path.dirname(frames[0])
    for f in frames:
        try:
            os.remove(f)
        except OSError:
            pass
    if parent and parent.startswith(tempfile.gettempdir()):
        try:
            shutil.rmtree(parent, ignore_errors=True)
        except OSError:
            pass
