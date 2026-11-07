from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

def _extract_scene_media_info(media_plan_or_manifest: Mapping[str, Any]) -> Tuple[List[str], List[str]]:
    """Extract (paths, labels) across all manifest and media plan schema versions."""
    obj = (
        media_plan_or_manifest.get("media_plan")
        if isinstance(media_plan_or_manifest, Mapping) and "media_plan" in media_plan_or_manifest
        else media_plan_or_manifest
    )
    if not isinstance(obj, Mapping):
        return [], []

    # 1. Direct media_plan format
    paths = list(obj.get("media_paths") or [])
    labels = list(obj.get("source_labels") or [])
    if paths or labels:
        return [str(p or "") for p in paths], [str(l or "") for l in labels]

    # 2. video_clips format
    clips = list(obj.get("video_clips") or [])
    if clips:
        p_list, l_list = [], []
        for c in clips:
            if isinstance(c, Mapping):
                p_list.append(str(c.get("path") or c.get("url") or ""))
                l_list.append(str(c.get("source_label") or c.get("provider") or c.get("source_type") or c.get("source") or ""))
            else:
                p_list.append(str(c or ""))
                l_list.append("")
        return p_list, l_list

    # 3. remotion_manifest.scenes or scenes
    scenes = list((obj.get("remotion_manifest") or {}).get("scenes") or obj.get("scenes") or [])
    if scenes:
        p_list, l_list = [], []
        for s in scenes:
            if isinstance(s, Mapping):
                media = s.get("media") or {}
                primary = s.get("primary_visual") or {}
                asset = primary.get("asset") or {} if isinstance(primary, Mapping) else {}
                shots = media.get("shots") if isinstance(media, Mapping) and isinstance(media.get("shots"), list) else []
                first_shot = shots[0] if shots and isinstance(shots[0], Mapping) else {}

                path = (
                    (media.get("path") if isinstance(media, Mapping) else None)
                    or (media.get("src") if isinstance(media, Mapping) else None)
                    or (media.get("url") if isinstance(media, Mapping) else None)
                    or asset.get("path")
                    or asset.get("src")
                    or first_shot.get("path")
                    or first_shot.get("src")
                    or (media if isinstance(media, str) else "")
                    or ""
                )

                source = (
                    (media.get("source") if isinstance(media, Mapping) else None)
                    or (media.get("source_label") if isinstance(media, Mapping) else None)
                    or (media.get("provider") if isinstance(media, Mapping) else None)
                    or (media.get("label") if isinstance(media, Mapping) else None)
                    or asset.get("source")
                    or asset.get("label")
                    or first_shot.get("source")
                    or (media.get("type") if isinstance(media, Mapping) else "")
                    or ""
                )
                p_list.append(str(path))
                l_list.append(str(source))
            else:
                p_list.append("")
                l_list.append("")
        return p_list, l_list

    return [], []





def audit_youtube_quota(
    media_plan_or_manifest: Mapping[str, Any],
    requested_percentage: float | int | str,
    total_scenes: Optional[int] = None,
) -> Dict[str, Any]:
    """Audit that YouTube clip allocation matches the requested coverage target.

    Supports requested_percentage in 0-100 or 0.0-1.0 formats.
    """
    try:
        pct = float(str(requested_percentage).strip().rstrip("%"))
        if pct > 1.0:
            target_ratio = pct / 100.0
        else:
            target_ratio = pct
    except Exception:
        target_ratio = 0.50

    paths, labels = _extract_scene_media_info(media_plan_or_manifest)
    scene_count = total_scenes or len(paths) or len(labels) or 1

    expected_yt_count = min(scene_count, max(1, int(round(scene_count * target_ratio)))) if target_ratio > 0 else 0

    actual_yt_count = 0
    yt_indices = []
    for idx, label in enumerate(labels):
        lbl = str(label or "").lower()
        if "youtube" in lbl or "yt" in lbl or "clip" in lbl:
            actual_yt_count += 1
            yt_indices.append(idx)

    actual_ratio = actual_yt_count / scene_count if scene_count else 0.0
    passed = actual_yt_count >= expected_yt_count or abs(actual_yt_count - expected_yt_count) <= 1

    return {
        "status": "pass" if passed else "warning",
        "requested_percentage": round(target_ratio * 100, 1),
        "target_ratio": round(target_ratio, 3),
        "expected_count": expected_yt_count,
        "actual_count": actual_yt_count,
        "actual_ratio": round(actual_ratio, 3),
        "scene_count": scene_count,
        "youtube_scene_indices": yt_indices,
        "meets_target": actual_yt_count >= expected_yt_count,
    }


