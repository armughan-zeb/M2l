"""Tests for the stuck-job debugger watchdog (services/job_debugger.py).

Uses fake queue files, a fake agent-bridge dir, and a stub actions object --
no live server, no real restarts, no real queue writes. The debugger's
diagnosis step is monkeypatched per test so the decision engine is tested in
isolation; the real diagnosis helpers (stale inbox scan, log tail, rewake)
are tested directly against fake files.
"""
import json
import os
import subprocess
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
    restart_server,
    set_scene_advance,
    is_scene_advance_requested,
    consume_scene_advance,
    tail_job_log,
)
from services import job_debugger as job_debugger_module  # noqa: E402

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
    # Rate-limit holds are recorded the same way; silence that too.
    dbg._record_event = lambda job_id, text: None
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


def test_restart_cap_flags_job_and_escalates_instead_of_restarting(debugger):
    dbg, actions, queue_path = debugger
    dbg.diagnose = lambda job_id: _fake_diagnosis(server_responsive=False)
    dbg.state["restarts"]["f04c6b9f"] = 3
    _write_queue(queue_path, [_job(text="Generating captions", seq=100)])
    assert dbg.tick(now=T0) == []
    taken = dbg.tick(now=T0 + 16 * 60)
    # No server restart after the ever-cap — but the job is NOT left
    # silently parked: the driver escalates it so a human can see it.
    assert actions.calls == []
    assert "f04c6b9f" in dbg.state["flagged"]
    assert taken == [
        {
            "job_id": "f04c6b9f",
            "kind": "escalate",
            "reason": "debugger restarted 3 times; left alone",
        }
    ]


def test_safe_actions_not_capped_by_prior_safe_actions(debugger):
    # REGRESSION (2026-11-16): the per-hour budget used to count every
    # recorded action, so three rewakes in an hour blocked further safe
    # recovery. Safe actions are bounded by the tick only, never capped.
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
    taken = dbg.tick(now=T0 + 11 * 60)
    assert len(taken) == 1
    assert actions.calls[0][0] == "rewake"


def test_restart_not_blocked_by_prior_safe_actions(debugger):
    # REGRESSION (2026-11-16, replayed live): a dead-worker job got NO
    # restart because three prior rewakes had consumed the per-hour
    # budget. Only prior restarts count against a restart.
    dbg, actions, queue_path = debugger
    dbg.diagnose = lambda job_id: _fake_diagnosis(server_responsive=False)
    dbg.state["actions"]["f04c6b9f"] = [
        {"action": "rewake", "reason": "x", "at": T0 - 600},
        {"action": "advance_scene", "reason": "x", "at": T0 - 1200},
        {"action": "kill_hung_child", "reason": "x", "at": T0 - 1800},
    ]
    _write_queue(queue_path, [_job(text="Generating captions", seq=100)])
    assert dbg.tick(now=T0) == []
    taken = dbg.tick(now=T0 + 16 * 60)
    assert len(taken) == 1
    assert actions.calls[0][0] == "restart_server"
    assert dbg.state["restarts"]["f04c6b9f"] == 1


def test_restart_rate_limit_counts_only_restarts(debugger):
    # The per-hour restart budget still works — it just counts restarts.
    # max_restarts is raised here so the ever-cap does not mask the
    # hourly check (with the default 3 ever, three restarts in an hour
    # already flags the job first).
    dbg, actions, queue_path = debugger
    dbg.config = DebuggerConfig(
        env={
            "VIDZEO_JOB_DEBUGGER_MAX_RESTARTS": "10",
            "VIDZEO_JOB_DEBUGGER_MAX_ACTIONS_PER_HOUR": "3",
        }
    )
    dbg.diagnose = lambda job_id: _fake_diagnosis(server_responsive=False)
    dbg.state["actions"]["f04c6b9f"] = [
        {"action": "restart_server", "reason": "x", "at": T0 - 600},
        {"action": "restart_server", "reason": "x", "at": T0 - 1200},
        {"action": "restart_server", "reason": "x", "at": T0 - 1800},
    ]
    _write_queue(queue_path, [_job(text="Generating captions", seq=100)])
    assert dbg.tick(now=T0) == []
    assert dbg.tick(now=T0 + 16 * 60) == []
    assert actions.calls == []
    # Two prior restarts in the hour: the restart is allowed.
    dbg.state["actions"]["f04c6b9f"] = dbg.state["actions"]["f04c6b9f"][:2]
    taken = dbg.tick(now=T0 + 32 * 60)
    assert len(taken) == 1
    assert actions.calls[0][0] == "restart_server"


