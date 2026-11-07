import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from services.editorial_director.production_contract import compile_production_timeline, script_requirements, TEMPLATE_PRIMARY_TYPES
from services.editorial_director.media_director import plan_media_composition, audit_media_compositions, choose_media_safe_region
from services.editorial_director.media_first import apply_media_first_direction
from services.editorial_director.manager import apply_manager_corrections
from services.template_library import validate_template_recipe


@pytest.mark.parametrize('kind', ['Chart', 'Timeline', 'Diagram', 'Comparison', 'DocumentHighlight'])
def test_supported_graphics_overlay_relevant_footage_by_default(kind):
    scene = {'primary_visual': {'type': kind}, 'media': {'src': 'real.mp4', 'type': 'video'}}
    plan = plan_media_composition(scene)
    assert plan['hero_layer'] == 'source_media'
    assert plan['render_mode'] == 'media_first_overlay'
    assert plan['media_visibility'] >= 0.78
    scene['media_composition'] = plan
    assert audit_media_compositions({'scenes': [scene]})['status'] == 'pass'


def test_map_or_explicit_primary_explanation_owns_the_canvas():
    media = {'src': 'real.mp4', 'type': 'video'}
    map_plan = plan_media_composition({'primary_visual': {'type': 'AnimatedMap'}, 'media': media})
    chart_plan = plan_media_composition({
        'primary_visual': {'type': 'Chart'}, 'visual_role': 'primary_explanation', 'media': media,
    })
    assert map_plan['hero_layer'] == chart_plan['hero_layer'] == 'graphic'
    assert map_plan['render_mode'] == chart_plan['render_mode'] == 'explanatory_canvas'
    assert map_plan['canvas_reason'] == 'grounded_map'
    assert chart_plan['canvas_reason'] == 'director_primary_explanation'


def test_transparent_diagram_preserves_real_media_even_with_stale_canvas_role():
    scene = {'primary_visual': {'type': 'Diagram', 'props': {'transparent_canvas': True}},
             'visual_role': 'primary_explanation', 'media': {'src': 'touchscreen.jpg', 'type': 'image'}}
    plan = plan_media_composition(scene)
    assert plan['hero_layer'] == 'source_media'
    assert plan['render_mode'] == 'media_first_overlay'


def test_narrated_team_lead_score_routes_to_brief_media_preserving_comparison():
    scene = {
        'start': 0, 'duration': 3,
        'narration': 'Super Bowl XLIX: Seattle trails New England, 28–24.',
        'primary_visual': {'type': 'StatCard', 'variant': 'hero_counter_large', 'props': {'value': 28}},
        'graphic': {'template_id': 'stat-counter', 'props': {'value': 28, 'display_text': '28–24'}},
        'media': {'src': 'unrelated-footage.mp4', 'type': 'video'},
    }
    manifest = compile_production_timeline({'fps': 30, 'scenes': [scene]})
    assert scene['primary_visual']['type'] == 'Comparison'
    assert scene['primary_visual']['props']['leftLabel'] == 'Seattle'
    assert scene['primary_visual']['props']['leftValue'] == '24'
    assert scene['primary_visual']['props']['rightLabel'] == 'New England'
    assert scene['primary_visual']['props']['rightValue'] == '28'
    assert scene['visual_role'] == 'media_overlay'
    assert scene['media_composition']['render_mode'] == 'media_first_overlay'
    assert scene['media_composition']['media_visibility'] >= .78
    assert scene['primary_visual']['props']['transparent_canvas'] is True
    assert scene['editor_graphic_window']['duration_frames'] == 72
    assert scene['editor_graphic_window']['reason'] == 'exact_narrated_score'
    assert any(item['component'] == 'AdaptiveComparison' and item['duration_frames'] == 72
               for item in manifest['editing_timeline']['tracks']['graphics'])
    assert scene['graphic']['template_id'] == 'split-screen'
    assert (scene['graphic']['props']['leftLabel'], scene['graphic']['props']['leftValue']) == ('Seattle', '24')
    assert (scene['graphic']['props']['rightLabel'], scene['graphic']['props']['rightValue']) == ('New England', '28')
    assert 'display_text' not in scene['graphic']['props']
    assert manifest['production_audit']['status'] == 'pass'


def test_timeline_adds_short_script_grounded_people_tags_and_contextual_full_names():
    scenes = [
        {
            'start': 0, 'duration': 6,
            'narration': 'Russell Wilson takes the snap and fires a slant toward Ricardo Lockette.',
            'primary_visual': {'type': 'SmartVideo'},
            'media': {'src': 'verified-play.mp4', 'type': 'video'},
            'word_cues': [{'timed_words': [
                {'index': 0, 'text': 'Russell', 'start': .2},
                {'index': 1, 'text': 'Wilson', 'start': .4},
            ]}],
        },
        {
            'start': 6, 'duration': 5,
            'narration': 'Malcolm Butler, an undrafted rookie from West Alabama, reads the route and cuts in front of Lockette.',
            'primary_visual': {'type': 'SmartVideo'},
            'media': {'src': 'verified-interception.mp4', 'type': 'video'},
        },
        {
            'start': 11, 'duration': 5,
            'narration': 'Moments earlier, Jermaine Kearse’s juggling catch kept Seattle alive.',
            'primary_visual': {'type': 'SmartVideo'},
            'media': {'src': 'verified-catch.mp4', 'type': 'video'},
        },
    ]
    manifest = compile_production_timeline({'fps': 30, 'scenes': scenes})
    assert scenes[0]['film_person_entities'] == ['Russell Wilson', 'Ricardo Lockette', 'Malcolm Butler', 'Jermaine Kearse']
    cues = [[cue for cue in scene.get('motion_cues', []) if cue.get('element') == 'person'] for scene in scenes]
    assert [cue['display_text'] for cue in cues[0]] == ['Russell Wilson', 'Ricardo Lockette']
    assert cues[0][0]['time_local'] == .2
    assert cues[1][0]['display_text'] == 'Malcolm Butler'
    # A last-second mention gets no flashing tag; the full name was already
    # identified on the preceding play and remains in film-wide context.
    assert len(cues[1]) == 1
    assert cues[2][0]['display_text'] == 'Jermaine Kearse'
    assert all(1.5 <= cue['duration_seconds'] <= 1.9 for group in cues for cue in group)
    assert not any('West Alabama' in cue['display_text'] for group in cues for cue in group)
    tags = [item for item in manifest['editing_timeline']['tracks']['graphics']
            if item['component'] == 'DirectorPersonTag']
    assert {item['text'] for item in tags} >= {'Russell Wilson', 'Ricardo Lockette', 'Malcolm Butler', 'Jermaine Kearse'}
    assert all(item['copy_source'] == 'exact_script_entity' and item['duration_frames'] <= 57 for item in tags)


