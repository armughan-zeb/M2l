"""FFmpeg-based semantic template renderer for documentary production.

Replaces the Remotion renderer with direct FFmpeg filter_compositing and
Pillow-generated overlay graphics. Takes the same semantic manifest format
from build_semantic_edit_manifest() and produces a finished MP4.

No per-frame JavaScript overhead — renders at roughly real-time speed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from .job_context import JobThreadPoolExecutor as ThreadPoolExecutor

from .media_asset_policy import emergency_media_allowed, is_synthetic_media

_ACTIVE_CANCELLATION = None
_ACTIVE_RENDER_DEADLINE: float | None = None
_ACTIVE_RENDER_BUDGET_SECONDS: float | None = None
_ACTIVE_FAST_RENDER = False
_ACTIVE_ENCODE_THREADS = 0
_SOFTWARE_ENCODER_FORCED = False


class SemanticRenderBudgetExceeded(TimeoutError):
    """Raised when the native renderer exhausts its job-level wall budget."""


def _positive_int(value: Any, default: int, minimum: int = 1, maximum: int = 3600) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = int(default)
    return max(minimum, min(maximum, parsed))


def _resolve_semantic_render_budget(render_config: Mapping[str, Any]) -> int:
    """Resolve the semantic renderer wall while preserving caller intent.

    Standalone callers use ``semantic_render_timeout_seconds`` while the app
    uses ``semantic_render_budget_seconds``.  An environment value of ``0``
    means "not configured" and must not be clamped to the 30-second minimum;
    doing so aborts ordinary one-minute videos during scene encoding.
    """
    explicit_budget = render_config.get("semantic_render_budget_seconds")
    if explicit_budget in (None, "", 0, "0"):
        explicit_budget = render_config.get("semantic_render_timeout_seconds")
    if explicit_budget in (None, "", 0, "0"):
        explicit_budget = os.environ.get("VIDZEO_SEMANTIC_RENDER_BUDGET_SECONDS")
    if explicit_budget in (None, "", 0, "0"):
        explicit_budget = 300
    return _positive_int(explicit_budget, 300, 30, 7200)


def _enabled(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _should_skip_decorative_passes(
    *,
    fast_render_mode: bool,
    n_scenes: int,
    planned_scene_duration: float,
    budget_forced_fast: bool,
) -> bool:
    """Decide when to omit full-frame companion encodes.

    The semantic scene encode is the quality-critical path: it carries the
    selected media, camera motion, captions, and editorial text. Image
    treatment, atmosphere, and scene-overlay passes are additional full
    encodes, so on long timelines they are the safest work to remove when
    speed matters. The threshold is duration-aware because a three-minute
    script can legitimately contain fewer than forty scenes.
    """
    threshold = _positive_int(
        os.environ.get("VIDZEO_FAST_RENDER_SCENE_THRESHOLD", "20") or 20,
        20,
        8,
        120,
    )
    try:
        duration = max(0.0, float(planned_scene_duration or 0.0))
    except (TypeError, ValueError):
        duration = 0.0
    long_timeline = int(n_scenes or 0) >= threshold or duration >= 180.0
    # ``semantic_fast_render`` is an explicit operator request to keep the
    # quality-critical scene encode (motion, transitions, text, captions and
    # audio) while dropping only redundant full-frame companion encodes. Keep
    # the established quality profile for medium short-form timelines, but
    # apply the fast lane to the tiny <=60s jobs that are used for previews
    # and queue smoke tests; those were the runs that previously spent minutes
    # re-encoding atmosphere and image treatment passes.
    short_fast = int(n_scenes or 0) <= 8 and duration <= 60.0
    return bool((fast_render_mode and (long_timeline or short_fast)) or budget_forced_fast)


def _remaining_render_budget() -> float | None:
    if _ACTIVE_RENDER_DEADLINE is None:
        return None
    return _ACTIVE_RENDER_DEADLINE - time.monotonic()


def _budgeted_timeout(requested: float | int | None) -> float | None:
    """Clamp every FFmpeg child to the current renderer wall deadline."""
    remaining = _remaining_render_budget()
    if remaining is None:
        return requested
    if remaining <= 0.05:
        budget = _ACTIVE_RENDER_BUDGET_SECONDS or 0
        raise SemanticRenderBudgetExceeded(
            f"Semantic render exceeded its {budget:.0f}s wall budget"
        )
    if requested is None:
        return max(0.05, remaining)
    return max(0.05, min(float(requested), remaining))


def _run_ffmpeg_process(command, *args, budget_exempt=False, **kwargs):
    """Run ffmpeg, clamping the child timeout to the renderer wall.

    `budget_exempt=True` is used by the last-chance delivery recovery: it must
    never be starved by a shrinking wall remnant (a recovered scene can take
    longer than whatever seconds remain in an already-expired semantic budget).
    """
    cancellation = _ACTIVE_CANCELLATION
    requested_timeout = kwargs.pop("timeout", None)
    if budget_exempt:
        timeout = requested_timeout
    else:
        timeout = _budgeted_timeout(requested_timeout)
    if cancellation is None:
        try:
            return subprocess.run(command, *args, timeout=timeout, **kwargs)
        except subprocess.TimeoutExpired as exc:
            if (
                not budget_exempt
                and _remaining_render_budget() is not None
                and (_remaining_render_budget() or 0) <= 0.25
            ):
                raise SemanticRenderBudgetExceeded(
                    f"Semantic render exhausted its {_ACTIVE_RENDER_BUDGET_SECONDS or 0:.0f}s wall budget"
                ) from exc
            raise
    try:
        from services.cancellation import run_cancellable_process
    except ImportError:
        from .cancellation import run_cancellable_process
    cwd = kwargs.pop("cwd", None)
    check = kwargs.pop("check", False)
    try:
        result = run_cancellable_process(
            command,
            cancellation=cancellation,
            timeout=timeout,
            cwd=cwd,
            **kwargs,
        )
    except subprocess.TimeoutExpired as exc:
        if (
            not budget_exempt
            and _remaining_render_budget() is not None
            and (_remaining_render_budget() or 0) <= 0.25
        ):
            raise SemanticRenderBudgetExceeded(
                f"Semantic render exhausted its {_ACTIVE_RENDER_BUDGET_SECONDS or 0:.0f}s wall budget"
            ) from exc
        raise
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, command, stderr=result.stderr)
    return result

from PIL import Image, ImageDraw, ImageFont

from .text_position_contract import guide_anchor

# VOX_TEMPLATES must be imported with the correct path. The renderer can
# be loaded from different contexts (app.py, standalone test scripts, etc.)
# so we try multiple import paths to handle all cases.
VOX_TEMPLATES = {}
try:
    from services.native_engine.libraries.vox_templates import VOX_TEMPLATES as _VT
    VOX_TEMPLATES = _VT
except ImportError:
    pass
if not VOX_TEMPLATES:
    try:
        from native_engine.libraries.vox_templates import VOX_TEMPLATES as _VT
        VOX_TEMPLATES = _VT
    except ImportError:
        pass
if not VOX_TEMPLATES:
    try:
        from .native_engine.libraries.vox_templates import VOX_TEMPLATES as _VT
        VOX_TEMPLATES = _VT
    except ImportError:
        pass

# Overlay library (film grain, vignette, scanlines, letterbox, ...). The
# manifest can carry a per-scene ``overlays`` list; each entry is either an
# overlay id or a dict {"id": ..., "intensity": ...}.
_OVERLAY_LIBRARY = {}
try:
    from services.native_engine.libraries.overlays import registered_overlays as _RO
    _OVERLAY_LIBRARY = _RO
except ImportError:
    pass
if not _OVERLAY_LIBRARY:
    try:
        from native_engine.libraries.overlays import registered_overlays as _RO
        _OVERLAY_LIBRARY = _RO
    except ImportError:
        pass
if not _OVERLAY_LIBRARY:
    try:
        from .native_engine.libraries.overlays import registered_overlays as _RO
        _OVERLAY_LIBRARY = _RO
    except ImportError:
        pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
WIDTH, HEIGHT = 1920, 1080
FPS = 30
CRF = 17
_CODEC_PRESET_CACHE: str | None = None
def _preset() -> str:
    enc = _video_encoder()
    if enc == "h264_nvenc":
        return "p2"
    elif enc == "h264_qsv":
        return "veryfast"
    elif enc == "h264_amf":
        return "speed"
    return "ultrafast" if _ACTIVE_FAST_RENDER else "veryfast"


def _render_crf() -> int:
    """Use a sane delivery CRF for budget mode instead of mezzanine CRF 17."""
    if _ACTIVE_FAST_RENDER:
        return _positive_int(os.environ.get("VIDZEO_FAST_RENDER_CRF", "22"), 22, 17, 30)
    return CRF


def _encode_threads() -> str:
    """Avoid N concurrent FFmpeg children each consuming every CPU core."""
    if _ACTIVE_ENCODE_THREADS > 0:
        return str(_ACTIVE_ENCODE_THREADS)
    return "0"

ROOT = Path(__file__).resolve().parent.parent.parent
TOOLS_DIR = ROOT / "tools"
FONTS_DIR = ROOT / "fonts"
PUBLIC = ROOT / "public"
SOUND_EFFECTS_DIR = PUBLIC / "sound-effects" / "vidzeo"

# Mapping: template family -> (font_path, overlay_generator)
FONT_BEBAS = str(FONTS_DIR / "BebasNeue-Regular.ttf")
FONT_MONTSERRAT = str(FONTS_DIR / "Montserrat-Bold.ttf")
FONT_POPPINS = str(FONTS_DIR / "Poppins-Bold.ttf")
FONT_POPPINS_REGULAR = str(FONTS_DIR / "Poppins-Regular.ttf")
FONT_PLAYFAIR = str(FONTS_DIR / "PlayfairDisplay-Bold.ttf")
FONT_ANTON = str(FONTS_DIR / "Anton-Regular.ttf")
FONT_ROBOTO = str(FONTS_DIR / "Roboto-Bold.ttf")
FONT_ORBITRON = str(FONTS_DIR / "Orbitron-Bold.ttf")
FONT_CINZEL = str(FONTS_DIR / "Cinzel-Bold.ttf")
FONT_OPEN_SANS = str(FONTS_DIR / "OpenSans-Bold.ttf")
FONT_CAVEAT = str(FONTS_DIR / "Caveat-Bold.ttf")
FONT_CREEPSTER = str(FONTS_DIR / "Creepster-Regular.ttf")
FONT_NOSIFER = str(FONTS_DIR / "Nosifer-Regular.ttf")
FONT_INTER = str(FONTS_DIR / "Inter-SemiBold.ttf")
FONT_DM_SANS = str(FONTS_DIR / "DMSans-Bold.ttf")
FONT_PLUS_JAKARTA = str(FONTS_DIR / "PlusJakartaSans-Bold.ttf")
FONT_ARCHIVO_BLACK = str(FONTS_DIR / "ArchivoBlack-Regular.ttf")
FONT_OSWALD = str(FONTS_DIR / "Oswald-SemiBold.ttf")
FONT_JETBRAINS = str(FONTS_DIR / "JetBrainsMono-Bold.ttf")
FONT_LORA = str(FONTS_DIR / "Lora-Bold.ttf")

# Colors (documentary palette defaults — overridden from manifest profile)
_PALETTE = {
    "background": (11, 19, 32),
    "surface": (22, 38, 59),
    "primary": (244, 240, 230),
    "accent": (242, 193, 78),
    "secondary": (73, 166, 216),
    "danger": (239, 111, 108),
}


def _apply_palette(profile: dict, visual_dna: dict = None):
    """Apply an editing profile palette to the renderer globals."""
    palette = {}
    if isinstance(visual_dna, dict) and visual_dna.get("palette"):
        palette.update(visual_dna.get("palette") or {})
    if isinstance(profile, dict) and profile.get("palette"):
        palette.update(profile.get("palette") or {})
    for key in _PALETTE:
        hex_val = palette.get(key)
        if hex_val and isinstance(hex_val, str) and hex_val.startswith("#") and len(hex_val) == 7:
            try:
                _PALETTE[key] = (
                    int(hex_val[1:3], 16),
                    int(hex_val[3:5], 16),
                    int(hex_val[5:7], 16),
                )
            except (ValueError, IndexError):
                pass

    # Also apply typography to font selections
    typography = (visual_dna.get("typography") if isinstance(visual_dna, dict) else {}) or (profile.get("typography") if isinstance(profile, dict) else {}) or {}
    display_font = str(typography.get("display", "")).lower()
    body_font = str(typography.get("body", "")).lower()
    # Map profile font names to actual font files
    _FONT_MAP = {
        "anton": FONT_ANTON, "bebas": FONT_BEBAS, "bebas neue": FONT_BEBAS,
        "montserrat": FONT_MONTSERRAT, "poppins": FONT_POPPINS,
        "open_sans": FONT_OPEN_SANS, "open sans": FONT_OPEN_SANS,
        "roboto": FONT_ROBOTO, "caveat": FONT_CAVEAT,
        "creepster": FONT_CREEPSTER, "nosifer": FONT_NOSIFER,
        "playfair": FONT_PLAYFAIR, "playfair display": FONT_PLAYFAIR,
        "cinzel": FONT_CINZEL, "orbitron": FONT_ORBITRON,
        "inter": FONT_INTER, "dm_sans": FONT_DM_SANS, "dm sans": FONT_DM_SANS,
        "plus_jakarta": FONT_PLUS_JAKARTA, "plus jakarta sans": FONT_PLUS_JAKARTA,
        "archivo_black": FONT_ARCHIVO_BLACK, "archivo black": FONT_ARCHIVO_BLACK,
        "oswald": FONT_OSWALD, "jetbrains_mono": FONT_JETBRAINS,
        "jetbrains mono": FONT_JETBRAINS, "lora": FONT_LORA,
    }
    global _DISPLAY_FONT_OVERRIDE, _BODY_FONT_OVERRIDE
    _DISPLAY_FONT_OVERRIDE = _FONT_MAP.get(display_font, FONT_POPPINS)
    _BODY_FONT_OVERRIDE = _FONT_MAP.get(body_font, FONT_OPEN_SANS)


_DISPLAY_FONT_OVERRIDE = FONT_POPPINS
_BODY_FONT_OVERRIDE = FONT_OPEN_SANS


# Shorthand accessors that respect the active palette
def _bg(alpha=255): return (*_PALETTE["background"], alpha) if alpha != 255 else _PALETTE["background"]
def _surf(alpha=255): return (*_PALETTE["surface"], alpha) if alpha != 255 else _PALETTE["surface"]
def _pri(alpha=255): return (*_PALETTE["primary"], alpha) if alpha != 255 else _PALETTE["primary"]
def _acc(alpha=255): return (*_PALETTE["accent"], alpha) if alpha != 255 else _PALETTE["accent"]
def _sec(alpha=255): return (*_PALETTE["secondary"], alpha) if alpha != 255 else _PALETTE["secondary"]
def _dng(alpha=255): return (*_PALETTE["danger"], alpha) if alpha != 255 else _PALETTE["danger"]
SHADOW_COLOR = (0, 0, 0)


def _legible(color, minimum_luma=150):
    """Brighten a too-dark accent so overlay text stays readable on footage.

    Dark brand accents (e.g. editorial magenta 0xe137eb on near-black bg)
    vanish against dark video frames. Keep the hue, but lift the perceived
    luminance until the color clears the readability threshold.
    """
    r, g, b = (int(c) for c in color[:3])
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    if luma >= minimum_luma:
        return (r, g, b)
    chroma = max(r, g, b) - min(r, g, b)
    if chroma < 24:
        return _PALETTE["primary"]
    k = (minimum_luma - luma) / max(1.0, 255.0 - luma)
    return (
        min(255, int(r + (255 - r) * k)),
        min(255, int(g + (255 - g) * k)),
        min(255, int(b + (255 - b) * k)),
    )

# Font mapping per template: template_id -> (display_font, body_font)
_TEMPLATE_FONTS: Dict[str, Tuple[str, str]] = {
    "chapter-opener": (FONT_POPPINS, FONT_OPEN_SANS),
    "keyword-reveal": (FONT_POPPINS, FONT_OPEN_SANS),
    "date-card": (FONT_POPPINS, FONT_OPEN_SANS),
    "lower-third": (FONT_POPPINS, FONT_OPEN_SANS),
    "evidence-callout": (FONT_POPPINS, FONT_OPEN_SANS),
    "quote-card": (FONT_PLAYFAIR, FONT_OPEN_SANS),
    "stat-counter": (FONT_POPPINS, FONT_OPEN_SANS),
    "money-counter": (FONT_POPPINS, FONT_OPEN_SANS),
    "kinetic-statement": (FONT_POPPINS, FONT_OPEN_SANS),
    "cold-open": (FONT_POPPINS, FONT_OPEN_SANS),
    "paper-explainer": (FONT_PLAYFAIR, FONT_OPEN_SANS),
    "profile-dossier": (FONT_POPPINS, FONT_OPEN_SANS),
    "evidence-board": (FONT_POPPINS, FONT_OPEN_SANS),
    "field-caption": (FONT_OPEN_SANS, FONT_OPEN_SANS),
    "exhibit-label": (FONT_OPEN_SANS, FONT_OPEN_SANS),
    "end-card": (FONT_POPPINS, FONT_OPEN_SANS),
    "listicle-build": (FONT_POPPINS, FONT_OPEN_SANS),
    "witness-quote": (FONT_PLAYFAIR, FONT_OPEN_SANS),
    "source-highlight": (FONT_PLAYFAIR, FONT_OPEN_SANS),
    "word-cascade": (FONT_POPPINS, FONT_OPEN_SANS),
    "redaction-reveal": (FONT_POPPINS, FONT_OPEN_SANS),
    "cause-effect": (FONT_POPPINS, FONT_OPEN_SANS),
    "split-screen": (FONT_POPPINS, FONT_OPEN_SANS),
    "countdown-clock": (FONT_ORBITRON, FONT_OPEN_SANS),
    "archival-frame": (FONT_OPEN_SANS, FONT_OPEN_SANS),
    "data-race": (FONT_ORBITRON, FONT_OPEN_SANS),
    "blueprint-reveal": (FONT_CINZEL, FONT_OPEN_SANS),
    "signal-scan": (FONT_ORBITRON, FONT_OPEN_SANS),
    "bar-chart": (FONT_POPPINS, FONT_OPEN_SANS),
    "line-chart": (FONT_POPPINS, FONT_OPEN_SANS),
    "donut-chart": (FONT_POPPINS, FONT_OPEN_SANS),
    "decision-tree": (FONT_POPPINS, FONT_OPEN_SANS),
    "newsprint-reveal": (FONT_POPPINS, FONT_OPEN_SANS),
    "timeline-trace": (FONT_POPPINS, FONT_OPEN_SANS),
    "map-route": (FONT_POPPINS, FONT_OPEN_SANS),
    "gallery-stack": (FONT_POPPINS, FONT_OPEN_SANS),
    "callout-arrow": (FONT_POPPINS, FONT_OPEN_SANS),
    "pictogram-stat": (FONT_POPPINS, FONT_OPEN_SANS),
    "spotlight-lens": (FONT_POPPINS, FONT_OPEN_SANS),
    "metric-grid": (FONT_POPPINS, FONT_OPEN_SANS),
    "stamp-mark": (FONT_POPPINS, FONT_OPEN_SANS),
    "index-card": (FONT_POPPINS, FONT_OPEN_SANS),
}

_NVENC_AVAILABLE: Optional[bool] = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ffmpeg() -> str:
    configured = str(os.environ.get("VIDZEO_FFMPEG_PATH") or "").strip()
    if configured:
        return configured
    ff = TOOLS_DIR / "ffmpeg" / "ffmpeg.exe"
    return str(ff) if ff.exists() else "ffmpeg"


def _ffprobe() -> str:
    configured = str(os.environ.get("VIDZEO_FFPROBE_PATH") or "").strip()
    if configured:
        return configured
    probe = TOOLS_DIR / "ffmpeg" / "ffprobe.exe"
    return str(probe) if probe.exists() else "ffprobe"


@lru_cache(maxsize=512)
def _probe_duration_seconds(media_src: str) -> float:
    """Probe container duration without decoding media frames."""
    try:
        result = _run_ffmpeg_process(
            [
                _ffprobe(), "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(media_src),
            ],
            capture_output=True,
            text=True,
            timeout=4,
        )
        if result.returncode == 0 and str(result.stdout or "").strip():
            return max(0.0, float(str(result.stdout).strip()))
    except SemanticRenderBudgetExceeded:
        raise
    except Exception:
        pass
    return 0.0


@lru_cache(maxsize=512)
def _probe_media_dimensions(media_src: str) -> Tuple[int, int]:
    """Read the first video stream dimensions without decoding frames."""
    try:
        result = _run_ffmpeg_process(
            [
                _ffprobe(), "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0:s=x",
                str(media_src),
            ],
            capture_output=True,
            text=True,
            timeout=4,
        )
        if result.returncode == 0:
            raw = str(result.stdout or "").strip().splitlines()[0]
            width, height = (int(part) for part in raw.split("x", 1))
            if width > 0 and height > 0:
                return width, height
    except SemanticRenderBudgetExceeded:
        raise
    except Exception:
        pass
    return 0, 0


def _video_encoder() -> str:
    """Auto-detect optimal hardware encoder (NVENC, QSV, AMF) or fall back to libx264."""
    try:
        from services.gpu_accelerator import detect_optimal_gpu_encoder
        info = detect_optimal_gpu_encoder()
        return info.get("codec", "libx264")
    except Exception:
        return "libx264"


def _video_encoder_args() -> List[str]:
    """Return complete video encoding arguments including codec, rate control, pix_fmt, and GOP flags."""
    if _SOFTWARE_ENCODER_FORCED:
        preset = "ultrafast" if _ACTIVE_FAST_RENDER else "veryfast"
        return ["-c:v", "libx264", "-preset", preset, "-crf", str(_render_crf()), "-pix_fmt", "yuv420p", "-tune", "fastdecode", "-g", "60"]
    try:
        from services.gpu_accelerator import get_ffmpeg_video_encoder_args
        preset = "ultrafast" if _ACTIVE_FAST_RENDER else "veryfast"
        return get_ffmpeg_video_encoder_args(preset=preset, crf=_render_crf())
    except Exception:
        preset = "ultrafast" if _ACTIVE_FAST_RENDER else "veryfast"
        return ["-c:v", "libx264", "-preset", preset, "-crf", str(_render_crf()), "-pix_fmt", "yuv420p", "-tune", "fastdecode", "-g", "60"]


def _hardware_encoder_failed(stderr: Any) -> bool:
    """Recognize a missing/incompatible hardware encoder and fail over once.

    FFmpeg builds can advertise NVENC/QSV/AMF even when the matching runtime
    DLL or device is unavailable.  Retrying the same command only burns the
    render budget; the software encoder is a deterministic recovery tier.
    """
    text = str(stderr or "").casefold()
    return any(marker in text for marker in (
        "cannot load nvcuda", "no capable devices found", "device failed",
        "mfx error", "qsv", "h264_qsv", "h264_nvenc", "h264_amf",
        "cannot init encoder", "error while opening encoder",
    ))


def _force_software_encoder(stderr: Any = "") -> None:
    global _SOFTWARE_ENCODER_FORCED
    if not _SOFTWARE_ENCODER_FORCED and _hardware_encoder_failed(stderr):
        _SOFTWARE_ENCODER_FORCED = True
        print("   [gpu-autodetect] Hardware encoder unavailable at render time; falling back to libx264", flush=True)


# Thread-local for passing template_id into overlay generators
_TLS = threading.local()


def _font(size: int = 48, bold: bool = True, template_id: str = "") -> ImageFont.FreeTypeFont:
    tid = template_id or getattr(_TLS, "current_template_id", "")
    path = _DISPLAY_FONT_OVERRIDE if bold else _BODY_FONT_OVERRIDE
    if tid:
        fonts = _TEMPLATE_FONTS.get(tid)
        if fonts:
            if tid in {"quote-card", "witness-quote", "source-highlight", "paper-explainer"}:
                path = FONT_PLAYFAIR if bold else FONT_POPPINS_REGULAR
            elif tid in {"countdown-clock", "data-race", "signal-scan"}:
                path = FONT_ORBITRON if bold else FONT_POPPINS_REGULAR
            elif tid in {"blueprint-reveal"}:
                path = FONT_CINZEL if bold else FONT_POPPINS_REGULAR
            else:
                path = _DISPLAY_FONT_OVERRIDE if bold else _BODY_FONT_OVERRIDE
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _draw_multiline_text(
    draw: ImageDraw.ImageDraw,
    xy: Tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int = WIDTH - 200,
    line_spacing: int = 12,
    fill: Tuple[int, int, int] = _pri(),
    anchor: str = "mt",
    align: str = "center",
):
    """Draw wrapped text centered at (x, y)."""
    lines = []
    for paragraph in text.split("\n"):
        wrapped = textwrap.wrap(paragraph, width=int(max_width / (font.size * 0.6))) if font.size > 20 else [paragraph]
        lines.extend(wrapped or [""])
    x, y = xy
    line_height = font.size + line_spacing
    total_height = len(lines) * line_height
    start_y = y - total_height / 2
    for i, line in enumerate(lines):
        line_x = x - _text_width(line, font) / 2 if align == "center" else x
        draw.text((line_x, start_y + i * line_height), line, font=font, fill=fill)


def _text_width(text: str, font: ImageFont.FreeTypeFont) -> float:
    try:
        return font.getlength(text)
    except Exception:
        return len(text) * font.size * 0.55


def _wrap_to_fit(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    """Return text truncated with ellipsis to fit max_width."""
    if _text_width(text, font) <= max_width:
        return text
    for i in range(len(text), 0, -1):
        candidate = text[:i] + "..."
        if _text_width(candidate, font) <= max_width:
            return candidate
    return text[:1] + "..."


def _draw_text_shadow(
    draw: ImageDraw.ImageDraw,
    xy: Tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    shadow_offset: int = 3,
    shadow_opacity: int = 180,
    anchor: str = "mt",
):
    """Draw text with a drop shadow for readability over bright media."""
    sx, sy = xy
    for dx, dy in [(shadow_offset, shadow_offset), (shadow_offset, -shadow_offset),
                    (-shadow_offset, shadow_offset), (-shadow_offset, -shadow_offset)]:
        draw.text((sx + dx, sy + dy), text, font=font, fill=(*SHADOW_COLOR, shadow_opacity), anchor=anchor)


def _sanitize_name(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", name)[:64]


def _copy_asset(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_size == src.stat().st_size:
        return
    shutil.copy2(src, dst)


def _get_ffmpeg_img_path(img_path: str, temp_dir: str) -> str:
    """Normalize image to FFmpeg-friendly PNG."""
    source = Path(img_path).resolve()
    dest = (Path(temp_dir) / f"{hashlib.md5(str(source).encode()).hexdigest()[:16]}.png").resolve()
    if dest.exists():
        return str(dest)
    try:
        with Image.open(source) as img:
            img.load()
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA" if img.mode == "P" and "transparency" in img.info else "RGB")
            elif img.mode == "RGBA":
                bg = Image.new("RGB", img.size, "black")
                bg.paste(img, mask=img.getchannel("A"))
                img = bg
            dest.parent.mkdir(parents=True, exist_ok=True)
            img.save(str(dest), format="PNG")
        return str(dest) if dest.exists() and dest.stat().st_size > 0 else str(source)
    except Exception:
        return str(source)


# ---------------------------------------------------------------------------
# Overlay generators — each returns a (PIL.Image, duration) or None
# ---------------------------------------------------------------------------

def _overlay_chapter_opener(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    title = props.get("title", "CHAPTER").upper()[:60]
    kicker = props.get("kicker", "CHAPTER").upper()[:30]
    fnt_kicker = _font(30, bold=True)
    fnt_title = _font(88, bold=True)
    # Thin top accent bar
    draw.rectangle([0, 0, WIDTH, 6], fill=(*_acc(), 220))
    # Kicker
    draw.text((60, 40), kicker, font=fnt_kicker, fill=_acc())
    # Title with subtle text shadow
    title = _wrap_to_fit(title, fnt_title, WIDTH - 160)
    sx, sy = WIDTH / 2, HEIGHT / 2 - 20
    draw.text((sx + 3, sy + 3), title, font=fnt_title, fill=(0, 0, 0, 120), anchor="mt")
    draw.text((sx, sy), title, font=fnt_title, fill=_pri(), anchor="mt")
    # Bottom thin accent bar
    draw.rectangle([WIDTH // 4, HEIGHT - 60, WIDTH * 3 // 4, HEIGHT - 54], fill=(*_acc(), 160))
    return img


def _overlay_keyword_reveal(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    text_val = props.get("text", "KEY IDEA").upper()[:70]
    detail = props.get("detail", "")[:150]
    fnt_text = _font(76, bold=True)
    fnt_detail = _font(28, bold=False)
    # Text with shadow directly on image (no full-screen tint)
    text_val = _wrap_to_fit(text_val, fnt_text, WIDTH - 200)
    tw = _text_width(text_val, fnt_text)
    tx, ty = WIDTH / 2, HEIGHT / 2 - 50
    draw.text((tx + 2, ty + 2), text_val, font=fnt_text, fill=(0, 0, 0, 100), anchor="mt")
    draw.text((tx, ty), text_val, font=fnt_text, fill=_pri(), anchor="mt")
    # Underline bar
    bar_w = min(tw + 40, WIDTH - 120)
    draw.rectangle([(WIDTH - bar_w) / 2, HEIGHT / 2 + 8, (WIDTH + bar_w) / 2, HEIGHT / 2 + 16], fill=(*_acc(), 230))
    if detail:
        detail = _wrap_to_fit(detail, fnt_detail, WIDTH - 200)
        draw.text((WIDTH / 2, HEIGHT / 2 + 60), detail, font=fnt_detail, fill=(*_pri(), 200), anchor="mt")
    return img


def _overlay_date_card(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    label = props.get("label", "").upper()[:40]
    kicker = props.get("kicker", "TIMELINE").upper()[:46]
    fnt_label = _font(100, bold=True)
    fnt_kicker = _font(28, bold=True)
    # Thin horizontal line across center
    draw.line([0, HEIGHT / 2, WIDTH, HEIGHT / 2], fill=(*_pri(), 40), width=1)
    # Label with shadow
    label = _wrap_to_fit(label, fnt_label, WIDTH - 200)
    draw.text((WIDTH / 2, HEIGHT / 2 + 20), label, font=fnt_label, fill=_pri(), anchor="mt")
    # Accent dot + line
    draw.rectangle([(WIDTH - 200) / 2, HEIGHT / 2 + 120, (WIDTH - 200) / 2 + 40, HEIGHT / 2 + 130], fill=(*_acc(), 255))
    return img


def _overlay_lower_third(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    title = props.get("title", "").upper()[:32]
    subtitle = (props.get("subtitle") or props.get("detail") or "")[:24]
    fnt_title = _font(52, bold=True)
    fnt_sub = _font(24, bold=False)
    title = _wrap_to_fit(title, fnt_title, 620)
    title_w = int(_text_width(title, fnt_title))
    box_w = min(720, max(260, title_w + 68))
    box_h = 92 if not subtitle else 140
    box_x = 60
    box_y = HEIGHT - box_h - 60
    # Background box
    draw.rectangle([box_x, box_y, box_x + box_w, box_y + box_h], fill=(*_surf(), 220))
    # Left accent bar
    draw.rectangle([box_x, box_y, box_x + 8, box_y + box_h], fill=(*_acc(), 255))
    _draw_text_shadow(draw, (box_x + 30, box_y + 20), title, fnt_title, anchor="lt")
    draw.text((box_x + 30, box_y + 20), title, font=fnt_title, fill=_pri(), anchor="lt")
    if subtitle:
        _draw_text_shadow(draw, (box_x + 30, box_y + 75), subtitle, fnt_sub, anchor="lt")
        draw.text((box_x + 30, box_y + 75), subtitle, font=fnt_sub, fill=(*_pri(), 180), anchor="lt")
    return img


def _overlay_evidence_callout(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    text_val = props.get("text", "").upper()[:80]
    detail = props.get("detail", "")[:180]
    fnt_text = _font(72, bold=True)
    fnt_detail = _font(28, bold=False)
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(*_bg(), 190))
    # Border box
    margin = 140
    draw.rectangle([margin, 200, WIDTH - margin, HEIGHT - 200], outline=(*_acc(), 200), width=4)
    text_val = _wrap_to_fit(text_val, fnt_text, WIDTH - 400)
    draw.text((WIDTH / 2, HEIGHT / 2 - 60), text_val, font=fnt_text, fill=_pri(), anchor="mt")
    if detail:
        detail = _wrap_to_fit(detail, fnt_detail, WIDTH - 400)
        draw.text((WIDTH / 2, HEIGHT / 2 + 60), detail, font=fnt_detail, fill=(*_pri(), 180), anchor="mt")
    return img


def _overlay_quote_card(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    quote = props.get("quote", "")[:200]
    attribution = props.get("attribution", "").upper()[:54]
    fnt_quote = _font(52, bold=False)
    fnt_attr = _font(28, bold=True)
    draw.rectangle([100, 100, WIDTH - 100, HEIGHT - 100], fill=(*_surf(), 220))
    # Opening quote mark
    try:
        fnt_big = ImageFont.truetype(FONT_PLAYFAIR, 100)
        draw.text((160, 140), "\u201c", font=fnt_big, fill=(*_acc(), 200))
    except Exception:
        pass
    q = _wrap_to_fit(quote, fnt_quote, WIDTH - 400)
    draw.text((WIDTH / 2, HEIGHT / 2 - 40), q, font=fnt_quote, fill=_pri(), anchor="mt")
    # Accent line
    draw.rectangle([(WIDTH - 200) / 2, HEIGHT / 2 + 40, (WIDTH + 200) / 2, HEIGHT / 2 + 48], fill=(*_acc(), 255))
    if attribution:
        draw.text((WIDTH / 2, HEIGHT / 2 + 90), attribution, font=fnt_attr, fill=(*_acc(), 200), anchor="mt")
    return img


def _overlay_stat_counter(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    value = props.get("value", "0")
    label = props.get("label", "").upper()[:55]
    fnt_val = _font(120, bold=True)
    fnt_label = _font(32, bold=True)
    # Large number with shadow on clean image
    sv = str(value)
    tw = _text_width(sv, fnt_val)
    cx, cy = WIDTH / 2, HEIGHT / 2 - 30
    # Background card behind number only
    card_pad = 30
    draw.rounded_rectangle([cx - tw/2 - card_pad, cy - 70, cx + tw/2 + card_pad, cy + 40],
                          radius=12, fill=(*_surf(), 160))
    draw.text((cx + 2, cy + 2), sv, font=fnt_val, fill=(0, 0, 0, 80), anchor="mt")
    draw.text((cx, cy), sv, font=fnt_val, fill=(*_legible(_acc()), 255), anchor="mt")
    draw.text((WIDTH / 2, HEIGHT / 2 + 70), label, font=fnt_label, fill=(*_pri(), 255), anchor="mt")
    return img


def _overlay_money_counter(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    value = props.get("value", "0")
    unit = props.get("unit", "")
    label = props.get("label", "").upper()[:54]
    fnt_val = _font(120, bold=True)
    fnt_unit = _font(56, bold=True)
    fnt_label = _font(28, bold=True)
    display = f"${value}{unit}"
    tw = _text_width(display, fnt_val)
    cx, cy = WIDTH / 2, HEIGHT / 2 - 30
    card_pad = 30
    draw.rounded_rectangle([cx - tw/2 - card_pad, cy - 70, cx + tw/2 + card_pad, cy + 40],
                          radius=12, fill=(*_surf(), 160))
    draw.text((cx + 2, cy + 2), display, font=fnt_val, fill=(0, 0, 0, 80), anchor="mt")
    draw.text((cx, cy), display, font=fnt_val, fill=_acc(), anchor="mt")
    draw.text((WIDTH / 2, HEIGHT / 2 + 70), label, font=fnt_label, fill=(*_pri(), 200), anchor="mt")
    return img


def _overlay_kinetic_statement(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    text_val = props.get("text", "").upper()[:72]
    detail = props.get("detail", "")[:150]
    fnt_text = _font(72, bold=True)
    fnt_detail = _font(26, bold=False)
    text_val = _wrap_to_fit(text_val, fnt_text, WIDTH - 160)
    tw = _text_width(text_val, fnt_text)
    cx, cy = WIDTH / 2, HEIGHT / 2 - 40
    draw.text((cx + 2, cy + 2), text_val, font=fnt_text, fill=(0, 0, 0, 100), anchor="mt")
    draw.text((cx, cy), text_val, font=fnt_text, fill=_pri(), anchor="mt")
    if detail:
        detail = _wrap_to_fit(detail, fnt_detail, WIDTH - 200)
        draw.text((WIDTH / 2, HEIGHT / 2 + 50), detail, font=fnt_detail, fill=(*_pri(), 200), anchor="mt")
    return img


def _overlay_cold_open(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    title = props.get("title", "").upper()[:64]
    kicker = props.get("kicker", "THE QUESTION")[:36]
    subtitle = props.get("subtitle", "")[:150]
    fnt_kicker = _font(28, bold=True)
    fnt_title = _font(76, bold=True)
    fnt_sub = _font(24, bold=False)
    # Thin top accent bar
    draw.rectangle([0, 0, WIDTH, 5], fill=(*_acc(), 200))
    draw.text((60, 50), kicker, font=fnt_kicker, fill=_acc())
    title = _wrap_to_fit(title, fnt_title, WIDTH - 200)
    sx, sy = 60, 110
    draw.text((sx, sy), title, font=fnt_title, fill=_pri(), anchor="lt")
    # Accent bar under title
    bar_w = min(360, int(_text_width(title, fnt_title)) + 40)
    draw.rectangle([sx, sy + 60, sx + bar_w, sy + 68], fill=(*_acc(), 220))
    if subtitle:
        draw.text((sx, sy + 100), subtitle, font=fnt_sub, fill=(*_pri(), 180), anchor="lt")
    return img


def _overlay_paper_explainer(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    title = props.get("title", "SOURCE NOTE").upper()[:48]
    body = props.get("body", "")[:180]
    source = props.get("source", "")[:56]
    fnt_title = _font(28, bold=True)
    fnt_body = _font(36, bold=False)
    fnt_source = _font(20, bold=False)
    # Paper-like box
    margin = 200
    draw.rectangle([margin, 140, WIDTH - margin, HEIGHT - 140], fill=(*_pri(), 230))
    draw.text((margin + 40, 180), title, font=fnt_title, fill=(*_bg(), 255))
    draw.rectangle([margin + 40, 220, margin + 160, 225], fill=(*_acc(), 255))
    body = _wrap_to_fit(body, fnt_body, WIDTH - 2 * margin - 80)
    draw.text((margin + 40, 270), body, font=fnt_body, fill=(*_bg(), 230), anchor="lt")
    if source:
        draw.text((margin + 40, HEIGHT - 180), f"SOURCE: {source}", font=fnt_source, fill=(*_bg(), 150), anchor="lt")
    return img


def _overlay_profile_dossier(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    title = props.get("title", "").upper()[:56]
    subtitle = props.get("subtitle", "PROFILE").upper()[:36]
    detail = props.get("detail", "")[:120]
    fnt_sub = _font(24, bold=True)
    fnt_title = _font(48, bold=True)
    fnt_detail = _font(20, bold=False)
    box_w = 500
    box_h = 260
    box_x = 60
    box_y = HEIGHT - box_h - 100
    draw.rectangle([box_x, box_y, box_x + box_w, box_y + box_h], fill=(*_bg(), 220))
    # Left accent bar
    draw.rectangle([box_x, box_y, box_x + 10, box_y + box_h], fill=(*_acc(), 255))
    draw.text((box_x + 30, box_y + 25), subtitle, font=fnt_sub, fill=_acc())
    draw.text((box_x + 30, box_y + 60), title, font=fnt_title, fill=_pri(), anchor="lt")
    if detail:
        draw.text((box_x + 30, box_y + 125), detail, font=fnt_detail, fill=(*_pri(), 180), anchor="lt")
    return img


def _overlay_evidence_board(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    text_val = props.get("text", "").upper()[:76]
    detail = props.get("detail", "")[:168]
    stamp = props.get("stamp", "EVIDENCE")[:16]
    fnt_stamp = _font(28, bold=True)
    fnt_text = _font(56, bold=True)
    fnt_detail = _font(26, bold=False)
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(*_bg(), 190))
    # Torn paper effect via polygon approximation
    margin = 160
    draw.rectangle([margin, 120, WIDTH - margin, HEIGHT - 120], fill=(*_surf(), 230))
    draw.rectangle([margin, 120, WIDTH - margin, 130], fill=(*_acc(), 255))
    draw.text((margin + 30, 160), stamp, font=fnt_stamp, fill=_dng())
    text_val = _wrap_to_fit(text_val, fnt_text, WIDTH - 2 * margin - 60)
    draw.text((WIDTH / 2, HEIGHT / 2 - 40), text_val, font=fnt_text, fill=_pri(), anchor="mt")
    if detail:
        detail = _wrap_to_fit(detail, fnt_detail, WIDTH - 2 * margin - 60)
        draw.text((WIDTH / 2, HEIGHT / 2 + 50), detail, font=fnt_detail, fill=(*_pri(), 180), anchor="mt")
    return img


def _overlay_field_caption(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    title = props.get("title", "").upper()[:62]
    subtitle = props.get("subtitle", "").upper()[:38]
    fnt_title = _font(34, bold=True)
    fnt_sub = _font(20, bold=False)
    box_w = 500
    box_h = 100
    box_x = WIDTH - box_w - 60
    box_y = HEIGHT - box_h - 60
    draw.rectangle([box_x, box_y, box_x + box_w, box_y + box_h], fill=(*_surf(), 200))
    _draw_text_shadow(draw, (box_x + 20, box_y + 15), title, fnt_title, anchor="lt")
    draw.text((box_x + 20, box_y + 15), title, font=fnt_title, fill=_pri(), anchor="lt")
    if subtitle:
        _draw_text_shadow(draw, (box_x + 20, box_y + 60), subtitle, fnt_sub, anchor="lt")
        draw.text((box_x + 20, box_y + 60), subtitle, font=fnt_sub, fill=(*_pri(), 160), anchor="lt")
    return img


def _overlay_exhibit_label(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    title = props.get("title", "").upper()[:58]
    subtitle = props.get("subtitle", "ARCHIVE OBJECT").upper()[:42]
    fnt_title = _font(30, bold=True)
    fnt_sub = _font(18, bold=False)
    box_w = 380
    box_h = 90
    box_x = WIDTH - box_w - 40
    box_y = HEIGHT - box_h - 40
    draw.rectangle([box_x, box_y, box_x + box_w, box_y + box_h], fill=(*_surf(), 200))
    _draw_text_shadow(draw, (box_x + 15, box_y + 10), title, fnt_title, anchor="lt")
    draw.text((box_x + 15, box_y + 10), title, font=fnt_title, fill=_pri(), anchor="lt")
    _draw_text_shadow(draw, (box_x + 15, box_y + 52), subtitle, fnt_sub, anchor="lt")
    draw.text((box_x + 15, box_y + 52), subtitle, font=fnt_sub, fill=(*_pri(), 160), anchor="lt")
    return img


def _overlay_end_card(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    title = props.get("title", "FOLLOW THE STORY").upper()[:60]
    subtitle = props.get("subtitle", "").upper()[:60]
    fnt_title = _font(80, bold=True)
    fnt_sub = _font(30, bold=True)
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(*_bg(), 210))
    draw.text((WIDTH / 2, HEIGHT / 2 - 40), title, font=fnt_title, fill=_pri(), anchor="mt")
    if subtitle:
        draw.text((WIDTH / 2, HEIGHT / 2 + 60), subtitle, font=fnt_sub, fill=(*_acc(), 200), anchor="mt")
    return img


def _overlay_listicle_build(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    title = props.get("title", "").upper()[:54]
    items = props.get("items", ["KEY POINT", "WHAT HAPPENED", "WHY IT MATTERED"])[:4]
    fnt_title = _font(36, bold=True)
    fnt_item = _font(32, bold=False)
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(*_bg(), 180))
    draw.text((WIDTH / 2, 120), title, font=fnt_title, fill=_acc(), anchor="mt")
    start_y = 240
    for i, item in enumerate(items):
        y = start_y + i * 120
        cx, cy = WIDTH // 2 - 280, y + 30
        draw.ellipse([cx - 22, cy - 22, cx + 22, cy + 22], fill=(*_acc(), 255))
        draw.text((cx, cy), str(i + 1), font=_font(28, bold=True), fill=(*_bg(), 255), anchor="mm")
        item_str = str(item.get("label") if isinstance(item, dict) else item).upper()[:50]
        item_str = _wrap_to_fit(item_str, fnt_item, 800)
        draw.text((cx + 50, cy), item_str, font=fnt_item, fill=_pri(), anchor="lm")
        draw.line([cx - 180, y + 65, cx + 420, y + 65], fill=(*_pri(), 40), width=1)
    return img


def _overlay_witness_quote(props: dict) -> Image.Image:
    return _overlay_quote_card(props)


def _overlay_source_highlight(props: dict) -> Image.Image:
    return _overlay_paper_explainer(props)


def _overlay_word_cascade(props: dict) -> Image.Image:
    return _overlay_kinetic_statement(props)


def _overlay_redaction_reveal(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    text_val = props.get("text", "").upper()[:82]
    label = props.get("label", "RESTRICTED FILE")[:40]
    detail = props.get("detail", "")[:150]
    fnt_label = _font(22, bold=True)
    fnt_text = _font(56, bold=True)
    fnt_detail = _font(26, bold=False)
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(*_bg(), 200))
    draw.text((WIDTH / 2, 140), label, font=fnt_label, fill=_dng(), anchor="mt")
    text_val = _wrap_to_fit(text_val, fnt_text, WIDTH - 200)
    draw.text((WIDTH / 2, HEIGHT / 2 - 40), text_val, font=fnt_text, fill=_pri(), anchor="mt")
    # Redaction bar overlay (simulated redaction)
    bar_w = min(400, int(_text_width(text_val, fnt_text)))
    draw.rectangle([(WIDTH - bar_w) / 2, HEIGHT / 2 - 5, (WIDTH + bar_w) / 2, HEIGHT / 2 + 15], fill=(*_bg(), 180))
    if detail:
        detail = _wrap_to_fit(detail, fnt_detail, WIDTH - 300)
        draw.text((WIDTH / 2, HEIGHT / 2 + 70), detail, font=fnt_detail, fill=(*_pri(), 180), anchor="mt")
    return img


def _overlay_cause_effect(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cause = props.get("cause", "THE EVENT").upper()[:50]
    effect = props.get("effect", "THE CONSEQUENCE").upper()[:50]
    fnt_cause = _font(40, bold=True)
    fnt_effect = _font(40, bold=True)
    fnt_label = _font(22, bold=True)
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(*_bg(), 190))
    label = props.get("label", "CAUSE \u2192  EFFECT")
    draw.text((WIDTH / 2, 120), label, font=fnt_label, fill=(*_acc(), 200), anchor="mt")
    cause = _wrap_to_fit(cause, fnt_cause, WIDTH // 2 - 120)
    draw.text((WIDTH / 4, HEIGHT / 2), cause, font=fnt_cause, fill=_pri(), anchor="mm")
    # Arrow
    draw.text((WIDTH / 2, HEIGHT / 2), "\u2192", font=_font(60, bold=True), fill=_acc(), anchor="mm")
    effect = _wrap_to_fit(effect, fnt_effect, WIDTH // 2 - 120)
    draw.text((3 * WIDTH / 4, HEIGHT / 2), effect, font=fnt_effect, fill=_pri(), anchor="mm")
    return img


def _overlay_decision_tree(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    root = props.get("root", "THE DECISION").upper()[:48]
    left = props.get("left", "WAIT").upper()[:38]
    right = props.get("right", "ACT").upper()[:38]
    label = props.get("label", "THE TURNING POINT")[:42]
    fnt_root = _font(36, bold=True)
    fnt_branch = _font(40, bold=True)
    fnt_label = _font(22, bold=True)
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(*_bg(), 190))
    draw.text((WIDTH / 2, 120), label, font=fnt_label, fill=_acc(), anchor="mt")
    # Root node
    draw.text((WIDTH / 2, 260), root, font=fnt_root, fill=_pri(), anchor="mt")
    # Branches
    draw.text((WIDTH / 4, HEIGHT / 2 + 80), left, font=fnt_branch, fill=_sec(), anchor="mm")
    draw.text((3 * WIDTH / 4, HEIGHT / 2 + 80), right, font=fnt_branch, fill=(*_dng(), 220), anchor="mm")
    # Lines connecting root to branches
    draw.line([WIDTH / 2, 300, WIDTH / 4, HEIGHT / 2 + 40], fill=(*_pri(), 120), width=2)
    draw.line([WIDTH / 2, 300, 3 * WIDTH / 4, HEIGHT / 2 + 40], fill=(*_pri(), 120), width=2)
    return img


def _overlay_split_screen(props: dict) -> Image.Image:
    """Return a split-screen divider overlay."""
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    left = props.get("left", "BEFORE").upper()[:20]
    right = props.get("right", "AFTER").upper()[:20]
    fnt = _font(36, bold=True)
    # Vertical divider line
    draw.line([WIDTH / 2, 0, WIDTH / 2, HEIGHT], fill=(*_pri(), 180), width=3)
    # Labels at bottom
    draw.text((WIDTH / 4, HEIGHT - 80), left, font=fnt, fill=(*_pri(), 200), anchor="mm")
    draw.text((3 * WIDTH / 4, HEIGHT - 80), right, font=fnt, fill=(*_pri(), 200), anchor="mm")
    return img


def _overlay_countdown_clock(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    value = props.get("value", "00:40")
    label = props.get("label", "").upper()[:58]
    fnt_val = _font(120, bold=True)
    fnt_label = _font(30, bold=True)
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(*_bg(), 190))
    draw.text((WIDTH / 2, HEIGHT / 2 - 40), value, font=fnt_val, fill=_dng(), anchor="mt")
    draw.text((WIDTH / 2, HEIGHT / 2 + 70), label, font=fnt_label, fill=_pri(), anchor="mt")
    return img


def _overlay_archival_frame(props: dict) -> Image.Image:
    """Subtle archival border overlay."""
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    label = props.get("label", "ARCHIVE").upper()[:48]
    fnt = _font(18, bold=False)
    draw.rectangle([20, 20, WIDTH - 20, HEIGHT - 20], outline=(*_pri(), 60), width=2)
    draw.text((30, HEIGHT - 50), label, font=fnt, fill=(*_pri(), 80), anchor="lt")
    return img


def _overlay_data_race(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    left_label = props.get("left", "TIME").upper()[:28]
    right_label = props.get("right", "DISTANCE").upper()[:28]
    value = props.get("value", "02:40")
    label = props.get("label", "THE RACE").upper()[:54]
    fnt_lr = _font(32, bold=True)
    fnt_val = _font(100, bold=True)
    fnt_label = _font(26, bold=True)
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(*_bg(), 190))
    draw.text((WIDTH / 2, 100), label, font=fnt_label, fill=_acc(), anchor="mt")
    draw.text((WIDTH / 4, HEIGHT / 2 + 60), left_label, font=fnt_lr, fill=_sec(), anchor="mm")
    draw.text((3 * WIDTH / 4, HEIGHT / 2 + 60), right_label, font=fnt_lr, fill=_acc(), anchor="mm")
    draw.line([WIDTH / 2, 140, WIDTH / 2, HEIGHT - 100], fill=(*_pri(), 60), width=1)
    return img


def _overlay_blueprint_reveal(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    title = props.get("title", "").upper()[:62]
    detail = props.get("detail", "")[:150]
    fnt_title = _font(48, bold=True)
    fnt_detail = _font(26, bold=False)
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(*_bg(), 180))
    # Grid lines (blueprint style)
    for x in range(0, WIDTH, 80):
        draw.line([x, 0, x, HEIGHT], fill=(*_sec(), 30), width=1)
    for y in range(0, HEIGHT, 80):
        draw.line([0, y, WIDTH, y], fill=(*_sec(), 30), width=1)
    draw.text((WIDTH / 2, 120), title, font=fnt_title, fill=_sec(), anchor="mt")
    if detail:
        detail = _wrap_to_fit(detail, fnt_detail, WIDTH - 300)
        draw.text((WIDTH / 2, HEIGHT / 2), detail, font=fnt_detail, fill=(*_sec(), 200), anchor="mt")
    return img


def _overlay_signal_scan(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    message = props.get("message", "").upper()[:72]
    label = props.get("label", "WIRELESS LOG")[:36]
    detail = props.get("detail", "")[:140]
    fnt_label = _font(22, bold=True)
    fnt_msg = _font(48, bold=True)
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(*_bg(), 200))
    draw.text((WIDTH / 2, 120), label, font=fnt_label, fill=_dng(), anchor="mt")
    draw.text((WIDTH / 2, HEIGHT / 2), message, font=fnt_msg, fill=_pri(), anchor="mt")
    return img


def _overlay_bar_chart(props: dict) -> Image.Image:
    """Render a simple bar chart. Items: [{"label": str, "value": int}]"""
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    items = props.get("items", [])
    label = props.get("label", "COMPARISON").upper()[:40]
    if not items:
        return img
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(*_bg(), 190))
    draw.text((WIDTH / 2, 80), label, font=_font(32, bold=True), fill=_acc(), anchor="mt")
    max_val = max(item["value"] for item in items) or 1
    chart_w = 800
    chart_h = 400
    chart_x = (WIDTH - chart_w) / 2
    chart_y = 220
    bar_w = chart_w / len(items) * 0.6
    gap = chart_w / len(items) * 0.4
    for i, item in enumerate(items):
        v = item["value"]
        h = (v / max_val) * chart_h
        x = chart_x + i * (bar_w + gap) + gap / 2
        draw.rectangle([x, chart_y + chart_h - h, x + bar_w, chart_y + chart_h], fill=(*_acc(), 220))
        # Label
        lbl = str(item.get("label", ""))[:10]
        draw.text((x + bar_w / 2, chart_y + chart_h + 20), lbl, font=_font(22, bold=False), fill=(*_pri(), 180), anchor="mt")
    return img


def _overlay_line_chart(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    points = props.get("points", [])
    label = props.get("label", "TREND").upper()[:40]
    if not points:
        return img
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(*_bg(), 190))
    draw.text((WIDTH / 2, 80), label, font=_font(32, bold=True), fill=_acc(), anchor="mt")
    chart_w = 800
    chart_h = 400
    chart_x = (WIDTH - chart_w) / 2
    chart_y = 220
    max_val = max(points) or 1
    min_val = min(points)
    rng = max_val - min_val or 1
    # Grid lines
    for i in range(5):
        gy = chart_y + chart_h * i / 4
        draw.line([chart_x, gy, chart_x + chart_w, gy], fill=(*_pri(), 20), width=1)
    # Line segments
    step_x = chart_w / (len(points) - 1) if len(points) > 1 else chart_w
    coords = []
    for i, p in enumerate(points):
        px = chart_x + i * step_x
        py = chart_y + chart_h - ((p - min_val) / rng) * chart_h
        coords.append((px, py))
    for i in range(len(coords) - 1):
        draw.line([coords[i], coords[i + 1]], fill=(*_acc(), 255), width=4)
    # Dots
    for cx, cy in coords:
        draw.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], fill=(*_acc(), 255))
    return img


def _overlay_donut_chart(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    value = min(100, float(props.get("value", 50))) / 100.0
    label = props.get("label", "OF THE TOTAL").upper()[:54]
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(*_bg(), 190))
    draw.text((WIDTH / 2, 100), label, font=_font(28, bold=True), fill=_acc(), anchor="mt")
    # Donut
    cx, cy = WIDTH / 2, HEIGHT / 2 - 20
    r_outer, r_inner = 200, 120
    # Background circle
    draw.ellipse([cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer], fill=(*_surf(), 255))
    # Foreground arc (simplified - filled wedge approximation)
    draw.pieslice([cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer],
                  270 - int(360 * value), 270, fill=(*_acc(), 255))
    # Inner circle (cutout)
    draw.ellipse([cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner], fill=(*_bg(), 200))
    # Percentage text
    pct = int(value * 100)
    draw.text((cx, cy), f"{pct}%", font=_font(48, bold=True), fill=_pri(), anchor="mm")
    return img


# ---------------------------------------------------------------------------
# Additional missing template generators
# ---------------------------------------------------------------------------

def _overlay_newsprint_reveal(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    headline = props.get("headline", "").upper()[:88]
    deck = props.get("deck", "")[:120]
    label = props.get("label", "ARCHIVE REPORT").upper()[:30]
    fnt_headline = _font(64, bold=True)
    fnt_deck = _font(28, bold=False)
    fnt_label = _font(20, bold=True)
    draw.rectangle([120, 100, WIDTH - 120, HEIGHT - 100], fill=(*_pri(), 230))
    draw.text((WIDTH / 2, 160), label, font=fnt_label, fill=_dng(), anchor="mt")
    draw.line([WIDTH / 2 - 200, 190, WIDTH / 2 + 200, 190], fill=(*_bg(), 80), width=1)
    headline = _wrap_to_fit(headline, fnt_headline, WIDTH - 320)
    draw.text((WIDTH / 2, HEIGHT / 2 - 40), headline, font=fnt_headline, fill=_bg(), anchor="mt")
    if deck:
        deck = _wrap_to_fit(deck, fnt_deck, WIDTH - 320)
        draw.text((WIDTH / 2, HEIGHT / 2 + 60), deck, font=fnt_deck, fill=(*_bg(), 180), anchor="mt")
    return img


def _overlay_timeline_trace(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    events = props.get("events", [])[:5]
    label = props.get("label", "TIMELINE").upper()[:42]
    fnt_label = _font(28, bold=True)
    fnt_event = _font(28, bold=False)
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(*_bg(), 190))
    draw.text((WIDTH / 2, 80), label, font=fnt_label, fill=_acc(), anchor="mt")
    draw.line([WIDTH / 2, 160, WIDTH / 2, HEIGHT - 100], fill=(*_acc(), 150), width=3)
    for i, evt in enumerate(events):
        y = 200 + i * 140
        evt_label = str(evt.get("label", "EVENT") if isinstance(evt, dict) else evt).upper()[:40]
        evt_year = str(evt.get("year", "") if isinstance(evt, dict) else "")[:12]
        draw.ellipse([WIDTH / 2 - 10, y - 10, WIDTH / 2 + 10, y + 10], fill=(*_acc(), 255))
        draw.text((WIDTH / 2 + 30, y), evt_label, font=fnt_event, fill=_pri(), anchor="lm")
        if evt_year:
            draw.text((WIDTH / 2 - 40, y), evt_year, font=_font(18, bold=False), fill=(*_pri(), 120), anchor="rm")
    return img


def _overlay_map_route(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    label = str(props.get("label") or props.get("location") or "").strip().upper()[:40]
    if not label:
        return img
    from_label = props.get("from", "").upper()[:32]
    to_label = props.get("to", "").upper()[:32]
    fnt_label = _font(28, bold=True)
    fnt_place = _font(22, bold=False)
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(*_bg(), 180))
    draw.text((WIDTH / 2, 100), label, font=fnt_label, fill=_acc(), anchor="mt")
    # Simplified route line
    cx, cy = WIDTH / 2, HEIGHT / 2
    draw.ellipse([cx - 180, cy - 60, cx - 60, cy + 60], fill=(*_surf(), 200), outline=(*_acc(), 120), width=2)
    draw.ellipse([cx + 60, cy - 60, cx + 180, cy + 60], fill=(*_surf(), 200), outline=(*_acc(), 120), width=2)
    draw.line([cx - 60, cy, cx + 60, cy], fill=(*_acc(), 200), width=3)
    draw.text((cx - 120, cy), "\u25cf", font=_font(36, bold=True), fill=_acc(), anchor="mm")
    draw.text((cx + 120, cy), "\u25c9", font=_font(36, bold=True), fill=_dng(), anchor="mm")
    draw.text((cx - 120, cy + 60), from_label or "START", font=fnt_place, fill=(*_pri(), 160), anchor="mt")
    draw.text((cx + 120, cy + 60), to_label or "END", font=fnt_place, fill=(*_pri(), 160), anchor="mt")
    return img


def _overlay_gallery_stack(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    title = props.get("title", "GALLERY").upper()[:54]
    detail = props.get("detail", "")[:80]
    fnt_title = _font(36, bold=True)
    fnt_detail = _font(22, bold=False)
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(*_bg(), 160))
    draw.text((WIDTH / 2, 100), title, font=fnt_title, fill=_pri(), anchor="mt")
    if detail:
        draw.text((WIDTH / 2, HEIGHT - 100), detail, font=fnt_detail, fill=(*_pri(), 160), anchor="mt")
    return img


def _overlay_callout_arrow(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    label = props.get("label", "LOOK HERE").upper()[:40]
    bbox = props.get("bbox", [0.4, 0.3, 0.6, 0.5])
    fnt = _font(28, bold=True)
    # Arrow from bottom-right toward bbox center
    bx = bbox[0] * WIDTH if len(bbox) > 0 else WIDTH * 0.4
    by = bbox[1] * HEIGHT if len(bbox) > 1 else HEIGHT * 0.3
    ax, ay = WIDTH - 80, HEIGHT - 80
    draw.line([ax, ay, bx, by], fill=(*_dng(), 220), width=3)
    # Arrowhead
    draw.ellipse([bx - 8, by - 8, bx + 8, by + 8], fill=(*_dng(), 255))
    draw.text((ax - 10, ay - 40), label, font=fnt, fill=_dng(), anchor="mm")
    return img


def _overlay_pictogram_stat(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    value = max(1, min(10, int(props.get("value", 5))))
    total = max(1, int(props.get("total", 10)))
    label = props.get("label", "").upper()[:52]
    fnt_label = _font(30, bold=True)
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(*_bg(), 190))
    draw.text((WIDTH / 2, HEIGHT / 2 + 80), label, font=fnt_label, fill=_pri(), anchor="mt")
    # Simple dot pictogram
    dot_size = 30
    spacing = 50
    start_x = (WIDTH - (total * spacing)) / 2 + dot_size / 2
    for i in range(total):
        x = start_x + i * spacing
        filled = i < value
        draw.ellipse([x - dot_size / 2, HEIGHT / 2 - dot_size / 2 - 30,
                      x + dot_size / 2, HEIGHT / 2 + dot_size / 2 - 30],
                     fill=(*_acc(), 255 if filled else 60))
    return img


def _overlay_spotlight_lens(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    title = props.get("title", "").upper()[:74]
    detail = props.get("detail", "")[:100]
    fnt_title = _font(48, bold=True)
    fnt_detail = _font(26, bold=False)
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(*_bg(), 170))
    # Circular mask area (dark vignette that leaves center bright)
    draw.ellipse([WIDTH / 2 - 300, HEIGHT / 2 - 200, WIDTH / 2 + 300, HEIGHT / 2 + 200],
                 fill=(*_bg(), 0))
    title = _wrap_to_fit(title, fnt_title, WIDTH - 400)
    draw.text((WIDTH / 2, HEIGHT / 2 - 40), title, font=fnt_title, fill=_pri(), anchor="mt")
    if detail:
        draw.text((WIDTH / 2, HEIGHT / 2 + 60), detail, font=fnt_detail, fill=(*_pri(), 180), anchor="mt")
    return img


def _overlay_metric_grid(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    value = props.get("value", "0")
    label = props.get("label", "").upper()[:55]
    detail = props.get("detail", "")[:80]
    fnt_val = _font(100, bold=True)
    fnt_label = _font(28, bold=True)
    fnt_detail = _font(20, bold=False)
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(*_bg(), 190))
    draw.text((WIDTH / 2, HEIGHT / 2 - 40), str(value), font=fnt_val, fill=_acc(), anchor="mt")
    draw.text((WIDTH / 2, HEIGHT / 2 + 60), label, font=fnt_label, fill=_pri(), anchor="mt")
    if detail:
        draw.text((WIDTH / 2, HEIGHT / 2 + 110), detail, font=fnt_detail, fill=(*_pri(), 140), anchor="mt")
    return img


def _overlay_stamp_mark(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    title = props.get("title", "VERIFIED").upper()[:52]
    subtitle = props.get("subtitle", "ARCHIVE RECORD").upper()[:44]
    detail = props.get("detail", "")[:80]
    fnt_title = _font(40, bold=True)
    fnt_sub = _font(22, bold=False)
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=(*_bg(), 180))
    draw.text((WIDTH / 2, HEIGHT / 2 - 30), title, font=fnt_title, fill=_dng(), anchor="mt")
    draw.text((WIDTH / 2, HEIGHT / 2 + 30), subtitle, font=fnt_sub, fill=(*_pri(), 180), anchor="mt")
    # Stamp border circle
    draw.ellipse([WIDTH / 2 - 260, HEIGHT / 2 - 260, WIDTH / 2 + 260, HEIGHT / 2 + 260],
                 outline=(*_dng(), 100), width=3)
    return img


def _overlay_index_card(props: dict) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    title = props.get("title", "ARCHIVE ENTRY").upper()[:62]
    detail = props.get("detail", "")[:120]
    label = props.get("label", "CATALOGUE NOTE")[:36]
    fnt_label = _font(20, bold=True)
    fnt_title = _font(40, bold=True)
    fnt_detail = _font(24, bold=False)
    draw.rectangle([100, 120, WIDTH - 100, HEIGHT - 120], fill=(*_pri(), 230))
    draw.text((WIDTH / 2, 160), label, font=fnt_label, fill=_bg(), anchor="mt")
    draw.line([WIDTH / 2 - 180, 190, WIDTH / 2 + 180, 190], fill=(*_bg(), 60), width=1)
    title = _wrap_to_fit(title, fnt_title, WIDTH - 320)
    draw.text((WIDTH / 2, HEIGHT / 2 - 40), title, font=fnt_title, fill=_bg(), anchor="mt")
    if detail:
        draw.text((WIDTH / 2, HEIGHT / 2 + 60), detail, font=fnt_detail, fill=(*_bg(), 160), anchor="mt")
    return img


def _overlay_editorial_contract(props: dict) -> Image.Image:
    """Pillow fallback matching the native one-heading card contract."""
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    text = str(props.get("_contract_text") or props.get("text") or props.get("title") or "").strip().upper()[:48]
    if not text:
        return img
    anchor = guide_anchor(props.get("_position"), default="center")
    anchor_x = WIDTH * anchor["x"]
    anchor_y = HEIGHT * anchor["y"]
    size = max(48, min(150, int(props.get("_contract_size") or 84)))
    font_path = props.get("_font_path") or props.get("font_path")
    if not font_path:
        font_choices = [FONT_POPPINS, FONT_OPEN_SANS]
        font_path = font_choices[int(abs(hash(text))) % len(font_choices)]
    try:
        font = ImageFont.truetype(font_path, size)
    except Exception:
        font = _font(size, bold=True)
    text = _wrap_to_fit(text, font, 980)
    bbox = draw.textbbox((0, 0), text, font=font, anchor="lt")
    text_w = max(1, bbox[2] - bbox[0])
    text_h = max(1, bbox[3] - bbox[1])
    horizontal_pad = max(24, int(size * 0.32))
    vertical_pad = max(12, int(size * 0.16))
    card_w = min(1100, text_w + horizontal_pad * 2)
    card_h = min(240, max(52, text_h + vertical_pad * 2))
    if anchor["horizontal"] == "right":
        card_x = anchor_x - card_w
    elif anchor["horizontal"] == "center":
        card_x = anchor_x - card_w / 2
    else:
        card_x = anchor_x
    card_y = anchor_y - card_h / 2
    card_x = max(16, min(WIDTH - card_w - 16, card_x))
    card_y = max(16, min(HEIGHT - card_h - 16, card_y))
    glyph_x = int(card_x + horizontal_pad)
    glyph_y = int(card_y + vertical_pad)
    mode = str(props.get("_card_mode") or "surface").lower()
    if mode in {"none", "transparent", "bare"}:
        # Bare cards deliberately leave the verified footage visible.  Keep a
        # shadow behind the glyphs so white text remains readable over bright
        # frames without painting an opaque panel across the subject.
        card_color = None
    elif mode == "surface":
        card_color = (14, 20, 30, 235)
    elif mode == "accent":
        card_color = (*_acc(), 245)
    elif mode == "danger":
        card_color = (*_dng(), 245)
    elif mode == "frosted":
        card_color = (20, 26, 36, 210)
    else:
        card_color = (12, 16, 24, 220)
    if card_color:
        rect_bounds = [
            card_x,
            card_y,
            card_x + card_w,
            card_y + card_h,
        ]
        # Draw soft shadow behind card
        draw.rounded_rectangle([rect_bounds[0]+4, rect_bounds[1]+4, rect_bounds[2]+4, rect_bounds[3]+4], radius=14, fill=(0, 0, 0, 140))
        # Draw main pill card with subtle outline
        draw.rounded_rectangle(rect_bounds, radius=14, fill=card_color, outline=(*_acc(), 180), width=2)
        luma = 0.2126 * card_color[0] + 0.7152 * card_color[1] + 0.0722 * card_color[2]
        ink = (12, 16, 20, 255) if luma > 155 else (*_pri(), 255)
    else:
        ink = (*_pri(), 255)
    micro = str(props.get("_micro_label") or "").strip().upper()[:16]
    if micro:
        micro_anchor = guide_anchor("top_left")
        draw.text(
            (int(WIDTH * micro_anchor["x"]), int(HEIGHT * micro_anchor["y"])),
            micro,
            font=_font(18, bold=True),
            fill=(*_acc(), 255),
            anchor="lm",
        )
    # Text with slight drop shadow
    draw.text((glyph_x + 2, glyph_y + 2), text, font=font, fill=(0, 0, 0, 160), anchor="lt")
    draw.text((glyph_x, glyph_y), text, font=font, fill=ink, anchor="lt")
    return img


# Registry: template_id -> overlay generator function
def _overlay_text_card(props: dict) -> Image.Image:
    """Broadcast-grade text card overlay with keyword highlight styling."""
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    raw_text = str(props.get("text") or props.get("title") or props.get("headline") or "").strip()
    if not raw_text:
        return img
    kicker = str(props.get("kicker") or props.get("label") or "OFFICIAL RECORD").strip().upper()[:40]
    subtitle = str(props.get("subtitle") or props.get("detail") or "").strip()[:80]
    badge = str(props.get("badge") or "").strip().upper()[:20]
    style = str(props.get("style") or "vox-highlighter").lower()
    position = str(props.get("position") or "bottom-left").lower()
    highlight_words = [str(w).strip().lower() for w in (props.get("highlight_words") or []) if str(w).strip()]

    fnt_kicker = _font(22, bold=True)
    fnt_text = _font(44, bold=True)
    fnt_sub = _font(22, bold=False)
    fnt_badge = _font(16, bold=True)

    # Multi-line word wrap
    import textwrap
    clean_display = re.sub(r"[*_`]", "", raw_text)
    lines = textwrap.wrap(clean_display, width=32) or [clean_display]
    line_h = 56
    text_w = max(_text_width(line, fnt_text) for line in lines)
    text_h = len(lines) * line_h

    pad_h = 44
    pad_v = 32
    card_w = min(WIDTH - 120, max(460, int(text_w + pad_h * 2)))
    card_h = text_h + pad_v * 2 + (38 if (kicker or badge) else 0) + (36 if subtitle else 0)

    if "top" in position and "right" in position:
        card_x = WIDTH - card_w - 70
        card_y = 65
    elif "top" in position:
        card_x = 70
        card_y = 65
    elif "right" in position:
        card_x = WIDTH - card_w - 70
        card_y = HEIGHT - card_h - 75
    elif "center" in position:
        card_x = (WIDTH - card_w) / 2
        card_y = (HEIGHT - card_h) / 2
    else:
        card_x = 70
        card_y = HEIGHT - card_h - 75

    # Card background with drop shadow
    draw.rounded_rectangle([card_x + 6, card_y + 8, card_x + card_w + 6, card_y + card_h + 8], radius=14, fill=(0, 0, 0, 140))
    card_bg = (12, 16, 24, 238) if "memo" not in style else (244, 237, 225, 245)
    draw.rounded_rectangle([card_x, card_y, card_x + card_w, card_y + card_h], radius=14, fill=card_bg)
    accent_col = (*_acc(), 255)
    border_col = (255, 255, 255, 35) if "memo" not in style else (130, 110, 80, 80)
    draw.rounded_rectangle([card_x, card_y, card_x + card_w, card_y + card_h], radius=14, outline=border_col, width=1)
    draw.rounded_rectangle([card_x, card_y, card_x + 8, card_y + card_h], radius=3, fill=accent_col)

    cur_y = card_y + pad_v
    cur_x = card_x + pad_h

    # Kicker & Badge
    if kicker or badge:
        if badge:
            badge_w = int(_text_width(badge, fnt_badge) + 16)
            draw.rounded_rectangle([cur_x, cur_y - 2, cur_x + badge_w, cur_y + 22], radius=4, fill=accent_col)
            draw.text((cur_x + 8, cur_y), badge, font=fnt_badge, fill=(10, 14, 20, 255))
            cur_x += badge_w + 14
        if kicker:
            draw.text((cur_x, cur_y), f"● {kicker}", font=fnt_kicker, fill=accent_col)
        cur_y += 38
        cur_x = card_x + pad_h

    # Build highlight token set (only keywords >= 2 chars, or digits, or explicit currency/percent)
    hl_tokens = set()
    for hw in highlight_words:
        for token in re.findall(r"[\$\w%]+", hw.lower()):
            clean_t = re.sub(r"^[^\w\$%]+|[^\w\$%]+$", "", token)
            if len(clean_t) > 1 or clean_t.isdigit():
                hl_tokens.add(clean_t)

    # Render wrapped lines with keyword highlight pills
    text_color = _pri() if "memo" not in style else (18, 14, 10, 255)
    for line in lines:
        line_cur_x = cur_x
        words_in_line = [w for w in line.split(" ") if w]
        for w_idx, w in enumerate(words_in_line):
            clean_w = re.sub(r"^[^\w\$%]+|[^\w\$%]+$", "", w.lower())
            is_hl = clean_w in hl_tokens or bool(re.search(r"\$|\d+%", w))
            w_str = w + (" " if w_idx < len(words_in_line) - 1 else "")
            ww = _text_width(w_str, fnt_text)
            if is_hl:
                draw.rounded_rectangle([line_cur_x - 3, cur_y + 2, line_cur_x + ww - 2, cur_y + line_h - 12], radius=6, fill=accent_col)
                draw.text((line_cur_x, cur_y), w_str, font=fnt_text, fill=(10, 14, 20, 255))
            else:
                draw.text((line_cur_x, cur_y), w_str, font=fnt_text, fill=text_color)
            line_cur_x += ww
        cur_y += line_h

    # Subtitle
    if subtitle:
        cur_y += 6
        sub_color = (*_pri(), 185) if "memo" not in style else (70, 60, 50, 200)
        draw.text((cur_x, cur_y), subtitle, font=fnt_sub, fill=sub_color)

    return img


_OVERLAY_REGISTRY: Dict[str, callable] = {
    "text-card": _overlay_text_card,
    "vox-highlighter": _overlay_text_card,
    "cyber-hud": _overlay_text_card,
    "investigative-memo": _overlay_text_card,
    "broadsheet-callout": _overlay_text_card,
    "frosted-glass": _overlay_text_card,
    "brutalist-punch": _overlay_text_card,
    "chapter-opener": _overlay_chapter_opener,
    "keyword-reveal": _overlay_keyword_reveal,
    "date-card": _overlay_date_card,
    "lower-third": _overlay_lower_third,
    "evidence-callout": _overlay_evidence_callout,
    "quote-card": _overlay_quote_card,
    "stat-counter": _overlay_stat_counter,
    "money-counter": _overlay_money_counter,
    "kinetic-statement": _overlay_kinetic_statement,
    "cold-open": _overlay_cold_open,
    "paper-explainer": _overlay_paper_explainer,
    "profile-dossier": _overlay_profile_dossier,
    "evidence-board": _overlay_evidence_board,
    "field-caption": _overlay_field_caption,
    "exhibit-label": _overlay_exhibit_label,
    "end-card": _overlay_end_card,
    "listicle-build": _overlay_listicle_build,
    "witness-quote": _overlay_witness_quote,
    "source-highlight": _overlay_source_highlight,
    "word-cascade": _overlay_word_cascade,
    "redaction-reveal": _overlay_redaction_reveal,
    "cause-effect": _overlay_cause_effect,
    "split-screen": _overlay_split_screen,
    "countdown-clock": _overlay_countdown_clock,
    "archival-frame": _overlay_archival_frame,
    "data-race": _overlay_data_race,
    "blueprint-reveal": _overlay_blueprint_reveal,
    "signal-scan": _overlay_signal_scan,
    "bar-chart": _overlay_bar_chart,
    "line-chart": _overlay_line_chart,
    "donut-chart": _overlay_donut_chart,
    "decision-tree": _overlay_decision_tree,
    "newsprint-reveal": _overlay_newsprint_reveal,
    "timeline-trace": _overlay_timeline_trace,
    "map-route": _overlay_map_route,
    "gallery-stack": _overlay_gallery_stack,
    "callout-arrow": _overlay_callout_arrow,
    "pictogram-stat": _overlay_pictogram_stat,
    "spotlight-lens": _overlay_spotlight_lens,
    "metric-grid": _overlay_metric_grid,
    "stamp-mark": _overlay_stamp_mark,
    "index-card": _overlay_index_card,
}

# The semantic director uses these two spatial template names while the
# raster graphics catalog calls the matching composition ``map-route``.  Do
# not treat that naming difference as a no-op: an unrendered locator is worse
# than a deliberately simple, readable map card.
_RASTER_TEMPLATE_ALIASES = {
    "map-card": "map-route",
    "route-progress": "map-route",
}


def _raster_template_id(template_id: str) -> str:
    """Return the supported Pillow template for a semantic template id."""
    normalized = str(template_id or "").strip().lower()
    return _RASTER_TEMPLATE_ALIASES.get(normalized, normalized)


def generate_overlay(template_id: str, props: dict) -> Optional[str]:
    """Generate a transparent PNG overlay for the given template + props.
    Returns path to PNG or None if no overlay needed.
    """
    template_id = _raster_template_id(template_id)
    if template_id in ("full-bleed", "none", "") or bool((props or {}).get("no_data")):
        return None
    if template_id in _OVERLAY_REGISTRY:
        generator = _OVERLAY_REGISTRY.get(template_id)
    elif props.get("_editorial_contract"):
        return _overlay_editorial_contract(props)
    else:
        return None
    if not generator:
        return None
    try:
        img = generator(props)
        if img is None:
            return None
        fd, path = tempfile.mkstemp(suffix=".png", prefix=f"ovl_{template_id}_")
        os.close(fd)
        img.save(path, format="PNG")
        return path
    except Exception as e:
        print(f"   [overlay] {template_id} generation failed: {e}")
        return None


def generate_overlay_to_file(template_id: str, props: dict, output_path: str) -> Optional[str]:
    """Generate overlay PNG and save to file. Returns path or None."""
    template_id = _raster_template_id(template_id)
    if template_id in ("full-bleed", "none", "") or bool((props or {}).get("no_data")):
        return None
    if template_id in _OVERLAY_REGISTRY:
        generator = _OVERLAY_REGISTRY.get(template_id)
    elif props.get("_editorial_contract"):
        generator = _overlay_editorial_contract
    else:
        return None
    if not generator:
        return None
    try:
        _TLS.current_template_id = template_id
        img = generator(props)
        _TLS.current_template_id = ""
        if img is None:
            return None
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        img.save(output_path, format="PNG")
        return output_path if Path(output_path).exists() and Path(output_path).stat().st_size > 0 else None
    except Exception as e:
        print(f"   [overlay] {template_id} generation failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Motion templates (simplified zoompan)
# ---------------------------------------------------------------------------

def _zexpr_valley(base, peak, on, frames):
    return f"{base}+({peak}-{base})*sin(PI*on/{frames})"


def _zexpr_rise(base, peak, on, frames):
    return f"{base}+({peak}-{base})*(1-cos(PI*on/{frames}))/2"


def _zexpr_fall(base, peak, on, frames):
    return f"{peak}-({peak}-{base})*(1-cos(PI*on/{frames}))/2"


def _bounded_pan_expr(curve: str, span: float = 0.22) -> str:
    """Keep camera pans near the focal crop instead of sweeping edge-to-edge.

    ``zoompan`` expresses x/y as a 0..1 position inside the available crop.
    The old templates used the full 0..1 range, which could move a face or
    other verified subject out of frame after the initial focal crop.  A
    bounded curve keeps the authored movement while preserving the subject
    safety margin.
    """
    bounded_span = max(0.05, min(0.35, float(span)))
    return f"clip(0.5+{bounded_span:.3f}*(({curve})*2-1),0,1)"


def _multi_phase_still_motion(cover: str, motion: str, frames: int, w: int, h: int, sw: int = 2560, sh: int = 1440) -> Optional[str]:
    """Return a three-phase camera move for a long-held still.

    A single slow zoom is technically animated but still reads as one frozen
    image during a long narration beat. This keeps the same verified pixels
    while changing the camera language at two deliberate points: establish,
    move across the subject, then settle on a new crop. It is used only for
    stills, never for downloaded video clips.
    """
    if not cover or frames < 12:
        return None
    first = max(4, frames // 3)
    second = max(first + 1, min(frames - 4, first * 2))
    final = max(4, frames - second)

    def phase_rise(base: float, peak: float, start: int, length: int) -> str:
        return _zexpr_rise(base, peak, f"on-{start}" if start else "on", length)

    def phase_fall(base: float, peak: float, start: int, length: int) -> str:
        return _zexpr_fall(base, peak, f"on-{start}" if start else "on", length)

    def phase_move(start_value: float, end_value: float, start: int, length: int) -> str:
        if end_value >= start_value:
            return phase_rise(start_value, end_value, start, length)
        return phase_fall(end_value, start_value, start, length)

    def three_phase(a: str, b: str, c: str) -> str:
        return f"if(lt(on\\,{first})\\,{a}\\,if(lt(on\\,{second})\\,{b}\\,{c}))"

    normalized = str(motion or "").strip().lower()
    if normalized in {"pan_left", "pan_right", "pan_diagonal_bl", "pan_diagonal_tr"}:
        x_forward = normalized in {"pan_right", "pan_diagonal_tr"}
        y_forward = normalized in {"pan_diagonal_bl", "pan_diagonal_tr"}
        x_a = phase_move(0.0, 1.0, 0, first) if x_forward else phase_move(1.0, 0.0, 0, first)
        x_b = phase_move(1.0, 0.0, first, first) if x_forward else phase_move(0.0, 1.0, first, first)
        x_c = phase_move(0.0, 0.72, second, final) if x_forward else phase_move(0.72, 0.0, second, final)
        y_a = phase_move(0.0, 1.0, 0, first) if y_forward else phase_move(1.0, 0.0, 0, first)
        y_b = phase_move(1.0, 0.0, first, first) if y_forward else phase_move(0.0, 1.0, first, first)
        y_c = phase_move(0.0, 0.72, second, final) if y_forward else phase_move(0.72, 0.0, second, final)
        x = _bounded_pan_expr(three_phase(x_a, x_b, x_c))
        y = _bounded_pan_expr(three_phase(y_a, y_b, y_c)) if normalized.startswith("pan_diagonal") else "0.5"
        return (
            f"{cover},zoompan=z=1.1:x='(iw-iw/1.1)*{x}':"
            f"y='(ih-ih/1.1)*{y}':d={frames}:s={sw}x{sh}:fps={FPS},"
            f"scale={w}:{h}:flags=lanczos+accurate_rnd"
        )

    # Zoom-based moves use a gentle push, a controlled pull, and a final
    # tighter settle. The subject remains centered in the verified crop.
    z_a = phase_rise(1.0, 1.07, 0, first)
    z_b = phase_fall(1.07, 1.015, first, first)
    z_c = phase_rise(1.015, 1.11, second, final)
    z = three_phase(z_a, z_b, z_c)
    return (
        f"{cover},zoompan=z='{z}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':"
        f"d={frames}:s={sw}x{sh}:fps={FPS},"
        f"scale={w}:{h}:flags=lanczos+accurate_rnd"
    )


def _image_source_aspect(source: str) -> Optional[float]:
    """Read a still's aspect ratio without making rendering depend on it."""
    try:
        with Image.open(str(source)) as image:
            width, height = image.size
        if width > 0 and height > 0:
            return float(width) / float(height)
    except Exception:
        return None
    return None


