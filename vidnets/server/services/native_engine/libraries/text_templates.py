"""
Text Template Library — professionally designed documentary text layouts.

Each template returns a dict of drawtext filter chains ready for FFmpeg.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class TextLayer:
    """A single drawtext layer within a template."""
    text: str
    font_file: str = "Poppins-Bold.ttf"
    font_size: int = 72
    color: str = "#f4f0e6"
    x: str = "(w-text_w)/2"
    y: str = "(h-text_h)/2"
    alpha: str = "1"
    shadow: bool = True
    border: bool = False
    box: bool = True
    box_color: str = "0x0a0f1d@0.88"
    box_padding: int = 14


@dataclass  
class TextTemplate:
    """A complete text template (1-4 layers)."""
    id: str
    label: str
    category: str
    layers: List[TextLayer] = field(default_factory=list)
    background: Optional[Dict[str, str]] = None
    animation: str = "fade_in"
    performance_cost: int = 1
    style_tags: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════
# Template Registry
# ═══════════════════════════════════════════════════════════════════

TEMPLATES: Dict[str, TextTemplate] = {}

def register(t: TextTemplate):
    TEMPLATES[t.id] = t
    return t


# ── Chapter Title ──
register(TextTemplate(
    id="chapter_title",
    label="Chapter Title",
    category="opener",
    animation="scale_pop",
    performance_cost=1,
    style_tags=["chapter", "bold", "cinematic"],
    layers=[
        TextLayer(text="{kicker}", font_file="Poppins-Bold.ttf", font_size=22,
                  color="{accent}", y="(h-text_h)/2-140", shadow=False),
        TextLayer(text="{title}", font_file="Poppins-Bold.ttf", font_size=90,
                  color="{primary}", y="(h-text_h)/2-40"),
    ],
))

# ── Keyword Emphasis ──
register(TextTemplate(
    id="keyword_emphasis",
    label="Keyword Emphasis",
    category="emphasis",
    animation="scale_pop",
    performance_cost=1,
    style_tags=["emphasis", "reveal", "impact"],
    layers=[
        TextLayer(text="{text}", font_file="Poppins-Bold.ttf", font_size=84,
                  color="{primary}", y="(h-text_h)/2-50"),
        TextLayer(text="{detail}", font_file="OpenSans-Bold.ttf", font_size=22,
                  color="{primary}", y="(h-text_h)/2+80", shadow=False),
    ],
))

# ── Quote Card ──
register(TextTemplate(
    id="quote_card",
    label="Quote Card",
    category="testimony",
    animation="fade_in",
    performance_cost=1,
    style_tags=["testimony", "emotional", "serious"],
    layers=[
        TextLayer(text='\\"', font_file="Poppins-Bold.ttf", font_size=200,
                  color="{accent}", x="(w-text_w)/2-500", y="(h-text_h)/2-200", shadow=False),
        TextLayer(text="{quote}", font_file="Poppins-Bold.ttf", font_size=52,
                  color="{primary}", y="(h-text_h)/2-40", shadow=False),
        TextLayer(text="— {attribution}", font_file="OpenSans-Bold.ttf", font_size=20,
                  color="{accent}", y="(h-text_h)/2+100", shadow=False),
    ],
))

# ── Date Reveal ──
register(TextTemplate(
    id="date_reveal",
    label="Date Reveal",
    category="timeline",
    animation="slide_up",
    performance_cost=1,
    style_tags=["timeline", "reveal", "historical"],
    layers=[
        TextLayer(text="{kicker}", font_file="Poppins-Bold.ttf", font_size=20,
                  color="{accent}", y="(h-text_h)/2-140", shadow=False),
        TextLayer(text="{date}", font_file="Poppins-Bold.ttf", font_size=100,
                  color="{primary}", y="(h-text_h)/2-30"),
    ],
))

# ── Location Label ──
register(TextTemplate(
    id="location_label",
    label="Location Label",
    category="location",
    animation="slide_left",
    performance_cost=1,
    style_tags=["location", "informational"],
    layers=[
        TextLayer(text="{location}", font_file="Poppins-Bold.ttf", font_size=30,
                  color="{primary}", x="w*0.06", y="h-text_h-h*0.10", shadow=False),
        TextLayer(text="{detail}", font_file="OpenSans-Bold.ttf", font_size=18,
                  color="{accent}", x="w*0.06", y="h-text_h-h*0.10+38", shadow=False),
    ],
))

# ── Stat Counter ──
register(TextTemplate(
    id="stat_counter",
    label="Statistic Counter",
    category="counter",
    animation="scale_pop",
    performance_cost=2,
    style_tags=["statistic", "impact", "data"],
    layers=[
        TextLayer(text="{value}", font_file="Poppins-Bold.ttf", font_size=140,
                  color="{accent}", y="(h-text_h)/2-30"),
        TextLayer(text="{label}", font_file="Poppins-Bold.ttf", font_size=28,
                  color="{primary}", y="(h-text_h)/2+90", shadow=False),
    ],
))

# ── Money Counter ──
register(TextTemplate(
    id="money_counter",
    label="Money Counter",
    category="counter",
    animation="scale_pop",
    performance_cost=2,
    style_tags=["finance", "impact", "money"],
    layers=[
        TextLayer(text="${value}{unit}", font_file="Poppins-Bold.ttf", font_size=120,
                  color="{accent}", y="(h-text_h)/2-30"),
        TextLayer(text="{label}", font_file="Poppins-Bold.ttf", font_size=26,
                  color="{primary}", y="(h-text_h)/2+80", shadow=False),
    ],
))

# ── Evidence Callout ──
register(TextTemplate(
    id="evidence_callout",
    label="Evidence Callout",
    category="evidence",
    animation="slide_left",
    performance_cost=2,
    style_tags=["investigation", "evidence", "crime"],
    layers=[
        TextLayer(text="{stamp}", font_file="Poppins-Bold.ttf", font_size=18,
                  color="{danger}", x="w*0.06", y="h*0.08", shadow=False),
        TextLayer(text="{text}", font_file="Poppins-Bold.ttf", font_size=64,
                  color="{primary}", x="w*0.06", y="h*0.08+38",
                  box=True, box_color="{bg}", box_padding=30),
        TextLayer(text="{detail}", font_file="OpenSans-Bold.ttf", font_size=20,
                  color="{primary}", x="w*0.06", y="h*0.08+110", shadow=False),
    ],
))

# ── Lower Third ──
register(TextTemplate(
    id="lower_third",
    label="Lower Third",
    category="label",
    animation="slide_left",
    performance_cost=1,
    style_tags=["label", "informational", "clean"],
    layers=[
        TextLayer(text="{name}", font_file="Poppins-Bold.ttf", font_size=30,
                  color="{primary}", x="w*0.06", y="h-text_h-h*0.10-38", shadow=False),
        TextLayer(text="{title}", font_file="OpenSans-Bold.ttf", font_size=18,
                  color="{accent}", x="w*0.06", y="h-text_h-h*0.10", shadow=False),
    ],
))

# ── End Card ──
register(TextTemplate(
    id="end_card",
    label="End Card",
    category="closing",
    animation="scale_pop",
    performance_cost=1,
    style_tags=["closing", "cta", "brand"],
    layers=[
        TextLayer(text="{title}", font_file="Poppins-Bold.ttf", font_size=72,
                  color="{primary}", y="(h-text_h)/2-40"),
        TextLayer(text="{subtitle}", font_file="Poppins-Bold.ttf", font_size=24,
                  color="{accent}", y="(h-text_h)/2+60", shadow=False),
    ],
))

# ── Redaction / Classified ──
register(TextTemplate(
    id="redaction_reveal",
    label="Classified File",
    category="evidence",
    animation="glitch_in",
    performance_cost=2,
    style_tags=["investigation", "classified", "secret"],
    layers=[
        TextLayer(text="CLASSIFIED", font_file="Poppins-Bold.ttf", font_size=18,
                  color="{danger}", y="(h-text_h)/2-130", shadow=False),
        TextLayer(text="{text}", font_file="Poppins-Bold.ttf", font_size=56,
                  color="{primary}", y="(h-text_h)/2-20"),
        TextLayer(text="{detail}", font_file="OpenSans-Bold.ttf", font_size=20,
                  color="{primary}", y="(h-text_h)/2+70", shadow=False),
    ],
))

# ── Source Citation ──
register(TextTemplate(
    id="source_citation",
    label="Source Citation",
    category="evidence",
    animation="fade_in",
    performance_cost=1,
    style_tags=["evidence", "credibility", "source"],
    layers=[
        TextLayer(text="SOURCE", font_file="Poppins-Bold.ttf", font_size=14,
                  color="{accent}", y="(h-text_h)/2-60", shadow=False),
        TextLayer(text="{text}", font_file="OpenSans-Bold.ttf", font_size=28,
                  color="{primary}", y="(h-text_h)/2-10", shadow=False),
        TextLayer(text="{source}", font_file="OpenSans-Bold.ttf", font_size=16,
                  color="{accent}", y="(h-text_h)/2+50", shadow=False),
    ],
))

# ── Countdown ──
register(TextTemplate(
    id="countdown_clock",
    label="Countdown Clock",
    category="tension",
    animation="pulse",
    performance_cost=1,
    style_tags=["tension", "time", "urgent"],
    layers=[
        TextLayer(text="{value}", font_file="Poppins-Bold.ttf", font_size=120,
                  color="{danger}", y="(h-text_h)/2-20"),
        TextLayer(text="{label}", font_file="Poppins-Bold.ttf", font_size=22,
                  color="{primary}", y="(h-text_h)/2+90", shadow=False),
    ],
))

# ── Before / After ──
register(TextTemplate(
    id="before_after",
    label="Before / After",
    category="comparison",
    animation="slide_left",
    performance_cost=2,
    style_tags=["comparison", "contrast", "data"],
    layers=[
        TextLayer(text="{before_label}", font_file="Poppins-Bold.ttf", font_size=28,
                  color="{accent}", x="(w-text_w)/4", y="(h-text_h)/2-180", shadow=False),
        TextLayer(text="{before_value}", font_file="Poppins-Bold.ttf", font_size=64,
                  color="{primary}", x="(w-text_w)/4", y="(h-text_h)/2-100"),
        TextLayer(text="{after_label}", font_file="Poppins-Bold.ttf", font_size=28,
                  color="{accent}", x="3*(w-text_w)/4", y="(h-text_h)/2-180", shadow=False),
        TextLayer(text="{after_value}", font_file="Poppins-Bold.ttf", font_size=64,
                  color="{primary}", x="3*(w-text_w)/4", y="(h-text_h)/2-100"),
    ],
))
