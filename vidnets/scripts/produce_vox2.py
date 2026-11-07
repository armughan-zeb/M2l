"""Vox-style crime documentary — proper spacing, accent bars, transitions."""
import sys, os, time, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from PIL import Image
from services.native_engine.vox_composer import VoxComposer

tmp = tempfile.mkdtemp()
tints = [(8,10,15),(12,14,20),(10,8,14),(15,12,18),(6,8,12),(14,10,16),
         (9,11,17),(11,9,13),(7,9,14),(13,11,19)]
for i, c in enumerate(tints):
    Image.new("RGB", (1920, 1080), c).save(os.path.join(tmp, f"bg_{i:03d}.png"))

palette = {
    "primary": "#f4f0e6", "accent": "#d62e3c", "danger": "#ef6f6c",
    "background": "#0a0a0f", "surface": "#16263b",
}

data = [
    (3.0, "chapter_title",   {"kicker":"BOSTON MASSACHUSETTS","title":"THE GARDNER HEIST"},          "dip_black", 0.5),
    (3.5, "keyword_emphasis",{"text":"THIRTEEN MASTERPIECES","detail":"Stolen in 81 minutes by two men in police uniforms"}, "wipe_right", 0.4),
    (3.0, "money_counter",   {"value":"500","unit":"M","label":"DOLLAR PROPERTY THEFT IN HISTORY"},    "slide_left", 0.4),
    (3.0, "stat_counter",    {"value":"35","label":"YEARS WITHOUT ANSWERS"},                          "dip_black", 0.5),
    (3.5, "keyword_emphasis",{"text":"THE EMPTY FRAMES","detail":"Still hanging in the museum today, a silent memorial"}, "dissolve", 0.5),
    (4.0, "quote_card",      {"quote":"Whoever has those paintings knows they cannot sell them. But they also cannot let them go.","attribution":"Anthony Amore, Security Director"}, "dip_black", 0.6),
    (3.0, "stat_counter",    {"value":"10","label":"MILLION DOLLAR REWARD FOR INFORMATION"},          "dissolve", 0.4),
    (3.0, "keyword_emphasis",{"text":"THE CASE REMAINS OPEN","detail":"A $10M reward still stands for any information"}, "wipe_right", 0.4),
    (3.5, "end_card",        {"title":"THE CASE REMAINS OPEN","subtitle":"CALL THE FBI TIPLINE"},     "dip_black", 0.0),
]

scenes = []
for i, (dur, tid, props, trans_id, trans_dur) in enumerate(data):
    scenes.append({
        "duration": dur,
        "background_asset": os.path.join(tmp, f"bg_{i:03d}.png"),
        "image_treatment": "static_hold",
        "template_id": tid,
        "props": props,
        "palette": palette,
        "transition": {"id": trans_id, "duration": trans_dur},
    })

total = sum(s["duration"] for s in scenes)
print(f"[vox] {len(scenes)} scenes, {total:.0f}s raw")
print(f"[vox] Rendering...")

started = time.monotonic()
composer = VoxComposer(1920, 1080, 30)
output = str(ROOT / "output" / "vox_crime_doc.mp4")

try:
    result = composer.render(timeline={"scenes": scenes}, output_path=output)
    elapsed = time.monotonic() - started
    print(f"\n[vox] DONE in {elapsed:.0f}s")
    print(f"[vox] Video: {output}")
    print(f"[vox] Size: {result['size_mb']} MB")
    print(f"[vox] Render: {result['render_seconds']}s")
except Exception as e:
    elapsed = time.monotonic() - started
    print(f"\n[vox] FAILED after {elapsed:.0f}s: {e}")
