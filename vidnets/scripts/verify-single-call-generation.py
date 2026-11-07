"""Small live API proof: one POST returns MP4; replay does not call providers."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/single_api_release_20261006/live'
PACKAGE = ROOT / 'dist-build-cache/builder-output/win-unpacked/resources'
OUT.mkdir(parents=True, exist_ok=True)
env = os.environ.copy()
for line in (ROOT / '.env').read_text(encoding='utf-8-sig').splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        key, value = line.split('=', 1)
        if ('API_KEY' in key or 'COOKIE' in key) and value.strip():
            env[key.strip()] = value.strip().strip('"').strip("'")
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
env.update(VIDZEO_ROOT=str(OUT / 'runtime'), VIDZEO_RESOURCE_ROOT=str(PACKAGE),
    VIDZEO_PORT=str(port), VIDZEO_BIND_HOST='127.0.0.1', VIDZEO_NO_BROWSER='1',
    VIDZEO_AUTH_MODE='local', VIDZEO_PACKAGED='0', VIDZEO_OUTPUT_DIR=str(OUT / 'outputs'),
    VIDZEO_TEMP_DIR=str(OUT / 'temp'), VIDZEO_QUEUE_FILE=str(OUT / 'queue.json'),
    PYTHONDONTWRITEBYTECODE='1', VIDZEO_OPENROUTER_USAGE_LOG=str(OUT / 'provider-usage.jsonl'))
previous = json.loads((ROOT / 'artifacts/release_validation_20261006/live-generation-final/result-resume.json').read_text())
scenes = previous['response']['component_manifest']['scenes']
images = [scenes[i]['media']['src'] for i in (0, 1, 4)]
config = {'request_id': 'single-call-qwen-proof-20261006', 'response_mode': 'video',
    'cost_profile': 'efficient', 'type': 'long', 'title': 'NFL Space and Timing',
    'script': 'An NFL field is one hundred yards long. A passing play depends on space and timing. Before the throw, routes reshape the defense.',
    'image_source': 'Custom Images', 'images': images, 'category': 'sports',
    'tts_model': 'audiogram', 'voice': 'Brian', 'vo_mode': 'AI Narration',
    'captions_enabled': True, 'include_captions': True, 'sound_effects_enabled': True,
    'graphic_density': .3, 'render_resolution': [640, 360], 'fps': 24,
    'duration': '20 seconds', 'delivery_budget_seconds': 600,
    'creative_review_budget_seconds': 45, 'post_media_direction_seconds': 20}
(OUT / 'request.json').write_text(json.dumps(config, indent=2))
record = {'scope': 'Isolated test authorization; provided NFL stills; real Qwen/TTS/Remotion calls.'}
process = None
for name in ('backend.log', 'result.json'):
    previous_file = OUT / name
    if previous_file.exists():
        import shutil
        shutil.copy2(previous_file, OUT / (name + '.' + str(time.time_ns()) + '.previous'))
bootstrap = OUT / 'diagnostic-backend.py'
bootstrap.write_text("import faulthandler, runpy, sys\nfaulthandler.enable()\nfaulthandler.dump_traceback_later(45, repeat=True)\nsys.path.insert(0, " + repr(str(PACKAGE)) + ")\nrunpy.run_path(" + repr(str(PACKAGE / 'main.py')) + ", run_name='__main__')\n")
try:
    with (OUT / 'backend.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen([str(PACKAGE / 'tools/python/python.exe'), '-B', str(bootstrap)],
            cwd=str(PACKAGE), env=env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        base = f'http://127.0.0.1:{port}'
        headers = {'Authorization': 'Bearer local-dev-token'}
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('Test backend exited before readiness')
            try:
                if requests.get(base + '/api/status', timeout=3).json().get('ready'):
                    break
            except requests.RequestException:
                pass
            time.sleep(.5)
        else:
            raise RuntimeError('Test backend startup timed out')
        started = time.monotonic()
        response = requests.post(base + '/api/generate', headers=headers, json=config, timeout=720)
        record.update(http_status=response.status_code, elapsed_seconds=round(time.monotonic() - started, 2),
                      content_type=response.headers.get('Content-Type'))
        if response.status_code != 200 or not response.headers.get('Content-Type', '').startswith('video/mp4'):
            record['response'] = response.json()
            raise RuntimeError('Single-call video delivery did not succeed')
        (OUT / 'single-call-preview.mp4').write_bytes(response.content)
        ledger = OUT / 'provider-usage.jsonl'
        before = ledger.read_bytes() if ledger.exists() else b''
        replay = requests.post(base + '/api/generate', headers=headers,
            json={**config, 'response_mode': 'json'}, timeout=15)
        record['replay_status'] = replay.status_code
        record['replay'] = replay.json()
        record['replay_added_provider_calls'] = (ledger.read_bytes() if ledger.exists() else b'') != before
        assert replay.status_code == 200 and record['replay'].get('idempotency_replayed')
        assert not record['replay_added_provider_calls']
        rows = [json.loads(line) for line in before.decode().splitlines() if line.strip()]
        record['models'] = sorted({row['model'] for row in rows})
        record['provider_reported_usd'] = sum(row.get('reported_cost_usd') or 0 for row in rows)
        record['unpriced_calls'] = sum(row.get('reported_cost_usd') is None for row in rows)
        assert all(row['model'].startswith('qwen/') for row in rows if row['kind'] != 'tts')
        record['success'] = True
except Exception as exc:
    record['error'] = f'{type(exc).__name__}: {exc}'
finally:
    if process and process.poll() is None:
        import psutil
        try:
            children = psutil.Process(process.pid).children(recursive=True)
        except psutil.Error:
            children = []
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        for child in children:
            try:
                child.terminate()
            except psutil.Error:
                pass
    (OUT / 'result.json').write_text(json.dumps(record, indent=2, default=str))
    print(json.dumps({k: record[k] for k in ('success', 'http_status', 'elapsed_seconds', 'error') if k in record}))
if not record.get('success'):
    raise SystemExit(1)
