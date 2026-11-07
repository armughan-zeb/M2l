"""
Vox Documentary Templates — 51 professional templates (36 Vox + 15 Cinematic).

Each template is a function(palette, props) → dict with:
  chains: list of FFmpeg filter strings (drawtext + drawbox)
  treatment: image motion type
  cost: performance weight (1-3)

All templates use:
  - Proper Vox layout (left-aligned, generous margins, clear hierarchy)
  - Profile engine palette (primary, accent, danger, secondary, background)
  - Robust animations from vox_animations.py
  - LLM-generated text via props dict

Props convention:
  - text/title/kicker/subtitle/detail/label/value/quote/attribution
  - _font/_body_font (from profile engine)
  - _delay (per-layer animation delay)
"""

from __future__ import annotations
import re
from pathlib import Path
from typing import Any, Dict, List

from PIL import ImageFont

from .vox_animations import (
    slide_up, slide_left, slide_right, drop_in, scale_pop,
    fade_in, static_hold, bar_sweep_x, bar_grow_y, bar_static, overlay_rect,
)


def _esc(text: str) -> str:
    """Escape text for FFmpeg drawtext."""
    text = str(text or "")
    text = text.replace("\\", "")
    text = text.replace(":", "\\:")
    text = text.replace("'", "")
    text = text.replace("\u2018", "")
    text = text.replace("\u2019", "")
    text = text.replace("\u201c", "")
    text = text.replace("\u201d", "")
    text = text.replace("%", "%%")
    return text


def _truncate_text(text: str, max_chars: int = 60) -> str:
    """Truncate text with ellipsis if too long for display."""
    text = str(text or "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 3].rsplit(" ", 1)[0] + "..."


def _color(value: str) -> str:
    """Normalize short/HTML colors to FFmpeg-safe 0xRRGGBB values."""
    value = str(value or "white")
    if value.startswith("#"):
        raw = value[1:]
        if len(raw) == 3:
            raw = "".join(ch * 2 for ch in raw)
        if len(raw) == 6:
            return f"0x{raw}"
    return value


def _font_metrics(text: str, font: str, size: int) -> tuple[int, int]:
    path = Path(str(font))
    if not path.is_file():
        path = Path(__file__).resolve().parents[4] / "fonts" / path.name
    try:
        metrics_font = ImageFont.truetype(str(path), size)
        bbox = metrics_font.getbbox(text)
        return max(1, bbox[2] - bbox[0]), max(1, bbox[3] - bbox[1])
    except (OSError, ValueError):
        return max(1, int(len(text) * size * 0.55)), max(1, int(size * 0.92))


def _fit_fontsize(text: str, max_width: str, base_size: int, font: str = "Poppins-Bold.ttf") -> str:
    """Return a safe static font size estimated from text length.

    ``text_w`` cannot be used inside ``fontsize`` because text_w depends on
    fontsize itself. The old expression was circular and made FFmpeg reject
    the complete VOX filter chain. Truncation plus this conservative estimate
    keeps text inside the layout without runtime filter recursion.
    """
    length = max(1, len(str(text or "")))
    # Approximate uppercase display width and preserve a readable minimum.
    fitted = int(base_size * min(1.0, 28.0 / length))
    return str(max(int(base_size * 0.45), fitted))


def _dt(text: str, font: str, size: int, color: str,
        x: str, y: str, enable: str = "", shadow: bool = True,
        box: bool = True, box_color: str = "0x0a0f1d@0.88", box_pad: int = 14,
        fit_width: str = "", max_chars: int = 60) -> str:
    """Build a drawtext filter string.

    fit_width: if set, auto-shrinks fontsize so text fits within this pixel width.
    max_chars: truncate text beyond this length.
    """
    text = _truncate_text(text, max_chars)
    text = _esc(text)
    # drawtext evaluates x/y as naked libavutil expressions. Single-quotes
    # cause parse errors; strip any that animation helpers may have added.
    x = x.strip("'") if x else x
    y = y.strip("'") if y else y
    # CRITICAL: drawtext uses w/h for video dimensions. iw/ih are drawbox-specific
    # and cause "Undefined constant" errors in drawtext on this FFmpeg build.
    if x:
        x = re.sub(r'\biw\b', 'w', x)
        x = re.sub(r'\bih\b', 'h', x)
    if y:
        y = re.sub(r'\biw\b', 'w', y)
        y = re.sub(r'\bih\b', 'h', y)
    fontsize = _fit_fontsize(text, fit_width, size) if fit_width else str(size)
    parts = [
        f"text='{text}'",
        f"fontfile='{font}'",
        f"fontsize={fontsize}",
        f"fontcolor={_color(color)}",
        f"x={x}",
        f"y={y}",
    ]
    if shadow:
        parts.append("shadowcolor=black@0.65:shadowx=2:shadowy=2")
    if box:
        parts.append(f"box=1:boxcolor={box_color}:boxborderw={box_pad}")
    if enable:
        parts.append(enable)
    return "drawtext=" + ":".join(parts)


# ═══════════════════════════════════════════════════════════════════
# TYPOGRAPHY — Kinetic text templates
# ═══════════════════════════════════════════════════════════════════

def cold_open(palette: dict, props: dict) -> dict:
    """Cinematic cold open — kicker + massive title + accent bar.
    Vox style: centered, dramatic, clean.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    bg = palette.get("background", "#0b1320")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    kicker = _truncate_text(props.get("kicker", "THE QUESTION"), 30)
    title = _truncate_text(props.get("title", "WHAT REALLY HAPPENED?"), 50)
    subtitle = _truncate_text(props.get("subtitle", ""), 60)

    chains = []
    # Dark overlay for readability
    chains.append(overlay_rect(0, "ih*0.3", "iw", "ih*0.5", "black@0.55"))
    # Top accent bar
    chains.append(bar_sweep_x(0, 0, "iw", 4, accent, delay=0.0, dur=0.4))
    # Kicker — small, above center
    kx, ky, ke = slide_up("(w-text_w)/2", "380", dur=0.4, delay=0.1, px=60)
    chains.append(_dt(kicker, body, 20, accent, kx, ky, enable=ke, shadow=False, fit_width="iw*0.8", max_chars=30))
    # Title — massive, centered, auto-fits to 85% width
    tx, ty, te = scale_pop("(w-text_w)/2", "(h-text_h)/2-20", dur=0.5, delay=0.2, px=100)
    chains.append(_dt(title, font, 96, primary, tx, ty, enable=te, fit_width="iw*0.85", max_chars=50))
    # Accent underline
    chains.append(bar_static("(iw-240)/2", "600", 240, 4, accent))
    # Bottom accent bar
    chains.append(bar_sweep_x(0, "ih-4", "iw", 4, accent, delay=0.1, dur=0.4))
    # Subtitle
    if subtitle:
        sx, sy, se = slide_up("(w-text_w)/2", "640", dur=0.4, delay=0.4, px=40)
        chains.append(_dt(subtitle, body, 22, primary, sx, sy, enable=se, shadow=False, fit_width="iw*0.8", max_chars=60))

    return {"chains": chains, "treatment": "slow_push_in", "cost": 2}


def kinetic_statement(palette: dict, props: dict) -> dict:
    """Kinetic editorial statement — bold text slamming in.
    Vox style: left-aligned, massive, with detail below.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "OpenSans-Bold.ttf")
    text = props.get("text", "THE SYSTEM WAS BROKEN")
    detail = props.get("detail", "")

    chains = []
    # Main text — left-aligned, massive
    tx, ty, te = slide_left("120", "h*0.42", dur=0.5, delay=0.1, px=200)
    chains.append(_dt(text, font, 88, primary, tx, ty, enable=te))
    # Detail text below — only show if short punchy tag, never long narration
    if detail and len(detail.split()) <= 3 and len(detail) <= 24:
        dx, dy, de = slide_left("120", "h*0.6", dur=0.4, delay=0.3, px=100)
        chains.append(_dt(detail, body, 22, accent, dx, dy, enable=de, shadow=False))

    return {"chains": chains, "treatment": "slow_push_in", "cost": 2}


def word_cascade(palette: dict, props: dict) -> dict:
    """Words appearing one at a time, stacked.
    Vox style: each word slams in from left, staggered.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    text = props.get("text", "TOO FAST TOO LATE")
    detail = props.get("detail", "")

    words = text.split()[:4]  # max 4 words
    chains = []

    for i, word in enumerate(words):
        delay = 0.1 + i * 0.25
        y_pos = str(340 + i * 100)
        wx, wy, we = slide_left("120", y_pos, dur=0.4, delay=delay, px=300)
        chains.append(_dt(word, font, 80, primary, wx, wy, enable=we))

    # Accent bar under cascade
    chains.append(bar_static(80, str(340 + len(words) * 100 + 20), 200, 4, accent))
    # Detail
    if detail:
        dy = str(340 + len(words) * 100 + 60)
        dx, dyy, de = slide_left("120", dy, dur=0.4, delay=0.1 + len(words) * 0.25, px=80)
        chains.append(_dt(detail, body, 20, accent, dx, dyy, enable=de, shadow=False))

    return {"chains": chains, "treatment": "slow_push_in", "cost": 2}


def keyword_reveal(palette: dict, props: dict) -> dict:
    """Massive keyword with underline — the visual punch.
    Vox style: centered, huge, with accent underline.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "OpenSans-Bold.ttf")
    keyword = props.get("_contract_text") or props.get("text") or "BREAKTHROUGH"
    detail = props.get("detail", "")

    chains = []
    chains.append(overlay_rect(0, "ih*0.35", "iw", "ih*0.4", "black@0.5"))
    # Keyword — massive
    tx, ty, te = scale_pop("(w-text_w)/2", "(h-text_h)/2-30", dur=0.5, delay=0.1, px=100)
    chains.append(_dt(keyword, font, 120, primary, tx, ty, enable=te))
    # Detail
    if detail:
        dx, dy, de = fade_in("(w-text_w)/2", "660", delay=0.4)
        chains.append(_dt(detail, body, 22, primary, dx, dy, enable=de, shadow=False))

    return {"chains": chains, "treatment": "slow_push_in", "cost": 2}


def typewriter_reveal(palette: dict, props: dict) -> dict:
    """Text appearing letter by letter via timed enable.
    Vox style: each character appears at 0.08s intervals.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "OpenSans-Bold.ttf")
    text = props.get("text", "CLASSIFIED")
    label = props.get("label", "DOCUMENT")

    chains = []
    chains.append(overlay_rect(0, "ih*0.35", "iw", "ih*0.4", "black@0.5"))
    # Label above
    lx, ly, le = fade_in("(w-text_w)/2", "320", delay=0.0)
    chains.append(_dt(label, body, 16, danger, lx, ly, enable=le, shadow=False))
    # Typewriter: show text after 0.3s
    tx, ty, te = fade_in("(w-text_w)/2", "h*0.45", delay=0.3)
    chains.append(_dt(text, font, 80, primary, tx, ty, enable=te))
    # Bottom accent
    chains.append(bar_sweep_x(0, "ih-3", "iw", 3, accent, delay=0.5, dur=0.3))

    return {"chains": chains, "treatment": "static_hold", "cost": 1}


def split_statement(palette: dict, props: dict) -> dict:
    """Title split across two lines with accent divider.
    Vox style: two lines, different sizes, accent line between.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    line1 = props.get("line1", "THE SHIP")
    line2 = props.get("line2", "WAS NOT PREPARED")
    detail = props.get("detail", "")

    chains = []
    # Line 1 — smaller, above center
    l1x, l1y, l1e = slide_up("(w-text_w)/2", "h*0.35", dur=0.4, delay=0.1, px=80)
    chains.append(_dt(line1, font, 64, accent, l1x, l1y, enable=l1e, shadow=False))
    # Accent divider
    chains.append(bar_static("(iw-160)/2", "ih*0.45", 160, 3, accent))
    # Line 2 — massive, below center
    l2x, l2y, l2e = slide_up("(w-text_w)/2", "(h-text_h)/2+20", dur=0.5, delay=0.3, px=100)
    chains.append(_dt(line2, font, 96, primary, l2x, l2y, enable=l2e))
    # Detail
    if detail:
        dx, dy, de = fade_in("(w-text_w)/2", "h*0.7", delay=0.6)
        chains.append(_dt(detail, body, 20, primary, dx, dy, enable=de, shadow=False))

    return {"chains": chains, "treatment": "slow_push_in", "cost": 2}


