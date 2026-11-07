"""
Component manifest system for the Native Documentary Composition Engine.

Every component has:
  - id: unique identifier the LLM selects from
  - category: grouping for template packs
  - render_mode: how FFmpeg renders it (drawtext, overlay, zoompan, xfade, etc.)
  - performance_cost: 1-5 (text=1, animated=2, blur=3, particles=4, heavy=5)
  - inputs: typed parameters the LLM fills in
  - style_tags: for template pack filtering
  - ffmpeg_template: the FFmpeg filter graph snippet
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ═══════════════════════════════════════════════════════════════════
# Performance cost limits for 8 GB machine
# ═══════════════════════════════════════════════════════════════════
PERFORMANCE_LIMITS = {
    "max_scene_cost": 5,
    "max_simultaneous_overlays": 3,
    "max_blur_layers": 1,
    "max_animated_text_layers": 2,
    "max_heavy_effects": 1,
}

# ═══════════════════════════════════════════════════════════════════
# Component Registry
# ═══════════════════════════════════════════════════════════════════

COMPONENTS: Dict[str, Dict[str, Any]] = {
    # ── Documentary Text ──
    "chapter_title": {
        "id": "chapter_title",
        "category": "documentary_text",
        "label": "Chapter Title",
        "render_mode": "drawtext",
        "performance_cost": 1,
        "duration_min": 1.5,
        "duration_max": 5.0,
        "inputs": {
            "text": "string",
            "kicker": "string",
            "font_size": "number=96",
            "font_file": "string=Poppins-Bold.ttf",
            "color": "string=#f4f0e6",
            "accent_color": "string=#f2c14e",
            "anim": "string=scale_in",
        },
        "style_tags": ["chapter", "bold", "serious"],
        "supports_proxy": True,
    },
    "keyword_emphasis": {
        "id": "keyword_emphasis",
        "category": "documentary_text",
        "label": "Keyword Emphasis",
        "render_mode": "drawtext",
        "performance_cost": 1,
        "duration_min": 1.5,
        "duration_max": 4.0,
        "inputs": {
            "text": "string",
            "detail": "string",
            "font_size": "number=88",
            "font_file": "string=Poppins-Bold.ttf",
            "color": "string=#f4f0e6",
            "accent_color": "string=#f2c14e",
            "anim": "string=scale_pop",
        },
        "style_tags": ["emphasis", "reveal"],
        "supports_proxy": True,
    },
    "quote_card": {
        "id": "quote_card",
        "category": "documentary_text",
        "label": "Quote Card",
        "render_mode": "composite",
        "performance_cost": 2,
        "duration_min": 2.0,
        "duration_max": 6.0,
        "inputs": {
            "quote": "string",
            "attribution": "string",
            "font_size": "number=56",
            "font_file": "string=Poppins-Bold.ttf",
            "color": "string=#f4f0e6",
            "accent_color": "string=#f2c14e",
        },
        "style_tags": ["testimony", "emotional"],
        "supports_proxy": True,
    },
    "date_reveal": {
        "id": "date_reveal",
        "category": "documentary_text",
        "label": "Date Reveal",
        "render_mode": "drawtext",
        "performance_cost": 1,
        "duration_min": 1.5,
        "duration_max": 3.5,
        "inputs": {
            "date": "string",
            "kicker": "string",
            "font_size": "number=100",
            "font_file": "string=Poppins-Bold.ttf",
            "color": "string=#f4f0e6",
            "accent_color": "string=#f2c14e",
        },
        "style_tags": ["timeline", "reveal"],
        "supports_proxy": True,
    },
    "location_label": {
        "id": "location_label",
        "category": "documentary_text",
        "label": "Location Label",
        "render_mode": "drawtext",
        "performance_cost": 1,
        "duration_min": 1.5,
        "duration_max": 4.0,
        "inputs": {
            "text": "string",
            "subtitle": "string",
            "font_size": "number=32",
            "font_file": "string=Poppins-Bold.ttf",
            "color": "string=#f4f0e6",
        },
        "style_tags": ["location", "informational"],
        "supports_proxy": True,
    },
    "source_citation": {
        "id": "source_citation",
        "category": "documentary_text",
        "label": "Source Citation",
        "render_mode": "drawtext",
        "performance_cost": 1,
        "duration_min": 2.0,
        "duration_max": 5.0,
        "inputs": {
            "text": "string",
            "source": "string",
            "font_size": "number=20",
            "font_file": "string=OpenSans-Bold.ttf",
        },
        "style_tags": ["evidence", "credibility"],
        "supports_proxy": True,
    },

    # ── Financial / Counter ──
    "money_counter": {
        "id": "money_counter",
        "category": "financial_counter",
        "label": "Money Counter",
        "render_mode": "drawtext+counter",
        "performance_cost": 2,
        "duration_min": 1.5,
        "duration_max": 4.0,
        "inputs": {
            "value": "number",
            "label": "string",
            "prefix": "string=$",
            "unit": "string",
            "font_size": "number=120",
            "font_file": "string=Poppins-Bold.ttf",
            "color": "string=#f2c14e",
            "label_color": "string=#f4f0e6",
        },
        "style_tags": ["finance", "impact", "serious"],
        "supports_proxy": True,
    },
    "stat_counter": {
        "id": "stat_counter",
        "category": "financial_counter",
        "label": "Statistic Counter",
        "render_mode": "drawtext+counter",
        "performance_cost": 2,
        "duration_min": 1.5,
        "duration_max": 4.0,
        "inputs": {
            "value": "number",
            "label": "string",
            "font_size": "number=120",
            "font_file": "string=Poppins-Bold.ttf",
            "color": "string=#f4f0e6",
            "accent_color": "string=#f2c14e",
        },
        "style_tags": ["statistic", "impact"],
        "supports_proxy": True,
    },
    "percentage_change": {
        "id": "percentage_change",
        "category": "financial_counter",
        "label": "Percentage Change",
        "render_mode": "drawtext+counter",
        "performance_cost": 2,
        "duration_min": 1.5,
        "duration_max": 3.5,
        "inputs": {
            "value": "number",
            "label": "string",
            "direction": "string=down",
            "font_size": "number=100",
            "font_file": "string=Poppins-Bold.ttf",
            "up_color": "string=#42d796",
            "down_color": "string=#ef6f6c",
        },
        "style_tags": ["finance", "change"],
        "supports_proxy": True,
    },

    # ── Image / Video Treatments ──
    "slow_push_in": {
        "id": "slow_push_in",
        "category": "image_treatment",
        "label": "Slow Push In (Ken Burns)",
        "render_mode": "zoompan",
        "performance_cost": 2,
        "duration_min": 2.0,
        "duration_max": 8.0,
        "inputs": {
            "src": "string",
            "zoom": "number=1.08",
            "fps": "number=30",
        },
        "style_tags": ["cinematic", "emotional", "slow"],
        "supports_proxy": True,
    },
    "slow_pull_out": {
        "id": "slow_pull_out",
        "category": "image_treatment",
        "label": "Slow Pull Out",
        "render_mode": "zoompan",
        "performance_cost": 2,
        "duration_min": 2.0,
        "duration_max": 8.0,
        "inputs": {
            "src": "string",
            "zoom": "number=1.10",
            "fps": "number=30",
        },
        "style_tags": ["cinematic", "reveal"],
        "supports_proxy": True,
    },
    "pan_left": {
        "id": "pan_left",
        "category": "image_treatment",
        "label": "Pan Left",
        "render_mode": "zoompan",
        "performance_cost": 2,
        "duration_min": 2.0,
        "duration_max": 6.0,
        "inputs": {
            "src": "string",
            "fps": "number=30",
        },
        "style_tags": ["movement", "cinematic"],
        "supports_proxy": True,
    },
    "pan_right": {
        "id": "pan_right",
        "category": "image_treatment",
        "label": "Pan Right",
        "render_mode": "zoompan",
        "performance_cost": 2,
        "duration_min": 2.0,
        "duration_max": 6.0,
        "inputs": {
            "src": "string",
            "fps": "number=30",
        },
        "style_tags": ["movement", "cinematic"],
        "supports_proxy": True,
    },
    "static_hold": {
        "id": "static_hold",
        "category": "image_treatment",
        "label": "Static Hold",
        "render_mode": "scale",
        "performance_cost": 1,
        "duration_min": 0.5,
        "duration_max": 6.0,
        "inputs": {
            "src": "string",
        },
        "style_tags": ["simple", "fast"],
        "supports_proxy": True,
    },
    "split_screen": {
        "id": "split_screen",
        "category": "image_treatment",
        "label": "Split Screen Compare",
        "render_mode": "composite",
        "performance_cost": 3,
        "duration_min": 2.0,
        "duration_max": 6.0,
        "inputs": {
            "left_src": "string",
            "right_src": "string",
            "left_label": "string",
            "right_label": "string",
        },
        "style_tags": ["comparison", "evidence"],
        "supports_proxy": True,
    },

    # ── Overlays / Atmosphere ──
    "film_grain": {
        "id": "film_grain",
        "category": "overlay",
        "label": "Film Grain",
        "render_mode": "overlay",
        "performance_cost": 1,
        "duration_min": 1.0,
        "duration_max": 30.0,
        "inputs": {
            "intensity": "number=0.04",
        },
        "style_tags": ["texture", "archive", "cinematic"],
        "supports_proxy": True,
    },
    "dark_vignette": {
        "id": "dark_vignette",
        "category": "overlay",
        "label": "Dark Vignette",
        "render_mode": "geq",
        "performance_cost": 2,
        "duration_min": 1.0,
        "duration_max": 30.0,
        "inputs": {
            "intensity": "number=0.5",
        },
        "style_tags": ["focus", "dramatic"],
        "supports_proxy": True,
    },
    "red_evidence_mark": {
        "id": "red_evidence_mark",
        "category": "overlay",
        "label": "Red Evidence Marking",
        "render_mode": "drawtext+overlay",
        "performance_cost": 2,
        "duration_min": 2.0,
        "duration_max": 6.0,
        "inputs": {
            "text": "string",
            "x": "number=0.65",
            "y": "number=0.15",
            "font_size": "number=24",
        },
        "style_tags": ["investigation", "evidence", "crime"],
        "supports_proxy": True,
    },

    # ── Transitions ──
    "flash_cut": {
        "id": "flash_cut",
        "category": "transition",
        "label": "Flash Cut",
        "render_mode": "xfade",
        "performance_cost": 1,
        "duration_min": 0.1,
        "duration_max": 0.5,
        "inputs": {},
        "style_tags": ["fast", "energetic"],
        "supports_proxy": True,
    },
    "dip_black": {
        "id": "dip_black",
        "category": "transition",
        "label": "Dip to Black",
        "render_mode": "fade+xfade",
        "performance_cost": 1,
        "duration_min": 0.2,
        "duration_max": 1.0,
        "inputs": {},
        "style_tags": ["dramatic", "chapter"],
        "supports_proxy": True,
    },
    "slide_left": {
        "id": "slide_left",
        "category": "transition",
        "label": "Slide Left",
        "render_mode": "xfade",
        "performance_cost": 2,
        "duration_min": 0.2,
        "duration_max": 0.8,
        "inputs": {},
        "style_tags": ["movement", "dynamic"],
        "supports_proxy": True,
    },
    "glitch_transition": {
        "id": "glitch_transition",
        "category": "transition",
        "label": "Glitch Transition",
        "render_mode": "xfade+rgb_split",
        "performance_cost": 3,
        "duration_min": 0.15,
        "duration_max": 0.5,
        "inputs": {},
        "style_tags": ["tech", "intense", "crime"],
        "supports_proxy": True,
    },
    "light_leak_transition": {
        "id": "light_leak_transition",
        "category": "transition",
        "label": "Light Leak",
        "render_mode": "xfade+overlay",
        "performance_cost": 2,
        "duration_min": 0.3,
        "duration_max": 0.8,
        "inputs": {},
        "style_tags": ["cinematic", "warm"],
        "supports_proxy": True,
    },

    # ── Audio ──
    "voiceover": {
        "id": "voiceover",
        "category": "audio",
        "label": "Voiceover Track",
        "render_mode": "audio",
        "performance_cost": 1,
        "duration_min": 0,
        "duration_max": 0,
        "inputs": {
            "src": "string",
            "volume": "number=1.0",
        },
        "style_tags": ["narration"],
        "supports_proxy": True,
    },
    "background_music": {
        "id": "background_music",
        "category": "audio",
        "label": "Background Music",
        "render_mode": "audio+sidechain",
        "performance_cost": 1,
        "duration_min": 0,
        "duration_max": 0,
        "inputs": {
            "src": "string",
            "volume": "number=0.15",
            "duck_volume": "number=0.04",
        },
        "style_tags": ["ambient", "mood"],
        "supports_proxy": True,
    },
    "sfx_hit": {
        "id": "sfx_hit",
        "category": "audio",
        "label": "SFX Impact",
        "render_mode": "audio",
        "performance_cost": 1,
        "duration_min": 0,
        "duration_max": 0,
        "inputs": {
            "src": "string",
            "start": "number",
            "volume": "number=0.25",
        },
        "style_tags": ["emphasis"],
        "supports_proxy": True,
    },
}


def get_component(component_id: str) -> Optional[Dict[str, Any]]:
    """Get a component definition by ID."""
    return COMPONENTS.get(component_id)


def get_components_by_category(category: str) -> List[Dict[str, Any]]:
    """Get all components in a category."""
    return [c for c in COMPONENTS.values() if c["category"] == category]


def get_components_by_tags(tags: List[str]) -> List[Dict[str, Any]]:
    """Get components matching style tags."""
    tag_set = set(tags)
    return [c for c in COMPONENTS.values() if tag_set & set(c.get("style_tags", []))]


def get_llm_component_catalog() -> List[Dict[str, Any]]:
    """Return a simplified catalog for the LLM to select from."""
    return [
        {
            "id": c["id"],
            "label": c["label"],
            "category": c["category"],
            "cost": c["performance_cost"],
            "inputs": c["inputs"],
            "tags": c["style_tags"],
        }
        for c in COMPONENTS.values()
    ]
