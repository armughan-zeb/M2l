"""YouTube Clip Intelligence Pipeline.

Per clip:
  1. Layer 1 — cheap CPU pre-filter (resolution, duration, aspect)
  2. Frame extraction — scene-aware sampling (3/5/8 frames), low-res 480px
  3. Layer 2 — Qwen 2.5 Flash Vision analysis (sequence of frames)
  4. Universal ranking engine → final score
  5. Optional LAVIP cleaning pass for watermarked/subtitled clips

CPU-friendly: one clip at a time, frames deleted after analysis, small
base64 payload, sequential Qwen calls.
"""
from __future__ import annotations

import os
from typing import List, Optional

from .models import ClipAnalysis
from .ranking import score_clip
from .vision_client import QwenVisionClient, VisionClientError
from .prompts import build_clip_prompt, CLIP_SYSTEM_PROMPT
from .prefilter import apply_clip_prefilter
from .frame_extractor import extract_clip_frames, cleanup_frames


DEFAULT_ACCEPT_THRESHOLD = float(
    os.environ.get("VIDZEO_VIP_CLIP_ACCEPT", "55") or 55
)
DEFAULT_EMERGENCY_THRESHOLD = float(
    os.environ.get("VIDZEO_VIP_CLIP_EMERGENCY", "28") or 28
)
MAX_FRAMES_PER_CLIP = int(os.environ.get("VIDZEO_VIP_MAX_FRAMES", "8") or 8)


def _is_budget_exhaustion(error: str) -> bool:
    """Budget/deadline exhaustion (pass could not run) vs real analysis failure."""
    text = (error or "").lower()
    return "budget" in text or "deadline" in text


