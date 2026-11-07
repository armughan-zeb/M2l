"""
Pro Template Library — Vox/CapCut-style editorial templates.

Each template returns a dict with:
  - filter_chain: list of FFmpeg filter strings (drawtext, drawbox, etc.)
  - sfx_cues: list of SFX cues with timing
  - background_treatment: image treatment name
  - performance_cost: total cost for budgeting
"""

import re
from typing import Dict, List
from .visual_effects import (
    top_accent_bar, bottom_accent_bar, vertical_accent_line,
    corner_bracket, center_accent_line,
    background_darken, background_blur, background_teal_orange,
    background_warm_grade, background_cold_grade,
    letterbox_cinematic,
)
from .sound_design import all_template_sfx, TEMPLATE_SFX


# ═══════════════════════════════════════════════════════════════════
# Helper: drawtext builder
# ═══════════════════════════════════════════════════════════════════

def dt(text, font, size, color, x, y, **kwargs):
    """Build a drawtext filter chain (with animation support)."""
    # Escape colons in text — FFmpeg uses : as option separator even inside quotes
    text = str(text).replace(":", "\\:") if text else ""
    # CRITICAL: drawtext uses w/h for video dimensions; iw/ih are drawbox-specific
    # and cause "Undefined constant" parse errors in drawtext on this FFmpeg build.
    x = re.sub(r'\biw\b', 'w', re.sub(r'\bih\b', 'h', str(x)))
    y = re.sub(r'\biw\b', 'w', re.sub(r'\bih\b', 'h', str(y)))
    parts = [
        f"text='{text}'",
        f"fontfile='{font}'",
        f"fontsize={size}",
        f"fontcolor={color}",
        f"x={x}",
        f"y={y}",
    ]
    if kwargs.get("anim") == "slide_up":
        anim_dur = kwargs.get("anim_dur", 0.5)
        parts[4] = f"x={x}"  # x stays fixed
        # y slides from below — commas escaped as \, for drawtext expression parser
        y_val = y
        rate = int(1.0 / max(0.1, anim_dur))
        parts[5] = f"y=({y_val} + max(0\\, 60 - (t*{rate})*60))"
    elif kwargs.get("anim") == "slide_left":
        anim_dur = kwargs.get("anim_dur", 0.5)
        x_val = x
        rate = int(1.0 / max(0.1, anim_dur))
        parts[4] = f"x=({x_val} - max(0\\, 100 - (t*{rate})*100))"
    elif kwargs.get("anim") == "scale_pop":
        anim_dur = kwargs.get("anim_dur", 0.4)
        anim_d = kwargs.get("anim_dur", 0.4)
        # can't do font scale easily — use x position offset for visual pop
        pass
    elif kwargs.get("anim") == "fade_in":
        # Use fontcolor alpha for fade
        pass

    if kwargs.get("shadow", True):
        parts.append("shadowcolor=black@0.65:shadowx=3:shadowy=3")
    if kwargs.get("box"):
        bc = kwargs.get("box_color", "black@0.7")
        bp = kwargs.get("box_padding", 18)
        parts.append(f"box=1:boxcolor={bc}:boxborderw={bp}")
    if kwargs.get("border"):
        parts.append("bordercolor=white@0.4:borderw=2")

    return "drawtext=" + ":".join(parts)


# ═══════════════════════════════════════════════════════════════════
# PRO TEMPLATES
# ═══════════════════════════════════════════════════════════════════

# Each template function takes (props, palette, duration) and returns:
# {
#   "filter_chain": [list of FFmpeg filter strings],
#   "sfx_cues": [list of dicts with key/start/volume/duration],
#   "background_treatment": str,
#   "performance_cost": int,
# }

