"""Editing Director LLM Engine.

Orchestrates the entire programmatic documentary editing pipeline:
Script -> Semantic Analysis -> Visual DNA -> Scene Decisions -> Grammar Rules -> EditPlan JSON.
"""

from __future__ import annotations

import copy
import json
import os
import re
import time
import unicodedata
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .contract import (
    EditPlan,
    VisualDNA,
    SceneDecision,
    PrimaryVisual,
    SecondaryVisual,
    MotionParameters,
    AudioPlan,
    PacingBeat,
    VisualSceneType,
    UNIVERSAL_SCENE_TYPES,
    SCENE_TYPE_VARIANTS,
    ALLOWED_CAMERAS,
    ALLOWED_ENTRANCES,
    ALLOWED_TRANSITIONS,
    validate_edit_plan,
)
from .visual_dna import generate_visual_dna, compute_video_seed
from .script_analyzer import analyze_sentence_triggers, compute_beat_pacing, SPOKEN_MEASUREMENT_PATTERN
from ..highlight_selector import select_grounded_highlight
from ..geographic_regions import REGION_CENTERS, is_curated_region
from ..template_library import list_template_catalog, validate_template_recipe
from .scene_grammar import enforce_scene_grammar
from .similarity_detector import compute_edit_fingerprint, reroll_variations_if_similar, EditFingerprint
from .audio_sync import align_scene_to_words


_GEOGRAPHIC_CATALOG_CACHE = None
_ADMIN1_CATALOG_CACHE = None
_COUNTRY_CODE_BY_NAME_CACHE = None

_TEXT_PANEL_STYLES = {"hero_type", "stacked_type", "outline_type", "marker_type", "editorial_caption", "editorial_banner", "highlight_band", "minimal_rule", "impact_slab", "none"}
_MAP_RENDER_STYLES = {"globe_3d", "satellite_local", "vector_editorial"}
_GRAPHIC_THEMES = {"editorial_light", "cinematic_dark", "accent_focus"}


def _safe_map_render_style(value: Any, location_names: Sequence[str], props: Mapping[str, Any] | None = None) -> str:
    """Choose a supported map grammar without claiming unavailable imagery."""
    requested = str(value or "").strip().lower()
    if requested not in _MAP_RENDER_STYLES:
        requested = "globe_3d" if len(location_names) > 1 or any(country_code_for_place(name) or is_curated_region(name) for name in location_names) else "vector_editorial"
    props = props if isinstance(props, Mapping) else {}
    if requested == "satellite_local" and not (props.get("satellite_image") and props.get("satellite_bounds")):
        # The render hydrator can fetch an attributed satellite plate for a
        # compact route with real coordinates. A name alone is insufficient.
        route = props.get("route_points")
        points = route if isinstance(route, list) and len(route) >= 2 else [props.get("p1"), props.get("p2")]
        def coordinate(point: Any) -> tuple[float, float] | None:
            if isinstance(point, Mapping):
                values = (point.get("lon"), point.get("lat"))
            elif isinstance(point, (list, tuple)) and len(point) >= 2:
                values = (point[0], point[1])
            else:
                return None
            try:
                longitude, latitude = float(values[0]), float(values[1])
            except (TypeError, ValueError):
                return None
            return (longitude, latitude) if -180 <= longitude <= 180 and -70 <= latitude <= 70 else None
        coordinates = [coordinate(point) for point in points]
        if len(coordinates) < 2 or any(point is None for point in coordinates):
            requested = "globe_3d" if location_names else "vector_editorial"
        else:
            longitudes = [point[0] for point in coordinates if point]
            latitudes = [point[1] for point in coordinates if point]
            if max(longitudes) - min(longitudes) > 3.5 or max(latitudes) - min(latitudes) > 2.5:
                requested = "globe_3d"
    return requested


def _safe_text_treatment(value: Any, palette: Mapping[str, Any] | None, visible: bool) -> Dict[str, str]:
    """Allow scene variation within a coherent, legible film palette."""
    if not visible:
        return {"panel_style": "none"}
    supplied = value if isinstance(value, Mapping) else {}
    palette = palette if isinstance(palette, Mapping) else {}
    def valid_color(value: Any) -> str:
        candidate = str(value or "").strip().upper()
        return candidate if re.fullmatch(r"#[0-9A-F]{6}", candidate) else ""

    primary = valid_color(palette.get("primary")) or "#F8F7F2"
    contrast_ink = valid_color(palette.get("text_contrast")) or "#FFFFFF"
    surface = valid_color(palette.get("surface")) or valid_color(palette.get("background")) or "#0B1720"
    accent = valid_color(palette.get("accent")) or "#FFFFFF"
    background_choices = {surface}
    if valid_color(palette.get("background")):
        background_choices.add(valid_color(palette.get("background")))
    text_choices = {primary, contrast_ink, "#FFFFFF", "#F8F7F2", "#111827"}

    def color(key: str, fallback: str, choices: set[str]) -> str:
        proposed = str(supplied.get(key) or "").strip().upper()
        return proposed if proposed in choices else fallback
    panel_style = str(supplied.get("panel_style") or "editorial_caption").strip().lower()
    if panel_style not in _TEXT_PANEL_STYLES:
        panel_style = "editorial_caption"
    treatment = {
        "panel_style": panel_style,
        # Colors are role-constrained. A palette's accent2, border or glow is
        # not interchangeable with its primary word accent or panel surface.
        "text_color": color("text_color", primary, text_choices),
        "accent_color": color("accent_color", accent, {accent}),
        "panel_background": color("panel_background", surface, background_choices),
    }
    def luminance(hex_color: str) -> float:
        values = [int(hex_color[pos:pos + 2], 16) / 255.0 for pos in (1, 3, 5)]
        linear = [part / 12.92 if part <= 0.04045 else ((part + 0.055) / 1.055) ** 2.4 for part in values]
        return sum(weight * part for weight, part in zip((0.2126, 0.7152, 0.0722), linear))
    def contrast(a: str, b: str) -> float:
        bright, dark = sorted((luminance(a), luminance(b)), reverse=True)
        return (bright + 0.05) / (dark + 0.05)
    if panel_style != "none" and contrast(treatment["text_color"], treatment["panel_background"]) < 4.5:
        treatment["text_color"] = max(text_choices, key=lambda candidate: contrast(candidate, treatment["panel_background"]))
    if panel_style != "none" and contrast(treatment["accent_color"], treatment["panel_background"]) < 3.0:
        treatment["accent_color"] = treatment["text_color"]
    return treatment


def load_geographic_catalog():
    """Load the bundled Natural Earth country and populated-place names."""
    global _GEOGRAPHIC_CATALOG_CACHE
    if _GEOGRAPHIC_CATALOG_CACHE is not None:
        return _GEOGRAPHIC_CATALOG_CACHE
    country_codes = set()
    country_names = set()
    city_names = set()
    try:
        root = __import__("pathlib").Path(__file__).resolve().parents[3]
        countries_path = root / "src" / "remotion" / "maps" / "ne_110m_admin_0_countries.json"
        places_path = root / "src" / "remotion" / "maps" / "ne_110m_populated_places.json"
        admin1_path = root / "src" / "remotion" / "maps" / "ne_10m_admin_1_focus.json"
        countries_payload = json.loads(countries_path.read_text(encoding="utf-8"))
        places_payload = json.loads(places_path.read_text(encoding="utf-8"))
        for feature in countries_payload.get("features", []):
            props = feature.get("properties") or {}
            for key in ("ISO_A3_EH", "ISO_A3", "ADM0_A3"):
                code = str(props.get(key) or "").strip().upper()
                if code and code != "-99":
                    country_codes.add(code)
            for key in ("ADMIN", "NAME", "NAME_EN", "NAME_LONG"):
                name = str(props.get(key) or "").strip().casefold()
                if name:
                    country_names.add(name)
        for feature in places_payload.get("features", []):
            props = feature.get("properties") or {}
            for key in ("NAME", "NAMEASCII", "NAME_EN"):
                name = str(props.get(key) or "").strip().casefold()
                if name:
                    city_names.add(name)
        admin1_payload = json.loads(admin1_path.read_text(encoding="utf-8"))
        for feature in admin1_payload.get("features", []):
            props = feature.get("properties") or {}
            name = str(props.get("name") or "").strip().casefold()
            if name:
                city_names.add(name)
        curated_cities = {
            "cape canaveral",
            "osaka", "southampton", "new york", "new york city", "belfast",
            "cherbourg", "queenstown", "cobh", "halifax", "boston",
            "london", "paris", "berlin", "rome", "madrid", "washington",
        }
        city_names.update(curated_cities)
        country_names.update({"england", "scotland", "wales", "great britain", "uk", "britain", "united states", "usa", "us"})
    except Exception:
        # Without the bundled geographic source, do not draw a guess.
        pass
    _GEOGRAPHIC_CATALOG_CACHE = (country_codes, country_names, city_names)
    return _GEOGRAPHIC_CATALOG_CACHE


def load_admin1_catalog():
    """Return bundled high-resolution region names and stable feature IDs."""
    global _ADMIN1_CATALOG_CACHE
    if _ADMIN1_CATALOG_CACHE is not None:
        return _ADMIN1_CATALOG_CACHE
    catalog = {}
    try:
        root = __import__("pathlib").Path(__file__).resolve().parents[3]
        path = root / "src" / "remotion" / "maps" / "ne_10m_admin_1_focus.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        for feature in payload.get("features", []):
            props = feature.get("properties") or {}
            name = str(props.get("name") or "").strip()
            feature_id = str(props.get("adm1_code") or props.get("iso_3166_2") or "").strip().upper()
            if name and feature_id:
                catalog[name.casefold()] = feature_id
    except Exception:
        pass
    _ADMIN1_CATALOG_CACHE = catalog
    return catalog


DIRECTOR_SYSTEM_PROMPT = """You are the Lead Documentary Video Editing Director.
Your job is NOT to write code or generate pixels.
Your job is to make scene-by-scene EDITORIAL and MOTION DESIGN decisions for a high-end documentary.
Use an original Vox-style explainer grammar: clear visual arguments, animated
maps and evidence, graphic reveals, deliberate rhythm, and one memorable visual
idea per beat. Maintain story context across chunks and select every visible
highlight from the exact narration; do not let deterministic keyword heuristics
replace an approved LLM choice.

Choose only from the closed vocabulary of 15 Universal Scene Systems. The exact
component-to-variant catalog and approved Remotion template catalog are supplied
with each scene batch from the live renderer contract. Treat those runtime lists
as authoritative; never invent, rename, or infer a variant from memory.

Allowed Cameras: static, slow_push, pan_left, pan_right, pan_up, pan_down, pull_out, focus_zoom, drift, cinematic_parallax
Allowed Transitions: hard_cut, cross_dissolve, directional_slide_left, directional_slide_right, wipe_left, wipe_right, film_burn, paper_tear, light_leak, zoom_cut

Return strictly a valid JSON object matching the requested schema. Do NOT include markdown code fences or conversational prose.
"""


WORLD_COUNTRIES_INFO: Dict[str, Dict[str, str]] = {
    "USA": {"name": "UNITED STATES", "capital": "WASHINGTON D.C.", "coords": "38.9072° N, 77.0369° W", "gdp": "$27.4T", "share": "24.1%"},
    "GBR": {"name": "UNITED KINGDOM", "capital": "LONDON", "coords": "51.5074° N, 0.1278° W", "gdp": "$3.3T", "share": "3.1%"},
    "DEU": {"name": "GERMANY", "capital": "BERLIN", "coords": "52.5200° N, 13.4050° E", "gdp": "$4.4T", "share": "4.2%"},
    "FRA": {"name": "FRANCE", "capital": "PARIS", "coords": "48.8566° N, 2.3522° E", "gdp": "$3.0T", "share": "2.9%"},
    "JPN": {"name": "JAPAN", "capital": "TOKYO", "coords": "35.6762° N, 139.6503° E", "gdp": "$4.2T", "share": "4.1%"},
    "CHN": {"name": "CHINA", "capital": "BEIJING", "coords": "39.9042° N, 116.4074° E", "gdp": "$17.8T", "share": "18.3%"},
    "IND": {"name": "INDIA", "capital": "NEW DELHI", "coords": "28.6139° N, 77.2090° E", "gdp": "$3.7T", "share": "3.5%"},
    "RUS": {"name": "RUSSIA", "capital": "MOSCOW", "coords": "55.7558° N, 37.6173° E", "gdp": "$2.0T", "share": "1.9%"},
    "BRA": {"name": "BRAZIL", "capital": "BRASILIA", "coords": "15.8267° S, 47.9218° W", "gdp": "$2.2T", "share": "2.1%"},
    "AUS": {"name": "AUSTRALIA", "capital": "CANBERRA", "coords": "35.2809° S, 149.1300° E", "gdp": "$1.7T", "share": "1.7%"},
    "CAN": {"name": "CANADA", "capital": "OTTAWA", "coords": "45.4215° N, 75.6972° W", "gdp": "$2.1T", "share": "2.0%"},
    "SAU": {"name": "SAUDI ARABIA", "capital": "RIYADH", "coords": "24.7136° N, 46.6753° E", "gdp": "$1.1T", "share": "1.1%"},
    "KOR": {"name": "SOUTH KOREA", "capital": "SEOUL", "coords": "37.5665° N, 126.9780° E", "gdp": "$1.7T", "share": "1.6%"},
    "ITA": {"name": "ITALY", "capital": "ROME", "coords": "41.9028° N, 12.4964° E", "gdp": "$2.2T", "share": "2.0%"},
    "BEL": {"name": "BELGIUM", "capital": "BRUSSELS", "coords": "50.8503° N, 4.3517° E", "gdp": "$630B", "share": "0.6%"},
    "NLD": {"name": "NETHERLANDS", "capital": "AMSTERDAM", "coords": "52.3676° N, 4.9041° E", "gdp": "$1.1T", "share": "1.0%"},
    "CHE": {"name": "SWITZERLAND", "capital": "BERN", "coords": "46.9480° N, 7.4474° E", "gdp": "$900B", "share": "0.8%"},
    "AUT": {"name": "AUSTRIA", "capital": "VIENNA", "coords": "48.2082° N, 16.3738° E", "gdp": "$520B", "share": "0.5%"},
    "ESP": {"name": "SPAIN", "capital": "MADRID", "coords": "40.4168° N, 3.7038° W", "gdp": "$1.6T", "share": "1.5%"},
}


def _extract_numbers_from_text(raw_text: str) -> list:
    """Extract meaningful numeric data points from narration text for chart visualization."""
    import re as _re
    results = []
    chronology_spans = [m.span() for m in _re.finditer(
        r"\b\d{1,2}:\d{2}\s*(?:AM|PM)?\b|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:,\s*\d{4})?\b",
        raw_text, _re.IGNORECASE,
    )]

    def is_chronology(start: int, end: int) -> bool:
        return any(start < stop and end > begin for begin, stop in chronology_spans)

    # Spoken compound figures are common in narration, while the numeric
    # renderers need a numeric target. Convert only explicit magnitudes (for
    # example, "twenty five billion dollars") so ordinary phrases such as
    # "one small step" are never mistaken for data.
    number_words = {
        "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
        "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
        "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
        "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
        "ninety": 90,
    }
    spoken_scale_pattern = _re.compile(r"\b(?P<scale>hundred|thousand|million|billion|trillion)\b", _re.IGNORECASE)

    def parse_number_words(value: str) -> int | None:
        total = 0
        current = 0
        for token in _re.split(r"[\s-]+", str(value or "").casefold()):
            if not token or token == "and":
                continue
            if token in number_words:
                current += number_words[token]
            elif token == "hundred":
                current = max(1, current) * 100
            elif token in {"thousand", "million", "billion", "trillion"}:
                scale_value = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}[token]
                total += max(1, current) * int(scale_value)
                current = 0
            else:
                return None
        return total + current if total or current else None

    spoken_scale_values = {
        "hundred": 1e2, "thousand": 1e3, "million": 1e6,
        "billion": 1e9, "trillion": 1e12,
    }
    for match in spoken_scale_pattern.finditer(raw_text):
        if is_chronology(match.start(), match.end()):
            continue
        # "one hundred twenty thousand" is one amount, not an earlier 100
        # followed by 120,000. Consume the outer phrase at its final scale.
        continuation = []
        tail = raw_text[match.end():match.end()+72]
        previous_end = 0
        for token in _re.finditer(r"[A-Za-z]+", tail):
            if not _re.fullmatch(r"[\s-]*", tail[previous_end:token.start()]):
                break
            previous_end = token.end()
            key = token.group(0).casefold()
            if key not in number_words and key not in {'and', 'hundred', 'thousand', 'million', 'billion', 'trillion'}:
                break
            continuation.append(key)
        if any(token in {'hundred', 'thousand', 'million', 'billion', 'trillion'} for token in continuation):
            continue
        lookback_start = max(0, match.start() - 72)
        lookback = raw_text[lookback_start:match.start()]
        word_matches = list(_re.finditer(r"[A-Za-z]+", lookback))
        number_tokens = {**number_words, "hundred": 100, "and": 0, "thousand": 1000, "million": 1000000, "billion": 1000000000, "trillion": 1000000000000}
        collected = []
        for token_match in reversed(word_matches):
            token = token_match.group(0).casefold()
            if token not in number_tokens:
                break
            collected.append(token)
        number_phrase = " ".join(reversed(collected))
        mantissa = parse_number_words(number_phrase)
        if mantissa is None:
            continue
        spoken_scale = match.group("scale").casefold()
        full_number = parse_number_words(number_phrase + ' ' + spoken_scale)
        if full_number is None:
            continue
        # Normalize the spoken amount to the largest readable unit. This keeps
        # "one million five hundred thousand" as 1.5 MILLION rather than the
        # less readable 1500 THOUSAND, and renders one hundred simply as 100.
        scale = next((unit for unit in ('trillion','billion','million','thousand')
                      if full_number >= spoken_scale_values[unit]), '')
        divisor = spoken_scale_values[scale] if scale else 1
        mantissa = full_number / divisor
        mantissa = int(mantissa) if float(mantissa).is_integer() else mantissa
        phrase_start = match.start() - len(number_phrase)
        if collected:
            phrase_start = lookback_start + word_matches[-len(collected)].start()
        currency_prefix = _re.search(r"(\$|£|€)\s*$", raw_text[:phrase_start])
        following = raw_text[match.end():match.end() + 32]
        unit_match = _re.match(r"\s+(?:US\s+)?(dollars?|pounds?|euros?)\b", following, _re.IGNORECASE)
        unit_text = str(unit_match.group(1) if unit_match else "").casefold()
        symbol = (currency_prefix.group(1) if currency_prefix else "") or (
            "$" if "dollar" in unit_text else "£" if "pound" in unit_text
            else "€" if "euro" in unit_text else ""
        )
        raw_phrase_end = match.end() + (unit_match.end() if unit_match else 0)
        source_phrase = raw_text[phrase_start:raw_phrase_end].strip()
        display = f"{symbol}{mantissa} {scale.upper()}".strip()
        results.append({
            "raw": float(full_number),
            "display": display,
            "unit": scale,
            "full_match": source_phrase.upper(),
            "prefix": symbol,
            "_compound": True,
            "spoken_source": source_phrase,
        })

    # Counted units are useful graphic anchors even without a magnitude word.
    # Requiring a unit keeps ordinary prose ("one small step") out of charts.
    for match in SPOKEN_MEASUREMENT_PATTERN.finditer(raw_text):
        if is_chronology(match.start(), match.end()):
            continue
        phrase = match.group("number")
        has_scale = bool(_re.search(r"\b(?:hundred|thousand|million|billion|trillion)\b", phrase, _re.I))
        if has_scale and _re.fullmatch(r"dollars?|pounds?|euros?", match.group("unit"), _re.I):
            continue  # Preserve the normalized currency mantissa and suffix.
        value = parse_number_words(phrase)
        if value is None and not _re.fullmatch(r"zero", phrase, _re.I):
            continue
        unit = match.group("unit").casefold()
        if has_scale:
            # The magnitude scanner may have stopped at an inner "hundred".
            # A measured phrase owns that whole count, including its unit.
            phrase_folded = phrase.casefold().replace('-', ' ')
            results = [r for r in results if not (
                r.get('spoken_source') and str(r['spoken_source']).casefold().replace('-', ' ') in phrase_folded)]
        prefix = "$" if unit.startswith("dollar") else "£" if unit.startswith("pound") else "€" if unit.startswith("euro") else ""
        unit = "" if prefix else "%" if unit == "percent" else unit
        value = value or 0
        results.append({"raw": float(value), "display": f"{prefix}{value}",
                        "unit": unit, "prefix": prefix,
                        "full_match": match.group(0).upper(),
                        "spoken_source": match.group(0)})

    # Compound numbers: merge "100 million", "$2.5 billion", etc. into single data points
    for cm in _re.finditer(
        r'(?:\$|£|€)?(\d[\d,]*(?:\.\d+)?)\s+(million|billion|trillion|thousand|hundred)s?\b\s*([a-z]+(?:\s+[a-z]+)?)?',
        raw_text, _re.IGNORECASE
    ):
        if is_chronology(cm.start(), cm.end()):
            continue
        num_str = cm.group(1).replace(',', '')
        scale = cm.group(2).lower()
        noun = (cm.group(3) or '').strip().upper()
        if noun.lower() in {'and', 'or', 'in', 'of', 'to', 'for', 'with', 'by', 'at', 'the', 'a', 'an', 'leaving', 'today'}:
            noun = ''
        try:
            val = float(num_str)
            scale_map = {'thousand': 1e3, 'hundred': 1e2, 'million': 1e6, 'billion': 1e9, 'trillion': 1e12}
            val *= scale_map.get(scale, 1)
            has_dollar = '$' in cm.group(0) or '£' in cm.group(0) or '€' in cm.group(0)
            display = f"${num_str} {scale.upper()}" if has_dollar else f"{num_str} {scale.upper()}"
            label = f"{display} {noun}".strip() if noun else cm.group(0).strip().upper()
            results.append({'raw': val, 'display': display.upper(), 'unit': scale, 'full_match': label, '_compound': True})
        except (ValueError, TypeError):
            continue

    # First match "zero alarms", "zero breaches", etc.
    for m in _re.finditer(r'\bzero\s+([a-z]+)\b', raw_text, _re.IGNORECASE):
        if any(r.get('spoken_source', '').casefold() == m.group(0).casefold() for r in results):
            continue
        noun = m.group(1).strip().upper()
        if noun.lower() not in {"point", "percent", "tolerance"}:
            results.append({"raw": 0.0, "display": "0", "unit": noun, "full_match": f"0 {noun}"})

    # Match numeric patterns
    for m in _re.finditer(
        r'(?:\$|£|€)?(\d[\d,]*(?:\.\d+)?)\s*(%|billion|million|trillion|thousand|k|b|m|meters?|feet|miles|kilometers?|km|tons?|tonnes?|years?|centuries|decades?|spectators?|gladiators?|soldiers?|people|inhabitants|citizens|workers?|slaves?|men|women|ships?|gallons?|acres?|hectares?|floors?|levels?|stories|seats?)?\s*([a-z]+(?:\s+[a-z]+)?)?',
        raw_text, _re.IGNORECASE
    ):
        if is_chronology(m.start(), m.end()):
            continue
        num_str = m.group(1).replace(',', '')
        # Skip if this number was already captured as part of a compound number
        if any(r.get('_compound') and num_str in r.get('display', '') for r in results):
            continue
        unit = (m.group(2) or '').strip().lower()
        noun = (m.group(3) or '').strip().upper()
        if noun.lower() in {"and", "or", "in", "of", "to", "for", "with", "by", "at", "the", "a", "an", "leaving", "today"}:
            noun = ""
        try:
            val = float(num_str)
            if val == 0 and not unit and not noun:
                continue
            # Years and AD/BC dates belong to chronology, not quantitative charts.
            nearby = f"{raw_text[max(0, m.start() - 8):m.start()]} {raw_text[m.end():m.end() + 8]}"
            if (
                (not unit and not re.search(r"[$£€]", m.group(0)) and re.fullmatch(r"(?:18|19|20)\d{2}", num_str))
                or re.search(r"\b(?:AD|A\.D\.|BC|B\.C\.|BCE|CE|C\.E\.)\b", nearby, re.I)
            ):
                continue
            # Format display value
            if unit in ('%',):
                display = f"{num_str}%"
            elif unit in ('billion', 'b'):
                display = f"${num_str}B" if '$' in m.group(0) else f"{num_str}B"
            elif unit in ('million', 'm'):
                display = f"${num_str}M" if '$' in m.group(0) else f"{num_str}M"
            elif unit in ('trillion',):
                display = f"${num_str}T" if '$' in m.group(0) else f"{num_str}T"
            elif unit in ('thousand', 'k'):
                display = f"{num_str}K"
            elif re.search(r"[$£€]", m.group(0)):
                display = re.search(r"[$£€]", m.group(0)).group(0) + num_str
            elif unit:
                display = f"{num_str} {unit}"
            else:
                display = num_str

            label = f"{display} {noun}".strip() if noun else m.group(0).strip()
            results.append({"raw": val, "display": display.upper(), "unit": unit, "full_match": label.upper()})
        except (ValueError, TypeError):
            continue
    return results


def _is_single_financial_stat(raw_text: str) -> bool:
    """Whether one explicit narrated financial amount deserves a stat beat."""
    text = str(raw_text or "")
    if not re.search(r"\b(?:cost|spent|spending|budget|price|worth|valued|value|revenue|investment|loss)\b", text, re.I):
        return False
    if not re.search(r"\b(?:dollars?|euros?|pounds?)\b|[$£€]", text, re.I):
        return False
    return len(_extract_numbers_from_text(text)) == 1


def _extract_chart_title(raw_text: str) -> str:
    """Generate a contextual chart title from narration text — never use 'DATA ANALYSIS'."""
    import re as _re
    raw_lower = raw_text.lower()
    # Extract the key topic phrases
    topic_patterns = [
        (r'\b(population|spectators?|crowd|audience|attendance|visitors?|tourists?)\b', 'POPULATION & ATTENDANCE'),
        (r'\b(casualties?|deaths?|killed|wounded|victims?|survivors?|fatalities)\b', 'HUMAN TOLL'),
        (r'\b(height|length|width|depth|diameter|dimensions?|size|scale|measurement)\b', 'STRUCTURAL DIMENSIONS'),
        (r'\b(cost|budget|revenue|price|value|worth|expenditure|investment|spending)\b', 'FINANCIAL OVERVIEW'),
        (r'\b(construction|built|erected|building|architecture|engineering|structure)\b', 'CONSTRUCTION METRICS'),
        (r'\b(battle|war|siege|campaign|military|combat|assault|invasion)\b', 'MILITARY ANALYSIS'),
        (r'\b(trade|commerce|export|import|shipping|cargo|merchant)\b', 'TRADE & COMMERCE'),
        (r'\b(production|manufacturing|output|yield|harvest|capacity)\b', 'PRODUCTION OUTPUT'),
        (r'\b(growth|expansion|rise|increase|surge|boom)\b', 'GROWTH TRAJECTORY'),
        (r'\b(decline|fall|collapse|decrease|reduction|shrinkage)\b', 'DECLINE ANALYSIS'),
        (r'\b(territory|land|area|region|province|empire|kingdom|domain)\b', 'TERRITORIAL SCOPE'),
        (r'\b(time|duration|period|era|century|decade|years?)\b', 'HISTORICAL TIMELINE'),
    ]
    for pattern, title in topic_patterns:
        if _re.search(pattern, raw_lower):
            return title

    # Fallback: use first meaningful words from narration
    words = [w for w in raw_text.split()[:5] if len(w) > 2]
    if words:
        return ' '.join(words[:3]).upper()
    return 'KEY METRICS'


def _build_chart_props(
    variant: str,
    raw_text: str,
    numbers: list,
    card_theme: str,
    used_chart_variants: list,
    context_text: str = "",
) -> dict:
    """Build chart props with REAL data extracted from narration text — no fake data."""
    raw_lower = raw_text.lower()
    pv_props = {
        "background_blur": True,
        "card_theme": card_theme,
    }

    # Vary the layout only within the same data grammar. Switching a repeated
    # bar to a donut or time series would claim a relationship not in the story.
    resolved_variant = variant
    if variant == "distribution_histogram":
        # Histograms need real bins and frequencies, not arbitrary category
        # values dressed as a distribution. Accept an explicit narrated table.
        bins = list(re.finditer(r"(?P<low>\d+(?:\.\d+)?)\s*[-–]\s*(?P<high>\d+(?:\.\d+)?)\s*(?P<unit>years?|seconds?|minutes?|hours?|km|miles?|meters?)?\s*[:=]\s*(?P<count>\d+)\b", raw_text, re.I))
        if len(bins) < 2 or len(bins) > 6:
            return {"variant": variant, "no_data": True, "reason": "Histogram requires explicit grounded bins and frequencies"}
        bounds = [(float(item['low']), float(item['high'])) for item in bins]
        units = {str(item['unit'] or '').lower() for item in bins}
        if len(units) > 1 or any(low >= high for low, high in bounds) or any(bounds[i][0] < bounds[i-1][1] for i in range(1, len(bounds))) or len({high-low for low, high in bounds}) != 1:
            return {"variant": variant, "no_data": True, "reason": "Histogram bins must be ordered, equally wide and non-overlapping"}
        return {"variant": variant, "data": [{"label": f"{item['low']}–{item['high']} {item['unit'] or ''}".strip(), "value": int(item['count'])} for item in bins]}
    if used_chart_variants and resolved_variant == used_chart_variants[-1]:
        if variant in {"ranked_horizontal_bars", "editorial_bar"}:
            resolved_variant = "editorial_bar" if variant == "ranked_horizontal_bars" else "ranked_horizontal_bars"
    used_chart_variants.append(resolved_variant)
    pv_props["variant"] = resolved_variant

    # Extract real data from narration. If scene was split across commas,
    # extract comparative data numbers from the full sentence context.
    extracted = _extract_numbers_from_text(raw_text)
    if len(extracted) < 2 and context_text:
        ctx_extracted = _extract_numbers_from_text(context_text)
        if len(ctx_extracted) >= 2:
            extracted = ctx_extracted

    unit_set = {str(item.get("unit") or "").strip().lower() for item in extracted if item.get("unit")}
    relation_is_explicit = bool(re.search(
        r"\b(?:from\b.+\bto|between\b.+\band\b|compared (?:with|to)|versus|\bvs\.?\b|before\b.+\bafter)\b",
        context_text or raw_text,
        re.I | re.S,
    ))
    comparable = len(extracted) >= 2 and len(unit_set) <= 1 and (bool(unit_set) or relation_is_explicit)

    source_text = context_text or raw_text
    source_lower = source_text.lower()
    if resolved_variant == "donut_distribution":
        is_partition = bool(re.search(r"\b(?:share|shares|split|breakdown|distribution|of the total)\b", source_lower))
        percentages = len(extracted) >= 2 and all(ex.get("unit") == "%" for ex in extracted)
        totals_hundred = percentages and 98 <= sum(ex["raw"] for ex in extracted[:5]) <= 102
        if not (is_partition and totals_hundred):
            resolved_variant = "ranked_horizontal_bars"
            pv_props["variant"] = resolved_variant
    if resolved_variant in {"multi_line_trend", "area_growth_fill", "trend_line_with_markers"}:
        is_trend = bool(re.search(r"\b(?:over time|year by year|month by month|rose|fell|grew|growth|declined|trend|from .+ to)\b", source_lower))
        if not is_trend:
            resolved_variant = "editorial_bar"
            pv_props["variant"] = resolved_variant
    if resolved_variant == "before_after_delta" and not re.search(r"\b(?:before|after|from .+ to)\b", source_lower):
        resolved_variant = "editorial_bar"
        pv_props["variant"] = resolved_variant

    def point_label(ex: dict, index: int) -> str:
        label = str(ex.get("full_match") or "").upper()
        value = str(ex.get("display") or "").upper()
        if value and label.startswith(value):
            label = label[len(value):].strip(" :,-")
        if label and not re.fullmatch(r"[\d\s.,%$£€+-]+", label):
            return label[:28]
        if len(extracted) == 2 and re.search(r"\bfrom\b.+\bto\b", source_lower):
            return ("FROM", "TO")[index]
        if len(extracted) == 2 and "before" in source_lower and "after" in source_lower:
            return ("BEFORE", "AFTER")[index]
        return ""

    chart_title = _extract_chart_title(context_text if len(extracted) >= 2 and context_text else raw_text)
    emphasis = extracted[0]["display"] if extracted else ""

    def no_data():
        return {
            "variant": resolved_variant,
            "title": chart_title,
            "no_data": True,
            "reason": "No grounded multi-value relationship was narrated",
            "background_blur": True,
            "card_theme": card_theme,
        }

    if not comparable and resolved_variant not in {"radial_gauge"}:
        return no_data()

    if resolved_variant == "ranked_horizontal_bars":
        pv_props["title"] = chart_title
        pv_props["emphasis"] = emphasis
        if len(extracted) >= 2:
            pv_props["data"] = [
                {"label": point_label(ex, i), "value": ex["display"]}
                for i, ex in enumerate(extracted[:5])
            ]
        else:
            return no_data()

    elif resolved_variant == "donut_distribution":
        pv_props["title"] = chart_title
        pv_props["emphasis"] = emphasis
        if extracted and '%' in str(extracted[0].get("unit", "")):
            pv_props["centerMetric"] = extracted[0]["display"]
            pv_props["centerLabel"] = "PRIMARY SHARE"
            pv_props["data"] = [
                {"label": point_label(ex, i), "value": ex["raw"]}
                for i, ex in enumerate(extracted[:4])
            ]
        else:
            if len(extracted) < 2:
                return no_data()
            pv_props["centerMetric"] = extracted[0]["display"]
            pv_props["centerLabel"] = "CORE METRIC"
            pv_props["data"] = [
                {"label": point_label(ex, i), "value": ex["raw"]}
                for i, ex in enumerate(extracted[:4])
            ]

    elif resolved_variant == "multi_line_trend":
        pv_props["title"] = chart_title
        pv_props["emphasis"] = emphasis
        if len(extracted) >= 4:
            half = len(extracted) // 2
            pv_props["seriesA"] = {
                "name": "PRIMARY TREND",
                "points": [{"label": point_label(ex, i), "value": ex["raw"]} for i, ex in enumerate(extracted[:half])]
            }
            pv_props["seriesB"] = {
                "name": "SECONDARY TREND",
                "points": [{"label": point_label(ex, i + half), "value": ex["raw"]} for i, ex in enumerate(extracted[half:])]
            }
        else:
            return no_data()

    elif resolved_variant == "area_growth_fill":
        pv_props["title"] = chart_title
        pv_props["emphasis"] = emphasis
        pv_props["peakLabel"] = f"PEAK: {extracted[-1]['display']}" if extracted else "PEAK"
        pv_props["data"] = [
            {"label": point_label(ex, i), "value": ex["raw"]}
            for i, ex in enumerate(extracted[:5])
        ] if extracted else no_data()

    elif resolved_variant == "radial_gauge":
        val = None
        for ex in extracted:
            if ex.get("unit") == '%':
                try:
                    val = int(ex["raw"])
                except (ValueError, TypeError):
                    pass
                break
        if val is None:
            return no_data()
        pv_props["title"] = chart_title
        pv_props["emphasis"] = f"{val}% CAPACITY"
        pv_props["currentValue"] = val
        pv_props["maxValue"] = 100
        pv_props["unit"] = "%"
        pv_props["label"] = raw_text[:35].upper().rstrip('., ')

    elif resolved_variant == "before_after_delta":
        pv_props["title"] = chart_title
        if len(extracted) >= 2:
            before_val = extracted[0]["display"]
            after_val = extracted[-1]["display"]
            try:
                delta = ((extracted[-1]["raw"] - extracted[0]["raw"]) / max(extracted[0]["raw"], 1)) * 100
                pv_props["emphasis"] = f"+{delta:.0f}% CHANGE" if delta > 0 else f"{delta:.0f}% CHANGE"
                pv_props["deltaLabel"] = pv_props["emphasis"]
            except (ValueError, TypeError, ZeroDivisionError):
                pv_props["emphasis"] = "SIGNIFICANT CHANGE"
                pv_props["deltaLabel"] = "DELTA"
        else:
            return no_data()
        pv_props["context"] = raw_text[:50].upper().rstrip('., ')
        pv_props["before"] = {"value": before_val, "label": "NARRATED VALUE A"}
        pv_props["after"] = {"value": after_val, "label": "NARRATED VALUE B"}

    elif resolved_variant == "trend_line_with_markers":
        pv_props["title"] = chart_title
        pv_props["emphasis"] = emphasis
        pv_props["data"] = [
            {"label": point_label(ex, i), "value": ex["raw"]}
            for i, ex in enumerate(extracted[:6])
        ] if extracted else no_data()

    else:  # editorial_bar
        pv_props["title"] = chart_title
        pv_props["emphasis"] = emphasis
        pv_props["data"] = [
            {"label": point_label(ex, i), "value": ex["raw"]}
            for i, ex in enumerate(extracted[:5])
        ] if extracted else no_data()

    return pv_props


