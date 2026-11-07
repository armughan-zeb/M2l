# =============================================================================
# VIDEO SERVICE - FFmpeg Video Processing (InVideo-Style Fast Assembly)
# =============================================================================

import os
import subprocess
import hashlib
import time
import re
from pathlib import Path
from PIL import Image
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from .job_context import JobThreadPoolExecutor as ThreadPoolExecutor

# Force unbuffered output (fixes RDP terminal freeze)
os.environ['PYTHONUNBUFFERED'] = '1'
_original_print = print
def print(*args, **kwargs):
    kwargs.setdefault('flush', True)
    try:
        _original_print(*args, **kwargs)
    except UnicodeEncodeError:
        safe_args = [
            str(arg).encode("ascii", errors="replace").decode("ascii")
            for arg in args
        ]
        _original_print(*safe_args, **kwargs)

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import TEMP_DIR, OUTPUT_DIR, FONTS_DIR, FPS, CROSSFADE_DURATION, EFFECTS, ANIMATIONS, FONT_NAME_MAP

# Motion Template System (Canva-style - animate once, reuse forever)
from services.motion_templates import (
    generate_all_templates,
    render_scene_with_template,
    get_template_for_effect,
    TEMPLATE_RES,
    TEMPLATE_FPS
)
from services.media_asset_policy import emergency_media_allowed, is_synthetic_media

# Whisper model reference (lazy loaded)
_whisper_model = None

_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