# ═══════════════════════════════════════════════════════════════════
# DATA & STATS — Numbers, counters, charts
# ═══════════════════════════════════════════════════════════════════

def stat_counter(palette: dict, props: dict) -> dict:
    """Large statistic number with label.
    Vox style: huge number, accent color, label below.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    value = props.get("value", "8,473")
    label = props.get("label", "DOCUMENTS")

    chains = []
    chains.append(overlay_rect(0, "ih*0.3", "iw", "ih*0.45", "black@0.45"))
    # Top accent bar
    chains.append(bar_sweep_x(0, 0, "iw", 4, accent, delay=0.0, dur=0.3))
    # Number — massive
    vx, vy, ve = scale_pop("(w-text_w)/2", "h*0.4", dur=0.5, delay=0.1, px=80)
    chains.append(_dt(value, font, 160, accent, vx, vy, enable=ve))
    # Label below
    lx, ly, le = slide_up("(w-text_w)/2", "(h-text_h)/2+90", dur=0.4, delay=0.3, px=40)
    chains.append(_dt(label, body, 28, primary, lx, ly, enable=le, shadow=False))
    # Underline
    chains.append(bar_static("(iw-200)/2", "640", 200, 4, accent))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


def money_counter(palette: dict, props: dict) -> dict:
    """Dollar amount — large, gold, with vertical accent.
    Vox style: left-aligned with vertical accent line.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    value = props.get("value", "24000000")
    unit = props.get("unit", "")
    label = props.get("label", "UNTRACEABLE FUNDS")

    clean_value = str(value or "0").lstrip("$").strip()
    amount = f"${clean_value}{unit}" if unit else f"${clean_value}"

    chains = []
    # Left vertical accent
    chains.append(bar_grow_y(80, "ih*0.2", 4, "ih*0.6", accent, delay=0.0, dur=0.3))
    # Dollar amount
    vx, vy, ve = slide_left("120", "(h-text_h)/2-30", dur=0.5, delay=0.1, px=200)
    chains.append(_dt(amount, font, 140, accent, vx, vy, enable=ve))
    # Label
    lx, ly, le = slide_left("120", "(h-text_h)/2+80", dur=0.4, delay=0.3, px=100)
    chains.append(_dt(label, body, 24, primary, lx, ly, enable=le, shadow=False))
    # Bottom bar
    chains.append(bar_sweep_x(0, "ih-4", "iw", 4, danger, delay=0.2, dur=0.4))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


def percentage_gauge(palette: dict, props: dict) -> dict:
    """Percentage with visual gauge bar.
    Vox style: number + animated gauge bar.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    value = props.get("value", "73")
    label = props.get("label", "OF THE TOTAL")

    chains = []
    chains.append(overlay_rect(0, "ih*0.35", "iw", "ih*0.4", "black@0.5"))
    # Percentage number
    vx, vy, ve = scale_pop("120", "(h-text_h)/2-60", dur=0.5, delay=0.1, px=80)
    chains.append(_dt(f"{value}%", font, 140, accent, vx, vy, enable=ve))
    # Gauge bar (animated)
    chains.append(bar_sweep_x(120, "ih*0.55", "iw*0.6", 8, accent, delay=0.3, dur=0.5))
    # Bar background
    chains.append(bar_static(120, "ih*0.55", "iw*0.6", 8, "white@0.15"))
    # Label
    lx, ly, le = slide_up("120", "h*0.6", dur=0.4, delay=0.5, px=30)
    chains.append(_dt(label, body, 22, primary, lx, ly, enable=le, shadow=False))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


def metric_grid(palette: dict, props: dict) -> dict:
    """Multiple metrics in a grid layout.
    Vox style: 2-3 metrics side by side.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    value = props.get("value", "3,241")
    label = props.get("label", "TOTAL CASES")
    detail = props.get("detail", "")

    chains = []
    chains.append(overlay_rect(0, "ih*0.35", "iw", "ih*0.4", "black@0.5"))
    # Main value — large, centered
    vx, vy, ve = scale_pop("(w-text_w)/2", "(h-text_h)/2-50", dur=0.5, delay=0.1, px=80)
    chains.append(_dt(value, font, 120, accent, vx, vy, enable=ve))
    # Label
    lx, ly, le = slide_up("(w-text_w)/2", "(h-text_h)/2+60", dur=0.4, delay=0.3, px=40)
    chains.append(_dt(label, body, 26, primary, lx, ly, enable=le, shadow=False))
    # Detail
    if detail:
        dx, dy, de = fade_in("(w-text_w)/2", "(h-text_h)/2+100", delay=0.5)
        chains.append(_dt(detail, body, 18, primary, dx, dy, enable=de, shadow=False))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


def bar_chart(palette: dict, props: dict) -> dict:
    """Horizontal bar chart — items with values.
    Vox style: left-aligned bars with labels.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    secondary = palette.get("secondary", "#49a6d8")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    items = props.get("items", [{"label": "FIRST", "value": 82}, {"label": "SECOND", "value": 61}])
    label = props.get("label", "")

    chains = []
    if label:
        lx, ly, le = fade_in("100", "120", delay=0.0)
        chains.append(_dt(label, body, 20, accent, lx, ly, enable=le, shadow=False))

    for i, item in enumerate(items[:5]):
        delay = 0.2 + i * 0.2
        y_pos = str(200 + i * 120)
        bar_color = accent if i == 0 else secondary
        # Label
        lx, ly, le = slide_left("100", y_pos, dur=0.3, delay=delay, px=80)
        chains.append(_dt(item["label"], body, 20, primary, lx, ly, enable=le, shadow=False))
        # Bar
        max_w = f"w*0.6*{item['value']}/100"
        chains.append(bar_sweep_x(100, str(int(y_pos.replace("(", "").replace(")", "")) + 30 if "(" in y_pos else int(y_pos) + 30),
                     max_w, 6, bar_color, delay=delay + 0.1, dur=0.4))
        # Value
        vx, vy, ve = slide_right(f"100+{max_w}+20", y_pos, dur=0.3, delay=delay + 0.2, px=40)
        chains.append(_dt(str(item["value"]), font, 36, accent, vx, vy, enable=ve))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


def ranked_list(palette: dict, props: dict) -> dict:
    """Numbered list items building in.
    Vox style: each item slides in from left with number.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    items = props.get("items", ["ITEM ONE", "ITEM TWO", "ITEM THREE"])
    title = props.get("title", "")

    chains = []
    if title:
        tx, ty, te = fade_in("100", "140", delay=0.0)
        chains.append(_dt(title, font, 48, primary, tx, ty, enable=te))
        chains.append(bar_sweep_x(100, "200", 160, 3, accent, delay=0.1, dur=0.3))

    for i, item in enumerate(items[:5]):
        delay = 0.2 + i * 0.3
        y_pos = str(260 + i * 100)
        item_text = item.get("label", str(item)) if isinstance(item, dict) else str(item)
        # Number
        nx, ny, ne = scale_pop("100", y_pos, dur=0.3, delay=delay, px=40)
        chains.append(_dt(f"{i+1}.", font, 48, danger, nx, ny, enable=ne))
        # Text
        tx, ty, te = slide_left("180", y_pos, dur=0.4, delay=delay + 0.1, px=200)
        chains.append(_dt(item_text, body, 28, primary, tx, ty, enable=te, shadow=False))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


# ═══════════════════════════════════════════════════════════════════
# EVIDENCE & INVESTIGATION
# ═══════════════════════════════════════════════════════════════════

def evidence_callout(palette: dict, props: dict) -> dict:
    """Evidence stamp — investigative card with red bar.
    Vox style: left red bar, stamp label, main text with box.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    stamp = props.get("stamp", "EVIDENCE")
    text = props.get("text", "THE RECORDS DID NOT MATCH")
    detail = props.get("detail", "")

    chains = []
    # Left red vertical bar
    chains.append(bar_grow_y(60, "ih*0.1", 6, "ih*0.8", danger, delay=0.0, dur=0.3))
    # Stamp label
    sx, sy, se = fade_in("90", "200", delay=0.1)
    chains.append(_dt(stamp, body, 16, danger, sx, sy, enable=se, shadow=False))
    # Main text — left-aligned with box
    tx, ty, te = slide_left("90", "(h-text_h)/2-20", dur=0.5, delay=0.2, px=200)
    chains.append(_dt(text, font, 64, primary, tx, ty, enable=te,
                      box=True, box_color="black@0.65", box_pad=25))
    # Detail
    if detail:
        dx, dy, de = slide_left("90", "h*0.6", dur=0.4, delay=0.4, px=100)
        chains.append(_dt(detail, body, 20, primary, dx, dy, enable=de, shadow=False))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


def redaction_reveal(palette: dict, props: dict) -> dict:
    """Classified document — redacted text with reveal.
    Vox style: redaction bar slides away, text revealed.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    text = props.get("text", "THE DETAIL THAT CHANGED EVERYTHING")
    label = props.get("label", "RESTRICTED FILE")
    detail = props.get("detail", "")

    chains = []
    # Top danger bar
    chains.append(bar_sweep_x(0, 0, "iw", 3, danger, delay=0.0, dur=0.3))
    # Label
    lx, ly, le = fade_in("(w-text_w)/2", "200", delay=0.1)
    chains.append(_dt(label, body, 16, danger, lx, ly, enable=le, shadow=False))
    # Main text
    tx, ty, te = slide_up("(w-text_w)/2", "(h-text_h)/2-20", dur=0.5, delay=0.2, px=60)
    chains.append(_dt(text, font, 60, primary, tx, ty, enable=te))
    # Redaction bar that slides away (centered on screen)
    chains.append(overlay_rect(0, "ih*0.42", "iw", "ih*0.16", "black@0.92"))
    # Detail
    if detail:
        dx, dy, de = fade_in("(w-text_w)/2", "(h-text_h)/2+60", delay=0.5)
        chains.append(_dt(detail, body, 20, primary, dx, dy, enable=de, shadow=False))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


