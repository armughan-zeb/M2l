import json
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import app as server_app
from services.generation_cost_policy import DEFAULT_MODEL, apply_generation_cost_policy
from services.generation_registry import GenerationRegistry
from services.job_context import job_scope
from services.openrouter_llm import OpenRouterLLM
from services.structured_response_cache import cache_structured_response
from services.visual_intelligence.vision_client import QwenVisionClient
_REAL_QUEUE_CALLBACK = server_app._queue_generation_callback


def test_qwen_prompt_includes_required_structured_contract(monkeypatch):
    from services import openrouter_llm
    captured = []
    schema = {'type': 'object', 'properties': {'title': {'type': 'string'}},
              'required': ['title'], 'additionalProperties': False}
    def post(_url, **kwargs):
        captured.append(kwargs['json'])
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {
            'model': DEFAULT_MODEL, 'choices': [{'message': {'content': '{"title":"NFL"}'}, 'finish_reason': 'stop'}]})
    monkeypatch.setattr(openrouter_llm.requests, 'post', post)
    result = OpenRouterLLM('test-key').chat('Return title.', json_mode=True,
        json_schema=schema, max_attempts=1)
    assert json.loads(result)['title'] == 'NFL'
    prompt = captured[0]['messages'][-1]['content']
    assert 'Required response schema' in prompt and '"title":{"type":"string"}' in prompt


def test_research_native_search_is_killed_when_it_stalls(monkeypatch):
    import subprocess
    import time
    from services import research_ledger
    processes = []
    class StalledProcess:
        def __init__(self, *_args, **_kwargs):
            self.killed = False
            processes.append(self)
        def communicate(self, *_args, **_kwargs):
            if not self.killed:
                raise subprocess.TimeoutExpired('isolated-search', .1)
            return ('[]', '')
        def poll(self):
            return 1 if self.killed else None
        def kill(self):
            self.killed = True
    monkeypatch.setattr(research_ledger.subprocess, 'Popen', StalledProcess)
    rows, result = research_ledger._discover_sources(['The field is one hundred yards.'], 'NFL', time.monotonic()+1)
    assert not rows and result['status'] == 'unresolved'
    assert processes and all(process.killed for process in processes)


def test_verbose_direction_notes_do_not_discard_plan_or_modify_copy(monkeypatch):
    from services import openrouter_llm
    schema = {'type': 'object', 'properties': {'visual_motif': {'type': 'string', 'maxLength': 12}},
              'required': ['visual_motif'], 'additionalProperties': False}
    def post(_url, **_kwargs):
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {
            'model': DEFAULT_MODEL, 'choices': [{'message': {'content': '{"visual_motif":"A short visual directing description"}'}, 'finish_reason': 'stop'}]})
    monkeypatch.setattr(openrouter_llm.requests, 'post', post)
    client = OpenRouterLLM('test-key')
    result = client.chat('Plan.', json_mode=True, json_schema=schema,
        json_schema_name='vidnets_art_direction_v1', max_attempts=1)
    assert len(json.loads(result)['visual_motif']) <= 12
    assert client.attempt_history[-1]['shortened_direction_notes'] == ['visual_motif']
    with pytest.raises(ValueError, match='text is too long'):
        client.chat('Exact copy.', json_mode=True, json_schema=schema,
            json_schema_name='approved_copy', max_attempts=1)


@pytest.fixture
def api_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(server_app, 'TEMP_DIR', tmp_path / 'temp')
    monkeypatch.setattr(server_app, '_get_authenticated_state',
                        lambda: ('test-token', {'id': 'account-1', 'access_state': 'active'}))
    monkeypatch.setattr(server_app, '_attach_plan_constraints', lambda config, user: dict(config))
    monkeypatch.setattr(server_app, '_apply_env_api_key_defaults', lambda config: dict(config))
    monkeypatch.setattr(server_app, '_enforce_generation_plan_limits', lambda config, user=None: config)
    monkeypatch.setattr(server_app, '_validate_media_request_config', lambda config: None)
    # This suite tests API orchestration; account-wide admission is exercised
    # separately against the entitlement contract, not the live billing service.
    monkeypatch.setattr('services.export_entitlements.reserve_generation_export', lambda *_a, **_k: {'allowed': True})
    def prepare(config, name):
        package = Path(config.get('output_package_dir') or tmp_path / 'project')
        package.mkdir(parents=True, exist_ok=True)
        config.update(_output_package_dir=str(package), output_package_dir=str(package))
        return package
    monkeypatch.setattr(server_app, '_prepare_video_output_package', prepare)
    return server_app.app.test_client()


