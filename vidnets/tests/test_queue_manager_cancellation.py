import os
import json
import sys
import threading
import time
import pytest
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services import queue_manager as queue_module
from services.cancellation import CancellationContext, JobCancelled
from services.delivery_budget import DeliveryBudget
from services.queue_manager import (
    JobStatus,
    QueueManager,
    _callback_config_for_run,
)


def _manager(tmp_path):
    QueueManager._instance = None
    original_queue_file = queue_module.QUEUE_FILE
    queue_module.QUEUE_FILE = tmp_path / "queue.json"
    manager = QueueManager()
    return manager, original_queue_file


def _restore(original_queue_file):
    QueueManager._instance = None
    queue_module.QUEUE_FILE = original_queue_file


def test_queue_status_remains_readable_during_slow_persistence(tmp_path, monkeypatch):
    manager, original = _manager(tmp_path)
    writing = threading.Event()
    release = threading.Event()
    real_dump = queue_module.json.dump
    writer = None
    try:
        item = manager.add_item({"script": "A grounded scene."})

        def slow_dump(*args, **kwargs):
            writing.set()
            assert release.wait(5.0)
            return real_dump(*args, **kwargs)

        monkeypatch.setattr(queue_module.json, "dump", slow_dump)
        writer = threading.Thread(target=manager._save_queue)
        writer.start()
        assert writing.wait(5.0)
        with ThreadPoolExecutor(max_workers=1) as readers:
            snapshot = readers.submit(manager.get_item_snapshot, item["id"])
            try:
                assert snapshot.result(timeout=1.0)["id"] == item["id"]
            finally:
                release.set()
        writer.join(5.0)
        assert not writer.is_alive()
        saved = json.loads((tmp_path / "queue.json").read_text(encoding="utf-8"))
        assert saved["items"][0]["id"] == item["id"]
    finally:
        release.set()
        if writer is not None:
            writer.join(5.0)
        _restore(original)


def test_queue_redaction_does_not_hold_status_lock(tmp_path, monkeypatch):
    manager = QueueManager.__new__(QueueManager)
    manager._persistence_lock = threading.Lock()
    manager._queue_lock = threading.Lock()
    manager._save_tick = 0
    manager._full_backup_interval = 100
    manager.queue = [{"id": "large-history", "status": "completed", "result": {"rows": [1, 2, 3]}}]
    monkeypatch.setattr(queue_module, "QUEUE_FILE", tmp_path / "queue.json")
    entered_redaction = threading.Event()
    release_redaction = threading.Event()
    original_redact = queue_module._redact_sensitive_data

    def slow_redact(value, **kwargs):
        entered_redaction.set()
        assert release_redaction.wait(3.0)
        return original_redact(value, **kwargs)

    monkeypatch.setattr(queue_module, "_redact_sensitive_data", slow_redact)
    writer = threading.Thread(target=manager._save_queue)
    writer.start()
    assert entered_redaction.wait(2.0)
    lock_available = manager._queue_lock.acquire(timeout=0.5)
    if lock_available:
        manager._queue_lock.release()
    release_redaction.set()
    writer.join(3.0)
    assert not writer.is_alive()
    assert lock_available, "redacting the persisted queue must not block status reads"


def test_child_stage_cancellation_is_linked_but_does_not_cancel_parent():
    parent = CancellationContext(job_id="job")
    child = parent.child("job:youtube")

    child.cancel("stage deadline")

    assert child.cancelled()
    assert not parent.cancelled()


def test_parent_cancellation_is_observed_by_child_stage():
    parent = CancellationContext(job_id="job")
    child = parent.child("job:youtube")

    parent.cancel("operator stopped job")

    assert child.cancelled()
    try:
        child.raise_if_cancelled()
    except JobCancelled as exc:
        assert "operator stopped job" in str(exc)
    else:
        raise AssertionError("linked child did not raise parent cancellation")


def test_parent_cancellation_propagates_to_nested_stage_reason():
    parent = CancellationContext(job_id="job")
    child = parent.child("job:youtube")
    grandchild = child.child("job:youtube:download")

    parent.cancel("aggregate YouTube wall reached")

    assert child.cancelled()
    assert grandchild.cancelled()
    assert grandchild.reason == "aggregate YouTube wall reached"