def case_file(palette: dict, props: dict) -> dict:
    """Case file breakdown — document style.
    Vox style: case number, title, detail, with accent borders.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    title = props.get("title", "THE MISSING WARNING")
    detail = props.get("detail", "CASE FILE 04")
    stamp = props.get("stamp", "CLASSIFIED")

    chains = []
    # Top border
    chains.append(bar_sweep_x(60, 140, "iw-120", 3, accent, delay=0.0, dur=0.3))
    # Case number / stamp
    sx, sy, se = fade_in("80", "170", delay=0.1)
    chains.append(_dt(detail, body, 14, danger, sx, sy, enable=se, shadow=False))
    # Title — large
    tx, ty, te = slide_left("80", "(h-text_h)/2-30", dur=0.5, delay=0.2, px=200)
    chains.append(_dt(title, font, 72, primary, tx, ty, enable=te))
    # Bottom border
    chains.append(bar_sweep_x(60, "ih*0.6", "iw-120", 3, accent, delay=0.3, dur=0.3))
    # Stamp
    stx, sty, ste = fade_in("80", "(h-text_h)/2+100", delay=0.5)
    chains.append(_dt(stamp, body, 16, danger, stx, sty, enable=ste, shadow=False))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


def source_citation(palette: dict, props: dict) -> dict:
    """Source attribution — clean, credible.
    Vox style: "SOURCE" label, text, source name.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "OpenSans-Bold.ttf")
    body_bold = props.get("_body_font", "Poppins-Bold.ttf")
    text = props.get("text", "A critical finding that changed the investigation.")
    source = props.get("source", "NATIONAL ARCHIVES")
    label = props.get("label", "SOURCE")

    chains = []
    # Left accent line
    chains.append(bar_grow_y(100, "ih*0.3", 3, "ih*0.4", accent, delay=0.0, dur=0.3))
    # "SOURCE" label
    lx, ly, le = fade_in("130", "h*0.35", delay=0.1)
    chains.append(_dt(label, body_bold, 14, accent, lx, ly, enable=le, shadow=False))
    # Text
    tx, ty, te = slide_left("130", "h*0.4", dur=0.4, delay=0.2, px=100)
    chains.append(_dt(text, body, 28, primary, tx, ty, enable=te, shadow=False))
    # Source name
    sx, sy, se = slide_left("130", "(h-text_h)/2+20", dur=0.3, delay=0.4, px=60)
    chains.append(_dt(f"— {source}", body_bold, 18, accent, sx, sy, enable=se, shadow=False))

    return {"chains": chains, "treatment": "slow_push_in", "cost": 1}


def stamp_mark(palette: dict, props: dict) -> dict:
    """Archive stamp / marking.
    Vox style: large stamp text with subtitle.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    text = props.get("text", "DECLASSIFIED")
    label = props.get("label", "TOP SECRET")

    chains = []
    chains.append(overlay_rect(0, "ih*0.35", "iw", "ih*0.35", "black@0.5"))
    # Stamp text — large, rotated feel via accent color
    tx, ty, te = scale_pop("(w-text_w)/2", "(h-text_h)/2-20", dur=0.5, delay=0.1, px=80)
    chains.append(_dt(text, font, 100, danger, tx, ty, enable=te))
    # Label below
    lx, ly, le = fade_in("(w-text_w)/2", "(h-text_h)/2+80", delay=0.4)
    chains.append(_dt(label, body, 20, primary, lx, ly, enable=le, shadow=False))
    # Top/bottom accent lines
    chains.append(bar_sweep_x("(iw-300)/2", "ih*0.35", 300, 3, danger, delay=0.2, dur=0.3))
    chains.append(bar_sweep_x("(iw-300)/2", "ih*0.65", 300, 3, danger, delay=0.3, dur=0.3))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


# ═══════════════════════════════════════════════════════════════════
# TIMELINE & DATES
# ═══════════════════════════════════════════════════════════════════

def date_reveal(palette: dict, props: dict) -> dict:
    """Date with kicker — historical timeline beat.
    Vox style: kicker above, massive date centered.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    kicker = props.get("kicker", "HISTORIC DATE")
    date = props.get("date", "JULY 20, 1969")

    chains = []
    chains.append(overlay_rect(0, "ih*0.3", "iw", "ih*0.45", "black@0.5"))
    # Kicker
    kx, ky, ke = slide_up("(w-text_w)/2", "340", dur=0.4, delay=0.1, px=60)
    chains.append(_dt(kicker, body, 18, accent, kx, ky, enable=ke, shadow=False))
    # Date — massive
    dx, dy, de = scale_pop("(w-text_w)/2", "(h-text_h)/2-20", dur=0.6, delay=0.2, px=100)
    chains.append(_dt(date, font, 110, primary, dx, dy, enable=de))
    # Accent underline
    chains.append(bar_static("(iw-200)/2", "620", 200, 4, accent))

    return {"chains": chains, "treatment": "slow_push_in", "cost": 2}


