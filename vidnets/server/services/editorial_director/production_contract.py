"""Semantic production requirements shared by planning, review and export.

This module does not invent factual payloads or copy. It preserves explicit
script requirements across later repairs and exposes all editing tracks.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import copy
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .media_director import owns_explanatory_canvas, plan_media_composition
from .contract import has_approved_director_copy

SPECIALISTS = {
    "chart": "AdaptiveChart", "timeline": "AdaptiveTimeline", "diagram": "ExplanatoryDiagram", "metricgrid": "MetricGrid",
    "comparison": "AdaptiveComparison", "animatedmap": "AnimatedMap", "satellitemap": "SatelliteMap",
    "documenthighlight": "DocumentHighlight", "personprofile": "PersonProfile",
    "smartimage": "SmartImage", "smartvideo": "SmartVideo", "kinetictext": "EditorialType",
    "statcard": "AdaptiveStat", "quotecard": "QuoteCard", "chaptertitle": "ChapterTitle",
}

RENDERER_TEMPLATES = {
    "Chart": "bar-chart", "Timeline": "timeline-trace", "Diagram": "cause-effect",
    "Comparison": "split-screen", "AnimatedMap": "map-route", "SatelliteMap": "map-route",
    "DocumentHighlight": "source-highlight", "PersonProfile": "profile-dossier",
    "Collage": "gallery-stack", "QuoteCard": "quote-card", "ChapterTitle": "chapter-opener",
    "Headline": "kinetic-statement", "KineticText": "kinetic-statement", "StatCard": "stat-counter",
}

# Stable bridge from the director's full template catalog to Remotion's
# specialist renderer. Keep this separate from the primary-visual mapping:
# a SmartVideo scene can carry a chart/map as a timed overlay while its media
# remains visible underneath.
TEMPLATE_PRIMARY_TYPES = {
    "full-bleed": "SmartImage",
    "kinetic-statement": "KineticText", "headline-center": "KineticText", "kinetic-text": "KineticText",
    "keyword-reveal": "KineticText", "chapter-opener": "ChapterTitle",
    "timeline-trace": "Timeline", "timeline-strip": "Timeline", "date-card": "Timeline",
    "bar-chart": "Chart", "line-chart": "Chart", "area-chart": "Chart",
    "donut-chart": "Chart", "ranked-bars": "Chart",
    "map-route": "AnimatedMap", "map-comparison": "AnimatedMap",
    "source-highlight": "DocumentHighlight", "quote-card": "QuoteCard", "witness-quote": "QuoteCard",
    "split-screen": "Comparison", "cause-effect": "Diagram", "process-flow": "Diagram",
    "lower-third": "PersonProfile", "person-profile": "PersonProfile", "profile-dossier": "PersonProfile", "dossier-card": "PersonProfile",
    "gallery-stack": "Collage", "evidence-board": "Collage", "photo-pinboard": "Collage",
    "stat-card": "StatCard", "stat-counter": "StatCard", "metric-grid": "StatCard", "metric-grid-enhanced": "StatCard",
    "media-focus-card": "SmartImage", "generated-recipe": "GeneratedRecipe",
}

TEMPLATE_VARIANT_DEFAULTS = {
    "line-chart": "trend_line_with_markers", "area-chart": "area_growth_fill",
    "donut-chart": "donut_distribution", "ranked-bars": "ranked_horizontal_bars",
    "timeline-trace": "horizontal_milestone_trail", "timeline-strip": "vertical_chronology",
    "date-card": "date_flip_calendar", "map-comparison": "city_to_city_transit",
    "cause-effect": "step_by_step_process", "process-flow": "step_by_step_process",
    "split-screen": "side_by_side_cards",
}

TEXT_TEMPLATE_STYLES = {
    "kinetic-statement": "hero_type", "headline-center": "hero_type",
    "keyword-reveal": "color-band", "chapter-opener": "chapter-lockup",
    "lower-third": "editorial_caption",
}

TEXT_TEMPLATE_VARIANTS = {
    # Distinct, restrained defaults keep these templates recognizable even
    # when a model omits a motion choice. Explicit validated variants still win.
    "kinetic-statement": "staggered_lines",
    "headline-center": "impact_slab",
    "keyword-reveal": "word_by_word_punch",
    "chapter-opener": "cinematic_minimal_act",
}
KINETIC_TEXT_VARIANTS = {
    "word_by_word_punch", "staggered_lines", "mask_slide_up", "bold_minimal_center",
    "tracking_expand", "split_counter_accent", "impact_slab",
}
CHAPTER_TEXT_VARIANTS = {
    "cinematic_minimal_act", "archival_slate_divider", "bold_editorial_kicker",
    "technical_timecode_chapter",
}
DIRECTOR_TEXT_POSITIONS = {
    "center", "hero", "middle", "top", "bottom", "top-left", "top-right",
    "bottom-left", "bottom-right", "center-left", "center-right", "none",
}
DIRECTOR_TEXT_PANEL_STYLES = {
    "none", "impact_slab", "hero_type", "stacked_type", "outline_type", "marker_type",
    "editorial_caption", "minimal_rule", "editorial-plaque", "color-band",
    "chapter-lockup", "minimal-outline", "locator",
}


def _validated_director_text_settings(template_id: str, graphic: Mapping[str, Any], props: Mapping[str, Any], text: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only bounded style controls for the approved-copy renderer."""
    result = dict(text)
    result["template_id"] = template_id
    variant = str(graphic.get("variant") or props.get("variant") or "").strip().lower().replace("-", "_")
    allowed_variants = CHAPTER_TEXT_VARIANTS if template_id == "chapter-opener" else KINETIC_TEXT_VARIANTS
    if variant in allowed_variants:
        result["variant"] = variant
    else:
        result["variant"] = TEXT_TEMPLATE_VARIANTS[template_id]
    position = str(props.get("position") or text.get("position") or "").strip().lower().replace("_", "-")
    if position in DIRECTOR_TEXT_POSITIONS:
        result["position"] = position
    panel_style = str(props.get("panel_style") or props.get("text_style") or text.get("panel_style") or "").strip().lower()
    normalized_panel_style = panel_style.replace("-", "_")
    valid_panel_styles = {value.replace("-", "_") for value in DIRECTOR_TEXT_PANEL_STYLES}
    if normalized_panel_style in valid_panel_styles:
        # EditorialType consumes snake case; TextCard's named shapes use the
        # established hyphenated ids. Preserve those exact component tokens.
        editorial_styles = {"impact_slab", "hero_type", "stacked_type", "outline_type", "marker_type", "editorial_caption", "minimal_rule"}
        result["panel_style"] = normalized_panel_style if normalized_panel_style in editorial_styles else panel_style
    for key in ("accent_color", "text_color", "panel_background"):
        value = str(props.get(key) or text.get(key) or "").strip()
        if re.fullmatch(r"#[0-9a-fA-F]{3,8}", value):
            result[key] = value
    return result


def _grounded_template_props(template_id: str, props: Mapping[str, Any], scene: Mapping[str, Any]) -> bool:
    """Return true only when a media-overlay template has its required payload."""
    values = props.get("data") or props.get("values") or props.get("items") or []
    if template_id in {"bar-chart", "line-chart", "area-chart", "donut-chart", "ranked-bars"}:
        return isinstance(values, list) and len(values) >= 2
    if template_id in {"timeline-trace", "timeline-strip"}:
        return len(props.get("events") or []) >= 2 or bool(props.get("date") or props.get("year"))
    if template_id == "date-card":
        return bool(props.get("date") or props.get("year") or props.get("label"))
    if template_id in {"map-route", "map-comparison"}:
        shot = scene.get("map_shot") if isinstance(scene.get("map_shot"), Mapping) else {}
        return bool(
            (props.get("from") and props.get("to"))
            or (props.get("start") and props.get("end"))
            or props.get("location") or props.get("country") or props.get("place")
            or shot.get("location_names") or shot.get("feature_ids") or shot.get("route_id")
        )
    if template_id == "source-highlight":
        capture = props.get("source_capture") if isinstance(props.get("source_capture"), Mapping) else {}
        return capture.get("verification_status") == "exact_passage_verified" and bool(capture.get("exact_excerpt"))
    if template_id == "quote-card":
        return bool(props.get("quote") and (props.get("attribution") or props.get("speaker")))
    if template_id in {"split-screen"}:
        return bool((props.get("left") or props.get("leftValue")) and (props.get("right") or props.get("rightValue")))
    if template_id in {"cause-effect", "process-flow"}:
        return bool((props.get("cause") and props.get("effect")) or len(props.get("steps") or []) >= 2)
    if template_id == "metric-grid":
        return len(props.get("metrics") or props.get("items") or []) >= 2
    if template_id in {"stat-card", "metric-grid-enhanced"}:
        return props.get("value") is not None or props.get("number") is not None or props.get("stat") is not None
    if template_id in {"person-profile", "profile-dossier", "dossier-card", "lower-third"}:
        return bool(props.get("name") or props.get("person"))
    if template_id == "media-focus-card":
        # This treatment is a short visual emphasis over verified media. Its
        # headline must already be approved script copy; never use its legacy
        # placeholder title to manufacture a message.
        return has_approved_director_copy(scene)
    if template_id in {"gallery-stack", "evidence-board", "photo-pinboard"}:
        return len(props.get("images") or []) >= 2 or len((scene.get("media") or {}).get("shots") or []) >= 2
    return False


