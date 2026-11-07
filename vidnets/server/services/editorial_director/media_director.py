"""Media-first composition rules for documentary scenes.

The media pipeline owns the frame. Graphics are editorial instruments placed
around the verified subject, never a replacement for the shot unless the
scene has no usable media or the Director explicitly requests a graphic-only
beat.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


MEDIA_FIRST_RULES: Dict[str, Any] = {
    "hero_layer": "source_media",
    "minimum_media_visibility": 0.78,
    "maximum_overlay_area": 0.28,
    "maximum_overlay_opacity": 0.88,
    "maximum_overlay_count": 2,
    "avoid_subject_intersection": True,
    "use_safe_text_regions": True,
    "allow_full_frame_graphic_with_media": False,
    "preferred_graphic_scale": "supporting",
}

EXPLANATORY_TYPES = frozenset({
    "chart", "timeline", "diagram", "processflow", "comparison", "animatedmap",
    "satellitemap", "documenthighlight", "kinetictext", "chaptertitle",
})
MAP_CANVAS_TYPES = frozenset({"animatedmap", "satellitemap"})


def owns_explanatory_canvas(scene: Mapping[str, Any]) -> bool:
    primary = scene.get("primary_visual") or {}
    kind = str(primary.get("type") or "").lower()
    role = str(scene.get("visual_role") or "").strip().lower()
    props = primary.get('props') or {}
    if kind not in MAP_CANVAS_TYPES and props.get('transparent_canvas') is True and _has_media(scene):
        return False
    # A map is itself the visual evidence: give it a stable, uninterrupted
    # cartographic canvas. Other specialist graphics overlay source footage by
    # default. They may take the full canvas only when the director explicitly
    # asks for a primary explanation; this also lets a deliberate cold-open
    # kinetic statement own the frame instead of shrinking into a caption.
    return kind in MAP_CANVAS_TYPES or (
        kind in EXPLANATORY_TYPES
        and role in {"primary_explanation", "full_canvas", "explanatory_canvas"}
    )


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _box(value: Any) -> Optional[List[float]]:
    if isinstance(value, Mapping):
        try:
            x, y = float(value["x"]), float(value["y"])
            value = [x, y, x + float(value["width"]), y + float(value["height"])]
        except (KeyError, TypeError, ValueError):
            return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    values = [_number(part, -1.0) for part in value]
    if not all(0.0 <= part <= 1.0 for part in values):
        return None
    x1, y1, x2, y2 = values
    if x2 <= x1 or y2 <= y1:
        return None
    return [round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)]


def _area(box: Sequence[float]) -> float:
    return max(0.0, float(box[2] - box[0])) * max(0.0, float(box[3] - box[1]))


def _intersection(a: Sequence[float], b: Sequence[float]) -> float:
    x1 = max(float(a[0]), float(b[0]))
    y1 = max(float(a[1]), float(b[1]))
    x2 = min(float(a[2]), float(b[2]))
    y2 = min(float(a[3]), float(b[3]))
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _inflate(box: Sequence[float], margin: float = 0.035) -> List[float]:
    return [
        max(0.0, float(box[0]) - margin),
        max(0.0, float(box[1]) - margin),
        min(1.0, float(box[2]) + margin),
        min(1.0, float(box[3]) + margin),
    ]


def _media(scene: Mapping[str, Any]) -> Mapping[str, Any]:
    value = scene.get("media")
    return value if isinstance(value, Mapping) else {}


def _visual_decision(scene: Mapping[str, Any]) -> Mapping[str, Any]:
    media = _media(scene)
    value = media.get("visual_decision") or (scene.get("primary_visual") or {}).get("asset", {}).get("visual_decision")
    return value if isinstance(value, Mapping) else {}


def _subject_box(scene: Mapping[str, Any]) -> Optional[List[float]]:
    media = _media(scene)
    decision = _visual_decision(scene)
    for value in (
        scene.get("focal_bbox"),
        (scene.get("design") or {}).get("focal_bbox") if isinstance(scene.get("design"), Mapping) else None,
        decision.get("subject_bbox"),
        decision.get("subject_box"),
        decision.get("focal_bbox"),
        media.get("subject_bbox"),
        media.get("focal_bbox"),
    ):
        normalized = _box(value)
        if normalized:
            return normalized
    return None


def _safe_regions(scene: Mapping[str, Any]) -> List[List[float]]:
    media = _media(scene)
    decision = _visual_decision(scene)
    design = scene.get("design") if isinstance(scene.get("design"), Mapping) else {}
    candidates: Iterable[Any] = (
        decision.get("safe_text_regions"),
        media.get("safe_text_regions"),
        design.get("safe_text_regions"),
    )
    result: List[List[float]] = []
    for group in candidates:
        if not isinstance(group, (list, tuple)):
            continue
        for value in group:
            normalized = _box(value)
            if normalized and normalized not in result:
                result.append(normalized)
    return result


def choose_media_safe_region(scene: Mapping[str, Any]) -> List[float]:
    """Choose an editorial region clear of the subject and enabled captions."""

    subject = _inflate(_subject_box(scene) or [0.42, 0.28, 0.58, 0.72])
    candidates = _safe_regions(scene)
    candidates.extend([
        [0.06, 0.06, 0.39, 0.22],
        [0.61, 0.06, 0.94, 0.22],
        [0.06, 0.76, 0.48, 0.94],
        [0.52, 0.76, 0.94, 0.94],
        [0.06, 0.38, 0.36, 0.61],
        [0.64, 0.38, 0.94, 0.61],
    ])

    ranked: List[Tuple[float, List[float]]] = []
    for candidate in candidates:
        normalized = _box(candidate)
        if not normalized:
            continue
        if scene.get("captions_enabled") and normalized[3] > .80:
            continue
        overlap = _intersection(normalized, subject)
        score = _area(normalized) - (overlap * 6.0)
        if overlap <= 0.002:
            score += 2.0
        ranked.append((score, normalized))
    if ranked:
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked[0][1]
    return [0.06, 0.76, 0.48, 0.94]


def _media_type(scene: Mapping[str, Any]) -> str:
    media = _media(scene)
    raw = str(media.get("type") or ("video" if media.get("is_video") else "image")).lower()
    return "video" if raw.startswith("video") else "image"


def _has_media(scene: Mapping[str, Any]) -> bool:
    media = _media(scene)
    return bool(str(media.get("src") or media.get("url") or media.get("path") or "").strip() or media.get("shots"))


def _overlay_grammar(scene: Mapping[str, Any]) -> str:
    primary = scene.get("primary_visual") if isinstance(scene.get("primary_visual"), Mapping) else {}
    props = primary.get("props") if isinstance(primary.get("props"), Mapping) else {}
    contract = props.get("editorial_contract") if isinstance(props.get("editorial_contract"), Mapping) else {}
    # The media-first director is authoritative for new manifests.  Legacy
    # type inference remains below only for projects saved before v2.
    # Director copy contracts carry provenance and approved words only. They
    # are not composition contracts, so they must not turn every specialist
    # (KineticText, map, evidence, etc.) into the generic "none" grammar.
    if contract and any(key in contract for key in ("enabled", "family", "legacy_grammar")):
        if not contract.get("enabled"):
            return "none"
        grammar = str(contract.get("legacy_grammar") or "").strip().lower()
        if grammar:
            return grammar
    kind = str(primary.get("type") or "").lower()
    variant = str(primary.get("variant") or "").lower().replace("_", "-")
    # These are media-led editorial instruments. They intentionally map to
    # open geometry in Remotion rather than the old filled-card templates.
    if kind in {"quotecard", "quote"} or "quote" in variant:
        return "quote_pull"
    if kind in {"chaptertitle", "chapter"} or any(token in variant for token in ("chapter", "act", "opener", "slate")):
        return "chapter_slate"
    if kind in {"collage"} or any(token in variant for token in ("contact", "gallery", "archive", "photo")):
        return "archive_contact_sheet"
    if kind in {"personprofile"} or any(token in variant for token in ("profile", "portrait", "lower-third")):
        return "portrait_id"
    if kind in {"chart"}:
        return "chart_center"
    if kind in {"statcard"}:
        return "data_inset"
    if kind in {"animatedmap"} or "map" in variant or "route" in variant:
        return "locator_inset"
    if kind in {"timeline"} or "date" in variant or "chron" in variant:
        return "chronology_strip"
    if kind in {"documenthighlight", "headline", "quoteCard".lower(), "personprofile"} or any(token in variant for token in ("document", "evidence", "quote", "dossier", "press")):
        return "evidence_annotation"
    if kind in {"diagram", "comparison"} or any(token in variant for token in ("signal", "terminal", "console", "flow", "before-after")):
        return "analytic_callout"
    return "field_caption"


def plan_media_composition(scene: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the render contract consumed by Remotion and the quality manager."""

    media_present = _has_media(scene)
    primary = scene.get("primary_visual") if isinstance(scene.get("primary_visual"), Mapping) else {}
    primary_type = str(primary.get("type") or "SmartImage")
    standalone = primary_type.lower() in {"smartimage", "smartvideo", "image", "video"}
    if owns_explanatory_canvas(scene):
        is_map = primary_type.lower() in MAP_CANVAS_TYPES
        return {
            "enabled": True, "hero_layer": "graphic", "render_mode": "explanatory_canvas",
            "component_type": primary_type,
            "overlay_grammar": "none", "allow_full_frame_graphic": True,
            "media_visibility": 0.0, "placement": "full_canvas",
            "blur_media_underlay": False, "safe_region": [0.06, 0.09, 0.94, 0.75],
            "collision_policy": "reserve_copy_and_caption_bands",
            "caption_region": [0.08, 0.83, 0.92, 0.97],
            "canvas_reason": "grounded_map" if is_map else "director_primary_explanation",
        }
    if not media_present:
        return {
            "enabled": False,
            "hero_layer": "graphic",
            "render_mode": "graphic_only",
            "component_type": primary_type,
            "overlay_grammar": "none",
            "media_visibility": 0.0,
        }

    safe_region = choose_media_safe_region(scene)
    focal = _subject_box(scene)
    grammar = "none" if standalone else _overlay_grammar(scene)
    decision = _visual_decision(scene)
    raw_text_tone = decision.get("text_tone") or _media(scene).get("text_tone") or "light"
    text_tone = str(raw_text_tone).strip().lower()
    if text_tone not in {"light", "dark"}:
        text_tone = "light"
    props = primary.get("props") if isinstance(primary.get("props"), Mapping) else {}
    contract = props.get("editorial_contract") if isinstance(props.get("editorial_contract"), Mapping) else {}
    # A centered data explanation is allowed, but it does not automatically
    # darken or blur the footage.  The asset/vision pipeline must explicitly
    # request local contrast treatment after examining the actual media.
    central_chart = bool(contract.get("family") == "data_chart" or grammar == "chart_center")
    placement = "center" if central_chart else "corner"
    if central_chart:
        # A chart is an explanatory instrument, not a corner label. The
        # source image remains present underneath, but is deliberately softened
        # so the actual data can be read without adding a giant opaque card.
        safe_region = [0.19, 0.20, 0.81, 0.80]
    visibility = 0.92 if _media_type(scene) == "video" else 0.88
    # The overlay is deliberately bounded. A data callout can be slightly
    # denser than a caption, but neither may become a second full-screen edit.
    max_area = 0.28 if central_chart else (0.24 if grammar in {"data_inset", "locator_inset", "archive_contact_sheet"} else 0.18)
    return {
        "enabled": True,
        "hero_layer": "source_media",
        "render_mode": "media_first_overlay" if not standalone else "media_only",
        "component_type": primary_type,
        "media_type": _media_type(scene),
        "media_visibility": visibility,
        "overlay_grammar": grammar,
        "placement": placement,
        "center_data_visual": central_chart,
        "blur_media_underlay": bool(
            central_chart
            and (
                decision.get("requires_chart_contrast")
                or _media(scene).get("requires_chart_contrast")
            )
        ),
        "overlay_budget": 1 if grammar != "analytic_callout" else 2,
        "overlay_max_area": max_area,
        "overlay_max_opacity": 0.82,
        "overlay_scale": "central" if central_chart else ("micro" if grammar in {"data_inset", "locator_inset", "portrait_id"} else "supporting"),
        "safe_region": safe_region,
        "caption_region": [0.08, 0.83, 0.92, 0.97],
        "text_tone": text_tone,
        "subject_bbox": focal or [],
        "collision_policy": "avoid_verified_subject",
        "allow_full_frame_graphic": False,
        "visibility_floor": MEDIA_FIRST_RULES["minimum_media_visibility"],
    }


