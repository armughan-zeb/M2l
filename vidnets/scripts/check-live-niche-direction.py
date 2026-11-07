"""Exercise real chunked direction, preserving plans and timing without rendering."""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
for file in ('.env.public', '.env.defaults', '.env'):
    load_dotenv(ROOT / file, override=True)
from services.editorial_director.director import EditingDirector
from services.editing_profile import deterministic_profile

OUT = ROOT / 'artifacts' / 'niche_editing_revision'
OUT.mkdir(parents=True, exist_ok=True)
SCRIPTS = {
 'crime': ['The name on the ticket was Dan Cooper. An FBI investigation followed the hijacking.',
           'On November 24, 1971, he boarded Northwest Flight 305 from Portland to Seattle.',
           'He demanded four parachutes and two hundred thousand dollars in ransom.',
           'In 1980, a child found $5,800 in cash on the Columbia River shore.'],
 'travel': ['A travel itinerary follows the coastal road to the city of Lisbon.',
            'The journey moves from the city streets to the Atlantic coast.'],
 'science': ['A solar eclipse occurs when the Moon passes between the Sun and Earth.',
             'The Moon blocks the sunlight and its shadow reaches Earth.'],
}

def run(item):
    niche, scenes = item
    script = ' '.join(scenes)
    profile = deterministic_profile(niche + ' documentary', category=niche, script=script, variation_seed=17)
    started = time.monotonic()
    plan = EditingDirector(director_config={'director_scene_workers': 3}).plan_documentary(
        script, scenes, [5.0]*len(scenes), topic=niche+' documentary', category=niche,
        editing_profile=profile, variation_seed=17, width=854, height=480)
    payload = plan.to_dict()
    (OUT / f'{niche}-live-director.json').write_text(json.dumps(payload, indent=2, default=str), encoding='utf-8')
    manifest = plan.to_remotion_manifest()
    manifest['review_layout'] = True
    manifest['review_frames'] = [round((index*5 + 2)*manifest['fps']) for index in range(len(scenes))]
    (OUT / f'{niche}-live-manifest.json').write_text(json.dumps(manifest, indent=2, default=str), encoding='utf-8')
    summary = {'niche': niche, 'elapsed_seconds': round(time.monotonic()-started, 2),
               'metadata': payload.get('director') or payload.get('metadata'),
               'scene_count': len(payload.get('scenes') or []),
               'visual_types': [s.get('primary_visual', {}).get('type') for s in payload.get('scenes', [])],
               'copy_count': sum(bool(s.get('display_text')) for s in payload.get('scenes', []))}
    print(json.dumps(summary, default=str), flush=True)
    return summary

selected = {key: value for key, value in SCRIPTS.items() if len(sys.argv) < 2 or key in sys.argv[1:]}
with ThreadPoolExecutor(max_workers=3) as executor:
    summaries = list(executor.map(run, selected.items()))
(OUT / ('live-director-results.json' if len(sys.argv) < 2 else 'live-director-recheck.json')).write_text(json.dumps(summaries, indent=2, default=str), encoding='utf-8')