def _media_source_aspect(source: str, *, is_video: bool = False) -> Optional[float]:
    """Return the source aspect for both stills and clips."""
    if not source:
        return None
    if not is_video:
        return _image_source_aspect(source)
    width, height = _probe_media_dimensions(str(source))
    if width > 0 and height > 0:
        return float(width) / float(height)
    return None


def _motion_filter(
    motion: str,
    duration: float,
    w: int,
    h: int,
    sw: int = 2560,
    sh: int = 1440,
    *,
    source: str = "",
    focal_bbox: Optional[Sequence[float]] = None,
) -> Optional[str]:
    """Return a zoompan+scale filter for the given motion type."""
    frames = int(duration * FPS)
    if frames < 1:
        frames = 1
    aspect = _image_source_aspect(source) if source else None
    target_aspect = float(w) / float(h) if h > 0 else 16.0 / 9.0
    fx = 0.5
    fy = 0.25 if (aspect and aspect < (target_aspect * 0.85)) else 0.5
    if isinstance(focal_bbox, (list, tuple)) and len(focal_bbox) == 4:
        try:
            x1, y1, x2, y2 = (float(part) for part in focal_bbox)
            if 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1:
                fx = max(0.0, min(1.0, (x1 + x2) / 2.0))
                fy = max(0.0, min(1.0, (y1 + y2) / 2.0))
        except (TypeError, ValueError):
            pass

    # Universal full-bleed cover crop anchored to focal point / subject
    cover = (
        f"scale={sw}:{sh}:force_original_aspect_ratio=increase,"
        f"crop={sw}:{sh}:x='clip(iw*{fx:.5f}-ow/2,0,iw-ow)':"
        f"y='clip(ih*{fy:.5f}-oh/2,0,ih-oh)'"
    )
    cover_fit = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos+accurate_rnd,"
        f"crop={w}:{h}:x='clip(iw*{fx:.5f}-ow/2,0,iw-ow)':"
        f"y='clip(ih*{fy:.5f}-oh/2,0,ih-oh)',setsar=1"
    )

    # Long stills need a camera beat change, not just a barely perceptible
    # full-duration zoom. Keep this threshold configurable for editorial
    # tuning, with a conservative default that leaves normal short scenes
    # untouched.
    try:
        long_still_seconds = max(
            4.0,
            min(30.0, float(os.environ.get("VIDZEO_LONG_STILL_MOTION_SECONDS", "8") or 8)),
        )
    except (TypeError, ValueError):
        long_still_seconds = 8.0
    if cover and float(duration or 0.0) >= long_still_seconds and motion not in {"static", ""}:
        multi_phase = _multi_phase_still_motion(cover, motion, frames, w, h, sw=sw, sh=sh)
        if multi_phase:
            return multi_phase

    if motion == "static":
        return cover_fit

    # Center/focal-point expressions for smooth camera lock
    z_x = f"(iw-iw/zoom)*{fx:.5f}"
    z_y = f"(ih-ih/zoom)*{fy:.5f}"
    smooth_scale = f"s={sw}x{sh}:fps={FPS},scale={w}:{h}:flags=lanczos+accurate_rnd"

    if motion == "zoom_in" and cover:
        z = _zexpr_rise(1.0, 1.08, 'on', frames)
        return f"{cover},zoompan=z='{z}':x='{z_x}':y='{z_y}':d={frames}:{smooth_scale}"
    elif motion == "zoom_out" and cover:
        z = _zexpr_fall(1.0, 1.08, 'on', frames)
        return f"{cover},zoompan=z='{z}':x='{z_x}':y='{z_y}':d={frames}:{smooth_scale}"
    elif motion == "zoom_fast" and cover:
        z = _zexpr_rise(1.0, 1.12, 'on', frames)
        return f"{cover},zoompan=z='{z}':x='{z_x}':y='{z_y}':d={frames}:{smooth_scale}"
    elif motion == "zoom_slow" and cover:
        z = _zexpr_rise(1.0, 1.05, 'on', frames)
        return f"{cover},zoompan=z='{z}':x='{z_x}':y='{z_y}':d={frames}:{smooth_scale}"
    elif motion == "zoom_in_slight" and cover:
        z = _zexpr_rise(1.0, 1.04, 'on', frames)
        return f"{cover},zoompan=z='{z}':x='{z_x}':y='{z_y}':d={frames}:{smooth_scale}"
    elif motion == "zoom_out_slight" and cover:
        z = _zexpr_fall(1.0, 1.04, 'on', frames)
        return f"{cover},zoompan=z='{z}':x='{z_x}':y='{z_y}':d={frames}:{smooth_scale}"

    elif motion == "pan_left" and cover:
        x = _bounded_pan_expr(_zexpr_fall(1, 0, 'on', frames))
        return f"{cover},zoompan=z=1.1:x='(iw-iw/1.1)*{x}':y='(ih-ih/1.1)/2':d={frames}:{smooth_scale}"
    elif motion == "pan_right" and cover:
        x = _bounded_pan_expr(_zexpr_rise(0, 1, 'on', frames))
        return f"{cover},zoompan=z=1.1:x='(iw-iw/1.1)*{x}':y='(ih-ih/1.1)/2':d={frames}:{smooth_scale}"
    elif motion == "pan_up" and cover:
        y = _bounded_pan_expr(_zexpr_fall(1, 0, 'on', frames))
        return f"{cover},zoompan=z=1.1:x='(iw-iw/1.1)/2':y='(ih-ih/1.1)*{y}':d={frames}:{smooth_scale}"
    elif motion == "pan_down" and cover:
        y = _bounded_pan_expr(_zexpr_rise(0, 1, 'on', frames))
        return f"{cover},zoompan=z=1.1:x='(iw-iw/1.1)/2':y='(ih-ih/1.1)*{y}':d={frames}:{smooth_scale}"
    elif motion == "pan_diagonal_bl" and cover:
        x = _bounded_pan_expr(_zexpr_fall(1, 0, 'on', frames))
        y = _bounded_pan_expr(_zexpr_rise(0, 1, 'on', frames))
        return f"{cover},zoompan=z=1.1:x='(iw-iw/1.1)*{x}':y='(ih-ih/1.1)*{y}':d={frames}:{smooth_scale}"
    elif motion == "pan_diagonal_tr" and cover:
        x = _bounded_pan_expr(_zexpr_rise(0, 1, 'on', frames))
        y = _bounded_pan_expr(_zexpr_fall(1, 0, 'on', frames))
        return f"{cover},zoompan=z=1.1:x='(iw-iw/1.1)*{x}':y='(ih-ih/1.1)*{y}':d={frames}:{smooth_scale}"

    elif motion == "rotate_left":
        angle = f"max(-0.06, -0.06 + (t/{duration:.3f})*0.06)"
        return f"{cover_fit},rotate=a='{angle}':fillcolor=black@0"
    elif motion == "rotate_right":
        angle = f"min(0.06, (t/{duration:.3f})*0.06)"
        return f"{cover_fit},rotate=a='{angle}':fillcolor=black@0"
    elif motion == "rotate_gentle":
        angle = "0.015*sin(t*1.2)"
        return f"{cover_fit},rotate=a='{angle}':fillcolor=black@0"

    elif motion == "settle" and cover:
        z = _zexpr_rise(1.08, 1.0, 'on', frames)
        return f"{cover},zoompan=z='{z}':x='{z_x}':y='{z_y}':d={frames}:{smooth_scale}"
    elif motion == "drift_left" and cover:
        x = _bounded_pan_expr(_zexpr_fall(1, 0, 'on', frames), 0.16)
        return f"{cover},zoompan=z=1.06:x='(iw-iw/1.06)*{x}':y='(ih-ih/1.06)/2':d={frames}:{smooth_scale}"
    elif motion == "drift_right" and cover:
        x = _bounded_pan_expr(_zexpr_rise(0, 1, 'on', frames), 0.16)
        return f"{cover},zoompan=z=1.06:x='(iw-iw/1.06)*{x}':y='(ih-ih/1.06)/2':d={frames}:{smooth_scale}"

    return cover_fit