def test_recovery_waits_for_nested_worker_before_retrying_same_job(tmp_path):
    manager, original = _manager(tmp_path)
    try:
        item = manager.add_item({'script': 'Saved script.'})
        parent = CancellationContext(job_id=item['id'])
        nested = parent.child().child()
        future = Future()
        assert future.set_running_or_notify_cancel()
        nested.register_future(future)
        parent.cancel('Preparation stalled')
        manager.get_item(item['id'])['status'] = JobStatus.SHUTDOWN_TIMEOUT
        manager.active_jobs[item['id']] = {'cleanup_status': 'shutdown_timeout', 'context': parent}
        assert not manager.resume_item(item['id'])
        future.set_result(None)
        assert manager.resume_item(item['id'])
        assert manager.get_item(item['id'])['status'] == JobStatus.QUEUED
    finally:
        _restore(original)


def test_partial_queue_edits_keep_content_title_and_output_name_independent(tmp_path):
    manager, original = _manager(tmp_path)
    try:
        item = manager.add_item({
            "content_title": "Seven Historic Bridges",
            "output_name": "bridges-v1",
            "script": "A sufficiently long script for the queue.",
        })

        assert manager.update_item(item["id"], {"config": {"output_name": "bridges-final"}})
        stored = manager.get_item(item["id"])["config"]
        assert stored["content_title"] == "Seven Historic Bridges"
        assert stored["title"] == "Seven Historic Bridges"
        assert stored["output_name"] == "bridges-final"
        assert stored["name"] == "bridges-final"

        assert manager.update_item(item["id"], {"config": {"title": "Eight Historic Bridges"}})
        stored = manager.get_item(item["id"])["config"]
        assert stored["content_title"] == "Eight Historic Bridges"
        assert stored["output_name"] == "bridges-final"
    finally:
        manager.stop_worker()
        _restore(original)


def test_new_and_resumed_jobs_start_with_isolated_progress_history(tmp_path):
    manager, original = _manager(tmp_path)
    try:
        item = manager.add_item({
            "title": "Fresh job",
            "script": "A sufficiently long script for an isolated queue job.",
        })
        assert item["progress"] == 0
        assert item["progress_seq"] == 1
        assert item["progress_events"] == [
            {"seq": 1, "progress": 0, "text": "Queued"}
        ]

        stored = manager.get_item(item["id"])
        stored["status"] = JobStatus.ERROR
        stored["progress"] = 100
        stored["progress_text"] = "Completed stale job"
        stored["progress_seq"] = 99
        stored["progress_events"] = [
            {"seq": 99, "progress": 100, "text": "Completed stale job"}
        ]

        assert manager.resume_item(item["id"])
        resumed = manager.get_item(item["id"])
        assert resumed["progress"] == 0
        assert resumed["progress_seq"] == 1
        assert resumed["progress_events"] == [
            {"seq": 1, "progress": 0, "text": "Queued (Resumed)"}
        ]
    finally:
        manager.stop_worker()
        _restore(original)