def annotate_media_compositions(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """Copy a manifest and attach media-first contracts to every scene."""

    result = deepcopy(dict(manifest))
    scenes = []
    for scene in result.get("scenes") or []:
        scene_copy = dict(scene)
        scene_copy["media_composition"] = plan_media_composition(scene_copy)
        scenes.append(scene_copy)
    result["scenes"] = scenes
    result.setdefault("visual_system", {})["media_policy"] = "media-first-overlay-v1"
    result["media_first_rules"] = dict(MEDIA_FIRST_RULES)
    return result


def audit_media_compositions(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """Deterministic integration audit for the manager/vision critic."""

    errors: List[str] = []
    warnings: List[str] = []
    media_scene_count = 0
    overlay_scene_count = 0
    for index, scene in enumerate(manifest.get("scenes") or []):
        if not isinstance(scene, Mapping) or not _has_media(scene):
            continue
        media_scene_count += 1
        plan = scene.get("media_composition") if isinstance(scene.get("media_composition"), Mapping) else plan_media_composition(scene)
        if owns_explanatory_canvas(scene):
            if plan.get("hero_layer") != "graphic" or plan.get("render_mode") != "explanatory_canvas":
                errors.append(f"scene {index}: primary explanation does not own its canvas")
            continue
        if plan.get("hero_layer") != "source_media":
            errors.append(f"scene {index}: source media is not the hero layer")
        if _number(plan.get("media_visibility"), 0.0) < MEDIA_FIRST_RULES["minimum_media_visibility"]:
            errors.append(f"scene {index}: media visibility is below the documentary floor")
        primary = scene.get("primary_visual") if isinstance(scene.get("primary_visual"), Mapping) else {}
        if str(primary.get("type") or "").lower() not in {"smartimage", "smartvideo", "image", "video"}:
            overlay_scene_count += 1
            if plan.get("render_mode") != "media_first_overlay":
                errors.append(f"scene {index}: graphic media scene is not routed through the overlay compositor")
            if bool(plan.get("allow_full_frame_graphic")):
                errors.append(f"scene {index}: full-frame graphic is enabled over source media")
            if _number(plan.get("overlay_max_area"), 1.0) > MEDIA_FIRST_RULES["maximum_overlay_area"]:
                errors.append(f"scene {index}: overlay area exceeds the media visibility budget")
            region = _box(plan.get("safe_region"))
            if not region:
                errors.append(f"scene {index}: media overlay has no safe region")
            subject = _box(plan.get("subject_bbox"))
            if subject and region and _intersection(_inflate(region, 0.01), _inflate(subject, 0.01)) > 0:
                warnings.append(f"scene {index}: safe region overlaps the subject; fallback placement will be used")
    status = "fail" if errors else "pass"
    all_scenes = [scene for scene in (manifest.get('scenes') or []) if isinstance(scene, Mapping)]
    def seconds(scene):
        return max(0.0, _number(scene.get('duration'), 0.0))
    duration = sum(seconds(scene) for scene in all_scenes)
    real_duration = sum(seconds(scene) for scene in all_scenes
                        if (scene.get('media') or {}).get('type') in {'video', 'image'}
                        and not (scene.get('media') or {}).get('is_graphic_fallback')
                        and (scene.get('media') or {}).get('source') not in {'neutral', 'graphic'})
    return {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "media_scene_count": media_scene_count,
        "media_first_ratio": real_duration / duration if duration else None,
        "metric_basis": "selected real-media duration; automated manifest check",
        "reviewPerformed": False,
        "overlay_scene_count": overlay_scene_count,
        "ready_for_long_render": status == "pass",
    }