def _blurred_fit_filter(w: int, h: int) -> str:
    """Fit media without black bars by compositing a blurred source backdrop."""
    # FFmpeg's boxblur chroma radius is constrained by the frame dimensions.
    # The production canvas can use the full 24px radius, but small preview
    # and regression frames (e.g. 160x90) reject that filter and produce a
    # blank scene. Keep the visual treatment while bounding the radius to a
    # dimension-safe value.
    blur_radius = max(1, min(24, min(int(w), int(h)) // 4))
    blur_power = max(1, min(12, blur_radius // 2))
    return (
        f"split=2[bg][fg];"
        f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"boxblur={blur_radius}:{blur_power},eq=contrast=0.92:saturation=0.78:brightness=-0.08[bgfit];"
        f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fgfit];"
        "[bgfit][fgfit]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )


def _safe_animated_fit_filter(
    motion: str,
    duration: float,
    w: int,
    h: int,
    *,
    force_blur: bool = False,
) -> str:
    """Contain a source and add bounded motion without cropping its subject.

    The foreground is kept inside a 96% canvas box, leaving a real safety
    margin for the overlay drift.  The source is never zoomed past that box;
    only the centered foreground position changes over time while the blurred
    source copy fills the canvas behind it.  This gives static web frames an
    authored camera beat without bringing back the old full-bleed crop.
    """
    normalized = str(motion or "").strip().lower()
    # Long-form fast mode keeps the authored camera movement but avoids the
    # second full-resolution branch and boxblur that made every still scene
    # disproportionately expensive.  A small overscan plus an animated crop
    # gives the same visual movement with one scale pass and no blurred
    # backdrop; the normal (non-fast) path below remains the premium fit.
    if (
        _ACTIVE_FAST_RENDER
        and not force_blur
        and str(os.environ.get("VIDZEO_FAST_IMAGE_BLUR", "0") or "0").strip().lower() not in {
            "1", "true", "yes", "on",
        }
    ):
        try:
            seconds = max(0.1, float(duration or 1.0))
        except (TypeError, ValueError):
            seconds = 1.0
        p = f"min(1,max(0,t/{seconds:.3f}))"
        ease = f"({p}*{p}*(3-2*{p}))"
        # Keep the overscan deliberately small so the verified subject stays
        # in frame while providing a noticeable drift for still imagery.
        scaled_w = max(w + 2, int(round(float(w) * 1.06)))
        scaled_h = max(h + 2, int(round(float(h) * 1.06)))
        start_x, end_x = 0.5, 0.5
        start_y, end_y = 0.5, 0.5
        if normalized in {"pan_left", "drift_left", "zoom_in", "zoom_in_slight", "zoom_fast", "zoom_slow"}:
            start_x, end_x = 0.62, 0.38
        elif normalized in {"pan_right", "drift_right", "zoom_out", "zoom_out_slight"}:
            start_x, end_x = 0.38, 0.62
        elif normalized == "pan_up":
            start_y, end_y = 0.62, 0.38
        elif normalized == "pan_down":
            start_y, end_y = 0.38, 0.62
        elif normalized == "pan_diagonal_bl":
            start_x, end_x, start_y, end_y = 0.62, 0.38, 0.38, 0.62
        elif normalized == "pan_diagonal_tr":
            start_x, end_x, start_y, end_y = 0.38, 0.62, 0.62, 0.38
        x_expr = f"(in_w-out_w)*({start_x:.3f}+({end_x - start_x:.3f})*{ease})"
        y_expr = f"(in_h-out_h)*({start_y:.3f}+({end_y - start_y:.3f})*{ease})"
        return (
            f"scale={scaled_w}:{scaled_h}:force_original_aspect_ratio=increase:flags=fast_bilinear,"
            f"crop={w}:{h}:x='{x_expr}':y='{y_expr}',setsar=1"
        )
    if normalized in {"", "static", "hold"}:
        return _blurred_fit_filter(w, h)
    try:
        seconds = max(0.1, float(duration or 1.0))
    except (TypeError, ValueError):
        seconds = 1.0
    # Smoothstep cubic easing: 3*p^2 - 2*p^3 for organic camera deceleration/acceleration
    p = f"min(1,max(0,t/{seconds:.3f}))"
    ease = f"({p}*{p}*(3-2*{p}))"

    # Ensure noticeable but elegant drift regardless of whether image is narrow or wide
    w_drift = f"max(28, (W-w)*0.28)"
    h_drift = f"max(18, (H-h)*0.22)"

    x_delta = "0"
    y_delta = "0"
    if normalized in {"pan_left", "drift_left"}:
        x_delta = f"{w_drift}*(1-2*{ease})"
    elif normalized in {"pan_right", "drift_right"}:
        x_delta = f"{w_drift}*(2*{ease}-1)"
    elif normalized == "pan_up":
        y_delta = f"{h_drift}*(1-2*{ease})"
    elif normalized == "pan_down":
        y_delta = f"{h_drift}*(2*{ease}-1)"
    elif normalized == "pan_diagonal_bl":
        x_delta = f"{w_drift}*(1-2*{ease})"
        y_delta = f"{h_drift}*(2*{ease}-1)"
    elif normalized == "pan_diagonal_tr":
        x_delta = f"{w_drift}*(2*{ease}-1)"
        y_delta = f"{h_drift}*(1-2*{ease})"
    elif normalized in {"settle", "zoom_out", "zoom_out_slight"}:
        x_delta = f"{w_drift}*0.4*(1-{ease})"
        y_delta = f"-{h_drift}*0.3*(1-{ease})"
    elif normalized in {"zoom_in", "zoom_in_slight", "zoom_fast", "zoom_slow"}:
        x_delta = f"-{w_drift}*0.4*(1-{ease})"
        y_delta = f"-{h_drift}*0.3*(1-{ease})"
    elif normalized in {"rotate_left", "rotate_right", "rotate_gentle"}:
        direction = "-" if normalized == "rotate_left" else ""
        x_delta = f"{direction}{w_drift}*0.35*(2*{ease}-1)"
        y_delta = f"{h_drift}*0.25*(1-2*{ease})"

    def _add_delta(base: str, delta: str) -> str:
        d = str(delta).strip()
        if not d or d == "0":
            return base
        if d.startswith(("+", "-")):
            return f"{base}{d}"
        return f"{base}+{d}"

    x_expr = _add_delta("(W-w)/2", x_delta)
    y_expr = _add_delta("(H-h)/2", y_delta)

    blur_radius = max(1, min(24, min(int(w), int(h)) // 4))
    blur_power = max(1, min(12, blur_radius // 2))
    # A full-resolution boxblur for every portrait scene is the dominant
    # render cost on CPU/QSV machines. In the fast production profile the
    # backdrop is intentionally soft, so blur a quarter-resolution copy and
    # upscale it before compositing. The foreground remains full quality and
    # fully visible; only the decorative backdrop uses the cheaper path.
    if _ACTIVE_FAST_RENDER:
        bg_w = max(320, int(w) // 4)
        bg_h = max(180, int(h) // 4)
        bg_scale = (
            f"scale={bg_w}:{bg_h}:force_original_aspect_ratio=increase:flags=fast_bilinear,"
            f"crop={bg_w}:{bg_h},boxblur=8:2,"
            f"scale={w}:{h}:flags=fast_bilinear"
        )
        fg_w = max(2, int(round(float(w) * 0.96)))
        fg_h = max(2, int(round(float(h) * 0.96)))
        return (
            f"split=2[bg][fg];"
            f"[bg]{bg_scale},eq=contrast=0.92:saturation=0.78:brightness=-0.08[bgfit];"
            f"[fg]scale={fg_w}:{fg_h}:force_original_aspect_ratio=decrease:flags=fast_bilinear[fgfit];"
            "[bgfit][fgfit]overlay=x='(W-w)/2':y='(H-h)/2':eval=frame,setsar=1"
        )
    fg_w = max(2, int(round(float(w) * 0.96)))
    fg_h = max(2, int(round(float(h) * 0.96)))
    return (
        f"split=2[bg][fg];"
        f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"boxblur={blur_radius}:{blur_power},eq=contrast=0.92:saturation=0.78:brightness=-0.08[bgfit];"
        f"[fg]scale={fg_w}:{fg_h}:force_original_aspect_ratio=decrease:flags=lanczos+accurate_rnd[fgfit];"
        f"[bgfit][fgfit]overlay=x='{x_expr}':y='{y_expr}':eval=frame,setsar=1"
    )


def _crop_fill_filter(w: int, h: int, fy: float = 0.5) -> str:
    """Full-bleed cover crop with studio-grade Lanczos resampling."""
    if abs(float(fy) - 0.5) > 1e-4:
        return (
            f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos+accurate_rnd,"
            f"crop={w}:{h}:x='(in_w-out_w)/2':y='clip(in_h*{fy:.5f}-out_h/2,0,in_h-out_h)',setsar=1"
        )
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos+accurate_rnd,"
        f"crop={w}:{h},setsar=1"
    )


def _transition_filter(transition_id: str, duration: float) -> str:
    """Return xfade transition name for the transition ID."""
    if transition_id == "cut":
        return ""
    t_map = {
        "dissolve": "fade",
        "dip_black": "fadeblack",
        "dip_white": "fadewhite",
        "slide": "slideright",
        "slide_left": "slideleft",
        "slide_right": "slideright",
        "slide_up": "slideup",
        "slide_down": "slidedown",
        "smooth": "smoothleft",
        "smooth_left": "smoothleft",
        "smooth_right": "smoothright",
        "smooth_up": "smoothup",
        "smooth_down": "smoothdown",
        "directional_wipe": "wipeleft",
        "wipe": "wiperight",
        "wipe_left": "wipeleft",
        "wipe_right": "wiperight",
        "wipe_up": "wipeup",
        "wipe_down": "wipedown",
        "wipe_tl": "wipetl",
        "wipe_tr": "wipetr",
        "wipe_bl": "wipebl",
        "wipe_br": "wipebr",
        "circle_open": "circleopen",
        "circle_close": "circleclose",
        "circle_reveal": "circlecrop",
        "zoom_in": "zoomin",
        "zoom_out": "zoomout",
        "quick_zoom": "zoomin",
        "quick_zoom_in": "zoomin",
        "quick_zoom_out": "zoomout",
        "squeeze_h": "squeezeh",
        "squeeze_v": "squeezev",
        "radial": "radial",
        "radial_blur": "radial",
        "hblur": "hblur",
        "fade_grays": "fadegrays",
        "diagonal_tl": "diagtl",
        "diagonal_tr": "diagtr",
        "diagonal_bl": "diagbl",
        "diagonal_br": "diagbr",
        "film_burn": "fade",
        "light_leak": "fade",
        "page_flip": "smoothleft",
        "paper_tear": "wipeleft",
        "luma_flash": "fadewhite",
        "white_flash": "fadewhite",
        "map_sweep": "smoothleft",
        "glitch_slice": "pixelize",
        "glitch_burst": "pixelize",
        "ink_bleed": "fadeblack",
        "split_reveal": "squeezeh",
        "iris": "circleopen",
        "black_flash": "fadeblack",
        "ripple": "distance",
        "glitch": "pixelize",
    }
    return t_map.get(transition_id, "")


def _apply_blur_stage(base_path: str, output_path: str, blur_radius: int = 8) -> bool:
    """Apply boxblur + slight vignette for blurred_stage/depth_separation treatment."""
    ffmpeg = _ffmpeg()
    vf = f"boxblur={blur_radius}:2,eq=contrast=1.08:saturation=0.85:brightness=-0.04"
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", base_path,
        "-vf", vf,
        *_video_encoder_args(), "-threads", _encode_threads(),
        output_path,
    ]
    try:
        result = _run_ffmpeg_process(cmd, capture_output=True, timeout=120)
        if result.returncode == 0 and Path(output_path).exists() and Path(output_path).stat().st_size > 4096:
            return True
    except Exception:
        pass
    return False


# Image-treatment → FFmpeg vf chain. Each treatment applies a distinct visual
# style so the video looks professionally designed rather than flat.
_IMAGE_TREATMENTS: dict = {
    # Soften background, slight desaturation, vignette
    "blurred_stage": lambda: "eq=contrast=1.04:saturation=0.92:brightness=-0.02,vignette=angle=PI/5",
    # Texture separation without smearing the footage
    "depth_separation": lambda: "unsharp=lx=3:ly=3:la=0.35,eq=contrast=1.06:saturation=0.90:brightness=-0.02",
    # Cropped tighter with a slight zoom
    "editorial_crop": lambda: "crop=iw*0.92:ih*0.92:(iw-iw*0.92)/2:(ih-ih*0.92)/2,scale={w}:{h}:force_original_aspect_ratio=increase,eq=contrast=1.05".format(w=WIDTH, h=HEIGHT),
    # Split-screen mask (vertical center).  The line is intentionally subtle;
    # the actual two-shot composition is handled by the shot contract.
    "split_mask": lambda: "eq=contrast=1.04:saturation=0.94,drawbox=x=iw/2-2:y=0:w=4:h=ih:color=white@0.28:t=fill",
    # Film gate: preserve the configured canvas, then apply the warm grade.
    # Padding without an explicit target used to leak a larger frame into the
    # concat stage when a source or treatment had a different aspect ratio.
    "film_gate": lambda: "scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,eq=contrast=1.04:saturation=0.90:brightness=0.01:gamma=0.98".format(w=WIDTH, h=HEIGHT),
    # Paper reveal: subtle white wash + slight desaturation
    "paper_reveal": lambda: "eq=contrast=1.03:saturation=0.82:brightness=0.025:gamma=1.02",
    # Map window: draw a frame border + slight crop. The final scale/crop is
    # intentional: a treatment must never change the semantic canvas size.
    "map_window": lambda: "crop=iw*0.88:ih*0.88:(iw-iw*0.88)/2:(ih-ih*0.88)/2,scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},drawbox=x=0:y=0:w=iw:h=4:color=white@0.6:t=fill,drawbox=x=0:y=ih-4:w=iw:h=4:color=white@0.6:t=fill,drawbox=x=0:y=0:w=4:h=ih:color=white@0.6:t=fill,drawbox=x=iw-4:y=0:w=4:h=ih:color=white@0.6:t=fill".format(w=WIDTH, h=HEIGHT),
    # Focus treatments are deliberately restrained because the verified
    # subject must remain visible.  They add a clear visual language without
    # hard-cropping faces or text from the source frame.
    "iris_focus": lambda: "eq=contrast=1.05:saturation=0.96,vignette=angle=PI/4",
    "spotlight": lambda: "eq=contrast=1.08:saturation=0.94:brightness=-0.015,vignette=angle=PI/5",
    "subject_slide": lambda: "crop=iw*0.94:ih*0.94:(iw-ow)/2:(ih-oh)/2,scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}".format(w=WIDTH, h=HEIGHT),
    "archival_white_border": lambda: "scale={w}*0.92:{h}*0.92:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=#f5f5f7,eq=contrast=1.06:saturation=0.88:brightness=0.01".format(w=WIDTH, h=HEIGHT),
    "film_matte_16mm": lambda: "scale={w}*0.95:{h}*0.95:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=#08080a,eq=contrast=1.08:saturation=0.92:gamma=0.96,vignette=angle=PI/4".format(w=WIDTH, h=HEIGHT),
    "side_by_side_collage": lambda: "eq=contrast=1.05:saturation=0.95,drawbox=x=iw/2-2:y=0:w=4:h=ih:color=white@0.4:t=fill",
    "depth_drift_25d": lambda: "unsharp=lx=5:ly=5:la=0.45,eq=contrast=1.07:saturation=1.02:brightness=-0.01",
    # Hold (no-op)
    "hold": lambda: None,
    # Static — same as hold
    "static": lambda: None,
}


def _apply_image_treatment(base_path: str, output_path: str, treatment: str) -> bool:
    """Apply the given image treatment as a separate post-processing pass.

    Each treatment is a distinct visual style. If the treatment is unknown
    or produces no effect, the base file is left untouched (returns False).
    """
    if treatment in ("hold", "static", "", None):
        return False
    vf_fn = _IMAGE_TREATMENTS.get(treatment)
    if vf_fn is None:
        return False
    vf = vf_fn()
    if not vf:
        return False
    ffmpeg = _ffmpeg()
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", base_path,
        "-vf", vf,
        *_video_encoder_args(), "-threads", _encode_threads(),
        output_path,
    ]
    try:
        result = _run_ffmpeg_process(cmd, capture_output=True, timeout=120)
        if result.returncode == 0 and Path(output_path).exists() and Path(output_path).stat().st_size > 4096:
            return True
    except Exception:
        pass
    return False


# Atmosphere layer: grain + light_leak + dust effects
def _apply_atmosphere(base_path: str, output_path: str, atmosphere: dict, *, protect_text: bool = False) -> bool:
    """Apply atmosphere filters (grain, light_leak, dust) to a rendered segment.

    The atmosphere dict comes from scene.atmosphere in the manifest and
    carries:
      - grain: 0..1 strength
      - vignette: 0..1 strength
      - dust: 0..1 strength
      - light_leak: bool (adds a warm color overlay)
      - intensity: 0..1 overall strength
    When ``protect_text`` is set (scene carries baked editorial text), any
    filter that would darken the text window (vignette) is skipped so cards
    stay legible over footage.
    Returns True if the treatment was applied.
    """
    if not atmosphere:
        return False
    grain = float(atmosphere.get("grain", 0) or 0)
    vignette = float(atmosphere.get("vignette", 0) or 0)
    dust = float(atmosphere.get("dust", 0) or 0)
    light_leak = bool(atmosphere.get("light_leak", False))
    intensity = float(atmosphere.get("intensity", 0) or 0)
    if protect_text:
        # Baked editorial text must stay bright: drop any luminance-cutting
        # pass (vignette) and cap the midtone curves so text stays crisp.
        vignette = 0.0
        dust = min(dust, 0.35)
    # Skip if everything is zero
    if grain <= 0.01 and vignette <= 0.01 and dust <= 0.01 and not light_leak:
        return False

    parts = []
    if grain > 0.01:
        # alls/100 is roughly the noise percentage
        ns = max(1, int(grain * 40))
        parts.append(f"noise=alls={ns}:allf=t+u")
    if vignette > 0.01:
        vi = max(0.1, min(1.0, vignette))
        # A strong PI/5 vignette darkens the whole frame except the exact
        # center, which made corner/card editorial text unreadable. Use a
        # gentler angle and scale the effect with the requested strength.
        angle = math.pi / (5.0 + 2.2 * (1.0 - vi))
        parts.append(f"vignette=angle={angle:.6f}")
    if light_leak:
        # Warm color overlay on right side (subtle light leak)
        parts.append(
            "colorchannelmixer=rr=1.08:gg=1.02:bb=0.92"
        )
    if dust:
        # Subtle brightness flicker
        ds = max(0.005, min(0.02, dust * 0.01))
        parts.append(f"curves=all='0/0 0.5/{0.5+ds} 1/1'")

    if not parts:
        return False
    vf = ",".join(parts)
    ffmpeg = _ffmpeg()
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", base_path,
        "-vf", vf,
        *_video_encoder_args(), "-threads", _encode_threads(),
        output_path,
    ]
    try:
        result = _run_ffmpeg_process(cmd, capture_output=True, timeout=120)
        if result.returncode == 0 and Path(output_path).exists() and Path(output_path).stat().st_size > 4096:
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Scene rendering
# ---------------------------------------------------------------------------

def _apply_scene_overlays(base_path: str, output_path: str, overlays: list) -> bool:
    """Apply the scene ``overlays`` list as one combined filter pass.

    Entries are either overlay ids ("film_grain", "letterbox", "scanlines",
    ...) or dicts {"id": ..., "intensity": ...}. Filters come from the
    overlays library; unknown ids are skipped so old manifests stay safe.
    """
    parts = []
    for item in overlays or []:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = str(item.get("id") or item.get("overlay_id") or "")
        else:
            continue
        entry = _OVERLAY_LIBRARY.get(name)
        if not entry:
            continue
        try:
            fn = entry["fn"]
            if isinstance(item, dict) and item.get("intensity") is not None:
                chain = fn(intensity=float(item["intensity"]))
            else:
                chain = fn()
        except TypeError:
            chain = fn()
        except Exception:
            continue
        if chain:
            parts.append(chain)
    if not parts:
        return False
    vf = ",".join(parts)
    ffmpeg = _ffmpeg()
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", base_path,
        "-vf", vf,
        *_video_encoder_args(), "-threads", _encode_threads(),
        output_path,
    ]
    try:
        result = _run_ffmpeg_process(cmd, capture_output=True, timeout=120)
        if result.returncode == 0 and Path(output_path).exists() and Path(output_path).stat().st_size > 4096:
            return True
    except Exception:
        pass
    return False


def _color(value: str, fallback: str = "white") -> str:
    """Normalize a CSS/HTML color to the FFmpeg ``0xRRGGBB`` form."""
    raw = str(value or fallback).strip()
    if raw.startswith("#"):
        digits = raw[1:]
        if len(digits) == 3:
            digits = "".join(char * 2 for char in digits)
        if re.fullmatch(r"[0-9a-fA-F]{6}", digits):
            return f"0x{digits}"
    return raw or fallback


def _apply_text_card_mode(chains: Sequence[str], mode: str, palette: Mapping[str, Any]) -> list[str]:
    """Apply the semantic text-card background choice to native drawtext.

    VOX templates intentionally share one safe ``_dt`` builder.  Applying the
    card mode at the boundary keeps those templates reusable while allowing a
    scene to use a bare headline, an accent plate, or a dark surface.  Unknown
    modes fall back to the template's authored styling.
    """
    normalized = str(mode or "surface").strip().lower()
    if normalized not in {"none", "transparent", "bare", "surface", "accent", "danger"}:
        return list(chains or [])
    accent = str((palette or {}).get("accent") or "#d8a84e")
    danger = str((palette or {}).get("danger") or "#c94a3d")
    # drawtext's box options are deliberately simple in the curated template
    # catalog.  Keep the expression conservative so commas/colons inside x/y
    # expressions are never touched.
    box_pattern = re.compile(r":box=1:boxcolor=[^:]+(?::boxborderw=[^:]+)?")
    out: list[str] = []
    for raw in chains or []:
        chain = str(raw or "")
        if normalized in {"none", "transparent", "bare"}:
            chain = box_pattern.sub("", chain)
        else:
            desired = accent if normalized == "accent" else (danger if normalized == "danger" else "#0a0f1d")
            # Retain the authored opacity while changing only the plate hue.
            chain = re.sub(
                r"boxcolor=[^:]+",
                f"boxcolor={_color(desired)}@0.88",
                chain,
                count=1,
            )
        out.append(chain)
    return out


def _scene_target_frames(duration: float) -> int:
    """Return a CFR frame count that never undershoots the narration slot."""
    return max(1, int(math.ceil(max(0.0, float(duration or 0.0)) * FPS - 1e-9)))


def _scene_frame_contracts(scenes: Sequence[Mapping[str, Any]]) -> list[int]:
    """Quantize contiguous scene slots without accumulating frame rounding.

    Rounding every scene independently can add almost one frame per scene.  A
    long documentary would then drift by seconds even though each individual
    scene looked correct.  Quantizing cumulative boundaries preserves every
    scene in order while making the final frame count equal to the rounded-up
    total timeline duration.
    """
    contracts: list[int] = []
    elapsed_seconds = 0.0
    elapsed_frames = 0
    for scene in scenes:
        try:
            duration = max(0.0, float(scene.get("duration", 0.0) or 0.0))
        except (TypeError, ValueError):
            duration = 0.0
        elapsed_seconds += duration
        boundary_frames = max(
            elapsed_frames + 1,
            int(math.ceil(elapsed_seconds * max(1, FPS) - 1e-9)),
        )
        contracts.append(boundary_frames - elapsed_frames)
        elapsed_frames = boundary_frames
    return contracts


def _exact_scene_filter(filter_chain: str, duration: float) -> str:
    """Pad/trim a filter chain to an exact CFR scene-frame contract."""
    frames = _scene_target_frames(duration)
    pad_seconds = max(float(duration or 0.0), frames / max(1, FPS)) + 0.25
    chain = str(filter_chain or "null").strip().strip(",") or "null"
    sep = "" if chain.endswith("]") else ","
    return (
        f"{chain}{sep}tpad=stop_mode=clone:stop_duration={pad_seconds:.3f},"
        f"trim=end_frame={frames},setpts=N/({FPS}*TB)"
    )


def _scene_graphic_interval(scene: Mapping[str, Any]) -> Tuple[float, float]:
    """Return the graphic's scene-local, clamped visibility interval.

    Semantic manifests historically stored ``graphic.start`` as an absolute
    timeline position even though each scene is rendered in its own FFmpeg
    process.  EditorialCueV2 stores explicit local bounds.  This adapter keeps
    old manifests renderable while making the local contract authoritative.
    """
    graphic = scene.get("graphic") if isinstance(scene.get("graphic"), Mapping) else {}
    cue = graphic.get("cue") if isinstance(graphic.get("cue"), Mapping) else {}
    try:
        scene_start = max(0.0, float(scene.get("start") or 0.0))
        scene_duration = max(0.001, float(scene.get("duration") or 0.001))
    except (TypeError, ValueError):
        scene_start, scene_duration = 0.0, 0.001

    local_start_value = (
        graphic.get("scene_local_start")
        if graphic.get("scene_local_start") is not None
        else cue.get("scene_local_start")
    )
    local_end_value = (
        graphic.get("scene_local_end")
        if graphic.get("scene_local_end") is not None
        else cue.get("scene_local_end")
    )
    try:
        if local_start_value is None:
            legacy_start = float(graphic.get("start") or scene_start)
            local_start = legacy_start - scene_start if legacy_start >= scene_start else legacy_start
        else:
            local_start = float(local_start_value)
    except (TypeError, ValueError):
        local_start = 0.0
    local_start = max(0.0, min(scene_duration, local_start))

    # Strict scene-boundary exit buffer:
    # Transitions (dissolve, wipe, xfade) occupy 0.35-0.5s at the end of the scene.
    # To guarantee the graphic NEVER enters or bleeds into the next scene,
    # the graphic MUST finish and fully fade out before the transition begins.
    trans = scene.get("transition") if isinstance(scene.get("transition"), Mapping) else {}
    trans_dur = float(trans.get("duration") or 0.35)
    exit_buffer = max(0.45, trans_dur + 0.15)
    max_graphic_end = max(local_start + 0.3, scene_duration - exit_buffer) if scene_duration > 1.2 else scene_duration

    try:
        if local_end_value is not None:
            local_end = float(local_end_value)
        else:
            local_end = local_start + float(graphic.get("duration") or scene_duration)
    except (TypeError, ValueError):
        local_end = max_graphic_end
    local_end = max(local_start, min(max_graphic_end, local_end))
    return round(local_start, 3), round(local_end, 3)


def _shift_filter_time(expression: str, local_start: float) -> str:
    """Shift FFmpeg's time variable without touching words containing ``t``."""
    if local_start <= 0:
        return str(expression or "")
    return re.sub(
        r"(?<![A-Za-z0-9_])t(?![A-Za-z0-9_])",
        f"(t-{local_start:.3f})",
        str(expression or ""),
    )


def _gate_timeline_filter(chain: str, local_start: float, local_end: float) -> str:
    """Gate one drawtext/drawbox layer and restart its animation at cue entry.

    Template builders emit relative animation expressions starting at t=0.
    Scene rendering, however, evaluates ``t`` from the beginning of the scene.
    Rewriting animation-bearing fields to ``t-local_start`` keeps FFmpeg and
    Remotion motion in sync, while the final ``between`` gate guarantees that
    every visual layer disappears at the exact cue end.
    """
    value = str(chain or "")
    if not value.startswith(("drawtext=", "drawbox=")):
        return value
    prefix, payload = value.split("=", 1)
    fields = re.split(r"(?<!\\):", payload)
    gated = []
    existing_enable = ""
    animated_fields = {"x", "y", "w", "h", "alpha"}
    for field in fields:
        key, sep, raw = field.partition("=")
        if not sep:
            gated.append(field)
            continue
        if key == "enable":
            existing_enable = _shift_filter_time(raw, local_start)
            continue
        if key in animated_fields and re.search(r"(?<![A-Za-z0-9_])t(?![A-Za-z0-9_])", raw):
            raw = _shift_filter_time(raw, local_start)
        gated.append(f"{key}={raw}")
    window = f"between(t\\,{local_start:.3f}\\,{local_end:.3f})"
    if existing_enable:
        window = f"({existing_enable})*({window})"
    gated.append(f"enable='{window}'")
    return f"{prefix}=" + ":".join(gated)


def _render_base_scene(
    media_src: str,
    media_type: str,
    motion: str,
    duration: float,
    output_path: str,
    temp_dir: str,
    start_time: float = 0,
    extra_vf: str = "",
    focal_bbox: Optional[Sequence[float]] = None,
    safe_framing: bool = False,
) -> bool:
    """Render a base segment while preserving narrow-source subjects.

    ``safe_framing`` remains an explicit still-image preference. Portrait,
    square, and other narrow sources are also detected from their media
    dimensions and rendered as a contained foreground over a restrained
    blurred copy of the same source. Wide video keeps the normal full-bleed
    treatment.
    """
    ffmpeg = _ffmpeg()
    w, h = WIDTH, HEIGHT
    out_file = str(Path(output_path).resolve())
    Path(out_file).parent.mkdir(parents=True, exist_ok=True)
    media_src_str = str(Path(media_src).resolve()) if Path(media_src).is_file() else str(media_src)
    media_type_key = str(media_type or "").strip().lower()
    is_video = media_type_key == "video" or media_type_key.startswith("video/") or (
        Path(media_src).suffix.lower() in {".mp4", ".mov", ".avi", ".webm", ".mkv"}
    )

    fallback_fy: float = 0.5
    target_aspect = float(w) / float(h) if h > 0 else 16.0 / 9.0
    source_aspect = _media_source_aspect(media_src_str, is_video=is_video)
    # Portrait, square, and other narrow sources must remain fully visible.
    # A blurred copy of the same source fills the 16:9 canvas, while the
    # original is contained in the centre. This applies to clips as well as
    # stills; the previous video-only cover path silently cropped vertical
    # footage before it reached the editor.
    needs_contained_backdrop = bool(
        source_aspect is not None and source_aspect < (target_aspect * 0.96)
    )
    if needs_contained_backdrop:
        vf = _safe_animated_fit_filter(
            motion,
            duration,
            w,
            h,
            force_blur=True,
        )
    elif is_video:
        vf = _crop_fill_filter(w, h)
    else:
        img_aspect = source_aspect
        fallback_fy = 0.25 if (img_aspect and img_aspect < (target_aspect * 0.85)) else 0.5
        if safe_framing:
            # Keep the complete verified still visible.  Editorial text,
            # transitions, grain, and audio cues still animate around it; the
            # source itself is not cropped merely to fill 16:9.  The bounded
            # foreground drift keeps successive stills alive without forcing
            # a Ken-Burns cover zoom into the subject.
            vf = _safe_animated_fit_filter(motion, duration, w, h)
        else:
            # Give every cover-framed image active cinematic motion (zoom
            # in/out, pan, drift, Ken Burns) for legacy/explicit cover media.
            vf = _motion_filter(
                motion or "zoom_in",
                duration,
                w,
                h,
                source=media_src_str,
                focal_bbox=focal_bbox,
            )
            if not vf:
                vf = _crop_fill_filter(w, h, fy=fallback_fy)

    if extra_vf:
        vf = f"{vf},{extra_vf}"

    if is_video:
        seek = max(float(start_time or 0), 0.0)
        src_dur = _probe_duration_seconds(media_src_str)
        if src_dur > 0:
            seek = min(seek, max(0.0, src_dur - duration))
        target_frames = _scene_target_frames(duration)
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-err_detect", "ignore_err",
            "-fflags", "+discardcorrupt+genpts",
            "-i", media_src_str,
            "-ss", f"{seek:.3f}",
            "-vf", _exact_scene_filter(f"fps={FPS},{vf}", duration),
            "-frames:v", str(target_frames),
            *_video_encoder_args(),
            "-threads", _encode_threads(),
            out_file,
        ]
    else:
        img_path = _get_ffmpeg_img_path(media_src_str, temp_dir)
        target_frames = _scene_target_frames(duration)
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-f", "image2", "-loop", "1", "-framerate", str(FPS),
            "-i", img_path,
            "-vf", _exact_scene_filter(vf, duration),
            "-frames:v", str(target_frames),
            *_video_encoder_args(),
            "-threads", _encode_threads(),
            out_file,
        ]

    try:
        result = _run_ffmpeg_process(cmd, capture_output=True, timeout=300, cwd=temp_dir)
        if result.returncode == 0 and Path(out_file).exists() and Path(out_file).stat().st_size > 4096:
            return True
        # Tier 1 fallback: if primary render failed, retry with simple crop/fill filter
        err_msg = (result.stderr or "").strip()[:180]
        if result.returncode != 0:
            print(f"   [scene] primary ffmpeg encode failed (rc={result.returncode}): {err_msg}")
            _force_software_encoder(result.stderr)
        simple_vf = (
            _safe_animated_fit_filter(
                motion or "static",
                duration,
                w,
                h,
                force_blur=needs_contained_backdrop,
            )
            if (safe_framing or needs_contained_backdrop)
            else _crop_fill_filter(w, h, fy=fallback_fy)
        )
        if extra_vf:
            simple_vf = f"{simple_vf},{extra_vf}"
        if is_video:
            fallback_cmd = [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
                "-err_detect", "ignore_err", "-fflags", "+discardcorrupt+genpts",
                "-i", media_src_str, "-ss", f"{seek:.3f}",
                "-vf", _exact_scene_filter(f"fps={FPS},{simple_vf}", duration),
                "-frames:v", str(_scene_target_frames(duration)),
                *_video_encoder_args(),
                "-threads", _encode_threads(), out_file,
            ]
        else:
            img_path = _get_ffmpeg_img_path(media_src_str, temp_dir)
            fallback_cmd = [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
                "-f", "image2", "-loop", "1", "-framerate", str(FPS),
                "-i", img_path,
                "-vf", _exact_scene_filter(simple_vf, duration),
                "-frames:v", str(_scene_target_frames(duration)),
                *_video_encoder_args(),
                "-threads", _encode_threads(),
                out_file,
            ]
        fb_result = _run_ffmpeg_process(fallback_cmd, capture_output=True, timeout=300, cwd=temp_dir)
        if fb_result.returncode == 0 and Path(out_file).exists() and Path(out_file).stat().st_size > 4096:
            return True
        if fb_result.returncode != 0:
            print(f"   [scene] fallback ffmpeg encode failed (rc={fb_result.returncode}): {(fb_result.stderr or '').strip()[:180]}")
            _force_software_encoder(fb_result.stderr)
    except SemanticRenderBudgetExceeded:
        # Never hide a job-level wall exhaustion behind a generic scene
        # failure.  The caller owns the native delivery recovery decision.
        raise
    except subprocess.TimeoutExpired:
        print(f"   [scene] base render timed out for {media_src[:60]}")
    except Exception as e:
        print(f"   [scene] base render error: {e}")
    return False



def _composite_overlay(
    base_path: str,
    overlay_path: str,
    output_path: str,
    duration: float,
    local_start: float = 0.0,
    local_end: Optional[float] = None,
) -> bool:
    """Composite overlay onto base video using FFmpeg overlay filter."""
    ffmpeg = _ffmpeg()
    end = max(float(local_start or 0.0), min(float(duration), float(local_end if local_end is not None else duration)))
    start = max(0.0, min(end, float(local_start or 0.0)))
    total_visible = max(0.1, end - start)
    fade_in_d = min(0.35, max(0.08, total_visible * 0.25))
    fade_out_d = min(0.30, max(0.08, total_visible * 0.25))
    fade_out_st = max(start + fade_in_d, end - fade_out_d)

    # A factual card should arrive decisively, then hold long enough to read.
    # The former 350ms linear float read as laggy and was especially obvious on
    # short crime-video facts.  This uses a quick eased entrance/exit while
    # keeping the complete phrase visible as one stable graphic.
    fade_in_d = min(0.20, max(0.08, total_visible * 0.18))
    fade_out_d = min(0.18, max(0.08, total_visible * 0.18))
    fade_out_st = max(start + fade_in_d, end - fade_out_d)
    entry_p = f"min(1,max(0,(t-{start:.3f})/{fade_in_d:.3f}))"
    exit_p = f"min(1,max(0,(t-{fade_out_st:.3f})/{fade_out_d:.3f}))"
    y_expr = (
        f"if(lte(t,{start + fade_in_d:.3f}),(1-{entry_p})*(1-{entry_p})*30,"
        f"if(gte(t,{fade_out_st:.3f}),-({exit_p}*{exit_p})*16,0))"
    )
    filter_graph = (
        f"[1:v]format=rgba,"
        f"fade=t=in:st={start:.3f}:d={fade_in_d:.3f}:alpha=1,"
        f"fade=t=out:st={fade_out_st:.3f}:d={fade_out_d:.3f}:alpha=1[ov];"
        f"[0:v][ov]overlay=x=0:y='{y_expr}':format=auto:"
        f"enable='between(t,{start:.3f},{end:.3f})'[vcomp];"
        + _exact_scene_filter("[vcomp]", duration)
        + "[vout]"
    )
    overlay_ext = Path(overlay_path).suffix.lower()
    overlay_input = (
        ["-loop", "1", "-framerate", str(FPS), "-i", str(overlay_path)]
        if overlay_ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
        else ["-i", str(overlay_path)]
    )
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", base_path,
        *overlay_input,
        "-filter_complex", filter_graph,
        "-map", "[vout]",
        "-frames:v", str(_scene_target_frames(duration)),
        *_video_encoder_args(),
        "-threads", _encode_threads(),
        str(output_path),
    ]

    try:
        result = _run_ffmpeg_process(cmd, capture_output=True, timeout=180)
        if result.returncode == 0 and Path(output_path).exists() and Path(output_path).stat().st_size > 4096:
            return True
        print(f"   [composite] failed: {result.stderr.decode(errors='replace')[:200]}")
    except SemanticRenderBudgetExceeded:
        raise
    except Exception as e:
        print(f"   [composite] error: {e}")
    return False


def _ensure_scene_segment_duration(
    segment_path: str,
    duration: float,
    output_path: str,
) -> Optional[str]:
    """Repair a short final scene segment before it reaches stream-copy concat."""
    path = Path(segment_path)
    if not path.is_file() or path.stat().st_size < 1024:
        return None
    expected = _scene_target_frames(duration) / max(1, FPS)
    actual = _probe_duration_seconds(str(path))
    # MPEG-TS duration metadata has frame-sized jitter. Anything within 1.5
    # frames of the target already satisfies the exact CFR contract.
    if actual > 0 and actual + (1.5 / max(1, FPS)) >= expected:
        return str(path)

    repaired = Path(output_path)
    repaired.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(path),
        "-vf", _exact_scene_filter(f"fps={FPS},format=yuv420p", duration),
        "-frames:v", str(_scene_target_frames(duration)),
        "-an",
        *_video_encoder_args(), "-threads", _encode_threads(),
        str(repaired),
    ]
    try:
        result = _run_ffmpeg_process(cmd, capture_output=True, timeout=60)
        if result.returncode == 0 and repaired.is_file() and repaired.stat().st_size >= 1024:
            repaired_duration = _probe_duration_seconds(str(repaired))
            if repaired_duration > 0 and repaired_duration + (1.5 / max(1, FPS)) >= expected:
                print(
                    f"   [scene] duration repair: {actual:.3f}s -> "
                    f"{repaired_duration:.3f}s"
                )
                return str(repaired)
    except SemanticRenderBudgetExceeded:
        raise
    except Exception as exc:
        print(f"   [scene] duration repair failed: {exc}")
    return None


def _merge_static_overlays(paths: Sequence[str], output_path: str) -> Optional[str]:
    """Flatten semantic PNG layers so a scene needs only one overlay encode."""
    usable = [str(path) for path in paths if path and Path(path).is_file()]
    if not usable:
        return None
    if len(usable) == 1:
        return usable[0]
    try:
        canvas = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        for raw_path in usable:
            with Image.open(raw_path) as layer:
                rgba = layer.convert("RGBA")
                if rgba.size != (WIDTH, HEIGHT):
                    rgba = rgba.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
                canvas.alpha_composite(rgba)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path, format="PNG", optimize=False)
        return output_path
    except Exception as exc:
        print(f"   [overlay] flatten failed ({exc}); using first semantic layer")
        return usable[0]


