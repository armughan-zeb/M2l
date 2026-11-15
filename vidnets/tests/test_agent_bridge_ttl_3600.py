"""TTL 1200 -> 3600s for the agent inbox (Nov 15 2026 speedup, item a).

The effective TTL observed in production was 1200s (caller timeouts); the
new floor is VIDZEO_AGENT_MIN_WALL_SECONDS = 3600s. The wall is a deadline,
not a delay: an answer submitted inside the window is accepted at once,
and expiry still eventually happens (proven here with a short wall, since
waiting out a real 3600s wall would stall the suite).
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
        'operation': 'visual_review',
        'kind': 'vision',
        'messages': [{'role': 'user', 'content': 'score this'}],
        'response_format': {'type': 'json_object'},
    }
    payload.update(overrides)
    return payload


def _post_kwargs(payload):
    return {
        'messages': payload['messages'],
        'response_format': payload['response_format'],
    }


def test_agent_wall_default_is_3600s(tmp_path, monkeypatch):
    # No env override: a fresh request must carry a 3600s expiry.
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    monkeypatch.delenv('VIDZEO_AGENT_MIN_WALL_SECONDS', raising=False)
    assert agent_bridge._agent_wall_seconds() == 3600.0

    def _wait():
        with job_scope({'agent_owner_id': 'owner', 'job_id': 'job-1'}):
            agent_bridge.agent_post(
                _post_kwargs(_payload()), timeout=2,
                operation='visual_review', kind='vision')

    thread = threading.Thread(target=_wait, daemon=True)
    thread.start()
    # Wait until the request dir appears AND request.json is written: the
    # dir is created before the file lands, so reading on dir-appearance
    # alone races (FileNotFoundError under load).
    deadline = time.monotonic() + 10
    row = None
    while time.monotonic() < deadline and row is None:
        for candidate in tmp_path.iterdir():
            if not candidate.is_dir():
                continue
            request_file = candidate / 'request.json'
            if not request_file.is_file():
                continue
            try:
                row = json.loads(request_file.read_text(encoding='utf-8'))
            except ValueError:
                continue
            break
        if row is None:
            time.sleep(0.05)
    assert row is not None, 'agent_post did not mint a request'
    assert row['expires_at'] - row['created_at'] == pytest.approx(3600.0, abs=1.0)
    # Answer inside the window: the wait must end at once, not at 3600s.
    agent_bridge.complete_request(row['id'], 'owner', '{"score": 85}')
    thread.join(timeout=10)
    assert not thread.is_alive(), 'agent_post did not return on an in-window answer'


def test_answer_inside_new_window_is_accepted(tmp_path, monkeypatch):
    # Same as above, but assert on the returned content: an answer filed
    # after the old 1200s TTL would have expired is accepted at ~1s.
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    monkeypatch.delenv('VIDZEO_AGENT_MIN_WALL_SECONDS', raising=False)
    monkeypatch.setattr(agent_bridge, '_ACTIVITY_RECORD_SECONDS', 0.2)
    results = {}

    def _wait():
        with job_scope({'agent_owner_id': 'owner', 'job_id': 'job-1'}):
            try:
                results['response'] = agent_bridge.agent_post(
                    _post_kwargs(_payload()), timeout=2,
                    operation='visual_review', kind='vision')
            except Exception as exc:  # noqa: BLE001 - record the failure
                results['error'] = exc

    thread = threading.Thread(target=_wait, daemon=True)
    thread.start()
    time.sleep(0.5)
    dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
    assert len(dirs) == 1
    request_id = dirs[0].name
    agent_bridge.complete_request(request_id, 'owner', '{"score": 85}')
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert 'response' in results, results.get('error')
    assert json.loads(results['response'].json()['choices'][0]['message']['content']) == {'score': 85}


def test_expiry_still_happens_with_a_short_wall(tmp_path, monkeypatch):
    # The wall is longer, not infinite: with a 1s floor an unanswered
    # request still expires and is persisted as expired.
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    monkeypatch.setenv('VIDZEO_AGENT_MIN_WALL_SECONDS', '1')
    start = time.monotonic()
    with job_scope({'agent_owner_id': 'owner', 'job_id': 'job-1'}):
        with pytest.raises(agent_bridge.AgentBridgeError, match='expired'):
            agent_bridge.agent_post(
                _post_kwargs(_payload()), timeout=1,
                operation='visual_review', kind='vision')
    assert time.monotonic() - start < 10
    dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
    assert len(dirs) == 1
    row = json.loads((dirs[0] / 'request.json').read_text(encoding='utf-8'))
    assert row['state'] == 'expired'


def test_env_override_still_tunes_the_wall(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    monkeypatch.setenv('VIDZEO_AGENT_MIN_WALL_SECONDS', '600')
    assert agent_bridge._agent_wall_seconds() == 600.0
    monkeypatch.setenv('VIDZEO_AGENT_MIN_WALL_SECONDS', '1200')
    assert agent_bridge._agent_wall_seconds() == 1200.0
