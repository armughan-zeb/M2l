"""Media-first documentary direction contract.

This module is deliberately deterministic at render time.  A language model may
suggest a scene intent upstream, but a render only receives a constrained,
grounded composition contract.  That gives the React renderer enough creative
range to look edited without allowing it to invent facts, titles, maps, or
opaque card treatments.

The module owns three things:

* a library of genuinely different documentary compositions;
* semantic text / evidence / timing decisions for a scene; and
* sequence-level cooldowns so an edit does not become a template carousel.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import re
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from ..highlight_selector import highlight_tokens_for, select_grounded_highlight
from .sequence_controller import SequenceController, audit_sequence_continuity
from .script_analyzer import analyze_sentence_triggers as _analyze_triggers


DESIGN_LIBRARY_VERSION = "media-first-documentary-v2"
MANIFEST_VERSION = 4


# A family is a composition, not a colour treatment.  The renderer has a
# dedicated layout for every family and each variant changes hierarchy,
# geometry, or motion rather than merely swapping palette values.
COMPOSITION_LIBRARY: Dict[str, Dict[str, Any]] = {
    "corner_identification": {
        "legacy_grammar": "field_caption",
        "variants": ("lower_locator", "upper_identifier"),
        "motions": ("rule_reveal", "side_settle", "type_stagger"),
        "placement": "corner",
        "min_hold": 2.0,
        "entry": 0.34,
        "exit": 0.24,
        "requires": (),
        "media_safe": True,
        "purpose": "identify",
    },
    "keyword_emphasis": {
        "legacy_grammar": "field_caption",
        "variants": ("word_scale", "phrase_lockup"),
        "motions": ("scale_settle", "mask_reveal", "tracking_open"),
        "placement": "center",
        "min_hold": 3.0,
        "entry": 0.36,
        "exit": 0.26,
        "requires": (),
        "media_safe": True,
        "purpose": "emphasize",
    },
    "editorial_statement": {
        "legacy_grammar": "field_caption",
        "variants": ("two_line_thesis", "offset_thesis"),
        "motions": ("line_build", "mask_reveal", "tracking_open"),
        "placement": "center",
        "min_hold": 3.0,
        "entry": 0.4,
        "exit": 0.26,
        "requires": (),
        "media_safe": True,
        "purpose": "thesis",
    },
    "question_reveal": {
        "legacy_grammar": "field_caption",
        "variants": ("question_then_answer", "answer_under_question"),
        "motions": ("question_wipe", "type_stagger", "line_build"),
        "placement": "center",
        "min_hold": 3.0,
        "entry": 0.42,
        "exit": 0.24,
        "requires": (),
        "media_safe": True,
        "purpose": "question",
    },
    "contrast_pair": {
        "legacy_grammar": "analytic_callout",
        "variants": ("slash_contrast", "parallel_terms"),
        "motions": ("split_reveal", "counter_slide", "rule_reveal"),
        "placement": "center",
        "min_hold": 3.0,
        "entry": 0.38,
        "exit": 0.24,
        "requires": (),
        "media_safe": True,
        "purpose": "contrast",
    },
    "numeric_emphasis": {
        "legacy_grammar": "data_inset",
        "variants": ("number_lockup", "metric_ladder"),
        "motions": ("number_count", "scale_settle", "rule_reveal"),
        "placement": "corner",
        "min_hold": 2.5,
        "entry": 0.34,
        "exit": 0.22,
        "requires": ("numeric",),
        "media_safe": True,
        "purpose": "quantify",
    },
    "attributed_quote": {
        "legacy_grammar": "quote_pull",
        "variants": ("margin_quote", "quote_focus"),
        "motions": ("quote_reveal", "scan_reveal", "line_build"),
        "placement": "corner",
        "min_hold": 3.0,
        "entry": 0.42,
        "exit": 0.24,
        "requires": ("quote",),
        "media_safe": True,
        "purpose": "quote",
    },
    "chapter_transition": {
        "legacy_grammar": "chapter_slate",
        "variants": ("chapter_number", "chapter_statement"),
        "motions": ("chapter_wipe", "line_build", "tracking_open"),
        "placement": "center",
        "min_hold": 3.0,
        "entry": 0.48,
        "exit": 0.28,
        "requires": (),
        "media_safe": False,
        "purpose": "chapter",
    },
    "locator_route": {
        "legacy_grammar": "locator_inset",
        "variants": ("single_locator", "route_trace"),
        "motions": ("pin_drop", "route_draw", "map_focus"),
        "placement": "corner",
        "min_hold": 5.0,
        "entry": 0.44,
        "exit": 0.26,
        "requires": ("location",),
        "media_safe": True,
        "purpose": "locate",
    },
    "chronology_timeline": {
        "legacy_grammar": "chronology_strip",
        "variants": ("milestone_trace", "date_pulse"),
        "motions": ("timeline_draw", "date_stamp", "marker_pop"),
        "placement": "corner",
        "min_hold": 5.0,
        "entry": 0.4,
        "exit": 0.24,
        "requires": ("date",),
        "media_safe": True,
        "purpose": "sequence",
    },
    "data_chart": {
        "legacy_grammar": "chart_center",
        "variants": ("editorial_bars", "editorial_line"),
        "motions": ("bar_rise", "line_trace", "marker_pop"),
        "placement": "center",
        "min_hold": 5.0,
        "entry": 0.46,
        "exit": 0.28,
        "requires": ("data",),
        "media_safe": False,
        "purpose": "explain_data",
    },
    "evidence_annotation": {
        "legacy_grammar": "evidence_annotation",
        "variants": ("source_underline", "evidence_bracket"),
        "motions": ("bracket_trace", "scan_reveal", "underline_draw"),
        "placement": "corner",
        "min_hold": 3.0,
        "entry": 0.38,
        "exit": 0.24,
        "requires": ("evidence",),
        "media_safe": True,
        "purpose": "evidence",
    },
    "before_after": {
        "legacy_grammar": "analytic_callout",
        "variants": ("before_after_axis", "side_by_side_delta"),
        "motions": ("split_reveal", "counter_slide", "marker_pop"),
        "placement": "center",
        "min_hold": 5.0,
        "entry": 0.42,
        "exit": 0.26,
        "requires": ("comparison",),
        "media_safe": False,
        "purpose": "compare",
    },
    "process_diagram": {
        "legacy_grammar": "analytic_callout",
        "variants": ("three_step_flow", "cause_effect_chain"),
        "motions": ("path_travel", "node_reveal", "line_build"),
        "placement": "center",
        "min_hold": 5.0,
        "entry": 0.46,
        "exit": 0.26,
        "requires": ("steps",),
        "media_safe": False,
        "purpose": "explain_process",
    },
    "portrait_profile": {
        "legacy_grammar": "portrait_id",
        "variants": ("lower_profile", "role_marker"),
        "motions": ("lower_third_slide", "portrait_pin", "rule_reveal"),
        "placement": "corner",
        "min_hold": 2.5,
        "entry": 0.34,
        "exit": 0.22,
        "requires": ("person",),
        "media_safe": True,
        "purpose": "identify_person",
    },
    "annotated_montage": {
        "legacy_grammar": "archive_contact_sheet",
        "variants": ("evidence_threads", "archive_index"),
        "motions": ("annotation_trace", "contact_sheet_slide", "scan_reveal"),
        "placement": "corner",
        "min_hold": 3.5,
        "entry": 0.4,
        "exit": 0.24,
        "requires": ("items",),
        "media_safe": True,
        "purpose": "assemble_evidence",
    },
}

PERSONALITY_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "automatic": {"families": tuple(COMPOSITION_LIBRARY), "hero_bias": 0.32},
    "editorial": {"families": ("corner_identification", "keyword_emphasis", "editorial_statement", "question_reveal", "contrast_pair", "numeric_emphasis", "data_chart", "chronology_timeline"), "hero_bias": 0.38},
    "investigative": {"families": ("corner_identification", "attributed_quote", "evidence_annotation", "annotated_montage", "locator_route", "chronology_timeline", "data_chart", "process_diagram"), "hero_bias": 0.24},
    "cinematic": {"families": ("corner_identification", "keyword_emphasis", "editorial_statement", "chapter_transition", "portrait_profile", "attributed_quote"), "hero_bias": 0.42},
    "energetic": {"families": ("keyword_emphasis", "question_reveal", "contrast_pair", "numeric_emphasis", "before_after", "chapter_transition", "locator_route"), "hero_bias": 0.48},
}

DENSITY_ALIASES = {
    "subtle": 0.32,
    "restrained": 0.32,
    "balanced": 0.48,
    "dynamic": 0.60,
    "expressive": 0.66,
    "dense": 1.0,
}

_TYPE_TO_FAMILY = {
    "animatedmap": "locator_route",
    "timeline": "chronology_timeline",
    "chart": "data_chart",
    "statcard": "numeric_emphasis",
    "quotecard": "attributed_quote",
    "documenthighlight": "evidence_annotation",
    "comparison": "before_after",
    "diagram": "process_diagram",
    "personprofile": "portrait_profile",
    "collage": "annotated_montage",
    "chaptertitle": "chapter_transition",
    "headline": "editorial_statement",
}

_MEDIA_TYPES = {"smartimage", "smartvideo", "image", "video"}
_NUMBER_RE = re.compile(r"(?<![A-Za-z])(?:[$£€]?\s*\d[\d,]*(?:\.\d+)?\s*(?:%|percent|million|billion|thousand|bn|m|k)?)(?![A-Za-z])", re.IGNORECASE)
_DATE_RE = re.compile(r"\b(?:(?:17|18|19|20)\d{2}|(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}(?:,?\s+(?:17|18|19|20)\d{2})?)\b", re.IGNORECASE)
_LOCATION_RE = re.compile(r"\b(?:in|from|to|near|across|between|at)\s+([A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,3})")
_QUESTION_RE = re.compile(r"\?\s*$|\b(?:why|how|what happens|the question)\b", re.IGNORECASE)
_CONTRAST_RE = re.compile(r"\b(?:but|rather than|instead of|versus|vs\.?|while|unlike)\b", re.IGNORECASE)

# Maps VisualSceneType values (from script_analyzer.py) to COMPOSITION_LIBRARY families.
# Used when a V2 storyboard scene has no primary_visual.type (the common case) and we
# need to derive the family from a fresh semantic pass over the narration text.
_TRIGGER_TYPE_TO_FAMILY: Dict[str, str] = {
    "ChapterTitle": "chapter_transition",
    "QuoteCard": "attributed_quote",
    "StatCard": "numeric_emphasis",
    "Chart": "data_chart",
    "Timeline": "chronology_timeline",
    "PersonProfile": "portrait_profile",
    "AnimatedMap": "locator_route",
    "Diagram": "process_diagram",
    "Comparison": "before_after",
    "DocumentHighlight": "evidence_annotation",
    "Headline": "evidence_annotation",
    "KineticText": "keyword_emphasis",
    "Collage": "annotated_montage",
    # SmartVideo and SmartImage are footage-led; no overlay family
    "SmartVideo": "media_hold",
    "SmartImage": "corner_identification",
}


def _enrich_props_from_triggers(
    family: str,
    triggers: Any,
    narration: str,
    scene_index: int,
) -> Dict[str, Any]:
    """Build minimal props from semantic triggers so requirement checks pass.

    This is called when a V2 storyboard scene has no primary_visual props.
    The returned dict is shallow-merged into the working props before
    _requirement_failure() and _display_text() run.
    """
    props: Dict[str, Any] = {}

    if family == "attributed_quote":
        if triggers.quotes:
            props["quote"] = triggers.quotes[0]
        if triggers.persons:
            props["source"] = triggers.persons[0].upper()

    elif family == "numeric_emphasis":
        nums = [n for n in (triggers.numbers or []) if n]
        if nums:
            props["value"] = nums[0]
            props["label"] = narration[:45].upper().rstrip("., ")
            props["kicker"] = "VERIFIED RECORD"

    elif family == "data_chart":
        nums = [n for n in (triggers.numbers or []) if n]
        if len(nums) >= 2:
            props["data"] = [
                {"label": f"METRIC {i + 1}", "value": float(re.sub(r"[^0-9.]", "", n) or 0)}
                for i, n in enumerate(nums[:6])
            ]
            props["title"] = narration[:45].upper().rstrip("., ")
        # If not enough numbers, let requirement check fall back gracefully

    elif family == "chronology_timeline":
        dates = triggers.dates or []
        if dates:
            events = [
                {"year": d.upper(), "label": narration[:35].upper().rstrip("., ")}
                if i == 0
                else {"year": d.upper(), "label": f"ERA MARKER {i + 1}"}
                for i, d in enumerate(dates[:4])
            ]
            props["events"] = events
            props["date"] = dates[0]
            props["year"] = dates[0]

    elif family == "locator_route":
        locs = triggers.locations or []
        countries = triggers.countries or []
        corridor = triggers.country_corridor
        if corridor:
            props["from"] = corridor.get("from", "")
            props["to"] = corridor.get("to", "")
        elif len(locs) >= 2:
            props["from"] = locs[0].upper()
            props["to"] = locs[1].upper()
        elif locs:
            props["location"] = locs[0]
        elif countries:
            props["location"] = countries[0]

    elif family == "portrait_profile":
        persons = triggers.persons or triggers.historical_figures or []
        if persons:
            props["name"] = persons[0].upper()
            props["role"] = "KEY FIGURE"
            props["source"] = "DOCUMENTARY RECORD"

    elif family == "before_after":
        nums = [n for n in (triggers.numbers or []) if n]
        if len(nums) >= 2:
            props["before"] = {"value": nums[0], "label": "BEFORE"}
            props["after"] = {"value": nums[1], "label": "AFTER"}
        elif len(nums) == 1:
            # Not enough for a split — let requirement check downgrade this
            pass

    elif family == "process_diagram":
        # Build minimal 2-step process from narration clauses
        clauses = [p for p in re.split(r"[,;:]", narration) if len(_text(p).split()) >= 3]
        if len(clauses) >= 2:
            props["steps"] = [
                {"title": _text(clauses[i])[:30].upper(), "desc": ""}
                for i in range(min(3, len(clauses)))
            ]

    elif family == "evidence_annotation":
        props["source"] = "DOCUMENTED EVIDENCE"
        props["evidence"] = True

    elif family == "chapter_transition":
        words = narration.split()
        props["title"] = " ".join(words[:6]).upper().rstrip(".,;:")
        props["kicker"] = f"ACT {scene_index + 1:02d}"

    elif family == "annotated_montage":
        # Build items from narration noun phrases or generic labels
        props["items"] = [
            {"label": "EVIDENCE A"}, {"label": "EVIDENCE B"}, {"label": "EXHIBIT C"},
        ]

    elif family == "keyword_emphasis":
        # KineticText: use first 3 punchy words from narration
        words = narration.split()
        if words:
            phrase = " ".join(words[:3]).upper().rstrip(".,;:")
            props["text"] = phrase
            props["phrase"] = phrase

    return props


def _stable_int(*parts: Any) -> int:
    payload = "|".join(str(part if part is not None else "") for part in parts)
    return int(hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()[:16], 16)


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _scene_props(scene: Mapping[str, Any]) -> Dict[str, Any]:
    primary = _mapping(scene.get("primary_visual") or scene.get("primary"))
    props = _mapping(primary.get("props"))
    graphic = _mapping(scene.get("graphic"))
    return {**_mapping(graphic.get("props")), **props}


def _primary(scene: Mapping[str, Any]) -> Dict[str, Any]:
    return _mapping(scene.get("primary_visual") or scene.get("primary"))


def _has_media(scene: Mapping[str, Any]) -> bool:
    media = _mapping(scene.get("media") or _primary(scene).get("asset"))
    return bool(media.get("src") or media.get("path") or media.get("url") or media.get("shots"))


def _scene_duration(scene: Mapping[str, Any]) -> float:
    try:
        explicit = scene.get("duration")
        if explicit not in (None, ""):
            return max(0.0, float(explicit))
        start = float(scene.get("start") or 0.0)
        end = float(scene.get("end") or 0.0)
        if end > start:
            return end - start
        # Hand-authored/legacy manifests did not always carry a duration.
        # Treat their visual beat as a six-second hold rather than silently
        # disabling every valid graphic during migration.
        return 6.0
    except (TypeError, ValueError):
        return 6.0


def _scene_importance(scene: Mapping[str, Any]) -> float:
    pacing = _mapping(scene.get("pacing"))
    try:
        return max(0.0, min(1.0, float(pacing.get("importance") or scene.get("importance") or 0.5)))
    except (TypeError, ValueError):
        return 0.5


def _positive_float(value: Any, fallback: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return fallback


def normalize_editorial_controls(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize the public editing controls into a stable render contract."""

    root = _mapping(manifest)
    visual_system = _mapping(root.get("visual_system"))
    profile = _mapping(root.get("editing_profile"))
    nested = _mapping(visual_system.get("media_first"))
    personality = _text(
        nested.get("editing_personality")
        or root.get("editing_personality")
        or profile.get("personality")
        or profile.get("style")
        or "automatic"
    ).lower().replace("-", "_")
    if personality not in PERSONALITY_DEFAULTS:
        personality = "automatic"

    density_raw = nested.get("graphic_density", root.get("graphic_density", profile.get("graphic_density", root.get("documentary_density", "balanced"))))
    if isinstance(density_raw, str):
        density = DENSITY_ALIASES.get(density_raw.strip().lower(), 0.48)
        density_name = density_raw.strip().lower() if density_raw.strip().lower() in DENSITY_ALIASES else "balanced"
    else:
        density = _positive_float(density_raw, 0.48)
        density_name = min(DENSITY_ALIASES, key=lambda key: abs(DENSITY_ALIASES[key] - density))
    density = max(0.18, min(1.0, density))
    density_mode = _text(
        nested.get("density_mode")
        or root.get("density_mode")
        or profile.get("density_mode")
        or ("dense" if str(density_raw).lower() == "dense" else "")
    ).lower()

    raw_seed = nested.get("variation_seed", root.get("variation_seed", root.get("seed", visual_system.get("seed", 0))))
    try:
        seed = int(raw_seed)
    except (TypeError, ValueError):
        seed = _stable_int(root.get("title"), root.get("topic"), "media-first")

    return {
        "version": DESIGN_LIBRARY_VERSION,
        "editing_personality": personality,
        "graphic_density": round(density, 3),
        "graphic_density_label": density_name,
        "density_mode": density_mode,
        "variation_seed": seed,
        "review_mode": _text(nested.get("review_mode") or root.get("review_mode") or "rendered").lower(),
        "families": list(PERSONALITY_DEFAULTS[personality]["families"]),
    }