def _recipe_has_grounded_visual_content(recipe: Mapping[str, Any], scene: Mapping[str, Any]) -> bool:
    """Reject structurally safe recipes that render only empty decoration.

    Schema validation alone accepts a large blank rectangle and empty group,
    which can dim an entire photograph without explaining the narration.
    Require a grounded text/evidence payload, a usable specialist payload, or
    a labeled visual mark before a recipe can own or augment the scene.
    """
    layers = recipe.get("layers") if isinstance(recipe, Mapping) else None
    if not isinstance(layers, list) or not layers:
        return False
    narration = str(scene.get("narration") or "")
    try:
        from ..template_library import validate_template_recipe
        normalized = validate_template_recipe(recipe)
        from .director import _recipe_is_grounded, _recipe_supports_visual_type
        if not _recipe_is_grounded(normalized, narration):
            return False
        primary = scene.get("primary_visual") if isinstance(scene.get("primary_visual"), Mapping) else {}
        primary_type = str(primary.get("type") or "")
        if primary_type in {"Chart", "Timeline", "Diagram", "Comparison"}:
            if not _recipe_supports_visual_type(normalized, primary_type, bool((scene.get("map_shot") or {}).get("enabled"))):
                return False
    except (ImportError, TypeError, ValueError, KeyError):
        return False

    source = re.sub(r"[^\w%$]+", " ", narration, flags=re.UNICODE).casefold().strip()
    media = scene.get("media") if isinstance(scene.get("media"), Mapping) else {}
    shots = media.get("shots") if isinstance(media.get("shots"), list) else []
    for layer in normalized.get("layers") or []:
        if not isinstance(layer, Mapping):
            continue
        kind = str(layer.get("kind") or "").casefold()
        text = re.sub(r"[^\w%$]+", " ", str(layer.get("text") or ""), flags=re.UNICODE).casefold().strip()
        actor = re.sub(r"[^\w%$]+", " ", str(layer.get("semantic_actor") or ""), flags=re.UNICODE).casefold().strip()
        data = layer.get("data") if isinstance(layer.get("data"), list) else []
        grounded_labels = [
            re.sub(r"[^\w%$]+", " ", str(row.get("label") or ""), flags=re.UNICODE).casefold().strip()
            for row in data if isinstance(row, Mapping)
        ]
        if text and text in source:
            return True
        if actor and actor in source and kind in {"line", "path", "circle", "sphere", "cone", "arrow", "rect"}:
            return True
        if kind == "chart" and len(data) >= 2 and all(row.get("value") is not None for row in data if isinstance(row, Mapping)):
            return True
        if kind == "diagram" and len(data) >= 2 and len(layer.get("links") or []) >= 1:
            return True
        if kind in {"timeline", "comparison"} and len(data) >= 2:
            return True
        if kind == "map" and (scene.get("map_shot") or {}).get("enabled") and grounded_labels:
            return True
        if kind == "counter" and any(
            isinstance(row, Mapping) and row.get("value") is not None
            for row in data
        ) and any(char.isdigit() for char in narration):
            return True
        if kind == "image" and (layer.get("asset_id") or (isinstance(layer.get("image_index"), int) and layer["image_index"] < len(shots))):
            return True
        if kind == "media_montage" and (len(data) >= 2 or len(shots) >= 2):
            return True
    return False


def _sync_director_graphic_to_primary(scene: dict[str, Any], primary: Mapping[str, Any]) -> None:
    """Make the Remotion-facing graphic agree with the final director decision.

    V2 manifests retain a legacy ``graphic`` field alongside ``primary_visual``.
    Keeping stale props there lets an old number, component or position override
    the validated director choice at render time. Remotion still consumes that
    graphic field, so rebuild it from the final primary contract on every compile.
    """
    source = str(scene.get("visual_role_source") or "").strip().lower()
    if not (
        scene.get("director_treatment_enabled") is True
        or source in {"llm_director", "llm_pixel_director", "system_grounded_mapping"}
    ):
        return

    kind = str(primary.get("type") or "").strip()
    props = primary.get("props") if isinstance(primary.get("props"), Mapping) else {}
    existing_graphic = scene.get("graphic") if isinstance(scene.get("graphic"), Mapping) else {}
    requested_template = str(scene.get("template_id") or existing_graphic.get("template_id") or "").strip().lower().replace("_", "-")
    requested_type = TEMPLATE_PRIMARY_TYPES.get(requested_template)
    requested_props = {**dict(props), **dict(existing_graphic.get("props") or {})}
    recipe = scene.get("template_recipe") if isinstance(scene.get("template_recipe"), Mapping) else {}
    if not recipe and isinstance(props.get("template_recipe"), Mapping):
        recipe = props.get("template_recipe")
    if recipe.get("layers"):
        template_id = "generated-recipe"
        render_props = {**dict(props), "template_recipe": dict(recipe)}
    elif requested_template and requested_type == kind:
        # Retain the exact director-picked layout when it belongs to this
        # component family (e.g. line-chart vs ranked-bars).
        template_id = requested_template
        render_props = requested_props
    elif kind in {"SmartImage", "SmartVideo", "image", "video"} and requested_template and _grounded_template_props(requested_template, requested_props, scene):
        # Media-first beats can animate a specialist graphic over verified
        # footage. Do not throw that authored choice away as full-bleed media.
        template_id = requested_template
        render_props = requested_props
    elif kind in {"SmartImage", "SmartVideo", "image", "video"}:
        if requested_template in TEXT_TEMPLATE_STYLES:
            current_text = scene.get("text") if isinstance(scene.get("text"), Mapping) else {}
            scene["text"] = _validated_director_text_settings(
                requested_template, existing_graphic, requested_props, current_text,
            )
        template_id = "full-bleed"
        render_props = {}
    elif kind == "KineticText":
        variants = {
            "word_by_word_punch": "keyword-reveal", "staggered_lines": "kinetic-statement",
            "mask_slide_up": "kinetic-text", "bold_minimal_center": "headline-center",
            "tracking_expand": "kinetic-statement", "split_counter_accent": "keyword-reveal",
            "impact_slab": "keyword-reveal",
        }
        template_id = variants.get(str(primary.get("variant") or ""), "kinetic-statement")
        render_props = dict(props)
    elif kind == "StatCard":
        template_id = "metric-grid-enhanced" if str(primary.get("variant") or "") == "kpi_metric_card" else "stat-counter"
        render_props = dict(props)
    elif kind == "AnimatedMap":
        variants = {"route_flight_arc": "map-comparison", "city_to_city_transit": "map-comparison"}
        template_id = variants.get(str(primary.get("variant") or ""), "map-route")
        render_props = dict(props)
    else:
        template_id = RENDERER_TEMPLATES.get(kind)
        if not template_id:
            return
        render_props = dict(props)

    graphic = dict(existing_graphic)
    graphic["template_id"] = template_id
    render_props.setdefault("template_id", template_id)
    if template_id in {"line-chart", "area-chart", "donut-chart", "ranked-bars", "timeline-strip", "date-card", "map-comparison"}:
        render_props["variant"] = TEMPLATE_VARIANT_DEFAULTS[template_id]
    elif not render_props.get("variant"):
        render_props["variant"] = str(primary.get("variant") or TEMPLATE_VARIANT_DEFAULTS.get(template_id) or "")
    graphic["props"] = render_props
    graphic["variant"] = str(render_props.get("variant") or primary.get("variant") or "")
    graphic.setdefault("scene_local_start", 0.0)
    graphic["scene_local_end"] = _number(scene.get("duration"))
    scene["template_id"] = template_id
    scene["graphic"] = graphic


def script_requirements(narration: str) -> dict[str, Any]:
    text = str(narration or "")
    # Explicit viewer instructions; mentions such as "mapped his career" are
    # not geographical requirements. Never infer a route from two names alone.
    map_request = bool(re.search(
        r"\b(?:watch|follow|trace|see|show|look|track)\b[^.!?]{0,90}\b(?:on (?:a |the )?map|map below|this map|the map)\b"
        r"|\b(?:map shows|map traces|map reveals|mapped route)\b", text, re.I))
    return {"map_required": map_request}


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        n = float(value)
        return n if math.isfinite(n) else fallback
    except (TypeError, ValueError):
        return fallback


def _spoken_copy_time(scene: Mapping[str, Any], phrase: str) -> float | None:
    """Use the same scene-local word clock as Remotion's spokenPhraseFrame."""
    words = {}
    for cue in scene.get('word_cues') or []:
        for word in cue.get('timed_words') or []:
            if isinstance(word, Mapping) and isinstance(word.get('index'), (int, float)):
                words[int(word['index'])] = word
    ordered = sorted(words.values(), key=lambda word: word['index'])
    token = lambda value: re.sub(r'[^a-z0-9]', '', str(value or '').lower())
    target = [token(value) for value in str(phrase).split() if token(value)]
    for index in range(len(ordered) - len(target) + 1):
        if target and all(token(ordered[index+k].get('text')) == value
                          and ordered[index+k]['index'] == ordered[index]['index'] + k
                          for k, value in enumerate(target)):
            local = _number(ordered[index].get('start'), -1)
            if 0 <= local < _number(scene.get('duration')):
                return local
    return None