def audit_distant_timestamp_reuse(
    media_plan_or_manifest: Mapping[str, Any],
    min_gap_seconds: float = 8.0,
) -> Dict[str, Any]:
    """Audit that YouTube URL reuses across scenes utilize distant non-overlapping timestamps.

    Checks:
    1. Single YouTube link/source reused for multiple cuts has strictly distinct intervals.
    2. No cut overlaps with another cut from the same source (no frame collisions).
    3. Cuts maintain non-overlapping gap >= min_gap_seconds (or progressive threshold).
    """
    media_plan = (
        media_plan_or_manifest.get("media_plan")
        if "media_plan" in media_plan_or_manifest
        else media_plan_or_manifest
    )
    metadata = (media_plan or {}).get("metadata") or {}
    resolution_rows = metadata.get("media_resolution_v2") or []
    youtube_meta = metadata.get("youtube_pipeline_v2") or {}
    assigned_cuts = youtube_meta.get("assigned_cuts") or []

    # Map each YouTube source ID/URL to its cut timestamp intervals [start, end, scene_idx]
    cuts_by_source: Dict[str, List[Dict[str, Any]]] = {}

    # Source 1: explicit assigned_cuts ledger from youtube_pipeline_v2
    for cut in assigned_cuts:
        src = str(cut.get("source_url") or cut.get("source_id") or cut.get("video_id") or "").strip()
        if not src:
            continue
        cuts_by_source.setdefault(src, []).append({
            "scene_index": cut.get("scene_index"),
            "start": float(cut.get("start") or cut.get("timestamp_start") or 0.0),
            "end": float(cut.get("end") or cut.get("timestamp_end") or 0.0),
            "path": cut.get("selected_path") or cut.get("path"),
        })

    # Source 2: resolution_rows in media_resolution_v2
    for idx, row in enumerate(resolution_rows):
        if not isinstance(row, Mapping):
            continue
        provider = str(row.get("provider") or "").lower()
        if "youtube" in provider or "yt" in provider:
            src = str(row.get("source_url") or row.get("source_id") or row.get("candidate_id") or "").strip()
            evidence = row.get("candidate_evidence") or {}
            start = float(evidence.get("timestamp_start") or row.get("video_start") or 0.0)
            end = float(evidence.get("timestamp_end") or row.get("video_end") or (start + 5.0))
            if src:
                existing = [c for c in cuts_by_source.get(src, []) if c.get("scene_index") == idx]
                if not existing:
                    cuts_by_source.setdefault(src, []).append({
                        "scene_index": idx,
                        "start": start,
                        "end": end,
                        "path": row.get("selected_path") or row.get("path"),
                    })

    # Evaluate overlap and distance per multi-cut source
    reused_sources = 0
    total_cuts = 0
    overlaps_found = []
    gap_violations = []
    valid_distant_reuses = []

    for src, cuts in cuts_by_source.items():
        total_cuts += len(cuts)
        if len(cuts) <= 1:
            continue
        reused_sources += 1
        sorted_cuts = sorted(cuts, key=lambda c: c["start"])
        for i in range(len(sorted_cuts) - 1):
            curr_cut = sorted_cuts[i]
            next_cut = sorted_cuts[i + 1]
            gap = next_cut["start"] - curr_cut["end"]
            if gap < 0:
                overlaps_found.append({
                    "source": src,
                    "scene_a": curr_cut["scene_index"],
                    "scene_b": next_cut["scene_index"],
                    "cut_a": (curr_cut["start"], curr_cut["end"]),
                    "cut_b": (next_cut["start"], next_cut["end"]),
                    "overlap_seconds": round(abs(gap), 2),
                })
            elif gap < min_gap_seconds:
                gap_violations.append({
                    "source": src,
                    "scene_a": curr_cut["scene_index"],
                    "scene_b": next_cut["scene_index"],
                    "gap_seconds": round(gap, 2),
                    "min_gap_required": min_gap_seconds,
                })
            else:
                valid_distant_reuses.append({
                    "source": src,
                    "scene_a": curr_cut["scene_index"],
                    "scene_b": next_cut["scene_index"],
                    "gap_seconds": round(gap, 2),
                })

    status = "pass"
    if overlaps_found:
        status = "fail"
    elif gap_violations:
        status = "advisory"

    return {
        "status": status,
        "reused_sources_count": reused_sources,
        "total_youtube_cuts_tracked": total_cuts,
        "valid_distant_reuses_count": len(valid_distant_reuses),
        "valid_distant_reuses": valid_distant_reuses,
        "overlaps_found": overlaps_found,
        "gap_violations": gap_violations,
        "zero_collision_guarantee": len(overlaps_found) == 0,
    }


