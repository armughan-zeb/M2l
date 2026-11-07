"""Record primary-source editorial corrections to the preserved news test job."""
import copy
import json
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/news_validation_20261005'
response = json.loads((OUT / 'retry-response.json').read_text(encoding='utf-8'))['response']
plan = Path(response['manifest_path'])
manifest = json.loads(plan.read_text(encoding='utf-8'))
(OUT / 'before-primary-source-correction-plan.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
sources = json.loads((OUT / 'source-media/sources.json').read_text(encoding='utf-8'))
audit = []
for scene_index, source in zip((2, 3), sources):
    scene = manifest['scenes'][scene_index]
    path = OUT / 'source-media' / source['name']
    with Image.open(path) as image:
        width, height = image.size
    old = scene.get('media') or {}
    media = {'type': 'image', 'media_type': 'image', 'path': str(path), 'src': str(path),
             'source': 'web', 'source_url': source['page'], 'title': source['title'],
             'width': width, 'height': height, 'aspect_ratio': width / height,
             'resolution_kind': 'primary_source_editorial_correction',
             'visual_decision': {'accepted': True, 'status': 'primary_source_review',
                                 'reason': 'NASA caption and downloaded image reviewed together'},
             'candidate_evidence': {'primary_source_url': source['page'],
                                    'source_caption': source['title'],
                                    'current_event_image': scene_index == 2}}
    if scene_index == 3:
        media['source_credit'] = {'approved': True, 'source': 'primary_source_review',
                                 'label': 'NASA archive · 2020', 'source_url': source['page']}
    scene['media'] = media
    scene['primary_visual']['asset'] = copy.deepcopy(media)
    scene['primary_visual'].setdefault('props', {}).update(width=width, height=height, aspect_ratio=width / height)
    audit.append({'scene_index': scene_index, 'previous_source_url': old.get('source_url'),
                  'new_source_url': source['page'], 'asset': str(path),
                  'reason': 'replace old launch image' if scene_index == 2 else 'replace station image with actual Crew Dragon',
                  'archive_labeled': scene_index == 3})
manifest['primary_source_corrections'] = audit
plan.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
(OUT / 'source-media-correction-audit.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
print('Corrected two scenes using reviewed NASA imagery; archive provenance retained.')
