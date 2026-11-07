"""Verify normal authorization and byte identity from the delivered app folder."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import requests

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / 'dist/Vidnets'
PACKAGE = RELEASE / 'App/resources'
OUT = ROOT / 'artifacts/single_api_release_20261006'


def digest(path):
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


record = {'critical_files_match': False, 'production_auth': False}
process = None
try:
    files = ['server/app.py', 'server/config.py', 'server/services/generation_request.py',
        'server/services/generation_cost_policy.py', 'server/services/generation_registry.py',
        'server/services/structured_response_cache.py', 'server/services/openrouter_llm.py',
        'server/services/openrouter_usage.py', 'server/services/queue_manager.py',
        'server/services/bounded_http.py', 'server/services/jev_decision_service.py',
        'server/services/research_ledger.py', 'server/services/research_search_worker.py',
        'server/services/web_image/universal_scraper.py',
        'server/services/visual_intelligence/vision_client.py',
        'server/services/editorial_director/director.py', '.env.defaults', 'public/update-build.json']
    record['files'] = [{'path': name, 'sha256': digest(PACKAGE / name),
        'matches_source': digest(PACKAGE / name) == digest(ROOT / name)} for name in files]
    assert all(row['matches_source'] for row in record['files'])
    record['critical_files_match'] = True
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env.update(VIDZEO_ROOT=str(OUT / 'final-app-smoke/runtime'), VIDZEO_RESOURCE_ROOT=str(PACKAGE),
        VIDZEO_PORT=str(port), VIDZEO_BIND_HOST='127.0.0.1', VIDZEO_NO_BROWSER='1',
        VIDZEO_QUEUE_FILE=str(OUT / 'final-app-smoke/queue.json'),
        VIDZEO_OUTPUT_DIR=str(OUT / 'final-app-smoke/outputs'),
        VIDZEO_TEMP_DIR=str(OUT / 'final-app-smoke/temp'),
        VIDZEO_PACKAGED='1', VIDZEO_AUTH_MODE='supabase', PYTHONDONTWRITEBYTECODE='1')
    log = (OUT / 'final-app-smoke.log').open('w', encoding='utf-8')
    process = subprocess.Popen([str(PACKAGE / 'tools/python/python.exe'), '-B', str(PACKAGE / 'main.py')],
        cwd=str(PACKAGE), env=env, stdout=log, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    base = f'http://127.0.0.1:{port}'
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Final app backend exited before readiness')
        try:
            if requests.get(base + '/api/status', timeout=3).json().get('ready'):
                break
        except requests.RequestException:
            pass
        time.sleep(.5)
    else:
        raise RuntimeError('Final app backend readiness timed out')
    record['unauthenticated_status'] = requests.post(base + '/api/generate', json={'script': 'Test.'}, timeout=10).status_code
    record['development_token_status'] = requests.post(base + '/api/generate',
        headers={'Authorization': 'Bearer local-dev-token'}, json={'script': 'Test.'}, timeout=10).status_code
    assert record['unauthenticated_status'] == record['development_token_status'] == 401
    record['production_auth'] = True
    record['build_id'] = json.loads((PACKAGE / 'public/update-build.json').read_text())['buildId']
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
    (OUT / 'clean-app-verification.json').write_text(json.dumps(record, indent=2))
    print(json.dumps({key: record.get(key) for key in ('success', 'critical_files_match', 'production_auth', 'error')}))
if not record.get('success'):
    raise SystemExit(1)
