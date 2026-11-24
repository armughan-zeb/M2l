"""Spec alignment (Nov 22 2026): Vidzeo Local spec values, Muse inference.

The spec's OpenRouter models are replaced by Muse inference
(external_agent, compulsory), but every timeout, budget, niche weight
and gate threshold in the spec is copied verbatim into this repo.
These tests pin the spec numbers so a later tuning cannot silently
re-inflate a budget or loosen a gate.
"""
import os

import pytest

from server.services.delivery_budget import install_delivery_budget
from server.services.youtube_discovery_budget import (
    discovery_branch_wall_seconds,
    discovery_budget_seconds,
)
from server.services.visual_intelligence.frame_extractor import (
    _scene_split_timestamps,
)
from server.services.visual_intelligence import prefilter


class FakeClock:
    def __init__(self, value=100.0):
        self.value = float(value)

    def __call__(self):
        return self.value


def test_delivery_formula_spec_section_3_1(monkeypatch):
    """T_audio = W/2.3; T_total = max(900, min(7200, 2.5*T_audio+600)).

    GTA example in the spec: 1668 words -> T_audio ~= 725s, so
    T_total ~= 2412s and T_reserve = max(330, min(1800, 2412*0.35)) ~= 844s.
    """
    monkeypatch.delenv("VIDZEO_VIDEO_DELIVERY_BUDGET_SECONDS", raising=False)
    monkeypatch.delenv("VIDZEO_RENDER_RESERVE_SECONDS", raising=False)
    budget = install_delivery_budget(
        {"script": ("word " * 1668).strip()}, clock=FakeClock()
    )
    assert budget.total_seconds == pytest.approx(2412.5, abs=1.0)
    assert budget.render_reserve_seconds == pytest.approx(844.0, abs=1.0)
    assert budget.absolute_cap_seconds == 7200.0


def test_delivery_formula_short_job_is_900s(monkeypatch):
    monkeypatch.delenv("VIDZEO_VIDEO_DELIVERY_BUDGET_SECONDS", raising=False)
    monkeypatch.delenv("VIDZEO_RENDER_RESERVE_SECONDS", raising=False)
    budget = install_delivery_budget(
        {"script": ("word " * 200).strip()}, clock=FakeClock()
    )
    assert budget.total_seconds == 900.0
    assert budget.render_reserve_seconds == 330.0


def test_discovery_capped_at_branch_wall_1200(monkeypatch):
    monkeypatch.delenv("VIDZEO_MIXED_YT_BRANCH_WALL_TIMEOUT_SECONDS", raising=False)
    assert discovery_branch_wall_seconds({}) == 1200.0
    assert discovery_budget_seconds(1) == 300.0
    assert discovery_budget_seconds(10) == 1200.0


def test_query_workers_default_is_four(monkeypatch):
    # Spec (Vidzeo Local thread pools): Query Brain 3-4. The Nov 22
    # spec alignment set 8; the user ordered the spec values applied
    # verbatim over that local tuning.
    from server.services.scene_query_brain import _stock_query_workers

    monkeypatch.delenv("VIDZEO_STOCK_LLM_QUERY_WORKERS", raising=False)
    assert _stock_query_workers() == 4
    monkeypatch.setenv("VIDZEO_STOCK_LLM_QUERY_WORKERS", "99")
    assert _stock_query_workers() == 4


def test_thread_pools_spec_values(monkeypatch):
    """Spec (Vidzeo Local thread pools), verbatim: Web Retrieval 6-12,
    Vision 4-8, Coordinator 8, YT Clip Workers 2-4."""
    from server.services.media_resolution_coordinator import (
        MediaResolutionCoordinator,
        _visual_gate_workers,
        resolve_storyboard_media_v2,
    )
    from server.services.pipeline_v2_media_adapter import (
        _mixed_web_scene_worker_limit,
    )
    from server.services.youtube_pipeline_v2 import (
        _requested_download_workers,
        mixed_youtube_download_worker_capacity,
    )
    import inspect

    # Web Retrieval 6-12: default 8, inside the range.
    monkeypatch.delenv("VIDZEO_MIXED_WEB_SCENE_WORKERS", raising=False)
    assert _mixed_web_scene_worker_limit({}) == 8
    monkeypatch.setenv("VIDZEO_MIXED_WEB_SCENE_WORKERS", "99")
    assert _mixed_web_scene_worker_limit({}) == 12

    # Vision 4-8: default 8, clamped at 8.
    monkeypatch.delenv("VIDZEO_VISUAL_GATE_WORKERS", raising=False)
    assert _visual_gate_workers(38) == 8
    monkeypatch.setenv("VIDZEO_VISUAL_GATE_WORKERS", "99")
    assert _visual_gate_workers(38) == 8

    # Coordinator 8: both the class default and the V2 entry point.
    assert inspect.signature(MediaResolutionCoordinator.__init__).parameters[
        "max_workers"
    ].default == 8
    assert inspect.signature(resolve_storyboard_media_v2).parameters[
        "max_workers"
    ].default == 8

    # YT Clip Workers 2-4: default 4, clamped at 4.
    monkeypatch.delenv("VIDZEO_YT_DOWNLOAD_CONCURRENCY", raising=False)
    monkeypatch.delenv("VIDZEO_MIXED_YT_DOWNLOAD_CONCURRENCY", raising=False)
    assert _requested_download_workers() == 4
    assert mixed_youtube_download_worker_capacity({}) == 4
    monkeypatch.setenv("VIDZEO_YT_DOWNLOAD_CONCURRENCY", "99")
    assert _requested_download_workers() == 4


