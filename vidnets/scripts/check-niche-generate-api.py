"""Run one short real generation, recording requests, responses and elapsed time."""
import json
import shutil
import sys
import time
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'niche_editing_revision' / 'api-smoke'
OUT.mkdir(parents=True, exist_ok=True)
source = ROOT / 'artifacts' / 'crime_story_test_20261005' / 'request.json'
original = json.loads(source.read_text(encoding='utf-8'))
config = dict(original['config'])
for field in ('mixed_media_weights', 'min_web_image_ratio'):
    config.pop(field, None)
script = ('Dan Cooper was the name used by the hijacker later called D.B. Cooper. '
          'On November 24, 1971, he boarded Northwest Flight 305 from Portland to Seattle. '
          'He demanded four parachutes and $200,000 in ransom.')
fresh = '--fresh' in sys.argv
name = 'Adaptive Crime Pipeline Final Test' if fresh else 'Adaptive Crime Pipeline Smoke Test'
config.update({'title': name, 'content_title': 'D.B. Cooper FBI hijacking investigation', 'output_name': name,
               'category': 'crime', 'graphic_density': .32, 'delivery_budget_seconds': 420,
               'media_deadline_seconds': 120, 'visual_deadline_seconds': 90, 'render_reserve_seconds': 90})
payload = {'title': config['content_title'], 'script': script, 'output_name': name,
           'render_resolution': [854, 480], 'config': config}
retrying = len(sys.argv) > 1 and not fresh
if retrying:
    package = Path(sys.argv[1]).resolve()
    package.relative_to(ROOT / 'output')
    payload = {**config, **{key: value for key, value in payload.items() if key != 'config'}, 'output_package_dir': str(package)}
suffix = '-final' if fresh else '-retry' if retrying else ''
(OUT / f'request{suffix}.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
started = time.monotonic()
record = {'endpoint': 'http://127.0.0.1:5000/api/generate', 'request_file': str(OUT / 'request.json')}
try:
    response = requests.post(record['endpoint'], json=payload, timeout=900)
    record['http_status'] = response.status_code
    record['response'] = response.json()
except Exception as exc:
    record['transport_error'] = f'{type(exc).__name__}: {exc}'
record['elapsed_seconds'] = round(time.monotonic() - started, 2)
(OUT / f'response{suffix}.json').write_text(json.dumps(record, indent=2, default=str), encoding='utf-8')
print(json.dumps({key:record[key] for key in ['http_status','elapsed_seconds','transport_error'] if key in record}), flush=True)
for directory in (ROOT / 'output').glob('*'):
    if directory.is_dir() and name.casefold() in directory.name.casefold():
        shutil.copytree(directory, OUT / ('generation-package-final' if fresh else 'generation-package'), dirs_exist_ok=True)
if record.get('http_status') != 200:
    raise SystemExit(1)
