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

    # No 90s scene wall anymore (Nov 16): an explicit operator wall is
    # honoured as-is (no hard cap), the stage deadline binds when it is
    # earlier, and with neither set the scene has no wall-clock deadline
    # at all — liveness is the trip.
    monkeypatch.setenv("VIDZEO_WEB_SCENE_DEADLINE_SECONDS", "9999")
    assert _scene_deadline(1000.0, now=100.0) == 1000.0
    assert _scene_deadline(20000.0, now=100.0) == 10099.0
    assert _scene_deadline(125.0, now=100.0) == 125.0
    monkeypatch.delenv("VIDZEO_WEB_SCENE_DEADLINE_SECONDS", raising=False)
    assert _scene_deadline(1000.0, now=100.0) == 1000.0
    assert _scene_deadline(None, now=100.0) is None


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

# ── GTA web-lane fix (Nov 16): wave waits, late harvest, liveness ──────────

def _fake_row(title="Test Subject documentary photo"):
    return {
        "url": "http://127.0.0.1:9/test.jpg",
        "image_url": "http://127.0.0.1:9/test.jpg",
        "title": title,
        "source": "universal_scraper",
        "provider": "universal_yandex",
        "query": "test query",
        "tags": "",
        "description": "",
        "source_url": "",
        "page_url": "",
        "preview_url": "",
        "thumbnail_url": "",
        "domain": "",
    }


def _resolve_scene_for_wave_test(monkeypatch, tmp_path, *, scene_index, discover, env=None):
    """Drive one _resolve_scene with a fake transport; return (result, telemetry, elapsed)."""
    import threading

    from services.web_image import resolver
    from services.web_image.source_broker import SourceTelemetry

    monkeypatch.setenv("VIDZEO_UNIVERSAL_SCRAPER_SCENE_FALLBACK", "1")
    monkeypatch.setenv("VIDZEO_UNIVERSAL_LATE_WAIT_SECONDS", "0")
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(resolver, "_discover_source_query", discover)
    monkeypatch.setattr(
        resolver,
        "_enabled_fast_sources",
        lambda: [
            ("universal_scraper", "universal"),
            ("yandex", "api"),
            ("flickr_api", "flickr_api"),
        ],
    )
    # The resolver's module-global query cache persists across tests in one
    # process: a row cached by an earlier wave test would leak into the next
    # test's wave and defeat the slow-transport fixture. Clear it per test.
    # (The universal late spool is deliberately NOT cleared here — the
    # harvest test pre-seeds it before calling this helper.)
    with resolver._CACHE_LOCK:
        resolver._SEARCH_QUERY_CACHE.clear()
    with resolver._WEB_WAVE_PROGRESS_LOCK:
        resolver._WEB_WAVE_PROGRESS.clear()
    telemetry = SourceTelemetry()
    started = time.monotonic()
    result = resolver._resolve_scene(
        scene_index,
        "test scene text",
        ["test query"],
        None,
        tmp_path,
        "Test Subject",
        {},
        {},
        set(),
        threading.Lock(),
        threading.Lock(),
        threading.Lock(),
        source_telemetry=telemetry,
    )
    return result, telemetry, time.monotonic() - started


def test_wave_never_cancels_live_universal_future(monkeypatch, tmp_path):
    """(a) A wave with a 0.15s deadline still waits for a 0.6s universal batch."""
    from services.web_image import resolver

    calls = {"universal_scraper": 0}

    def discover(source_name, source_type, query, gallery_cmd=None, max_timeout=None, wait_until_complete=False):
        if source_name == "universal_scraper":
            calls["universal_scraper"] += 1
            if calls["universal_scraper"] == 1:
                time.sleep(0.6)
                return [_fake_row()]
            return []
        return []

    _result, telemetry, elapsed = _resolve_scene_for_wave_test(
        monkeypatch,
        tmp_path,
        scene_index=0,
        discover=discover,
        env={"VIDZEO_WEB_QUERY_DEADLINE_SECONDS": "0.15"},
    )
    providers = telemetry.snapshot().get("providers", {})
    assert providers.get("universal_scraper", {}).get("discovered", 0) >= 1
    assert elapsed >= 0.6, f"wave returned after {elapsed:.2f}s, universal needed 0.6s"
    assert resolver.web_wave_stalled_for_scene(1) is False


