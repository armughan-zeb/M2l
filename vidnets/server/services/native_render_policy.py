"""Native FFmpeg render policy shared by production and editor exports."""

from __future__ import annotations

import os
import copy
import math
import re
import threading
from .job_context import JobThread
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from .job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from .globe_map_service import hydrate_globe_maps
from .person_cutout_service import hydrate_person_cutouts
from .satellite_map_service import hydrate_satellite_maps


ROOT = Path(os.environ.get("VIDZEO_RESOURCE_ROOT") or Path(__file__).resolve().parents[2]).resolve()
PUBLIC = ROOT / "public"
_RENDER_SLOT = threading.Lock()


def _render_deadlines(*, continue_while_active: bool, now: float, timeout_seconds: float) -> tuple[float, float]:
    """Return the stage and safety deadlines for one Remotion export.

    Continuous mode is supervised by frame/output advancement and memory
    availability. It has no elapsed-time ceiling.
    """
    if continue_while_active:
        return math.inf, math.inf
    return now + timeout_seconds, now + 7200.0


def _stop_remotion_process(process) -> None:
    """Reap Node and its Chrome/FFmpeg children before another job starts."""
    from .cancellation import CancellationContext
    if process is not None:
        CancellationContext._terminate_process(process)


def _transient_remotion_failure(error: BaseException) -> bool:
    """Classify failures that are safe to retry with one render worker.

    The retry reuses the exact manifest, narration and media package. Source
    or React-plan defects stay terminal so production does not repeat an
    expensive render that cannot succeed without a code/data correction.
    """
    message = str(error or "").lower()
    hard = (
        "syntaxerror", "referenceerror", "cannot find module", "module not found",
        "edit plan", "invalid manifest", "unsupported component", "cardinality mismatch", "attempt limit reached",
    )
    if any(token in message for token in hard):
        return False
    transient = (
        "out of memory", "enomem", "memory pressure", "target closed", "page crashed",
        "browser disconnected", "browser has disconnected", "socket hang up", "epipe",
        "econnreset", "timed out", "timeout", "stalled", "exit code",
    )
    return any(token in message for token in transient)


