"""The driver: a light in-process loop on the debugger's 120s tick that
owns each job until it completes.

It resumes system-killed jobs from their checkpoints (never a job the user
cancelled), advances stuck scenes, and escalates — loudly, on the job
record and in the server log — when a job cannot be recovered. Safe
recovery actions are not capped; only server restarts are.
"""

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services import queue_manager as queue_module
from services.job_debugger import DebuggerConfig, JobDebugger
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


def _seed(manager, *, status, checkpoint=None, **fields):
    added = manager.add_item({"script": "A grounded scene.", "title": "Seed"})
    item = manager.get_item(added["id"])
    item.update(status=status, progress=50,
                progress_text="Preparing media 7/14", progress_seq=5)
    if checkpoint is not None:
        item["checkpoint_dir"] = str(checkpoint)
    item.update(fields)
    manager._save_queue()
    return item


def _wait_for_status(manager, job_id, status, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if manager.get_item(job_id)["status"] == status:
            return True
        time.sleep(0.1)
    return False


def _run_stub_worker(manager, tmp_path):
    done = threading.Event()

    def stub(config, progress_cb, check_cancelled, cancellation=None):
        done.set()
        return {"video_path": str(tmp_path / "master.mp4")}

    manager.start_worker(stub)
    assert done.wait(15.0), "worker never picked the job up"


def test_driver_recovers_watchdog_cancelled_job_to_completion(driver_env):
    """The Wright case: cancelled by the deadline watchdog at 64%, resume
    refused it. The driver recovers it from its checkpoint and it runs to
    completed."""
    manager, debugger, _make, tmp_path = driver_env
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    item = _seed(manager, status="cancelled", checkpoint=ckpt,
                 progress_text="Cancelled", progress=64)

    taken = debugger.tick(now=1000.0)

    assert any(a.get("kind") == "resume" and a.get("job_id") == item["id"]
               for a in taken)
    assert manager.get_item(item["id"])["status"] == "queued"
    _run_stub_worker(manager, tmp_path)
    assert _wait_for_status(manager, item["id"], "completed")
    assert manager.get_item(item["id"])["progress"] == 100


def test_driver_resumes_reboot_interrupted_job_from_checkpoint(driver_env):
    """Simulated reboot: the job was processing when the machine died.
    With auto-requeue off the restart parks it PAUSED; the driver resumes
    it and it completes."""
    manager, debugger, make_debugger, tmp_path = driver_env
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    item = _seed(manager, status="processing", checkpoint=ckpt)

    QueueManager._instance = None
    rebooted = QueueManager()
    queue_module.queue_manager = rebooted
    parked = rebooted.get_item(item["id"])
    assert parked["status"] == "paused"
    assert parked["paused_reason"] == "backend_restart"

    reboot_debugger = make_debugger(rebooted)
    taken = reboot_debugger.tick(now=2000.0)
    assert any(a.get("kind") == "resume" for a in taken)
    assert rebooted.get_item(item["id"])["status"] == "queued"
    _run_stub_worker(rebooted, tmp_path)
    assert _wait_for_status(rebooted, item["id"], "completed")
    rebooted.stop_worker()


def test_driver_advances_stuck_scene_without_action_cap(driver_env):
    """A scene stuck 21 minutes gets advanced from on-disk media — even
    though 3 actions were already recorded this hour (the old cap would
    have blocked any further action)."""
    manager, debugger, _make, tmp_path = driver_env
    item = _seed(manager, status="processing",
                 progress_text="Preparing media 3/14")
    debugger.state["actions"][item["id"]] = [
        {"action": "advance_scene", "reason": "x", "at": 1000.0 - 600},
        {"action": "advance_scene", "reason": "x", "at": 1000.0 - 300},
        {"action": "rewake", "reason": "x", "at": 1000.0 - 60},
    ]

    debugger.tick(now=1000.0)  # observes the scene
    taken = debugger.tick(now=1000.0 + 21 * 60)

    assert debugger.actions.advances, taken
    assert debugger.actions.advances[0][1] == 3


def test_driver_leaves_user_cancelled_alone_and_escalates_unrecoverable(driver_env):
    manager, debugger, _make, tmp_path = driver_env
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    user = _seed(manager, status="cancelled", checkpoint=ckpt,
                 cancel_requested=True, progress_text="Cancelled")
    hard = _seed(manager, status="error", attempt=1,
                 error="Media contract failed: no media could be resolved")
    unknown_pause = _seed(manager, status="paused",
                          paused_reason="operator_hold")

    taken = debugger.tick(now=3000.0)

    assert manager.get_item(user["id"])["status"] == "cancelled"
    assert "needs_human" not in manager.get_item(user["id"])
    hard_item = manager.get_item(hard["id"])
    assert hard_item.get("needs_human") is True
    assert "needs a human" in hard_item["progress_text"]
    assert any(a.get("kind") == "escalate" and a.get("job_id") == hard["id"]
               for a in taken)
    assert manager.get_item(unknown_pause["id"])["status"] == "paused"
    assert "needs_human" not in manager.get_item(unknown_pause["id"])
    # A second tick never re-processes the escalated job.
    taken2 = debugger.tick(now=3000.0 + 120)
    assert not [a for a in taken2 if a.get("job_id") == hard["id"]]


def test_driver_tick_is_bounded(driver_env):
    """One cheap sweep per tick: at most one action per job, no spinning."""
    manager, debugger, _make, tmp_path = driver_env
    for _ in range(3):
        _seed(manager, status="paused", paused_reason="delivery_deadline")

    start = time.monotonic()
    taken = debugger.tick(now=4000.0)
    elapsed = time.monotonic() - start

    assert elapsed < 5.0
    assert len([a for a in taken if a.get("kind") == "resume"]) == 3
    # The next tick has nothing left to do: resumed jobs are queued now.
    assert debugger.tick(now=4000.0 + 120) == []
    assert debugger.status()["driver"]["resumed"] == 3


def test_driver_keeps_restart_cap_for_destructive_actions(driver_env):
    """Safe actions are uncapped, but a server restart is still capped at
    3/hour: a dead worker with 3 recent actions must NOT be restarted."""
    manager, debugger, _make, tmp_path = driver_env
    item = _seed(manager, status="processing",
                 progress_text="Preparing media 3/14")
    debugger.state["actions"][item["id"]] = [
        {"action": "restart_server", "reason": "x", "at": 5000.0 - 600},
        {"action": "restart_server", "reason": "x", "at": 5000.0 - 300},
        {"action": "restart_server", "reason": "x", "at": 5000.0 - 60},
    ]

    debugger.tick(now=5000.0)  # observes progress + heartbeat seq
    taken = debugger.tick(now=5000.0 + 16 * 60)  # heartbeat seq frozen 16 min

    assert debugger.actions.restarts == []
    assert not any(a.get("kind") == "restart_server" for a in taken)
