"""Test: Native drawtext engine producing a 7-scene crime documentary."""
import sys, os, tempfile, subprocess, shutil, time, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from PIL import Image
from services.native_engine.timeline_compiler import TimelineCompiler, _drawtext_filter, _ffmpeg, _font_path
from services.native_engine.component_manifest import COMPONENTS

# Generate test backgrounds
tmp = tempfile.mkdtemp()
tints = [(18, 24, 36), (22, 30, 44), (16, 20, 32), (28, 34, 48),
         (14, 18, 28), (20, 26, 40), (24, 32, 46)]
for i, c in enumerate(tints):
    Image.new("RGB", (1920, 1080), c).save(os.path.join(tmp, f"bg_{i:03d}.png"))

# Copy fonts to tmp
fonts_src = ROOT / "fonts"
cwd = os.path.join(tmp, "fonts")
os.makedirs(cwd, exist_ok=True)
for f in os.listdir(fonts_src):
    if f.endswith(".ttf"):
        shutil.copy2(str(fonts_src / f), os.path.join(cwd, f))

started = time.monotonic()

# Build timeline
compiler = TimelineCompiler(1920, 1080, 30)

scenes = [
    {"dur": 3.0, "bg": "bg_000.png", "text": "THE GARDNER HEIST", "kicker": "BOSTON · MARCH 1990", "font": "BebasNeue-Regular.ttf", "size": 88, "color": "#f4f0e6"},
    {"dur": 3.5, "bg": "bg_001.png", "text": "THIRTEEN MASTERPIECES", "detail": "Stolen in 81 minutes by thieves posing as police", "font": "BebasNeue-Regular.ttf", "size": 76, "color": "#f4f0e6"},
    {"dur": 3.0, "bg": "bg_002.png", "text": "$500 MILLION", "label": "LARGEST PROPERTY THEFT IN HISTORY", "font": "BebasNeue-Regular.ttf", "size": 110, "color": "#d62e3c"},
    {"dur": 3.5, "bg": "bg_003.png", "text": "35 YEARS UNSOLVED", "label": "EMPTY FRAMES STILL HANG IN THE MUSEUM", "font": "BebasNeue-Regular.ttf", "size": 100, "color": "#f4f0e6"},
    {"dur": 4.0, "bg": "bg_004.png", "text": "The trail went cold. The paintings vanished.", "attribution": "FBI Lead Investigator", "font": "PlayfairDisplay-Bold.ttf", "size": 48, "color": "#f4f0e6"},
    {"dur": 2.5, "bg": "bg_005.png", "text": "MARCH 18, 1990", "kicker": "THE NIGHT OF THE HEIST", "font": "BebasNeue-Regular.ttf", "size": 90, "color": "#f4f0e6"},
    {"dur": 3.0, "bg": "bg_006.png", "text": "THE CASE REMAINS OPEN", "detail": "A $10M reward still stands for information", "font": "BebasNeue-Regular.ttf", "size": 72, "color": "#f4f0e6"},
]

scene_labels = []
cum_offset = 0.0