def _enabled(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _semantic_preview_enabled(config: Mapping[str, Any], manifest: Mapping[str, Any]) -> bool:
    """Optional model review is opt-in; objective delivery checks always remain."""
    if "semantic_preview_gate" in config:
        return _enabled(config.get("semantic_preview_gate"))
    return bool(manifest.get("review_frames")) and _enabled(os.environ.get("VIDZEO_RENDER_VISION_REVIEW"))


def _perceptual_repeat_repair_findings(audit_errors: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Turn only verified pixel-repeat audit rows into a narrow media retry."""
    repeats = [
        row
        for error in audit_errors
        if error.get("code") == "unresolved_perceptual_repetition"
        for row in error.get("repeats") or []
        if isinstance(row, Mapping) and isinstance(row.get("scene_index"), int)
    ]
    indices = sorted({int(row["scene_index"]) for row in repeats})
    findings = []
    for index in indices:
        matches = sorted({
            int(row["matches_scene_index"])
            for row in repeats
            if row.get("scene_index") == index and isinstance(row.get("matches_scene_index"), int)
        })
        findings.append({
            "scene_index": index,
            "repair_kind": "media",
            "fallback_reason": "repeated_pixels",
            "issues": [
                "Fresh candidate repeats the actual imagery in another scene. Find a distinct, scene-specific view; do not return another file of the same subject/composition."
            ],
            "matching_scene_indices": matches,
        })
    return findings


def _grounded_graphic_fallback_feedback(findings: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Ask the pixel director for a grounded graphic when verified media is exhausted."""
    feedback = {}
    for finding in findings:
        index = finding.get('scene_index')
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            continue
        matches = sorted({
            value for value in (finding.get('matching_scene_indices') or [])
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        })
        repeated = finding.get('fallback_reason') == 'repeated_pixels'
        issue = (
            'The bounded media search did not find a distinct verified image; the current pixels repeat another scene. '
            if repeated else
            'The bounded source-media repair did not provide a verified shot that illustrates this beat. '
        ) + (
            'Recompose this beat as a subject-specific Remotion explanatory graphic instead of showing the repeated media. '
            if repeated else
            'Recompose this beat as a subject-specific Remotion explanatory graphic instead of unrelated or generic footage. '
        ) + (
            'Choose the best eligible component and visual grammar for this narration. Use only exact script or research facts, '
            'keep exact approved copy or omit text when none is approved, and make the design distinct from neighboring scenes.'
        )
        feedback[str(index)] = {
            'scene_index': index,
            'status': 'needs_revision',
            'repair_kind': 'design',
            'repeated_visual_graphic_fallback': True,
            'remotion_graphic_fallback': True,
            'fallback_reason': str(finding.get('fallback_reason') or 'repeated_pixels'),
            'matching_scene_indices': matches,
            'issues': [issue],
        }
    return feedback


def _is_actual_media_mismatch(row: Mapping[str, Any]) -> bool:
    """Only retrieve media when the reviewed source itself is wrong.

    Missing dates, tactical diagrams, labels, safe text positions, and visual
    treatments are design/copy repairs. Treating those as media failures starts
    irrelevant web searches and delays a repair that belongs in Remotion.
    """
    issues = [str(issue).strip() for issue in row.get('issues') or [] if str(issue).strip()]
    return bool(issues) and any(_MEDIA_MISMATCH_FINDING.search(issue) for issue in issues)


def _run_bounded_media_repair(repair_media, manifest, findings, timeout_seconds: float) -> bool:
    """Keep optional remote media repair from holding the renderer indefinitely."""
    timeout = max(0.1, min(30.0, float(timeout_seconds)))
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='bounded-creative-media-repair')
    candidate = copy.deepcopy(manifest)
    future = pool.submit(repair_media, candidate, copy.deepcopy(findings), timeout)
    try:
        repaired = bool(future.result(timeout=timeout))
        if repaired:
            manifest.clear()
            manifest.update(copy.deepcopy(candidate))
        return repaired
    except FutureTimeoutError:
        future.cancel()
        return False
    finally:
        # Remote providers may ignore cancellation; do not wait for them here.
        pool.shutdown(wait=False, cancel_futures=True)


def _preview_review_fallback(reason: str, *, strict: bool) -> dict[str, Any]:
    """Fail open to export when optional review expires, unless strict review was requested."""
    detail = str(reason or 'preview_report_unavailable').strip()
    if strict:
        raise TimeoutError(detail)
    return {
        'status': 'skipped',
        'fallback': 'direct_render',
        'reason': detail,
        'llm_review_certified': False,
    }


_MATERIAL_REVIEW_FINDING = re.compile(
    r"\b(?:wrong|unrelated|unsupported|inaccurate|unapproved|unauthorized|collision|overlap|"
    r"obscur\w*|illegible|unreadable|contrast|clipped|cut off|missing|generic|"
    r"placeholder|repetition|repeated|factual error|source mark|"
    r"watermark|burned[- ]in|not grounded|contradict\w*)\b",
    re.IGNORECASE,
)
_MEDIA_MISMATCH_FINDING = re.compile(
    r"\b(?:visual mismatch|subject mismatch|anachron\w*|historical mismatch|wrong (?:era|year|period|person|player|subject|event|action|image|photo|footage|clip|media)|"
    r"unrelated (?:person|player|subject|event|image|photo|footage|clip)|does not show|doesn't show|"
    r"depicts? the wrong|shows? .{0,120}\bbut (?:the )?narration|repeats? (?:the )?selected (?:image|pixels|footage)|"
    r"duplicate (?:image|photo|footage|clip)|identity conflict|contradict\w*\s+(?:the\s+)?(?:narration|script))\b",
    re.IGNORECASE,
)
_MATERIAL_REVIEW_FINDING = re.compile(
    _MATERIAL_REVIEW_FINDING.pattern + r"|\b(?:subject|visual intent|data|number|score|script|fact|"
    r"geographic|source|media|identity|content|claim|map)\s+mismatch\b",
    re.IGNORECASE,
)
_MINOR_STYLE_REVIEW_FINDING = re.compile(
    r"\b(?:minor|slight|subtle)\s+(?:aesthetic|stylistic|motion|transition|spacing|accent|"
    r"visual polish|visual variation|art.direction|composition)\b|"
    r"\b(?:could benefit from|could use|would benefit from)\s+(?:(?:a little|slightly|a subtle)\s+)?"
    r"(?:more\s+)?(?:polish|motion|visual variation|aesthetic flourish|accent continuity|"
    r"stylistic detail|transition flourish)\b|"
    r"\b(?:optional|nonessential)\s+(?:polish|accent|motion detail|transition detail)\b",
    re.IGNORECASE,
)


def _defer_minor_creative_findings(
    creative: dict[str, Any], production_audit: Mapping[str, Any], pixel_audit: Mapping[str, Any],
    *, manifest: Mapping[str, Any] | None = None, openrouter_api_key: str = "",
) -> list[dict[str, Any]]:
    """After bounded revisions, defer only explicit style polish findings.

    This is a controlled delivery policy, not a quality-pass override: local
    frame/production audits must pass, and ambiguous or factual/technical
    findings continue to block. Deferred notes remain in the manifest.
    """
    if (production_audit.get('status') != 'pass'
            or pixel_audit.get('status') != 'pass'):
        return []
    unresolved = set(creative.get('unresolved_scene_indices') or [])
    if not unresolved:
        return []
    rows = {row.get('scene_index'): row for row in creative.get('scenes') or []
            if isinstance(row, Mapping)}
    potential = []
    scene_list = (manifest or {}).get('scenes') or []
    for index in sorted(unresolved):
        row = rows.get(index)
        if not isinstance(row, Mapping):
            continue
        issues = [str(issue).strip() for issue in row.get('issues') or [] if str(issue).strip()]
        if (issues and str(row.get('repair_kind') or '').casefold() != 'media'
                and not row.get('media_repair_required')
                and not any(_MATERIAL_REVIEW_FINDING.search(issue) for issue in issues)):
            scene = scene_list[index] if isinstance(index, int) and 0 <= index < len(scene_list) else {}
            primary = scene.get('primary_visual') if isinstance(scene, Mapping) else {}
            potential.append({
                'scene_index': index,
                'issues': issues,
                'scene_context': ' | '.join(filter(None, [
                    str((scene or {}).get('narration') or '')[:220],
                    str((scene or {}).get('visual_intent') or '')[:220],
                    str((primary or {}).get('type') or '')[:80],
                ])),
            })
    jev_decisions = {}
    if openrouter_api_key and potential:
        try:
            from .jev_decision_service import classify_review_findings

            # Jev accepts up to eight typed questions per request. Keep total
            # request time bounded; a Jev outage falls back to the explicit
            # local allowlist and never weakens any blocking check.
            for offset in range(0, min(len(potential), 24), 8):
                batch = potential[offset:offset + 8]
                jev_decisions.update(classify_review_findings(
                    batch, openrouter_api_key, timeout_seconds=2.5,
                ))
        except Exception:
            jev_decisions = {}
    deferred = []
    blocked = []
    for index in sorted(unresolved):
        row = rows.get(index)
        if not isinstance(row, dict):
            blocked.append(index)
            continue
        issues = [str(issue).strip() for issue in row.get('issues') or [] if str(issue).strip()]
        repair_kind = str(row.get('repair_kind') or '').casefold()
        decision = jev_decisions.get(index)
        locally_minor = bool(issues) and all(
            _MINOR_STYLE_REVIEW_FINDING.search(issue) for issue in issues
        )
        # Any received but low-confidence/non-minor Jev result stays blocked.
        # The narrow lexical allowlist is only a no-key/provider-outage route.
        eligible_minor = bool(
            decision.get('eligible_minor_polish') if decision is not None else locally_minor
        )
        if (not issues or repair_kind == 'media' or row.get('media_repair_required')
                or any(_MATERIAL_REVIEW_FINDING.search(issue) for issue in issues)
                or not eligible_minor):
            blocked.append(index)
            continue
        row['status'] = 'deferred_minor'
        row['blocking'] = False
        row['deferral_reason'] = 'bounded_revisions_exhausted; local production and pixel audits passed'
        if decision:
            row['jev_triage'] = decision
        deferred.append({'scene_index': index, 'issues': issues,
                         'triage': decision or {'method': 'explicit_minor_local_rule'}})
    creative['unresolved_scene_indices'] = blocked
    if deferred:
        creative['deferred_minor_findings'] = deferred
        if not blocked and not creative.get('unavailable_scene_indices'):
            creative['status'] = 'pass_with_notes'
    return deferred


def _nonmaterial_creative_advisories(
    creative: Mapping[str, Any], production_audit: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return exhausted review notes that are safe to keep as delivery advisories.

    Non-strict delivery already treats the local pixel review as advisory. Do
    not let the creative-review gate silently become stricter than that policy:
    after bounded redesign rounds, export only when production checks pass and
    every remaining note is present, non-media, and clearly non-material.
    Preserve every note and keep the LLM review uncertified in the manifest.
    """
    if production_audit.get('status') != 'pass':
        return []
    unresolved = sorted({
        index for index in (creative.get('unresolved_scene_indices') or [])
        if isinstance(index, int) and not isinstance(index, bool) and index >= 0
    })
    if not unresolved:
        return []
    rows = {
        row.get('scene_index'): row for row in creative.get('scenes') or []
        if isinstance(row, Mapping)
    }
    advisories = []
    for index in unresolved:
        row = rows.get(index)
        if not isinstance(row, Mapping):
            return []
        issues = [str(issue).strip() for issue in row.get('issues') or [] if str(issue).strip()]
        if (not issues or str(row.get('repair_kind') or '').casefold() == 'media'
                or row.get('media_repair_required')
                or any(_MATERIAL_REVIEW_FINDING.search(issue) for issue in issues)):
            return []
        advisories.append({'scene_index': index, 'issues': issues})
    return advisories


def _frame_align_remotion_manifest(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """Convert the scene timeline to contiguous integer frames without drift.

    Rounding each scene duration independently adds up fractional-frame error
    across the edit. Instead, round each absolute narration boundary once and
    derive each scene's length from adjacent boundaries. The final boundary is
    pinned to the narration duration, so all scenes together land on the exact
    audio endpoint at the chosen frame rate.
    """
    aligned = dict(manifest or {})
    try:
        fps = max(1, int(round(float(aligned.get("fps") or 30))))
    except (TypeError, ValueError):
        fps = 30
    scenes = list(aligned.get("scenes") or [])
    try:
        declared_duration = max(0.0, float(aligned.get("duration") or 0.0))
    except (TypeError, ValueError):
        declared_duration = 0.0

    absolute_cursor = 0.0
    previous_end_frame = 0
    prepared = []
    try:
        strict_timeline = int(aligned.get("edit_plan_schema_version") or 1) >= 4
    except (TypeError, ValueError):
        strict_timeline = False
    for index, value in enumerate(scenes):
        scene = dict(value) if isinstance(value, Mapping) else {}
        try:
            duration = max(0.0, float(scene.get("duration") or 0.0))
        except (TypeError, ValueError):
            duration = 0.0
        raw_start = scene.get("start")
        if raw_start is None and scene.get("from") is not None:
            try:
                raw_start = float(scene.get("from") or 0.0) / fps
            except (TypeError, ValueError):
                raw_start = None
        try:
            start_seconds = max(0.0, float(raw_start)) if raw_start is not None else absolute_cursor
        except (TypeError, ValueError):
            start_seconds = absolute_cursor

        # A v4 director plan promises an audio-aligned, gap-free narration
        # timeline. Fail visibly if that promise was broken instead of silently
        # shifting later scenes during render.
        drift_frames = abs(start_seconds - absolute_cursor) * fps
        if index and strict_timeline and drift_frames > 1.01:
            raise ValueError(
                f"Remotion scene {index + 1} starts {drift_frames:.2f} frames away "
                "from the previous narration beat boundary."
            )

        start_frame = int(round(start_seconds * fps)) if index == 0 else previous_end_frame
        end_frame = int(round((start_seconds + duration) * fps))
        if index == len(scenes) - 1 and declared_duration > 0.0:
            end_frame = int(round(declared_duration * fps))
        end_frame = max(start_frame + 1, end_frame)
        scene["from"] = start_frame
        scene["duration_in_frames"] = end_frame - start_frame
        prepared.append(scene)
        previous_end_frame = end_frame
        absolute_cursor = start_seconds + duration

    if scenes:
        total_frames = previous_end_frame
    else:
        total_frames = int(round(declared_duration * fps))
    aligned["scenes"] = prepared
    aligned["fps"] = fps
    aligned["duration_in_frames"] = total_frames
    aligned["duration"] = round(total_frames / fps, 6)
    return aligned


def native_render_enabled(config: Optional[Mapping[str, Any]] = None) -> bool:
    config = dict(config or {})
    if "semantic_render_enabled" in config:
        return bool(config.get("semantic_render_enabled"))
    return True


def validate_native_backend(config: Optional[Mapping[str, Any]] = None) -> str:
    config = dict(config or {})
    requested = str(
        config.get("render_backend")
        or os.environ.get("VIDZEO_RENDER_BACKEND", "remotion")
    ).strip().lower()
    if requested in {"remotion", "react"}:
        return "remotion"
    if requested in {"", "ffmpeg", "native"}:
        return "ffmpeg"
    raise RuntimeError(
        f"Unsupported render backend '{requested}'. Supported: remotion, ffmpeg."
    )


def semantic_render_enabled(config: Optional[Mapping[str, Any]] = None) -> bool:
    """Compatibility name for the quality-aware semantic renderer."""
    return native_render_enabled(config)


def semantic_render_required(config: Optional[Mapping[str, Any]] = None) -> bool:
    """Return whether a semantic job may fall back to legacy FFmpeg."""
    config = dict(config or {})
    if "semantic_render_required" in config:
        return bool(config.get("semantic_render_required"))
    if "allow_legacy_render_fallback" in config:
        return not bool(config.get("allow_legacy_render_fallback"))
    if _enabled(os.environ.get("VIDZEO_ALLOW_LEGACY_RENDER_FALLBACK", "0")):
        return False
    return semantic_render_enabled(config) and _enabled(
        os.environ.get("VIDZEO_SEMANTIC_RENDER_REQUIRED", "1")
    )


def assert_semantic_edit_quality(
    manifest: Mapping[str, Any],
    config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    config = dict(config or {})
    audit = ((manifest.get("metrics") or {}).get("variation_audit") or {})
    if not isinstance(audit, Mapping):
        raise RuntimeError("Semantic manifest has no variation audit.")
    strict = config.get("semantic_variation_required")
    if strict is None:
        strict = _enabled(os.environ.get("VIDZEO_SEMANTIC_VARIATION_REQUIRED", "1"))
    if strict and str(audit.get("status") or "") != "pass":
        warnings = ", ".join(str(item) for item in audit.get("warnings") or []) or "unknown repetition"
        raise RuntimeError(f"Semantic variation gate rejected the edit: {warnings}")
    return dict(audit)


def procedural_sound_library() -> Dict[str, Dict[str, str]]:
    base = PUBLIC / "sound-effects" / "vidzeo"
    filenames = {
        "whoosh": "whoosh.wav", "impact": "impact.wav", "paper_flip": "paper_flip.wav",
        "typewriter": "typewriter.wav", "map_pulse": "map_pulse.wav", "riser": "riser.wav",
        "ui_tick": "ui_tick.wav", "water_pulse": "water_pulse.wav", "room_tone": "room_tone.wav",
        "impact_soft": "impact_soft.wav", "sub_boom": "sub_boom.wav", "reverse_swell": "reverse_swell.wav",
        "paper_rustle": "paper_rustle.wav", "photo_slide": "photo_slide.wav", "camera_shutter": "camera_shutter.wav",
        "pencil_mark": "pencil_mark.wav", "decision_tick": "decision_tick.wav", "clock_tick": "clock_tick.wav",
        "data_tick": "data_tick.wav", "radio_static": "radio_static.wav", "whoosh_soft": "whoosh_soft.wav",
        "paper_tear": "paper_tear.wav", "calendar_flip": "calendar_flip.wav", "marker_scratch": "marker_scratch.wav",
        "stamp_hit": "stamp_hit.wav", "typing_burst": "typing_burst.wav", "technical_tick": "technical_tick.wav",
        "signal_ping": "signal_ping.wav", "route_ping": "route_ping.wav", "lens_click": "lens_click.wav",
        "vinyl_crackle": "vinyl_crackle.wav", "low_rumble": "low_rumble.wav", "suspense_pulse": "suspense_pulse.wav",
        "whoosh_air": "whoosh_air.wav", "whoosh_dark": "whoosh_dark.wav", "whoosh_snap": "whoosh_snap.wav",
        "impact_metal": "impact_metal.wav", "impact_wood": "impact_wood.wav", "impact_glass": "impact_glass.wav",
        "bass_drop": "bass_drop.wav", "tension_string": "tension_string.wav", "heartbeat": "heartbeat.wav",
        "reveal_chime": "reveal_chime.wav", "news_sting": "news_sting.wav", "telegraph": "telegraph.wav",
        "keyboard_single": "keyboard_single.wav", "mouse_click": "mouse_click.wav",
        "notification_up": "notification_up.wav", "notification_down": "notification_down.wav",
        "coin_tick": "coin_tick.wav", "cash_counter": "cash_counter.wav", "camera_wind": "camera_wind.wav",
        "projector": "projector.wav", "film_splice": "film_splice.wav", "page_stack": "page_stack.wav",
        "map_draw": "map_draw.wav", "sonar": "sonar.wav", "archive_drone": "archive_drone.wav",
        "wind_room": "wind_room.wav", "crowd_murmur": "crowd_murmur.wav", "transition_suck": "transition_suck.wav",
    }
    library = {
        key: {
            "src": str(base / filename), "path": str(base / filename),
            "license": "Vidzeo procedural / generated",
            "rights_status": "generated", "publisher": "Vidzeo local procedural library",
        }
        for key, filename in filenames.items()
        if (base / filename).is_file()
    }
    # Editorial accents live in a separate curated folder, but the semantic
    # planner consumes one library. Previously these cues were never exposed,
    # so text-card and transition sounds were planned as missing and dropped.
    library.update(editorial_sound_library())
    from .editorial_director.sound_events import measure_sound
    for item in library.values():
        source = str(item.get('src') or item.get('path') or '')
        if source.startswith('/sound-effects/'):
            source = str(PUBLIC / source.lstrip('/'))
        item.update(measure_sound(source))
    return library


def editorial_sound_library() -> Dict[str, Dict[str, str]]:
    """A curated, editorial-focused subset of the procedural SFX library.

    These are deliberately tight, mix-friendly cues designed to fire on text-card
    entrances, transitions, fades, and other editing grammar — not ambient
    wallpaper. Each clip is short (0.05–0.65s) and pre-leveled for placement
    under dialogue. Files live under public/sound-effects/edit/.
    """
    base = PUBLIC / "sound-effects" / "edit"
    filenames = {
        # Transitions / movement
        "edit_whoosh_light": "edit_whoosh_light.wav",
        "edit_whoosh_deep": "edit_whoosh_deep.wav",
        "edit_whoosh_sucked": "edit_whoosh_sucked.wav",
        "edit_whoosh_air": "edit_whoosh_air.wav",
        # Paper / archive accents
        "edit_paper_shuffle": "edit_paper_shuffle.wav",
        "edit_paper_tear_subtle": "edit_paper_tear_subtle.wav",
        "edit_staple": "edit_staple.wav",
        "edit_book_close": "edit_book_close.wav",
        # Typographic
        "edit_type_click": "edit_type_click.wav",
        "edit_type_burst_short": "edit_type_burst_short.wav",
        "edit_cursor_blink": "edit_cursor_blink.wav",
        # Hits / stings
        "edit_hit_dry": "edit_hit_dry.wav",
        "edit_hit_tight": "edit_hit_tight.wav",
        "edit_hit_boomed": "edit_hit_boomed.wav",
        "edit_sting_minor": "edit_sting_minor.wav",
        "edit_sting_major": "edit_sting_major.wav",
        # Reveals
        "edit_reveal_soft": "edit_reveal_soft.wav",
        "edit_reveal_chime": "edit_reveal_chime.wav",
        "edit_shutter_snap": "edit_shutter_snap.wav",
        "edit_flash_bulb": "edit_flash_bulb.wav",
        # Room / tape / device
        "edit_room_shift": "edit_room_shift.wav",
        "edit_tape_stop": "edit_tape_stop.wav",
        "edit_vinyl_pop": "edit_vinyl_pop.wav",
        # Edit-grammar accents
        "edit_zoom_whirr": "edit_zoom_whirr.wav",
        "edit_marker_underline": "edit_marker_underline.wav",
        "edit_check_tick": "edit_check_tick.wav",
        "edit_slider_move": "edit_slider_move.wav",
    }
    return {
        key: {
            "src": str(base / filename), "path": str(base / filename),
            "license": "Vidzeo procedural / generated (editorial)",
            "rights_status": "generated", "publisher": "Vidzeo editorial SFX",
        }
        for key, filename in filenames.items()
        if (base / filename).is_file()
    }


def normalize_remotion_caption_settings(raw: Any) -> Dict[str, Any]:
    """Clamp the app caption options to the bundled Remotion design contract."""
    captions = raw if isinstance(raw, Mapping) else {}
    style = str(captions.get("style") or "documentary").strip().lower()
    font = str(captions.get("font") or "DM Sans").strip()
    position = str(captions.get("position") or "auto").strip().lower()
    mode = str(captions.get("mode") or "word").strip().lower()
    return {
        "enabled": bool(captions.get("enabled", False)),
        "style": style if style in {"documentary", "impact", "clean", "archive"} else "documentary",
        "font": font if font in {"DM Sans", "Archivo Black", "Barlow Condensed", "Plus Jakarta Sans", "JetBrains Mono"} else "DM Sans",
        "position": position if position in {"auto", "top", "middle", "bottom"} else "auto",
        "mode": mode if mode in {"word", "phrase", "karaoke", "smart_emphasis"} else "word",
        "animation": str(captions.get("animation") or "Pop"),
    }


def render_semantic_documentary_remotion(
    manifest: Mapping[str, Any],
    output_path: str | Path,
    config: Optional[Mapping[str, Any]] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    cancellation=None,
    memory_snapshot: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Render a semantic documentary using Remotion via headless Chrome.

    100% local, zero external cloud dependencies.
    """
    import json
    import re
    import shutil
    import subprocess
    import tempfile
    import time
    import threading
    from queue import Empty, Queue

    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    render_log_path = output.parent / f"{output.stem}.remotion-render.log"
    started = time.monotonic()

    # Completed-master reuse is handled by the digest-bound package checkpoint
    # and the renderer export receipt. Nearby videos with matching duration
    # are not proof that this manifest/narration has already been rendered.
    configured_node = str(os.environ.get("VIDZEO_NODE_PATH") or "").strip()
    if configured_node and Path(configured_node).is_file():
        node_bin = configured_node
    elif _enabled(os.environ.get("VIDZEO_PACKAGED")):
        raise FileNotFoundError(
            "The bundled Node.js runtime required by Remotion is missing. "
            "Repair or reinstall Vidnets Studio before rendering."
        )
    else:
        node_bin = shutil.which("node") or "node"
    script_path = ROOT / "scripts" / "render-remotion-manifest.js"
    if not script_path.is_file():
        raise FileNotFoundError(f"Remotion render script not found at {script_path}")

    def _json_default(obj: Any) -> Any:
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        if isinstance(obj, Mapping):
            return dict(obj)
        if hasattr(obj, "__dict__"):
            return obj.__dict__
        if isinstance(obj, Path):
            return str(obj)
        return str(obj)

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
        # ── Pre-processing: sanitize manifest before handing to Remotion ─────
        # Work on a shallow copy so we don't mutate the caller's dict.
        manifest_out = dict(manifest)

        # 1. Remotion is the sole owner of visible captions. Normalize the
        #    user-facing caption contract here so every render path uses the
        #    same bundled fonts and style choices.
        manifest_out["captions"] = normalize_remotion_caption_settings(manifest_out.get("captions"))

        # 2. Convert absolute narration boundaries to a contiguous frame
        #    timeline. Independent per-scene rounding can accumulate drift.
        manifest_out = _frame_align_remotion_manifest(manifest_out)
        from .render_media_timing import repair_short_video_slots
        repair_short_video_slots(manifest_out)
        from .recovery_contract import recover_manifest
        recover_manifest(manifest_out)
        # A saved recovery plan may predate a registered physical grammar.
        # Upgrade only grounded graphic recoveries, preserving real media and
        # never inventing measured data or a model-specific blueprint.
        from .editorial_director.director import _build_diagram_props
        for recovery_scene in manifest_out.get('scenes') or []:
            recovery_media = recovery_scene.get('media') or {}
            if not (recovery_media.get('graphic_fallback') or recovery_media.get('is_graphic_fallback') or recovery_scene.get('graphic_fallback')):
                continue
            recovered_diagram = _build_diagram_props('cause_and_effect_flow', str(recovery_scene.get('narration') or ''), None)
            if recovered_diagram.get('variant') == 'energy_conversion':
                recovery_scene.update(primary_visual={'type': 'Diagram', 'variant': 'energy_conversion', 'props': recovered_diagram},
                    visual_role='primary_explanation', frame_mode='diagram')
                recovery_scene['template_recipe'] = {}
                recovery_scene['template_id'] = 'cause-effect'
        if not _enabled(os.environ.get("VIDZEO_DISABLE_PERSON_CUTOUTS")):
            manifest_out = hydrate_person_cutouts(manifest_out, output.parent)
        if not _enabled(os.environ.get("VIDZEO_DISABLE_SATELLITE_MAPS")):
            manifest_out = hydrate_satellite_maps(manifest_out, output.parent)
        manifest_out = hydrate_globe_maps(manifest_out, output.parent, ROOT)
        _video_extensions = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".wmv"}
        for scene_dict in manifest_out.get("scenes") or []:
            scene_dict.pop("captions", None)
            # 3. Media type mismatch correction: if a scene has a video source
            #    (.mp4 YouTube clip) but primary_visual.type is SmartImage,
            #    correct it to SmartVideo. Without this, Remotion tries to
            #    render the .mp4 with <Img> and crashes with EncodingError.
            media_dict = scene_dict.get("media")
            primary_dict = scene_dict.get("primary_visual")
            if isinstance(media_dict, dict) and isinstance(primary_dict, dict):
                pv_type = str(primary_dict.get("type") or "").strip()
                media_src = str(media_dict.get("src") or media_dict.get("path") or "")
                media_type_field = str(media_dict.get("type") or "").strip().lower()
                src_ext = Path(media_src.split("?", 1)[0]).suffix.lower() if media_src else ""
                src_is_video = media_type_field in {"video", "video/mp4", "video/webm"} or src_ext in _video_extensions
                if pv_type == "SmartImage" and src_is_video:
                    primary_dict["type"] = "SmartVideo"
                    primary_dict["variant"] = "full_bleed"
                    if media_type_field not in {"video", "video/mp4", "video/webm"}:
                        media_dict["type"] = "video"

            # 4. Aspect ratio & safe-framing detection for still imagery:
            #    Square (1:1), 4:3, and portrait (<1.35) images must not be cropped
            #    or stretched into a 16:9 canvas. Stamp width, height, aspect_ratio,
            #    safe_framing=True, and fit="contain" so Remotion renders them with
            #    the cinematic contained presentation over matching blurred backdrop.
            test_src = ""
            if isinstance(media_dict, dict):
                test_src = str(media_dict.get("src") or media_dict.get("path") or "")
            if not test_src and isinstance(primary_dict, dict) and isinstance(primary_dict.get("asset"), dict):
                test_src = str(primary_dict["asset"].get("src") or primary_dict["asset"].get("path") or "")
            if not test_src and isinstance(scene_dict.get("background"), dict):
                test_src = str(scene_dict["background"].get("src") or scene_dict["background"].get("path") or "")

            test_ext = Path(test_src.split("?", 1)[0]).suffix.lower() if test_src else ""
            test_is_video = (
                (str((media_dict or {}).get("type") or "").strip().lower() in {"video", "video/mp4", "video/webm"} if isinstance(media_dict, dict) else False)
                or test_ext in _video_extensions
            )

            if test_src and not test_is_video and os.path.isfile(test_src):
                try:
                    from PIL import Image as _PILImage
                    with _PILImage.open(test_src) as _im:
                        _w, _h = _im.size
                        if _w > 0 and _h > 0:
                            _ar = round(_w / _h, 4)
                            _is_square_or_portrait = _ar < 1.35

                            def _tag_asset_dict(d: dict):
                                # Alternates can have different aspect ratios
                                # from the scene's first photograph. Never copy
                                # its dimensions onto every timed media shot.
                                asset_src = str(d.get("src") or d.get("path") or d.get("asset_path") or test_src)
                                if Path(asset_src.split("?", 1)[0]).suffix.lower() in _video_extensions:
                                    return
                                width, height = _w, _h
                                if asset_src != test_src:
                                    if not os.path.isfile(asset_src):
                                        return
                                    try:
                                        with _PILImage.open(asset_src) as alternate_image:
                                            width, height = alternate_image.size
                                    except Exception:
                                        return
                                if width <= 0 or height <= 0:
                                    return
                                aspect = round(width / height, 4)
                                d["width"] = width
                                d["height"] = height
                                d["aspect_ratio"] = aspect
                                if aspect < 1.35:
                                    d["safe_framing"] = True
                                    d["fit"] = "contain"

                            if isinstance(media_dict, dict):
                                _tag_asset_dict(media_dict)
                                for _shot in media_dict.get("shots") or []:
                                    if isinstance(_shot, dict):
                                        _tag_asset_dict(_shot)
                            if isinstance(scene_dict.get("background"), dict):
                                _tag_asset_dict(scene_dict["background"])
                            if isinstance(primary_dict, dict):
                                if not isinstance(primary_dict.get("props"), dict):
                                    primary_dict["props"] = {}
                                _tag_asset_dict(primary_dict["props"])
                                if isinstance(primary_dict.get("asset"), dict):
                                    _tag_asset_dict(primary_dict["asset"])
                except Exception:
                    pass
        # ─────────────────────────────────────────────────────────────────────

        if 'production_audit' in manifest_out:
            from .editorial_director.production_contract import compile_production_timeline
            compile_production_timeline(manifest_out)
            if manifest_out['production_audit']['status'] == 'fail':
                raise ValueError(f"Unresolved production requirements: {manifest_out['production_audit']['errors']}")
        from .rendered_graphics_audit import update_graphic_audit
        update_graphic_audit(manifest_out)
        json.dump(manifest_out, tf, indent=2, ensure_ascii=False, default=_json_default)
        temp_manifest_path = tf.name

    try:
        manifest_dump_path = output.with_suffix(".manifest.json")
        with open(manifest_dump_path, "w", encoding="utf-8") as df:
            json.dump(manifest_out, df, indent=2, ensure_ascii=False, default=_json_default)
    except Exception:
        pass

    try:
        cmd = [node_bin, "--max-old-space-size=4096", str(script_path), temp_manifest_path, str(output)]
        env = os.environ.copy()
        cfg = dict(config or {})
        memory_snapshot = dict(memory_snapshot or {})
        available_memory_mb = int(memory_snapshot.get("available_mb") or 0)
        if available_memory_mb > 0:
            env["VIDZEO_REMOTION_AVAILABLE_RAM_MB"] = str(available_memory_mb)
        if "scale" in cfg:
            env["VIDZEO_REMOTION_SCALE"] = str(cfg["scale"])
        else:
            quality = str(cfg.get("semantic_render_quality") or cfg.get("quality") or "standard").strip().lower()
            preview_only = _enabled(cfg.get("preview_only") or cfg.get("render_preview"))
            default_scale = (0.55 if quality == "fast" else (0.75 if quality == "medium" else 1.0)) if preview_only else 1.0
            env["VIDZEO_REMOTION_SCALE"] = str(max(0.35, min(1.5, float(cfg.get("semantic_render_scale") or default_scale))))
        # Flask's explicit Fast Render switch changes only x264's encoder
        # preset. Keep Remotion's 1080p scale and CRF 18 quality target intact;
        # faster presets trade file size for encode time, not source-image
        # resolution or the motion/compositing pass. A per-job preset remains
        # available for operators who need a different speed/size balance.
        requested_preset = str(cfg.get("semantic_render_x264_preset") or "").strip().lower()
        if requested_preset:
            selected_x264_preset = requested_preset
        elif _enabled(cfg.get("semantic_fast_render")) or _enabled(cfg.get("fast_mode")):
            selected_x264_preset = str(
                env.get("VIDZEO_REMOTION_FAST_X264_PRESET") or "ultrafast"
            ).strip().lower()
        else:
            selected_x264_preset = str(
                env.get("VIDZEO_REMOTION_X264_PRESET") or "veryfast"
            ).strip().lower()
        if selected_x264_preset not in {
            "ultrafast", "superfast", "veryfast", "faster", "fast",
            "medium", "slow", "slower", "veryslow", "placebo",
        }:
            selected_x264_preset = "veryfast"
        env["VIDZEO_REMOTION_X264_PRESET"] = selected_x264_preset
        requested_crf = cfg.get("semantic_render_crf")
        try:
            selected_crf = int(
                requested_crf
                if requested_crf not in (None, "")
                else env.get("VIDZEO_REMOTION_CRF", 18)
            )
        except (TypeError, ValueError):
            selected_crf = 18
        if not 0 <= selected_crf <= 51:
            selected_crf = 18
        env["VIDZEO_REMOTION_CRF"] = str(selected_crf)
        # Resource preflight chose the fastest safe count for the current RAM,
        # CPU and frame size. The Node renderer applies its own fresh RAM cap.
        # NOTE: the M2l worker sends its request as `concurrency` AND
        # `semantic_render_concurrency` (adapters/vidnets.js buildRequest,
        # MUSE_RENDER_CONCURRENCY, default 2). The server layer only
        # forwards semantic_render_concurrency into the render config, so
        # read it here too — otherwise the requested count never reaches
        # the Node script and the render silently uses its own default.
        user_concurrency = (
            cfg.get("concurrency")
            or cfg.get("remotion_concurrency")
            or cfg.get("semantic_render_concurrency")
            or os.environ.get("VIDZEO_REMOTION_CONCURRENCY")
        )
        if user_concurrency:
            env["VIDZEO_REMOTION_CONCURRENCY"] = str(user_concurrency)
        else:
            # Automatic exports re-evaluate headroom between saved sections;
            # do not pin them to the worker count measured before bundling.
            env.pop("VIDZEO_REMOTION_CONCURRENCY", None)
        env["VIDZEO_REMOTION_FRAME_BUFFERS"] = str(int(memory_snapshot.get("frame_buffers") or 1))
        env["VIDZEO_REMOTION_WORKER_OVERHEAD_MB"] = str(int(memory_snapshot.get("worker_overhead_mb") or 64))
        try:
            timeout_seconds = float(
                cfg.get("semantic_render_timeout_seconds")
                or cfg.get("semantic_render_budget_seconds")
                or os.environ.get("VIDZEO_REMOTION_RENDER_TIMEOUT_SECONDS", "7200")
                or 7200
            )
        except (TypeError, ValueError):
            timeout_seconds = 7200.0
        timeout_seconds = max(30.0, min(7200.0, timeout_seconds if math.isfinite(timeout_seconds) else 7200.0))
        continue_render = _enabled(cfg.get('continue_render_while_active', os.environ.get('VIDZEO_RENDER_CONTINUE_WHILE_ACTIVE', '1')))
        env['VIDZEO_RENDER_CONTINUE_WHILE_ACTIVE'] = '1' if continue_render else '0'

        process_options = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "cwd": str(ROOT),
            "env": env,
        }
        if os.name == "nt" and _enabled(env.get("VIDZEO_RENDER_PRIORITY_ENABLED", "1")):
            process_options["creationflags"] = getattr(subprocess, "ABOVE_NORMAL_PRIORITY_CLASS", 0x00008000)
        elif os.name != 'nt':
            process_options['start_new_session'] = True
        preview_enabled = _semantic_preview_enabled(cfg, manifest_out)
        if _enabled(preview_enabled):
            from .editorial_director.visual_critic import audit_frames, intentional_transition_positions
            from .editorial_director.pixel_revision import review_and_revise
            from .editorial_director.review_context import review_budget
            preview_report_path = output.with_suffix('.preview.json')
            preview_cmd = [node_bin, "--max-old-space-size=4096", str(script_path), temp_manifest_path, str(preview_report_path), '--review-frames']
            frame_count = len(manifest_out.get('review_frames') or [])
            scene_waves = max(1, math.ceil(len(manifest_out.get('scenes') or []) / 4))
            # Every scene is reviewed in parallel waves, with two repair passes
            # and one final sign-off. Cap this whole phase at five minutes so a
            # slow provider cannot turn a short film into a multi-minute stall.
            review_policy = review_budget(cfg, len(manifest_out.get('scenes') or []), frame_count)
            # This allowance includes the final render. Review cannot spend
            # it all and leave the job's frame export without headroom.
            preview_budget = min(review_policy['total_seconds'], max(10.0, timeout_seconds * .30))
            preview_deadline = time.monotonic() + min(preview_budget, timeout_seconds)
            creative_history = []
            from .editorial_director.creative_review_cache import load_pass_cache, save_pass_cache
            creative_cache_path = output.with_suffix('.creative-pass-cache.json')
            creative_pass_cache = load_pass_cache(creative_cache_path)
            media_repair_used = False
            archive = None
            vision_key = str(cfg.get('openrouter_api_key') or os.environ.get('OPENROUTER_API_KEY') or os.environ.get('VIDZEO_OPENROUTER_API_KEY') or '')
            client_factory = cfg.get('_creative_review_client_factory')
            # Muse inference is compulsory for external_agent jobs: the
            # creative-review pixel audit must never call Qwen directly when
            # this render belongs to a Muse-inference job. (The audit is
            # advisory — when the factory is unset the render proceeds
            # without it — so skipping it here loses nothing.)
            from .job_context import current_job as _current_job
            _is_external_agent = (
                str(cfg.get('llm_provider') or '') == 'external_agent'
                or str(_current_job().get('llm_provider') or '') == 'external_agent'
            )
            if not callable(client_factory) and vision_key and not _is_external_agent:
                from .visual_intelligence.vision_client import QwenVisionClient
                client_factory = lambda remaining: QwenVisionClient(api_key=vision_key, max_attempts=1,
                timeout_seconds=max(1,min(18,remaining)),fast_mode=True)
            last_review_round = review_policy['revision_rounds']
            preview_failed = False
            preview_failure_reason = ''
            review = None
            creative = None
            for revision_round in range(last_review_round + 1):
                if time.monotonic() >= preview_deadline:
                    preview_failed = True
                    preview_failure_reason = 'Preview review deadline exhausted before final assessment'
                    break
                # Delete a stale report so an unsuccessful render cannot reuse
                # the prior round's pixels or falsely certify a changed scene.
                update_graphic_audit(manifest_out)
                with open(temp_manifest_path, 'w', encoding='utf-8') as saved:
                    json.dump(manifest_out, saved, indent=2, ensure_ascii=False, default=_json_default)
                preview_report_path.unlink(missing_ok=True)
                preview = subprocess.Popen(preview_cmd, **process_options)
                if cancellation is not None:
                    cancellation.register_process(preview)
                try:
                    while True:
                        if cancellation is not None and cancellation.cancelled():
                            raise InterruptedError('Remotion preview cancelled')
                        if time.monotonic() >= preview_deadline:
                            preview_failure_reason = 'Remotion preview exceeded its bounded review budget'
                            print(f"   [render] {preview_failure_reason}; stopping review and continuing to export under non-strict policy.")
                            preview_failed = True
                            _stop_remotion_process(preview)
                            break
                        try:
                            preview_log, _ = preview.communicate(timeout=1)
                            break
                        except subprocess.TimeoutExpired:
                            continue
                    if preview_failed:
                        break
                    completed_layout_report = False
                    if preview.returncode != 0 and preview_report_path.exists():
                        try:
                            candidate_report = json.loads(preview_report_path.read_text(encoding='utf-8'))
                            completed_layout_report = bool(candidate_report.get('layout_failures')) and candidate_report.get('coverage') == 'complete'
                        except (ValueError, OSError):
                            pass
                    # A complete frame set with measured overflow is useful
                    # repair evidence. Send it to the critic rather than bypass
                    # the whole review because the renderer returned nonzero.
                    if preview.returncode != 0 and not completed_layout_report:
                        preview_error_path = output.with_suffix('.preview-error.log')
                        try:
                            preview_error_path.write_text(str(preview_log), encoding='utf-8')
                        except Exception:
                            pass
                        print(f"   [render] Warning: Remotion preview attempt failed (exit {preview.returncode}). Retrying preview once...")
                        if preview_deadline - time.monotonic() <= 1:
                            preview_failed = True
                            preview_failure_reason = 'Preview retry has no time left in the review budget'
                            break
                        retry_preview = subprocess.Popen(preview_cmd, **process_options)
                        if cancellation is not None:
                            cancellation.register_process(retry_preview)
                        try:
                            retry_log, _ = retry_preview.communicate(timeout=max(.01, min(120, preview_deadline - time.monotonic())))
                            retry_layout_report = False
                            if retry_preview.returncode != 0 and preview_report_path.exists():
                                try:
                                    candidate_report = json.loads(preview_report_path.read_text(encoding='utf-8'))
                                    retry_layout_report = bool(candidate_report.get('layout_failures')) and candidate_report.get('coverage') == 'complete'
                                except (ValueError, OSError):
                                    pass
                            if retry_preview.returncode == 0 or retry_layout_report:
                                preview = retry_preview
                                preview_log = retry_log
                            else:
                                print(f"   [render] Remotion preview retry also failed (exit {retry_preview.returncode}). Failing open directly to full video render.")
                                preview_failed = True
                                break
                        except Exception as retry_err:
                            print(f"   [render] Remotion preview retry error: {retry_err}. Failing open directly to full video render.")
                            preview_failed = True
                            break
                        finally:
                            if retry_preview.poll() is None:
                                _stop_remotion_process(retry_preview)
                            if cancellation is not None:
                                cancellation.unregister_process(retry_preview)

                    if not preview_report_path.exists():
                        print("   [render] Warning: Preview report was not generated. Failing open directly to full video export.")
                        preview_failed = True
                        break

                    try:
                        review = json.loads(preview_report_path.read_text(encoding='utf-8'))
                        frame_paths = [row['path'] for row in review.get('frames') or []]
                        if not frame_paths or review.get('coverage') != 'complete' or review.get('omitted_frames'):
                            print("   [render] Warning: Preview report frame coverage incomplete. Failing open directly to full video export.")
                            preview_failed = True
                            break
                    except Exception as parse_err:
                        print(f"   [render] Warning: Error parsing preview report ({parse_err}). Failing open directly to full video export.")
                        preview_failed = True
                        break
                    transition_positions = intentional_transition_positions(
                        review.get('frames') or [], manifest_out.get('transitions') or [], manifest_out.get('fps') or 30
                    )
                    review['pixel_audit'] = audit_frames(
                        frame_paths, intentional_transition_positions=transition_positions
                    )
                    if review.get('layout_failures'):
                        review['pixel_audit']['status'] = 'fail'
                    # Preserve actual before/after PNGs, not just a overwritten verdict.
                    archive = output.parent / f'{output.stem}.creative-review' / f'round-{revision_round}'
                    archive.mkdir(parents=True,exist_ok=True)
                    for row in review['frames']:
                        preserved = archive / Path(row['path']).name
                        shutil.copy2(row['path'],preserved)
                        row['path'] = str(preserved)
                    assessment_only = revision_round == last_review_round or preview_deadline - time.monotonic() <= review_policy['assessment_reserve_seconds']
                    if callable(client_factory):
                        budget = min(max(35.0, scene_waves * 20.0),
                                     max(.01,preview_deadline-time.monotonic()))
                        if not assessment_only:
                            budget = min(budget, max(.01, preview_deadline-time.monotonic()-review_policy['assessment_reserve_seconds']))
                        before = json.loads(json.dumps(manifest_out,default=_json_default))
                        creative = review_and_revise(manifest_out,review['frames'],client_factory,
                            budget_seconds=budget,cancellation=cancellation,pass_cache=creative_pass_cache,
                            pixel_audit=review['pixel_audit'], layout_audit=review.get('layout_audit'),
                            assessment_only=assessment_only)
                        save_pass_cache(creative_cache_path, creative_pass_cache)
                        # Last round is assessment only: unapplied suggestions cannot pass.
                        if revision_round == last_review_round and creative['changed_scene_indices']:
                            manifest_out = before
                            creative['unresolved_scene_indices'] = sorted(set(creative['unresolved_scene_indices'] + creative['changed_scene_indices']))
                            creative['changed_scene_indices'] = []
                            creative['status'] = 'needs_revision'
                        creative_history.append(creative)
                    else:
                        creative = {'status':'unavailable','reason':'No configured vision provider; creative quality has not been certified'}
                        creative_history.append(creative)
                    review['creative_review'] = creative
                    (archive / 'report.json').write_text(json.dumps(review,indent=2,default=str),encoding='utf-8')
                    preview_report_path.write_text(json.dumps(review,indent=2,default=str),encoding='utf-8')
                    manifest_out['creative_review'] = {'status':creative['status'],'rounds':creative_history,'evidence_path':str(archive.parent),
                        'policy': review_policy, 'llm_review_certified': creative.get('llm_review_certified', False)}
                    # Keep failed review findings beside the retryable plan.
                    # A blocker must not leave only the pre-review manifest.
                    with open(manifest_dump_path,'w',encoding='utf-8') as saved:
                        json.dump(manifest_out,saved,indent=2,default=_json_default)
                    design_changed = bool(creative.get('changed_scene_indices'))
                    if design_changed:
                        from .semantic_edit_engine import rescore_revised_soundtrack
                        rescore_revised_soundtrack(manifest_out, creative['changed_scene_indices'], procedural_sound_library())
                        from .editorial_director.production_contract import compile_production_timeline
                        compile_production_timeline(manifest_out)
                        if manifest_out['production_audit']['status']=='fail':
                            raise ValueError('Creative revision violated the production contract')
                        with open(temp_manifest_path,'w',encoding='utf-8') as saved:
                            json.dump(manifest_out,saved,indent=2,default=_json_default)
                    # Repair bad footage in the same iteration as a valid
                    # composition change. Previously repeated design changes
                    # consumed every review round before media recovery ran.
                    media_repairs = [
                        {**row, 'repair_kind': 'media'}
                        for row in creative.get('scenes') or []
                        if row.get('status') == 'needs_revision'
                        and _is_actual_media_mismatch(row)
                    ]
                    media_changed = False
                    repair_media = cfg.get('_creative_media_repair')
                    remaining = preview_deadline-time.monotonic()
                    if (media_repairs and callable(repair_media) and not media_repair_used
                            and remaining > 15 and revision_round < last_review_round and not assessment_only):
                        media_repair_used = True
                        try:
                            media_changed = _run_bounded_media_repair(
                                repair_media, manifest_out, media_repairs, min(25, remaining),
                            )
                        except Exception as exc:
                            creative['media_repair_error'] = f'{type(exc).__name__}: {str(exc)[:300]}'
                        if media_changed:
                            from .semantic_edit_engine import rescore_revised_soundtrack
                            rescore_revised_soundtrack(manifest_out, [row['scene_index'] for row in media_repairs], procedural_sound_library())
                            from .editorial_director.production_contract import compile_production_timeline
                            compile_production_timeline(manifest_out)
                            # A fresh query can pass subject verification and
                            # still repeat the pixels already used by a nearby
                            # scene. Give only those repeated scenes one
                            # bounded, context-aware diversity retry before
                            # stopping the render.
                            audit = manifest_out.get('production_audit') or {}
                            if audit.get('status') == 'fail':
                                audit_errors = list(audit.get('errors') or [])
                                diversity_findings = _perceptual_repeat_repair_findings(audit_errors)
                                repeat_indices = [row['scene_index'] for row in diversity_findings]
                                retry_only_repeats = bool(repeat_indices) and all(
                                    error.get('code') == 'unresolved_perceptual_repetition'
                                    for error in audit_errors
                                )
                                remaining = preview_deadline-time.monotonic()
                                if retry_only_repeats and remaining > 20:
                                    try:
                                        diversity_changed = _run_bounded_media_repair(
                                            repair_media, manifest_out, diversity_findings, min(12, remaining),
                                        )
                                    except Exception as exc:
                                        creative['media_diversity_retry_error'] = f'{type(exc).__name__}: {str(exc)[:240]}'
                                        diversity_changed = False
                                    if diversity_changed:
                                        compile_production_timeline(manifest_out)
                                        media_changed = True
                                        audit = manifest_out.get('production_audit') or {}
                                        creative['media_diversity_retry'] = {
                                            'scene_indices': repeat_indices,
                                            'status': 'pass' if audit.get('status') != 'fail' else 'needs_revision',
                                        }
                                if audit.get('status') == 'fail':
                                    audit_errors = list(audit.get('errors') or [])
                                    creative['media_repair_audit_errors'] = audit_errors
                                    creative['media_repair_status'] = 'rejected_by_production_audit'
                                    manifest_out.setdefault('creative_review', {})['media_repair'] = {
                                        'status': 'rejected_by_production_audit',
                                        'errors': audit_errors,
                                    }
                                    remaining = preview_deadline-time.monotonic()
                                    final_repeat_findings = _perceptual_repeat_repair_findings(audit_errors)
                                    only_repeats_remain = bool(final_repeat_findings) and all(
                                        error.get('code') == 'unresolved_perceptual_repetition'
                                        for error in audit_errors
                                    )
                                    if only_repeats_remain and revision_round < last_review_round and remaining > 20:
                                        # Media retrieval was tried twice and still returned repeated pixels.
                                        # Pass the exact blockers to the scene-local director for a grounded
                                        # component redesign instead of accepting the duplicate or stopping
                                        # before the existing Remotion-grammar fallback can run.
                                        fallback_feedback = _grounded_graphic_fallback_feedback(final_repeat_findings)
                                        current_feedback = manifest_out.get('_creative_feedback')
                                        if not isinstance(current_feedback, Mapping):
                                            current_feedback = {}
                                        manifest_out['_creative_feedback'] = {
                                            **dict(current_feedback), **fallback_feedback,
                                        }
                                        fallback_indices = sorted(int(index) for index in fallback_feedback)
                                        creative['graphic_fallback_scene_indices'] = fallback_indices
                                        creative['unresolved_scene_indices'] = sorted(set(
                                            list(creative.get('unresolved_scene_indices') or []) + fallback_indices
                                        ))
                                        creative['media_repair_status'] = 'grounded_graphic_recomposition_requested'
                                        creative['media_diversity_retry'] = {
                                            'scene_indices': fallback_indices,
                                            'status': 'needs_grounded_graphic_recomposition',
                                        }
                                        manifest_out.setdefault('creative_review', {})['media_repair'] = {
                                            'status': 'grounded_graphic_recomposition_requested',
                                            'errors': audit_errors,
                                            'scene_indices': fallback_indices,
                                        }
                                        for saved_manifest in (temp_manifest_path, manifest_dump_path):
                                            with open(saved_manifest, 'w', encoding='utf-8') as saved:
                                                json.dump(manifest_out, saved, indent=2, ensure_ascii=False, default=_json_default)
                                        review['creative_review'] = creative
                                        preview_report_path.write_text(json.dumps(review, indent=2, default=str), encoding='utf-8')
                                        (archive / 'report.json').write_text(json.dumps(review, indent=2, default=str), encoding='utf-8')
                                        if callable(progress_callback):
                                            progress_callback(0.0, 'Recomposing repeated shots as grounded Remotion graphics')
                                        continue
                                    # Keep the failed plan and exact blockers
                                    # next to the review frames so recovery is
                                    # actionable and never appears clean.
                                    for saved_manifest in (temp_manifest_path, manifest_dump_path):
                                        with open(saved_manifest, 'w', encoding='utf-8') as saved:
                                            json.dump(manifest_out, saved, indent=2, ensure_ascii=False, default=_json_default)
                                    review['creative_review'] = creative
                                    preview_report_path.write_text(json.dumps(review, indent=2, default=str), encoding='utf-8')
                                    (archive / 'report.json').write_text(json.dumps(review, indent=2, default=str), encoding='utf-8')
                                    detail = json.dumps(audit_errors, ensure_ascii=False, default=str)[:1800]
                                    raise ValueError(f'Fresh creative media violated the production contract: {detail}')
                            with open(temp_manifest_path,'w',encoding='utf-8') as saved:
                                json.dump(manifest_out,saved,indent=2,default=_json_default)
                    if media_repair_used and revision_round < last_review_round and not assessment_only:
                        # Make the documented graphic fallback available only
                        # to scene-specific media failures that remain after
                        # their one bounded verified-media repair attempt.
                        media_still_unresolved = [
                            row for row in creative.get('scenes') or []
                            if row.get('status') == 'needs_revision'
                            and _is_actual_media_mismatch(row)
                            and isinstance(row.get('scene_index'), int)
                        ]
                        graphic_feedback = _grounded_graphic_fallback_feedback([
                            {'scene_index': row['scene_index'], 'fallback_reason': 'media_search_exhausted'}
                            for row in media_still_unresolved
                        ])
                        if graphic_feedback:
                            current_feedback = manifest_out.get('_creative_feedback')
                            if not isinstance(current_feedback, Mapping):
                                current_feedback = {}
                            manifest_out['_creative_feedback'] = {
                                **dict(current_feedback), **graphic_feedback,
                            }
                            fallback_indices = sorted(int(index) for index in graphic_feedback)
                            creative['graphic_fallback_scene_indices'] = sorted(set(
                                list(creative.get('graphic_fallback_scene_indices') or []) + fallback_indices
                            ))
                            creative['unresolved_scene_indices'] = sorted(set(
                                list(creative.get('unresolved_scene_indices') or []) + fallback_indices
                            ))
                            manifest_out.setdefault('creative_review', {})['media_fallback'] = {
                                'status': 'grounded_graphic_recomposition_requested',
                                'scene_indices': fallback_indices,
                            }
                            for saved_manifest in (temp_manifest_path, manifest_dump_path):
                                with open(saved_manifest, 'w', encoding='utf-8') as saved:
                                    json.dump(manifest_out, saved, indent=2, ensure_ascii=False, default=_json_default)
                            review['creative_review'] = creative
                            preview_report_path.write_text(json.dumps(review, indent=2, default=str), encoding='utf-8')
                            (archive / 'report.json').write_text(json.dumps(review, indent=2, default=str), encoding='utf-8')
                    if design_changed or media_changed:
                        if callable(progress_callback):
                            progress_callback(0.0,'Reviewing revised graphics and media before export')
                        continue
                    if creative.get('unresolved_scene_indices') and revision_round < last_review_round:
                        # A rejected design is actionable feedback, not the end
                        # of orchestration. Keep successful scenes and ask the
                        # remaining scene designers to correct the exact fault.
                        current_feedback = manifest_out.get('_creative_feedback')
                        if not isinstance(current_feedback, Mapping):
                            current_feedback = {}
                        manifest_out['_creative_feedback'] = {
                            **dict(current_feedback),
                            **{str(row['scene_index']):row for row in creative.get('scenes') or []
                               if row.get('status')=='needs_revision'},
                        }
                        if assessment_only:
                            break
                        continue
                    break
                finally:
                    if preview.poll() is None:
                        _stop_remotion_process(preview)
                    if cancellation is not None:
                        cancellation.unregister_process(preview)
            if preview_failed or not review or not isinstance(review.get('pixel_audit'), dict):
                print("   [render] Remotion visual critic preview was bypassed/failed-open; proceeding directly to full video render.")
                manifest_out['preview_review'] = _preview_review_fallback(
                    preview_failure_reason or 'preview_report_unavailable',
                    strict=_enabled(cfg.get('strict_creative_review', False)),
                )
                manifest_out['creative_review'] = {
                    **(manifest_out.get('creative_review') or {}),
                    'status': 'incomplete', 'llm_review_certified': False,
                    'reason': preview_failure_reason or 'preview_report_unavailable',
                }
            else:
                if review['pixel_audit']['status'] == 'fail':
                    if _enabled(cfg.get('strict_creative_review', False)):
                        raise ValueError(f"Remotion preview requires correction: {review['pixel_audit'].get('findings')}")
                    print(f"   [render] Pixel audit flagged advisory findings ({review['pixel_audit'].get('findings')}); non-strict mode proceeding to export.")
                deferred_minor = _defer_minor_creative_findings(
                    creative,
                    manifest_out.get('production_audit') or {},
                    review.get('pixel_audit') or {},
                    manifest=manifest_out,
                    openrouter_api_key=str(
                        cfg.get('openrouter_api_key') or os.environ.get('OPENROUTER_API_KEY')
                        or os.environ.get('VIDZEO_OPENROUTER_API_KEY') or ''
                    ),
                )
                if deferred_minor:
                    # Preserve the exact review and make the bounded, nonblocking
                    # decision visible in both the preview report and final plan.
                    review['creative_review'] = creative
                    manifest_out['creative_review'] = {
                        **(manifest_out.get('creative_review') or {}),
                        'status': creative.get('status') if creative else None,
                        'deferred_minor_findings': deferred_minor,
                        'deferred_minor_policy': 'production_and_pixel_audits_passed_after_bounded_revisions',
                    }
                    preview_report_path.write_text(
                        json.dumps(review, indent=2, default=str), encoding='utf-8'
                    )
                    if archive is not None:
                        (archive / 'report.json').write_text(
                            json.dumps(review, indent=2, default=str), encoding='utf-8'
                        )
                    for saved_manifest in (temp_manifest_path, manifest_dump_path):
                        with open(saved_manifest, 'w', encoding='utf-8') as saved:
                            json.dump(manifest_out, saved, indent=2, ensure_ascii=False, default=_json_default)
                if creative and creative.get('unresolved_scene_indices'):
                    safe_advisories = _nonmaterial_creative_advisories(
                        creative, manifest_out.get('production_audit') or {},
                    )
                    if (safe_advisories and review.get('pixel_audit', {}).get('status') == 'pass'
                            and not _enabled(cfg.get('strict_creative_review', False))):
                        print(f"   [render] pixel audit passed; deferring remaining advisory review notes for scenes: {creative['unresolved_scene_indices']}")
                        creative['render_fallback'] = {
                            'mode': 'pixel_audited_creative_advisory_deferred',
                            'scene_indices': creative['unresolved_scene_indices'],
                            'llm_review_certified': False,
                        }
                        manifest_out['creative_review'] = {
                            **(manifest_out.get('creative_review') or {}),
                            'status': 'pass_with_advisories',
                            'render_fallback': creative['render_fallback'],
                        }
                    elif not _enabled(cfg.get('strict_creative_review', False)):
                        advisories = _nonmaterial_creative_advisories(
                            creative, manifest_out.get('production_audit') or {},
                        )
                        if advisories:
                            # Pixel findings remain visible as a failed/advisory
                            # local audit; this only prevents a second, stricter
                            # gate from discarding the deliverable after all
                            # blocking production checks have passed.
                            creative['render_fallback'] = {
                                'mode': 'bounded_nonmaterial_review_advisories',
                                'scene_indices': [row['scene_index'] for row in advisories],
                                'findings': advisories,
                                'llm_review_certified': False,
                                'pixel_audit_status': review.get('pixel_audit', {}).get('status'),
                            }
                            manifest_out['creative_review'] = {
                                **(manifest_out.get('creative_review') or {}),
                                'status': 'exported_with_advisories',
                                'advisories': advisories,
                                'render_fallback': creative['render_fallback'],
                            }
                            print(
                                '   [render] exporting after bounded redesign rounds; '
                                f"non-material creative notes remain for scenes: {[row['scene_index'] for row in advisories]}"
                            )
                        else:
                            advisories = [
                                {'scene_index': idx, 'issues': (creative.get('scenes', [{}])[idx] if isinstance(creative.get('scenes'), list) and 0 <= idx < len(creative.get('scenes', [])) else {}).get('issues') or ['Creative review advisory']}
                                for idx in (creative.get('unresolved_scene_indices') or [])
                            ]
                            creative['render_fallback'] = {
                                'mode': 'bounded_review_advisories',
                                'scene_indices': [row['scene_index'] for row in advisories],
                                'findings': advisories,
                                'llm_review_certified': False,
                                'pixel_audit_status': review.get('pixel_audit', {}).get('status'),
                            }
                            manifest_out['creative_review'] = {
                                **(manifest_out.get('creative_review') or {}),
                                'status': 'exported_with_advisories',
                                'advisories': advisories,
                                'render_fallback': creative['render_fallback'],
                            }
                            print(
                                '   [render] exporting after bounded redesign rounds; '
                                f"creative notes recorded as advisories for scenes: {[row['scene_index'] for row in advisories]}"
                            )
                    else:
                        raise ValueError(f"Creative frame review requires scene redesign: {creative['unresolved_scene_indices']}")
                if creative and creative.get('status') in {'incomplete','unavailable'} and _enabled(cfg.get('require_creative_acceptance',True)):
                    # A vision-provider outage must not turn a clean, pixel-audited
                    # Remotion render into a failed job. Preserve the incomplete
                    # LLM-review status and allow export only when every scene
                    # without a review is explicitly listed as unavailable, no
                    # actual revision finding remains, and the local frame audit
                    # passed. This is a delivery fallback, never a creative-pass
                    # certification.
                    unavailable = sorted(set(creative.get('unavailable_scene_indices') or []))
                    if unavailable and review.get('pixel_audit', {}).get('status') == 'pass':
                        creative['render_fallback'] = {
                            'mode': 'pixel_audited_provider_unavailable',
                            'scene_indices': unavailable,
                            'llm_review_certified': False,
                        }
                        manifest_out['creative_review'] = {
                            **(manifest_out.get('creative_review') or {}),
                            'status': creative.get('status'),
                            'render_fallback': creative['render_fallback'],
                        }
                    elif not _enabled(cfg.get('strict_creative_review', False)) or review.get('pixel_audit', {}).get('status') == 'pass':
                        creative['render_fallback'] = {
                            'mode': 'pixel_audited_creative_advisory_deferred',
                            'scene_indices': unavailable or list(range(len(manifest_out.get('scenes') or []))),
                            'llm_review_certified': False,
                        }
                        manifest_out['creative_review'] = {
                            **(manifest_out.get('creative_review') or {}),
                            'status': 'pass_with_advisories',
                            'render_fallback': creative['render_fallback'],
                        }
                    else:
                        raise ValueError('Creative frame review incomplete; retry without certifying unreviewed scenes')
                manifest_out['preview_review'] = {'path':str(preview_report_path),'status':review['pixel_audit']['status'],'coverage':review.get('coverage')}
            # Recompute after the final repair, not from the procedural draft.
            # Do not bind old screenshots to a newly changed manifest.
            update_graphic_audit(manifest_out, review.get('layout_audit') if isinstance(review, Mapping) else None)
            manifest_out.pop('_creative_feedback',None)
            for saved_manifest in (temp_manifest_path, manifest_dump_path):
                with open(saved_manifest, 'w', encoding='utf-8') as saved:
                    json.dump(manifest_out, saved, indent=2, ensure_ascii=False, default=_json_default)
            if callable(progress_callback):
                certified = (manifest_out.get('creative_review') or {}).get('status') == 'pass'
                progress_callback(0.0, 'Frame review passed; starting full export' if certified else 'Bounded frame review finished; starting export with recorded review notes')
        process = subprocess.Popen(cmd, **process_options)
        if cancellation is not None:
            cancellation.register_process(process)

        output_queue = Queue()

        def _read_output():
            try:
                for output_line in iter(process.stdout.readline, ""):
                    output_queue.put(output_line)
            finally:
                output_queue.put(None)

        reader = JobThread(target=_read_output, name="remotion-output", daemon=True)
        reader.start()
        last_progress = None
        last_progress_pct = 0.0
        last_progress_time = time.monotonic()
        last_output_time = time.monotonic()
        last_frame_activity = None
        diagnostic_tail = []
        waiting_for_memory = False
        output_sizes = {}
        last_activity_probe = 0.0
        # CMS documentary exports may legitimately exceed the preparation
        # budget on memory-constrained hosts. When continue-while-active is
        # enabled, real frame/output progress and the stall watchdog govern
        # this process; elapsed wall time alone must never kill a healthy render.
        deadline, hard_deadline = _render_deadlines(
            continue_while_active=continue_render,
            now=time.monotonic(),
            timeout_seconds=timeout_seconds,
        )
        default_stall_seconds = 300.0 if cfg.get("llm_provider") == "external_agent" else 1200.0
        try:
            stall_timeout = float(os.environ.get("VIDZEO_REMOTION_STALL_TIMEOUT_SECONDS", default_stall_seconds))
            if not math.isfinite(stall_timeout):
                stall_timeout = default_stall_seconds
        except (TypeError, ValueError):
            stall_timeout = default_stall_seconds
        stall_timeout = max(180.0, min(1200.0, stall_timeout))
        stream_closed = False
        while not stream_closed:
            if cancellation is not None and cancellation.cancelled():
                _stop_remotion_process(process)
                raise InterruptedError("Remotion render cancelled")
            now = time.monotonic()
            if not continue_render and now >= hard_deadline:
                _stop_remotion_process(process)
                raise TimeoutError('Remotion reached the two-hour hard limit; resume the saved render checkpoint.')
            if now - last_activity_probe >= 5.0:
                last_activity_probe = now
                # CPU use or repeated source reads can belong to a stuck
                # worker. Only advancing counters or a growing owned export
                # extend inactivity, including a quiet final FFmpeg join.
                for candidate in (output, output.parent / f'.{output.stem}.rendering-{process.pid}{output.suffix}'):
                    try:
                        size = candidate.stat().st_size
                        if size > output_sizes.get(str(candidate), 0):
                            last_progress_time = now
                            output_sizes[str(candidate)] = size
                    except OSError:
                        pass
            if not continue_render and now >= deadline:
                # If Remotion is actively advancing through Webpack, Chromium,
                # or frame rendering (real progress within the last 90s)
                # and hasn't exceeded the hard ceiling, grant dynamic grace time so healthy renders finish!
                if (
                    (now - last_progress_time) < 90.0
                ):
                    extension = 120.0
                    deadline = min(hard_deadline, now + extension)
                    print(
                        f"   [remotion] active render progress detected ({last_progress_pct:.0f}%); "
                        f"extending deadline by {extension:.0f}s to {deadline - started:.0f}s"
                    )
                else:
                    _stop_remotion_process(process)
                    raise TimeoutError(
                        f"Remotion render exceeded {timeout_seconds:.0f}s "
                        f"(last progress: {last_progress_pct:.0f}%, "
                        f"last progress update {now - last_progress_time:.1f}s ago)"
                    )
            # Final muxing can legitimately be quiet after Remotion reports
            # 99-100%. Give that bounded closeout a longer window instead of
            # killing a completed frame render and presenting it as a stall.
            effective_stall_timeout = max(
                stall_timeout,
                300.0 if last_progress_pct >= 99.0 else stall_timeout,
            )
            if not waiting_for_memory and now - last_progress_time > effective_stall_timeout:
                _stop_remotion_process(process)
                raise TimeoutError(f"Remotion render stalled: no output for {effective_stall_timeout:.0f}s")

            try:
                line = output_queue.get(timeout=0.5)
            except Empty:
                if process.poll() is not None and output_queue.empty():
                    break
                continue
            if line is None:
                stream_closed = True
                continue
            last_output_time = time.monotonic()
            line_str = str(line).strip()
            if not line_str:
                continue
            try:
                with render_log_path.open("a", encoding="utf-8") as render_log:
                    render_log.write(line_str + "\n")
            except OSError:
                pass
            diagnostic_tail.append(line_str)
            diagnostic_tail = diagnostic_tail[-18:]
            stage_line = re.search(r"\[Remotion\]\s*Stage:\s*(.+)$", line_str, re.IGNORECASE)
            if stage_line:
                # Match known stage prefixes instead of splitting on the
                # decorative dash. Windows locale/codepage conversions can
                # change that punctuation on the Python subprocess pipe.
                stage_body = re.sub(r"\s+", " ", stage_line.group(1).strip().lower())
                waiting_for_memory = stage_body.startswith(('waiting for memory', 'waiting for render slot'))
                stage_names = (
                    "preparing renderer",
                    "bundling composition",
                    "selecting composition",
                    "launching render browser",
                    "rendering frames",
                    "finalizing render output",
                )
                stage = next((name for name in stage_names if stage_body.startswith(name)), "")
                percent_match = (
                    re.match(rf"^{re.escape(stage)}\s+(\d{{1,3}})%", stage_body)
                    if stage
                    else None
                )
                try:
                    stage_pct = max(0.0, min(100.0, float(percent_match.group(1) if percent_match else 0)))
                except (TypeError, ValueError):
                    stage_pct = 0.0
                stage_progress = {
                    "preparing renderer": 0.025,
                    "bundling composition": 0.035 + (0.00125 * stage_pct),
                    "selecting composition": 0.17,
                    "launching render browser": 0.18,
                    "rendering frames": 0.20,
                    "finalizing render output": 0.98,
                }.get(stage)
                if stage_progress is not None:
                    stage_percent = stage_progress * 100.0
                    if stage_percent > last_progress_pct:
                        last_progress_pct = stage_percent
                        last_progress_time = time.monotonic()
                if stage_progress is not None and progress_callback:
                    stage_labels = {
                        "preparing renderer": "Preparing the video renderer",
                        "bundling composition": f"Preparing the video renderer ({int(stage_pct)}%)",
                        "selecting composition": "Loading the documentary edit",
                        "launching render browser": "Starting render workers",
                        "rendering frames": "Rendering scenes",
                        "finalizing render output": "Finalizing the Remotion render",
                    }
                    try:
                        progress_callback(stage_progress, stage_labels[stage])
                    except Exception:
                        pass
            progress_match = re.search(r"Progress:\s*(\d+)%", line_str)
            # Frame counters advance even when a long film remains on the
            # same rounded percentage for several minutes.
            activity_match = re.search(r"Activity:\s*rendered=(\d+)\s+encoded=(\d+)", line_str)
            if activity_match:
                activity = tuple(int(value) for value in activity_match.groups())
                previous = last_frame_activity or (0, 0)
                if any(current > saved for current, saved in zip(activity, previous)):
                    last_progress_time = time.monotonic()
                last_frame_activity = tuple(max(current, saved) for current, saved in zip(activity, previous))
            if progress_match:
                pct_text = progress_match.group(1)
                try:
                    pct_val = float(pct_text)
                    mapped_pct = 0.20 + (0.77 * max(0.0, min(1.0, pct_val / 100.0)))
                    mapped_percent = mapped_pct * 100.0
                    if mapped_percent > last_progress_pct:
                        last_progress_pct = mapped_percent
                        last_progress_time = time.monotonic()
                except (TypeError, ValueError):
                    pass
                if pct_text == last_progress:
                    continue
                last_progress = pct_text
            print(f"[remotion] {line_str}")
            if progress_match and progress_callback:
                try:
                    pct = float(progress_match.group(1)) / 100.0
                    # Renderer setup and cold Webpack bundling have their own
                    # real progress range. Reserve the main range for frame
                    # rendering, then leave a small tail for muxing/validation.
                    mapped_pct = 0.20 + (0.77 * max(0.0, min(1.0, pct)))
                    progress_callback(mapped_pct, f"Remotion frame progress: {progress_match.group(1)}%")
                except Exception:
                    pass

        process.wait(timeout=5)
        if process.returncode != 0:
            # Keep the actual finalization/decoder cause rather than an opaque
            # exit-code wrapper. Full diagnostics stay in the owned job log.
            detail = "\n".join(diagnostic_tail)[-4000:]
            raise RuntimeError(f"Remotion render failed with exit code {process.returncode}: {detail}")

        if not output.is_file() or output.stat().st_size < 4096:
            raise RuntimeError(f"Remotion output file missing or empty: {output}")

        elapsed = round(time.monotonic() - started, 3)
        if progress_callback:
            progress_callback(1.0, "Remotion render complete!")

        return {
            "output_path": str(output),
            "render_seconds": elapsed,
            "quality": str(cfg.get("quality") or "remotion-full"),
            "scale": str(env.get("VIDZEO_REMOTION_SCALE") or "1.0"),
            "x264_preset": selected_x264_preset,
            "crf": selected_crf,
            "renderer": "remotion-react",
            "preview_review": manifest_out.get('preview_review'),
            "creative_review": manifest_out.get('creative_review'),
            "rendered_graphics_audit": (manifest_out.get("metrics") or {}).get("rendered_graphics_audit"),
            "rendered_manifest_path": str(manifest_dump_path),
        }
    finally:
        render_process = locals().get('process')
        if render_process is not None:
            if render_process.poll() is None:
                _stop_remotion_process(render_process)
            if cancellation is not None:
                cancellation.unregister_process(render_process)
            staged_output = output.parent / f'.{output.stem}.rendering-{render_process.pid}{output.suffix}'
            try:
                staged_output.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            if os.path.exists(temp_manifest_path):
                os.remove(temp_manifest_path)
        except Exception:
            pass


