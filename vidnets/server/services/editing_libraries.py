"""Curated, render-safe building blocks for the semantic editing engine.

These registries describe a design grammar, not complete video templates.  The
editing profile chooses compatible packs and the scene planner varies their
parameters.  Keeping the vocabulary allowlisted makes LLM output safe and
keeps preview/export deterministic.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, Mapping

from .documentary_template_system import catalog_manifest


FONT_LIBRARY: Dict[str, Dict[str, Any]] = {
    "anton": {"family": "Anton", "file": "Anton-Regular.ttf", "class": "condensed_display", "niches": ["crime", "sports", "business"], "license": "OFL-1.1"},
    "bebas": {"family": "Bebas Neue", "file": "BebasNeue-Regular.ttf", "class": "condensed_display", "niches": ["history", "crime", "business", "travel"], "license": "OFL-1.1"},
    "montserrat": {"family": "Montserrat", "file": "Montserrat-Bold.ttf", "class": "geometric_sans", "niches": ["business", "travel", "science", "general"], "license": "OFL-1.1"},
    "poppins": {"family": "Poppins", "file": "Poppins-Bold.ttf", "bold_file": "Poppins-Bold.ttf", "class": "geometric_sans", "niches": ["travel", "education", "general"], "license": "OFL-1.1"},
    "open_sans": {"family": "Open Sans", "file": "OpenSans-Bold.ttf", "class": "humanist_sans", "niches": ["general", "history", "science"], "license": "OFL-1.1"},
    "roboto": {"family": "Roboto", "file": "Roboto-Bold.ttf", "class": "humanist_sans", "niches": ["general", "history", "crime", "finance", "technology"], "license": "Apache-2.0"},
    "playfair": {"family": "Playfair Display", "file": "PlayfairDisplay-Bold.ttf", "class": "editorial_serif", "niches": ["history", "biography", "luxury"], "license": "OFL-1.1"},
    "cinzel": {"family": "Cinzel", "file": "Cinzel-Bold.ttf", "class": "inscription_serif", "niches": ["history", "mythology", "biography"], "license": "OFL-1.1"},
    "orbitron": {"family": "Orbitron", "file": "Orbitron-Bold.ttf", "class": "technical_display", "niches": ["technology", "science", "finance"], "license": "OFL-1.1"},
    "inter": {"family": "Inter", "file": "Inter-SemiBold.ttf", "class": "modern_sans", "niches": ["general", "history", "science", "technology", "finance"], "license": "OFL-1.1"},
    "dm_sans": {"family": "DM Sans", "file": "DMSans-Bold.ttf", "class": "modern_sans", "niches": ["general", "travel", "education", "business"], "license": "OFL-1.1"},
    "plus_jakarta": {"family": "Plus Jakarta Sans", "file": "PlusJakartaSans-Bold.ttf", "class": "modern_sans", "niches": ["general", "travel", "education", "business"], "license": "OFL-1.1"},
    "archivo_black": {"family": "Archivo Black", "file": "ArchivoBlack-Regular.ttf", "class": "display_black", "niches": ["crime", "sports", "technology", "gaming"], "license": "OFL-1.1"},
    "oswald": {"family": "Oswald", "file": "Oswald-SemiBold.ttf", "class": "condensed_display", "niches": ["history", "crime", "sports", "cinematic"], "license": "OFL-1.1"},
    "jetbrains_mono": {"family": "JetBrains Mono", "file": "JetBrainsMono-Bold.ttf", "class": "technical_mono", "niches": ["technology", "science", "finance"], "license": "OFL-1.1"},
    "lora": {"family": "Lora", "file": "Lora-Bold.ttf", "class": "editorial_serif", "niches": ["history", "biography", "luxury"], "license": "OFL-1.1"},
    "audiowide": {"family": "Audiowide", "file": "Poppins-Bold.ttf", "class": "technical_display", "niches": ["technology", "science"], "license": "OFL-1.1"},
    "righteous": {"family": "Righteous", "file": "Poppins-Bold.ttf", "class": "expressive_display", "niches": ["travel", "culture", "entertainment"], "license": "OFL-1.1"},
    "bungee": {"family": "Bungee", "file": "Poppins-Bold.ttf", "class": "expressive_display", "niches": ["sports", "entertainment"], "license": "OFL-1.1"},
    "caveat": {"family": "Caveat", "file": "OpenSans-Bold.ttf", "class": "handwritten", "niches": ["travel", "history", "education"], "license": "OFL-1.1"},
    "patrick_hand": {"family": "Patrick Hand", "file": "OpenSans-Bold.ttf", "class": "handwritten", "niches": ["education", "travel"], "license": "OFL-1.1"},
    "creepster": {"family": "Creepster", "file": "Poppins-Bold.ttf", "class": "horror_display", "niches": ["horror"], "license": "OFL-1.1", "accent_only": True},
    "nosifer": {"family": "Nosifer", "file": "Poppins-Bold.ttf", "class": "horror_display", "niches": ["horror"], "license": "OFL-1.1", "accent_only": True},
}


FONT_PAIRINGS: Dict[str, list[Dict[str, str]]] = {
    "royal": [{"display": "plus_jakarta", "body": "inter", "accent": "dm_sans"}, {"display": "archivo_black", "body": "dm_sans", "accent": "inter"}],
    "history": [
        {"display": "plus_jakarta", "body": "inter", "accent": "dm_sans"},
        {"display": "archivo_black", "body": "dm_sans", "accent": "inter"},
    ],
    "crime": [
        {"display": "archivo_black", "body": "inter", "accent": "dm_sans"},
        {"display": "plus_jakarta", "body": "dm_sans", "accent": "inter"},
    ],
    "travel": [
        {"display": "plus_jakarta", "body": "inter", "accent": "dm_sans"},
        {"display": "archivo_black", "body": "dm_sans", "accent": "inter"},
    ],
    "finance": [
        {"display": "plus_jakarta", "body": "inter", "accent": "jetbrains_mono"},
        {"display": "archivo_black", "body": "dm_sans", "accent": "inter"},
    ],
    "technology": [
        {"display": "plus_jakarta", "body": "inter", "accent": "jetbrains_mono"},
        {"display": "archivo_black", "body": "dm_sans", "accent": "inter"},
    ],
    "sports": [
        {"display": "archivo_black", "body": "inter", "accent": "dm_sans"},
        {"display": "plus_jakarta", "body": "dm_sans", "accent": "inter"},
    ],
    "gaming": [
        {"display": "archivo_black", "body": "inter", "accent": "jetbrains_mono"},
        {"display": "plus_jakarta", "body": "inter", "accent": "dm_sans"},
    ],
    "horror": [
        {"display": "archivo_black", "body": "inter", "accent": "dm_sans"},
        {"display": "plus_jakarta", "body": "inter", "accent": "dm_sans"},
    ],
    "general": [
        {"display": "plus_jakarta", "body": "inter", "accent": "dm_sans"},
        {"display": "archivo_black", "body": "dm_sans", "accent": "inter"},
    ],
}

# Fonts permitted in generated documentary profiles. The full font registry is
# still available to the editor, but automatic projects should not drift into
# handwriting, novelty, or horror display faces for ordinary narration.
DOCUMENTARY_FONT_ROLES: Dict[str, tuple[str, ...]] = {
    "display": ("inter", "plus_jakarta", "archivo_black", "dm_sans"),
    "body": ("inter", "dm_sans", "plus_jakarta"),
    "accent": ("dm_sans", "inter", "jetbrains_mono"),
}


OVERLAY_LIBRARY: Dict[str, Dict[str, Any]] = {
    "clean": {"layers": ["vignette"], "intensity": 0.12},
    "archive": {"layers": ["paper_fibers", "dust", "film_scratches", "vignette"], "intensity": 0.28},
    "cinematic": {"layers": ["grain", "halation", "light_leak", "vignette"], "intensity": 0.22},
    "investigative": {"layers": ["grain", "scanlines", "film_scratches", "vignette"], "intensity": 0.25},
    "field_notes": {"layers": ["paper_fibers", "dust", "light_leak"], "intensity": 0.17},
    "digital": {"layers": ["scanlines", "chromatic_edge", "signal_noise", "vignette"], "intensity": 0.18},
    "noir": {"layers": ["grain", "dust", "projector_flicker", "vignette"], "intensity": 0.3},
    "dream": {"layers": ["bokeh", "halation", "light_leak"], "intensity": 0.2},
    "storm": {"layers": ["rain_glass", "grain", "vignette"], "intensity": 0.24},
}


TRANSITION_PACKS: Dict[str, list[str]] = {
    "editorial": ["cut", "dissolve", "directional_wipe", "luma_flash", "map_sweep"],
    "archive": ["cut", "dissolve", "paper_tear", "film_burn", "dip_black"],
    "investigative": ["cut", "dip_black", "glitch_slice", "luma_flash", "ink_bleed"],
    "energetic": ["cut", "directional_wipe", "luma_flash", "glitch_slice", "split_reveal"],
    "minimal": ["cut", "dissolve", "dip_black", "dip_white"],
    "cinematic": ["cut", "dissolve", "dip_black", "film_burn", "iris"],
    "travel": ["cut", "map_sweep", "directional_wipe", "light_leak", "dissolve"],
}


IMAGE_TREATMENT_LIBRARY: Dict[str, Dict[str, Any]] = {
    "hold": {"motion": "static", "family": "hold", "requires": [], "max_strength": 0.0},
    "editorial_crop": {"motion": "masked_reveal", "family": "reveal", "requires": [], "max_strength": 0.35},
    "split_mask": {"motion": "split_reveal", "family": "reveal", "requires": [], "max_strength": 0.42},
    "paper_reveal": {"motion": "paper_reveal", "family": "archive", "requires": [], "max_strength": 0.38},
    "iris_focus": {"motion": "iris_focus", "family": "focus", "requires": ["focal_bbox"], "max_strength": 0.3},
    "subject_slide": {"motion": "subject_slide", "family": "focus", "requires": ["focal_bbox"], "max_strength": 0.4},
    "depth_separation": {"motion": "depth_separation", "family": "focus", "requires": ["segmentation"], "max_strength": 0.25},
    "blurred_stage": {"motion": "blurred_stage", "family": "stage", "requires": [], "max_strength": 0.2},
    "film_gate": {"motion": "film_gate", "family": "archive", "requires": [], "max_strength": 0.2},
    "contact_sheet": {"motion": "contact_sheet", "family": "collage", "requires": ["three_media"], "max_strength": 0.3},
    "map_window": {"motion": "map_window", "family": "stage", "requires": [], "max_strength": 0.35},
    "spotlight": {"motion": "spotlight", "family": "focus", "requires": ["focal_bbox"], "max_strength": 0.25},
    "archival_white_border": {"motion": "archival_white_border", "family": "archive", "requires": [], "max_strength": 0.3},
    "film_matte_16mm": {"motion": "film_matte_16mm", "family": "archive", "requires": [], "max_strength": 0.3},
    "side_by_side_collage": {"motion": "side_by_side_collage", "family": "collage", "requires": [], "max_strength": 0.35},
    "depth_drift_25d": {"motion": "depth_drift_25d", "family": "focus", "requires": [], "max_strength": 0.35},
}

IMAGE_TREATMENT_ALIASES: Dict[str, str] = {
    "static": "hold", "static_hold": "hold", "ken_burns": "editorial_crop",
    "blur_stage": "blurred_stage", "subject_reveal": "subject_slide",
    "flip_border": "film_gate",
    "archival_frame": "archival_white_border", "polaroid": "archival_white_border",
    "film_16mm": "film_matte_16mm", "16mm_matte": "film_matte_16mm",
    "side_by_side": "side_by_side_collage", "25d_drift": "depth_drift_25d",
}


TEXT_STYLE_LIBRARY: Dict[str, Dict[str, Any]] = {
    "highlight_block": {"animation": "mask_wipe", "layout": "editorial_block"},
    "word_cascade": {"animation": "stagger_words", "layout": "center"},
    "kinetic_columns": {"animation": "column_build", "layout": "split"},
    "archive_label": {"animation": "stamp_in", "layout": "lower_corner"},
    "typewriter_clause": {"animation": "type_on", "layout": "document"},
    "outline_echo": {"animation": "echo_reveal", "layout": "full"},
    "marker_strike": {"animation": "marker_wipe", "layout": "inline"},
    "redaction": {"animation": "redaction_reveal", "layout": "document"},
    "data_label": {"animation": "number_roll", "layout": "metric"},
    "chapter_spine": {"animation": "vertical_wipe", "layout": "edge"},
    "subtitle_punch": {"animation": "scale_pop", "layout": "lower"},
    "quote_pull": {"animation": "line_build", "layout": "editorial_quote"},
}


GRAPH_LIBRARY: Dict[str, Dict[str, Any]] = {
    "bar": {"template_id": "bar-chart", "roles": ["comparison", "evidence"]},
    "line": {"template_id": "line-chart", "roles": ["trend", "evidence"]},
    "area": {"template_id": "area-chart", "roles": ["trend", "scale"]},
    "donut": {"template_id": "donut-chart", "roles": ["share", "statistic"]},
    "slope": {"template_id": "slope-chart", "roles": ["before_after", "comparison"]},
    "ranking": {"template_id": "ranked-bars", "roles": ["ranking", "comparison"]},
    "pictogram": {"template_id": "pictogram-stat", "roles": ["human_stakes", "statistic"]},
    "timeline": {"template_id": "timeline-trace", "roles": ["chronology", "evidence"]},
    "metric_grid": {"template_id": "metric-grid", "roles": ["scale", "statistic"]},
}


SFX_PACKS: Dict[str, list[str]] = {
    "archive": ["paper_flip", "paper_rustle", "paper_tear", "typewriter", "camera_shutter", "vinyl_crackle", "stamp_hit", "projector", "film_splice", "page_stack", "archive_drone", "telegraph"],
    "investigative": ["low_rumble", "suspense_pulse", "radio_static", "reverse_swell", "sub_boom", "marker_scratch", "heartbeat", "tension_string", "whoosh_dark", "transition_suck"],
    "editorial": ["whoosh", "whoosh_soft", "whoosh_air", "whoosh_snap", "impact_soft", "decision_tick", "photo_slide", "typing_burst", "reveal_chime", "news_sting"],
    "technology": ["ui_tick", "technical_tick", "signal_ping", "data_tick", "radio_static", "keyboard_single", "mouse_click", "notification_up", "notification_down", "sonar"],
    "travel": ["route_ping", "map_pulse", "map_draw", "water_pulse", "camera_shutter", "camera_wind", "whoosh_soft", "wind_room"],
    "finance": ["data_tick", "decision_tick", "impact_soft", "technical_tick", "ui_tick", "coin_tick", "cash_counter", "notification_up"],
    "hook": ["riser", "reverse_swell", "impact", "impact_metal", "bass_drop", "sub_boom", "whoosh", "reveal_chime"],
    "quiet": ["room_tone", "vinyl_crackle", "paper_rustle", "whoosh_soft", "archive_drone", "wind_room", "crowd_murmur"],
}


NICHE_PACKS: Dict[str, Dict[str, str]] = {
    "aviation": {'overlay': 'cinematic', 'transition': 'editorial', 'sfx': 'editorial', 'text': 'archive_label', 'image': 'editorial_crop'},
    "maritime": {'overlay': 'cinematic', 'transition': 'editorial', 'sfx': 'editorial', 'text': 'archive_label', 'image': 'editorial_crop'},
    "engineering": {'overlay': 'clean', 'transition': 'editorial', 'sfx': 'editorial', 'text': 'data_label', 'image': 'editorial_crop'},
    "business": {'overlay': 'cinematic', 'transition': 'editorial', 'sfx': 'editorial', 'text': 'archive_label', 'image': 'editorial_crop'},
    "food": {'overlay': 'cinematic', 'transition': 'editorial', 'sfx': 'quiet', 'text': 'archive_label', 'image': 'editorial_crop'},
    "architecture": {'overlay': 'cinematic', 'transition': 'editorial', 'sfx': 'editorial', 'text': 'archive_label', 'image': 'editorial_crop'},
    "education": {'overlay': 'clean', 'transition': 'editorial', 'sfx': 'editorial', 'text': 'data_label', 'image': 'editorial_crop'},
    "royal": {"overlay": "cinematic", "transition": "cinematic", "sfx": "editorial", "text": "archive_label", "image": "editorial_crop"},
    "history": {"overlay": "archive", "transition": "archive", "sfx": "archive", "text": "archive_label", "image": "film_gate"},
    "crime": {"overlay": "investigative", "transition": "investigative", "sfx": "investigative", "text": "redaction", "image": "spotlight"},
    "travel": {"overlay": "field_notes", "transition": "travel", "sfx": "travel", "text": "chapter_spine", "image": "map_window"},
    "finance": {"overlay": "digital", "transition": "editorial", "sfx": "finance", "text": "data_label", "image": "split_mask"},
    "technology": {"overlay": "digital", "transition": "energetic", "sfx": "technology", "text": "outline_echo", "image": "editorial_crop"},
    "sports": {"overlay": "cinematic", "transition": "energetic", "sfx": "editorial", "text": "subtitle_punch", "image": "split_mask"},
    "gaming": {"overlay": "digital", "transition": "energetic", "sfx": "technology", "text": "highlight_block", "image": "editorial_crop"},
    "science": {"overlay": "clean", "transition": "minimal", "sfx": "technology", "text": "data_label", "image": "editorial_crop"},
    "politics": {"overlay": "clean", "transition": "editorial", "sfx": "editorial", "text": "archive_label", "image": "editorial_crop"},
    "military": {"overlay": "investigative", "transition": "cinematic", "sfx": "investigative", "text": "data_label", "image": "film_gate"},
    "nature": {"overlay": "cinematic", "transition": "travel", "sfx": "quiet", "text": "chapter_spine", "image": "editorial_crop"},
    "biography": {"overlay": "archive", "transition": "cinematic", "sfx": "archive", "text": "archive_label", "image": "film_gate"},
    "health": {"overlay": "clean", "transition": "editorial", "sfx": "editorial", "text": "data_label", "image": "editorial_crop"},
    "automotive": {"overlay": "cinematic", "transition": "energetic", "sfx": "editorial", "text": "highlight_block", "image": "split_mask"},
    "culture": {"overlay": "field_notes", "transition": "editorial", "sfx": "editorial", "text": "chapter_spine", "image": "editorial_crop"},
    "horror": {"overlay": "noir", "transition": "investigative", "sfx": "investigative", "text": "redaction", "image": "spotlight"},
    "general": {"overlay": "cinematic", "transition": "editorial", "sfx": "editorial", "text": "highlight_block", "image": "editorial_crop"},
}


def library_manifest() -> Dict[str, Any]:
    return {
        "fonts": deepcopy(FONT_LIBRARY),
        "font_pairings": deepcopy(FONT_PAIRINGS),
        "overlays": deepcopy(OVERLAY_LIBRARY),
        "transition_packs": deepcopy(TRANSITION_PACKS),
        "image_treatments": deepcopy(IMAGE_TREATMENT_LIBRARY),
        "image_treatment_aliases": deepcopy(IMAGE_TREATMENT_ALIASES),
        "text_styles": deepcopy(TEXT_STYLE_LIBRARY),
        "graphs": deepcopy(GRAPH_LIBRARY),
        "sfx_packs": deepcopy(SFX_PACKS),
        "niche_packs": deepcopy(NICHE_PACKS),
        "documentary_template_catalog": catalog_manifest(),
    }


def allowed_id(value: Any, registry: Mapping[str, Any], fallback: str) -> str:
    candidate = str(value or "").strip().lower().replace("-", "_")
    return candidate if candidate in registry else fallback


def allowed_list(values: Iterable[Any], allowed: Iterable[str], fallback: Iterable[str]) -> list[str]:
    permitted = set(allowed)
    result: list[str] = []
    for value in values or []:
        candidate = str(value or "").strip().lower().replace("-", "_")
        if candidate in permitted and candidate not in result:
            result.append(candidate)
    return result or list(fallback)
