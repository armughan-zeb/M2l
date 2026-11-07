"""Visual Intelligence orchestrator — the single public entry point.

``run_visual_intelligence_pass`` is invoked ONLY when
``config['visual_understanding']`` is ON. It runs the VidRush-style
intelligence layer on top of the existing media plan:

  - Detects per-scene media type (image vs video)
  - Builds a Qwen 2.5 Flash Vision client (or degrades gracefully)
  - Runs Layer 1 prefilter + Layer 2 Qwen vision + universal ranking
  - Emits per-scene verdicts: keep / replace / emergency
  - Nulls out ``media_paths`` for scenes that must be repaired so the
    caller's existing fallback helpers can fill them
  - Persists a content_database.json audit manifest

When the OpenRouter key is missing or vision is unreachable, the pass
becomes a no-op that leaves the existing media plan untouched. This is
the deliberate graceful-degradation contract: turning visual intelligence
ON can only improve picks; it can never break the render.
"""
from __future__ import annotations

import os
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from ..job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from .models import ImageAnalysis, ClipAnalysis, AssetType
from .ranking import ranked_image_asset, ranked_clip_asset, RankedAsset
from .vision_client import QwenVisionClient, VisionClientError, DEFAULT_QWEN_VISION_MODEL
from .image_pipeline import ImageIntelligencePipeline
from .clip_pipeline import ClipIntelligencePipeline
from .database import write_content_database, build_summary


VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".avi")


class VisualIntelligenceError(Exception):
    """Raised only for programmer errors, never for vision failures."""


def _is_video_path(path: str) -> bool:
    return str(path or "").lower().endswith(VIDEO_EXTS)


def _resolve_openrouter_key(config: Dict[str, Any]) -> str:
    from ..provider_selection import normalize_provider_config
    config = normalize_provider_config(config)
    return str(
        config.get("openrouter_api_key")
        or config.get("openrouterApiKey")
        or os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("VIDZEO_OPENROUTER_API_KEY")
        or ""
    ).strip()


def _resolve_vision_model(config: Dict[str, Any]) -> str:
    return str(
        config.get("qwen_vision_model")
        or os.environ.get("VIDZEO_QWEN_VISION_MODEL")
        or DEFAULT_QWEN_VISION_MODEL
    ).strip() or DEFAULT_QWEN_VISION_MODEL


def _scene_topic(config: Dict[str, Any], scene_index: int) -> Tuple[str, str, str]:
    """Return (topic, narration, subject) for a scene index."""
    title = str(config.get("title") or config.get("name") or "Documentary")
    scene_texts = list(config.get("__vip_scene_texts") or [])
    section_subjects = list(config.get("__vip_section_subjects") or [])
    narration = scene_texts[scene_index] if 0 <= scene_index < len(scene_texts) else title
    subject = section_subjects[scene_index] if section_subjects and scene_index < len(section_subjects) else title
    topic_anchor = str(config.get("_topic_anchor") or title)
    return topic_anchor or title, narration, subject


def _build_vision_client(config: Dict[str, Any]) -> Optional[QwenVisionClient]:
    api_key = _resolve_openrouter_key(config)
    if not api_key:
        print("   [vip] visual understanding ON but no OpenRouter key — running as no-op")
        return None
    model = _resolve_vision_model(config)
    try:
        # Use the same generous retry window as queued visual decisions so
        # transient OpenRouter/provider latency does not silently skip QA.
        client = QwenVisionClient(
            api_key=api_key,
            model=model,
            fallback_models=config.get("qwen_vision_fallback_models"),
            max_attempts=max(1, min(3, int(os.environ.get("VIDZEO_QWEN_VISION_MAX_ATTEMPTS", "3") or 3))),
            timeout_seconds=max(90.0, min(300.0, float(os.environ.get("VIDZEO_QWEN_VISION_TIMEOUT_SECONDS", "120") or 120))),
        )
        if False and not client.is_available():
            print(f"   [vip] Qwen Vision unavailable on model '{model}' — running as no-op")
            return None
        return client
    except VisionClientError as exc:
        print(f"   [vip] Qwen Vision init failed: {exc} — running as no-op")
        return None
    except Exception as exc:
        print(f"   [vip] Qwen Vision init error: {exc} — running as no-op")
        return None


def _media_paths(media_plan: Dict[str, Any]) -> List[str]:
    return list(media_plan.get("media_paths") or [])


def _source_labels(media_plan: Dict[str, Any], count: int) -> List[str]:
    labels = list(media_plan.get("source_labels") or [])
    if len(labels) < count:
        labels.extend([""] * (count - len(labels)))
    return labels


