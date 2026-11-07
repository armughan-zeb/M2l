import hashlib
from pathlib import Path

import pytest

from services.media_resolution_coordinator import _verification_result
from services.youtube_overlay_policy import preserve_source_overlays
from services.media_query_strategy import enrich_scene_queries, query_planning_budget


@pytest.mark.parametrize('mutation', ['none', 'pixels', 'contract', 'unchecked', 'conflict', 'missing'])
def test_verified_asset_survives_provider_outage_only_with_matching_proof(tmp_path, mutation):
    path = tmp_path / 'asset.jpg'
    path.write_bytes(b'previously verified pixels')
    scene = {'text': 'Toyota Corolla', 'visual_requirement': {
        'canonical_entity': 'Toyota Corolla', 'subject_type': 'vehicle',
        'strict_identity': True, 'requirement_hash': 'corolla-exterior'}}
    verdict = {'accepted': True, 'visual_checked': True, 'visual_available': True,
        'semantic_verified': True, 'score': 88,
        'verified_asset_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'verified_requirement_hash': 'corolla-exterior'}
    if mutation == 'pixels':
        path.write_bytes(b'different car')
    elif mutation == 'contract':
        verdict['verified_requirement_hash'] = 'other-car'
    elif mutation == 'unchecked':
        verdict['visual_checked'] = False
    elif mutation == 'conflict':
        verdict['entity_conflict'] = True
    elif mutation == 'missing':
        path.unlink()
    candidate = {'path': str(path), 'source': 'web', 'visual_decision': verdict}
    accepted, reason, _, _ = _verification_result(scene, candidate, None)
    assert accepted is (mutation == 'none')
    if accepted:
        assert reason == 'visual_pass_reused'


def test_small_source_caption_survives_without_blur_but_obstruction_does_not():
    small = preserve_source_overlays({'reject': True, 'has_subtitle': True,
        'overlay_types': ['subtitle'], 'rejection_reason': 'caption_overlay_rejected',
        'regions': [{'type': 'subtitle', 'box': [0.2, 0.88, 0.8, 0.96]}]})
    assert not small['reject']
    assert small['no_blur']
    large = preserve_source_overlays({'reject': True, 'overlay_types': ['full_screen_text'],
        'rejection_reason': 'full_screen_text'})
    assert large['reject']


def test_long_films_receive_multiple_query_planning_waves():
    assert query_planning_budget(12, 4) > query_planning_budget(4, 4)


def test_physical_fallback_is_an_explanation_not_clause_cards():
    from services.editorial_director.director import _build_diagram_props
    text = 'Under the hood, the engine turned fuel into motion.'
    props = _build_diagram_props('step_by_step_process', text, None)
    assert props['variant'] == 'energy_conversion'
    assert [row['title'] for row in props['steps']] == ['fuel', 'engine', 'motion']
    assert all(node['label'] in text for node in props['node_copy_contract']['nodes'])
    assert _build_diagram_props('step_by_step_process', 'A car waits in traffic.', None).get('variant') != 'energy_conversion'


@pytest.mark.parametrize('response', [{'scene_index': 2, 'type': 'SmartImage'},
    [{'scene_index': 2, 'type': 'SmartImage'}]])
def test_single_scene_director_responses_keep_their_global_index(response):
    import json
    from services.editorial_director.director import _decode_director_json
    decoded = _decode_director_json(json.dumps(response))
    assert decoded['scenes'][0]['scene_index'] == 2


def test_youtube_prefers_browser_native_hd_before_expensive_codec_fallback(monkeypatch):
    from services.youtube_pipeline_v2 import _youtube_quality_formats
    monkeypatch.delenv('VIDZEO_YT_FORMAT', raising=False)
    formats = _youtube_quality_formats()
    assert 'vcodec^=avc1' in formats[0]
    assert all('height>=720' in selector for selector in formats)


def test_native_clip_workers_fit_available_memory_without_render_limits():
    from services.youtube_pipeline_v2 import _ranged_download_worker_limit
    assert _ranged_download_worker_limit(6, 700, 8*1024**3) == 1
    assert _ranged_download_worker_limit(6, 1250, 8*1024**3) == 2
    assert _ranged_download_worker_limit(6, 5000, 32*1024**3) == 6


def test_fresh_stock_candidate_without_previous_visual_does_not_crash(tmp_path):
    from services.pipeline_v2_media_adapter import build_qwen_verifier
    from services.visual_intelligence.context import VisualDecisionContext
    fake_img = tmp_path / "stock.jpg"
    fake_img.write_bytes(b"dummy image bytes")
    scene = {
        "scene_id": "scene-stock-1",
        "text": "A modern highway traffic scene.",
        "visual_requirement": {"canonical_entity": "Highway", "requirement_hash": "highway-hash"}
    }
    candidate = {
        "path": str(fake_img),
        "source": "stock",
        "provider": "pexels",
        "metadata_score": 75.0,
        "score": 75.0
    }
    ctx = VisualDecisionContext.from_config({}, 1)
    verifier, _ = build_qwen_verifier({}, [scene], visual_context=ctx)
    # Must not raise AttributeError: 'NoneType' object has no attribute 'get'
    res = verifier(scene, candidate)
    assert isinstance(res, dict)
    assert "accepted" in res

