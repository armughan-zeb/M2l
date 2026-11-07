# =============================================================================
# MOTION TEMPLATES - SINE-EASED ZOOM/PAN (PRODUCTION GRADE)
# =============================================================================
#
# All zoom/pan animations use sine-curve easing for smooth,
# natural-looking motion that lasts the full clip duration.
#
# CPU Golden Rules Applied:
# ✅ Scale ONCE outside zoompan
# ✅ Sine easing on frame counter (on), not internal zoom var
# ✅ veryfast preset
# ✅ -threads 0 (full CPU usage)
#
# =============================================================================

import os
import subprocess
from pathlib import Path
from PIL import Image

TEMPLATE_DIR = Path(__file__).parent / "motion_templates"
TOOLS_DIR = Path(__file__).parent.parent.parent / "tools"

# CPU-OPTIMIZED SETTINGS
OUTPUT_SIZE = (1920, 1080)   # Final output
SCALE_SIZE = (2560, 1440)    # Scale ONCE to this, then zoompan crops
FPS = 30                      # Match config.py (30fps) for smooth quality and flawless TS concat compatibility
CRF = 17                      # High premium cinematic quality
PRESET = "veryfast"           # Vastly superior quality than ultrafast

# Backward compatibility aliases
TEMPLATE_RES = OUTPUT_SIZE
TEMPLATE_FPS = FPS


def ensure_template_dir():
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    return TEMPLATE_DIR


def get_ffmpeg_path():
    configured = str(os.environ.get("VIDZEO_FFMPEG_PATH") or "").strip()
    if configured:
        return configured
    ffmpeg = TOOLS_DIR / "ffmpeg" / "ffmpeg.exe"
    return str(ffmpeg) if ffmpeg.exists() else "ffmpeg"


def _prepare_image_for_ffmpeg(img_path, output_path):
    """Normalize mislabeled/corrupt web images into an FFmpeg-friendly PNG."""
    source = Path(str(img_path))
    normalized = Path(str(output_path)).with_suffix(".input.png")
    try:
        with Image.open(str(source)) as img:
            img.load()
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            elif img.mode == "RGBA":
                background = Image.new("RGB", img.size, "black")
                background.paste(img, mask=img.getchannel("A"))
                img = background
            else:
                img = img.copy()
            normalized.parent.mkdir(parents=True, exist_ok=True)
            img.save(str(normalized), format="PNG", optimize=False)
        if normalized.exists() and normalized.stat().st_size > 0:
            return str(normalized)
    except Exception:
        return str(source)
    return str(source)


def generate_all_templates(duration=None, force=False):
    ensure_template_dir()
    print(f"   ✅ Zoompan ready (CPU-Optimized: {OUTPUT_SIZE[0]}x{OUTPUT_SIZE[1]} @ {FPS}fps)")
    return True


def get_template_for_effect(effect, index=0):
    effect_map = {
        # Basic effects
        "None": "static",
        "Static": "static",
        "Zoom In": "zoom_in",
        "Zoom Out": "zoom_out",
        "Pan Left": "pan_left",
        "Pan Right": "pan_right",
        "Pan Up": "pan_up",
        "Pan Down": "pan_down",
        "Diagonal": "diagonal",
        
        # Combo effects (2-phase)
        "Zoom Combo": "zoom_combo",           # Zoom in → Zoom out
        "Ken Burns": "zoom_combo",
        "Pan Vertical": "pan_vertical",       # Pan up → Pan down
        "Pan Horizontal": "pan_horizontal",   # Pan left → Pan right
        "Zoom Pan": "zoom_pan",               # Zoom in + Pan right
        "Ken Burns Pro": "ken_burns_pro",     # Zoom in + diagonal pan
        
        # Multi-phase combos
        "Dynamic": "dynamic",                 # Zoom in → Pan → Zoom out
        "Master Combo": "master_combo",       # All effects combined!
        "Alternate": "alternate",             # Alternates between scenes
    }
    
    pattern = effect_map.get(effect, "zoom_in")
    
    if effect == "Zoom Combo":
        return "zoom_in" if index % 2 == 0 else "zoom_out"
    if pattern == "alternate":
        patterns = ["zoom_in", "zoom_out", "pan_left", "pan_right", "zoom_combo", "pan_vertical"]
        pattern = patterns[index % len(patterns)]

    return pattern


def _zexpr_valley(base, peak, on, frames):
    """Sine easing: base at start, peak at mid, back to base at end."""
    return f"{base}+({peak}-{base})*sin(PI*on/{frames})"