_PERSON_NAME = re.compile(
    r"(?<![\w])(?P<name>[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+"
    r"(?:\s+(?:(?:of|the|and)\s+)?[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+){1,2})"
    r"(?=$|[^A-Za-zÀ-ÖØ-öø-ÿ])"
)
_PERSON_ACTION = re.compile(
    r"\b(?:takes?|took|snaps?|fired?|fires?|threw|throws?|passes?|catches?|caught|"
    r"reads?|cuts?|intercepts?|intercepted|blocks?|runs?|ran|scores?|scored|"
    r"says?|said|announces?|announced|creates?|created|wins?|won|loses?|lost|"
    r"leads?|led|returns?|returned|holds?|held|plays?|played|coaches?|coached|"
    r"calls?|called|moves?|moved|turns?|turned|becomes?|became|is|are|was|were)\b",
    re.IGNORECASE,
)
_NON_PERSON_NAME_TOKENS = {
    "university", "stadium", "college", "school", "airport", "museum", "river",
    "mountain", "county", "island", "ocean", "sea", "lake", "city", "state",
    "province", "country", "continent", "alabama", "england", "france", "india",
    "america", "american", "europe", "africa", "asia", "pacific", "atlantic",
    "bowl", "cup", "league", "championship", "team", "patriots", "seahawks",
}
_NON_PERSON_NAME_PHRASES = {"moments earlier", "super bowl", "new england"}
_NON_PERSON_NAME_LEADS = {"a", "an", "but", "and", "the", "then", "however", "earlier", "later", "moments", "at", "after", "before"}


def _normalized_person_name(value: str) -> str:
    value = re.sub(r"[.,;:!?]+$", "", str(value or "").strip())
    return re.sub(r"\s+", " ", re.sub(r"[’']s$", "", value, flags=re.I)).strip()


def _person_name_has_action(text: str, start: int, end: int) -> bool:
    """Require a nearby narrated human action before considering a name tag."""
    before = text[max(0, start - 90):start]
    after = text[end:min(len(text), end + 105)]
    # Place/institution names following locative prepositions are not identity
    # labels; in particular, a player's college is not another player.
    if re.search(r"\b(?:from|at|near|inside)\s+$", before, re.I):
        return False
    if _PERSON_ACTION.search(after) or _PERSON_ACTION.search(before):
        return True
    return bool(re.search(r"\b(?:catch|interception|goal|pass|collision|decision|victory|defeat)\b", after, re.I))


def _film_person_entities(scenes: list[Mapping[str, Any]]) -> list[str]:
    """Collect multi-word names only where the script also describes an action."""
    names: dict[str, str] = {}
    for scene in scenes:
        narration = str(scene.get("narration") or "")
        for match in _PERSON_NAME.finditer(narration):
            name = _normalized_person_name(match.group("name"))
            words = name.split()
            folded = name.casefold()
            if (len(words) < 2 or folded in _NON_PERSON_NAME_PHRASES
                    or words[0].casefold().strip(".'’-") in _NON_PERSON_NAME_LEADS
                    or any(word.casefold().strip(".'’-") in _NON_PERSON_NAME_TOKENS for word in words)
                    or not _person_name_has_action(narration, match.start(), match.end())):
                continue
            names.setdefault(folded, name)
    return list(names.values())


def _person_motion_cues(scene: dict[str, Any], film_names: list[str], fps: int) -> list[dict[str, Any]]:
    """Add brief identity tags for names tied to an action in this media beat.

    Cue anchors remain exact narration excerpts. A surname can resolve to a
    full name only when that unique full name is established elsewhere in the
    same film script.
    """
    narration = str(scene.get("narration") or "")
    primary = scene.get("primary_visual") if isinstance(scene.get("primary_visual"), Mapping) else {}
    primary_kind = str(primary.get("type") or "").casefold()
    if primary_kind not in {"smartimage", "smartvideo", "brollimage", "brollvideo", "image", "video"}:
        return []
    frame_mode = str((scene.get("text") or {}).get("frame_mode") or scene.get("frame_mode") or "").casefold()
    if frame_mode not in {"", "media_only", "media", "media_first"}:
        return []
    media = scene.get("media") if isinstance(scene.get("media"), Mapping) else {}
    asset = primary.get("asset") if isinstance(primary.get("asset"), Mapping) else {}
    if not (media.get("src") or media.get("path") or media.get("shots") or asset.get("src") or asset.get("path")):
        return []

    canonical_names = [str(name).strip() for name in film_names if str(name).strip()]
    by_surname: dict[str, list[str]] = {}
    for name in canonical_names:
        by_surname.setdefault(name.split()[-1].casefold(), []).append(name)

    occurrences: list[tuple[int, int, str, str]] = []
    for match in _PERSON_NAME.finditer(narration):
        name = _normalized_person_name(match.group("name"))
        folded = name.casefold()
        if (folded in _NON_PERSON_NAME_PHRASES
                or name.split()[0].casefold().strip(".'’-") in _NON_PERSON_NAME_LEADS or any(
            word.casefold().strip(".'’-") in _NON_PERSON_NAME_TOKENS for word in name.split()
        )):
            continue
        if _person_name_has_action(narration, match.start(), match.end()):
            occurrences.append((match.start(), match.end(), match.group("name"), name))

    # Resolve unambiguous surname references (e.g. “Wilson throws”) using the
    # full name already established in this film's narrated context.
    for canonical in canonical_names:
        surname = canonical.split()[-1]
        if len(by_surname.get(surname.casefold(), [])) != 1:
            continue
        for match in re.finditer(r"(?<!\w)" + re.escape(surname) + r"(?!\w)", narration, re.I):
            if _person_name_has_action(narration, match.start(), match.end()):
                occurrences.append((match.start(), match.end(), match.group(0), canonical))

    cues: list[dict[str, Any]] = []
    seen: set[str] = set()
    for start, end, spoken, canonical in sorted(occurrences, key=lambda item: item[0]):
        key = canonical.casefold()
        if key in seen:
            continue
        spoken_match = re.search(re.escape(spoken), narration[start:end], re.I)
        anchor = spoken_match.group(0) if spoken_match else narration[start:end]
        anchor = re.sub(r"[.,;:!?]+$", "", anchor).strip()
        local = _spoken_copy_time(scene, anchor)
        if local is None:
            local = _number(scene.get("duration")) * start / max(1, len(narration))
        duration_seconds = 1.75
        remaining = _number(scene.get("duration")) - local
        if local < 0 or remaining < 1.35:
            continue
        cues.append({
            "element": "person", "action": "reveal", "anchor_text": anchor,
            "display_text": canonical, "time_local": round(local, 3),
            "duration_seconds": duration_seconds,
            "source": "script_grounded_name_context",
        })
        seen.add(key)
        if len(cues) == 2:
            break
    return cues


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _verified_render_transcode(shot: Mapping[str, Any], path: str, verified: str, expected: str | None) -> bool:
    """Accept only the pipeline's recorded, byte-bound decode-compatible transcode.

    The review hash remains bound to the original bytes. This record permits a
    deterministic codec/container adaptation for rendering without claiming
    that the transformed bytes received a separate vision review.
    """
    transform = shot.get('render_transform')
    if not isinstance(transform, Mapping):
        return False
    if (transform.get('schema_version') != 'render_media_normalization.v1'
            or transform.get('kind') != 'codec_transcode'
            or transform.get('tool') != 'vidzeo_media_snapshot_v1'
            or str(transform.get('render_codec') or '').casefold() != 'h264'):
        return False
    source_path = Path(str(transform.get('source_path') or '')).expanduser()
    render_path = Path(str(transform.get('render_path') or '')).expanduser()
    if not source_path.is_file() or not render_path.is_file():
        return False
    try:
        source_path = source_path.resolve()
        render_path = render_path.resolve()
        shot_path = Path(path).expanduser().resolve()
        if render_path != shot_path:
            return False
        source_sha = _sha256_file(source_path)
        render_sha = _sha256_file(render_path)
    except (OSError, RuntimeError, ValueError):
        return False
    return bool(
        source_sha
        and source_sha == str(transform.get('source_sha256') or '')
        and source_sha == verified
        and (not expected or source_sha == expected)
        and render_sha
        and render_sha == str(transform.get('render_sha256') or '')
    )


def _narrated_score_comparison(narration: str) -> dict[str, Any] | None:
    """Bind an explicit two-team lead/trail score to its correct labels."""
    # Two separately narrated team scores are also an explicit comparison;
    # they do not need a 'versus' token or a lead/trail phrase. Bind each value
    # to its own exact team label instead of assuming number order is a label.
    explicit = list(re.finditer(
        r"(?P<label>[A-Z][\w&.'’-]*(?:\s+[A-Z][\w&.'’-]*){0,4})\s+"
        r"(?:scored|scores|finished\s+with)\s+(?P<value>\d{1,3})(?!\d|[.,]\d)",
        str(narration or ""),
    ))
    if len(explicit) == 2:
        left, right = explicit
        units = []
        for match in explicit:
            tail = str(narration or "")[match.end():]
            unit = re.match(r"\s*(%|points?\b|touchdowns?\b|goals?\b|runs?\b|wins?\b|percent\b)", tail, re.I)
            if unit:
                units.append(unit[1].casefold())
            elif re.match(r"\s+[a-zA-Z]", tail) and not re.match(r"\s+(?:and|while|but)\b", tail, re.I):
                return None  # An unknown measurement cannot become a unitless score.
            else:
                units.append("")
        normalized = [unit.rstrip('s') for unit in units]
        if normalized[0] != normalized[1] or normalized[0] in {'%', 'percent'}:
            return None
        if left['label'].casefold() != right['label'].casefold():
            values = [match['value'] + (f" {unit}" if unit else "")
                      for match, unit in zip(explicit, units)]
            return {'leftLabel': left['label'], 'leftValue': values[0],
                    'rightLabel': right['label'], 'rightValue': values[1]}
    # The score may be followed by a contrast clause (“28–24, but ...”). The
    # relation before the score is still an exact, local binding; anchoring the
    # score to sentence-end silently dropped those common documentary lines.
    score_pattern = re.compile(r"(?P<score_a>\d{1,3})\s*[–—-]\s*(?P<score_b>\d{1,3})(?!\d)")
    relation_pattern = re.compile(
        r"\b(?P<relation>leads?|trails?|is ahead of|is behind|are leading|are trailing|are ahead of|are behind)\b\s+",
        re.IGNORECASE,
    )
    for clause in re.split(r"[.!?;:\n]+", str(narration or "")):
        score_match = score_pattern.search(clause.strip())
        if not score_match:
            continue
        before_score = clause.strip()[:score_match.start()].strip(" ,:-")
        relation_match = relation_pattern.search(before_score)
        if not relation_match:
            continue
        first = before_score[:relation_match.start()].strip(" ,:-")
        # A clause may start with an event/date preface ("At Super Bowl XLIX, ...").
        # The team is the final phrase before the lead/trail verb, after that
        # appositive comma. Do not feed the event title into the team label.
        if "," in first:
            first = first.rsplit(",", 1)[-1].strip(" ,:-")
        second = before_score[relation_match.end():].strip(" ,:-")
        first = re.sub(r"^(?:the|a|an)\s+", "", first, flags=re.IGNORECASE)
        second = re.sub(r"^(?:the|a|an)\s+", "", second, flags=re.IGNORECASE)
        first = re.sub(r"\s+", " ", first).strip(" ,:-")
        second = re.sub(r"\s+", " ", second).strip(" ,:-")
        if not first or not second or first.casefold() == second.casefold():
            continue
        score_a, score_b = int(score_match.group('score_a')), int(score_match.group('score_b'))
        if score_a == score_b:
            # A tie cannot ground a narrated lead/trail relationship.
            return None
        relation = relation_match.group('relation').casefold()
        first_is_leader = relation.startswith('lead') or relation in {'is ahead of', 'are leading', 'are ahead of'}
        leading_score, trailing_score = max(score_a, score_b), min(score_a, score_b)
        # Team order in prose is not necessarily scoreboard order. The explicit
        # relation binds the higher value to the leader and the lower value to
        # the trailer; the spoken number order alone must never relabel teams.
        first_value, second_value = (
            (leading_score, trailing_score) if first_is_leader
            else (trailing_score, leading_score)
        )
        return {
            'leftLabel': first,
            'leftValue': str(first_value),
            'rightLabel': second,
            'rightValue': str(second_value),
        }
    return None


