"""Inline per-candidate ranking primitive for the per-scene continuity chain.

This is the "rank as you go" keystone: given a single downloaded candidate
(image or video clip) and the scene's context, return a fast verdict so the
chain can accept-or-continue immediately instead of deferring ranking to an
end-of-pipeline batch pass.

It reuses the existing, tested Visual Intelligence pipelines
(``ImageIntelligencePipeline`` / ``ClipIntelligencePipeline``) so scoring stays
identical to the rest of the app. When vision is unavailable the pipelines
degrade to a neutral "keep" verdict (fail-open), matching the app's policy.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

from services.visual_intelligence.context import VisualDecisionContext
from services.visual_intelligence.image_pipeline import ImageIntelligencePipeline
from services.visual_intelligence.clip_pipeline import ClipIntelligencePipeline


VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v")


def is_video(path: str) -> bool:
    return str(path or "").lower().endswith(VIDEO_EXTS)


@dataclass
class RankResult:
    """Outcome of ranking one candidate for one scene."""
    score: float = 0.0
    verdict: str = "keep"          # keep | replace | emergency
    is_video: bool = False
    vision_available: bool = False
    reason: str = ""

    @property
    def accepted(self) -> bool:
        """A candidate is accepted when the ranker says keep it."""
        return self.verdict == "keep"


def build_pipelines(
    visual_context: Optional[VisualDecisionContext],
) -> Tuple[ImageIntelligencePipeline, ClipIntelligencePipeline]:
    """Construct image/clip pipelines sharing one (possibly None) vision client.

    Passing ``None`` or a disabled context yields pipelines with no vision
    client — they still run the cheap CPU prefilter and fail open to a neutral
    keep, so the chain degrades gracefully instead of crashing.
    """
    client = None
    if visual_context is not None and getattr(visual_context, "enabled", False):
        try:
            client = visual_context.get_client()
        except Exception:
            client = None
    try:
        final_clip_frames = max(3, min(5, int(os.environ.get("VIDZEO_VIP_FINAL_CLIP_FRAMES", "3") or 3)))
    except Exception:
        final_clip_frames = 3
    visual_required = bool(getattr(visual_context, "visual_required", False))
    image_pipeline = ImageIntelligencePipeline(client, visual_required=visual_required)
    clip_pipeline = ClipIntelligencePipeline(
        client,
        max_frames=final_clip_frames,
        visual_required=visual_required,
    )
    return image_pipeline, clip_pipeline


def rank_candidate(
    path: str,
    scene_index: int,
    topic: str,
    narration: str,
    subject: str,
    *,
    image_pipeline: ImageIntelligencePipeline,
    clip_pipeline: ClipIntelligencePipeline,
    seen_phashes: Optional[List[int]] = None,
) -> RankResult:
    """Rank ONE candidate for a scene and return a keep/replace/emergency verdict.

    Never raises: any failure degrades to a neutral keep (fail-open) so a
    transient vision error never discards a downloaded asset.
    """
    seen_phashes = seen_phashes if seen_phashes is not None else []
    video = is_video(path)
    try:
        if video:
            analysis = clip_pipeline.analyze_clip(
                scene_index=scene_index,
                clip_path=path,
                topic=topic,
                narration=narration,
                subject=subject,
                seen_phashes=seen_phashes,
            )
            verdict = clip_pipeline.verdict_for(analysis)
        else:
            analysis = image_pipeline.analyze_scene_image(
                scene_index=scene_index,
                image_path=path,
                topic=topic,
                narration=narration,
                subject=subject,
                seen_phashes=seen_phashes,
            )
            verdict = image_pipeline.verdict_for(analysis)
        return RankResult(
            score=float(getattr(analysis, "final_score", 0.0) or 0.0),
            verdict=verdict,
            is_video=video,
            vision_available=bool(getattr(analysis, "vision_available", False)),
            reason=str(getattr(analysis, "reason", "") or getattr(analysis, "error", "") or ""),
        )
    except Exception as exc:
        # Fail-open: a ranking error must not drop a real asset.
        return RankResult(
            score=50.0, verdict="keep", is_video=video,
            vision_available=False, reason=f"rank_error:{type(exc).__name__}",
        )
