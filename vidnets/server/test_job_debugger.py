"""Tests for the stuck-job debugger watchdog (services/job_debugger.py).

Uses fake queue files, a fake agent-bridge dir, and a stub actions object --
no live server, no real restarts, no real queue writes. The debugger's
diagnosis step is monkeypatched per test so the decision engine is tested in
isolation; the real diagnosis helpers (stale inbox scan, log tail, rewake)
are tested directly against fake files.
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from services.job_debugger import (  # noqa: E402
    DebuggerConfig,
    JobDebugger,
    find_stale_inbox_requests,
    force_rewake,
    normalize_progress_text,
    parse_scene_number,
    pick_advance_candidate,
    set_scene_advance,
    is_scene_advance_requested,
    consume_scene_advance,
)

T0 = 1794286000.0


def _job(job_id="f04c6b9f", progress=64, text="Preparing media 14/15", seq=100):
    return {
        "id": job_id,
        "status": "processing",
        "progress": progress,
        "progress_text": text,
        "progress_seq": seq,
        "progress_events": [
            {"seq": seq, "progress": progress, "text": text},
        ],
    }


def _write_queue(path, jobs):
    path.write_text(json.dumps({"items": jobs}), encoding="utf-8")


class _StubActions:
    """Records debugger actions instead of performing them."""

    def __init__(self):
        self.calls = []

    def force_rewake(self, stale):
        self.calls.append(("rewake", stale))
        return {"ok": True, "removed": len(stale)}

    def request_scene_advance(self, job_id, scene_number, reason):
        self.calls.append(("advance_scene", job_id, scene_number, reason))
        return {"ok": True, "job_id": job_id, "scene_number": scene_number}

    def kill_hung_child(self, pid):
        self.calls.append(("kill_hung_child", pid))
        return {"ok": True, "pid": pid}

    def restart_server(self, reason):
        self.calls.append(("restart_server", reason))
        return {"ok": True, "reason": reason}


@pytest.fixture()
def debugger(tmp_path):
    queue_path = tmp_path / "queue.json"
    _write_queue(queue_path, [_job()])
    actions = _StubActions()
    dbg = JobDebugger(
        DebuggerConfig(),
        queue_path=queue_path,
        state_path=tmp_path / "state.json",
        bridge=tmp_path / "bridge",
        log_path=tmp_path / "server-runtime.log",
        seen_path=tmp_path / "seen.txt",
        state={"observations": {}, "actions": {}, "restarts": {}, "flagged": {}},
        actions=actions,
    )
    # Diagnosis recording would touch the real queue manager; silence it.
    dbg._record_diagnosis = lambda job_id, diagnosis, now: None
    return dbg, actions, queue_path


def _fake_diagnosis(**overrides):
    diagnosis = {
        "server_responsive": True,
        "stale_inbox": [],
        "log": {"lines": [], "count": 0, "approval_wait": False, "hung_download": False},
        "hung_children": [],
        "live_render": False,
    }
    diagnosis.update(overrides)
    return diagnosis


def test_normalize_and_scene_number():
    assert normalize_progress_text("Preparing media 14/15 — still working (3877s); please wait") == "Preparing media 14/15"
    assert normalize_progress_text("Generating captions") == "Generating captions"
    assert parse_scene_number("Preparing media 14/15") == 14
    assert parse_scene_number("Preparing media 14/15 — still working (3877s); please wait") == 14
    assert parse_scene_number("Generating captions") is None


def test_progressing_job_no_action(debugger):
    dbg, actions, queue_path = debugger
    dbg.diagnose = lambda job_id: _fake_diagnosis()
    assert dbg.tick(now=T0) == []
    _write_queue(queue_path, [_job(progress=65, text="Preparing media 14/15", seq=105)])
    # Progress changed 2 minutes ago: still moving, however slowly.
    assert dbg.tick(now=T0 + 120) == []
    assert actions.calls == []


def test_stalled_job_with_stale_inbox_rewakes(debugger):
    dbg, actions, queue_path = debugger
    dbg.diagnose = lambda job_id: _fake_diagnosis(
        stale_inbox=[{"id": "abc123", "operation": "vidzeo_visual_review", "kind": "vision", "age_seconds": 300.0, "threshold_seconds": 240}]
    )
    assert dbg.tick(now=T0) == []
    taken = dbg.tick(now=T0 + 11 * 60)
    assert len(taken) == 1
    assert actions.calls[0][0] == "rewake"
    assert actions.calls[0][1][0]["id"] == "abc123"


def test_scene_stuck_advances_scene(debugger):
    dbg, actions, queue_path = debugger
    dbg.diagnose = lambda job_id: _fake_diagnosis()
    assert dbg.tick(now=T0) == []
    # Heartbeat still ticking (seq moves) but the scene counter is frozen.
    _write_queue(queue_path, [_job(seq=130)])
    taken = dbg.tick(now=T0 + 21 * 60)
    assert len(taken) == 1
    assert actions.calls[0][0] == "advance_scene"
    assert actions.calls[0][2] == 14  # scene number from "Preparing media 14/15"


def test_scene_stall_below_threshold_no_action(debugger):
    dbg, actions, queue_path = debugger
    dbg.diagnose = lambda job_id: _fake_diagnosis()
    assert dbg.tick(now=T0) == []
    _write_queue(queue_path, [_job(seq=130)])
    # 11 minutes quiet: past the general stall threshold but the scene
    # threshold is 20 minutes, and no other diagnosis applies.
    assert dbg.tick(now=T0 + 11 * 60) == []
    assert actions.calls == []


def test_dead_worker_restarts_server(debugger):
    dbg, actions, queue_path = debugger
    dbg.diagnose = lambda job_id: _fake_diagnosis(server_responsive=False)
    _write_queue(queue_path, [_job(text="Generating captions", seq=100)])
    assert dbg.tick(now=T0) == []
    # Same seq for 16 minutes: heartbeat thread dead too. No scene number
    # in the text, so the scene branch cannot fire.
    taken = dbg.tick(now=T0 + 16 * 60)
    assert len(taken) == 1
    assert actions.calls[0][0] == "restart_server"
    assert dbg.state["restarts"]["f04c6b9f"] == 1


def test_restart_cap_flags_job_and_stops(debugger):
    dbg, actions, queue_path = debugger
    dbg.diagnose = lambda job_id: _fake_diagnosis(server_responsive=False)
    dbg.state["restarts"]["f04c6b9f"] = 3
    _write_queue(queue_path, [_job(text="Generating captions", seq=100)])
    assert dbg.tick(now=T0) == []
    taken = dbg.tick(now=T0 + 16 * 60)
    assert taken == []
    assert actions.calls == []
    assert "f04c6b9f" in dbg.state["flagged"]


def test_action_rate_limit(debugger):
    dbg, actions, queue_path = debugger
    dbg.diagnose = lambda job_id: _fake_diagnosis(
        stale_inbox=[{"id": "abc123", "operation": "x", "kind": "vision", "age_seconds": 300.0, "threshold_seconds": 240}]
    )
    dbg.state["actions"]["f04c6b9f"] = [
        {"action": "rewake", "reason": "x", "at": T0 - 600},
        {"action": "rewake", "reason": "x", "at": T0 - 1200},
        {"action": "rewake", "reason": "x", "at": T0 - 1800},
    ]
    assert dbg.tick(now=T0) == []
    assert dbg.tick(now=T0 + 11 * 60) == []
    assert actions.calls == []


def test_hung_child_killed(debugger):
    dbg, actions, queue_path = debugger
    dbg.diagnose = lambda job_id: _fake_diagnosis(
        hung_children=[{"pid": 4242, "age_seconds": 1200.0, "cmdline": "yt-dlp ...", "kind": "yt-dlp"}]
    )
    assert dbg.tick(now=T0) == []
    taken = dbg.tick(now=T0 + 11 * 60)
    assert len(taken) == 1
    assert actions.calls[0] == ("kill_hung_child", 4242)


def test_find_stale_inbox_requests(tmp_path):
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    now = T0

    def _request(dirname, job_id, age, state="pending", operation="vidzeo_visual_review", answered=False, expired=False):
        folder = bridge / dirname
        folder.mkdir()
        payload = {
            "id": dirname,
            "job_id": job_id,
            "operation": operation,
            "kind": "vision",
            "state": state,
            "created_at": now - age,
            "expires_at": now - 10 if expired else now + 600,
        }
        (folder / "request.json").write_text(json.dumps(payload), encoding="utf-8")
        if answered:
            (folder / "response.json").write_text("{}", encoding="utf-8")

    _request("stale-live", "f04c6b9f", age=300)               # stale
    _request("other-job", "deadbeef", age=900)               # wrong job: ignored
    _request("answered", "f04c6b9f", age=900, answered=True)  # answered: ignored
    _request("fresh", "f04c6b9f", age=60)                    # fresh: ignored
    _request("expired", "f04c6b9f", age=900, expired=True)   # expired: ignored
    _request("stale-scriptit", "f04c6b9f", age=150, operation="scriptit.draft")  # 120s threshold: stale

    stale = find_stale_inbox_requests("f04c6b9f", now=now, bridge=bridge)
    assert {item["id"] for item in stale} == {"stale-live", "stale-scriptit"}


def test_force_rewake_removes_seen_ids(tmp_path):
    seen = tmp_path / "seen.txt"
    seen.write_text("abc123 1794285000\ndef456 1794285000\n", encoding="utf-8")
    result = force_rewake([{"id": "abc123"}], seen_path=seen)
    assert result["ok"] is True and result["removed"] == 1
    assert seen.read_text(encoding="utf-8") == "def456 1794285000\n"


def test_pick_advance_candidate(tmp_path):
    # On-disk files: a fatal-mismatch clip (high score, must be skipped), a
    # partial-match clip (usable), an ungated clip (not vision-checked), and
    # a candidate whose file is missing.
    fatal_path = tmp_path / "fatal.mp4"
    fatal_path.write_bytes(b"x" * 2048)
    partial_path = tmp_path / "partial.mp4"
    partial_path.write_bytes(b"x" * 2048)
    low_path = tmp_path / "low.mp4"
    low_path.write_bytes(b"x" * 2048)
    ungated_path = tmp_path / "ungated.mp4"
    ungated_path.write_bytes(b"x" * 2048)
    candidates = [
        {"path": str(fatal_path), "final_score": 90.0, "source": "youtube"},
        {"path": str(partial_path), "final_score": 58.0, "source": "youtube"},
        {"path": str(low_path), "final_score": 42.0, "source": "youtube"},
        {"path": str(ungated_path), "final_score": 80.0, "source": "web"},
        {"path": str(tmp_path / "missing.mp4"), "final_score": 99.0, "source": "web"},
    ]
    gated = {
        (3, str(fatal_path)): (90.0, 0, False, "rejected", 15.0, {"accepted": False, "fatal_mismatch": True}),
        (3, str(partial_path)): (58.0, 0, False, "partial_match", 58.0, {"accepted": False, "scene_match": False}),
        (3, str(low_path)): (42.0, 0, False, "partial_match", 42.0, {"accepted": False, "scene_match": False}),
    }
    pick = pick_advance_candidate(3, candidates, gated)
    assert pick is not None
    candidate, visual_score, verdict = pick
    assert candidate["path"] == str(partial_path)  # best non-fatal vision score
    assert visual_score == 58.0


def test_pick_advance_candidate_nothing_usable(tmp_path):
    assert pick_advance_candidate(0, [], {}) is None
    assert pick_advance_candidate(0, [{"path": "/nonexistent/x.mp4", "final_score": 99.0}], {}) is None


def test_pick_advance_candidate_skips_irrelevant_verdict(tmp_path):
    # A high-scoring clip vision called off-topic is not "closest" media,
    # whatever its score -- it must not be reused for the scene.
    clip = tmp_path / "offtopic.mp4"
    clip.write_bytes(b"x" * 2048)
    candidates = [{"path": str(clip), "final_score": 90.0, "source": "youtube"}]
    gated = {
        (0, str(clip)): (
            90.0, 0, False, "visual_rejected", 90.0,
            {"accepted": False, "reason": "off_topic", "fatal_mismatch": False},
        ),
    }
    assert pick_advance_candidate(0, candidates, gated) is None


def test_pick_advance_candidate_keeps_soft_partial_match(tmp_path):
    # The coordinator labels a soft partial-match rejection in a non-relaxed
    # scene "explicit_scene_conflict", but the model's own verdict is just a
    # wrong-viewpoint partial match -- exactly the closest-available media.
    clip = tmp_path / "partial.mp4"
    clip.write_bytes(b"x" * 2048)
    candidates = [{"path": str(clip), "final_score": 58.0, "source": "youtube"}]
    gated = {
        (0, str(clip)): (
            58.0, 0, False, "explicit_scene_conflict", 58.0,
            {
                "accepted": False, "status": "rejected",
                "reason": "partial_match", "fatal_mismatch": False,
                "rejection_reason": "explicit_scene_conflict",
            },
        ),
    }
    pick = pick_advance_candidate(0, candidates, gated)
    assert pick is not None
    assert pick[0]["path"] == str(clip)
    assert pick[1] == 58.0


def test_advance_registry_roundtrip():
    set_scene_advance("f04c6b9f", 14, "test")
    assert is_scene_advance_requested(13) is True   # scene 14 (0-based 13)
    assert is_scene_advance_requested(14) is True   # at-or-after also matches
    assert is_scene_advance_requested(5) is False
    record = consume_scene_advance(13)
    assert record is not None and record["scene_number"] == 14
    assert is_scene_advance_requested(13) is False
