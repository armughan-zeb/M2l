import json
from pathlib import Path

p = Path("temp/Antwerp_Diamond_Heist_Crime_Test_semantic.manifest.json")
if not p.exists():
    print("Manifest not found:", p)
    exit(1)

m = json.loads(p.read_text(encoding="utf-8"))
vd = m.get("visual_dna", {})
print("=" * 60)
print("VISUAL DNA AUDIT:")
print("Genre:", vd.get("genre"))
print("Palette:", json.dumps(vd.get("palette"), indent=2))
print("Motion Style:", vd.get("motion_style"))
print("Typography:", json.dumps(vd.get("typography"), indent=2))
print("=" * 60)

scenes = m.get("scenes", [])
print(f"TOTAL SCENES: {len(scenes)}")
for i, s in enumerate(scenes):
    dur = round(s.get("duration", 0), 2)
    contract = s.get("editorial_contract") or {}
    pv = s.get("primary_visual") or {}
    family = contract.get("family")
    enabled = contract.get("enabled")
    text = contract.get("display_text")
    emphasis = contract.get("emphasis_phrase")
    pv_type = pv.get("type")
    pv_var = pv.get("variant")
    narr = s.get("narration", "")
    print(f"\n--- SCENE {i + 1} ({dur}s) ---")
    print(f"   Narration: {narr}")
    print(f"   Primary Visual: {pv_type} ({pv_var})")
    print(f"   Editorial Contract: family={family}, enabled={enabled}")
    print(f"   Display Text: '{text}', Emphasis: '{emphasis}'")
    if "data" in contract or "data" in pv.get("props", {}):
        data = contract.get("data") or pv.get("props", {}).get("data")
        print(f"   Data Points: {data}")
    if "events" in contract or "events" in pv.get("props", {}):
        evs = contract.get("events") or pv.get("props", {}).get("events")
        print(f"   Timeline Events: {evs}")
    if "from" in contract or "to" in contract:
        print(f"   Route: {contract.get('from')} -> {contract.get('to')}")
