"""Lightweight AI Video Intelligence Pipeline (LAVIP) — V1.

CPU-only, 8GB-RAM-friendly scene understanding for harvested video clips.
V1 covers: scene detection, frame sampling, quality filtering (blur,
brightness, black-frame), duplicate detection, watermark detection,
subtitle detection, face detection, object detection (YOLOv8 Nano),
and FFmpeg-based cleaning (blur watermarks/subtitles).  Ranking uses
weighted scoring across quality, cleanliness, uniqueness, and motion.
"""
from __future__ import annotations

from .pipeline import analyze_clips, clean_clips, filter_and_rank, SceneUnderstandingError
from .models import ClipAnalysis, ClipScore

__all__ = [
    "analyze_clips",
    "clean_clips",
    "filter_and_rank",
    "ClipAnalysis",
    "ClipScore",
    "SceneUnderstandingError",
]
