"""Round 12 (Nov 23, 2026): web + YouTube must both land on every scene.

Job 9ab2eb05 (GTA VI 30-sec, 8 scenes) COMPLETED with web 6/8,
YouTube 0/7, graphics 2/8. Diagnosis, grounded in the job's own log
+ code:

YT lane — the transcript stage died, not search or ranking:
- search returned 53 candidates (6h disk cache); live queries timed out.
- the session probe timed out at its 15s read bound (unverified).
- transcript: 0/8 completed at the 120s deadline; every fetch was
  still in flight (yt-dlp "Read timed out. (read timeout=15.0)").
- download: 0 attempts (no transcripts -> no moments -> no downloads).
- the coordinator gave up at the 263s branch wall while the YT worker
  kept running to 870s+.
- CORRECTION to round 11: the anonymous retry never ran because its
  trigger (empty pools) was not met — the first pass returned the 53
  cached candidates. The failure was downstream, not the retry.

Web lane (scenes 6-7 fell to graphics):
- web lane vision budget 200s (25s/scene x 8) expired with 2 scenes
  left unverdicted ("[web] stream vision: lane budget expired, 2
  scene(s) left unverdicted for drain").
- the rescue granted a 300s window with 0 headroom but refreshed only
  the shared context deadline — live verification is charged against
  the per-lane budget clock, which the grant never extended — so no
  live verification happened in the rescue.

Fix (user order Nov 23, refined 22:08 PKT: "timeouts aren't
important, the media is more important"; web + vision have NO
timeouts at all; YT MAY keep a timeout and falls back to web per
scene in the go):
- YT per-call timeouts 15s -> 60s, transcript deadlines 120s -> 300s,
  probe 8/15s -> 15/30s, branch wall 263s -> 505s (delivery allowance).
- Vision lane budgets UNBOUNDED by default (time + calls); the
  slope/floor formula (90s/scene, 300s floor) is the bounded path.
- Web waits until complete by default (VIDZEO_WEB_WAIT_UNTIL_COMPLETE,
  default on): discovery exempt from the media deadline, web recovery
  and recovery-visual tails unbounded, late-web wait uncapped, the
  parallel gate and per-scene vision windows do not refuse web.
- The rescue grant extends the web lane budget's own clock too.
- Round-12 follow-up (the failing fallback test): an *infinite*
  deadline is not a waitable timeout. CPython's Condition.wait /
  Lock.acquire raise OverflowError on timeout=inf when the wait
  actually has to block, so every place the "no timeouts" change
  feeds inf into a timed wait must pass timeout=None instead:
  consider_recovery's future.result, the adaptive FIRST_COMPLETED
  wait, the visual context's lane-budget slot acquire, and
  LazyBatchLane.get_row's late-web wait. The YT-timeout -> web
  fallback died on exactly this (error:OverflowError, 0 rows).

Every test here FAILS on clean 9c13e9d and PASSES with the fix.
"""

import math
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))


class _FakeClock:
    def __init__(self, now):
        self.now = float(now)

    def __call__(self):
        return float(self.now)


class _AcceptingVerifier:
    """Live vision that accepts every candidate; records every call."""

    def __init__(self):
        self.calls = []

    def __call__(self, _scene, _candidate):
        self.calls.append(str(_candidate.get("path")))
        return {"accepted": True, "status": "visual_pass", "final_score": 95.0,
                "visual_checked": True, "visual_available": True}


class _StubVisualContext:
    """Minimal VisualDecisionContext shape for the rescue-grant test."""

    def __init__(self, lane_budgets):
        self.deadline_at = time.monotonic() - 100.0  # already expired
        self.max_calls = 10
        self.calls = 0
        self.degraded_reason = "visual_deadline_exceeded"
        self.lane_budgets = lane_budgets
        self._lock = threading.Lock()

    def refresh_deadline(self, deadline_at=None, deadline_seconds=None):
        if deadline_at is not None:
            self.deadline_at = float(deadline_at)
        if self.degraded_reason == "visual_deadline_exceeded":
            self.degraded_reason = ""


