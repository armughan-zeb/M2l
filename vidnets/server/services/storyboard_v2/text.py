"""Text normalization and exact-offset token helpers for Storyboard V2."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Dict, Iterable, List, Sequence, Tuple


# Unicode-aware enough for narration scripts without requiring a third-party
# tokenizer.  Internal apostrophes and hyphens remain part of the source token.
TOKEN_RE = re.compile(r"[^\W_]+(?:[’'][^\W_]+)*(?:-[^\W_]+)*", re.UNICODE)


def mask_sentence_fact_punctuation(source: str) -> str:
    """Hide fact-internal periods without changing any exact source offsets."""
    pattern = (r"\b[apAP]\.\s*[mM]\.(?=\s*[,;:]|\s+[a-z])|"
               r"\b[apAP]\.(?=\s*[mM]\.)|"
               r"\b(?:[A-Za-z]\.\s*){2,}(?=[A-Z][a-z])|"
               r"\b[A-Z]\.(?=\s+[A-Z][a-z])|"
               r"\b(?:Mr|Mrs|Ms|Dr|Prof|St|Sr|Jr)\.(?=\s+\w)|"
               r"(?<=\d)\.(?=\d)")
    return re.sub(pattern, lambda match: match.group(0).replace('.', '\ue000'), source)


_CARDINALS: Dict[str, str] = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
}

_ORDINALS: Dict[str, str] = {
    "first": "1",
    "second": "2",
    "third": "3",
    "fourth": "4",
    "fifth": "5",
    "sixth": "6",
    "seventh": "7",
    "eighth": "8",
    "ninth": "9",
    "tenth": "10",
    "eleventh": "11",
    "twelfth": "12",
    "thirteenth": "13",
    "fourteenth": "14",
    "fifteenth": "15",
    "sixteenth": "16",
    "seventeenth": "17",
    "eighteenth": "18",
    "nineteenth": "19",
    "twentieth": "20",
}

_ABBREVIATIONS: Dict[str, str] = {
    "dr": "doctor",
    "mr": "mister",
    "mrs": "missus",
    "ms": "miss",
    "prof": "professor",
    "dept": "department",
    "approx": "approximately",
    "vs": "versus",
}


@dataclass(frozen=True)
class SourceToken:
    index: int
    text: str
    normalized: str
    char_start: int
    char_end: int


def normalize_script_with_map(source: str) -> Tuple[str, Tuple[int, ...]]:
    """Normalize line endings/NBSP while retaining every original boundary.

    The returned map has ``len(normalized) + 1`` entries and translates any
    normalized half-open span directly back to source offsets.  We intentionally
    do not collapse ordinary whitespace: doing so would destroy exact grounding.
    """

    normalized: List[str] = []
    boundary_map: List[int] = [0]
    cursor = 0
    while cursor < len(source):
        char = source[cursor]
        if char == "\r":
            if cursor + 1 < len(source) and source[cursor + 1] == "\n":
                cursor += 2
            else:
                cursor += 1
            normalized.append("\n")
            boundary_map.append(cursor)
            continue
        normalized.append(" " if char == "\u00a0" else char)
        cursor += 1
        boundary_map.append(cursor)
    return "".join(normalized), tuple(boundary_map)


def canonical_token(value: str) -> str:
    """Normalize one script/transcript token for monotonic alignment."""

    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("’", "'")
    text = re.sub(r"^[^\w]+|[^\w]+$", "", text, flags=re.UNICODE)
    text = text.replace("_", "")
    if not text:
        return ""
    if text in _CARDINALS:
        return _CARDINALS[text]
    if text in _ORDINALS:
        return _ORDINALS[text]
    ordinal_match = re.fullmatch(r"0*(\d+)(?:st|nd|rd|th)", text)
    if ordinal_match:
        return str(int(ordinal_match.group(1)))
    number_match = re.fullmatch(r"0*(\d+)", text)
    if number_match:
        return str(int(number_match.group(1)))
    if text in _ABBREVIATIONS:
        return _ABBREVIATIONS[text]
    # Treat curly/straight apostrophe variants and hyphenation consistently.
    return re.sub(r"[-']", "", text)


def tokenize_source(source: str) -> Tuple[SourceToken, ...]:
    result: List[SourceToken] = []
    for index, match in enumerate(TOKEN_RE.finditer(source)):
        result.append(
            SourceToken(
                index=index,
                text=match.group(0),
                normalized=canonical_token(match.group(0)),
                char_start=match.start(),
                char_end=match.end(),
            )
        )
    return tuple(result)


def token_range_for_char_span(
    tokens: Sequence[SourceToken], char_start: int, char_end: int
) -> Tuple[int, int]:
    """Return the half-open token range intersecting a character span."""

    indexes = [
        token.index
        for token in tokens
        if token.char_end > char_start and token.char_start < char_end
    ]
    if indexes:
        return indexes[0], indexes[-1] + 1
    # Empty/whitespace span: pin the range to the next token boundary.
    next_index = next(
        (token.index for token in tokens if token.char_start >= char_end),
        len(tokens),
    )
    return next_index, next_index


def line_number_at(source: str, offset: int) -> int:
    return source.count("\n", 0, max(0, min(len(source), offset))) + 1


def meaningful_tokens(values: Iterable[str]) -> Tuple[str, ...]:
    return tuple(token for token in (canonical_token(item) for item in values) if token)