def test_download_limiter_spec_values():
    """Spec (Vidzeo Local): DownloadLimiter 12 total, max 3 per host,
    30s cooldown on 429."""
    import inspect

    from server.services.web_image.hybrid_search import (
        _DOWNLOAD_LIMITER,
        DownloadLimiter,
    )

    assert _DOWNLOAD_LIMITER.total == 12
    assert _DOWNLOAD_LIMITER.per_host == 3
    assert inspect.signature(DownloadLimiter.defer).parameters[
        "seconds"
    ].default == 30


def test_yt_min_source_duration_is_20s(monkeypatch):
    """Spec (Vidzeo Local): YouTube min source duration 20s."""
    from server.services.youtube_pipeline_v2 import _hard_block_reason

    monkeypatch.delenv("VIDZEO_YT_MIN_SOURCE_DURATION", raising=False)
    assert _hard_block_reason({"duration": 15.0}) == "too_short"
    assert _hard_block_reason({"duration": 19.9}) == "too_short"
    assert _hard_block_reason({"duration": 20.0}) == ""
    assert _hard_block_reason({"duration": 25.0}) == ""
    monkeypatch.setenv("VIDZEO_YT_MIN_SOURCE_DURATION", "12")
    assert _hard_block_reason({"duration": 15.0}) == ""


def test_pool_slice_clamped_to_4_to_8s():
    """Spec (Vidzeo Local): 4-8s FFmpeg slice around the matched moment."""
    from server.services.youtube_pool_renderer import _pool_slice_seconds

    assert _pool_slice_seconds([2.6, 3.4], 0) == 4.0
    assert _pool_slice_seconds([2.6, 3.4], 1) == 4.0
    assert _pool_slice_seconds([6.0], 0) == 6.0
    assert _pool_slice_seconds([12.0], 0) == 8.0
    assert _pool_slice_seconds([], 0) == 4.0


def test_web_source_order_spec(monkeypatch):
    """Spec (Vidzeo Local) web source order, verbatim — as amended by the
    Nov 22, 2026 muse-search change (test-30 fix): the muse_search wave
    (Openverse + Wikimedia JSON APIs) runs FIRST, before Brave (no key,
    so it stays skipped), Yandex, Google Images, then UniversalScraper.
    The standalone wikimedia/openverse lanes are folded into muse_search,
    so they no longer appear as separate first-wave entries."""
    from server.services.web_image.resolver import (
        _FAST_SOURCES,
        _TIER_ONE_SOURCES,
    )
    from server.services.web_image.source_broker import (
        WEB_IMAGE_SOURCE_CATALOG,
    )

    names = [name for name, _ in _FAST_SOURCES]
    assert names[:6] == [
        "muse_search",
        "brave_images",
        "yandex",
        "google_images",
        "wikimedia",
        "openverse",
    ]
    assert _TIER_ONE_SOURCES == (
        "muse_search",
        "yandex",
        "google_images",
        "universal_scraper",
    )
    tier_zero = {
        entry["name"]
        for entry in WEB_IMAGE_SOURCE_CATALOG
        if entry["tier"] == 0
    }
    assert tier_zero == {"muse_search", "brave_images", "yandex", "google_images"}


def test_prefilter_spec_section_7():
    # Resolution > 720p on the long edge; pHash duplicate < 6.
    assert prefilter.MIN_LONG_EDGE == 720
    assert prefilter.PHASH_DUPLICATE_HAMMING == 5


def test_keyframes_spec_section_7():
    # Normal scene: 5 frames at 10/30/50/70/90% of the clip.
    stamps = _scene_split_timestamps(10.0)
    assert len(stamps) == 5
    assert stamps == pytest.approx([1.0, 3.0, 5.0, 7.0, 9.0])
    # Short scene keeps 10/50/90%.
    assert _scene_split_timestamps(3.0) == pytest.approx([0.3, 1.5, 2.7])