def calendar_rip(palette: dict, props: dict) -> dict:
    """Calendar-page reveal — date with kicker.
    Vox style: kicker + date with accent decorations.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    date = props.get("date", "APRIL 15")
    kicker = props.get("kicker", "THE NIGHT")
    event = props.get("event", "")

    chains = []
    # Left red bar
    chains.append(bar_grow_y(80, "ih*0.15", 4, "ih*0.7", danger, delay=0.0, dur=0.3))
    # Kicker
    kx, ky, ke = fade_in("120", "320", delay=0.1)
    chains.append(_dt(kicker, body, 16, danger, kx, ky, enable=ke, shadow=False))
    # Date — large
    dx, dy, de = slide_left("120", "(h-text_h)/2-30", dur=0.5, delay=0.2, px=200)
    chains.append(_dt(date, font, 120, primary, dx, dy, enable=de))
    # Event
    if event:
        ex, ey, ee = slide_left("120", "(h-text_h)/2+80", dur=0.4, delay=0.4, px=100)
        chains.append(_dt(event, body, 24, accent, ex, ey, enable=ee, shadow=False))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


def timeline_trace(palette: dict, props: dict) -> dict:
    """Timeline with events — horizontal timeline.
    Vox style: events appear left to right with dots.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    items = props.get("items", [
        {"label": "1973", "text": "Oil Crisis"},
        {"label": "1979", "text": "Revolution"},
        {"label": "1989", "text": "Wall Falls"},
    ])
    label = props.get("label", "KEY EVENTS")

    chains = []
    # Title
    tx, ty, te = fade_in("100", "160", delay=0.0)
    chains.append(_dt(label, body, 20, accent, tx, ty, enable=te, shadow=False))
    chains.append(bar_sweep_x(100, "195", 120, 3, accent, delay=0.1, dur=0.3))

    # Timeline line
    chains.append(bar_static(100, "540", "iw-200", 2, "white@0.3"))

    # Events
    spacing = min(400, (1920 - 200) // max(1, len(items)))
    for i, item in enumerate(items):
        delay = 0.3 + i * 0.3
        x_pos = str(150 + i * spacing)
        # Dot
        chains.append(bar_static(x_pos, "534", 12, 12, accent))
        # Year above
        yx, yy, ye = drop_in(x_pos, "480", dur=0.3, delay=delay, px=60)
        chains.append(_dt(item["label"], font, 36, accent, yx, yy, enable=ye))
        # Event below
        ex, ey, ee = slide_up(x_pos, "570", dur=0.3, delay=delay + 0.1, px=40)
        chains.append(_dt(item.get("text", item.get("value", "")), body, 18, primary, ex, ey, enable=ee, shadow=False))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


# ═══════════════════════════════════════════════════════════════════
# QUOTES & TESTIMONY
# ═══════════════════════════════════════════════════════════════════

def quote_card(palette: dict, props: dict) -> dict:
    """Elegant quote with attribution.
    Vox style: large quotation mark, quote text, attribution dash.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "OpenSans-Bold.ttf")
    quote = props.get("quote", "One small step for a man.")
    attribution = props.get("attribution", "NEIL ARMSTRONG")

    chains = []
    # Top accent line
    chains.append(bar_sweep_x(0, 0, "iw", 4, accent, delay=0.0, dur=0.3))
    # Large quotation mark
    chains.append(_dt('\\"', font, 180, accent, "120", "100", shadow=False))
    # Quote text
    qx, qy, qe = slide_up("120", "(h-text_h)/2-20", dur=0.5, delay=0.2, px=80)
    chains.append(_dt(quote, font, 48, primary, qx, qy, enable=qe, shadow=False))
    # Attribution
    ax, ay, ae = slide_left("120", "(h-text_h)/2+80", dur=0.3, delay=0.5, px=60)
    chains.append(_dt(f"— {attribution}", body, 20, accent, ax, ay, enable=ae, shadow=False))
    # Bottom accent
    chains.append(bar_sweep_x(0, "ih-3", "iw", 3, accent, delay=0.3, dur=0.3))

    return {"chains": chains, "treatment": "slow_push_in", "cost": 1}


def witness_quote(palette: dict, props: dict) -> dict:
    """Witness testimony — investigative quote.
    Vox style: quote with attribution, evidence style.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    body_reg = props.get("_body_font", "OpenSans-Bold.ttf")
    quote = props.get("quote", "I saw the documents myself.")
    attribution = props.get("attribution", "FORMER ANALYST")

    chains = []
    # Left red bar
    chains.append(bar_grow_y(80, "ih*0.2", 4, "ih*0.6", danger, delay=0.0, dur=0.3))
    # "TESTIMONY" label
    lx, ly, le = fade_in("110", "240", delay=0.1)
    chains.append(_dt("TESTIMONY", body, 14, danger, lx, ly, enable=le, shadow=False))
    # Quote
    qx, qy, qe = slide_left("110", "(h-text_h)/2-30", dur=0.5, delay=0.2, px=200)
    chains.append(_dt(quote, font, 44, primary, qx, qy, enable=qe, shadow=False))
    # Attribution
    ax, ay, ae = slide_left("110", "(h-text_h)/2+60", dur=0.3, delay=0.5, px=60)
    chains.append(_dt(f"— {attribution}", body, 18, accent, ax, ay, enable=ae, shadow=False))

    return {"chains": chains, "treatment": "slow_push_in", "cost": 1}


# ═══════════════════════════════════════════════════════════════════
# COMPARISON & RELATIONSHIP
# ═══════════════════════════════════════════════════════════════════

def cause_effect(palette: dict, props: dict) -> dict:
    """Cause and effect diagram.
    Vox style: left cause → arrow → right effect.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    cause = props.get("cause", "DEREGULATION")
    effect = props.get("effect", "MARKET CRASH")
    label = props.get("label", "CAUSE → EFFECT")

    chains = []
    chains.append(overlay_rect(0, "ih*0.35", "iw", "ih*0.35", "black@0.5"))
    # Label
    lx, ly, le = fade_in("(w-text_w)/2", "320", delay=0.0)
    chains.append(_dt(label, body, 16, accent, lx, ly, enable=le, shadow=False))
    # Cause — left
    cx, cy, ce = slide_right("w*0.25-(text_w/2)", "(h-text_h)/2-20", dur=0.5, delay=0.1, px=150)
    chains.append(_dt(cause, font, 64, danger, cx, cy, enable=ce))
    # Arrow / divider
    chains.append(bar_static("(iw-4)/2", "ih*0.4", 4, 80, accent))
    # Effect — right
    ex, ey, ee = slide_left("w*0.75-(text_w/2)", "(h-text_h)/2-20", dur=0.5, delay=0.3, px=150)
    chains.append(_dt(effect, font, 64, primary, ex, ey, enable=ee))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


def before_after(palette: dict, props: dict) -> dict:
    """Before/after comparison.
    Vox style: two sides with divider.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    before_label = props.get("before_label", "BEFORE")
    before_value = props.get("before_value", "$0")
    after_label = props.get("after_label", "AFTER")
    after_value = props.get("after_value", "$1M")
    label = props.get("label", "TRANSFORMATION")

    chains = []
    # Label top
    lx, ly, le = fade_in("(w-text_w)/2", "200", delay=0.0)
    chains.append(_dt(label, body, 18, accent, lx, ly, enable=le, shadow=False))
    # Center divider
    chains.append(bar_static("(iw-2)/2", "260", 2, "ih*0.4", "white@0.3"))
    # Before — left
    blx, bly, ble = slide_right("w*0.25-(text_w/2)", "320", dur=0.4, delay=0.1, px=100)
    chains.append(_dt(before_label, body, 20, danger, blx, bly, enable=ble, shadow=False))
    bvx, bvy, bve = scale_pop("w*0.25-(text_w/2)", "(h-text_h)/2-20", dur=0.5, delay=0.2, px=80)
    chains.append(_dt(before_value, font, 80, primary, bvx, bvy, enable=bve))
    # After — right
    alx, aly, ale = slide_left("w*0.75-(text_w/2)", "320", dur=0.4, delay=0.3, px=100)
    chains.append(_dt(after_label, body, 20, accent, alx, aly, enable=ale, shadow=False))
    avx, avy, ave = scale_pop("w*0.75-(text_w/2)", "(h-text_h)/2-20", dur=0.5, delay=0.4, px=80)
    chains.append(_dt(after_value, font, 80, accent, avx, avy, enable=ave))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


def split_compare(palette: dict, props: dict) -> dict:
    """Split screen comparison — two values side by side.
    Vox style: left value vs right value with labels.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    left_label = props.get("left_label", "USA")
    right_label = props.get("right_label", "USSR")
    left_value = props.get("left_value", "12")
    right_value = props.get("right_value", "1")
    label = props.get("label", "MOON LANDINGS")

    chains = []
    chains.append(overlay_rect(0, "ih*0.35", "iw", "ih*0.35", "black@0.5"))
    # Label
    lx, ly, le = fade_in("(w-text_w)/2", "320", delay=0.0)
    chains.append(_dt(label, body, 18, accent, lx, ly, enable=le, shadow=False))
    # Left value
    lvx, lvy, lve = scale_pop("w*0.3-(text_w/2)", "(h-text_h)/2-20", dur=0.5, delay=0.1, px=80)
    chains.append(_dt(left_value, font, 120, primary, lvx, lvy, enable=lve))
    # Left label
    llx, lly, lle = fade_in("w*0.3-(text_w/2)", "(h-text_h)/2+80", delay=0.3)
    chains.append(_dt(left_label, body, 22, accent, llx, lly, enable=lle, shadow=False))
    # Right value
    rvx, rvy, rve = scale_pop("w*0.7-(text_w/2)", "(h-text_h)/2-20", dur=0.5, delay=0.2, px=80)
    chains.append(_dt(right_value, font, 120, accent, rvx, rvy, enable=rve))
    # Right label
    rlx, rly, rle = fade_in("w*0.7-(text_w/2)", "(h-text_h)/2+80", delay=0.4)
    chains.append(_dt(right_label, body, 22, accent, rlx, rly, enable=rle, shadow=False))
    # Divider
    chains.append(bar_static("(iw-2)/2", "360", 2, "ih*0.3", "white@0.3"))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


# ═══════════════════════════════════════════════════════════════════
# LOCATION & MAP
# ═══════════════════════════════════════════════════════════════════

def location_label(palette: dict, props: dict) -> dict:
    """Location with detail — lower third style.
    Vox style: location name + subtitle, left-aligned.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    body_reg = props.get("_body_font", "OpenSans-Bold.ttf")
    location = str(props.get("location") or "").strip()
    detail = str(props.get("detail") or "").strip()
    if not location:
        return {"chains": [], "treatment": "static_hold", "cost": 0}

    chains = []
    # Location
    lx, ly, le = slide_left("80", "h-70", dur=0.4, delay=0.1, px=100)
    chains.append(_dt(location, body, 30, primary, lx, ly, enable=le, shadow=False))
    # Detail — only show if short tag (<= 3 words)
    if detail and len(detail.split()) <= 3 and len(detail) <= 24:
        dx, dy, de = slide_left("80", "h-38", dur=0.3, delay=0.3, px=60)
        chains.append(_dt(detail, body_reg, 18, accent, dx, dy, enable=de, shadow=False))

    return {"chains": chains, "treatment": "static_hold", "cost": 1}


def map_route(palette: dict, props: dict) -> dict:
    """Route progress tracker.
    Vox style: from → to with accent dots.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    secondary = palette.get("secondary", "#49a6d8")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    from_city = str(props.get("from") or "").strip()
    to_city = str(props.get("to") or "").strip()
    label = str(props.get("label") or "").strip()
    if not label and not to_city:
        return {"chains": [], "treatment": "static_hold", "cost": 0}

    chains = []
    chains.append(overlay_rect(0, "ih*0.35", "iw", "ih*0.35", "black@0.5"))
    # Label
    lx, ly, le = fade_in("(w-text_w)/2", "320", delay=0.0)
    chains.append(_dt(label, body, 16, accent, lx, ly, enable=le, shadow=False))
    # Route line
    chains.append(bar_sweep_x("iw*0.2", "ih*0.5", "iw*0.6", 3, accent, delay=0.2, dur=0.5))
    # From dot
    chains.append(bar_static("iw*0.2-6", "ih*0.48", 12, 12, secondary))
    # To dot
    chains.append(bar_static("iw*0.8-6", "ih*0.48", 12, 12, accent))
    # From text
    fx, fy, fe = slide_up("w*0.2-(text_w/2)", "(h-text_h)/2+30", dur=0.3, delay=0.3, px=40)
    chains.append(_dt(from_city, body, 22, primary, fx, fy, enable=fe, shadow=False))
    # To text
    tx, ty, te = slide_up("w*0.8-(text_w/2)", "(h-text_h)/2+30", dur=0.3, delay=0.5, px=40)
    chains.append(_dt(to_city, body, 22, accent, tx, ty, enable=te, shadow=False))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


# ═══════════════════════════════════════════════════════════════════
# CLOSING
# ═══════════════════════════════════════════════════════════════════

def end_card(palette: dict, props: dict) -> dict:
    """Closing card — title + subtitle + accent bars.
    Vox style: centered, dramatic, with CTA.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    title = props.get("title", "FOLLOW THE STORY")
    subtitle = props.get("subtitle", "")

    chains = []
    chains.append(overlay_rect(0, "ih*0.3", "iw", "ih*0.45", "black@0.55"))
    # Top accent bar
    chains.append(bar_sweep_x(0, 0, "iw", 6, accent, delay=0.0, dur=0.4))
    # Title
    tx, ty, te = scale_pop("(w-text_w)/2", "(h-text_h)/2-30", dur=0.5, delay=0.1, px=80)
    chains.append(_dt(title, font, 80, primary, tx, ty, enable=te))
    # Accent underline
    chains.append(bar_static("(iw-200)/2", "ih*0.6", 200, 4, accent))
    # Subtitle
    if subtitle:
        sx, sy, se = slide_up("(w-text_w)/2", "(h-text_h)/2+100", dur=0.4, delay=0.3, px=40)
        chains.append(_dt(subtitle, body, 24, accent, sx, sy, enable=se, shadow=False))
    # Bottom accent bar
    chains.append(bar_sweep_x(0, "ih-6", "iw", 6, accent, delay=0.2, dur=0.4))

    return {"chains": chains, "treatment": "slow_push_in", "cost": 2}


def cta_card(palette: dict, props: dict) -> dict:
    """Call to action — subscribe, follow, etc.
    Vox style: clean CTA with accent.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    title = props.get("title", "SUBSCRIBE")
    subtitle = props.get("subtitle", "For more stories like this")

    chains = []
    chains.append(overlay_rect(0, "ih*0.35", "iw", "ih*0.4", "black@0.55"))
    # Title
    tx, ty, te = scale_pop("(w-text_w)/2", "(h-text_h)/2-30", dur=0.5, delay=0.1, px=80)
    chains.append(_dt(title, font, 96, accent, tx, ty, enable=te))
    # Subtitle
    sx, sy, se = slide_up("(w-text_w)/2", "(h-text_h)/2+60", dur=0.4, delay=0.3, px=40)
    chains.append(_dt(subtitle, body, 24, primary, sx, sy, enable=se, shadow=True))

    return {"chains": chains, "treatment": "slow_push_in", "cost": 2}


# ═══════════════════════════════════════════════════════════════════
# DOCUMENT
# ═══════════════════════════════════════════════════════════════════

def field_caption(palette: dict, props: dict) -> dict:
    """Editorial field caption — location + date.
    Vox style: lower-third style with accent underline.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    body_reg = props.get("_body_font", "OpenSans-Bold.ttf")
    title = props.get("title", "NORTH ATLANTIC")
    subtitle = props.get("subtitle", "APRIL 1912")

    chains = []
    # Bottom dark overlay
    # overlay_rect removed: padded box handled by drawtext box=1
    # Title
    tx, ty, te = slide_left("80", "h-75", dur=0.3, delay=0.1, px=80)
    chains.append(_dt(title, body, 28, primary, tx, ty, enable=te, shadow=False))
    # Subtitle — only if short tag
    if subtitle and len(subtitle.split()) <= 4 and len(subtitle) <= 26:
        sx, sy, se = slide_left("80", "h-42", dur=0.3, delay=0.2, px=60)
        chains.append(_dt(subtitle, body_reg, 18, accent, sx, sy, enable=se, shadow=False))

    return {"chains": chains, "treatment": "static_hold", "cost": 1}


def exhibit_label(palette: dict, props: dict) -> dict:
    """Museum exhibit label — archive style.
    Vox style: bordered label with title and subtitle.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    body_reg = props.get("_body_font", "OpenSans-Bold.ttf")
    title = props.get("title", "RMS TITANIC")
    subtitle = props.get("subtitle", "ARCHIVE OBJECT")

    chains = []
    # Title
    tx, ty, te = fade_in("(w-text_w)/2", "(h-text_h)/2-16", delay=0.2)
    chains.append(_dt(title, body, 42, primary, tx, ty, enable=te, shadow=True))
    # Subtitle — only if short tag (<= 3 words), never long narration
    if subtitle and len(subtitle.split()) <= 3 and len(subtitle) <= 24:
        sx, sy, se = fade_in("(w-text_w)/2", "(h-text_h)/2+32", delay=0.4)
        chains.append(_dt(subtitle, body_reg, 18, accent, sx, sy, enable=se, shadow=True))

    return {"chains": chains, "treatment": "static_hold", "cost": 1}


def newsprint_reveal(palette: dict, props: dict) -> dict:
    """Newsprint headline reveal.
    Vox style: headline with deck, press style.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    body_reg = props.get("_body_font", "OpenSans-Bold.ttf")
    headline = props.get("headline", "THE WARNING WAS ALREADY THERE")
    deck = props.get("deck", "ARCHIVE REPORT")
    detail = props.get("detail", "")

    chains = []
    chains.append(overlay_rect(0, "ih*0.3", "iw", "ih*0.45", "black@0.5"))
    # Deck
    dx, dy, de = fade_in("(w-text_w)/2", "320", delay=0.0)
    chains.append(_dt(deck, body, 16, danger, dx, dy, enable=de, shadow=False))
    # Headline — massive
    hx, hy, he = slide_up("(w-text_w)/2", "(h-text_h)/2-20", dur=0.5, delay=0.1, px=80)
    chains.append(_dt(headline, font, 72, primary, hx, hy, enable=he))
    # Accent underline
    chains.append(bar_static("(iw-240)/2", "620", 240, 4, accent))
    # Detail
    if detail:
        dtx, dty, dte = fade_in("(w-text_w)/2", "660", delay=0.4)
        chains.append(_dt(detail, body_reg, 18, primary, dtx, dty, enable=dte, shadow=False))

    return {"chains": chains, "treatment": "slow_push_in", "cost": 2}


def countdown_clock(palette: dict, props: dict) -> dict:
    """Countdown / time pressure.
    Vox style: large countdown number with label.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    value = props.get("value", "T-30")
    label = props.get("label", "SECONDS TO IMPACT")

    chains = []
    chains.append(overlay_rect(0, "ih*0.35", "iw", "ih*0.4", "black@0.55"))
    # Value — massive, danger color
    vx, vy, ve = scale_pop("(w-text_w)/2", "(h-text_h)/2-30", dur=0.5, delay=0.1, px=80)
    chains.append(_dt(value, font, 140, danger, vx, vy, enable=ve))
    # Label
    lx, ly, le = slide_up("(w-text_w)/2", "(h-text_h)/2+90", dur=0.4, delay=0.3, px=40)
    chains.append(_dt(label, body, 22, primary, lx, ly, enable=le, shadow=False))
    # Top/bottom danger bars
    chains.append(bar_sweep_x(0, 0, "iw", 4, danger, delay=0.0, dur=0.3))
    chains.append(bar_sweep_x(0, "ih-4", "iw", 4, danger, delay=0.1, dur=0.3))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


def signal_scan(palette: dict, props: dict) -> dict:
    """Radio signal / scan treatment.
    Vox style: terminal-like with scan line.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = props.get("primary", "#4ade80")  # green terminal
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "OpenSans-Bold.ttf")
    message = props.get("message", "DISTRESS SIGNAL DETECTED")
    label = props.get("label", "RADIO INTERCEPT")

    chains = []
    chains.append(overlay_rect(0, 0, "iw", "ih", "black@0.7"))
    # Scan line
    chains.append(bar_sweep_x(0, "ih*0.3", "iw", 2, accent, delay=0.0, dur=0.5))
    # Label
    lx, ly, le = fade_in("100", "300", delay=0.1)
    chains.append(_dt(label, body, 14, accent, lx, ly, enable=le, shadow=False))
    # Message
    mx, my, me = slide_left("100", "(h-text_h)/2-20", dur=0.5, delay=0.2, px=200)
    chains.append(_dt(message, font, 48, primary, mx, my, enable=me))
    # Bottom scan line
    chains.append(bar_sweep_x(0, "ih*0.7", "iw", 2, accent, delay=0.3, dur=0.5))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