def _build_map_props(
    variant: str,
    triggers: Any,
    raw_text: str,
) -> Dict[str, Any]:
    pv_props: Dict[str, Any] = {"variant": variant}
    raw_lower = raw_text.lower()

    country_codes, country_names, city_names = load_geographic_catalog()

    locs = list(dict.fromkeys(triggers.locations or []))
    city_locations = [str(place).strip() for place in locs if str(place).strip().casefold() in city_names]

    # Draw an aircraft arc only when the narration identifies air travel.
    # Other explicit journeys use a restrained transit trace with no invented
    # vehicle or implied mode of transport.
    route_variant = "route_flight_arc" if re.search(
        r"\b(?:flight|flew|flying|aircraft|airplane|plane|airlift|by air|air route)\b",
        raw_text,
        re.I,
    ) else "city_to_city_transit"

    # Named cities are more precise than the modern country alias that may
    # also be inferred from a capital. Resolve city-to-city narration first so
    # a Berlin scene does not become a Germany/Russia country-network graphic.
    found_route = False
    if len(city_locations) >= 2:
        for i in range(len(city_locations)):
            for j in range(len(city_locations)):
                if i != j and _has_explicit_map_connection(raw_text, city_locations[i], city_locations[j]):
                    pv_props["variant"] = route_variant
                    pv_props["from"] = city_locations[i].upper()
                    pv_props["to"] = city_locations[j].upper()
                    pv_props["start"] = city_locations[i].upper()
                    pv_props["end"] = city_locations[j].upper()
                    pv_props["location"] = city_locations[i].upper()
                    distance = re.search(r"\b(\d[\d,]*(?:\.\d+)?)\s*(?:km|kilometers?|miles?)\b", raw_text, re.I)
                    if distance:
                        pv_props["distance"] = distance.group(0).upper()
                    found_route = True
                    break
            if found_route:
                break

    if found_route:
        pass
    elif city_locations:
        pv_props["variant"] = "country_spotlight"
        pv_props["location"] = city_locations[0].upper()
        pv_props["title"] = city_locations[0].upper()

    elif triggers.country_corridor:
        pv_props["variant"] = route_variant
        corridor = triggers.country_corridor
        orig_code = corridor.get("from")
        dest_code = corridor.get("to")
        if not country_codes or orig_code not in country_codes or dest_code not in country_codes:
            return {"variant": variant, "no_data": True, "reason": "Map route endpoints are not in the local verified geography catalog"}
        c_orig = WORLD_COUNTRIES_INFO.get(orig_code)
        c_dest = WORLD_COUNTRIES_INFO.get(dest_code)
        pv_props["from_country"] = orig_code
        pv_props["to_country"] = dest_code
        pv_props["from"] = c_orig["name"] if c_orig else str(orig_code).upper()
        pv_props["to"] = c_dest["name"] if c_dest else str(dest_code).upper()
        pv_props["start"] = pv_props["from"]
        pv_props["end"] = pv_props["to"]
        pv_props["country"] = pv_props["from"]
        pv_props["location"] = pv_props["from"]
        distance = re.search(r"\b(\d[\d,]*(?:\.\d+)?)\s*(?:km|kilometers?|miles?)\b", raw_text, re.I)
        if distance:
            pv_props["distance"] = distance.group(0).upper()

    elif len(triggers.countries) >= 2:
        pv_props["variant"] = "global_network"
        codes = triggers.countries
        if not country_codes or any(code not in country_codes for code in codes):
            return {"variant": variant, "no_data": True, "reason": "Map locations are not in the local verified geography catalog"}
        pv_props["countries"] = codes

    elif len(triggers.countries) == 1:
        pv_props["variant"] = "country_spotlight"
        code = triggers.countries[0]
        if not country_codes or code not in country_codes:
            return {"variant": variant, "no_data": True, "reason": "Country is not in the local verified geography catalog"}
        c_info = WORLD_COUNTRIES_INFO.get(code)
        pv_props["country"] = code
        pv_props["country_code"] = code
        pv_props["location"] = c_info['name'] if c_info else code
        pv_props["title"] = c_info["name"] if c_info else str(code).upper()

    else:
        if len(locs) >= 2 and _has_explicit_map_connection(raw_text, locs[0], locs[1]):
            pv_props["variant"] = route_variant
            if not city_names or any(str(place).casefold() not in city_names and str(place).casefold() not in country_names for place in locs[:2]):
                return {"variant": variant, "no_data": True, "reason": "Map endpoints are not in the local verified geography catalog"}
            pv_props["from"] = locs[0].upper()
            pv_props["to"] = locs[1].upper()
            pv_props["start"] = locs[0].upper()
            pv_props["end"] = locs[1].upper()
            pv_props["location"] = locs[0].upper()
            distance = re.search(r"\b(\d[\d,]*(?:\.\d+)?)\s*(?:km|kilometers?|miles?)\b", raw_text, re.I)
            if distance:
                pv_props["distance"] = distance.group(0).upper()
        elif locs:
            if not city_names and not country_names:
                return {"variant": variant, "no_data": True, "reason": "Location is not in the local verified geography catalog"}
            if str(locs[0]).casefold() not in city_names and str(locs[0]).casefold() not in country_names:
                return {"variant": variant, "no_data": True, "reason": "Location is not in the local verified geography catalog"}
            # Two nearby place names do not prove a journey. Use a local
            # spotlight unless the narration explicitly connects the endpoints.
            pv_props["variant"] = "country_spotlight"
            pv_props["location"] = locs[0].upper()
            pv_props["title"] = locs[0].upper()
        else:
            return {"variant": variant, "no_data": True, "reason": "A map requires two narrated locations or a country entity"}

    return pv_props


def _build_timeline_props(
    variant: str,
    raw_text: str,
    dates: Sequence[str],
    triggers: Any,
) -> Dict[str, Any]:
    """Build timeline props with extracted dates and chronological milestones."""
    pv_props: Dict[str, Any] = {"variant": variant}
    raw_lower = raw_text.lower()

    primary_date = dates[0] if dates else ""
    if not primary_date:
        return {"variant": variant, "no_data": True, "reason": "No narrated date or era"}

    pv_props["year"] = primary_date
    pv_props["date"] = primary_date
    pv_props["title"] = ""
    events = []
    for date in dates[:4]:
        match = re.search(re.escape(str(date)), raw_text, re.I)
        label = ""
        if match:
            remainder = raw_text[match.end():]
            next_date_offsets = [found.start() for other in dates if str(other) != str(date)
                                 for found in [re.search(re.escape(str(other)), remainder, re.I)] if found]
            if next_date_offsets:
                remainder = remainder[:min(next_date_offsets)]
            clause = re.split(r"[.!?;]", remainder, maxsplit=1)[0].strip(" ,:–—-")
            clause = re.sub(r"^(?:and|then|when)\s+", "", clause, flags=re.I).strip()
            # Keep a complete exact phrase; never replace facts with OPENING,
            # OUTCOME or numbered PHASE labels, nor cut a long clause mid-word.
            if clause and len(clause.split()) <= 10 and not re.search(r"\b(?:and|or|to|from|in|the)$", clause, re.I):
                label = clause
        events.append({"date": str(date), "year": str(date), "label": label})
    pv_props["events"] = events

    if variant == "era_bracket_card" or len(dates) >= 2:
        if len(dates) >= 2:
            pv_props["eraSpan"] = f"{dates[0].upper()} — {dates[-1].upper()}"
        else:
            pv_props["eraSpan"] = primary_date

    month_match = re.search(r'\b(january|february|march|april|may|june|july|august|september|october|november|december)\b', raw_lower)
    day_match = re.search(r'\b(\d{1,2})(?:st|nd|rd|th)?\b', raw_text)
    if month_match:
        pv_props["month"] = month_match.group(1).upper()
    if day_match:
        pv_props["day"] = day_match.group(1)

    return pv_props