def test_timeline_tracks_exact_director_data_cue_only_when_media_is_available():
    scenes = [
        {'start': 0, 'duration': 2.5, 'narration': 'February 1, 2015.',
         'primary_visual': {'type': 'SmartImage'}, 'media': {'src': 'archive.jpg', 'type': 'image'},
         'motion_cues': [{'element': 'data', 'action': 'reveal', 'anchor_text': 'February 1, 2015.', 'time_local': 0}]},
        {'start': 2.5, 'duration': 2.5, 'narration': 'The chart shows the exact value.',
         'primary_visual': {'type': 'Chart'}, 'media': {'src': 'chart.jpg', 'type': 'image'},
         'motion_cues': [{'element': 'data', 'action': 'count', 'anchor_text': 'exact value', 'time_local': .4}]},
    ]
    manifest = compile_production_timeline({'fps': 30, 'scenes': scenes})
    callouts = [item for item in manifest['editing_timeline']['tracks']['graphics']
                if item['component'] == 'DirectorDataCallout']
    assert len(callouts) == 1
    assert callouts[0]['text'] == 'February 1, 2015.'
    assert callouts[0]['start_frame'] == 0
    assert 40 <= callouts[0]['duration_frames'] <= 52


def test_event_prefaced_lead_score_routes_to_correct_team_comparison():
    scene = {
        'start': 0, 'duration': 3,
        'narration': 'At Super Bowl XLIX, the Seahawks trail the Patriots, 28–24.',
        'primary_visual': {'type': 'StatCard', 'props': {'value': 28, 'display_text': '28–24'}},
    }
    manifest = compile_production_timeline({'fps': 30, 'scenes': [scene]})
    props = scene['primary_visual']['props']
    assert scene['primary_visual']['type'] == 'Comparison'
    assert (props['leftLabel'], props['leftValue']) == ('Seahawks', '24')
    assert (props['rightLabel'], props['rightValue']) == ('Patriots', '28')
    assert manifest['production_audit']['status'] == 'pass'


@pytest.mark.parametrize(('narration','first','first_score','second','second_score'), [
    ('Seattle trails New England, 28–24.', 'Seattle', '24', 'New England', '28'),
    ('New England leads Seattle, 24–28.', 'New England', '28', 'Seattle', '24'),
    ('Seattle is behind New England, 24–28.', 'Seattle', '24', 'New England', '28'),
    ('At Super Bowl XLIX, the Seahawks trail the Patriots, 28–24.', 'Seahawks', '24', 'Patriots', '28'),
])
def test_lead_relation_binds_high_score_to_leader_independent_of_number_order(
    narration, first, first_score, second, second_score,
):
    from services.editorial_director.production_contract import _narrated_score_comparison

    result=_narrated_score_comparison(narration)
    assert result=={
        'leftLabel':first,'leftValue':first_score,
        'rightLabel':second,'rightValue':second_score,
    }


def test_lead_relation_does_not_ground_a_tied_score():
    from services.editorial_director.production_contract import _narrated_score_comparison

    assert _narrated_score_comparison('Seattle leads New England, 24–24.') is None


def test_timeline_recompile_discards_legacy_automatic_canvas_assignment_but_keeps_llm_choice():
    legacy = {'start': 0, 'duration': 4, 'narration': 'A chart shows two results.',
              'primary_visual': {'type': 'Chart'}, 'visual_role': 'primary_explanation',
              'media': {'src': 'real.mp4', 'type': 'video'},
              'media_composition': {'hero_layer': 'source_media', 'render_mode': 'media_only'}}
    directed = {'start': 4, 'duration': 4, 'narration': 'The diagram explains a real process.',
                'primary_visual': {'type': 'Diagram'}, 'visual_role': 'primary_explanation',
                'visual_role_source': 'llm_director',
                'media': {'src': 'other.mp4', 'type': 'video'}}
    compile_production_timeline({'fps': 30, 'scenes': [legacy, directed]})
    assert legacy['visual_role'] == 'media_overlay'
    assert legacy['media_composition']['render_mode'] == 'media_first_overlay'
    assert directed['visual_role'] == 'primary_explanation'
    assert directed['media_composition']['render_mode'] == 'explanatory_canvas'


def test_directed_stat_counter_and_generated_recipe_routes_match_renderer_registry():
    manifest = {'fps': 30, 'scenes': [
        {'start': 0, 'duration': 3, 'director_treatment_enabled': True,
         'visual_role_source': 'llm_director', 'primary_visual': {'type': 'StatCard'},
         'template_id': 'stat-counter', 'media': {'src': 'number.jpg', 'type': 'image'}},
        {'start': 3, 'duration': 3, 'director_treatment_enabled': True,
         'visual_role_source': 'llm_director', 'primary_visual': {'type': 'SmartImage', 'props': {'template_recipe': {
             'version': 2, 'id': 'grounded-recipe', 'name': 'Grounded title',
             'layers': [{'kind': 'text', 'text': 'The diagram explains a real process.', 'x': 15, 'y': 20, 'width': 70, 'height': 20}],
         }}},
         'narration': 'The diagram explains a real process.',
         'template_id': 'generated-recipe', 'media': {'src': 'diagram.jpg', 'type': 'image'}},
    ]}
    compile_production_timeline(manifest)
    assert [scene['graphic_component_mapping'] for scene in manifest['scenes']] == ['AdaptiveStat', 'DeclarativeRecipe']
    assert manifest['production_audit']['status'] == 'pass'


def test_director_kinetic_text_alias_is_registered_for_production_audit_and_remotion():
    scene = {
        'start': 0, 'duration': 3, 'director_treatment_enabled': True,
        'visual_role_source': 'llm_director', 'primary_visual': {'type': 'KineticText'},
        'template_id': 'kinetic-text', 'media': {'src': 'verified.jpg', 'type': 'image'},
    }
    manifest = compile_production_timeline({'fps': 30, 'scenes': [scene]})
    assert scene['component_mapping'] == 'EditorialType'
    assert scene['graphic_component_mapping'] == 'EditorialType'
    assert manifest['production_audit']['status'] == 'pass'