def test_one_call_returns_video_and_repeated_request_is_free(api_boundary, monkeypatch, tmp_path):
    calls = []
    video = tmp_path / 'finished.mp4'
    video.write_bytes(b'fixture-video-bytes')
    def generate(config):
        calls.append(config)
        assert config['scene_brain_model'] == config['director_model'] == DEFAULT_MODEL
        return {'success': True, 'video_path': str(video), 'video_url': '/output/finished.mp4', 'job_id': 'job-1'}
    monkeypatch.setattr(server_app, 'generate_video', generate)
    body = {'script': 'The field measures one hundred yards.', 'request_id': 'unique-video-1'}
    assert api_boundary.post('/api/generate', json=body).get_json()['success']
    replay = api_boundary.post('/api/generate', json=body)
    assert replay.get_json()['idempotency_replayed']
    assert len(calls) == 1
    download = api_boundary.post('/api/generate', json={**body, 'response_mode': 'video'})
    assert download.mimetype == 'video/mp4'
    assert download.data == video.read_bytes()
    assert download.headers['X-Idempotency-Replayed'] == 'true'
    assert len(calls) == 1
    download.close()
    changed = api_boundary.post('/api/generate', json={**body, 'script': 'A different story.'})
    assert changed.status_code == 409
    video.unlink()
    assert api_boundary.post('/api/generate', json=body).status_code == 410
    assert len(calls) == 1


def test_queue_mode_starts_worker_in_the_same_call(api_boundary, monkeypatch):
    items = {}
    started = []
    def add(config):
        item = {'id': 'queued-1', 'status': 'queued', 'config': config}
        items[item['id']] = item
        return item
    manager = SimpleNamespace(add_item=add, worker_running=True,
        start_worker=lambda callback: started.append(callback) or True,
        get_item_snapshot=lambda identity: items.get(identity))
    monkeypatch.setattr(server_app, 'get_queue_manager', lambda: manager)
    body = {'script': 'A complete narration script.', 'execution_mode': 'queue', 'request_id': 'queue-1'}
    response = api_boundary.post('/api/generate', json=body)
    assert response.status_code == 202
    assert response.get_json()['item']['id'] == 'queued-1'
    assert len(started) == 1
    assert api_boundary.post('/api/generate', json=body).get_json()['idempotency_replayed']
    assert len(started) == 1
    assert items['queued-1']['config']['cost_profile'] == 'efficient'
    assert items['queued-1']['config']['output_package_dir']


def test_registry_is_account_scoped_and_resumes_failed_work(tmp_path):
    registry = GenerationRegistry(tmp_path / 'requests')
    body = {'script': 'One script.', 'openrouter_api_key': 'never-store-this'}
    first = registry.reserve('a', 'same-key', body)
    assert first['action'] == 'run'
    assert registry.reserve('a', 'same-key', body)['action'] == 'busy'
    assert registry.reserve('b', 'same-key', body)['action'] == 'run'
    registry.set_package(first, tmp_path / 'saved-project')
    registry.finish(first, {'error': 'temporary provider error', 'retryable': True}, 503)
    resumed = registry.reserve('a', 'same-key', body)
    assert resumed['output_package_dir'].endswith('saved-project')
    assert resumed['action'] == 'run'
    assert 'never-store-this' not in Path(first['path']).read_text()


