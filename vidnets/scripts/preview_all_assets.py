"""Preview ALL assets — fonts, templates, transitions, overlays, animations — in one MP4."""
import sys, os, time, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from PIL import Image
from services.native_engine.vox_composer import VoxComposer

tmp = tempfile.mkdtemp()
tints = [(8,10,15),(12,14,20),(10,8,14),(15,12,18),(6,8,12),(14,10,16),
         (9,11,17),(11,9,13),(7,9,14),(13,11,19),(8,12,16),(10,7,13),
         (12,9,15),(14,8,11),(16,20,26),(10,15,20),(18,22,30),(20,14,22),
         (11,17,23),(13,9,15),(15,11,19),(9,13,21),(14,10,18),(16,12,20)]
for i, c in enumerate(tints):
    Image.new("RGB", (1920, 1080), c).save(os.path.join(tmp, f"bg_{i:03d}.png"))

palette = {"primary": "#f4f0e6", "accent": "#d62e3c", "danger": "#ef6f6c",
           "background": "#0a0a0f", "surface": "#16263b"}

scenes = []

# ═══════════════════════════════════════════════════════════════════
# SECTION 1: ALL 15 FONTS (2s each)
# ═══════════════════════════════════════════════════════════════════
fonts = [
    "BebasNeue-Regular.ttf", "Poppins-Bold.ttf", "Poppins-Regular.ttf",
    "PlayfairDisplay-Bold.ttf", "Montserrat-Bold.ttf", "Anton-Regular.ttf",
    "Orbitron-Bold.ttf", "Cinzel-Bold.ttf", "Audiowide-Regular.ttf",
    "Righteous-Regular.ttf", "Bungee-Regular.ttf", "Caveat-Bold.ttf",
    "PatrickHand-Regular.ttf", "Creepster-Regular.ttf", "Nosifer-Regular.ttf",
]
for i, font in enumerate(fonts):
    name = font.replace(".ttf","").replace("-"," ")
    scenes.append({
        "duration": 2.0,
        "background_asset": os.path.join(tmp, f"bg_{i:03d}.png"),
        "image_treatment": "static_hold",
        "template_id": "chapter_title",
        "props": {"kicker": f"FONT {i+1}/15", "title": name,
                   "_font": font, "_body_font": "Poppins-Bold.ttf"},
        "palette": palette,
        "transition": {"id": "dissolve", "duration": 0.2},
    })

# ═══════════════════════════════════════════════════════════════════
# SECTION 2: ALL 8 TEMPLATES (2.5s each)
# ═══════════════════════════════════════════════════════════════════
templates = [
    ("chapter_title",    {"kicker":"CHAPTER TITLE","title":"Bold Cinematic Opener"}),
    ("keyword_emphasis", {"text":"KEYWORD EMPHASIS","detail":"Supporting detail line appears below the keyword"}),
    ("stat_counter",     {"value":"8473","label":"STAT COUNTER - Large Number"}),
    ("money_counter",    {"value":"500","unit":"M","label":"MONEY COUNTER"}),
    ("quote_card",       {"quote":"This is the quote card template for testimonial evidence and witness statements.","attribution":"Source Attribution"}),
    ("evidence_callout", {"stamp":"EVIDENCE","text":"EVIDENCE CALLOUT","detail":"Red accent bar with investigative stamp"}),
    ("redaction_reveal", {"label":"CLASSIFIED","text":"REDACTION REVEAL","detail":"Black bar with tension styling"}),
    ("end_card",         {"title":"END CARD TEMPLATE","subtitle":"Closing Call To Action"}),
]
for i, (tid, props) in enumerate(templates):
    scenes.append({
        "duration": 2.5,
        "background_asset": os.path.join(tmp, f"bg_{15+i:03d}.png"),
        "image_treatment": "static_hold",
        "template_id": tid,
        "props": props,
        "palette": palette,
        "transition": {"id": "dip_black", "duration": 0.3},
    })

# ═══════════════════════════════════════════════════════════════════
# SECTION 3: ALL TRANSITIONS (1.5s each x 8 pairs = 8 scenes)
# ═══════════════════════════════════════════════════════════════════
transitions = [
    "dissolve", "dip_black", "dip_white", "wipe_right",
    "wipe_left", "slide_left", "slide_right", "circle_reveal",
]
for i, trans in enumerate(transitions):
    scenes.append({
        "duration": 1.5,
        "background_asset": os.path.join(tmp, f"bg_{23+i:03d}.png"),
        "image_treatment": "static_hold",
        "template_id": "chapter_title",
        "props": {"kicker": f"TRANSITION {i+1}/8", "title": trans.upper().replace("_"," ")},
        "palette": palette,
        "transition": {"id": "dissolve", "duration": 0.2},
    })

total = sum(s["duration"] for s in scenes)
total_scenes = len(scenes)
print(f"[preview] {total_scenes} scenes across fonts/templates/transitions, {total:.0f}s total")
print(f"[preview] Rendering...")

started = time.monotonic()
composer = VoxComposer(1920, 1080, 30)
output = str(ROOT / "output" / "full_asset_preview.mp4")

result = composer.render(timeline={"scenes": scenes}, output_path=output)
elapsed = time.monotonic() - started

print(f"\n[preview] DONE in {elapsed:.0f}s")
print(f"[preview] Video: {output}")
print(f"[preview] Size: {result['size_mb']} MB")
print(f"[preview] Contains: 15 fonts + 8 templates + 8 transitions = {total_scenes} scenes")
