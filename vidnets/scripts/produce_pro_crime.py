"""Pro crime documentary — 1 minute, full Vox-style with SFX."""
import sys, os, time, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from PIL import Image
from services.native_engine.pro_composer import ProComposer

tmp = tempfile.mkdtemp()
tints = [(8,10,15),(12,14,20),(10,8,14),(15,12,18),(6,8,12),(14,10,16),
         (9,11,17),(11,9,13),(7,9,14),(13,11,19)]
for i, c in enumerate(tints):
    Image.new("RGB", (1920, 1080), c).save(os.path.join(tmp, f"bg_{i:03d}.png"))

palette = {
    "primary": "#f4f0e6",
    "accent": "#d62e3c",
    "danger": "#ff334c",
    "background": "#0a0a0f",
    "surface": "#1a1a25",
}

# (duration, template, props, treatment, transition_id, transition_dur)
data = [
    (3.0, "chapter_title",     {"kicker":"BOSTON MASSACHUSETTS", "title":"THE GARDNER HEIST"}, "slow_push_in", "dip_black", 0.5),
    (3.5, "stat_counter",      {"value":"35","label":"YEARS WITHOUT ANSWERS"}, "slow_push_in", "wipe_right", 0.4),
    (3.5, "stat_counter",      {"value":"10","label":"MILLION DOLLAR REWARD FOR INFORMATION"}, "slow_push_in", "slide_left", 0.4),
    (3.5, "chapter_title",     {"kicker":"THE INVESTIGATION", "title":"$500 MILLION MISSING"}, "slow_push_in", "dip_black", 0.5),
    (4.0, "end_card",          {"title":"THE CASE REMAINS OPEN", "subtitle":"CALL THE FBI TIPLINE"}, "static_hold", "dip_black", 0.0),
]

scenes = []
for i, (dur, tid, props, treatment, trans_id, trans_dur) in enumerate(data):
    scenes.append({
        "duration": dur,
        "background_asset": os.path.join(tmp, f"bg_{i:03d}.png"),
        "image_treatment": treatment,
        "template_id": tid,
        "animation": "fade_in",
        "props": props,
        "palette": palette,
        "overlays": [],
        "transition": {"id": trans_id, "duration": trans_dur},
    })

timeline = {"scenes": scenes}
total = sum(s["duration"] for s in scenes)
print(f"[pro] {len(scenes)} scenes, {total:.0f}s raw duration")
print(f"[pro] Rendering with SFX + multi-layer templates...")

started = time.monotonic()
composer = ProComposer(1920, 1080, 30)
output = str(ROOT / "output" / "pro_crime_doc.mp4")

try:
    result = composer.render(timeline, output)
    elapsed = time.monotonic() - started
    print(f"\n[pro] DONE in {elapsed:.0f}s")
    print(f"[pro] Video: {output}")
    print(f"[pro] Size: {result['size_mb']} MB")
    print(f"[pro] Resolution: {result['resolution']}")
    print(f"[pro] Render: {result['render_seconds']}s")
except Exception as e:
    elapsed = time.monotonic() - started
    print(f"\n[pro] FAILED after {elapsed:.0f}s: {e}")
    import traceback
    traceback.print_exc()
