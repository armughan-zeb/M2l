"""Layer 1 — cheap CPU pre-filter (free, deterministic).

Removes duplicates (perceptual hash), low-resolution, and bad aspect-ratio
candidates BEFORE any Qwen Vision call. This is the cost-optimization gate
that keeps the Flash tier budget tiny.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

from .models import ImageAnalysis, ClipAnalysis


# =============================================================================
# CONFIGURATION — low-end CPU friendly
# =============================================================================

# Historical/public-domain collections commonly expose clean 800x450 or
# 800x600 derivatives. They are perfectly usable as documentary source
# material after a modest upscale, and Qwen is better placed to judge their
# visual quality than a 1000 px hard gate. Keep the free prefilter focused on
# genuinely tiny assets while allowing the vision ranker to inspect the rest.
MIN_LONG_EDGE = int(os.environ.get("VIDZEO_VIP_MIN_LONG_EDGE", "640") or 640)
MIN_SHORT_EDGE = int(os.environ.get("VIDZEO_VIP_MIN_SHORT_EDGE", "360") or 360)
ASPECT_TARGET = 16.0 / 9.0
ASPECT_TOLERANCE = float(os.environ.get("VIDZEO_VIP_ASPECT_TOL", "0.30") or 0.30)
PHASH_DUPLICATE_HAMMING = int(os.environ.get("VIDZEO_VIP_PHASH_HAMMING", "4") or 4)


@dataclass
class PrefilterVerdict:
    rejected: bool
    reason: str = ""
    width: int = 0
    height: int = 0
    phash: Optional[int] = None


# =============================================================================
# IMAGE DICTION
# =============================================================================

def _image_dimensions(path: str) -> Tuple[int, int]:
    try:
        from PIL import Image  # type: ignore
        with Image.open(path) as img:
            return int(img.width), int(img.height)
    except Exception:
        return 0, 0


def _average_hash(path: str) -> Optional[int]:
    try:
        from PIL import Image  # type: ignore
        with Image.open(path) as img:
            gray = img.convert("L").resize((8, 8))
            pixels = list(gray.getdata())
        avg = sum(pixels) / max(1, len(pixels))
        bits = 0
        for pixel in pixels:
            bits = (bits << 1) | (1 if pixel >= avg else 0)
        return bits
    except Exception:
        return None


def _hamming(a: int, b: int) -> int:
    try:
        return int(a ^ b).bit_count()
    except Exception:
        return 64


def prefilter_image(path: str, seen_phashes: Iterable[int] = ()) -> PrefilterVerdict:
    """Run the cheap Layer 1 checks for a single image candidate."""
    if not path or not os.path.exists(path):
        return PrefilterVerdict(True, reason="missing_file")
    w, h = _image_dimensions(path)
    if w <= 0 or h <= 0:
        return PrefilterVerdict(True, reason="unreadable_image", width=w, height=h)
    if max(w, h) < MIN_LONG_EDGE or min(w, h) < MIN_SHORT_EDGE:
        return PrefilterVerdict(
            True, reason=f"low_resolution_{w}x{h}", width=w, height=h
        )
    phash = _average_hash(path)
    if phash is not None:
        for existing in seen_phashes:
            if existing is not None and _hamming(phash, existing) <= PHASH_DUPLICATE_HAMMING:
                return PrefilterVerdict(
                    True, reason="duplicate_phash", width=w, height=h, phash=phash
                )
    aspect = (w / h) if h else 0.0
    if aspect > 0 and abs(aspect - ASPECT_TARGET) / ASPECT_TARGET > ASPECT_TOLERANCE:
        # We don't reject on aspect — we just downgrade later. But extreme
        # vertical (e.g., 9:16) for a 16:9 documentary IS a reject.
        if aspect < 1.0:
            return PrefilterVerdict(
                True, reason=f"portrait_aspect_{aspect:.2f}", width=w, height=h, phash=phash
            )
    return PrefilterVerdict(False, "", w, h, phash)


def apply_image_prefilter(analysis: ImageAnalysis, seen_phashes: Iterable[int] = ()) -> ImageAnalysis:
    verdict = prefilter_image(analysis.path, seen_phashes=seen_phashes)
    analysis.width = verdict.width
    analysis.height = verdict.height
    analysis.phash = verdict.phash
    analysis.prefilter_rejected = verdict.rejected
    analysis.prefilter_reason = verdict.reason
    return analysis


# =============================================================================
# CLIP PREFILTER
# =============================================================================

def _video_meta(path: str) -> Tuple[int, int, float, float]:
    """Return (width, height, fps, duration) via ffprobe. Zeroes on failure."""
    try:
        import subprocess  # type: ignore
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate",
                "-show_entries", "format=duration",
                "-of", "csv=p=0", path,
            ],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0:
            return 0, 0, 0.0, 0.0
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        # stream line: width,height,r_frame_rate
        # format line: duration
        width = height = 0
        fps = duration = 0.0
        for line in lines:
            parts = line.split(",")
            if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
                width = int(parts[0]); height = int(parts[1])
                rate = parts[2]
                if "/" in rate:
                    num, den = rate.split("/", 1)
                    den_f = float(den)
                    fps = (float(num) / den_f) if den_f else 0.0
                else:
                    try:
                        fps = float(rate)
                    except Exception:
                        fps = 0.0
            else:
                try:
                    duration = float(line)
                except Exception:
                    pass
        return width, height, fps, duration
    except Exception:
        return 0, 0, 0.0, 0.0


def prefilter_clip(path: str, seen_phashes: Iterable[int] = (), min_duration: float = 1.5) -> PrefilterVerdict:
    if not path or not os.path.exists(path):
        return PrefilterVerdict(True, reason="missing_file")
    w, h, fps, duration = _video_meta(path)
    if w <= 0 or h <= 0:
        return PrefilterVerdict(True, reason="unreadable_video", width=w, height=h)
    if duration > 0 and duration < min_duration:
        return PrefilterVerdict(
            True, reason=f"too_short_{duration:.1f}s", width=w, height=h
        )
    if min(w, h) < 360:
        return PrefilterVerdict(
            True, reason=f"low_resolution_{w}x{h}", width=w, height=h
        )
    aspect = (w / h) if h else 0.0
    if aspect > 0 and aspect < 1.0:
        # Portrait clips aren't usable in a 16:9 doc without heavy crop.
        return PrefilterVerdict(
            True, reason=f"portrait_aspect_{aspect:.2f}", width=w, height=h
        )
    return PrefilterVerdict(False, "", w, h)


def apply_clip_prefilter(analysis: ClipAnalysis, seen_phashes: Iterable[int] = ()) -> ClipAnalysis:
    verdict = prefilter_clip(analysis.path, seen_phashes=seen_phashes)
    analysis.width = verdict.width
    analysis.height = verdict.height
    analysis.prefilter_rejected = verdict.rejected
    analysis.prefilter_reason = verdict.reason
    # Fill the missing metadata fields for downstream stages
    if not analysis.duration:
        _, _, _, duration = _video_meta(analysis.path)
        analysis.duration = duration
    return analysis