def _add_transition(
    scene_a_path: str,
    scene_b_path: str,
    transition_id: str,
    transition_duration: float,
    output_path: str,
) -> bool:
    """Apply transition between two scenes using xfade."""
    if transition_id == "cut" or transition_duration <= 0:
        return shutil.copy2(scene_b_path, output_path) is not None

    ffmpeg = _ffmpeg()
    xfade = _transition_filter(transition_id, transition_duration)
    if not xfade:
        return shutil.copy2(scene_b_path, output_path) is not None

    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", scene_a_path,
        "-i", scene_b_path,
        "-filter_complex", f"[0:v][1:v]xfade=transition={xfade}:duration={transition_duration:.3f}:offset=0",
        "-c:v", "libx264", "-preset", _preset(), "-crf", str(_render_crf()),
        "-pix_fmt", "yuv420p",
        "-threads", _encode_threads(),
        str(output_path),
    ]
    try:
        result = _run_ffmpeg_process(cmd, capture_output=True, timeout=180)
        return result.returncode == 0 and Path(output_path).exists() and Path(output_path).stat().st_size > 4096
    except Exception as e:
        print(f"   [transition] error: {e}")
    return False


def _composite_overlay_frames(
    base_path: str,
    frames_dir: str,
    output_path: str,
    duration: float,
) -> bool:
    """Composite a pre-rendered frame sequence (PNGs) onto base video using FFmpeg.
    
    The frames directory should contain frame_0000.png through frame_NNNN.png.
    This is the fast path — Remotion renders the animation once, FFmpeg composites at speed.
    """
    ffmpeg = _ffmpeg()
    # Check if at least one frame exists (after rename: frame_0000.png)
    first_frame = Path(frames_dir) / "frame_0000.png"
    if not first_frame.is_file():
        return False

    fps = 30
    frame_pattern = str(Path(frames_dir) / "frame_%04d.png")
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", base_path,
        "-framerate", str(fps), "-i", frame_pattern,
        "-filter_complex",
        "[1:v]format=rgba[ov];[0:v][ov]overlay=format=auto",
        "-t", f"{duration:.3f}",
        *_video_encoder_args(),
        "-threads", _encode_threads(),
        str(output_path),
    ]
    try:
        result = _run_ffmpeg_process(cmd, capture_output=True, timeout=180)
        if result.returncode == 0 and Path(output_path).exists() and Path(output_path).stat().st_size > 4096:
            return True
        stderr_tail = (result.stderr.decode(errors='replace')[-300:] if result.stderr else "")
        print(f"   [composite-frames] failed: {stderr_tail[:200]}")
    except Exception as e:
        print(f"   [composite-frames] error: {e}")
    return False


