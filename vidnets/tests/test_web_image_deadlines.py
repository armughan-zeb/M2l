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
    assert actual == 1800.0


def test_web_stage_default_scales_with_scene_count(monkeypatch):
    # The old fixed 180s ceiling cut a 121-scene job off after ~35 scenes.
    # The default wall is now 12s per scene (completion-scaled).
    from services.web_image.resolver import _default_web_stage_deadline_seconds

    monkeypatch.delenv("VIDZEO_WEB_STAGE_HARD_CAP_SECONDS", raising=False)
    assert _default_web_stage_deadline_seconds(121) == 1452.0
    assert _default_web_stage_deadline_seconds(2) == 45.0
    assert _default_web_stage_deadline_seconds(500) == 1800.0


def test_web_stage_hard_cap_is_env_tunable(monkeypatch):
    from services.web_image.resolver import _web_stage_hard_cap_seconds

    monkeypatch.setenv("VIDZEO_WEB_STAGE_HARD_CAP_SECONDS", "600")
    assert _web_stage_hard_cap_seconds() == 600.0
    monkeypatch.setenv("VIDZEO_WEB_STAGE_HARD_CAP_SECONDS", "60")
    assert _web_stage_hard_cap_seconds() == 180.0


def test_mixed_web_wall_grows_with_remaining_scenes(monkeypatch):
    # A 121-scene job gets its scene-scaled wall (121 * 6s); a 5-scene job
    # keeps its short wall; the delivery budget still caps the wall.
    from services.pipeline_v2_media_adapter import _mixed_web_wall_seconds

    monkeypatch.delenv("VIDZEO_MIXED_WEB_COMPLETION_CAP_SECONDS", raising=False)
    monkeypatch.delenv("VIDZEO_MIXED_WEB_PER_SCENE_SECONDS", raising=False)
    assert _mixed_web_wall_seconds(121, 120.0, 120.0, 1800.0) == 726.0
    assert _mixed_web_wall_seconds(5, 120.0, 120.0, 1800.0) == 120.0
    assert _mixed_web_wall_seconds(24, 120.0, 120.0, 1800.0) == 144.0
    assert _mixed_web_wall_seconds(500, 120.0, 120.0, 1800.0) == 1800.0
    assert (
        _mixed_web_wall_seconds(121, 120.0, 120.0, 1800.0, budget_allowance_seconds=300.0)
        == 300.0
    )


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
