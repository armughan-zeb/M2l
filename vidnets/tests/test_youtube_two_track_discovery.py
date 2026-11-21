"""Two-track YouTube discovery: budget, general pool, background downloads.

Covers the user-ordered discovery system:
* discovery budget = 5 min per minute of video (300s/min), NO cap, rate is
  a configurable setting (config key wins, env next, 300s default);
* the budget bounds discovery only — downloads are exempt and run in the
  background download queue;
* Track B general pool: broad topic queries, vision-verified entries, a
  pool clip covers at most 3 scenes.
"""
import time

import pytest

from server.services.youtube_discovery_budget import (
    DEFAULT_SECONDS_PER_MINUTE,
    DiscoveryBudget,
    discovery_budget_seconds,
    discovery_seconds_per_minute,
    video_duration_minutes,
)
from server.services.youtube_download_queue import YouTubeDownloadQueue
from server.services.youtube_general_pool import (
    GeneralPoolEntry,
    YouTubeGeneralPool,
    build_general_pool_queries,
    general_pool_reuse_cap,
)
from server.services.youtube_pipeline_v2 import _yt_download_loop_decision


# ---------------------------------------------------------------------------
# Download loop: soft (discovery) deadline drains, hard deadline stops
# ---------------------------------------------------------------------------


def _decision(now=300.0, **overrides):
    kwargs = dict(
        hard_deadline=1000.0,
        soft_ceiling_deadline=500.0,
        soft_deadline=400.0,
        soft_enabled=True,
        ready_ratio=0.0,
        soft_coverage=0.0,
        discovery_stopped=False,
    )
    kwargs.update(overrides)
    return _yt_download_loop_decision(now, **kwargs)


def test_loop_decision_continues_before_any_deadline():
    assert _decision() == ("continue", False)


def test_loop_decision_soft_timeout_stops_discovery_not_the_wait():
    # Soft deadline hit, coverage 0 (fire regardless): discovery stops,
    # but the action is NOT hard_stop — in-flight downloads keep draining.
    action, stopped = _decision(now=450.0)
    assert action == "soft_timeout"
    assert stopped is True


def test_loop_decision_soft_ceiling_stops_discovery_not_the_wait():
    action, stopped = _decision(now=550.0, soft_deadline=None)
    assert action == "soft_ceiling"
    assert stopped is True


def test_loop_decision_soft_deadline_respects_coverage_gate():
    # Coverage not yet met: the soft timeout must not fire.
    assert _decision(now=450.0, ready_ratio=0.2, soft_coverage=0.5) == ("continue", False)


def test_loop_decision_hard_deadline_ends_the_wait():
    assert _decision(now=1000.0) == ("hard_stop", False)
    # Hard wins even after discovery already stopped.
    assert _decision(now=1200.0, discovery_stopped=True) == ("hard_stop", True)


def test_loop_decision_drain_never_retriggers_after_discovery_stopped():
    # Once discovery stopped, later ticks keep draining (continue), they
    # must not flip the stop reason or end the wait early.
    assert _decision(now=600.0, discovery_stopped=True) == ("continue", True)
    assert _decision(now=900.0, discovery_stopped=True) == ("continue", True)


def test_loop_decision_soft_disabled_never_stops_discovery():
    assert _decision(now=450.0, soft_enabled=False) == ("continue", False)
    assert _decision(now=550.0, soft_enabled=False) == ("continue", False)


# ---------------------------------------------------------------------------
# Discovery budget: 5 minutes per minute of video, no cap, shiftable rate
# ---------------------------------------------------------------------------


def test_budget_five_minutes_per_minute_of_video():
    assert discovery_budget_seconds(1) == 300.0
    assert discovery_budget_seconds(5) == 1500.0
    assert discovery_budget_seconds(15) == 4500.0
    assert discovery_budget_seconds(0.5) == 150.0


def test_budget_has_no_cap_for_long_videos():
    # 15 min -> 75 min of discovery. The old 20-min plan capped; this one
    # must not: the rate scales linearly with duration by design.
    assert discovery_budget_seconds(60) == 18000.0


def test_budget_rate_is_configurable_via_config(monkeypatch):
    monkeypatch.delenv("VIDZEO_YT_DISCOVERY_SECONDS_PER_MINUTE", raising=False)
    assert discovery_seconds_per_minute({"yt_discovery_seconds_per_minute": 120}) == 120.0
    assert discovery_budget_seconds(1, config={"yt_discovery_seconds_per_minute": 120}) == 120.0