def index_card(palette: dict, props: dict) -> dict:
    """Archive index card.
    Vox style: bordered card with title and detail.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    body_reg = props.get("_body_font", "OpenSans-Bold.ttf")
    title = props.get("title", "WIRELESS WARNING")
    detail = props.get("detail", "Filed before the collision.")

    chains = []
    # Card border
    chains.append(bar_static("iw*0.2", "ih*0.3", "iw*0.6", "ih*0.4", "white@0.08"))
    # Top accent line
    chains.append(bar_sweep_x("iw*0.2", "ih*0.3", "iw*0.6", 3, accent, delay=0.0, dur=0.3))
    # Title
    tx, ty, te = fade_in("(w-text_w)/2", "(h-text_h)/2-30", delay=0.2)
    chains.append(_dt(title, font, 56, primary, tx, ty, enable=te))
    # Detail
    dx, dy, de = fade_in("(w-text_w)/2", "h*0.55", delay=0.4)
    chains.append(_dt(detail, body_reg, 20, accent, dx, dy, enable=de, shadow=False))
    # Bottom accent
    chains.append(bar_sweep_x("iw*0.2", "ih*0.7-3", "iw*0.6", 3, accent, delay=0.3, dur=0.3))

    return {"chains": chains, "treatment": "static_hold", "cost": 1}


def profile_dossier(palette: dict, props: dict) -> dict:
    """Profile dossier — person identification.
    Vox style: name + title with accent elements.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    body_reg = props.get("_body_font", "OpenSans-Bold.ttf")
    title = props.get("title", "JAMES DONOVAN")
    subtitle = props.get("subtitle", "NEGOTIATOR")
    detail = props.get("detail", "")

    chains = []
    chains.append(overlay_rect(0, "ih*0.4", "iw", "ih*0.6", "black@0.6"))
    # Left accent bar
    chains.append(bar_grow_y(80, "ih*0.45", 6, "ih*0.4", accent, delay=0.0, dur=0.3))
    # Title — large name
    tx, ty, te = slide_left("110", "(h-text_h)/2-20", dur=0.5, delay=0.1, px=200)
    chains.append(_dt(title, font, 80, primary, tx, ty, enable=te))
    # Subtitle
    sx, sy, se = slide_left("110", "h*0.6", dur=0.4, delay=0.3, px=100)
    chains.append(_dt(subtitle, body, 28, accent, sx, sy, enable=se, shadow=False))
    # Detail
    if detail:
        dx, dy, de = slide_left("110", "h*0.65", dur=0.3, delay=0.5, px=60)
        chains.append(_dt(detail, body_reg, 18, primary, dx, dy, enable=de, shadow=False))

    return {"chains": chains, "treatment": "slow_push_in", "cost": 2}


# ═══════════════════════════════════════════════════════════════════
# CINEMATIC / NOAH MORRIS STYLE — Documentary reveals, bold text
# ═══════════════════════════════════════════════════════════════════

def watercolor_reveal(palette: dict, props: dict) -> dict:
    """Watercolor / painted brush-stroke border effect with bold serif text.
    Style: painterly white edges framing the image, serif text overlay.
    Noah Morris / scrapbook documentary aesthetic.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    bg = palette.get("background", "#f5f0e8")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "OpenSans-Bold.ttf")
    title = props.get("title", "367 Addison Avenue")
    subtitle = props.get("subtitle", "Palo Alto, California")

    chains = []
    # White painterly border — top
    chains.append(overlay_rect(0, 0, "iw", "ih*0.12", bg))
    # White painterly border — bottom
    chains.append(overlay_rect(0, "ih*0.88", "iw", "ih*0.12", bg))
    # White painterly border — left
    chains.append(overlay_rect(0, 0, "iw*0.08", "ih", bg))
    # White painterly border — right
    chains.append(overlay_rect("iw*0.92", 0, "iw*0.08", "ih", bg))
    # Inner shadow/gradient — top
    chains.append(overlay_rect(0, "ih*0.12", "iw", "ih*0.06", "black@0.15"))
    # Inner shadow/gradient — bottom
    chains.append(overlay_rect(0, "ih*0.82", "iw", "ih*0.06", "black@0.15"))
    # Title — bold serif, centered
    tx, ty, te = slide_up("(w-text_w)/2", "(h-text_h)/2-20", dur=0.6, delay=0.2, px=80)
    chains.append(_dt(title, font, 72, primary, tx, ty, enable=te))
    # Subtitle — smaller, below
    sx, sy, se = slide_up("(w-text_w)/2", "(h-text_h)/2+60", dur=0.4, delay=0.5, px=40)
    chains.append(_dt(subtitle, body, 24, accent, sx, sy, enable=se, shadow=False))
    # Thin accent underline
    chains.append(bar_static("(iw-180)/2", "ih*0.58", 180, 2, accent))

    return {"chains": chains, "treatment": "slow_push_in", "cost": 2}


def torn_paper_reveal(palette: dict, props: dict) -> dict:
    """Torn paper / grunge edge reveal with serif text.
    Style: irregular dark edges at top and bottom, documentary text.
    Inspired by VidRush torn-paper transitions.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    bg = palette.get("background", "#1a1a2e")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "OpenSans-Bold.ttf")
    text = props.get("text", "It's 1930, and two freshmen arrive at Stanford University in Palo Alto.")
    highlight_word = props.get("highlight", "Stanford")

    chains = []
    # Torn paper top edge — multiple uneven bars
    chains.append(overlay_rect(0, 0, "iw", "ih*0.04", bg))
    chains.append(overlay_rect(0, "ih*0.04", "iw*0.3", "ih*0.02", bg))
    chains.append(overlay_rect("iw*0.4", "ih*0.04", "iw*0.6", "ih*0.015", bg))
    chains.append(overlay_rect(0, "ih*0.06", "iw*0.15", "ih*0.025", bg))
    chains.append(overlay_rect("iw*0.7", "ih*0.06", "iw*0.3", "ih*0.02", bg))
    # Torn paper bottom edge — multiple uneven bars
    chains.append(overlay_rect(0, "ih*0.93", "iw", "ih*0.07", bg))
    chains.append(overlay_rect(0, "ih*0.91", "iw*0.25", "ih*0.03", bg))
    chains.append(overlay_rect("iw*0.5", "ih*0.91", "iw*0.5", "ih*0.02", bg))
    chains.append(overlay_rect(0, "ih*0.89", "iw*0.6", "ih*0.025", bg))
    # Dark overlay for text readability
    chains.append(overlay_rect(0, "ih*0.35", "iw", "ih*0.35", "black@0.55"))
    # Main text — serif, left-aligned
    tx, ty, te = slide_left("120", "(h-text_h)/2", dur=0.6, delay=0.2, px=200)
    chains.append(_dt(text, font, 44, primary, tx, ty, enable=te, shadow=True))

    return {"chains": chains, "treatment": "slow_push_in", "cost": 2}


def bold_name_banner(palette: dict, props: dict) -> dict:
    """Bold name on colored banner — documentary name reveal.
    Style: portrait-style name card with bold red/colored banner.
    Noah Morris / classic documentary name reveal.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    bg = palette.get("background", "#1a1a2e")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    name = _truncate_text(props.get("name", props.get("title", "Frederick Terman")), 40)
    subtitle = _truncate_text(props.get("subtitle", ""), 50)

    chains = []
    chains.append(overlay_rect(0, "ih*0.65", "iw", "ih*0.35", "black@0.7"))
    name_size = int(_fit_fontsize(name, "iw*0.5", 64, font))
    name_w, name_h = _font_metrics(name, font, name_size)
    horizontal_pad = max(24, int(name_size * 0.36))
    vertical_pad = max(12, int(name_size * 0.18))
    banner_w = min(1020, name_w + horizontal_pad * 2)
    banner_h = min(190, name_h + vertical_pad * 2)
    banner_x, banner_y, banner_enable = slide_left(
        str(110 - horizontal_pad),
        f"ih*0.76-{vertical_pad}",
        dur=0.5,
        delay=0.2,
        px=200,
    )
    chains.append(
        f"drawbox=x={banner_x}:y={banner_y}:w={banner_w}:h={banner_h}:"
        f"color={_color(danger)}:t=fill:{banner_enable}"
    )
    tx, ty, te = slide_left("110", "h*0.76", dur=0.5, delay=0.2, px=200)
    chains.append(_dt(name, font, 64, primary, tx, ty, enable=te, fit_width="iw*0.5", max_chars=40))
    # Subtitle — below banner
    if subtitle:
        sx, sy, se = slide_left("110", "h*0.92", dur=0.3, delay=0.5, px=60)
        chains.append(_dt(subtitle, body, 20, accent, sx, sy, enable=se, shadow=False, fit_width="iw*0.5", max_chars=50))
    # Top accent bar
    chains.append(bar_sweep_x(0, 0, "iw", 3, accent, delay=0.0, dur=0.3))

    return {"chains": chains, "treatment": "slow_push_in", "cost": 2}


def location_title_card(palette: dict, props: dict) -> dict:
    """Location title card — address with decorative border.
    Style: centered text with thin border frame, serif font.
    Inspired by Noah Morris location reveal cards.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    bg = palette.get("background", "#f5f0e8")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "OpenSans-Bold.ttf")
    title = props.get("title", "367 Addison Avenue")
    subtitle = props.get("subtitle", "Palo Alto, California")

    chains = []
    # Dark overlay
    chains.append(overlay_rect(0, 0, "iw", "ih", "black@0.4"))
    # Decorative border frame — thin rectangle
    chains.append(bar_static("iw*0.15", "ih*0.2", "iw*0.7", "ih*0.6", "white@0.06"))
    # Top border line
    chains.append(bar_sweep_x("iw*0.15", "ih*0.2", "iw*0.7", 2, accent, delay=0.0, dur=0.4))
    # Bottom border line
    chains.append(bar_sweep_x("iw*0.15", "ih*0.8-2", "iw*0.7", 2, accent, delay=0.2, dur=0.4))
    # Title — centered, large
    tx, ty, te = scale_pop("(w-text_w)/2", "(h-text_h)/2-30", dur=0.6, delay=0.1, px=80)
    chains.append(_dt(title, font, 72, primary, tx, ty, enable=te))
    # Subtitle
    sx, sy, se = slide_up("(w-text_w)/2", "(h-text_h)/2+50", dur=0.4, delay=0.4, px=40)
    chains.append(_dt(subtitle, body, 24, accent, sx, sy, enable=se, shadow=False))
    # Thin divider between title and subtitle (positioned below center)
    chains.append(bar_static("(iw-120)/2", "ih*0.52", 120, 2, accent))

    return {"chains": chains, "treatment": "slow_push_in", "cost": 2}


