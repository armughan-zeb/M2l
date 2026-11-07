"""Deterministic validation for scene timing and editorial sound-effect cues."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence


def sound_effect_family(kind: Any) -> str:
    """Group near-identical SFX so a video cannot pass on filename variety alone."""
    value = re.sub(r"[^a-z0-9]+", "_", str(kind or "").casefold()).strip("_")
    if any(token in value for token in ("water", "wave", "hydraulic", "splash")):
        return "water"
    if any(token in value for token in ("whoosh", "swell", "riser", "transition", "slider", "zoom_whirr", "suck")):
        return "movement"
    if any(token in value for token in ("paper", "archive", "film", "photo", "rustle", "projector")):
        return "archive"
    if any(token in value for token in ("tick", "type", "typing", "click", "counter", "cash", "keyboard")):
        return "data"
    if any(token in value for token in ("map", "route", "sonar", "ping")):
        return "spatial"
    if any(token in value for token in ("impact", "hit", "boom", "sting", "bass_drop", "snap")):
        return "impact"
    if any(token in value for token in ("room_tone", "wind_room", "vinyl_crackle", "low_rumble", "archive_drone")):
        return "ambience"
    return value or "other"


def whisper_anchor_time(
    scene: Mapping[str, Any],
    anchor: str,
    *,
    edge: str = "start",
) -> Optional[float]:
    """Return the onset or end of an exact contiguous Whisper-timed phrase."""
    tokens = [token for token in re.findall(r"\d+(?:[.,]\d+)?|[A-Za-z]+(?:['’][A-Za-z]+)?", str(anchor or ""))]
    normalized_anchor = [re.sub(r"[^a-z0-9]+", "", token.casefold()) for token in tokens]
    normalized_anchor = [token for token in normalized_anchor if token]
    if not normalized_anchor:
        return None

    timed_words = []
    for phrase in scene.get("word_cues") or []:
        if not isinstance(phrase, Mapping) or not str(phrase.get("timing_quality") or "").startswith("whisper"):
            continue
        timed_words.extend(word for word in phrase.get("timed_words") or [] if isinstance(word, Mapping))
    timed_words.sort(key=lambda word: int(word.get("index", 0) or 0))
    spoken = [
        re.sub(r"[^a-z0-9]+", "", str(word.get("text") or "").casefold())
        for word in timed_words
    ]
    for offset in range(0, len(spoken) - len(normalized_anchor) + 1):
        if spoken[offset:offset + len(normalized_anchor)] != normalized_anchor:
            continue
        group = timed_words[offset:offset + len(normalized_anchor)]
        indices = [int(word.get("index", -1)) for word in group]
        if indices != list(range(indices[0], indices[0] + len(indices))):
            continue
        first = group[0]
        if str(edge or "start").casefold() == "end":
            last = group[-1]
            if last.get("absolute_end") is not None:
                return float(last["absolute_end"])
            return float(scene.get("start") or 0.0) + float(last.get("end") or 0.0)
        if first.get("absolute_start") is not None:
            return float(first["absolute_start"])
        return float(scene.get("start") or 0.0) + float(first.get("start") or 0.0)
    return None


def validate_whisper_scene_plan(
    script: str,
    scene_texts: Sequence[str],
    scene_timing_windows: Sequence[Mapping[str, Any]],
    word_timings: Sequence[Mapping[str, Any]],
    audio_duration: float,
    *,
    timing_source: str = "whisper",
    tolerance_seconds: float = float(os.environ.get("VIDZEO_WHISPER_CUT_TOLERANCE", 0.25)),
) -> Dict[str, Any]:
    """Verify exact script coverage and that scene cuts land on Whisper words."""

    def tokens(value: Any) -> list[str]:
        return [
            token.casefold().replace("’", "'")
            for token in re.findall(r"\d+(?:[.,]\d+)?|[A-Za-z]+(?:['’][A-Za-z]+)?", str(value or ""))
        ]

    errors = []
    warnings = []
    source_tokens = tokens(script)
    scene_tokens = [token for text in (scene_texts or []) for token in tokens(text)]
    if not source_tokens or source_tokens != scene_tokens:
        errors.append({
            "code": "scene_script_coverage_mismatch",
            "script_token_count": len(source_tokens),
            "scene_token_count": len(scene_tokens),
        })

    duration = max(0.0, float(audio_duration or 0.0))
    windows = [row for row in (scene_timing_windows or []) if isinstance(row, Mapping)]
    if len(windows) != len(scene_texts or []):
        errors.append({"code": "scene_window_count_mismatch", "scene_count": len(scene_texts or []), "window_count": len(windows)})

    ordered_words: Dict[int, float] = {}
    for fallback_index, row in enumerate(word_timings or []):
        if not isinstance(row, Mapping):
            continue
        try:
            index = int(row.get("index", fallback_index))
            start = float(row.get("start_seconds", row.get("start", 0.0)) or 0.0)
        except (TypeError, ValueError):
            continue
        ordered_words[index] = start

    normalized_windows = []
    cursor = 0.0
    for index, window in enumerate(windows):
        try:
            start = float(window.get("start_seconds", window.get("start", cursor)) or 0.0)
            end = float(window.get("end_seconds", start + float(window.get("duration_needed") or 0.0)) or 0.0)
        except (TypeError, ValueError):
            errors.append({"code": "invalid_scene_window", "scene_index": index})
            continue
        if abs(start - cursor) > 0.03:
            errors.append({"code": "scene_window_gap_or_overlap", "scene_index": index, "delta_seconds": round(start - cursor, 4)})
        if end <= start or end > duration + 0.05:
            errors.append({"code": "invalid_scene_window_bounds", "scene_index": index, "start": start, "end": end})
        if index == 0 and abs(start) > 0.03:
            errors.append({"code": "scene_timeline_does_not_start_at_zero", "start": start})

        raw_word_index = window.get("word_start_index")
        if index > 0 and raw_word_index is not None:
            try:
                word_index = int(raw_word_index)
            except (TypeError, ValueError):
                word_index = -1
            expected_start = ordered_words.get(word_index)
            if expected_start is None:
                errors.append({"code": "scene_word_anchor_missing", "scene_index": index, "word_index": word_index})
            elif abs(start - expected_start) > max(0.03, float(tolerance_seconds)):
                errors.append({"code": "scene_cut_not_on_whisper_word", "scene_index": index, "expected": round(expected_start, 4), "actual": round(start, 4)})
        elif index > 0:
            warnings.append({"code": "scene_cut_has_no_whisper_word_index", "scene_index": index})

        normalized_windows.append({"scene_index": index, "start": start, "end": end})
        cursor = end

    if windows and abs(cursor - duration) > max(0.05, float(tolerance_seconds)):
        errors.append({"code": "scene_timeline_does_not_cover_audio", "scene_end": round(cursor, 4), "audio_duration": round(duration, 4)})
    if str(timing_source or "").startswith("tts_script_timing"):
        warnings.append({"code": "whisper_timing_fallback_used", "timing_source": timing_source})

    return {
        "status": "fail" if errors else ("warning" if warnings else "pass"),
        "timing_source": str(timing_source or "unknown"),
        "scene_count": len(windows),
        "script_token_count": len(source_tokens),
        "scene_token_count": len(scene_tokens),
        "word_timing_count": len(ordered_words),
        "audio_duration_seconds": round(duration, 3),
        "errors": errors,
        "warnings": warnings,
        "windows": normalized_windows,
    }


def validate_audio_edit_timeline(
    scenes: Sequence[Mapping[str, Any]],
    effects: Sequence[Mapping[str, Any]],
    *,
    min_effect_gap_seconds: float = 10.0,
    require_unique_families: bool = False,
    timing_tolerance_seconds: float = 0.06,
) -> Dict[str, Any]:
    """Check contiguous scene splits, cue files, Whisper anchors, and SFX variety.

    Transition cues are expected on a scene boundary. Other cues may be anchored
    to an exact phrase when the director supplies ``sound_anchor``.
    """
    errors = []
    warnings = []
    ordered_scenes = sorted(
        (scene for scene in scenes if isinstance(scene, Mapping)),
        key=lambda scene: float(scene.get("start") or 0.0),
    )
    for index, (left, right) in enumerate(zip(ordered_scenes, ordered_scenes[1:])):
        left_end = float(left.get("start") or 0.0) + float(left.get("duration") or 0.0)
        right_start = float(right.get("start") or 0.0)
        if abs(left_end - right_start) > 0.03:
            errors.append({"code": "scene_split_gap_or_overlap", "scene_index": index, "delta_seconds": round(right_start - left_end, 4)})

    cue_rows = []
    for cue_index, cue in enumerate(effects):
        if not isinstance(cue, Mapping):
            errors.append({"code": "invalid_sound_cue", "cue_index": cue_index})
            continue
        kind = str(cue.get("kind") or cue.get("sfx_id") or "")
        source = str(cue.get("src") or cue.get("path") or "")
        try:
            start = float(cue.get("start"))
        except (TypeError, ValueError):
            errors.append({"code": "invalid_sound_start", "cue_index": cue_index, "kind": kind})
            continue
        if not source or not Path(source).is_file():
            errors.append({"code": "missing_sound_asset", "cue_index": cue_index, "kind": kind, "path": source})
        owner_candidates = [
            scene for scene in ordered_scenes
            if float(scene.get("start") or 0.0) - timing_tolerance_seconds <= start <=
            float(scene.get("start") or 0.0) + float(scene.get("duration") or 0.0) + timing_tolerance_seconds
        ]
        owner = min(owner_candidates, key=lambda scene: abs(start - float(scene.get("start") or 0.0))) if owner_candidates else None
        if owner is None:
            errors.append({"code": "sound_outside_scene", "cue_index": cue_index, "kind": kind, "start": start})
        elif cue.get("sound_anchor"):
            expected = whisper_anchor_time(
                owner,
                str(cue.get("sound_anchor")),
                edge=str(cue.get("sound_anchor_edge") or "start"),
            )
            try:
                expected = None if expected is None else expected + float(cue.get("sound_anchor_offset_seconds") or 0.0)
            except (TypeError, ValueError):
                expected = None
            if expected is None:
                errors.append({"code": "whisper_sound_anchor_not_found", "cue_index": cue_index, "kind": kind})
            elif abs(start - expected) > timing_tolerance_seconds:
                errors.append({"code": "whisper_sound_anchor_mismatch", "cue_index": cue_index, "kind": kind, "expected": round(expected, 4), "actual": round(start, 4)})
        elif str(cue.get("sound_intent") or "").casefold() == "transition":
            boundary = float(owner.get("start") or 0.0) if owner else start
            if abs(start - boundary) > timing_tolerance_seconds:
                errors.append({"code": "transition_sound_not_on_scene_boundary", "cue_index": cue_index, "kind": kind, "expected": round(boundary, 4), "actual": round(start, 4)})
        else:
            warnings.append({"code": "sound_without_sync_anchor", "cue_index": cue_index, "kind": kind})
        cue_rows.append({"kind": kind, "family": sound_effect_family(kind), "start": start})

    cue_rows.sort(key=lambda row: row["start"])
    for previous, current in zip(cue_rows, cue_rows[1:]):
        gap = current["start"] - previous["start"]
        if gap < max(0.0, float(min_effect_gap_seconds)):
            errors.append({"code": "sound_effects_too_close", "previous": previous["kind"], "current": current["kind"], "gap_seconds": round(gap, 3)})
    families = [row["family"] for row in cue_rows]
    if require_unique_families and len(families) != len(set(families)):
        errors.append({"code": "similar_sound_effect_families", "families": families})

    return {
        "status": "fail" if errors else ("warning" if warnings else "pass"),
        "errors": errors,
        "warnings": warnings,
        "scene_count": len(ordered_scenes),
        "sound_effect_count": len(cue_rows),
        "sound_effect_families": families,
        "minimum_sound_gap_seconds": min(
            (round(cue_rows[i]["start"] - cue_rows[i - 1]["start"], 3) for i in range(1, len(cue_rows))),
            default=None,
        ),
        "whisper_anchored_count": sum(bool(cue.get("sound_anchor")) for cue in effects if isinstance(cue, Mapping)),
        "transition_boundary_count": sum(str(cue.get("sound_intent") or "").casefold() == "transition" for cue in effects if isinstance(cue, Mapping)),
    }
