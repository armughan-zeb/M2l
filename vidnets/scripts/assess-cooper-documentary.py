"""Assess the repaired test's rendered pixels without silently applying edits."""
import json
import os
from pathlib import Path
import sys
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/cooper_one_minute_20261005'
sys.path.insert(0, str(ROOT / 'server'))
for filename in ('.env.public', '.env.defaults', '.env'):
    load_dotenv(ROOT / filename, override=True)
os.environ['VIDZEO_OPENROUTER_USAGE_LOG'] = str(OUT / 'openrouter-usage.jsonl')
from services.editorial_director.pixel_revision import review_and_revise
from services.visual_intelligence.vision_client import QwenVisionClient

film = json.loads((OUT / 'repaired-manifest.json').read_text(encoding='utf-8'))
preview = json.loads((OUT / 'repaired-preview.json').read_text(encoding='utf-8'))
key = os.environ.get('OPENROUTER_API_KEY') or os.environ.get('VIDZEO_OPENROUTER_API_KEY')
report = review_and_revise(film, preview['frames'],
    lambda remaining: QwenVisionClient(api_key=key, max_attempts=1,
        timeout_seconds=max(1,min(18,remaining)), fast_mode=True),
    budget_seconds=65, workers=4, assessment_only=True,
    layout_audit=preview.get('layout_audit'))
(OUT / 'repaired-vision-review.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({k:report[k] for k in ('status','coverage','llm_review_certified',
    'unresolved_scene_indices','unavailable_scene_indices','elapsed_seconds')}))
