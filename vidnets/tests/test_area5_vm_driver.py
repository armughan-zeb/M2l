"""Area 5 — VM / driver issues: render-failed pauses, resume bounds, the
user-cancel guardrail, flagged-job recovery, and worker startup.

Replays the real GTA VI case (job ef306926): paused at 78% with
paused_reason='remotion_render', checkpoints intact and
render_failure.json retryable:true — a job the driver used to strand
forever because 'remotion_render' was not in the system-recovery set.
Also replays the Wright watchdog-cancel path with the new streak bound,
and the user-cancelled job whose cleanup timed out (shutdown_timeout)
being re-parked as a retryable pause on reboot.
"""

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services import queue_manager as queue_module
from services.job_debugger import (
    DRIVER_RESUME_STREAK_LIMIT,
    DebuggerConfig,
    JobDebugger,
)
from services.queue_manager import QueueManager


class FakeActions:
    """Test seam for the debugger's action hooks."""

    def __init__(self):
        self.advances = []
        self.rewakes = []
        self.kills = []
        self.restarts = []

    def force_rewake(self, stale):
        self.rewakes.append(list(stale or []))
        return {"ok": True, "rewoken": len(stale or [])}

    def request_scene_advance(self, job_id, scene_number, reason=""):
        self.advances.append((job_id, int(scene_number), reason))
        return {"ok": True, "advanced": True}

    def kill_hung_child(self, pid):
        self.kills.append(int(pid))
        return {"ok": True}

    def restart_server(self, reason=""):
        self.restarts.append(reason)
        return {"ok": True}


@pytest.fixture()
def driver_env(tmp_path, monkeypatch):
    QueueManager._instance = None
    queue_module.queue_manager = None
    original = queue_module.QUEUE_FILE
    queue_module.QUEUE_FILE = tmp_path / "queue.json"
    monkeypatch.setenv("VIDZEO_AUTO_REQUEUE_INTERRUPTED", "0")
    monkeypatch.setenv("VIDZEO_QUEUE_PREP_WORKERS", "1")
    manager = QueueManager()
    queue_module.queue_manager = manager

    def _debugger(mgr):
        return JobDebugger(
            DebuggerConfig(env={}),
            queue_path=tmp_path / "queue.json",
            state_path=tmp_path / "debugger-state.json",
            bridge=tmp_path / "bridge",
            log_path=tmp_path / "server.log",
            seen_path=tmp_path / "hook-seen.json",
            state={"observations": {}, "actions": {}, "restarts": {},
                   "flagged": {}, "driver": {"actions": []}},
            actions=FakeActions(),
            manager=mgr,
        )

    debugger = _debugger(manager)
    yield manager, debugger, _debugger, tmp_path
    for mgr in (manager, queue_module.queue_manager):
        try:
            if mgr is not None:
                mgr.stop_worker()
        except Exception:
            pass
    QueueManager._instance = None
    queue_module.queue_manager = None
    queue_module.QUEUE_FILE = original


@pytest.fixture()
def queue_env(tmp_path, monkeypatch):
    QueueManager._instance = None
    queue_module.queue_manager = None
    original = queue_module.QUEUE_FILE
    queue_module.QUEUE_FILE = tmp_path / "queue.json"
    monkeypatch.setenv("VIDZEO_AUTO_REQUEUE_INTERRUPTED", "0")
    monkeypatch.setenv("VIDZEO_QUEUE_PREP_WORKERS", "1")
    manager = QueueManager()
    queue_module.queue_manager = manager
    yield manager, tmp_path
    try:
        manager.stop_worker()
    except Exception:
        pass
    QueueManager._instance = None
    queue_module.queue_manager = None
    queue_module.QUEUE_FILE = original


def _seed(manager, *, status, checkpoint=None, progress=78,
          paused_reason=None, cancel_requested=False, attempt=2,
          error=None, progress_text="seeded"):
    added = manager.add_item({"script": "A grounded scene.", "title": "Seed"})
    item = manager.get_item(added["id"])
    item.update(status=status, attempt=attempt, progress=progress,
                progress_text=progress_text, progress_seq=5)
    if checkpoint is not None:
        item["checkpoint_dir"] = str(checkpoint)
    if paused_reason is not None:
        item["paused_reason"] = paused_reason
        item["retryable"] = True
    if cancel_requested:
        item["cancel_requested"] = True
    if error is not None:
        item["error"] = error
    manager._save_queue()
    return item


