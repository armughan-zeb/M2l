"""Deterministic, exact-span script preflight for Storyboard V2."""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
import hashlib
import re
from typing import Iterable, List, Optional, Sequence, Tuple

from .models import (
    ContainerV1,
    ContractValidationError,
    ScriptPlanV1,
    SUPPORTED_FORMATS,
    stable_id,
)
from .text import line_number_at, normalize_script_with_map, mask_sentence_fact_punctuation


_NUMBER_WORD = (
    r"(?:\d{1,3}(?:st|nd|rd|th)?|zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|"
    r"thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|nineteenth|twentieth)"
)

_LIST_LINE_RE = re.compile(
    rf"(?im)^[ \t]*(?:"
    rf"(?:number|no\.?|#)\s*(?P<n1>{_NUMBER_WORD})\s*(?:[:.)\-–—]\s*|\bis\s+|\bgoes\s+to\s+)?|"
    rf"(?P<n2>\d{{1,3}}(?:st|nd|rd|th)?)[.)]\s+|"
    rf"(?P<n3>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s*"
    rf"(?:[:.)\-–—]\s+|,\s+)"
    rf")"
)

_LIST_PHRASE_RE = re.compile(
    rf"(?i)(?:\bat\s+)?\b(?:number|no\.?)\s*(?P<n1>{_NUMBER_WORD})\s+"
    rf"(?:is\s+|goes\s+to\s+)?|"
    rf"\b(?:taking|claiming|in|coming\s+in\s+at)\s+(?:the\s+)?"
    rf"(?P<n2>{_NUMBER_WORD})\s+(?:spot|place)\s*(?:is\s+|goes\s+to\s+)?"
)

_LIST_INLINE_RE = re.compile(
    rf"(?i)(?:\b(?:number|no\.?)\s*(?P<n1>{_NUMBER_WORD})|"
    rf"(?<!\w)#\s*(?P<n2>\d{{1,3}})|"
    rf"(?<!\w)(?P<n3>\d{{1,3}}(?:st|nd|rd|th)?)[.)])\s*"
    rf"(?:[:.)\-–—]\s*|\bis\s+|\bgoes\s+to\s+)"
)

_STEP_RE = re.compile(
    rf"(?im)^[ \t]*(?:step|stage|phase)\s+(?P<n1>{_NUMBER_WORD})\s*"
    rf"(?:[:.)\-–—]\s*|\bis\s+)?"
)

_QUESTION_RE = re.compile(
    r"(?im)^[ \t]*(?:q(?:uestion)?(?:\s*(?P<number>\d+))?)\s*[:.)\-–—]\s*"
)

_ANSWER_RE = re.compile(r"(?im)^[ \t]*(?:a(?:nswer)?)\s*[:.)\-–—]\s*")

_DIALOGUE_RE = re.compile(
    r"(?m)^[ \t]*(?P<speaker>[A-Z][A-Za-zÀ-ÖØ-öø-ÿ0-9 _'’\-]{0,32})\s*:\s*"
)

_TIMELINE_RE = re.compile(
    r"(?im)^[ \t]*(?:(?:in\s+)?(?P<year>(?:1[5-9]\d{2}|20\d{2}|21\d{2}))|"
    r"(?P<timestamp>(?:\d{1,2}:){1,2}\d{2}))\s*(?:[:.\-–—,]\s*|\s+)"
)

_MARKDOWN_HEADING_RE = re.compile(
    r"(?m)^[ \t]{0,3}#{1,6}[ \t]+(?P<heading>[^\n#][^\n]*?)[ \t]*#*[ \t]*(?:\n|$)"
)

_CHAPTER_RE = re.compile(
    rf"(?im)^[ \t]*(?P<label>chapter|part|section)\s+"
    rf"(?P<number>{_NUMBER_WORD})\s*(?:[:.)\-–—]\s*|\s+)"
    rf"(?P<heading>[^\n]*)"
)

