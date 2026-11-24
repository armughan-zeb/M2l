"""Regression tests for the agent-bridge request claim protocol.

2026-11-21, job a42a1b7b: the daemon's resident inbox and hook-woken
agents both picked up the same visual_review requests at once. The
bridge had no ownership state, so every answerer composed in parallel
and complete_request's first-write-wins refused all but the fastest
with "already answered" — 6 answers lost in ~2.5 min (07:57:24–07:59:46
UTC). A claim marks a pending request as owned by one answerer for a
lease; every picker skips live claims, and complete_request refuses a
completion from anyone but the claim owner while the lease is live.
"""
import json
import time

import pytest

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


def _make_request(root, request_id, payload, *, expires_in=120, state='pending'):
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
    (directory / 'request.json').write_text(json.dumps(row), encoding='utf-8')
    return row


def _read_row(root, request_id):
    return json.loads((root / request_id / 'request.json').read_text(encoding='utf-8'))


def _write_row(root, request_id, row):
    (root / request_id / 'request.json').write_text(json.dumps(row), encoding='utf-8')


def test_claim_hides_request_from_pending(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    request_id = 'a' * 32
    _make_request(tmp_path, request_id, _payload())
    assert [row['id'] for row in agent_bridge.pending_requests('owner')] == [request_id]
    assert agent_bridge.claim_request(request_id, 'owner', 'resident-inbox') is True
    assert agent_bridge.pending_requests('owner') == []
    row = _read_row(tmp_path, request_id)
    assert row['state'] == 'claimed'
    assert row['claimed_by'] == 'resident-inbox'


def test_claim_by_another_fails_and_same_claimant_refreshes(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    request_id = 'b' * 32
    _make_request(tmp_path, request_id, _payload())
    assert agent_bridge.claim_request(request_id, 'owner', 'resident-inbox') is True
    assert agent_bridge.claim_request(request_id, 'owner', 'hook-agent') is False
    # Same claimant re-claiming refreshes its own lease.
    assert agent_bridge.claim_request(request_id, 'owner', 'resident-inbox') is True
    assert _read_row(tmp_path, request_id)['claimed_by'] == 'resident-inbox'


def test_complete_by_owner_succeeds_while_claimed(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    request_id = 'c' * 32
    _make_request(tmp_path, request_id, _payload())
    assert agent_bridge.claim_request(request_id, 'owner', 'resident-inbox') is True
    agent_bridge.complete_request(request_id, 'owner', '{"score": 80}', claimant='resident-inbox')
    row = _read_row(tmp_path, request_id)
    assert row['state'] == 'answered'
    assert 'claimed_by' not in row


def test_complete_by_another_while_claimed_refused_with_claim_error(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    request_id = 'd' * 32
    _make_request(tmp_path, request_id, _payload())
    assert agent_bridge.claim_request(request_id, 'owner', 'resident-inbox') is True
    with pytest.raises(ValueError, match='claimed by another worker'):
        agent_bridge.complete_request(request_id, 'owner', '{"score": 80}', claimant='hook-agent')
    # The loser's attempt did not answer the request: the owner can still
    # complete it.
    assert _read_row(tmp_path, request_id)['state'] == 'claimed'
    agent_bridge.complete_request(request_id, 'owner', '{"score": 80}', claimant='resident-inbox')
    assert _read_row(tmp_path, request_id)['state'] == 'answered'


def test_expired_claim_reclaimable_by_another(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    request_id = 'e' * 32
    _make_request(tmp_path, request_id, _payload())
    assert agent_bridge.claim_request(request_id, 'owner', 'resident-inbox') is True
    # The owner died mid-work: its lease lapsed.
    row = _read_row(tmp_path, request_id)
    row['claim_expires_at'] = time.time() - 1
    _write_row(tmp_path, request_id, row)
    assert agent_bridge.claim_request(request_id, 'owner', 'hook-agent') is True
    assert _read_row(tmp_path, request_id)['claimed_by'] == 'hook-agent'
    # And a read resets a lapsed claim back to pending when nobody
    # re-claims it.
    row = _read_row(tmp_path, request_id)
    row['claim_expires_at'] = time.time() - 1
    _write_row(tmp_path, request_id, row)
    assert agent_bridge.read_request(request_id, 'owner')['state'] == 'pending'


def test_release_claim_reopens_for_pending(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    request_id = 'f' * 32
    _make_request(tmp_path, request_id, _payload())
    assert agent_bridge.claim_request(request_id, 'owner', 'resident-inbox') is True
    # Only the claim owner may release it.
    assert agent_bridge.release_claim(request_id, 'owner', 'hook-agent') is False
    assert agent_bridge.release_claim(request_id, 'owner', 'resident-inbox') is True
    assert _read_row(tmp_path, request_id)['state'] == 'pending'
    assert [row['id'] for row in agent_bridge.pending_requests('owner')] == [request_id]
    # After the release, another answerer may complete it.
    agent_bridge.complete_request(request_id, 'owner', '{"score": 80}', claimant='hook-agent')
    assert _read_row(tmp_path, request_id)['state'] == 'answered'


def test_unclaimed_complete_still_works(tmp_path, monkeypatch):
    # Backward compatibility: an answerer that never claimed (old code,
    # or a request filed before claims existed) still completes.
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    request_id = '0' * 32
    _make_request(tmp_path, request_id, _payload())
    agent_bridge.complete_request(request_id, 'owner', '{"score": 80}')
    assert _read_row(tmp_path, request_id)['state'] == 'answered'
