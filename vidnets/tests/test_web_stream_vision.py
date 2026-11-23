"""Web-stream vision: file web verdicts DURING retrieval (test 28 fix).

Design ``work/web-stream-vision-design.md`` §7. Every test here must FAIL
on clean HEAD b44b982 and PASS with the fix. Run with
``VIDZEO_RENDER_GATE=off``.
"""
import copy
import random
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services.visual_intelligence.lane_budget import (
    LaneScheduler,
    LaneVisionBudget,
    create_lane_budgets,
)
from services.media_resolution_coordinator import _late_web_branch_accept
from services.pipeline_v2_media_adapter import LazyBatchLane


def _load_streamer():
    """Import the streamer lazily so this module still COLLECTS on HEAD
    (where the name does not exist) and each test fails there instead
    of erroring at import time."""
    from services.web_image.resolver import _WebVisionStreamer

    return _WebVisionStreamer


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = float(now)

    def __call__(self):
        return self.now


def _passing_model_row(candidate_id):
    return {
        "candidate_id": candidate_id,
        "scene_description": "a street scene with cars",
        "identity_score": 85.0,
        "action_score": 85.0,
        "context_score": 85.0,
        "quality": 85.0,
        "final_score": 85.0,
        "relevance": 85.0,
        "confidence": 85.0,
        "domain_match": True,
        "scene_match": True,
        "fatal_mismatch": False,
        "entity_conflict": False,
        "reason": "shows the scene subject",
        "visible_overlays": [],
        "subject_box": {},
        "safe_text_regions": [],
        "has_text": False,
    }


def _rejecting_model_row(candidate_id):
    row = _passing_model_row(candidate_id)
    row.update({
        "identity_score": 5.0,
        "action_score": 5.0,
        "context_score": 5.0,
        "quality": 5.0,
        "final_score": 5.0,
        "relevance": 5.0,
        "confidence": 90.0,
        "domain_match": False,
        "scene_match": False,
        "fatal_mismatch": True,
        "reason": "shows a different subject",
    })
    return row


def _image(tmp_path, name, color=(12, 34, 56)):
    path = tmp_path / name
    Image.new("RGB", (64, 48), color).save(path, "JPEG", quality=70)
    return str(path)


def _scene_result(idx, tmp_path, n_candidates=1):
    """A _resolve_scene-style return dict for scene idx."""
    shortlist = []
    for j in range(n_candidates):
        shortlist.append({
            "candidate_id": f"web:{idx}:c{j}",
            "path": _image(tmp_path, f"web_{idx}_{j}.jpg", (10 + idx, 40 + j, 80)),
            "title": "Grand Theft Auto VI street scene",
            "description": "a street scene",
            "domain": "example.com",
            "provider": "web",
            "source": "web",
            "url": "https://example.com/img.jpg",
            "query_used": "GTA VI street",
        })
    winner = dict(shortlist[0])
    winner["_vision_shortlist"] = shortlist
    winner["candidate_pool_size"] = len(shortlist)
    winner["downloaded_candidate_count"] = len(shortlist)
    winner["query_used"] = "GTA VI street"
    winner["final_score"] = 80.0
    return winner


class FakeWebClient:
    """Records analyze_images_batch requests; returns passing verdicts."""

    def __init__(self, rows=None):
        self.calls = []
        self.lock = threading.Lock()
        self.rows = rows
        self.model = "qwen/test"

    def analyze_images_batch(self, paths, prompt, system, ids, scene_tags=None):
        with self.lock:
            self.calls.append({
                "paths": list(paths), "ids": list(ids),
                "scene_tags": list(scene_tags or []),
                "at": time.monotonic(),
            })
        out = {}
        for cid in ids:
            if self.rows is not None and cid in self.rows:
                out[cid] = dict(self.rows[cid])
            else:
                out[cid] = _passing_model_row(cid)
        return out


def _fake_lane_context(client, budget, scheduler=None):
    ctx = SimpleNamespace(
        model="qwen/test", visual_required=False, fail_closed=False,
        lane_scheduler=scheduler, _parent=None,
    )
    ctx._lane_budget = budget
    ctx.get_client = lambda: client
    return ctx


