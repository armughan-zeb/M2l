"""Documentary-style edit planning shared by preview manifests and FFmpeg render."""

from __future__ import annotations

import re
import hashlib
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .text_position_contract import (
    ALLOWED_TEXT_POSITIONS,
    guide_anchor,
    normalize_text_position,
)


DEFAULT_DOCUMENTARY_SETTINGS = {
    "enabled": False,
    "date_cards": True,
    "key_highlights": True,
    "money_counters": True,
    "section_transitions": True,
    "color_grade": True,
    "style": "investigative",
    "density": "balanced",
    "texture": "film_grain",
    "font_family": "Montserrat",
}

_STYLE_DEFAULTS = {
    "investigative": {"texture": "film_grain", "font_family": "Montserrat"},
    "archival": {"texture": "paper", "font_family": "Playfair Display"},
    "history": {"texture": "paper", "font_family": "Poppins"},
    "crime": {"texture": "scanlines", "font_family": "Poppins"},
    "modern": {"texture": "grid", "font_family": "Poppins"},
    "minimalist": {"texture": "none", "font_family": "Open Sans"},
    "standard": {"texture": "none", "font_family": "Poppins"},
}

_FONT_FILES = {
    "Montserrat": "Poppins-Bold.ttf",
    "Playfair Display": "OpenSans-Bold.ttf",
    "Cinzel": "Poppins-Bold.ttf",
    "Anton": "Poppins-Bold.ttf",
    "Poppins": "Poppins-Bold.ttf",
    "Open Sans": "OpenSans-Bold.ttf",
    "Roboto": "OpenSans-Bold.ttf",
}

TEXT_TEMPLATE_FAMILIES = {
    "date": ("date_stamp", "date_minimal", "date_outline", "date_ribbon", "date_corner"),
    "money": ("counter_center", "counter_edge", "counter_minimal", "counter_ribbon", "counter_split"),
    "statistic": ("stat_big", "stat_marker", "stat_outline", "stat_ribbon", "stat_corner"),
    "chapter_title": ("chapter_center", "chapter_edge", "chapter_minimal", "chapter_number", "chapter_split"),
    "person": ("person_lower", "person_center", "person_edge", "person_outline", "person_minimal"),
    "place": ("place_stamp", "place_edge", "place_center", "place_minimal", "place_outline"),
    "quote": ("quote_clean", "quote_edge", "quote_center", "quote_ribbon", "quote_minimal"),
    "cta": ("cta_pop", "cta_minimal", "cta_outline", "cta_ribbon", "cta_corner"),
    "highlight": ("keyword_pop", "keyword_outline", "keyword_marker", "keyword_clean", "keyword_split"),
}

_TEMPLATE_STYLE = {
    "date_stamp": ("edge", "top_left", 52, "slide_left"),
    "date_minimal": ("none", "top_left", 58, "fade"),
    "date_outline": ("outline", "top_left", 56, "quick_zoom_out"),
    "date_ribbon": ("underline", "top_left", 54, "wipe"),
    "date_corner": ("marker", "top_left", 50, "quick_zoom_in"),
    "counter_center": ("none", "center", 84, "scale_pop"),
    "counter_edge": ("edge", "top_left", 64, "slide_left"),
    "counter_minimal": ("underline", "bottom_left", 72, "quick_zoom_in"),
    "counter_ribbon": ("marker", "bottom_left", 68, "rise"),
    "counter_split": ("outline", "center", 76, "quick_zoom_out"),
    "stat_big": ("none", "center", 88, "quick_zoom_in"),
    "stat_marker": ("marker", "center", 74, "scale_pop"),
    "stat_outline": ("outline", "center", 68, "quick_zoom_out"),
    "stat_ribbon": ("underline", "bottom_left", 72, "wipe"),
    "stat_corner": ("edge", "top_left", 62, "slide_right"),
    "chapter_center": ("none", "center", 82, "blur_in"),
    "chapter_edge": ("edge", "top_left", 66, "slide_left"),
    "chapter_minimal": ("underline", "bottom_left", 70, "rise"),
    "chapter_number": ("marker", "top_left", 78, "scale_pop"),
    "chapter_split": ("outline", "center", 76, "quick_zoom_in"),
    "person_lower": ("none", "bottom_left", 58, "slide_left"),
    "person_center": ("none", "center", 72, "fade"),
    "person_edge": ("edge", "bottom_left", 56, "rise"),
    "person_outline": ("outline", "center", 62, "quick_zoom_out"),
    "person_minimal": ("underline", "center", 58, "wipe"),
    "place_stamp": ("edge", "top_left", 56, "slide_right"),
    "place_edge": ("edge", "bottom_left", 58, "rise"),
    "place_center": ("none", "center", 70, "fade"),
    "place_minimal": ("underline", "center", 60, "wipe"),
    "place_outline": ("outline", "top_left", 56, "quick_zoom_out"),
    "quote_clean": ("none", "center", 52, "fade"),
    "quote_edge": ("edge", "bottom_left", 48, "slide_left"),
    "quote_center": ("outline", "center", 54, "quick_zoom_out"),
    "quote_ribbon": ("underline", "center", 50, "wipe"),
    "quote_minimal": ("marker", "bottom_left", 48, "rise"),
    "cta_pop": ("marker", "center", 72, "scale_pop"),
    "cta_minimal": ("none", "center", 60, "rise"),
    "cta_outline": ("outline", "center", 68, "quick_zoom_in"),
    "cta_ribbon": ("underline", "center", 64, "wipe"),
    "cta_corner": ("edge", "top_left", 62, "slide_right"),
    "keyword_pop": ("none", "center", 82, "scale_pop"),
    "keyword_outline": ("outline", "center", 76, "quick_zoom_out"),
    "keyword_marker": ("marker", "center", 68, "wipe"),
    "keyword_clean": ("none", "center", 72, "fade"),
    "keyword_split": ("underline", "center", 78, "quick_zoom_in"),
}

_CAMERA_MOTIONS = {
    "calm": ("static", "zoom_out", "pan_left"),
    "normal": ("zoom_in", "zoom_out", "pan_left", "pan_right", "static"),
    "tense": ("slow_zoom", "pan_left", "pan_right"),
    "energetic": ("quick_zoom_in", "quick_zoom_out", "pan_left", "pan_right"),
    "climactic": ("quick_zoom_in", "quick_zoom_out", "dynamic"),
}

# Captions rotate across the five green slots in the placement guide. Keeping
# this order explicit prevents a renderer from reintroducing a side-middle slot.
_CAPTION_POSITION_ROTATION = ALLOWED_TEXT_POSITIONS


