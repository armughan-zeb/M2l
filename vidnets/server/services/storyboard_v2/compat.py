"""Read-only compatibility loading for pre-V2 storyboard artifacts.

Legacy Vidzeo jobs persisted several related, parallel structures rather than
one storyboard contract.  Depending on the entry point, a saved payload may
contain ``scene_texts``/``scene_durations`` and ``media_paths`` arrays, a V1
component manifest with ``video_clips``, or a semantic manifest with scene
rows.  This module can expose those artifacts through the V2 reader API
without ever upgrading the file in place.

Compatibility imports are deliberately fail-closed:

* media is retained only as an explicitly *unverified* candidate;
* index assignment is allowed only after exact scene-span reconstruction;
* old text graphics are imported only when their text and timing are explicit
  and the text maps to one unambiguous source span; and
* insufficient artifacts raise ``ContractValidationError`` instead of
  fabricating narration, media, or card copy.

All new persistence continues to use :class:`StoryboardV2` and schema 2.0.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from .models import (
    SCHEMA_VERSION,
    SUPPORTED_FORMATS,
    AttemptLedgerEntryV1,
    ContractValidationError,
    EditorialCueV2,
    GenerationRequestV2,
    MediaCandidateV1,
    SceneContractV1,
    ScriptPlanV1,
    StoryboardV2,
    stable_id,
    utc_now_iso,
)
from .script_parser import build_script_plan
from .text import token_range_for_char_span, tokenize_source


MAX_COMPAT_FILE_BYTES = 16 * 1024 * 1024
MAX_COMPAT_SCENES = 10_000
_VIEW_KEYS = (
    "storyboard",
    "request",
    "script_plan",
    "config",
    "result",
    "component_manifest",
    "manifest",
    "semantic_manifest",
    "editor_manifest",
    "audio_plan",
    "media_plan",
    "metadata",
    "prompt_bundle",
)
_GENERIC_CARD_COPY = {
    "key point",
    "important",
    "did you know",
    "fact",
    "quick fact",
    "highlight",
}
_VIDEO_SUFFIXES = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
    ".m4v",
    ".wmv",
}


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _is_sequence(value: Any) -> bool:
    return isinstance(value, (list, tuple))


def _views(raw: Mapping[str, Any]) -> Tuple[Mapping[str, Any], ...]:
    """Return bounded legacy payload views in deterministic precedence order."""

    result: List[Mapping[str, Any]] = []
    queue: List[Tuple[Mapping[str, Any], int]] = [(raw, 0)]
    seen: set[int] = set()
    while queue:
        current, depth = queue.pop(0)
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(current)
        if depth >= 3:
            continue
        for key in _VIEW_KEYS:
            child = current.get(key)
            if isinstance(child, Mapping):
                queue.append((child, depth + 1))
    return tuple(result)


def _first_value(views: Sequence[Mapping[str, Any]], keys: Iterable[str]) -> Any:
    for view in views:
        for key in keys:
            value = view.get(key)
            if value is not None:
                return value
    return None


def _first_text(views: Sequence[Mapping[str, Any]], keys: Iterable[str]) -> str:
    value = _first_value(views, keys)
    return str(value).strip() if value is not None else ""


def _first_list(views: Sequence[Mapping[str, Any]], keys: Iterable[str]) -> List[Any]:
    value = _first_value(views, keys)
    return list(value) if _is_sequence(value) else []


def _safe_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if result != result or result in {float("inf"), float("-inf")}:
        return None
    return result


def _read_payload(source: Mapping[str, Any] | str | os.PathLike[str]) -> Mapping[str, Any]:
    if isinstance(source, Mapping):
        return source
    path = Path(source)
    try:
        if path.stat().st_size > MAX_COMPAT_FILE_BYTES:
            raise ContractValidationError("Legacy storyboard exceeds the compatibility size limit")
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except ContractValidationError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"Unable to read legacy storyboard: {path}") from exc
    if not isinstance(payload, Mapping):
        raise ContractValidationError("Storyboard root must be a JSON object")
    return payload


def _scene_rows(views: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    for view in views:
        rows = view.get("scenes")
        if _is_sequence(rows) and rows and all(isinstance(row, Mapping) for row in rows):
            if len(rows) > MAX_COMPAT_SCENES:
                raise ContractValidationError("Legacy storyboard exceeds the scene limit")
            return [row for row in rows if isinstance(row, Mapping)]
    return []


def _legacy_scene_texts(
    views: Sequence[Mapping[str, Any]], scene_rows: Sequence[Mapping[str, Any]]
) -> List[Tuple[int, str]]:
    rows = _first_list(views, ("scene_texts", "source_scene_texts", "beat_texts"))
    if rows:
        if len(rows) > MAX_COMPAT_SCENES:
            raise ContractValidationError("Legacy storyboard exceeds the scene limit")
        return [
            (index, str(value).strip())
            for index, value in enumerate(rows)
            if str(value or "").strip()
        ]
    result: List[Tuple[int, str]] = []
    for index, row in enumerate(scene_rows):
        text = str(
            row.get("narration")
            or row.get("text")
            or row.get("scene_text")
            or row.get("script_segment")
            or ""
        ).strip()
        if text:
            result.append((index, text))
    return result


def _explicit_scene_spans(
    script: str,
    pairs: Sequence[Tuple[int, str]],
    scene_rows: Sequence[Mapping[str, Any]],
    *,
    reconstructed: bool,
) -> Optional[List[Tuple[int, int, int]]]:
    """Map old scene indexes to exact source spans, or fail closed.

    The returned spans cover the complete script.  Legacy row offsets are used
    only when every row proves its own text against the source; otherwise the
    text must have one unambiguous monotonic occurrence.
    """

    if not pairs:
        return None
    if reconstructed:
        result: List[Tuple[int, int, int]] = []
        cursor = 0
        for position, (legacy_index, text) in enumerate(pairs):
            end = cursor + len(text) + (1 if position + 1 < len(pairs) else 0)
            result.append((legacy_index, cursor, end))
            cursor = end
        return result if cursor == len(script) else None

    explicit_starts: List[Tuple[int, int]] = []
    explicit_offsets_ok = bool(scene_rows) and all(
        legacy_index < len(scene_rows) for legacy_index, _text in pairs
    )
    if explicit_offsets_ok:
        for legacy_index, expected in pairs:
            row = scene_rows[legacy_index]
            start = row.get("char_start")
            end = row.get("char_end")
            if start is None or end is None:
                source_span = _as_mapping(row.get("source_span"))
                start, end = source_span.get("char_start"), source_span.get("char_end")
            try:
                start_i, end_i = int(start), int(end)
            except (TypeError, ValueError):
                explicit_offsets_ok = False
                break
            if not (0 <= start_i < end_i <= len(script)):
                explicit_offsets_ok = False
                break
            if script[start_i:end_i].strip() != expected:
                explicit_offsets_ok = False
                break
            explicit_starts.append((legacy_index, start_i))

    starts: List[Tuple[int, int]] = []
    if explicit_offsets_ok and len(explicit_starts) == len(pairs):
        starts = explicit_starts
    else:
        cursor = 0
        for legacy_index, expected in pairs:
            occurrences: List[int] = []
            search_at = cursor
            while True:
                position = script.find(expected, search_at)
                if position < 0:
                    break
                occurrences.append(position)
                if len(occurrences) > 1:
                    break
                search_at = position + max(1, len(expected))
            if len(occurrences) != 1:
                return None
            starts.append((legacy_index, occurrences[0]))
            cursor = occurrences[0] + len(expected)

    if any(right[1] <= left[1] for left, right in zip(starts, starts[1:])):
        return None
    result = []
    for position, (legacy_index, _start) in enumerate(starts):
        start = 0 if position == 0 else starts[position][1]
        end = starts[position + 1][1] if position + 1 < len(starts) else len(script)
        if end <= start:
            return None
        result.append((legacy_index, start, end))
    return result


def _timing_rows(
    views: Sequence[Mapping[str, Any]],
    scene_rows: Sequence[Mapping[str, Any]],
) -> Tuple[List[Any], List[Mapping[str, Any]]]:
    durations = _first_list(views, ("scene_durations", "durations"))
    clips = []
    for view in views:
        value = view.get("video_clips")
        if _is_sequence(value):
            clips = [row for row in value if isinstance(row, Mapping)]
            if clips:
                break
    return durations, clips


def _scene_timing(
    legacy_index: int,
    scene_rows: Sequence[Mapping[str, Any]],
    durations: Sequence[Any],
    clips: Sequence[Mapping[str, Any]],
    cumulative_start: float,
) -> Tuple[Optional[float], Optional[float], float]:
    row = scene_rows[legacy_index] if legacy_index < len(scene_rows) else {}
    clip = clips[legacy_index] if legacy_index < len(clips) else {}
    start = _safe_float(
        row.get("start_seconds", row.get("start_time", row.get("start")))
    )
    end = _safe_float(row.get("end_seconds", row.get("end_time")))
    duration = _safe_float(row.get("duration"))
    if duration is None and legacy_index < len(durations):
        duration = _safe_float(durations[legacy_index])
    if duration is None and clip:
        clip_start = _safe_float(clip.get("start"))
        clip_end = _safe_float(clip.get("end"))
        if clip_start is not None and clip_end is not None and clip_end > clip_start:
            duration = clip_end - clip_start
        start = _safe_float(clip.get("track_offset")) if start is None else start
    if end is None and start is not None and duration is not None:
        end = start + duration
    if start is None and duration is not None:
        start, end = cumulative_start, cumulative_start + duration
    if start is None or end is None or start < 0 or end <= start:
        return None, None, cumulative_start
    return start, end, max(cumulative_start, end)


def _legacy_plan(
    request: GenerationRequestV2,
    pairs: Sequence[Tuple[int, str]],
    scene_rows: Sequence[Mapping[str, Any]],
    views: Sequence[Mapping[str, Any]],
    *,
    reconstructed: bool,
) -> Tuple[ScriptPlanV1, Dict[str, int], str]:
    base = build_script_plan(
        request.script,
        content_title=request.content_title,
        format_hint=request.format_hint,
        language=request.language,
        scene_mode=request.scene_mode,
    )
    spans = _explicit_scene_spans(
        request.script,
        pairs,
        scene_rows,
        reconstructed=reconstructed,
    )
    if not spans:
        return base, {}, "canonical_v2_reparse_no_safe_legacy_index_map"

    leaves = base.leaf_containers
    source_tokens = tokenize_source(request.script)
    policy_by_container = {
        scene.container_id: scene for scene in base.scenes
    }
    durations, clips = _timing_rows(views, scene_rows)
    cumulative = 0.0
    migrated: List[SceneContractV1] = []
    scene_to_legacy: Dict[str, int] = {}
    for order, (legacy_index, start, end) in enumerate(spans):
        container = next(
            (
                leaf
                for leaf in leaves
                if leaf.char_start <= start < end <= leaf.char_end
            ),
            None,
        )
        if container is None:
            return base, {}, "canonical_v2_reparse_legacy_scene_crossed_container"
        template = policy_by_container.get(container.container_id)
        if template is None:
            return base, {}, "canonical_v2_reparse_missing_container_policy"
        token_start, token_end = token_range_for_char_span(source_tokens, start, end)
        scene = SceneContractV1(
            scene_id=stable_id(
                "scn", base.script_hash, container.container_id, start, end
            ),
            order=order,
            container_id=container.container_id,
            text=request.script[start:end],
            char_start=start,
            char_end=end,
            token_start=token_start,
            token_end=token_end,
            visual_intent=template.visual_intent,
            positive_subjects=template.positive_subjects,
            forbidden_subjects=template.forbidden_subjects,
            source_policy=template.source_policy,
            reuse_policy=template.reuse_policy,
        ).with_computed_hash()
        timing_start, timing_end, cumulative = _scene_timing(
            legacy_index, scene_rows, durations, clips, cumulative
        )
        if timing_start is not None and timing_end is not None:
            scene = scene.with_timing(
                word_start_index=None,
                word_end_index=None,
                start_seconds=timing_start,
                end_seconds=timing_end,
                timing_quality="legacy_estimated",
                alignment_confidence=0.0,
            )
        migrated.append(scene)
        scene_to_legacy[scene.scene_id] = legacy_index
    return base.with_scenes(migrated), scene_to_legacy, "exact_legacy_scene_spans"


def _media_rows(views: Sequence[Mapping[str, Any]]) -> Tuple[List[Any], List[str], List[Mapping[str, Any]]]:
    paths = _first_list(views, ("media_paths", "image_paths", "stock_paths"))
    labels = [str(value or "") for value in _first_list(views, ("source_labels", "sources"))]
    rows: List[Mapping[str, Any]] = []
    for key in ("video_clips", "media_candidates", "media_items"):
        for view in views:
            value = view.get(key)
            if _is_sequence(value) and value:
                rows = [row if isinstance(row, Mapping) else {} for row in value]
                break
        if rows:
            break
    return paths, labels, rows


def _http_url(value: Any) -> str:
    text = str(value or "").strip()
    try:
        return text if urlsplit(text).scheme.casefold() in {"http", "https"} else ""
    except ValueError:
        return ""


def _infer_media_type(path: str, explicit: Any) -> str:
    value = str(explicit or "").strip().casefold()
    if value in {"image", "video"}:
        return value
    suffix = Path(urlsplit(path).path).suffix.casefold() if path else ""
    return "video" if suffix in _VIDEO_SUFFIXES else "image"


def _candidate_for_scene(
    scene: SceneContractV1,
    legacy_index: int,
    paths: Sequence[Any],
    labels: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> Optional[MediaCandidateV1]:
    row: Mapping[str, Any] = rows[legacy_index] if legacy_index < len(rows) else {}
    path_value = paths[legacy_index] if legacy_index < len(paths) else None
    if isinstance(path_value, Mapping):
        merged = dict(path_value)
        merged.update(dict(row))
        row = merged
        path_value = row.get("local_path") or row.get("path") or row.get("src")
    explicit_path = str(
        path_value
        or row.get("local_path")
        or row.get("path")
        or row.get("src")
        or ""
    ).strip()
    origin_url = _http_url(
        row.get("origin_url")
        or row.get("source_url")
        or row.get("publisher_url")
        or row.get("url")
        or explicit_path
    )
    local_path = "" if _http_url(explicit_path) else explicit_path
    if not local_path and not origin_url:
        return None
    label = labels[legacy_index] if legacy_index < len(labels) else ""
    explicit_source = str(row.get("source") or label or "legacy_import").strip()
    fingerprint = str(row.get("fingerprint") or row.get("sha256") or "").strip()
    candidate_id = stable_id(
        "cand_legacy",
        scene.scene_id,
        legacy_index,
        fingerprint,
        local_path,
        origin_url,
    )
    gate_outcomes = {
        "compatibility_import": "v1_read_only",
        "legacy_scene_index": legacy_index,
        "legacy_container_id": str(row.get("container_id") or ""),
        "integrity_verified": False,
        "relevance_verified": False,
        "container_ownership_verified": False,
        "qwen_verified": False,
        "eligible_for_automatic_render": False,
    }
    video_start = _safe_float(row.get("video_start", row.get("video_start_seconds")))
    video_end = _safe_float(row.get("video_end", row.get("video_end_seconds")))
    if video_start is None or video_end is None or video_start < 0 or video_end <= video_start:
        video_start, video_end = None, None
    return MediaCandidateV1(
        candidate_id=candidate_id,
        scene_id=scene.scene_id,
        container_id=scene.container_id,
        provider=str(row.get("provider") or "legacy").strip() or "legacy",
        source=explicit_source,
        source_id=str(row.get("source_id") or row.get("id") or "").strip(),
        origin_url=origin_url,
        local_path=local_path,
        media_type=_infer_media_type(local_path or origin_url, row.get("media_type") or row.get("type")),
        query=str(row.get("query") or row.get("query_used") or "").strip(),
        fingerprint=fingerprint,
        rights_status=str(row.get("rights_status") or "unknown").strip() or "unknown",
        license=str(row.get("license") or "").strip(),
        attribution=str(row.get("attribution") or row.get("creator") or "").strip(),
        video_start_seconds=video_start,
        video_end_seconds=video_end,
        deterministic_scores={},
        qwen_result={"status": "not_run", "reason": "legacy_v1_import"},
        gate_outcomes=gate_outcomes,
        status="migrated_unverified",
    )


def _cue_rows(
    views: Sequence[Mapping[str, Any]], scene_rows: Sequence[Mapping[str, Any]]
) -> List[Tuple[Optional[int], Mapping[str, Any]]]:
    for key in ("editorial_cues", "text_cards", "graphic_elements", "text_overlays"):
        for view in views:
            value = view.get(key)
            if _is_sequence(value) and value:
                return [
                    (None, row)
                    for row in value
                    if isinstance(row, Mapping)
                ]
    result: List[Tuple[Optional[int], Mapping[str, Any]]] = []
    for index, scene in enumerate(scene_rows):
        graphic = _as_mapping(scene.get("graphic"))
        cue = graphic.get("cue")
        if isinstance(cue, Mapping):
            result.append((index, cue))
    return result


def _cue_scene_index(row: Mapping[str, Any], fallback: Optional[int]) -> Optional[int]:
    value = row.get("scene_index", row.get("scene_number"))
    if value is None:
        return fallback
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    if row.get("scene_number") is not None and row.get("scene_index") is None and index > 0:
        index -= 1
    return index if index >= 0 else None


def _grounded_cue_text(row: Mapping[str, Any], scene: SceneContractV1) -> Optional[Tuple[str, int, int]]:
    props = _as_mapping(row.get("props"))
    candidates = (
        row.get("text"),
        row.get("title"),
        row.get("label"),
        row.get("value"),
        row.get("emphasis"),
        props.get("text"),
        props.get("title"),
        props.get("value"),
        props.get("label"),
    )
    for raw_text in candidates:
        text = str(raw_text or "").strip()
        if not text or text.casefold() in _GENERIC_CARD_COPY:
            continue
        explicit_start = row.get("char_start")
        explicit_end = row.get("char_end")
        try:
            start, end = int(explicit_start), int(explicit_end)
        except (TypeError, ValueError):
            start, end = -1, -1
        if (
            scene.char_start <= start < end <= scene.char_end
            and scene.text[start - scene.char_start : end - scene.char_start] == text
        ):
            return text, start, end
        occurrences: List[int] = []
        search_at = 0
        while True:
            position = scene.text.find(text, search_at)
            if position < 0:
                break
            occurrences.append(position)
            if len(occurrences) > 1:
                break
            search_at = position + max(1, len(text))
        if len(occurrences) == 1:
            start = scene.char_start + occurrences[0]
            return text, start, start + len(text)
    return None


def _explicit_cue_interval(
    row: Mapping[str, Any], scene: SceneContractV1
) -> Optional[Tuple[float, float, float, float]]:
    if scene.start_seconds is None or scene.end_seconds is None:
        return None
    local_start = _safe_float(row.get("scene_local_start_seconds"))
    local_end = _safe_float(row.get("scene_local_end_seconds"))
    absolute_start = _safe_float(row.get("absolute_start_seconds"))
    absolute_end = _safe_float(row.get("absolute_end_seconds"))
    if local_start is None or local_end is None:
        if absolute_start is None or absolute_end is None:
            return None
        local_start = absolute_start - scene.start_seconds
        local_end = absolute_end - scene.start_seconds
    if absolute_start is None or absolute_end is None:
        absolute_start = scene.start_seconds + local_start
        absolute_end = scene.start_seconds + local_end
    duration = scene.end_seconds - scene.start_seconds
    if (
        local_start < 0
        or local_end <= local_start
        or local_end > duration + 1e-6
        or absolute_start < scene.start_seconds - 1e-6
        or absolute_end > scene.end_seconds + 1e-6
    ):
        return None
    return local_start, local_end, absolute_start, absolute_end


def _migrate_cues(
    cue_rows: Sequence[Tuple[Optional[int], Mapping[str, Any]]],
    plan: ScriptPlanV1,
    scene_to_legacy: Mapping[str, int],
) -> Tuple[Tuple[EditorialCueV2, ...], Dict[str, str]]:
    legacy_to_scene = {legacy: scene_id for scene_id, legacy in scene_to_legacy.items()}
    by_id = {scene.scene_id: scene for scene in plan.scenes}
    cues: List[EditorialCueV2] = []
    states: Dict[str, str] = {}
    used_scenes: set[str] = set()
    for fallback_index, row in cue_rows:
        legacy_index = _cue_scene_index(row, fallback_index)
        scene_id = legacy_to_scene.get(legacy_index) if legacy_index is not None else None
        scene = by_id.get(scene_id or "")
        if scene is None or scene.scene_id in used_scenes:
            continue
        grounded = _grounded_cue_text(row, scene)
        interval = _explicit_cue_interval(row, scene)
        if grounded is None or interval is None:
            states[scene.scene_id] = "dropped_unverified_legacy_cue"
            continue
        text, char_start, char_end = grounded
        token_start, token_end = token_range_for_char_span(
            tokenize_source(plan.source_script), char_start, char_end
        )
        if token_end <= token_start:
            states[scene.scene_id] = "dropped_unverified_legacy_cue"
            continue
        local_start, local_end, absolute_start, absolute_end = interval
        cue = EditorialCueV2(
            cue_id=stable_id("cue_legacy", scene.scene_id, char_start, char_end, local_start, local_end),
            scene_id=scene.scene_id,
            container_id=scene.container_id,
            cue_type=str(row.get("cue_type") or row.get("type") or "legacy_grounded"),
            text=text,
            char_start=char_start,
            char_end=char_end,
            token_start=token_start,
            token_end=token_end,
            word_start_index=None,
            word_end_index=None,
            scene_local_start_seconds=local_start,
            scene_local_end_seconds=local_end,
            absolute_start_seconds=absolute_start,
            absolute_end_seconds=absolute_end,
            timing_quality="legacy_explicit_unverified",
            template="generic_highlight",
            props={"title": text, "migration": "v1_read_only"},
            placement={},
            audit_reason="legacy cue imported with exact text grounding and explicit timing",
        )
        cues.append(cue)
        used_scenes.add(scene.scene_id)
        states[scene.scene_id] = "migrated_grounded_unverified"
    return tuple(cues), states


def _resolve_scope_id(
    raw_value: Any,
    requested: Optional[str],
    *,
    kind: str,
    seed: str,
) -> str:
    explicit = str(raw_value or "").strip()
    wanted = str(requested or "").strip()
    if explicit and wanted and explicit != wanted:
        raise ContractValidationError(
            f"Legacy storyboard {kind}_id does not match the requested scope"
        )
    return wanted or explicit or stable_id(f"legacy_{kind}", seed)


def upgrade_storyboard_v1(
    raw: Mapping[str, Any],
    *,
    job_id: str | None = None,
    run_id: str | None = None,
) -> StoryboardV2:
    """Upgrade one legacy in-memory artifact without mutating or writing it."""

    if not isinstance(raw, Mapping):
        raise ContractValidationError("Legacy storyboard root must be an object")
    all_views = _views(raw)
    rows = _scene_rows(all_views)
    pairs = _legacy_scene_texts(all_views, rows)
    if len(pairs) > MAX_COMPAT_SCENES:
        raise ContractValidationError("Legacy storyboard exceeds the scene limit")

    script = _first_text(all_views, ("script", "source_script", "narration_script"))
    reconstructed = False
    if not script:
        if not pairs:
            raise ContractValidationError(
                "Legacy artifact has no script or scene narration; safe migration is impossible"
            )
        script = "\n".join(text for _index, text in pairs)
        reconstructed = True
    if not script.strip():
        raise ContractValidationError("Legacy storyboard script must not be empty")

    format_hint = _first_text(all_views, ("format_hint", "detected_format")) or "auto"
    format_hint = format_hint.casefold().replace("-", "_").replace(" ", "_")
    if format_hint not in SUPPORTED_FORMATS:
        format_hint = "auto"
    scene_mode = _first_text(all_views, ("scene_mode",)) or "adaptive"
    request = GenerationRequestV2.from_mapping(
        {
            "script": script,
            "content_title": _first_text(all_views, ("content_title", "title", "topic")),
            "output_name": _first_text(all_views, ("output_name", "name")) or "Legacy Project",
            "format_hint": format_hint,
            "language": _first_text(all_views, ("language",)) or "auto",
            "scene_mode": scene_mode,
            "media_mode": "legacy_read_only",
            "youtube_rights_mode": "legacy_unknown",
            "allow_relevant_stock": False,
            "pipeline_v2_mode": "compat_read_only",
        }
    )
    plan, scene_to_legacy, alignment_status = _legacy_plan(
        request,
        pairs,
        rows,
        all_views,
        reconstructed=reconstructed,
    )

    source_schema = str(_first_value(all_views, ("schema_version", "version")) or "1.0")
    seed = stable_id(
        "compat",
        plan.script_hash,
        request.output_name,
        source_schema,
    )
    legacy_job_id = _first_value(all_views, ("job_id",))
    if (
        legacy_job_id is None
        and isinstance(raw.get("config"), Mapping)
        and isinstance(raw.get("result"), Mapping)
    ):
        # Queue rows historically called the job identifier simply ``id``.
        legacy_job_id = raw.get("id")
    effective_job_id = _resolve_scope_id(
        legacy_job_id, job_id, kind="job", seed=seed
    )
    effective_run_id = _resolve_scope_id(
        _first_value(all_views, ("run_id",)), run_id, kind="run", seed=seed
    )

    paths, labels, media_rows = _media_rows(all_views)
    media_candidates: List[MediaCandidateV1] = []
    scene_states: Dict[str, Dict[str, Any]] = {}
    for scene in plan.scenes:
        legacy_index = scene_to_legacy.get(scene.scene_id)
        state: Dict[str, Any] = {
            "terminal_state": "degraded",
            "degradation_reason": "legacy_v1_read_only_import",
            "timing_status": scene.timing_quality,
            "media_status": "unresolved",
            "compatibility": {
                "adapter": "storyboard_v1_read_only",
                "source_schema_version": source_schema,
                "scene_alignment": alignment_status,
                "legacy_scene_index": legacy_index,
            },
        }
        if legacy_index is not None:
            candidate = _candidate_for_scene(
                scene, legacy_index, paths, labels, media_rows
            )
            if candidate is not None:
                media_candidates.append(candidate)
                state["media_status"] = "migrated_unverified"
                state["legacy_media_candidate_id"] = candidate.candidate_id
                state["automatic_render_eligible"] = False
        scene_states[scene.scene_id] = state

    raw_cues = _cue_rows(all_views, rows)
    cues, cue_states = _migrate_cues(raw_cues, plan, scene_to_legacy)
    for scene_id, cue_status in cue_states.items():
        scene_states[scene_id]["legacy_cue_status"] = cue_status

    now = utc_now_iso()
    ledger = (
        AttemptLedgerEntryV1(
            sequence=1,
            timestamp=now,
            event_type="compat.v1_loaded",
            job_id=effective_job_id,
            run_id=effective_run_id,
            payload={
                "adapter": "storyboard_v1_read_only",
                "source_schema_version": source_schema,
                "script_reconstructed_from_scene_texts": reconstructed,
                "scene_alignment": alignment_status,
                "legacy_scene_count": len(pairs),
                "mapped_scene_count": len(scene_to_legacy),
                "legacy_media_count": max(len(paths), len(media_rows)),
                "unverified_media_retained": len(media_candidates),
                "legacy_cue_count": len(raw_cues),
                "grounded_cues_retained": len(cues),
                "automatic_render_eligible": False,
            },
        ),
    )
    return StoryboardV2(
        job_id=effective_job_id,
        run_id=effective_run_id,
        request=request,
        script_plan=plan,
        media_candidates=tuple(media_candidates),
        editorial_cues=cues,
        scene_states=scene_states,
        attempt_ledger=ledger,
        created_at=now,
        updated_at=now,
        schema_version=SCHEMA_VERSION,
    )


def load_storyboard_compatible(
    source: Mapping[str, Any] | str | os.PathLike[str],
    *,
    job_id: str | None = None,
    run_id: str | None = None,
) -> StoryboardV2:
    """Load V2 directly or expose a safe, in-memory V1 compatibility view.

    This function performs no writes.  Callers that subsequently choose to
    save the returned object will emit the ordinary V2 schema, never V1.
    """

    raw = _read_payload(source)
    source_schema = str(raw.get("schema_version") or "")
    is_native_v2 = source_schema.split(".", 1)[0] == "2"
    if (
        is_native_v2
        and isinstance(raw.get("request"), Mapping)
        and isinstance(raw.get("script_plan"), Mapping)
    ):
        storyboard = StoryboardV2.from_dict(raw)
        if job_id and storyboard.job_id != str(job_id):
            raise ContractValidationError("Storyboard job_id does not match the requested scope")
        if run_id and storyboard.run_id != str(run_id):
            raise ContractValidationError("Storyboard run_id does not match the requested scope")
        return storyboard
    return upgrade_storyboard_v1(raw, job_id=job_id, run_id=run_id)


__all__ = [
    "MAX_COMPAT_FILE_BYTES",
    "load_storyboard_compatible",
    "upgrade_storyboard_v1",
]
