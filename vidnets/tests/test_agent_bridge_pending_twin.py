"""Pending-twin dedupe in agent_post (Nov 15 2026 speedup, item d).

Before minting a fresh request id, agent_post scans for a PENDING twin for
the same job_id + operation + payload fingerprint and, if one exists and is
unexpired, waits on it instead of creating a duplicate. The answered-twin
reuse (reuse_cached_answer) is untouched: a twin answered while this caller
waits is still picked up through the cache.
"""
import json
import threading
import time

import pytest

from services import agent_bridge
from services.job_context import job_scope


def _payload(**overrides):
    payload = {
        'job_id': 'job-1',
        'operation': 'vidzeo_scene_queries_v3',
        'kind': 'text',
        'messages': [{'role': 'user', 'content': 'plan scenes 1-8'}],
        'response_format': {'type': 'json_object'},
    }
    payload.update(overrides)
    return payload


def _post_kwargs(payload):
    return {
        'messages': payload['messages'],
        'response_format': payload['response_format'],
    }


def _post_in_thread(results, key, payload, timeout=120):
    def _run():
        with job_scope({'agent_owner_id': 'owner', 'job_id': 'job-1'}):
            try:
                results[key] = agent_bridge.agent_post(
                    _post_kwargs(payload), timeout=timeout,
                    operation=payload['operation'], kind=payload['kind'])
            except Exception as exc:  # noqa: BLE001 - record the failure
                results[key] = exc

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def _request_dirs(root):
    return [d for d in root.iterdir() if d.is_dir()]


def _content(result):
    assert not isinstance(result, Exception), result
    return json.loads(result.json()['choices'][0]['message']['content'])


def test_resume_files_the_same_payload_twice_yields_one_pending_request(tmp_path, monkeypatch):
    # The resume case: the first filing is still pending when the pipeline
    # refiles the identical payload. Both waiters must get the one answer,
    # and only one request directory may exist.
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    monkeypatch.setenv('VIDZEO_AGENT_MIN_WALL_SECONDS', '0')
    payload = _payload()
    results = {}
    first = _post_in_thread(results, 'first', payload)
    time.sleep(0.5)
    assert len(_request_dirs(tmp_path)) == 1
    second = _post_in_thread(results, 'second', payload)
    time.sleep(0.5)
    assert len(_request_dirs(tmp_path)) == 1, 'refile minted a duplicate request'
    request_id = _request_dirs(tmp_path)[0].name
    agent_bridge.complete_request(request_id, 'owner', '{"scenes": [1, 2]}')
    first.join(timeout=10)
    second.join(timeout=10)
    assert not first.is_alive() and not second.is_alive()
    assert _content(results['first']) == {'scenes': [1, 2]}
    assert _content(results['second']) == {'scenes': [1, 2]}