def _rotate_caption_position(template_position: str, scene_index: int, has_caption: bool) -> str:
    """Pick a caption anchor for a scene.

    Template families still express intent (e.g. date stamps read best in a
    corner, counters centered), but the user's requested editing priority is
    white bold text spread across positions. Rotate the five requested anchors
    deterministically by scene index whenever the scene actually carries a
    caption; fall back to the template's position otherwise.
    """
    if not has_caption:
        return normalize_text_position(template_position)
    return _CAPTION_POSITION_ROTATION[abs(int(scene_index or 0)) % len(_CAPTION_POSITION_ROTATION)]

_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december|"
    "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
_DATE_RE = re.compile(
    rf"\b(?:(?:{_MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?|"
    rf"\d{{1,2}}\s+(?:{_MONTHS})\.?\s+\d{{4}}|(?:19|20)\d{{2}})\b",
    re.IGNORECASE,
)
_MONEY_RE = re.compile(
    r"(?P<prefix>[$£€])\s*(?P<number>\d[\d,]*(?:\.\d+)?)\s*(?P<scale>billion|million|thousand|bn|m|k)?\b|"
    r"\b(?P<number_words>\d[\d,]*(?:\.\d+)?)\s*(?P<scale_words>billion|million|thousand)\s+"
    r"(?P<currency>dollars?|pounds?|euros?)\b",
    re.IGNORECASE,
)
_STAT_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|percent|million|billion|thousand)\b", re.IGNORECASE)
_QUOTE_RE = re.compile(r'["“]([^"”]{18,110})["”]')
_CTA_RE = re.compile(r"\b(?:subscribe|follow|like and share|join us|sign up)\b", re.IGNORECASE)


def normalize_documentary_settings(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    source = dict(config or {})
    nested = source.get("documentary_editing")
    if isinstance(nested, dict):
        source = {**source, **nested}
    settings = dict(DEFAULT_DOCUMENTARY_SETTINGS)
    settings["enabled"] = bool(source.get("documentary_editing_enabled", source.get("enabled", settings["enabled"])))
    for key in ("date_cards", "key_highlights", "money_counters", "section_transitions", "color_grade"):
        settings[key] = bool(source.get(f"documentary_{key}", source.get(key, settings[key])))
    style = str(source.get("documentary_style") or source.get("style") or settings["style"]).strip().lower()
    settings["style"] = style if style in {
        "investigative", "archival", "modern", "crime", "history", "minimalist", "standard"
    } else "investigative"
    density = str(source.get("documentary_density") or source.get("density") or settings["density"]).strip().lower()
    settings["density"] = density if density in {"subtle", "balanced", "dynamic"} else "balanced"
    defaults = _STYLE_DEFAULTS[settings["style"]]
    texture = str(source.get("documentary_texture") or source.get("texture") or defaults["texture"]).strip().lower()
    settings["texture"] = texture if texture in {"none", "film_grain", "paper", "scanlines", "grid"} else defaults["texture"]
    settings["font_family"] = str(
        source.get("documentary_font_family") or source.get("font_family") or defaults["font_family"]
    ).strip() or defaults["font_family"]
    return settings


def _first_date(text: str) -> str:
    match = _DATE_RE.search(str(text or ""))
    return re.sub(r"\s+", " ", match.group(0)).strip() if match else ""


def _money_cue(text: str) -> Optional[Dict[str, Any]]:
    source_text = str(text or "")
    match = _MONEY_RE.search(source_text)
    if not match:
        return None
    number = match.group("number") or match.group("number_words") or ""
    try:
        numeric = float(number.replace(",", ""))
    except ValueError:
        return None
    scale = (match.group("scale") or match.group("scale_words") or "").lower()
    multiplier = {"thousand": 1_000, "k": 1_000, "million": 1_000_000, "m": 1_000_000,
                  "billion": 1_000_000_000, "bn": 1_000_000_000}.get(scale, 1)
    prefix = match.group("prefix") or {"dollar": "$", "pound": "£", "euro": "€"}.get(
        re.sub(r"s$", "", (match.group("currency") or "").lower()), "$"
    )
    if prefix == "\u0141":
        prefix = "\u00a3"
    suffix = {"thousand": "K", "k": "K", "million": "M", "m": "M",
              "billion": "B", "bn": "B"}.get(scale, "")
    return {
        "label": match.group(0).strip(),
        "prefix": prefix,
        "value": int(numeric * multiplier),
        "display_value": numeric,
        "suffix": suffix,
    }


_HIGHLIGHT_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "in", "on", "at", "to", "for",
    "with", "by", "from", "as", "was", "were", "had", "has", "have", "it", "its",
    "this", "that", "these", "those", "they", "their", "he", "she", "him", "her",
    "his", "would", "could", "should", "will", "can", "just", "then", "than",
    "when", "what", "which", "who", "how", "there", "here", "into", "over",
    "under", "again", "after", "before", "about", "between", "against", "became",
    "began", "being", "been", "while", "during", "until", "did", "does", "do",
    "is", "are", "not", "no", "yes", "so", "too", "very", "because", "out",
    "also", "even", "still", "yet", "now", "many", "much", "more", "most",
}

_HIGHLIGHT_POWER_WORDS = {
    "first", "last", "only", "never", "always", "deadliest", "deadly", "largest",
    "smallest", "record", "final", "missing", "sank", "sinking", "struck",
    "hit", "collided", "collision", "discovered", "evidence", "survivors",
    "survivor", "lifeboats", "iceberg", "abandoned", "vanished", "escaped",
    "rescued", "wreck", "wreckage", "trapped", "crashed", "exploded", "warning",
    "crisis", "secret", "truth", "revealed", "confirmed", "banned", "critical",
}


def _compact_highlight_phrase(phrase: str, scene_text: str = "") -> str:
    """Reduce an LLM emphasis phrase to ONE salient word.

    The old behavior drew the whole phrase ("the ship struck an iceberg")
    as one giant block. Word-level emphasis is more readable and timable:
    keep only the single strongest word that carries the visual payload.
    """
    phrase = re.sub(r"\s+", " ", str(phrase or "")).strip(" ,.;:!?")
    if not phrase:
        return ""
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9'%,-]*", phrase)
    meaningful = [t for t in tokens if t.lower() not in _HIGHLIGHT_STOPWORDS]
    if not meaningful:
        meaningful = tokens

    def _weight(token: str) -> int:
        t = token.lower()
        score = 0
        if re.search(r"\d|%", token):
            score += 4
        if t in _HIGHLIGHT_POWER_WORDS:
            score += 3
        if token.isupper() and len(token) > 2:
            score += 2
        if len(t) >= 6:
            score += 1
        return score

    ranked = sorted(((_weight(t), i, t) for i, t in enumerate(meaningful)), reverse=True)
    kept = [t for _, i, t in sorted(ranked[:1], key=lambda item: item[1])]
    if not kept:
        return " ".join(tokens)[:46]
    return " ".join(kept)[:32]


