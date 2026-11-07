"""Visual DNA Generator for the Programmatic Documentary Editor.

Enforces:
1. Client / Channel Brand (top level: persistent logos, colors, forbidden items)
2. Documentary Visual DNA (generated per project: coherent design language)
3. Scene Variation (procedural layout/camera within the project's DNA)

One documentary = one coherent design language.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Mapping, Optional, Sequence

from .contract import VisualDNA


FONT_PERSONALITIES: Dict[str, Dict[str, str]] = {
    # Kept as compatibility keys for older manifests, but deliberately mapped
    # to the modern sans system with max 2 font families per documentary.
    "serifEditorial": {
        "display": "Archivo Black",
        "body": "Inter",
        "numbers": "Inter",
        "class": "modern_editorial",
    },
    "serifHistorical": {
        "display": "Plus Jakarta Sans",
        "body": "DM Sans",
        "numbers": "DM Sans",
        "class": "modern_archival",
    },
    "neoGrotesk": {
        "display": "Plus Jakarta Sans",
        "body": "Inter",
        "numbers": "Inter",
        "class": "modern_grotesk",
    },
    "geometric": {
        "display": "Plus Jakarta Sans",
        "body": "Inter",
        "numbers": "Inter",
        "class": "clean_geometric",
    },
    "humanist": {
        "display": "DM Sans",
        "body": "Inter",
        "numbers": "Inter",
        "class": "humanist_neutral",
    },
    "condensed": {
        "display": "Archivo Black",
        "body": "Inter",
        "numbers": "Inter",
        "class": "bold_editorial",
    },
    "technical": {
        "display": "Inter",
        "body": "JetBrains Mono",
        "numbers": "JetBrains Mono",
        "class": "digital_technical",
    },
    "displayLuxury": {
        "display": "Plus Jakarta Sans",
        "body": "Inter",
        "numbers": "Inter",
        "class": "luxury_classic",
    },
    "historicalParchment": {
        "display": "Archivo Black",
        "body": "DM Sans",
        "numbers": "DM Sans",
        "class": "modern_archival",
    },
    "voxBold": {
        "display": "Archivo Black",
        "body": "Inter",
        "numbers": "Inter",
        "class": "vox_editorial",
    },
}

GENRE_PALETTE_FAMILIES: Dict[str, List[Dict[str, str]]] = {
    "investigative-documentary": [
        {
            "primary": "#FFFFFF",
            "secondary": "#E2E8F0",
            "accent": "#FACC15",  # Canary yellow (Vox style)
            "accent2": "#F97316", # Warm amber
            "background": "#0D1117",
            "surface": "#161B22",
            "text_contrast": "#FFFFFF",
            "border": "#30363D",
            "glow": "rgba(250, 204, 21, 0.32)",
        },
        {
            "primary": "#F8FAFC",
            "secondary": "#CBD5E1",
            "accent": "#E53E3E",  # Investigative crimson
            "accent2": "#F59E0B", # Caution gold
            "background": "#0C0E14",
            "surface": "#161820",
            "text_contrast": "#FFFFFF",
            "border": "#2D3748",
            "glow": "rgba(229, 62, 62, 0.30)",
        },
        {
            "primary": "#FFFFFF",
            "secondary": "#94A3B8",
            "accent": "#E2E8F0",  # Clean monochrome slate
            "accent2": "#64748B",
            "background": "#09090B",
            "surface": "#18181B",
            "text_contrast": "#FFFFFF",
            "border": "#27272A",
            "glow": "rgba(226, 232, 240, 0.25)",
        },
    ],
    "technology": [
        {
            "primary": "#FFFBEB",
            "secondary": "#FDE68A",
            "accent": "#F59E0B",  # Silicon Amber
            "accent2": "#D97706", # Copper conductor
            "background": "#09090B",
            "surface": "#18181B",
            "text_contrast": "#FFFFFF",
            "border": "#27272A",
            "glow": "rgba(245, 158, 11, 0.35)",
        },
        {
            "primary": "#F0FDF4",
            "secondary": "#BBF7D0",
            "accent": "#10B981",  # Emerald wafer
            "accent2": "#84CC16", # Laser lime
            "background": "#060B08",
            "surface": "#0F1A13",
            "text_contrast": "#FFFFFF",
            "border": "#193324",
            "glow": "rgba(16, 185, 129, 0.35)",
        },
        {
            "primary": "#FFFFFF",
            "secondary": "#CBD5E1",
            "accent": "#FF4365",  # Laser Coral / Superconductor
            "accent2": "#FFAA00", # Plasma amber
            "background": "#0B0C12",
            "surface": "#161824",
            "text_contrast": "#FFFFFF",
            "border": "#282B3F",
            "glow": "rgba(255, 67, 101, 0.35)",
        },
    ],
    "true-crime": [
        {
            "primary": "#F8FAFC",
            "secondary": "#CBD5E1",
            "accent": "#D90429",  # Blood crimson
            "accent2": "#FFB703", # Hazard amber
            "background": "#0D0D11",
            "surface": "#16161D",
            "text_contrast": "#FFFFFF",
            "border": "#3D1720",
            "glow": "rgba(217, 4, 41, 0.40)",
        },
        {
            "primary": "#F8FAFC",
            "secondary": "#94A3B8",
            "accent": "#EA580C",  # Warning ochre
            "accent2": "#CA8A04", # Dossier gold
            "background": "#0C0A09",
            "surface": "#1A1613",
            "text_contrast": "#FFFFFF",
            "border": "#382517",
            "glow": "rgba(234, 88, 12, 0.36)",
        },
        {
            "primary": "#F1F5F9",
            "secondary": "#64748B",
            "accent": "#DC2626",  # Evidence red
            "accent2": "#475569", # Cold slate
            "background": "#0A0B0D",
            "surface": "#14161B",
            "text_contrast": "#FFFFFF",
            "border": "#252B38",
            "glow": "rgba(220, 38, 38, 0.35)",
        },
    ],
    "finance-explainer": [
        {
            "primary": "#F8FAFC",
            "secondary": "#94A3B8",
            "accent": "#10B981",  # Wall street mint
            "accent2": "#F59E0B", # Amber ledger
            "background": "#090D16",
            "surface": "#111827",
            "text_contrast": "#FFFFFF",
            "border": "#1F2937",
            "glow": "rgba(16, 185, 129, 0.35)",
        },
        {
            "primary": "#FDFBF7",
            "secondary": "#E2D9C8",
            "accent": "#D4AF37",  # Swiss Bullion gold
            "accent2": "#B45309", # Bronze reserve
            "background": "#0C0A09",
            "surface": "#1C1917",
            "text_contrast": "#FFFFFF",
            "border": "#3B2E1E",
            "glow": "rgba(212, 175, 55, 0.36)",
        },
        {
            "primary": "#F0FDF4",
            "secondary": "#A7F3D0",
            "accent": "#059669",  # Terminal emerald
            "accent2": "#E11D48", # Bearish indicator crimson
            "background": "#060D0B",
            "surface": "#0E1C18",
            "text_contrast": "#FFFFFF",
            "border": "#1B3B31",
            "glow": "rgba(5, 150, 105, 0.35)",
        },
    ],
    "military-tactical": [
        {
            "primary": "#FEF3C7",
            "secondary": "#FDE68A",
            "accent": "#F59E0B",  # Tactical amber flare
            "accent2": "#B45309", # Field bronze
            "background": "#0E0D09",
            "surface": "#1A1813",
            "text_contrast": "#FFFFFF",
            "border": "#2E2A1E",
            "glow": "rgba(245, 158, 11, 0.32)",
        },
        {
            "primary": "#E2E8F0",
            "secondary": "#94A3B8",
            "accent": "#F97316",  # Beacon flare orange
            "accent2": "#EF4444", # Threat radar red
            "background": "#0A0B0D",
            "surface": "#14161A",
            "text_contrast": "#FFFFFF",
            "border": "#2B2825",
            "glow": "rgba(249, 115, 22, 0.35)",
        },
    ],
    "gaming-blockbuster": [
        {
            "primary": "#FFFFFF",
            "secondary": "#FFE082",
            "accent": "#FF2A6D",  # Neon flamingo
            "accent2": "#FFB300", # Sunset gold
            "background": "#0E1017",
            "surface": "#181B26",
            "text_contrast": "#FFFFFF",
            "border": "#FF2A6D",
            "glow": "rgba(255, 42, 109, 0.40)",
        },
        {
            "primary": "#FFFFFF",
            "secondary": "#A7F3D0",
            "accent": "#FF3E3E",  # Arcade vermillion
            "accent2": "#10B981", # High-score green
            "background": "#0D0D11",
            "surface": "#18181E",
            "text_contrast": "#FFFFFF",
            "border": "#3A1B24",
            "glow": "rgba(255, 62, 62, 0.35)",
        },
    ],
    "science-nature": [
        {
            "primary": "#FFFBEB",
            "secondary": "#FED7AA",
            "accent": "#F97316",  # Solar flare
            "accent2": "#EAB308", # Fusion gold
            "background": "#0D0B08",
            "surface": "#1A1612",
            "text_contrast": "#FFFFFF",
            "border": "#362419",
            "glow": "rgba(249, 115, 22, 0.35)",
        },
        {
            "primary": "#F0FDF4",
            "secondary": "#BBF7D0",
            "accent": "#16A34A",  # Canopy green
            "accent2": "#CA8A04", # Harvest amber
            "background": "#060F0A",
            "surface": "#0D1E14",
            "text_contrast": "#FFFFFF",
            "border": "#193B27",
            "glow": "rgba(22, 163, 74, 0.32)",
        },
        {
            "primary": "#F0FDFA",
            "secondary": "#99F6E4",
            "accent": "#0D9488",  # Oceanic teal
            "accent2": "#0284C7", # Abyssal blue
            "background": "#050F14",
            "surface": "#0B1D26",
            "text_contrast": "#FFFFFF",
            "border": "#153B4D",
            "glow": "rgba(13, 148, 136, 0.32)",
        },
    ],
    "royal-history": [
        {
            "primary": "#FDFBF7",
            "secondary": "#E2D9C8",
            "accent": "#D4AF37",  # Imperial gold
            "accent2": "#991B1B", # Royal burgundy
            "background": "#140A10",
            "surface": "#23121D",
            "text_contrast": "#FFF9E6",
            "border": "#4A283C",
            "glow": "rgba(212, 175, 55, 0.35)",
        },
        {
            "primary": "#FAF5EF",
            "secondary": "#D7C4B0",
            "accent": "#C29B38",  # Archival brass
            "accent2": "#78350F", # Burnt sienna
            "background": "#120F0C",
            "surface": "#1F1A15",
            "text_contrast": "#FFFDF5",
            "border": "#3B2E20",
            "glow": "rgba(194, 155, 56, 0.30)",
        },
    ],
    "historical-documentary": [
        {
            "primary": "#F4F0E8", "secondary": "#C5B8A5", "accent": "#C17A3A",
            "accent2": "#6F8790", "background": "#111312", "surface": "#1B1D1B",
            "text_contrast": "#FFFFFF", "border": "#3E403C", "glow": "rgba(193, 122, 58, 0.24)",
        },
    ],
    # Genre palettes are restrained editorial systems. They inform contrast
    # and emphasis while scene-by-scene LLM direction selects the actual look.
    "sports-documentary": [{
        "primary": "#F5F3EA", "secondary": "#A7B1B6", "accent": "#D6A84F",
        "accent2": "#4F8B78", "background": "#101816", "surface": "#1A2420",
        "text_contrast": "#FFFFFF", "border": "#34443C", "glow": "rgba(214, 168, 79, 0.28)",
    }],
    "politics-explainer": [{
        "primary": "#F4F1E9", "secondary": "#B7B2A7", "accent": "#D5A548",
        "accent2": "#6D8FA0", "background": "#111518", "surface": "#1C2326",
        "text_contrast": "#FFFFFF", "border": "#394246", "glow": "rgba(213, 165, 72, 0.24)",
    }],
    "travelogue": [{
        "primary": "#F5F2E9", "secondary": "#B4C1B3", "accent": "#8DAF83",
        "accent2": "#D2A96B", "background": "#0D1916", "surface": "#172722",
        "text_contrast": "#FFFFFF", "border": "#34483E", "glow": "rgba(141, 175, 131, 0.25)",
    }],
    "automotive-documentary": [{
        "primary": "#F4F4F1", "secondary": "#ADB4B8", "accent": "#C88D59",
        "accent2": "#71818B", "background": "#111416", "surface": "#1B2023",
        "text_contrast": "#FFFFFF", "border": "#384146", "glow": "rgba(200, 141, 89, 0.27)",
    }],
    "biographical-essay": [{
        "primary": "#F4EFE5", "secondary": "#C9BDAA", "accent": "#BE9C5B",
        "accent2": "#8C7866", "background": "#171512", "surface": "#24201A",
        "text_contrast": "#FFFDF8", "border": "#494035", "glow": "rgba(190, 156, 91, 0.24)",
    }],
    "cultural-chronicle": [{
        "primary": "#F4F0E9", "secondary": "#BDB5AA", "accent": "#C87555",
        "accent2": "#77958C", "background": "#151615", "surface": "#222522",
        "text_contrast": "#FFFFFF", "border": "#414742", "glow": "rgba(200, 117, 85, 0.25)",
    }],
    "health-science": [{
        "primary": "#F0F6F3", "secondary": "#AAC1BA", "accent": "#55A18E",
        "accent2": "#D4B56C", "background": "#0C1717", "surface": "#142322",
        "text_contrast": "#FFFFFF", "border": "#2E4A46", "glow": "rgba(85, 161, 142, 0.24)",
    }],
}

GENRE_PRESETS: Dict[str, Dict[str, Any]] = {
    "investigative-documentary": {
        "palette": GENRE_PALETTE_FAMILIES["investigative-documentary"][0],
        "personality": "voxBold",
        "geometry": "sharp",
        "texture": "clean",
        "motion_style": "restrained",
        "caption_style": "documentary_small",
        "map_style": "dark_desaturated",
        "chart_style": "editorial_white",
        "image_treatment": "cinematic_grade",
        "transition_grammar": "mostly_cuts",
        "corner_radius": 2,
        "stroke_width": 2,
    },
    "technology": {
        "palette": GENRE_PALETTE_FAMILIES["technology"][0],
        "personality": "technical",
        "geometry": "sharp",
        "texture": "clean",
        "motion_style": "technical_crisp",
        "caption_style": "minimal",
        "map_style": "blueprint_slate",
        "chart_style": "editorial_white",
        "image_treatment": "cinematic_grade",
        "transition_grammar": "technical_wipes",
        "corner_radius": 4,
        "stroke_width": 2,
    },
    "royal-history": {
        "palette": GENRE_PALETTE_FAMILIES["royal-history"][0],
        "personality": "geometric",
        "geometry": "framed",
        "texture": "clean",
        "motion_style": "cinematic_slow",
        "caption_style": "cinematic",
        "map_style": "archival_sepia",
        "chart_style": "editorial",
        "image_treatment": "warm_archival",
        "transition_grammar": "archival_burns",
        "corner_radius": 0,
        "stroke_width": 2,
    },
    "historical-documentary": {
        "palette": GENRE_PALETTE_FAMILIES["historical-documentary"][0],
        "personality": "humanist",
        "geometry": "editorial_fine",
        "texture": "paper",
        "motion_style": "editorial_smooth",
        "caption_style": "documentary_small",
        "map_style": "archival_sepia",
        "chart_style": "editorial",
        "image_treatment": "warm_archival",
        "transition_grammar": "editorial_slides",
        "corner_radius": 2,
        "stroke_width": 2,
    },
    "military-tactical": {
        "palette": GENRE_PALETTE_FAMILIES["military-tactical"][0],
        "personality": "condensed",
        "geometry": "brutalist",
        "texture": "clean",
        "motion_style": "restrained",
        "caption_style": "boxed_news",
        "map_style": "satellite_tactical",
        "chart_style": "bold_data",
        "image_treatment": "cinematic_grade",
        "transition_grammar": "mostly_cuts",
        "corner_radius": 1,
        "stroke_width": 3,
    },
    "finance-explainer": {
        "palette": GENRE_PALETTE_FAMILIES["finance-explainer"][0],
        "personality": "neoGrotesk",
        "geometry": "sharp",
        "texture": "clean",
        "motion_style": "editorial_smooth",
        "caption_style": "documentary_small",
        "map_style": "minimal_monochrome",
        "chart_style": "editorial_white",
        "image_treatment": "cinematic_grade",
        "transition_grammar": "editorial_slides",
        "corner_radius": 4,
        "stroke_width": 2,
    },
    "true-crime": {
        "palette": GENRE_PALETTE_FAMILIES["true-crime"][0],
        "personality": "condensed",
        "geometry": "brutalist",
        "texture": "clean",
        "motion_style": "punchy",
        "caption_style": "large_emphasis",
        "map_style": "dark_desaturated",
        "chart_style": "bold_data",
        "image_treatment": "cinematic_grade",
        "transition_grammar": "mostly_cuts",
        "corner_radius": 0,
        "stroke_width": 3,
    },
    "gaming-blockbuster": {
        "palette": GENRE_PALETTE_FAMILIES["gaming-blockbuster"][0],
        "personality": "voxBold",
        "geometry": "sharp",
        "texture": "clean",
        "motion_style": "editorial_smooth",
        "caption_style": "minimal",
        "map_style": "satellite_tactical",
        "chart_style": "editorial_white",
        "image_treatment": "cinematic_grade",
        "transition_grammar": "mostly_cuts",
        "corner_radius": 4,
        "stroke_width": 2,
    },
    "minimal-editorial": {
        "palette": {
            "primary": "#FFFFFF",
            "secondary": "#D1D5DB",
            "accent": "#FACC15",
            "accent2": "#F97316",
            "background": "#0A0A0B",
            "surface": "#141416",
            "text_contrast": "#FFFFFF",
            "border": "#27272A",
            "glow": "rgba(250, 204, 21, 0.28)",
        },
        "personality": "geometric",
        "geometry": "editorial_fine",
        "texture": "clean",
        "motion_style": "editorial_smooth",
        "caption_style": "minimal",
        "map_style": "minimal_monochrome",
        "chart_style": "editorial_white",
        "image_treatment": "cinematic_grade",
        "transition_grammar": "mostly_cuts",
        "corner_radius": 6,
        "stroke_width": 1,
    },
    "science-nature": {
        "palette": GENRE_PALETTE_FAMILIES["science-nature"][0],
        "personality": "humanist",
        "geometry": "rounded",
        "texture": "clean",
        "motion_style": "cinematic_slow",
        "caption_style": "documentary_small",
        "map_style": "satellite_tactical",
        "chart_style": "editorial",
        "image_treatment": "cinematic_grade",
        "transition_grammar": "editorial_slides",
        "corner_radius": 8,
        "stroke_width": 2,
    },
}


def _niche_preset(genre: str, personality: str, geometry: str, motion: str, map_style: str, chart_style: str, transitions: str) -> Dict[str, Any]:
    return {
        "palette": GENRE_PALETTE_FAMILIES[genre][0],
        "personality": personality,
        "geometry": geometry,
        "texture": "clean",
        "motion_style": motion,
        "caption_style": "documentary_small",
        "map_style": map_style,
        "chart_style": chart_style,
        "image_treatment": "cinematic_grade",
        "transition_grammar": transitions,
        "corner_radius": 2 if geometry == "sharp" else 4,
        "stroke_width": 2,
    }


GENRE_PRESETS.update({
    "sports-documentary": _niche_preset("sports-documentary", "condensed", "sharp", "precise_action", "dark_desaturated", "bold_data", "mostly_cuts"),
    "politics-explainer": _niche_preset("politics-explainer", "neoGrotesk", "editorial_fine", "editorial_smooth", "minimal_monochrome", "editorial_white", "mostly_cuts"),
    "travelogue": _niche_preset("travelogue", "humanist", "open", "cinematic_slow", "satellite_tactical", "editorial", "editorial_slides"),
    "automotive-documentary": _niche_preset("automotive-documentary", "technical", "sharp", "technical_crisp", "dark_desaturated", "editorial_white", "technical_wipes"),
    "biographical-essay": _niche_preset("biographical-essay", "geometric", "framed", "cinematic_slow", "archival_sepia", "editorial", "archival_burns"),
    "cultural-chronicle": _niche_preset("cultural-chronicle", "humanist", "open", "editorial_smooth", "minimal_monochrome", "editorial", "editorial_slides"),
    "health-science": _niche_preset("health-science", "humanist", "sharp", "restrained", "minimal_monochrome", "editorial_white", "mostly_cuts"),
})


def compute_video_seed(user_id: Any = "", project_id: Any = "", script_text: str = "") -> int:
    """Deterministic hash seed: identical inputs produce identical visual selections."""
    raw = f"{user_id}:{project_id}:{script_text.strip()[:1000]}"
    digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()
    return int(digest[:12], 16)


def detect_genre(topic: str = "", category: str = "", script: str = "") -> str:
    """Resolve the shared niche classifier to a renderable visual DNA preset."""
    from ..editing_profile import classify_niche

    niche = classify_niche(topic, category, script)
    # History is a broad category: royalty can use its historical subgenre
    # without changing the user's selected editing niche.
    if niche == 'history' and classify_niche(topic, '', script) == 'royal':
        return 'royal-history'
    genre_by_niche = {
        "aviation": "technology", "maritime": "technology", "engineering": "technology",
        "business": "finance-explainer", "food": "travelogue", "architecture": "travelogue", "education": "science-nature",
        "crime": "true-crime",
        "sports": "sports-documentary",
        "gaming": "gaming-blockbuster",
        "history": "historical-documentary",
        "royal": "royal-history",
        "travel": "travelogue",
        "finance": "finance-explainer",
        "technology": "technology",
        "science": "science-nature",
        "health": "health-science",
        "politics": "politics-explainer",
        "military": "military-tactical",
        "nature": "science-nature",
        "biography": "biographical-essay",
        "automotive": "automotive-documentary",
        "culture": "cultural-chronicle",
    }
    genre = genre_by_niche.get(niche, "investigative-documentary")
    return genre if genre in GENRE_PRESETS else "investigative-documentary"


def score_palette_by_topic(
    topic: str = "",
    category: str = "",
    script: str = "",
    seed: int = 0,
    genre: str = "investigative-documentary"
) -> Dict[str, str]:
    """Score and resolve bespoke 4-color palette based on subject matter and seed.
    
    Eliminates cliché generic Remotion defaults (no forced blue/purple).
    Every video gets an authentic, topic-matched editorial palette with seed variance.
    """
    text = f"{topic} {category} {script[:1500]}".lower()
    family = GENRE_PALETTE_FAMILIES.get(genre, GENRE_PALETTE_FAMILIES["investigative-documentary"])

    selected_index = 0
    if genre == "investigative-documentary":
        if any(w in text for w in ["scandal", "leak", "classified", "court", "investigate", "wiretap"]):
            selected_index = 1  # Editorial crimson
        elif any(w in text for w in ["vox", "explainer", "data", "how", "why", "breakdown"]):
            selected_index = 0  # Vox canary yellow
        elif any(w in text for w in ["monochrome", "cold", "nordic", "dossier"]):
            selected_index = 2  # Monochrome slate
        else:
            selected_index = seed % len(family)
    elif genre == "technology":
        if any(w in text for w in ["chip", "semiconductor", "silicon", "hardware", "tsmc", "intel", "nvidia"]):
            selected_index = 0  # Silicon Amber
        elif any(w in text for w in ["clean", "energy", "battery", "ev", "solar", "lithium", "grid"]):
            selected_index = 1  # Emerald wafer / Clean tech
        elif any(w in text for w in ["superconductor", "quantum", "laser", "ai", "neural", "photon"]):
            selected_index = 2  # Laser Coral
        else:
            selected_index = seed % len(family)
    elif genre == "true-crime":
        if any(w in text for w in ["fbi", "cia", "detective", "undercover", "wiretap", "informant"]):
            selected_index = 1  # Undercover ochre
        elif any(w in text for w in ["forensic", "cold case", "evidence", "autopsy"]):
            selected_index = 2  # Evidence red
        else:
            selected_index = 0  # Blood crimson
    elif genre == "finance-explainer":
        if any(w in text for w in ["gold", "bullion", "swiss", "reserve", "sovereign", "treasury", "currency"]):
            selected_index = 1  # Swiss Bullion gold
        elif any(w in text for w in ["crash", "crisis", "bear", "collapse", "bankrupt", "bubble"]):
            selected_index = 2  # Terminal emerald / bearish indicator
        else:
            selected_index = 0  # Wall street mint
    elif genre == "gaming-blockbuster":
        if any(w in text for w in ["vice city", "florida", "miami", "sunset", "leonida", "gta"]):
            selected_index = 0  # Neon flamingo
        else:
            selected_index = 1  # Arcade vermillion
    elif genre == "science-nature":
        if any(w in text for w in ["ocean", "marine", "sea", "abyss", "water", "coral", "trench"]):
            selected_index = 2  # Oceanic teal
        elif any(w in text for w in ["forest", "wildlife", "canopy", "plant", "amazon", "rainforest"]):
            selected_index = 1  # Canopy green
        else:
            selected_index = 0  # Solar flare
    elif genre == "royal-history":
        if any(w in text for w in ["parchment", "archive", "ancient", "manuscript", "charter"]):
            selected_index = 1  # Archival brass
        else:
            selected_index = 0  # Imperial gold
    else:
        selected_index = seed % len(family)

    base_palette = dict(family[selected_index % len(family)])
    return base_palette


def generate_visual_dna(
    topic: str = "",
    category: str = "",
    script: str = "",
    channel_brand: Optional[Mapping[str, Any]] = None,
    seed: Optional[int] = None,
    genre_override: Optional[str] = None,
) -> VisualDNA:
    """Generate a complete, coherent VisualDNA for the video project.
    
    Adheres to the 3-tier hierarchy:
      1. Channel Brand (persistent constraints like preferred/forbidden colors)
      2. Documentary Visual DNA (generated per project: custom 4-color palette)
      3. Scene Variation (governed downstream)
    """
    resolved_seed = int(seed if seed is not None else compute_video_seed(topic, category, script))
    genre = genre_override if genre_override in GENRE_PRESETS else detect_genre(topic, category, script)
    preset = GENRE_PRESETS.get(genre, GENRE_PRESETS["investigative-documentary"])

    # Resolve bespoke topic-scored 4-color palette
    palette = score_palette_by_topic(topic, category, script, resolved_seed, genre)
    personality_key = preset.get("personality", "neoGrotesk")
    font_spec = dict(FONT_PERSONALITIES.get(personality_key, FONT_PERSONALITIES["neoGrotesk"]))

    # Apply Channel Branding overrides if specified
    if isinstance(channel_brand, Mapping):
        preferred_accent = channel_brand.get("accent_color") or channel_brand.get("brand_color")
        if isinstance(preferred_accent, str) and re.fullmatch(r"#[a-fA-F0-9]{3}(?:[a-fA-F0-9]{3})?", preferred_accent):
            if len(preferred_accent) == 4:
                preferred_accent = '#' + ''.join(value * 2 for value in preferred_accent[1:])
            palette["accent"] = preferred_accent
            rgb = tuple(int(preferred_accent.lstrip('#')[offset:offset + 2], 16) for offset in (0, 2, 4))
            palette["glow"] = f"rgba({rgb[0]}, {rgb[1]}, {rgb[2]}, 0.35)"

        preferred_bg = channel_brand.get("background_color")
        if preferred_bg and isinstance(preferred_bg, str):
            palette["background"] = preferred_bg

        preferred_font = channel_brand.get("font_family")
        if preferred_font and isinstance(preferred_font, str) and not re.search(
            r"playfair|cinzel|lora|georgia|times|serif|courier|typewriter|impact",
            preferred_font,
            flags=re.IGNORECASE,
        ):
            font_spec["display"] = preferred_font
            font_spec["body"] = preferred_font

    typography = {
        "display": font_spec["display"],
        "body": font_spec["body"],
        "numbers": font_spec["numbers"],
        "personality": personality_key,
    }

    # High-contrast data journalism theme selection
    chart_style = preset.get("chart_style", "editorial_white")

    return VisualDNA(
        genre=genre,
        palette=palette,
        typography=typography,
        geometry=preset.get("geometry", "sharp"),
        texture=preset.get("texture", "clean"),
        motion_style=preset.get("motion_style", "restrained"),
        caption_style=preset.get("caption_style", "documentary_small"),
        map_style=preset.get("map_style", "dark_desaturated"),
        chart_style=chart_style,
        image_treatment=preset.get("image_treatment", "cinematic_grade"),
        transition_grammar=preset.get("transition_grammar", "mostly_cuts"),
        corner_radius=int(preset.get("corner_radius", 4)),
        stroke_width=int(preset.get("stroke_width", 2)),
        seed=resolved_seed,
    )
