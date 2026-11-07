"""Regressions from the live crime render and shared niche/media handoff."""
import pytest
from server.services.editing_profile import classify_niche, deterministic_profile, normalize_profile
from server.services.editorial_director.director import _build_stat_card_props, _safe_text_treatment
from server.services.editorial_director.visual_dna import detect_genre
from server.services.mixed_media_planner import resolve_media_policy, build_adaptive_mixed_media_plan
from server.services.scene_splitter import _split_script_visual_units, _is_abbreviation_period
from server.services.youtube_pipeline_v2 import _score_candidate, _subject_aliases


@pytest.mark.parametrize('niche,script', [
    ('crime', 'A hijacker demanded ransom aboard an airliner. FBI agents investigated.'),
    ('travel', 'A travel itinerary takes us through a national park.'),
    ('sports', 'An NFL quarterback reaches the Super Bowl playoffs.'),
    ('biography', 'The biography follows the career of an artist.'),
    ('aviation', 'The Boeing aircraft approached the runway with cockpit instruments.'),
    ('maritime', 'A container ship crosses the Panama Canal to the port.'),
    ('engineering', 'The hydraulic turbine reveals its mechanical design.'),
    ('architecture', 'The architect explores the skyscraper and urban design.'),
    ('food', 'The chef explains the recipe and ingredients of this cuisine.'),
    ('business', 'An entrepreneur changes the business model and company strategy.'),
    ('education', 'Education and teaching follow a new curriculum.'),
])
def test_shared_niche_contract(niche, script):
    assert classify_niche(script=script) == niche
    profile = deterministic_profile('Documentary', script=script, variation_seed=8)
    policy = resolve_media_policy(title='Documentary', script=script)
    assert profile['niche'] == policy['niche'] == niche
    assert profile['visual_grammar']['use_when_grounded']
    assert profile['palette']['niche'] == niche
    assert detect_genre('Documentary', script=script) != 'general'


def test_crime_palette_cannot_be_replaced_with_blue_by_profile_model():
    base = deterministic_profile('Hijacking and ransom', script='FBI investigation.', variation_seed=8)
    result = normalize_profile({'niche': 'crime', 'palette': {'accent': '#4488EE', 'primary': '#111827'}}, base)
    assert result['palette'] == base['palette']
    treatment = _safe_text_treatment({'text_color': '#111827'}, base['palette'], True)
    assert treatment['text_color'] != '#111827'


def test_stat_excludes_year_and_preserves_currency_and_meaning():
    props = _build_stat_card_props('hero_counter_large', 'In 1980, a child found $5,800 in cash.', [], None)
    assert props['value'] == 5800
    assert props['prefix'] == '$'
    assert props['label'] == 'RECOVERED CASH'
    assert _build_stat_card_props('hero_counter_large', 'In 2016, investigators changed direction.', [], None)['no_data']


def test_llm_controls_supported_source_allocation_only():
    script = 'A hijacker demanded ransom. FBI agents searched for evidence.'
    supported = {'niche': 'crime', 'confidence': .9, 'evidence_terms': ['hijacker', 'ransom'],
                 'media_weights': {'Web Images': .25, 'YouTube Clips': .5, 'Stock Media': .25}}
    policy = resolve_media_policy(script=script, llm_analysis=supported)
    assert policy['llm_allocation_used']
    assert policy['weights']['Stock Media'] == .25
    invalid = {**supported, 'media_weights': {'YouTube Clips': -1, 'Stock Media': 3}}
    assert not resolve_media_policy(script=script, llm_analysis=invalid)['llm_allocation_used']


def test_stock_is_contextual_and_explicit_percentages_remain_authoritative():
    scenes = ['Dan Cooper boarded an aircraft in 1971.', 'Factory workers inspect the machinery.', 'Drone view of an empty airport.']
    automatic = build_adaptive_mixed_media_plan(scenes, title='An FBI hijacking investigation', scene_media_mode='auto')
    assert automatic.decisions[0].assigned_source != 'Stock Media'
    explicit = build_adaptive_mixed_media_plan(scenes, weights={'Web Images': 1}, scene_media_mode='auto', strict_weights=True)
    assert explicit.final_counts['Web Images'] == len(scenes)


def test_identity_introductions_favor_portraits_over_unrelated_documentary_clips():
    from server.services.mixed_media_planner import automatic_source_preferences
    assert automatic_source_preferences('Dan Cooper was the name used by the hijacker later called D.B. Cooper.')[0] == 'Web Images'


def test_quoted_initials_do_not_create_point_two_second_scenes():
    words = [{'word': '\ufffdD.'}, {'word': 'B.\ufffd'}, {'word': 'Cooper'}]
    assert _is_abbreviation_period(words, 0)
    assert _is_abbreviation_period(words, 1)
    units = _split_script_visual_units('The press called him D. B. Cooper. He disappeared.')
    assert len(units) == 2
    assert 'D. B. Cooper' in units[0]
    dated = _split_script_visual_units('On November 24, 1971, he boarded the aircraft. The FBI investigated.')
    assert len(dated) == 2
    assert 'November 24, 1971' in dated[0]
    from server.services.scene_splitter import _is_narrated_date_boundary
    dated_words = [{'word': word} for word in ['On', 'November', '24,', '1971,', 'he', 'boarded.']]
    assert _is_narrated_date_boundary(dated_words, 2)
    assert _is_narrated_date_boundary(dated_words, 3)
    assert not _is_narrated_date_boundary(dated_words, 5)