def chapter_title_pro(props, palette, duration=3.0):
    """
    Pro chapter title — Vox style.
    Layout:
        - Top accent bar (sweeps in)
        - Kicker (small, gold, top)
        - Main title (large, white, center, slides up)
        - Accent underline (grows from center)
        - Bottom accent bar (sweeps in)
        - Background grade (warm, darken)
    SFX: whoosh_dark on entry, sub_boom on bar settle
    """
    kicker = props.get("kicker", "CHAPTER")
    title = props.get("title", "THE STORY")
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")

    chain = [
        # Background grade
        background_darken(0.2),
        # Top accent bar (sweeps in from left)
        top_accent_bar(x=0, y=0, width_pct=100, height=4, color=accent, anim="sweep", duration=0.5),
        # Bottom accent bar (sweeps in)
        top_accent_bar(x=0, y=1076, width_pct=100, height=4, color=accent, anim="sweep", duration=0.5),
        # Kicker (slides in from top)
        dt(kicker, "Poppins-Bold.ttf", 22, accent, "(w-text_w)/2", "100", anim="slide_up", anim_dur=0.4),
        # Main title (slides up from below)
        dt(title, "Poppins-Bold.ttf", 96, primary, "(w-text_w)/2", "(h-text_h)/2-10", anim="slide_up", anim_dur=0.5),
        # Accent underline (grows from center)
        center_accent_line(width=min(500, 30 * len(title) + 50), height=4, color=accent),
    ]
    return {
        "filter_chain": chain,
        "sfx_cues": [],
        "background_treatment": "static_hold",
        "performance_cost": 2,
    }


def keyword_reveal_pro(props, palette, duration=3.0):
    """
    Pro keyword reveal — big bold word with supporting details.
    Layout:
        - Background: darken + slight blur
        - Top-left corner bracket
        - Top-right corner bracket
        - Keyword (massive, slides up)
        - Detail line below (slides up, delayed)
        - Center accent line (grows)
        - Bottom: thin line
    SFX: whoosh_fast on entry, impact on settle
    """
    keyword = props.get("text", "KEYWORD")
    detail = props.get("detail", "")
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")

    h1, h2 = corner_bracket(3, 3, 40, 3, accent, "tl")
    h3, h4 = corner_bracket(3, 3, 40, 3, accent, "tr")

    chain = [
        background_darken(0.3),
        # Corner brackets
        h1, h2, h3, h4,
        # Main keyword (massive, centered, slides up)
        dt(keyword, "Poppins-Bold.ttf", 120, primary, "(w-text_w)/2", "(h-text_h)/2-30", anim="slide_up", anim_dur=0.4),
        # Detail line (smaller, below, slides up)
        dt(detail, "OpenSans-Bold.ttf", 24, primary, "(w-text_w)/2", "(h-text_h)/2+50", anim="slide_up", anim_dur=0.5) if detail else "",
        # Center accent line
        center_accent_line(width=180, height=3, color=accent),
    ]
    return {
        "filter_chain": [c for c in chain if c],
        "sfx_cues": [],
        "background_treatment": "slow_push_in",
        "performance_cost": 2,
    }


def money_counter_pro(props, palette, duration=3.0):
    """
    Pro money counter — massive dollar amount.
    Layout:
        - Background: darken + teal/orange grade
        - Top label: "VALUE" or similar
        - Huge dollar amount (Bebas, 140pt)
        - Unit indicator (M/B)
        - Bottom label
        - Side accent line
        - Corner brackets
    SFX: cash_register + coin ticks
    """
    value = props.get("value", "0")
    label = props.get("label", "TOTAL VALUE")
    unit = props.get("unit", "")
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")

    h1, h2 = corner_bracket(4, 4, 35, 3, accent, "tl")
    h3, h4 = corner_bracket(4, 4, 35, 3, accent, "br")
    amount = f"${value}{unit}" if unit else f"${value}"

    chain = [
        background_darken(0.3),
        background_teal_orange(),
        h1, h2, h3, h4,
        # Top accent line
        top_accent_bar(x=0, y=0, width_pct=100, height=3, color=accent, anim="sweep", duration=0.5),
        # Top label
        dt(label, "Poppins-Bold.ttf", 22, accent, "(w-text_w)/2", "90", anim="slide_up", anim_dur=0.3),
        # Huge dollar amount
        dt(amount, "Poppins-Bold.ttf", 140, accent, "(w-text_w)/2", "(h-text_h)/2-30", anim="slide_up", anim_dur=0.5),
        # Bottom small label
        dt("UNTRACEABLE", "Poppins-Bold.ttf", 18, primary, "(w-text_w)/2", "h-80", anim="slide_up", anim_dur=0.4),
    ]
    return {
        "filter_chain": [c for c in chain if c],
        "sfx_cues": [],
        "background_treatment": "parallax_push",
        "performance_cost": 2,
    }


