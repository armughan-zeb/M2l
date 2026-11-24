"""Separate per-lane vision capacity: one clock, one call budget, one
in-flight cap per lane (``youtube`` / ``web``), plus a lane-aware
scheduler that files vision batches.

This is the fix for test 26 (job ``ffe90224``): one shared absolute
vision deadline (``VisualDecisionContext.deadline_at``, 450 s for 13
scenes) was minted at context creation and ran through the whole web
retrieval phase (~525 s), so the web visual phase started 439 s past
its deadline and only a flat 240 s floor rescued it — 6 of 13 scenes
ended ``web_visual_grace_deadline``. The shared call counter was NOT
the binding constraint there (web logged ``calls=0/17``), but any
context both lanes share is a latent call-starvation channel, so calls
are partitioned too.

Design (``work/vision-lanes-design.md`` §3):

- Lane-aware scheduling, NOT a hard worker partition: two per-lane
  claim queues + one dispatcher; workers help the other lane only
  when their own queue is empty. Pool ceiling stays 8 (hard).
- In-flight caps: global 8, YT 5, web 4, guaranteed floor 3 each
  when both lanes are busy; the remaining 2 slots float to the lane
  whose oldest pending batch waited longest.
- Call budgets partition today's coordinator total (``6N+8``) exactly:
  YT ``3N+4``, web ``2N+4``, shared reserve ``N``.
- One clock per lane, starting at that lane's FIRST vision submission
  (never at construction/retrieval): YT ``max(180, 30*N)`` s, web
  ``max(180, 25*N)`` s. The slopes are sized for the derived per-call
  latency L~100-160 s on the external-agent path (design §7.1: L is
  unmeasured — do not silently re-derive the slopes).

Round 12 (Nov 23, 2026, user order: "timeouts aren't important, the
media is more important" — refined: no timeouts at all for vision):
the lane clocks and call counts are UNBOUNDED by default
(``VIDZEO_VISION_LANE_UNBOUNDED=1``): a scene's verdict waits for
vision, never expires on a budget. The slope/floor formula below is
kept as the *bounded* path (``VIDZEO_VISION_LANE_UNBOUNDED=0``),
with the slopes raised to 90 s/scene and the floor to 300 s, and the
shrink-to-wall step behind ``VIDZEO_VISION_LANE_BUDGET_FIT_WALL``
(default 0 = do not shrink). The job's media deadline and the
per-scene attempt caps remain the only outer bounds; the pool
ceiling (8) and per-host throttle are untouched.
- Web wave-1 batches: 8 scenes x 1 candidate per request; never file
  a web batch with < 4 scenes while >= 4 scenes are still unverified.
- Batch failure retries only missing candidates (half-size retry on
  submission failure, then singles); never re-judge a decided
  candidate.

Pure logic + threading; no provider calls. No new dependencies.
"""
from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Env knobs (design §4). Caps are hard; the global cap is not env-rais able.
# ---------------------------------------------------------------------------

GLOBAL_INFLIGHT_CAP = 8  # hard constant: the pool ceiling. Never raised.


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(os.environ.get(name, default) or default)))
    except (TypeError, ValueError):
        return default


def lane_seconds_per_scene(lane: str) -> int:
    """Vision-budget slope (seconds/scene) for a lane.

    Round 12 bounded path: 90 s/scene for both lanes (was YT 30, web
    25). Job 9ab2eb05's web retrieval spread ran 826 s while the web
    lane budget was 200 s (25*8) — the clock, not the call count, was
    the binding constraint. The default path is unbounded (see
    :func:`vision_lane_unbounded`); this slope only applies when the
    operator restores bounded budgets.
    """
    if str(lane or "").strip().lower() == "youtube":
        return _env_int("VIDZEO_VISION_LANE_YT_SECONDS_PER_SCENE", 90, 10, 120)
    return _env_int("VIDZEO_VISION_LANE_WEB_SECONDS_PER_SCENE", 90, 10, 120)