def test_all_manual_profiles_keep_the_qwen_only_model_policy():
    expensive = {'llm_model': 'premium/model', 'director_model': 'premium/director'}
    with pytest.raises(ValueError, match='paid models are disabled'):
        apply_generation_cost_policy(expensive)
    efficient = apply_generation_cost_policy({'director_model': DEFAULT_MODEL})
    assert efficient['director_model'] == DEFAULT_MODEL
    with job_scope({'cost_profile': 'efficient', 'llm_provider': 'openrouter', 'llm_model': DEFAULT_MODEL}):
        text = OpenRouterLLM('test-key', model='premium/model', fallback_models=['premium/second'])
        vision = QwenVisionClient('test-key', model='premium/vision', fallback_models=['premium/second'])
        assert text.model_chain == (DEFAULT_MODEL,)
        assert vision.model == DEFAULT_MODEL and vision.fallback_models == []
    custom = apply_generation_cost_policy({'director_model': DEFAULT_MODEL, 'cost_profile': 'custom'})
    assert custom['director_model'] == DEFAULT_MODEL
    assert custom['scene_brain_model'] == DEFAULT_MODEL


def test_only_valid_structured_responses_are_cached_per_project(tmp_path):
    calls = []
    schema = {'type': 'object', 'properties': {'answer': {'type': 'string'}}, 'required': ['answer']}
    class Client:
        model_chain = (DEFAULT_MODEL,)
        model = last_model = DEFAULT_MODEL
        attempt_history = []
        @cache_structured_response
        def chat(self, user_prompt, json_mode=True, json_schema=None):
            calls.append(user_prompt)
            return json.dumps({'answer': user_prompt})
    client = Client()
    with job_scope({'llm_cache_dir': str(tmp_path / 'one')}):
        assert client.chat('same', json_schema=schema) == client.chat('same', json_schema=schema)
        client.chat('changed', json_schema=schema)
    assert calls == ['same', 'changed']
    with job_scope({'llm_cache_dir': str(tmp_path / 'two')}):
        client.chat('same', json_schema=schema)
    assert len(calls) == 3


def test_queue_callback_delegates_to_canonical_generator(monkeypatch):
    calls = []
    monkeypatch.setattr(server_app, 'generate_video', lambda *args: calls.append(args) or {'success': True})
    assert _REAL_QUEUE_CALLBACK({'script': 'Narration.'}, lambda *args: None, lambda: False)['success']
    assert len(calls) == 1


def test_outbound_cost_guard_keeps_tts_choice_and_preserves_text_model():
    from services.bounded_http import provider_post
    captured = []
    def post(url, **kwargs):
        captured.append(kwargs['json'])
        return SimpleNamespace(status_code=200, json=lambda: {})
    with job_scope({'cost_profile': 'efficient'}):
        provider_post(post, 'https://openrouter.ai/api/v1/chat/completions', headers={},
            json={'model': DEFAULT_MODEL, 'models': ['premium/second']}, timeout=2)
        provider_post(post, 'https://openrouter.ai/api/v1/chat/completions', headers={},
            json={'model': 'selected/tts'}, timeout=2, usage_kind='tts')
    assert captured[0]['model'] == DEFAULT_MODEL and 'models' not in captured[0]
    assert captured[1]['model'] == 'selected/tts'


def test_invalid_response_is_never_cached_and_same_call_is_single_flight(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import time
    schema = {'type': 'object', 'properties': {'answer': {'type': 'string'}}, 'required': ['answer']}
    class Client:
        model_chain = (DEFAULT_MODEL,)
        model = last_model = DEFAULT_MODEL
        def __init__(self):
            self.calls = 0
            self.valid = False
            self.attempt_history = []
        @cache_structured_response
        def chat(self, user_prompt, json_mode=True, json_schema=None):
            self.calls += 1
            time.sleep(.02)
            return '{"answer":"yes"}' if self.valid else '{}'
    client = Client()
    context = {'llm_cache_dir': str(tmp_path / 'cache')}
    with job_scope(context):
        client.chat('same', json_schema=schema)
        client.chat('same', json_schema=schema)
    assert client.calls == 2
    client.valid = True
    def work():
        with job_scope(context):
            return client.chat('same', json_schema=schema)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: work(), range(2))) == ['{"answer":"yes"}'] * 2
    assert client.calls == 3
