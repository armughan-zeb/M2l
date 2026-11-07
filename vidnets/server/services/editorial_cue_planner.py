"""Strict, source-grounded EditorialCueV2 planning.

This module is deliberately deterministic.  Models may rank an already
grounded phrase upstream, but no text, number, date, item identity, or timing
is authored here.  A cue either maps to an exact source span and aligned Whisper
word indexes, or it is suppressed.  There is no character-position timing
fallback for speech-linked cards.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from difflib import SequenceMatcher
import math
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .storyboard_v2 import EditorialCueV2, stable_id
from .storyboard_v2.text import canonical_token, tokenize_source
from .text_position_contract import ALLOWED_TEXT_POSITIONS
from .documentary_template_system import art_direction_pack, resolve_template_design
from .highlight_selector import select_grounded_highlight


_ITEM_KINDS = {
    "list_item", "listicle_item", "item", "step", "chapter", "timeline_event",
}
_STRUCTURAL_KINDS = {"item_heading", "chapter_heading"}
_NEGATIVE_TREATMENTS = {"none", "breathing", "transition", "visual_only"}
_GENERIC_HIGHLIGHTS = {
    "key", "point", "thing", "things", "reason", "reasons", "fact", "facts",
    "important", "story", "moment", "today", "actually", "basically", "really",
    "he", "she", "his", "her", "their", "them", "they", "it", "its", "this", "that",
    "these", "those", "before", "after", "then", "now", "here", "there", "meanwhile",
}
_PHRASE_STOPWORDS = _GENERIC_HIGHLIGHTS | {
    "a", "an", "as", "at", "be", "but", "by", "can", "could", "did", "do",
    "does", "for", "from", "have", "how", "if", "in", "into", "is", "of", "on",
    "or", "so", "than", "to", "was", "were", "what", "when", "where", "which",
    "who", "why", "will", "with", "would", "you", "your",
}
_PHRASE_SIGNAL_WORDS = {
    "breakthrough", "collision", "crisis", "danger", "deadliest", "disaster", "discovered",
    "emergency", "explosion", "flooded", "impact", "launched", "missing", "record", "rescue",
    "revealed", "revolution", "sank", "sinking", "survived", "turning", "victims", "warning",
    "wreck", "years", "million", "billion", "percent", "first", "last", "only",
}
_NUMBER_WORDS = (
    "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    "first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth"
)
_ITEM_MARKER_RE = re.compile(
    rf"^\s*(?:[#>*-]+\s*)?(?:(?:number|no\.?|item|step|chapter)\s+)?"
    rf"(?:\#\s*)?(?P<rank>\d{{1,3}}(?:st|nd|rd|th)?|{_NUMBER_WORDS})"
    r"\s*(?:[.):\-–—]|\b)\s*(?P<subject>[^.!?\n]{2,72})",
    re.IGNORECASE,
)
_LIST_COUNT_RE = re.compile(
    rf"\b(?P<count>\d{{1,2}}|{_NUMBER_WORDS})\s+"
    r"(?P<label>reasons|ways|steps|lessons|facts|things|rules|mistakes|jobs|items)\b",
    re.IGNORECASE,
)
_TOP_COUNT_RE = re.compile(
    rf"\btop\s+(?P<count>\d{{1,2}}|{_NUMBER_WORDS})\s+"
    r"(?:(?:best|greatest|current|modern)\s+)?"
    r"(?P<label>players?|footballers?|teams?|clubs?|films?|movies?|albums?|reasons?|facts?)\b",
    re.IGNORECASE,
)
_MONTH = (
    r"January|February|March|April|May|June|July|August|September|October|"
    r"November|December|Jan\.?|Feb\.?|Mar\.?|Apr\.?|Jun\.?|Jul\.?|Aug\.?|"
    r"Sep\.?|Sept\.?|Oct\.?|Nov\.?|Dec\."
)
_DATE_RE = re.compile(
    rf"\b(?:{_MONTH})\s+\d{{1,2}}(?!\d)(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b|"
    rf"\b\d{{1,2}}\s+(?:{_MONTH})(?:\s+\d{{4}})?\b|"
    r"\b(?:1[0-9]{3}|20[0-9]{2}|2100)\b",
    re.IGNORECASE,
)
_STAT_RE = re.compile(
    r"(?<![\w])(?:over|more\s+than|nearly|around|almost)?\s*(?:[$€£¥]\s*)?"
    r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s+(?:career|league|international|club|professional|season|total|major|individual)){0,3}"
    r"\s+(?:goals?|titles?|trophies?|awards?|championships?|records?|matches?|apps?|appearances?)\b|"
    r"(?<![\w])(?:[$€£¥]\s*)?"
    r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s*(?:%|percent|per\s+cent|million|billion|trillion|thousand|"
    r"people|workers|passengers|deaths|survivors|votes|users|views|miles|"
    r"kilometers|km|meters|feet|hours|minutes|seconds|days|years|dollars|"
    r"euros|pounds|tons|tonnes|degrees))\b|"
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|forty|fifty|"
    r"sixty|seventy|eighty|ninety)(?:\s+(?:one|two|three|four|five|six|seven|eight|nine|ten))?\s+percent\b",
    re.IGNORECASE,
)
_ACHIEVEMENT_RE = re.compile(
    r"\b(?:"
    r"(?:multiple|several|numerous|countless)\s+)?"
    r"(?:UEFA\s+)?Champions\s+League(?:\s+(?:titles?|trophies?))?"
    r"|FIFA\s+World\s+Cup(?:\s+(?:title|trophy|final|win|victory))?"
    r"|individual\s+awards?"
    r"|league\s+championships?"
    r"|scoring\s+records?"
    r"|Ballon\s+d'Or"
    r"\b",
    re.IGNORECASE,
)
_QUOTE_RE = re.compile(r"[\"“](?P<quote>[^\"”\n]{3,96})[\"”]")
_CHRONOLOGY_RE = re.compile(
    r"\b(?:"
    r"(?:centuries|decades|years)\s+of\b"
    r"|(?:across|over|in|during)\s+(?:seven|several|multiple|\d+)\s+decades\b"
    r"|dating\s+back\s+to\b"
    r"|\d+(?:st|nd|rd|th)\s+century\b"
    r"|since\s+\d{4}\b"
    r"|\b(?:1[5-9]\d\d|20\d\d)s?\b"
    r")",
    re.IGNORECASE,
)
_CONTRAST_RE = re.compile(
    r"\b(?:"
    r"combining\s+[^.!?,\r\n]{3,40}\s+with\b"
    r"|balance\s+[^.!?,\r\n]{3,40}\s+with\b"
    r"|contrast(?:ing|\s+with)?\b"
    r"|compared\s+to\b"
    r"|divided\s+between\b"
    r"|while\s+the\s+[^.!?,\r\n]{3,40}\b"
    r")",
    re.IGNORECASE,
)
_LANDMARK_WORDS = {
    "castle", "palace", "cathedral", "abbey", "monument", "tower", "bridge",
    "commonwealth", "headquarters", "center", "centre", "hall", "mansion",
    "estate", "station", "airport", "museum", "gallery", "theatre", "theater",
    "stadium", "arena", "harbour", "harbor", "port", "temple", "shrine",
    "square", "park", "garden", "avenue", "street", "road", "boulevard",
    "lane", "court", "island", "islands", "peninsula", "bay", "strait",
    "channel", "gulf", "valley", "mountain", "mount", "peak", "hill",
    "desert", "forest", "plain", "plains", "county", "district", "province",
    "territory", "republic", "empire", "federation", "union", "confederation",
    "kingdom", "nation", "ocean", "sea", "river", "city",
}

_TITLED_PERSON_RE = re.compile(
    r"\b(?:King|Queen|Prince|Princess|Duke|Duchess|Dr\.?|Professor|President|Prime\s+Minister)\s+"
    r"[A-Z][A-Za-z'’-]{1,24}(?:\s+(?:[A-Z][A-Za-z'’-]{1,24}|I{1,3}|IV|V)){0,2}\b"
)
_PROPER_NAME_RE = re.compile(
    r"\b[A-Z][A-Za-z'’-]{2,24}\s+[A-Z][A-Za-z'’-]{2,24}"
    r"(?:\s+[A-Z][A-Za-z'’-]{2,24})?\b"
)
_NON_PERSON_NAME_WORDS = {
    "the", "this", "that", "these", "those", "british", "royal", "family", "palace", "house",
    "world", "cup", "league", "ocean", "sea", "river", "city", "university",
    "government", "party", "media", "kingdom", "states", "north", "south",
    "east", "west", "august", "september", "october", "november", "december",
    "grand", "theft", "auto", "games", "rockstar", "largest", "densest",
    "first", "official", "trailer", "blockbuster", "entertainment", "launch",
    "physics", "handling", "streets", "sandbox", "weather", "storms",
    "and", "but", "or", "nor", "so", "yet", "for", "with", "from", "by",
    "at", "in", "on", "into", "onto", "about", "after", "before", "while",
    "during", "over", "under", "across", "through", "is", "are", "was",
    "were", "has", "have", "had", "will", "would", "can", "could", "still",
    "also", "only", "just", "not", "than", "then", "there", "here", "microsoft",
    "sony", "nintendo", "google", "apple", "amazon", "valve", "developer", "studio",
} | _LANDMARK_WORDS
_NON_LOCATION_WORDS = {
    "microsoft", "google", "apple", "amazon", "sony", "rockstar", "nintendo",
    "valve", "meta", "netflix", "company", "corporation", "studio", "studios",
    "publisher", "developer", "industry", "market", "records", "record",
    "police", "highway", "getaways", "sandbox", "trailer",
}


@dataclass(frozen=True)
class _Candidate:
    cue_type: str
    text: str
    local_char_start: int
    local_char_end: int
    local_token_start: int
    local_token_end: int
    template: str
    priority: int
    reason: str
    structural: bool = False


@dataclass(frozen=True)
class _TimedWord:
    index: int
    text: str
    start: float
    end: float
    confidence: float


def _value(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def _as_mapping(row: Any) -> Dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    if hasattr(row, "to_dict"):
        return dict(row.to_dict())
    return {}


def _norm_span(text: str, start: int, end: int) -> Tuple[str, int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return text[start:end], start, end


def _token_span(text: str, start: int, end: int) -> Tuple[int, int]:
    tokens = tokenize_source(text)
    indexes = [token.index for token in tokens if token.char_end > start and token.char_start < end]
    if not indexes:
        return 0, 0
    return indexes[0], indexes[-1] + 1


def _exact_span(text: str, candidate: str) -> Optional[Tuple[int, int]]:
    """Find one exact, case-insensitive contiguous source span."""
    needle = re.sub(r"\s+", " ", str(candidate or "")).strip()
    if not needle:
        return None
    pattern = r"\s+".join(re.escape(part) for part in needle.split(" "))
    match = re.search(pattern, text, re.IGNORECASE | re.UNICODE)
    return (match.start(), match.end()) if match else None


def _important_phrase(value: str) -> bool:
    """Reject pronoun/connective fragments before they become text cards."""
    tokens = [token.normalized for token in tokenize_source(value)]
    if not tokens or len(tokens) > 4:
        return False
    meaningful = [token for token in tokens if token not in _PHRASE_STOPWORDS and len(token) > 2]
    if not meaningful:
        return False
    if any(re.search(r"\d|%", token) for token in meaningful):
        return True
    if any(token in _PHRASE_SIGNAL_WORDS for token in meaningful):
        return True
    # A multi-token named entity or concrete noun phrase is useful; a lone
    # generic verb/adverb is not.  Proper-case is checked against the source
    # by callers, so this final gate only handles the one-word case.
    return len(meaningful) >= 2 or len(meaningful[0]) >= 5


def _container_for(scene: Any, containers: Mapping[str, Any]) -> Any:
    return containers.get(str(_value(scene, "container_id", "")))


def _is_opening_scene(index: int, scenes: Sequence[Any]) -> bool:
    if index <= 0:
        return True
    return str(_value(scenes[index - 1], "container_id", "")) != str(
        _value(scenes[index], "container_id", "")
    )


def _add_candidate(
    target: List[_Candidate], text: str, cue_type: str, start: int, end: int,
    template: str, priority: int, reason: str, *, structural: bool = False,
) -> None:
    grounded, start, end = _norm_span(text, start, end)
    token_start, token_end = _token_span(text, start, end)
    if not grounded or token_end <= token_start:
        return
    target.append(_Candidate(
        cue_type=cue_type,
        text=grounded,
        local_char_start=start,
        local_char_end=end,
        local_token_start=token_start,
        local_token_end=token_end,
        template=template,
        priority=priority,
        reason=reason,
        structural=structural,
    ))


def _extract_candidates(
    text: str,
    beat: Mapping[str, Any],
    scene: Any,
    container: Any,
    *,
    opening_scene: bool,
) -> List[_Candidate]:
    candidates: List[_Candidate] = []
    treatment = str(beat.get("treatment") or "").strip().lower()
    role = str(beat.get("role") or "").strip().lower()
    container_kind = str(_value(container, "kind", "") or "").strip().lower()

    if opening_scene and container_kind in _ITEM_KINDS:
        marker = _ITEM_MARKER_RE.search(text)
        if marker:
            subject_end = marker.end("subject")
            # The marker pattern permits a heading and its narration on the
            # same line, so the subject group can consume the whole sentence.
            # Prefer the parser's exact canonical-subject span when available;
            # that keeps the structural card limited to rank plus subject.
            canonical_subject = str(
                _value(container, "canonical_subject", "") or ""
            ).strip()
            canonical_span = _exact_span(text, canonical_subject)
            if (
                canonical_span
                and canonical_span[0] >= marker.start("subject")
                and canonical_span[1] <= marker.end("subject")
            ):
                subject_end = canonical_span[1]
            # Keep one complete item heading, not the following explanation.
            subject_text = marker.group("subject").strip(" -–—,:;")
            subject_tokens = list(tokenize_source(subject_text))
            if subject_end == marker.end("subject") and len(subject_tokens) > 8:
                subject_end = marker.start("subject") + subject_tokens[7].char_end
            cue_type = "chapter_heading" if container_kind == "chapter" else "item_heading"
            _add_candidate(
                candidates, text, cue_type, marker.start(), subject_end,
                "chapter-opener",
                110, "explicit_container_heading", structural=True,
            )
        else:
            subject = str(_value(container, "canonical_subject", "") or "").strip()
            subject_span = _exact_span(text, subject)
            if subject_span:
                cue_type = "chapter_heading" if container_kind == "chapter" else "item_heading"
                _add_candidate(
                    candidates, text, cue_type, *subject_span,
                    "chapter-opener",
                    105, "grounded_container_subject", structural=True,
                )

    count_match = _LIST_COUNT_RE.search(text)
    if count_match:
        _add_candidate(
            candidates, text, "item_heading", count_match.start(), count_match.end(),
            "listicle-build", 100, "grounded_enumeration", structural=opening_scene,
        )

    top_count_match = _TOP_COUNT_RE.search(text)
    if top_count_match:
        _add_candidate(
            candidates, text, "item_heading", top_count_match.start(), top_count_match.end(),
            "listicle-build", 114, "grounded_top_count", structural=True,
        )

    occupied: List[Tuple[int, int]] = []
    stat_matches = list(_STAT_RE.finditer(text))
    for match in _DATE_RE.finditer(text):
        # A four-digit count is not a year merely because it lies in the year
        # range: 1500 people, 2000 dollars and 1912 miles are quantities. Full
        # calendar phrases remain dates; a standalone, explicitly narrated
        # year still produces a chronology cue.
        if re.fullmatch(r"\d{4}", match.group(0)) and any(
            stat.start() <= match.start() and stat.end() >= match.end()
            for stat in stat_matches
        ):
            continue
        _add_candidate(
            candidates, text, "date", match.start(), match.end(),
            "date-card", 96, "grounded_date", structural=True,
        )
        occupied.append((match.start(), match.end()))

    for match in _CHRONOLOGY_RE.finditer(text):
        if re.fullmatch(r"\d{4}", match.group(0)) and any(
            stat.start() <= match.start() and stat.end() >= match.end()
            for stat in stat_matches
        ):
            continue
        if any(match.start() < end and match.end() > start for start, end in occupied):
            continue
        _add_candidate(
            candidates, text, "date", match.start(), match.end(),
            "date-card", 95, "grounded_chronology", structural=True,
        )
        occupied.append((match.start(), match.end()))

    for match in _CONTRAST_RE.finditer(text):
        if any(match.start() < end and match.end() > start for start, end in occupied):
            continue
        _add_candidate(
            candidates, text, "comparison", match.start(), match.end(),
            "split-screen", 92, "grounded_contrast", structural=True,
        )
        occupied.append((match.start(), match.end()))

    if len(stat_matches) >= 2:
        m = stat_matches[0]
        if not any(m.start() < end and m.end() > start for start, end in occupied):
            _add_candidate(
                candidates, text, "statistic", m.start(), m.end(),
                "bar-chart", 96, "grounded_comparative_chart", structural=True,
            )
            occupied.append((m.start(), m.end()))

    for match in _STAT_RE.finditer(text):
        if any(match.start() < end and match.end() > start for start, end in occupied):
            continue
        _add_candidate(
            candidates, text, "statistic", match.start(), match.end(),
            "stat-card", 92, "grounded_statistic", structural=True,
        )
        occupied.append((match.start(), match.end()))

    for match in _ACHIEVEMENT_RE.finditer(text):
        if any(match.start() < end and match.end() > start for start, end in occupied):
            continue
        achievement_text = match.group(0)
        achievement_priority = 98 if re.search(r"FIFA\s+World\s+Cup", achievement_text, re.I) else (
            91 if re.search(r"Champions\s+League", achievement_text, re.I) else 88
        )
        _add_candidate(
            candidates, text, "highlight", match.start(), match.end(),
            "keyword-reveal", achievement_priority, "grounded_achievement", structural=True,
        )
        occupied.append((match.start(), match.end()))

    for match in _QUOTE_RE.finditer(text):
        _add_candidate(
            candidates, text, "quote", match.start("quote"), match.end("quote"),
            "quote-card", 88, "grounded_quote", structural=True,
        )

    # Model-independent identity recovery. A person card is useful only when
    # the narration supplies a complete multi-word identity; never turn a
    # pronoun, bare first name, or generic capitalized phrase into an overlay.
    person_matches = list(_TITLED_PERSON_RE.finditer(text))
    person_matches.extend(_PROPER_NAME_RE.finditer(text))
    person_matches.sort(key=lambda item: (item.start(), -(item.end() - item.start())))
    for match in person_matches:
        phrase = match.group(0).strip()
        tokens = phrase.split()
        if tokens and tokens[0].lower().strip(".,;:–—- \"'") in {
            "and", "but", "or", "nor", "for", "with", "from", "by", "in", "on", "at", "to", "after", "before", "while", "during"
        }:
            tokens.pop(0)
        if not tokens or len(tokens) < 2:
            continue
        cleaned_tokens = [re.sub(r"['’]s$", "", t.lower().strip(".,;:–—- \"'“”‘’()[]{}")) for t in tokens]
        if any(t in _LANDMARK_WORDS for t in cleaned_tokens):
            if not any(match.start() < end and match.end() > start for start, end in occupied):
                _add_candidate(
                    candidates, text, "location", match.start(), match.end(),
                    "map-route", 87, "grounded_landmark_location", structural=True,
                )
                occupied.append((match.start(), match.end()))
            continue
        if any(t in _NON_PERSON_NAME_WORDS for t in cleaned_tokens):
            continue
        if any(match.start() < end and match.end() > start for start, end in occupied):
            continue
        _add_candidate(
            candidates, text, "person", match.start(), match.end(),
            "lower-third", 89, "grounded_full_person_name", structural=True,
        )
        break

    emphasis = str(beat.get("emphasis") or beat.get("selected_for_text") or "").strip()
    emphasis_explicit = bool(beat.get("_emphasis_explicit", bool(emphasis)))
    emphasis_span = None
    span_hint = beat.get("emphasis_span") if isinstance(beat.get("emphasis_span"), Mapping) else {}
    hinted_start = beat.get("emphasis_char_start", span_hint.get("char_start"))
    hinted_end = beat.get("emphasis_char_end", span_hint.get("char_end"))
    if emphasis_explicit and hinted_start is not None and hinted_end is not None:
        try:
            hinted_start, hinted_end = int(hinted_start), int(hinted_end)
            scene_char_start = int(_value(scene, "char_start", 0) or 0)
            # Accept either scene-local or canonical script offsets.
            if hinted_start >= scene_char_start and hinted_end > len(text):
                hinted_start -= scene_char_start
                hinted_end -= scene_char_start
            if 0 <= hinted_start < hinted_end <= len(text):
                hinted_text = text[hinted_start:hinted_end]
                if [token.normalized for token in tokenize_source(hinted_text)] == [
                    token.normalized for token in tokenize_source(emphasis)
                ]:
                    emphasis_span = (hinted_start, hinted_end)
        except (TypeError, ValueError):
            emphasis_span = None
    if emphasis_span is None and emphasis_explicit:
        emphasis_span = _exact_span(text, emphasis)
    if emphasis_span and (role in {"person", "location"} or treatment in {"person", "location"}):
        cue_type = "person" if role == "person" or treatment == "person" else "location"
        if cue_type == "person":
            emp_text = text[emphasis_span[0]:emphasis_span[1]].strip()
            emp_words = emp_text.split()
            cleaned_emp = [re.sub(r"['’]s$", "", t.lower().strip(".,;:–—- \"'“”‘’()[]{}")) for t in emp_words]
            if (
                any(t in _NON_PERSON_NAME_WORDS for t in cleaned_emp)
                or not any(w[0].isupper() for w in emp_words if w)
            ):
                cue_type = ""
        elif cue_type == "location":
            emp_text = text[emphasis_span[0]:emphasis_span[1]].strip()
            emp_words = emp_text.split()
            cleaned_emp = [re.sub(r"['’]s$", "", t.lower().strip(".,;:–—- \"'“”‘’()[]{}")) for t in emp_words]
            if any(t in _NON_LOCATION_WORDS for t in cleaned_emp):
                cue_type = ""
        if cue_type:
            _add_candidate(
                candidates, text, cue_type, *emphasis_span,
                "lower-third" if cue_type == "person" else "map-route",
                84, f"grounded_{cue_type}",
            )
    elif emphasis_span:
        token_count = len(tokenize_source(text[emphasis_span[0]:emphasis_span[1]]))
        importance = float(beat.get("importance") or beat.get("salience") or 0.0)
        normalized = canonical_token(emphasis)
        if (
            1 <= token_count <= 4
            and normalized not in _GENERIC_HIGHLIGHTS
            and _important_phrase(emphasis)
            and importance >= 0.70
            and treatment not in _NEGATIVE_TREATMENTS
        ):
            _add_candidate(
                candidates, text, "highlight", *emphasis_span,
                "keyword-reveal", 70, "explicit_grounded_highlight",
            )

    # Strong factual structures are self-evident.  Ordinary highlights obey
    # explicit negative/breathing decisions.
    if beat.get("selected_for_text") is False:
        candidates = [
            item for item in candidates
            if item.cue_type in (_STRUCTURAL_KINDS | {"date", "statistic", "person"})
        ]
    return sorted(
        candidates,
        key=lambda item: (-item.priority, item.local_char_start, item.local_char_end),
    )


def _coerce_words(values: Iterable[Any]) -> List[_TimedWord]:
    words: List[_TimedWord] = []
    for fallback_index, row in enumerate(values or []):
        try:
            index = int(_value(row, "index", fallback_index))
            text = str(_value(row, "text", _value(row, "word", "")) or "")
            start = float(_value(row, "start_seconds", _value(row, "start", 0.0)) or 0.0)
            end = float(_value(row, "end_seconds", _value(row, "end", start)) or start)
            confidence = float(_value(row, "confidence", _value(row, "probability", 1.0)) or 0.0)
        except (TypeError, ValueError):
            continue
        if text.strip() and start >= 0 and end >= start:
            words.append(_TimedWord(index, text, start, end, max(0.0, min(1.0, confidence))))
    return sorted(words, key=lambda item: item.index)


def _expanded_word_tokens(words: Sequence[_TimedWord]) -> Tuple[List[str], List[int]]:
    tokens: List[str] = []
    owner_indexes: List[int] = []
    for word in words:
        pieces = tokenize_source(word.text)
        normalized = [piece.normalized for piece in pieces if piece.normalized]
        if not normalized:
            fallback = canonical_token(word.text)
            normalized = [fallback] if fallback else []
        for token in normalized:
            tokens.append(token)
            owner_indexes.append(word.index)
    return tokens, owner_indexes


def _align_source_to_words(source: Sequence[str], transcript: Sequence[str]) -> Dict[int, int]:
    """Needleman-Wunsch alignment returning exact-token source/word matches."""
    n, m = len(source), len(transcript)
    if not n or not m:
        return {}
    gap = 0.72
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    move = [[""] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0], move[i][0] = i * gap, "up"
    for j in range(1, m + 1):
        dp[0][j], move[0][j] = j * gap, "left"
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            equal = source[i - 1] == transcript[j - 1]
            choices = (
                (dp[i - 1][j - 1] + (0.0 if equal else 1.0), "diag"),
                (dp[i - 1][j] + gap, "up"),
                (dp[i][j - 1] + gap, "left"),
            )
            dp[i][j], move[i][j] = min(choices, key=lambda item: item[0])
    mapping: Dict[int, int] = {}
    i, j = n, m
    while i or j:
        direction = move[i][j]
        if direction == "diag":
            if source[i - 1] == transcript[j - 1]:
                mapping[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif direction == "up":
            i -= 1
        elif direction == "left":
            j -= 1
        else:
            break
    return mapping


def _fold_alignment_token(value: str) -> str:
    """Fold accents and punctuation for a conservative ASR name match."""
    folded = unicodedata.normalize("NFKD", str(value or ""))
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]", "", folded.lower())


def _fuzzy_candidate_alignment(source: Sequence[str], transcript: Sequence[str]) -> Dict[int, int]:
    """Align a short grounded phrase to common Whisper spelling variants.

    Whisper often returns ``Holland`` for ``Haaland``, ``Killian Umbape`` for
    ``Kylian Mbappé``, or ``Messy`` for ``Messi``. The source phrase is already
    grounded by the script, so a short monotonic fuzzy match (never a broad
    character-position guess) safely recovers the spoken timestamps.
    """
    if not source or not transcript:
        return {}
    folded_source = [_fold_alignment_token(token) for token in source]
    folded_transcript = [_fold_alignment_token(token) for token in transcript]
    mapping: Dict[int, int] = {}
    cursor = 0
    for source_index, source_token in enumerate(folded_source):
        if not source_token:
            return {}
        best_index = -1
        best_score = 0.0
        for transcript_index in range(cursor, len(folded_transcript)):
            transcript_token = folded_transcript[transcript_index]
            if not transcript_token:
                continue
            score = SequenceMatcher(None, source_token, transcript_token).ratio()
            # Exact/nearby words win over a later accidental match. A small
            # distance penalty keeps names ordered without character timing.
            score -= min(0.08, max(0, transcript_index - cursor) * 0.004)
            if score > best_score:
                best_score = score
                best_index = transcript_index
        threshold = 0.66 if len(source_token) >= 5 else 0.78
        if best_index < 0 or best_score < threshold:
            return {}
        mapping[source_index] = best_index
        cursor = best_index + 1
    return mapping


def _candidate_timing(
    candidate: _Candidate,
    scene: Any,
    all_words: Sequence[_TimedWord],
) -> Optional[Tuple[int, int, float, float, float, float, str]]:
    try:
        scene_start = float(_value(scene, "start_seconds", _value(scene, "start", 0.0)) or 0.0)
        scene_end_value = _value(scene, "end_seconds", None)
        if scene_end_value is None:
            scene_end = scene_start + float(_value(scene, "duration", 0.0) or 0.0)
        else:
            scene_end = float(scene_end_value)
    except (TypeError, ValueError):
        return None
    scene_duration = scene_end - scene_start
    word_count = candidate.local_token_end - candidate.local_token_start
    ideal_minimum = min(3.8, max(2.5, 2.5 + 0.30 * max(0, word_count - 1)))
    minimum = min(scene_duration, ideal_minimum)
    if scene_duration < 1.2 or minimum < 1.0:
        return None

    word_start = _value(scene, "word_start_index", None)
    word_end = _value(scene, "word_end_index", None)
    timing_quality = str(_value(scene, "timing_quality", "pending") or "pending").lower()
    try:
        alignment_confidence = float(_value(scene, "alignment_confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        alignment_confidence = 0.0
    if word_start is not None and word_end is not None and alignment_confidence >= 0.72 and timing_quality not in {"estimated", "pending", "failed"}:
        scene_words = [word for word in all_words if int(word_start) <= word.index < int(word_end)]
        source_tokens = [token.normalized for token in tokenize_source(str(_value(scene, "text", "") or ""))]
        transcript_tokens, owners = _expanded_word_tokens(scene_words)
        alignment = _align_source_to_words(source_tokens, transcript_tokens)
        mapped_token_indexes = [alignment.get(index) for index in range(candidate.local_token_start, candidate.local_token_end)]
        if not mapped_token_indexes or not all(index is not None for index in mapped_token_indexes):
            # Retry only the grounded candidate span. This preserves exact
            # Whisper timing while tolerating phonetic name spellings; it does
            # not estimate timing from character offsets.
            candidate_tokens = source_tokens[candidate.local_token_start:candidate.local_token_end]
            fuzzy_alignment = _fuzzy_candidate_alignment(candidate_tokens, transcript_tokens)
            mapped_token_indexes = [
                fuzzy_alignment.get(index)
                for index in range(len(candidate_tokens))
            ]
        if mapped_token_indexes and all(index is not None for index in mapped_token_indexes):
            owner_ids = [owners[int(index)] for index in mapped_token_indexes]
            by_id = {word.index: word for word in scene_words}
            matched_words = [by_id[index] for index in sorted(set(owner_ids)) if index in by_id]
            if matched_words and min(word.confidence for word in matched_words) >= 0.55:
                spoken_start = matched_words[0].start
                spoken_end = matched_words[-1].end
                local_start = max(0.0, spoken_start - scene_start - 0.08)
                local_end = min(scene_duration, max(local_start + minimum, spoken_end - scene_start + 1.8))
                if local_end - local_start < minimum:
                    missing = minimum - (local_end - local_start)
                    local_start = max(0.0, local_start - missing / 2)
                    local_end = min(scene_duration, local_end + missing / 2)
                    if local_end - local_start < minimum:
                        if local_start <= 1e-6:
                            local_end = min(scene_duration, minimum)
                        elif local_end >= scene_duration - 1e-6:
                            local_start = max(0.0, scene_duration - minimum)
                if local_end - local_start + 1e-6 >= min(1.0, minimum):
                    return (
                        min(owner_ids), max(owner_ids) + 1,
                        round(local_start, 3), round(local_end, 3),
                        round(scene_start + local_start, 3), round(scene_start + local_end, 3),
                        "word_aligned",
                    )

    # Never guess a speech-linked card from character position.  A phonetic
    # mismatch is still a mismatch: returning an apparently synchronized date,
    # name, or quote is worse than leaving the frame media-only.  Structural
    # cards can be re-requested by the planner after a corrected Whisper pass.
    return None


def _normalized_bbox(value: Any) -> Optional[Tuple[float, float, float, float]]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(part) for part in value)
    except (TypeError, ValueError):
        return None
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        return None
    return (x1, y1, x2, y2)


def _intersection_ratio(a: Sequence[float], b: Sequence[float]) -> float:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    if right <= left or bottom <= top:
        return 0.0
    area = (right - left) * (bottom - top)
    return area / max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))


_PLACEMENT_BOXES: Dict[str, Tuple[float, float, float, float]] = {
    "top_left": (0.08, 0.07, 0.58, 0.30),
    "top_right": (0.42, 0.07, 0.92, 0.30),
    "center": (0.20, 0.34, 0.80, 0.64),
    "bottom_left": (0.08, 0.65, 0.58, 0.87),
    "bottom_right": (0.42, 0.65, 0.92, 0.87),
}


def _placement(
    cue_type: str,
    media: Mapping[str, Any],
    *,
    captions_enabled: bool,
    caption_band: Optional[Sequence[float]],
    recent_positions: Sequence[str],
) -> Optional[Dict[str, Any]]:
    decision = media.get("visual_decision") if isinstance(media.get("visual_decision"), Mapping) else {}
    subject = _normalized_bbox(
        decision.get("subject_bbox") or decision.get("focal_bbox")
        or media.get("subject_bbox") or media.get("focal_bbox")
    )
    caption = _normalized_bbox(caption_band) if captions_enabled else None
    if captions_enabled and caption is None:
        caption = (0.04, 0.76, 0.96, 0.98)
    safe_regions = [
        box for box in (
            _normalized_bbox(value) for value in (decision.get("safe_text_regions") or [])
        ) if box is not None
    ]
    preferred = {
        "statistic": ("top_right", "top_left", "center", "bottom_right", "bottom_left"),
        "date": ("top_left", "top_right", "center", "bottom_left", "bottom_right"),
        "item_heading": ("center", "top_left", "top_right", "bottom_left", "bottom_right"),
        "chapter_heading": ("center", "top_left", "top_right", "bottom_left", "bottom_right"),
        "person": ("bottom_left", "bottom_right", "top_left", "top_right", "center"),
        "location": ("bottom_left", "top_left", "bottom_right", "top_right", "center"),
        "quote": ("center", "top_left", "top_right", "bottom_left", "bottom_right"),
        "highlight": ("center", "top_left", "top_right", "bottom_left", "bottom_right"),
    }.get(cue_type, ("top_left", "top_right", "center", "bottom_left", "bottom_right"))
    scores: List[Tuple[float, int, str, Tuple[float, float, float, float]]] = []
    for order, position in enumerate(preferred):
        if position not in ALLOWED_TEXT_POSITIONS:
            continue
        box = _PLACEMENT_BOXES[position]
        subject_overlap = _intersection_ratio(box, subject) if subject else 0.0
        caption_overlap = _intersection_ratio(box, caption) if caption else 0.0
        if subject_overlap > 0.10 or caption_overlap > 0.01:
            continue
        safe_bonus = max((_intersection_ratio(box, region) for region in safe_regions), default=0.0)
        repeat_penalty = 0.20 if position in set(recent_positions[-1:]) else 0.0
        score = 1.0 - order * 0.05 + safe_bonus * 0.35 - repeat_penalty
        scores.append((score, -order, position, box))
    if not scores:
        return None
    _, _, position, box = max(scores)
    return {
        "position": position,
        "normalized_box": [round(value, 4) for value in box],
        "subject_bbox": list(subject) if subject else None,
        "caption_band": list(caption) if caption else None,
        "reason": "safe_region_subject_caption_avoidance",
    }


def _clean_cut(s: str, max_chars: int = 48) -> str:
    s = str(s or "").strip()
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars]
    if " " in cut:
        return cut.rsplit(" ", 1)[0].strip()
    return cut


def _props(
    candidate: _Candidate,
    placement: Mapping[str, Any],
    *,
    variation_seed: Any = 0,
    art_direction: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    value = candidate.text
    # Keep the card's editorial emphasis explicit in the manifest.  Structural
    # cards are already grounded by their exact source span, but the renderer
    # still needs one salient token to accent so the project's own typography
    # does not read like an unedited subtitle.
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9'/-]*", value)
    stop = _PHRASE_STOPWORDS | {
        "a", "an", "and", "for", "from", "in", "of", "on", "the", "to",
        "he", "she", "his", "her", "their", "them", "they", "it", "its", "this", "that",
        "with", "by", "at", "as", "into", "through", "during", "before", "after",
        "above", "below", "between", "under", "across", "behind", "but", "or", "nor",
        "so", "yet", "both", "either", "neither", "about", "against", "over", "such",
        "than", "too", "very", "can", "will", "just", "should", "now", "also", "was",
        "were", "is", "are", "been", "being", "have", "has", "had", "would", "could",
    }
    grounded = select_grounded_highlight(value, max_words=2)
    if grounded:
        highlight = grounded
    else:
        highlight = next(
            (token for token in reversed(tokens) if token.lower() not in stop and len(token) > 2),
            tokens[-1] if tokens else value,
        )
    result: Dict[str, Any]
    if candidate.cue_type in {"item_heading", "chapter_heading"}:
        result = {"title": value, "highlight": highlight, "items": [value], "line1": value, "line2": "", "line3": "", "kicker": ""}
    elif candidate.cue_type == "date":
        result = {"label": value, "date": value, "value": value, "highlight": value, "title": "", "kicker": ""}
    elif candidate.cue_type == "statistic":
        result = {"value": value, "label": "", "unit": "", "highlight": value}
    elif candidate.cue_type == "quote":
        result = {"quote": value, "attribution": ""}
    elif candidate.cue_type == "comparison":
        result = {"title": value, "left": value, "right": "", "highlight": highlight}
    elif candidate.cue_type == "person":
        result = {"title": value, "name": value, "subtitle": ""}
    elif candidate.cue_type == "location":
        result = {"label": value, "location": value, "to": value, "from": ""}
    else:
        result = {"text": value, "title": value, "highlight": highlight}
    meaningful = [
        token for token in tokens
        if token.lower() not in stop and len(token) > 1
    ]
    if candidate.cue_type == "statistic":
        # Numbers and their unit carry the factual payload; do not flash
        # connective words such as “over” or “career”.
        important_tokens = [
            token for token in meaningful
            if re.search(r"\d", token)
            or token.lower().rstrip("s") in {
                "goal", "title", "trophy", "award", "championship",
                "record", "match", "appearance", "app",
            }
        ]
        highlight_tokens = important_tokens or meaningful[-2:]
    elif candidate.cue_type == "highlight":
        highlight_tokens = [t for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9'/-]*", highlight)] or meaningful[:5] or tokens[-1:]
    else:
        highlight_tokens = [t for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9'/-]*", highlight)] if highlight else []
    result["_highlight_tokens"] = [str(token).lower().strip(".,!?;:\"'()[]") for token in highlight_tokens]
    contract_kind = {
        "item_heading": "chapter", "chapter_heading": "chapter",
        "statistic": "stat", "comparison": "comparison",
    }.get(candidate.cue_type, candidate.cue_type)
    design = resolve_template_design(
        candidate.template,
        kind=contract_kind,
        seed=variation_seed,
        art_direction=art_direction or art_direction_pack("general", variation_seed),
        position=str(placement.get("position") or "center"),
    )
    contract_size = {
        "stat": 106, "date": 82, "chapter": 86, "person": 78,
        "location": 72, "quote": 66, "archive": 72, "highlight": 76,
    }.get(contract_kind, 76)
    result.update({
        "_editorial_contract": True,
        "_cue_schema_version": 2,
        "_contract_text": _clean_cut(value, 48),
        "_contract_kind": contract_kind,
        "_use_generic_contract": candidate.cue_type == "highlight",
        "_position": placement.get("position", "center"),
        "_contract_font": design["display_font"],
        "_body_font": design["body_font"],
        "_accent_font": design["accent_font"],
        "_contract_size": contract_size,
        "_card_mode": design["card_mode"],
        "_entry_motion": design["entry_motion"],
        "_font_style": design["font_style"],
        "_body_font_style": design["body_font_style"],
        "_text_transform": design["text_transform"],
        "_strip_style": design["strip_style"],
        "_template_family": design["family"],
        "_template_variant": design["variant"],
        "_art_direction": design["direction"],
        "_show_rule": False,
    })
    return result


def plan_editorial_cues(
    scenes: Iterable[Any],
    word_timings: Iterable[Any],
    *,
    containers: Iterable[Any] = (),
    editorial_beats: Iterable[Mapping[str, Any]] = (),
    media: Iterable[Mapping[str, Any]] = (),
    captions_enabled: bool = False,
    caption_band: Optional[Sequence[float]] = None,
    variation_seed: Any = 0,
    art_direction: Optional[Mapping[str, Any]] = None,
) -> Tuple[EditorialCueV2, ...]:
    """Plan a small, exact set of validated cues per scene.

    A chapter opener can now share a scene with one grounded fact/highlight
    when their Whisper windows do not collide. This is what lets a ranking
    scene show both “Lionel Messi” and “FIFA World Cup” instead of silently
    dropping the second editorial beat.
    """
    scene_rows = list(scenes or [])
    beats = [dict(value or {}) for value in (editorial_beats or [])]
    media_rows = [dict(value or {}) for value in (media or [])]
    container_map = {str(_value(item, "container_id", "")): item for item in (containers or [])}
    words = _coerce_words(word_timings)
    result: List[EditorialCueV2] = []
    recent_positions: List[str] = []
    last_nonstructural_end = -math.inf
    seen_person_cues: set[str] = set()
    recent_templates: List[str] = []

    cue_template_variants = {
        "date": ("timeline-trace", "date-card"),
        "statistic": ("bar-chart", "stat-card", "metric-grid"),
        "comparison": ("split-screen", "comparison"),
        "highlight": ("keyword-reveal", "kinetic-statement"),
        "person": ("lower-third", "person-profile", "dossier-card"),
        "location": ("map-route", "map-comparison"),
        "quote": ("quote-card",),
    }

    for index, scene in enumerate(scene_rows):
        text = str(_value(scene, "text", _value(scene, "narration", "")) or "")
        if not text.strip():
            continue
        beat = beats[index] if index < len(beats) else {}
        container = _container_for(scene, container_map)
        candidates = _extract_candidates(
            text,
            beat,
            scene,
            container,
            opening_scene=_is_opening_scene(index, scene_rows),
        )
        if not candidates:
            continue
        media_item = media_rows[index] if index < len(media_rows) else {}
        selected_rows: List[Tuple[_Candidate, Tuple[int, int, float, float, float, float, str]]] = []
        for candidate in candidates:
            person_key = canonical_token(candidate.text)
            if candidate.cue_type == "person" and person_key in seen_person_cues:
                continue
            candidate_timing = _candidate_timing(candidate, scene, words)
            if candidate_timing is None:
                continue
            if not candidate.structural and candidate_timing[4] - last_nonstructural_end < 0.75:
                continue
            # Never put two cards over the same spoken window. Structural
            # cards may coexist with one fact card, but each scene is capped
            # at three total cards to keep the frame readable.
            if any(
                candidate_timing[4] < selected_timing[5] - 0.05
                and candidate_timing[5] > selected_timing[4] + 0.05
                for _selected, selected_timing in selected_rows
            ):
                continue
            selected_rows.append((candidate, candidate_timing))
            if len(selected_rows) >= 3:
                break
            if not candidate.structural:
                last_nonstructural_end = candidate_timing[5]
        if not selected_rows:
            continue
        scene_char_start = int(_value(scene, "char_start", 0) or 0)
        scene_token_start = int(_value(scene, "token_start", 0) or 0)
        scene_id = str(_value(scene, "scene_id", _value(scene, "id", f"scene-{index + 1}")))
        container_id = str(_value(scene, "container_id", "general") or "general")
        for selected, timing in selected_rows:
            placement = _placement(
                selected.cue_type,
                media_item,
                captions_enabled=captions_enabled,
                caption_band=caption_band,
                recent_positions=recent_positions,
            )
            if placement is None:
                continue
            word_start, word_end, local_start, local_end, absolute_start, absolute_end, timing_quality = timing
            # Consecutive facts of the same kind should retain their semantic
            # treatment without repeating the same card and entrance motion.
            # The text/timing contract stays untouched; only the compatible
            # renderer template rotates.
            cue_template = selected.template
            variants = cue_template_variants.get(selected.cue_type, ())
            if recent_templates and (cue_template == recent_templates[-1] or (len(recent_templates) >= 2 and cue_template in recent_templates[-2:])) and variants:
                choices = [template for template in variants if template not in recent_templates[-2:]] or [t for t in variants if t != recent_templates[-1]]
                if choices:
                    template_key = stable_id(
                        "cue-template", scene_id, selected.cue_type, variation_seed
                    ).rsplit("_", 1)[-1]
                    cue_template = choices[
                        int(template_key[:8], 16) % len(choices)
                    ]
            selected_for_props = replace(selected, template=cue_template)
            props = _props(
                selected_for_props,
                placement,
                variation_seed=variation_seed,
                art_direction=art_direction,
            )
            if word_start >= 0 and word_end > word_start:
                aligned_cues = []
                for word in words:
                    if word_start <= word.index < word_end:
                        aligned_cues.append({
                            "index": word.index,
                            "text": word.text,
                            "start": round(max(0.0, word.start - absolute_start), 3),
                            "end": round(max(0.0, word.end - absolute_start), 3),
                            "confidence": round(word.confidence, 3),
                        })
                if aligned_cues:
                    props["_word_cues"] = aligned_cues
                    props["_word_highlight_mode"] = "whisper"
            cue = EditorialCueV2(
                cue_id=stable_id(
                    "cue", scene_id, selected.cue_type,
                    scene_char_start + selected.local_char_start,
                    scene_char_start + selected.local_char_end,
                ),
                scene_id=scene_id,
                container_id=container_id,
                cue_type=selected.cue_type,
                text=selected.text,
                char_start=scene_char_start + selected.local_char_start,
                char_end=scene_char_start + selected.local_char_end,
                token_start=scene_token_start + selected.local_token_start,
                token_end=scene_token_start + selected.local_token_end,
                word_start_index=None if word_start < 0 else word_start,
                word_end_index=None if word_end < 0 else word_end,
                scene_local_start_seconds=local_start,
                scene_local_end_seconds=local_end,
                absolute_start_seconds=absolute_start,
                absolute_end_seconds=absolute_end,
                timing_quality=timing_quality,
                template=cue_template,
                props=props,
                placement=placement,
                audit_reason=selected.reason,
            )
            result.append(cue)
            if selected.cue_type == "person":
                seen_person_cues.add(canonical_token(selected.text))
            recent_positions.append(str(placement.get("position") or ""))
            recent_templates.append(cue_template)
            if not selected.structural:
                last_nonstructural_end = max(last_nonstructural_end, absolute_end)

    return tuple(result)


def cue_to_manifest_dict(cue: EditorialCueV2) -> Dict[str, Any]:
    """Render-friendly aliases while preserving the canonical serialized cue."""
    row = cue.to_dict()
    row.update({
        "schema_version": 2,
        "scene_local_start": cue.scene_local_start_seconds,
        "scene_local_end": cue.scene_local_end_seconds,
        "absolute_start": cue.absolute_start_seconds,
        "absolute_end": cue.absolute_end_seconds,
    })
    return row


__all__ = ["cue_to_manifest_dict", "plan_editorial_cues"]