def _key_phrase(text: str) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean:
        return ""
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9'%-]*", clean)
    power = re.compile(
        r"^(?:first|largest|deadliest|record|never|only|secret|critical|confirmed|revealed|"
        r"discovered|final|missing|banned|million|billion|percent|evidence|truth)$",
        re.I,
    )
    best_index = -1
    best_score = 0
    for index, token in enumerate(tokens):
        score = 5 if power.search(token) else 0
        score += 4 if re.search(r"\d|%", token) else 0
        score += 2 if token.isupper() and len(token) > 2 else 0
        if score > best_score:
            best_score, best_index = score, index
    if best_index < 0:
        return ""
    left = best_index
    right = min(len(tokens), best_index + 1)
    while right < len(tokens) and right - left < 4 and len(tokens[right]) > 2:
        right += 1
    return " ".join(tokens[left:right])[:46]


def _stable_number(*parts: Any) -> int:
    payload = "|".join(str(part or "") for part in parts).encode("utf-8", errors="ignore")
    return int(hashlib.sha256(payload).hexdigest()[:12], 16)


def _choose_without_recent(options: Iterable[str], seed: int, recent: Iterable[str]) -> str:
    choices = list(options or [])
    if not choices:
        return ""
    recent_set = set(list(recent or [])[-3:])
    available = [choice for choice in choices if choice not in recent_set] or choices
    return available[seed % len(available)]


def _template_for_scene(cue_type: str, text: str, index: int, style: str, recent: Iterable[str], beat: Dict[str, Any]) -> str:
    """Route beat to VOX template via smart_router, fallback to legacy selection."""
    try:
        from .native_engine.smart_router import route_beat as _vox_route
        vox_beat = dict(beat) if beat else {}
        if cue_type and not vox_beat.get("treatment"):
            vox_beat["treatment"] = cue_type
        return _vox_route(vox_beat, used_recent=list(recent or []), scene_index=index)
    except Exception:
        options = TEXT_TEMPLATE_FAMILIES.get(cue_type) or TEXT_TEMPLATE_FAMILIES["highlight"]
        return _choose_without_recent(options, _stable_number(text, index, style, beat.get("energy")), recent)


def _build_vox_props(beat: Dict[str, Any], text: str, overlay_label: str, cue_type: str) -> Dict[str, Any]:
    """Build structured props for VOX templates from editorial beat."""
    try:
        from .native_engine.smart_router import build_props_from_beat
        props = build_props_from_beat(beat, text)
        if overlay_label and not props.get("text"):
            props["text"] = overlay_label
        return props
    except Exception:
        return {"text": overlay_label or text[:60], "title": overlay_label or text[:60]}


def _transition_for_scene(
    beat: Dict[str, Any], boundary: bool, text: str, index: int, recent: Iterable[str], enabled: bool
) -> str:
    if not enabled or index == 0:
        return "cut"
    requested = str(beat.get("transition") or "cut").strip().lower()
    requested = {"dip_black": "dip_to_black", "fade": "crossfade"}.get(requested, requested)
    energy = str(beat.get("energy") or "normal")
    seed = _stable_number(text, index, beat.get("role"), energy)
    if boundary:
        options = ("dip_to_black", "crossfade", "quick_zoom_in", "slide_left", "wipe")
    else:
        # Every clip boundary receives a short rendered transition. A stable
        # cadence guarantees visible zoom changes without repeating one trick.
        cadence = ("quick_zoom_in", "crossfade", "quick_zoom_out", "slide_left", "quick_zoom_in", "slide_right")
        cadence_choice = cadence[(index - 1) % len(cadence)]
        if requested == "cut" or requested in set(list(recent or [])[-2:]):
            return cadence_choice
        options = ("crossfade", "quick_zoom_in", "quick_zoom_out", "slide_left", "slide_right", "wipe")
    if requested in options and requested not in set(list(recent or [])[-2:]):
        return requested
    if not boundary:
        return cadence_choice
    return _choose_without_recent(options, seed, recent)


def _scene_texture(settings: Dict[str, Any], beat: Dict[str, Any], index: int, text: str) -> str:
    preferred = str(beat.get("texture") or "none").strip().lower()
    if preferred not in {"none", "film_grain", "paper", "scanlines", "grid"}:
        preferred = "none"
    if preferred != "none":
        return preferred
    configured = str(settings.get("texture") or "none")
    role = str(beat.get("role") or "context")
    # Texture is a momentary editorial layer, not a permanent blanket.
    if configured != "none" and role in {"intro", "chapter", "evidence", "reveal"} and _stable_number(text, index) % 100 < 42:
        return configured
    return "none"


def _cue_offset(text: str, cue: str, duration: float, cue_duration: float, default_ratio: float = 0.18) -> float:
    """Place a visual near the spoken phrase without requiring word timestamps."""
    source = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    needle = re.sub(r"\s+", " ", str(cue or "")).strip().lower()
    ratio = default_ratio
    if source and needle:
        position = source.find(needle)
        if position >= 0:
            ratio = position / max(1, len(source))
    latest = max(0.0, duration - cue_duration - 0.12)
    return round(min(latest, max(0.12, duration * ratio - 0.12)), 3)


def _overlay_duration(duration: float, overlay_type: str) -> float:
    preferred = 2.8 if overlay_type in {"chapter_title", "cta"} else 3.4
    return round(max(0.8, min(preferred, duration - 0.12)), 3)


def _spoken_tokens(value: str) -> List[str]:
    tokens = []
    for token in re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", str(value or "")):
        normalized = token.lower()
        ordinal = re.fullmatch(r"(\d+)(?:st|nd|rd|th)", normalized)
        tokens.append(ordinal.group(1) if ordinal else normalized)
    return tokens


