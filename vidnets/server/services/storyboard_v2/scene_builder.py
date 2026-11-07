"""Container-safe scene construction for Storyboard V2."""

from __future__ import annotations

import math
import re
from typing import Iterable, List, Sequence, Tuple

from .models import ContainerV1, ContractValidationError, SceneContractV1, stable_id
from .text import SourceToken, token_range_for_char_span, tokenize_source, mask_sentence_fact_punctuation
from ..visual_requirement import build_visual_requirement


DEFAULT_WORDS_PER_SECOND = 2.5
DEFAULT_MIN_DURATION = 1.5
DEFAULT_TARGET_MIN = 4.5
DEFAULT_TARGET_MAX = 7.0


# Numeric phrases are a single on-screen fact, not independent typography
# tokens.  A scene boundary between ``100`` and ``million`` caused the
# director to receive two different facts, which in turn produced the broken
# one-word-at-a-time cards seen in production exports.
_NUMERIC_SCALE_WORDS = {
    "hundred", "hundreds", "thousand", "thousands", "million", "millions",
    "billion", "billions", "trillion", "trillions", "percent", "percentage",
}
_NUMBER_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
}


def _is_numeric_phrase_boundary(left: SourceToken, right: SourceToken) -> bool:
    """Return true when a split would tear one spoken numeric fact apart."""
    right_word = str(right.text or "").strip(".,;:!?()[]{}\"'").casefold()
    left_word = str(left.text or "").strip(".,;:!?()[]{}\"'").casefold()
    if right_word not in _NUMERIC_SCALE_WORDS:
        return False
    return bool(
        re.fullmatch(r"[$£€]?\d+(?:[,.]\d+)?%?", left_word)
        or left_word in _NUMBER_WORDS
        or left_word in _NUMERIC_SCALE_WORDS
    )


def _is_decimal_fragment_boundary(source: str, left: SourceToken, right: SourceToken) -> bool:
    """Return true when a cut would split one decimal/thousands number."""
    separator = source[left.char_end : right.char_start]
    return bool(
        left.text.isdigit()
        and right.text.isdigit()
        and separator in {".", ","}
    )


def _leaf_containers(containers: Sequence[ContainerV1]) -> List[ContainerV1]:
    leaves = [item for item in containers if item.is_leaf]
    return sorted(leaves, key=lambda item: (item.char_start, item.char_end))


def _word_count(tokens: Sequence[SourceToken], start: int, end: int) -> int:
    return sum(1 for token in tokens if token.char_end > start and token.char_start < end)


def _split_span_at_words(
    start: int,
    end: int,
    tokens: Sequence[SourceToken],
    max_words: int,
    source: str = "",
) -> List[Tuple[int, int]]:
    inside = [token for token in tokens if token.char_end > start and token.char_start < end]
    if len(inside) <= max_words:
        return [(start, end)]
    cuts: List[int] = []
    for index in range(max_words, len(inside), max_words):
        # Prefer exceeding the nominal word limit by one token over breaking
        # ``100 million``, ``two hundred``, or ``$4.5 billion`` across two
        # scenes.  The exact text span is later used by captions, factual
        # cards, and the media query, so this guard fixes all three together.
        while index < len(inside) and (
            _is_numeric_phrase_boundary(inside[index - 1], inside[index])
            or _is_decimal_fragment_boundary(
                source, inside[index - 1], inside[index]
            )
        ):
            index += 1
        if index >= len(inside):
            break
        cut = inside[index].char_start
        if start < cut < end:
            cuts.append(cut)
    result: List[Tuple[int, int]] = []
    cursor = start
    for cut in cuts:
        result.append((cursor, cut))
        cursor = cut
    result.append((cursor, end))
    return result


