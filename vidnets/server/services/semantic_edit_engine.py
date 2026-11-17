"""Semantic documentary edit planning shared by the API, renderer, and Remotion.

The existing documentary planner is intentionally kept as the cue-detection layer.
This module converts its final scene contracts into a small, versioned manifest that
can be rendered by either the Electron Player or the hybrid FFmpeg export path.
There are no network calls here: planning is deterministic, fast, and safe to cache.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .editing_libraries import (
    FONT_LIBRARY,
    IMAGE_TREATMENT_LIBRARY,
    OVERLAY_LIBRARY,
    SFX_PACKS,
    TRANSITION_PACKS,
    library_manifest,
)
from .editing_profile import deterministic_profile, normalize_profile
from .edit_variation_audit import audit_edit_variation
from .text_position_contract import ALLOWED_TEXT_POSITIONS
from .documentary_template_system import (
    art_direction_pack,
    catalog_manifest,
    resolve_template_design,
)
from .highlight_selector import highlight_tokens_for, select_grounded_highlight


MANIFEST_VERSION = 1


THEME_PROFILES: Dict[str, Dict[str, Any]] = {
    "vox_clarity": {
        "id": "vox_clarity",
        "label": "Clarity Explainer",
        "palette": {"background": "#0b1320", "surface": "#16263b", "primary": "#f4f0e6", "accent": "#f2c14e", "secondary": "#49a6d8", "danger": "#ef6f6c"},
        "font_family": "Inter, Arial, sans-serif",
        "display_font": "Inter, Arial, sans-serif",
        "density": "balanced",
        "pace": "measured",
        "transition_policy": [
            "cut", "slide", "wipe", "dissolve",
            "quick_zoom_in", "quick_zoom_out", "slide_left", "slide_right",
            "dip_black", "paper_tear", "page_flip",
        ],
        "graphic_density": 0.58,
        "sound_density": 0.40,
        "grade": {"style": "cool_documentary", "strength": 0.35, "grain": 0.08, "vignette": 0.12},
        "caption": {
            "color": "#ffffff",
            "background": "rgba(11,19,32,.92)",
            "position": "bottom",
            "weight": 800,
            "border_width": 3,
            "shadow": "0 3px 14px rgba(0,0,0,0.95)",
            "shadow_color": "#000000",
            "shadow_x": 5,
            "shadow_y": 5,
        },
        "safe_area": {"x": 0.08, "y": 0.08, "width": 0.84, "height": 0.84},
    },
    "magnates_cinematic": {
        "id": "magnates_cinematic",
        "label": "Cinematic Business",
        "palette": {"background": "#11100e", "surface": "#28221b", "primary": "#f3ead8", "accent": "#d8a84e", "secondary": "#b84a3c", "danger": "#e45b4e"},
        "font_family": "Inter, Arial, sans-serif",
        "display_font": "Georgia, serif",
        "density": "subtle",
        "pace": "cinematic",
        "transition_policy": ["cut", "dissolve", "quick_zoom", "black_flash"],
        "graphic_density": 0.45,
        "sound_density": 0.55,
        "grade": {"style": "warm_cinematic", "strength": 0.48, "grain": 0.13, "vignette": 0.28},
        "caption": {"color": "#fff7e8", "background": "rgba(17,16,14,.86)", "position": "bottom", "weight": 600},
        "safe_area": {"x": 0.08, "y": 0.08, "width": 0.84, "height": 0.84},
    },
    "investigative_crime": {
        "id": "investigative_crime",
        "label": "Investigative",
        "palette": {"background": "#0c0b0e", "surface": "#181216", "primary": "#ffffff", "accent": "#e50914", "secondary": "#8a9ba8", "danger": "#ff1744"},
        "font_family": "Open Sans, Roboto, Arial, sans-serif",
        "display_font": "Anton, Impact, Arial Black, sans-serif",
        "density": "dynamic",
        "pace": "tense",
        "transition_policy": ["cut", "dip_black", "glitch", "quick_zoom", "black_flash", "luma_flash"],
        "graphic_density": 0.64,
        "sound_density": 0.62,
        "grade": {"style": "noir", "strength": 0.52, "grain": 0.16, "vignette": 0.34},
        "caption": {"color": "#ffffff", "background": "rgba(12,11,14,.92)", "position": "bottom", "weight": 700},
        "safe_area": {"x": 0.08, "y": 0.08, "width": 0.84, "height": 0.84},
    },
    "history_archive": {
        "id": "history_archive",
        "label": "History Archive",
        "palette": {"background": "#1c1813", "surface": "#32291f", "primary": "#f2e5c7", "accent": "#c28b43", "secondary": "#8b9b82", "danger": "#b9553f"},
        "font_family": "Inter, Arial, sans-serif",
        "display_font": "Georgia, serif",
        "density": "balanced",
        "pace": "measured",
        # Expanded transition mix: include the vox editorial favorites
        # (paper_tear, page_flip) plus creative motion (quick_zoom, slide,
        # dip_to_black) so each scene boundary feels authored instead of a
        # plain hard cut.
        "transition_policy": [
            "cut",
            "paper_tear",
            "page_flip",
            "dissolve",
            "wipe",
            "quick_zoom_in",
            "quick_zoom_out",
            "slide_left",
            "slide_right",
            "dip_black",
        ],
        "graphic_density": 0.52,
        "sound_density": 0.46,
        "grade": {"style": "archive", "strength": 0.42, "grain": 0.2, "vignette": 0.22},
        # Bolder caption / display text so it punches through archival
        # footage and the paper-grain grade.
        "caption": {
            "color": "#ffffff",
            "background": "rgba(15,12,9,.92)",
            "position": "bottom",
            "weight": 800,
            "border_width": 3,
            "shadow": "0 3px 14px rgba(0,0,0,0.95)",
            "shadow_color": "#000000",
            "shadow_x": 5,
            "shadow_y": 5,
        },
        "safe_area": {"x": 0.08, "y": 0.08, "width": 0.84, "height": 0.84},
    },
    "travel_fieldnotes": {
        "id": "travel_fieldnotes",
        "label": "Field Notes",
        "palette": {"background": "#081712", "surface": "#10261e", "primary": "#ffffff", "accent": "#10b981", "secondary": "#fbbf24", "danger": "#f59e0b"},
        "font_family": "Poppins, Open Sans, sans-serif",
        "display_font": "Montserrat, Trebuchet MS, Arial, sans-serif",
        "density": "dynamic",
        "pace": "energetic",
        "transition_policy": ["cut", "slide", "wipe", "dissolve", "circle_open"],
        "graphic_density": 0.55,
        "sound_density": 0.5,
        "grade": {"style": "sunlit_teal", "strength": 0.32, "grain": 0.06, "vignette": 0.08},
        "caption": {"color": "#ffffff", "background": "rgba(8,23,18,.82)", "position": "bottom", "weight": 650},
        "safe_area": {"x": 0.08, "y": 0.08, "width": 0.84, "height": 0.84},
    },
    "finance_terminal": {
        "id": "finance_terminal",
        "label": "Finance Terminal",
        "palette": {"background": "#0b1110", "surface": "#14201d", "primary": "#e8f4ed", "accent": "#37d391", "secondary": "#75a99a", "danger": "#ee6b6b"},
        "font_family": "IBM Plex Sans, Arial, sans-serif",
        "display_font": "IBM Plex Mono, Consolas, monospace",
        "density": "dynamic",
        "pace": "precise",
        "transition_policy": ["cut", "slide", "quick_zoom", "wipe"],
        "graphic_density": 0.68,
        "sound_density": 0.36,
        "grade": {"style": "terminal", "strength": 0.26, "grain": 0.04, "vignette": 0.08},
        "caption": {"color": "#e8f4ed", "background": "rgba(11,17,16,.84)", "position": "bottom", "weight": 600},
        "safe_area": {"x": 0.08, "y": 0.08, "width": 0.84, "height": 0.84},
    },
    "gaming_cyberpunk": {
        "id": "gaming_cyberpunk",
        "label": "Gaming & Next-Gen",
        "palette": {
            "background": "#0E1017",
            "surface": "#181B26",
            "primary": "#FFFFFF",
            "accent": "#FF2A6D",
            "secondary": "#FFB300",
            "danger": "#D90429",
        },
        "font_family": "Inter, Arial, sans-serif",
        "display_font": "Oswald, Archivo Black, Arial Black, sans-serif",
        "density": "dynamic",
        "pace": "energetic",
        "transition_policy": [
            "cut", "quick_zoom_in", "quick_zoom_out", "slide_left", "slide_right",
            "dissolve", "wipe", "luma_flash",
        ],
        "graphic_density": 0.60,
        "sound_density": 0.50,
        "grade": {"style": "warm_cinematic", "strength": 0.35, "grain": 0.08, "vignette": 0.15},
        "caption": {
            "color": "#ffffff",
            "background": "rgba(14,16,23,.90)",
            "position": "bottom",
            "weight": 800,
            "border_width": 3,
            "shadow": "0 3px 14px rgba(0,0,0,0.95)",
            "shadow_color": "#000000",
            "shadow_x": 5,
            "shadow_y": 5,
        },
        "safe_area": {"x": 0.08, "y": 0.08, "width": 0.84, "height": 0.84},
    },
    "gaming": {
        "id": "gaming",
        "label": "Vice City & Gaming Blockbuster",
        "palette": {
            "background": "#0E1017",
            "surface": "#181B26",
            "primary": "#FFFFFF",
            "accent": "#FF2A6D",
            "secondary": "#FFB300",
            "danger": "#D90429",
        },
        "font_family": "Inter, Arial, sans-serif",
        "display_font": "Oswald, Archivo Black, Arial Black, sans-serif",
        "density": "dynamic",
        "pace": "energetic",
        "transition_policy": [
            "cut", "quick_zoom_in", "quick_zoom_out", "slide_left", "slide_right",
            "dissolve", "wipe", "luma_flash",
        ],
        "graphic_density": 0.60,
        "sound_density": 0.50,
        "grade": {"style": "vibrant_neon", "strength": 0.28, "grain": 0.05, "vignette": 0.10},
        "caption": {
            "color": "#ffffff",
            "background": "rgba(8,9,15,.88)",
            "position": "bottom",
            "weight": 800,
            "border_width": 3,
            "shadow": "0 3px 14px rgba(0,0,0,0.95)",
            "shadow_color": "#000000",
            "shadow_x": 5,
            "shadow_y": 5,
        },
        "safe_area": {"x": 0.08, "y": 0.08, "width": 0.84, "height": 0.84},
    },
}


TEMPLATE_REGISTRY: List[Dict[str, Any]] = [
    {"id": "text-card", "family": "editorial", "label": "Broadcast text card & keyword reveal", "supportedBeatRoles": ["context", "evidence", "turn", "climax", "statistic", "reveal"], "requiredProps": ["text"], "variationCount": 6, "previewProps": {"text": "A record-breaking milestone was confirmed.", "kicker": "OFFICIAL RECORD", "highlight_words": ["record-breaking milestone"]}},
    {"id": "chapter-opener", "family": "chapter", "label": "Chapter opener", "supportedBeatRoles": ["chapter", "context", "turn"], "requiredProps": ["title"], "variationCount": 4, "previewProps": {"title": "THE TURNING POINT", "kicker": "CHAPTER 02"}},
    {"id": "keyword-reveal", "family": "keyword", "label": "Keyword reveal", "supportedBeatRoles": ["context", "evidence", "turn", "climax"], "requiredProps": ["text"], "variationCount": 6, "previewProps": {"text": "THE LAST SIGNAL"}},
    {"id": "date-card", "family": "date", "label": "Date / era card", "supportedBeatRoles": ["context", "evidence", "chapter"], "requiredProps": ["label"], "variationCount": 4, "previewProps": {"label": "APRIL 15, 1912"}},
    {"id": "money-counter", "family": "money", "label": "Money counter", "supportedBeatRoles": ["evidence", "turn", "climax"], "requiredProps": ["value", "label"], "variationCount": 4, "previewProps": {"value": "12.4", "unit": "B", "label": "ESTIMATED LOSS"}},
    {"id": "stat-counter", "family": "stat", "label": "Statistic counter", "supportedBeatRoles": ["evidence", "turn", "climax"], "requiredProps": ["value", "label"], "variationCount": 5, "previewProps": {"value": "73%", "label": "OF THE DECK"}},
    {"id": "bar-chart", "family": "chart", "label": "Bar chart", "supportedBeatRoles": ["evidence", "comparison"], "requiredProps": ["items"], "variationCount": 3, "previewProps": {"items": [{"label": "A", "value": 72}, {"label": "B", "value": 48}, {"label": "C", "value": 30}]}},
    {"id": "line-chart", "family": "chart", "label": "Line chart", "supportedBeatRoles": ["evidence", "turn", "climax"], "requiredProps": ["points"], "variationCount": 3, "previewProps": {"points": [20, 34, 31, 66, 82]}},
    {"id": "timeline-trace", "family": "timeline", "label": "Timeline trace", "supportedBeatRoles": ["context", "chapter", "evidence"], "requiredProps": ["events"], "variationCount": 4, "previewProps": {"events": [{"label": "Departure", "year": "1912"}, {"label": "Impact", "year": "23:40"}]}},
    {"id": "map-route", "family": "map", "label": "Map route", "supportedBeatRoles": ["location", "context", "evidence"], "requiredProps": ["label"], "variationCount": 4, "previewProps": {"label": "NORTH ATLANTIC"}},
    {"id": "paper-explainer", "family": "paper", "label": "Paper explainer", "supportedBeatRoles": ["evidence", "context", "quote"], "requiredProps": ["title", "body"], "variationCount": 4, "previewProps": {"title": "ARCHIVE NOTE", "body": "A source-backed detail appears here."}},
    {"id": "quote-card", "family": "quote", "label": "Quote treatment", "supportedBeatRoles": ["quote", "climax", "context"], "requiredProps": ["quote"], "variationCount": 4, "previewProps": {"quote": "The sea was calm."}},
    {"id": "lower-third", "family": "lower-third", "label": "Person / place lower third", "supportedBeatRoles": ["person", "location", "context"], "requiredProps": ["title"], "variationCount": 5, "previewProps": {"title": "EDWARD SMITH", "subtitle": "CAPTAIN"}},
    {"id": "evidence-callout", "family": "callout", "label": "Evidence callout", "supportedBeatRoles": ["evidence", "turn", "climax"], "requiredProps": ["text"], "variationCount": 5, "previewProps": {"text": "WHAT CHANGED?"}},
    {"id": "split-screen", "family": "split", "label": "Split comparison", "supportedBeatRoles": ["comparison", "evidence", "turn"], "requiredProps": ["left", "right"], "variationCount": 3, "previewProps": {"left": "BEFORE", "right": "AFTER"}},
    {"id": "full-bleed", "family": "media", "label": "Full-bleed media", "supportedBeatRoles": ["context", "climax", "transition"], "requiredProps": [], "variationCount": 6, "previewProps": {}},
    {"id": "end-card", "family": "end-card", "label": "End card", "supportedBeatRoles": ["cta", "climax"], "requiredProps": ["title"], "variationCount": 3, "previewProps": {"title": "FOLLOW THE STORY"}},
]


def _template_meta(
    template_id: str,
    family: str,
    label: str,
    roles: Sequence[str],
    required: Sequence[str] = (),
    *,
    variation_count: int = 4,
    render_mode: str = "overlay",
    performance: str = "light",
    emphasis: str = "medium",
    min_media_assets: int = 0,
    preview: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Catalogue metadata used by both the editor browser and the router."""
    return {
        "id": template_id,
        "family": family,
        "label": label,
        "supportedBeatRoles": list(roles),
        "requiredProps": list(required),
        "variationCount": variation_count,
        "renderMode": render_mode,
        "performanceClass": performance,
        "emphasis": emphasis,
        "minDistinctMediaAssets": max(0, int(min_media_assets)),
        "supportedAspectRatios": ["16:9", "9:16", "1:1"],
        "previewProps": preview or {},
    }


# The original IDs remain supported for existing projects. These higher-value
# components are a reusable documentary grammar, not a pile of generic cards.
TEMPLATE_REGISTRY.extend([
    _template_meta("cold-open", "narrative", "Cinematic cold open", ["intro", "reveal", "climax"], ["title"], variation_count=5, render_mode="replace", performance="medium", emphasis="hero", preview={"title": "ONE NIGHT CHANGED EVERYTHING", "kicker": "THE QUESTION"}),
    _template_meta("profile-dossier", "profile", "Pinned profile dossier", ["person", "context", "evidence"], ["title"], variation_count=5, render_mode="overlay", performance="medium", emphasis="high", preview={"title": "EDWARD SMITH", "subtitle": "CAPTAIN"}),
    _template_meta("evidence-board", "evidence", "Evidence board", ["evidence", "turn", "reveal"], ["text"], variation_count=5, render_mode="overlay", performance="medium", emphasis="high", preview={"text": "THE RECORDS DID NOT MATCH"}),
    _template_meta("source-highlight", "document", "Source clause highlight", ["evidence", "quote"], ["body"], variation_count=4, render_mode="overlay", emphasis="medium", preview={"body": "A marked source explains the critical detail."}),
    _template_meta("listicle-build", "explanation", "Listicle build", ["context", "evidence", "comparison"], ["items"], variation_count=6, render_mode="overlay", emphasis="medium", preview={"items": ["THE SHIP", "THE WARNING", "THE DECISION"]}),
    _template_meta("cause-effect", "relationship", "Cause-and-effect diagram", ["turn", "evidence", "comparison"], ["cause", "effect"], variation_count=4, render_mode="replace", performance="medium", emphasis="high", preview={"cause": "THE COLLISION", "effect": "FLOODING"}),
    _template_meta("archival-frame", "media", "Archival photo treatment", ["context", "evidence", "transition"], (), variation_count=8, render_mode="overlay", emphasis="low", preview={}),
    _template_meta("gallery-stack", "media", "Layered media gallery", ["context", "evidence", "climax"], (), variation_count=5, render_mode="overlay", performance="medium", emphasis="medium", min_media_assets=3, preview={}),
    _template_meta("callout-arrow", "annotation", "Vision-targeted callout arrow", ["evidence", "context"], ["label", "bbox"], variation_count=4, render_mode="overlay", emphasis="medium", preview={"label": "LOOK HERE", "bbox": [0.58, 0.28, 0.76, 0.52]}),
    _template_meta("kinetic-statement", "typography", "Kinetic editorial statement", ["intro", "context", "turn", "climax"], ["text"], variation_count=7, render_mode="overlay", performance="light", emphasis="high", preview={"text": "THE SHIP WAS NOT PREPARED", "detail": "A key line becomes the visual beat."}),
    _template_meta("newsprint-reveal", "press", "Newsprint headline reveal", ["evidence", "context", "quote"], ["headline"], variation_count=6, render_mode="overlay", performance="medium", emphasis="high", preview={"headline": "THE WARNING WAS ALREADY THERE", "deck": "ARCHIVE REPORT"}),
    _template_meta("signal-scan", "signal", "Radio signal / scan treatment", ["evidence", "turn", "location"], ["message"], variation_count=5, render_mode="replace", performance="medium", emphasis="high", preview={"message": "ICE WARNING RECEIVED", "label": "WIRELESS LOG"}),
    _template_meta("blueprint-reveal", "blueprint", "Blueprint / mechanism explainer", ["context", "evidence", "comparison"], ["title"], variation_count=5, render_mode="replace", performance="medium", emphasis="high", preview={"title": "THE WATERTIGHT DESIGN", "detail": "HOW THE SYSTEM WORKED"}),
    _template_meta("decision-tree", "logic", "Decision tree explainer", ["comparison", "turn", "evidence"], ["root"], variation_count=5, render_mode="replace", performance="medium", emphasis="high", preview={"root": "THE DECISION", "left": "WAIT", "right": "ACT"}),
    _template_meta("redaction-reveal", "document", "Redaction and reveal", ["evidence", "turn", "climax"], ["text"], variation_count=5, render_mode="overlay", performance="light", emphasis="medium", preview={"text": "THE DETAIL THAT CHANGED THE STORY", "label": "RESTRICTED FILE"}),
    _template_meta("countdown-clock", "time", "Countdown / time pressure", ["turn", "climax", "evidence"], ["label"], variation_count=4, render_mode="replace", performance="medium", emphasis="high", preview={"label": "MINUTES TO RESPOND", "value": "00:40"}),
    _template_meta("photo-pinboard", "media", "Pinned-photo collage", ["person", "context", "evidence"], ["title"], variation_count=6, render_mode="overlay", performance="medium", emphasis="medium", min_media_assets=3, preview={"title": "THE PEOPLE ON BOARD", "detail": "ARCHIVE COLLECTION"}),
    _template_meta("data-race", "data", "Data race comparison", ["comparison", "evidence", "climax"], ["left"], variation_count=5, render_mode="replace", performance="medium", emphasis="high", preview={"left": "TIME", "right": "DISTANCE", "value": "02:40"}),
    _template_meta("field-caption", "caption", "Editorial field caption", ["context", "location", "person"], ["title"], variation_count=8, render_mode="overlay", emphasis="low", preview={"title": "NORTH ATLANTIC", "subtitle": "APRIL 1912"}),
    _template_meta("spotlight-lens", "focus", "Spotlight / focus lens", ["evidence", "context", "turn"], ["title"], variation_count=6, render_mode="overlay", performance="light", emphasis="medium", preview={"title": "THE DETAIL THAT MATTERED", "detail": "A focused editorial beat."}),
    _template_meta("exhibit-label", "archive", "Museum exhibit label", ["context", "evidence", "person"], ["title"], variation_count=7, render_mode="overlay", emphasis="low", preview={"title": "RMS TITANIC", "subtitle": "ARCHIVE OBJECT"}),
    _template_meta("witness-quote", "quote", "Witness testimony", ["quote", "context", "climax"], ["quote"], variation_count=6, render_mode="overlay", performance="medium", emphasis="high", preview={"quote": "The sea was calm.", "attribution": "WITNESS ACCOUNT"}),
    _template_meta("calendar-rip", "date", "Calendar-page reveal", ["context", "evidence", "chapter"], ["label"], variation_count=5, render_mode="replace", performance="medium", emphasis="medium", preview={"label": "APRIL 15, 1912", "kicker": "THE NIGHT"}),
    _template_meta("terminal-log", "signal", "Terminal / log readout", ["evidence", "turn", "location"], ["message"], variation_count=6, render_mode="replace", performance="medium", emphasis="medium", preview={"message": "POSITION CONFIRMED", "label": "LOG ENTRY"}),
    _template_meta("metric-grid", "data", "Metric-grid reveal", ["evidence", "comparison", "climax"], ["value"], variation_count=6, render_mode="replace", performance="medium", emphasis="high", preview={"value": "1500", "label": "LIVES LOST"}),
    _template_meta("route-progress", "map", "Route progress tracker", ["location", "context", "evidence"], ["to"], variation_count=5, render_mode="replace", performance="medium", emphasis="medium", preview={"from": "SOUTHAMPTON", "to": "NORTH ATLANTIC"}),
    _template_meta("index-card", "document", "Archive index-card", ["evidence", "context", "quote"], ["title"], variation_count=7, render_mode="overlay", emphasis="medium", preview={"title": "WIRELESS WARNING", "detail": "Filed before the collision."}),
    _template_meta("stamp-mark", "archive", "Archive stamp / marking", ["evidence", "turn", "transition"], ["title"], variation_count=6, render_mode="overlay", emphasis="medium", preview={"title": "VERIFIED", "subtitle": "ARCHIVE RECORD"}),
    _template_meta("word-cascade", "typography", "Word-cascade emphasis", ["intro", "context", "turn", "climax"], ["text"], variation_count=8, render_mode="overlay", performance="light", emphasis="high", preview={"text": "TOO FAST. TOO LATE.", "detail": "The central conclusion."}),
    _template_meta("case-file", "document", "Case-file breakdown", ["evidence", "person", "turn"], ["title"], variation_count=6, render_mode="overlay", performance="medium", emphasis="high", preview={"title": "THE MISSING WARNING", "detail": "CASE FILE 04"}),
])

TEMPLATE_REGISTRY.extend([
    _template_meta("donut-chart", "chart", "Donut / share explainer", ["evidence", "comparison"], ["value"], variation_count=5, render_mode="replace", performance="light", emphasis="high", preview={"value": 73, "label": "OF THE TOTAL"}),
    _template_meta("area-chart", "chart", "Animated area trend", ["evidence", "comparison", "turn"], ["points"], variation_count=5, render_mode="replace", performance="light", emphasis="high", preview={"points": [18, 27, 24, 53, 78], "label": "GROWTH OVER TIME"}),
    _template_meta("slope-chart", "chart", "Before / after slope", ["comparison", "evidence"], ["left", "right"], variation_count=5, render_mode="replace", performance="light", emphasis="high", preview={"left": 32, "right": 76, "leftLabel": "BEFORE", "rightLabel": "AFTER"}),
    _template_meta("ranked-bars", "chart", "Ranked data bars", ["comparison", "evidence"], ["items"], variation_count=6, render_mode="replace", performance="light", emphasis="high", preview={"items": [{"label": "FIRST", "value": 82}, {"label": "SECOND", "value": 61}, {"label": "THIRD", "value": 39}]}),
    _template_meta("pictogram-stat", "chart", "Human-scale pictogram", ["evidence", "climax"], ["value"], variation_count=5, render_mode="replace", performance="light", emphasis="high", preview={"value": 7, "total": 10, "label": "PEOPLE AFFECTED"}),
    _template_meta("map-comparison", "map", "Geographic transit route", ["location", "context", "evidence"], ["from", "to"], variation_count=5, render_mode="replace", performance="medium", emphasis="high", preview={"from": "LONDON", "to": "PARIS", "title": "TRANSIT ROUTE"}),
    _template_meta("metric-grid-enhanced", "data", "4-Up KPI metric dashboard", ["evidence", "comparison", "climax"], ["title"], variation_count=6, render_mode="replace", performance="medium", emphasis="high", preview={"title": "KEY PERFORMANCE METRICS"}),
    _template_meta("dossier-card", "document", "Classified intelligence file", ["person", "evidence", "turn"], ["subject"], variation_count=5, render_mode="replace", performance="medium", emphasis="high", preview={"subject": "TARGET DOSSIER", "status": "CONFIRMED"}),
    _template_meta("process-flow", "logic", "Horizontal step-by-step pipeline", ["context", "evidence", "comparison"], ["steps"], variation_count=5, render_mode="replace", performance="medium", emphasis="high", preview={"title": "PIPELINE WORKFLOW"}),
    _template_meta("media-focus", "focus", "Central focus graphic with dark shadow", ["climax", "turn", "intro"], ["title"], variation_count=5, render_mode="overlay", performance="light", emphasis="hero", preview={"title": "THE TURNING POINT"}),
])


SOUND_FAMILIES = {
    "cut": "whoosh",
    "slide": "whoosh",
    "wipe": "whoosh",
    "dissolve": "room_tone",
    "quick_zoom": "impact",
    "black_flash": "impact",
    "glitch": "ui_tick",
    "page_flip": "paper_flip",
    "ripple": "water_pulse",
    "date": "typewriter",
    "money": "impact",
    "stat": "impact",
    "chart": "ui_tick",
    "map": "map_pulse",
    "quote": "room_tone",
    "chapter": "riser",
    "callout": "ui_tick",
    "typography": "typewriter",
    "press": "paper_rustle",
    "signal": "radio_static",
    "blueprint": "pencil_mark",
    "logic": "decision_tick",
    "document": "paper_flip",
    "time": "clock_tick",
    "data": "data_tick",
    "media": "photo_slide",
    "editorial": "whoosh_soft",
    "text_card": "whoosh_soft",
}


_DATE_RE = re.compile(
    r"\b(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"\s+\d{1,2}(?!\d)(?:st|nd|rd|th)?(?:,?\s+(?:18|19|20)\d{2})?|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"\s+(?:18|19|20)\d{2}|(?:18|19|20)\d{2})\b",
    re.I,
)
_MONEY_RE = re.compile(r"(?:[$€£]\s?\d[\d,.]*|\d[\d,.]*\s?(?:million|billion|trillion|bn|mn))", re.I)
_STAT_RE = re.compile(
    r"\b\d[\d,]*(?:\.\d+)?\s?(?:%|percent|million|billion|trillion|thousand|"
    r"people|persons?|customers?|workers?|employees?|passengers?|victims?|survivors?|"
    r"deaths?|years?|ships?|countries?|goals?|matches?|runs?|wickets?|miles?|"
    r"kilometers?|kilometres?|meters?|metres?|feet|tons?|tonnes?)\b",
    re.I,
)
_NUMBER_RE = re.compile(r"(?<![A-Za-z])(?:[$€£]\s*)?\d+(?:[,.]\d+)*(?:\.\d+)?(?:\s*(?:%|percent|million|billion|trillion|thousand|bn|mn))?", re.I)
_QUOTE_RE = re.compile(r'["“”\']([^"“”\']{12,180})["“”\']')
_PERSON_RE = re.compile(
    r"\b(?:Captain|President|Prime Minister|Dr\.?|Professor)?\s*"
    r"([A-Z][a-z]+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b"
)
_LOCATION_RE = re.compile(r"\b(?:in|near|from|to|across|through|toward|between|crossed|reached|moved\s+to)\s+(?:the\s+)?([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})\b")
_PERSON_FALSE_POSITIVES = {
    "in", "on", "the", "a", "an", "april", "may", "march", "june", "july",
    "august", "september", "october", "november", "december", "january",
    "february", "north", "south", "east", "west", "world", "trade", "global",
    "center", "modern", "organization", "united", "state", "states", "private",
    "enterprise", "special", "economic", "communist", "party", "games", "studios",
    "entertainment", "pictures", "records", "productions", "corporation", "corp",
    "inc", "ltd", "media", "company", "agency", "grand", "theft", "auto",
}

_QUANTITATIVE_CHARTS = {
    "bar-chart", "line-chart", "donut-chart", "area-chart",
    "slope-chart", "ranked-bars",
}
_DATA_MOMENTS = {
    "money-counter", "stat-counter", "metric-grid", "data-race",
    "pictogram-stat",
}
_CONCEPT_DIAGRAMS = {
    "cause-effect", "decision-tree", "blueprint-reveal",
}

# The semantic template is the source of truth for both preview and export.
# Native names are retained only as a renderer adapter for the FFmpeg path.
NATIVE_TEMPLATE_MAP: Dict[str, str] = {
    "text-card": "text_card",
    "vox-highlighter": "text_card",
    "cyber-hud": "text_card",
    "investigative-memo": "text_card",
    "broadsheet-callout": "text_card",
    "frosted-glass": "text_card",
    "brutalist-punch": "text_card",
    "cold-open": "cold_open",
    "chapter-opener": "chapter_title",
    "keyword-reveal": "keyword_reveal",
    "date-card": "date_numeral",
    "money-counter": "money_counter",
    "stat-counter": "stat_counter",
    "bar-chart": "bar_chart",
    "line-chart": "bar_chart",
    "donut-chart": "percentage_gauge",
    "area-chart": "bar_chart",
    "slope-chart": "split_compare",
    "ranked-bars": "ranked_list",
    "pictogram-stat": "percentage_gauge",
    "timeline-trace": "timeline_strip",
    "map-route": "map_card",
    "map-comparison": "map_card",
    "metric-grid-enhanced": "metric_grid",
    "dossier-card": "profile_dossier",
    "process-flow": "listicle_steps",
    "media-focus": "evidence_highlight",
    "paper-explainer": "archive_canvas",
    "quote-card": "quote_card",
    "lower-third": "profile_card",
    "evidence-callout": "evidence_callout",
    "split-screen": "split_compare",
    "end-card": "end_card",
    "profile-dossier": "profile_dossier",
    "evidence-board": "evidence_highlight",
    "source-highlight": "evidence_highlight",
    "listicle-build": "listicle_steps",
    "cause-effect": "cause_effect",
    "archival-frame": "archival_caption",
    "gallery-stack": "photo_frame",
    "callout-arrow": "evidence_highlight",
    "kinetic-statement": "kinetic_statement",
    "newsprint-reveal": "newsprint_reveal",
    "signal-scan": "signal_scan",
    "blueprint-reveal": "archive_canvas",
    "decision-tree": "cause_effect",
    "redaction-reveal": "redaction_reveal",
    "countdown-clock": "countdown_clock",
    "photo-pinboard": "photo_frame",
    "data-race": "metric_grid",
    "field-caption": "field_caption",
    "spotlight-lens": "evidence_highlight",
    "exhibit-label": "exhibit_label",
    "witness-quote": "witness_quote",
    "calendar-rip": "calendar_rip",
    "terminal-log": "signal_scan",
    "metric-grid": "metric_grid",
    "route-progress": "map_card",
    "index-card": "index_card",
    "stamp-mark": "stamp_mark",
    "word-cascade": "word_cascade",
    "case-file": "case_file",
}

PREFERRED_TEMPLATE_ALIASES: Dict[str, str] = {
    "archival-caption": "field-caption",
    "date-numeral": "date-card",
    "evidence-highlight": "evidence-callout",
    "profile-card": "profile-dossier",
    "subject-grid": "profile-dossier",
    "listicle-steps": "listicle-build",
    "timeline-strip": "timeline-trace",
    "headline-center": "kinetic-statement",
    "polaroid-card": "archival-frame",
    "photo-frame": "archival-frame",
    "presenter-card": "lower-third",
    "card-overlay": "paper-explainer",
    "archive-canvas": "paper-explainer",
    "step-progress": "listicle-build",
    "map-card": "map-route",
    "cyber-hud": "text-card",
    "investigative-memo": "text-card",
    "broadsheet-callout": "text-card",
    "frosted-glass": "text-card",
    "brutalist-punch": "text-card",
    "vox-highlighter": "text-card",
    "textcard": "text-card",
    "text-card": "text-card",
}


class SemanticManifestError(ValueError):
    """Raised when a semantic manifest cannot be safely rendered."""


def stable_seed(*parts: Any) -> int:
    payload = "|".join(str(part or "") for part in parts).encode("utf-8", errors="ignore")
    return int(hashlib.sha256(payload).hexdigest()[:8], 16)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