# ---------------------------------------------------------------------------
# Audio mixing
# ---------------------------------------------------------------------------

def _resolve_sound_cue_file(cue: Mapping[str, Any]) -> Optional[str]:
    """Resolve an SFX cue to a verified on-disk audio file across all directories and registries."""
    src = str(cue.get("src") or cue.get("path") or "").strip()
    if src and Path(src).is_file():
        return str(Path(src).resolve())

    # Check web-style /sound-effects/ paths
    clean_src = src.lstrip("/\\").replace("\\", "/")
    if clean_src.startswith("sound-effects/"):
        cand = PUBLIC / clean_src
        if cand.is_file():
            return str(cand.resolve())

    # Check SFX_REGISTRY token
    sfx_id = str(cue.get("sfx_id") or cue.get("kind") or "").strip()
    try:
        from services.editorial_director.sound_director import SFX_REGISTRY
        if sfx_id in SFX_REGISTRY:
            cand = PUBLIC / "sound-effects" / SFX_REGISTRY[sfx_id]["file"]
            if cand.is_file():
                return str(cand.resolve())
    except Exception:
        pass

    # Search folders under public/sound-effects
    sfx_root = PUBLIC / "sound-effects"
    for base in [sfx_root, sfx_root / "vidzeo", sfx_root / "edit"]:
        for test in [src, sfx_id, f"{src}.wav", f"{sfx_id}.wav", f"{sfx_id.replace('.', '_')}.wav"]:
            if test:
                cand = base / test
                if cand.is_file():
                    return str(cand.resolve())
    return None


