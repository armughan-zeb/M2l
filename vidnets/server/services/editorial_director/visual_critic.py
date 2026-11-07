"""Deterministic visual critic for documentary showcase renders.

This is the render-time quality gate. It does not pretend to replace a human
director or a multimodal model; it catches predictable failures before a long
render: blank frames, unreadable occupancy, unsafe placement, unsupported
factual visuals, and repetitive grammar choices.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .media_first import audit_editorial_contracts


def _safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _frame_metrics(path: Path) -> Dict[str, Any]:
    try:
        from PIL import Image, ImageChops, ImageStat

        image = Image.open(path).convert("RGB")
        small = image.resize((96, 54))
        stat = ImageStat.Stat(small)
        mean = sum(stat.mean) / 3.0 / 255.0
        variance = sum(stat.var) / 3.0 / (255.0 * 255.0)
        gray = small.convert("L")
        pixels = list(gray.getdata())
        edges = 0
        for row in range(54):
            for col in range(96):
                index = row * 96 + col
                if col and abs(pixels[index] - pixels[index - 1]) > 26:
                    edges += 1
                if row and abs(pixels[index] - pixels[index - 96]) > 26:
                    edges += 1
        edge_density = edges / float(max(1, 2 * 96 * 54))
        dark_fraction = sum(1 for value in pixels if value < 34) / float(max(1, len(pixels)))
        highlight_fraction = sum(1 for value in pixels if value > 224) / float(max(1, len(pixels)))
        return {
            "path": str(path),
            "width": image.width,
            "height": image.height,
            "mean_luma": round(mean, 4),
            "variance": round(variance, 4),
            "edge_density": round(edge_density, 4),
            "dark_fraction": round(dark_fraction, 4),
            "highlight_fraction": round(highlight_fraction, 4),
            "low_information": bool(
                (variance < 0.003 and edge_density < 0.018)
                and not (highlight_fraction > 0.0005 or (mean >= 0.04 and (variance >= 0.0008 or edge_density >= 0.004)))
            ),
            # Night footage can have a mostly black sky while the subject is
            # sharply lit. Treat that as detailed imagery, not a blank frame.
            "excessively_dark": bool(
                mean < 0.16
                and dark_fraction > 0.58
                and not (edge_density >= 0.035 and variance >= 0.012)
            ),
            "flat_or_soft": bool(edge_density < 0.014 and variance < 0.012),
            "status": "pass",
        }
    except Exception as exc:  # pragma: no cover - depends on optional Pillow
        return {"path": str(path), "status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}


def _scene_grammar(scene: Mapping[str, Any]) -> str:
    primary = scene.get("primary_visual") or {}
    primary = primary if isinstance(primary, Mapping) else {}
    primary_kind = str(primary.get("type") or "").lower()
    text_contract = scene.get("text") if isinstance(scene.get("text"), Mapping) else {}
    frame_mode = str(text_contract.get("frame_mode") or scene.get("frame_mode") or "").strip().lower()
    template_id = str(
        scene.get("template_id")
        or (scene.get("graphic") or {}).get("template_id")
        or ""
    ).strip().lower().replace("_", "-")

    # V4 director decisions replace the legacy media-composition hints. Those
    # hints can remain in older manifests after a scene is deliberately set to
    # media_only; counting them as active graphics caused false chronology
    # failures and made QA report the wrong visual grammar.
    if scene.get("director_treatment_enabled") and frame_mode:
        if frame_mode == "media_only":
            if template_id and template_id not in {"", "full-bleed"}:
                return f"media:{template_id}"
            return "cinematic_type"
        if frame_mode == "map":
            return "spatial_atlas"
        if frame_mode == "data":
            return "chronology" if primary_kind == "timeline" else "data_inset"
        if frame_mode == "diagram":
            return "process_diagram"
        if frame_mode == "evidence":
            return "evidence_annotation"
        if frame_mode == "portrait":
            return "portrait_id"
        if frame_mode == "chapter":
            return "chapter_slate"
        if frame_mode == "quote":
            return "quote_pull"
        if frame_mode == "kinetic":
            return "cinematic_type"

    media_plan = scene.get("media_composition") if isinstance(scene.get("media_composition"), Mapping) else {}
    overlay_grammar = str(media_plan.get("overlay_grammar") or "").lower()
    swarm = scene.get("swarm_plan") if isinstance(scene.get("swarm_plan"), Mapping) else {}
    swarm_graphic = swarm.get("graphic") if isinstance(swarm.get("graphic"), Mapping) else {}
    normalized_overlay = overlay_grammar or str(swarm_graphic.get("grammar") or "").lower()
    if normalized_overlay in {"quote_pull", "chapter_slate", "archive_contact_sheet", "portrait_id", "chart_center", "data_inset", "locator_inset", "chronology_strip", "evidence_annotation", "analytic_callout", "field_caption"}:
        return normalized_overlay
    kind = primary_kind
    variant = str(primary.get("variant") or "").lower()
    if kind in {"statcard", "chart"}:
        return "data_story"
    if kind == "timeline":
        return "chronology"
    if any(token in variant for token in ("signal", "terminal", "console")):
        return "signal_console"
    if kind == "animatedmap" or "map" in variant or "route" in variant or "flow" in variant:
        return "spatial_atlas"
    if kind == "diagram":
        props = primary.get("props") if isinstance(primary.get("props"), Mapping) else {}
        if any(props.get(key) for key in ("from", "to", "start", "end", "country", "steps", "events")):
            return "spatial_atlas"
        # A generic systems/process diagram is not a map. Do not raise the
        # locator-specific warning for diagrams that contain no geography.
        return "cinematic_type"
    if kind in {"collage", "documenthighlight", "headline", "personprofile", "comparison"} or any(token in variant for token in ("dossier", "evidence", "newspaper")):
        return "evidence_desk"
    return "cinematic_type"


def _has_chronology_payload(scene: Mapping[str, Any], primary_props: Mapping[str, Any]) -> bool:
    """Recognize date facts across the native edit-plan representations.

    Media-first plans can express a chronology strip in ``media_composition``
    while the actual date marker lives in ``graphic.props`` or in the visible
    title copy.  Looking only at ``primary_visual.props`` incorrectly rejects
    those valid plans.
    """
    graphic = scene.get("graphic") if isinstance(scene.get("graphic"), Mapping) else {}
    graphic_props = graphic.get("props") if isinstance(graphic.get("props"), Mapping) else {}
    graphic_cue = graphic.get("cue") if isinstance(graphic.get("cue"), Mapping) else {}
    cue_props = graphic_cue.get("props") if isinstance(graphic_cue.get("props"), Mapping) else {}
    semantic = scene.get("semantic_beat") if isinstance(scene.get("semantic_beat"), Mapping) else {}

    date_keys = ("date", "year", "events", "eraSpan")
    for props in (primary_props, graphic_props, cue_props):
        if any(props.get(key) not in (None, "", [], {}) for key in date_keys):
            return True

    # Some title treatments render a grounded date as ordinary visible copy
    # instead of a dedicated date prop. Require a calendar year/date token in
    # both the script narration and that rendered copy, so prose mentioning a
    # date without displaying it does not satisfy the chronology requirement.
    narration = str(scene.get("narration") or scene.get("text") or "")
    visible_copy = " ".join(
        str(value)
        for props in (primary_props, graphic_props, cue_props)
        for key, value in props.items()
        if key in {"title", "text", "label", "kicker", "display_text"} and value
    )
    year_pattern = r"\b(?:18|19|20|21)\d{2}\b"
    script_years = set(re.findall(year_pattern, narration))
    rendered_years = set(re.findall(year_pattern, visible_copy))
    if script_years & rendered_years:
        return True

    emphasis = str(semantic.get("emphasis") or "")
    return bool(emphasis and emphasis in narration and emphasis in visible_copy and re.search(year_pattern, emphasis))


def audit_manifest(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    scenes = list(manifest.get("scenes") or [])
    findings: List[Dict[str, Any]] = []
    grammars: List[str] = []
    signatures: List[str] = []
    media_led = 0
    graphic_events = 0
    fps = max(1.0, _safe_float(manifest.get("fps"), 30.0))
    frame_tolerance = 1.01 / fps
    try:
        strict_timeline = int(manifest.get("timeline_contract_version") or 0) >= 1
    except (TypeError, ValueError):
        strict_timeline = False
    timeline_cursor = 0.0
    timeline_end = 0.0
    scenes_with_absolute_timing = 0
    checked_shots = 0
    checked_motion_cues = 0
    checked_sound_cues = 0
    for index, scene in enumerate(scenes):
        if not isinstance(scene, Mapping):
            findings.append({"severity": "error", "scene_index": index, "code": "scene_not_object"})
            continue
        has_timing = scene.get("duration") is not None
        scene_duration = _safe_float(scene.get("duration"), 0.0)
        scene_start = _safe_float(scene.get("start"), timeline_cursor)
        scene_end = _safe_float(scene.get("end"), scene_start + scene_duration)
        if scene.get("start") is not None:
            scenes_with_absolute_timing += 1
        if strict_timeline and (not has_timing or scene.get("start") is None):
            findings.append({"severity": "error", "scene_index": index, "code": "timeline_absolute_timing_missing"})
        if has_timing:
            if scene_duration <= 0:
                findings.append({"severity": "error", "scene_index": index, "code": "timeline_invalid_duration"})
            if scene_start < -frame_tolerance or scene_end <= scene_start:
                findings.append({"severity": "error", "scene_index": index, "code": "timeline_invalid_scene_bounds"})
            if abs((scene_end - scene_start) - scene_duration) > frame_tolerance:
                findings.append({"severity": "error", "scene_index": index, "code": "timeline_duration_mismatch"})
            if scene_start < timeline_cursor - frame_tolerance:
                findings.append({"severity": "error", "scene_index": index, "code": "timeline_scene_overlap"})
            elif scene_start > timeline_cursor + frame_tolerance and index:
                findings.append({"severity": "warning", "scene_index": index, "code": "timeline_scene_gap", "gap_seconds": round(scene_start - timeline_cursor, 3)})
            timeline_cursor = max(timeline_cursor, scene_end)
            timeline_end = max(timeline_end, scene_end)

        grammar = _scene_grammar(scene)
        grammars.append(grammar)
        primary = scene.get("primary_visual") or {}
        signature = (
            f"{str(primary.get('type') or '').lower()}:{str(primary.get('variant') or '').lower()}"
            if isinstance(primary, Mapping)
            else ":"
        )
        signatures.append(signature)
        props = primary.get("props") if isinstance(primary, Mapping) else {}
        props = props if isinstance(props, Mapping) else {}
        scene_text = str(scene.get("narration") or scene.get("text") or "").strip()
        primary_type = str(primary.get("type") or "") if isinstance(primary, Mapping) else ""
        media = scene.get("media") if isinstance(scene.get("media"), Mapping) else {}
        media_plan = scene.get("media_composition") if isinstance(scene.get("media_composition"), Mapping) else {}
        contract = scene.get("editorial_contract") if isinstance(scene.get("editorial_contract"), Mapping) else {}
        shots = media.get("shots") if isinstance(media.get("shots"), list) else []
        previous_shot_end = 0.0
        first_shot_start = None
        ordered_shots = sorted(
            (row for row in shots if isinstance(row, Mapping)),
            key=lambda row: _safe_float(row.get("shot_start", row.get("start", 0.0))),
        )
        for shot_index, shot in enumerate(ordered_shots):
            shot_start = _safe_float(shot.get("shot_start", shot.get("start", 0.0)), 0.0)
            shot_duration = _safe_float(shot.get("duration"), 0.0)
            shot_end = _safe_float(shot.get("shot_end", shot.get("end")), shot_start + shot_duration)
            if shot_end <= shot_start and shot_duration > 0:
                shot_end = shot_start + shot_duration
            if first_shot_start is None:
                first_shot_start = shot_start
            checked_shots += 1
            if has_timing and (shot_start < -frame_tolerance or shot_end <= shot_start or shot_end > scene_duration + frame_tolerance):
                findings.append({"severity": "error", "scene_index": index, "shot_index": shot_index, "code": "timeline_shot_out_of_bounds"})
            if shot_start < previous_shot_end - frame_tolerance:
                findings.append({"severity": "error", "scene_index": index, "shot_index": shot_index, "code": "timeline_shot_overlap"})
            elif shot_start > previous_shot_end + frame_tolerance and shot_index:
                findings.append({"severity": "error" if strict_timeline else "warning", "scene_index": index, "shot_index": shot_index, "code": "timeline_shot_gap"})
            previous_shot_end = max(previous_shot_end, shot_end)
        if strict_timeline and has_timing and ordered_shots and (
            first_shot_start is None
            or first_shot_start > frame_tolerance
            or previous_shot_end < scene_duration - frame_tolerance
        ):
            findings.append({"severity": "error", "scene_index": index, "code": "timeline_shots_do_not_cover_scene"})

        motion_cues = scene.get("motion_cues") if isinstance(scene.get("motion_cues"), list) else []
        if len(motion_cues) > 3:
            findings.append({"severity": "error", "scene_index": index, "code": "timeline_motion_cue_limit"})
        narration = str(scene.get("narration") or "")
        for cue_index, cue in enumerate(motion_cues):
            if not isinstance(cue, Mapping):
                continue
            checked_motion_cues += 1
            cue_start = _safe_float(cue.get("time_local"), -1.0)
            cue_duration = _safe_float(cue.get("duration_seconds"), 0.0)
            anchor = str(cue.get("anchor_text") or "").strip()
            if has_timing and (cue_start < 0 or cue_start >= scene_duration or cue_duration < 0 or cue_start + cue_duration > scene_duration + frame_tolerance):
                findings.append({"severity": "error", "scene_index": index, "cue_index": cue_index, "code": "timeline_motion_cue_out_of_bounds"})
            if anchor and anchor.casefold() not in narration.casefold():
                findings.append({"severity": "error", "scene_index": index, "cue_index": cue_index, "code": "timeline_motion_anchor_not_spoken"})

        if has_timing:
            sound_cues = scene.get("sound_cues") if isinstance(scene.get("sound_cues"), list) else []
            for sound_index, cue in enumerate(sound_cues):
                if not isinstance(cue, Mapping) or cue.get("start") is None:
                    continue
                checked_sound_cues += 1
                sound_start = _safe_float(cue.get("start"), -1.0)
                sound_duration = max(0.0, _safe_float(cue.get("duration"), 0.0))
                if sound_start < scene_start - frame_tolerance or sound_start >= scene_end + frame_tolerance:
                    findings.append({"severity": "error", "scene_index": index, "cue_index": sound_index, "code": "timeline_sound_cue_out_of_bounds"})
                elif sound_start + sound_duration > scene_end + 0.35:
                    findings.append({"severity": "warning", "scene_index": index, "cue_index": sound_index, "code": "timeline_sound_tail_exceeds_scene"})

        graphic = scene.get("graphic") if isinstance(scene.get("graphic"), Mapping) else {}
        if has_timing and (graphic.get("scene_local_start") is not None or graphic.get("scene_local_end") is not None):
            graphic_start = _safe_float(graphic.get("scene_local_start"), 0.0)
            graphic_end = _safe_float(graphic.get("scene_local_end"), graphic_start + _safe_float(graphic.get("duration"), 0.0))
            if graphic_start < -frame_tolerance or graphic_end <= graphic_start or graphic_end > scene_duration + frame_tolerance:
                findings.append({"severity": "error", "scene_index": index, "code": "timeline_graphic_out_of_bounds"})
        has_source_media = bool(media.get("src") or media.get("url") or media.get("path") or media.get("shots"))
        if primary_type.lower() in {"smartimage", "smartvideo", "image", "video"} or (
            has_source_media and media_plan.get("hero_layer", "source_media") == "source_media"
        ):
            media_led += 1
        primary_graphic_types = {
            "kinetictext", "statcard", "quotecard", "headline",
            "documenthighlight", "animatedmap", "timeline", "chart",
            "comparison", "collage", "personprofile", "diagram", "chaptertitle",
        }
        if contract.get("enabled") or primary_type.lower() in primary_graphic_types:
            graphic_events += 1
        if contract.get("enabled"):
            approved = str(contract.get("display_text") or "").strip()
            prop_text = str(props.get("display_text") or "").strip()
            if approved and prop_text and approved != prop_text:
                findings.append({"severity": "error", "scene_index": index, "code": "renderer_text_not_director_approved"})
            if media_plan.get("overlay_background") not in {None, "", "transparent", "none"}:
                findings.append({"severity": "error", "scene_index": index, "code": "boxed_overlay_background"})
            family = str(contract.get("family") or "")
            if media_plan.get("blur_media_underlay") and family != "data_chart":
                findings.append({"severity": "error", "scene_index": index, "code": "unearned_media_blur"})
            if family == "data_chart" and media_plan.get("blur_media_underlay") and not (
                media.get("requires_chart_contrast") or (media.get("visual_decision") or {}).get("requires_chart_contrast")
            ):
                findings.append({"severity": "warning", "scene_index": index, "code": "chart_blur_without_media_evidence"})
        if not scene_text:
            findings.append({"severity": "error", "scene_index": index, "code": "missing_narration"})
        has_data_payload = any(props.get(key) for key in (
            "value", "stat", "number", "data", "values", "currentValue", "before", "after",
        )) or bool(
            props.get("leftLabel") and props.get("leftValue")
            and props.get("rightLabel") and props.get("rightValue")
        )
        if grammar in {"data_story", "chart_center", "data_inset"} and not has_data_payload:
            findings.append({"severity": "error", "scene_index": index, "code": "data_without_payload"})
        if grammar in {"chronology", "chronology_strip"} and not _has_chronology_payload(scene, props):
            findings.append({"severity": "error", "scene_index": index, "code": "chronology_without_date"})
        spatial_variant = str(primary.get("variant") or "").lower() if isinstance(primary, Mapping) else ""
        has_signal_payload = any(token in spatial_variant for token in ("signal", "terminal", "console", "telemetry")) and any(props.get(key) for key in ("message", "text", "title"))
        if grammar == "spatial_atlas" and not has_signal_payload and not any(props.get(key) for key in ("from", "to", "start", "end", "country", "steps", "events")):
            findings.append({"severity": "warning", "scene_index": index, "code": "spatial_visual_is_abstract", "message": "Keep the locator abstract; do not imply an unsupported route."})
        if not props and primary_type and primary_type.lower() not in {"smartimage", "smartvideo"}:
            findings.append({"severity": "warning", "scene_index": index, "code": "graphic_without_props"})
    for index in range(1, len(grammars)):
        if signatures[index] == signatures[index - 1] and grammars[index] != "cinematic_type":
            findings.append({"severity": "warning", "scene_index": index, "code": "adjacent_grammar_repeat", "grammar": grammars[index], "signature": signatures[index]})
    # A long director-led storyboard should make room for the distinct
    # explanations its own narration earns. Count only clear factual/process
    # opportunities; atmosphere-heavy films and short social cuts are exempt.
    if len(scenes) >= 8 and any(
        isinstance(scene, Mapping) and scene.get("director_treatment_enabled")
        for scene in scenes
    ):
        story_opportunities = 0
        for scene_index, scene in enumerate(scenes):
            if not isinstance(scene, Mapping):
                continue
            narration = str(scene.get("narration") or "")
            mode = str(((scene.get("text") or {}).get("frame_mode") if isinstance(scene.get("text"), Mapping) else scene.get("frame_mode")) or "").lower()
            primary = scene.get("primary_visual") if isinstance(scene.get("primary_visual"), Mapping) else {}
            primary_kind = str(primary.get("type") or "").lower()
            if mode not in {"media_only", "kinetic", "chapter", ""}:
                continue
            if re.search(r"\b(?:\d{1,2}\s+)?(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?,?\s+(?:18|19|20|21)\d{2}\b|\b(?:18|19|20|21)\d{2}\b", narration, re.I):
                story_opportunities += 1
            if re.search(
                r"\b(?:because|therefore|as a result|which led to|caused|forced|triggered|turn(?:ed)? into|goes wrong|pulls .{0,35} into|compared with|versus|before .{0,35} after)\b",
                narration, re.I,
            ):
                story_opportunities += 1
            if primary_kind in {"smartimage", "smartvideo", "image", "video"}:
                media = scene.get("media") if isinstance(scene.get("media"), Mapping) else {}
                resolution = media.get("resolution") if isinstance(media.get("resolution"), Mapping) else {}
                if (str(media.get("source") or resolution.get("provider") or "").casefold() == "graphic"
                        or str(media.get("resolution_kind") or resolution.get("resolution_kind") or "").casefold() in {"grounded_graphic_fallback", "graphic_fallback"}):
                    findings.append({"severity": "warning", "scene_index": scene.get("scene_index", scene_index), "code": "uncomposed_graphic_fallback"})
        active_explanations = {
            grammar for grammar in grammars
            if grammar not in {"cinematic_type", "chapter_slate"}
        }
        if story_opportunities >= 2 and len(active_explanations) < 2:
            findings.append({
                "severity": "error",
                "code": "director_grammar_underdiversity",
                "story_opportunities": story_opportunities,
                "unique_explanatory_grammars": len(active_explanations),
                "message": "Use grounded explanatory components for clear date/process/data/place beats while keeping atmospheric beats media-led.",
            })
    # A graphic layered safely over footage is still a graphic event.  The
    # former count treated every media-first graphic as "media only", so the
    # wallpaper check could never catch a dense, repetitive edit.
    graphic_scenes = graphic_events
    if scenes and not manifest.get("qa_showcase") and graphic_scenes / len(scenes) > 0.72:
        findings.append({"severity": "warning", "code": "graphic_wallpaper_risk", "ratio": round(graphic_scenes / len(scenes), 3)})
    contract_audit = audit_editorial_contracts(manifest) if any(
        isinstance(scene, Mapping) and isinstance(scene.get("editorial_contract"), Mapping)
        for scene in scenes
    ) else None
    if contract_audit:
        for error in contract_audit.get("errors") or []:
            findings.append({"severity": "error", "code": "editorial_contract", "message": error})
        for warning in contract_audit.get("warnings") or []:
            findings.append({"severity": "warning", "code": "editorial_contract", "message": warning})
    errors = [item for item in findings if item.get("severity") == "error"]
    return {
        "status": "fail" if errors else "pass",
        "scene_count": len(scenes),
        "timeline": {
            "fps": fps,
            "duration_seconds": round(timeline_end, 3),
            "scenes_with_absolute_timing": scenes_with_absolute_timing,
            "shots_checked": checked_shots,
            "motion_cues_checked": checked_motion_cues,
            "sound_cues_checked": checked_sound_cues,
        },
        "media_led_scenes": media_led,
        "graphic_ratio": round(graphic_scenes / len(scenes), 3) if scenes else 0.0,
        "grammar_sequence": grammars,
        "signature_sequence": signatures,
        "unique_grammars": len(set(grammars)),
        "graphic_events": graphic_events,
        "editorial_contract": contract_audit,
        "findings": findings,
        "errors": len(errors),
        "warnings": len(findings) - len(errors),
    }


def intentional_transition_positions(frame_rows, transitions, fps=30):
    """Return one-based samples that land inside short, supported transitions.

    A transition can briefly contain a masked, partially covered, or nearly
    blank frame by design. Keep this exemption tied to a known Remotion
    transition and a short declared window; arbitrary labels and long holds
    must still pass the normal frame-quality gate.
    """
    try:
        rate = max(1.0, float(fps))
    except (TypeError, ValueError):
        rate = 30.0
    intentional = {
        'dipblack', 'diptoblack', 'dipwhite', 'diptowhite', 'blackflash', 'whiteflash',
        'glitchburst', 'glitchslice', 'zoomburst', 'scaletransition', 'lightleak',
        'filmburn', 'slide', 'directionalwipe', 'wipe', 'inkbleed', 'inkwipe',
        'pushblur', 'whippan', 'snapzoom', 'crossdissolve', 'lumadissolve',
        'lumaflash', 'splitreveal',
    }
    windows = []
    for transition in transitions or []:
        if not isinstance(transition, Mapping):
            continue
        kind = re.sub(r'[^a-z]', '', str(transition.get('template_id') or transition.get('id') or transition.get('type') or '').lower())
        if kind not in intentional:
            continue
        try:
            start = float(transition.get('start', float(transition.get('start_frame', 0)) / rate))
            duration = float(transition.get('duration', float(transition.get('duration_frames', 0)) / rate))
        except (TypeError, ValueError):
            continue
        # A long black pause is a real editing defect, even if a manifest
        # accidentally labels it as a transition. Only short transition beats
        # receive this sampling exemption.
        if not 0 < duration <= .4:
            continue
        windows.append((start, start + duration))
    return {
        index for index, row in enumerate(frame_rows or [], start=1)
        if isinstance(row, Mapping)
        and any(start - .001 <= float(row.get('frame', -1)) / rate <= end + .001 for start, end in windows)
    }


def audit_frames(frame_paths: Iterable[str | Path], *, intentional_transition_positions=()) -> Dict[str, Any]:
    metrics = [_frame_metrics(Path(path)) for path in frame_paths]
    intentional_positions = {int(value) for value in intentional_transition_positions if str(value).isdigit() and int(value) > 0}
    # Exact t=0 captures can be black while Chrome/FFmpeg is opening the first
    # media stream. Treat only that first-frame decode artifact as non-fatal;
    # low-information frames later in the timeline remain hard failures.
    opening_decode_frame = bool(
        len(metrics) > 1
        and metrics[0].get("low_information")
        and float(metrics[0].get("mean_luma") or 1.0) < 0.07
    )
    failures = [
        item for index, item in enumerate(metrics)
        if item.get("low_information") and index + 1 not in intentional_positions
        and not (opening_decode_frame and index == 0)
    ]
    dark_frames = [item for item in metrics if item.get("excessively_dark")]
    soft_frames = [item for item in metrics if item.get("flat_or_soft") and not item.get("low_information")]
    count = max(1, len(metrics))
    findings = []
    unavailable = [item for item in metrics if item.get('status') != 'pass']
    if unavailable or not metrics:
        findings.append({'severity':'error', 'code':'review_frames_unavailable', 'count':len(unavailable),
                         'message':'Requested review frames could not be decoded. A missing pixel review cannot pass.'})
    if failures:
        findings.append({
            "severity": "error",
            "code": "low_information_frames",
            "count": len(failures),
            "frame_indices": [
                index + 1
                for index, item in enumerate(metrics)
                if item.get("low_information")
                and index + 1 not in intentional_positions
                and not (opening_decode_frame and index == 0)
            ],
            "message": "One or more sampled frames have too little visible detail; inspect the listed frames and strengthen the scene visual.",
        })
    if dark_frames:
        findings.append({
            "severity": "warning",
            "code": "excessive_darkness",
            "count": len(dark_frames),
            "message": "Raise the base media grade or remove generic dark overlays; reserve deep blacks for intentional transitions.",
        })
    if soft_frames:
        findings.append({
            "severity": "warning",
            "code": "soft_or_blurred_frames",
            "count": len(soft_frames),
            "message": "Keep blur limited to centered data underlays; source media should remain sharp and visible.",
        })
    errors = [item for item in findings if item.get("severity") == "error"]
    return {
        "status": "fail" if failures or errors else "pass",
        "frames": metrics,
        "low_information_frames": len(failures),
        "dark_frames": len(dark_frames),
        "soft_frames": len(soft_frames),
        "ignored_opening_decode_frames": 1 if opening_decode_frame else 0,
        "ignored_intentional_transition_frames": sum(
            1 for index, item in enumerate(metrics, start=1)
            if index in intentional_positions and item.get('low_information')
        ),
        "findings": findings,
    }


def audit(manifest_path: str | Path, frame_paths: Optional[Iterable[str | Path]] = None) -> Dict[str, Any]:
    path = Path(manifest_path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    report = {"manifest": str(path), "manifest_audit": audit_manifest(manifest)}
    if frame_paths is not None:
        report["frame_audit"] = audit_frames(frame_paths)
    report["status"] = "fail" if any(value.get("status") == "fail" for value in report.values() if isinstance(value, Mapping)) else "pass"
    return report


__all__ = ["audit", "audit_frames", "audit_manifest"]