def render_semantic_documentary(
    manifest: Mapping[str, Any],
    output_path: str | Path,
    config: Optional[Mapping[str, Any]] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    cancellation=None,
) -> Dict[str, Any]:
    """Serialize renders and reclaim Vidnets-owned idle memory before rendering."""
    waiting_reported = False
    last_wait_report = 0.0
    from .job_context import check_job_active, remaining_seconds, current_job
    continue_render = _enabled((config or {}).get('continue_render_while_active', os.environ.get('VIDZEO_RENDER_CONTINUE_WHILE_ACTIVE', '1')))
    budget = current_job().get('delivery_budget') or (config or {}).get('_delivery_budget')
    if continue_render and budget is not None:
        budget.begin_render_phase()
    try:
        wait_budget = float((config or {}).get('semantic_render_budget_seconds') or 900)
        if not math.isfinite(wait_budget) or wait_budget <= 0:
            wait_budget = 900
    except (TypeError, ValueError):
        wait_budget = 900
    wait_deadline = time.monotonic() + min(wait_budget, remaining_seconds(wait_budget))
    # Continuing an active render never grants an infinite wait for another
    # job's slot. A lost owner/child must time out with its checkpoint intact.
    while not _RENDER_SLOT.acquire(timeout=0.5):
        check_job_active()
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        now = time.monotonic()
        if now >= wait_deadline:
            error = TimeoutError('Waiting for the previous Remotion render exhausted this job budget. Retry the saved job.')
            error.stage = 'remotion_render'
            error.retryable = True
            raise error
        if not waiting_reported or (now - last_wait_report) >= 4.0:
            msg = "Waiting for previous video render to finish..."
            print(f"[render-memory] {msg}")
            if progress_callback:
                try:
                    progress_callback(0.0, msg)
                except Exception:
                    pass
            waiting_reported = True
            last_wait_report = now
    try:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        from .render_resource_manager import (
            prepare_render_resources,
            RenderMemoryLowError,
            wait_for_render_memory,
        )

        selected_backend = str(
            (config or {}).get("render_backend")
            or os.environ.get("VIDZEO_RENDER_BACKEND", "remotion")
        ).strip().lower()
        manifest_config = dict((manifest or {}).get("config") or {}) if isinstance((manifest or {}).get("config"), Mapping) else {}
        render_width = int((manifest or {}).get("width") or manifest_config.get("width") or 1920)
        render_height = int((manifest or {}).get("height") or manifest_config.get("height") or 1080)
        video_extensions = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".wmv", ".m3u8"}
        video_heavy = False
        for scene in (manifest or {}).get("scenes", []):
            if not isinstance(scene, Mapping):
                continue
            primary = scene.get("primary_visual") if isinstance(scene.get("primary_visual"), Mapping) else {}
            for asset in (scene.get("media"), primary.get("asset")):
                if isinstance(asset, Mapping):
                    media_type = str(asset.get("type") or "").lower()
                    media_path = str(asset.get("src") or asset.get("path") or asset.get("url") or "")
                else:
                    media_type = ""
                    media_path = str(asset or "")
                if media_type == "video" or media_type.startswith("video/") or Path(media_path.split("?", 1)[0]).suffix.lower() in video_extensions:
                    video_heavy = True
                    break
            if video_heavy:
                break
        try:
            memory_wait_seconds = max(1.0, min(1200.0, float(os.environ.get('VIDZEO_REMOTION_MEMORY_WAIT_SECONDS') or 120)))
        except (TypeError, ValueError):
            memory_wait_seconds = 120.0
        try:
            memory_poll_seconds = max(0.1, min(5.0, float(os.environ.get('VIDZEO_REMOTION_MEMORY_POLL_SECONDS') or 0.5)))
        except (TypeError, ValueError):
            memory_poll_seconds = 0.5
        try:
            memory_worker_extend_seconds = max(1.0, min(300.0, float(os.environ.get('VIDZEO_REMOTION_MEMORY_WORKER_EXTEND_SECONDS') or 30)))
        except (TypeError, ValueError):
            memory_worker_extend_seconds = 30.0
        try:
            memory_no_worker_grace_seconds = max(0.0, min(120.0, float(os.environ.get('VIDZEO_REMOTION_MEMORY_NO_WORKER_GRACE_SECONDS') or 10)))
        except (TypeError, ValueError):
            memory_no_worker_grace_seconds = 10.0
        # Worker-aware memory wait (Nov 10, 2026): the old loop slept a
        # fixed 2s and expired after a flat 120s even while this job's own
        # media workers (YouTube ffmpeg downloads, search workers) were
        # still running — the job then paused and burned wrapper retries
        # until a retry happened to land after a worker exit. Now admission
        # is re-checked every ~0.5s, the deadline extends in 30s steps
        # while media workers are alive (bounded by the delivery budget),
        # and the wait fails fast once no worker is left to free memory.
        prepare_state = {'attempts': 0}

        def _prepare_once():
            prepare_state['attempts'] += 1
            return prepare_render_resources(
                require_remotion=selected_backend in {"remotion", "react"},
                width=render_width, height=render_height, video_heavy=video_heavy,
                release_idle=prepare_state['attempts'] <= 1,
            )

        def _check_active():
            check_job_active()
            if cancellation is not None:
                cancellation.raise_if_cancelled()

        def _on_memory_wait(info):
            workers = info.get('live_workers') or []
            message = (
                f"Waiting for render memory ({info.get('available_mb', 0)} MB available; "
                f"{len(workers)} media worker(s) holding "
                f"{info.get('live_worker_rss_mb', 0)} MB, "
                f"{info.get('deadline_in_seconds', 0):.0f}s of memory wait left); saved edit retained"
            )
            print(f"   [render-memory] {message}", flush=True)
            if progress_callback:
                try:
                    progress_callback(0.0, message)
                except Exception:
                    pass

        if continue_render:
            memory_snapshot = wait_for_render_memory(
                _prepare_once,
                wait_deadline=wait_deadline,
                base_wait_seconds=memory_wait_seconds,
                poll_seconds=memory_poll_seconds,
                worker_extend_seconds=memory_worker_extend_seconds,
                no_worker_grace_seconds=memory_no_worker_grace_seconds,
                on_wait=_on_memory_wait,
                check_active=_check_active,
            )
        else:
            memory_snapshot = _prepare_once()
        result = _render_semantic_documentary_unlocked(
            manifest=manifest,
            output_path=output_path,
            config=config,
            progress_callback=progress_callback,
            cancellation=cancellation,
            memory_snapshot=memory_snapshot,
        )
        if isinstance(result, dict):
            result = dict(result)
            result["render_resources"] = {
                **memory_snapshot,
                "render_slot": "exclusive_in_process",
            }
        return result
    finally:
        _RENDER_SLOT.release()


