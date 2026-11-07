import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from services.generation_request import normalize_request
from services.job_context import JobThreadPoolExecutor, job_scope
from services.remotion_render_gate import run_with_remotion_render_slot
from services.recovery_contract import recover_manifest
from services.bounded_http import provider_post
from services.openrouter_usage import write_job_receipt


def test_nested_request_flat_values_win_without_mutating_input():
    payload = {'config': {'graphic_density': .1, 'delivery_budget_seconds': 120}, 'graphic_density': .3}
    result = normalize_request(payload)
    assert result['graphic_density'] == .3
    assert result['delivery_budget_seconds'] == 120
    assert 'request_schema_version' not in payload


def test_public_generation_api_cannot_enable_local_retrieval_only_mode():
    with pytest.raises(ValueError, match='local CMS diagnostic bridge'):
        normalize_request({'media_retrieval_only': True})


@pytest.mark.parametrize('payload', [None, [], {'config': []}, {'_delivery_budget': 1},
                                    {'delivery_budget_seconds': float('nan')}, {'script': 4}])
def test_invalid_public_request_is_rejected(payload):
    with pytest.raises(ValueError):
        normalize_request(payload)


def test_render_wait_is_bounded_and_does_not_enter_render():
    gate = threading.Semaphore(0)
    entered = []
    began = time.monotonic()
    with pytest.raises(TimeoutError) as failure:
        run_with_remotion_render_slot(lambda: entered.append(True), gate=gate, timeout_seconds=.02)
    assert failure.value.retryable is True
    assert failure.value.stage == 'remotion_render'
    assert time.monotonic() - began < 1
    assert entered == []


def test_completed_job_rejects_late_worker_result():
    finished = threading.Event()
    entered = threading.Event()
    release = threading.Event()
    def work():
        entered.set()
        release.wait(.5)
        return 'must not be delivered'
    with job_scope({'finished_event': finished}):
        pool = JobThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(work)
            assert entered.wait(.5)
            finished.set()
            release.set()
            with pytest.raises(InterruptedError):
                future.result(timeout=1)
        finally:
            release.set()
            pool.shutdown(wait=True)


def test_late_provider_charge_is_recorded_without_changing_failed_status(tmp_path, monkeypatch):
    monkeypatch.delenv('VIDZEO_OPENROUTER_USAGE_LOG', raising=False)
    finished = threading.Event()
    job = {'job_id': 'offline-regression', 'attempt_id': 'attempt-one',
           'usage_path': str(tmp_path / 'usage.jsonl'), 'finished_event': finished}
    called = threading.Event()
    def slow_post(*_args, **_kwargs):
        called.set()
        time.sleep(.08)
        return SimpleNamespace(status_code=200, json=lambda: {'id': 'gen-offline-hardening', 'usage': {'cost': .004}})
    with job_scope(job):
        with pytest.raises(requests.Timeout):
            provider_post(slow_post, 'https://invalid.test', headers={}, json={'model': 'offline'}, timeout=.025)
        assert called.is_set()
        finished.set()
        write_job_receipt(job, 'failed')
    deadline = time.monotonic() + 1
    receipt_path = tmp_path / 'cost-attempt-one.json'
    while time.monotonic() < deadline:
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        if receipt['late_response_count']:
            break
        time.sleep(.01)
    assert receipt['status'] == 'failed'
    assert receipt['late_response_count'] == 1
    assert receipt['provider_reported_usd'] == .004


def test_supported_recovery_is_a_stat_without_verified_footage_claim():
    manifest = {'scenes': [{'narration': 'There were 800 suspects.', 'media': {'src': 'graphic_fallback_scene.png'},
                            'primary_visual': {'type': 'SmartImage'}}]}
    recover_manifest(manifest)
    scene = manifest['scenes'][0]
    assert scene['primary_visual']['props']['value'] == 800
    assert scene['media']['verified'] is False
    assert scene['recovery']['claims_source_footage'] is False


def test_real_media_empty_fallback_metadata_is_preserved():
    scene = {'narration': 'The plane departed.', 'media': {'src': 'real.jpg', 'graphic_fallback': {}}}
    recover_manifest({'scenes': [scene]})
    assert 'recovery' not in scene


def test_paid_mode_does_not_grant_localhost_access(monkeypatch):
    import app
    monkeypatch.setenv('VIDZEO_AUTH_MODE', 'supabase')
    monkeypatch.setenv('VIDZEO_PACKAGED', '1')
    response = app.app.test_client().get('/api/queue/status')
    assert response.status_code == 401


def test_queue_rejects_non_object_config_patch_before_manager(monkeypatch):
    import app
    monkeypatch.setenv('VIDZEO_AUTH_MODE', 'local')
    monkeypatch.setenv('VIDZEO_PACKAGED', '0')
    response = app.app.test_client().post('/api/queue/update', json={'id': 'anything', 'updates': {'config': []}})
    assert response.status_code == 400


@pytest.mark.parametrize('script, expected', [
    ('After eight p.m., Cooper jumped. His fate is unresolved.', 2),
    ('At nine a.m. the crew arrived. It left again.', 2),
    ('They arrived at eight p.m. The crew stayed.', 2),
    ('At eight P.M., the crew stayed. Then it left.', 2),
    ('They watched a film. the crew stayed.', 2),
])
def test_time_abbreviations_keep_exact_sentence_spans(script, expected):
    from services.storyboard_v2.script_parser import _sentence_spans
    from services.storyboard_v2.text import mask_sentence_fact_punctuation
    spans = _sentence_spans(script)
    assert len(spans) == expected
    assert ''.join(script[a:b] for a, b in spans).strip() == script.strip()
    assert len(mask_sentence_fact_punctuation(script)) == len(script)


def test_incidental_story_entities_do_not_force_listicle_override():
    import inspect
    import app
    source = inspect.getsource(app._resolve_llm_topic_anchor)
    assert '_LISTICLE_HINT_RE.search' in source


@pytest.mark.parametrize('value, default, expected', [
    (None, False, False), (None, True, True), (False, True, False),
    ('false', True, False), ('true', False, True), (0, True, False),
])
def test_render_validation_boolean_helper(value, default, expected):
    import app
    assert app._enabled(value, default) is expected


@pytest.mark.parametrize('existing', [True, False])
def test_recovered_stats_replace_stale_media_only_frame_contract(existing):
    scene = {'narration': 'In Seattle, 36 passengers walked free.',
             'media': {'src': '/graphic_fallback_1.png', 'source': 'graphic'},
             'text': {'frame_mode': 'media_only', 'display_text': ''},
             'primary_visual': {'type': 'StatCard' if existing else 'SmartImage',
                                'props': {'value': 36, 'source_span': '36 passengers'}}}
    recover_manifest({'scenes': [scene]})
    assert scene['text']['frame_mode'] == 'data'
    assert scene['media_composition']['hero_layer'] == 'graphic'
    assert scene['design']['graphics_enabled'] is True
    assert scene['media']['verified'] is False
    assert scene['text']['display_text'] == ''


def test_scene_reveal_uses_defined_local_frame_clock():
    source = (Path(__file__).resolve().parents[1] / 'src/remotion/DocumentaryComposition.jsx').read_text(encoding='utf-8')
    scene = source.split('function Scene({', 1)[1].split('export function DocumentaryComposition', 1)[0]
    assert 'const filmFrame = useCurrentFrame();' in scene
    assert 'const frame = filmFrame - from;' in scene
    assert scene.index('const frame = filmFrame - from;') < scene.index('const beforeDataReveal')

