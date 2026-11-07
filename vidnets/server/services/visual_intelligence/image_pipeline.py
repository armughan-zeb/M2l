"""Web Image Intelligence Pipeline.

Per scene:
  1. Layer 1 — cheap CPU pre-filter (perceptual hash, resolution, aspect)
  2. Layer 2 — Qwen 2.5 Flash Vision analysis (single image)
  3. Universal ranking engine → final score
  4. Verdict: keep, replace, or emergency

Designed for low-end CPU: one image at a time, small base64 payload, no
parallel Qwen calls (rate limits + RAM friendliness).
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from .models import ImageAnalysis
from .ranking import score_image, ranked_image_asset, RankedAsset
from .vision_client import QwenVisionClient, VisionClientError
from .prompts import build_image_prompt, IMAGE_SYSTEM_PROMPT
from .prefilter import apply_image_prefilter


# Acceptance threshold — below this a scene is flagged for replacement.
DEFAULT_ACCEPT_THRESHOLD = float(
    os.environ.get("VIDZEO_VIP_IMAGE_ACCEPT", "55") or 55
)
# Hard reject — even worse than replace; emit an emergency placeholder.
DEFAULT_EMERGENCY_THRESHOLD = float(
    os.environ.get("VIDZEO_VIP_IMAGE_EMERGENCY", "25") or 25
)


def _is_budget_exhaustion(error: str) -> bool:
    """Budget/deadline exhaustion (pass could not run) vs real analysis failure."""
    return "budget" in (error or "").lower() or "deadline" in (error or "").lower()


class ImageIntelligencePipeline:
    def __init__(
        self,
        vision_client: Optional[QwenVisionClient],
        *,
        accept_threshold: float = DEFAULT_ACCEPT_THRESHOLD,
        emergency_threshold: float = DEFAULT_EMERGENCY_THRESHOLD,
        visual_required: bool = False,
    ):
        self.vision_client = vision_client
        self.visual_required = bool(visual_required)
        self.accept_threshold = float(accept_threshold)
        self.emergency_threshold = float(emergency_threshold)

    def analyze_scene_image(
        self,
        scene_index: int,
        image_path: str,
        topic: str,
        narration: str,
        subject: str,
        seen_phashes: List[int],
    ) -> ImageAnalysis:
        analysis = ImageAnalysis(
            path=image_path or "",
            scene_index=scene_index,
            source_hint="web_image",
        )
        if not image_path or not os.path.exists(image_path):
            analysis.prefilter_rejected = True
            analysis.prefilter_reason = "missing_file"
            return analysis

        # Layer 1 — cheap CPU pre-filter
        apply_image_prefilter(analysis, seen_phashes=seen_phashes)
        if analysis.prefilter_rejected:
            return analysis
        if analysis.phash is not None:
            seen_phashes.append(analysis.phash)

        # Layer 2 — Qwen Vision (graceful degradation if vision unavailable)
        if self.vision_client is None:
            return analysis
        try:
            prompt = build_image_prompt(topic=topic, narration=narration, subject=subject)
            result = self.vision_client.analyze_image(
                image_path=image_path,
                user_prompt=prompt,
                system_prompt=IMAGE_SYSTEM_PROMPT,
            )
            analysis.relevance = result["relevance"]
            analysis.quality = result["quality"]
            analysis.historical_match = result["historical_match"]
            analysis.composition = result["composition"]
            analysis.watermark = result["watermark"]
            analysis.has_text = result["has_text"]
            analysis.unsafe_content = bool(result.get("unsafe_content"))
            analysis.suggestive_content = bool(result.get("suggestive_content"))
            analysis.synthetic_content = bool(result.get("synthetic_content"))
            analysis.overlay_type = result.get("overlay_type", "none")
            analysis.overlay_regions = list(result.get("overlay_regions") or [])
            analysis.fatal_mismatch = bool(result.get("fatal_mismatch"))
            analysis.confidence = result.get("confidence", 0.0)
            analysis.reason = result["reason"]
            analysis.scene_description = result["scene_description"]
            analysis.motion_effect = result["motion_effect"]
            analysis.text_safe_area = result["text_safe_area"]
            analysis.vision_available = True
            # Qwen returns its own final_score; we recompute via our ranking
            # engine so the weighting is consistent with the spec.
            analysis.final_score = score_image(analysis)
        except VisionClientError as exc:
            analysis.error = str(exc)
            # If vision fails, fall back to a neutral score so the existing
            # image stays in place rather than getting replaced arbitrarily.
            analysis.vision_available = False
            analysis.final_score = 50.0
        except Exception as exc:
            analysis.error = f"{type(exc).__name__}:{exc}"
            analysis.vision_available = False
            analysis.final_score = score_image(analysis)
        return analysis

    def verdict_for(self, analysis: ImageAnalysis) -> str:
        """Return one of: ``keep`` | ``replace`` | ``emergency``."""
        if analysis.prefilter_rejected and not os.path.exists(analysis.path):
            return "emergency"
        if analysis.prefilter_rejected:
            return "replace"
        if analysis.fatal_mismatch or analysis.watermark:
            return "replace"
        if analysis.unsafe_content or analysis.suggestive_content or analysis.synthetic_content:
            return "replace"
        # When Qwen Vision is unavailable, we have no signal to challenge
        # the existing pipeline's pick, so trust it. Prefilter rejections
        # above are still honored (those are cheap CPU facts, not vision).
        if not analysis.vision_available:
            if _is_budget_exhaustion(analysis.error):
                # Budget/deadline exhaustion means the pass could not RUN -
                # not that the asset failed. Fail open (keep) so a cost guard
                # does not blank the whole plan and trigger a re-fetch loop.
                return "keep"
            return "replace" if self.visual_required else "keep"
        # Aligned pass is a semantic acceptance floor, matching the retrieval
        # gate (decision.py): a scene must meet the minimum relevance to ship.
        # Qwen inflates scores, so a single-model "relevance" below the floor
        # is treated as a mismatch regardless of stated confidence.
        min_relevance = float(
            os.environ.get("VIDZEO_WEB_MIN_RELEVANCE", "55") or 55
        )
        if analysis.relevance < min_relevance:
            return "replace"
        return "keep"