def test_empty_recipe_panel_is_removed_and_does_not_block_person_callout():
    scene = {
        'start': 0, 'duration': 4, 'director_treatment_enabled': True,
        'visual_role_source': 'llm_director', 'visual_role': 'media_overlay',
        'narration': 'Malcolm Butler reads the route.',
        'primary_visual': {'type': 'SmartImage', 'props': {}},
        'media': {'src': 'butler.jpg', 'type': 'image'},
        'template_id': 'generated-recipe',
        'template_recipe': {'version': 2, 'id': 'empty-draft', 'name': 'Draft visual', 'review_status': 'draft', 'layers': [
            {'kind': 'rect', 'x': 10, 'y': 15, 'width': 80, 'height': 70, 'color': '#111b17'},
            {'kind': 'group', 'x': 0, 'y': 0, 'width': 10, 'height': 10},
        ]},
    }
    manifest = compile_production_timeline({'fps': 30, 'scenes': [scene]})
    assert scene['recipe_repair']['decision'] == 'rejected'
    assert scene['template_id'] == 'full-bleed'
    assert 'template_recipe' not in scene
    assert scene['primary_visual']['type'] == 'SmartImage'
    assert any(item['component'] == 'DirectorPersonTag' and item['text'] == 'Malcolm Butler'
               for item in manifest['editing_timeline']['tracks']['graphics'])
    assert manifest['production_audit']['status'] == 'pass'


def test_grounded_graphic_fallback_borrows_nonadjacent_verified_same_entity_still(tmp_path):
    nearby = tmp_path / 'nearby.jpg'
    same_story = tmp_path / 'same-story.jpg'
    fallback = tmp_path / 'placeholder.png'
    for path in (nearby, same_story, fallback):
        path.write_bytes(b'image fixture')

    def visual_asset(entity):
        return {'resolution': {'final_scene_context': {'visual_requirement': {
            'canonical_entity': entity, 'required_terms': [entity], 'positive_evidence': [entity],
        }}}}

    manifest = {'fps': 30, 'scenes': [
        {'start': 0, 'duration': 2, 'narration': 'Glendale, Arizona.',
         'primary_visual': {'type': 'SmartImage', 'asset': visual_asset('Super Bowl XLIX')},
         'media': {'src': str(nearby), 'type': 'image', 'shots': [{
             'path': str(nearby), 'media_type': 'image', 'verification': 'verified',
             'fallback_layer': 'primary', 'source_url': 'https://example.test/super-bowl-xlix-stadium',
             'asset_fingerprint': 'nearby-stadium',
         }]}},
        {'start': 2, 'duration': 2, 'narration': 'February 1, 2015.',
         'visual_intent': 'Establish the exact date for Super Bowl XLIX.',
         'primary_visual': {'type': 'SmartImage', 'asset': visual_asset('Super Bowl XLIX')},
         'media': {'src': str(fallback), 'type': 'image', 'source': 'graphic',
                   'resolution_kind': 'grounded_graphic_fallback', 'shots': [{
                       'path': str(fallback), 'media_type': 'image', 'verification': 'graphic_fallback',
                       'fallback_layer': 'degraded:no_verified_media',
                   }]}},
        {'start': 4, 'duration': 2, 'narration': 'The Moon blocks sunlight.',
         'primary_visual': {'type': 'SmartImage', 'asset': visual_asset('Solar eclipse')},
         'media': {'src': str(nearby), 'type': 'image'}},
        {'start': 6, 'duration': 2, 'narration': 'The game at the University of Phoenix Stadium changed.',
         'primary_visual': {'type': 'SmartImage', 'asset': visual_asset('Super Bowl XLIX')},
         'media': {'src': str(same_story), 'type': 'image', 'source': 'web', 'shots': [{
             'path': str(same_story), 'media_type': 'image', 'verification': 'verified',
             'fallback_layer': 'verified_alternate', 'source_url': 'https://example.test/super-bowl-xlix-stadium',
             'asset_fingerprint': 'same-story-stadium',
         }]}},
    ]}

    compiled = compile_production_timeline(manifest)
    repaired = manifest['scenes'][1]
    assert repaired['media']['src'] == str(same_story)
    assert repaired['media_repair']['decision'] == 'reused_verified_same_entity_still'
    assert repaired['media_repair']['source_scene_index'] == 3
    assert repaired['media']['shots'][0]['reuse_kind'] == 'same_entity_nonadjacent_media_recovery'
    assert compiled['editing_timeline']['tracks']['media'][1]['repair'] == repaired['media_repair']


def test_graphic_fallback_is_not_patched_with_unrelated_stills(tmp_path):
    unrelated = tmp_path / 'royal-family.jpg'
    fallback = tmp_path / 'placeholder.png'
    unrelated.write_bytes(b'image fixture')
    fallback.write_bytes(b'image fixture')
    def visual_asset(entity):
        return {'resolution': {'final_scene_context': {'visual_requirement': {'canonical_entity': entity}}}}
    scene = {'start': 0, 'duration': 2, 'narration': 'February 1, 2015.',
             'primary_visual': {'type': 'SmartImage', 'asset': visual_asset('Super Bowl XLIX')},
             'media': {'src': str(fallback), 'type': 'image', 'source': 'graphic',
                       'resolution_kind': 'grounded_graphic_fallback', 'shots': [{
                           'path': str(fallback), 'media_type': 'image', 'verification': 'graphic_fallback',
                           'fallback_layer': 'degraded:no_verified_media',
                       }]}}
    other = {'start': 2, 'duration': 2, 'narration': 'A royal family gathers.',
             'primary_visual': {'type': 'SmartImage', 'asset': visual_asset('British royal family')},
             'media': {'src': str(unrelated), 'type': 'image', 'shots': [{
                 'path': str(unrelated), 'media_type': 'image', 'verification': 'verified',
                 'fallback_layer': 'primary', 'source_url': 'https://example.test/royal-family',
             }]}}
    compile_production_timeline({'fps': 30, 'scenes': [scene, other]})
    assert scene['media']['src'] == str(fallback)
    assert 'media_repair' not in scene


def test_normalized_vision_boxes_protect_subject():
    scene = {'media': {'src': 'portrait.jpg', 'visual_decision': {
        'subject_box': {'x': .05, 'y': .05, 'width': .4, 'height': .8},
        'safe_text_regions': [{'x': .6, 'y': .2, 'width': .3, 'height': .2}]}}}
    box = choose_media_safe_region(scene)
    assert box[0] >= .5


def test_explicit_map_directive_is_not_downgraded_to_diagram():
    assert script_requirements('Watch the path on a map.')['map_required']
    assert not script_requirements('He mapped his career in science.')['map_required']
    manifest = {'fps': 30, 'scenes': [{'start': 0, 'duration': 4, 'narration': 'Watch the path on a map.', 'primary_visual': {'type': 'Diagram'}}]}
    compile_production_timeline(manifest)
    assert manifest['production_audit']['status'] == 'fail'
    assert manifest['production_audit']['errors'][0]['code'] == 'required_map_not_routed'
    manifest['scenes'][0]['primary_visual']['type'] = 'AnimatedMap'
    compile_production_timeline(manifest)
    assert manifest['production_audit']['status'] == 'pass'


