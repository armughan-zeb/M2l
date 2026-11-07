"""Monotonic script-token to Whisper-word alignment for Storyboard V2."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .models import (
    ContractValidationError,
    SceneContractV1,
    ScriptPlanV1,
    WordTimingV1,
)
from .text import SourceToken, canonical_token, tokenize_source


DELETE_COST = 1.0
INSERT_COST = 1.0
SUBSTITUTE_COST = 1.25


@dataclass(frozen=True)
class TokenAlignmentV1:
    source_tokens: Tuple[SourceToken, ...]
    words: Tuple[WordTimingV1, ...]
    source_to_word: Tuple[Optional[int], ...]
    token_scores: Tuple[float, ...]
    matched_ratio: float
    confidence: float


@dataclass(frozen=True)
class AlignmentResultV1:
    scenes: Tuple[SceneContractV1, ...]
    words: Tuple[WordTimingV1, ...]
    token_alignment: TokenAlignmentV1
    audio_duration: float


def coerce_word_timings(words: Iterable[WordTimingV1 | Mapping[str, Any]]) -> Tuple[WordTimingV1, ...]:
    result: List[WordTimingV1] = []
    last_start = 0.0
    for raw in words:
        if isinstance(raw, WordTimingV1):
            item = WordTimingV1(
                index=len(result),
                text=raw.text,
                start_seconds=raw.start_seconds,
                end_seconds=raw.end_seconds,
                confidence=raw.confidence,
            )
        else:
            item = WordTimingV1.from_mapping(len(result), raw)
            item = WordTimingV1(
                index=len(result),
                text=item.text,
                start_seconds=item.start_seconds,
                end_seconds=item.end_seconds,
                confidence=item.confidence,
            )
        if item.start_seconds + 1e-6 < last_start:
            raise ContractValidationError("Whisper words must be in monotonic time order")
        last_start = item.start_seconds
        result.append(item)
    if not result:
        raise ContractValidationError("At least one Whisper word is required for alignment")
    return tuple(result)


@lru_cache(maxsize=32768)
def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left[0] != right[0] or abs(len(left) - len(right)) > max(2, len(left) // 2):
        return 0.0
    if min(len(left), len(right)) >= 4 and (left in right or right in left):
        return 0.84
    ratio = SequenceMatcher(None, left, right, autojunk=False).ratio()
    return ratio if ratio >= 0.68 else 0.0


def _diagonal_cost(left: str, right: str) -> float:
    score = _similarity(left, right)
    if score >= 0.999:
        return 0.0
    if score:
        return max(0.2, 1.0 - score)
    return SUBSTITUTE_COST


def _banded_directions(source: Sequence[str], transcript: Sequence[str]) -> List[Dict[int, int]]:
    """Needleman-Wunsch in a proportional band; returns backtrace directions.

    Direction values are 1=diagonal, 2=delete source token, 3=insert
    transcript word.  The band expands automatically if the end cell is not
    reachable, preserving deterministic behavior for unusually noisy audio.
    """

    n, m = len(source), len(transcript)
    if not n or not m:
        return [{} for _ in range(n + 1)]
    initial_band = max(64, abs(n - m) + 32, math.ceil(max(n, m) * 0.08))
    band = min(max(n, m), initial_band)
    while True:
        directions: List[Dict[int, int]] = [dict() for _ in range(n + 1)]
        previous: Dict[int, float] = {
            j: j * INSERT_COST for j in range(0, min(m, band) + 1)
        }
        for j in range(1, min(m, band) + 1):
            directions[0][j] = 3

        for i in range(1, n + 1):
            expected = round(i * m / n)
            lo = max(0, expected - band)
            hi = min(m, expected + band)
            if i <= band:
                lo = 0
            if n - i <= band:
                hi = m
            current: Dict[int, float] = {}
            if lo == 0:
                current[0] = i * DELETE_COST
                directions[i][0] = 2
            for j in range(max(1, lo), hi + 1):
                candidates: List[Tuple[float, int]] = []
                if j - 1 in previous:
                    candidates.append(
                        (
                            previous[j - 1] + _diagonal_cost(source[i - 1], transcript[j - 1]),
                            1,
                        )
                    )
                if j in previous:
                    candidates.append((previous[j] + DELETE_COST, 2))
                if j - 1 in current:
                    candidates.append((current[j - 1] + INSERT_COST, 3))
                if not candidates:
                    continue
                # Diagonal wins exact ties, then deletion, making repeated-word
                # alignment stable and monotonic across runs.
                cost, direction = min(candidates, key=lambda item: (item[0], item[1]))
                current[j] = cost
                directions[i][j] = direction
            previous = current

        if m in previous:
            return directions
        if band >= max(n, m):
            raise ContractValidationError("Unable to construct a monotonic token alignment")
        band = min(max(n, m), band * 2)


def _align_segment(
    source: Sequence[str], transcript: Sequence[str]
) -> Tuple[List[Optional[int]], List[float]]:
    mapping: List[Optional[int]] = [None] * len(source)
    scores: List[float] = [0.0] * len(source)
    if not source or not transcript:
        return mapping, scores
    directions = _banded_directions(source, transcript)
    i, j = len(source), len(transcript)
    while i > 0 or j > 0:
        direction = directions[i].get(j)
        if direction == 1:
            mapping[i - 1] = j - 1
            scores[i - 1] = _similarity(source[i - 1], transcript[j - 1])
            i -= 1
            j -= 1
        elif direction == 2:
            i -= 1
        elif direction == 3:
            j -= 1
        else:
            if i:
                i -= 1
            elif j:
                j -= 1
    return mapping, scores


def align_script_tokens(
    source_script: str,
    words: Iterable[WordTimingV1 | Mapping[str, Any]],
) -> TokenAlignmentV1:
    source_tokens = tokenize_source(source_script)
    if not source_tokens:
        raise ContractValidationError("Script contains no alignable tokens")
    all_words = coerce_word_timings(words)
    word_rows = [
        (item.index, canonical_token(item.text))
        for item in all_words
        if canonical_token(item.text)
    ]
    if not word_rows:
        raise ContractValidationError("Whisper output contains no alignable words")
    transcript_indexes = [item[0] for item in word_rows]
    source_values = [item.normalized for item in source_tokens]
    transcript_values = [item[1] for item in word_rows]
    mapping: List[Optional[int]] = [None] * len(source_tokens)
    scores: List[float] = [0.0] * len(source_tokens)

    if source_values == transcript_values:
        for index, transcript_index in enumerate(transcript_indexes):
            mapping[index] = transcript_index
            scores[index] = 1.0
    else:
        # Exact matching blocks make the common long-form case close to linear;
        # dynamic programming is confined to the noisy gaps between anchors.
        matcher = SequenceMatcher(
            None,
            source_values,
            transcript_values,
            autojunk=max(len(source_values), len(transcript_values)) >= 500,
        )
        source_cursor = 0
        transcript_cursor = 0
        for block in matcher.get_matching_blocks():
            local_mapping, local_scores = _align_segment(
                source_values[source_cursor : block.a],
                transcript_values[transcript_cursor : block.b],
            )
            for offset, local_word_index in enumerate(local_mapping):
                source_index = source_cursor + offset
                if local_word_index is not None:
                    mapping[source_index] = transcript_indexes[
                        transcript_cursor + local_word_index
                    ]
                    scores[source_index] = local_scores[offset]
            for offset in range(block.size):
                source_index = block.a + offset
                mapping[source_index] = transcript_indexes[block.b + offset]
                scores[source_index] = 1.0
            source_cursor = block.a + block.size
            transcript_cursor = block.b + block.size

    strong = [score for score in scores if score >= 0.75]
    matched_ratio = len(strong) / len(source_tokens)
    mean_score = sum(strong) / len(strong) if strong else 0.0
    confidence = max(0.0, min(1.0, 0.75 * matched_ratio + 0.25 * mean_score))
    return TokenAlignmentV1(
        source_tokens=source_tokens,
        words=all_words,
        source_to_word=tuple(mapping),
        token_scores=tuple(scores),
        matched_ratio=matched_ratio,
        confidence=confidence,
    )


def _scene_alignment_data(
    scene: SceneContractV1,
    alignment: TokenAlignmentV1,
) -> Tuple[List[int], float, float]:
    token_count = max(1, scene.token_end - scene.token_start)
    word_indexes: List[int] = []
    strong_scores: List[float] = []
    for token_index in range(scene.token_start, min(scene.token_end, len(alignment.source_tokens))):
        word_index = alignment.source_to_word[token_index]
        score = alignment.token_scores[token_index]
        if word_index is not None and score >= 0.45:
            word_indexes.append(word_index)
        if word_index is not None and score >= 0.75:
            strong_scores.append(score)
    coverage = len(strong_scores) / token_count
    mean_score = sum(strong_scores) / len(strong_scores) if strong_scores else 0.0
    confidence = max(0.0, min(1.0, 0.7 * coverage + 0.3 * mean_score))
    return sorted(set(word_indexes)), coverage, confidence


def _fill_timing_boundaries(
    scenes: Sequence[SceneContractV1],
    scene_words: Sequence[Sequence[int]],
    words: Sequence[WordTimingV1],
    audio_duration: float,
) -> List[float]:
    count = len(scenes)
    boundaries: List[Optional[float]] = [None] * (count + 1)
    boundaries[0] = 0.0
    boundaries[-1] = audio_duration
    for index in range(1, count):
        current_words = scene_words[index]
        previous_words = scene_words[index - 1]
        if current_words:
            boundaries[index] = words[current_words[0]].start_seconds
        elif previous_words:
            boundaries[index] = words[previous_words[-1]].end_seconds

    # Discard contradictory anchors.  The token mapping is monotonic, but noisy
    # timestamps can still overlap or collapse to the same instant.
    last_value = 0.0
    for index in range(1, count):
        value = boundaries[index]
        if value is None:
            continue
        value = max(0.0, min(audio_duration, float(value)))
        if value <= last_value + 1e-4:
            boundaries[index] = None
        else:
            boundaries[index] = value
            last_value = value

    known = [index for index, value in enumerate(boundaries) if value is not None]
    for left_index, right_index in zip(known, known[1:]):
        left_time = float(boundaries[left_index] or 0.0)
        right_time = float(boundaries[right_index] or audio_duration)
        missing = right_index - left_index
        if missing <= 1:
            continue
        weights = [
            max(1, scenes[index].token_end - scenes[index].token_start)
            for index in range(left_index, right_index)
        ]
        total = float(sum(weights))
        elapsed = 0.0
        for offset in range(1, missing):
            elapsed += weights[offset - 1]
            boundaries[left_index + offset] = left_time + (right_time - left_time) * elapsed / total

    resolved = [float(value if value is not None else 0.0) for value in boundaries]
    # Guarantee positive scene intervals even for duplicate/zero-duration word
    # timestamps.  If necessary, fall back to token-proportional allocation.
    if any(resolved[index + 1] <= resolved[index] for index in range(count)):
        weights = [max(1, scene.token_end - scene.token_start) for scene in scenes]
        total = float(sum(weights))
        resolved = [0.0]
        elapsed = 0.0
        for weight in weights:
            elapsed += weight
            resolved.append(audio_duration * elapsed / total)
    return resolved


def align_scenes_to_words(
    script_plan: ScriptPlanV1,
    words: Iterable[WordTimingV1 | Mapping[str, Any]],
    *,
    audio_duration: Optional[float] = None,
) -> AlignmentResultV1:
    """Return timed copies of the immutable scenes while retaining stable IDs."""

    alignment = align_script_tokens(script_plan.source_script, words)
    resolved_duration = float(
        audio_duration
        if audio_duration is not None
        else max(item.end_seconds for item in alignment.words)
    )
    if resolved_duration <= 0:
        raise ContractValidationError("audio_duration must be positive")
    if resolved_duration + 1e-6 < max(item.end_seconds for item in alignment.words):
        raise ContractValidationError("audio_duration ends before the final Whisper word")

    per_scene = [_scene_alignment_data(scene, alignment) for scene in script_plan.scenes]
    scene_word_indexes = [item[0] for item in per_scene]
    boundaries = _fill_timing_boundaries(
        script_plan.scenes,
        scene_word_indexes,
        alignment.words,
        resolved_duration,
    )

    timed: List[SceneContractV1] = []
    for index, scene in enumerate(script_plan.scenes):
        word_indexes, coverage, confidence = per_scene[index]
        quality = "exact" if word_indexes and coverage >= 0.60 and confidence >= 0.72 else "estimated"
        timed.append(
            scene.with_timing(
                word_start_index=word_indexes[0] if word_indexes else None,
                word_end_index=(word_indexes[-1] + 1) if word_indexes else None,
                start_seconds=boundaries[index],
                end_seconds=boundaries[index + 1],
                timing_quality=quality,
                alignment_confidence=confidence,
            )
        )
    return AlignmentResultV1(
        scenes=tuple(timed),
        words=alignment.words,
        token_alignment=alignment,
        audio_duration=resolved_duration,
    )


def align_script_plan_to_words(
    script_plan: ScriptPlanV1,
    words: Iterable[WordTimingV1 | Mapping[str, Any]],
    *,
    audio_duration: Optional[float] = None,
) -> Tuple[ScriptPlanV1, AlignmentResultV1]:
    result = align_scenes_to_words(script_plan, words, audio_duration=audio_duration)
    return script_plan.with_scenes(result.scenes), result
