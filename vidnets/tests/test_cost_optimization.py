"""Cost reductions preserve request identity, validation and billing accuracy."""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from services.job_context import job_scope
from services import structured_response_cache as cache
from services.openrouter_usage import record_usage, record_cache_hit, write_job_receipt
from services.openrouter_llm import _compact_json_prompt
from services.openrouter_llm import _json_schema_error

SCHEMA = {'type': 'object', 'properties': {'answer': {'type': 'string'}},
          'required': ['answer'], 'additionalProperties': False}


class Client:
    provider = 'openrouter'

    def __init__(self, key='test-key-a', model='qwen/test'):
        self.api_key = key
        self.model = self.last_model = model
        self.attempt_history = []
        self.calls = 0
        self.invalid = False
        self.fail = False

    @property
    def model_chain(self):
        return (self.model,)

    @cache.cache_structured_response
    def chat(self, user_prompt, json_mode=True, json_schema=None, temperature=.3,
             timeout_seconds=None, total_timeout_seconds=None):
        self.calls += 1
        time.sleep(.02)
        if self.fail:
            raise ValueError('transient provider failure')
        return '{}' if self.invalid else json.dumps({'answer': user_prompt})


@pytest.fixture(autouse=True)
def clear_memory():
    with cache._GUARD:
        cache._MEMORY.clear()
        cache._MEMORY_BYTES = 0
    yield


def test_six_identical_parallel_requests_pay_once(tmp_path):
    client = Client()
    context = {'llm_cache_dir': str(tmp_path)}
    def work(_):
        with job_scope(context):
            return client.chat('Same plan', json_schema=SCHEMA)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(work, range(6)))
    assert len(set(results)) == 1 and client.calls == 1


def test_waiters_share_failure_without_blocking_a_new_retry(tmp_path):
    from threading import Event
    started, release = Event(), Event()
    class SlowFailure(Client):
        @cache.cache_structured_response
        def chat(self, user_prompt, json_mode=True, json_schema=None):
            self.calls += 1
            started.set()
            assert release.wait(2)
            if self.fail:
                raise ValueError('provider failure')
            return json.dumps({'answer': user_prompt})
    client = SlowFailure()
    client.fail = True
    context = {'llm_cache_dir': str(tmp_path)}
    def work():
        with job_scope(context):
            return client.chat('Same', json_schema=SCHEMA)
    with ThreadPoolExecutor(max_workers=6) as pool:
        owner = pool.submit(work)
        assert started.wait(1)
        waiting = [pool.submit(work) for _ in range(5)]
        time.sleep(.08)
        release.set()
        for future in [owner, *waiting]:
            with pytest.raises(ValueError, match='provider failure'):
                future.result(timeout=2)
    assert client.calls == 1
    client.fail = False
    assert json.loads(work())['answer'] == 'Same' and client.calls == 2


def test_text_scope_includes_account_project_model_schema_prompt_and_controls(tmp_path):
    first = Client()
    other_key = Client(key='test-key-b')
    other_model = Client(model='qwen/other')
    with job_scope({'llm_cache_dir': str(tmp_path/'one')}):
        first.chat('Same', json_schema=SCHEMA)
        first.chat('Same', json_schema=SCHEMA, timeout_seconds=2)  # content unchanged
        first.chat('Changed', json_schema=SCHEMA)
        first.chat('Same', json_schema=SCHEMA, temperature=.8)
        first.chat('Same', json_schema={**SCHEMA, 'properties': {'answer': {'type': 'string', 'maxLength': 80}}})
        other_key.chat('Same', json_schema=SCHEMA)
        other_model.chat('Same', json_schema=SCHEMA)
    assert first.calls == 4 and other_key.calls == other_model.calls == 1
    with job_scope({'llm_cache_dir': str(tmp_path/'two')}):
        first.chat('Same', json_schema=SCHEMA)
    assert first.calls == 5
    serialized = ''.join(path.read_text() for path in tmp_path.rglob('*.json'))
    assert 'test-key-a' not in serialized and 'test-key-b' not in serialized