def test_copy_track_and_renderer_window_share_exact_narrated_cue():
    scene = {'start': 10, 'duration': 5, 'narration': 'The Moon blocks sunlight.', 'primary_visual': {'type': 'SmartImage'},
             'editorial_contract': {'version': 4, 'source': 'llm', 'source_span': 'The Moon blocks sunlight.', 'approved': True, 'display_text': 'Moon', 'emphasis_spans': [{'start': 0, 'end': 4}]},
             'motion_cues': [{'element': 'copy', 'time_local': 1.2}]}
    manifest = compile_production_timeline({'fps': 60, 'scenes': [scene]})
    assert scene['copy_window'] == {'clock': 'scene_local_frames', 'start_frame': 72, 'duration_frames': 78}
    copy = manifest['editing_timeline']['tracks']['copy'][0]
    assert copy['start_frame'] == 672
    assert copy['text'] == 'Moon'
    assert copy['emphasis_spans'] == [{'start': 0, 'end': 4}]


def test_llm_director_component_copy_and_motion_survive_media_first_handoff():
    copy_contract = {
        'version': 4, 'source': 'llm', 'source_span': 'The Moon blocks sunlight.',
        'approved': True, 'display_text': 'Moon', 'emphasis_spans': [{'start': 0, 'end': 4}],
    }
    scene = {
        'scene_index': 0, 'start': 0, 'duration': 4, 'narration': 'The Moon blocks sunlight.',
        'director_treatment_enabled': True,
        'primary_visual': {'type': 'KineticText', 'variant': 'bold_minimal_center', 'props': {
            'text': 'Moon', 'display_text': 'Moon', 'editorial_contract': dict(copy_contract),
        }},
        'editorial_contract': dict(copy_contract),
        'motion_cues': [{'element': 'copy', 'action': 'reveal', 'anchor_text': 'Moon blocks', 'time_local': 0.4}],
        'media': {'src': 'eclipse.mp4', 'type': 'video'},
        'media_composition': {'hero_layer': 'source_media', 'render_mode': 'media_only', 'media_visibility': 1.0},
    }

    manifest = apply_media_first_direction({'fps': 30, 'scenes': [scene]}, plan_media_composition)
    final = manifest['scenes'][0]

    assert final['primary_visual']['type'] == 'KineticText'
    assert final['primary_visual']['props']['text'] == 'Moon'
    assert final['motion_cues'] == scene['motion_cues']
    assert final['editorial_contract'] == copy_contract
    assert final['editorial_contract']['source'] == 'llm'
    assert final['media_composition']['render_mode'] == 'media_first_overlay'
    assert manifest['editing_timeline']['tracks']['copy'][0]['text'] == 'Moon'
    assert manifest['quality_control']['ready_for_long_render'] is True


def test_repeated_media_hidden_by_primary_graphic_is_not_a_release_blocker():
    manifest = {
        'fps': 30,
        'media_shot_plan': {'perceptual_repetition_audit': {
            'status': 'needs_revision',
            'unresolved_repeats': [{'scene_index': 1, 'shot_index': 0, 'candidate_id': 'reused-image'}],
        }},
        'scenes': [
            {'start': 0, 'duration': 3, 'narration': 'A real eclipse moves.', 'director_treatment_enabled': True,
             'primary_visual': {'type': 'SmartVideo'}, 'media': {'src': 'a.mp4', 'type': 'video'}},
            {'start': 3, 'duration': 3, 'narration': 'The Moon blocks sunlight.', 'director_treatment_enabled': True,
             'primary_visual': {'type': 'Diagram', 'props': {'steps': [{'title': 'Moon'}, {'title': 'Earth'}]}},
             'visual_role': 'primary_explanation',
             'visual_role_source': 'llm_director',
             'media': {'src': 'a.mp4', 'type': 'video'}},
        ],
    }

    compile_production_timeline(manifest)

    audit = manifest['media_shot_plan']['perceptual_repetition_audit']
    assert audit['visible_unresolved_repeats'] == []
    assert len(audit['hidden_under_explanatory_canvas']) == 1
    assert manifest['production_audit']['status'] == 'pass'
    assert manifest['quality_control']['ready_for_long_render'] is True


def test_pixel_director_primary_explanation_role_survives_final_timeline_compile():
    scene={'start':0,'duration':3,'narration':'The Moon blocks sunlight.',
           'primary_visual':{'type':'Diagram','props':{}},
           'visual_role':'primary_explanation','visual_role_source':'llm_pixel_director',
           'media':{'src':'source.jpg','type':'image'}}
    compile_production_timeline({'fps':30,'scenes':[scene]})
    assert scene['visual_role']=='primary_explanation'
    assert scene['media_composition']['render_mode']=='explanatory_canvas'


def test_single_milestone_preserves_actual_media_under_timeline():
    scene = {'start': 0, 'duration': 4, 'narration': 'Roadster deliveries began in 2008.',
             'primary_visual': {'type': 'Timeline', 'props': {'events': [{'date': '2008'}]},
                                'asset': {'src': 'roadster.jpg', 'type': 'image', 'is_graphic_fallback': False}},
             'visual_role': 'primary_explanation',
             'media': {'src': 'roadster.jpg', 'type': 'graphic', 'is_graphic_fallback': True}}
    compile_production_timeline({'fps': 30, 'scenes': [scene]})
    assert scene['visual_role'] == 'media_overlay'
    assert scene['media']['type'] == 'image'
    assert not scene['media']['is_graphic_fallback']
    assert scene['media_composition']['hero_layer'] == 'source_media'
    assert scene['media_composition']['overlay_grammar'] == 'chronology_strip'


def test_copy_window_prefers_word_timing_and_never_moves_late_phrase_earlier():
    scene={'start':0,'duration':5,'narration':'The Moon blocks sunlight.',
           'primary_visual':{'type':'SmartImage'},
           'editorial_contract':{'version':4,'source':'llm','approved':True,'display_text':'Moon','source_span':'Moon'},
           'motion_cues':[{'element':'copy','time_local':1}],
           'word_cues':[{'timed_words':[{'index':1,'text':'Moon','start':4.6,'end':4.9}]}]}
    film=compile_production_timeline({'fps':30,'scenes':[scene]})
    assert scene['copy_window']['start_frame']==138
    assert scene['copy_window']['duration_frames']==12
    assert film['editing_timeline']['tracks']['copy'][0]['start_frame']==138


