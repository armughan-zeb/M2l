import json

d = json.load(open(r'temp/storyboards/Grand Theft Auto VI is still.storyboard.json', 'r', encoding='utf-8'))
script_plan = d.get('script_plan', {})
scenes = script_plan.get('scenes', [])
scene_states = d.get('scene_states', {})
candidates = d.get('media_candidates', {})

print(f"Total plan scenes: {len(scenes)}")
for i, s in enumerate(scenes):
    sc_id = s.get('scene_id')
    text = s.get('narration_text', '')
    req = s.get('visual_requirement') or {}
    state = scene_states.get(sc_id, {})
    selected = state.get('selected_path', '')
    
    print(f"\n==========================================")
    print(f"Scene {i+1} [{sc_id}]: {text}")
    print(f"Visual Requirement:")
    print(f"  Subject: {req.get('subject')}")
    print(f"  Type: {req.get('subject_type')}")
    print(f"  Queries: {req.get('search_queries')}")
    print(f"Selected Media:")
    print(f"  Source: {state.get('selected_source')}")
    print(f"  Status: {state.get('status')}")
    print(f"  Verif: {state.get('verification_status')}")
    print(f"  Path: {selected}")