def _build_audio_concat(
    voiceover_path: str,
    music_path: str,
    music_volume: float,
    sound_cues: list,
    total_duration: float,
    temp_dir: str,
) -> Optional[str]:
    """Build full audio track: voiceover + music + sound cues mixed together.
    Returns path to mixed audio or None.

    Audio normalization strategy:
      1. Voice, music, and cues are converted to a common 48 kHz stereo
         timeline. This is a sample-rate/channel-layout conversion only; it
         never changes playback speed or pitch.
      2. Music is kept below narration and sound cues are placed with explicit
         millisecond delays.
      3. The final mix is peak-limited and trimmed/padded to the requested
         picture duration. No ``-shortest`` or ``atempo`` path is used, so a
         short source cannot truncate or time-stretch the narration.
    """
    ffmpeg = _ffmpeg()

    def _voiceover_fallback(reason: str) -> Optional[str]:
        """Keep narration deliverable when the shared wall is nearly gone.

        The picture master is already complete at this point.  Returning the
        original voiceover lets the final mux preserve timing and pitch rather
        than failing the whole job because a large multi-input SFX graph could
        not start.  Normal runs still use the full voice+music+cue mix.
        """
        if voiceover_path and Path(voiceover_path).is_file():
            print(f"   [audio] {reason}; preserving narration track")
            return str(Path(voiceover_path).resolve())
        return None

    remaining_audio_budget = _remaining_render_budget()
    # A 75-cue graph can spend 30-60s in FFmpeg before it reports a failure.
    # Reserve enough time for the final video/audio mux; if that reserve is
    # already gone, skip the doomed graph and use the untouched narration.
    audio_graph_reserve = max(
        30.0,
        min(90.0, 12.0 + (len(sound_cues) * 0.75)),
    )
    if remaining_audio_budget is not None and remaining_audio_budget < audio_graph_reserve:
        return _voiceover_fallback(
            f"only {remaining_audio_budget:.0f}s remain for audio design "
            f"(reserve={audio_graph_reserve:.0f}s)"
        )

    audio_inputs = []
    filter_parts = []
    input_index = 0

    # Voiceover (normalized for consistent level)
    if voiceover_path and Path(voiceover_path).is_file():
        audio_inputs.extend(["-i", voiceover_path])
        # Keep the narration's timing and pitch untouched.  The gentle filter
        # removes only inaudible rumble/ultrasonic noise; loudness is handled
        # once at the final mix stage rather than by stacking time-varying
        # normalizers on the source voice.
        filter_parts.append(
            f"[{input_index}:a]highpass=f=80,lowpass=f=15000,"
            f"aformat=sample_rates=48000:channel_layouts=stereo,volume=1.0[a_voice]"
        )
        input_index += 1
    else:
        # Generate silence
        silence = Path(temp_dir) / "_silence.m4a"
        if not silence.exists():
            _run_ffmpeg_process([
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono",
                "-t", f"{total_duration:.3f}", "-c:a", "aac", str(silence),
            ], capture_output=True, timeout=60)
        audio_inputs.extend(["-i", str(silence)])
        filter_parts.append(
            f"[{input_index}:a]adelay=0|0,"
            "aformat=sample_rates=48000:channel_layouts=stereo[a_voice]"
        )
        input_index += 1

    # Music (with seamless looping, sidechain ducking, and smooth in/out fades)
    if music_path and Path(music_path).is_file():
        vol = max(0.0, min(1.0, music_volume if music_volume <= 1.0 else music_volume / 100.0))
        audio_inputs.extend(["-stream_loop", "-1", "-i", music_path])
        target_dur = max(0.1, float(total_duration or 0.0))
        fade_out_dur = min(3.0, max(0.1, target_dur * 0.2))
        fade_out_st = max(0.0, target_dur - fade_out_dur)
        fade_in_dur = min(1.5, max(0.1, target_dur * 0.2))
        fade_chain = f"afade=t=in:ss=0:d={fade_in_dur:.3f},afade=t=out:st={fade_out_st:.3f}:d={fade_out_dur:.3f}"
        if voiceover_path and Path(voiceover_path).is_file():
            # Keep music below narration. The former inline sidechaincompress
            # chain supplied only one input to a two-input filter, causing the
            # complete voice/music/SFX mix to fail on standard FFmpeg builds.
            filter_parts.append(
                f"[{input_index}:a]adelay=0|0,volume={vol * 0.55},"
                f"highpass=f=45,lowpass=f=14500,{fade_chain},"
                "aformat=sample_rates=48000:channel_layouts=stereo[a_music]"
            )
        else:
            filter_parts.append(
                f"[{input_index}:a]adelay=0|0,volume={vol},{fade_chain},"
                "aformat=sample_rates=48000:channel_layouts=stereo[a_music]"
            )
        input_index += 1

    # Sound cues — every cue is mixed. Sources are grouped so each sound file
    # is loaded once; each occurrence becomes its own delayed asplit branch.
    cue_filters: List[str] = []
    cue_groups: Dict[str, List[Dict]] = {}
    for cue in sound_cues:
        cue_path = _resolve_sound_cue_file(cue)
        if cue_path and Path(cue_path).is_file():
            cue_groups.setdefault(cue_path, []).append(cue)
    max_total_cues = 160
    total_placed = 0
    for cue_path, group in cue_groups.items():
        if total_placed >= max_total_cues:
            break
        group = group[: max(1, max_total_cues - total_placed)]
        total_placed += len(group)
        audio_inputs.extend(["-i", cue_path])
        split_labels = [f"[sp{input_index}_{i}]" for i in range(len(group))]
        if len(group) > 1:
            filter_parts.append(f"[{input_index}:a]asplit={len(group)}{''.join(split_labels)}")
        for branch_index, cue in enumerate(group):
            cue_start = float(cue.get("start", 0))
            cue_duration = float(cue.get("duration", 0.5))
            cue_volume = float(cue.get("volume", 0.15))
            delay_ms = int(max(0, cue_start) * 1000)
            # Sound-cue volume is an editorial mix value, not a gain multiplier
            # with a loud floor. Preserve quiet director levels under narration.
            actual_vol = min(0.48, max(0.04, cue_volume * 1.35))
            branch_in = split_labels[branch_index] if len(group) > 1 else f"[{input_index}:a]"
            tag = f"a_cue{input_index}_{branch_index}"
            filter_parts.append(
                f"{branch_in}adelay={delay_ms}|{delay_ms},"
                f"afade=in:st=0:d=0.05,volume={actual_vol},"
                f"afade=out:st={max(0.0, cue_duration - 0.1):.3f}:d=0.1,"
                "aformat=sample_rates=48000:channel_layouts=stereo"
                f"[{tag}]"
            )
            cue_filters.append(f"[{tag}]")
        input_index += 1


    if not filter_parts:
        return None

    # Build amix
    mix_count = 1  # voiceover
    if music_path and Path(music_path).is_file():
        mix_count += 1
    mix_count += len(cue_filters)

    all_inputs = "".join(["[a_voice]"] +
                         (["[a_music]"] if music_path and Path(music_path).is_file() else []) +
                         cue_filters)
    # First mix all sources, then apply final loudnorm for consistent output
    filter_parts.append(f"{all_inputs}amix=inputs={mix_count}:duration=longest:dropout_transition=3:normalize=0[mix_pre]")
    # Apply loudnorm to the mixed output for consistent perceived loudness
    # I=-16 LUFS, LRA=11, TP=-1.5 (broadcast/podcast standard)
    filter_parts.append(
        f"[mix_pre]loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary[mix_norm]"
    )
    # Final peak limiter plus a fixed-duration audio contract. ``atrim``
    # discards excess tail; ``apad`` is applied again at the final mux so a
    # short source is padded with silence, never sped up or used to shorten
    # the video.
    target_duration = max(0.1, float(total_duration or 0.0))
    filter_parts.append(
        f"[mix_norm]alimiter=limit=0.95,"
        f"aresample=48000:async=0:first_pts=0,"
        f"apad,atrim=start=0:end={target_duration:.3f},"
        f"asetpts=PTS-STARTPTS[a_out]"
    )

    full_filter = ";".join(filter_parts)
    output_path = Path(temp_dir) / "_mixed_audio.m4a"

    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
        *audio_inputs,
        "-filter_complex", full_filter,
        "-map", "[a_out]",
        "-c:a", "aac", "-b:a", "192k",
        "-ar", "48000", "-ac", "2",
        "-t", f"{target_duration:.3f}",
        str(output_path),
    ]

    try:
        result = _run_ffmpeg_process(cmd, capture_output=True, timeout=300)
        if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 1024:
            return str(output_path)
        if (
            _remaining_render_budget() is not None
            and (_remaining_render_budget() or 0.0) < 45.0
        ):
            return _voiceover_fallback(
                "full audio graph failed near the render deadline"
            )
        # Retry without loudnorm if it fails (loudnorm needs 2-pass for accuracy)
        print(f"   [audio] loudnorm mix failed, trying simpler normalization...")
        fallback_filter = ";".join(filter_parts[:-2])  # drop loudnorm + final limiter
        fallback_filter += (
            f";[mix_pre]alimiter=limit=0.95,"
            f"aresample=48000:async=0:first_pts=0,"
            f"apad,atrim=start=0:end={target_duration:.3f},"
            f"asetpts=PTS-STARTPTS[a_out]"
        )
        cmd_fallback = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
            *audio_inputs,
            "-filter_complex", fallback_filter,
            "-map", "[a_out]",
            "-c:a", "aac", "-b:a", "192k",
            "-ar", "48000", "-ac", "2",
            "-t", f"{target_duration:.3f}",
            str(output_path),
        ]
        result2 = _run_ffmpeg_process(cmd_fallback, capture_output=True, timeout=300)
        if result2.returncode == 0 and output_path.exists() and output_path.stat().st_size > 1024:
            return str(output_path)
        print(f"   [audio] mix failed: {result.stderr.decode(errors='replace')[:200]}")
    except SemanticRenderBudgetExceeded:
        return _voiceover_fallback(
            "audio design exceeded the remaining semantic render wall"
        )
    except Exception as e:
        print(f"   [audio] mix error: {e}")
        if _remaining_render_budget() is not None and (_remaining_render_budget() or 0.0) < 45.0:
            return _voiceover_fallback("audio design failed near the render deadline")
    return None


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

# Project root — used to resolve web-root-relative media URLs (/temp, /output,
# /fonts, /cache) that the semantic manifest carries for browser preview.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _caption_chains(text: str, duration: float, start: float = 0.0, end: Optional[float] = None) -> list:
    """Build lower-third caption drawtext chains from narration text.

    Uses static fontsize only (time-expressions on fontsize crash this FFmpeg
    build) — the fade in/out is driven purely by an alpha expression. Text is
    word-wrapped to at most 2 lines; overflow is truncated with an ellipsis.
    """
    text = str(text or "").strip()
    if not text:
        return []
    fontsize = max(22, min(56, int(HEIGHT * 0.05)))
    line_height = fontsize + 14
    # Keep caption lines short: long lines look cluttered and overlap safe
    # regions. ~28 chars at 1080p is a clean lower-third width.
    max_line_chars = max(20, min(32, int((WIDTH * 0.9) / (fontsize * 0.55))))

    lines = textwrap.wrap(text, width=max_line_chars, break_long_words=False)
    if len(lines) > 2:
        lines = lines[:2]
        lines[-1] = lines[-1].rsplit(" ", 1)[0] + "\u2026"
    if not lines:
        return []

    def _cap_esc(value: str) -> str:
        value = str(value or "")
        value = value.replace("\\", "")
        value = value.replace(":", "\\:")
        value = value.replace("'", "").replace('"', "")
        value = value.replace("\u2018", "").replace("\u2019", "")
        value = value.replace("\u201c", "").replace("\u201d", "")
        value = value.replace("%", "%%")
        return value

    bottom_margin = max(56, int(HEIGHT * 0.075))
    cue_start = max(0.0, float(start or 0.0))
    cue_end = min(float(duration), float(end if end is not None else duration))
    cue_end = max(cue_start + 0.12, cue_end)
    alpha = (
        f"alpha=min(max((t-{cue_start:.2f})/0.12\\,0)\\,1)"
        f"*min(max(({cue_end:.2f}-t)/0.16\\,0)\\,1)"
    )
    chains = []
    for index, line in enumerate(lines):
        # Line 0 sits on top; subsequent lines stack below it.
        y_pos = f"h-{bottom_margin + (len(lines) - index) * line_height}"
        chains.append(
            "drawtext="
            f"text='{_cap_esc(line)}'"
            ":fontfile='fonts/Poppins-Bold.ttf'"
            f":fontsize={fontsize}"
            ":fontcolor=white"
            ":x=(w-text_w)/2"
            f":y={y_pos}"
            f":{alpha}"
            f":enable='between(t\\,{cue_start:.3f}\\,{cue_end:.3f})'"
            ":box=1:boxcolor=black@0.55:boxborderw=14"
            ":shadowcolor=black@0.8:shadowx=2:shadowy=2"
        )
    return chains


