"""Renderer-neutral documentary template grammar.

The catalog is intentionally declarative.  Python uses it when building the
semantic manifest and the native FFmpeg renderer consumes the resulting
properties.  The Remotion preview consumes the same properties, so the two
paths cannot silently choose different fonts, positions, or motion.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any, Dict, Mapping


CATALOG_VERSION = 3

# These are files that are present in the repository's packaged ``fonts``
# directory.  Do not advertise a font until its file has been verified.
FONT_LIBRARY: Dict[str, Dict[str, Any]] = {
    "anton": {"family": "Anton", "file": "Anton-Regular.ttf", "role": "display", "style": "normal"},
    "bebas": {"family": "Bebas Neue", "file": "BebasNeue-Regular.ttf", "role": "display", "style": "normal"},
    "montserrat": {"family": "Montserrat", "file": "Montserrat-Bold.ttf", "role": "sans", "style": "normal"},
    "open_sans": {"family": "Open Sans", "file": "OpenSans-Bold.ttf", "role": "sans", "style": "normal"},
    "poppins": {"family": "Poppins", "file": "Poppins-Bold.ttf", "role": "sans", "style": "normal"},
    "roboto": {"family": "Roboto", "file": "Roboto-Bold.ttf", "role": "sans", "style": "normal"},
    "playfair": {"family": "Playfair Display", "file": "PlayfairDisplay-Bold.ttf", "role": "serif", "style": "normal"},
    "cinzel": {"family": "Cinzel", "file": "Cinzel-Bold.ttf", "role": "serif", "style": "normal"},
    "orbitron": {"family": "Orbitron", "file": "Orbitron-Bold.ttf", "role": "mono", "style": "normal"},
    "caveat": {"family": "Caveat", "file": "Caveat-Bold.ttf", "role": "accent", "style": "normal"},
    "creepster": {"family": "Creepster", "file": "Creepster-Regular.ttf", "role": "accent", "style": "normal", "accent_only": True},
    "nosifer": {"family": "Nosifer", "file": "Nosifer-Regular.ttf", "role": "accent", "style": "normal", "accent_only": True},
    # Modern editorial faces used by the script-driven director.  These are
    # intentionally restrained: they provide hierarchy without making every
    # scene look like a preset pack.
    "inter": {"family": "Inter", "file": "Inter-SemiBold.ttf", "role": "sans", "style": "normal"},
    "dm_sans": {"family": "DM Sans", "file": "DMSans-Bold.ttf", "role": "sans", "style": "normal"},
    "plus_jakarta": {"family": "Plus Jakarta Sans", "file": "PlusJakartaSans-Bold.ttf", "role": "sans", "style": "normal"},
    "archivo_black": {"family": "Archivo Black", "file": "ArchivoBlack-Regular.ttf", "role": "display", "style": "normal"},
    "oswald": {"family": "Oswald", "file": "Oswald-SemiBold.ttf", "role": "display", "style": "normal"},
    "jetbrains_mono": {"family": "JetBrains Mono", "file": "JetBrainsMono-Bold.ttf", "role": "mono", "style": "normal"},
    "lora": {"family": "Lora", "file": "Lora-Bold.ttf", "role": "serif", "style": "normal"},
}


ART_DIRECTIONS: Dict[str, Dict[str, Any]] = {
    "editorial": {"fonts": ("archivo_black", "inter", "plus_jakarta"), "strip": "rule", "texture": "clean", "motion": "measured"},
    # Archive is a subject treatment, not a typeface instruction.  Keep the
    # paper/trace motion but use contemporary sans faces for readability.
    "archive": {"fonts": ("archivo_black", "dm_sans", "plus_jakarta"), "strip": "rule", "texture": "archive", "motion": "paper"},
    "investigative": {"fonts": ("archivo_black", "dm_sans", "jetbrains_mono"), "strip": "redaction", "texture": "noir", "motion": "tense"},
    "cinematic": {"fonts": ("plus_jakarta", "inter", "dm_sans"), "strip": "rule", "texture": "film", "motion": "slow"},
    "data": {"fonts": ("archivo_black", "inter", "jetbrains_mono"), "strip": "grid", "texture": "digital", "motion": "precise"},
    "field": {"fonts": ("plus_jakarta", "inter", "dm_sans"), "strip": "label", "texture": "field", "motion": "light"},
    "horror": {"fonts": ("archivo_black", "dm_sans", "inter"), "strip": "redaction", "texture": "noir", "motion": "tense"},
    "gaming": {"fonts": ("archivo_black", "inter", "jetbrains_mono"), "strip": "clean", "texture": "clean", "motion": "energetic"},
}


NICHE_ART_DIRECTION = {
    "royal": "cinematic", "royalty": "cinematic",
    "history": "archive", "crime": "investigative", "finance": "data",
    "technology": "data", "travel": "field", "sports": "cinematic",
    "science": "data", "politics": "editorial", "military": "investigative",
    "nature": "field", "biography": "archive", "health": "data",
    "automotive": "cinematic", "culture": "field",
    "horror": "horror", "gaming": "gaming", "general": "editorial",
}


# Existing IDs remain valid.  These families provide a stable, smaller design
# grammar underneath the large compatibility registry.
TEMPLATE_FAMILIES: Dict[str, Dict[str, Any]] = {
    "cold_open": {"ids": ("cold-open",), "kinds": ("chapter", "highlight"), "variants": 5, "motion": ("rise", "wipe", "pop")},
    "heading_strip": {"ids": ("chapter-opener", "headline-center", "kinetic-statement"), "kinds": ("chapter", "highlight"), "variants": 6, "motion": ("slide_left", "slide_right", "wipe")},
    "date_marker": {"ids": ("date-card", "calendar-rip", "date-numeral"), "kinds": ("date",), "variants": 5, "motion": ("slide_left", "paper", "rise")},
    "stat_counter": {"ids": ("stat-counter", "money-counter", "metric-grid"), "kinds": ("stat",), "variants": 6, "motion": ("pop", "rise", "data")},
    "quote_pull": {"ids": ("quote-card", "witness-quote"), "kinds": ("quote",), "variants": 5, "motion": ("fade", "slide_left", "paper")},
    "profile_dossier": {"ids": ("profile-dossier", "profile-card", "lower-third", "exhibit-label"), "kinds": ("person",), "variants": 6, "motion": ("slide_left", "slide_right", "stamp")},
    "location_map": {"ids": ("map-route", "map-card", "route-progress", "field-caption"), "kinds": ("location",), "variants": 5, "motion": ("route", "slide_left", "wipe")},
    "source_newspaper": {"ids": ("source-highlight", "newsprint-reveal", "paper-explainer"), "kinds": ("archive", "highlight"), "variants": 6, "motion": ("paper", "wipe", "typewriter")},
    "evidence_file": {"ids": ("evidence-board", "evidence-callout", "case-file", "redaction-reveal"), "kinds": ("archive", "highlight"), "variants": 6, "motion": ("stamp", "redaction", "slide_left")},
    "timeline": {"ids": ("timeline-trace", "timeline-strip"), "kinds": ("date", "chapter"), "variants": 5, "motion": ("route", "rise", "wipe")},
    "split_compare": {"ids": ("split-screen", "split-compare", "before-after"), "kinds": ("highlight", "stat"), "variants": 5, "motion": ("split", "wipe", "slide_left")},
    "cause_effect": {"ids": ("cause-effect", "decision-tree", "blueprint-reveal"), "kinds": ("highlight",), "variants": 5, "motion": ("route", "rise", "split")},
    "data_chart": {"ids": ("bar-chart", "line-chart", "area-chart", "donut-chart", "ranked-bars", "pictogram-stat"), "kinds": ("stat",), "variants": 6, "motion": ("data", "rise", "wipe")},
    "list_build": {"ids": ("listicle-build", "listicle-steps", "word-cascade"), "kinds": ("chapter", "highlight"), "variants": 6, "motion": ("cascade", "slide_left", "rise")},
    "cinematic_focus": {"ids": ("spotlight-lens", "archival-frame", "gallery-stack", "photo-pinboard"), "kinds": ("archive", "highlight"), "variants": 6, "motion": ("focus", "paper", "slow")},
    "signal_alert": {"ids": ("signal-scan", "terminal-log", "countdown-clock", "data-race"), "kinds": ("stat", "highlight"), "variants": 5, "motion": ("scan", "data", "pop")},
}


def stable_seed(*parts: Any) -> int:
    raw = "|".join(str(part or "") for part in parts).encode("utf-8", errors="ignore")
    return int(hashlib.sha256(raw).hexdigest()[:12], 16)


def choose_art_direction(niche: str, seed: Any, mood: str = "") -> str:
    base = NICHE_ART_DIRECTION.get(str(niche or "general").lower(), "editorial")
    if str(mood or "").lower() in {"somber", "authoritative"} and base == "editorial":
        return "cinematic"
    if str(mood or "").lower() in {"tense", "clinical"} and base not in {"data", "investigative", "horror"}:
        return "investigative"
    return base


def art_direction_pack(niche: str, seed: Any, mood: str = "") -> Dict[str, Any]:
    direction_id = choose_art_direction(niche, seed, mood)
    return direction_pack(direction_id, seed)


def direction_pack(direction_id: str, seed: Any) -> Dict[str, Any]:
    direction_id = str(direction_id or "editorial").strip().lower()
    if direction_id not in ART_DIRECTIONS:
        direction_id = "editorial"
    direction = deepcopy(ART_DIRECTIONS[direction_id])
    font_ids = list(direction["fonts"])
    # Keep one coherent display/body/accent relationship across a video.
    shift = stable_seed(seed, direction_id) % len(font_ids)
    ordered = font_ids[shift:] + font_ids[:shift]
    direction.update({
        "id": direction_id,
        "display": ordered[0],
        "body": ordered[1],
        "accent": ordered[2],
        "seed": stable_seed(seed, direction_id, "art-direction"),
    })
    return direction


def template_family(template_id: str, kind: str = "highlight") -> str:
    tid = str(template_id or "").strip().lower().replace("_", "-")
    for family_id, family in TEMPLATE_FAMILIES.items():
        if tid in family["ids"]:
            return family_id
    for family_id, family in TEMPLATE_FAMILIES.items():
        if kind in family.get("kinds", ()):
            return family_id
    return "heading_strip"


def resolve_template_design(
    template_id: str,
    *,
    kind: str = "highlight",
    seed: Any = 0,
    art_direction: Mapping[str, Any] | None = None,
    position: str = "center",
) -> Dict[str, Any]:
    direction = dict(art_direction or art_direction_pack("general", seed))
    family_id = template_family(template_id, kind)
    family = TEMPLATE_FAMILIES[family_id]
    variant = stable_seed(seed, template_id, family_id, position) % int(family["variants"])
    motion_options = list(family.get("motion") or ("fade",))
    motion = motion_options[stable_seed(seed, family_id, "motion") % len(motion_options)]
    # Position participates in motion direction so a right-hand card does not
    # always enter from the same side.
    if position.endswith("_right") and motion == "slide_left":
        motion = "slide_right"
    if position.endswith("_left") and motion == "slide_right":
        motion = "slide_left"
    # Keep a coherent video-level family, but rotate the role assignment by
    # scene/family.  A single fixed face made every card look like the same
    # preset; this gives the director restrained modern variation without
    # turning typography into roulette.
    font_ids = list(direction.get("fonts") or (direction.get("display") or "poppins", direction.get("body") or "open_sans", direction.get("accent") or "open_sans"))
    # Modern documentary typography is sans-first.  Serif entries remain in
    # FONT_LIBRARY for old manifest compatibility, but the active resolver is
    # not allowed to select them for new scenes.
    display_ids = [key for key in font_ids if FONT_LIBRARY.get(str(key), {}).get("role") in {"display", "sans"}] or ["archivo_black", "inter"]
    body_ids = [key for key in font_ids if FONT_LIBRARY.get(str(key), {}).get("role") == "sans"] or ["inter", "dm_sans"]
    if position == "center" and len(display_ids) > 1:
        # Center hero text gets the cleaner reading face; the display face can
        # take the more characterful role in corners without making the whole
        # video feel like one repeated title card.
        display_key = display_ids[1]
    else:
        display_key = display_ids[stable_seed(seed, template_id, family_id, position, "display") % len(display_ids)]
    body_key = body_ids[stable_seed(seed, template_id, family_id, position, "body") % len(body_ids)]
    accent_key = font_ids[stable_seed(seed, template_id, family_id, position, "accent") % len(font_ids)]
    display = FONT_LIBRARY.get(str(display_key), FONT_LIBRARY["poppins"])
    body = FONT_LIBRARY.get(str(body_key), FONT_LIBRARY["open_sans"])
    accent_font = FONT_LIBRARY.get(str(accent_key), FONT_LIBRARY["open_sans"])
    # Novelty faces are never allowed to become body copy. They can only mark
    # a short danger/alert word in a compatible art direction.
    if accent_font.get("accent_only") and kind not in {"highlight", "stat"}:
        accent_font = display
    card_mode = "accent" if kind == "stat" else ("danger" if family_id in {"evidence_file", "signal_alert"} and variant % 4 == 0 else "surface")
    return {
        "catalog_version": CATALOG_VERSION,
        "family": family_id,
        "variant": variant,
        "entry_motion": motion,
        "display_font": display["file"],
        "body_font": body["file"],
        "accent_font": accent_font["file"],
        "font_style": display.get("style", "normal"),
        "body_font_style": body.get("style", "normal"),
        "text_transform": "uppercase" if family_id not in {"quote_pull", "source_newspaper"} else "none",
        "card_mode": card_mode,
        "strip_style": direction.get("strip", "black"),
        "texture": direction.get("texture", "clean"),
        "position": position,
        "direction": direction.get("id", "editorial"),
    }


def catalog_manifest() -> Dict[str, Any]:
    return {
        "version": CATALOG_VERSION,
        "families": deepcopy(TEMPLATE_FAMILIES),
        "art_directions": deepcopy(ART_DIRECTIONS),
        "fonts": deepcopy(FONT_LIBRARY),
        "placement_positions": ["top_left", "top_right", "center", "bottom_left", "bottom_right"],
        "forbidden_positions": ["middle_left", "middle_right"],
    }


__all__ = [
    "ART_DIRECTIONS", "CATALOG_VERSION", "FONT_LIBRARY", "TEMPLATE_FAMILIES",
    "art_direction_pack", "catalog_manifest", "choose_art_direction",
    "direction_pack",
    "resolve_template_design", "stable_seed", "template_family",
]
