"""Grounded, editorially useful words for documentary graphics.

The director may suggest an emphasis phrase, but the renderer must never turn
an entire narration sentence (or a connective word) into a graphic.  This
module is intentionally dependency-free so both the semantic planner and the
timed cue planner use exactly the same selection rules.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


TOKEN_RE = re.compile(r"[$£€]?[A-Za-z0-9][A-Za-z0-9'%/-]*")

STOPWORDS = {
    "a", "about", "above", "across", "after", "against", "all", "also", "an", "and",
    "are", "around", "as", "at", "be", "because", "been", "before", "being", "between",
    "but", "by", "can", "could", "did", "do", "does", "for", "from", "had", "has", "have",
    "he", "her", "here", "hers", "him", "his", "how", "i", "if", "in", "into", "is", "it",
    "its", "just", "like", "may", "me", "more", "most", "much", "my", "near", "no", "not",
    "of", "off", "on", "once", "one", "only", "or", "other", "our", "out", "over", "she",
    "should", "so", "some", "that", "the", "their", "them", "then", "there", "these", "they",
    "this", "those", "through", "to", "too", "under", "until", "up", "us", "was", "we", "were",
    "what", "when", "where", "which", "while", "who", "will", "with", "would", "you", "your",
}

# Negation is grammatical glue only when isolated.  Inside an explicitly
# approved phrase it changes the factual meaning ("does not exist", "never
# sleeps", "without warning") and must survive both server and renderer highlight selection.
NEGATION_WORDS = {"no", "not", "never", "without", "nowhere", "hardly", "barely"}

WEAK_WORDS = {
    "also", "back", "began", "became", "been", "being", "built", "called", "came", "created",
    "designed", "does", "doing", "feels", "gives", "going", "has", "have", "keeps", "made", "become", "becomes",
    "make", "makes", "move", "moves", "moving", "pushes", "returns", "seems", "shows", "sits", "starts",
    "stays", "takes", "turns", "used", "uses", "creates", "watching", "where", "works",
}

GENERIC_WORDS = {
    "background", "chapter", "city", "detail", "documentary", "game", "games", "idea", "line",
    "moment", "new", "next", "people", "place", "pressure", "record", "scene", "series", "story",
    "thing", "things", "time", "video", "world", "year", "years",
}

POWER_WORDS = {
    "benchmark", "breakthrough", "chase", "collapse", "conflict", "consequence", "danger",
    "evidence", "escape", "explosion", "flooded", "impact", "instability", "observed", "pressure",
    "revealed", "risk", "route", "strategy", "trust", "warning", "unstable", "violence",
    "unprecedented", "blockbuster", "record-breaking", "densest",
}

CONTEXT_WORDS = {
    "coast", "contrast", "crowds", "density", "economy", "environment", "highway", "industrial",
    "map", "media", "nightlife", "open-world", "real-time", "system", "systems", "traffic",
    "vehicle", "wetlands", "trailer", "budget", "launch", "analysts",
}

UNITS_WORDS = {
    "million", "billion", "trillion", "thousand", "percent", "%", "hours", "days", "months", "years",
    "dollars", "cents", "miles", "km", "meters", "feet",
}


def _tokens(value: str) -> List[str]:
    return TOKEN_RE.findall(str(value or ""))


def _normalized(value: str) -> List[str]:
    return [token.lower().strip(".,!?;:\"'()[]{}") for token in _tokens(value)]


def _meaningful(token: str) -> bool:
    lowered = token.lower().strip(".,!?;:\"'()[]{}")
    return bool(lowered) and len(lowered) >= 3 and lowered not in STOPWORDS and lowered not in WEAK_WORDS and lowered not in GENERIC_WORDS


def clean_highlight_tokens(value: str) -> List[str]:
    """Return salient tokens only; stopwords never become highlighted words,

    except when negation words modify meaningful tokens.
    """
    result: List[str] = []
    seen = set()
    source = _tokens(value)
    phrase_has_meaning = any(_meaningful(token) for token in source)
    for token in source:
        normalized = token.lower().strip(".,!?;:\"'()[]{}")
        preserve_negation = normalized in NEGATION_WORDS and phrase_has_meaning
        if (not _meaningful(token) and not preserve_negation) or normalized in seen:
            continue
        seen.add(normalized)
        result.append(token)
    return result


def _explicit_phrase(source: Sequence[str], emphasis: str, max_words: int) -> str:
    requested = _tokens(emphasis)
    if not requested:
        return ""
    source_lower = [token.lower() for token in source]
    requested_lower = [token.lower() for token in requested]
    # First honor an exact contiguous LLM/editor suggestion.
    for size in range(min(max_words, len(requested_lower)), 0, -1):
        needle = requested_lower[:size]
        for start in range(0, len(source_lower) - size + 1):
            if source_lower[start:start + size] != needle:
                continue
            span = source[start:start + size]
            salient = clean_highlight_tokens(" ".join(span))
            if salient and any(_meaningful(token) or token.lower() in NEGATION_WORDS for token in span):
                return " ".join(span)
    return ""


def _score_phrase(tokens: Sequence[str], *, explicit: bool = False) -> float:
    if not tokens or not any(_meaningful(token) or token.lower() in NEGATION_WORDS for token in tokens):
        return -1e9
    normalized = [token.lower() for token in tokens]
    score = 100.0 if explicit else 0.0
    score += sum(5.0 for token in normalized if token in POWER_WORDS)
    score += sum(4.0 for token in normalized if token in CONTEXT_WORDS)
    score += sum(2.0 for token in normalized if len(token) >= 7)
    score += sum(1.5 for token in tokens if token[:1].isupper())
    
    # Preserving negation boosts factual value
    if any(token in NEGATION_WORDS for token in normalized):
        score += 15.0

    if len(tokens) == 2 and all(_meaningful(token) or token.lower() in NEGATION_WORDS for token in tokens):
        score += 8.0
    if len(tokens) > 2:
        score -= (len(tokens) - 2) * 3.0
    if normalized and normalized[0] in GENERIC_WORDS and len(tokens) == 1:
        score -= 9.0
    if any(token in WEAK_WORDS for token in normalized):
        score -= 15.0
    return score


def select_grounded_highlight(text: str, emphasis: str = "", max_words: int = 3) -> str:
    """Choose a short, source-grounded phrase for the visual hierarchy.

    Priority is:
    1. Grounded director/LLM suggestion matching the script.
    2. Compound proper nouns ("Grand Theft Auto VI", "Rockstar Games", "Vice City").
    3. Negation phrases ("never sleeps", "without warning", "not exist").
    4. Quantitative figures and dates ("$2 billion", "24 hours", "10 years").
    5. Salient multi-word concepts ("unprecedented budget", "definitive blockbuster").
    6. Strongest single documentary noun.
    """
    source = _tokens(text)
    if not source:
        return ""
    explicit = _explicit_phrase(source, emphasis, max_words)
    if explicit:
        return explicit

    candidates: List[tuple[float, str]] = []

    # 1. Compound proper-name runs (e.g. Grand Theft Auto VI, Rockstar Games, Vice City, Stephen Elop)
    months = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"}
    for start in range(len(source)):
        token0 = source[start]
        if token0[:1].isupper() and token0.lower() not in STOPWORDS:
            is_month = token0.lower() in months
            run = [token0]
            for k in range(start + 1, min(len(source), start + max_words)):
                nxt = source[k]
                if is_month:
                    if nxt.isdigit():
                        run.append(nxt)
                    break
                if nxt.isdigit():
                    run.append(nxt)
                    break
                if nxt[:1].isupper() or re.match(r"^(?:VI|V|IV|III|II|I|X)$", nxt, re.I) or nxt.lower() in ("of", "the", "&"):
                    run.append(nxt)
                else:
                    break
            while run and run[-1].lower() in ("of", "the", "&"):
                run.pop()
            if len(run) >= 2 and any(_meaningful(t) for t in run):
                score = (95.0 + len(run)) if is_month else (120.0 + len(run) * 4.0)
                phrase_str = " ".join(run)
                candidates.append((score, phrase_str))

    # 2. Negation phrases (e.g. "never sleeps", "without warning", "not exist")
    for index in range(len(source) - 1):
        w1 = source[index].lower()
        if w1 in NEGATION_WORDS:
            w2 = source[index + 1]
            if _meaningful(w2) or w2.lower() not in STOPWORDS:
                phrase_str = f"{source[index]} {w2}"
                candidates.append((115.0, phrase_str))

    # 3. Numbers and units (e.g. "$2 billion", "24 hours", "10 years")
    for index, token in enumerate(source):
        normalized = token.lower().strip(".,!?;:\"'()[]{}")
        if re.search(r"\d|%", token):
            if index + 1 < len(source) and source[index + 1].lower() in UNITS_WORDS:
                candidates.append((110.0, f"{token} {source[index + 1]}"))
            else:
                candidates.append((96.0 + len(token) / 100.0, token))

    # 4. Multi-word concepts (2-3 words)
    for index in range(max(0, len(source) - 1)):
        phrase = source[index:index + 2]
        if all(_meaningful(token) or token.lower() in NEGATION_WORDS for token in phrase):
            candidates.append((_score_phrase(phrase) + 12.0, " ".join(phrase)))
    for index in range(max(0, len(source) - 2)):
        phrase = source[index:index + 3]
        if all(_meaningful(token) or token.lower() in NEGATION_WORDS for token in phrase):
            candidates.append((_score_phrase(phrase), " ".join(phrase)))

    # 5. Single strong tokens
    for index, token in enumerate(source):
        if token[:1].isupper() and (index > 0 or len(source) == 1) and _meaningful(token):
            candidates.append((82.0 + len(token) / 100.0, token))
        if _meaningful(token):
            candidates.append((_score_phrase([token]), token))

    if not candidates:
        return ""
    candidates.sort(key=lambda item: (-item[0], len(item[1]), item[1].lower()))
    return candidates[0][1]


def highlight_tokens_for(text: str, emphasis: str = "", max_tokens: int = 3) -> List[str]:
    """Return the exact words to accent inside a displayed phrase."""
    phrase = select_grounded_highlight(text, emphasis, max_words=max_tokens)
    tokens = clean_highlight_tokens(phrase)
    if tokens:
        return tokens[:max_tokens]
    return clean_highlight_tokens(text)[:max_tokens]


def align_highlight_with_whisper(
    phrase: str,
    word_timings: Sequence[Dict[str, Any]],
    scene_start: float = 0.0,
) -> Tuple[float, float]:
    """Align a highlight phrase with Whisper word-level timestamps.

    Returns (start_seconds, end_seconds) relative to the audio track.
    If not aligned or empty, returns (scene_start, scene_start + 2.5).
    """
    phrase_tokens = _normalized(phrase)
    if not phrase_tokens or not word_timings:
        return (scene_start, scene_start + 2.5)

    n = len(phrase_tokens)
    normalized_words = [
        re.sub(r"[^\w]", "", str(w.get("word") or "").lower())
        for w in word_timings
    ]

    for i in range(len(normalized_words) - n + 1):
        if normalized_words[i:i + n] == phrase_tokens:
            w_start = float(word_timings[i].get("start", scene_start))
            w_end = float(word_timings[i + n - 1].get("end", w_start + 1.5))
            return (w_start, max(w_start + 0.8, w_end))

    # Single-token fallback if multi-word phrase didn't match perfectly
    for pt in phrase_tokens:
        for w in word_timings:
            clean_w = re.sub(r"[^\w]", "", str(w.get("word") or "").lower())
            if clean_w == pt:
                w_start = float(w.get("start", scene_start))
                w_end = float(w.get("end", w_start + 1.5))
                return (w_start, max(w_start + 0.8, w_end))

    return (scene_start, scene_start + 2.5)


__all__ = [
    "STOPWORDS",
    "NEGATION_WORDS",
    "clean_highlight_tokens",
    "highlight_tokens_for",
    "select_grounded_highlight",
    "align_highlight_with_whisper",
]
