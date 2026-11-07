"""Export the reviewed repair through the production Remotion composition."""
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/cooper_one_minute_20261005'
preview = json.loads((OUT / 'repaired-preview.json').read_text(encoding='utf-8'))
if preview.get('coverage') != 'complete' or preview.get('layout_failures'):
    raise RuntimeError('The repaired preview has missing frames or text overflow')
target = OUT / 'D.B. Cooper - repaired Remotion master.mp4'
started = time.monotonic()
with (OUT / 'repaired-render.log').open('w', encoding='utf-8') as log:
    result = subprocess.run([str(ROOT / 'tools/node/node.exe'),
        str(ROOT / 'scripts/render-remotion-manifest.js'),
        str(OUT / 'repaired-manifest.json'), str(target)], cwd=ROOT,
        stdout=log, stderr=log, timeout=720)
record = {'method':'production_remotion_composition_saved_manifest',
    'exit_code':result.returncode, 'elapsed_seconds':round(time.monotonic()-started,2),
    'response':{'success':result.returncode==0 and target.exists(), 'video_path':str(target)},
    'note':'Curated repair of the failed API test. This is not an API success response.'}
(OUT / 'render-result.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
print(json.dumps(record))
if not record['response']['success']: raise SystemExit(1)
