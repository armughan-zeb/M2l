"""Check the running app handoff and preserve a reviewable response."""
import json
import time
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'niche_editing_revision'
request = {
    'title': 'D.B. Cooper: An FBI Hijacking Investigation', 'category': 'crime',
    'script': 'Dan Cooper was the name used by the hijacker later called D.B. Cooper. On November 24, 1971, he boarded an aircraft from Portland to Seattle. The FBI investigated the ransom demand.',
    'fps': 24, 'width': 854, 'height': 480, 'variation_seed': 8,
    'director_scene_workers': 3,
}
(OUT / 'director-api-request.json').write_text(json.dumps(request, indent=2), encoding='utf-8')
started = time.monotonic()
response = requests.post('http://127.0.0.1:5000/api/director/plan', json=request, timeout=160)
response.raise_for_status()
payload = response.json()
(OUT / 'director-api-response.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
assert payload.get('success'), payload.get('error')
plan = payload['edit_plan']
assert plan['director']['niche'] == 'crime'
assert len(plan['scenes']) == 3, 'Initials must not become standalone scenes'
assert all(scene['text']['frame_contract_validation']['status'] in {'pass', 'repaired'} for scene in plan['scenes'])
summary = {'http_status': response.status_code, 'elapsed_seconds': round(time.monotonic()-started, 2),
           'niche': plan['director']['niche'], 'scene_count': len(plan['scenes']),
           'palette': plan['visual_dna']['palette'], 'sound_cue_count': payload['sound_cue_count'],
           'visual_types': [scene['primary_visual']['type'] for scene in plan['scenes']]}
(OUT / 'director-api-summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps(summary))