def test_budget_rate_is_configurable_via_env(monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_DISCOVERY_SECONDS_PER_MINUTE", "600")
    assert discovery_seconds_per_minute({}) == 600.0
    assert discovery_budget_seconds(2) == 1200.0


def test_budget_config_wins_over_env(monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_DISCOVERY_SECONDS_PER_MINUTE", "600")
    assert discovery_seconds_per_minute({"yt_discovery_seconds_per_minute": 60}) == 60.0


def test_budget_invalid_rate_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_DISCOVERY_SECONDS_PER_MINUTE", "banana")
    assert discovery_seconds_per_minute({}) == DEFAULT_SECONDS_PER_MINUTE
    monkeypatch.setenv("VIDZEO_YT_DISCOVERY_SECONDS_PER_MINUTE", "-5")
    assert discovery_seconds_per_minute({}) == DEFAULT_SECONDS_PER_MINUTE


def test_budget_unknown_duration_falls_back_to_one_minute():
    assert discovery_budget_seconds(0) == 300.0
    assert discovery_budget_seconds(None) == 300.0
    assert discovery_budget_seconds("nonsense") == 300.0


def test_video_duration_minutes_prefers_explicit_seconds():
    assert video_duration_minutes(90) == 1.5
    assert video_duration_minutes(60, scene_count=13) == 1.0


def test_video_duration_minutes_estimates_from_scenes():
    # ~4.5s per scene: 13 scenes ~= 1 minute.
    assert video_duration_minutes(None, scene_count=13) == pytest.approx(0.975)
    assert video_duration_minutes(0, scene_count=0) == 1.0


def test_discovery_budget_deadline_and_expiry():
    budget = DiscoveryBudget(300.0, started_at=1000.0)
    assert budget.deadline_at == 1300.0
    assert budget.remaining(now=1100.0) == 200.0
    assert not budget.expired(now=1299.9)
    assert budget.expired(now=1300.0)
    assert budget.expired(now=1400.0)


def test_discovery_budget_to_dict_reports_state():
    budget = DiscoveryBudget(300.0, started_at=time.monotonic())
    state = budget.to_dict()
    assert state["budget_seconds"] == 300.0
    assert state["expired"] is False


# ---------------------------------------------------------------------------
# Track B: general pool queries from the video topic
# ---------------------------------------------------------------------------


def test_pool_queries_anchor_on_topic():
    queries = build_general_pool_queries(
        "Grand Theft Auto VI: Return to Vice City",
        topic_anchor="Grand Theft Auto VI",
    )
    assert queries[0] == "Grand Theft Auto VI gameplay"
    assert "Grand Theft Auto VI official gameplay" in queries
    assert "Grand Theft Auto VI trailer" in queries
    assert "Grand Theft Auto VI" in queries
    assert len(queries) <= 6
    assert len(set(queries)) == len(queries)


def test_pool_queries_derive_anchor_from_title_when_missing():
    queries = build_general_pool_queries("How the Panama Canal Changed World Trade")
    assert queries
    assert any("Panama Canal" in q for q in queries)


def test_pool_queries_use_top_scene_subjects():
    queries = build_general_pool_queries(
        "Grand Theft Auto VI: Return to Vice City",
        topic_anchor="Grand Theft Auto VI",
        scene_subjects=["Vice City", "Lucia", "Vice City"],
    )
    assert any("Vice City Grand Theft Auto VI" in q for q in queries)
    assert any("Lucia Grand Theft Auto VI" in q for q in queries)


def test_pool_queries_empty_without_topic():
    assert build_general_pool_queries("") == []
    assert build_general_pool_queries("   ") == []


# ---------------------------------------------------------------------------
# Track B: pool entries, subject-matched claims, reuse cap 3
# ---------------------------------------------------------------------------


def _entry(video_id, title, score=80.0, subjects=()):
    return GeneralPoolEntry(
        video_id=video_id, title=title, vision_score=score,
        subjects=list(subjects), best_start=10.0, best_end=40.0,
    )


def test_pool_claim_matches_subject_first():
    pool = YouTubeGeneralPool()
    pool.add(_entry("aaa", "GTA VI Vice City dusk skyline gameplay", 70.0, ["Vice City"]))
    pool.add(_entry("bbb", "GTA VI Lucia gym workout gameplay", 90.0, ["Lucia"]))
    claimed = pool.claim("Lucia", "Lucia trains at the gym in Vice City")
    assert claimed is not None
    assert claimed.video_id == "bbb"


def test_pool_claim_falls_back_to_best_verified_when_no_subject_match():
    pool = YouTubeGeneralPool()
    pool.add(_entry("aaa", "GTA VI gameplay montage", 70.0))
    pool.add(_entry("bbb", "GTA VI open world driving", 85.0))
    claimed = pool.claim("Unrelated Subject", "narration about something else")
    assert claimed is not None
    assert claimed.video_id == "bbb"


def test_pool_reuse_cap_three_scenes():
    pool = YouTubeGeneralPool()
    pool.add(_entry("aaa", "GTA VI gameplay", 80.0))
    for _ in range(3):
        assert pool.claim("GTA VI", "gameplay") is not None
    # Fourth scene: the clip is spent; no claim.
    assert pool.claim("GTA VI", "gameplay") is None


def test_pool_reuse_cap_is_configurable(monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_GENERAL_POOL_REUSE_CAP", "2")
    assert general_pool_reuse_cap({}) == 2
    assert general_pool_reuse_cap({"yt_general_pool_reuse_cap": 5}) == 5


def test_pool_claim_respects_exclusions():
    pool = YouTubeGeneralPool()
    pool.add(_entry("aaa", "GTA VI gameplay", 80.0))
    assert pool.claim("GTA VI", exclude_video_ids=["aaa"]) is None
    assert pool.claim("GTA VI") is not None


def test_pool_add_dedupes_by_video_id_and_keeps_best_score():
    pool = YouTubeGeneralPool()
    assert pool.add(_entry("aaa", "GTA VI gameplay", 70.0)) is True
    assert pool.add(_entry("aaa", "GTA VI gameplay", 90.0)) is False
    assert len(pool) == 1
    assert pool.entries()[0].vision_score == 90.0


def test_pool_claim_prefers_least_used_on_tied_scores():
    pool = YouTubeGeneralPool()
    pool.add(_entry("aaa", "GTA VI gameplay", 80.0))
    pool.add(_entry("bbb", "GTA VI gameplay", 80.0))
    first = pool.claim("GTA VI")
    second = pool.claim("GTA VI")
    assert first.video_id != second.video_id


def test_pool_empty_claim_returns_none():
    assert YouTubeGeneralPool().claim("GTA VI") is None


# ---------------------------------------------------------------------------
# Downloads: background queue, decoupled from the discovery budget
# ---------------------------------------------------------------------------


def test_download_queue_runs_in_parallel_and_returns_paths():
    queue = YouTubeDownloadQueue(max_workers=4)
    started = []

    def fake_download(name, delay=0.05):
        def _run():
            started.append(name)
            time.sleep(delay)
            return f"/tmp/{name}.mp4"
        return _run

    queue.submit("clip-a", fake_download("a"))
    queue.submit("clip-b", fake_download("b"))
    results = queue.wait_all(timeout=5.0)
    assert results == {"clip-a": "/tmp/a.mp4", "clip-b": "/tmp/b.mp4"}
    assert queue.pending_count() == 0
    queue.shutdown()


def test_download_queue_failure_is_a_none_path_not_an_exception():
    queue = YouTubeDownloadQueue(max_workers=2)

    def _boom():
        raise RuntimeError("yt-dlp stalled")

    queue.submit("clip-a", _boom)
    queue.submit("clip-b", lambda: "/tmp/b.mp4")
    results = queue.wait_all(timeout=5.0)
    assert results["clip-a"] is None
    assert results["clip-b"] == "/tmp/b.mp4"
    assert "clip-a" in queue.errors()
    queue.shutdown()


def test_download_queue_drain_waits_for_slow_downloads():
    queue = YouTubeDownloadQueue(max_workers=1)

    def _slow():
        time.sleep(0.3)
        return "/tmp/slow.mp4"

    queue.submit("slow", _slow)
    assert queue.pending_count() == 1
    results = queue.wait_all(timeout=5.0)
    assert results["slow"] == "/tmp/slow.mp4"
    queue.shutdown()


def test_download_queue_timeout_leaves_unfinished_running():
    queue = YouTubeDownloadQueue(max_workers=1)

    def _slow():
        time.sleep(0.5)
        return "/tmp/slow.mp4"

    queue.submit("slow", _slow)
    partial = queue.wait_all(timeout=0.05)
    assert partial.get("slow") is None
    assert queue.pending_count() == 1
    finished = queue.wait_all(timeout=5.0)
    assert finished["slow"] == "/tmp/slow.mp4"
    queue.shutdown()