def test_flagged_job_still_gets_safe_action(debugger):
    # REGRESSION (2026-11-16): the tick used to skip a flagged job
    # wholesale, so a restart-cap flag silently blocked safe recovery
    # too. The flag blocks restarts only; a rewake still fires.
    dbg, actions, queue_path = debugger
    dbg.diagnose = lambda job_id: _fake_diagnosis(
        stale_inbox=[{"id": "abc123", "operation": "x", "kind": "vision", "age_seconds": 300.0, "threshold_seconds": 240}]
    )
    dbg.state["restarts"]["f04c6b9f"] = 3
    dbg.state["flagged"]["f04c6b9f"] = {"reason": "debugger restarted 3 times; left alone", "at": T0}
    assert dbg.tick(now=T0) == []
    taken = dbg.tick(now=T0 + 11 * 60)
    assert actions.calls[0][0] == "rewake"
    assert all(call[0] != "restart_server" for call in actions.calls)
    assert len(taken) == 1 and taken[0]["job_id"] == "f04c6b9f"


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


def test_tail_job_log_reads_tail_without_type_error(tmp_path):
    # Regression: tail_job_log read the log in text mode, encoded each chunk
    # to bytes, then called bytes.count("\\n") with a str — every tick that
    # reached diagnose() crashed with TypeError and the watchdog never acted.
    log_path = tmp_path / "server-runtime.log"
    filler = "".join(f"[noise] filler line {i}\n" for i in range(500))
    log_path.write_text(
        filler
        + "[queue] job f04c6b9f heartbeat: Generating captions — still working (425s); please wait\n"
        + "[queue] job f04c6b9f heartbeat: Generating captions — still working (486s); please wait\n",
        encoding="utf-8",
    )
    result = tail_job_log("f04c6b9f", log_path=log_path)
    assert result["count"] == 2
    assert "Generating captions" in result["lines"][0]
    assert result["approval_wait"] is False


def test_tail_job_log_missing_file_returns_empty(tmp_path):
    result = tail_job_log("f04c6b9f", log_path=tmp_path / "no-such.log")
    assert result == {
        "lines": [],
        "count": 0,
        "approval_wait": False,
        "hung_download": False,
    }


# ---------------------------------------------------------------------------
# restart_server (2026-11-21 death loop): the restarter used to cd to
# vidnets/server/ and run `bash VIDNETS_AGENT.sh` there, but the launcher
# lives in vidnets/ -- so the start failed with "No such file" and the
# server never came back, silently (stderr to DEVNULL, start backgrounded
# with &, port never verified). These tests pin the fixed behaviour.
# ---------------------------------------------------------------------------


def _fake_vidnets_tree(tmp_path):
    """A vidnets/ tree with a launcher, for restart_server's __file__ math.

    restart_server derives the repo root as parent.parent.parent of its
    own __file__ (services/job_debugger.py -> server -> vidnets)."""
    services_dir = tmp_path / "vidnets" / "server" / "services"
    services_dir.mkdir(parents=True)
    return tmp_path / "vidnets" / "VIDNETS_AGENT.sh", services_dir / "job_debugger.py"


def test_restart_server_aborts_when_launcher_missing(monkeypatch, tmp_path):
    launcher, fake_file = _fake_vidnets_tree(tmp_path)
    assert not launcher.exists()
    monkeypatch.setattr(job_debugger_module, "__file__", str(fake_file))
    monkeypatch.setenv("VIDZEO_RUNTIME_ROOT", str(tmp_path / "runtime"))

    spawned = []
    monkeypatch.setattr(
        job_debugger_module.subprocess, "Popen",
        lambda *args, **kwargs: spawned.append((args, kwargs)),
    )
    result = restart_server("dead worker test")
    assert result["ok"] is False
    assert "VIDNETS_AGENT.sh not found" in result["error"]
    assert spawned == []  # never spawn a restarter that cannot start anything
    restarter_log = tmp_path / "runtime" / "logs" / "job-debugger-restart.log"
    assert "VIDNETS_AGENT.sh not found" in restarter_log.read_text(encoding="utf-8")


