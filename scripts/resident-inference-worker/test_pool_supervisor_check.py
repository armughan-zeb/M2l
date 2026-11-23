"""Tests for the pool-supervisor-watch liveness mechanism.

Covers pool_supervisor_check.py (the decision core) and
pool-supervisor-watch.sh (the hook wrapper). All state lives in tmp dirs
under fake spools; the live spool is never touched except read-only by
nothing here. The render gate is stubbed with a tiny shell script that
exits 0 (idle) or 2 (render ACTIVE), exactly like render-gate.js status.

Run:  <repo>/vidnets/.venv/bin/python -m pytest \
        scripts/resident-inference-worker/test_pool_supervisor_check.py -v
"""

import json
import os
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(HERE, "pool_supervisor_check.py")
WRAPPER = os.path.join(HERE, "pool-supervisor-watch.sh")


def _write(path, row):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(row, fh)


def _stub_gate(path, exit_code):
    """A stub render gate: a real Node script, because the checker runs
    it as `node <gate> status` — a bash `exit N` stub would be a JS
    syntax error and fail open instead of reporting ACTIVE."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/usr/bin/env node\nprocess.exit(%d);\n" % exit_code)
    os.chmod(path, 0o755)


def _spool(tmp_path, status_age=None, marker_age=None, workers=(),
           parked=False, pending=0):
    """Build a fake spool. Ages are seconds old (None = file absent)."""
    spool = tmp_path / "spool"
    spool.mkdir()
    now_ms = time.time() * 1000
    if status_age is not None:
        _write(spool / "pool-status.json",
               {"updatedAt": now_ms - status_age * 1000,
                "workers": len(workers), "target": 3, "pending": pending})
    if marker_age is not None:
        _write(spool / "supervisor-alive.json",
               {"updatedAt": now_ms - marker_age * 1000,
                "supervisorAgentId": "sup-old", "workers": len(workers),
                "target": 3})
    for worker_id, age in workers:
        _write(spool / ("worker-alive-%s.json" % worker_id),
               {"updatedAt": now_ms - age * 1000,
                "worker": "resident-inference", "workerId": worker_id,
                "pending": 0, "answered": 1})
    if parked:
        _write(spool / "supervisor-parked.json",
               {"parkedAt": now_ms, "reason": "parent stop"})
    for i in range(pending):
        _write(spool / ("%032x.request.json" % i),
               {"request": {"id": "%032x" % i,
                            "expires_at": time.time() + 3600}})
    return str(spool)


def _run_checker(spool, state_dir, gate, dry_run=False, extra_env=None):
    env = dict(os.environ)
    env.update({
        "MUSE_INFERENCE_SPOOL_DIR": spool,
        "POOL_WATCH_STATE_DIR": str(state_dir),
        "POOL_WATCH_RENDER_GATE": gate,
        "HATCH_HOOK_DRY_RUN": "1" if dry_run else "0",
    })
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run([sys.executable, CHECK], capture_output=True,
                          text=True, env=env, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture
def idle_gate(tmp_path):
    gate = str(tmp_path / "gate-idle.sh")
    _stub_gate(gate, 0)
    return gate


@pytest.fixture
def active_gate(tmp_path):
    gate = str(tmp_path / "gate-active.sh")
    _stub_gate(gate, 2)
    return gate


def test_dead_supervisor_wakes_and_cooldown_suppresses_second_wake(
        tmp_path, idle_gate):
    spool = _spool(tmp_path, status_age=600, marker_age=600)
    state = tmp_path / "state"
    first = _run_checker(spool, state, idle_gate)
    assert first["decision"] == "wake"
    assert first["reason"] == "supervisor_dead"
    assert first["payload"]["live_workers"] == []
    assert first["payload"]["action"] == "adopt_live_workers"
    assert (state / "last-wake.json").exists()
    second = _run_checker(spool, state, idle_gate)
    assert second["decision"] == "silent"
    assert second["reason"] == "respawn_cooldown"


def test_live_supervisor_fresh_status_no_wake(tmp_path, idle_gate):
    spool = _spool(tmp_path, status_age=10, marker_age=600,
                   workers=[("worker-1", 5)])
    state = tmp_path / "state"
    result = _run_checker(spool, state, idle_gate)
    assert result["decision"] == "silent"
    # Live 1 < target 2 with the gate idle: the pool is short, so the
    # first poll is a shortfall WATCH (silent), not a supervisor_alive
    # verdict — the shortfall has only just been seen.
    assert result["reason"] == "pool_shortfall_watch"
    assert not (state / "last-wake.json").exists()


def test_fresh_marker_covers_stale_status(tmp_path, idle_gate):
    # Old supervisor that never wrote the marker: stale status alone must
    # not trigger a respawn when the marker is fresh (and vice versa).
    # (No workers live, so the first poll records a shortfall watch —
    # still silent, still no wake.)
    spool = _spool(tmp_path, status_age=600, marker_age=20)
    result = _run_checker(spool, tmp_path / "state", idle_gate)
    assert result["decision"] == "silent"
    assert result["reason"] == "pool_shortfall_watch"


def test_dead_supervisor_with_live_workers_payload_says_adopt(
        tmp_path, idle_gate):
    spool = _spool(tmp_path, status_age=600, marker_age=600,
                   workers=[("worker-1", 5), ("worker-2", 8),
                            ("worker-3", 500)], pending=3)
    result = _run_checker(spool, tmp_path / "state", idle_gate, dry_run=True)
    assert result["decision"] == "wake"
    assert result["payload"]["live_workers"] == ["worker-1", "worker-2"]
    assert result["payload"]["pending"] == 3
    assert result["checks"]["live_workers"][0]["workerId"] == "worker-1"
    # worker-3 heartbeat 500s old (>90s) is dead, not adoptable.
    assert "worker-3" not in result["payload"]["live_workers"]


def test_wake_payload_carries_pending_actions_and_owner_flag(
        tmp_path, idle_gate):
    spool = _spool(tmp_path, status_age=600, marker_age=600,
                   workers=[("worker-1", 5)])
    now_ms = time.time() * 1000
    _write(os.path.join(spool, "supervisor-actions.json"), {
        "updatedAt": now_ms,
        "actions": [
            {"id": "respawn-worker-3-1", "action": "respawn",
             "workerId": "worker-3", "reason": "heartbeat stale",
             "createdAtMs": now_ms - 900 * 1000, "status": "pending"},
            {"id": "spawn-worker-4-1", "action": "spawn",
             "workerId": "worker-4", "reason": "backlog",
             "createdAtMs": now_ms - 60 * 1000, "status": "pending"},
            {"id": "done-1", "action": "spawn", "workerId": "worker-5",
             "reason": "old", "createdAtMs": now_ms - 60 * 1000,
             "status": "done"},
        ]})
    _write(os.path.join(spool, "supervisor-owner-unresponsive.json"), {
        "updatedAt": now_ms, "supervisorAgentId": "sup-old",
        "oldestPendingActionAgeS": 900, "pendingActions": 2})
    result = _run_checker(spool, tmp_path / "state", idle_gate, dry_run=True)
    assert result["decision"] == "wake"
    pending = result["payload"]["pending_actions"]
    assert [(a["action"], a["workerId"]) for a in pending] == [
        ("respawn", "worker-3"), ("spawn", "worker-4")]
    assert pending[0]["age_s"] > 800  # oldest first
    assert result["payload"]["owner_unresponsive"]["age_s"] == 900
    assert result["payload"]["owner_unresponsive"]["pending_actions"] == 2


def test_render_active_no_respawn(tmp_path, active_gate):
    spool = _spool(tmp_path, status_age=600, marker_age=600)
    result = _run_checker(spool, tmp_path / "state", active_gate)
    assert result["decision"] == "silent"
    assert result["reason"] == "render_active"
    assert result["checks"]["render_active"] is True


# --- scaling target reported by the live hook (pool_scaling rule) -------

def test_checks_target_media_floor_four(tmp_path, idle_gate):
    # Pending media work, gate idle: the floor is 4 (was 2).
    spool = _spool(tmp_path, status_age=600, marker_age=600, pending=3)
    result = _run_checker(spool, tmp_path / "state", idle_gate, dry_run=True)
    assert result["checks"]["pending"] == 3
    assert result["checks"]["target"] == 4


def test_checks_target_render_active_zero(tmp_path, active_gate):
    # Render ACTIVE: target is still 0 even with pending media work.
    spool = _spool(tmp_path, status_age=600, marker_age=600, pending=9)
    result = _run_checker(spool, tmp_path / "state", active_gate)
    assert result["checks"]["pending"] == 9
    assert result["checks"]["target"] == 0


def test_checks_target_idle_no_over_spawn(tmp_path, idle_gate):
    # Nothing pending, gate idle: 2 warm, not the media floor of 4.
    spool = _spool(tmp_path, status_age=600, marker_age=600, pending=0)
    result = _run_checker(spool, tmp_path / "state", idle_gate, dry_run=True)
    assert result["checks"]["pending"] == 0
    assert result["checks"]["target"] == 2


def test_checks_target_ceiling_eight(tmp_path, idle_gate):
    spool = _spool(tmp_path, status_age=600, marker_age=600, pending=100)
    result = _run_checker(spool, tmp_path / "state", idle_gate, dry_run=True)
    assert result["checks"]["target"] == 8


def test_parked_supervisor_no_respawn(tmp_path, idle_gate):
    spool = _spool(tmp_path, status_age=600, marker_age=600, parked=True)
    result = _run_checker(spool, tmp_path / "state", idle_gate)
    assert result["decision"] == "silent"
    assert result["reason"] == "supervisor_parked"


def test_missing_spool_wakes_fresh_vm_case(tmp_path, idle_gate):
    result = _run_checker(str(tmp_path / "no-such-spool"),
                          tmp_path / "state", idle_gate, dry_run=True)
    assert result["decision"] == "wake"
    assert result["reason"] == "supervisor_dead"
    assert result["payload"]["live_workers"] == []


def test_dry_run_wakes_but_writes_no_cooldown(tmp_path, idle_gate):
    spool = _spool(tmp_path, status_age=600, marker_age=600)
    state = tmp_path / "state"
    result = _run_checker(spool, state, idle_gate, dry_run=True)
    assert result["decision"] == "wake"
    assert not (state / "last-wake.json").exists()


# --- pool shortfall: fresh liveness, empty pool (2026-11-20 fix) --------

def test_shortfall_first_poll_silent_and_records_state(
        tmp_path, idle_gate):
    # Fresh liveness, 0 workers, gate idle: first sighting is a watch,
    # not a wake — a render may just have ended.
    spool = _spool(tmp_path, status_age=10, marker_age=10)
    state = tmp_path / "state"
    result = _run_checker(spool, state, idle_gate)
    assert result["decision"] == "silent"
    assert result["reason"] == "pool_shortfall_watch"
    assert result["checks"]["shortfall_s"] == 0
    assert (state / "pool-shortfall.json").exists()
    assert not (state / "last-wake.json").exists()


def test_shortfall_wakes_after_sustained_empty_pool(tmp_path, idle_gate):
    # Same pool, shortfall already 400s old: wake, with the pool-empty
    # reason and the spawn action (not the supervisor-respawn action).
    spool = _spool(tmp_path, status_age=10, marker_age=10)
    state = tmp_path / "state"
    state.mkdir()
    _write(state / "pool-shortfall.json",
           {"first_seen_at": time.time() - 400})
    result = _run_checker(spool, state, idle_gate)
    assert result["decision"] == "wake"
    assert result["reason"] == "pool_empty_idle"
    assert result["payload"]["live_workers"] == []
    assert result["payload"]["target"] == 2
    assert result["payload"]["action"] == "spawn_missing_workers"
    assert result["payload"]["shortfall_s"] >= 400
    # Cooldown suppresses the next wake.
    second = _run_checker(spool, state, idle_gate)
    assert second["decision"] == "silent"
    assert second["reason"] == "respawn_cooldown"


def test_shortfall_below_target_wakes(tmp_path, idle_gate):
    # 1 live worker against target 2, sustained: wake pool_below_target.
    spool = _spool(tmp_path, status_age=10, marker_age=10,
                   workers=[("worker-1", 5)])
    state = tmp_path / "state"
    state.mkdir()
    _write(state / "pool-shortfall.json",
           {"first_seen_at": time.time() - 301})
    result = _run_checker(spool, state, idle_gate)
    assert result["decision"] == "wake"
    assert result["reason"] == "pool_below_target"
    assert result["payload"]["live_workers"] == ["worker-1"]


def test_no_shortfall_when_at_target(tmp_path, idle_gate):
    spool = _spool(tmp_path, status_age=10, marker_age=10,
                   workers=[("worker-1", 5), ("worker-2", 8)])
    state = tmp_path / "state"
    result = _run_checker(spool, state, idle_gate)
    assert result["decision"] == "silent"
    assert result["reason"] == "supervisor_alive"
    assert not (state / "pool-shortfall.json").exists()


def test_shortfall_clears_when_pool_recovers(tmp_path, idle_gate):
    spool = _spool(tmp_path, status_age=10, marker_age=10,
                   workers=[("worker-1", 5), ("worker-2", 8)])
    state = tmp_path / "state"
    state.mkdir()
    _write(state / "pool-shortfall.json",
           {"first_seen_at": time.time() - 400})
    result = _run_checker(spool, state, idle_gate)
    assert result["decision"] == "silent"
    assert result["reason"] == "supervisor_alive"
    assert not (state / "pool-shortfall.json").exists()


def test_shortfall_silent_during_render(tmp_path, active_gate):
    # Render ACTIVE beats the shortfall: target is 0, never wake.
    spool = _spool(tmp_path, status_age=10, marker_age=10)
    state = tmp_path / "state"
    state.mkdir()
    _write(state / "pool-shortfall.json",
           {"first_seen_at": time.time() - 400})
    result = _run_checker(spool, state, active_gate)
    assert result["decision"] == "silent"
    assert result["reason"] == "render_active"


def test_wrapper_wakes_on_sustained_empty_pool(tmp_path, idle_gate):
    spool = _spool(tmp_path, status_age=10, marker_age=10)
    state = tmp_path / "state"
    state.mkdir()
    _write(state / "pool-shortfall.json",
           {"first_seen_at": time.time() - 400})
    calls = _run_wrapper(spool, state, idle_gate, tmp_path)
    kinds = [c[0] for c in calls]
    assert "WAKE" in kinds
    assert "SILENT" not in kinds
    wake = next(c for c in calls if c[0] == "WAKE")
    payload = json.loads(wake[2])
    assert payload["action"] == "spawn_missing_workers"


def test_unprovable_render_gate_fails_open(tmp_path):
    gate = str(tmp_path / "gate-broken.sh")
    _stub_gate(gate, 1)  # neither 0 (idle) nor 2 (active)
    spool = _spool(tmp_path, status_age=600, marker_age=600)
    result = _run_checker(spool, tmp_path / "state", gate, dry_run=True)
    assert result["decision"] == "wake"
    assert result["checks"]["render_gate_note"] is not None


# --- bash wrapper: fake hook runtime, real script ------------------------

def _fake_runtime(path):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            "#!/usr/bin/env bash\n"
            "CALLS_FILE=\"${HOOK_CALLS_FILE:-/dev/null}\"\n"
            "wake() { echo \"WAKE|$1|$2\" >> \"$CALLS_FILE\"; }\n"
            "silent() { echo \"SILENT|$1|$2\" >> \"$CALLS_FILE\"; }\n"
            "log() { echo \"LOG|$1|$2\" >> \"$CALLS_FILE\"; }\n")
    os.chmod(path, 0o755)


def _run_wrapper(spool, state_dir, gate, tmp_path):
    calls = tmp_path / "calls.txt"
    runtime = tmp_path / "fake-runtime.sh"
    _fake_runtime(str(runtime))
    # The wrapper resolves the checker next to itself (repo copy), so
    # point it at a scratch copy dir holding both files.
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name in ("pool-supervisor-watch.sh", "pool_supervisor_check.py",
                 "pool_scaling.py"):
        with open(os.path.join(HERE, name), "rb") as src:
            with open(bindir / name, "wb") as dst:
                dst.write(src.read())
    os.chmod(bindir / "pool-supervisor-watch.sh", 0o755)
    env = dict(os.environ)
    env.update({
        "HATCH_HOOK_RUNTIME": str(runtime),
        "HOOK_CALLS_FILE": str(calls),
        "MUSE_INFERENCE_SPOOL_DIR": spool,
        "POOL_WATCH_STATE_DIR": str(state_dir),
        "POOL_WATCH_RENDER_GATE": gate,
        "HATCH_HOOK_DRY_RUN": "0",
    })
    proc = subprocess.run(["bash", str(bindir / "pool-supervisor-watch.sh")],
                          capture_output=True, text=True, env=env, timeout=30)
    assert proc.returncode == 0, proc.stderr
    lines = calls.read_text().splitlines() if calls.exists() else []
    return [line.split("|", 2) for line in lines]


def test_wrapper_wakes_on_dead_supervisor(tmp_path, idle_gate):
    spool = _spool(tmp_path, status_age=600, marker_age=600,
                   workers=[("worker-1", 5)])
    calls = _run_wrapper(spool, tmp_path / "state", idle_gate, tmp_path)
    kinds = [c[0] for c in calls]
    assert "WAKE" in kinds
    assert "SILENT" not in kinds
    wake = next(c for c in calls if c[0] == "WAKE")
    payload = json.loads(wake[2])
    assert payload["live_workers"] == ["worker-1"]
    assert payload["action"] == "adopt_live_workers"


def test_wrapper_stays_silent_on_live_supervisor(tmp_path, idle_gate):
    spool = _spool(tmp_path, status_age=10, marker_age=10)
    calls = _run_wrapper(spool, tmp_path / "state", idle_gate, tmp_path)
    kinds = [c[0] for c in calls]
    assert "WAKE" not in kinds
    assert "SILENT" in kinds


def test_wrapper_stays_silent_during_render(tmp_path, active_gate):
    spool = _spool(tmp_path, status_age=600, marker_age=600)
    calls = _run_wrapper(spool, tmp_path / "state", active_gate, tmp_path)
    kinds = [c[0] for c in calls]
    assert "WAKE" not in kinds
    assert "SILENT" in kinds


# --- scale-up signal fast wake (2026-11-21) ------------------------------


def _signal(spool, age_s=5, spawns=("worker-3", "worker-4"),
            blocked=()):
    _write(os.path.join(spool, "supervisor-scaleup-needed.json"),
           {"updatedAt": time.time() * 1000 - age_s * 1000,
            "supervisorAgentId": "test-supervisor",
            "live": 2, "target": 4, "pending": 6,
            "spawns": [{"action": "spawn", "actionId": "spawn-%s-1" % w,
                        "workerId": w, "reason": "live=2 target=4 pending=6"}
                       for w in spawns],
            "blocked": list(blocked)})


def test_scaleup_signal_wakes_immediately(tmp_path, idle_gate):
    # Fresh signal + live 2 / target 4 + gate idle: wake at once with
    # reason pool_scaleup_needed — no 300s shortfall wait, and no
    # shortfall state file is even started.
    spool = _spool(tmp_path, status_age=10, marker_age=10,
                   workers=[("worker-1", 5), ("worker-2", 8)], pending=6)
    _signal(spool)
    state = tmp_path / "state"
    result = _run_checker(spool, state, idle_gate)
    assert result["decision"] == "wake"
    assert result["reason"] == "pool_scaleup_needed"
    assert result["payload"]["action"] == "spawn_missing_workers"
    assert [s["workerId"] for s in
            result["payload"]["scaleup_spawns"]] == ["worker-3", "worker-4"]
    assert result["payload"]["live_workers"] == ["worker-1", "worker-2"]
    assert result["payload"]["target"] == 4
    assert not (state / "pool-shortfall.json").exists()
    # Cooldown suppresses the next wake.
    second = _run_checker(spool, state, idle_gate)
    assert second["decision"] == "silent"
    assert second["reason"] == "respawn_cooldown"


def test_stale_scaleup_signal_does_not_wake_fast(tmp_path, idle_gate):
    # Signal 600s old: ignored — the first poll is a shortfall watch,
    # not a wake (the 300s shortfall path is the fallback).
    spool = _spool(tmp_path, status_age=10, marker_age=10,
                   workers=[("worker-1", 5), ("worker-2", 8)], pending=6)
    _signal(spool, age_s=600)
    state = tmp_path / "state"
    result = _run_checker(spool, state, idle_gate)
    assert result["decision"] == "silent"
    assert result["reason"] == "pool_shortfall_watch"
    assert result["checks"]["scaleup_signal"] is None


def test_scaleup_signal_no_wake_when_at_target(tmp_path, idle_gate):
    # Fresh signal, but the pool already recovered to target: silent.
    spool = _spool(tmp_path, status_age=10, marker_age=10,
                   workers=[("worker-1", 5), ("worker-2", 8),
                            ("worker-3", 5), ("worker-4", 8)], pending=6)
    _signal(spool)
    result = _run_checker(spool, tmp_path / "state", idle_gate)
    assert result["decision"] == "silent"
    assert result["reason"] == "supervisor_alive"


def test_scaleup_signal_no_wake_during_render(tmp_path, active_gate):
    # Fresh signal, but a render is ACTIVE: never wake (target 0).
    spool = _spool(tmp_path, status_age=10, marker_age=10,
                   workers=[("worker-1", 5), ("worker-2", 8)], pending=6)
    _signal(spool)
    result = _run_checker(spool, tmp_path / "state", active_gate)
    assert result["decision"] == "silent"
    assert result["reason"] == "render_active"


def test_scaleup_signal_blocked_shortfall_still_wakes(tmp_path, idle_gate):
    # Signal with NO spawns but a blocked reason (every free slot held
    # / sighted): the pool is short and the parent must be told anyway.
    spool = _spool(tmp_path, status_age=10, marker_age=10,
                   workers=[("worker-1", 5), ("worker-2", 8)], pending=6)
    _signal(spool, spawns=(), blocked=["held slots (3 deaths): worker-3"])
    result = _run_checker(spool, tmp_path / "state", idle_gate)
    assert result["decision"] == "wake"
    assert result["reason"] == "pool_scaleup_needed"
    assert result["payload"]["scaleup_spawns"] == []
    assert result["payload"]["scaleup_blocked"] == [
        "held slots (3 deaths): worker-3"]
