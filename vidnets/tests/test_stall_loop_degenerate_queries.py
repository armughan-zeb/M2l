"""Stall-loop + degenerate-query fix (Nov 22, job d1ece590).

Job d1ece590 (Rockstar, 127 scenes) paused TWICE at 60-61% "Preparation
stalled" — each attempt lived only ~15 min. Root causes, each proven from
the run-0006/0007 logs and audits:

1. The preparation-stall detector (300s, queue_manager.py) kills an
   attempt whose progress text does not change, but the web lane records
   activity only on scene completion and the YT child relays progress only
   when a counter CHANGES — so a media stage working slowly looks dead.
2. Degenerate queries: narration "They drove." produced scene_subject
   'They' (mechanical planner took the sentence-initial pronoun as an
   entity), the requirement contract grounded it, and the resolver sent
   'They documentary drove photograph' etc. to the engines — each burning
   its whole 5-attempt retry budget (~35-40s) before settling zero.
3. A cancelled job re-ran every dead query 5x: _discover_universal_urls
   never checked cancellation between retries.
4. The YT lane's vision work consumed the shared visual window first, so
   the web visual phase's grace collapsed to 1.0s (run-0006: 0/125,
   context_degraded_reason "visual_deadline_exceeded").

Tests below pin each fix. They use the real job's scene text
("They drove.") and the exact audit queries — no fabricated data.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from services.mechanical_scene_planner import cap_phrases, plan_mechanical_scene_rows
from services.visual_requirement import (
    build_visual_requirement,
    provider_query_variants,
    rebase_visual_requirement_for_scene,
    scene_subject_contract,
)
from services.web_image import resolver
from services.web_image.query import clean_scene_queries, is_degenerate_query
from services.web_image.universal_scraper import UniversalSearchResponse

TITLE = "How Rockstar Built the Most Detailed Map in Gaming History"

# Exact queries from the run-0006 web audit for scenes 2-3 (all degenerate).
AUDIT_DEGENERATE_QUERIES = [
    "They documentary drove photograph",
    "They documentary drove reference image",
    "They documentary drove",
    "They documentary photograph",
    "They drove photo",
    "They drove",
    "They photo",
    "They",
    "They photograph",
    "They documentary",
]

GOOD_QUERIES = [
    "Grand Theft Auto VI Vice City skyline photograph",
    "Rockstar Games Grand Theft Auto VI release documentary photograph",
    "Grand Theft Auto V xbox series documentary gameplay photograph",
    "Rockstar photograph",
    "Grand Theft Auto VI drove photo",
]


# ── Fix 1A: mechanical planner never emits a pronoun subject ──────────

def test_cap_phrases_drops_sentence_initial_pronouns():
    assert cap_phrases("They drove.") == []
    assert cap_phrases("He built it.") == []
    assert cap_phrases("She opened the map.") == []
    assert cap_phrases("We drove south.") == []
    # Real entities still extract.
    assert "Rockstar Games" in cap_phrases("Rockstar Games released GTA VI.")
    assert "Vice City" in cap_phrases("Welcome to Vice City.")


def test_pronoun_only_scene_inherits_container_subject():
    scenes = [
        "On November 19, 2026, Rockstar Games released Grand Theft Auto VI.",
        "They drove.",
        "They exist because of the causeway.",
    ]
    rows = plan_mechanical_scene_rows(
        scenes, TITLE, "Grand Theft Auto VI",
        ["Grand Theft Auto VI", "Grand Theft Auto VI", "Grand Theft Auto VI"],
    )
    assert rows[1]["scene_subject"] != "They"
    assert rows[1]["scene_subject"] == "Grand Theft Auto VI"
    assert rows[2]["scene_subject"] != "They"
    assert "They" not in rows[1]["web"]["primary"]
    assert "They" not in rows[2]["web"]["primary"]


def test_mechanical_bundle_scene_two_has_real_subject():
    from services.mechanical_query_bundle import build_mechanical_query_bundle

    scenes = [
        "On November 19, 2026, thirteen years after Grand Theft Auto V, "
        "Rockstar Games released Grand Theft Auto VI.",
        "They drove.",
        "They exist because of the causeway and the landmarks.",
    ]
    bundle = build_mechanical_query_bundle(
        title=TITLE,
        scene_texts=scenes,
        section_subjects=["Grand Theft Auto VI"] * 3,
        sections=[{"container_id": "section_1"}] * 3,
    )
    web = bundle["web"]
    assert web[1]["scene_subject"] != "They"
    assert web[1]["search_query"].startswith("Grand Theft Auto VI")
    assert web[2]["scene_subject"] != "They"


# ── Fix 1B: requirement contract refuses pronoun identities ───────────

def test_contract_refuses_pronoun_director_subject():
    ctx = {"scene_subject": "They", "visual_subject_type": "concept"}
    contract = scene_subject_contract(TITLE, "They drove.", ctx)
    assert contract["scene_local"] is False
    assert contract["origin"] == "film_context"
    assert contract["film_subject"] == "Rockstar"


def test_rebase_keeps_film_identity_for_pronoun_scene():
    plan_req = {
        "canonical_entity": "Rockstar",
        "subject_type": "video_game",
        "domain": "documentary",
        "action_terms": ["drove"],
        "positive_subjects": ["Rockstar"],
    }
    ctx = {"scene_subject": "They", "visual_subject_type": "concept"}
    out = rebase_visual_requirement_for_scene(
        plan_req, title=TITLE, scene_text="They drove.", script="",
        semantic_context=ctx,
    )
    assert out.get("canonical_entity") == "Rockstar"
    queries = provider_query_variants(out, provider="web")
    assert queries and all("They" not in q.split() for q in queries)


# ── Fix 1C: query cleaning safety net ──────────────────────────────────

@pytest.mark.parametrize("query", AUDIT_DEGENERATE_QUERIES)
def test_audit_queries_are_degenerate(query):
    assert is_degenerate_query(query) is True


@pytest.mark.parametrize("query", GOOD_QUERIES)
def test_good_queries_are_not_degenerate(query):
    assert is_degenerate_query(query) is False


def test_clean_drops_garbage_keeps_good_queries():
    queries = [
        "They documentary drove photograph",
        "Grand Theft Auto VI Vice City skyline photograph",
        "They photo",
        "Rockstar Games Grand Theft Auto VI release documentary photograph",
    ]
    cleaned = clean_scene_queries(queries, fallback_subject="Rockstar")
    assert cleaned == [
        "Grand Theft Auto VI Vice City skyline photograph",
        "Rockstar Games Grand Theft Auto VI release documentary photograph",
    ]


def test_clean_falls_back_to_topic_query_when_all_garbage():
    cleaned = clean_scene_queries(
        ["They documentary drove photograph", "They photo"],
        fallback_subject="Rockstar",
    )
    assert cleaned == ["Rockstar documentary photograph"]


def test_clean_returns_empty_without_fallback():
    assert clean_scene_queries(["They", "They photo"]) == []


# ── Fix 2: _discover_universal_urls fails fast + records liveness ─────

def _failed_stat(engine, error="ReadTimeout"):
    return SimpleNamespace(
        engine=engine, count=0, duration_ms=15000, success=False, error=error
    )


def _install_fake_scraper(monkeypatch, search_fn, rescue_attempts="0"):
    monkeypatch.delenv("VIDZEO_UNIVERSAL_SCRAPER_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("VIDZEO_UNIVERSAL_LATE_WAIT_SECONDS", "0")
    monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RETRY_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RESCUE_ATTEMPTS", rescue_attempts)
    monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RESCUE_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RESCUE_DEADLINE_SECONDS", "60")

    def search_via_pool(query, **options):
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=2) as ex:
            return ex.submit(search_fn, query, **options).result()

    monkeypatch.setattr(
        resolver,
        "_UNIVERSAL_SCRAPER",
        SimpleNamespace(search=search_via_pool, collect_late_results=lambda _q, **_: []),
    )
    monkeypatch.setattr(
        resolver, "universal_result_to_candidate", lambda item, **_: {"url": item.image_url}
    )
    monkeypatch.setattr(resolver, "candidate_is_safe", lambda _candidate: (True, "fixture"))
    monkeypatch.setattr(resolver, "_report_provider_success", lambda _source: None)
    monkeypatch.setattr(resolver, "_report_provider_failure", lambda *_args: None)


def test_degenerate_query_never_reaches_a_provider(monkeypatch):
    calls = []

    def search(query, **options):
        calls.append(query)
        return UniversalSearchResponse(query, 0, [], [])

    _install_fake_scraper(monkeypatch, search)
    rows = resolver._discover_universal_urls("They documentary drove photograph")
    assert rows == []
    assert calls == []
    assert resolver._universal_audit_snapshot().get("degenerate_query") is True


def test_settled_batch_records_pipeline_activity(monkeypatch):
    from services.pipeline_activity import last_activity, reset_activity

    def search(query, **options):
        stats = [_failed_stat("bing"), _failed_stat("yandex"), _failed_stat("flickr")]
        return UniversalSearchResponse(query, 0, [], stats)

    _install_fake_scraper(monkeypatch, search)
    reset_activity("test")
    assert last_activity() is None
    rows = resolver._discover_universal_urls("Grand Theft Auto VI skyline")
    assert rows == []
    # Even a zero-result batch is real work done: the stall detector must
    # see it, or a slow media stage looks dead and gets killed at 300s.
    assert last_activity() is not None


def test_no_retry_after_cancellation(monkeypatch):
    from services.job_context import job_scope

    calls = []

    def search(query, **options):
        calls.append(query)
        stats = [_failed_stat("bing"), _failed_stat("yandex"), _failed_stat("flickr")]
        return UniversalSearchResponse(query, 0, [], stats)

    _install_fake_scraper(monkeypatch, search)
    finished = threading.Event()
    finished.set()
    with job_scope({"finished_event": finished}):
        with pytest.raises(InterruptedError):
            resolver._discover_universal_urls("Grand Theft Auto VI skyline")
    # The cancellation check fires before the first search: a cancelled job
    # must not burn 5 attempts on a dead query.
    assert calls == []


# ── Fix 2b: YT child log growth counts as liveness ────────────────────

def test_yt_poll_records_activity_when_child_log_grows(tmp_path):
    import app as server_app
    from services import pipeline_activity
    from services.youtube_worker_progress import YouTubeWorkerProgress

    log_path = tmp_path / "youtube-worker-test.log"
    log_path.write_bytes(b"[yt-worker] child started: 127 scene(s)\n")
    relay = YouTubeWorkerProgress(log_path, "test", emit=lambda *_a, **_k: None)
    future = object.__new__(server_app._YoutubeProcessFuture)
    future._progress_relay = relay
    future._finished = False
    future._process = SimpleNamespace(poll=lambda: None)

    pipeline_activity.reset_activity("test")
    assert pipeline_activity.last_activity() is None
    future._poll()
    assert pipeline_activity.last_activity() is not None


def test_yt_poll_records_no_activity_when_child_is_silent(tmp_path):
    import app as server_app
    from services import pipeline_activity
    from services.youtube_worker_progress import YouTubeWorkerProgress

    log_path = tmp_path / "youtube-worker-test.log"
    log_path.write_bytes(b"")
    relay = YouTubeWorkerProgress(log_path, "test", emit=lambda *_a, **_k: None)
    future = object.__new__(server_app._YoutubeProcessFuture)
    future._progress_relay = relay
    future._finished = False
    future._process = SimpleNamespace(poll=lambda: None)

    pipeline_activity.reset_activity("test")
    future._poll()
    # A wedged child writes nothing: no activity, so the stall detector
    # still trips for a genuinely stuck branch.
    assert pipeline_activity.last_activity() is None


# ── Fix 4 (vision lanes, 2026-11-22): no visual-window refresh ───────
# The old _refresh_web_visual_window bought a flat 240 s rescue for a
# shared deadline that web had already overdrawn by 439 s. It is
# DELETED: each lane has its own clock starting at its first vision
# submission, and a lane budget is NEVER extended — not by the
# resolver, not by the coordinator. These tests pin the replacement:
# refresh_deadline on a lane child is a no-op, and the resolver no
# longer has a refresh helper at all.

def test_lane_budget_is_never_extended_when_nearly_expired(monkeypatch):
    import time

    from services.visual_intelligence.context import VisualDecisionContext
    from services.visual_intelligence.lane_budget import LaneVisionBudget

    assert not hasattr(resolver, "_refresh_web_visual_window"), \
        "the shared-deadline refresh helper is deleted with vision lanes"
    context = VisualDecisionContext.from_config({"llm_provider": "external_agent"}, 3)
    budget = LaneVisionBudget("web", scene_count=3, budget_seconds=240.0)
    child = context.child("web", budget)
    # The lane clock has not started: no deadline yet, nothing to extend.
    assert budget.deadline_at is None
    budget.start_if_needed()
    deadline_started = budget.deadline_at
    assert deadline_started is not None
    child.refresh_deadline(deadline_seconds=3600.0)
    child.refresh_deadline(deadline_at=time.monotonic() + 3600.0)
    assert budget.deadline_at == deadline_started, \
        "a lane budget is never extended by refreshing a deadline"
    assert child.deadline_at == float("inf"), "the child's own deadline is untouched too"


def test_lane_budget_leaves_healthy_window_alone():
    import time

    from services.visual_intelligence.context import VisualDecisionContext
    from services.visual_intelligence.lane_budget import LaneVisionBudget

    context = VisualDecisionContext.from_config({"llm_provider": "external_agent"}, 3)
    budget = LaneVisionBudget("web", scene_count=3, budget_seconds=240.0)
    child = context.child("web", budget)
    budget.start_if_needed()
    deadline_started = budget.deadline_at
    child.refresh_deadline(deadline_seconds=3600.0)
    assert budget.deadline_at == deadline_started
    assert not budget.expired()


def test_lane_budget_expiry_is_terminal():
    """Once a lane's own deadline passes, it stays expired: no rescue."""
    from services.visual_intelligence.context import VisualDecisionContext
    from services.visual_intelligence.lane_budget import LaneVisionBudget

    context = VisualDecisionContext.from_config({"llm_provider": "external_agent"}, 3)
    budget = LaneVisionBudget("web", scene_count=3, budget_seconds=60.0,
                              clock=lambda: 1000.0)
    context.child("web", budget)
    budget.start_if_needed(now=1000.0)
    assert budget.deadline_at == 1060.0
    assert budget.expired(now=1060.0) is True
    assert budget.remaining(now=1060.0) == 0.0


# ── Fix 2c: Mixed Media web fan-out default ───────────────────────────

def test_mixed_web_scene_worker_default_is_four(monkeypatch):
    from services.pipeline_v2_media_adapter import _mixed_web_scene_worker_limit

    monkeypatch.delenv("VIDZEO_MIXED_WEB_SCENE_WORKERS", raising=False)
    assert _mixed_web_scene_worker_limit({}) == 4
    monkeypatch.setenv("VIDZEO_MIXED_WEB_SCENE_WORKERS", "2")
    assert _mixed_web_scene_worker_limit({}) == 2
    monkeypatch.setenv("VIDZEO_MIXED_WEB_SCENE_WORKERS", "6")
    assert _mixed_web_scene_worker_limit({}) == 6
