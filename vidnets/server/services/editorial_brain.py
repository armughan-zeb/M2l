"""LLM-directed editorial beats with a fast deterministic fallback."""

from __future__ import annotations

import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from .job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from typing import Any, Dict, Iterable, List, Mapping, Optional


EDITORIAL_ROLES = {
    "intro", "setup", "context", "reveal", "evidence", "statistic",
    "escalation", "quote", "chapter", "conclusion", "cta",
}
TREATMENTS = {
    "none", "keyword", "title", "lower_third", "date", "location", "person", "listicle",
    "counter", "quote", "chapter", "cta",
}
ANIMATIONS = {
    "none", "fade", "quick_zoom_in", "quick_zoom_out", "scale_pop",
    "slide_left", "slide_right", "rise", "wipe", "type_on", "blur_in",
}
BACKGROUNDS = {"none", "underline", "marker", "edge", "solid", "outline"}
TRANSITIONS = {"cut", "fade", "dip_black", "dip_white", "quick_zoom_in", "quick_zoom_out", "slide_left", "slide_right", "wipe"}
ENERGIES = {"calm", "normal", "tense", "energetic", "climactic"}
SOUND_INTENTS = {
    "none", "quiet_texture", "transition_air", "paper_action", "typing",
    "data_tick", "location_ping", "signal", "tension", "reveal", "impact",
    "human_stakes", "archive", "technology",
}
HIGHLIGHT_KINDS = {
    "none", "milestone", "scale", "turning_point", "warning", "contrast",
    "consequence", "quote", "conclusion", "location", "key_fact", "human_stakes",
}

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "for", "from", "had",
    "has", "have", "he", "her", "his", "in", "into", "is", "it", "its", "of", "on", "or",
    "she", "that", "the", "their", "them", "they", "this", "to", "was", "were", "with", "would",
}
_POWER_WORDS = {
    "first", "last", "largest", "smallest", "deadliest", "secret", "critical", "confirmed", "revealed",
    "discovered", "record", "never", "only", "final", "missing", "banned", "collapsed", "exploded",
    "won", "lost", "million", "billion", "percent", "warning", "evidence", "truth", "cost",
    "maiden", "unsinkable", "unprecedented", "breakthrough", "historic", "defining",
    "victims", "survivors", "lives", "immigrants", "passengers", "families", "future",
    "changed", "struck", "collided", "collision", "erupted",
}

_LOCATION_RE = re.compile(
    r"\b(?:in|from|to|at|near|inside|outside|across)\s+"
    r"([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){0,3})\b"
)
_MONTH_NAMES = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
}
_DATE_RE = re.compile(
    r"\b(?:(?:january|february|march|april|may|june|july|august|september|october|november|december)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+(?:19|20)\d{2})?|"
    r"\d{1,2}\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)"
    r"(?:\s+(?:19|20)\d{2})?|(?:19|20)\d{2})\b",
    re.I,
)
_WEAK_SINGLE_WORDS = {
    "arrived", "designed", "directly", "even", "lost", "people", "passenger", "passengers",
    "person", "crew", "ship", "thing", "things", "world", "safety", "event", "events",
}
_WEAK_TRAILING_WORDS = {
    "a", "an", "and", "as", "at", "by", "directly", "even", "for", "from", "in", "into",
    "of", "on", "or", "the", "to", "with", "without", "which", "that", "this", "its",
}
_EDITORIAL_SIGNAL_WORDS = _POWER_WORDS | {
    "aftermath", "attack", "collision", "danger", "disaster", "earthquake", "evacuated", "explosion",
    "flooded", "flooding", "iceberg", "impact", "lifeboat", "lifeboats", "rescue", "rescued", "sank",
    "sinking", "split", "struck", "survived", "survival", "tearing", "warning", "wreck",
}

# Singular generic subject/verb words that never carry a highlight alone.
_GENERIC_SINGLE_HIGHLIGHTS = {
    "came", "cost", "died", "families", "immigrants", "lives", "lost", "made",
    "passengers", "people", "ship", "survivors", "they", "victims", "wanted",
    "went", "won", "worked",
}


_HIGHLIGHT_STOPWORDS = {
    "a", "an", "and", "as", "at", "be", "by", "for", "from", "had", "has",
    "have", "in", "into", "is", "it", "its", "of", "on", "onto", "or",
    "that", "the", "their", "then", "there", "they", "this", "to", "was",
    "were", "with", "would",
}


def _single_emphasis_word(phrase: str) -> str:
    """Reduce a highlight phrase to exactly ONE salient word.

    Generic subject/verb words ("people", "ship", "went", "changed") read as a
    meaningless standalone card, so they are skipped in favour of a concrete,
    content-bearing word the viewer can decode.
    """
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'%/-]*", str(phrase or ""))
    if not words:
        return ""
    meaningful = [
        w for w in words
        if w.lower() not in _HIGHLIGHT_STOPWORDS
        and w.lower() not in _WEAK_SINGLE_WORDS
        and w.lower() not in _GENERIC_SINGLE_HIGHLIGHTS
        and len(w) > 2
    ]
    pool = meaningful or [w for w in words if w.lower() not in _HIGHLIGHT_STOPWORDS and len(w) > 2] or words
    for word in pool:
        if re.search(r"\d|%", word):
            return word
    for word in pool:
        if word.lower() in _EDITORIAL_SIGNAL_WORDS:
            return word
    for word in pool:
        if len(word) >= 5:
            return word
    return pool[0]


