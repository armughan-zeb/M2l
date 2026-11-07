"""Build a real-media shot timeline from resolved scene candidates.

Media discovery resolves *what* is safe and relevant.  This module decides
*how long* each accepted asset stays on screen.  It deliberately never
creates a synthetic visual or promotes an unverified candidate; extracted
frames inherit the identity verification of the accepted YouTube cut and
retain the source/timestamp provenance.
"""
from __future__ import annotations

import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence


_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def _is_video(path: str, media_type: str = "") -> bool:
    return str(media_type or "").strip().lower() == "video" or Path(str(path or "")).suffix.lower() in _VIDEO_EXTENSIONS


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@lru_cache(maxsize=512)
def _local_video_duration(path: str) -> float | None:
    """Read duration only for positive local seeks; cut files have their own clock."""
    try:
        from .media_integrity import probe_media
        result = probe_media(path, min_bytes=256, timeout_seconds=3.0)
        return float(result.duration) if result.valid and result.duration > 0 else None
    except Exception:
        return None


def _local_render_seek(candidate: Mapping[str, Any], path: str, shot_duration: float) -> tuple[float | None, dict[str, Any] | None]:
    """Keep source timestamps out of already-trimmed local clips.

    ``video_start``/``clip_start`` identify a moment in the original YouTube
    source. ``render_clip_start`` is the only local seek. A stale source-time
    value must never seek seven seconds into a 1.7-second downloaded cut.
    """
    raw = candidate.get("render_clip_start")
    if raw in (None, ""):
        return (0.0, None) if Path(path).is_file() else (None, None)
    requested = max(0.0, _float(raw, 0.0))
    if requested <= 0 or not Path(path).is_file():
        return requested, None
    duration = _local_video_duration(path)
    if duration is None:
        return 0.0, {
            "requested_local_seek": requested,
            "local_source_duration": None,
            "render_clip_start": 0.0,
            "reason": "unprobeable_local_clip_defaults_to_its_own_start",
        }
    if requested >= duration or requested + min(max(0.0, shot_duration), duration) > duration + 0.05:
        return 0.0, {
            "requested_local_seek": requested,
            "local_source_duration": round(duration, 3),
            "render_clip_start": 0.0,
            "reason": "source_timestamp_exceeds_local_cut_timebase",
        }
    return requested, None


def _timed_word(row: Any, fallback_index: int) -> dict[str, Any] | None:
    """Normalize V1/V2/Whisper word rows to one small timing shape."""
    if isinstance(row, Mapping):
        get = row.get
    else:
        get = lambda key, default=None: getattr(row, key, default)
    text = str(get("text", "") or "").strip()
    start = _float(get("start_seconds", get("start", None)), -1.0)
    end = _float(get("end_seconds", get("end", None)), -1.0)
    if start < 0 or end <= start:
        return None
    return {
        "index": int(_float(get("index", fallback_index), fallback_index)),
        "text": text,
        "start": start,
        "end": end,
    }


def _narration_aligned_windows(
    duration: float,
    count: int,
    scene_start: float,
    word_timings: Sequence[Any],
) -> list[dict[str, Any]]:
    """Place planned cuts on spoken word boundaries, favoring pauses and punctuation."""
    if duration <= 0 or count <= 0:
        return []
    words = [
        word for index, raw in enumerate(word_timings or [])
        if (word := _timed_word(raw, index)) is not None
        and word["end"] > scene_start + 0.03
        and word["start"] < scene_start + duration - 0.03
    ]
    words.sort(key=lambda item: (item["start"], item["index"]))
    if not words:
        return [
            {
                "start": round(duration * index / count, 3),
                "end": round(duration * (index + 1) / count, 3),
                "text": "",
                "source": "even_visual_beat",
                "cut_in_source": "scene_boundary" if index == 0 else "even_visual_beat",
                "cut_out_source": "scene_boundary" if index == count - 1 else "even_visual_beat",
            }
            for index in range(count)
        ]

    boundaries = []
    for index, word in enumerate(words[:-1]):
        local_end = max(0.0, min(duration, word["end"] - scene_start))
        next_start = max(local_end, words[index + 1]["start"] - scene_start)
        pause = max(0.0, next_start - local_end)
        text = word["text"]
        punctuation = 1.0 if text.endswith((".", "?", "!", ";", ":")) else 0.0
        if local_end > 0.08 and local_end < duration - 0.08:
            boundaries.append({"time": local_end, "pause": pause, "punctuation": punctuation})

    cuts: list[dict[str, Any]] = []
    min_gap = min(0.9, max(0.45, duration / max(4, count * 3)))
    for cut_index in range(1, count):
        target = duration * cut_index / count
        lower = (float(cuts[-1]["time"]) + min_gap) if cuts else min_gap
        upper = duration - min_gap * (count - cut_index)
        candidates = [row for row in boundaries if lower <= row["time"] <= upper and all(abs(row["time"] - float(old["time"])) >= min_gap for old in cuts)]
        if candidates:
            chosen = min(
                candidates,
                key=lambda row: abs(row["time"] - target) - min(0.8, row["pause"] * 0.8) - row["punctuation"] * 0.35,
            )
            cuts.append({"time": float(chosen["time"]), "source": "word_timing_boundary"})
        else:
            cuts.append({"time": max(lower, min(upper, target)), "source": "even_visual_beat"})

    edges = [{"time": 0.0, "source": "scene_boundary"}, *sorted(cuts, key=lambda item: float(item["time"])), {"time": duration, "source": "scene_boundary"}]
    windows = []
    for edge_index, (start_edge, end_edge) in enumerate(zip(edges, edges[1:])):
        start = float(start_edge["time"])
        end = float(end_edge["time"])
        phrase = " ".join(
            word["text"] for word in words
            if word["end"] > scene_start + start and word["start"] < scene_start + end
        ).strip()
        windows.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "text": phrase,
            "source": "word_timing_boundary" if phrase else "even_visual_beat",
            "cut_in_source": str(start_edge["source"]),
            "cut_out_source": str(end_edge["source"]),
        })
    return windows


def _align_plan_to_narration(
    plans: list[list[dict[str, Any]]],
    durations: Sequence[float],
    word_timings: Sequence[Any],
) -> dict[str, Any]:
    """Retain candidate order while snapping shot changes to natural speech beats."""
    cursor = 0.0
    aligned = 0
    total = 0
    word_boundary_cuts = 0
    internal_cuts = 0
    for index, duration in enumerate(durations):
        plan = plans[index] if index < len(plans) else []
        if not plan:
            cursor += max(0.0, duration)
            continue
        total += len(plan)
        windows = _narration_aligned_windows(float(duration), len(plan), cursor, word_timings)
        if len(windows) == len(plan):
            for shot, window in zip(plan, windows):
                shot["shot_start"] = window["start"]
                shot["shot_end"] = window["end"]
                shot["duration"] = round(max(0.001, window["end"] - window["start"]), 3)
                shot["beat_text"] = window["text"]
                shot["beat_start"] = window["start"]
                shot["beat_end"] = window["end"]
                shot["timing_source"] = window["source"]
                shot["cut_in_source"] = window["cut_in_source"]
                shot["cut_out_source"] = window["cut_out_source"]
                if window["text"]:
                    aligned += 1
                if window["cut_out_source"] != "scene_boundary":
                    internal_cuts += 1
                    if window["cut_out_source"] == "word_timing_boundary":
                        word_boundary_cuts += 1
        cursor += max(0.0, duration)
    return {
        "shots_with_narration_timing": aligned,
        "shot_count": total,
        "internal_cut_count": internal_cuts,
        "word_boundary_cut_count": word_boundary_cuts,
    }


def _candidate_identity(row: Mapping[str, Any]) -> str:
    media_path = str(row.get("path") or row.get("asset_path") or "").strip()
    media_type = str(row.get("media_type") or row.get("type") or "")
    if _is_video(media_path, media_type):
        # A source timestamp is provenance, not proof that a trimmed local
        # file contains another usable moment. Distinct source moments must be
        # downloaded/materialized as distinct cuts before the edit can treat
        # them as unique.
        fingerprint = str(row.get("asset_fingerprint") or row.get("fingerprint") or "").strip().casefold()
        if fingerprint:
            return f"video:fingerprint:{fingerprint}"
        if media_path:
            normalized = os.path.normcase(os.path.normpath(media_path))
            return f"video:path:{normalized}"
    for key in ("asset_fingerprint", "fingerprint", "video_id", "youtube_id", "path", "asset_path", "source_url", "origin_url", "url"):
        value = str(row.get(key) or "").strip().casefold()
        if not value:
            continue
        if _is_video(str(row.get("path") or row.get("asset_path") or ""), str(row.get("media_type") or row.get("type") or "")):
            moment = next((row.get(name) for name in (
                "frame_timestamp", "render_clip_start", "video_start", "clip_start", "start",
            ) if row.get(name) not in (None, "")), None)
            if moment is not None:
                return f"{key}:{value}:moment:{moment}"
        return f"{key}:{value}"
    candidate_id = str(row.get("candidate_id") or "").strip().casefold()
    if candidate_id:
        return f"candidate_id:{candidate_id}"
    return ""