def _zexpr_rise(base, peak, on, frames):
    """Sine easing: base at start, peak at end."""
    return f"{base}+({peak}-{base})*(1-cos(PI*on/{frames}))/2"

def _zexpr_fall(base, peak, on, frames):
    """Sine easing: peak at start, base at end."""
    return f"{peak}-({peak}-{base})*(1-cos(PI*on/{frames}))/2"

def render_scene_with_template(img_path, template_name, duration, output_path):
    w, h = OUTPUT_SIZE
    sw, sh = SCALE_SIZE
    frames = int(duration * FPS)
    ffmpeg = get_ffmpeg_path()
    target_ratio = w / h
    ffmpeg_img_path = _prepare_image_for_ffmpeg(img_path, output_path)

    def contain_filter(width=w, height=h):
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
        )

    def cover_scale_filter(width=sw, height=sh):
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}"
        )

    # Do not stretch square/portrait/narrow images into landscape. They should
    # stay intact on a black matte instead of being distorted by zoompan.
    try:
        with Image.open(str(img_path)) as img:
            img_w, img_h = img.size
        source_ratio = (img_w / img_h) if img_h else target_ratio
        if source_ratio < target_ratio * 0.82 or source_ratio > target_ratio * 1.22:
            template_name = "static"
    except Exception:
        pass
    
    smooth_scale = f":s={sw}x{sh}:fps={FPS}"
    smooth_downscale = f",scale={w}:{h}:flags=lanczos+accurate_rnd"

    if template_name == "static":
        vf = contain_filter()
        use_zoompan = False

    elif template_name == "zoom_in":
        z = _zexpr_rise(1.0, 1.25, 'on', frames)
        zoompan = f"zoompan=z='{z}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':d={frames}{smooth_scale}"
        vf = f"{cover_scale_filter()},{zoompan}{smooth_downscale}"
        use_zoompan = True

    elif template_name == "zoom_out":
        z = _zexpr_fall(1.0, 1.25, 'on', frames)
        zoompan = f"zoompan=z='{z}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':d={frames}{smooth_scale}"
        vf = f"{cover_scale_filter()},{zoompan}{smooth_downscale}"
        use_zoompan = True

    elif template_name == "zoom_combo":
        half = max(1, frames // 2)
        z1 = _zexpr_rise(1.0, 1.2, 'on', half)
        z2 = _zexpr_fall(1.0, 1.2, f'on-{half}', frames - half)
        zoompan = f"zoompan=z='if(lt(on\\,{half})\\,{z1}\\,{z2})':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':d={frames}{smooth_scale}"
        vf = f"{cover_scale_filter()},{zoompan}{smooth_downscale}"
        use_zoompan = True

    elif template_name == "pan_left":
        zoom = 1.1
        x = _zexpr_fall(1, 0, 'on', frames)
        zoompan = f"zoompan=z={zoom}:x='(iw-iw/{zoom})*{x}':y='(ih-ih/{zoom})/2':d={frames}{smooth_scale}"
        vf = f"{cover_scale_filter()},{zoompan}{smooth_downscale}"
        use_zoompan = True

    elif template_name == "pan_right":
        zoom = 1.1
        x = _zexpr_rise(0, 1, 'on', frames)
        zoompan = f"zoompan=z={zoom}:x='(iw-iw/{zoom})*{x}':y='(ih-ih/{zoom})/2':d={frames}{smooth_scale}"
        vf = f"{cover_scale_filter()},{zoompan}{smooth_downscale}"
        use_zoompan = True

    elif template_name == "pan_up":
        zoom = 1.1
        y = _zexpr_fall(1, 0, 'on', frames)
        zoompan = f"zoompan=z={zoom}:x='(iw-iw/{zoom})/2':y='(ih-ih/{zoom})*{y}':d={frames}{smooth_scale}"
        vf = f"{cover_scale_filter()},{zoompan}{smooth_downscale}"
        use_zoompan = True

    elif template_name == "pan_down":
        zoom = 1.1
        y = _zexpr_rise(0, 1, 'on', frames)
        zoompan = f"zoompan=z={zoom}:x='(iw-iw/{zoom})/2':y='(ih-ih/{zoom})*{y}':d={frames}{smooth_scale}"
        vf = f"{cover_scale_filter()},{zoompan}{smooth_downscale}"
        use_zoompan = True

    elif template_name == "pan_vertical":
        half = max(1, frames // 2)
        zoom = 1.1
        y1 = _zexpr_fall(1, 0, 'on', half)
        y2 = _zexpr_rise(0, 1, f'on-{half}', frames - half)
        zoompan = f"zoompan=z={zoom}:x='(iw-iw/{zoom})/2':y='(ih-ih/{zoom})*if(lt(on\\,{half})\\,{y1}\\,{y2})':d={frames}{smooth_scale}"
        vf = f"{cover_scale_filter()},{zoompan}{smooth_downscale}"
        use_zoompan = True

    elif template_name == "diagonal":
        zoom = 1.1
        ease = _zexpr_rise(0, 1, 'on', frames)
        zoompan = f"zoompan=z={zoom}:x='(iw-iw/{zoom})*{ease}':y='(ih-ih/{zoom})*{ease}':d={frames}{smooth_scale}"
        vf = f"{cover_scale_filter()},{zoompan}{smooth_downscale}"
        use_zoompan = True

    elif template_name == "pan_horizontal":
        half = max(1, frames // 2)
        zoom = 1.1
        x1 = _zexpr_fall(1, 0, 'on', half)
        x2 = _zexpr_rise(0, 1, f'on-{half}', frames - half)
        zoompan = f"zoompan=z={zoom}:x='(iw-iw/{zoom})*if(lt(on\\,{half})\\,{x1}\\,{x2})':y='(ih-ih/{zoom})/2':d={frames}{smooth_scale}"
        vf = f"{cover_scale_filter()},{zoompan}{smooth_downscale}"
        use_zoompan = True

    elif template_name == "zoom_pan":
        z = _zexpr_rise(1.0, 1.15, 'on', frames)
        x = _zexpr_rise(0, 1, 'on', frames)
        zoompan = f"zoompan=z='{z}':x='(iw-iw/zoom)*{x}':y='(ih-ih/zoom)/2':d={frames}{smooth_scale}"
        vf = f"{cover_scale_filter()},{zoompan}{smooth_downscale}"
        use_zoompan = True

    elif template_name == "ken_burns_pro":
        z = _zexpr_rise(1.0, 1.12, 'on', frames)
        ease = _zexpr_rise(0, 1, 'on', frames)
        zoompan = f"zoompan=z='{z}':x='(iw-iw/zoom)*{ease}':y='(ih-ih/zoom)*{ease}':d={frames}{smooth_scale}"
        vf = f"{cover_scale_filter()},{zoompan}{smooth_downscale}"
        use_zoompan = True

    elif template_name == "dynamic":
        third = max(1, frames // 3)
        two_thirds = third * 2
        z1 = _zexpr_rise(1.0, 1.15, 'on', third)
        z3 = _zexpr_fall(1.0, 1.15, f'on-{two_thirds}', frames - two_thirds)
        z_expr = f"if(lt(on\\,{third})\\,{z1}\\,if(lt(on\\,{two_thirds})\\,1.15\\,{z3}))"
        # Continuous pan across middle phase: smooth sine ease without discontinuous teleport
        x_pan = f"(1-cos(2*PI*(on-{third})/{third}))/2"
        x_expr = f"if(lt(on\\,{third})\\,(iw-iw/zoom)/2\\,if(lt(on\\,{two_thirds})\\,(iw-iw/zoom)*{x_pan}\\,(iw-iw/zoom)/2))"
        y_expr = "(ih-ih/zoom)/2"
        zoompan = f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={frames}{smooth_scale}"
        vf = f"{cover_scale_filter()},{zoompan}{smooth_downscale}"
        use_zoompan = True

    elif template_name == "master_combo":
        half = max(1, frames // 2)
        z1 = _zexpr_rise(1.0, 1.18, 'on', half)
        z2 = _zexpr_fall(1.0, 1.18, f'on-{half}', frames - half)
        z_expr = f"if(lt(on\\,{half})\\,{z1}\\,{z2})"
        ease = _zexpr_rise(0, 1, 'on', frames)
        zoompan = f"zoompan=z='{z_expr}':x='(iw-iw/zoom)*{ease}':y='(ih-ih/zoom)*{ease}':d={frames}{smooth_scale}"
        vf = f"{cover_scale_filter()},{zoompan}{smooth_downscale}"
        use_zoompan = True
    
    else:
        vf = contain_filter()
        use_zoompan = False
    
    input_args = ['-loop', '1', '-framerate', str(FPS), '-i', str(img_path)]

    def _build_image_cmd(use_loop=True):
        local_input_args = (
            ['-f', 'image2', '-loop', '1', '-framerate', str(FPS), '-i', str(ffmpeg_img_path)]
            if use_loop
            else ['-f', 'image2', '-framerate', str(FPS), '-i', str(ffmpeg_img_path)]
        )
        effective_vf = vf
        if not use_loop and not use_zoompan:
            effective_vf = f"{vf},fps={FPS},tpad=stop_mode=clone:stop_duration={float(duration):.3f}"
        cmd = [
            ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-nostdin',
            *local_input_args,
            '-vf', effective_vf,
            '-t', str(duration),
        ]
        if not use_zoompan:
            cmd.extend(['-r', str(FPS)])
        cmd.extend([
            '-c:v', 'libx264',
            '-threads', '0',
            '-preset', str(PRESET),
            '-crf', str(CRF),
            '-pix_fmt', 'yuv420p',
            str(output_path),
        ])
        return cmd

    last_stderr = ""
    for _use_loop in (False,):
        try:
            result = subprocess.run(
                _build_image_cmd(use_loop=_use_loop),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                timeout=300,
            )
            if result.returncode == 0 and os.path.exists(output_path):
                return output_path
            last_stderr = result.stderr.decode(errors='replace') if result.stderr else ""
            if _use_loop and ("Option loop not found" in last_stderr or "Option not found" in last_stderr):
                continue
            break
        except subprocess.TimeoutExpired:
            print(f"   Timeout on {template_name}")
            break
        except Exception as e:
            print(f"   Error rendering {template_name}: {e}")
            break
    if last_stderr:
        print(f"   {template_name} failed:\n{last_stderr}")
    return render_static_fallback(img_path, duration, output_path)

    # CPU-OPTIMIZED command
    if use_zoompan:
        cmd = [
            ffmpeg, '-threads', '0',  # Use ALL CPU cores
            '-y', '-hide_banner', '-loglevel', 'error', '-nostdin',
            *input_args,
            '-vf', vf,
            '-t', str(duration),
            '-c:v', 'libx264',
            '-preset', str(PRESET),
            '-crf', str(CRF),
            '-pix_fmt', 'yuv420p',
            str(output_path)
        ]
    else:
        # Static - even simpler
        cmd = [
            ffmpeg, '-threads', '0',
            '-y', '-hide_banner', '-loglevel', 'error', '-nostdin',
            *input_args,
            '-vf', vf,
            '-t', str(duration),
            '-r', str(FPS),
            '-c:v', 'libx264', '-preset', str(PRESET), '-crf', str(CRF),
            '-pix_fmt', 'yuv420p',
            str(output_path)
        ]
    
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=300
        )
        
        if result.returncode == 0 and os.path.exists(output_path):
            return output_path
        else:
            stderr = result.stderr.decode() if result.stderr else ""
            print(f"   ⚠️ {template_name} failed:\n{stderr}")
    except subprocess.TimeoutExpired:
        print(f"   ⚠️ Timeout on {template_name}")
    except Exception as e:
        print(f"   ⚠️ Error: {e}")
    
    # Fallback to static
    return render_static_fallback(img_path, duration, output_path)


def render_static_fallback(img_path, duration, output_path, use_loop=False):
    """Fast static fallback."""
    ffmpeg = get_ffmpeg_path()
    w, h = OUTPUT_SIZE
    ffmpeg_img_path = _prepare_image_for_ffmpeg(img_path, output_path)
    
    input_args = (
        ['-f', 'image2', '-loop', '1', '-framerate', str(FPS), '-i', str(ffmpeg_img_path)]
        if use_loop
        else ['-f', 'image2', '-framerate', str(FPS), '-i', str(ffmpeg_img_path)]
    )

    vf = f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1'
    if not use_loop:
        vf = f"{vf},fps={FPS},tpad=stop_mode=clone:stop_duration={float(duration):.3f}"

    cmd = [
        ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-nostdin',
        *input_args,
        '-vf', vf,
        '-t', str(duration),
        '-r', str(FPS),
        '-c:v', 'libx264', '-threads', '0', '-preset', str(PRESET), '-crf', str(CRF),
        '-pix_fmt', 'yuv420p',
        str(output_path)
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
        if result.returncode == 0 and os.path.exists(output_path):
            return output_path
        stderr = result.stderr.decode(errors='replace') if result.stderr else ""
        if use_loop and ("Option loop not found" in stderr or "Option not found" in stderr):
            return render_static_fallback(img_path, duration, output_path, use_loop=False)
    except Exception as e:
        print(f"   ⚠️ Static fallback failed: {e}")
    
    return None


# Legacy
def get_template_path(template_name, duration=None):
    return TEMPLATE_DIR / f"motion_{template_name}.ts"

def generate_motion_template(template_name, duration=None, force=False):
    return str(get_template_path(template_name, duration))

def get_animation_filter(pattern_name, duration):
    return f"scale={OUTPUT_SIZE[0]}:{OUTPUT_SIZE[1]}"


ensure_template_dir()