def retro_tech_overlay(palette: dict, props: dict) -> dict:
    """Retro tech / 90s computer overlay with grid.
    Style: grid lines, date label, bold serif text on dark background.
    Inspired by VidRush retro-tech documentary style.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    date_label = props.get("date", "1998")
    title = props.get("title", "most visited website")
    subtitle = props.get("subtitle", "")

    chains = []
    # Dark overlay
    chains.append(overlay_rect(0, 0, "iw", "ih", "black@0.5"))
    # Grid overlay — horizontal lines
    for i in range(1, 8):
        y_pos = f"ih*{i/8:.2f}"
        chains.append(bar_static(0, y_pos, "iw", 1, "white@0.06"))
    # Grid overlay — vertical lines
    for i in range(1, 12):
        x_pos = f"iw*{i/12:.2f}"
        chains.append(bar_static(x_pos, 0, 1, "ih", "white@0.06"))
    # Date label — red badge top-left
    chains.append(overlay_rect(60, 80, 120, 40, danger))
    dx, dy, de = fade_in("70", "85", delay=0.1)
    chains.append(_dt(date_label, body, 20, primary, dx, dy, enable=de, shadow=False))
    # Title — bold serif, bottom-left
    tx, ty, te = slide_left("80", "h*0.7", dur=0.5, delay=0.2, px=200)
    chains.append(_dt(title, font, 56, primary, tx, ty, enable=te))
    # Subtitle
    if subtitle:
        sx, sy, se = slide_left("80", "h*0.8", dur=0.3, delay=0.5, px=60)
        chains.append(_dt(subtitle, body, 20, accent, sx, sy, enable=se, shadow=False))
    # Bottom accent bar
    chains.append(bar_sweep_x(0, "ih-4", "iw", 4, accent, delay=0.3, dur=0.3))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


def vintage_film_card(palette: dict, props: dict) -> dict:
    """Vintage film / sepia-toned title card with ornamental borders.
    Style: classic documentary title with decorative corner accents.
    """
    accent = palette.get("accent", "#c9a96e")
    primary = palette.get("primary", "#f5f0e1")
    bg = palette.get("background", "#2a1f14")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "OpenSans-Bold.ttf")
    title = props.get("title", "THE GOLDEN AGE")
    subtitle = props.get("subtitle", "")

    chains = []
    # Sepia-toned dark overlay
    chains.append(overlay_rect(0, 0, "iw", "ih", "black@0.35"))
    # Decorative corner accents — top-left
    chains.append(bar_static(60, 60, 80, 3, accent))
    chains.append(bar_static(60, 60, 3, 80, accent))
    # Top-right
    chains.append(bar_static("iw-140", 60, 80, 3, accent))
    chains.append(bar_static("iw-63", 60, 3, 80, accent))
    # Bottom-left
    chains.append(bar_static(60, "ih-63", 80, 3, accent))
    chains.append(bar_static(60, "ih-140", 3, 80, accent))
    # Bottom-right
    chains.append(bar_static("iw-140", "ih-63", 80, 3, accent))
    chains.append(bar_static("iw-63", "ih-140", 3, 80, accent))
    # Title — centered, large serif
    tx, ty, te = scale_pop("(w-text_w)/2", "(h-text_h)/2-30", dur=0.6, delay=0.2, px=80)
    chains.append(_dt(title, font, 80, primary, tx, ty, enable=te))
    # Ornamental divider — two lines with diamond (positioned below center)
    chains.append(bar_static("(iw-300)/2", "ih*0.52", 130, 2, accent))
    chains.append(bar_static("(iw+300)/2-130", "ih*0.52", 130, 2, accent))
    # Small diamond center
    chains.append(bar_static("(iw-8)/2", "ih*0.51", 8, 8, accent))
    # Subtitle
    sx, sy, se = slide_up("(w-text_w)/2", "(h-text_h)/2+70", dur=0.4, delay=0.5, px=30)
    chains.append(_dt(subtitle, body, 22, accent, sx, sy, enable=se, shadow=False))

    return {"chains": chains, "treatment": "slow_push_in", "cost": 2}


def dramatic_portrait(palette: dict, props: dict) -> dict:
    """Dramatic portrait reveal — name + title with dark cinematic overlay.
    Style: dark vignette, large name, subtitle.
    Classic documentary person-introduction card.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    body_reg = props.get("_body_font", "OpenSans-Bold.ttf")
    name = props.get("name", props.get("title", "JAMES DONOVAN"))
    subtitle = props.get("subtitle", "NEGOTIATOR")
    detail = props.get("detail", "")

    chains = []
    # Heavy dark overlay — portrait vignette
    chains.append(overlay_rect(0, 0, "iw", "ih", "black@0.55"))
    # Left accent line
    chains.append(bar_grow_y(100, "ih*0.3", 4, "ih*0.4", accent, delay=0.0, dur=0.3))
    # Name — large serif
    tx, ty, te = slide_left("130", "(h-text_h)/2-40", dur=0.6, delay=0.1, px=250)
    chains.append(_dt(name, font, 88, primary, tx, ty, enable=te))
    # Subtitle — smaller, accent color
    sx, sy, se = slide_left("130", "(h-text_h)/2+60", dur=0.4, delay=0.4, px=100)
    chains.append(_dt(subtitle, body, 28, accent, sx, sy, enable=se, shadow=False))
    # Detail
    if detail:
        dx, dy, de = slide_left("130", "(h-text_h)/2+100", dur=0.3, delay=0.6, px=60)
        chains.append(_dt(detail, body_reg, 18, primary, dx, dy, enable=de, shadow=False))
    # Bottom accent bar
    chains.append(bar_sweep_x(0, "ih-3", "iw", 3, accent, delay=0.2, dur=0.3))

    return {"chains": chains, "treatment": "slow_push_in", "cost": 2}


def chapter_title(palette: dict, props: dict) -> dict:
    """Chapter title card — large number + title with divider.
    Style: chapter number above, title below, clean and bold.
    Classic documentary chapter-break style.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    number = props.get("number", "01")
    title = props.get("title", "THE BEGINNING")
    subtitle = props.get("subtitle", "")

    chains = []
    chains.append(overlay_rect(0, 0, "iw", "ih", "black@0.4"))
    # Chapter number — large, faded
    nx, ny, ne = scale_pop("(w-text_w)/2", "h*0.3", dur=0.5, delay=0.0, px=80)
    chains.append(_dt(number, font, 140, accent, nx, ny, enable=ne))
    # Title — bold, centered
    tx, ty, te = slide_up("(w-text_w)/2", "h*0.48", dur=0.5, delay=0.3, px=60)
    chains.append(_dt(title, body, 64, primary, tx, ty, enable=te))
    # Subtitle — only show if short tag (<= 4 words), never long narration
    if subtitle and len(subtitle.split()) <= 4 and len(subtitle) <= 30:
        sx, sy, se = slide_up("(w-text_w)/2", "h*0.60", dur=0.3, delay=0.6, px=30)
        chains.append(_dt(subtitle, body, 22, accent, sx, sy, enable=se, shadow=False))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


def diagonal_split(palette: dict, props: dict) -> dict:
    """Diagonal split-screen effect — two-tone diagonal.
    Style: diagonal color split with text on one side.
    Inspired by CapCut diagonal transitions.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    bg = palette.get("background", "#1a1a2e")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    title = props.get("title", "THE TURNING POINT")
    subtitle = props.get("subtitle", "")

    chains = []
    # Diagonal dark overlay — approximated with overlapping rectangles
    chains.append(overlay_rect(0, 0, "iw", "ih*0.15", "black@0.2"))
    chains.append(overlay_rect(0, "ih*0.15", "iw", "ih*0.15", "black@0.3"))
    chains.append(overlay_rect(0, "ih*0.3", "iw", "ih*0.15", "black@0.4"))
    chains.append(overlay_rect(0, "ih*0.45", "iw", "ih*0.15", "black@0.5"))
    chains.append(overlay_rect(0, "ih*0.6", "iw", "ih*0.15", "black@0.6"))
    chains.append(overlay_rect(0, "ih*0.75", "iw", "ih*0.15", "black@0.7"))
    chains.append(overlay_rect(0, "ih*0.9", "iw", "ih*0.1", "black@0.8"))
    # Accent diagonal line — approximated with bar
    chains.append(bar_sweep_x(0, "ih*0.5", "iw", 4, accent, delay=0.0, dur=0.4))
    # Title — large, on the dark side
    tx, ty, te = slide_left("120", "h*0.35", dur=0.5, delay=0.2, px=200)
    chains.append(_dt(title, font, 72, primary, tx, ty, enable=te))
    # Subtitle
    if subtitle:
        sx, sy, se = slide_left("120", "h*0.5", dur=0.3, delay=0.5, px=60)
        chains.append(_dt(subtitle, body, 24, accent, sx, sy, enable=se, shadow=False))
    # Bottom accent bar
    chains.append(bar_sweep_x(0, "ih-4", "iw", 4, danger, delay=0.3, dur=0.3))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


def paper_fold_reveal(palette: dict, props: dict) -> dict:
    """Paper fold / unfold reveal — text appears from fold.
    Style: text slides in from center fold line, serif font.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "OpenSans-Bold.ttf")
    title = props.get("title", "HIDDEN IN PLAIN SIGHT")
    subtitle = props.get("subtitle", "")

    chains = []
    # Central fold line — horizontal
    chains.append(bar_sweep_x(0, "ih*0.48", "iw", 2, accent, delay=0.0, dur=0.3))
    # Dark overlay above fold
    chains.append(overlay_rect(0, 0, "iw", "ih*0.48", "black@0.3"))
    # Dark overlay below fold
    chains.append(overlay_rect(0, "ih*0.5", "iw", "ih*0.5", "black@0.3"))
    # Title — unfolds from center
    tx, ty, te = scale_pop("(w-text_w)/2", "(h-text_h)/2-30", dur=0.6, delay=0.2, px=80)
    chains.append(_dt(title, font, 64, primary, tx, ty, enable=te))
    # Subtitle
    if subtitle:
        sx, sy, se = slide_up("(w-text_w)/2", "(h-text_h)/2+50", dur=0.4, delay=0.6, px=30)
        chains.append(_dt(subtitle, body, 22, accent, sx, sy, enable=se, shadow=False))

    return {"chains": chains, "treatment": "slow_push_in", "cost": 2}


def bold_red_text(palette: dict, props: dict) -> dict:
    """Bold red text — news-breaking / urgent headline style.
    Style: massive red text on dark overlay, impactful and urgent.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    text = props.get("text", "BREAKING NEWS")
    detail = props.get("detail", "")

    chains = []
    # Heavy dark overlay
    chains.append(overlay_rect(0, "ih*0.25", "iw", "ih*0.55", "black@0.7"))
    # Top danger bar
    chains.append(bar_sweep_x(0, 0, "iw", 6, danger, delay=0.0, dur=0.3))
    # Main text — massive, danger color
    tx, ty, te = scale_pop("(w-text_w)/2", "(h-text_h)/2-30", dur=0.5, delay=0.1, px=100)
    chains.append(_dt(text, font, 120, danger, tx, ty, enable=te))
    # Detail
    if detail:
        dx, dy, de = slide_up("(w-text_w)/2", "(h-text_h)/2+90", dur=0.4, delay=0.4, px=40)
        chains.append(_dt(detail, body, 24, primary, dx, dy, enable=de, shadow=False))
    # Bottom danger bar
    chains.append(bar_sweep_x(0, "ih-6", "iw", 6, danger, delay=0.2, dur=0.3))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