def _make_streamer(tmp_path, n, client, budget, results=None):
    streamer_cls = _load_streamer()
    results = results if results is not None else [None] * n
    published = []
    applied = []

    def apply_result(idx, result):
        applied.append(idx)
        results[idx] = result
        published.append((idx, time.monotonic()))

    ctx = _fake_lane_context(client, budget)
    streamer = streamer_cls(
        visual_context=ctx,
        lane_budget=budget,
        scene_texts=[f"Grand Theft Auto VI street scene {i}" for i in range(n)],
        scene_active_subjects=["Grand Theft Auto VI"] * n,
        title="Grand Theft Auto VI",
        results=results,
        results_lock=threading.RLock(),
        apply_result=apply_result,
    )
    return streamer, results, published, applied


def _wait_until(predicate, timeout=5.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ---------------------------------------------------------------------------
# T1 — streaming files vision before retrieval completes
# ---------------------------------------------------------------------------

def test_streaming_files_vision_before_retrieval_completes(tmp_path):
    """T1: >= 1 vision call filed while retrieval_done is False, and the
    first web row published before the drain. HEAD has no streamer: 0
    calls until the drain; first publish after retrieval end."""
    client = FakeWebClient()
    budget = LaneVisionBudget("web", scene_count=13)
    streamer, results, published, _applied = _make_streamer(
        tmp_path, 13, client, budget
    )
    retrieval_done = threading.Event()
    try:
        for idx in range(8):
            assert streamer.offer(idx, _scene_result(idx, tmp_path))
        assert _wait_until(lambda: len(client.calls) >= 1, timeout=5.0), \
            "no vision call filed while retrieval was still running"
        assert not retrieval_done.is_set()
        first_call_at = client.calls[0]["at"]
        # The publish lands synchronously inside the same driver call,
        # just after the client records the request — wait for it.
        assert _wait_until(lambda: len(published) >= 1, timeout=5.0), \
            "first web row must publish during retrieval"
        assert published, "first web row must publish during retrieval"
        assert published[0][1] < first_call_at + 5.0
        for idx in range(8, 13):
            assert streamer.offer(idx, _scene_result(idx, tmp_path))
    finally:
        retrieval_done.set()
        streamer.close()
    assert sorted(streamer.decided_snapshot()) == list(range(13))
    assert len(published) == 13, f"each scene publishes once, got {len(published)}"
    assert all(
        results[idx] is not None and results[idx].get("visual_outcome") == "pass"
        for idx in range(13)
    )


def test_streaming_stress_random_completion_order(tmp_path):
    """T1 stress (§8): 20 runs with random per-scene completion delays.
    Invariants on every run: each candidate filed <= once, each scene
    published <= once, no exception from the handover, all scenes
    decided. If this is flaky, the streamer must be simplified (drop
    the slow-tail flush trigger) rather than shipped flaky."""
    for run in range(20):
        rng = random.Random(1000 + run)
        client = FakeWebClient()
        budget = LaneVisionBudget("web", scene_count=13)
        streamer, results, published, applied = _make_streamer(
            tmp_path, 13, client, budget
        )
        order = list(range(13))
        rng.shuffle(order)
        try:
            for idx in order:
                time.sleep(rng.uniform(0.0, 0.05))
                assert streamer.offer(idx, _scene_result(idx, tmp_path))
        finally:
            streamer.close()
        filed = [cid for call in client.calls for cid in call["ids"]]
        assert len(filed) == len(set(filed)), \
            f"run {run}: a candidate was filed twice: {filed}"
        assert len(applied) == len(set(applied)), \
            f"run {run}: a scene was applied twice"
        assert len(published) == len(set(i for i, _ in published)), \
            f"run {run}: a scene published twice"
        assert sorted(streamer.decided_snapshot()) == list(range(13)), \
            f"run {run}: undecided scenes remain: {sorted(streamer.decided_snapshot())}"


# ---------------------------------------------------------------------------
# T2 — packs of 8 preserved
# ---------------------------------------------------------------------------

def test_streaming_packs_of_8_preserved(tmp_path):
    """T2: 13 scenes, staggered completion -> vision request sizes
    exactly [8, 5] (wave 1), never 1-by-1; total wave-1 requests == 2.
    HEAD files 0 calls during retrieval; a one-by-one implementation
    fails on request sizes [1, 1, ...]."""
    client = FakeWebClient()
    budget = LaneVisionBudget("web", scene_count=13)
    streamer, _results, _published, _applied = _make_streamer(
        tmp_path, 13, client, budget
    )
    try:
        for idx in range(8):
            assert streamer.offer(idx, _scene_result(idx, tmp_path))
            time.sleep(0.05)
        assert _wait_until(lambda: len(client.calls) >= 1, timeout=5.0)
        assert len(client.calls) == 1, "first pack-set must file exactly 1 wave-1 request"
        assert len(client.calls[0]["ids"]) == 8
        for idx in range(8, 13):
            assert streamer.offer(idx, _scene_result(idx, tmp_path))
            time.sleep(0.05)
    finally:
        streamer.close()
    assert len(client.calls) == 2, f"wave 1 must file 2 requests, got {len(client.calls)}"
    sizes = sorted(len(call["ids"]) for call in client.calls)
    assert sizes == [5, 8], f"packs must be 8+5, got {sizes}"


# ---------------------------------------------------------------------------
# T3 — no double-filing
# ---------------------------------------------------------------------------

def test_streaming_no_double_filing(tmp_path):
    """T3: after a full run each candidate_id appears exactly once
    across streamer + drain, and the drain's visual_tasks excludes
    streamer-decided scenes. HEAD's drain rebuilds tasks from all
    results and re-files streamer-decided candidates (each twice)."""
    client = FakeWebClient()
    budget = LaneVisionBudget("web", scene_count=13)
    streamer, results, _published, _applied = _make_streamer(
        tmp_path, 13, client, budget
    )
    try:
        for idx in range(13):
            assert streamer.offer(idx, _scene_result(idx, tmp_path, n_candidates=2))
            time.sleep(0.02)
    finally:
        streamer.close()
    filed = [cid for call in client.calls for cid in call["ids"]]
    assert len(filed) == len(set(filed)), f"a candidate was filed twice: {filed}"
    # Wave 1 files each scene's top-1 exactly once; no wave 2 (all pass).
    assert set(filed) == {f"web:{idx}:c0" for idx in range(13)}
    decided = streamer.decided_snapshot()
    assert decided == set(range(13))
    # The drain builds visual_tasks only from undecided scenes (§1.6.1).
    drain_tasks = [
        idx for idx, result in enumerate(results)
        if result and idx not in decided
        and not result.get("_streaming_vision_passed")
        and result.get("visual_outcome") != "pass"
    ]
    assert drain_tasks == [], f"drain must not re-file decided scenes, got {drain_tasks}"


# ---------------------------------------------------------------------------
# T4 — wave 2 for late scenes
# ---------------------------------------------------------------------------

def test_streaming_wave2_for_late_scene(tmp_path):
    """T4: a scene offered after its pack filed joins the next pack-set;
    its top-1 is rejected and its top-2 is filed in the same pack-set's
    wave 2 and accepted — and the wave-2 call lands before retrieval
    completes. HEAD judges the scene only in the post-retrieval phase."""
    rows = {f"web:8:c0": _rejecting_model_row("web:8:c0")}
    client = FakeWebClient(rows=rows)
    budget = LaneVisionBudget("web", scene_count=16)
    streamer, results, published, _applied = _make_streamer(
        tmp_path, 16, client, budget
    )
    retrieval_done = threading.Event()
    try:
        for idx in range(8):
            assert streamer.offer(idx, _scene_result(idx, tmp_path))
        assert _wait_until(lambda: len(client.calls) >= 1, timeout=5.0)
        for idx in range(8, 16):
            assert streamer.offer(
                idx, _scene_result(idx, tmp_path, n_candidates=2)
            )
        assert _wait_until(lambda: len(client.calls) >= 3, timeout=5.0), \
            f"pack-set 2 must file wave 1 + wave 2 before retrieval ends, got {len(client.calls)} calls"
        assert not retrieval_done.is_set()
        wave2_call = client.calls[2]
        assert wave2_call["ids"] == ["web:8:c1"], \
            f"wave 2 must file scene 8's top-2, got {wave2_call['ids']}"
    finally:
        retrieval_done.set()
        streamer.close()
    assert results[8] is not None and results[8].get("visual_outcome") == "pass"
    assert results[8].get("path", "").endswith("web_8_1.jpg"), \
        "scene 8's winner must be its top-2 candidate"
    assert any(idx == 8 for idx, _at in published)


# ---------------------------------------------------------------------------
# T5 — budget closes mid-retrieval
# ---------------------------------------------------------------------------

def test_streaming_budget_closes_mid_retrieval(tmp_path):
    """T5: scenes completing after the lane budget expires get no vision
    call; they stay undecided (never auto-passed) for the drain, which
    retains them web_visual_grace_deadline; and the fake late-web seeder
    refuses a deterministic stamp for them. HEAD's streamer (absent)
    files past expiry, or the scene is seeded as accepted."""
    clock = FakeClock(1000.0)
    budget = LaneVisionBudget("web", scene_count=13, clock=clock)
    budget.start_if_needed()  # clock starts at 1000, deadline 1325
    client = FakeWebClient()
    streamer, results, _published, _applied = _make_streamer(
        tmp_path, 13, client, budget
    )
    try:
        for idx in range(8):
            assert streamer.offer(idx, _scene_result(idx, tmp_path))
        assert _wait_until(lambda: len(client.calls) >= 1, timeout=5.0)
        calls_before_expiry = len(client.calls)
        clock.now = 2000.0  # past the web deadline (1325)
        assert budget.expired()
        for idx in range(8, 13):
            assert not streamer.offer(idx, _scene_result(idx, tmp_path)), \
                "an offer after budget expiry must not enter the streamer"
        time.sleep(0.6)
        assert len(client.calls) == calls_before_expiry, \
            "no vision call may be filed after the lane budget expired"
    finally:
        streamer.close()
    decided = streamer.decided_snapshot()
    assert decided == set(range(8)), f"late scenes must stay undecided, got {sorted(decided)}"
    for idx in range(8, 13):
        assert results[idx] is None or results[idx].get("visual_outcome") != "pass", \
            f"scene {idx} must never be auto-passed after budget expiry"
    # The drain's retention stamp is a deterministic stamp — the
    # late-web seeder must refuse it (d0f80b7 rule, unchanged).
    still = _image(tmp_path, "still.jpg")
    scene = {"scene_index": 8, "text": "Grand Theft Auto VI street scene",
             "visual_requirement": {"strict_identity": True}}
    for status in ("deterministic_web_image", "deterministic_only", "deterministic_degraded"):
        candidate = {
            "path": still, "source": "web",
            "qwen_result": {
                "accepted": True, "status": "accepted",
                "verification_status": status, "final_score": 80.0,
                "visual_checked": False, "semantic_verified": False,
            },
        }
        assert _late_web_branch_accept(scene, candidate) is None, \
            f"{status} must never seed as accepted"


# ---------------------------------------------------------------------------
# T6 — late-web wait gate (§3)
# ---------------------------------------------------------------------------

class _CountingVerifier:
    def __init__(self):
        self.calls = []

    def verify_batch(self, _scene, _candidates):
        self.calls.append(("batch", [str(c.get("path")) for c in _candidates]))
        return {}

    def __call__(self, _scene, _candidate):
        self.calls.append(("single", str(_candidate.get("path"))))
        return {"accepted": True, "status": "visual_pass", "final_score": 95.0,
                "visual_checked": True, "visual_available": True}


def _run_coordinator_with_web_lane(web_lane, budgets, deadline_seconds=2):
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    def yt_lane(_scene, _cancel):
        return []

    return resolve_storyboard_media_v2(
        [{"scene_index": 0, "scene_id": "gta", "text": "Grand Theft Auto VI street scene"}],
        youtube_lane=yt_lane,
        web_lane=web_lane,
        visual_verifier=_CountingVerifier(),
        neutral_factory=lambda _scene: None,
        deadline_seconds=deadline_seconds,
        vision_lane_budgets=budgets,
    )


def test_late_web_wait_skipped_when_loader_still_in_retrieval(monkeypatch, capsys):
    """T6 (negative): fake web lane still loading, no rows, web budget
    clock never started -> the 60 s late-web wait is skipped (elapsed
    < 8 s, skip line logged). HEAD burns the full 60 s (waited≈60.0s)."""
    monkeypatch.setenv("VIDZEO_WEB_LATE_WAIT_SECONDS", "60")
    budgets = create_lane_budgets(1)["budgets"]
    assert budgets["web"].started_at is None

    def slow_loader():
        time.sleep(120.0)
        return {}

    lane = LazyBatchLane(slow_loader)
    started = time.monotonic()
    _run_coordinator_with_web_lane(lane, budgets)
    elapsed = time.monotonic() - started
    out = capsys.readouterr().out
    assert "discovered at late-web: web=0 youtube=0 scenes_with_web=0/1" in out
    assert "web late-wait skipped: loader still in retrieval" in out
    assert "needing=1 scene(s)" in out
    assert elapsed < 8.0, f"wait must be skipped, but the run took {elapsed:.1f}s"


def test_late_web_wait_still_runs_when_visual_phase_started(monkeypatch, capsys):
    """T6 (positive): web budget clock started (visual phase) -> the
    wait still runs (bounded, returns on row arrival) and is not
    skipped."""
    monkeypatch.setenv("VIDZEO_WEB_LATE_WAIT_SECONDS", "60")
    budgets = create_lane_budgets(1)["budgets"]
    budgets["web"].start_if_needed()
    assert budgets["web"].started_at is not None

    def fast_loader():
        time.sleep(0.5)
        return {0: {"path": "web_still.jpg", "source": "web",
                    "provider": "web", "final_score": 80.0}}

    lane = LazyBatchLane(fast_loader)
    started = time.monotonic()
    _run_coordinator_with_web_lane(lane, budgets)
    elapsed = time.monotonic() - started
    out = capsys.readouterr().out
    assert "web late-wait skipped" not in out
    assert elapsed < 8.0, f"bounded wait must return on row arrival, took {elapsed:.1f}s"


# ---------------------------------------------------------------------------
# T7 — loud failure (§5)
# ---------------------------------------------------------------------------

def test_web_lane_context_missing_raises(monkeypatch):
    """T7: adapter load_web with a verifier present but no web lane child
    -> RuntimeError naming the site (primary / recovery / stock).
    Control: verifier is None -> existing behaviour preserved (the
    disabled transport context is returned, no raise). HEAD falls back
    to the disabled transport context silently; no error raised."""
    from services.pipeline_v2_media_adapter import _web_lane_visual_context

    transport = SimpleNamespace(disabled=True)
    verifier = SimpleNamespace(lane_visual_contexts={})
    for site in ("primary", "recovery", "stock"):
        with pytest.raises(RuntimeError, match=f"site={site}"):
            _web_lane_visual_context(verifier, transport, site)
    # A verifier with a real web child returns the child.
    child = SimpleNamespace(lane="web")
    verifier_ok = SimpleNamespace(lane_visual_contexts={"web": child})
    assert _web_lane_visual_context(verifier_ok, transport, "primary") is child
    # Control: no verifier at all -> existing behaviour preserved.
    assert _web_lane_visual_context(None, transport, "primary") is transport
    # Control: a verifier with no lane_visual_contexts attribute at all
    # (a legacy caller / test double bypassing build_qwen_verifier,
    # which always sets the map) keeps the old fallback — no lane child
    # was demonstrably expected there.
    assert _web_lane_visual_context(SimpleNamespace(), transport, "primary") is transport
    # And the three load_web sites actually use the helper (no silent
    # `or transport_visual_context` fallback remains in the file).
    adapter_path = Path(__file__).resolve().parents[1] / "server" / "services" / "pipeline_v2_media_adapter.py"
    assert "or transport_visual_context" not in adapter_path.read_text()


# ---------------------------------------------------------------------------
# T8 — fail-faster streak cap (§2.1)
# ---------------------------------------------------------------------------

def test_empty_streak_cap_skips_retry_waves(monkeypatch):
    """T8: fake engines that all stall: 3 consecutive all-empty batches
    (each paying its retry waves), then a 4th query — the 4th batch's
    retry waves are skipped (1 engine run, not 3). Control: a batch
    returning rows still returns them and resets the counter, so the
    next stalled batch retries again. HEAD pays full retry waves on
    the 4th batch too."""
    import requests
    from services.web_image.universal_scraper import (
        UniversalImageResult,
        UniversalScraperManager,
        _universal_empty_streak_cap,
    )

    monkeypatch.setenv("VIDZEO_UNIVERSAL_EMPTY_STREAK_CAP", "3")
    monkeypatch.setenv("VIDZEO_UNIVERSAL_EMPTY_RETRY_WAVES", "2")
    monkeypatch.setenv("VIDZEO_UNIVERSAL_EMPTY_RETRY_BACKOFF_SECONDS", "0")
    assert _universal_empty_streak_cap() == 3

    manager = UniversalScraperManager(max_workers=2, engine_timeout_seconds=5)
    runs = {"count": 0, "return_rows": False}

    class StallEngine:
        name = "stall"

        def search(self, query, limit, page, safe_search, direct=False):
            runs["count"] += 1
            if runs["return_rows"]:
                return [UniversalImageResult(
                    title="stub", image_url="https://images.example/stub.jpg",
                    source_engine="stall",
                )]
            raise requests.exceptions.ReadTimeout("stalling engine timed out")

    manager._engines = {"stall": StallEngine()}
    try:
        per_batch = []
        for _ in range(4):
            before = runs["count"]
            resp = manager.search("moon", engine_ids=["stall"])
            per_batch.append(runs["count"] - before)
            assert resp.total == 0
        assert per_batch == [3, 3, 3, 1], \
            f"first 3 batches pay 1+2 retry waves, the 4th pays none, got {per_batch}"
        assert manager.consecutive_empty_stalled_batches == 4
        # Control: a batch returning rows resets the counter...
        runs["return_rows"] = True
        before = runs["count"]
        resp = manager.search("moon", engine_ids=["stall"])
        assert resp.total == 1
        assert manager.consecutive_empty_stalled_batches == 0
        # ...so the next stalled batch retries again.
        runs["return_rows"] = False
        before = runs["count"]
        resp = manager.search("moon", engine_ids=["stall"])
        assert resp.total == 0
        assert runs["count"] - before == 3, "retries must resume after a reset"
    finally:
        manager._executor.shutdown(wait=True)


def test_rescue_skipped_when_empty_streak_at_cap(monkeypatch):
    """T8 (rescue gate): when the scraper's empty-streak counter is at
    its cap, the resolver's rescue pass for that query is 0 attempts —
    the rescue must not re-pay a wall the retry cap just refused.
    Control (streak 0): rescue attempts run as configured."""
    from services.web_image import resolver

    def _failed_stat(engine, error="ReadTimeout"):
        return SimpleNamespace(
            engine=engine, count=0, duration_ms=15000, success=False, error=error
        )

    def install_fake(search_fn, streak, rescue_attempts="3"):
        monkeypatch.delenv("VIDZEO_UNIVERSAL_SCRAPER_TIMEOUT_SECONDS", raising=False)
        monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RETRY_BACKOFF_SECONDS", "0")
        monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RESCUE_ATTEMPTS", rescue_attempts)
        monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RESCUE_BACKOFF_SECONDS", "0")
        monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RESCUE_DEADLINE_SECONDS", "60")
        monkeypatch.setenv("VIDZEO_UNIVERSAL_EMPTY_STREAK_CAP", "3")

        def search_via_pool(query, **options):
            return search_fn(query, **options)

        fake = SimpleNamespace(
            search=search_via_pool,
            collect_late_results=lambda _q, **_: [],
            progress_snapshot=lambda: {"consecutive_empty_stalled_batches": streak},
        )
        monkeypatch.setattr(resolver, "_UNIVERSAL_SCRAPER", fake)
        monkeypatch.setattr(
            resolver, "universal_result_to_candidate",
            lambda item, **_: {"url": item.image_url},
        )
        monkeypatch.setattr(resolver, "candidate_is_safe", lambda _c: (True, "fixture"))
        monkeypatch.setattr(resolver, "_report_provider_success", lambda _s: None)
        monkeypatch.setattr(resolver, "_report_provider_failure", lambda *_a: None)

    calls = []

    def search(query, **options):
        calls.append(options.get("direct"))
        stats = [_failed_stat("bing"), _failed_stat("yandex")]
        return resolver.UniversalSearchResponse(query, 0, [], stats)

    # Streak at cap: 2 main attempts, zero rescue attempts.
    install_fake(search, streak=3)
    rows = resolver._discover_universal_urls("Moon phases")
    assert rows == []
    assert calls == [False, True], f"rescue must be skipped at cap, got {calls}"
    assert resolver._universal_audit_snapshot()["rescue_attempts"] == 0

    # Control: streak 0 -> rescue attempts run (2 main + 3 rescue).
    calls.clear()
    install_fake(search, streak=0)
    rows = resolver._discover_universal_urls("Moon phases")
    assert rows == []
    assert calls == [False, True, True, False, True], \
        f"rescue must run below the cap, got {calls}"
    assert resolver._universal_audit_snapshot()["rescue_attempts"] == 3