_OUTRO_RE = re.compile(
    r"(?im)^[ \t]*(?P<label>conclusion|in conclusion|outro|final thoughts?|closing thoughts?)\s*"
    r"(?:[:.\-–—]\s*|\n)"
)

_HONORABLE_RE = re.compile(
    r"(?im)^[ \t]*(?P<label>honou?rable mentions?)\s*(?:[:.\-–—]\s*|\n)"
)

_ORDINAL_VALUES = {
    "zero": 0,
    "one": 1,
    "first": 1,
    "two": 2,
    "second": 2,
    "three": 3,
    "third": 3,
    "four": 4,
    "fourth": 4,
    "five": 5,
    "fifth": 5,
    "six": 6,
    "sixth": 6,
    "seven": 7,
    "seventh": 7,
    "eight": 8,
    "eighth": 8,
    "nine": 9,
    "ninth": 9,
    "ten": 10,
    "tenth": 10,
    "eleven": 11,
    "eleventh": 11,
    "twelve": 12,
    "twelfth": 12,
    "thirteen": 13,
    "thirteenth": 13,
    "fourteen": 14,
    "fourteenth": 14,
    "fifteen": 15,
    "fifteenth": 15,
    "sixteen": 16,
    "sixteenth": 16,
    "seventeen": 17,
    "seventeenth": 17,
    "eighteen": 18,
    "eighteenth": 18,
    "nineteen": 19,
    "nineteenth": 19,
    "twenty": 20,
    "twentieth": 20,
}


@dataclass(frozen=True)
class _Marker:
    start: int
    end: int
    kind: str
    ordinal: Optional[int]
    subject_hint: str = ""
    explicit: bool = True


def _ordinal_value(value: str | None) -> Optional[int]:
    value = str(value or "").strip().lower()
    if not value:
        return None
    if value in _ORDINAL_VALUES:
        return _ORDINAL_VALUES[value]
    match = re.fullmatch(r"0*(\d+)(?:st|nd|rd|th)?", value)
    return int(match.group(1)) if match else None


def _non_overlapping(markers: Iterable[_Marker]) -> List[_Marker]:
    result: List[_Marker] = []
    for marker in sorted(markers, key=lambda item: (item.start, -(item.end - item.start))):
        if result and marker.start < result[-1].end:
            continue
        if result and marker.start == result[-1].start:
            continue
        result.append(marker)
    return result


def _list_markers(script: str) -> List[_Marker]:
    markers: List[_Marker] = []
    for match in _LIST_LINE_RE.finditer(script):
        raw = match.group("n1") or match.group("n2") or match.group("n3")
        markers.append(_Marker(match.start(), match.end(), "list_item", _ordinal_value(raw)))
    for match in _LIST_INLINE_RE.finditer(script):
        raw = match.group("n1") or match.group("n2") or match.group("n3")
        markers.append(_Marker(match.start(), match.end(), "list_item", _ordinal_value(raw)))
    for match in _LIST_PHRASE_RE.finditer(script):
        raw = match.group("n1") or match.group("n2")
        markers.append(_Marker(match.start(), match.end(), "list_item", _ordinal_value(raw)))
    return _non_overlapping(markers)


def _step_markers(script: str) -> List[_Marker]:
    return [
        _Marker(match.start(), match.end(), "step", _ordinal_value(match.group("n1")))
        for match in _STEP_RE.finditer(script)
    ]


def _question_markers(script: str) -> List[_Marker]:
    return [
        _Marker(
            match.start(),
            match.end(),
            "q_and_a",
            int(match.group("number")) if match.group("number") else index + 1,
        )
        for index, match in enumerate(_QUESTION_RE.finditer(script))
    ]


def _dialogue_markers(script: str) -> List[_Marker]:
    result: List[_Marker] = []
    for index, match in enumerate(_DIALOGUE_RE.finditer(script)):
        speaker = re.sub(r"\s+", " ", match.group("speaker")).strip()
        if speaker.casefold() in {"q", "question", "a", "answer", "step", "number"}:
            continue
        result.append(
            _Marker(match.start(), match.end(), "dialogue_turn", index + 1, speaker)
        )
    return result


