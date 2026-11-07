"""A deterministic director swarm for media-led documentary scenes.

The swarm is intentionally small and inspectable. It gives each scene a
source-analysis decision, a media composition decision, a graphic grammar,
and a motion family before the manager/vision critic gets the final say.
This keeps LLM suggestions useful without allowing them to turn every beat
into the same decorative card.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Mapping, Sequence

from .media_director import audit_media_compositions, plan_media_composition
from .visual_critic import audit_manifest
from .media_first import apply_media_first_direction, audit_editorial_contracts


SWARM_RULES: Dict[str, Any] = {
    "version": "documentary-swarm-v2",
    "media_priority": "source_media_first",
    "max_overlay_area": 0.28,
    "max_overlay_opacity": 0.82,
    "max_overlay_count": 1,
    "adjacent_grammar_cooldown": 1,
    "grammar_cooldown": 3,
    "same_variant_cooldown": 4,
    "hero_cooldown": 2,
    "motion_cooldown": 2,
    "rolling_window_graphics": 4,
    "no_box_backgrounds": True,
    "text_must_fit_safe_region": True,
}

AGENTS = (
    "source_analyzer",
    "media_editor",
    "graphic_director",
    "motion_designer",
    "vision_critic",
    "manager",
)

HIGH_SALIENCE = {"quote_pull", "locator_inset", "data_inset", "chart_center", "chapter_slate"}

MOTION_BY_ROLE = {
    "opening_locator": ["date_stamp", "pin_drop", "quiet_hold"],
    "stat": ["number_count", "underline_draw", "quiet_hold"],
    "quote": ["quote_reveal", "soft_mask", "quiet_hold"],
    "chapter": ["chapter_wipe", "rule_extend", "quiet_hold"],
    "locator": ["route_draw", "pin_drop", "quiet_hold"],
    "archive": ["contact_sheet_slide", "scan_reveal", "quiet_hold"],
    "portrait": ["lower_third_slide", "portrait_pin", "quiet_hold"],
    "evidence": ["bracket_trace", "source_stamp", "quiet_hold"],
    "analytic": ["signal_trace", "marker_pop", "quiet_hold"],
    "field_caption": ["caption_slide", "rule_extend", "quiet_hold"],
}


def _primary(scene: Mapping[str, Any]) -> Mapping[str, Any]:
    value = scene.get("primary_visual")
    return value if isinstance(value, Mapping) else {}


def _props(scene: Mapping[str, Any]) -> Mapping[str, Any]:
    value = _primary(scene).get("props")
    return value if isinstance(value, Mapping) else {}


def _has_media(scene: Mapping[str, Any]) -> bool:
    media = scene.get("media") if isinstance(scene.get("media"), Mapping) else {}
    return bool(media.get("src") or media.get("url") or media.get("path") or media.get("shots"))


def _text(scene: Mapping[str, Any]) -> str:
    return " ".join(str(scene.get(key) or "") for key in ("narration", "text")).lower()


def source_analyzer(scene: Mapping[str, Any]) -> Dict[str, Any]:
    """Classify the editorial job of a scene from grounded fields only."""

    primary = _primary(scene)
    props = _props(scene)
    kind = str(primary.get("type") or "").lower()
    variant = str(primary.get("variant") or "").lower().replace("_", "-")
    text = _text(scene)
    if not kind or kind in {"smartimage", "smartvideo", "image", "video"}:
        role = "media_hold"
    elif kind in {"chaptertitle", "chapter", "textcard"} and any(token in variant for token in ("chapter", "act", "opener", "slate")):
        role = "chapter"
    elif kind in {"quotecard", "quote"} or "quote" in variant or props.get("quote"):
        role = "quote"
    elif kind in {"statcard", "chart"}:
        role = "stat"
    elif kind in {"animatedmap"} or any(token in variant for token in ("map", "route", "locator")):
        role = "locator"
    elif kind in {"timeline"} or any(token in variant for token in ("date", "chron", "history")):
        role = "opening_locator" if not scene.get("scene_index") else "archive"
    elif kind in {"collage"} or any(token in variant for token in ("contact", "gallery", "archive", "photo")):
        role = "archive"
    elif kind in {"personprofile"} or any(token in variant for token in ("profile", "portrait", "lower-third", "dossier")):
        role = "portrait"
    elif kind in {"diagram", "comparison"} or any(token in variant for token in ("signal", "terminal", "console", "flow")):
        role = "analytic"
    elif kind in {"documenthighlight", "headline"} or any(token in variant for token in ("evidence", "document", "press", "source")):
        role = "evidence"
    elif any(token in text for token in ("on the ", "in ", "headquarters", "finland", "london", "new york")) and int(scene.get("scene_index") or 0) == 0:
        role = "opening_locator"
    else:
        role = "field_caption"
    return {
        "agent": "source_analyzer",
        "beat_role": role,
        "primary_type": str(primary.get("type") or "SmartImage"),
        "grounded_payload": bool(props) or kind in {"smartimage", "smartvideo", "image", "video"},
        "narration_length": len(_text(scene).split()),
        "media_present": _has_media(scene),
    }


def media_editor(scene: Mapping[str, Any]) -> Dict[str, Any]:
    """Keep the source clip/photo dominant and return its safe placement brief."""

    plan = scene.get("media_composition") if isinstance(scene.get("media_composition"), Mapping) else plan_media_composition(scene)
    has_media = _has_media(scene)
    return {
        "agent": "media_editor",
        "hero_layer": plan.get("hero_layer") or ("source_media" if has_media else "graphic"),
        "render_mode": str(plan.get("render_mode") or ("media_only" if has_media else "graphic_only")),
        "media_visibility_floor": 0.78 if has_media and plan.get("hero_layer") == "source_media" else 0.0,
        "safe_region": list(plan.get("safe_region") or []),
        "subject_bbox": list(plan.get("subject_bbox") or []),
        "overlay_area_budget": float(plan.get("overlay_max_area") or 0.18) if has_media else 1.0,
        "collision_policy": str(plan.get("collision_policy") or "none"),
    }


def graphic_director(scene: Mapping[str, Any], beat: Mapping[str, Any], media: Mapping[str, Any]) -> Dict[str, Any]:
    """Select an open, semantic graphic grammar rather than a generic card."""

    role = str(beat.get("beat_role") or "field_caption")
    grammar_by_role = {
        "media_hold": "none",
        "opening_locator": "chapter_slate",
        "stat": "data_inset",
        "quote": "quote_pull",
        "chapter": "chapter_slate",
        "locator": "locator_inset",
        "archive": "archive_contact_sheet",
        "portrait": "portrait_id",
        "evidence": "evidence_annotation",
        "analytic": "analytic_callout",
        "field_caption": "field_caption",
    }
    grammar = "chart_center" if str(beat.get("primary_type") or "").lower() == "chart" else grammar_by_role.get(role, "field_caption")
    try:
        importance = float((scene.get("pacing") or {}).get("importance") or 0.0)
    except (TypeError, ValueError):
        importance = 0.0
    # Centre is reserved for a script-level thesis, chapter hook, or an
    # earned quote.  Position may never be selected from a scene-number
    # cadence: that creates decorative title cards unrelated to the story.
    central_hero = bool(
        grammar == "chart_center"
        or (role == "chapter" and importance >= 0.88)
        or (role == "quote" and importance >= 0.82 and not media.get("subject_bbox"))
        or (
            role in {"field_caption", "evidence", "portrait"}
            and importance >= 0.9
            and not media.get("subject_bbox")
        )
    )
    if not media.get("hero_layer") == "source_media":
        priority = "primary" if grammar != "field_caption" else "supporting"
    else:
        priority = "supporting"
    graphic_background = {"background": "none"} if grammar == "none" else {"background": "transparent"}
    return {
        "agent": "graphic_director",
        "grammar": grammar,
        "priority": "source_only" if grammar == "none" else priority,
        "scale": "none" if grammar == "none" else ("hero" if central_hero else ("micro" if grammar in {"data_inset", "locator_inset", "portrait_id"} else "supporting")),
        "geometry": "none" if grammar == "none" else ("open" if grammar != "archive_contact_sheet" else "edge_strip"),
        **graphic_background,
        "placement": "none" if grammar == "none" else ("center" if central_hero else "corner"),
    }


def _choose_motion(role: str, scene_index: int, history: Sequence[str]) -> str:
    candidates = MOTION_BY_ROLE.get(role, MOTION_BY_ROLE["field_caption"])
    recent = set(history[-SWARM_RULES["motion_cooldown"] :])
    for offset in range(len(candidates)):
        candidate = candidates[(scene_index + offset) % len(candidates)]
        if candidate not in recent:
            return candidate
    return candidates[scene_index % len(candidates)]


def motion_designer(scene: Mapping[str, Any], beat: Mapping[str, Any], history: Sequence[str]) -> Dict[str, Any]:
    role = str(beat.get("beat_role") or "field_caption")
    if role == "media_hold":
        return {
            "agent": "motion_designer",
            "motion_family": "quiet_hold",
            "dominant_motion": "source_clip" if _has_media(scene) else "none",
            "intensity": "low",
            "reason": "script_did_not_request_graphics",
        }
    scene_index = int(scene.get("scene_index") or 0)
    motion = _choose_motion(role, scene_index, history)
    return {
        "agent": "motion_designer",
        "motion_family": motion,
        "dominant_motion": "source_clip" if _has_media(scene) else motion,
        "intensity": "low" if _has_media(scene) else "medium",
        "reason": f"semantic_{role}",
    }


def _sequence_audit(scene_plans: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    grammars: List[str] = []
    motions: List[str] = []
    for index, plan in enumerate(scene_plans):
        grammar = str(plan.get("graphic", {}).get("grammar") or "")
        motion = str(plan.get("motion", {}).get("motion_family") or "")
        grammars.append(grammar)
        motions.append(motion)
        if plan.get("media", {}).get("hero_layer") == "source_media":
            if plan.get("media", {}).get("media_visibility_floor", 0) < 0.78:
                errors.append(f"scene {index}: media visibility floor is below 0.78")
            if plan.get("graphic", {}).get("geometry") == "filled_panel":
                errors.append(f"scene {index}: filled panel is forbidden over source media")
        if index and grammar and grammar == grammars[index - 1] and grammar in HIGH_SALIENCE:
            errors.append(f"scene {index}: high-salience grammar repeated adjacent to previous scene")
        if index and motion and motion == motions[index - 1]:
            warnings.append(f"scene {index}: motion family repeated adjacent to previous scene")
        if index >= SWARM_RULES["high_salience_cooldown"] and grammar in HIGH_SALIENCE:
            recent = grammars[index - SWARM_RULES["high_salience_cooldown"] : index]
            if grammar in recent:
                warnings.append(f"scene {index}: {grammar} is inside its salience cooldown")
        if index >= SWARM_RULES["grammar_cooldown"] and grammar not in {"", "none", "field_caption"}:
            recent = grammars[index - SWARM_RULES["grammar_cooldown"] : index]
            if grammar in recent:
                warnings.append(f"scene {index}: {grammar} repeats inside the editorial grammar cooldown")
    # Long documentaries are allowed to return to a grammar later.  The v2
    # editor audits rolling fingerprint windows instead of imposing an
    # impossible global-uniqueness percentage on a coherent sequence.
    return {
        "status": "fail" if errors else "pass",
        "errors": errors,
        "warnings": warnings,
        "grammar_sequence": grammars,
        "motion_sequence": motions,
        "unique_grammars": len(set(grammars)),
        "unique_motion_families": len(set(motions)),
    }


def run_documentary_swarm(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """Create the canonical media-first scene contract and audit it.

    The old v1 path decided centre hierarchy from ``scene_index % 5`` and
    degraded a repeated visual into another generic field caption.  v2 first
    asks whether a graphic is earned, then schedules genuinely distinct
    composition families with explicit cooldowns.  It is deterministic so a
    saved project remains reproducible across preview, retry, and export.
    """

    result = apply_media_first_direction(manifest, plan_media_composition)
    scene_plans = [
        scene.get("swarm_plan")
        for scene in result.get("scenes") or []
        if isinstance(scene, Mapping) and isinstance(scene.get("swarm_plan"), Mapping)
    ]
    contract_audit = audit_editorial_contracts(result)
    grammar_sequence = [
        str((plan.get("graphic") or {}).get("grammar") or "none")
        for plan in scene_plans
    ]
    motion_sequence = [
        str((plan.get("motion") or {}).get("motion_family") or "quiet_hold")
        for plan in scene_plans
    ]
    sequence_audit = {
        "status": contract_audit["status"],
        "errors": list(contract_audit["errors"]),
        "warnings": list(contract_audit["warnings"]),
        "grammar_sequence": grammar_sequence,
        "motion_sequence": motion_sequence,
        "family_sequence": list(contract_audit["family_sequence"]),
        "composition_fingerprints": list(contract_audit["composition_fingerprints"]),
        "unique_grammars": len(set(grammar_sequence)),
        "unique_motion_families": len(set(motion_sequence)),
        "unique_families": contract_audit["unique_families"],
        "graphic_duration_ratio": contract_audit["graphic_duration_ratio"],
    }
    result["visual_system"] = {
        **(result.get("visual_system") if isinstance(result.get("visual_system"), Mapping) else {}),
        "documentary_swarm": "documentary-swarm-v2",
        "media_policy": "media-first-overlay-v2",
    }
    result["swarm_plan"] = {
        "version": SWARM_RULES["version"],
        "agents": list(AGENTS),
        "rules": dict(SWARM_RULES),
        "scenes": scene_plans,
        "sequence_audit": sequence_audit,
    }
    result["metrics"] = {
        **(result.get("metrics") if isinstance(result.get("metrics"), Mapping) else {}),
        "documentary_swarm": {
            "scene_count": len(scene_plans),
            "unique_grammars": sequence_audit["unique_grammars"],
            "unique_motion_families": sequence_audit["unique_motion_families"],
            "unique_families": sequence_audit["unique_families"],
            "graphic_duration_ratio": sequence_audit["graphic_duration_ratio"],
            "status": sequence_audit["status"],
        },
    }
    from .investigative_agents import investigate_director_decisions

    investigation = investigate_director_decisions(result)
    result["swarm_audit"] = {
        "sequence": sequence_audit,
        "manifest": audit_manifest(result),
        "media": audit_media_compositions(result),
        "investigators": investigation,
        "status": "fail" if any(item.get("status") == "fail" for item in (sequence_audit, investigation)) else "pass",
    }
    return result


__all__ = [
    "AGENTS",
    "SWARM_RULES",
    "graphic_director",
    "media_editor",
    "motion_designer",
    "run_documentary_swarm",
    "source_analyzer",
]
