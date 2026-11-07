import pytest
import importlib
import sys


@pytest.fixture(autouse=True)
def _isolate_web_safety_mode(monkeypatch):
    """A local app preference must not alter the baseline safety tests."""
    monkeypatch.setenv('VIDZEO_WEB_DISABLE_VISUAL_SAFETY', '0')


@pytest.fixture(autouse=True)
def _quarantine_real_queue_persistence(tmp_path, monkeypatch):
    """Redirect queue persistence away from the production server/queue.json.

    Tests that exercise /api/queue/add would otherwise write real queue items
    into the user's live queue file.  Point the module-level QUEUE_FILE at a
    per-test temp path (it is resolved at call time inside the manager).
    """
    queue_file = tmp_path / "test_queue.json"
    for module_name in ("services.queue_manager", "server.services.queue_manager"):
        try:
            queue_manager = sys.modules.get(module_name) or importlib.import_module(module_name)
            monkeypatch.setattr(queue_manager, "QUEUE_FILE", queue_file)
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _quarantine_real_queue_generation(monkeypatch):
    """Keep the real generation pipeline out of unit tests.

    The queue manager is a process-wide singleton: a test that adds a queue
    item starts a background worker that runs the REAL pipeline in-process.
    TTS/whisper stages swallow cancellation, so that thread can keep running
    for minutes — long enough to land inside a later test's mocked pipeline
    and corrupt its assertions (e.g. the wiring tests' _resolve_media_paths
    mock).  Stub the queue generation callback so queued items complete
    instantly in tests, and stop/cancel any worker at teardown.
    """

    def _stub_generation(config, _progress=None, _check_cancelled=None, _cancellation=None):
        return {"success": True, "video": "", "video_url": "", "name": config.get("name") or "Video"}

    monkeypatch.setattr("app._queue_generation_callback", _stub_generation)
    yield
    for module_name in ("services.queue_manager", "server.services.queue_manager"):
        try:
            module = sys.modules.get(module_name) or importlib.import_module(module_name)
            qm = module.get_queue_manager()
            for job_id, active in list(getattr(qm, "active_jobs", {}) or {}).items():
                try:
                    context = active.get("context")
                    if context:
                        context.cancel()
                except Exception:
                    pass
            qm.stop_worker()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _quarantine_real_search_cache(tmp_path, monkeypatch):
    """Keep the yt-dlp search disk cache out of unit tests.

    The search cache (temp/yt_search_cache) is a production throttle shield.
    A test that runs a mocked search stores its results there, and a later
    test that repeats the same query then serves from disk with zero yt-dlp
    instances - breaking instance-count assertions and hiding code paths.
    Point the cache at a per-test temp directory instead.
    """
    cache_dir = tmp_path / "yt_search_cache"
    monkeypatch.setenv("VIDZEO_YT_SEARCH_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("VIDZEO_YT_CHAPTER_CACHE_DIR", str(tmp_path / "yt_chapter_cache"))