def test_different_payload_mints_a_second_request(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    monkeypatch.setenv('VIDZEO_AGENT_MIN_WALL_SECONDS', '0')
    results = {}
    first = _post_in_thread(results, 'first', _payload())
    time.sleep(0.5)
    second = _post_in_thread(results, 'second', _payload(
        messages=[{'role': 'user', 'content': 'plan scenes 9-16'}]))
    time.sleep(0.5)
    assert len(_request_dirs(tmp_path)) == 2
    for directory in _request_dirs(tmp_path):
        agent_bridge.complete_request(
            directory.name, 'owner',
            '{"scenes": ["%s"]}' % directory.name[:4])
    first.join(timeout=10)
    second.join(timeout=10)
    assert 'response' in str(type(results['first'])) or not isinstance(results['first'], Exception)
    assert not isinstance(results['first'], Exception), results['first']
    assert not isinstance(results['second'], Exception), results['second']


def test_expired_twin_is_not_joined(tmp_path, monkeypatch):
    # A twin whose expiry is past must not be waited on: the refile mints
    # a fresh request with a live wall.
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    monkeypatch.setenv('VIDZEO_AGENT_MIN_WALL_SECONDS', '0')
    payload = _payload()
    now = time.time()
    directory = tmp_path / ('a' * 32)
    directory.mkdir()
    (directory / 'request.json').write_text(json.dumps({
        'id': 'a' * 32, 'owner_id': 'owner', 'job_id': 'job-1',
        'operation': payload['operation'], 'kind': payload['kind'],
        'state': 'pending', 'created_at': now - 120, 'expires_at': now - 60,
        'messages': payload['messages'],
        'response_format': payload['response_format'],
    }), encoding='utf-8')
    assert agent_bridge.find_pending_twin(
        owner='owner', job_id='job-1', operation=payload['operation'],
        kind=payload['kind'], messages=payload['messages'],
        response_format=payload['response_format']) is None
    results = {}
    thread = _post_in_thread(results, 'only', payload, timeout=120)
    time.sleep(0.5)
    dirs = _request_dirs(tmp_path)
    assert len(dirs) == 2, 'expired twin was joined instead of minting fresh'
    fresh = next(d for d in dirs if d.name != 'a' * 32)
    agent_bridge.complete_request(fresh.name, 'owner', '{"scenes": [9]}')
    thread.join(timeout=10)
    assert _content(results['only']) == {'scenes': [9]}


def test_twin_expiry_falls_through_to_a_fresh_request(tmp_path, monkeypatch):
    # The joiner waits on the twin only until the twin's own expiry; if it
    # expires unanswered while the joiner's wall remains, a fresh request
    # is filed and its answer is returned.
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    monkeypatch.setenv('VIDZEO_AGENT_MIN_WALL_SECONDS', '0')
    monkeypatch.setattr(agent_bridge, '_ACTIVITY_RECORD_SECONDS', 0.05)
    payload = _payload()
    results = {}
    first = _post_in_thread(results, 'first', payload, timeout=2)
    time.sleep(0.5)
    # Joiner gets a longer wall than the twin's remaining ~1.5s.
    second = _post_in_thread(results, 'second', payload, timeout=120)
    time.sleep(0.3)
    assert len(_request_dirs(tmp_path)) == 1, 'joiner minted before the twin expired'
    first.join(timeout=10)
    assert isinstance(results['first'], agent_bridge.AgentBridgeError), results['first']
    # The joiner must have filed fresh after the twin expired.
    deadline = time.monotonic() + 5
    fresh = None
    while time.monotonic() < deadline:
        pending = []
        for d in _request_dirs(tmp_path):
            request_file = d / 'request.json'
            if not request_file.is_file():
                continue  # dir created before request.json lands; skip, don't crash
            try:
                state = json.loads(request_file.read_text(encoding='utf-8'))['state']
            except (ValueError, KeyError):
                continue
            if state == 'pending':
                pending.append(d)
        if pending:
            fresh = pending[0]
            break
        time.sleep(0.05)
    assert fresh is not None, 'joiner did not file a fresh request after twin expiry'
    agent_bridge.complete_request(fresh.name, 'owner', '{"scenes": [3]}')
    second.join(timeout=10)
    assert _content(results['second']) == {'scenes': [3]}


def test_answered_twin_reuse_still_works(tmp_path, monkeypatch):
    # Dedupe must not break the existing answered-twin path: a request
    # whose payload was already answered returns at once from the cache.
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    monkeypatch.setenv('VIDZEO_AGENT_MIN_WALL_SECONDS', '0')
    payload = _payload()
    first = 'a' * 32
    directory = tmp_path / first
    directory.mkdir()
    (directory / 'request.json').write_text(json.dumps({
        'id': first, 'owner_id': 'owner', 'job_id': 'job-1',
        'operation': payload['operation'], 'kind': payload['kind'],
        'state': 'pending', 'created_at': time.time(), 'expires_at': time.time() + 120,
        'messages': payload['messages'],
        'response_format': payload['response_format'],
    }), encoding='utf-8')
    agent_bridge.complete_request(first, 'owner', '{"scenes": [1]}')
    start = time.monotonic()
    with job_scope({'agent_owner_id': 'owner', 'job_id': 'job-1'}):
        response = agent_bridge.agent_post(
            _post_kwargs(payload), timeout=2,
            operation=payload['operation'], kind=payload['kind'])
    assert time.monotonic() - start < 5
    assert json.loads(response.json()['choices'][0]['message']['content']) == {'scenes': [1]}


# D1 (fewer-requests): a vision payload is filed with inline data-URI images
# but stored with local_image parts, so the raw fingerprint never matched a
# stored twin and every vision re-file minted a duplicate. The fingerprint
# now normalizes image parts to content hashes.
_ONE_PIXEL_PNG_B64 = (
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
)


def _vision_payload(**overrides):
    payload = {
        'job_id': 'job-1',
        'operation': 'visual_review',
        'kind': 'vision',
        'messages': [{'role': 'user', 'content': [
            {'type': 'text', 'text': 'Is this frame accurate for the scene?'},
            {'type': 'image_url', 'image_url': {
                'url': 'data:image/png;base64,' + _ONE_PIXEL_PNG_B64, 'detail': 'auto'}}]}],
        'response_format': {'type': 'json_object'},
    }
    payload.update(overrides)
    return payload


def test_vision_filed_and_stored_forms_share_a_fingerprint(tmp_path):
    # The filed form (inline data-URI) and the stored form (local_image
    # file) of the same vision payload must fingerprint identically, or
    # twin dedupe and the answer cache can never fire for vision.
    filed = _vision_payload()
    stored_dir = tmp_path / 'stored'
    stored_dir.mkdir()
    stored_messages = agent_bridge._messages_with_local_images(
        filed['messages'], stored_dir)
    stored = dict(filed, messages=stored_messages)
    assert agent_bridge.payload_fingerprint(filed) == agent_bridge.payload_fingerprint(stored)
    other = _vision_payload(messages=[{'role': 'user', 'content': [
        {'type': 'text', 'text': 'Is this frame accurate for the scene?'},
        {'type': 'image_url', 'image_url': {
            'url': 'data:image/png;base64,' + 'AAAA', 'detail': 'auto'}}]}])
    assert agent_bridge.payload_fingerprint(filed) != agent_bridge.payload_fingerprint(other)


def test_vision_refile_joins_the_pending_twin(tmp_path, monkeypatch):
    # A vision re-file must wait on the pending twin instead of minting a
    # duplicate the operator has to answer twice.
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    monkeypatch.setenv('VIDZEO_AGENT_MIN_WALL_SECONDS', '0')
    payload = _vision_payload()
    results = {}
    first = _post_in_thread(results, 'first', payload)
    time.sleep(0.5)
    assert len(_request_dirs(tmp_path)) == 1
    second = _post_in_thread(results, 'second', payload)
    time.sleep(0.5)
    assert len(_request_dirs(tmp_path)) == 1, 'vision refile minted a duplicate request'
    request_id = _request_dirs(tmp_path)[0].name
    agent_bridge.complete_request(request_id, 'owner', '{"verdict": "pass"}')
    first.join(timeout=10)
    second.join(timeout=10)
    assert not first.is_alive() and not second.is_alive()
    assert _content(results['first']) == {'verdict': 'pass'}
    assert _content(results['second']) == {'verdict': 'pass'}


def test_expired_twin_fanout_mints_exactly_one_fresh_request(tmp_path, monkeypatch):
    # Two waiters queued on one twin that expires unanswered must not each
    # mint a fresh request: the twin scan and the mint are one atomic step
    # under _mint_lock, so the second waiter finds the first waiter's fresh
    # twin and joins it.
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    monkeypatch.setenv('VIDZEO_AGENT_MIN_WALL_SECONDS', '0')
    payload = _payload()
    results = {}
    first = _post_in_thread(results, 'first', payload, timeout=2)
    time.sleep(0.5)
    second = _post_in_thread(results, 'second', payload, timeout=120)
    third = _post_in_thread(results, 'third', payload, timeout=120)
    time.sleep(0.3)
    assert len(_request_dirs(tmp_path)) == 1, 'joiner minted before the twin expired'
    first.join(timeout=10)
    assert isinstance(results['first'], agent_bridge.AgentBridgeError), results['first']
    deadline = time.monotonic() + 5
    fresh = None
    while time.monotonic() < deadline:
        pending = []
        for d in _request_dirs(tmp_path):
            request_file = d / 'request.json'
            if not request_file.is_file():
                continue
            try:
                state = json.loads(request_file.read_text(encoding='utf-8'))['state']
            except (ValueError, KeyError):
                continue
            if state == 'pending':
                pending.append(d)
        if pending:
            fresh = pending[0]
            break
        time.sleep(0.05)
    assert fresh is not None, 'joiners did not file a fresh request after twin expiry'
    time.sleep(0.5)
    assert len(_request_dirs(tmp_path)) == 2, 'expired-twin fan-out minted duplicates'
    agent_bridge.complete_request(fresh.name, 'owner', '{"scenes": [7]}')
    second.join(timeout=10)
    third.join(timeout=10)
    assert not second.is_alive() and not third.is_alive()
    assert _content(results['second']) == {'scenes': [7]}
    assert _content(results['third']) == {'scenes': [7]}