def _timeline_markers(script: str) -> List[_Marker]:
    result: List[_Marker] = []
    for index, match in enumerate(_TIMELINE_RE.finditer(script)):
        label = match.group("year") or match.group("timestamp") or ""
        result.append(_Marker(match.start(), match.end(), "event", index + 1, label))
    return result


def _heading_markers(script: str) -> List[_Marker]:
    result: List[_Marker] = []
    for index, match in enumerate(_MARKDOWN_HEADING_RE.finditer(script)):
        heading = re.sub(r"\s+", " ", match.group("heading")).strip(" #:–—-")
        result.append(_Marker(match.start(), match.end(), "section", index + 1, heading))

    for marker in _chapter_markers(script):
        result.append(
            _Marker(
                marker.start,
                marker.end,
                "section",
                marker.ordinal,
                marker.subject_hint,
                True,
            )
        )

    # A short standalone line surrounded by a blank line is also a heading.
    line_re = re.compile(r"(?m)^(?P<line>[^\n]{2,70})\n")
    for match in line_re.finditer(script):
        if match.start() > 0 and not script[: match.start()].endswith("\n\n"):
            continue
        if match.end() >= len(script) or not script[match.end() :].startswith("\n"):
            continue
        line = match.group("line").strip()
        if not line or re.search(r"[.!?]$", line) or len(line.split()) > 9:
            continue
        if _LIST_LINE_RE.match(line) or _STEP_RE.match(line):
            continue
        result.append(_Marker(match.start(), match.end(), "section", None, line.strip(" #:–—-")))
    return _non_overlapping(result)


def _chapter_markers(script: str) -> List[_Marker]:
    result: List[_Marker] = []
    for match in _CHAPTER_RE.finditer(script):
        heading = _clean_subject(match.group("heading")) or match.group("label").title()
        result.append(
            _Marker(
                match.start(),
                match.end(),
                "chapter",
                _ordinal_value(match.group("number")),
                heading,
                True,
            )
        )
    return result


def detect_script_format(
    script: str,
    *,
    format_hint: str = "auto",
    content_title: str = "",
) -> Tuple[str, float]:
    """Classify supported structure using only evidence in the supplied text."""

    hint = str(format_hint or "auto").strip().lower()
    if hint not in SUPPORTED_FORMATS:
        raise ContractValidationError(f"Unsupported format_hint: {hint}")
    if hint != "auto":
        return hint, 1.0

    questions = _question_markers(script)
    answers = list(_ANSWER_RE.finditer(script))
    if questions and answers:
        return "q_and_a", 0.99 if len(questions) >= 2 else 0.92

    steps = _step_markers(script)
    if len(steps) >= 2 or (steps and re.search(r"(?i)\bhow\s+to\b", content_title)):
        return "how_to", 0.99 if len(steps) >= 2 else 0.88

    list_items = _list_markers(script)
    if len(list_items) >= 2:
        return "listicle", 0.99

    timeline = _timeline_markers(script)
    if len(timeline) >= 2:
        return "timeline", 0.97

    dialogue = _dialogue_markers(script)
    speakers = {item.subject_hint.casefold() for item in dialogue}
    if len(dialogue) >= 3 and len(speakers) >= 2:
        return "dialogue", 0.97

    title_and_script = f"{content_title}\n{script[:600]}"
    if re.search(r"(?i)\b(?:vs\.?|versus)\b", title_and_script):
        return "comparison", 0.9

    headings = _heading_markers(script)
    if len(headings) >= 2:
        return "mixed_multi_subject", 0.9

    return "narrative", 0.82


