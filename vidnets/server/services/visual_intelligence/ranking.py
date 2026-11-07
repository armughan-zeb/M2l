"""Universal ranking engine — the "taste" on top of Qwen's "eyes".

Implements the weighted final-score formulas exactly as specified in the
VidRush plan:

  Image final score:
      relevance 35% + visual quality 25% + historical accuracy 20%
      + composition 15% + cleanliness 5%

  Clip final score:
      story relevance 30% + visual quality 20% + documentary value 20%
      + emotional impact 10% + cinematic quality 10% + cleanliness 10%

Cleanliness is derived from detected watermark/subtitle/text-overlay flags.
The engine never raises; it always returns a 0-100 score so the pipeline
keeps running even when Qwen returns partial JSON.
"""
from __future__ import annotations

import hashlib
import os

from .models import ImageAnalysis, ClipAnalysis, RankedAsset, AssetType


# =============================================================================
# IMAGE RANKING
# =============================================================================

W_IMAGE_RELEVANCE = 0.35
W_IMAGE_QUALITY = 0.25
W_IMAGE_HISTORICAL = 0.20
W_IMAGE_COMPOSITION = 0.15
W_IMAGE_CLEANLINESS = 0.05

WATERMARK_PENALTY_IMAGE = 18.0
TEXT_PENALTY_IMAGE = 12.0


def score_image(analysis: ImageAnalysis) -> float:
    """Return the final 0-100 score for an image analysis record."""
    if analysis.prefilter_rejected:
        return 0.0
    if analysis.fatal_mismatch or analysis.unsafe_content or analysis.suggestive_content or analysis.synthetic_content:
        return 0.0
    if not analysis.vision_available:
        # Without vision we have no real signal; keep a neutral score so
        # the pipeline still has a stable order based on limb prefilter only.
        return 50.0

    score = (
        analysis.relevance * W_IMAGE_RELEVANCE
        + analysis.quality * W_IMAGE_QUALITY
        + analysis.historical_match * W_IMAGE_HISTORICAL
        + analysis.composition * W_IMAGE_COMPOSITION
    )

    cleanliness = 100.0
    if analysis.watermark:
        cleanliness -= 100.0
    if analysis.has_text:
        cleanliness -= 80.0
    cleanliness = max(0.0, cleanliness)

    score += cleanliness * W_IMAGE_CLEANLINESS

    # Additional hard penalties so dirty images cannot top clean ones even
    # when relevance is high.
    if analysis.watermark:
        score -= WATERMARK_PENALTY_IMAGE
    if analysis.has_text:
        score -= TEXT_PENALTY_IMAGE

    return max(0.0, min(100.0, score))


# =============================================================================
# CLIP RANKING
# =============================================================================

W_CLIP_STORY = 0.30
W_CLIP_QUALITY = 0.20
W_CLIP_DOCUMENTARY = 0.20
W_CLIP_EMOTION = 0.10
W_CLIP_CINEMA = 0.10
W_CLIP_CLEANLINESS = 0.10

WATERMARK_PENALTY_CLIP = 18.0
SUBTITLE_PENALTY_CLIP = 10.0
TEXT_OVERLAY_PENALTY_CLIP = 14.0


def score_clip(analysis: ClipAnalysis) -> float:
    if analysis.prefilter_rejected:
        return 0.0
    if analysis.fatal_mismatch:
        return 0.0
    if analysis.talking_head and str(
        os.environ.get("VIDZEO_YT_REJECT_TALKING_HEAD", "1") or "1"
    ).strip().lower() in {"1", "true", "yes", "on"}:
        return 0.0
    if not analysis.vision_available:
        return 50.0

    score = (
        analysis.story_relevance * W_CLIP_STORY
        + analysis.visual_quality * W_CLIP_QUALITY
        + analysis.documentary_value * W_CLIP_DOCUMENTARY
        + analysis.emotional_score * W_CLIP_EMOTION
        + analysis.cinematic_score * W_CLIP_CINEMA
    )

    cleanliness = 100.0
    if analysis.watermark:
        cleanliness -= 100.0
    if analysis.subtitle:
        cleanliness -= 60.0
    if analysis.has_text_overlay:
        cleanliness -= 70.0
    cleanliness = max(0.0, cleanliness)

    score += cleanliness * W_CLIP_CLEANLINESS

    if analysis.watermark:
        score -= WATERMARK_PENALTY_CLIP
    if analysis.subtitle:
        score -= SUBTITLE_PENALTY_CLIP
    if analysis.has_text_overlay:
        score -= TEXT_OVERLAY_PENALTY_CLIP

    return max(0.0, min(100.0, score))


# =============================================================================
# ASSET BUILDERS
# =============================================================================

def ranked_image_asset(analysis: ImageAnalysis, topic: str = "") -> RankedAsset:
    analysis.final_score = score_image(analysis)
    digest = hashlib.sha256(str(analysis.path or "").encode("utf-8", "replace")).hexdigest()[:12]
    asset_id = f"img_{analysis.scene_index:03d}_{digest}"
    return RankedAsset(
        asset_id=asset_id,
        type=AssetType.IMAGE,
        path=analysis.path,
        scene_index=analysis.scene_index,
        topic=topic,
        score=analysis.final_score,
        subjects=[s for s in (analysis.scene_description or "").split() if len(s) > 3][:5],
        usage=analysis.motion_effect or "static",
        analysis=analysis,
    )


def ranked_clip_asset(analysis: ClipAnalysis, topic: str = "") -> RankedAsset:
    analysis.final_score = score_clip(analysis)
    digest = hashlib.sha256(str(analysis.path or "").encode("utf-8", "replace")).hexdigest()[:12]
    asset_id = f"clip_{analysis.scene_index:03d}_{digest}"
    return RankedAsset(
        asset_id=asset_id,
        type=AssetType.VIDEO,
        path=analysis.path,
        scene_index=analysis.scene_index,
        topic=topic,
        score=analysis.final_score,
        subjects=list(analysis.subjects or [])[:5],
        usage=analysis.best_usage or "background",
        analysis=analysis,
    )
