"""Bounded documentary coverage diagnostics from final timeline and sampled DOM.

Timeline intervals are an estimate, never a claim that every rendered pixel was
reviewed. Captions, default titles and decorative geometry do not satisfy the
explanatory graphic target. Large script-grounded emphasis is reported separately
and included only when its actual DOM footprint is prominent.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math


TEMPLATE_FAMILIES = {
    **dict.fromkeys(("map-route", "map-card", "map-comparison"), "map"),
    **dict.fromkeys(("bar-chart", "ranked-bars", "line-chart", "slope-chart", "area-chart", "donut-chart"), "chart"),
    **dict.fromkeys(("timeline-trace", "timeline-strip", "date-card", "date-reveal", "date-numeral"), "timeline"),
    **dict.fromkeys(("stat-card", "stat-counter", "money-counter", "metric-grid", "metric-grid-enhanced"), "stat"),
    **dict.fromkeys(("cause-effect", "cause-effect-flow", "process-flow", "decision-tree"), "diagram"),
    **dict.fromkeys(("split-screen", "comparison-split"), "comparison"),
    **dict.fromkeys(("paper-explainer", "source-highlight", "redaction-reveal"), "document"),
    **dict.fromkeys(("quote-card", "witness-quote"), "quote"),
    **dict.fromkeys(("person-profile", "profile-dossier", "dossier-card"), "profile"),
    **dict.fromkeys(("gallery-stack", "evidence-board", "photo-pinboard"), "collage"),
}
TYPE_FAMILIES = {"AnimatedMap": "map", "SatelliteMap": "map", "Chart": "chart", "Timeline": "timeline",
                 "StatCard": "stat", "MetricGrid": "stat", "Diagram": "diagram", "Comparison": "comparison",
                 "DocumentHighlight": "document", "QuoteCard": "quote", "PersonProfile": "profile", "Collage": "collage"}


def _row(value):
    return value if isinstance(value, Mapping) else {}


def _number(value, default=0.):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _list(value):
    return list(value) if isinstance(value, (list, tuple)) else []


def _union_seconds(intervals):
    end = -1.
    total = 0.
    for start, stop in sorted(intervals):
        if stop <= start:
            continue
        total += max(0., stop - max(start, end))
        end = max(end, stop)
    return total


def _grounded(family, props, scene):
    if props.get("no_data") is True:
        return False
    rows = _list(props.get("data") or props.get("values") or props.get("items"))
    if family == "chart":
        return len(rows) >= 2 or len(_list(_row(props.get("seriesA")).get("points"))) >= 2
    if family == "diagram":
        return len(rows or _list(props.get("steps")) or _list(props.get("nodes"))) >= 2
    if family == "timeline":
        return len(_list(props.get("events")) or rows) >= 2 or bool(props.get("date") or props.get("year"))
    if family == "stat":
        return any(props.get(key) is not None and str(props.get(key)).strip() for key in ("value", "stat", "number")) or len(_list(props.get("metrics")) or rows) >= 2
    if family == "map":
        shot = _row(scene.get("map_shot") or props.get("map_shot"))
        return shot.get("enabled") is not False and bool(shot.get("location_names") or props.get("location") or props.get("place") or props.get("country") or (props.get("from") and props.get("to")))
    if family == "comparison":
        return bool((props.get("left") or props.get("leftValue")) and (props.get("right") or props.get("rightValue"))) or len(rows) == 2 or (props.get("before") is not None and props.get("after") is not None)
    if family == "document":
        return _row(props.get("source_capture")).get("status") == "exact_passage_verified"
    if family == "quote":
        return bool(props.get("quote") and (props.get("attribution") or props.get("speaker")))
    if family == "profile":
        return bool(props.get("name") or props.get("person")) and bool(scene.get("media") or _row(scene.get("primary_visual")).get("asset"))
    if family == "collage":
        return len(_list(props.get("images")) or _list(_row(scene.get("media")).get("shots"))) >= 2
    return False


def _recipe_family(recipe, scene):
    layers = [_row(layer) for layer in _list(recipe.get("layers"))]
    # Arbitrary lines/shapes or narration pasted into text are not an explanation.
    for layer in layers:
        family = {"chart": "chart", "diagram": "diagram", "comparison": "comparison", "timeline": "timeline", "map": "map"}.get(layer.get("kind"))
        if family and _grounded(family, layer, scene):
            return family
    actors = {str(layer.get("semantic_actor") or "").strip().lower() for layer in layers if layer.get("semantic_actor")}
    narrative = str(scene.get("narration") or "").lower()
    grounded_actors = {actor for actor in actors if actor and actor in narrative}
    relations = [layer for layer in layers if layer.get("kind") in {"arrow", "path", "cone"} ]
    return "diagram" if len(grounded_actors) >= 2 and relations else ""


def _recipe_start_frame(recipe, scene):
    layers = [_row(layer) for layer in _list(recipe.get("layers"))]
    layer_ids = {layer.get("id"): layer for layer in layers if isinstance(layer.get("id"), str)}
    def start(layer):
        result = max(0., _number(layer.get("start_frame")))
        parent = layer.get("parent_id")
        seen = set()
        while isinstance(parent, str) and parent in layer_ids and parent not in seen:
            seen.add(parent)
            result = max(result, _number(layer_ids[parent].get("start_frame")))
            parent = layer_ids[parent].get("parent_id")
        return result
    qualified = [start(layer) for layer in layers if layer.get("kind") in {"chart", "diagram", "comparison", "timeline", "map"}
                 and _grounded(str(layer.get("kind")), layer, scene)]
    if qualified:
        return min(qualified)
    narrative = str(scene.get("narration") or "").lower()
    actors = {}
    for layer in layers:
        actor = str(layer.get("semantic_actor") or "").strip().lower()
        if actor and actor in narrative:
            actors[actor] = min(actors.get(actor, float("inf")), start(layer))
    relations = [start(layer) for layer in layers if layer.get("kind") in {"arrow", "path", "cone"}]
    return max(sorted(actors.values())[1], min(relations)) if len(actors) >= 2 and relations else 0.


def _graphic_window(scene, fps, duration):
    window = _row(scene.get("editor_graphic_window"))
    if window:
        start = _number(window.get("start_frame")) / fps
        end = start + _number(window.get("duration_frames")) / fps
    else:
        graphic = _row(scene.get("graphic"))
        timing = _row(_row(scene.get("editorial_contract")).get("timing") or _row(_row(_row(scene.get("primary_visual")).get("props")).get("editorial_contract")).get("timing"))
        cue = _row(graphic.get("cue"))
        start = _number(timing.get("scene_local_start", graphic.get("scene_local_start", cue.get("scene_local_start", _number(graphic.get("start"), _number(scene.get("start"))) - _number(scene.get("start"))))))
        end = _number(timing.get("scene_local_end", graphic.get("scene_local_end", cue.get("scene_local_end"))), start + _number(timing.get("total_seconds", graphic.get("duration")), duration))
    return max(0., min(duration, start)), max(0., min(duration, end))


def _review_signature(manifest):
    scenes = []
    for scene in _list(manifest.get("scenes")):
        scene = _row(scene)
        primary = _row(scene.get("primary_visual"))
        scenes.append({**{key: scene.get(key) for key in ("from", "start", "duration", "duration_in_frames", "template_id", "text", "graphic", "editorial_contract", "copy_window", "editor_graphic_window", "editor_track_visibility", "template_recipe", "media_composition", "motion", "map_shot")},
                       "primary": {key: primary.get(key) for key in ("type", "variant", "props")}})
    return hashlib.sha256(json.dumps([manifest.get("fps"), manifest.get("width"), manifest.get("height"), manifest.get("visual_dna"), scenes], sort_keys=True, default=str).encode()).hexdigest()


def audit_rendered_graphics(manifest, layout_audit=None):
    manifest = _row(manifest)
    fps = max(1., _number(manifest.get("fps"), 30.))
    scenes = [_row(scene) for scene in _list(manifest.get("scenes"))]
    duration = max(_number(manifest.get("duration_in_frames")) / fps, _number(manifest.get("duration")), sum(_number(scene.get("duration")) for scene in scenes))
    events, fingerprints, warnings = [], [], []
    for index, scene in enumerate(scenes):
        if _row(scene.get("editor_track_visibility")).get("graphics") is False:
            continue
        primary, graphic = _row(scene.get("primary_visual")), _row(scene.get("graphic"))
        props = {**_row(primary.get("props")), **_row(graphic.get("props")), **_row(graphic.get("vox_props"))}
        template = str(scene.get("template_id") or "")
        if template in {"", "full-bleed", "media-only", "image", "video"}:
            template = str(graphic.get("template_id") or template)
        recipe = _row(scene.get("template_recipe") or props.get("template_recipe"))
        recipe_family = _recipe_family(recipe, scene) if template == "generated-recipe" else ""
        family = recipe_family or TYPE_FAMILIES.get(primary.get("type"), "") or TEMPLATE_FAMILIES.get(template, "")
        if not family or (not recipe_family and not _grounded(family, props, scene)):
            continue
        scene_duration = _number(scene.get("duration_in_frames")) / fps or _number(scene.get("duration"))
        start = _number(scene.get("from")) / fps if scene.get("from") is not None else _number(scene.get("start"))
        local_start, local_end = _graphic_window(scene, fps, scene_duration)
        if recipe_family:
            local_start += _recipe_start_frame(recipe, scene) / fps
        if local_end <= local_start:
            continue
        variant = str(primary.get("variant") or graphic.get("variant") or props.get("variant") or "default")
        structure = str(_row(scene.get("media_composition")).get("mode") or scene.get("visual_role") or _row(scene.get("design")).get("visual_structure") or "default")
        motion = str(_row(scene.get("motion")).get("entrance") or "default")
        # Content, colors and random seeds cannot disguise a repeated arrangement.
        recipe_layout = [(layer.get("kind"), layer.get("x"), layer.get("y"), layer.get("width"), layer.get("height")) for layer in _list(recipe.get("layers")) if isinstance(layer, Mapping)] if recipe_family else []
        signature = hashlib.sha256(json.dumps([family, variant, structure, motion, recipe_layout], sort_keys=True, default=str).encode()).hexdigest()[:16]
        events.append({"scene_index": scene.get("scene_index", index), "family": family, "variant": variant,
                       "start_seconds": round(start + local_start, 4), "end_seconds": round(start + local_end, 4), "fingerprint": signature})
        fingerprints.append((index, signature, scene.get("continuous_reveal") is True))
    seconds = _union_seconds((event["start_seconds"], event["end_seconds"]) for event in events)
    target = max(0., min(1., _number(_row(manifest.get("metrics")).get("graphic_coverage_target"), .30)))
    if seconds / max(.001, duration) + .05 < target:
        warnings.append("explanatory_graphic_coverage_below_target")
    if len(events) >= 3 and len({event["fingerprint"] for event in events}) == 1:
        warnings.append("same_visible_graphic_treatment")
    if any(left[0] + 1 == right[0] and left[1] == right[1] and not right[2] for left, right in zip(fingerprints, fingerprints[1:])):
        warnings.append("adjacent_visible_graphic_repeat")

    # DOM samples confirm actual component presence/prominence, independent of
    # the selected template name. This is sampled evidence, not dense video QA.
    samples = {}
    signature = _review_signature(manifest)
    emphasis_intervals = []
    stale_samples = False
    for sample in _list(layout_audit):
        sample = _row(sample)
        if sample.get("graphics_audit_version") != 1:
            continue
        if sample.get("graphics_manifest_signature") != signature:
            stale_samples = True
            continue
        frame = _number(sample.get("frame"), -1.)
        if frame < 0:
            continue
        graphics = [_row(row) for row in _list(sample.get("graphics"))]
        visible = [row for row in graphics if row.get("visible") is True and _number(row.get("area_ratio")) >= .025 and row.get("category") in {"explanatory", "emphasis"}]
        for row in visible:
            if row.get("category") == "emphasis":
                start = max(0., _number(row.get("start_seconds")))
                stop = min(duration, _number(row.get("end_seconds")))
                if stop > start:
                    emphasis_intervals.append((start, stop))
        # Prefer the final log for duplicate frame updates after font/video load.
        samples[int(frame)] = visible
    sample_rows = [{"frame": frame, "has_graphic": bool(rows), "families": sorted({str(row.get("family") or "") for row in rows})} for frame, rows in sorted(samples.items())]
    observed_scenes = set()
    visible_layouts = {}
    for sample in sample_rows:
        for index, scene in enumerate(scenes):
            scene_start = _number(scene.get("from")) / fps if scene.get("from") is not None else _number(scene.get("start"))
            scene_end = scene_start + (_number(scene.get("duration_in_frames")) / fps or _number(scene.get("duration")))
            if scene_start <= sample["frame"] / fps < scene_end:
                observed_scenes.add(index)
                graphic_rows = samples[sample["frame"]]
                shapes = sorted({str(row.get("layout_fingerprint")) for row in graphic_rows if row.get("layout_fingerprint")})
                if shapes and scene.get("continuous_reveal") is not True:
                    distance = abs(sample["frame"] / fps - (scene_start + (scene_end-scene_start)*.5))
                    if index not in visible_layouts or distance < visible_layouts[index][0]:
                        visible_layouts[index] = (distance, tuple(shapes))
    combined_seconds = _union_seconds([(event["start_seconds"], event["end_seconds"]) for event in events] + emphasis_intervals)
    if combined_seconds / max(.001, duration) + .05 >= target:
        warnings = [warning for warning in warnings if warning != "explanatory_graphic_coverage_below_target"]
    if stale_samples and not samples:
        warnings.append("graphic_visibility_samples_stale")
    if events and not samples:
        warnings.append("graphic_visibility_not_sampled")
    if samples and len(observed_scenes) < len(scenes):
        warnings.append("graphic_visibility_samples_incomplete")
    if len(visible_layouts) >= 3 and len({row[1] for row in visible_layouts.values()}) == 1:
        warnings.append("same_rendered_graphic_layout")
    if samples and events and not any(sample["has_graphic"] for sample in sample_rows):
        warnings.append("planned_graphics_not_visible_in_review_samples")
    return {"version": 1, "review_signature": signature, "status": "review_needed" if warnings else "pass", "warnings": warnings,
            "coverage_basis": "union of grounded explanatory graphic timeline windows; excludes captions, plain headings and decoration; visibility reported separately",
            "timeline_explanatory_seconds": round(seconds, 4), "timeline_explanatory_ratio": round(seconds / max(.001, duration), 4),
            "dom_confirmed_emphasis_window_seconds": round(_union_seconds(emphasis_intervals), 4),
            "documentary_graphic_window_ratio": round(combined_seconds / max(.001, duration), 4),
            "coverage_target": target, "video_seconds": round(duration, 4), "graphic_events": events,
            "explanatory_family_unique": len({event["family"] for event in events}),
            "visible_treatment_unique": len({event["fingerprint"] for event in events}),
            "visibility_basis": "sampled DOM component presence and prominence; not full-video pixel certification" if samples else "not_sampled",
            "sampled_visible_treatment_unique": len({row[1] for row in visible_layouts.values()}),
            "sampled_frame_count": len(samples), "sampled_scene_count": len(observed_scenes),
            "sampled_graphic_frame_ratio": round(sum(sample["has_graphic"] for sample in sample_rows) / len(samples), 4) if samples else None,
            "visibility_samples": sample_rows}


def update_graphic_audit(manifest, layout_audit=None):
    """Attach diagnostics without raising or retrying generation."""
    try:
        audit = audit_rendered_graphics(manifest, layout_audit)
    except (ValueError, TypeError, KeyError, OverflowError):
        # Diagnostics cannot abort a usable render or create a repair loop.
        audit = {"version": 1, "status": "review_needed", "warnings": ["graphic_audit_unavailable"], "graphic_events": []}
    if not isinstance(manifest.get("metrics"), dict):
        manifest["metrics"] = {}
    manifest["metrics"]["rendered_graphics_audit"] = audit
    for index, scene in enumerate(manifest.get("scenes") or []):
        if isinstance(scene, dict):
            scene["_graphic_audit"] = {"family": next((event["family"] for event in audit["graphic_events"]
                if event["scene_index"] == scene.get("scene_index", index)), "")}
    return audit