def test_opening_approved_hook_starts_at_first_frame_but_later_copy_stays_spoken_aligned():
    opening = {
        'start': 0, 'duration': 5, 'narration': 'What happened next changed everything.',
        'primary_visual': {'type': 'ChapterTitle'},
        'text': {'frame_mode': 'chapter'},
        'editorial_contract': {'version': 4, 'source': 'llm', 'approved': True,
                              'display_text': 'What happened next', 'source_span': 'What happened next'},
        'word_cues': [{'timed_words': [{'index': 0, 'text': 'What', 'start': 1.5, 'end': 1.7}]}],
    }
    later = {
        'start': 5, 'duration': 5, 'narration': 'Then the final door opened.',
        'primary_visual': {'type': 'SmartImage'},
        'editorial_contract': {'version': 4, 'source': 'llm', 'approved': True,
                              'display_text': 'final door', 'source_span': 'final door'},
        'word_cues': [{'timed_words': [{'index': 0, 'text': 'Then', 'start': 2, 'end': 2.2},
                                      {'index': 1, 'text': 'the', 'start': 2.2, 'end': 2.3},
                                      {'index': 2, 'text': 'final', 'start': 2.3, 'end': 2.5},
                                      {'index': 3, 'text': 'door', 'start': 2.5, 'end': 2.7}]}],
    }
    film = compile_production_timeline({'fps': 30, 'scenes': [opening, later]})
    assert opening['copy_window']['start_frame'] == 0
    assert later['copy_window']['start_frame'] >= 60
    assert film['editing_timeline']['tracks']['copy'][0]['start_frame'] == 0


def test_fallback_media_is_flagged_for_visual_repair_without_inventing_subject():
    from services.editorial_director.visual_critic import audit_manifest
    scenes = [{
        'scene_index': index, 'director_treatment_enabled': True,
        'narration': 'A quiet visual beat holds on the landscape.',
        'primary_visual': {'type': 'SmartImage', 'variant': 'full_bleed_cinematic'},
        'media': ({'src': 'graphic_fallback.png', 'source': 'graphic',
                   'resolution_kind': 'grounded_graphic_fallback'} if index == 0 else {'src': f'image-{index}.jpg'}),
    } for index in range(8)]
    report = audit_manifest({'scenes': scenes})
    assert any(item['code'] == 'uncomposed_graphic_fallback' for item in report['findings'])


def test_verified_source_allows_only_hash_bound_h264_render_transcode(tmp_path):
    import hashlib

    source = tmp_path / 'reviewed-av1.mp4'
    render = tmp_path / 'render-h264.mp4'
    source.write_bytes(b'original reviewed media bytes')
    render.write_bytes(b'normalized render media bytes')
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    render_hash = hashlib.sha256(render.read_bytes()).hexdigest()
    shot = {
        'path': str(render),
        'verified_asset_sha256': source_hash,
        'visual_decision': {'verified_asset_sha256': source_hash},
        'render_transform': {
            'schema_version': 'render_media_normalization.v1',
            'kind': 'codec_transcode',
            'tool': 'vidzeo_media_snapshot_v1',
            'source_path': str(source), 'source_sha256': source_hash,
            'render_path': str(render), 'render_sha256': render_hash,
            'source_codec': 'av1', 'render_codec': 'h264',
        },
    }
    manifest = {'fps': 30, 'scenes': [{'start': 0, 'duration': 3, 'narration': 'A verified play.',
        'primary_visual': {'type': 'SmartVideo'}, 'media': {'shots': [shot]}}]}

    compile_production_timeline(manifest)
    assert manifest['production_audit']['status'] == 'pass'

    render.write_bytes(b'changed after the transcode record')
    compile_production_timeline(manifest)
    assert manifest['production_audit']['status'] == 'fail'
    assert any(error['code'] == 'verified_shot_changed_after_review'
               for error in manifest['production_audit']['errors'])


def test_missing_data_manager_fallback_has_no_narration_card():
    scene = {'narration': 'A complex sentence must never become a card.', 'primary_visual': {'type': 'Chart', 'props': {}},
             'text': {'display_text': 'old'}, 'editorial_contract': {'approved': True, 'display_text': 'old'}}
    result = apply_manager_corrections({'scenes': [scene]})['scenes'][0]
    assert result['primary_visual']['type'] == 'SmartImage'
    assert result['text']['overlay_policy'] == 'none'
    assert not result['editorial_contract']['approved']
    assert result['primary_visual']['props'] == {}


@pytest.mark.parametrize('source,span', [('local','Moon'), ('llm','invented phrase'), ('', 'Moon')])
def test_copy_track_rejects_unapproved_provenance_and_invented_spans(source,span):
    scene={'start':0,'duration':5,'narration':'The Moon blocks sunlight.',
           'primary_visual':{'type':'SmartImage'},
           'editorial_contract':{'version':4,'source':source,'approved':True,'display_text':'Moon','source_span':span},
           'copy_window':{'start_frame':0,'duration_frames':150}}
    result=compile_production_timeline({'fps':30,'scenes':[scene]})
    assert result['editing_timeline']['tracks']['copy']==[]
    assert 'copy_window' not in scene


def test_groups_masks_paths_validate_without_executable_code():
    raw = {'id': 'masked-science', 'name': 'Masked science', 'layers': [
        {'id': 'panel', 'kind': 'group', 'x': 5, 'y': 10, 'width': 90, 'height': 65, 'shape': 'circle', 'clip': True},
        {'id': 'trace', 'parent_id': 'panel', 'kind': 'path', 'x': 5, 'y': 5, 'width': 90, 'height': 90,
         'points': [{'x': 0, 'y': 50}, {'x': 50, 'y': 0}, {'x': 100, 'y': 50}]}]}
    safe = validate_template_recipe(raw)
    assert safe['layers'][1]['parent_id'] == 'panel'
    assert len(safe['layers'][1]['points']) == 3
    raw['layers'][0]['parent_id'] = 'panel'
    with pytest.raises(ValueError, match='cycles'):
        validate_template_recipe(raw)
    raw['layers'][0]['parent_id'] = 'missing'
    with pytest.raises(ValueError, match='parent'):
        validate_template_recipe(raw)


