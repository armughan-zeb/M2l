"""Pre-scale verified media to 1080p during advance prep.

The render/compose path prefers a pre-scaled file when one exists next to
the original (same naming, wired by the render worker); this module only
creates those files.  It never upscales and never fails a prep run: any
problem returns ``None`` and the caller keeps the original.

Naming (shared with the render worker — do not change unilaterally):
    <stem>.scaled-1080p.<ext>   next to the original file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

MAX_WIDTH = 1920
MAX_HEIGHT = 1080
_SCALED_SUFFIX = ".scaled-1080p"


def scaled_path_for(path: str | Path) -> Path:
    """Return the pre-scaled path for ``path`` (does not create it)."""
    source = Path(str(path or ""))
    return source.with_name(f"{source.stem}{_SCALED_SUFFIX}{source.suffix}")


def _probe_dimensions(path: Path) -> Optional[tuple[int, int]]:
    """Return (width, height) via ffprobe, or None when it cannot be read."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    try:
        streams = json.loads(result.stdout or "{}").get("streams") or []
    except ValueError:
        return None
    if not streams:
        return None
    try:
        width = int(streams[0].get("width") or 0)
        height = int(streams[0].get("height") or 0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def ensure_scaled(path: str | Path) -> Optional[str]:
    """Return the 1080p pre-scaled path for ``path``, or None.

    - The scaled file is created next to the original as
      ``<stem>.scaled-1080p.<ext>`` (see :func:`scaled_path_for`).
    - Only downscales: a source already within 1920x1080 returns None
      (the original is already small enough; never upscale).
    - An existing scaled file newer than the source is reused as-is.
    - On any failure (missing file, no ffmpeg/ffprobe, encode error)
      returns None and prep continues with the original.
    """
    source = Path(str(path or ""))
    if not source.is_file():
        return None
    target = scaled_path_for(source)
    try:
        if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
            return str(target)
    except OSError:
        return None
    dimensions = _probe_dimensions(source)
    if dimensions is None:
        return None
    width, height = dimensions
    if width <= MAX_WIDTH and height <= MAX_HEIGHT:
        return None
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp{target.suffix}")
    # Images re-encode as JPEG; video clips re-encode as H.264 (audio copied).
    # Writing H.264 into a .jpg produces a raw NAL stream, not a JPEG.
    is_video = source.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".avi"}
    if is_video:
        command = [
            ffmpeg, "-y", "-v", "error",
            "-i", str(source),
            "-vf",
            f"scale={MAX_WIDTH}:{MAX_HEIGHT}:force_original_aspect_ratio=decrease",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            str(tmp),
        ]
    else:
        command = [
            ffmpeg, "-y", "-v", "error",
            "-i", str(source),
            "-vf",
            f"scale={MAX_WIDTH}:{MAX_HEIGHT}:force_original_aspect_ratio=decrease",
            "-frames:v", "1",
            "-q:v", "3",
            str(tmp),
        ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
        if result.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        os.replace(tmp, target)
        return str(target)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None