def _build_person_props(
    variant: str,
    raw_text: str,
    persons: Sequence[str],
    triggers: Any,
) -> Dict[str, Any]:
    """Build a name treatment from an explicitly narrated person."""
    pv_props: Dict[str, Any] = {"variant": variant}
    person_name = persons[0] if persons else ""
    if not person_name and getattr(triggers, 'historical_figures', None):
        person_name = triggers.historical_figures[0]
    if not person_name:
        m = re.search(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b', raw_text)
        person_name = m.group(1) if m else ""

    if not person_name:
        return {
            "variant": variant,
            "no_data": True,
            "reason": "No explicitly named person was narrated",
        }

    pv_props["name"] = person_name.upper()

    return pv_props


def _has_explicit_map_connection(text: str, origin: str, destination: str) -> bool:
    """Require a stated geographic relationship before drawing a route."""
    source = str(text or "")
    left = str(origin or "").strip()
    right = str(destination or "").strip()
    if not source or not left or not right or left.casefold() == right.casefold():
        return False

    def place_pattern(value: str) -> str:
        words = re.escape(value).replace(r"\ ", r"\s+")
        return rf"(?:the\s+)?{words}"

    candidates = ((left, right), (right, left))
    for first, second in candidates:
        a, b = place_pattern(first), place_pattern(second)
        patterns = (
            rf"\bfrom\s+{a}\b[^.!?;]{{0,100}}?\b(?:to|toward|towards|into)\s+{b}\b",
            rf"\bbetween\s+{a}\b[^.!?;]{{0,100}}?\band\s+{b}\b",
            rf"\b(?:connect(?:s|ed|ing)?|link(?:s|ed|ing)?|join(?:s|ed|ing)?)\b[^.!?;]{{0,70}}?\b{a}\b[^.!?;]{{0,50}}?\b(?:and|to|with)\b[^.!?;]{{0,35}}?\b{b}\b",
            rf"\b{a}\b[^.!?;]{{0,45}}?\band\s+{b}\b[^.!?;]{{0,45}}?\b(?:connect(?:ed|s)?|link(?:ed|s)?|join(?:ed|s)?)\b",
        )
        if any(re.search(pattern, source, re.I | re.S) for pattern in patterns):
            return True
    return False


def _repair_unverified_map_route(scene: SceneDecision) -> bool:
    """Remove route geometry when the scene cannot ground a place-to-place link."""
    if scene.primary_visual.type != VisualSceneType.ANIMATED_MAP.value:
        return False
    visual = scene.primary_visual
    props = dict(visual.props or {})
    map_shot = dict(scene.map_shot or props.get("map_shot") or {})
    origin = str(props.get("from") or props.get("start") or props.get("startCity") or "").strip()
    destination = str(props.get("to") or props.get("end") or props.get("endCity") or "").strip()
    has_pair = bool(origin and destination) or bool(props.get("p1") and props.get("p2"))
    route_supported = bool(map_shot.get("route_id")) or _has_explicit_map_connection(
        scene.narration or "", origin, destination
    )
    requested_variant = str(visual.variant or props.get("variant") or "").strip().lower()
    route_variant = requested_variant in {"route_flight_arc", "city_to_city_transit"}
    changed = False

    if has_pair and not route_supported:
        fallback_place = str(
            props.get("location") or props.get("place") or props.get("country")
            or props.get("target_country")
            or next(iter(map_shot.get("location_names") or []), "")
        ).strip()
        for key in (
            "from", "to", "start", "end", "startCity", "endCity",
            "from_country", "to_country", "p1", "p2",
            "from_coordinates", "to_coordinates",
        ):
            props.pop(key, None)
        changed = True
        if fallback_place:
            props["location"] = fallback_place
            props["variant"] = "country_spotlight"
            visual.variant = "country_spotlight"
        else:
            props["no_data"] = True
    elif route_variant and not route_supported:
        highlight_countries = props.get("highlight_countries") or props.get("countries") or []
        has_admin1 = any(props.get(key) for key in ("admin1", "state", "region")) or bool(map_shot.get("feature_ids"))
        if isinstance(highlight_countries, list) and len(highlight_countries) >= 2:
            safe_variant = "global_network"
        elif has_admin1:
            safe_variant = "territory_zoom_highlight"
        else:
            safe_variant = "country_spotlight"
        props["variant"] = safe_variant
        visual.variant = safe_variant
        changed = True

    if not route_supported and str(map_shot.get("motion_profile") or props.get("motion_profile") or "").lower() == "route_trace":
        map_shot["motion_profile"] = "auto"
        changed = True

    if changed:
        props["map_shot"] = map_shot
        visual.props = props
        scene.map_shot = map_shot
        scene.text = dict(scene.text or {})
        scene.text["map_route_validation"] = {
            "status": "repaired",
            "reason": "route_requires_grounded_endpoints_and_narrated_connection",
            "resolved_variant": "media_only" if props.get("no_data") else visual.variant,
        }
    return changed


def _map_props_are_renderable(props: Mapping[str, Any], map_shot: Mapping[str, Any]) -> bool:
    """Return whether the bundled Remotion map can resolve this grounded shot."""
    if not isinstance(props, Mapping) or props.get("no_data"):
        return False
    country_codes, country_names, city_names = load_geographic_catalog()
    admin1 = load_admin1_catalog()
    codes = {str(code).strip().upper() for code in country_codes}
    admin1_codes = {str(code).strip().upper() for code in admin1.values()}

    def resolves_country(value: Any) -> bool:
        text = str(value or "").strip()
        return bool(text and (_fold_place_name(text) in country_names or text.upper() in codes))

    def resolves_place(value: Any) -> bool:
        text = str(value or "").strip()
        return bool(text and (_fold_place_name(text) in country_names or _fold_place_name(text) in city_names))

    def resolves_admin1(value: Any) -> bool:
        text = str(value or "").strip()
        return bool(text and (_fold_place_name(text) in admin1 or text.upper() in admin1_codes))

    def valid_point(value: Any) -> bool:
        if not isinstance(value, Mapping):
            return False
        try:
            if "lon" in value or "lat" in value:
                lon, lat = float(value.get("lon")), float(value.get("lat"))
                return -180 <= lon <= 180 and -90 <= lat <= 90
            x, y = float(value.get("x")), float(value.get("y"))
            return 0 <= x <= 1200 and 0 <= y <= 700
        except (TypeError, ValueError):
            return False

    locations = map_shot.get("location_names") if isinstance(map_shot, Mapping) else []
    locations = locations if isinstance(locations, list) else []
    feature_ids = map_shot.get("feature_ids") if isinstance(map_shot, Mapping) else []
    if any(resolves_country(location) or resolves_place(location) or resolves_admin1(location) for location in locations):
        return True
    if any(is_curated_region(location) for location in locations):
        return True
    if isinstance(feature_ids, list) and any(str(feature_id or "").strip().upper() in admin1_codes for feature_id in feature_ids):
        return True
    if resolves_country(props.get("country")) or resolves_country(props.get("target_country")):
        return True
    if resolves_place(props.get("location")) or resolves_place(props.get("place")):
        return True
    if any(resolves_admin1(props.get(key)) for key in ("admin1", "state", "region")):
        return True
    countries = props.get("highlight_countries") or props.get("countries") or []
    countries = countries if isinstance(countries, list) else [countries]
    if len(countries) >= 2 and all(resolves_country(country) for country in countries):
        return True
    origin = props.get("from") or props.get("start") or props.get("startCity")
    destination = props.get("to") or props.get("end") or props.get("endCity")
    if resolves_place(origin) and resolves_place(destination):
        return True
    return valid_point(props.get("p1") or props.get("from_coordinates")) and valid_point(
        props.get("p2") or props.get("to_coordinates")
    )


def _scene_frame_mode(scene: SceneDecision) -> str:
    text = scene.text if isinstance(scene.text, Mapping) else {}
    explicit = str(text.get("frame_mode") or "").strip().lower()
    if explicit:
        return explicit
    inferred = {
        VisualSceneType.ANIMATED_MAP.value: "map",
        VisualSceneType.DIAGRAM.value: "diagram",
        VisualSceneType.CHART.value: "data",
        VisualSceneType.STAT_CARD.value: "data",
        VisualSceneType.COMPARISON.value: "data",
        VisualSceneType.PERSON_PROFILE.value: "portrait",
        VisualSceneType.DOCUMENT_HIGHLIGHT.value: "evidence",
        VisualSceneType.QUOTE_CARD.value: "quote",
        VisualSceneType.KINETIC_TEXT.value: "kinetic",
        VisualSceneType.CHAPTER_TITLE.value: "chapter",
    }
    return inferred.get(scene.primary_visual.type, "media_only")


def _scene_frame_contract_failure(scene: SceneDecision, fps: int = 30) -> str:
    """Check that the selected frame grammar has a complete renderable visual."""
    mode = _scene_frame_mode(scene)
    visual = scene.primary_visual
    props = visual.props if isinstance(visual.props, Mapping) else {}
    if mode == "map":
        if visual.type != VisualSceneType.ANIMATED_MAP.value:
            return "map_frame_has_no_map_renderer"
        if not _map_props_are_renderable(props, scene.map_shot or props.get("map_shot") or {}):
            return "map_has_no_grounded_local_endpoint"
    elif mode == "diagram":
        # A validated bespoke composition is the diagram renderer's payload.
        # Requiring a second clause-based diagram here discarded successful
        # LLM geometry at the final handoff and silently erased its recipe.
        recipe = scene.template_recipe
        if visual.type == VisualSceneType.DIAGRAM.value and isinstance(recipe, Mapping):
            try:
                recipe = validate_template_recipe(recipe)
                if (_recipe_is_grounded(recipe, scene.narration)
                        and _recipe_supports_visual_type(recipe, VisualSceneType.DIAGRAM.value)
                        and not _diagram_recipe_quality_error(recipe, scene.duration, fps)):
                    return ""
            except ValueError:
                pass
        steps = props.get("steps") if isinstance(props.get("steps"), list) else []
        usable_steps = [step for step in steps if isinstance(step, Mapping) and str(step.get("desc") or step.get("label") or step.get("title") or "").strip()]
        if visual.type != VisualSceneType.DIAGRAM.value or props.get("no_data") or len(usable_steps) < 2:
            return "diagram_has_no_complete_stages"
        node_contract = props.get("node_copy_contract") if isinstance(props.get("node_copy_contract"), Mapping) else {}
        approved_labels = {
            node["label"].casefold()
            for node in validated_diagram_nodes(node_contract.get("nodes"), scene.narration)
        } if node_contract.get("source") in {"llm", "llm_scene_repair", "system_clause_extractor"} and node_contract.get("approved") is True else set()
        for step in usable_steps:
            label = str(step.get("desc") or step.get("label") or step.get("title") or "").strip()
            if label.casefold() in approved_labels:
                continue
            if len(label.split()) < 3 or re.search(r"\b(?:and|or|to|with|from|by|of|the|a|an|while|as)$", label, re.I):
                return "diagram_stage_is_fragmentary"
    elif mode == "data":
        valid_chart = visual.type == VisualSceneType.CHART.value and (
            (isinstance(props.get("data"), list) and len(props["data"]) >= 2)
            or bool(props.get("before") and props.get("after"))
            or bool(props.get("seriesA") and props.get("seriesB"))
        )
        valid_stat = visual.type == VisualSceneType.STAT_CARD.value and not props.get("no_data") and (
            props.get("value") is not None or props.get("target") is not None
        )
        valid_comparison = visual.type == VisualSceneType.COMPARISON.value and not props.get("no_data") and (
            props.get("leftValue") is not None and props.get("rightValue") is not None
        )
        valid_timeline = visual.type == VisualSceneType.TIMELINE.value and not props.get("no_data") and any(
            props.get(key) not in (None, "", [], {})
            for key in ("date", "year", "events", "eraSpan")
        )
        if not (valid_chart or valid_stat or valid_comparison or valid_timeline):
            return "data_frame_has_no_grounded_values"
    elif mode == "portrait":
        if visual.type != VisualSceneType.PERSON_PROFILE.value or not str(props.get("name") or "").strip():
            return "portrait_frame_has_no_named_subject"
    elif mode == "quote":
        if visual.type != VisualSceneType.QUOTE_CARD.value or not str(props.get("quote") or "").strip():
            return "quote_frame_has_no_narrated_quote"
    elif mode == "kinetic":
        if visual.type != VisualSceneType.KINETIC_TEXT.value or not str(props.get("text") or props.get("emphasis") or "").strip():
            return "kinetic_frame_has_no_copy"
    elif mode == "chapter":
        if visual.type != VisualSceneType.CHAPTER_TITLE.value or not str(props.get("title") or props.get("headline") or "").strip():
            return "chapter_frame_has_no_opening_title"
    elif mode == "evidence":
        capture = props.get("source_capture") if isinstance(props.get("source_capture"), Mapping) else {}
        if visual.type != VisualSceneType.DOCUMENT_HIGHLIGHT.value or capture.get("verification_status") != "exact_passage_verified":
            return "evidence_frame_has_no_verified_capture"
    elif mode == "media_only":
        if visual.type not in {VisualSceneType.SMART_IMAGE.value, VisualSceneType.SMART_VIDEO.value}:
            return "media_frame_has_graphic_primary"
    return ""


def _repair_scene_frame_contract(scene: SceneDecision, media_item: Optional[Mapping[str, Any]] = None, fps: int = 30) -> None:
    """Repair invalid director frame choices before serializing the edit plan."""
    scene.text = dict(scene.text or {})
    mode = _scene_frame_mode(scene)
    scene.text["frame_mode"] = mode
    visual = scene.primary_visual
    props = visual.props if isinstance(visual.props, dict) else {}

    # A grounded map payload is sufficient to select the map renderer even if
    # an earlier conservative branch left the primary type as SmartImage.
    if mode == "map" and _map_props_are_renderable(props, scene.map_shot or props.get("map_shot") or {}):
        visual.type = VisualSceneType.ANIMATED_MAP.value
        visual.variant = str(props.get("variant") or visual.variant or "country_spotlight")

    failure = _scene_frame_contract_failure(scene, fps)
    if failure:
        original_mode = mode
        asset = dict(visual.asset or {})
        if isinstance(media_item, Mapping):
            asset.update({key: value for key, value in media_item.items() if value not in (None, "")})
        src = str(asset.get("src") or asset.get("path") or asset.get("url") or "").lower()
        kind = str(asset.get("media_type") or asset.get("type") or "").lower()
        is_video = kind in {"video", "youtube", "clip"} or src.endswith((".mp4", ".webm", ".mov", ".m4v"))
        visual.type = VisualSceneType.SMART_VIDEO.value if is_video else VisualSceneType.SMART_IMAGE.value
        visual.variant = "full_bleed" if is_video else "full_bleed_cinematic"
        visual.props = {"variant": visual.variant, "image_treatment": "cinematic_grade"}
        if asset:
            visual.asset = asset
        scene.map_shot = {}
        scene.template_id = "full-bleed"
        scene.template_recipe = {}
        scene.text["frame_mode"] = "media_only"
        scene.text["frame_contract_validation"] = {
            "status": "repaired",
            "requested_mode": original_mode,
            "reason": failure,
            "resolved_mode": "media_only",
        }
        return

    compatible_templates = {
        "map": {"map-route", "map-card"},
        "diagram": {"cause-effect", "cause-effect-flow", "decision-tree"},
        "data": {"bar-chart", "line-chart", "donut-chart", "area-chart", "ranked-bars", "stat-counter", "money-counter", "split-screen", "timeline-trace", "timeline-strip", "date-card", "calendar-rip"},
        "portrait": {"profile-dossier", "person-dossier", "profile-card", "presenter-card"},
        "evidence": {"paper-explainer", "source-highlight", "redaction-reveal"},
        "quote": {"quote-card", "witness-quote"},
        "kinetic": {"keyword-reveal", "kinetic-statement", "kinetic-text", "headline-center"},
        "chapter": {"chapter-opener", "cold-open"},
    }
    allowed = compatible_templates.get(mode)
    template_recipe = scene.template_recipe if isinstance(scene.template_recipe, Mapping) else {}
    authored_recipe = bool(template_recipe.get("layers")) and scene.template_id == "generated-recipe"
    if allowed and scene.template_id and scene.template_id not in allowed and not authored_recipe:
        defaults = {
            "map": "map-route", "diagram": "cause-effect", "data": "bar-chart",
            "portrait": "profile-dossier", "evidence": "source-highlight",
            "quote": "quote-card", "kinetic": "kinetic-statement", "chapter": "chapter-opener",
        }
        if mode == "data" and visual.type == VisualSceneType.TIMELINE.value:
            scene.template_id = "timeline-trace"
        elif mode == "data" and visual.type == VisualSceneType.STAT_CARD.value:
            scene.template_id = "stat-counter"
        elif mode == "data" and visual.type == VisualSceneType.COMPARISON.value:
            scene.template_id = "split-screen"
        else:
            scene.template_id = defaults[mode]
    # Reconcile media type with asset kind to prevent SmartImage / video mismatch
    asset = dict(visual.asset or {})
    if isinstance(media_item, Mapping):
        asset.update({key: value for key, value in media_item.items() if value not in (None, "")})
    src = str(asset.get("src") or asset.get("path") or asset.get("url") or "").lower()
    kind = str(asset.get("media_type") or asset.get("type") or "").lower()
    is_video = kind in {"video", "youtube", "clip"} or src.endswith((".mp4", ".webm", ".mov", ".m4v"))
    if visual.type == VisualSceneType.SMART_IMAGE.value and is_video:
        visual.type = VisualSceneType.SMART_VIDEO.value
        visual.variant = "full_bleed"
        if isinstance(visual.props, dict):
            visual.props["variant"] = "full_bleed"
    elif visual.type == VisualSceneType.SMART_VIDEO.value and not is_video and src:
        visual.type = VisualSceneType.SMART_IMAGE.value
        visual.variant = "full_bleed_cinematic"
        if isinstance(visual.props, dict):
            visual.props["variant"] = "full_bleed_cinematic"
    scene.text["frame_contract_validation"] = {
        "status": "pass",
        "frame_mode": mode,
        "visual_type": visual.type,
    }


def validated_diagram_nodes(raw_nodes, narration):
    """Keep short model labels only when both label and evidence are spoken."""
    if not isinstance(raw_nodes, list):
        return []
    text = re.sub(r"\s+", " ", str(narration or "")).strip()
    accepted = []
    seen = set()
    for node in raw_nodes[:4]:
        if not isinstance(node, Mapping):
            continue
        label = re.sub(r"\s+", " ", str(node.get("label") or "")).strip()
        source = re.sub(r"\s+", " ", str(node.get("source_span") or "")).strip()
        if not label or len(label) > 64 or len(label.split()) > 6 or not source:
            continue
        if source.casefold() not in text.casefold() or not re.search(
            r"(?<!\w)" + re.escape(label) + r"(?!\w)", source, re.IGNORECASE,
        ):
            continue
        if label.casefold() in seen:
            continue
        seen.add(label.casefold())
        accepted.append({"label": label, "source_span": source})
    return accepted


def validated_diagram_links(raw_links, nodes, narration):
    """Keep only explicitly narrated relations between approved diagram actors."""
    if not isinstance(raw_links, list):
        return []
    text = re.sub(r"\s+", " ", str(narration or "")).strip()
    labels = {str(node.get("label") or "") for node in nodes if isinstance(node, Mapping)}
    accepted, seen = [], set()
    for link in raw_links[:8]:
        if not isinstance(link, Mapping):
            continue
        source = str(link.get("from") or "").strip()
        target = str(link.get("to") or "").strip()
        evidence = re.sub(r"\s+", " ", str(link.get("evidence") or "")).strip()
        key = (source.casefold(), target.casefold())
        if source not in labels or target not in labels or source == target or key in seen:
            continue
        if not evidence or len(evidence) > 180 or evidence.casefold() not in text.casefold():
            continue
        # Validate a local clause. A verb elsewhere in the evidence sentence
        # must not connect actors in unrelated or explicitly denied clauses.
        clauses = re.split(r"[.!?;]|,\s*and\b|\b(?:while|whereas|but)\b", evidence, flags=re.I)
        local = [clause for clause in clauses
                 if all(re.search(r"(?<!\w)" + re.escape(label) + r"(?!\w)", clause, re.I)
                        for label in (source, target))
                 and not re.search(r"\b(?:not(?!\s+only\b)|never|neither|cannot|without)\b|n['’]t\b", clause, re.I)]
        if not local:
            continue
        directional = []
        for clause in local:
            source_match = re.search(r"(?<!\w)" + re.escape(source) + r"(?!\w)", clause, re.I)
            target_match = re.search(r"(?<!\w)" + re.escape(target) + r"(?!\w)", clause, re.I)
            if source_match.start() < target_match.start():
                # A passive relation names its recipient first, so reversing
                # an active sentence must not silently flip its arrow.
                between = clause[source_match.end():target_match.start()]
                if not re.search(r"\bby\s+(?:the\s+)?$", between, re.I):
                    directional.append(clause)
            elif re.search(r"\b(?:powered|charged|fed|supplied|controlled|caused|carried|produced|heated|cooled|driven|enabled)\s+by\s+(?:the\s+)?$",
                           clause[target_match.end():source_match.start()], re.I):
                directional.append(clause)
        if not directional:
            continue
        relation_text = " ".join(directional)
        if not re.search(
            r"\b(?:cause|causes|caused|drive|drives|driven|lead|leads|led|carry|carries|carried|shift|shifts|shifted|form|forms|formed|create|creates|created|trigger|triggers|triggered|flow|flows|connect|connects|connected|before|after|then|follow|follows|followed|through|from|to|move|moves|moved|rise|rises|rose|fall|falls|fell|increase|increases|decrease|decreases|transfer|transfers|transferred|change|changes|changed|become|becomes|became|produce|produces|produced|result|results|resulted|enable|enables|enabled|warm|warms|cool|cools|block|blocks|blocked|sweep|sweeps|orbit|orbits|occlude|occludes|reflect|reflects|absorb|absorbs|rotate|rotates|protect|protects|press|presses|push|pushes|pull|pulls|passes|between|reaches)\b",
            relation_text,
            re.IGNORECASE,
        ) and not re.search(
            r"\b(?:charg(?:e|es|ed|ing)|power(?:s|ed|ing)?|stor(?:e|es|ed|ing)|suppl(?:y|ies|ied)|feed(?:s|ing)?|fed|"
            r"convert(?:s|ed|ing)?|consum(?:e|es|ed|ing)|deliver(?:s|ed|ing)?|enter(?:s|ed|ing)?|"
            r"exit(?:s|ed|ing)?|contain(?:s|ed|ing)?|support(?:s|ed|ing)?|control(?:s|led|ling)?|"
            r"send(?:s|ing)?|sent|receiv(?:e|es|ed|ing)|transmit(?:s|ted|ting)?|"
            r"lift(?:s|ed|ing)?|lower(?:s|ed|ing)?|heat(?:s|ed|ing)?|cool(?:s|ed|ing)?)\b",
            relation_text, re.IGNORECASE,
        ):
            continue
        if not all(re.search(r"(?<!\w)" + re.escape(label) + r"(?!\w)", evidence, re.IGNORECASE)
                   for label in (source, target)):
            continue
        accepted.append({"from": source, "to": target, "evidence": evidence})
        seen.add(key)
    return accepted


def _build_diagram_props(
    variant: str,
    raw_text: str,
    triggers: Any,
) -> Dict[str, Any]:
    """Build process / system diagram props."""
    pv_props: Dict[str, Any] = {"variant": variant}
    raw_lower = raw_text.lower()
    physical_relation_denied = bool(re.search(
        r"\b(?:not(?!\s+only\b)|never|neither|cannot|without)\b|n['’]t\b", raw_lower))

    # Diagram headings are visible editorial copy, so the LLM copy contract
    # owns them. The component can explain the relationship without a generic
    # "cause & effect" title plate.
    pv_props["title"] = ""

    # This relationship is a physical grammar, independent of a brand or film.
    # Exact narrated actors authorize the schematic, never invented metrics.
    if (not physical_relation_denied and all(re.search(r"\b" + actor + r"\b", raw_lower) for actor in ("fuel", "engine", "motion"))
            and re.search(r"\b(?:turn\w*|convert\w*)\b.{0,40}\bfuel\b.{0,25}\binto\s+motion\b", raw_lower)):
        nodes = [{"label": re.search(r"\b" + actor + r"\b", raw_text, re.I).group(0), "source_span": raw_text}
                 for actor in ("fuel", "engine", "motion")]
        pv_props.update(variant="energy_conversion", illustrative_not_to_scale=True,
            steps=[{"title": node["label"], "desc": node["source_span"]} for node in nodes],
            node_copy_contract={"source": "system_clause_extractor", "approved": True, "nodes": nodes},
            links=[{"from": nodes[0]["label"], "to": nodes[1]["label"]},
                   {"from": nodes[1]["label"], "to": nodes[2]["label"]}])
        return pv_props

    # This is a physical relationship grammar, not a film-specific template.
    # Only the explicitly narrated solar alignment permits the shadow model.
    if not physical_relation_denied and all(re.search(r"\b" + actor + r"\b", raw_lower) for actor in ("sun", "moon", "earth")) and re.search(
        r"moon\s+passes\s+between\s+the\s+sun\s+and\s+(?:the\s+)?earth", raw_lower,
    ):
        nodes = [{"label": re.search(r"\b" + actor + r"\b", raw_text, re.I).group(0), "source_span": raw_text}
                 for actor in ("Sun", "Moon", "Earth")]
        pv_props.update({
            "variant": "eclipse_alignment", "illustrative_not_to_scale": True,
            "steps": [{"title": node["label"], "desc": node["source_span"]} for node in nodes],
            "node_copy_contract": {"source": "system_clause_extractor", "approved": True, "nodes": nodes},
            "links": [],
        })
        return pv_props

    if not physical_relation_denied and all(re.search(r"\b" + actor + r"\b", raw_lower) for actor in ("moon", "sunlight", "earth")) and re.search(
        r"\bmoon\s+blocks\s+the\s+sunlight\b", raw_lower,
    ) and "shadow" in raw_lower:
        nodes = [{"label": re.search(r"\b" + actor + r"\b", raw_text, re.I).group(0), "source_span": raw_text}
                 for actor in ("sunlight", "Moon", "Earth")]
        pv_props.update({"variant": "shadow_projection", "illustrative_not_to_scale": True,
                         "steps": [{"title": node["label"], "desc": node["source_span"]} for node in nodes],
                         "node_copy_contract": {"source": "system_clause_extractor", "approved": True, "nodes": nodes},
                         "links": []})
        return pv_props

    has_process_relation = bool(re.search(
        r"\b(?:then|after|before|while|as|because|therefore|as a result|which meant|which allowed|caused|forced|carried|delivered|built|triggered|led to|joined|rose|assembled|aligned|checked|fixed|supported|resisted|goes wrong|pulls (?:them|it|him|her) into|turns? into|unravels|collapses into)\b",
        raw_lower,
    ))
    clauses = [
        re.sub(r"^(?:and|or|while|as|because|therefore|then|after|before|whose|which)\s+", "", re.sub(r"[.!?]+$", "", c.strip()), flags=re.I)
        for c in re.split(
            r'[,;]|\b(?:then|after|before|while|as|because|therefore|as a result|which meant|which allowed)\b|'
            r'\band(?=\s+(?:pulls?|leads?|turns?|causes?|forces?|triggers?|unravels|collapses))',
            raw_text, flags=re.I,
        )
        if len(c.strip()) > 8
    ]
    clauses = list(dict.fromkeys(clauses))[:3]
    if has_process_relation and len(clauses) >= 2 and all(len(clause.split()) >= 3 for clause in clauses):
        def concise_exact_label(clause: str) -> str:
            """Select a short, contiguous label from the narrated clause."""
            clean = re.sub(r"^[\s,;:.!?]+|[\s,;:.!?]+$", "", str(clause or ""))
            words = re.findall(r"[\w’'-]+", clean)
            if len(words) <= 5:
                return clean
            # Prefer a short object/subject phrase after a relation marker,
            # while retaining exact script wording and its word order.
            for marker in (r"\b(?:on|into|across|through|around|between|toward|from)\s+", r"\b(?:whose|which)\s+"):
                matches = list(re.finditer(marker, clean, re.I))
                for match in reversed(matches):
                    candidate = clean[match.end():].strip()
                    candidate_words = re.findall(r"[\w’'-]+", candidate)
                    if 2 <= len(candidate_words) <= 5:
                        return candidate
            # Remove a subordinate-clause lead-in, then keep a compact exact
            # phrase. This avoids invented labels such as PHASE 1.
            clean = re.sub(r"^(?:whose|which|that|they|it|he|she|and|or)\s+", "", clean, flags=re.I)
            return " ".join(re.findall(r"[\w’'-]+", clean)[:5])

        steps = []
        seen_labels = set()
        for clause in clauses:
            label = concise_exact_label(clause)
            key = label.casefold()
            if len(label.split()) >= 2 and key not in seen_labels:
                seen_labels.add(key)
                # Keep the full exact clause as evidence for contract checks;
                # the renderer presents only the concise actor label.
                steps.append({"title": label, "desc": clause})
        if len(steps) >= 2:
            pv_props["steps"] = steps
            approved_nodes = validated_diagram_nodes(
                [{"label": step["title"], "source_span": step["desc"]} for step in steps],
                raw_text,
            )
            if len(approved_nodes) >= 2:
                pv_props["node_copy_contract"] = {
                    "source": "system_clause_extractor",
                    "approved": True,
                    "nodes": approved_nodes,
                }
        else:
            pv_props["no_data"] = True
            pv_props["reason"] = "No two distinct, concise narrated stages were available"
    else:
        pv_props["no_data"] = True
        pv_props["reason"] = "No complete, connected process stages were narrated"
    return pv_props


def _grounded_diagram_recovery(decision: Mapping[str, Any], narration: str) -> Dict[str, Any]:
    """Recover a real narrated mechanism without accepting invalid bespoke geometry."""
    built = _build_diagram_props(str(decision.get("variant") or "cause_and_effect_flow"), narration, None)
    if built.get("variant") in {"eclipse_alignment", "shadow_projection", "energy_conversion"} and not built.get("no_data") and built.get("node_copy_contract", {}).get("approved"):
        return built
    nodes = validated_diagram_nodes(decision.get("diagram_nodes"), narration)
    links = validated_diagram_links(decision.get("diagram_links"), nodes, narration)
    if len(nodes) >= 2 and links:
        variant = str(decision.get("variant") or "cause_and_effect_flow")
        if variant not in {"cause_and_effect_flow", "step_by_step_process", "network_node_connect", "hierarchical_system_tree"}:
            variant = "cause_and_effect_flow"
        return {"variant": variant, "title": "",
                "steps": [{"title": node["label"], "desc": node["source_span"]} for node in nodes],
                "node_copy_contract": {"source": "llm", "approved": True, "nodes": nodes}, "links": links}
    return {}


def _recover_explanatory_decision(decision: Dict[str, Any], narration: str) -> bool:
    recovery = _grounded_diagram_recovery(decision, narration)
    if not recovery:
        return False
    decision.update({"type": VisualSceneType.DIAGRAM.value,
        "variant": recovery["variant"], "frame_mode": "diagram",
        "visual_role": "primary_explanation", "template_id": "full-bleed", "template_recipe": {},
        "diagram_nodes": recovery["node_copy_contract"]["nodes"],
        "diagram_links": recovery.get("links", []),
        "_composition_pass": "grounded_specialist_recovery", "_composition_quality_rejected": False})
    return True


def _build_kinetic_text_props(
    variant: str,
    raw_text: str,
    triggers: Any,
) -> Dict[str, Any]:
    """Build kinetic typography props."""
    pv_props: Dict[str, Any] = {"variant": variant}
    raw_lower = raw_text.lower()

    emphasis = ""
    for w in ["never", "everything", "forever", "shattered", "record", "greatest", "deadliest", "ultimate", "revolution", "truth"]:
        if w in raw_lower:
            emphasis = w.upper()
            break

    # A local keyword heuristic must not silently become the on-screen text.
    # _sync_scene_display_props fills these only from an approved LLM phrase.
    pv_props["text"] = ""
    pv_props["emphasis"] = ""
    pv_props["kicker"] = ""
    return pv_props


def _build_comparison_props(
    variant: str,
    raw_text: str,
    numbers: Sequence[str],
    triggers: Any,
) -> Dict[str, Any]:
    """Build comparative analysis props."""
    pv_props: Dict[str, Any] = {"variant": variant}
    raw_lower = raw_text.lower()

    extracted = _extract_numbers_from_text(raw_text)

    pv_props["title"] = ""
    from .production_contract import _narrated_score_comparison
    score_pair = _narrated_score_comparison(raw_text)
    if score_pair:
        return {**pv_props, **score_pair}
    if "before" in raw_lower and "after" in raw_lower:
        pv_props["leftLabel"] = "BEFORE"
        pv_props["rightLabel"] = "AFTER"
    elif "vs" in raw_lower or "versus" in raw_lower:
        pv_props["leftLabel"] = ""
        pv_props["rightLabel"] = ""
    else:
        pv_props["leftLabel"] = ""
        pv_props["rightLabel"] = ""
        pv_props["no_data"] = True
        pv_props["reason"] = "No explicit comparison was narrated"

    if len(extracted) >= 2:
        pv_props["leftValue"] = extracted[0]["display"]
        pv_props["rightValue"] = extracted[-1]["display"]
    elif numbers and len(numbers) >= 2:
        pv_props["leftValue"] = numbers[0]
        pv_props["rightValue"] = numbers[1]
    else:
        pv_props["no_data"] = True
        pv_props["reason"] = "No grounded before/after values were narrated"

    return pv_props


def _build_stat_card_props(
    variant: str,
    raw_text: str,
    numbers: Sequence[str],
    triggers: Any,
) -> Dict[str, Any]:
    """Build KPI metric and stat card props."""
    pv_props: Dict[str, Any] = {"variant": variant}
    extracted = _extract_numbers_from_text(raw_text)
    extracted.sort(key=lambda item: 0 if re.search(r"[$£€]|dollars?|pounds?|euros?", str(item.get("full_match", "")), re.I) else 1)

    # A metric label is supporting copy, not a truncated caption.  The old
    # approach used the first 40 characters of narration, which could turn
    # a sentence split into a dangling fragment such as "JEWELRY AND OTHER".
    # Keep the number's meaning intact with a complete, factual label.
    lower_text = str(raw_text or "").lower()
    ratio_label = _extract_ratio_metric_label(raw_text, extracted[0]["raw"] if extracted else None)
    if ratio_label:
        metric_label = ratio_label
    elif re.search(r"\bmission\b", lower_text) and re.search(r"\b(?:cost|budget|spent|spending)\b", lower_text):
        metric_label = "MISSION COST"
    elif "ransom" in lower_text:
        metric_label = "RANSOM"
    elif re.search(r"\b(?:found|recovered)\b", lower_text) and re.search(r"[$£€]|dollars?", lower_text):
        metric_label = "RECOVERED CASH"
    elif any(token in lower_text for token in ("cost", "budget", "spent", "spending", "price")):
        metric_label = "COST"
    elif any(token in lower_text for token in ("loss", "lost", "stolen", "taken", "valued", "value", "worth")):
        metric_label = "ESTIMATED LOSS" if any(token in lower_text for token in ("loss", "lost", "stolen")) else "ESTIMATED VALUE"
    elif any(token in lower_text for token in ("security layer", "security layers")):
        metric_label = "SECURITY LAYERS"
    elif any(token in lower_text for token in ("deposit box", "boxes opened", "boxes untouched")):
        metric_label = "DEPOSIT BOXES"
    else:
        metric_label = "KEY METRIC"

    if metric_label == "KEY METRIC" and extracted and extracted[0].get("spoken_source"):
        unit = str(extracted[0].get("unit") or "")
        if unit and unit not in {"hundred", "thousand", "million", "billion", "trillion", "%"}:
            metric_label = unit.upper()

    if extracted:
        ex = extracted[0]
        # Compound values are stored as their expanded numeric magnitude for
        # source fidelity (for example, 2.3 million -> 2,300,000). The counter
        # itself must animate the spoken mantissa when its unit is shown as a
        # suffix; otherwise the frame reads "2,300,000 MILLION".
        display_value = ex["raw"]
        if ex.get("_compound"):
            spoken_mantissa = re.search(r"\d[\d,]*(?:\.\d+)?", str(ex.get("display") or ""))
            if spoken_mantissa:
                mantissa = spoken_mantissa.group(0).replace(",", "")
                display_value = float(mantissa)
                if "." in mantissa:
                    pv_props["decimals"] = len(mantissa.split(".", 1)[1])
            pv_props["source_value"] = ex["raw"]
        pv_props["value"] = display_value
        pv_props["target"] = display_value
        pv_props["prefix"] = str(ex.get("prefix") or ("$" if "$" in ex["full_match"] else ""))
        pv_props["suffix"] = f" {ex['unit'].upper()}" if ex['unit'] and ex['unit'] != '%' else ("%" if ex['unit'] == '%' else "")
        pv_props["label"] = metric_label
        # A narrated number is not its own source label. Keep the graphic
        # factual and visually clean unless research supplied a real citation.
        pv_props["kicker"] = ""
        qualifier = re.search(
            r"\b(?:in today's money|in current dollars|adjusted for inflation|inflation-adjusted dollars)\b",
            str(raw_text or ""),
            re.I,
        )
        if qualifier:
            pv_props["context"] = qualifier.group(0).upper()
        explicit_delta = re.search(r"\bfrom\b\s+.+?\bto\b\s+.+", str(raw_text or ""), re.I)
        if str(variant).lower() == "delta_growth_badge" and len(extracted) >= 2 and explicit_delta:
            pv_props["fromVal"] = str(extracted[0].get("display") or extracted[0].get("raw"))
            pv_props["toVal"] = str(extracted[1].get("display") or extracted[1].get("raw"))
            pv_props["fromLabel"] = "FROM"
            pv_props["toLabel"] = "TO"
    else:
        pv_props["no_data"] = True
        pv_props["reason"] = "No grounded metric was narrated"

    return pv_props


def _extract_ratio_metric_label(raw_text: str, numerator: Any = None) -> str:
    """Keep the denominator and subject attached to narrated ratio statistics.

    A number such as ``123`` is not useful as a standalone editorial card when
    the script says ``123 of the center's 160 safes``. This helper only derives
    the label from nouns and figures present in that same script passage.
    """
    match = re.search(
        r"\b(?P<numerator>\d[\d,]*)\s+(?:out\s+of|of)\s+"
        r"(?:(?:the|their|its)\s+)?(?:[A-Za-z][A-Za-z’'\-]*\s+){0,4}?"
        r"(?P<denominator>\d[\d,]*)\s+(?P<noun>[A-Za-z][A-Za-z’'\-]*)\b",
        str(raw_text or ""),
        re.IGNORECASE,
    )
    if not match:
        return ""
    try:
        expected = float(str(match.group("numerator")).replace(",", ""))
        if numerator is not None and float(numerator) != expected:
            return ""
    except (TypeError, ValueError):
        return ""
    denominator = match.group("denominator").replace(",", "")
    noun = match.group("noun").upper()
    return f"OF {denominator} {noun}"


def _build_headline_props(
    variant: str,
    raw_text: str,
    dates: Sequence[str],
    triggers: Any,
) -> Dict[str, Any]:
    """Build newspaper / press headline props."""
    pv_props: Dict[str, Any] = {"variant": variant}
    pv_props["headline"] = ""
    if dates:
        pv_props["date"] = dates[0].upper()
    pv_props["subdeck"] = ""
    pv_props["no_data"] = True
    pv_props["reason"] = "A source-backed document headline was not supplied"
    return pv_props


def _build_document_props(
    variant: str,
    raw_text: str,
    triggers: Any,
) -> Dict[str, Any]:
    """Build grounded evidence typography props without generic card labels."""
    pv_props: Dict[str, Any] = {"variant": variant}
    pv_props["title"] = ""
    pv_props["highlight"] = ""
    pv_props["no_data"] = True
    pv_props["reason"] = "A verified document excerpt was not supplied"
    return pv_props


def _build_collage_props(
    variant: str,
    raw_text: str,
    asset: Optional[Dict[str, Any]],
    triggers: Any,
) -> Dict[str, Any]:
    """Build collage & evidence board props."""
    pv_props: Dict[str, Any] = {"variant": variant}
    pv_props["title"] = ""
    src = (asset.get("src") or asset.get("path") if asset else "") or ""
    if not src:
        return {
            "variant": variant,
            "no_data": True,
            "reason": "A collage requires distinct source media",
        }
    pv_props["images"] = [
        {"src": src, "label": "PRIMARY ARTIFACT"},
    ]
    alternates = list((asset or {}).get("alternates") or [])
    for index, alternate in enumerate(alternates[:2], start=2):
        alternate_src = str((alternate or {}).get("src") or (alternate or {}).get("path") or "").strip()
        if alternate_src and alternate_src != src:
            pv_props["images"].append({"src": alternate_src, "label": f"SOURCE {index}"})
    if len(pv_props["images"]) < 2:
        pv_props["no_data"] = True
        pv_props["reason"] = "A collage requires at least two distinct source media items"
    return pv_props


def _plan_deterministic_scenes(
    scene_texts: Sequence[str],
    scene_durations: Sequence[float],
    media_items: Optional[Sequence[Mapping[str, Any]]] = None,
    word_timings: Optional[Sequence[Mapping[str, Any]]] = None,
    visual_dna: Optional[VisualDNA] = None,
) -> List[SceneDecision]:
    """Fast, reliable deterministic scene planner using semantic triggers when offline or without LLM."""
    scenes: List[SceneDecision] = []
    current_time = 0.0
    media_list = list(media_items or [])
    total_scenes = len(scene_texts)

    used_chart_variants: List[str] = []
    card_theme = visual_dna.chart_style if visual_dna else "editorial_white"
    if card_theme not in ("white_editorial", "dark_modern", "editorial_white"):
        card_theme = "white_editorial"
    if card_theme == "editorial_white":
        card_theme = "white_editorial"

    for idx, raw_text in enumerate(scene_texts):
        duration = float(scene_durations[idx]) if idx < len(scene_durations) else 4.0
        start = current_time
        end = start + duration
        current_time = end

        # Compute full sentence context bounded by full stops or script boundaries
        start_bound = idx
        while start_bound > 0 and not re.search(r'[.!?]["”]?\s*$', scene_texts[start_bound - 1]):
            start_bound -= 1
        end_bound = idx
        while end_bound < len(scene_texts) - 1 and not re.search(r'[.!?]["”]?\s*$', scene_texts[end_bound]):
            end_bound += 1
        sentence_context = ' '.join(scene_texts[start_bound:end_bound + 1])

        triggers = analyze_sentence_triggers(raw_text, is_first_scene=(idx == 0), context_text=sentence_context)
        pacing = compute_beat_pacing(raw_text, idx, total_scenes)

        asset = None
        if idx < len(media_list) and media_list[idx]:
            asset = dict(media_list[idx])

        # Formulate primary visual props with universal builders
        rec_type = triggers.recommended_scene_type
        rec_variant = triggers.recommended_variant
        if idx > 0 and _is_single_financial_stat(raw_text):
            rec_type = VisualSceneType.STAT_CARD.value
            rec_variant = "hero_counter_large"

        if rec_type == VisualSceneType.CHART.value:
            pv_props = _build_chart_props(
                rec_variant,
                raw_text,
                triggers.numbers,
                card_theme,
                used_chart_variants,
                context_text=sentence_context,
            )
        elif rec_type == VisualSceneType.ANIMATED_MAP.value:
            pv_props = _build_map_props(rec_variant, triggers, raw_text)
        elif rec_type == VisualSceneType.STAT_CARD.value:
            pv_props = _build_stat_card_props(rec_variant, raw_text, triggers.numbers, triggers)
        elif rec_type == VisualSceneType.TIMELINE.value:
            pv_props = _build_timeline_props(rec_variant, raw_text, triggers.dates, triggers)
        elif rec_type == VisualSceneType.PERSON_PROFILE.value:
            pv_props = _build_person_props(rec_variant, raw_text, triggers.persons, triggers)
        elif rec_type == VisualSceneType.DIAGRAM.value:
            pv_props = _build_diagram_props(rec_variant, raw_text, triggers)
        elif rec_type == VisualSceneType.KINETIC_TEXT.value:
            pv_props = _build_kinetic_text_props(rec_variant, raw_text, triggers)
        elif rec_type == VisualSceneType.COMPARISON.value:
            pv_props = _build_comparison_props(rec_variant, raw_text, triggers.numbers, triggers)
        elif rec_type == VisualSceneType.COLLAGE.value:
            pv_props = _build_collage_props(rec_variant, raw_text, asset, triggers)
        elif rec_type == VisualSceneType.HEADLINE.value:
            pv_props = _build_headline_props(rec_variant, raw_text, triggers.dates, triggers)
        elif rec_type == VisualSceneType.DOCUMENT_HIGHLIGHT.value:
            pv_props = _build_document_props(rec_variant, raw_text, triggers)
        elif rec_type == VisualSceneType.QUOTE_CARD.value:
            pv_props = {"variant": rec_variant}
            if not triggers.quotes:
                pv_props["no_data"] = True
                pv_props["reason"] = "No explicit quote was narrated"
            else:
                pv_props["quote"] = triggers.quotes[0]
            speaker = triggers.persons[0] if triggers.persons else ""
            if not speaker:
                m = re.search(r'([A-Z][A-Za-z0-9\s]+?)\s+(?:later\s+)?(?:declared|stated|noted|announced|said|wrote|claimed|proclaimed|testified)', f"{sentence_context} {raw_text}")
                if m:
                    speaker = m.group(1).strip()
            pv_props["speaker"] = (speaker or "DIRECT STATEMENT").upper()
            pv_props["role"] = "Documented Statement" if triggers.quotes else ""
        elif rec_type == VisualSceneType.CHAPTER_TITLE.value:
            pv_props = {"variant": rec_variant}
            pv_props["kicker"] = f"ACT 0{idx + 1}"
            # Chapter beats are visual punctuation.  Never turn narration into
            # a paragraph heading; the compact grounded phrase is applied
            # again after the LLM pass and remains empty when no strong beat
            # deserves on-screen type.
            chapter_highlight = _compact_grounded_highlight(raw_text)
            pv_props["title"] = chapter_highlight.upper() if chapter_highlight else ""
            pv_props["subtitle"] = ""
        else:
            pv_props = {"variant": rec_variant}

        rec_type = triggers.recommended_scene_type
        if idx > 0 and _is_single_financial_stat(raw_text):
            rec_type = VisualSceneType.STAT_CARD.value
            rec_variant = "hero_counter_large"
            pv_props = _build_stat_card_props(rec_variant, raw_text, triggers.numbers, triggers)
        if rec_type == VisualSceneType.CHART.value and pv_props.get("no_data") and _extract_numbers_from_text(raw_text):
            # An incomparable pair still contains a real metric worth showing.
            # Keep one narrated measurement as a short stat over media rather
            # than fabricating a common chart scale or discarding the fact.
            rec_type = VisualSceneType.STAT_CARD.value
            rec_variant = "hero_counter_large"
            pv_props = _build_stat_card_props(rec_variant, raw_text, triggers.numbers, triggers)
            pv_props["kicker"] = ""
            if pv_props.get("label") == "KEY METRIC":
                pv_props["label"] = ""
        if rec_type == VisualSceneType.STAT_CARD.value and pv_props.get("no_data"):
            rec_type = VisualSceneType.SMART_IMAGE.value
            rec_variant = "full_bleed_cinematic"
            pv_props = {"variant": rec_variant, "image_treatment": "cinematic_grade"}
        if rec_type == VisualSceneType.SMART_IMAGE.value and asset:
            asset_type = str(asset.get("type") or "").lower()
            asset_src = str(asset.get("src") or asset.get("path") or "")
            if asset_type == "video" or asset_src.lower().endswith((".mp4", ".webm", ".mov", ".m4v")):
                rec_type = VisualSceneType.SMART_VIDEO.value
            elif asset_src and os.path.isfile(asset_src):
                try:
                    from PIL import Image as _PILImage
                    with _PILImage.open(asset_src) as _im:
                        _w, _h = _im.size
                        if _w > 0 and _h > 0:
                            _ar = round(_w / _h, 4)
                            asset["width"] = _w
                            asset["height"] = _h
                            asset["aspect_ratio"] = _ar
                            if _ar < 1.35:
                                asset["safe_framing"] = True
                                asset["fit"] = "contain"
                                pv_props["safe_framing"] = True
                                pv_props["fit"] = "contain"
                                pv_props["aspect_ratio"] = _ar
                except Exception:
                    pass

        primary_visual = PrimaryVisual(
            type=rec_type,
            variant=pv_props.get("variant", triggers.recommended_variant),
            props=pv_props,
            asset=asset,
        )

        # The 3-Second Rule: Scene 0 must be high-energy and gripping immediately
        if idx == 0:
            cam = "slow_push"
            entrance = "mask_reveal"
            speed = 1.25
            pacing.energy = max(0.85, pacing.energy)
            pacing.importance = max(0.90, pacing.importance)
        else:
            cameras = ["slow_push", "pan_left", "drift", "focus_zoom", "pan_right"]
            cam = cameras[idx % len(cameras)]
            entrances = ["mask_reveal", "scale_pop", "fade", "slide_up"]
            entrance = entrances[idx % len(entrances)]
            speed = 1.0

        motion = MotionParameters(
            camera=cam,
            speed=speed,
            entrance=entrance,
        )

        transitions = ["hard_cut", "cross_dissolve", "zoom_cut", "hard_cut"]
        trans_out = transitions[idx % len(transitions)]

        fallback_frame_modes = {
            VisualSceneType.CHAPTER_TITLE.value: "chapter",
            VisualSceneType.ANIMATED_MAP.value: "map",
            VisualSceneType.PERSON_PROFILE.value: "portrait",
            VisualSceneType.DOCUMENT_HIGHLIGHT.value: "evidence",
            VisualSceneType.CHART.value: "data",
            VisualSceneType.STAT_CARD.value: "data",
            VisualSceneType.DIAGRAM.value: "diagram",
            VisualSceneType.QUOTE_CARD.value: "quote",
            VisualSceneType.KINETIC_TEXT.value: "kinetic",
        }
        decision = SceneDecision(
            scene_id=f"scene_{idx + 1}",
            scene_index=idx,
            start=round(start, 3),
            end=round(end, 3),
            duration=round(duration, 3),
            narration=raw_text,
            visual_intent="narrative_beat",
            primary_visual=primary_visual,
            text={
                "primary": raw_text,
                "frame_mode": fallback_frame_modes.get(rec_type, "media_only"),
                "overlay_policy": "none",
                "editorial_priority": round(float(pacing.importance), 2),
            },
            motion=motion,
            pacing=pacing,
            transition_out=trans_out,
            transition_duration=0.25,
        )
        decision = align_scene_to_words(decision, word_timings=word_timings)
        scenes.append(decision)

    return scenes


def _fold_place_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def country_code_for_place(value: str) -> str:
    """Resolve an exact grounded country name or ISO code from local Natural Earth data."""
    global _COUNTRY_CODE_BY_NAME_CACHE
    target = _fold_place_name(value).strip()
    if not target:
        return ""
    code_keys, _country_names, _city_names = load_geographic_catalog()
    normalized_codes = {str(code).strip().upper() for code in code_keys}
    if target.upper() in normalized_codes:
        return target.upper()
    if _COUNTRY_CODE_BY_NAME_CACHE is None:
        catalog: Dict[str, str] = {}
        try:
            root = __import__("pathlib").Path(__file__).resolve().parents[3]
            path = root / "src" / "remotion" / "maps" / "ne_110m_admin_0_countries.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            for feature in payload.get("features", []):
                props = feature.get("properties") or {}
                code = ""
                for key in ("ISO_A3_EH", "ISO_A3", "ADM0_A3"):
                    candidate = str(props.get(key) or "").strip().upper()
                    if candidate and candidate != "-99":
                        code = candidate
                        break
                if not code:
                    continue
                for key in ("ADMIN", "NAME", "NAME_EN", "NAME_LONG"):
                    name = _fold_place_name(props.get(key))
                    if name:
                        catalog.setdefault(name, code)
        except Exception:
            catalog = {}
        _COUNTRY_CODE_BY_NAME_CACHE = catalog
    return str(_COUNTRY_CODE_BY_NAME_CACHE.get(target) or "").upper()


def apply_grounded_map_country_props(props: Mapping[str, Any], map_shot: Mapping[str, Any]) -> Dict[str, Any]:
    """Make a map's rendered country props agree with its verified spoken place names."""
    result = dict(props or {})
    if isinstance(map_shot, Mapping):
        result["map_shot"] = dict(map_shot)
    locations = map_shot.get("location_names") if isinstance(map_shot, Mapping) else []
    if not isinstance(locations, list):
        locations = []
    grounded_countries = [
        (str(name or "").strip(), country_code_for_place(str(name or "")))
        for name in locations
        if country_code_for_place(str(name or ""))
    ]
    if not grounded_countries:
        return result

    # Preserve a grounded route. Previously, any named country could replace
    # city-to-city endpoints with a country spotlight, repeating the same shot.
    route_from = result.get("from") or result.get("start") or result.get("startCity")
    route_to = result.get("to") or result.get("end") or result.get("endCity")
    if route_from and route_to:
        return result

    # Prefer the first exact country in narration order. A city or continent
    # cannot override it, and unrelated country triggers cannot leak through.
    place, code = grounded_countries[0]
    country_info = WORLD_COUNTRIES_INFO.get(code) or {}
    display_name = str(country_info.get("name") or place).strip().upper()
    result.update({
        "variant": "country_spotlight",
        "country": code,
        "country_code": code,
        "location": display_name,
        "title": display_name,
        "map_shot": dict(map_shot),
    })
    for key in ("from", "to", "start", "end", "startCity", "endCity", "from_country", "to_country"):
        result.pop(key, None)
    return result


def _narrated_local_geography(text: str) -> tuple[list[str], list[str]]:
    """Find exact locally catalogued place names and admin1 feature IDs in prose."""
    country_codes, country_names, city_names = load_geographic_catalog()
    admin1 = load_admin1_catalog()
    known = {
        _fold_place_name(name)
        for name in (set(country_names) | set(city_names) | set(admin1) | set(REGION_CENTERS))
        if str(name or "").strip()
    }
    token_matches = list(re.finditer(r"[^\W_]+(?:[-][^\W_]+)*", str(text or ""), re.UNICODE))
    found: list[tuple[int, int, str]] = []
    for start_index, first in enumerate(token_matches):
        for end_index in range(start_index, min(len(token_matches), start_index + 8)):
            start, end = first.start(), token_matches[end_index].end()
            raw_name = str(text or "")[start:end].strip("'’-")
            folded = _fold_place_name(raw_name)
            if folded in known:
                found.append((start, end, raw_name))
    # Prefer longer official names over nested single-word matches such as
    # "Guinea" inside "Papua New Guinea" while preserving narration order.
    selected: list[tuple[int, int, str]] = []
    for candidate in sorted(found, key=lambda item: (item[0], -(item[1] - item[0]))):
        start, end, _name = candidate
        if any(start >= kept_start and end <= kept_end for kept_start, kept_end, _kept_name in selected):
            continue
        selected.append(candidate)
    unique_names = {}
    for _start, _end, name in sorted(selected, key=lambda item: item[0]):
        unique_names.setdefault(_fold_place_name(name), name)
    names = list(unique_names.values())
    feature_ids = list(dict.fromkeys(
        admin1[_fold_place_name(name)]
        for name in names
        if _fold_place_name(name) in admin1
    ))
    return names[:8], feature_ids[:8]


def _grounded_director_map(
    raw_text: str,
    variant: str,
    requested: Mapping[str, Any] | None = None,
    context_text: str = "",
) -> tuple[Dict[str, Any], Dict[str, Any]] | None:
    """Build a renderable local map from places literally named in this beat.

    The director is allowed to choose that a map explains the beat. Geography
    comes from the local catalog and narration, so an omitted/partial LLM map
    payload cannot turn a valid map decision into unrelated stock footage.
    """
    spoken_names, spoken_feature_ids = _narrated_local_geography(raw_text)
    context_names, context_feature_ids = _narrated_local_geography(context_text)
    requested = requested if isinstance(requested, Mapping) else {}
    # Use a single locally verified place from adjacent script context when
    # the current beat refers to an already established site ("the site by
    # the Seine"). Never turn other nearby place mentions into an implied route.
    grounded_names = spoken_names or context_names[:1]
    grounded_feature_ids = spoken_feature_ids or context_feature_ids[:1]
    spoken_by_folded = {_fold_place_name(name): name for name in grounded_names}
    exact_names = [
        spoken_by_folded[_fold_place_name(name)]
        for name in requested.get("location_names", [])
        if _fold_place_name(name) in spoken_by_folded
    ]
    names = list(dict.fromkeys(exact_names + grounded_names))[:8]
    feature_ids = list(dict.fromkeys(
        str(value).strip().upper()
        for value in (requested.get("feature_ids") or [])
        if str(value or "").strip().upper() in grounded_feature_ids
    ))
    feature_ids = list(dict.fromkeys(feature_ids + grounded_feature_ids))[:8]
    if not names and not feature_ids:
        return None

    map_shot = {
        "enabled": True,
        "location_names": names,
        "feature_ids": feature_ids,
        # A source ID must have come from the verified source manifest. The
        # caller may retain a route only when it independently verifies it.
        "route_id": str(requested.get("route_id") or "").strip(),
        "source_ids": [str(value) for value in requested.get("source_ids", []) if str(value).strip()][:8],
        "motion_profile": str(requested.get("motion_profile") or "auto").strip().lower(),
        "render_style": _safe_map_render_style(requested.get("render_style"), names),
    }
    triggers = analyze_sentence_triggers(raw_text)
    props = _build_map_props(variant or "country_spotlight", triggers, raw_text)
    if props.get("no_data"):
        place = names[0] if names else feature_ids[0]
        props = {
            "variant": "country_spotlight",
            "location": place.upper(),
            "title": place.upper(),
        }
    props = apply_grounded_map_country_props(props, map_shot)
    props["map_shot"] = map_shot
    props["render_style"] = map_shot["render_style"]
    props.pop("no_data", None)
    return props, map_shot


def _apply_directed_region_map(scene: SceneDecision, decision: Mapping[str, Any], narration: str) -> bool:
    """Promote an explicitly directed, exactly narrated sea/region cue to 3D."""
    map_requested = (
        str(decision.get("frame_mode") or "").strip().lower() == "map"
        or decision.get("type") == VisualSceneType.ANIMATED_MAP.value
        or any(cue.get("element") == "map" for cue in (scene.motion_cues or []) if isinstance(cue, Mapping))
    )
    if not map_requested:
        return False
    narrated_names, _features = _narrated_local_geography(narration)
    regions = [name for name in narrated_names if is_curated_region(name)]
    if not regions:
        return False
    request = dict(decision.get("map_shot") or {})
    request.update({"enabled": True, "location_names": regions, "render_style": "globe_3d"})
    grounded = _grounded_director_map(
        narration,
        str(decision.get("variant") or "country_spotlight"),
        request,
        narration,
    )
    if not grounded:
        return False
    props, map_shot = grounded
    map_shot["render_style"] = "globe_3d"
    map_shot["motion_profile"] = str(request.get("motion_profile") or "auto")
    props["render_style"] = "globe_3d"
    props["map_shot"] = map_shot
    scene.primary_visual.type = VisualSceneType.ANIMATED_MAP.value
    scene.primary_visual.variant = str(props.get("variant") or "country_spotlight")
    scene.primary_visual.props = props
    scene.map_shot = map_shot
    scene.template_id = "map-route"
    scene.text = dict(scene.text or {})
    scene.text["frame_mode"] = "map"
    return True


def _adjacent_site_location_context(scene_index: int, scene_texts: Sequence[str], raw_text: str) -> str:
    """Return adjacent script context only for an explicit site-location beat."""
    current = str(raw_text or "")
    has_site = bool(re.search(r"\b(?:site|location|located|where it stood|where it was built)\b", current, re.I))
    has_spatial_relation = bool(re.search(r"\b(?:beside|near|along|within|adjacent to|located|where it stood|where it was built)\b", current, re.I))
    if not has_site or not has_spatial_relation:
        return ""
    index = max(0, min(len(scene_texts), int(scene_index)))
    # Search back for the nearest explicit named locality because scene
    # splitting can put the city and the later "site" reference two beats
    # apart. Stop at the first named locality; do not merge unrelated places.
    prior_location = ""
    for prior in reversed(list(scene_texts[max(0, index - 4):index])):
        prior_names, _prior_features = _narrated_local_geography(str(prior or ""))
        if prior_names:
            prior_location = str(prior or "")
            break
    context = " ".join(part for part in (prior_location, current) if part)
    context_names, _feature_ids = _narrated_local_geography(context)
    if not context_names:
        return ""
    # Nearby names can establish a single site; they never authorize routes.
    return context


def _use_grounded_fallback_media(scene: SceneDecision) -> bool:
    """Use the resolved scene asset when a fallback graphic is ungrounded."""
    asset = scene.primary_visual.asset if isinstance(scene.primary_visual.asset, Mapping) else {}
    media_ref = str(asset.get("src") or asset.get("path") or asset.get("url") or "").strip()
    if not media_ref:
        return False
    media_kind = str(asset.get("media_type") or asset.get("type") or "").strip().lower()
    is_video = media_kind in {"video", "youtube", "clip"} or media_ref.split("?", 1)[0].lower().endswith(
        (".mp4", ".webm", ".mov", ".m4v")
    )
    scene.primary_visual.type = (
        VisualSceneType.SMART_VIDEO.value if is_video else VisualSceneType.SMART_IMAGE.value
    )
    scene.primary_visual.variant = "full_bleed_cinematic"
    scene.primary_visual.props = {"image_treatment": "cinematic_grade", "fit": "cover"}
    scene.map_shot = {}
    scene.template_id = "full-bleed"
    scene.text = {}
    return True


def _remove_text_only_fallback(scene: SceneDecision) -> None:
    """A failed director cannot leave a title-only primary with no approved title."""
    if scene.primary_visual.type not in {
        VisualSceneType.CHAPTER_TITLE.value,
        VisualSceneType.KINETIC_TEXT.value,
        VisualSceneType.HEADLINE.value,
    }:
        return
    if not _use_grounded_fallback_media(scene):
        scene.primary_visual.type = VisualSceneType.SMART_IMAGE.value
        scene.primary_visual.variant = "full_bleed_cinematic"
        scene.primary_visual.props = {"image_treatment": "cinematic_grade"}
        scene.template_id = "full-bleed"
    scene.text = {"frame_mode": "media_only", "overlay_policy": "none"}


def _apply_local_fallback_editing(scenes: Sequence[SceneDecision]) -> None:
    """Add only complete, grounded copy and verified geography to fallback scenes."""
    for scene in scenes:
        narration = str(scene.narration or "").strip()
        if not narration:
            continue
        names, feature_ids = _narrated_local_geography(narration)
        name_priorities = []
        admin1 = load_admin1_catalog()
        _country_codes, country_names, _city_names = load_geographic_catalog()
        for name in names:
            folded = _fold_place_name(name)
            priority = 4 if folded in admin1 else (3 if folded not in country_names else 1)
            name_priorities.append((priority, len(name), name))

        meta_map_explanation = bool(
            re.search(r"\b(?:map|visual) grammars?\b", narration, re.I)
            and re.search(r"\b(?:two|both|different)\b", narration, re.I)
        )
        if meta_map_explanation:
            scene.primary_visual.type = VisualSceneType.KINETIC_TEXT.value
            scene.primary_visual.variant = "bold_minimal_center"
            scene.primary_visual.props = _build_kinetic_text_props(
                "bold_minimal_center", narration, None,
            )
            scene.map_shot = {}
        elif scene.primary_visual.type == VisualSceneType.ANIMATED_MAP.value and not names:
            # Fictional places such as Vice City and Leonida have no verified
            # Natural Earth geometry. Preserve the actual scene asset instead
            # of drawing a plausible-looking but unsupported map.
            _use_grounded_fallback_media(scene)
        elif names and (
            scene.primary_visual.type == VisualSceneType.ANIMATED_MAP.value
            or re.search(r"\b(map|outline|border|polygon|geograph|city callout|map scene)\b", narration, re.I)
        ):
            triggers = analyze_sentence_triggers(
                narration,
                is_first_scene=scene.scene_index == 0,
                context_text=narration,
            )
            map_props = _build_map_props("country_spotlight", triggers, narration)
            map_props.pop("no_data", None)
            if feature_ids:
                map_props["admin1"] = feature_ids[0]
                region_name = next((name for name in names if _fold_place_name(name) in admin1), "")
                if region_name:
                    map_props["region"] = region_name
            map_shot = {
                "enabled": True,
                "location_names": names,
                "feature_ids": feature_ids,
                "route_id": "",
                "source_ids": [],
                "variant": "country_spotlight",
            }
            map_props = apply_grounded_map_country_props(map_props, map_shot)
            map_props["map_shot"] = map_shot
            scene.primary_visual.type = VisualSceneType.ANIMATED_MAP.value
            scene.primary_visual.variant = str(map_props.get("variant") or "country_spotlight")
            scene.primary_visual.props = map_props
            scene.map_shot = map_shot
            scene.template_id = "map-route"

        props = scene.primary_visual.props if isinstance(scene.primary_visual.props, Mapping) else {}
        source_capture = props.get("source_capture") if isinstance(props.get("source_capture"), Mapping) else {}
        verified_capture = (
            source_capture.get("status") == "verified_capture"
            and source_capture.get("verification_status") == "exact_passage_verified"
            and source_capture.get("exact_excerpt")
            and source_capture.get("image_path")
        )
        if scene.primary_visual.type in {
            VisualSceneType.HEADLINE.value,
            VisualSceneType.DOCUMENT_HIGHLIGHT.value,
        } and not verified_capture:
            # Never manufacture a newspaper/source label in the local fallback.
            _use_grounded_fallback_media(scene)

        selected_copy = _compact_grounded_highlight(
            narration,
            next((place_name for _priority, _length, place_name in sorted(name_priorities, reverse=True)), ""),
        )
        selected_source = _source_span_for_phrase(narration, selected_copy) if selected_copy else ""
        if not _valid_director_copy(narration, selected_copy, selected_source):
            selected_copy, selected_source = "", ""
        scene.display_text = selected_copy
        scene.source_span = selected_source
        scene.text = dict(scene.text or {})
        if selected_copy:
            scene.text["display_text"] = selected_copy
            scene.text["position"] = "top_left" if scene.primary_visual.type == VisualSceneType.ANIMATED_MAP.value else "center"
        else:
            scene.text = {}
            scene.emphasis_spans = []
            if scene.primary_visual.type in {
                VisualSceneType.KINETIC_TEXT.value,
                VisualSceneType.HEADLINE.value,
                VisualSceneType.DOCUMENT_HIGHLIGHT.value,
                VisualSceneType.CHAPTER_TITLE.value,
                VisualSceneType.QUOTE_CARD.value,
            }:
                _use_grounded_fallback_media(scene)

        emphasis_spans = []
        candidates = [name for _priority, _length, name in sorted(name_priorities, reverse=True)]
        candidates.extend(["maps", "map", "Remotion"])
        for candidate in dict.fromkeys(candidates):
            match = re.search(re.escape(candidate), selected_copy, re.I)
            if match:
                emphasis_spans.append({"start": match.start(), "end": match.end()})
                if len(emphasis_spans) >= 2:
                    break
        scene.emphasis_spans = emphasis_spans
        if scene.primary_visual.type == VisualSceneType.KINETIC_TEXT.value:
            scene.primary_visual.props["emphasis_spans"] = [dict(span) for span in emphasis_spans]
        _sync_scene_display_props(scene)


# ── LLM-Assisted Scene Planning ─────────────────────────────────────────────

_VALID_SCENE_TYPES = {
    'smartimage', 'smartvideo', 'kinetictext', 'statcard', 'quotecard',
    'headline', 'documenthighlight', 'animatedmap', 'timeline', 'chart',
    'comparison', 'collage', 'personprofile', 'diagram', 'chaptertitle',
}

_LLM_DIRECTOR_PROMPT = """You are an inventive documentary editor and Remotion motion-graphics director.
Create original Vox-style visual arguments: grounded source media, contemporary sans-serif typography,
precise evidence marks, maps, comparisons, chronology, cutouts and recognizable mechanism illustrations.
Diagnose the script's niche, evidence and emotional shape using the supplied film grammar. Maintain
coherent film fonts/palette while giving each scene its own visual intent, composition and reveal order.
Do not copy a video/logo. Do not invent facts, dates, quotes, sources, names, measurements or routes.

STORY AND VARIETY
Choose what the viewer must understand BEFORE selecting a component. Each layer must explain something
that media alone cannot. Aim near 30% meaningful graphics, weighted toward real explanation beats;
atmosphere, emotion, testimony and connective footage may breathe. Sparse visible copy is not sparse editing:
a map, mechanism, comparison or source reveal can explain without a headline;
that copy rule does not require the visual itself to be media_only. Never force graphs or cards to hit a quota. Avoid repeated layouts,
camera moves, entrances and transitions across neighboring scenes unless continuing the same argument.
Establish the actual subject and question immediately. In the first 15-20 seconds, use a meaningful reveal
or verified editorial cut every 2-3 seconds when supported, then settle. Longer scenes can reveal or reframe
every 3-5 seconds; give each reveal a readable hold. Choose trace, peel, bracket, assemble, count, split,
pin, reveal, lock or hold to express meaning; do not keep all layers moving. Decorative anonymous dots,
rings, brackets, fake telemetry, HUD/dashboard/dossier panels, emoji and generic filler are prohibited.

LIVE DESIGN CONTRACT
The supplied complete capability index and JSON schema are authoritative. Choose one supported component,
its exact variant, approved template_id and matching frame_mode per owned scene. All listed options remain
available: the opportunity board is advice, not a restricted shortlist. Return only the requested scenes JSON.
Use visual_role=media_overlay when media carries the story, primary_explanation when the graphic needs the
canvas, or map_canvas for geography. Give each scene one dominant visual owner; stagger subordinate cues in
separate time/screen regions. Match components to evidence:
- SmartImage / SmartVideo: descriptive, atmospheric or action media with purposeful framing.
- KineticText: rare decisive exact phrase; StatCard: one amount, preserving unit/qualifier over relevant media.
- Chart: at least two compatible narrated values; do not invent baselines, bins, shares or time-series relations.
- Comparison: explicit versus, before/after or supported contrast; scores retain both teams and both values.
- Timeline: narrated dates/chronology; a four-digit quantity with a unit is not automatically a year.
- AnimatedMap: actual narrated geography under the map rules below.
- PersonProfile: identified person and matching source imagery; isolated presentation requires a supplied
  transparent cutout asset. Never pretend an opaque image has background removal.
- Headline / DocumentHighlight: verified report/capture and exact visible passage; never manufacture a source.
- QuoteCard: exact quotation with grounded attribution. Collage: at least two distinct verified assets.
- Diagram: stated actors and relationship needing explanation; a metaphor, rivalry, named product or decision
  is not automatically a physical mechanism. Never represent people as anonymous floating spheres.
- ChapterTitle: opening or genuine section transition only.
Use media_only, kinetic, data, map, portrait, evidence, diagram, quote or chapter to match the component.
A supplied photo must not suppress a useful map/mechanism; an unrelated clip must not crowd out an explanation.

MEDIA AND SPOKEN EVENTS
Choose media_plan.visual_beats=1-4 for actual distinct ideas/moments, matching rhythm=single_hold/two_beat/
three_beat/four_beat. Prefer clip for action/evidence, images for documentary stills, either when suitable.
Use fewer beats when verified assets are insufficient. One still has one restrained continuous move; never
fake variety by replaying one file or duplicating a montage slot. A clip may supply separately verified
non-overlapping timestamp windows. Respect the phrase assigned to each cut. Do not substitute generic
sports/game footage for named events. Media descriptions are references, not proof of unseen source content.
Use one to three motion_cues for actual geography, pairings, stages, evidence or sourced numbers; anchors
must be exact narration phrases (empty only for an opening reveal). Identify pivotal named people at their
spoken full names, at most two identity tags, without covering a face/title/evidence. Keep structured graphic
cues even when headline copy is off. For Diagram provide 2-4 short verbatim actor labels, exact source_spans
and links only for explicitly narrated relationships, each with its exact evidence phrase.

VISIBLE COPY AND PLACEMENT
Respect the supplied film-wide short-copy budget and cooldown; select memorable names, units, reversals,
stakes or decisive claims rather than narration transcripts. overlay_policy is none or single_short_highlight.
For none, display_text/source_span/emphasis_spans are empty and text_treatment=null. Otherwise display_text
is a complete exact contiguous phrase from this scene, normally 1-3 words, maximum 5 words/42 characters;
source_span is its exact supporting narration phrase/sentence. Preserve full names, numbers, units, dates
and negation. Avoid generic labels, verbs and dangling fragments. Split name/value into separate timed beats when necessary.
Never split an introductory sentence into diagram actors. Diagrams require at least two distinct grounded
actors and an explained relationship, sequence or comparison. Unknown actor artwork is omitted; never use
crossed-out boxes, decorative target reticles or fabricated logos. A fragment like "The Toyota Corolla started"
must become a meaningful entity/fact highlight or no overlay. Keep readable line spacing and reserve caption space.
At most one emphasis span uses exact character offsets within display_text: one meaningful
word or tight two-word phrase, not an arbitrary first word or the entirety of a longer highlight. Empty spans
mean no accent backing; never infer replacement emphasis. Never overlap captions, approved titles or source
text. Use subject_box/safe_text_regions and negative space; placement=none for unlabelled full-frame media/maps.

TYPOGRAPHY, COLOR AND MOTION
Use supplied bundled fonts and exact film palette roles. text_color comes from primary/text_contrast,
accent_color from accent, panel_background from surface/background; not border, glow or accent2. Crime uses
charcoal/black, readable white and restrained crimson; other niches follow their actual film grammar.
Vary text_treatment deliberately: hero_type open shadowed type, stacked_type staggered lockup, outline_type,
marker_type tight phrase backing, editorial_caption compact type, or impact_slab for a rare hook/number/turn.
impact_slab can highlight a phrase, not just a number: tight accent backing only behind approved emphasis,
remaining words on a separate shadowed line. Do not make it default. editorial_banner/highlight_band/minimal_rule
require evidence/legibility justification. Backing fits glyphs, never huge empty boxes. Protect the focal subject.
Headings hold at most 2.6 seconds, emphasis 1.3-2.2 seconds. graphic_theme=editorial_light for analytic source/data,
cinematic_dark for atmospheric/spatial media, accent_focus for a decisive value/boundary/mechanism; vary only
for a narrative reason. No unrelated pink/purple/neon. A sparse copy policy is not sparse motion graphics.

MAPS
Before choosing media_only, check whether actual geography is the explanation: a resolved place, boundary,
blockade/corridor or stated movement earns a map.
A single place earns a locator or boundary reveal; it does not earn a route.
Routes require two exact verified endpoints and an explicit narrated connection/movement;
two incidental place mentions never imply a route, and flight arcs require stated flight. Use actual country/city
names; do not substitute prefecture polygons for cities. feature_ids must be supplied exact IDs named in narration.
render_style=globe_3d for world/interregional scale; satellite_local only for compact narrated geography with
verified coordinates and an attributed plate; vector_editorial for boundaries/spatial explanations otherwise.
Choose supported motion_profile to suit the subject and vary choreography: one eased world-to-subject move,
then stable readable hold, never oscillation. Non-maps have enabled=false/render_style=vector_editorial.

CUSTOM DESIGN
Choose an existing composition when it explains best; design generated-recipe for a distinctive grounded
argument that presets would flatten. The schema lists every supported layer/motion/mask. No JavaScript,
HTML, CSS or remote assets. Maximum 16 useful layers, one matching semantic layer plus purposeful source
imagery/geometry/masks and staged labels; not paragraph cards or decorative lines. Measurements, labels
and relationships stay verbatim and factual. A physical mechanism needs recognizable actors, not a node
network by default. Bind images by available image_index only; never duplicate one image to fill a layout.
Recipe geometry uses canvas percentages (12,20,70,58, not fractions); frame counts are local at supplied fps.
Semantic regions need at least 15% width/12% height; keep actors substantial/distinct, labels separate and all
layers in canvas/caption-safe bounds. Explicit grounded contact can justify actor overlap. Use only film palette
colors and reveal after the corresponding spoken trigger. For first-pass Diagram, select actors/relationships
and layout intent with template_recipe=null; the bounded composition pass supplies the illustration.

Before returning JSON, check narrated facts, media/beat alignment, duplicate neighboring arguments,
readability, subject/caption collisions and unsupported graphics. Revise once internally. Return JSON only,
without reasoning commentary, markdown fences or duplicated narrative explanations."""

DIRECTOR_DEFAULT_MODEL = "qwen/qwen3.5-flash-02-23"
DIRECTOR_FALLBACK_MODELS = ()


def _director_fallback_models(configured=None):
    """Resolve the documentary director's own ordered fallback chain.

    The general scene-brain chain serves unrelated pipeline stages and must
    not be prepended here; doing so can route the visual director to a
    different, often rate-limited model before its configured candidates.
    """
    if configured is None:
        configured = os.environ.get("VIDZEO_EDITORIAL_DIRECTOR_FALLBACK_MODELS")
    if isinstance(configured, str):
        configured = [item.strip() for item in re.split(r"[,;|]+", configured) if item.strip()]
    candidates = configured or DIRECTOR_FALLBACK_MODELS
    result = []
    seen = set()
    for item in candidates:
        model = str(item or "").strip()
        if model and model not in seen:
            seen.add(model)
            result.append(model)
    return tuple(result or DIRECTOR_FALLBACK_MODELS)


def _composition_model_route(director_config: Mapping[str, Any] | None, worker_client: Any) -> tuple[str, list[str]]:
    """Use the director's active model for composition, with its bounded fallback route.

    Composition is part of the editorial decision, so silently routing it to a
    weaker generic model made diagram recipes a frequent source of degradation.
    An explicit composition override is still honored; otherwise the scene
    director's model and known fallback chain are reused.
    """
    config = director_config if isinstance(director_config, Mapping) else {}
    requested_model = (
        config.get("composition_model")
        or os.environ.get("VIDZEO_EDITORIAL_COMPOSITION_MODEL")
        or getattr(worker_client, "model", None)
        or DIRECTOR_DEFAULT_MODEL
    )
    primary = str(requested_model or DIRECTOR_DEFAULT_MODEL).strip()

    configured_fallbacks = config.get("composition_fallback_models")
    if configured_fallbacks is None:
        configured_fallbacks = os.environ.get("VIDZEO_EDITORIAL_COMPOSITION_FALLBACK_MODELS")
    if isinstance(configured_fallbacks, str):
        configured_fallbacks = [item.strip() for item in re.split(r"[,;|]+", configured_fallbacks) if item.strip()]

    candidates = list(configured_fallbacks or ())
    candidates.extend(list(getattr(worker_client, "fallback_models", ()) or ()))
    candidates.extend(_director_fallback_models())
    # The efficient Qwen route must not inherit premium models from an older
    # environment file or a generic client fallback chain.
    if primary.startswith('qwen/'):
        candidates = [candidate for candidate in candidates if str(candidate).startswith('qwen/')]
    fallbacks = []
    seen = {primary}
    for candidate in candidates:
        model = str(candidate or "").strip()
        if model and model not in seen:
            seen.add(model)
            fallbacks.append(model)
    return primary, fallbacks


def _composition_media_fallback_intent(scene_text: Any, media_kind: Any) -> str:
    """Describe a safe, scene-specific, text-free hold when bespoke composition fails."""
    beat = re.sub(r"\s+", " ", str(scene_text or "")).strip()
    if beat:
        beat = re.split(r"(?<=[.!?])\s+", beat, maxsplit=1)[0].strip()
    if len(beat) > 180:
        beat = beat[:177].rstrip()
        beat = re.sub(r"\s+\S*$", "", beat) or beat
        beat += "..."
        beat = beat[:180].rstrip()
    if beat and beat[-1] not in ".!?…":
        beat += "."
    kind = str(media_kind or "verified visual").strip().lower()
    if kind in {"youtube", "clip", "mp4"}:
        kind = "verified footage"
    elif kind in {"image", "photo", "still"}:
        kind = "verified image"
    elif kind not in {"video", "verified visual", "verified footage", "verified image"}:
        kind = "verified visual"
    anchor = f" for this narration beat: {beat} " if beat else " for this narration beat. "
    return f"Hold the {kind} full-frame{anchor}Keep the actual subject clear and add no overlay copy."


_DIRECTOR_TEMPLATE_CATALOG = tuple(
    item for item in list_template_catalog().get("templates", [])
    if item.get("status") == "approved"
)
_DIRECTOR_AUTO_TEMPLATES = tuple(item["id"] for item in _DIRECTOR_TEMPLATE_CATALOG)
_DIRECTOR_CATALOG_BY_ID = {str(item.get("id") or ""): item for item in _DIRECTOR_TEMPLATE_CATALOG}
_DIRECTOR_DISPLAY_FONT_NAMES = ["Archivo Black", "Plus Jakarta Sans"]
_DIRECTOR_BODY_FONT_NAMES = ["Plus Jakarta Sans", "DM Sans"]
_DIRECTOR_FONT_NAMES = sorted(set(_DIRECTOR_DISPLAY_FONT_NAMES + _DIRECTOR_BODY_FONT_NAMES + ["JetBrains Mono"]))
_MAP_MOTION_PROFILES = (
    "world_to_region", "boundary_trace", "city_reveal", "route_trace", "wide_context",
)

_ART_DIRECTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["style", "display_font", "body_font", "accent_color", "visual_motif", "motion_rhythm", "story_arc", "retention_plan"],
    "properties": {
        "style": {"type": "string", "enum": ["editorial", "investigative", "field", "data", "archive", "cinematic"]},
        "display_font": {"type": "string", "enum": _DIRECTOR_DISPLAY_FONT_NAMES},
        "body_font": {"type": "string", "enum": _DIRECTOR_BODY_FONT_NAMES},
        "accent_color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
        "visual_motif": {"type": "string", "maxLength": 120},
        "motion_rhythm": {"type": "string", "maxLength": 220},
        "story_arc": {"type": "string", "maxLength": 360},
        "retention_plan": {"type": "string", "maxLength": 240},
    },
}

