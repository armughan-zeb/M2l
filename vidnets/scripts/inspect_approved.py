import json
import glob
import sys

path = sys.argv[1] if len(sys.argv) > 1 else 'output/Approved#1/The Ice Bowl - One Yard, One Decision_remotion_edit_plan.json'
plans = glob.glob(path) if '*' in path else [path]
for p in plans:
    print('===', p, '===')
    with open(p, encoding='utf-8') as f:
        d = json.load(f)
    scenes = d.get('scenes', [])
    print(f'total scenes: {len(scenes)}')
    for i, s in enumerate(scenes):
        pv = s.get('primary_visual', {})
        gr = s.get('graphic', {})
        txt = s.get('text', {})
        ec = s.get('editorial_contract', {})
        m = s.get('media', {})
        print(f"Scene {i}: pv={pv.get('type')}, pv_treat={pv.get('treatment')}, gr={gr.get('type')}, gr_var={gr.get('variant')}, txt_style={txt.get('panel_style')}, text={ec.get('display_text') or txt.get('highlight')}, media_src={m.get('source')}, media_path={m.get('path', '')[-30:]}")