def _clean_subject(value: str, *, max_words: int = 9) -> str:
    text = re.sub(r"^[\s:#.)\-–—]+", "", str(value or ""))
    # Periods inside initials and model numbers are identity, not a sentence.
    marker = "\uE000"
    text = re.sub(r"\b(?:[A-Z]\.\s*){1,4}(?=[A-Z][a-z])", lambda match: match.group(0).replace(".", marker), text)
    text = re.sub(r"(?<=\d)\.(?=\d)", marker, text)
    text = re.split(r"[\n.!?;]", text, maxsplit=1)[0].replace(marker, ".")
    text = re.split(
        r"(?i)\s+(?:is|are|was|were|has|have|can|could|will|offers?|provides?|works?|uses?)\s+",
        text,
        maxsplit=1,
    )[0]
    text = re.sub(r"(?i)^(?:the\s+)?(?:entry|choice|pick)\s+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" \t\"'“”‘’:#,.-–—")
    words = text.split()
    return " ".join(words[:max_words]).strip()


def _subject_after_marker(script: str, marker: _Marker, interval_end: int) -> str:
    if marker.subject_hint:
        return _clean_subject(marker.subject_hint)
    value = script[marker.end : min(interval_end, marker.end + 220)]
    if marker.kind == "q_and_a":
        question = re.split(r"[\n?]", value, maxsplit=1)[0]
        question = re.sub(r"\s+", " ", question).strip(" \t\"'“”‘’:#,.-–—")
        return " ".join(question.split()[:12])
    return _clean_subject(value)


def _outro_markers(script: str, *, after: int) -> List[_Marker]:
    result: List[_Marker] = []
    for match in _HONORABLE_RE.finditer(script):
        if match.start() > after:
            result.append(_Marker(match.start(), match.end(), "honorable_mention", None, match.group("label")))
    for match in _OUTRO_RE.finditer(script):
        if match.start() > after:
            result.append(_Marker(match.start(), match.end(), "outro", None, match.group("label")))
    return _non_overlapping(result)


def _extract_comparison_subjects(script: str, title: str) -> Tuple[str, str]:
    for source in (title, *script.splitlines()[:3], script[:240]):
        match = re.search(r"(?i)\b(?:vs\.?|versus)\b", source)
        if not match:
            continue
        left = re.split(r"[:.!?]", source[: match.start()])[-1]
        right = re.split(r"[:.!?]", source[match.end() :])[0]
        left_words = re.findall(r"[\w&+'’\-]+", left, flags=re.UNICODE)[-6:]
        right_words = re.findall(r"[\w&+'’\-]+", right, flags=re.UNICODE)[:6]
        stop = {"which", "who", "what", "why", "how", "the", "a", "an", "comparison"}
        while left_words and left_words[0].casefold() in stop:
            left_words.pop(0)
        while right_words and right_words[-1].casefold() in stop:
            right_words.pop()
        if left_words and right_words:
            return " ".join(left_words), " ".join(right_words)
    return "Left subject", "Right subject"


def _sentence_spans(script: str) -> List[Tuple[int, int]]:
    if not script:
        return []
    masked = mask_sentence_fact_punctuation(script)
    cuts = [match.end() for match in re.finditer(r"(?:[.!?]+[\"'”’)]*|\n+)(?:[ \t]+|(?=\S)|$)", masked)]
    result: List[Tuple[int, int]] = []
    cursor = 0
    for cut in cuts:
        if cut > cursor:
            result.append((cursor, cut))
            cursor = cut
    if cursor < len(script):
        result.append((cursor, len(script)))
    return result or [(0, len(script))]


def _comparison_markers(script: str, title: str) -> List[_Marker]:
    left, right = _extract_comparison_subjects(script, title)
    left_key = left.casefold()
    right_key = right.casefold()
    provisional: List[_Marker] = []
    for start, end in _sentence_spans(script):
        sentence = script[start:end].casefold()
        has_left = left_key in sentence
        has_right = right_key in sentence
        if has_left and not has_right:
            kind, subject = "comparison_left", left
        elif has_right and not has_left:
            kind, subject = "comparison_right", right
        else:
            kind, subject = "comparison_shared", f"{left} vs {right}"
        if provisional and provisional[-1].kind == kind:
            continue
        provisional.append(_Marker(start, start, kind, None, subject, False))
    return provisional or [_Marker(0, 0, "comparison_shared", None, f"{left} vs {right}", False)]


