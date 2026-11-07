"""Small, deterministic helpers for exact named matchups in scene text."""

from __future__ import annotations

import re
from typing import Iterable


_ENTITY = r"(?:(?i:the)\s+)?[A-Z][\w&.'’–-]*(?:\s+(?:[A-Z][\w&.'’–-]*|(?i:of|the|and))){0,2}"
_CONNECTOR = r"(?i:will\s+(?:face|meet|battle|play)|take\s+on|takes\s+on|face|faces|battle|battles|meet|meets|play|plays|against|versus|vs\.?)"
_PAIR_RE = re.compile(rf"(?P<left>{_ENTITY})\s+\b{_CONNECTOR}\b\s+(?P<right>{_ENTITY})")
_LEADING_FILLERS = {"the", "a", "an", "and", "but", "today", "now"}


def normalize_entity_phrase(value: str) -> str:
    """Normalize an exact spoken entity without broadening it into a topic."""
    tokens = re.findall(r"[a-z0-9]+", str(value or "").casefold())
    while tokens and tokens[0] in _LEADING_FILLERS:
        tokens.pop(0)
    return " ".join(tokens)


def explicit_named_pairs(text: str) -> list[dict[str, str]]:
    """Extract explicitly narrated pairings; never infer an unspoken matchup."""
    output: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in _PAIR_RE.finditer(str(text or "")):
        left = re.sub(r"\s+", " ", match.group("left")).strip(" .,:;!?\n\t")
        right = re.sub(r"\s+", " ", match.group("right")).strip(" .,:;!?\n\t")
        left_key, right_key = normalize_entity_phrase(left), normalize_entity_phrase(right)
        if not left_key or not right_key or left_key == right_key:
            continue
        key = tuple(sorted((left_key, right_key)))
        if key in seen:
            continue
        seen.add(key)
        output.append({"left": left, "right": right, "key": "|".join(key)})
    return output


def comparison_pair_conflict(scene_text: str, candidate_descriptions: Iterable[str]) -> bool:
    """Veto explicit pairings that do not match any spoken pairing."""
    required = explicit_named_pairs(scene_text)
    if not required:
        return False
    observed: list[dict[str, str]] = []
    for description in candidate_descriptions:
        observed.extend(explicit_named_pairs(description))
    if not observed:
        return False

    def same_entity(left: str, right: str) -> bool:
        a, b = normalize_entity_phrase(left), normalize_entity_phrase(right)
        # Search titles frequently prefix the spoken team/person with a city or
        # honorific ("Kansas City Chiefs" vs "Chiefs"). Accept that exact
        # trailing-name expansion while still comparing complete pairings.
        return bool(a and b and (a == b or a.endswith(f" {b}") or b.endswith(f" {a}")))

    for expected in required:
        for actual in observed:
            direct = same_entity(expected["left"], actual["left"]) and same_entity(expected["right"], actual["right"])
            reverse = same_entity(expected["left"], actual["right"]) and same_entity(expected["right"], actual["left"])
            if direct or reverse:
                return False
    return True

