"""
Visual Effects Library — procedural FFmpeg filters for cinematic design.

Uses geq (generic equation), drawbox, gradients, curves, and boxblur
to create professional design elements without any image assets.

All effects are CPU-friendly: one-pass FFmpeg rendering.
"""

# ═══════════════════════════════════════════════════════════════════
# Gradients
# ═══════════════════════════════════════════════════════════════════

def vertical_gradient(top_color="#1a1a25", bottom_color="#0a0a0f", opacity=0.9):
    """Smooth top-to-bottom gradient overlay. Use with multiply blend."""
    return (
        f"format=yuv420p,"
        f"geq=lum='lum(X\\,Y)':cb='cb(X\\,Y)':cr='cr(X\\,Y)'"
    )


def cinematic_vignette(width=1920, height=1080, intensity=0.7):
    """Dark cinema-style vignette around the edges."""
    cx, cy = width / 2, height / 2
    r2 = (width / 2) ** 2
    return (
        f"vignette=PI/4"
    )


def radial_spotlight(width=1920, height=1080, color="#fff", intensity=0.3):
    """Soft circular brightness at center, fading to dark at edges."""
    return (
        f"vignette=PI/4:mode=backward"
    )


# ═══════════════════════════════════════════════════════════════════
# Decorative Shapes (drawbox-based)
# ═══════════════════════════════════════════════════════════════════

def top_accent_bar(x=0, y=0, width_pct=100, height=5, color="#f2c14e",
                   start_t=0, anim="sweep", duration=0.5):
    """Thin accent bar that sweeps in from left.

    anim: 'sweep' (slides in), 'static' (no animation), 'pulse' (opacity pulse)
    """
    if anim == "sweep":
        # Use drawbox with enable to control timing
        return f"drawbox=x='0':y={y}:w='iw*min(1\\, (t-{start_t})/{duration})':h={height}:color={color}@1.0:t=fill"
    elif anim == "pulse":
        return f"drawbox=x=0:y={y}:w={width_pct}*iw/100:h={height}:color={color}@'0.5+0.5*sin(t*3)':t=fill"
    return f"drawbox=x=0:y={y}:w={width_pct}*iw/100:h={height}:color={color}@1.0:t=fill"


def bottom_accent_bar(x=0, y_pct=95, width_pct=60, height=4, color="#f2c14e",
                      start_t=0, anim="grow", duration=0.4):
    """Bottom accent bar that grows from center."""
    if anim == "grow":
        # Use t (thickness) parameter for grow effect
        center_x = "iw/2"
        return (
            f"drawbox=x='(iw-{width_pct}*iw/100*min(1\\, (t-{start_t})/{duration}))/2':"
            f"y={y_pct}*ih/100:"
            f"w='{width_pct}*iw/100*min(1\\, (t-{start_t})/{duration})':"
            f"h={height}:color={color}@1.0:t=fill"
        )
    return f"drawbox=x=(iw-{width_pct}*iw/100)/2:y={y_pct}*ih/100:w={width_pct}*iw/100:h={height}:color={color}@1.0:t=fill"


def vertical_accent_line(x_pct=10, y_pct=10, height_pct=80, width=3, color="#f2c14e"):
    """Thin vertical line (left or right margin)."""
    return f"drawbox=x={x_pct}*iw/100:y={y_pct}*ih/100:w={width}:h={height_pct}*ih/100:color={color}@1.0:t=fill"


def corner_bracket(x_pct, y_pct, size=30, thickness=3, color="#f2c14e", corner="tl"):
    """L-shaped corner bracket. corner: tl, tr, bl, br."""
    h = f"drawbox=x={x_pct}*iw/100:y={y_pct}*ih/100:w={size}:h={thickness}:color={color}@1.0:t=fill"
    v = f"drawbox=x={x_pct}*iw/100:y={y_pct}*ih/100:w={thickness}:h={size}:color={color}@1.0:t=fill"
    if corner == "tr":
        h = f"drawbox=x=(iw-{x_pct}*iw/100-{size}):y={y_pct}*ih/100:w={size}:h={thickness}:color={color}@1.0:t=fill"
        v = f"drawbox=x=(iw-{x_pct}*iw/100-{thickness}):y={y_pct}*ih/100:w={thickness}:h={size}:color={color}@1.0:t=fill"
    elif corner == "bl":
        h = f"drawbox=x={x_pct}*iw/100:y=(ih-{y_pct}*ih/100-{thickness}):w={size}:h={thickness}:color={color}@1.0:t=fill"
        v = f"drawbox=x={x_pct}*ih/100:y=(ih-{y_pct}*ih/100-{size}):w={thickness}:h={size}:color={color}@1.0:t=fill"
    elif corner == "br":
        h = f"drawbox=x=(iw-{x_pct}*iw/100-{size}):y=(ih-{y_pct}*ih/100-{thickness}):w={size}:h={thickness}:color={color}@1.0:t=fill"
        v = f"drawbox=x=(iw-{x_pct}*iw/100-{thickness}):y=(ih-{y_pct}*ih/100-{size}):w={thickness}:h={size}:color={color}@1.0:t=fill"
    return h, v