def _r12_scene():
    return {"scene_index": 0, "scene_id": "gta",
            "text": "Grand Theft Auto VI street scene"}


# ---------------------------------------------------------------------------
# YT lane: per-call timeouts, transcript deadlines, probe, wall
# ---------------------------------------------------------------------------

def test_yt_metadata_socket_timeout_default_60(monkeypatch):
    """9ab2eb05: metadata/transcript fetches died on 15s read timeouts
    in a slow hour. The per-attempt bound is now 60s."""
    from services.youtube_pipeline_v2 import _youtube_metadata_socket_timeout

    monkeypatch.delenv("VIDZEO_YT_METADATA_SOCKET_TIMEOUT", raising=False)
    assert _youtube_metadata_socket_timeout() == 60
    monkeypatch.setenv("VIDZEO_YT_METADATA_SOCKET_TIMEOUT", "15")
    assert _youtube_metadata_socket_timeout() == 15


def test_yt_transcript_deadlines_default_300(monkeypatch):
    """9ab2eb05: the 120s deadline retained 0/8 transcripts — every
    fetch was still in flight when it fired. The ceiling is now 300s."""
    from services.youtube_pipeline_v2 import (
        _yt_pool_transcript_deadline_seconds,
        _yt_ranged_transcript_deadline_seconds,
    )

    monkeypatch.delenv("VIDZEO_YT_TRANSCRIPT_DEADLINE_SECONDS", raising=False)
    assert _yt_pool_transcript_deadline_seconds() == 300.0
    assert _yt_ranged_transcript_deadline_seconds() == 300.0
    monkeypatch.setenv("VIDZEO_YT_TRANSCRIPT_DEADLINE_SECONDS", "600")
    assert _yt_pool_transcript_deadline_seconds() == 300.0
    assert _yt_ranged_transcript_deadline_seconds() == 300.0
    monkeypatch.setenv("VIDZEO_YT_TRANSCRIPT_DEADLINE_SECONDS", "60")
    assert _yt_pool_transcript_deadline_seconds() == 60.0