def test_scene_level_late_spool_harvest_before_broadened_round(monkeypatch, tmp_path):
    """(c) Rows spooled by a late engine are recovered by the pre-broadened harvest."""
    from services.web_image import resolver
    from services.web_image.universal_scraper import UniversalImageResult

    resolver._UNIVERSAL_SCRAPER._spool_late_results(
        "test query",
        "yandex",
        [
            UniversalImageResult(
                title="Test Subject documentary photo",
                image_url="http://127.0.0.1:9/seed.jpg",
                source_engine="Yandex",
            )
        ],
    )

    def discover(source_name, source_type, query, gallery_cmd=None, max_timeout=None, wait_until_complete=False):
        return []  # every live wave finds nothing; only the spool has rows

    _result, telemetry, _elapsed = _resolve_scene_for_wave_test(
        monkeypatch,
        tmp_path,
        scene_index=3,
        discover=discover,
        env={"VIDZEO_WEB_QUERY_DEADLINE_SECONDS": "0.15"},
    )
    providers = telemetry.snapshot().get("providers", {})
    assert providers.get("universal_scraper", {}).get("discovered", 0) >= 1


def test_web_wave_stalled_after_liveness_window_without_progress(monkeypatch, tmp_path):
    """(e) A wave with zero progress for the full window is flagged web_retrieval_stalled."""
    from services.web_image import resolver

    def discover(source_name, source_type, query, gallery_cmd=None, max_timeout=None, wait_until_complete=False):
        if source_name == "universal_scraper":
            calls["universal_scraper"] += 1
            if calls["universal_scraper"] == 1:
                time.sleep(3.0)
                return [_fake_row()]
            return []
        time.sleep(0.05)
        return []

    calls = {"universal_scraper": 0}
    monkeypatch.setattr(resolver, "_web_liveness_window_seconds", lambda: 0.5)
    _result, _telemetry, elapsed = _resolve_scene_for_wave_test(
        monkeypatch,
        tmp_path,
        scene_index=1,
        discover=discover,
        env={"VIDZEO_WEB_QUERY_DEADLINE_SECONDS": "0.15"},
    )
    assert elapsed < 2.5, f"stalled wave hung for {elapsed:.2f}s instead of giving up"
    assert resolver.web_wave_stalled_for_scene(2) is True
    stalled = [
        rec
        for rec in resolver.web_wave_progress_snapshot().values()
        if rec.get("stalled")
    ]
    assert stalled and all(
        rec.get("stall_reason") == "web_retrieval_stalled" for rec in stalled
    )


def test_web_wave_with_trickling_progress_is_not_stalled(monkeypatch, tmp_path):
    """(e) A wave whose engines keep completing is NOT flagged, however slow."""
    from services.web_image import resolver

    def discover(source_name, source_type, query, gallery_cmd=None, max_timeout=None, wait_until_complete=False):
        if source_name == "universal_scraper":
            time.sleep(0.9)
            return [_fake_row()]
        if source_name == "yandex":
            time.sleep(0.3)
            return []
        time.sleep(0.55)
        return []

    monkeypatch.setattr(resolver, "_web_liveness_window_seconds", lambda: 0.8)
    _result, telemetry, elapsed = _resolve_scene_for_wave_test(
        monkeypatch,
        tmp_path,
        scene_index=2,
        discover=discover,
        env={"VIDZEO_WEB_QUERY_DEADLINE_SECONDS": "0.15"},
    )
    providers = telemetry.snapshot().get("providers", {})
    assert providers.get("universal_scraper", {}).get("discovered", 0) >= 1
    assert elapsed >= 0.9, f"wave returned after {elapsed:.2f}s, universal needed 0.9s"
    assert resolver.web_wave_stalled_for_scene(3) is False


