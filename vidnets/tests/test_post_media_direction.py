import copy
import json
import time

from server.services.editorial_director.media_repair import apply_repair, repair_failed_shots
from server.services.recovery_contract import recover_manifest
from server.services.media_resolution_coordinator import _verification_result


def scene():
    return {'narration': 'The rocket carries the crew from Florida to the space station.',
            'duration': 5, 'media': {'source': 'graphic', 'src': 'graphic_fallback_0.png'},
            'primary_visual': {'type': 'SmartImage'}, 'text': {'frame_mode': 'media_only'}}


def proposal(index=0):
    return {'index': index, 'nodes': [{'label': 'Florida', 'source_span': 'from Florida'},
                                    {'label': 'space station', 'source_span': 'the space station'}],
            'relationship_span': 'from Florida to the space station', 'relationship': 'sequence',
            'variant': 'step_by_step_process'}


def test_grounded_repair_owns_canvas_and_preserves_provenance():
    s = scene()
    assert apply_repair(s, proposal())
    assert s['text']['frame_mode'] == 'diagram'
    assert s['media_composition']['allow_full_frame_graphic']
    assert s['primary_visual']['props']['node_copy_contract']['source'] == 'llm_scene_repair'
    assert len(s['primary_visual']['props']['links']) == 1
    assert s['recovery']['claims_source_footage'] is False


def test_invented_actor_is_rejected_without_mutation():
    s = scene()
    original = copy.deepcopy(s)
    p = proposal()
    p['nodes'][0]['label'] = 'Houston'
    assert not apply_repair(s, p)
    assert s == original


def test_independent_actors_do_not_get_invented_causal_links():
    s = scene()
    p = proposal()
    p['relationship'] = 'cause'
    assert apply_repair(s, p)
    assert s['primary_visual']['props']['links'] == []


def test_repair_chunks_keep_full_context_and_successful_media():
    good = {**scene(), 'media': {'source': 'web', 'src': 'accepted.jpg'}}
    manifest = {'scenes': [scene(), good]}
    class Client:
        def chat(self, **kwargs):
            context = json.loads(kwargs['user_prompt'])
            assert 'story_arc' not in context
            assert context['scenes'][0]['next']['index'] == 1
            return json.dumps({'scenes': [proposal()]})
    report = repair_failed_shots(manifest, client=Client())
    assert report['repaired'] == [0]
    assert manifest['scenes'][1] == good


def test_slow_provider_does_not_hold_film_or_publish_late_result():
    manifest = {'scenes': [scene()]}
    class Slow:
        def chat(self, **_):
            time.sleep(.15)
            return json.dumps({'scenes': [proposal()]})
    start = time.monotonic()
    report = repair_failed_shots(manifest, {'post_media_direction_seconds': .02}, Slow())
    assert time.monotonic() - start < .1
    assert report['unresolved'] == [0]
    time.sleep(.17)
    assert manifest['scenes'][0]['primary_visual']['type'] == 'SmartImage'


def test_grounded_stat_keeps_full_canvas_flags():
    s = scene()
    s['narration'] = 'There were 36 passengers.'
    recover_manifest({'scenes': [s]})
    assert s['media_composition']['enabled']
    assert s['media_composition']['allow_full_frame_graphic']


def test_promotional_web_thumbnail_is_not_soft_passed():
    candidate = {'source': 'web', 'url': 'https://i.ytimg.com/vi/abcdef/hqdefault.jpg'}
    verdict = {'accepted': True, 'relevance': 95, 'visual_checked': True, 'semantic_verified': True}
    accepted, reason, _, _ = _verification_result({'narration': 'A rocket launch.'}, candidate, lambda *_: verdict)
    assert not accepted
    assert reason == 'promotional_thumbnail_not_source_media'


def test_zero_stat_is_kept_by_manager():
    from server.services.editorial_director.manager import apply_manager_corrections
    manifest = {'scenes': [{'primary_visual': {'type': 'StatCard', 'props': {'value': 0}}}]}
    result = apply_manager_corrections(manifest)
    assert result['scenes'][0]['primary_visual']['type'] == 'StatCard'
    assert result['manager_corrections'] == []


def test_thumbnail_is_rejected_even_without_vision():
    candidate = {'source': 'web', 'url': 'https://i.ytimg.com/vi/abcdef/maxresdefault.jpg',
                 'candidate_evidence': {'identity_proof': True}}
    accepted, reason, _, _ = _verification_result({}, candidate, None)
    assert not accepted
    assert reason == 'promotional_thumbnail_not_source_media'


def test_group_profile_retains_media_and_uses_all_approved_names():
    s = {'narration': 'On board: Jessica Watkins and Luke Delaney.',
         'media': {'source': 'web', 'src': 'verified.jpg'},
         'primary_visual': {'type': 'PersonProfile', 'props': {'name': 'JESSICA WATKINS'}}}
    p = {'nodes': [{'label': 'Jessica Watkins', 'source_span': 'Jessica Watkins'},
                   {'label': 'Luke Delaney', 'source_span': 'Luke Delaney'}],
         'relationship_span': 'Jessica Watkins and Luke Delaney'}
    assert apply_repair(s, p)
    assert s['primary_visual']['type'] == 'PersonProfile'
    assert len(s['primary_visual']['props']['people']) == 2
    assert s['media']['src'] == 'verified.jpg'


def test_graphic_recovery_sets_renderer_contract_version():
    s = scene()
    assert apply_repair(s, proposal())
    assert s['visual_role_contract_version'] == 2


def test_native_render_wait_does_not_stall(monkeypatch):
    import threading
    import pytest
    from server.services import native_render_policy
    gate = threading.Lock()
    gate.acquire()
    monkeypatch.setattr(native_render_policy, '_RENDER_SLOT', gate)
    start = time.monotonic()
    with pytest.raises(TimeoutError) as failure:
        native_render_policy.render_semantic_documentary({}, 'unused.mp4', {'semantic_render_budget_seconds': .02})
    assert time.monotonic() - start < 1.5
    assert failure.value.stage == 'remotion_render'
    assert failure.value.retryable
