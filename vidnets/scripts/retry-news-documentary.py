"""Exercise HTTP checkpoint retry, preserving the original attempt evidence."""
import importlib.util
import json
from pathlib import Path
import shutil
import time
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/news_validation_20261005'
spec = importlib.util.spec_from_file_location('news_validation', ROOT / 'scripts/validate-news-documentary.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original = json.loads((OUT / 'response.json').read_text(encoding='utf-8'))
payload = json.loads((OUT / 'request.json').read_text(encoding='utf-8'))
payload.update(output_package_dir=original['response']['output_package_dir'],
               rerender_from_checkpoint=True,
               delivery_budget_seconds=1000, semantic_render_budget_seconds=750,
               creative_review_budget_seconds=90, creative_revision_rounds=0,
               semantic_preview_gate=True)
module.write('retry-request.json', payload)
start = time.monotonic()
response = requests.post('http://127.0.0.1:5000/api/generate', json=payload,
                         headers={'Authorization': 'Bearer local-dev-token'}, timeout=1120)
result = {'http_status': response.status_code, 'response': response.json(),
          'elapsed_seconds': round(time.monotonic() - start, 2), 'through_http': True}
module.write('retry-response.json', result)
source = Path(payload['output_package_dir']).resolve()
source.relative_to((ROOT / 'output').resolve())
shutil.copytree(source, OUT / 'generation-package', dirs_exist_ok=True)
if result['response'].get('video_path'):
    shutil.copy2(result['response']['video_path'], OUT / 'crew13-news-preview.mp4')
print(json.dumps({key: value for key, value in result.items() if key != 'response'}), flush=True)
module.cost_report()