def _timed_caption_chains(cues: Sequence[Mapping[str, Any]], duration: float) -> list:
    """Render only the grounded Whisper-selected phrases for a scene."""
    chains: list = []
    for cue in cues or ():
        if not isinstance(cue, Mapping):
            continue
        text = str(cue.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(cue.get("start", 0.0) or 0.0)
            end = float(cue.get("end", start + 0.4) or start + 0.4)
        except (TypeError, ValueError):
            continue
        chains.extend(_caption_chains(text, duration, start, end))
    return chains


def render_straight_media_ffmpeg(
    media_paths: Sequence[str],
    scene_durations: Sequence[float],
    audio_path: str,
    output_path: str | Path,
    *,
    subtitle_path: str | None = None,
    width: int = 1280,
    height: int = 720,
    fps: int = 30,
    budget_seconds: int = 85,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    cancellation=None,
    allow_emergency_media: bool | None = None,
) -> str:
    """One-pass delivery fallback used only after the semantic wall expires.

    Every resolved scene remains in order.  The fallback normalizes scenes once
    in a small bounded worker pool, then stream-copies them into the master,
    muxes narration, and attaches the subtitle track without retry loops.
    """
    global _ACTIVE_CANCELLATION, _ACTIVE_RENDER_DEADLINE
    global _ACTIVE_RENDER_BUDGET_SECONDS, _ACTIVE_FAST_RENDER, _ACTIVE_ENCODE_THREADS

    if allow_emergency_media is None:
        # Keep direct library callers compatible; the production app passes
        # its explicit ``False`` setting below.
        allow_emergency_media = emergency_media_allowed(None)
    paths = [str(path) for path in media_paths if path]
    if not paths or len(paths) != len(media_paths):
        raise ValueError("Straight render requires one media path per scene")
    if not allow_emergency_media:
        synthetic = [path for path in paths if is_synthetic_media(path)]
        if synthetic:
            raise ValueError(
                "Straight render rejected synthetic/emergency media: "
                + ", ".join(synthetic[:3])
            )
    if not audio_path or not Path(audio_path).is_file():
        raise ValueError("Straight render requires readable narration audio")

    durations = [max(0.1, float(value)) for value in scene_durations]
    if len(durations) < len(paths):
        fallback_duration = durations[-1] if durations else 3.0
        durations.extend([fallback_duration] * (len(paths) - len(durations)))
    durations = durations[:len(paths)]

    requested_w = max(320, int(width or 1280))
    requested_h = max(320, int(height or 720))
    # Recovery is a delivery safety net, not permission to silently reduce a
    # manual 1080p/30fps export.  Operators can explicitly lower this via the
    # environment for previews, while normal manual-source jobs preserve the
    # requested canvas and frame rate.
    try:
        recovery_long_edge = int(os.environ.get("VIDZEO_RECOVERY_RENDER_LONG_EDGE", "1920") or 1920)
    except (TypeError, ValueError):
        recovery_long_edge = 960
    recovery_long_edge = max(640, min(1920, recovery_long_edge))
    scale = min(1.0, float(recovery_long_edge) / max(requested_w, requested_h))
    out_w = max(320, int(requested_w * scale) // 2 * 2)
    out_h = max(320, int(requested_h * scale) // 2 * 2)
    out_fps = max(12, min(60, int(fps or 30)))
    wall = _positive_int(budget_seconds, 150, 60, 600)
    recovery_workers = max(1, min(4, (os.cpu_count() or 4) // 2))
    encode_threads = 1

    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f"{output.stem}.recovery.partial{output.suffix}")
    try:
        partial.unlink(missing_ok=True)
    except Exception:
        pass

    previous_cancellation = _ACTIVE_CANCELLATION
    previous_deadline = _ACTIVE_RENDER_DEADLINE
    previous_budget = _ACTIVE_RENDER_BUDGET_SECONDS
    previous_fast = _ACTIVE_FAST_RENDER
    previous_threads = _ACTIVE_ENCODE_THREADS
    _ACTIVE_CANCELLATION = cancellation
    _ACTIVE_RENDER_BUDGET_SECONDS = float(wall)
    _ACTIVE_RENDER_DEADLINE = time.monotonic() + float(wall)
    _ACTIVE_FAST_RENDER = True
    _ACTIVE_ENCODE_THREADS = encode_threads

    if progress_callback:
        progress_callback(0.02, f"Fast recovery render ({len(paths)} scenes)...")
    print(
        f"   [ffmpeg] straight recovery: {len(paths)} scenes, "
        f"{out_w}x{out_h}, budget={wall}s"
    )

    try:
        with tempfile.TemporaryDirectory(prefix="vidzeo_straight_") as temp_dir:
            segment_dir = Path(temp_dir) / "segments"
            segment_dir.mkdir(parents=True, exist_ok=True)
            video_exts = {".mp4", ".mov", ".avi", ".webm", ".mkv", ".flv", ".wmv"}

            def _render_recovery_scene(index: int, path: str, duration: float) -> str:
                segment = segment_dir / f"scene_{index:04d}.ts"
                if Path(path).suffix.lower() in video_exts:
                    input_args = ["-stream_loop", "-1", "-i", path]
                else:
                    # Web providers occasionally return AVIF/AV1 bytes while
                    # retaining a .jpg URL suffix. Pillow accepts those bytes,
                    # but FFmpeg's image2 demuxer rejects them (often with
                    # ``Option loop not found``) and the old path substituted
                    # a visually empty neutral plate. Normalize every still
                    # frame to an explicit PNG before invoking FFmpeg.
                    normalized = segment_dir / f"scene_{index:04d}.png"
                    try:
                        from PIL import Image
                        with Image.open(path) as image:
                            image.convert("RGB").save(normalized, format="PNG")
                        input_args = ["-loop", "1", "-framerate", str(out_fps), "-i", str(normalized)]
                    except Exception:
                        input_args = ["-loop", "1", "-framerate", str(out_fps), "-i", path]
                # Keep the complete object visible in the delivery reserve as
                # well.  The semantic path uses the same centered containment
                # contract; recovery must not silently revert stills to a
                # cover crop just because the main render hit its wall.
                if Path(path).suffix.lower() in video_exts:
                    visual_filter = (
                        f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase:"
                        f"flags=lanczos+accurate_rnd,crop={out_w}:{out_h},setsar=1"
                    )
                else:
                    visual_filter = _blurred_fit_filter(out_w, out_h)
                filter_complex = (
                    f"[0:v]fps={out_fps},{visual_filter},format=yuv420p[v]"
                )
                command = [
                    _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
                    *input_args, "-t", f"{duration:.3f}", "-filter_complex", filter_complex,
                    "-map", "[v]", "-an",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "25",
                    "-pix_fmt", "yuv420p", "-threads", str(encode_threads),
                    "-r", str(out_fps), "-g", str(out_fps),
                    "-f", "mpegts", str(segment),
                ]
                per_scene_timeout = max(30, min(120, int(duration * 10) + 25))
                result = _run_ffmpeg_process(
                    command,
                    capture_output=True,
                    timeout=per_scene_timeout,
                    budget_exempt=True,
                )
                if result.returncode != 0 or not segment.is_file() or segment.stat().st_size < 1024:
                    stderr = result.stderr.decode(errors="replace") if result.stderr else ""
                    print(
                        f"   [ffmpeg] recovery scene {index + 1} media open failed "
                        f"({stderr[:160]})"
                    )
                    # A corrupt or partially downloaded real asset is a decode
                    # failure, not a reason to abort the whole delivery. Use
                    # the neutral recovery plate for this scene so the job
                    # remains renderable and the manifest can record the
                    # failure. The synthetic-media policy above still rejects
                    # an explicitly supplied emergency asset before rendering.
                    print(
                        f"   [ffmpeg] recovery scene {index + 1}: using neutral "
                        "decode-failure plate"
                    )
                    plate_cmd = [
                        _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
                        "-f", "lavfi", "-i",
                        f"color=c=0x12121c:s={out_w}x{out_h}:r={out_fps}:d={duration:.3f}",
                        "-t", f"{duration:.3f}", "-vf",
                        f"fps={out_fps},setsar=1,format=yuv420p",
                        "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "25",
                        "-pix_fmt", "yuv420p", "-threads", str(encode_threads),
                        "-r", str(out_fps), "-g", str(out_fps),
                        "-f", "mpegts", str(segment),
                    ]
                    plate_result = _run_ffmpeg_process(
                        plate_cmd,
                        capture_output=True,
                        timeout=per_scene_timeout,
                        budget_exempt=True,
                    )
                    if (plate_result.returncode != 0 or not segment.is_file()
                            or segment.stat().st_size < 1024):
                        raise RuntimeError(f"Recovery scene {index + 1} failed: {stderr[:180]}")
                return str(segment)

            rendered: dict[int, str] = {}
            completed = 0
            with ThreadPoolExecutor(max_workers=recovery_workers) as executor:
                futures = {
                    executor.submit(_render_recovery_scene, i, path, duration): i
                    for i, (path, duration) in enumerate(zip(paths, durations))
                }
                for future in as_completed(futures):
                    index = futures[future]
                    rendered[index] = future.result()
                    completed += 1
                    if progress_callback:
                        progress_callback(
                            0.05 + 0.82 * completed / len(paths),
                            f"Fast recovery scene {completed}/{len(paths)}...",
                        )
            if len(rendered) != len(paths):
                raise RuntimeError(f"Straight recovery incomplete: {len(rendered)}/{len(paths)}")

            concat_list = Path(temp_dir) / "concat.txt"
            concat_list.write_text(
                "".join(f"file '{rendered[index]}'\n" for index in range(len(paths))),
                encoding="utf-8",
            )
            command = [
                _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
                "-f", "concat", "-safe", "0", "-i", str(concat_list),
                "-i", str(audio_path),
            ]
            subtitle_index = None
            if subtitle_path and Path(subtitle_path).is_file():
                subtitle_index = 2
                command.extend(["-i", str(subtitle_path)])
            command.extend(["-map", "0:v:0", "-map", "1:a:0"])
            if subtitle_index is not None:
                command.extend(["-map", f"{subtitle_index}:s:0?"])
            command.extend([
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-ar", "48000", "-ac", "2",
            ])
            if subtitle_index is not None:
                command.extend(["-c:s", "mov_text", "-disposition:s:0", "default"])
            # Keep the picture timeline authoritative.  ``-shortest`` used to
            # truncate a valid video whenever TTS ended a few frames early.
            # Padding/trimming is sample-accurate and does not time-stretch
            # or alter narration pitch.
            recovery_duration = max(0.1, sum(durations))
            command.extend([
                "-af", (
                    "apad,"
                    f"atrim=start=0:end={recovery_duration:.3f},"
                    "asetpts=PTS-STARTPTS"
                ),
                "-t", f"{recovery_duration:.3f}",
                "-movflags", "+faststart",
                "-max_muxing_queue_size", "4096", str(partial),
            ])
            result = _run_ffmpeg_process(
                command,
                capture_output=True,
                timeout=90,
                budget_exempt=True,
            )
            if result.returncode != 0 or not partial.is_file() or partial.stat().st_size < 4096:
                stderr = result.stderr.decode(errors="replace") if result.stderr else ""
                raise RuntimeError(f"Straight recovery render failed: {stderr[:300]}")
            os.replace(str(partial), str(output))
            if progress_callback:
                progress_callback(1.0, "Fast recovery render complete")
            return str(output)
    finally:
        try:
            partial.unlink(missing_ok=True)
        except Exception:
            pass
        _ACTIVE_CANCELLATION = previous_cancellation
        _ACTIVE_RENDER_DEADLINE = previous_deadline
        _ACTIVE_RENDER_BUDGET_SECONDS = previous_budget
        _ACTIVE_FAST_RENDER = previous_fast
        _ACTIVE_ENCODE_THREADS = previous_threads


def render_semantic_documentary_ffmpeg(
    manifest: Mapping[str, Any],
    output_path: str | Path,
    config: Optional[Mapping[str, Any]] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    cancellation=None,
) -> Dict[str, Any]:
    global WIDTH, HEIGHT, FPS
    global _ACTIVE_CANCELLATION, _ACTIVE_RENDER_DEADLINE
    global _ACTIVE_RENDER_BUDGET_SECONDS, _ACTIVE_FAST_RENDER, _ACTIVE_ENCODE_THREADS
    render_config = dict(config or {})
    budget_seconds = _resolve_semantic_render_budget(render_config)
    scene_count = len(list(manifest.get("scenes") or []))
    try:
        manifest_duration = float(manifest.get("duration") or 0.0)
    except (TypeError, ValueError):
        manifest_duration = 0.0
    # Long jobs can have fewer, longer scenes. Use duration as a second safe
    # signal so a three-to-five-minute timeline does not accidentally retain
    # every expensive companion re-encode merely because it has 30 scenes.
    explicit_job_type = str(
        render_config.get("job_type") or render_config.get("type") or ""
    ).strip().lower()
    long_form_default = (
        explicit_job_type in {"long", "long-form", "long_form"}
        or scene_count >= 40
        or manifest_duration >= 180.0
    )
    fast_render = _enabled(
        render_config.get("semantic_fast_render"),
        default=long_form_default,
    )
    try:
        from services.gpu_accelerator import detect_optimal_gpu_encoder
        _gpu_info = detect_optimal_gpu_encoder()
        _gpu_active = bool(_gpu_info.get("is_hardware_accelerated") or _gpu_info.get("accelerated", False))
    except Exception:
        _gpu_active = False

    if _gpu_active:
        cpu_default_workers = max(4, min(8, (os.cpu_count() or 4) - 2))
    else:
        cpu_default_workers = max(2, min(4, (os.cpu_count() or 4) // 2))

    raw_concurrency = render_config.get("semantic_render_concurrency") or os.environ.get("VIDZEO_SEMANTIC_RENDER_CONCURRENCY")
    if raw_concurrency is not None and str(raw_concurrency).strip().lower() in ("auto", "0", ""):
        raw_concurrency = None
    worker_count = _positive_int(
        raw_concurrency,
        cpu_default_workers,
        1,
        max(1, min(8, os.cpu_count() or 4)),
    )
    default_threads = max(1, min(4, (os.cpu_count() or 4) // max(1, worker_count)))
    encode_threads = _positive_int(
        render_config.get("semantic_render_threads_per_job")
        or os.environ.get("VIDZEO_SEMANTIC_RENDER_THREADS_PER_JOB", str(default_threads)),
        default_threads,
        1,
        max(1, os.cpu_count() or 4),
    )
    previous_cancellation = _ACTIVE_CANCELLATION
    previous_deadline = _ACTIVE_RENDER_DEADLINE
    previous_budget = _ACTIVE_RENDER_BUDGET_SECONDS
    previous_fast = _ACTIVE_FAST_RENDER
    previous_threads = _ACTIVE_ENCODE_THREADS
    previous_dimensions = (WIDTH, HEIGHT, FPS)
    _ACTIVE_CANCELLATION = cancellation
    _ACTIVE_RENDER_BUDGET_SECONDS = float(budget_seconds)
    _ACTIVE_RENDER_DEADLINE = time.monotonic() + float(budget_seconds)
    _ACTIVE_FAST_RENDER = bool(fast_render)
    _ACTIVE_ENCODE_THREADS = int(encode_threads)
    render_config["semantic_render_concurrency"] = worker_count
    render_config["semantic_fast_render"] = bool(fast_render)
    print(
        f"   [ffmpeg] render budget: {budget_seconds}s, workers={worker_count}, "
        f"threads/job={encode_threads}, fast_mode={bool(fast_render)}"
    )
    try:
        return _render_semantic_documentary_ffmpeg_impl(
            manifest=manifest,
            output_path=output_path,
            config=render_config,
            progress_callback=progress_callback,
            cancellation=cancellation,
        )
    finally:
        _ACTIVE_CANCELLATION = previous_cancellation
        _ACTIVE_RENDER_DEADLINE = previous_deadline
        _ACTIVE_RENDER_BUDGET_SECONDS = previous_budget
        _ACTIVE_FAST_RENDER = previous_fast
        _ACTIVE_ENCODE_THREADS = previous_threads
        # A render may target portrait/preview dimensions or a custom frame
        # rate. Restore the module defaults after the job so the next render
        # cannot inherit another job's geometry or cadence.
        WIDTH, HEIGHT, FPS = previous_dimensions


def _render_semantic_documentary_ffmpeg_impl(
    manifest: Mapping[str, Any],
    output_path: str | Path,
    config: Optional[Mapping[str, Any]] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    cancellation=None,
) -> Dict[str, Any]:
    """Render a semantic documentary manifest using FFmpeg.

    Args:
        manifest: Semantic manifest from build_semantic_edit_manifest()
        output_path: Output MP4 path
        config: Optional config dict (same keys as Remotion renderer)
        progress_callback: Optional progress callback

    Returns:
        Dict with output_path, render_seconds, quality, etc.
    """
    global WIDTH, HEIGHT, FPS
    config = dict(config or {})
    allow_emergency_media = emergency_media_allowed(config)
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if cancellation is not None:
        cancellation.raise_if_cancelled()

    # Native FFmpeg must honor the manifest contract. The old renderer kept a
    # module-wide 1920x1080/30 default even for portrait and editor exports.
    try:
        WIDTH = max(320, min(7680, int(manifest.get("width") or config.get("width") or WIDTH)))
        HEIGHT = max(320, min(7680, int(manifest.get("height") or config.get("height") or HEIGHT)))
        FPS = max(1, min(120, int(manifest.get("fps") or config.get("fps") or FPS)))
    except (TypeError, ValueError):
        pass

    # Apply video-specific editing profile palette + typography
    editing_profile = config.get("editing_profile") or manifest.get("editing_profile") or {}
    visual_dna = config.get("visual_dna") or manifest.get("visual_dna") or {}
    _apply_palette(editing_profile, visual_dna=visual_dna)

    scenes = list(manifest.get("scenes") or [])
    audio_info = manifest.get("audio") or {}
    theme = manifest.get("theme") or {}
    fps = FPS

    try:
        manifest_dump_path = output.with_suffix(".manifest.json")
        with open(manifest_dump_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, default=str)
    except Exception:
        pass

    if not scenes:
        raise ValueError("Semantic manifest has no scenes")

    scene_frame_contracts = _scene_frame_contracts(scenes)
    scene_render_durations = [frames / max(1, fps) for frames in scene_frame_contracts]
    expected_picture_duration = sum(scene_frame_contracts) / max(1, fps)
    planned_scene_duration = sum(
        max(0.0, float(scene.get("duration", 0.0) or 0.0)) for scene in scenes
    )
    print(
        "   [ffmpeg] timeline contract: "
        f"planned={planned_scene_duration:.3f}s, "
        f"picture={expected_picture_duration:.3f}s/{sum(scene_frame_contracts)} frames"
    )

    total_duration = float(manifest.get("duration", 0))
    voiceover = audio_info.get("voiceover") or {}
    music = audio_info.get("music") or {}
    sound_cues_raw = list(manifest.get("audio", {}).get("effects") or [])
    sound_cues = [cue for cue in sound_cues_raw if isinstance(cue, dict)]

    voiceover_path = voiceover.get("path") or voiceover.get("src") or ""
    music_path = music.get("path") or music.get("src") or ""
    music_volume = float(music.get("volume", 0.2))

    quality = str(
        config.get("semantic_render_quality")
        or config.get("quality")
        or "standard"
    )

    with tempfile.TemporaryDirectory(prefix="vidzeo_ffmpeg_") as tmp:
        temp_dir = tmp
        segment_dir = Path(temp_dir) / "segments"
        segment_dir.mkdir(parents=True, exist_ok=True)

        # Copy fonts to temp dir so VOX drawtext chains can find them
        # (FFmpeg drawtext resolves fontfile relative to CWD, not the manifest)
        _fonts_src = Path(__file__).resolve().parents[2] / "fonts"
        _fonts_dst = Path(temp_dir) / "fonts"
        if _fonts_src.exists():
            _fonts_dst.mkdir(exist_ok=True)
            for _f in _fonts_src.glob("*.ttf"):
                shutil.copy2(str(_f), str(_fonts_dst / _f.name))

        # Keep the scene index alongside the path. Decorative/composite passes
        # add numeric suffixes to filenames, so parsing the last filename token
        # can silently scramble otherwise valid parallel render results.
        indexed_segments: list[tuple[int, str]] = []

        n_scenes = len(scenes)
        started = time.monotonic()
        fast_render_mode = bool(config.get("semantic_fast_render"))
        # Atmosphere, image-treatment and overlay passes are intentionally
        # separate encodes.  On a busy CPU they can consume the entire wall
        # budget after media retrieval, even though the actual editorial
        # composition (motion, text cards, captions and audio) is healthy.
        # Switch only those expensive companion passes off when the remaining
        # wall is already tight; the core semantic templates stay enabled.
        remaining_at_scene_start = _remaining_render_budget()
        budget_fast_threshold = max(240.0, float(n_scenes) * 14.0)
        budget_forced_fast = bool(
            n_scenes >= 20
            and remaining_at_scene_start is not None
            and remaining_at_scene_start < budget_fast_threshold
        )
        skip_decorative_passes = _should_skip_decorative_passes(
            fast_render_mode=fast_render_mode,
            n_scenes=n_scenes,
            planned_scene_duration=planned_scene_duration,
            budget_forced_fast=budget_forced_fast,
        )
        if budget_forced_fast and not fast_render_mode:
            # Activate the encoder profile as well as skipping companions.
            # Previously the log claimed fast mode while core scene encodes
            # remained ``veryfast``/CRF17 and exhausted the wall anyway.
            _ACTIVE_FAST_RENDER = True
            print(
                "   [ffmpeg] render budget tight: preserving semantic motion/text "
                "while skipping expensive companion re-encodes"
            )
        if skip_decorative_passes:
            print(
                "   [ffmpeg] long-form fast mode: preserving motion/text overlays; "
                "folding companion overlays and skipping redundant decorative re-encodes"
            )

        def _render_neutral_scene(scene_idx: int, duration: float) -> Optional[str]:
            """Create a deterministic neutral plate for a damaged scene.

            A single corrupt image/clip must not make an otherwise complete
            documentary fail after all other scenes have rendered.  The plate
            keeps the timeline/frame contract intact and lets the native
            delivery path finish; the media audit still records the original
            scene error in the server log.
            """
            plate = segment_dir / f"neutral_{scene_idx:04d}.png"
            output = segment_dir / f"neutral_{scene_idx:04d}.ts"
            try:
                if not plate.exists():
                    canvas = Image.new("RGB", (WIDTH, HEIGHT), (16, 20, 28))
                    draw = ImageDraw.Draw(canvas)
                    # A restrained editorial accent makes the fallback
                    # intentional rather than an obvious black flash.
                    draw.rectangle((0, HEIGHT - 8, WIDTH, HEIGHT), fill=(226, 184, 72))
                    canvas.save(plate, format="PNG", optimize=False)
                ok = _render_base_scene(
                    str(plate), "image", "static", duration,
                    str(output), str(segment_dir),
                )
                if not ok:
                    return None
                return _ensure_scene_segment_duration(
                    str(output), duration, str(segment_dir / f"neutral_exact_{scene_idx:04d}.ts")
                )
            except SemanticRenderBudgetExceeded:
                raise
            except Exception as exc:
                print(f"   [scene] {scene_idx + 1}: neutral fallback failed: {exc}")
                return None

        # -------------------------------------------------------------------
        # Phase 1: Render each scene (parallel)
        # -------------------------------------------------------------------
        print(f"   [ffmpeg] rendering {n_scenes} scenes (parallel)...")

        def _render_one_scene(scene_idx: int, scene: dict) -> Optional[str]:
            """Render a single scene and return segment path, or None on failure."""
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            # Use the cumulative frame contract rather than independently
            # rounding each duration. This makes 77 parallel outputs add up to
            # the narration timeline exactly (within one final CFR frame).
            duration = scene_render_durations[scene_idx]
            media = scene.get("media") or {}
            # Scene "start" is a timeline offset, not an offset into the clip.
            # Only seek into the clip when the media contract explicitly
            # provides a clip start; otherwise start at the clip's beginning.
            _raw_st = media.get("render_clip_start")
            if _raw_st is None:
                _raw_st = media.get("clip_start", 0)
            st = float(_raw_st or 0.0)
            # Prefer the local contract field. ``src`` may be /temp or /output
            # for the browser preview and is not a filesystem path for FFmpeg.
            media_src = media.get("path") or scene.get("media_src") or media.get("src") or ""
            # Production media contracts can contain workspace-relative paths,
            # or web-root URLs ("/temp/...", "/output/...") for the browser
            # preview. FFmpeg runs from a temporary fonts directory, so these
            # must be normalized to real filesystem paths before invoking it.
            # A leading-slash web path would otherwise resolve against the
            # filesystem root (C:\temp\...) and silently miss the asset.
            if media_src:
                _ms = str(media_src)
                _candidate = Path(_ms)
                # On Windows, Path('/temp/x').is_absolute() is False (no drive)
                # but joining it onto PROJECT_ROOT would still reset the anchor
                # and produce C:\temp\... instead of the project temp dir. Treat
                # any path starting with a temp/output/cache/fonts segment as
                # web-root-relative to the project, not cwd-relative.
                _web_root_dirs = ("temp", "output", "cache", "fonts", "render_media")
                _norm = _ms.replace("\\", "/")
                _first_seg = _norm.lstrip("/").split("/", 1)[0].lower()
                if _first_seg in _web_root_dirs:
                    _candidate = Path(os.path.normpath(PROJECT_ROOT / _norm.lstrip("/")))
                elif not _candidate.is_absolute():
                    _candidate = Path(os.path.normpath(PROJECT_ROOT / _ms))
                    if not _candidate.exists():
                        _cwd_resolved = Path(_ms).resolve()
                        if _cwd_resolved.exists():
                            _candidate = _cwd_resolved
                media_src = str(_candidate)
            media_type = media.get("type") or "image"
            media_type_normalized = str(media_type or "image").strip().lower()
            requested_fit = str(media.get("fit") or "").strip().lower()
            safe_framing = bool(
                media.get("safe_framing")
                or (
                    media_type_normalized not in {"video", "video/mp4", "video/webm"}
                    and requested_fit in {"contain", "fit", "safe", "center", "letterbox"}
                )
            )
            camera = scene.get("camera") or {}
            motion = camera.get("motion", "static")
            graphic = scene.get("graphic") or {}
            template_id = graphic.get("template_id", "full-bleed")
            props = graphic.get("props", {})
            graphic_local_start, graphic_local_end = _scene_graphic_interval(scene)
            graphic_duration = max(0.001, graphic_local_end - graphic_local_start)

            # --- VOX template: build FFmpeg drawtext/drawbox filter chain ---
            vox_vf = ""
            vox_template_id = ""
            vox_props = {}
            try:
                from services.semantic_edit_engine import (
                    _native_props_for_template,
                    _native_template_id,
                )
                vox_template_id = _native_template_id(str(template_id))
                if vox_template_id:
                    vox_props = _native_props_for_template(
                        vox_template_id,
                        props,
                        scene.get("semantic_beat") or {},
                        scene.get("narration") or "",
                        manifest.get("topic") or "",
                    )
                    if isinstance(props.get("_word_cues"), list):
                        # Remotion receives cue-relative times inside a nested
                        # Sequence; FFmpeg receives a scene-local filter graph.
                        # Shift only the native copy so both renderers land on
                        # the same Whisper word boundaries.
                        native_words = []
                        for row in props.get("_word_cues") or []:
                            if not isinstance(row, dict):
                                continue
                            native_row = dict(row)
                            try:
                                native_row["start"] = round(float(row.get("start", 0.0)) + graphic_local_start, 3)
                                native_row["end"] = round(float(row.get("end", 0.0)) + graphic_local_start, 3)
                            except (TypeError, ValueError):
                                continue
                            native_words.append(native_row)
                        if native_words:
                            vox_props["_word_cues"] = native_words
                    # Keep the display contract profile-aware without making
                    # every template know about the renderer's global font map.
                    # Pass font filenames, not absolute Windows paths. The
                    # resolver below maps these names to temp_dir/fonts and
                    # avoids drive-letter colons inside FFmpeg filters.
                    vox_props.setdefault("_font", Path(_DISPLAY_FONT_OVERRIDE).name)
                    vox_props.setdefault("_body_font", Path(_BODY_FONT_OVERRIDE).name)
                    # Professional templates need the actual canvas dimensions
                    # to keep text-card geometry inside the frame on both
                    # landscape and portrait exports.
                    vox_props.setdefault("_render_width", WIDTH)
                    vox_props.setdefault("_render_height", HEIGHT)
            except Exception:
                vox_template_id = ""
            if not vox_template_id and str(template_id).strip().lower() != "full-bleed":
                vox_template_id = graphic.get("vox_template") or ""
                vox_props = graphic.get("vox_props") or props or {}
            if vox_template_id and vox_template_id in VOX_TEMPLATES:
                try:
                    vox_palette = (scene.get("palette") or graphic.get("palette") or theme.get("palette") or editing_profile.get("palette") or
                                   {"primary": "#f4f0e6", "accent": "#f2c14e", "danger": "#ef6f6c",
                                    "background": "#0b1320", "surface": "#16263b"})
                    tmpl_result = VOX_TEMPLATES[vox_template_id](
                        vox_palette,
                        vox_props,
                        scene_duration=graphic_duration,
                    )
                    chains = tmpl_result.get("chains") or []
                    # The template catalog owns layout; the semantic contract
                    # owns whether this particular card should carry a plate.
                    # Apply it before timeline gating so preview and native
                    # export share the same no-background/background decision.
                    chains = _apply_text_card_mode(
                        chains,
                        str(vox_props.get("_card_mode") or props.get("_card_mode") or "surface"),
                        vox_palette,
                    )
                    if chains:
                        # Resolve font paths: replace fontfile='Name.ttf' with absolute path
                        # FFmpeg's drawtext resolves fontfile relative to CWD, not the workspace.
                        # We copied fonts to temp_dir/fonts/ above.
                        _fonts_dir = Path(temp_dir) / "fonts"
                        _resolved_chains = []
                        for _chain in chains:
                            if _chain and "fontfile='" in _chain:
                                # Extract font filename and resolve
                                import re as _re_font
                                _m = _re_font.search(r"fontfile='([^']+\.ttf)'", _chain)
                                if _m:
                                    _fname = _m.group(1)
                                    # If just a filename (no path), resolve to temp_dir/fonts
                                    if "\\" not in _fname and "/" not in _fname:
                                        # Keep this path filter-safe on Windows. The
                                        # subprocess runs from temp_dir, so this resolves
                                        # to temp_dir/fonts/<filename>.ttf without a drive
                                        # colon or backslashes in the filter expression.
                                        _resolved_path = f"fonts/{_fname}"
                                        _chain = _chain.replace(
                                            f"fontfile='{_fname}'",
                                            f"fontfile='{_resolved_path}'"
                                        )
                            _resolved_chains.append(
                                _gate_timeline_filter(
                                    _chain,
                                    graphic_local_start,
                                    graphic_local_end,
                                )
                            )
                        # format=yuv420p is required before drawtext for color compatibility
                        vox_vf = "format=yuv420p," + ",".join(c for c in _resolved_chains if c)
                except Exception as e:
                    print(f"   [vox] scene {scene_idx + 1}: VOX template '{vox_template_id}' failed: {e}")
                    vox_vf = ""

            # Large documentaries use stream-copy concat for stable duration.
            # Apply the selected transition as a clean intro effect on the
            # incoming scene so transitions are still visible without a giant
            # O(n^2) xfade graph.
            if scene_idx > 0:
                transition = scene.get("transition") or {}
                transition_id = str(transition.get("template_id") or "cut")
                transition_duration = max(0.08, min(0.5, float(transition.get("duration") or 0.3)))
                if transition_id != "cut":
                    transition_color = "white" if transition_id == "dip_white" else "black"
                    transition_vf = f"fade=t=in:st=0:d={transition_duration:.3f}:color={transition_color}"
                    vox_vf = f"{vox_vf},{transition_vf}" if vox_vf else transition_vf

            # This compatibility renderer never draws captions. Visible
            # captions are a Remotion composition layer, so a fallback render
            # cannot silently switch font, timing, or style to FFmpeg filters.

            if (
                is_synthetic_media(
                    media_src,
                    media.get("source_label")
                    or media.get("label")
                    or media.get("provider")
                    or scene.get("source_label")
                    or "",
                )
                and not allow_emergency_media
            ):
                raise RuntimeError(
                    f"Scene {scene_idx + 1} contains synthetic/emergency media; "
                    "real media is required for production rendering"
                )
            if not media_src or not Path(media_src).is_file():
                if not allow_emergency_media:
                    raise RuntimeError(
                        f"Scene {scene_idx + 1} has no renderable media asset"
                    )
                placeholder = Path(temp_dir) / f"placeholder_{scene_idx}.png"
                if not placeholder.exists():
                    Image.new("RGB", (WIDTH, HEIGHT), (*_bg(),)).save(str(placeholder), format="PNG")
                media_src = str(placeholder)

            ext = Path(media_src).suffix.lower()
            if ext in (".mp4", ".mov", ".webm", ".avi", ".mkv"):
                media_type = "video"

            # Native drawtext chains are attractive when they work, but a
            # malformed chain must never turn an edited video scene into raw
            # footage.  Use the resilient RGBA-card compositor for video
            # sources by default.  It has an explicit, short eased in/out
            # motion pass in ``_composite_overlay`` and is independent of
            # FFmpeg drawtext/font parsing.  Still-image scenes retain the
            # VOX path unless the caller opts into the same reliable mode.
            reliable_overlay_mode = bool(config.get("reliable_overlay_mode", True))
            force_raster_graphics = bool(config.get("force_raster_graphics"))
            is_video_source = str(media_type or "").lower().startswith("video")
            if str(template_id).strip().lower() != "full-bleed" and (
                force_raster_graphics or (reliable_overlay_mode and is_video_source)
            ):
                if vox_vf:
                    print(
                        f"   [graphics] scene {scene_idx + 1}: using resilient raster card "
                        "for video source"
                    )
                vox_vf = ""

            # Generate overlay — check for Universal Local Scene System first
            overlay_path = None
            overlay_paths = []
            pv = scene.get("primary_visual") or {}
            pv_type = str(pv.get("type") or "").strip()
            pv_variant = str(pv.get("variant") or "").strip()
            is_visual_graphic_system = bool(
                pv_type
                and pv_type.lower() not in ("smartimage", "smartvideo", "fullbleed", "full-bleed", "none")
            )

            if is_visual_graphic_system:
                try:
                    from services.local_scene_systems import render_scene_overlay
                    merged_pv_props = {
                        **dict(props or {}),
                        **dict(pv.get("props") or {}),
                        "variant": pv_variant,
                        "style": pv.get("props", {}).get("style") or props.get("style") or pv_variant or "vox-highlighter",
                        "highlight_words": pv.get("props", {}).get("highlight_words") or props.get("highlight_words") or [],
                        "position": pv.get("props", {}).get("position") or props.get("position") or props.get("_position") or "bottom-left",
                        "kicker": pv.get("props", {}).get("kicker") or props.get("kicker") or props.get("label") or "KEY DETAIL",
                        "badge": pv.get("props", {}).get("badge") or props.get("badge") or "",
                        "title": pv.get("props", {}).get("title") or props.get("title") or props.get("headline") or manifest.get("topic") or "",
                        "subtitle": pv.get("props", {}).get("subtitle") or props.get("subtitle") or props.get("detail") or "",
                        "stat": pv.get("props", {}).get("stat") or props.get("number") or props.get("stat") or "",
                        "quote": pv.get("props", {}).get("quote") or props.get("text") or scene.get("narration") or "",
                    }
                    pv_img = render_scene_overlay(
                        system_type=pv_type,
                        visual_dna=manifest.get("visual_dna") or {},
                        props=merged_pv_props,
                        width=WIDTH,
                        height=HEIGHT,
                    )
                    if pv_img:
                        pv_overlay_file = str(segment_dir / f"overlay_pv_{scene_idx:04d}.png")
                        pv_img.save(pv_overlay_file, format="PNG", optimize=False)
                        overlay_path = pv_overlay_file
                        # Do not bake conflicting raw drawtext on top of this high-res overlay
                        vox_vf = ""
                except Exception as pv_err:
                    print(f"   [local_systems] scene {scene_idx + 1}: primary visual overlay failed: {pv_err}")

            if not overlay_path and template_id != "full-bleed" and not vox_vf:
                # Production fallback: generate static Pillow overlay directly
                overlay_file = str(segment_dir / f"overlay_{scene_idx:04d}.png")
                generated = generate_overlay_to_file(template_id, props, overlay_file)
                if generated:
                    overlay_path = generated

            # Semantic companion overlays (lower thirds, callouts, etc.) are
            # native Pillow compositions too. They must not be confused with
            # atmosphere overlays, which are handled in a separate FFmpeg pass.
            for overlay_index, overlay in enumerate(scene.get("overlays") or []):
                if not isinstance(overlay, dict):
                    continue
                overlay_id = str(overlay.get("template_id") or overlay.get("id") or "").strip()
                if not overlay_id or overlay_id == "full-bleed":
                    continue
                overlay_file = str(segment_dir / f"overlay_{scene_idx:04d}_{overlay_index:02d}.png")
                generated = generate_overlay_to_file(
                    overlay_id,
                    dict(overlay.get("props") or {}),
                    overlay_file,
                )
                if generated:
                    try:
                        overlay_start = float(
                            overlay.get("scene_local_start")
                            if overlay.get("scene_local_start") is not None
                            else graphic_local_start
                        )
                        if overlay.get("scene_local_end") is not None:
                            overlay_end = float(overlay.get("scene_local_end"))
                        elif overlay.get("duration") is not None:
                            overlay_end = overlay_start + float(overlay.get("duration"))
                        else:
                            overlay_end = graphic_local_end
                    except (TypeError, ValueError):
                        overlay_start, overlay_end = graphic_local_start, graphic_local_end
                    overlay_paths.append((generated, overlay_start, overlay_end))

            # Render one or more real-media beats. The shot list is produced
            # upstream from accepted candidates only; this renderer never
            # invents a replacement asset. VOX/caption filters are applied
            # after shot concatenation so their scene-local timing remains
            # aligned with the parent narration scene.
            focal_bbox = media.get("subject_bbox") or media.get("focal_bbox")
            shot_specs = [
                shot for shot in (media.get("shots") or [])
                if isinstance(shot, dict) and (shot.get("path") or shot.get("asset_path"))
            ]

            def _normalize_media_source(raw_source: Any) -> str:
                if not raw_source:
                    return ""
                raw = str(raw_source)
                candidate = Path(raw)
                web_dirs = ("temp", "output", "cache", "fonts", "render_media")
                normalized = raw.replace("\\", "/")
                first_segment = normalized.lstrip("/").split("/", 1)[0].lower()
                if first_segment in web_dirs:
                    candidate = Path(os.path.normpath(PROJECT_ROOT / normalized.lstrip("/")))
                elif not candidate.is_absolute():
                    candidate = Path(os.path.normpath(PROJECT_ROOT / raw))
                    if not candidate.exists():
                        cwd_resolved = Path(raw).resolve()
                        if cwd_resolved.exists():
                            candidate = cwd_resolved
                return str(candidate)

            def _shot_float(value: Any, default: float = 0.0) -> float:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return default

            def _shot_motion_for_phase(base_motion: str, phase: int, shot: Mapping[str, Any]) -> str:
                """Vary camera treatment for repeated or adjacent still shots.

                Media shot planning may split one verified still into short
                motion segments to keep an intro paced. Reusing the exact
                same camera move makes those segments read as duplicate
                slides, so rotate through compatible pan/zoom/drift moves.
                The source path and verification stay unchanged.
                """
                requested = str(shot.get("motion") or "").strip().lower()
                if requested:
                    return requested
                base = str(base_motion or "zoom_in_slight").strip().lower()
                families = {
                    "zoom": ["zoom_in_slight", "zoom_out_slight", "drift_left", "drift_right"],
                    "pan": ["pan_left", "pan_right", "pan_diagonal_tr", "pan_diagonal_bl"],
                    "drift": ["drift_left", "drift_right", "zoom_in_slight", "zoom_out_slight"],
                    "rotate": ["rotate_gentle", "drift_left", "pan_right", "settle"],
                    "settle": ["settle", "drift_right", "zoom_out_slight", "pan_left"],
                }
                family = "zoom"
                for key in families:
                    if key in base:
                        family = key
                        break
                options = families[family]
                # For an actual multi-shot plan, only offset a repeated source;
                # distinct candidates keep the scene's selected motion unless
                # the neighboring phase would otherwise be identical.
                reuse_kind = str(shot.get("reuse_kind") or "").strip().lower()
                offset = int(phase or 0) if "motion" in reuse_kind or "still" in reuse_kind else 0
                return options[(options.index(base) + offset) % len(options)] if base in options else options[offset % len(options)]

            def _render_base_with_fallback(
                source: str,
                source_type: str,
                local_duration: float,
                output_path: str,
                seek: float,
                extra_filter: str,
                bbox: Any,
                safe_framing: bool = False,
                motion_override: str = "",
            ) -> bool:
                if not source or not Path(source).is_file():
                    return False
                is_source_video = (
                    str(source_type or "").strip().lower() in {"video", "video/mp4", "video/webm"}
                    or Path(source).suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
                )
                effective_motion = motion_override or motion
                if _render_base_scene(
                    source,
                    source_type,
                    effective_motion,
                    local_duration,
                    output_path,
                    temp_dir,
                    seek,
                    extra_vf=extra_filter,
                    focal_bbox=bbox,
                    safe_framing=safe_framing,
                ):
                    return True
                # Rendering recovery may simplify the filter, but it must keep
                # the same accepted source and timestamp. This is a codec
                # recovery tier, not a relevance/media fallback.
                target_aspect = float(WIDTH) / float(HEIGHT) if HEIGHT > 0 else 16.0 / 9.0
                source_aspect = _media_source_aspect(source, is_video=is_source_video)
                narrow_source = bool(
                    source_aspect is not None and source_aspect < (target_aspect * 0.96)
                )
                img_aspect = source_aspect
                fallback_fy = 0.25 if (img_aspect and img_aspect < (target_aspect * 0.85)) else 0.5
                simple_vf = (
                    _safe_animated_fit_filter(
                        effective_motion,
                        local_duration,
                        WIDTH,
                        HEIGHT,
                        force_blur=narrow_source,
                    )
                    if (safe_framing or narrow_source)
                    else _crop_fill_filter(WIDTH, HEIGHT, fy=fallback_fy)
                )
                if extra_filter:
                    simple_vf = f"{simple_vf},{extra_filter}"
                ffmpeg = _ffmpeg()
                if is_source_video:
                    cmd = [
                        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
                        "-err_detect", "ignore_err", "-fflags", "+discardcorrupt+genpts",
                        "-ss", f"{max(0.0, float(seek or 0.0)):.3f}", "-i", source,
                        "-vf", _exact_scene_filter(f"fps={FPS},{simple_vf}", local_duration),
                        "-frames:v", str(_scene_target_frames(local_duration)),
                        *_video_encoder_args(), "-threads", _encode_threads(), output_path,
                    ]
                else:
                    image_path = _get_ffmpeg_img_path(source, temp_dir)
                    cmd = [
                        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
                        "-f", "image2", "-loop", "1", "-framerate", str(FPS), "-i", image_path,
                        "-vf", _exact_scene_filter(simple_vf, local_duration),
                        "-frames:v", str(_scene_target_frames(local_duration)),
                        *_video_encoder_args(), "-threads", _encode_threads(), output_path,
                    ]
                proc = _run_ffmpeg_process(cmd, capture_output=True, timeout=180, cwd=temp_dir)
                if Path(output_path).is_file() and Path(output_path).stat().st_size >= 4096:
                    return True
                if proc.returncode != 0:
                    print(f"   [scene] _render_base_with_fallback ffmpeg failed (rc={proc.returncode}): {(proc.stderr or '').strip()[:180]}")
                if is_source_video:
                    # A damaged/very short accepted cut may not be encodable
                    # as a moving segment, but a frame from that same
                    # timestamp is still truthful real media.  This is the
                    # renderer's last codec recovery tier; it never changes
                    # the candidate, entity, or source to an unrelated asset.
                    frame_path = Path(output_path).with_suffix(".source_frame.jpg")
                    frame_result = _run_ffmpeg_process([
                        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
                        "-ss", f"{max(0.0, float(seek or 0.0)):.3f}", "-i", source,
                        "-frames:v", "1", "-q:v", "2", str(frame_path),
                    ], capture_output=True, timeout=60, cwd=temp_dir)
                    if frame_result.returncode == 0 and frame_path.is_file() and frame_path.stat().st_size >= 1024:
                        return bool(_render_base_scene(
                            str(frame_path), "image", effective_motion, local_duration,
                            output_path, temp_dir, 0.0, extra_filter, bbox,
                            safe_framing=safe_framing,
                        ))
                return False

            if len(shot_specs) > 1:
                shot_segments: list[str] = []
                shot_durations: list[float] = []
                for shot_index, shot in enumerate(shot_specs):
                    shot_start = max(0.0, _shot_float(shot.get("shot_start"), 0.0))
                    shot_end = min(duration, _shot_float(shot.get("shot_end"), duration))
                    shot_duration = max(0.05, shot_end - shot_start)
                    shot_source = _normalize_media_source(shot.get("path") or shot.get("asset_path"))
                    shot_type = str(shot.get("media_type") or shot.get("type") or "image")
                    if is_synthetic_media(shot_source, shot.get("source") or media.get("source") or "") and not allow_emergency_media:
                        raise RuntimeError(f"Scene {scene_idx + 1} shot {shot_index + 1} contains synthetic media")
                    shot_path = str(segment_dir / f"shot_{scene_idx:04d}_{shot_index:02d}.ts")
                    _raw_shot_seek = shot.get("render_clip_start")
                    if _raw_shot_seek is None:
                        _raw_shot_seek = shot.get("clip_start")
                    shot_seek = _shot_float(_raw_shot_seek, 0.0)
                    shot_motion = _shot_motion_for_phase(motion, shot_index, shot)
                    if not _render_base_with_fallback(
                        shot_source,
                        shot_type,
                        shot_duration,
                        shot_path,
                        shot_seek,
                        "",
                        shot.get("subject_bbox") or focal_bbox,
                        bool(shot.get("safe_framing", safe_framing)),
                        shot_motion,
                    ):
                        print(
                            f"   [scene] {scene_idx + 1}: shot {shot_index + 1} render failed "
                            f"(source='{shot_source}', exists={bool(shot_source and Path(shot_source).is_file())})"
                        )
                        if allow_emergency_media:
                            emergency_fallback = _render_neutral_scene(scene_idx, duration)
                            if emergency_fallback:
                                return emergency_fallback
                        return None
                    exact_shot = _ensure_scene_segment_duration(
                        shot_path,
                        shot_duration,
                        str(segment_dir / f"shot_exact_{scene_idx:04d}_{shot_index:02d}.ts"),
                    )
                    if not exact_shot:
                        return None
                    shot_segments.append(exact_shot)
                    shot_durations.append(shot_duration)
                shot_list_path = Path(segment_dir) / f"shots_{scene_idx:04d}.txt"
                def _concat_line(path: str) -> str:
                    # FFmpeg's concat demuxer expects POSIX separators even on
                    # Windows and uses the shell-style ''' sequence for an
                    # embedded apostrophe.
                    normalized = str(path).replace("\\", "/").replace("'", "'\\''")
                    return f"file '{normalized}'\n"

                shot_list_path.write_text(
                    "".join(_concat_line(path) for path in shot_segments),
                    encoding="utf-8",
                )
                base_segment = str(segment_dir / f"base_{scene_idx:04d}.ts")
                concat_result = _run_ffmpeg_process([
                    _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
                    "-f", "concat", "-safe", "0", "-i", str(shot_list_path),
                    "-c", "copy", base_segment,
                ], capture_output=True, timeout=180, cwd=temp_dir)
                if concat_result.returncode != 0 or not Path(base_segment).is_file():
                    return None
                exact_concat = str(segment_dir / f"base_exact_{scene_idx:04d}.ts")
                normalized_concat = _ensure_scene_segment_duration(
                    base_segment,
                    duration,
                    exact_concat,
                )
                if not normalized_concat:
                    # A stream-copy concat can expose a short/ambiguous MPEG-TS
                    # duration even though its frames are valid. Keep the
                    # multi-shot core alive and let the final scene contract
                    # repair it; dropping the whole semantic scene here used
                    # to trigger the one-pass renderer and silently discard
                    # every planned shot.
                    if _probe_duration_seconds(base_segment) > 0:
                        print(
                            f"   [scene] {scene_idx + 1}: concat duration metadata was short; "
                            "retaining verified shot concat for final normalization"
                        )
                        normalized_concat = base_segment
                    else:
                        print(f"   [scene] {scene_idx + 1}: shot concat produced no readable video")
                        return None
                base_segment = normalized_concat
                # Preserve the verified multi-shot core. Decorative or text
                # filters are optional presentation layers and must not erase
                # the real-media shot plan when an edge-case filter fails.
                core_segment = base_segment
                if vox_vf:
                    filtered_segment = str(segment_dir / f"base_filtered_{scene_idx:04d}.ts")
                    filtered_result = _run_ffmpeg_process([
                        _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
                        "-i", base_segment,
                        "-vf", _exact_scene_filter(vox_vf, duration),
                        "-frames:v", str(_scene_target_frames(duration)),
                        *_video_encoder_args(), "-threads", _encode_threads(), filtered_segment,
                    ], capture_output=True, timeout=180, cwd=temp_dir)
                    if filtered_result.returncode != 0 or not Path(filtered_segment).is_file():
                        print(
                            f"   [scene] {scene_idx + 1}: shot text filter failed; "
                            "retaining the verified multi-shot core"
                        )
                    else:
                        base_segment = filtered_segment
            else:
                base_segment = str(segment_dir / f"base_{scene_idx:04d}.ts")
                if shot_specs and len(shot_specs) == 1:
                    single_source = _normalize_media_source(shot_specs[0].get("path") or shot_specs[0].get("asset_path")) or media_src
                    single_type = str(shot_specs[0].get("media_type") or shot_specs[0].get("type") or media_type)
                    _raw_single_seek = shot_specs[0].get("render_clip_start")
                    if _raw_single_seek is None:
                        _raw_single_seek = shot_specs[0].get("clip_start")
                    single_seek = _shot_float(_raw_single_seek, st)
                    media_src = single_source
                    media_type = single_type
                    st = single_seek
                if not _render_base_with_fallback(
                    media_src,
                    media_type,
                    duration,
                    base_segment,
                    st,
                    vox_vf,
                    focal_bbox,
                    safe_framing,
                ):
                    print(
                        f"   [scene] {scene_idx + 1}: base render failed "
                        f"(source='{media_src}', exists={bool(media_src and Path(media_src).is_file())})"
                    )
                    if allow_emergency_media:
                        emergency_fallback = _render_neutral_scene(scene_idx, duration)
                        if emergency_fallback:
                            return emergency_fallback
                    return None

            # Keep the shot-bearing segment as a last-resort presentation
            # recovery point.  This is still the exact accepted source/shot
            # timeline; only a nonessential visual treatment is dropped.
            core_segment = base_segment

            if not skip_decorative_passes:
                # These treatments each require a full additional encode. Keep
                # them for short/premium jobs; long-form budget mode retains the
                # selected media, motion and editorial text without stacking
                # three decorative encode passes per scene.
                img_treatment = scene.get("design", {}).get("image_treatment", "hold")
                if img_treatment and img_treatment not in ("hold", "static", ""):
                    treated = str(segment_dir / f"treated_{scene_idx:04d}.ts")
                    ok = _apply_image_treatment(base_segment, treated, img_treatment)
                    if ok:
                        base_segment = treated
                    elif img_treatment in ("blurred_stage", "depth_separation"):
                        ok = _apply_blur_stage(base_segment, treated)
                        if ok:
                            base_segment = treated

                atmosphere = scene.get("atmosphere", {})
                if atmosphere:
                    atm_path = str(segment_dir / f"atm_{scene_idx:04d}.ts")
                    has_baked_text = (
                        bool(vox_vf)
                        or bool(overlay_path)
                        or bool(overlay_paths)
                        or scene.get("graphic", {}).get("template_id", "full-bleed") != "full-bleed"
                    )
                    if _apply_atmosphere(base_segment, atm_path, atmosphere, protect_text=has_baked_text):
                        base_segment = atm_path

                scene_overlays = scene.get("overlays") or []
                if scene_overlays:
                    ovl_path = str(segment_dir / f"ovl_{scene_idx:04d}.ts")
                    if _apply_scene_overlays(base_segment, ovl_path, scene_overlays):
                        base_segment = ovl_path

            # Composite overlay onto base segment (only when not using inline VOX filters)
            composite_paths = (
                [(overlay_path, graphic_local_start, graphic_local_end)]
                if overlay_path else []
            ) + overlay_paths
            intervals = {(round(start, 3), round(end, 3)) for _, start, end in composite_paths}
            if fast_render_mode and len(composite_paths) > 1 and len(intervals) == 1:
                flattened = _merge_static_overlays(
                    [path for path, _, _ in composite_paths],
                    str(segment_dir / f"overlay_flat_{scene_idx:04d}.png"),
                )
                if flattened:
                    only_start, only_end = next(iter(intervals))
                    composite_paths = [(flattened, only_start, only_end)]
                else:
                    composite_paths = []
            for composite_index, (current_overlay, overlay_start, overlay_end) in enumerate(composite_paths):
                if not current_overlay or not Path(current_overlay).is_file():
                    continue
                composited = str(segment_dir / f"comp_{scene_idx:04d}.ts")
                if composite_index:
                    composited = str(segment_dir / f"comp_{scene_idx:04d}_{composite_index:02d}.ts")
                ok = _composite_overlay(
                    base_segment,
                    current_overlay,
                    composited,
                    duration,
                    local_start=overlay_start,
                    local_end=overlay_end,
                )
                if ok:
                    print(f"   [composite] scene {scene_idx + 1}: composited overlay ({Path(current_overlay).name}) from {overlay_start:.2f}s to {overlay_end:.2f}s")
                    base_segment = composited

            exact_segment = _ensure_scene_segment_duration(
                base_segment,
                duration,
                str(segment_dir / f"exact_{scene_idx:04d}.ts"),
            )
            if not exact_segment:
                if core_segment != base_segment:
                    core_exact = _ensure_scene_segment_duration(
                        core_segment,
                        duration,
                        str(segment_dir / f"core_exact_{scene_idx:04d}.ts"),
                    )
                    if core_exact:
                        print(
                            f"   [scene] {scene_idx + 1}: presentation filter contract failed; "
                            "retaining the verified shot timeline"
                        )
                        return core_exact
                raise SemanticRenderBudgetExceeded(
                    f"Semantic scene {scene_idx + 1} could not satisfy its "
                    f"{duration:.3f}s/{scene_frame_contracts[scene_idx]}-frame contract"
                )
            return exact_segment

        max_workers = _positive_int(
            config.get("semantic_render_concurrency") or 4,
            4,
            1,
            max(1, min(8, os.cpu_count() or 4)),
        )
        processed_scenes = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for i, scene in enumerate(scenes):
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                future = executor.submit(_render_one_scene, i, scene)
                if cancellation is not None:
                    cancellation.register_future(future)
                futures[future] = i
            for future in as_completed(futures):
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                idx = futures[future]
                try:
                    seg = future.result()
                    if seg:
                        indexed_segments.append((idx, seg))
                    elif allow_emergency_media:
                        print(f"   [scene] {idx + 1}: render produced no segment; generating emergency neutral scene")
                        fallback = _render_neutral_scene(idx, scene_render_durations[idx])
                        if fallback:
                            indexed_segments.append((idx, fallback))
                    processed_scenes += 1
                    if progress_callback:
                        done = len(indexed_segments)
                        progress_callback(0.05 + 0.75 * (done / n_scenes),
                                          f"Rendered {done}/{n_scenes} scenes ({processed_scenes} processed)...")
                except SemanticRenderBudgetExceeded:
                    for pending in futures:
                        pending.cancel()
                    raise
                except Exception as e:
                    processed_scenes += 1
                    print(f"   [scene] {idx + 1}: render error: {e}")
                    if not allow_emergency_media:
                        # Preserve the real failure so the pipeline can
                        # surface an actionable media error instead of
                        # silently inserting a blank/neutral scene.
                        raise
                    # Keep the scene slot in the timeline.  This is only a
                    # last-chance placeholder; healthy scenes and all
                    # semantic overlays continue to render normally.
                    fallback = _render_neutral_scene(idx, scene_render_durations[idx])
                    if fallback:
                        indexed_segments.append((idx, fallback))
                        if progress_callback:
                            done = len(indexed_segments)
                            progress_callback(
                                0.05 + 0.75 * (done / n_scenes),
                                f"Recovered {done}/{n_scenes} scenes...",
                            )

        # Sort segments back into scene order (parallel workers finish out of order).
        rendered_segments = [path for _, path in sorted(indexed_segments)]

        if not rendered_segments:
            raise RuntimeError("No scenes rendered successfully")
        if len(rendered_segments) != n_scenes:
            raise RuntimeError(
                f"Semantic scene render incomplete: {len(rendered_segments)}/{n_scenes}"
            )

        if cancellation is not None:
            cancellation.raise_if_cancelled()

        # -------------------------------------------------------------------
        # Phase 2: Concatenate segments with transitions
        # -------------------------------------------------------------------
        print(f"   [ffmpeg] concatenating {len(rendered_segments)} segments...")
        if progress_callback:
            progress_callback(0.82, "Concatenating scenes...")

        concat_path = Path(temp_dir) / "_concat.ts"
        transitions = list(manifest.get("transitions") or [])
        transitions_by_scene = {
            int(item.get("scene_index")): item
            for item in transitions
            if isinstance(item, dict) and item.get("scene_index") is not None
        }

        # The semantic renderer owns transitions. Keep the limit configurable,
        # but default high enough that normal documentaries do not silently
        # degrade to hard cuts.
        try:
            # Full xfade graph for smooth cinematic transitions
            xfade_scene_limit = max(0, int(os.environ.get("VIDZEO_XFADE_MAX_SCENES", "50") or 50))
        except Exception:
            xfade_scene_limit = 50


        # A full xfade graph is O(n²) and can consume the final minutes of a
        # long render after the scene workers finish.  That used to leave only
        # ~78 seconds for concat, causing a healthy 22-scene job to die in its
        # final assembly.  When the remaining wall is tight, use the exact
        # stream-copy concat; each scene already carries its own deterministic
        # transition intro/fade, so editorial motion is preserved without the
        # giant graph.
        try:
            xfade_min_remaining = max(
                30.0,
                float(os.environ.get("VIDZEO_XFADE_MIN_REMAINING_SECONDS", "60") or 60),
            )
        except (TypeError, ValueError):
            xfade_min_remaining = 60.0
        remaining_before_concat = _remaining_render_budget()
        use_xfade = (
            bool(transitions)
            and xfade_scene_limit > 0
            and len(rendered_segments) <= xfade_scene_limit
            and (
                remaining_before_concat is None
                or remaining_before_concat >= xfade_min_remaining
            )
        )
        if transitions and not use_xfade:
            remaining_label = (
                "unbounded"
                if remaining_before_concat is None
                else f"{remaining_before_concat:.0f}s"
            )
            print(
                f"   [ffmpeg] using budget-safe stream concat for "
                f"{len(rendered_segments)} scenes (remaining={remaining_label}, "
                f"xfade reserve={xfade_min_remaining:.0f}s)"
            )

        if use_xfade:
            last_label = "0:v"
            filter_parts = []
            inputs = []
            # Pad each outgoing tail by the overlap amount. This keeps xfade
            # transitions without shortening the narration-aligned timeline.
            merged_dur = 0.0
            for i, seg in enumerate(rendered_segments):
                ilabel = f"{i}:v"
                inputs.extend(["-i", seg])
                if i == 0:
                    # Scale to canonical size on first input only
                    fmt_label = f"fmt{i}"
                    filter_parts.append(
                        f"[{ilabel}]fps={FPS},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
                        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,"
                        f"format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS[{fmt_label}]"
                    )
                    last_label = fmt_label
                    merged_dur = float(scenes[0].get("duration", 3.0))
                    continue
                # Manifest transition rows are keyed by the incoming scene
                # index; they intentionally omit cut boundaries.
                trans = transitions_by_scene.get(i, {})
                trans_id = trans.get("template_id", "dissolve")
                trans_dur = min(0.25, float(trans.get("duration", 0.3)))
                xfade_name = _transition_filter(trans_id, trans_dur)
                if not xfade_name:
                    xfade_name = "fade"
                    trans_dur = max(0.001, 1.0 / max(1, FPS))
                # Scale this input to canonical size too
                fmt_label = f"fmt{i}"
                filter_parts.append(
                    f"[{ilabel}]fps={FPS},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
                    f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,"
                    f"format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS[{fmt_label}]"
                )
                cur_dur = float(scenes[i].get("duration", 3.0))
                offset = max(0.0, merged_dur)
                pad_label = f"pad{i}"
                filter_parts.append(
                    f"[{last_label}]tpad=stop_mode=add:stop_duration={trans_dur:.3f}[{pad_label}]"
                )
                xf_label = f"t{i}"
                filter_parts.append(
                    f"[{pad_label}][{fmt_label}]xfade=transition={xfade_name}:"
                    f"duration={trans_dur:.3f}:offset={offset:.3f}[{xf_label}]"
                )
                last_label = xf_label
                merged_dur += cur_dur
            fc = ";".join(filter_parts)
            fc_file = Path(temp_dir) / "xfade.txt"
            fc_file.write_text(fc, encoding="utf-8")
            ffmpeg = _ffmpeg()
            cmd = [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
                *inputs,
                "-filter_complex_script", str(fc_file),
                "-map", f"[{last_label}]",
                "-an",
                *_video_encoder_args(),
                "-threads", _encode_threads(),
                str(concat_path),
            ]
        else:
            # Stream-copy concat (fast, used for large scene counts or no transitions)
            concat_list = Path(temp_dir) / "concat.txt"
            with open(concat_list, "w") as f:
                for scene_index, seg in enumerate(rendered_segments):
                    f.write(f"file '{seg}'\n")
                    f.write(
                        "duration "
                        f"{scene_frame_contracts[scene_index] / max(1, FPS):.9f}\n"
                    )
            ffmpeg = _ffmpeg()
            cmd = [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-c", "copy",
                str(concat_path),
            ]

        # The edited picture master is already complete at this point. Give
        # its concatenation a generous completion window outside the scene-render
        # wall so xfade re-encoding can finish. If it times out or errors,
        # gracefully fall back to the stream-copy of the rendered scenes so no
        # motion graphics, templates, or sound design are lost.
        concat_timeout = max(360, int(len(rendered_segments) * 45 + total_duration * 4)) if use_xfade else 120
        try:
            result = _run_ffmpeg_process(
                cmd, capture_output=True, timeout=concat_timeout, budget_exempt=True,
            )
        except subprocess.TimeoutExpired as exc:
            print(f"   [concat] primary timed out after {concat_timeout}s; falling back to stream concat...")
            result = subprocess.CompletedProcess(cmd, returncode=1, stderr=b"timeout", stdout=b"")
        except Exception as exc:
            print(f"   [concat] primary failed ({exc}); falling back to stream concat...")
            result = subprocess.CompletedProcess(cmd, returncode=1, stderr=str(exc).encode(), stdout=b"")

        if result.returncode != 0 or not concat_path.exists() or concat_path.stat().st_size < 4096:
            # Fallback: stream-copy concat if xfade fails, or re-encode if stream copy fails
            print(f"   [concat] primary failed (rc={result.returncode}), using concat fallback...")
            if result.stderr:
                err_snippet = result.stderr.decode(errors="replace")[:300]
                print(f"   [concat] error: {err_snippet}")
            if not use_xfade:
                concat_list = Path(temp_dir) / "concat.txt"
                cmd = [
                    ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
                    "-f", "concat", "-safe", "0",
                    "-i", str(concat_list),
                    *_video_encoder_args(),
                    "-threads", _encode_threads(),
                    str(concat_path),
                ]
            else:
                # xfade failed, fall back to instant stream-copy concat of rendered segments
                concat_list = Path(temp_dir) / "concat_fallback.txt"
                with open(concat_list, "w") as f:
                    for scene_index, seg in enumerate(rendered_segments):
                        f.write(f"file '{seg}'\n")
                        f.write(
                            "duration "
                            f"{scene_frame_contracts[scene_index] / max(1, FPS):.9f}\n"
                        )
                cmd = [
                    ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
                    "-f", "concat", "-safe", "0",
                    "-i", str(concat_list),
                    "-c", "copy",
                    str(concat_path),
                ]
            try:
                result = _run_ffmpeg_process(cmd, capture_output=True, timeout=90, budget_exempt=True)
            except Exception as fb_exc:
                print(f"   [concat] fallback error: {fb_exc}")
            if result.returncode != 0 or not concat_path.exists() or concat_path.stat().st_size < 4096:
                raise RuntimeError(f"Failed to concatenate scenes: {result.stderr.decode(errors='replace')[:200]}")

        concat_duration = _probe_duration_seconds(str(concat_path))
        duration_tolerance = max(0.05, 1.5 / max(1, FPS))
        if (
            concat_duration > 0
            and concat_duration + duration_tolerance < expected_picture_duration
        ):
            # Do not mux a known-short picture master. Raising the typed budget
            # error hands control to the already-bounded straight-media
            # recovery instead of silently truncating the narration again.
            raise SemanticRenderBudgetExceeded(
                "Semantic concat violated duration contract: "
                f"{concat_duration:.3f}s < {expected_picture_duration:.3f}s"
            )
        if concat_duration > 0:
            print(
                "   [ffmpeg] concat duration verified: "
                f"{concat_duration:.3f}s (target {expected_picture_duration:.3f}s)"
            )

        # -------------------------------------------------------------------
        # Phase 3: Build and mix audio
        # -------------------------------------------------------------------
        if progress_callback:
            progress_callback(0.90, "Mixing audio...")

        print(f"   [ffmpeg] mixing audio ({len(sound_cues)} sound cues)...")
        mixed_audio = _build_audio_concat(
            voiceover_path, music_path, music_volume, sound_cues,
            total_duration or 0, temp_dir,
        )
        if voiceover_path and Path(voiceover_path).is_file() and not mixed_audio:
            raise RuntimeError(
                "Designed audio mix failed; refusing to deliver a video without narration/SFX"
            )
        if sound_cues and not mixed_audio:
            raise RuntimeError(
                f"Sound-design mix failed for {len(sound_cues)} planned cues"
            )

        # -------------------------------------------------------------------
        # Phase 4: Final mux
        # -------------------------------------------------------------------
        video_duration = total_duration or 0.0
        try:
            duration_probe = _run_ffmpeg_process(
                [ffmpeg, "-hide_banner", "-nostdin", "-i", str(concat_path)],
                capture_output=True,
                text=True,
                timeout=20,
            )
            duration_match = re.search(
                r"Duration: (\d+):(\d+):(\d+\.\d+)", duration_probe.stderr or ""
            )
            if duration_match:
                video_duration = (
                    int(duration_match.group(1)) * 3600
                    + int(duration_match.group(2)) * 60
                    + float(duration_match.group(3))
                )
        except Exception:
            pass
        video_duration = max(0.1, float(video_duration))
        if mixed_audio:
            print(f"   [ffmpeg] muxing audio into final video...")
            cmd = [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
                "-i", str(concat_path),
                "-i", mixed_audio,
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-ar", "48000", "-ac", "2",
                # Picture duration is authoritative. Pad a short mix and
                # trim an overlong tail without changing playback speed or
                # pitch; never use -shortest for the final delivery.
                "-af", (
                    "apad,"
                    f"atrim=start=0:end={video_duration:.3f},"
                    "asetpts=PTS-STARTPTS"
                ),
                "-t", f"{video_duration:.3f}",
                "-movflags", "+faststart",
                str(output),
            ]
        else:
            # No audio - just copy video
            print(f"   [ffmpeg] no audio to mix")
            cmd = [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
                "-i", str(concat_path),
                "-c", "copy",
                str(output),
            ]

        result = _run_ffmpeg_process(cmd, capture_output=True, timeout=600)
        if result.returncode != 0 or not output.exists() or output.stat().st_size < 4096:
            raise RuntimeError(f"Final mux failed: {result.stderr.decode(errors='replace')[:200]}")

        elapsed = round(time.monotonic() - started, 3)
        if progress_callback:
            progress_callback(1.0, "Complete!")

        print(f"   [ffmpeg] render complete in {elapsed:.1f}s -> {output}")

    return {
        "output_path": str(output),
        "render_seconds": elapsed,
        "quality": quality,
        "scale": "1.0",
        "renderer": "ffmpeg-template",
    }
