import ast
import inspect
from pathlib import Path

import pytest


@pytest.mark.parametrize('allowed', [True, False])
def test_late_youtube_rejection_has_one_policy_controlled_repair(monkeypatch, allowed):
    import app
    from services import pipeline_v2_media_adapter as adapter
    plan = {'media_paths': ['bad-cut.mp4'], 'metadata': {}}
    calls = []
    def validate(media, *args, **kwargs):
        calls.append('validate')
        if len(calls) == 1:
            media['metadata']['youtube_delivery_failures'] = [{'scene_index': 0, 'reason': 'facecam'}]
            raise RuntimeError('youtube_video_coverage_not_met: 0')
        assert media['media_paths'] == ['grounded.png']
        return media
    def repair(**kwargs):
        calls.append('repair')
        assert kwargs['rejected'] == [0]
        assert kwargs['media_plan']['media_paths'] == [None]
        kwargs['media_plan']['media_paths'] = ['grounded.png']
        return kwargs['media_plan'], []
    monkeypatch.setattr(adapter, 'validate_media_plan_for_render_v2', validate)
    monkeypatch.setattr(app, 'grounded_graphics_allowed', lambda _: allowed)
    monkeypatch.setattr(app, '_fill_strict_visual_delivery_fallback', repair)
    monkeypatch.setattr(app, '_sync_v2_audio_contracts_from_media', lambda *a: None)
    kwargs = dict(audio_plan={}, job_config={}, render_resolution=(854, 480), title='Test')
    if allowed:
        result = app._validate_final_v2_media_plan(plan, **kwargs)
        assert calls == ['validate', 'repair', 'validate']
        assert result['metadata']['late_youtube_rejections'][0]['reason'] == 'facecam'
    else:
        with pytest.raises(RuntimeError, match='youtube_video_coverage_not_met'):
            app._validate_final_v2_media_plan(plan, **kwargs)
        assert calls == ['validate']


def test_render_recovery_call_keywords_match_real_renderers():
    from services.semantic_ffmpeg_renderer import render_straight_media_ffmpeg
    from services.video_service import generate_video
    targets = {f.__name__: inspect.signature(f) for f in (render_straight_media_ffmpeg, generate_video)}
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'server/app.py').read_text(encoding='utf-8-sig'))
    checked = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in targets:
            signature = targets[node.func.id]
            for keyword in node.keywords:
                if keyword.arg:
                    assert keyword.arg in signature.parameters, (node.func.id, keyword.arg, node.lineno)
            checked += 1
    assert checked >= 2


@pytest.mark.parametrize('flag', ['synthetic_content', 'talking_head'])
def test_clip_quality_flags_survive_normalization_and_veto_named_broll(flag):
    from services.visual_intelligence.vision_client import _normalise_clip_response
    from services.pipeline_v2_media_adapter import _score_verdict
    verdict = _normalise_clip_response({flag: True, 'candidate_id': 'yt:test',
        'identity_score': 95, 'action_score': 90, 'context_score': 90,
        'scene_match': True, 'domain_match': True, 'final_score': 95,
        'source_presentation_type': 'video_frame', 'talking_head_confidence': 95})
    assert verdict[flag] is True
    scored = _score_verdict({'text': 'The Toyota Corolla drives on the road.',
        'visual_required': True, 'visual_requirement': {'strict_identity': True,
            'canonical_entity': 'Toyota Corolla', 'subject_type': 'automobile'}}, verdict)
    assert scored['accepted'] is False


@pytest.mark.parametrize('evidence', [dict(identity_confirmed=False),
    dict(reason='The specific model cannot be visually confirmed as a Toyota Corolla.')])
def test_same_industry_does_not_override_unknown_product_identity(evidence):
    from services.pipeline_v2_media_adapter import _score_verdict
    scored = _score_verdict({'text': 'The Toyota Corolla is a practical car.',
        'visual_requirement': {'strict_identity': True, 'subject_type': 'automobile'}},
        dict(identity_score=90, context_score=95, action_score=90, domain_match=True,
             scene_match=True, **evidence))
    assert not scored['accepted']
    assert scored['rejection_reason'] == 'named_product_identity_unconfirmed'


def test_repair_tutorial_cannot_illustrate_daily_driving():
    from services.youtube_pipeline_v2 import _score_candidate
    candidate = dict(title='Toyota Corolla full body restoration', description='Car bodywork', channel='Car garage')
    assert _score_candidate(candidate, 'Toyota Corolla', 'Daily driving and cabin controls.', 'Toyota Corolla') < 0
    assert candidate['v2_reject_reason'] == 'repair_tutorial_for_road_or_cabin_scene'


@pytest.mark.parametrize('matching_hash', [True, False])
def test_graphic_entity_survives_director_serialization_without_cross_scene_leak(matching_hash):
    import app
    manifest = {'scenes': [{'narration': 'Its compact shape.'}]}
    spec = dict(entity='Toyota Corolla', grounded=True, requirement_hash='a' if matching_hash else 'b')
    app._hydrate_grounded_scene_requirements(manifest, [dict(graphic_fallback=spec,
        visual_requirement=dict(canonical_entity='Toyota Corolla', subject_type='automobile', requirement_hash='a'))])
    scene = manifest['scenes'][0]
    assert scene['visual_requirement']['canonical_entity'] == 'Toyota Corolla'
    assert bool(scene.get('media', {}).get('graphic_fallback')) == matching_hash


def test_title_cased_braking_process_is_not_a_named_car_model():
    from services.visual_requirement import build_visual_requirement
    req = build_visual_requirement(title='How Anti Lock Braking Works',
        script='Anti-lock braking prevents wheel lock. The controller adjusts pressure.',
        scene_text='The braking system prevents a wheel from locking while the vehicle slows down.',
        semantic_context={'subject_type': 'automobile'})
    assert req['subject_type'] == 'concept'
    assert req['strict_identity'] is False
    assert not req['required_attributes'].get('model_or_make')


def test_requested_game_footage_is_not_rejected_for_being_rendered():
    from services.pipeline_v2_media_adapter import _score_verdict
    scored = _score_verdict({'text': 'The game world in Grand Theft Auto VI.',
        'visual_requirement': {'strict_identity': True, 'subject_type': 'video_game', 'domain': 'gaming'}},
        dict(synthetic_content=True, identity_confirmed=True, identity_score=95,
             context_score=95, action_score=90, domain_match=True, scene_match=True))
    assert scored['accepted'] is True
