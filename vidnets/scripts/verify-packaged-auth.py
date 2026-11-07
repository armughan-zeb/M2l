"""Check the normal packaged auth boundary, with no login or paid provider calls."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import requests

root = Path(__file__).resolve().parents[1]
package = root / 'dist-build-cache/builder-output/win-unpacked/resources'
out = root / 'artifacts/release_validation_20261006/production-auth'
out.mkdir(parents=True, exist_ok=True)
with socket.socket() as s:
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
env = os.environ.copy()
env.update(VIDZEO_ROOT=str(out), VIDZEO_RESOURCE_ROOT=str(package), VIDZEO_PORT=str(port),
           VIDZEO_PACKAGED='1', VIDZEO_AUTH_MODE='remote', VIDZEO_QUEUE_FILE=str(out / 'queue.json'),
           VIDZEO_OUTPUT_DIR=str(out / 'outputs'), VIDZEO_TEMP_DIR=str(out / 'temp'))
env.pop('VIDZEO_ALLOW_PACKAGED_LOCAL_AUTH', None)
results = []
with (out / 'backend.log').open('w', encoding='utf-8') as log:
    process = subprocess.Popen([str(package / 'tools/python/python.exe'), '-B', str(package / 'main.py')],
                               cwd=str(package), env=env, stdout=log, stderr=subprocess.STDOUT,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        base = f'http://127.0.0.1:{port}'
        for attempt in range(90):
            try:
                if requests.get(base + '/api/status', timeout=2).json().get('ready'):
                    break
            except requests.RequestException:
                pass
            time.sleep(.5)
        else:
            raise RuntimeError('Packaged auth test startup timed out')
        for token in ('', 'local-dev-token', 'local-bypass'):
            headers = {'Authorization': f'Bearer {token}'} if token else {}
            response = requests.post(base + '/api/generate', json={'script': 'No generation is authorized.'}, headers=headers, timeout=5)
            results.append({'case': 'no token' if not token else 'development token', 'status': response.status_code})
            assert response.status_code == 401
        print('Packaged production authentication rejected all unauthenticated/development generation requests')
    finally:
        process.terminate()
        process.wait(timeout=10)
        (out / 'checks.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