def _visual_identity_key(row: Mapping[str, Any]) -> tuple[str, str, str, str, str, str] | None:
    """Return the conservative identity tuple used for intro cross-scene reuse.

    Intro candidates may be borrowed from another narration scene only when
    the structured requirement proves that they are the same entity/domain
    and the same model, year, and era.  Missing proof means no cross-scene
    borrowing; a broad topic word is never enough.
    """
    requirement = row.get("visual_requirement")
    if not isinstance(requirement, Mapping):
        requirement = row.get("requirement")
    if not isinstance(requirement, Mapping):
        return None
    attrs = requirement.get("required_attributes")
    attrs = attrs if isinstance(attrs, Mapping) else {}
    entity = str(
        requirement.get("canonical_entity")
        or requirement.get("active_subject")
        or requirement.get("entity")
        or ""
    ).strip().casefold()
    domain = str(
        requirement.get("domain")
        or requirement.get("subject_type")
        or ""
    ).strip().casefold()
    model = str(attrs.get("model") or attrs.get("model_or_make") or "").strip().casefold()
    year = str(attrs.get("year") or attrs.get("decade") or "").strip().casefold()
    era = str(attrs.get("era") or "").strip().casefold()
    if not entity or not domain:
        return None
    matchup_key = ""
    try:
        from .scene_entities import explicit_named_pairs

        scene_text = str(row.get("scene_text") or row.get("narration") or row.get("text") or "")
        matchup_key = ";".join(sorted(pair["key"] for pair in explicit_named_pairs(scene_text)))
    except Exception:
        pass
    review = row.get('visual_decision')
    if not isinstance(review, Mapping):
        resolution = row.get('resolution')
        review = resolution.get('visual_decision') if isinstance(resolution, Mapping) else None
    # A brand match is not permission to borrow a garage shot for a charger
    # sentence. Current scope-bound jobs may borrow only within the reviewed
    # narration scope; legacy rows retain their historical identity matching.
    if isinstance(review, Mapping) and review.get('verified_asset_sha256'):
        matchup_key += '|scope:' + str(requirement.get('requirement_hash') or review.get('verified_requirement_hash') or '')
    return entity, domain, model, year, era, matchup_key


def _candidate_claim_key(row: Mapping[str, Any]) -> str:
    """Key the actual local asset; timestamps alone cannot make a short cut unique."""
    fingerprint = str(row.get("asset_fingerprint") or row.get("fingerprint") or "").strip().casefold()
    media_path = str(row.get("path") or row.get("asset_path") or "").strip()
    media_type = str(row.get("media_type") or row.get("type") or "")
    if _is_video(media_path, media_type):
        if fingerprint:
            return f"video:fingerprint:{fingerprint}"
        if media_path:
            return f"video:path:{os.path.normcase(os.path.normpath(media_path))}"
    source_identity = fingerprint or str(
        row.get("video_id") or row.get("youtube_id") or row.get("source_url")
        or row.get("origin_url") or row.get("url") or row.get("path") or ""
    ).strip().casefold()
    if source_identity:
        media_type = str(row.get("media_type") or row.get("type") or "").strip().lower()
        if media_type == "video" or _is_video(str(row.get("path") or ""), media_type):
            moment = row.get("frame_timestamp")
            if moment in (None, ""):
                moment = row.get("render_clip_start")
            if moment in (None, ""):
                moment = row.get("video_start", row.get("clip_start", row.get("start")))
            if moment not in (None, ""):
                return f"{source_identity}:moment:{moment}"
        return f"source:{source_identity}"
    return str(row.get("candidate_id") or _candidate_identity(row) or "").strip().casefold()


def _shot_visual_key(shot: Mapping[str, Any]) -> tuple[str, str]:
    """Return stable path/fingerprint keys for adjacent-shot protection.

    A candidate can be materialized into a different temporary path during
    fallback or rendering.  Comparing only paths therefore misses the case
    where two neighboring shots are the same underlying asset.  Fingerprints
    are checked independently and empty values are ignored by the comparison.
    """
    path = os.path.normcase(os.path.normpath(str(
        shot.get("path") or shot.get("asset_path") or ""
    ).strip()))
    fingerprint = str(
        shot.get("asset_fingerprint") or shot.get("fingerprint") or ""
    ).strip().casefold()
    return path, fingerprint