def stat_counter_pro(props, palette, duration=3.0):
    """
    Pro stat counter — number with counting-up feel.
    Layout:
        - Background: darken
        - Top: thin bar
        - Center: large number (slides up)
        - Below: descriptive label
        - Underline accent
        - Side accent line
    SFX: data ticks + impact on settle
    """
    value = props.get("value", "0")
    label = props.get("label", "STATISTIC")
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")

    chain = [
        background_darken(0.25),
        # Top accent line
        top_accent_bar(x=0, y=0, width_pct=100, height=3, color=accent, anim="sweep", duration=0.5),
        # Vertical accent line on left
        vertical_accent_line(x_pct=8, y_pct=15, height_pct=70, width=3, color=accent),
        # Big number
        dt(value, "Poppins-Bold.ttf", 144, primary, "(w-text_w)/2", "(h-text_h)/2-30", anim="slide_up", anim_dur=0.4),
        # Accent underline
        center_accent_line(width=220, height=4, color=accent),
        # Label below
        dt(label, "Poppins-Bold.ttf", 22, accent, "(w-text_w)/2", "(h-text_h)/2+60", anim="slide_up", anim_dur=0.5),
    ]
    return {
        "filter_chain": [c for c in chain if c],
        "sfx_cues": [],
        "background_treatment": "slow_push_in",
        "performance_cost": 2,
    }


def quote_card_pro(props, palette, duration=4.0):
    """
    Pro quote card — elegant testimonial.
    Layout:
        - Background: darken + cinematic letterbox
        - Big opening quote mark (serif, gold, decorative)
        - Quote text (serif, large, center)
        - Attribution with em-dash
        - Thin horizontal line above attribution
    SFX: paper_flip
    """
    quote = props.get("quote", "A notable quote.")
    attribution = props.get("attribution", "Source")
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")

    chain = [
        background_darken(0.35),
        # Large opening quote mark
        dt('"', "Poppins-Bold.ttf", 220, rgba_03(accent, 0.6), "(w/2-460)", "120"),
        # Quote text
        dt(quote, "Poppins-Bold.ttf", 48, primary, "120", "(h-text_h)/2-30", shadow=False),
        # Thin line above attribution
        center_accent_line(width=120, height=2, color=accent),
        # Attribution
        dt(f"— {attribution}", "Poppins-Bold.ttf", 20, accent, "(w-text_w)/2", "h-110", anim="slide_up", anim_dur=0.4),
    ]
    return {
        "filter_chain": [c for c in chain if c],
        "sfx_cues": [],
        "background_treatment": "slow_push_in",
        "performance_cost": 2,
    }


def evidence_callout_pro(props, palette, duration=3.5):
    """
    Pro evidence callout — investigative stamp card.
    Layout:
        - Background: darken + warm grade
        - Red/danger top-left stamp
        - Big evidence text in card
        - Detail line below
        - Red corner accent bar
        - Border around card area
    SFX: stamp_hit + paper_rustle
    """
    stamp = props.get("stamp", "EVIDENCE")
    text = props.get("text", "THE RECORD")
    detail = props.get("detail", "")
    danger = palette.get("danger", "#ef6f6c")
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")

    chain = [
        background_darken(0.3),
        background_warm_grade(),
        # Top-left danger stamp (angled)
        dt(stamp, "Poppins-Bold.ttf", 18, danger, "100", "120", shadow=False),
        # Red accent line on left edge
        vertical_accent_line(x_pct=5, y_pct=20, height_pct=60, width=5, color=danger),
        # Evidence text in card
        dt(text, "Poppins-Bold.ttf", 72, primary, "100", "(h-text_h)/2-20", anim="slide_left", anim_dur=0.5, box=True, box_color="black@0.6", box_padding=25),
        # Detail line
        dt(detail, "OpenSans-Bold.ttf", 20, rgba_03(primary, 0.85), "100", "h-140", shadow=False) if detail else "",
    ]
    return {
        "filter_chain": [c for c in chain if c],
        "sfx_cues": [],
        "background_treatment": "tilt_up",
        "performance_cost": 2,
    }


