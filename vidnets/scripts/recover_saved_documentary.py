"""Repair a saved real generation without repeating audio and retrieval stages."""
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'server'))
import app
from services.media_shot_plan import build_media_shot_plan
from services.editorial_director.production_contract import compile_production_timeline
from services.native_render_policy import render_semantic_documentary

source=Path(sys.argv[1]).resolve()
destination=Path(sys.argv[2]).resolve()
destination.mkdir(parents=True,exist_ok=True)
manifest=json.loads(source.read_text(encoding='utf-8'))
scenes=manifest['scenes']
items=[dict(s.get('media') or (s.get('primary_visual') or {}).get('asset') or {}) for s in scenes]
for index,item in enumerate(items):
    item['scene_index']=index
    item.setdefault('candidate_id',f'scene:{index}')
texts=[s['narration'] for s in scenes]
durations=[s['duration'] for s in scenes]
cfg={'visual_understanding':True,'render_backend':'remotion','semantic_render_required':True,'fast_mode':True,
     'semantic_fast_render':True,'strict_retrieval_accuracy':True,'semantic_render_timeout_seconds':1200}
cfg['_creative_media_repair']=lambda film,findings,budget:app._repair_creative_media(film,findings,budget,cfg)
summary=manifest.get('media_shot_plan') or {}
plans,summary=build_media_shot_plan(items,durations,config=cfg)
if (summary.get('perceptual_repetition_audit') or {}).get('unresolved_repeats'):
    repair=app._recover_perceptual_media_candidates(items,texts,durations,summary,cfg,manifest.get('topic',''),' '.join(texts))
    plans,summary=build_media_shot_plan(items,durations,config=cfg)
    summary['fresh_visual_recovery']=repair
manifest['media_shot_plan']=summary
for scene,item,shots in zip(scenes,items,plans):
    item['shots']=shots
    scene['media']=item
    scene['primary_visual']['asset']=item
compile_production_timeline(manifest)
plan_path=destination/'repaired_plan.json'
plan_path.write_text(json.dumps(manifest,indent=2,default=str),encoding='utf-8')
if manifest['production_audit']['status']=='fail':
    raise SystemExit('Source variety repair still requires work: '+str(manifest['production_audit']['errors']))
result=render_semantic_documentary(manifest,destination/'review_master.mp4',config=cfg,
    progress_callback=lambda value,message:print(message,flush=True))
(destination/'render_report.json').write_text(json.dumps(result,indent=2,default=str),encoding='utf-8')
print(json.dumps(result,indent=2,default=str),flush=True)