def test_restart_server_script_kills_exact_pid_and_uses_repo_root(monkeypatch, tmp_path):
    launcher, fake_file = _fake_vidnets_tree(tmp_path)
    launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    monkeypatch.setattr(job_debugger_module, "__file__", str(fake_file))
    monkeypatch.setenv("VIDZEO_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setattr(os, "getpid", lambda: 424242)

    spawned = []

    class _FakePopen:
        def __init__(self, args, **kwargs):
            spawned.append((args, kwargs))

    monkeypatch.setattr(job_debugger_module.subprocess, "Popen", _FakePopen)
    result = restart_server("dead worker test")
    assert result["ok"] is True
    assert result["pid"] == 424242
    assert len(spawned) == 1
    args, kwargs = spawned[0]
    assert kwargs.get("start_new_session") is True  # detached from the dying server
    assert kwargs.get("stdin") is subprocess.DEVNULL
    assert kwargs.get("stdout") is not subprocess.DEVNULL  # failures must be loud
    assert kwargs.get("stderr") is subprocess.STDOUT

    script = (tmp_path / "runtime" / "logs" / "job-debugger-restart.log").read_text(encoding="utf-8")
    assert f'bash "{launcher}"' in script  # repo root, not vidnets/server/
    assert "kill -TERM 424242" in script
    assert "kill -KILL 424242" in script
    assert "pkill" not in script  # exact PID only, never a pattern kill
    assert "/proc/424242/cmdline" in script and "main.py" in script  # PID guard
    assert "nohup" not in script  # start runs in the foreground: its rc is checked
    assert "start_rc" in script and "VERIFIED" in script and "5010" in script


def test_restart_server_actually_restarts_fake_server(monkeypatch, tmp_path):
    """End to end with a fake server + fake launcher (no live server touched).

    A fake ``main.py`` sleeps as the server PID; a fake VIDNETS_AGENT.sh
    starts a scratch-port HTTP listener and exits 0 once it answers.
    The real restarter script must kill the fake server, run the fake
    launcher, verify the port, and log VERIFIED.
    """
    import socket
    import subprocess
    import sys

    launcher, fake_file = _fake_vidnets_tree(tmp_path)
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    verify_port = probe.getsockname()[1]
    probe.close()
    pid_file = tmp_path / "fake-http.pid"
    launcher.write_text(
        "#!/usr/bin/env bash\n"
        f'python3 -m http.server {verify_port} --bind 127.0.0.1 >/dev/null 2>&1 &\n'
        f'echo $! > "{pid_file}"\n'
        "for i in $(seq 1 20); do\n"
        f"  if timeout 2 bash -c 'exec 3<>/dev/tcp/127.0.0.1/{verify_port}' 2>/dev/null; then exit 0; fi\n"
        "  sleep 0.5\n"
        "done\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_main = tmp_path / "main.py"
    fake_main.write_text("import time\ntime.sleep(300)\n", encoding="utf-8")
    monkeypatch.setattr(job_debugger_module, "__file__", str(fake_file))
    monkeypatch.setenv("VIDZEO_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("VIDZEO_JOB_DEBUGGER_RESTART_VERIFY_PORT", str(verify_port))

    server_proc = subprocess.Popen(
        [sys.executable, str(fake_main)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    http_pid = None
    try:
        monkeypatch.setattr(os, "getpid", lambda: server_proc.pid)
        result = restart_server("e2e fake restart")
        assert result["ok"] is True
        restarter_log = tmp_path / "runtime" / "logs" / "job-debugger-restart.log"
        # The log first contains the script text itself (which mentions
        # VERIFIED in an echo line), so wait for the SECOND occurrence:
        # the one the running restarter actually printed.
        deadline = time.time() + 30
        while time.time() < deadline:
            if restarter_log.read_text(encoding="utf-8", errors="replace").count(
                "VERIFIED: server responding"
            ) >= 2:
                break
            time.sleep(0.5)
        log_text = restarter_log.read_text(encoding="utf-8", errors="replace")
        assert log_text.count("VERIFIED: server responding") >= 2, log_text[-2000:]
        assert f"responding on :{verify_port}" in log_text
        assert server_proc.poll() is not None, "restarter did not kill the fake server"
        # The "server" the fake launcher started really answers.
        with socket.create_connection(("127.0.0.1", verify_port), timeout=3):
            pass
    finally:
        if server_proc.poll() is None:
            server_proc.kill()
        try:
            http_pid = int(pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            http_pid = None
        if http_pid:
            try:
                os.kill(http_pid, 15)
            except OSError:
                pass
