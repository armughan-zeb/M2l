"""Isolated live integration check against the shipped runtime, with private keys kept out of artifacts."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'release_validation_20261006' / ('live-generation-final' if '--fresh-final' in sys.argv or '--resume-final' in sys.argv else 'live-generation')
OUT.mkdir(parents=True, exist_ok=True)
PACKAGE = ROOT / 'dist-build-cache' / 'builder-output' / 'win-unpacked' / 'resources'
env = os.environ.copy()
# Only credentials/session configuration is borrowed from development. Shipped
# tuning defaults are loaded by the packaged backend itself.
for line in (ROOT / '.env').read_text(encoding='utf-8-sig').splitlines():
    if '=' not in line or line.lstrip().startswith('#'):
        continue
    key, value = line.split('=', 1)
    key = key.strip()
    if ('API_KEY' in key or 'COOKIE' in key) and value.strip():
        env[key] = value.strip().strip('"').strip("'")
with socket.socket() as s:
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
env.update(VIDZEO_ROOT=str(OUT / 'runtime'), VIDZEO_RESOURCE_ROOT=str(PACKAGE),
           VIDZEO_PORT=str(port), VIDZEO_BIND_HOST='127.0.0.1', VIDZEO_NO_BROWSER='1',
           VIDZEO_AUTH_MODE='local', VIDZEO_PACKAGED='0',
           VIDZEO_QUEUE_FILE=str(OUT / 'queue.json'), VIDZEO_TEMP_DIR=str(OUT / 'temp'),
           VIDZEO_OUTPUT_DIR=str(OUT / 'outputs'), PYTHONDONTWRITEBYTECODE='1',
           VIDZEO_OPENROUTER_USAGE_LOG=str(OUT / 'openrouter-usage.jsonl'))
(OUT / 'runtime').mkdir(exist_ok=True)
config = json.loads((ROOT / 'artifacts/news_validation_20261005/request.json').read_text(encoding='utf-8'))
config.update(title='NFL Field — Packaged Pipeline Verification', content_title='NFL football field passing play',
              output_name='NFL Field Verification', category='sports',
              script='An NFL field stretches one hundred yards between the goal lines. But a passing play is about more than distance. The quarterback searches for a receiver. Defenders close the space. A route can pull coverage one way and open a window somewhere else. The camera sees the throw. The real story is the movement before it.',
              render_resolution=[640, 360], fps=24, delivery_budget_seconds=900 if '--fresh-final' in sys.argv else 600,
              media_deadline_seconds=120, visual_deadline_seconds=90,
              render_reserve_seconds=150, creative_review_budget_seconds=45,
              post_media_direction_seconds=20, intro_media_min_shots=3, intro_media_max_shots=5,
              duration='30 seconds')
suffix = '-resume' if '--resume-final' in sys.argv else '-retry-cache' if '--retry-cache' in sys.argv else '-retry' if '--retry' in sys.argv else ''
if suffix:
    previous = json.loads((OUT / 'result.json').read_text(encoding='utf-8'))
    config['output_package_dir'] = previous.get('response', {}).get('output_package_dir') or str(next((OUT / 'outputs').glob('*/checkpoints/pre_render.json')).parent.parent)
(OUT / f'request{suffix}.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
record = {'test_mode': 'isolated local authorization; production entitlement is checked separately',
          'runtime': str(PACKAGE), 'port': port}
started = time.monotonic()
process = None
try:
    with (OUT / f'backend{suffix}.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen([str(PACKAGE / 'tools/python/python.exe'), '-B', str(PACKAGE / 'main.py')],
                                   cwd=str(PACKAGE), env=env, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        base = f'http://127.0.0.1:{port}'
        record['pid'] = process.pid
        (OUT / 'process.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('Packaged backend exited before readiness')
            try:
                health = requests.get(base + '/api/status', timeout=3).json()
                if health.get('ready') and health.get('ffmpeg'):
                    record['health'] = {k: health.get(k) for k in ('ready', 'ffmpeg', 'pipeline_build_id', 'runtime_build')}
                    break
            except requests.RequestException:
                pass
            time.sleep(.5)
        else:
            raise RuntimeError('Packaged backend startup deadline exceeded')
        headers = {'Authorization': 'Bearer local-dev-token'}
        invalid = requests.post(base + '/api/generate', json={'script': 17}, headers=headers, timeout=10)
        record['invalid_request_status'] = invalid.status_code
        blocked = requests.post(base + '/api/generate', json=config, headers={**headers, 'Origin': 'https://untrusted.example'}, timeout=10)
        record['untrusted_origin_status'] = blocked.status_code
        if invalid.status_code != 400 or blocked.status_code not in (401, 403):
            raise RuntimeError('Request/auth boundary checks failed')
        (OUT / f'progress{suffix}.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
        response = requests.post(base + '/api/generate', json=config, headers=headers, timeout=config['delivery_budget_seconds'] + 120)
        record['http_status'] = response.status_code
        record['response'] = response.json()
        record['queue_status'] = requests.get(base + '/api/queue/status', headers=headers, timeout=10).json()
except Exception as exc:
    record['error'] = f'{type(exc).__name__}: {exc}'
finally:
    record['elapsed_seconds'] = round(time.monotonic() - started, 2)
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    (OUT / f'result{suffix}.json').write_text(json.dumps(record, indent=2, default=str), encoding='utf-8')
    print(json.dumps({key: record[key] for key in ('http_status', 'elapsed_seconds', 'error') if key in record}), flush=True)
if record.get('http_status') != 200 or not record.get('response', {}).get('success'):
    raise SystemExit(1)