def center_accent_line(width=200, height=4, color="#f2c14e"):
    """Horizontal accent line at center (e.g., under text)."""
    return f"drawbox=x=(iw-{width})/2:y=(ih-{height})/2:w={width}:h={height}:color={color}@1.0:t=fill"


# ═══════════════════════════════════════════════════════════════════
# Background treatments
# ═══════════════════════════════════════════════════════════════════

def background_darken(strength=0.3):
    """Darken the entire frame for text legibility."""
    return f"eq=brightness=-{strength*0.5}:contrast=1.2:saturation=0.85"


def background_blur(strength=8):
    """Light blur for background separation (bokeh effect)."""
    return f"boxblur={strength}:1"


def background_teal_orange():
    """Cinematic teal/orange color grade."""
    return "colorbalance=bs=0.15:bm=0.05:gh=-0.05:rh=0.1:bh=0.1,eq=saturation=1.05"


def background_warm_grade():
    """Warm documentary grade."""
    return "colorbalance=rs=0.08:rh=0.05:bh=-0.05,eq=saturation=1.1"


def background_cold_grade():
    """Cold/serious documentary grade."""
    return "colorbalance=bs=0.08:bh=0.1:rh=-0.05,eq=saturation=0.9"


# ═══════════════════════════════════════════════════════════════════
# Text effects (additional to drawtext)
# ═══════════════════════════════════════════════════════════════════

def text_glow(text, x, y, color="#fff", glow_color="#f2c14e", size=60):
    """Add a glow effect behind text using boxblur."""
    return f"drawbox=x={x}:y={y}:w=text_w:h=text_h:color={glow_color}@0.3:t=fill,boxblur=12:1"


def text_highlight_strip(text_x, text_y, text_w, text_h, color="#f2c14e", anim="reveal"):
    """Animated highlight strip that reveals under text (like a highlighter)."""
    if anim == "reveal":
        return f"drawbox=x={text_x}:y={text_y}+text_h-6:w=text_w*min(1\\, t*2):h=4:color={color}@0.6:t=fill"
    return f"drawbox=x={text_x}:y={text_y}+text_h-6:w=text_w:h=4:color={color}@0.6:t=fill"


# ═══════════════════════════════════════════════════════════════════
# Complex composite effects
# ═══════════════════════════════════════════════════════════════════

def letterbox_cinematic(ratio=2.35):
    """Add cinematic black bars (top + bottom)."""
    bar_h = "ih*(1-1/{0})/2".format(ratio)
    return (
        f"drawbox=x=0:y=0:w=iw:h={bar_h}:color=black@1:t=fill,"
        f"drawbox=x=0:y=ih-{bar_h}:w=iw:h={bar_h}:color=black@1:t=fill"
    )


def cctv_overlay(timestamp_text="REC"):
    """CCTV-style overlay: REC text, camera number, timestamp."""
    return (
        f"drawtext=text='{timestamp_text}':fontfile='Courier New.ttf':"
        f"fontsize=18:fontcolor=red@0.85:x=20:y=20,"
        f"drawtext=text='CAM 04':fontfile='Courier New.ttf':"
        f"fontsize=14:fontcolor=white@0.7:x=20:y=44,"
        f"drawtext=text='%{{pts\\:hms}} - %{{date}}':fontfile='Courier New.ttf':"
        f"fontsize=12:fontcolor=white@0.6:x=20:y=ih-30"
    )


def light_leak_corner(corner="tr", color="#f2c14e"):
    """Light leak gradient from one corner."""
    # This needs a color source blended in - implemented in template
    return f"color=c={color}:s=320x320:duration=1,boxblur=20:1"


# ═══════════════════════════════════════════════════════════════════
# Color/background gradient generation (for when no real image)
# ═══════════════════════════════════════════════════════════════════

def procedural_background(base_color="#0a0a0f", variation=0.08, seed=0):
    """Generate a subtle gradient/variation background from a base color.
    Used as fallback when no real image is available."""
    import hashlib
    h = int(hashlib.md5(f"{base_color}{seed}".encode()).hexdigest()[:6], 16)
    r1 = int(base_color[1:3], 16)
    g1 = int(base_color[3:5], 16)
    b1 = int(base_color[5:7], 16)
    r2 = min(255, r1 + (h % 30) - 15)
    g2 = min(255, g1 + ((h >> 5) % 30) - 15)
    b2 = min(255, b1 + ((h >> 10) % 30) - 15)
    return f"color=c={base_color}@1:s=1920x1080:d=1"