def _visual_requirement_context(scene: Mapping[str, Any]) -> tuple[str, str]:
    """Return a scene's canonical visual entity and its grounded search context."""
    primary = scene.get("primary_visual") if isinstance(scene.get("primary_visual"), Mapping) else {}
    asset = primary.get("asset") if isinstance(primary.get("asset"), Mapping) else {}
    resolution = asset.get("resolution") if isinstance(asset.get("resolution"), Mapping) else {}
    final_context = resolution.get("final_scene_context") if isinstance(resolution.get("final_scene_context"), Mapping) else {}
    requirement = final_context.get("visual_requirement") if isinstance(final_context.get("visual_requirement"), Mapping) else {}
    canonical = str(requirement.get("canonical_entity") or final_context.get("canonical_entity") or "").strip()
    evidence = [
        *list(requirement.get("required_terms") or []),
        *list(requirement.get("positive_evidence") or []),
        *list(final_context.get("positive_subjects") or []),
    ]
    context = " ".join(str(value) for value in evidence if value)
    return canonical, context


def _scene_has_graphic_fallback_media(scene: Mapping[str, Any]) -> bool:
    media = scene.get("media") if isinstance(scene.get("media"), Mapping) else {}
    if str(media.get("source") or "").casefold() == "graphic":
        return True
    if str(media.get("resolution_kind") or "").casefold() in {"graphic_fallback", "grounded_graphic_fallback"}:
        return True
    shots = media.get("shots") if isinstance(media.get("shots"), list) else []
    return any(
        isinstance(shot, Mapping)
        and (
            str(shot.get("verification") or "").casefold() == "graphic_fallback"
            or str(shot.get("fallback_layer") or "").casefold().startswith("degraded:")
        )
        for shot in shots
    )


def _media_identity_text(scene: Mapping[str, Any]) -> str:
    media = scene.get("media") if isinstance(scene.get("media"), Mapping) else {}
    pieces = [str(scene.get("narration") or ""), str(scene.get("visual_intent") or "")]
    for shot in media.get("shots") or []:
        if isinstance(shot, Mapping):
            pieces.extend((str(shot.get("source_url") or ""), str(shot.get("visual_reference") or "")))
    canonical, context = _visual_requirement_context(scene)
    pieces.extend((canonical, context))
    return re.sub(r"[^a-z0-9]+", " ", " ".join(pieces).casefold()).strip()


def _local_visual_context(scenes: list[Any], index: int) -> str:
    """Include adjacent script/source context when a beat itself is a short fact."""
    pieces: list[str] = []
    for neighbor_index in (index - 1, index, index + 1):
        if not 0 <= neighbor_index < len(scenes):
            continue
        scene = scenes[neighbor_index]
        if not isinstance(scene, Mapping):
            continue
        pieces.extend((str(scene.get("narration") or ""), str(scene.get("visual_intent") or ""), _media_identity_text(scene)))
    return re.sub(r"[^a-z0-9]+", " ", " ".join(pieces).casefold()).strip()