_TRAILING_DANGLERS = {
    "to", "of", "in", "and", "or", "for", "with", "at", "by", "from", "on",
    "the", "a", "an", "as", "into", "onto", "across", "about", "is", "are",
    "was", "were", "m", "s", "t", "d", "re", "ve", "ll", "its", "their", "our",
    "your", "his", "her", "bring", "brings", "delivering", "bringing", "delivers",
    "features", "featuring", "setting", "inspired", "returning", "lead", "leads", "reacts",
    "still", "has", "have", "had", "be", "been", "being", "will", "would", "can",
    "could", "should", "isn", "isn't", "aren", "aren't", "wasn", "wasn't", "weren",
    "weren't", "don", "don't", "doesn", "doesn't", "didn", "didn't", "not", "but",
    "so", "yet", "than", "if", "that", "this", "these", "those", "which",
}
_LEADING_DANGLERS = {
    "from", "with", "by", "for", "in", "on", "at", "to", "and", "or", "nor", "as",
    "into", "onto", "after", "before", "while", "during", "the", "a", "an",
    "setting", "inspired", "returning", "s", "t", "d", "m", "re", "ve", "ll",
    "but", "so", "yet",
}


def _clean_truncate(
    text: Any,
    max_chars: int = 90,
    strip_dangling: bool = True,
    strip_leading_dangler: bool = False,
) -> str:
    """Truncate text cleanly at word boundaries without chopping words in half.
    Also strips trailing and optional leading prepositions/conjunctions.
    """
    s = str(text or "").strip()
    if not s:
        return ""
    if strip_leading_dangler:
        tokens = s.split()
        while tokens and tokens[0].lower().strip(".,;:–—- \"'“”‘’()[]{}") in _LEADING_DANGLERS:
            tokens.pop(0)
        s = " ".join(tokens).strip()
    if len(s) <= max_chars:
        result = s
    else:
        # Cut at word boundary at or before max_chars
        cut = s[:max_chars]
        if " " in cut:
            result = cut.rsplit(" ", 1)[0].strip()
        else:
            # Single word longer than max_chars
            result = cut if len(cut) < len(s) and len(s) > max_chars + 4 else s

    # Strip punctuation from ends
    result = result.strip(".,;:–—- \"'“”‘’()[]{}")

    if strip_dangling:
        tokens = result.split()
        while tokens and tokens[0].lower().strip(".,;:–—- \"'“”‘’()[]{}") in {"s", "t", "d", "m", "re", "ve", "ll"}:
            tokens.pop(0)
        while tokens and tokens[-1].lower().strip(".,;:–—- \"'“”‘’()[]{}") in _TRAILING_DANGLERS:
            tokens.pop()
        result = " ".join(tokens).strip(".,;:–—- \"'“”‘’()[]{}")

    return result or s[:max_chars].strip()


def _person_name(text: str) -> str:
    """Return a real-looking named person or duo, not a capitalized date/location pair."""
    titled = re.search(
        r"\b(Captain|President|Prime Minister|Dr\.?|Professor)\s+"
        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b",
        text or "",
    )
    if titled:
        return _norm(f"{titled.group(1)} {titled.group(2)}")
    duos = re.findall(r"\b([A-Z][a-z]+)\s+(?:and|&)\s+([A-Z][a-z]+)\b", text or "")
    if duos:
        for cand1, cand2 in reversed(duos):
            if (
                cand1.lower() not in _PERSON_FALSE_POSITIVES
                and cand2.lower() not in _PERSON_FALSE_POSITIVES
                and cand1.lower() not in _CARD_STOPWORDS
                and cand2.lower() not in _CARD_STOPWORDS
            ):
                return f"{cand1} & {cand2}"
    for match in _PERSON_RE.finditer(text or ""):
        candidate = _norm(match.group(1))
        tokens = [token.lower().strip(".") for token in candidate.split()]
        if 2 <= len(tokens) <= 3 and not any(token in _PERSON_FALSE_POSITIVES for token in tokens):
            return candidate
    return ""


def choose_theme(topic: str = "", category: str = "", requested: str = "") -> Dict[str, Any]:
    requested = _norm(requested).lower().replace("-", "_")
    if requested in THEME_PROFILES:
        return deepcopy(THEME_PROFILES[requested])
    cat = _norm(category).lower()
    if cat in {"gaming", "game", "esports"}:
        return deepcopy(THEME_PROFILES["gaming_cyberpunk"])
    haystack = f"{topic} {category}".lower()
    if any(word in haystack for word in ("gaming", "game", "gameplay", "esports", "gta", "playstation", "xbox", "cyberpunk")):
        key = "gaming_cyberpunk"
    elif any(word in haystack for word in ("crime", "murder", "investigation", "case")):
        key = "investigative_crime"
    elif any(word in haystack for word in ("finance", "money", "market", "business", "company")):
        key = "finance_terminal"
    elif any(word in haystack for word in ("travel", "journey", "destination", "city tour")):
        key = "travel_fieldnotes"
    elif any(word in haystack for word in ("history", "historical", "archive", "titanic", "war", "empire")):
        key = "history_archive"
    elif any(word in haystack for word in ("founder", "entrepreneur", "magnate", "ceo")):
        key = "magnates_cinematic"
    else:
        key = "vox_clarity"
    return deepcopy(THEME_PROFILES[key])


def _template(template_id: str) -> Dict[str, Any]:
    for item in TEMPLATE_REGISTRY:
        if item["id"] == template_id:
            return item
    return TEMPLATE_REGISTRY[0]


def _beat(raw: Optional[Mapping[str, Any]], text: str, index: int, total: int) -> Dict[str, Any]:
    value = dict(raw or {})
    role = _norm(value.get("role") or "context").lower().replace(" ", "_")
    energy = _norm(value.get("energy") or "normal").lower()
    treatment = _norm(value.get("treatment") or "none").lower().replace(" ", "_")
    role_aliases = {
        "introduction": "intro", "introduce_topic": "intro", "state_fact": "evidence",
        "statistic": "evidence", "reveal": "turn", "conclusion": "cta",
        "show_date": "evidence", "show_timeline": "evidence", "show_number": "evidence",
        "show_location": "location", "show_relationship": "comparison",
    }
    role = role_aliases.get(role, role)
    if role not in {"chapter", "intro", "context", "evidence", "turn", "climax", "quote", "location", "person", "comparison", "cta", "transition", "highlight"}:
        role = "context"
    if energy not in {"calm", "normal", "tense", "high", "urgent", "reflective", "energetic"}:
        energy = "normal"
    emphasis = _norm(value.get("emphasis") or value.get("selected_for_text") or "")
    emphasis_explicit = bool(emphasis)
    # An absent editorial decision means “let the footage breathe”. Never
    # manufacture a card from the first words of narration; that is how
    # pronouns, connective phrases, and subtitle-like fragments reached the
    # renderer.
    importance = value.get("importance", value.get("salience", 0.5))
    try:
        importance = max(0.0, min(1.0, float(importance)))
    except Exception:
        importance = 0.5
    return {
        **value,
        "role": role,
        "energy": energy,
        "treatment": treatment,
        "emphasis": emphasis[:80],
        "_emphasis_explicit": emphasis_explicit,
        "importance": round(importance, 3),
        "scene_position": round(index / max(1, total - 1), 4),
    }


def _semanticize_beat(beat: Mapping[str, Any], text: str, index: int, total: int) -> Dict[str, Any]:
    """Repair the small set of facts that must outrank an LLM guess.

    Dates, list items, quotes, locations, and narrated numbers have dedicated
    visual grammar. If a model calls one of these scenes ``keyword`` or
    ``none``, the renderer cannot recover the intended treatment later.
    """
    result = dict(beat or {})
    source = _norm(text)
    lower = source.lower()
    explicit_emphasis = str(result.get("emphasis") or "").strip()
    explicit_emphasis_tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9'%/-]*", explicit_emphasis)
    }
    source_tokens_lower = {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9'%/-]*", source)
    }
    stale_explicit_emphasis = bool(
        result.get("_emphasis_explicit")
        and explicit_emphasis_tokens
        and not (explicit_emphasis_tokens & source_tokens_lower)
    )
    date_match = _DATE_RE.search(source)
    list_signal = bool(
        re.search(
            r"(?:^(?:first|second|third|fourth|finally)\b|\b(?:first|second|third|fourth|finally),\s*|\b(?:first|second|third|fourth|finally)\s+(?:step|reason|lesson|thing|point|factor|key|way)\b|\bstep\s+\d+\b)|"
            r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:ways|reasons|steps|things|lessons|facts)\b|"
            r"\b\d+\s+(?:ways|reasons|steps|things|lessons|facts)\b|"
            r"\b(?:top|key)\s+\d+\b",
            lower,
        )
    )
    quote_match = _QUOTE_RE.search(source)
    quote_signal = bool(quote_match)
    location_signal = bool(
        re.search(r"\b(?:in|near|from|across|through|toward|between|to|crossed|reached|moved\s+to)\s+(?:the\s+)?[A-Z][A-Za-z'-]+", source)
    )
    location_match = _LOCATION_RE.search(source)
    quantitative = _quantitative_intent(source, result)

    if date_match:
        result.update({
            "role": "evidence" if index else result.get("role") or "intro",
            "treatment": "date",
            "highlight_kind": "milestone",
            "emphasis": date_match.group(0).strip(),
            "importance": max(float(result.get("importance") or 0.0), 0.76),
            "editorial_reason": result.get("editorial_reason") or "chronology requires a date treatment",
        })
    elif list_signal:
        result.update({
            "role": result.get("role") if result.get("role") in {"intro", "context", "evidence", "turn"} else "context",
            "treatment": "listicle",
            "highlight_kind": result.get("highlight_kind") if result.get("highlight_kind") not in {None, "none"} else "key_fact",
            "importance": max(float(result.get("importance") or 0.0), 0.68),
            "editorial_reason": result.get("editorial_reason") or "enumerated editorial beat",
        })
    elif quote_signal:
        result.update({
            "treatment": "quote",
            "role": "quote",
            "highlight_kind": "quote",
            "importance": max(float(result.get("importance") or 0.0), 0.66),
        })
    elif (result.get('role') == 'highlight' and result.get('_emphasis_explicit')
          and not stale_explicit_emphasis and not quantitative.get('quantitative')):
        result['treatment'] = 'keyword'
    elif _person_name(source) and not location_signal:
        result.update({
            "treatment": "person",
            "role": "person",
            "highlight_kind": "human_stakes" if result.get("highlight_kind") in {None, "none"} else result.get("highlight_kind"),
            "importance": max(float(result.get("importance") or 0.0), 0.70),
        })
    elif location_signal:
        result.update({
            "treatment": "location",
            "role": "location" if result.get("role") in {"context", "evidence", "location"} else result.get("role"),
            "highlight_kind": "location",
            "emphasis": (
                result.get("emphasis")
                if stale_explicit_emphasis
                else (location_match.group(1).strip() if location_match else result.get("emphasis"))
            ),
            "importance": max(float(result.get("importance") or 0.0), 0.64),
        })
    elif quantitative.get("quantitative"):
        result.update({
            "treatment": "counter" if result.get("treatment") not in {"chart", "counter"} else result.get("treatment"),
            "highlight_kind": "scale",
            "importance": max(float(result.get("importance") or 0.0), 0.72),
        })

    if not result.get("emphasis") and not stale_explicit_emphasis:
        # Specialized cards must carry their own exact value. Do not use the
        # whole sentence as a “counter” or “location” label.
        if result.get("treatment") == "date" and date_match:
            result["emphasis"] = date_match.group(0).strip()
        elif result.get("treatment") == "quote" and quote_match:
            result["emphasis"] = quote_match.group(1).strip()
        elif result.get("treatment") == "location" and location_match:
            result["emphasis"] = location_match.group(1).strip()
        elif result.get("treatment") == "counter":
            quantitative_match = _MONEY_RE.search(source) or _STAT_RE.search(source)
            if quantitative_match:
                result["emphasis"] = quantitative_match.group(0).strip()
    result["scene_index"] = index
    result["scene_position"] = round(index / max(1, total - 1), 4)
    return result


def _native_template_id(template_id: str) -> str:
    selected = str(template_id or '').strip().lower().replace('_', '-')
    selected = PREFERRED_TEMPLATE_ALIASES.get(selected, selected)
    return NATIVE_TEMPLATE_MAP.get(selected, '')


def _supported_remotion_variant(
    scene_type: str,
    variant: str,
    preferred_variant: str = "",
) -> Tuple[str, bool]:
    """Keep legacy editor selectors inside the current Remotion contract."""
    from .editorial_director.contract import SCENE_TYPE_VARIANTS

    supported = SCENE_TYPE_VARIANTS.get(str(scene_type or ""), ())
    if not supported or variant in supported:
        return variant, False
    if preferred_variant in supported:
        return preferred_variant, True
    return supported[0], True


def _distinct_media_assets(media_item: Mapping[str, Any]) -> int:
    """Count real, unique source assets available for a multi-image treatment."""
    seen = set()
    for candidate in [media_item, *(media_item.get("variants") or []), *(media_item.get("related_assets") or [])]:
        if not isinstance(candidate, Mapping):
            continue
        value = _norm(candidate.get("fingerprint") or candidate.get("path") or candidate.get("src") or candidate.get("url"))
        if value:
            seen.add(value)
    return len(seen)


def _quantitative_intent(text: str, beat: Mapping[str, Any], context_text: str = "") -> Dict[str, Any]:
    """Require narrated data before choosing a data visualization.

    An LLM-proposed ``chart`` or ``comparison`` treatment is advisory. It does
    not permit the renderer to fabricate a graph when the exact narration has
    no quantitative relationship.
    """
    lower = text.lower()
    ctx_lower = context_text.lower() if context_text else lower
    date_values = {match.group(0).strip().lower() for match in _DATE_RE.finditer(f"{text} {context_text}" if context_text else text)}
    values = [
        match.group(0).strip()
        for match in _NUMBER_RE.finditer(text)
        if match.group(0).strip().lower() not in date_values
    ]
    ctx_values = [
        match.group(0).strip()
        for match in _NUMBER_RE.finditer(context_text)
        if match.group(0).strip().lower() not in date_values
    ] if context_text else values

    has_stat = bool(_STAT_RE.search(text) or _MONEY_RE.search(text) or (context_text and (_STAT_RE.search(context_text) or _MONEY_RE.search(context_text))))
    share = bool(re.search(r"\b(?:share|portion|percent|percentage|out of|of the total)\b", ctx_lower))
    trend = bool(re.search(r"\b(?:rose|fell|grew|declined|increased|decreased|over time|trend|from .+ to)\b", ctx_lower))
    ranking = bool(re.search(r"\b(?:rank(?:ed|ing)?|largest|smallest|top|highest|lowest|first place|second place|combinations|cameras|alarms|detectors)\b", ctx_lower))
    comparison = bool(re.search(r"\b(?:compared with|compared to|versus|vs\.?|more than|less than|twice|half as|bypassing)\b", ctx_lower))

    if not values and ctx_values and (has_stat or share or trend or ranking or comparison):
        values = ctx_values

    if share and (values or len(ctx_values) >= 2):
        kind = "share"
        if not values and ctx_values:
            values = ctx_values
    elif trend and (len(values) >= 2 or len(ctx_values) >= 2):
        kind = "trend"
        if len(values) < 2:
            values = ctx_values
    elif ranking and (len(values) >= 2 or len(ctx_values) >= 2 or has_stat):
        kind = "ranking"
        if len(values) < 2:
            values = ctx_values
    elif comparison and (len(values) >= 2 or len(ctx_values) >= 2 or has_stat):
        kind = "comparison"
        if len(values) < 2:
            values = ctx_values
    elif has_stat and values:
        kind = "single_value"
    else:
        kind = "none"
    return {
        "kind": kind,
        "values": values[:6],
        "quantitative": kind != "none",
        "relational": kind in {"share", "trend", "ranking", "comparison"},
        "requested_treatment": _norm(beat.get("treatment") or "").lower(),
    }


def _visual_structure(template_id: str) -> str:
    """Collapse template IDs into perceptually similar layout grammars."""
    if template_id == "full-bleed":
        return "media"
    if template_id in _QUANTITATIVE_CHARTS:
        return "quantitative_chart"
    if template_id in _DATA_MOMENTS:
        return "data_moment"
    if template_id in _CONCEPT_DIAGRAMS:
        return "concept_diagram"
    family = str(_template(template_id).get("family") or "")
    if family in {"document", "paper", "press", "archive", "evidence"}:
        return "document"
    if family in {"typography", "keyword", "callout", "narrative", "chapter"}:
        return "typography"
    if family in {"map", "timeline", "date", "time", "signal"}:
        return "spatial_temporal"
    if family in {"profile", "lower-third", "caption", "focus", "annotation"}:
        return "annotation"
    if family == "quote":
        return "quote"
    if family in {"media", "split"}:
        return "media_composition"
    return family or "editorial"