def lane_budget_floor_seconds() -> int:
    return _env_int("VIDZEO_VISION_LANE_BUDGET_FLOOR_SECONDS", 300, 60, 600)


def vision_lane_unbounded() -> bool:
    """True when lane vision budgets carry no time/call cutoff.

    Round 12 default (user order: no timeouts at all for vision — a
    scene's verdict waits for vision). Set
    ``VIDZEO_VISION_LANE_UNBOUNDED=0`` to restore the bounded
    slope/floor budgets.
    """
    raw = str(os.environ.get("VIDZEO_VISION_LANE_UNBOUNDED", "1") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def vision_lane_fit_wall() -> bool:
    """True when bounded lane budgets shrink to fit the outer wall.

    Round 12 default 0 (do not shrink): shrinking the lane budgets to
    the job wall re-imposes the timeout the unbounded default removes.
    Set ``VIDZEO_VISION_LANE_BUDGET_FIT_WALL=1`` to restore the old
    shrink behaviour on the bounded path.
    """
    raw = str(os.environ.get("VIDZEO_VISION_LANE_BUDGET_FIT_WALL", "0") or "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


# Effectively-unbounded call count for the unbounded path: no job files
# a million vision calls (the media deadline and per-scene attempt caps
# bound real usage); a float infinity cannot pass the int() cast in
# LaneVisionBudget.__init__.
_UNBOUNDED_CALLS = 10 ** 6


def lane_inflight_cap(lane: str) -> int:
    """Per-lane in-flight cap (vision requests concurrently filed)."""
    if str(lane or "").strip().lower() == "youtube":
        return _env_int("VIDZEO_VISION_LANE_YT_INFLIGHT", 5, 1, 8)
    return _env_int("VIDZEO_VISION_LANE_WEB_INFLIGHT", 4, 1, 8)


def lane_min_guaranteed() -> int:
    """Per-lane guaranteed in-flight floor when both lanes are busy."""
    return _env_int("VIDZEO_VISION_LANE_MIN_GUARANTEED", 3, 1, 4)


def web_vision_batch_images() -> int:
    """Web scenes per vision request (8; hard cap 16 = transport ceiling)."""
    return _env_int("VIDZEO_WEB_VISION_BATCH_IMAGES", 8, 1, 16)


def yt_vision_batch_candidates() -> int:
    """YouTube candidates per vision request (4; cap 8)."""
    return _env_int("VIDZEO_YT_VISION_BATCH_CANDIDATES", 4, 1, 8)


def yt_vision_batch_frames() -> int:
    """YouTube frames per candidate (2; cap 2)."""
    return _env_int("VIDZEO_YT_VISION_BATCH_FRAMES", 2, 1, 2)


def vision_batch_retry_attempts() -> int:
    """Batch retry attempts after the first (2; clamp 0-3)."""
    return _env_int("VIDZEO_VISION_BATCH_RETRY_ATTEMPTS", 2, 0, 3)


def split_call_budgets(scene_count: int) -> Dict[str, int]:
    """Partition today's coordinator call total (``ceil(6N)+8``) per lane.

    YT ``ceil(3N)+4``, web ``ceil(2N)+4``, shared reserve ``N``.
    The total is identical to today: ``6N+8`` (for N=13: 43 / 30 / 13).
    """
    n = max(0, int(scene_count or 0))
    return {
        "youtube": int(math.ceil(3 * n)) + 4,
        "web": int(math.ceil(2 * n)) + 4,
        "reserve": n,
        "total": int(math.ceil(6 * n)) + 8,
    }


def lane_budget_seconds(lane: str, scene_count: int) -> float:
    """One lane's vision budget: ``max(floor, slope * N)`` seconds.

    Round 12 bounded path: YT ``max(300, 90*N)``; web ``max(300,
    90*N)``. The floor (300 s) is one slow round trip + margin, so a
    1-scene job is never given a 90 s budget. NOTE: design §3.1.4
    step 2 cites a 120 s floor; §3.2 (the corrected final formula)
    says 180 s — 180 s was implemented before round 12 raised it to
    300 s. The default path is unbounded (see
    :func:`vision_lane_unbounded`); this formula only applies when
    the operator restores bounded budgets.
    """
    n = max(1, int(scene_count or 1))
    floor = float(lane_budget_floor_seconds())
    return max(floor, float(lane_seconds_per_scene(lane)) * n)


# ---------------------------------------------------------------------------
# Shared call reserve (either lane, first-come, logged).
# ---------------------------------------------------------------------------


class ReservePool:
    """The shared ``N``-call reserve. Not a third lane; files no work."""

    def __init__(self, total_calls: int):
        self.total = max(0, int(total_calls or 0))
        self._lock = threading.Lock()
        self.drawn: Dict[str, int] = {}

    def draw(self, lane: str) -> bool:
        """Draw one reserve call for ``lane``; False when exhausted."""
        with self._lock:
            if sum(self.drawn.values()) >= self.total:
                return False
            self.drawn[str(lane)] = self.drawn.get(str(lane), 0) + 1
            return True

    def remaining(self) -> int:
        with self._lock:
            return max(0, self.total - sum(self.drawn.values()))


# ---------------------------------------------------------------------------
# Per-lane budget: own deadline (starts at first submission), own calls,
# own in-flight count.
# ---------------------------------------------------------------------------


class LaneVisionBudget:
    """One lane's vision budget for one job.

    The clock starts at the lane's FIRST vision submission
    (:meth:`start_if_needed`), never at construction — construction
    time, retrieval time, and the other lane's time consume nothing.
    """

    def __init__(
        self,
        lane: str,
        *,
        scene_count: int,
        budget_seconds: Optional[float] = None,
        max_calls: Optional[int] = None,
        max_inflight: Optional[int] = None,
        reserve: Optional[ReservePool] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.lane = str(lane or "").strip().lower()
        self.scene_count = max(1, int(scene_count or 1))
        self.budget_seconds = float(
            budget_seconds
            if budget_seconds is not None
            else lane_budget_seconds(self.lane, self.scene_count)
        )
        split = split_call_budgets(self.scene_count)
        self.max_calls = int(
            max_calls if max_calls is not None else split.get(self.lane, 4)
        )
        self.max_inflight = int(
            max_inflight if max_inflight is not None else lane_inflight_cap(self.lane)
        )
        self.reserve = reserve
        self._clock = clock
        self._lock = threading.Lock()
        self.started_at: Optional[float] = None
        self.deadline_at: Optional[float] = None
        self.calls = 0
        self.reserve_calls = 0
        self.inflight = 0

    # -- clock -------------------------------------------------------------
    def start_if_needed(self, now: Optional[float] = None) -> float:
        """Start the lane clock at first submission; return ``deadline_at``."""
        with self._lock:
            if self.started_at is None:
                started = float(now if now is not None else self._clock())
                self.started_at = started
                self.deadline_at = started + self.budget_seconds
                budget_label = (
                    "unbounded"
                    if math.isinf(self.budget_seconds)
                    else f"{self.budget_seconds:.0f}s"
                )
                print(
                    f"[vision-lane] {self.lane} budget start: "
                    f"N={self.scene_count} budget={budget_label} "
                    f"deadline={self.deadline_at:.1f}",
                    flush=True,
                )
            return float(self.deadline_at)

    def remaining(self, now: Optional[float] = None) -> float:
        """Seconds left on this lane's clock (full budget before start)."""
        with self._lock:
            if self.deadline_at is None:
                return self.budget_seconds
            return max(0.0, self.deadline_at - float(now if now is not None else self._clock()))

    def expired(self, now: Optional[float] = None) -> bool:
        """True once this lane's own deadline has passed."""
        with self._lock:
            if self.deadline_at is None:
                return False
            return float(now if now is not None else self._clock()) >= self.deadline_at

    def extend_deadline(self, deadline_at: float) -> Optional[float]:
        """Extend this lane's deadline (never shrink); return the old one.

        Round 12 rescue support: the pre-stock rescue grants a visual
        window by refreshing the shared context deadline, but live
        verification is charged against this lane clock
        (``VisualDecisionContext._reserve_call`` raises on an expired
        lane budget), so the grant must extend the lane clock too —
        job 9ab2eb05 granted a 300 s rescue window and verified nothing
        live because this clock was never extended. Returns None when
        the clock never started (nothing to extend; the first
        submission starts it with the full budget). A no-op on an
        unbounded (infinite-deadline) budget.
        """
        with self._lock:
            old = self.deadline_at
            if old is None:
                return None
            new = float(deadline_at)
            if new > old:
                self.deadline_at = new
            return old

    def reset_deadline(self, deadline_at: Optional[float]) -> None:
        """Restore a deadline saved by :meth:`extend_deadline`.

        Used by the rescue restore step to put the lane clock back
        exactly where the grant found it. Unconditional by design —
        the caller owns the saved value.
        """
        with self._lock:
            self.deadline_at = deadline_at

    # -- calls --------------------------------------------------------------
    def try_record_call(self) -> bool:
        """Charge one vision call to this lane (own budget, then reserve).

        Starts the lane clock. Returns False when both the lane budget
        and the shared reserve are exhausted — the caller must treat
        the verdict as unavailable, never as a pass.
        """
        self.start_if_needed()
        with self._lock:
            if self.calls < self.max_calls:
                self.calls += 1
                return True
        if self.reserve is not None and self.reserve.draw(self.lane):
            with self._lock:
                self.reserve_calls += 1
            print(
                f"[vision-lane] {self.lane} drew shared reserve call "
                f"(reserve remaining={self.reserve.remaining()})",
                flush=True,
            )
            return True
        return False

    # -- in-flight ----------------------------------------------------------
    def try_acquire_inflight(self) -> bool:
        with self._lock:
            if self.inflight >= self.max_inflight:
                return False
            self.inflight += 1
            return True

    def release_inflight(self) -> None:
        with self._lock:
            self.inflight = max(0, self.inflight - 1)

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "lane": self.lane,
                "scene_count": self.scene_count,
                "budget_seconds": self.budget_seconds,
                "started_at": self.started_at,
                "deadline_at": self.deadline_at,
                "calls": self.calls,
                "reserve_calls": self.reserve_calls,
                "max_calls": self.max_calls,
                "max_inflight": self.max_inflight,
                "inflight": self.inflight,
            }


def create_lane_budgets(
    scene_count: int,
    *,
    config: Optional[dict] = None,
    outer_wall_seconds: Optional[float] = None,
    clock: Callable[[], float] = time.monotonic,
) -> Dict[str, Any]:
    """Build both lane budgets + the shared reserve for one job.

    Round 12 default: both lane budgets are UNBOUNDED (infinite clock,
    effectively-unbounded calls) — vision is never cut off by a budget
    and a scene's verdict waits for vision. No shrink to the outer
    wall happens on that path.

    Bounded path (``VIDZEO_VISION_LANE_UNBOUNDED=0``): lane budgets
    are ``min(formula, remaining delivery/media wall)``. When the wall
    cannot hold both floors, degrade in the design's order (§3.1.4)
    and log each step: (1) reserve to 0; (2) the second-starting lane
    (web) to its floor; (3) both proportionally, never below 60 s
    each; (4) never zero one lane while the other still has budget.
    The shrink step itself runs only when
    ``VIDZEO_VISION_LANE_BUDGET_FIT_WALL=1`` (default 0).
    """
    n = max(1, int(scene_count or 1))
    split = split_call_budgets(n)
    reserve = ReservePool(split["reserve"])
    if vision_lane_unbounded():
        # Infinite clock + effectively-unbounded calls (see
        # _UNBOUNDED_CALLS). The job's media deadline and the
        # per-scene attempt caps remain the only outer bounds.
        budgets = {
            "youtube": LaneVisionBudget(
                "youtube", scene_count=n, budget_seconds=float("inf"),
                max_calls=_UNBOUNDED_CALLS, reserve=reserve, clock=clock,
            ),
            "web": LaneVisionBudget(
                "web", scene_count=n, budget_seconds=float("inf"),
                max_calls=_UNBOUNDED_CALLS, reserve=reserve, clock=clock,
            ),
        }
        return {
            "budgets": budgets,
            "reserve": reserve,
            "degraded": ["lane vision budgets unbounded (round 12: no vision timeout)"],
        }
    yt_seconds = lane_budget_seconds("youtube", n)
    web_seconds = lane_budget_seconds("web", n)
    degraded: List[str] = []
    if vision_lane_fit_wall() and outer_wall_seconds is not None:
        wall = max(0.0, float(outer_wall_seconds))
        if yt_seconds + web_seconds > wall:
            reserve = ReservePool(0)
            degraded.append("shared reserve shrunk to 0 (job wall smaller than both lane floors)")
            if yt_seconds + web_seconds > wall:
                web_seconds = min(web_seconds, max(60.0, lane_budget_floor_seconds()))
                degraded.append(
                    f"web lane shrunk to its floor ({web_seconds:.0f}s)"
                )
            if yt_seconds + web_seconds > wall and wall > 0:
                # Proportional shrink, never below 60 s each, never zero.
                total = yt_seconds + web_seconds
                yt_seconds = max(60.0, wall * yt_seconds / total)
                web_seconds = max(60.0, wall * web_seconds / total)
                if yt_seconds + web_seconds > wall:
                    scale = wall / (yt_seconds + web_seconds)
                    yt_seconds = max(60.0, yt_seconds * scale)
                    web_seconds = max(60.0, web_seconds * scale)
                degraded.append(
                    f"both lanes shrunk proportionally to the job wall "
                    f"(yt={yt_seconds:.0f}s web={web_seconds:.0f}s)"
                )
    for note in degraded:
        print(f"[vision-lane] budget degraded: {note}", flush=True)
    budgets = {
        "youtube": LaneVisionBudget(
            "youtube", scene_count=n, budget_seconds=yt_seconds,
            max_calls=split["youtube"], reserve=reserve, clock=clock,
        ),
        "web": LaneVisionBudget(
            "web", scene_count=n, budget_seconds=web_seconds,
            max_calls=split["web"], reserve=reserve, clock=clock,
        ),
    }
    return {"budgets": budgets, "reserve": reserve, "degraded": degraded}


# ---------------------------------------------------------------------------
# Claim queues + lane-aware scheduler (not a hard worker partition).
# ---------------------------------------------------------------------------


@dataclass
class BatchJob:
    """One filed vision batch: a set of candidates for one lane."""

    lane: str
    batch_id: str
    candidate_ids: List[str]
    scene_indices: List[int] = field(default_factory=list)
    submitted_at: float = 0.0
    attempts: int = 0


class LaneClaimQueue:
    """One FIFO queue of batch jobs per lane."""

    def __init__(self, lane: str):
        self.lane = str(lane)
        self._lock = threading.Lock()
        self._jobs: List[BatchJob] = []

    def put(self, job: BatchJob) -> None:
        with self._lock:
            self._jobs.append(job)

    def pop(self) -> Optional[BatchJob]:
        with self._lock:
            return self._jobs.pop(0) if self._jobs else None

    def __len__(self) -> int:
        with self._lock:
            return len(self._jobs)


class LaneScheduler:
    """Dispatch vision batches across lanes under one global cap.

    Workers help the other lane only when their own queue is empty
    (work-stealing). While both lanes have pending work, each lane is
    guaranteed ``floor`` in-flight slots; the remaining slots float to
    the lane whose oldest pending batch waited longest. The global cap
    (8) is hard and never env-rais able.
    """

    def __init__(
        self,
        *,
        global_cap: int = GLOBAL_INFLIGHT_CAP,
        caps: Optional[Dict[str, int]] = None,
        floor: Optional[int] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.global_cap = int(global_cap)
        self.caps = {
            "youtube": int((caps or {}).get("youtube", lane_inflight_cap("youtube"))),
            "web": int((caps or {}).get("web", lane_inflight_cap("web"))),
        }
        self.floor = int(floor if floor is not None else lane_min_guaranteed())
        self._clock = clock
        self._cond = threading.Condition(threading.Lock())
        self._inflight: Dict[str, int] = {"youtube": 0, "web": 0}
        self._waiters: List[Dict[str, Any]] = []  # {lane, since}
        self._peak_global = 0
        self._peak_lane: Dict[str, int] = {"youtube": 0, "web": 0}
        self._decided: Dict[str, set] = {"youtube": set(), "web": set()}

    # -- in-flight accounting ----------------------------------------------
    @property
    def inflight(self) -> Dict[str, int]:
        with self._cond:
            return dict(self._inflight)

    @property
    def peak_global(self) -> int:
        with self._cond:
            return self._peak_global

    @property
    def peak_lane(self) -> Dict[str, int]:
        with self._cond:
            return dict(self._peak_lane)

    def _other_lane(self, lane: str) -> str:
        return "web" if str(lane) == "youtube" else "youtube"

    def _can_grant(self, lane: str) -> bool:
        lane = str(lane)
        other = self._other_lane(lane)
        if sum(self._inflight.values()) >= self.global_cap:
            return False
        if self._inflight.get(lane, 0) >= self.caps.get(lane, 0):
            return False
        # Guaranteed floor: never give this lane a slot above its floor
        # while the other lane has pending work and sits below its floor.
        if self._inflight.get(lane, 0) >= self.floor:
            other_waiting = any(w["lane"] == other for w in self._waiters)
            if other_waiting and self._inflight.get(other, 0) < self.floor:
                return False
            # Floating slots go to the lane whose oldest pending batch
            # waited longest; if this lane is not that lane, wait.
            if other_waiting and self._waiters:
                oldest = min(self._waiters, key=lambda w: w["since"])
                if oldest["lane"] == other:
                    return False
        return True

    def acquire(self, lane: str, timeout: Optional[float] = None) -> bool:
        """Block until this lane may file one more in-flight request."""
        lane = str(lane)
        deadline = None if timeout is None else self._clock() + max(0.0, timeout)
        with self._cond:
            waiter = {"lane": lane, "since": self._clock()}
            self._waiters.append(waiter)
            try:
                while True:
                    if self._can_grant(lane):
                        self._inflight[lane] = self._inflight.get(lane, 0) + 1
                        total = sum(self._inflight.values())
                        self._peak_global = max(self._peak_global, total)
                        self._peak_lane[lane] = max(
                            self._peak_lane.get(lane, 0), self._inflight[lane]
                        )
                        return True
                    if deadline is not None and self._clock() >= deadline:
                        return False
                    wait_for = None
                    if deadline is not None:
                        wait_for = max(0.0, deadline - self._clock())
                    self._cond.wait(timeout=wait_for)
            finally:
                self._waiters.remove(waiter)

    def release(self, lane: str) -> None:
        with self._cond:
            self._inflight[str(lane)] = max(0, self._inflight.get(str(lane), 0) - 1)
            self._cond.notify_all()

    # -- decided set ---------------------------------------------------------
    def is_decided(self, lane: str, candidate_id: str) -> bool:
        with self._cond:
            return str(candidate_id) in self._decided.get(str(lane), set())

    def mark_decided(self, lane: str, candidate_ids: Sequence[str]) -> None:
        with self._cond:
            self._decided.setdefault(str(lane), set()).update(
                str(cid) for cid in candidate_ids
            )

    # -- batch filing with retry-only-missing --------------------------------
    def file_batch(
        self,
        lane: str,
        batch_id: str,
        candidate_ids: Sequence[str],
        call: Callable[[List[str]], Dict[str, Any]],
        *,
        retry_attempts: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """File one batch; retry only its missing candidates.

        ``call(ids)`` performs one vision request for ``ids`` and
        returns ``{candidate_id: verdict}``. A partial return keeps the
        good rows and retries only the missing ids (packed with other
        missing ids from the same lane). A submission failure retries
        once at half size, then as singles. A decided candidate is
        never re-judged. Returns ``{candidate_id: verdict}`` for every
        candidate that produced a verdict; missing ids are simply
        absent (the caller treats them as vision-unavailable).
        """
        lane = str(lane)
        max_attempts = 1 + int(
            retry_attempts if retry_attempts is not None else vision_batch_retry_attempts()
        )
        wanted = [str(cid) for cid in candidate_ids if not self.is_decided(lane, cid)]
        results: Dict[str, Any] = {}
        attempts: Dict[str, int] = {cid: 0 for cid in wanted}
        queue: List[List[str]] = [list(wanted)] if wanted else []
        while queue:
            group = [cid for cid in queue.pop(0) if cid not in results]
            if not group:
                continue
            for cid in group:
                attempts[cid] = attempts.get(cid, 0) + 1
            try:
                if not self.acquire(lane, timeout=timeout):
                    break
                try:
                    returned = call(group) or {}
                finally:
                    self.release(lane)
            except Exception:
                # Submission failure: retry once at half size, then as
                # singles. A single-candidate failure is terminal for it.
                if len(group) > 1:
                    half = max(1, len(group) // 2)
                    retryable = [
                        cid for cid in group if attempts.get(cid, 0) < max_attempts
                    ]
                    if retryable:
                        queue.insert(0, retryable[half:])
                        queue.insert(0, retryable[:half])
                continue
            if not isinstance(returned, dict):
                returned = {}
            for cid, verdict in returned.items():
                if str(cid) in wanted and str(cid) not in results:
                    results[str(cid)] = verdict
            self.mark_decided(lane, list(results))
            missing = [
                cid for cid in group
                if cid not in results and attempts.get(cid, 0) < max_attempts
            ]
            if missing:
                queue.append(missing)
        return results


# ---------------------------------------------------------------------------
# Web batch packing + scaling helpers.
# ---------------------------------------------------------------------------


def pack_web_scenes(
    scene_indices: Sequence[int],
    *,
    batch_size: Optional[int] = None,
    min_pack: int = 4,
) -> List[List[int]]:
    """Pack web scenes into wave-1 batches of ``batch_size`` (default 8).

    Never file a batch with < ``min_pack`` scenes while >= ``min_pack``
    scenes are still unverified: a short tail is rebalanced from the
    previous batch (13 -> 8+5; 10 -> 6+4). The only short batch is the
    tail when fewer than ``min_pack`` scenes remain in total.
    """
    size = int(batch_size if batch_size is not None else web_vision_batch_images())
    size = max(1, min(16, size))
    indices = list(scene_indices)
    if not indices:
        return []
    chunks = [indices[i:i + size] for i in range(0, len(indices), size)]
    while len(chunks) > 1 and len(chunks[-1]) < min_pack:
        prev = chunks[-2]
        tail = chunks[-1]
        need = min_pack - len(tail)
        move = min(need, len(prev) - min_pack)
        if move <= 0:
            break
        tail[:0] = prev[-move:]
        del prev[-move:]
    return chunks


def web_wave_count(scene_count: int, *, inflight: Optional[int] = None) -> int:
    """Wave-1 latency rounds for the web lane at ``inflight`` concurrency."""
    n = max(0, int(scene_count or 0))
    batches = (n + web_vision_batch_images() - 1) // web_vision_batch_images()
    cap = int(inflight if inflight is not None else lane_inflight_cap("web"))
    return int(math.ceil(batches / max(1, cap)))


def yt_request_count(scene_count: int) -> int:
    """Worst-case YT vision requests: one batch per scene (early exit cuts most)."""
    return max(0, int(scene_count or 0))


def lane_budget_expired_outcome(visual_required: bool) -> Dict[str, str]:
    """Outcome for a scene whose lane budget expired with no verdict.

    A timeout / missing verdict is *unavailable*, never a pass — where
    a live verdict was required, nothing ships on a deterministic stamp
    alone (design §3.3.5).
    """
    return {
        "visual_outcome": "unavailable",
        "visual_reason": "lane_budget_expired",
        "visual_unverified": True,
    }