def test_yt_transcript_provider_timeouts_default_60(monkeypatch):
    """The transcript provider chain (yt-dlp caption-track fetch, the
    caption download, the transcript-api leg) all timed out at 15s/30s
    in 9ab2eb05's slow hour. All three are now 60s."""
    from services.youtube_transcript_index import (
        _api_timeout_seconds,
        _transcript_caption_timeout_seconds,
        _transcript_client_timeout_seconds,
    )

    monkeypatch.delenv("VIDZEO_YT_TRANSCRIPT_API_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("VIDZEO_YT_TRANSCRIPT_CLIENT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("VIDZEO_YT_TRANSCRIPT_CAPTION_TIMEOUT_SECONDS", raising=False)
    assert _api_timeout_seconds() == 60.0
    assert _transcript_client_timeout_seconds() == 60.0
    assert _transcript_caption_timeout_seconds() == 60.0
    monkeypatch.setenv("VIDZEO_YT_TRANSCRIPT_CLIENT_TIMEOUT_SECONDS", "15")
    assert _transcript_client_timeout_seconds() == 15.0


def test_yt_auth_probe_timeout_default_15_30(monkeypatch):
    """9ab2eb05: the session probe timed out at its 15s read bound and
    left a live account unverified for the whole job. Connect 15s /
    read 30s now."""
    from services.youtube_session import _auth_probe_timeout

    monkeypatch.delenv("VIDZEO_YT_AUTH_CONNECT_TIMEOUT", raising=False)
    monkeypatch.delenv("VIDZEO_YT_AUTH_READ_TIMEOUT", raising=False)
    assert _auth_probe_timeout() == (15.0, 30.0)
    monkeypatch.setenv("VIDZEO_YT_AUTH_READ_TIMEOUT", "15")
    assert _auth_probe_timeout() == (15.0, 15.0)


def test_yt_wall_prices_9ab2eb05_at_delivery_allowance():
    """9ab2eb05's own numbers: 7 scenes, 505.136s delivery allowance,
    4 fitted workers. The old caps priced this at a 263s wall and the
    coordinator gave up there; the new caps price it at the full
    allowance, with a 120s search cap."""
    from services.pipeline_v2_media_adapter import mixed_youtube_time_budget

    assert mixed_youtube_time_budget(
        7, 505.136, download_workers=4, coverage_target=1.0
    ) == (505.136, 120.0)


def test_yt_wall_old_values_restored_by_knobs(monkeypatch):
    """The old 263s wall is restorable via the three VIDZEO_YT_WALL_*
    knobs — the loosened wall is a default, not a removal."""
    from services.pipeline_v2_media_adapter import mixed_youtube_time_budget

    monkeypatch.setenv("VIDZEO_YT_WALL_BASE_CAP_SECONDS", "240")
    monkeypatch.setenv("VIDZEO_YT_WALL_SEARCH_CAP_SECONDS", "35")
    monkeypatch.setenv("VIDZEO_YT_WALL_PER_WAVE_SECONDS", "80")
    assert mixed_youtube_time_budget(
        7, 505.136, download_workers=4, coverage_target=1.0
    ) == (263.0, 35.0)


# ---------------------------------------------------------------------------
# Vision: no timeouts at all (unbounded lane budgets by default)
# ---------------------------------------------------------------------------

def test_vision_lane_budgets_unbounded_by_default(monkeypatch):
    """User order (refined 22:08 PKT): vision calls are not cut off by
    a budget — a scene's verdict waits for vision. Default budgets
    carry an infinite clock and effectively-unbounded calls: a fake
    clock 100,000s past the start still leaves the lanes open, and
    100 recorded calls sail past the old 2N+4=20 web call cap."""
    from services.visual_intelligence.lane_budget import create_lane_budgets

    monkeypatch.delenv("VIDZEO_VISION_LANE_UNBOUNDED", raising=False)
    monkeypatch.delenv("VIDZEO_VISION_LANE_BUDGET_FIT_WALL", raising=False)
    clock = _FakeClock(1000.0)
    created = create_lane_budgets(8, clock=clock)
    budgets = created["budgets"]
    assert math.isinf(budgets["web"].budget_seconds)
    assert math.isinf(budgets["youtube"].budget_seconds)
    budgets["web"].start_if_needed()
    budgets["youtube"].start_if_needed()
    clock.now = 1000.0 + 100000.0
    assert not budgets["web"].expired()
    assert not budgets["youtube"].expired()
    assert sum(1 for _ in range(100) if budgets["web"].try_record_call()) == 100


def test_vision_lane_budgets_bounded_when_unbounded_off(monkeypatch):
    """Bounded path (VIDZEO_VISION_LANE_UNBOUNDED=0): the slope/floor
    formula is 90s/scene with a 300s floor (was 25/30s per scene, 180s
    floor — the 200s web budget that expired on 9ab2eb05). No shrink
    to the outer wall by default; FIT_WALL=1 restores the old shrink
    (reserve 0, both lanes to their 60s floors)."""
    from services.visual_intelligence.lane_budget import (
        create_lane_budgets,
        lane_budget_floor_seconds,
        lane_budget_seconds,
    )

    monkeypatch.setenv("VIDZEO_VISION_LANE_UNBOUNDED", "0")
    monkeypatch.delenv("VIDZEO_VISION_LANE_BUDGET_FIT_WALL", raising=False)
    assert lane_budget_seconds("web", 8) == 720.0
    assert lane_budget_seconds("youtube", 8) == 720.0
    assert lane_budget_floor_seconds() == 300
    clock = _FakeClock(1000.0)
    created = create_lane_budgets(8, clock=clock)
    assert created["budgets"]["web"].budget_seconds == 720.0
    assert created["budgets"]["web"].max_calls == 20  # 2N+4
    assert created["budgets"]["youtube"].max_calls == 28  # 3N+4
    # A wall that cannot hold both budgets does NOT shrink them now.
    noshrink = create_lane_budgets(8, outer_wall_seconds=100.0, clock=clock)
    assert noshrink["budgets"]["web"].budget_seconds == 720.0
    assert noshrink["budgets"]["youtube"].budget_seconds == 720.0
    # FIT_WALL=1 restores the old shrink behaviour.
    monkeypatch.setenv("VIDZEO_VISION_LANE_BUDGET_FIT_WALL", "1")
    shrunk = create_lane_budgets(8, outer_wall_seconds=100.0, clock=clock)
    assert shrunk["budgets"]["web"].budget_seconds == 60.0
    assert shrunk["budgets"]["youtube"].budget_seconds == 60.0
    assert shrunk["reserve"].remaining() == 0


def test_lane_budget_extend_and_reset_deadline():
    """The rescue grant extends the lane clock (round 12): extend
    moves an expired budget's deadline forward and never shrinks it;
    reset puts it back exactly."""
    from services.visual_intelligence.lane_budget import LaneVisionBudget

    clock = _FakeClock(1000.0)
    budget = LaneVisionBudget("web", scene_count=8, budget_seconds=200.0, clock=clock)
    budget.start_if_needed()
    assert budget.deadline_at == pytest.approx(1200.0)
    clock.now = 1300.0
    assert budget.expired()
    old = budget.extend_deadline(2000.0)
    assert old == pytest.approx(1200.0)
    assert not budget.expired()
    assert budget.deadline_at == pytest.approx(2000.0)
    assert budget.extend_deadline(1500.0) == pytest.approx(2000.0)
    assert budget.deadline_at == pytest.approx(2000.0)
    budget.reset_deadline(old)
    assert budget.deadline_at == pytest.approx(1200.0)
    assert budget.expired()


def test_rescue_grant_extends_web_lane_budget(monkeypatch):
    """9ab2eb05: the rescue granted a 300s window but refreshed only
    the shared context deadline — live verification is charged against
    the per-lane budget clock, which stayed expired, so the rescue
    verified nothing live. The grant now extends the web lane clock
    too, and the restore puts it back exactly.

    Clock domains: the lane budget runs on the fake clock (1000->2000)
    but the grant computes its rescue deadline from time.monotonic().
    After a VM reboot monotonic is small (~270s), so the grant's
    deadline (monotonic + 300s) lands BELOW the lane's 1200s deadline
    and extend_deadline (never shrink) becomes a no-op — the test
    fails for no reason to do with the grant. Patch time.monotonic to
    the fake clock for the grant so both clocks share one domain.
    """
    from services.media_resolution_coordinator import (
        _grant_rescue_visual_budget,
        _restore_rescue_visual_budget,
    )
    from services.visual_intelligence.lane_budget import LaneVisionBudget

    clock = _FakeClock(1000.0)
    budget = LaneVisionBudget("web", scene_count=8, budget_seconds=200.0, clock=clock)
    budget.start_if_needed()
    clock.now = 2000.0  # past the 1200s lane deadline: expired
    assert budget.expired()
    context = _StubVisualContext({"web": budget})
    old_context_deadline = context.deadline_at
    monkeypatch.setattr(time, "monotonic", clock)
    record = _grant_rescue_visual_budget(context, 2, "external_agent", None)
    assert record is not None, "the grant must produce a window"
    assert record["lane_deadline_at"] == pytest.approx(1200.0)
    assert not budget.expired(), "the grant must extend the lane clock"
    assert budget.deadline_at == pytest.approx(record["rescue_deadline"])
    _restore_rescue_visual_budget(context, record)
    assert budget.deadline_at == pytest.approx(1200.0)
    assert budget.expired(), "the restore must put the lane clock back"
    assert context.deadline_at == pytest.approx(old_context_deadline)


# ---------------------------------------------------------------------------
# Web lane: no timeouts at all (waits until complete, default on)
# ---------------------------------------------------------------------------

def test_web_wait_until_complete_default_on(monkeypatch):
    """User order (refined 22:08 PKT): a web scene keeps going until it
    resolves a verified image. The flag is ON by default — an explicit
    job-config value wins, else VIDZEO_WEB_WAIT_UNTIL_COMPLETE (set
    it to "0" to restore the old deadline-bound web lane)."""
    from services.pipeline_v2_media_adapter import _web_wait_until_complete_enabled

    monkeypatch.delenv("VIDZEO_WEB_WAIT_UNTIL_COMPLETE", raising=False)
    assert _web_wait_until_complete_enabled({}) is True
    assert _web_wait_until_complete_enabled(None) is True
    monkeypatch.setenv("VIDZEO_WEB_WAIT_UNTIL_COMPLETE", "0")
    assert _web_wait_until_complete_enabled({}) is False
    assert _web_wait_until_complete_enabled({"web_wait_until_complete": True}) is True
    monkeypatch.delenv("VIDZEO_WEB_WAIT_UNTIL_COMPLETE", raising=False)
    assert _web_wait_until_complete_enabled({"web_wait_until_complete": False}) is False


def test_yt_timeout_falls_back_to_web_recovery_past_media_deadline(tmp_path):
    """Per-scene YT -> web fallback in the go, past the media deadline.

    The YT lane times out (sleeps past the 2s media deadline and
    returns nothing) and the primary web lane also returns nothing
    after 3s — the scene is unresolved at the 2s media deadline with
    zero grace. On clean 9c13e9d the web recovery wave is skipped
    ("skipped_after_media_deadline") and the scene falls to a graphic.
    With the fix + web_wait_until_complete, the web recovery tail is
    unbounded: the fallback lane's verified image resolves the scene
    instead. No scene ends up graphic just because a budget fired.

    Note: the cross-lane recovery fast path accepts non-strict scenes
    on the hard gate without a live Qwen call (by design — generic
    documentary scenes do not need pixel-level verification on
    recovery candidates), so this test asserts provider + verified
    status, not a verifier call. The adaptive variant below (test 13)
    is the path where the row IS verified live.
    """
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    image = tmp_path / "web_still.jpg"
    image.write_bytes(b"\x00" * 1024)
    verifier = _AcceptingVerifier()

    def yt_lane(_scene, _cancel):
        time.sleep(3.0)  # past the 2s media deadline: provider timeout
        return []

    def web_lane(_scene, _cancel):
        time.sleep(3.0)
        return []

    def web_fallback_lane(_scene, _cancel):
        return [{"path": str(image), "source": "web", "provider": "web",
                 "media_type": "image", "candidate_id": "web:still",
                 "title": "Grand Theft Auto VI official screenshot",
                 "final_score": 80.0}]

    started = time.monotonic()
    result = resolve_storyboard_media_v2(
        [_r12_scene()],
        youtube_lane=yt_lane,
        web_lane=web_lane,
        web_fallback_lane=web_fallback_lane,
        visual_verifier=verifier,
        neutral_factory=lambda _scene: None,
        deadline_seconds=2,
        web_fallback_grace_seconds=0,
        web_wait_until_complete=True,
    )
    elapsed = time.monotonic() - started
    assert result[0].provider == "web", (
        f"YT timed out and web recovery must cover the scene, got {result[0].provider!r}"
    )
    assert result[0].selected_path == str(image)
    assert result[0].verification_status == "verified"
    assert elapsed < 30.0, f"recovery must stay bounded by the lanes, took {elapsed:.1f}s"


# ---------------------------------------------------------------------------
# Round-12 follow-up: infinite deadlines must wait with timeout=None
# (CPython raises OverflowError on timeout=inf when the wait blocks)
# ---------------------------------------------------------------------------

def test_yt_timeout_falls_back_to_web_adaptive_live_verified(tmp_path):
    """Adaptive review path: the YT-timeout -> web fallback with a slow
    fallback lane (5s) and adaptive_media_review on.

    The adaptive branch waits for the first recovery future with the
    (infinite) web recovery deadline. On clean 9c13e9d the finite web
    deadline expires first and the scene goes neutral. On round-12
    code without the timeout=None fix, wait(timeout=inf) raises
    OverflowError the moment it has to block. With the fix the wait
    holds until the fallback lane lands, the row is verified LIVE
    (adaptive disables the recovery fast path), and the scene
    resolves to web.
    """
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    image = tmp_path / "web_still.jpg"
    image.write_bytes(b"\x00" * 1024)
    verifier = _AcceptingVerifier()

    def yt_lane(_scene, _cancel):
        time.sleep(3.0)  # past the 2s media deadline: provider timeout
        return []

    def web_lane(_scene, _cancel):
        time.sleep(3.0)
        return []

    def web_fallback_lane(_scene, _cancel):
        time.sleep(5.0)  # slow lane: the adaptive wait must actually wait
        return [{"path": str(image), "source": "web", "provider": "web",
                 "media_type": "image", "candidate_id": "web:still",
                 "title": "Grand Theft Auto VI official screenshot",
                 "final_score": 80.0}]

    started = time.monotonic()
    result = resolve_storyboard_media_v2(
        [_r12_scene()],
        youtube_lane=yt_lane,
        web_lane=web_lane,
        web_fallback_lane=web_fallback_lane,
        visual_verifier=verifier,
        neutral_factory=lambda _scene: None,
        deadline_seconds=2,
        web_fallback_grace_seconds=0,
        web_wait_until_complete=True,
        adaptive_media_review=True,
    )
    elapsed = time.monotonic() - started
    assert result[0].provider == "web", (
        f"adaptive YT-timeout fallback must resolve to web, got {result[0].provider!r}"
    )
    assert result[0].selected_path == str(image)
    assert verifier.calls, "adaptive recovery must verify the row live"
    assert elapsed < 30.0, f"recovery must stay bounded by the lanes, took {elapsed:.1f}s"


def test_lazy_batch_lane_get_row_infinite_timeout_waits_for_publish():
    """LazyBatchLane.get_row(index, timeout=inf) must wait for the row
    instead of raising OverflowError (Condition.wait_for with an
    infinite timeout raises when it has to block). This is the
    late-web wait's primitive: with web_wait_until_complete the
    coordinator passes the unbounded remaining wait here.
    """
    from services.pipeline_v2_media_adapter import LazyBatchLane

    lane = LazyBatchLane(loader=lambda: {})
    lane._state = "loading"  # simulate a still-loading shared batch
    threading.Timer(0.3, lambda: lane.publish_row(0, {"path": "late.jpg"})).start()
    started = time.monotonic()
    assert lane.get_row(0, timeout=float("inf")) == {"path": "late.jpg"}
    assert time.monotonic() - started < 10.0


def test_visual_context_acquire_unbounded_budget_waits_for_slot(monkeypatch):
    """VisualDecisionContext._acquire_before_deadline with an unbounded
    lane budget (round 12 default) must wait for a busy slot instead
    of raising OverflowError (semaphore.acquire(timeout=inf) raises
    when the slot is busy). The slot frees after 0.3s; the acquire
    must return, not raise and not give up.
    """
    from services.visual_intelligence.context import VisualDecisionContext
    from services.visual_intelligence.lane_budget import create_lane_budgets

    monkeypatch.delenv("VIDZEO_VISION_LANE_UNBOUNDED", raising=False)
    monkeypatch.delenv("VIDZEO_VISION_LANE_BUDGET_FIT_WALL", raising=False)
    clock = _FakeClock(1000.0)
    created = create_lane_budgets(1, clock=clock)
    assert math.isinf(created["budgets"]["web"].budget_seconds)
    parent = VisualDecisionContext(
        enabled=False, api_key="", model="test", scene_count=1,
        max_calls=10, budget_usd=0.0, deadline_at=float("inf"),
    )
    child = parent.child("web", lane_budget=created["budgets"]["web"])
    sem = threading.BoundedSemaphore(1)
    assert sem.acquire(blocking=False)
    threading.Timer(0.3, sem.release).start()
    started = time.monotonic()
    child._acquire_before_deadline(sem)
    assert time.monotonic() - started < 10.0