def test_openrouter_failure_pauses_with_durable_package_for_retry(tmp_path, monkeypatch):
    manager, original = _manager(tmp_path)
    monkeypatch.setenv('VIDZEO_QUEUE_PREP_WORKERS', '1')
    package_dir = tmp_path / 'documentary-package'
    (package_dir / 'checkpoints').mkdir(parents=True)
    class RetryableScenePlanError(RuntimeError):
        retryable = True
        stage = 'openrouter_scene_plan'

    def fail_after_checkpoint(config, update_progress, check_cancelled, cancellation):
        config['output_package_dir'] = str(package_dir)
        update_progress(27, 'OpenRouter query planning: directing scenes')
        raise RetryableScenePlanError('Provider rejected scene plan')

    try:
        item = manager.add_item({
            'title': 'Checkpointed documentary',
            'script': 'A sufficiently long script for an isolated queue job.',
        })
        manager.start_worker(fail_after_checkpoint)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and manager.get_item(item['id'])['status'] != JobStatus.PAUSED:
            time.sleep(0.02)
        while manager.active_jobs and time.monotonic() < deadline:
            time.sleep(0.02)
        paused = manager.get_item(item['id'])
        assert paused['status'] == JobStatus.PAUSED
        assert paused['paused_reason'] == 'openrouter_scene_plan'
        assert paused['retryable'] is True
        assert paused['config']['output_package_dir'] == str(package_dir)
        assert paused['checkpoint_dir'] == str(package_dir / 'checkpoints')
        assert 'OpenRouter' in paused['progress_text']
        stored = {}
        while time.monotonic() < deadline:
            stored = json.loads(queue_module.QUEUE_FILE.read_text(encoding='utf-8'))
            if stored['items'][0]['config'].get('output_package_dir') == str(package_dir):
                break
            time.sleep(0.02)
        assert stored['items'][0]['config']['output_package_dir'] == str(package_dir)
        manager.stop_worker()
        assert manager.resume_item(item['id'])
        assert manager.get_item(item['id'])['config']['output_package_dir'] == str(package_dir)
    finally:
        manager.stop_worker()
        _restore(original)


def test_media_contract_failure_pauses_with_retryable_package(tmp_path, monkeypatch):
    from services.pipeline_contracts import MediaContractError

    manager, original = _manager(tmp_path)
    monkeypatch.setenv('VIDZEO_QUEUE_PREP_WORKERS', '1')
    package_dir = tmp_path / 'media-retry-package'
    (package_dir / 'checkpoints').mkdir(parents=True)

    def fail_media(config, update_progress, check_cancelled, cancellation):
        config['output_package_dir'] = str(package_dir)
        update_progress(52, 'Checking scene visuals')
        raise MediaContractError('Scene 1 has no verified media', {'scene_index': 0})

    try:
        item = manager.add_item({'title': 'Media retry', 'script': 'A short documentary scene.'})
        manager.start_worker(fail_media)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and manager.get_item(item['id'])['status'] != JobStatus.PAUSED:
            time.sleep(0.02)
        paused = manager.get_item(item['id'])
        assert paused['status'] == JobStatus.PAUSED
        assert paused['paused_reason'] == 'media_contract'
        assert paused['retryable'] is True
        assert paused['checkpoint_dir'] == str(package_dir / 'checkpoints')
        # Paused is published before callback cleanup finishes. Retry is
        # intentionally blocked while that attempt can still write files.
        while manager.active_jobs and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not manager.active_jobs
        assert manager.resume_item(item['id'])
    finally:
        manager.stop_worker()
        _restore(original)


def test_restart_reconciles_stale_cancellation_request_without_leaving_ui_stuck(tmp_path):
    original = queue_module.QUEUE_FILE
    QueueManager._instance = None
    queue_module.QUEUE_FILE = tmp_path / "queue.json"
    queue_module.QUEUE_FILE.write_text(json.dumps({
        "items": [{
            "id": "cancelled-job",
            "status": JobStatus.CANCELLATION_REQUESTED,
            "progress": 46,
            "progress_text": "Stopping...",
            "config": {"title": "Stale cancellation", "script": "A sufficiently long test script."},
        }],
    }), encoding="utf-8")
    try:
        manager = QueueManager()
        item = manager.get_item("cancelled-job")
        assert item["status"] == JobStatus.CANCELLED
        assert item["progress_text"] == "Cancelled after backend restart"
        assert item["cancel_recovered"] is True
        assert not manager.get_status()["active_jobs"]
        # The status is persisted immediately, so the next backend process
        # cannot rediscover the same stopping state.
        stored = json.loads(queue_module.QUEUE_FILE.read_text(encoding="utf-8"))
        assert stored["items"][0]["status"] == JobStatus.CANCELLED
    finally:
        QueueManager._instance = None
        queue_module.QUEUE_FILE = original