def test_niche_weights_spec_section_5_1():
    from server.services import mixed_media_planner as planner

    assert planner._automatic_mode_weights("gaming")["YouTube Clips"] == pytest.approx(0.80)
    assert planner._automatic_mode_weights("sports")["YouTube Clips"] == pytest.approx(0.75)
    assert planner._automatic_mode_weights("history")["YouTube Clips"] == pytest.approx(0.65)
    assert planner._automatic_mode_weights("documentary")["YouTube Clips"] == pytest.approx(0.60)
    assert planner._automatic_mode_weights("crime")["YouTube Clips"] == pytest.approx(0.60)
    lifestyle = planner._automatic_mode_weights("lifestyle")
    assert lifestyle["YouTube Clips"] == pytest.approx(0.20)
    assert lifestyle["Web Images"] == pytest.approx(0.80)
    assert lifestyle["Stock Media"] == pytest.approx(0.00)


def test_media_wall_floor_is_300_not_3600(monkeypatch):
    """Non-render speed fix (Nov 22 2026): the coordinator wall floor.

    The old 3600s floor gave test 22's 13-scene coordinator a 3300s
    working deadline (3600s wall minus the 300s vision reserve)
    against a 412s discovery+tail budget — the wall never bound
    anything. With the floor at 300s (one inbox round trip), a 1-min
    external-agent job's wall is the computed/delivery-allowance
    value: 1170s (delivery total 1800 minus render reserve 630, the
    spec 35% reserve).
    """
    from server.services.delivery_budget import install_delivery_budget
    from server.services.pipeline_v2_media_adapter import (
        _media_stage_deadline_seconds,
    )

    monkeypatch.delenv("VIDZEO_AGENT_MIN_WALL_SECONDS", raising=False)
    monkeypatch.delenv("VIDZEO_MEDIA_DEADLINE_SECONDS", raising=False)
    monkeypatch.delenv("VIDZEO_MIXED_YT_BRANCH_WALL_TIMEOUT_SECONDS", raising=False)
    config = {"script": ("word " * 265).strip(), "llm_provider": "external_agent"}
    install_delivery_budget(config, clock=FakeClock())
    assert _media_stage_deadline_seconds(config, "mixed media") == 1170.0

    plain = {"script": ("word " * 265).strip()}
    install_delivery_budget(plain, clock=FakeClock())
    assert _media_stage_deadline_seconds(plain, "mixed media") == 570.0


def test_pool_transcript_deadline_capped_at_300(monkeypatch):
    """Round 12 (Nov 23): the transcript deadline cap is 300s, not
    120s — the 120s cap retained 0/8 transcripts in job 9ab2eb05's
    slow hour, so the lane reached zero downloads with 53 ranked
    candidates in hand. A 300s batch covers the measured cost
    (per-video ~8s good hour / ~45s slow hour at 6 workers)."""
    from server.services.youtube_pipeline_v2 import (
        _yt_pool_transcript_deadline_seconds,
        _yt_ranged_transcript_deadline_seconds,
    )

    monkeypatch.setenv("VIDZEO_YT_TRANSCRIPT_DEADLINE_SECONDS", "300")
    assert _yt_pool_transcript_deadline_seconds() == 300.0
    assert _yt_ranged_transcript_deadline_seconds() == 300.0
    monkeypatch.delenv("VIDZEO_YT_TRANSCRIPT_DEADLINE_SECONDS", raising=False)
    assert _yt_pool_transcript_deadline_seconds() == 300.0
    monkeypatch.setenv("VIDZEO_YT_TRANSCRIPT_DEADLINE_SECONDS", "60")
    assert _yt_pool_transcript_deadline_seconds() == 60.0


def test_anonymous_search_retry_budget_is_30s(monkeypatch):
    """Non-render speed fix: the anonymous retry after a zero-candidate
    pool search gets 30s, not 60s. A cache-served retry answers in
    seconds; a throttled one recovered 0 candidates in 60s in test 22
    and only burned discovery time."""
    from server.services import youtube_pipeline_v2 as yt

    monkeypatch.delenv("VIDZEO_YT_SEARCH_ANON_RETRY_SECONDS", raising=False)
    monkeypatch.delenv("VIDZEO_YT_V2_SEARCH_TIMEOUT", raising=False)
    monkeypatch.delenv("VIDZEO_YT_SCENE_SEARCH_BUDGET_SECONDS", raising=False)
    calls = []

    def fake_pass(jobs, *, session_jar=None, search_timeout, max_workers, cancellation=None):
        calls.append(search_timeout)
        return True, {}

    monkeypatch.setattr(yt, "_run_pool_search_pass", fake_pass)
    groups = {"c": [{"queries": [f"q{i}" for i in range(12)]}]}
    yt._search_container_pools(groups, session_jar={})
    assert len(calls) == 2
    assert calls[1] == 30.0
