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
from .prompts import (
    build_clip_batch_prompt,
    build_clip_prompt,
    CLIP_BATCH_SYSTEM_PROMPT,
    CLIP_SYSTEM_PROMPT,
)
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


def _apply_clip_result(analysis: ClipAnalysis, result: dict) -> ClipAnalysis:
    """Copy one normalised vision verdict onto a ClipAnalysis.

    Shared by the single-candidate path (``analyze_clip``) and the batched
    path (``analyze_clips_batch``) so a batched verdict produces exactly
    the same analysis fields — and therefore the same accept/reject
    decision — as a sequential verdict with the same scores.
    """
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
    analysis.batch_verdict = str(result.get("batch_verdict") or "")
    analysis.vision_available = True
    analysis.final_score = score_clip(analysis)
    return analysis


class ClipIntelligencePipeline:
    def __init__(
        self,
        vision_client: Optional[QwenVisionClient],
        *,
        accept_threshold: float = DEFAULT_ACCEPT_THRESHOLD,
        emergency_threshold: float = DEFAULT_EMERGENCY_THRESHOLD,
        max_frames: int = MAX_FRAMES_PER_CLIP,
        visual_required: bool = False,
        lane_budget=None,
        lane_scheduler=None,
    ):
        self.vision_client = vision_client
        self.visual_required = bool(visual_required)
        self.accept_threshold = float(accept_threshold)
        self.emergency_threshold = float(emergency_threshold)
        self.max_frames = int(max_frames)
        # Vision lanes: when a lane scheduler is supplied, batch vision
        # is filed through it (per-lane in-flight caps + decided-set
        # retry-only-missing) and charged to ``lane_budget``'s lane.
        # The client itself is normally a _ContextClient bound to that
        # lane's child context (see VisualDecisionContext.child), so
        # _reserve_call enforces the lane's call budget too. Without a
        # scheduler the pipeline behaves exactly as before.
        self.lane_budget = lane_budget
        self.lane_scheduler = lane_scheduler

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
            _apply_clip_result(analysis, result)
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

    def analyze_clips_batch(
        self,
        scene_index: int,
        clips: List[dict],
        topic: str,
        narration: str,
        subject: str,
        seen_phashes: Optional[List[int]] = None,
    ) -> dict:
        """Judge ALL of one scene's clip candidates in ONE vision call.

        ``clips`` is a list of ``{"candidate_id": str, "path": str}``
        (``candidate_id`` defaults to the path). Returns
        ``{candidate_id: ClipAnalysis}`` — one analysis per candidate that
        reached vision, with the same fields and the same
        ``verdict_for``/gate outcome as if each candidate had been judged
        singly with the same scores.

        Prefilter still runs per clip first (cheap CPU checks never spend
        a vision call), frames are extracted per clip, then a single
        ``analyze_clip_frames_batch`` judges every surviving candidate.
        A candidate the batch did not judge (missing row, encode failure)
        comes back with ``vision_available=False`` — the caller treats it
        exactly like a sequential vision miss (fail-open, as today).
        If the batch call itself fails, fall back to sequential
        ``analyze_clip`` per candidate: batching must never lose a scene
        that sequential vision could have judged.
        """
        seen_phashes = seen_phashes if seen_phashes is not None else []
        analyses: dict = {}
        batch_candidates: List[dict] = []
        batch_frames: dict = {}
        for clip in clips or []:
            if not isinstance(clip, dict):
                continue
            candidate_id = str(clip.get("candidate_id") or clip.get("path") or "")
            clip_path = str(clip.get("path") or "")
            if not candidate_id or not clip_path:
                continue
            analysis = ClipAnalysis(
                path=clip_path,
                scene_index=scene_index,
                source_hint="youtube_clip",
            )
            analyses[candidate_id] = analysis
            if not os.path.exists(clip_path):
                analysis.prefilter_rejected = True
                analysis.prefilter_reason = "missing_file"
                continue
            apply_clip_prefilter(analysis, seen_phashes=seen_phashes)
            if analysis.prefilter_rejected:
                continue
            batch_candidates.append({"candidate_id": candidate_id, "path": clip_path})
        if self.vision_client is None:
            return analyses
        # Vision lanes: batches are 4 candidates x 2 frames (8 images)
        # per request. The context layer splits by images-per-request
        # (transport ceiling 16) and files chunks in parallel;
        # truncating here would silently drop candidates from vision
        # (they would fail open instead of being judged).
        # Cap the total at 8 candidates (2 lane batches) as a safety bound.
        batch_candidates = batch_candidates[:8]
        if not batch_candidates:
            return analyses
        extracted: dict = {}
        try:
            for cand in batch_candidates:
                frames, duration = extract_clip_frames(
                    cand["path"],
                    max_frames=self.max_frames,
                )
                analysis = analyses[cand["candidate_id"]]
                if duration and not analysis.duration:
                    analysis.duration = duration
                if not frames:
                    analysis.error = "frame_extraction_failed"
                    analysis.vision_available = False
                    analysis.final_score = score_clip(analysis)
                    continue
                extracted[cand["candidate_id"]] = frames
            judged = [
                {"candidate_id": cand["candidate_id"], "frame_paths": extracted[cand["candidate_id"]]}
                for cand in batch_candidates
                if cand["candidate_id"] in extracted
            ]
            if not judged:
                return analyses
            prompt = build_clip_batch_prompt(
                topic=topic,
                narration=narration,
                subject=subject,
                candidate_ids=[c["candidate_id"] for c in judged],
            )
            if self.lane_scheduler is not None:
                # File through the lane scheduler: per-lane in-flight
                # caps, and a partial batch retries only its missing
                # candidates (never re-judges a decided one). The vision
                # client call itself still goes through the lane child's
                # _ContextClient, so calls are charged to the YT lane.
                by_id = {c["candidate_id"]: c for c in judged}

                def _file_batch(ids):
                    subset = [by_id[cid] for cid in ids if cid in by_id]
                    if not subset:
                        return {}
                    return self.vision_client.analyze_clip_frames_batch(
                        subset,
                        user_prompt=prompt,
                        system_prompt=CLIP_BATCH_SYSTEM_PROMPT,
                    )

                results = self.lane_scheduler.file_batch(
                    "youtube",
                    f"yt-scene-{scene_index}",
                    [c["candidate_id"] for c in judged],
                    _file_batch,
                )
            else:
                results = self.vision_client.analyze_clip_frames_batch(
                    judged,
                    user_prompt=prompt,
                    system_prompt=CLIP_BATCH_SYSTEM_PROMPT,
                )
            for cand in batch_candidates:
                candidate_id = cand["candidate_id"]
                if candidate_id not in extracted:
                    continue
                analysis = analyses[candidate_id]
                result = (results or {}).get(candidate_id)
                if not isinstance(result, dict):
                    # Batch judged the others but not this one: same as a
                    # sequential vision miss — fail-open downstream.
                    analysis.error = "batch_verdict_missing"
                    analysis.vision_available = False
                    analysis.final_score = score_clip(analysis)
                    continue
                _apply_clip_result(analysis, result)
        except Exception as exc:
            # A failed batch must never lose the scene: judge the survivors
            # singly, exactly as the old sequential path did.
            for cand in batch_candidates:
                candidate_id = cand["candidate_id"]
                if candidate_id not in extracted:
                    continue
                analysis = analyses[candidate_id]
                if analysis.vision_available:
                    continue
                try:
                    single = self.analyze_clip(
                        scene_index,
                        cand["path"],
                        topic,
                        narration,
                        subject,
                        seen_phashes=seen_phashes,
                    )
                    analyses[candidate_id] = single
                except Exception:
                    analysis.error = f"{type(exc).__name__}:{exc}"
                    analysis.vision_available = False
                    analysis.final_score = score_clip(analysis)
        finally:
            for frames in extracted.values():
                if frames:
                    cleanup_frames(frames)
        return analyses

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