def _clean_phrase(value: Any, scene_text: str) -> str:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'%-]*", str(value or ""))
    if not words:
        return ""
    phrase = " ".join(words[:5]).strip()
    if len(phrase) > 46:
        phrase = " ".join(phrase.split()[:4])[:46].strip()
    if not phrase:
        return ""
    scene_words = [word.lower() for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9'%-]*", str(scene_text or ""))]
    phrase_words = [word.lower() for word in phrase.split()]
    grounded = any(
        scene_words[index:index + len(phrase_words)] == phrase_words
        for index in range(0, max(0, len(scene_words) - len(phrase_words) + 1))
    )
    return phrase if grounded else ""


def _clean_sound_anchor(value: Any, scene_text: str) -> str:
    """Return an exact short spoken phrase for Whisper-timed sound placement."""
    token_pattern = r"\d+(?:[.,]\d+)?|[A-Za-z]+(?:['’][A-Za-z]+)?"
    raw = str(value or "").strip()
    anchor_matches = list(re.finditer(token_pattern, raw))
    scene_matches = list(re.finditer(token_pattern, str(scene_text or "")))
    if not anchor_matches or len(anchor_matches) > 5:
        return ""
    normalize = lambda token: re.sub(r"[^a-z0-9]+", "", token.casefold())
    anchor_tokens = [normalize(match.group(0)) for match in anchor_matches]
    scene_tokens = [normalize(match.group(0)) for match in scene_matches]
    for index in range(0, len(scene_tokens) - len(anchor_tokens) + 1):
        if scene_tokens[index:index + len(anchor_tokens)] == anchor_tokens:
            return str(scene_text)[scene_matches[index].start():scene_matches[index + len(anchor_tokens) - 1].end()]
    return ""


# Words that bind tightly to the preceding word as part of the same phrase.
# A highlight phrase ending right before one of these is a dangling
# mid-sentence fragment ("...for the safety" + "of life", "...an iceberg
# directly" + "ahead"). Optional adjuncts ("during the disaster", "before
# dawn") do NOT count — a complete phrase may end before them.
_DANGLING_NEXT_WORDS = {
    "a", "ahead", "an", "at", "away", "by", "for", "from", "in", "into",
    "of", "on", "onto", "that", "the", "to", "toward", "towards", "under",
    "upon", "with", "within",
}


def _is_dangling_fragment(phrase: str, source: str) -> bool:
    """True when a multi-word phrase ends right before a clause-continuation word."""
    words = [word.lower() for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9'%-]*", str(phrase or ""))]
    if len(words) < 2:
        return False
    scene_words = [word.lower() for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9'%-]*", str(source or ""))]
    n = len(words)
    for index in range(0, max(0, len(scene_words) - n + 1)):
        if scene_words[index:index + n] == words:
            next_word = scene_words[index + n] if index + n < len(scene_words) else ""
            if next_word and next_word not in {"and", "or"} and next_word in _DANGLING_NEXT_WORDS:
                return True
            break
    return False


def _editorial_phrase_is_worthy(phrase: str, treatment: str, highlight_kind: str) -> bool:
    """Reject grounded text fragments that still read like accidental subtitles."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'%-]*", str(phrase or ""))
    lowered = [word.lower() for word in words]
    if not words:
        return False
    if treatment == "date" or highlight_kind == "milestone":
        return bool(_DATE_RE.fullmatch(" ".join(words)))
    if treatment == "location" or highlight_kind == "location":
        return 1 <= len(words) <= 4 and all(word not in _WEAK_TRAILING_WORDS for word in (lowered[0], lowered[-1]))
    if treatment == "person":
        return (
            1 <= len(words) <= 4
            and any(word[:1].isupper() for word in words)
            and not any(word in _WEAK_SINGLE_WORDS for word in lowered)
        )
    if treatment == "counter" or highlight_kind == "scale":
        return len(words) <= 4 and any(re.search(r"\d|%", word) for word in words)
    if len(words) > 5 or lowered[-1] in _WEAK_TRAILING_WORDS:
        return False
    if len(words) == 1:
        if lowered[0] in _GENERIC_SINGLE_HIGHLIGHTS:
            return False
        return (
            bool(re.search(r"\d|%", words[0]))
            or lowered[0] in _POWER_WORDS
            or lowered[0] in _EDITORIAL_SIGNAL_WORDS
        )
    meaningful = [word for word in lowered if word not in _STOPWORDS and len(word) > 2]
    if len(meaningful) < 1:
        return False
    if highlight_kind == "human_stakes":
        return bool(set(meaningful) & {
            "died", "displaced", "evacuated", "killed", "lost", "rescued", "rescue",
            "saved", "save", "survived", "survival",
        })
    # Trust the LLM's judgement for these highlight kinds
    if highlight_kind in {"turning_point", "warning", "contrast", "consequence", "conclusion", "key_fact", "quote"}:
        return True
    
    # If it has at least 2 meaningful words, it's likely a valid phrase, no need to strictly enforce signal words
    if len(meaningful) >= 2:
        return True
        
    return bool(set(meaningful) & _EDITORIAL_SIGNAL_WORDS)


def deterministic_editorial_beat(text: str, index: int, total: int) -> Dict[str, Any]:
    source = re.sub(r"\s+", " ", str(text or "")).strip()
    lower = source.lower()
    role = "intro" if index == 0 else "conclusion" if index == total - 1 else "context"
    decisive_action_match = re.search(
        r"\b(?:struck the [A-Za-z0-9'\-]+|collided with [A-Za-z0-9'\-]+)\b",
        source,
        re.I,
    )
    turning_match = decisive_action_match or re.search(
        r"\b(?:everything changed|changed everything|struck the [A-Za-z0-9'\-]+|collided with [A-Za-z0-9'\-]+|"
        r"the collision|the [A-Za-z0-9'\-]+ erupted)\b",
        source,
        re.I,
    )
    date_match = _DATE_RE.search(source)
    has_date = bool(date_match)
    has_full_date = bool(date_match and any(month in date_match.group(0).lower() for month in _MONTH_NAMES))
    scale_match = re.search(
        r"(?:[$\u00a3\u20ac]\s*)?\d[\d,.]*\s*(?:percent|%|million|billion|trillion|thousand|"
        r"kilometers?|meters?|miles?|feet|tons?|people|deaths?|victims?)\b",
        source,
        re.I,
    )
    has_scale = bool(scale_match)
    if has_full_date:
        role = "evidence"
    elif has_scale:
        role = "statistic"
    elif has_date:
        role = "evidence"
    elif re.search(r"\b(?:revealed|discovered|truth|secret|finally|evidence)\b", lower):
        role = "reveal"
    elif turning_match:
        role = "reveal"
    elif re.search(r"\b(?:subscribe|follow|like and share|join us)\b", lower):
        role = "cta"
    energy = "climactic" if role in {"reveal", "cta"} else "energetic" if role == "statistic" else "normal"

    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9'%-]*", source)
    scored = []
    for token_index, token in enumerate(tokens):
        normalized = token.lower()
        if normalized in _STOPWORDS or len(token) < 3:
            continue
        score = 5 if normalized in _POWER_WORDS else 0
        score += 4 if re.search(r"\d|%", token) else 0
        score += 2 if token.isupper() and len(token) > 2 else 0
        scored.append((score, token_index, token))
    scored.sort(key=lambda item: (-item[0], item[1]))
    if scored and scored[0][0] >= 3:
        emphasis = scored[0][2]
    elif scored:
        emphasis = max((item[2] for item in scored), key=len, default="")
    else:
        emphasis = ""

    location_match = _LOCATION_RE.search(source)
    location_phrase = str(location_match.group(1) if location_match else "").strip()
    if location_phrase and location_phrase.split()[0].lower() in _MONTH_NAMES:
        location_phrase = ""
    human_action_match = re.search(
        r"\b(?:rescue|rescued|save|saved)\s+(?:the\s+)?(?:survivors?|victims?|people|passengers?|crew|families|lives)\b",
        source,
        re.I,
    )

    treatment = "counter" if role == "statistic" else "date" if has_date else "cta" if role == "cta" else "keyword" if emphasis else "none"
    highlight_kind = (
        "scale" if role == "statistic" else
        "milestone" if has_date else
        "location" if location_phrase else
        "turning_point" if role == "reveal" else
        "warning" if re.search(r"\b(?:warning|danger|threat|risk)\b", lower) else
        "consequence" if re.search(r"\b(?:caused|consequence|destroyed|died|killed|led to|resulted in|forced)\b", lower) else
        "contrast" if re.search(r"\b(?:but|however|despite|instead|unlike|whereas)\b", lower) else
        "quote" if re.search(r"[\"\u201c\u201d].{2,80}[\"\u201c\u201d]", source) else
        "key_fact" if re.search(r"\b(?:first|last|largest|smallest|deadliest|record|maiden|unsinkable|unprecedented|breakthrough|historic|defining|only|never)\b", lower) else
        "human_stakes" if human_action_match or re.search(r"\b(?:victims?|survivors?|lives|families|children|immigrants?|refugees?|passengers?|crew|workers?|better future)\b", lower) else
        "conclusion" if role in {"conclusion", "cta"} and bool(emphasis) else
        ("key_fact" if bool(emphasis) else "none")
    )
    if highlight_kind == "scale" and scale_match:
        emphasis = scale_match.group(0)
        treatment = "counter"
    elif highlight_kind == "milestone" and date_match:
        emphasis = date_match.group(0)
        treatment = "date"
    elif highlight_kind == "location":
        emphasis = location_phrase
        treatment = "location"
    elif highlight_kind == "turning_point" and turning_match:
        emphasis = turning_match.group(0)
        treatment = "keyword"
    elif highlight_kind == "human_stakes" and human_action_match:
        emphasis = human_action_match.group(0)
        treatment = "keyword"
    importance = (
        0.78 if role in {"statistic", "reveal", "cta"} else
        0.70 if has_date else
        0.72 if highlight_kind in {"key_fact", "warning", "consequence", "contrast"} else
        0.68 if highlight_kind == "human_stakes" else
        0.66 if highlight_kind == "location" else
        0.35
    )
    emphasis = _clean_phrase(emphasis, source)
    if not _editorial_phrase_is_worthy(emphasis, treatment, highlight_kind):
        emphasis = ""
        treatment = "none"
        highlight_kind = "none"
        importance = min(importance, 0.40)
    return {
        "scene_index": index,
        "role": role,
        "emphasis": emphasis,
        "treatment": treatment,
        "animation": "scale_pop" if energy in {"energetic", "climactic"} else "fade",
        "background": "none" if treatment == "keyword" else "edge" if treatment in {"counter", "cta"} else "none",
        "transition": "cut",
        "energy": energy,
        "grade": "auto",
        "texture": "none",
        "confidence": 0.55,
        "importance": importance,
        "highlight_kind": highlight_kind,
        "editorial_reason": "deterministic semantic signal" if highlight_kind != "none" else "",
        "source": "deterministic",
    }


def _semantic_fallback_beat(text: str, index: int, total: int) -> Dict[str, Any]:
    """Recover one compact, grounded fact when the LLM returns no visible cue.

    This is deliberately a rescue path, not a second editorial system. It only
    runs when the brain produced no usable moments and chooses an exact phrase
    already present in the narration. That keeps the normal LLM ranking in
    charge while preventing an all-empty edit plan from reaching the renderer.
    """
    fallback = deterministic_editorial_beat(text, index, total)
    if fallback.get("treatment") != "none" and fallback.get("emphasis"):
        return fallback

    source = re.sub(r"\s+", " ", str(text or "")).strip()
    if not source:
        return fallback
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9'%-]*", source)
    if len(tokens) < 2:
        return fallback

    number_words = {
        "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
        "hundred", "hundreds", "thousand", "thousands", "million", "millions", "billion", "billions",
    }
    filler_words = {"now", "also", "already", "just", "really", "even", "still", "only", "more", "very"}
    generic_verbs = {
        "returns", "return", "features", "feature", "includes", "include", "built", "build",
        "created", "create", "promising", "promise", "expect", "expects", "expected", "lead",
        "leads", "shows", "show", "remain", "remains", "could", "will", "may", "make", "makes",
        "made", "gives", "give", "reaches", "reached", "represents", "means", "combines", "combine",
        "follow", "follows", "watch", "watches", "generate", "generates", "attracting", "attracted",
    }
    cue_words = _EDITORIAL_SIGNAL_WORDS | _POWER_WORDS | {
        "returns", "features", "includes", "built", "created", "promising", "detail",
        "detailed", "dynamic", "realistic", "expected", "forecast", "impact", "influence",
        "ambition", "technology", "budget", "community", "launch", "release", "records",
    }
    candidates = []
    # Preserve coordinated names as a complete grounded phrase ("Jason and
    # Lucia") instead of reducing them to a dangling verb fragment.
    for proper_match in re.finditer(
        r"\b([A-Z][A-Za-z0-9'%-]+\s+(?:and|&)\s+[A-Z][A-Za-z0-9'%-]+)\b",
        source,
    ):
        phrase = proper_match.group(1).strip()
        candidates.append((7, -proper_match.start(), -3, phrase))
    for size in (3, 2):
        for start in range(0, len(tokens) - size + 1):
            window = tokens[start:start + size]
            lowered = [word.lower() for word in window]
            if lowered[0] in _STOPWORDS or lowered[-1] in _WEAK_TRAILING_WORDS:
                continue
            meaningful = [word for word in lowered if word not in _STOPWORDS and len(word) > 2]
            if len(meaningful) < 2:
                continue
            if any(word in filler_words for word in lowered):
                continue
            if any(word in generic_verbs for word in lowered):
                continue
            has_number = any(re.search(r"\d|%", word) or word in number_words for word in lowered)
            has_signal = bool(set(meaningful) & (cue_words - generic_verbs))
            has_proper = any(word[:1].isupper() and word.lower() not in _MONTH_NAMES for word in window)
            if not (has_number or has_signal or has_proper):
                continue
            if all(word in _WEAK_SINGLE_WORDS for word in meaningful):
                continue
            score = (4 if has_number else 0) + (3 if has_signal else 0) + (2 if has_proper else 0)
            score += 1 if size == 2 else 0
            # Avoid selecting the capitalized first word of a sentence as if it
            # were a proper name unless the phrase contains another signal.
            if start == 0 and has_proper and not (has_signal or has_number):
                continue
            candidates.append((score, -start, -size, " ".join(window)))
    if not candidates:
        # Plain narration should stay plain. A fabricated two-word card is
        # worse than a clean visual pause and was the source of pronoun and
        # sentence-starter cards in fallback renders.
        return fallback
    candidates.sort(reverse=True)
    phrase = candidates[0][3]
    return {
        **fallback,
        "role": "evidence",
        "emphasis": phrase,
        "treatment": "keyword",
        "animation": ("scale_pop", "slide_left", "quick_zoom_in", "fade")[index % 4],
        "background": "none",
        "highlight_kind": "key_fact",
        "importance": 0.60,
        "confidence": 0.46,
        "editorial_reason": "exact narrated phrase recovered after empty brain response",
        "source": "semantic-fallback",
        "scene_index": index,
    }


def _normalize_beat(raw: Dict[str, Any], text: str, index: int, total: int, source_provider: str = "openrouter") -> Dict[str, Any]:
    fallback = deterministic_editorial_beat(text, index, total)
    raw_grounded_emphasis = _clean_phrase(raw.get("emphasis"), text)
    beat = {
        **fallback,
        "role": str(raw.get("role") or fallback["role"]).strip().lower(),
        "emphasis": raw_grounded_emphasis,
        "treatment": str(raw.get("treatment") or fallback["treatment"]).strip().lower(),
        "animation": str(raw.get("animation") or fallback["animation"]).strip().lower(),
        "background": str(raw.get("background") or fallback["background"]).strip().lower(),
        "transition": str(raw.get("transition") or fallback["transition"]).strip().lower(),
        "energy": str(raw.get("energy") or fallback["energy"]).strip().lower(),
        "grade": str(raw.get("grade") or "auto").strip().lower(),
        "texture": str(raw.get("texture") or "none").strip().lower(),
        "confidence": max(0.0, min(1.0, float(raw.get("confidence") if raw.get("confidence") is not None else fallback["confidence"]))),
        "importance": max(0.0, min(1.0, float(
            raw.get("importance") if raw.get("importance") is not None
            else raw.get("confidence") if raw.get("confidence") is not None
            else fallback["importance"]
        ))),
        "highlight_kind": str(raw.get("highlight_kind") or fallback["highlight_kind"]).strip().lower(),
        "editorial_reason": str(raw.get("editorial_reason") or "").strip()[:160],
        "sound_intent": str(raw.get("sound_intent") or "none").strip().lower(),
        "sound_reason": str(raw.get("sound_reason") or "").strip()[:120],
        "sound_anchor": _clean_sound_anchor(raw.get("sound_anchor"), text),
        "sound_intensity": max(0.0, min(1.0, float(raw.get("sound_intensity") or 0.0))),
        "source": str(raw.get("source") or source_provider),
        "scene_index": index,
    }
    if beat["role"] not in EDITORIAL_ROLES:
        beat["role"] = fallback["role"]
    if beat["treatment"] not in TREATMENTS:
        beat["treatment"] = fallback["treatment"]
    if beat["animation"] not in ANIMATIONS:
        beat["animation"] = fallback["animation"]
    if beat["background"] not in BACKGROUNDS:
        beat["background"] = fallback["background"]
    if beat["transition"] not in TRANSITIONS:
        beat["transition"] = "cut"
    if beat["energy"] not in ENERGIES:
        beat["energy"] = fallback["energy"]
    if beat["highlight_kind"] not in HIGHLIGHT_KINDS:
        beat["highlight_kind"] = fallback["highlight_kind"]
    if beat["sound_intent"] not in SOUND_INTENTS:
        beat["sound_intent"] = "none"
    rejected_grounded_phrase = (
        bool(beat["emphasis"])
        and (
            _is_dangling_fragment(beat["emphasis"], text)
            or not _editorial_phrase_is_worthy(beat["emphasis"], beat["treatment"], beat["highlight_kind"])
        )
    )
    if rejected_grounded_phrase:
        beat.update({
            "emphasis": "", "treatment": "none", "animation": "none",
            "background": "none", "highlight_kind": "none", "importance": 0.0,
            "editorial_reason": "rejected incomplete or generic text fragment",
        })
        if fallback["treatment"] != "none" and fallback["emphasis"]:
            beat.update({
                "emphasis": fallback["emphasis"], "treatment": fallback["treatment"],
                "animation": fallback["animation"], "background": fallback["background"],
                "highlight_kind": fallback["highlight_kind"], "importance": fallback["importance"],
                "editorial_reason": "replaced weak phrase with compact narrated fact",
                "source": f"{source_provider}+deterministic",
            })
    if not beat["emphasis"] and beat["treatment"] in {"keyword", "title", "lower_third"}:
        beat["treatment"] = "none"
    if (
        beat["treatment"] == "none"
        and str(raw.get("treatment") or "").strip().lower() == "none"
        and not str(raw.get("emphasis") or "").strip()
        and fallback["treatment"] != "none"
        and fallback["highlight_kind"] in {"milestone", "scale", "turning_point", "warning", "contrast", "consequence", "location", "key_fact", "human_stakes"}
        and float(fallback.get("importance") or 0.0) >= 0.66
    ):
        beat.update({
            "emphasis": fallback["emphasis"],
            "treatment": fallback["treatment"],
            "animation": fallback["animation"],
            "background": fallback["background"],
            "highlight_kind": fallback["highlight_kind"],
            "importance": max(float(beat.get("importance") or 0.0), float(fallback["importance"])),
            "editorial_reason": beat.get("editorial_reason") or "strong narrated fallback signal",
            "source": f"{source_provider}+deterministic",
        })
    if fallback["treatment"] == "date" and len(fallback["emphasis"].split()) >= 2 and beat["treatment"] != "date":
        beat.update({
            "emphasis": fallback["emphasis"], "treatment": "date",
            "animation": fallback["animation"], "background": "none",
            "highlight_kind": "milestone", "importance": max(float(beat.get("importance") or 0.0), 0.74),
            "editorial_reason": "full narrated date preserved for chronology",
            "source": f"{source_provider}+deterministic",
        })
    # Preserve a grounded 1–4 word phrase.  The Whisper cue planner aligns
    # the complete phrase to its per-word timestamps; collapsing it to one
    # token here produced cards such as “LOCATION” or a random verb and made
    # multi-word names lose their identity.
    if beat["emphasis"]:
        beat["emphasis"] = " ".join(str(beat["emphasis"]).split()[:4])[:64].strip()
    return beat


def _chunk_prompt(rows: List[Dict[str, Any]]) -> str:
    recovery_target = 0
    for row in rows:
        try:
            recovery_target = max(recovery_target, int(row.get("_editorial_recovery_target") or 0))
        except (TypeError, ValueError):
            continue
    prompt_rows = [
        {key: value for key, value in row.items() if not str(key).startswith("_editorial_")}
        for row in rows
    ]
    if recovery_target:
        return (
            "This is one focused corrective pass after an earlier editorial plan returned too few usable "
            "visible-text decisions. Re-read all candidate scenes together and nominate the strongest "
            f"{recovery_target} additional scene(s), or fewer only when the narration truly has fewer "
            "editorial facts. Return ONLY nominated visible candidates; do not return ordinary/none rows. "
            "Each emphasis must be exactly ONE meaningful word copied verbatim from that scene and must read as a "
            "complete intentional label. Prefer a named place/person, number, date, consequence, warning, "
            "meaningful human stake, surprising scale, defining fact, or chronology. Never nominate filler, a "
            "generic subject name, a weak verb/adjective, a determiner, or a dangling sentence fragment. Keep "
            "each original scene_index exactly.\n\n"
            "CANDIDATE SCENES:\n" + json.dumps(prompt_rows, ensure_ascii=True)
        )

    # The brain may nominate candidates, but the director owns the final
    # sparse overlay budget. Keeping this first pass near 20–30% prevents a
    # recovery or batching artefact from flooding the edit with cards.
    candidate_budget = max(1, round(len(prompt_rows) * 0.30))
    return (
        "Plan restrained, premium documentary edits for these narration scenes. "
        f"Nominate strong visible-text CANDIDATES for up to {candidate_budget} scenes; a later global editor will keep the best distributed set at roughly 20-30% of scenes. "
        "When the narration contains enough real facts, use the candidate allowance instead of returning only dates or numbers. All ordinary scenes MUST use treatment=none and empty emphasis. "
        "A visible phrase must be a punchy 1-4 word phrase or key term copied verbatim from that scene's text and communicate scale, a decisive turning point, warning, contrast, consequence, milestone, quote, major location, defining fact, item title, human stakes, or conclusion. "
        "Choose a clear, powerful phrase or term (1-4 words), never an awkward grammatical fragment. It must read as a complete intentional label or title card when shown alone; reject standalone weak verbs, determiners, vague adjectives, or connective words. "
        "Prefer what changes the viewer's understanding: surprising numbers, danger, causes, reversals, records, decisive actions, key entities, and consequences. "
        "A location qualifies only when the place is central to the event, journey, conflict, discovery, or change; use treatment=location and highlight_kind=location. "
        "A person or named entity may use treatment=person for an exact grounded name or short complete identity label; never invent a name. "
        "A listicle or section heading MUST use treatment=chapter and highlight_kind=key_fact when it clearly opens a new ranked item, chapter, or narrative container (e.g. 'Trappist-1 System', 'Cosmic Web', 'Black Hole'). "
        "A defining superlative, breakthrough, first/last event, core claim, or identity-changing fact may use highlight_kind=key_fact. "
        "Meaningful human impact such as lives, victims, survivors, families, displacement, or personal stakes may use highlight_kind=human_stakes. "
        "Preserve every distinct date that marks a real event, departure, deadline, discovery, law, death, release, or turning point; return the complete narrated date including its year when present. Ignore incidental dates. "
        "Never highlight filler, connective narration, generic verbs, or a phrase merely because every scene needs a decision. "
        "Set importance from 0 to 1 so only genuinely consequential moments rank highly. "
        "Use no overlay when footage should breathe. Use cards for dates, chapters, keywords, and counters. "
        "Transitions should usually be cut; reserve motion transitions for reveals, energy changes, and chapters. "
        "Return one object per scene in the same order.\n\nSCENES:\n"
        + json.dumps(prompt_rows, ensure_ascii=True)
    )


def _parse_editorial_payload(content: Any) -> Dict[str, Any]:
    """Decode OpenRouter JSON even when a model wraps it in markdown prose.

    ``json_mode`` is a request hint, not a guarantee across free routed
    models.  Some otherwise valid responses arrive as a fenced object or
    with one sentence before/after the JSON.  Extracting the first complete
    object keeps the editorial lane fail-soft without accepting arbitrary
    non-object payloads.
    """
    raw = str(content or "").strip()
    if not raw:
        return {}
    candidates = [raw]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", raw, re.I | re.S)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list):
            return {"beats": payload}
    # Fall back to a raw_decode scan so braces inside a short preamble do not
    # poison the entire chunk.  The first object is the schema payload; later
    # objects may be examples included by an over-helpful model.
    for match in re.finditer(r"[\[{]", raw):
        try:
            payload, _end = decoder.raw_decode(raw[match.start():])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list):
            return {"beats": payload}
    raise ValueError("OpenRouter editorial response did not contain a JSON object")


_EDITORIAL_BEAT_REQUIRED_FIELDS = (
    "scene_index", "role", "emphasis", "treatment", "animation", "background",
    "transition", "energy", "confidence", "importance", "highlight_kind",
    "editorial_reason", "sound_intent", "sound_intensity", "sound_reason", "sound_anchor",
)


def _validate_editorial_chunk(payload: Any, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Reject schema-shaped but incomplete OpenRouter responses before fallback."""
    if not isinstance(payload, Mapping):
        raise ValueError("editorial response must be a JSON object")
    beats = payload.get("beats")
    if not isinstance(beats, list):
        raise ValueError("editorial response is missing the required beats array")
    expected = [int(row["scene_index"]) for row in rows]
    if len(beats) != len(expected):
        raise ValueError(f"editorial response must contain exactly {len(expected)} scene beats")

    by_index: Dict[int, Dict[str, Any]] = {}
    string_fields = set(_EDITORIAL_BEAT_REQUIRED_FIELDS) - {
        "scene_index", "confidence", "importance", "sound_intensity",
    }
    for beat in beats:
        if not isinstance(beat, Mapping):
            raise ValueError("editorial beat must be a JSON object")
        missing = [field for field in _EDITORIAL_BEAT_REQUIRED_FIELDS if field not in beat]
        if missing:
            raise ValueError("editorial beat is missing required fields: " + ", ".join(missing))
        raw_index = beat.get("scene_index")
        if isinstance(raw_index, bool) or not isinstance(raw_index, int):
            raise ValueError("editorial scene_index must be an integer")
        scene_index = raw_index
        if scene_index in by_index:
            raise ValueError(f"editorial response duplicated scene_index {scene_index}")
        if scene_index not in expected:
            raise ValueError(f"editorial response returned unexpected scene_index {scene_index}")
        if any(not isinstance(beat.get(field), str) for field in string_fields):
            raise ValueError(f"editorial beat {scene_index} has a non-string text field")
        for field in ("confidence", "importance", "sound_intensity"):
            value = beat.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"editorial beat {scene_index} has an invalid {field}")
        by_index[scene_index] = dict(beat)

    if set(by_index) != set(expected):
        missing_indices = sorted(set(expected) - set(by_index))
        raise ValueError(f"editorial response omitted scene indices: {missing_indices}")
    return [by_index[index] for index in expected]


def _plan_chunk(
    api_key: str,
    model: str,
    rows: List[Dict[str, Any]],
    timeout: float,
    fallback_models: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    try:
        from .openrouter_llm import OpenRouterLLM
    except ImportError:
        from services.openrouter_llm import OpenRouterLLM

    schema = {
        "type": "object",
        "required": ["beats"],
        "properties": {
            "beats": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["scene_index", "role", "emphasis", "treatment", "animation", "background", "transition", "energy", "grade", "texture", "confidence", "importance", "highlight_kind", "editorial_reason", "sound_intent", "sound_intensity", "sound_reason", "sound_anchor"],
                    "properties": {
                        "scene_index": {"type": "integer"},
                        "role": {"type": "string"},
                        "emphasis": {"type": "string"},
                        "treatment": {"type": "string"},
                        "animation": {"type": "string"},
                        "background": {"type": "string"},
                        "transition": {"type": "string"},
                        "energy": {"type": "string"},
                        "grade": {"type": "string"},
                        "texture": {"type": "string"},
                        "confidence": {"type": "number"},
                        "importance": {"type": "number"},
                        "highlight_kind": {"type": "string"},
                        "editorial_reason": {"type": "string"},
                        "sound_intent": {"type": "string"},
                        "sound_intensity": {"type": "number"},
                        "sound_reason": {"type": "string"},
                        "sound_anchor": {"type": "string"},
                    },
                },
            }
        },
        "additionalProperties": False,
    }
    model_chain = list(dict.fromkeys([str(model or "").strip(), *[str(item).strip() for item in (fallback_models or ())]]))
    model_chain = [item for item in model_chain if item]
    if not model_chain:
        raise ValueError("editorial model route is empty")
    client = OpenRouterLLM(
        api_key=api_key,
        model=model_chain[0],
        fallback_models=model_chain[1:],
    )
    system_prompt = (
            "You are Vidzeo's senior documentary editor. Output grounded edit decisions, not generic decoration. "
            "Think in storyboard beats: media-only hold, map, portrait, evidence, diagram, data, quote, or one rare text accent. "
            "Most scenes must be treatment=none with empty emphasis so the image or clip can breathe. Never turn every scene into a heading. "
            "Allowed treatments: none, keyword, title, lower_third, date, location, person, listicle, counter, quote, chapter, cta. "
            "A person treatment is only an exact grounded name or short identity label; never invent one. "
            "Allowed animations: none, fade, quick_zoom_in, quick_zoom_out, scale_pop, slide_left, slide_right, rise, wipe, type_on, blur_in. "
            "Allowed backgrounds: none, underline, marker, edge, solid, outline."
            " Allowed highlight_kind: none, milestone, scale, turning_point, warning, contrast, consequence, quote, conclusion, location, key_fact, human_stakes."
            " Visible text is exceptional, not a scene label: normally select no more than 20-30% of scenes, distributed across the film. "
            "An emphasis must be a complete 1-3 word label, exact in the narration, and must never be a dangling fragment such as 'closing massive' or 'Every transit'. Reject standalone weak verbs, determiners, vague adjectives, and connective words. "
            "If no phrase changes the viewer's understanding, return treatment=none and empty emphasis."
            " Sound is semantic and rare: across a 60-second film use at most three sound cues, and allow many scenes to stay silent. Never add repeated room tone or a cue just because a scene starts."
            " For every non-none sound_intent, sound_anchor must be an exact contiguous phrase copied from that scene's narration, chosen at the event itself. If no precise spoken anchor exists, use sound_intent=none and sound_anchor empty."
            " Sound_reason describes only the designed sound effect; never claim a generated cue is real archival audio, radio, alarm, or an authentic historical recording. The original voiceover remains dominant."
            " Allowed sound_intent: none, quiet_texture, transition_air, paper_action, typing, data_tick, location_ping, signal, tension, reveal, impact, human_stakes, archive, technology."
    )
    try:
        configured_total = float(os.environ.get("VIDZEO_EDITORIAL_BRAIN_TOTAL_TIMEOUT_SECONDS", "600") or 600)
    except (TypeError, ValueError):
        configured_total = 600.0
    total_timeout = max(180.0, min(900.0, configured_total))
    deadline = time.monotonic() + total_timeout
    request_prompt = _chunk_prompt(rows)
    try:
        request_attempts = max(2, min(4, int(os.environ.get("VIDZEO_EDITORIAL_BRAIN_MAX_ATTEMPTS", "2") or 2)))
    except (TypeError, ValueError):
        request_attempts = 2
    last_validation_error = None

    for schema_retry in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 1.0:
            raise TimeoutError("editorial schema retry deadline exhausted") from last_validation_error
        content = client.chat(
            request_prompt,
            system_prompt=system_prompt,
            temperature=0.25,
            max_tokens=max(2200, len(rows) * 180),
            json_mode=True,
            json_schema=schema,
            max_attempts=request_attempts if schema_retry == 0 else 1,
            timeout_seconds=min(float(timeout), remaining),
            total_timeout_seconds=remaining,
            json_schema_name="vidzeo_editorial_beats_v1",
        )
        try:
            payload = _parse_editorial_payload(content)
            return _validate_editorial_chunk(payload, rows)
        except (TypeError, ValueError) as exc:
            last_validation_error = exc
            if schema_retry:
                raise ValueError(f"OpenRouter editorial response failed schema validation after repair: {exc}") from exc
            current_model = str(getattr(client, "last_model", "") or model_chain[0]).casefold()
            used_index = next((index for index, item in enumerate(model_chain) if item.casefold() == current_model), 0)
            retry_index = min(used_index + 1, len(model_chain) - 1)
            client = OpenRouterLLM(
                api_key=api_key,
                model=model_chain[retry_index],
                fallback_models=model_chain[retry_index + 1:],
            )
            indices = [int(row["scene_index"]) for row in rows]
            request_prompt = (
                _chunk_prompt(rows)
                + "\n\nFORMAT REPAIR REQUIRED: The previous routed response failed local validation ("
                + str(exc)[:180]
                + "). Return a complete beats array with exactly one object for every scene_index in "
                + json.dumps(indices)
                + ". Include every required schema field, preserve these indices, and output JSON only."
            )
    raise ValueError("OpenRouter editorial response was invalid")


def _select_sparse_beats(
    beats: List[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Choose a small, semantically varied set of on-screen editorial moments."""
    normalized = beats
    total = len(normalized)
    config = dict(config or {})
    density = str(config.get("documentary_density") or config.get("density") or "balanced").strip().lower()
    # Visible editorial cues are accents, not per-scene captions. The Remotion
    # director owns the larger frame grammar; this brain only nominates a few
    # grounded moments for type, markers, or sound.
    ratio = {"subtle": 0.20, "balanced": 0.28, "dynamic": 0.32}.get(density, 0.28)
    minimum = 1 if total < 8 else 2 if density == "subtle" else 3
    budget = max(minimum, min(total, round(total * ratio))) if total else 0
    visible = [
        index for index, beat in enumerate(normalized)
        if beat.get("treatment") not in {"none", "chapter"}
        and beat.get("highlight_kind") != "none"
        and float(beat.get("importance") or 0.0) >= 0.56
        and bool(beat.get("emphasis"))
    ]

    def source_weight(index: int) -> float:
        source = str(normalized[index].get("source") or "").strip().lower()
        if source == "openrouter":
            return 0.14
        if source.startswith("openrouter"):
            return 0.08
        return 0.0

    role_weight = {
        "cta": 0.12, "reveal": 0.16, "statistic": 0.15,
        "evidence": 0.08, "conclusion": 0.10,
    }
    kind_weight = {
        "turning_point": 0.18, "warning": 0.17, "consequence": 0.16,
        "contrast": 0.14, "scale": 0.13, "key_fact": 0.13,
        "conclusion": 0.12, "human_stakes": 0.11, "quote": 0.10,
        "location": 0.07, "milestone": 0.0,
    }
    ranked = sorted(
        visible,
        key=lambda index: (
            float(normalized[index].get("importance") or 0.0)
            + role_weight.get(str(normalized[index].get("role") or ""), 0.0)
            + kind_weight.get(str(normalized[index].get("highlight_kind") or ""), 0.0)
            + source_weight(index),
            float(normalized[index].get("confidence") or 0.0),
            -index,
        ),
        reverse=True,
    )
    starts_raw = config.get("_editorial_scene_starts")
    starts = [float(value) for value in starts_raw] if isinstance(starts_raw, (list, tuple)) and len(starts_raw) == total else [float(index) for index in range(total)]
    axis_end = float(config.get("_editorial_total_duration") or (starts[-1] + 1.0 if starts else 1.0))
    axis_end = max(0.1, axis_end)
    keep_order: List[int] = []
    kind_counts: Dict[str, int] = {}
    used_phrases = set()
    date_limit = max(3, min(8, int(math.ceil(axis_end / 35.0))))
    kind_limits = {
        "milestone": date_limit,
        "location": 2,
        "scale": 3,
        # Empty-brain recovery can produce several distinct, grounded facts;
        # keep them distributed by time band instead of capping the whole edit
        # to three words for a long documentary.
        "key_fact": max(3, int(math.ceil(budget * 0.5))),
        "human_stakes": 2,
    }

    def phrase_key(index: int) -> str:
        phrase = " ".join(re.findall(r"[a-z0-9]+", str(normalized[index].get("emphasis") or "").lower()))
        numeric = re.search(r"\d+(?:[.,]\d+)?", phrase)
        if numeric:
            # “27” and “27 percent” are the same fact; do not spend two card
            # slots repeating one statistic across the documentary.
            return f"number:{numeric.group(0).replace(',', '')}"
        return phrase

    def score(index: int) -> float:
        beat = normalized[index]
        kind = str(beat.get("highlight_kind") or "none")
        novelty = 0.10 if kind_counts.get(kind, 0) == 0 else 0.0
        return (
            float(beat.get("importance") or 0.0)
            + role_weight.get(str(beat.get("role") or ""), 0.0)
            + kind_weight.get(kind, 0.0)
            + novelty
            + source_weight(index)
        )

    def allowed(index: int) -> bool:
        kind = str(normalized[index].get("highlight_kind") or "none")
        key = phrase_key(index)
        limit = kind_limits.get(kind, 2)
        return bool(key) and key not in used_phrases and kind_counts.get(kind, 0) < limit

    def keep(index: int) -> None:
        keep_order.append(index)
        kind = str(normalized[index].get("highlight_kind") or "none")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        used_phrases.add(phrase_key(index))

    # Dates carry chronology. Reserve their places before time-band ranking so
    # a later high-energy phrase cannot silently evict a narrated milestone.
    date_candidates = [
        index for index in visible
        if normalized[index].get("highlight_kind") == "milestone"
        and normalized[index].get("treatment") == "date"
    ]
    unique_dates = {phrase_key(index) for index in date_candidates if phrase_key(index)}
    non_date_reserve = max(2, round(total * 0.12))
    budget = max(budget, min(total, min(date_limit, len(unique_dates)) + non_date_reserve))
    for index in sorted(date_candidates, key=lambda candidate: (starts[candidate], -score(candidate))):
        if len(keep_order) >= budget or kind_counts.get("milestone", 0) >= date_limit:
            break
        if allowed(index):
            keep(index)

    # Pick the strongest distinct moment in each time band first. This keeps
    # excellent cues from clustering while the rest of the video stays empty.
    for slot in range(budget):
        if len(keep_order) >= budget:
            break
        lower = axis_end * slot / max(1, budget)
        upper = axis_end * (slot + 1) / max(1, budget)
        band = [
            index for index in visible
            if index not in keep_order
            and lower <= starts[index] < (upper if slot < budget - 1 else axis_end + 0.001)
            and allowed(index)
        ]
        if band:
            keep(max(band, key=lambda index: (score(index), -abs(starts[index] - ((lower + upper) / 2.0)), -index)))

    for index in ranked:
        if len(keep_order) >= budget:
            break
        if index not in keep_order and allowed(index):
            keep(index)

    keep = set(keep_order)
    for index, beat in enumerate(normalized):
        if index in keep:
            beat["selected_for_text"] = True
            continue
        beat.update({
            "emphasis": "", "treatment": "none", "animation": "none", "background": "none",
            "selected_for_text": False,
        })
    return normalized


def remap_beats_to_primary_scenes(
    beats: Iterable[Dict[str, Any]],
    primary_texts: Iterable[str],
) -> tuple[List[Dict[str, Any]], int]:
    """Move a look-ahead cue to the scene that actually contains its phrase."""
    mapped = [dict(beat or {}) for beat in beats or []]
    texts = [str(text or "") for text in primary_texts or []]

    def tokens(value: str) -> List[str]:
        return [token.lower() for token in re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", value)]

    def contains(text: str, phrase: str) -> bool:
        haystack, needle = tokens(text), tokens(phrase)
        return bool(needle) and any(
            haystack[index:index + len(needle)] == needle
            for index in range(max(0, len(haystack) - len(needle) + 1))
        )

    moved = 0
    for source_index in range(min(len(mapped), len(texts))):
        beat = mapped[source_index]
        phrase = str(beat.get("emphasis") or "").strip()
        if beat.get("selected_for_text") is not True or not phrase or contains(texts[source_index], phrase):
            continue
        matches = [index for index, text in enumerate(texts) if contains(text, phrase)]
        if not matches:
            continue  # A valid phrase may deliberately span the current and next scene.
        target_index = min(matches, key=lambda index: (abs(index - source_index), index))
        if target_index == source_index or mapped[target_index].get("selected_for_text") is True:
            continue
        mapped[target_index] = {**beat, "scene_index": target_index}
        mapped[source_index] = {
            **beat,
            "scene_index": source_index,
            "emphasis": "",
            "treatment": "none",
            "animation": "none",
            "background": "none",
            "selected_for_text": False,
        }
        moved += 1
    return mapped, moved


def plan_editorial_beats(
    scene_texts: Iterable[str],
    config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    texts = [str(text or "") for text in scene_texts or []]
    total = len(texts)
    fallback = [deterministic_editorial_beat(text, index, total) for index, text in enumerate(texts)]
    config = dict(config or {})
    # The distributable is OpenRouter-only.  Keep accepting legacy provider
    # fields in saved projects, but never read or forward a legacy provider
    # key; otherwise an old config could make the packaged runtime call a
    # provider whose client is intentionally excluded from the installer.
    source_provider = "openrouter"
    api_key = str(
        config.get("openrouter_api_key")
        or os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("VIDZEO_OPENROUTER_API_KEY")
    ).strip()
    if not texts:
        return []
    if not api_key:
        return _select_sparse_beats(fallback, config)
    model = str(
        config.get("scene_brain_model")
        or config.get("llm_model")
        or os.environ.get("VIDZEO_SCENE_BRAIN_MODEL")
        or "qwen/qwen3.5-flash-02-23"
    ).strip()
    fallback_models = config.get("scene_brain_fallback_chain")
    if fallback_models is None:
        fallback_models = config.get("scene_brain_fallback_models")
    if fallback_models is None:
        fallback_models = os.environ.get("VIDZEO_SCENE_BRAIN_FALLBACK_CHAIN")
    if isinstance(fallback_models, str):
        fallback_models = [item.strip() for item in fallback_models.replace(";", ",").split(",") if item.strip()]
    fallback_models = list(fallback_models or ())

    def _compat_plan_chunk(rows, timeout):
        """Call patched/legacy chunk workers while passing the model chain."""
        try:
            return _plan_chunk(api_key, model, rows, timeout, fallback_models)
        except TypeError as exc:
            # A few integrations monkeypatch the historical four-argument
            # worker. Keep that contract compatible without dropping the
            # fallback chain in the normal implementation.
            if "positional" not in str(exc).lower() and "argument" not in str(exc).lower():
                raise
            return _plan_chunk(api_key, model, rows, timeout)

    timeout = max(120.0, min(360.0, float(config.get("editorial_brain_timeout") or 240.0)))
    chunk_size = 24
    chunks = []
    narrative_context = re.sub(r"\s+", " ", " ".join(texts)).strip()[:5000]
    for start in range(0, total, chunk_size):
        rows = [{"scene_index": i, "text": texts[i][:420]} for i in range(start, min(total, start + chunk_size))]
        if rows:
            rows[0]["narrative_context"] = narrative_context
        chunks.append((start, rows))

    raw_by_index: Dict[int, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(5, len(chunks)), thread_name_prefix="edit-brain") as executor:
        futures = {
            executor.submit(_compat_plan_chunk, rows, timeout): start
            for start, rows in chunks
        }
        for future in as_completed(futures):
            start = futures[future]
            try:
                for local_index, raw in enumerate(future.result()):
                    # OpenRouter occasionally returns a malformed array/object
                    # for one beat.  Do not let that row poison the whole
                    # editorial pass (or surface as a late ``list.get``
                    # failure in the renderer); keep the deterministic beat
                    # for that scene instead.
                    if not isinstance(raw, dict):
                        continue
                    requested = int(raw.get("scene_index", start + local_index))
                    index = requested if start <= requested < min(total, start + chunk_size) else start + local_index
                    if 0 <= index < total:
                        raw_by_index[index] = raw
            except Exception as exc:
                print(f"   [edit] OpenRouter editorial chunk {start + 1}-{min(total, start + chunk_size)} fallback: {exc}")

    normalized = [
        _normalize_beat(raw_by_index.get(index, {}), text, index, total, source_provider=source_provider)
        if index in raw_by_index else fallback[index]
        for index, text in enumerate(texts)
    ]
    def _is_llm_beat(beat: Dict[str, Any]) -> bool:
        source = str(beat.get("source") or "").strip().lower()
        return source == source_provider or source.startswith(source_provider + "+")

    def is_visible(beat: Dict[str, Any]) -> bool:
        return bool(
            beat.get("treatment") not in {"none", "chapter"}
            and beat.get("highlight_kind") != "none"
            and beat.get("emphasis")
        )

    # The LLM remains the decision maker when its first response is valid JSON
    # but editorially under-filled. Give OpenRouter one small, focused correction
    # request before invoking any local grounded safety net. This is a logical
    # quality retry (not an unbounded transport retry) and only includes scenes
    # that do not already have a usable LLM-authored decision.
    recovery_threshold = min(total, max(2, int(math.ceil(total * 0.34))))
    llm_visible_before = sum(
        1 for beat in normalized
        if is_visible(beat) and _is_llm_beat(beat)
    )
    openrouter_repair_count = 0
    if llm_visible_before < recovery_threshold:
        repair_indices = [
            index for index, beat in enumerate(normalized)
            if not (
                is_visible(beat)
                and _is_llm_beat(beat)
            )
        ]
        repair_rows = [
            {"scene_index": index, "text": texts[index][:420]}
            for index in repair_indices
        ]
        if repair_rows:
            repair_rows[0]["narrative_context"] = narrative_context
            repair_rows[0]["_editorial_recovery_target"] = min(
                len(repair_rows), recovery_threshold - llm_visible_before
            )
            repair_timeout = max(
                12.0,
                min(
                    timeout,
                    float(
                        config.get("editorial_brain_repair_timeout")
                        or os.environ.get("VIDZEO_EDITORIAL_BRAIN_REPAIR_TIMEOUT_SECONDS")
                        or 75.0
                    ),
                ),
            )
            try:
                repair_raw = _compat_plan_chunk(repair_rows, repair_timeout)
                allowed_indices = set(repair_indices)
                for local_index, raw in enumerate(repair_raw or []):
                    if not isinstance(raw, dict):
                        continue
                    try:
                        requested = int(raw.get("scene_index"))
                    except (TypeError, ValueError):
                        requested = repair_indices[local_index] if local_index < len(repair_indices) else -1
                    if requested not in allowed_indices:
                        continue
                    candidate = _normalize_beat(raw, texts[requested], requested, total, source_provider=source_provider)
                    if is_visible(candidate) and _is_llm_beat(candidate):
                        normalized[requested] = candidate
                        openrouter_repair_count += 1
                print(
                    f"   [edit] OpenRouter editorial first pass {llm_visible_before}/{total}; " 
                    f"bounded focused repair added {openrouter_repair_count} candidate(s) "
                    f"(target={recovery_threshold})"
                )
            except Exception as exc:
                print(f"   [edit] bounded OpenRouter editorial repair unavailable: {exc}")

    visible_before_recovery = sum(1 for beat in normalized if is_visible(beat))
    recovery_count = 0
    # Only after the bounded OpenRouter correction has failed to provide enough real
    # candidates do we recover exact narration phrases locally. The global
    # selector gives LLM-authored moments a ranking advantage, so this fallback
    # fills gaps instead of replacing sound OpenRouter decisions.
    if visible_before_recovery < recovery_threshold:
        recovered = []
        for index, text in enumerate(texts):
            existing = normalized[index]
            existing_visible = is_visible(existing)
            beat = existing if existing_visible else _semantic_fallback_beat(text, index, total)
            if beat.get("treatment") != "none" and beat.get("emphasis"):
                if not existing_visible:
                    recovery_count += 1
            recovered.append(beat)
        normalized = recovered
        if recovery_count:
            print(
                f"   [edit] post-retry editorial coverage was {visible_before_recovery}/{total}; grounded recovery "
                f"generated {recovery_count}/{total} grounded candidates"
            )
    selected = _select_sparse_beats(normalized, config)
    if recovery_count:
        fallback_selected = sum(
            1 for beat in selected
            if beat.get("selected_for_text") is True
            and not str(beat.get("source") or "").strip().lower().startswith("openrouter")
        )
        print(f"   [edit] grounded recovery supplied {fallback_selected} selected sparse cue(s)")
    return selected
