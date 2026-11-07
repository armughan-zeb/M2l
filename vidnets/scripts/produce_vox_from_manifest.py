import json, os, glob, time, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from PIL import Image
from services.native_engine.vox_composer import VoxComposer

# Load the production manifest to get scene images
manifests = sorted(
    glob.glob(str(ROOT / "temp" / "manifests" / "*semantic_manifest*")),
    key=os.path.getmtime, reverse=True,
)

scenes_data = []
if manifests:
    with open(manifests[0]) as f:
        data = json.load(f)
    m = data.get("semantic_edit_manifest", data.get("manifest", data))
    for s in m.get("scenes", []):
        media = s.get("media", {})
        img = media.get("src", media.get("path", ""))
        if img and os.path.exists(img):
            scenes_data.append({
                "narration": s.get("narration", ""),
                "duration": s.get("duration", 3.0),
                "image": img,
            })

print(f"Found {len(scenes_data)} scenes with images")

if not scenes_data:
    # Generate fallback images
    tmp = tempfile.mkdtemp()
    tints = [(8,10,15),(12,14,20),(10,8,14),(15,12,18),(6,8,12),(14,10,16),(9,11,17),(11,9,13)]
    for i, c in enumerate(tints[:8]):
        fp = os.path.join(tmp, f"bg_{i:03d}.png")
        Image.new("RGB", (1920, 1080), c).save(fp)
        scenes_data.append({"narration": "", "duration": 3.0, "image": fp})

# Template rotation: chapter → keyword → stat → money → stat → keyword → quote → stat → end
templates = [
    "chapter_title", "keyword_emphasis", "stat_counter", "money_counter",
    "stat_counter", "keyword_emphasis", "quote_card", "stat_counter", "end_card",
]
props_list = [
    {"kicker":"BOSTON - 1990", "title":"THE GARDNER HEIST"},
    {"text":"THIRTEEN MASTERPIECES", "detail":"Stolen in 81 minutes by men in police uniforms"},
    {"value":"500", "label":"MILLION DOLLAR THEFT"},
    {"value":"500", "unit":"M", "label":"LARGEST PROPERTY THEFT EVER"},
    {"value":"35", "label":"YEARS WITHOUT ANSWERS"},
    {"text":"THE EMPTY FRAMES", "detail":"Still hanging in the museum as a silent memorial"},
    {"quote":"Whoever has those paintings cannot sell them. But they also cannot let them go.", "attribution":"Anthony Amore, Security Director"},
    {"value":"10", "label":"MILLION DOLLAR REWARD"},
    {"title":"THE CASE REMAINS OPEN", "subtitle":"CALL THE FBI TIPLINE"},
]

palette = {"primary": "#f4f0e6", "accent": "#d62e3c", "danger": "#ef6f6c", "background": "#0a0a0f", "surface": "#16263b"}

# Take first N scenes, apply templates in rotation
scenes = []
for i in range(min(len(scenes_data), len(templates))):
    scenes.append({
        "duration": scenes_data[i]["duration"],
        "background_asset": scenes_data[i]["image"],
        "image_treatment": "static_hold",
        "template_id": templates[i],
        "props": props_list[i],
        "palette": palette,
        "transition": {"id": "dissolve", "duration": 0.4},
    })

total = sum(s["duration"] for s in scenes)
print(f"Building {len(scenes)} scenes, {total:.0f}s with Vox templates")
print(f"Rendering...")

started = time.monotonic()
composer = VoxComposer(1920, 1080, 30)
output = str(ROOT / "output" / "gardner_vox_doc.mp4")

result = composer.render(timeline={"scenes": scenes}, output_path=output)
elapsed = time.monotonic() - started

print(f"\nDONE in {elapsed:.0f}s")
print(f"Video: {output}")
print(f"Size: {result['size_mb']} MB, {result['resolution']}, {result['scenes']} scenes")
