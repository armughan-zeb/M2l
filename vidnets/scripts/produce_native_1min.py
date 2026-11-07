"""Quick 1-minute native documentary test."""
import sys, os, time, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from PIL import Image
from services.native_engine.smart_composer import SmartComposer

tmp = tempfile.mkdtemp()
tints = [(8,10,15),(12,14,20),(10,8,14),(15,12,18),(6,8,12),(14,10,16),
         (9,11,17),(11,9,13),(7,9,14),(13,11,19),(8,12,16),(10,7,13)]
for i, c in enumerate(tints):
    Image.new("RGB", (1920, 1080), c).save(os.path.join(tmp, f"bg_{i:03d}.png"))

palette = {"primary": "#f4f0e6", "accent": "#d62e3c", "danger": "#ff4455", "bg": "#0a0a0f"}

# Each scene: (duration, template_id, props_dict, transition_name, transition_duration)
data = [
    (3.5, "chapter_title",    {"kicker":"BOSTON - MARCH 1990","title":"THE GARDNER HEIST"}, "dip_black", 0.5),
    (3.5, "keyword_emphasis", {"text":"THIRTEEN MASTERPIECES","detail":"Stolen in 81 minutes by men posing as police"}, "wipe_right", 0.4),
    (3.0, "money_counter",    {"value":"500","unit":"M","label":"LARGEST PROPERTY THEFT EVER RECORDED"}, "slide_left", 0.4),
    (3.5, "stat_counter",     {"value":"35","label":"YEARS WITHOUT ANSWERS"}, "dip_black", 0.5),
    (4.0, "keyword_emphasis", {"text":"THE EMPTY FRAMES","detail":"Still hanging in the museum today - a silent memorial"}, "dissolve", 0.5),
    (4.5, "quote_card",       {"quote":"Whoever has those paintings knows they cannot sell them. But they also cannot let them go.","attribution":"Anthony Amore, Security Director"}, "dip_black", 0.6),
    (3.0, "stat_counter",     {"value":"10","label":"MILLION DOLLAR REWARD FOR INFORMATION"}, "dissolve", 0.4),
    (3.5, "keyword_emphasis", {"text":"THE CASE REMAINS OPEN","detail":"The statute of limitations expired but the hunt continues"}, "wipe_right", 0.4),
    (4.0, "end_card",         {"title":"THE GARDNER HEIST","subtitle":"IF YOU HAVE INFORMATION - CALL THE FBI"}, "dip_black", 0.0),
]

scenes = []
for i, (dur, tid, props, trans_id, trans_dur) in enumerate(data):
    scenes.append({
        "duration": dur,
        "background_asset": os.path.join(tmp, f"bg_{i:03d}.png"),
        "image_treatment": "static_hold",
        "template_id": tid,
        "animation": "slide_up",
        "props": props,
        "palette": palette,
        "overlays": [],
        "transition": {"id": trans_id, "duration": trans_dur},
    })

total_raw = sum(s["duration"] for s in scenes)
print(f"[native] {len(scenes)} scenes, {total_raw:.0f}s raw")
print(f"[native] Rendering...")

started = time.monotonic()
composer = SmartComposer(1920, 1080, 30)
output = str(ROOT / "output" / "native_1min_test.mp4")

try:
    result = composer.render(timeline={"scenes": scenes}, output_path=output)
    elapsed = time.monotonic() - started
    print(f"\n[native] DONE in {elapsed:.0f}s")
    print(f"[native] Video: {output}")
    print(f"[native] Size: {result['size_mb']} MB")
    print(f"[native] Resolution: {result['resolution']}")
    print(f"[native] Render: {result['render_seconds']}s")
except Exception as e:
    elapsed = time.monotonic() - started
    print(f"\n[native] FAILED after {elapsed:.0f}s: {e}")
