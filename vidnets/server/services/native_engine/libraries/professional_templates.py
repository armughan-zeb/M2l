"""Restrained editorial FFmpeg templates.

These templates intentionally avoid full-frame cards, oversized kinetic text,
and decorative overlays. They use a small number of typographic elements,
consistent safe-area placement, and one accent rule or marker per composition.
The public IDs remain the existing VOX IDs so manifests and routing stay stable.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict

from PIL import ImageFont

try:
    from ...text_position_contract import GUIDE_HEIGHT, guide_anchor, normalize_text_position
except (ImportError, ValueError):
    try:
        from services.text_position_contract import GUIDE_HEIGHT, guide_anchor, normalize_text_position
    except (ImportError, ValueError):
        from text_position_contract import GUIDE_HEIGHT, guide_anchor, normalize_text_position


DISPLAY = "Poppins-Bold.ttf"
SANS = "Poppins-Bold.ttf"
BODY = "Poppins-Bold.ttf"
SERIF = "Poppins-Bold.ttf"
MONO = "OpenSans-Bold.ttf"
SAFE_X = "w*0.06"
SAFE_RIGHT_X = "w-text_w-w*0.06"
SAFE_RIGHT_W = "w*0.28"


def _esc(value: Any, limit: int = 64) -> str:
    text = str(value or "")[:limit]
    return (text.replace("\\", "")
        .replace(":", "\\:")
        .replace("'", "")
        .replace("%", "%%"))


def _upper(value: Any, fallback: str = "") -> str:
    return str(value or fallback).strip().upper()


def _color(value: str, fallback: str = "#f2f0e8") -> str:
    value = str(value or fallback)
    alpha = ""
    if "@" in value:
        value, alpha = value.split("@", 1)
        alpha = "@" + alpha
    if value.startswith("#"):
        raw = value[1:]
        if len(raw) == 3:
            raw = "".join(ch * 2 for ch in raw)
        if len(raw) == 6:
            value = f"0x{raw}"
    return f"{value}{alpha}"


def _size(text: Any, base: int, limit: int = 34) -> int:
    length = max(1, len(str(text or "")))
    return max(int(base * 0.52), int(base * min(1.0, limit / length)))


def _corner_coords(
    position: str,
    line_index: int = 0,
    total_lines: int = 1,
    line_spacing: int = 56,
) -> tuple[str, str]:
    """Calculate strict FFmpeg drawtext coordinates for middle/center and 4 corners only.
    
    Anchors:
      - 'center' / 'middle': exact screen center, vertically grouped if multi-line.
      - 'top_left': top-left safe corner (6% left margin, 8% top margin).
      - 'top_right': top-right safe corner (6% right margin, 8% top margin).
      - 'bottom_left': bottom-left safe corner (6% left margin, 10% bottom margin).
      - 'bottom_right': bottom-right safe corner (6% right margin, 10% bottom margin).
    """
    pos = normalize_text_position(position)
    
    # Horizontal expression
    if pos == "center":
        x = "(w-text_w)/2"
    elif pos in ("top_right", "bottom_right"):
        x = "w-text_w-w*0.06"
    else:  # top_left, bottom_left
        x = "w*0.06"
        
    # Vertical expression
    if pos == "center":
        if total_lines <= 1:
            y = "(h-text_h)/2"
        else:
            offset = int((line_index - (total_lines - 1) / 2.0) * line_spacing)
            if offset > 0:
                y = f"(h-text_h)/2+{offset}"
            elif offset < 0:
                y = f"(h-text_h)/2{offset}"
            else:
                y = "(h-text_h)/2"
    elif pos in ("top_left", "top_right"):
        offset = int(line_index * line_spacing)
        if offset > 0:
            y = f"h*0.08+{offset}"
        else:
            y = "h*0.08"
    else:  # bottom_left, bottom_right
        offset = int((total_lines - 1 - line_index) * line_spacing)
        if offset > 0:
            y = f"h-text_h-h*0.10-{offset}"
        else:
            y = "h-text_h-h*0.10"
            
    return x, y


def _dt(text: Any, font: str, size: int, color: str, x: str, y: str,
        *, limit: int = 64, shadow: bool = True, enable: str = "",
        box: bool = True, box_color: str = "0x0a0f1d@0.88", boxborderw: int = 14) -> str:
    text_value = _esc(text, limit)
    # drawtext evaluates x/y as naked libavutil expressions — single-quotes
    # cause "Undefined constant or missing '('" parse errors. Strip them.
    x = x.strip("'")
    y = y.strip("'")
    # CRITICAL: drawtext uses w/h for video dimensions. iw/ih are drawbox-specific
    # and cause "Undefined constant" errors in drawtext on this FFmpeg build.
    x = re.sub(r'\biw\b', 'w', x)
    x = re.sub(r'\bih\b', 'h', x)
    y = re.sub(r'\biw\b', 'w', y)
    y = re.sub(r'\bih\b', 'h', y)
    parts = [
        f"text='{text_value}'",
        f"fontfile='{font}'",
        f"fontsize={_size(text_value, size)}",
        f"fontcolor={_color(color)}",
        f"x={x}",
        f"y={y}",
    ]
    # Restrained drop shadow
    parts.append("shadowcolor=black@0.72:shadowx=2:shadowy=2")
    if enable:
        parts.append(enable)
    if box:
        parts.append(f"box=1:boxcolor={_color(box_color)}:boxborderw={int(boxborderw)}")
    return "drawtext=" + ":".join(parts)


def _rule(x: str, y: str, width: str, color: str, height: int = 4) -> str:
    # Decorative lines/bars disabled per design specification to avoid floating visual artifacts
    return ""


def _base(palette: dict):
    raw_accent = str(palette.get("accent") or "#d8a84e")
    niche = str(palette.get("niche") or "").lower()
    if niche not in {"gaming", "technology", "cyberpunk", "sports"} and not bool(palette.get("allow_cool_accents", False)):
        if raw_accent.startswith("#") and len(raw_accent) in (4, 7):
            raw = raw_accent[1:]
            if len(raw) == 3:
                raw = "".join(ch * 2 for ch in raw)
            if len(raw) == 6:
                red, green, blue = int(raw[:2], 16), int(raw[2:4], 16), int(raw[4:], 16)
                if blue > red * 1.15 and blue > green * 1.05:
                    raw_accent = "#d8a84e"
    # Dark accents disappear against dark footage; lift the luminance (keep
    # the hue) so overlay text stays legible. Mirrors _legible() in the
    # semantic renderer for the Pillow overlay path.
    if raw_accent.startswith("#") and len(raw_accent) == 7:
        red, green, blue = int(raw_accent[1:3], 16), int(raw_accent[3:5], 16), int(raw_accent[5:7], 16)
        luma = 0.2126 * red + 0.7152 * green + 0.0722 * blue
        if luma < 150:
            if max(red, green, blue) - min(red, green, blue) < 24:
                raw_accent = "#f4f0e6"
            else:
                k = (150.0 - luma) / max(1.0, 255.0 - luma)
                raw_accent = "#{:02x}{:02x}{:02x}".format(
                    min(255, int(red + (255 - red) * k)),
                    min(255, int(green + (255 - green) * k)),
                    min(255, int(blue + (255 - blue) * k)),
                )
    return (_color("#ffffff", "#ffffff"),
            _color(raw_accent, "#d8a84e"),
            _color("#e7ebef", "#e7ebef"),
            _color(palette.get("danger"), "#c94a3d"))


def _anim_enable(delay: float = 0.0) -> str:
    """Delayed-entry enable expression for drawtext (escaped comma, no quotes)."""
    if delay <= 0:
        return ""
    return f"enable=gte(t\\,{delay:.2f})"


def _slide_up_y(target_y: str, delay: float = 0.0, dur: float = 0.5, px: int = 120) -> str:
    """y expression sliding text up into target_y after a delay (comma-escaped for drawtext)."""
    rate = px / max(0.1, dur)
    # drawtext requires commas inside expressions to be escaped as \,
    # and does NOT support single-quote wrapping for x/y values.
    return f"min({target_y}\\,{target_y}-{px}+(t-{delay:.2f})*{rate:.0f})"


def _slide_in_right(target_x: str, delay: float = 0.0, dur: float = 0.5, px: int = 140) -> str:
    """x expression sliding text in from the right into target_x after a delay (comma-escaped for drawtext)."""
    rate = px / max(0.1, dur)
    # drawtext requires commas inside expressions to be escaped as \,
    # and does NOT support single-quote wrapping for x/y values.
    return f"max({target_x}\\,{target_x}+{px}-(t-{delay:.2f})*{rate:.0f})"


def _box(x: str, y: str, w: str, h: str, color: str, thick: str = "fill") -> str:
    """drawbox with expression-safe quoting (no commas in position/size).

    ``h``/``w`` inside drawbox mean the box's own size, so standalone h/w in
    position expressions are normalized to ih/iw (video dimensions).
    """
    def expr(value: Any) -> str:
        value = str(value)
        value = re.sub(r"(?<![A-Za-z])([hw])(?![A-Za-z])", lambda m: {"h": "ih", "w": "iw"}[m.group(1)], value)
        if any(token in value for token in ("iw", "ih", "w*", "h*", "/", "*", "+", "-", "(")) and not value.startswith("'"):
            return f"'{value}'"
        return value
    return f"drawbox=x={expr(x)}:y={expr(y)}:w={expr(w)}:h={expr(h)}:color={_color(color)}:t={thick}"


def _sweep_bar(x: str, y: str, max_w: str, color: str, delay: float = 0.3,
               dur: float = 0.5, height: int = 4) -> str:
    """drawbox bar that sweeps left-to-right starting at delay (per-frame eval)."""
    y = re.sub(r"(?<![A-Za-z])([hw])(?![A-Za-z])", lambda m: {"h": "ih", "w": "iw"}[m.group(1)], str(y))
    w = f"'max(6\\,({max_w})*min(1\\,(t-{delay:.2f})/{dur:.2f}))'"
    return f"drawbox=x={x}:y={y}:w={w}:h={height}:color={_color(color)}:t=fill"


def _contract_layout(position: str) -> Dict[str, str]:
    """Return the exact normalized anchor copied from the placement guide."""
    anchor = guide_anchor(position)
    return {
        "x": f"iw*{anchor['x']:.6f}",
        "y": f"ih*{anchor['y']:.6f}",
        "horizontal": anchor["horizontal"],
        "micro_y": f"ih*{anchor['rect']['top'] / GUIDE_HEIGHT:.6f}",
        "position": normalize_text_position(position),
    }


def _contract_fill(palette: dict, mode: str, accent: str, danger: str) -> str:
    """Resolve a profile-aware card fill, or return an empty fill."""
    mode = str(mode or "none").strip().lower()
    if mode == "surface":
        return f"{_color(palette.get('surface') or palette.get('background') or '#0d1117')}@0.90"
    if mode == "accent":
        return f"{_color(accent)}@0.92"
    if mode == "danger":
        return f"{_color(danger)}@0.92"
    return ""


def _contract_text_color(mode: str, primary: str) -> str:
    """Use dark ink on bright accent cards and white ink everywhere else."""
    return "0x101318" if str(mode or "").lower() in {"accent", "danger"} else primary


def _contract_font_path(font: str) -> str:
    path = Path(str(font))
    if path.is_file():
        return str(path)
    return str(Path(__file__).resolve().parents[4] / "fonts" / path.name)


def _contract_metrics(text: str, font: str, size: int) -> tuple[int, int]:
    try:
        metrics_font = ImageFont.truetype(_contract_font_path(font), size)
        bbox = metrics_font.getbbox(text)
        return max(1, bbox[2] - bbox[0]), max(1, bbox[3] - bbox[1])
    except (OSError, ValueError):
        return max(1, int(len(text) * size * 0.55)), max(1, int(size * 0.92))


def _contract_template(palette: dict, props: dict) -> dict:
    """Render the active semantic text contract.

    The old library exposed dozens of compositions, but many of them rendered
    the same generic kicker, oversized panel, and long rule. The semantic path
    now uses this small editorial grammar: one meaningful heading, an optional
    archive micro-label, a compact profile-colored card, and one short animated
    underline.
    """
    primary, accent, secondary, danger = _base(palette)
    raw_text = str(props.get("_contract_text") or props.get("text") or props.get("title") or "").strip()
    transform = str(props.get("_text_transform") or "uppercase").lower()
    text = raw_text.upper() if transform == "uppercase" else raw_text
    if not text:
        return {"chains": [], "treatment": "hold", "cost": 1}

    position = normalize_text_position(props.get("_position") or props.get("position") or "center")
    kind = str(props.get("_contract_kind") or "highlight").lower()
    # Generic contracts are also used by older storyboard producers that do
    # not carry an explicit size. Keep those cards legible at 1080p while
    # still allowing an operator supplied size to win within a safe range.
    base_size = int(props.get("_contract_size") or {
        "date": 110,
        "stat": 118,
        "quote": 64,
        "person": 76,
        "location": 76,
        "archive": 82,
        "chapter": 104,
        "cta": 82,
    }.get(kind, 72))
    base_size = max(52, min(170, base_size))
    text_limit = 42 if kind == "date" else 48
    text = text[:text_limit]

    contract_font = props.get("_contract_font", SANS)
    text_color = props.get("_card_text_color") or primary

    # The same contracts render on landscape and portrait canvases. A size
    # that is safe at 1920px can overflow a 1080px Shorts canvas (for example
    # ``CITIES MUST UPGRADE POWER``), producing a clipped heading even though
    # the media itself is valid. Scale the authored size against the actual
    # render width carried by the renderer and leave a comfortable side margin.
    try:
        render_width = max(320, int(props.get("_render_width") or 1920))
    except (TypeError, ValueError):
        render_width = 1920
    max_text_width = max(240, int(render_width * 0.82))
    measured_preview, _ = _contract_metrics(text, contract_font, base_size)
    while measured_preview > max_text_width and base_size > 42:
        base_size -= 2
        measured_preview, _ = _contract_metrics(text, contract_font, base_size)
    if measured_preview > max_text_width:
        scale = max_text_width / max(1, measured_preview)
        base_size = max(36, int(base_size * scale))

    micro = _upper(props.get("_micro_label"))
    total_lines = 2 if micro else 1
    spacing = max(44, int(base_size * 0.85))

    title_x, title_y = _corner_coords(position, line_index=1 if micro else 0, total_lines=total_lines, line_spacing=spacing)
    chains = []

    measured_w, measured_h = _contract_metrics(text, contract_font, base_size)
    horizontal_pad = max(24, int(base_size * 0.36))
    vertical_pad = max(12, int(base_size * 0.16))
    card_mode = str(props.get("_card_mode") or "none").strip().lower()
    fill = _contract_fill(palette, card_mode, accent, danger)

    def _drawbox_expr(value: Any) -> str:
        """Translate drawtext-only dimensions before using an expression in drawbox.

        ``title_x``/``title_y`` are authored for drawtext and legitimately use
        ``w-text_w`` and ``h-text_h``.  FFmpeg's drawbox filter does not expose
        ``text_w``/``text_h`` (and uses ``iw``/``ih`` for the frame), so passing
        the expression through verbatim makes the QSV and software encoders
        fail with ``Undefined constant ... text_w``.  The measured text box is
        already known here; substitute those values and keep the frame aliases
        expression-safe for drawbox.
        """
        expression = str(value or "")
        expression = re.sub(r"(?<![A-Za-z0-9_])text_w(?![A-Za-z0-9_])", str(measured_w), expression)
        expression = re.sub(r"(?<![A-Za-z0-9_])text_h(?![A-Za-z0-9_])", str(measured_h), expression)
        expression = re.sub(r"(?<![A-Za-z0-9_])w(?![A-Za-z0-9_])", "iw", expression)
        expression = re.sub(r"(?<![A-Za-z0-9_])h(?![A-Za-z0-9_])", "ih", expression)
        return expression

    if fill:
        card_w = measured_w + horizontal_pad * 2
        card_h = min(210, max(56, measured_h + vertical_pad * 2))
        card_x = f"({_drawbox_expr(title_x)})-{horizontal_pad}"
        card_y = f"({_drawbox_expr(title_y)})-{vertical_pad}"
        chains.append(f"drawbox=x={card_x}:y={card_y}:w={card_w}:h={card_h}:color={fill}:t=fill")

    if props.get("_show_rule"):
        rule_w = f"({measured_w + 18})"
        rule_x = _drawbox_expr(title_x)
        rule_y = f"({_drawbox_expr(title_y)})+{base_size}+8"
        chains.append(f"drawbox=x={rule_x}:y={rule_y}:w={rule_w}:h=3:color={_color(accent)}:t=fill")

    if micro:
        mx, my = _corner_coords(position, line_index=0, total_lines=total_lines, line_spacing=spacing)
        chains.append(_dt(micro, MONO, 16, accent, mx, my, limit=20,
                          shadow=False, enable=_anim_enable(0.06),
                          box=not fill, box_color="0x0a0f1d@0.88", boxborderw=8))

    chains.append(_dt(text, contract_font, base_size, text_color,
                      title_x, title_y, limit=text_limit,
                      shadow=not fill, enable=_anim_enable(0.16),
                      box=not fill, box_color="0x0a0f1d@0.88", boxborderw=14))

    # Word-level highlight track comes directly from Whisper.
    word_cues = props.get("_word_cues") if isinstance(props.get("_word_cues"), list) else []
    if word_cues:
        configured_highlights = {
            str(token).lower().strip(".,!?;:\"'()[]")
            for token in (props.get("_highlight_tokens") or [])
            if str(token).strip()
        }
        rendered_size = _size(text, base_size)
        cursor = 0
        words = text.split()
        for index, word in enumerate(words):
            word_width, _ = _contract_metrics(word, contract_font, rendered_size)
            cue = word_cues[index] if index < len(word_cues) and isinstance(word_cues[index], dict) else None
            if cue:
                try:
                    start = max(0.0, float(cue.get("start", 0.0)))
                    end = max(start + 0.02, float(cue.get("end", start + 0.02)))
                except (TypeError, ValueError):
                    start, end = 0.0, 0.02
                word_enable = f"enable=between(t\\,{start:.3f}\\,{end:.3f})"
                word_x = f"({title_x})+{cursor}"
                normalized_word = word.lower().strip(".,!?;:\"'()[]")
                if not configured_highlights or normalized_word in configured_highlights:
                    chains.append(_dt(
                        word, contract_font, base_size, "#101318",
                        word_x, title_y, limit=max(8, len(word)), shadow=False,
                        enable=word_enable, box=True, box_color=f"{_color(accent)}@0.92",
                        boxborderw=max(4, int(rendered_size * 0.12)),
                    ))
            cursor += word_width + max(8, int(rendered_size * 0.22))

    return {"chains": chains, "treatment": "hold", "cost": 1}


# ────────────────────────────────────────────────────────────────
# Entry-motion library: fade in/out, zoom in/out, rise, slides.
# Zoom is simulated with a two-layer size cross-fade because this
# FFmpeg build crashes on fontsize expressions (drawtext alpha
# expressions DO work and are used everywhere below).
# ────────────────────────────────────────────────────────────────

# Weighted toward zoom-in and fade-in per editorial preference.
_MOTION_PALETTE = (
    ["zoom_in"] * 3
    + ["fade_in"] * 3
    + ["rise"] * 2
    + ["slide_left", "slide_right", "zoom_out", "fade_out"]
)
_MOTION_ZOOM_IN_FACTOR = 0.60
_MOTION_ZOOM_OUT_FACTOR = 1.45
_MOTION_ENTRY_DUR = 0.34
_MOTION_EXIT_DUR = 0.32


def _alpha_ramp(start: float, dur: float = _MOTION_ENTRY_DUR) -> str:
    """Alpha ramp from 0 to 1 after start (comma-escaped for drawtext)."""
    return f"min(max((t-{start:.3f})/{dur:.3f}\\,0)\\,1)"


def _alpha_fade_out(end: float, dur: float = _MOTION_EXIT_DUR) -> str:
    """Alpha ramp back to 0 before the scene end (comma-escaped)."""
    return f"if(lt(t\\,{end - dur:.3f})\\,1\\,max(0\\,({end:.3f}-t)/{dur:.3f}))"


def _entry_animation(seed_key: str, index: int) -> str:
    """Deterministic per-template animation mix."""
    return _MOTION_PALETTE[hash((seed_key, index)) % len(_MOTION_PALETTE)]


def _replace_field(fields: list, prefix: str, value: str) -> list:
    """Replace the first field starting with prefix, appending if absent."""
    for idx, field in enumerate(fields):
        if field.startswith(prefix):
            fields[idx] = f"{prefix}={value}"
            return fields
    fields.append(f"{prefix}={value}")
    return fields


def _set_enable(fields: list, start: float) -> list:
    if not any(f.startswith("enable=") for f in fields):
        fields.append(f"enable=gte(t\\,{start:.2f})")
    return fields


_FORCED_ENTRY_ANIMATIONS = {
    # Profile language -> the concrete animation primitives supported by the
    # FFmpeg drawtext builder. Keep the public profile vocabulary expressive,
    # but never pass arbitrary model text into a filter expression.
    "slide_left": "slide_left",
    "slide_right": "slide_right",
    "wipe": "slide_left",
    "rise": "rise",
    "fade": "fade_in",
    "pop": "zoom_in",
    "stamp": "zoom_in",
    "paper": "fade_in",
    "data": "rise",
}


def _animated_text_fields(chain: str, seed_key: str, text_idx: int, delay: float,
                          scene_duration: float | None,
                          forced_motion: str = "") -> tuple:
    """Apply one mixed entry animation to a static drawtext chain.

    Returns (layers, text_idx) where ``layers`` is the list of resulting
    chain strings (zoom keeps two cross-faded layers, everything else one).
    """
    fields = re.split(r"(?<!\\):", chain)
    x_field = next((f for f in fields if f.startswith("x=")), None)
    y_field = next((f for f in fields if f.startswith("y=")), None)
    size_field = next((f for f in fields if f.startswith("fontsize=")), None)
    if not x_field or not y_field or not size_field:
        return [chain], text_idx
    anim = _FORCED_ENTRY_ANIMATIONS.get(str(forced_motion or "").strip().lower())
    if not anim:
        anim = _entry_animation(seed_key, text_idx)
    if anim == "fade_out" and not scene_duration:
        anim = "fade_in"
    x_val = x_field[2:]
    y_val = y_field[2:]
    try:
        base_size = int(float(size_field[len("fontsize="):].strip("'")))
    except (TypeError, ValueError):
        base_size = 0
    slide_progress = _alpha_ramp(delay)
    alpha_entry = _alpha_ramp(delay)
    if scene_duration:
        alpha_entry = f"({alpha_entry})*({_alpha_fade_out(scene_duration)})"
    enable = f"enable=gte(t\\,{delay:.2f})"
    if anim == "zoom_in":
        small_size = max(12, int(base_size * _MOTION_ZOOM_IN_FACTOR)) if base_size else base_size
        small = ":".join(_set_enable(_replace_field(
            _replace_field(list(fields), "fontsize", str(small_size)),
            "alpha", f"(1-({slide_progress}))*({alpha_entry})"), delay))
        main = ":".join(_set_enable(_replace_field(list(fields), "alpha", alpha_entry), delay))
        return [small, main], text_idx
    if anim == "zoom_out":
        large_size = int(base_size * _MOTION_ZOOM_OUT_FACTOR) if base_size else base_size
        large = ":".join(_set_enable(_replace_field(
            _replace_field(list(fields), "fontsize", str(large_size)),
            "alpha", f"(1-({slide_progress}))*({alpha_entry})"), delay))
        main = ":".join(_set_enable(_replace_field(list(fields), "alpha", alpha_entry), delay))
        return [large, main], text_idx
    if anim == "fade_in" or anim == "fade_out":
        return [":".join(_set_enable(_replace_field(list(fields), "alpha", alpha_entry), delay))], text_idx
    if anim == "rise":
        return [":".join(_set_enable(
            _replace_field(_replace_field(list(fields), "y", _slide_up_y(y_val, delay, dur=0.42, px=96)),
                           "alpha", alpha_entry), delay))], text_idx
    if anim == "slide_left":
        x_new = f"-text_w+({slide_progress})*(({x_val})+text_w)"
        return [":".join(_set_enable(
            _replace_field(_replace_field(list(fields), "x", x_new), "alpha", alpha_entry), delay))], text_idx
    if anim == "slide_right":
        x_new = f"w-({slide_progress})*(w-({x_val}))"
        return [":".join(_set_enable(
            _replace_field(_replace_field(list(fields), "x", x_new), "alpha", alpha_entry), delay))], text_idx
    return [chain], text_idx


def _motion_chains(chains: list, seed_key: str = "template", stagger: float = 0.12,
                   px: int = 110, scene_duration: float | None = None,
                   forced_motion: str = "") -> list:
    """Give static chains mixed entry motion: staggered, varied animations.

    Animations are chosen per text element from a weighted palette (zoom-in
    and fade-in dominate), seeded by template name so every template gets a
    stable but different mix. Chains that already contain time expressions
    (map_card, step_progress, money counters) are left untouched. Fields are
    split on ':' that is not escaped, since drawtext values may contain
    '\\:' (text, %{eif...} counters).
    """
    out: list = []
    text_idx = 0
    for chain in chains:
        if not chain:
            continue
        fields = re.split(r"(?<!\\):", chain)
        if chain.startswith("drawtext="):
            y_field = next((f for f in fields if f.startswith("y=")), None)
            has_enable = any(f.startswith("enable=") for f in fields)
            # ``text_h`` contains the letter ``t`` but is not a temporal
            # expression. Only skip chains that reference FFmpeg's standalone
            # time variable, otherwise centered headlines never animate.
            has_time_variable = bool(re.search(r"(?<![A-Za-z0-9_])t(?![A-Za-z0-9_])", y_field or ""))
            if y_field and not has_time_variable and not has_enable:
                delay = round(text_idx * stagger, 2)
                layers, text_idx = _animated_text_fields(
                    chain, seed_key, text_idx, delay, scene_duration,
                    forced_motion=forced_motion,
                )
                out.extend(layers)
            else:
                out.append(chain)
            text_idx += 1
        elif chain.startswith("drawbox="):
            w_field = next((f for f in fields if f.startswith("w=")), None)
            h_field = next((f for f in fields if f.startswith("h=")), None)
            if w_field and h_field:
                w_val = w_field[2:].strip("'")
                h_val = h_field[2:].strip("'")
                if h_val.isdigit() and int(h_val) <= 8 and "iw" in w_val and "t" not in w_val:
                    delay = round(text_idx * stagger, 2)
                    fields = [f"w='max(6\\,({w_val})*min(1\\,(t-{delay:.2f})/{_MOTION_ENTRY_DUR:.2f}))'" if f is w_field else f
                              for f in fields]
                    chain = ":".join(fields)
            out.append(chain)
        else:
            out.append(chain)
    return out


def _animated(fn):
    """Wrap a template builder so its chains always get mixed entry motion."""
    def wrapped(palette: dict, props: dict, scene_duration: float | None = None) -> dict:
        # EditorialCueV2 keeps typed templates typed.  The previous adapter
        # replaced date, statistic, quote, person, location and listicle
        # compositions with the same generic heading card as soon as an
        # editorial contract was present.  Only a genuine generic highlight
        # opts into that compact fallback now.
        use_generic = props.get("_use_generic_contract")
        if use_generic is None:
            use_generic = int(props.get("_cue_schema_version") or 0) < 2
        if props.get("_editorial_contract") and use_generic:
            result = _contract_template(palette, props)
        else:
            result = fn(palette, props)
        chains = result.get("chains")
        if chains:
            result["chains"] = _motion_chains(
                chains,
                seed_key=fn.__name__,
                scene_duration=scene_duration,
                forced_motion=str(props.get("_entry_motion") or ""),
            )
        return result
    return wrapped


def professional_chapter(palette: dict, props: dict) -> dict:
    primary, accent, secondary, _ = _base(palette)
    title = props.get("title") or props.get("text") or "THE STORY"
    kicker = props.get("kicker") or "CHAPTER"
    subtitle = props.get("subtitle") or props.get("detail") or ""
    pos = normalize_text_position(props.get("_position") or props.get("position") or "center")
    lines = [kicker, title] + ([subtitle] if subtitle else [])
    total = len(lines)
    kx, ky = _corner_coords(pos, 0, total, line_spacing=48)
    tx, ty = _corner_coords(pos, 1, total, line_spacing=68)
    chains = [
        _dt(_upper(kicker), MONO, 18, accent, kx, ky, limit=28),
        _dt(_upper(title), DISPLAY, 64, primary, tx, ty, limit=42),
    ]
    if subtitle:
        sx, sy = _corner_coords(pos, 2, total, line_spacing=52)
        chains.append(_dt(_upper(subtitle), BODY, 22, secondary, sx, sy, limit=70))
    return {"chains": chains, "treatment": "editorial_crop", "cost": 1}


def professional_headline(palette: dict, props: dict) -> dict:
    primary, accent, secondary, _ = _base(palette)
    text = props.get("text") or props.get("title") or "THE KEY IDEA"
    detail = props.get("detail") or props.get("subtitle") or ""
    pos = normalize_text_position(props.get("_position") or props.get("position") or "center")
    total = 2 if detail else 1
    tx, ty = _corner_coords(pos, 0, total, line_spacing=64)
    chains = [
        _dt(_upper(text), SANS, 56, primary, tx, ty, limit=40),
    ]
    if detail:
        dx, dy = _corner_coords(pos, 1, total, line_spacing=64)
        chains.append(_dt(_upper(detail), BODY, 22, secondary, dx, dy, limit=80))
    return {"chains": chains, "treatment": "hold", "cost": 1}


def professional_lower_third(palette: dict, props: dict) -> dict:
    primary, accent, secondary, _ = _base(palette)
    title = props.get("title") or props.get("name") or props.get("text") or ""
    subtitle = props.get("subtitle") or props.get("detail") or ""
    pos = normalize_text_position(props.get("_position") or props.get("position") or "bottom_left")
    total = 2 if subtitle else 1
    tx, ty = _corner_coords(pos, 0, total, line_spacing=54)
    chains = [
        _dt(_upper(title), SANS, 42, primary, tx, ty, limit=38),
    ]
    if subtitle:
        sx, sy = _corner_coords(pos, 1, total, line_spacing=54)
        chains.append(_dt(_upper(subtitle), BODY, 20, secondary, sx, sy, limit=58))
    return {"chains": chains, "treatment": "hold", "cost": 1}


def professional_date(palette: dict, props: dict) -> dict:
    primary, accent, secondary, _ = _base(palette)
    date = props.get("date") or props.get("value") or props.get("title") or "1969"
    is_v2 = int(props.get("_cue_schema_version") or 0) >= 2
    title = props.get("title") or props.get("detail") or ("" if is_v2 else "A TURNING POINT")
    pos = normalize_text_position(props.get("_position") or props.get("position") or "top_left")
    total = 2 if title else 1
    dx, dy = _corner_coords(pos, 0, total, line_spacing=72)
    chains = [
        _dt(_upper(date), DISPLAY, 72, primary, dx, dy, limit=18),
    ]
    if title:
        tx, ty = _corner_coords(pos, 1, total, line_spacing=52)
        chains.append(_dt(_upper(title), MONO, 20, secondary, tx, ty, limit=42))
    return {"chains": chains, "treatment": "film_gate", "cost": 1}


def professional_stat(palette: dict, props: dict) -> dict:
    primary, accent, secondary, _ = _base(palette)
    value = props.get("value") or props.get("title") or "0"
    is_v2 = int(props.get("_cue_schema_version") or 0) >= 2
    label = props.get("label") or props.get("subtitle") or ("" if is_v2 else "THE FIGURE")
    detail = props.get("detail") or ""
    pos = normalize_text_position(props.get("_position") or props.get("position") or "center")
    total = 1 + (1 if label else 0) + (1 if detail else 0)
    vx, vy = _corner_coords(pos, 0, total, line_spacing=80)
    chains = [
        _dt(_upper(value), DISPLAY, 88, primary, vx, vy, limit=18),
    ]
    idx = 1
    if label:
        lx, ly = _corner_coords(pos, idx, total, line_spacing=50)
        chains.append(_dt(_upper(label), MONO, 20, accent, lx, ly, limit=34))
        idx += 1
    if detail:
        dx, dy = _corner_coords(pos, idx, total, line_spacing=46)
        chains.append(_dt(_upper(detail), BODY, 20, secondary, dx, dy, limit=70))
    return {"chains": chains, "treatment": "zoom_slow", "cost": 1}


def professional_quote(palette: dict, props: dict) -> dict:
    primary, accent, secondary, _ = _base(palette)
    quote = props.get("quote") or props.get("text") or props.get("title") or ""
    attribution = props.get("attribution") or props.get("detail") or ""
    pos = normalize_text_position(props.get("_position") or props.get("position") or "center")
    total = 2 if attribution else 1
    qx, qy = _corner_coords(pos, 0, total, line_spacing=62)
    chains = [
        _dt(quote, SANS, 42, primary, qx, qy, limit=85),
    ]
    if attribution:
        ax, ay = _corner_coords(pos, 1, total, line_spacing=50)
        chains.append(_dt("- " + _upper(attribution), BODY, 20, secondary, ax, ay, limit=50))
    return {"chains": chains, "treatment": "hold", "cost": 1}


def professional_evidence(palette: dict, props: dict) -> dict:
    primary, accent, secondary, danger = _base(palette)
    title = props.get("title") or props.get("text") or "EVIDENCE"
    detail = props.get("detail") or props.get("subtitle") or ""
    label = props.get("label") or props.get("kicker") or "DOCUMENT"
    pos = normalize_text_position(props.get("_position") or props.get("position") or "top_left")
    total = 2 if detail else 1
    lx, ly = _corner_coords(pos, 0, total + 1, line_spacing=42)
    tx, ty = _corner_coords(pos, 1, total + 1, line_spacing=60)
    chains = [
        _dt(_upper(label), MONO, 17, accent, lx, ly, limit=24),
        _dt(_upper(title), SANS, 48, primary, tx, ty, limit=42),
    ]
    if detail:
        dx, dy = _corner_coords(pos, 2, total + 1, line_spacing=48)
        chains.append(_dt(_upper(detail), BODY, 20, secondary, dx, dy, limit=80))
    return {"chains": chains, "treatment": "editorial_crop", "cost": 1}


def professional_location(palette: dict, props: dict) -> dict:
    primary, accent, secondary, _ = _base(palette)
    title = props.get("title") or props.get("text") or props.get("location") or ""
    if not str(title).strip():
        return {"chains": [], "treatment": "map_window", "cost": 0}
    subtitle = props.get("subtitle") or props.get("detail") or ""
    pos = normalize_text_position(props.get("_position") or props.get("position") or "bottom_left")
    total = 2 if subtitle else 1
    lx, ly = _corner_coords(pos, 0, total + 1, line_spacing=40)
    tx, ty = _corner_coords(pos, 1, total + 1, line_spacing=54)
    chains = [
        _dt("LOCATION", MONO, 17, accent, lx, ly, limit=16),
        _dt(_upper(title), SANS, 42, primary, tx, ty, limit=36),
    ]
    if subtitle:
        sx, sy = _corner_coords(pos, 2, total + 1, line_spacing=46)
        chains.append(_dt(_upper(subtitle), BODY, 20, secondary, sx, sy, limit=58))
    return {"chains": chains, "treatment": "map_window", "cost": 1}


def professional_split(palette: dict, props: dict) -> dict:
    primary, accent, secondary, _ = _base(palette)
    left = props.get("cause") or props.get("left") or props.get("title") or "BEFORE"
    right = props.get("effect") or props.get("right") or props.get("subtitle") or "AFTER"
    chains = [
        _dt("A", MONO, 18, accent, "w*0.06", "h*0.08", limit=1),
        _dt(_upper(left), SANS, 40, primary, "w*0.06", "h*0.08+48", limit=24),
        _dt("B", MONO, 18, accent, "w-text_w-w*0.06", "h*0.08", limit=1),
        _dt(_upper(right), SANS, 40, primary, "w-text_w-w*0.06", "h*0.08+48", limit=24),
    ]
    return {"chains": chains, "treatment": "split_mask", "cost": 1}


def professional_name(palette: dict, props: dict) -> dict:
    primary, accent, secondary, _ = _base(palette)
    name = props.get("name") or props.get("title") or props.get("text") or ""
    subtitle = props.get("subtitle") or props.get("detail") or ""
    pos = normalize_text_position(props.get("_position") or props.get("position") or "bottom_left")
    total = 2 if subtitle else 1
    nx, ny = _corner_coords(pos, 0, total, line_spacing=56)
    chains = [
        _dt(_upper(name), SANS, 46, primary, nx, ny, limit=34),
    ]
    if subtitle:
        sx, sy = _corner_coords(pos, 1, total, line_spacing=46)
        chains.append(_dt(_upper(subtitle), MONO, 20, secondary, sx, sy, limit=46))
    return {"chains": chains, "treatment": "hold", "cost": 1}


def professional_document(palette: dict, props: dict) -> dict:
    primary, accent, secondary, _ = _base(palette)
    title = props.get("title") or props.get("text") or "ARCHIVE"
    detail = props.get("detail") or props.get("subtitle") or ""
    pos = normalize_text_position(props.get("_position") or props.get("position") or "top_left")
    total = 2 if detail else 1
    ax, ay = _corner_coords(pos, 0, total + 1, line_spacing=38)
    tx, ty = _corner_coords(pos, 1, total + 1, line_spacing=52)
    chains = [
        _dt("ARCHIVE", MONO, 16, accent, ax, ay, limit=16),
        _dt(_upper(title), SANS, 42, primary, tx, ty, limit=36),
    ]
    if detail:
        dx, dy = _corner_coords(pos, 2, total + 1, line_spacing=46)
        chains.append(_dt(_upper(detail), BODY, 20, secondary, dx, dy, limit=90))
    return {"chains": chains, "treatment": "paper_reveal", "cost": 1}


def professional_end(palette: dict, props: dict) -> dict:
    primary, accent, secondary, _ = _base(palette)
    title = props.get("title") or props.get("text") or "THE END"
    subtitle = props.get("subtitle") or props.get("detail") or ""
    pos = normalize_text_position(props.get("_position") or props.get("position") or "center")
    total = 2 if subtitle else 1
    tx, ty = _corner_coords(pos, 0, total, line_spacing=72)
    chains = [
        _dt(_upper(title), DISPLAY, 68, primary, tx, ty, limit=38),
    ]
    if subtitle:
        sx, sy = _corner_coords(pos, 1, total, line_spacing=52)
        chains.append(_dt(_upper(subtitle), BODY, 22, secondary, sx, sy, limit=70))
    return {"chains": chains, "treatment": "hold", "cost": 1}


def professional_cinematic(palette: dict, props: dict) -> dict:
    primary, accent, secondary, _ = _base(palette)
    title = props.get("title") or props.get("text") or "A MOMENT"
    subtitle = props.get("subtitle") or props.get("detail") or ""
    pos = normalize_text_position(props.get("_position") or props.get("position") or "center")
    total = 2 if subtitle else 1
    tx, ty = _corner_coords(pos, 0, total, line_spacing=68)
    chains = [
        _dt(_upper(title), DISPLAY, 64, primary, tx, ty, limit=42),
    ]
    if subtitle:
        sx, sy = _corner_coords(pos, 1, total, line_spacing=48)
        chains.append(_dt(_upper(subtitle), MONO, 20, secondary, sx, sy, limit=58))
    return {"chains": chains, "treatment": "film_gate", "cost": 1}


def archival_caption(palette: dict, props: dict) -> dict:
    """Archival lower-left caption for historical footage."""
    primary, accent, secondary, _ = _base(palette)
    place = props.get("location") or props.get("title") or props.get("text") or "ARCHIVE"
    date = props.get("date") or props.get("subtitle") or props.get("detail") or ""
    pos = normalize_text_position(props.get("_position") or props.get("position") or "bottom_left")
    total = 2 if date else 1
    px, py = _corner_coords(pos, 0, total, line_spacing=46)
    chains = [
        _dt(_upper(place), SANS, 34, primary, px, py, limit=30),
    ]
    if date:
        dx, dy = _corner_coords(pos, 1, total, line_spacing=46)
        chains.append(_dt(_upper(date), MONO, 18, accent, dx, dy, limit=26))
    return {"chains": chains, "treatment": "hold", "cost": 1}


def date_numeral(palette: dict, props: dict) -> dict:
    """Large restrained date/numeral card, centered without a full panel."""
    primary, accent, secondary, _ = _base(palette)
    value = props.get("date") or props.get("value") or props.get("title") or "1969"
    highlight = props.get("highlight") or value
    is_v2 = int(props.get("_cue_schema_version") or 0) >= 2
    label = props.get("kicker") or ("" if is_v2 else props.get("label") or "A TURNING POINT")
    detail = props.get("detail") or props.get("subtitle") or ""
    pos = normalize_text_position(props.get("_position") or props.get("position") or "center")
    total = 1 + (1 if label else 0) + (1 if detail else 0)
    chains = []
    idx = 0
    if label:
        lx, ly = _corner_coords(pos, idx, total, line_spacing=48)
        chains.append(_dt(_upper(label), MONO, 18, accent, lx, ly, limit=28))
        idx += 1
    vx, vy = _corner_coords(pos, idx, total, line_spacing=92)
    chains.append(_dt(_upper(value), DISPLAY, 96, accent if highlight else primary, vx, vy, limit=18))
    idx += 1
    if detail:
        dx, dy = _corner_coords(pos, idx, total, line_spacing=50)
        chains.append(_dt(_upper(detail), BODY, 20, secondary, dx, dy, limit=48))
    return {"chains": chains, "treatment": "hold", "cost": 1}


def evidence_highlight(palette: dict, props: dict) -> dict:
    """Evidence annotation with one restrained marker and source line."""
    primary, accent, secondary, danger = _base(palette)
    title = props.get("title") or props.get("text") or "KEY EVIDENCE"
    detail = props.get("detail") or props.get("subtitle") or ""
    source = props.get("source") or props.get("label") or "SOURCE"
    pos = normalize_text_position(props.get("_position") or props.get("position") or "top_left")
    total = 2 if detail else 1
    sx, sy = _corner_coords(pos, 0, total + 1, line_spacing=40)
    tx, ty = _corner_coords(pos, 1, total + 1, line_spacing=54)
    chains = [
        _dt(_upper(source), MONO, 17, accent, sx, sy, limit=22),
        _dt(_upper(title), SANS, 46, primary, tx, ty, limit=40),
    ]
    if detail:
        dx, dy = _corner_coords(pos, 2, total + 1, line_spacing=48)
        chains.append(_dt(_upper(detail), BODY, 20, secondary, dx, dy, limit=74))
    return {"chains": chains, "treatment": "editorial_crop", "cost": 1}


def profile_card(palette: dict, props: dict) -> dict:
    """Clean profile/name card without scrapbook or oversized panels."""
    primary, accent, secondary, _ = _base(palette)
    name = props.get("name") or props.get("title") or props.get("text") or ""
    role = props.get("role") or props.get("subtitle") or props.get("detail") or ""
    date = props.get("date") or props.get("label") or ""
    pos = normalize_text_position(props.get("_position") or props.get("position") or "bottom_left")
    total = 1 + (1 if role else 0) + (1 if date else 0)
    nx, ny = _corner_coords(pos, 0, total, line_spacing=54)
    chains = [
        _dt(_upper(name), SANS, 44, primary, nx, ny, limit=30),
    ]
    idx = 1
    if role:
        rx, ry = _corner_coords(pos, idx, total, line_spacing=46)
        chains.append(_dt(_upper(role), BODY, 20, secondary, rx, ry, limit=42))
        idx += 1
    if date:
        dx, dy = _corner_coords(pos, idx, total, line_spacing=42)
        chains.append(_dt(_upper(date), MONO, 16, accent, dx, dy, limit=22))
    return {"chains": chains, "treatment": "hold", "cost": 1}


def subject_grid(palette: dict, props: dict) -> dict:
    """Sparse presenter/subject grid, clean layout without floating rules."""
    primary, accent, secondary, _ = _base(palette)
    name = props.get("name") or props.get("title") or props.get("text") or ""
    role = props.get("role") or props.get("subtitle") or ""
    detail = props.get("detail") or ""
    pos = normalize_text_position(props.get("_position") or props.get("position") or "top_right")
    total = 1 + (1 if role else 0) + (1 if detail else 0)
    nx, ny = _corner_coords(pos, 0, total, line_spacing=52)
    chains = [
        _dt(_upper(name), SANS, 42, primary, nx, ny, limit=28),
    ]
    idx = 1
    if role:
        rx, ry = _corner_coords(pos, idx, total, line_spacing=46)
        chains.append(_dt(_upper(role), BODY, 19, secondary, rx, ry, limit=38))
        idx += 1
    if detail:
        dx, dy = _corner_coords(pos, idx, total, line_spacing=44)
        chains.append(_dt(_upper(detail), BODY, 18, secondary, dx, dy, limit=54))
    return {"chains": chains, "treatment": "depth_separation", "cost": 1}


def listicle_steps(palette: dict, props: dict) -> dict:
    """Three-row editorial listicle, intentionally limited and readable."""
    primary, accent, secondary, _ = _base(palette)
    is_v2 = int(props.get("_cue_schema_version") or 0) >= 2
    heading = props.get("title") or props.get("kicker") or ("" if is_v2 else "THE THREE THINGS")
    highlight = _upper(props.get("highlight") or "")
    pos = normalize_text_position(props.get("_position") or props.get("position") or "top_left")
    rows = [props.get("line1") or props.get("text") or "", props.get("line2") or props.get("detail") or "", props.get("line3") or ""]
    valid_rows = [r for r in rows if r]
    total = (1 if heading else 0) + (1 if (is_v2 and highlight) else len(valid_rows))
    chains = []
    idx = 0
    if heading:
        hx, hy = _corner_coords(pos, idx, max(1, total), line_spacing=50)
        chains.append(_dt(_upper(heading), SANS, 36, primary, hx, hy, limit=48))
        idx += 1
    if is_v2:
        if highlight and highlight not in _upper(heading):
            hlx, hly = _corner_coords(pos, idx, max(1, total), line_spacing=46)
            chains.append(_dt(highlight, DISPLAY, 28, accent, hlx, hly, limit=24))
        return {"chains": chains, "treatment": "hold", "cost": 1}
    for row in valid_rows:
        rx, ry = _corner_coords(pos, idx, max(1, total), line_spacing=44)
        chains.append(_dt(_upper(row), SANS, 26, secondary, rx, ry, limit=42))
        idx += 1
    return {"chains": chains, "treatment": "hold", "cost": 1}


def timeline_strip(palette: dict, props: dict) -> dict:
    """Minimal horizontal timeline with up to three explicit moments, no floating lines."""
    primary, accent, secondary, _ = _base(palette)
    title = props.get("title") or props.get("text") or "THE TIMELINE"
    pos = normalize_text_position(props.get("_position") or props.get("position") or "top_left")
    line1 = props.get("line1") or props.get("date") or ""
    line2 = props.get("line2") or props.get("detail") or ""
    line3 = props.get("line3") or props.get("subtitle") or ""
    valid_labels = [lbl for lbl in (line1, line2, line3) if lbl and lbl.upper() not in {"START", "TURNING POINT", "AFTERMATH", "EVENT"}]
    total = 1 + len(valid_labels)
    tx, ty = _corner_coords(pos, 0, total, line_spacing=46)
    chains = [
        _dt(_upper(title), SANS, 34, primary, tx, ty, limit=28),
    ]
    for i, label in enumerate(valid_labels, 1):
        lx, ly = _corner_coords(pos, i, total, line_spacing=40)
        chains.append(_dt(_upper(label), MONO, 20, accent if i == 1 else secondary, lx, ly, limit=24))
    return {"chains": chains, "treatment": "hold", "cost": 1}


def headline_center(palette: dict, props: dict) -> dict:
    """Centered two-line headline for rare, high-importance beats."""
    primary, accent, secondary, _ = _base(palette)
    title = props.get("title") or props.get("text") or "THE MOMENT"
    line1 = props.get("line1") or title
    line2 = props.get("line2") or props.get("detail") or ""
    kicker = props.get("kicker") or ""
    subtitle = props.get("subtitle") or ""
    pos = normalize_text_position(props.get("_position") or props.get("position") or "center")
    lines = []
    if kicker:
        lines.append((kicker, MONO, 18, accent, 42))
    lines.append((line1, SANS, 56, primary, 66))
    if line2:
        lines.append((line2, SANS, 56, primary, 66))
    if subtitle:
        lines.append((subtitle, BODY, 22, secondary, 50))
    total = len(lines)
    chains = []
    for idx, (txt, font, sz, col, spc) in enumerate(lines):
        x, y = _corner_coords(pos, idx, total, line_spacing=spc)
        chains.append(_dt(_upper(txt), font, sz, col, x, y, limit=48))
    return {"chains": chains, "treatment": "hold", "cost": 1}


def polaroid_card(palette: dict, props: dict) -> dict:
    """Polaroid composition: dark padded card at strict anchor."""
    primary, accent, secondary, _ = _base(palette)
    caption = props.get("caption") or props.get("title") or props.get("text") or "THE PHOTO"
    date = props.get("date") or props.get("subtitle") or ""
    pos = normalize_text_position(props.get("_position") or props.get("position") or "bottom_left")
    total = 2 if date else 1
    cx, cy = _corner_coords(pos, 0, total, line_spacing=46)
    chains = [
        _dt(_upper(caption), SANS, 34, primary, cx, cy, limit=30),
    ]
    if date:
        dx, dy = _corner_coords(pos, 1, total, line_spacing=38)
        chains.append(_dt(_upper(date), MONO, 16, secondary, dx, dy, limit=24))
    return {"chains": chains, "treatment": "hold", "cost": 1}


def photo_frame(palette: dict, props: dict) -> dict:
    """Photo frame label at strict anchor with padded badge."""
    primary, accent, secondary, _ = _base(palette)
    label = props.get("title") or props.get("text") or "ARCHIVE"
    date = props.get("date") or props.get("subtitle") or ""
    pos = normalize_text_position(props.get("_position") or props.get("position") or "bottom_left")
    total = 2 if date else 1
    lx, ly = _corner_coords(pos, 0, total, line_spacing=46)
    chains = [
        _dt(_upper(label), SANS, 36, primary, lx, ly, limit=30),
    ]
    if date:
        dx, dy = _corner_coords(pos, 1, total, line_spacing=38)
        chains.append(_dt(_upper(date), MONO, 16, accent, dx, dy, limit=20))
    return {"chains": chains, "treatment": "hold", "cost": 1}


def presenter_card(palette: dict, props: dict) -> dict:
    """Bottom-left presenter card: name, role."""
    primary, accent, secondary, _ = _base(palette)
    name = props.get("name") or props.get("title") or props.get("text") or ""
    role = props.get("role") or props.get("subtitle") or props.get("detail") or ""
    pos = normalize_text_position(props.get("_position") or props.get("position") or "bottom_left")
    total = 2 if role else 1
    nx, ny = _corner_coords(pos, 0, total, line_spacing=54)
    chains = [
        _dt(_upper(name), SANS, 44, primary, nx, ny, limit=30),
    ]
    if role:
        rx, ry = _corner_coords(pos, 1, total, line_spacing=46)
        chains.append(_dt(_upper(role), BODY, 20, secondary, rx, ry, limit=52))
    return {"chains": chains, "treatment": "hold", "cost": 1}


def card_overlay(palette: dict, props: dict) -> dict:
    """Editorial card at strict anchor: accent tag, title, detail."""
    primary, accent, secondary, _ = _base(palette)
    tag = props.get("label") or props.get("kicker") or "NOTE"
    title = props.get("title") or props.get("text") or ""
    detail = props.get("detail") or props.get("subtitle") or ""
    pos = normalize_text_position(props.get("_position") or props.get("position") or "top_right")
    total = 3 if detail else 2
    tx, ty = _corner_coords(pos, 0, total, line_spacing=36)
    hx, hy = _corner_coords(pos, 1, total, line_spacing=50)
    chains = [
        _dt(_upper(tag), MONO, 16, accent, tx, ty, limit=20),
        _dt(_upper(title), SANS, 42, primary, hx, hy, limit=26),
    ]
    if detail:
        dx, dy = _corner_coords(pos, 2, total, line_spacing=42)
        chains.append(_dt(_upper(detail), BODY, 19, secondary, dx, dy, limit=64))
    return {"chains": chains, "treatment": "hold", "cost": 1}


def archive_canvas(palette: dict, props: dict) -> dict:
    """Dark archive canvas at strict anchor: mono labels and title."""
    primary, accent, secondary, _ = _base(palette)
    title = props.get("title") or props.get("text") or "THE RECORD"
    detail = props.get("detail") or props.get("subtitle") or ""
    date = props.get("date") or ""
    pos = normalize_text_position(props.get("_position") or props.get("position") or "top_left")
    items = [("RECORD", MONO, 14, accent)]
    if title:
        items.append((title, SANS, 38, primary))
    if detail:
        items.append((detail, BODY, 18, secondary))
    if date:
        items.append((date, MONO, 14, secondary))
    chains = []
    for idx, (txt, font, sz, col) in enumerate(items):
        x, y = _corner_coords(pos, idx, len(items), line_spacing=sz + 14)
        chains.append(_dt(_upper(txt), font, sz, col, x, y, limit=48))
    return {"chains": chains, "treatment": "hold", "cost": 1}


def step_progress(palette: dict, props: dict) -> dict:
    """Numbered steps at strict anchor."""
    primary, accent, secondary, _ = _base(palette)
    heading = props.get("title") or props.get("kicker") or "THE PLAN"
    pos = normalize_text_position(props.get("_position") or props.get("position") or "top_left")
    steps = [
        props.get("line1") or props.get("step1") or "",
        props.get("line2") or props.get("step2") or "",
        props.get("line3") or props.get("step3") or "",
    ]
    valid_steps = [s for s in steps if s]
    items = [(heading, SANS, 32, accent)]
    for num, s in enumerate(valid_steps, 1):
        items.append((f"{num}. {s}", BODY, 22, primary))
    chains = []
    for idx, (txt, font, sz, col) in enumerate(items):
        x, y = _corner_coords(pos, idx, len(items), line_spacing=sz + 16)
        chains.append(_dt(_upper(txt), font, sz, col, x, y, limit=44))
    return {"chains": chains, "treatment": "hold", "cost": 1}


def map_card(palette: dict, props: dict) -> dict:
    """Bottom-right location card: clean backdrop and place name."""
    primary, accent, secondary, _ = _base(palette)
    place = props.get("location") or props.get("title") or props.get("text") or ""
    if not str(place).strip():
        return {"chains": [], "treatment": "hold", "cost": 0}
    detail = props.get("detail") or props.get("subtitle") or ""
    pos = normalize_text_position(props.get("_position") or props.get("position") or "bottom_right")
    total = 2 if detail else 1
    px, py = _corner_coords(pos, 0, total, line_spacing=50)
    chains = [
        _dt(_upper(place), SANS, 38, primary, px, py, limit=28),
    ]
    if detail:
        dx, dy = _corner_coords(pos, 1, total, line_spacing=44)
        chains.append(_dt(_upper(detail), BODY, 18, secondary, dx, dy, limit=40))
    return {"chains": chains, "treatment": "hold", "cost": 1}


PROFESSIONAL_TEMPLATES: Dict[str, Any] = {}

for _name in ("cold_open", "chapter_title", "split_statement"):
    PROFESSIONAL_TEMPLATES[_name] = professional_chapter
for _name in ("kinetic_statement", "keyword_reveal", "word_cascade", "typewriter_reveal"):
    PROFESSIONAL_TEMPLATES[_name] = professional_headline
for _name in ("stat_counter", "money_counter", "percentage_gauge", "metric_grid", "bar_chart", "ranked_list"):
    PROFESSIONAL_TEMPLATES[_name] = professional_stat
for _name in ("evidence_callout", "redaction_reveal", "case_file", "source_citation", "stamp_mark"):
    PROFESSIONAL_TEMPLATES[_name] = professional_evidence
for _name in ("date_reveal", "calendar_rip", "timeline_trace", "vintage_headline"):
    PROFESSIONAL_TEMPLATES[_name] = professional_date
for _name in ("quote_card", "witness_quote"):
    PROFESSIONAL_TEMPLATES[_name] = professional_quote
for _name in ("cause_effect", "before_after", "split_compare", "diagonal_split", "split_screen_doc"):
    PROFESSIONAL_TEMPLATES[_name] = professional_split
for _name in ("location_label", "map_route", "location_title_card"):
    PROFESSIONAL_TEMPLATES[_name] = professional_location
for _name in ("profile_dossier", "dramatic_portrait", "bold_name_banner", "passport_stamp"):
    PROFESSIONAL_TEMPLATES[_name] = professional_name
for _name in ("field_caption", "exhibit_label", "newsprint_reveal", "index_card", "paper_fold_reveal", "torn_paper_reveal"):
    PROFESSIONAL_TEMPLATES[_name] = professional_document
for _name in ("end_card", "cta_card"):
    PROFESSIONAL_TEMPLATES[_name] = professional_end
for _name in ("watercolor_reveal", "retro_tech_overlay", "vintage_film_card", "cinematic_zoom_text"):
    PROFESSIONAL_TEMPLATES[_name] = professional_cinematic
PROFESSIONAL_TEMPLATES["bold_red_text"] = professional_headline
for _name in ("countdown_clock", "signal_scan", "split_screen_doc"):
    PROFESSIONAL_TEMPLATES.setdefault(_name, professional_document)

# Additional dedicated compositions inspired by archival documentary and
# modern creator-editorial references. They are deliberately not aliases.
PROFESSIONAL_TEMPLATES.update({
    "archival_caption": archival_caption,
    "date_numeral": date_numeral,
    "evidence_highlight": evidence_highlight,
    "profile_card": profile_card,
    "subject_grid": subject_grid,
    "listicle_steps": listicle_steps,
    "timeline_strip": timeline_strip,
    "headline_center": headline_center,
    "polaroid_card": polaroid_card,
    "photo_frame": photo_frame,
    "presenter_card": presenter_card,
    "card_overlay": card_overlay,
    "archive_canvas": archive_canvas,
    "step_progress": step_progress,
    "map_card": map_card,
})

# Every template gets entry motion (staggered slide-up text, sweeping rules)
# so cards never render statically.
PROFESSIONAL_TEMPLATES = {_k: _animated(_v) for _k, _v in PROFESSIONAL_TEMPLATES.items()}
