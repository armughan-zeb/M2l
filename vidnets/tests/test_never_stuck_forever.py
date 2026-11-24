"""Never stuck forever: reboot / network / deadline recovery.

A job the system killed must auto-resume from its checkpoint; a job the
user cancelled must never be touched. These tests replay the Wright
Brothers case (job 9606ea41): the delivery-deadline watchdog cancelled it
at 64% and resume refused it, because the queue marked it CANCELLED with
no resumable pause.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services import queue_manager as queue_module
from services.cancellation import CancellationContext
from services.queue_manager import JobStatus, QueueManager


@pytest.fixture()
def queue_env(tmp_path, monkeypatch):
    """A manager on a temp queue file; restores the module state after."""
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


def _seed(manager, *, status, checkpoint=None, cancel_requested=False,
          error=None, attempt=1, paused_reason=None):
    added = manager.add_item({"script": "A grounded scene.", "title": "Seed"})
    item = manager.get_item(added["id"])
    item.update(status=status, attempt=attempt, progress=64,
                progress_text="seeded")
    if checkpoint is not None:
        item["checkpoint_dir"] = str(checkpoint)
    if cancel_requested:
        item["cancel_requested"] = True
    if error is not None:
        item["error"] = error
    if paused_reason is not None:
        item["paused_reason"] = paused_reason
    manager._save_queue()
    return item


def _fresh_manager():
    QueueManager._instance = None
    manager = QueueManager()
    queue_module.queue_manager = manager
    return manager


def test_reboot_recovers_system_jobs_but_not_user_cancelled(queue_env, monkeypatch):
    manager, tmp_path = queue_env
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    processing = _seed(manager, status="processing")
    sys_cancelled = _seed(manager, status="cancelled", checkpoint=ckpt)
    user_cancelled = _seed(manager, status="cancelled", checkpoint=ckpt,
                           cancel_requested=True)
    no_ckpt = _seed(manager, status="cancelled")
    transient = _seed(manager, status="error", attempt=1,
                      error="Connection reset by peer while downloading transcript")
    exhausted = _seed(manager, status="error", attempt=3,
                      error="Connection reset by peer while downloading transcript")
    hard = _seed(manager, status="error", attempt=1,
                 error="Media contract failed: no media could be resolved")

    monkeypatch.setenv("VIDZEO_AUTO_REQUEUE_INTERRUPTED", "1")
    restarted = _fresh_manager()

    assert restarted.get_item(processing["id"])["status"] == "queued"
    assert restarted.get_item(sys_cancelled["id"])["status"] == "queued"
    assert restarted.get_item(user_cancelled["id"])["status"] == "cancelled"
    assert restarted.get_item(no_ckpt["id"])["status"] == "cancelled"
    assert restarted.get_item(transient["id"])["status"] == "queued"
    assert restarted.get_item(exhausted["id"])["status"] == "error"
    assert restarted.get_item(hard["id"])["status"] == "error"


def test_reboot_without_auto_requeue_parks_system_jobs_paused(queue_env):
    manager, tmp_path = queue_env
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    processing = _seed(manager, status="processing")
    sys_cancelled = _seed(manager, status="cancelled", checkpoint=ckpt)
    transient = _seed(manager, status="error", attempt=1,
                      error="Gateway timeout (504) from the CMS")

    restarted = _fresh_manager()

    item = restarted.get_item(processing["id"])
    assert item["status"] == "paused"
    assert item["paused_reason"] == "backend_restart"
    item = restarted.get_item(sys_cancelled["id"])
    assert item["status"] == "paused"
    assert item["paused_reason"] == "system_cancel_recovered"
    assert item["retryable"] is True
    assert restarted.get_item(transient["id"])["status"] == "error"


def test_resume_item_accepts_system_cancelled_not_user_cancelled(queue_env):
    manager, tmp_path = queue_env
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    sys_cancelled = _seed(manager, status="cancelled", checkpoint=ckpt)
    user_cancelled = _seed(manager, status="cancelled", checkpoint=ckpt,
                           cancel_requested=True)
    no_ckpt = _seed(manager, status="cancelled")
    paused = _seed(manager, status="paused", paused_reason="delivery_deadline")

    assert manager.resume_item(sys_cancelled["id"]) is True
    assert manager.get_item(sys_cancelled["id"])["status"] == "queued"
    assert manager.resume_item(user_cancelled["id"]) is False
    assert manager.get_item(user_cancelled["id"])["status"] == "cancelled"
    assert manager.resume_item(no_ckpt["id"]) is False
    assert manager.resume_item(paused["id"]) is True


def test_deadline_watchdog_cancel_lands_paused_not_cancelled(queue_env):
    """The Wright bug: the watchdog cancelled directly, so the finalize
    stored CANCELLED and resume refused it. A system deadline cancel must
    land PAUSED with a resumable reason instead."""
    manager, _tmp = queue_env
    added = manager.add_item({"script": "A grounded scene.", "title": "Wright"})
    job_id = added["id"]
    item = manager.get_item(job_id)
    cancellation = CancellationContext(job_id)
    cancellation.cancel("Shared video processing deadline exceeded")
    manager.active_jobs[job_id] = {
        "context": cancellation,
        "requested_at": None,
        "remove_after_cancel": False,
    }

    manager._finalize_cancelled_job(job_id, item, cancellation)

    assert item["status"] == "paused"
    assert item["paused_reason"] == "delivery_deadline"
    assert item["retryable"] is True
    assert item["cancel_reason"] == "Shared video processing deadline exceeded"
    # And a paused-by-deadline job resumes.
    assert manager.resume_item(job_id) is True


def test_user_cancel_still_lands_cancelled(queue_env):
    manager, _tmp = queue_env
    added = manager.add_item({"script": "A grounded scene.", "title": "User"})
    job_id = added["id"]
    item = manager.get_item(job_id)
    cancellation = CancellationContext(job_id)
    cancellation.cancel("Job cancelled by user")
    manager.active_jobs[job_id] = {
        "context": cancellation,
        "requested_at": "2026-11-15T16:41:56",
        "remove_after_cancel": False,
    }

    manager._finalize_cancelled_job(job_id, item, cancellation)

    assert item["status"] == "cancelled"
    assert "paused_reason" not in item


def test_unknown_system_cancel_reason_stays_cancelled(queue_env):
    """Only the known system reasons (deadline / preparation stall) get the
    resumable pause; anything unrecognized must not be auto-resumed."""
    manager, _tmp = queue_env
    added = manager.add_item({"script": "A grounded scene.", "title": "Other"})
    job_id = added["id"]
    item = manager.get_item(job_id)
    cancellation = CancellationContext(job_id)
    cancellation.cancel("Something else broke")
    manager.active_jobs[job_id] = {
        "context": cancellation,
        "requested_at": None,
        "remove_after_cancel": False,
    }

    manager._finalize_cancelled_job(job_id, item, cancellation)

    assert item["status"] == "cancelled"


def test_record_yt_to_web_substitution_is_visible_and_deduped():
    import app

    metadata = {}
    app._record_yt_to_web_substitution(
        metadata, 3, via="shadow_web", reason="youtube_lane_no_clip",
        to_path="/tmp/web_3.jpg",
    )
    assert metadata["yt_to_web_substitutions"] == [{
        "scene_index": 3,
        "from": "YouTube Clips",
        "to": "/tmp/web_3.jpg",
        "via": "shadow_web",
        "reason": "youtube_lane_no_clip",
    }]
    # One record per scene: a second rescue path for the same scene does
    # not duplicate the entry.
    app._record_yt_to_web_substitution(
        metadata, 3, via="general_web_fallback",
        reason="youtube_lane_no_clip", to_path="/tmp/other.jpg",
    )
    assert len(metadata["yt_to_web_substitutions"]) == 1
    app._record_yt_to_web_substitution(
        metadata, 5, via="general_web_fallback",
        reason="youtube_lane_unresolved", to_path="/tmp/web_5.jpg",
    )
    assert len(metadata["yt_to_web_substitutions"]) == 2
    # Never crashes on bad input.
    app._record_yt_to_web_substitution(
        None, 1, via="x", reason="y", to_path="z",
    )
    app._record_yt_to_web_substitution(
        {}, "not-a-scene", via="x", reason="y", to_path="z",
    )