def test_disk_write_failure_still_reuses_success_in_bounded_memory(tmp_path, monkeypatch):
    client = Client()
    original = Path.mkdir
    def fail_cache_mkdir(path, *args, **kwargs):
        if path == tmp_path:
            raise PermissionError('disk read-only')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', fail_cache_mkdir)
    with job_scope({'llm_cache_dir': str(tmp_path)}):
        assert client.chat('Same', json_schema=SCHEMA) == client.chat('Same', json_schema=SCHEMA)
    assert client.calls == 1


def test_invalid_and_failed_calls_do_not_poison_cache(tmp_path):
    client = Client()
    with job_scope({'llm_cache_dir': str(tmp_path)}):
        client.invalid = True
        client.chat('Same', json_schema=SCHEMA)
        client.invalid = False
        client.fail = True
        with pytest.raises(ValueError):
            client.chat('Same', json_schema=SCHEMA)
        client.fail = False
        client.chat('Same', json_schema=SCHEMA)
        client.chat('Same', json_schema=SCHEMA)
    assert client.calls == 3


def test_expired_disk_entry_is_not_reused(tmp_path):
    client = Client()
    with job_scope({'llm_cache_dir': str(tmp_path)}):
        client.chat('Same', json_schema=SCHEMA)
        entry = next(tmp_path.glob('*.json'))
        payload = json.loads(entry.read_text())
        payload['created_at'] = time.time() - 8*86400
        entry.write_text(json.dumps(payload))
        with cache._GUARD:
            cache._MEMORY.clear()
            cache._MEMORY_BYTES = 0
        client.chat('Same', json_schema=SCHEMA)
    assert client.calls == 2


def test_memory_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(cache, '_MAX_MEMORY_BYTES', 100)
    for index in range(12):
        cache._memory_put(str(index), {'content': 'x'*30})
    assert cache._MEMORY_BYTES <= 100 and len(cache._MEMORY) == 3


def test_receipt_distinguishes_paid_tokens_provider_cache_and_local_reuse(tmp_path):
    context = {'job_id': 'video', 'attempt_id': 'attempt',
               'usage_path': str(tmp_path/'openrouter-usage.jsonl'), 'cost_profile': 'efficient'}
    with job_scope(context):
        record_usage({'id': 'cost-test-text', 'usage': {'prompt_tokens': 1000, 'completion_tokens': 100,
            'cost': .001, 'prompt_tokens_details': {'cached_tokens': 800, 'cache_write_tokens': 200},
            'completion_tokens_details': {'reasoning_tokens': 0}}}, kind='text', operation='director')
        record_usage({'id': 'cost-test-vision', 'usage': {'prompt_tokens': 300, 'completion_tokens': 40,
            'cost': .0001}}, kind='vision', operation='visual_review')
        record_cache_hit('qwen/test', 'director')
        record_cache_hit('qwen/test', 'visual_review', kind='vision')
    write_job_receipt(context, 'completed')
    receipt = json.loads((tmp_path/'cost-attempt.json').read_text())
    assert receipt['input_tokens'] == 1300 and receipt['output_tokens'] == 140
    assert receipt['provider_cached_input_tokens'] == 800
    assert receipt['provider_cache_write_tokens'] == 200
    assert receipt['captured_generation_count'] == 2 and receipt['validated_response_cache_hits'] == 2
    assert receipt['local_response_cache_hits_by_kind'] == {'text': 1, 'vision': 1}
    assert receipt['provider_reported_usd'] == pytest.approx(.0011)
    assert receipt['operations']['director']['request_count'] == 1


def test_json_formatting_is_compacted_without_changing_script_or_choices():
    value = {'script': 'Captain Edward Smith.\n26 seconds.', 'catalog': ['map', 'timeline', 'diagram'],
             'constraints': {'captions': False, 'graphics': .3}, 'anchors': ['London', 'Paris']}
    pretty = json.dumps(value, indent=2)
    compact = _compact_json_prompt(pretty)
    assert json.loads(compact) == value and len(compact) < len(pretty)
    assert json.loads(compact)['script'] == value['script']


@pytest.mark.parametrize('text', ['Narrate this exactly: {x}', '{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}', '"literal text"'])
def test_ambiguous_or_non_object_prompts_remain_exact(text):
    assert _compact_json_prompt(text) == text


@pytest.mark.parametrize('number', [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_numbers_cannot_validate_as_cached_structured_answers(number):
    assert _json_schema_error(number, {'type': 'number'})
