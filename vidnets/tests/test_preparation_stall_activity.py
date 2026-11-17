"""Preparation-stall detector vs genuine lane activity.

The queue's preparation-stall detector fires after ``preparation_stall_seconds``
(300s in production) without a ``(progress, text)`` change. Long-running lanes
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


def test_queue_stalls_when_genuinely_idle(tmp_path, monkeypatch):
    manager, original = _manager(tmp_path)
    monkeypatch.setenv("VIDZEO_QUEUE_PREP_WORKERS", "1")
    manager._progress_heartbeat_poll_seconds = 0.05

    def idle_stage(config, update_progress, check_cancelled, cancellation):
        time.sleep(1.5)

    try:
        item = manager.add_item(
            {
                "title": "Idle stage",
                "script": "A sufficiently long script for an isolated queue job.",
                "preparation_stall_seconds": 0.5,
            }
        )
        manager.start_worker(idle_stage)
        paused = _wait_for(manager, item["id"], JobStatus.PAUSED)
        assert paused["status"] == JobStatus.PAUSED
        assert paused["paused_reason"] == "preparation_stall"
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
            }
        )
        manager.start_worker(busy_stage)
        done = _wait_for(manager, item["id"], JobStatus.COMPLETED)
        assert done["status"] == JobStatus.COMPLETED
        assert done.get("paused_reason") is None
    finally:
        manager.stop_worker()
        _restore(original)