def _env_int(name: str, default: int, min_value: int = 1, max_value: int | None = None) -> int:
    """Read a positive integer env override without letting bad values break render."""
    try:
        value = int(str(os.environ.get(name, default)).strip() or default)
    except Exception:
        value = default
    value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def sanitize_output_name(value: str, fallback: str = "Video") -> str:
    """Return a Windows-safe, readable filename stem."""
    text = str(value or "").strip()
    text = re.sub(r"</?think>|think\s*>|<[^>]*>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text:
        text = fallback
    text = text[:96].strip(" .") or fallback
    if text.upper() in _WINDOWS_RESERVED_NAMES:
        text = f"{text}_video"
    return text


def _format_ffmpeg_error(error: Exception, fallback: str = "FFmpeg failed") -> str:
    if isinstance(error, subprocess.TimeoutExpired):
        return f"{fallback}: timed out after {error.timeout}s"
    if isinstance(error, subprocess.CalledProcessError):
        stderr = error.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        stderr = (stderr or "").strip()
        detail = stderr.splitlines()[-1] if stderr else ""
        return f"{fallback}: exit code {error.returncode}{f' ({detail})' if detail else ''}"
    return f"{fallback}: {error}"

# =============================================================================
# INVIDEO-STYLE UNIVERSAL CPU SCHEDULER
# =============================================================================
# Auto-scales from 2-core laptop to 128-core dual-EPYC
# Works on Windows/Linux, single/multi-CPU systems

def detect_cpu_count():
    """
    Robust CPU count detection - CMD-only, Windows RDP/VPS safe.

    Uses:
    1. WMIC (most reliable, reads from Windows kernel)
    2. NUMBER_OF_PROCESSORS env var (fallback)
    3. os.cpu_count() (last resort)
    """
    detected = None
    method = None

    # Method 1: WMIC - CMD-ONLY, RDP-SAFE, MULTI-CPU AWARE
    try:
        out = subprocess.check_output(
            ["wmic", "cpu", "get", "NumberOfLogicalProcessors"],
            shell=False,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode()
        cores = sum(int(x) for x in out.split() if x.isdigit())
        if cores > 0:
            detected = cores
            method = 'WMIC'
    except:
        pass

    # Method 2: Windows environment variable (fallback)
    if detected is None or detected < 2:
        try:
            env_count = int(os.environ.get('NUMBER_OF_PROCESSORS', 0))
            if env_count > (detected or 0):
                detected = env_count
                method = 'NUMBER_OF_PROCESSORS'
        except:
            pass

    # Method 3: Standard Python (last resort)
    if detected is None or detected < 2:
        detected = os.cpu_count() or 2
        method = 'os.cpu_count()'

    print(f"   ðŸ–¥ï¸ CPU Detection ({method}): {detected} logical cores")
    return detected


def cpu_scheduler():
    """
    Production-grade CPU scheduler for InVideo-style parallel rendering.

    Works with:
    - Single CPU laptops (2-8 cores)
    - Multi-core servers (16-64 cores)
    - Multi-CPU EPYC systems (64-256 cores)

    Returns optimal parallel jobs and threads per job.
    """
    # Use robust detection
    logical_cores = detect_cpu_count()

    # Reserve 10% for OS responsiveness (minimum 1 core)
    reserve = max(1, logical_cores // 10)
    usable = logical_cores - reserve

    # Optimal threads per FFmpeg job (x264 sweet spot is 4-8)
    # Use 6 for 16+ cores, 4 for smaller systems
    if logical_cores >= 64:
        cores_per_job = 6  # EPYC/Xeon: 6 threads optimal
    elif logical_cores >= 16:
        cores_per_job = 6  # Multi-core: 6 threads
    elif logical_cores >= 8:
        cores_per_job = 4  # 8-core: 4 threads
    else:
        cores_per_job = max(2, logical_cores // 2)  # Small: half cores

    # Calculate parallel jobs
    parallel_jobs = max(1, usable // cores_per_job)

    # Cap at reasonable maximum (diminishing returns beyond ~20 jobs)
    parallel_jobs = min(parallel_jobs, 24)

    return {
        "logical_cores": logical_cores,
        "usable_cores": usable,
        "cores_per_job": cores_per_job,
        "parallel_jobs": parallel_jobs
    }


def estimate_render_time(num_scenes: int, duration_per_scene: float) -> dict:
    """
    Estimate render time based on CPU configuration.
    Returns estimated minutes for scene rendering + concat.

    MOTION TEMPLATE SYSTEM: Overlay on pre-rendered template = <1s per scene!
    """
    # Get actual resource partition for accurate job count
    try:
        res = get_resource_partition()
        jobs = max(1, res.get('video_workers', 1))  # Minimum 1 for 2-core
    except:
        jobs = 1  # Fallback to 2-core default

    # MOTION TEMPLATE: ~2 seconds per scene (overlay is nearly instant)
    # First video takes +10s for template generation (cached after)
    scene_time_per_batch = 2  # seconds per scene batch
    batches = (num_scenes + jobs - 1) // jobs
    scene_total = batches * scene_time_per_batch

    # Template generation (first time only, ~10s total)
    template_time = 10  # seconds (amortized to 0 after first video)

    # Concat: instant (stream copy)
    concat_time = 2  # seconds

    # Audio mux: instant (stream copy when no subtitles)
    mux_time = 2  # seconds

    total_seconds = scene_total + template_time + concat_time + mux_time

    return {
        "estimated_minutes": max(1, round(total_seconds / 60, 1)),
        "parallel_jobs": jobs,
        "scenes": num_scenes,
        "batches": batches
    }


def get_resource_partition() -> dict:
    """
    INVIDEO-STYLE: CPU Resource Partitioning.

    Reserves CPU lanes for different tasks to prevent:
    - CPU thrashing
    - Context switching overhead
    - System unresponsiveness

    Returns: dict with cores/workers for each task type
    """
    # Use robust detection (works on Windows Server/RDP)
    logical_cores = detect_cpu_count()

    # Calculate base values (same logic as cpu_scheduler)
    reserve = max(1, logical_cores // 10)
    usable = logical_cores - reserve

    # =================================================================
    # ULTIMATE FAST MODE: CPU BUDGET (FROM PRODUCTION SPEC)
    # =================================================================
    # | Subsystem             | Cores  | Notes                 |
    # | OS + overhead         | 4      | Always reserved       |
    # | TTS (Audiogram)       | 8      | 4 workers x 2 threads |
    # | Image generation      | 16     | Parallel API / local  |
    # | Scene video rendering | 32     | 16 jobs Ã— 2 threads   |
    # | Final mux             | 2      | burst only            |
    # | Total                 | 62     | Safe, stable          |
    # =================================================================

    if logical_cores >= 64:
        # 64+ cores: ULTIMATE FAST MODE
        # Images = 1 (sequential, avoid rate limits)
        # TTS/Video = scale with cores
        result = {
            "os_reserve": 4,
            "tts_workers": 8,       # 8 parallel TTS chunks
            "tts_threads": 2,       # Ã— 2 threads = 16 cores for TTS
            "image_workers": 1,     # SEQUENTIAL - avoid Pollinations rate limits
            "video_workers": 24,    # 24 parallel FFmpeg jobs
            "video_threads": 2      # Ã— 2 threads = 48 cores for video
        }
    elif logical_cores >= 32:
        # 32-63 cores: Fast mode
        result = {
            "os_reserve": 2,
            "tts_workers": 6,       # 6 parallel TTS
            "tts_threads": 2,
            "image_workers": 1,     # SEQUENTIAL
            "video_workers": 12,    # 12 parallel FFmpeg
            "video_threads": 2
        }
    elif logical_cores >= 16:
        # 16-31 cores: Desktop/Workstation
        result = {
            "os_reserve": 2,
            "tts_workers": 4,       # 4 parallel TTS
            "tts_threads": 2,
            "image_workers": 1,     # SEQUENTIAL
            "video_workers": 6,     # 6 parallel FFmpeg
            "video_threads": 2
        }
    elif logical_cores >= 8:
        # 8-15 cores: Good laptop
        result = {
            "os_reserve": 1,
            "tts_workers": 3,       # 3 parallel TTS
            "tts_threads": 2,
            "image_workers": 1,     # SEQUENTIAL to avoid rate limits
            "video_workers": 4,     # Faster 60+ scene jobs on common 8-core desktops
            "video_threads": 1
        }
    elif logical_cores >= 4:
        # 4-7 cores: Low-end laptop
        result = {
            "os_reserve": 1,
            "tts_workers": 2,
            "tts_threads": 1,
            "image_workers": 1,     # SEQUENTIAL
            "video_workers": 2,
            "video_threads": 1
        }
    else:
        # 2-3 cores: Minimum system
        result = {
            "os_reserve": 0,
            "tts_workers": 1,
            "tts_threads": 1,
            "image_workers": 1,     # SEQUENTIAL
            "video_workers": 1,
            "video_threads": 1
        }

    # Portable tuning knobs for distributed builds.  Defaults are conservative,
    # but long mixed-media jobs should not crawl just because the local CPU has
    # more safe lanes available.
    max_workers = max(1, logical_cores - result.get("os_reserve", 0))
    result["video_workers"] = _env_int(
        "VIDZEO_VIDEO_RENDER_WORKERS",
        int(result.get("video_workers", 1) or 1),
        1,
        max_workers,
    )
    result["video_threads"] = _env_int(
        "VIDZEO_VIDEO_THREADS_PER_JOB",
        int(result.get("video_threads", 1) or 1),
        1,
        max(1, logical_cores),
    )
    result["tts_workers"] = _env_int(
        "VIDZEO_TTS_WORKERS",
        int(result.get("tts_workers", 1) or 1),
        1,
        max_workers,
    )

    print(f"   ðŸ“Š Resource Partition: TTS={result['tts_workers']}Ã—{result['tts_threads']}, Images=1 (sequential), Video={result['video_workers']}Ã—{result['video_threads']}")
    return result


# Get scheduler settings at startup with explicit debug
print("\n" + "="*50)
print("âš¡ INVIDEO-STYLE CPU SCHEDULER INIT")
print("="*50)

# Detect cores EXPLICITLY
_detected_cores = os.cpu_count()
print(f"   os.cpu_count() returned: {_detected_cores}")

# Fallback if detection fails
if _detected_cores is None or _detected_cores < 2:
    print(f"   âš ï¸ CPU detection failed! Forcing to 8 cores minimum")
    _detected_cores = 8

# FORCE MINIMUM for any reasonable system
if _detected_cores >= 8:
    _min_parallel = max(2, _detected_cores // 8)  # At least 2 parallel jobs on 8+ cores
else:
    _min_parallel = 1

_cpu_config = cpu_scheduler()

# Apply minimum enforcement
THREADS_PER_JOB = _env_int("VIDZEO_FFMPEG_THREADS_PER_JOB", max(2, _cpu_config["cores_per_job"]), 1, max(1, _detected_cores))
PARALLEL_WORKERS = max(_min_parallel, _cpu_config["parallel_jobs"])

print(f"   Detected {_detected_cores} logical cores")
print(f"   THREADS_PER_JOB = {THREADS_PER_JOB}")
print(f"   PARALLEL_WORKERS = {PARALLEL_WORKERS}")

# Fixed output settings (NEVER CHANGE - ensures zero-cost final assembly)
OUTPUT_WIDTH = 1920
OUTPUT_HEIGHT = 1080

# Quality presets for export (resolution + encoder settings)
QUALITY_PRESETS = {
    "high":     {"crf": 18, "cq": 18, "preset": "medium",   "preset_nvenc": "p5", "width": 1920, "height": 1080},
    "standard": {"crf": 23, "cq": 23, "preset": "fast",     "preset_nvenc": "p4", "width": 1920, "height": 1080},
    "medium":   {"crf": 23, "cq": 23, "preset": "fast",     "preset_nvenc": "p4", "width": 1280, "height": 720},
    "fast":     {"crf": 28, "cq": 28, "preset": "veryfast", "preset_nvenc": "p2", "width": 854,  "height": 480},
}


# Global state
_gpu_name = "CPU"
_encoder = "libx264"
_has_gpu = False


def init_video_service():
    """Initialize video service and detect GPU"""
    global _gpu_name, _encoder, _has_gpu

    # Print InVideo-style CPU configuration
    cfg = _cpu_config
    res = get_resource_partition()

    print(f"")
    print(f"âš¡ InVideo-Style CPU Config:")
    print(f"   ðŸ–¥ï¸  Logical cores detected: {cfg['logical_cores']}")
    print(f"   ðŸ“Š Usable cores: {cfg['usable_cores']}")
    print(f"   ðŸ”§ Threads per FFmpeg job: {cfg['cores_per_job']}")
    print(f"   ðŸš€ Parallel video workers: {cfg['parallel_jobs']}")
    print(f"")
    print(f"   Resource Partition:")
    print(f"      TTS workers: {res['tts_workers']}")
    print(f"      Image workers: {res['image_workers']}")
    print(f"      Video workers: {res['video_workers']}")

    # Estimate performance for 10-min video (100 scenes)
    est = estimate_render_time(100, 6)
    print(f"   Est. 10-min video: ~{est['estimated_minutes']} min")

    try:
        from services.gpu_accelerator import detect_optimal_gpu_encoder
        gpu_info = detect_optimal_gpu_encoder()
        _gpu_name = gpu_info["device_name"]
        _encoder = gpu_info["codec"]
        _has_gpu = gpu_info["is_hardware_accelerated"]
        if _has_gpu:
            print(f"   GPU: {_gpu_name} ({gpu_info['encoder_name']})")
            return
    except Exception:
        pass

    print(f"   Encoder: CPU (libx264)")


def get_gpu_info() -> dict:
    """Get GPU information"""
    return {
        "name": _gpu_name,
        "encoder": _encoder,
        "has_gpu": _has_gpu
    }


def validate_image(path: str) -> bool:
    """Validate image or video file"""
    try:
        ext = Path(path).suffix.lower()
        if ext in ('.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv'):
            import subprocess
            result = subprocess.run(
                ['ffmpeg', '-v', 'error', '-i', path, '-c', 'copy', '-f', 'null', '-'],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            return result.returncode == 0
        img = Image.open(path)
        img.verify()
        return True
    except:
        return False


def _is_video(path: str) -> bool:
    """Check if a path is a video file."""
    ext = Path(path).suffix.lower()
    return ext in ('.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv')


def _probe_media_duration(path: str) -> float:
    """Return media duration in seconds, or 0 if ffprobe cannot read it."""
    try:
        result = subprocess.run(
            [
                'ffprobe', '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        if result.returncode == 0 and result.stdout.strip():
            return max(0.0, float(result.stdout.strip()))
    except Exception:
        pass
    return 0.0


# =============================================================================
# CAPTION GENERATION
# =============================================================================

def get_font_name(dropdown_value: str) -> str:
    """Extract font family name from dropdown value like 'â¬› Bold | BebasNeue-Regular'"""
    if " | " in dropdown_value:
        filename = dropdown_value.split(" | ")[1]
    else:
        filename = dropdown_value
    return FONT_NAME_MAP.get(filename, filename)


def to_ass_time(t: float) -> str:
    """Convert seconds to ASS time format"""
    cs = int(round(t * 100))
    return f"{cs // 360000}:{(cs // 6000) % 60:02d}:{(cs // 100) % 60:02d}.{cs % 100:02d}"


def hex_to_ass(c: str) -> str:
    """Convert hex color to ASS format"""
    c = c.lstrip("#")
    return f"&H00{int(c[4:6], 16):02X}{int(c[2:4], 16):02X}{int(c[0:2], 16):02X}"


def hex_bgr(c: str) -> str:
    """Convert hex color to BGR format for ASS"""
    c = c.lstrip("#")
    return f"{int(c[4:6], 16):02X}{int(c[2:4], 16):02X}{int(c[0:2], 16):02X}"


def esc(s: str) -> str:
    """Escape special characters for ASS"""
    return s.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def load_whisper_model():
    """Load the shared configured Whisper model, including offline fallbacks."""
    try:
        from services.transcription_service import init_whisper
        if not init_whisper():
            return None
        from services import transcription_service
        return transcription_service._whisper_model
    except Exception as exc:
        print(f"   Whisper load failed: {exc}")
        return None


def transcribe_audio(audio_path: str) -> tuple:
    """Transcribe audio using faster-whisper, return (words, segments)."""
    from services.transcription_service import transcribe_audio as shared_transcribe_audio
    return shared_transcribe_audio(audio_path, language="en")


def generate_captions(
    audio_path: str,
    caption_mode: str = "single",
    words_per_group: int = 3,
    font: str = "Arial",
    fontsize: int = 48,
    bold: bool = True,
    uppercase: bool = True,
    text_color: str = "#FFFFFF",
    highlight_color: str = "#FFFF00",
    outline_color: str = "#000000",
    outline_size: int = 3,
    shadow_on: bool = True,
    shadow_color: str = "#000000",
    shadow_depth: int = 2,
    bg_on: bool = False,
    bg_color: str = "#000000",
    bg_opacity: int = 80,
    position: str = "bottom",
    margin_v: int = 50,
    animation: str = "Pop"
) -> str:
    """
    Generate ASS subtitle file from audio using Whisper.

    Args:
        audio_path: Path to audio file
        caption_mode: 'single' (one word), 'word' (word groups), 'line' (full lines)
        words_per_group: Words per group in 'word' mode
        font: Font name or dropdown value
        fontsize: Font size
        bold: Bold text
        uppercase: Uppercase text
        text_color: Primary text color (hex)
        highlight_color: Highlighted word color (hex)
        outline_color: Text outline color (hex)
        outline_size: Outline thickness
        shadow_on: Enable shadow
        shadow_color: Shadow color (hex)
        shadow_depth: Shadow distance
        bg_on: Enable background box
        bg_color: Background color (hex)
        bg_opacity: Background opacity (0-100)
        position: Text position ('top', 'middle', 'bottom')
        margin_v: Vertical margin
        animation: Animation style ('None', 'Fade', 'Pop')

    Returns:
        Path to generated ASS file, or None if failed
    """
    # Extract font name if categorized format
    font = get_font_name(font)

    words, segments = transcribe_audio(audio_path)
    if not words:
        return None

    align = {"top": 8, "middle": 5, "bottom": 2}.get(position, 2)
    shadow = shadow_depth if shadow_on else 0
    shadow_ass = hex_to_ass(shadow_color).replace("&H00", "&H80")
    border_style = 3 if bg_on else 1
    bg_ass = hex_to_ass(bg_color).replace("&H00", f"&H{255 - int(bg_opacity * 2.55):02X}")
    bold_val = -1 if bold else 0

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{fontsize},{hex_to_ass(text_color)},{hex_to_ass(text_color)},{hex_to_ass(outline_color)},{shadow_ass if shadow_on else bg_ass},{bold_val},0,0,0,100,100,0,0,{border_style},{outline_size},{shadow},{align},50,50,{margin_v},1
Style: Highlight,{font},{fontsize},{hex_to_ass(highlight_color)},{hex_to_ass(highlight_color)},{hex_to_ass(outline_color)},{shadow_ass if shadow_on else bg_ass},{bold_val},0,0,0,100,100,0,0,{border_style},{outline_size + 1},{shadow},{align},50,50,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines = [header]
    anim = ANIMATIONS.get(animation, ANIMATIONS["None"])
    anim_tag = "{" + anim.get("in", "") + "}" if anim.get("in") else ""

    hl_bgr = hex_bgr(highlight_color)
    txt_bgr = hex_bgr(text_color)

    if caption_mode == "single":
        for w in words:
            txt = w["word"].upper() if uppercase else w["word"]
            start, end = w["start"], w["end"]
            if end <= start:
                end = start + 0.25
            lines.append(f"Dialogue: 0,{to_ass_time(start)},{to_ass_time(end)},Highlight,,0,0,0,,{anim_tag}{esc(txt)}\n")

    elif caption_mode == "line":
        # Line mode: Show N words at a time (using words_per_group), no highlighting
        n = len(words)
        i = 0
        while i < n:
            j = min(i + words_per_group, n)
            block = words[i:j]

            if not block:
                i = j
                continue

            # Get timing from first and last word in block
            start = block[0]["start"]
            end = block[-1]["end"]
            if end <= start:
                end = start + 0.5

            # Join all words in block
            txt_parts = []
            for w in block:
                txt = w["word"].upper() if uppercase else w["word"]
                txt_parts.append(esc(txt))

            full_txt = " ".join(txt_parts)
            lines.append(f"Dialogue: 0,{to_ass_time(start)},{to_ass_time(end)},Default,,0,0,0,,{anim_tag}{full_txt}\n")

            i = j

    else:  # word mode (groups)
        n = len(words)
        i = 0
        while i < n:
            j = min(i + words_per_group, n)
            block = words[i:j]
            for k, w in enumerate(block):
                start, end = w["start"], w["end"]
                if end <= start:
                    end = start + 0.2
                parts = [anim_tag] if k == 0 and anim_tag else []
                for m, word in enumerate(block):
                    txt = word["word"].upper() if uppercase else word["word"]
                    if m == k:
                        parts.append("{\\rHighlight\\1c&H" + hl_bgr + "&}" + esc(txt))
                    else:
                        parts.append("{\\rDefault\\1c&H" + txt_bgr + "&}" + esc(txt))
                    if m < len(block) - 1:
                        parts.append(" ")
                lines.append(f"Dialogue: 0,{to_ass_time(start)},{to_ass_time(end)},Default,,0,0,0,,{''.join(parts)}\n")
            i = j

    ass_path = str(TEMP_DIR / f"cap_{hashlib.sha256(str(time.time()).encode()).hexdigest()[:8]}.ass")
    with open(ass_path, 'w', encoding='utf-8') as f:
        f.write("".join(lines))
    print(f"   âœ… Captions saved: {ass_path}")
    return ass_path


def get_smooth_zoom_filter(effect: str, duration: float, index: int = 0) -> str:
    """
    Ultra-smooth zoom/pan with sine-curve easing.
    """
    frames = int(duration * FPS)
    frames = max(frames, FPS)  # At least 1 second worth of frames

    if effect == 'Zoom Combo':
        effect = 'Zoom In' if index % 2 == 0 else 'Zoom Out'

    if effect == 'Zoom In':
        zoom_expr = f"1+0.08*(1-cos(PI*on/{frames}))/2"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif effect == 'Zoom Out':
        zoom_expr = f"1.08-0.08*(1-cos(PI*on/{frames}))/2"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif effect == 'Pan Left':
        zoom_expr = "1.05"
        x_expr = f"(iw-iw/zoom)*(1-(1-cos(PI*on/{frames}))/2)"
        y_expr = "(ih-ih/zoom)/2"
    elif effect == 'Pan Right':
        zoom_expr = "1.05"
        x_expr = f"(iw-iw/zoom)*(1-cos(PI*on/{frames}))/2"
        y_expr = "(ih-ih/zoom)/2"
    else:  # None - subtle slow zoom
        zoom_expr = f"1+0.03*(1-cos(PI*on/{frames}))/2"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"

    return f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s=1920x1080:fps={FPS}"


def normalize_image(img_path: str) -> str:
    """
    INVIDEO-STYLE: Normalize image to 1920x1080 ONCE.
    Prevents per-frame scaling in zoompan = huge speed win.
    """
    base = os.path.basename(img_path)
    name, ext = os.path.splitext(base)
    output_path = str(TEMP_DIR / f"{name}_norm.png")

    # Skip if already normalized
    if os.path.exists(output_path):
        return output_path

    # FFmpeg normalize: scale + crop to exact 1920x1080
    cmd = [
        'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
        '-i', img_path,
        '-vf', f'scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=cover,crop={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}',
        '-q:v', '2',
        output_path
    ]

    try:
        subprocess.run(cmd, capture_output=True, timeout=30, check=True)
        return output_path if os.path.exists(output_path) else img_path
    except:
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except:
            pass
        return img_path


def concat_ts_segments(clips: list, output_path: str) -> str:
    """
    EPYC-Optimized: Zero-reencode concat of .ts segments.
    Uses binary cat + stream copy = INSTANT (seconds, not minutes).
    """
    if not clips:
        raise ValueError("No clips to concatenate")

    # Ensure output is .mp4
    if not output_path.endswith('.mp4'):
        output_path = output_path.rsplit('.', 1)[0] + '.mp4'

    # All clips should be .ts
    ts_clips = [c for c in clips if c and c.endswith('.ts')]

    if not ts_clips:
        print("   âš ï¸ No .ts clips found, falling back to old concat")
        return concatenate_videos_smooth(clips, output_path)

    # Method 1: Binary concat to .ts then stream copy to .mp4
    temp_ts = str(TEMP_DIR / f"concat_{hashlib.sha256(str(time.time()).encode()).hexdigest()[:8]}.ts")

    try:
        # Binary cat all .ts files
        with open(temp_ts, 'wb') as outfile:
            for ts_path in ts_clips:
                if os.path.exists(ts_path):
                    with open(ts_path, 'rb') as infile:
                        while True:
                            chunk = infile.read(65536)
                            if not chunk:
                                break
                            outfile.write(chunk)

        # Stream copy to mp4 (no re-encode!)
        cmd = [
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
            '-i', temp_ts,
            '-c', 'copy',  # NO RE-ENCODE
            '-movflags', '+faststart',
            output_path
        ]

        subprocess.run(cmd, capture_output=True, timeout=120, check=True)

        # Cleanup temp .ts
        try:
            os.remove(temp_ts)
        except:
            pass

        if os.path.exists(output_path):
            print(f"   âš¡ Zero-reencode concat: {len(ts_clips)} segments â†’ {os.path.basename(output_path)}")
            return output_path

    except Exception as e:
        print(f"   âš ï¸ Binary concat failed: {e}, falling back...")

    # Fallback: FFmpeg concat demuxer with copy
    concat_file = output_path + ".txt"
    with open(concat_file, 'w') as f:
        for c in ts_clips:
            f.write(f"file '{os.path.abspath(c)}'\n")

    try:
        subprocess.run([
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
            '-f', 'concat', '-safe', '0', '-i', concat_file,
            '-c', 'copy', output_path
        ], check=True, timeout=120)
        return output_path
    except:
        pass
    finally:
        try:
            os.remove(concat_file)
        except:
            pass

    return None


def concatenate_videos_smooth(clips: list, output_path: str, crossfade_duration: float = 0.4) -> str:
    """Concatenate videos with smooth crossfade transitions (legacy)"""

    # Check if all clips are .ts - use zero-reencode
    if all(c and c.endswith('.ts') for c in clips if c):
        return concat_ts_segments(clips, output_path)

    if len(clips) == 1:
        subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                       '-i', clips[0], '-c', 'copy', output_path], check=True)
        return output_path

    n = len(clips)

    # Get durations
    durations = []
    for clip in clips:
        result = subprocess.run([
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', clip
        ], capture_output=True, text=True)
        durations.append(float(result.stdout.strip()))

    # Build filter complex with 'fade' transition
    filter_parts = []
    current = "[0:v]"
    offset = durations[0] - crossfade_duration

    for i in range(1, n):
        next_in = f"[{i}:v]"
        out = f"[v{i}]" if i < n - 1 else "[vout]"
        filter_parts.append(f"{current}{next_in}xfade=transition=fade:duration={crossfade_duration}:offset={offset:.3f}{out}")
        current = out
        if i < n - 1:
            offset += durations[i]

    filter_complex = ";".join(filter_parts)

    cmd = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error']
    for c in clips:
        cmd += ['-i', c]

    cmd += ['-filter_complex', filter_complex, '-map', '[vout]']

    if _encoder == 'h264_nvenc':
        cmd += ['-c:v', 'h264_nvenc', '-preset', 'p4', '-cq', '23', '-b:v', '6M']
    else:
        cmd += ['-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23']

    cmd += ['-pix_fmt', 'yuv420p', '-an', output_path]

    # Dynamic timeout based on number of clips: 30s base + 5s per clip
    concat_timeout = max(120, 30 + len(clips) * 5)

    try:
        subprocess.run(cmd, capture_output=True, timeout=concat_timeout, check=True)
        return output_path
    except subprocess.SubprocessError:
        # Fallback to simple concat
        print("   âš ï¸ Crossfade failed, using simple concat")
        concat_file = output_path + ".txt"
        with open(concat_file, 'w') as f:
            for c in clips:
                f.write(f"file '{os.path.abspath(c)}'\n")
        try:
            subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                           '-f', 'concat', '-safe', '0', '-i', concat_file,
                           '-c', 'copy', output_path], check=True, timeout=300)
            return output_path
        finally:
            try:
                os.remove(concat_file)
            except:
                pass


def assemble_final_video(
    video_path: str,
    audio_path: str,
    subtitle_path: str = None,
    music_path: str = None,
    music_volume: int = 20,
    total_duration: float = None,
    output_name: str = "Video",
    output_dir: str = None,
    quality: str = "standard",
    anti_copy: bool = False,
    cancellation=None,
) -> str:
    """
    Assemble final video with audio, subtitles, and optional music.
    """
    output_name = sanitize_output_name(output_name)

    # Use custom output_dir if provided, otherwise default OUTPUT_DIR
    if output_dir and output_dir.strip():
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
    else:
        out_path = OUTPUT_DIR

    # Resolve quality preset and determine output path (collision-safe)
    qp = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["standard"])
    needs_scale = (qp["width"], qp["height"]) != (OUTPUT_WIDTH, OUTPUT_HEIGHT)

    def _unique_path(directory, stem, ext):
        path = directory / f"{stem}{ext}"
        if not path.exists():
            return path
        counter = 1
        while True:
            path = directory / f"{stem}_{counter}{ext}"
            if not path.exists():
                return path
            counter += 1

    final_output = _unique_path(out_path, output_name, ".mp4")

    use_subtitles = bool(subtitle_path and os.path.exists(subtitle_path))
    soft_subtitles_requested = str(os.environ.get('VIDZEO_SOFT_SUBTITLES', '0')).strip().lower() in {'1', 'true', 'yes', 'on'}
    force_burn_subtitles = str(os.environ.get('VIDZEO_FORCE_BURN_SUBTITLES', '1')).strip().lower() in {'1', 'true', 'yes', 'on'}
    burn_subtitles = bool(use_subtitles and force_burn_subtitles and not soft_subtitles_requested)
    soft_subtitles = bool(use_subtitles and not burn_subtitles)

    cmd = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
           '-i', video_path, '-i', audio_path]
    next_input_index = 2
    music_input_index = None
    subtitle_input_index = None

    if music_path and os.path.exists(music_path):
        music_input_index = next_input_index
        next_input_index += 1
        cmd += ['-i', music_path]

    if soft_subtitles:
        subtitle_input_index = next_input_index
        next_input_index += 1
        cmd += ['-i', subtitle_path]
        print("   Soft subtitle mux enabled for long video (no video re-encode)")

    if music_input_index is not None:
        cmd += ['-filter_complex',
                f"[1:a]volume=1.0[v];[{music_input_index}:a]volume={music_volume/100}[m];[v][m]amix=inputs=2:duration=first[a]",
                '-map', '0:v', '-map', '[a]']
    else:
        cmd += ['-map', '0:v', '-map', '1:a']

    if soft_subtitles and subtitle_input_index is not None:
        cmd += ['-map', f'{subtitle_input_index}:s?']

    if total_duration:
        cmd += ['-t', f"{total_duration:.3f}"]

    # Build video filter chain for re-encode scenarios
    vf_parts = []
    if burn_subtitles:
        sub_filename = os.path.basename(subtitle_path)
        fonts_rel = os.path.relpath(FONTS_DIR, TEMP_DIR).replace("\\", "/")
        vf_parts.append(f"subtitles='{sub_filename}':fontsdir='{fonts_rel}'")
    if needs_scale:
        vf_parts.append(f"scale={qp['width']}:{qp['height']}:flags=lanczos")
    needs_reencode = bool(vf_parts)

    if needs_reencode:
        cmd += ['-vf', ','.join(vf_parts)]
        if _encoder == 'h264_nvenc':
            cmd += ['-c:v', 'h264_nvenc', '-preset', qp['preset_nvenc'], '-cq', str(qp['cq']), '-b:v', '8M',
                    '-profile:v', 'high', '-level', '4.2']
        else:
            cmd += ['-c:v', 'libx264', '-preset', qp['preset'], '-crf', str(qp['crf']),
                    '-profile:v', 'high', '-level', '4.2',
                    '-pix_fmt', 'yuv420p',
                    '-threads', str(THREADS_PER_JOB)]
        print(f"   Using quality: {quality} ({qp['width']}x{qp['height']}, CRF {qp['crf']}, {qp['preset']})")
    elif _encoder == 'h264_nvenc':
        cmd += ['-c:v', 'h264_nvenc', '-preset', qp['preset_nvenc'], '-cq', str(qp['cq']), '-b:v', '8M',
                '-profile:v', 'high', '-level', '4.2']
        print(f"   Using quality: {quality} (CQ {qp['cq']}, {qp['preset_nvenc']})")
    else:
        cmd += ['-c:v', 'copy']
        print("   Using stream copy for final video (no video re-encode)")

    cmd += ['-c:a', 'aac', '-b:a', '256k', '-ar', '48000']
    if soft_subtitles:
        cmd += ['-c:s', 'mov_text', '-disposition:s:0', 'default', '-metadata:s:s:0', 'language=eng']
    cmd += ['-shortest',
            '-threads', '0',
            '-max_muxing_queue_size', '4096',
            str(final_output)]

    cwd = str(TEMP_DIR) if burn_subtitles else None

    duration_label = f"{total_duration:.0f}s" if total_duration else "unknown duration"
    soft_factor = 0.20 if soft_subtitles else (0.08 if not burn_subtitles else 0.35)
    est_time = int(total_duration * soft_factor) if total_duration else 180
    print(f"   Encoding final video ({duration_label})...")
    print(f"   Estimated time: ~{est_time//60}m {est_time%60}s")

    timeout_factor = 2.0 if soft_subtitles else (1.5 if not burn_subtitles else 2.5)
    final_timeout = max(600, int(90 + (total_duration or 300) * timeout_factor))
    nosub_timeout = max(600, int(90 + (total_duration or 300) * 1.5))
    def run_ffmpeg_non_blocking(cmd, cwd, timeout, desc="encoding"):
        """
        Run FFmpeg with non-blocking I/O to prevent terminal freeze on Windows RDP.
        Uses Popen with DEVNULL to prevent buffer blocking issues.
        """
        import threading
        from .job_context import JobThread
        start_time = time.time()

        # Use files/DEVNULL to prevent pipe buffer blocking on Windows/RDP.
        err_log = TEMP_DIR / f"ffmpeg_{hashlib.sha256(str(time.time()).encode()).hexdigest()[:8]}.err"
        err_handle = open(err_log, "wb")
        process = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=err_handle,
            stdin=subprocess.DEVNULL,  # Prevent any input waiting
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        if cancellation is not None:
            cancellation.register_process(process)

        # Watchdog thread to show progress and prevent "frozen" appearance
        stop_watchdog = threading.Event()

        def watchdog():
            while not stop_watchdog.is_set():
                stop_watchdog.wait(15)  # Update every 15 seconds
                if not stop_watchdog.is_set() and process.poll() is None:
                    elapsed = int(time.time() - start_time)
                    remaining = max(0, est_time - elapsed)
                    _original_print(f"   Still {desc}... {elapsed}s elapsed, ~{remaining}s remaining")
                    try:
                        import sys as _sys
                        _sys.stdout.flush()
                    except:
                        pass

        watchdog_thread = JobThread(target=watchdog, daemon=True)
        watchdog_thread.start()

        # Poll process with timeout and queue cancellation.
        deadline = time.time() + timeout
        try:
            while process.poll() is None:
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                if time.time() > deadline:
                    process.kill()
                    stop_watchdog.set()
                    err_handle.close()
                    raise subprocess.TimeoutExpired(cmd, timeout)
                time.sleep(0.1)
        except BaseException:
            if process.poll() is None:
                if cancellation is not None:
                    cancellation._terminate_process(process)
                else:
                    process.kill()
            stop_watchdog.set()
            raise
        finally:
            if cancellation is not None:
                cancellation.unregister_process(process)

        stop_watchdog.set()
        elapsed = int(time.time() - start_time)

        err_handle.close()
        stderr = ""
        try:
            stderr = err_log.read_text(encoding="utf-8", errors="replace")
        except Exception:
            stderr = ""
        try:
            err_log.unlink(missing_ok=True)
        except Exception:
            pass

        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd, stderr=stderr)

        return elapsed

    def _build_nosub_cmd():
        return [
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
            '-i', video_path, '-i', audio_path,
            '-map', '0:v', '-map', '1:a',
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '256k',
            '-shortest',
            str(final_output),
        ]

    try:
        elapsed = run_ffmpeg_non_blocking(cmd, cwd, final_timeout, "encoding")
        print(f"   Encoding complete in {elapsed}s")
        # Run faststart as a separate step so we can measure and report progress
        if os.path.exists(final_output) and os.path.getsize(final_output) > 0:
            faststart_start = time.time()
            temp_output = str(final_output).replace(".mp4", "_temp.mp4")
            os.replace(final_output, temp_output)
            faststart_cmd = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                            '-i', temp_output,
                            '-c', 'copy',
                            '-movflags', '+faststart',
                            str(final_output)]
            try:
                subprocess.run(faststart_cmd, capture_output=True, timeout=max(120, int((total_duration or 300) * 0.05)), check=True)
                faststart_elapsed = time.time() - faststart_start
                print(f"   Fast-start optimization: {faststart_elapsed:.0f}s")
                try:
                    os.remove(temp_output)
                except Exception:
                    pass
            except Exception:
                # faststart failed, use temp_output as final
                try:
                    if os.path.exists(final_output):
                        os.remove(final_output)
                except Exception:
                    pass
                os.replace(temp_output, final_output)
    except subprocess.TimeoutExpired:
        print(f"   Final assembly timeout after {final_timeout}s, trying faster preset...")
        # Retry with faster preset
        cmd_fast = [c for c in cmd]
        for i, arg in enumerate(cmd_fast):
            if arg == 'ultrafast':
                pass  # Already fastest
            elif arg in ['medium', 'fast', 'faster', 'veryfast', 'superfast']:
                cmd_fast[i] = 'ultrafast'
        try:
            run_ffmpeg_non_blocking(cmd_fast, cwd, final_timeout * 2, "fast encoding")
        except Exception as e:
            print(f"   {_format_ffmpeg_error(e, 'Final encoding failed')}")
            # Last resort: skip subtitles entirely
            print(f"   Attempting without subtitles...")
            subprocess.run(_build_nosub_cmd(), check=True, capture_output=True, timeout=nosub_timeout)
    except subprocess.CalledProcessError as e:
        print(f"   {_format_ffmpeg_error(e, 'Encoding error')}, trying without subtitles...")
        # Fallback: skip subtitles
        subprocess.run(_build_nosub_cmd(), check=True, capture_output=True, timeout=nosub_timeout)
        print(f"   Video saved without subtitles")
    except Exception as e:
        print(f"   Unexpected error: {e}")
        # Ultimate fallback
        if not os.path.exists(final_output):
            subprocess.run(_build_nosub_cmd(), check=True, capture_output=True, timeout=nosub_timeout)

    return str(final_output)


def generate_video(
    images: list,
    audio_path: str,
    total_duration: float,
    effect: str = "Zoom Combo",
    subtitle_path: str = None,
    music_path: str = None,
    music_volume: int = 20,
    output_name: str = "Video",
    output_dir: str = None,
    progress_callback=None,
    scene_durations: list = None,
    source_labels: list = None,
    quality: str = "standard",
    cancellation=None,
    allow_emergency_media: bool | None = None,
) -> str:
    """
    Full video generation pipeline.

    Args:
        images: List of image paths
        audio_path: Path to audio file
        total_duration: Total video duration in seconds
        effect: Transition effect
        subtitle_path: Path to ASS subtitle file
        music_path: Path to background music
        music_volume: Music volume percentage
        output_name: Output video name
        output_dir: Custom output directory (optional)
        progress_callback: Progress callback function
        scene_durations: Optional list of individual scene durations
        source_labels: Optional source label per input media path

    Returns:
        str: Path to generated video
    """
    if cancellation is not None:
        cancellation.raise_if_cancelled()
    if allow_emergency_media is None:
        # Direct legacy callers retain the historical diagnostic behavior;
        # production app calls pass the explicit strict setting.
        allow_emergency_media = emergency_media_allowed(None)
    output_name = sanitize_output_name(output_name)
    jid = hashlib.sha256(f"{output_name}{time.time()}".encode()).hexdigest()[:8]

    # Validate media (images + video clips)
    labels = list(source_labels or [])
    if not allow_emergency_media:
        invalid_media = [
            str(media or "")
            for media in images
            if not media
            or is_synthetic_media(media)
            or not validate_image(media)
        ]
        if invalid_media:
            raise ValueError(
                "Legacy renderer rejected missing/synthetic media; "
                "real media is required for every scene."
            )
    media_entries = [
        (source_index, m, labels[source_index] if source_index < len(labels) else "")
        for source_index, m in enumerate(images)
        if m and validate_image(m)
    ]
    valid_media = [entry[1] for entry in media_entries]
    if not valid_media:
        raise ValueError("No valid images provided")

    # Separate into images and video clips
    image_paths = [m for m in valid_media if not _is_video(m)]
    video_paths = [(i, m) for i, m in enumerate(valid_media) if _is_video(m)]
    n = len(valid_media)

    has_clips = len(video_paths) > 0
    has_images = len(image_paths) > 0

    # Compute scene duration before any branch logging/render setup. The image-only
    # path uses this immediately for status output, so keep it above the media split.
    crossfade = CROSSFADE_DURATION
    total_crossfade_time = crossfade * (n - 1) if n > 1 else 0
    per_image_duration = max(1.5, (total_duration + total_crossfade_time) / n)

    if has_clips:
        print(f"\nðŸŽ¬ Processing {n} media ({len(image_paths)} images, {len(video_paths)} video clips)...")
    else:
        print(f"\nðŸ–¼ï¸ Processing {n} images...")
        print(f"   Duration: {per_image_duration:.2f}s each")

    # =================================================================
    # MOTION TEMPLATE SYSTEM (Canva-style - animate once, reuse forever)
    # =================================================================
    # Generate templates ONCE (cached for all future videos)
    if has_images:
        print(f"   ðŸŽ¬ Initializing motion templates...")
        generate_all_templates(duration=per_image_duration)

        # Map effect to template
        template_name = get_template_for_effect(effect)
        print(f"   ðŸŽ¯ Using template: {template_name}")
        print(f"   âš¡ Resolution: {TEMPLATE_RES[0]}x{TEMPLATE_RES[1]} @ {TEMPLATE_FPS}fps")

    # Show estimated time
    est = estimate_render_time(n, per_image_duration)
    print(f"   ETA: ~{max(1, est['estimated_minutes'])} min")

    # Pre-render scenes using motion templates (images) or direct copy (video clips)
    if progress_callback:
        progress_callback(0.4, "Pre-rendering scenes...")

    def _is_youtube_source(label: str) -> bool:
        return "youtube" in str(label or "").lower()

    def _fullscreen_video_filter(source_label: str = "", duration: float = 0.0) -> str:
        tail_pad = max(0.1, float(duration or 0.0) + 0.25)
        if _is_youtube_source(source_label):
            yt_frame_scale = min(
                0.9,
                max(0.75, float(os.environ.get("VIDZEO_YT_FINGERPRINT_FRAME_SCALE", "0.82") or 0.82)),
            )
            framed_width = max(2, int(OUTPUT_WIDTH * yt_frame_scale) // 2 * 2)
            framed_height = max(2, int(OUTPUT_HEIGHT * yt_frame_scale) // 2 * 2)
            return (
                f"[0:v]scale={framed_width}:{framed_height}:force_original_aspect_ratio=decrease,"
                f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,"
                f"tpad=stop_mode=clone:stop_duration={tail_pad:.3f},"
                f"trim=duration={float(duration):.3f},setpts=PTS-STARTPTS,format=yuv420p[v]"
            )
        return (
            f"[0:v]scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={OUTPUT_WIDTH}:{OUTPUT_HEIGHT},"
            f"tpad=stop_mode=clone:stop_duration={tail_pad:.3f},"
            f"trim=duration={float(duration):.3f},setpts=PTS-STARTPTS,"
            "setsar=1,format=yuv420p[v]"
        )

    def _render_video_frame_fallback(media_path: str, duration: float, output: str, scene_index: int):
        frame_path = str(TEMP_DIR / f"scene_{jid}_{scene_index}_fallback.jpg")
        try:
            extract_cmd = [
                'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                '-ss', '0.5',
                '-i', media_path,
                '-frames:v', '1',
                '-q:v', '3',
                frame_path,
            ]
            subprocess.run(extract_cmd, check=True, capture_output=True, timeout=30)
            if not os.path.exists(frame_path) or os.path.getsize(frame_path) < 1024:
                if not allow_emergency_media:
                    raise RuntimeError(
                        f"Scene {scene_index + 1} could not produce a frame from real footage"
                    )
                return _render_neutral_video_fallback(duration, output, scene_index)
            return render_scene_with_template(frame_path, "static", duration, output)
        except Exception as frame_error:
            print(f"   Scene {scene_index + 1} frame fallback failed: {_format_ffmpeg_error(frame_error, 'frame fallback failed')}")
            if not allow_emergency_media:
                raise
            return _render_neutral_video_fallback(duration, output, scene_index)

    def _render_neutral_video_fallback(duration: float, output: str, scene_index: int):
        if not allow_emergency_media:
            raise RuntimeError(
                f"Scene {scene_index + 1} has no renderable real media; "
                "neutral fallback is disabled"
            )
        try:
            cmd = [
                'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                '-f', 'lavfi',
                '-i', f'color=c=black:s={OUTPUT_WIDTH}x{OUTPUT_HEIGHT}:r={FPS}',
                '-t', f"{float(duration):.3f}",
                '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28',
                '-pix_fmt', 'yuv420p',
                '-f', 'mpegts', output,
            ]
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
            return output if os.path.exists(output) and os.path.getsize(output) > 1024 else None
        except Exception as neutral_error:
            print(f"   Scene {scene_index + 1} neutral fallback failed: {_format_ffmpeg_error(neutral_error, 'neutral fallback failed')}")
            return None

    def _video_stream_is_readable(media_path: str) -> bool:
        try:
            probe = subprocess.run(
                [
                    'ffprobe', '-v', 'error',
                    '-select_streams', 'v:0',
                    '-show_entries', 'stream=codec_type,width,height',
                    '-of', 'csv=p=0',
                    media_path,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return probe.returncode == 0 and "video" in (probe.stdout or "")
        except Exception:
            return True

    def render_scene_task(args):
        """Task for parallel scene rendering with templates or direct video copy"""
        i, media_path, duration, source_label = args

        # Video clip: use the cheapest normalized render path. YouTube-specific
        # flip/crop/speed has already been applied during clip preprocessing.
        if _is_video(media_path):
            output = str(TEMP_DIR / f"scene_{jid}_{i}.ts")
            if not _video_stream_is_readable(media_path):
                if not allow_emergency_media:
                    raise RuntimeError(
                        f"Scene {i + 1} video stream is unreadable; real footage is required"
                    )
                print(f"   Scene {i + 1} video stream invalid before render; trying still-frame fallback")
                return (i, _render_video_frame_fallback(media_path, duration, output, i))
            try:
                vf = _fullscreen_video_filter(source_label, duration)
                cmd = [
                    'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                    '-analyzeduration', '5M', '-probesize', '5M',
                    '-fflags', '+genpts',
                    '-i', media_path,
                    '-t', f"{float(duration):.3f}",
                    '-filter_complex', vf,
                    '-map', '[v]', '-an',
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
                    '-profile:v', 'high', '-level', '4.1',
                    '-threads', str(max(1, int(res.get('video_threads', 1) or 1))),
                    '-vsync', 'cfr',
                    '-g', '30', '-keyint_min', '30',
                    '-r', str(FPS),
                    '-f', 'mpegts', output,
                ]
                render_timeout = max(45, min(90, int(float(duration) * 8) + 20))
                subprocess.run(cmd, check=True, capture_output=True, timeout=render_timeout)
                return (i, output)
            except Exception as e:
                print(f"   Scene {i + 1} video render failed: {_format_ffmpeg_error(e, 'render failed')}; trying still-frame fallback")
                if not allow_emergency_media:
                    raise
                fallback = _render_video_frame_fallback(media_path, duration, output, i)
                return (i, fallback)

        # Image: use zoompan template
        output = str(TEMP_DIR / f"scene_{jid}_{i}.ts")
        scene_template = get_template_for_effect(effect if effect != "Alternate" else "Alternate", i)
        result = render_scene_with_template(media_path, scene_template, duration, output)
        return (i, result)

    scenes = {}
    completed = 0
    start_time = time.time()

    # Get workers AT RUNTIME (critical for proper CPU detection)
    res = get_resource_partition()
    video_workers = max(1, res.get('video_workers', 1))  # Minimum 1 for 2-core

    # For 2-core systems, use sequential rendering to avoid overload
    if video_workers <= 1:
        print(f"   ?? Sequential rendering (2-core mode)")
    else:
        print(f"   ? Parallel rendering ({video_workers} workers)")

    # Helper function to get the precise scene duration if available
    def get_duration(idx):
        if scene_durations and idx < len(scene_durations):
            return float(scene_durations[idx])
        return per_image_duration

    with ThreadPoolExecutor(max_workers=video_workers) as executor:
        futures = {
            executor.submit(render_scene_task, (i, m, get_duration(i), label)): i
            for i, (_source_index, m, label) in enumerate(media_entries)
        }

        for future in as_completed(futures):
            try:
                # Timeout per scene: 5 minutes max
                i, result = future.result(timeout=300)
                if result:
                    scenes[i] = result
                    completed += 1

                    # Calculate ETA
                    elapsed = time.time() - start_time
                    if completed > 0:
                        rate = completed / elapsed
                        remaining = (n - completed) / rate if rate > 0 else 0
                        print(f"   ? Scene {completed}/{n} ({remaining:.0f}s remaining)")
                    else:
                        print(f"   ? Scene {completed}/{n}")
            except Exception as e:
                print(f"   ?? Scene failed: {e}")

            if progress_callback:
                progress_callback(0.4 + 0.25 * completed / n, f"Scene {completed}/{n}...")

    # Print total time
    total_time = time.time() - start_time
    print(f"   ? Scenes rendered in {total_time:.1f}s ({completed}/{n} success)")

    # Sort scenes by index to maintain order
    clips = [scenes[i] for i in sorted(scenes.keys()) if i in scenes]

    # Check all expected scenes rendered. Do not silently ship a video with
    # missing later sections; that creates frozen/black tails in the final mux.
    if not clips:
        raise ValueError("Failed to render any scenes - check FFmpeg installation and image paths")
    missing_scene_indices = [i for i in range(n) if i not in scenes or not scenes.get(i)]
    if missing_scene_indices:
        raise RuntimeError(
            "Scene rendering incomplete; refusing to create a stuck/frozen output. "
            f"Missing scenes: {', '.join(str(i + 1) for i in missing_scene_indices[:20])}"
            f"{'...' if len(missing_scene_indices) > 20 else ''}"
        )

    _temp_cleanup_done = False

    def _do_cleanup():
        nonlocal _temp_cleanup_done
        if _temp_cleanup_done:
            return
        _temp_cleanup_done = True
        try:
            _all_temp = list(clips)
            if concat_output and os.path.exists(concat_output):
                _all_temp.append(concat_output)
            cleanup_temp_files(job_id=jid, clips=_all_temp)
        except Exception:
            pass

    # INVIDEO-STYLE: Zero-cost final concat (binary cat + stream copy)
    if progress_callback:
        progress_callback(0.7, "Final concat (instant)...")
    print(f"\n? Final concat ({len(clips)} scenes ? 1 video)...")
    concat_output = str(TEMP_DIR / f"concat_{jid}.mp4")

    final_path = None
    try:
        concat_ts_segments(clips, concat_output)
        if not os.path.exists(concat_output):
            raise FileNotFoundError(f"Concat output not created: {concat_output}")
        print("   ? Instant!")
        if total_duration:
            _actual = _probe_media_duration(concat_output)
            if _actual <= 0:
                raise RuntimeError("Could not verify concatenated video duration.")
            _deficit = float(total_duration) - _actual
            if _deficit > 0.75:
                raise RuntimeError(
                    "Rendered scene video is shorter than the audio target; refusing to pad with frozen/black frames. "
                    f"video={_actual:.2f}s target={float(total_duration):.2f}s deficit={_deficit:.2f}s"
                )
    except Exception as e:
        print(f"   ? Concat failed: {e}")
        try:
            filter_input = '|'.join(clips)
            fallback_cmd = [
                'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                '-i', f'concat:{filter_input}',
                '-c', 'copy',
                concat_output
            ]
            subprocess.run(fallback_cmd, capture_output=True, timeout=120, check=True)
            print("   ? Fallback concat succeeded")
        except Exception as e2:
            _do_cleanup()
            raise RuntimeError(f"Both concat methods failed: primary={e}, fallback={e2}")

    if progress_callback:
        progress_callback(0.85, "Final assembly...")

    try:
        final_path = assemble_final_video(
            video_path=concat_output,
            audio_path=audio_path,
            subtitle_path=subtitle_path,
            music_path=music_path,
            music_volume=music_volume,
            total_duration=total_duration,
            output_name=output_name,
            output_dir=output_dir,
            quality=quality,
            anti_copy=False,
            cancellation=cancellation,
        )
        if not final_path or not os.path.exists(final_path):
            raise FileNotFoundError(f"Final video not created")
    except Exception as e:
        print(f"   ? Final assembly failed: {_format_ffmpeg_error(e, 'assembly failed')}")
        try:
            simple_dir = Path(output_dir) if output_dir and str(output_dir).strip() else OUTPUT_DIR
            simple_dir.mkdir(parents=True, exist_ok=True)
            simple_output = str(simple_dir / f"{output_name}_simple.mp4")
            simple_cmd = [
                'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                '-i', concat_output,
                '-i', audio_path,
                '-c:v', 'copy',
                '-c:a', 'aac',
                '-shortest',
                simple_output
            ]
            subprocess.run(simple_cmd, capture_output=True, timeout=300, check=True)
            if os.path.exists(simple_output):
                final_path = simple_output
                print("   ? Simple assembly succeeded")
            else:
                raise FileNotFoundError("Simple assembly output not created")
        except Exception as e2:
            _do_cleanup()
            raise RuntimeError(
                "Both assembly methods failed: "
                f"primary={_format_ffmpeg_error(e, 'primary')}; "
                f"fallback={_format_ffmpeg_error(e2, 'fallback')}"
            )

    _do_cleanup()

    if progress_callback:
        progress_callback(1.0, "Done!")

    print(f"\n? {final_path}")
    return final_path


def get_effects() -> list:
    """Get list of available effects"""
    return EFFECTS


def check_ffmpeg() -> bool:
    """Check if FFmpeg is available"""
    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5)
        return result.returncode == 0
    except:
        return False


def cleanup_temp_files(job_id: str = None, clips: list = None):
    """
    Clean up temporary files after video generation.

    Args:
        job_id: If provided, delete all files matching this job ID pattern
        clips: List of specific clip paths to delete
    """
    import shutil
    deleted = 0

    # Delete specific clips
    if clips:
        for clip in clips:
            try:
                if os.path.exists(clip):
                    os.remove(clip)
                    deleted += 1
            except Exception:
                pass

    # Delete by job ID pattern
    if job_id:
        try:
            for f in TEMP_DIR.glob(f"*{job_id}*"):
                try:
                    os.remove(f)
                    deleted += 1
                except Exception:
                    pass
        except Exception:
            pass

    # YouTube source pools are shared by parallel scene cutters and may belong
    # to another active queue job. Never delete the entire yt_clips root from a
    # render's per-job cleanup. Age-based maintenance below owns those folders.

    # Clean up web_images_simple_* directories
    try:
        for p in TEMP_DIR.iterdir():
            if p.is_dir() and p.name.startswith("web_images_simple_"):
                shutil.rmtree(p, ignore_errors=True)
                deleted += 1
    except Exception:
        pass

    # Clean up scene_*.ts render artifacts for this job
    try:
        scene_pattern = f"scene_{job_id}_*.ts" if job_id else "scene_*.ts"
        for f in TEMP_DIR.glob(scene_pattern):
            try:
                os.remove(f)
                deleted += 1
            except Exception:
                pass
    except Exception:
        pass

    if deleted > 0:
        print(f"   Cleaned up {deleted} temp files")


def cleanup_old_temp_files(max_age_hours: int = 24):
    """
    Clean up temp files older than max_age_hours.
    Should be called periodically to prevent disk space issues.

    Args:
        max_age_hours: Delete files older than this many hours (default 24)
    """
    import shutil
    import time

    cutoff_time = time.time() - (max_age_hours * 3600)
    deleted = 0

    try:
        for f in TEMP_DIR.iterdir():
            if f.is_file():
                try:
                    if f.stat().st_mtime < cutoff_time:
                        os.remove(f)
                        deleted += 1
                except Exception:
                    pass
            elif f.is_dir() and f.name != "yt_source_cache":
                # Only remove known temporary working directories
                if not f.name.startswith(("web_images_simple_", "yt_clips", "yt_")):
                    continue
                try:
                    if f.stat().st_mtime < cutoff_time:
                        shutil.rmtree(f, ignore_errors=True)
                        deleted += 1
                except Exception:
                    pass
    except Exception:
        pass

    if deleted > 0:
        print(f"Cleaned up {deleted} old temp files (>{max_age_hours}h)")

    return deleted