def _same_visual( left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_path, left_fingerprint = _shot_visual_key(left)
    right_path, right_fingerprint = _shot_visual_key(right)
    if left_path and right_path and left_path == right_path:
        return True
    return bool(left_fingerprint and right_fingerprint and left_fingerprint == right_fingerprint)


def _reflow_shot_timing(
    plan: list[dict[str, Any]],
    scene_duration: float | None = None,
    *,
    exact_duration: bool = False,
) -> None:
    """Keep a reordered plan contiguous while preserving its shot durations.

    Shot timing is scene-local.  A presentation-only reorder must therefore
    move the timing slots with the candidates, rather than leaving the old
    ``shot_start``/``shot_end`` values on the newly positioned shots.  The
    latter creates a gap in the semantic manifest even though the shot list
    still contains valid media.
    """
    if not plan:
        return
    scene_end = 0.0
    durations: list[float] = []
    for shot in plan:
        try:
            start = float(shot.get("shot_start", 0.0))
        except (TypeError, ValueError):
            start = 0.0
        try:
            end = float(shot.get("shot_end", start))
        except (TypeError, ValueError):
            end = start
        try:
            declared = float(shot.get("duration", end - start))
        except (TypeError, ValueError):
            declared = end - start
        duration = max(0.001, declared if declared > 0 else end - start)
        durations.append(duration)
        scene_end = max(scene_end, end)
    if scene_duration is not None:
        try:
            requested_end = max(0.0, float(scene_duration))
            scene_end = requested_end if exact_duration else max(scene_end, requested_end)
        except (TypeError, ValueError):
            pass
    if scene_end <= 0:
        scene_end = sum(durations)

    # Intro rebalancing reflows an already-offset tail into a shorter local
    # window. In that case the old absolute shot_end values are larger than
    # the new tail duration; preserve the relative cut lengths but scale them
    # down to the requested window instead of treating the old end as a lower
    # bound. Otherwise the tail grows beyond the narration scene's duration.
    total_duration = sum(durations)
    if exact_duration and scene_end > 0 and total_duration > scene_end:
        scale = scene_end / total_duration
        durations = [duration * scale for duration in durations]

    cursor = 0.0
    for index, (shot, shot_duration) in enumerate(zip(plan, durations)):
        end = scene_end if index == len(plan) - 1 else min(scene_end, cursor + shot_duration)
        if end <= cursor:
            end = scene_end if index == len(plan) - 1 else cursor + 0.001
        shot["shot_start"] = round(cursor, 3)
        shot["shot_end"] = round(end, 3)
        shot["duration"] = round(max(0.001, end - cursor), 3)
        cursor = end


def _repair_adjacent_shot_reuse(plans: list[list[dict[str, Any]]]) -> int:
    """Reorder available in-scene shots to avoid back-to-back same visuals.

    Cross-scene borrowing is deliberately allowed for relevance, but it must
    not make a scene boundary show the exact same image or frame twice.  The
    planner already has the accepted alternatives at this point, so a local
    reorder preserves provenance and verification while removing avoidable
    repetition.  Timing slots are reflowed after the reorder so the semantic
    renderer receives a contiguous scene timeline.  If no alternative exists,
    the repeated shot is retained: continuity is preferable to manufacturing
    a new media claim.
    """
    repaired = 0
    previous: Mapping[str, Any] | None = None
    original_scene_ends = [
        max((_float(shot.get("shot_end"), 0.0) for shot in plan), default=0.0)
        for plan in plans
    ]
    for plan_index, plan in enumerate(plans):
        if not plan:
            continue
        for shot_index in range(len(plan)):
            current = plan[shot_index]
            if previous is None or not _same_visual(previous, current):
                previous = current
                continue

            replacement_index: int | None = None
            for candidate_index in range(shot_index + 1, len(plan)):
                trial = list(plan)
                trial[shot_index], trial[candidate_index] = trial[candidate_index], trial[shot_index]
                # Check the changed neighborhood only.  The remainder of the
                # plan was already valid before this swap.
                start = max(1, shot_index)
                end = min(len(trial), candidate_index + 2)
                if any(_same_visual(trial[pos - 1], trial[pos]) for pos in range(start, end)):
                    continue
                replacement_index = candidate_index
                break

            if replacement_index is not None:
                plan[shot_index], plan[replacement_index] = plan[replacement_index], plan[shot_index]
                repaired += 1
                current = plan[shot_index]
            previous = current
    # Scene boundaries can still collide when a sparse intro borrowed a
    # cross-scene candidate and the following scene selected that same stock
    # asset.  Prefer an alternate shot; when none exists, drop the borrowed
    # duplicate and let the remaining accepted shot cover the scene duration.
    # This removes the visible back-to-back repeat without inventing media.
    for boundary in range(1, len(plans)):
        prior_plan = plans[boundary - 1]
        current_plan = plans[boundary]
        if not prior_plan or not current_plan or not _same_visual(prior_plan[-1], current_plan[0]):
            continue
        replacement_index: int | None = None
        for candidate_index in range(1, len(current_plan)):
            if not _same_visual(prior_plan[-1], current_plan[candidate_index]):
                replacement_index = candidate_index
                break
        if replacement_index is not None:
            current_plan[0], current_plan[replacement_index] = current_plan[replacement_index], current_plan[0]
            repaired += 1
            _reflow_shot_timing(current_plan, original_scene_ends[boundary])
            continue
        # A borrowed cross-scene shot is the least valuable row when it is
        # the collision. Keep the scene's own accepted shot(s) and reflow.
        if len(current_plan) > 1 and str(current_plan[0].get("reuse_kind") or "").strip().lower() in {
            "cross_scene_candidate", "intro_cross_scene_candidate", "intro_motion_segment", "still_motion_segment",
        }:
            del current_plan[0]
            repaired += 1
            _reflow_shot_timing(current_plan, original_scene_ends[boundary])
        elif len(prior_plan) > 1 and str(prior_plan[-1].get("reuse_kind") or "").strip().lower() in {
            "cross_scene_candidate", "intro_cross_scene_candidate", "intro_motion_segment", "still_motion_segment",
        }:
            prior_scene_end = _float(prior_plan[-1].get("shot_end"), 0.0)
            prior_plan.pop()
            repaired += 1
            _reflow_shot_timing(prior_plan, original_scene_ends[boundary - 1])
            # Preserve the original scene duration after dropping only the
            # colliding borrowed shot. Without this extension the planner's
            # last remaining shot ends at the removed shot's start, violating
            # the semantic manifest's exact scene coverage contract.
            if prior_plan and prior_scene_end > 0.0:
                last = prior_plan[-1]
                start = _float(last.get("shot_start"), 0.0)
                last["shot_end"] = round(max(start + 0.001, prior_scene_end), 3)
                last["duration"] = round(max(0.001, prior_scene_end - start), 3)
    # A swap changes the order of candidates but not the scene duration.  Move
    # the local timing slots with the new order before the plan is serialized.
    for plan_index, plan in enumerate(plans):
        _reflow_shot_timing(plan, original_scene_ends[plan_index])
    return repaired


def _candidate_rows(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    resolution = item.get("resolution") if isinstance(item.get("resolution"), Mapping) else item
    primary_path = str(
        item.get("path")
        or item.get("src")
        or item.get("selected_path")
        or item.get("cleaned_path")
        or (resolution or {}).get("selected_path")
        or (resolution or {}).get("cleaned_path")
        or ""
    ).strip()
    rows: list[dict[str, Any]] = []
    if primary_path and os.path.isfile(primary_path):
        primary_status = str(
            item.get("verification_status")
            or (resolution or {}).get("verification_status")
            or ""
        ).strip().casefold()
        if primary_status in {"rejected", "unresolved", "schema_invalid", "not_applicable"}:
            primary_path = ""
    if primary_path and os.path.isfile(primary_path):
        media_type = str(
            item.get("media_type")
            or item.get("type")
            or (resolution or {}).get("media_type")
            or (resolution or {}).get("type")
            or ("video" if _is_video(primary_path, "") else "image")
        ).strip()
        rows.append({
            **dict(resolution or {}),
            **dict(item),
            "path": primary_path,
            "media_type": media_type,
            "type": media_type,
            "candidate_id": str(
                item.get("candidate_id")
                or (resolution or {}).get("candidate_id")
                or f"scene:{item.get('scene_index', 0)}"
            ),
            "verified": True,
            "fallback_layer": str(item.get("fallback_layer") or (resolution or {}).get("fallback_reason") or "primary"),
        })
    alternates = []
    if isinstance(resolution, Mapping):
        alternates.extend(resolution.get("alternates") or ())
        alternates.extend(resolution.get("verified_alternates") or ())
    alternates.extend(item.get("alternates") or ())
    seen = {_candidate_identity(row) for row in rows}
    seen_image_paths = {
        os.path.normcase(os.path.normpath(str(row.get("path") or "")))
        for row in rows
        if str(row.get("media_type") or row.get("type") or "").strip().casefold() != "video"
    }
    for alternate in alternates:
        if not isinstance(alternate, Mapping):
            continue
        from .visual_intelligence.semantic_guard import explicit_scene_conflict
        if any(explicit_scene_conflict(alternate.get(key)) for key in ("visual_decision", "qwen_result")) or explicit_scene_conflict(alternate):
            continue
        alternate_status = str(alternate.get("verification_status") or "").strip().casefold()
        explicit_verified = (
            alternate.get("verified") is True
            or alternate_status in {
                "accepted",
                "verified",
                "verified_reuse",
                "deterministic_pool_cut",
                "deterministic_degraded",
            }
            or str(alternate.get("verification") or "").strip().casefold()
            in {"inherited_from_verified_youtube_cut", "verified"}
        )
        # Alternates are rendered as real shots, so missing verification is
        # not equivalent to a pass.  The resolver marks accepted finalists
        # explicitly; unverified provider rows stay in the audit ledger but
        # cannot leak into the presentation timeline.
        if not explicit_verified or alternate.get("verified") is False or alternate_status in {
            "rejected", "unresolved", "schema_invalid", "not_applicable",
        }:
            continue
        if (
            alternate.get("facecam_inset")
            or alternate.get("talking_head")
            or float(alternate.get("facecam_confidence") or 0.0) >= 0.50
            or "facecam" in str(alternate.get("rejection_reason") or "").lower()
            or "talking_head" in str(alternate.get("rejection_reason") or "").lower()
        ):
            continue
        path = str(alternate.get("path") or alternate.get("asset_path") or "").strip()
        if not path or not os.path.isfile(path):
            continue
        parent_decision = resolution.get('visual_decision') if isinstance(resolution, Mapping) else None
        requirement = item.get('visual_requirement') or item.get('subject_requirement') or resolution.get('visual_requirement')
        if (isinstance(parent_decision, Mapping) and parent_decision.get('verified_asset_sha256')
                and isinstance(requirement, Mapping) and requirement.get('strict_identity')):
            from .pipeline_v2_media_adapter import _has_bound_pixel_review
            # The parent's pixel approval cannot approve a different image.
            # Current factual jobs require the alternate's own bytes and scene
            # scope to be reviewed before it becomes a timed shot.
            if not _has_bound_pixel_review(requirement, alternate, path):
                continue
        alternate_type = str(alternate.get("media_type") or alternate.get("type") or "image").strip().casefold()
        normalized_path = os.path.normcase(os.path.normpath(path))
        if alternate_type != "video" and normalized_path in seen_image_paths:
            continue
        row = {
            **dict(item),
            **dict(resolution or {}),
            **dict(alternate),
            "path": path,
            # The source item's media_type can be "video". An explicitly
            # accepted image alternate must override that inherited type or
            # it gets routed through the footage path with a still-image path.
            "media_type": alternate_type,
            "type": alternate_type,
        }
        # Identity belongs to the alternate asset, never its parent shot.
        # An inherited fingerprint/video ID silently discarded new images as
        # duplicates before their actual pixels could be reviewed.
        for key in ("asset_fingerprint", "fingerprint", "video_id", "youtube_id", "visual_decision"):
            if key not in alternate:
                row.pop(key, None)
        identity = _candidate_identity(row)
        if not identity or identity in seen:
            continue
        seen.add(identity)
        if alternate_type != "video":
            seen_image_paths.add(normalized_path)
        row["candidate_id"] = str(row.get("candidate_id") or f"{item.get('candidate_id', 'scene')}:alternate:{len(rows)}")
        row["verified"] = bool(row.get("verified", True))
        row["fallback_layer"] = str(row.get("fallback_layer") or "verified_alternate")
        rows.append(row)
    return rows


def _extract_video_frames(row: Mapping[str, Any], scene_index: int, wanted: int) -> list[dict[str, Any]]:
    """Create same-video still candidates when a scene needs visual beats."""
    if wanted <= 0 or not _is_video(str(row.get("path") or ""), str(row.get("media_type") or row.get("type") or "")):
        return []
    try:
        from .visual_intelligence.frame_extractor import extract_clip_frames
        source_path = str(row.get("path") or "")
        frame_dir = Path(source_path).parent / "shot_plan_frames" / f"scene_{scene_index:04d}"
        frames, duration = extract_clip_frames(
            source_path,
            out_dir=str(frame_dir),
            long_edge=1920,
            render_quality=True,
            max_frames=max(2, min(8, int(wanted))),
        )
    except Exception:
        return []
    output: list[dict[str, Any]] = []
    rejected_count = 0
    total = max(1, len(frames))
    context_frames = list(frames)
    for frame_index, frame_path in enumerate(frames):
        if not frame_path or not os.path.isfile(frame_path):
            continue
        # Keep inherited source frames sharp. Small archival labels are useful
        # context; obstruction screening below never rewrites source pixels.
        frame_cleanup: dict[str, Any] = {"path": str(frame_path), "no_blur": True, "cleaned": False}
        try:
            from services.scene_understanding.analyzers import screen_extracted_frame
            frame_screen = screen_extracted_frame(str(frame_path)) or {}
        except Exception:
            frame_screen = {}
        # Extracted frames inherit identity verification from their parent
        # clip, but never inherit a clean-overlay decision. A later timestamp
        # may contain a presenter PiP or a creator title card that was absent
        # from the parent clip's initial samples.
        if frame_screen and frame_screen.get("accepted") is False:
            rejected_count += 1
            continue
        frame = dict(row)
        frame.update({
            "path": str(frame_path),
            "asset_path": str(frame_path),
            "media_type": "image",
            "type": "image",
            "candidate_id": f"{row.get('candidate_id') or 'youtube'}:frame:{frame_index + 1}",
            "asset_fingerprint": f"{row.get('asset_fingerprint') or row.get('fingerprint') or source_path}:frame:{frame_index + 1}",
            "fallback_layer": "same_video_timestamp_frame",
            "source_media_path": str(row.get("path") or ""),
            "frame_index": frame_index,
            "frame_count": total,
            "frame_timestamp": round(duration * ((frame_index + 1) / (total + 1)), 3) if duration else None,
            "verification": "inherited_from_verified_youtube_cut",
            "verified": True,
            "overlay_blurred": bool(frame_cleanup.get("blurred")),
            "overlay_regions": list(frame_cleanup.get("regions") or []),
            "overlay_cleanup": str(frame_cleanup.get("detector") or ""),
        })
        output.append(frame)
    # Expose the result on the in-memory parent row. This is intentionally
    # internal planner state: it lets the planner remove a contaminated full
    # MP4 while preserving any clean timestamp frames from that same source.
    try:
        row["_frame_hard_contamination_count"] = rejected_count  # type: ignore[index]
    except (TypeError, AttributeError):
        pass
    return output


def _scene_candidates(item: Mapping[str, Any], scene_index: int, needed: int) -> list[dict[str, Any]]:
    rows = _candidate_rows(item)
    if not rows:
        return []
    result = list(rows)
    primary = rows[0]
    is_primary_video = _is_video(str(primary.get("path") or ""), str(primary.get("media_type") or primary.get("type") or ""))
    if is_primary_video:
        # Video scenes must preserve actual moving video footage.
        # Do not extract still frames or chop moving video into static JPGs.
        video_rows = [
            row for row in result
            if _is_video(str(row.get("path") or ""), str(row.get("media_type") or row.get("type") or ""))
        ]
        if video_rows:
            result = video_rows
        # If the video file is missing or unreadable on disk, fall back to extracted frames or alternates
        else:
            extracted = _extract_video_frames(primary, scene_index, max(3, needed - len(result) + 1))
            if extracted:
                result = extracted
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in result:
        identity = _candidate_identity(row)
        if not identity or identity in seen:
            continue
        seen.add(identity)
        deduped.append(row)
    return deduped


def _director_media_choice(item: Mapping[str, Any]) -> tuple[int | None, str]:
    choice = item.get("director_media_plan")
    if not isinstance(choice, Mapping):
        return None, "either"
    try:
        count = max(1, min(4, int(choice.get("visual_beats"))))
    except (TypeError, ValueError):
        count = None
    preference = str(choice.get("media_preference") or "either").strip().lower()
    if preference not in {"clip", "images", "either"}:
        preference = "either"
    return count, preference


def _preferred_candidates(
    candidates: Sequence[dict[str, Any]],
    preference: str,
) -> list[dict[str, Any]]:
    if preference == "either":
        return list(candidates)
    wanted_type = "video" if preference == "clip" else "image"
    preferred = [
        row for row in candidates
        if ("video" if _is_video(str(row.get("path") or ""), str(row.get("media_type") or row.get("type") or "")) else "image") == wanted_type
    ]
    # The director's choice guides selection when that source exists. It can
    # never force a missing or unverified format into the scene.
    return preferred or list(candidates)


def _make_shots(
    candidates: Sequence[Mapping[str, Any]],
    duration: float,
    *,
    intro: bool = False,
    max_shots: int | None = None,
) -> list[dict[str, Any]]:
    if duration <= 0 or not candidates:
        return []
    selected = list(candidates[: max_shots or len(candidates)])
    if not selected:
        return []
    each = duration / len(selected)
    shots: list[dict[str, Any]] = []
    cursor = 0.0
    for index, candidate in enumerate(selected):
        end = duration if index == len(selected) - 1 else round(cursor + each, 3)
        path = str(candidate.get("path") or candidate.get("asset_path") or "")
        media_type = "video" if _is_video(path, str(candidate.get("media_type") or candidate.get("type") or "")) else "image"
        render_clip_start, timebase_repair = (
            _local_render_seek(candidate, path, max(0.001, end - cursor))
            if media_type == "video" else (None, None)
        )
        try:
            from .media_provenance import classify_rights

            rights = classify_rights(candidate)
        except Exception:
            rights = {"rights_status": "unknown", "rights_risk": "high", "rights_review_required": True}
        shot = {
            "path": path,
            "asset_path": path,
            "media_type": media_type,
            "type": media_type,
            "shot_start": round(cursor, 3),
            "shot_end": round(end, 3),
            "duration": round(max(0.001, end - cursor), 3),
            "clip_start": candidate.get("video_start", candidate.get("start")),
            "clip_end": candidate.get("video_end", candidate.get("end")),
            # Always make the local timebase explicit for local video files;
            # source video_start/clip_start are provenance only.
            "render_clip_start": render_clip_start,
            "source": str(candidate.get("provider") or candidate.get("source") or ""),
            "source_url": str(candidate.get("source_url") or candidate.get("origin_url") or candidate.get("url") or ""),
            "candidate_id": str(candidate.get("candidate_id") or ""),
            "fallback_layer": str(candidate.get("fallback_layer") or "primary"),
            "verification": candidate.get("verification") or candidate.get("verification_status") or "verified",
            "asset_fingerprint": str(candidate.get("asset_fingerprint") or candidate.get("fingerprint") or ""),
            "visual_decision": dict(candidate.get('visual_decision') or candidate.get('qwen_result') or {}),
            "frame_timestamp": candidate.get("frame_timestamp"),
            "reuse_kind": str(candidate.get("reuse_kind") or "unique_candidate"),
            "source_scene_index": candidate.get("scene_index"),
            "rights_status": str(candidate.get("rights_status") or rights.get("rights_status") or "unknown"),
            "rights_risk": str(candidate.get("rights_risk") or rights.get("rights_risk") or "high"),
            "rights_review_required": bool(candidate.get("rights_review_required", rights.get("rights_review_required", True))),
            "rights_evidence": str(candidate.get("rights_evidence") or rights.get("rights_evidence") or "no_license_evidence"),
            "license": str(candidate.get("license") or candidate.get("license_name") or ""),
            "license_url": str(candidate.get("license_url") or ""),
            "attribution": str(candidate.get("attribution") or ""),
            "creator": str(candidate.get("creator") or ""),
            "motion_phase": index,
            "intro_shot": bool(intro),
            "visual_reference": str(next((candidate.get(key) for key in (
                "title", "headline", "caption", "alt", "description", "visual_summary", "query",
            ) if str(candidate.get(key) or "").strip()), "") or "").strip()[:180],
        }
        if timebase_repair:
            shot["timebase_repair"] = timebase_repair
        decision = shot['visual_decision']
        inherited = shot['verification'] == 'inherited_from_verified_youtube_cut'
        shot['verification_scope'] = 'inherited_parent' if inherited else 'actual_asset'
        if inherited:
            shot['verified_parent_sha256'] = decision.get('verified_asset_sha256')
        elif decision.get('verified_asset_sha256'):
            shot['verified_asset_sha256'] = decision['verified_asset_sha256']
        shots.append(shot)
        cursor = end
    return shots


_CROSS_SCENE_REUSE_KINDS = {
    "cross_scene_candidate",
    "intro_cross_scene_candidate",
    "intro_same_identity_cross_scene",
}


def _is_cross_scene_reuse(shot: Mapping[str, Any]) -> bool:
    kind = str(shot.get("reuse_kind") or "").strip().lower()
    layer = str(shot.get("fallback_layer") or "").strip().lower()
    return kind in _CROSS_SCENE_REUSE_KINDS or layer in {
        "same_identity_cross_scene",
        "intro_same_identity_cross_scene",
    }


def _repair_recent_source_reuse(
    plans: list[list[dict[str, Any]]],
    candidate_rows: Mapping[int, Sequence[dict[str, Any]]],
    durations: Sequence[float],
    *,
    cooldown_scenes: int = 2,
) -> dict[str, Any]:
    """Replace avoidable cross-scene repeats within a short editorial window.

    Resolver pools can be sparse during the intro. The initial-montage planner
    may borrow a verified same-identity image before a later scene's native
    choice is assembled. This pass reserves native scene choices, replaces a
    repeat with an unused local alternate where possible, and removes an
    earlier borrowed beat when the later scene owns that same source. If no
    distinct image exists, it keeps the verified native shot and records the
    unavoidable repeat instead of inventing media.
    """
    cooldown = max(0, int(cooldown_scenes))
    prior_uses: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    removals: dict[int, set[int]] = {}
    replaced = 0
    removed = 0
    unresolved: list[dict[str, Any]] = []

    def active_recent(key: str, scene_index: int) -> list[tuple[int, dict[str, Any]]]:
        return [
            entry for entry in prior_uses.get(key, [])
            if scene_index - entry[0] <= cooldown
            and id(entry[1]) not in removals.get(entry[0], set())
        ]

    for scene_index, plan in enumerate(plans):
        local_claims: set[str] = set()
        rows = list(candidate_rows.get(scene_index) or [])
        updated: list[dict[str, Any]] = []
        for shot in plan:
            claim = _candidate_claim_key(shot)
            recent = active_recent(claim, scene_index) if claim else []
            duplicate = bool(claim and (claim in local_claims or recent))
            current = shot

            if duplicate:
                recent_native = [entry for entry in recent if not _is_cross_scene_reuse(entry[1])]
                recent_borrowed = [entry for entry in recent if _is_cross_scene_reuse(entry[1])]
                if _is_cross_scene_reuse(current) and (updated or len(plan) > 1):
                    removed += 1
                    continue
                if recent_borrowed:
                    # The later scene's native source wins over a previously
                    # borrowed intro insert. Keep that scene's own visual.
                    old_scene, old_shot = recent_borrowed[-1]
                    if len(plans[old_scene]) > 1:
                        removals.setdefault(old_scene, set()).add(id(old_shot))
                        removed += 1
                        recent = [entry for entry in recent if entry != (old_scene, old_shot)]
                    else:
                        recent_native = recent

                if recent_native or claim in local_claims:
                    recent_claims = {
                        key for key, entries in prior_uses.items()
                        if any(scene_index - old_index <= cooldown and id(old_shot) not in removals.get(old_index, set())
                               for old_index, old_shot in entries)
                    }
                    alternatives = [
                        candidate for candidate in rows
                        if _candidate_claim_key(candidate)
                        and _candidate_claim_key(candidate) not in recent_claims
                        and _candidate_claim_key(candidate) not in local_claims
                    ]
                    if alternatives:
                        candidate = alternatives[0]
                        start = _float(current.get("shot_start"), 0.0)
                        end = _float(current.get("shot_end"), start + _float(current.get("duration"), 0.001))
                        replacement = _make_shots(
                            [candidate], max(0.001, end - start),
                            intro=bool(current.get("intro_shot")), max_shots=1,
                        )[0]
                        replacement["shot_start"] = round(start, 3)
                        replacement["shot_end"] = round(end, 3)
                        replacement["duration"] = round(max(0.001, end - start), 3)
                        replacement["motion_phase"] = current.get("motion_phase", 0)
                        replacement["intro_shot"] = bool(current.get("intro_shot"))
                        current = replacement
                        claim = _candidate_claim_key(current)
                        replaced += 1
                    else:
                        unresolved.append({"scene_index": scene_index, "candidate_id": current.get("candidate_id") or "", "reason": "no_distinct_verified_local_alternate"})

            if claim:
                local_claims.add(claim)
            updated.append(current)
            if claim:
                prior_uses.setdefault(claim, []).append((scene_index, current))

        plans[scene_index][:] = updated

    for scene_index, removed_ids in removals.items():
        plan = plans[scene_index]
        plans[scene_index][:] = [shot for shot in plan if id(shot) not in removed_ids]
        if plans[scene_index]:
            _reflow_shot_timing(plans[scene_index], durations[scene_index] if scene_index < len(durations) else None, exact_duration=True)

    return {
        "cooldown_scenes": cooldown,
        "replaced_borrowed_shots": replaced,
        "removed_borrowed_shots": removed,
        "unavoidable_repeat_count": len(unresolved),
        "unavoidable_repeats": unresolved[:20],
    }


def _repair_perceptual_reuse(
    plans: list[list[dict[str, Any]]],
    candidate_rows: Mapping[int, Sequence[dict[str, Any]]],
    *,
    budget_seconds: float = 20.0,
    cooldown_scenes: int | None = None,
) -> dict[str, Any]:
    """Replace repeated pixels across different assets with verified local media.

    Source IDs and distant source timestamps cannot prove visual variety.
    Preserve all beat windows and evidence when no distinct accepted alternate
    exists; attach a visible audit instead of inventing a replacement.
    """
    from .perceptual_media import PixelSampler, samples_match

    sampler = PixelSampler(budget_seconds)
    recent: list[tuple[int, dict[str, Any], list[dict[str, Any]]]] = []
    unresolved: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []
    detected = replaced = checked = 0
    # A production film should not recycle the same pixels after an arbitrary
    # three-scene grace period. The caller defaults this to the full film.
    cooldown = max(0, len(plans) - 1 if cooldown_scenes is None else int(cooldown_scenes))

    def duplicate(current: Mapping[str, Any], samples: list[dict[str, Any]], entries: list[tuple[int, dict[str, Any], list[dict[str, Any]]]]) -> tuple[int, dict[str, Any]] | None:
        for old_scene, old_shot, old_samples in reversed(entries):
            if samples_match(samples, old_samples, both_video=_is_video(str(current.get("path") or ""), str(current.get("media_type") or "")) and _is_video(str(old_shot.get("path") or ""), str(old_shot.get("media_type") or ""))):
                return old_scene, old_shot
        return None

    removed_same_scene_duplicates = 0
    for scene_index, plan in enumerate(plans):
        recent = [entry for entry in recent if scene_index - entry[0] <= cooldown]
        kept_plan: list[dict[str, Any]] = []
        for shot_index, shot in enumerate(list(plan)):
            samples, sample_status = sampler.sample(shot)
            if not samples:
                diagnostic = {"scene_index": scene_index, "shot_index": shot_index, "candidate_id": shot.get("candidate_id") or "", "reason": sample_status}
                unavailable.append(diagnostic)
                shot["perceptual_review"] = {"status": "unavailable", "reason": sample_status}
                # An unavailable local decode is an incomplete audit, not a
                # reason to silently remove otherwise selected media.
                kept_plan.append(shot)
                continue
            checked += 1
            match = duplicate(shot, samples, recent)
            if match:
                detected += 1
                old_scene, old_shot = match
                selected_keys = {_candidate_identity(other) for other_plan in plans for other in other_plan if other is not shot}
                replacement = None
                for candidate in candidate_rows.get(scene_index) or []:
                    if candidate.get("verified") is not True or str(candidate.get("verification_status") or "").lower() in {"rejected", "unresolved", "schema_invalid"}:
                        continue
                    if _candidate_identity(candidate) in selected_keys or _candidate_identity(candidate) == _candidate_identity(shot):
                        continue
                    trial = _make_shots([candidate], _float(shot.get("duration"), 0.001), intro=bool(shot.get("intro_shot")), max_shots=1)[0]
                    trial_samples, _trial_status = sampler.sample(trial)
                    if not trial_samples or duplicate(trial, trial_samples, recent):
                        continue
                    replacement = trial
                    for field in ("shot_start", "shot_end", "duration", "motion_phase", "intro_shot"):
                        if field in shot:
                            replacement[field] = shot[field]
                    replacement["perceptual_replacement_for"] = shot.get("candidate_id") or shot.get("path")
                    plan[shot_index] = replacement
                    shot = replacement
                    samples = trial_samples
                    replaced += 1
                    break
                if replacement is None:
                    if old_scene == scene_index:
                        # The same still should not cut to itself under a new
                        # filename. Preserve the beat as one held image with
                        # its continuous camera move rather than fake variety.
                        old_duration = _float(old_shot.get("duration"), 0.0)
                        added_duration = _float(shot.get("duration"), 0.0)
                        old_shot["duration"] = round(old_duration + added_duration, 3)
                        old_shot["shot_end"] = shot.get("shot_end", old_shot.get("shot_end"))
                        old_shot["perceptual_hold_extended"] = True
                        removed_same_scene_duplicates += 1
                        continue
                    diagnostic = {"scene_index": scene_index, "shot_index": shot_index, "candidate_id": shot.get("candidate_id") or "", "matches_scene_index": old_scene, "matches_candidate_id": old_shot.get("candidate_id") or "", "reason": "no_distinct_verified_local_alternate"}
                    unresolved.append(diagnostic)
                    shot["perceptual_review"] = {"status": "unresolved_repeat", **diagnostic}
            if not match or replacement is not None:
                shot["perceptual_review"] = {"status": "distinct" if not match else "replaced", "sample_count": len(samples), "pixel_signature": sampler.signature(samples)}
            recent.append((scene_index, shot, samples))
            kept_plan.append(shot)
        plan[:] = kept_plan

    return {
        "method": "actual_frame_edges_color_and_luminance.v1",
        "status": "needs_revision" if unresolved else "incomplete" if unavailable else "pass",
        "checked_shot_count": checked,
        "detected_repeat_count": detected,
        "replaced_shot_count": replaced,
        "removed_same_scene_duplicate_shot_count": removed_same_scene_duplicates,
        "unresolved_repeat_count": len(unresolved),
        "unresolved_repeats": unresolved,
        "unavailable_shot_count": len(unavailable),
        "unavailable_shots": unavailable,
        "video_decode_count": sampler.decode_count,
        "cooldown_scenes": cooldown,
    }


def _set_continuous_still_motion(
    plans: list[list[dict[str, Any]]],
    *,
    max_still_hold: float,
) -> int:
    """Keep one still as one shot and give a long hold one continuous move.

    Splitting a single photograph into several nominal shots made a cut look
    like new media when only the crop changed. Preserve the verified source as
    one continuous Remotion shot instead; the scene-local camera animation
    supplies motion without creating artificial repeats.
    """
    camera_moves = ("slow_push", "pan_left", "pull_out", "pan_right")
    animated = 0
    for plan_index, plan in enumerate(plans):
        for shot_index, shot in enumerate(plan):
            media_type = str(shot.get("media_type") or shot.get("type") or "").lower()
            start = _float(shot.get("shot_start"), 0.0)
            end = _float(shot.get("shot_end"), start + _float(shot.get("duration"), 0.0))
            duration = max(0.0, end - start)
            if media_type == "video" or duration <= max_still_hold + 0.001:
                continue
            existing_camera = shot.get("camera") if isinstance(shot.get("camera"), Mapping) else {}
            move = str(
                existing_camera.get("motion")
                or shot.get("motion")
                or camera_moves[(plan_index + shot_index) % len(camera_moves)]
            )
            shot["motion"] = move
            shot["camera"] = {**dict(existing_camera), "motion": move}
            shot["continuous_motion"] = True
            animated += 1
    return animated


def _rebalance_intro_stills(
    plans: list[list[dict[str, Any]]],
    durations: Sequence[float],
    offsets: Sequence[float],
    *,
    intro_duration: float,
    enabled: bool,
) -> int:
    """Animate distinct opening stills without manufacturing duplicate cuts."""
    if not enabled:
        return 0
    camera_moves = ("slow_push", "pan_left", "pull_out", "pan_right")
    assigned_motion_count = 0
    for index, plan in enumerate(plans):
        offset = float(offsets[index]) if index < len(offsets) else 0.0
        duration = float(durations[index]) if index < len(durations) else 0.0
        window = max(0.0, min(duration, intro_duration - offset))
        intro_rows = [dict(shot) for shot in plan if bool(shot.get("intro_shot"))]
        if window > 0 and not intro_rows and plan and all(
            str(shot.get("media_type") or shot.get("type") or "").lower() != "video"
            for shot in plan
        ):
            for shot in plan:
                shot["intro_shot"] = True
            intro_rows = [dict(shot) for shot in plan]
        if window <= 0 or not intro_rows or any(
            str(shot.get("media_type") or shot.get("type") or "").lower() == "video"
            for shot in intro_rows
        ):
            continue
        unique_count = len(intro_rows)
        beat_duration = window / unique_count
        for shot_index, shot in enumerate(intro_rows):
            start = beat_duration * shot_index
            end = window if shot_index == unique_count - 1 else beat_duration * (shot_index + 1)
            move = camera_moves[shot_index % len(camera_moves)]
            shot["intro_shot"] = True
            shot["motion_phase"] = shot_index
            shot["motion"] = move
            shot["camera"] = {"motion": move}
            shot["shot_start"] = round(start, 3)
            shot["shot_end"] = round(end, 3)
            shot["duration"] = round(max(0.001, end - start), 3)
            assigned_motion_count += 1

        tail_duration = max(0.0, duration - window)
        tail_rows = [dict(shot) for shot in plan if not bool(shot.get("intro_shot"))]
        if tail_duration > 0.001 and tail_rows:
            _reflow_shot_timing(tail_rows, tail_duration, exact_duration=True)
            for shot in tail_rows:
                shot["shot_start"] = round(float(shot["shot_start"]) + window, 3)
                shot["shot_end"] = round(float(shot["shot_end"]) + window, 3)
        elif tail_duration > 0.001 and intro_rows:
            # No distinct tail asset was verified, so continue the last still's
            # single camera move instead of cutting to another copy of it.
            last = intro_rows[-1]
            last["shot_end"] = round(duration, 3)
            last["duration"] = round(max(0.001, duration - float(last["shot_start"])), 3)
        plans[index][:] = intro_rows + tail_rows
    return assigned_motion_count


def build_media_shot_plan(
    media_items: Sequence[Mapping[str, Any]],
    scene_durations: Sequence[float],
    *,
    config: Mapping[str, Any] | None = None,
    word_timings: Sequence[Any] | None = None,
) -> tuple[list[list[dict[str, Any]]], dict[str, Any]]:
    """Return per-narration-scene shots and an auditable timing summary."""
    config = config if isinstance(config, Mapping) else {}
    try:
        intro_duration = max(0.0, min(30.0, float(config.get("intro_media_duration_seconds", 15.0))))
    except (TypeError, ValueError):
        intro_duration = 15.0
    try:
        intro_min = max(1, min(12, int(config.get("intro_media_min_shots", 6))))
        intro_max = max(intro_min, min(16, int(config.get("intro_media_max_shots", 8))))
    except (TypeError, ValueError):
        intro_min, intro_max = 6, 8
    try:
        max_still_hold = max(1.5, min(8.0, float(config.get("max_still_hold_seconds", 3.5))))
    except (TypeError, ValueError):
        max_still_hold = 3.5
    try:
        max_visual_beat = max(1.5, min(6.0, float(config.get("max_visual_beat_seconds", 3.5))))
    except (TypeError, ValueError):
        max_visual_beat = 3.5
    # Opening pacing is governed first by distinct verified candidates, then
    # by bounded camera-motion windows for stills when the opening needs more
    # beats than the available imagery can support.
    try:
        max_intro_shot = max(1.0, min(2.5, float(config.get("intro_max_shot_seconds", 2.5))))
    except (TypeError, ValueError):
        max_intro_shot = 2.5
    try:
        default_source_cooldown = max(0, len(scene_durations or []) - 1)
        source_reuse_cooldown = max(0, min(
            default_source_cooldown,
            int(config.get("source_reuse_cooldown_scenes", default_source_cooldown)),
        ))
    except (TypeError, ValueError):
        source_reuse_cooldown = max(0, len(scene_durations or []) - 1)

    durations = [_float(value, 0.0) for value in scene_durations]
    plans: list[list[dict[str, Any]]] = [[] for _ in durations]
    offsets: list[float] = []
    cursor = 0.0
    for duration in durations:
        offsets.append(cursor)
        cursor += max(0.0, duration)

    intro_indices = [
        index for index, start in enumerate(offsets)
        if start < intro_duration and durations[index] > 0
    ]
    intro_candidate_rows: dict[int, list[dict[str, Any]]] = {}
    for index in intro_indices:
        item = media_items[index] if index < len(media_items) else {}
        intro_candidate_rows[index] = _scene_candidates(item, index, intro_max)
    scene_candidate_rows: dict[int, list[dict[str, Any]]] = dict(intro_candidate_rows)
    for index, duration in enumerate(durations):
        if index in scene_candidate_rows or duration <= 0:
            continue
        item = media_items[index] if index < len(media_items) else {}
        scene_candidate_rows[index] = _scene_candidates(
            item,
            index,
            max(1, int(math.ceil(duration / max_visual_beat))),
        )
    intro_identity_pool: dict[tuple[str, str, str, str, str, str], list[tuple[int, dict[str, Any]]]] = {}
    for index in intro_indices:
        for row in intro_candidate_rows[index]:
            identity_key = _visual_identity_key(row)
            if identity_key is not None:
                intro_identity_pool.setdefault(identity_key, []).append((index, row))
    claimed_intro_candidates: set[str] = set()
    available_intro_claims = {
        _candidate_claim_key(candidate)
        for rows in intro_candidate_rows.values()
        for candidate in rows
        if _candidate_claim_key(candidate)
    }
    available_intro = len(available_intro_claims)
    # The intro can cut faster when it has distinct verified material, but a
    # sparse pool stays sparse. A zero-candidate or one-candidate opening is
    # recorded honestly instead of being padded with fake duplicate shots.
    intro_director_configured = any(
        key in config
        for key in (
            "intro_media_duration_seconds",
            "intro_media_min_shots",
            "intro_media_max_shots",
        )
    )
    intro_target = min(intro_max, available_intro)
    if intro_target >= intro_min:
        remaining = intro_target
        total_intro_duration = sum(min(durations[index], max(0.0, intro_duration - offsets[index])) for index in intro_indices)
        for position, index in enumerate(intro_indices):
            window = min(durations[index], max(0.0, intro_duration - offsets[index]))
            proportional = max(1, int(round(intro_target * window / max(0.001, total_intro_duration))))
            item = media_items[index] if index < len(media_items) and isinstance(media_items[index], Mapping) else {}
            director_count, preference = _director_media_choice(item)
            available_rows = _preferred_candidates(intro_candidate_rows[index], preference)
            unique_available = [
                candidate for candidate in available_rows
                if _candidate_claim_key(candidate) not in claimed_intro_candidates
            ]
            if unique_available:
                available_rows = unique_available
            count = min(len(available_rows), director_count or proportional, remaining)
            if position == len(intro_indices) - 1:
                count = min(len(available_rows), director_count or remaining, remaining)
            if count:
                scene_has_video = any(
                    _is_video(str(r.get("path") or ""), str(r.get("media_type") or r.get("type") or ""))
                    for r in available_rows
                )
                if scene_has_video:
                    selected_rows = [
                        r for r in intro_candidate_rows[index]
                        if r in available_rows
                        if _is_video(str(r.get("path") or ""), str(r.get("media_type") or r.get("type") or ""))
                    ]
                    if len(selected_rows) == 1:
                        plans[index] = _make_shots(
                            selected_rows,
                            durations[index],
                            intro=True,
                            max_shots=1,
                        )
                        remaining -= 1
                    else:
                        plans[index] = _make_shots(
                            selected_rows,
                            window,
                            intro=True,
                            max_shots=len(selected_rows),
                        )
                        remaining -= len(plans[index])
                        if window < durations[index] - 0.001:
                            # Keep the last moving shot continuous across the
                            # intro boundary. Restarting the same montage in
                            # the scene tail made the opening visibly loop.
                            last = plans[index][-1]
                            last["shot_end"] = round(durations[index], 3)
                            last["duration"] = round(max(
                                0.001, durations[index] - float(last.get("shot_start") or 0.0)
                            ), 3)
                    continue
                # ``proportional`` is the director's shot budget for this
                # scene.  Keep the cadence floor as well, but when sparse
                # media is being intentionally repeated, do not let the
                # physical-duration floor collapse a requested six-shot
                # intro into four shots.
                required_count = director_count or max(
                    1,
                    int(math.ceil(window / max_intro_shot)),
                    proportional,
                )
                # An opening still must not consume a long narration beat
                # when verified, scene-specific alternatives are available.
                # Explanatory canvases and intentionally continuous footage
                # keep their director-authored timing.
                if str(item.get("director_primary_type") or "") in {"SmartImage", "ChapterTitle", "Headline", "KineticText"}:
                    required_count = max(required_count, min(
                        len(available_rows), max(1, int(math.ceil(window / max_intro_shot))),
                    ))
                selected_rows = list(available_rows[:count])
                # Add only distinct verified alternates. A longer hold on one
                # source is preferable to pretending a zoom change is new media.
                if required_count > len(selected_rows):
                    # Prefer unused candidates from this scene, then borrow
                    # an unused candidate from another intro scene only when
                    # the structured identity contract is identical.  This
                    # keeps the intro rich on sparse scenes without turning a
                    # broad topic into an unsafe global image pool.
                    additional: list[dict[str, Any]] = []
                    for candidate in available_rows[len(selected_rows):]:
                        claim_key = _candidate_claim_key(candidate)
                        if claim_key and claim_key in claimed_intro_candidates:
                            continue
                        additional.append(candidate)
                        if len(selected_rows) + len(additional) >= required_count:
                            break
                    identity_key = _visual_identity_key(selected_rows[0]) if selected_rows else None
                    if len(selected_rows) + len(additional) < required_count and identity_key is not None:
                        for source_index, candidate in intro_identity_pool.get(identity_key, []):
                            if source_index == index:
                                continue
                            claim_key = _candidate_claim_key(candidate)
                            if not claim_key or claim_key in claimed_intro_candidates:
                                continue
                            candidate = dict(candidate)
                            candidate["fallback_layer"] = "intro_same_identity_cross_scene"
                            candidate["reuse_kind"] = "intro_cross_scene_candidate"
                            additional.append(candidate)
                            if len(selected_rows) + len(additional) >= required_count:
                                break
                    selected_rows.extend(additional)
                for candidate in selected_rows:
                    claim_key = _candidate_claim_key(candidate)
                    if claim_key:
                        claimed_intro_candidates.add(claim_key)
                plans[index] = _make_shots(
                    selected_rows,
                    window,
                    intro=True,
                    max_shots=len(selected_rows),
                )
                remaining -= len(plans[index])
                if window < durations[index] - 0.001:
                    tail_candidates = [
                        candidate for candidate in available_rows[count:]
                        if _candidate_claim_key(candidate) not in claimed_intro_candidates
                    ]
                    if not tail_candidates and selected_rows:
                        identity_key = _visual_identity_key(selected_rows[0])
                        if identity_key is not None:
                            for source_index, candidate in intro_identity_pool.get(identity_key, []):
                                if source_index == index:
                                    continue
                                claim_key = _candidate_claim_key(candidate)
                                if not claim_key or claim_key in claimed_intro_candidates:
                                    continue
                                borrowed = dict(candidate)
                                borrowed["fallback_layer"] = "intro_same_identity_cross_scene"
                                borrowed["reuse_kind"] = "intro_cross_scene_candidate"
                                tail_candidates = [borrowed]
                                claimed_intro_candidates.add(claim_key)
                                break
                    if tail_candidates:
                        tail = _make_shots(
                            tail_candidates,
                            durations[index] - window,
                            intro=False,
                            max_shots=min(len(tail_candidates), int(math.ceil((durations[index] - window) / max_visual_beat))),
                        )
                        for shot in tail:
                            shot["shot_start"] = round(float(shot["shot_start"]) + window, 3)
                            shot["shot_end"] = round(float(shot["shot_end"]) + window, 3)
                        plans[index].extend(tail)
                    elif plans[index]:
                        plans[index][-1]["shot_end"] = round(durations[index], 3)
                        plans[index][-1]["duration"] = round(max(0.001, durations[index] - float(plans[index][-1].get("shot_start") or 0.0)), 3)
    # The per-scene cadence floor can legitimately ask for more shots than
    # the global intro contract when several narration scenes overlap the
    # first 15 seconds. Trim only the last intro shots (never the tail) until
    # the configured maximum is met, then reflow the affected scene locally.
    # This keeps the intro fast without changing narration or inventing media.
    if intro_director_configured:
        intro_shot_count = sum(
            1 for index in intro_indices for shot in plans[index]
            if bool(shot.get("intro_shot"))
        )
        while intro_shot_count > intro_max:
            removed = False
            for index in reversed(intro_indices):
                intro_positions = [
                    position for position, shot in enumerate(plans[index])
                    if bool(shot.get("intro_shot"))
                ]
                if len(intro_positions) <= 1:
                    continue
                del plans[index][intro_positions[-1]]
                _reflow_shot_timing(plans[index])
                intro_shot_count -= 1
                removed = True
                break
            if not removed:
                break
    adjacent_reuse_repairs_before_assembly = 0
    for index, duration in enumerate(durations):
        if duration <= 0 or plans[index]:
            continue
        item = media_items[index] if index < len(media_items) else {}
        candidates = list(scene_candidate_rows.get(index) or [])
        if not candidates:
            continue
        director_count, preference = _director_media_choice(item if isinstance(item, Mapping) else {})
        candidates = _preferred_candidates(candidates, preference)
        prior_claims = {
            _candidate_claim_key(shot)
            for prior_plan in plans[:index]
            for shot in prior_plan
            if _candidate_claim_key(shot)
        }
        previous_boundary_claim = (
            _candidate_claim_key(plans[index - 1][-1])
            if index > 0 and plans[index - 1]
            else ""
        )
        repeated_boundary_primary = bool(
            candidates
            and previous_boundary_claim
            and _candidate_claim_key(candidates[0]) == previous_boundary_claim
        )
        fresh_candidates = [
            candidate for candidate in candidates
            if _candidate_claim_key(candidate) not in prior_claims
        ]
        if fresh_candidates:
            if repeated_boundary_primary and _candidate_claim_key(fresh_candidates[0]) != previous_boundary_claim:
                # Source selection already avoided this adjacent repeat. Count
                # it alongside the later presentation reorder repairs so the
                # diagnostic reflects all boundary repeats actually fixed.
                adjacent_reuse_repairs_before_assembly += 1
            candidates = fresh_candidates
        scene_has_video = any(
            _is_video(str(c.get("path") or ""), str(c.get("media_type") or c.get("type") or ""))
            for c in candidates
        )
        if scene_has_video:
            video_candidates = [
                c for c in candidates
                if _is_video(str(c.get("path") or ""), str(c.get("media_type") or c.get("type") or ""))
            ]
            local_beat_count = max(1, min(4, int(math.ceil(duration / max_visual_beat))))
            plans[index] = _make_shots(
                video_candidates,
                duration,
                max_shots=min(len(video_candidates), director_count or local_beat_count),
            )
            continue
        media_type = str(item.get("type") or item.get("media_type") or "").lower()
        max_hold = max_still_hold
        wanted = director_count or max(1, int(math.ceil(duration / max_hold)))
        if len(candidates) < wanted:
            identity_key = _visual_identity_key(candidates[0])
            selected_claims = {_candidate_claim_key(candidate) for candidate in candidates}
            previous_claims = prior_claims
            if identity_key is not None:
                # A sparse scene may borrow a verified candidate from another
                # scene only within the same structured identity contract.
                # This is the nearest-match layer: it improves visual variety
                # without opening a broad topic-level image pool.
                pool = []
                pool_claims = set(selected_claims)
                for source_index, source_rows in scene_candidate_rows.items():
                    if source_index == index:
                        continue
                    for candidate in source_rows:
                        if _visual_identity_key(candidate) != identity_key:
                            continue
                        claim_key = _candidate_claim_key(candidate)
                        if not claim_key or claim_key in pool_claims or claim_key in previous_claims:
                            continue
                        pool.append((source_index, candidate))
                        pool_claims.add(claim_key)
                pool.sort(key=lambda pair: (abs(pair[0] - index), pair[0]))
                for _, candidate in pool:
                    borrowed = dict(candidate)
                    borrowed["fallback_layer"] = "same_identity_cross_scene"
                    borrowed["reuse_kind"] = "cross_scene_candidate"
                    candidates.append(borrowed)
                    selected_claims.add(_candidate_claim_key(borrowed))
                    if len(candidates) >= wanted:
                        break
        plans[index] = _make_shots(candidates, duration, max_shots=min(wanted, len(candidates)))
        # If only one verified still exists, it holds once with a single
        # continuous camera move. Never split it into fake new shots.
    # Scenes whose intro candidate pool was too small still receive their
    # strongest verified real asset for the remainder of the narration.
    for index, duration in enumerate(durations):
        if duration <= 0 or plans[index]:
            continue
        item = media_items[index] if index < len(media_items) else {}
        candidates = list(scene_candidate_rows.get(index) or [])[:1]
        plans[index] = _make_shots(candidates, duration, intro=index in intro_indices, max_shots=1)

    # Resolve avoidable repeats introduced when a sparse scene borrows a
    # verified candidate from a neighboring scene. This runs before final
    # narration alignment so any replaced/removed beat receives fresh word
    # boundaries and exact scene coverage.
    source_reuse_audit = _repair_recent_source_reuse(
        plans,
        scene_candidate_rows,
        durations,
        cooldown_scenes=source_reuse_cooldown,
    )
    adjacent_reuse_repairs = (
        _repair_adjacent_shot_reuse(plans)
        + adjacent_reuse_repairs_before_assembly
    )
    perceptual_repetition_audit = _repair_perceptual_reuse(
        plans,
        # Ordinary selection prefers moving footage. Recovery must also see
        # verified still alternates when that footage repeats actual pixels.
        {index: _candidate_rows(item) for index, item in enumerate(media_items) if isinstance(item, Mapping)},
        budget_seconds=_float(config.get("perceptual_review_budget_seconds"), min(40.0, 12.0 + sum(len(plan) for plan in plans) * 0.6)),
        cooldown_scenes=int(_float(config.get("perceptual_reuse_cooldown_scenes"), max(0, len(plans) - 1))),
    )
    timing_audit = _align_plan_to_narration(plans, durations, list(word_timings or config.get("word_timings") or []))
    intro_motion_segments = _rebalance_intro_stills(
        plans,
        durations,
        offsets,
        intro_duration=intro_duration,
        enabled=intro_director_configured,
    )
    continuous_still_motion_count = _set_continuous_still_motion(
        plans,
        max_still_hold=max_still_hold,
    ) + intro_motion_segments

    scene_media_choices = []
    requested_visual_beats = 0
    director_shortfall_indices = []
    for index, plan in enumerate(plans):
        item = media_items[index] if index < len(media_items) and isinstance(media_items[index], Mapping) else {}
        requested, preference = _director_media_choice(item)
        if requested is not None:
            requested_visual_beats += requested
            if len(plan) < requested:
                director_shortfall_indices.append(index)
        scene_media_choices.append({
            "scene_index": index,
            "requested_visual_beats": requested,
            "selected_shots": len(plan),
            "media_preference": preference,
            "selected_types": list(dict.fromkeys(str(shot.get("media_type") or "") for shot in plan)),
        })
    source_claims = [
        _candidate_claim_key(shot)
        for plan in plans for shot in plan
        if _candidate_claim_key(shot)
    ]
    source_reuse_count = len(source_claims) - len(set(source_claims))
    rights_by_source: dict[str, dict[str, Any]] = {}
    for scene_index, plan in enumerate(plans):
        for shot in plan:
            claim = _candidate_claim_key(shot) or f"scene:{scene_index}:{shot.get('candidate_id') or len(rights_by_source)}"
            if claim in rights_by_source:
                continue
            rights_by_source[claim] = {
                "scene_index": scene_index,
                "source_url": str(shot.get("source_url") or ""),
                "provider": str(shot.get("source") or ""),
                "rights_status": str(shot.get("rights_status") or "unknown"),
                "rights_risk": str(shot.get("rights_risk") or "high"),
                "rights_review_required": bool(shot.get("rights_review_required", True)),
                "rights_evidence": str(shot.get("rights_evidence") or "no_license_evidence"),
                "license": str(shot.get("license") or ""),
                "license_url": str(shot.get("license_url") or ""),
                "attribution": str(shot.get("attribution") or ""),
                "creator": str(shot.get("creator") or ""),
            }
    rights_assets = list(rights_by_source.values())
    rights_audit = {
        "source_asset_count": len(rights_assets),
        "review_required_asset_count": sum(1 for row in rights_assets if row["rights_review_required"]),
        "high_risk_asset_count": sum(1 for row in rights_assets if row["rights_risk"] == "high"),
        "unresolved_assets": [row for row in rights_assets if row["rights_review_required"]][:100],
        "release_clearance_required": any(row["rights_review_required"] for row in rights_assets),
    }

    summary = {
        "schema_version": "media_shot_plan.v1",
        "scene_count": len(plans),
        "shot_count": sum(len(plan) for plan in plans),
        "video_shot_count": sum(
            1 for plan in plans for shot in plan
            if str(shot.get("media_type") or "").lower() == "video"
        ),
        "intro_scene_indices": intro_indices,
        "intro_shot_count": sum(
            1 for index in intro_indices for shot in plans[index]
            if bool(shot.get("intro_shot")) and float(shot.get("shot_start", 0.0)) + offsets[index] < intro_duration
        ),
        "intro_shortfall": bool(intro_target < intro_min),
        "intro_available_verified_candidates": available_intro,
        "intro_target": {"min": intro_min, "max": intro_max, "duration_seconds": intro_duration},
        "director_requested_visual_beats": requested_visual_beats,
        # Kept for saved-manifest compatibility; the planner no longer
        # duplicates stills into artificial cuts to meet a pacing quota.
        "still_motion_segments": 0,
        "continuous_still_motion_count": continuous_still_motion_count,
        "director_media_shortfall_scene_indices": director_shortfall_indices,
        "scene_media_choices": scene_media_choices,
        "repeated_source_shot_count": source_reuse_count,
        "source_reuse_cooldown": source_reuse_audit,
        "perceptual_repetition_audit": perceptual_repetition_audit,
        "asset_rights_audit": rights_audit,
        "max_still_hold_seconds": max_still_hold,
        "max_visual_beat_seconds": max_visual_beat,
        "intro_max_shot_seconds": max_intro_shot,
        "cross_scene_fallback_shot_count": sum(
            1 for plan in plans for shot in plan
            if str(shot.get("fallback_layer") or "") == "same_identity_cross_scene"
        ),
        "motion_segment_count": sum(
            1 for plan in plans for shot in plan
            if str(shot.get("reuse_kind") or "") in {"still_motion_segment", "intro_motion_segment"}
        ),
        "adjacent_reuse_repairs": adjacent_reuse_repairs,
        "narration_timing_alignment": timing_audit,
        "timing_source": (
            "word_timing_boundaries"
            if timing_audit["word_boundary_cut_count"] == timing_audit["internal_cut_count"] and timing_audit["internal_cut_count"]
            else "partial_word_timing_boundaries"
            if timing_audit["word_boundary_cut_count"]
            else "even_visual_beats"
        ),
        "unresolved_scene_indices": [index for index, plan in enumerate(plans) if not plan],
    }
    return plans, summary