def test_restart_makes_stale_shutdown_timeout_resumable(tmp_path):
    original = queue_module.QUEUE_FILE
    QueueManager._instance = None
    queue_module.QUEUE_FILE = tmp_path / "queue.json"
    queue_module.QUEUE_FILE.write_text(json.dumps({
        "items": [{
            "id": "timeout-job",
            "status": JobStatus.SHUTDOWN_TIMEOUT,
            "progress": 62,
            "progress_text": "Cancellation cleanup timed out",
            "config": {"title": "Stale timeout", "script": "A sufficiently long test script."},
        }],
    }), encoding="utf-8")
    try:
        manager = QueueManager()
        item = manager.get_item("timeout-job")
        assert item["status"] == JobStatus.PAUSED
        assert item["paused_reason"] == "cancellation_cleanup_timeout"
        assert "click Resume" in item["progress_text"]
        assert manager.resume_item("timeout-job") is True
        assert manager.get_item("timeout-job")["status"] == JobStatus.QUEUED
    finally:
        QueueManager._instance = None
        queue_module.QUEUE_FILE = original


def test_retry_refuses_second_attempt_while_timed_out_worker_is_still_active(tmp_path):
    manager, original = _manager(tmp_path)
    try:
        item = manager.add_item({"title": "Still stopping", "script": "A sufficiently long test script."})
        stored = manager.get_item(item["id"])
        stored["status"] = JobStatus.SHUTDOWN_TIMEOUT
        manager.active_jobs[item["id"]] = {"context": CancellationContext(job_id=item["id"])}

        assert manager.resume_item(item["id"]) is False
        assert stored["status"] == JobStatus.SHUTDOWN_TIMEOUT
    finally:
        manager.stop_worker()
        _restore(original)


def test_concurrent_run_payloads_with_same_scene_index_are_deeply_isolated():
    barrier = threading.Barrier(2)

    def build(job_id, run_id, subject):
        config = {
            "content_title": f"Facts about {subject}",
            "output_name": f"facts-{subject.lower()}",
            "script": f"One. {subject} has a distinct history.",
            "scene_contracts": [{"scene_index": 0, "subject": subject}],
        }
        barrier.wait(timeout=1.0)
        payload = _callback_config_for_run(
            config,
            job_id=job_id,
            run_id=run_id,
            attempt=1,
        )
        payload["scene_contracts"][0]["resolved_by"] = job_id
        return config, payload

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(build, "job-alpha", "run-alpha", "Alpha")
        second_future = executor.submit(build, "job-beta", "run-beta", "Beta")
        first_original, first = first_future.result(timeout=2.0)
        second_original, second = second_future.result(timeout=2.0)

    assert first["scene_contracts"][0] == {
        "scene_index": 0,
        "subject": "Alpha",
        "resolved_by": "job-alpha",
    }
    assert second["scene_contracts"][0] == {
        "scene_index": 0,
        "subject": "Beta",
        "resolved_by": "job-beta",
    }
    assert "resolved_by" not in first_original["scene_contracts"][0]
    assert "resolved_by" not in second_original["scene_contracts"][0]
    assert first["_queue_job_id"] == first["job_id"] == "job-alpha"
    assert second["_queue_job_id"] == second["job_id"] == "job-beta"
    assert first["_queue_run_id"] == first["run_id"] == "run-alpha"
    assert second["_queue_run_id"] == second["run_id"] == "run-beta"


def test_active_delete_waits_for_callback_exit_before_removing_item(tmp_path):
    manager, original = _manager(tmp_path)
    started = threading.Event()
    released = threading.Event()
    try:
        item = manager.add_item({"title": "Blocking", "script": "A sufficiently long test script."})

        def callback(_config, _progress, check_cancelled, cancellation):
            started.set()
            while not check_cancelled():
                time.sleep(0.005)
            cancellation.raise_if_cancelled()

        manager.start_worker(callback)
        # The first queue run may import the media-provider runtime on its
        # worker thread; don't confuse that cold-start cost with a deadlock.
        assert started.wait(10.0)
        result = manager.delete_item(item["id"])
        assert result["pending"] is True
        assert manager.get_item(item["id"])["status"] == JobStatus.CANCELLATION_REQUESTED

        deadline = time.monotonic() + 10.0
        while manager.active_jobs and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not manager.active_jobs
        assert manager.get_item(item["id"]) is None
    finally:
        manager.stop_worker()
        _restore(original)