def _atomic_spans(
    source: str,
    container: ContainerV1,
    tokens: Sequence[SourceToken],
    *,
    max_words: int,
) -> List[Tuple[int, int]]:
    """Return contiguous beat spans that exactly cover one leaf container."""

    local = source[container.char_start : container.char_end]
    # Keep a narration beat intact. Commas and word-count targets are not scene
    # boundaries: cutting on them created fragments such as ``as crews`` / 
    # ``moved thousands`` and broke Whisper-aligned edits mid-thought. Sentence,
    # semicolon, and explicit em-dash boundaries remain usable beat boundaries.
    separator = re.compile(
        r"(?:[.!?;]+[\"'”’)]*\s*|\n+|\s+[—–]\s+)",
        re.UNICODE,
    )
    raw: List[Tuple[int, int]] = []
    cursor = container.char_start
    for match in separator.finditer(mask_sentence_fact_punctuation(local)):
        matched_text = local[match.start() : match.end()]
        if (
            matched_text.startswith(".")
            and match.start() > 0
            and match.end() < len(local)
            and local[match.start() - 1].isdigit()
            and local[match.end()].isdigit()
        ):
            # The sentence matcher also sees the decimal point in values such
            # as 2.3 as punctuation. Treat it as part of the number so a scene
            # can never begin with the fractional digit.
            continue
        cut = container.char_start + match.end()
        if cut <= cursor:
            continue
        raw.append((cursor, cut))
        cursor = cut
    if cursor < container.char_end:
        raw.append((cursor, container.char_end))
    if not raw:
        raw = [(container.char_start, container.char_end)]

    # max_words is a soft visual-duration target, not permission to split a
    # complete sentence or clause across scenes. Remotion can vary the camera
    # and graphic motion inside a longer beat while its narration stays whole.
    return raw


def _parse_beat_group(scene_mode: str) -> int | None:
    mode = re.sub(r"\s+", " ", str(scene_mode or "adaptive")).strip().lower()
    # The desktop UI exposes this as ``Every Sentence``/``Sentence by
    # Sentence``.  Treat it as a fixed one-atom group so the canonical V2
    # storyboard keeps each sentence intact.  Returning ``None`` here used
    # the adaptive word target instead, which split a sentence across scene
    # boundaries and leaked the previous sentence's year/entity into the next
    # web query.
    if mode in {"adaptive", "auto"}:
        return None
    if mode in {
        "every sentence",
        "sentence",
        "sentence by sentence",
        "every sentences",
        "every beat",
        "beat",
    }:
        return 1
    if re.fullmatch(r"every\s+(?:\d+|one|two|three|four|five)\s+beats?", mode):
        return 1
    # Unknown/old presets still keep the one-beat documentary contract.
    return 1


def _group_fixed(
    atoms: Sequence[Tuple[int, int]],
    group_size: int,
    tokens: Sequence[SourceToken],
    min_words: int,
) -> List[Tuple[int, int]]:
    if group_size <= 1:
        # Every Beat is a hard scene boundary. Do not merge a short final beat
        # back into its predecessor merely to satisfy the average word target.
        return list(atoms)
    result = [
        (atoms[index][0], atoms[min(index + group_size, len(atoms)) - 1][1])
        for index in range(0, len(atoms), group_size)
    ]
    if len(result) > 1 and _word_count(tokens, *result[-1]) < min_words:
        result[-2] = (result[-2][0], result[-1][1])
        result.pop()
    return result


def _group_adaptive(
    atoms: Sequence[Tuple[int, int]],
    tokens: Sequence[SourceToken],
    *,
    target_min_words: int,
    target_max_words: int,
    min_words: int,
) -> List[Tuple[int, int]]:
    result: List[Tuple[int, int]] = []
    cursor = 0
    while cursor < len(atoms):
        start = atoms[cursor][0]
        end = atoms[cursor][1]
        count = _word_count(tokens, start, end)
        cursor += 1
        while cursor < len(atoms):
            next_end = atoms[cursor][1]
            combined = _word_count(tokens, start, next_end)
            if count >= target_min_words and combined > target_max_words:
                break
            end = next_end
            count = combined
            cursor += 1
            if count >= target_min_words:
                # Prefer a sentence/full-stop cut once the readable target is met.
                break
        result.append((start, end))

    if len(result) > 1 and _word_count(tokens, *result[-1]) < min_words:
        result[-2] = (result[-2][0], result[-1][1])
        result.pop()
    return result


def _visual_intent(container: ContainerV1, position_in_container: int) -> str:
    if container.kind == "intro":
        return "topic_overview"
    if container.kind == "outro":
        return "closing_context"
    if container.kind in {"list_item", "honorable_mention", "section"}:
        return "item_identity" if position_in_container == 0 else "item_action_detail"
    if container.kind == "step":
        return "instruction_step"
    if container.kind == "event":
        return "historical_event"
    if container.kind.startswith("comparison_"):
        return container.kind
    if container.kind == "q_and_a":
        return "answer_context"
    if container.kind == "dialogue_turn":
        return "speaker_context"
    return "documentary_context"


def _strict_format(detected_format: str) -> bool:
    return detected_format in {
        "listicle",
        "how_to",
        "comparison",
        "timeline",
        "mixed_multi_subject",
    }


