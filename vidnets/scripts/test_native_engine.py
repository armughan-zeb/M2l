"""Test the Native Documentary Composition Engine with a crime documentary."""
import json, os, sys, time, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from PIL import Image
from services.native_engine.renderer import render_documentary
from services.native_engine.component_manifest import get_llm_component_catalog

# Generate test background images
tmp = tempfile.mkdtemp()
for i in range(8):
    tints = [(18, 24, 36), (22, 30, 44), (16, 20, 32), (28, 34, 48), 
             (14, 18, 28), (20, 26, 40), (24, 32, 46), (12, 16, 26)]
    img = Image.new("RGB", (1920, 1080), tints[i % len(tints)])
    fp = os.path.join(tmp, f"bg_{i:03d}.png")
    img.save(fp)

# Build timeline JSON (what the LLM would generate)
timeline = {
    "version": 1,
    "fps": 30,
    "width": 1920,
    "height": 1080,
    "scenes": [
        {
            "scene_index": 0,
            "duration": 3.0,
            "background_asset": os.path.join(tmp, "bg_000.png"),
            "image_treatment": "slow_push_in",
            "components": [
                {"id": "chapter_title", "text": "THE GARDNER HEIST", "kicker": "BOSTON · 1990", "anim": "scale_pop",
                 "font_size": 88, "font_file": "BebasNeue-Regular.ttf", "color": "#f4f0e6", "accent_color": "#d62e3c"},
            ],
            "transition": {"id": "dip_black", "duration": 0.4},
        },
        {
            "scene_index": 1,
            "duration": 3.5,
            "background_asset": os.path.join(tmp, "bg_001.png"),
            "image_treatment": "slow_push_in",
            "components": [
                {"id": "keyword_emphasis", "text": "THIRTEEN MASTERPIECES", "detail": "Stolen in eighty-one minutes by thieves posing as police",
                 "font_size": 80, "font_file": "BebasNeue-Regular.ttf", "color": "#f4f0e6", "accent_color": "#d62e3c"},
            ],
            "transition": {"id": "flash_cut", "duration": 0.2},
        },
        {
            "scene_index": 2,
            "duration": 3.0,
            "background_asset": os.path.join(tmp, "bg_002.png"),
            "image_treatment": "pan_right",
            "components": [
                {"id": "money_counter", "value": 500000000, "label": "TOTAL VALUE STOLEN", "prefix": "$", "unit": "M",
                 "font_size": 110, "font_file": "BebasNeue-Regular.ttf", "color": "#d62e3c", "label_color": "#f4f0e6"},
            ],
            "transition": {"id": "slide_left", "duration": 0.3},
        },
        {
            "scene_index": 3,
            "duration": 3.5,
            "background_asset": os.path.join(tmp, "bg_003.png"),
            "image_treatment": "slow_push_in",
            "components": [
                {"id": "stat_counter", "value": 35, "label": "YEARS UNSOLVED", 
                 "font_size": 120, "font_file": "BebasNeue-Regular.ttf", "color": "#f4f0e6", "accent_color": "#d62e3c"},
            ],
            "transition": {"id": "dip_black", "duration": 0.4},
        },
        {
            "scene_index": 4,
            "duration": 4.0,
            "background_asset": os.path.join(tmp, "bg_004.png"),
            "image_treatment": "pan_left",
            "components": [
                {"id": "quote_card", "quote": "The empty frames still hang in the museum, waiting for the return of the masterpieces", "attribution": "FBI Special Agent",
                 "font_size": 48, "font_file": "PlayfairDisplay-Bold.ttf", "color": "#f4f0e6", "accent_color": "#d62e3c"},
            ],
            "transition": {"id": "light_leak_transition", "duration": 0.5},
        },
        {
            "scene_index": 5,
            "duration": 2.5,
            "background_asset": os.path.join(tmp, "bg_005.png"),
            "image_treatment": "static_hold",
            "components": [
                {"id": "date_reveal", "date": "MARCH 18, 1990", "kicker": "THE NIGHT OF THE HEIST",
                 "font_size": 90, "font_file": "BebasNeue-Regular.ttf", "color": "#f4f0e6", "accent_color": "#d62e3c"},
            ],
            "transition": {"id": "dip_black", "duration": 0.6},
        },
        {
            "scene_index": 6,
            "duration": 3.0,
            "background_asset": os.path.join(tmp, "bg_006.png"),
            "image_treatment": "slow_push_in",
            "components": [
                {"id": "keyword_emphasis", "text": "THE CASE REMAINS OPEN", "detail": "A $10 million reward still stands for information",
                 "font_size": 76, "font_file": "BebasNeue-Regular.ttf", "color": "#f4f0e6", "accent_color": "#d62e3c"},
            ],
            "transition": {"id": "dip_black", "duration": 0.5},
        },
    ],
    "voiceover": {"path": "", "src": ""},
    "background_music": {"path": "", "src": ""},
}

output = os.path.join(tmp, "native_crime_test.mp4")

print(f"[native] Rendering {len(timeline['scenes'])} scene documentary...")
started = time.monotonic()

result = render_documentary(
    timeline=timeline,
    output_path=output,
    mode="final",
)

elapsed = time.monotonic() - started
print(f"\n[native] Total: {elapsed:.0f}s")
print(f"[native] Output: {output}")
print(f"[native] Resolution: {result['resolution']}")
print(f"[native] Size: {result['size_mb']} MB")

# Verify
if os.path.exists(output):
    sz = os.path.getsize(output)
    print(f"\n[native] SUCCESS - video at {output} ({sz/1024/1024:.1f} MB)")
else:
    print(f"\n[native] FAILED - no output file")