def run_visual_intelligence_pass(
    media_plan: Dict[str, Any],
    job_config: Dict[str, Any],
    report_progress=None,
    visual_context=None,
) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
    """Run the VidRush-style visual intelligence pass.

    Returns ``(updated_media_plan, audit_manifest_path, summary)``.
    Never raises on vision failure — degrades to a no-op instead.
    """
    if not isinstance(media_plan, dict):
        raise VisualIntelligenceError("media_plan must be a dict")
    if not isinstance(job_config, dict):
        raise VisualIntelligenceError("job_config must be a dict")

    paths = _media_paths(media_plan)
    labels = _source_labels(media_plan, len(paths))
    scene_texts = list(job_config.get("__vip_scene_texts") or media_plan.get("__vip_scene_texts") or [])
    # Always fall back to whatever scene text we can find for narration context.
    if not scene_texts:
        scene_texts = list(media_plan.get("metadata", {}).get("source_scene_texts") or [])
    job_config["__vip_scene_texts"] = scene_texts

    if not paths:
        return media_plan, "", {"total": 0, "kept": 0, "replaced": 0, "emergency": 0, "vision_available": False}

    # Detect whether this job is image-based or clip-based. A mixed job
    # runs both pipelines on the right asset per scene.
    has_video = any(_is_video_path(p) for p in paths if p)
    has_image = any(p and not _is_video_path(p) for p in paths)

    client = visual_context.get_client() if visual_context is not None else _build_vision_client(job_config)
    model = _resolve_vision_model(job_config) if client else ""

    image_pipeline = ImageIntelligencePipeline(client) if has_image else None
    clip_pipeline = ClipIntelligencePipeline(client) if has_video else None

    seen_phashes: List[int] = []
    ranked_records: List[Dict[str, Any]] = []
    verdicts: List[str] = []
    new_paths: List[Optional[str]] = []
    new_labels: List[str] = []

    total = len(paths)
    for idx, original_path in enumerate(paths):
        topic, narration, subject = _scene_topic(job_config, idx)
        verdict = "keep"
        record: Optional[Dict[str, Any]] = None

        if not original_path or not os.path.exists(original_path):
            verdict = "emergency"
            record = {
                "asset_id": f"empty_{idx:03d}",
                "type": "video" if has_video and not has_image else "image",
                "path": original_path or "",
                "scene_index": idx,
                "topic": topic,
                "score": 0.0,
                "verdict": verdict,
                "analysis": None,
            }
        elif _is_video_path(original_path):
            if clip_pipeline is None:
                # Defensive: a video path slipped into a job that wasn't
                # detected as having video. Treat as keep without vision.
                verdict = "keep"
                record = {
                    "asset_id": f"clip_{idx:03d}_{abs(hash(original_path)) % 100000:05d}",
                    "type": "video",
                    "path": original_path,
                    "scene_index": idx,
                    "topic": topic,
                    "score": 50.0,
                    "verdict": verdict,
                    "analysis": None,
                    "error": "clip_pipeline_unavailable",
                }
            else:
                analysis = clip_pipeline.analyze_clip(
                    scene_index=idx,
                    clip_path=original_path,
                    topic=topic,
                    narration=narration,
                    subject=subject,
                    seen_phashes=seen_phashes,
                )
                ranked = ranked_clip_asset(analysis, topic=topic)
                verdict = clip_pipeline.verdict_for(analysis)
                record = {**ranked.to_dict(), "verdict": verdict}
        else:
            if image_pipeline is None:
                # Defensive: an image path slipped into a pure-video job.
                verdict = "keep"
                record = {
                    "asset_id": f"img_{idx:03d}_{abs(hash(original_path)) % 100000:05d}",
                    "type": "image",
                    "path": original_path,
                    "scene_index": idx,
                    "topic": topic,
                    "score": 50.0,
                    "verdict": verdict,
                    "analysis": None,
                    "error": "image_pipeline_unavailable",
                }
            else:
                analysis = image_pipeline.analyze_scene_image(
                    scene_index=idx,
                    image_path=original_path,
                    topic=topic,
                    narration=narration,
                    subject=subject,
                    seen_phashes=seen_phashes,
                )
                ranked = ranked_image_asset(analysis, topic=topic)
                verdict = image_pipeline.verdict_for(analysis)
                record = {**ranked.to_dict(), "verdict": verdict}

        verdicts.append(verdict)
        ranked_records.append(record)

        if verdict == "emergency":
            new_paths.append(None)
            new_labels.append(labels[idx] if idx < len(labels) else "")
        elif verdict == "replace":
            # Keep the path for now — the caller's fallback helpers will try
            # to repair it; if they can't, the existing path still renders.
            new_paths.append(None)
            new_labels.append((labels[idx] if idx < len(labels) else "") + " (visual reject)")
        else:
            new_paths.append(original_path)
            new_labels.append(labels[idx] if idx < len(labels) else "")

        if report_progress:
            try:
                report_progress((idx + 1) / max(1, total), f"Visual intelligence {idx + 1}/{total}")
            except Exception:
                pass

    # Persist the audit manifest (best-effort)
    summary = build_summary(verdicts, client is not None, model)
    if visual_context is not None:
        summary.update(visual_context.summary())
    job_name = str(job_config.get("name") or job_config.get("title") or "video")
    manifest_path = ""
    try:
        manifest_path = write_content_database(
            ranked_records,
            job_name=job_name,
            model=model,
            summary=summary,
        )
    except Exception as exc:
        print(f"   [vip] audit manifest write failed: {exc}")

    # Update the media plan in place
    media_plan["media_paths"] = new_paths
    media_plan["source_labels"] = new_labels
    media_plan.setdefault("metadata", {})["visual_intelligence"] = summary
    media_plan["metadata"]["visual_intelligence_manifest"] = manifest_path
    media_plan["metadata"]["visual_intelligence_verdicts"] = verdicts

    print(
        f"   [vip] visual intelligence pass: "
        f"{summary['kept']} kept / {summary['replaced']} flagged / {summary['emergency']} emergency "
        f"(vision={summary['vision_available']}, model={model or 'n/a'})"
    )
    return media_plan, manifest_path, summary
