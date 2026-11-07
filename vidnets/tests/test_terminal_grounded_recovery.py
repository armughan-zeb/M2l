from pathlib import Path

import app


def _disable_network(monkeypatch):
    for name in ('_fill_unresolved_with_rotating_video_pool', '_fill_unresolved_with_general_web_fallback'):
        monkeypatch.setattr(app, name, lambda *args, **kwargs: 0)
    monkeypatch.setattr(app, '_run_stock_media_branch', lambda *args, **kwargs: {'stock_paths': []})
    monkeypatch.setattr(app, '_media_path_exists_for_render', lambda path: bool(path and Path(path).is_file()))


def test_generic_scene_gets_grounded_graphic_without_emergency_plates(tmp_path, monkeypatch):
    _disable_network(monkeypatch)
    plate = tmp_path / 'graphic_fallback_1.png'
    plate.write_bytes(b'fixture')
    calls = []
    def create(*args, **kwargs):
        calls.append(kwargs)
        return str(plate)
    monkeypatch.setattr(app, '_create_emergency_render_asset', create)
    plan, remaining = app._fill_strict_visual_delivery_fallback(
        {'media_paths': [None], 'metadata': {}},
        {'scene_texts': ['Light takes eight minutes to reach Earth.'], 'scene_contracts': [{'visual_requirement': {}}]}, [0], (640, 360), 'Sunlight',
        {'delivery_first_media': True, 'allow_emergency_media': False,
         'allow_grounded_graphic_fallback': True, 'cross_source_fallback': False},
    )
    assert remaining == []
    assert calls[0]['graphic_fallback'] is True
    row = plan['metadata']['media_resolution_v2'][0]
    assert row['graphic_fallback']['text'] == 'Light takes eight minutes to reach Earth.'
    assert row['requirement_hash']
    assert row['provider'] == 'graphic' and row['verified'] is False
    assert row['candidate_evidence']['real_media_exhausted'] is True


def test_explicit_real_media_only_policy_is_preserved(monkeypatch):
    _disable_network(monkeypatch)
    monkeypatch.setattr(app, '_create_emergency_render_asset', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('graphic not permitted')))
    _, remaining = app._fill_strict_visual_delivery_fallback(
        {'media_paths': [None], 'metadata': {}}, {'scene_texts': ['Only genuine footage.']},
        [0], (640, 360), 'Topic', {'delivery_first_media': True, 'allow_emergency_media': False,
        'allow_grounded_graphic_fallback': False, 'cross_source_fallback': False},
    )
    assert remaining == [0]


def test_missing_narration_does_not_cycle_another_scenes_facts(tmp_path, monkeypatch):
    _disable_network(monkeypatch)
    plate = tmp_path / 'graphic.png'
    plate.write_bytes(b'fixture')
    calls = []
    def create(*args, **kwargs):
        calls.append(kwargs)
        return str(plate)
    monkeypatch.setattr(app, '_create_emergency_render_asset', create)
    app._fill_strict_visual_delivery_fallback(
        {'media_paths': [str(plate), None], 'metadata': {}},
        {'scene_texts': ['This belongs only to scene one.'], 'scene_contracts': [{}, {'text': 'The second scene has its own narration.'}]},
        [1], (640, 360), 'Topic', {'delivery_first_media': True, 'allow_emergency_media': False,
        'allow_grounded_graphic_fallback': True, 'cross_source_fallback': False},
    )
    assert calls[0]['scene_text'] == 'The second scene has its own narration.'