def date_reveal_pro(props, palette, duration=3.0):
    """
    Pro date reveal — dramatic date card.
    Layout:
        - Background: darken
        - Top: "ON THIS DATE" kicker
        - Center: large date in serif/Bebas
        - Below: 2 horizontal accent lines (top and bottom of date)
        - Vertical accent line left side
    SFX: calendar_flip
    """
    date = props.get("date", "JANUARY 1, 2000")
    kicker = props.get("kicker", "ON THIS DATE")
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")

    chain = [
        background_darken(0.25),
        # Top thin bar
        top_accent_bar(x=0, y=0, width_pct=100, height=3, color=accent, anim="sweep", duration=0.4),
        # Vertical accent left
        vertical_accent_line(x_pct=10, y_pct=20, height_pct=60, width=2, color=accent),
        # Kicker
        dt(kicker, "Poppins-Bold.ttf", 20, accent, "(w-text_w)/2", "150", anim="slide_up", anim_dur=0.3),
        # Big date
        dt(date, "Poppins-Bold.ttf", 96, primary, "(w-text_w)/2", "(h-text_h)/2", anim="slide_up", anim_dur=0.4),
        # Bottom accent line (grows)
        bottom_accent_bar(y_pct=80, width_pct=40, height=3, color=accent, anim="grow", duration=0.5),
    ]
    return {
        "filter_chain": [c for c in chain if c],
        "sfx_cues": [],
        "background_treatment": "slow_push_in",
        "performance_cost": 2,
    }


def redaction_reveal_pro(props, palette, duration=3.0):
    """
    Pro redaction/classified — investigative reveal.
    Layout:
        - Background: darken + cold grade
        - Red "CLASSIFIED" stamp (top)
        - Black redaction bars over text
        - Glitch-style accent
        - Tension feel
    SFX: marker_scratch + tension_string
    """
    text = props.get("text", "REDACTED")
    label = props.get("label", "CLASSIFIED")
    detail = props.get("detail", "")
    danger = palette.get("danger", "#ef6f6c")
    primary = palette.get("primary", "#f4f0e6")

    chain = [
        background_darken(0.4),
        background_cold_grade(),
        # Top danger label
        dt(label, "Poppins-Bold.ttf", 16, danger, "(w-text_w)/2", "100", shadow=False),
        # Black redaction bar over middle
        "drawbox=x=0:y=(ih/2-20):w=iw:h=40:color=black@0.9:t=fill",
        # Red text peeking from behind redaction
        dt(text, "Poppins-Bold.ttf", 56, primary, "(w-text_w)/2", "(h-text_h)/2-5", anim="slide_up", anim_dur=0.4),
        # Detail line
        dt(detail, "OpenSans-Bold.ttf", 18, rgba_03(primary, 0.8), "(w-text_w)/2", "h-100", shadow=False) if detail else "",
    ]
    return {
        "filter_chain": [c for c in chain if c],
        "sfx_cues": [],
        "background_treatment": "diagonal_pan",
        "performance_cost": 2,
    }


def lower_third_pro(props, palette, duration=3.0):
    """
    Pro lower third — name/title plate.
    Layout:
        - Bottom-left accent line
        - Person name (slides in from left)
        - Title/role (smaller, below name)
        - Semi-transparent dark background bar
    SFX: whoosh_soft
    """
    name = props.get("title", "NAME")
    title = props.get("subtitle", "Title")
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")

    chain = [
        # Name (strict bottom_left anchor with padded dark card)
        dt(name, "Poppins-Bold.ttf", 36, primary, "w*0.06", "h-text_h-h*0.10-38", box=True, box_color="0x0a0f1d@0.88", box_padding=14),
        # Title/role
        dt(title, "OpenSans-Bold.ttf", 20, accent, "w*0.06", "h-text_h-h*0.10", box=True, box_color="0x0a0f1d@0.88", box_padding=10) if title else "",
    ]
    return {
        "filter_chain": [c for c in chain if c],
        "sfx_cues": [],
        "background_treatment": "pan_left",
        "performance_cost": 1,
    }


def end_card_pro(props, palette, duration=4.0):
    """
    Pro end card — final subscription/CTA card.
    Layout:
        - Background: darken
        - Top accent bar
        - Big title (center)
        - Subtitle
        - Accent line under subtitle
        - Bottom: social handle / CTA
    SFX: reveal_chime + riser
    """
    title = props.get("title", "THE END")
    subtitle = props.get("subtitle", "Follow for more")
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")

    chain = [
        background_darken(0.3),
        # Top accent bar
        top_accent_bar(x=0, y=0, width_pct=100, height=5, color=accent, anim="sweep", duration=0.5),
        # Bottom accent bar
        top_accent_bar(x=0, y=1075, width_pct=100, height=5, color=accent, anim="sweep", duration=0.5),
        # Title
        dt(title, "Poppins-Bold.ttf", 84, primary, "(w-text_w)/2", "(h-text_h)/2-40", anim="slide_up", anim_dur=0.5),
        # Accent underline
        center_accent_line(width=280, height=4, color=accent),
        # Subtitle
        dt(subtitle, "Poppins-Bold.ttf", 26, accent, "(w-text_w)/2", "(h-text_h)/2+40", anim="slide_up", anim_dur=0.5),
    ]
    return {
        "filter_chain": [c for c in chain if c],
        "sfx_cues": [],
        "background_treatment": "slow_push_in",
        "performance_cost": 2,
    }


