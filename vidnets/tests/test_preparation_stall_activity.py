"""Preparation-stall detector vs genuine lane activity.

The queue's preparation-stall detector fires after ``preparation_stall_seconds``
without a ``(progress, text)`` change on jobs that opt in. Long-running lanes
(web collection, YouTube downloads, vision rounds) do real work for longer
than that while the displayed media count stands still, so the detector
must read lane activity, not only display progress. These tests pin both
sides: a genuinely idle stage still stalls, and a stage whose lane keeps
reporting activity does not.
"""

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services import queue_manager as queue_module
from services.queue_manager import JobStatus, QueueManager


def _manager(tmp_path):
    QueueManager._instance = None
    original_queue_file = queue_module.QUEUE_FILE
    queue_module.QUEUE_FILE = tmp_path / "queue.json"
    manager = QueueManager()
    return manager, original_queue_file


def _restore(original_queue_file):
    QueueManager._instance = None
    queue_module.QUEUE_FILE = original_queue_file


def _wait_for(manager, item_id, status, deadline_s=5.0):
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        item = manager.get_item(item_id)
        if item is not None and item["status"] == status:
            return item
        time.sleep(0.02)
    return manager.get_item(item_id)


def test_activity_registry_records_and_resets():
    from services.pipeline_activity import last_activity, record_activity, reset_activity

    reset_activity("test")
    assert last_activity() is None
    record_activity("web")
    first = last_activity()
    assert first is not None
    time.sleep(0.02)
    record_activity("youtube")
    assert last_activity() >= first
    reset_activity("test")
    assert last_activity() is None


def test_activity_before_reset_is_ignored():
    from services.pipeline_activity import last_activity, record_activity, reset_activity

    record_activity("web")
    assert last_activity() is not None
    reset_activity("test")
    assert last_activity() is None
    record_activity("web")
    assert last_activity() is not None


@pytest.mark.parametrize("saved_muse_request", [False, True])
def test_queue_idle_policy_respects_saved_muse_requests(tmp_path, monkeypatch, saved_muse_request):
    manager, original = _manager(tmp_path)
    monkeypatch.setenv("VIDZEO_QUEUE_PREP_WORKERS", "1")
    manager._progress_heartbeat_poll_seconds = 0.05

    def idle_stage(config, update_progress, check_cancelled, cancellation):
        time.sleep(1.5)

    try:
        config = {
            "title": "Idle stage",
            "script": "A sufficiently long script for an isolated queue job.",
            "preparation_stall_seconds": 0.5,
            "llm_provider": "external_agent",
            "continue_render_while_active": saved_muse_request,
        }
        item = manager.add_item(config)
        manager.start_worker(idle_stage)
        expected = JobStatus.COMPLETED if saved_muse_request else JobStatus.PAUSED
        result = _wait_for(manager, item["id"], expected)
        assert result["status"] == expected
        assert result.get("paused_reason") == (None if saved_muse_request else "preparation_stall")
    finally:
        manager.stop_worker()
        _restore(original)


def test_queue_does_not_stall_while_lane_reports_activity(tmp_path, monkeypatch):
    from services.pipeline_activity import record_activity

    manager, original = _manager(tmp_path)
    monkeypatch.setenv("VIDZEO_QUEUE_PREP_WORKERS", "1")
    manager._progress_heartbeat_poll_seconds = 0.05

    def busy_stage(config, update_progress, check_cancelled, cancellation):
        # A long lane (web collection) doing real work: it records activity
        # but never calls update_progress, so the displayed count stands
        # still for the whole stage.
        stop = time.monotonic() + 1.5
        while time.monotonic() < stop:
            record_activity("web")
            time.sleep(0.2)

    try:
        item = manager.add_item(
            {
                "title": "Busy lane",
                "script": "A sufficiently long script for an isolated queue job.",
                "preparation_stall_seconds": 0.5,
                "llm_provider": "external_agent",
                "continue_render_while_active": False,
            }
        )
        manager.start_worker(busy_stage)
        done = _wait_for(manager, item["id"], JobStatus.COMPLETED)
        assert done["status"] == JobStatus.COMPLETED
        assert done.get("paused_reason") is None
    finally:
        manager.stop_worker()
        _restore(original)