def _render_semantic_documentary_unlocked(
    manifest: Mapping[str, Any],
    output_path: str | Path,
    config: Optional[Mapping[str, Any]] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    cancellation=None,
    memory_snapshot: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Primary entry point for semantic documentary rendering.

    Supports Remotion (high-end React documentary motion system) and
    native FFmpeg as a reliable fallback.
    """
    config = dict(config or {})
    backend = str(
        config.get("render_backend")
        # The request layer normally supplies the quality-aware backend. When
        # called directly, the environment selects the canonical default.
        or os.environ.get("VIDZEO_RENDER_BACKEND", "remotion")
    ).strip().lower()

    if backend in {"remotion", "react"}:
        try:
            return render_semantic_documentary_remotion(
                manifest=manifest,
                output_path=output_path,
                config=config,
                progress_callback=progress_callback,
                cancellation=cancellation,
                memory_snapshot=memory_snapshot,
            )
        except Exception as exc:
            if (
                semantic_render_required(config)
                and not bool(config.get("_remotion_safe_retry"))
                and _transient_remotion_failure(exc)
            ):
                retry_config = dict(config)
                retry_config.update({
                    "_remotion_safe_retry": True,
                    "concurrency": 1,
                    "remotion_concurrency": 1,
                    "continue_render_while_active": True,
                })
                print(
                    "[native_render_policy] transient Remotion process failure; "
                    "retrying the saved manifest once with one worker",
                    flush=True,
                )
                if progress_callback:
                    try:
                        progress_callback(
                            0.0,
                            "Render process recovery: retrying the saved edit with one worker",
                        )
                    except Exception:
                        pass
                try:
                    return render_semantic_documentary_remotion(
                        manifest=manifest,
                        output_path=output_path,
                        config=retry_config,
                        progress_callback=progress_callback,
                        cancellation=cancellation,
                        memory_snapshot=memory_snapshot,
                    )
                except Exception as retry_exc:
                    exc = retry_exc
            # A required Remotion job must never silently downgrade to the
            # legacy FFmpeg compositor: that masks broken React paths and can
            # deliver exactly the generic graphics the operator asked us to
            # eliminate. Callers that explicitly opt into legacy recovery can
            # still use the fallback below.
            if semantic_render_required(config):
                wrapped = RuntimeError(
                    f"Required Remotion render failed; legacy fallback is disabled: {exc}"
                )
                wrapped.stage = 'remotion_render'
                wrapped.retryable = _transient_remotion_failure(exc)
                memory_snapshot = getattr(exc, "memory_snapshot", None)
                if memory_snapshot:
                    wrapped.memory_snapshot = dict(memory_snapshot)
                raise wrapped from exc
            print(f"[native_render_policy] Remotion render failed ({exc}), falling back to FFmpeg...")

    from .semantic_ffmpeg_renderer import render_semantic_documentary_ffmpeg

    return render_semantic_documentary_ffmpeg(
        manifest=manifest,
        output_path=output_path,
        config=config,
        progress_callback=progress_callback,
        cancellation=cancellation,
    )
