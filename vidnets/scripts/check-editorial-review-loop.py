"""Assess actual saved crime preview frames through the production reviewer.

Use --live to call the configured vision provider. Never logs credentials.
"""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
OUT = ROOT / 'artifacts' / 'editorial_review_loop'


def main():
    from services.editorial_director.pixel_revision import review_and_revise
    manifest = json.loads((OUT / 'preview-manifest.json').read_text(encoding='utf-8'))
    rendered = json.loads((OUT / 'preview-report.json').read_text(encoding='utf-8'))
    manifest['scenes'] = manifest['scenes'][:3]
    manifest.setdefault('editing_profile', {})['niche'] = 'crime'
    frames = [row for row in rendered['frames'] if row['frame'] < 360]
    if '--live' not in sys.argv:
        print('Preview frames are ready; use --live for provider review.')
        return
    from dotenv import load_dotenv
    for filename in ('.env.public', '.env.defaults', '.env'):
        load_dotenv(ROOT / filename, override=True)
    key = os.environ.get('OPENROUTER_API_KEY') or os.environ.get('VIDZEO_OPENROUTER_API_KEY')
    if not key:
        print('No vision provider configured; live review skipped.')
        return
    from services.visual_intelligence.vision_client import QwenVisionClient
    report = review_and_revise(manifest, frames,
        lambda remaining: QwenVisionClient(api_key=key, max_attempts=1,
            timeout_seconds=max(1, min(18, remaining)), fast_mode=True),
        budget_seconds=35, workers=3, assessment_only=True,
        layout_audit=rendered.get('layout_audit'))
    (OUT / 'live-review.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({name: report[name] for name in
        ('status', 'coverage', 'llm_review_certified', 'unresolved_scene_indices',
         'unavailable_scene_indices', 'elapsed_seconds')}))


if __name__ == '__main__':
    main()
