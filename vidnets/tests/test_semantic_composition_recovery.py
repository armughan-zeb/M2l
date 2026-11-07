"""Local layout and failed-provider recovery preserve the narrated explanation."""
import json
from copy import deepcopy

import pytest

from server.services.editorial_director.director import (
    EditingDirector, _compile_designed_diagram_recipe, _grounded_diagram_recovery,
    _recover_explanatory_decision,
    _build_comparison_props, _build_timeline_props, validated_diagram_links,
)

PALETTE = {'accent': '#D64040', 'primary': '#F7F4ED', 'secondary': '#BAC1C8', 'background': '#101216'}
NARRATION = 'Batteries supply electricity to vehicles.'
NODES = [{'label': label, 'source_span': NARRATION} for label in ['Batteries', 'vehicles']]
LINKS = [{'from': 'Batteries', 'to': 'vehicles', 'evidence': NARRATION}]


def authored_paths(**geometry):
    return {'id': 'energy-transfer', 'name': 'Energy transfer', 'layout': 'flow', 'objects': [
        {'kind': 'path', 'actor_label': label, 'points': [{'x': 0, 'y': 0}, {'x': 100, 'y': 0},
            {'x': 100, 'y': 100}, {'x': 0, 'y': 100}, {'x': 0, 'y': 0}], 'closed': True,
         'fill_role': 'primary', 'color_role': 'accent', 'motion': 'wipe', 'start_seconds': index*.5,
         **geometry} for index, label in enumerate(['Batteries', 'vehicles'])]}


def test_nullable_geometry_is_fitted_without_changing_authored_contours():
    draft = authored_paths(x=None, y=None, width=None, height=None)
    before = deepcopy(draft)
    recipe = _compile_designed_diagram_recipe(draft, NODES, LINKS, PALETTE, NARRATION, 5, 30, 'fallback')
    assert draft == before
    assert recipe['layout_source'] == 'local_actor_layout'
    assert recipe['layers'][0]['links'] == LINKS
    actors = recipe['layers'][1:]
    assert all(actor['points'] == draft['objects'][0]['points'] for actor in actors)
    assert actors[0]['x'] + actors[0]['width'] < actors[1]['x']
    assert all(actor['y'] + actor['height'] <= 75 for actor in actors)


def test_tiny_contours_are_repaired_locally_before_model_retry():
    draft = authored_paths(x=48, y=28, width=3, height=3)
    recipe = _compile_designed_diagram_recipe(draft, NODES, LINKS, PALETTE, NARRATION, 5, 30, 'fallback')
    assert recipe['layout_source'] == 'local_actor_layout'
    assert min(actor['width'] for actor in recipe['layers'][1:]) >= 30


def test_geometry_repair_never_substitutes_unknown_or_missing_actor():
    draft = authored_paths(x=None, y=None, width=None, height=None)
    draft['objects'][1]['actor_label'] = 'Invented generator'
    with pytest.raises(ValueError, match='actor_ungrounded'):
        _compile_designed_diagram_recipe(draft, NODES, LINKS, PALETTE, NARRATION, 5, 30, 'fallback')


@pytest.mark.parametrize('niche,text,labels', [
    ('crime', 'The ransom moved from the aircraft to the ground.', ['ransom', 'aircraft']),
    ('travel', 'Passengers move from the station to the train.', ['station', 'train']),
    ('sports', 'The quarterback passes to the receiver.', ['quarterback', 'receiver']),
    ('technology', NARRATION, ['Batteries', 'vehicles']),
])
def test_failed_initial_and_correction_preserve_semantic_graphic(niche, text, labels):
    nodes = [{'label': label, 'source_span': text} for label in labels]
    links = [{'from': labels[0], 'to': labels[1], 'evidence': text}]

    class Client:
        calls = []

        def chat(self, **kwargs):
            stage = kwargs.get('json_schema_name')
            self.calls.append(stage)
            if stage == 'vidnets_art_direction_v1':
                return json.dumps({'genre': niche+'-documentary', 'display_font': 'Archivo Black',
                    'body_font': 'DM Sans', 'accent_color': PALETTE['accent']})
            if stage == 'vidnets_scene_direction_v1':
                return json.dumps({'scenes': [{'scene_index': 0, 'type': 'Diagram',
                    'variant': 'cause_and_effect_flow', 'frame_mode': 'diagram',
                    'visual_role': 'primary_explanation', 'overlay_policy': 'none',
                    'display_text': '', 'template_id': 'generated-recipe', 'template_recipe': None,
                    'diagram_nodes': nodes, 'diagram_links': links}]})
            if stage in {'vidnets_scene_composition_v1', 'vidnets_scene_composition_repair_v1'}:
                raise ValueError('provider unavailable for graphic design')
            return '{}'

    client = Client()
    plan = EditingDirector(llm_client=client).plan_documentary(script_text=text, scene_texts=[text],
        scene_durations=[5], topic=niche, category=niche, fps=30)
    scene = plan.scenes[0]
    assert scene.primary_visual.type == 'Diagram'
    assert scene.primary_visual.props['node_copy_contract']['nodes'] == nodes
    assert scene.primary_visual.props['links'] == links
    assert plan.director_metadata['composition_passes'][0]['status'] == 'grounded_specialist_recovery'
    assert 'vidnets_scene_quality_revision_v1' not in client.calls


