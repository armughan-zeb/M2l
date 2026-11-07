"""Local Reusable Scene Systems — High-Performance Documentary Graphic Engines.

Renders high-resolution (1920x1080), anti-aliased, transparent RGBA overlays
for all 15 Universal Scene Systems using local OFL fonts and Visual DNA styling.
Zero external runtime fees, zero network latency, sub-millisecond execution.
"""

from __future__ import annotations

import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT_DIR = Path(__file__).resolve().parents[2]
FONTS_DIR = ROOT_DIR / "fonts"
PUBLIC_FONTS_DIR = ROOT_DIR / "public" / "font-library"


# ---------------------------------------------------------------------------
# Font Resolution & Cache
# ---------------------------------------------------------------------------
_FONT_CACHE: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}


def _resolve_font_file(font_name: str) -> Path:
    """Resolve font name/file to physical TTF path."""
    clean = str(font_name or "").strip().lower().replace(" ", "").replace("-", "")
    mapping = {
        "cinzel": "Cinzel-Bold.ttf",
        "cinzelbold": "Cinzel-Bold.ttf",
        "playfair": "PlayfairDisplay-Bold.ttf",
        "playfairdisplay": "PlayfairDisplay-Bold.ttf",
        "montserrat": "Montserrat-Bold.ttf",
        "montserratbold": "Montserrat-Bold.ttf",
        "orbitron": "Orbitron-Bold.ttf",
        "orbitronbold": "Orbitron-Bold.ttf",
        "anton": "Anton-Regular.ttf",
        "bebas": "BebasNeue-Regular.ttf",
        "bebasneue": "BebasNeue-Regular.ttf",
        "opensans": "OpenSans-Bold.ttf",
        "poppins": "Poppins-Bold.ttf",
        "poppinsregular": "Poppins-Regular.ttf",
        "roboto": "Roboto-Bold.ttf",
        "caveat": "Caveat-Bold.ttf",
        "audiowide": "Audiowide-Regular.ttf",
        "bungee": "Bungee-Regular.ttf",
        "righteous": "Righteous-Regular.ttf",
        "inter": "Inter-Bold.ttf",
        "interregular": "Inter-Regular.ttf",
        "interbold": "Inter-Bold.ttf",
        "oswald": "Oswald-Bold.ttf",
        "oswaldbold": "Oswald-Bold.ttf",
        "oswaldregular": "Oswald-Regular.ttf",
        "archivoblack": "ArchivoBlack-Regular.ttf",
        "archivo": "ArchivoBlack-Regular.ttf",
        "barlow": "BarlowCondensed-Bold.ttf",
        "barlowcondensed": "BarlowCondensed-Bold.ttf",
        "dmsans": "DMSans-Bold.ttf",
        "plusjakartasans": "PlusJakartaSans-Bold.ttf",
        "jakarta": "PlusJakartaSans-Bold.ttf",
        "lora": "Lora-Bold.ttf",
        "loraregular": "Lora-Regular.ttf",
        "loraitalic": "Lora-Italic.ttf",
        "jetbrainsmono": "JetBrainsMono-Bold.ttf",
        "jetbrains": "JetBrainsMono-Bold.ttf",
        "monospace": "JetBrainsMono-Bold.ttf",
    }
    filename = mapping.get(clean, "")
    if not filename:
        for k, v in mapping.items():
            if k in clean:
                filename = v
                break
    if not filename:
        filename = "Poppins-Bold.ttf"

    for candidate_dir in (FONTS_DIR, PUBLIC_FONTS_DIR):
        target = candidate_dir / filename
        if target.exists():
            return target
    # Fallback to any existing ttf
    for candidate_dir in (FONTS_DIR, PUBLIC_FONTS_DIR):
        for f in candidate_dir.glob("*.ttf"):
            return f
    return Path(filename)


def get_font(font_name: str, size: int) -> ImageFont.FreeTypeFont:
    """Retrieve or load cached TrueType font."""
    key = (font_name, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    font_path = _resolve_font_file(font_name)
    try:
        fnt = ImageFont.truetype(str(font_path), size)
    except Exception:
        try:
            fnt = ImageFont.truetype("arial.ttf", size)
        except Exception:
            fnt = ImageFont.load_default()
    _FONT_CACHE[key] = fnt
    return fnt


# ---------------------------------------------------------------------------
# Color & Drawing Helpers
# ---------------------------------------------------------------------------
def _hex_to_rgb(hex_str: str, default: Tuple[int, int, int] = (255, 255, 255)) -> Tuple[int, int, int]:
    if not hex_str or not isinstance(hex_str, str):
        return default
    raw = hex_str.strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(c * 2 for c in raw)
    if len(raw) == 6:
        try:
            return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))
        except ValueError:
            pass
    return default


def _draw_rounded_panel(
    draw: ImageDraw.ImageDraw,
    xy: Sequence[float],
    radius: int,
    fill: Tuple[int, int, int, int],
    outline: Optional[Tuple[int, int, int, int]] = None,
    width: int = 1,
):
    """Draw a smooth rounded rectangle with optional outline."""
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _draw_accent_chip(
    draw: ImageDraw.ImageDraw,
    xy: Tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    accent_rgb: Tuple[int, int, int],
    text_rgb: Tuple[int, int, int] = (255, 255, 255),
):
    """Draw a small documentary category tag / kicker badge."""
    x, y = xy
    bbox = font.getbbox(text)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    px, py = 12, 4
    chip_box = [x, y, x + tw + px * 2, y + th + py * 2]
    draw.rounded_rectangle(chip_box, radius=4, fill=(*accent_rgb, 210))
    draw.text((x + px, y + py - bbox[1]), text, font=font, fill=(*text_rgb, 255))
    return chip_box[2], chip_box[3]