def test_clear_all_keeps_active_jobs_visible_until_cancelled(tmp_path):
    manager, original = _manager(tmp_path)
    started = threading.Event()
    try:
        item = manager.add_item({"title": "Blocking", "script": "A sufficiently long test script."})

        callback_release = threading.Event()

        def callback(_config, _progress, _check_cancelled, cancellation):
            started.set()
            cancellation.event.wait(2.0)
            callback_release.wait(0.25)
            cancellation.raise_if_cancelled()

        manager.start_worker(callback)
        assert started.wait(10.0)
        result = manager.clear_all()
        assert result["pending"] == [item["id"]]
        status = manager.get_status()
        assert item["id"] in status["active_jobs"]
        assert status["items"][0]["status"] == JobStatus.CANCELLATION_REQUESTED

        deadline = time.monotonic() + 10.0
        while manager.active_jobs and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not manager.active_jobs
        assert manager.get_status()["items"] == []
    finally:
        manager.stop_worker()
        _restore(original)


def test_worker_processes_next_job_after_cancellation(tmp_path):
    manager, original = _manager(tmp_path)
    started = threading.Event()
    completed = threading.Event()
    calls = []
    try:
        first = manager.add_item({"title": "First", "script": "A sufficiently long first test script."})
        second = manager.add_item({"title": "Second", "script": "A sufficiently long second test script."})

        def callback(config, _progress, _check_cancelled, cancellation):
            calls.append(config["title"])
            if config["title"] == "First":
                started.set()
                cancellation.event.wait(2.0)
                cancellation.raise_if_cancelled()
            completed.set()
            return {"ok": True}

        manager.start_worker(callback)
        assert started.wait(10.0)
        result = manager.delete_item(first["id"])
        assert result["pending"] is True
        assert completed.wait(10.0)

        deadline = time.monotonic() + 10.0
        while (
            (
                manager.get_item(second["id"])["status"] != JobStatus.COMPLETED
                or bool(manager.active_jobs)
            )
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert manager.get_item(second["id"])["status"] == JobStatus.COMPLETED
        assert calls == ["First", "Second"]
    finally:
        manager.stop_worker()
        _restore(original)


def test_worker_records_terminal_error_in_durable_progress_events(tmp_path):
    manager, original = _manager(tmp_path)
    try:
        item = manager.add_item({"title": "Failure visibility", "script": "A sufficiently long test script."})

        def callback(*_args):
            raise RuntimeError("media resolver exhausted every source")

        manager.start_worker(callback)
        deadline = time.monotonic() + 10.0
        while manager.get_item(item["id"])["status"] not in {JobStatus.ERROR, JobStatus.CANCELLED} and time.monotonic() < deadline:
            time.sleep(0.01)
        stored = manager.get_item(item["id"])
        assert stored["status"] == JobStatus.ERROR
        assert stored["progress_events"][-1]["text"].startswith("Error: media resolver")
        assert stored["progress_seq"] == stored["progress_events"][-1]["seq"]
    finally:
        manager.stop_worker()
        _restore(original)


def test_late_media_callback_does_not_relabel_an_active_render(tmp_path):
    manager, original = _manager(tmp_path)
    observed = []
    try:
        item = manager.add_item({'title': 'Stage ordering', 'script': 'A short test.'})
        def callback(_config, progress, *_args):
            progress(80, 'Rendering video')
            progress(65, 'Preparing media 5/5')
            observed.append(manager.get_item(item['id'])['progress_text'])
            return {'success': True}
        manager.start_worker(callback)
        deadline = time.monotonic() + 5
        while manager.get_item(item['id'])['status'] != JobStatus.COMPLETED and time.monotonic() < deadline:
            time.sleep(.01)
        assert observed == ['Rendering video']
    finally:
        manager.stop_worker()
        _restore(original)


def test_delivery_deadline_stops_active_job_and_leaves_retryable_pause(tmp_path, monkeypatch):
    manager, original = _manager(tmp_path)
    manager._progress_heartbeat_poll_seconds = 0.02
    started = threading.Event()
    try:
        budget = DeliveryBudget.create(total_seconds=0.25, render_reserve_seconds=0.05)
        monkeypatch.setattr(queue_module, "install_delivery_budget", lambda _config: budget)
        item = manager.add_item({"title": "Deadline recovery", "script": "A short test documentary."})

        def wait_for_deadline(_config, _progress, check_cancelled, cancellation):
            started.set()
            while not check_cancelled():
                time.sleep(0.01)
            cancellation.raise_if_cancelled()

        manager.start_worker(wait_for_deadline)
        assert started.wait(5)
        deadline = time.monotonic() + 5
        while manager.active_jobs and time.monotonic() < deadline:
            time.sleep(0.01)

        paused = manager.get_item(item["id"])
        assert not manager.active_jobs
        assert paused["status"] == JobStatus.PAUSED
        assert paused["paused_reason"] == "delivery_deadline"
        assert paused["retryable"] is True
        assert "Retry" in paused["progress_text"]
        assert any(
            "Processing time limit reached" in event["text"]
            for event in paused["progress_events"]
        )
    finally:
        manager.stop_worker()
        _restore(original)


@pytest.mark.parametrize('render_started', [False, True])
def test_preparation_watchdog_resumes_same_job_without_interrupting_render(tmp_path, monkeypatch, render_started):
    manager, original = _manager(tmp_path)
    manager._progress_heartbeat_poll_seconds = 0.02
    manager._progress_heartbeat_idle_seconds = 0.02
    manager._progress_heartbeat_emit_seconds = 0.02
    attempts = []
    retained = tmp_path / 'saved-media'
    budget = DeliveryBudget.create(total_seconds=20)
    monkeypatch.setattr(queue_module, 'install_delivery_budget', lambda _config: budget)

    def generate(config, progress, check_cancelled, cancellation):
        attempts.append(config['_queue_job_id'])
        if len(attempts) == 2:
            assert retained.read_text() == 'ready'
            return {'success': True}
        retained.write_text('ready')
        progress(60, 'Preparing media')
        if render_started:
            budget.begin_render_phase()
        end = time.monotonic() + 0.4
        while time.monotonic() < end and not check_cancelled():
            progress(60, 'Preparing media')
            time.sleep(0.01)
        cancellation.raise_if_cancelled()
        return {'success': True}

    try:
        item = manager.add_item({'script': 'An offline test.', 'preparation_stall_seconds': 0.12})
        manager.start_worker(generate)
        expected = JobStatus.COMPLETED if render_started else JobStatus.PAUSED
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if manager.get_item(item['id'])['status'] == expected and not manager.active_jobs:
                break
            time.sleep(0.01)
        assert manager.get_item(item['id'])['status'] == expected
        if not render_started:
            assert manager.get_item(item['id'])['paused_reason'] == 'preparation_stall'
            assert manager.resume_item(item['id'])
            deadline = time.monotonic() + 5
            while manager.get_item(item['id'])['status'] != JobStatus.COMPLETED and time.monotonic() < deadline:
                time.sleep(0.01)
            assert manager.get_item(item['id'])['status'] == JobStatus.COMPLETED
            assert attempts == [item['id'], item['id']]
    finally:
        manager.stop_worker()
        _restore(original)


def test_start_worker_is_idempotent_under_repeated_start_requests(tmp_path):
    manager, original = _manager(tmp_path)
    try:
        first = manager.start_worker(lambda *_args: {"ok": True})
        second = manager.start_worker(lambda *_args: {"ok": True})

        assert first is True
        assert second is False
        assert manager.worker_running is True
    finally:
        manager.stop_worker()
        _restore(original)


def test_duplicate_provider_progress_emits_wait_hint_without_faking_percent(tmp_path):
    manager, original = _manager(tmp_path)
    manager._progress_heartbeat_poll_seconds = 0.02
    manager._progress_heartbeat_idle_seconds = 0.06
    manager._progress_heartbeat_emit_seconds = 0.06
    try:
        item = manager.add_item({"title": "Quiet media provider", "script": "A sufficiently long test script."})

        def callback(_config, update_progress, check_cancelled, _cancellation):
            deadline = time.monotonic() + 0.45
            while time.monotonic() < deadline and not check_cancelled():
                update_progress(69, "Preparing media")
                time.sleep(0.01)
            return {"ok": True}

        manager.start_worker(callback)
        deadline = time.monotonic() + 10.0
        while manager.get_item(item["id"])["status"] != JobStatus.COMPLETED and time.monotonic() < deadline:
            time.sleep(0.01)

        stored = manager.get_item(item["id"])
        assert stored["status"] == JobStatus.COMPLETED
        wait_events = [event for event in stored["progress_events"] if "still working" in event["text"]]
        assert wait_events
        assert all(event["progress"] == 69 for event in wait_events)
    finally:
        manager.stop_worker()
        _restore(original)


def test_cancelled_attempt_cannot_leak_identity_or_scene_data_to_next_job(tmp_path, monkeypatch):
    # This is a sequential handoff assertion. Parallel preparation has its
    # own isolation tests and may legitimately start Beta before Alpha.
    monkeypatch.setenv('VIDZEO_QUEUE_PREP_WORKERS', '1')
    manager, original = _manager(tmp_path)
    first_started = threading.Event()
    second_completed = threading.Event()
    observed = []
    try:
        first = manager.add_item({
            "content_title": "Alpha Subject",
            "output_name": "alpha-output",
            "script": "One. Alpha is the subject of this scene.",
            "scene_contracts": [{"scene_index": 0, "subject": "Alpha"}],
            # Imported IDs must never escape into the queue-owned run.
            "job_id": "stale-imported-job",
            "run_id": "stale-imported-run",
        })
        second = manager.add_item({
            "content_title": "Beta Subject",
            "output_name": "beta-output",
            "script": "One. Beta is the subject of this scene.",
            "scene_contracts": [{"scene_index": 0, "subject": "Beta"}],
        })

        def callback(config, _progress, _check_cancelled, cancellation):
            observed.append({
                "job_id": config["_queue_job_id"],
                "run_id": config["_queue_run_id"],
                "context_job_id": cancellation.job_id,
                "title": config["content_title"],
                "output_name": config["output_name"],
                "scene": dict(config["scene_contracts"][0]),
            })
            # Mutating a callback payload must not mutate queue persistence or
            # the next attempt's nested scene data.
            config["scene_contracts"][0]["subject"] = "MUTATED"
            if config["content_title"] == "Alpha Subject":
                first_started.set()
                # Keep this controlled attempt alive until the test cancels
                # it, including when render load delays the test thread.
                cancellation.event.wait(20.0)
                cancellation.raise_if_cancelled()
            second_completed.set()
            return {"ok": True}

        manager.start_worker(callback)
        assert first_started.wait(10.0)
        active_state = manager.get_status()["active_job_state"][first["id"]]
        assert active_state["run_id"] == observed[0]["run_id"]
        assert active_state["attempt"] == 1
        assert manager.delete_item(first["id"])["pending"] is True
        assert second_completed.wait(10.0)

        deadline = time.monotonic() + 10.0
        while (
            (
                manager.get_item(second["id"])["status"] != JobStatus.COMPLETED
                or bool(manager.active_jobs)
            )
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)

        assert [row["job_id"] for row in observed] == [first["id"], second["id"]]
        assert [row["context_job_id"] for row in observed] == [first["id"], second["id"]]
        assert observed[0]["run_id"] != observed[1]["run_id"]
        assert observed[0]["scene"] == {"scene_index": 0, "subject": "Alpha"}
        assert observed[1]["scene"] == {"scene_index": 0, "subject": "Beta"}
        assert observed[0]["title"] == "Alpha Subject"
        assert observed[0]["output_name"] == "alpha-output"
        assert observed[1]["title"] == "Beta Subject"
        assert observed[1]["output_name"] == "beta-output"

        stored_second = manager.get_item(second["id"])
        assert stored_second["status"] == JobStatus.COMPLETED
        assert stored_second["config"]["scene_contracts"][0]["subject"] == "Beta"
        assert "_queue_job_id" not in stored_second["config"]
        assert "_queue_run_id" not in stored_second["config"]
        assert manager.active_jobs == {}
    finally:
        manager.stop_worker()
        _restore(original)
