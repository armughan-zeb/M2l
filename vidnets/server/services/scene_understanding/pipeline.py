"""LAVIP V1 pipeline orchestrator.

Takes a list of harvested clip paths, runs all V1 analyzers on each, and
returns ranked ClipAnalysis records.  Designed for CPU-only 8GB RAM:
- batch size 1 (process one clip at a time)
- frames deleted after analysis
- never loads full video into memory
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from typing import Dict, List, Optional

from .models import ClipAnalysis, ClipScore
from . import analyzers

logger = logging.getLogger("lavip")

# Rejection thresholds — tuned for documentary-style content
BLUR_REJECT = 80.0          # Laplacian variance below this = too blurry
BRIGHTNESS_REJECT = 30.0    # Average brightness below this = too dark
BLACK_RATIO_REJECT = 0.90   # 90%+ black pixels = blank frame
WATERMARK_REJECT = 0.80     # Corner stability above this = likely logo
DUPLICATE_HAMMING = 5       # phash hamming distance below this = duplicate

# Ranking weights (sum to 1.0)
W_QUALITY = 0.35
W_CLEANLINESS = 0.25
W_UNIQUENESS = 0.20
W_MOTION = 0.20


class SceneUnderstandingError(Exception):
    """Raised when the pipeline cannot complete."""


def _cleanup(frames: List[str]) -> None:
    for f in frames:
        try:
            os.remove(f)
        except OSError:
            pass
    parent = os.path.dirname(frames[0]) if frames else ""
    if parent and parent.startswith(tempfile.gettempdir()):
        try:
            shutil.rmtree(parent, ignore_errors=True)
        except OSError:
            pass


def _score_quality(blur: float, brightness: float, black_ratio: float) -> float:
    """Convert raw metrics to 0-100 quality score."""
    if blur < BLUR_REJECT:
        return max(0.0, (blur / BLUR_REJECT) * 50.0)
    if brightness < BRIGHTNESS_REJECT:
        return max(0.0, (brightness / BRIGHTNESS_REJECT) * 40.0)
    if black_ratio > BLACK_RATIO_REJECT:
        return 10.0
    # Good quality — scale blur up
    base = 60.0
    bonus = min(40.0, (blur - BLUR_REJECT) / 10.0)
    brightness_bonus = 0.0
    if 40 <= brightness <= 220:
        brightness_bonus = 5.0
    return min(100.0, base + bonus + brightness_bonus)


def _score_cleanliness(watermark_prob: float, has_subtitle: bool) -> float:
    """0-100, 100 = perfectly clean."""
    penalty = 0.0
    if watermark_prob > WATERMARK_REJECT:
        penalty += 10.0
    elif watermark_prob > 0.5:
        penalty += 5.0
    if has_subtitle:
        penalty += 5.0
    return max(0.0, 100.0 - penalty)


def _score_uniqueness(is_duplicate: bool, phash: str, all_phashes: List[str]) -> float:
    if is_duplicate:
        return 0.0
    if not phash:
        return 50.0
    # Count how many other clips are similar
    try:
        from .analyzers import find_duplicate
        similar = 0
        for other in all_phashes:
            if other and other != phash:
                if find_duplicate(phash, {other: ""}, threshold=8):
                    similar += 1
        return max(0.0, 100.0 - similar * 20.0)
    except Exception:
        return 80.0


def analyze_single_clip(
    path: str,
    scene_id: int = 0,
    seen_phashes: Optional[Dict[str, str]] = None,
    all_phashes: Optional[List[str]] = None,
    frame_count: int = 3,
) -> ClipAnalysis:
    """Run all V1 analyzers on a single clip."""
    seen_phashes = seen_phashes or {}
    all_phashes = all_phashes or []

    analysis = ClipAnalysis(path=path, scene_id=scene_id)
    analysis.duration = analyzers._video_duration(path)
    analysis.width, analysis.height, analysis.fps = analyzers._video_meta(path)

    # Sample frames
    frames = analyzers.sample_frames(path, count=frame_count, duration=analysis.duration)
    if not frames:
        analysis.score.rejected = True
        analysis.score.reject_reason = "frame_extraction_failed"
        return analysis

    try:
        # Quality
        quality = analyzers.analyze_quality(frames)
        analysis.blur_score = quality["blur"]
        analysis.brightness = quality["brightness"]
        analysis.black_frame_ratio = quality["black_ratio"]

        # Perceptual hash (use middle frame for stability)
        mid_frame = frames[len(frames) // 2] if frames else frames[0]
        analysis.phash = analyzers.compute_phash(mid_frame)

        # Duplicate check
        if analysis.phash:
            dup_path = analyzers.find_duplicate(analysis.phash, seen_phashes, threshold=DUPLICATE_HAMMING)
            if dup_path:
                analysis.is_duplicate = True
                analysis.duplicate_of = dup_path

        # Watermark detection
        wm = analyzers.detect_watermark(frames)
        if wm:
            max_region = max(wm, key=wm.get)
            max_prob = wm[max_region]
            analysis.watermark_prob = max_prob
            analysis.watermark_region = max_region
            analysis.has_watermark = max_prob >= WATERMARK_REJECT

        # Subtitle detection
        subtitle_result = analyzers.detect_subtitles(frames)
        analysis.has_subtitle = subtitle_result["has_subtitle"]
        analysis.subtitle_confidence = subtitle_result["subtitle_confidence"]

        # Face detection
        face_result = analyzers.analyze_faces(frames)
        analysis.face_count = face_result["max_faces"]
        analysis.face_coverage = face_result["face_coverage"]
        analysis.has_faces = face_result["has_faces"]
        analysis.faces_stable = face_result["faces_stable"]

        # Object detection (YOLOv8 Nano, optional)
        obj_result = analyzers.analyze_objects(frames)
        analysis.object_count = obj_result["total_objects"]
        analysis.dominant_classes = obj_result["dominant_classes"]
        analysis.scene_type = obj_result["scene_type"]
        analysis.yolo_available = obj_result.get("available", False)

        # Motion
        motion = analyzers.measure_motion(frames)

        # Scoring
        analysis.score.quality = _score_quality(
            analysis.blur_score, analysis.brightness, analysis.black_frame_ratio
        )
        analysis.score.cleanliness = _score_cleanliness(
            analysis.watermark_prob, analysis.has_subtitle
        )
        analysis.score.uniqueness = _score_uniqueness(
            analysis.is_duplicate, analysis.phash, all_phashes
        )
        analysis.score.motion = motion

        # Reject actual image quality failures, not small source labels.
        if analysis.black_frame_ratio > BLACK_RATIO_REJECT:
            analysis.score.rejected = True
            analysis.score.reject_reason = "black_frame"
        elif analysis.brightness < BRIGHTNESS_REJECT and analysis.score.quality < 30:
            analysis.score.rejected = True
            analysis.score.reject_reason = "dark"
        elif analysis.blur_score < BLUR_REJECT and analysis.score.quality < 30:
            analysis.score.rejected = True
            analysis.score.reject_reason = "blur"
        elif analysis.is_duplicate:
            analysis.score.rejected = True
            analysis.score.reject_reason = "duplicate"

        # Final weighted score (only if not rejected)
        if not analysis.score.rejected:
            analysis.score.final_score = (
                analysis.score.quality * W_QUALITY
                + analysis.score.cleanliness * W_CLEANLINESS
                + analysis.score.uniqueness * W_UNIQUENESS
                + analysis.score.motion * W_MOTION
            )
        else:
            analysis.score.final_score = 0.0

    finally:
        _cleanup(frames)

    return analysis


def analyze_clips(
    clip_paths: List[str],
    *,
    progress_callback=None,
    frame_count: int = 3,
) -> List[ClipAnalysis]:
    """Run the V1 pipeline over a list of clip paths.

    Returns one ClipAnalysis per clip, sorted by final_score descending.
    Rejected clips are kept but flagged so the caller can decide.
    """
    if not clip_paths:
        return []

    results: List[ClipAnalysis] = []
    seen_phashes: Dict[str, str] = {}
    all_phashes: List[str] = []

    total = len(clip_paths)
    for i, path in enumerate(clip_paths):
        if not path or not os.path.exists(path):
            results.append(ClipAnalysis(path=path or "", scene_id=i,
                                       score=ClipScore(rejected=True,
                                                       reject_reason="missing_file")))
            continue
        try:
            analysis = analyze_single_clip(
                path, scene_id=i,
                seen_phashes=seen_phashes,
                all_phashes=all_phashes,
                frame_count=frame_count,
            )
        except Exception as exc:
            logger.warning("LAVIP: clip %s failed: %s", path, exc)
            analysis = ClipAnalysis(path=path, scene_id=i,
                                    score=ClipScore(rejected=True,
                                                    reject_reason=f"error:{type(exc).__name__}"))
        if analysis.phash:
            seen_phashes[analysis.phash] = path
            all_phashes.append(analysis.phash)
        results.append(analysis)
        if progress_callback:
            progress_callback((i + 1) / total, f"Scene understanding {i + 1}/{total}")

    # Sort by final score descending (rejected clips sink to bottom)
    results.sort(key=lambda a: (not a.score.rejected, a.score.final_score), reverse=True)
    return results


def filter_and_rank(analyses: List[ClipAnalysis], *, min_score: float = 60.0) -> List[ClipAnalysis]:
    """Return only clips that pass the quality bar, sorted by score."""
    return [a for a in analyses if not a.score.rejected and a.score.final_score >= min_score]


def clean_clips(analyses: List[ClipAnalysis]) -> List[ClipAnalysis]:
    """Compatibility pass: preserve the original sharp source pixels.

    Obstructive cards are handled by screening; small source labels are kept.
    Never mark an unchanged clip as cosmetically cleaned or boost its score.
    """
    return analyses