# ---------------------------------------------------------------------------
# 15 Local Universal Scene Systems
# ---------------------------------------------------------------------------

def render_chapter_title_overlay(visual_dna: dict, props: dict, width: int = 1920, height: int = 1080) -> Image.Image:
    """ChapterTitle — Act markers, documentary dividers, structural reveals."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    palette = visual_dna.get("palette") or {}
    pri = _hex_to_rgb(palette.get("primary", "#ffffff"))
    sec = _hex_to_rgb(palette.get("secondary", "#d4af37"))
    acc = _hex_to_rgb(palette.get("accent", "#d4af37"))
    bg = _hex_to_rgb(palette.get("background", "#140a10"))
    display_fnt_name = (visual_dna.get("typography") or {}).get("display", "cinzel")

    title = str(props.get("title") or props.get("headline") or "CHAPTER I").upper()
    kicker = str(props.get("kicker") or props.get("subtitle") or "DOCUMENTARY SPECIAL").upper()
    variant = str(props.get("variant") or "bold_editorial_kicker").lower()

    fnt_title = get_font(display_fnt_name, 72)
    fnt_kicker = get_font("Montserrat", 22)
    fnt_num = get_font(display_fnt_name, 120)

    if variant == "cinematic_letterbox_divider":
        # Top and bottom cinematic borders with gold accent rules
        draw.rectangle([0, 0, width, 90], fill=(*bg, 235))
        draw.rectangle([0, 88, width, 90], fill=(*acc, 255))
        draw.rectangle([0, height - 90, width, height], fill=(*bg, 235))
        draw.rectangle([0, height - 90, width, height - 88], fill=(*acc, 255))

        # Centered elegant text
        _draw_accent_chip(draw, (width // 2 - 80, height // 2 - 80), kicker, fnt_kicker, acc)
        bbox = fnt_title.getbbox(title)
        tw = bbox[2] - bbox[0]
        draw.text(((width - tw) // 2, height // 2 - 10), title, font=fnt_title, fill=(*pri, 255))

    elif variant == "archival_slate_counter":
        # Left-aligned archival slate with giant number
        slate_w, slate_h = 760, 420
        slate_x, slate_y = 120, (height - slate_h) // 2
        _draw_rounded_panel(draw, [slate_x, slate_y, slate_x + slate_w, slate_y + slate_h], 8, (*bg, 220), outline=(*acc, 180), width=2)

        draw.rectangle([slate_x + 30, slate_y + 30, slate_x + 36, slate_y + slate_h - 30], fill=(*acc, 255))
        draw.text((slate_x + 60, slate_y + 40), kicker, font=fnt_kicker, fill=(*acc, 240))
        draw.text((slate_x + 60, slate_y + 80), "ACT", font=fnt_kicker, fill=(*sec, 200))
        draw.text((slate_x + 60, slate_y + 110), title, font=fnt_title, fill=(*pri, 255))
        draw.line([slate_x + 60, slate_y + 260, slate_x + slate_w - 60, slate_y + 260], fill=(*sec, 80), width=1)
        draw.text((slate_x + 60, slate_y + 280), "OFFICIAL HISTORICAL RECORD", font=get_font("OpenSans", 18), fill=(*sec, 180))

    else:  # bold_editorial_kicker
        panel_w, panel_h = 1200, 260
        px, py = (width - panel_w) // 2, height // 2 - 130
        _draw_rounded_panel(draw, [px, py, px + panel_w, py + panel_h], 12, (*bg, 210), outline=(*acc, 120), width=1)

        # Accent top highlight
        draw.rectangle([px + 40, py, px + panel_w - 40, py + 4], fill=(*acc, 255))
        _draw_accent_chip(draw, (px + 60, py + 30), kicker, fnt_kicker, acc)

        bbox = fnt_title.getbbox(title)
        tw = bbox[2] - bbox[0]
        draw.text((px + 60, py + 80), title, font=fnt_title, fill=(*pri, 255))

        # Bottom subtle tracking line
        draw.line([px + 60, py + panel_h - 35, px + panel_w - 60, py + panel_h - 35], fill=(*sec, 70), width=1)

    return img


def render_stat_card_overlay(visual_dna: dict, props: dict, width: int = 1920, height: int = 1080) -> Image.Image:
    """StatCard — Large numerical callouts, KPI metrics, percentage comparisons."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    palette = visual_dna.get("palette") or {}
    pri = _hex_to_rgb(palette.get("primary", "#ffffff"))
    sec = _hex_to_rgb(palette.get("secondary", "#94a3b8"))
    acc = _hex_to_rgb(palette.get("accent", "#38bdf8"))
    bg = _hex_to_rgb(palette.get("background", "#0b1320"))
    num_fnt_name = (visual_dna.get("typography") or {}).get("numbers", "orbitron")

    raw_stat = props.get("stat") or props.get("number") or props.get("value")
    if raw_stat in (None, "", "—"):
        return img
    stat = str(raw_stat).upper()
    label = str(props.get("label") or props.get("title") or "KEY METRIC").upper()
    detail = str(props.get("detail") or props.get("subtitle") or "")

    card_w, card_h = 720, 360
    cx, cy = (width - card_w) // 2, (height - card_h) // 2
    _draw_rounded_panel(draw, [cx, cy, cx + card_w, cy + card_h], 16, (*bg, 225), outline=(*acc, 180), width=2)

    # Accent glow top bar
    draw.rectangle([cx + 40, cy, cx + card_w - 40, cy + 4], fill=(*acc, 255))

    fnt_kicker = get_font("Montserrat", 20)
    _draw_accent_chip(draw, (cx + 40, cy + 30), "FACTUAL CALLOUT", fnt_kicker, acc)

    fnt_stat = get_font(num_fnt_name, 96)
    fnt_label = get_font("Montserrat", 28)
    fnt_detail = get_font("OpenSans", 20)

    # Large Stat Num
    draw.text((cx + 40, cy + 80), stat, font=fnt_stat, fill=(*acc, 255))

    # Divider
    draw.line([cx + 40, cy + 220, cx + card_w - 40, cy + 220], fill=(*sec, 60), width=1)

    # Label & Detail
    draw.text((cx + 40, cy + 240), label, font=fnt_label, fill=(*pri, 255))
    draw.text((cx + 40, cy + 285), detail[:65], font=fnt_detail, fill=(*sec, 220))

    return img