def test_scene_wall_never_cuts_progressing_wave(monkeypatch, tmp_path):
    """(GTA fix, Nov 16) The removed 90s scene wall: a universal batch that
    is still progressing past the old 90s cap is NOT cut off. The scene
    clock is faked 200s forward once the wave exists (past the old cap);
    the wave must still wait for the 0.9s batch and take its row live —
    not via the late spool, and not flagged stalled. A zero-progress wave
    is still flagged by the liveness watchdog (see
    test_web_wave_stalled_after_liveness_window_without_progress)."""
    import time as _time

    from services.web_image import resolver

    real_monotonic = _time.monotonic
    t0 = real_monotonic()

    def _jumped_monotonic():
        now = real_monotonic()
        # Jump only once a wave record exists, so the scene deadline is
        # captured pre-jump and the wave then ages 200s in one step.
        if resolver._WEB_WAVE_PROGRESS and now - t0 > 0.1:
            return now + 200.0
        return now

    monkeypatch.setattr(_time, "monotonic", _jumped_monotonic)
    # Liveness must not trip on the jump: only the scene wall is under test.
    monkeypatch.setattr(resolver, "_web_liveness_window_seconds", lambda: 3600.0)

    def discover(source_name, source_type, query, gallery_cmd=None, max_timeout=None, wait_until_complete=False):
        if source_name == "universal_scraper":
            _time.sleep(0.9)
            return [_fake_row()]
        return []

    real_started = real_monotonic()
    _result, telemetry, _elapsed = _resolve_scene_for_wave_test(
        monkeypatch,
        tmp_path,
        scene_index=4,
        discover=discover,
        env={"VIDZEO_WEB_QUERY_DEADLINE_SECONDS": "0.15"},
    )
    real_elapsed = real_monotonic() - real_started
    providers = telemetry.snapshot().get("providers", {})
    assert providers.get("universal_scraper", {}).get("discovered", 0) >= 1
    assert real_elapsed >= 0.9, (
        f"wave returned after {real_elapsed:.2f}s real, universal needed 0.9s"
    )
    # The row arrived through the LIVE wave, not the late-spool harvest:
    # under the old 90s wall the wave broke at the jump and rows_arrived
    # stayed 0.
    wave_rows = [
        rec.get("rows_arrived", 0)
        for rec in resolver.web_wave_progress_snapshot().values()
        if int(rec.get("scene_number") or 0) == 5
    ]
    assert wave_rows and max(int(v) for v in wave_rows) >= 1
    assert resolver.web_wave_stalled_for_scene(5) is False


def test_mixed_web_scene_worker_limit_defaults_to_two(monkeypatch):
    """(d) The stampede cap: 2 scene workers by default; legacy 6/12 no longer wins."""
    from services.pipeline_v2_media_adapter import _mixed_web_scene_worker_limit

    monkeypatch.delenv("VIDZEO_MIXED_WEB_SCENE_WORKERS", raising=False)
    assert _mixed_web_scene_worker_limit({}) == 2
    assert _mixed_web_scene_worker_limit({"web_max_scene_workers": 6}) == 2
    assert _mixed_web_scene_worker_limit({"web_max_scene_workers": 12}) == 2
    # An explicit operator raise via env still wins.
    monkeypatch.setenv("VIDZEO_MIXED_WEB_SCENE_WORKERS", "6")
    assert _mixed_web_scene_worker_limit({}) == 6
    monkeypatch.setenv("VIDZEO_MIXED_WEB_SCENE_WORKERS", "1")
    assert _mixed_web_scene_worker_limit({"web_max_scene_workers": 6}) == 1


def test_first_wave_is_tier_one_and_families_deduped():
    """(d) First wave = the 4 real transports; at most one Bing lane."""
    from services.web_image import resolver

    assert set(resolver._TIER_ONE_SOURCES) == {
        "universal_scraper",
        "yandex",
        "flickr_api",
        "bing_hybrid",
    }
    names = [name for name, _kind in resolver._dedupe_transport_families(list(resolver._FAST_SOURCES))]
    assert names.count("bing_hybrid") == 1
    assert "primary_archive" not in names and "getty_bing" not in names
    assert "ddg_hybrid" not in names
    assert "getty_ddg" not in names
    assert names[0] == "universal_scraper"
    # Without the plain lane, the first listed family member is kept.
    kept = resolver._dedupe_transport_families(
        [("primary_archive", "hybrid"), ("getty_bing", "hybrid")]
    )
    assert [name for name, _kind in kept] == ["primary_archive"]
