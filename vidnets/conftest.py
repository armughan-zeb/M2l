import pytest
import importlib
import importlib.util
import os
import sys
import time
from pathlib import Path

_RENDER_GATE_MODULE = None


def _load_render_gate():
    """Load services/render_gate.py by file path (no package import, so
    the gate never pulls the server in at session start)."""
    global _RENDER_GATE_MODULE
    if _RENDER_GATE_MODULE is not None:
        return _RENDER_GATE_MODULE
    gate_path = Path(__file__).resolve().parent / "server" / "services" / "render_gate.py"
    spec = importlib.util.spec_from_file_location("vidnets_render_gate", gate_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _RENDER_GATE_MODULE = module
    return module


def pytest_sessionstart(session):
    """Render gate: a render in progress owns the machine (Berlin Wall,
    Nov 15 — the suite ran through a render at ~160 MB free until pytest
    was killed by hand). If a render is active, wait for it (bounded by
    VIDZEO_RENDER_GATE_WAIT_SECONDS, default 1200s) instead of starting
    heavy test work on top of it. On timeout, proceed with a loud
    warning — a test run must never hang forever, and it must never
    fail the suite because a render exists. VIDZEO_RENDER_GATE=off
    skips the wait entirely. Any gate error fails open: note it and
    run the suite."""
    try:
        gate = _load_render_gate()
        if gate is None:
            return
        if not gate.render_gate_enabled():
            return
        status = gate.is_render_active()
        if not status["active"]:
            return
        wait_seconds = gate.render_gate_wait_seconds()
        print(
            "[RenderGate] render in progress ("
            + "; ".join(status["signals"])
            + f") — waiting up to {wait_seconds:.0f}s for the machine to free up "
            "(VIDZEO_RENDER_GATE_WAIT_SECONDS to change, VIDZEO_RENDER_GATE=off to skip)",
            flush=True,
        )
        last_notice = time.monotonic()
        started = time.monotonic()

        def _on_wait(wait_status, waited):
            nonlocal last_notice
            if time.monotonic() - last_notice >= 30.0:
                last_notice = time.monotonic()
                print(
                    "[RenderGate] still waiting for render to finish "
                    f"({waited:.0f}s of {wait_seconds:.0f}s; "
                    + "; ".join(wait_status["signals"])
                    + ")",
                    flush=True,
                )

        result = gate.wait_for_render_idle(
            timeout_seconds=wait_seconds, poll_seconds=5.0, on_wait=_on_wait
        )
        if result["idle"]:
            print(
                f"[RenderGate] render finished after {result['waitedSeconds']:.0f}s; starting tests",
                flush=True,
            )
        else:
            print(
                "[RenderGate] WARNING: render still active after "
                f"{result['waitedSeconds']:.0f}s ("
                + "; ".join(result["status"]["signals"])
                + ") — proceeding anyway; the render will stay starved, "
                "re-check before heavy work",
                flush=True,
            )
    except Exception as exc:
        print(f"[RenderGate] gate check failed ({exc}); proceeding without it", flush=True)


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