def _whisper_cue_timing(
    cue: str,
    scene_start: float,
    scene_duration: float,
    whisper_words: Iterable[Dict[str, Any]],
    fallback_start: float,
    fallback_duration: float,
) -> tuple[float, float, str]:
    """Align a grounded cue to the exact spoken word span inside its scene."""
    needle = _spoken_tokens(cue)
    if not needle:
        return fallback_start, fallback_duration, "estimated"
    scene_end = scene_start + scene_duration
    candidates = []
    for word in whisper_words or []:
        try:
            start = float(word.get("start") or 0.0)
            end = float(word.get("end") or start)
        except (TypeError, ValueError):
            continue
        token_parts = _spoken_tokens(word.get("word") or word.get("text") or "")
        if not token_parts or end < scene_start - 0.55 or start > scene_end + 0.55:
            continue
        for token in token_parts:
            candidates.append((token, start, end))
    if len(candidates) < len(needle):
        return fallback_start, fallback_duration, "estimated"
    # Ordered match with gap tolerance: "struck iceberg" must align to
    # "struck an iceberg" in the transcript, so compact word highlights
    # survive even when the narration inserts filler words between them.
    for start_index in range(0, len(candidates) - len(needle) + 1):
        if candidates[start_index][0] != needle[0]:
            continue
        cursor = start_index + 1
        matched = 1
        for needle_token in needle[1:]:
            found = None
            for j in range(cursor, min(len(candidates), cursor + 4)):
                if candidates[j][0] == needle_token:
                    found = j
                    break
            if found is None:
                break
            cursor = found + 1
            matched += 1
        if matched != len(needle):
            continue
        spoken_start = candidates[start_index][1]
        spoken_end = candidates[cursor - 1][2]
        if spoken_start < scene_start - 0.55 or spoken_start > scene_end + 0.20:
            continue
        local_start = max(0.0, min(scene_duration - 0.35, spoken_start - scene_start - 0.08))
        readable_hold = max(1.0, (spoken_end - spoken_start) + 0.85)
        local_duration = max(0.35, min(2.8, readable_hold, scene_duration - local_start - 0.05))
        return round(local_start, 3), round(local_duration, 3), "whisper"
    return fallback_start, fallback_duration, "estimated"