_ART_DIRECTION_PROMPT = """Act as the series art director for a sharp, high-retention visual documentary.
Use the visual argument and pacing craft of Vox-style explainers as inspiration,
while creating an original look for this story. Do not imitate a logo or copy a
specific video. Read the complete script and return:
- one concise story_arc: the central question, stakes, explanation, and payoff;
- one retention_plan: where to hook, orient, accelerate, turn, and let the story breathe;
- one distinctive visual_motif tied to the subject, not generic decoration;
- a motion_rhythm that names different, narration-synced motion verbs and where intensity rises.

Make the visual idea memorable: build maps, timelines, comparisons, evidence marks,
cutouts, charts, or cause-and-effect systems from real script payloads. For an
explanatory beat, prefer a bespoke animated explanation when it will clarify the
story more than another static stock-image hold. Keep media-led scenes alive with
purposeful reframing or a meaningful reveal. In the opening 15–20 seconds specify
an information reveal or supported editorial cut every 2–3 seconds, with one dominant
element at a time. Name the opening's visual argument; reject a footage-plus-small-card
opening when a grounded mechanism, comparison, map, or large type can explain it.
After the hook, aim for a noticeable visual change roughly every 3–5 seconds when scene length and narration allow, then settle long
enough to read. Use quiet holds as deliberate contrast around the hook, emotional
beats, and payoff. Never add movement, text, data, or evidence just to fill time.

Avoid generic rounded boxes, dashboard panels, fake labels, decorative data,
repeated preset styling, constant camera drift, and motion that competes with
faces or source evidence. Return only the requested structured fields."""

_COPY_VERIFICATION_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "maxItems": 8, "items": {
        "type": "object", "additionalProperties": False,
        "required": ["scene_index", "supported", "meaning_preserved"],
        "properties": {
            "scene_index": {"type": "integer", "minimum": 0},
            "supported": {"type": "boolean"},
            "meaning_preserved": {"type": "boolean"},
        },
    }}},
}


def _decode_director_json(raw: Any) -> dict:
    if isinstance(raw, Mapping):
        value = dict(raw)
    else:
        cleaned = re.sub(r"^```(?:json)?|```$", "", str(raw or "").strip(), flags=re.MULTILINE).strip()
        value = json.loads(cleaned)
    if isinstance(value, list) and all(isinstance(row, Mapping) for row in value):
        value = {"scenes": value}
    if isinstance(value, dict) and 'scene_index' in value and 'scenes' not in value and 'decisions' not in value:
        value = {"scenes": [value]}
    if not isinstance(value, dict):
        raise ValueError("Director response must be a JSON object")
    return value


def _copy_has_critical_anchors(source: str, display: str) -> bool:
    source = str(source or "")
    display_folded = str(display or "").casefold()
    anchors = re.findall(r"(?<!\w)(?:\$\s*)?\d[\d,]*(?:\.\d+)?\s*(?:%|percent|million|billion|thousand|km|miles|years?)?", source, re.I)
    anchors.extend(re.findall(r"\b(?:not|never|no|without|neither|nor|didn't|doesn't|wasn't|weren't|cannot|can't)\b", source, re.I))
    proper_names = re.findall(r"\b(?:[A-Z][a-z]+(?:\s+|$)){1,3}", source)
    anchors.extend(name.strip() for name in proper_names if name.strip().casefold() not in {"the", "in", "on", "at", "a", "an", "when", "this", "that"})
    return all(anchor.casefold() in display_folded for anchor in anchors)


def _copy_is_complete(text: str) -> bool:
    words = re.findall(r"[\w’'-]+", str(text or "").strip())
    if not words or len(text) > 64 or len(words) > 8:
        return False
    unfinished = {"a", "an", "the", "and", "or", "but", "to", "of", "for", "with", "by", "was", "were", "is", "are", "not", "because", "that", "which", "who", "when", "from", "into"}
    return words[-1].casefold().strip("'’-") not in unfinished


def _source_span_for_phrase(scene_text: str, phrase: str) -> str:
    """Return the smallest complete narrated span that grounds a phrase."""
    source = re.sub(r"\s+", " ", str(scene_text or "")).strip()
    needle = re.sub(r"\s+", " ", str(phrase or "")).strip()
    if not source or not needle:
        return ""
    match = re.search(re.escape(needle), source, re.I)
    if not match:
        return ""
    start = max(source.rfind(".", 0, match.start()), source.rfind("!", 0, match.start()), source.rfind("?", 0, match.start())) + 1
    ends = [value for value in (source.find(".", match.end()), source.find("!", match.end()), source.find("?", match.end())) if value >= 0]
    end = min(ends) + 1 if ends else len(source)
    return source[start:end].strip(" \t\r\n,;:")


def _compact_grounded_highlight(scene_text: str, preferred: str = "", max_words: int = 6) -> str:
    """Choose an occasional exact, source-grounded documentary highlight."""
    source = re.sub(r"\s+", " ", str(scene_text or "")).strip()
    if not source:
        return ""
    max_words = max(2, min(8, int(max_words or 6)))
    candidates: List[str] = []
    requested = re.sub(r"\s+", " ", str(preferred or "")).strip(" \t\r\n.,;:")
    requested_words = re.findall(r"[\w’-]+", requested)
    if requested and len(requested_words) > 1 and len(requested_words) <= max_words and len(requested) <= 64 and re.search(re.escape(requested), source, re.I):
        candidates.append(requested)
    for pattern in (
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:,\s*\d{4})?\b",
        r"\b\d{1,2}:\d{2}\s*(?:in the morning|at night)?\b",
        r"(?<!\w)(?:[$€£]\s*)?\d[\d,.]*(?:\s*(?:million|billion|thousand|percent|%|km|miles|years|hours))?\b",
    ):
        candidates.extend(match.group(0) for match in re.finditer(pattern, source, re.I))
    # Preserve visible proper-name runs such as "Panama Canal" and
    # "RMS Titanic" before the gazetteer returns a shorter nested name.
    candidates.extend(
        match.group(0)
        for match in re.finditer(r"\b[A-Z][A-Za-z0-9'-]*(?:\s+(?:of|the|and)\s+|\s+)[A-Z][A-Za-z0-9'-]*(?:\s+[A-Z][A-Za-z0-9'-]*)?\b", source)
    )
    if requested and len(requested_words) == 1 and len(requested) <= 64 and re.search(re.escape(requested), source, re.I):
        candidates.append(requested)
    places, _feature_ids = _narrated_local_geography(source)
    candidates.extend(places)
    candidates.extend(quoted for quoted in re.findall(r"[\"“‘']([^\"”’']{3,80})[\"”’']", source))
    if not candidates:
        selected = select_grounded_highlight(source, max_words=max_words)
        if selected:
            candidates.append(selected)
    stop = {"a", "an", "the", "and", "or", "but", "to", "of", "for", "with", "by", "was", "were", "is", "are", "not", "because", "that", "which", "who", "when", "from", "into"}
    for candidate in candidates:
        phrase = re.sub(r"\s+", " ", str(candidate or "")).strip(" \t\r\n.,;:")
        words = re.findall(r"[\w’'-]+", phrase)
        if not phrase or len(words) > max_words or len(phrase) > 64 or words[-1].casefold().strip("'’- ") in stop:
            continue
        if not re.search(re.escape(phrase), source, re.I):
            continue
        meaningful = [word for word in words if len(word) >= 5 and word.casefold() not in stop]
        proper = any(word[:1].isupper() for word in words)
        numeric = bool(re.search(r"\d|%|[$€£]", phrase))
        weak_terms = {"process", "moved", "move", "moves", "moving", "links", "link", "shows", "show", "turns", "turned", "allowing", "allowed", "used", "uses"}
        if not proper and not numeric and meaningful and all(word.casefold() in weak_terms for word in meaningful):
            continue
        if len(words) == 1 and not proper and not numeric and not meaningful:
            continue
        return phrase
    return ""


def _valid_director_copy(scene_text: str, display: str, source_span: str) -> bool:
    display = re.sub(r"\s+", " ", str(display or "")).strip()
    source_span = re.sub(r"\s+", " ", str(source_span or "")).strip()
    if not display:
        return not source_span
    if not source_span or not re.search(re.escape(source_span), str(scene_text or ""), re.I):
        return False
    if not _copy_is_complete(display):
        return False
    original_words = re.findall(r"[\w’'-]+", display)
    words = [word.casefold().strip("'’-") for word in original_words]
    fragment_edges = {
        "a", "an", "the", "every", "each", "this", "that", "closing", "opening",
        "using", "making", "moving", "rising", "falling", "turning", "becoming",
        "becomes", "links", "linking", "shows", "showing", "allows", "allowing",
    }
    weak_terminal_adjectives = {
        "massive", "dangerous", "global", "same", "different", "important", "major",
        "final", "first", "last", "new", "old", "large", "small",
        "bring", "brings", "bringing", "provide", "provides", "providing",
        "offer", "offers", "offering", "cause", "causes", "causing",
        "give", "gives", "giving", "deliver", "delivers", "delivering",
        "help", "helps", "helping", "require", "requires", "requiring",
    }
    starts_named_title = words[0] == "the" and any(word[:1].isupper() for word in original_words[1:])
    if len(words) >= 2 and ((words[0] in fragment_edges and not starts_named_title) or words[-1] in weak_terminal_adjectives):
        return False
    weak = {"a", "an", "the", "and", "or", "but", "to", "of", "for", "with", "by", "was", "were", "is", "are", "links", "link", "shows", "show", "makes", "made", "moved", "move", "moves", "moving", "process", "began", "begins", "became", "becomes", "used", "uses", "called", "this", "that"}
    if words and all(word in weak for word in words):
        return False
    if len(words) <= 2 and any(word in weak for word in words) and not re.search(r"\d|%|[$€£]", display) and not any(word[:1].isupper() for word in re.findall(r"[\w’'-]+", display)):
        return False
    # A highlight must be an exact narrated phrase. The supporting source
    # span may remain a full sentence for auditability, but the displayed copy
    # is intentionally short and never a rewritten headline.
    return bool(re.search(re.escape(display), source_span, re.I) or re.search(re.escape(display), str(scene_text or ""), re.I))


def _apply_opportunity_highlights(parsed, proposals, scene_texts):
    """Apply only exact LLM-selected copy; keep a cooldown and native variants."""
    by_index = {row.get('scene_index'): row for row in parsed if isinstance(row, dict)}
    variants = set(SCENE_TYPE_VARIANTS.get(VisualSceneType.KINETIC_TEXT.value) or ())
    accepted = []
    for proposal in proposals if isinstance(proposals, list) else []:
        if not isinstance(proposal, Mapping):
            continue
        index = proposal.get('scene_index')
        if not isinstance(index, int) or isinstance(index, bool) or index not in by_index or not 0 <= index < len(scene_texts):
            continue
        display, span = str(proposal.get('display_text') or ''), str(proposal.get('source_span') or '')
        if (not _valid_director_copy(scene_texts[index], display, span)
                or proposal.get('variant') not in variants
                or any(abs(index - prior) <= 1 for prior in accepted)):
            continue
        row = by_index[index]
        if row.get('frame_mode') not in {'media_only', 'kinetic'}:
            continue
        row.update(type=VisualSceneType.KINETIC_TEXT.value, variant=proposal['variant'],
                   frame_mode='kinetic', visual_role='media_overlay', template_id='keyword-reveal',
                   template_recipe=None, overlay_policy='single_short_highlight',
                   display_text=display, source_span=span, emphasis_spans=[display],
                   placement=proposal.get('placement') or 'center',
                   visual_intent='Keep verified media visible; reveal the approved exact highlight: '+display)
        accepted.append(index)
    return accepted


def _director_overlay_budget(total: int, config: Optional[Mapping[str, Any]] = None) -> int:
    """Set a film-wide ceiling for short LLM-selected highlights."""
    config = dict(config or {})
    try:
        ratio = float(config.get("director_overlay_ratio") or os.environ.get("VIDZEO_DIRECTOR_OVERLAY_RATIO", "0.50"))
    except (TypeError, ValueError):
        ratio = 0.50
    ratio = max(0.18, min(0.60, ratio))
    total = max(0, int(total or 0))
    if not total:
        return 0
    if total <= 3:
        return 1
    return max(1, min(total, int(round(total * ratio))))


def _director_story_map(scene_texts: Sequence[str], max_chars: int = 1400, max_anchors: int = 5) -> str:
    """Give each small LLM scene batch stable beginning-to-payoff context."""
    texts = [re.sub(r"\s+", " ", str(item or "")).strip() for item in scene_texts]
    if not texts:
        return "No script beats were supplied."
    anchor_count = min(max(1, int(max_anchors or 5)), len(texts))
    indices = sorted({round(index * (len(texts) - 1) / max(1, anchor_count - 1)) for index in range(anchor_count)})
    labels_by_count = {
        1: ("ONLY SCENE",),
        2: ("OPENING / HOOK", "PAYOFF / ENDING"),
        3: ("OPENING / HOOK", "CENTRAL TURN", "PAYOFF / ENDING"),
        4: ("OPENING / HOOK", "EARLY SETUP", "LATE TURN", "PAYOFF / ENDING"),
        5: ("OPENING / HOOK", "EARLY SETUP", "CENTRAL EXPLANATION", "LATE TURN", "PAYOFF / ENDING"),
    }
    labels = labels_by_count.get(len(indices))
    rows = []
    for position, index in enumerate(indices):
        label = labels[min(position, len(labels) - 1)] if labels else (
            "OPENING / HOOK" if position == 0 else
            "PAYOFF / ENDING" if position == len(indices) - 1 else
            f"FILM SECTION {position + 1}/{len(indices)}"
        )
        text = texts[index]
        if len(text) > 210:
            text = text[:207].rsplit(" ", 1)[0] + "..."
        rows.append(f"{label} — scene {index}: {text}")
    return "\n".join(rows)[:max(240, int(max_chars or 1400))]


_MOTION_CUE_ACTIONS = {
    "map": {"trace", "pin", "reveal", "hold"},
    "data": {"count", "trace", "wipe", "reveal", "hold"},
    "steps": {"trace", "wipe", "reveal", "hold"},
    "comparison": {"split", "wipe", "reveal", "hold"},
    "person": {"reveal", "hold"},
    "source": {"trace", "wipe", "reveal", "hold"},
    "image": {"pan", "wipe", "reveal", "hold"},
    "copy": {"wipe", "reveal", "hold"},
}


