"""
Overlay Library — procedural film grain, vignette, scanlines, light leaks via FFmpeg geq.

Each overlay returns a filter chain that produces the effect.
These are composited as additional tracks over the base video.
"""

OVERLAYS = {}
registered_overlays = {}


def overlay(name: str, cost: int = 1, tags: list = None):
    def deco(fn):
        registered_overlays[name] = {"fn": fn, "cost": cost, "tags": tags or []}
        return fn
    return deco


# ═══════════════════════════════════════════════════════════════════
# Procedural overlays (generated via geq/lut/color filters)
# ═══════════════════════════════════════════════════════════════════

@overlay("dark_vignette", cost=1, tags=["focus", "dramatic", "cinematic"])
def dark_vignette(width=1920, height=1080, intensity=0.6):
    """Dark vignette — darkens edges to focus center."""
    cx, cy = width / 2, height / 2
    r2 = (width / 2) ** 2
    return (
        f"geq=r='r(X,Y)*max(0,1-{intensity}*(pow(X-{cx},2)+pow(Y-{cy},2))/{r2})':"
        f"g='g(X,Y)*max(0,1-{intensity}*(pow(X-{cx},2)+pow(Y-{cy},2))/{r2})':"
        f"b='b(X,Y)*max(0,1-{intensity}*(pow(X-{cx},2)+pow(Y-{cy},2))/{r2})'"
    )


@overlay("film_grain", cost=2, tags=["texture", "archive", "film"])
def film_grain(width=1920, height=1080, intensity=0.04):
    """Subtle film grain using noise generation."""
    return (
        f"geq=r='r(X,Y)+{intensity}*255*(random(1)-0.5)':"
        f"g='g(X,Y)+{intensity}*255*(random(1)-0.5)':"
        f"b='b(X,Y)+{intensity}*255*(random(1)-0.5)'"
    )


@overlay("heavy_grain", cost=2, tags=["texture", "archive", "grunge"])
def heavy_grain(width=1920, height=1080, intensity=0.09):
    """Heavier grain for archive/grunge look."""
    return film_grain(width, height, intensity)


@overlay("scanlines", cost=1, tags=["tech", "surveillance", "monitor"])
def scanlines(width=1920, height=1080, intensity=0.15):
    """Horizontal scan lines — CCTV/monitor effect."""
    return (
        f"geq=r='r(X,Y)*(1-{intensity}*(mod(Y,4)<1))':"
        f"g='g(X,Y)*(1-{intensity}*(mod(Y,4)<1))':"
        f"b='b(X,Y)*(1-{intensity}*(mod(Y,4)<1))'"
    )


@overlay("dust_specks", cost=2, tags=["texture", "archive", "old_film"])
def dust_specks(width=1920, height=1080, intensity=0.03):
    """Occasional bright dust specks for old film look."""
    return (
        f"geq=r='r(X,Y)+{intensity}*255*if(lt(random(1),0.003),1,0)':"
        f"g='g(X,Y)+{intensity}*255*if(lt(random(1),0.003),1,0)':"
        f"b='b(X,Y)+{intensity}*255*if(lt(random(1),0.003),1,0)'"
    )


@overlay("chromatic_aberration", cost=3, tags=["glitch", "tech", "distortion"])
def chromatic_aberration(width=1920, height=1080, intensity=3.0):
    """RGB channel offset — subtle chromatic aberration."""
    return (
        f"split=3[r][g][b];"
        f"[r]geq=r='r(X+{intensity},Y)':g='0':b='0'[r2];"
        f"[g]geq=r='0':g='g(X,Y)':b='0'[g2];"
        f"[b]geq=r='0':g='0':b='b(X-{intensity},Y)'[b2];"
        f"[r2][g2][b2]blend=all_mode=addition"
    )


@overlay("flicker", cost=2, tags=["horror", "archive", "unstable"])
def flicker(width=1920, height=1080, intensity=0.2):
    """Random brightness flicker."""
    return (
        f"geq=r='r(X,Y)*(1+{intensity}*sin(t*19)*random(1))':"
        f"g='g(X,Y)*(1+{intensity}*sin(t*19)*random(1))':"
        f"b='b(X,Y)*(1+{intensity}*sin(t*19)*random(1))'"
    )


# ═══════════════════════════════════════════════════════════════════
# Pre-generated overlay inputs (require color source + filter)
# ═══════════════════════════════════════════════════════════════════

@overlay("light_leak", cost=2, tags=["cinematic", "warm", "transition"])
def light_leak_overlay(width=1920, height=1080, color="#f2c14e"):
    """Warm light leak bloom from one side."""
    return (
        f"color=c={color}:s={width}x{height}:d=1,"
        f"geq=r='r(X,Y)*(1-abs(X-250)/600)*(1-abs(Y-{height/2})/{height*0.8})':"
        f"g='g(X,Y)*(1-abs(X-250)/600)*(1-abs(Y-{height/2})/{height*0.8})':"
        f"b='b(X,Y)*(1-abs(X-250)/600)*(1-abs(Y-{height/2})/{height*0.8})',"
        f"boxblur=20"
    )


@overlay("cctv_overlay", cost=3, tags=["surveillance", "tech", "investigation"])
def cctv_overlay(width=1920, height=1080):
    """CCTV-style overlay with timestamp + scanlines + corner text."""
    return (
        f"drawtext=text='REC':fontfile='Poppins-Bold.ttf':fontsize=16:fontcolor=red@0.8:x=20:y=20,"
        f"drawtext=text='CAM 04':fontfile='Poppins-Regular.ttf':fontsize=14:fontcolor=white@0.7:x=20:y=45,"
        f"drawtext=text='%{{pts\\:hms}}':fontfile='Poppins-Regular.ttf':fontsize=14:fontcolor=white@0.7:x=w-tw-20:y=20"
    )


@overlay("letterbox", cost=1, tags=["cinematic", "widescreen"])
def letterbox(width=1920, height=1080, ratio=2.35):
    """Cinematic letterbox bars."""
    bar_h = int(height * (1 - 1 / ratio) / 2)
    return (
        f"drawbox=x=0:y=0:w={width}:h={bar_h}:color=black@1:t=fill,"
        f"drawbox=x=0:y={height - bar_h}:w={width}:h={bar_h}:color=black@1:t=fill"
    )


# ═══════════════════════════════════════════════════════════════════
# Helper
# ═══════════════════════════════════════════════════════════════════

def get_overlay(name: str) -> str:
    """Get the FFmpeg filter snippet for an overlay."""
    entry = registered_overlays.get(name)
    if entry:
        return entry["fn"]()
    return ""


def get_overlay_cost(name: str) -> int:
    entry = registered_overlays.get(name)
    return entry["cost"] if entry else 1


def get_overlay_tags(name: str) -> list:
    entry = registered_overlays.get(name)
    return entry["tags"] if entry else []