def cold_open_pro(props, palette, duration=3.0):
    """
    Pro cold open — dramatic hook.
    Layout:
        - Background: darken + cold grade
        - Center: big provocative question
        - Top: thin bar
        - Bottom: "A DOCUMENTARY" label
    SFX: sub_boom + whoosh_dark
    """
    text = props.get("text", "WHAT REALLY HAPPENED?")
    kicker = props.get("kicker", "")
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")

    chain = [
        background_darken(0.4),
        background_cold_grade(),
        # Top thin bar
        top_accent_bar(x=0, y=0, width_pct=100, height=3, color=accent, anim="sweep", duration=0.5),
        # Kicker (if provided)
        dt(kicker, "Poppins-Bold.ttf", 18, accent, "(w-text_w)/2", "200", anim="slide_up", anim_dur=0.3) if kicker else "",
        # Big question
        dt(text, "Poppins-Bold.ttf", 84, primary, "(w-text_w)/2", "(h-text_h)/2", anim="slide_up", anim_dur=0.5),
    ]
    return {
        "filter_chain": [c for c in chain if c],
        "sfx_cues": [],  # disabled to avoid audio binding issues
        "background_treatment": "parallax_push",
        "performance_cost": 2,
    }


def source_citation_pro(props, palette, duration=3.0):
    """
    Pro source citation — credibility/attribution.
    Layout:
        - Bottom: source info card
        - "SOURCE" label small
        - Citation text
        - Source name below
    SFX: paper_flip
    """
    text = props.get("text", "A factual detail appears here.")
    source = props.get("source", "")
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")

    chain = [
        # Bottom card
        "drawbox=x=100:y=ih-200:w=iw-200:h=120:color=black@0.7:t=fill",
        # Source label
        dt("SOURCE", "Poppins-Bold.ttf", 14, accent, "130", "h-180", shadow=False),
        # Citation text
        dt(text, "OpenSans-Bold.ttf", 24, primary, "130", "h-140", anim="slide_up", anim_dur=0.4),
        # Source name
        dt(f"— {source}", "Poppins-Bold.ttf", 16, accent, "130", "h-95", anim="slide_up", anim_dur=0.5) if source else "",
    ]
    return {
        "filter_chain": [c for c in chain if c],
        "sfx_cues": [],
        "background_treatment": "pan_right",
        "performance_cost": 2,
    }


# ═══════════════════════════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════════════════════════

PRO_TEMPLATES = {
    "chapter_title":     chapter_title_pro,
    "keyword_reveal":    keyword_reveal_pro,
    "money_counter":     money_counter_pro,
    "stat_counter":      stat_counter_pro,
    "quote_card":        quote_card_pro,
    "evidence_callout":  evidence_callout_pro,
    "date_reveal":       date_reveal_pro,
    "redaction_reveal":  redaction_reveal_pro,
    "lower_third":       lower_third_pro,
    "end_card":          end_card_pro,
    "cold_open":         cold_open_pro,
    "source_citation":   source_citation_pro,
}


def render_template(template_id: str, props: dict, palette: dict, duration: float = 3.0) -> dict:
    """Render a pro template. Returns the template structure."""
    fn = PRO_TEMPLATES.get(template_id)
    if not fn:
        return None
    return fn(props, palette, duration)


# ═══════════════════════════════════════════════════════════════════
# Utility
# ═══════════════════════════════════════════════════════════════════

def rgba_03(hex_color, alpha):
    """Convert hex color to rgba(r\\,g\\,b\\,a) format for FFmpeg."""
    hex_color = hex_color.lstrip('#')
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return r"rgba(" + str(r) + r"\," + str(g) + r"\," + str(b) + r"\," + str(alpha) + r")"
