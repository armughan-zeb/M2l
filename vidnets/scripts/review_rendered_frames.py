"""Run the production pixel critic against preserved real frame evidence."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'server'))
import config  # load local provider credentials without printing them
from services.editorial_director.pixel_revision import review_and_revise
from services.visual_intelligence.vision_client import QwenVisionClient
from services.editorial_director.visual_critic import audit_frames, intentional_transition_positions

manifest_path=Path(sys.argv[1]).resolve()
preview_path=Path(sys.argv[2]).resolve()
manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
preview=json.loads(preview_path.read_text(encoding='utf-8'))
key=os.environ.get('OPENROUTER_API_KEY') or os.environ.get('VIDZEO_OPENROUTER_API_KEY')
if not key:
    raise SystemExit('No configured visual review provider')
report=review_and_revise(manifest,preview['frames'],
    lambda remaining:QwenVisionClient(api_key=key,max_attempts=1,timeout_seconds=min(25,remaining),fast_mode=True),
    budget_seconds=55,pixel_audit=preview.get('pixel_audit') or audit_frames(
        [row['path'] for row in preview['frames']],
        intentional_transition_positions=intentional_transition_positions(
            preview['frames'],manifest.get('transitions') or [],manifest.get('fps') or 30
        ),
    ))
destination=manifest_path.parent/'pixel_creative_review'
destination.mkdir(exist_ok=True)
(destination/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
(destination/'proposed_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print(json.dumps({'path':str(destination),'status':report['status'],
                  'changed':report['changed_scene_indices'],'unresolved':report['unresolved_scene_indices'],
                  'unavailable':report['unavailable_scene_indices']}),flush=True)
