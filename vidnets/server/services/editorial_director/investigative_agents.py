"""Investigative quality agents that challenge the director's decisions.

These checks are deliberately adversarial: they ask whether a graphic was
actually earned by the script, whether a map has real endpoints, whether a
chart has data, and whether placement follows the documentary layout policy.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping


MEDIA_ONLY_TYPES = {"smartimage", "smartvideo", "image", "video"}
CHART_GRAMMARS = {"chart_center", "data_inset"}
MAP_GRAMMARS = {"locator_inset"}
CENTRAL_HEADLINE_GRAMMARS = {"field_caption", "evidence_annotation", "chapter_slate", "quote_pull", "analytic_callout"}


def _primary(scene: Mapping[str, Any]) -> Mapping[str, Any]:
    value = scene.get("primary_visual")
    return value if isinstance(value, Mapping) else {}


def _props(scene: Mapping[str, Any]) -> Mapping[str, Any]:
    value = _primary(scene).get("props")
    return value if isinstance(value, Mapping) else {}


def _has_media(scene: Mapping[str, Any]) -> bool:
    media = scene.get("media") if isinstance(scene.get("media"), Mapping) else {}
    return bool(media.get("src") or media.get("path") or media.get("url") or media.get("shots"))


def _has_chart_payload(props: Mapping[str, Any]) -> bool:
    return any(props.get(key) for key in ("value", "stat", "number", "data", "values", "currentValue", "before", "after"))


def _has_map_payload(props: Mapping[str, Any]) -> bool:
    return bool(
        props.get("points")
        or props.get("locations")
        or props.get("country")
        or props.get("from")
        or props.get("to")
        or props.get("start")
        or props.get("end")
    )


def investigate_director_decisions(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """Return an approval report for the director's scene-level choices."""

    errors: List[str] = []
    warnings: List[str] = []
    decisions: List[Dict[str, Any]] = []
    graphic_scene_count = 0
    for index, scene in enumerate(manifest.get("scenes") or []):
        if not isinstance(scene, Mapping):
            errors.append(f"scene {index}: scene is not an object")
            continue
        primary = _primary(scene)
        props = _props(scene)
        primary_type = str(primary.get("type") or "").lower()
        swarm = scene.get("swarm_plan") if isinstance(scene.get("swarm_plan"), Mapping) else {}
        graphic = swarm.get("graphic") if isinstance(swarm.get("graphic"), Mapping) else {}
        composition = scene.get("media_composition") if isinstance(scene.get("media_composition"), Mapping) else {}
        contract = scene.get("editorial_contract") if isinstance(scene.get("editorial_contract"), Mapping) else {}
        grammar = str(graphic.get("grammar") or composition.get("overlay_grammar") or "none").lower()
        placement = str(graphic.get("placement") or composition.get("placement") or ("center" if grammar == "chart_center" else "corner")).lower()
        media_present = _has_media(scene)
        explanatory_canvas = composition.get("render_mode") == "explanatory_canvas"
        should_be_media_only = not primary or primary_type in MEDIA_ONLY_TYPES

        if should_be_media_only and grammar != "none":
            errors.append(f"scene {index}: director added {grammar} to a media-only beat without a script visual request")
        if grammar != "none":
            graphic_scene_count += 1
            if contract:
                approved_text = str(contract.get("display_text") or "").strip()
                if approved_text and str(props.get("display_text") or "").strip() != approved_text:
                    errors.append(f"scene {index}: renderer props do not match director-approved wording")
                if composition.get("overlay_background") not in {None, "", "none", "transparent"}:
                    errors.append(f"scene {index}: a filled overlay background is forbidden")
                if not contract.get("timing", {}).get("enabled"):
                    errors.append(f"scene {index}: graphic is visible without readable timing")
        if grammar in CHART_GRAMMARS and not _has_chart_payload(props):
            errors.append(f"scene {index}: chart decision has no grounded data payload")
        location = contract.get("location") if isinstance(contract.get("location"), Mapping) else {}
        if grammar in MAP_GRAMMARS and not (_has_map_payload(props) or location):
            errors.append(f"scene {index}: map decision has no location, country, point, or endpoint payload")
        if grammar in MAP_GRAMMARS:
            origin = str(location.get("from") or props.get("from") or props.get("start") or "").strip().lower()
            destination = str(location.get("to") or props.get("to") or props.get("end") or "").strip().lower()
            if origin and destination and origin == destination:
                errors.append(f"scene {index}: map route repeats the same endpoint; use a single locator")
        if grammar == "chart_center" and placement != "center" and not explanatory_canvas:
            errors.append(f"scene {index}: chart is not centered as required")
        central_headline = placement == "center" and grammar in CENTRAL_HEADLINE_GRAMMARS and str(graphic.get("scale") or composition.get("overlay_scale") or "") == "hero"
        if grammar not in {"none", "chart_center", "analytic_callout"} and placement != "corner" and not central_headline and not explanatory_canvas:
            errors.append(f"scene {index}: heading/overlay escaped the corner placement policy")
        if media_present and grammar == "chart_center" and composition.get("blur_media_underlay"):
            plan = scene.get("media_composition") if isinstance(scene.get("media_composition"), Mapping) else {}
            decision = scene.get("media", {}).get("visual_decision") if isinstance(scene.get("media"), Mapping) else {}
            if not (isinstance(decision, Mapping) and decision.get("requires_chart_contrast")):
                warnings.append(f"scene {index}: chart blur is not backed by a media contrast finding")
        if media_present and grammar == "none":
            warnings.append(f"scene {index}: source media carries the beat without added graphics")
        decisions.append({
            "scene_index": index,
            "script_authority": "media_hold" if grammar == "none" else "semantic_visual",
            "grammar": grammar,
            "placement": placement,
            "has_media": media_present,
        })

    status = "fail" if errors else "pass"
    return {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "decisions": decisions,
        "graphic_scene_count": graphic_scene_count,
        "media_hold_count": sum(item["grammar"] == "none" for item in decisions),
        "ready_for_long_render": status == "pass",
    }


__all__ = ["investigate_director_decisions"]
