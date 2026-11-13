import threading
import time
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _server_import_path():
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
    yield
    sys.path.pop(0)


def test_configured_web_stage_budget_is_hard_capped(monkeypatch):
    from services.web_image.resolver import _WEB_STAGE_HARD_CAP_SECONDS, _bounded_web_seconds

    monkeypatch.setenv("VIDZEO_WEB_STAGE_DEADLINE_SECONDS", "9999")
    actual = _bounded_web_seconds(
        "9999", 45.0, _WEB_STAGE_HARD_CAP_SECONDS
    )
    assert actual == 180.0


def test_scene_deadline_starts_at_worker_execution_and_respects_stage(monkeypatch):
    from services.web_image.resolver import _scene_deadline

    monkeypatch.setenv("VIDZEO_WEB_SCENE_DEADLINE_SECONDS", "9999")
    assert _scene_deadline(1000.0, now=100.0) == 190.0
    assert _scene_deadline(125.0, now=100.0) == 125.0


def test_wait_mode_shared_provider_pool_returns_at_batch_deadline(monkeypatch):
    from services.web_image import resolver

    finished = threading.Event()

    def slow_source(*_args, **_kwargs):
        try:
            time.sleep(0.6)
            return []
        finally:
            finished.set()

    monkeypatch.setattr(resolver, "_enabled_fast_sources", lambda: [("slow", "api")])
    monkeypatch.setattr(resolver, "_discover_source_query", slow_source)
    started = time.monotonic()
    result = resolver._warm_shared_discovery_pool(
        query="bounded test",
        scene_count=2,
        gallery_cmd=None,
        discovery_cache=None,
        source_telemetry=None,
        stage_deadline_at=started + 2.0,
        batch_seconds_override=0.05,
        wait_until_complete=True,
    )

    assert time.monotonic() - started < 0.45
    assert result["providers_pending"] == 1
    assert finished.wait(1.0)