def build_container_safe_scenes(
    source_script: str,
    containers: Sequence[ContainerV1],
    *,
    script_hash: str,
    detected_format: str,
    scene_mode: str = "adaptive",
    words_per_second: float = DEFAULT_WORDS_PER_SECOND,
    min_duration: float = DEFAULT_MIN_DURATION,
    target_min_duration: float = DEFAULT_TARGET_MIN,
    target_max_duration: float = DEFAULT_TARGET_MAX,
) -> Tuple[SceneContractV1, ...]:
    """Split every leaf independently; no operation can cross a container."""

    if words_per_second <= 0:
        raise ContractValidationError("words_per_second must be positive")
    leaves = _leaf_containers(containers)
    if not leaves:
        raise ContractValidationError("At least one leaf container is required")
    cursor = 0
    for container in leaves:
        if container.char_start != cursor:
            raise ContractValidationError("Leaf containers do not exactly cover the script")
        cursor = container.char_end
    if cursor != len(source_script):
        raise ContractValidationError("Leaf containers do not cover the source script")

    all_tokens = tokenize_source(source_script)
    min_words = max(1, math.ceil(min_duration * words_per_second))
    target_min_words = max(min_words, math.ceil(target_min_duration * words_per_second))
    target_max_words = max(target_min_words + 1, math.floor(target_max_duration * words_per_second))
    beat_group = _parse_beat_group(scene_mode)

    strict = _strict_format(detected_format)
    strict_subjects = tuple(
        dict.fromkeys(
            item.canonical_subject
            for item in leaves
            if item.kind not in {"intro", "outro", "chapter_intro"}
            and item.canonical_subject
        )
    )
    comparison_side_subjects = tuple(
        dict.fromkeys(
            item.canonical_subject
            for item in leaves
            if item.kind in {"comparison_left", "comparison_right"}
            and item.canonical_subject
        )
    )
    scenes: List[SceneContractV1] = []
    for container in leaves:
        atoms = _atomic_spans(
            source_script,
            container,
            all_tokens,
            max_words=target_max_words,
        )
        if beat_group is not None:
            grouped = _group_fixed(atoms, beat_group, all_tokens, min_words)
        else:
            grouped = _group_adaptive(
                atoms,
                all_tokens,
                target_min_words=target_min_words,
                target_max_words=target_max_words,
                min_words=min_words,
            )

        if container.kind == "comparison_shared" and comparison_side_subjects:
            positive = comparison_side_subjects
            forbidden = tuple()
        else:
            positive = (container.canonical_subject,) if container.canonical_subject else tuple()
            forbidden = tuple()
        if container.kind in {"comparison_left", "comparison_right"}:
            forbidden = tuple(
                item for item in comparison_side_subjects if item != container.canonical_subject
            )
        elif strict and container.kind not in {
            "intro",
            "outro",
            "comparison_shared",
        }:
            forbidden = tuple(item for item in strict_subjects if item != container.canonical_subject)
        named_factual = container.kind in {
            "list_item",
            "honorable_mention",
            "event",
            "section",
            "comparison_left",
            "comparison_right",
            "comparison_shared",
        }
        source_policy = {
            "primary_sources": ["youtube", "web_image"],
            "stock_allowed": bool(not named_factual),
            "visual_verification_required": bool(strict or named_factual),
            "strict_visual_verification": bool(strict or named_factual),
            "named_factual": bool(named_factual),
            "neutral_on_failure": True,
        }
        reuse_policy = {
            "scope": "container",
            "max_uses": 2,
            "non_adjacent": True,
        }
        for position, (start, end) in enumerate(grouped):
            token_start, token_end = token_range_for_char_span(all_tokens, start, end)
            scene_id = stable_id("scn", script_hash, container.container_id, start, end)
            scene = SceneContractV1(
                scene_id=scene_id,
                order=len(scenes),
                container_id=container.container_id,
                text=source_script[start:end],
                char_start=start,
                char_end=end,
                token_start=token_start,
                token_end=token_end,
                visual_intent=_visual_intent(container, position),
                positive_subjects=positive,
                forbidden_subjects=forbidden,
                source_policy=source_policy,
                reuse_policy=reuse_policy,
                visual_requirement=build_visual_requirement(
                    title=container.canonical_subject,
                    script=source_script,
                    scene_text=source_script[start:end],
                    positive_subjects=positive,
                    forbidden_subjects=forbidden,
                    visual_intent=_visual_intent(container, position),
                    semantic_context={
                        "container_type": getattr(container, "container_type", "")
                        or getattr(container, "kind", "")
                        or "",
                        "domain_category": getattr(container, "domain_category", "") or "",
                        "canonical_entity": container.canonical_subject,
                    },
                ),
            ).with_computed_hash()
            scenes.append(scene)

    return tuple(scenes)