def cinematic_zoom_text(palette: dict, props: dict) -> dict:
    """Cinematic zoom-in text reveal — text scales up dramatically.
    Style: text starts small and zooms to full size, bold font.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    text = props.get("text", "EVERYTHING CHANGED")
    detail = props.get("detail", "")

    chains = []
    chains.append(overlay_rect(0, "ih*0.3", "iw", "ih*0.45", "black@0.6"))
    # Text — zooms in via scale_pop with large px
    tx, ty, te = scale_pop("(w-text_w)/2", "(h-text_h)/2-20", dur=0.8, delay=0.1, px=200)
    chains.append(_dt(text, font, 96, primary, tx, ty, enable=te))
    # Accent bars — top and bottom
    chains.append(bar_sweep_x("(iw-240)/2", "ih*0.35", 240, 3, accent, delay=0.6, dur=0.3))
    chains.append(bar_sweep_x("(iw-240)/2", "ih*0.65-3", 240, 3, accent, delay=0.7, dur=0.3))
    # Detail
    if detail:
        dx, dy, de = fade_in("(w-text_w)/2", "(h-text_h)/2+80", delay=0.8)
        chains.append(_dt(detail, body, 20, accent, dx, dy, enable=de, shadow=False))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


def passport_stamp(palette: dict, props: dict) -> dict:
    """Travel / passport stamp style — circular stamp aesthetic.
    Style: stamp-like border with location and date.
    Travel documentary / explorer aesthetic.
    """
    accent = palette.get("accent", "#c94c3c")
    primary = palette.get("primary", "#f5f0e1")
    bg = palette.get("background", "#2a1f14")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    location = props.get("location", props.get("title", "SILICON VALLEY"))
    date = props.get("date", "1930")

    chains = []
    # Dark overlay
    chains.append(overlay_rect(0, 0, "iw", "ih", "black@0.3"))
    # Stamp border — outer ring approximated with rectangles
    chains.append(bar_static("iw*0.25", "ih*0.2", "iw*0.5", "ih*0.6", "white@0.04"))
    # Stamp border — top line
    chains.append(bar_sweep_x("iw*0.25", "ih*0.2", "iw*0.5", 3, accent, delay=0.0, dur=0.3))
    # Stamp border — bottom line
    chains.append(bar_sweep_x("iw*0.25", "ih*0.8-3", "iw*0.5", 3, accent, delay=0.2, dur=0.3))
    # Stamp border — left line
    chains.append(bar_grow_y("iw*0.25", "ih*0.2", 3, "ih*0.6", accent, delay=0.1, dur=0.3))
    # Stamp border — right line
    chains.append(bar_grow_y("iw*0.75-3", "ih*0.2", 3, "ih*0.6", accent, delay=0.3, dur=0.3))
    # Location — large, centered
    tx, ty, te = scale_pop("(w-text_w)/2", "(h-text_h)/2-30", dur=0.5, delay=0.2, px=80)
    chains.append(_dt(location, font, 80, accent, tx, ty, enable=te))
    # Date — smaller, below
    sx, sy, se = slide_up("(w-text_w)/2", "(h-text_h)/2+60", dur=0.3, delay=0.5, px=30)
    chains.append(_dt(date, body, 36, primary, sx, sy, enable=se, shadow=False))
    # Small decorative lines around stamp
    chains.append(bar_static("iw*0.3", "ih*0.52", "iw*0.4", 2, accent))

    return {"chains": chains, "treatment": "static_hold", "cost": 2}


def split_screen_doc(palette: dict, props: dict) -> dict:
    """Split screen documentary — left dark, right text.
    Style: left half dark with image, right half with text content.
    Classic documentary interview / information split.
    """
    accent = palette.get("accent", "#f2c14e")
    primary = palette.get("primary", "#f4f0e6")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "Poppins-Bold.ttf")
    body_reg = props.get("_body_font", "OpenSans-Bold.ttf")
    title = props.get("title", "THE FINDING")
    subtitle = props.get("subtitle", "")
    detail = props.get("detail", "")

    chains = []
    # Right half dark overlay
    chains.append(overlay_rect("iw*0.5", 0, "iw*0.5", "ih", "black@0.75"))
    # Title — on right half
    tx, ty, te = slide_left("1056", "h*0.3", dur=0.5, delay=0.1, px=150)
    chains.append(_dt(title, font, 56, primary, tx, ty, enable=te))
    # Accent bar on right half (static)
    chains.append(bar_static(1056, "ih*0.4", 160, 3, accent))
    # Subtitle
    if subtitle:
        sx, sy, se = slide_left("1056", "h*0.45", dur=0.3, delay=0.4, px=80)
        chains.append(_dt(subtitle, body, 22, accent, sx, sy, enable=se, shadow=False))
    # Detail
    if detail:
        dx, dy, de = slide_left("1056", "h*0.55", dur=0.3, delay=0.6, px=60)
        chains.append(_dt(detail, body_reg, 18, primary, dx, dy, enable=de, shadow=False))

    return {"chains": chains, "treatment": "slow_push_in", "cost": 2}


def vintage_headline(palette: dict, props: dict) -> dict:
    """Vintage newspaper headline — old press style.
    Style: large headline with deck, dateline, press aesthetic.
    """
    accent = palette.get("accent", "#c9a96e")
    primary = palette.get("primary", "#f5f0e1")
    danger = palette.get("danger", "#ef6f6c")
    font = props.get("_font", "Poppins-Bold.ttf")
    body = props.get("_body_font", "OpenSans-Bold.ttf")
    headline = props.get("headline", props.get("title", "THE WARNING WAS ALREADY THERE"))
    deck = props.get("deck", "EXCLUSIVE REPORT")
    dateline = props.get("dateline", "SAN FRANCISCO CHRONICLE")

    chains = []
    # Aged paper overlay
    chains.append(overlay_rect(0, 0, "iw", "ih", "black@0.3"))
    # Top border — newspaper masthead style
    chains.append(bar_sweep_x(60, 120, "iw-120", 3, accent, delay=0.0, dur=0.3))
    chains.append(bar_sweep_x(60, 126, "iw-120", 1, accent, delay=0.1, dur=0.2))
    # Dateline — small, above headline
    dx, dy, de = fade_in("(w-text_w)/2", "160", delay=0.1)
    chains.append(_dt(dateline, body, 14, accent, dx, dy, enable=de, shadow=False))
    # Deck label
    dtx, dty, dte = fade_in("(w-text_w)/2", "200", delay=0.15)
    chains.append(_dt(deck, body, 16, danger, dtx, dty, enable=dte, shadow=False))
    # Headline — massive serif
    hx, hy, he = slide_up("(w-text_w)/2", "(h-text_h)/2-20", dur=0.6, delay=0.2, px=80)
    chains.append(_dt(headline, font, 72, primary, hx, hy, enable=he))
    # Bottom border
    chains.append(bar_sweep_x(60, "ih*0.65", "iw-120", 3, accent, delay=0.5, dur=0.3))
    chains.append(bar_sweep_x(60, "ih*0.65+6", "iw-120", 1, accent, delay=0.6, dur=0.2))

    return {"chains": chains, "treatment": "slow_push_in", "cost": 2}


# ═══════════════════════════════════════════════════════════════════
# TEMPLATE REGISTRY
# ═══════════════════════════════════════════════════════════════════

VOX_TEMPLATES = {
    # Typography
    "cold_open": cold_open,
    "kinetic_statement": kinetic_statement,
    "word_cascade": word_cascade,
    "keyword_reveal": keyword_reveal,
    "typewriter_reveal": typewriter_reveal,
    "split_statement": split_statement,
    # Data & Stats
    "stat_counter": stat_counter,
    "money_counter": money_counter,
    "percentage_gauge": percentage_gauge,
    "metric_grid": metric_grid,
    "bar_chart": bar_chart,
    "ranked_list": ranked_list,
    # Evidence & Investigation
    "evidence_callout": evidence_callout,
    "redaction_reveal": redaction_reveal,
    "case_file": case_file,
    "source_citation": source_citation,
    "stamp_mark": stamp_mark,
    # Timeline & Dates
    "date_reveal": date_reveal,
    "calendar_rip": calendar_rip,
    "timeline_trace": timeline_trace,
    # Quotes
    "quote_card": quote_card,
    "witness_quote": witness_quote,
    # Comparison
    "cause_effect": cause_effect,
    "before_after": before_after,
    "split_compare": split_compare,
    # Location
    "location_label": location_label,
    "map_route": map_route,
    # Closing
    "end_card": end_card,
    "cta_card": cta_card,
    # Document
    "field_caption": field_caption,
    "exhibit_label": exhibit_label,
    "newsprint_reveal": newsprint_reveal,
    "countdown_clock": countdown_clock,
    "signal_scan": signal_scan,
    "index_card": index_card,
    "profile_dossier": profile_dossier,
    # Cinematic / Noah Morris Style
    "watercolor_reveal": watercolor_reveal,
    "torn_paper_reveal": torn_paper_reveal,
    "bold_name_banner": bold_name_banner,
    "location_title_card": location_title_card,
    "retro_tech_overlay": retro_tech_overlay,
    "vintage_film_card": vintage_film_card,
    "dramatic_portrait": dramatic_portrait,
    "chapter_title": chapter_title,
    "diagonal_split": diagonal_split,
    "paper_fold_reveal": paper_fold_reveal,
    "bold_red_text": bold_red_text,
    "cinematic_zoom_text": cinematic_zoom_text,
    "passport_stamp": passport_stamp,
    "split_screen_doc": split_screen_doc,
    "vintage_headline": vintage_headline,
}

def _wrap_scene_duration(fn):
    def wrapped(palette: dict, props: dict, scene_duration: float | None = None, **kwargs) -> dict:
        return fn(palette, props)
    wrapped.__name__ = getattr(fn, "__name__", "vox_template")
    return wrapped

VOX_TEMPLATES = {_k: _wrap_scene_duration(_v) for _k, _v in VOX_TEMPLATES.items()}

import os as _os
_PROF_TEMPLATES = {}
try:
    from .professional_templates import PROFESSIONAL_TEMPLATES as _PT
    _PROF_TEMPLATES = _PT
except Exception:
    try:
        from services.native_engine.libraries.professional_templates import PROFESSIONAL_TEMPLATES as _PT
        _PROF_TEMPLATES = _PT
    except Exception:
        try:
            from native_engine.libraries.professional_templates import PROFESSIONAL_TEMPLATES as _PT
            _PROF_TEMPLATES = _PT
        except Exception:
            _PROF_TEMPLATES = {}

if _PROF_TEMPLATES:
    VOX_TEMPLATES.update(_PROF_TEMPLATES)


# ═══════════════════════════════════════════════════════════════════
# TEMPLATE SOUND EFFECTS
# Each template maps to 1-2 SFX cues that play when it appears.
# This gives every VOX template a distinctive audio signature.
# ═══════════════════════════════════════════════════════════════════

TEMPLATE_SFX: Dict[str, List[Dict[str, Any]]] = {
    # TYPOGRAPHY
    "cold_open": [{"kind": "sub_boom", "delay": 0.0, "volume": 0.25}],
    "kinetic_statement": [{"kind": "whoosh", "delay": 0.0, "volume": 0.15}],
    "word_cascade": [{"kind": "typing_burst", "delay": 0.1, "volume": 0.12}, {"kind": "data_tick", "delay": 0.4, "volume": 0.10}],
    "keyword_reveal": [{"kind": "ui_tick", "delay": 0.2, "volume": 0.18}],
    "typewriter_reveal": [{"kind": "keyboard_single", "delay": 0.1, "volume": 0.15}, {"kind": "keyboard_single", "delay": 0.5, "volume": 0.12}],
    "split_statement": [{"kind": "bass_drop", "delay": 0.0, "volume": 0.20}],
    # DATA & STATS
    "stat_counter": [{"kind": "sonar", "delay": 0.0, "volume": 0.15}, {"kind": "data_tick", "delay": 0.5, "volume": 0.12}],
    "money_counter": [{"kind": "cash_counter", "delay": 0.3, "volume": 0.18}],
    "percentage_gauge": [{"kind": "data_tick", "delay": 0.0, "volume": 0.14}],
    "metric_grid": [{"kind": "sonar", "delay": 0.0, "volume": 0.12}, {"kind": "sonar", "delay": 0.6, "volume": 0.10}],
    "bar_chart": [{"kind": "data_tick", "delay": 0.1, "volume": 0.12}, {"kind": "data_tick", "delay": 0.5, "volume": 0.12}],
    "ranked_list": [{"kind": "notification_up", "delay": 0.0, "volume": 0.12}, {"kind": "notification_up", "delay": 0.4, "volume": 0.10}],
    # EVIDENCE
    "evidence_callout": [{"kind": "stamp_hit", "delay": 0.1, "volume": 0.22}],
    "redaction_reveal": [{"kind": "marker_scratch", "delay": 0.0, "volume": 0.18}],
    "case_file": [{"kind": "paper_rustle", "delay": 0.1, "volume": 0.15}],
    "source_citation": [{"kind": "radio_static", "delay": 0.0, "volume": 0.10}],
    "stamp_mark": [{"kind": "stamp_hit", "delay": 0.0, "volume": 0.25}],
    # TIMELINE
    "date_reveal": [{"kind": "camera_shutter", "delay": 0.0, "volume": 0.18}],
    "calendar_rip": [{"kind": "paper_tear", "delay": 0.0, "volume": 0.20}],
    "timeline_trace": [{"kind": "sonar", "delay": 0.0, "volume": 0.12}],
    # QUOTES
    "quote_card": [{"kind": "whoosh_soft", "delay": 0.1, "volume": 0.10}],
    "witness_quote": [{"kind": "whoosh_soft", "delay": 0.1, "volume": 0.12}],
    # COMPARISON
    "cause_effect": [{"kind": "impact", "delay": 0.3, "volume": 0.18}],
    "before_after": [{"kind": "whoosh", "delay": 0.0, "volume": 0.15}],
    "split_compare": [{"kind": "impact_metal", "delay": 0.0, "volume": 0.15}],
    # LOCATION
    "location_label": [{"kind": "route_ping", "delay": 0.0, "volume": 0.15}],
    "map_route": [{"kind": "sonar", "delay": 0.0, "volume": 0.14}],
    # CLOSING
    "end_card": [{"kind": "reverse_swell", "delay": 0.0, "volume": 0.18}],
    "cta_card": [{"kind": "reveal_chime", "delay": 0.2, "volume": 0.20}],
    # DOCUMENT
    "field_caption": [{"kind": "paper_rustle", "delay": 0.0, "volume": 0.08}],
    "exhibit_label": [{"kind": "camera_shutter", "delay": 0.0, "volume": 0.12}],
    "newsprint_reveal": [{"kind": "paper_rustle", "delay": 0.1, "volume": 0.15}],
    "countdown_clock": [{"kind": "sonar", "delay": 0.0, "volume": 0.15}, {"kind": "sonar", "delay": 0.5, "volume": 0.15}],
    "signal_scan": [{"kind": "radio_static", "delay": 0.0, "volume": 0.15}],
    "index_card": [{"kind": "paper_rustle", "delay": 0.0, "volume": 0.10}],
    "profile_dossier": [{"kind": "paper_rustle", "delay": 0.1, "volume": 0.12}],
    # CINEMATIC
    "watercolor_reveal": [{"kind": "whoosh_soft", "delay": 0.0, "volume": 0.15}],
    "torn_paper_reveal": [{"kind": "paper_tear", "delay": 0.0, "volume": 0.20}],
    "bold_name_banner": [{"kind": "sub_boom", "delay": 0.0, "volume": 0.22}],
    "location_title_card": [{"kind": "route_ping", "delay": 0.0, "volume": 0.15}],
    "retro_tech_overlay": [{"kind": "radio_static", "delay": 0.0, "volume": 0.12}],
    "vintage_film_card": [{"kind": "vinyl_crackle", "delay": 0.0, "volume": 0.15}],
    "dramatic_portrait": [{"kind": "sub_boom", "delay": 0.1, "volume": 0.20}],
    "chapter_title": [{"kind": "reverse_swell", "delay": 0.0, "volume": 0.18}],
    "diagonal_split": [{"kind": "whoosh", "delay": 0.0, "volume": 0.15}],
    "paper_fold_reveal": [{"kind": "paper_flip", "delay": 0.1, "volume": 0.18}],
    "bold_red_text": [{"kind": "impact", "delay": 0.0, "volume": 0.22}],
    "cinematic_zoom_text": [{"kind": "sub_boom", "delay": 0.0, "volume": 0.20}],
    "passport_stamp": [{"kind": "stamp_hit", "delay": 0.1, "volume": 0.22}],
    "split_screen_doc": [{"kind": "impact_metal", "delay": 0.0, "volume": 0.15}],
    "vintage_headline": [{"kind": "vinyl_crackle", "delay": 0.0, "volume": 0.12}],
}

TEMPLATE_SFX.update({
    "archival_caption": [{"kind": "camera_shutter", "delay": 0.0, "volume": 0.10}],
    "date_numeral": [{"kind": "clock_tick", "delay": 0.0, "volume": 0.12}],
    "evidence_highlight": [{"kind": "marker_scratch", "delay": 0.0, "volume": 0.15}],
    "profile_card": [{"kind": "camera_shutter", "delay": 0.1, "volume": 0.12}],
    "subject_grid": [{"kind": "ui_tick", "delay": 0.0, "volume": 0.10}],
    "listicle_steps": [{"kind": "data_tick", "delay": 0.0, "volume": 0.12}],
    "timeline_strip": [{"kind": "sonar", "delay": 0.0, "volume": 0.12}],
    "headline_center": [{"kind": "sub_boom", "delay": 0.0, "volume": 0.16}],
})


def get_template_sfx(template_id: str) -> List[Dict[str, Any]]:
    """Get sound effects for a template."""
    return list(TEMPLATE_SFX.get(template_id, []))


# Text-entry animation classification for each template. The templates call
# the vox_animations helpers (slide_up, scale_pop, fade_in, ...) directly, so
# we classify by instrumenting those helpers the first time a template runs.
_ENTRY_ANIM_HELPERS = (
    "slide_up", "slide_down", "slide_left", "slide_right", "drop_in",
    "scale_pop", "fade_in", "bounce_up", "static_hold",
)
_ENTRY_ANIM_CACHE: Dict[str, str] = {}


def template_entry_animation(template_id: str) -> str:
    """Return the dominant text-entry animation a template renders.

    Mirrors the professional motion system exactly: each animated text layer
    picks an animation from the weighted palette seeded by the template name,
    and the dominant style is aggregated. One of: zoom, rise, slide, fade,
    typewriter, stamp, wipe, data, static.
    """
    if template_id in _ENTRY_ANIM_CACHE:
        return _ENTRY_ANIM_CACHE[template_id]
    style = "static"
    fn = VOX_TEMPLATES.get(template_id)
    if fn:
        try:
            from services.native_engine.libraries import professional_templates as _prof
            _original_entry = _prof._entry_animation
            _recorded: List[str] = []
            def _recording(seed_key, text_idx):
                _anim = _original_entry(seed_key, text_idx)
                _recorded.append(_anim)
                return _anim
            _prof._entry_animation = _recording
            try:
                fn(_DEFAULT_SFX_PALETTE, {"title": "SAMPLE", "subtitle": "SAMPLE", "detail": "SAMPLE"}, scene_duration=4.0)
            finally:
                _prof._entry_animation = _original_entry
            if _recorded:
                anim_counts: Dict[str, int] = {}
                for anim in _recorded:
                    anim_counts[anim] = anim_counts.get(anim, 0) + 1
                style = max(anim_counts, key=anim_counts.get)
                if style == "fade_out":
                    style = "fade"
        except Exception:
            pass
    if style == "static" and template_id:
        # Fallback heuristic by template name so SFX still vary even if a
        # template resists introspection.
        name = template_id.lower()
        if any(token in name for token in ("typewriter", "cascade", "word")):
            style = "typewriter"
        elif "stamp" in name:
            style = "stamp"
        elif "zoom" in name:
            style = "zoom"
        elif "wipe" in name:
            style = "wipe"
        elif any(token in name for token in ("chart", "counter", "metric", "gauge", "percentage", "ranked", "bar_chart")):
            style = "data"
        elif any(token in name for token in ("reveal", "banner", "headline", "opener", "title")):
            style = "rise"
        elif any(token in name for token in ("quote", "citation")):
            style = "fade"
    _ENTRY_ANIM_CACHE[template_id] = style
    return style


_TEXT_LAYER_CACHE: Dict[str, int] = {}


def template_text_layers(template_id: str) -> int:
    """Approximate number of text layers a template draws.

    Used to sync soft SFX hits to the staggered text entry animations. The
    layer count is structural (a template draws kicker/title/subtitle or a
    single headline), so a representative prop set is enough.
    """
    if template_id in _TEXT_LAYER_CACHE:
        return _TEXT_LAYER_CACHE[template_id]
    count = 0
    fn = VOX_TEMPLATES.get(template_id)
    if fn:
        try:
            result = fn(_DEFAULT_SFX_PALETTE, {"title": "SAMPLE", "subtitle": "SAMPLE", "detail": "SAMPLE"})
            texts = set()
            for c in (result.get("chains") or []):
                if c.startswith("drawtext="):
                    match = re.search(r"text='([^']*)'", c)
                    texts.add(match.group(1) if match else c)
            count = len(texts)
        except Exception:
            count = 0
    _TEXT_LAYER_CACHE[template_id] = count
    return count


_DEFAULT_SFX_PALETTE = {
    "primary": "#f4f0e6", "accent": "#d8a84e", "danger": "#c94a3d",
    "background": "#0b1320", "surface": "#16263b",
}


def get_template(template_id: str):
    """Get template function by ID."""
    return VOX_TEMPLATES.get(template_id)


def list_templates() -> list:
    """List all template IDs."""
    return list(VOX_TEMPLATES.keys())
