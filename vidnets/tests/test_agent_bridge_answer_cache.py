import json
import os
import time

from services import agent_bridge


def _payload(**overrides):
    payload = {
        'job_id': 'job-1',
        'operation': 'visual_review',
        'kind': 'vision',
        'messages': [{'role': 'user', 'content': 'score this'}],
        'response_format': {'type': 'json_object'},
    }
    payload.update(overrides)
    return payload


def _make_request(root, request_id, payload, *, expires_in=120, state='pending', mtime=None):
    directory = root / request_id
    directory.mkdir(parents=True, exist_ok=True)
    now = time.time()
    row = {
        'id': request_id,
        'owner_id': 'owner',
        'state': state,
        'job_id': payload.get('job_id', 'job-1'),
        'operation': payload.get('operation', 'visual_review'),
        'kind': payload.get('kind', 'vision'),
        'messages': payload.get('messages', []),
        'response_format': payload.get('response_format', {}),
        'created_at': now,
        'expires_at': now + expires_in,
    }
    path = directory / 'request.json'
    path.write_text(json.dumps(row), encoding='utf-8')
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return row


def test_fingerprint_matches_reissue_and_differs_on_payload(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    base = _payload()
    reissue = _payload()
    changed = _payload(messages=[{'role': 'user', 'content': 'score that'}])
    assert agent_bridge.payload_fingerprint(base) == agent_bridge.payload_fingerprint(reissue)
    assert agent_bridge.payload_fingerprint(base) != agent_bridge.payload_fingerprint(changed)


def test_complete_caches_answer_and_reuse_completes_reissue(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    payload = _payload()
    first = 'a' * 32
    second = 'b' * 32
    _make_request(tmp_path, first, payload)
    agent_bridge.complete_request(first, 'owner', '{"score": 85}')
    # Re-issued under a new id with the identical payload.
    _make_request(tmp_path, second, payload)
    assert agent_bridge.cached_answer_for(second, 'owner') == '{"score": 85}'
    assert agent_bridge.reuse_cached_answer(second) == {'reused': True, 'request_id': second}
    row = json.loads((tmp_path / second / 'request.json').read_text(encoding='utf-8'))
    assert row['state'] == 'answered'
    reply = json.loads((tmp_path / second / 'response.json').read_text(encoding='utf-8'))
    assert reply == {'content': '{"score": 85}'}


def test_reuse_returns_false_without_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    request_id = 'c' * 32
    _make_request(tmp_path, request_id, _payload())
    assert agent_bridge.reuse_cached_answer(request_id) == {'reused': False, 'request_id': request_id}


def test_reuse_rejects_unknown_request(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    try:
        agent_bridge.reuse_cached_answer('d' * 32)
    except ValueError as exc:
        assert 'not found' in str(exc)
    else:
        raise AssertionError('expected ValueError for an unknown request')


def test_pending_scan_skips_old_entries_without_parsing(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    now = time.time()
    # Expired an hour ago: inside the 24h prune grace (so the prune keeps
    # them) but far past the 180s pending cutoff (so the scan must skip
    # them without parsing — read_request would flip them to 'expired').
    for index in range(5):
        _make_request(tmp_path, f'{index:032d}', _payload(),
                      expires_in=-3600, mtime=now - 3600)
    live = 'e' * 32
    _make_request(tmp_path, live, _payload(), expires_in=120)
    rows = agent_bridge.pending_requests('owner')
    assert [row['id'] for row in rows] == [live]
    for index in range(5):
        row = json.loads((tmp_path / f'{index:032d}' / 'request.json').read_text(encoding='utf-8'))
        assert row['state'] == 'pending', 'old entry was parsed instead of skipped'


def test_prune_removes_only_terminal_old_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    now = time.time()
    old = now - 25 * 3600
    # Live request: kept.
    _make_request(tmp_path, 'f' * 32, _payload(), expires_in=120)
    # Recent expiry (1h ago): inside the 24h grace, kept.
    _make_request(tmp_path, '0' * 32, _payload(), expires_in=-3600, mtime=now - 3600)
    # Old expiry (25h ago): pruned.
    _make_request(tmp_path, '1' * 32, _payload(), expires_in=-25 * 3600, mtime=old)
    # Old answered (25h ago): terminal and past expiry, pruned.
    _make_request(tmp_path, '2' * 32, _payload(), expires_in=-25 * 3600,
                  state='answered', mtime=old)
    # Cache a composed answer first: the cache file must survive the prune.
    _make_request(tmp_path, '3' * 32, _payload(), expires_in=120)
    agent_bridge.complete_request('3' * 32, 'owner', '{"score": 85}')
    assert agent_bridge.prune_bridge() == 2
    assert (tmp_path / ('f' * 32)).is_dir()
    assert (tmp_path / ('0' * 32)).is_dir()
    assert not (tmp_path / ('1' * 32)).exists()
    assert not (tmp_path / ('2' * 32)).exists()
    assert (tmp_path / '_answer_cache.json').is_file()
    cache = json.loads((tmp_path / '_answer_cache.json').read_text(encoding='utf-8'))
    fingerprint = agent_bridge.payload_fingerprint(_payload())
    assert cache[fingerprint]['content'] == '{"score": 85}'