def test_timeout_recovery_uses_the_same_grounded_specialist_contract():
    decision = {'diagram_nodes': NODES, 'diagram_links': LINKS, 'variant': 'network_node_connect',
                '_composition_error': 'timeout', 'display_text': ''}
    assert _recover_explanatory_decision(decision, NARRATION)
    assert decision['frame_mode'] == 'diagram'
    assert decision['type'] == 'Diagram'
    assert decision['variant'] == 'network_node_connect'
    assert not decision['_composition_quality_rejected']


def test_ungrounded_relation_cannot_be_recovered_to_fake_diagram():
    decision = {'diagram_nodes': NODES, 'diagram_links': [{'from': 'Batteries', 'to': 'vehicles',
                  'evidence': 'Batteries caused vehicles to explode.'}]}
    assert _grounded_diagram_recovery(decision, NARRATION) == {}


def test_native_glyph_uses_actor_identity_instead_of_shared_sentence():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1]/'src/remotion/components/ExplanatoryDiagram.jsx').read_text(encoding='utf-8')
    assert '<DiagramGlyph label={steps[i].label}' in source
    assert 'steps[i].evidence}`}' not in source
    assert 'glyphSize' in source and 'glyphHalfHeight' in source


def test_charging_and_storage_relations_are_not_lost_to_verb_whitelist():
    text = 'Solar panels charge batteries, and batteries power vehicles.'
    nodes = [{'label': label, 'source_span': text} for label in ['Solar panels', 'batteries', 'vehicles']]
    links = [{'from': a['label'], 'to': b['label'], 'evidence': text} for a, b in zip(nodes, nodes[1:])]
    assert validated_diagram_links(links, nodes, text) == links
    assert _grounded_diagram_recovery({'diagram_nodes': nodes, 'diagram_links': links}, text)


def test_separately_spoken_team_scores_keep_names_and_values():
    props = _build_comparison_props('side_by_side_cards',
        'Seattle scored 24, while New England scored 28.', [], None)
    assert props['leftLabel'] == 'Seattle' and props['leftValue'] == '24'
    assert props['rightLabel'] == 'New England' and props['rightValue'] == '28'
    assert not props.get('no_data')


def test_timeline_preserves_events_instead_of_generic_phase_names():
    props = _build_timeline_props('vertical_chronology',
        'In 1912 the voyage began. In 1985 the wreck was found.', ['1912', '1985'], None)
    assert props['events'][0]['label'] == 'the voyage began'
    assert props['events'][1]['label'] == 'the wreck was found'


@pytest.mark.parametrize('text', [
    'Alice scored 12.5 points. Bob scored 10.5 points.',
    'Tom Brady scored 6 touchdowns. Michael Jordan scored 30 points.',
    'Alice scored 80 percent. Bob scored 90 percent.',
])
def test_score_recovery_does_not_truncate_or_compare_incompatible_units(text):
    from server.services.editorial_director.production_contract import _narrated_score_comparison
    assert _narrated_score_comparison(text) is None


def test_compatible_score_units_are_preserved():
    from server.services.editorial_director.production_contract import _narrated_score_comparison
    result = _narrated_score_comparison('Alice scored 3 goals. Bob scored 2 goals.')
    assert result['leftValue'] == '3 goals' and result['rightValue'] == '2 goals'


@pytest.mark.parametrize('text,labels', [
    ('Batteries do not power vehicles.', ['Batteries', 'vehicles']),
    ('Batteries cannot power vehicles.', ['Batteries', 'vehicles']),
    ('Solar panels charge batteries, while vehicles use fuel.', ['Solar panels', 'vehicles']),
])
def test_denied_and_cross_clause_relationships_are_not_drawn(text, labels):
    nodes = [{'label': label, 'source_span': text} for label in labels]
    assert validated_diagram_links([{'from': labels[0], 'to': labels[1], 'evidence': text}], nodes, text) == []


@pytest.mark.parametrize('text', ['Batteries power vehicles.', 'Vehicles are powered by batteries.'])
def test_arrow_direction_tracks_active_and_passive_narration(text):
    labels = ['batteries', 'vehicles']
    nodes = [{'label': label, 'source_span': text} for label in labels]
    correct = {'from': 'batteries', 'to': 'vehicles', 'evidence': text}
    wrong = {'from': 'vehicles', 'to': 'batteries', 'evidence': text}
    assert validated_diagram_links([correct, wrong], nodes, text) == [correct]


def test_special_physical_grammar_cannot_bypass_negation():
    text = 'The engine does not convert fuel into motion.'
    nodes = [{'label': label, 'source_span': text} for label in ['fuel', 'engine', 'motion']]
    decision = {'diagram_nodes': nodes, 'diagram_links': [], 'variant': 'energy_conversion'}
    assert _grounded_diagram_recovery(decision, text) == {}