def _structure_is_eligible(
    template_id: str,
    *,
    quantitative: Mapping[str, Any],
    recent_structures: Sequence[str],
    structure_usage: Mapping[str, int],
    total: int,
) -> bool:
    structure = _visual_structure(template_id)
    if template_id in _QUANTITATIVE_CHARTS and not bool(quantitative.get("relational")):
        return False
    if template_id in _DATA_MOMENTS and not bool(quantitative.get("quantitative")):
        return False
    if structure == "quantitative_chart":
        chart_cap = max(1, int((max(1, total) + 9) // 10))
        if int(structure_usage.get(structure, 0)) >= chart_cap:
            return False
        if structure in recent_structures[-5:]:
            return False
    elif structure in {"data_moment", "concept_diagram"}:
        structure_cap = max(1, int((max(1, total) + 5) // 6))
        if int(structure_usage.get(structure, 0)) >= structure_cap:
            return False
        if structure in recent_structures[-2:]:
            return False
    elif structure not in {"media", "annotation"} and structure in recent_structures[-1:]:
        return False
    return True


def _template_is_eligible(template_id: str, usage: Mapping[str, int], total: int, media_assets: int) -> bool:
    template = _template(template_id)
    minimum_assets = int(template.get("minDistinctMediaAssets") or 0)
    if media_assets < minimum_assets:
        return False
    # Repetition is a planning bug, not an acceptable visual fallback. The
    # cap is deliberately conservative for designed treatments; long-form
    # media holds may repeat because their source shots are distinct.
    if template_id in {"full-bleed", "lower-third", "archival-frame"}:
        return True
    cap = max(1, int(round(max(1, total) * 0.09)))
    if template.get("emphasis") == "low":
        cap = max(cap, 4)
    return int(usage.get(template_id, 0)) < cap


# EditorialCueV2 supplies semantically correct templates, but a contract must
# not turn into a repeated visual card. These are interchangeable grammars
# within the same meaning, so changing one does not weaken script grounding.
_CONTRACT_TEMPLATE_ALTERNATES: Dict[str, Tuple[str, ...]] = {
    "date-card": ("calendar-rip", "timeline-trace"),
    "calendar-rip": ("date-card", "timeline-trace"),
    "timeline-trace": ("date-card", "calendar-rip"),
    "listicle-build": ("timeline-trace", "word-cascade", "keyword-reveal"),
    "listicle-steps": ("listicle-build", "word-cascade", "keyword-reveal"),
    "stat-counter": ("metric-grid", "pictogram-stat", "kinetic-statement"),
    "money-counter": ("stat-counter", "metric-grid", "kinetic-statement"),
    "quote-card": ("witness-quote", "paper-explainer", "index-card"),
    "witness-quote": ("quote-card", "paper-explainer", "index-card"),
}


def _vary_contract_template(
    template_id: str,
    *,
    recent: Sequence[str],
    usage: Mapping[str, int],
    total: int,
    media_assets: int,
    quantitative: Mapping[str, Any],
    recent_structures: Sequence[str],
    structure_usage: Mapping[str, int],
) -> str:
    """Keep grounded cue templates semantic while breaking perceptual loops."""
    candidate = str(template_id or "full-bleed")
    if candidate not in recent[-2:] and _template_is_eligible(candidate, usage, total, media_assets):
        return candidate
    for alternative in _CONTRACT_TEMPLATE_ALTERNATES.get(candidate, ()):
        if alternative in recent[-2:]:
            continue
        if not _template_is_eligible(alternative, usage, total, media_assets):
            continue
        if not _structure_is_eligible(
            alternative,
            quantitative=quantitative,
            recent_structures=recent_structures,
            structure_usage=structure_usage,
            total=total,
        ):
            continue
        return alternative
    return candidate


def _choose_template(
    beat: Mapping[str, Any],
    text: str,
    index: int,
    recent: Sequence[str],
    *,
    usage: Optional[Mapping[str, int]] = None,
    total: int = 1,
    media_item: Optional[Mapping[str, Any]] = None,
    design_seed: Any = 0,
    recent_structures: Sequence[str] = (),
    structure_usage: Optional[Mapping[str, int]] = None,
    preferred_template: str = "",
    force_graphic: bool = False,
) -> str:
    usage = usage or {}
    structure_usage = structure_usage or {}
    media_item = media_item or {}
    media_assets = _distinct_media_assets(media_item)
    role = str(beat.get("role") or "context")
    treatment = str(beat.get("treatment") or "").lower()
    lower = text.lower()
    is_list = bool(re.search(r"(?:^(?:first|second|third|fourth|finally)\b|\b(?:first|second|third|fourth|finally),\s*|\b(?:first|second|third|fourth|finally)\s+(?:step|reason|lesson|thing|point|factor|key|way)\b|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s*(?:ways|reasons|steps|things|lessons|facts)\b)", lower))
    is_causal = bool(re.search(r"\b(?:caused|led to|resulted in|because|therefore|which meant|forcing)\b", lower))
    is_decision = bool(re.search(r"\b(?:decision|choice|choose|option|either|instead|versus|vs\.?|debate|dilemma)\b", lower))
    is_signal = bool(re.search(r"\b(?:radio|wireless|signal|telegram|message|transmission|operator|call|broadcast)\b", lower))
    is_press = bool(re.search(r"\b(?:newspaper|headline|press|journal|article|reported|report|front page)\b", lower))
    is_mechanism = bool(re.search(r"\b(?:engine|design|system|mechanism|blueprint|structure|compartment|technical|built|construction)\b", lower))
    is_time_pressure = bool(re.search(r"\b(?:minutes?|seconds?|hours?|countdown|deadline|before midnight|too late|race against time)\b", lower))
    time_match = re.search(r"\b\d+(?:\.\d+)?\s*(?:minutes?|mins?|seconds?|secs?|hours?|hrs?)\b", lower)
    is_redaction = bool(re.search(r"\b(?:secret|hidden|redacted|covered|suppressed|unknown|mystery|missing|revealed)\b", lower))
    person_match = _person_name(text)
    location_match = _LOCATION_RE.search(text)
    verified_target = _verified_target_bbox(media_item)
    has_evidence = bool(re.search(r"\b(?:record|report|evidence|document|log|wireless|message|warning|file|archive)\b", lower))
    quantitative = _quantitative_intent(text, beat)
    if (not preferred_template and ((index == 0 and not (treatment in {"date", "era", "timeline", "counter", "money", "statistic", "chart", "map", "location", "place", "quote", "signal", "blueprint", "mechanism", "decision", "press", "newspaper", "countdown", "time", "redaction", "document", "listicle"} or _DATE_RE.search(text) or _MONEY_RE.search(text) or _STAT_RE.search(text))) or role == "chapter" or treatment in {"title", "chapter"})):
        candidates = ["chapter-opener", "cold-open", "keyword-reveal"]
    elif role == "cta" or any(k in lower for k in ("subscribe for", "stay tuned for", "follow the story")):
        candidates = ["end-card", "keyword-reveal"]
    # Dedicated chronology and listicle families must be selected before broad
    # signal/keyword heuristics. This prevents "April 1912" or "three reasons"
    # from being rendered as a generic headline.
    elif _DATE_RE.search(text) or treatment in {"date", "timeline"}:
        candidates = ["date-card", "calendar-rip", "timeline-trace"]
    elif is_list or treatment in {"listicle"}:
        candidates = ["listicle-build", "timeline-trace", "keyword-reveal"]
    elif is_signal or treatment in {"signal", "radio"}:
        candidates = ["signal-scan", "terminal-log"]
    elif is_time_pressure or treatment in {"countdown", "time"}:
        candidates = (
            ["countdown-clock", "calendar-rip", "word-cascade", "timeline-trace"]
            if time_match
            else ["word-cascade", "timeline-trace", "kinetic-statement"]
        )
    elif treatment in {"blueprint", "mechanism"} or is_mechanism:
        candidates = ["blueprint-reveal", "cause-effect", "decision-tree", "metric-grid", "evidence-board"]
    elif treatment in {"decision"} or is_decision:
        candidates = ["decision-tree", "data-race", "split-screen", "cause-effect"]
    elif treatment in {"newspaper", "press"} or is_press:
        candidates = ["newsprint-reveal", "source-highlight", "index-card", "paper-explainer", "redaction-reveal"]
    elif treatment in {"redaction"} or is_redaction:
        candidates = ["redaction-reveal", "case-file", "evidence-board", "source-highlight", "kinetic-statement"]
    elif location_match or treatment in {"location", "map"}:
        candidates = ["map-route", "map-card", "route-progress", "field-caption"]
    elif role == 'highlight' and treatment == 'keyword' and not quantitative['quantitative']:
        candidates = ['keyword-reveal', 'kinetic-statement', 'headline-center', 'word-cascade']
    elif role == "person" or treatment == "person" or person_match:
        candidates = ["lower-third", "exhibit-label", "field-caption"]
    elif _MONEY_RE.search(text) or treatment in {"money"} or any(k in lower for k in ("budget", "billion dollars")):
        candidates = ["money-counter", "kinetic-statement", "evidence-callout", "word-cascade", "stat-counter"]
    elif quantitative["quantitative"]:
        if quantitative["kind"] == "share":
            candidates = ["donut-chart", "pictogram-stat", "stat-counter", "metric-grid", "bar-chart"]
        elif quantitative["kind"] == "trend":
            candidates = ["area-chart", "slope-chart", "line-chart", "data-race", "stat-counter"]
        elif quantitative["kind"] == "ranking":
            candidates = ["ranked-bars", "bar-chart", "metric-grid", "stat-counter"]
        elif quantitative["kind"] == "comparison":
            candidates = ["slope-chart", "bar-chart", "split-screen", "data-race", "stat-counter"]
        elif quantitative["kind"] == "single_value":
            if treatment in {"counter", "statistic"}:
                candidates = ["stat-counter", "pictogram-stat"]
            else:
                candidates = ["stat-counter", "pictogram-stat", "kinetic-statement", "evidence-callout", "word-cascade"]
        else:
            # A model asking for a chart is not permission to fabricate data.
            candidates = ["kinetic-statement", "split-screen", "evidence-callout", "word-cascade", "exhibit-label"]
    elif _STAT_RE.search(text) or treatment in {"counter", "statistic"} or any(k in lower for k in ("twenty-four hours", "24 hours", "decade")):
        if treatment in {"counter", "statistic"}:
            candidates = ["stat-counter", "pictogram-stat"]
        else:
            candidates = ["stat-counter", "pictogram-stat", "kinetic-statement", "evidence-callout"]
    elif role == "quote" or treatment == "quote" or _QUOTE_RE.search(text):
        candidates = ["witness-quote", "quote-card", "paper-explainer", "index-card"]
    elif is_causal:
        candidates = ["cause-effect", "evidence-board", "keyword-reveal"]
    elif treatment in {"paper", "document", "archive"} or has_evidence:
        candidates = ["evidence-board", "case-file", "index-card", "source-highlight", "evidence-callout", "stamp-mark"]
    elif role == "comparison":
        candidates = ["split-screen", "cause-effect", "kinetic-statement", "evidence-callout", "word-cascade"]
    elif role in {"evidence", "turn", "climax"}:
        candidates = ["text-card", "kinetic-statement", "keyword-reveal", "word-cascade", "evidence-callout", "exhibit-label", "field-caption", "lower-third"]
        if verified_target:
            candidates.insert(2, "spotlight-lens")
    else:
        candidates = ["text-card", "field-caption", "keyword-reveal", "exhibit-label", "kinetic-statement", "word-cascade", "lower-third", "archival-frame"]
        if verified_target:
            candidates.insert(1, "spotlight-lens")
    preferred = _norm(preferred_template).lower().replace("_", "-")
    preferred = PREFERRED_TEMPLATE_ALIASES.get(preferred, preferred)
    has_preferred = False
    if preferred and preferred != "full-bleed" and (any(item.get("id") == preferred for item in TEMPLATE_REGISTRY) or preferred in PREFERRED_TEMPLATE_ALIASES.values()):
        # A manually selected style is a preference for generic beats, never a
        # reason to replace a date/list/quote/stat semantic contract.
        if treatment not in {"date", "era", "timeline", "listicle", "quote", "counter", "statistic", "chart", "location", "map", "person"}:
            candidates = [preferred] + [candidate for candidate in candidates if candidate != preferred]
            has_preferred = True
    # Rotate only within the semantically valid pool when no specific preference is set.
    if not has_preferred and len(candidates) > 1:
        # The first four entries are the tight semantic tier; later entries
        # are graceful fallbacks. Rotate the tight tier without promoting a
        # merely decorative fallback above a scene-specific treatment.
        primary_span = min(4, len(candidates))
        offset = stable_seed(text, index, design_seed, "semantic-template") % primary_span
        candidates = (
            candidates[offset:primary_span]
            + candidates[:offset]
            + candidates[primary_span:]
        )
    for candidate in candidates:
        if (
            candidate not in recent[-2:]
            and _template_is_eligible(candidate, usage, total, media_assets)
            and _structure_is_eligible(
                candidate,
                quantitative=quantitative,
                recent_structures=recent_structures,
                structure_usage=structure_usage,
                total=total,
            )
        ):
            return candidate
    for candidate in candidates:
        if (
            _template_is_eligible(candidate, usage, total, media_assets)
            and _structure_is_eligible(
                candidate,
                quantitative=quantitative,
                recent_structures=recent_structures,
                structure_usage=structure_usage,
                total=total,
            )
        ):
            return candidate
    # When a requested graph lacks narrated data, or a structural budget has
    # been consumed, keep the edit semantic with a media/typography treatment.
    fallbacks = ["kinetic-statement", "exhibit-label", "field-caption", "keyword-reveal", "word-cascade"]
    offset = stable_seed(text, index, design_seed, "semantic-fallback") % len(fallbacks)
    for candidate in fallbacks[offset:] + fallbacks[:offset]:
        if (
            candidate not in recent[-2:]
            and _template_is_eligible(candidate, usage, total, media_assets)
            and _structure_is_eligible(
                candidate,
                quantitative=quantitative,
                recent_structures=recent_structures,
                structure_usage=structure_usage,
                total=total,
            )
        ):
            return candidate
    if force_graphic:
        forced_fallbacks = [
            "lower-third",
            "archival-frame",
            "field-caption",
            "exhibit-label",
            "keyword-reveal",
            "text-card",
            "kinetic-statement",
        ]
        for candidate in forced_fallbacks:
            if recent and candidate == recent[-1]:
                continue
            if _template_is_eligible(candidate, usage, total, media_assets):
                return candidate
        for candidate in forced_fallbacks:
            if recent and candidate == recent[-1]:
                continue
            if candidate in {"lower-third", "archival-frame"}:
                return candidate
        return "lower-third"
    return "full-bleed"


def _transition_family(value: Any) -> str:
    """Group transition IDs into visual families for a smoother edit rhythm."""
    transition = str(value or "").strip().lower().replace("-", "_")
    if transition in {"cut", ""}:
        return "cut"
    if any(token in transition for token in ("zoom", "squeeze")):
        return "zoom"
    if any(token in transition for token in ("slide", "smooth", "wipe", "sweep", "split", "directional")):
        return "push"
    if any(token in transition for token in ("glitch", "pixel")):
        return "glitch"
    if any(token in transition for token in ("dip_black", "black_flash", "ink_bleed")):
        return "black"
    if any(token in transition for token in ("dip_white", "white_flash", "luma", "flash")):
        return "flash"
    if any(token in transition for token in ("paper", "page", "film_burn")):
        return "paper"
    if any(token in transition for token in ("circle", "iris", "radial")):
        return "reveal"
    if transition in {"dissolve", "fade", "light_leak", "film_burn"}:
        return "fade"
    return transition


def _transition(beat: Mapping[str, Any], index: int, recent: Sequence[str], theme: Mapping[str, Any]) -> str:
    requested = _norm(beat.get("transition") or beat.get("transition_in") or "").lower().replace("-", "_")
    # To keep cuts smooth, exclude abrupt hard cuts for non-opening scenes; prioritize cinematic dissolves, smooth pushes, and fades
    allowed = [t for t in (theme.get("transition_policy") or ["dissolve", "smooth_left", "fade", "slide_left"]) if t != "cut"]
    if not allowed:
        allowed = ["dissolve", "fade", "smooth_left", "slide_left"]
    recent_families = [_transition_family(item) for item in recent if item and item != "cut"]
    # Keep one transition family from taking over a short edit.  A requested
    # dip/flash is still honoured when it is fresh, but once a family has
    # reached the one-third budget for the current timeline we choose another
    # compatible family.  This prevents the familiar "dip to black every few
    # scenes" rhythm that makes otherwise animated stills feel like a slide
    # deck while leaving deterministic fallbacks for tiny transition packs.
    family_counts = __import__("collections").Counter(recent_families)
    projected_non_cut = len(recent_families) + 1
    family_cap = max(2, int(projected_non_cut * 0.34))

    def _family_within_budget(candidate: str) -> bool:
        family = _transition_family(candidate)
        if family == "cut":
            return True
        return int(family_counts.get(family, 0)) < family_cap

    if (
        requested
        and requested in allowed
        and requested not in recent[-2:]
        and _transition_family(requested) not in recent_families[-1:]
        and _family_within_budget(requested)
    ):
        return requested
    if index == 0:
        return "cut"
    seed = stable_seed(beat.get("role"), beat.get("energy"), index, theme.get("_design_seed"))
    ordered = allowed[seed % len(allowed):] + allowed[:seed % len(allowed)]
    # Avoid repeating the same visual family at adjacent boundaries. A zoom
    # followed by another zoom or three wipes in a row feels mechanical even
    # when the IDs differ, so prefer a different family before falling back
    # to the exact-ID guard when a theme has a tiny transition pack.
    fresh = [
        candidate for candidate in ordered
        if candidate not in recent[-2:]
        and _transition_family(candidate) not in recent_families[-2:]
        and _family_within_budget(candidate)
    ]
    if fresh:
        return fresh[0]
    family_fresh = [
        candidate for candidate in ordered
        if _transition_family(candidate) != (recent_families[-1] if recent_families else "")
        and candidate != (recent[-1] if recent else "")
        and _family_within_budget(candidate)
    ]
    if family_fresh:
        return family_fresh[0]
    return next(
        (
            candidate for candidate in ordered
            if candidate != (recent[-1] if recent else "")
            and _family_within_budget(candidate)
        ),
        next((candidate for candidate in ordered if candidate != (recent[-1] if recent else "")), ordered[0]),
    )


def _motion_family(value: Any) -> str:
    """Group camera motions so adjacent scenes change direction/energy."""
    motion = str(value or "").strip().lower().replace("-", "_")
    if motion in {"", "static", "hold", "settle"}:
        return "hold"
    if motion.startswith(("zoom", "push")):
        return "zoom"
    if motion.startswith(("pan", "drift")):
        return "pan"
    if motion.startswith("rotate") or motion.startswith("tilt"):
        return "rotate"
    return motion


def _choose_flow_motion(
    options: Sequence[str],
    *,
    text: str,
    index: int,
    variation_seed: Any,
    recent_motion: Sequence[str],
    recent_still_motion: Sequence[str],
    is_still_media: bool,
) -> str:
    """Choose a deterministic camera beat without adjacent family loops."""
    pool = list(dict.fromkeys(str(item) for item in options if str(item))) or ["settle"]
    offset = stable_seed(text, index, variation_seed, "motion") % len(pool)
    ordered = pool[offset:] + pool[:offset]
    blocked = set()
    if recent_motion:
        blocked.add(_motion_family(recent_motion[-1]))
    if is_still_media and recent_still_motion:
        blocked.add(_motion_family(recent_still_motion[-1]))
    family_fresh = [candidate for candidate in ordered if _motion_family(candidate) not in blocked]
    if family_fresh:
        return family_fresh[0]
    exact_fresh = [candidate for candidate in ordered if candidate not in set(recent_motion[-1:])]
    return (exact_fresh or ordered)[0]


_ENTRY_MOTION_POOL = ("slide_left", "slide_right", "rise", "fade", "wipe", "pop", "stamp", "paper", "data")


def _vary_entry_motion(
    requested: Any,
    *,
    text: str,
    index: int,
    variation_seed: Any,
    recent: Sequence[str],
) -> str:
    """Prevent a profile's preferred entry motion from becoming a video-wide loop."""
    current = str(requested or "").strip().lower()
    if current not in _ENTRY_MOTION_POOL:
        return current
    ordered = list(dict.fromkeys((current,) + _ENTRY_MOTION_POOL))
    offset = stable_seed(text, index, variation_seed, "entry-motion") % len(ordered)
    ordered = ordered[offset:] + ordered[:offset]
    recent_block = set(recent[-2:])
    return next(
        (candidate for candidate in ordered if candidate not in recent_block),
        next((candidate for candidate in ordered if candidate != (recent[-1] if recent else "")), current),
    )


def _image_treatment(
    preferred: str,
    *,
    text: str,
    index: int,
    variation_seed: Any,
    recent: Sequence[str],
    focal_bbox: Sequence[float],
    segmentation: bool,
    distinct_assets: int,
) -> str:
    """Select a renderable scene treatment without turning a profile into a loop."""
    candidates = [
        preferred,
        "archival_white_border",
        "film_matte_16mm",
        "depth_drift_25d",
        "side_by_side_collage",
        "editorial_crop",
        "split_mask",
        "paper_reveal",
        "film_gate",
        "map_window",
        "hold",
    ]
    if focal_bbox:
        candidates.extend(["iris_focus", "subject_slide", "spotlight"])
    if segmentation:
        candidates.append("depth_separation")
    if distinct_assets >= 3:
        candidates.append("contact_sheet")
    eligible: List[str] = []
    for candidate in candidates:
        if candidate in eligible or candidate not in IMAGE_TREATMENT_LIBRARY:
            continue
        requirements = set(IMAGE_TREATMENT_LIBRARY[candidate].get("requires") or [])
        if "focal_bbox" in requirements and not focal_bbox:
            continue
        if "segmentation" in requirements and not segmentation:
            continue
        if "three_media" in requirements and distinct_assets < 3:
            continue
        eligible.append(candidate)
    if not eligible:
        return "hold"
    offset = stable_seed(text, index, variation_seed, "image-treatment") % len(eligible)
    ordered = eligible[offset:] + eligible[:offset]
    recent_families = {
        str(IMAGE_TREATMENT_LIBRARY.get(item, {}).get("family") or item)
        for item in recent[-2:]
    }
    family_fresh = [
        candidate for candidate in ordered
        if str(IMAGE_TREATMENT_LIBRARY.get(candidate, {}).get("family") or candidate)
        not in recent_families
    ]
    if family_fresh:
        return family_fresh[0]
    return next((candidate for candidate in ordered if candidate not in recent[-2:]), ordered[0])


_CARD_STOPWORDS = {
    # articles / determiners / conjunctions / prepositions
    "a", "about", "above", "across", "after", "afterward", "again", "against", "all", "almost",
    "along", "already", "also", "although", "always", "am", "among", "an", "and", "another",
    "any", "anybody", "anyone", "anything", "anyway", "are", "around", "as", "at",
    "back", "be", "because", "been", "before", "behind", "being", "below", "between",
    "beyond", "both", "but", "by", "can", "cannot", "could", "did", "do", "does", "doing",
    "down", "during", "each", "either", "else", "enough", "etc", "even", "ever", "every",
    "everybody", "everyone", "everything", "everywhere", "few", "finally", "for", "from",
    "further", "furthermore", "get", "gets", "got", "had", "has", "have", "having", "he",
    "hence", "her", "here", "here's", "hers", "herself", "him", "himself", "his", "how",
    "however", "i", "if", "in", "inc", "indeed", "instead", "into", "is", "it", "its", "itself",
    "just", "last", "lately", "later", "least", "less", "like", "likely", "made", "make",
    "many", "may", "maybe", "me", "meanwhile", "might", "more", "moreover", "most", "much",
    "must", "my", "myself", "near", "nearly", "neither", "never", "nevertheless", "next",
    "no", "nobody", "none", "noone", "nor", "not", "nothing", "now", "nowhere", "of", "off",
    "often", "on", "once", "one", "only", "onto", "or", "other", "others", "otherwise", "our",
    "ours", "ourselves", "out", "over", "per", "perhaps", "probably", "quite", "rather", "really",
    "said", "same", "say", "says", "several", "shall", "she", "should", "since", "so", "some",
    "somebody", "someone", "something", "sometimes", "somewhere", "soon", "still", "such",
    "than", "that", "that's", "the", "their", "theirs", "them", "themselves", "then", "there",
    "there's", "theres", "therefore", "these", "they", "they're", "thing", "things", "this",
    "those", "though", "through", "throughout", "thru", "thus", "to", "together", "too",
    "toward", "towards", "truly", "under", "until", "up", "upon", "us", "very", "via", "was",
    "way", "ways", "we", "we're", "well", "were", "what", "whatever", "when", "whence",
    "whenever", "where", "whereafter", "whereas", "whereby", "wherein", "whereupon", "wherever",
    "whether", "which", "while", "whither", "who", "whoever", "whole", "whom", "whose", "why",
    "will", "with", "within", "without", "would", "yet", "you", "you'd", "you'll", "you're",
    "you've", "your", "yours", "yourself", "yourselves",
    # weak narrative verbs / filler
    "built", "began", "became", "called", "carried", "created", "designed",
    "formed", "given", "known", "lived", "made", "opened", "passed", "reached",
    "remained", "returned", "seemed", "started", "stayed", "stopped", "took",
    "turned", "used", "wanted", "worked", "write", "wrote", "arguably",
    # number words
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
    "hundred", "thousand", "dozen", "half",
}
_CARD_POWER_WORDS = {
    "collision", "danger", "deadliest", "disaster", "discovered", "emergency",
    "explosion", "flooded", "impact", "lifeboats", "rescue", "revealed",
    "sank", "sinking", "struck", "survived", "unsinkable", "warning", "wreck",
    "breakthrough", "deadly", "maiden", "record", "secret", "collapse",
    "catastrophe", "iceberg", "marooned", "miraculous", "pioneer", "rivalry",
    "shipwreck", "tragedy", "tragic", "triumph", "survivors", "fatal",
    "abandoned", "crashed", "drowned", "escaped", "evacuated", "heroic",
    "legend", "legendary", "memorial", "monument", "nostalgia", "outbreak",
    "revolt", "revolution", "scandal", "siege", "slaughter", "tornado",
    "turbulence", "typhoon", "volcano", "wildfire", "wounded", "launched",
    "vanished", "victims", "voyage", "warnings", "whirlpool", "engineered",
    "evidence", "final", "changed", "decisive", "history", "subscribe", "follow",
}
_CARD_CONTEXT_WORDS = {
    "engine", "design", "system", "mechanism", "compartment", "compartments",
    "decision", "choice", "option", "warning", "newspaper", "headline", "archive",
    "route", "journey", "crossing", "transformation", "revolution", "evidence",
    "signal", "message", "deadline", "minutes", "seconds", "history", "empire",
    "verified", "marked", "object", "detail", "focus",
}
_CARD_GENERIC_WORDS = {
    "line", "star", "white", "captain", "officer", "chief", "doctor", "dr",
    "professor", "first", "second", "north", "south", "east", "west", "new",
    "york", "the", "record", "document", "archive", "location", "source",
    "company", "team", "founder", "crew", "ship", "people", "customers",
    "workers", "employees", "headquarters", "service", "market", "world",
}


def _clean_card_phrase(phrase: str) -> str:
    """Strips leading/trailing prepositions, articles, and pronouns from card text."""
    if not phrase:
        return ""
    words = phrase.strip().split()
    while words and words[0].lower().strip(".,:;!?\"'") in _CARD_STOPWORDS:
        words.pop(0)
    while words and words[-1].lower().strip(".,:;!?\"'") in _CARD_STOPWORDS:
        words.pop()
    if not words:
        return ""
    if len(words) == 1 and words[0].lower().strip(".,:;!?\"'") in _CARD_STOPWORDS:
        return ""
    return " ".join(words)


def _single_highlight_word(text: str, emphasis: str) -> str:
    """Return a single fact-aware highlight word for a text card."""
    tokens = re.findall(r"[A-Za-z0-9]+", str(text or ""))
    if not tokens:
        return ""
    emp_clean = re.findall(r"[A-Za-z0-9]+", str(emphasis or ""))
    emp_token = emp_clean[0].lower() if len(emp_clean) == 1 else ""
    candidates = []
    for idx, token in enumerate(tokens):
        lowered = token.lower()
        if lowered in _CARD_STOPWORDS:
            continue
        score = 0.0
        if emp_token and lowered == emp_token:
            score = 2000.0
        elif token.isdigit():
            if len(token) >= 3:
                score = 1000.0 + len(token) * 10.0
            else:
                score = 450.0 + len(token) * 5.0
        elif lowered in _CARD_POWER_WORDS:
            score = 500.0
        elif token[0].isupper() and lowered not in _CARD_GENERIC_WORDS and (idx > 0 or len(tokens) == 1):
            score = 300.0
        elif lowered in _CARD_CONTEXT_WORDS:
            score = 200.0
        elif lowered not in _CARD_GENERIC_WORDS and len(lowered) >= 3:
            score = 100.0
        else:
            continue
        score -= idx * 0.01
        candidates.append((score, token))
    if not candidates:
        return ""
    candidates.sort(key=lambda c: -c[0])
    return candidates[0][1]



def _graphic_props(
    template_id: str,
    text: str,
    beat: Mapping[str, Any],
    topic: str,
    focal_bbox: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    emphasis = _norm(beat.get("emphasis"))
    date = _DATE_RE.search(text)
    money = _MONEY_RE.search(text)
    stat = _STAT_RE.search(text)
    quote = _QUOTE_RE.search(text)
    person = _person_name(text)
    person_title = ""
    person_clean = person
    titled_m = re.match(r"^(Captain|President|Prime Minister|Dr\.?|Professor)\s+(.+)$", person, re.I) if person else None
    if titled_m:
        person_title = titled_m.group(1).upper()
        person_clean = titled_m.group(2).strip()
    location = _LOCATION_RE.search(text)
    word = _single_highlight_word(text, emphasis).upper()
    list_items = [
        part.strip(" -–—,.;:") for part in re.split(r"(?:;|,\s+(?:and|or)\s+|\band\b)", text)
        if len(part.strip()) >= 4
    ][:4]
    clauses = [c.strip() for c in re.split(r"[;:\n\.\!\?]|(?:\s*[—–-]\s*)|\s*,\s*(?=(?:and|but|while|whereas|as|because|although)\b)", text) if len(c.strip()) > 8]
    sub_clause = _clean_truncate(clauses[1].upper(), 90) if len(clauses) > 1 else ""
    tail_clause = _clean_truncate(clauses[-1].upper(), 90) if len(clauses) > 1 else ""

    if template_id in {"cold-open", "chapter-opener"}:
        short_topic = topic.split(" - ")[0].split(":")[0].strip() if topic else ""
        if short_topic and len(short_topic) <= 24:
            title_val = short_topic.upper()
        elif topic and topic.lower() in text.lower():
            title_val = _clean_truncate(_norm(topic).upper(), 80)
        else:
            title_val = (word if word and word.lower() not in _CARD_STOPWORDS else "") or _clean_truncate(_norm(topic).upper(), 80)
        if any(k in text.lower() for k in ("trailer", "premier", "first", "announced")):
            kicker_val = "WORLD PREMIERE"
        elif any(k in text.lower() for k in ("decade", "years", "blockbuster")):
            kicker_val = "THE BLOCKBUSTER"
        elif any(k in text.lower() for k in ("record", "records")):
            kicker_val = "RECORD BREAKER"
        else:
            kicker_val = "SPECIAL REPORT"
        return {"title": title_val, "kicker": kicker_val, "subtitle": ""}
    if template_id == "date-card":
        if not date:
            return {"variant": template_id, "no_data": True, "reason": "No narrated date"}
        value = date.group(0).strip().upper()
        return {"label": value, "date": value, "value": value, "title": "TIMELINE", "kicker": ""}
    if template_id == "money-counter":
        raw = money.group(0) if money else ""
        if not raw:
            spelled = re.search(r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\s+billion(?:\s+dollars)?\b", text, re.I)
            if spelled:
                raw = spelled.group(0).upper()
        raw = (raw or emphasis).strip("$ ")
        if not raw:
            return {"variant": template_id, "no_data": True, "reason": "No narrated monetary value"}
        label_val = "PRODUCTION BUDGET" if "budget" in text.lower() else (word or "ESTIMATED VALUE")
        return {"value": raw, "label": _clean_truncate(label_val, 80), "unit": "$"}
    if template_id == "stat-counter":
        raw = stat.group(0) if stat else ""
        if not raw:
            spelled = re.search(r"\b(?:over\s+a\s+decade|a\s+decade|decade|\d+\s+hours?|twenty[\s-]four\s*hours?)\b", text, re.I)
            if spelled:
                raw = spelled.group(0).upper()
        raw = raw or emphasis
        if not raw:
            return {"variant": template_id, "no_data": True, "reason": "No narrated metric"}
        label_val = "HOURS TO SHATTER RECORDS" if "hours" in text.lower() else ("YEARS OF ANTICIPATION" if any(k in text.lower() for k in ("decade", "years")) else (word or "KEY METRIC"))
        return {"value": _clean_truncate(raw, 18), "label": _clean_truncate(label_val, 80), "unit": ""}
    if template_id in {"bar-chart", "line-chart", "area-chart", "ranked-bars"}:
        values = [int(v.replace(",", "")) for v in re.findall(r"\b[\d,]+(?:\.\d+)?\b", text) if v.replace(",", "").isdigit()][:8]
        values = [v for v in values if not 1800 <= v <= 2100]
        if len(values) < 2:
            return {"variant": template_id, "no_data": True, "reason": "A chart requires at least two narrated values"}
        if template_id in {"line-chart", "area-chart"}:
            return {"points": values, "label": word or "TREND"}
        return {"items": [{"label": str(i + 1), "value": value} for i, value in enumerate(values)], "label": word or "COMPARISON"}
    if template_id == "donut-chart":
        raw_values = [v for v in re.findall(r"\b\d+(?:\.\d+)?\b", f"{text} {emphasis}") if not 1800 <= float(v) <= 2100]
        if not raw_values:
            return {"variant": template_id, "no_data": True, "reason": "No narrated percentage or share"}
        raw = raw_values[0]
        return {"value": min(100, float(raw)), "label": word or "OF TOTAL"}
    if template_id == "slope-chart":
        values = [float(value) for value in re.findall(r"\b\d+(?:\.\d+)?\b", text) if not 1800 <= float(value) <= 2100][:2]
        if len(values) < 2:
            return {"variant": template_id, "no_data": True, "reason": "A slope chart requires two narrated values"}
        return {"left": values[0], "right": values[1], "leftLabel": "", "rightLabel": "", "label": word or "CHANGE"}
    if template_id == "pictogram-stat":
        raw_values = [v for v in re.findall(r"\b\d+(?:\.\d+)?\b", text) if not 1800 <= float(v) <= 2100]
        if not raw_values:
            return {"variant": template_id, "no_data": True, "reason": "No narrated pictogram count"}
        raw = float(raw_values[0])
        value = max(1, min(10, round(raw / 10 if raw > 10 else raw)))
        return {"value": value, "total": 10, "label": word or "HUMAN SCALE"}
    if template_id == "timeline-trace":
        if not date:
            return {"variant": template_id, "no_data": True, "reason": "No narrated date"}
        return {"events": [{"label": word or "EVENT", "year": date.group(0)}], "label": word or "TIMELINE", "date": date.group(0)}
    if template_id == "map-route":
        locations = [match.group(1).strip() for match in _LOCATION_RE.finditer(text)]
        locations = list(dict.fromkeys(locations))
        if len(locations) < 2:
            return {"variant": template_id, "no_data": True, "reason": "A route requires two narrated locations"}
        origin, destination = locations[:2]
        return {
            "label": _clean_truncate(destination, 20),
            "location": _clean_truncate(destination, 20),
            "from": _clean_truncate(origin, 20),
            "to": _clean_truncate(destination, 20),
            "detail": "",
        }
    if template_id == "paper-explainer":
        return {"title": _clean_truncate(word or _norm(topic).upper(), 80), "body": "", "source": ""}
    if template_id == "source-highlight":
        return {"title": _clean_truncate(word or _norm(topic).upper(), 80), "body": "", "highlight": word or "", "source": ""}
    if template_id == "evidence-board":
        headline = _clean_truncate(clauses[0].upper(), 90, strip_leading_dangler=True) if clauses else (word or "CRITICAL EVIDENCE")
        return {"text": headline, "detail": "", "stamp": "EVIDENCE FILE", "source": ""}
    if template_id in {"profile-dossier", "lower-third", "profile-card"}:
        name = person_clean or ("LUCIA & JASON" if any(k in text.lower() for k in ("lucia", "jason")) else (word or _clean_truncate(_norm(topic).upper(), 80)))
        role = person_title or ("DUAL PROTAGONISTS" if any(k in text.lower() for k in ("lucia", "jason", "protagonist")) else (_clean_truncate(_norm(beat.get("role") or "SUBJECT PROFILE").upper(), 20)))
        return {"title": _clean_truncate(name, 80), "name": _clean_truncate(name, 80), "subtitle": role, "role": role, "detail": "", "stamp": "CONFIDENTIAL"}
    if template_id == "listicle-build":
        items = list_items or [word or "KEY POINT", "WHAT HAPPENED", "WHY IT MATTERED"]
        clean_items = [_clean_truncate(item.upper(), 80) for item in items[:4]]
        return {
            "title": _clean_truncate(word or _norm(topic).upper(), 80),
            "items": clean_items,
            "line1": clean_items[0] if clean_items else "KEY POINT",
            "line2": clean_items[1] if len(clean_items) > 1 else "",
            "line3": clean_items[2] if len(clean_items) > 2 else "",
        }
    if template_id == "cause-effect":
        return {"cause": _clean_truncate(clauses[0].upper(), 80, strip_leading_dangler=True) if clauses else (word or "THE EVENT"), "effect": _clean_truncate(tail_clause, 80) if tail_clause else "CONSEQUENCE", "label": ""}
    if template_id == "archival-frame":
        return {"label": _clean_truncate(word or _norm(topic).upper(), 80), "source": ""}
    if template_id == "gallery-stack":
        return {"title": _clean_truncate(word or _norm(topic).upper(), 80), "detail": ""}
    if template_id == "kinetic-statement":
        headline = _clean_truncate(clauses[0].upper(), 90, strip_leading_dangler=True) if clauses else (word or "NEXT-GEN IMMERSION")
        return {"text": headline, "title": headline, "detail": "", "accent": ""}
    if template_id == "newsprint-reveal":
        return {"headline": _clean_truncate(clauses[0].upper(), 90, strip_leading_dangler=True) if clauses else (word or "BREAKING NEWS"), "deck": "", "label": "PRESS ARCHIVE", "source": ""}
    if template_id == "signal-scan":
        return {"message": _clean_truncate(clauses[0].upper(), 90, strip_leading_dangler=True) if clauses else (word or "ACTIVE SIGNAL"), "label": "TRANSMISSION", "detail": ""}
    if template_id == "blueprint-reveal":
        return {"title": _clean_truncate(clauses[0].upper(), 90, strip_leading_dangler=True) if clauses else (word or "TECHNICAL BLUEPRINT"), "detail": "", "label": "SYSTEM SPEC"}
    if template_id == "decision-tree":
        return {"root": word or "THE DECISION", "left": "", "right": "", "label": ""}
    if template_id == "redaction-reveal":
        return {"text": _clean_truncate(clauses[0].upper(), 90, strip_leading_dangler=True) if clauses else (word or "CLASSIFIED"), "label": "RESTRICTED", "detail": ""}
    if template_id == "countdown-clock":
        match = re.search(r"\b(\d{1,2})\s*(?:minutes?|mins?|seconds?|secs?|hours?)\b", text, re.I)
        if not match:
            return {"variant": template_id, "no_data": True, "reason": "No narrated countdown"}
        value = f"00:{int(match.group(1)):02d}" if int(match.group(1)) < 60 else f"{int(match.group(1)):02d}:00"
        return {"value": value, "label": word or "TIME", "detail": ""}
    if template_id == "photo-pinboard":
        return {"title": _clean_truncate(word or _norm(topic).upper(), 80), "detail": "", "label": ""}
    if template_id == "data-race":
        values = re.findall(r"\b\d+(?:\.\d+)?\b", text)
        if not values:
            return {"variant": template_id, "no_data": True, "reason": "No narrated race value"}
        return {"left": "", "right": "", "value": values[0], "label": word or "THE RACE"}
    if template_id == "field-caption":
        label = (location.group(1) if location else "") or ("VICE CITY" if "vice city" in text.lower() else (person_clean or word or _clean_truncate(_norm(topic).upper(), 20)))
        return {"title": _clean_truncate(label, 20) or "FIELD NOTE", "location": _clean_truncate(label, 20), "subtitle": person_title or "LOCATION", "detail": ""}
    if template_id == "spotlight-lens":
        return {
            "title": _clean_truncate(word or _norm(topic).upper(), 80),
            "detail": "",
            "label": "",
            "bbox": list(focal_bbox or []),
        }
    if template_id == "exhibit-label":
        ex_title = _clean_truncate((person_clean or word or _norm(topic)).upper(), 80)
        ex_sub = person_title or ("INSPIRATION" if "inspired" in text.lower() else "KEY HIGHLIGHT")
        return {"title": ex_title, "subtitle": ex_sub, "detail": ""}
    if template_id == "witness-quote":
        raw_quote = quote.group(1) if quote else (clauses[0] if clauses else word or "")
        clean_quote = _clean_truncate(raw_quote, 48, strip_dangling=True, strip_leading_dangler=True)
        attr = person_clean or ("TESTIMONY" if quote else (word or "STATEMENT"))
        return {"quote": clean_quote, "attribution": attr}
    if template_id == "calendar-rip":
        return {"label": (date.group(0) if date else word or _clean_truncate(text, 20)).upper(), "kicker": "", "detail": ""}
    if template_id == "terminal-log":
        return {"message": _clean_truncate(clauses[0].upper(), 90, strip_leading_dangler=True) if clauses else (word or "LOG ENTRY"), "label": "TERMINAL", "detail": ""}
    if template_id == "metric-grid":
        raw_values = re.findall(r"\b\d+(?:\.\d+)?\b", text)
        raw = stat.group(0) if stat else (raw_values[0] if raw_values else "")
        if not raw:
            return {"variant": template_id, "no_data": True, "reason": "No narrated metric"}
        return {"value": raw, "label": word or "METRIC", "detail": ""}
    if template_id == "route-progress":
        place = (location.group(1) if location else "") or ("VICE CITY" if "vice city" in text.lower() else "")
        if not place:
            return {"variant": template_id, "no_data": True, "reason": "No narrated destination"}
        return {"from": "", "to": _clean_truncate(place, 20), "label": _clean_truncate(place, 20), "detail": ""}
    if template_id == "index-card":
        return {"title": _clean_truncate(word or _norm(topic).upper(), 80), "detail": "", "label": ""}
    if template_id == "stamp-mark":
        return {"title": _clean_truncate(word or _norm(topic).upper(), 80), "subtitle": "OFFICIAL", "detail": ""}
    if template_id == "word-cascade":
        return {"text": _clean_truncate(clauses[0].upper(), 90, strip_leading_dangler=True) if clauses else (word or "TOO FAST TOO LATE"), "detail": "", "label": ""}
    if template_id == "case-file":
        return {"title": _clean_truncate(word or _norm(topic).upper(), 80), "detail": "", "label": "CASE FILE"}
    if template_id == "quote-card":
        raw_quote = quote.group(1) if quote else (clauses[0] if clauses else word or "")
        clean_quote = _clean_truncate(raw_quote, 48, strip_dangling=True, strip_leading_dangler=True)
        return {"quote": clean_quote, "attribution": person or (word if not person else "")}
    if template_id == "split-screen":
        return {"left": _clean_truncate(_norm(beat.get("left") or ""), 18), "right": _clean_truncate(_norm(beat.get("right") or ""), 18)}
    if template_id == "end-card":
        short_topic = topic.split(" - ")[0].split(":")[0].strip() if topic else ""
        title_val = _clean_truncate(short_topic.upper() if short_topic else _norm(topic).upper(), 80) or "GRAND THEFT AUTO VI"
        sub_val = "SUBSCRIBE FOR UPDATES" if "subscribe" in text.lower() else "COMING SOON"
        return {"title": title_val, "subtitle": sub_val}
    if template_id == "evidence-callout":
        headline = _clean_truncate(clauses[0].upper(), 90, strip_leading_dangler=True) if clauses else (word or "CRITICAL FINDING")
        stamp_val = "TACTICAL ALERT" if any(k in text.lower() for k in ("police", "getaway", "highway")) else "OFFICIAL RECORD"
        return {"text": headline, "detail": "", "stamp": stamp_val}
    if template_id == "keyword-reveal":
        keyword_val = word if word and word.lower() not in _CARD_STOPWORDS else (clauses[0].split()[0].upper() if clauses else "BREAKTHROUGH")
        return {
            "text": _clean_truncate(keyword_val, 20),
            "detail": "",
            "highlight_words": highlight_tokens_for(text, emphasis, max_tokens=3),
            "kicker": beat.get("role", "KEY INSIGHT").upper(),
            "style": "vox-highlighter",
        }
    if template_id in {"text-card", "vox-highlighter", "cyber-hud", "investigative-memo", "broadsheet-callout", "frosted-glass", "brutalist-punch"}:
        style_val = template_id if template_id != "text-card" else str(beat.get("card_style") or beat.get("style") or "")
        if not style_val or style_val == "text-card":
            niche_ctx = str(beat.get("niche") or topic or "").lower()
            energy_ctx = str(beat.get("energy") or "normal").lower()
            if any(k in niche_ctx for k in ("tech", "cyber", "ai", "digital", "gaming", "crypto", "hack")):
                style_val = "cyber-hud"
            elif any(k in niche_ctx for k in ("crime", "murder", "fbi", "police", "war", "investigative", "spy")):
                style_val = "investigative-memo"
            elif any(k in niche_ctx for k in ("history", "royal", "bio", "monarch", "century", "empire", "news")):
                style_val = "broadsheet-callout"
            elif any(k in niche_ctx for k in ("sports", "action", "fight", "racing", "speed")) or energy_ctx in {"urgent", "high", "energetic"}:
                style_val = "brutalist-punch"
            elif any(k in niche_ctx for k in ("finance", "money", "economy", "market", "stock", "billion")):
                style_val = "frosted-glass"
            else:
                style_val = "vox-highlighter"

        display_headline = _clean_truncate(
            select_grounded_highlight(text, emphasis, max_words=4) or word or text,
            55,
            strip_leading_dangler=True,
        )
        # Only the salient words glow.  The previous implementation appended
        # the full emphasis, dates, stats, names, and locations together, so a
        # card could highlight stopwords and look like a pasted transcript.
        highlight_tokens = highlight_tokens_for(display_headline, emphasis, max_tokens=4)
        for candidate in _MONEY_RE.findall(text) + _STAT_RE.findall(text) + _DATE_RE.findall(text):
            for token in highlight_tokens_for(candidate, candidate, max_tokens=4):
                if token.lower() not in {item.lower() for item in highlight_tokens}:
                    highlight_tokens.append(token)

        if "record" in text.lower():
            kicker_val = "OFFICIAL RECORD"
        elif any(k in text.lower() for k in ("billion", "dollar", "budget", "cost", "revenue", "loss", "profit")):
            kicker_val = "FINANCIAL DATA"
        elif location and location.group(1):
            kicker_val = f"LOCATION • {location.group(1).upper()}"
        elif date:
            kicker_val = f"TIMELINE • {date.group(0).upper()}"
        elif person_clean:
            kicker_val = f"PROFILE • {person_clean.upper()}"
        else:
            kicker_val = str(beat.get("role") or "CRITICAL DETAIL").upper()

        if "confirmed" in text.lower() or "official" in text.lower():
            badge_val = "CONFIRMED"
        elif "exclusive" in text.lower() or "first" in text.lower():
            badge_val = "EXCLUSIVE"
        elif "investigation" in text.lower() or "evidence" in text.lower():
            badge_val = "EVIDENCE"
        elif money or stat:
            badge_val = "DATA POINT"
        else:
            badge_val = "REPORT"

        return {
            "text": display_headline,
            "kicker": kicker_val,
            "subtitle": sub_clause if sub_clause and sub_clause != display_headline.upper() else "",
            "highlight_words": highlight_tokens,
            "style": style_val,
            "badge": badge_val,
            "position": str(beat.get("position") or "bottom-left"),
        }
    return {"text": _clean_truncate(word or _norm(topic).upper(), 80), "detail": ""}


def _native_props_for_template(
    native_template: str,
    props: Mapping[str, Any],
    beat: Mapping[str, Any],
    text: str,
    topic: str,
) -> Dict[str, Any]:
    """Adapt canonical semantic props to the native renderer contract."""
    result = dict(props or {})
    date = _DATE_RE.search(text)
    value = str(date.group(0) if date else result.get("date") or result.get("value") or result.get("label") or result.get("title") or "").strip()
    if native_template in {"date_numeral", "date_reveal", "calendar_rip", "timeline_strip"}:
        result.update({
            "date": value.upper(),
            "value": value.upper(),
            "title": result.get("title") or "",
            "label": result.get("label") or "",
            "kicker": result.get("kicker") or "",
        })
    elif native_template in {"cold_open", "chapter_title"}:
        result.update({
            "title": _clean_truncate(str(result.get("title") or _norm(topic)).upper(), 90),
            "kicker": _clean_truncate(str(result.get("kicker") or "CHAPTER").upper(), 80),
            "subtitle": _clean_truncate(str(result.get("subtitle") or result.get("detail") or "").upper(), 90, strip_leading_dangler=True),
        })
    elif native_template in {"stat_counter"}:
        result.update({
            "value": _clean_truncate(str(result.get("value") or "100").upper(), 20),
            "label": _clean_truncate(str(result.get("label") or "TOTAL").upper(), 90, strip_leading_dangler=True),
            "unit": str(result.get("unit") or "").upper(),
        })
    elif native_template in {"money_counter"}:
        clean_val = str(result.get("value") or "0").lstrip("$ ").strip()
        result.update({
            "value": _clean_truncate(clean_val.upper(), 20),
            "label": _clean_truncate(str(result.get("label") or "VALUE").upper(), 90, strip_leading_dangler=True),
            "unit": str(result.get("unit") or "$").upper(),
        })
    elif native_template in {"location_label", "map_card"}:
        loc = result.get("location") or result.get("label") or result.get("title") or ""
        result.update({
            "location": _clean_truncate(str(loc).upper(), 80),
            "detail": _clean_truncate(str(result.get("detail") or result.get("subtitle") or "").upper(), 90, strip_leading_dangler=True),
        })
    elif native_template in {"profile_dossier", "profile_card", "presenter_card", "professional_name"}:
        name = result.get("title") or result.get("name") or result.get("text") or _norm(topic)
        result.update({
            "title": _clean_truncate(str(name).upper(), 80),
            "name": _clean_truncate(str(name).upper(), 80),
            "subtitle": _clean_truncate(str(result.get("subtitle") or result.get("role") or "").upper(), 90, strip_leading_dangler=True),
            "role": _clean_truncate(str(result.get("role") or result.get("subtitle") or "").upper(), 90, strip_leading_dangler=True),
            "detail": _clean_truncate(str(result.get("detail") or "").upper(), 90, strip_leading_dangler=True),
            "stamp": _clean_truncate(str(result.get("stamp") or "CONFIDENTIAL").upper(), 20),
        })
    elif native_template in {"kinetic_statement"}:
        result.update({
            "text": _clean_truncate(str(result.get("text") or result.get("title") or "").upper(), 90, strip_leading_dangler=True),
            "detail": _clean_truncate(str(result.get("detail") or "").upper(), 90, strip_leading_dangler=True),
        })
    elif native_template in {"evidence_callout", "evidence_highlight"}:
        result.update({
            "text": _clean_truncate(str(result.get("text") or result.get("title") or "").upper(), 90, strip_leading_dangler=True),
            "detail": _clean_truncate(str(result.get("detail") or "").upper(), 90, strip_leading_dangler=True),
            "stamp": _clean_truncate(str(result.get("stamp") or "RECORD").upper(), 80),
        })
    elif native_template in {"end_card", "cta_card"}:
        result.update({
            "title": _clean_truncate(str(result.get("title") or _norm(topic)).upper(), 90),
            "subtitle": _clean_truncate(str(result.get("subtitle") or "FOLLOW THE STORY").upper(), 90),
        })
    elif native_template in {"word_cascade", "keyword_reveal"}:
        active_text = result.get("_contract_text") or result.get("text") or result.get("title") or ""
        if result.get("_contract_text") and len(str(result.get("text") or "").split()) <= 1 and len(str(result.get("_contract_text")).split()) > 1:
            active_text = result.get("_contract_text")
        result.update({
            "text": _clean_truncate(str(active_text).upper(), 90, strip_leading_dangler=True),
            "detail": _clean_truncate(str(result.get("detail") or "").upper(), 90, strip_leading_dangler=True),
        })
    elif native_template == "listicle_steps":
        items = list(result.get("items") or [])
        result.update({
            "title": result.get("title") or "",
            "line1": result.get("line1") or (items[0] if items else ""),
            "line2": result.get("line2") or (items[1] if len(items) > 1 else ""),
            "line3": result.get("line3") or (items[2] if len(items) > 2 else ""),
        })
    elif native_template == "timeline_strip":
        events = result.get("events") or []
        labels = [str(event.get("label") or event.get("year") or "") for event in events if isinstance(event, Mapping)]
        result.update({
            "line1": result.get("line1") or (labels[0] if labels else value or ""),
            "line2": result.get("line2") or (labels[1] if len(labels) > 1 else ""),
            "line3": result.get("line3") or (labels[2] if len(labels) > 2 else ""),
        })

    # Strip internal pipeline tokens and generic debugging labels from user-facing text
    _INTERNAL_STRIP = {
        "person", "location", "stat", "statistic", "date", "highlight",
        "chapter", "quote", "archive", "context", "evidence", "turn",
        "climax", "transition", "comparison", "cta", "intro",
        "subject profile", "unknown", "none", "key highlight", "key point",
        "note", "source", "field note", "official", "confidential"
    }
    for field in ("subtitle", "role", "detail", "label"):
        val = str(result.get(field) or "").strip().lower()
        if val in _INTERNAL_STRIP:
            result[field] = ""

    return result


_DISPLAY_GENERIC_LABELS = {
    "ARCHIVE", "DOCUMENT", "LOCATION", "SOURCE", "RECORD", "NOTE", "THE FIGURE",
    "A TURNING POINT", "KEY MOMENT", "FOLLOW THE STORY", "THE STORY", "THE RECORD",
    "VALUE", "TOTAL", "COMPARISON", "OF TOTAL", "HUMAN SCALE", "THE RACE",
    "FIELD NOTE", "THE PLAN", "THE TIMELINE", "THE THREE THINGS",
}


def _grounded_display_text(candidate: Any, narration: str) -> bool:
    """Return whether a card value is a contiguous token span in narration."""
    candidate_tokens = [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9'%/-]*", str(candidate or ""))
    ]
    source_tokens = [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9'%/-]*", str(narration or ""))
    ]
    if not candidate_tokens or not source_tokens or len(candidate_tokens) > len(source_tokens):
        return False
    span = len(candidate_tokens)
    return any(
        source_tokens[index:index + span] == candidate_tokens
        for index in range(len(source_tokens) - span + 1)
    )


def _card_phrase_is_important(candidate: str, narration: str, kind_hint: str = "") -> bool:
    """Allow only grounded phrases that carry editorial meaning on their own."""
    phrase = _clean_card_phrase(candidate)
    if not phrase or not _grounded_display_text(phrase, narration):
        return False
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'%/-]*", phrase)
    lowered = {word.lower() for word in words}
    if not words or all(word in _CARD_STOPWORDS for word in lowered):
        return False
    if any(re.search(r"\d|%", word) for word in words):
        return True
    if lowered & _CARD_POWER_WORDS:
        return True
    if kind_hint == "highlight" and lowered & _CARD_CONTEXT_WORDS:
        return True
    if kind_hint in {"date", "stat", "person", "location", "chapter", "quote", "archive"}:
        return True
    named = _person_name(narration)
    if named and phrase.lower() == named.lower():
        return True
    # A proper multi-word name may be selected by the brain even when the
    # scene classifier did not label it as a person. Avoid sentence-start
    # nouns such as “Company grew” by requiring a second capitalized token.
    if len(words) >= 2 and sum(1 for word in words if word[:1].isupper()) >= 2:
        return not any(word.lower() in _CARD_GENERIC_WORDS for word in words)
    return False


def _card_font_and_size(kind: str) -> Tuple[str, int]:
    """Use a restrained automatic type system instead of per-scene roulette."""
    return {
        "stat": (FONT_BEBAS, 106),
        "date": (FONT_MONTSERRAT, 82),
        "chapter": (FONT_MONTSERRAT, 86),
        "person": (FONT_MONTSERRAT, 78),
        "location": (FONT_ROBOTO, 72),
        "quote": (FONT_POPPINS, 66),
        "archive": (FONT_ROBOTO, 72),
        "highlight": (FONT_MONTSERRAT, 76),
    }.get(kind, (FONT_MONTSERRAT, 76))

FONTS_DIR = Path(__file__).resolve().parent.parent.parent / "fonts"
FONT_BEBAS = str(FONTS_DIR / "Poppins-Bold.ttf")
FONT_MONTSERRAT = str(FONTS_DIR / "Poppins-Bold.ttf")
FONT_POPPINS = str(FONTS_DIR / "Poppins-Bold.ttf")
FONT_PLAYFAIR = str(FONTS_DIR / "OpenSans-Bold.ttf")
FONT_ANTON = str(FONTS_DIR / "Poppins-Bold.ttf")
FONT_ROBOTO = str(FONTS_DIR / "OpenSans-Bold.ttf")
FONT_ORBITRON = str(FONTS_DIR / "Poppins-Bold.ttf")
FONT_CINZEL = str(FONTS_DIR / "Poppins-Bold.ttf")


def _extract_meaningful_highlight_phrase(
    text: str,
    emphasis: str = "",
    topic: str = "",
    kind_hint: str = "",
    seed: int = 0,
) -> Tuple[str, str, str, int]:
    """Extracts a bold, meaningful phrase for a text card along with its semantic kind, font path, and size.
    Returns: (display_text, detected_kind, font_path, font_size)
    """
    clean_text = str(text or "").strip()
    if not clean_text:
        return "", "", "", 0

    # 1. Chapter / Listicle Header detection (e.g. "Number 1: The McLaren F1...", "Rank 2: Ferrari F40")
    list_match = re.search(r"(?:^|[\.\n])\s*(?:Number|No\.?|#|Rank|Item|Chapter|Step)\s*(\d+)[:\s\-–—]+([^\.,;:]+)", clean_text, re.I)
    if list_match:
        num, title_part = list_match.group(1), list_match.group(2).strip()
        title_clean = _clean_card_phrase(title_part)
        if title_clean:
            title_short = " ".join(title_clean.split()[:4]).upper()
            font, size = _card_font_and_size("chapter")
            return f"#{num} {title_short}", "chapter", font, size

    # 2. Money / Currency (e.g. "$2 billion", "$140 million", "500 million dollars")
    money_match = re.search(r"(\$[\d,]+(?:\.\d+)?\s*(?:billion|million|k|trillion|B|M)?|\b[\d,]+(?:\.\d+)?\s*(?:dollars|euros|pounds|rupees|usd)\b)", clean_text, re.I)
    if money_match:
        font, size = _card_font_and_size("stat")
        return money_match.group(1).upper(), "stat", font, size

    # 3. Compound Stats, Measurements & Performance (e.g. "450 goals in 438 matches", "250 miles per hour", "140 meters high", "500 wickets")
    stat_match = re.search(
        r"(\b[\d,]+(?:\.\d+)?\s*(?:goals?|matches?|mph|km/h|horsepower|hp|tons?|tonnes?|meters?|metres?|feet|ft|miles?|km|kilometers?|percent|%|wickets?|runs?|points?|times?|x|people|workers?|customers?|passengers?|victims?|survivors?|countries?)\b(?:\s+(?:in|of)\s+[\d,]+\s*(?:matches?|games?|years?|seconds?|mins?))?|"
        r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:\s+(?:one|two|three|four|five|six|seven|eight|nine|ten))?\s+percent\b)",
        clean_text, re.I
    )
    if stat_match:
        font, size = _card_font_and_size("stat")
        return stat_match.group(1).upper(), "stat", font, size

    # 4. Dates & Historical Years (e.g. "600 BC", "in 1992", "April 14, 1912", "In 2009")
    date_match = _DATE_RE.search(clean_text)
    if date_match:
        cleaned_date = _clean_card_phrase(date_match.group(0))
        if cleaned_date:
            font, size = _card_font_and_size("date")
            return cleaned_date.upper().strip(), "date", font, size

    if kind_hint == "location":
        location_match = _LOCATION_RE.search(clean_text)
        if location_match:
            place = _clean_card_phrase(location_match.group(1))
            if place and _card_phrase_is_important(place, clean_text, "location"):
                font, size = _card_font_and_size("location")
                return _clean_truncate(place.upper(), 90), "location", font, size

    # 5. A brain-selected phrase is allowed only when it is grounded and
    # independently meaningful.  The shared selector strips filler and caps
    # the result, so a stale model emphasis cannot paste a full sentence into
    # the frame.
    selected = select_grounded_highlight(clean_text, emphasis, max_words=4)
    if selected:
        detected = kind_hint if kind_hint in {"date", "stat", "person", "location", "chapter", "quote", "archive"} else "highlight"
        font, size = _card_font_and_size(detected)
        return _clean_truncate(selected.upper(), 90), detected, font, size

    # 6. Named people/entities are safe when the classifier identified a
    # person treatment. Otherwise do not turn sentence-capitalized words into
    # labels.
    if kind_hint == "person":
        named = _person_name(clean_text)
        if named and _card_phrase_is_important(named, clean_text, "person"):
            font, size = _card_font_and_size("person")
            return _clean_truncate(named.upper(), 90), "person", font, size

    signal_words = _CARD_POWER_WORDS | {
        "warning", "danger", "rescue", "impact", "choice", "decision", "subscribe",
    }
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9'%/-]*", clean_text):
        if token.lower() in signal_words and _card_phrase_is_important(token, clean_text, kind_hint):
            detected = "chapter" if kind_hint == "chapter" else "highlight"
            font, size = _card_font_and_size(detected)
            return _clean_truncate(token.upper(), 80), detected, font, size

    # Treatment-aware context terms are safer than mining the first noun from
    # a sentence. They give blueprint/decision/press/archive moments a real
    # grounded label while leaving ordinary narration card-free.
    context_tokens = [
        token for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9'%/-]*", clean_text)
        if token.lower() in _CARD_CONTEXT_WORDS
    ]
    for token in context_tokens:
        if _card_phrase_is_important(token, clean_text, kind_hint):
            detected_context_kind = kind_hint if kind_hint in {"archive", "location", "person", "chapter", "stat", "date"} else "highlight"
            font, size = _card_font_and_size(detected_context_kind)
            return _clean_truncate(token.upper(), 80), detected_context_kind, font, size

    # 7. Spelled-out numbers, durations and magnitudes
    num_spelled = [
        (r"\b(?:twenty[\s-]four|24)\s*hours?\b", "24 HOURS", "stat"),
        (r"\b(?:over\s+a\s+decade|a\s+decade|decade)\b", "10+ YEARS", "stat"),
        (r"\b(?:two|2)\s+billion(?:\s+dollars)?\b", "$2 BILLION", "stat"),
        (r"\b(?:one|1)\s+billion(?:\s+dollars)?\b", "$1 BILLION", "stat"),
        (r"\b\d+\s*(?:million|billion|trillion)\s*dollars?\b", None, "stat"),
    ]
    for pattern, override_val, detected_k in num_spelled:
        m = re.search(pattern, clean_text, re.I)
        if m:
            val = override_val or m.group(0).upper()
            font, size = _card_font_and_size(detected_k)
            return _clean_truncate(val, 90), detected_k, font, size

    # 8. Topic / Title match (e.g. Grand Theft Auto VI)
    if topic and topic.lower() in clean_text.lower():
        detected_k = "chapter" if kind_hint in {"chapter", "intro", ""} else kind_hint
        font, size = _card_font_and_size(detected_k)
        return _clean_truncate(topic.upper(), 90), detected_k, font, size

    # 9. Location mention anywhere in text
    loc_mention = re.search(r"\b(?:streets of|streets in|in|near|across)\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2})\b", clean_text)
    if loc_mention:
        place_cand = _clean_card_phrase(loc_mention.group(1))
        if place_cand and not any(place_cand.lower().startswith(fp) for fp in _PERSON_FALSE_POSITIVES):
            font, size = _card_font_and_size("location")
            return _clean_truncate(place_cand.upper(), 90), "location", font, size

    # 10. No arbitrary first-clause fallback.  If no grounded term survives,
    # suppress the card; footage remains the story instead of receiving a
    # generic “NEW GAME IS DESIGNED” plate.

    return "", "", "", 0



def _editorial_text_contract(
    template_id: str,
    props: Mapping[str, Any],
    beat: Mapping[str, Any],
    text: str,
    topic: str,
    index: int,
    variation_seed: Any,
    editing_profile: Mapping[str, Any],
    recent_positions: Sequence[str] = (),
    recent_card_modes: Sequence[str] = (),
) -> Dict[str, Any]:
    """Build the small, profile-aware display contract used by native cards."""
    template_id = str(template_id or "").strip().lower()
    treatment = _norm(beat.get("treatment") or "").lower()
    role = _norm(beat.get("role") or "").lower()
    highlight_kind = _norm(beat.get("highlight_kind") or "").lower()

    if template_id in {"date_numeral", "date_reveal", "calendar_rip", "timeline_strip"} or treatment == "date":
        kind = "date"
    elif template_id in {"profile_card", "presenter_card", "professional_name", "profile_dossier", "exhibit_label"} or treatment == "person" or role == "person":
        kind = "person"
    elif template_id in {"map_card", "location_label", "field_caption"} or treatment == "location" or role == "location":
        kind = "location"
    elif template_id in {"stat_counter", "money_counter", "metric_grid", "percentage_gauge", "bar_chart", "ranked_list", "step_progress"} or treatment in {"counter", "chart"}:
        kind = "stat"
    elif template_id in {"quote_card", "witness_quote", "source_citation"} or treatment == "quote" or role == "quote":
        kind = "quote"
    elif template_id in {"archive_canvas", "archival_caption", "photo_frame", "polaroid_card", "paper_explainer"} or highlight_kind in {"archive", "document"}:
        kind = "archive"
    elif template_id in {"cold_open", "chapter_title", "chapter_opener", "headline_center", "end_card", "cta_card"} or treatment in {"chapter", "cta"}:
        kind = "chapter"
    else:
        kind = "highlight"

    seed = stable_seed(topic, text, index, variation_seed, template_id, "display-contract")
    
    # An explicit semantic highlight is typography, even when an earlier
    # template router chose a profile/location container. Otherwise words
    # such as "trust" inherit person styling and every pivotal highlight
    # collapses into the same small corner label.
    if role == 'highlight' and _norm(beat.get('emphasis')) and kind in {'person', 'location'}:
        kind = 'highlight'
    # Extract rich meaningful highlight phrase and occasion font
    display_text, detected_kind, _legacy_font_path, legacy_size = _extract_meaningful_highlight_phrase(
        text, _norm(beat.get("emphasis")), topic, kind, seed
    )
    if detected_kind:
        kind = detected_kind

    # An LLM emphasis can be stale or belong to a different scene. If none of
    # its tokens occur verbatim in the narration, collapse a multi-word
    # fallback to one grounded token instead of emitting a confident-looking
    # invented card (for example, “NEW YORK” for an emphasis of “Tokyo”).
    emphasis_tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9'%/-]*", _norm(beat.get("emphasis")))
    }
    narration_tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9'%/-]*", str(text or ""))
    }
    if emphasis_tokens and not (emphasis_tokens & narration_tokens):
        # When LLM emphasis is disjoint from narration, fall back to grounded salient phrase
        # instead of collapsing into single isolated words like "WENT" or "BECAUSE".
        display_text = select_grounded_highlight(str(text or ""), "", max_words=6) or display_text

    display_text = _norm(display_text).strip(" -:;")
    if not display_text or display_text.upper() in _DISPLAY_GENERIC_LABELS:
        return {"_editorial_contract": True, "_contract_text": ""}

    # Use a small, intentional layout grammar. Randomly cycling every green
    # slot made stats land over faces and pushed unrelated cards into the same
    # visual hierarchy.
    importance = float(beat.get("importance") or beat.get("salience") or 0.0)
    preferred_positions = {
        "stat": "top_right",
        "date": "top_left",
        "person": "bottom_left",
        "location": "bottom_right",
        "chapter": "center",
        "quote": "bottom_left",
        "archive": "top_left",
        "highlight": ("top_right", "bottom_left", "top_left", "bottom_right")[seed % 4],
    }
    position_options = list(ALLOWED_TEXT_POSITIONS)
    position = preferred_positions.get(kind, "center")
    # A high-salience phrase earns the visual center.  Center typography is a
    # distinct hierarchy level, not a smaller corner lower-third.
    if kind == "highlight" and (importance >= 0.84 or index % 5 == 0):
        position = "center"
    if position in set(recent_positions[-1:]):
        position = next((candidate for candidate in position_options if candidate not in set(recent_positions[-1:])), position)

    direction = editing_profile.get("art_direction") if isinstance(editing_profile.get("art_direction"), Mapping) else art_direction_pack(
        str(editing_profile.get("niche") or "general"), variation_seed, str(editing_profile.get("mood") or "")
    )
    design = resolve_template_design(
        template_id,
        kind=kind,
        seed=seed,
        art_direction=direction,
        position=position,
    )
    # Let the video-level creative director choose the text-entry language,
    # while keeping it inside the verified motion registry.  This makes a
    # gaming edit consistently directional and a crime edit deliberately
    # investigative without allowing arbitrary model strings into FFmpeg.
    requested_entry_motion = str(editing_profile.get("text_entry_motion") or "").strip().lower()
    if requested_entry_motion in {"slide_left", "slide_right", "wipe", "rise", "fade", "pop", "stamp", "paper", "data"}:
        design["entry_motion"] = requested_entry_motion
    # Keep automatic cards legible and editorial. Danger is reserved for a
    # grounded warning/consequence, never for arbitrary random decoration.
    if highlight_kind in {"warning", "consequence", "turning_point"}:
        design["card_mode"] = "danger"
    else:
        # A professional edit needs visual breathing room.  If every card has
        # an opaque plate, footage gets buried under UI chrome; if every card
        # is bare, important facts lose contrast.  Use a seeded, semantic mix
        # and explicitly avoid repeating the previous card treatment.
        # Media-first documentary typography is open by default. A thin
        # accent reveal is allowed to carry hierarchy; opaque surface cards
        # are reserved for explicit graphic-only scenes and are not selected
        # by the automatic director.
        card_options = {
            "stat": ("none", "accent"),
            "date": ("none", "accent"),
            "chapter": ("none", "accent"),
            "quote": ("none", "accent"),
            "archive": ("none", "accent"),
            "person": ("none", "accent"),
            "location": ("none", "accent"),
            "highlight": ("none", "none", "none", "accent"),
        }
        options = list(card_options.get(kind, ("surface", "none")))
        previous = str(recent_card_modes[-1] if recent_card_modes else "").strip().lower()
        if previous and len(options) > 1:
            alternatives = [candidate for candidate in options if candidate != previous]
            if alternatives:
                options = alternatives
        design["card_mode"] = options[seed % len(options)]
    default_size = {
        "stat": 106, "date": 82, "chapter": 96, "person": 78,
        "location": 72, "quote": 66, "archive": 72,
        "highlight": 108 if position == "center" else 76,
    }.get(kind, 76)
    size_cap = 126 if position == "center" else 92
    requested_size = int(legacy_size or default_size)
    if position == "center" and kind in {"highlight", "chapter"}:
        requested_size = max(requested_size, 108 if kind == "highlight" else 96)
    size = max(48, min(size_cap, requested_size))

    # The value itself is the label. Generic “HISTORY/METRIC/CHAPTER” tags
    # consume space and make cards feel templated rather than informative.
    micro_label = ""

    return {
        "_editorial_contract": True,
        "_contract_text": _clean_truncate(display_text, 48),
        "_contract_kind": kind,
        "_contract_size": size,
        "_font_path": str(Path(__file__).resolve().parents[2] / "fonts" / design["display_font"]),
        "_contract_font": design["display_font"],
        "_body_font": design["body_font"],
        "_accent_font": design["accent_font"],
        "_position": position,
        "_card_mode": design["card_mode"],
        "_micro_label": micro_label,
        "_entry_motion": design["entry_motion"],
        "_font_style": design["font_style"],
        "_body_font_style": design["body_font_style"],
        "_text_transform": design["text_transform"],
        "_strip_style": design["strip_style"],
        "_template_family": design["family"],
        "_template_variant": design["variant"],
        "_art_direction": design["direction"],
        "_use_generic_contract": False,
        "_show_rule": bool(kind in {"date", "chapter"} or template_id.startswith("date_")),
    }


def _normalized_bbox(value: Any) -> Optional[List[float]]:
    """Accept only a vision/editor supplied normalized target box.

    The motion engine never invents an arrow target from narration. A future
    visual decision may supply `subject_bbox`, while an editor can set the same
    field manually. This keeps annotations factual and locally verifiable.
    """
    if isinstance(value, Mapping):
        # Vision providers commonly return an object-localized box as
        # ``{x, y, width, height}``, while the render contract uses corners.
        # Normalize both forms at the boundary so every renderer receives the
        # same focal contract.
        try:
            x = float(value.get("x"))
            y = float(value.get("y"))
            width = float(value.get("width"))
            height = float(value.get("height"))
        except (AttributeError, TypeError, ValueError):
            return None
        x1, y1, x2, y2 = x, y, x + width, y + height
    elif isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            x1, y1, x2, y2 = [float(part) for part in value]
        except (TypeError, ValueError):
            return None
    else:
        return None
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        return None
    if (x2 - x1) * (y2 - y1) > 0.42:
        return None
    return [round(value, 4) for value in (x1, y1, x2, y2)]


def _verified_target_bbox(media_item: Mapping[str, Any]) -> Optional[List[float]]:
    """Accept target coordinates only with vision confidence or editor intent."""
    source = _norm(media_item.get("bbox_source") or "").lower()
    if source in {"editor", "manual", "user"} or bool(media_item.get("bbox_verified")):
        return _normalized_bbox(
            media_item.get("subject_bbox")
            or media_item.get("focal_bbox")
            or media_item.get("subject_box")
        )
    decision = media_item.get("visual_decision")
    if not isinstance(decision, Mapping):
        return None
    try:
        confidence = float(decision.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    status = _norm(decision.get("status") or "").lower()
    if confidence < 80 or status not in {"accepted", "needs_cleanup"} or bool(decision.get("fatal_mismatch")):
        return None
    return _normalized_bbox(
        decision.get("subject_bbox")
        or decision.get("focal_bbox")
        or decision.get("subject_box")
    )


def _scene_overlays(
    template_id: str,
    text: str,
    beat: Mapping[str, Any],
    media_item: Mapping[str, Any],
    topic: str,
    index: int,
) -> List[Dict[str, Any]]:
    overlays: List[Dict[str, Any]] = []
    lower = text.lower()
    # The selected graphic is already the scene's text treatment. Do not add a
    # second automatic lower-third for the same person; it created duplicate
    # names, generic "DOCUMENTARY" subtitles, and oversized bottom cards.
    # Do not add a decorative frame to otherwise resolved media. It made the
    # previous output feel like a slideshow and was the source of the unwanted
    # white perimeter. Archival treatment now comes from colour, grain, labels,
    # and scene-specific graphics instead.
    target = _verified_target_bbox(media_item)
    if target and not overlays and ("look" in lower or "notice" in lower or "warning" in lower or "evidence" in lower):
        overlays.append({
            "template_id": "callout-arrow",
            "props": {"label": (_norm(beat.get("emphasis")) or "LOOK HERE").upper()[:42], "bbox": target},
            "position": "vision-target",
            "source": "vision_or_editor_bbox",
        })
    return overlays[:2]


def _motion_level(template_id: str, beat: Mapping[str, Any], hero_used: int, hero_limit: int) -> str:
    hero_templates = {"cold-open", "chapter-opener", "end-card", "money-counter", "stat-counter", "bar-chart", "line-chart", "quote-card", "witness-quote", "metric-grid", "word-cascade", "countdown-clock"}
    explanatory = {"map-route", "route-progress", "timeline-trace", "profile-dossier", "evidence-board", "paper-explainer", "source-highlight", "listicle-build", "cause-effect", "split-screen", "gallery-stack", "blueprint-reveal", "decision-tree", "signal-scan", "terminal-log", "newsprint-reveal", "calendar-rip", "index-card", "case-file"}
    importance = float(beat.get("importance") or 0.0)
    forced_hero = template_id in {"cold-open", "chapter-opener", "end-card"}
    if template_id in hero_templates and (forced_hero or importance >= 0.66) and hero_used < hero_limit:
        return "hero"
    if template_id in explanatory:
        return "explanatory"
    return "supporting"


SOUND_VARIANT_FAMILIES: Dict[str, List[str]] = {
    "media": ["photo_slide", "camera_shutter", "paper_rustle", "whoosh_soft", "vinyl_crackle"],
    "typography": ["typewriter", "typing_burst", "marker_scratch", "ui_tick"],
    "date": ["clock_tick", "paper_rustle", "typewriter", "calendar_flip"],
    "signal": ["radio_static", "signal_ping", "decision_tick", "sub_boom"],
    "blueprint": ["pencil_mark", "technical_tick", "decision_tick", "low_rumble"],
    "press": ["paper_rustle", "camera_shutter", "typewriter", "paper_tear"],
    "document": ["paper_flip", "pencil_mark", "stamp_hit", "paper_rustle"],
    "map": ["map_pulse", "water_pulse", "route_ping", "room_tone"],
    "time": ["clock_tick", "reverse_swell", "suspense_pulse", "impact_soft"],
    "data": ["data_tick", "impact_soft", "decision_tick", "ui_tick"],
    "logic": ["decision_tick", "ui_tick", "impact_soft", "marker_scratch"],
    "archive": ["stamp_hit", "vinyl_crackle", "paper_rustle", "camera_shutter"],
    "caption": ["whoosh_soft", "ui_tick", "typing_burst"],
    "focus": ["lens_click", "whoosh_soft", "marker_scratch"],
    "quote": ["room_tone", "vinyl_crackle", "paper_rustle"],
    "narrative": ["whoosh_soft", "low_rumble", "riser"],
    "editorial": ["whoosh_soft", "marker_scratch", "paper_rustle", "edit_whoosh_light", "typing_burst", "data_tick"],
    "text_card": ["whoosh_soft", "marker_scratch", "paper_rustle", "edit_whoosh_light", "typing_burst", "data_tick"],
}

SOUND_INTENT_OPTIONS: Dict[str, List[str]] = {
    "quiet_texture": ["room_tone", "wind_room", "vinyl_crackle", "archive_drone"],
    "transition_air": ["whoosh_air", "whoosh_soft", "transition_suck", "whoosh_snap"],
    "paper_action": ["paper_flip", "page_stack", "paper_tear", "paper_rustle"],
    "typing": ["typewriter", "typing_burst", "keyboard_single", "telegraph"],
    "data_tick": ["data_tick", "technical_tick", "coin_tick", "cash_counter"],
        "location_ping": ["route_ping", "map_pulse", "map_draw", "sonar"],
        "signal": ["radio_static", "signal_ping", "telegraph", "sonar"],
    "tension": ["suspense_pulse", "heartbeat", "tension_string", "low_rumble"],
    "reveal": ["reveal_chime", "reverse_swell", "riser", "news_sting"],
        "impact": ["water_pulse", "impact_soft", "impact", "impact_wood", "impact_metal"],
    "human_stakes": ["heartbeat", "sub_boom", "archive_drone", "wind_room"],
    "archive": ["projector", "film_splice", "camera_shutter", "vinyl_crackle"],
    "technology": ["notification_up", "ui_tick", "technical_tick", "keyboard_single"],
}

# Text-entry animation style (from vox_templates.template_entry_animation) →
# editorial SFX. Distinct entry animations carry distinct sound so the edit
# does not fire the same whoosh on every text card.
_ENTRY_ANIM_EDIT_SFX: Dict[str, str] = {
    "zoom": "edit_zoom_whirr",
    "rise": "edit_whoosh_air",
    "slide": "edit_whoosh_light",
    "fade": "edit_reveal_soft",
    "typewriter": "edit_type_burst_short",
    "stamp": "edit_staple",
    "wipe": "edit_slider_move",
    "data": "edit_type_click",
    "static": "edit_whoosh_light",
}
_ENTRY_ANIM_STAGGER_SFX: Dict[str, str] = {
    "zoom": "whoosh_soft",
    "rise": "whoosh_soft",
    "slide": "whoosh_soft",
    "fade": "whoosh_soft",
    "typewriter": "edit_type_click",
    "stamp": "edit_staple",
    "wipe": "edit_slider_move",
    "data": "data_tick",
    "static": "whoosh_soft",
}


def _entry_anim_style(vox_template: str) -> str:
    """Map a VOX template to its dominant text-entry animation style."""
    if not vox_template:
        return "static"
    style = "static"
    try:
        from .native_engine.libraries.vox_templates import template_entry_animation
        anim = str(template_entry_animation(vox_template) or "").lower()
    except Exception:
        anim = ""
    if anim.startswith("zoom"):
        style = "zoom"
    elif anim.startswith("slide"):
        style = "slide"
    elif anim.startswith("fade"):
        style = "fade"
    elif anim in {"rise", "rise_up"}:
        style = "rise"
    elif anim in _ENTRY_ANIM_EDIT_SFX:
        style = anim
    return style


def _sound_choice(options: Sequence[str], *, seed: Any, recent: Sequence[str], disallow: Sequence[str] = ()) -> str:
    candidates = [key for key in options if key not in disallow and key not in recent[-3:]]
    if not candidates:
        candidates = [key for key in options if key not in disallow]
    if not candidates:
        return ""
    return candidates[stable_seed(seed, "sound-choice") % len(candidates)]


def _resolve_sound_item(
    kind: str,
    library: Optional[Mapping[str, Any]],
) -> Tuple[str, str, Dict[str, Any]]:
    if not library or not isinstance(library, Mapping):
        return "", "missing", {}
    item = library.get(kind)
    if isinstance(item, Mapping):
        src = _norm(item.get("src") or item.get("path"))
        rights = _norm(item.get("rights_status") or item.get("license") or ("unknown" if src else "missing"))
        metadata = {name: item[name] for name in ("publisher", "source_url", "attribution", "license") if item.get(name)}
        return src, rights, metadata
    return "", "missing", {}


def plan_sound_cues(
    scene: Mapping[str, Any],
    index: int,
    library: Optional[Mapping[str, Any]] = None,
    *,
    recent: Sequence[str] = (),
) -> List[Dict[str, Any]]:
    """Build deterministic, voice-first sound motifs rather than one repeated hit.

    Each high-value beat may have a primary effect plus a quiet transition or
    texture accent. The cap keeps narration intelligible and makes a long edit
    feel authored instead of mechanically firing the same cue every scene.
    """
    primary_vis = scene.get("primary_visual") if isinstance(scene.get("primary_visual"), Mapping) else {}
    pv_type = str(primary_vis.get("type") or "").strip()
    graphic = scene.get("graphic") if isinstance(scene.get("graphic"), Mapping) else {}
    template_id = str(graphic.get("template_id") or "full-bleed")
    family = _template(template_id).get("family", "")
    transition = str((scene.get("transition") or {}).get("template_id") or "cut")
    beat = scene.get("semantic_beat") if isinstance(scene.get("semantic_beat"), Mapping) else {}
    canonical = SOUND_FAMILIES.get(family) or ""
    variants = SOUND_VARIANT_FAMILIES.get(family) or []
    design = scene.get("design") if isinstance(scene.get("design"), Mapping) else {}
    profile_options = list(design.get("sfx_options") or []) if isinstance(design, Mapping) else []
    sound_intent = str(beat.get("sound_intent") or "none").strip().lower()
    is_llm_run = str(beat.get("source") or "").startswith("openrouter") or beat.get("selected_for_text") is not None

    # The director owns sound for LLM-directed scenes. Respect its silence
    # decisions and require an exact, contiguous Whisper-timed spoken anchor.
    if is_llm_run:
        if sound_intent not in SOUND_INTENT_OPTIONS or sound_intent == "quiet_texture":
            return []
        reason_text = str(beat.get("sound_reason") or "").strip().lower()
        if any(phrase in reason_text for phrase in ("no sound", "no specific sound", "silence", "not needed")):
            return []
        options = list(SOUND_INTENT_OPTIONS.get(sound_intent) or [])
        if sound_intent == "quiet_texture" and any(token in reason_text for token in ("wind", "air", "sand")):
            options = ["wind_room", *[key for key in options if key != "wind_room"]]
        available = []
        for key in options:
            item = library.get(key) if isinstance(library, Mapping) else None
            if isinstance(item, Mapping) and _norm(item.get("src") or item.get("path")):
                available.append(key)
        if not available:
            return []
        anchor_text = str(beat.get("sound_anchor") or "").strip()
        preferred = {
            "quiet_texture": "wind_room" if any(token in reason_text for token in ("wind", "air", "sand")) else "room_tone",
            "data_tick": "data_tick",
            "impact": "water_pulse" if any(token in anchor_text.casefold() for token in ("splash", "water", "splashed")) else "impact_soft",
            "location_ping": "route_ping",
            "archive": "projector",
            "signal": "radio_static",
            "tension": "heartbeat",
        }.get(sound_intent)
        selected = preferred if preferred in available else _sound_choice(
            available, seed=(scene.get("scene_id") or index, sound_intent), recent=recent
        )
        if not selected:
            return []

        anchor_tokens = [
            re.sub(r"[^a-z0-9]+", "", token.casefold())
            for token in re.findall(r"\d+(?:[.,]\d+)?|[A-Za-z]+(?:['’][A-Za-z]+)?", anchor_text)
        ]
        anchor_tokens = [token for token in anchor_tokens if token]
        if not anchor_tokens:
            return []
        timed_words = []
        for phrase in (scene.get("word_cues") or []):
            if not isinstance(phrase, Mapping) or not str(phrase.get("timing_quality") or "").startswith("whisper"):
                continue
            for word in (phrase.get("timed_words") or []):
                if isinstance(word, Mapping):
                    timed_words.append(word)
        timed_words.sort(key=lambda word: int(word.get("index", 0) or 0))
        normalized_words = [
            re.sub(r"[^a-z0-9]+", "", str(word.get("text") or "").casefold())
            for word in timed_words
        ]
        matched_words = None
        for offset in range(0, len(normalized_words) - len(anchor_tokens) + 1):
            if normalized_words[offset:offset + len(anchor_tokens)] != anchor_tokens:
                continue
            group = timed_words[offset:offset + len(anchor_tokens)]
            indices = [int(word.get("index", -1)) for word in group]
            if indices != list(range(indices[0], indices[0] + len(indices))):
                continue
            matched_words = group
            break
        if not matched_words:
            return []
        matched_word = matched_words[0]
        anchor_text = " ".join(str(word.get("text") or "").strip() for word in matched_words).strip()
        scene_start = max(0.0, float(scene.get("start") or 0.0))
        scene_duration = max(0.08, float(scene.get("duration") or 0.5))
        local_start = max(0.0, float(matched_word.get("start") or 0.0))
        timing_quality = "whisper_word_aligned"
        item = library.get(selected) if isinstance(library, Mapping) else {}
        source = _norm(item.get("src") or item.get("path")) if isinstance(item, Mapping) else ""
        if not source:
            return []
        local_start = min(local_start, max(0.0, scene_duration - 0.04))
        cue_duration = min(scene_duration - local_start, 0.42)
        volume = 0.24 if sound_intent == "impact" else 0.18
        absolute_start = round(scene_start + local_start, 3)
        return [{
            "kind": selected,
            "src": source,
            "start": absolute_start,
            "time_local": round(local_start, 3),
            "scene_local_start": round(local_start, 3),
            "duration": round(max(0.08, cue_duration), 3),
            "volume": volume,
            "fade_in": 0.015,
            "fade_out": 0.12,
            "duck_voice": 0.02,
            "reason": str(beat.get("sound_reason") or sound_intent),
            "sound_intent": sound_intent,
            "sound_anchor": anchor_text,
            "timing_quality": timing_quality,
            "rights_status": _norm(item.get("rights_status") or item.get("license") or "unknown") if isinstance(item, Mapping) else "unknown",
            **({key: item[key] for key in ("publisher", "source_url", "attribution", "license") if isinstance(item, Mapping) and item.get(key)}),
        }]

    intent_options = SOUND_INTENT_OPTIONS.get(sound_intent, [])
    family_options = (
        [key for key in intent_options if key]
        + [key for key in profile_options if key]
        + ([canonical] if canonical else [])
        + [key for key in variants if key and key != canonical]
    )
    family_options = list(dict.fromkeys(family_options))
    if library and isinstance(library, Mapping):
        available = [key for key in family_options if isinstance(library.get(key), Mapping)]
        if available:
            family_options = available
    mg_type = pv_type or (template_id if template_id not in {"full-bleed", "none", ""} else "")
    importance = float(beat.get("importance") or 0.0)
    # For LLM-driven generation, keep sound restrained and occasional:
    # Unselected ordinary shots remain quiet and clean.
    if is_llm_run and beat.get("selected_for_text") is False and not mg_type and index > 0 and importance < 0.85:
        primary = ""
    else:
        primary = _sound_choice(family_options, seed=(template_id, index, "primary"), recent=recent)
    keys: List[str] = [primary] if primary else []

    # Cold open hook punch: scene 0 grabs attention immediately
    if index == 0 and not keys:
        hook_candidates = [k for k in ["edit_hit_tight", "impact_soft", "whoosh_snap", "reveal_chime"] if not library or (isinstance(library, Mapping) and k in library)]
        if hook_candidates:
            hook_chosen = _sound_choice(hook_candidates, seed=("hook", index), recent=recent)
            if hook_chosen:
                keys.append(hook_chosen)

    # Dedicated motion graphic sound effects: Every motion graphic receives an entrance sound cue
    mg_type = pv_type or (template_id if template_id not in {"full-bleed", "none", ""} else "")
    if mg_type:
        lower_mg = mg_type.lower()
        mg_candidates = []
        if any(k in lower_mg for k in ("map", "route")):
            mg_candidates = ["map_pulse", "route_ping", "map_draw", "sonar"]
        elif any(k in lower_mg for k in ("chart", "graph", "bars", "slope", "donut")):
            mg_candidates = ["data_tick", "technical_tick", "coin_tick", "edit_type_click", "ui_tick"]
        elif any(k in lower_mg for k in ("stat", "counter", "money", "metric", "kpi")):
            mg_candidates = ["cash_counter", "impact_soft", "edit_hit_tight", "data_tick"]
        elif any(k in lower_mg for k in ("quote", "witness")):
            mg_candidates = ["paper_flip", "paper_rustle", "edit_paper_shuffle", "typewriter"]
        elif any(k in lower_mg for k in ("timeline", "calendar", "date", "clock")):
            mg_candidates = ["clock_tick", "calendar_flip", "decision_tick", "reverse_swell"]
        elif any(k in lower_mg for k in ("chapter", "opener", "cold-open")):
            mg_candidates = ["riser", "edit_sting_major", "news_sting", "reveal_chime"]
        elif any(k in lower_mg for k in ("split", "gallery", "stack")):
            mg_candidates = ["edit_slider_move", "photo_slide", "whoosh_snap"]
        elif any(k in lower_mg for k in ("text", "kinetic", "keyword", "word", "card")):
            mg_candidates = ["edit_whoosh_light", "whoosh_soft", "edit_reveal_soft", "typing_burst"]
        elif any(k in lower_mg for k in ("diagram", "flow", "tree", "blueprint", "evidence")):
            mg_candidates = ["pencil_mark", "technical_tick", "decision_tick", "marker_scratch"]
        if library and isinstance(library, Mapping):
            mg_candidates = [k for k in mg_candidates if isinstance(library.get(k), Mapping)]
        if mg_candidates:
            mg_chosen = _sound_choice(mg_candidates, seed=(mg_type, index, "mg"), recent=recent)
            if mg_chosen:
                keys.insert(0, mg_chosen)

    role = str(beat.get("role") or "")
    energy = str(beat.get("energy") or "normal")
    importance = float(beat.get("importance") or 0.0)
    try:
        sound_intensity = max(0.0, min(1.0, float(beat.get("sound_intensity") or 0.0)))
    except (TypeError, ValueError):
        sound_intensity = 0.0
    # An LLM-authored quiet scene is allowed to stay quiet. Deterministic
    # fallback projects retain the broader procedural sound coverage.
    if (
        sound_intent == "none"
        and str(beat.get("source") or "").startswith("openrouter")
        and importance < 0.7
        and transition == "cut"
        and not mg_type
    ):
        return []
    # Major narrative milestone or chapter turning point (restrained, occasional)
    if role in {"chapter", "climax"} or (importance >= 0.88 and index % 3 == 0):
        keys.append("riser")
    elif sound_intent in {"impact", "reveal"} and not keys and importance >= 0.75:
        intent_candidates = [k for k in ["impact_soft", "reveal_chime", "edit_sting_major"] if not library or (isinstance(library, Mapping) and k in library)]
        if intent_candidates:
            intent_chosen = _sound_choice(intent_candidates, seed=("intent", index), recent=recent)
            if intent_chosen:
                keys.append(intent_chosen)
    if energy in {"tense", "urgent"} and (role in {"climax", "turning_point"} or importance >= 0.88):
        keys.append("sub_boom" if index % 2 else "reverse_swell")
    elif energy in {"high", "energetic"} and role in {"climax", "turning_point"} and index % 2:
        keys.append("impact_soft")
    # Add a quiet companion texture for ordinary shots too. This avoids a
    # whole project sounding like the same single whoosh while staying below
    # the voiceover rather than turning each scene into a trailer sting.
    if is_llm_run and (beat.get("selected_for_text") is False or importance < 0.85):
        companion = ""
    else:
        companion = _sound_choice(family_options, seed=(template_id, index, "companion"), recent=recent, disallow=keys)
    if companion:
        keys.append(companion)
    if not keys:
        return []
    unique_keys: List[str] = []
    for key in keys:
        if key and key not in unique_keys:
            unique_keys.append(key)
    cue_limit = 3 if (role in {"chapter", "climax"} or energy in {"tense", "urgent"}) else 2
    duration = max(0.12, min(0.9, float(scene.get("duration") or 1.0) * 0.22))
    scene_start = max(0.0, float(scene.get("start") or 0.0))
    cues: List[Dict[str, Any]] = []
    for cue_index, key in enumerate(unique_keys[:cue_limit]):
        source = ""
        rights_status = "missing"
        metadata: Dict[str, Any] = {}
        if library:
            item = library.get(key) if isinstance(library, Mapping) else None
            if isinstance(item, Mapping):
                source = _norm(item.get("src") or item.get("path"))
                rights_status = _norm(item.get("rights_status") or item.get("license") or ("unknown" if source else "missing"))
                metadata = {name: item[name] for name in ("publisher", "source_url", "attribution", "license") if item.get(name)}
        start = scene_start + (0.04 if index or cue_index else 0.0) + cue_index * min(.32, max(.12, float(scene.get("duration") or 1) * .1))
        loud = key in {"impact", "impact_metal", "impact_wood", "bass_drop", "sub_boom", "riser", "reverse_swell"}
        base_volume = 0.24 if loud and cue_index == 0 else (0.16 if loud else 0.12)
        if sound_intensity:
            base_volume *= 0.72 + sound_intensity * .48
        local_t = round(start - scene_start, 3)
        cues.append({
            "kind": key, "src": source, "start": round(start, 3), "duration": round(duration, 3),
            "time_local": local_t, "scene_local_start": local_t,
            "volume": round(min(.3, base_volume), 3), "fade_in": 0.03, "fade_out": 0.16,
            "duck_voice": 0.08 if loud else 0.04,
            "reason": _norm(beat.get("sound_reason")) or sound_intent or family or transition,
            "sound_intent": sound_intent, "rights_status": rights_status, **metadata,
        })

    # Inject VOX template-specific SFX for distinctive audio signature
    vox_template = str((scene.get("graphic") or {}).get("vox_template") or "")
    anim_style = _entry_anim_style(vox_template)
    contract_props = (scene.get("graphic") or {}).get("props") or {}
    contract_motion = str(contract_props.get("_entry_motion") or "").lower()
    if contract_motion in {"slide_left", "slide_right"}:
        anim_style = "slide"
    elif contract_motion == "wipe":
        anim_style = "wipe"
    elif contract_motion in {"rise", "data"}:
        anim_style = "rise"
    elif contract_motion in {"fade", "paper"}:
        anim_style = "fade"
    elif contract_motion in {"pop", "stamp"}:
        anim_style = "zoom"
    if vox_template:
        try:
            from .native_engine.libraries.vox_templates import get_template_sfx
            template_sfx = get_template_sfx(vox_template)
            for ts in template_sfx:
                tkind = ts.get("kind", "")
                if not tkind:
                    continue
                # Resolve template SFX source from library
                tsrc = ""
                trights = "missing"
                if library and isinstance(library, Mapping):
                    titem = library.get(tkind)
                    if isinstance(titem, Mapping):
                        tsrc = _norm(titem.get("src") or titem.get("path"))
                        trights = _norm(titem.get("rights_status") or titem.get("license") or "unknown")
                if not tsrc:
                    continue  # skip if no real file
                tstart = scene_start + float(ts.get("delay", 0.0))
                tvol = min(0.3, float(ts.get("volume", 0.15)))
                # Dedupe by kind
                if any(c.get("kind") == tkind and abs(float(c.get("start", 0)) - tstart) < 0.2 for c in cues):
                    continue
                cues.append({
                    "kind": tkind, "src": tsrc, "start": round(tstart, 3),
                    "duration": round(duration, 3), "volume": round(tvol, 3),
                    "fade_in": 0.02, "fade_out": 0.12, "duck_voice": 0.06,
                    "reason": f"vox:{vox_template}", "sound_intent": "template_signature",
                    "rights_status": trights,
                })
        except Exception:
            pass

    # ----- Text-entry stagger pass -------------------------------------------
    # Multi-layer templates animate their text elements ~0.12s apart. Land a
    # soft tick on each later layer so the sound matches the visible motion.
    if vox_template:
        try:
            from .native_engine.libraries.vox_templates import template_text_layers
            layers = template_text_layers(vox_template)
        except Exception:
            layers = 0
        scene_end = scene_start + max(0.5, float(scene.get("duration") or 1.0))
        staggered = 0
        for layer in range(1, layers):
            tstart = scene_start + round(layer * 0.12, 2)
            if tstart >= scene_end - 0.35 or staggered >= 2:
                break
            tick_kind = "data_tick" if family in {"data", "stat"} else _ENTRY_ANIM_STAGGER_SFX.get(anim_style, "whoosh_soft")
            if any(c.get("kind") == tick_kind and abs(float(c.get("start", 0)) - tstart) < 0.2 for c in cues):
                continue
            tsrc = ""
            trights = "missing"
            if library and isinstance(library, Mapping):
                titem = library.get(tick_kind)
                if isinstance(titem, Mapping):
                    tsrc = _norm(titem.get("src") or titem.get("path"))
                    trights = _norm(titem.get("rights_status") or titem.get("license") or "unknown")
            if not tsrc:
                continue
            cues.append({
                "kind": tick_kind, "src": tsrc, "start": round(tstart, 3),
                "duration": 0.3, "volume": 0.08,
                "fade_in": 0.02, "fade_out": 0.10, "duck_voice": 0.05,
                "reason": f"text_stagger:{vox_template}", "sound_intent": "text_entry",
                "rights_status": trights,
            })
            staggered += 1

    # ----- Editorial-cue pass ------------------------------------------------
    # Tight accents tied to visible editing grammar:
    #   text-card entrance, non-cut transitions, emphasis beats.
    # These are clipped to a single cue per scene and shaped low in the mix
    # so they read as authored punctuation, not trailer stingers.
    has_graphic = (template_id and template_id != "full-bleed") or bool((scene.get("primary_visual") or {}).get("type"))
    edit_cues: List[Tuple[str, float, float, str]] = []  # (kind, delay_offset, volume, reason)
    if has_graphic:
        g_start = 0.0
        if isinstance(scene.get("graphic"), Mapping):
            g_start = float(scene["graphic"].get("scene_local_start") or 0.0)
        elif isinstance(scene.get("primary_visual"), Mapping):
            g_start = float(scene["primary_visual"].get("scene_local_start") or 0.0)
        
        # Pre-roll whoosh leads visual entrance by 2-3 frames (~0.08s)
        whoosh_lead = max(0.0, g_start - 0.08)

        pv_type = str((scene.get("primary_visual") or {}).get("type") or "").strip()
        if pv_type in {"StatCard", "Timeline", "Chart"} or template_id in {"stat-counter", "money-counter", "date-card"}:
            edit_cues.append(("impact_soft", round(g_start, 3), 0.24, f"stat_impact:{pv_type or template_id}"))

        if template_id in {"text-card", "vox-highlighter", "cyber-hud", "investigative-memo", "broadsheet-callout", "frosted-glass", "brutalist-punch"} or pv_type == "TextCard":
            card_style = str((scene.get("graphic") or {}).get("props", {}).get("style") or (scene.get("primary_visual") or {}).get("variant") or template_id)
            style_sfx_map = {
                "cyber-hud": ("technical_tick", whoosh_lead, 0.20, "text_card_entrance:cyber_hud"),
                "investigative-memo": ("paper_rustle", whoosh_lead, 0.22, "text_card_entrance:memo"),
                "broadsheet-callout": ("paper_flip", whoosh_lead, 0.20, "text_card_entrance:broadsheet"),
                "frosted-glass": ("edit_reveal_soft", whoosh_lead, 0.18, "text_card_entrance:glass"),
                "brutalist-punch": ("whoosh_snap", whoosh_lead, 0.24, "text_card_entrance:brutalist"),
                "vox-highlighter": ("edit_whoosh_light", whoosh_lead, 0.20, "text_card_entrance:highlighter"),
            }
            edit_cues.append(style_sfx_map.get(card_style, ("edit_whoosh_light", whoosh_lead, 0.20, f"text_card_entrance:{card_style}")))
        else:
            # Text-card entrance SFX matches the visible entry animation so the
            # sound differs per text motion (zoom whirr vs whoosh vs type tick).
            entry_kind = _ENTRY_ANIM_EDIT_SFX.get(anim_style, "edit_whoosh_light")
            edit_cues.append((entry_kind, whoosh_lead, 0.18, f"text_card_entrance:{anim_style}"))
    # Transitions: keep cuts silent (they read as authored already); color
    # specific transition flavors with their own editorial accent. Fires only
    # on ~2 of every 3 transitions so the edit breathes between accents.
    transition_editorial_map = {
        "dissolve": ("edit_whoosh_air", 0.0, 0.16),
        "dip_black": ("edit_hit_dry", 0.0, 0.16),
        "dip_white": ("edit_hit_tight", 0.0, 0.16),
        "wipe": ("edit_slider_move", 0.0, 0.15),
        "slide_left": ("edit_whoosh_air", 0.0, 0.17),
        "slide_right": ("edit_whoosh_air", 0.0, 0.17),
        "paper_tear": ("edit_paper_tear_subtle", 0.0, 0.20),
        "page_flip": ("edit_paper_shuffle", 0.0, 0.18),
        "quick_zoom_in": ("edit_zoom_whirr", 0.0, 0.16),
        "quick_zoom_out": ("edit_zoom_whirr", 0.0, 0.16),
        "ink_bleed": ("sub_boom", 0.0, 0.24),
        "push_blur": ("whoosh_snap", 0.0, 0.22),
        "snap_zoom": ("bass_drop", 0.0, 0.24),
    }
    if transition and transition != "cut" and index % 3 != 0:
        tedit = transition_editorial_map.get(transition)
        if tedit:
            edit_cues.append((tedit[0], 0.0, tedit[2], f"transition:{transition}"))
    # Emphasis beats get a light chime/dry hit — never on chapter fades which
    # already have riser/impact planned.
    if importance >= 0.85 and role in {"setup", "evidence", "context"} and transition == "cut":
        edit_cues.append(("edit_hit_tight", 0.0, 0.14, "emphasis:tight"))
    # Filter to cues we actually have a file for, and keep at most 2 per scene.
    existing_kinds = {c.get("kind") for c in cues}
    added = 0
    for kind, delay, vol, reason in edit_cues:
        if added >= 2:
            break
        if kind in existing_kinds:
            continue
        src = ""
        rights = "missing"
        if library and isinstance(library, Mapping):
            item = library.get(kind)
            if isinstance(item, Mapping):
                src = _norm(item.get("src") or item.get("path"))
                rights = _norm(item.get("rights_status") or item.get("license") or "unknown")
        if not src:
            continue
        local_t = round(delay, 3)
        cues.append({
            "kind": kind, "src": src, "start": round(scene_start + delay, 3),
            "time_local": local_t, "scene_local_start": local_t,
            "duration": 0.4, "volume": round(min(0.25, vol), 3),
            "fade_in": 0.01, "fade_out": 0.08, "duck_voice": 0.05,
            "reason": f"editorial:{reason}", "sound_intent": "editorial",
            "rights_status": rights,
        })
        existing_kinds.add(kind)
        added += 1

    return cues


def _sound_category(kind: str) -> str:
    """Group near-identical effects so a long edit does not sound mechanical."""
    value = str(kind or "").lower().replace("-", "_")
    if "background" in value or value in {"room_tone", "low_rumble", "archive_drone", "wind_room", "vinyl_crackle", "suspense_pulse"}:
        return "background"
    if any(token in value for token in ("whoosh", "swell", "riser", "transition", "slider", "zoom_whirr", "suck")):
        return "movement"
    if any(token in value for token in ("impact", "hit", "boom", "sting", "bass_drop", "snap", "water_pulse")):
        return "impact"
    if any(token in value for token in ("map", "route", "sonar", "ping")):
        return "spatial"
    if any(token in value for token in ("paper", "archive", "film", "photo", "rustle")):
        return "archive"
    if any(token in value for token in ("tick", "type", "typing", "click", "counter", "cash")):
        return "data"
    if any(token in value for token in ("chime", "reveal", "signal", "decision", "pencil", "marker")):
        return "detail"
    return value or "other"


def align_directed_motion_to_speech(scene: Dict[str, Any]) -> None:
    """Snap the director's visual events to the recorded words when available."""
    words = []
    for phrase in scene.get("word_cues") or []:
        if isinstance(phrase, Mapping) and str(phrase.get("timing_quality") or "").startswith("whisper"):
            words.extend(word for word in phrase.get("timed_words") or [] if isinstance(word, Mapping))
    words.sort(key=lambda word: int(word.get("index", 0) or 0))
    timing_source = 'whisper_word_aligned'
    if not words:
        timing_source = 'caption_word_aligned'
        scene_start = float(scene.get('start') or 0)
        for index, item in enumerate(scene.get('caption_words') or []):
            if isinstance(item, Mapping):
                local = float(item.get('start') or 0) - (0 if scene.get('caption_clock') == 'scene' else scene_start)
                words.append({'index': index, 'text': item.get('word') or item.get('text') or '', 'start': local})
    tokens = [re.sub(r"[^a-z0-9]", "", str(word.get("text") or "").casefold()) for word in words]
    duration = max(0.0, float(scene.get("duration") or 0.0))
    for cue in scene.get("motion_cues") or []:
        if not isinstance(cue, dict):
            continue
        if cue.get('preview_before_narration') is True:
            continue
        phrase = str(cue.get('anchor_text') or cue.get('trigger_phrase') or cue.get('narration_phrase') or '')
        anchor = [re.sub(r"[^a-z0-9]", "", token.casefold()) for token in phrase.split()]
        anchor = [token for token in anchor if token]
        if not anchor:
            continue
        for offset in range(len(tokens) - len(anchor) + 1):
            if tokens[offset:offset + len(anchor)] != anchor:
                continue
            group = words[offset:offset + len(anchor)]
            indices = [int(word.get("index", -1)) for word in group]
            if indices != list(range(indices[0], indices[0] + len(indices))):
                continue
            start = float(group[0].get("start") or 0.0)
            if 0 <= start < duration:
                cue["time_local"] = round(start, 3)
                cue["timing_quality"] = timing_source
                cue["anchor_text"] = " ".join(str(word.get("text") or "").strip() for word in group)
            break


def plan_directed_sound_cues(scene: Mapping[str, Any], index: int, library: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Score audible events from the final visual direction, not the pre-director draft."""
    from .editorial_director.contract import has_approved_director_copy
    beat = scene.get("semantic_beat") if isinstance(scene.get("semantic_beat"), Mapping) else {}
    reason = str(beat.get("sound_reason") or "").casefold()
    if any(phrase in reason for phrase in ("intentional silence", "deliberate silence", "no sound under")):
        return []
    cues = plan_sound_cues(scene, index, library)
    start = float(scene.get("start") or 0.0)
    duration = max(0.0, float(scene.get("duration") or 0.0))
    visual = scene.get("primary_visual") if isinstance(scene.get("primary_visual"), Mapping) else {}
    visual_type = str(visual.get("type") or "").casefold()
    camera = scene.get("camera") if isinstance(scene.get("camera"), Mapping) else {}
    motion_spec = scene.get("motion") if isinstance(scene.get("motion"), Mapping) else {}
    entrance = str(camera.get("entrance") or motion_spec.get("entrance") or "").casefold()
    graphic = scene.get("graphic") if isinstance(scene.get("graphic"), Mapping) else {}
    template = str(graphic.get("template_id") or "").casefold()

    def count_sound(anchor: str = "") -> str:
        from .editorial_director.review_context import narration_opportunities
        normalized_anchor = re.sub(r'\W+', ' ', anchor.casefold()).strip()
        date_anchor = bool(anchor) and any(
            row['kind'] == 'date'
            and re.sub(r'\W+', ' ', row['source_span'].casefold()).strip() == normalized_anchor
            for row in narration_opportunities(anchor)
        ) and not re.search(r'[$£€]|\b(?:dollars?|pounds?|euros?)\b', anchor, re.I)
        if visual_type in {"timeline", "calendar"} or beat.get("highlight_kind") == "date" or date_anchor:
            return "clock_tick" if isinstance(library, Mapping) and "clock_tick" in library else "data_tick"
        return "cash_counter"

    def add(kind: str, at: float, event: str, anchor: str = "", quality: str = "visual_frame_aligned") -> None:
        item = library.get(kind) if isinstance(library, Mapping) else None
        source = _norm(item.get("src") or item.get("path")) if isinstance(item, Mapping) else ""
        if not source or at < 0 or at >= duration - 0.08:
            return
        if any(abs(float(c.get("visual_event_time_local", c.get("time_local")) or 0.0) - at) < 0.65 for c in cues):
            return
        try:
            transient_offset = max(0.0, min(2.0, float(item.get("transient_offset_seconds") or 0.0)))
        except (TypeError, ValueError):
            transient_offset = 0.0
        playback_at = max(0.0, at - transient_offset)
        trim_start = max(0.0, transient_offset - at)
        sound_duration = min(2.0, max(0.55, transient_offset + .2), duration - playback_at)
        volume = 0.38 if kind == 'cash_counter' else 0.32
        measured_peak = item.get('source_peak_dbfs')
        if isinstance(measured_peak, (int, float)) and math.isfinite(measured_peak):
            # Similar event loudness across quiet ticks and loud whooshes.
            # Never amplify beyond unity or let a reveal compete with speech.
            target_peak = -12.0 if kind == 'cash_counter' else -14.0
            volume = min(.85, max(.25, 10 ** ((target_peak - measured_peak) / 20)))
        cues.append({
            "kind": kind, "src": source, "start": round(start + playback_at, 3),
            "time_local": round(playback_at, 3), "scene_local_start": round(playback_at, 3),
            "visual_event_time_local": round(at, 3), "event_id": f"scene-{index}:{event}",
            "transient_offset_seconds": transient_offset,
            "trim_start_seconds": round(trim_start, 4),
            "transient_alignment_error_seconds": round(playback_at + transient_offset - trim_start - at, 3),
            "duration": round(sound_duration, 3), "volume": round(volume, 4),
            "fade_in": 0.012, "fade_out": 0.09, "duck_voice": 0.04,
            "reason": f"visual:{event}", "sound_intent": "visual_action",
            "sound_anchor": anchor if quality == "whisper_word_aligned" else "",
            "timing_quality": quality, "visual_trigger": True,
            "rights_status": _norm(item.get("rights_status") or item.get("license") or "unknown"),
        })

    if entrance in {"scale_pop", "zoom", "zoom_in", "punch_in", "whip_zoom"}:
        add("edit_zoom_whirr", 0.05, "camera_zoom")
    motion = scene.get("motion_cues") or []
    for cue in motion:
        if not isinstance(cue, Mapping):
            continue
        action = str(cue.get("action") or "").casefold()
        element = str(cue.get("element") or "").casefold()
        if action in {"hold", "settle", "pause"}:
            continue
        # A motion cue can be a planning hint without a visible graphic. Do
        # not play diagram/map sounds over an ordinary full-bleed photograph.
        visible_copy = element == "copy" and has_approved_director_copy(scene) and (scene.get("text") or {}).get("position") != "none"
        if visual_type in {"smartimage", "smartvideo", "video", "image", "footage"} and template in {"", "full-bleed"} and not visible_copy:
            continue
        if ("map" in element or "route" in element) and visual_type != "animatedmap" and template not in {"map-route", "map-card"}:
            continue
        at = float(cue.get("time_local") or 0.0)
        if visible_copy:
            kind = "marker_scratch" if action == "wipe" else "edit_reveal_soft"
        elif action == "count" or any(token in element for token in ("value", "number", "data", "counter")):
            kind = count_sound(str(cue.get("anchor_text") or "")) if action == "count" else "data_tick"
        elif "map" in element or "route" in element or visual_type == "animatedmap":
            kind = "map_draw" if action in {"draw", "trace", "reveal"} else "route_ping"
        elif any(token in element for token in ("step", "node", "diagram", "bar", "line")):
            kind = "technical_tick"
        elif "document" in element or "evidence" in element or visual_type == "document":
            kind = "paper_flip"
        elif action in {"slide", "split", "wipe"}:
            kind = "edit_slider_move"
        elif action in {"reveal", "assemble", "trace"} and (visual_type in {"chart", "diagram", "comparison", "timeline", "statcard", "chaptertitle"} or template not in {"", "full-bleed"}):
            kind = "reveal_chime"
        else:
            continue
        quality = "whisper_word_aligned" if cue.get("timing_quality") == "whisper_word_aligned" else "visual_frame_aligned"
        add(kind, at, f"{element}:{action}", str(cue.get("anchor_text") or ""), quality)
    # A pixel director can replace a failed shot with a validated composition.
    # Its layer clock is the actual reveal clock even when older motion cues
    # describe the replaced media. Score that authored animation directly.
    fps = max(1.0, float(scene.get("fps") or 30))
    recipe = scene.get("template_recipe") or {}
    recipe_events = 0
    for layer in recipe.get("layers") or []:
        if recipe_events >= 3 or not isinstance(layer, Mapping):
            continue
        motion_kind = str(layer.get("motion") or "none").casefold()
        layer_kind = str(layer.get("kind") or "").casefold()
        if motion_kind in {"none", "hold", "parallax"}:
            continue
        if layer.get("semantic_actor") or layer_kind == "diagram":
            kind = "technical_tick"
        elif layer_kind == "chart":
            kind = count_sound(str(layer.get("text") or "")) if motion_kind == "count" else "data_tick"
        elif layer_kind == "timeline":
            kind = "technical_tick"
        elif layer_kind == "comparison":
            kind = "edit_slider_move"
        else:
            continue
        add(kind, max(0.0, float(layer.get("start_frame") or 0) / fps),
            f"recipe:{layer.get('id') or layer_kind}:{motion_kind}")
        recipe_events += 1
    # Only a motivated boundary gets a transition accent. Ordinary cuts and
    # dissolves remain quiet, and the global timeline applies the final budget.
    transition_spec = scene.get("transition") or {}
    transition = str(scene.get("transition_out") or (transition_spec.get("template_id") if isinstance(transition_spec, Mapping) else transition_spec) or "").casefold().replace("-", "_")
    role = str(beat.get("role") or "").casefold()
    motivated_boundary = visual_type == "chaptertitle" or role in {"pivot", "reveal", "climax", "turn"}
    sentence_ends = str(scene.get("narration") or "").strip().endswith((".", "!", "?"))
    transition_set = {
        "cross_dissolve", "luma_dissolve", "dip_black", "dip_white",
        "snap_zoom", "glitch_slice", "ink_bleed", "slide",
        "wipe", "wipe_left", "wipe_right", "whip_pan", "zoom_cut",
        "slide_left", "slide_right", "directional_wipe"
    }
    if transition in transition_set and motivated_boundary and sentence_ends:
        add("whoosh_soft", max(0.0, duration - 0.46), "transition")
    return cues


def rescore_revised_soundtrack(manifest: Dict[str, Any], indices: Sequence[int], library: Mapping[str, Any]) -> None:
    """Rebuild generated sound after visual repair using the final scene clock."""
    # User-authored editor tracks remain authoritative during an editor export.
    if "sfx" in ((manifest.get("editor_timeline") or {}).get("tracks") or {}):
        return
    scenes = manifest.get("scenes") or []
    for index in sorted(set(indices)):
        if not 0 <= index < len(scenes):
            continue
        scene = scenes[index]
        scene["fps"] = manifest.get("fps") or 30
        align_directed_motion_to_speech(scene)
        scene["sound_cues"] = plan_directed_sound_cues(scene, index, library)
    manifest["sound_audit"] = _sanitize_sound_cue_timeline(scenes, library)
    effects = [cue for scene in scenes for cue in scene.get("sound_cues") or []]
    manifest.setdefault("audio", {})["effects"] = effects
    manifest.setdefault("metrics", {})["sound_cue_count"] = len(effects)


_SOUND_ANCHOR_STOPWORDS = {
    "a", "an", "and", "as", "at", "be", "been", "but", "by", "for", "from",
    "had", "has", "have", "he", "her", "his", "in", "into", "is", "it", "its",
    "of", "on", "or", "our", "she", "that", "the", "their", "them", "then",
    "there", "they", "this", "to", "was", "were", "which", "who", "with",
}


def _anchor_sound_cue_to_whisper(scene: Mapping[str, Any], cue: Dict[str, Any]) -> None:
    """Snap non-transition effects to a real, relevant Whisper word in-scene."""
    if cue.get("sound_anchor") or str(cue.get("timing_quality") or "").startswith("whisper"):
        return
    intent = str(cue.get("sound_intent") or "").casefold()
    reason = str(cue.get("reason") or "").casefold()
    if intent in {"transition", "template_signature", "text_entry", "background_bed", "quiet_texture"}:
        return
    if _sound_category(str(cue.get("kind") or "")) == "background":
        return

    words = []
    for phrase in scene.get("word_cues") or []:
        if not isinstance(phrase, Mapping) or not str(phrase.get("timing_quality") or "").startswith("whisper"):
            continue
        words.extend(word for word in phrase.get("timed_words") or [] if isinstance(word, Mapping))
    deduped = {}
    for word in words:
        try:
            index = int(word.get("index", -1))
            start = float(word.get("absolute_start", float(scene.get("start") or 0.0) + float(word.get("start") or 0.0)))
            end = float(word.get("absolute_end", float(scene.get("start") or 0.0) + float(word.get("end") or 0.0)))
        except (TypeError, ValueError):
            continue
        text = str(word.get("text") or "").strip()
        normalized = re.sub(r"[^a-z0-9]+", "", text.casefold())
        if index < 0 or not normalized or normalized in _SOUND_ANCHOR_STOPWORDS or (len(normalized) < 3 and not re.search(r"\d", normalized)):
            continue
        deduped[index] = {"index": index, "text": text, "start": start, "end": end, "normalized": normalized}
    candidates = list(deduped.values())
    if not candidates:
        return

    category = _sound_category(str(cue.get("kind") or ""))
    if category == "data":
        specialized = [row for row in candidates if re.search(r"\d", row["normalized"])]
        if specialized:
            candidates = specialized
    elif category == "spatial":
        specialized = [
            row for row in candidates
            if str(row["text"][:1]).isupper() and row["normalized"] not in _SOUND_ANCHOR_STOPWORDS
        ]
        if specialized:
            candidates = specialized

    scene_start = float(scene.get("start") or 0.0)
    scene_duration = max(0.08, float(scene.get("duration") or 0.5))
    try:
        planned_start = float(cue.get("start", scene_start) or scene_start)
    except (TypeError, ValueError):
        planned_start = scene_start

    if reason.startswith("punctuation:"):
        terminal_words = [row for row in candidates if re.search(r"[.!?][\])}'\"’”]*$", row["text"])]
        if terminal_words:
            chosen = min(terminal_words, key=lambda row: abs((row["end"] + 0.05) - planned_start))
            edge, offset, start = "end", 0.05, chosen["end"] + 0.05
        else:
            chosen = min(candidates, key=lambda row: abs(row["start"] - planned_start))
            edge, offset, start = "start", 0.0, chosen["start"]
    else:
        after = [row for row in candidates if row["start"] >= planned_start - 0.06]
        pool = after or candidates
        chosen = min(pool, key=lambda row: (abs(row["start"] - planned_start), row["start"], row["index"]))
        edge, offset, start = "start", 0.0, chosen["start"]

    start = max(scene_start, min(start, scene_start + scene_duration - 0.04))
    if edge == "end":
        offset = round(start - chosen["end"], 3)
    cue["start"] = round(start, 3)
    cue["time_local"] = round(start - scene_start, 3)
    cue["scene_local_start"] = cue["time_local"]
    cue["sound_anchor"] = chosen["text"]
    cue["sound_anchor_edge"] = edge
    cue["sound_anchor_offset_seconds"] = offset
    cue["timing_quality"] = "whisper_word_aligned"


def _sanitize_sound_cue_timeline(
    scenes: List[Dict[str, Any]],
    library: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Clamp effects to their scene and enforce authored variety at the final timeline."""
    repeat_gap = max(3.0, float(os.environ.get("VIDZEO_SFX_REPEAT_GAP_SECONDS", "6.0") or 6.0))
    category_gap = max(1.0, float(os.environ.get("VIDZEO_SFX_CATEGORY_GAP_SECONDS", "2.5") or 2.5))
    minimum_effect_gap = max(0.8, float(os.environ.get("VIDZEO_SFX_MIN_EFFECT_GAP_SECONDS", "1.35") or 1.35))
    total_duration = sum(max(0.0, float((scene or {}).get("duration") or 0.0)) for scene in (scenes or []))
    effects_per_minute = max(1.0, min(12.0, float(os.environ.get("VIDZEO_SFX_MAX_PER_MINUTE", "10.0") or 10.0)))
    max_effect_cues = max(1, int(math.ceil(total_duration / 60.0 * effects_per_minute)))
    # Procedural transition sounds are optional accents, not an event every
    # third scene. Keep atmosphere sparse while preserving the larger budget
    # for authored, narration-aligned graphic/counter/map events.
    max_procedural_transition_cues = max(1, int(math.ceil(total_duration / 60.0 * min(4.0, effects_per_minute))))

    def _procedural_transition(cue: Mapping[str, Any]) -> bool:
        return (str(cue.get("reason") or "").startswith("scene_rhythm:transition_")
                and not cue.get("visual_trigger") and not cue.get("sound_anchor"))
    before = 0
    after = 0
    dropped = 0
    exact_repeats = 0
    category_repeats = 0
    accepted: List[Dict[str, Any]] = []
    cursor = 0.0

    def _priority(cue: Mapping[str, Any]) -> int:
        if cue.get("sound_anchor") and str(cue.get("timing_quality") or "").startswith("whisper"):
            return 0
        if cue.get("visual_trigger"):
            return 1
        intent = str(cue.get("sound_intent") or "").lower()
        reason = str(cue.get("reason") or "").lower()
        if intent == "background_bed":
            return 30
        if "punctuation" in reason:
            return 25
        if intent in {"transition", "template_signature", "text_entry"}:
            return 5
        return 10

    def _spoken_anchor_start(scene: Mapping[str, Any], cue: Mapping[str, Any]) -> Optional[float]:
        anchor = str(cue.get("sound_anchor") or "").strip()
        anchor_tokens = [
            re.sub(r"[^a-z0-9]+", "", token.casefold())
            for token in re.findall(r"\d+(?:[.,]\d+)?|[A-Za-z]+(?:['’][A-Za-z]+)?", anchor)
        ]
        anchor_tokens = [token for token in anchor_tokens if token]
        if not anchor_tokens:
            return None
        words = []
        for phrase in scene.get("word_cues") or []:
            if not isinstance(phrase, Mapping) or not str(phrase.get("timing_quality") or "").startswith("whisper"):
                continue
            words.extend(word for word in (phrase.get("timed_words") or []) if isinstance(word, Mapping))
        words.sort(key=lambda word: int(word.get("index", 0) or 0))
        tokens = [
            re.sub(r"[^a-z0-9]+", "", str(word.get("text") or "").casefold())
            for word in words
        ]
        for offset in range(0, len(tokens) - len(anchor_tokens) + 1):
            if tokens[offset:offset + len(anchor_tokens)] != anchor_tokens:
                continue
            group = words[offset:offset + len(anchor_tokens)]
            indices = [int(word.get("index", -1)) for word in group]
            if indices != list(range(indices[0], indices[0] + len(indices))):
                continue
            try:
                scene_start = float(scene.get("start", 0.0) or 0.0)
                edge = str(cue.get("sound_anchor_edge") or "start").casefold()
                word = group[-1] if edge == "end" else group[0]
                local_time = float(word.get("end" if edge == "end" else "start", 0.0) or 0.0)
                offset = float(cue.get("sound_anchor_offset_seconds") or 0.0)
                return scene_start + local_time + offset
            except (TypeError, ValueError):
                return None
        return None

    for scene_index, scene in enumerate(scenes or []):
        try:
            scene_start = float(scene.get("start", cursor) or cursor)
        except (TypeError, ValueError):
            scene_start = cursor
        try:
            scene_duration = max(0.05, float(scene.get("duration", 0.0) or 0.0))
        except (TypeError, ValueError):
            scene_duration = 0.05
        scene_end = scene_start + scene_duration
        raw_cues = list(scene.get("sound_cues") or [])
        semantic_beat = scene.get("semantic_beat") if isinstance(scene.get("semantic_beat"), Mapping) else {}
        llm_directed_scene = str(semantic_beat.get("source") or "").startswith("openrouter")
        before += len(raw_cues)
        prepared: List[Dict[str, Any]] = []
        for order, raw in enumerate(raw_cues):
            if not isinstance(raw, Mapping):
                dropped += 1
                continue
            cue = dict(raw)
            if not llm_directed_scene:
                _anchor_sound_cue_to_whisper(scene, cue)
            kind = str(cue.get("kind") or "").strip()
            if not kind:
                dropped += 1
                continue
            intent = str(cue.get("sound_intent") or "").strip().lower()
            reason_text = str(cue.get("reason") or "").strip().lower()
            if intent in {"none", "no_sound", "none_needed", "silence"} or any(
                phrase in reason_text for phrase in ("no sound event needed", "no specific sound", "no sound needed")
            ):
                dropped += 1
                continue
            if llm_directed_scene and not cue.get("visual_trigger") and (
                not str(cue.get("sound_anchor") or "").strip()
                or str(cue.get("timing_quality") or "") != "whisper_word_aligned"
                or _sound_category(kind) == "background"
            ):
                dropped += 1
                continue
            if llm_directed_scene and not cue.get("visual_trigger"):
                spoken_start = _spoken_anchor_start(scene, cue)
                try:
                    planned_start = float(cue.get("start", scene_start) or scene_start)
                except (TypeError, ValueError):
                    planned_start = scene_start
                if spoken_start is None or abs(planned_start - spoken_start) > 0.10:
                    dropped += 1
                    continue
            expected_category = {
                "quiet_texture": "background",
                "background_bed": "background",
                "data_tick": "data",
                "impact": "impact",
                "location_ping": "spatial",
                "transition_air": "movement",
                "paper_action": "archive",
                "typing": "data",
            }.get(intent)
            if expected_category and _sound_category(kind) != expected_category:
                dropped += 1
                continue
            try:
                local_start = cue.get("time_local", None)
                if local_start is None:
                    local_start = float(cue.get("start", scene_start) or scene_start) - scene_start
                local_start = max(0.0, min(max(0.0, scene_duration - 0.04), float(local_start or 0.0)))
            except (TypeError, ValueError):
                local_start = 0.0
            try:
                cue_duration = max(0.08, float(cue.get("duration", 0.2) or 0.2))
            except (TypeError, ValueError):
                cue_duration = 0.2
            cue_duration = min(cue_duration, max(0.08, scene_duration - local_start))
            cue["start"] = round(scene_start + local_start, 3)
            cue["time_local"] = round(local_start, 3)
            cue["scene_local_start"] = round(local_start, 3)
            cue["duration"] = round(cue_duration, 3)
            cue["scene_index"] = scene_index
            cue["_order"] = order
            prepared.append(cue)

        # Keep the director's beat timing; use priority only to break ties.
        prepared.sort(key=lambda item: (_priority(item), float(item.get("time_local", 0.0)), int(item.get("_order", 0))))
        scene_kept = 0
        scene_role = str((scene.get("semantic_beat") or {}).get("role") or "").lower()
        scene_limit = 2 if any(cue.get("visual_trigger") for cue in prepared) else 1
        for cue in prepared:
            if scene_kept >= scene_limit:
                dropped += 1
                continue
            kind = str(cue.get("kind") or "")
            category = _sound_category(kind)
            abs_start = float(cue.get("start", scene_start) or scene_start)
            if category != "background":
                prior_effects = [
                    item for item in accepted
                    if _sound_category(str(item.get("kind") or "")) != "background"
                ]
                if len(prior_effects) >= max_effect_cues:
                    dropped += 1
                    continue
                if _procedural_transition(cue) and sum(_procedural_transition(item) for item in prior_effects) >= max_procedural_transition_cues:
                    dropped += 1
                    continue
                if prior_effects:
                    last_effect_start = max(float(item.get("start", 0.0) or 0.0) for item in prior_effects)
                    if any(abs(abs_start - float(item.get("start", 0.0) or 0.0)) < minimum_effect_gap for item in prior_effects):
                        dropped += 1
                        continue
            same_kind = [item for item in accepted if str(item.get("kind") or "") == kind]
            same_category = [item for item in accepted if _sound_category(str(item.get("kind") or "")) == category and category != "background"]
            if any(abs(abs_start - float(item.get("start", 0.0))) < repeat_gap for item in same_kind):
                exact_repeats += 1
                # If the library has a meaningful alternate, use it only when it
                # is also available and not immediately repeated. Otherwise drop.
                alternatives = []
                if library and isinstance(library, Mapping):
                    for candidate in library.keys():
                        candidate = str(candidate)
                        if _sound_category(candidate) != category or candidate == kind:
                            continue
                        if any(abs(abs_start - float(item.get("start", 0.0))) < repeat_gap for item in accepted if str(item.get("kind") or "") == candidate):
                            continue
                        alternatives.append(candidate)
                if alternatives:
                    alternatives.sort(key=lambda value: (sum(1 for item in accepted if str(item.get("kind") or "") == value), value))
                    cue["kind"] = alternatives[0]
                    kind = alternatives[0]
                    category = _sound_category(kind)
                    if isinstance(library.get(kind), Mapping):
                        replacement = library.get(kind) or {}
                        cue["src"] = _norm(replacement.get("src") or replacement.get("path"))
                        cue["rights_status"] = _norm(replacement.get("rights_status") or replacement.get("license") or ("unknown" if cue.get("src") else "missing"))
                        for metadata_name in ("publisher", "source_url", "attribution", "license"):
                            if replacement.get(metadata_name):
                                cue[metadata_name] = replacement.get(metadata_name)
                else:
                    dropped += 1
                    continue
            if any(abs(abs_start - float(item.get("start", 0.0))) < category_gap for item in same_category):
                category_repeats += 1
                dropped += 1
                continue
            cue.pop("_order", None)
            accepted.append(cue)
            scene_kept += 1

        scene["sound_cues"] = [cue for cue in accepted if int(cue.get("scene_index", -1)) == scene_index]
        for cue in scene["sound_cues"]:
            cue.pop("scene_index", None)
        after += len(scene["sound_cues"])
        cursor = scene_end

    final_cues = [cue for scene in scenes for cue in (scene.get("sound_cues") or [])]
    final_exact_repeats = 0
    final_category_repeats = 0
    for left_index, left in enumerate(final_cues):
        left_start = float(left.get("start", 0.0) or 0.0)
        left_kind = str(left.get("kind") or "")
        for right in final_cues[left_index + 1:]:
            right_start = float(right.get("start", 0.0) or 0.0)
            gap = abs(right_start - left_start)
            if gap >= repeat_gap:
                continue
            if str(right.get("kind") or "") == left_kind:
                final_exact_repeats += 1
            elif _sound_category(str(right.get("kind") or "")) == _sound_category(left_kind) and _sound_category(left_kind) != "background" and gap < category_gap:
                final_category_repeats += 1
    starts = sorted(float(cue.get("start", 0.0) or 0.0) for cue in final_cues)
    min_gap = None
    if len(starts) > 1:
        min_gap = round(min(starts[idx] - starts[idx - 1] for idx in range(1, len(starts))), 3)
    effect_starts = sorted(
        float(cue.get("start", 0.0) or 0.0)
        for cue in final_cues
        if _sound_category(str(cue.get("kind") or "")) != "background"
    )
    minimum_effect_gap_actual = None
    if len(effect_starts) > 1:
        minimum_effect_gap_actual = round(min(
            effect_starts[idx] - effect_starts[idx - 1]
            for idx in range(1, len(effect_starts))
        ), 3)
    return {
        "status": "ok",
        "before": before,
        "after": after,
        "dropped": dropped,
        "exact_repeat_conflicts": final_exact_repeats,
        "similar_category_conflicts": final_category_repeats,
        "attempted_exact_repeat_conflicts": exact_repeats,
        "attempted_similar_category_conflicts": category_repeats,
        "repeat_gap_seconds": repeat_gap,
        "category_gap_seconds": category_gap,
        "minimum_effect_gap_seconds": minimum_effect_gap,
        "max_effect_cues": max_effect_cues,
        "max_procedural_transition_cues": max_procedural_transition_cues,
        "effects_per_minute": effects_per_minute,
        "actual_minimum_effect_gap_seconds": minimum_effect_gap_actual,
        "minimum_cue_gap_seconds": min_gap,
        "effect_kinds": [str(cue.get("kind") or "") for cue in final_cues if _sound_category(str(cue.get("kind") or "")) != "background"],
        "effect_categories": sorted({
            _sound_category(str(cue.get("kind") or ""))
            for cue in final_cues
            if _sound_category(str(cue.get("kind") or "")) != "background"
        }),
        "effects_per_minute_actual": round(
            len(effect_starts) / max(0.01, total_duration / 60.0), 3
        ) if effect_starts else 0.0,
    }


def _contract_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _v2_storyboard_inputs(config: Mapping[str, Any]) -> Tuple[List[Any], List[Any], List[Any]]:
    """Read V2 rows from either a StoryboardV2 object or serialized mapping."""
    storyboard = config.get("storyboard_v2")
    if storyboard is None:
        return [], [], []
    script_plan = _contract_value(storyboard, "script_plan", None)
    scenes = list(_contract_value(script_plan, "scenes", ()) or ())
    containers = list(_contract_value(script_plan, "containers", ()) or ())
    words = list(_contract_value(storyboard, "word_timings", ()) or ())
    return scenes, containers, words


def _synthetic_v2_rows(
    texts: Sequence[str],
    durations: Sequence[float],
    beats: Sequence[Mapping[str, Any]],
    media_items: Sequence[Mapping[str, Any]],
    word_timings: Sequence[Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build exact local rows for callers migrating before ScriptPlanV1 wiring.

    These rows are intentionally conservative and are used only when V2 is
    explicitly enabled.  They preserve text/token/timeline spans and never
    infer cross-scene identity from the output title.
    """
    try:
        from .storyboard_v2.text import tokenize_source
    except Exception:
        tokenize_source = lambda value: re.findall(r"\w+", str(value or ""))  # type: ignore

    def word_value(row: Any, key: str, fallback: Any = None) -> Any:
        return _contract_value(row, key, fallback)

    rows: List[Dict[str, Any]] = []
    containers_by_id: Dict[str, Dict[str, Any]] = {}
    char_cursor = 0
    token_cursor = 0
    timeline = 0.0
    for index, text in enumerate(texts):
        try:
            duration = max(0.1, float(durations[index] if index < len(durations) else 3.0))
        except (TypeError, ValueError):
            duration = 3.0
        beat = beats[index] if index < len(beats) else {}
        media = media_items[index] if index < len(media_items) else {}
        container_id = _norm(
            beat.get("container_id") or beat.get("section_id")
            or media.get("container_id") or media.get("section_id")
        ) or f"scene_{index + 1}"
        treatment = _norm(beat.get("treatment") or "").lower()
        role = _norm(beat.get("role") or "").lower()
        kind = _norm(beat.get("container_kind") or beat.get("container_type") or "").lower()
        if not kind:
            kind = "chapter" if treatment == "chapter" or role == "chapter" else (
                "listicle_item" if treatment == "listicle" else "narrative"
            )
        scene_tokens = list(tokenize_source(text))
        scene_end = timeline + duration
        matching_words = []
        for fallback_index, word in enumerate(word_timings or []):
            try:
                word_index = int(word_value(word, "index", fallback_index))
                word_start = float(word_value(word, "start_seconds", word_value(word, "start", 0.0)) or 0.0)
                word_end = float(word_value(word, "end_seconds", word_value(word, "end", word_start)) or word_start)
            except (TypeError, ValueError):
                continue
            if word_end >= timeline - 0.08 and word_start <= scene_end + 0.08:
                matching_words.append(word_index)
        row = {
            "scene_id": _norm(beat.get("scene_id") or media.get("scene_id")) or f"scene-{index + 1}",
            "order": index,
            "container_id": container_id,
            "text": text,
            "char_start": char_cursor,
            "char_end": char_cursor + len(text),
            "token_start": token_cursor,
            "token_end": token_cursor + len(scene_tokens),
            "word_start_index": min(matching_words) if matching_words else None,
            "word_end_index": max(matching_words) + 1 if matching_words else None,
            "start_seconds": timeline,
            "end_seconds": scene_end,
            "timing_quality": "word_aligned" if matching_words else "pending",
            "alignment_confidence": 1.0 if matching_words else 0.0,
        }
        rows.append(row)
        if container_id not in containers_by_id:
            containers_by_id[container_id] = {
                "container_id": container_id,
                "kind": kind,
                "ordinal": beat.get("ordinal") or beat.get("marker_number"),
                "canonical_subject": _norm(
                    beat.get("container_subject") or beat.get("section_subject")
                    or media.get("container_subject") or media.get("section_subject")
                ),
                "char_start": row["char_start"],
                "char_end": row["char_end"],
                "text": text,
                "explicit_marker": bool(beat.get("explicit_marker") or beat.get("has_explicit_marker")),
            }
        else:
            containers_by_id[container_id]["char_end"] = row["char_end"]
            containers_by_id[container_id]["text"] += "\n" + text
        char_cursor += len(text) + 1
        token_cursor += len(scene_tokens)
        timeline = scene_end
    return rows, list(containers_by_id.values())


_TIMED_CAPTION_STOPWORDS = {
    "a", "an", "and", "as", "at", "be", "because", "by", "but", "can",
    "could", "did", "do", "does", "for", "from", "had", "has", "have", "he",
    "her", "here", "hers", "him", "his", "how", "i", "if", "in", "into", "is",
    "it", "its", "me", "my", "of", "on", "or", "our", "ours", "she", "so",
    "that", "the", "their", "theirs", "them", "then", "there", "they", "this",
    "those", "to", "too", "us", "was", "we", "were", "what", "when", "where",
    "which", "who", "why", "will", "with", "would", "you", "your", "yours",
    "everything", "something", "anything", "nothing", "more", "most", "often",
    "regularly", "usually", "really", "very", "actually", "basically", "just",
}


def _timed_word_cues(
    scene: Mapping[str, Any],
    words: Sequence[Any],
    duration: float,
    narration: str,
) -> List[Dict[str, Any]]:
    """Create short, grounded caption phrases from Whisper word timings.

    The displayed phrase is made only from the meaningful words in a compact
    spoken span, while its start/end covers the complete original span. This
    keeps the text editorially selective without making a phrase jump ahead of
    the audio or lose its connecting grammar.
    """
    raw_scene_start = _contract_value(scene, "start_seconds", _contract_value(scene, "start", None))
    raw_scene_end = _contract_value(scene, "end_seconds", _contract_value(scene, "end", None))
    if raw_scene_start is None and raw_scene_end is None:
        return []
    try:
        scene_start = float(raw_scene_start or 0.0)
    except (TypeError, ValueError):
        scene_start = 0.0
    scene_end = scene_start + max(0.1, float(duration or 0.1))
    try:
        raw_word_start = _contract_value(scene, "word_start_index", None)
        raw_word_end = _contract_value(scene, "word_end_index", None)
        word_start = int(raw_word_start) if raw_word_start is not None else None
        word_end = int(raw_word_end) if raw_word_end is not None else None
    except (TypeError, ValueError):
        word_start = word_end = None

    rows: List[Dict[str, Any]] = []
    for fallback_index, row in enumerate(words or ()):
        try:
            index = int(_contract_value(row, "index", fallback_index))
            text = str(_contract_value(row, "text", _contract_value(row, "word", "")) or "").strip()
            start = float(_contract_value(row, "start_seconds", _contract_value(row, "start", 0.0)) or 0.0)
            end = float(_contract_value(row, "end_seconds", _contract_value(row, "end", start)) or start)
            confidence = float(_contract_value(row, "confidence", _contract_value(row, "probability", 1.0)) or 0.0)
        except (TypeError, ValueError):
            continue
        if not text or end < scene_start - 0.05 or start > scene_end + 0.05:
            continue
        if word_start is not None and word_end is not None and not (word_start <= index < word_end):
            continue
        token = re.sub(r"[^A-Za-z0-9%'-]", "", text).lower()
        if not token:
            continue
        rows.append({
            "index": index,
            "text": text,
            "start": max(scene_start, start),
            "end": min(scene_end, max(start, end)),
            "confidence": max(0.0, min(1.0, confidence)),
            "important": bool(
                len(token) > 2
                and token not in _TIMED_CAPTION_STOPWORDS
                and (re.search(r"\d|%", token) or len(token) >= 4)
            ),
        })
    rows.sort(key=lambda item: (item["start"], item["index"]))
    important = [row for row in rows if row["important"] and row["confidence"] >= 0.42]
    if not important:
        return []

    groups: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    for row in important:
        if not current:
            current = [row]
            continue
        gap = float(row["start"]) - float(current[-1]["end"])
        if gap <= 1.15 and len(current) < 4:
            current.append(row)
        else:
            groups.append(current)
            current = [row]
    if current:
        groups.append(current)

    cues: List[Dict[str, Any]] = []
    for group in groups:
        first, last = group[0], group[-1]
        # Include the exact spoken connectors between selected words, but do
        # not allow a long sentence to become a caption card.
        span_rows = [
            row for row in rows
            if first["start"] - 0.01 <= row["start"] <= last["end"] + 0.01
        ]
        phrase = " ".join(str(row["text"]).strip() for row in span_rows if str(row["text"]).strip())
        phrase = re.sub(r"\s+", " ", phrase).strip(" ,.;:!?-")
        if not phrase or len(phrase.split()) > 7 or len(phrase) > 48:
            phrase = " ".join(str(row["text"]).strip() for row in group).strip()
        if not phrase:
            continue
        local_start = max(0.0, float(first["start"]) - scene_start - 0.04)
        local_end = min(float(duration), float(last["end"]) - scene_start + 0.16)
        if local_end - local_start < 0.32:
            local_end = min(float(duration), local_start + 0.45)
        if local_end <= local_start:
            continue
        cues.append({
            "text": phrase,
            "words": [str(row["text"]) for row in group],
            "timed_words": [
                {
                    "text": str(row["text"]),
                    "index": int(row["index"]),
                    "start": round(max(0.0, float(row["start"]) - scene_start), 3),
                    "end": round(max(0.0, float(row["end"]) - scene_start), 3),
                    "absolute_start": round(float(row["start"]), 3),
                    "absolute_end": round(float(row["end"]), 3),
                }
                for row in span_rows
            ],
            "word_start_index": int(first["index"]),
            "word_end_index": int(last["index"]) + 1,
            "start": round(local_start, 3),
            "end": round(local_end, 3),
            "absolute_start": round(scene_start + local_start, 3),
            "absolute_end": round(scene_start + local_end, 3),
            "timing_quality": "whisper_word_aligned",
        })
    return cues[:8]


def build_semantic_edit_manifest(
    scene_texts: Iterable[str],
    scene_durations: Iterable[float],
    media: Optional[Iterable[Mapping[str, Any]]] = None,
    editorial_beats: Optional[Iterable[Mapping[str, Any]]] = None,
    topic: str = "",
    category: str = "",
    config: Optional[Mapping[str, Any]] = None,
    variation_seed: Any = 0,
    scene_contracts: Optional[Iterable[Any]] = None,
    word_timings: Optional[Iterable[Any]] = None,
    containers: Optional[Iterable[Any]] = None,
) -> Dict[str, Any]:
    """Return a final-scene semantic manifest suitable for Player or export."""
    config = dict(config or {})
    if "variation_seed" not in config:
        config["variation_seed"] = variation_seed
    texts = [str(value or "").strip() for value in (scene_texts or [])]
    durations = list(scene_durations or [])
    media_items = [dict(value or {}) for value in (media or [])]
    beats = [dict(value or {}) for value in (editorial_beats or [])]
    if not texts:
        raise SemanticManifestError("At least one final narration scene is required")
    fps = max(1, min(120, int(config.get("fps", 30))))
    width = max(320, min(7680, int(config.get("width", 1920))))
    height = max(180, min(7680, int(config.get("height", 1080))))
    fallback_profile = deterministic_profile(
        topic,
        category,
        " ".join(texts),
        variation_seed=config.get("variation_seed", variation_seed),
    )
    editing_profile = normalize_profile(
        config.get("editing_profile") if isinstance(config.get("editing_profile"), Mapping) else None,
        fallback_profile,
    )
    theme = choose_theme(topic, category, str(config.get("theme_id") or config.get("style_profile") or ""))
    theme["palette"] = {**dict(theme.get("palette") or {}), **dict(editing_profile.get("palette") or {})}
    typography = editing_profile.get("typography") if isinstance(editing_profile.get("typography"), Mapping) else {}
    body_font = FONT_LIBRARY.get(str(typography.get("body") or ""), {})
    display_font = FONT_LIBRARY.get(str(typography.get("display") or ""), {})
    accent_font = FONT_LIBRARY.get(str(typography.get("accent") or ""), {})
    if body_font:
        theme["font_family"] = f"{body_font['family']}, Arial, sans-serif"
    if display_font:
        theme["display_font"] = f"{display_font['family']}, Arial, sans-serif"
    theme["accent_font"] = f"{accent_font.get('family', body_font.get('family', 'Inter'))}, Arial, sans-serif"
    transition_pack = TRANSITION_PACKS.get(str(editing_profile.get("transition_pack") or ""), [])
    if transition_pack:
        theme["transition_policy"] = list(transition_pack)
    theme["graphic_density"] = float(editing_profile.get("graphic_density", theme.get("graphic_density", 0.5)))
    theme["sound_density"] = float(editing_profile.get("sound_density", theme.get("sound_density", 0.5)))
    theme["_design_seed"] = editing_profile.get("seed")
    theme_overrides = config.get("theme_overrides")
    if isinstance(theme_overrides, Mapping):
        for key, value in theme_overrides.items():
            if isinstance(value, Mapping) and isinstance(theme.get(key), Mapping):
                theme[key] = {**theme[key], **dict(value)}
            else:
                theme[key] = value
    palette_accents = list(editing_profile.get("palette", {}).get("accents") or theme.get("palette", {}).get("accents") or [])
    palette_niche = str(editing_profile.get("palette", {}).get("niche") or editing_profile.get("niche") or "").lower()
    theme["palette"] = {
        **dict(theme.get("palette") or {}),
        "primary": "#ffffff",
        "secondary": "#e7ebef",
        "accents": palette_accents,
        "niche": palette_niche,
    }
    theme["caption"] = {
        **dict(theme.get("caption") or {}),
        "color": "#ffffff",
        "shadow": "0 2px 8px rgba(0,0,0,.78)",
    }
    theme["safe_area"] = {"x": 0.08, "y": 0.08, "width": 0.84, "height": 0.84}
    theme["typography_policy"] = {
        "display": f"{display_font.get('family', 'Bebas Neue')} with seeded documentary variants",
        "body": f"{body_font.get('family', 'Open Sans')} sentence-case supporting detail",
        "accent": f"{accent_font.get('family', 'Playfair Display')} for contextual emphasis",
        "shadow": "black 0 2px 8px",
        "direction": (editing_profile.get("art_direction") or {}).get("id", "editorial"),
    }
    scenes: List[Dict[str, Any]] = []
    transitions: List[Dict[str, Any]] = []
    recent_templates: List[str] = []
    recent_vox_templates: List[str] = []
    recent_motion: List[str] = []
    recent_still_motion: List[str] = []
    recent_transitions: List[str] = []
    recent_treatments: List[str] = []
    recent_structures: List[str] = []
    recent_text_positions: List[str] = []
    recent_card_modes: List[str] = []
    recent_entry_motions: List[str] = []
    template_usage: Dict[str, int] = {}
    # Persistent cross-video template history (to avoid repeating same templates in consecutive videos)
    _cross_video_template_file = Path("cache/.template_history.json")
    cross_video_recent: List[str] = []
    if _cross_video_template_file.exists():
        try:
            cross_video_recent = json.loads(_cross_video_template_file.read_text(encoding="utf-8"))[-10:]
        except Exception:
            cross_video_recent = []
    structure_usage: Dict[str, int] = {}
    recent_sfx: List[str] = []
    hero_count = 0
    hero_limit = max(1, int(round(len(texts) * 0.15)))
    offset = 0.0
    sound_library = config.get("sound_library") if isinstance(config.get("sound_library"), Mapping) else None
    variation_seed = config.get("variation_seed", 0)
    last_full_stop_whoosh_time = -10.0
    sfx_usage_counts: Dict[str, int] = {}
    normalized_beats = [
        _semanticize_beat(
            _beat(beats[index] if index < len(beats) else None, texts[index], index, len(texts)),
            texts[index], index, len(texts),
        )
        for index in range(len(texts))
    ]
    storyboard_scenes, storyboard_containers, storyboard_words = _v2_storyboard_inputs(config)
    v2_scenes = list(scene_contracts or config.get("scene_contracts") or storyboard_scenes or [])
    v2_containers = list(containers or config.get("script_containers") or storyboard_containers or [])
    v2_words = list(
        word_timings
        or config.get("word_timings")
        or config.get("whisper_words")
        or storyboard_words
        or []
    )
    pipeline_v2_mode = _norm(config.get("pipeline_v2_mode") or "").lower()
    cue_v2_shadow = pipeline_v2_mode == "shadow" and bool(v2_scenes and v2_words)
    if "editorial_cue_v2_enabled" in config:
        cue_v2_enabled = bool(config.get("editorial_cue_v2_enabled"))
    else:
        cue_v2_enabled = pipeline_v2_mode != "shadow"
    cue_v2_planning = True

    # Motion is part of the media contract, not only a decoration for text
    # cards. Keep the explicit opt-out for preview/editorial tooling, but let
    # production stills and cleaned YouTube frames receive the same restrained
    # treatment selector as graphic scenes.
    animate_all_media = bool(config.get("animate_all_media", True))
    if cue_v2_planning and not v2_scenes:
        v2_scenes, synthetic_containers = _synthetic_v2_rows(
            texts, durations, normalized_beats, media_items, v2_words,
        )
        if not v2_containers:
            v2_containers = synthetic_containers
    editorial_cues_v2: List[Any] = []
    if cue_v2_planning:
        try:
            from .editorial_cue_planner import plan_editorial_cues
            editorial_cues_v2 = list(plan_editorial_cues(
                v2_scenes,
                v2_words,
                containers=v2_containers,
                editorial_beats=normalized_beats,
                media=media_items,
                captions_enabled=bool(
                    config.get("editorial_captions_enabled", config.get("captions_enabled", False))
                ),
                caption_band=config.get("caption_band"),
                variation_seed=config.get("variation_seed", variation_seed),
                art_direction=editing_profile.get("art_direction"),
            ))
        except Exception as exc:
            # Fail closed: a cue-planning failure must remove automatic text,
            # not resurrect legacy fabricated/default cards.
            print(f"   [editorial-cue-v2] planning suppressed: {exc}")
            editorial_cues_v2 = []
    cue_by_scene_id = {
        str(_contract_value(cue, "scene_id", "")): cue for cue in editorial_cues_v2
    }
    # Editorial target: one explanatory graphic about every three beats (~30% of
    # scene count). Factual turns and passages that genuinely need a map, chart,
    # timeline, or diagram can exceed the target; captions and decorative copy
    # are not counted as explanatory graphics.
    # Intervening scenes stay clean, fast-paced cinematic cuts (push/pan/drift) without visual card clutter.
    # Facts and figures (money, numbers, stats, percentages, dates, locations) are prioritized for charts/counters.
    n_scenes = len(texts)
    target_density = float(config.get("graphic_density") or editing_profile.get("graphic_density") or theme.get("graphic_density") or 0.30)
    graphic_indices = set()
    if n_scenes <= 1:
        graphic_indices = {0}
    elif n_scenes == 2:
        graphic_indices = {0}
        b1 = normalized_beats[1] if 1 < len(normalized_beats) else {}
        b1_treatment = str(b1.get("treatment") or "").lower()
        b1_role = str(b1.get("role") or "").lower()
        has_b1_figure = bool(
            _MONEY_RE.search(texts[1])
            or _STAT_RE.search(texts[1])
            or _LOCATION_RE.search(texts[1])
            or _DATE_RE.search(texts[1])
            or b1_treatment in {"location", "map", "date", "counter", "chart", "money", "statistic", "quote", "person"}
            or b1_role in {"location", "evidence", "climax", "turn", "person", "quote"}
            or target_density > 0.5
        )
        if has_b1_figure:
            graphic_indices.add(1)
    else:
        # Scene 0 is always the Cold Open / Hook Opener
        graphic_indices.add(0)

        def _scene_fact_and_figure_score(idx: int) -> float:
            """Score scene by factual prominence (numbers, money, stats, dates, locations)."""
            t = texts[idx]
            b = normalized_beats[idx]
            sc = 0.0
            # Currency / revenue / valuation
            if _MONEY_RE.search(t) or any(w in t.lower() for w in ("$", "dollar", "billion", "million", "usd", "cost")):
                sc += 14.0
            # Numbers / percentages / stats
            numbers = re.findall(r"\b\d+(?:\.\d+)?%?\b", t)
            if numbers:
                sc += 9.0 + min(4.0, len(numbers) * 2.0)
            if _STAT_RE.search(t) or "%" in t or "percent" in t.lower():
                sc += 8.0
            # Dates / chronological milestones
            if _DATE_RE.search(t):
                sc += 7.0
            # Geographic locations / routes
            if _LOCATION_RE.search(t):
                sc += 6.0
            # Treatment hints
            treatment = str(b.get("treatment") or "").lower()
            if treatment in {"counter", "chart", "money"}:
                sc += 8.0
            elif treatment in {"date", "location", "quote", "person"}:
                sc += 5.0
            role = str(b.get("role") or "").lower()
            if role in {"location", "evidence", "climax", "turn", "person", "quote"}:
                sc += 4.0
            # Grounded editorial cue boost
            if cue_v2_enabled and idx < len(v2_scenes):
                if str(_contract_value(v2_scenes[idx], "scene_id", "")) in cue_by_scene_id:
                    sc += 10.0
            # Narrative importance / turning point
            sc += float(b.get("importance") or 0.0) * 4.0
            return sc

        # Stride through remaining scenes in editorial windows of 2 to 3 scenes
        curr = 1
        while curr < n_scenes:
            rem = n_scenes - curr
            if rem == 3:
                # 3 scenes left: take all 3 as a single window so the last scene doesn't get isolated
                window_len = 3
            elif rem == 1:
                # Trailing solitary scene: don't force a graphic unless it has a heavy fact/stat
                if _scene_fact_and_figure_score(curr) >= 9.0:
                    graphic_indices.add(curr)
                break
            elif target_density < 0.40:
                window_len = 3 if rem >= 3 else rem
            elif target_density > 0.70:
                # In high density mode, allow 1 graphic every 1-2 scenes
                window_len = 1 if ((curr + variation_seed) % 2 == 0) and rem >= 2 else 2
                window_len = min(rem, window_len)
            else:
                # Standard fast-paced editorial cadence: 1 graphic every 2 scenes
                window_len = 2 if rem >= 2 else rem

            window = list(range(curr, min(n_scenes, curr + window_len)))
            best_scene = max(window, key=_scene_fact_and_figure_score)
            graphic_indices.add(best_scene)
            if len(window) > 1:
                for scn in window:
                    if scn != best_scene and (
                        (target_density > 0.70 and _scene_fact_and_figure_score(scn) >= 6.0)
                        or _scene_fact_and_figure_score(scn) >= 15.0
                    ):
                        graphic_indices.add(scn)
            curr += window_len

    for index, text in enumerate(texts):
        try:
            duration = max(0.1, float(durations[index] if index < len(durations) else 3.0))
        except Exception:
            duration = 3.0
        beat = normalized_beats[index]
        media_item = media_items[index] if index < len(media_items) else {}
        visual_decision = media_item.get("visual_decision") if isinstance(media_item.get("visual_decision"), Mapping) else {}
        focal_bbox = _verified_target_bbox(media_item)
        safe_regions = visual_decision.get("safe_text_regions") if isinstance(visual_decision.get("safe_text_regions"), list) else []
        segmentation = bool(visual_decision.get("segmentation_mask") or media_item.get("segmentation_mask"))
        distinct_assets = _distinct_media_assets(media_item)
        media_src = _norm(media_item.get("src") or media_item.get("url") or media_item.get("path"))
        media_type = _norm(media_item.get("type") or ("video" if media_item.get("is_video") else "image")) or "image"
        media_type_key = str(media_type).strip().lower()
        is_still_media = media_type_key not in {"video", "video/mp4", "video/webm"} and Path(media_src).suffix.lower() not in {
            ".mp4", ".mov", ".webm", ".avi", ".mkv", ".m4v",
        }
        # Compute full sentence context bounded by full stops or script boundaries
        start_bound = index
        while start_bound > 0 and not re.search(r'''[.!?]["'”’]?\s*$''', texts[start_bound - 1]):
            start_bound -= 1
        end_bound = index
        while end_bound < len(texts) - 1 and not re.search(r'''[.!?]["'”’]?\s*$''', texts[end_bound]):
            end_bound += 1
        sentence_text = ' '.join(texts[start_bound:end_bound + 1])

        quantitative_intent = _quantitative_intent(text, beat, context_text=sentence_text)
        # Text graphics are accents, not subtitles. The selected budget above
        # is the only gate; ordinary narration scenes remain visual-first.
        importance = float(beat.get("importance") or 0.0)
        role = str(beat.get("role") or "")
        # In LLM-governed mode, respect explicit False selection decisions.
        # In deterministic/test mode (selected_for_text is None), preserve planned cadence.
        if beat.get("selected_for_text") is False:
            graphics_enabled = False
        else:
            graphics_enabled = index in graphic_indices
        scene_contract = v2_scenes[index] if cue_v2_enabled and index < len(v2_scenes) else None
        editorial_cue = (
            cue_by_scene_id.get(str(_contract_value(scene_contract, "scene_id", "")))
            if scene_contract is not None else None
        )
        # An explicitly selected, exact source-backed cue is authored direction.
        # The automatic 30% scheduling budget may choose different beats, but
        # must not silently erase a specifically requested person/fact label.
        # Plain True or ungrounded emphasis alone cannot authorize visible copy.
        if (beat.get("selected_for_text") is True and beat.get("_emphasis_explicit")
                and editorial_cue is not None
                and _norm(beat.get("emphasis")).casefold() in _norm(_contract_value(editorial_cue, "text", "")).casefold()):
            graphics_enabled = True
        palette_niche = str(theme.get("palette", {}).get("niche") or editing_profile.get("palette", {}).get("niche") or editing_profile.get("niche") or "").lower()
        pool_accents = (
            list(theme.get("palette", {}).get("accents") or [])
            or list(editing_profile.get("palette", {}).get("accents") or [])
            or [theme.get("palette", {}).get("accent", "#00f0ff")]
        )
        pool_accents = [c for c in pool_accents if c] or ["#00f0ff", "#ff007f", "#ffd700", "#8b5cf6"]
        scene_accent = pool_accents[index % len(pool_accents)]
        scene_secondary = pool_accents[(index + 1) % len(pool_accents)]
        scene_palette = {
            **dict(theme.get("palette") or {}),
            "accent": scene_accent,
            "secondary": scene_secondary,
            "niche": palette_niche,
        }
        if graphics_enabled and editorial_cue is not None:
            template_id = str(_contract_value(editorial_cue, "template", "full-bleed") or "full-bleed")
            template_id = _vary_contract_template(
                template_id,
                recent=recent_templates,
                usage=template_usage,
                total=len(texts),
                media_assets=distinct_assets,
                quantitative=quantitative_intent,
                recent_structures=recent_structures,
                structure_usage=structure_usage,
            )
        elif graphics_enabled:
            template_id = _choose_template(
                beat, text, index, recent_templates,
                usage=template_usage, total=len(texts), media_item=media_item,
                design_seed=editing_profile.get("seed"),
                recent_structures=recent_structures,
                structure_usage=structure_usage,
                preferred_template=str(config.get("selected_template") or ""),
                force_graphic=graphics_enabled,
            )
            if template_id in recent_templates[-2:] and template_id != "full-bleed":
                neutral_fallbacks = [
                    "field-caption", "exhibit-label", "kinetic-statement",
                    "keyword-reveal", "word-cascade", "lower-third",
                    "archival-frame",
                ]
                fallback_offset = stable_seed(text, index, variation_seed, "repeat-fallback") % len(neutral_fallbacks)
                neutral_fallbacks = neutral_fallbacks[fallback_offset:] + neutral_fallbacks[:fallback_offset]
                template_id = next(
                    (
                        candidate for candidate in neutral_fallbacks
                        if candidate not in recent_templates[-2:]
                        and _template_is_eligible(candidate, template_usage, len(texts), distinct_assets)
                        and _structure_is_eligible(
                            candidate,
                            quantitative=quantitative_intent,
                            recent_structures=recent_structures,
                            structure_usage=structure_usage,
                            total=len(texts),
                        )
                    ),
                    "full-bleed",
                )
        else:
            template_id = "full-bleed"
        template = _template(template_id)
        visual_structure = _visual_structure(template_id)
        transition_id = _transition(beat, index, recent_transitions, theme)
        # Graphics carry the edit. Source media defaults to dynamic motion;
        # every image gets rich Ken Burns movement (zoom, pan, drift).
        motion_options = {
            "calm": ["zoom_in_slight", "zoom_out_slight", "drift_left", "drift_right", "rotate_gentle", "settle"],
            "reflective": ["zoom_out_slight", "drift_left", "zoom_in_slight", "drift_right", "rotate_gentle", "settle"],
            "tense": ["zoom_in", "zoom_fast", "pan_right", "pan_left", "drift_left"],
            "high": ["zoom_in", "zoom_fast", "pan_left", "pan_right", "pan_diagonal_tr"],
            "urgent": ["zoom_fast", "zoom_in", "pan_diagonal_tr", "pan_left"],
            "energetic": ["zoom_in", "pan_right", "pan_left", "zoom_fast", "pan_diagonal_bl"],
            "normal": ["zoom_in_slight", "zoom_out_slight", "drift_left", "drift_right", "pan_left", "pan_right", "rotate_gentle"],
        }.get(beat.get("energy") or "normal", ["zoom_in_slight", "drift_left", "pan_right"])
        if str(beat.get("treatment") or "").lower() in {"blueprint", "decision", "newspaper", "countdown", "redaction", "document", "archive"}:
            motion_options = ["settle", "drift_left", "drift_right"]
        motion = _choose_flow_motion(
            motion_options,
            text=text,
            index=index,
            variation_seed=variation_seed,
            recent_motion=recent_motion,
            recent_still_motion=recent_still_motion,
            is_still_media=is_still_media,
        )

        # Still-image beats get their own adjacency guard.  A video scene in
        # between two stills must not reset the image animation rhythm, and a
        # one-item treatment pool must still fall back to a different camera
        # beat rather than repeating the same move back-to-back.
        still_previous = recent_still_motion[-1] if recent_still_motion else ""
        if is_still_media and still_previous and motion == still_previous:
            alternatives = [candidate for candidate in motion_options if candidate != still_previous]
            if not alternatives:
                alternatives = [
                    "zoom_in_slight", "zoom_out_slight", "drift_left", "drift_right",
                    "pan_left", "pan_right", "settle",
                ]
                alternatives = [candidate for candidate in alternatives if candidate != still_previous]
            if alternatives:
                motion = alternatives[stable_seed(text, index, variation_seed, "still-motion-repeat") % len(alternatives)]
        if recent_motion and motion == recent_motion[-1]:
            alternatives = [candidate for candidate in motion_options if candidate != recent_motion[-1]]
            if alternatives:
                motion = alternatives[stable_seed(text, index, variation_seed, "motion-repeat") % len(alternatives)]
        try:
            long_still_motion_seconds = max(
                4.0,
                min(
                    30.0,
                    float(
                        config.get("long_still_motion_seconds")
                        or os.environ.get("VIDZEO_LONG_STILL_MOTION_SECONDS", "8")
                        or 8
                    ),
                ),
            )
        except (TypeError, ValueError):
            long_still_motion_seconds = 8.0
        long_still_motion = bool(
            media_type != "video" and duration >= long_still_motion_seconds
        )
        motion_level = _motion_level(template_id, beat, hero_count, hero_limit)
        if motion_level == "hero":
            hero_count += 1
        overlays = (
            [] if editorial_cue is not None
            else (_scene_overlays(template_id, text, beat, media_item, topic, index) if graphics_enabled else [])
        )
        graphic_seed = stable_seed(text, index, variation_seed, template_id)
        if editorial_cue is not None:
            graphic_props = dict(_contract_value(editorial_cue, "props", {}) or {})
            emphasis_tokens = {
                token.lower()
                for token in re.findall(
                    r"[A-Za-z0-9][A-Za-z0-9'%/-]*",
                    _norm(beat.get("emphasis")),
                )
            }
            narration_tokens = {
                token.lower()
                for token in re.findall(
                    r"[A-Za-z0-9][A-Za-z0-9'%/-]*",
                    str(text or ""),
                )
            }
            if emphasis_tokens and not (emphasis_tokens & narration_tokens):
                contract_text = str(graphic_props.get("_contract_text") or "").strip()
                grounded_tokens = re.findall(
                    r"[A-Za-z0-9][A-Za-z0-9'%/-]*", str(text or "")
                )
                if len(contract_text.split()) > 1 and grounded_tokens:
                    # EditorialCueV2 bypasses _editorial_text_contract, so
                    # apply the same stale-emphasis guard to its props.
                    graphic_props["_contract_text"] = grounded_tokens[-1]
            # EditorialCueV2 is the production path and normally bypasses the
            # legacy text-contract builder below. Carry the video-level
            # creative director's validated motion choice into the grounded
            # cue props so preview and FFmpeg use the same entry animation.
            requested_entry_motion = str(editing_profile.get("text_entry_motion") or "").strip().lower()
            allowed_entry_motions = {
                *_ENTRY_MOTION_POOL,
            }
            if requested_entry_motion in allowed_entry_motions or str(graphic_props.get("_entry_motion") or "").strip().lower() in allowed_entry_motions:
                chosen_entry_motion = _vary_entry_motion(
                    str(graphic_props.get("_entry_motion") or requested_entry_motion),
                    text=text,
                    index=index,
                    variation_seed=variation_seed,
                    recent=recent_entry_motions,
                )
                if chosen_entry_motion:
                    graphic_props["_entry_motion"] = chosen_entry_motion
            cue_local_start = float(_contract_value(editorial_cue, "scene_local_start_seconds", 0.0) or 0.0)
            cue_local_end = float(_contract_value(editorial_cue, "scene_local_end_seconds", cue_local_start) or cue_local_start)
            try:
                from .editorial_cue_planner import cue_to_manifest_dict
                cue_manifest = cue_to_manifest_dict(editorial_cue)
            except Exception:
                cue_manifest = editorial_cue.to_dict() if hasattr(editorial_cue, "to_dict") else dict(editorial_cue)
            if graphic_props.get("_entry_motion") and isinstance(cue_manifest, Mapping):
                cue_manifest = dict(cue_manifest)
                cue_manifest["props"] = {
                    **dict(cue_manifest.get("props") or {}),
                    "_entry_motion": graphic_props.get("_entry_motion"),
                }
            graphic = {
                "template_id": template_id,
                "family": template.get("family"),
                "props": graphic_props,
                "palette": scene_palette,
                "variant": graphic_seed % max(1, int(template.get("variationCount") or 1)),
                "start": round(offset + cue_local_start, 3),
                "end": round(offset + cue_local_end, 3),
                "scene_local_start": round(cue_local_start, 3),
                "scene_local_end": round(cue_local_end, 3),
                "duration": round(max(0.001, cue_local_end - cue_local_start), 3),
                "cue": cue_manifest,
                "seed": graphic_seed,
            }
        else:
            graphic_props = _graphic_props(template_id, text, beat, topic, focal_bbox=focal_bbox)
            graphic = {"template_id": template_id, "family": template.get("family"), "props": graphic_props, "palette": scene_palette, "variant": graphic_seed % max(1, int(template.get("variationCount") or 1)), "start": round(offset, 3), "duration": round(min(duration, max(0.2, duration * 0.88)), 3), "seed": graphic_seed}
        # Native export follows the same semantic template chosen for preview.
        # The adapter only translates the ID/prop names required by FFmpeg; it
        # no longer lets a second random router select a different composition.
        vox_template = _native_template_id(template_id)
        vox_props = _native_props_for_template(vox_template, graphic_props, beat, text, topic)
        if vox_template and editorial_cue is None:
            display_contract = _editorial_text_contract(
                template_id=vox_template,
                props=vox_props,
                beat=beat,
                text=text,
                topic=topic,
                index=index,
                variation_seed=variation_seed,
                editing_profile=editing_profile,
                recent_positions=recent_text_positions,
                recent_card_modes=recent_card_modes,
            )
            if not display_contract.get("_contract_text"):
                grounded_text = str(
                    vox_props.get("title")
                    or vox_props.get("text")
                    or vox_props.get("location")
                    or vox_props.get("label")
                    or ""
                ).strip()
                if grounded_text and grounded_text.upper() not in _DISPLAY_GENERIC_LABELS:
                    display_contract["_contract_text"] = grounded_text[:48]
                else:
                    template_id = "full-bleed"
                    template = _template(template_id)
                    graphic["template_id"] = template_id
                    graphic["family"] = template.get("family")
                    graphic["props"] = {}
                    graphic_props = {}
                    vox_template = ""
                    vox_props = {}
            if graphics_enabled and display_contract.get("_entry_motion"):
                display_contract["_entry_motion"] = _vary_entry_motion(
                    display_contract["_entry_motion"],
                    text=text,
                    index=index,
                    variation_seed=variation_seed,
                    recent=recent_entry_motions,
                )
            graphic_props.update(display_contract)
            vox_props.update(display_contract)
            graphic["props"] = graphic_props
            if display_contract.get("_position"):
                recent_text_positions.append(str(display_contract["_position"]))
            if display_contract.get("_card_mode"):
                recent_card_modes.append(str(display_contract["_card_mode"]))
        if not graphics_enabled:
            # Non-graphic scenes remain clean cinematic full-bleed media without cards
            template_id = "full-bleed"
            template = _template("full-bleed")
            graphic = {
                "template_id": "full-bleed",
                "family": template.get("family"),
                "props": {},
                "palette": scene_palette,
                "variant": 0,
                "start": round(offset, 3),
                "duration": round(duration, 3),
                "seed": graphic_seed,
            }
            graphic_props = {}
            vox_template = ""
            vox_props = {}
        if vox_template:
            graphic["vox_template"] = vox_template
            graphic["vox_props"] = vox_props
            recent_vox_templates.append(vox_template)
            if len(recent_vox_templates) > 5:
                recent_vox_templates = recent_vox_templates[-5:]
        preferred_treatment = _image_treatment(
            str(editing_profile.get("image_treatment_pack") or "hold"),
            text=text,
            index=index,
            variation_seed=variation_seed,
            recent=recent_treatments,
            focal_bbox=focal_bbox,
            segmentation=segmentation,
            distinct_assets=distinct_assets,
        )
        if not graphics_enabled and not animate_all_media and not long_still_motion:
            preferred_treatment = "hold"
        media_request = None
        wants_comparison_media = (
            str(beat.get("role") or "") == "comparison"
            or str(beat.get("treatment") or "") in {"listicle", "gallery", "comparison"}
        )
        if bool(config.get("allow_semantic_media_requests", True)) and wants_comparison_media and distinct_assets < 3:
            media_request = {
                "count": 3,
                "media_type": "image",
                "query_anchor": _norm(topic)[:80],
                "facets": [_norm(beat.get("emphasis"))[:72], _norm(text)[:120]],
                "reason": "multi_image_comparison",
                "required": False,
                "fallback": preferred_treatment,
            }
        overlay_pack = OVERLAY_LIBRARY.get(str(editing_profile.get("overlay_pack") or ""), OVERLAY_LIBRARY["clean"])
        # A very light grain layer is the continuous visual glue. Pack-specific
        # textures and light leaks are accents, not full-time wallpaper.
        overlay_layers = list(dict.fromkeys(
            list(overlay_pack.get("layers") or []) + ["grain", "light_leak"]
        ))
        # A project has a coherent texture language, but not every layer fires
        # on every scene. This keeps atmosphere authored instead of wallpaper.
        base_layer = "grain"
        active_layers = [base_layer]
        active_layers.extend(
            layer for layer in overlay_layers
            if layer != base_layer
            and stable_seed(text, index, layer, variation_seed) % 100
            < (
                (30 if motion_level == "hero" else 14)
                if layer == "light_leak"
                else (62 if motion_level == "hero" else 27)
            )
        )
        if beat["energy"] in {"high", "urgent", "tense"} and "light_leak" in overlay_layers and "light_leak" not in active_layers:
            active_layers.append("light_leak")
        active_layers = list(dict.fromkeys(active_layers))[:3]
        atmosphere = {
            "grain": round(float(theme.get("grade", {}).get("grain", 0.08)), 3),
            "vignette": round(float(theme.get("grade", {}).get("vignette", 0.12)), 3),
            "dust": round(0.12 + (stable_seed(text, index, "dust") % 13) / 100, 3),
            "light_leak": bool(beat["energy"] in {"high", "urgent", "tense"} or motion_level == "hero"),
            "layers": active_layers,
            "pack": editing_profile.get("overlay_pack"),
            "intensity": round(float(overlay_pack.get("intensity") or 0.12), 3),
            "seed": stable_seed(text, index, variation_seed, "atmosphere"),
        }
        scene_sfx_options = list(SFX_PACKS.get(str(editing_profile.get("sfx_pack") or ""), []))
        hook_phase = offset < float((editing_profile.get("hook_strategy") or {}).get("duration_seconds") or 18)
        if hook_phase:
            scene_sfx_options = list(dict.fromkeys(list(SFX_PACKS.get("hook", [])) + scene_sfx_options))
        stable_scene_id = str(_contract_value(scene_contract, "scene_id", "")) if scene_contract is not None else ""
        stable_container_id = str(_contract_value(scene_contract, "container_id", "")) if scene_contract is not None else ""
        scene = {
            "id": stable_scene_id or f"scene-{index + 1}",
            "scene_id": stable_scene_id or f"scene-{index + 1}",
            "palette": scene_palette,
            "container_id": stable_container_id or _norm(beat.get("container_id") or "general"),
            "scene_index": index, "start": round(offset, 3),
            "duration": round(duration, 3), "narration": text,
            "source_span": {
                "char_start": _contract_value(scene_contract, "char_start", None),
                "char_end": _contract_value(scene_contract, "char_end", None),
                "token_start": _contract_value(scene_contract, "token_start", None),
                "token_end": _contract_value(scene_contract, "token_end", None),
                "word_start_index": _contract_value(scene_contract, "word_start_index", None),
                "word_end_index": _contract_value(scene_contract, "word_end_index", None),
                "timing_quality": _contract_value(scene_contract, "timing_quality", "legacy"),
            },
            "media": {
                "src": media_src, "type": media_type,
                "verified": bool(media_item.get('verified')),
                "graphic_fallback": dict(media_item.get('graphic_fallback') or {}),
                "visual_decision": dict(visual_decision),
                # Multiple real-media beats may belong to one narration
                # scene. The native renderer consumes this list without
                # changing the parent scene/audio/caption timing.
                "shots": [dict(shot) for shot in (media_item.get("shots") or []) if isinstance(shot, Mapping)],
                # Stills default to subject-preserving containment.  A caller
                # can still request an explicit ``cover`` treatment, but the
                # normal web-image path must not crop the verified object just
                # to fill a 16:9 canvas.
                "fit": _norm(
                    media_item.get("fit")
                    or (
                        "contain"
                        if str(media_type).strip().lower() not in {"video", "video/mp4", "video/webm"}
                        and media_item.get("safe_framing", True) is not False
                        else "cover"
                    )
                ),
                # The native renderer uses the same contract to keep stills
                # face-safe.  An explicit fit remains an intentional override;
                # otherwise the Remotion preview gets a blurred contain stage
                # instead of silently cutting portrait subjects.
                "safe_framing": bool(
                    str(media_type).strip().lower() not in {"video", "video/mp4", "video/webm"}
                    and not media_item.get("fit")
                    and media_item.get("safe_framing", True) is not False
                ),
                "position": _norm(media_item.get("position") or "center"),
                "rights_status": _norm(media_item.get("rights_status") or "unknown"),
                "source": _norm(media_item.get("source") or media_item.get("provider") or ""),
                "source_url": _norm(media_item.get("source_url") or media_item.get("publisher_url") or ""),
                "subject_bbox": focal_bbox,
            },
            "semantic_beat": beat, "graphic": graphic, "overlays": overlays,
            "visual_requirement": dict(media_item.get('visual_requirement') or {}),
            "motion_level": motion_level,
            "transition": {
                "template_id": transition_id,
                "duration": round(min(0.8, max(0.12, float(config.get("transition_duration", 0.24)))), 3),
            },
            "camera": {
                "motion": motion,
                "motion_family": _motion_family(motion),
                "seed": stable_seed(text, index, variation_seed, "camera"),
                "scale": 1.0,
            },
            "atmosphere": atmosphere,
            "design": {
                "image_treatment": preferred_treatment,
                "text_style": editing_profile.get("text_style_pack"),
                "sfx_options": scene_sfx_options,
                "hook_phase": hook_phase,
                "focal_bbox": focal_bbox,
                "safe_text_regions": safe_regions[:3],
                "vision_reused": bool(visual_decision),
                "multi_image_available": distinct_assets >= 3,
                "long_still_motion": long_still_motion,
                "long_still_motion_seconds": round(long_still_motion_seconds, 3),
                "media_request": media_request,
                "graphics_enabled": graphics_enabled,
                "visual_structure": visual_structure,
                "template_family": template.get("family"),
                "template_variant": graphic.get("variant", 0),
                "template_position": (graphic.get("props") or {}).get("_position"),
                "template_shuffle": "semantic_pool_v2",
                "semantic_visual_intent": quantitative_intent,
                "seed": stable_seed(text, index, variation_seed, "scene-design"),
            },
            "sound_cues": [],
        }
        try:
            from .editorial_director import analyze_sentence_triggers, extract_documentary_highlight
            _triggers = analyze_sentence_triggers(text, is_first_scene=(index == 0), context_text=sentence_text)
            _pv_props = dict(graphic.get("props") or {})
            grounded_numbers = [
                number for number in _triggers.numbers
                if not any(re.search(rf"\b{re.escape(str(number).strip())}\b", str(date_value), re.I) for date_value in _triggers.dates)
            ]
            if grounded_numbers and not _pv_props.get("stat") and not _pv_props.get("number"):
                _pv_props["stat"] = grounded_numbers[0]
            if _triggers.quotes and not _pv_props.get("quote"):
                _pv_props["quote"] = _triggers.quotes[0]
            if _triggers.dates and not _pv_props.get("events"):
                if len(_triggers.dates) == 1:
                    _pv_props["events"] = [{"date": _triggers.dates[0], "label": extract_documentary_highlight(text, max_words=3)}]
                elif len(_triggers.dates) == 2:
                    _pv_props["events"] = [
                        {"date": _triggers.dates[0], "label": "START"},
                        {"date": _triggers.dates[1], "label": "CONCLUDED"},
                    ]
                else:
                    _pv_props["events"] = [{"date": d, "label": f"PHASE {i + 1}"} for i, d in enumerate(_triggers.dates[:4])]
            if _triggers.persons and not _pv_props.get("name"):
                _pv_props["name"] = _triggers.persons[0]
            if _triggers.locations and len(_triggers.locations) >= 2:
                _pv_props.setdefault("start", _triggers.locations[0])
                _pv_props.setdefault("end", _triggers.locations[1])
                _pv_props.setdefault("from", _triggers.locations[0])
                _pv_props.setdefault("to", _triggers.locations[1])
            elif _triggers.country_corridor:
                _pv_props.setdefault("start", _triggers.country_corridor.get("from"))
                _pv_props.setdefault("end", _triggers.country_corridor.get("to"))
                _pv_props.setdefault("from", _triggers.country_corridor.get("from"))
                _pv_props.setdefault("to", _triggers.country_corridor.get("to"))
            elif _triggers.locations:
                _pv_props.setdefault("location", _triggers.locations[0])
            elif _triggers.countries:
                _pv_props.setdefault("country", _triggers.countries[0])
                _pv_props.setdefault("location", _triggers.countries[0])
            
            llm_text = str(beat.get("emphasis") or "").strip()
            if llm_text:
                _short_highlight = llm_text
                _pv_props["title"] = llm_text
                _pv_props["headline"] = llm_text
                _pv_props["label"] = llm_text
                _pv_props["text"] = llm_text
            else:
                _short_highlight = extract_documentary_highlight(text, max_words=4)
                _pv_props.setdefault("title", _short_highlight)
                _pv_props.setdefault("headline", _short_highlight)
                if not _pv_props.get("label"):
                    _pv_props["label"] = extract_documentary_highlight(text, max_words=3)
                if not _pv_props.get("text"):
                    _pv_props["text"] = str(_pv_props.get("title") or _pv_props.get("headline") or _pv_props.get("location") or _short_highlight or text).strip()
            if not _pv_props.get("highlight_words"):
                _hw_candidates = [w for w in (_triggers.numbers + _triggers.dates + _triggers.persons + _triggers.locations) if w]
                _pv_props["highlight_words"] = list(dict.fromkeys(_hw_candidates))

            universal_map = {
                "text-card": ("TextCard", str(_pv_props.get("style") or "vox-highlighter")),
                "vox-highlighter": ("TextCard", "vox-highlighter"),
                "cyber-hud": ("TextCard", "cyber-hud"),
                "investigative-memo": ("TextCard", "investigative-memo"),
                "broadsheet-callout": ("TextCard", "broadsheet-callout"),
                "frosted-glass": ("TextCard", "frosted-glass"),
                "brutalist-punch": ("TextCard", "brutalist-punch"),
                "field-caption": ("TextCard", "field-caption"),
                "card-overlay": ("TextCard", "vox-highlighter"),
                "stat-counter": ("StatCard", "hero_counter_large"),
                "money-counter": ("StatCard", "hero_counter_large"),
                "metric-grid": ("StatCard", "kpi_metric_card"),
                "countdown-clock": ("StatCard", "hero_counter_large"),
                "data-race": ("StatCard", "delta_growth_badge"),
                "bar-chart": ("Chart", "editorial_bar"),
                "line-chart": ("Chart", "trend_line_with_markers"),
                "area-chart": ("Chart", "area_growth_fill"),
                "donut-chart": ("Chart", "donut_distribution"),
                "ranked-bars": ("Chart", "ranked_horizontal_bars"),
                "slope-chart": ("Chart", "before_after_delta"),
                "pictogram-stat": ("Chart", "ranked_horizontal_bars"),
                "map-route": ("AnimatedMap", "city_to_city_transit"),
                "map-card": ("AnimatedMap", "country_spotlight"),
                "route-progress": ("AnimatedMap", "city_to_city_transit"),
                "profile-dossier": ("PersonProfile", "minimal_profile_split"),
                "profile-card": ("PersonProfile", "cinematic_lower_third_card"),
                "presenter-card": ("PersonProfile", "cinematic_lower_third_card"),
                "lower-third": ("PersonProfile", "cinematic_lower_third_card"),
                "case-file": ("PersonProfile", "minimal_profile_split"),
                "exhibit-label": ("PersonProfile", "minimal_profile_split"),
                "quote-card": ("QuoteCard", "editorial_large_quote"),
                "witness-quote": ("QuoteCard", "quote_fragment_punch"),
                "timeline-trace": ("Timeline", "horizontal_milestone_trail"),
                "timeline-strip": ("Timeline", "horizontal_milestone_trail"),
                "date-card": ("Timeline", "date_flip_calendar"),
                "calendar-rip": ("Timeline", "date_flip_calendar"),
                "newsprint-reveal": ("Headline", "newspaper_front_page"),
                "paper-explainer": ("DocumentHighlight", "yellow_marker_highlight"),
                "source-highlight": ("DocumentHighlight", "yellow_marker_highlight"),
                "redaction-reveal": ("DocumentHighlight", "declassified_redaction"),
                "evidence-board": ("Collage", "evidence_pinboard"),
                "gallery-stack": ("Collage", "three_panel_asymmetric"),
                "photo-pinboard": ("Collage", "evidence_pinboard"),
                "split-screen": ("Comparison", "side_by_side_cards"),
                "comparison": ("Comparison", "side_by_side_cards"),
                "callout-arrow": ("DocumentHighlight", "yellow_marker_highlight"),
                "spotlight-lens": ("DocumentHighlight", "yellow_marker_highlight"),
                "archival-frame": ("DocumentHighlight", "report_page_pan"),
                "index-card": ("TextCard", "investigative-memo"),
                "stamp-mark": ("TextCard", "brutalist-punch"),
                "date-numeral": ("Timeline", "date_flip_calendar"),
                "percentage-gauge": ("StatCard", "kpi_metric_card"),
                "listicle-build": ("Diagram", "cause_and_effect_flow"),
                "step-progress": ("Diagram", "cause_and_effect_flow"),
                "chart": ("Chart", "editorial_bar"),
                "statcard": ("StatCard", "hero_counter_large"),
                "cause-effect": ("Diagram", "cause_and_effect_flow"),
                "decision-tree": ("Diagram", "hierarchical_system_tree"),
                "blueprint-reveal": ("Diagram", "network_node_connect"),
                "signal-scan": ("Diagram", "network_node_connect"),
                "terminal-log": ("Diagram", "step_by_step_process"),
                "chapter-opener": ("ChapterTitle", "bold_editorial_kicker"),
                "cold-open": ("ChapterTitle", "bold_editorial_kicker"),
                "end-card": ("ChapterTitle", "archival_slate_divider"),
                "keyword-reveal": ("KineticText", "bold_minimal_center"),
                "kinetic-statement": ("KineticText", "staggered_lines"),
                "word-cascade": ("KineticText", "word_by_word_punch"),
                "headline-center": ("KineticText", "bold_minimal_center"),
                "cyber-hud": ("TextCard", "cyber-hud"),
                "investigative-memo": ("TextCard", "investigative-memo"),
                "broadsheet-callout": ("TextCard", "broadsheet-callout"),
                "frosted-glass": ("TextCard", "frosted-glass"),
                "brutalist-punch": ("TextCard", "brutalist-punch"),
                "vox-highlighter": ("TextCard", "vox-highlighter"),
            }

            prev_pv_type = (scenes[-1].get("primary_visual") or {}).get("type") if scenes else None
            if graphics_enabled and template_id != "full-bleed":
                if (
                    _triggers.recommended_scene_type in {"Chart", "AnimatedMap", "QuoteCard", "PersonProfile"}
                    and _triggers.recommended_scene_type != prev_pv_type
                ):
                    pv_type = _triggers.recommended_scene_type
                    pv_variant = _triggers.recommended_variant
                else:
                    mapped = universal_map.get(template_id)
                    if mapped and mapped[0] != prev_pv_type:
                        pv_type, pv_variant = mapped
                    else:
                        pv_type = _triggers.recommended_scene_type
                        pv_variant = _triggers.recommended_variant
            elif not graphics_enabled:
                pv_type = "SmartVideo" if (str(media_type).strip().lower() in {"video", "video/mp4", "video/webm"} or not is_still_media) else "SmartImage"
                pv_variant = preferred_treatment or ("full_bleed" if not is_still_media else "full_bleed_cinematic")
            else:
                pv_type = _triggers.recommended_scene_type
                pv_variant = _triggers.recommended_variant

            requested_variant = str(pv_variant or "")
            pv_variant, variant_repaired = _supported_remotion_variant(
                pv_type,
                requested_variant,
                str(_triggers.recommended_variant or ""),
            )
            if variant_repaired:
                scene["variant_repair"] = {
                    "reason": "unsupported_remotion_variant",
                    "requested": requested_variant,
                    "selected": pv_variant,
                }

            if pv_type in {"TextCard", "text-card"}:
                niche_req = str(config.get("selected_template") or "").lower().replace("_", "-")
                if niche_req in {"cyber-hud", "investigative-memo", "broadsheet-callout", "frosted-glass", "brutalist-punch", "vox-highlighter"}:
                    pv_variant = niche_req
                _pv_props.setdefault("style", pv_variant or "vox-highlighter")
                if not _pv_props.get("kicker"):
                    _pv_props["kicker"] = str(_pv_props.get("label") or _pv_props.get("subtitle") or "KEY DETAIL").upper()
                if llm_text:
                    _pv_props["text"] = llm_text
                    _pv_props["title"] = llm_text
            elif pv_type in {"StatCard", "stat-card"}:
                from .editorial_director.director import _extract_ratio_metric_label

                stat_val = str(_pv_props.get("value") or _pv_props.get("number") or _pv_props.get("stat") or "")
                _pv_props.setdefault("stat", stat_val)
                _pv_props.setdefault("number", stat_val)
                # This is the final manifest boundary.  Do not promote a
                # scene fragment (for example, "jewelry and other") into a
                # stat label: it reads like broken grammar in the rendered
                # frame.  Use a complete factual descriptor instead.
                _metric_context = str(text or sentence_text or "").lower()
                _ratio_label = _extract_ratio_metric_label(_metric_context, stat_val)
                if _ratio_label:
                    _metric_label = _ratio_label
                elif any(token in _metric_context for token in ("loss", "lost", "stolen")):
                    _metric_label = "ESTIMATED LOSS"
                elif any(token in _metric_context for token in ("taken", "valued", "value", "worth")):
                    _metric_label = "ESTIMATED VALUE"
                elif "security layer" in _metric_context:
                    _metric_label = "SECURITY LAYERS"
                elif any(token in _metric_context for token in ("deposit box", "boxes opened", "boxes untouched")):
                    _metric_label = "DEPOSIT BOXES"
                else:
                    _metric_label = "KEY METRIC"
                _pv_props["label"] = _metric_label
            elif pv_type in {"Chart", "chart"}:
                if not _pv_props.get("data") and not _pv_props.get("values"):
                    from .editorial_director.director import _extract_numbers_from_text, _extract_chart_title
                    _chart_data = _extract_numbers_from_text(sentence_text if len(_triggers.numbers) >= 2 else text)
                    if len(_chart_data) >= 2:
                        _pv_props["data"] = [
                            {"label": ex.get("full_match", f"METRIC {i+1}")[:25].upper(), "value": ex["raw"]}
                            for i, ex in enumerate(_chart_data[:5])
                        ]
                        _pv_props["values"] = _pv_props["data"]
                        _pv_props["title"] = _extract_chart_title(sentence_text or text)
                        _pv_props["emphasis"] = _chart_data[0]["display"] if _chart_data else ""
                _pv_props.setdefault("title", _pv_props.get("title") or _pv_props.get("label") or "DOCUMENTARY DATA")
                if "items" in _pv_props and "values" not in _pv_props:
                    _pv_props["values"] = _pv_props["items"]
            elif pv_type == "Timeline" and pv_variant == "date_flip_calendar":
                timeline_events = _pv_props.get("events")
                if not _pv_props.get("date") and not _pv_props.get("year") and isinstance(timeline_events, list):
                    first_event = timeline_events[0] if timeline_events else {}
                    if isinstance(first_event, Mapping):
                        event_date = first_event.get("date") or first_event.get("year") or first_event.get("label")
                        if event_date:
                            _pv_props["year"] = str(event_date)
            elif pv_type == "QuoteCard":
                if _triggers.quotes and not _pv_props.get("quote"):
                    _pv_props["quote"] = _triggers.quotes[0]
                speaker = _pv_props.get("speaker") or _pv_props.get("source") or (_triggers.persons[0] if _triggers.persons else "")
                if not speaker:
                    m = re.search(r'([A-Z][A-Za-z0-9\s]+?)\s+(?:later\s+)?(?:declared|stated|noted|announced|said|wrote|claimed|proclaimed|testified)', f"{sentence_text} {text}")
                    if m:
                        speaker = m.group(1).strip()
                _pv_props["speaker"] = (speaker or "DIRECT STATEMENT").upper()
                _pv_props["source"] = (speaker or "DIRECT STATEMENT").upper()
                _pv_props["role"] = "Documented Statement"

            # The semantic fallback must obey the same grounding contract as
            # the Director. If a factual visual has no source-backed payload,
            # downgrade it to a restrained text beat instead of allowing a
            # Remotion/native component to invent a chart, map, or timeline.
            factual_types = {"StatCard", "Chart", "AnimatedMap", "Timeline", "Comparison", "Diagram"}
            has_chart_payload = bool(
                (isinstance(_pv_props.get("values"), list) and len(_pv_props.get("values") or []) >= 2)
                or (isinstance(_pv_props.get("data"), list) and len(_pv_props.get("data") or []) >= 2)
                or (isinstance(_pv_props.get("points"), list) and len(_pv_props.get("points") or []) >= 2)
                or _pv_props.get("currentValue") is not None
                or (_pv_props.get("before") and _pv_props.get("after"))
            )
            grounded_factual_visual = (
                not _pv_props.get("no_data")
                and (
                    (pv_type == "StatCard" and bool(_pv_props.get("stat") or _pv_props.get("number") or _pv_props.get("value")))
                    or (pv_type == "Chart" and has_chart_payload)
                    or (pv_type == "AnimatedMap" and bool(
                        _pv_props.get("country")
                        or _pv_props.get("location")
                        or (_pv_props.get("from") and _pv_props.get("to"))
                        or (_pv_props.get("start") and _pv_props.get("end"))
                    ))
                    or (pv_type == "Timeline" and bool(_pv_props.get("events") or _pv_props.get("date") or _pv_props.get("year") or _pv_props.get("eraSpan")))
                    or (pv_type == "Comparison" and bool(
                        (_pv_props.get("leftValue") and _pv_props.get("rightValue"))
                        or (_pv_props.get("left") and _pv_props.get("right"))
                    ))
                    or (pv_type == "Diagram" and (
                        (isinstance(_pv_props.get("steps"), list) and len(_pv_props.get("steps") or []) >= 2)
                        or (
                            str(pv_variant).lower().replace("_", "-") in {"terminal-log", "code-console", "signal-console", "signal-scan", "telemetry-scan"}
                            and bool(_pv_props.get("message") or _pv_props.get("text") or _pv_props.get("title"))
                        )
                    ))
                )
            )
            if pv_type in factual_types and not grounded_factual_visual:
                if beat.get("selected_for_text") is False or str(beat.get("source") or "").startswith("openrouter"):
                    pv_type = "SmartVideo" if (str(media_type).strip().lower() in {"video", "video/mp4", "video/webm"} or not is_still_media) else "SmartImage"
                    pv_variant = preferred_treatment or ("full_bleed" if not is_still_media else "full_bleed_cinematic")
                    _pv_props = dict(graphic.get("props") or {})
                else:
                    pv_type = "KineticText"
                    pv_variant = "staggered_lines"
                    _pv_props = {
                        "text": _short_highlight or extract_documentary_highlight(text, max_words=7) or "",
                        "title": _short_highlight or "",
                        "grounding": {"status": "grounded", "source": "script", "scene_text": text},
                    }

            scene["primary_visual"] = {
                "type": pv_type,
                "variant": pv_variant,
                "props": _pv_props,
                "asset": scene.get("media"),
            }
        except Exception:
            scene["primary_visual"] = {
                "type": "SmartImage",
                "variant": "full_bleed_cinematic",
                "props": dict(graphic.get("props") or {}),
                "asset": scene.get("media"),
            }

        scene["word_cues"] = _timed_word_cues(
            scene_contract if scene_contract is not None else {
                "start_seconds": offset,
                "word_start_index": scene.get("source_span", {}).get("word_start_index"),
                "word_end_index": scene.get("source_span", {}).get("word_end_index"),
            },
            v2_words,
            duration,
            text,
        )
        scene["sound_cues"] = plan_sound_cues(scene, index, sound_library, recent=recent_sfx)
        if editorial_cue is not None:
            cue_start = float(_contract_value(editorial_cue, "absolute_start_seconds", offset) or offset)
            for sound_cue in scene["sound_cues"]:
                sound_intent = str(sound_cue.get("sound_intent") or "")
                reason = str(sound_cue.get("reason") or "")
                if (
                    sound_intent in {"template_signature", "text_entry"}
                    or reason.startswith("editorial:text_card_entrance")
                    or reason.startswith("vox:")
                    or reason.startswith("text_stagger:")
                ):
                    relative_delay = max(0.0, float(sound_cue.get("start") or offset) - offset)
                    sound_cue["start"] = round(cue_start + relative_delay, 3)
                    sound_cue["time_local"] = round(cue_start + relative_delay - offset, 3)
                    sound_cue["scene_local_start"] = sound_cue["time_local"]

        # Track existing sound cues for balance
        for sc in scene["sound_cues"]:
            k = str(sc.get("kind") or "")
            if k:
                sfx_usage_counts[k] = sfx_usage_counts.get(k, 0) + 1

        is_mock_test_library = bool(
            sound_library is not None
            and len(sound_library) < 15
            and not config.get("enable_speech_whooshes")
        )
        if (
            not is_mock_test_library
            and not str(beat.get("source") or "").startswith("openrouter")
        ):
            def _pick_least_used(candidates: Sequence[str]) -> str:
                if not candidates:
                    return ""
                sorted_cands = sorted(
                    candidates,
                    key=lambda k: (sfx_usage_counts.get(k, 0), stable_seed(text, index, k)),
                )
                chosen = sorted_cands[0]
                sfx_usage_counts[chosen] = sfx_usage_counts.get(chosen, 0) + 1
                return chosen

            def _build_cue(
                kind: str,
                start_sec: float,
                duration_sec: float,
                volume_lvl: float,
                reason_str: str,
                intent_str: str,
                fade_in_sec: float = 0.03,
                fade_out_sec: float = 0.12,
                duck_sec: float = 0.04,
            ) -> Optional[Dict[str, Any]]:
                if not kind:
                    return None
                src, rights, meta = _resolve_sound_item(kind, sound_library)
                return {
                    "kind": kind,
                    "src": src,
                    "start": round(max(0.0, start_sec), 3),
                    "duration": round(max(0.1, duration_sec), 3),
                    "volume": round(max(0.01, min(0.35, volume_lvl)), 3),
                    "fade_in": round(fade_in_sec, 3),
                    "fade_out": round(fade_out_sec, 3),
                    "duck_voice": round(duck_sec, 3),
                    "reason": reason_str,
                    "sound_intent": intent_str,
                    "rights_status": rights,
                    **meta,
                }

            # 1. Whoosh sound effect after each full stop (. ! ?)
            full_stop_candidates = [
                "whoosh_soft", "whoosh_air", "edit_whoosh_light", "edit_whoosh_air",
                "whoosh_snap", "whoosh_dark", "edit_whoosh_deep", "edit_whoosh_sucked",
            ]
            if sound_library:
                avail_ws = [k for k in full_stop_candidates if k in sound_library]
                if avail_ws:
                    full_stop_candidates = avail_ws

            scene_end = offset + duration
            found_stops: List[float] = []
            if v2_words:
                for w in v2_words:
                    w_start = float(_contract_value(w, "start_seconds", _contract_value(w, "start", 0.0)) or 0.0)
                    w_end = float(_contract_value(w, "end_seconds", _contract_value(w, "end", w_start)) or w_start)
                    w_text = str(_contract_value(w, "text", _contract_value(w, "word", "")) or "").strip()
                    if not w_text or w_end < offset - 0.05 or w_start > scene_end + 0.05:
                        continue
                    clean_w = w_text.rstrip("'\")}]")
                    if clean_w.endswith((".", "!", "?")):
                        low = clean_w.lower()
                        if low not in {
                            "mr.", "mrs.", "ms.", "dr.", "vs.", "etc.", "approx.",
                            "u.s.", "u.s.a.", "no.", "e.g.", "i.e.", "a.m.", "p.m.",
                        }:
                            found_stops.append(w_end)
            if not found_stops and text.strip().endswith((".", "!", "?")):
                found_stops.append(offset + max(0.2, duration - 0.22))

            for stop_ts in found_stops:
                whoosh_start = round(stop_ts + 0.05, 3)
                if (
                    whoosh_start - last_full_stop_whoosh_time >= 6.0
                    and whoosh_start <= scene_end + 0.35
                    and (float(beat.get("importance") or 0.0) >= 0.90 or str(beat.get("sound_intent") or "") in {"reveal", "impact"})
                ):
                    chosen_whoosh = _pick_least_used(full_stop_candidates)
                    cue = _build_cue(
                        chosen_whoosh,
                        start_sec=whoosh_start,
                        duration_sec=0.42,
                        volume_lvl=0.16,
                        reason_str="punctuation:full_stop_whoosh",
                        intent_str="punctuation",
                        fade_in_sec=0.02,
                        fade_out_sec=0.12,
                        duck_sec=0.04,
                    )
                    if cue:
                        scene["sound_cues"].append(cue)
                        last_full_stop_whoosh_time = whoosh_start

            # 2. Transition sound effects: exactly 1 in 3 transitions (index % 3 == 0 and index > 0), non-cuts only
            trans_type = str((scene.get("transition") or {}).get("template_id") or "cut").lower()
            if index > 0 and index % 3 == 0 and trans_type not in {"cut", "none", ""}:
                trans_candidates = [
                    "edit_whoosh_light", "whoosh_soft", "edit_whoosh_sucked", "whoosh_air",
                    "edit_whoosh_air", "whoosh_snap", "edit_room_shift", "transition_suck",
                    "film_splice", "edit_slider_move", "edit_whoosh_deep", "whoosh_dark",
                ]
                if sound_library:
                    avail_trans = [k for k in trans_candidates if k in sound_library]
                    if avail_trans:
                        trans_candidates = avail_trans
                if not any(
                    abs(float(c.get("start", 0)) - offset) < 0.25 and "whoosh" in str(c.get("kind", ""))
                    for c in scene["sound_cues"]
                ):
                    chosen_trans = _pick_least_used(trans_candidates)
                    cue = _build_cue(
                        chosen_trans,
                        start_sec=offset,
                        duration_sec=0.48,
                        volume_lvl=0.18,
                        reason_str=f"scene_rhythm:transition_{chosen_trans}_{index}",
                        intent_str="transition",
                        fade_in_sec=0.02,
                        fade_out_sec=0.14,
                        duck_sec=0.05,
                    )
                    if cue:
                        scene["sound_cues"].append(cue)

                # Companion accents reserved strictly for major chapter turns
                if index > 0 and index % 3 == 0 and str(beat.get("role") or "") in {"chapter", "climax"}:
                    companion_candidates = [
                        "reveal_chime", "impact_soft", "signal_ping",
                        "edit_reveal_soft", "edit_hit_tight",
                    ]
                    if sound_library:
                        avail_comp = [k for k in companion_candidates if k in sound_library]
                        if avail_comp:
                            companion_candidates = avail_comp
                    chosen_comp = _pick_least_used(companion_candidates)
                    cue = _build_cue(
                        chosen_comp,
                        start_sec=offset + 0.10,
                        duration_sec=0.38,
                        volume_lvl=0.14,
                        reason_str=f"scene_rhythm:companion_accent_{index}",
                        intent_str="accent",
                        fade_in_sec=0.02,
                        fade_out_sec=0.12,
                        duck_sec=0.04,
                    )
                    if cue:
                        scene["sound_cues"].append(cue)

            # 3. Background sound effects sitting behind the scenes
            bg_candidates = [
                "room_tone", "low_rumble", "suspense_pulse",
                "archive_drone", "wind_room", "vinyl_crackle",
            ]
            if sound_library:
                avail_bg = [k for k in bg_candidates if k in sound_library]
                if avail_bg:
                    bg_candidates = avail_bg
            b_energy = str(beat.get("energy") or "normal").lower()
            b_role = str(beat.get("role") or "").lower()
            if b_energy in {"tense", "urgent"}:
                preferred_bg = [k for k in ["suspense_pulse", "low_rumble"] if k in bg_candidates]
            elif b_energy in {"high", "energetic"}:
                preferred_bg = [k for k in ["low_rumble", "suspense_pulse"] if k in bg_candidates]
            elif b_role in {"setup", "evidence", "context"}:
                preferred_bg = [k for k in ["archive_drone", "room_tone", "vinyl_crackle"] if k in bg_candidates]
            else:
                preferred_bg = [k for k in ["room_tone", "wind_room"] if k in bg_candidates]

            # Background audio textures: keep occasional, only on high-tension climax beats
            if b_energy in {"tense", "urgent"} and (b_role in {"climax", "chapter"} or float(beat.get("importance") or 0.0) >= 0.90):
                chosen_bg = _pick_least_used(preferred_bg or bg_candidates)
                cue = _build_cue(
                    chosen_bg,
                    start_sec=offset,
                    duration_sec=min(duration, 5.0),
                    volume_lvl=0.25,
                    reason_str=f"background_texture:{chosen_bg}",
                    intent_str="background_bed",
                    fade_in_sec=0.25,
                    fade_out_sec=0.25,
                    duck_sec=0.02,
                )
                if cue:
                    scene["sound_cues"].append(cue)

        scenes.append(scene)
        recent_templates.append(template_id)
        template_usage[template_id] = int(template_usage.get(template_id, 0)) + 1
        recent_structures.append(visual_structure)
        structure_usage[visual_structure] = int(structure_usage.get(visual_structure, 0)) + 1
        recent_motion.append(motion)
        if is_still_media:
            recent_still_motion.append(motion)
        recent_transitions.append(transition_id)
        recent_treatments.append(preferred_treatment)
        entry_motion = str((graphic.get("props") or {}).get("_entry_motion") or "").strip().lower()
        if entry_motion:
            recent_entry_motions.append(entry_motion)
            recent_entry_motions = recent_entry_motions[-8:]
        # Track the primary sound motif to prevent adjacent primary sound loops
        if scene["sound_cues"]:
            prim_k = str(scene["sound_cues"][0].get("kind") or "")
            if prim_k:
                recent_sfx.append(prim_k)
        recent_sfx = [key for key in recent_sfx if key][-8:]
        if transition_id != "cut":
            transitions.append({"scene_index": index, "start": round(offset, 3), "duration": scene["transition"]["duration"], "template_id": transition_id})
        offset += duration

    try:
        from .editorial_director import enforce_scene_dict_grammar
        scenes = enforce_scene_dict_grammar(scenes)
    except Exception:
        pass

    # Sound timing is finalized only after all editorial, punctuation, and
    # transition cues have been added. This keeps every cue inside its scene
    # and removes close repeats that make the mix feel machine generated.
    sound_cue_audit = _sanitize_sound_cue_timeline(scenes, sound_library)

    design_fingerprint = hashlib.sha256(
        "|".join(
            f"{scene['graphic']['template_id']}:{scene['graphic']['variant']}:{scene['transition']['template_id']}:{scene['design']['image_treatment']}:{','.join(scene['atmosphere']['layers'])}"
            for scene in scenes
        ).encode("utf-8")
    ).hexdigest()[:20]
    try:
        from .editorial_director import generate_visual_dna
        dna_instance = generate_visual_dna(
            topic=topic,
            category=category,
            script=" ".join(texts),
            channel_brand=config.get("channel_brand"),
            seed=variation_seed,
        )
        computed_visual_dna = dna_instance.to_dict()
    except Exception:
        computed_visual_dna = {}

    # Keep one display and one body family across the whole documentary. The
    # director may still vary size, weight, tracking, and placement, but a
    # third numbers family makes the sequence audit reject an otherwise valid
    # edit and introduces an avoidable visual mismatch.
    dna_for_manifest = config.get("visual_dna") or computed_visual_dna
    if isinstance(dna_for_manifest, Mapping):
        dna_for_manifest = deepcopy(dict(dna_for_manifest))
        typography = dna_for_manifest.get("typography")
        if isinstance(typography, Mapping):
            typography = dict(typography)
            typography["numbers"] = typography.get("body") or typography.get("display") or "Inter"
            dna_for_manifest["typography"] = typography

    manifest = {
        "version": MANIFEST_VERSION, "kind": "vidzeo-semantic-edit", "fps": fps,
        "width": width, "height": height, "duration": round(offset, 3),
        "duration_in_frames": max(1, round(offset * fps)), "topic": _norm(topic),
        "category": _norm(category), "render_quality": _norm(config.get("render_quality") or "standard"),
        "aspect_ratio": _norm(config.get("aspect_ratio") or "16:9"), "theme": theme,
        "visual_dna": dna_for_manifest,
        "visual_system": {
            "version": "advanced-doc-v1",
            "selection": "semantic_grammar_director",
            "grammars": ["cinematic_type", "evidence_desk", "data_story", "spatial_atlas", "chronology", "signal_console"],
            "manager": "editorial_director.manager",
            "critic": "editorial_director.visual_critic",
        },
        "editing_profile": editing_profile,
        "style_profile": {
            "id": theme["id"], "name": theme["label"], "pace": editing_profile["pace"],
            "graphic_density": theme["graphic_density"], "sound_density": theme["sound_density"],
            "transition_policy": list(theme["transition_policy"]),
            "editorial_archetype": editing_profile["editorial_archetype"],
        },
        "reusable_template_system": {
            "version": 3,
            "selection": "documentary_catalog_v3",
            "shuffle_seed": variation_seed,
            "template_ids": [str(item.get("id")) for item in TEMPLATE_REGISTRY if item.get("id")],
            "position_contract": "subject_and_caption_safe_regions",
            "font_contract": "verified_local_art_direction",
            "catalog": catalog_manifest(),
        },
        "captions": {
            "enabled": bool(config.get("captions_enabled", False)),
            "style": _norm(config.get("caption_style") or "documentary"),
            "font": _norm(config.get("caption_font") or "DM Sans"),
            "position": _norm(config.get("position") or "bottom"),
            "mode": _norm(config.get("caption_mode") or "word"),
            "animation": _norm(config.get("animation") or "Pop"),
        },
        "scenes": scenes,
        "editorial_cues": [
            scene["graphic"]["cue"]
            for scene in scenes
            if isinstance(scene.get("graphic", {}).get("cue"), Mapping)
        ],
        "editorial_cues_shadow": (
            [
                (cue.to_dict() if hasattr(cue, "to_dict") else dict(cue))
                for cue in editorial_cues_v2
            ]
            if cue_v2_shadow
            else []
        ),
        "audio": {"voiceover": dict(config.get("voiceover") or {}), "music": (dict(config.get("music")) if (isinstance(config.get("music"), dict) and config.get("music")) else ({"path": str(config.get("music_path") or ""), "src": str(config.get("music_path") or ""), "volume": (float(config.get("music_volume", 20)) / 100.0 if float(config.get("music_volume", 20)) > 1.0 else float(config.get("music_volume", 0.2)))} if config.get("music_path") else {})), "effects": [cue for scene in scenes for cue in scene["sound_cues"]]},
        "transitions": transitions,
        "metrics": {
            "scene_count": len(scenes),
            "graphic_scene_count": sum(scene["graphic"]["template_id"] != "full-bleed" for scene in scenes),
            "graphic_scene_indices": sorted(
                index for index, scene in enumerate(scenes)
                if scene["graphic"]["template_id"] != "full-bleed"
            ),
            "graphic_scene_ratio": round(
                sum(scene["graphic"]["template_id"] != "full-bleed" for scene in scenes) / max(1, len(scenes)),
                4,
            ),
            "atmosphere_scene_count": sum(bool(scene["atmosphere"]["layers"]) for scene in scenes),
            "template_counts": dict(__import__("collections").Counter(scene["graphic"]["template_id"] for scene in scenes)),
            "visual_structure_counts": dict(__import__("collections").Counter(scene["design"]["visual_structure"] for scene in scenes)),
            "quantitative_chart_count": sum(scene["design"]["visual_structure"] == "quantitative_chart" for scene in scenes),
            "concept_diagram_count": sum(scene["design"]["visual_structure"] == "concept_diagram" for scene in scenes),
            "motion_level_counts": dict(__import__("collections").Counter(scene["motion_level"] for scene in scenes)),
            "motion_family_counts": dict(__import__("collections").Counter(
                str((scene.get("camera") or {}).get("motion_family") or _motion_family((scene.get("camera") or {}).get("motion")))
                for scene in scenes
            )),
            "transition_family_counts": dict(__import__("collections").Counter(
                _transition_family((scene.get("transition") or {}).get("template_id"))
                for scene in scenes
            )),
            "overlay_layer_counts": dict(__import__("collections").Counter(layer for scene in scenes for layer in scene["atmosphere"]["layers"])),
            "image_treatment_counts": dict(__import__("collections").Counter(scene["design"]["image_treatment"] for scene in scenes)),
            "sound_cue_count": sum(len(scene["sound_cues"]) for scene in scenes),
            "sound_cue_audit": sound_cue_audit,
            "editorial_cue_v2_enabled": cue_v2_enabled,
            "editorial_cue_v2_shadow": cue_v2_shadow,
            "editorial_cue_v2_count": sum(
                isinstance(scene.get("graphic", {}).get("cue"), Mapping) for scene in scenes
            ),
            "variation_seed": variation_seed, "design_fingerprint": design_fingerprint,
            "planner": "semantic_edit_engine",
        },
    }
    # Media-first composition is part of the semantic manifest contract. The
    # Remotion compositor uses this plan to keep clips/photos dominant while
    # placing restrained evidence graphics in vision-approved safe regions.
    try:
        from .editorial_director import (
            annotate_media_compositions,
            audit_media_compositions,
            run_documentary_swarm,
            apply_media_first_direction,
            plan_media_composition,
            audit_sequence_continuity,
            execute_reviewer_council,
        )
        manifest = annotate_media_compositions(manifest)
        # The swarm is the media-to-graphics handoff: source analysis, media
        # placement, semantic grammar, motion diversity, then the critics.
        manifest = run_documentary_swarm(manifest)
        manifest = apply_media_first_direction(manifest, plan_media_composition)
        manifest["version"] = 4
        manifest["sequence_audit"] = audit_sequence_continuity(manifest)
        manifest["reviewer_council"] = execute_reviewer_council(manifest)
        manifest["metrics"]["media_first_audit"] = audit_media_compositions(manifest)
        manifest["metrics"]["documentary_swarm_audit"] = manifest.get("swarm_audit", {})
    except Exception as exc:
        manifest["metrics"]["media_first_audit"] = {
            "status": "unavailable",
            "ready_for_long_render": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    manifest["metrics"]["variation_audit"] = audit_edit_variation(
        scenes,
        design_fingerprint=design_fingerprint,
        history_fingerprints=list(config.get("channel_edit_fingerprints") or []),
        grounded_cue_mode=bool(cue_v2_enabled),
    )
    from .research_ledger import build_research_ledger, configured_research_client, reconcile_research_source_marks
    manifest["research_ledger"] = build_research_ledger(
        texts,
        sources=config.get("research_sources") or [],
        claim_reviews=config.get("research_claim_reviews") or [],
        fetch_sources=bool(config.get("research_fetch_sources", False)),
        budget_seconds=config.get("research_budget_seconds", 20.0),
        llm_client=configured_research_client(config) if bool(config.get("research_llm_review", False)) and (config.get("research_sources") or config.get("research_discover_sources")) else None,
        discover_sources=bool(config.get("research_discover_sources", False)),
        topic=topic,
    )
    manifest["metrics"]["research_status"] = manifest["research_ledger"]["status"]
    manifest["metrics"]["unresolved_research_claim_count"] = len(manifest["research_ledger"]["unresolved_claim_ids"])
    reconcile_research_source_marks(manifest)
    try:
        from .editorial_director import manager_review
        manifest["quality_control"] = manager_review(manifest)
        manifest["metrics"]["manager_ready_for_long_render"] = bool(
            manifest["quality_control"].get("ready_for_long_render")
        )
    except Exception as exc:
        # The render contract remains usable if the optional quality manager
        # cannot import, but the manifest records that it was not run.
        manifest["quality_control"] = {
            "status": "unavailable",
            "ready_for_long_render": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        manifest["metrics"]["manager_ready_for_long_render"] = False

    # Persist cross-video template history for variety across runs
    try:
        _cross_video_template_file.parent.mkdir(parents=True, exist_ok=True)
        _combined = list(cross_video_recent) + list(recent_vox_templates)
        # Keep only the last 20 unique templates
        _seen = set()
        _deduped = []
        for _t in _combined:
            if _t not in _seen:
                _seen.add(_t)
                _deduped.append(_t)
        _cross_video_template_file.write_text(
            json.dumps(_deduped[-20:], ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass

    return validate_semantic_manifest(manifest)


def validate_semantic_manifest(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise SemanticManifestError("Manifest must be an object")
    result = deepcopy(dict(manifest))
    if int(result.get("version", 0)) not in (MANIFEST_VERSION, 2, 3, 4):
        raise SemanticManifestError("Unsupported semantic manifest version")
    fps = float(result.get("fps", 0))
    width = int(result.get("width", 0))
    height = int(result.get("height", 0))
    if not 1 <= fps <= 120 or width < 320 or height < 180:
        raise SemanticManifestError("Invalid render dimensions or frame rate")
    scenes = result.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise SemanticManifestError("Manifest requires scenes")
    previous_end = 0.0
    for expected_index, scene in enumerate(scenes):
        if not isinstance(scene, Mapping):
            raise SemanticManifestError(f"Scene {expected_index} is malformed")
        narration = _norm(scene.get("narration"))
        if not narration:
            raise SemanticManifestError(f"Scene {expected_index} is missing narration")
        start = float(scene.get("start", -1))
        duration = float(scene.get("duration", 0))
        if start < 0 or duration <= 0 or start + duration < previous_end - 0.02:
            raise SemanticManifestError(f"Scene {expected_index} has invalid timing")
        if int(scene.get("scene_index", expected_index)) != expected_index:
            raise SemanticManifestError(f"Scene {expected_index} index is not stable")
        graphic = scene.get("graphic") or {}
        if not _norm(graphic.get("template_id")):
            raise SemanticManifestError(f"Scene {expected_index} has no graphic template")
        media = scene.get("media") if isinstance(scene.get("media"), Mapping) else {}
        shots = media.get("shots") if isinstance(media.get("shots"), list) else []
        if shots:
            shot_cursor = 0.0
            for shot_index, shot in enumerate(shots):
                if not isinstance(shot, Mapping) or not _norm(shot.get("path") or shot.get("asset_path")):
                    raise SemanticManifestError(f"Scene {expected_index} shot {shot_index} has no media path")
                try:
                    shot_start = float(shot.get("shot_start", shot_cursor))
                    shot_end = float(shot.get("shot_end"))
                except (TypeError, ValueError):
                    raise SemanticManifestError(f"Scene {expected_index} shot {shot_index} has malformed timing")
                if shot_start < -0.001 or shot_end <= shot_start or shot_end > duration + 0.02:
                    raise SemanticManifestError(f"Scene {expected_index} shot {shot_index} is outside scene bounds")
                if shot_start > shot_cursor + 0.03:
                    raise SemanticManifestError(f"Scene {expected_index} shot timeline has a gap")
                shot_cursor = max(shot_cursor, shot_end)
            if shot_cursor < duration - 0.03:
                raise SemanticManifestError(f"Scene {expected_index} shot timeline does not cover the scene")
        cue = graphic.get("cue") if isinstance(graphic.get("cue"), Mapping) else None
        if cue is not None:
            try:
                cue_start = float(cue.get("scene_local_start_seconds", cue.get("scene_local_start")))
                cue_end = float(cue.get("scene_local_end_seconds", cue.get("scene_local_end")))
            except (TypeError, ValueError):
                raise SemanticManifestError(f"Scene {expected_index} has malformed cue timing")
            if cue_start < 0 or cue_end <= cue_start or cue_end > duration + 0.001:
                raise SemanticManifestError(f"Scene {expected_index} cue is outside scene bounds")
            if _norm(cue.get("scene_id")) != _norm(scene.get("scene_id") or scene.get("id")):
                raise SemanticManifestError(f"Scene {expected_index} cue references another scene")
            if _norm(cue.get("container_id")) != _norm(scene.get("container_id")):
                raise SemanticManifestError(f"Scene {expected_index} cue crosses a container")
            cue_text = _norm(cue.get("text"))
            props = graphic.get("props") if isinstance(graphic.get("props"), Mapping) else {}
            visible_values: List[str] = []
            for key in (
                "title", "text", "name", "label", "date", "value", "quote",
                "location", "to", "line1", "line2", "line3", "subtitle", "kicker",
            ):
                if _norm(props.get(key)):
                    visible_values.append(_norm(props.get(key)))
            visible_values.extend(
                _norm(value) for value in (props.get("items") or []) if _norm(value)
            )
            cue_is_grounded = bool(cue_text) and _grounded_display_text(cue_text, narration)
            props_are_grounded = all(
                _grounded_display_text(value, narration) for value in visible_values
            )
            if not cue_is_grounded or not props_are_grounded:
                # Keep the valid media scene and its non-text motion design,
                # but never display a phrase that is not traceable to the
                # narration.  This prevents a malformed cue from triggering
                # a low-quality recovery render in manual-source jobs.
                graphic.pop("cue", None)
                graphic["props"] = {}
                result["scenes"][expected_index]["graphic"] = graphic
            elif (
                _norm(cue.get("timing_quality")) not in {"structural_boundary", "scene_window_estimate"}
                and (cue.get("word_start_index") is None or cue.get("word_end_index") is None)
            ):
                raise SemanticManifestError(f"Scene {expected_index} cue lacks exact word indexes")
        transition = scene.get("transition") or {}
        try:
            transition_duration = float(transition.get("duration", 0))
        except Exception:
            transition_duration = -1
        if transition_duration < 0 or transition_duration > 0.8:
            raise SemanticManifestError(f"Scene {expected_index} transition is outside bounds")
        previous_end = start + duration
    result["duration"] = round(max(float(result.get("duration", 0)), previous_end), 3)
    result["duration_in_frames"] = max(1, int(result.get("duration_in_frames") or round(result["duration"] * fps)))
    return result


def get_template_manifest() -> Dict[str, Any]:
    return {
        "version": MANIFEST_VERSION,
        "themes": deepcopy(THEME_PROFILES),
        "templates": deepcopy(TEMPLATE_REGISTRY),
        "sound_families": deepcopy(SOUND_FAMILIES),
        "libraries": library_manifest(),
    }