def test_criminal_aliases_and_actor_namesakes():
    assert 'D.B. Cooper' in _subject_aliases('Dan Cooper')
    wrong = {'title': 'Dan Stevens and Dominic Cooper movie premiere', 'channel': 'Actor interviews'}
    assert _score_candidate(wrong, 'Dan Cooper', 'The hijacker demanded ransom.', 'Dan Cooper archival footage', {'domain_category': 'crime'}) < 0
    assert wrong['v2_reject_reason'] == 'canonical_entity_mismatch'


@pytest.mark.parametrize('name', ['D.B. Cooper', 'D. B. Cooper', 'J. Robert Oppenheimer'])
def test_storyboard_and_web_identity_preserve_initials(name):
    from server.services.storyboard_v2.script_parser import _clean_subject
    from server.services.web_image.query import _extract_main_subject
    assert _clean_subject(name) == name
    assert _extract_main_subject(name) == name


def test_immutable_storyboard_does_not_split_initials_or_decimals():
    from server.services.storyboard_v2.script_parser import build_script_plan
    script = 'The hijacker later called D.B. Cooper boarded the aircraft. On November 24, 1971, the flight departed. The amount rose by 2.3 percent.'
    plan = build_script_plan(script, content_title='D.B. Cooper', scene_mode='Every Beat')
    assert len(plan.scenes) == 3
    assert ''.join(script[scene.char_start:scene.char_end] for scene in plan.scenes) == script


def test_histogram_requires_a_real_distribution():
    from server.services.editorial_director.director import _build_chart_props
    from server.services.editorial_director.contract import SCENE_TYPE_VARIANTS
    assert 'distribution_histogram' in SCENE_TYPE_VARIANTS['Chart']
    props = _build_chart_props('distribution_histogram', '0-10 years: 12; 10-20 years: 8; 20-30 years: 5.', [], '', [])
    assert [row['value'] for row in props['data']] == [12, 8, 5]
    for text in ['Revenue rose from 12 to 30.', '0-10 years: 12; 5-20 years: 8.', '0-10 years: 12; 10-30 years: 8.']:
        assert _build_chart_props('distribution_histogram', text, [], '', [])['no_data']


def test_failed_bespoke_composition_recovers_only_grounded_mechanisms():
    from server.services.editorial_director.director import _grounded_diagram_recovery
    narration = 'A solar eclipse occurs when the Moon passes between the Sun and Earth.'
    props = _grounded_diagram_recovery({}, narration)
    assert props['variant'] == 'eclipse_alignment'
    assert [node['label'] for node in props['node_copy_contract']['nodes']] == ['Sun', 'Moon', 'Earth']
    assert props['illustrative_not_to_scale']
    assert not _grounded_diagram_recovery({}, 'The Moon was photographed from Earth.')
    assert not _grounded_diagram_recovery({'variant': 'eclipse_alignment'}, 'The Moon was photographed from Earth.')
    shadow = _grounded_diagram_recovery({}, 'The Moon blocks the sunlight and its shadow reaches Earth.')
    assert shadow['variant'] == 'shadow_projection'
    assert not _grounded_diagram_recovery({'diagram_nodes': [{'label': 'FBI', 'source_span': 'FBI investigated.'}]}, 'FBI investigated.')


def test_valid_bespoke_diagram_survives_final_frame_contract():
    from server.services.editorial_director.director import _compile_designed_diagram_recipe, _repair_scene_frame_contract
    from server.services.editorial_director.contract import SceneDecision, PrimaryVisual
    narration = 'The Moon blocks the sunlight and its shadow reaches Earth.'
    nodes = [{'label': name, 'source_span': narration} for name in ['Moon', 'Earth']]
    palette = {'accent': '#D32F2F', 'primary': '#FFFFFF', 'background': '#111111'}
    objects = [{'kind': 'sphere', 'actor_label': name, 'x': x, 'y': 20, 'width': 24, 'height': 43,
                'motion': 'fade', 'color_role': 'primary', 'start_seconds': .2} for name, x in [('Moon', 10), ('Earth', 62)]]
    recipe = _compile_designed_diagram_recipe({'id': 'grounded-moon-earth', 'name': 'Grounded mechanism', 'objects': objects},
        nodes, [], palette, narration, 4, 24, 'fallback-id')
    scene = SceneDecision(scene_id='bespoke', scene_index=0, start=0, end=4, duration=4, narration=narration,
        visual_intent='Explain the shadow', primary_visual=PrimaryVisual(type='Diagram', variant='cause_and_effect_flow', props={'no_data': True}),
        text={'frame_mode': 'diagram'}, template_recipe=recipe)
    _repair_scene_frame_contract(scene, fps=24)
    assert scene.primary_visual.type == 'Diagram'
    assert scene.template_recipe == recipe
