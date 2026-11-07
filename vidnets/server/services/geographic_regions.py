"""Small, explicit catalogue for broad named regions used by map scenes.

These are camera centers for editorial-scale locator maps, not route endpoints
or claims about an exact incident location. A region is eligible only when its
name appears in the narration.
"""

from __future__ import annotations

import re
import unicodedata


REGION_CENTERS = {
    "north atlantic": (-40.0, 50.0),
    "south atlantic": (-15.0, -25.0),
    "atlantic ocean": (-30.0, 0.0),
    "atlantic": (-30.0, 0.0),
    "north pacific": (-165.0, 38.0),
    "south pacific": (-145.0, -25.0),
    "pacific ocean": (-150.0, 0.0),
    "pacific": (-150.0, 0.0),
    "indian ocean": (80.0, -20.0),
    "arctic ocean": (0.0, 78.0),
    "southern ocean": (0.0, -60.0),
    "mediterranean sea": (18.0, 35.0),
    "caribbean sea": (-75.0, 17.0),
    "gulf of mexico": (-90.0, 25.0),
    "red sea": (38.0, 20.0),
    "black sea": (35.0, 43.0),
    "north sea": (3.0, 56.0),
    "south china sea": (114.0, 12.0),
}


def normalize_region_name(value: object) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or "").casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s-]", " ", plain)).strip()


def region_center(value: object) -> tuple[float, float] | None:
    return REGION_CENTERS.get(normalize_region_name(value))


def is_curated_region(value: object) -> bool:
    return region_center(value) is not None


def narrated_region_names(text: object) -> list[str]:
    """Return only exact, bounded region names present in the supplied text."""
    source = str(text or "")
    matches: list[tuple[int, int, str]] = []
    for name in REGION_CENTERS:
        phrase = re.escape(name).replace(r"\ ", r"\s+")
        for match in re.finditer(rf"(?<![\w]){phrase}(?![\w])", source, re.I):
            matches.append((match.start(), match.end(), match.group(0)))
    selected: list[tuple[int, int, str]] = []
    for candidate in sorted(matches, key=lambda item: (item[0], -(item[1] - item[0]))):
        start, end, _ = candidate
        if any(start >= kept_start and end <= kept_end for kept_start, kept_end, _name in selected):
            continue
        selected.append(candidate)
    return list(dict.fromkeys(name for _start, _end, name in sorted(selected)))
