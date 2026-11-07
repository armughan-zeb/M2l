import inspect
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

import app as server_app
from services import remotion_render_gate
from services.parallel_director_pipeline import ParallelDirectorPipeline
from services import queue_manager as queue_manager_module
from services.queue_manager import QueueManager


def test_remotion_render_slot_serializes_concurrent_video_exports(monkeypatch, tmp_path):
    # Exercise serialization without waiting on a real desktop export owned
    # by another process during the release acceptance test.
    monkeypatch.setenv('VIDNETS_RENDER_LOCK_FILE', str(tmp_path / 'render.lock'))
    shared_gate = threading.Semaphore(1)
    monkeypatch.setattr(server_app, "REMOTION_RENDER_GATE", shared_gate)
    monkeypatch.setattr(remotion_render_gate, "REMOTION_RENDER_GATE", shared_gate)
    first_entered = threading.Event()
    second_started = threading.Event()
    second_entered = threading.Event()
    release_first = threading.Event()
    state_lock = threading.Lock()
    active_renders = 0
    max_active_renders = 0

    def render(label):
        nonlocal active_renders, max_active_renders
        with state_lock:
            active_renders += 1
            max_active_renders = max(max_active_renders, active_renders)
        try:
            if label == "first":
                first_entered.set()
                assert release_first.wait(3)
            else:
                second_entered.set()
            return label
        finally:
            with state_lock:
                active_renders -= 1

    def run_first():
        return server_app._run_with_remotion_render_slot(lambda: render("first"))

    def run_second():
        second_started.set()
        # Simulates the parallel director pipeline, which enters through the
        # shared service gate rather than the Flask queue wrapper.
        return remotion_render_gate.run_with_remotion_render_slot(lambda: render("second"))

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(run_first)
        assert first_entered.wait(2)
        second = executor.submit(run_second)
        assert second_started.wait(2)
        try:
            assert not second_entered.wait(0.15)
        finally:
            release_first.set()
        assert first.result(timeout=3) == "first"
        assert second.result(timeout=3) == "second"

    assert max_active_renders == 1


def test_queue_retry_and_editor_render_paths_share_the_serialized_slot():
    assert "_run_with_remotion_render_slot(" in inspect.getsource(
        server_app._render_video_with_semantic_editing
    )
    assert "_run_with_remotion_render_slot(" in inspect.getsource(
        server_app._resume_checkpointed_remotion_render_impl
    )
    assert "_run_with_remotion_render_slot(" in inspect.getsource(
        server_app._api_editor_render_impl
    )
    assert "run_with_remotion_render_slot(" in inspect.getsource(
        server_app._resume_checkpointed_remotion_render_impl
    )
    assert "_run_job_scoped(" in inspect.getsource(server_app._resume_checkpointed_remotion_render)
    assert "_resume_checkpointed_remotion_render_impl(" in inspect.getsource(server_app._resume_checkpointed_remotion_render)
    assert "run_with_remotion_render_slot(" in inspect.getsource(
        ParallelDirectorPipeline.render_timeline
    )


def test_completed_queue_paths_are_json_safe_in_compact_and_full_status():
    manager = QueueManager.__new__(QueueManager)
    manager._queue_lock = threading.RLock()
    manager.queue = [{
        "id": "completed-job",
        "status": "completed",
        "result": {
            "video_path": Path(r"C:\\exports\\finished.mp4"),
            "manifest_path": Path(r"C:\\exports\\manifest.json"),
        },
    }]
    manager.worker_running = False
    manager.worker_count = 1
    manager.active_jobs = {}

    compact = manager.get_status(compact=True)
    full = manager.get_status(compact=False)
    assert json.loads(json.dumps(compact))["items"][0]["result"]["video_path"].endswith("finished.mp4")
    assert json.loads(json.dumps(full))["items"][0]["result"]["video_path"].endswith("finished.mp4")


def test_queue_persistence_serializes_windows_path_values(monkeypatch, tmp_path):
    queue_file = tmp_path / "queue.json"
    monkeypatch.setattr(queue_manager_module, "QUEUE_FILE", queue_file)
    manager = QueueManager.__new__(QueueManager)
    manager._persistence_lock = threading.RLock()
    manager._queue_lock = threading.RLock()
    manager._save_tick = 0
    manager._full_backup_interval = 100
    manager.queue = [{"id": "finished", "result": {"video_path": Path(r"C:\\exports\\finished.mp4")}}]

    manager._save_queue()

    saved = json.loads(queue_file.read_text(encoding="utf-8"))
    assert saved["items"][0]["result"]["video_path"].endswith("finished.mp4")