def _make_root(script: str, script_hash: str, detected_format: str, title: str) -> ContainerV1:
    return ContainerV1(
        container_id=stable_id("ctr", script_hash, 0, len(script), "root"),
        kind="root",
        ordinal=None,
        canonical_subject=_clean_subject(title) or detected_format.replace("_", " "),
        char_start=0,
        char_end=len(script),
        text=script,
        explicit_marker=False,
        source_line_start=1,
        source_line_end=line_number_at(script, len(script)),
        is_leaf=False,
        depth=0,
    )


def _markers_to_containers(
    script: str,
    *,
    script_hash: str,
    detected_format: str,
    markers: Sequence[_Marker],
    content_title: str,
) -> Tuple[ContainerV1, ...]:
    root = _make_root(script, script_hash, detected_format, content_title)
    ordered = _non_overlapping(markers)
    if not ordered:
        leaf = ContainerV1(
            container_id=stable_id("ctr", script_hash, 0, len(script), detected_format),
            kind="narrative" if detected_format == "narrative" else detected_format,
            ordinal=None,
            canonical_subject=_clean_subject(content_title),
            char_start=0,
            char_end=len(script),
            text=script,
            parent_container_id=root.container_id,
            explicit_marker=False,
            source_line_start=1,
            source_line_end=line_number_at(script, len(script)),
            is_leaf=True,
            depth=1,
        )
        return root, leaf

    starts: List[Tuple[int, Optional[_Marker]]] = []
    first = ordered[0]
    prefix = script[: first.start]
    if prefix.strip():
        starts.append((0, None))
        starts.extend((marker.start, marker) for marker in ordered)
    else:
        starts.append((0, first))
        starts.extend((marker.start, marker) for marker in ordered[1:])

    result: List[ContainerV1] = [root]
    for index, (start, marker) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(script)
        if end <= start:
            continue
        if marker is None:
            kind = "intro"
            ordinal = None
            subject = _clean_subject(content_title)
            marker_start = marker_end = None
            explicit = False
        else:
            kind = marker.kind
            ordinal = marker.ordinal
            subject = _subject_after_marker(script, marker, end)
            marker_start = marker.start if marker.explicit else None
            marker_end = marker.end if marker.explicit else None
            explicit = marker.explicit
        container_id = stable_id("ctr", script_hash, start, end, kind)
        result.append(
            ContainerV1(
                container_id=container_id,
                kind=kind,
                ordinal=ordinal,
                canonical_subject=subject,
                char_start=start,
                char_end=end,
                text=script[start:end],
                parent_container_id=root.container_id,
                marker_start=marker_start,
                marker_end=marker_end,
                explicit_marker=explicit,
                source_line_start=line_number_at(script, start),
                source_line_end=line_number_at(script, end),
                is_leaf=True,
                depth=1,
            )
        )

    return tuple(result)


def _nest_chapter_parents(
    script: str,
    *,
    script_hash: str,
    containers: Sequence[ContainerV1],
    chapters: Sequence[_Marker],
) -> Tuple[ContainerV1, ...]:
    """Add overlapping chapter parents while keeping exact leaf coverage."""

    if not chapters:
        return tuple(containers)
    root = next(item for item in containers if not item.is_leaf and item.kind == "root")
    leaves = sorted(
        (item for item in containers if item.is_leaf),
        key=lambda item: (item.char_start, item.char_end),
    )
    ordered = _non_overlapping(chapters)
    parents: List[ContainerV1] = []
    parent_ranges: List[Tuple[int, int, ContainerV1]] = []
    for index, marker in enumerate(ordered):
        start = marker.start
        if not script[:start].strip():
            start = 0
        end = ordered[index + 1].start if index + 1 < len(ordered) else len(script)
        parent_id = stable_id("ctr", script_hash, start, end, "chapter_parent")
        parent = ContainerV1(
            container_id=parent_id,
            kind="chapter",
            ordinal=marker.ordinal,
            canonical_subject=marker.subject_hint,
            char_start=start,
            char_end=end,
            text=script[start:end],
            parent_container_id=root.container_id,
            marker_start=marker.start,
            marker_end=marker.end,
            explicit_marker=True,
            source_line_start=line_number_at(script, start),
            source_line_end=line_number_at(script, end),
            is_leaf=False,
            depth=1,
        )
        parents.append(parent)
        parent_ranges.append((start, end, parent))

    nested_leaves: List[ContainerV1] = []
    for leaf in leaves:
        parent = next(
            (
                item
                for start, end, item in parent_ranges
                if start <= leaf.char_start and leaf.char_end <= end
            ),
            None,
        )
        if parent is not None:
            leaf = replace(leaf, parent_container_id=parent.container_id, depth=2)
        nested_leaves.append(leaf)
    return tuple([root, *parents, *nested_leaves])