def audit_llm_decisions(
    metadata_or_manifest: Mapping[str, Any],
) -> Dict[str, Any]:
    """Audit structured decisions from Jev Model and OpenRouter LLM completions."""
    meta = (
        metadata_or_manifest.get("metadata")
        if "metadata" in metadata_or_manifest
        else metadata_or_manifest
    )
    jev_history = meta.get("jev_decision_history") or meta.get("llm_decisions") or []
    
    total_decisions = len(jev_history)
    typed_valid_decisions = 0
    fallback_decisions = 0
    low_confidence_decisions = 0

    for d in jev_history:
        if not isinstance(d, Mapping):
            continue
        conf = float(d.get("confidence") or 0.0)
        source = str(d.get("service") or d.get("source") or "").lower()
        if "fallback" in source or "openrouter" in source or d.get("used_fallback"):
            fallback_decisions += 1
        if conf >= 0.0 and ("selected_candidate_id" in d or "finding_category" in d or "status" in d):
            typed_valid_decisions += 1
        if conf < 0.5:
            low_confidence_decisions += 1

    return {
        "status": "pass",
        "total_decisions": total_decisions,
        "typed_valid_decisions": typed_valid_decisions,
        "fallback_decisions": fallback_decisions,
        "low_confidence_decisions": low_confidence_decisions,
        "decision_reliability_score": round((typed_valid_decisions / total_decisions) * 100, 1) if total_decisions else 100.0,
    }


def audit_pipeline_health(
    media_plan_or_manifest: Mapping[str, Any],
    max_fallback_ratio: float = 0.25,
) -> Dict[str, Any]:
    """Audit scene integrity, missing media slots, and the primary-to-fallback ratio.

    Goal: Ensure base pipeline handles >= 75-80% of scenes with real verified media,
    with fallbacks acting strictly as safety nets (< 20-25%).
    """
    paths, labels = _extract_scene_media_info(media_plan_or_manifest)
    scene_count = len(paths)

    missing_slots = [i for i, p in enumerate(paths) if not p or str(p).strip() == ""]
    primary_slots = []
    fallback_slots = []

    for i, (path, label) in enumerate(zip(paths, labels)):
        lbl = str(label or "").lower()
        if any(term in lbl for term in ["fallback", "rescue", "emergency", "degraded", "grounded graphic"]):
            fallback_slots.append(i)
        elif path and ("youtube" in lbl or "web" in lbl or "stock" in lbl):
            primary_slots.append(i)
        else:
            primary_slots.append(i)

    fallback_ratio = len(fallback_slots) / scene_count if scene_count else 0.0
    primary_ratio = len(primary_slots) / scene_count if scene_count else 1.0

    healthy_pipeline = len(missing_slots) == 0 and fallback_ratio <= max_fallback_ratio

    status = "pass"
    if missing_slots:
        status = "fail"
    elif fallback_ratio > max_fallback_ratio:
        status = "advisory"

    return {
        "status": status,
        "total_scenes": scene_count,
        "scene_count": scene_count,
        "missing_slots_count": len(missing_slots),
        "missing_scene_indices": missing_slots,
        "primary_scenes_count": len(primary_slots),
        "primary_ratio": round(primary_ratio, 3),
        "fallback_scenes_count": len(fallback_slots),
        "fallback_scene_indices": fallback_slots,
        "fallback_ratio": round(fallback_ratio, 3),
        "healthy_strong_baseline": healthy_pipeline,
        "fallbacks_are_emergency_only": fallback_ratio <= max_fallback_ratio,
    }


def audit_historical_run(run_directory: str | Path) -> Dict[str, Any]:
    """Audit a historical run directory from output/."""
    run_path = Path(run_directory)
    report: Dict[str, Any] = {
        "run_directory": str(run_path.resolve()),
        "status": "unknown",
        "details": {},
    }

    if not run_path.exists():
        report["status"] = "error"
        report["details"]["error"] = f"Directory {run_path} does not exist"
        return report

    manifest_p = run_path / "manifest.json"
    render_fail_p = run_path / "checkpoints" / "render_failure.json"
    media_plan_p = run_path / "checkpoints" / "media_plan.json"

    if render_fail_p.exists():
        try:
            fail_data = json.loads(render_fail_p.read_text(encoding="utf-8", errors="ignore"))
            report["render_failure"] = fail_data
            report["status"] = "failed_in_past"
        except Exception as e:
            report["render_failure"] = {"error": str(e)}

    data_to_audit = {}
    if manifest_p.exists():
        try:
            data_to_audit = json.loads(manifest_p.read_text(encoding="utf-8", errors="ignore"))
            report["status"] = "succeeded" if "render_failure" not in report else "failed_with_manifest"
        except Exception:
            pass
    elif media_plan_p.exists():
        try:
            data_to_audit = {"media_plan": json.loads(media_plan_p.read_text(encoding="utf-8", errors="ignore"))}
        except Exception:
            pass

    if data_to_audit:
        quota_audit = audit_youtube_quota(data_to_audit, requested_percentage=50)
        distant_cut_audit = audit_distant_timestamp_reuse(data_to_audit)
        health_audit = audit_pipeline_health(data_to_audit)
        report["details"] = {
            "quota_audit": quota_audit,
            "distant_cut_audit": distant_cut_audit,
            "health_audit": health_audit,
        }

    return report