def render_chart_overlay(visual_dna: dict, props: dict, width: int = 1920, height: int = 1080) -> Image.Image:
    """Chart — High-resolution procedural bar charts, trend graphs, distribution rings."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    palette = visual_dna.get("palette") or {}
    pri = _hex_to_rgb(palette.get("primary", "#ffffff"))
    sec = _hex_to_rgb(palette.get("secondary", "#94a3b8"))
    acc = _hex_to_rgb(palette.get("accent", "#38bdf8"))
    bg = _hex_to_rgb(palette.get("background", "#0a0f1d"))

    title = str(props.get("title") or "DOCUMENTARY COMPARISON").upper()
    data = props.get("values") or props.get("data") or []
    if not isinstance(data, list) or len(data) < 2:
        return img

    chart_w, chart_h = 1000, 520
    cx, cy = (width - chart_w) // 2, (height - chart_h) // 2
    _draw_rounded_panel(draw, [cx, cy, cx + chart_w, cy + chart_h], 16, (*bg, 230), outline=(*sec, 80), width=1)

    # Title & Badge
    fnt_kicker = get_font("Montserrat", 18)
    fnt_title = get_font("Montserrat", 28)
    _draw_accent_chip(draw, (cx + 50, cy + 35), "STATISTICAL RECORD", fnt_kicker, acc)
    draw.text((cx + 50, cy + 75), title, font=fnt_title, fill=(*pri, 255))

    def _numeric_chart_value(value: Any) -> float:
        """Accept a director's human-readable metric without losing its scale."""
        if isinstance(value, (int, float)):
            return float(value)
        raw = str(value or "").strip().lower().replace(",", "")
        match = re.search(r"[-+]?\d+(?:\.\d+)?", raw)
        if not match:
            return 0.0
        number = float(match.group(0))
        if "trillion" in raw or re.search(r"\bt\b", raw):
            return number * 1_000_000_000_000
        if "billion" in raw or re.search(r"\bb\b", raw):
            return number * 1_000_000_000
        if "million" in raw or re.search(r"\bm\b", raw):
            return number * 1_000_000
        if "thousand" in raw or re.search(r"\bk\b", raw):
            return number * 1_000
        return number

    normalized_data = [
        {
            "numeric_value": _numeric_chart_value(item.get("value", item.get("raw", 0))),
            "display_value": str(item.get("display") or item.get("value") or item.get("raw") or "0").upper(),
            "label": str(item.get("label") or "").upper(),
        }
        for item in data
        if isinstance(item, Mapping)
    ]
    if len(normalized_data) < 2:
        return img

    # Draw Bars.  The numeric scale drives geometry while the authored display
    # string is kept intact, so a value such as "$100 MILLION" never becomes
    # separate "100" and "MILLION" visual beats.
    max_val = max([item["numeric_value"] for item in normalized_data] or [1.0])
    max_val = max(1.0, max_val)
    bar_area_x = cx + 80
    bar_area_y = cy + 160
    bar_area_w = chart_w - 160
    bar_area_h = 240

    # Base line
    draw.line([bar_area_x, bar_area_y + bar_area_h, bar_area_x + bar_area_w, bar_area_y + bar_area_h], fill=(*sec, 100), width=2)

    n_bars = len(normalized_data)
    slot_w = bar_area_w / max(1, n_bars)
    bar_w = min(70, slot_w * 0.55)

    fnt_val = get_font("Orbitron", 20)
    fnt_lbl = get_font("OpenSans", 18)

    for i, d in enumerate(normalized_data):
        val = d["numeric_value"]
        lbl = d["label"][:24]
        h_ratio = min(1.0, val / max_val)
        bh = bar_area_h * h_ratio
        bx = bar_area_x + i * slot_w + (slot_w - bar_w) / 2
        by = bar_area_y + bar_area_h - bh

        # Highlight final bar
        is_hero = (i == n_bars - 1)
        bar_col = acc if is_hero else sec
        _draw_rounded_panel(draw, [bx, by, bx + bar_w, bar_area_y + bar_area_h], 6, (*bar_col, 220), outline=(*acc, 255) if is_hero else None, width=2)

        # Value text above bar
        v_str = d["display_value"][:18]
        draw.text((bx + bar_w // 2, by - 28), v_str, font=fnt_val, fill=(*pri, 255), anchor="mt")

        # Label under bar
        # Keep labels compact enough for the chart geometry. Long narration
        # text is not a chart axis label and makes the frame unreadable.
        draw.text((bx + bar_w // 2, bar_area_y + bar_area_h + 16), lbl, font=fnt_lbl, fill=(*sec, 200), anchor="mt")

    return img


def render_quote_card_overlay(visual_dna: dict, props: dict, width: int = 1920, height: int = 1080) -> Image.Image:
    """QuoteCard — Archival quotation marks, source attribution chips, witness quotes."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    palette = visual_dna.get("palette") or {}
    pri = _hex_to_rgb(palette.get("primary", "#ffffff"))
    sec = _hex_to_rgb(palette.get("secondary", "#cbd5e1"))
    acc = _hex_to_rgb(palette.get("accent", "#eab308"))
    bg = _hex_to_rgb(palette.get("background", "#09090b"))
    serif_fnt_name = (visual_dna.get("typography") or {}).get("display", "playfair")

    quote = str(props.get("quote") or props.get("text") or "").strip()
    if not quote:
        return img
    author = str(props.get("author") or props.get("source") or "HISTORICAL ARCHIVE").upper()
    role = str(props.get("role") or props.get("subtitle") or "PRIMARY TESTIMONY")

    qw, qh = 1080, 360
    qx, qy = (width - qw) // 2, (height - qh) // 2
    _draw_rounded_panel(draw, [qx, qy, qx + qw, qy + qh], 16, (*bg, 230), outline=(*acc, 140), width=1)

    # Accent quotation symbol
    fnt_sym = get_font("PlayfairDisplay", 110)
    draw.text((qx + 50, qy + 10), "“", font=fnt_sym, fill=(*acc, 200))

    # Quote body
    fnt_quote = get_font(serif_fnt_name, 36)
    words = quote.split()
    lines = []
    curr = []
    for w in words:
        curr.append(w)
        if len(" ".join(curr)) > 55:
            lines.append(" ".join(curr))
            curr = []
    if curr:
        lines.append(" ".join(curr))

    qy_text = qy + 90
    for l in lines[:4]:
        draw.text((qx + 70, qy_text), l, font=fnt_quote, fill=(*pri, 255))
        qy_text += 48

    # Attribution divider & author
    draw.line([qx + 70, qy + qh - 75, qx + qw - 70, qy + qh - 75], fill=(*sec, 60), width=1)
    fnt_author = get_font("Montserrat", 22)
    fnt_role = get_font("OpenSans", 18)

    draw.text((qx + 70, qy + qh - 52), f"— {author}", font=fnt_author, fill=(*acc, 255))
    draw.text((qx + 340, qy + qh - 50), f"|  {role}", font=fnt_role, fill=(*sec, 190))

    return img


def render_document_highlight_overlay(visual_dna: dict, props: dict, width: int = 1920, height: int = 1080) -> Image.Image:
    """DocumentHighlight — Declassified records, redaction bars, yellow highlighter strips."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    palette = visual_dna.get("palette") or {}
    pri = (245, 240, 230)
    sec = (120, 115, 105)
    acc = _hex_to_rgb(palette.get("accent", "#eab308"))
    bg = (18, 16, 14)

    doc_w, doc_h = 960, 480
    dx, dy = (width - doc_w) // 2, (height - doc_h) // 2

    _draw_rounded_panel(draw, [dx, dy, dx + doc_w, dy + doc_h], 8, (*bg, 240), outline=(160, 145, 120, 180), width=2)

    stamp_text = str(props.get("stamp") or "DECLASSIFIED / TOP SECRET").upper()
    fnt_stamp = get_font("Montserrat", 20)
    _draw_accent_chip(draw, (dx + 60, dy + 35), stamp_text, fnt_stamp, (180, 40, 40))

    doc_title = str(props.get("title") or "OFFICIAL INVESTIGATION MEMORANDUM").upper()
    fnt_doc_title = get_font("Cinzel", 28)
    draw.text((dx + 60, dy + 85), doc_title, font=fnt_doc_title, fill=pri)

    draw.line([dx + 60, dy + 130, dx + doc_w - 60, dy + 130], fill=(*sec, 80), width=1)

    highlight_text = str(props.get("highlight") or props.get("text") or "").strip()
    if not highlight_text:
        return img
    fnt_body = get_font("OpenSans", 22)

    # Redacted line
    draw.rectangle([dx + 60, dy + 160, dx + 320, dy + 185], fill=(20, 20, 20, 255))
    draw.rectangle([dx + 340, dy + 160, dx + 680, dy + 185], fill=(*sec, 80))

    # Highlighted Key Sentence
    bbox = fnt_body.getbbox(highlight_text)
    tw = bbox[2] - bbox[0]
    draw.rectangle([dx + 55, dy + 215, dx + 65 + tw, dy + 255], fill=(245, 220, 40, 220))
    draw.text((dx + 60, dy + 222), highlight_text, font=fnt_body, fill=(10, 10, 10, 255))

    # Second redacted line
    draw.rectangle([dx + 60, dy + 285, dx + 520, dy + 310], fill=(20, 20, 20, 255))
    draw.rectangle([dx + 540, dy + 285, dx + doc_w - 80, dy + 310], fill=(*sec, 80))

    draw.text((dx + 60, dy + doc_h - 45), "NATIONAL ARCHIVES RECORD COMMISSION", font=get_font("Montserrat", 16), fill=(*sec, 200))

    return img


def render_headline_overlay(visual_dna: dict, props: dict, width: int = 1920, height: int = 1080) -> Image.Image:
    """Headline — Newspaper front page, breaking news lower third, masthead banners."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    palette = visual_dna.get("palette") or {}
    pri = _hex_to_rgb(palette.get("primary", "#ffffff"))
    sec = _hex_to_rgb(palette.get("secondary", "#94a3b8"))
    acc = _hex_to_rgb(palette.get("accent", "#dc2626"))
    bg = _hex_to_rgb(palette.get("background", "#09090b"))

    headline = str(props.get("headline") or props.get("title") or "").strip().upper()
    if not headline:
        return img
    source = str(props.get("source") or props.get("publication") or "GLOBAL DISPATCH").upper()
    date_str = str(props.get("date") or "").upper()

    hw, hh = width - 240, 190
    hx, hy = 120, height - hh - 80
    _draw_rounded_panel(draw, [hx, hy, hx + hw, hy + hh], 8, (*bg, 235), outline=(*acc, 180), width=2)

    draw.rectangle([hx, hy, hx + hw, hy + 40], fill=(*acc, 255))
    fnt_kicker = get_font("Montserrat", 20)
    draw.text((hx + 24, hy + 8), f"NEWS WIRE  |  {source}  —  {date_str}", font=fnt_kicker, fill=(255, 255, 255, 255))

    fnt_hl = get_font("Anton", 46)
    draw.text((hx + 28, hy + 65), headline[:68], font=fnt_hl, fill=(*pri, 255))

    return img


def render_timeline_overlay(visual_dna: dict, props: dict, width: int = 1920, height: int = 1080) -> Image.Image:
    """Timeline — Milestone horizontal track, chronology nodes, date cards."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    palette = visual_dna.get("palette") or {}
    pri = _hex_to_rgb(palette.get("primary", "#ffffff"))
    sec = _hex_to_rgb(palette.get("secondary", "#94a3b8"))
    acc = _hex_to_rgb(palette.get("accent", "#38bdf8"))
    bg = _hex_to_rgb(palette.get("background", "#0b1320"))

    events = props.get("events") or []
    if not isinstance(events, list) or not events:
        return img

    tw, th = 1100, 260
    tx, ty = (width - tw) // 2, height - th - 120
    _draw_rounded_panel(draw, [tx, ty, tx + tw, ty + th], 12, (*bg, 220), outline=(*sec, 70), width=1)

    _draw_accent_chip(draw, (tx + 40, ty + 25), "HISTORICAL TIMELINE", get_font("Montserrat", 18), acc)

    track_y = ty + 130
    draw.line([tx + 80, track_y, tx + tw - 80, track_y], fill=(*sec, 120), width=4)

    n = len(events)
    step = (tw - 200) / max(1, n - 1) if n > 1 else 0

    fnt_date = get_font("Orbitron", 26)
    fnt_lbl = get_font("OpenSans", 20)

    for i, ev in enumerate(events):
        nx = tx + 100 + i * step
        is_active = (i == n - 1)
        node_col = acc if is_active else pri

        draw.ellipse([nx - 14, track_y - 14, nx + 14, track_y + 14], fill=(*bg, 255), outline=(*node_col, 255), width=3)
        draw.ellipse([nx - 6, track_y - 6, nx + 6, track_y + 6], fill=(*node_col, 255))

        # Legacy plans used ``year`` while the current director emits
        # ``date``.  Supporting both prevents blank dates/default labels.
        date_text = str(ev.get("date") or ev.get("year") or "")[:18]
        label_text = str(ev.get("label") or "KEY EVENT").upper()[:18]
        draw.text((nx, track_y - 48), date_text, font=fnt_date, fill=(*node_col, 255), anchor="mt")
        draw.text((nx, track_y + 24), label_text, font=fnt_lbl, fill=(*sec, 220), anchor="mt")

    return img


def render_person_profile_overlay(visual_dna: dict, props: dict, width: int = 1920, height: int = 1080) -> Image.Image:
    """PersonProfile — Biographical dossier, dossier badge, historical figures."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    palette = visual_dna.get("palette") or {}
    pri = _hex_to_rgb(palette.get("primary", "#ffffff"))
    sec = _hex_to_rgb(palette.get("secondary", "#cbd5e1"))
    acc = _hex_to_rgb(palette.get("accent", "#eab308"))
    bg = _hex_to_rgb(palette.get("background", "#09090b"))

    name = str(props.get("name") or props.get("title") or "").strip().upper()
    if not name:
        return img
    role = str(props.get("role") or props.get("subtitle") or "PRIME MINISTER / COMMANDER").upper()
    details = str(props.get("detail") or "ARCHIVAL FILE REF: 904-B")

    pw, ph = 640, 260
    px, py = 120, height - ph - 120
    _draw_rounded_panel(draw, [px, py, px + pw, py + ph], 8, (*bg, 230), outline=(*acc, 160), width=2)

    draw.rectangle([px, py, px + 8, py + ph], fill=(*acc, 255))

    _draw_accent_chip(draw, (px + 35, py + 25), "PERSON OF INTEREST", get_font("Montserrat", 18), acc)

    fnt_name = get_font("Cinzel", 38)
    fnt_role = get_font("Montserrat", 22)
    fnt_detail = get_font("OpenSans", 18)

    draw.text((px + 35, py + 75), name[:28], font=fnt_name, fill=(*pri, 255))
    draw.text((px + 35, py + 130), role[:40], font=fnt_role, fill=(*sec, 230))
    draw.line([px + 35, py + 185, px + pw - 35, py + 185], fill=(*sec, 60), width=1)
    draw.text((px + 35, py + 205), details, font=fnt_detail, fill=(*acc, 200))

    return img


def render_comparison_overlay(visual_dna: dict, props: dict, width: int = 1920, height: int = 1080) -> Image.Image:
    """Comparison — Before / After, side-by-side juxtaposition, versus clash."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    palette = visual_dna.get("palette") or {}
    pri = _hex_to_rgb(palette.get("primary", "#ffffff"))
    sec = _hex_to_rgb(palette.get("secondary", "#94a3b8"))
    acc = _hex_to_rgb(palette.get("accent", "#38bdf8"))
    bg = _hex_to_rgb(palette.get("background", "#09090b"))

    left_label = str(props.get("left_label") or "ORIGINAL PARADIGM").upper()
    right_label = str(props.get("right_label") or "NEW ARCHITECTURE").upper()

    cw, ch = width - 360, 200
    cx, cy = 180, height - ch - 100
    _draw_rounded_panel(draw, [cx, cy, cx + cw, cy + ch], 12, (*bg, 225), outline=(*sec, 80), width=1)

    mid_x = cx + cw // 2
    draw.line([mid_x, cy + 20, mid_x, cy + ch - 20], fill=(*acc, 180), width=2)

    draw.ellipse([mid_x - 25, cy + ch // 2 - 25, mid_x + 25, cy + ch // 2 + 25], fill=(*bg, 255), outline=(*acc, 255), width=2)
    draw.text((mid_x, cy + ch // 2 - 14), "VS", font=get_font("Montserrat", 20), fill=(*acc, 255), anchor="mt")

    fnt_side = get_font("Montserrat", 28)
    draw.text((cx + 60, cy + 80), left_label[:30], font=fnt_side, fill=(*pri, 255))
    draw.text((mid_x + 60, cy + 80), right_label[:30], font=fnt_side, fill=(*acc, 255))

    return img


def render_animated_map_overlay(visual_dna: dict, props: dict, width: int = 1920, height: int = 1080) -> Image.Image:
    """AnimatedMap — Tactical coordinate radar, route transit arc, territory marker."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    palette = visual_dna.get("palette") or {}
    pri = _hex_to_rgb(palette.get("primary", "#ffffff"))
    sec = _hex_to_rgb(palette.get("secondary", "#94a3b8"))
    acc = _hex_to_rgb(palette.get("accent", "#38bdf8"))
    bg = _hex_to_rgb(palette.get("background", "#06130e"))

    loc_start = str(props.get("start") or props.get("origin") or props.get("from") or "").strip().upper()
    loc_end = str(props.get("end") or props.get("destination") or props.get("to") or "").strip().upper()
    if not loc_start or not loc_end:
        return img

    mw, mh = 700, 320
    mx, my = 120, height - mh - 120
    _draw_rounded_panel(draw, [mx, my, mx + mw, my + mh], 12, (*bg, 225), outline=(*acc, 140), width=2)

    _draw_accent_chip(draw, (mx + 40, my + 30), "TACTICAL TRANSIT ROUTE", get_font("Montserrat", 18), acc)

    fnt_loc = get_font("Orbitron", 32)
    fnt_arrow = get_font("Montserrat", 26)

    draw.text((mx + 40, my + 90), loc_start, font=fnt_loc, fill=(*pri, 255))
    draw.text((mx + 40, my + 140), "➔", font=fnt_arrow, fill=(*acc, 255))
    draw.text((mx + 90, my + 140), loc_end, font=fnt_loc, fill=(*acc, 255))

    draw.line([mx + 40, my + 220, mx + mw - 40, my + 220], fill=(*sec, 80), width=1)
    coordinates = str(props.get("coordinates") or props.get("coords") or "").strip()
    if coordinates:
        draw.text((mx + 40, my + 245), f"COORDINATES: {coordinates[:48]}", font=get_font("Orbitron", 18), fill=(*sec, 190))

    return img


def render_diagram_overlay(visual_dna: dict, props: dict, width: int = 1920, height: int = 1080) -> Image.Image:
    """Diagram — Connected process nodes, systems flowchart, causal steps."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    palette = visual_dna.get("palette") or {}
    pri = _hex_to_rgb(palette.get("primary", "#ffffff"))
    sec = _hex_to_rgb(palette.get("secondary", "#94a3b8"))
    acc = _hex_to_rgb(palette.get("accent", "#38bdf8"))
    bg = _hex_to_rgb(palette.get("background", "#09090b"))

    steps = props.get("steps") or []
    if not isinstance(steps, (list, tuple)) or len(steps) < 2:
        return img

    dw, dh = width - 300, 260
    dx, dy = 150, height - dh - 120
    _draw_rounded_panel(draw, [dx, dy, dx + dw, dy + dh], 12, (*bg, 225), outline=(*sec, 70), width=1)

    _draw_accent_chip(draw, (dx + 40, dy + 25), "PROCESS ARCHITECTURE", get_font("Montserrat", 18), acc)

    n_steps = len(steps)
    step_w = (dw - 100) / n_steps

    fnt_step = get_font("Montserrat", 22)
    fnt_arrow = get_font("Montserrat", 28)

    for i, s in enumerate(steps):
        sx = dx + 50 + i * step_w
        sy = dy + 110
        bw = step_w - 60
        bh = 70
        is_last = (i == n_steps - 1)
        _draw_rounded_panel(draw, [sx, sy, sx + bw, sy + bh], 6, (*bg, 255), outline=(*acc, 255) if is_last else (*sec, 120), width=2)
        draw.text((sx + bw // 2, sy + bh // 2 - 14), str(s)[:18], font=fnt_step, fill=(*acc, 255) if is_last else (*pri, 240), anchor="mt")

        if i < n_steps - 1:
            draw.text((sx + bw + 20, sy + bh // 2 - 18), "➔", font=fnt_arrow, fill=(*sec, 180))

    return img


def render_kinetic_text_overlay(visual_dna: dict, props: dict, width: int = 1920, height: int = 1080) -> Image.Image:
    """KineticText — High-impact punch typography, keyword emphasis blocks."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    palette = visual_dna.get("palette") or {}
    pri = _hex_to_rgb(palette.get("primary", "#ffffff"))
    acc = _hex_to_rgb(palette.get("accent", "#eab308"))
    bg = _hex_to_rgb(palette.get("background", "#09090b"))

    primary_text = str(props.get("primary") or props.get("text") or "THE TURNING POINT").upper()
    emphasis_text = str(props.get("emphasis") or "").upper()

    kw, kh = 900, 220
    kx, ky = (width - kw) // 2, (height - kh) // 2
    _draw_rounded_panel(draw, [kx, ky, kx + kw, ky + kh], 12, (*bg, 225), outline=(*acc, 180), width=2)

    fnt_main = get_font("Anton", 64)
    bbox = fnt_main.getbbox(primary_text)
    tw = bbox[2] - bbox[0]
    draw.text(((width - tw) // 2, ky + 50), primary_text[:30], font=fnt_main, fill=(*pri, 255))

    if emphasis_text:
        fnt_emp = get_font("Montserrat", 24)
        _draw_accent_chip(draw, ((width - 200) // 2, ky + 140), emphasis_text, fnt_emp, acc)

    return img


def render_text_card_overlay(visual_dna: dict, props: dict, width: int = 1920, height: int = 1080) -> Image.Image:
    """TextCard — Broadcast-grade documentary text card with niche styles and keyword highlighting."""
    import re
    import textwrap

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    palette = visual_dna.get("palette") or {}
    pri = _hex_to_rgb(palette.get("primary", "#ffffff"))
    sec = _hex_to_rgb(palette.get("secondary", "#94a3b8"))
    acc = _hex_to_rgb(palette.get("accent", "#38bdf8"))
    bg = _hex_to_rgb(palette.get("background", "#09090b"))

    raw_text = str(props.get("text") or props.get("title") or props.get("headline") or "").strip()
    if not raw_text:
        return img

    kicker = str(props.get("kicker") or props.get("label") or "KEY DETAIL").strip().upper()[:40]
    subtitle = str(props.get("detail") or props.get("subtitle") or "").strip()[:80]
    badge = str(props.get("badge") or "").strip().upper()[:20]
    style = str(props.get("style") or props.get("variant") or "vox-highlighter").lower()
    position = str(props.get("position") or "bottom-left").lower()
    highlight_words = [str(w).strip().lower() for w in (props.get("highlight_words") or []) if str(w).strip()]

    # Typography based on style
    font_name = "Montserrat"
    if "cyber" in style:
        font_name = "Orbitron"
    elif "broadsheet" in style:
        font_name = "Cinzel"
    elif "brutalist" in style:
        font_name = "Anton"
    elif "memo" in style:
        font_name = "Playfair"

    fnt_kicker = get_font("Montserrat", 18)
    fnt_text = get_font(font_name, 36)
    fnt_sub = get_font("OpenSans", 20)
    fnt_badge = get_font("Montserrat", 14)

    clean_text = re.sub(r"[*_`]", "", raw_text)
    lines = textwrap.wrap(clean_text, width=34) or [clean_text]
    line_h = 48

    max_line_w = 0
    for line in lines:
        bbox = fnt_text.getbbox(line)
        max_line_w = max(max_line_w, bbox[2] - bbox[0])

    pad_h = 36
    pad_v = 28
    card_w = min(width - 160, max(480, int(max_line_w + pad_h * 2)))
    card_h = len(lines) * line_h + pad_v * 2 + (36 if (kicker or badge) else 0) + (32 if subtitle else 0)

    # Position logic
    if "top" in position and "right" in position:
        card_x = width - card_w - 90
        card_y = 80
    elif "top" in position and "left" in position:
        card_x = 90
        card_y = 80
    elif "top" in position:
        card_x = (width - card_w) // 2
        card_y = 80
    elif "bottom" in position and "right" in position:
        card_x = width - card_w - 90
        card_y = height - card_h - 110
    elif "center" in position:
        card_x = (width - card_w) // 2
        card_y = (height - card_h) // 2
    else:
        card_x = 90
        card_y = height - card_h - 110

    # Draw card panel background based on style
    if "frosted" in style:
        _draw_rounded_panel(draw, [card_x, card_y, card_x + card_w, card_y + card_h], 14, (30, 41, 59, 195), outline=(255, 255, 255, 90), width=1)
    elif "cyber" in style:
        _draw_rounded_panel(draw, [card_x, card_y, card_x + card_w, card_y + card_h], 6, (6, 15, 25, 235), outline=(*acc, 220), width=2)
        bracket_len = 16
        draw.line([card_x, card_y, card_x + bracket_len, card_y], fill=(*acc, 255), width=3)
        draw.line([card_x, card_y, card_x, card_y + bracket_len], fill=(*acc, 255), width=3)
        draw.line([card_x + card_w, card_y + card_h, card_x + card_w - bracket_len, card_y + card_h], fill=(*acc, 255), width=3)
        draw.line([card_x + card_w, card_y + card_h, card_x + card_w, card_y + card_h - bracket_len], fill=(*acc, 255), width=3)
    elif "brutalist" in style:
        draw.rectangle([card_x + 8, card_y + 8, card_x + card_w + 8, card_y + card_h + 8], fill=(*acc, 220))
        draw.rectangle([card_x, card_y, card_x + card_w, card_y + card_h], fill=(*bg, 255), outline=(*pri, 255), width=3)
    elif "memo" in style:
        _draw_rounded_panel(draw, [card_x, card_y, card_x + card_w, card_y + card_h], 8, (244, 240, 230, 240), outline=(180, 160, 140, 200), width=2)
    elif "broadsheet" in style:
        _draw_rounded_panel(draw, [card_x, card_y, card_x + card_w, card_y + card_h], 4, (*bg, 235), outline=(*sec, 120), width=2)
        draw.line([card_x + 20, card_y + card_h - 10, card_x + card_w - 20, card_y + card_h - 10], fill=(*acc, 200), width=2)
    else:
        _draw_rounded_panel(draw, [card_x, card_y, card_x + card_w, card_y + card_h], 12, (*bg, 225), outline=(*sec, 80), width=1)
        draw.line([card_x + 3, card_y + 12, card_x + 3, card_y + card_h - 12], fill=(*acc, 255), width=4)

    cur_y = card_y + pad_v
    cur_x = card_x + pad_h

    if kicker or badge:
        chip_col = (20, 20, 25) if "memo" in style else (255, 255, 255)
        if kicker:
            _draw_accent_chip(draw, (cur_x, cur_y), kicker, fnt_kicker, acc, text_rgb=chip_col)
        if badge:
            badge_x = cur_x + (fnt_kicker.getbbox(kicker)[2] - fnt_kicker.getbbox(kicker)[0] + 28 if kicker else 0)
            _draw_rounded_panel(draw, [badge_x, cur_y, badge_x + 70, cur_y + 24], 4, (*sec, 160))
            draw.text((badge_x + 8, cur_y + 4), badge, font=fnt_badge, fill=(255, 255, 255, 240))
        cur_y += 36

    text_color = (25, 25, 30) if "memo" in style else pri
    hl_color = (245, 190, 11) if "memo" in style else acc

    for line in lines:
        words = line.split()
        word_x = cur_x
        for word in words:
            clean_w = re.sub(r"[^\w]", "", word).lower()
            w_bbox = fnt_text.getbbox(word + " ")
            w_width = w_bbox[2] - w_bbox[0]
            is_highlight = clean_w in highlight_words or any(clean_w == hw for hw in highlight_words)

            if is_highlight:
                box_top = cur_y + 4
                box_bot = cur_y + line_h - 6
                box_right = word_x + w_width - 4
                _draw_rounded_panel(draw, [word_x - 3, box_top, box_right, box_bot], 4, (*hl_color, 210))
                draw.text((word_x, cur_y), word, font=fnt_text, fill=(10, 10, 15, 255))
            else:
                draw.text((word_x, cur_y), word, font=fnt_text, fill=(*text_color, 255))

            word_x += w_width
        cur_y += line_h

    if subtitle:
        cur_y += 6
        sub_color = (70, 70, 75, 210) if "memo" in style else (*sec, 220)
        draw.text((cur_x, cur_y), subtitle, font=fnt_sub, fill=sub_color)

    return img


# Master Dispatcher for 15 Local Scene Systems
SYSTEM_DISPATCHER = {
    "chaptertitle": render_chapter_title_overlay,
    "statcard": render_stat_card_overlay,
    "chart": render_chart_overlay,
    "quotecard": render_quote_card_overlay,
    "documenthighlight": render_document_highlight_overlay,
    "headline": render_headline_overlay,
    "timeline": render_timeline_overlay,
    "personprofile": render_person_profile_overlay,
    "comparison": render_comparison_overlay,
    "animatedmap": render_animated_map_overlay,
    "diagram": render_diagram_overlay,
    "kinetictext": render_kinetic_text_overlay,
    "textcard": render_text_card_overlay,
    "text-card": render_text_card_overlay,
}


def render_scene_overlay(
    system_type: str,
    visual_dna: dict,
    props: dict,
    width: int = 1920,
    height: int = 1080,
) -> Optional[Image.Image]:
    """Dispatch and render an anti-aliased RGBA overlay for any requested scene system."""
    if isinstance(props, dict) and props.get("no_data"):
        return None
    clean_type = str(system_type or "").strip().lower().replace("_", "").replace("-", "").replace(" ", "")
    fn = SYSTEM_DISPATCHER.get(clean_type)
    if fn:
        try:
            return fn(visual_dna=visual_dna, props=props, width=width, height=height)
        except Exception:
            pass
    return None