def test_direct_renderer_refuses_low_memory(monkeypatch):
    code = "const os=require('os'),assert=require('assert');os.totalmem=()=>8*1024**3;os.freemem=()=>200*1024**2; const {adaptiveConcurrency}=require(process.argv[1]); assert.throws(()=>adaptiveConcurrency({width:1920,height:1080}),e=>e.code==='VIDZEO_LOW_MEMORY');os.freemem=()=>4*1024**3; assert.ok(adaptiveConcurrency({width:1920,height:1080}).concurrency>=1);"
    monkeypatch.delenv('VIDZEO_REMOTION_AVAILABLE_RAM_MB', raising=False)
    result = subprocess.run([shutil.which('node'), '-e', code, str(ROOT/'scripts/render-remotion-manifest.js')], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_os_render_slot_excludes_second_export_and_recovers():
    code = "const assert=require('assert');const {acquireRenderLease}=require(process.argv[1]);(async()=>{const a=await acquireRenderLease(0);const port=a.address().port;await assert.rejects(acquireRenderLease(port),e=>e.code==='VIDZEO_RENDER_BUSY');await new Promise(r=>a.close(r));const b=await acquireRenderLease(port);await new Promise(r=>b.close(r));})().catch(e=>{console.error(e);process.exitCode=1});"
    result = subprocess.run([shutil.which('node'), '-e', code, str(ROOT/'scripts/render-remotion-manifest.js')], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_vision_rechecks_pixels_after_asset_replacement(tmp_path, monkeypatch):
    import hashlib
    import services.pipeline_v2_media_adapter as adapter
    from services.visual_intelligence.context import VisualDecisionContext
    old_path, new_path = tmp_path/'old.jpg', tmp_path/'new.jpg'
    old_path.write_bytes(b'old pixels')
    new_path.write_bytes(b'different pixels')
    verdict = {'candidate_id':'youtube:moon', 'identity_score':95, 'action_score':90, 'context_score':95, 'quality':90,
               'entity_conflict':False, 'fatal_mismatch':False, 'visible_overlays':[], 'subject_box':{}, 'safe_text_regions':[],
               'reason':'The image shows the Moon.', 'visual_checked':True, 'visual_available':True,
               'source':'youtube', 'verified_asset_sha256':hashlib.sha256(old_path.read_bytes()).hexdigest(),
               'verified_requirement_hash':'moon-req'}
    class Client:
        model = 'test-vision'
        calls = 0
        def analyze_image(self, path, *args):
            self.calls += 1
            assert path == str(new_path)
            return dict(verdict)
    client = Client()
    context = VisualDecisionContext.from_config({'visual_understanding':True,'openrouter_api_key':'test-key'},scene_count=1)
    context._client = object()
    context.get_client = lambda: client
    monkeypatch.setattr(adapter.VisualDecisionContext, 'from_config', lambda *a,**kw: context)
    scene = {'text':'The Moon is visible.', 'positive_subjects':['Moon'], 'visual_requirement':{'strict_identity':True,'canonical_entity':'Moon','requirement_hash':'moon-req'}}
    verify, _ = adapter.build_qwen_verifier({'visual_understanding':True}, [scene])
    candidate = {'path':str(new_path),'candidate_id':'youtube:moon','media_type':'image','source':'youtube','qwen_result':verdict}
    result = verify(scene, candidate)
    assert client.calls == 1
    assert result['verified_asset_sha256'] == hashlib.sha256(new_path.read_bytes()).hexdigest()
    verify(scene, candidate)
    assert client.calls == 1  # job cache is bound to those same bytes
    verdict['source_presentation_type'] = 'homework'
    verify_slides, _ = adapter.build_qwen_verifier({'visual_understanding':True}, [scene])
    slide_result = verify_slides(scene, candidate)
    assert slide_result['accepted'] is False
    assert slide_result['rejection_reason'] == 'unrequested_text_heavy_slide'
    assert slide_result['visual_review_policy_version'] == adapter.VISUAL_REVIEW_POLICY_VERSION


def test_director_falls_back_from_unfinished_bespoke_diagrams():
    from services.editorial_director import EditingDirector
    class Client:
        model = 'test-director'
        model_chain = [model]
        attempt_history = []
        def chat(self, **kwargs):
            stage = kwargs['json_schema_name']
            if stage == 'vidnets_art_direction_v1':
                return json.dumps({'display_font':'Archivo Black','body_font':'DM Sans','accent_color':'#E8B75C'})
            if stage == 'vidnets_scene_composition_v1':
                raise TimeoutError('optional composition unavailable')
            is_revision = stage == 'vidnets_scene_quality_revision_v1'
            if is_revision:
                assert kwargs['total_timeout_seconds'] <= 20
            return json.dumps({'scenes':[{'scene_index':i,'type':'SmartImage' if is_revision else 'Diagram',
                'variant':'full_bleed_cinematic' if is_revision else 'step_by_step_process',
                'frame_mode':'media_only' if is_revision else 'diagram','overlay_policy':'none',
                'diagram_nodes':[{'label':'striker','source_span':'The striker faces the keeper'},{'label':'keeper','source_span':'The striker faces the keeper'}],
                'template_recipe':None,'display_text':'','source_span':'','emphasis_spans':[], 'visual_intent':'Show actual football action.'} for i in range(5)]})
    plan = EditingDirector(llm_client=Client()).plan_documentary(script_text='The striker faces the keeper. '*5,
        scene_texts=['The striker faces the keeper.']*5,scene_durations=[4]*5,category='sports')
    assert sum(scene.primary_visual.type == 'Diagram' for scene in plan.scenes) <= 2
    assert plan.director_metadata['visual_quality_status'] == 'degraded_media_fallback'
    assert plan.director_metadata['composition_fallback_scene_indices'] == [0, 1, 2, 3, 4]
    assert all(scene.text['frame_mode'] == 'media_only' for scene in plan.scenes)


def test_review_samples_include_reveal_hold_exit():
    from services.editorial_director.production_contract import review_sample_times
    times = review_sample_times({'fps':30,'scenes':[{'start':0,'duration':6,'primary_visual':{'type':'Diagram'},'motion_cues':[{'time_local':2.1}]}]},6)
    assert .36 in times
    assert 2.1 in times
    assert 3.9 in times
    assert 5.64 in times


def test_review_samples_keep_scene_coverage_without_rendering_redundant_stills():
    from services.editorial_director.production_contract import review_sample_times
    scenes = [{'start':i*4,'duration':4,'primary_visual':{'type':'SmartImage'}} for i in range(14)]
    scenes.append({'start':56,'duration':4,'primary_visual':{'type':'Comparison'},'motion_cues':[{'time_local':1.25}]})
    times = review_sample_times({'fps':30,'scenes':scenes},60)
    assert len(times) < 40
    assert any(abs(time - 57.25) < .01 for time in times)
    assert any(time >= 59.7 for time in times)


def test_sound_transient_binds_actual_visual_event(monkeypatch):
    import services.semantic_edit_engine as engine
    monkeypatch.setattr(engine, 'plan_sound_cues', lambda *args: [])
    scene = {'start': 5, 'duration': 4, 'primary_visual': {'type': 'Diagram'},
             'motion_cues': [{'element': 'nodes', 'action': 'assemble', 'time_local': 1.2, 'anchor_text': 'Moon', 'timing_quality': 'whisper_word_aligned'}]}
    cues = engine.plan_directed_sound_cues(scene, 2, {'technical_tick': {'src': 'tick.wav', 'transient_offset_seconds': .2}})
    assert len(cues) == 1
    assert cues[0]['start'] == 6
    assert cues[0]['visual_event_time_local'] == 1.2
    assert cues[0]['transient_alignment_error_seconds'] == 0
    assert cues[0]['event_id'] == 'scene-2:nodes:assemble'


def test_early_sound_event_trims_silence_to_keep_transient_aligned(monkeypatch):
    import services.semantic_edit_engine as engine
    monkeypatch.setattr(engine, 'plan_sound_cues', lambda *args: [])
    scene = {'start':0, 'duration':3, 'primary_visual':{'type':'Diagram'},
             'motion_cues':[{'element':'nodes','action':'assemble','time_local':.1,'anchor_text':'Moon'}]}
    cues = engine.plan_directed_sound_cues(scene, 0, {'technical_tick':{'src':'tick.wav','transient_offset_seconds':.3}})
    assert cues[0]['time_local'] == 0
    assert cues[0]['trim_start_seconds'] == pytest.approx(.2)
    assert cues[0]['transient_alignment_error_seconds'] == 0


def test_sound_measurement_uses_actual_pcm_and_rejects_missing_files(tmp_path):
    import wave
    from array import array
    from services.editorial_director.sound_events import measure_sound
    path = tmp_path/'tick.wav'
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(1000)
        audio.writeframes(array('h', [0]*200 + [16384]*50 + [0]*250).tobytes())
    result = measure_sound(path)
    assert result['transient_offset_seconds'] == .2
    assert result['source_peak_dbfs'] == pytest.approx(-6.02, abs=.02)
    assert result['duration_seconds'] == .5
    assert measure_sound(tmp_path/'missing.wav') == {}


def test_unresolved_visual_revision_blocks_even_one_sparse_scene():
    manifest = compile_production_timeline({'director':{'visual_quality_status':'needs_revision','unresolved_visual_scene_indices':[0]},
        'scenes':[{'start':0,'duration':4,'primary_visual':{'type':'Diagram'}}]})
    assert manifest['production_audit']['status'] == 'fail'
    assert any(row['code'] == 'unresolved_visual_revision' for row in manifest['production_audit']['errors'])


@pytest.mark.parametrize('revision_failure', ['timeout', 'unknown_component'])
@pytest.mark.parametrize('pre_retrieval', [False, True])
def test_failed_creative_revision_keeps_verified_media_and_reports_fallback(revision_failure, pre_retrieval):
    from services.editorial_director import EditingDirector
    class Client:
        model = 'test-director'
        model_chain = [model]
        attempt_history = []
        def chat(self, **kwargs):
            stage = kwargs['json_schema_name']
            if stage == 'vidnets_art_direction_v1':
                return json.dumps({'display_font':'Archivo Black','body_font':'DM Sans','accent_color':'#E8B75C'})
            if stage == 'vidnets_scene_composition_v1':
                raise TimeoutError('composition unavailable')
            if stage == 'vidnets_scene_quality_revision_v1' and revision_failure == 'timeout':
                raise TimeoutError('revision unavailable')
            revision = stage == 'vidnets_scene_quality_revision_v1'
            return json.dumps({'scenes':[{'scene_index':i, 'type':'UnsupportedMagic' if revision else 'Diagram',
                'frame_mode':'media_only' if revision else 'diagram', 'overlay_policy':'none',
                'diagram_nodes':[{'label':'striker','source_span':'The striker faces the keeper'},
                                 {'label':'keeper','source_span':'The striker faces the keeper'}],
                'template_recipe':None,'display_text':'','source_span':'','emphasis_spans':[]} for i in range(2)]})
    plan = EditingDirector(llm_client=Client()).plan_documentary(
        script_text='The striker faces the keeper. '*2, scene_texts=['The striker faces the keeper.']*2,
        scene_durations=[4]*2, media_items=None if pre_retrieval else [{'src':'football.jpg','type':'image'}]*2, category='sports')
    assert all(scene.primary_visual.type == 'SmartImage' for scene in plan.scenes)
    assert plan.director_metadata['creative_coverage'] == 'partial'
    assert plan.director_metadata['directed_scene_count'] == 0
    assert plan.director_metadata['visual_quality_status'] == 'degraded_media_fallback'
    assert plan.director_metadata['local_fallback_scene_indices'] == [0,1]
    if pre_retrieval:
        import app
        bound = app._reconcile_parallel_director_plan(plan, ['The striker faces the keeper.']*2, [5,6],
            [{'src':'football.mp4','type':'video'}]*2, [])
        assert all(scene.primary_visual.type == 'SmartVideo' for scene in bound.scenes)
        assert bound.scenes[1].start == 5
        assert bound.director_metadata['media_hydrated_after_parallel_planning']


def test_generated_image_layers_can_bind_different_verified_image_slots():
    base = {'kind':'image','x':0,'y':0,'width':40,'height':40}
    recipe = validate_template_recipe({'id':'two-images','name':'Two images', 'layers':[
        {**base,'image_index':0}, {**base,'x':50,'image_index':1}]})
    assert [layer['image_index'] for layer in recipe['layers']] == [0,1]
    with pytest.raises(ValueError, match='image_index'):
        validate_template_recipe({'id':'bad-index','name':'Bad index','layers':[{**base,'image_index':-1}]})


def test_missing_or_empty_frame_review_cannot_pass(tmp_path):
    from services.editorial_director.visual_critic import audit_frames, intentional_transition_positions
    assert audit_frames([tmp_path/'missing.png'])['status'] == 'fail'
    assert audit_frames([])['status'] == 'fail'

    from PIL import Image
    blank=tmp_path/'intentional-dip.png'
    Image.new('RGB',(160,90),(0,0,0)).save(blank)
    detailed=tmp_path/'detailed.png'
    Image.effect_noise((160,90),64).convert('RGB').save(detailed)
    frames=[{'frame':0,'path':str(detailed)},{'frame':45,'path':str(blank)}]
    positions=intentional_transition_positions(frames,[{'scene_index':1,'start':1.5,'duration':.24,'template_id':'dip_black'}],30)
    assert positions=={2}
    report=audit_frames([detailed,blank],intentional_transition_positions=positions)
    assert report['status']=='pass'
    assert report['ignored_intentional_transition_frames']==1

    # Scene-boundary frames can be intentionally masked during a short
    # Remotion transition (the NFL test exposed a glitch-slice sample).
    glitch=intentional_transition_positions(frames,[{'start':1.5,'duration':.24,'template_id':'glitch_slice'}],30)
    assert glitch=={2}
    assert audit_frames([detailed,blank],intentional_transition_positions=glitch)['status']=='pass'

    # The exemption only applies to supported, bounded transitions; a blank
    # frame or a long black hold cannot be hidden behind arbitrary metadata.
    assert not intentional_transition_positions(frames,[{'start':1.5,'duration':.24,'template_id':'mystery'}],30)
    assert audit_frames([detailed,blank])['status']=='fail'

    long_dip=intentional_transition_positions(frames,[{'start':1.5,'duration':.8,'template_id':'dip_black'}],30)
    assert not long_dip


def test_final_timeline_refuses_changed_verified_asset_bytes(tmp_path):
    import hashlib
    path = tmp_path/'image.jpg'
    path.write_bytes(b'approved image')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {'scenes':[{'start':0,'duration':3,'primary_visual':{'type':'SmartImage'}, 'media':{'shots':[
        {'path':str(path),'visual_decision':{'verified_asset_sha256':digest},'verified_asset_sha256':digest,'verification_scope':'actual_asset'}]}}]}
    assert compile_production_timeline(manifest)['production_audit']['status'] == 'pass'
    path.write_bytes(b'replaced image')
    assert compile_production_timeline(manifest)['production_audit']['status'] == 'fail'


def test_vision_preserves_source_presentation_classification():
    from services.visual_intelligence.vision_client import _normalise_image_response
    assert _normalise_image_response({'source_presentation_type':'homework'})['source_presentation_type'] == 'homework'


def test_render_cleanup_uses_shared_process_tree_termination(monkeypatch):
    from services.native_render_policy import _stop_remotion_process
    from services.cancellation import CancellationContext
    calls = []
    process = object()
    monkeypatch.setattr(CancellationContext, '_terminate_process', staticmethod(calls.append))
    _stop_remotion_process(process)
    _stop_remotion_process(None)
    assert calls == [process]


@pytest.mark.parametrize('niche,kind', [('crime','DocumentHighlight'),('science','Diagram'),('sports','Chart'),('history','Timeline'),('business','Comparison'),('travel','AnimatedMap'),('biography','PersonProfile'),('technology','Diagram'),('nature','SmartVideo'),('culture','SmartImage')])
def test_multiple_niche_timelines_keep_distinct_component_roles(niche, kind):
    manifest = compile_production_timeline({'category': niche, 'fps': 30, 'scenes': [{'start':0,'duration':4,'narration':'A grounded documentary beat.', 'primary_visual':{'type':kind}, 'media':{'src':'asset.jpg'}}]})
    assert manifest['production_audit']['status'] == 'pass'
    assert len(manifest['editing_timeline']['tracks']) == 9
    assert manifest['scenes'][0]['component_mapping'] not in {'EditorialSceneCanvas', 'MediaOverlayCanvas'}


def test_director_selected_chart_template_survives_media_handoff_and_maps_to_remotion():
    scene = {
        'start': 0, 'duration': 4, 'narration': 'The measured rate climbed from 12 to 27.',
        'director_treatment_enabled': True, 'visual_role_source': 'llm_director',
        'primary_visual': {'type': 'SmartVideo', 'variant': 'media_hero'},
        'media': {'type': 'video', 'src': 'verified.mp4'},
        'template_id': 'line-chart', 'graphic': {'template_id': 'line-chart', 'props': {
            'data': [{'label': 'Before', 'value': 12}, {'label': 'After', 'value': 27}],
        }},
    }
    manifest = compile_production_timeline({'fps': 30, 'scenes': [scene]})
    assert scene['template_id'] == 'line-chart'
    assert scene['graphic']['props']['variant'] == 'trend_line_with_markers'
    assert scene['component_mapping'] == 'SmartVideo'
    assert scene['graphic_component_mapping'] == 'AdaptiveChart'
    assert manifest['editing_timeline']['tracks']['graphics'][0]['component'] == 'AdaptiveChart'
    assert manifest['production_audit']['status'] == 'pass'


def test_text_template_is_preserved_for_size_aware_copy_treatment():
    scene = {
        'start': 0, 'duration': 4, 'narration': 'Then the city changed overnight.',
        'director_treatment_enabled': True, 'visual_role_source': 'llm_director',
        'primary_visual': {'type': 'SmartVideo'}, 'media': {'type': 'video', 'src': 'verified.mp4'},
        'template_id': 'keyword-reveal', 'graphic': {'template_id': 'keyword-reveal'},
        'text': {'position': 'bottom-left'},
        'editorial_contract': {'version': 4, 'approved': True, 'source': 'llm', 'display_text': 'CHANGED OVERNIGHT', 'source_span': 'changed overnight', 'emphasis_spans': [{'start': 8, 'end': 17}]},
    }
    compile_production_timeline({'fps': 30, 'scenes': [scene]})
    assert scene['text']['template_id'] == 'keyword-reveal'
    assert scene['copy_window']['duration_frames'] <= 72


def test_builtin_director_catalog_has_a_remotion_template_mapping():
    import json
    catalog = json.loads((ROOT / 'resources/template-library/builtin/manifest.json').read_text(encoding='utf-8'))
    ids = {item['id'] for item in catalog.get('templates', [])}
    assert ids <= set(TEMPLATE_PRIMARY_TYPES)


def test_all_mapped_remotion_sound_effect_files_exist():
    import re
    source = (ROOT / 'src/remotion/DocumentaryComposition.jsx').read_text(encoding='utf-8')
    registry = source.split('const SFX_REGISTRY_MAP = {', 1)[1].split('\n};', 1)[0]
    assets = re.findall(r"['\"]([^'\"]+)['\"]\s*:\s*['\"]([^'\"]+)['\"]", registry)
    assert len(assets) >= 80
    assert all((ROOT / 'public' / asset).is_file() for _key, asset in assets)
def test_global_map_reconciliation_never_invents_a_route():
    from server.services.editorial_director.production_contract import reconcile_global_map_requests
    film={'scenes':[{'narration':'The Moon casts a shadow on Earth.','primary_visual':{'type':'Diagram'}},
                    {'narration':'Watch the path on a map.','primary_visual':{'type':'Diagram'},'template_recipe':{'layers':[{'kind':'diagram'}]}}]}
    assert reconcile_global_map_requests(film)==[1]
    scene=film['scenes'][1]
    assert scene['primary_visual']['type']=='AnimatedMap'
    assert scene['map_shot']['route_id']==''
    assert scene['map_shot']['route_status']=='not_established_by_script'
    assert scene['template_recipe']=={}


def test_review_sample_times_include_stat_hold_and_reduce_ordinary_media_samples():
    from services.editorial_director.production_contract import review_sample_times
    manifest = {'fps': 30, 'scenes': [
        {'start': 0, 'duration': 4, 'primary_visual': {'type': 'SmartVideo'}},
        {'start': 4, 'duration': 4, 'primary_visual': {'type': 'StatCard'}},
    ]}
    times = review_sample_times(manifest, 8)
    # Global boundary samples plus one ordinary scene hold and stat reveal/hold.
    assert len(times) <= 10
    assert any(5.0 <= value <= 7.9 for value in times)


def test_unknown_geographic_scope_stays_unresolved():
    from server.services.editorial_director.production_contract import reconcile_global_map_requests
    film={'scenes':[{'narration':'Watch the path on a map.','primary_visual':{'type':'Diagram'}}]}
    assert reconcile_global_map_requests(film)==[]
