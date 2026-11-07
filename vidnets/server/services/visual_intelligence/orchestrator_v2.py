"""Bounded, parallel visual selection over final scene-aligned media."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from ..job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .clip_pipeline import ClipIntelligencePipeline
from .decision import (
    SCENE_DECISION_VERSION,
    asset_fingerprint,
    normalize_scene_decision,
    scene_context_hash,
)
from .context import VisualDecisionContext
from .database import build_summary, write_content_database
from .image_pipeline import ImageIntelligencePipeline
from .ranking import ranked_clip_asset, ranked_image_asset


VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".avi")


class VisualIntelligenceError(Exception):
    pass


def _is_video(path: str) -> bool:
    return str(path or "").lower().endswith(VIDEO_EXTS)


def _stable_id(prefix: str, scene_index: int, path: str) -> str:
    digest = hashlib.sha256(str(path or "").encode("utf-8", "replace")).hexdigest()[:12]
    return f"{prefix}_{scene_index:03d}_{digest}"


def _scene_context(config: Dict[str, Any], index: int) -> Tuple[str, str, str]:
    title = str(config.get("title") or config.get("name") or "Documentary")
    scene_texts = list(config.get("__vip_scene_texts") or [])
    subjects = list(config.get("__vip_section_subjects") or [])
    narration = scene_texts[index] if 0 <= index < len(scene_texts) else title
    subject = subjects[index] if 0 <= index < len(subjects) and subjects[index] else title
    topic = str(config.get("_topic_anchor") or title)
    return topic, narration, subject


def _text_terms(value: str) -> set:
    return {
        word
        for word in re.findall(r"[a-z0-9]{3,}", str(value or "").lower())
        if word not in {
            "the", "and", "that", "this", "with", "from", "into", "were",
            "was", "are", "for", "its", "his", "her", "their", "about",
        }
    }


def _recover_image_slots_from_local_clips(
    *,
    selected_paths: List[Optional[str]],
    selected_labels: List[str],
    verdicts: List[str],
    results: List[Optional[Dict[str, Any]]],
    media_plan: Dict[str, Any],
    job_config: Dict[str, Any],
    context: VisualDecisionContext,
) -> int:
    """Bounded, zero-download frame recovery for final image-assigned slots."""
    final_grouped = (
        (media_plan.get("metadata") or {})
        .get("final_media_plan", {})
        .get("grouped_indices", {})
    )
    expected_images = (
        set(final_grouped.get("Web Images") or [])
        | set(final_grouped.get("AI Images") or [])
    )
    missing_images = [
        index
        for index in sorted(expected_images)
        if 0 <= index < len(selected_paths) and not selected_paths[index]
    ]
    clip_indices = [
        index
        for index, path in enumerate(selected_paths)
        if path and _is_video(path) and os.path.exists(path)
    ]
    client = context.get_client()
    if not missing_images or not clip_indices or client is None:
        return 0
    try:
        cap = max(
            0,
            int(os.environ.get("VIDZEO_VIP_FRAME_RECOVERY_CAP", "12") or 12),
        )
    except Exception:
        cap = 12
    missing_images = missing_images[:cap]
    if not missing_images:
        return 0

    scene_texts = list(job_config.get("__vip_scene_texts") or [])
    subjects = list(job_config.get("__vip_section_subjects") or [])
    title = str(job_config.get("_topic_anchor") or job_config.get("title") or "Documentary")

    def recover(index: int):
        from .frame_extractor import cleanup_frames, extract_clip_frames

        target_text = scene_texts[index] if index < len(scene_texts) else title
        target_terms = _text_terms(target_text)
        target_subject = subjects[index] if index < len(subjects) else ""
        ranked = []
        seen_paths = set()
        for clip_index in clip_indices:
            clip_path = str(selected_paths[clip_index] or "")
            clip_key = os.path.abspath(clip_path).lower()
            if clip_key in seen_paths:
                continue
            seen_paths.add(clip_key)
            candidate_subject = subjects[clip_index] if clip_index < len(subjects) else ""
            if target_subject and candidate_subject and target_subject.lower() != candidate_subject.lower():
                continue
            candidate_text = (
                scene_texts[clip_index]
                if clip_index < len(scene_texts)
                else title
            )
            candidate_terms = _text_terms(candidate_text)
            overlap = len(target_terms & candidate_terms) / max(
                1,
                len(target_terms | candidate_terms),
            )
            ranked.append((overlap, -abs(clip_index - index), clip_path))
        if not ranked:
            return index, "", None
        ranked.sort(reverse=True)
        clip_path = ranked[0][2]
        frames = []
        try:
            frames, _duration = extract_clip_frames(
                clip_path,
                max_frames=2,
                long_edge=720,
            )
            if not frames:
                return index, "", None
            comparison = client.compare_images(
                frames[:4],
                (
                    f"Scene narration: {target_text[:500]}\n"
                    f"Documentary topic: {title}\n"
                    f"Required subject: {target_subject or title}\n"
                    "Select a frame only if it accurately depicts the narration, is "
                    "sharp, and has no ownership watermark, subtitle, title card, or "
                    "large text. Return selected_index, confidence, reason, and a "
                    "candidates array with relevance, fatal_mismatch, "
                    "ownership_watermark, and text_overlay for every frame. If none "
                    "qualifies, set confidence below 50."
                ),
            )
            chosen = max(
                0,
                min(len(frames) - 1, int(comparison.get("selected_index") or 0)),
            )
            rows = comparison.get("candidates")
            rows = rows if isinstance(rows, list) else []
            row = rows[chosen] if chosen < len(rows) and isinstance(rows[chosen], dict) else {}

            def number(*keys, default=0.0):
                for key in keys:
                    try:
                        if row.get(key) is not None:
                            return float(row.get(key))
                    except Exception:
                        pass
                return float(default)

            def flag(*keys):
                return any(
                    value is True
                    or str(value or "").strip().lower() in {"1", "true", "yes", "on"}
                    for value in (row.get(key) for key in keys)
                )

            confidence = float(comparison.get("confidence") or 0.0)
            relevance = number(
                "relevance",
                "scene_relevance",
                "score",
                default=confidence if not rows else 0.0,
            )
            dirty = flag(
                "fatal_mismatch",
                "ownership_watermark",
                "watermark",
                "subtitle",
                "text_overlay",
                "has_text",
            )
            if confidence < 60.0 or relevance < 55.0 or dirty:
                return index, "", None
            target = (
                Path(clip_path).parent
                / f"vip_frame_{index:04d}_{int(time.time() * 1000)}.jpg"
            )
            shutil.copy2(frames[chosen], target)
            record = {
                "asset_id": _stable_id("frame", index, str(target)),
                "type": "image",
                "path": str(target),
                "scene_index": index,
                "topic": title,
                "score": round(relevance, 2),
                "analysis": {
                    "recovered_from_clip": clip_path,
                    "confidence": confidence,
                    "relevance": relevance,
                    "reason": str(comparison.get("reason") or ""),
                },
                "verdict": "keep",
                "recovery": "qwen_local_youtube_frame",
            }
            return index, str(target), record
        except Exception:
            return index, "", None
        finally:
            if frames:
                cleanup_frames(frames)

    recovered = 0
    workers = max(1, min(context.max_concurrent, len(missing_images)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="visual-frame") as executor:
        futures = {executor.submit(recover, index): index for index in missing_images}
        for future in as_completed(futures):
            index = futures[future]
            try:
                result_index, frame_path, record = future.result()
            except Exception:
                continue
            if not frame_path or record is None:
                continue
            selected_paths[result_index] = frame_path
            selected_labels[result_index] = "Visual Intelligence (YT frame)"
            verdicts[result_index] = "keep"
            results[result_index] = record
            recovered += 1
    return recovered


def run_visual_intelligence_pass(
    media_plan: Dict[str, Any],
    job_config: Dict[str, Any],
    report_progress=None,
    visual_context: Optional[VisualDecisionContext] = None,
) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
    if not isinstance(media_plan, dict) or not isinstance(job_config, dict):
        raise VisualIntelligenceError("media_plan and job_config must be dictionaries")

    paths = list(media_plan.get("media_paths") or [])
    labels = list(media_plan.get("source_labels") or [])
    labels.extend([""] * max(0, len(paths) - len(labels)))
    if not paths:
        summary = {
            "total": 0,
            "kept": 0,
            "replaced": 0,
            "emergency": 0,
            "vision_available": False,
        }
        return media_plan, "", summary

    context = visual_context or VisualDecisionContext.from_config(job_config, len(paths))
    client = context.get_client()
    degraded_reason = str(context.degraded_reason or "") if client is None else ""
    if degraded_reason:
        print(
            f"[visual] pass degraded ({degraded_reason}); applying configured availability policy",
            flush=True,
        )
    image_pipeline = ImageIntelligencePipeline(client, visual_required=bool(context.visual_required))
    try:
        final_clip_frames = max(
            3,
            min(
                5,
                int(os.environ.get("VIDZEO_VIP_FINAL_CLIP_FRAMES", "3") or 3),
            ),
        )
    except Exception:
        final_clip_frames = 3
    clip_pipeline = ClipIntelligencePipeline(
        client,
        max_frames=final_clip_frames,
        visual_required=bool(context.visual_required),
    )
    results: List[Optional[Dict[str, Any]]] = [None] * len(paths)
    verdicts = ["keep"] * len(paths)
    selected_paths: List[Optional[str]] = list(paths)
    selected_labels = list(labels)
    decision_store = (media_plan.setdefault("metadata", {}).get("visual_decisions") or {})
    if isinstance(decision_store, list):
        decision_store = {str(row.get("scene_index")): row for row in decision_store if isinstance(row, dict)}
    if not isinstance(decision_store, dict):
        decision_store = {}
    media_plan.setdefault("metadata", {})["visual_decisions"] = decision_store

    def analyze(index: int):
        path = paths[index]
        topic, narration, subject = _scene_context(job_config, index)
        media_type = "video" if _is_video(path) else "image"
        context_hash = scene_context_hash(
            narration=narration, subject=subject, topic=topic, media_type=media_type,
        )
        fingerprint = asset_fingerprint(path)
        cached = decision_store.get(str(index))
        retrieval_decisions = (media_plan.get("metadata") or {}).get("retrieval_visual_decisions") or {}
        retrieval_cached = retrieval_decisions.get(str(index)) if isinstance(retrieval_decisions, dict) else None
        if isinstance(retrieval_cached, dict) and not isinstance(cached, dict):
            cached = retrieval_cached
        reuse_enabled = str(os.environ.get("VIDZEO_VISUAL_DECISION_REUSE", "1")).strip().lower() in {"1", "true", "yes", "on"}
        if reuse_enabled and (
            isinstance(cached, dict)
            and cached.get("version") == SCENE_DECISION_VERSION
            and cached.get("scene_context_hash") == context_hash
            and cached.get("asset_fingerprint") == fingerprint
            and cached.get("status") in ({"accepted"} if context.visual_required else {"accepted", "unverified"})
        ):
            verdict = "keep"
            return index, verdict, {
                "asset_id": _stable_id("cached", index, path),
                "type": media_type,
                "path": path,
                "scene_index": index,
                "topic": topic,
                "score": float(cached.get("relevance") or 50.0),
                "analysis": cached,
                "verdict": verdict,
                "decision_reused": True,
                "scene_decision": cached,
            }
        if not path or not os.path.exists(path):
            return index, "emergency", {
                "asset_id": _stable_id("empty", index, path or ""),
                "type": "video" if _is_video(path) else "image",
                "path": path or "",
                "scene_index": index,
                "topic": topic,
                "score": 0.0,
                "analysis": None,
                "verdict": "emergency",
            }
        if _is_video(path):
            analysis = clip_pipeline.analyze_clip(
                scene_index=index,
                clip_path=path,
                topic=topic,
                narration=narration,
                subject=subject,
                seen_phashes=[],
            )
            ranked = ranked_clip_asset(analysis, topic=topic)
            verdict = clip_pipeline.verdict_for(analysis)
        else:
            analysis = image_pipeline.analyze_scene_image(
                scene_index=index,
                image_path=path,
                topic=topic,
                narration=narration,
                subject=subject,
                seen_phashes=[],
            )
            ranked = ranked_image_asset(analysis, topic=topic)
            verdict = image_pipeline.verdict_for(analysis)
        record = {**ranked.to_dict(), "verdict": verdict}
        raw_analysis = record.get("analysis") or {}
        decision = normalize_scene_decision(
            raw_analysis,
            scene_index=index,
            media_type=media_type,
            source=labels[index] if index < len(labels) else "",
            model=context.model if client else "",
            asset_fingerprint_value=fingerprint,
            scene_context_hash_value=context_hash,
        ).to_dict()
        record["scene_decision"] = decision
        return index, verdict, record

    completed = 0
    if client is None and degraded_reason:
        # Strict factual jobs fail closed when vision cannot run. Non-strict
        # legacy jobs keep their previous availability behavior.
        strict_unavailable = bool(context.visual_required and getattr(context, "fail_closed", False))
        for index, path in enumerate(paths):
            results[index] = {
                "asset_id": _stable_id("unverified", index, path or ""),
                "type": "video" if _is_video(path) else "image",
                "path": path or "",
                "scene_index": index,
                "topic": _scene_context(job_config, index)[0],
                "score": 50.0,
                "analysis": None,
                "verdict": "replace" if strict_unavailable else "keep",
                "error": f"vision_unavailable:{degraded_reason}",
            }
            if strict_unavailable:
                selected_paths[index] = None
                selected_labels[index] = f"{labels[index]} (visual unavailable)".strip()
        verdicts = (["replace"] if strict_unavailable else ["keep"]) * len(paths)
        completed = len(paths)
    else:
        workers = max(1, min(context.max_concurrent, len(paths)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="visual-final") as executor:
            futures = {executor.submit(analyze, index): index for index in range(len(paths))}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    result_index, verdict, record = future.result()
                except Exception as exc:
                    result_index = index
                    # A vision failure means the pass could not RUN (API/decode/
                    # budget error) — it did not observe a bad asset. Fail open and
                    # KEEP the retrieved pick by default (VIDZEO_VIP_FAIL_OPEN=1) so a
                    # transient hiccup does not blank a scene and trigger an expensive
                    # re-fetch. Set VIDZEO_VIP_FAIL_OPEN=0 to restore fail-closed.
                    _fail_open = str(
                        os.environ.get("VIDZEO_VIP_FAIL_OPEN", "0")
                    ).strip().lower() in {"1", "true", "yes", "on"}
                    verdict = "keep" if (
                        not context.visual_required
                        or not getattr(context, "fail_closed", False)
                        or _fail_open
                        or not context.enabled
                    ) else "replace"
                    record = {
                        "asset_id": _stable_id("error", index, paths[index] or ""),
                        "type": "video" if _is_video(paths[index]) else "image",
                        "path": paths[index] or "",
                        "scene_index": index,
                        "score": 50.0,
                        "analysis": None,
                        "verdict": verdict,
                        "error": f"{type(exc).__name__}:{exc}",
                    }
                results[result_index] = record
                verdicts[result_index] = verdict
                decision = record.get("scene_decision") if isinstance(record, dict) else None
                if isinstance(decision, dict):
                    decision_store[str(result_index)] = decision
                if verdict in {"replace", "emergency"}:
                    selected_paths[result_index] = None
                    selected_labels[result_index] = (
                        f"{labels[result_index]} (visual {verdict})".strip()
                    )
                completed += 1
                if report_progress:
                    try:
                        report_progress(
                            completed / max(1, len(paths)),
                            f"Visual ranking {completed}/{len(paths)} scenes",
                        )
                    except Exception:
                        pass

    recovered_frames = _recover_image_slots_from_local_clips(
        selected_paths=selected_paths,
        selected_labels=selected_labels,
        verdicts=verdicts,
        results=results,
        media_plan=media_plan,
        job_config=job_config,
        context=context,
    )
    summary = build_summary(verdicts, client is not None, context.model if client else "")
    summary.update(context.summary())
    summary["local_frame_recoveries"] = recovered_frames
    manifest_path = write_content_database(
        [record for record in results if record is not None],
        job_name=str(job_config.get("name") or job_config.get("title") or "video"),
        model=context.model if client else "",
        summary=summary,
    )
    media_plan["media_paths"] = selected_paths
    media_plan["source_labels"] = selected_labels
    metadata = media_plan.setdefault("metadata", {})
    metadata["visual_intelligence"] = summary
    metadata["visual_intelligence_manifest"] = manifest_path
    metadata["visual_intelligence_verdicts"] = verdicts
    metadata["visual_intelligence_records"] = [
        {
            "scene_index": record.get("scene_index"),
            "asset_id": record.get("asset_id"),
            "score": record.get("score"),
            "verdict": record.get("verdict"),
        }
        for record in results
        if record is not None
    ]
    return media_plan, manifest_path, summary
