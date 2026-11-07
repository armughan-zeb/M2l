"""Quick test to verify GTA 6 Director planning, restraint, and chart/map selection."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.services.editorial_director import EditingDirector, detect_genre

SCENES = [
    "For more than a decade, the gaming world has waited for Grand Theft Auto VI.",
    "Rockstar Games shattered global records within 24 hours, racking up 93 million views for the debut trailer.",
    "Returning players to the neon streets of Vice City and the vast state of Leonida.",
    "Inspired by the legend of Bonnie and Clyde, protagonists Jason and Lucia lead a high-stakes crime saga.",
    "Next-generation physics bring bustling ocean shores, wild Everglades, and realistic vehicle handling to life.",
    "From high-speed highway pursuits to dynamic weather storms, the living world reacts to every criminal move.",
    "With an unprecedented development budget reaching 2 billion dollars, analysts project it to become the most expensive game in history.",
    'Rockstar Games declared "our goal is to push the boundaries of open-world interactive entertainment."',
    "Setting a revolutionary new benchmark, Grand Theft Auto VI is ready to redefine entertainment forever."
]

genre = detect_genre("Grand Theft Auto VI", "gaming", "Vice City Rockstar")
print(f"Detected Genre: {genre}")

director = EditingDirector()
plan = director.plan_documentary(
    script_text=" ".join(SCENES),
    scene_texts=SCENES,
    scene_durations=[6.5] * len(SCENES),
    topic="Grand Theft Auto VI",
    category="gaming",
)

print(f"Visual DNA Genre: {plan.visual_dna.genre}")
pal = plan.visual_dna.palette
acc = pal.get('accent') if isinstance(pal, dict) else pal.accent
sec = pal.get('secondary') if isinstance(pal, dict) else pal.secondary
print(f"Visual DNA Palette Accent: {acc}, Secondary: {sec}")
print("-" * 75)
for s in plan.scenes:
    props = s.primary_visual.props
    pv_type = s.primary_visual.type
    variant = s.primary_visual.variant
    card_theme = props.get("card_theme", "standard")
    title = props.get("title", "")
    print(f"Scene {s.scene_index}: [{pv_type:14}] | Variant: {variant:22} | Theme: {card_theme:15} | Title: {title}")
