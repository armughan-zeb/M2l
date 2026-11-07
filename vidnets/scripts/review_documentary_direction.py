"""Replay the production director against a saved film's existing media/audio."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
import app
from services.editorial_director import EditingDirector
from services.semantic_edit_engine import align_directed_motion_to_speech, plan_directed_sound_cues
from services.native_render_policy import procedural_sound_library

source = Path(sys.argv[1]).resolve()
manifest = json.loads(source.read_text(encoding='utf-8'))
scenes = manifest['scenes']
timed_words = {}
for scene in scenes:
    for cue in scene.get('word_cues', []):
        for word in cue.get('timed_words', []):
            timed_words[word['index']] = {'word': word['text'], 'start': word['absolute_start'], 'end': word['absolute_end']}
plan = EditingDirector().plan_documentary(
    script_text=' '.join(s['narration'] for s in scenes),
    scene_texts=[s['narration'] for s in scenes], scene_durations=[s['duration'] for s in scenes],
    media_items=[{**s.get('media', {}), **s.get('primary_visual', {}).get('asset', {})} for s in scenes],
    word_timings=[timed_words[key] for key in sorted(timed_words)],
    topic=manifest.get('topic', ''), category=manifest.get('category', ''),
    fps=manifest['fps'], width=manifest['width'], height=manifest['height'],
    editing_profile=manifest.get('editing_profile'),
)
directed = plan.to_remotion_manifest()
library = procedural_sound_library()
for i, (original, revised) in enumerate(zip(scenes, directed['scenes'])):
    for key in ['primary_visual', 'text', 'editorial_contract', 'display_text', 'source_span', 'emphasis_spans',
                'template_id', 'template_recipe', 'graphic', 'motion_cues', 'map_shot', 'camera', 'transition']:
        original[key] = revised.get(key, {} if key not in ['display_text', 'source_span', 'template_id'] else '')
    original['director_treatment_enabled'] = True
    align_directed_motion_to_speech(original)
    original['sound_cues'] = plan_directed_sound_cues(original, i, library)
manifest['director'] = directed['director']
manifest['visual_dna'] = directed['visual_dna']
app._repair_incomplete_diagram_scenes(manifest)
app._repair_unsupported_chart_scenes(manifest)
app._repair_unsupported_route_map_scenes(manifest)
app._repair_repeated_adjacent_treatments(manifest)
app._repair_invalid_director_templates_and_motion_cues(manifest)
validation = app._validate_remotion_edit_plan(manifest, len(scenes))
manifest['edit_plan_validation'] = validation
output = source.parent / 'visual_review' / 'creative_replay.json'
output.parent.mkdir(exist_ok=True)
output.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print(json.dumps({'path': str(output), 'coverage': manifest['director'].get('creative_coverage'),
                  'errors': validation.get('errors'), 'scenes': [
    {'type': s['primary_visual']['type'], 'recipe_layers': len(s.get('template_recipe', {}).get('layers', []))} for s in scenes
]}, indent=2), flush=True)
if validation.get('errors'):
    raise SystemExit(1)