class ClipIntelligencePipeline:
    def __init__(
        self,
        vision_client: Optional[QwenVisionClient],
        *,
        accept_threshold: float = DEFAULT_ACCEPT_THRESHOLD,
        emergency_threshold: float = DEFAULT_EMERGENCY_THRESHOLD,
        max_frames: int = MAX_FRAMES_PER_CLIP,
        visual_required: bool = False,
    ):
        self.vision_client = vision_client
        self.visual_required = bool(visual_required)
        self.accept_threshold = float(accept_threshold)
        self.emergency_threshold = float(emergency_threshold)
        self.max_frames = int(max_frames)

    def analyze_clip(
        self,
        scene_index: int,
        clip_path: str,
        topic: str,
        narration: str,
        subject: str,
        seen_phashes: Optional[List[int]] = None,
    ) -> ClipAnalysis:
        analysis = ClipAnalysis(
            path=clip_path or "",
            scene_index=scene_index,
            source_hint="youtube_clip",
        )
        seen_phashes = seen_phashes if seen_phashes is not None else []
        if not clip_path or not os.path.exists(clip_path):
            analysis.prefilter_rejected = True
            analysis.prefilter_reason = "missing_file"
            return analysis

        # Layer 1 — cheap CPU pre-filter
        apply_clip_prefilter(analysis, seen_phashes=seen_phashes)
        if analysis.prefilter_rejected:
            return analysis

        # Layer 2 — Qwen Vision across sequence of frames
        if self.vision_client is None:
            return analysis

        frames = []
        try:
            frames, duration = extract_clip_frames(
                clip_path,
                max_frames=self.max_frames,
            )
            if duration and not analysis.duration:
                analysis.duration = duration
            if not frames:
                analysis.error = "frame_extraction_failed"
                analysis.vision_available = False
                analysis.final_score = score_clip(analysis)
                return analysis
            prompt = build_clip_prompt(
                topic=topic, narration=narration, subject=subject,
                frame_count=len(frames),
            )
            result = self.vision_client.analyze_clip_frames(
                frame_paths=frames,
                user_prompt=prompt,
                system_prompt=CLIP_SYSTEM_PROMPT,
            )
            analysis.scene_description = result["scene_description"]
            analysis.category = result["category"]
            analysis.subjects = result["subjects"]
            analysis.shot_type = result["shot_type"]
            analysis.story_relevance = result["story_relevance"]
            analysis.visual_quality = result["visual_quality"]
            analysis.documentary_value = result["documentary_value"]
            analysis.emotional_score = result["emotional_score"]
            analysis.cinematic_score = result["cinematic_score"]
            analysis.watermark = result["watermark"]
            analysis.subtitle = result["subtitle"]
            analysis.has_text_overlay = result["has_text_overlay"]
            analysis.talking_head = bool(result.get("talking_head"))
            analysis.overlay_type = result.get("overlay_type", "none")
            analysis.overlay_regions = list(result.get("overlay_regions") or [])
            analysis.fatal_mismatch = bool(result.get("fatal_mismatch"))
            analysis.confidence = result.get("confidence", 0.0)
            analysis.best_usage = result["best_usage"]
            analysis.recommended_duration = result["recommended_duration"]
            analysis.transition = result["transition"]
            analysis.caption_position = result["caption_position"]
            analysis.music = result["music"]
            analysis.camera_movement = result["camera_movement"]
            analysis.vision_available = True
            analysis.final_score = score_clip(analysis)
        except VisionClientError as exc:
            analysis.error = str(exc)
            analysis.vision_available = False
            analysis.final_score = 50.0
        except Exception as exc:
            analysis.error = f"{type(exc).__name__}:{exc}"
            analysis.vision_available = False
            analysis.final_score = score_clip(analysis)
        finally:
            if frames:
                cleanup_frames(frames)
        return analysis

    def verdict_for(self, analysis: ClipAnalysis) -> str:
        """Return the compatibility decision for one analyzed clip.

        Retrieval code uses the richer V2 contract, while the legacy clip
        pipeline still exposes this small ``keep``/``replace``/``emergency``
        API. Keep the cheap prefilter and explicit safety/mismatch checks
        authoritative, and fail open only when a bounded vision call could
        not run.
        """
        if analysis.talking_head and str(
            os.environ.get("VIDZEO_YT_REJECT_TALKING_HEAD", "1") or "1"
        ).strip().lower() in {"1", "true", "yes", "on"}:
            th_conf = float(getattr(analysis, "talking_head_confidence", getattr(analysis, "confidence", 0.0)) or 0.0)
            th_thresh = float(os.environ.get("VIDZEO_YT_TALKING_HEAD_HARD_CONFIDENCE", "75") or 75)
            if th_conf >= th_thresh:
                return "replace"
            try:
                from services.scene_understanding.analyzers import hard_talking_head_signal
                if hard_talking_head_signal(
                    True,
                    th_conf,
                    getattr(analysis, "talking_head_reason", ""),
                ):
                    return "replace"
            except Exception:
                if th_conf >= 75.0:
                    return "replace"
        if analysis.prefilter_rejected and not os.path.exists(analysis.path):
            return "emergency"
        if analysis.prefilter_rejected:
            return "replace"
        reject_only_irrelevant = str(
            os.environ.get("VIDZEO_VISUAL_REJECT_ONLY_IRRELEVANT", "1")
        ).strip().lower() in {"1", "true", "yes", "on"}
        if analysis.fatal_mismatch:
            return "replace"
        # No vision signal -> trust the existing pipeline's choice.
        if not analysis.vision_available:
            if _is_budget_exhaustion(analysis.error):
                return "keep"
            return "replace" if self.visual_required else "keep"

        relevance_score = float(getattr(analysis, "story_relevance", getattr(analysis, "final_score", 0.0)) or 0.0)
        relevance_target = float(os.environ.get("VIDZEO_VIP_CLIP_ACCEPT", "50") or 50)
        if relevance_score >= relevance_target and not analysis.fatal_mismatch:
            return "keep"

        if reject_only_irrelevant:
            try:
                hard_reject_relevance = float(
                    os.environ.get("VIDZEO_VISUAL_HARD_REJECT_RELEVANCE", "45") or 45
                )
                hard_reject_confidence = float(
                    os.environ.get("VIDZEO_VISUAL_HARD_REJECT_CONFIDENCE", "70") or 70
                )
            except (TypeError, ValueError):
                hard_reject_relevance, hard_reject_confidence = 45.0, 70.0
            if (
                float(analysis.story_relevance or 0.0) < hard_reject_relevance
                and float(analysis.confidence or 0.0) >= hard_reject_confidence
            ):
                return "replace"
            return "keep"
        if analysis.watermark:
            return "replace"
        if analysis.final_score < self.emergency_threshold:
            return "emergency"
        if analysis.final_score < self.accept_threshold:
            return "replace"
        return "keep"
