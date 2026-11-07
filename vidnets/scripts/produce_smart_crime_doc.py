"""Industry-level crime documentary — uses all native engine libraries."""
import sys, os, time, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from PIL import Image
from services.native_engine.smart_composer import SmartComposer

# ── Generate test backgrounds (16 unique gradient images) ──
tmp = tempfile.mkdtemp()
palette = {
    "primary": "#f4f0e6", "accent": "#d62e3c", "danger": "#ff4455",
    "background": "#0a0a0f", "surface": "#1a1a25", "bg": "#0a0a0f",
}

tints = [
    (8, 10, 15), (12, 14, 20), (10, 8, 14), (15, 12, 18),
    (6, 8, 12), (14, 10, 16), (9, 11, 17), (11, 9, 13),
    (7, 9, 14), (13, 11, 19), (8, 12, 16), (10, 7, 13),
    (12, 9, 15), (14, 8, 11), (9, 10, 14), (11, 13, 18),
]
for i, c in enumerate(tints):
    img = Image.new("RGB", (1920, 1080), c)
    # Add subtle gradient for visual interest
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    for y in range(0, 1080, 60):
        draw.rectangle([0, y, 1920, y + 60], fill=tuple(min(255, v + y // 40) for v in c))
    img.save(os.path.join(tmp, f"bg_{i:03d}.png"))

# ── Build timeline JSON (what the LLM would generate) ──
timeline = {
    "scenes": [
        # Scene 0 — Cold Open / Chapter Title
        {
            "duration": 3.5,
            "background_asset": os.path.join(tmp, "bg_000.png"),
            "image_treatment": "slow_push_in",
            "template_id": "chapter_title",
            "animation": "scale_pop",
            "props": {"kicker": "BOSTON - ST PATRICKS DAY - 1990", "title": "THE GARDNER HEIST"},
            "palette": palette,
            "overlays": [], 
            "transition": {"id": "dip_black", "duration": 0.5},
        },
        # Scene 1 — Keyword Emphasis
        {
            "duration": 3.5,
            "background_asset": os.path.join(tmp, "bg_001.png"),
            "image_treatment": "slow_push_in",
            "template_id": "keyword_emphasis",
            "animation": "fade_in",
            "props": {"text": "THIRTEEN MASTERPIECES", "detail": "Stolen in 81 minutes by two men posing as Boston police officers"},
            "palette": palette,
            "overlays": [], 
            "transition": {"id": "wipe_right", "duration": 0.4},
        },
        # Scene 2 — Money Counter
        {
            "duration": 3.5,
            "background_asset": os.path.join(tmp, "bg_002.png"),
            "image_treatment": "parallax_push",
            "template_id": "money_counter",
            "animation": "scale_pop",
            "props": {"value": "500,000,000", "unit": "", "label": "LARGEST PROPERTY THEFT EVER RECORDED"},
            "palette": palette,
            "overlays": [], 
            "transition": {"id": "slide_left", "duration": 0.4},
        },
        # Scene 3 — Evidence Callout
        {
            "duration": 4.0,
            "background_asset": os.path.join(tmp, "bg_003.png"),
            "image_treatment": "tilt_up",
            "template_id": "evidence_callout",
            "animation": "slide_up",
            "props": {"stamp": "FBI CASE FILE", "text": "THE EMPTY FRAMES", "detail": "Still hanging in the museum today — a silent memorial to what was lost"},
            "palette": palette,
            "overlays": [], 
            "transition": {"id": "dissolve", "duration": 0.4},
        },
        # Scene 4 — Stat Counter (35 years)
        {
            "duration": 3.5,
            "background_asset": os.path.join(tmp, "bg_004.png"),
            "image_treatment": "parallax_push",
            "template_id": "stat_counter",
            "animation": "scale_pop",
            "props": {"value": "35", "label": "YEARS WITHOUT ANSWERS"},
            "palette": palette,
            "overlays": [], 
            "transition": {"id": "dip_black", "duration": 0.5},
        },
        # Scene 5 — Quote Card
        {
            "duration": 4.5,
            "background_asset": os.path.join(tmp, "bg_005.png"),
            "image_treatment": "slow_push_in",
            "template_id": "quote_card",
            "animation": "fade_in",
            "props": {"quote": "Whoever has those paintings knows they cannot sell them. But they also cannot let them go.", "attribution": "Anthony Amore, Director of Security"},
            "palette": palette,
            "overlays": [], 
            "transition": {"id": "dissolve", "duration": 0.6},
        },
        # Scene 6 — Redaction / Classified
        {
            "duration": 3.5,
            "background_asset": os.path.join(tmp, "bg_006.png"),
            "image_treatment": "diagonal_pan",
            "template_id": "redaction_reveal",
            "animation": "slide_up",
            "props": {"text": "CASE REMAINS OPEN", "detail": "A $10 million reward still stands for information leading to the recovery"},
            "palette": palette,
            "overlays": [],
            "transition": {"id": "dip_black", "duration": 0.5},
        },
        # Scene 7 — Source Citation
        {
            "duration": 3.5,
            "background_asset": os.path.join(tmp, "bg_007.png"),
            "image_treatment": "pan_left",
            "template_id": "source_citation",
            "animation": "fade_in",
            "props": {"text": "The statute of limitations expired in 1995. No one can be prosecuted. The paintings are classified as stolen property in perpetuity.", "source": "FBI Art Crime Team · Boston Field Office"},
            "palette": palette,
            "overlays": [], 
            "transition": {"id": "dip_black", "duration": 0.5},
        },
        # Scene 8 — End Card
        {
            "duration": 4.0,
            "background_asset": os.path.join(tmp, "bg_008.png"),
            "image_treatment": "slow_push_in",
            "template_id": "end_card",
            "animation": "scale_pop",
            "props": {"title": "THE CASE REMAINS OPEN", "subtitle": "IF YOU HAVE INFORMATION · CALL THE FBI TIPLINE"},
            "palette": palette,
            "overlays": [], 
            "transition": {"id": "dip_black", "duration": 0.0},
        },
    ],
}

# ── Render ──
print(f"[smart] Building {len(timeline['scenes'])} scene crime documentary...")
started = time.monotonic()

composer = SmartComposer(1920, 1080, 30)
output = str(ROOT / "output" / "smart_crime_doc.mp4")

result = composer.render(timeline, output)
elapsed = time.monotonic() - started

print(f"\n[smart] Complete in {elapsed:.0f}s")
print(f"[smart] Output: {result['output_path']}")
print(f"[smart] Size: {result['size_mb']} MB")
print(f"[smart] Resolution: {result['resolution']}")
print(f"[smart] Render time: {result['render_seconds']}s")
print(f"[smart] Scenes: {result['scenes']}")