def parse_containers(
    script: str,
    *,
    format_hint: str = "auto",
    content_title: str = "",
) -> Tuple[str, float, Tuple[ContainerV1, ...]]:
    if not isinstance(script, str) or not script.strip():
        raise ContractValidationError("Script must not be empty")
    detected_format, confidence = detect_script_format(
        script,
        format_hint=format_hint,
        content_title=content_title,
    )
    chapters = _chapter_markers(script)
    if detected_format == "listicle":
        markers = _list_markers(script)
    elif detected_format == "how_to":
        markers = _step_markers(script)
    elif detected_format == "timeline":
        markers = _timeline_markers(script)
    elif detected_format == "q_and_a":
        markers = _question_markers(script)
    elif detected_format == "dialogue":
        markers = _dialogue_markers(script)
    elif detected_format == "mixed_multi_subject":
        markers = _heading_markers(script)
    elif detected_format == "comparison":
        markers = _comparison_markers(script, content_title)
    else:
        markers = []

    if chapters:
        chapter_boundaries = [
            _Marker(
                item.start,
                item.end,
                "chapter_intro",
                item.ordinal,
                item.subject_hint,
                True,
            )
            for item in chapters
        ]
        markers = _non_overlapping([*markers, *chapter_boundaries])

    if markers and detected_format in {"listicle", "how_to", "timeline", "mixed_multi_subject"}:
        markers = _non_overlapping(
            [*markers, *_outro_markers(script, after=markers[0].start)]
        )

    script_hash = hashlib.sha256(script.encode("utf-8")).hexdigest()
    containers = _markers_to_containers(
        script,
        script_hash=script_hash,
        detected_format=detected_format,
        markers=markers,
        content_title=content_title,
    )
    containers = _nest_chapter_parents(
        script,
        script_hash=script_hash,
        containers=containers,
        chapters=chapters,
    )
    return detected_format, confidence, containers


@lru_cache(maxsize=128)
def build_script_plan(
    script: str,
    *,
    content_title: str = "",
    format_hint: str = "auto",
    language: str = "auto",
    scene_mode: str = "adaptive",
) -> ScriptPlanV1:
    """Build the canonical pre-TTS plan and validate all span invariants."""

    detected_format, confidence, containers = parse_containers(
        script,
        format_hint=format_hint,
        content_title=content_title,
    )
    normalized, normalization_map = normalize_script_with_map(script)
    script_hash = hashlib.sha256(script.encode("utf-8")).hexdigest()
    from .scene_builder import build_container_safe_scenes

    scenes = build_container_safe_scenes(
        script,
        containers,
        script_hash=script_hash,
        detected_format=detected_format,
        scene_mode=scene_mode,
    )
    return ScriptPlanV1(
        script_hash=script_hash,
        source_script=script,
        normalized_script=normalized,
        normalization_to_original=normalization_map,
        detected_format=detected_format,
        format_confidence=confidence,
        language=str(language or "auto"),
        containers=containers,
        scenes=scenes,
    )
