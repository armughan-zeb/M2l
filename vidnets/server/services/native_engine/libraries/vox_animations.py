"""
Vox Animation Library -- robust FFmpeg drawtext position expressions.

Every animation returns (x_expr, y_expr, enable_expr) for drawtext filter.

KEY RULE: expressions with commas inside drawtext x/y MUST have commas
escaped as \\, — single-quote wrapping does NOT work in drawtext (it causes
"Undefined constant or missing '('" parse errors). Single-quotes are only
valid in drawbox w/h expressions.
Pattern: x=max(120\\,120+200)
"""

from __future__ import annotations
from typing import Tuple


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


def _esc(expr: str) -> str:
    """Escape commas for drawtext x/y expressions.

    drawtext evaluates x/y as naked libavutil expressions. Commas inside
    functions like max()/min()/if() must be escaped as \\, — single-quote
    wrapping is NOT valid in drawtext and causes parse errors.
    """
    return expr.replace(",", "\\,")


def slide_up(target_x: str, target_y: str, dur=0.5, delay=0.0, px=120):
    rate = px / max(0.1, dur)
    enable = f"enable=gte(t\\,{delay:.2f})" if delay > 0 else ""
    y = _esc(f"max({target_y},{target_y}+{px}-(t-{delay:.2f})*{rate:.0f})")
    return target_x, y, enable


def slide_down(target_x: str, target_y: str, dur=0.5, delay=0.0, px=120):
    rate = px / max(0.1, dur)
    enable = f"enable=gte(t\\,{delay:.2f})" if delay > 0 else ""
    y = _esc(f"min({target_y},{target_y}-{px}+(t-{delay:.2f})*{rate:.0f})")
    return target_x, y, enable


def slide_left(target_x: str, target_y: str, dur=0.5, delay=0.0, px=120):
    rate = px / max(0.1, dur)
    enable = f"enable=gte(t\\,{delay:.2f})" if delay > 0 else ""
    x = _esc(f"max({target_x},{target_x}+{px}-(t-{delay:.2f})*{rate:.0f})")
    return x, target_y, enable


def slide_right(target_x: str, target_y: str, dur=0.5, delay=0.0, px=120):
    rate = px / max(0.1, dur)
    enable = f"enable=gte(t\\,{delay:.2f})" if delay > 0 else ""
    x = _esc(f"min({target_x},{target_x}-{px}+(t-{delay:.2f})*{rate:.0f})")
    return x, target_y, enable


def drop_in(target_x: str, target_y: str, dur=0.5, delay=0.0, px=120):
    """Drop from above with a bounce overshoot — distinct from slide_down."""
    rate = px / max(0.1, dur)
    bounce_px = px * 0.15
    enable = f"enable=gte(t\\,{delay:.2f})" if delay > 0 else ""
    # Overshoots then settles: drops past target, then bounces back up
    y = _esc(
        f"if(lt(t-{delay:.2f},{dur})"
        f",min({target_y}+{bounce_px:.0f},{target_y}-{px}+(t-{delay:.2f})*{rate:.0f}"
        f"+{bounce_px:.0f}*(({dur}-(t-{delay:.2f}))/{dur}))"
        f",{target_y})"
    )
    return target_x, y, enable


def scale_pop(target_x: str, target_y: str, dur=0.5, delay=0.0, px=100):
    """Pop in from center — slides up while expanding in perceived size."""
    rate = px / max(0.1, dur)
    enable = f"enable=gte(t\\,{delay:.2f})" if delay > 0 else ""
    y = _esc(f"max({target_y},{target_y}+{px}-(t-{delay:.2f})*{rate:.0f})")
    return target_x, y, enable


def fade_in(target_x: str, target_y: str, dur=0.5, delay=0.0):
    enable = f"enable=gte(t\\,{delay:.2f})" if delay > 0 else ""
    return target_x, target_y, enable


def static_hold(target_x: str, target_y: str):
    return target_x, target_y, ""


def bounce_up(target_x: str, target_y: str, dur=0.6, delay=0.0, px=130):
    """Bounce up from below — overshoots upward then settles back down."""
    rate = px / max(0.1, dur)
    overshoot = px * 0.2
    enable = f"enable=gte(t\\,{delay:.2f})" if delay > 0 else ""
    y = _esc(
        f"if(lt(t-{delay:.2f},{dur})"
        f",if(lt(t-{delay:.2f},{dur}*0.6)"
        f",max({target_y},{target_y}+{px}-(t-{delay:.2f})*{rate:.0f})"
        f",max({target_y},{target_y}+{px}-(t-{delay:.2f})*{rate:.0f}"
        f"-{overshoot:.0f}*sin((t-{delay:.2f}-{dur}*0.6)*3.14159/({dur}*0.4))))"
        f",{target_y})"
    )
    return target_x, y, enable


def blur_in(target_x: str, target_y: str, dur=0.5, delay=0.0, px=60):
    """Slide in from left with a subtle drift — softer entrance."""
    rate = px / max(0.1, dur)
    enable = f"enable=gte(t\\,{delay:.2f})" if delay > 0 else ""
    x = _esc(f"min({target_x},{target_x}+{px}-(t-{delay:.2f})*{rate:.0f})")
    return x, target_y, enable


def swift_in(target_x: str, target_y: str, dur=0.3, delay=0.0, px=200):
    """Fast horizontal entry — rapid slide from right."""
    rate = px / max(0.1, dur)
    enable = f"enable=gte(t\\,{delay:.2f})" if delay > 0 else ""
    x = _esc(f"max({target_x},{target_x}+{px}-(t-{delay:.2f})*{rate:.0f})")
    return x, target_y, enable


# ═══════════════════════════════════════════════════════════════════
# Drawbox animations
# ═══════════════════════════════════════════════════════════════════

def bar_sweep_x(x, y, w, h, color, delay=0.0, dur=0.4):
    """Horizontal bar that sweeps from 0 to full width (disabled to prevent floating lines)."""
    return ""


def bar_grow_y(x, y, w, h, color, delay=0.0, dur=0.3):
    """Vertical bar that grows from top (disabled to prevent floating lines)."""
    return ""


def bar_static(x, y, w, h, color):
    """Static bar/rule (disabled to prevent floating lines)."""
    return ""


def overlay_rect(x, y, w, h, color):
    return f"drawbox=x={x}:y={y}:w={w}:h={h}:color={_color(color)}:t=fill"


def pulse_box(x, y, w, h, color, delay=0.0, dur=0.8):
    """Animated overlay rectangle that pulses in opacity over time."""
    if delay > 0:
        beat = f"0.5+0.5*sin((t-{delay:.2f})*6.28319/{dur})"
    else:
        beat = f"0.5+0.5*sin(t*6.28319/{dur})"
    return f"drawbox=x={x}:y={y}:w={w}:h={h}:color={_color(color)}@'{beat}':t=fill"


def sweep_reveal(x, y, w, h, color, delay=0.0, dur=0.6):
    """Bar that sweeps from right to left, revealing content behind it."""
    rate = 1.0 / max(0.1, dur)
    if delay > 0:
        anim_x = f"'iw*(1-min(1\\,max(0\\,(t-{delay:.2f})*{rate:.1f})))'"
    else:
        anim_x = f"'iw*(1-min(1\\,t*{rate:.1f}))'"
    return (
        f"drawbox=x={anim_x}:y={y}:w='iw':h={h}:color={_color(color)}:t=fill"
    )