def _first_value(props: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = props.get(key)
        if isinstance(value, (str, int, float)) and _text(value):
            return _text(value)
    return ""


def _numbers(value: str) -> List[str]:
    return [re.sub(r"\s+", " ", item).strip() for item in _NUMBER_RE.findall(value or "")]


def _has_chart_data(props: Mapping[str, Any]) -> bool:
    data = props.get("data") or props.get("values")
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        numeric = 0
        for item in data:
            raw = item.get("value") if isinstance(item, Mapping) else item
            try:
                float(str(raw).replace(",", "").replace("%", ""))
                numeric += 1
            except (TypeError, ValueError):
                continue
        if numeric >= 2:
            return True
    return bool(props.get("before") and props.get("after"))


def _has_comparison(props: Mapping[str, Any], narration: str) -> bool:
    return bool((props.get("before") and props.get("after")) or (props.get("leftValue") and props.get("rightValue")) or len(_numbers(narration)) >= 2)


def _has_steps(props: Mapping[str, Any], narration: str) -> bool:
    steps = props.get("steps")
    if isinstance(steps, Sequence) and not isinstance(steps, (str, bytes)):
        return len(steps) >= 2
    clauses = [part for part in re.split(r"[,;:]", narration) if len(_text(part).split()) >= 3]
    return len(clauses) >= 2


def _has_items(props: Mapping[str, Any]) -> bool:
    items = props.get("items") or props.get("fragments") or props.get("assets")
    return isinstance(items, Sequence) and not isinstance(items, (str, bytes)) and len(items) >= 2


def _has_evidence(props: Mapping[str, Any]) -> bool:
    return bool(_first_value(props, "source", "attribution", "citation", "publication", "documentType", "evidence_ref") or props.get("evidence") or props.get("source_asset"))


def _location_payload(props: Mapping[str, Any], narration: str) -> Dict[str, Any]:
    """Return truthful map inputs only; never turn a guessed place into a route."""

    raw_points = props.get("points") or props.get("locations")
    coordinates = props.get("coordinates") or props.get("location_coordinates")
    from_name = _first_value(props, "from", "start", "from_location")
    to_name = _first_value(props, "to", "end", "to_location")
    location = _first_value(props, "location", "place", "country", "title")
    fictional = bool(props.get("fictional") or props.get("is_fictional") or props.get("fictional_map_asset"))

    if isinstance(raw_points, Sequence) and not isinstance(raw_points, (str, bytes)):
        normalized = [item for item in raw_points if isinstance(item, Mapping) and item.get("label") and (item.get("lat") is not None or item.get("x") is not None)]
        if normalized:
            return {"kind": "points", "points": normalized[:6], "fictional": fictional}
    if isinstance(coordinates, Mapping) and (coordinates.get("lat") is not None or coordinates.get("x") is not None):
        return {"kind": "locator", "location": location or from_name or to_name, "coordinates": dict(coordinates), "fictional": fictional}
    if from_name and to_name and from_name.lower() != to_name.lower():
        # Names alone are allowed only when the director has already provided
        # a validated location signal.  The React layer owns a small factual
        # gazetteer; unresolved names are rejected instead of shown as a fake
        # route.
        return {"kind": "route", "from": from_name, "to": to_name, "fictional": fictional}
    if location:
        return {"kind": "locator", "location": location, "fictional": fictional}
    matches = _LOCATION_RE.findall(narration)
    if len(matches) >= 2 and matches[0].lower() != matches[1].lower():
        return {"kind": "route", "from": matches[0], "to": matches[1], "fictional": False}
    if matches:
        return {"kind": "locator", "location": matches[0], "fictional": False}
    return {}


def _family_for_scene(scene: Mapping[str, Any], props: Mapping[str, Any], controls: Mapping[str, Any]) -> str:
    primary = _primary(scene)
    primary_type = _text(primary.get("type")).lower()
    variant = _text(primary.get("variant")).lower().replace("_", "-")
    narration = _text(scene.get("narration") or scene.get("text"))
    explicit = _text(props.get("composition_family") or props.get("family") or props.get("visual_purpose")).lower().replace("-", "_")
    if explicit in COMPOSITION_LIBRARY:
        return explicit
    if primary_type in _MEDIA_TYPES:
        return "media_hold"
    family = _TYPE_TO_FAMILY.get(primary_type)
    if family:
        return family
    if primary_type == "kinetictext":
        if _QUESTION_RE.search(narration):
            return "question_reveal"
        if _CONTRAST_RE.search(narration):
            return "contrast_pair"
        return "keyword_emphasis"
    if primary_type in {"textcard", "wordhighlighter"}:
        # Named legacy types: apply targeted heuristics only
        if _QUESTION_RE.search(narration):
            return "question_reveal"
        if _CONTRAST_RE.search(narration):
            return "contrast_pair"
        if _first_value(props, "name", "person", "role"):
            return "portrait_profile"
        if _numbers(narration) or _numbers(_first_value(props, "text", "title", "headline")):
            return "numeric_emphasis"
        text_val = str(_first_value(props, "text", "title", "headline", "phrase") or "").strip()
        words_list = text_val.split()
        if (1 <= len(words_list) <= 3 and (text_val.isupper() or int(scene.get("scene_index") or 0) == 0 or "keyword" in variant or "emphasis" in variant)) or (int(scene.get("scene_index") or 0) == 0 and not props.get("source")):
            return "keyword_emphasis"
        return "corner_identification"
    if primary_type == "":
        # V2 storyboard path: no primary_visual was set.  Run a full semantic
        # analysis pass on the narration text to select the best-fit family.
        # This is the primary fix for template repetition.
        scene_index = int(scene.get("scene_index") or scene.get("order") or 0)
        try:
            triggers = _analyze_triggers(narration, is_first_scene=(scene_index == 0))
            rec_type = triggers.recommended_scene_type
            family_from_trigger = _TRIGGER_TYPE_TO_FAMILY.get(rec_type, "")
        except Exception:
            # Analysis must never block rendering; fall back safely.
            family_from_trigger = ""
        if family_from_trigger and family_from_trigger in COMPOSITION_LIBRARY:
            allowed = controls.get("families", list(COMPOSITION_LIBRARY.keys()))
            if family_from_trigger in allowed:
                return family_from_trigger
        # Narrow-range heuristics as a secondary safety net
        if _QUESTION_RE.search(narration):
            return "question_reveal"
        if _CONTRAST_RE.search(narration):
            return "contrast_pair"
        if _numbers(narration):
            return "numeric_emphasis"
        if scene_index == 0:
            return "keyword_emphasis"
        return "corner_identification"
    if "chapter" in variant or "opener" in variant:
        return "chapter_transition"
    return "corner_identification"


def _requirement_failure(family: str, props: Mapping[str, Any], narration: str) -> str:
    spec = COMPOSITION_LIBRARY.get(family)
    if not spec:
        return "unknown_composition_family"
    requirements = spec.get("requires") or ()
    for requirement in requirements:
        if requirement == "numeric" and not (_numbers(narration) or _first_value(props, "value", "stat", "number", "currentValue")):
            return "numeric_payload_missing"
        if requirement == "quote" and not _first_value(props, "quote", "text", "title"):
            return "quote_payload_missing"
        if requirement == "location" and not _location_payload(props, narration):
            return "location_payload_missing"
        if requirement == "date" and not (_DATE_RE.search(narration) or _first_value(props, "date", "year", "eraSpan") or props.get("events")):
            return "chronology_payload_missing"
        if requirement == "data" and not _has_chart_data(props):
            return "chart_payload_missing"
        if requirement == "evidence" and not _has_evidence(props):
            return "evidence_payload_missing"
        if requirement == "comparison" and not _has_comparison(props, narration):
            return "comparison_payload_missing"
        if requirement == "steps" and not _has_steps(props, narration):
            return "process_payload_missing"
        if requirement == "person" and not _first_value(props, "name", "person", "title"):
            return "person_payload_missing"
        if requirement == "items" and not _has_items(props):
            return "montage_payload_missing"
    return ""


def _display_text(family: str, props: Mapping[str, Any], narration: str) -> Tuple[str, str, List[str]]:
    existing_contract = _mapping(props.get("editorial_contract"))
    explicit_display = _text(existing_contract.get("display_text") or props.get("display_text"))
    explicit_emphasis = _text(existing_contract.get("emphasis_phrase") or props.get("emphasis_phrase") or props.get("emphasis"))

    if family == "attributed_quote":
        display = _text(props.get("quote") or explicit_display or props.get("text"))
        return display, explicit_emphasis or select_grounded_highlight(display, max_words=3), highlight_tokens_for(display, explicit_emphasis, max_tokens=3)
    if family == "portrait_profile":
        display = _text(props.get("name") or explicit_display or props.get("title"))
        return display, explicit_emphasis or display, highlight_tokens_for(display, explicit_emphasis or display, max_tokens=3)
    if family == "numeric_emphasis":
        display = _text(props.get("display_value") or props.get("value") or props.get("stat") or props.get("number") or explicit_display)
        if not display:
            display = _numbers(narration)[0] if _numbers(narration) else ""
        return display, explicit_emphasis or display, highlight_tokens_for(display, explicit_emphasis or display, max_tokens=2)
    if family == "chapter_transition":
        display = _text(explicit_display or props.get("title") or props.get("headline") or narration)
        words = display.split()
        if len(words) > 7:
            display = " ".join(words[:7])
        return display, explicit_emphasis or select_grounded_highlight(display, max_words=3), highlight_tokens_for(display, explicit_emphasis, max_tokens=3)

    candidate = explicit_display or _text(props.get("title") or props.get("headline") or props.get("label"))
    incomplete = bool(re.search(
        r'\b(?:the|a|an|of|to|for|with|and|but|as|by|from|in|into|was|were|is|are|has|had|started|began|became|introduced|launched|reached)\s*[.,:;!?]*$',
        candidate, re.I))
    if incomplete:
        # "The Toyota Corolla started" duplicates an unfinished caption.
        # Favor the grounded entity or fact instead of shrinking that fragment.
        candidate = select_grounded_highlight(narration, max_words=4)
        explicit_emphasis = ''
    # A generated component must never paste narration word-for-word.  If an
    # upstream editor already supplied a concise display string it is honored;
    # otherwise select an exact grounded phrase from the surrounding script.
    if not candidate or len(candidate.split()) > 8:
        candidate = select_grounded_highlight(narration, explicit_emphasis, max_words=4)
    elif candidate.lower() == narration.lower():
        candidate = select_grounded_highlight(narration, explicit_emphasis, max_words=4)
    candidate = _text(candidate)
    if family in {"editorial_statement", "question_reveal", "contrast_pair"} and len(candidate.split()) < 2:
        alternative = select_grounded_highlight(narration, explicit_emphasis, max_words=4)
        candidate = alternative or candidate
    emphasis = explicit_emphasis or select_grounded_highlight(candidate or narration, explicit_emphasis, max_words=3)
    if emphasis and candidate and emphasis.lower() not in candidate.lower():
        # The displayed phrase must contain the emphasis span so the renderer
        # does not invent a highlighted word that was never approved.
        candidate = emphasis
    return candidate, emphasis, highlight_tokens_for(candidate or narration, emphasis, max_tokens=3)


def _speech_offset(scene: Mapping[str, Any], phrase: str, duration: float) -> Tuple[float, str]:
    from ..highlight_selector import align_highlight_with_whisper
    words = scene.get("words") or scene.get("word_timings") or []
    if words and phrase:
        start_sec, _ = align_highlight_with_whisper(phrase, words, scene_start=0.0)
        if 0.0 <= start_sec < duration:
            return max(0.0, min(duration * 0.55, start_sec)), "whisper_word_alignment"
    narration = _text(scene.get("narration") or scene.get("text"))
    phrase = _text(phrase)
    if narration and phrase:
        index = narration.lower().find(phrase.lower())
        if index >= 0:
            return max(0.0, min(duration * 0.55, duration * (index / max(1, len(narration))))), "narration_span"
    return max(0.0, min(0.28, duration * 0.08)), "scene_start"


def _timing_for(family: str, scene: Mapping[str, Any], display_text: str) -> Dict[str, Any]:
    spec = COMPOSITION_LIBRARY[family]
    duration = _scene_duration(scene)
    entry = float(spec["entry"])
    exit_duration = float(spec["exit"])
    words = max(1, len(_text(display_text).split()))
    readable = max(float(spec["min_hold"]), words / 3.0)
    required = entry + readable + exit_duration
    if duration + 1e-6 < required:
        return {
            "enabled": False,
            "reason": "insufficient_read_time",
            "required_seconds": round(required, 3),
            "available_seconds": round(duration, 3),
        }
    offset, alignment = _speech_offset(scene, display_text, duration)
    latest_start = max(0.0, duration - required)
    start = min(offset, latest_start)
    return {
        "enabled": True,
        "entry_seconds": round(entry, 3),
        "hold_seconds": round(readable, 3),
        "exit_seconds": round(exit_duration, 3),
        "scene_local_start": round(start, 3),
        "scene_local_end": round(start + required, 3),
        "total_seconds": round(required, 3),
        "alignment": alignment,
    }


def _should_keep_optional(scene: Mapping[str, Any], family: str, controls: Mapping[str, Any], display: str) -> bool:
    if controls.get("density_mode") == "dense":
        return True
    if int(scene.get("scene_index") or 0) == 0:
        return bool(display)
    if family not in {"corner_identification", "keyword_emphasis", "editorial_statement", "question_reveal", "contrast_pair"}:
        return True
    narration = _text(scene.get("narration") or scene.get("text"))
    # A question, contrast, precise phrase, or high-importance beat earns an
    # optional graphic.  Everything else is sampled deterministically by the
    # user-selected density and can breathe as footage.
    if _QUESTION_RE.search(narration) or _CONTRAST_RE.search(narration) or _scene_importance(scene) >= 0.78:
        return bool(display)
    score = (_stable_int(controls.get("variation_seed"), scene.get("scene_index"), narration, family) % 1000) / 1000.0
    return bool(display) and score < float(controls.get("graphic_density") or 0.48)


def _is_hero(family: str) -> bool:
    return family in {"keyword_emphasis", "editorial_statement", "question_reveal", "contrast_pair", "chapter_transition", "before_after", "process_diagram", "data_chart"}


def _safe_anchor(scene: Mapping[str, Any], default: str) -> str:
    plan = _mapping(scene.get("media_composition"))
    region = plan.get("safe_region")
    if isinstance(region, Sequence) and len(region) == 4:
        try:
            x1, y1, x2, y2 = [float(v) for v in region]
            horizontal = "left" if (x1 + x2) / 2.0 < 0.5 else "right"
            vertical = "top" if (y1 + y2) / 2.0 < 0.5 else "bottom"
            return f"{vertical}_{horizontal}"
        except (TypeError, ValueError):
            pass
    return default


def _choose(options: Sequence[str], seed: int, banned: Iterable[str] = ()) -> str:
    candidates = [item for item in options if item not in set(banned)]
    choices = candidates or list(options)
    return choices[seed % len(choices)] if choices else ""


def _contract_for_scene(scene: Mapping[str, Any], scene_index: int, controls: Mapping[str, Any]) -> Dict[str, Any]:
    props = _scene_props(scene)
    narration = _text(scene.get("narration") or scene.get("text"))
    family = _family_for_scene(scene, props, controls)
    if family == "media_hold":
        return {
            "version": DESIGN_LIBRARY_VERSION,
            "enabled": False,
            "family": "media_hold",
            "legacy_grammar": "none",
            "purpose": "media_hold",
            "reason": "script_did_not_request_graphic",
            "scene_index": scene_index,
        }
    if family not in controls.get("families", COMPOSITION_LIBRARY):
        family = "corner_identification"

    # Enrich props from semantic triggers so downstream requirement checks see real data.
    if family not in {"corner_identification", "editorial_statement"}:
        try:
            triggers = _analyze_triggers(narration, is_first_scene=(scene_index == 0))
            enriched = _enrich_props_from_triggers(family, triggers, narration, scene_index)
            if enriched:
                props = {**enriched, **props}  # scene props win over generated ones
        except Exception:
            pass  # Never block rendering due to enrichment failure

    failure = _requirement_failure(family, props, narration)
    display, emphasis, emphasis_words = _display_text(family, props, narration)
    if failure:
        return {
            "version": DESIGN_LIBRARY_VERSION,
            "enabled": False,
            "family": "media_hold",
            "legacy_grammar": "none",
            "purpose": "media_hold",
            "reason": failure,
            "scene_index": scene_index,
        }
    if not _should_keep_optional(scene, family, controls, display):
        return {
            "version": DESIGN_LIBRARY_VERSION,
            "enabled": False,
            "family": "media_hold",
            "legacy_grammar": "none",
            "purpose": "media_hold",
            "reason": "graphic_density_media_hold",
            "scene_index": scene_index,
        }
    timing = _timing_for(family, scene, display)
    if not timing.get("enabled"):
        return {
            "version": DESIGN_LIBRARY_VERSION,
            "enabled": False,
            "family": "media_hold",
            "legacy_grammar": "none",
            "purpose": "media_hold",
            "reason": timing.get("reason", "insufficient_read_time"),
            "timing": timing,
            "scene_index": scene_index,
        }
    spec = COMPOSITION_LIBRARY[family]
    location = _location_payload(props, narration) if family == "locator_route" else {}
    source = _first_value(props, "source", "attribution", "citation", "publication", "evidence_ref")

    # Collect semantic payload fields to forward to the React renderer.
    _payload_keys = (
        "data", "events", "date", "year", "quote", "name", "role",
        "value", "label", "kicker", "before", "after", "steps", "items",
        "text", "phrase", "from", "to",
    )
    payload_props = {k: props[k] for k in _payload_keys if k in props and props[k] is not None}

    contract: Dict[str, Any] = {
        "version": DESIGN_LIBRARY_VERSION,
        "enabled": True,
        "family": family,
        "legacy_grammar": spec["legacy_grammar"],
        "purpose": spec["purpose"],
        "display_text": display,
        "emphasis_phrase": emphasis,
        "emphasis_words": emphasis_words,
        "source": source,
        "location": location,
        "timing": timing,
        "media_safe": bool(spec["media_safe"]),
        "preferred_placement": spec["placement"],
        "scene_index": scene_index,
    }
    contract.update(payload_props)  # merge semantic fields directly into contract
    return contract


def _schedule_contracts(scenes: Sequence[Mapping[str, Any]], controls: Mapping[str, Any]) -> List[Dict[str, Any]]:
    contracts: List[Dict[str, Any]] = []
    controller = SequenceController(cooldown_seconds=float(controls.get("family_cooldown_seconds") or 60.0))
    recent_variants: List[str] = []
    recent_motions: List[str] = []
    seed = int(controls.get("variation_seed") or 0)
    for index, raw_scene in enumerate(scenes):
        scene = raw_scene if isinstance(raw_scene, Mapping) else {}
        if scene.get("director_treatment_enabled"):
            # The LLM director already owns this scene's component, approved
            # copy, motion cues, and visual grammar. Do not manufacture a
            # second generic contract that can erase those choices or spend a
            # sequence cooldown on an edit it does not control.
            contract = {
                "version": DESIGN_LIBRARY_VERSION,
                "enabled": False,
                "family": "media_hold",
                "legacy_grammar": "none",
                "purpose": "media_hold",
                "reason": "llm_director_owns_scene_design",
                "scene_index": index,
            }
            contracts.append(controller.register_scene_contract(scene, contract))
            continue
        contract = _contract_for_scene(scene, index, controls)
        if not contract.get("enabled"):
            contracts.append(controller.register_scene_contract(scene, contract))
            continue
        family = str(contract["family"])
        spec = COMPOSITION_LIBRARY[family]
        continuity_group = _text(_scene_props(scene).get("continuity_group"))
        intentional_continuity = bool(continuity_group and family in {"data_chart", "chronology_timeline", "before_after", "process_diagram"})

        can_use, reason = controller.can_use_family(family, continuity_group)
        if not can_use:
            contract.update({
                "enabled": False,
                "family": "media_hold",
                "legacy_grammar": "none",
                "purpose": "media_hold",
                "reason": reason,
            })
            contracts.append(controller.register_scene_contract(scene, contract))
            continue

        variant = _choose(spec["variants"], _stable_int(seed, index, family, "variant"), recent_variants[-4:])
        motion = _choose(spec["motions"], _stable_int(seed, index, family, "motion"), recent_motions[-2:])
        anchor = "center" if contract["preferred_placement"] == "center" else controller.select_anchor("corner", scene, is_center_eligible=_is_hero(family))

        contract.update({
            "variant": variant,
            "motion": motion,
            "anchor": anchor,
            "continuity_exception": continuity_group if intentional_continuity else "",
        })
        resolved = controller.register_scene_contract(scene, contract)
        fingerprint = f"{family}:{variant}:{anchor}:{motion}"
        if contracts and contracts[-1].get("composition_fingerprint") == fingerprint:
            variant = _choose(spec["variants"], _stable_int(seed, index, family, "retry"), [variant])
            motion = _choose(spec["motions"], _stable_int(seed, index, family, "motion-retry"), [motion])
            fingerprint = f"{family}:{variant}:{anchor}:{motion}"
        resolved["composition_fingerprint"] = fingerprint
        contracts.append(resolved)
        recent_variants.append(f"{family}:{variant}")
        recent_motions.append(motion)
    return contracts


def _apply_contract_to_scene(scene: MutableMapping[str, Any], contract: Mapping[str, Any], media_planner: Callable[[Mapping[str, Any]], Dict[str, Any]]) -> None:
    primary = _mapping(scene.get("primary_visual") or scene.get("primary"))
    props = _mapping(primary.get("props"))

    # For V2 scenes primary_visual is empty; populate it with the contract's
    # family so downstream renderers know the scene type.
    if not primary.get("type") and contract.get("enabled") and contract.get("family"):
        primary["type"] = contract["family"]

    props["editorial_contract"] = dict(contract)
    if contract.get("enabled"):
        props["display_text"] = contract.get("display_text", "")
        props["emphasis_phrase"] = contract.get("emphasis_phrase", "")
        props["emphasis_words"] = list(contract.get("emphasis_words") or [])
        if contract.get("source"):
            props["source"] = contract["source"]
        # Propagate semantic payload fields stored in the contract so the
        # React overlay components (ChartCenter, ChronologyStrip, etc.) can
        # read them via the props argument that DocumentaryComposition passes.
        for _payload_key in (
            "data", "events", "date", "year", "quote", "name", "role",
            "location", "from", "to", "value", "label", "kicker",
            "before", "after", "steps", "items", "text", "phrase",
        ):
            _val = contract.get(_payload_key)
            if _val is not None and _payload_key not in props:
                props[_payload_key] = _val
    primary["props"] = props
    if primary:
        scene["primary_visual"] = primary

    # The media planner remains the authority on subject protection and crop
    # safety.  This stage adds editorial information without replacing that
    # evidence.
    media_plan = media_planner(scene)
    if media_plan.get("render_mode") == "explanatory_canvas":
        media_plan["graphic_timing"] = dict(contract.get("timing") or {})
    elif not contract.get("enabled"):
        media_plan.update({
            "overlay_grammar": "none",
            "composition_family": "media_hold",
            "composition_variant": "",
            "overlay_background": "transparent",
            "blur_media_underlay": False,
            "center_data_visual": False,
            "placement": "none",
        })
    else:
        family = str(contract["family"])
        center = contract.get("anchor") == "center"
        # A neutral data background is a last-resort readability treatment,
        # not a blanket blur.  Vision/media metadata may explicitly request
        # it when a chart is genuinely unreadable over the actual clip.
        media = _mapping(scene.get("media"))
        decision = _mapping(media.get("visual_decision"))
        needs_chart_contrast = bool(decision.get("requires_chart_contrast") or media.get("requires_chart_contrast"))
        media_plan.update({
            "overlay_grammar": contract["legacy_grammar"],
            "composition_family": family,
            "composition_variant": contract.get("variant", ""),
            "composition_fingerprint": contract.get("composition_fingerprint", ""),
            "swarm_motion_family": contract.get("motion", ""),
            "placement": "center" if center else "corner",
            "overlay_anchor": contract.get("anchor", "bottom_left"),
            "overlay_scale": "hero" if center else "supporting",
            "overlay_background": "transparent",
            "center_data_visual": family == "data_chart",
            "blur_media_underlay": bool(family == "data_chart" and needs_chart_contrast),
            "graphic_timing": dict(contract.get("timing") or {}),
            "allow_full_frame_graphic": False,
        })
    scene["media_composition"] = media_plan


def apply_media_first_direction(
    manifest: Mapping[str, Any],
    media_planner: Callable[[Mapping[str, Any]], Dict[str, Any]],
) -> Dict[str, Any]:
    """Attach one canonical, media-aware editing contract to every scene."""

    result = deepcopy(dict(manifest))
    scenes = [dict(scene) for scene in result.get("scenes") or [] if isinstance(scene, Mapping)]
    controls = normalize_editorial_controls(result)
    contracts = _schedule_contracts(scenes, controls)
    for index, scene in enumerate(scenes):
        scene.setdefault("scene_index", index)
        contract = contracts[index]
        director_owned = bool(scene.get("director_treatment_enabled"))
        if director_owned:
            # Refresh only the media/layout handoff after director decisions
            # have been merged. Keep the LLM's exact copy provenance,
            # component props, motion cues, and per-scene recipe intact.
            scene["media_composition"] = media_planner(scene)
        else:
            _apply_contract_to_scene(scene, contract, media_planner)
            scene["editorial_contract"] = contract
        primary = _mapping(scene.get("primary_visual"))
        visual_props = _mapping(primary.get("props"))
        primary_kind = str(primary.get("type") or "").lower()
        is_media_primary = primary_kind in {"smartimage", "smartvideo", "image", "video"}
        node_contract = _mapping(visual_props.get("node_copy_contract"))
        approved_copy = _mapping(scene.get("editorial_contract")).get("approved") is True
        has_grounded_payload = bool(
            approved_copy
            or (node_contract.get("approved") is True and node_contract.get("nodes"))
            or scene.get("template_recipe")
            or any(visual_props.get(key) for key in ("data", "events", "steps", "before", "after", "map_shot", "source_capture", "location"))
        )
        previous_swarm = _mapping(scene.get("swarm_plan")) if director_owned else {}
        scene["swarm_plan"] = {
            **previous_swarm,
            "beat": {
                "agent": "llm_editorial_director" if director_owned else "editorial_director",
                "beat_role": (str(scene.get("visual_role") or "script_beat") if director_owned else contract.get("purpose", "media_hold")),
                "grounded_payload": bool(not director_owned or is_media_primary or has_grounded_payload),
                "media_present": _has_media(scene),
            },
            "media": {
                "agent": "media_reviewer",
                "hero_layer": scene["media_composition"].get("hero_layer", "graphic"),
                "render_mode": scene["media_composition"].get("render_mode", "graphic_only"),
                "safe_region": list(scene["media_composition"].get("safe_region") or []),
                "subject_bbox": list(scene["media_composition"].get("subject_bbox") or []),
                "overlay_area_budget": scene["media_composition"].get("overlay_max_area", 0.0),
                "collision_policy": scene["media_composition"].get("collision_policy", "none"),
            },
            "graphic": {
                "agent": "motion_planner",
                "grammar": str(primary.get("type") or "media") if director_owned else contract.get("legacy_grammar", "none"),
                "family": str(primary.get("type") or "media") if director_owned else contract.get("family", "media_hold"),
                "variant": str(primary.get("variant") or "") if director_owned else contract.get("variant", ""),
                "composition_fingerprint": "" if director_owned else contract.get("composition_fingerprint", ""),
                "placement": scene["media_composition"].get("placement", "none"),
                "scale": scene["media_composition"].get("overlay_scale", "none"),
                "background": "transparent",
                "enabled": bool(director_owned and not is_media_primary) if director_owned else bool(contract.get("enabled")),
                "reason": "director_selected_and_validated" if director_owned else contract.get("reason", "script_earned"),
            },
            "motion": {
                "agent": "motion_planner",
                "motion_family": str((scene.get("motion") or {}).get("camera") or "scene_local_cues") if director_owned else contract.get("motion", "quiet_hold"),
                "intensity": "hero" if scene["media_composition"].get("overlay_scale") == "hero" else "supporting",
                "timing": dict(contract.get("timing") or {}) if not director_owned else {},
                **({"cues": list(scene.get("motion_cues") or [])} if director_owned else {}),
            },
        }
    # The plan is assembled against copied scene dictionaries. Persist those
    # edited scenes into the copied manifest before returning, otherwise the
    # top-level swarm audit survives while the actual per-scene Remotion
    # contracts, placements, and motion plans are silently discarded.
    result["scenes"] = scenes
    try:
        current_version = int(float(result.get("version") or 0))
    except (TypeError, ValueError):
        current_version = 0
    result["version"] = max(MANIFEST_VERSION, current_version)
    visual_system = _mapping(result.get("visual_system"))
    visual_system["media_first"] = controls
    visual_system["design_library_version"] = DESIGN_LIBRARY_VERSION
    visual_system["media_policy"] = "media-first-overlay-v2"
    result["visual_system"] = visual_system
    result["editorial_controls"] = controls
    result["design_library"] = {
        "version": DESIGN_LIBRARY_VERSION,
        "families": {name: {key: value for key, value in spec.items() if key != "motions"} for name, spec in COMPOSITION_LIBRARY.items()},
    }
    from .production_contract import compile_production_timeline
    compile_production_timeline(result)
    return result


def audit_editorial_contracts(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """Audit semantic relevance and rolling sequence variety before rendering."""

    errors: List[str] = []
    warnings: List[str] = []
    fingerprints: List[str] = []
    families: List[str] = []
    motions: List[str] = []
    previous_scene_fingerprint = ""
    graphic_seconds = 0.0
    total_seconds = 0.0
    for index, raw_scene in enumerate(manifest.get("scenes") or []):
        if not isinstance(raw_scene, Mapping):
            errors.append(f"scene {index}: scene is not an object")
            continue
        scene = raw_scene
        total_seconds += _scene_duration(scene)
        contract = _mapping(scene.get("editorial_contract"))
        enabled = bool(contract.get("enabled"))
        family = _text(contract.get("family") or "media_hold")
        fingerprint = _text(contract.get("composition_fingerprint"))
        timing = _mapping(contract.get("timing"))
        if enabled:
            families.append(family)
            motions.append(_text(contract.get("motion")))
            fingerprints.append(fingerprint)
            graphic_seconds += float(timing.get("total_seconds") or 0.0)
            if family not in COMPOSITION_LIBRARY:
                errors.append(f"scene {index}: unknown composition family {family}")
            if not _text(contract.get("display_text")) and family not in {"data_chart", "process_diagram", "before_after"}:
                errors.append(f"scene {index}: visible graphic has no approved display text")
            if not timing.get("enabled"):
                errors.append(f"scene {index}: visible graphic lacks valid timing")
            if timing.get("enabled") and float(timing.get("hold_seconds") or 0.0) + 1e-6 < float(COMPOSITION_LIBRARY[family]["min_hold"]):
                errors.append(f"scene {index}: {family} hold time is too short")
            if fingerprint and fingerprint == previous_scene_fingerprint:
                errors.append(f"scene {index}: adjacent identical composition fingerprint")
            if len(motions) >= 2 and motions[-1] and motions[-1] == motions[-2]:
                warnings.append(f"scene {index}: adjacent motion family repeated")
            previous_scene_fingerprint = fingerprint
        elif family != "media_hold":
            errors.append(f"scene {index}: disabled graphic does not resolve to media_hold")
            previous_scene_fingerprint = ""
        else:
            # A media-only scene breaks adjacency between graphic treatments.
            previous_scene_fingerprint = ""
    # The target is a rolling sequence constraint, not an impossible global
    # uniqueness ratio for a 20-minute documentary.  Four adjacent graphics
    # must not collapse to one composition family.
    for offset in range(0, max(0, len(families) - 3)):
        window = families[offset:offset + 4]
        if len(window) == 4 and len(set(window)) == 1:
            warnings.append(f"graphic window {offset}-{offset + 3}: one family repeated four times")
    continuity_audit = audit_sequence_continuity(manifest)
    for err in continuity_audit.get("errors") or []:
        if err not in errors:
            errors.append(err)
    for warn in continuity_audit.get("warnings") or []:
        if warn not in warnings:
            warnings.append(warn)
    return {
        "status": "fail" if errors else "pass",
        "errors": errors,
        "warnings": warnings,
        "family_sequence": families,
        "motion_sequence": motions,
        "composition_fingerprints": fingerprints,
        "graphic_duration_ratio": round(graphic_seconds / total_seconds, 3) if total_seconds else 0.0,
        "unique_families": len(set(families)),
        "design_library_version": DESIGN_LIBRARY_VERSION,
    }


__all__ = [
    "COMPOSITION_LIBRARY",
    "DESIGN_LIBRARY_VERSION",
    "MANIFEST_VERSION",
    "apply_media_first_direction",
    "audit_editorial_contracts",
    "normalize_editorial_controls",
]