def _verified_still_for_reuse(scene: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Only return a local, previously verified still from the same film."""
    media = scene.get("media") if isinstance(scene.get("media"), Mapping) else {}
    if str(media.get("source") or "").casefold() == "graphic":
        return None
    shots = media.get("shots") if isinstance(media.get("shots"), list) else []
    for raw_shot in shots:
        if not isinstance(raw_shot, Mapping):
            continue
        shot = dict(raw_shot)
        kind = str(shot.get("media_type") or shot.get("type") or media.get("type") or "").casefold()
        verification = str(shot.get("verification") or "").casefold()
        fallback = str(shot.get("fallback_layer") or "").casefold()
        path = str(shot.get("asset_path") or shot.get("path") or media.get("src") or media.get("path") or "").strip()
        if kind not in {"image", "still", "photo"} or verification not in {"verified", "vision_verified", "exact_passage_verified"}:
            continue
        if fallback.startswith("degraded:") or not path:
            continue
        try:
            if not Path(path).expanduser().is_file():
                continue
        except (OSError, ValueError):
            continue
        return path, shot
    return None


def _repair_graphic_fallback_media(scenes: list[Any], *, fps: int) -> list[int]:
    """Fill a missing-media beat from a verified, nonadjacent still of the same entity.

    Unresolved graphic plates can otherwise become the full-bleed background
    for perfectly good data/person cues. Reuse is limited to a film-wide
    canonical-entity match, verified local stills, nonadjacent scenes, and the
    source scene's declared reuse budget. The director's graphic and narration
    remain unchanged.
    """
    use_counts: Counter[str] = Counter()
    candidates: list[tuple[int, Mapping[str, Any], str, dict[str, Any], str]] = []
    for source_index, source_scene in enumerate(scenes):
        if not isinstance(source_scene, Mapping):
            continue
        resolved = _verified_still_for_reuse(source_scene)
        if not resolved:
            continue
        path, shot = resolved
        asset_key = str(shot.get("asset_fingerprint") or Path(path).expanduser().resolve()).casefold()
        use_counts[asset_key] += 1
        candidates.append((source_index, source_scene, path, shot, asset_key))

    repaired: list[int] = []
    for target_index, target_scene in enumerate(scenes):
        if not isinstance(target_scene, dict) or not _scene_has_graphic_fallback_media(target_scene):
            continue
        # Specialist canvases may intentionally own a no-footage scene. This
        # recovery is for a media-led treatment whose fallback plate is being
        # used as footage behind overlays.
        primary = target_scene.get("primary_visual") if isinstance(target_scene.get("primary_visual"), Mapping) else {}
        primary_kind = str(primary.get("type") or "").casefold()
        if primary_kind not in {"smartimage", "smartvideo", "brollimage", "brollvideo", "image", "video"}:
            continue
        canonical, target_evidence = _visual_requirement_context(target_scene)
        normalized_entity = re.sub(r"[^a-z0-9]+", " ", canonical.casefold()).strip()
        if len(normalized_entity.split()) < 2:
            continue
        context = _local_visual_context(scenes, target_index)
        context_tokens = set(re.findall(r"[a-z0-9]+", context))
        entity_tokens = set(re.findall(r"[a-z0-9]+", normalized_entity))
        context_tokens -= entity_tokens
        context_tokens -= {
            "this", "that", "with", "from", "into", "over", "under", "before", "after", "between",
            "there", "their", "they", "them", "then", "when", "where", "while", "what", "which",
            "have", "has", "had", "were", "was", "will", "would", "could", "should", "does", "did",
            "and", "the", "for", "its", "his", "her", "our", "your", "into", "just", "still",
            "seconds", "second", "game", "day", "exact", "visual", "chronological", "anchor", "establish",
            "final", "drive", "super", "bowl", "xlix",
        }
        best: tuple[float, int, Mapping[str, Any], str, dict[str, Any], str] | None = None
        for source_index, source_scene, path, shot, asset_key in candidates:
            if source_index == target_index or abs(source_index - target_index) <= 1:
                continue
            source_media = source_scene.get("media") if isinstance(source_scene.get("media"), Mapping) else {}
            source_identity = _media_identity_text(source_scene)
            source_entity, _ = _visual_requirement_context(source_scene)
            source_entity_norm = re.sub(r"[^a-z0-9]+", " ", source_entity.casefold()).strip()
            if normalized_entity != source_entity_norm and not re.search(r"\b" + re.escape(normalized_entity) + r"\b", source_identity):
                continue
            # Existing policies default to one cross-scene reuse. A declared
            # two-use, nonadjacent allowance (the common documentary policy)
            # permits one intentional callback within the same film.
            source_primary = source_scene.get("primary_visual") if isinstance(source_scene.get("primary_visual"), Mapping) else {}
            source_asset = source_primary.get("asset") if isinstance(source_primary.get("asset"), Mapping) else {}
            source_resolution = source_asset.get("resolution") if isinstance(source_asset.get("resolution"), Mapping) else {}
            source_context = source_resolution.get("final_scene_context") if isinstance(source_resolution, Mapping) else {}
            reuse_policy = source_context.get("reuse_policy") if isinstance(source_context, Mapping) and isinstance(source_context.get("reuse_policy"), Mapping) else {}
            max_uses = max(1, int(_number(reuse_policy.get("max_uses"), 2)))
            if use_counts[asset_key] >= max_uses:
                continue
            score = 10.0
            # A date or location anchor borrows only within its exact named
            # documentary entity. Favor a source that also shares the local
            # explanatory context, but never broaden to niche-only matching.
            source_tokens = set(re.findall(r"[a-z0-9]+", source_identity))
            source_tokens -= entity_tokens
            score += min(2.0, 0.25 * len(context_tokens & source_tokens))
            # Match role as well as subject: a short date/venue beat should not
            # borrow a future player action merely because it is the same game.
            target_role_words = set(re.findall(r"[a-z0-9]+", str(target_scene.get("visual_intent") or "").casefold()))
            source_role_words = set(re.findall(r"[a-z0-9]+", str(source_scene.get("visual_intent") or "").casefold()))
            if target_role_words & source_role_words:
                score += 0.15
            if re.search(r"\b(?:date|chronolog|venue|stadium|location|establish)\b", str(target_scene.get("visual_intent") or "").casefold()):
                source_narration = str(source_scene.get("narration") or "").casefold()
                source_url = " ".join(str(item.get("source_url") or "") for item in source_media.get("shots") or [] if isinstance(item, Mapping)).casefold()
                if not re.search(r"\b(?:date|venue|stadium|location|university|phoenix|glendale|arena|city|field)\b", source_narration + " " + source_url):
                    score -= 0.45
            # If the late source is explicitly a post-outcome beat, avoid
            # spoiling it as an opening fallback when an earlier visual fits.
            source_narration = str(source_scene.get("narration") or "").casefold()
            if source_index > target_index and re.search(r"\b(?:wins?|won|holds on|title|champion|celebration|victory|final score)\b", source_narration):
                score -= 0.75
            if best is None or score > best[0]:
                best = (score, source_index, source_scene, path, shot, asset_key)
        if best is None:
            continue

        _, source_index, source_scene, path, source_shot, asset_key = best
        target_media = target_scene.get("media") if isinstance(target_scene.get("media"), Mapping) else {}
        original = {key: target_media.get(key) for key in ("src", "source", "resolution_kind") if target_media.get(key) is not None}
        target_duration = max(1 / max(1, fps), _number(target_scene.get("duration")))
        replacement = copy.deepcopy(dict(source_scene.get("media") or {}))
        replacement["src"] = path
        replacement["path"] = path
        replacement["type"] = "image"
        replacement["source"] = str(replacement.get("source") or source_shot.get("source") or "verified")
        replacement["fit"] = "cover"
        # Clone only the exact reviewed still we selected. Copying the source
        # scene's other shots while pointing all of them at the first file
        # would carry mismatched review hashes onto that file.
        replacement_shot = copy.deepcopy(source_shot)
        replacement_shot.update({
            "path": path, "asset_path": path, "shot_start": 0.0,
            "shot_end": target_duration, "duration": target_duration,
            "reuse_kind": "same_entity_nonadjacent_media_recovery",
            "fallback_layer": "same_identity_cross_scene",
        })
        replacement["shots"] = [replacement_shot]
        target_scene["media"] = replacement
        if isinstance(primary, dict) and isinstance(primary.get("asset"), Mapping):
            primary_asset = copy.deepcopy(dict(primary["asset"]))
            primary_asset.update({"src": path, "path": path, "type": "image", "source": replacement["source"]})
            primary["asset"] = primary_asset
            target_scene["primary_visual"] = {**dict(primary), "asset": primary_asset}
        target_scene["media_repair"] = {
            "source": "production_grounding_guard",
            "decision": "reused_verified_same_entity_still",
            "source_scene_index": source_index,
            "source_asset_fingerprint": source_shot.get("asset_fingerprint") or "",
            "entity": canonical,
            "reuse_kind": "same_entity_nonadjacent_media_recovery",
            "original_fallback": original,
        }
        use_counts[asset_key] += 1
        repaired.append(target_index)
    return repaired


def visible_perceptual_repeats(manifest: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate visible repeats from source media hidden by full-canvas graphics."""
    shot_plan = manifest.get("media_shot_plan") if isinstance(manifest.get("media_shot_plan"), Mapping) else {}
    audit = shot_plan.get("perceptual_repetition_audit") if isinstance(shot_plan.get("perceptual_repetition_audit"), Mapping) else {}
    rows = audit.get("unresolved_repeats") if isinstance(audit.get("unresolved_repeats"), list) else []
    scenes = manifest.get("scenes") if isinstance(manifest.get("scenes"), list) else []
    visible, hidden = [], []
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        try:
            index = int(row.get("scene_index", -1))
        except (TypeError, ValueError):
            index = -1
        if index < 0 or index >= len(scenes):
            # Missing scene context cannot prove that the repeated media was
            # hidden, so keep the conservative release blocker.
            visible.append(row)
            continue
        scene = scenes[index] if 0 <= index < len(scenes) and isinstance(scenes[index], Mapping) else {}
        media = scene.get("media_composition") if isinstance(scene.get("media_composition"), Mapping) else {}
        if not media:
            try:
                media = plan_media_composition(scene)
            except Exception:
                media = {}
        primary = scene.get("primary_visual") if isinstance(scene.get("primary_visual"), Mapping) else {}
        source_media = scene.get("media") if isinstance(scene.get("media"), Mapping) else {}
        asset = primary.get("asset") if isinstance(primary.get("asset"), Mapping) else {}
        has_media_source = bool(source_media.get("src") or source_media.get("path") or source_media.get("shots") or asset.get("src") or asset.get("path"))
        hidden_from_output = (
            has_media_source
            and (
                owns_explanatory_canvas(scene)
                or str(media.get("render_mode") or "").lower() == "explanatory_canvas"
                or (str(media.get("hero_layer") or "").lower() == "graphic" and _number(media.get("media_visibility"), 1.0) <= 0.01)
            )
        )
        (hidden if hidden_from_output else visible).append(row)
    return visible, hidden


def audit_production_requirements(manifest: Mapping[str, Any]) -> dict[str, Any]:
    errors, warnings = [], []
    signatures = []
    file_digests = {}
    perceptual = (manifest.get('media_shot_plan') or {}).get('perceptual_repetition_audit') or {}
    visible_repeats, hidden_repeats = visible_perceptual_repeats(manifest)
    if perceptual:
        # Retain the raw source audit for diagnosis while gating on what is
        # actually visible in the final Remotion composition.
        perceptual['visible_unresolved_repeats'] = visible_repeats
        perceptual['hidden_under_explanatory_canvas'] = hidden_repeats
        perceptual['visible_status'] = 'needs_revision' if visible_repeats else 'pass'
    if visible_repeats:
        # When every repeat is marked 'no_distinct_verified_local_alternate' it
        # means the repair pipeline exhausted all options and no better media
        # exists.  Demote to warning so the video still renders — similar
        # adjacent images are acceptable when the director applies different
        # overlays, text, and motion treatments.
        all_exhausted = all(
            isinstance(r, Mapping)
            and str(r.get('reason', '')).startswith('no_distinct')
            for r in visible_repeats
        )
        entry = {'code':'unresolved_perceptual_repetition',
                 'repeats':visible_repeats,
                 'message':'Different files or timestamps still show the same imagery. Supply a distinct verified shot before export.'}
        if all_exhausted:
            entry['message'] += ' (demoted: no distinct verified alternate exists)'
            warnings.append(entry)
        else:
            errors.append(entry)
    if perceptual.get('status') == 'incomplete':
        warnings.append({'code':'perceptual_review_incomplete','message':'Some actual media frames could not be checked for repetition.'})
    for index, scene in enumerate(manifest.get("scenes") or []):
        primary = scene.get("primary_visual") or {}
        kind = str(primary.get("type") or "").lower()
        if scene.get("director_treatment_enabled") is True or str(scene.get("visual_role_source") or "").lower() in {"llm_director", "llm_pixel_director", "system_grounded_mapping"}:
            template_id = str(scene.get("template_id") or (scene.get("graphic") or {}).get("template_id") or "").strip().lower().replace("_", "-")
            primary_component = SPECIALISTS.get(kind)
            expected_type = TEMPLATE_PRIMARY_TYPES.get(template_id)
            expected_graphic_component = (
                scene.get("component_mapping") if template_id == "full-bleed"
                else "MetricGrid" if template_id == "metric-grid"
                else "DeclarativeRecipe" if template_id == "generated-recipe"
                else SPECIALISTS.get(str(expected_type or "").lower(), expected_type or "")
            )
            if not template_id:
                errors.append({"scene_index": index, "code": "director_template_missing"})
            elif template_id != "generated-recipe" and template_id not in TEMPLATE_PRIMARY_TYPES:
                errors.append({"scene_index": index, "code": "director_template_unmapped", "template_id": template_id})
            elif expected_graphic_component and scene.get("graphic_component_mapping") != expected_graphic_component:
                errors.append({"scene_index": index, "code": "director_template_component_mismatch", "template_id": template_id, "expected": expected_graphic_component, "actual": scene.get("graphic_component_mapping")})
            if not primary_component:
                errors.append({"scene_index": index, "code": "director_primary_component_unmapped", "primary_type": primary.get("type")})
        props = primary.get("props") or {}
        req = script_requirements(scene.get("narration") or "")
        map_shot = scene.get("map_shot") or props.get("map_shot") or {}
        recipe_map = any(layer.get("kind") == "map" for layer in (scene.get("template_recipe") or {}).get("layers", []))
        if req["map_required"] and kind not in {"animatedmap", "satellitemap"} and not (recipe_map and map_shot.get("enabled")):
            errors.append({"scene_index": index, "code": "required_map_not_routed", "message": "The narration explicitly requests a map. Supply grounded geography and a map specialist; a diagram is not a substitute."})
        if owns_explanatory_canvas(scene) and scene.get("media_composition"):
            if scene["media_composition"].get("render_mode") != "explanatory_canvas":
                errors.append({"scene_index": index, "code": "explanation_canvas_conflict"})
        start = _number(scene.get("start"))
        duration = _number(scene.get("duration"))
        for shot in (scene.get("media") or {}).get("shots", []):
            decision = shot.get("visual_decision") or {}
            expected = shot.get("verified_asset_sha256") or shot.get("asset_sha256")
            verified = decision.get("verified_asset_sha256")
            if expected and verified and expected != verified:
                errors.append({"scene_index": index, "code": "shot_verification_asset_mismatch"})
            path = str(shot.get('path') or shot.get('asset_path') or '')
            if verified and shot.get('verification_scope') != 'inherited_parent' and path and Path(path).is_file():
                if path not in file_digests:
                    try:
                        file_digests[path] = _sha256_file(Path(path))
                    except OSError:
                        file_digests[path] = ''
                if file_digests[path] != verified and not _verified_render_transcode(shot, path, str(verified), str(expected) if expected else None):
                    errors.append({'scene_index':index, 'code':'verified_shot_changed_after_review', 'path':path})
            if shot.get("verification_status") in {"rejected", "failed", "blocked"}:
                errors.append({"scene_index": index, "code": "rejected_shot_in_timeline"})
        for cue in scene.get("sound_cues") or []:
            local = _number(cue.get("time_local"), _number(cue.get("start"), start) - start)
            if local < 0 or local >= duration:
                errors.append({"scene_index": index, "code": "sound_event_outside_scene"})
        # Fingerprints include layout/motion rather than just a palette name.
        recipe = scene.get("template_recipe") or {}
        recipe_copy_contract = scene.get('recipe_copy_contract')
        if recipe_copy_contract is not None:
            valid_contract = (
                isinstance(recipe_copy_contract, Mapping)
                and recipe_copy_contract.get('version') == 1
                and recipe_copy_contract.get('source') == 'pixel_director_narration_span'
                and recipe_copy_contract.get('approved') is True
                and isinstance(recipe_copy_contract.get('spans'), list)
            )
            recipe_texts = {
                str(layer.get('text') or '').strip().casefold()
                for layer in recipe.get('layers', []) if isinstance(layer, Mapping)
                and str(layer.get('text') or '').strip()
            }
            authorized_recipe_texts = set()
            if valid_contract:
                from .director import _valid_director_copy
                for span in recipe_copy_contract['spans']:
                    if (not isinstance(span, Mapping)
                            or set(span) != {'display_text', 'source_span'}
                            or str(span.get('display_text') or '').strip().casefold() not in recipe_texts
                            or not _valid_director_copy(
                                str(scene.get('narration') or ''),
                                span.get('display_text'), span.get('source_span'),
                            )):
                        valid_contract = False
                        break
                    authorized_recipe_texts.add(str(span.get('display_text') or '').strip().casefold())
            if has_approved_director_copy(scene):
                authorized_recipe_texts.add(str((scene.get('editorial_contract') or {}).get('display_text') or '').strip().casefold())
            if not recipe_texts.issubset(authorized_recipe_texts):
                valid_contract = False
            if not valid_contract:
                errors.append({'scene_index': index, 'code': 'invalid_recipe_copy_provenance'})
        structural = {"kind": kind, "variant": primary.get("variant"), "motion": (scene.get("camera") or {}).get("motion"),
                      "layers": [{k: layer.get(k) for k in ("kind", "x", "y", "width", "height", "motion", "parent_id")} for layer in recipe.get("layers", [])]}
        signature = hashlib.sha256(json.dumps(structural, sort_keys=True, default=str).encode()).hexdigest()[:16]
        signatures.append(signature)
        if len(signatures) > 1 and signatures[-1] == signatures[-2]:
            warnings.append({"scene_index": index, "code": "adjacent_composition_repeat", "message": "Revise hierarchy or explain intentional continuity; do not merely swap colors."})
    diagrams = [scene for scene in manifest.get('scenes') or [] if str((scene.get('primary_visual') or {}).get('type') or '').lower() == 'diagram']
    director = manifest.get('director') or {}
    if director.get('visual_quality_status') == 'needs_revision' and director.get('unresolved_visual_scene_indices'):
        errors.append({'code':'unresolved_visual_revision', 'scene_indices':director['unresolved_visual_scene_indices'], 'message':'The bounded director revision left sparse explanations unresolved. Review these scenes before export.'})
    if (manifest.get('director') or {}).get('visual_quality_status') == 'needs_revision' and len(diagrams) >= 3 and len(diagrams) / max(1, len(manifest.get('scenes') or [])) >= .6:
        errors.append({'code':'unresolved_diagram_overuse','message':'The bounded creative revision did not resolve excessive diagrams. Revise the affected beats before export.'})
    return {"status": "fail" if errors else "pass", "errors": errors, "warnings": warnings,
            "structural_signatures": signatures, "signature_counts": dict(Counter(signatures))}


def compile_production_timeline(manifest: dict[str, Any]) -> dict[str, Any]:
    """Attach inspectable tracks using final timing, without changing copy."""
    # Reconcile only explicit map requests after the final scene choices are
    # available. This creates a truthful global locator where supported, never
    # an unspoken route.
    reconcile_global_map_requests(manifest)
    if manifest.get('research_ledger'):
        from ..research_ledger import reconcile_research_source_marks
        reconcile_research_source_marks(manifest)
    fps = max(1, _number(manifest.get("fps"), 30))
    scenes = manifest.get("scenes") or []
    _repair_graphic_fallback_media(scenes, fps=fps)
    film_names = _film_person_entities([scene for scene in scenes if isinstance(scene, Mapping)])
    tracks = {name: [] for name in ("media", "graphics", "copy", "camera", "transitions", "narration", "captions", "music", "sfx")}
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        start = round(_number(scene.get("start")) * fps)
        duration = max(1, round(_number(scene.get("duration")) * fps))
        scene["production_requirements"] = script_requirements(scene.get("narration") or "")
        scene["film_person_entities"] = list(film_names)
        existing_cues = [cue for cue in scene.get("motion_cues") or [] if isinstance(cue, Mapping)]
        existing_person_keys = {
            _normalized_person_name(str(cue.get("display_text") or cue.get("anchor_text") or "")).casefold()
            for cue in existing_cues if cue.get("element") == "person"
        }
        auto_people = [cue for cue in _person_motion_cues(scene, film_names, fps)
                       if cue["display_text"].casefold() not in existing_person_keys]
        # Keep the director's three-cue budget, replacing only low-information
        # generic holds/images when an exact narrated name would otherwise be
        # omitted. Copy, map, data, and comparison cues always keep priority.
        for cue in auto_people:
            cues = list(scene.get("motion_cues") or [])
            if len(cues) >= 3:
                replace_index = next((i for i, current in enumerate(cues)
                                      if isinstance(current, Mapping)
                                      and current.get("element") in {"image", "source", "steps"}
                                      and str(current.get("action") or "").lower() == "hold"), None)
                if replace_index is None:
                    replace_index = next((i for i, current in enumerate(cues)
                                          if isinstance(current, Mapping)
                                          and current.get("element") == "image"), None)
                if replace_index is None:
                    continue
                cues.pop(replace_index)
            cues.append(cue)
            scene["motion_cues"] = sorted(cues, key=lambda item: _number(item.get("time_local")))
        primary_before_mapping = scene.get("primary_visual") if isinstance(scene.get("primary_visual"), Mapping) else {}
        graphic_before_recipe_check = scene.get("graphic") if isinstance(scene.get("graphic"), Mapping) else {}
        recipe_candidate = scene.get("template_recipe")
        if not isinstance(recipe_candidate, Mapping):
            recipe_candidate = (primary_before_mapping.get("props") or {}).get("template_recipe")
        if not isinstance(recipe_candidate, Mapping):
            recipe_candidate = (graphic_before_recipe_check.get("props") or {}).get("template_recipe")
        requested_template = str(
            scene.get("template_id") or graphic_before_recipe_check.get("template_id") or ""
        ).strip().lower().replace("_", "-")
        if requested_template == "generated-recipe" and not _recipe_has_grounded_visual_content(
            recipe_candidate if isinstance(recipe_candidate, Mapping) else {}, scene,
        ):
            # A blank or ungrounded model recipe is not allowed to replace the
            # verified shot. Restore its native primary component and record
            # the reason, so the motion cue layer can still add a small,
            # non-overlapping narration-grounded treatment.
            primary_props = dict(primary_before_mapping.get("props") or {})
            primary_props.pop("template_recipe", None)
            scene["primary_visual"] = {**dict(primary_before_mapping), "props": primary_props}
            scene.pop("template_recipe", None)
            scene.pop("recipe_copy_contract", None)
            fallback_template = (
                "full-bleed" if str(primary_before_mapping.get("type") or "").lower()
                in {"smartimage", "smartvideo", "brollimage", "brollvideo", "image", "video"}
                else RENDERER_TEMPLATES.get(str(primary_before_mapping.get("type") or ""), "full-bleed")
            )
            scene["template_id"] = fallback_template
            graphic_props = dict(graphic_before_recipe_check.get("props") or {})
            graphic_props.pop("template_recipe", None)
            scene["graphic"] = {**dict(graphic_before_recipe_check), "template_id": fallback_template,
                                "props": graphic_props}
            scene["recipe_repair"] = {
                "source": "production_grounding_guard",
                "decision": "rejected",
                "reason": "recipe_has_no_grounded_visual_content",
                "fallback_template": fallback_template,
            }
            primary_before_mapping = scene["primary_visual"]
        score_pair = _narrated_score_comparison(scene.get("narration") or "")
        if score_pair and str(primary_before_mapping.get('type') or '') in {
            'SmartImage', 'SmartVideo', 'StatCard', 'KineticText',
        }:
            # A narrated lead/trail score is a paired fact, not a single
            # counter. Route it to the specialist comparison and bind each
            # value to the team named by the sentence.
            scene['primary_visual'] = {
                **dict(primary_before_mapping),
                'type': 'Comparison',
                'variant': 'versus_contrast_clash',
                'props': {**score_pair, 'variant': 'versus_contrast_clash', 'transparent_canvas': True},
            }
            has_media = bool(
                (scene.get("media") or {}).get("src") or (scene.get("media") or {}).get("path")
                or (scene.get("media") or {}).get("shots")
            )
            scene['visual_role'] = 'media_overlay' if has_media else 'primary_explanation'
            scene['visual_role_source'] = 'system_grounded_mapping'
            scene['template_id'] = 'split-screen'
            # Replace a prior stat-card's legacy graphic payload too; otherwise
            # stale display text can leak back through the compatibility bridge.
            scene['graphic'] = {
                'template_id': 'split-screen',
                'variant': 'versus_contrast_clash',
                'props': {**score_pair, 'variant': 'versus_contrast_clash', 'transparent_canvas': True},
            }
            scene.setdefault('text', {})['frame_mode'] = 'data'
            narration = str(scene.get("narration") or "")
            cue_phrase = next((label for label in (score_pair.get("leftLabel"), score_pair.get("rightLabel"))
                               if label and re.search(re.escape(label), narration, re.I)), "")
            cue_local = _spoken_copy_time(scene, cue_phrase) if cue_phrase else None
            if cue_local is None and cue_phrase:
                match = re.search(re.escape(cue_phrase), narration, re.I)
                cue_local = _number(scene.get("duration")) * match.start() / max(1, len(narration)) if match else 0.0
            cue_local = max(0.0, min(max(0.0, _number(scene.get("duration")) - .01), cue_local or 0.0))
            graphic_duration = min(duration, max(1, round(min(2.4, _number(scene.get("duration"), duration / fps)) * fps)))
            graphic_start = min(max(0, duration - graphic_duration), max(0, round(cue_local * fps)))
            scene['editor_graphic_window'] = {
                'start_frame': graphic_start, 'duration_frames': graphic_duration,
                'clock': 'scene_local_frames', 'reason': 'exact_narrated_score',
            }
        primary = scene.get("primary_visual") if isinstance(scene.get("primary_visual"), Mapping) else {}
        primary_kind = str(primary.get("type") or "").lower()
        _sync_director_graphic_to_primary(scene, primary)
        requested_role = str(scene.get("visual_role") or "").strip().lower()
        role_source = str(scene.get("visual_role_source") or "").strip().lower()
        if role_source in {"llm_director", "llm_pixel_director", "system_grounded_mapping"} and requested_role in {"media_overlay", "primary_explanation", "map_canvas"}:
            visual_role = "map_canvas" if primary_kind in {"animatedmap", "satellitemap"} else requested_role
        else:
            # Recompile legacy auto-assigned roles using the v2 default: maps
            # own a clean cartographic canvas, while supported graphics layer
            # over the verified image or clip unless direction says otherwise.
            visual_role = "map_canvas" if primary_kind in {"animatedmap", "satellitemap"} else "media_overlay"
            scene["visual_role_source"] = "system_default"
        scene["visual_role"] = visual_role
        scene["visual_role_contract_version"] = 2
        # The final component, media, and visual role are authoritative. Always
        # rebuild the core routing so an earlier full-canvas decision cannot
        # survive a late component change; retain authored layout details only
        # when the prior compositor explicitly belongs to this same component.
        # A single date or title is an annotation, not a replacement visual.
        # Preserve the sourced image/clip underneath these brief graphics.
        primary_media = scene.get('primary_visual') or {}
        source_asset = primary_media.get('asset') or {}
        source_media = scene.get('media') or {}
        real_media = bool(
            (source_media.get('src') or source_media.get('path'))
            and not source_asset.get('is_graphic_fallback')
            and str(source_asset.get('type') or source_media.get('type') or '').lower()
            in {'image', 'video', 'smartimage', 'smartvideo'}
        )
        component = str(primary_media.get('type') or '').lower()
        events = (primary_media.get('props') or {}).get('events') or []
        brief_annotation = component in {'kinetictext', 'chaptertitle'} or (
            component == 'timeline' and isinstance(events, list) and len(events) < 2
        )
        if real_media and brief_annotation:
            scene['visual_role'] = 'media_overlay'
            scene['visual_role_source'] = 'media_required_annotation'
            source_media['is_graphic_fallback'] = False
            source_media['type'] = str(source_asset.get('type') or source_media.get('type') or 'image').lower()
        previous_composition = scene.get("media_composition") if isinstance(scene.get("media_composition"), Mapping) else {}
        planned_composition = plan_media_composition(scene)
        previous_component = str(previous_composition.get("component_type") or "").strip().lower()
        if previous_component and previous_component == primary_kind:
            for key in (
                "overlay_grammar", "composition_family", "composition_variant",
                "composition_fingerprint", "swarm_motion_family", "overlay_anchor",
                "overlay_scale", "overlay_background", "graphic_timing",
                "center_data_visual", "blur_media_underlay",
            ):
                if key in previous_composition:
                    planned_composition[key] = previous_composition[key]
            if planned_composition.get("hero_layer") == "source_media":
                planned_composition["placement"] = previous_composition.get("placement", planned_composition.get("placement"))
        scene["media_composition"] = planned_composition
        primary = scene.get("primary_visual") or {}
        scene["component_mapping"] = SPECIALISTS.get(str(primary.get("type") or "").lower(), primary.get("type"))
        template_id = str(scene.get("template_id") or (scene.get("graphic") or {}).get("template_id") or "full-bleed").strip().lower().replace("_", "-")
        mapped_template_type = TEMPLATE_PRIMARY_TYPES.get(template_id)
        scene["graphic_component_mapping"] = (
            scene["component_mapping"] if template_id == "full-bleed"
            else "MetricGrid" if template_id == "metric-grid"
            else "DeclarativeRecipe" if template_id == "generated-recipe"
            else SPECIALISTS.get(str(mapped_template_type or "").lower(), mapped_template_type or scene["component_mapping"])
        )
        common = {"scene_index": index, "start_frame": start, "duration_frames": duration}
        media = scene.get("media") or primary.get("asset") or {}
        tracks["media"].append({**common, "shots": media.get("shots") or [], "asset": media.get("src") or media.get("path"),
                                 "visible_role": scene["visual_role"], "repair": scene.get("media_repair")})
        graphic_start = max(0, int(scene.get("editor_graphic_window", {}).get("start_frame", 0)))
        graphic_duration = max(1, min(duration - graphic_start, int(scene.get("editor_graphic_window", {}).get("duration_frames", duration))))
        tracks["graphics"].append({**common, "start_frame": start + graphic_start, "duration_frames": graphic_duration,
                                   "component": scene["graphic_component_mapping"], "template_id": template_id, "recipe": scene.get("template_recipe")})
        tracks["camera"].append({**common, "direction": scene.get("camera") or {}})
        tracks["narration"].append({**common, "text": scene.get("narration") or ""})
        tracks["captions"].append({**common, "word_cues": scene.get("word_cues") or []})
        tracks["transitions"].append({**common, "transition": scene.get("transition") or "cut"})
        contract = scene.get("editorial_contract") or {}
        if has_approved_director_copy(scene):
            copy = contract["display_text"]
            word_count = len(copy.split())
            hold = min(2.6, max(1.6, 1.35 + word_count * .22)) if primary.get("type") == "ChapterTitle" else min(2.4 if word_count > 3 else 2.2, max(1.3, .92 + word_count * .32))
            copy_duration = min(duration, max(1, round(hold * fps)))
            cue = next((cue for cue in scene.get("motion_cues") or [] if cue.get("element") == "copy"), {})
            narration = str(scene.get("narration") or "")
            offset = narration.lower().find(copy.lower())
            estimated = offset / max(1, len(narration)) * duration / fps if offset >= 0 else .08
            spoken = _spoken_copy_time(scene, copy)
            frame_mode = str((scene.get("text") or {}).get("frame_mode") or "").strip().lower()
            # The cold-open phrase is a teaser, so it lands with the opening
            # frame instead of waiting for the last words of a question to be
            # spoken. All later emphasis remains narration-aligned.
            local = (
                0.0
                if index == 0 and frame_mode in {"kinetic", "chapter"}
                else (spoken if spoken is not None else _number(cue.get("time_local"), estimated))
            )
            copy_start = min(duration - 1, max(0, round(local * fps)))
            copy_duration = min(copy_duration, duration - copy_start)
            scene["copy_window"] = {"start_frame": copy_start, "duration_frames": copy_duration, "clock": "scene_local_frames"}
            tracks["copy"].append({**common, "start_frame": start + copy_start, "duration_frames": copy_duration,
                                   "text": contract["display_text"], "emphasis_spans": contract.get("emphasis_spans") or [], "approved": True})
        else:
            scene.pop("copy_window", None)
        # Expose timed director callouts as actual events on the graphics
        # track, but only when their verified source media is visible and no
        # approved copy or specialist graphic owns the same frames.
        narration = str(scene.get("narration") or "")
        media_primary = primary_kind in {"smartimage", "smartvideo", "brollimage", "brollvideo", "image", "video"}
        allowed_people = {str(name).casefold() for name in scene.get("film_person_entities") or []}
        copy_window = scene.get("copy_window") if isinstance(scene.get("copy_window"), Mapping) else {}
        for cue in scene.get("motion_cues") or []:
            if not isinstance(cue, Mapping) or cue.get("element") not in {"person", "data"}:
                continue
            element = str(cue.get("element"))
            anchor = str(cue.get("anchor_text") or "").strip()
            display = str(cue.get("display_text") or anchor).strip()
            if not anchor or not re.search(re.escape(anchor), narration, re.I):
                continue
            if element == "person" and (display.casefold() not in allowed_people and display.casefold() != anchor.casefold()):
                continue
            if element == "data" and not any(char.isdigit() for char in anchor) and not re.search(
                r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December|"
                r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
                r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand)\b",
                anchor, re.I,
            ):
                continue
            if not media_primary or template_id != "full-bleed":
                continue
            local = max(0.0, _number(cue.get("time_local")))
            if local >= _number(scene.get("duration")):
                continue
            if element == "person":
                cue_seconds = min(1.9, max(1.35, _number(cue.get("duration_seconds"), 1.75)))
                component, cue_template, copy_source = "DirectorPersonTag", "person-name-tag", "exact_script_entity"
            else:
                cue_seconds = min(2.2, max(1.35, .95 + len(anchor.split()) * .2))
                component, cue_template, copy_source = "DirectorDataCallout", "exact_script_data_cue", "exact_script_data_cue"
            cue_duration = min(cue_seconds, _number(scene.get("duration")) - local)
            cue_start = round(local * fps)
            cue_end = cue_start + round(cue_duration * fps)
            if cue_duration < 1.35:
                continue
            copy_start = int(copy_window.get("start_frame", -1))
            copy_end = copy_start + int(copy_window.get("duration_frames", 0))
            if copy_start >= 0 and cue_start < copy_end and cue_end > copy_start:
                continue
            editor_window = scene.get("editor_graphic_window") if isinstance(scene.get("editor_graphic_window"), Mapping) else {}
            graphic_start = int(editor_window.get("start_frame", -1))
            graphic_end = graphic_start + int(editor_window.get("duration_frames", 0))
            if graphic_start >= 0 and cue_start < graphic_end and cue_end > graphic_start:
                continue
            tracks["graphics"].append({
                "scene_index": index, "start_frame": start + cue_start,
                "duration_frames": max(1, round(cue_duration * fps)),
                "component": component, "template_id": cue_template,
                "text": display, "anchor_text": anchor, "copy_source": copy_source,
            })
        retained_sound_cues = []
        for cue in scene.get("sound_cues") or []:
            if cue.get('visual_trigger') and str(cue.get('event_id') or '').endswith(':copy:reveal'):
                if not copy_window:
                    continue
                visual_time = int(copy_window.get('start_frame', 0)) / fps
                onset = max(0.0, _number(cue.get('transient_offset_seconds')) - _number(cue.get('trim_start_seconds')))
                local_start = max(0.0, visual_time - onset)
                cue.update(time_local=local_start, scene_local_start=local_start,
                           scene_local_start_seconds=local_start, start=start / fps + local_start,
                           visual_event_time_local=visual_time,
                           sound_anchor=contract.get('display_text', ''), timing_quality='copy_window_aligned',
                           transient_alignment_error_seconds=abs(local_start + onset - visual_time))
            local = _number(cue.get("time_local"), _number(cue.get("start"), start / fps) - start / fps)
            retained_sound_cues.append(cue)
            tracks["sfx"].append({**cue, "scene_index": index, "start_frame": start + round(local * fps)})
        scene["sound_cues"] = retained_sound_cues
    audio = manifest.get("audio") or {}
    timed_effects = {cue.get('event_id'): cue for cue in tracks['sfx'] if cue.get('event_id')}
    effects = audio.get('effects') or []
    if isinstance(effects, list):
        audio['effects'] = [cue for cue in effects if not (isinstance(cue, dict)
            and cue.get('visual_trigger') and str(cue.get('event_id') or '').endswith(':copy:reveal')
            and cue.get('event_id') not in timed_effects)]
    for cue in audio.get('effects') or []:
        if isinstance(cue, dict) and cue.get('event_id') in timed_effects:
            cue.update(timed_effects[cue['event_id']])
    tracks["music"] = list(audio.get("music") or []) if isinstance(audio.get("music"), list) else ([audio["music"]] if audio.get("music") else [])
    manifest["editing_timeline"] = {"version": 1, "clock": "film_global_frames", "fps": fps, "tracks": tracks}
    manifest['review_frames'] = [round(t * fps) for t in review_sample_times(manifest, _number(manifest.get('duration'), sum(_number(scene.get('duration')) for scene in manifest.get('scenes') or [])))]
    manifest["production_audit"] = audit_production_requirements(manifest)
    try:
        from .manager import manager_review
        manifest["quality_control"] = manager_review(manifest)
        manifest.setdefault("metrics", {})["manager_ready_for_long_render"] = bool(
            manifest["quality_control"].get("ready_for_long_render")
        )
    except Exception as exc:
        manifest["quality_control"] = {
            "status": "unavailable",
            "ready_for_long_render": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        manifest.setdefault("metrics", {})["manager_ready_for_long_render"] = False
    return manifest


def reconcile_global_map_requests(manifest: dict[str, Any]) -> list[int]:
    """Restore an explicit map request with a narrated global scope only.

    An Earth overview does not establish a date-specific route. Preserve that
    distinction in the manifest and leave requests with no known scope blocked.
    """
    scenes=manifest.get('scenes') or []
    passage=' '.join(str(scene.get('narration') or '') for scene in scenes)
    if not re.search(r'\bEarth\b',passage,re.I):return []
    repaired=[]
    for index,scene in enumerate(scenes):
        primary=scene.get('primary_visual') or {}
        if not script_requirements(scene.get('narration',''))['map_required']:
            continue
        if str(primary.get('type','')).lower() in {'animatedmap','satellitemap'}:
            if (scene.get('map_shot') or {}).get('geographic_scope')=='Earth':
                scene.setdefault('text',{})['frame_mode']='map'
            continue
        shot={'enabled':True,'render_style':'globe_3d','location_names':[], 'feature_ids':[], 'route_id':'',
              'source_ids':[],'motion_profile':'world_overview','geographic_scope':'Earth',
              'scope_evidence':'Earth','scope_evidence_clock':'film_narration',
              'route_status':'not_established_by_script'}
        props={'variant':'country_spotlight','map_shot':shot,'map_render_style':'globe_3d',
               'globe_center':{'lon':0,'lat':0},'show_labels':False,'display_text':''}
        scene['primary_visual']={**primary,'type':'AnimatedMap','variant':'country_spotlight','props':props}
        scene['map_shot']=shot
        scene['template_id']='map-route'
        scene['template_recipe']={}
        scene['graphic']={'template_id':'map-route','props':props}
        scene.setdefault('text',{})['frame_mode']='map'
        scene['mapping_repair']={'source':'explicit_script_requirement','scope':'Earth','invented_route':False}
        repaired.append(index)
    return repaired


def review_sample_times(manifest: Mapping[str, Any], video_duration: float) -> list[float]:
    """Capture an economical, scene-complete set of opening/reveal/hold frames."""
    if video_duration <= 0:
        return []
    fps = max(1, _number(manifest.get("fps"), 30))
    # Global samples are useful for detecting a broken cold-open, middle or
    # ending, but the previous five global positions duplicated the scene
    # samples and made one-minute films render 70+ stills in every review round.
    times = [.1, video_duration * .52, video_duration - .1]
    for scene in manifest.get("scenes") or []:
        start, duration = _number(scene.get("start")), _number(scene.get("duration"))
        if duration <= 0:
            continue
        # Every scene needs actual visual evidence, including ordinary media
        # where irrelevant subjects or repeated-looking clips can hide.
        primary = scene.get("primary_visual") if isinstance(scene.get("primary_visual"), Mapping) else {}
        specialist_graphic = str(primary.get("type") or "").lower() in {
            "chart", "timeline", "diagram", "processflow", "comparison",
            "animatedmap", "satellitemap", "documenthighlight", "personprofile", "statcard",
        }
        if specialist_graphic or scene.get("template_recipe"):
            # Keep four meaningful stages for data/map/diagram graphics.
            # Ordinary footage beats need only an early composition and a
            # settled hold; their other frames are redundant with the global
            # review and narration-aligned copy sample below.
            ratios = (.06, .30, .65, .94)
        else:
            ratios = (.78,)
        times.extend(start + min(duration - 1 / fps, duration * ratio) for ratio in ratios)
        cues = scene.get("motion_cues") or []
        if cues:
            # Sample the first narration-aligned action without rendering a
            # second redundant still after every cue in the same beat.
            local = max(0.0, min(duration - 1 / fps, _number(cues[0].get("time_local"))))
            times.append(start + local)
        window = scene.get("copy_window") or {}
        if window.get("duration_frames"):
            times.append(start + (_number(window.get("start_frame")) + _number(window.get("duration_frames")) * .6) / fps)
    return sorted({round(max(.01, min(video_duration - .01, time)), 3) for time in times})
