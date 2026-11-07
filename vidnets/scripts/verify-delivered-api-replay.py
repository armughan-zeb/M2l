"""Restart delivered backend in isolated test mode; prove durable free replay."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import requests

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'dist/Vidnets/App/resources'
OUT = ROOT / 'artifacts/single_api_release_20261006'
LIVE = OUT / 'live'
config = json.loads((LIVE / 'request.json').read_text())
registry_file = next((LIVE / 'temp/generation-requests').glob('*.json'))
if json.loads(registry_file.read_text()).get('state') != 'completed':
    raise SystemExit('No completed integration request exists; refusing to regenerate.')
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
env = os.environ.copy()
env.update(VIDZEO_ROOT=str(LIVE / 'runtime'), VIDZEO_RESOURCE_ROOT=str(PACKAGE),
    VIDZEO_PORT=str(port), VIDZEO_BIND_HOST='127.0.0.1', VIDZEO_NO_BROWSER='1',
    VIDZEO_AUTH_MODE='local', VIDZEO_PACKAGED='0', VIDZEO_OUTPUT_DIR=str(LIVE / 'outputs'),
    VIDZEO_TEMP_DIR=str(LIVE / 'temp'), VIDZEO_QUEUE_FILE=str(LIVE / 'queue.json'),
    PYTHONDONTWRITEBYTECODE='1', VIDZEO_OPENROUTER_USAGE_LOG=str(LIVE / 'provider-usage.jsonl'))
record = {'scope': 'Final delivered backend; isolated test authorization for replay only.'}
process = None
try:
    ledger = LIVE / 'provider-usage.jsonl'
    before = ledger.read_bytes()
    with (OUT / 'delivered-replay.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen([str(PACKAGE / 'tools/python/python.exe'), '-B', str(PACKAGE / 'main.py')],
            cwd=str(PACKAGE), env=env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        base = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('Delivered backend exited before readiness')
            try:
                if requests.get(base + '/api/status', timeout=3).json().get('ready'):
                    break
            except requests.RequestException:
                pass
            time.sleep(.5)
        else:
            raise RuntimeError('Delivered backend readiness timed out')
        began = time.monotonic()
        response = requests.post(base + '/api/generate', json={**config, 'response_mode': 'json'},
            headers={'Authorization': 'Bearer local-dev-token'}, timeout=15)
        result = response.json()
        assert response.status_code == 200 and result.get('idempotency_replayed'), 'Expected replay, never new work'
        response = requests.post(base + '/api/generate', json=config,
            headers={'Authorization': 'Bearer local-dev-token'}, timeout=15)
        assert response.status_code == 200 and response.headers.get('Content-Type', '').startswith('video/mp4')
        record.update(http_status=response.status_code,
            elapsed_seconds=round(time.monotonic()-began, 2),
            video_sha256=hashlib.sha256(response.content).hexdigest(),
            provider_calls_added=ledger.read_bytes()!=before)
        assert response.content == (LIVE / 'single-call-preview.mp4').read_bytes()
        assert not record['provider_calls_added']
        record['success'] = True
except Exception as error:
    record['error'] = f'{type(error).__name__}: {error}'
finally:
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    (OUT / 'delivered-replay.json').write_text(json.dumps(record, indent=2))
    print(json.dumps(record))
if not record.get('success'):
    raise SystemExit(1)