def _validated_motion_cues(raw_cues, narration, scene_start, duration, word_timings=None):
    """Keep only supported, narrated cues and place them on the local audio clock."""
    if not isinstance(raw_cues, list):
        return []
    narration = str(narration or "")
    words = []
    for item in word_timings or []:
        if isinstance(item, Mapping):
            value = item
        elif hasattr(item, "word"):
            value = {"word": getattr(item, "word", ""), "start": getattr(item, "start", 0)}
        else:
            continue
        try:
            start = float(value.get("start") or 0)
        except (TypeError, ValueError):
            continue
        if scene_start - 0.2 <= start <= scene_start + duration + 0.2:
            token = re.sub(r"[^\w]+", "", str(value.get("word") or "")).casefold()
            if token:
                words.append((token, start))
    cues = []
    for raw in raw_cues[:3]:
        if not isinstance(raw, Mapping):
            continue
        element = str(raw.get("element") or "").strip().lower()
        action = str(raw.get("action") or "").strip().lower()
        anchor = re.sub(r"\s+", " ", str(raw.get("anchor_text") or "")).strip()
        if action not in _MOTION_CUE_ACTIONS.get(element, set()):
            continue
        match = re.search(re.escape(anchor), narration, re.I) if anchor else None
        if anchor and not match:
            continue
        if element == "person":
            name_parts = [re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ'-]", "", part) for part in anchor.split()]
            discourse_leads = {"but", "and", "then", "however", "earlier", "later", "moments", "the", "at", "after", "before"}
            if (len(name_parts) < 2 or name_parts[0].casefold() in discourse_leads
                    or any(not part or not part[0].isupper() for part in name_parts)):
                continue
        local = 0.0
        if match:
            target = [re.sub(r"[^\w]+", "", token).casefold() for token in anchor.split()]
            target = [token for token in target if token]
            aligned = next((start for index, (_, start) in enumerate(words)
                            if target and [word for word, _ in words[index:index + len(target)]] == target), None)
            local = (aligned - scene_start) if aligned is not None else duration * match.start() / max(1, len(narration))
        local = max(0.0, min(max(0.0, duration - 0.2), local))
        cues.append({
            "element": element,
            "action": action,
            "anchor_text": match.group(0) if match else "",
            "time_local": round(local, 3),
            "duration_seconds": 0.85 if action == "trace" else (0.65 if action in {"split", "pan", "count"} else 0.42),
        })
    return sorted(cues, key=lambda cue: cue["time_local"])


def _explicit_comparison_cue_candidates(narration: str) -> List[dict]:
    """Recover only plainly narrated named matchups when the LLM omits a cue.

    The anchor is an exact substring from narration and the renderer derives
    no score, logo, venue, or other fact from it. This is a narrow coverage
    repair for explicit pairings, not a replacement copy/highlight generator.
    """
    text = str(narration or "")
    entity = r"(?:the\s+)?[A-Z][\w&.'’–-]*(?:\s+(?:[A-Z][\w&.'’–-]*|of|the|and)){0,3}"
    connector = r"(?:will\s+(?:face|meet|battle)|take\s+on|takes\s+on|face|faces|battle|battles|meet|meets|play|plays|versus|vs\.?)"
    pair_pattern = re.compile(rf"{entity}\s+{connector}\s+{entity}")
    candidates = []
    seen = set()
    for match in pair_pattern.finditer(text):
        anchor = match.group(0).strip()
        key = re.sub(r"\W+", " ", anchor).casefold().strip()
        if key in seen:
            continue
        seen.add(key)
        candidates.append({
            "element": "comparison",
            "action": "split" if not candidates else "reveal",
            "anchor_text": anchor,
        })
        if len(candidates) >= 3:
            break
    return candidates


def _director_overlay_score(item: Mapping[str, Any], index: int) -> float:
    """Rank grounded, model-selected cues for a varied visual rhythm."""
    phrase = str(item.get("display_text") or "").strip()
    lowered = phrase.casefold()
    score = float(item.get("editorial_priority") or 0.0) * 2.0
    frame_mode = str(item.get("frame_mode") or "media_only").strip().lower()
    if frame_mode == "chapter":
        score += 0.55
    if frame_mode in {"map", "portrait", "evidence", "data", "quote"}:
        score += 0.25
    if re.search(r"\d|%|[$€£]", phrase):
        score += 0.45
    if any(word[:1].isupper() for word in re.findall(r"[\w’'-]+", phrase)):
        score += 0.20
    if any(signal in lowered for signal in (
        "first", "last", "only", "never", "record", "warning", "impact", "surviv",
        "collapse", "changed", "canal", "route", "divide", "horn", "lake",
    )):
        score += 0.22
    score -= max(0, len(re.findall(r"[\w’'-]+", phrase)) - 3) * 0.12
    score -= index * 0.001
    return score


def _enforce_director_overlay_budget(
    parsed: Sequence[Mapping[str, Any]],
    scene_texts: Sequence[str],
    director_config: Optional[Mapping[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> List[dict]:
    """Keep a distributed set of meaningful overlays and clear the rest."""
    normalized = [dict(item) for item in parsed if isinstance(item, Mapping)]
    total = len(scene_texts)
    budget = _director_overlay_budget(total, director_config)
    candidates = []
    for item in normalized:
        index = int(item.get("scene_index", -1))
        if index < 0 or index >= total:
            continue
        policy = str(item.get("overlay_policy") or "none").strip().lower()
        display = str(item.get("display_text") or "").strip()
        source_span = str(item.get("source_span") or "").strip()
        if policy != "single_short_highlight" or not display:
            continue
        if not _valid_director_copy(str(scene_texts[index]), display, source_span):
            # A model-selected highlight is either grounded and shown, or
            # rejected. Replacing it here with a heuristic phrase would make
            # a supposedly LLM-directed emphasis feel generic again.
            item["display_text"], item["source_span"], item["emphasis_spans"] = "", "", []
            item["overlay_policy"] = "none"
            continue
        candidates.append((index, _director_overlay_score(item, index), item))

    chosen: List[int] = []
    opening = next(
        (row for row in candidates if row[0] == 0 and str(row[2].get("frame_mode") or "").lower() == "chapter"),
        None,
    )
    if opening and budget:
        chosen.append(0)
    for index, _score, _item in sorted(candidates, key=lambda row: row[1], reverse=True):
        if len(chosen) >= budget:
            break
        if index in chosen or any(abs(index - existing) <= 1 for existing in chosen):
            continue
        chosen.append(index)
    # If the strongest remaining candidates are adjacent, leave the later
    # beat media-only instead of filling the quota and recreating a run of
    # headline cards. The budget is a ceiling, not a minimum.
    chosen_set = set(chosen)
    for item in normalized:
        index = int(item.get("scene_index", -1))
        if index not in chosen_set:
            item["display_text"] = ""
            item["source_span"] = ""
            item["emphasis_spans"] = []
            item["overlay_policy"] = "none"
        else:
            item["overlay_policy"] = "single_short_highlight"
    if metadata is not None:
        metadata["overlay_budget"] = budget
        metadata["overlay_scene_indices"] = sorted(chosen_set)
        metadata["overlay_policy"] = "distributed_director_selection"
        metadata["highlight_source"] = "llm_director"
        metadata["highlight_count"] = len(chosen_set)
    return normalized


def _safe_director_placement(placement: str, asset: Optional[Mapping[str, Any]], frame_mode: str) -> str:
    """Keep a short cue out of the detected subject or a full-frame map."""
    placement = str(placement or "none").strip().lower()
    if frame_mode == "map" or placement == "none":
        return "none"
    if placement not in {"top_left", "top_right", "bottom_left", "bottom_right", "center"}:
        placement = "bottom_left"
    if placement == "center":
        return placement
    asset = asset if isinstance(asset, Mapping) else {}
    visual = asset.get("visual_decision") if isinstance(asset.get("visual_decision"), Mapping) else {}
    safe_regions = visual.get("safe_text_regions") if isinstance(visual.get("safe_text_regions"), list) else []
    subject = visual.get("subject_box") if isinstance(visual.get("subject_box"), Mapping) else {}
    subject_box = (
        float(subject.get("x") or 0.0), float(subject.get("y") or 0.0),
        float(subject.get("width") or 0.0), float(subject.get("height") or 0.0),
    )
    boxes = {
        "top_left": (0.04, 0.05, 0.34, 0.28),
        "top_right": (0.62, 0.05, 0.34, 0.28),
        "bottom_left": (0.04, 0.70, 0.34, 0.25),
        "bottom_right": (0.62, 0.70, 0.34, 0.25),
    }

    def overlap(a, b):
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        return max(0.0, min(ax + aw, bx + bw) - max(ax, bx)) * max(0.0, min(ay + ah, by + bh) - max(ay, by))

    if safe_regions:
        for candidate, box in boxes.items():
            if any(overlap(box, (float(region.get("x") or 0), float(region.get("y") or 0), float(region.get("width") or 0), float(region.get("height") or 0))) > 0.01 for region in safe_regions if isinstance(region, Mapping)):
                return candidate
    if subject_box[2] > 0 and subject_box[3] > 0 and overlap(boxes.get(placement, boxes["bottom_left"]), subject_box) > 0.035:
        return min(boxes, key=lambda key: overlap(boxes[key], subject_box))
    return placement


def _sync_scene_display_props(scene: SceneDecision) -> None:
    """Keep Remotion component props aligned with the compact copy contract."""
    narration = str(scene.narration or "")
    proposed = str(scene.display_text or "").strip()
    source_span = str(scene.source_span or "").strip()
    text = dict(scene.text or {})
    if "overlay_policy" in text and str(text.get("overlay_policy") or "none").lower() != "single_short_highlight":
        proposed = ""
        source_span = ""
    llm_selected_copy = (
        str(text.get("overlay_policy") or "").strip().lower() == "single_short_highlight"
        and bool(proposed)
    )
    local_candidate = _compact_grounded_highlight(narration)
    proposed_words = re.findall(r"[\w’'-]+", proposed)
    if not llm_selected_copy and local_candidate and proposed and (
        len(proposed_words) > 3
        or proposed_words[:1] and proposed_words[0].casefold() in {"a", "an", "the"}
        or any(word.casefold() in {"for", "with", "through", "because", "that", "from"} for word in proposed_words)
    ) and len(re.findall(r"[\w’'-]+", local_candidate)) <= 3:
        proposed = local_candidate
        source_span = _source_span_for_phrase(narration, proposed)
    if proposed and not _valid_director_copy(narration, proposed, source_span):
        if llm_selected_copy:
            proposed, source_span = "", ""
        else:
            proposed = _compact_grounded_highlight(narration, proposed)
            source_span = _source_span_for_phrase(narration, proposed) if proposed else ""
    if proposed and not _valid_director_copy(narration, proposed, source_span):
        proposed, source_span = "", ""
    scene.display_text = proposed
    scene.source_span = source_span
    props = dict(scene.primary_visual.props or {})
    emphasis_spans = _normalized_emphasis_spans(proposed, scene.emphasis_spans) if proposed else []
    if emphasis_spans:
        props["emphasis_spans"] = [dict(span) for span in emphasis_spans]
        props["emphasis_phrase"] = proposed[emphasis_spans[0]["start"]:emphasis_spans[0]["end"]]
    else:
        props.pop("emphasis_spans", None)
        props.pop("emphasis_phrase", None)
    if proposed:
        text["display_text"] = proposed
        text["primary"] = proposed
        props["display_text"] = proposed
        props["_display_text"] = proposed
        visual_type = scene.primary_visual.type
        if visual_type == VisualSceneType.KINETIC_TEXT.value:
            props["text"] = proposed
            props["lines"] = [proposed]
        elif visual_type == VisualSceneType.CHAPTER_TITLE.value:
            props["title"] = proposed
            props["subtitle"] = ""
        elif visual_type == VisualSceneType.HEADLINE.value:
            props["headline"] = proposed
            props["title"] = proposed
            props["subdeck"] = ""
        elif visual_type == VisualSceneType.DOCUMENT_HIGHLIGHT.value:
            capture = props.get("source_capture") if isinstance(props.get("source_capture"), Mapping) else {}
            if not capture.get("exact_excerpt"):
                props["title"] = proposed
                props["highlight"] = proposed
                props["paragraph"] = proposed
                props["text"] = proposed
        elif visual_type == VisualSceneType.TIMELINE.value:
            props["title"] = props.get("title") or proposed
            props["caption"] = ""
        elif visual_type == VisualSceneType.DIAGRAM.value:
            # The source-grounded mechanism remains visible beneath its brief
            # model-selected phrase; copy is rendered in a separate window.
            pass
    else:
        text.pop("display_text", None)
        text["primary"] = ""
        props.pop("display_text", None)
        props.pop("_display_text", None)
        if scene.primary_visual.type == VisualSceneType.KINETIC_TEXT.value:
            # No approved copy means no text treatment. Never promote the
            # narration or a locally guessed phrase into an on-screen title.
            props.pop("text", None)
            props.pop("lines", None)
            props.pop("kicker", None)
            props.pop("emphasis", None)
        elif scene.primary_visual.type == VisualSceneType.CHAPTER_TITLE.value:
            props.pop("title", None)
            props.pop("headline", None)
            props.pop("subtitle", None)
        elif scene.primary_visual.type == VisualSceneType.HEADLINE.value:
            props.pop("headline", None)
            props.pop("title", None)
            props.pop("subdeck", None)
        elif scene.primary_visual.type == VisualSceneType.TIMELINE.value:
            props.pop("title", None)
            props["caption"] = ""
        elif scene.primary_visual.type == VisualSceneType.DIAGRAM.value:
            props["steps"] = [
                dict(step)
                for step in (props.get("steps", []) if isinstance(props.get("steps"), list) else [])
                if isinstance(step, Mapping) and str(step.get("desc") or step.get("description") or step.get("label") or step.get("title") or "").strip()
            ][:4]
        elif scene.primary_visual.type == VisualSceneType.DOCUMENT_HIGHLIGHT.value:
            capture = props.get("source_capture") if isinstance(props.get("source_capture"), Mapping) else {}
            if not capture.get("exact_excerpt"):
                props["title"] = ""
                props["highlight"] = ""
                props["paragraph"] = ""
                props["text"] = ""
    scene.text = text
    scene.primary_visual.props = props
    scene.emphasis_spans = emphasis_spans


def _normalized_emphasis_spans(display_text: str, spans: Any) -> List[Dict[str, int]]:
    """Keep one exact, in-bounds accent span that remains visually tight."""
    output = []
    total_words = len(re.findall(r"[\w’'-]+", display_text, re.UNICODE))
    for span in spans if isinstance(spans, list) else []:
        if not isinstance(span, Mapping):
            continue
        try:
            start, end = int(span.get("start")), int(span.get("end"))
        except (TypeError, ValueError):
            continue
        if 0 <= start < end <= len(display_text) and display_text[start:end].strip():
            if (start > 0 and display_text[start-1].isalnum() and display_text[start].isalnum()
                    or end < len(display_text) and display_text[end-1].isalnum() and display_text[end].isalnum()):
                continue
            selected_words = len(re.findall(r"[\w’'-]+", display_text[start:end], re.UNICODE))
            numeric_phrase = bool(re.search(
                r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
                r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
                r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
                r"eighty|ninety|hundred|thousand|million|billion|trillion|"
                r"dollars?|euros?|pounds?|percent|percentage)\b|\d",
                display_text[start:end], re.IGNORECASE,
            ))
            whole_display = start == 0 and end == len(display_text)
            if selected_words > 3 or (whole_display and selected_words > 2 and not numeric_phrase):
                continue
            output.append({"start": start, "end": end})
    return output[:1]


def _recipe_is_grounded(recipe: Mapping[str, Any], scene_text: str) -> bool:
    """Reject custom recipe copy, data, or assets that cannot be grounded locally."""
    from pathlib import Path

    source = str(scene_text or "")
    source_numbers = re.findall(r"(?<!\w)(?:\$\s*)?\d[\d,]*(?:\.\d+)?%?", source)
    normalized_numbers = set()
    for value in source_numbers:
        try:
            normalized_numbers.add(float(value.replace(",", "").replace("$", "").replace("%", "")))
        except ValueError:
            continue
    source_copy = re.sub(r"[^\w%$]+", " ", source, flags=re.UNICODE).casefold().strip()
    project_root = Path(__file__).resolve().parents[3]
    for layer in recipe.get("layers", []) if isinstance(recipe.get("layers"), list) else []:
        if not isinstance(layer, Mapping):
            return False
        layer_text = str(layer.get("text") or "").strip()
        if layer_text:
            normalized_layer_text = re.sub(r"[^\w%$]+", " ", layer_text, flags=re.UNICODE).casefold().strip()
            if normalized_layer_text not in source_copy:
                return False
        if str(layer.get("kind") or "").lower() == "image" and str(layer.get("asset_id") or "").strip():
            asset_id = str(layer.get("asset_id") or "").strip().replace("\\", "/")
            asset_path = (project_root / "public" / asset_id).resolve()
            public_root = (project_root / "public").resolve()
            if not asset_path.is_relative_to(public_root) or not asset_path.is_file():
                return False
        for row in layer.get("data", []) if isinstance(layer.get("data"), list) else []:
            if not isinstance(row, Mapping):
                return False
            label = str(row.get("label") or "").strip()
            normalized_label = re.sub(r"[^\w%$]+", " ", label, flags=re.UNICODE).casefold().strip()
            if normalized_label not in source_copy:
                return False
            if row.get("value") is not None:
                try:
                    value = float(row.get("value"))
                except (TypeError, ValueError):
                    return False
                if not any(abs(value - narrated) < 1e-9 for narrated in normalized_numbers):
                    return False
        if str(layer.get("kind") or "").lower() == "diagram":
            declared = {
                str(row.get("label") or "").strip()
                for row in layer.get("data", []) if isinstance(row, Mapping)
            }
            for link in layer.get("links", []) if isinstance(layer.get("links"), list) else []:
                if not isinstance(link, Mapping) or link.get("from") not in declared or link.get("to") not in declared:
                    return False
                evidence = str(link.get("evidence") or "").strip()
                normalized_evidence = re.sub(r"[^\w%$]+", " ", evidence, flags=re.UNICODE).casefold().strip()
                if not normalized_evidence or normalized_evidence not in source_copy:
                    return False
                if not re.search(r"\b(?:be|am|is|are|was|were|being|been|cause|causes|caused|drive|drives|driven|lead|leads|led|carry|carries|carried|shift|shifts|shifted|form|forms|formed|create|creates|created|trigger|triggers|triggered|flow|flows|connect|connects|connected|before|after|then|follow|follows|followed|through|from|to|move|moves|moved|rise|rises|rose|fall|falls|fell|increase|increases|decrease|decreases|transfer|transfers|transferred|change|changes|changed|become|becomes|became|produce|produces|produced|result|results|resulted|enable|enables|enabled|warm|warms|cool|cools|block|blocks|blocked|sweep|sweeps|orbit|orbits|occlude|occludes|reflect|reflects|absorb|absorbs|rotate|rotates|protect|protects|press|presses|push|pushes|pull|pulls)\b", normalized_evidence):
                    return False
                for endpoint in (link.get("from"), link.get("to")):
                    normalized_endpoint = re.sub(r"[^\w%$]+", " ", str(endpoint or ""), flags=re.UNICODE).casefold().strip()
                    if not normalized_endpoint or normalized_endpoint not in normalized_evidence:
                        return False
    return True


def _normalize_conflicting_frame_mode(decision: dict, narration: str) -> bool:
    """Route actorless diagrams to media before the optional composition call."""
    if decision.get('frame_mode') != 'diagram' or len(validated_diagram_nodes(decision.get('diagram_nodes'), narration)) >= 2:
        return False
    normalized = re.sub(r'[\s_-]', '', str(decision.get('type') or '')).casefold()
    modes = {'smartimage':'media_only', 'smartvideo':'media_only', 'kinetictext':'kinetic',
             'chaptertitle':'chapter', 'personprofile':'portrait', 'documenthighlight':'evidence',
             'statcard':'data', 'chart':'data', 'comparison':'data', 'timeline':'data',
             'quotecard':'quote', 'collage':'media_only'}
    # A Diagram is itself not a media source. If its narration does not ground
    # two distinct nodes, keep its subject-specific real media instead of
    # asking the composition model for a recipe that cannot be compiled.
    decision['frame_mode'] = modes.get(normalized, 'media_only')
    decision['template_recipe'] = None
    decision['_frame_mode_normalized'] = (
        'native_component_without_diagram_actors' if normalized in modes
        else 'diagram_without_grounded_actor_pair_to_media'
    )
    return True


def _diagram_recipe_quality_error(recipe: Mapping[str, Any], duration: float, fps: int) -> str:
    """Apply the same visible-layout contract to first-pass and repaired recipes.

    This is a structural guard, not a substitute for rendered-frame criticism.
    Count occupied space once, exclude background plates and reserve copy space.
    """
    layers = recipe.get('layers') or []
    groups = {row.get('id'): row for row in layers if row.get('kind') == 'group' and row.get('id')}

    def bounds(row):
        x, y, w, h = (float(row.get(key, 0)) for key in ('x', 'y', 'width', 'height'))
        parent = row.get('parent_id')
        visited = set()
        while parent in groups and parent not in visited:
            visited.add(parent)
            group = groups[parent]
            gx, gy, gw, gh = (float(group.get(key, 0)) for key in ('x', 'y', 'width', 'height'))
            x, y, w, h = gx+x*gw/100, gy+y*gh/100, w*gw/100, h*gh/100
            parent = group.get('parent_id')
        return x, y, w, h

    objects = [row for row in layers if row.get('kind') in {'sphere', 'circle', 'cone', 'rect', 'image', 'path'}
               and not (row.get('kind') == 'rect' and float(row.get('width', 0)) >= 95 and float(row.get('height', 0)) >= 95)]
    actor_boxes = {}
    for row in objects:
        actor = row.get('semantic_actor')
        if not actor:continue
        x,y,w,h=bounds(row)
        previous=actor_boxes.get(actor,(x,y,x+w,y+h))
        actor_boxes[actor]=(min(x,previous[0]),min(y,previous[1]),max(x+w,previous[2]),max(y+h,previous[3]))
    linked_actor_pairs = set()
    for layer in layers:
        if not isinstance(layer, Mapping) or layer.get('kind') != 'diagram':
            continue
        for link in layer.get('links') or []:
            if not isinstance(link, Mapping):
                continue
            source, target = str(link.get('from') or '').casefold(), str(link.get('to') or '').casefold()
            if source and target and str(link.get('evidence') or '').strip():
                linked_actor_pairs.add(frozenset((source, target)))
    actor_items=list(actor_boxes.items())
    for i,(left_actor,left) in enumerate(actor_items):
        for right_actor,right in actor_items[i+1:]:
            overlap=max(0,min(left[2],right[2])-max(left[0],right[0]))*max(0,min(left[3],right[3])-max(left[1],right[1]))
            area=min((left[2]-left[0])*(left[3]-left[1]),(right[2]-right[0])*(right[3]-right[1]))
            # Contact is often the essential visual fact in a physical
            # mechanism (a shadow crossing a planet, an object striking a
            # surface). Permit it only when a semantic diagram layer names
            # both actors and records a narration-grounded link; otherwise
            # keep distinct actors separated to avoid accidental collisions.
            linked = frozenset((str(left_actor).casefold(), str(right_actor).casefold())) in linked_actor_pairs
            if area and overlap/area>.18 and not linked:
                return 'composition_distinct_actors_overlap'
    # Raster union prevents stacked copies or one large background from passing
    # a sum-of-areas guard. Paths count their contour's actual bounding extent.
    occupied = set()
    for row in objects:
        x, y, w, h = bounds(row)
        if row.get('kind') == 'path':
            points = row.get('points') or []
            if len(points) < 3:
                continue
            px, py = [float(p['x']) for p in points], [float(p['y']) for p in points]
            x, y, w, h = x+w*min(px)/100, y+h*min(py)/100, w*(max(px)-min(px))/100, h*(max(py)-min(py))/100
        if x < 0 or y < 0 or x+w > 100.01 or y+h > 100.01:
            return 'composition_outside_canvas'
        if y+h > 77:
            return 'composition_intrudes_caption_safe_area'
        if float(row.get('start_frame', 0)) > max(0, duration*fps - .65*fps):
            return 'composition_has_no_readable_hold'
        for cx in range(100):
            for cy in range(75):
                if x <= cx+.5 <= x+w and y <= cy+.5 <= y+h:
                    if row.get('kind') in {'sphere', 'circle'}:
                        # The renderer makes discs circular in physical pixels.
                        dw = min(w, h*9/16)
                        dh = min(h, w*16/9)
                        if dw <= 0 or dh <= 0 or ((cx+.5-(x+w/2))/(dw/2))**2 + ((cy+.5-(y+h/2))/(dh/2))**2 > 1:
                            continue
                    occupied.add((cx, cy))
    if len(occupied) < 1000:
        return 'Illustration is too sparse: use substantial subject-specific objects or verified imagery'

    label_boxes = []
    for layer in layers:
        if layer.get('kind') != 'diagram':
            continue
        for node in layer.get('data') or []:
            if node.get('x') is None or node.get('label_y') is None:
                continue
            label = str(node.get('label') or '')
            if re.search(r'\b(?:player|goalkeeper|striker|witness|person|people|woman|man|child|suspect|octopus|heart|brain|engine|ship|aircraft)\b', label, re.I):
                actor_x = float(node.get('x', 0))
                actor_y = float(node.get('y', node.get('label_y', 0)))
                circular_actors = []
                meaningful_actors = []
                for obj in objects:
                    ox, oy, ow, oh = bounds(obj)
                    if ox <= actor_x <= ox+ow and oy <= actor_y <= oy+oh:
                        (circular_actors if obj.get('kind') in {'circle', 'sphere'} else meaningful_actors).append(obj)
                if circular_actors and not meaningful_actors:
                    return 'composition_uses_generic_circle_for_subject'
            # Conservative 1080p label estimate; reject intersecting labels.
            width = min(42, max(9, len(label)*1.12))
            x, y = float(node['x']), float(node['label_y'])
            box = (x-width/2, y-2.1, x+width/2, y+2.1)
            if box[0] < 2 or box[2] > 98 or box[1] < 2 or box[3] > 75:
                return 'composition_label_outside_safe_area'
            if any(box[0] < old[2] and box[2] > old[0] and box[1] < old[3] and box[3] > old[1] for old in label_boxes):
                return 'composition_labels_collide'
            label_boxes.append(box)
    return ''


def _compile_designed_diagram_recipe(
    draft: Mapping[str, Any],
    approved_nodes: Sequence[Mapping[str, Any]],
    approved_links: Sequence[Mapping[str, Any]],
    palette: Mapping[str, str],
    scene_text: str,
    duration: float,
    fps: int,
    id_fallback: str,
    *,
    fit_layout: bool = True,
) -> dict:
    """Compile authored semantic geometry through one initial/repair path.

    Fix safe layout arithmetic locally before spending a correction request.
    Subject identity, meaningful contours and factual links stay untouched.
    """
    if len(approved_nodes) < 2:
        raise ValueError('Composition has no approved actors')
    from .composition_layout import fit_actor_objects
    geometry_missing = any(any(obj.get(key) is None for key in ('x', 'y', 'width', 'height'))
                           for obj in draft.get('objects', []) if isinstance(obj, Mapping))
    if geometry_missing:
        draft = fit_actor_objects(draft, approved_nodes, approved_links)
    recipe_id = re.sub(r'[^a-z0-9-]+', '-', str(draft.get('id') or '').casefold()).strip('-')[:64].rstrip('-') or id_fallback
    labels = {str(node['label']) for node in approved_nodes}
    layers = [{'kind': 'diagram', 'x': 0, 'y': 0, 'width': 100, 'height': 100,
               'color': palette['accent'], 'start_frame': 0,
               'duration_frames': min(1800, max(1, round(duration * fps))),
               'data': [{'label': node['label']} for node in approved_nodes],
               'links': list(approved_links)}]
    for obj in draft.get('objects', []):
        if obj.get('actor_label') not in labels:
            raise ValueError('composition_object_actor_ungrounded')
        layers.append({key: obj[key] for key in ['kind', 'x', 'y', 'width', 'height', 'motion']})
        if obj.get('kind') == 'path':
            layers[-1]['points'] = obj.get('points') or []
            layers[-1]['closed'] = obj.get('closed') is True
            if obj.get('fill_role') and obj['fill_role'] != 'none':
                layers[-1]['fill_color'] = palette.get(obj['fill_role'], palette['primary'])
        layers[-1]['semantic_actor'] = obj['actor_label']
        layers[-1]['width'] = min(float(obj['width']), 84)
        layers[-1]['height'] = min(float(obj['height']), 60)
        layers[-1]['x'] = max(6, min(float(obj['x']), 94 - layers[-1]['width']))
        layers[-1]['y'] = max(10, min(float(obj['y']), 72 - layers[-1]['height']))
        actor = next((row for row in layers[0]['data'] if row['label'] == obj.get('actor_label')), None)
        if actor is not None and 'x' not in actor:
            actor.update({'x': float(obj.get('label_x', layers[-1]['x'] + layers[-1]['width']/2)),
                          'y': layers[-1]['y'] + layers[-1]['height']/2,
                          'label_y': float(obj.get('label_y', max(7, layers[-1]['y']-3))), 'hide_marker': True})
        reveal_seconds = min(.55, max(.1, duration - .65))
        reveal_at = max(0, min(float(obj.get('start_seconds', 0)), duration - reveal_seconds - .65))
        layers[-1].update({'color': palette.get(obj.get('color_role'), palette['accent']),
                           'start_frame': round(reveal_at * fps),
                           'duration_frames': max(1, round(reveal_seconds * fps))})
    candidate = validate_template_recipe({'version': 2, 'id': recipe_id,
        'name': str(draft.get('name') or 'Scene composition'), 'layers': layers})
    quality_error = _diagram_recipe_quality_error(candidate, duration, fps)
    layout_errors = {'composition_labels_collide', 'composition_label_outside_safe_area',
                     'composition_distinct_actors_overlap', 'composition_intrudes_caption_safe_area',
                     'composition_outside_canvas'}
    if quality_error and fit_layout and ('too sparse' in quality_error or quality_error in layout_errors):
        fitted = fit_actor_objects(draft, approved_nodes, approved_links)
        result = _compile_designed_diagram_recipe(fitted, approved_nodes, approved_links,
            palette, scene_text, duration, fps, id_fallback, fit_layout=False)
        result['layout_source'] = 'local_actor_layout'
        return result
    if quality_error:
        raise ValueError(quality_error)
    if not _recipe_is_grounded(candidate, scene_text):
        raise ValueError('composition_recipe_ungrounded')
    if not _recipe_supports_visual_type(candidate, VisualSceneType.DIAGRAM.value):
        raise ValueError('composition_missing_diagram_layer')
    if geometry_missing:
        candidate['layout_source'] = 'local_actor_layout'
    return candidate


def _recipe_supports_visual_type(recipe: Mapping[str, Any], visual_type: str, map_enabled: bool = False) -> bool:
    """Require a recipe to carry the core explanatory visual it replaces."""
    layers = recipe.get("layers", []) if isinstance(recipe, Mapping) else []
    if not isinstance(layers, list) or not layers:
        return False
    if visual_type == VisualSceneType.CHART.value:
        return any(
            isinstance(layer, Mapping) and str(layer.get("kind") or "").lower() == "chart"
            and isinstance(layer.get("data"), list) and len(layer["data"]) >= 2
            and all(row.get("value") is not None for row in layer["data"] if isinstance(row, Mapping))
            for layer in layers
        )
    if visual_type == VisualSceneType.ANIMATED_MAP.value:
        return bool(map_enabled and any(
            isinstance(layer, Mapping) and str(layer.get("kind") or "").lower() == "map"
            for layer in layers
        ))
    required_kind = {
        VisualSceneType.DIAGRAM.value: "diagram",
        VisualSceneType.TIMELINE.value: "timeline",
        VisualSceneType.COMPARISON.value: "comparison",
        VisualSceneType.COLLAGE.value: "media_montage",
    }.get(str(visual_type))
    if required_kind:
        return any(
            isinstance(layer, Mapping)
            and str(layer.get("kind") or "").lower() == required_kind
            for layer in layers
        )
    return True


_COPY_REPAIR_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "maxItems": 8, "items": {
        "type": "object", "additionalProperties": False,
        "required": ["scene_index", "display_text", "source_span", "emphasis_spans"],
        "properties": {
            "scene_index": {"type": "integer", "minimum": 0},
            "display_text": {"type": "string", "maxLength": 42},
            "source_span": {"type": "string", "maxLength": 240},
            "emphasis_spans": {"type": "array", "maxItems": 2, "items": {
                "type": "object", "additionalProperties": False, "required": ["start", "end"],
                "properties": {"start": {"type": "integer", "minimum": 0}, "end": {"type": "integer", "minimum": 1}},
            }},
        },
    }}},
}

_RECIPE_LAYER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "x", "y", "width", "height", "text", "color", "asset_id", "motion", "start_frame", "duration_frames", "data", "shape", "orientation", "focal_x", "focal_y", "links"],
    "properties": {
        "kind": {"type": "string", "enum": ["text", "line", "rect", "circle", "image", "chart", "map", "diagram", "timeline", "comparison", "counter", "media_montage", "sphere", "cone", "arrow", "group", "path"]},
        "id": {"type": "string", "maxLength": 64},
        "parent_id": {"type": "string", "maxLength": 64},
        "clip": {"type": "boolean"},
        "points": {"type": "array", "maxItems": 32, "items": {"type": "object", "additionalProperties": False,
            "required": ["x", "y"], "properties": {"x": {"type": "number", "minimum": 0, "maximum": 100}, "y": {"type": "number", "minimum": 0, "maximum": 100}}}},
        "x": {"type": "number", "minimum": 0, "maximum": 100},
        "y": {"type": "number", "minimum": 0, "maximum": 100},
        "width": {"type": "number", "minimum": 0, "maximum": 100},
        "height": {"type": "number", "minimum": 0, "maximum": 100},
        "text": {"type": "string", "maxLength": 180},
        "color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
        "asset_id": {"type": "string", "maxLength": 120},
        "image_index": {"type": ["integer", "null"], "minimum": 0, "maximum": 15},
        "motion": {"type": "string", "enum": ["none", "fade", "rise", "slide_left", "slide_right", "scale", "draw", "wipe", "pulse", "parallax"]},
        "start_frame": {"type": "integer", "minimum": 0, "maximum": 100000},
        "duration_frames": {"type": "integer", "minimum": 1, "maximum": 1800},
        "data": {"type": "array", "maxItems": 8, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["label", "value"],
            "properties": {"label": {"type": "string", "maxLength": 48}, "value": {"type": ["number", "null"]}},
        }},
        "shape": {"type": "string", "enum": ["rect", "rounded", "arch", "circle", "hexagon"]},
        "orientation": {"type": "string", "enum": ["horizontal", "vertical"]},
        "focal_x": {"type": "number", "minimum": 0, "maximum": 100},
        "focal_y": {"type": "number", "minimum": 0, "maximum": 100},
        "links": {"type": "array", "maxItems": 8, "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"from": {"type": "string", "maxLength": 48}, "to": {"type": "string", "maxLength": 48}, "evidence": {"type": "string", "maxLength": 180}},
            "required": ["from", "to", "evidence"],
        }},
    },
}

_DIRECTOR_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["scenes"],
    "properties": {
        "scenes": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["scene_index", "type", "variant", "visual_intent", "frame_mode", "visual_role", "overlay_policy", "editorial_priority", "camera", "entrance", "transition", "text_focus", "display_text", "source_span", "emphasis_spans", "placement", "text_treatment", "graphic_theme", "template_id", "template_recipe", "map_shot", "media_plan"],
                "properties": {
                    "scene_index": {"type": "integer", "minimum": 0},
                    "type": {"type": "string", "enum": list(UNIVERSAL_SCENE_TYPES)},
                    "variant": {"type": "string", "maxLength": 48},
                    "visual_intent": {"type": "string", "maxLength": 180},
                    "frame_mode": {"type": "string", "enum": ["media_only", "chapter", "map", "portrait", "evidence", "data", "diagram", "quote", "kinetic"]},
                    "visual_role": {"type": "string", "enum": ["media_overlay", "primary_explanation", "map_canvas"]},
                    "overlay_policy": {"type": "string", "enum": ["none", "single_short_highlight"]},
                    "editorial_priority": {"type": "number", "minimum": 0, "maximum": 1},
                    "camera": {"type": "string", "enum": sorted(ALLOWED_CAMERAS)},
                    "entrance": {"type": "string", "enum": sorted(ALLOWED_ENTRANCES)},
                    "transition": {"type": "string", "enum": sorted(ALLOWED_TRANSITIONS)},
                    "text_focus": {"type": "string", "maxLength": 240},
                    "display_text": {"type": "string", "maxLength": 42},
                    "source_span": {"type": "string", "maxLength": 240},
                    "emphasis_spans": {"type": "array", "maxItems": 2, "items": {
                        "type": "object", "additionalProperties": False, "required": ["start", "end"],
                        "properties": {"start": {"type": "integer", "minimum": 0}, "end": {"type": "integer", "minimum": 1}},
                    }},
                    "placement": {"type": "string", "enum": ["top_left", "top_right", "bottom_left", "bottom_right", "center", "none"]},
                    "text_treatment": {"type": ["object", "null"], "additionalProperties": False,
                        "required": ["panel_style", "text_color", "accent_color", "panel_background"],
                        "properties": {
                            "panel_style": {"type": "string", "enum": sorted(_TEXT_PANEL_STYLES)},
                            "text_color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
                            "accent_color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
                            "panel_background": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
                        }},
                    "graphic_theme": {"type": "string", "enum": sorted(_GRAPHIC_THEMES)},
                    "media_plan": {"type": "object", "additionalProperties": False,
                        "required": ["visual_beats", "media_preference", "rhythm"],
                        "properties": {
                            "visual_beats": {"type": "integer", "minimum": 1, "maximum": 4},
                            "media_preference": {"type": "string", "enum": ["clip", "images", "either"]},
                            "rhythm": {"type": "string", "enum": ["single_hold", "two_beat", "three_beat", "four_beat"]},
                        }},
                    "template_id": {"type": "string", "enum": [*_DIRECTOR_AUTO_TEMPLATES, "generated-recipe"]},
                    "template_recipe": {"type": ["object", "null"], "additionalProperties": False,
                        "required": ["version", "id", "name", "layers"],
                        "properties": {
                            "version": {"type": "integer", "enum": [1, 2]},
                            "id": {"type": "string", "maxLength": 64},
                            "name": {"type": "string", "maxLength": 80},
                            "layers": {"type": "array", "maxItems": 16, "items": _RECIPE_LAYER_SCHEMA},
                        }},
                    "map_shot": {"type": "object", "additionalProperties": False,
                        "required": ["enabled", "location_names", "feature_ids", "route_id", "source_ids", "motion_profile", "render_style"],
                        "properties": {
                            "enabled": {"type": "boolean"},
                            "location_names": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 80}},
                            "feature_ids": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 80}},
                            "route_id": {"type": "string", "maxLength": 120},
                            "source_ids": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 120}},
                            "motion_profile": {"type": "string", "enum": ["auto", *_MAP_MOTION_PROFILES]},
                            "render_style": {"type": "string", "enum": sorted(_MAP_RENDER_STYLES)},
                        }},
                    "diagram_nodes": {"type": "array", "maxItems": 4, "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["label", "source_span"],
                        "properties": {
                            "label": {"type": "string", "maxLength": 64},
                            "source_span": {"type": "string", "maxLength": 220},
                        },
                    }},
                    "diagram_links": {"type": "array", "maxItems": 8, "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["from", "to", "evidence"],
                        "properties": {
                            "from": {"type": "string", "maxLength": 64},
                            "to": {"type": "string", "maxLength": 64},
                            "evidence": {"type": "string", "maxLength": 180},
                        },
                    }},
                    "motion_cues": {"type": "array", "maxItems": 3, "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["element", "action", "anchor_text"],
                        "properties": {
                            "element": {"type": "string", "enum": ["map", "data", "steps", "comparison", "person", "source", "image", "copy"]},
                            "action": {"type": "string", "enum": ["trace", "pin", "count", "wipe", "split", "pan", "reveal", "hold"]},
                            "anchor_text": {"type": "string", "maxLength": 80},
                        },
                    }},
                },
            },
        },
    },
}