for i, sc in enumerate(scenes):
    bg_path = os.path.join(tmp, sc["bg"])
    label = compiler.add_media_input(bg_path, sc["dur"])
    
    # Main text
    dt = _drawtext_filter(
        text=sc["text"],
        font_file=sc.get("font", "BebasNeue-Regular.ttf"),
        font_size=sc["size"],
        color=sc.get("color", "#f4f0e6"),
        y="(h-text_h)/2" if not sc.get("kicker") else "(h-text_h)/2-40",
        shadow=True,
    )
    
    current_label = f"s{i}"
    
    if sc.get("kicker"):
        # Kicker above main text
        k_label = f"k{i}"
        k_dt = _drawtext_filter(
            text=sc["kicker"],
            font_file="Poppins-Bold.ttf",
            font_size=24,
            color="#d62e3c",
            y="(h-text_h)/2-120",
            shadow=False,
        )
        compiler.filter_parts.append(f"[{label}]{k_dt}[{k_label}]")
        compiler.filter_parts.append(f"[{k_label}]{dt}[{current_label}]")
    elif sc.get("detail"):
        # Detail below main text
        d_label = f"d{i}"
        d_dt = _drawtext_filter(
            text=sc["detail"],
            font_file="Poppins-Regular.ttf",
            font_size=22,
            color="#f4f0e6",
            y="(h-text_h)/2+100",
            shadow=False,
        )
        compiler.filter_parts.append(f"[{label}]{dt}[{d_label}]")
        compiler.filter_parts.append(f"[{d_label}]{d_dt}[{current_label}]")
    elif sc.get("label"):
        # Label below main text  
        lb_label = f"l{i}"
        lb_dt = _drawtext_filter(
            text=sc["label"],
            font_file="Poppins-Bold.ttf",
            font_size=24,
            color="#d62e3c",
            y="(h-text_h)/2+90",
            shadow=False,
        )
        compiler.filter_parts.append(f"[{label}]{dt}[{lb_label}]")
        compiler.filter_parts.append(f"[{lb_label}]{lb_dt}[{current_label}]")
    elif sc.get("attribution"):
        # Attribution below quote
        a_label = f"a{i}"
        a_dt = _drawtext_filter(
            text=f"— {sc['attribution']}",
            font_file="Poppins-Regular.ttf",
            font_size=20,
            color="#d62e3c",
            y="(h-text_h)/2+100",
            shadow=False,
        )
        compiler.filter_parts.append(f"[{label}]{dt}[{a_label}]")
        compiler.filter_parts.append(f"[{a_label}]{a_dt}[{current_label}]")
    else:
        compiler.filter_parts.append(f"[{label}]{dt}[{current_label}]")
    
    scene_labels.append((current_label, sc["dur"]))

# Build xfade chain
last = scene_labels[0][0]
total_dur = scene_labels[0][1]
x_offset = scene_labels[0][1]

for i in range(1, len(scene_labels)):
    prev = last
    cur, dur = scene_labels[i]
    xf_label = f"xf{i}"
    # xfade offset: scene start minus overlap, overlap = 0.5s
    overlap = min(0.5, dur * 0.15, scene_labels[i-1][1] * 0.15)
    offset = x_offset - overlap
    compiler.filter_parts.append(
        f"[{prev}][{cur}]xfade=transition=fade:duration={overlap:.3f}:offset={offset:.3f}[{xf_label}]"
    )
    last = xf_label
    x_offset += dur - overlap
    total_dur = x_offset

fc = compiler.build_filter_complex()
print(f"[native] {len(scenes)} scenes, {total_dur:.1f}s total")
print(f"[native] Filter complex: {len(fc)} chars")

# Build FFmpeg command
ffmpeg = _ffmpeg()
out = str(ROOT / "output" / "native_crime_test.mp4")

cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "warning"]
for inp in compiler.inputs:
    cmd.extend(inp)
cmd.extend(["-filter_complex", fc])
cmd.extend(["-map", f"[{last}]"])
cmd.extend(["-t", f"{total_dur:.3f}"])
cmd.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out])

print(f"[native] Running FFmpeg...")
r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=cwd)

elapsed = time.monotonic() - started

if r.returncode == 0 and os.path.exists(out):
    sz = os.path.getsize(out) / (1024 * 1024)
    print(f"[native] SUCCESS in {elapsed:.0f}s ({sz:.1f} MB) → {out}")
    
    # Verify with frame samples
    for fn, label in [(45, 'scene1'), (150, 'scene2'), (260, 'scene3'), (360, 'scene4')]:
        fp = os.path.join(tmp, f"vfy_{label}.png")
        subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", out,
            "-vf", "select=eq(n\\,%d)" % fn, "-vframes", "1", fp], capture_output=True)
        im = Image.open(fp)
        bright = sum(1 for y in range(0, 1080, 10) for x in range(0, 1920, 10) if im.getpixel((x, y))[0] > 150)
        print(f"  {label} (frame {fn}): {bright} bright text pixels")
else:
    print(f"[native] FAILED after {elapsed:.0f}s")
    for line in (r.stderr or "").splitlines():
        if "error" in line.lower() or "fail" in line.lower() or "cannot" in line.lower():
            print(f"  {line[:180]}")
