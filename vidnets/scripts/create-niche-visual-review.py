"""Build reproducible rendering fixtures; these are layout tests, not research footage."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from services.editing_profile import deterministic_profile
from services.editorial_director.visual_dna import generate_visual_dna

out = ROOT / 'artifacts' / 'niche_editing_revision'
out.mkdir(parents=True, exist_ok=True)
scenes = []

def add(niche, narration, kind, variant, props, display='', role='primary_explanation'):
    profile = deterministic_profile(narration, category=niche, script=narration, variation_seed=8)
    dna = generate_visual_dna(narration, niche, narration).to_dict()
    dna['palette'].update(profile['palette'])
    dna['typography'] = {'display': 'Archivo Black', 'body': 'DM Sans', 'numbers': 'JetBrains Mono'}
    scene = {'id': f'review-{len(scenes)}', 'scene_index': len(scenes), 'start': len(scenes) * 4,
             'from': len(scenes) * 120, 'duration': 4, 'duration_in_frames': 120, 'narration': narration,
             'primary_visual': {'type': kind, 'variant': variant, 'props': {'variant': variant, **props}},
             'director_treatment_enabled': True, 'visual_role_source': 'llm_director',
             'visual_role_contract_version': 2, 'visual_role': role, 'visual_dna': dna,
             'text': {'overlay_policy': 'none', 'frame_mode': 'media_only', 'panel_style': 'none'},
             'transition': {'template_id': 'cut'}, 'camera': {'motion': 'static'}, 'sound_cues': []}
    if display:
        # Fixture contract deliberately exercises approved-copy rendering.
        scene['editorial_contract'] = {'version': 4, 'approved': True, 'source': 'llm',
             'display_text': display, 'source_span': narration, 'emphasis_spans': [{'start': 0, 'end': len(display)}]}
    if kind == 'Diagram':
        scene['primary_visual']['props']['node_copy_contract'] = {'source': 'system_clause_extractor', 'approved': True,
            'nodes': [{'label': step['label'], 'source_span': narration} for step in props['steps']]}
    scenes.append(scene)

add('crime', 'The ticket carried the name Dan Cooper.', 'KineticText', 'word_by_word_punch', {}, 'Dan Cooper')
add('crime', 'On November 24, 1971, the aircraft left Portland.', 'Timeline', 'date_flip_calendar', {'date': 'November 24, 1971'})
add('crime', 'In 1980, a child found $5,800 in cash.', 'StatCard', 'impact_slab', {'value': 5800, 'prefix': '$', 'label': 'RECOVERED CASH'}, '$5,800')
add('aviation', 'Passengers entered the aircraft before the flight.', 'Diagram', 'cause_effect_chain', {'steps': [{'label': 'Passengers'}, {'label': 'aircraft'}, {'label': 'flight'}]})
add('sports', 'Seattle scored 24. New England scored 28.', 'Comparison', 'side_by_side_cards', {'leftValue': '24', 'rightValue': '28', 'leftLabel': 'Seattle', 'rightLabel': 'New England'})
add('business', 'Illustrative fixture values: Company A 12, Company B 30, Company C 18.', 'Chart', 'ranked_horizontal_bars', {'data': [{'label': 'Company A', 'value': 12}, {'label': 'Company B', 'value': 30}, {'label': 'Company C', 'value': 18}]})
add('science', 'Illustrative frequency table: 0–10 years: 12; 10–20 years: 8; 20–30 years: 5.', 'Chart', 'distribution_histogram', {'data': [{'label': '0–10 years', 'value': 12}, {'label': '10–20 years', 'value': 8}, {'label': '20–30 years', 'value': 5}]})
add('history', 'In 1912 the voyage began. In 1985 the wreck was found.', 'Timeline', 'vertical_chronology', {'events': [{'date': '1912', 'label': 'The voyage began'}, {'date': '1985', 'label': 'The wreck was found'}]})
add('maritime', 'Cargo travels by ship to the port.', 'Diagram', 'cause_effect_chain', {'steps': [{'label': 'Cargo'}, {'label': 'ship'}, {'label': 'port'}]})
add('science', 'A solar eclipse occurs when the Moon passes between the Sun and Earth.', 'Diagram', 'eclipse_alignment', {'steps': [{'label': 'Sun'}, {'label': 'Moon'}, {'label': 'Earth'}], 'links': [], 'illustrative_not_to_scale': True})
manifest = {'version': 4, 'kind': 'semantic_edit_manifest', 'fps': 30, 'width': 1280, 'height': 720,
            'duration': len(scenes)*4, 'duration_in_frames': len(scenes)*120,
            'topic': 'Rendering regression fixtures — illustrative chart data',
            'visual_dna': scenes[0]['visual_dna'], 'scenes': scenes, 'captions': {'enabled': False},
            'review_layout': True, 'review_frames': [102] + [i*120+42 for i in range(1, len(scenes))]}
(out / 'visual-fixtures.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print(f'{len(scenes)} fixtures written to {out}')
