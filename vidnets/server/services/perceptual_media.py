"""Bounded, local visual-repeat checks on pixels rather than filenames.

This is a conservative duplicate detector, not semantic similarity: two photos
of the same subject should remain different shots. Blank frames are excluded.
No media is uploaded, and an unavailable decoder is reported rather than passed.
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

import numpy as np
from PIL import Image, ImageOps


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if np.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def describe_frame(image: Image.Image) -> dict[str, Any] | None:
    """Combine spatial edges and actual color; reject uninformative fades."""
    image = ImageOps.exif_transpose(image).convert("RGB")
    pixels = np.asarray(image.resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float32) / 255.0
    luminance = pixels @ np.asarray([0.299, 0.587, 0.114], dtype=np.float32)
    if float(luminance.std()) < 0.045:
        return None
    edge_pixels = np.asarray(image.convert("L").resize((17, 16), Image.Resampling.LANCZOS))
    edges = edge_pixels[:, 1:] > edge_pixels[:, :-1]
    return {"edges": edges, "pixels": pixels, "luminance": luminance}


def frames_match(left: Mapping[str, Any], right: Mapping[str, Any], *, motion_tolerant: bool = False) -> bool:
    # All three gates must pass. A single edge hash can confuse similar maps,
    # uniform water or letterboxed shots; pixel and color gates prevent that.
    edge_distance = int(np.count_nonzero(left["edges"] != right["edges"]))
    color_error = float(np.sqrt(np.mean((left["pixels"] - right["pixels"]) ** 2)))
    luminance_error = float(np.sqrt(np.mean((left["luminance"] - right["luminance"]) ** 2)))
    # Video frames inside one sustained shot contain small natural movements.
    # Allow those only when two independent samples also match; image pairs
    # retain the tighter duplicate thresholds.
    return edge_distance <= (32 if motion_tolerant else 10) and color_error <= (0.075 if motion_tolerant else 0.065) and luminance_error <= (0.07 if motion_tolerant else 0.055)


def samples_match(left: list[dict[str, Any]], right: list[dict[str, Any]], *, both_video: bool) -> bool:
    if not left or not right:
        return False
    # Distinct one-to-one frames, not repeated matches against one incidental
    # frame. For video require two independent matching samples.
    used: set[int] = set()
    count = 0
    for sample in left:
        for index, other in enumerate(right):
            if index not in used and frames_match(sample, other, motion_tolerant=both_video):
                used.add(index)
                count += 1
                break
    return count >= (2 if both_video else 1)


class PixelSampler:
    """Sample actual played windows with one film-wide wall-clock budget."""

    def __init__(self, budget_seconds: float = 20.0) -> None:
        self.deadline = time.monotonic() + max(0.0, min(60.0, _number(budget_seconds, 20.0)))
        self.cache: dict[tuple[Any, ...], tuple[list[dict[str, Any]], str]] = {}
        self.decode_count = 0

    def sample(self, shot: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str]:
        path = str(shot.get("path") or shot.get("asset_path") or "")
        try:
            stat = os.stat(path)
        except (OSError, ValueError):
            return [], "asset_unavailable"
        video = str(shot.get("media_type") or shot.get("type") or "").lower() == "video" or Path(path).suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
        # clip_start can be an original YouTube timestamp on an already cut
        # local file. Only render_clip_start denotes the actual local seek.
        start = max(0.0, _number(shot.get("render_clip_start")))
        duration = max(0.05, _number(shot.get("duration"), 2.0))
        source_span = _number(shot.get("clip_end")) - _number(shot.get("clip_start"))
        if source_span > 0:
            duration = min(duration, source_span)
        key = (os.path.normcase(os.path.abspath(path)), stat.st_size, stat.st_mtime_ns, video, round(start, 3) if video else 0, round(duration, 3) if video else 0)
        if key in self.cache:
            return self.cache[key]
        if time.monotonic() >= self.deadline:
            return [], "sampling_budget_exhausted"
        frames: list[dict[str, Any]] = []
        reason = "no_informative_frames"
        if not video:
            try:
                with Image.open(path) as image:
                    image.thumbnail((512, 512))
                    description = describe_frame(image)
                if description is not None:
                    frames.append(description)
            except (OSError, ValueError, Image.DecompressionBombError):
                reason = "image_decode_failed"
        else:
            from .motion_templates import get_ffmpeg_path
            for fraction in (0.12, 0.5, 0.85):
                remaining = self.deadline - time.monotonic()
                if remaining <= 0.05:
                    reason = "sampling_budget_exhausted"
                    break
                self.decode_count += 1
                try:
                    result = subprocess.run(
                        [get_ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-threads", "1", "-ss", str(round(start + duration * fraction, 3)), "-i", path,
                         "-frames:v", "1", "-vf", "scale=160:90", "-f", "image2pipe", "-vcodec", "png", "-"],
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        timeout=min(3.0, remaining), check=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    if result.returncode == 0 and result.stdout:
                        with Image.open(io.BytesIO(result.stdout)) as image:
                            description = describe_frame(image)
                        if description is not None:
                            frames.append(description)
                    else:
                        reason = "video_decode_failed"
                except (OSError, subprocess.TimeoutExpired, ValueError):
                    reason = "video_decode_failed"
                    break
        if video and len(frames) < 2:
            result = ([], "insufficient_video_samples" if frames else reason)
        else:
            result = (frames, "sampled" if frames else reason)
        self.cache[key] = result
        return result

    @staticmethod
    def signature(samples: list[dict[str, Any]]) -> str:
        return hashlib.sha256(b"".join(np.packbits(sample["edges"]).tobytes() for sample in samples)).hexdigest() if samples else ""