def director_planning_budget(scene_count: int) -> Dict[str, float]:
    """Size the director deadline by parallel waves of small scene chunks."""
    def number(name: str, default: float, minimum: float, maximum: float) -> float:
        try:
            return max(minimum, min(maximum, float(os.environ.get(name, str(default)) or default)))
        except (TypeError, ValueError):
            return default

    # Small scene windows bound both the prompt and the failure domain.  A
    # slow provider response should not erase direction for three adjacent
    # beats when each beat can be planned in parallel with the same film arc.
    batch_size = int(number("VIDZEO_EDITORIAL_DIRECTOR_SCENE_BATCH_SIZE", 8, 1, 10))
    workers = int(number("VIDZEO_EDITORIAL_DIRECTOR_SCENE_WORKERS", 4, 1, 4))
    waves = max(1, (max(1, int(scene_count)) + batch_size * workers - 1) // (batch_size * workers))
    art = number("VIDZEO_EDITORIAL_DIRECTOR_ART_TIMEOUT_SECONDS", 20, 5, 20)
    scene_call = number("VIDZEO_EDITORIAL_DIRECTOR_SCENE_TIMEOUT_SECONDS", 55, 10, 55)
    composition = number('VIDZEO_EDITORIAL_DIRECTOR_COMPOSITION_TIMEOUT_SECONDS', 40, 0, 40)
    repair = number("VIDZEO_EDITORIAL_DIRECTOR_REPAIR_TIMEOUT_SECONDS", 10, 3, 10)
    from ..job_context import current_job
    if current_job().get('llm_provider') == 'external_agent':
        # A tool-driven host needs time to read and answer the inbox. Keep
        # every request bounded while preserving adaptive parallel chunks.
        art, scene_call, composition, repair = 180, 300, 180, 60
        batch_size, workers = 4, 2
        waves = max(1, (max(1, int(scene_count)) + batch_size * workers - 1) // (batch_size * workers))
    base = number("VIDZEO_EDITORIAL_DIRECTOR_TOTAL_TIMEOUT_SECONDS", 90, 15, 3600)
    cap = number("VIDZEO_EDITORIAL_DIRECTOR_MAX_TOTAL_TIMEOUT_SECONDS", 3600, 90, 7200)
    adaptive = str(os.environ.get("VIDZEO_EDITORIAL_DIRECTOR_ADAPTIVE_BUDGET", "1")).strip().lower() not in {"0", "false", "off", "no"}
    requested = max(base, art + (scene_call + composition) * waves + repair + 5) if adaptive else base
    total = min(cap, requested)
    # Art direction may finish far sooner than its allowance. The scene stage
    # spends that recovered time while the shared wall deadline still wins.
    stage = max(0.0, min((scene_call + composition) * waves, total - repair - 5))
    return {
        "total_seconds": total,
        "art_seconds": art,
        "scene_call_seconds": scene_call,
        "composition_seconds": composition,
        "scene_stage_seconds": stage,
        "repair_seconds": repair,
        "waves": waves,
        "scene_batch_size": batch_size,
        "scene_workers": workers,
    }


def _director_visual_argument_board(scene_texts: Sequence[str]) -> List[Dict[str, Any]]:
    """Share evidence-led alternatives across parallel windows before picking layouts.

    This is an opportunity index, not a storyboard quota or permission to invent
    chart values, document screenshots or portraits. Its exact evidence anchors
    keep the model's visual argument tied to the narration. Every component and
    variant remains available through the separate live capability contract.
    """
    board: List[Dict[str, Any]] = []
    for index, value in enumerate(scene_texts):
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        triggers = analyze_sentence_triggers(text, is_first_scene=index == 0, context_text=text)
        alternatives: List[str] = []
        anchors: List[str] = []
        if triggers.dates:
            alternatives.append("chronology: Timeline / artifact milestone")
            anchors.extend(str(item) for item in triggers.dates[:2])
        if triggers.locations or triggers.countries:
            alternatives.append("geography: AnimatedMap / located source media")
            anchors.extend(str(item) for item in (list(triggers.locations) + list(triggers.countries))[:2])
        if len(triggers.numbers) >= 2:
            chart = _build_chart_props("editorial_bar", text, triggers.numbers, "dark_modern", [])
            if not chart.get("no_data"):
                alternatives.append("magnitude/change: Chart / Comparison")
                anchors.extend(str(item) for item in triggers.numbers[:2])
        if triggers.quotes:
            alternatives.append("testimony: QuoteCard / verified source crop")
            anchors.append(str(triggers.quotes[0]))
        if triggers.persons:
            alternatives.append("identity: PersonProfile / portrait-led media")
            anchors.append(str(triggers.persons[0]))
        if re.search(r"\b(?:report|memo|record|document|letter|note|transcript|evidence)\b", text, re.I):
            alternatives.append("evidence: DocumentHighlight / source-led collage")
        diagram = _build_diagram_props("cause_and_effect_flow", text, triggers)
        if not diagram.get("no_data") and len(diagram.get("steps") or []) >= 2:
            alternatives.append("mechanism: bespoke actor illustration / Diagram")
        if _is_single_financial_stat(text):
            alternatives.append("one amount: StatCard / exact media highlight")
        board.append({
            "scene": index,
            "arguments": alternatives or ["observation: verified media with purposeful framing"],
            "anchors": [item[:100] for item in dict.fromkeys(anchors)][:4],
        })
    return board


def _director_runtime_capabilities(template_catalog: Optional[Sequence[Mapping[str, Any]]] = None) -> Dict[str, Any]:
    """Expose the renderer's real component, variant, and template surface to the LLM."""
    components = {
        "SmartImage": "SmartImage", "SmartVideo": "SmartVideo", "KineticText": "KineticText",
        "StatCard": "AdaptiveStat", "QuoteCard": "QuoteCard", "Headline": "Headline",
        "DocumentHighlight": "DocumentHighlight", "AnimatedMap": "AnimatedMap",
        "Timeline": "AdaptiveTimeline", "Chart": "AdaptiveChart", "Comparison": "AdaptiveComparison",
        "Collage": "Collage", "PersonProfile": "PersonProfile", "Diagram": "ExplanatoryDiagram",
        "ChapterTitle": "ChapterTitle",
    }
    visuals = {
        scene_type: {"remotion_component": components.get(scene_type, scene_type), "variants": list(variants)}
        for scene_type, variants in SCENE_TYPE_VARIANTS.items()
    }
    templates = [
        {"id": str(item.get("id") or ""), "purpose": str(item.get("purpose") or ""),
         "component": str(item.get("component") or ""), "roles": list(item.get("roles") or [])}
        for item in (template_catalog if template_catalog is not None else list_template_catalog().get("templates", []))
        if item.get("status") == "approved"
    ]
    return {
        "visual_systems": visuals,
        "map_render_styles": {
            "globe_3d": {
                "renderer": "Globe3D via AnimatedMap",
                "use_when": "world-scale or interregional geography; use only script-grounded locations and routes",
                "motion": "single eased globe turn with a stable hold",
            },
            "satellite_local": {
                "renderer": "SatelliteMap via AnimatedMap",
                "use_when": "compact route with verified coordinates and an attributed satellite plate",
                "motion": "one smooth subject-focused move; no camera oscillation",
            },
            "vector_editorial": {
                "renderer": "AnimatedMap Natural Earth vectors",
                "use_when": "country/state boundaries or a spatial explanation without a suitable satellite plate",
                "motion": "monotonic eased pan/zoom, then a readable hold",
            },
        },
        "text_treatments": sorted(_TEXT_PANEL_STYLES),
        "bundled_typography": {
            "display": list(_DIRECTOR_DISPLAY_FONT_NAMES),
            "body": list(_DIRECTOR_BODY_FONT_NAMES),
            "numbers": "JetBrains Mono",
        },
        "opening_rhythm": "meaningful reveal or supported editorial cut every 2–3 seconds; then settle",
        "approved_templates": templates,
        "generated_recipe": {
            "version": 2,
            "layer_kinds": ["text", "line", "rect", "circle", "image", "chart", "map", "diagram", "timeline", "comparison", "counter", "media_montage", "sphere", "cone", "arrow", "group", "path"],
            "motion": ["none", "fade", "rise", "slide_left", "slide_right", "scale", "draw", "wipe", "pulse", "parallax"],
            "frame_shapes": ["rect", "rounded", "arch", "circle", "hexagon"],
            "maximum_layers": 16,
            "image_binding": "Use image_index to bind distinct verified scene images, in unique image order. Omit unavailable slots; never duplicate one image merely to fill a multi-frame layout.",
            "no_code_or_remote_assets": True,
        },
    }


def _director_design_contract_snapshot() -> Dict[str, Any]:
    """Bind one job's prompt, schema and payload lookup to the same live catalog.

    Approving a custom recipe after server startup must make it selectable on
    the next job. A private schema copy also prevents parallel jobs/workers
    from mutating each other's allowed enum during catalog updates.
    """
    catalog = list_template_catalog()
    approved = [copy.deepcopy(dict(item)) for item in catalog.get("templates", [])
                if isinstance(item, Mapping) and item.get("status") == "approved" and item.get("id")]
    ids = list(dict.fromkeys(str(item["id"]) for item in approved))
    schema = copy.deepcopy(_DIRECTOR_RESPONSE_SCHEMA)
    schema["properties"]["scenes"]["items"]["properties"]["template_id"]["enum"] = [*ids, "generated-recipe"]
    return {"schema": schema, "templates_by_id": {str(item["id"]): item for item in approved},
            "capabilities": _director_runtime_capabilities(approved), "version": catalog.get("version", 1)}


def _director_prompt_art_direction(art_direction: Mapping[str, Any]) -> Dict[str, Any]:
    """Send each film policy once, without changing the stored art direction.

    The full niche grammar is a separate authoritative section of every
    request. Repeating it inside art_direction adds tokens, not information.
    Keep all other fields, including user-supplied creative instructions.
    """
    return {key: value for key, value in art_direction.items() if key != "visual_grammar"}


def _director_prompt_argument_window(board: Sequence[Mapping[str, Any]], start: int, end: int) -> Dict[str, Any]:
    """Retain film-wide grammar opportunities and local grounding compactly.

    All scene indices stay in the family index so parallel workers can avoid
    overusing one explanation grammar. Exact anchors are needed only for the
    scenes this worker owns; full-film story/adjacent narration stays separate.
    """
    families: Dict[str, List[int]] = {}
    for row in board:
        for argument in row.get("arguments") or []:
            families.setdefault(str(argument), []).append(int(row["scene"]))
    return {"film_arguments": families, "window": [dict(row) for row in board if start <= int(row["scene"]) < end]}


def _plan_llm_scenes(
    scene_texts: Sequence[str],
    scene_durations: Sequence[float],
    media_items: Optional[Sequence[Mapping[str, Any]]] = None,
    word_timings: Optional[Sequence[Mapping[str, Any]]] = None,
    visual_dna: Optional[VisualDNA] = None,
    llm_client: Optional[Any] = None,
    director_config: Optional[Mapping[str, Any]] = None,
    editing_profile: Optional[Mapping[str, Any]] = None,
    metadata_out: Optional[Dict[str, Any]] = None,
    variation_seed: Optional[int] = None,
    fps: int = 30,
) -> Optional[List[SceneDecision]]:
    """Attempt structured, LLM-assisted scene direction via OpenRouter.

    Returns a list of SceneDecision objects if successful, or None on failure
    (so the caller can fall back to deterministic planning).
    """
    from ..provider_selection import normalize_provider_config
    director_config = normalize_provider_config(director_config or {})
    selected_provider = director_config["llm_provider"]
    metadata = metadata_out if metadata_out is not None else {}
    design_contract = _director_design_contract_snapshot()
    live_templates_by_id = design_contract["templates_by_id"]
    scene_response_schema = design_contract["schema"]
    try:
        creative_seed = int(variation_seed) if variation_seed is not None else 0
    except (TypeError, ValueError):
        creative_seed = 0
    if llm_client is None:
        # Try to construct from environment
        api_key = (
            director_config.get("openrouter_api_key")
            or director_config.get("openrouterApiKey")
            or os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("VIDZEO_OPENROUTER_API_KEY")
            or ""
        ).strip()
        if not api_key:
            metadata.update({"provider": selected_provider, "status": "deterministic_fallback", "reason": "api_key_missing"})
            return None
        try:
            from ..openrouter_llm import OpenRouterLLM
            model = str(
                director_config.get("director_model")
                or os.environ.get("VIDZEO_EDITORIAL_DIRECTOR_MODEL")
                or DIRECTOR_DEFAULT_MODEL
            ).strip()
            configured_fallbacks = director_config.get("director_fallback_models")
            fallbacks = list(_director_fallback_models(configured_fallbacks))
            fallbacks = [item for item in fallbacks if str(item).strip() != model]
            llm_client = OpenRouterLLM(
                api_key=api_key,
                model=model,
                fallback_models=fallbacks,
                use_server_fallbacks=False,
            )
            metadata.update({"provider": selected_provider, "configured_model": model, "fallback_chain": [model, *fallbacks]})
        except Exception as exc:
            print(f"   [LLM Director] Could not create LLM client: {exc}")
            metadata.update({"provider": selected_provider, "status": "deterministic_fallback", "reason": f"client_error:{type(exc).__name__}"})
            return None
    else:
        metadata.update({"provider": selected_provider, "configured_model": str(getattr(llm_client, "model", "") or "")})

    # Build the user prompt with numbered scene texts and niche guidance
    from ..editing_profile import classify_niche, niche_visual_grammar, NICHE_VISUAL_GRAMMAR

    supplied_profile = dict(editing_profile or {})
    palette_niche = str((getattr(visual_dna, "palette", {}) or {}).get("niche") or "").strip().lower()
    profile_niche = str(supplied_profile.get("niche") or palette_niche or "").strip().lower()
    detected_niche = classify_niche(
        str(director_config.get("topic") or director_config.get("content_title") or ""),
        str(director_config.get("category") or ""),
        " ".join(str(value or "") for value in scene_texts),
    )
    inferred_general = (
        profile_niche == "general"
        and detected_niche != "general"
        and not bool(supplied_profile.get("niche_locked"))
    )
    niche = (
        detected_niche
        if inferred_general
        else profile_niche if profile_niche in NICHE_VISUAL_GRAMMAR else detected_niche
    )
    metadata["niche"] = niche
    metadata["niche_source"] = (
        "script_reclassification" if inferred_general
        else str(supplied_profile.get("niche_source") or "script_or_explicit_profile")
    )
    visual_grammar = supplied_profile.get("visual_grammar")
    if not isinstance(visual_grammar, Mapping):
        visual_grammar = niche_visual_grammar(niche)
    niche_note = (
        f"Film niche: {niche}. This guide defines useful evidence and visual vocabularies, not a fixed template or quota. "
        "Let each scene's facts, emotional role, and available verified media decide which instrument to use.\n"
        f"Niche visual grammar: {json.dumps(dict(visual_grammar), ensure_ascii=False)}\n"
    )

    scene_lines = []
    for idx, text in enumerate(scene_texts):
        dur = float(scene_durations[idx]) if idx < len(scene_durations) else 4.0
        line = f"Scene {idx} ({dur:.1f}s): {text.strip()}"
        if media_items and idx < len(media_items) and isinstance(media_items[idx], Mapping):
            media = media_items[idx]
            media_kind = str(media.get("media_type") or media.get("type") or "visual").strip().lower()
            resolution_kind = str(media.get("resolution_kind") or "").strip().casefold()
            is_graphic_fallback = bool(media.get("is_graphic_fallback")) or resolution_kind in {
                "grounded_graphic_fallback", "graphic_fallback",
            } or str(media.get("source") or "").strip().casefold() == "graphic"
            if is_graphic_fallback:
                fallback = media.get("graphic_fallback") if isinstance(media.get("graphic_fallback"), Mapping) else {}
                fallback_facts = [str(value).strip() for value in fallback.get("facts") or [] if str(value).strip()]
                line += (
                    "\n  MEDIA STATUS: emergency locally generated graphic fallback, NOT verified source imagery. "
                    "Do not call this an approved photo, clip, screenshot, or evidence. Prefer a script-earned "
                    "Remotion explanation (timeline, real-place map, process diagram, comparison, or sourced data) "
                    "when its exact payload is present; otherwise request a grounded replacement asset and keep "
                    "the fallback visually quiet. Never invent a map, measurement, quote, or visible copy."
                )
                if fallback_facts:
                    line += "\n  Grounded fallback terms: " + ", ".join(fallback_facts[:4])
            media_title = next((str(media.get(key) or "").strip() for key in (
                "title", "headline", "page_title", "caption", "alt", "description", "query",
            ) if str(media.get(key) or "").strip()), "")
            media_title = re.sub(r"https?://\S+|file://\S+", "", media_title)
            media_title = re.sub(r"\s+", " ", media_title).strip()[:150]
            if media_title:
                line += f"\n  Available visual reference ({media_kind}): {media_title}"
            elif (media.get("src") or media.get("path")) and not is_graphic_fallback:
                line += f"\n  An approved {media_kind} asset is available; choose it only if it fits this beat."
            alternates = [item for item in (media.get("alternates") or []) if isinstance(item, Mapping) and (item.get("src") or item.get("path"))]
            if alternates:
                line += f"\n  Additional distinct media assets available: {len(alternates)}"
            if media.get("satellite_image") and media.get("satellite_bounds"):
                line += "\n  Verified georeferenced satellite imagery is available for this scene."
            elif media.get("route_points") or (media.get("p1") and media.get("p2")):
                line += "\n  Coordinate-grounded route metadata is available; choose satellite_local only for a compact, narrated route."
            planned_shots = [item for item in (media.get("shots") or []) if isinstance(item, Mapping)]
            if planned_shots:
                shot_notes = []
                for shot in planned_shots[:5]:
                    try:
                        start = float(shot.get("shot_start") or 0.0)
                        end = float(shot.get("shot_end") or start)
                    except (TypeError, ValueError):
                        continue
                    phrase = re.sub(r"\s+", " ", str(shot.get("beat_text") or "")).strip()[:110]
                    reference = re.sub(r"\s+", " ", str(shot.get("visual_reference") or "")).strip()[:110]
                    note = f"{start:.1f}–{end:.1f}s"
                    if phrase:
                        note += f" on narration: {phrase}"
                    if reference:
                        note += f" | visual reference: {reference}"
                    shot_notes.append(note)
                if shot_notes:
                    line += "\n  Verified cut windows: " + "; ".join(shot_notes)
        scene_lines.append(line)
    overlay_budget = _director_overlay_budget(len(scene_texts), director_config)
    art_story_map = _director_story_map(scene_texts, max_chars=5000, max_anchors=12)
    user_prompt = (
        f"Documentary with {len(scene_texts)} scenes.\n"
        f"Visible overlay budget for the complete film: at most {overlay_budget} scenes.\n"
        "Use an exact, LLM-grounded short highlight on roughly one in three eligible story beats, choosing pivotal actions, score/value changes, reversals, stakes, and named people or places. Do not force text on atmospheric beats, and never place highlights in adjacent scenes. Direct the visual grammar as well as the words. Use media_only for intentional breathing room, not as the default for explanatory beats. When facts support them, use animated maps, diagrams, timelines, portraits, comparisons, data, or evidence frames.\n"
        f"{niche_note}"
        f"Representative film-wide story arc (opening through payoff):\n{art_story_map}"
    )

    try:
        print(f"   [LLM Director] Setting video art direction, then directing {len(scene_texts)} scenes...")

        def _model_route_record(stage: str, client: Any, scene_indices=None) -> Dict[str, Any]:
            configured_model = str(getattr(client, "model", "") or "").strip()
            selected_model = str(
                getattr(client, "last_model", "") or configured_model
            ).strip()
            try:
                fallback_chain = [
                    str(item).strip()
                    for item in (getattr(client, "model_chain", ()) or ())
                    if str(item).strip()
                ]
            except Exception:
                fallback_chain = []
            if not fallback_chain and configured_model:
                fallback_chain = [configured_model]
            attempts = [
                dict(item)
                for item in (getattr(client, "attempt_history", ()) or ())
                if isinstance(item, Mapping)
            ]
            fallback_used = bool(
                configured_model
                and selected_model
                and selected_model != configured_model
            ) or any(
                str(item.get("resolved_model") or "").strip()
                and str(item.get("resolved_model") or "").strip() != configured_model
                for item in attempts
            )
            route = {
                "stage": str(stage),
                "configured_model": configured_model,
                "selected_model": selected_model or configured_model,
                "fallback_chain": fallback_chain,
                "fallback_used": bool(fallback_used),
                "status": "fallback" if fallback_used else "primary",
                "attempt_history": attempts,
            }
            if scene_indices is not None:
                route["scene_indices"] = [int(index) for index in scene_indices]
            return route

        fallback_art_direction = {
            "niche": niche,
            "visual_grammar": dict(visual_grammar),
            "style": "editorial",
            "display_font": "Archivo Black",
            "body_font": "Inter",
            "accent_color": "#F2C14E",
            "visual_motif": "subject-led maps, evidence marks, cutouts, and scale shifts",
            "motion_rhythm": "short narration-synced traces, reveals, splits, and settled holds",
            "story_arc": _director_story_map(scene_texts),
            "retention_plan": "Open with a clear question, vary visual scale through explanation, accent the turn, then give the payoff room to land.",
        }
        try:
            director_retries = max(1, min(3, int(os.environ.get("VIDZEO_EDITORIAL_DIRECTOR_MAX_ATTEMPTS", "2") or 2)))
        except (TypeError, ValueError):
            director_retries = 2
        budget = director_planning_budget(len(scene_texts))
        total_timeout = budget["total_seconds"]
        art_timeout = budget["art_seconds"]
        scene_timeout = budget["scene_call_seconds"]
        scene_stage_timeout = budget["scene_stage_seconds"]
        repair_timeout = budget["repair_seconds"]
        deadline = time.monotonic() + total_timeout
        metadata["planning_budget_seconds"] = total_timeout
        metadata["planning_budget_waves"] = budget["waves"]
        from concurrent.futures import ThreadPoolExecutor, wait
        from ..job_context import JobThreadPoolExecutor as ThreadPoolExecutor

        def _bounded_chat(client, hard_timeout, **kwargs):
            # The model client normally enforces its own network timeout. This
            # outer wait also bounds providers or test doubles that ignore it.
            pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="director-call")
            def timed_chat():
                from ..job_context import phase_scope
                with phase_scope(hard_timeout):
                    return client.chat(**kwargs)
            future = pool.submit(timed_chat)
            try:
                return future.result(timeout=max(.01, hard_timeout))
            finally:
                pool.shutdown(wait=False, cancel_futures=True)

        try:
            art_call_timeout = max(.01, min(art_timeout, deadline - time.monotonic()))
            art_response = _bounded_chat(
                llm_client, art_call_timeout,
                user_prompt=(f"Creative variation seed for this production: {creative_seed}. Use it only as a tie-breaker between equally grounded design choices; never let it alter facts. "
                             "Set the video-level art direction. Read the full script context, identify its story arc and retention curve, "
                             "then set a memorable visual motif and purposeful motion rhythm. Return only one coherent direction.\n\n" + user_prompt),
                system_prompt=_ART_DIRECTION_PROMPT,
                temperature=0.35,
                max_tokens=900,
                json_mode=True,
                json_schema=_ART_DIRECTION_SCHEMA,
                json_schema_name="vidnets_art_direction_v1",
                primary_timeout_share=.8,
                max_attempts=director_retries,
                total_timeout_seconds=art_call_timeout,
            )
            art_payload = _decode_director_json(art_response)
            art_route = _model_route_record("art_direction", llm_client)
        except Exception as art_error:
            # A slow or unavailable art-direction call must not prevent the
            # separate, smaller scene-direction requests from using the LLM.
            art_payload = {}
            art_route = _model_route_record("art_direction", llm_client)
            art_route["status"] = "failed"
            art_route["error_type"] = type(art_error).__name__
            metadata["art_direction_fallback"] = type(art_error).__name__
            print(f"   [LLM Director] Art-direction request failed; continuing with bounded scene batches ({type(art_error).__name__})")
        art_attempt_history = list(art_route.get("attempt_history") or [])
        scene_batch_failures = []
        # Older clients and test doubles may return the former all-in-one
        # response. Preserve that load path while live OpenRouter uses the
        # video-direction pass followed by bounded scene batches.
        if isinstance(art_payload.get("scenes"), list):
            art_route["stage"] = "art_direction_and_scenes"
            scene_decisions = art_payload.get("scenes") or []
            art_direction = fallback_art_direction
            model_routes = [art_route]
        else:
            art_direction = {**fallback_art_direction, **art_payload}
            # Scene copy may personalize the film motif, but the LLM's art
            # call cannot relabel the explicit script niche for later chunks.
            art_direction["niche"] = niche
            art_direction["visual_grammar"] = dict(visual_grammar)
            if niche == "crime":
                art_direction["accent_color"] = str((editing_profile or {}).get("palette", {}).get("accent") or "#D32F2F")
            for key in ("visual_motif", "motion_rhythm", "story_arc", "retention_plan"):
                value = str(art_direction.get(key) or "").strip()
                art_direction[key] = value or fallback_art_direction[key]
            scene_decisions = []
            model_routes = [art_route]
            batches = []
            try:
                scene_batch_size = max(1, min(10, int(os.environ.get("VIDZEO_EDITORIAL_DIRECTOR_SCENE_BATCH_SIZE", "8") or 8)))
            except (TypeError, ValueError):
                scene_batch_size = 8
            scene_batch_size = int(budget.get('scene_batch_size', scene_batch_size))
            story_map = _director_story_map(scene_texts)
            runtime_capabilities = design_contract["capabilities"]
            visual_argument_board = _director_visual_argument_board(scene_texts)
            compact_capabilities = json.dumps(runtime_capabilities, ensure_ascii=False, separators=(",", ":"))
            scene_palette = dict(visual_dna.palette or {}) if visual_dna else {}
            scene_palette["accent"] = str(art_direction.get("accent_color") or scene_palette.get("accent") or "#FFFFFF")
            metadata["context_strategy"] = "full_film_arc_scene_anchors_and_adjacent_beats"
            metadata["scene_batch_size"] = scene_batch_size
            metadata["visual_argument_board"] = visual_argument_board
            metadata["capability_index_strategy"] = "complete_compact_catalog_no_removed_variants"
            for batch_start in range(0, len(scene_lines), scene_batch_size):
                batch_end = min(len(scene_lines), batch_start + scene_batch_size)
                batch_lines = scene_lines[batch_start:batch_end]
                before_context = scene_lines[batch_start - 1] if batch_start > 0 else "(opening scene)"
                after_context = scene_lines[batch_end] if batch_end < len(scene_lines) else "(final scene)"
                progress_start = batch_start / max(1, len(scene_lines))
                progress_end = batch_end / max(1, len(scene_lines))
                batch_context = " ".join(scene_texts[max(0, batch_start - 1):min(len(scene_texts), batch_end + 1)])
                admin1_catalog = {
                    name: code for name, code in load_admin1_catalog().items()
                    if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", batch_context, re.I)
                }
                batches.append((batch_start, batch_end, batch_lines, (
                    f"Video art direction (locked for this film): {json.dumps(_director_prompt_art_direction(art_direction), ensure_ascii=False, separators=(',', ':'))}\n"
                    f"Live Remotion design capabilities (exact selectors and approved catalog): {compact_capabilities}\n"
                    f"Creative variation seed: {creative_seed}. Use it only to resolve equally suitable visual choices; story facts always win.\n"
                    f"Niche visual grammar: {json.dumps(dict(visual_grammar), ensure_ascii=False, separators=(',', ':'))}\n"
                    f"Approved film color palette (exact hex/semantic roles): {json.dumps(scene_palette, ensure_ascii=False, separators=(',', ':'))}\n"
                    f"Full-film story map, from the beginning through payoff:\n{story_map}\n"
                    f"Film-wide visual argument opportunities: {json.dumps(_director_prompt_argument_window(visual_argument_board, batch_start, batch_end), ensure_ascii=False, separators=(',', ':'))}\n"
                    "Choose the explanatory question and evidence BEFORE the template. These opportunities are alternatives, not a restricted component shortlist.\n"
                    f"This window covers {progress_start:.0%}–{progress_end:.0%} of the film. Treat its scenes as one connected passage in that full arc.\n"
                    f"Immediate setup just before this window: {before_context}\n"
                    f"Immediate narrative handoff after this window: {after_context}\n"
                    f"Available Natural Earth 1:10m state/prefecture features: {json.dumps(admin1_catalog, ensure_ascii=False)}.\n"
                    f"Film-wide short-copy budget: at most {overlay_budget} scenes; one scene of cooldown, weighted toward pivotal exact facts/entities. Graphics can explain without extra headlines.\n"
                    f"Direct only these scenes, preserving their global indices {batch_start}-{batch_end - 1}.\n"
                    "STRICT REMOTION DESIGN CONTRACT: Use exact live component/variant/template selectors and schema fields. Explain the scene-specific fact; preserve all supported custom design options.\n"
                    "The entire live palette is available; choose and vary evidence-supported families across the film. Aim near 30% purposeful graphics; retain clean media when it serves best.\n"
                    "Apply the system grounding/placement rules and supplied local fps. Return only the owned scenes.\n\n"
                    + "\n".join(batch_lines)
                )))
            try:
                scene_max_tokens = max(1800, min(8000, int(os.environ.get("VIDZEO_EDITORIAL_DIRECTOR_SCENE_MAX_TOKENS", str(min(8000, 2400 * scene_batch_size))) or 2400 * scene_batch_size)))
            except (TypeError, ValueError):
                scene_max_tokens = min(8000, 2400 * scene_batch_size)
            try:
                scene_workers = max(1, min(4, int(os.environ.get("VIDZEO_EDITORIAL_DIRECTOR_SCENE_WORKERS", "4") or 4)))
            except (TypeError, ValueError):
                scene_workers = 3
            scene_workers = int(budget.get('scene_workers', scene_workers))

            def _batch_client():
                # OpenRouterLLM keeps per-request retry history, so each live
                # worker receives an isolated client. Test doubles are reused
                # for backwards-compatible unit probes.
                if hasattr(llm_client, "api_key") and hasattr(llm_client, "fallback_models"):
                    try:
                        return llm_client.__class__(
                            api_key=llm_client.api_key,
                            model=getattr(llm_client, "model", None),
                            fallback_models=list(getattr(llm_client, "fallback_models", ()) or ()),
                            use_server_fallbacks=bool(getattr(llm_client, "use_server_fallbacks", False)),
                        )
                    except Exception:
                        pass
                return llm_client

            def _direct_batch(batch):
                batch_start, batch_end, batch_lines, batch_prompt = batch
                worker_client = _batch_client()
                failure_stage = 'provider_request'
                try:
                    remaining = min(scene_timeout, deadline - time.monotonic() - repair_timeout)
                    batch_deadline = time.monotonic() + remaining
                    if remaining < 4.0:
                        raise TimeoutError("director scene planning budget exhausted")
                    response = _bounded_chat(worker_client, remaining,
                        user_prompt=batch_prompt + '\nFIRST PASS: For Diagram return template_recipe=null with grounded actors/links and layout intent for the separate composition pass. Return concise decisions, not repeated policy or reasoning.',
                        system_prompt=_LLM_DIRECTOR_PROMPT,
                        temperature=0.5,
                        max_tokens=scene_max_tokens,
                        json_mode=True,
                        json_schema=scene_response_schema,
                        json_schema_name="vidnets_scene_direction_v1",
                        primary_timeout_share=0.65,
                        max_attempts=director_retries,
                        total_timeout_seconds=remaining,
                    )
                    failure_stage = 'response_decoding'
                    payload = _decode_director_json(response)
                    decisions = payload.get("scenes") or payload.get("decisions") or []
                    failure_stage = 'scene_index_coverage'
                    if isinstance(decisions, list):
                        for row in decisions:
                            if isinstance(row, dict):
                                index_value = row.get('scene_index')
                                if isinstance(index_value, str) and index_value.strip().isdigit():
                                    row['scene_index'] = int(index_value)
                        # Full-film context can cause a model to return extra
                        # scenes. Only this worker's owned indices may mutate.
                        decisions = [row for row in decisions if isinstance(row, Mapping)
                                     and isinstance(row.get("scene_index"), int)
                                     and batch_start <= row["scene_index"] < batch_end]
                    if not isinstance(decisions, list) or {row["scene_index"] for row in decisions} != set(range(batch_start, batch_end)) or len(decisions) != len(batch_lines):
                        raise ValueError(f"scene batch {batch_start}-{batch_end - 1} returned an incomplete plan")
                    # A concrete composition pass prevents the broad director
                    # from always choosing the cheapest diagram template.
                    # It shares this chunk's deadline and never discards a
                    # successful scene when the optional design pass fails.
                    for decision in decisions:
                        _normalize_conflicting_frame_mode(decision, scene_texts[int(decision['scene_index'])])
                        if str(decision.get('frame_mode') or '') != 'diagram':
                            continue
                        passage = str(scene_texts[int(decision.get('scene_index', batch_start))])
                        try:
                            existing_recipe = validate_template_recipe(decision.get('template_recipe'))
                            quality_error = _diagram_recipe_quality_error(existing_recipe, scene_durations[int(decision.get('scene_index', batch_start))], fps)
                            if quality_error:
                                decision['template_recipe'] = None
                                decision['_composition_error'] = quality_error
                                decision['_composition_quality_rejected'] = True
                                raise ValueError(quality_error)
                            if _recipe_is_grounded(existing_recipe, passage) and _recipe_supports_visual_type(existing_recipe, VisualSceneType.DIAGRAM.value):
                                decision.pop('_composition_error', None)
                                decision.pop('_composition_quality_rejected', None)
                                decision['_composition_pass'] = 'initial_composition_valid'
                                continue
                        except (ValueError, TypeError):
                            pass
                        # Invalid or ungrounded first-pass geometry cannot block
                        # later repair just because a recipe dictionary exists.
                        decision['template_recipe'] = None
                        design_remaining = min(budget['composition_seconds'], batch_deadline + budget['composition_seconds'] - time.monotonic(), deadline - time.monotonic() - repair_timeout)
                        if design_remaining < 6:
                            decision['_composition_pass'] = 'budget_exhausted'
                            continue
                        # Per-scene state cannot leak from a preceding recipe,
                        # and a preflight rejection has no correction prompt.
                        composition_user_prompt = None
                        recipe_schema = None
                        try:
                            composition_client = worker_client
                            if hasattr(worker_client, 'api_key') and hasattr(worker_client, 'fallback_models'):
                                composition_model, composition_fallbacks = _composition_model_route(director_config, worker_client)
                                decision['_composition_model_chain'] = [composition_model, *composition_fallbacks]
                                composition_client = worker_client.__class__(
                                    api_key=worker_client.api_key, model=composition_model,
                                    fallback_models=composition_fallbacks, use_server_fallbacks=False)
                            approved_nodes = validated_diagram_nodes(decision.get('diagram_nodes'), passage)
                            approved_links = validated_diagram_links(decision.get('diagram_links'), approved_nodes, passage)
                            if len(approved_nodes) < 2:
                                raise ValueError('Composition has no approved actors')
                            object_properties = {
                                'actor_label': {'type': 'string', 'enum': [node['label'] for node in approved_nodes]},
                                'kind': {'type': 'string', 'enum': ['sphere', 'cone', 'arrow', 'circle', 'line', 'rect', 'path']},
                                'points': {'type':'array', 'maxItems':12, 'items':{'type':'object','additionalProperties':False,
                                    'required':['x','y'], 'properties':{'x':{'type':'number','minimum':0,'maximum':100},'y':{'type':'number','minimum':0,'maximum':100}}}},
                                **{key: {'type': ['number', 'null'], 'minimum': 0, 'maximum': 100} for key in ['x', 'y', 'width', 'height']},
                                'color_role': {'type': 'string', 'enum': ['accent', 'secondary', 'primary', 'background']},
                                'fill_role': {'type': ['string', 'null'], 'enum': ['accent', 'secondary', 'primary', 'background', 'none', None]},
                                'closed': {'type': ['boolean', 'null']},
                                'motion': {'type': 'string', 'enum': ['fade', 'scale', 'draw', 'wipe', 'rise']},
                                'start_seconds': {'type': 'number', 'minimum': 0, 'maximum': 60},
                            }
                            recipe_schema = {'type': 'object', 'additionalProperties': False,
                                'required': ['id', 'name', 'objects', 'layout'], 'properties': {
                                    'layout': {'type': ['string', 'null'], 'enum': ['flow', 'vertical', 'branch', 'network', None]},
                                    'id': {'type': 'string', 'maxLength': 64}, 'name': {'type': 'string', 'maxLength': 80},
                                    'objects': {'type': 'array', 'minItems': 2, 'maxItems': 8, 'items': {
                                        'type': 'object', 'additionalProperties': False, 'required': list(object_properties),
                                        'properties': object_properties}}}}
                            composition_user_prompt = (f'Full film context: {story_map}\nNiche grammar: {json.dumps(dict(visual_grammar), ensure_ascii=False)}\nApproved palette: {json.dumps(scene_palette)}\nSelected narration: {passage}\nFrame clock: {int(fps)} fps; scene duration: {scene_durations[int(decision.get("scene_index", batch_start))]} seconds.\n' +
                                'COMPOSITION PASS: Return only a recipe for this selected direction: ' + json.dumps({key: decision.get(key) for key in ['scene_index', 'type', 'variant', 'visual_intent', 'diagram_nodes', 'diagram_links', 'motion_cues']}, ensure_ascii=False) +
                                '\nQUALITY REPAIR: '+str(decision.get('_composition_error') or 'No prior layout')+'. Keep objects within x=6..94 and y=10..72. Separate actor labels horizontally or vertically; leave at least .65 seconds of hold after the final reveal. Occupy substantial distinct frame area; background plates and stacked duplicates do not count.\nAllowed kinds: sphere,cone,arrow,circle,line,rect,path. A path supplies 2–12 normalized points for a meaningful traced contour or flow. Geometric illustration only; no external images. Do not use anonymous circles as substitutes for people, machinery or animal anatomy. Use substantial subject-specific silhouettes, contours and relationships, with readable labels. ' +
                                '\nReturn a compact layout draft: id,name,objects. Every object includes points: use [] for non-path shapes and 2–12 explicit x/y contour points for a path; source/target identifiers cannot replace its geometry. Draw 3–5 purposeful geometric objects, each bound to its exact actor_label from the approved nodes. Use percentages for positions/sizes, palette roles for colors, local seconds for starts. Reserve the bottom 25% for emphasis and subtitles. Use semantic lighting: dark or occluding regions use background, illuminated or emitting elements use primary, and accent is an annotation. The compiler places each actor label above its object; account for label space and the 16:9 canvas. Circle/sphere boxes use different percentage axes: a round object 22% wide needs 39% height in 16:9. Square percentage boxes shrink the visible disc. Occupy at least 10% distinct frame area with actual actors, not arrows or background plates. Choose positions and shapes that explain this specific mechanism. Do not emit the full Remotion layer format, decorative card rows or generic heading plates.')
                            composition_user_prompt += '\nPrefer to set x,y,width,height to null and choose layout=flow|vertical|branch|network: the local actor-layout compiler will fit your semantic shapes and labels. Supply geometry only when a physical spatial relationship requires it. Keep meaningful contours and separate subject objects for every approved actor.'
                            composition_system_prompt = 'You design original documentary explanatory compositions from a validated Remotion vocabulary. Explain the spoken mechanism visually. Preserve the full story context and all grounding rules. Main subject paths should be closed filled silhouettes using fill_role; connectors use fill_role=none. Keep distinct actors separated with clear gutters and overlap below 18 percent of the smaller actor. Use narration-aligned short reveals followed by readable holds, never scene-long drawing.'
                            initial_composition_budget = max(6.0, min(design_remaining, design_remaining * 0.6))
                            designed = _bounded_chat(composition_client, initial_composition_budget,
                                user_prompt=composition_user_prompt,
                                system_prompt=composition_system_prompt,
                                temperature=.6, max_tokens=2200, json_mode=True, json_schema=recipe_schema,
                                json_schema_name='vidnets_scene_composition_v1', primary_timeout_share=.8,
                                max_attempts=1, total_timeout_seconds=initial_composition_budget,
                            )
                            decision['_composition_model'] = str(getattr(composition_client, 'last_model', None) or getattr(composition_client, 'model', '') or '')
                            draft = _decode_director_json(designed)
                            draft['id'] = re.sub(r'[^a-z0-9-]+', '-', str(draft.get('id') or '').casefold()).strip('-')[:64].rstrip('-') or f'scene-{batch_start}-composition'
                            approved_nodes = validated_diagram_nodes(decision.get('diagram_nodes'), passage)
                            if 'objects' in draft:
                                draft = _compile_designed_diagram_recipe(
                                    draft, approved_nodes, approved_links, scene_palette, passage,
                                    scene_durations[int(decision.get('scene_index', batch_start))], fps,
                                    f'scene-{batch_start}-composition',
                                )
                            candidate = validate_template_recipe(draft)
                            quality_error = _diagram_recipe_quality_error(candidate, scene_durations[int(decision.get('scene_index', batch_start))], fps)
                            if quality_error:
                                decision['_composition_quality_rejected'] = True
                                raise ValueError(quality_error)
                            if _recipe_is_grounded(candidate, passage) and _recipe_supports_visual_type(candidate, VisualSceneType.DIAGRAM.value):
                                decision['template_recipe'] = candidate
                                decision['template_id'] = 'generated-recipe'
                                decision.pop('_composition_error', None)
                                decision.pop('_composition_quality_rejected', None)
                                decision['_composition_pass'] = 'accepted'
                            else:
                                decision['_composition_pass'] = 'rejected_ungrounded'
                        except Exception as design_error:
                            decision['_composition_error'] = str(design_error)[:180]
                            decision['_composition_attempt_count'] = 1
                            # Reserve part of the bounded composition allowance
                            # for one focused correction. A rejected geometry
                            # gets a chance to fix its exact issue before the
                            # scene is degraded to media-only.
                            correction_budget = min(
                                budget['composition_seconds'],
                                batch_deadline + budget['composition_seconds'] - time.monotonic(),
                                deadline - time.monotonic() - repair_timeout,
                            )
                            if correction_budget >= 6 and composition_user_prompt and recipe_schema:
                                try:
                                    corrected = _bounded_chat(composition_client, correction_budget,
                                        user_prompt=composition_user_prompt + '\nCORRECTION PASS: The previous layout was rejected because: ' + str(design_error)[:220] + '. Redesign the shapes and positions to fix this exact issue. Keep the same approved actors, narration facts, palette, and visual argument; make each actor substantial and distinct, prevent label collisions, and leave a readable hold.',
                                        system_prompt=composition_system_prompt, temperature=.45, max_tokens=2200,
                                        json_mode=True, json_schema=recipe_schema,
                                        json_schema_name='vidnets_scene_composition_repair_v1', primary_timeout_share=.8,
                                        max_attempts=1, total_timeout_seconds=correction_budget,
                                    )
                                    decision['_composition_model'] = str(getattr(composition_client, 'last_model', None) or getattr(composition_client, 'model', '') or '')
                                    corrected_draft = _decode_director_json(corrected)
                                    corrected_recipe = _compile_designed_diagram_recipe(
                                        corrected_draft, approved_nodes, approved_links, scene_palette, passage,
                                        scene_durations[int(decision.get('scene_index', batch_start))], fps,
                                        f'scene-{batch_start}-composition-repair',
                                    )
                                    decision['template_recipe'] = corrected_recipe
                                    decision['template_id'] = 'generated-recipe'
                                    decision.pop('_composition_error', None)
                                    decision.pop('_composition_quality_rejected', None)
                                    decision['_composition_pass'] = 'accepted_after_correction'
                                    decision['_composition_attempt_count'] = 2
                                except Exception as correction_error:
                                    # Keep the original composition failure as the
                                    # primary status. The focused correction is a
                                    # recovery attempt; its failure should remain
                                    # visible without obscuring why recovery began.
                                    decision['_composition_pass'] = 'preserved_scene:' + type(design_error).__name__
                                    decision['_composition_error'] = (
                                        'initial=' + str(design_error)[:90]
                                        + '; correction=' + type(correction_error).__name__
                                        + ': ' + str(correction_error)[:70]
                                    )
                                    decision['_composition_attempt_count'] = 2
                            else:
                                decision['_composition_pass'] = 'preserved_scene:' + type(design_error).__name__
                    return batch_start, decisions, worker_client, None
                except Exception as batch_error:
                    batch_error.director_failure_stage = failure_stage
                    return batch_start, [], worker_client, batch_error

            completed = {}
            failed_scene_indices = []
            scene_batch_failures = []
            executor = ThreadPoolExecutor(max_workers=min(scene_workers, max(1, len(batches))), thread_name_prefix="director-scene")
            try:
                futures = {executor.submit(_direct_batch, batch): batch[0] for batch in batches}
                stage_remaining = max(0.0, min(scene_stage_timeout, deadline - time.monotonic() - repair_timeout))
                done, pending = wait(futures, timeout=stage_remaining)
                for future in done:
                    start, decisions, worker_client, batch_error = future.result()
                    completed[start] = (decisions, worker_client, batch_error)
                for future in pending:
                    future.cancel()
                    completed[futures[future]] = ([], llm_client, TimeoutError("director scene planning deadline exceeded"))
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            for batch_start, batch_end, _batch_lines, _batch_prompt in batches:
                decisions, worker_client, batch_error = completed[batch_start]
                if batch_error is not None:
                    failed_scene_indices.extend(range(batch_start, batch_end))
                    scene_batch_failures.append({
                        "scene_indices": list(range(batch_start, batch_end)),
                        "error_type": type(batch_error).__name__,
                        "failure_stage": getattr(batch_error, 'director_failure_stage', 'deadline'),
                        "reason": str(batch_error)[:400],
                    })
                    route = _model_route_record("scene_direction", worker_client, range(batch_start, batch_end))
                    route['status'] = 'failed'
                    model_routes.append(route)
                    metadata.setdefault("scene_attempt_history", []).extend(list(route.get("attempt_history") or []))
                    print(f"   [LLM Director] Scene batch {batch_start}-{batch_end - 1} failed; only these beats will use local direction ({type(batch_error).__name__}: {str(batch_error)[:240]})")
                    continue
                for decision in decisions:
                    is_diagram = (
                        str(decision.get("frame_mode") or "").lower() == "diagram"
                        or str(decision.get("type") or "").lower() == "diagram"
                        or str(decision.get("_frame_mode_normalized") or "").startswith("diagram_")
                    )
                    if not is_diagram:
                        continue
                    recipe = decision.get("template_recipe")
                    composition_status = str(decision.get("_composition_pass") or "")
                    if isinstance(recipe, Mapping) and composition_status in {"accepted", "accepted_after_correction", "initial_composition_valid"}:
                        continue
                    scene_index = int(decision.get("scene_index", batch_start))
                    passage = scene_texts[scene_index] if scene_index < len(scene_texts) else ""
                    if _recover_explanatory_decision(decision, passage):
                        metadata.setdefault("grounded_specialist_recovery_scene_indices", []).append(scene_index)
                        continue
                    # Geometry failure does not invalidate separately approved
                    # narration-grounded copy. Keep that short selection over
                    # the verified media; the film-wide copy budget and cooldown
                    # below still decide whether it is displayed.
                    preserved_copy = (
                        str(decision.get('overlay_policy') or '') == 'single_short_highlight'
                        and bool(str(decision.get('display_text') or '').strip())
                        and _valid_director_copy(passage, decision.get('display_text'), decision.get('source_span'))
                    )
                    copy_fields = {key: decision.get(key) for key in ('display_text', 'source_span', 'emphasis_spans')}
                    asset = media_items[scene_index] if media_items and scene_index < len(media_items) and isinstance(media_items[scene_index], Mapping) else {}
                    media_kind = str(asset.get("media_type") or asset.get("type") or "").lower()
                    media_component = VisualSceneType.SMART_VIDEO.value if media_kind in {"video", "youtube", "clip", "mp4"} else VisualSceneType.SMART_IMAGE.value
                    decision.update({
                        "type": media_component,
                        "variant": "full_bleed" if media_component == VisualSceneType.SMART_VIDEO.value else "full_bleed_cinematic",
                        "frame_mode": "media_only",
                        "overlay_policy": "none",
                        "display_text": "",
                        "source_span": "",
                        "emphasis_spans": [],
                        "template_id": "full-bleed",
                        "template_recipe": {},
                        "diagram_nodes": [],
                        "motion_cues": [cue for cue in decision.get("motion_cues", []) if isinstance(cue, Mapping) and cue.get("element") == "image"][:1],
                        "visual_intent": _composition_media_fallback_intent(
                            scene_texts[scene_index] if scene_index < len(scene_texts) else "",
                            media_kind,
                        ),
                        "_composition_fallback": composition_status or "missing_recipe",
                    })
                    if preserved_copy:
                        decision.update(copy_fields)
                        decision.update(type=VisualSceneType.KINETIC_TEXT.value,
                                        variant='mask_slide_up', frame_mode='kinetic',
                                        visual_role='media_overlay', template_id='keyword-reveal',
                                        overlay_policy='single_short_highlight')
                        decision['visual_intent'] = (
                            'Keep the verified subject visible; reveal only this exact narration-grounded '
                            'highlight with a tight text-sized backing and separate caption band: '
                            + str(copy_fields['display_text']))
                        metadata.setdefault('preserved_composition_copy_scene_indices', []).append(scene_index)
                    metadata.setdefault("composition_fallback_scene_indices", []).append(scene_index)
                scene_decisions.extend(decisions)
                route = _model_route_record(
                    "scene_direction",
                    worker_client,
                    range(batch_start, batch_end),
                )
                model_routes.append(route)
                metadata.setdefault("scene_attempt_history", []).extend(
                    list(route.get("attempt_history") or [])
                )
            if failed_scene_indices:
                metadata["local_fallback_scene_indices"] = sorted(set(failed_scene_indices))
                metadata["scene_batch_failures"] = scene_batch_failures

        if not scene_decisions:
            failure_type = next((row.get("error_type") for row in scene_batch_failures if row.get("error_type")), "")
            reason = f"request_error:{failure_type}" if failure_type else "invalid_scene_array"
            metadata.update({"status": "deterministic_fallback", "reason": reason})
            return None
        # A schema-valid plan can still be visually poor. Revise only the
        # excessive/sparse diagrams, inside the same film-wide deadline.
        diagrams = [row for row in scene_decisions if str(row.get('type') or '').lower() == 'diagram' or row.get('frame_mode') == 'diagram']
        excessive = len(diagrams) >= 3 and len(diagrams) / max(1, len(scene_decisions)) >= .6
        def thin_diagram(row):
            if row.get('_composition_pass') == 'grounded_specialist_recovery':
                return False
            # Two abstract labels joined by a line do not explain physical
            # action. Keep a deliberately composed two-actor explanation when
            # it supplies meaningful geometry, imagery or a validated recipe.
            if row.get('_composition_quality_rejected'):
                return True
            nodes = row.get('diagram_nodes') or []
            return len(nodes) <= 2 and not row.get('template_recipe') and not any(
                node.get('x') is not None and node.get('y') is not None
                for node in nodes if isinstance(node, Mapping))

        thin_rows = [row for row in diagrams if thin_diagram(row)]
        revision_rows = [row for row in diagrams if excessive or thin_diagram(row) or 'too sparse' in str(row.get('_composition_error') or '')]
        if excessive:
            # Preserve the most informative explanatory beat; restore visual
            # contrast around it, including a media-led opening when needed.
            retained = sorted(revision_rows, key=lambda row: (row.get('scene_index') != 0, float(row.get('editorial_priority') or 0)))[:max(1,len(diagrams)-2)]
            revision_rows = [row for row in revision_rows if row in thin_rows or row in retained or 'too sparse' in str(row.get('_composition_error') or '')]
        if revision_rows:
            metadata['quality_revisions'] = []
            for round_index in range(2):
                remaining = min(20.0, deadline - time.monotonic() - repair_timeout)
                if remaining < 6:
                    metadata['quality_revisions'].append({'status':'budget_exhausted','scene_indices':[row['scene_index'] for row in revision_rows]})
                    break
                try:
                    revision_deadline = time.monotonic() + remaining
                    make_client = locals().get('_batch_client', lambda: llm_client)
                    revision_batch_size = max(1, min(8, scene_batch_size))
                    revision_chunks = [revision_rows[i:i + revision_batch_size] for i in range(0, len(revision_rows), revision_batch_size)]
                    def revise_batch(chunk):
                        chunk_indices = [row['scene_index'] for row in chunk]
                        chunk_indices_set = set(chunk_indices)
                        worker = make_client()
                        call_budget = revision_deadline - time.monotonic()
                        if call_budget < 1:
                            raise TimeoutError('scene revision deadline exhausted')
                        owned_scenes = [
                            {
                                'scene_index': idx,
                                'narration': scene_texts[idx],
                                'previous': scene_texts[idx - 1] if idx else '',
                                'next': scene_texts[idx + 1] if idx + 1 < len(scene_texts) else '',
                            }
                            for idx in chunk_indices
                        ]
                        indices_label = ', '.join(map(str, chunk_indices))
                        token_budget = min(4000, max(1800, len(chunk) * 600))
                        response = _bounded_chat(
                            worker, call_budget,
                            user_prompt='Revise ONLY scenes ' + indices_label + '. The film overuses sparse diagrams. Retain the full story arc and niche. Choose verified media or a grounded specialist that explains this beat. Preserve the explanatory purpose. You may return a grounded specialist or a materially different Diagram recipe with approved actors and relationships; do not substitute a generic title for a mechanism. Return supported components only. Keep approved copy exact, optional and brief; do not invent facts. Return complete scene rows for each owned scene from the usual schema.\n' + user_prompt + '\nOwned scenes:\n' + json.dumps(owned_scenes),
                            system_prompt=_LLM_DIRECTOR_PROMPT, json_mode=True, json_schema=scene_response_schema,
                            json_schema_name='vidnets_scene_quality_revision_v1', temperature=.35, max_tokens=token_budget,
                            max_attempts=1, total_timeout_seconds=call_budget, primary_timeout_share=.8
                        )
                        rows = _decode_director_json(response).get('scenes') or []
                        return [item for item in rows if isinstance(item, Mapping) and item.get('scene_index') in chunk_indices_set], worker
                    revision_pool = ThreadPoolExecutor(max_workers=min(locals().get('scene_workers', 4), len(revision_chunks)), thread_name_prefix='director-revision')
                    revised, revision_failures = [], []
                    revision_futures = {revision_pool.submit(revise_batch, chunk): [row['scene_index'] for row in chunk] for chunk in revision_chunks}
                    try:
                        done, pending = wait(revision_futures, timeout=max(.01, revision_deadline - time.monotonic()))
                        for future in done:
                            chunk_indices = revision_futures[future]
                            try:
                                rows, worker = future.result()
                                revised.extend(rows)
                                model_routes.append(_model_route_record('visual_quality_revision', worker, chunk_indices))
                            except Exception as exc:
                                for idx in chunk_indices:
                                    revision_failures.append({'scene_index': idx, 'error_type': type(exc).__name__})
                        for future in pending:
                            future.cancel()
                            for idx in revision_futures[future]:
                                revision_failures.append({'scene_index': idx, 'error_type': 'TimeoutError'})
                    finally:
                        revision_pool.shutdown(wait=False, cancel_futures=True)
                    requested = {row['scene_index'] for row in revision_rows}
                    supported_types = set(_director_runtime_capabilities()['visual_systems'])
                    accepted = {}
                    for replacement in revised:
                        if not isinstance(replacement, dict) or replacement.get('scene_index') not in requested or replacement.get('type') not in supported_types:
                            continue
                        idx = replacement['scene_index']
                        _normalize_conflicting_frame_mode(replacement, scene_texts[idx])
                        recipe = replacement.get('template_recipe')
                        if recipe:
                            try:
                                recipe = validate_template_recipe(recipe)
                                if not _recipe_is_grounded(recipe, scene_texts[idx]) or not _recipe_supports_visual_type(recipe, replacement['type'], bool((replacement.get('map_shot') or {}).get('enabled'))):
                                    continue
                                if replacement.get('frame_mode') == 'diagram' and _diagram_recipe_quality_error(recipe, scene_durations[idx], fps):
                                    continue
                                replacement['template_recipe'] = recipe
                                replacement['_composition_pass'] = 'initial_composition_valid'
                            except (TypeError, ValueError):
                                continue
                        elif replacement.get('frame_mode') == 'diagram' and not _recover_explanatory_decision(replacement, scene_texts[idx]):
                            continue
                        accepted[idx] = replacement
                    for row in scene_decisions:
                        if row['scene_index'] in accepted:
                            replacement = accepted[row['scene_index']]
                            row.clear(); row.update(replacement)
                    metadata['quality_revisions'].append({'round':round_index+1,'status':'accepted' if requested <= set(accepted) else 'partial','scene_indices':sorted(accepted), 'failures':revision_failures})
                    revision_rows = [row for row in revision_rows if row['scene_index'] not in accepted]
                    if not revision_rows:
                        break
                except Exception as exc:
                    metadata['quality_revisions'].append({'round':round_index+1,'status':'failed','error_type':type(exc).__name__})
                    break
            metadata['visual_quality_status'] = 'needs_revision' if revision_rows else 'revised'
            metadata['unresolved_visual_scene_indices'] = [row['scene_index'] for row in revision_rows]
            if revision_rows:
                # Provider failures must not force an empty explanation into
                # export. Preserve verified source media without inventing
                # replacement copy and report the local treatment honestly.
                fallback_indices = []
                semantic_indices = []
                for row in revision_rows:
                    idx = row['scene_index']
                    if _recover_explanatory_decision(row, scene_texts[idx]):
                        semantic_indices.append(idx)
                        continue
                    asset = media_items[idx] if media_items and idx < len(media_items) else {}
                    asset_ref = str(asset.get('src') or asset.get('path') or '').lower()
                    # Early planning deliberately precedes retrieval. Bind
                    # missing media during reconciliation rather than blocking
                    # the film because this stage has no downloaded file yet.
                    video = str(asset.get('media_type') or asset.get('type') or '').lower() in {'video','youtube','clip'} or asset_ref.endswith(('.mp4','.webm','.mov','.m4v'))
                    attempt = {key:row[key] for key in ('_composition_pass','_composition_model','_composition_error') if key in row}
                    row.clear()
                    row.update({'scene_index':idx, 'type':'SmartVideo' if video else 'SmartImage',
                                'variant':'full_bleed_cinematic', 'frame_mode':'media_only', 'overlay_policy':'none',
                                'display_text':'', 'source_span':'', 'emphasis_spans':[], 'template_recipe':None,
                                'motion_cues':[], 'visual_intent':'Use the verified scene media as a text-free local treatment.'})
                    row.update(attempt)
                    fallback_indices.append(idx)
                metadata['local_fallback_scene_indices'] = sorted(set(metadata.get('local_fallback_scene_indices') or []) | set(fallback_indices))
                metadata['unresolved_visual_scene_indices'] = [row['scene_index'] for row in revision_rows if row['scene_index'] not in fallback_indices + semantic_indices]
                metadata['grounded_specialist_recovery_scene_indices'] = sorted(set(metadata.get('grounded_specialist_recovery_scene_indices') or []) | set(semantic_indices))
                metadata['visual_quality_status'] = 'needs_revision' if metadata['unresolved_visual_scene_indices'] else ('degraded_media_fallback' if fallback_indices else 'recovered_semantic')
                metadata['quality_revisions'].append({'status':'local_semantic_recovery' if not fallback_indices else 'local_media_fallback','scene_indices':fallback_indices, 'semantic_scene_indices':semantic_indices})
        art_direction["display_font"] = art_direction.get("display_font") if art_direction.get("display_font") in _DIRECTOR_DISPLAY_FONT_NAMES else "Archivo Black"
        art_direction["body_font"] = art_direction.get("body_font") if art_direction.get("body_font") in _DIRECTOR_BODY_FONT_NAMES else "DM Sans"
        art_direction["accent_color"] = art_direction.get("accent_color") if re.fullmatch(r"#[0-9a-fA-F]{6}", str(art_direction.get("accent_color") or "")) else "#F2C14E"
        if niche == "crime":
            art_direction["accent_color"] = str(supplied_profile.get("palette", {}).get("accent") or "#D32F2F")
        if visual_dna is not None:
            # Keep a strict two-family pairing across every role. A previous
            # theme's DM Sans number face survived the director's display/body
            # choice and made valid plans fail the font audit at export.
            visual_dna.typography = {
                **dict(visual_dna.typography or {}),
                "display": art_direction["display_font"],
                "body": art_direction["body_font"],
                "numbers": "JetBrains Mono",
            }
            visual_dna.palette = {**dict(visual_dna.palette or {}), "accent": art_direction["accent_color"]}
        composition_fallback_indices = sorted(set(int(index) for index in metadata.get("composition_fallback_scene_indices") or []))
        metadata["composition_fallback_scene_indices"] = composition_fallback_indices
        metadata["local_fallback_scene_indices"] = sorted(set(
            int(index) for index in (list(metadata.get("local_fallback_scene_indices") or []) + composition_fallback_indices)
        ))
        if not metadata.get("visual_quality_status"):
            metadata["visual_quality_status"] = "degraded_media_fallback" if composition_fallback_indices else "directed"
            metadata.setdefault("unresolved_visual_scene_indices", [])
        metadata["composition_fallbacks"] = [
            {
                "scene_index": int(row.get("scene_index", -1)),
                "attempt_status": str(row.get("_composition_pass") or "missing_recipe"),
                "fallback": str(row.get("_composition_fallback") or ""),
            }
            for row in scene_decisions
            if row.get("_composition_fallback")
        ]
        degraded_indices = sorted(set(
            int(index) for index in (list(metadata.get("local_fallback_scene_indices") or []) + composition_fallback_indices)
        ))
        metadata.update({
            "status": "directed",
            "composition_passes": [{"scene_index": row.get('scene_index'), "status": row['_composition_pass'], "attempt_count": row.get('_composition_attempt_count', 1), "model": row.get('_composition_model', ''), "model_chain": row.get('_composition_model_chain', []), "detail": row.get('_composition_error', '')} for row in scene_decisions if row.get('_composition_pass')],
            "creative_coverage": "partial" if degraded_indices else "complete",
            "directed_scene_count": max(0, len(scene_decisions) - len(degraded_indices)),
            "fallback_scene_count": len(degraded_indices),
            "opening_fully_directed": not any(i in degraded_indices for i in range(min(3, len(scene_texts)))),
            # Preserve the legacy singular field as the video-level art
            # direction route; the stage records below identify the actual
            # model used for each independently parallelized scene batch.
            "model": str(art_route.get("selected_model") or getattr(llm_client, "model", "") or "unknown"),
            "model_routes": model_routes,
            "models_used": list(dict.fromkeys(
                str(route.get("selected_model") or "").strip()
                for route in model_routes
                if str(route.get("selected_model") or "").strip()
            )),
            "fallback_used": (
                any(bool(route.get("fallback_used")) for route in model_routes)
                or bool(metadata.get("local_fallback_scene_indices"))
                or bool(composition_fallback_indices)
                or str(art_route.get("status") or "") == "failed"
            ),
            "schema": "vidnets.edit-plan.v4",
            "art_direction": art_direction,
            "attempt_history": art_attempt_history,
            "template_catalog_version": int(list_template_catalog().get("version") or 1),
            "fallback_chain": list(getattr(llm_client, "model_chain", ()) or (getattr(llm_client, "model", ""),)),
        })
        parsed = scene_decisions

        # Validate each overlay against its own narration. Allow one repair
        # pass, then omit invalid copy instead of showing a clipped fragment.
        repair_items = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            idx = int(item.get("scene_index", -1))
            if idx < 0 or idx >= len(scene_texts):
                continue
            display = re.sub(r"\s+", " ", str(item.get("display_text") or "")).strip()
            source_span = re.sub(r"\s+", " ", str(item.get("source_span") or "")).strip()
            if not display:
                item["source_span"] = ""
                item["emphasis_spans"] = []
                continue
            if display and not _valid_director_copy(scene_texts[idx], display, source_span):
                repair_items.append({
                    "scene_index": idx,
                    "display_text": display,
                    "source_span": source_span,
                    "scene_text": str(scene_texts[idx]),
                    "previous_scene": str(scene_texts[idx - 1]) if idx > 0 else "(opening scene)",
                    "next_scene": str(scene_texts[idx + 1]) if idx + 1 < len(scene_texts) else "(final scene)",
                    "film_position": f"{idx + 1}/{len(scene_texts)}",
                })
        if repair_items and deadline - time.monotonic() >= 4.0:
            try:
                repair_call_timeout = max(.01, min(repair_timeout, deadline - time.monotonic()))
                repair_response = _bounded_chat(
                    llm_client, repair_call_timeout,
                    user_prompt=(
                        "Repair this on-screen copy once in the context of the full story arc and its surrounding beats. "
                        "Replace each display_text with one exact, grounded 1–3 word highlight (at most 5 words and 42 characters). Preserve a person's full name on pivotal named-person beats, preserve units/context with statistics, and never leave a head-to-head score without both sides; split these into separate narration-aligned beats if needed. "
                        "or return an empty display_text when no overlay is needed. source_span must be an exact supporting substring "
                        "or sentence from scene_text. Never return a full narration sentence or change the script's meaning.\n"
                        f"Full-film story map: {_director_story_map(scene_texts)}\n"
                        f"Art-director arc: {art_direction.get('story_arc', '')}\n"
                        f"Retention plan: {art_direction.get('retention_plan', '')}\n\n"
                        + json.dumps({"visual_motif": art_direction.get("visual_motif", ""), "items": repair_items}, ensure_ascii=False)
                    ),
                    system_prompt="You are a documentary copy editor. Return only the repaired fields in the requested schema.",
                    temperature=0.0,
                    max_tokens=1600,
                    json_mode=True,
                    json_schema=_COPY_REPAIR_SCHEMA,
                    json_schema_name="vidnets_copy_repair_v1",
                    max_attempts=director_retries,
                    total_timeout_seconds=repair_call_timeout,
                )
                model_routes.append(_model_route_record(
                    "copy_repair",
                    llm_client,
                    sorted(int(item["scene_index"]) for item in repair_items),
                ))
                repaired = _decode_director_json(repair_response).get("items") or []
                repaired_by_index = {int(row.get("scene_index", -1)): row for row in repaired if isinstance(row, Mapping)}
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    row = repaired_by_index.get(int(item.get("scene_index", -1)))
                    if row:
                        item["display_text"] = row.get("display_text", "")
                        item["source_span"] = row.get("source_span", "")
                        item["emphasis_spans"] = row.get("emphasis_spans", [])
            except Exception as repair_error:
                repair_route = _model_route_record(
                    "copy_repair",
                    llm_client,
                    sorted(int(item["scene_index"]) for item in repair_items),
                )
                repair_route["status"] = "failed"
                repair_route["error_type"] = type(repair_error).__name__
                model_routes.append(repair_route)
                print(f"   [LLM Director] Copy repair failed; invalid overlays will be omitted: {type(repair_error).__name__}")
        for item in parsed:
            if not isinstance(item, dict):
                continue
            idx = int(item.get("scene_index", -1))
            if idx < 0 or idx >= len(scene_texts):
                continue
            display = re.sub(r"\s+", " ", str(item.get("display_text") or "")).strip()
            source_span = re.sub(r"\s+", " ", str(item.get("source_span") or "")).strip()
            if not display:
                item["source_span"] = ""
                item["emphasis_spans"] = []
                continue
            if display and not _valid_director_copy(scene_texts[idx], display, source_span):
                item["display_text"] = ""
                item["source_span"] = ""
                item["emphasis_spans"] = []
                item["overlay_policy"] = "none"

        # The director owns a film-wide emphasis budget. This is applied after
        # copy repair so each accepted highlight remains a deliberate LLM choice.
        if (len(scene_texts) >= 6 and not any(
                str(row.get('display_text') or '').strip() for row in parsed if isinstance(row, Mapping))
                and deadline - time.monotonic() >= 8):
            # One small film-wide pass catches a parallel planner that leaves
            # all salient names and product details without editorial emphasis.
            # An empty answer remains valid; there is no heuristic replacement.
            try:
                copy_schema = {'type':'object', 'additionalProperties':False, 'required':['highlights'],
                    'properties':{'highlights':{'type':'array', 'maxItems':min(3, overlay_budget),
                        'items':{'type':'object', 'additionalProperties':False,
                            'required':['scene_index','display_text','source_span','variant','placement'],
                            'properties':{'scene_index':{'type':'integer','minimum':0,'maximum':len(scene_texts)-1},
                                'display_text':{'type':'string','minLength':1,'maxLength':42},
                                'source_span':{'type':'string','minLength':1,'maxLength':300},
                                'variant':{'type':'string','enum':list(SCENE_TYPE_VARIANTS[VisualSceneType.KINETIC_TEXT.value])},
                                'placement':{'type':'string','enum':['center','top_left','top_right','bottom_left']}}}}}}
                copy_response = _bounded_chat(llm_client, min(20, deadline-time.monotonic()),
                    user_prompt='Review this film for missed editorial emphasis. Select up to three non-adjacent pivotal named subjects, physical details, or exact facts. Return no highlights if none are useful. Each display_text must be a complete exact contiguous narration phrase, at most five words. Do not invent a heading. Choose distinct native KineticText variants and placements, with tight text-sized backings over the existing media. Keep the lower caption band clear. Prefer actual names over generic verbs.\n'+json.dumps({'niche':niche, 'story':list(enumerate(scene_texts))},ensure_ascii=False),
                    system_prompt='You are the documentary emphasis editor. Preserve exact facts and choose selective memorable typography, not captions for every sentence.',
                    temperature=.35, max_tokens=900, json_mode=True, json_schema=copy_schema,
                    json_schema_name='vidnets_film_emphasis_v1', max_attempts=1,
                    total_timeout_seconds=min(20, deadline-time.monotonic()))
                proposed = _decode_director_json(copy_response)
                metadata['opportunity_highlight_scene_indices'] = _apply_opportunity_highlights(parsed, proposed.get('highlights'), scene_texts)
                model_routes.append(_model_route_record('film_emphasis_review', llm_client))
            except Exception as emphasis_error:
                metadata['opportunity_highlight_review_error'] = type(emphasis_error).__name__
        parsed = _enforce_director_overlay_budget(
            parsed,
            scene_texts,
            director_config=director_config,
            metadata=metadata,
        )

        # Copy repair is an LLM stage too. Finalize the route summary only
        # after every director call has completed so a repair-model fallback
        # cannot disappear from the saved plan's provenance.
        metadata["model_routes"] = model_routes
        metadata["models_used"] = list(dict.fromkeys(
            str(route.get("selected_model") or "").strip()
            for route in model_routes
            if str(route.get("selected_model") or "").strip()
        ))
        metadata["fallback_used"] = any(
            bool(route.get("fallback_used")) for route in model_routes
        ) or bool(metadata.get("local_fallback_scene_indices"))

        # Map LLM type names to VisualSceneType values
        _type_map = {
            'smartimage': VisualSceneType.SMART_IMAGE.value,
            'smartvideo': VisualSceneType.SMART_VIDEO.value,
            'kinetictext': VisualSceneType.KINETIC_TEXT.value,
            'statcard': VisualSceneType.STAT_CARD.value,
            'quotecard': VisualSceneType.QUOTE_CARD.value,
            'headline': VisualSceneType.HEADLINE.value,
            'documenthighlight': VisualSceneType.DOCUMENT_HIGHLIGHT.value,
            'animatedmap': VisualSceneType.ANIMATED_MAP.value,
            'timeline': VisualSceneType.TIMELINE.value,
            'chart': VisualSceneType.CHART.value,
            'comparison': VisualSceneType.COMPARISON.value,
            'collage': VisualSceneType.COLLAGE.value,
            'personprofile': VisualSceneType.PERSON_PROFILE.value,
            'diagram': VisualSceneType.DIAGRAM.value,
            'chaptertitle': VisualSceneType.CHAPTER_TITLE.value,
        }
        geo_country_codes, geo_country_names, geo_city_names = load_geographic_catalog()
        geo_admin1_catalog = load_admin1_catalog()
        geo_known_places = geo_country_names | geo_city_names | set(geo_admin1_catalog) | set(REGION_CENTERS)
        geo_known_place_keys = {
            unicodedata.normalize("NFKD", str(place or "").casefold())
            .encode("ascii", "ignore")
            .decode("ascii")
            .strip()
            for place in geo_known_places
            if str(place or "").strip()
        }

        def grounded_map_names(map_shot, passage):
            if not isinstance(map_shot, Mapping) or map_shot.get("enabled") is not True:
                return []
            return [
                str(place).strip()
                for place in (map_shot.get("location_names") or [])
                if unicodedata.normalize("NFKD", str(place or "").casefold())
                .encode("ascii", "ignore")
                .decode("ascii")
                .strip() in geo_known_place_keys
                and re.search(re.escape(str(place).strip()), str(passage or ""), re.I)
            ][:8]

        # Validate and collect overrides
        overrides: Dict[int, Dict[str, Any]] = {}
        for item in parsed:
            if not isinstance(item, dict):
                continue
            idx = int(item.get("scene_index", -1))
            raw_type = re.sub(r'[\s_-]', '', str(item.get("type", ""))).lower()
            if idx < 0 or idx >= len(scene_texts):
                continue
            if raw_type not in _type_map:
                continue
            scene_type = _type_map[raw_type]
            raw_map_shot = item.get("map_shot") if isinstance(item.get("map_shot"), Mapping) else {}
            grounded_locations = grounded_map_names(raw_map_shot, scene_texts[idx])
            frame_mode = str(item.get("frame_mode") or "").strip().lower()
            if scene_type in {
                VisualSceneType.STAT_CARD.value,
                VisualSceneType.CHART.value,
                VisualSceneType.TIMELINE.value,
                VisualSceneType.COMPARISON.value,
            }:
                frame_mode = "data"
            elif scene_type == VisualSceneType.KINETIC_TEXT.value:
                frame_mode = "kinetic"
            elif scene_type == VisualSceneType.PERSON_PROFILE.value:
                frame_mode = "portrait"
            elif scene_type == VisualSceneType.DOCUMENT_HIGHLIGHT.value:
                frame_mode = "evidence"
            elif scene_type == VisualSceneType.DIAGRAM.value:
                frame_mode = "diagram"
            elif scene_type == VisualSceneType.QUOTE_CARD.value:
                frame_mode = "quote"
            elif scene_type == VisualSceneType.ANIMATED_MAP.value:
                frame_mode = "map" if grounded_locations else "media_only"
            elif scene_type == VisualSceneType.HEADLINE.value:
                frame_mode = "evidence"
            elif scene_type == VisualSceneType.CHAPTER_TITLE.value:
                frame_mode = "chapter"
            elif frame_mode == "map" and grounded_locations:
                scene_type = VisualSceneType.ANIMATED_MAP.value
            elif frame_mode == "portrait" and analyze_sentence_triggers(scene_texts[idx], is_first_scene=(idx == 0), context_text=scene_texts[idx]).persons:
                scene_type = VisualSceneType.PERSON_PROFILE.value
            elif frame_mode == "evidence":
                scene_type = VisualSceneType.DOCUMENT_HIGHLIGHT.value if scene_type == VisualSceneType.DOCUMENT_HIGHLIGHT.value else VisualSceneType.SMART_IMAGE.value
            elif frame_mode == "data":
                scene_type = VisualSceneType.CHART.value if scene_type == VisualSceneType.CHART.value else (VisualSceneType.STAT_CARD.value if scene_type == VisualSceneType.STAT_CARD.value else scene_type)
            elif frame_mode == "diagram":
                scene_type = VisualSceneType.DIAGRAM.value
            elif frame_mode == "quote":
                scene_type = VisualSceneType.QUOTE_CARD.value
            elif frame_mode == "kinetic":
                scene_type = VisualSceneType.KINETIC_TEXT.value
            else:
                frame_mode = "media_only"
                asset = media_items[idx] if media_items and idx < len(media_items) and isinstance(media_items[idx], Mapping) else {}
                asset_ref = str((asset or {}).get("src") or (asset or {}).get("path") or "").lower()
                asset_kind = str((asset or {}).get("media_type") or (asset or {}).get("type") or "").lower()
                scene_type = VisualSceneType.SMART_VIDEO.value if asset_kind in {"video", "youtube", "clip"} or asset_ref.endswith((".mp4", ".webm", ".mov", ".m4v")) else VisualSceneType.SMART_IMAGE.value
            variant = str(item.get("variant") or "").strip()
            if variant not in SCENE_TYPE_VARIANTS.get(scene_type, ()):
                requested_variant = variant
                if scene_type == VisualSceneType.ANIMATED_MAP.value and grounded_locations:
                    variant = "country_spotlight"
                elif scene_type == VisualSceneType.SMART_VIDEO.value:
                    variant = "full_bleed"
                elif scene_type == VisualSceneType.SMART_IMAGE.value:
                    variant = "full_bleed_cinematic"
                else:
                    supported = SCENE_TYPE_VARIANTS.get(scene_type) or ("default",)
                    variant = supported[(creative_seed + idx) % len(supported)]
                invalid_variants = metadata.setdefault("invalid_variant_fallbacks", [])
                invalid_variants.append({"scene_index": idx, "requested": requested_variant, "resolved": variant})
            focus = str(item.get("text_focus") or "").strip()
            # The director may emphasize an exact narrated phrase, never invent a quote.
            if focus:
                exact_focus = re.search(re.escape(focus), str(scene_texts[idx]), re.I)
                focus = exact_focus.group(0) if exact_focus else ""
            # Never carry an LLM's unrecognised map payload into the saved
            # plan. A map request is optional; an invalid place must become a
            # media or diagram beat instead of failing export after retrieval.
            validated_map_shot = {}
            if grounded_locations:
                validated_map_shot = dict(raw_map_shot)
                validated_map_shot["enabled"] = True
                validated_map_shot["location_names"] = list(grounded_locations)
                map_media = media_items[idx] if media_items and idx < len(media_items) and isinstance(media_items[idx], Mapping) else {}
                validated_map_shot["render_style"] = _safe_map_render_style(
                    raw_map_shot.get("render_style"), grounded_locations, map_media,
                )
                requested_motion = str(raw_map_shot.get("motion_profile") or "auto").strip().lower()
                validated_map_shot["motion_profile"] = requested_motion if requested_motion in {"auto", *_MAP_MOTION_PROFILES} else "auto"
                valid_features = []
                for feature_id in raw_map_shot.get("feature_ids") if isinstance(raw_map_shot.get("feature_ids"), list) else []:
                    feature_code = str(feature_id or "").strip().upper()
                    feature_name = str(geo_admin1_catalog.get(feature_code) or "").strip()
                    if feature_code and feature_name and re.search(re.escape(feature_name), str(scene_texts[idx]), re.I):
                        valid_features.append(feature_code)
                validated_map_shot["feature_ids"] = valid_features
                # A route id is only safe when its evidence is carried by the
                # scene manifest. Keep coordinate/camera data, but drop an
                # unsupported opaque id so validation cannot accept a false
                # journey.
                if validated_map_shot.get("route_id") and not any(
                    isinstance(ref, Mapping) and str(ref.get("route_id") or "").strip() == str(validated_map_shot.get("route_id") or "").strip()
                    for ref in ((media_items[idx] if media_items and idx < len(media_items) else {}),)
                ):
                    validated_map_shot["route_id"] = ""
            elif str(item.get("frame_mode") or "").strip().lower() == "map":
                # Adjacent narration may establish the single site. Retain
                # the bounded visual choice while the grounded-map builder
                # independently resolves the place from nearby script.
                requested_style = str(raw_map_shot.get("render_style") or "").strip().lower()
                validated_map_shot = {"render_style": requested_style if requested_style in _MAP_RENDER_STYLES else "vector_editorial"}
            overrides[idx] = {
                "type": scene_type,
                "variant": variant,
                "visual_intent": str(item.get("visual_intent") or "").strip()[:180],
                "frame_mode": frame_mode,
                "visual_role": item.get('visual_role') if item.get('visual_role') in {'media_overlay', 'primary_explanation', 'map_canvas'} else 'media_overlay',
                "_visual_role_explicit": item.get('visual_role') in {'media_overlay', 'primary_explanation', 'map_canvas'},
                "overlay_policy": str(item.get("overlay_policy") or "none").strip().lower(),
                "editorial_priority": max(0.0, min(1.0, float(item.get("editorial_priority") or 0.0))),
                "camera": item.get("camera"),
                "entrance": item.get("entrance"),
                "transition": item.get("transition"),
                "text_focus": focus,
                "display_text": str(item.get("display_text") or "").strip(),
                "source_span": str(item.get("source_span") or "").strip(),
                "emphasis_spans": item.get("emphasis_spans") or [],
                "template_id": str(item.get("template_id") or "full-bleed"),
                "template_recipe": item.get("template_recipe"),
                "map_shot": validated_map_shot,
                "graphic_theme": str(item.get("graphic_theme") or "").strip().lower() if str(item.get("graphic_theme") or "").strip().lower() in _GRAPHIC_THEMES else ("editorial_light" if str(item.get("frame_mode") or "").strip().lower() == "data" else "cinematic_dark"),
                "text_treatment": _safe_text_treatment(
                    item.get("text_treatment"),
                    visual_dna.palette if visual_dna else {},
                    str(item.get("overlay_policy") or "none").strip().lower() == "single_short_highlight" and bool(str(item.get("display_text") or "").strip()),
                ),
                "motion_cues": item.get("motion_cues") if isinstance(item.get("motion_cues"), list) else [],
                "diagram_nodes": item.get("diagram_nodes") if isinstance(item.get("diagram_nodes"), list) else [],
                "diagram_links": validated_diagram_links(
                    item.get("diagram_links"),
                    validated_diagram_nodes(item.get("diagram_nodes"), scene_texts[idx]),
                    scene_texts[idx],
                ),
                "media_plan": (
                    dict(item.get("media_plan"))
                    if isinstance(item.get("media_plan"), Mapping)
                    else {"visual_beats": 1, "media_preference": "either", "rhythm": "single_hold"}
                ),
                "placement": item.get("placement") if item.get("placement") in {"top_left", "top_right", "bottom_left", "bottom_right", "center", "none"} else "none",
            }

        missing_scene_indices = sorted(set(range(len(scene_texts))) - set(overrides))
        if missing_scene_indices:
            metadata["local_fallback_scene_indices"] = sorted(set(
                list(metadata.get("local_fallback_scene_indices") or []) + missing_scene_indices
            ))
            if not overrides:
                metadata.update({"status": "deterministic_fallback", "reason": "incomplete_scene_plan"})
                return None
            metadata["fallback_used"] = True
            print(f"   [LLM Director] LLM directed {len(overrides)}/{len(scene_texts)} scenes; only missing beats use local direction")

        print(f"   [LLM Director] Successfully mapped {len(overrides)}/{len(scene_texts)} scene types")

        # Now build scenes using deterministic pipeline but with LLM type overrides
        deterministic_scenes = _plan_deterministic_scenes(
            scene_texts, scene_durations, media_items,
            word_timings=word_timings, visual_dna=visual_dna,
        )

        # Apply LLM overrides to scene types (keep deterministic props & timing)
        for scene in deterministic_scenes:
            idx = scene.scene_index
            if idx in overrides:
                decision = overrides[idx]
                scene.graphic_theme = str(decision.get("graphic_theme") or "cinematic_dark")
                raw_media_plan = decision.get("media_plan") if isinstance(decision.get("media_plan"), Mapping) else {}
                try:
                    visual_beats = max(1, min(4, int(raw_media_plan.get("visual_beats") or 1)))
                except (TypeError, ValueError):
                    visual_beats = 1
                media_preference = str(raw_media_plan.get("media_preference") or "either").strip().lower()
                if media_preference not in {"clip", "images", "either"}:
                    media_preference = "either"
                rhythm = str(raw_media_plan.get("rhythm") or "").strip().lower()
                expected_rhythm = {1: "single_hold", 2: "two_beat", 3: "three_beat", 4: "four_beat"}[visual_beats]
                if rhythm != expected_rhythm:
                    rhythm = expected_rhythm
                scene.media_plan = {
                    "visual_beats": visual_beats,
                    "media_preference": media_preference,
                    "rhythm": rhythm,
                    "decision_source": "llm_director",
                }
                llm_type = decision["type"]
                current_type = scene.primary_visual.type
                raw_text = scene.narration or ""
                triggers = analyze_sentence_triggers(raw_text, is_first_scene=(idx == 0), context_text=raw_text)
                # Semantic visuals need grounded facts. Keep the deterministic visual
                # whenever the script cannot support a chart or geographic graphic.
                # The opening may be a chart, evidence reveal, portrait,
                # mechanism, or map. Keep the model's grounded choice rather
                # than forcing every niche through the same title opener.
                if llm_type == VisualSceneType.CHART.value and len(triggers.numbers) < 2:
                    llm_type = current_type
                if idx > 0 and _is_single_financial_stat(raw_text) and llm_type in {
                    VisualSceneType.SMART_IMAGE.value, VisualSceneType.SMART_VIDEO.value,
                    VisualSceneType.CHART.value,
                } and not decision.get("template_recipe"):
                    # One narrated financial figure has no series to chart;
                    # force the grounded number into the short counter system
                    # instead of leaving it as an unrelated stock-image beat.
                    llm_type = VisualSceneType.STAT_CARD.value
                if llm_type == VisualSceneType.ANIMATED_MAP.value and not (
                    triggers.country_corridor or triggers.countries or triggers.locations
                ) and not grounded_map_names(decision.get("map_shot"), raw_text):
                    llm_type = current_type
                if triggers.quotes and llm_type in {
                    VisualSceneType.SMART_IMAGE.value, VisualSceneType.SMART_VIDEO.value,
                } and not decision.get("template_recipe"):
                    llm_type = VisualSceneType.QUOTE_CARD.value

                # Preserve the director's semantic choice. A repeated scalar is
                # still a scalar; changing it to a chart or map merely for
                # variety invents a different visual argument. The sequence
                # grammar can vary compatible scalar layouts and entrances.
                if idx > 0 and llm_type == VisualSceneType.STAT_CARD.value:
                    previous_scene = deterministic_scenes[idx - 1] if idx - 1 < len(deterministic_scenes) else None
                    prev_scene_type = previous_scene.primary_visual.type if previous_scene else None
                    if prev_scene_type == VisualSceneType.STAT_CARD.value:
                        decision["visual_intent"] = str(decision.get("visual_intent") or "Reveal the exact narrated scalar") + "; vary scalar layout and reveal, preserving its meaning."

                if llm_type != current_type:
                    # Re-analyze with the forced type to get proper variant and props
                    variants = SCENE_TYPE_VARIANTS.get(llm_type, ())
                    requested_variant = str(decision.get("variant") or "")
                    variant = requested_variant if requested_variant in variants else (
                        "hero_counter_large" if llm_type == VisualSceneType.STAT_CARD.value and _is_single_financial_stat(raw_text)
                        else (variants[idx % len(variants)] if variants else "default")
                    )

                    # Re-build props for the new type
                    card_theme = visual_dna.chart_style if visual_dna else "white_editorial"
                    if card_theme not in ("white_editorial", "dark_modern"):
                        card_theme = "white_editorial"

                    if llm_type == VisualSceneType.CHART.value:
                        pv_props = _build_chart_props(variant, raw_text, triggers.numbers, card_theme, [])
                    elif llm_type == VisualSceneType.ANIMATED_MAP.value:
                        pv_props = _build_map_props(variant, triggers, raw_text)
                    elif llm_type == VisualSceneType.STAT_CARD.value:
                        pv_props = _build_stat_card_props(variant, raw_text, triggers.numbers, triggers)
                    elif llm_type == VisualSceneType.TIMELINE.value:
                        pv_props = _build_timeline_props(variant, raw_text, triggers.dates, triggers)
                    elif llm_type == VisualSceneType.PERSON_PROFILE.value:
                        pv_props = _build_person_props(variant, raw_text, triggers.persons, triggers)
                    elif llm_type == VisualSceneType.DIAGRAM.value:
                        pv_props = _build_diagram_props(variant, raw_text, triggers)
                    elif llm_type == VisualSceneType.KINETIC_TEXT.value:
                        pv_props = _build_kinetic_text_props(variant, raw_text, triggers)
                    elif llm_type == VisualSceneType.COMPARISON.value:
                        pv_props = _build_comparison_props(variant, raw_text, triggers.numbers, triggers)
                    elif llm_type == VisualSceneType.HEADLINE.value:
                        pv_props = _build_headline_props(variant, raw_text, triggers.dates, triggers)
                    elif llm_type == VisualSceneType.DOCUMENT_HIGHLIGHT.value:
                        pv_props = _build_document_props(variant, raw_text, triggers)
                    elif llm_type == VisualSceneType.QUOTE_CARD.value:
                        pv_props = {"variant": variant}
                        if triggers.quotes:
                            pv_props["quote"] = triggers.quotes[0]
                        pv_props["speaker"] = (triggers.persons[0] if triggers.persons else "DIRECT STATEMENT").upper()
                    elif llm_type == VisualSceneType.CHAPTER_TITLE.value:
                        pv_props = {"variant": variant, "kicker": f"ACT 0{idx + 1}", "title": raw_text[:60].upper().rstrip('.,;:')}
                    else:
                        pv_props = {"variant": variant}

                    # A validated map request is itself sufficient to render a
                    # grounded local map plate even when sentence-trigger
                    # heuristics did not classify the place. Do not let the
                    # generic "no_data" guard silently turn it back into a
                    # chapter card or image.
                    if llm_type == VisualSceneType.ANIMATED_MAP.value and grounded_map_names(decision.get("map_shot"), raw_text):
                        pv_props.pop("no_data", None)
                    if not pv_props.get("no_data"):
                        scene.primary_visual.type = llm_type
                        # Builders may intentionally canonicalize an unsafe
                        # requested variant (for example a one-place map
                        # requested as a flight arc). Keep the renderer's
                        # scene-level variant in sync with the grounded props.
                        scene.primary_visual.variant = str(pv_props.get("variant") or variant)
                        scene.primary_visual.props = pv_props
                        if llm_type == VisualSceneType.COMPARISON.value:
                            decision["template_id"] = "split-screen"
                        elif llm_type == VisualSceneType.CHART.value:
                            decision["template_id"] = "bar-chart"
                        elif llm_type == VisualSceneType.ANIMATED_MAP.value:
                            decision["template_id"] = "map-route"
                        elif llm_type == VisualSceneType.KINETIC_TEXT.value:
                            decision["template_id"] = "kinetic-statement"
                        elif llm_type == VisualSceneType.STAT_CARD.value:
                            decision["template_id"] = "stat-counter"
                        elif llm_type == VisualSceneType.SMART_IMAGE.value:
                            decision["template_id"] = "full-bleed"

                elif decision.get("variant") and decision["variant"] in SCENE_TYPE_VARIANTS.get(current_type, ()):
                    scene.primary_visual.variant = decision["variant"]
                    scene.primary_visual.props = {**(scene.primary_visual.props or {}), "variant": decision["variant"]}

                intent = decision.get("visual_intent")
                if intent:
                    scene.visual_intent = intent
                frame_mode = str(decision.get("frame_mode") or "media_only").strip().lower()
                adjacent_site_context = _adjacent_site_location_context(idx, scene_texts, raw_text)
                if frame_mode == "media_only" and adjacent_site_context:
                    # The script explicitly describes a site and names its
                    # city in the adjacent beat. Preserve that geography as a
                    # single-place locator, without drawing a route.
                    frame_mode = "map"
                    decision["visual_intent"] = "Locate the narrated site with a restrained map move using the adjacent named city."
                overlay_policy = str(decision.get("overlay_policy") or "none").strip().lower()
                if idx > 0 and _is_single_financial_stat(raw_text) and llm_type == VisualSceneType.STAT_CARD.value:
                    # The animated value and factual label are the emphasis on
                    # this beat; a second floating highlight would compete
                    # with the counter instead of clarifying it.
                    overlay_policy = "none"
                    scene.visual_intent = "Reveal the single narrated financial amount as a short, grounded animated counter over relevant media."
                scene.text["frame_mode"] = frame_mode
                scene.text["overlay_policy"] = overlay_policy
                scene.text["editorial_priority"] = float(decision.get("editorial_priority") or 0.0)
                requested_visual_role = str(decision.get("visual_role") or "media_overlay").strip().lower()
                opening_hook = bool(
                    idx == 0
                    and frame_mode in {"kinetic", "chapter"}
                    and overlay_policy == "single_short_highlight"
                    and str(decision.get("display_text") or "").strip()
                    and not (decision.get('_visual_role_explicit') and requested_visual_role == 'media_overlay')
                )
                if llm_type == VisualSceneType.ANIMATED_MAP.value:
                    scene.visual_role = "map_canvas"
                elif requested_visual_role == "primary_explanation" or opening_hook:
                    scene.visual_role = "primary_explanation"
                else:
                    scene.visual_role = "media_overlay"
                scene.visual_role_source = "llm_director_opening_hook" if opening_hook else "llm_director"
                # Keep text_focus out of the render-facing scene.text object
                # unless it is the approved short overlay. Several Remotion
                # components use scene.text.focus as a fallback title; storing
                # a full sentence here would recreate the very heading cards
                # the sparse storyboard contract is meant to prevent.
                if decision.get("text_focus") and overlay_policy == "single_short_highlight" and decision.get("display_text"):
                    scene.text["focus"] = str(decision.get("display_text") or "").strip()
                else:
                    scene.text.pop("focus", None)
                display_text = (decision.get("display_text") or "") if overlay_policy == "single_short_highlight" else ""
                scene_asset = media_items[idx] if media_items and idx < len(media_items) and isinstance(media_items[idx], Mapping) else {}
                safe_placement = _safe_director_placement(decision.get("placement"), scene_asset, frame_mode)
                scene.text["position"] = safe_placement
                scene.text.update(_safe_text_treatment(
                    decision.get("text_treatment"),
                    visual_dna.palette if visual_dna else {},
                    bool(display_text),
                ))
                if display_text:
                    scene.text['copy_source']='llm'
                    scene.display_text = display_text
                    scene.source_span = decision.get("source_span") or ""
                    scene.emphasis_spans = _normalized_emphasis_spans(display_text, decision.get("emphasis_spans"))
                    scene.text["display_text"] = display_text

                approved_ids = set(live_templates_by_id)
                template_id = decision.get("template_id")
                if template_id in approved_ids:
                    catalog_entry = live_templates_by_id.get(str(template_id)) or {}
                    if catalog_entry.get("family") == "recipe" and isinstance(catalog_entry.get("recipe"), Mapping):
                        try:
                            catalog_recipe = validate_template_recipe(catalog_entry["recipe"])
                            if scene.primary_visual.type == VisualSceneType.DIAGRAM.value:
                                quality_error = _diagram_recipe_quality_error(catalog_recipe, scene.duration, fps)
                                if quality_error:
                                    raise ValueError(quality_error)
                            if (
                                _recipe_is_grounded(catalog_recipe, raw_text)
                                and _recipe_supports_visual_type(
                                    catalog_recipe,
                                    scene.primary_visual.type,
                                    bool((decision.get("map_shot") or {}).get("enabled")) if isinstance(decision.get("map_shot"), Mapping) else False,
                                )
                            ):
                                scene.template_recipe = catalog_recipe
                                scene.template_id = "generated-recipe"
                            else:
                                scene.template_id = "full-bleed"
                        except ValueError:
                            scene.template_id = "full-bleed"
                    else:
                        scene.template_id = template_id
                recipe = decision.get("template_recipe")
                if template_id == "generated-recipe" and isinstance(recipe, Mapping):
                    try:
                        safe_recipe = validate_template_recipe(recipe)
                        if scene.primary_visual.type == VisualSceneType.DIAGRAM.value:
                            quality_error = _diagram_recipe_quality_error(safe_recipe, scene.duration, fps)
                            if quality_error:
                                raise ValueError(quality_error)
                        if (
                            _recipe_is_grounded(safe_recipe, raw_text)
                            and _recipe_supports_visual_type(
                                safe_recipe,
                                scene.primary_visual.type,
                                bool((decision.get("map_shot") or {}).get("enabled")) if isinstance(decision.get("map_shot"), Mapping) else False,
                            )
                        ):
                            scene.template_recipe = safe_recipe
                            scene.template_id = "generated-recipe"
                        else:
                            scene.template_id = "full-bleed"
                    except ValueError:
                        scene.template_id = "full-bleed"

                map_shot = decision.get("map_shot") or {}
                if isinstance(map_shot, Mapping) and map_shot.get("enabled"):
                    known_places = geo_country_names | geo_city_names
                    admin1_by_name = geo_admin1_catalog
                    valid_admin1_ids = set(admin1_by_name.values())
                    safe_places = []
                    for place in map_shot.get("location_names", []):
                        candidate = str(place or "").strip()
                        if candidate and candidate.casefold() in known_places and re.search(re.escape(candidate), raw_text, re.I):
                            safe_places.append(candidate)
                    supplied_route_ids = {
                        str(source.get("route_id") or "")
                        for source in (media_items or []) if isinstance(source, Mapping)
                    }
                    requested_route = str(map_shot.get("route_id") or "")
                    scene.map_shot = {
                        "enabled": bool(safe_places),
                        "location_names": safe_places[:8],
                        "feature_ids": [
                            str(feature_id).strip().upper()
                            for feature_id in map_shot.get("feature_ids", [])
                            if str(feature_id).strip().upper() in valid_admin1_ids
                            and any(name.casefold() in raw_text.casefold() and admin1_by_name.get(name.casefold()) == str(feature_id).strip().upper() for name in safe_places)
                        ][:8],
                        "route_id": requested_route if requested_route and requested_route in supplied_route_ids else "",
                        "source_ids": [str(source_id) for source_id in map_shot.get("source_ids", []) if str(source_id).strip()][:8],
                        "motion_profile": str(map_shot.get("motion_profile") or "auto").strip().lower(),
                        "render_style": _safe_map_render_style(
                            map_shot.get("render_style"), safe_places, scene_asset,
                        ),
                    }
                    if scene.map_shot["enabled"]:
                        scene.primary_visual.props = apply_grounded_map_country_props(
                            scene.primary_visual.props or {},
                            scene.map_shot,
                        )
                        scene.primary_visual.props["map_shot"] = scene.map_shot
                        scene.primary_visual.props["render_style"] = scene.map_shot["render_style"]
                        if scene.map_shot["render_style"] == "satellite_local" and scene_asset.get("satellite_image") and scene_asset.get("satellite_bounds"):
                            scene.primary_visual.props["satellite_image"] = scene_asset["satellite_image"]
                            scene.primary_visual.props["satellite_bounds"] = scene_asset["satellite_bounds"]
                        scene.primary_visual.props.pop("no_data", None)
                if frame_mode == "map":
                    grounded_map = _grounded_director_map(
                        raw_text,
                        str(decision.get("variant") or scene.primary_visual.variant or "country_spotlight"),
                        scene.map_shot or map_shot,
                        adjacent_site_context or " ".join(str(text or "") for text in scene_texts[max(0, idx - 1):min(len(scene_texts), idx + 2)]),
                    )
                    if grounded_map:
                        map_props, safe_map_shot = grounded_map
                        safe_map_shot["render_style"] = _safe_map_render_style(
                            map_shot.get("render_style"), safe_map_shot.get("location_names") or [], scene_asset,
                        )
                        map_props["render_style"] = safe_map_shot["render_style"]
                        map_props["map_shot"] = safe_map_shot
                        if safe_map_shot["render_style"] == "satellite_local" and scene_asset.get("satellite_image") and scene_asset.get("satellite_bounds"):
                            map_props["satellite_image"] = scene_asset["satellite_image"]
                            map_props["satellite_bounds"] = scene_asset["satellite_bounds"]
                        scene.primary_visual.type = VisualSceneType.ANIMATED_MAP.value
                        scene.primary_visual.variant = str(map_props.get("variant") or "country_spotlight")
                        scene.primary_visual.props = map_props
                        scene.map_shot = safe_map_shot
                elif frame_mode == "diagram":
                    # frame_mode is the director's final frame grammar. Some
                    # valid structured responses pair it with SmartImage as
                    # the base visual type; construct the bounded diagram
                    # payload here from complete clauses copied from this
                    # narration beat so the grammar survives validation.
                    diagram_props = _build_diagram_props(
                        str(decision.get("variant") or "cause_and_effect_flow"),
                        raw_text,
                        triggers,
                    )
                    approved_nodes = validated_diagram_nodes(decision.get("diagram_nodes"), raw_text)
                    if len(approved_nodes) >= 2 and diagram_props.get("variant") not in {"eclipse_alignment", "shadow_projection", "energy_conversion"}:
                        diagram_props.pop('no_data', None)
                        diagram_props.pop('reason', None)
                        diagram_props["steps"] = [{"title": node["label"], "desc": ""} for node in approved_nodes]
                        diagram_props["node_copy_contract"] = {
                            "source": "llm", "approved": True, "nodes": approved_nodes,
                        }
                        diagram_props["links"] = validated_diagram_links(
                            decision.get("diagram_links"), approved_nodes, raw_text,
                        )
                    diagram_candidate = SceneDecision(
                        scene_id=scene.scene_id,
                        scene_index=scene.scene_index,
                        start=scene.start,
                        end=scene.end,
                        duration=scene.duration,
                        narration=scene.narration,
                        visual_intent=scene.visual_intent,
                        primary_visual=PrimaryVisual(
                            type=VisualSceneType.DIAGRAM.value,
                            variant=str(diagram_props.get("variant") or "cause_and_effect_flow"),
                            props=diagram_props,
                        ),
                        text={"frame_mode": "diagram"},
                    )
                    if not _scene_frame_contract_failure(diagram_candidate, fps):
                        scene.primary_visual.type = VisualSceneType.DIAGRAM.value
                        scene.primary_visual.variant = str(diagram_props.get("variant") or "cause_and_effect_flow")
                        scene.primary_visual.props = diagram_props
                # Preserve narrated chronology when the director chooses a
                # map for the opening beat.  The date is a grounded fact and
                # is rendered as a compact locator label by AnimatedMap;
                # without this bridge, the map can silently drop an important
                # year even though it remains in narration.
                if llm_type == VisualSceneType.ANIMATED_MAP.value and triggers.dates:
                    scene.primary_visual.props["date"] = str(triggers.dates[0]).upper()
                if safe_placement in {"top_left", "top_right", "bottom_left", "bottom_right", "center"} and llm_type != VisualSceneType.ANIMATED_MAP.value and display_text:
                    scene.primary_visual.props["position"] = safe_placement
                if decision.get("camera") in ALLOWED_CAMERAS:
                    scene.motion.camera = decision["camera"]
                if decision.get("entrance") in ALLOWED_ENTRANCES:
                    scene.motion.entrance = decision["entrance"]
                if decision.get("transition") in ALLOWED_TRANSITIONS:
                    scene.transition_out = decision["transition"]
                scene.motion_cues = _validated_motion_cues(
                    decision.get("motion_cues"), raw_text, scene.start, scene.duration, word_timings,
                )
                if not any(cue.get("element") == "comparison" for cue in scene.motion_cues):
                    spare_cues = max(0, 3 - len(scene.motion_cues))
                    recovered = _explicit_comparison_cue_candidates(raw_text)[:spare_cues]
                    if recovered:
                        scene.motion_cues = _validated_motion_cues(
                            [
                                {"element": cue["element"], "action": cue["action"], "anchor_text": cue["anchor_text"]}
                                for cue in scene.motion_cues
                            ] + recovered,
                            raw_text, scene.start, scene.duration, word_timings,
                        )
                # A director-selected map cue on a narrated ocean/sea beat
                # must reach the actual globe renderer. The old map gate knew
                # only countries and cities, so a valid "North Atlantic"
                # treatment silently stayed on the stock image path.
                _apply_directed_region_map(scene, decision, raw_text)
                if scene.primary_visual.type in {
                    VisualSceneType.KINETIC_TEXT.value,
                    VisualSceneType.CHAPTER_TITLE.value,
                    VisualSceneType.HEADLINE.value,
                } and not (
                    display_text
                    and _valid_director_copy(raw_text, display_text, str(decision.get("source_span") or ""))
                ):
                    # A text-first component without an approved LLM phrase
                    # becomes a clean source-media beat. Never backfill it
                    # with the sentence, a heuristic highlight, or a stock
                    # chapter label.
                    media_kind = str(scene_asset.get("media_type") or scene_asset.get("type") or "").lower()
                    fallback_type = (
                        VisualSceneType.SMART_VIDEO.value
                        if media_kind in {"video", "youtube", "clip", "mp4"}
                        else VisualSceneType.SMART_IMAGE.value
                    )
                    scene.primary_visual.type = fallback_type
                    scene.primary_visual.variant = "full_bleed" if fallback_type == VisualSceneType.SMART_VIDEO.value else "full_bleed_cinematic"
                    scene.primary_visual.props = {}
                    scene.text["frame_mode"] = "media_only"
                    scene.text["overlay_policy"] = "none"
                    scene.text.pop("display_text", None)
                    scene.text.pop("focus", None)
                    scene.display_text = ""
                    scene.source_span = ""
                    scene.emphasis_spans = []
                    scene.template_id = "full-bleed"
                    scene.template_recipe = None
                _sync_scene_display_props(scene)
                type_to_frame_mode = {
                    VisualSceneType.CHAPTER_TITLE.value: "chapter",
                    VisualSceneType.ANIMATED_MAP.value: "map",
                    VisualSceneType.PERSON_PROFILE.value: "portrait",
                    VisualSceneType.DOCUMENT_HIGHLIGHT.value: "evidence",
                    VisualSceneType.CHART.value: "data",
                    VisualSceneType.STAT_CARD.value: "data",
                    VisualSceneType.TIMELINE.value: "data",
                    VisualSceneType.COMPARISON.value: "data",
                    VisualSceneType.DIAGRAM.value: "diagram",
                    VisualSceneType.QUOTE_CARD.value: "quote",
                    VisualSceneType.KINETIC_TEXT.value: "kinetic",
                    VisualSceneType.HEADLINE.value: "evidence",
                }
                if scene.primary_visual.type in type_to_frame_mode:
                    scene.text["frame_mode"] = type_to_frame_mode[scene.primary_visual.type]
            else:
                # A failed chunk may keep its grounded deterministic graphic,
                # but visible editorial copy must still be selected by an LLM.
                scene.display_text = ""
                scene.source_span = ""
                scene.emphasis_spans = []
                scene.motion_cues = []
                scene.text["overlay_policy"] = "none"
                scene.text.pop("display_text", None)
                scene.text.pop("focus", None)
                _remove_text_only_fallback(scene)

        # A route-shaped renderer needs grounded endpoints and an explicit
        # relationship. Two named places alone earn locators, not a drawn line.
        for scene in deterministic_scenes:
            if scene.primary_visual.type == VisualSceneType.ANIMATED_MAP.value:
                _repair_unverified_map_route(scene)

        # Apply a film-wide map-motion pass after factual map validation. The
        # LLM chooses the geography and intended treatment; this bounded pass
        # prevents repeated camera animation without implying unsupported routes.
        used_map_profiles: List[str] = []
        map_seed = compute_video_seed(script_text=" ".join(str(text or "") for text in scene_texts))
        for scene in deterministic_scenes:
            visual = scene.primary_visual
            if visual.type != VisualSceneType.ANIMATED_MAP.value:
                continue
            props = dict(visual.props or {})
            map_shot = dict(scene.map_shot or props.get("map_shot") or {})
            requested_profile = str(map_shot.get("motion_profile") or props.get("motion_profile") or "auto").strip().lower()
            origin = str(props.get("from") or props.get("start") or props.get("startCity") or "").strip()
            destination = str(props.get("to") or props.get("end") or props.get("endCity") or "").strip()
            route_supported = bool(map_shot.get("route_id")) or _has_explicit_map_connection(
                scene.narration or "", origin, destination
            )
            allowed_profiles = _MAP_MOTION_PROFILES if route_supported else tuple(
                profile_name for profile_name in _MAP_MOTION_PROFILES if profile_name != "route_trace"
            )
            if requested_profile not in allowed_profiles or requested_profile in used_map_profiles:
                offset = (int(map_seed) + int(scene.scene_index or 0)) % len(_MAP_MOTION_PROFILES)
                ordered_profiles = [
                    item for item in (list(_MAP_MOTION_PROFILES[offset:]) + list(_MAP_MOTION_PROFILES[:offset]))
                    if item in allowed_profiles
                ]
                profile = next((item for item in ordered_profiles if item not in used_map_profiles), "")
                if not profile:
                    last_profile = used_map_profiles[-1] if used_map_profiles else ""
                    profile = next((item for item in ordered_profiles if item != last_profile), ordered_profiles[0])
            else:
                profile = requested_profile
            used_map_profiles.append(profile)
            map_shot["motion_profile"] = profile
            scene_asset = media_items[scene.scene_index] if media_items and scene.scene_index < len(media_items) and isinstance(media_items[scene.scene_index], Mapping) else {}
            map_shot["render_style"] = _safe_map_render_style(
                map_shot.get("render_style") or props.get("render_style"),
                map_shot.get("location_names") or [],
                props if props.get("satellite_image") and props.get("satellite_bounds") else scene_asset,
            )
            if scene.map_shot:
                scene.map_shot = map_shot
            props["map_shot"] = map_shot
            props["motion_profile"] = profile
            props["render_style"] = map_shot["render_style"]
            visual.props = props

        recent_signatures = []
        for scene in deterministic_scenes:
            visual = scene.primary_visual
            if visual.type == VisualSceneType.ANIMATED_MAP.value and visual.props.get("no_data"):
                visual.type = VisualSceneType.SMART_IMAGE.value
                visual.variant = "editorial_crop"
                visual.props = {"image_treatment": "cinematic_grade"}
            elif visual.type == VisualSceneType.CHART.value:
                # A chart is only meaningful when the scene carries two or
                # more grounded values (or an explicit before/after/series
                # pair).  LLM responses can select Chart for a chronology
                # scene containing several dates, so checking only
                # ``no_data`` is not sufficient.  Downgrade unsupported
                # charts before they reach the strict manifest validator;
                # never invent values to make a graphic pass.
                chart_props = visual.props if isinstance(visual.props, dict) else {}
                chart_data = chart_props.get("data") if isinstance(chart_props.get("data"), list) else []
                has_comparison = (
                    len(chart_data) >= 2
                    or bool(chart_props.get("before") and chart_props.get("after"))
                    or bool(chart_props.get("seriesA") and chart_props.get("seriesB"))
                )
                if chart_props.get("no_data") or not has_comparison:
                    kinetic_vars = ("word_by_word_punch", "staggered_lines", "mask_slide_up", "bold_minimal_center", "tracking_expand")
                    visual.type = VisualSceneType.KINETIC_TEXT.value
                    visual.variant = kinetic_vars[scene.scene_index % len(kinetic_vars)]
                    visual.props = _build_kinetic_text_props(visual.variant, scene.narration, None)
            if visual.type == VisualSceneType.CHART.value and visual.props.get("no_data"):
                kinetic_vars = ("word_by_word_punch", "staggered_lines", "mask_slide_up", "bold_minimal_center", "tracking_expand")
                visual.type = VisualSceneType.KINETIC_TEXT.value
                visual.variant = kinetic_vars[scene.scene_index % len(kinetic_vars)]
                visual.props = _build_kinetic_text_props(visual.variant, scene.narration, None)
            variants = SCENE_TYPE_VARIANTS.get(visual.type, ())
            signature = (visual.type, visual.variant)
            if variants and signature in recent_signatures[-2:]:
                alternate = next(
                    (item for item in variants if (visual.type, item) not in recent_signatures[-3:]),
                    None,
                )
                if alternate:
                    visual.variant = alternate
                    visual.props = {**(visual.props or {}), "variant": alternate}
                    signature = (visual.type, alternate)
            if scene.scene_index > 0:
                prev_scene = deterministic_scenes[scene.scene_index - 1]
                prev_visual = prev_scene.primary_visual
                is_non_media = visual.type not in {VisualSceneType.SMART_IMAGE.value, VisualSceneType.SMART_VIDEO.value, VisualSceneType.CHAPTER_TITLE.value}
                prev_non_media = prev_visual.type not in {VisualSceneType.SMART_IMAGE.value, VisualSceneType.SMART_VIDEO.value, VisualSceneType.CHAPTER_TITLE.value}
                continuing = bool(
                    scene.pacing.continuation
                    or scene.pacing.continuous_reveal
                    or prev_scene.pacing.continuation
                    or prev_scene.pacing.continuous_reveal
                )
                if is_non_media and prev_non_media and not continuing:
                    if visual.type == prev_visual.type and visual.variant == prev_visual.variant:
                        alt_variants = [v for v in variants if v != prev_visual.variant]
                        if alt_variants:
                            visual.variant = alt_variants[scene.scene_index % len(alt_variants)]
                            visual.props = {**(visual.props or {}), "variant": visual.variant}
                            signature = (visual.type, visual.variant)
            recent_signatures.append(signature)
        return deterministic_scenes

    except json.JSONDecodeError as exc:
        print(f"   [LLM Director] Failed to parse LLM response as JSON: {exc}")
        metadata.update({
            "status": "deterministic_fallback",
            "reason": "invalid_structured_response",
            "attempt_history": list(getattr(llm_client, "attempt_history", ()) or ()),
        })
        return None
    except Exception as exc:
        print(f"   [LLM Director] LLM call failed: {exc}")
        metadata.update({
            "status": "deterministic_fallback",
            "reason": f"request_error:{type(exc).__name__}",
            "attempt_history": list(getattr(llm_client, "attempt_history", ()) or ()),
            "last_model": str(getattr(llm_client, "last_model", "") or ""),
        })
        # Keep a gated traceback for reproducing unexpected orchestration
        # failures without exposing prompts or provider response bodies in
        # ordinary production logs.
        if str(os.environ.get("VIDZEO_EDITORIAL_DIRECTOR_TRACEBACK", "")).strip().lower() in {"1", "true", "yes", "on"}:
            import traceback
            metadata["debug_traceback"] = traceback.format_exc()[-6000:]
        return None


def _repair_director_visual_opportunities(
    scenes: Sequence[SceneDecision],
    media_items: Optional[Sequence[Mapping[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Promote a few explicit script facts when the plan is still all footage.

    This is a bounded director QA pass, not a template quota. It only repairs
    media-only choices when the script contains a complete, renderable fact
    that a specialist Remotion component can explain without inference. It
    never creates visible copy, data values, routes, or portraits.
    """
    rows = list(scenes or [])
    if len(rows) < 2:
        return []

    specialist_types = {
        VisualSceneType.ANIMATED_MAP.value,
        VisualSceneType.CHART.value,
        VisualSceneType.TIMELINE.value,
        VisualSceneType.DIAGRAM.value,
        VisualSceneType.COMPARISON.value,
        VisualSceneType.QUOTE_CARD.value,
        VisualSceneType.DOCUMENT_HIGHLIGHT.value,
        VisualSceneType.STAT_CARD.value,
    }
    media = list(media_items or [])
    candidates: List[Dict[str, Any]] = []
    for index, scene in enumerate(rows):
        if not isinstance(scene, SceneDecision) or _scene_frame_mode(scene) != "media_only":
            continue
        current_type = str(scene.primary_visual.type or "")
        if current_type not in {VisualSceneType.SMART_IMAGE.value, VisualSceneType.SMART_VIDEO.value}:
            continue
        narration = str(scene.narration or "")
        if not narration.strip():
            continue
        triggers = analyze_sentence_triggers(
            narration, is_first_scene=(index == 0), context_text=narration,
        )
        selected: Optional[Dict[str, Any]] = None

        # A narrated lead/trail score is a high-salience paired fact even when
        # the director already used charts or maps elsewhere in the film.
        try:
            from .production_contract import _narrated_score_comparison
            score_pair = _narrated_score_comparison(narration)
        except Exception:
            score_pair = None
        if score_pair:
            selected = {
                "type": VisualSceneType.COMPARISON.value,
                "variant": "versus_contrast_clash",
                "frame_mode": "data",
                "template_id": "split-screen",
                "props": {**score_pair, "variant": "versus_contrast_clash", "transparent_canvas": True},
                "anchor": "",
                "reason": "exact_narrated_score",
            }

        # A single narrated date is a milestone, not a chart. Keep the exact
        # date and one compact label; platforms or other facts stay in copy
        # only when the LLM approved them separately.
        if selected is None and triggers.dates:
            timeline_variant = "horizontal_milestone_trail" if len(triggers.dates) >= 2 else "date_flip_calendar"
            props = _build_timeline_props(
                timeline_variant, narration, triggers.dates, triggers,
            )
            if not props.get("no_data"):
                date = str(triggers.dates[0])
                props["title"] = ""
                events = props.get("events") if isinstance(props.get("events"), list) else []
                if events and isinstance(events[0], dict):
                    events[0]["label"] = "SCHEDULED" if re.search(r"\bscheduled\b", narration, re.I) else "MILESTONE"
                selected = {
                    "type": VisualSceneType.TIMELINE.value,
                    "variant": timeline_variant,
                    "frame_mode": "data",
                    "template_id": "timeline-trace",
                    "props": props,
                    "anchor": date,
                    "reason": "exact_narrated_date",
                }

        # Maps are only eligible when the existing local place catalog can
        # resolve the narrated geography. Fictional/unresolved places stay out.
        if selected is None and triggers.recommended_scene_type == VisualSceneType.ANIMATED_MAP.value:
            props = _build_map_props("country_spotlight", triggers, narration)
            if not props.get("no_data") and _map_props_are_renderable(props, props.get("map_shot") or {}):
                location = str(props.get("location") or props.get("title") or "").strip()
                selected = {
                    "type": VisualSceneType.ANIMATED_MAP.value,
                    "variant": str(props.get("variant") or "country_spotlight"),
                    "frame_mode": "map",
                    "template_id": "map-route",
                    "props": props,
                    "anchor": location,
                    "reason": "catalog_resolved_narrated_place",
                }

        # A chart/comparison needs actual narrated comparable values. A lone
        # number never gets padded into a fabricated graph.
        if selected is None and triggers.recommended_scene_type in {
            VisualSceneType.CHART.value, VisualSceneType.COMPARISON.value,
        }:
            candidate_type = triggers.recommended_scene_type
            if candidate_type == VisualSceneType.CHART.value and len(triggers.numbers) >= 2:
                props = _build_chart_props(
                    "editorial_bar", narration, triggers.numbers, "editorial_white", [],
                    context_text=narration,
                )
                if not props.get("no_data"):
                    selected = {
                        "type": VisualSceneType.CHART.value, "variant": str(props.get("variant") or "editorial_bar"),
                        "frame_mode": "data", "template_id": "bar-chart", "props": props,
                        "anchor": "", "reason": "narrated_comparable_values",
                    }
            elif candidate_type == VisualSceneType.COMPARISON.value and triggers.has_comparison and len(triggers.numbers) >= 2:
                props = _build_comparison_props("side_by_side_cards", narration, triggers.numbers, triggers)
                if not props.get("no_data"):
                    selected = {
                        "type": VisualSceneType.COMPARISON.value, "variant": "side_by_side_cards",
                        "frame_mode": "data", "template_id": "split-screen", "props": props,
                        "anchor": "", "reason": "narrated_comparison_with_values",
                    }

        # Cause/effect is eligible only when the narration contains at least
        # two distinct exact stages and a stated relationship.
        if selected is None:
            props = _build_diagram_props("cause_and_effect_flow", narration, triggers)
            steps = props.get("steps") if isinstance(props.get("steps"), list) else []
            labels = [str(step.get("title") or "").strip() for step in steps if isinstance(step, Mapping)]
            if not props.get("no_data") and len(labels) >= 2 and all(
                label and re.search(r"(?<!\w)" + re.escape(label) + r"(?!\w)", narration, re.I)
                for label in labels
            ):
                props["links"] = [
                    {"from": labels[offset], "to": labels[offset + 1], "evidence": narration.strip()}
                    for offset in range(min(len(labels) - 1, 2))
                ]
                selected = {
                    "type": VisualSceneType.DIAGRAM.value, "variant": str(props.get("variant") or "cause_and_effect_flow"),
                    "frame_mode": "diagram", "template_id": "cause-effect", "props": props,
                    "anchor": labels[0], "reason": "narrated_causal_stages",
                }

        if selected is None:
            continue
        item = media[index] if index < len(media) and isinstance(media[index], Mapping) else {}
        is_fallback = bool(item.get("is_graphic_fallback")) or str(item.get("resolution_kind") or "").casefold() in {
            "grounded_graphic_fallback", "graphic_fallback",
        } or str(item.get("source") or "").casefold() == "graphic"
        selected.update({"scene_index": index, "fallback_media_replaced": is_fallback})
        candidates.append(selected)

    # Keep the script-led graphic share near 30% when supported; a one-scene
    # cooldown prevents wallpaper. Existing use of one specialist family must
    # not suppress another distinct, grounded opportunity later in the film.
    target = min(len(candidates), max(1, round(len(rows) * 0.3)))
    if len(rows) >= 8 and len(candidates) >= 2:
        target = min(len(candidates), max(2, target))
    chosen: List[Dict[str, Any]] = []
    specialist_positions = [
        (index, str(scene.primary_visual.type), str(scene.primary_visual.variant or ""))
        for index, scene in enumerate(rows)
        if getattr(scene, "primary_visual", None)
        and scene.primary_visual.type in specialist_types
        and _scene_frame_mode(scene) != "media_only"
    ]
    for item in sorted(candidates, key=lambda row: (
        row.get("reason") != "exact_narrated_score",
        not row.get("fallback_media_replaced"), row["scene_index"],
    )):
        if len(chosen) >= target:
            continue
        nearby_same_grammar = any(
            prior_type == item["type"] and abs(item["scene_index"] - prior_index) <= 1
            and (not prior_variant or prior_variant == item["variant"])
            for prior_index, prior_type, prior_variant in specialist_positions
        )
        if nearby_same_grammar:
            continue
        if any(abs(item["scene_index"] - prior["scene_index"]) <= 1 for prior in chosen):
            continue
        chosen.append(item)
        specialist_positions.append((item["scene_index"], item["type"], item["variant"]))

    repairs: List[Dict[str, Any]] = []
    for item in chosen:
        scene = rows[item["scene_index"]]
        scene.primary_visual.type = item["type"]
        scene.primary_visual.variant = item["variant"]
        scene.primary_visual.props = dict(item["props"])
        scene.template_id = item["template_id"]
        scene.template_recipe = None
        scene.visual_role = "map_canvas" if item["frame_mode"] == "map" else "primary_explanation"
        scene.visual_role_source = "system_grounded_mapping"
        scene.text = dict(scene.text or {})
        scene.text["frame_mode"] = item["frame_mode"]
        scene.text["visual_repair_source"] = "script_grounded_quality_guard"
        scene.text["visual_repair_reason"] = item["reason"]
        # A specialist graphic already displays its exact fact. Avoid a
        # duplicate date/label while leaving other LLM-approved copy untouched.
        if item.get("anchor") and str(scene.display_text or "").strip().casefold() == str(item["anchor"]).strip().casefold():
            scene.display_text = ""
            scene.source_span = ""
            scene.emphasis_spans = []
            scene.text["overlay_policy"] = "none"
            scene.text.pop("display_text", None)
        if item.get("anchor"):
            scene.motion_cues = [{
                "element": "map" if item["frame_mode"] == "map" else ("steps" if item["frame_mode"] == "diagram" else "data"),
                "action": "reveal" if item["frame_mode"] != "map" else "pin",
                "anchor_text": str(item["anchor"]),
            }]
        scene.visual_intent = (
            f"Use the {item['frame_mode']} Remotion treatment to explain only the narrated fact; "
            "keep its payload exact and leave a readable hold."
        )
        _repair_scene_frame_contract(scene, media[index] if index < len(media) else None)
        repairs.append({key: item[key] for key in (
            "scene_index", "type", "frame_mode", "template_id", "reason", "fallback_media_replaced",
        )})
    return repairs


class EditingDirector:
    """Lead Documentary Editing Director.
    
    Translates research and script into a complete, non-repetitive EditPlan JSON.
    """

    def __init__(self, llm_client: Optional[Any] = None, director_config: Optional[Mapping[str, Any]] = None):
        self.llm_client = llm_client
        self.director_config = dict(director_config or {})

    def plan_documentary(
        self,
        script_text: str,
        scene_texts: Sequence[str],
        scene_durations: Sequence[float],
        media_items: Optional[Sequence[Mapping[str, Any]]] = None,
        word_timings: Optional[Sequence[Mapping[str, Any]]] = None,
        topic: str = "",
        category: str = "",
        channel_brand: Optional[Mapping[str, Any]] = None,
        past_fingerprints: Optional[Sequence[EditFingerprint]] = None,
        variation_seed: Optional[int] = None,
        fps: int = 30,
        width: int = 1920,
        height: int = 1080,
        editing_profile: Optional[Mapping[str, Any]] = None,
        director_config: Optional[Mapping[str, Any]] = None,
    ) -> EditPlan:
        """Create a full EditPlan JSON for the documentary project."""
        seed = int(variation_seed if variation_seed is not None else compute_video_seed(topic, category, script_text))
        from ..editing_profile import deterministic_profile, normalize_profile

        profile_fallback = deterministic_profile(
            topic,
            category,
            script_text,
            variation_seed=seed,
        )
        profile = normalize_profile(
            editing_profile if isinstance(editing_profile, Mapping) else None,
            profile_fallback,
        )
        profile_niche = str(profile.get("niche") or "").strip().lower()
        profile_genres = {
            "history": "historical-documentary",
            "royal": "royal-history",
            "gaming": "gaming-blockbuster",
            "crime": "true-crime",
            "sports": "sports-documentary",
            "travel": "travelogue",
            "technology": "technology",
            "finance": "finance-explainer",
            "science": "science-nature",
            "nature": "science-nature",
            "military": "military-tactical",
            "politics": "politics-explainer",
            "automotive": "automotive-documentary",
            "biography": "biographical-essay",
            "culture": "cultural-chronicle",
            "health": "health-science",
        }
        profile_genre = profile_genres.get(profile_niche)

        # Art direction is planned beside retrieval/audio. Feed its grounded
        # palette and typography into VisualDNA while preserving explicit
        # channel-brand values as the higher-priority override.
        profile_brand: Dict[str, Any] = {}
        profile_palette = profile.get("palette") if isinstance(profile.get("palette"), Mapping) else {}
        for key in ("accent", "background", "surface", "primary", "secondary", "danger"):
            value = profile_palette.get(key)
            if isinstance(value, str) and value.strip():
                profile_brand[f"{key}_color" if key in {"accent", "background"} else key] = value
        profile_typography = profile.get("typography") if isinstance(profile.get("typography"), Mapping) else {}
        if profile_typography.get("display"):
            profile_brand["font_family"] = str(profile_typography["display"])
        merged_brand = dict(profile_brand)
        if isinstance(channel_brand, Mapping):
            merged_brand.update(dict(channel_brand))
        visual_dna = generate_visual_dna(
            topic,
            category,
            script_text,
            merged_brand or channel_brand,
            seed=seed,
            genre_override=profile_genre,
        )
        art_direction = profile.get("art_direction") if isinstance(profile.get("art_direction"), Mapping) else {}
        texture_map = {
            "archive": "paper",
            "paper": "paper",
            "blueprint": "scanlines",
            "technical": "scanlines",
            "film": "dust_and_scratch",
        }
        texture = texture_map.get(str(art_direction.get("texture") or "").lower())
        if texture:
            visual_dna.texture = texture
        motion = str(art_direction.get("motion") or "").strip().lower()
        if motion:
            visual_dna.motion_style = motion

        # Plan scene decisions: try LLM director first, fall back to deterministic
        director_metadata: Dict[str, Any] = {}
        scenes = _plan_llm_scenes(
            scene_texts, scene_durations, media_items,
            word_timings=word_timings, visual_dna=visual_dna,
            llm_client=self.llm_client,
            director_config={**self.director_config, **dict(director_config or {})},
            editing_profile=profile,
            metadata_out=director_metadata,
            variation_seed=seed,
            fps=fps,
        )
        used_local_fallback = scenes is None
        if scenes is None:
            director_metadata.setdefault("provider", str(self.director_config.get("llm_provider") or "openrouter"))
            director_metadata.setdefault("status", "deterministic_fallback")
            director_metadata.setdefault("reason", "director_unavailable")
            scenes = _plan_deterministic_scenes(scene_texts, scene_durations, media_items, word_timings=word_timings, visual_dna=visual_dna)

        # Run one deterministic, script-grounded creative repair before the
        # timeline is locked. It only upgrades a small number of media-only
        # beats when their exact narration supports a specialist component.
        # This keeps successful LLM direction intact and prevents long edits
        # from collapsing to full-bleed footage when clear dates, processes,
        # comparisons, or real locations are present.
        try:
            opportunity_repairs = _repair_director_visual_opportunities(scenes, media_items)
        except Exception as repair_error:
            opportunity_repairs = []
            director_metadata["story_grammar_repair"] = {
                "status": "unavailable",
                "error_type": type(repair_error).__name__,
            }
        if opportunity_repairs:
            director_metadata["story_grammar_repair"] = {
                "status": "applied",
                "method": "script_grounded_quality_guard",
                "repairs": opportunity_repairs,
            }

        # Enforce documentary variety and spacing rules
        scenes = enforce_scene_grammar(scenes)
        if used_local_fallback:
            _apply_local_fallback_editing(scenes)
            for scene in scenes:
                scene.display_text = ""
                scene.source_span = ""
                scene.emphasis_spans = []
                scene.motion_cues = []
                scene.text["overlay_policy"] = "none"
                scene.text.pop("display_text", None)
                scene.text.pop("focus", None)
                _remove_text_only_fallback(scene)

        # Hydrate props for any scenes promoted by grammar rules
        for s in scenes:
            pv_type = s.primary_visual.type
            pv_variant = s.primary_visual.variant
            pv_props = s.primary_visual.props or {}
            raw_narr = s.narration or ""
            if pv_type == VisualSceneType.KINETIC_TEXT.value and "text" not in pv_props:
                s.primary_visual.props = _build_kinetic_text_props(pv_variant, raw_narr, None)
            elif pv_type == VisualSceneType.STAT_CARD.value and ("value" not in pv_props or pv_props.get("value") == "—"):
                s.primary_visual.props = _build_stat_card_props(pv_variant, raw_narr, [], None)
            elif pv_type == VisualSceneType.PERSON_PROFILE.value and "name" not in pv_props:
                s.primary_visual.props = _build_person_props(pv_variant, raw_narr, [], None)
            elif pv_type == VisualSceneType.HEADLINE.value and "headline" not in pv_props:
                s.primary_visual.props = _build_headline_props(pv_variant, raw_narr, [], None)
            # Grammar promotions and prop hydration can otherwise reintroduce
            # the raw narration after the director has chosen a short cue.
            _sync_scene_display_props(s)

        # Enforce the selected frame grammar after all fallback and grammar
        # passes. A rejected map/diagram must become an explicit media frame;
        # the export plan must never claim a graphic the renderer will omit.
        for scene in scenes:
            media_item = media_items[scene.scene_index] if media_items and scene.scene_index < len(media_items) else None
            _repair_scene_frame_contract(scene, media_item, fps)
            if used_local_fallback:
                scene.display_text = ""
                scene.source_span = ""
                scene.emphasis_spans = []
                scene.text["overlay_policy"] = "none"
                scene.text.pop("display_text", None)
                scene.text.pop("focus", None)
                scene.text.pop("primary", None)
                props = dict(scene.primary_visual.props or {})
                for key in ("display_text", "_display_text", "emphasis_spans", "emphasis_phrase"):
                    props.pop(key, None)
                scene.primary_visual.props = props
            if scene.display_text and not any(cue.get("element") == "copy" for cue in scene.motion_cues):
                copy_cues = _validated_motion_cues(
                    [{"element": "copy", "action": "reveal", "anchor_text": scene.display_text}],
                    scene.narration, scene.start, scene.duration, word_timings,
                )
                if copy_cues:
                    if len(scene.motion_cues) >= 3:
                        removable = next((index for index, cue in enumerate(scene.motion_cues) if cue.get('action') == 'hold'), len(scene.motion_cues) - 1)
                        scene.motion_cues.pop(removable)
                    scene.motion_cues.extend(copy_cues[:1])

        total_duration = sum(float(d) for d in scene_durations) if scene_durations else 0.0
        audio_plan = AudioPlan(
            duration=total_duration,
            narration_timestamps=list(word_timings or []),
        )

        plan = EditPlan(
            title=topic or "Documentary Project",
            topic=topic,
            category=category,
            seed=seed,
            fps=fps,
            width=width,
            height=height,
            visual_dna=visual_dna,
            channel_brand=dict(channel_brand) if channel_brand else None,
            scenes=scenes,
            audio_plan=audio_plan,
            version="2.0",
            director_metadata=director_metadata,
        )

        # Anti-repetition check against creator's past projects
        if past_fingerprints:
            plan = reroll_variations_if_similar(plan, past_fingerprints)

        plan.director_metadata['bespoke_composition_scene_indices'] = [scene.scene_index for scene in plan.scenes if scene.template_recipe]
        design_passes = plan.director_metadata.get('composition_passes') or []
        plan.director_metadata['composition_coverage'] = 'partial' if any(
            row.get('status') not in {'accepted', 'accepted_after_correction', 'initial_composition_valid'} for row in design_passes
        ) else 'complete'

        return plan


# Backward-compatible alias for EditingDirector
RemotionEditorialDirector = EditingDirector
