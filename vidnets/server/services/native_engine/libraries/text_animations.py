"""
Text Animation Library — FFmpeg drawtext animation presets.

Each animation returns (x_expr, y_expr, alpha_expr) strings
that can be inserted into drawtext filter options.

Animations use FFmpeg's expression evaluation:
  - t = current time in seconds
  - n = current frame number  
  - w, h = video dimensions
  - text_w, text_h = rendered text dimensions
"""

ANIMATIONS = {}

def anim(name: str):
    """Decorator to register animation presets."""
    def deco(fn):
        ANIMATIONS[name] = fn
        return fn
    return deco


# ═══════════════════════════════════════════════════════════════════
# Entry Animations
# ═══════════════════════════════════════════════════════════════════

@anim("fade_in")
def fade_in(start_t: float = 0, duration: float = 0.6):
    alpha = "1"  # drawtext alpha doesn't support expressions — use position for animation instead
    y = f"(h-text_h)/2+{int(80 * (1 - 1))}"  # static
    x = "(w-text_w)/2"
    return x, y, alpha

@anim("scale_pop")
def scale_pop(start_t: float = 0, duration: float = 0.5):
    # Pop-up: text slides up from below with slight overshoot
    t_local = f"(t-{start_t})/{duration}"
    offset = f"max(0\\, 100-{t_local}*120)"  # starts 100px down, moves to position
    x = "(w-text_w)/2"
    y = f"(h-text_h)/2+{offset}"
    alpha = "1"
    return x, y, alpha

@anim("slide_up")
def slide_up(start_t: float = 0, duration: float = 0.5):
    t_local = f"(t-{start_t})/{duration}"
    offset = f"max(0\\, (1-{t_local})*120)"
    x = "(w-text_w)/2"
    y = f"(h-text_h)/2+{offset}"
    alpha = "1"
    return x, y, alpha

@anim("slide_left")
def slide_left(start_t: float = 0, duration: float = 0.5):
    t_local = f"(t-{start_t})/{duration}"
    offset = f"0-max(0\\, (1-{t_local})*120)"
    x = f"(w-text_w)/2+{offset}"
    y = "(h-text_h)/2"
    alpha = "1"
    return x, y, alpha

@anim("slide_right")
def slide_right(start_t: float = 0, duration: float = 0.5):
    t_local = f"(t-{start_t})/{duration}"
    offset = f"max(0\\, (1-{t_local})*120)"
    x = f"(w-text_w)/2+{offset}"
    y = "(h-text_h)/2"
    alpha = "1"
    return x, y, alpha

@anim("type_on")
def type_on(start_t: float = 0, duration: float = 2.0, text_len: int = 20):
    x = "(w-text_w)/2"
    y = "(h-text_h)/2"
    alpha = "1"
    return x, y, alpha

@anim("drop_in")
def drop_in(start_t: float = 0, duration: float = 0.6):
    # Drop from above
    t_local = f"(t-{start_t})/{duration}"
    offset = f"max(0\\, (1-{t_local})*(1-{t_local})*200)"
    x = "(w-text_w)/2"
    y = f"(h-text_h)/2-{offset}"
    alpha = "1"
    return x, y, alpha

@anim("reveal_left")
def reveal_left(start_t: float = 0, duration: float = 0.7):
    t_local = f"(t-{start_t})/{duration}"
    x = f"(w-text_w)/2-max(0\\, (1-{t_local}))*text_w"
    y = "(h-text_h)/2"
    alpha = "1"
    return x, y, alpha

@anim("pulse")
def pulse(start_t: float = 0, duration: float = 0.8):
    x = "(w-text_w)/2"
    y = f"(h-text_h)/2+sin((t-{start_t})*18)*4"
    alpha = "1"
    return x, y, alpha

@anim("glitch_in")
def glitch_in(start_t: float = 0, duration: float = 0.4):
    jitter = f"max(0\\, 0.4-(t-{start_t}))/0.4"
    x = f"(w-text_w)/2+sin(t*80)*{jitter}*30"
    y = f"(h-text_h)/2+cos(t*60)*{jitter}*15"
    alpha = "1"
    return x, y, alpha


# ═══════════════════════════════════════════════════════════════════
# Exit Animations  
# ═══════════════════════════════════════════════════════════════════

@anim("fade_out")
def fade_out(exit_t: float = 3.0, duration: float = 0.4):
    alpha = "1"
    x = "(w-text_w)/2"
    y = "(h-text_h)/2"
    return x, y, alpha

@anim("slide_out_left")
def slide_out_left(exit_t: float = 3.0, duration: float = 0.4):
    progression = f"max(0\\, min(1\\, (t-{exit_t})/{duration}))"
    x = f"(w-text_w)/2-{progression}*w"
    y = "(h-text_h)/2"
    alpha = "1"
    return x, y, alpha


# ═══════════════════════════════════════════════════════════════════
# Continuous Animations
# ═══════════════════════════════════════════════════════════════════

@anim("float")
def float(start_t: float = 0, intensity: float = 8.0):
    x = f"(w-text_w)/2+sin(t*1.3)*{intensity}"
    y = f"(h-text_h)/2+cos(t*0.9)*{intensity}"
    alpha = "1"
    return x, y, alpha

@anim("breath")
def breath(start_t: float = 0, intensity: float = 0.03):
    x = "(w-text_w)/2"
    y = "(h-text_h)/2"
    alpha = "1"
    return x, y, alpha

@anim("counter")
def counter(start_t: float = 0, duration: float = 2.0, start_val: float = 0, end_val: float = 1000000):
    # Animate a number counting up — requires frame-level text substitution
    # (handled separately by generating per-frame counter text)
    x = "(w-text_w)/2"
    y = "(h-text_h)/2"
    alpha = "1"
    return x, y, alpha


# ═══════════════════════════════════════════════════════════════════
# Combo: entry + hold + exit
# ═══════════════════════════════════════════════════════════════════

def combo_animation(entry: str, exit: str = "none", start_t: float = 0, exit_t: float = 3.0, total_dur: float = 3.5):
    """Combine entry and exit animations into x,y,alpha expressions."""
    ent = ANIMATIONS.get(entry)
    ext = ANIMATIONS.get(exit) if exit != "none" else None

    x = "(w-text_w)/2"
    y = "(h-text_h)/2"
    alpha = "1"

    if ent:
        ex, ey, ea = ent(start_t=start_t)
        x = f"if(lt(t,{start_t}+1.0),{ex},{x})"
        y = f"if(lt(t,{start_t}+1.0),{ey},{y})"
        alpha = f"if(lt(t,{start_t}+0.6),{ea},{alpha})"

    if ext:
        ex2, ey2, ea2 = ext(exit_t=exit_t)
        x = f"if(gt(t,{exit_t}),{ex2},{x})"
        y = f"if(gt(t,{exit_t}),{ey2},{y})"
        alpha = f"if(gt(t,{exit_t}),{ea2},{alpha})"

    return x, y, alpha