def build_documentary_edit_plan(
    scene_texts: Iterable[str],
    scene_durations: Iterable[float],
    config: Optional[Dict[str, Any]] = None,
    section_subjects: Optional[Iterable[str]] = None,
    editorial_beats: Optional[Iterable[Dict[str, Any]]] = None,
    whisper_words: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    texts = list(scene_texts or [])
    durations = list(scene_durations or [])
    subjects = list(section_subjects or [])
    beats = list(editorial_beats or [])
    timed_words = list(whisper_words or [])
    settings = normalize_documentary_settings(config)
    scenes: List[Dict[str, Any]] = []
    tracks = {"overlays": [], "highlights": [], "transitions": [], "grade": []}
    offset = 0.0
    previous_subject = ""
    last_major_overlay_scene = -10
    density_gap = {"subtle": 3, "balanced": 2, "dynamic": 1}[settings["density"]]
    recent_templates: List[str] = []
    recent_transitions: List[str] = []

    for index, text in enumerate(texts):
        duration = max(0.1, float(durations[index] if index < len(durations) else 3.0))
        if index < len(beats) and isinstance(beats[index], dict):
            beat = dict(beats[index])
        else:
            try:
                try:
                    from .editorial_brain import deterministic_editorial_beat
                except ImportError:
                    from services.editorial_brain import deterministic_editorial_beat
                beat = deterministic_editorial_beat(str(text or ""), index, len(texts))
            except Exception:
                beat = {"role": "context", "energy": "normal", "treatment": "none", "emphasis": "", "transition": "cut"}
        subject = str(subjects[index] if index < len(subjects) else "").strip()
        boundary = bool(index > 0 and subject and previous_subject and subject.lower() != previous_subject.lower())
        treatment = str(beat.get("treatment") or "none").strip().lower()
        role = str(beat.get("role") or "context").strip().lower()
        selected_for_text = beat.get("selected_for_text") is not False and treatment != "none"
        llm_emphasis = " ".join(str(beat.get("emphasis") or "").split()[:4])[:46]
        date_label = ""
        if settings["date_cards"] and selected_for_text and treatment in {"date", "title", "chapter"}:
            date_candidates = [candidate for candidate in (_first_date(llm_emphasis), _first_date(text)) if candidate]
            date_label = max(date_candidates, key=lambda value: (len(value.split()), len(value)), default="")
        location_label = llm_emphasis if selected_for_text and treatment == "location" else ""
        money = _money_cue(text) if settings["money_counters"] and selected_for_text and treatment == "counter" else None
        statistic_match = _STAT_RE.search(str(text or "")) if settings["key_highlights"] and selected_for_text and not money else None
        quote_match = _QUOTE_RE.search(str(text or "")) if settings["key_highlights"] and selected_for_text else None
        is_cta = bool(index == len(texts) - 1 and _CTA_RE.search(str(text or "")))
        overlay_card = ""
        overlay_type = ""
        if selected_for_text and statistic_match and not date_label and treatment in {"counter", "keyword", "title"}:
            overlay_card, overlay_type = statistic_match.group(0), "statistic"
        elif selected_for_text and treatment in {"chapter", "listicle"} and llm_emphasis and not date_label:
            overlay_card, overlay_type = llm_emphasis, "chapter_title"
        elif selected_for_text and quote_match and not date_label and treatment in {"quote", "title", "lower_third"}:
            overlay_card, overlay_type = quote_match.group(1).strip(), "quote"
        elif selected_for_text and treatment == "person" and llm_emphasis and not date_label:
            overlay_card, overlay_type = llm_emphasis, "person"
        elif location_label and not date_label:
            overlay_card, overlay_type = location_label, "location"
        elif is_cta:
            overlay_card, overlay_type = "SUBSCRIBE", "cta"
        has_major_overlay = bool(date_label or money or overlay_card)
        optional_callout = bool(overlay_type in {"statistic", "quote"} and not date_label and not money)
        if optional_callout and index - last_major_overlay_scene < density_gap and not boundary and not is_cta:
            date_label, money, overlay_card, overlay_type = "", None, "", ""
            has_major_overlay = False
        if has_major_overlay:
            last_major_overlay_scene = index
        highlight = (
            _compact_highlight_phrase(llm_emphasis, text) or _compact_highlight_phrase(_key_phrase(text), text)
            if settings["key_highlights"] and selected_for_text and not has_major_overlay and treatment not in {"none", "date", "location"}
            else ""
        )
        overlay_label = money["label"] if money else date_label or overlay_card
        cue_type = (
            "money" if money else
            "person" if overlay_type == "person" else
            "place" if overlay_type == "location" else
            overlay_type or ("date" if date_label else "highlight")
        )
        template_id = _template_for_scene(cue_type, str(text or ""), index, settings["style"], recent_templates, beat)
        template_background, template_position, template_size, template_animation = _TEMPLATE_STYLE.get(template_id, ("none", "center", 72, "fade"))
        vox_props = _build_vox_props(beat, str(text or ""), overlay_label, cue_type)
        # Inject VOX routing into beat for downstream semantic engine
        beat["vox_template"] = template_id
        beat["vox_props"] = vox_props
        requested_background = str(beat.get("background") or "").strip().lower()
        background_mode = requested_background if requested_background in {"none", "underline", "marker", "edge", "solid", "outline"} else template_background
        requested_animation = str(beat.get("animation") or "").strip().lower()
        animation = requested_animation if requested_animation in {
            "none", "fade", "quick_zoom_in", "quick_zoom_out", "scale_pop", "slide_left", "slide_right", "rise", "wipe", "type_on", "blur_in"
        } else template_animation
        if selected_for_text and (overlay_label or highlight):
            if requested_animation in {"", "none"}:
                animation = template_animation
            # A clean brain decision must stay clean. Older template families
            # used to restore their navy/cyan card here after background=none.
            if settings["style"] == "modern" or cue_type == "highlight":
                background_mode = "none"
        if overlay_label or highlight:
            recent_templates.append(template_id)
        overlay_duration = _overlay_duration(duration, overlay_type or ("money" if money else "date")) if overlay_label else 0.0
        overlay_offset = _cue_offset(text, overlay_label, duration, overlay_duration) if overlay_label else 0.0
        highlight_duration = round(max(0.8, min(3.2, duration - 0.12)), 3) if highlight else 0.0
        highlight_offset = _cue_offset(text, highlight, duration, highlight_duration, default_ratio=0.48) if highlight else 0.0
        overlay_timing_source = "none"
        highlight_timing_source = "none"
        if overlay_label:
            overlay_offset, overlay_duration, overlay_timing_source = _whisper_cue_timing(
                overlay_label, offset, duration, timed_words, overlay_offset, overlay_duration
            )
            if timed_words and overlay_timing_source != "whisper":
                date_label, money, overlay_card, overlay_type = "", None, "", ""
                overlay_label, overlay_duration, overlay_offset = "", 0.0, 0.0
                overlay_timing_source = "rejected_no_whisper_match"
        if highlight:
            highlight_offset, highlight_duration, highlight_timing_source = _whisper_cue_timing(
                highlight, offset, duration, timed_words, highlight_offset, highlight_duration
            )
            if timed_words and highlight_timing_source != "whisper":
                highlight, highlight_duration, highlight_offset = "", 0.0, 0.0
                highlight_timing_source = "rejected_no_whisper_match"
        transition_type = _transition_for_scene(
            beat, boundary, str(text or ""), index, recent_transitions, settings["section_transitions"]
        )
        if transition_type != "cut":
            recent_transitions.append(transition_type)
        energy = str(beat.get("energy") or "normal")
        camera_options = _CAMERA_MOTIONS.get(energy, _CAMERA_MOTIONS["normal"])
        camera_motion = _choose_without_recent(camera_options, _stable_number(text, index, "camera"), [])
        scene_texture = _scene_texture(settings, beat, index, str(text or "")) if settings["color_grade"] else "none"
        caption_position = _rotate_caption_position(template_position, index, bool(overlay_label or highlight))
        scene = {
            "scene_index": index,
            "start": round(offset, 3),
            "duration": round(duration, 3),
            "container_subject": subject,
            "container_boundary": boundary,
            "date_card": date_label,
            "money_counter": money,
            "overlay_card": overlay_card,
            "overlay_type": overlay_type,
            # Keep the legacy plan's family label for older editor consumers;
            # ``vox_template`` remains the actual canonical native ID.
            "overlay_template": (
                f"person_{template_id}" if overlay_label and overlay_type == "person" and not template_id.startswith("person_")
                else f"place_{template_id}" if overlay_label and overlay_type == "location" and not template_id.startswith("place_")
                else template_id if overlay_label else ""
            ),
            "overlay_start": overlay_offset,
            "overlay_duration": overlay_duration,
            "overlay_timing_source": overlay_timing_source,
            "key_highlight": highlight,
            "highlight_template": template_id if highlight else "",
            "highlight_start": highlight_offset,
            "highlight_duration": highlight_duration,
            "highlight_timing_source": highlight_timing_source,
            "text_background": background_mode,
            "text_position": caption_position,
            "text_size": template_size,
            "text_animation": animation,
            "text_animation_out": "fade",
            "transition_in": transition_type,
            "transition_out": "cut",
            "transition_duration": 0.30 if transition_type in {"quick_zoom_in", "quick_zoom_out"} else 0.24,
            "color_grade": settings["style"] if settings["color_grade"] else "none",
            "grade_strength": 0.72 if energy == "calm" else 0.9 if energy in {"normal", "tense"} else 1.0,
            "texture": scene_texture,
            "font_family": settings["font_family"],
            "camera_motion": camera_motion,
            "editorial_beat": beat,
            "vox_props": vox_props,
            "vox_template": template_id,
        }
        scenes.append(scene)
        if overlay_label:
            tracks["overlays"].append({"scene_index": index, "start": round(offset + overlay_offset, 3), "duration": overlay_duration,
                                       "type": "money" if money else overlay_type or "date",
                                       "label": overlay_label, "template": template_id, "animation": animation,
                                       "background": background_mode, "font_size": template_size, "position": caption_position})
        if highlight:
            tracks["highlights"].append({"scene_index": index, "start": round(offset + highlight_offset, 3), "duration": highlight_duration,
                                         "label": highlight, "template": template_id, "animation": animation,
                                         "background": background_mode, "font_size": template_size, "position": caption_position})
        if transition_type != "cut":
            tracks["transitions"].append({
                "scene_index": index, "start": round(offset, 3),
                "duration": scene["transition_duration"], "type": transition_type,
            })
        if settings["color_grade"]:
            tracks["grade"].append({"scene_index": index, "start": round(offset, 3), "duration": round(duration, 3), "type": settings["style"]})
        offset += duration
        if subject:
            previous_subject = subject

    return {"version": 3, "enabled": settings["enabled"], "settings": settings, "duration": round(offset, 3),
            "scenes": scenes, "tracks": tracks, "planning": {"phase": "final", "provisional": False}}


def finalize_documentary_edit_plan(
    provisional_plan: Optional[Dict[str, Any]],
    scene_texts: Iterable[str],
    scene_durations: Iterable[float],
    config: Optional[Dict[str, Any]] = None,
    section_subjects: Optional[Iterable[str]] = None,
    editorial_beats: Optional[Iterable[Dict[str, Any]]] = None,
    whisper_words: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Reconcile the early script plan to final audio-synced scene contracts."""
    seed_scenes = list((provisional_plan or {}).get("scenes") or [])
    final_texts = list(scene_texts or [])
    seed_beats = [scene.get("editorial_beat") or {} for scene in seed_scenes]
    final_beats: List[Dict[str, Any]] = list(editorial_beats or [])
    if not final_beats and seed_beats and final_texts:
        for index in range(len(final_texts)):
            mapped = min(len(seed_beats) - 1, int(index * len(seed_beats) / max(1, len(final_texts))))
            final_beats.append(dict(seed_beats[mapped]))
    plan = build_documentary_edit_plan(
        final_texts, scene_durations, config, section_subjects,
        editorial_beats=final_beats, whisper_words=whisper_words,
    )
    plan["planning"] = {
        "phase": "final",
        "provisional": False,
        "seed_scene_count": len(seed_scenes),
        "final_scene_count": len(plan.get("scenes") or []),
        "reconciled": bool(provisional_plan),
        "final_brain": bool(editorial_beats),
    }
    return plan


def _escape_drawtext(value: str) -> str:
    return str(value or "").replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'").replace("%", r"\%")


@lru_cache(maxsize=256)
def _measure_text_width(label: str, size: int, font_family: str = "") -> int:
    """Measure the rendered text width in pixels using the actual font.

    Drawtext sizes the on-screen text with its own font metrics, so cards and
    underlines drawn from a rough ``len(label)`` estimate drift off-center.  We
    measure with PIL (same ttf) so the background box wraps the real glyph span.
    """
    label = str(label or "")
    size = max(8, int(size or 0))
    if not label:
        return 0
    from PIL import ImageFont

    path = Path(__file__).resolve().parents[2] / "fonts" / str(_FONT_FILES.get(str(font_family or "")) or "")
    try:
        if path.is_file():
            font = ImageFont.truetype(str(path), size)
        else:
            font = ImageFont.load_default(size)
    except Exception:
        font = ImageFont.load_default()
    try:
        bbox = font.getbbox(label)
        return int(bbox[2] - bbox[0])
    except Exception:
        return int(len(label) * size * 0.62)


@lru_cache(maxsize=64)
def _luminance(hex_color: str) -> float:
    """Perceived luminance (0..1) of a '#rrggbb' color."""
    match = re.fullmatch(r"#[0-9a-fA-F]{6}", str(hex_color or ""))
    if not match:
        return 0.12
    hex_text = str(hex_color)[1:]
    r, g, b = (int(hex_text[i:i + 2], 16) for i in (0, 2, 4))
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def _legible_ink_for(background_hex: str) -> str:
    """Pick a readable text color on a given opaque background.

    Dark backgrounds get white text; light backgrounds get near-black text.
    This eliminates white-on-white / white-on-light cards (the accent marker
    used to draw light cyan behind white text, which was unreadable).
    """
    if _luminance(background_hex) < 0.45:
        return "white"
    return "0x0d1117"


def _fontfile_option(scene: Dict[str, Any]) -> str:
    filename = _FONT_FILES.get(str(scene.get("font_family") or "")) or "Poppins-Bold.ttf"
    path = Path(__file__).resolve().parents[2] / "fonts" / str(filename)
    if not path.is_file():
        path = Path(__file__).resolve().parents[2] / "fonts" / "Poppins-Bold.ttf"
    if not path.is_file():
        return "font='Arial Bold'"
    escaped = str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    return f"fontfile='{escaped}'"


@lru_cache(maxsize=512)
def analyze_media_profile(media_path: str) -> Dict[str, float]:
    """Sample one small frame for adaptive grading. Failure stays neutral."""
    path = Path(str(media_path or ""))
    neutral = {"brightness": 0.5, "saturation": 0.45, "contrast": 0.35}
    if not path.is_file():
        return neutral
    try:
        from PIL import Image, ImageStat
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}:
            with Image.open(path) as source:
                image = source.convert("RGB")
                image.thumbnail((96, 54))
        else:
            result = subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", "0.35", "-i", str(path),
                    "-frames:v", "1", "-vf", "scale=96:54", "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
                ],
                capture_output=True, timeout=8,
            )
            if result.returncode != 0 or len(result.stdout) != 96 * 54 * 3:
                return neutral
            image = Image.frombytes("RGB", (96, 54), result.stdout)
        gray = image.convert("L")
        brightness = ImageStat.Stat(gray).mean[0] / 255.0
        contrast = ImageStat.Stat(gray).stddev[0] / 128.0
        hsv = image.convert("HSV")
        saturation = ImageStat.Stat(hsv).mean[1] / 255.0
        return {
            "brightness": round(max(0.0, min(1.0, brightness)), 3),
            "saturation": round(max(0.0, min(1.0, saturation)), 3),
            "contrast": round(max(0.0, min(1.0, contrast)), 3),
        }
    except Exception:
        return neutral


def _adaptive_grade_filter(scene: Dict[str, Any], profile: Optional[Dict[str, float]]) -> str:
    grade = str(scene.get("color_grade") or "none")
    if grade == "none":
        return ""
    strength = max(0.0, min(1.0, float(scene.get("grade_strength") or 0.85)))
    base = {
        "archival": (1.08, 0.72, -0.018, 0.99),
        "history": (1.08, 0.76, -0.014, 0.99),
        "modern": (1.06, 1.08, -0.008, 1.0),
        "minimalist": (1.035, 1.0, 0.0, 1.0),
        "crime": (1.10, 0.82, -0.022, 0.98),
        "standard": (1.03, 1.0, -0.004, 1.0),
        "investigative": (1.08, 0.90, -0.014, 0.99),
    }.get(grade, (1.06, 0.94, -0.01, 0.99))
    contrast, saturation, brightness, gamma = base
    profile = profile or {"brightness": 0.5, "saturation": 0.45, "contrast": 0.35}
    source_brightness = float(profile.get("brightness", 0.5))
    source_saturation = float(profile.get("saturation", 0.45))
    source_contrast = float(profile.get("contrast", 0.35))
    if source_brightness < 0.30:
        brightness += min(0.055, (0.30 - source_brightness) * 0.22)
        gamma += 0.035
    elif source_brightness > 0.74:
        brightness -= min(0.045, (source_brightness - 0.74) * 0.20)
    if source_saturation < 0.18 and grade not in {"archival", "history", "crime"}:
        saturation += 0.10
    elif source_saturation > 0.72:
        saturation -= 0.08
    if source_contrast < 0.18:
        contrast += 0.045
    contrast = 1.0 + (contrast - 1.0) * strength
    saturation = 1.0 + (saturation - 1.0) * strength
    brightness *= strength
    gamma = 1.0 + (gamma - 1.0) * strength
    return f"eq=contrast={contrast:.3f}:saturation={saturation:.3f}:brightness={brightness:.3f}:gamma={gamma:.3f}"


def _transition_filters(scene: Dict[str, Any], width: int, height: int) -> List[str]:
    transition = str(scene.get("transition_in") or "cut")
    duration = max(0.1, float(scene.get("duration") or 0.1))
    transition_duration = min(0.32, max(0.14, float(scene.get("transition_duration") or 0.22)))
    filters: List[str] = []
    if transition in {"crossfade", "fade"}:
        filters.append(f"fade=t=in:st=0:d={transition_duration:.3f}")
    elif transition in {"dip_to_black", "dip_black"}:
        filters.append(f"fade=t=in:st=0:d={transition_duration:.3f}:color=black")
    elif transition == "dip_white":
        filters.append(f"fade=t=in:st=0:d={transition_duration:.3f}:color=white")
    elif transition == "quick_zoom_in":
        frames = max(4, int(transition_duration * 30))
        filters.append(
            f"zoompan=z='if(lte(on,{frames})\\,1+0.16*on/{frames}\\,1.16)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps=30"
        )
    elif transition == "quick_zoom_out":
        frames = max(4, int(transition_duration * 30))
        filters.append(
            f"zoompan=z='if(lte(on,{frames})\\,1.18-0.18*on/{frames}\\,1.0)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps=30"
        )
    elif transition in {"slide_left", "slide_right"}:
        direction = "(iw-ow)*(1-min(t/{:.3f},1))" if transition == "slide_left" else "(iw-ow)*min(t/{:.3f},1)"
        filters.extend([
            "scale=ceil(iw*1.08/2)*2:ceil(ih*1.08/2)*2",
            f"crop={width}:{height}:x='{direction.format(transition_duration)}':y='(ih-oh)/2'",
        ])
    elif transition == "wipe":
        filters.append(f"fade=t=in:st=0:d={max(0.12, transition_duration * 0.72):.3f}:color=white")
    if scene.get("transition_out") not in {None, "", "cut"} and duration > 0.5:
        filters.append(f"fade=t=out:st={max(0.0, duration - transition_duration):.3f}:d={transition_duration:.3f}")
    return filters


def _text_position(position: str, height: int) -> tuple[str, str]:
    pos = normalize_text_position(position)
    if pos == "center":
        return "(w-text_w)/2", "(h-text_h)/2"
    elif pos in ("top_right", "bottom_right"):
        x = "w-text_w-w*0.06"
    else:
        x = "w*0.06"
    if pos in ("top_left", "top_right"):
        y = "h*0.08"
    else:
        y = "h-text_h-h*0.10"
    return x, y


def _explicit_text_percent(value: Any) -> Optional[float]:
    """Normalize editor x/y values to a 0..1 screen coordinate."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not 0.0 <= number <= 100.0:
        return None
    # Editor blocks use percentages (20, 75); API callers may provide the
    # equivalent normalized form (0.20, 0.75).
    return number if number <= 1.0 else number / 100.0


def _animated_text_geometry(animation: str, target_x: str, target_y: str, start: float, size: int) -> tuple[str, str, str, str]:
    progress = f"min(max((t-{start:.3f})/0.28,0),1)"
    x, y = target_x, target_y
    fontsize = str(size)
    if animation == "slide_left":
        x = f"-text_w+(({target_x})+text_w)*{progress}"
    elif animation == "slide_right":
        x = f"w-(w-({target_x}))*{progress}"
    elif animation == "rise":
        y = f"h-(h-({target_y}))*{progress}"
    elif animation in {"quick_zoom_in", "scale_pop"}:
        y = f"({target_y})+18*(1-{progress})"
    elif animation == "quick_zoom_out":
        y = f"({target_y})-14*(1-{progress})"
    alpha = f"if(lt(t,{start:.3f}),0,min(max((t-{start:.3f})/0.18,0),1))"
    return x, y, fontsize, alpha


def _text_filters(scene: Dict[str, Any], width: int, height: int, label: str, money: Optional[Dict[str, Any]], highlight: bool) -> List[str]:
    if not label:
        return []
    cue_prefix = "highlight" if highlight else "overlay"
    font_scene = dict(scene)
    font_scene["font_family"] = scene.get(f"{cue_prefix}_font_family") or scene.get("font_family")
    font_option = _fontfile_option(font_scene)
    start_key = "highlight_start" if highlight else "overlay_start"
    duration_key = "highlight_duration" if highlight else "overlay_duration"
    cue_start = max(0.0, float(scene.get(start_key) or (0.35 if highlight else 0.0)))
    cue_duration = max(0.45, float(scene.get(duration_key) or 2.4))
    cue_end = min(float(scene.get("duration") or cue_start + cue_duration), cue_start + cue_duration)
    position = normalize_text_position(
        scene.get(f"{cue_prefix}_position") or scene.get("text_position") or ("center" if highlight else "top_left")
    )
    anchor = guide_anchor(position)
    target_x, target_y = _text_position(position, height)
    explicit_x = _explicit_text_percent(scene.get(f"{cue_prefix}_x"))
    explicit_y = _explicit_text_percent(scene.get(f"{cue_prefix}_y"))
    if explicit_x is not None:
        target_x = f"(w-text_w)*{explicit_x:.4f}"
    if explicit_y is not None:
        target_y = f"(h-text_h)*{explicit_y:.4f}"
    size = max(32, min(160, int(scene.get(f"{cue_prefix}_size") or scene.get("text_size") or (82 if highlight else 64))))
    animation = str(scene.get(f"{cue_prefix}_animation") or scene.get("text_animation") or "fade")
    x, y, fontsize, alpha = _animated_text_geometry(animation, target_x, target_y, cue_start, size)
    background = str(scene.get(f"{cue_prefix}_background") or scene.get("text_background") or "solid")
    # ``none`` is a deliberate editorial choice for modern/clean captions:
    # readability comes from the shadow and contrast grade, without putting a
    # static card over the footage.  Only an explicit card style adds a box.
    if background in {"none", ""}:
        background = "none"
    opacity = max(0.1, min(1.0, float(scene.get(f"{cue_prefix}_opacity") or 100) / 100.0))
    alpha = f"({alpha})*{opacity:.3f}"
    raw_color = str(scene.get(f"{cue_prefix}_font_color") or scene.get("font_color") or "#ffffff").strip()
    font_color = f"0x{raw_color[1:]}" if re.fullmatch(r"#[0-9a-fA-F]{6}", raw_color) else "white"
    accent = "0x42e6ff" if not money else "0xffd34e"
    safe = _escape_drawtext(label.upper())
    # The accent "marker" box is light (cyan/gold), so white text on it was
    # illegible. Keep the user's explicit ink when supplied, otherwise pick a
    # dark ink on the light marker and white on the dark solid/edge card.
    explicit_ink = scene.get(f"{cue_prefix}_font_color") or scene.get("font_color")
    background_hex = (
        "#09111f" if background in {"solid", "edge"} else
        "#42e6ff" if background == "marker" else
        (raw_color if re.fullmatch(r"#[0-9a-fA-F]{6}", raw_color) else "")
    )
    if not explicit_ink and background in {"marker", "solid", "edge"} and background_hex:
        font_color = _legible_ink_for(background_hex)
    relative_t = f"(t-{cue_start:.3f})"
    if money and "display_value" in money and float(money.get("display_value") or 0).is_integer():
        target = max(0, int(float(money.get("display_value") or 0)))
        prefix = _escape_drawtext(str(money.get("prefix") or "$"))
        suffix = _escape_drawtext(str(money.get("suffix") or ""))
        safe = f"{prefix}%{{eif\\:min({target}\\,{target}*max(0\\,{relative_t})/0.8)\\:d}}{suffix}"
    measured_w = _measure_text_width(str(label or ""), size, font_scene.get("font_family") or "")
    options = ""
    filters: List[str] = []
    if background == "marker":
        options = f":box=1:boxcolor={accent}@0.82:boxborderw=16"
    elif background == "solid":
        options = ":box=1:boxcolor=0x09111f@0.88:boxborderw=18"
    elif background == "outline":
        options = f":box=1:boxcolor=0x09111f@0.88:boxborderw=18:borderw=3:bordercolor={accent}@0.95"
    elif background == "edge":
        inner_pad = max(40, int(size * 0.55))
        card_width = min(int(measured_w + 2 * inner_pad), int(width * 0.84))
        card_height = max(78, int(size * 1.52))
        if explicit_x is not None:
            box_x = int(max(0.0, min(1.0, explicit_x)) * max(0, width - card_width))
        elif anchor["horizontal"] == "center":
            box_x = int((width - card_width) / 2)
            box_y = int((height - card_height) / 2)
        else:
            box_x = int(width * 0.06) if anchor["horizontal"] == "left" else int(width - card_width - width * 0.06)
        if explicit_y is not None:
            box_y = int(max(0.0, min(1.0, explicit_y)) * max(0, height - card_height))
        elif anchor["horizontal"] == "center":
            box_y = int((height - card_height) / 2)
        else:
            box_y = int(height * 0.08) if anchor["vertical"] == "top" else int(height - card_height - height * 0.10)
        filters.extend([
            f"drawbox=x={box_x}:y={box_y}:w={card_width}:h={card_height}:color=0x09111f@0.86:t=fill:enable='between(t,{cue_start:.3f},{cue_end:.3f})'",
            f"drawbox=x={box_x}:y={box_y}:w=8:h={card_height}:color={accent}@1:t=fill:enable='between(t,{cue_start:.3f},{cue_end:.3f})'",
        ])
        if explicit_x is None:
            target_x = f"({box_x} + ({card_width} - text_w)/2)"
        if explicit_y is None:
            target_y = f"({box_y} + ({card_height} - text_h)/2)"
        x, y, fontsize, alpha = _animated_text_geometry(animation, target_x, target_y, cue_start, size)
    elif background == "underline":
        underline_pad = max(26, int(size * 0.42))
        line_width = min(int(measured_w + 2 * underline_pad), int(width * 0.70))
        if anchor["horizontal"] == "center":
            line_x = int((width - line_width) / 2)
            line_y = int((height + size) / 2)
        else:
            line_x = int(width * 0.06) if anchor["horizontal"] == "left" else int(width - line_width - width * 0.06)
            line_y = int(height * 0.08 + size * 1.1) if anchor["vertical"] == "top" else int(height - height * 0.10)
        options = ":box=1:boxcolor=0x09111f@0.88:boxborderw=18"
        filters.append(
            f"drawbox=x={line_x}:y={line_y}:w={line_width}:h=8:color={accent}@0.95:t=fill:enable='between(t,{cue_start:.3f},{cue_end:.3f})'"
        )
    elif background == "none":
        options = ""
    else:
        options = ":box=1:boxcolor=0x09111f@0.88:boxborderw=18"
    filters.append(
        f"drawtext=text='{safe}':{font_option}:fontsize='{fontsize}':fontcolor={font_color}:"
        f"borderw={3 if background != 'outline' else 1}:bordercolor=black@0.92:"
        "shadowcolor=black@0.96:shadowx=5:shadowy=5:"
        f"x='{x}':y='{y}':alpha='{alpha}'{options}:enable='between(t,{cue_start:.3f},{cue_end:.3f})'"
    )
    return filters


def ffmpeg_scene_filters(
    scene: Optional[Dict[str, Any]], width: int, height: int,
    media_profile: Optional[Dict[str, float]] = None,
) -> List[str]:
    if not scene:
        return []
    filters: List[str] = []
    grade_filter = _adaptive_grade_filter(scene, media_profile)
    if grade_filter:
        filters.append(grade_filter)
    texture = str(scene.get("texture") or "none")
    if texture == "film_grain":
        filters.append("noise=alls=2.2:allf=t+u")
    elif texture == "paper":
        filters.append("noise=alls=1.4:allf=t")
    elif texture == "scanlines":
        filters.append("drawgrid=w=iw:h=4:t=1:c=black@0.08")
    elif texture == "grid":
        filters.append("drawgrid=w=96:h=96:t=1:c=white@0.025")

    filters.extend(_transition_filters(scene, width, height))

    editor_blocks = scene.get("editor_text_blocks") if isinstance(scene.get("editor_text_blocks"), list) else []
    if editor_blocks:
        for block in editor_blocks[:24]:
            if not isinstance(block, dict):
                continue
            highlight_role = str(block.get("role") or "overlay") == "highlight"
            prefix = "highlight" if highlight_role else "overlay"
            label = str(block.get("label") or "").strip()
            if not label:
                continue
            block_scene = dict(scene)
            for key in (
                "start", "duration", "position", "x", "y", "size", "font_family",
                "font_color", "opacity", "animation", "background",
            ):
                if block.get(key) is not None:
                    block_scene[f"{prefix}_{key}"] = block[key]
            money = (
                scene.get("money_counter")
                if str(block.get("template") or "").startswith(("counter", "money"))
                and isinstance(scene.get("money_counter"), dict)
                else None
            )
            filters.extend(_text_filters(block_scene, width, height, label, money, highlight=highlight_role))
    else:
        label = str(scene.get("date_card") or scene.get("overlay_card") or "").strip()
        money = scene.get("money_counter") if isinstance(scene.get("money_counter"), dict) else None
        if money:
            label = str(money.get("label") or "").strip()
        if label:
            filters.extend(_text_filters(scene, width, height, label, money, highlight=False))
        highlight = str(scene.get("key_highlight") or "").strip()
        if highlight:
            filters.extend(_text_filters(scene, width, height, highlight, None, highlight=True))
    return filters
