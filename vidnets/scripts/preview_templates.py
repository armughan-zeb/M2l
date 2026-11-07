"""Preview all Vox templates in one MP4 — 2s per template with label."""
import sys, os, time, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from PIL import Image
from services.native_engine.vox_composer import VoxComposer

tmp = tempfile.mkdtemp()
tints = [(8,10,15),(12,14,20),(10,8,14),(15,12,18),(6,8,12),(14,10,16),
         (9,11,17),(11,9,13)]
for i, c in enumerate(tints):
    Image.new("RGB", (1920, 1080), c).save(os.path.join(tmp, f"bg_{i:03d}.png"))

palette = {
    "primary": "#f4f0e6", "accent": "#d62e3c", "danger": "#ef6f6c",
    "background": "#0a0a0f", "surface": "#16263b",
}

# All 8 templates with varied props to show range
templates = [
    ("chapter_title",   {"kicker":"PREVIEW MODE", "title":"CHAPTER TITLE"}),
    ("keyword_emphasis",{"text":"BIG KEYWORD", "detail":"With supporting detail text below the main emphasis line"}),
    ("money_counter",   {"value":"500", "unit":"M", "label":"DOLLAR PROPERTY THEFT"}),
    ("stat_counter",    {"value":"8473", "label":"DOCUMENTS DECLASSIFIED"}),
    ("quote_card",      {"quote":"The empty frames still hang in the museum, waiting for the return of the masterpieces.","attribution":"FBI Lead Investigator"}),
    ("evidence_callout",{"stamp":"EVIDENCE","text":"THE RECORD","detail":"Classified documents revealed a pattern of deception"}),
    ("redaction_reveal",{"label":"CLASSIFIED","text":"THE CASE REMAINS OPEN","detail":"The statute of limitations expired"}),
    ("end_card",        {"title":"TEMPLATE PREVIEW","subtitle":"Vox Documentary Engine"}),
]

scenes = []
for i, (tid, props) in enumerate(templates):
    scenes.append({
        "duration": 2.5,
        "background_asset": os.path.join(tmp, f"bg_{i:03d}.png"),
        "image_treatment": "static_hold",
        "template_id": tid,
        "props": props,
        "palette": palette,
        "transition": {"id": "cut", "duration": 0.0},
    })

print(f"[preview] {len(scenes)} templates, 2.5s each")
print(f"[preview] Rendering...")

started = time.monotonic()
composer = VoxComposer(1920, 1080, 30)
output = str(ROOT / "output" / "template_previews.mp4")

result = composer.render(timeline={"scenes": scenes}, output_path=output)
elapsed = time.monotonic() - started

print(f"\n[preview] DONE in {elapsed:.0f}s")
print(f"[preview] Video: {output}")
print(f"[preview] Size: {result['size_mb']} MB")
print(f"[preview] {result['scenes']} templates at 1080p")