def _repause(manager, job_id, *, reason, progress):
    """Put a resumed job back into the paused state the worker loop would
    leave it in after the same stage fails again."""
    item = manager.get_item(job_id)
    item.update(status="paused", paused_reason=reason, progress=progress,
                progress_text="Remotion render or final check failed. "
                "Click Retry to use the saved narration, media, and edit plan.",
                retryable=True)
    manager._save_queue()
    return item


def _run_stub_worker(manager, tmp_path):
    done = threading.Event()

    def stub(config, progress_cb, check_cancelled, cancellation=None):
        done.set()
        return {"video_path": str(tmp_path / "master.mp4")}

    manager.start_worker(stub)
    assert done.wait(15.0), "worker never picked the job up"


def _wait_for_status(manager, job_id, status, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if manager.get_item(job_id)["status"] == status:
            return True
        time.sleep(0.1)
    return False


# ---------------------------------------------------------------------------
# The GTA VI case: a render-failed PAUSED job must be recovered
# ---------------------------------------------------------------------------

def test_driver_recovers_render_failed_paused_job(driver_env):
    """ef306926 replay: paused at 78% with paused_reason='remotion_render',
    checkpoints intact (edit plan, media plan, render_failure.json
    retryable:true), attempt 2. Before the fix the driver skipped it —
    'remotion_render' was not a system-recovery reason — and it sat
    stranded until a human clicked Retry. Now the driver resumes it and
    it runs to completed."""
    manager, debugger, _make, tmp_path = driver_env
    ckpt = tmp_path / "ckpt"
    (ckpt / "checkpoints").mkdir(parents=True)
    (ckpt / "checkpoints" / "render_failure.json").write_text(
        '{"kind": "render_failed", "retryable": true}', encoding="utf-8")
    (ckpt / "checkpoints" / "pre_render.json").write_text(
        '{"stage": "ready_for_render"}', encoding="utf-8")
    item = _seed(manager, status="paused", checkpoint=ckpt / "checkpoints",
                 progress=78, paused_reason="remotion_render", attempt=2,
                 progress_text="Remotion render or final check failed. "
                 "Click Retry to use the saved narration, media, and edit plan.")

    taken = debugger.tick(now=1000.0)

    assert any(a.get("kind") == "resume" and a.get("job_id") == item["id"]
               for a in taken)
    assert manager.get_item(item["id"])["status"] == "queued"
    _run_stub_worker(manager, tmp_path)
    assert _wait_for_status(manager, item["id"], "completed")
    assert manager.get_item(item["id"])["progress"] == 100


@pytest.mark.parametrize("reason", [
    "low_memory",
    "openrouter_scene_plan",
    "remotion_edit_plan",
    "media_contract",
    "remotion_render",
])
def test_driver_recovers_each_retryable_pause_reason(driver_env, reason):
    """Every retryable worker-loop pause is system-caused and
    checkpointed: the driver must resume all five, not just the
    backend-restart / deadline set."""
    manager, debugger, _make, tmp_path = driver_env
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    item = _seed(manager, status="paused", checkpoint=ckpt,
                 paused_reason=reason, attempt=1)

    taken = debugger.tick(now=1000.0)

    assert any(a.get("kind") == "resume" and a.get("job_id") == item["id"]
               for a in taken)
    assert manager.get_item(item["id"])["status"] == "queued"


def test_driver_leaves_unknown_pause_reason_alone(driver_env):
    """The allowlist still means something: a paused job with an
    unrecognised reason (possibly parked deliberately) is not
    auto-resumed."""
    manager, debugger, _make, tmp_path = driver_env
    item = _seed(manager, status="paused", paused_reason="operator_hold",
                 attempt=1)

    taken = debugger.tick(now=1000.0)

    assert manager.get_item(item["id"])["status"] == "paused"
    assert "needs_human" not in manager.get_item(item["id"])
    assert not [a for a in taken if a.get("job_id") == item["id"]]


# ---------------------------------------------------------------------------
# The streak bound: a deterministically failing stage must not loop
# ---------------------------------------------------------------------------

def test_driver_stops_after_three_same_point_resumes(driver_env):
    """A render that fails identically at 78% every time: the driver
    resumes it three times, then escalates (needs_human) instead of
    looping resume -> fail -> resume forever. Manual Retry stays
    available — escalation never kills the video."""
    manager, debugger, _make, tmp_path = driver_env
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    item = _seed(manager, status="paused", checkpoint=ckpt, progress=78,
                 paused_reason="remotion_render", attempt=2)
    job_id = item["id"]

    resumes = 0
    for cycle in range(4):
        taken = debugger.tick(now=1000.0 + cycle * 120)
        resumed = [a for a in taken
                   if a.get("kind") == "resume" and a.get("job_id") == job_id]
        escalated = [a for a in taken
                     if a.get("kind") == "escalate" and a.get("job_id") == job_id]
        if cycle < 3:
            assert len(resumed) == 1, f"cycle {cycle}: expected a resume"
            resumes += 1
            assert not escalated
            # The retry fails the same way, at the same progress.
            _repause(manager, job_id, reason="remotion_render", progress=78)
        else:
            assert not resumed
            assert len(escalated) == 1
            assert manager.get_item(job_id).get("needs_human") is True
            assert "manual Retry" in manager.get_item(job_id)["progress_text"]

    assert resumes == DRIVER_RESUME_STREAK_LIMIT == 3


def test_driver_escalates_when_reasons_alternate_at_same_progress(driver_env):
    """The GTA VI attempts 18-23 loop (Nov 16, 2026): the job stranded
    at 78% alternating low_memory -> remotion_render -> low_memory ...,
    and each reason change reset the old per-reason streak, so the
    driver retried a doomed attempt unboundedly. The streak is keyed on
    the job's progress, not its pause reason: alternating reasons
    without progress still escalate after three resumes."""
    manager, debugger, _make, tmp_path = driver_env
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    item = _seed(manager, status="paused", checkpoint=ckpt, progress=78,
                 paused_reason="low_memory", attempt=2)
    job_id = item["id"]

    reasons = ["low_memory", "remotion_render", "low_memory", "remotion_render"]
    resumes = 0
    for cycle in range(4):
        taken = debugger.tick(now=1000.0 + cycle * 120)
        resumed = [a for a in taken
                   if a.get("kind") == "resume" and a.get("job_id") == job_id]
        escalated = [a for a in taken
                     if a.get("kind") == "escalate" and a.get("job_id") == job_id]
        if cycle < 3:
            assert len(resumed) == 1, f"cycle {cycle}: expected a resume"
            resumes += 1
            assert not escalated
            # The retry strands again at 78%, with the other reason.
            _repause(manager, job_id, reason=reasons[cycle + 1], progress=78)
        else:
            assert not resumed
            assert len(escalated) == 1
            assert manager.get_item(job_id).get("needs_human") is True
            assert "manual Retry" in manager.get_item(job_id)["progress_text"]

    assert resumes == DRIVER_RESUME_STREAK_LIMIT == 3
    streak = debugger.state["driver"]["resume_streaks"][job_id]
    assert set(streak["reasons"]) == {"low_memory", "remotion_render"}
    # A later tick never re-processes the escalated job.
    assert not [a for a in debugger.tick(now=1000.0 + 4 * 120)
                if a.get("job_id") == job_id]


def test_driver_resume_streak_resets_when_job_progresses(driver_env):
    """The streak is keyed on stuckness, not total runs: a job that gets
    FURTHER (78% -> 85%) before failing again is progressing, so the
    driver resumes it again instead of capping it."""
    manager, debugger, _make, tmp_path = driver_env
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    item = _seed(manager, status="paused", checkpoint=ckpt, progress=78,
                 paused_reason="remotion_render", attempt=2)
    job_id = item["id"]

    assert [a for a in debugger.tick(now=1000.0)
            if a.get("kind") == "resume"]  # streak 1 at 78%
    _repause(manager, job_id, reason="remotion_render", progress=85)

    taken = debugger.tick(now=1120.0)
    assert any(a.get("kind") == "resume" and a.get("job_id") == job_id
               for a in taken)
    streak = debugger.state["driver"]["resume_streaks"][job_id]
    assert streak["count"] == 1
    assert streak["progress"] == 85


def test_driver_escalates_when_job_creeps_one_point_per_attempt(driver_env):
    """The GTA VI low-memory loop (Nov 16, 2026): the job waited on
    memory, failed, and re-paused 1 point further each time (73% -> 74%
    -> 75%), which used to reset the streak forever because progress
    changed. A creep of up to 5 points still counts as stranded, so the
    driver escalates after three resumes instead of looping."""
    manager, debugger, _make, tmp_path = driver_env
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    item = _seed(manager, status="paused", checkpoint=ckpt, progress=73,
                 paused_reason="low_memory", attempt=2)
    job_id = item["id"]

    resumes = 0
    for cycle in range(4):
        taken = debugger.tick(now=1000.0 + cycle * 120)
        resumed = [a for a in taken
                   if a.get("kind") == "resume" and a.get("job_id") == job_id]
        escalated = [a for a in taken
                     if a.get("kind") == "escalate" and a.get("job_id") == job_id]
        if cycle < 3:
            assert len(resumed) == 1, f"cycle {cycle}: expected a resume"
            resumes += 1
            assert not escalated
            # The retry waits on memory again, 1 point further along.
            _repause(manager, job_id, reason="low_memory", progress=73 + cycle + 1)
        else:
            assert not resumed
            assert len(escalated) == 1
            assert manager.get_item(job_id).get("needs_human") is True
            assert "manual Retry" in manager.get_item(job_id)["progress_text"]

    assert resumes == DRIVER_RESUME_STREAK_LIMIT == 3


def test_driver_escalates_repeatedly_system_cancelled_job(driver_env):
    """The Wright path, bounded: a system-cancelled job that stalls at
    the same progress (64%) three times is escalated, not looped on a
    fresh budget forever."""
    manager, debugger, _make, tmp_path = driver_env
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    item = _seed(manager, status="cancelled", checkpoint=ckpt, progress=64,
                 attempt=2, progress_text="Cancelled")
    job_id = item["id"]

    for cycle in range(4):
        taken = debugger.tick(now=1000.0 + cycle * 120)
        resumed = [a for a in taken
                   if a.get("kind") == "resume" and a.get("job_id") == job_id]
        escalated = [a for a in taken
                     if a.get("kind") == "escalate" and a.get("job_id") == job_id]
        if cycle < 3:
            assert len(resumed) == 1, f"cycle {cycle}: expected a resume"
            assert not escalated
            reparked = manager.get_item(job_id)
            reparked.update(status="cancelled", progress=64,
                            progress_text="Cancelled")
            manager._save_queue()
        else:
            assert not resumed
            assert len(escalated) == 1
            assert manager.get_item(job_id).get("needs_human") is True


# ---------------------------------------------------------------------------
# The user-cancel guardrail, including the shutdown-timeout hole
# ---------------------------------------------------------------------------

def test_driver_never_resumes_user_cancelled_paused_job(driver_env):
    """A user-cancelled job re-parked PAUSED (cancellation_cleanup_timeout)
    after its cleanup timed out and the machine rebooted: the driver
    must leave it alone — cancel_requested is never touched."""
    manager, debugger, _make, tmp_path = driver_env
    item = _seed(manager, status="paused",
                 paused_reason="cancellation_cleanup_timeout",
                 cancel_requested=True, attempt=1)

    taken = debugger.tick(now=1000.0)

    assert manager.get_item(item["id"])["status"] == "paused"
    assert "needs_human" not in manager.get_item(item["id"])
    assert not [a for a in taken if a.get("job_id") == item["id"]]


def test_resume_item_refuses_user_cancelled_job_in_any_status(queue_env):
    """resume_item is the choke point every path funnels through (manual
    /api/queue/resume, startup maintenance, the driver): a
    cancel_requested job is refused in PAUSED, ERROR and
    SHUTDOWN_TIMEOUT alike, not just CANCELLED."""
    manager, tmp_path = queue_env
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    paused = _seed(manager, status="paused",
                   paused_reason="cancellation_cleanup_timeout",
                   cancel_requested=True)
    errored = _seed(manager, status="error", cancel_requested=True,
                    error="Connection reset by peer")
    timed_out = _seed(manager, status="shutdown_timeout",
                      cancel_requested=True)
    # And a system-paused job with no flag is still resumable.
    system_paused = _seed(manager, status="paused",
                          paused_reason="remotion_render")

    assert manager.resume_item(paused["id"]) is False
    assert manager.get_item(paused["id"])["status"] == "paused"
    assert manager.resume_item(errored["id"]) is False
    assert manager.get_item(errored["id"])["status"] == "error"
    assert manager.resume_item(timed_out["id"]) is False
    assert manager.get_item(timed_out["id"])["status"] == "shutdown_timeout"
    assert manager.resume_item(system_paused["id"]) is True
    assert manager.get_item(system_paused["id"])["status"] == "queued"


def test_reboot_finalizes_user_cancelled_shutdown_timeout(queue_env, monkeypatch):
    """Reboot recovery: a SHUTDOWN_TIMEOUT job the USER cancelled is
    finalized CANCELLED, never re-parked PAUSED as a retryable
    interruption. A system-caused timeout still becomes a resumable
    pause."""
    manager, tmp_path = queue_env
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    user = _seed(manager, status="shutdown_timeout", cancel_requested=True)
    system = _seed(manager, status="shutdown_timeout")

    QueueManager._instance = None
    restarted = QueueManager()
    queue_module.queue_manager = restarted

    user_item = restarted.get_item(user["id"])
    assert user_item["status"] == "cancelled"
    assert user_item["progress_text"] == "Cancelled after backend restart"
    system_item = restarted.get_item(system["id"])
    assert system_item["status"] == "paused"
    assert system_item["paused_reason"] == "cancellation_cleanup_timeout"
    restarted.stop_worker()


# ---------------------------------------------------------------------------
# Flagged processing jobs: recovery must clear the flag, death escalates
# ---------------------------------------------------------------------------

def test_driver_clears_flag_when_flagged_job_recovers(driver_env):
    """A job flagged for the restart cap whose worker then recovers and
    makes progress must NOT be escalated: the flag clears and the job
    keeps running."""
    manager, debugger, _make, tmp_path = driver_env
    item = _seed(manager, status="processing",
                 progress_text="Preparing media 7/14", progress=50)
    job_id = item["id"]

    debugger.tick(now=1000.0)  # baseline observation
    debugger.state.setdefault("flagged", {})[job_id] = {
        "reason": "debugger restarted 3 times; left alone", "at": 1000.0,
    }
    # The worker recovers: progress advances.
    live = manager.get_item(job_id)
    live.update(progress=51, progress_text="Preparing media 8/14",
                progress_seq=6)
    manager._save_queue()

    taken = debugger.tick(now=2000.0)

    assert job_id not in (debugger.state.get("flagged") or {})
    assert "needs_human" not in manager.get_item(job_id)
    assert not [a for a in taken
                if a.get("kind") == "escalate" and a.get("job_id") == job_id]


def test_driver_escalates_flagged_job_that_stays_dead(driver_env):
    """The other side: a flagged job with no progress change for the full
    dead-worker window is still escalated — the stall-aware check must
    not swallow genuine deaths."""
    manager, debugger, _make, tmp_path = driver_env
    item = _seed(manager, status="processing",
                 progress_text="Preparing media 7/14", progress=50)
    job_id = item["id"]

    debugger.tick(now=1000.0)  # baseline observation
    debugger.state.setdefault("flagged", {})[job_id] = {
        "reason": "debugger restarted 3 times; left alone", "at": 1000.0,
    }

    taken = debugger.tick(now=1000.0 + 16 * 60)  # 16 min, no movement

    assert any(a.get("kind") == "escalate" and a.get("job_id") == job_id
               for a in taken)
    assert manager.get_item(job_id).get("needs_human") is True


# ---------------------------------------------------------------------------
# Startup: a driver-resumed QUEUED job needs a running worker
# ---------------------------------------------------------------------------

def test_startup_maintenance_starts_worker_for_queued_jobs(
        tmp_path, monkeypatch):
    """After a reboot the server starts with no worker (nothing sets
    VIDZEO_AUTO_REQUEUE_INTERRUPTED in the stock start path). A QUEUED
    job — e.g. one the driver just resumed — would sit stranded until
    the next /queue/add. run_startup_maintenance must start the worker
    whenever accepted work is already waiting, even with auto-requeue
    off. This changes no resume policy: with nothing queued, the worker
    is not started."""
    import app as app_module

    monkeypatch.setenv("VIDZEO_AUTO_REQUEUE_INTERRUPTED", "0")
    monkeypatch.delenv("VIDZEO_AGENT_RECOVER_QUEUE", raising=False)
    started = []

    class FakeManager:
        def __init__(self, items):
            self.queue = items
            self.worker_running = False

        def start_worker(self, callback):
            started.append(callback)
            self.worker_running = True
            return True

    queued = FakeManager([{"id": "job1", "status": "queued"}])
    monkeypatch.setattr(app_module, "get_queue_manager", lambda: queued)
    app_module.run_startup_maintenance()
    assert len(started) == 1

    started.clear()
    idle = FakeManager([{"id": "job2", "status": "paused",
                         "paused_reason": "remotion_render"}])
    monkeypatch.setattr(app_module, "get_queue_manager", lambda: idle)
    app_module.run_startup_maintenance()
    assert started == []
