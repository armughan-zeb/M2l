"""Web image resolution pipeline — parallel, multi-source, hierarchical.

Architecture:
  1. Query generation (prebuilt or mechanical fallback)
  2. Multi-source harvest (Flickr, Pinterest, Imgur via gallery-dl)
  3. Image validation (size, aspect ratio, format)
  4. Hierarchical scoring (identity > quality > relevance > source priority)
  5. Per-scene dedup then cross-scene dedup with reuse tracking
  6. Round-robin fallback for unresolved scenes
  7. Audit manifest (timestamped, full metadata)
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import re
import random
import shutil
import subprocess
import threading
import time
import urllib.parse
import uuid
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from ..job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from ..job_context import JobThread, check_job_active, current_job
from ..media_distinctness import (
    alternate_anchor_allows,
    content_identity as _distinct_content_identity,
    is_legacy_installment_image as _is_legacy_installment_image,
    narrated_installment_anchor as _narrated_installment_anchor,
    promote_deferred as _promote_deferred,
    source_host as _distinct_source_host,
)
from ..pipeline_activity import record_activity
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple, Union

from PIL import Image, ImageFile


def _bounded_worker_drain_seconds(stage_seconds: float) -> float:
    """Let finishing candidates settle without extending the retrieval wall."""
    try:
        requested = float(os.environ.get('VIDZEO_WEB_WORKER_DRAIN_SECONDS', '0.5'))
    except (ValueError, TypeError):
        requested = 0.5
    if not math.isfinite(requested):
        requested = 0.5
    return min(max(0.0, requested), 0.5, max(0.0, stage_seconds) * .1)


def _web_recovery_budget_floor_seconds(
    stage_deadline_seconds: float, remaining_seconds: float
) -> float:
    """Floor an exhaustive-recovery stage wall at one useful engine wave.

    Wright (Nov 16): the recovery run got only a ~22s wall and died
    before any engine could answer.  A recovery stage below one engine
    round-trip can never return a candidate.  The floor is bounded by
    the job's remaining wall time — never past the absolute deadline —
    and may dip into the render reserve by at most the floor itself.
    """
    try:
        floor = max(
            30.0,
            min(
                180.0,
                float(
                    os.environ.get("VIDZEO_WEB_RECOVERY_MIN_SECONDS", "60")
                    or 60
                ),
            ),
        )
    except (TypeError, ValueError):
        floor = 60.0
    try:
        remaining = float(remaining_seconds)
    except (TypeError, ValueError):
        return float(stage_deadline_seconds)
    if remaining <= 0.0:
        return float(stage_deadline_seconds)
    return max(float(stage_deadline_seconds), min(floor, remaining))


def _web_recovery_useful_floor_seconds(
    scene_count: int,
    scene_workers: int,
    batch_seconds: float = 45.0,
    prefetch_seconds: float = 10.0,
    per_scene_seconds: float = 30.0,
) -> float:
    """Minimum useful wall for one web-recovery stage, sized to the real
    phase budget instead of a flat 90s floor.

    Run-6 proof (job 453e5590, 7 scenes, 2 workers, 90s wall): the shared
    batch took ~24s, the shared-pool prefetch ~33s, and the first two scene
    workers were still unfinished after a further 32s — the 90s wall was
    spent before any scene could complete, so the branch resolved 0/7.
    The wall must cover discovery + prefetch + one scene wave per worker
    slot (``ceil(scenes / workers)`` waves at the measured per-scene local
    cost: multi-candidate downloads at 8s timeouts plus the deferred
    vision handoff).  7 scenes / 2 workers -> 45 + 10 + 4*30 = 175s.
    The delivery budget and the hard cap still bound it from above (see
    the exhaustive-fallback floor in ``resolve_web_images_for_video_fast``).
    """
    try:
        batch = max(0.0, float(batch_seconds))
    except (TypeError, ValueError):
        batch = 45.0
    try:
        prefetch = max(0.0, float(prefetch_seconds))
    except (TypeError, ValueError):
        prefetch = 10.0
    try:
        per_scene = max(1.0, float(per_scene_seconds))
    except (TypeError, ValueError):
        per_scene = 30.0
    waves = max(
        1,
        math.ceil(
            max(1, int(scene_count or 1)) / max(1, int(scene_workers or 1))
        ),
    )
    return max(90.0, batch + prefetch + waves * per_scene)


def _shared_batch_wall_seconds(
    batch_seconds_override: Optional[float],
    scene_count: int,
) -> float:
    """Shared-discovery wall: fit at least one full engine round-trip.

    Run-7 proof (job 6624244a, 9 scenes): the old 12s default — and the
    hard ``scene_count >= 8`` clamp to 12s — closed the shared batch
    before the universal lane could answer (its engines land at
    ~25-27s through the egress proxy), so the batch banked 0 candidates
    while 40 rows arrived minutes later into an orphaned cache entry.
    45s matches the adapter's recovery batch default and the resolver's
    own useful-floor batch (``_web_recovery_useful_floor_seconds``);
    the stage deadline still bounds the wait, an explicit operator
    value still wins, and the early-break still closes a fast batch
    early — 45s is a ceiling, not a fixed cost.  This changes wait
    time only: no new requests, no concurrency or throttle change.
    """
    try:
        wall = max(
            0.25,
            float(
                batch_seconds_override
                if batch_seconds_override is not None
                else os.environ.get("VIDZEO_WEB_BATCH_DISCOVERY_SECONDS", "45")
                or 45
            ),
        )
    except Exception:
        wall = 45.0
    return wall


def _web_shared_prefetch_seconds(stage_deadline_seconds: float) -> float:
    """Wall-clock budget for the shared-pool prefetch: at most 10s and at
    most 15% of the stage wall.

    Run-6 proof: the prefetch ran two sequential download rounds plus a
    pending-collection wait (~33s for 9 rows, 1/9 local) before any scene
    worker started, burning a third of the 90s wall.  The prefetch only
    warms the shared download cache — scene workers download their own
    picks through the same cache — so it must never starve the scene
    phase.  The caller runs it in a background thread and proceeds on
    timeout; in-flight downloads keep populating the cache.
    """
    try:
        stage = max(0.0, float(stage_deadline_seconds))
    except (TypeError, ValueError):
        stage = 0.0
    return min(10.0, max(3.0, stage * 0.15))


def _default_web_stage_deadline_seconds(scene_count: int) -> float:
    """Completion-scaled default stage wall: 12s per scene, floored at 45s.

    A 121-scene job gets 1452s instead of being cut off at the old fixed
    180s ceiling after ~35 scenes; a 2-scene job keeps its 45s floor.
    """
    return max(
        45.0,
        min(_web_stage_hard_cap_seconds(), max(1, int(scene_count)) * 12.0),
    )


def _web_stage_hard_cap_seconds() -> float:
    """Completion-oriented ceiling for one web retrieval stage.

    The old 180s ceiling cut a 121-scene job off after ~35 scenes: the
    stage wall must scale with the scenes left to resolve, not end at a
    fixed time. The default 1800s still bounds a wedged stage, and the
    delivery budget (render reserve) and the coordinator's outer wall clamp
    it further downstream.
    """
    try:
        return max(
            180.0,
            float(os.environ.get("VIDZEO_WEB_STAGE_HARD_CAP_SECONDS", "1800") or 1800),
        )
    except (TypeError, ValueError):
        return 1800.0


_WEB_STAGE_HARD_CAP_SECONDS = 1800.0
# NOTE (Nov 16): the per-scene 90s wall-clock cap
# (_WEB_SCENE_HARD_CAP_SECONDS) is REMOVED, not raised. User override:
# web has NO time limit — a working wave must never be cut off by a
# scene clock. The trip for a dead wave is the liveness watchdog
# (_web_liveness_window_seconds(), 600s of zero progress ->
# web_retrieval_stalled), and the enclosing stage deadline remains the
# outer wall. _scene_deadline() below is the only place the scene wall
# lived; an operator may still set VIDZEO_WEB_SCENE_DEADLINE_SECONDS
# to impose an explicit per-scene wall.


# ── Web-wave liveness (GTA fix, Nov 16) ──────────────────────────────────
# User override: web retrieval has NO time limit — being a bit late beats
# falling back to graphics. The per-query wave deadline therefore never
# cancels a live universal future. The counterweight is liveness, not a
# wall: a wave that shows ZERO progress (no engine completion, no rows)
# for a full window is provably dead and is flagged
# ``web_retrieval_stalled`` so the driver can resolve it instead of the
# stage sitting wedged forever. Any progress resets the window, so the
# wait is unbounded while engines are working, and only a provably-dead
# wave ever gives up.
#
# Interface for the driver / budget worker (worker D): each wave is a
# record in _WEB_WAVE_PROGRESS, readable via get_web_wave_progress(wave_id),
# last_web_wave_progress(), web_wave_progress_snapshot(), and
# web_wave_stalled_for_scene(scene_number). The universal manager's own
# progress clock is UniversalScraperManager.last_progress_at (monotonic)
# / progress_snapshot(). A wave's effective last-progress instant is
# max(wave record, manager clock).

def _web_liveness_window_seconds() -> float:
    """Bounded no-progress window before a web wave is declared stalled.

    Default 600s (10 min). That is >> the ~37s worst engine latency seen
    on GTA VI, >> the 53–200s stacked-timeout wall of a single engine
    call, and longer than the worst-case universal batch wall (~315s at a
    300s engine timeout + stagger; ~470s with the single all-empty
    retry), so a working batch can never trip it. Only a wave with no
    engine completing anything for ten full minutes is flagged.
    """
    try:
        return max(
            120.0,
            min(
                3600.0,
                float(
                    os.environ.get("VIDZEO_WEB_LIVENESS_WINDOW_SECONDS", "600")
                    or 600
                ),
            ),
        )
    except (TypeError, ValueError):
        return 600.0


_WEB_WAVE_PROGRESS: Dict[str, Dict[str, object]] = {}
_WEB_WAVE_PROGRESS_LOCK = threading.Lock()


def _web_wave_progress_new(
    wave_id: str, *, scene_number: int, query: str
) -> Dict[str, object]:
    now = time.monotonic()
    record = {
        "wave_id": wave_id,
        "scene_number": int(scene_number),
        "query": str(query or "")[:120],
        "started_at_monotonic": now,
        "started_at_wall": time.time(),
        "last_progress_at": now,
        "engines_completed": 0,
        "rows_arrived": 0,
        "stalled": False,
        "stall_reason": "",
        "stall_detected_at_monotonic": None,
        "last_event": "wave_started",
        "universal_detached": False,
    }
    with _WEB_WAVE_PROGRESS_LOCK:
        _WEB_WAVE_PROGRESS[wave_id] = record
        if len(_WEB_WAVE_PROGRESS) > 512:
            oldest = sorted(
                _WEB_WAVE_PROGRESS.values(),
                key=lambda rec: float(rec.get("started_at_monotonic") or 0.0),
            )[: len(_WEB_WAVE_PROGRESS) - 512]
            for rec in oldest:
                _WEB_WAVE_PROGRESS.pop(str(rec.get("wave_id") or ""), None)
    return record


def _web_wave_progress_touch(
    wave_id: str, *, engines_completed: int = 0, rows_arrived: int = 0, event: str = ""
) -> None:
    with _WEB_WAVE_PROGRESS_LOCK:
        record = _WEB_WAVE_PROGRESS.get(wave_id)
        if record is None:
            return
        record["last_progress_at"] = time.monotonic()
        record["engines_completed"] = int(record.get("engines_completed") or 0) + int(engines_completed or 0)
        record["rows_arrived"] = int(record.get("rows_arrived") or 0) + int(rows_arrived or 0)
        if event:
            record["last_event"] = str(event)[:160]
        if "detached" in str(event or ""):
            record["universal_detached"] = True


def _web_wave_progress_mark_stalled(
    wave_id: str, *, reason: str = "web_retrieval_stalled"
) -> Dict[str, object]:
    with _WEB_WAVE_PROGRESS_LOCK:
        record = _WEB_WAVE_PROGRESS.get(wave_id)
        if record is None:
            return {}
        record["stalled"] = True
        record["stall_reason"] = str(reason)
        record["stall_detected_at_monotonic"] = time.monotonic()
        record["last_event"] = f"stalled:{reason}"
        return dict(record)


def get_web_wave_progress(wave_id: str) -> Dict[str, object]:
    """Return one wave's progress record (copy) for the driver/coordinator."""
    with _WEB_WAVE_PROGRESS_LOCK:
        record = _WEB_WAVE_PROGRESS.get(str(wave_id or ""))
        return dict(record) if record else {}


def last_web_wave_progress() -> Dict[str, object]:
    """Return the most recently started wave's progress record (copy)."""
    with _WEB_WAVE_PROGRESS_LOCK:
        if not _WEB_WAVE_PROGRESS:
            return {}
        record = max(
            _WEB_WAVE_PROGRESS.values(),
            key=lambda rec: float(rec.get("started_at_monotonic") or 0.0),
        )
        return dict(record)


def web_wave_progress_snapshot() -> Dict[str, Dict[str, object]]:
    """Return copies of all known wave progress records, keyed by wave_id."""
    with _WEB_WAVE_PROGRESS_LOCK:
        return {key: dict(value) for key, value in _WEB_WAVE_PROGRESS.items()}


def web_wave_stalled_for_scene(scene_number: int) -> bool:
    """True when any wave for this 1-based scene was flagged stalled."""
    with _WEB_WAVE_PROGRESS_LOCK:
        return any(
            bool(rec.get("stalled"))
            and int(rec.get("scene_number") or 0) == int(scene_number)
            for rec in _WEB_WAVE_PROGRESS.values()
        )


def _universal_last_progress_at() -> float:
    """Monotonic clock of the universal manager's last engine completion."""
    try:
        return float(_UNIVERSAL_SCRAPER.last_progress_at or 0.0)
    except Exception:
        return 0.0


# Per-scene universal engine audits, keyed by one resolve_web_images_for_video_fast
# call's audit_token and 0-based scene index. _resolve_scene records every
# universal batch (engine, count, duration, error) here so the per-scene
# audit entry carries it even when the scene resolves to nothing — the
# module-global _UNIVERSAL_LAST_AUDIT alone is overwritten by the next
# scene and cannot do that. Entries are consumed (and dropped) when the
# audit manifest is built.
_SCENE_UNIVERSAL_AUDITS: Dict[str, Dict[int, Dict[str, object]]] = {}
_SCENE_UNIVERSAL_AUDITS_LOCK = threading.Lock()


def _record_universal_audit_entry(
    audits: List[Dict[str, object]],
    q_str: str,
    *,
    audit_token: Optional[str] = None,
    scene_index: Optional[int] = None,
) -> None:
    """Append the latest universal audit (if it belongs to ``q_str``).

    The snapshot carries per-engine {engine, count, duration_ms, success,
    error}; it is recorded once per query so the scene's audit entry shows
    every engine's failure instead of only a log grep. ``audits`` is the
    scene-local list; the token registry additionally covers scenes that
    return None (unresolved) and therefore have no candidate dict to
    attach the trail to.
    """
    snapshot = _universal_audit_snapshot()
    engines = snapshot.get("engines") if isinstance(snapshot, dict) else None
    if not isinstance(engines, list):
        return
    query = re.sub(r"\s+", " ", str(snapshot.get("query") or "")).strip().casefold()
    wanted = re.sub(r"\s+", " ", str(q_str or "")).strip().casefold()
    if not query or query != wanted:
        return
    if any(
        re.sub(r"\s+", " ", str(entry.get("query") or "")).strip().casefold() == query
        for entry in audits
    ):
        return
    entry = {
        "query": snapshot.get("query"),
        "total": snapshot.get("total"),
        "engines": [
            {
                "engine": engine.get("engine"),
                "count": engine.get("count"),
                "duration_ms": engine.get("duration_ms"),
                "success": engine.get("success"),
                "error": engine.get("error"),
            }
            for engine in engines
            if isinstance(engine, dict)
        ],
    }
    audits.append(entry)
    if audit_token is not None and scene_index is not None:
        with _SCENE_UNIVERSAL_AUDITS_LOCK:
            bucket = _SCENE_UNIVERSAL_AUDITS.setdefault(str(audit_token), {}).setdefault(
                int(scene_index),
                {"universal_engine_audits": []},
            )
            seen = {
                re.sub(r"\s+", " ", str(item.get("query") or "")).strip().casefold()
                for item in bucket["universal_engine_audits"]
            }
            if query not in seen:
                bucket["universal_engine_audits"].append(dict(entry))


def _scene_web_audit_extras(
    audit_token: Optional[str], scene_index: int
) -> Dict[str, object]:
    """Per-scene universal audit trail recorded under ``audit_token``."""
    if not audit_token:
        return {"universal_engine_audits": []}
    with _SCENE_UNIVERSAL_AUDITS_LOCK:
        bucket = _SCENE_UNIVERSAL_AUDITS.get(str(audit_token), {}).get(int(scene_index))
        if not bucket:
            return {"universal_engine_audits": []}
        return {
            "universal_engine_audits": [
                dict(entry) for entry in bucket.get("universal_engine_audits") or []
            ]
        }


def _merged_scene_universal_audits(
    audit_token: Optional[str], scene_index: int, row: Mapping[str, object]
) -> List[Dict[str, object]]:
    """Merge the token registry trail with the winner row's own trail.

    The registry covers unresolved scenes (no winner dict); the row covers
    direct _resolve_scene callers without a token. Deduped by query so a
    batch recorded through both channels appears once.
    """
    merged: List[Dict[str, object]] = []
    seen: Set[str] = set()

    def _add(entries: object) -> None:
        if not isinstance(entries, (list, tuple)):
            return
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            key = re.sub(
                r"\s+", " ", str(entry.get("query") or "")
            ).strip().casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(dict(entry))

    _add(_scene_web_audit_extras(audit_token, scene_index).get("universal_engine_audits"))
    _add(row.get("universal_engine_audits"))
    return merged


def _bounded_web_seconds(value, default: float, hard_cap: float, floor: float = 1.0) -> float:
    """Parse an operator budget without allowing an unbounded provider wait."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    if not math.isfinite(parsed):
        parsed = float(default)
    return min(float(hard_cap), max(float(floor), parsed))


def _scene_deadline(stage_deadline_at: Optional[float], now: Optional[float] = None) -> Optional[float]:
    """Scene deadline: the enclosing stage wall, nothing else.

    User override (Nov 16): web has NO time limit. The old per-scene
    90s wall-clock cap cut working waves off mid-retrieval, so it is
    removed — a scene's deadline is now just the stage deadline (the
    coordinator's total wall), and None when the stage itself has none.
    A working wave is therefore never cut off by a scene clock: the wave
    loop's liveness watchdog (zero progress for the full
    _web_liveness_window_seconds()) is the only trip for a dead wave,
    flagged web_retrieval_stalled for the driver. An operator may still
    set VIDZEO_WEB_SCENE_DEADLINE_SECONDS to impose an explicit
    per-scene wall; that explicit value is honoured as-is (no hard cap).
    """
    started = time.monotonic() if now is None else float(now)
    deadline: Optional[float] = None
    explicit = os.environ.get("VIDZEO_WEB_SCENE_DEADLINE_SECONDS")
    if explicit is not None and str(explicit).strip():
        try:
            scene_seconds = float(explicit)
        except (TypeError, ValueError):
            scene_seconds = None
        if (
            scene_seconds is not None
            and math.isfinite(scene_seconds)
            and scene_seconds > 0
        ):
            deadline = started + scene_seconds
    if stage_deadline_at is not None:
        try:
            stage_deadline = float(stage_deadline_at)
            if math.isfinite(stage_deadline):
                deadline = (
                    stage_deadline
                    if deadline is None
                    else min(deadline, stage_deadline)
                )
        except (TypeError, ValueError):
            pass
    return deadline


# ── Egress-outage circuit (round 10, job e4d610b9 fix) ───────────────────
# Job e4d610b9 proved the failure mode: the VM's egress went down for
# ~20 minutes and EVERY web provider failed on BOTH routes at once
# (Yandex/Bing/Flickr ReadTimeouts, Wikimedia/Openverse empty), while
# the stage kept hammering them for the whole 1103 s media budget and
# resolved 0/13 scenes. The engines, the queries, and the route logic
# were all healthy — the live round-10 probe gets 12 rows in ~4 s from
# the same code path. The mistake was treating a dead egress like a
# bad query: retrying immediately, burning the budget, and letting the
# media deadline fire with nothing resolved.
#
# WebOutageTracker is the stage-scoped circuit that stops that:
#  - it counts consecutive provider calls that end 0-row ON A TRANSPORT
#    FAILURE (timeout / connection error, never a genuine empty answer);
#  - at the threshold it declares an outage episode, and every scene
#    worker SLEEPS through a growing backoff (60 s, then 120 s) instead
#    of issuing more requests — no hammering while the egress is down;
#  - the backoff does NOT count against the media deadline: each
#    episode extends the stage deadline by the backoff (capped);
#  - at most 3 episodes per stage; past the cap the stage proceeds with
#    its normal per-scene fallbacks instead of looping forever.
# One tracker is created per resolve_web_images_for_video_fast call and
# passed into _resolve_scene; other callers get None (disabled, old
# behaviour). Slower is fine — reliability over speed.


def _web_outage_enabled() -> bool:
    return str(
        os.environ.get("VIDZEO_WEB_OUTAGE_ENABLED", "1") or "1"
    ).strip().lower() in {"1", "true", "yes", "on"}


def _web_outage_fail_threshold() -> int:
    try:
        return max(
            1,
            min(
                20,
                int(
                    os.environ.get("VIDZEO_WEB_OUTAGE_FAIL_THRESHOLD", "3")
                    or 3
                ),
            ),
        )
    except (TypeError, ValueError):
        return 3


def _web_outage_backoff_seconds() -> float:
    try:
        return max(
            1.0,
            min(
                600.0,
                float(
                    os.environ.get("VIDZEO_WEB_OUTAGE_BACKOFF_SECONDS", "60")
                    or 60
                ),
            ),
        )
    except (TypeError, ValueError):
        return 60.0


def _web_outage_backoff_max_seconds() -> float:
    try:
        return max(
            1.0,
            min(
                900.0,
                float(
                    os.environ.get(
                        "VIDZEO_WEB_OUTAGE_BACKOFF_MAX_SECONDS", "120"
                    )
                    or 120
                ),
            ),
        )
    except (TypeError, ValueError):
        return 120.0


def _web_outage_max_episodes() -> int:
    try:
        return max(
            0,
            min(
                10,
                int(
                    os.environ.get("VIDZEO_WEB_OUTAGE_MAX_EPISODES", "3")
                    or 3
                ),
            ),
        )
    except (TypeError, ValueError):
        return 3


def _web_outage_max_extension_seconds() -> float:
    try:
        return max(
            0.0,
            min(
                3600.0,
                float(
                    os.environ.get(
                        "VIDZEO_WEB_OUTAGE_MAX_EXTENSION_SECONDS", "360"
                    )
                    or 360
                ),
            ),
        )
    except (TypeError, ValueError):
        return 360.0


class WebOutageTracker:
    """Stage-scoped egress-outage circuit (round 10).

    Thread-safe: scene workers call :meth:`note_outcome` after every
    provider call and :meth:`wait_if_outage` before issuing one. All
    mutable state lives under one lock; the backoff sleep happens
    outside it so workers do not block each other while waiting.
    """

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        fail_threshold: Optional[int] = None,
        backoff_seconds: Optional[float] = None,
        backoff_max_seconds: Optional[float] = None,
        max_episodes: Optional[int] = None,
        max_extension_seconds: Optional[float] = None,
        sleep: Any = None,
    ) -> None:
        self._enabled = (
            _web_outage_enabled() if enabled is None else bool(enabled)
        )
        self._threshold = (
            _web_outage_fail_threshold()
            if fail_threshold is None
            else max(1, int(fail_threshold))
        )
        self._backoff = (
            _web_outage_backoff_seconds()
            if backoff_seconds is None
            else max(1.0, float(backoff_seconds))
        )
        self._backoff_max = (
            _web_outage_backoff_max_seconds()
            if backoff_max_seconds is None
            else max(1.0, float(backoff_max_seconds))
        )
        self._max_episodes = (
            _web_outage_max_episodes()
            if max_episodes is None
            else max(0, int(max_episodes))
        )
        self._max_extension = (
            _web_outage_max_extension_seconds()
            if max_extension_seconds is None
            else max(0.0, float(max_extension_seconds))
        )
        self._sleep = sleep if sleep is not None else time.sleep
        self._lock = threading.Lock()
        self._streak = 0
        self._download_failure_hosts: set[str] = set()
        self._episodes = 0
        self._extension = 0.0
        self._backoff_until = 0.0
        self._declared_total = 0

    @property
    def enabled(self) -> bool:
        return bool(self._enabled)

    @property
    def episodes_used(self) -> int:
        with self._lock:
            return int(self._episodes)

    @property
    def declared_total(self) -> int:
        """Outage episodes declared so far (audit)."""
        with self._lock:
            return int(self._declared_total)

    def extension_seconds(self) -> float:
        """Seconds added to the stage deadline by backoffs so far."""
        with self._lock:
            return float(self._extension)

    def backoff_remaining(self) -> float:
        """Seconds left in the active backoff window (0 when none)."""
        with self._lock:
            return max(0.0, float(self._backoff_until) - time.monotonic())

    def note_outcome(
        self, *, transport_failed: bool, rows: int, download_hosts: Optional[set[str]] = None,
    ) -> bool:
        """Record one provider call. Returns True if it declared an outage.

        Only a 0-row call that failed on transport counts toward the
        streak. Rows, or a genuine empty answer, prove the egress is
        alive and reset the streak — a bad query must never look like
        a dead network.
        """
        if not self._enabled:
            return False
        now = time.monotonic()
        with self._lock:
            if download_hosts is None:
                if rows > 0 or not transport_failed:
                    self._streak = 0
                    return False
                self._streak += 1
                if self._streak < self._threshold:
                    return False
                evidence = f"{self._threshold} consecutive search transport failures"
            else:
                if rows > 0:
                    self._download_failure_hosts.clear()
                    return False
                if not transport_failed:
                    return False
                self._download_failure_hosts.update(download_hosts)
                if len(self._download_failure_hosts) < 2:
                    return False
                evidence = f"image transfers failed across {len(self._download_failure_hosts)} hosts"
            if self._episodes >= self._max_episodes:
                return False
            if self._extension >= self._max_extension:
                return False
            if now < self._backoff_until:
                # Already backing off; a straggler's failure must not
                # declare a second episode for the same outage.
                return False
            # Declare a new episode: growing backoff, deadline extended
            # by the same amount so the sleep never eats the media wall.
            self._episodes += 1
            self._declared_total += 1
            backoff = min(
                self._backoff * (2 ** (self._episodes - 1)),
                self._backoff_max,
            )
            backoff = min(backoff, self._max_extension - self._extension)
            if backoff <= 0:
                return False
            self._backoff_until = now + backoff
            self._extension += backoff
            self._streak = 0
            self._download_failure_hosts.clear()
            episodes = int(self._episodes)
            extension = float(self._extension)
        _web_log(
            f"[web] EGRESS OUTAGE episode {episodes}/{self._max_episodes}: "
            f"{evidence}, backing off "
            f"{backoff:.0f}s (stage deadline +{extension:.0f}s total)"
        )
        return True

    def wait_if_outage(self, cancel_event: Any = None) -> bool:
        """Sleep through the active backoff window, if any.

        Called before issuing a provider call so no worker hammers a
        dead egress. Sleeps in short steps so a job cancellation ends
        the wait promptly. Returns True when it slept at all.
        """
        if not self._enabled:
            return False
        slept = False
        while True:
            remaining = self.backoff_remaining()
            if remaining <= 0:
                return slept
            if cancel_event is not None and cancel_event.is_set():
                return slept
            step = min(0.5, remaining)
            self._sleep(step)
            slept = True

    def snapshot(self) -> Dict[str, Any]:
        """Audit state: episodes, extension, active backoff."""
        with self._lock:
            return {
                "enabled": bool(self._enabled),
                "episodes_used": int(self._episodes),
                "episodes_declared": int(self._declared_total),
                "extension_seconds": round(float(self._extension), 1),
                "backoff_remaining_seconds": round(
                    max(0.0, float(self._backoff_until) - time.monotonic()), 1
                ),
                "max_episodes": int(self._max_episodes),
                "max_extension_seconds": float(self._max_extension),
            }


try:
    from config import TEMP_DIR
except ImportError:
    from server.config import TEMP_DIR
from .constants import (
    BAD_QUERY_TERMS,
    PERCEPTUAL_HASH_SIZE,
    PERCEPTUAL_HASH_THRESHOLD,
    STOPWORDS,
    SUPPORTED_IMAGE_EXTS,
)
from .runtime import _web_log, resolve_gallery_dl, web_direct_first
from .hybrid_search import bing_rows_are_decoy, download_bulk, search_bing
from .muse_search import (
    muse_search_enabled,
    muse_search_fallback_universal_enabled,
    muse_search_last_wave_transport_failed,
    muse_search_max_wave_wall,
    reset_muse_search_job_budget,
    search_muse_sources,
)
from .flickr_api_source import flickr_last_search_network_failure, search_flickr_api
from .host_allowlist import is_seen_host, load_seen_hosts, record_hosts
from .safety import candidate_is_safe, image_is_safe
from ..visual_requirement import (
    candidate_asset_metadata,
    candidate_domain_context_pending,
    candidate_nearest_match,
    candidate_requirement_match,
    provider_query_variants,
)
from services.sources import OpenverseSource, RedditSource, WikimediaSource, BraveSource, SearXNGSource, LibraryOfCongressSource
from services.sources.google_images_source import GoogleImagesSource
from services.sources.yandex_source import YandexSource
from .source_broker import web_image_source_catalog, web_image_fallback_layers
from .universal_scraper import (
    UniversalScraperManager,
    UniversalSearchResponse,
    _universal_empty_streak_cap,
    universal_result_to_candidate,
)

ImageFile.LOAD_TRUNCATED_IMAGES = True
# PIL exposes this as a process-global switch and the vision client enables it
# for permissive preview decoding. Serialize the short validation window so a
# resolver cannot accidentally inherit that permissive mode for production
# assets.
_PIL_VALIDATE_LOCK = threading.Lock()

# Global instances for free no-auth API sources
_OPENVERSE_SOURCE = OpenverseSource(
    config={"verify_license_pages": True, "license_verification_limit": 3}
)
_WIKIMEDIA_SOURCE = WikimediaSource()
_BRAVE_SOURCE = BraveSource()
_SEARXNG_SOURCE = SearXNGSource()
_REDDIT_SOURCE = RedditSource()
_GOOGLE_IMAGES_SOURCE = GoogleImagesSource()
_YANDEX_SOURCE = YandexSource()
# Library of Congress: public JSON API, no key, rights-documented photos.
# Wired into the defaults Nov 10, 2026 (web-first audit) — it was defined
# in services/sources/loc_source.py but never dispatched.
_LOC_SOURCE = LibraryOfCongressSource()
try:
    _universal_workers = int(os.environ.get("VIDZEO_UNIVERSAL_SCRAPER_WORKERS", "6") or 6)
except (TypeError, ValueError):
    _universal_workers = 6
try:
    # Direct callers use the fast-pass budget. Completion passes specify
    # their own budget when calling the manager.
    _universal_timeout = float(os.environ.get("VIDZEO_UNIVERSAL_SCRAPER_TIMEOUT_SECONDS") or 8)
except (TypeError, ValueError):
    _universal_timeout = 8.0
_UNIVERSAL_SCRAPER = UniversalScraperManager(
    max_workers=_universal_workers,
    engine_timeout_seconds=_universal_timeout,
    log_fn=_web_log,
)

# The manager keeps a compact per-job audit of each engine.  It is diagnostic
# only; candidate acceptance still happens through the resolver's normal
# safety, relevance, download, and vision gates.
_UNIVERSAL_AUDIT_LOCK = threading.Lock()
_UNIVERSAL_LAST_AUDIT: Dict[str, object] = {}

# Global semaphore — increased to 4 to avoid bottleneck across 24+ scenes
_GDL_SEMAPHORE = threading.Semaphore(4)



# =============================================================================
#  PERCEPTUAL HASHING
# =============================================================================

def _compute_average_hash(path: str) -> Optional[int]:
    try:
        img = Image.open(path).convert("L").resize(
            (PERCEPTUAL_HASH_SIZE, PERCEPTUAL_HASH_SIZE), Image.LANCZOS
        )
        pixels = list(img.getdata())
        avg = sum(pixels) / len(pixels)
        bits = 0
        for p in pixels:
            bits = (bits << 1) | (1 if p > avg else 0)
        return bits
    except Exception:
        return None


def _hamming_distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


# =============================================================================
#  IMAGE VALIDATION
# =============================================================================

def _validate_image(fpath: Union[str, Path, None]) -> Optional[Tuple[int, int]]:
    try:
        p = Path(fpath) if isinstance(fpath, (str, Path)) else None
        if not p or not p.is_file():
            return None
        sz = p.stat().st_size
        if sz < 15 * 1024 or sz > 50 * 1024 * 1024:
            return None
        ext = p.suffix.lower()
        if ext not in SUPPORTED_IMAGE_EXTS:
            return None
        # ``Image.open`` only parses the header.  A truncated JPEG/PNG can
        # therefore look valid here and fail later during rendering (or be
        # retained by the visual-verifier degraded path).  Verify the full
        # container before exposing the path to any downstream stage. PIL's
        # truncated-image setting is process-global, so temporarily force
        # strict decoding under a small lock and restore the caller's mode.
        with _PIL_VALIDATE_LOCK:
            previous_truncated_mode = ImageFile.LOAD_TRUNCATED_IMAGES
            ImageFile.LOAD_TRUNCATED_IMAGES = False
            try:
                with Image.open(str(p)) as img:
                    w, h = img.size
                    if str(getattr(img, "format", "") or "").upper() not in {
                        "JPEG", "PNG", "WEBP", "BMP", "TIFF",
                    }:
                        return None
                    img.verify()
                # ``verify()`` checks container markers but JPEG can still
                # pass when its final scan is truncated. Re-open and force
                # decoding of all pixel data as well (PIL cannot call
                # ``verify`` after ``load`` on the same object).
                with Image.open(str(p)) as decoded:
                    decoded.load()
            finally:
                ImageFile.LOAD_TRUNCATED_IMAGES = previous_truncated_mode
        # Accept portrait media as well as landscape.  A 500x736 portrait has
        # enough pixels for vertical editorial use, but the old axis-specific
        # test rejected it because its width was below 640.  Keep an
        # orientation-neutral minimum: one edge must be 640px and neither may
        # be below 360px.
        if min(w, h) < 360 or max(w, h) < 640:
            return None
        return (w, h)
    except Exception:
        return None


# =============================================================================
#  HIERARCHICAL SCORING
# =============================================================================

_SOURCE_PRIORITY = {
    "universal_scraper": 0.42,
    "universal_yandex": 0.40,
    "universal_bing": 0.40,
    "universal_baidu": 0.30,
    "universal_openverse": 0.40,
    "universal_flickr": 0.20,
    "bbc": 0.6,
    "cnn": 0.6,
    "reuters": 0.6,
    "nationalgeographic": 0.6,
    "britannica": 0.6,
    "wikimedia": 0.5,
    "openverse": 0.4,
    "muse_search": 0.45,
    "bing_hybrid": 0.4,
    "yandex": 0.3,
    "flickr_api": 0.1,
    "flickr": 0.1,
    "pinterest": 0.05,
    "imgur": 0.05,
}

# Terms that carry no subject information for relevance gating (image spam,
# captions boilerplate, listicle framing words).
_WEB_GENERIC_STOP = {
    'video', 'image', 'picture', 'photo', 'footage', 'scene', 'clip', 'show',
    'look', 'see', 'like', 'get', 'also', 'new', 'one', 'two', 'use', 'used',
    'using', 'make', 'made', 'may', 'well', 'many', 'really', 'just', 'way',
    'get', 'time', 'thing', 'things', 'big', 'little', 'good', 'bad',
}

# Container/framing words shared across every chapter of a multi-topic script.
# They must never satisfy the scene-relevance gate on their own.
_WEB_CONTAINER_TERMS = {
    'top', 'ten', 'number', 'extreme', 'job', 'jobs', 'world', 'most', 'best',
    'list', 'career', 'push', 'human', 'body', 'mind', 'limit', 'absolute',
    'people', 'great', 'life', 'work', 'worker', 'workers', 'story', 'video',
    'watch', 'sub', 'subscribe', 'welcome', 'channel',
}

_GENERIC_MODIFIER_TERMS = {
    'zone', 'area', 'region', 'giant', 'big', 'small', 'number', 'three', 'two', 'one',
    'four', 'five', 'best', 'most', 'top', 'photo', 'image', 'picture', 'video', 'view',
    'look', 'world', 'earth', 'deep', 'depth', 'trench', 'vent', 'vents', 'sea', 'ocean',
    'surface', 'secret', 'secrets', 'mysterious', 'frontier', 'landscape', 'portrait',
}


def _extract_discriminating_subject_terms(terms_or_subject) -> Set[str]:
    """Extract discriminating entity terms excluding generic modifier and container words."""
    if isinstance(terms_or_subject, str):
        words = set(re.findall(r'[a-z]{3,}', terms_or_subject.lower()))
    elif isinstance(terms_or_subject, (set, list, tuple)):
        words = {str(t).lower().strip() for t in terms_or_subject if len(str(t).strip()) >= 3}
    else:
        words = set()
    discriminating = {
        w for w in words
        if w not in STOPWORDS
        and w not in _WEB_GENERIC_STOP
        and w not in _WEB_CONTAINER_TERMS
        and w not in _GENERIC_MODIFIER_TERMS
    }
    return discriminating or (words - STOPWORDS - _WEB_GENERIC_STOP - _WEB_CONTAINER_TERMS)


# Low-signal page patterns: keyword-bleed spam sites and junk scrapers that
# routinely surface "Deep Sea Animals Names at <blog>"-style matches.
_WEB_JUNK_PATTERNS = (
    re.compile(r"\b(?:image|file|photo)\s+(?:is\s+)?not\s+found\b"),
    re.compile(r"\b(?:image|preview)\s+unavailable\b"),
    re.compile(r"\bno\s+image\s+available\b"),
    re.compile(r"\b(?:404\s+error|access\s+denied|hotlink(?:ing)?\s+(?:denied|blocked))\b"),
    re.compile(r"\bplaceholder(?:[_\s-]+image)?\b"),
    re.compile(r"\bnames?\s+at\b"),
    re.compile(r"\bat\s+[\w\s]{1,24}?\b(?:blog|website|site|journal)\b"),
    re.compile(r"\bblog\b"),
    re.compile(r"\bquora\b"),
    re.compile(r"\breddit\b"),
    re.compile(r"\bprintables?\b"),
    re.compile(r"\bworksheets?\b"),
    re.compile(r"\bcoloring\b"),
    re.compile(r"\btemplates?\b"),
    re.compile(r"\bhomework\b"),
    re.compile(r"\bwikihow\b"),
    re.compile(r"\b(?:band|bands|concert|music\s+tour|album|vocalist|guitarist|live\s+stage|drummer|bassist|singer|muchmusic|canada\s+day|brampton|ontario|pop\s+punk|pop\s+rock|fan\s+party|live\s+show|stage\s+show)\b"),
    re.compile(r"\b(?:bicycle|bicycles|cycling|mountain\s+bike)\b"),
    re.compile(r"\b(?:t-shirt|apparel|merchandise|sneakers|hoodie)\b"),
)


def _web_rare_terms(scene_terms, weak_terms=None):
    meaningful = {t for t in scene_terms if t not in _WEB_GENERIC_STOP}
    rare = meaningful - _WEB_CONTAINER_TERMS
    if weak_terms:
        rare = rare - set(weak_terms)
    return meaningful, rare


def _web_curated_source(fname, source_hint):
    blob = " ".join(str(x or "") for x in (fname, source_hint)).lower()
    return any(
        marker in blob
        for marker in ("flickr", "openverse", "pexels", "pixabay", "unsplash", "wikimedia", "commons")
    )


def _web_stem_terms(terms):
    """Light plural stemming so 'pipelines' matches 'pipeline' for gating."""
    out = set(terms)
    for t in terms:
        if len(t) > 5 and t.endswith("ies"):
            out.add(t[:-3] + "y")
        elif len(t) > 4 and t.endswith("s"):
            out.add(t[:-1])
    return out


def _web_media_matches_scene(scene_terms, all_text_terms, weak_terms=None, curated=False):
    """Relevance gate for a candidate's metadata vs the scene's narration.

    Requires a rare (discriminating) scene term in the metadata when the scene
    has any; a curated source with two meaningful matches may stand in.  A
    single shared keyword like "deep sea" must never admit a blog about fish.
    """
    meaningful, rare = _web_rare_terms(scene_terms, weak_terms)
    if not meaningful:
        return True
    all_text_terms = _web_stem_terms(all_text_terms)
    matched = _web_stem_terms(meaningful) & all_text_terms
    if not matched:
        return False
    if rare and not (_web_stem_terms(rare) & all_text_terms):
        return curated and len(matched) >= 2
    return True


def _web_junk_metadata(title, url, source_url, tags="", description=""):
    haystack = " ".join(str(x or "") for x in (title, url, source_url, tags, description)).lower()
    return any(pat.search(haystack) for pat in _WEB_JUNK_PATTERNS)

def _score_candidate(
    fpath: Path,
    w: int,
    h: int,
    query: str,
    scene_text: str,
    identity_terms: Set[str],
    source_hint: str = "",
    title: str = "",
    url: str = "",
    tags: str = "",
    description: str = "",
    source_url: str = "",
    weak_terms: Optional[Set[str]] = None,
    strict_subject: bool = False,
) -> float:
    strict_subject = bool(
        strict_subject
        or (identity_terms and set(identity_terms) - _WEB_CONTAINER_TERMS)
    )
    quality = min(w, h) / 2000.0
    fname = fpath.name.lower()
    title_lower = str(title or "").lower()
    url_lower = str(url or "").lower()
    source_url_lower = str(source_url or "").lower()
    tags_str = (" ".join(tags) if isinstance(tags, (list, tuple, set)) else str(tags or "")).lower()
    desc_str = (" ".join(description) if isinstance(description, (list, tuple, set)) else str(description or "")).lower()

    # Graded relevance gate. Junk pages, concert bands, merchandise are always hard-rejected.
    if _web_junk_metadata(title, url, source_url, tags_str, desc_str):
        return -50.0

    query_terms = set(re.findall(r'[a-z]{3,}', query.lower())) - STOPWORDS
    scene_terms = set(re.findall(r'[a-z]{3,}', scene_text.lower())) - STOPWORDS
    fname_terms = set(re.findall(r'[a-z]{3,}', fname))

    all_text_terms = (
        fname_terms
        | set(re.findall(r'[a-z]{3,}', title_lower))
        | set(re.findall(r'[a-z]{3,}', f"{url_lower} {source_url_lower}"))
        | set(re.findall(r'[a-z]{3,}', f"{tags_str} {desc_str}"))
    )

    query_match = len(query_terms & all_text_terms)
    scene_match = len(scene_terms & all_text_terms)
    relevance = (query_match * 2.0 + scene_match * 1.0) / max(len(query_terms | scene_terms), 1)

    # Keep the fallback adjustment below deterministic even when the
    # candidate has no meaningful metadata match.  Previously ``strict_ok``
    # was assigned only in the matched branch, so a perfectly normal
    # metadata-poor result raised UnboundLocalError and silently lost a scene.
    strict_ok = True
    if all_text_terms:
        meaningful, rare = _web_rare_terms(scene_terms, weak_terms)
        stemmed_all = _web_stem_terms(all_text_terms)
        matched = _web_stem_terms(meaningful) & stemmed_all
        if not matched:
            cand_meta = {
                "title": title, "url": url, "source": source_hint,
                "tags": tags_str, "description": desc_str, "source_url": source_url,
            }
            cand_class, _ = _metadata_candidate_class(
                cand_meta,
                main_subject=" ".join(identity_terms) if identity_terms else "",
                scene_text=scene_text,
            )
            has_identity = bool(
                identity_terms
                and any(
                    re.search(r"\b" + re.escape(t.lower().rstrip("s")) + r"s?\b", f"{title_lower} {url_lower} {fname}")
                    for t in (_extract_discriminating_subject_terms(identity_terms) or identity_terms)
                )
            )
            if cand_class in ("anchored", "anchored_generic") or has_identity:
                # Authentic topic asset: preserve baseline relevance rather than hard rejection
                # so scenes without a rare narration match still resolve unique topic imagery.
                relevance = max(relevance, 0.40 if cand_class == "anchored" else 0.20)
            else:
                return -2.0
        else:
            strict_ok = (
                not rare
                or bool(_web_stem_terms(rare) & stemmed_all)
                or (_web_curated_source(fpath.name, source_hint) and len(matched) >= 2)
            )
            if not strict_ok and rare:
                if strict_subject:
                    cand_meta = {
                        "title": title, "url": url, "source": source_hint,
                        "tags": tags_str, "description": desc_str, "source_url": source_url,
                    }
                    cand_class, _ = _metadata_candidate_class(
                        cand_meta,
                        main_subject=" ".join(identity_terms) if identity_terms else "",
                        scene_text=scene_text,
                    )
                    has_identity = bool(
                        identity_terms
                        and any(
                            re.search(r"\b" + re.escape(t.lower().rstrip("s")) + r"s?\b", f"{title_lower} {url_lower} {fname}")
                            for t in (_extract_discriminating_subject_terms(identity_terms) or identity_terms)
                        )
                    )
                    if cand_class in ("anchored", "anchored_generic") or has_identity:
                        relevance = max(relevance, 0.25)
                    else:
                        return -2.0
                else:
                    relevance = min(relevance, 0.15)

    # Identity gating: strong penalty when subject doesn't appear in title/url/filename
    identity_penalty = 0.0
    if identity_terms:
        discrim_terms = _extract_discriminating_subject_terms(identity_terms)
        target_terms = discrim_terms if discrim_terms else identity_terms

        has_full = all(
            re.search(r"\b" + re.escape(term.lower().rstrip("s")) + r"s?\b", f"{title_lower} {url_lower} {fname}")
            for term in target_terms
        )
        has_any = any(
            re.search(r"\b" + re.escape(term.lower().rstrip("s")) + r"s?\b", f"{title_lower} {url_lower} {fname}")
            for term in target_terms
        )
        if not has_any:
            identity_penalty = 8.0
        elif not has_full and len(target_terms) > 1:
            identity_penalty = 2.5

    # Semantic noise rejection (e.g. musical bands, concert tours, commercial bikes/merchandise)
    noise_penalty = 0.0
    text_blob = f"{title_lower} {tags_str} {desc_str} {url_lower}"
    for noise_pattern in _WEB_JUNK_PATTERNS:
        if noise_pattern.search(text_blob):
            noise_penalty += 6.0

    # Source priority bonus
    source_bonus = 0.0
    if source_hint:
        for source, bonus in _SOURCE_PRIORITY.items():
            if source in source_hint.lower() or source in fname:
                source_bonus = bonus
                break
    else:
        for source, bonus in _SOURCE_PRIORITY.items():
            if source in fname:
                source_bonus = bonus
                break

    # Watermark rejection: stock photo providers and explicit watermarks are hard-rejected
    watermark_terms = {"alamy", "gettyimages", "shutterstock", "istockphoto", "dreamstime", "depositphotos", "123rf", "watermark", "watermarked"}
    if any(wm in title_lower or wm in url_lower or wm in tags_str or wm in desc_str or wm in fname for wm in watermark_terms):
        return -50.0

    # Dynamic aspect ratio bonus/penalty matching the target project canvas
    target_aspect = 16.0 / 9.0
    try:
        pw = float(os.environ.get("VIDZEO_PROJECT_WIDTH", "1920") or 1920)
        ph = float(os.environ.get("VIDZEO_PROJECT_HEIGHT", "1080") or 1080)
        if pw > 0 and ph > 0:
            target_aspect = pw / ph
    except Exception:
        target_aspect = 16.0 / 9.0

    aspect = w / max(h, 1)
    if target_aspect >= 1.0: # Landscape or square canvas
        aspect_bonus = 0.3 if 1.2 <= aspect <= 2.2 else (-0.4 if aspect < 0.8 else 0.0)
    else: # Vertical 9:16 canvas
        aspect_bonus = 0.3 if 0.45 <= aspect <= 0.85 else (-0.4 if aspect > 1.3 else 0.0)

    # Minimum dimension bonus (prefer larger images)
    size_bonus = 0.0
    if min(w, h) >= 1080:
        size_bonus = 0.5
    elif min(w, h) >= 720:
        size_bonus = 0.3
    elif min(w, h) >= 540:
        size_bonus = 0.1

    result = quality + relevance * 3.0 - identity_penalty - noise_penalty + source_bonus + aspect_bonus + size_bonus
    if not strict_ok and rare:
        # Weak-but-topical fills rank below every strict match so a good
        # candidate always wins, while hard scenes still get a related image.
        result -= 2.5
    return result


def _metadata_has_identity(title: str, url: str, filename: str, identity_terms: Set[str]) -> bool:
    if not identity_terms:
        return True
    metadata = f"{title} {url} {filename}".lower()
    terms = [str(term).lower().strip() for term in identity_terms if str(term).strip()]
    # For a named person, one shared first name is not enough evidence:
    # “John the Beloved” must never satisfy a “John Cena” query. Callers pass
    # the discriminating name parts, so require every part here.
    return all(
        re.search(r"\b" + re.escape(term) + r"\b", metadata)
        for term in terms
    )


# =============================================================================
#  WRONG PERSON / HALLUCINATION FILTER
# =============================================================================

def _is_biographical_person(subject: str) -> bool:
    """Check if the subject is likely a biographical human person, rather than a concept, sport, or event phrase."""
    if not subject:
        return False
    subject_lower = subject.lower().strip()

    if re.search(r"\b(?:royal\s+family|british\s+royal|monarchy|windsor\s+family)\b", subject_lower):
        return False

    # Common non-person / sports / concept keywords
    non_person_keywords = {
        'nfl', 'nba', 'mlb', 'nhl', 'wwe', 'aew', 'ufc', 'mma', 'fifa', 'icc',
        'rms', 'hms', 'uss', 'ss', 'mv', 'ms', 'hmt', 'titanic', 'ship', 'ships',
        'liner', 'vessel', 'shipwreck',
        # Maritime/object anchors are frequently proper names (Bismarck,
        # Vasa, Mary Rose, Edmund Fitzgerald, Kursk).  Treat their explicit
        # vessel context as an object scene so the biographical-person gate
        # does not discard valid ship photographs whose captions omit the
        # full query wording.
        'battleship', 'warship', 'freighter', 'submarine', 'destroyer',
        'cruiser', 'corvette', 'frigate', 'steamer', 'steamship', 'tugboat',
        'yacht', 'ferry', 'cruise', 'cargo', 'carrier', 'wreck', 'wreckage',
        'scuttled', 'capsized', 'torpedoed', 'sinking', 'sank', 'collision',
        'explosion', 'mine', 'harbor', 'harbour', 'solent', 'lake', 'maritime',
        'nautical', 'museum', 'artifact', 'tudor',
        # Scene-role/facet words are object or event context, not proof that
        # the active subject is a biographical person.  This matters for
        # long documentaries where the planner emits anchors such as
        # ``Marconi Wireless Distress Signal SOS`` or ``Titanic Lifeboats``.
        'captain', 'officer', 'operator', 'architect', 'engineer', 'stoker',
        'stokers', 'fireman', 'firemen', 'wireless', 'distress', 'signal',
        'sos', 'flare', 'rocket', 'lifeboat', 'lifeboats', 'rescue',
        'regulation', 'regulations', 'safety', 'discovery', 'explorer',
        'crew', 'passenger', 'passengers', 'immigrant', 'immigrants',
        'survivor', 'survivors', 'musician', 'musicians', 'band', 'room',
        'deck', 'interior', 'staircase', 'stairs', 'iceberg', 'atlantic',
        'knockout', 'knockouts', 'moment', 'moments', 'touchdown', 'touchdowns',
        'tackle', 'tackles', 'interception', 'interceptions', 'sack', 'sacks',
        'super bowl', 'superbowl', 'playoff', 'playoffs', 'championship', 'tournament',
        'finals', 'semifinals', 'match', 'game', 'clash', 'derby', 'trophy',
        'football', 'basketball', 'baseball', 'soccer', 'cricket', 'rugby', 'tennis',
        'golf', 'hockey', 'wrestling', 'boxing', 'fight', 'fights', 'fighter', 'fighters',
        'history', 'documentary', 'revolution', 'battle', 'war', 'invasion', 'offensive',
        'accords', 'conspiracy', 'secrets', 'revelation', 'revelations', 'mysteries',
        'actor', 'actress', 'movie', 'film', 'series', 'episode', 'season', 'show',
        'car', 'cars', 'truck', 'trucks', 'bike', 'bikes', 'airplane', 'airplanes',
        'nature', 'animal', 'animals', 'dog', 'dogs', 'cat', 'cats', 'bird', 'birds',
        'building', 'buildings', 'city', 'cities', 'town', 'towns', 'country', 'countries'
    }
    
    # If any word in the subject is a non-person keyword, it's not a person
    for word in re.findall(r'[a-z]+', subject_lower):
        if word in non_person_keywords:
            return False

    # Typically, a person has 2-3 words (e.g. "Virat Kohli"). If it's more than 3 words, it's likely a concept/phrase.
    words = subject_lower.split()
    if len(words) > 3:
        return False

    return True


def _is_wrong_person(title: str, main_subject: str) -> bool:
    """Reject candidate if title contains a different first name with the same surname,
    or for single-name subjects, if title contains a clearly different person's name."""
    if not title or not main_subject:
        return False
        
    if not _is_biographical_person(main_subject):
        return False
        
    title_lower = title.lower()
    subject_parts = main_subject.lower().split()
    
    if len(subject_parts) >= 2:
        first_name = subject_parts[0]
        surname = subject_parts[-1]
        
        if surname in title_lower and first_name not in title_lower:
            match = re.search(rf'\b([a-z]+)\s+{re.escape(surname)}\b', title_lower)
            if match:
                other_first_name = match.group(1)
                if other_first_name not in STOPWORDS and other_first_name not in subject_parts:
                    return True
    else:
        known_alternatives = {
            "kohli": ["rohit", "dhawan", "rahane", "pujara", "dhoni", "jadeja", "bumrah", "shami", "pandya", "gill", "iyer", "siraj"],
            "rohit": ["kohli", "dhawan", "rahane", "pujara", "dhoni", "jadeja", "bumrah"],
            "dhoni": ["kohli", "rohit", "dhawan", "rahane", "pujara", "jadeja"],
        }
        subject_lower = subject_parts[0].lower()
        if subject_lower in known_alternatives:
            for alt_name in known_alternatives[subject_lower]:
                if alt_name in title_lower and subject_lower not in title_lower:
                    return True
    
    return False


def _web_asset_cache_paths() -> Tuple[Path, Path]:
    cache_dir = TEMP_DIR / "web_image_asset_cache"
    return cache_dir, cache_dir / "assets.json"


def _load_historical_web_assets() -> List[Dict]:
    """Load stable copies of previously verified web assets, if any."""
    _cache_dir, manifest_path = _web_asset_cache_paths()
    try:
        with manifest_path.open("r", encoding="utf-8") as handle:
            rows = json.load(handle)
        return [row for row in rows if isinstance(row, dict) and Path(str(row.get("path") or "")).is_file()]
    except Exception:
        return []


def _persist_web_asset_cache(entries: List[Dict], subjects: List[str], active_subject: str = "") -> None:
    """Persist verified web winners outside disposable scene directories."""
    cache_dir, manifest_path = _web_asset_cache_paths()
    cache_dir.mkdir(parents=True, exist_ok=True)
    existing = _load_historical_web_assets()
    by_key = {
        str(row.get("source_url") or row.get("path") or "").casefold(): row
        for row in existing
    }
    subject = str(active_subject or (subjects[0] if subjects else "") or "").strip()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        source_path = Path(str(entry.get("path") or ""))
        if not source_path.is_file():
            continue
        key = str(entry.get("source_url") or source_path or "").casefold()
        digest = hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:20]
        target = cache_dir / f"{digest}{source_path.suffix.lower() or '.jpg'}"
        try:
            if source_path.resolve() != target.resolve():
                shutil.copy2(source_path, target)
        except Exception:
            continue
        row = dict(entry)
        row["path"] = str(target)
        row["active_subject"] = subject
        row["subjects"] = list(subjects or [])
        by_key[key] = row
    try:
        manifest_path.write_text(json.dumps(list(by_key.values()), indent=2), encoding="utf-8")
    except Exception:
        pass

_RAW_OVERRIDE_QUERY_FIELDS = (
    "specific_query", "medium_query", "general_query", "search_query",
    "primary_query", "supporting_search_query", "alt_search_query",
    "primary", "supporting", "fallback",
)


def _override_raw_queries(row) -> List[str]:
    """Raw (pre-repair) query strings from one planner override row.

    The live resolver repairs/regenerates override queries before search
    (subject prepends, forbidden-subject strips, sanitize pass), so the
    repaired strings generally differ from the bundle's ``query_variants``
    that advance prep recorded cache keys under.  The prep-hit lookup must
    also try these raw variants, or a prepped entry is unreachable at a
    production stage deadline.  A hit still flows through the live visual
    pass, so a raw-variant key can only return an asset verified for this
    exact (title, narration, query) triple.
    """
    if not isinstance(row, Mapping):
        return []
    raw: List[str] = []
    for key in ("query_variants", "web_query_variants"):
        values = row.get(key)
        if isinstance(values, (list, tuple)):
            for value in values:
                if isinstance(value, str) and value.strip() and value not in raw:
                    raw.append(value)
    for key in _RAW_OVERRIDE_QUERY_FIELDS:
        value = row.get(key)
        if isinstance(value, str) and value.strip() and value not in raw:
            raw.append(value)
    return raw


def _prep_cache_hits_for_scenes(
    title: str,
    scene_texts: List[str],
    queries_per_scene: List[List[str]],
    output_dir: Path,
    raw_queries_per_scene: Optional[List[List[str]]] = None,
) -> Dict[int, Dict[str, Any]]:
    """Advance-prep cache hits, keyed by (title, scene narration, query).

    For each scene, try the scene's own (possibly repaired) live queries in
    order against the historical asset cache rows stamped with a prep_key by
    the prep pipeline (services/advance_prep.py), then the scene's raw
    planner variants (``raw_queries_per_scene``) — prep records keys under
    those, and the repair pass would otherwise make them unreachable.  A hit copies the verified
    image into this run's scene directory and returns a result row shaped
    like a normal resolved row, so the scene skips provider search entirely
    but still flows through the live visual pass below.  A corrupt cached
    file fails closed (no hit) and the scene searches live.
    """
    hits: Dict[int, Dict[str, Any]] = {}
    try:
        from services.advance_prep import find_prepped_web_asset
    except Exception as exc:
        _web_log(f"prep cache unavailable: {type(exc).__name__}: {exc}")
        return hits
    for idx, scene_text in enumerate(scene_texts or []):
        queries = list((queries_per_scene or [])[idx] if idx < len(queries_per_scene or []) else [])
        raw = list((raw_queries_per_scene or [])[idx] if idx < len(raw_queries_per_scene or []) else [])
        lookup_queries = list(dict.fromkeys([*queries, *raw]))
        if not lookup_queries:
            continue
        try:
            cached = find_prepped_web_asset(title, scene_text, lookup_queries)
        except Exception:
            continue
        if not cached:
            continue
        source_path = Path(str(cached.get("path") or ""))
        if not source_path.is_file():
            continue
        destination = Path(output_dir) / f"scene_{idx + 1}" / source_path.name
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)
            dimensions = _validate_image(destination)
            if dimensions is None:
                destination.unlink(missing_ok=True)
                continue
            width, height = dimensions
        except Exception:
            continue
        row = dict(cached)
        row.update({
            "path": str(destination),
            "width": width,
            "height": height,
            "url": str(cached.get("source_url") or cached.get("url") or ""),
            "prep_cache_hit": True,
            "reused_from_prep": True,
            "retrieval_status": "prep_cache_hit",
        })
        hits[idx] = row
        _web_log(
            f"scene {idx + 1} prep cache hit: reused verified web image "
            f"({cached.get('prep_query') or queries[0]})"
        )
        try:
            from services.advance_prep import note_reuse_by_title

            note_reuse_by_title(title, "web_cache_hit", {
                "scene": idx + 1,
                "query": str(cached.get("prep_query") or queries[0]),
                "path": str(destination),
            })
        except Exception:
            pass
    return hits


# =============================================================================
#  GALLERY-DL MULTI-SOURCE SEARCH
# =============================================================================

_GALLERYDL_SOURCES = [
    ("flickr", "https://flickr.com/search/?text="),
    ("pinterest", "https://www.pinterest.com/search/pins/?q="),
    ("500px", "https://500px.com/search?q={query}&type=photos"),
    ("reddit", "https://www.reddit.com/r/all/search/?q={query}"),
]

_GALLERYDL_TIMEOUTS = {
    "flickr": 45,
    "pinterest": 30,
    "500px": 25,
    "reddit": 35,
}

# Segmented sources for fast search path and slow fallback path.
# Spec (Vidzeo Local) source order, verbatim: Brave primary (no key,
# so it stays skipped via _enabled_fast_sources), Yandex, Google
# Images, Wikimedia/Openverse, UniversalScraper — then the remaining
# local lanes in their previous relative order. The universal
# scraper's internal engine order (Yandex/Bing/Flickr) is unchanged.
_FAST_SOURCES = [
    ("muse_search", "muse"),      # Muse-search API wave (Openverse + Wikimedia) — first lane
    ("brave_images", "api"),       # Brave documented Image Search API — spec primary, skipped without a key
    ("yandex", "api"),             # Yandex Images HTML scraper — priority lane
    ("google_images", "api"),      # Google Images HTML scraper (fail-soft, JS-gated)
    ("wikimedia", "api"),
    ("openverse", "api"),          # Openverse CC image API
    ("universal_scraper", "universal"), # three-engine parallel scraper adapter
    ("primary_archive", "hybrid"), # Official publishers enter the same identity/vision gates.
    ("bing_hybrid", "hybrid"),     # Bing image search
    ("flickr_api", "flickr_api"),  # Flickr REST API — fast, reliable, no scraping
    ("library_of_congress", "api"),  # LOC public JSON API — no key, rights-documented
    ("searxng_images", "api"),     # Free SearXNG JSON image aggregation
    ("getty_bing", "hybrid"),      # Getty via Bing site filter
]

_SLOW_SOURCES = [
    ("pinterest", "gdl"),          # gallery-dl Pinterest (slow, fallback)
    ("reddit", "api"),             # Registered for diagnostics; disabled by default below
    ("reddit", "gdl"),             # Registered for diagnostics; disabled by default below
    ("flickr", "gdl"),             # gallery-dl Flickr (slow, fallback)
    # 500px is listed in _GALLERYDL_SOURCES but deliberately NOT
    # dispatched here: gallery-dl's 500px search needs a logged-in
    # 'x-500px-access-token-prod' cookie and returns no URLs without it
    # (verified live Nov 10, 2026 — 40s, zero URLs). Wiring it in would
    # burn up to 10s of the slow-harvest budget per failed query for
    # nothing. Revisit only if a 500px login is ever supplied.
]

_ALL_SOURCES = _FAST_SOURCES + _SLOW_SOURCES

# First-wave tier: the first query wave fans out to muse-search first
# (Openverse + Wikimedia JSON APIs, 60 s wave wall), then Yandex,
# Google Images, UniversalScraper. The other lanes join later waves.
# Muse-search is the first lane of the first wave, not a later-wave
# lane: today wikimedia/openverse only join on later query variants
# (q_idx > 0) and are routinely starved by the universal lane. The
# standalone openverse/wikimedia lanes are removed from the default
# wave (see _enabled_fast_sources) so the same APIs are not called
# twice. Brave is skipped without a key, as before.
_TIER_ONE_SOURCES = ("muse_search", "yandex", "google_images", "universal_scraper")

# Lanes a per-scene correction wave may still use once the shared topic
# pool is warm (round 4, test 31 fix). muse_search belongs here: scene
# queries differ from the canonical topic query, so the API wave must
# run per distinct scene query — bounded by the manager's per-query
# cache (one wave per distinct query per job) and the scene query
# deadline, not by re-running a 60 s wave per scene. Keeping the set
# small avoids the request stampede this gate was built to stop.
_WARM_POOL_PER_SCENE_SOURCES = (
    "muse_search", "universal_scraper", "yandex", "flickr_api",
)

# Transport families sharing one host + proxy: at most one lane per family
# per query wave. primary_archive/getty_bing are search_bing re-skins,
# so running them beside the plain lane doubles Bing load for no new
# coverage. (DuckDuckGo was removed Nov 19, 2026 — bot-gates this egress —
# so the DDG family is gone with it.)
_BING_FAMILY_SOURCES = ("bing_hybrid", "primary_archive", "getty_bing")


def _dedupe_transport_families(
    source_rows: List[Tuple[str, str]],
) -> List[Tuple[str, str]]:
    """Keep at most one Bing-family lane per wave.

    The plain hybrid lane wins when several family members are listed;
    otherwise the first listed member wins. Non-family lanes pass through
    untouched and in order.
    """
    rows = list(source_rows or [])
    names = {name for name, _kind in rows}
    keep: Set[str] = set()
    for family, preferred in (
        (_BING_FAMILY_SOURCES, "bing_hybrid"),
    ):
        members = [name for name in family if name in names]
        if members:
            keep.add(preferred if preferred in members else members[0])
    out: List[Tuple[str, str]] = []
    for name, kind in rows:
        if name in _BING_FAMILY_SOURCES and name not in keep:
            continue
        out.append((name, kind))
    return out


from .constants import GENERIC_TOPIC_WORDS as _GENERIC_TOPIC_WORDS

# Thread-safe global query search cache to avoid duplicate requests across scenes
_SEARCH_QUERY_CACHE = {}
_CACHE_LOCK = threading.Lock()

# Active per-job provider circuit breaker.  Set by
# ``resolve_web_images_for_video_fast`` while a job runs; the ``_discover_*``
# helpers report failures through it so a dead provider is skipped instead of
# burning every scene's retry budget.  Falls back to no-op when unset.
_ACTIVE_PROVIDER_HEALTH: Optional["SourceHealth"] = None

_PROVIDER_HOSTS = {
    # Universal fan-out owns its own per-engine timeouts and must not be
    # blocked by one serial DNS probe here.
    "universal_scraper": "",
    "wikimedia": "commons.wikimedia.org",
    "library_of_congress": "www.loc.gov",
    "flickr_api": "www.flickr.com",
    "bing_hybrid": "www.bing.com",
    "openverse": "api.openverse.org",
    "yandex": "yandex.com",
    "google_images": "www.google.com",
    "brave_images": "api.search.brave.com",
    "searxng_images": "searx.tiekoetter.com",
    "getty_bing": "www.bing.com",
}


def _report_provider_failure(source_name: str, exc: BaseException) -> None:
    health = _ACTIVE_PROVIDER_HEALTH
    if health is not None:
        reason = f"{type(exc).__name__}: {str(exc)[:100]}"
        text = str(exc).lower()
        status = re.search(r"\b(?:HTTP(?: status)?\s*|status\s*)(\d{3})\b", str(exc), re.I)
        status_code = int(status.group(1)) if status else 0
        is_definitive = (
            status_code in {401, 403, 410, 451}
            or "flickr 100" in text
            or any(marker in text for marker in (
                "bot-gate", "bot check", "bot challenge", "robot check",
                "smartcaptcha", "captcha",
                "invalid api key", "key not found", "unauthorized", "forbidden"
            ))
        )
        if is_definitive:
            health.trip(source_name, reason)
        else:
            health.note_failure(source_name, reason)


def _report_provider_success(source_name: str) -> None:
    health = _ACTIVE_PROVIDER_HEALTH
    if health is not None:
        health.note_success(source_name)


def _web_verbose() -> bool:
    return str(os.environ.get("VIDZEO_WEB_VERBOSE", "0")).strip().lower() in {"1", "true", "yes", "on"}


def _semantic_pending_wave_limit() -> int:
    """Bound metadata-pending query waves before the shared vision gate."""
    try:
        return max(
            1,
            min(
                3,
                # A provisional first hit may fail pixel verification. Keep
                # one independent canonical/action wave before handing off.
                int(os.environ.get("VIDZEO_WEB_SEMANTIC_PENDING_WAVES", "2") or 2),
            ),
        )
    except (TypeError, ValueError):
        return 1


def _enabled_fast_sources() -> List[Tuple[str, str]]:
    """Return public fast lanes plus configured credentialed providers.

    A broad default source mix improves scene coverage. Per-job provider
    circuits retire failing services without disabling healthy alternatives.
    """
    # Operator-scoped allowlist used for controlled source benchmarks and
    # single-provider production jobs.  Keep it here so the shared warm pool,
    # per-scene waves, telemetry, and recovery all obey the same boundary.
    # The universal scraper is the web-image path by deliberate, permanent
    # operator instruction: it runs for every web-image query and no
    # allowlist may drop it. The allowlist restricts the OTHER lanes only.
    only = str(os.environ.get("VIDZEO_WEB_ONLY_PROVIDER", "") or "").strip().lower()
    if only:
        wanted = {item.strip() for item in only.split(",") if item.strip()}
        wanted.add("universal_scraper")
        return [(name, kind) for name, kind in _FAST_SOURCES if name in wanted]
    # Public providers run by default. Their requests share the bounded scene
    # pool; definitive 4xx responses open a circuit so blocked services do not
    # consume every scene's retrieval budget.
    # The standalone openverse/wikimedia lanes are NOT in the default wave:
    # the muse-search wave already calls those same APIs (folded in, Nov 22),
    # and dispatching them again would double-call every query. They stay
    # dispatchable via VIDZEO_WEB_ONLY_PROVIDER for diagnostics.
    default_names = {
        name for name, _kind in _FAST_SOURCES
        if name not in {"brave_images", "searxng_images", "openverse", "wikimedia"}
    }
    if _BRAVE_SOURCE.is_available():
        default_names.add("brave_images")
    searx_enabled = str(os.environ.get("VIDZEO_SEARXNG_ENABLE", "0")).strip().lower() in {"1", "true", "yes", "on"}
    # Do not probe a SearX instance unless the operator explicitly enabled or
    # configured one.  ``is_available()`` performs network I/O; evaluating it
    # first made every resolver call pay a multi-second TLS/DNS penalty,
    # including short-deadline jobs and the final audit serialization.
    searx_configured = bool(
        searx_enabled
        or os.environ.get("VIDZEO_SEARXNG_URLS")
        or os.environ.get("VIDZEO_SEARXNG_URL")
    )
    if searx_configured:
        default_names.add("searxng_images")
    return [
        (name, kind)
        for name, kind in _FAST_SOURCES
        if name in default_names
    ]


def _provider_download_headers(items: List[Dict]) -> Dict[str, Dict[str, str]]:
    """Build per-URL headers while retaining publisher provenance.

    Original publisher URLs should use the publisher page as their referrer;
    provider-hosted previews use the provider search page.  Returning a map
    keeps the helper compatible with callers that only need one URL.
    """
    headers: Dict[str, Dict[str, str]] = {}
    for item in items or []:
        provider = str(item.get("provider") or item.get("source") or "").lower()
        source_url = str(item.get("source_url") or item.get("page_url") or "").strip()
        original = str(item.get("url") or item.get("image_url") or "").strip()
        preview = str(
            item.get("preview_url")
            or item.get("thumbnail_url")
            or ""
        ).strip()
        if original:
            headers[original] = {
                "Referer": source_url or _provider_search_url(provider),
            }
        if preview:
            headers[preview] = {
                "Referer": _provider_search_url(provider),
            }
    return headers


def _provider_search_url(provider: str) -> str:
    provider = str(provider or "").lower()
    if "yandex" in provider:
        return "https://yandex.com/images/"
    if "bing" in provider:
        return "https://www.bing.com/images/"
    if "flickr" in provider:
        return "https://www.flickr.com/"
    if "google" in provider:
        return "https://www.google.com/imghp"
    return ""


def _discover_flickr_api_urls(query: str, direct: bool = False) -> List[Dict]:
    """Discover candidate URLs from Flickr REST API (fast, no subprocess)."""
    if re.search(
        r"\b(?:gta\s*(?:v|vi|5|6)|grand\s+theft\s+auto|gameplay|video\s+game|gaming|playstation|xbox|nintendo)\b",
        str(query or ""),
        re.IGNORECASE,
    ):
        # Flickr's gaming results are mostly conventions, people, cosplay and
        # unrelated real-world photography—not usable in-game visuals.
        return []
    try:
        results = search_flickr_api(query, limit=20, direct=direct)
        failed, error = flickr_last_search_network_failure()
        if failed:
            _report_provider_failure(
                "flickr_api", ConnectionError(f"network failure: {error}")
            )
        candidates = []
        for r in results:
            url = r.get("image_url", "")
            if url and url.startswith(("http://", "https://")):
                candidates.append({
                    "url": url,
                    "image_url": url,
                    "title": r.get("title", ""),
                    "source": "flickr_api",
                    "provider": r.get("provider") or "flickr",
                    "domain": r.get("domain") or "flickr.com",
                    "query": query,
                    "tags": r.get("tags", ""),
                    "description": r.get("description", ""),
                    "source_url": r.get("source_url") or r.get("page_url") or "",
                    "page_url": r.get("page_url") or r.get("source_url") or "",
                    "preview_url": r.get("preview_url") or r.get("thumbnail_url") or "",
                    "thumbnail_url": r.get("thumbnail_url") or r.get("preview_url") or "",
                })
        if candidates:
            _report_provider_success("flickr_api")
        return candidates
    except Exception as exc:
        _report_provider_failure("flickr_api", exc)
        _web_log(f"flickr-api discovery error for '{query[:30]}': {type(exc).__name__}")
        return []


def _discover_gallery_urls(source: str, query: str, gdl: List[str], max_timeout: Optional[int] = None) -> List[Dict]:
    """Discover candidate URLs from a gallery-dl source."""
    if source == "flickr" and re.search(
        r"\b(?:gta\s*(?:v|vi|5|6)|grand\s+theft\s+auto|gameplay|video\s+game|gaming|playstation|xbox|nintendo)\b",
        str(query or ""),
        re.IGNORECASE,
    ):
        return []
    base_url = next((bu for s, bu in _GALLERYDL_SOURCES if s == source), "")
    if not base_url:
        return []
    # Handle sources that use {query} placeholder
    if "{query}" in base_url:
        url = base_url.replace("{query}", urllib.parse.quote(query))
    else:
        url = f"{base_url}{urllib.parse.quote(query)}"
    timeout = _GALLERYDL_TIMEOUTS.get(source, 25)
    if max_timeout is not None:
        timeout = min(timeout, max_timeout)
    
    try:
        with _GDL_SEMAPHORE:
            try:
                requested_range = int(os.environ.get("VIDZEO_WEB_GDL_RANGE", "20") or 20)
            except (TypeError, ValueError):
                requested_range = 20
            requested_range = max(3, min(30, requested_range))
            cmd = gdl + [
                "--config", str(Path(__file__).parents[3] / "gallery-dl.conf"),
                "--get-urls",
                "--range", f"1-{requested_range}",
                "--http-timeout", "15",
                url,
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        
        urls = []
        if result.returncode == 0 and result.stdout:
            for line in result.stdout.splitlines():
                u = line.strip()
                if u and u.startswith(("http://", "https://")):
                    if not any(u.lower().endswith(vext) for vext in (".mp4", ".webm", ".ogv", ".gif", ".mov")):
                        urls.append({
                            "url": u,
                            "title": "",  # gallery-dl --get-urls doesn't return titles
                            "source": source,
                            "query": query,
                            # URL discovery increases recall but is not
                            # identity evidence. These rows remain pending
                            # until the downloaded pixels pass visual review.
                            "discovery_method": "gallery-dl",
                            "metadata_pending": True,
                        })
        return urls
    except subprocess.TimeoutExpired:
        _web_log(f"{source}-gdl '{query[:30]}': timeout ({timeout}s)")
    except Exception as e:
        _web_log(f"{source}-gdl '{query[:30]}': error {type(e).__name__}")
    return []


def _call_with_direct(fn, *args, direct: bool = False, **kwargs):
    """Call ``fn`` with ``direct=``, tolerating doubles that predate it.

    Provider adapters and test doubles written before the Nov 18
    web-resilience build take no ``direct`` keyword; a TypeError naming
    it retries the call without the keyword instead of failing the lane.
    """
    try:
        return fn(*args, direct=direct, **kwargs)
    except TypeError as exc:
        if "direct" not in str(exc):
            raise
        return fn(*args, **kwargs)


def _discover_hybrid_urls(source_name: str, query: str, direct: bool = False) -> List[Dict]:
    """Discover candidate URLs from hybrid robust search (Bing)."""
    try:
        if source_name == "bing_hybrid":
            candidates = search_bing(query, direct=direct)
        elif source_name == "primary_archive":
            from .primary_archives import archive_search_query
            candidates = search_bing(archive_search_query(query), limit=20, direct=direct)
        elif source_name == "getty_bing":
            candidates = search_bing(f"{query} site:gettyimages.com", direct=direct)
        else:
            return []

        # Decoy check (muse-search design §1.4): Bing serves a
        # trending/default page when the query is blocked, and
        # search_bing already raises on it — but a row set that reaches
        # here with no query token is the same failure, so discard it
        # and record a provider failure instead of banking junk.
        if candidates and bing_rows_are_decoy(candidates, query):
            _report_provider_failure(
                source_name,
                RuntimeError("bing decoy results: no row contains a query token"),
            )
            _web_log(
                f"{source_name} '{query[:30]}': decoy results discarded "
                "(no query token in rows)"
            )
            return []
        
        results = []
        for c in candidates[:20]:  # Increased from 15 to 20
            url = c.get("url") or c.get("image_url") or c.get("image") or ""
            if url:
                candidate = {
                    "url": url,
                    "image_url": url,
                    "title": c.get("title", ""),
                    "source": source_name,
                    "provider": c.get("provider") or source_name,
                    "query": query,
                    "tags": c.get("tags", "") or "",
                    "description": c.get("description", "") or "",
                    # Keep publisher and provider URLs separate.  The
                    # original image is used for download, while the preview
                    # and page URLs remain available for attribution, safety,
                    # and the correct download referrer.
                    "source_url": c.get("source_url") or c.get("page_url") or "",
                    "page_url": c.get("page_url") or c.get("source_url") or "",
                    "preview_url": c.get("preview_url") or c.get("thumbnail_url") or "",
                    "thumbnail_url": c.get("thumbnail_url") or c.get("preview_url") or "",
                    "domain": c.get("domain") or "",
                }
                safe, _reason = candidate_is_safe(candidate)
                if safe:
                    results.append(candidate)
        if results:
            _report_provider_success(source_name)
        return results
    except Exception as e:
        _report_provider_failure(source_name, e)
        _web_log(f"{source_name} '{query[:30]}': error {type(e).__name__}")
        return []

def _discover_api_urls(source_name: str, query: str, direct: bool = False) -> List[Dict]:
    """Discover candidate URLs from a free API source.

    ``direct=True`` re-runs the lane bypassing the egress proxy; the
    resolver's route-switching wrapper (in _fetch_source_query) uses it
    for one bounded retry when the lane errored instead of answering.
    """
    try:
        if source_name == "openverse":
            candidates = _call_with_direct(_OPENVERSE_SOURCE.search, query, limit=20, direct=direct)
            if not candidates and getattr(_OPENVERSE_SOURCE, "_circuit_open", False):
                health = _ACTIVE_PROVIDER_HEALTH
                if health is not None:
                    health.trip("openverse", "Openverse circuit open (504 or unauthenticated)")
        elif source_name == "wikimedia":
            candidates = _call_with_direct(_WIKIMEDIA_SOURCE.search, query, limit=20, direct=direct)
            candidates = _call_with_direct(_WIKIMEDIA_SOURCE.enrich_rights, candidates, limit=6, direct=direct)
        elif source_name == "library_of_congress":
            candidates = _call_with_direct(_LOC_SOURCE.search, query, limit=20, direct=direct)
        elif source_name == "yandex":
            candidates = _call_with_direct(_YANDEX_SOURCE.search, query, limit=20, direct=direct)
            # Yandex signals a robot/challenge page by opening its own
            # circuit and returning no rows. Propagate that definitive state
            # into the resolver's per-job health breaker so subsequent scene
            # waves skip Yandex instead of repeating the challenge dozens of
            # times. Other providers remain eligible for the same query.
            if not candidates and getattr(_YANDEX_SOURCE, "_circuit_open", False):
                health = _ACTIVE_PROVIDER_HEALTH
                if health is not None:
                    health.trip("yandex", "robot check or Yandex circuit open")
        elif source_name == "google_images":
            candidates = _call_with_direct(_GOOGLE_IMAGES_SOURCE.search, query, limit=12, direct=direct)
        elif source_name == "brave_images":
            candidates = _call_with_direct(_BRAVE_SOURCE.search, query, limit=20, direct=direct)
        elif source_name == "searxng_images":
            candidates = _call_with_direct(_SEARXNG_SOURCE.search, query, limit=20, direct=direct)
        elif source_name == "reddit":
            candidates = _call_with_direct(_REDDIT_SOURCE.search, query, limit=20, direct=direct)
            return [{
                "url": c.download_url or getattr(c, 'url', '') or "",
                "title": c.title or "",
                "source": "reddit",
                "query": query,
                "tags": "",
            } for c in candidates if (c.download_url or getattr(c, 'url', ''))]
        else:
            return []
        
        rows = []
        for c in candidates:
            if not c.download_url:
                continue
            metadata = getattr(c, "metadata", {}) or {}
            rows.append({
                "url": c.download_url,
                "image_url": c.download_url,
                "title": c.title,
                "source": source_name,
                "provider": metadata.get("provider") or source_name,
                "query": query,
                "tags": getattr(c, "categories", "") or "",
                "description": c.description or "",
                "source_url": metadata.get("foreign_url") or "",
                "preview_url": c.preview_url or "",
                "thumbnail_url": c.preview_url or "",
                "domain": source_name,
                "license": getattr(c, "license", "") or "",
                "license_url": metadata.get("license_url") or "",
                "attribution": metadata.get("attribution") or metadata.get("artist") or "",
                "creator": metadata.get("creator") or metadata.get("artist") or "",
                "width": int(getattr(c, "width", 0) or 0),
                "height": int(getattr(c, "height", 0) or 0),
                # Full-res original for provenance / download fallback
                # (muse-search §3.4); the download URL itself stays the
                # CDN thumbnail for wikimedia rows.
                "original_url": metadata.get("original_url") or "",
                # Yandex often omits card captions entirely (or emits the
                # SERP shell title). Mark that row so retrieval intent cannot
                # become identity evidence downstream.
                "title_is_query": bool(
                    metadata.get("title_is_query")
                    or not str(c.title or "").strip()
                    or re.sub(r"\s+", " ", str(c.title or "")).strip().casefold()
                    == re.sub(r"\s+", " ", str(query or "")).strip().casefold()
                ),
            })
        if rows:
            _report_provider_success(source_name)
        return rows
    except Exception as e:
        _report_provider_failure(source_name, e)
        _web_log(f"{source_name}-api '{query[:30]}': error {type(e).__name__}")
        return []





def _universal_retry_max_attempts() -> int:
    """Total universal-search attempts per query (Nov 18 retry).

    Default 2: attempt 1 via the egress proxy, attempt 2 direct (proxy
    bypass). Clamped to 1–3 so a wedged proxy cannot turn a query into an
    unbounded loop; the delivery budget and the per-attempt engine timeout
    bound the rest.
    """
    try:
        return max(1, min(3, int(os.environ.get("VIDZEO_WEB_UNIVERSAL_RETRY_ATTEMPTS", "2") or 2)))
    except (TypeError, ValueError):
        return 2


def _universal_retry_backoff_seconds() -> float:
    """Pause between universal-search attempts (Nov 18 retry)."""
    try:
        return max(0.0, min(30.0, float(os.environ.get("VIDZEO_WEB_UNIVERSAL_RETRY_BACKOFF_SECONDS", "3") or 3)))
    except (TypeError, ValueError):
        return 3.0


def _universal_rescue_max_attempts() -> int:
    """Extra rescue attempts after the main universal attempts (Nov 18).

    Default 3, clamped 0–6. 0 disables the rescue pass. The pass only
    runs when every engine failed transiently — never on a genuine
    no-results — and is bounded by the deadline below as well.
    """
    try:
        return max(0, min(6, int(os.environ.get("VIDZEO_WEB_UNIVERSAL_RESCUE_ATTEMPTS", "3") or 3)))
    except (TypeError, ValueError):
        return 3


def _universal_rescue_deadline_seconds() -> float:
    """Wall-clock cap for the whole rescue pass (Nov 18, web-resilience).

    Default 600s (10 min per scene batch, per the build brief), clamped
    0–1800s. 0 disables the rescue pass. The delivery budget and the
    liveness watchdog remain the outer bounds above this.
    """
    try:
        return max(0.0, min(1800.0, float(os.environ.get("VIDZEO_WEB_UNIVERSAL_RESCUE_DEADLINE_SECONDS", "600") or 600)))
    except (TypeError, ValueError):
        return 600.0


def _universal_rescue_backoff_seconds(attempt: int) -> float:
    """Backoff before rescue attempt N (1-based): exp growth + jitter.

    Base 5s (env VIDZEO_WEB_UNIVERSAL_RESCUE_BACKOFF_SECONDS), doubling
    per attempt, capped at 60s, ±25% jitter so concurrent scene workers
    do not re-hit the engines in lockstep. Anti-throttle discipline is
    explicit: never hammer the engines, stagger the retries.
    """
    try:
        base = max(0.0, min(30.0, float(os.environ.get("VIDZEO_WEB_UNIVERSAL_RESCUE_BACKOFF_SECONDS", "5") or 5)))
    except (TypeError, ValueError):
        base = 5.0
    delay = min(60.0, base * (2 ** max(0, attempt - 1)))
    return max(0.0, delay * random.uniform(0.75, 1.25))


def _api_lane_retry_backoff_seconds() -> float:
    """Jittered backoff before one direct retry of a failed api/hybrid lane.

    Base 2s (env VIDZEO_WEB_API_LANE_RETRY_BACKOFF_SECONDS), capped 10s,
    ±25% jitter. One retry only; genuine empty answers are never retried.
    """
    try:
        base = max(0.0, min(10.0, float(os.environ.get("VIDZEO_WEB_API_LANE_RETRY_BACKOFF_SECONDS", "2") or 2)))
    except (TypeError, ValueError):
        base = 2.0
    return max(0.0, base * random.uniform(0.75, 1.25))


def _universal_result_is_transient_failure(response: object) -> bool:
    """True when every engine errored, so a retry may succeed.

    A genuine no-results is engines answering (success) with zero rows —
    that is never retried. A transient failure is all engines failing
    (timeout, connection error, bot-gate): the query never got an answer,
    so retrying the same query (direct, on the second attempt) is the
    correct recovery instead of failing the scene.
    """
    stats = list(getattr(response, "stats", []) or [])
    if not stats:
        return False
    return all(not getattr(stat, "success", False) for stat in stats)


def _discover_universal_urls(
    query: str,
    wait_until_complete: bool = False,
    bounded: bool = False,
    scene_index: Optional[int] = None,
) -> List[Dict]:
    """Run the copied three-engine scraper and map rows into resolver records.

    ``bounded=True`` is the muse-search fallback mode (design §1.3):
    exactly ONE attempt, no rescue pass. It runs only after the
    muse-search wave returned 0 rows for the query — repeating the
    5-attempt × ~335 s loop after a failed API wave burns the stage
    wall twice. The default (unbounded) path keeps the 2-attempt retry
    + rescue pass for the exhaustive path and existing callers.

    Bounded retry (Nov 18, Moon 20s test): when every engine fails
    transiently (timeout / connection error / bot-gate) and the batch comes
    back with zero rows, the scene used to die with 0 candidates even
    though a retry would succeed. The same query is retried once (2
    attempts total): attempt 1 via the egress proxy, attempt 2 direct
    (proxy bypass — the proxy is runtime-managed and cannot be removed
    system-wide, so the bypass is per-request). A genuine no-results
    (at least one engine answered) is never retried. Bounded: at most 2
    attempts, a short backoff between them, and each attempt keeps its own
    engine timeout, so a dead proxy cannot hang a job; the delivery
    budget and the liveness watchdog remain the outer bounds.

    Never-settle-for-0 rescue pass (Nov 18, web-resilience): if the main
    attempts still leave 0 rows with every engine failed, a bounded
    rescue pass keeps retrying — alternating route (direct, proxy,
    direct, ...), exponential backoff with jitter — until rows arrive,
    the attempt cap, or the rescue deadline (default 10 min per scene
    batch). A total internet outage still yields 0, but the system keeps
    retrying and reports SETTLED ZERO instead of silently banking 0.
    This covers both the prep path (advance_prep calls the same resolver
    lane) and the live job's web lane.
    """
    global _UNIVERSAL_LAST_AUDIT
    # Nov 22 (d1ece590 stall fix): fail fast on a degenerate query. A query
    # that names no entity ('They', 'They photo', 'They documentary drove
    # photograph') can never return a scene-relevant image, and without this
    # guard it burns the whole retry budget (5 attempts, ~35-40s) on every
    # route before settling zero. Return empty immediately — the caller
    # moves on to the scene's next query — and record the skip in the audit
    # so a zero-result scene shows WHY instead of looking like an engine
    # failure.
    try:
        from .query import is_degenerate_query
    except Exception:
        is_degenerate_query = None
    if is_degenerate_query is not None and is_degenerate_query(query):
        _web_log(
            f"universal scraper: skipping degenerate query "
            f"'{str(query)[:48]}' (no searchable entity) — no provider call"
        )
        with _UNIVERSAL_AUDIT_LOCK:
            _UNIVERSAL_LAST_AUDIT = {
                "query": query,
                "total": 0,
                "engines": [],
                "rescue_attempts": 0,
                "settled_zero": False,
                "degenerate_query": True,
            }
        return []
    default_engine_timeout = 30.0 if wait_until_complete else 8.0
    try:
        enabled = str(os.environ.get("VIDZEO_UNIVERSAL_SCRAPER_ENGINES", "") or "").strip()
        engine_ids = [part.strip() for part in enabled.split(",") if part.strip()] or None
        limit = max(6, min(30, int(os.environ.get("VIDZEO_UNIVERSAL_SCRAPER_LIMIT", "20") or 20)))
        timeout = max(2.0, min(300.0, float(
            os.environ.get("VIDZEO_UNIVERSAL_SCRAPER_TIMEOUT_SECONDS") or default_engine_timeout
        )))
    except (TypeError, ValueError):
        engine_ids, limit, timeout = None, 20, default_engine_timeout
    max_attempts = _universal_retry_max_attempts()
    if bounded:
        # Muse-search fallback: one bounded attempt, no retry, no rescue.
        max_attempts = 1
    backoff = _universal_retry_backoff_seconds()
    response = None
    rows: List[Dict] = []
    _universal_started = time.monotonic()

    def _harvest_late(resp):
        # Wright (Nov 16): candidates arrived a minute after the stage gave
        # up and were lost. When a batch comes back empty — or any engine
        # timed out — re-check the late spool before reporting zero: engines
        # still running past their deadline may have landed meanwhile, and
        # the spool holds rows from earlier batches for this exact query.
        # This is a bounded wait, not another crawl.
        timed_out = any(
            (not stat.success) and "timeout" in str(stat.error or "").lower()
            for stat in resp.stats
        )
        if resp.total == 0 or timed_out:
            default_late_wait = 120.0 if wait_until_complete else 5.0
            try:
                # Late wait raised 5s -> 120s (GTA fix, Nov 16): the old 5s was
                # far short of the ~22s gap between the wave abandoning a batch
                # and its engines landing, so every late row was orphaned. 120s
                # covers the gap with headroom; the scene-level pre-broadened
                # harvest is the second chance for rows that arrive later still.
                late_wait = max(
                    0.0,
                    min(
                        300.0,
                        float(
                            os.environ.get("VIDZEO_UNIVERSAL_LATE_WAIT_SECONDS") or default_late_wait
                        ),
                    ),
                )
            except (TypeError, ValueError):
                late_wait = default_late_wait
            if resp.total and not wait_until_complete:
                # Healthy engines already supplied usable leads. Harvest
                # available late rows now and let recovery query again for gaps.
                late_wait = 0.0
            late_rows = _UNIVERSAL_SCRAPER.collect_late_results(
                query, wait_seconds=late_wait
            )
            if late_rows:
                seen_urls = {
                    str(item.image_url or "").strip().casefold()
                    for item in resp.results
                }
                extra = [
                    item for item in late_rows
                    if str(item.image_url or "").strip().casefold() not in seen_urls
                ]
                if extra:
                    return UniversalSearchResponse(
                        resp.query,
                        resp.total + len(extra),
                        list(resp.results) + extra,
                        list(resp.stats),
                    )
        return resp

    def _map_rows(resp):
        mapped: List[Dict] = []
        for item in resp.results:
            candidate = universal_result_to_candidate(item, query=query)
            safe, _reason = candidate_is_safe(candidate)
            if safe:
                mapped.append(candidate)
        return mapped

    search_kwargs = {
        "engine_ids": engine_ids,
        "limit_per_engine": limit,
        "timeout_seconds": timeout,
        "wait_until_complete": bool(wait_until_complete),
    }
    direct_first = web_direct_first()
    for attempt in range(max_attempts):
        direct = attempt % 2 == 0 if direct_first else attempt > 0
        # Nov 22 (d1ece590 stall fix): never retry after cancellation. When
        # the attempt is finished, every engine "fails" (the job context
        # raises inside the workers), so without this check a cancelled job
        # re-runs each dead query 5x and fills the log with zero batches.
        # check_job_active() is a no-op when no job is attached (tests,
        # proof scripts), and raises InterruptedError/TimeoutError here so
        # the caller records the scene error and moves on.
        check_job_active()
        if attempt > 0:
            if backoff > 0:
                time.sleep(backoff)
            _web_log(
                f"universal scraper retry {attempt + 1}/{max_attempts} "
                f"for '{str(query)[:48]}' via {'direct' if direct else 'proxy'}"
            )
        search_kwargs["direct"] = direct
        response = _UNIVERSAL_SCRAPER.search(query, **search_kwargs)
        response = _harvest_late(response)
        # Liveness (Nov 22 stall fix): a batch that settled — even with zero
        # rows — is real work done. Record it so the preparation-stall
        # detector can tell "working slowly" from "stuck" during long
        # engine searches instead of killing the attempt at 300s.
        record_activity("web")
        rows = _map_rows(response)
        if rows:
            break
        # No rows: retry only on a transient (all-engines-failed) failure.
        # A genuine no-results (an engine answered with zero rows) is not
        # retried — the query was answered, and repeating it would burn the
        # same wall for the same empty answer.
        if not _universal_result_is_transient_failure(response):
            break
        if attempt + 1 >= max_attempts:
            break
        _web_log(
            f"universal scraper: all engines failed transiently for "
            f"'{str(query)[:48]}'; retrying (attempt {attempt + 2}/{max_attempts})"
        )
    # ── Never-settle-for-0 rescue pass ─────────────────────────────────
    # The main attempts are exhausted with 0 rows and every engine failed.
    # Keep retrying (bounded) instead of silently banking 0: alternate
    # route each attempt, exponential backoff with jitter, until rows
    # arrive, the attempt cap, or the rescue deadline. Genuine no-results
    # never reach here (the loop above breaks on them).
    rescue_attempts = _universal_rescue_max_attempts()
    if bounded:
        rescue_attempts = 0
    rescue_deadline = _universal_rescue_deadline_seconds()
    # Fail-faster (§2.1): when the scraper's consecutive all-empty
    # stalled-batch counter is already at its cap, the retry cap just
    # refused to pay another wall for this query — the rescue pass
    # must not re-pay it either. Rescue attempts for that query are 0.
    # Test 29: the counter was GLOBAL across queries, so one query's
    # failures zeroed 21 other queries' rescues. The streak is now
    # per-query: pass this query so the gate reads only its own
    # failures (a snapshot without a query answers the max, for
    # backward compatibility with callers that have no query).
    try:
        _rescue_streak = 0
        _rescue_snapshot = getattr(_UNIVERSAL_SCRAPER, "progress_snapshot", None)
        if callable(_rescue_snapshot):
            try:
                _rescue_streak = int(
                    _rescue_snapshot(query).get("consecutive_empty_stalled_batches") or 0
                )
            except TypeError:
                _rescue_streak = int(
                    _rescue_snapshot().get("consecutive_empty_stalled_batches") or 0
                )
        if _rescue_streak >= _universal_empty_streak_cap():
            _web_log(
                f"universal scraper rescue skipped: empty-batch streak "
                f"{_rescue_streak} at cap for '{str(query)[:48]}'"
            )
            rescue_attempts = 0
    except Exception:
        pass
    rescue_count = 0
    rescue_started = time.monotonic()
    # Round 13: in wait mode the rescue deadline does not apply — the
    # loop runs until rows arrive or the attempt cap (the genuine
    # failure bound; the loop only runs on transient failure). A clock
    # expiry while the engines are still answering transiently is not
    # a genuine failure.
    while (
        not rows
        and response is not None
        and _universal_result_is_transient_failure(response)
        and rescue_count < rescue_attempts
        and (
            wait_until_complete
            or (time.monotonic() - rescue_started) < rescue_deadline
        )
    ):
        # Same no-retry-after-cancellation rule as the main loop above.
        check_job_active()
        rescue_count += 1
        route_step = max_attempts + rescue_count - 1 if direct_first else rescue_count - 1
        rescue_direct = route_step % 2 == 0
        delay = _universal_rescue_backoff_seconds(rescue_count)
        if delay > 0:
            time.sleep(delay)
        _web_log(
            f"universal scraper rescue pass {rescue_count}/{rescue_attempts} "
            f"for '{str(query)[:48]}' "
            f"(route={'direct' if rescue_direct else 'proxy'}, "
            f"elapsed={time.monotonic() - rescue_started:.0f}s)"
        )
        search_kwargs["direct"] = rescue_direct
        response = _UNIVERSAL_SCRAPER.search(query, **search_kwargs)
        response = _harvest_late(response)
        record_activity("web")
        rows = _map_rows(response)
    settled_zero = bool(
        not rows
        and response is not None
        and _universal_result_is_transient_failure(response)
    )
    if settled_zero:
        _web_log(
            f"universal scraper SETTLED ZERO for '{str(query)[:48]}' after "
            f"{max_attempts + rescue_count} attempt(s) "
            f"({time.monotonic() - rescue_started:.0f}s): all engines failed "
            "on every live route — no candidates banked, scene falls back "
            "to other media"
        )
    with _UNIVERSAL_AUDIT_LOCK:
        _UNIVERSAL_LAST_AUDIT = {
            "query": response.query if response is not None else query,
            "total": response.total if response is not None else 0,
            "engines": [
                {
                    "engine": stat.engine,
                    "count": stat.count,
                    "duration_ms": stat.duration_ms,
                    "success": stat.success,
                    "error": stat.error,
                }
                for stat in (response.stats if response is not None else [])
            ],
            "rescue_attempts": rescue_count,
            "settled_zero": settled_zero,
        }
    if rows:
        _report_provider_success("universal_scraper")
    elif response is not None and response.total == 0 and response.stats and all(not stat.success for stat in response.stats):
        _report_provider_failure(
            "universal_scraper",
            RuntimeError("all universal engines failed with errors"),
        )
    if bounded:
        _scene_tag = (
            f" scene {int(scene_index) + 1}"
            if scene_index is not None else ""
        )
        _web_log(
            f"universal fallback{_scene_tag}: 1 attempt, {len(rows)} rows "
            f"in {time.monotonic() - _universal_started:.1f}s"
        )
    _web_log(
        f"universal scraper mapped {len(rows)}/{response.total if response is not None else 0} candidate(s) "
        f"for '{str(query)[:48]}'"
    )
    return rows


def _universal_audit_snapshot() -> Dict[str, object]:
    with _UNIVERSAL_AUDIT_LOCK:
        return dict(_UNIVERSAL_LAST_AUDIT or {})


def _discover_source_query(
    source_name: str,
    source_type: str,
    query: str,
    gallery_cmd: Optional[List[str]] = None,
    max_timeout: Optional[int] = None,
    wait_until_complete: bool = False,
    direct: Optional[bool] = None,
    universal_bounded: bool = False,
    q_idx: int = 0,
    scene_index: Optional[int] = None,
) -> List[Dict]:
    """Run exactly one provider/query pair."""
    if not str(query or "").strip():
        return []
    if source_type == "muse" or source_name == "muse_search":
        # Muse-search API wave (Openverse + Wikimedia). Route order is
        # per-source inside the module; the wrapper's direct= retry does
        # not apply — the module retries alternate routes itself.
        # Round 13: thread wait_until_complete into the wave — without
        # it the wave abandoned in-flight providers at its 60 s wall
        # even when the resolver was told to wait.
        return search_muse_sources(
            query, q_idx=q_idx, scene_index=scene_index,
            wait_until_complete=wait_until_complete,
        )
    if source_type == "universal" or source_name == "universal_scraper":
        return _discover_universal_urls(
            query,
            wait_until_complete=wait_until_complete,
            bounded=universal_bounded,
            scene_index=scene_index,
        )
    if source_type == "gdl":
        if not gallery_cmd:
            return []
        return _discover_gallery_urls(
            source_name, query, gallery_cmd, max_timeout=max_timeout,
        )
    if direct is None:
        direct = web_direct_first()
    if source_type == "hybrid":
        return _call_with_direct(_discover_hybrid_urls, source_name, query, direct=direct)
    if source_type == "flickr_api":
        return _call_with_direct(_discover_flickr_api_urls, query, direct=direct)
    return _call_with_direct(_discover_api_urls, source_name, query, direct=direct)


def _shared_batch_subject(
    main_subject: str,
    scene_active_subjects: List[str],
    scene_texts: List[str],
    topic_anchor: str = "",
) -> str:
    """Return one canonical pool query for a predominantly single-topic job."""
    from services.media_query_strategy import supported_specific_topic
    specific = supported_specific_topic(topic_anchor, ' '.join(scene_texts), main_subject)
    if specific:
        return specific
    # A recurring character is not a safe shared search anchor for a story
    # containing several named people.  For a royal-family story, warm one
    # family-level pool and leave exact identities to each scene's own query.
    # Other multi-person stories skip shared discovery rather than letting the
    # first identity monopolize every scene's candidate pool.
    distinct_people = {
        re.sub(r"\s+", " ", str(value or "")).strip().casefold()
        for value in scene_active_subjects
        if _is_biographical_person(str(value or "").strip())
    }
    if len(distinct_people) > 1:
        anchor = re.sub(r"\s+", " ", str(topic_anchor or "")).strip()
        lower_anchor = anchor.casefold()
        if re.search(r"\b(?:british\s+)?royal\s+famil(?:y|ies)\b", lower_anchor):
            return "British Royal Family" if "british" in lower_anchor else "Royal Family"
        if re.search(r"\b(?:british\s+)?monarch(?:y|ies)\b", lower_anchor):
            return "British monarchy" if "british" in lower_anchor else "Monarchy"
        return ""
    # Planner rows are often more specific than a truncated UI title. Promote
    # an explicit proper-name entity repeated across most scene contracts (for
    # example ``RMS Titanic`` over ``On April``) before testing the title-derived
    # subject. Requiring two content tokens and proper-name casing keeps generic
    # recurring phrases from becoming a cross-scene search anchor.
    # A short job still benefits from a canonical shared pool.  The previous
    # four-scene floor left the common 2–3 scene UI jobs without the warm
    # multi-source lane, so every scene raced the same provider fallbacks and
    # frequently timed out with only one image.  Require two scenes to avoid
    # changing single-scene semantics while covering the normal short-video
    # path.
    if len(scene_texts) >= 2:
        normalized_subjects: Dict[str, Tuple[str, int]] = {}
        for raw_subject in scene_active_subjects:
            candidate = re.sub(r"\s+", " ", str(raw_subject or "")).strip()
            if not candidate:
                continue
            key = candidate.casefold()
            display, count = normalized_subjects.get(key, (candidate, 0))
            normalized_subjects[key] = (display, count + 1)
        recurring = sorted(
            normalized_subjects.values(),
            key=lambda pair: (pair[1], len(pair[0])),
            reverse=True,
        )
        if recurring:
            candidate, count = recurring[0]
            content_tokens = [
                token
                for token in re.findall(r"[A-Za-z0-9]+", candidate)
                if len(token) >= 2 and token.lower() not in STOPWORDS
            ]
            has_proper_token = any(
                token.isupper() or token[:1].isupper()
                for token in content_tokens
            )
            if (
                count / max(1, len(scene_texts)) >= 0.40
                and len(content_tokens) >= 2
                and has_proper_token
                and not _is_weak_active_subject(candidate)
            ):
                return candidate

    subject = re.sub(r"\s+", " ", str(main_subject or "")).strip()
    if not subject or len(scene_texts) < 2:
        return ""
    # A verified strong anchor (multi-word proper name or explicit override)
    # must warm the pool even when most scene contracts do not repeat it
    # verbatim.  The app run audit showed 0 candidates for the whole batch
    # because the 60%-match gate was unreachable for 'RMS Titanic' contracts
    # phrased as 'Stretching...', 'Wealthy...' etc.
    content_tokens = [
        token
        for token in re.findall(r"[A-Za-z0-9]+", subject)
        if len(token) >= 2 and token.lower() not in STOPWORDS
    ]
    if (
        len(content_tokens) >= 2
        and not _is_weak_active_subject(subject)
    ):
        return subject
    anchor_terms = {
        term
        for term in re.findall(r"[a-z0-9]+", subject.lower())
        if len(term) >= 3 and term not in STOPWORDS
    }
    if not anchor_terms:
        return ""
    matched = 0
    for active_subject, scene_text in zip(scene_active_subjects, scene_texts):
        blob = f"{active_subject or ''} {scene_text or ''}".lower()
        if any(re.search(r"\b" + re.escape(term) + r"\b", blob) for term in anchor_terms):
            matched += 1
    return subject if matched / max(1, len(scene_texts)) >= 0.40 else ""


def _shared_pool_prefetch_limit(
    scene_count: int,
    *,
    multi_identity_job: bool,
    configured_limit: Any = None,
) -> int:
    """Bound blocking warm-pool downloads so scene queries get time to run."""
    count = max(1, int(scene_count or 1))
    if multi_identity_job:
        default = max(4, min(8, count))
        floor, ceiling = 4, 12
    else:
        default = max(8, min(16, count + 2))
        floor, ceiling = 8, 20
    try:
        requested = int(configured_limit if configured_limit is not None else default)
    except (TypeError, ValueError):
        requested = default
    return max(floor, min(ceiling, requested))


def _has_explicit_visual_identity_conflict(candidate: Mapping[str, Any]) -> bool:
    """Identify affirmative metadata conflicts without treating missing data as a mismatch."""
    if not isinstance(candidate, Mapping):
        return False
    evidence = candidate.get("candidate_evidence")
    if not isinstance(evidence, Mapping):
        evidence = {}
    return bool(
        evidence.get("canonical_entity_mismatch")
        or evidence.get("conflicting_entity")
        or evidence.get("conflicting_subject_match")
        or evidence.get("forbidden_subject_match")
        or candidate.get("hard_identity_conflict")
    )


def _warm_shared_discovery_pool(
    *,
    query: str,
    scene_count: int,
    gallery_cmd: Optional[List[str]],
    discovery_cache,
    source_telemetry,
    stage_deadline_at: float,
    source_health=None,
    batch_seconds_override: Optional[float] = None,
    wait_until_complete: bool = False,
) -> Dict[str, object]:
    """Warm one shared provider pool and retain partial provider success."""
    query = re.sub(r"\s+", " ", str(query or "")).strip()
    if not query:
        return {"query": "", "candidates": 0, "providers_completed": 0, "providers_pending": 0}
    # Run-7 fix: the wall must fit one engine round-trip (see
    # _shared_batch_wall_seconds).  The stage deadline still bounds the
    # wait via batch_deadline below; there is no scene-count clamp.
    batch_seconds = _shared_batch_wall_seconds(batch_seconds_override, scene_count)
    # Wait mode collects all providers that finish within this batch wall;
    # it must not turn one slow provider into an unbounded stage wait.
    batch_deadline = min(stage_deadline_at, time.monotonic() + batch_seconds)
    sources = []
    # Source *names* are unique; the transport kind is not. Treating every
    # ``api`` provider as the same kind used to warm only Yandex plus one
    # other lane, silently skipping Openverse/Wikimedia/Google rescue rows.
    # Keep the providers complementary while still avoiding duplicate names.
    seen_names = set()
    for name, kind in _enabled_fast_sources():
        if source_health is not None and not source_health.healthy(name):
            continue
        # Warm each healthy source once; scene workers share these results.
        if name in seen_names:
            continue
        sources.append((name, kind))
        seen_names.add(name)
    # One Bing-family lane per wave (GTA fix): the warm pool is the first
    # wave of the job, and fanning the Bing re-skins beside the plain lane
    # doubled proxy load for the same host before any scene even started.
    sources = _dedupe_transport_families(sources)
    # Round 5 (test 32 fix): the muse-search wave can spend up to its
    # max wall (first attempt + one transport retry) when the proxy is
    # slow — a wave that answers at 55–115 s is a success, not a stall.
    # The batch deadline must cover it, or the warm-up pool cancels the
    # wave's future at the 45 s batch wall and its rows are orphaned
    # even though the wave itself completed. The early-break still
    # closes a fast batch early, so this costs time only when the pool
    # is empty; the stage deadline still bounds the wait. The floor
    # applies only when no explicit batch wall was passed: an explicit
    # override (tests, health probes, a nearly-spent budget) still
    # wins, per _shared_batch_wall_seconds' contract.
    if (
        batch_seconds_override is None
        and muse_search_enabled()
        and any(name == "muse_search" for name, _kind in sources)
    ):
        batch_deadline = max(
            batch_deadline,
            min(
                stage_deadline_at,
                time.monotonic() + muse_search_max_wave_wall(),
            ),
        )
    if not sources:
        _web_log("shared batch pool: all providers unhealthy; skipping batch warm-up")
        return {"query": "", "candidates": 0, "providers_completed": 0, "providers_pending": 0}
    # Keep enough physical URLs for the per-scene duplicate ledger. A small
    # ``scene_count + 8`` pool was quickly exhausted by a flaky host or one
    # repeated thumbnail, leaving later scenes with no fresh candidate.
    target = max(24, min(96, int(scene_count) * 3 + 12))
    candidates_by_url: Dict[str, Dict] = {}
    completed_sources: List[str] = []
    universal_audit: Dict[str, object] = {}

    def fetch(name: str, kind: str) -> List[Dict]:
        callback = lambda: _discover_source_query(
            name,
            kind,
            query,
            gallery_cmd,
            max_timeout=30,
            wait_until_complete=wait_until_complete,
        )
        if discovery_cache is not None:
            remaining = max(0.25, batch_deadline - time.monotonic())
            return discovery_cache.get_or_fetch(
                name,
                query,
                callback,
                telemetry=source_telemetry,
                # Round 13 (W2 reverted): the warm-up keeps its batch
                # bound even in wait mode — it is a prefetch, not a
                # gate (pinned by
                # test_wait_mode_shared_provider_pool_returns_at_batch_deadline).
                # A provider still working at the wall keeps running in
                # the background and its rows land in the cache, where
                # the per-scene wave (unbounded in wait mode) harvests
                # them. The scene — not the warm-up — is what must never
                # be cut off.
                wait_timeout=remaining,
            )
        return callback()

    pool = ThreadPoolExecutor(
        max_workers=max(1, min(8, len(sources))),
        thread_name_prefix="web-batch-discovery",
    )
    futures = {pool.submit(fetch, name, kind): name for name, kind in sources}
    pending = set(futures)
    try:
        # Round 13 (W2 reverted): the warm-up keeps its batch deadline
        # in wait mode too — see the note on the cache wait above. The
        # per-scene wave is the stage that runs to resolution.
        while pending and time.monotonic() < batch_deadline:
            remaining = max(0.0, batch_deadline - time.monotonic())
            done, pending = wait(
                pending,
                timeout=min(0.25, remaining),
                return_when=FIRST_COMPLETED,
            )
            if not done:
                continue
            for future in done:
                source_name = futures[future]
                completed_sources.append(source_name)
                try:
                    rows = list(future.result() or [])
                    if rows and source_health is not None:
                        source_health.note_success(source_name)
                except Exception as exc:
                    rows = []
                    if source_health is not None:
                        source_health.note_failure(
                            source_name, f"{type(exc).__name__}: {str(exc)[:80]}"
                        )
                if source_name == "universal_scraper":
                    universal_audit = _universal_audit_snapshot()
                for item in rows:
                    url = str(item.get("url") or item.get("image_url") or "").strip()
                    safe, _reason = candidate_is_safe(item)
                    if url and safe:
                        candidates_by_url.setdefault(url, item)
            # Do not stop as soon as Yandex/Flickr fill the numeric target:
            # those lanes can return blocked thumbnails while Openverse or
            # Wikimedia has a clean, directly downloadable rescue row still
            # in flight. The universal scraper already fans out to Openverse,
            # though, so a completed universal batch with Openverse rows is
            # equivalent rights coverage and should not wait for the slower
            # dedicated Openverse request a second time.
            universal_openverse_ready = bool(
                "universal_scraper" in completed_sources
                and any(
                    str(engine.get("engine") or "").strip().lower() == "openverse"
                    and int(engine.get("count") or 0) > 0
                    for engine in (universal_audit.get("engines") or [])
                    if isinstance(engine, Mapping)
                )
            )
            rights_ready = bool(
                universal_openverse_ready
                or any(
                    provider in completed_sources
                    for provider in ("openverse", "wikimedia")
                )
            )
            # Universal discovery is the primary copied scraper lane. Let it
            # finish its own three-engine fan-out before closing a full pool so
            # the winning rows are actually available to scene assignment;
            # otherwise a fast legacy provider could fill the numeric target
            # while the universal callback was still running in the background.
            universal_ready = (
                "universal_scraper" not in seen_names
                or "universal_scraper" in completed_sources
            )
            if (
                not wait_until_complete
                and len(candidates_by_url) >= target
                and rights_ready
                and universal_ready
            ):
                break
    finally:
        # Round 13 (W2 reverted): a provider still working at the batch
        # deadline is left pending — cancel() only stops a future that
        # has not started; a running call finishes in the background and
        # its rows land in the cache. The warm-up never blocks the
        # per-scene wave.
        for future in pending:
            future.cancel()
        # In-flight provider calls remain bounded by their own network timeout.
        pool.shutdown(
            wait=not pending,
            cancel_futures=bool(pending),
        )

    _web_log(
        "shared batch pool: "
        f"query='{query[:48]}', {len(candidates_by_url)} candidate(s), "
        f"providers={len(completed_sources)}/{len(sources)} completed"
    )
    return {
        "query": query,
        "candidates": len(candidates_by_url),
        # Keep the actual rows, not only telemetry counts.  The old warm-up
        # populated broker telemetry but the per-scene resolver could not
        # consume those rows when the cache entry was released/expired. That
        # made a successful shared search appear as 0 resolved scenes.
        "candidate_rows": list(candidates_by_url.values()),
        "providers_completed": len(completed_sources),
        "providers_pending": len(pending),
        "provider_names_completed": completed_sources,
        "universal_audit": universal_audit,
    }


def _pre_score_candidate(
    url: str,
    title: str,
    source: str,
    query: str,
    scene_text: str,
    identity_terms: Set[str],
    main_subject: str = "",
    tags: str = "",
    description: str = "",
    source_url: str = "",
    weak_terms: Optional[Set[str]] = None,
    strict_subject: bool = False,
    allow_topic_pending: bool = False,
) -> float:
    strict_subject = bool(
        strict_subject
        or (identity_terms and set(identity_terms) - _WEB_CONTAINER_TERMS)
    )
    score = 0.0
    tags_str = (" ".join(tags) if isinstance(tags, (list, tuple, set)) else str(tags or "")).lower()
    desc_str = (" ".join(description) if isinstance(description, (list, tuple, set)) else str(description or "")).lower()
    url_lower = str(url or "").lower()
    title_lower = str(title or "").lower()
    source_url_lower = str(source_url or "").lower()

    if _web_junk_metadata(title, url, source_url, tags_str, desc_str):
        return -50.0
    watermark_terms = {"alamy", "gettyimages", "shutterstock", "istockphoto", "dreamstime", "depositphotos", "123rf", "watermark", "watermarked"}
    if any(wm in title_lower or wm in url_lower or wm in tags_str or wm in desc_str or wm in source_url_lower for wm in watermark_terms):
        return -50.0
    metadata_item = {
        "url": url,
        "title": title,
        "source": source,
        "tags": tags_str,
        "description": desc_str,
        "source_url": source_url,
    }
    candidate_class, _class_reason = _metadata_candidate_class(
        metadata_item,
        main_subject=main_subject,
        scene_text=scene_text,
    )
    if candidate_class == "irrelevant" and not allow_topic_pending:
        return -50.0
    topic_mismatch = _topic_candidate_mismatch(
        title=title,
        url=f"{url} {source_url}",
        query=query,
        scene_text=scene_text,
        main_subject=main_subject,
    )
    if topic_mismatch and not allow_topic_pending:
        if (
            _is_biographical_person(main_subject)
            and not (
                candidate_class == "anchored_generic"
                and _class_reason == "rail_role_alias_pending_visual"
            )
            and not _metadata_has_identity(title, url, "", identity_terms)
        ):
            return -50.0
        return -25.0
    
    # 1. Term matching in title and URL
    query_terms = set(re.findall(r'[a-z]{3,}', query.lower())) - STOPWORDS
    scene_terms = set(re.findall(r'[a-z]{3,}', scene_text.lower())) - STOPWORDS
    
    title_words = set(re.findall(r'[a-z]{3,}', title_lower))
    url_words = set(re.findall(r'[a-z]{3,}', f"{url_lower} {source_url_lower}"))
    metadata_words = set(re.findall(r'[a-z]{3,}', f"{tags_str} {desc_str}"))
    all_words = title_words | url_words | metadata_words
    
    query_match = len(query_terms & all_words)
    scene_match = len(scene_terms & all_words)
    
    relevance = (query_match * 2.0 + scene_match * 1.0) / max(len(query_terms | scene_terms), 1)
    score += relevance * 3.0
    if candidate_class == "anchored":
        score += 1.0
    elif candidate_class == "anchored_generic":
        score -= 1.5
    
    # 1b. Graded gate: junk pages hard-rejected; rare-term matches score
    # normally; topic-adjacent candidates with generic overlap score low but
    # stay eligible so hard scenes still resolve instead of failing the job.
    # ``strict_ok`` is used by the final score adjustment for both matched and
    # unmatched metadata.  Initialize it before the branch so sparse provider
    # rows cannot crash a scene worker.
    strict_ok = True
    if all_words:
        meaningful, rare = _web_rare_terms(scene_terms, weak_terms)
        stemmed_all = _web_stem_terms(all_words)
        matched = _web_stem_terms(meaningful) & stemmed_all
        if not matched:
            has_identity = bool(
                identity_terms
                and any(
                    re.search(r"\b" + re.escape(t.lower().rstrip("s")) + r"s?\b", f"{title_lower} {url_lower}")
                    for t in (_extract_discriminating_subject_terms(identity_terms) or identity_terms)
                )
            )
            if allow_topic_pending:
                # The query is item-anchored but the caption has no shared
                # scene vocabulary. Keep a bounded negative ordering score so
                # the downloaded pixels can decide relevance.
                score = max(score - 2.0, -4.5)
            elif candidate_class in ("anchored", "anchored_generic") or has_identity:
                # Authentic topic asset: keep eligible at baseline score rather than hard rejection
                # so scenes without a rare narration match still resolve unique topic imagery.
                score = max(score, 1.0 if candidate_class == "anchored" else 0.5)
            else:
                return -15.0
        else:
            strict_ok = (
                not rare
                or bool(_web_stem_terms(rare) & stemmed_all)
                or (_web_curated_source("", source) and len(matched) >= 2)
            )
            if not strict_ok and rare:
                if strict_subject:
                    has_identity = bool(
                        identity_terms
                        and any(
                            re.search(r"\b" + re.escape(t.lower().rstrip("s")) + r"s?\b", f"{title_lower} {url_lower}")
                            for t in (_extract_discriminating_subject_terms(identity_terms) or identity_terms)
                        )
                    )
                    if allow_topic_pending:
                        score = max(score - 2.0, -4.5)
                    elif candidate_class in ("anchored", "anchored_generic") or has_identity:
                        score = max(score, 0.5)
                    else:
                        return -15.0
                else:
                    # Shared setting words (for example "deep sea") are not enough
                    # when the scene has a more discriminating subject ("welder").
                    # Keep the candidate as a last-resort possibility, but below the
                    # normal acceptance threshold so irrelevant topic-adjacent pages
                    # cannot outrank an actually anchored result.
                    score -= 4.0

    # 2. Identity gating: heavy penalty if subject discriminating name not in title or URL
    if identity_terms:
        if (
            _is_biographical_person(main_subject)
            and not (
                candidate_class == "anchored_generic"
                and _class_reason == "rail_role_alias_pending_visual"
            )
            and not _metadata_has_identity(title, url, "", identity_terms)
        ):
            return -50.0
        discrim_terms = _extract_discriminating_subject_terms(identity_terms)
        target_terms = discrim_terms if discrim_terms else identity_terms

        has_full_identity = all(
            re.search(r"\b" + re.escape(term.lower().rstrip("s")) + r"s?\b", f"{title_lower} {url_lower} {tags_str}")
            for term in target_terms
        )
        if not has_full_identity:
            partial_match = any(
                re.search(r"\b" + re.escape(term.lower().rstrip("s")) + r"s?\b", f"{title_lower} {url_lower} {tags_str}")
                for term in target_terms
            )
            if allow_topic_pending:
                # Item-specific search text is an admissible ordering hint for
                # listicles, but it is never identity proof. Keep the row in
                # the shortlist for the pixel gate with a bounded penalty.
                score -= 4.0
            elif partial_match and len(target_terms) > 1:
                score -= 6.0
            elif partial_match:
                score -= 4.0
            else:
                score -= 25.0
                
    # 2.5 Wrong Person Filter
    if main_subject and _is_wrong_person(title, main_subject):
        score -= 40.0
            
    # 3. Source priority bonus
    source_bonuses = {
        "bing_hybrid": 2.0,
        "wikimedia": 1.8,
        "openverse": 1.5,
        "flickr_api": 1.5,
        "getty_bing": 1.8,
        "reddit": 1.0,
        "pinterest": 0.8,
        "flickr": 1.0,
        "qwant": 0.2,
        "imgur": 0.1,
    }
    score += source_bonuses.get(source, 0.0)
    
    # 4. Dimension hints
    if any(p in url_lower for p in ("/1080", "/1920", "/2048", "orig", "large", "high")):
        score += 0.5
    if any(p in url_lower for p in ("/portrait", "/square", "/avatar", "/320x")):
        score -= 0.5
        
    return score


def _topic_candidate_mismatch(title: str, url: str, query: str, scene_text: str, main_subject: str = "") -> bool:
    """Reject broad-topic image hits that drift into a different visual niche.

    Scene-subject aware (GTA VI proof job): a scene whose narration is
    ABOUT an earlier installment (e.g. scene 0 narrates GTA V's sales
    and the decade-long wait) legitimately shows that installment, so a
    GTA V image is not a mismatch for THAT scene — only for scenes
    about the newer installment's own content. The 2002 Vice City game
    never gains an anchor: it is neither the narrated installment nor
    the main subject.
    """
    metadata_text = f"{title} {url}".lower()
    if not metadata_text.strip():
        return False
    subject = re.sub(r"\s+", " ", str(main_subject or "").strip())
    if not subject:
        return False
    if not _is_legacy_installment_image(
        f"{title} {url}"
    ) and alternate_anchor_allows(
        _narrated_installment_anchor(scene_text, subject), metadata_text
    ):
        # Scene-subject anchor: the scene narrates this installment, so
        # its imagery is on-topic here even though it is not the main
        # subject. Legacy-cutscene rows never gain this exemption.
        return False
    discrim = _extract_discriminating_subject_terms(subject)
    terms = list(discrim) if discrim else [
        token for token in re.findall(r"[a-z0-9]+", subject.lower())
        if len(token) > 2 and token not in STOPWORDS
    ]
    if not terms:
        return False
    return not any(re.search(r"\b" + re.escape(term.rstrip("s")) + r"s?\b", metadata_text) for term in terms)


def _publisher_screenshot_query(subject: str, scene_text: str) -> str:
    """Use an official still first for GTA, with the scene's installment intact."""
    match = re.search(r"\b(?:grand theft auto|gta)\s*(vi|v|6|5)\b", subject, re.I)
    if not match:
        return ""
    edition = "VI" if match.group(1).lower() in {"vi", "6"} else "V"
    alternate = _narrated_installment_anchor(scene_text, f"Grand Theft Auto {edition}") or {}
    if alternate.get("version") in {"5", "6"}:
        edition = "VI" if alternate["version"] == "6" else "V"
    terms = re.findall(r"\b(?:Lucia|Jason|Vice City|Leonida|beaches|swamps|trailer|gameplay)\b", scene_text, re.I)
    focus = " ".join(dict.fromkeys(term.lower() for term in terms))[:48] if edition == "VI" else ""
    return " ".join(part for part in (
        f"Grand Theft Auto {edition}", focus, "official screenshot site:rockstargames.com",
    ) if part)


def _royal_identity_requirement(main_subject: str, scene_text: str = "") -> str:
    """Return the required royal identity class for a scene, if any.

    Bare first names are never sufficient in a Charles/Harry story.  The
    requirement is scene-local so a Charles image and a Harry image may both
    be used without making every scene require both people.
    """
    subject = str(main_subject or "").lower()
    scene = str(scene_text or "").lower()
    blob = f"{subject} {scene}"
    royal_context = bool(
        re.search(r"\broyal\s+family\b|\bbritish\s+royal\b|\bwindsor\b|\bduke\s+of\s+sussex\b|\bking\s+charles\b|\bcharles\s+iii\b|\bprince\s+harry\b", blob)
        or (re.search(r"\bcharles\b", blob) and re.search(r"\bharry\b", blob))
    )
    if not royal_context:
        return ""
    # A family/group anchor stays group-scoped. Scene metadata may mention
    # either person, so one name inside the scene must not over-specialize the
    # global family gate.
    if re.search(r"\b(?:royal\s+family|british\s+royal|monarchy)\b", subject):
        return "family"
    if re.search(r"\broyal\s+family\b|\bbritish\s+royal\b|\bwindsor\b", subject) and re.search(r"\b(?:charles|harry)\b", subject):
        return "family"
    if re.search(r"\bcharles\b", scene) and not re.search(r"\bharry\b", scene):
        return "charles"
    if re.search(r"\bharry\b", scene) and not re.search(r"\bcharles\b", scene):
        return "harry"
    if re.search(r"\b(?:harry|prince\s+harry|duke\s+of\s+sussex)\b", subject) and not re.search(r"\bcharles\b", subject):
        return "harry"
    if re.search(r"\b(?:charles|king\s+charles|charles\s+iii|prince\s+of\s+wales)\b", subject) and not re.search(r"\bharry\b", subject):
        return "charles"
    return "family"


def _royal_metadata_matches(metadata: str, requirement: str) -> bool:
    """Require title-qualified royal metadata, excluding music/film collisions."""
    if not requirement:
        return True
    text = str(metadata or "").lower()
    # A band/concert/album is never a royal-family still, even if the member is
    # named Harry or Charles.
    if re.search(r"\b(?:band|concert|music|album|singer|guitarist|drummer|tour|stage)\b", text):
        return False
    # Search engines frequently return a literal "frog prince"/cartoon card
    # for a Prince Harry query.  These are clear confusable entities and must
    # be rejected before download; uncertain/near matches still flow to the
    # pixel verifier through the normal pending path.
    if re.search(
        r"\b(?:frog|toad|cartoon|toy|mascot|anime|fictional\s+character|"
        r"fantasy\s+character|plush|figurine|action\s+figure|storybook\s+character)\b",
        text,
    ):
        return False
    royal_marker = bool(re.search(r"\b(?:royal|windsor|monarchy|palace|prince|king|duke|sussex|wales)\b", text))
    if requirement == "harry":
        return bool(re.search(r"\bharry\b", text) and royal_marker)
    if requirement == "charles":
        return bool(re.search(r"\bcharles\b", text) and royal_marker)
    # Family scenes need a royal marker plus one of the two named subjects;
    # this avoids accepting a generic royal-costume image as the story anchor.
    return royal_marker and bool(re.search(r"\b(?:charles|harry)\b", text))


def _metadata_candidate_class(
    item: Dict,
    main_subject: str = "",
    scene_text: str = "",
    negative_entities: Optional[List[str]] = None,
) -> Tuple[str, str]:
    """Classify a candidate using only provider metadata, never its query.

    ``anchored`` means the container subject and at least one scene facet are
    visible in provider metadata.  ``anchored_generic`` is usable but weaker;
    ``irrelevant`` is a hard pre-download rejection.  Candidates with no
    metadata remain ``unverified`` so legacy providers that return bare URLs do
    not change behavior.
    """
    urls = " ".join(
        str(item.get(key) or "")
        for key in ("url", "image_url", "source_url", "page_url")
    )
    metadata = re.sub(
        r"\s+", " ", f"{candidate_asset_metadata(item)} {urls}"
    ).strip().lower()
    if not metadata:
        return "unverified", "missing_metadata"

    royal_requirement = _royal_identity_requirement(main_subject, scene_text)
    # Source-locked Yandex runs can deliberately use the resolver as a broad
    # recall lane. The normal royal-family prefilter is useful for mixed jobs,
    # but it rejects legitimate palace/ceremony pages whose titles omit
    # Charles or Harry. Keep the relaxation opt-in and leave all download,
    # safety, and duplicate checks unchanged.
    relaxed_royal_prefilter = str(
        os.environ.get("VIDZEO_WEB_DISABLE_ROYAL_PREFILTER", "0") or "0"
    ).strip().lower() in {"1", "true", "yes", "on"}
    if royal_requirement and not relaxed_royal_prefilter and not _royal_metadata_matches(metadata, royal_requirement):
        return "irrelevant", f"royal_identity_mismatch:{royal_requirement}"
    if royal_requirement == "family":
        # A provider may label the same qualified result by either person or
        # by the group. Do not require every token from the long canonical
        # anchor after the royal gate has already passed.
        return "anchored", ""

    for entity in negative_entities or []:
        entity = str(entity or "").strip()
        if entity and re.search(r"\b" + re.escape(entity.lower()) + r"\b", metadata):
            return "irrelevant", f"conflicting_entity:{entity}"

    discrim = _extract_discriminating_subject_terms(main_subject)
    subject_terms = list(discrim) if discrim else [
        term for term in re.findall(r"[a-z0-9]+", str(main_subject or "").lower())
        if len(term) >= 3 and term not in STOPWORDS and term not in _GENERIC_TOPIC_WORDS
    ]
    if not subject_terms:
        return "unverified", "no_container_subject"

    def _has_term(term: str, text: str) -> bool:
        singular = term[:-1] if term.endswith("s") and len(term) > 4 else term
        return bool(re.search(r"\b" + re.escape(singular) + r"s?\b", text))

    # Occupational rail aliases are not biographies.  The generic person
    # heuristic otherwise classifies the two capitalized words in "Train
    # Crew" as a human name and rejects a legitimate "Locomotive Engineer"
    # or "Railway Worker" result before the semantic contract can defer it to
    # pixel-level verification.
    rail_subject = bool(re.search(
        r"\b(?:train|railway|railroad|rail)\s+crew\b",
        str(main_subject or ""),
        re.IGNORECASE,
    ))
    rail_role_metadata = bool(re.search(
        r"\b(?:train|railway|railroad|rail|locomotive|conductor|engineer|dispatcher|yardmaster|switchman)\b",
        metadata,
        re.IGNORECASE,
    ))
    if rail_subject and rail_role_metadata:
        return "anchored_generic", "rail_role_alias_pending_visual"

    is_person_subject = _is_biographical_person(main_subject)
    if is_person_subject and len(subject_terms) > 1:
        if not all(_has_term(term, metadata) for term in subject_terms):
            return "irrelevant", "missing_full_person_identity"
    elif not any(_has_term(term, metadata) for term in subject_terms):
        return "irrelevant", "missing_container_anchor"

    scene_terms = [
        term for term in re.findall(r"[a-z0-9]+", str(scene_text or "").lower())
        if len(term) >= 4
        and term not in STOPWORDS
        and term not in _GENERIC_TOPIC_WORDS
        and term not in subject_terms
        and term not in {
            "scene", "image", "photo", "picture", "shows", "showing",
            "people", "person", "thing", "things", "would", "could",
        }
    ]
    if not scene_terms:
        return "anchored", ""
    if any(_has_term(term, metadata) for term in scene_terms):
        return "anchored", ""
    return "anchored_generic", "missing_scene_facet"


# =============================================================================
#  PER-SCENE RESOLVER

def _compute_md5(path: str) -> Optional[str]:
    try:
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception:
        return None


def _extract_script_asset_variants(script: str, title: str, subject: str, limit: int = 36) -> List[str]:
    text = " ".join(str(part or "") for part in (title, script, subject))
    lower = text.lower()
    variants: List[str] = []
    reject_terms = {
        "whether", "let", "inside", "introduced", "assist", "for over", "century",
        "today", "while", "because", "there", "these", "those", "this", "that",
        "level", "enter", "designed", "active", "drive", "driving", "future",
    }

    capitalized = re.findall(r"\b[A-Z][A-Za-z0-9-]{2,}(?:\s+[A-Z][A-Za-z0-9-]{2,}){0,2}\b", text)
    for item in capitalized:
        cleaned = " ".join(w for w in item.split() if w.lower() not in STOPWORDS and len(w) > 2)
        cleaned_lower = cleaned.lower()
        if (
            cleaned
            and cleaned_lower not in {"for over", "vidnets studio"}
            and not any(part in reject_terms for part in cleaned_lower.split())
        ):
            variants.append(f"{cleaned} photo")
            # NOTE: the "{cleaned} documentary" variant used to be appended
            # here too. It produced nonsense queries from sentence-start words
            # ("Behind documentary", "Amtrak Long Run Through documentary")
            # that no image engine can match, so it was removed.

    visual_terms = _extract_visual_keywords(text, max_kws=16) if "_extract_visual_keywords" in globals() else []
    if subject:
        for term in visual_terms:
            if term.lower() not in subject.lower() and term.lower() not in BAD_QUERY_TERMS:
                variants.append(f"{subject} {term}")

    seen = set()
    unique = []
    for variant in variants:
        cleaned = re.sub(r"\s+", " ", variant).strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            unique.append(cleaned)
        if len(unique) >= limit:
            break
    return unique


def _query_content_word_count(query: str, subject: str) -> int:
    words = [w for w in re.findall(r"[a-z0-9]+", str(query or "").lower()) if len(w) > 2]
    subject_words = set(re.findall(r"[a-z0-9]+", str(subject or "").lower()))
    return len([w for w in words if w not in subject_words and w not in STOPWORDS and w not in BAD_QUERY_TERMS])


def _same_query_candidate_limit(max_candidate_downloads: int = 20, top_score: float = 0.0) -> int:
    """Return the bounded number of results to retain from one web query.

    Provider APIs and scrapers commonly return a useful page of results for a
    single exact query.  The resolver used to throw almost all of that recall
    away when the first metadata match scored highly (two downloads), leaving
    the visual gate and shot planner without alternate assets.  Keep a small,
    configurable page of candidates instead.  This improves scene coverage
    without treating query text as evidence: every retained row still passes
    metadata, safety, pixel validation, deduplication, and the visual gate.
    """
    try:
        ceiling = max(3, min(20, int(max_candidate_downloads or 20)))
    except Exception:
        ceiling = 20
    # A high metadata score is not proof that the first download is the best
    # visual. Keep a bounded shortlist for the verifier instead of collapsing
    # the query to two files. The optional setting is intentionally applied
    # only to high-confidence rows; low-score queries retain their full
    # recall ceiling so a nearest relevant frame can still be found.
    env_val = os.environ.get("VIDZEO_WEB_SAME_QUERY_CANDIDATES")
    if float(top_score or 0.0) >= 8.0:
        try:
            # Default matches the per-scene top-12 pool (Nov 10, 2026):
            # a high-confidence query keeps a full page of 12, not 8, so
            # the vision shortlist can actually reach 12.
            configured = max(3, min(12, int(env_val))) if env_val else 12
            return min(ceiling, configured)
        except Exception:
            return min(ceiling, 12)
    if float(top_score or 0.0) >= 5.0:
        # A decent metadata hit still is not proof that the first result is
        # the best frame. Keep a wider bounded page so the cross-scene
        # allocator and final visual gate have real alternatives. The caller
        # still applies its per-scene ceiling (4 in recovery, 5+ in normal
        # mixed-media runs), so this cannot turn into an unbounded scrape.
        return min(ceiling, 6)
    return ceiling


def _vision_shortlist_limit() -> int:
    """Return the maximum downloaded candidates sent to visual comparison."""
    try:
        configured = int(
            os.environ.get("VIDZEO_WEB_VISION_SHORTLIST", "12") or 12
        )
    except Exception:
        configured = 12
    return max(3, min(12, configured))


def _single_verify_cap() -> int:
    """Return how many ranked candidates the visual gate single-verifies (P4).

    The gate tries candidates in metadata-rank order and stops at the
    first qualifier, so the cap bounds the worst case, not the common
    one. Default 3: the old compare judged at most 4 images in one call.
    """
    try:
        configured = int(
            os.environ.get("VIDZEO_WEB_VISUAL_GATE_SINGLE_VERIFY_CAP", "3") or 3
        )
    except Exception:
        configured = 3
    return max(1, min(4, configured))


def _is_prep_cache_hit(candidate) -> bool:
    """True when a candidate is a prep-banked verified image (cached_prep).

    The prep pipeline only banks winners that passed live vision for
    their (title, narration, query) triple, so the image is already
    vision-verified — the live visual pass must spend no vision call on it.
    """
    if not isinstance(candidate, Mapping):
        return False
    return bool(candidate.get("prep_cache_hit") or candidate.get("reused_from_prep"))


def _prep_verified_visual_decision() -> dict:
    """Synthetic accepted verdict for a prep-banked image.

    Same stamp the coordinator's pre-stock web rescue applies
    (cached_prep, 0 fresh vision calls): the image was verified at prep
    time, so downstream gates treat it as vision-verified. The final
    gate still sees the verified requirement hash + asset sha stamped on
    acceptance, and every other gate (file, hard gate, reuse caps,
    adjacency, watermark, identity/conflict) still applies.
    """
    return {
        "accepted": True,
        "status": "accepted",
        "visual_checked": True,
        "semantic_verified": True,
        "visual_available": True,
        "prep_verified": True,
        "verification_source": "cached_prep",
    }


def _prep_verified_model_row(candidate) -> dict:
    """Synthetic vision row for a prep-banked image, for _judge_candidate.

    An accepted verdict, not a guess: the prep pipeline banked this
    image only after it passed live vision. It carries no pixel
    description (scene_description stays empty so facet/conflict checks
    cannot veto on metadata text) and costs no fresh vision call.
    _judge_candidate still applies its explicit-conflict and safety
    rules to it, so a prep row that conflicts with the scene can still
    be rejected and the next live candidate tried.
    """
    return {
        "candidate_id": str(
            candidate.get("candidate_id")
            or candidate.get("prep_key")
            or candidate.get("path")
            or ""
        ),
        "relevance": 90.0,
        "quality": 85.0,
        "confidence": 90.0,
        "fatal_mismatch": False,
        "entity_conflict": False,
        "adult_content": False,
        "suggestive_content": False,
        "synthetic_content": False,
        "unsafe_content": False,
        "watermark": False,
        "has_text": False,
        "domain_match": True,
        "scene_match": True,
        "contextual_match": True,
        "nearest_match": True,
        "visible_overlays": [],
        "subject_box": {},
        "safe_text_regions": [],
        "reason": "prep_cache_verified: already vision-verified at prep time; no fresh vision call",
        "scene_description": "",
        "prep_verified": True,
        "verification_source": "cached_prep",
    }


def _cross_scene_phash_threshold() -> int:
    """Return the conservative near-duplicate threshold used across scenes.

    The old resolver reused ``PERCEPTUAL_HASH_THRESHOLD`` (12) for both
    near-identical derivatives and legitimately different photographs of the
    same subject.  That was too broad for archival subjects: multiple
    compositions can be within twelve average-hash bits while still being
    useful, distinct shots.  Exact duplicates remain rejected by MD5, URL,
    and filename checks; this threshold is only a cross-scene backstop for
    visually near-identical derivatives.
    """
    try:
        configured = int(
            os.environ.get("VIDZEO_WEB_CROSS_SCENE_PHASH_THRESHOLD", "6") or 6
        )
    except Exception:
        configured = 6
    return max(1, min(PERCEPTUAL_HASH_THRESHOLD, configured))


def _diversify_weak_scene_queries(queries: List[str], scene_text: str, script: str, title: str, subject: str, idx: int) -> List[str]:
    queries = [q for q in (queries or []) if q]
    weak = not queries or all(_query_content_word_count(q, subject) <= 1 for q in queries[:3])
    if not weak:
        return queries

    try:
        from .query import _topic_fallback_queries, _is_person_fallback_subject, _repair_query_pack
    except Exception:
        _topic_fallback_queries = None
        _is_person_fallback_subject = None
        _repair_query_pack = None

    variants = _extract_script_asset_variants(script, title, subject, limit=36)
    scene_kws = _extract_visual_keywords(scene_text or "", max_kws=6) if "_extract_visual_keywords" in globals() else []
    if subject and scene_kws:
        variants = [f"{subject} {' '.join(scene_kws[:2])}", f"{subject} {scene_kws[0]}"] + variants
    if subject and _topic_fallback_queries and not (_is_person_fallback_subject and _is_person_fallback_subject(subject)):
        variants = _topic_fallback_queries(subject, scene_text, limit=8) + variants

    if variants:
        rotated = variants[idx % len(variants):] + variants[:idx % len(variants)]
        queries = rotated[:5] + queries

    if _repair_query_pack:
        repaired = _repair_query_pack(
            queries,
            subject,
            scene_text,
            limit=8,
            context_text=script,
        )
        if repaired:
            return repaired

    seen = set()
    diversified = []
    for query in queries:
        cleaned = re.sub(r"\s+", " ", str(query or "")).strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            diversified.append(cleaned)
        if len(diversified) >= 8:
            break
    return diversified


def _is_weak_active_subject(subject: str, main_subject: str = "") -> bool:
    try:
        from .query import _is_weak_subject_phrase, _is_person_fallback_subject
    except Exception:
        _is_weak_subject_phrase = None
        _is_person_fallback_subject = None
    subject_clean = re.sub(r"\s+", " ", str(subject or "")).strip()
    if not subject_clean:
        return True
    try:
        from services.container_guard import is_weak_container_subject
        if is_weak_container_subject(subject_clean):
            return True
    except Exception:
        pass
    if main_subject and subject_clean.lower() == str(main_subject).lower():
        return False
    if _is_weak_subject_phrase and _is_weak_subject_phrase(subject_clean):
        return True
    if _is_person_fallback_subject and not _is_person_fallback_subject(subject_clean):
        words = re.findall(r"[a-z]+", subject_clean.lower())
        if len(words) <= 2 and any(w in {"iranian", "american", "canadian", "mexican", "fans", "team", "crowd"} for w in words):
            return True
    return False


_SHORT_KEEPERS = {"ai", "vr", "ar", "ui", "ux", "3d", "4k", "uk", "us", "f1", "gt", "rs", "v8", "w16", "f40", "m3", "amg", "gta", "ps5", "pc"}


def _forced_subject_rejected(forced_subject: str, main_subject: str, scene_text: str) -> bool:
    """Return True when a planner-row subject must not replace the topic anchor.

    Sentence fragments ('On April'), peripheral mentions ('Lookouts',
    'Wealthy'), and single generic words must not drive provider searches when
    a strong documentary anchor exists.  Aliases of the anchor ('Titanic' for
    'RMS Titanic') and multi-token entity names ('Harland Wolff',
    'Anushka Sharma', 'McLaren F1') are preserved.
    """
    if not main_subject or not forced_subject:
        return False
    if _is_weak_active_subject(forced_subject, main_subject):
        return True
    forced_terms = [
        token
        for token in re.findall(r"[a-z0-9]+", forced_subject.lower())
        if (len(token) >= 3 or token in _SHORT_KEEPERS) and token not in STOPWORDS
    ]
    main_terms = [
        token
        for token in re.findall(r"[a-z0-9]+", main_subject.lower())
        if (len(token) >= 3 or token in _SHORT_KEEPERS) and token not in STOPWORDS
    ]
    if main_terms and (set(forced_terms) & set(main_terms)):
        return False
    if len(forced_terms) < 2:
        return not _should_switch_active_subject(
            forced_subject, main_subject, scene_text
        )
    return False


def _should_switch_active_subject(candidate: str, main_subject: str, scene_text: str) -> bool:
    if _is_weak_active_subject(candidate, main_subject):
        return False
    if not main_subject:
        return True
    main_lower = re.sub(r"\s+", " ", str(main_subject or "").lower()).strip()
    cand_lower = re.sub(r"\s+", " ", str(candidate or "").lower()).strip()
    scene_lower = re.sub(r"\s+", " ", str(scene_text or "").lower()).strip()
    if cand_lower == main_lower:
        return False
    # Documentary/object anchors (ships, aircraft, vehicles, landmarks) must
    # not be replaced by peripheral mentions (a company name, a lookout, a
    # foreman) — the scene's visuals are still about the object itself.
    _VESSEL_ANCHOR_TERMS = {
        "rms", "hms", "uss", "u.s.s.", "mv", "ss", "titanic", "ship", "ships",
        "liner", "vessel", "ferry", "steamship", "airship", "submarine",
    }
    main_tokens = set(re.findall(r"[a-z0-9]+", main_lower)) | {main_lower}
    if main_tokens & _VESSEL_ANCHOR_TERMS:
        return False
    candidate_terms = [
        term
        for term in re.findall(r"[a-z0-9]+", cand_lower)
        if (len(term) >= 3 or term in _SHORT_KEEPERS) and term not in STOPWORDS
    ]
    if not candidate_terms:
        return False
    # A game's franchise/product identity is the retrieval anchor. Character
    # first names such as ``Jason`` or ``Lucia`` are too ambiguous to replace
    # ``Grand Theft Auto VI`` and otherwise drift into unrelated people.
    game_franchise_anchor = bool(re.search(
        r"\b(?:grand\s+theft\s+auto|gta|red\s+dead\s+redemption|call\s+of\s+duty|"
        r"assassin'?s\s+creed|playstation|xbox|nintendo)\b",
        main_lower,
    ))
    if game_franchise_anchor and len(candidate_terms) < 2:
        return False
    fragment_terms = {
        "born", "before", "after", "during", "current", "today", "yesterday",
        "january", "february", "march", "april", "june", "july", "august",
        "september", "october", "november", "december", "first", "second",
        "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
        "number", "scene", "video", "thing", "things",
    }
    if all(term in fragment_terms for term in candidate_terms):
        return False
    if any(re.search(r"\b" + re.escape(term) + r"\b", scene_lower) for term in candidate_terms if term not in fragment_terms):
        return True
    return bool(re.search(r"\b" + re.escape(cand_lower) + r"\b", scene_lower))




def _passes_web_relevance_floor(candidate: Dict, score: float, main_subject: str = "", scene_text: str = "") -> bool:
    title = str(candidate.get("title") or "")
    url = str(candidate.get("url") or candidate.get("source_url") or "")
    query = str(candidate.get("query_used") or candidate.get("query") or "")
    haystack = f"{title} {url}".lower()
    if _web_junk_metadata(title, url, ""):
        return False
    if candidate.get("semantic_visual_pending"):
        # Safety and explicit wrong-type metadata have already failed closed.
        # This row was intentionally admitted so the downloaded pixels—not a
        # terse provider caption—can decide identity. Do not repeat the same
        # lexical gate here and erase the visual-verification shortlist.
        return float(score) >= 0.0
    subject = str(main_subject or "").strip()
    subject_words = [w for w in re.findall(r"[a-z0-9]+", subject.lower()) if len(w) > 1]
    # Scene-subject anchor (GTA VI proof job): when the scene itself
    # narrates an earlier installment (scene 0: GTA V's sales and the
    # decade-long wait), imagery of that installment anchors the scene
    # even though it is not the main subject. The anchor never admits a
    # 2002 Vice City row.
    anchor = (
        _narrated_installment_anchor(scene_text, subject)
        if scene_text
        else None
    )
    anchor_allows = bool(anchor) and alternate_anchor_allows(anchor, haystack)
    if _is_legacy_installment_image(haystack):
        # 2002 Vice City imagery is admissible only for scenes actually
        # about that game (subject or narration names "Grand Theft Auto
        # Vice City"). In a newer-installment video it is never the right
        # still — not even for a scene set in the new Vice City, where
        # "vice" + "city" word matches would otherwise admit it.
        _legacy_subject = bool(
            re.search(r"\bgrand\s+theft\s+auto\b.{0,24}\bvice\s+city\b", subject.lower())
            or re.search(
                r"\bgrand\s+theft\s+auto\b.{0,24}\bvice\s+city\b",
                str(scene_text or "").lower(),
            )
        )
        if not _legacy_subject:
            return False
    if not subject_words:
        # Weak-but-topical fills (score just below zero) are allowed so hard
        # scenes resolve instead of forcing the job into the generic pool.
        return score >= -1.0

    generic_junk = {"sketch", "practice", "pads", "absorbing", "template", "sticker"}
    if any(re.search(r"\b" + re.escape(term) + r"\b", haystack) for term in generic_junk):
        return False

    subject_tokens = re.findall(r"[a-z0-9]+", subject.lower())
    version_aliases = {
        "ii": {"ii", "2"}, "iii": {"iii", "3"}, "iv": {"iv", "4"},
        "v": {"v", "5"}, "vi": {"vi", "6"}, "vii": {"vii", "7"},
        "viii": {"viii", "8"}, "ix": {"ix", "9"}, "x": {"x", "10"},
    }
    terminal = subject_tokens[-1] if subject_tokens else ""
    cand_ev = candidate.get("candidate_evidence") or {}
    nearest_pending = bool(cand_ev.get("nearest_match") or candidate.get("nearest_match_pending"))
    if (terminal.isdigit() or terminal in version_aliases) and not nearest_pending:
        required_versions = version_aliases.get(terminal, {terminal})
        if not any(re.search(r"\b" + re.escape(version) + r"\b", haystack) for version in required_versions) and not anchor_allows:
            return False

    if cand_ev.get("canonical_entity_mismatch") and not nearest_pending and not anchor_allows:
        return False

    if len(subject_words) >= 2:
        matched_words = [word for word in subject_words if re.search(r"\b" + re.escape(word) + r"\b", haystack)]
        subject_phrase = " ".join(subject_words)
        base_words = [w for w in subject_words if w not in version_aliases and not w.isdigit()]
        acronyms = []
        full_acronym = "".join(w[0] for w in subject_words if len(w) > 1)
        if len(full_acronym) >= 2:
            acronyms.append(full_acronym)
        if base_words and len(base_words) >= 2:
            base_acronym = "".join(w[0] for w in base_words if len(w) > 1)
            if len(base_acronym) >= 2:
                acronyms.append(base_acronym)
        phrase_match = bool(re.search(r"\b" + re.escape(subject_phrase) + r"\b", haystack))
        acronym_match = any(bool(re.search(r"\b" + re.escape(acr) + r"\b", haystack)) for acr in acronyms)
        role_terms = {
            "conductor", "conductors", "locomotive engineer", "locomotive engineers",
            "train driver", "train drivers", "railway worker", "railway workers",
            "railroad worker", "railroad workers", "dispatcher", "dispatchers",
        }
        has_role_match = any(re.search(r"\b" + re.escape(role) + r"\b", haystack) for role in role_terms) and any(
            term in subject.lower() for term in ("train", "rail", "railway", "railroad", "crew", "locomotive")
        )
        anchored = phrase_match or acronym_match or has_role_match or (len(matched_words) >= 2 and len(matched_words) >= int(len(subject_words) * 0.6))
    else:
        anchored = any(re.search(r"\b" + re.escape(word) + r"\b", haystack) for word in subject_words)

    if not anchored and not anchor_allows:
        return False

    landscape_markers = {
        "canyon", "canyons", "mountain", "mountains", "national park", "waterfall",
        "waterfalls", "glacier", "glaciers", "volcano", "desert", "hiking", "trekking",
    }
    subject_is_geo = any(term in subject.lower() for term in landscape_markers)
    if not subject_is_geo:
        candidate_geo = [term for term in landscape_markers if re.search(r"\b" + re.escape(term) + r"\b", haystack)]
        if candidate_geo and not (len(subject_words) >= 2 and " ".join(subject_words) in haystack):
            return False

    generic_junk = {"amazon"}
    if any(re.search(r"\b" + re.escape(term) + r"\b", haystack) for term in generic_junk):
        return False
    return score >= -1.0


def _download_and_accept_single_candidate(item: Dict, scene_dir: Path, idx: int,
    scene_text: str, identity_terms: Set[str], main_subject: str,
    global_phashes: Dict[int, int], global_md5s: Dict[str, int], used_files: Set[str],
    phash_lock: threading.Lock, md5_lock: threading.Lock, file_lock: threading.Lock,
    weak_terms: Optional[Set[str]] = None,
    strict_subject: bool = False) -> Optional[Dict]:
    """Download and accept one candidate only after the normal relevance floor."""
    strict_subject = bool(
        strict_subject
        or (identity_terms and set(identity_terms) - _WEB_CONTAINER_TERMS)
    )
    score_scene_text = (
        f"{scene_text} {main_subject}".strip()
        if strict_subject and main_subject
        else scene_text
    )
    url = item.get("url", "")
    if not url:
        return None
    safe, _reason = candidate_is_safe(item)
    if not safe:
        return None
    if _web_junk_metadata(item.get("title", ""), url, item.get("source_url", "")):
        return None
    if _is_biographical_person(main_subject) and not _metadata_has_identity(item.get("title", ""), url, "", identity_terms):
        return None
    download_headers = _provider_download_headers([item])
    dl_results = download_bulk(
        [url], scene_dir, max_concurrent=1, timeout_seconds=_web_image_download_timeout_seconds(),
        headers_by_url=download_headers,
    )
    downloaded_url = url
    usable_downloads = [
        (fpath, err)
        for _downloaded, fpath, err in dl_results
        if not err and fpath and fpath.exists()
    ]
    if not usable_downloads:
        preview_url = str(item.get("preview_url") or item.get("thumbnail_url") or "").strip()
        preview_host = urllib.parse.urlparse(preview_url).netloc.lower()
        preview_allowed = bool(
            preview_host
            and (
                is_seen_host(preview_host, load_seen_hosts())
                or _host_is_preferred(preview_host, _preferred_image_hosts())
            )
        )
        if (
            preview_allowed
            and preview_url.startswith(("https://", "http://"))
            and preview_url != url
        ):
            preview_results = download_bulk(
                [preview_url], scene_dir, max_concurrent=1, timeout_seconds=_web_image_download_timeout_seconds(),
                headers_by_url=download_headers,
            )
            usable_downloads = [
                (fpath, err)
                for _downloaded, fpath, err in preview_results
                if not err and fpath and fpath.exists()
            ]
            if usable_downloads:
                downloaded_url = preview_url
    if not usable_downloads:
        # Muse-search §3.4: a Wikimedia thumbnail that fails on
        # transport retries the full-res original once (same host gate).
        original_url = str(item.get("original_url") or "").strip()
        original_host = urllib.parse.urlparse(original_url).netloc.lower()
        original_allowed = bool(
            original_host
            and (
                is_seen_host(original_host, load_seen_hosts())
                or _host_is_preferred(original_host, _preferred_image_hosts())
            )
        )
        if (
            original_allowed
            and original_url.startswith(("https://", "http://"))
            and original_url != url
        ):
            original_results = download_bulk(
                [original_url], scene_dir, max_concurrent=1, timeout_seconds=_web_image_download_timeout_seconds(),
                headers_by_url=download_headers,
            )
            usable_downloads = [
                (fpath, err)
                for _downloaded, fpath, err in original_results
                if not err and fpath and fpath.exists()
            ]
            if usable_downloads:
                downloaded_url = original_url
    for fpath, err in usable_downloads:
        dims = _validate_image(fpath)
        if dims is None:
            continue
        safe, reason = image_is_safe(
            fpath,
            title=item.get("title", ""),
            url=url,
            tags=item.get("tags", ""),
            source=item.get("source", ""),
            description=item.get("description", ""),
            source_url=item.get("source_url", ""),
            page_url=item.get("page_url", ""),
            preview_url=item.get("preview_url", ""),
        )
        if not safe:
            _web_log(f"scene {idx + 1} blocked unsafe image ({reason})")
            continue
        w, h = dims
        if w / max(h, 1) < 0.5:
            continue
        md5 = _compute_md5(str(fpath))
        phash = _compute_average_hash(str(fpath))
        fname = Path(fpath).name
        with md5_lock:
            if md5 is not None:
                global_md5s[md5] = global_md5s.get(md5, 0) + 1
        with phash_lock:
            if phash is not None:
                global_phashes[phash] = global_phashes.get(phash, 0) + 1
        with file_lock:
            used_files.add(fname)
        score = _score_candidate(
            fpath,
            w,
            h,
            item.get("query", ""),
            score_scene_text,
            identity_terms,
            item.get("source", ""),
            item.get("title", ""),
            url,
            item.get("tags", ""),
            item.get("description", ""),
            item.get("source_url", ""),
            weak_terms=weak_terms,
            strict_subject=strict_subject,
        )
        candidate_for_gate = {
            **item,
            "path": str(fpath),
            "query_used": item.get("query", main_subject),
            "download_url_used": downloaded_url,
        }
        if score < 0.0 or not _passes_web_relevance_floor(candidate_for_gate, score, main_subject, scene_text):
            try:
                fpath.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        return {
            "path": str(fpath), "width": w, "height": h,
            "url": url, "domain": item.get("domain") or item.get("source", "flickr_api"),
            "download_url_used": downloaded_url,
            "relevance": score, "final_score": score, "reused_from_scene": False,
            "title": item.get("title", "") or f"scene_{idx + 1}",
            "source_url": item.get("source_url") or item.get("page_url") or url,
            "page_url": item.get("page_url") or item.get("source_url") or url,
            "preview_url": item.get("preview_url") or item.get("thumbnail_url") or "",
            "provider": item.get("provider") or item.get("source", ""),
            "license": item.get("license", ""),
            "license_url": item.get("license_url", ""),
            "attribution": item.get("attribution", ""),
            "creator": item.get("creator", ""),
            "query_used": item.get("query", main_subject),
            "tags": item.get("tags", ""),
            "description": item.get("description", ""),
        }
    return None


# Hosts the pipeline has already fetched images from in earlier jobs, so a
# fetch from one of them does not pop a new per-host network approval card.
# Proven by the running BNSF pool (51/51 candidates resolved on 2026-11-10):
# i.ytimg.com, live.staticflickr.com, upload.wikimedia.org, i.pinimg.com,
# pinterest.com, youtube.com, flickr.com, rrpicturearchives.net. Override
# (comma-separated) with VIDZEO_PREFERRED_IMAGE_HOSTS; an empty value
# disables the preference entirely.
_DEFAULT_PREFERRED_IMAGE_HOSTS = (
    "upload.wikimedia.org",
    "live.staticflickr.com",
    "i.ytimg.com",
    "i.pinimg.com",
    "mm.bing.net",
    "gstatic.com",
    "yandex.net",
    "yandex.com",
    "duckduckgo.com",
    "pinterest.com",
    "www.pinterest.com",
    "flickr.com",
    "youtube.com",
    "rrpicturearchives.net",
)


def _preferred_image_hosts():
    """Hosts whose fetches are already approved; env override, else default."""
    raw = os.environ.get("VIDZEO_PREFERRED_IMAGE_HOSTS")
    if raw is None:
        return set(_DEFAULT_PREFERRED_IMAGE_HOSTS)
    return {host.strip().lower() for host in raw.split(",") if host.strip()}


def _host_is_preferred(host, preferred):
    host = (host or "").lower()
    return any(host == item or host.endswith("." + item) for item in preferred)


def _diverse_download_candidates(scored, limit, preferred_hosts=None):
    """Diversify similarly relevant rows without promoting weak host results.

    Among hosts within the relevance slack, prefer hosts the pipeline has
    already fetched from: each new host costs a per-host network approval
    card and can stall media search, so a near-equal candidate from a
    known host wins over a slightly better one from an unseen host. A
    preferred host never overrides a real relevance gap — it must still be
    within slack of the best candidate — and the alternate-host reserve
    below is kept.

    Diversity is measured by PUBLISHER/source host, not by image CDN
    host (GTA VI proof job a57cb5c7: all 5 web images came from
    Pinterest — ``i.pinimg.com`` image URLs behind ``br.pinterest.com``
    pin pages — while the round-robin still saw "one host" and had
    nothing to swap in). Grouping by source host makes ``i.pinimg.com``
    and ``pinimg.com`` variants one source, so the reserve below can
    actually fire. When the eligible set holds other sources, keep a
    diversity reserve of up to a quarter of the shortlist for them even
    when they sit outside the relevance slack: they still passed the
    metadata floor, and vision — not the pre-score — decides whether
    their pixels fit the scene. Without the reserve, a
    Pinterest-dominated query fills every download slot with pins and
    the scene can never pick a distinct non-Pinterest image.
    """
    preferred = (
        _preferred_image_hosts()
        if preferred_hosts is None
        else {str(host).strip().lower() for host in preferred_hosts if str(host).strip()}
    )

    def _source_key(item):
        source = _distinct_source_host(item) if isinstance(item, Mapping) else ""
        return source or urllib.parse.urlparse(str(item.get("url") or "")).netloc.lower()

    def _image_host(item):
        return urllib.parse.urlparse(str(item.get("url") or "")).netloc.lower()

    def _is_preferred_source(item):
        return (
            _host_is_preferred(_source_key(item), preferred)
            or _host_is_preferred(_image_host(item), preferred)
        )

    groups = {}
    for score, item in scored:
        groups.setdefault(_source_key(item), []).append((float(score), item))
    for rows in groups.values():
        rows.sort(key=lambda pair: pair[0], reverse=True)
    selected = []
    uses = {}
    while groups and len(selected) < limit:
        best = max(rows[0][0] for rows in groups.values())
        slack = max(.25, abs(best) * .1)
        eligible = [host for host, rows in groups.items() if rows[0][0] >= best - slack]
        host = min(
            eligible,
            key=lambda key: (
                0 if _is_preferred_source(groups[key][0][1]) else 1,
                uses.get(key, 0),
                -groups[key][0][0],
                key,
            ),
        )
        selected.append(groups[host].pop(0))
        uses[host] = uses.get(host, 0) + 1
        if not groups[host]:
            del groups[host]
    # One failed host must not consume the entire transfer allowance. Keep
    # a diversity reserve from other sources even when their metadata is
    # weaker: without it a single-source query (Pinterest pins for a
    # game-key-art search) fills every slot with one publisher and the
    # scene's pool can never diversify. Reserve up to a quarter of the
    # shortlist; the replaced rows are the lowest-scoring selected ones.
    selected_hosts = {_source_key(item) for _score, item in selected}
    reserves = [
        (rows[0][0], host)
        for host, rows in groups.items()
        if host not in selected_hosts
    ]
    if len(selected) > 1 and len(selected_hosts) == 1 and reserves:
        reserve_count = max(1, min(len(reserves), int(limit) // 4 or 1))
        reserves.sort(key=lambda pair: (_is_preferred_source(groups[pair[1]][0][1]), pair[0]), reverse=True)
        for _score, host in reserves[:reserve_count]:
            replacement = groups[host].pop(0)
            # Replace the lowest-scoring selected row, never the best.
            worst_index = min(range(len(selected)), key=lambda i: selected[i][0])
            selected[worst_index] = replacement
            if not groups[host]:
                del groups[host]
    return selected


def _vision_stall_seconds() -> float:
    """Seconds with no visual verdict before the gate retries/escalates.

    Round 14. Env VIDZEO_VISION_STALL_SECONDS (default 300; 0 disables
    the watchdog). With an unbounded vision budget the visual-gate
    join below would otherwise wait forever on a lost verdict.
    """
    try:
        return max(0.0, float(os.environ.get("VIDZEO_VISION_STALL_SECONDS", "300") or 300))
    except (TypeError, ValueError):
        return 300.0


def _web_stall_zero_chunks() -> int:
    """Consecutive zero-land chunks before a batch stops feeding dead hosts.

    Round 14. Env VIDZEO_WEB_STALL_ZERO_CHUNKS (default 2; 0 disables
    the skip and restores the old behaviour of retrying every URL).
    """
    try:
        return max(0, int(os.environ.get("VIDZEO_WEB_STALL_ZERO_CHUNKS", "2") or 2))
    except (TypeError, ValueError):
        return 2


def _is_connection_error(error: str) -> bool:
    """True when a download error means the host could not be reached at all.

    Narrower than a timeout: a slow host still transfers, a dead host
    never connects. Only connection failures mark a host dead for the
    round-14 stall skip — a read timeout on an otherwise live CDN must
    not blacklist it.
    """
    lowered = (error or "").lower()
    return any(
        marker in lowered
        for marker in (
            "cannot connect",
            "connection refused",
            "connection reset",
            "connection aborted",
            "name or service not known",
            "temporary failure in name resolution",
            "network is unreachable",
            "no route to host",
            "ssl",
            "certificate",
        )
    )


def _url_host(url: str) -> str:
    try:
        return (urllib.parse.urlparse(str(url or "")).hostname or "").lower()
    except Exception:
        return ""


def _web_download_chunk_size() -> int:
    """Max URLs per download_bulk sub-batch (muse-search design §3.2).

    Test 30: one shared 90 s batch wall covered every URL in the batch
    and both attempts of each, so later URLs died as
    batch-budget-exhausted before starting. Splitting into sub-batches
    of 6 gives each chunk its own wall (the wall scales with the number
    of URLs, not the timeout). Env VIDZEO_WEB_DOWNLOAD_CHUNK_SIZE.
    """
    try:
        return max(
            1,
            min(12, int(os.environ.get("VIDZEO_WEB_DOWNLOAD_CHUNK_SIZE", "6") or 6)),
        )
    except (TypeError, ValueError):
        return 6


def _download_bulk_chunked(
    urls: List[str],
    output_dir: Path,
    max_concurrent: int,
    timeout_seconds: float,
    headers_by_url: Optional[Dict[str, Dict[str, str]]] = None,
    download_stats: Optional[List[Dict]] = None,
    wait_until_complete: bool = False,
) -> List[Tuple[str, Optional[Path], str]]:
    """download_bulk in sub-batches of at most _web_download_chunk_size().

    Each chunk gets its own download_bulk call and its own batch wall,
    so a slow first chunk cannot starve later URLs of their transfer
    budget. Total wall per scene stays bounded by the scene download
    budget and the stage deadline.

    Round 13: ``wait_until_complete`` removes the per-chunk batch wall
    and the slot-wait cap (see hybrid_search.download_bulk) — every
    URL runs to resolution on its per-attempt socket budget.
    """
    urls = list(urls or [])
    if not urls:
        return []
    chunk_size = _web_download_chunk_size()
    stall_zero_chunks = _web_stall_zero_chunks()
    out: List[Tuple[str, Optional[Path], str]] = []
    consecutive_zero = 0
    dead_hosts: Set[str] = set()
    stall_logged = False
    batch_started = time.monotonic()
    for start in range(0, len(urls), chunk_size):
        chunk = urls[start:start + chunk_size]
        if stall_zero_chunks > 0 and consecutive_zero >= stall_zero_chunks and dead_hosts:
            # Round 14: after N consecutive zero-land chunks, stop
            # feeding the same dead hosts. The wave ends on the
            # remaining URLs and the caller moves to fresh candidates
            # instead of burning ~400 s per chunk on hosts that already
            # failed to connect in this batch (job 66b52291: chunk after
            # chunk landed 0 on i.pinimg.com / washingtonpost /
            # gtaboom while "Preparing media 3/8" sat for 2389 s).
            live_chunk = [u for u in chunk if _url_host(u) not in dead_hosts]
            skipped = len(chunk) - len(live_chunk)
            if skipped:
                _web_log(
                    f"[web-stall] skipping {skipped}/{len(chunk)} urls on dead hosts "
                    f"({', '.join(sorted(dead_hosts)[:5])}) after {consecutive_zero} zero-land chunks"
                )
            chunk = live_chunk
            if not chunk:
                continue
        chunk_started = time.monotonic()
        # Pass stats= only when the caller asked for timing records:
        # older download_bulk doubles take no stats keyword. The same
        # applies to wait_until_complete (round 13): only pass it when
        # on, so the default path keeps the exact old call shape.
        chunk_kwargs: Dict[str, Any] = {}
        if download_stats is not None:
            chunk_kwargs["stats"] = download_stats
        if wait_until_complete:
            chunk_kwargs["wait_until_complete"] = True
        chunk_results = download_bulk(
            chunk, output_dir,
            max_concurrent=min(max_concurrent, len(chunk)),
            timeout_seconds=timeout_seconds,
            headers_by_url=headers_by_url,
            **chunk_kwargs,
        )
        landed = sum(
            1 for _u, _p, _e in chunk_results if _p and Path(_p).is_file()
        )
        # Round 6 (test 33): a bare "landed 0" hides whether downloads died
        # on timeouts, slot waits, or refusals — the next forensics pass
        # needed a live probe to learn it. Report the error breakdown.
        failed_errors = Counter(
            str(_e or "download failed")[:48]
            for _u, _p, _e in chunk_results
            if not (_p and Path(_p).is_file())
        )
        errors_suffix = (
            f" errors={dict(failed_errors)}" if failed_errors else ""
        )
        _web_log(
            f"download chunk: {len(chunk)} urls, landed {landed} "
            f"in {time.monotonic() - chunk_started:.1f}s{errors_suffix}"
        )
        if landed:
            consecutive_zero = 0
        else:
            consecutive_zero += 1
            for _u, _p, _e in chunk_results:
                if not (_p and Path(_p).is_file()) and _is_connection_error(str(_e or "")):
                    host = _url_host(_u)
                    if host:
                        dead_hosts.add(host)
            if (
                stall_zero_chunks > 0
                and consecutive_zero >= stall_zero_chunks
                and dead_hosts
                and not stall_logged
            ):
                stall_logged = True
                _web_log(
                    f"[web-stall] {consecutive_zero} consecutive zero-land chunks "
                    f"in {time.monotonic() - batch_started:.0f}s; dead hosts: "
                    f"{', '.join(sorted(dead_hosts)[:8])} — remaining urls on those hosts will be skipped"
                )
        out.extend(chunk_results)
    return out


def _download_bulk_chunked_tolerant(
    urls: List[str],
    output_dir: Path,
    max_concurrent: int,
    timeout_seconds: float,
    headers_by_url: Optional[Dict[str, Dict[str, str]]] = None,
    download_stats: Optional[List[Dict]] = None,
    wait_until_complete: bool = False,
) -> List[Tuple[str, Optional[Path], str]]:
    """_download_bulk_chunked, tolerating download_bulk doubles without stats.

    Older test doubles for download_bulk take no ``stats`` keyword; a
    TypeError naming it retries the chunks without stats instead of
    failing the scene. The same applies to the round-13
    ``wait_until_complete`` keyword: a double predating it retries
    without it (the batch wall then applies to that double).
    """
    try:
        return _download_bulk_chunked(
            urls, output_dir, max_concurrent, timeout_seconds,
            headers_by_url=headers_by_url, download_stats=download_stats,
            wait_until_complete=wait_until_complete,
        )
    except TypeError as exc:
        if download_stats is not None and "stats" in str(exc):
            return _download_bulk_chunked(
                urls, output_dir, max_concurrent, timeout_seconds,
                headers_by_url=headers_by_url, download_stats=None,
                wait_until_complete=wait_until_complete,
            )
        if wait_until_complete and "wait_until_complete" in str(exc):
            return _download_bulk_chunked(
                urls, output_dir, max_concurrent, timeout_seconds,
                headers_by_url=headers_by_url, download_stats=download_stats,
                wait_until_complete=False,
            )
        raise


def _download_candidate_batch_tolerant(
    items, output_dir, download_stats=None, **kwargs
) -> List[Tuple[str, Optional[Path], str]]:
    """_download_candidate_batch, tolerating doubles without download_stats.

    Test fakes for _download_candidate_batch predate the download_stats
    kwarg (muse-search §4.2); a TypeError naming it retries without it
    instead of failing the scene. The same applies to the round-13
    ``wait_until_complete`` kwarg: a double predating it retries
    without it (the batch wall then applies to that double).
    """
    try:
        return _download_candidate_batch(
            items, output_dir, download_stats=download_stats, **kwargs
        )
    except TypeError as exc:
        if "download_stats" in str(exc) or "wait_until_complete" in str(exc):
            # A double predating the newer kwargs retries without them.
            kwargs.pop("wait_until_complete", None)
            return _download_candidate_batch(items, output_dir, **kwargs)
        raise


def _image_download_transport_failed(error: str) -> bool:
    text = str(error or "").strip().lower()
    return text == "timeout" or any(marker in text for marker in (
        "connection error", "cannot connect to host", "name resolution",
        "getaddrinfo failed", "name or service not known", "no route to host",
        "network is unreachable", "host is unreachable", "connection refused",
        "connection reset", "connect timeout", "read timed out",
    ))


def _download_batch_with_outage_recovery(
    items, output_dir, *, outage_tracker, cancel_event, deadline, **kwargs,
) -> List[Tuple[str, Optional[Path], str]]:
    if outage_tracker is None or not outage_tracker.enabled:
        return _download_candidate_batch_tolerant(items, output_dir, **kwargs)

    def within_deadline():
        limit = deadline()
        return (cancel_event is None or not cancel_event.is_set()) and (
            limit is None or time.monotonic() < limit
        )

    outage_tracker.wait_if_outage(cancel_event=cancel_event)
    if not within_deadline():
        return []
    results = _download_candidate_batch_tolerant(items, output_dir, **kwargs)
    failed_urls = {url for url, path, error in results if not path and _image_download_transport_failed(error)}
    hosts = {urllib.parse.urlsplit(url).hostname for url in failed_urls} - {None}
    downloaded = sum(bool(path) for _, path, _ in results)
    outage_tracker.note_outcome(
        transport_failed=bool(hosts), rows=downloaded, download_hosts=hosts,
    )
    if downloaded or not failed_urls or not outage_tracker.wait_if_outage(cancel_event=cancel_event):
        return results
    if not within_deadline():
        return results
    # Reuse this scene's ranked candidates after the network wait. The
    # download cache can reuse a file recovered by a neighboring scene.
    retry_items = [item for item in items if item["url"] in failed_urls]
    limit = deadline()
    if limit is not None:
        kwargs["timeout_seconds"] = min(kwargs["timeout_seconds"], max(0.1, limit - time.monotonic()))
    retried = {row[0]: row for row in _download_candidate_batch_tolerant(retry_items, output_dir, **kwargs)}
    if any(path for _, path, _ in retried.values()):
        outage_tracker.note_outcome(transport_failed=False, rows=1, download_hosts=set())
    return [retried.get(row[0], row) for row in results]


def _is_definitive_download_error(error: str) -> bool:
    """True when a download failure proves the URL itself is unusable.

    Read-side classifier for the shared download cache (round 6). Every
    failure is recorded in the cache with the real error, but only a
    definitive failure cools the URL down for 30 s. A transport failure
    — timeout, slot wait, batch-budget exhaustion, connection error —
    says nothing about the URL: in test 33 the warm-pool prefetch failed
    all 15 shared-pool URLs on a 5 s budget while the egress needed
    ~16-18 s per transfer, and the failure markers then made every scene
    worker's first pass return 'cached_download_failure' without ever
    attempting the URL. So a transport-failure marker is retryable at
    once by the next caller. Refusals and content rejections (403/404/
    410/429, not-an-image, too small/large, invalid URL) are definitive:
    retrying them in the same job only hammers the host.
    """
    text = str(error or "").strip().lower()
    if not text:
        return False
    if text.startswith("http 403") or text.startswith("http 404"):
        return True
    if text.startswith("http 410") or text.startswith("http 429"):
        return True
    if text.startswith("not image:") or text.startswith("too small"):
        return True
    if text.startswith("too large") or text.startswith("invalid url"):
        return True
    return False


def _download_candidate_batch(items, output_dir, max_concurrent, timeout_seconds, download_cache=None, download_cache_lock=None, download_stats=None, wait_until_complete: bool = False):
    # Shared provider pools intentionally overlap across scenes. Reuse a
    # successful job-local URL fetch and never stampede the same CDN URL.
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    cache = download_cache if download_cache is not None else {}
    cache_lock = download_cache_lock or threading.Lock()
    ready_results = []
    fetch_items = []
    pending_items = []
    # A string owner token distinguishes an active fetch from a genuine
    # cached path.  The previous ``None`` sentinel made every sibling wait the
    # full timeout and then drop the URL, even when the original fetch was only
    # slightly slow.
    inflight_markers: Dict[str, str] = {}
    marker_prefix = "__vidzeo_inflight__"
    failed_prefix = '__vidzeo_failed__'
    for item in items:
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        supplied_path = Path(str(item.get("path") or "").strip())
        if supplied_path.is_file():
            try:
                ext = supplied_path.suffix or ".jpg"
                dest = Path(output_dir) / (
                    "pool_" + hashlib.sha1(url.encode("utf-8", "ignore")).hexdigest()[:14] + ext
                )
                if str(supplied_path.resolve()) != str(dest.resolve()) and not dest.exists():
                    shutil.copy2(str(supplied_path), str(dest))
                ready_results.append((url, dest if dest.is_file() else supplied_path, ""))
                continue
            except Exception:
                pass
        cached_path = None
        in_flight = False
        inflight_marker = ""
        owner_marker = ""
        with cache_lock:
            if url in cache:
                value = cache.get(url)
                value_text = str(value or "")
                if value_text.startswith(failed_prefix):
                    # A definitive failure (403/404/429, not-an-image, ...)
                    # cools down for 30 s. A transport failure (timeout,
                    # slot wait, connection error) is recorded with the
                    # real error but never blocks a retry: the next caller
                    # takes ownership and attempts the URL again. (Round 6:
                    # the prefetch used to poison the cache with 5 s-budget
                    # timeouts and every scene worker then returned
                    # 'cached_download_failure' without trying.)
                    try:
                        failed_parts = value_text[len(failed_prefix):].split(':', 1)
                        failed_at = float(failed_parts[0])
                        failed_error = failed_parts[1] if len(failed_parts) > 1 else ""
                    except (ValueError, IndexError):
                        failed_at = 0
                        failed_error = ""
                    if (
                        _is_definitive_download_error(failed_error)
                        and time.monotonic() - failed_at < 30
                    ):
                        ready_results.append((url, None, 'cached_download_failure'))
                        continue
                    owner_marker = f"{marker_prefix}{threading.get_ident()}_{time.monotonic_ns()}"
                    cache[url] = owner_marker
                    inflight_markers[url] = owner_marker
                elif value_text.startswith(marker_prefix):
                    in_flight = True
                    inflight_marker = value_text
                elif value:
                    cached_path = Path(value_text)
            else:
                owner_marker = f"{marker_prefix}{threading.get_ident()}_{time.monotonic_ns()}"
                cache[url] = owner_marker
                inflight_markers[url] = owner_marker
        if cached_path is not None and cached_path.is_file():
            try:
                ext = cached_path.suffix or ".jpg"
                dest = Path(output_dir) / (
                    "cached_" + hashlib.sha1(url.encode("utf-8", "ignore")).hexdigest()[:14] + ext
                )
                if not dest.exists():
                    shutil.copy2(str(cached_path), str(dest))
                ready_results.append((url, dest, ""))
                continue
            except Exception:
                with cache_lock:
                    cache.pop(url, None)
        if in_flight:
            # Collect siblings first. Waiting per URL serially used to spend
            # eight seconds *per candidate* before starting independent work.
            pending_items.append(item)
            continue
        fetch_items.append(item)

    def collect_pending():
        pending = {str(item.get('url') or ''): item for item in pending_items}
        # URLs whose owner published a TRANSPORT failure (timeout, slot
        # wait, connection error) are re-queued for the leftover direct
        # download below instead of dying as 'cached_download_failure':
        # the failure is recorded, but the URL itself was never proven
        # unusable. Definitive failures (403/404/429, not-an-image, ...)
        # still short-circuit on the 30 s cooldown.
        transport_retry_items = {}
        # Test 29: the wait was capped at 8s while an owner needs a slot
        # wait plus a full transfer (up to ~2x the per-image timeout), so
        # every slow-hour sibling timed out of the wait and died as
        # 'inflight_cached' even though the image was fetchable. Wait the
        # full per-image timeout instead.
        wait_until = time.monotonic() + max(0.5, float(timeout_seconds or 8.0))
        while pending:
            for url in list(pending):
                with cache_lock:
                    value = cache.get(url)
                value_text = str(value or '')
                if value_text.startswith(failed_prefix):
                    failed_parts = value_text[len(failed_prefix):].split(':', 1)
                    failed_error = failed_parts[1] if len(failed_parts) > 1 else ""
                    if _is_definitive_download_error(failed_error):
                        ready_results.append((url, None, 'cached_download_failure'))
                        pending.pop(url)
                    else:
                        transport_retry_items[url] = pending.pop(url)
                    continue
                if value_text and not value_text.startswith(marker_prefix):
                    cached_path = Path(value_text)
                    if cached_path.is_file():
                        try:
                            dest = Path(output_dir) / (
                                'cached_' + hashlib.sha1(url.encode('utf-8', 'ignore')).hexdigest()[:14]
                                + (cached_path.suffix or '.jpg'))
                            if not dest.exists():
                                shutil.copy2(str(cached_path), str(dest))
                            ready_results.append((url, dest, ''))
                            pending.pop(url)
                            continue
                        except OSError:
                            pass
                if not value:
                    ready_results.append((url, None, 'shared_download_failed'))
                    pending.pop(url)
            if not pending or time.monotonic() >= wait_until:
                break
            time.sleep(0.05)
        # A URL whose owner is STILL in flight after a full per-image
        # timeout is not resolved — but dropping it here killed 36
        # candidates in test 29 (scene 8 lost all 18 this way). Re-queue
        # the leftovers for a direct download in this call instead:
        # claim ownership (overwriting the stale marker, as the main
        # path's takeover rule already allows) and fetch them. Only a
        # failed re-download records a failure — with the real error,
        # never 'inflight_cached'. Transport-failure URLs whose owner
        # already published a marker ride along the same path.
        if pending or transport_retry_items:
            leftover_items = [pending[url] for url in list(pending)] + list(
                transport_retry_items.values()
            )
            leftover_urls = [str(item.get('url') or '') for item in leftover_items]
            for url in leftover_urls:
                with cache_lock:
                    marker = f"{marker_prefix}{threading.get_ident()}_{time.monotonic_ns()}"
                    cache[url] = marker
                    inflight_markers[url] = marker
            try:
                leftover_results = _download_bulk_chunked_tolerant(
                    leftover_urls, output_dir,
                    max_concurrent=min(max_concurrent, len(leftover_urls)),
                    timeout_seconds=timeout_seconds,
                    headers_by_url=_provider_download_headers(leftover_items),
                    download_stats=download_stats,
                    wait_until_complete=wait_until_complete,
                )
            except Exception:
                with cache_lock:
                    for url in leftover_urls:
                        marker = inflight_markers.get(url)
                        if marker and cache.get(url) == marker:
                            cache.pop(url, None)
                leftover_results = [
                    (url, None, "shared_download_failed") for url in leftover_urls
                ]
            for url, path, error in leftover_results:
                with cache_lock:
                    marker = inflight_markers.get(url)
                    current = str(cache.get(url) or "")
                    if marker and current != marker:
                        # A takeover has already published a newer state.
                        continue
                    if path and Path(path).is_file():
                        cache[url] = str(path)
                    else:
                        # Every failure is recorded with the real error, so
                        # the scene's failure summary stays honest (the
                        # leftover-failure test pins this). The READ side
                        # decides what the marker means: a definitive
                        # failure cools down for 30 s, a transport failure
                        # is retryable at once — so recording it no longer
                        # poisons the next caller (round 6).
                        cache[url] = (
                            failed_prefix + str(time.monotonic()) + ':'
                            + str(error or 'download_failed')[:80]
                        )
                if path and Path(path).is_file():
                    ready_results.append((url, path, ""))
                else:
                    ready_results.append((url, None, error or "download failed"))
            for url in leftover_urls:
                pending.pop(url, None)

    if not fetch_items:
        collect_pending()
        return ready_results
    headers = _provider_download_headers(items)
    try:
        results = _download_bulk_chunked_tolerant(
            [item["url"] for item in fetch_items], output_dir,
            max_concurrent=max_concurrent, timeout_seconds=timeout_seconds,
            headers_by_url=headers, download_stats=download_stats,
            wait_until_complete=wait_until_complete,
        )
    except Exception:
        # A transport exception must not leave permanent ownership markers.
        with cache_lock:
            for url, marker in inflight_markers.items():
                if cache.get(url) == marker:
                    cache.pop(url, None)
        raise
    by_url = {item["url"]: item for item in fetch_items}
    for url, path, _error in results:
        with cache_lock:
            marker = inflight_markers.get(url)
            current = str(cache.get(url) or "")
            if marker and current != marker:
                # A takeover has already published a newer state.
                continue
            if path and Path(path).is_file():
                cache[url] = str(path)
            # Retain ownership while its preview rescue is still pending.
    # A publisher may deny its original URL while the search provider's
    # preview remains accessible. The normal image validation and visual gate
    # still decide whether the recovered pixels are usable for this scene.
    fallback = {}
    preview_seen_hosts = load_seen_hosts()
    preview_preferred_hosts = _preferred_image_hosts()
    for url, path, error in results:
        item = by_url.get(url, {})
        if path:
            continue
        preview = str(item.get("preview_url") or item.get("thumbnail_url") or "").strip()
        if preview.startswith(("https://", "http://")) and preview != url:
            _preview_host = urllib.parse.urlparse(preview).netloc.lower()
            if _preview_host and (
                is_seen_host(_preview_host, preview_seen_hosts)
                or _host_is_preferred(_preview_host, preview_preferred_hosts)
            ):
                fallback[url] = preview
                continue
        # Muse-search §3.4: a Wikimedia thumbnail that fails on transport
        # retries the full-res original once (same host, same gate).
        original = str(item.get("original_url") or "").strip()
        if original.startswith(("https://", "http://")) and original != url:
            _original_host = urllib.parse.urlparse(original).netloc.lower()
            if _original_host and (
                is_seen_host(_original_host, preview_seen_hosts)
                or _host_is_preferred(_original_host, preview_preferred_hosts)
            ):
                fallback[url] = original
    if fallback:
        try:
            previews = _download_bulk_chunked_tolerant(
                list(dict.fromkeys(fallback.values())), output_dir,
                max_concurrent=max_concurrent, timeout_seconds=timeout_seconds,
                headers_by_url=headers, download_stats=download_stats,
                wait_until_complete=wait_until_complete,
            )
        except Exception as exc:
            _web_log(f"preview recovery failed: {type(exc).__name__}: {str(exc)[:160]}")
            previews = []
        preview_paths = {url: path for url, path, error in previews if path}
        recovered = []
        for url, path, error in results:
            if not path and fallback.get(url) in preview_paths:
                path = preview_paths[fallback[url]]
                by_url[url]["download_url_used"] = fallback[url]
                error = ""
            recovered.append((url, path, error))
        results = recovered
        for url, path, _error in results:
            if path and Path(path).is_file():
                with cache_lock:
                    marker = inflight_markers.get(url)
                    current = str(cache.get(url) or "")
                    if not marker or current == marker:
                        cache[url] = str(path)
    for url, path, error in results:
        if path and Path(path).is_file():
            continue
        with cache_lock:
            marker = inflight_markers.get(url)
            if marker and cache.get(url) == marker:
                # Every failure is recorded with the real error (see the
                # leftover write site above). The read side treats a
                # definitive failure as a 30 s cooldown and a transport
                # failure as retryable at once.
                cache[url] = failed_prefix + str(time.monotonic()) + ':' + str(error or 'download_failed')[:80]
    collect_pending()
    # Learn the hosts this batch actually downloaded from, so the next job
    # reuses them instead of popping a fresh per-host approval card. Only
    # successful downloads count (a host in search results is not a fetch).
    # Best-effort: a store failure must never fail the batch.
    try:
        _downloaded_hosts = {
            urllib.parse.urlparse(str(url)).netloc.lower()
            for url, path, _error in list(ready_results) + list(results)
            if path and Path(path).is_file() and str(url).startswith(("http://", "https://"))
        }
        _downloaded_hosts.discard("")
        if _downloaded_hosts:
            record_hosts(_downloaded_hosts, source="job")
    except Exception:
        pass
    return ready_results + results


def _preserve_downloaded_shortlist(winner, shortlist, candidate_pool_size, downloaded_count):
    """Keep downloaded alternates when streaming vision returns a new winner row."""
    result = dict(winner) if isinstance(winner, dict) else {}
    merged = []
    seen = set()
    for candidate in [result, *(shortlist or []), *(result.get('_vision_shortlist') or [])]:
        if not isinstance(candidate, dict):
            continue
        path = str(candidate.get('path') or '').strip()
        if not path or path in seen:
            continue
        seen.add(path)
        merged.append(dict(candidate))
        if len(merged) >= _vision_shortlist_limit():
            break
    if merged:
        result['_vision_shortlist'] = merged
    result['candidate_pool_size'] = max(
        int(result.get('candidate_pool_size') or 0), int(candidate_pool_size or 0), len(merged)
    )
    result['downloaded_candidate_count'] = max(
        int(result.get('downloaded_candidate_count') or 0), int(downloaded_count or 0), len(merged)
    )
    return result


def _artifact_safety_may_defer(item, requirement, reason, external_visual_gate):
    """Keep a named warrant artifact pending a real pixel check, never accept it."""
    if not external_visual_gate or reason not in {'high_exposure_visual', 'placeholder_text_card'}:
        return False
    if not isinstance(requirement, Mapping) or not requirement.get('strict_identity'):
        return False
    if requirement.get('subject_type') != 'organization' or not requirement.get('requirement_hash'):
        return False
    if not candidate_is_safe(item)[0]:
        return False
    # Gold/wood plaques and white certificate pages can trip generic skin
    # and text-card heuristics. A source title must identify the actual artifact;
    # a query echo alone cannot grant this pending handoff.
    canonical = str(requirement.get('canonical_entity') or '').casefold()
    evidence = candidate_asset_metadata(item).casefold()
    return bool(re.search(r'\broyal\s+warrants?\b', canonical)
                and re.search(r'\broyal\s+warrants?\b', evidence))


def _web_image_download_timeout_seconds(fast_mode: bool = False) -> float:
    """Per-image download timeout (seconds) for the web lane.

    Default 120s (fast mode 60s), env-overridable via
    VIDZEO_WEB_IMAGE_DOWNLOAD_TIMEOUT_SECONDS, clamped to 3-300s.

    Nov 22, test 23: the old 15s default timed out 50/118 download
    attempts in a slow egress hour, while search pages on the same
    egress needed 31-118s to answer.  A healthy-hour image download
    takes ~5s; 30s (fix 6f2b961) helped, but the user ordered the
    timeout raised further for connection issues: 60s gives the
    slow-hour tail a real chance while the per-URL and batch
    deadlines still bound a dead host.  Clamp max 120s so the env
    override can go further without another code change.

    Nov 22, test 29: the fast-mode default was 8s — and the live V2
    path runs fast mode, so every web image got an 8s transfer budget
    inside a 24s batch wall (3x timeout). 36 of 85 download attempts
    died as "timeout" and 8 more as batch-budget-exhausted; 0 of 190
    mapped candidates were ever downloaded, so web came back 0 even
    though search returned rows. 8s is below the ~8s a slow-hour
    image transfer alone needs (test 25), before any slot wait.
    Fast mode now defaults to the same proven 30s; the batch wall
    becomes 90s. The env override still wins, so a slower hour can
    go to 60s without a code change.

    Nov 23, test 35 (round 8): 60s stopped being enough. The median
    transfer in that hour was 61.0s — censored at the 60s line, so the
    true need is above 60s — and 25 of 26 chunks landed 0 (~150 URLs,
    2 landed). The slow-hour transfer bracket is ~60-120s (test 34's
    hour landed transfers inside 60s; test 35's hour needs more), so
    the default doubles to 120s (fast mode 60s, the old non-fast
    default). 120s is the largest budget that still leaves room for
    the second route inside the 180s batch wall (120s + 60s); a fully
    degraded hour needing >120s per transfer is still out of reach,
    and the clamp max rises to 300s so the env override can go
    further without another code change. The batch wall and the
    stage deadline still bound a dead host — the budget is per
    transfer, not per job.
    """
    try:
        raw = os.environ.get(
            "VIDZEO_WEB_IMAGE_DOWNLOAD_TIMEOUT_SECONDS",
            "60" if fast_mode else "120",
        )
        value = float(raw or (60 if fast_mode else 120))
    except (TypeError, ValueError):
        value = 60.0 if fast_mode else 120.0
    return max(3.0, min(300.0, value))


def _shared_pool_prefetch_timeout_seconds(
    fast_mode: bool, stage_deadline_at: Optional[float] = None
) -> float:
    """Per-image download timeout (seconds) for the shared-pool prefetch.

    Round 6 (test 33): the prefetch used a hardcoded 5 s (fast mode) /
    8 s budget, while a healthy-hour image transfer via the proxy takes
    ~16-18 s — so the warm pool could NEVER land a file in fast mode,
    and all 15 prefetch rows died in test 33 (0/15 local assets) even
    though the wave had just returned 40 rows. The prefetch now uses the
    same proven scene budget as every other web download. The prefetch
    is still bounded: the main thread only joins it for the prefetch
    seconds, and the stage deadline caps the budget.
    """
    remaining = None
    if stage_deadline_at is not None:
        try:
            remaining = float(stage_deadline_at) - time.monotonic()
        except (TypeError, ValueError):
            remaining = None
    budget = _web_image_download_timeout_seconds(fast_mode)
    if remaining is not None:
        budget = min(budget, max(3.0, remaining))
    return budget


def _resolve_scene(idx: int, scene_text: str, queries: List[str],
                   gdl: Optional[List[str]], output_dir: Path, main_subject: str,
                   global_phashes: Dict[int, int], global_md5s: Dict[str, int], used_files: Set[str],
                   phash_lock: threading.Lock, md5_lock: threading.Lock, file_lock: threading.Lock,
                    broaden: bool = True, fast_mode: bool = False,
                    max_candidate_downloads: int = 20,
                    api_worker_limit: int = 6,
                    source_telemetry=None,
                    deadline_at: Optional[float] = None,
                    discovery_cache=None,
                    shared_pool_query: str = "",
                    shared_pool_candidates: Optional[List[Dict]] = None,
                    cancel_event: Optional[threading.Event] = None,
                    weak_terms: Optional[Set[str]] = None,
                    strict_subject: bool = False,
                    source_health=None,
                    used_urls: Optional[Set[str]] = None,
                    url_lock: Optional[threading.Lock] = None,
                    shared_preferred_url: str = "",
                    shared_reserved_owners: Optional[Mapping[str, int]] = None,
                    visual_requirement: Optional[Mapping[str, Any]] = None,
                    allow_visual_pending: bool = False,
                    defer_context_verification: bool = False,
                    query_seconds_override: Optional[float] = None,
                    visual_context: Any = None,
                    visual_pool: Optional[ThreadPoolExecutor] = None,
                    global_topic: str = "",
                    download_cache: Optional[Dict[str, Optional[str]]] = None,
                    download_cache_lock: Optional[threading.Lock] = None,
                    exhaustive_fallback: bool = False,
                    wait_until_complete: bool = False,
                    prioritize_director_query: bool = False,
                    audit_token: Optional[str] = None,
                    outage_tracker: Optional["WebOutageTracker"] = None) -> Optional[Dict]:
    if source_health is None:
        source_health = _ACTIVE_PROVIDER_HEALTH
    # Start the per-scene budget when a worker begins, not when it is queued.
    # Otherwise later scenes can lose most of their deadline waiting for a
    # worker slot while one unhealthy provider owns the retrieval wall.
    deadline_at = _scene_deadline(deadline_at)
    # Round 10: the effective deadline is the stage deadline PLUS the
    # outage tracker's granted extension. Backoff time must not count
    # against the media wall, so every deadline check below reads the
    # effective value — a scene that already slept through an outage
    # still gets its full wave budget once the egress recovers.
    def _effective_deadline_at() -> Optional[float]:
        if deadline_at is None:
            return None
        if outage_tracker is None:
            return deadline_at
        return float(deadline_at) + outage_tracker.extension_seconds()
    # Per-scene universal engine audit trail (GTA fix, Nov 16): every
    # universal batch's per-engine {engine, count, duration, error} is
    # recorded via _fetch_source_query so the scene's audit entry shows
    # what each engine did — even when the scene resolves to nothing.
    # audit_token (one resolve_web_images_for_video_fast call) scopes the
    # registry so concurrent jobs cannot mix scenes.
    universal_engine_audits: List[Dict[str, object]] = []
    try:
        from .query import _extract_visual_keywords as _scene_visual_keywords
    except Exception:
        _scene_visual_keywords = None
    scene_dir = output_dir / f"scene_{idx + 1}"
    scene_dir.mkdir(parents=True, exist_ok=True)
    # Keep the scene query pack small and predictable.  The shared provider
    # warm-up already supplies breadth; four scene-level waves are enough for
    # identity, action, archival, and one recovery variant without turning a
    # slow image host into a per-scene latency multiplier.
    queries = list(dict.fromkeys(str(q).strip() for q in (queries or []) if str(q).strip()))
    canonical = (visual_requirement or {}).get("canonical_entity") if isinstance(visual_requirement, Mapping) else ""
    publisher_query = _publisher_screenshot_query(str(canonical or main_subject or ""), scene_text)
    if publisher_query:
        # Preserve the scene director's most specific search and reserve
        # the next attempt for a genuine publisher still.
        queries = list(dict.fromkeys(queries[:1] + [publisher_query] + queries[1:]))
    # Nov 22 (d1ece590 stall fix): query cleaning as a safety net. Even if
    # a bad query somehow reached this scene, drop it before it reaches any
    # provider; if every query was garbage, fall back to one broad
    # topic-anchored query so the scene still searches. A bad query must
    # never throw or kill the whole web pipeline — the scene falls back to
    # its other queries, and the pipeline keeps moving.
    try:
        from .query import clean_scene_queries
        queries = clean_scene_queries(
            queries, fallback_subject=str(main_subject or global_topic or "")
        )
    except Exception:
        pass
    queries = queries[:4]
    # Rank a bounded shortlist first, retaining a second host/query wave for
    # failures. The shared scene budget covers every query and replacement.
    if fast_mode:
        try:
            # Default matches the per-scene top-12 pool (Nov 10, 2026):
            # fast mode keeps 12 downloads per query, not 8, so the
            # vision shortlist can actually reach 12.
            configured_fast_per_query = int(
                os.environ.get("VIDZEO_WEB_FAST_CANDIDATES_PER_QUERY", "12") or 12
            )
            # Named-person contracts need enough independent pixels for the
            # final vision gate to choose a real portrait.  Three downloads
            # often meant that one bad Flickr caption and one watermarked
            # Yandex hit exhausted the scene before Wikimedia/Openverse could
            # contribute their official portrait.  Keep the increase bounded;
            # it applies only to a semantic person scene, not every web job.
            person_recall = bool(
                visual_requirement
                and _is_biographical_person(
                    str((visual_requirement or {}).get("canonical_entity") or main_subject)
                )
            )
            fast_per_query = max(
                1,
                min(12, configured_fast_per_query),
            )
            if person_recall:
                fast_per_query = max(5, fast_per_query)
        except (TypeError, ValueError):
            fast_per_query = 12
        try:
            scene_download_budget = max(
                fast_per_query,
                min(
                    16,
                    int(
                        os.environ.get(
                            "VIDZEO_WEB_FAST_DOWNLOADS_PER_SCENE",
                            "12",
                        )
                        or 12
                    ),
                ),
            )
        except (TypeError, ValueError):
            scene_download_budget = max(fast_per_query, 12)
    else:
        fast_per_query = 0
        scene_download_budget = max(1, int(max_candidate_downloads or 1))
    scene_downloaded_candidates = 0
    # Per-URL download timing for this scene (muse-search design §4.2):
    # filled by _download_candidate_batch via download_bulk stats —
    # one record per URL with route / slot_wait_ms / transfer_ms — so
    # the failure summary can tell a slot wait from a slow transfer.
    scene_download_stats: List[Dict] = []
    scene_attempted_urls = set()
    semantic_requirement = dict(visual_requirement or {}) if isinstance(visual_requirement, Mapping) else {}
    # Compute the job shape before the shared-row recovery callback is
    # defined.  The callback can run immediately when the warm pool already
    # has rows, so leaving this assignment below the callback creates a Python
    # free-variable race and aborts every scene.
    _topic_wide_job_fallback = bool(
        re.search(r"\b(?:top|number|list|famous|most|best)\b", str(global_topic or "").casefold())
        or re.search(r"\b(?:top|number)\s*\d+\b", str(global_topic or ""), re.I)
    )
    # The unified V2 adapter owns the final visual decision.  Provider
    # transports may therefore download identity-bearing candidates whose
    # page metadata does not describe the scene action, but only as an
    # explicitly pending handoff.  This is not an acceptance path.
    # A semantic contract always has an authoritative verification boundary:
    # either this resolver's visual shortlist or the V2 coordinator.  Do not
    # discard URL-only/terse-title rows before either boundary merely because
    # an internal transport flag was omitted by one call path. Pending rows
    # remain explicitly marked and can never self-accept.
    can_defer_context_verification = bool(semantic_requirement)
    if semantic_requirement:
        semantic_subject = str(semantic_requirement.get("canonical_entity") or "").strip()
        if semantic_subject:
            # Never score a semantically locked row against the caller's
            # legacy/global topic anchor. The row's immutable identity is the
            # only subject allowed to influence provider metadata ranking.
            main_subject = semantic_subject
            strict_subject = True
    # Reserve a bounded final query for the immutable subject. Narrative
    # phrases (and a news year) can make every supplied variant empty even
    # though the provider has useful archival photos of the same entity. Keep
    # the pack small when the planner supplied a long list; this prevents a
    # semantic scene from spending its whole wall on near-duplicate variants.
    # Event scenes may add one action query below, so their exact event query
    # still gets first refusal without expanding ordinary person packs.
    if semantic_requirement and main_subject:
        canonical_query = re.sub(r"\s+", " ", main_subject).strip()
        canonical_tokens = re.findall(r"[a-z0-9]+", canonical_query.casefold())
        identity_descriptors = r"\b(?:portrait|photo|photograph|profile|archive|official|ceremony|event)\b"
        has_identity_wave = any(
            all(
                re.search(
                    r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])",
                    str(q).casefold(),
                )
                for token in canonical_tokens
            )
            and (
                str(q).strip().casefold() == canonical_query.casefold()
                or re.search(identity_descriptors, str(q), re.I)
            )
            for q in queries
        )
        if canonical_query and not has_identity_wave:
            # Add an exact identity wave only when the planner did not already
            # provide one. Preserve the strongest supplied variants and reserve
            # the final bounded wave for the canonical entity.
            queries = queries[:3] + [canonical_query]
    # Keep the first identity query (usually ``<person> portrait``), then run
    # one scene-action query before the generic photo/alias waves. This is the
    # useful middle ground for people and events: portrait search establishes
    # identity, while the action/venue query avoids family trees, memes, and
    # unrelated same-domain portraits. The final coordinator still owns pixel
    # acceptance; this only improves discovery recall.
    if semantic_requirement and main_subject and len(queries) >= 1:
        action_terms = []
        for value in (
            semantic_requirement.get("action_terms") or (),
            semantic_requirement.get("positive_evidence") or (),
            scene_text,
        ):
            for token in re.findall(r"[A-Za-z][A-Za-z'’-]{3,}", str(value)):
                clean = token.strip("'’").lower()
                if clean in STOPWORDS or clean in _WEB_CONTAINER_TERMS:
                    continue
                if clean in {part.lower() for part in re.findall(r"[A-Za-z][A-Za-z'’-]+", main_subject)}:
                    continue
                if clean not in {item.lower() for item in action_terms}:
                    action_terms.append(token.strip("'’"))
                if len(action_terms) >= 3:
                    break
            if len(action_terms) >= 3:
                break
        if action_terms:
            action_query = f"{main_subject} {' '.join(action_terms[:3])} photograph"
            if action_query.casefold() not in {q.casefold() for q in queries}:
                # Event/fact scenes need the event wave before a generic
                # portrait wave.  The resolver stops after the first query
                # that yields a validated shortlist; putting ``portrait``
                # first made a generic or wrong-person hit suppress the
                # exact ceremony/coronation query entirely.  Ordinary person
                # scenes retain their existing portrait-first ordering.
                event_markers = {
                    "wedding", "married", "marriage", "coronation", "crowned",
                    "funeral", "inauguration", "ceremony", "sworn", "appointed",
                    "welcomed", "welcomes", "birth", "born", "launch", "opened",
                }
                event_scene = bool(
                    event_markers.intersection(
                        {
                            token.casefold()
                            for token in re.findall(
                                r"[A-Za-z][A-Za-z'’-]{3,}",
                                " ".join(action_terms) + " " + str(scene_text or ""),
                            )
                        }
                    )
                )
                # An action wave is valuable for a ceremony/coronation, but
                # an ordinary long person pack should remain bounded to the
                # three useful variants plus the canonical subject query.
                # This keeps discovery predictable while preserving exact
                # event recall.
                if event_scene or len(queries) < 4:
                    queries.insert(0 if event_scene else 1, action_query)
        queries = queries[:4]
    subject_terms = {
        token.lower()
        for token in re.findall(r"[a-z0-9]+", str(main_subject or ""))
        if token
    }
    strict_subject = bool(
        strict_subject
        or (subject_terms and not subject_terms <= _WEB_CONTAINER_TERMS)
    )
    score_scene_text = (
        f"{scene_text} {main_subject}".strip()
        if strict_subject and main_subject
        else scene_text
    )

    identity_terms = set()
    if main_subject:
        identity_terms = {
            w.lower()
            for w in re.findall(r"[a-z]{3,}", main_subject.lower())
            if w.lower() not in STOPWORDS
        }
        # Do not let a shared pool qualify a different person merely because
        # both names carry the same honorific (``Prince Harry`` vs
        # ``Prince William``).  The concrete surname/given name remains the
        # discriminating term for the scene-specific wave.
        if str(semantic_requirement.get("subject_type") or "").strip().lower() == "person":
            identity_terms -= {
                "prince", "princess", "king", "queen", "duke", "duchess",
                "president", "minister", "chancellor", "emperor", "empress",
                "pope", "senator", "governor", "royal", "royalty", "monarch",
            }

    def _allow_query_anchored_visual_pending(
        item: Mapping[str, Any], reason: str, evidence: Mapping[str, Any]
    ) -> bool:
        """Keep plausible search hits for the authoritative pixel gate.

        Search queries are never identity evidence.  They are only used here
        to verify that discovery was anchored to the complete canonical name;
        the downloaded pixels still have to pass the shared vision verifier.
        This repairs URL-only and terse-title search results without admitting
        explicit wrong-type, unsafe, or confusable metadata.
        """
        if not can_defer_context_verification or not semantic_requirement:
            return False
        weak_reasons = {
            "missing_asset_identity_metadata",
            "missing_automobile_evidence",
            "missing_animal_evidence",
            "missing_rail_crew_evidence",
            "missing_required_model_or_make",
            # Provider captions frequently attach a neighbouring person or
            # family member to an otherwise valid exact-name search hit.  A
            # scene-locked query is a recall signal, not identity proof, so
            # let the downloaded pixels reach the authoritative vision gate.
            # Explicit synthetic/confusable metadata remains blocked below.
            "person_confusable_entity",
            # Current-event and archival searches often return an older
            # photograph whose caption carries a different year.  The year
            # belongs to the narrative requirement; it is not a reason to
            # throw away an otherwise topic-anchored image before the pixel
            # verifier has seen it.
            "wrong_required_year",
            "canonical_entity_mismatch",
        }
        if str(reason or "") not in weak_reasons:
            return False
        if str(reason or '') == 'canonical_entity_mismatch':
            caption = ' '.join(str(item.get(key) or '') for key in ('title', 'description', 'tags')).strip()
            if len(caption) > 10:
                from .hybrid_search import _relevance_gate
                canonical = str(semantic_requirement.get('canonical_entity') or main_subject or '')
                contexts = [canonical, str(global_topic or shared_pool_query or '')]
                if (semantic_requirement.get('subject_type') == 'person'
                        and re.search(r'\b(?:Prince|Princess|King|Queen|Duke|Duchess)\b', canonical)):
                    contexts.append('royal family')
                if canonical and not any(_relevance_gate([dict(item)], context)
                                         for context in contexts if context):
                    # A query is discovery context, never evidence for an
                    # explicitly captioned unrelated person/sport/object.
                    return False
        if str(reason or "") == "person_confusable_entity":
            # Do not turn the soft handoff into a path for toys, wax figures,
            # avatars, animals, or other explicit confusable catalog assets.
            # Those are deterministic metadata evidence and should never be
            # delegated to a permissive visual fallback.
            hard_confusable_keys = {
                "confusable_hits",
                "forbidden_match",
                "animal_hits",
                "auto_hits",
                "virtual_avatar_catalog",
                "wax_museum",
            }
            if any(
                key in evidence and bool(evidence.get(key))
                for key in hard_confusable_keys
            ):
                return False
        if any(key in evidence for key in ("forbidden_match", "animal_hits", "auto_hits")) and str(reason).startswith("wrong_"):
            return False
        url = str(item.get("url") or item.get("image_url") or item.get("preview_url") or "").strip()
        if not url:
            return False
        # The warm shared pool is anchored to the video topic while an
        # individual scene may narrow that topic to a person or event facet.
        # Preserve clean pooled rows as an explicit pending handoff so the
        # pixel verifier, rather than terse provider metadata, makes the
        # final relevance decision.
        if item.get("_from_shared_batch"):
            shared_query = str(
                item.get("query")
                or item.get("query_used")
                or shared_pool_query
                or global_topic
                or ""
            ).casefold()
            shared_tokens = [
                token for token in re.findall(
                    r"[a-z0-9]{3,}",
                    str(shared_pool_query or global_topic or "").casefold(),
                )
                if token not in STOPWORDS and token not in _WEB_CONTAINER_TERMS
            ]
            metadata_blob = " ".join(
                str(item.get(field) or "")
                for field in ("title", "tags", "description", "source_url", "page_url")
            ).casefold()
            # A warmed pool is intentionally broad and belongs to the job's
            # anchor.  It is safe for a scene only when the asset metadata also
            # names that scene's concrete identity.  Without this check, a
            # Prince Harry pool row was handed to Prince William/King Charles
            # scenes because the shared query itself contained a common royal
            # role.  The pixel verifier cannot repair a candidate that was
            # never queried for the scene, and when vision is unavailable this
            # became an incorrect accepted still.
            scene_identity_tokens = [
                token
                for token in re.findall(
                    r"[a-z0-9]+",
                    str(semantic_requirement.get("canonical_entity") or "").casefold(),
                )
                if token not in STOPWORDS
                and token not in _WEB_CONTAINER_TERMS
                and token not in {
                    "prince", "princess", "king", "queen", "duke", "duchess",
                    "president", "minister", "chancellor", "emperor", "empress",
                    "pope", "senator", "governor", "royal", "royalty", "monarch",
                    "british", "family", "monarchy",
                }
                and len(token) >= 3
            ]
            shared_query_tokens = set(
                re.findall(r"[a-z0-9]+", str(shared_pool_query or "").casefold())
            )
            scene_is_narrower_than_pool = bool(
                scene_identity_tokens
                and not all(token in shared_query_tokens for token in scene_identity_tokens)
            )
            if scene_is_narrower_than_pool:
                identity_hits = sum(
                    1
                    for token in scene_identity_tokens
                    if re.search(
                        r"(?<![a-z0-9])" + re.escape(token) + r"s?(?![a-z0-9])",
                        metadata_blob,
                    )
                )
                # One distinctive name token is enough for a pending handoff
                # (providers often omit middle names/titles), but a completely
                # different person's row must never enter this scene.
                if identity_hits < 1:
                    # A family-level shared pool may contain a related royal
                    # (for example William in a Harry/William reconciliation
                    # scene).  Keep that clean same-domain row pending for the
                    # coordinator's vision decision; never accept it here.
                    subject_type = str(semantic_requirement.get("subject_type") or "").casefold()
                    is_royal_family_pool = bool(
                        re.search(r"\b(?:british\s+)?royal\s+famil(?:y|ies)\b", shared_query)
                        or re.search(r"\b(?:british\s+)?monarch(?:y|ies)\b", shared_query)
                    )
                    has_royal_role = bool(re.search(
                        r"\b(?:king|queen|prince|princess|duke|duchess|monarch|royal)\b",
                        metadata_blob,
                    ))
                    if not (subject_type == "person" and is_royal_family_pool and has_royal_role):
                        return False
            # Query text alone is never evidence. Require the asset-authored
            # metadata to carry the topic anchor (a full phrase, or at least
            # two discriminating title tokens) before handing it to vision.
            metadata_hits = sum(
                1
                for token in dict.fromkeys(shared_tokens)
                if re.search(
                    r"(?<![a-z0-9])" + re.escape(token) + r"s?(?![a-z0-9])",
                    metadata_blob,
                )
            )
            required_hits = 1 if len(shared_tokens) <= 1 else min(2, len(shared_tokens))
            if shared_tokens and metadata_hits >= required_hits:
                return True
            # Yandex's unescaped SERP frequently has no card caption at all;
            # the query and image URL are still a valid recall handoff when
            # the row is explicitly marked as a Yandex candidate.  Keep it
            # pending for the pixel verifier rather than discarding the
            # entire Yandex lane for missing publisher metadata.
            provider_key = str(
                item.get("provider") or item.get("source") or item.get("universal_engine") or ""
            ).casefold()
            if "yandex" in provider_key:
                return True
        canonical_tokens = [
            token for token in re.findall(r"[a-z0-9]+", str(semantic_requirement.get("canonical_entity") or "").casefold())
            if len(token) > 1
        ]
        query = str(item.get("query") or item.get("query_used") or "").casefold()
        canonical_query_match = bool(
            canonical_tokens
            and all(
                re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", query)
                for token in canonical_tokens
            )
        )
        normalized_query = re.sub(r"[^a-z0-9]+", " ", query).strip()
        alias_query_match = any(
            re.sub(r"[^a-z0-9]+", " ", str(alias).casefold()).strip() in normalized_query
            for alias in (semantic_requirement.get("aliases") or ())
            if str(alias or "").strip()
        )
        if canonical_tokens and not (canonical_query_match or alias_query_match):
            return False
        # Provider captions are frequently generic page labels ("front view",
        # numeric CDN names, or collection titles). They may rank candidates,
        # but absence of the entity in that label must not prevent the actual
        # pixels from reaching vision. Explicit unsafe/wrong-type reasons were
        # excluded above; this branch remains pending and cannot self-accept.
        return True

    def _passes_semantic_metadata(item: Mapping[str, Any]) -> bool:
        if not semantic_requirement:
            return True
        matched, reason, evidence = candidate_requirement_match(semantic_requirement, item)
        if not matched:
            if can_defer_context_verification and _allow_domain_context_pending(item, reason, evidence):
                if isinstance(item, dict):
                    item["semantic_context_pending"] = True
                    item["candidate_evidence"] = {
                        **evidence,
                        "metadata_source": "domain_context_pending",
                        "verification_required": True,
                    }
                    item["requirement_hash"] = str(
                        semantic_requirement.get("requirement_hash") or ""
                    )
                return True
            nearest_pending, _nearest_reason, nearest_evidence = candidate_nearest_match(
                semantic_requirement, item
            )
            if can_defer_context_verification and nearest_pending:
                if isinstance(item, dict):
                    item["semantic_context_pending"] = True
                    item["nearest_match_pending"] = True
                    item["candidate_evidence"] = {
                        **dict(nearest_evidence or {}),
                        "metadata_source": "nearest_match_pending",
                        "verification_required": True,
                    }
                    item["identity_gate_reason"] = "nearest_match_pending_visual_required"
                    item["requirement_hash"] = str(
                        semantic_requirement.get("requirement_hash") or ""
                    )
                return True
            # gallery-dl's --get-urls mode returns bare asset URLs. That is a
            # recall source, not identity proof. Permit the row to continue
            # only when this job has a required visual verifier waiting for
            # the downloaded pixels; query text never satisfies this branch.
            if (
                can_defer_context_verification
                and item.get("metadata_pending")
                and reason == "missing_asset_identity_metadata"
            ):
                if isinstance(item, dict):
                    item["semantic_visual_pending"] = True
                    item["candidate_evidence"] = {
                        **evidence,
                        "metadata_source": "visual_pending",
                        "verification_required": True,
                    }
                    item["requirement_hash"] = str(
                        semantic_requirement.get("requirement_hash") or ""
                    )
                return True
            if _allow_query_anchored_visual_pending(item, reason, evidence):
                if isinstance(item, dict):
                    item["semantic_visual_pending"] = True
                    item["candidate_evidence"] = {
                        **evidence,
                        "metadata_source": "query_anchored_visual_pending",
                        "verification_required": True,
                    }
                    item["requirement_hash"] = str(semantic_requirement.get("requirement_hash") or "")
                return True
            if isinstance(item, dict):
                item["semantic_rejection_reason"] = reason
                item["candidate_evidence"] = evidence
            return False
        if isinstance(item, dict):
            if item.get("semantic_context_pending") or item.get("semantic_visual_pending"):
                # Preserve the explicit pending handoff marker. The later
                # semantic metadata pass adds evidence, but must not turn an
                # identity-only candidate into an accepted asset.
                pending_evidence = dict(item.get("candidate_evidence") or {})
                item["candidate_evidence"] = {
                    **evidence,
                    **pending_evidence,
                    "verification_required": True,
                }
            else:
                item["candidate_evidence"] = evidence
            item["requirement_hash"] = str(semantic_requirement.get("requirement_hash") or "")
        return True

    def _allow_domain_context_pending(
        item: Mapping[str, Any],
        reason: str,
        evidence: Mapping[str, Any],
    ) -> bool:
        """Admit plausible domain rows to the final pixel-level verifier.

        Scrapers frequently return a strong rail photograph titled only with
        the locomotive, railway, or station name. Requiring the literal word
        ``crew`` at this stage throws away exactly the alternate images that
        should be compared by vision. This is deliberately narrower than a
        generic lenient mode: it applies only to the contract's rail-crew
        domain, requires asset metadata (never the query), rejects animal
        evidence, and marks the row pending so the shared strict verifier must
        decide it before rendering.
        """
        if not semantic_requirement or str(reason or "") != "missing_rail_crew_evidence":
            return False
        pending, _pending_evidence = candidate_domain_context_pending(
            semantic_requirement, item
        )
        return bool(pending)

    def _provisional_visual_score(item: Mapping[str, Any], score: float) -> float:
        """Keep URL-only scraper rows downloadable, but never authoritative."""
        if item.get("semantic_visual_pending") and can_defer_context_verification:
            # The score only controls which bounded candidates get downloaded.
            # It is deliberately not evidence and is replaced by visual
            # relevance after the image is decoded.
            return max(0.25, float(score))
        return float(score)

    def _defer_identity_candidate(item: Mapping[str, Any], score: float) -> float:
        """Keep an identity-bearing row for the final pixel-level verifier.

        Scene/action wording is often absent from archival image metadata.  A
        semantically locked identity match is still useful recall, but it must
        be marked pending and must never be accepted by this transport stage.
        Hard junk/watermark rows remain rejected even when their title happens
        to contain the canonical identity.
        """
        if not semantic_requirement or not can_defer_context_verification:
            return float(score)
        if _web_junk_metadata(
            item.get("title", ""),
            item.get("url", ""),
            item.get("source_url", ""),
            item.get("tags", ""),
            item.get("description", ""),
        ):
            return float(score)
        haystack = " ".join(
            str(item.get(key) or "")
            for key in ("title", "url", "source_url", "tags", "description")
        ).casefold()
        if any(
            marker in haystack
            for marker in (
                "alamy", "gettyimages", "shutterstock", "istockphoto",
                "dreamstime", "depositphotos", "123rf", "watermark",
            )
        ):
            return float(score)
        matched, _reason, evidence = candidate_requirement_match(
            semantic_requirement, item
        )
        # A role title can prove the rail domain without describing the scene
        # action. Keep that identity-only row for the final visual verifier,
        # even when the lexical provisional score is positive.
        candidate_class, class_reason = _metadata_candidate_class(
            item,
            main_subject=main_subject,
            scene_text=scene_text,
        )
        metadata_text = " ".join(
            str(item.get(key) or "")
            for key in ("title", "description", "tags", "url", "source_url")
        ).casefold()
        context_terms = {
            term.casefold()
            for term in re.findall(r"[a-z0-9]{4,}", str(scene_text or "").casefold())
            if term not in STOPWORDS
            and term not in _WEB_CONTAINER_TERMS
            and term not in {
                token.casefold()
                for token in re.findall(r"[a-z0-9]{3,}", str(main_subject or "").casefold())
            }
        }
        has_scene_context = any(
            re.search(r"(?<![a-z0-9])" + re.escape(term.rstrip("s")) + r"s?(?![a-z0-9])", metadata_text)
            for term in context_terms
        )
        identity_only = bool(
            candidate_class == "anchored_generic"
            and class_reason == "rail_role_alias_pending_visual"
            and not has_scene_context
        )
        if score >= 0.0 and not identity_only:
            return float(score)
        identity_pending = bool(matched) or identity_only
        if not identity_pending:
            identity_pending = _allow_domain_context_pending(
                item, "missing_rail_crew_evidence", {}
            )
        if not identity_pending:
            return float(score)
        if isinstance(item, dict):
            item["semantic_context_pending"] = True
            item["candidate_evidence"] = {
                **dict(evidence or {}),
                "metadata_source": "identity_only_scene_context_pending",
                "scene_context_pending": True,
                "verification_required": True,
                "requirement_hash": str(
                    semantic_requirement.get("requirement_hash") or ""
                ),
            }
            item["requirement_hash"] = str(
                semantic_requirement.get("requirement_hash") or ""
            )
        # A small positive ordering score is deliberately not evidence; the
        # coordinator must still run vision before the row can render.
        return 0.25
    discovered_candidates = []
    seen_urls = set()
    results_lock = threading.Lock()
    max_candidate_downloads = max(3, min(20, int(max_candidate_downloads or 20)))
    try:
        # A web still is a one-beat asset by default.  Long storyboards often
        # contain several beats for the same named person, though; enforcing a
        # global cap of one makes concurrent scene workers discard every shared
        # finalist and spend the whole retrieval wall on duplicate retries.
        # Allow a small bounded pool reuse for large jobs.  The coordinator
        # still rejects adjacent duplicates and records every reuse, so this
        # improves recall without turning the edit into a slideshow of one
        # image.
        configured_reuse = os.environ.get('VIDZEO_WEB_IMAGE_REUSE_CAP')
        if configured_reuse is None:
            configured_reuse = '2' if len(scene_texts) >= 16 else '1'
        _max_reuse = int(configured_reuse or 1)
    except Exception:
        _max_reuse = 3

    def _deadline_reached() -> bool:
        effective = _effective_deadline_at()
        return bool(
            (cancel_event is not None and cancel_event.is_set())
            or (effective and time.monotonic() >= float(effective))
        )

    def _fetch_source_query(source_name: str, source_type: str, q_str: str, max_timeout: Optional[int] = None, q_idx: int = 0) -> List[Dict]:
        if not q_str:
            return []

        def _universal_bounded_here() -> bool:
            """Bounded universal fallback (muse-search design §1.3).

            When the muse-search wave is enabled, the universal lane
            gets exactly one attempt per query (no retry, no rescue)
            — it already ran after the muse wave returned 0, so
            repeating the 5-attempt loop burns the stage wall twice.
            The exhaustive/rescue path keeps the full loop.
            """
            if source_name != "universal_scraper":
                return False
            if exhaustive_fallback:
                return False
            return muse_search_enabled()

        def _call_discover(direct: bool) -> List[Dict]:
            kwargs = {
                "max_timeout": max_timeout,
                "wait_until_complete": wait_until_complete,
                "direct": direct,
                "universal_bounded": _universal_bounded_here(),
                "q_idx": q_idx,
                "scene_index": idx,
            }
            # Older provider adapters and test doubles predate the
            # wait-until-complete / direct keywords. Drop each keyword
            # the callee rejects, one at a time, and retry — a double
            # may predate both — keeping the new bounded wait contract
            # without turning a compatible adapter into an empty scene.
            while True:
                try:
                    return _discover_source_query(
                        source_name, source_type, q_str, gdl, **kwargs
                    )
                except TypeError as exc:
                    text = str(exc)
                    dropped = False
                    for key in ("direct", "wait_until_complete", "universal_bounded", "q_idx", "scene_index"):
                        if key in text and key in kwargs:
                            kwargs.pop(key)
                            dropped = True
                            break
                    if not dropped:
                        raise

        def fetch() -> List[Dict]:
            # Diagnostic kill-switch (muse-search design §4.1): with
            # VIDZEO_MUSE_SEARCH_FALLBACK_UNIVERSAL=0 the universal lane
            # never runs as a fallback while the muse wave is enabled.
            # The exhaustive path ignores the switch (it needs every lane).
            if (
                source_name == "universal_scraper"
                and muse_search_enabled()
                and not exhaustive_fallback
                and not muse_search_fallback_universal_enabled()
            ):
                return []
            # Round 10: if the stage declared an egress outage, do not
            # issue another provider call — sleep through the backoff
            # instead. The backoff is excluded from the media deadline
            # (see _effective_deadline_at), so waiting here costs the
            # job nothing but time, and the egress gets a chance to
            # recover instead of being hammered by every scene worker.
            if outage_tracker is not None:
                outage_tracker.wait_if_outage(cancel_event=cancel_event)
            if _deadline_reached():
                return []
            # Route-switching retry (Nov 18, web-resilience): the
            # api/hybrid/flickr_api lanes had no retry at all. When a lane
            # comes back empty AND recorded a provider failure during the
            # call, retry once using the alternate route. A genuine empty answer
            # — the lane answered, no failure recorded — is never retried.
            # The universal lane is excluded: it already retries plus a
            # rescue pass inside _discover_universal_urls.
            #
            # Round 10: the failure count is snapshotted for EVERY lane,
            # not just the route-switch ones — it is the outage
            # circuit's transport-failure signal for the universal lane
            # (whose _report_provider_failure bumps this counter when all
            # engines fail) and the api/hybrid/flickr_api lanes. The
            # muse-search wave never raises and never touches this
            # counter, so its signal comes from the manager flag instead
            # (see _note_fetch_outcome below).
            health_before = (
                source_health.failure_count(source_name)
                if source_health is not None
                and hasattr(source_health, "failure_count")
                else None
            )
            direct_first = web_direct_first()
            rows = _call_discover(direct=direct_first)
            if (
                rows
                or source_type not in ("api", "hybrid", "flickr_api")
                or health_before is None
            ):
                _note_fetch_outcome(source_name, rows, health_before)
                return rows
            if source_health.failure_count(source_name) <= health_before:
                _note_fetch_outcome(source_name, rows, health_before)
                return rows
            delay = _api_lane_retry_backoff_seconds()
            if delay > 0:
                time.sleep(delay)
            _web_log(
                f"{source_name} '{q_str[:30]}': empty after provider error; "
                f"retrying once via {'proxy' if direct_first else 'direct'}"
            )
            if not _deadline_reached():
                rows = _call_discover(direct=not direct_first)
            _note_fetch_outcome(source_name, rows, health_before)
            return rows

        def _note_fetch_outcome(
            _source_name: str,
            _rows: Optional[List[Dict]],
            _health_before: Optional[int],
        ) -> None:
            """Report one provider call to the stage outage circuit.

            Round 10: only a 0-row call that failed on TRANSPORT
            counts. The muse-search wave reports its verdict through
            the manager flag (it swallows provider exceptions); every
            other lane reports through the provider-health failure
            counter, which grows only when the lane actually errored —
            a genuine empty answer never grows it.
            """
            if outage_tracker is None:
                return
            got_rows = len(_rows or [])
            if got_rows > 0:
                outage_tracker.note_outcome(transport_failed=False, rows=got_rows)
                return
            transport_failed = False
            if _source_name == "muse_search":
                try:
                    transport_failed = bool(
                        muse_search_last_wave_transport_failed()
                    )
                except Exception:
                    transport_failed = False
            elif (
                _health_before is not None
                and source_health is not None
                and hasattr(source_health, "failure_count")
            ):
                try:
                    transport_failed = bool(
                        source_health.failure_count(_source_name)
                        > _health_before
                    )
                except Exception:
                    transport_failed = False
            outage_tracker.note_outcome(
                transport_failed=transport_failed, rows=0
            )


        rows = None
        try:
            if discovery_cache is not None:
                # Run-7 fix: a scene wave asking for a query the shared
                # batch already owns must wait for that owner instead of
                # giving up after 15s.  The batch owner lands its rows at
                # ~25-27s (universal engines through the proxy); the old
                # 15s coalesce timeout returned [] while the rows were
                # still in flight, orphaning them — no later wave ever
                # re-peeked the same query.  60s covers one engine wave
                # plus a retry and is still bounded by the scene/stage
                # deadline; it waits for work already in progress, it
                # issues no new request.
                remaining = 60.0
                _eff_dl = _effective_deadline_at()
                if _eff_dl:
                    remaining = max(
                        0.1,
                        min(60.0, float(_eff_dl) - time.monotonic()),
                    )
                rows = discovery_cache.get_or_fetch(
                    source_name,
                    q_str,
                    fetch,
                    telemetry=source_telemetry,
                    wait_timeout=remaining,
                )
            else:
                cache_key = (source_name, q_str)
                with _CACHE_LOCK:
                    if _SEARCH_QUERY_CACHE.get(cache_key):
                        rows = list(_SEARCH_QUERY_CACHE[cache_key])
                if rows is None:
                    rows = fetch()
                    with _CACHE_LOCK:
                        # A transient empty search is not permanent negative
                        # evidence. Recovery and later jobs must retry it;
                        # otherwise a brief provider outage poisons this query
                        # for the lifetime of the service.
                        if rows:
                            _SEARCH_QUERY_CACHE[cache_key] = list(rows)
                        else:
                            _SEARCH_QUERY_CACHE.pop(cache_key, None)
        except Exception as exc:
            if source_health is not None:
                source_health.note_failure(
                    source_name, f"{type(exc).__name__}: {str(exc)[:80]}"
                )
            return []
        if rows and source_health is not None:
            source_health.note_success(source_name)
        if source_name == "universal_scraper":
            _record_universal_audit_entry(
                universal_engine_audits, q_str,
                audit_token=audit_token, scene_index=idx,
            )
        return list(rows or [])

    def _accept_source_rows(source_name: str, rows: List[Dict]) -> int:
        res = list(rows or [])
        if discovery_cache is None and source_telemetry is not None:
            source_telemetry.record(source_name, "discovered", len(res))
        added = 0
        with results_lock:
            for raw_item in res:
                if not isinstance(raw_item, Mapping):
                    continue
                # Discovery rows can be shared across concurrent scene workers.
                # Each worker must own its annotations (requirement hash,
                # semantic-pending flags and evidence) or one scene can
                # overwrite another scene's identity decision.
                item = dict(raw_item)
                if isinstance(item.get("candidate_evidence"), Mapping):
                    item["candidate_evidence"] = dict(item["candidate_evidence"])
                if source_name == "shared_batch":
                    # Mark provenance before semantic matching so pooled rows
                    # can take the explicit visual-pending handoff.
                    item["_from_shared_batch"] = True
                safe, reason = candidate_is_safe(item)
                if not safe:
                    if _web_verbose():
                        _web_log(f"scene {idx + 1} blocked unsafe metadata ({reason}) from {source_name}")
                    continue
                if semantic_requirement:
                    matched, semantic_reason, evidence = candidate_requirement_match(
                        semantic_requirement,
                        item,
                    )
                    if not matched:
                        if can_defer_context_verification and _allow_domain_context_pending(item, semantic_reason, evidence):
                            item["semantic_context_pending"] = True
                            evidence = {
                                **evidence,
                                "metadata_source": "domain_context_pending",
                                "verification_required": True,
                            }
                        elif (
                            can_defer_context_verification
                            and item.get("metadata_pending")
                            and semantic_reason == "missing_asset_identity_metadata"
                        ):
                            item["semantic_visual_pending"] = True
                            evidence = {
                                **evidence,
                                "metadata_source": "visual_pending",
                                "verification_required": True,
                            }
                        else:
                            nearest_pending, _nearest_reason, nearest_evidence = candidate_nearest_match(
                                semantic_requirement, item
                            )
                            if can_defer_context_verification and nearest_pending:
                                item["semantic_context_pending"] = True
                                item["nearest_match_pending"] = True
                                evidence = {
                                    **dict(nearest_evidence or {}),
                                    "metadata_source": "nearest_match_pending",
                                    "verification_required": True,
                                }
                            elif _allow_query_anchored_visual_pending(item, semantic_reason, evidence):
                                item["semantic_visual_pending"] = True
                                evidence = {
                                    **evidence,
                                    "metadata_source": "query_anchored_visual_pending",
                                    "verification_required": True,
                                }
                            else:
                                if source_telemetry is not None:
                                    source_telemetry.record(source_name, "semantic_relevance_rejected")
                                if _web_verbose():
                                    _web_log(
                                        f"scene {idx + 1} blocked semantic metadata "
                                        f"({semantic_reason}) from {source_name}"
                                    )
                                continue
                    if isinstance(item, dict):
                        item["candidate_evidence"] = evidence
                        item["requirement_hash"] = str(
                            semantic_requirement.get("requirement_hash") or ""
                        )
                        # The legacy royal prefilter is intentionally stricter
                        # than the semantic contract (it wants Charles/Harry
                        # words in metadata). Keep a contract-matched family
                        # row as a pending visual candidate so the pixel gate
                        # can decide whether a palace/ceremony frame is
                        # useful for this scene.
                        try:
                            candidate_class, class_reason = _metadata_candidate_class(
                                item,
                                main_subject=main_subject,
                                scene_text=score_scene_text,
                            )
                            if (
                                candidate_class == "irrelevant"
                                and str(class_reason).startswith("royal_identity_mismatch")
                            ):
                                item["semantic_visual_pending"] = True
                                item["candidate_evidence"] = {
                                    **dict(item.get("candidate_evidence") or {}),
                                    "metadata_source": "royal_prefilter_pending",
                                    "verification_required": True,
                                }
                        except Exception:
                            pass
                elif source_name != "shared_batch":
                    candidate_class, class_reason = _metadata_candidate_class(
                        item,
                        main_subject=main_subject,
                        scene_text=score_scene_text,
                    )
                    # A named listicle query is enough to queue an otherwise
                    # terse/unverified provider row for pixel verification.
                    # The query remains an ordering hint only; the final
                    # image gate still decides whether it is usable.
                    if _topic_query_visual_pending(item):
                        item["topic_query_pending"] = True
                    if candidate_class == "irrelevant":
                        evidence = dict(item.get("candidate_evidence") or {})
                        visual_pending = bool(
                            item.get("semantic_visual_pending")
                            or item.get("semantic_context_pending")
                            or item.get("nearest_match_pending")
                            or item.get("topic_query_pending")
                        )
                        identity_grounded = bool(
                            evidence.get("identity_proof")
                            or evidence.get("thematic_near_match")
                        )
                        # The legacy royal-family classifier requires Charles
                        # or Harry in metadata, even for a scene whose locked
                        # subject is William, Eugenie, Archie, or Charlotte.
                        # It also treats missing scene-action words as proof of
                        # irrelevance. Those are recall hints, not pixel proof:
                        # when the immutable scene contract or exact query
                        # already grounds the candidate, pass it to the shared
                        # visual gate instead of silently dropping it here.
                        # Explicit conflicting-entity metadata stays a hard
                        # stop; unsafe-media screening has already run above.
                        weak_legacy_reasons = (
                            str(class_reason).startswith("royal_identity_mismatch")
                            or str(class_reason) in {
                                "missing_full_person_identity",
                                "missing_container_anchor",
                            }
                        )
                        defer_legacy_rejection = bool(
                            semantic_requirement
                            and weak_legacy_reasons
                            and (visual_pending or identity_grounded)
                            and not str(class_reason).startswith("conflicting_entity:")
                        )
                        if defer_legacy_rejection and isinstance(item, dict):
                            item["semantic_context_pending"] = True
                            item["visual_verification_required"] = True
                            item["candidate_evidence"] = {
                                **evidence,
                                "metadata_source": "legacy_prefilter_pending_visual",
                                "verification_required": True,
                                "legacy_prefilter_reason": str(class_reason),
                            }
                            item["requirement_hash"] = str(
                                semantic_requirement.get("requirement_hash") or ""
                            )
                            if source_telemetry is not None:
                                source_telemetry.record(
                                    source_name, "legacy_prefilter_deferred_to_visual"
                                )
                            if _web_verbose():
                                _web_log(
                                    f"scene {idx + 1} deferred legacy metadata gate "
                                    f"({class_reason}) to visual verifier from {source_name}"
                                )
                        elif not item.get("topic_query_pending"):
                            if source_telemetry is not None:
                                source_telemetry.record(source_name, "relevance_rejected")
                            if _web_verbose():
                                _web_log(
                                    f"scene {idx + 1} blocked irrelevant metadata "
                                    f"({class_reason}) from {source_name}"
                                )
                            continue
                url = item["url"]
                if source_name == "shared_batch" and isinstance(item, dict):
                    # Keep provenance on the row so the scene allocator can
                    # distinguish a broad topic fallback from a scene-level
                    # provider hit.  The fallback remains subject-scored and
                    # safety-screened; this marker only controls the final
                    # bounded recall floor for listicle/topic-wide jobs.
                    item["_from_shared_batch"] = True
                    # Documentary scenes often narrow the canonical topic to
                    # a person, vessel role, or event facet (Captain Smith,
                    # lifeboats, the wireless room).  Their broad RMS
                    # Titanic pool rows are still useful recall candidates,
                    # but the metadata classifier quite correctly cannot
                    # prove that facet from a terse caption.  Mark clean
                    # topic-anchored rows as pending so they reach the same
                    # download/vision boundary instead of becoming a silent
                    # zero-result scene.  Listicles stay item-specific: a
                    # generic ``Famous Shipwrecks`` row must not substitute
                    # for a named entry.
                    if not _topic_wide_job_fallback:
                        try:
                            candidate_class, _class_reason = _metadata_candidate_class(
                                item,
                                main_subject=main_subject,
                                scene_text=score_scene_text,
                            )
                            if (
                                candidate_class == "irrelevant"
                                and _shared_topic_fallback_allowed(item)
                            ):
                                item["topic_query_pending"] = True
                        except Exception:
                            pass
                if url not in seen_urls:
                    seen_urls.add(url)
                    discovered_candidates.append(item)
                    added += 1
                    if source_telemetry is not None:
                        source_telemetry.record(source_name, "accepted")
        return added

    def _harvest_source_query(source_name: str, source_type: str, q_str: str, max_timeout: Optional[int] = None) -> int:
        rows = _fetch_source_query(source_name, source_type, q_str, max_timeout=max_timeout)
        return _accept_source_rows(source_name, rows)

    searx_fallback_used = False

    # Set by the scene loop when the warmed topic pool lacks an identity row
    # for this scene.  The provider helper is defined before that loop, so it
    # needs a stable default for emergency calls made after the loop.
    _scene_specific_wave_required = False

    def _run_api_harvest_single(q_str: str) -> float:
        nonlocal searx_fallback_used
        if _deadline_reached():
            return 0.0
        topic_wide_for_query = bool(
            re.search(r"\b(?:top|number|list|famous|most|best)\b", str(global_topic or "").casefold())
            or re.search(r"\b(?:top|number)\s*\d+\b", str(global_topic or ""), re.I)
        )
        is_shared_query = bool(
            shared_pool_query
            and re.sub(r"\s+", " ", q_str).strip().casefold()
            == re.sub(r"\s+", " ", shared_pool_query).strip().casefold()
        )
        universal_scene_fallback = str(
            os.environ.get(
                "VIDZEO_UNIVERSAL_SCRAPER_SCENE_FALLBACK",
                "1" if _topic_wide_job_fallback else "0",
            )
            or ("1" if _topic_wide_job_fallback else "0")
        ).strip().lower() in {"1", "true", "yes", "on"}
        api_tasks = []
        deferred_universal_task = None
        # The pre-stock recovery remains multi-source, but it is layered by
        # query wave: the first exact query fans out to the spec-order
        # first wave (Brave/Yandex/Google/UniversalScraper), then later
        # query variants unlock the remaining providers only if the scene
        # is still unresolved. Starting all 12 lanes for every scene/query
        # created hundreds of live requests and left every worker waiting
        # at the shared stage deadline. Tiered first wave (spec order):
        # the first query fans out to the four spec transports only;
        # later query variants unlock all enabled fast sources. The old
        # _FAST_SOURCES[:5] tier smuggled in primary_archive — a Bing
        # re-skin. Every wave is then de-duplicated to
        # at most one Bing-family lane.
        if q_idx == 0:
            tier_one = [
                (name, kind)
                for name, kind in _enabled_fast_sources()
                if name in _TIER_ONE_SOURCES
            ]
            source_rows = tier_one or _enabled_fast_sources()
        else:
            source_rows = _enabled_fast_sources()
        source_rows = _dedupe_transport_families(source_rows)
        for src_name, src_type in source_rows:
            if src_type in ("api", "hybrid", "flickr_api", "universal"):
                # Once a topic pool is warm, a scene correction only needs
                # the fast, high-recall lanes. Sending every scene variant
                # to all nine providers recreates the request stampede
                # that made long jobs slower without improving their final
                # shortlist. muse_search stays in the set (round 4): the
                # API wave is the primary web lane now, and scene queries
                # differ from the shared topic query — the manager's
                # per-query cache keeps it to one wave per distinct query.
                if (
                    shared_pool_candidates
                    and not exhaustive_fallback
                    and not is_shared_query
                    and src_name not in _WARM_POOL_PER_SCENE_SOURCES
                ):
                    continue
                # The universal lane already fanned out once for the shared
                # topic. Avoid multiplying three-engine requests for every
                # scene query; existing provider lanes remain the per-scene
                # fallback. A single-scene job (no shared pool) still uses it.
                if (
                    src_name == "universal_scraper"
                    and not exhaustive_fallback
                    and discovery_cache is None
                    and not universal_scene_fallback
                ):
                    # The standalone/legacy resolver path has no shared
                    # discovery cache. Keep the universal fan-out as a true
                    # last-resort there so existing provider lanes can return
                    # immediately and callers/tests that inject those lanes
                    # do not incur an unexpected second network batch. The
                    # main job path still warms this lane through the shared
                    # pool before scenes resolve.
                    deferred_universal_task = (src_name, src_type, q_str)
                    continue
                # The universal scraper is the web-image path by deliberate,
                # permanent operator instruction: it is never skipped for a
                # scene query just because the shared topic pool already
                # fanned out once. Scene queries differ from the topic query,
                # so the scraper runs for them as well.
                if source_health is not None and not source_health.healthy(src_name):
                    continue
                api_tasks.append((src_name, src_type, q_str))
        
        if not api_tasks and deferred_universal_task is None:
            return 0.0
        if not api_tasks and deferred_universal_task is not None:
            # No established provider is available, so the deferred lane is
            # promoted directly rather than constructing an empty executor.
            start = time.time()
            if not _deadline_reached():
                source_name, source_type, query = deferred_universal_task
                _harvest_source_query(source_name, source_type, query, max_timeout=8)
            return time.time() - start

        if _web_verbose():
            _web_log(
                f"scene {idx + 1} provider wave lanes="
                f"{','.join(name for name, _kind, _query in api_tasks) or 'none'}"
            )

        if is_shared_query and discovery_cache is not None:
            # Consume only completed batch entries. Never create one waiter
            # thread per scene for a shared provider that is still in flight.
            completed_cache_entries = 0
            for source_name, _source_type, _query in api_tasks:
                cached = discovery_cache.peek(source_name, q_str)
                if cached is None:
                    continue
                completed_cache_entries += 1
                _accept_source_rows(source_name, cached)
            # A cached provider response may be present but semantically
            # rejected for this scene. Continue into uncached/secondary lanes
            # in that case; otherwise one stale warm response becomes a false
            # zero-result short circuit.
            if completed_cache_entries and (discovered_candidates or not semantic_requirement):
                return 0.0

        try:
            # Round 13: in wait mode the per-scene wave has no 90 s
            # cap — the wave waits until every provider settles (each
            # provider is bounded by its own network timeout). The
            # 90 s hard cap in _bounded_web_seconds killed slow
            # providers mid-flight even when the job was told to wait.
            if wait_until_complete:
                query_deadline = float("inf")
            else:
                default_query_seconds = 15.0 if fast_mode else 30.0
                query_seconds = _bounded_web_seconds(
                    query_seconds_override
                    if query_seconds_override is not None
                    else os.environ.get(
                        "VIDZEO_WEB_QUERY_DEADLINE_SECONDS",
                        str(default_query_seconds),
                    )
                    or default_query_seconds,
                    default_query_seconds,
                    90.0,
                    floor=0.25,
                )
                query_deadline = time.monotonic() + query_seconds
        except Exception:
            query_deadline = (
                float("inf")
                if wait_until_complete
                else time.monotonic() + (10.0 if fast_mode else 15.0)
            )
        _eff_dl = _effective_deadline_at()
        if _eff_dl:
            query_deadline = min(query_deadline, float(_eff_dl))
        try:
            enough_candidates = max(
                1,
                int(
                    os.environ.get(
                        "VIDZEO_WEB_QUERY_EARLY_CANDIDATES",
                        str(_vision_shortlist_limit()),
                    )
                    or _vision_shortlist_limit()
                ),
            )
        except Exception:
            enough_candidates = 4
        collect_all_sources = str(
            # The shared warm pool already fans out across every enabled
            # provider. For scene-specific correction, stop after a useful
            # two-provider shortlist so a slow/blocked lane cannot consume
            # the entire scene deadline. Set the env switch to 1 for a
            # diagnostic exhaustive provider benchmark.
            os.environ.get("VIDZEO_WEB_COLLECT_ALL_SOURCES", "0")
        ).strip().lower() in {"1", "true", "yes", "on"}
        # A broad source map does not require waiting for every provider after
        # two healthy lanes have already supplied a useful shortlist.  This is
        # the common production path: the shared warm-up has already fanned
        # out across Yandex/Bing/Flickr/Openverse, while this scene wave
        # only needs a quick subject-specific correction.  Keep the explicit
        # collect-all switch available for diagnostics and source benchmarks.
        adaptive_fast_stop = str(
            os.environ.get("VIDZEO_WEB_ADAPTIVE_FAST_STOP", "1")
        ).strip().lower() in {"1", "true", "yes", "on"}
        if exhaustive_fallback:
            # Exhaustive means the job can progress through every source tier
            # before emergency stock; it does not mean every scene must wait
            # for every provider on every query. Continue collecting the
            # bounded shortlist from concurrent sources, then move on.
            collect_all_sources = str(
                os.environ.get("VIDZEO_WEB_EXHAUSTIVE_COLLECT_ALL_SOURCES", "0")
            ).strip().lower() in {"1", "true", "yes", "on"}
            adaptive_fast_stop = str(
                os.environ.get("VIDZEO_WEB_EXHAUSTIVE_ADAPTIVE_FAST_STOP", "1")
            ).strip().lower() in {"1", "true", "yes", "on"}

        if wait_until_complete:
            # A wait-mode web job is explicitly allowed to finish its entire
            # provider wave. Do not stop after a numeric shortlist and do not
            # cancel slower complementary sources; they may contain the only
            # usable high-quality or identity-correct candidate for a scene.
            collect_all_sources = True
            adaptive_fast_stop = False

        # A warmed shared topic pool is already metadata-filtered and contains
        # the candidate rows for every scene. Do not issue a redundant provider
        # request for each scene-specific query; rank the warm pool first and
        # only fan out when it did not yield enough usable candidates.
        # A broad shared pool can satisfy ordinary documentary scenes after
        # metadata ranking, but it cannot satisfy a listicle item's identity:
        # ``Famous Shipwrecks`` rows may fill the shortlist while containing
        # no Lusitania/Vasa/Bismarck pixels.  Keep the bounded item-specific
        # provider wave alive for topic-wide/listicle jobs even when the pool
        # already has enough rows numerically.
        # The warm pool is itself a completed tier, so this short-circuit
        # also applies in exhaustive/rescue mode: GTA test 11 (job 676bf2cf)
        # had 60 shared-batch rows in hand, yet every rescue scene re-fanned
        # out a per-scene universal wave and burned the whole 175s stage
        # wall waiting for it — 0 downloads.  Skipping the redundant wave
        # here does not make rescue less exhaustive: if the pooled rows
        # fail scoring/download, the next query iteration runs its own wave
        # and the slow harvest still runs when nothing scored.
        if (
            shared_pool_candidates
            and len(discovered_candidates) >= enough_candidates
            and not topic_wide_for_query
            and not _scene_specific_wave_required
        ):
            return 0.0

        start = time.time()
        # Wave progress record (GTA fix): every wave emits engine
        # completions + rows arrived here, readable by the driver via
        # get_web_wave_progress(wave_id) / web_wave_progress_snapshot().
        wave_id = (
            f"scene-{idx + 1}-{int(time.monotonic() * 1000)}-{uuid.uuid4().hex[:6]}"
        )
        wave = _web_wave_progress_new(wave_id, scene_number=idx + 1, query=q_str)
        pool = ThreadPoolExecutor(
            max_workers=min(len(api_tasks), max(1, int(api_worker_limit or 8))),
            thread_name_prefix="web-query",
        )
        futures = {
            pool.submit(_fetch_source_query, name, stype, q_str, q_idx=q_idx): name
            for name, stype, _query in api_tasks
        }
        pending = set(futures)
        accepted_sources: set[str] = set()
        universal_detached = False
        try:
            while pending:
                now = time.monotonic()
                universal_pending = any(
                    futures[future] == "universal_scraper" for future in pending
                )
                if not universal_pending and now >= query_deadline:
                    break
                if universal_pending and now >= query_deadline:
                    # The wave deadline is NOT a cap for the universal lane
                    # (user override, Nov 16: no time limit for web — being
                    # a bit late beats falling back to graphics). Keep
                    # waiting while the wave is making progress; only a
                    # provably dead wave gives up. Progress is any engine
                    # completion or row arrival in this wave, or any engine
                    # completion in the universal manager (its clock covers
                    # engines still working inside the batch). The scene /
                    # stage deadline above still bounds the wait — that
                    # outer wall belongs to the stage budget, not this wave.
                    last_progress = max(
                        float(wave.get("last_progress_at") or 0.0),
                        _universal_last_progress_at(),
                    )
                    if now - last_progress >= _web_liveness_window_seconds():
                        _web_wave_progress_mark_stalled(
                            wave_id, reason="web_retrieval_stalled"
                        )
                        _web_log(
                            f"scene {idx + 1} web wave stalled: no progress "
                            f"for {_web_liveness_window_seconds():.1f}s "
                            f"(wave {wave_id})"
                        )
                        break
                    _eff_dl = _effective_deadline_at()
                    if _eff_dl is not None and now >= float(_eff_dl):
                        break
                done, pending = wait(
                    pending,
                    timeout=0.20,
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    continue
                for future in done:
                    source_name = futures[future]
                    try:
                        rows = future.result() or []
                    except Exception:
                        rows = []
                    accepted = _accept_source_rows(source_name, rows)
                    _web_wave_progress_touch(
                        wave_id,
                        engines_completed=1,
                        rows_arrived=int(accepted),
                        event=f"{source_name}:+{int(accepted)}",
                    )
                    if _web_verbose():
                        _web_log(
                            f"scene {idx + 1} provider complete: {source_name} "
                            f"rows={len(rows)} accepted={accepted}"
                        )
                    if accepted > 0:
                        accepted_sources.add(source_name)
                # A completed provider with useful rows is enough to begin
                # ranking/downloading. Do not make it wait for a throttled
                # secondary provider. The universal scraper is exempt: it is
                # the web-image path by operator instruction, so a wave must
                # not close (and cancel its future) while it is still in
                # flight. The query deadline still bounds the wait for the
                # non-universal lanes; the universal lane is liveness-gated.
                # Exception (GTA test 11, job 676bf2cf): when a warmed shared
                # pool is already in hand, the wave already holds a full
                # shortlist, so waiting out a slow universal wave adds no
                # recall — it burned the whole 175s rescue wall with 60
                # shared rows and 0 downloads. Break and rank/download the
                # pool; the detached universal future's rows still land in
                # the late spool for later queries. Without a warm pool the
                # wave keeps waiting for universal, as before.
                universal_pending = any(
                    futures[future] == "universal_scraper" for future in pending
                )
                warm_pool_in_hand = any(
                    bool(row.get("_from_shared_batch"))
                    for row in discovered_candidates
                )
                # Muse-search early-stop (Nov 22, test 30 fix): rows from
                # the muse wave are API rows in hand, exactly like a warm
                # pool — once the wave holds enough of them, do not wait
                # out a pending universal future; detach it to the late
                # spool instead of holding the wave open.
                muse_rows_in_hand = any(
                    str(row.get("source") or "") == "muse_search"
                    for row in discovered_candidates
                )
                if len(discovered_candidates) >= enough_candidates and not collect_all_sources and (not universal_pending or warm_pool_in_hand or muse_rows_in_hand):
                    break
                # Even with collect-all enabled, stop the per-scene correction
                # wave once it has a diverse shortlist.  The shared warm pool
                # remains multisource; this avoids paying a 15s tail timeout
                # for providers that cannot improve the shortlist.
                if (
                    adaptive_fast_stop
                    and len(discovered_candidates) >= max(enough_candidates, 6)
                    and len(accepted_sources) >= 2
                    and (not universal_pending or warm_pool_in_hand or muse_rows_in_hand)
                ):
                    _web_log(
                        f"scene {idx + 1} adaptive provider stop: "
                        f"{len(discovered_candidates)} candidates from "
                        f"{len(accepted_sources)} sources"
                    )
                    break
        finally:
            # Round 13: in wait mode never cancel a pending provider —
            # the wave only exits early on an exception here, and a
            # cancelled provider's rows would be lost. Wait for each
            # pending future and harvest its rows (each provider is
            # bounded by its own network timeout).
            if wait_until_complete and pending:
                for future in list(pending):
                    source_name = futures.get(future)
                    try:
                        rows = future.result() or []
                    except Exception:
                        rows = []
                    try:
                        _accept_source_rows(source_name, rows)
                    except Exception:
                        pass
                    _web_wave_progress_touch(
                        wave_id,
                        engines_completed=1,
                        rows_arrived=int(len(rows or [])),
                        event=f"{source_name}:+{int(len(rows or []))} (wait-mode harvest)",
                    )
            for future in pending:
                source_name = futures.get(future)
                if source_name == "universal_scraper" and future.running():
                    # Never cancel a live universal future (GTA fix): the
                    # wave abandoned it at 15s on GTA VI and its rows were
                    # lost. A detached future keeps running in the pool;
                    # its rows land in the manager's late spool and the
                    # pre-broadened harvest recovers them.
                    universal_detached = True
                    _web_wave_progress_touch(
                        wave_id, event="universal_detached"
                    )
                    _web_log(
                        f"scene {idx + 1} universal future detached; rows "
                        f"recoverable via late spool (wave {wave_id})"
                    )
                    continue
                if wait_until_complete:
                    # Already harvested above; nothing to cancel.
                    continue
                future.cancel()
            pool.shutdown(
                wait=not universal_detached,
                cancel_futures=False,
            )
        # Free last-resort metasearch recovery. Keep it out of the normal
        # stampede, but give a completely empty scene one additional source
        # family before it is declared unresolved.
        searx_configured = bool(
            str(os.environ.get("VIDZEO_SEARXNG_ENABLE", "0")).strip().lower()
            in {"1", "true", "yes", "on"}
            or os.environ.get("VIDZEO_SEARXNG_URLS")
            or os.environ.get("VIDZEO_SEARXNG_URL")
        )
        if not discovered_candidates and searx_configured and not searx_fallback_used and not _deadline_reached():
            searx_fallback_used = True
            _harvest_source_query("searxng_images", "api", q_str, max_timeout=8)
        # Standalone resolver calls use the universal three-engine scraper only
        # after the established fast providers produced no usable candidates.
        # This preserves the layered fallback contract without multiplying
        # requests when a primary provider already succeeded.
        if (
            not discovered_candidates
            and deferred_universal_task is not None
            and not _deadline_reached()
        ):
            source_name, source_type, query = deferred_universal_task
            _harvest_source_query(source_name, source_type, query, max_timeout=8)
        return time.time() - start

    def _run_slow_harvest_single(q_str: str) -> float:
        if _deadline_reached():
            return 0.0
        slow_tasks = []
        for src_name, src_type in _SLOW_SOURCES:
            if src_name == "reddit" and str(os.environ.get("VIDZEO_WEB_ENABLE_REDDIT", "0")).strip().lower() not in {"1", "true", "yes", "on"}:
                continue
            if src_type == "gdl" and not gdl:
                # gallery-dl is optional; do not schedule empty subprocess
                # lanes when the binary is unavailable.
                continue
            if source_health is not None and not source_health.healthy(src_name):
                continue
            slow_tasks.append((src_name, src_type, q_str))
        
        if not slow_tasks:
            return 0.0
        
        start = time.time()
        pool = ThreadPoolExecutor(max_workers=min(len(slow_tasks), 3), thread_name_prefix="web-slow-query")
        futures = {
            pool.submit(
                _fetch_source_query,
                name,
                stype,
                q_str,
                10 if stype == "gdl" else 5,
            ): name
            for name, stype, _query in slow_tasks
        }
        pending = set(futures)
        slow_seconds = 12.0 if gdl else 5.0
        slow_deadline = min(
            float(_effective_deadline_at())
            if _effective_deadline_at()
            else time.monotonic() + 5.0,
            time.monotonic() + slow_seconds,
        )
        try:
            while pending and time.monotonic() < slow_deadline:
                done, pending = wait(
                    pending,
                    timeout=min(0.20, max(0.0, slow_deadline - time.monotonic())),
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    source_name = futures[future]
                    try:
                        rows = future.result() or []
                    except Exception:
                        rows = []
                    _accept_source_rows(source_name, rows)
                if discovered_candidates:
                    break
        finally:
            for future in pending:
                future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
        return time.time() - start


    selected_candidate = None
    duplicate_fallback = None
    # One per-scene download summary line (§4.3), at first selection.
    _download_summary_logged = [False]
    # True once streaming vision hard-fails any candidate in this scene.
    # A vision-rejected scene must be rescued by the inline chain (which
    # re-ranks the row as general_related and re-submits it to vision),
    # never by the duplicate fallback below: the duplicate row was never
    # vision-judged for THIS scene, and letting it pre-empt the chain
    # ships an unvetted row that the post-retrieval pass then drops —
    # the scene dies despite the chain holding a valid rescue.
    streaming_vision_rejected = False
    flickr_prefetch_pool: List[Dict] = []

    def _prefetch_flickr_pool(query_list: List[str]) -> None:
        nonlocal flickr_prefetch_pool
        # Only consume the already-warmed shared Flickr row here.  The former
        # implementation synchronously issued three Flickr searches per scene
        # before the deadline-aware provider wave even started.
        broad_queries = list(dict.fromkeys(str(q).strip() for q in query_list if str(q).strip()))[:1]
        if not broad_queries:
            return
        if _web_verbose():
            _web_log(f"scene {idx + 1} Flickr-first prefetch: {broad_queries}")
        if source_health is not None and not source_health.healthy("flickr_api"):
            return
        merged: List[Dict] = []
        for fq in broad_queries:
            if discovery_cache is not None:
                cached = discovery_cache.peek("flickr_api", fq)
            else:
                cache_key = ("flickr_api", fq)
                with _CACHE_LOCK:
                    cached = _SEARCH_QUERY_CACHE.get(cache_key)
            if cached is None:
                continue
            merged.extend(cached or [])
        seen_prefetch = set()
        for item in merged:
            url = item.get("url")
            safe, _reason = candidate_is_safe(item)
            if safe and url and url not in seen_prefetch:
                seen_prefetch.add(url)
                flickr_prefetch_pool.append(item)

    _prefetch_flickr_pool(queries)

    try:
        shared_shortlist_target = max(
            1,
            int(
                os.environ.get(
                    "VIDZEO_WEB_QUERY_EARLY_CANDIDATES",
                    str(_vision_shortlist_limit()),
                )
                or _vision_shortlist_limit()
            ),
        )
    except (TypeError, ValueError):
        shared_shortlist_target = max(1, _vision_shortlist_limit())

    # A shared pool is anchored to the video topic, not to every scene facet.
    # Before deciding that the pool is sufficient, check whether it actually
    # contains a discriminating word from this scene (Southampton, iceberg,
    # lifeboat, a listicle item name, and so on).  If it does not, one bounded
    # scene query is allowed to repair the facet; later query variants still
    # use the warmed pool and cannot multiply provider traffic indefinitely.
    _shared_scene_terms = {
        token.rstrip("s")
        for token in re.findall(r"[a-z0-9]{4,}", str(main_subject or "").casefold())
        if token not in STOPWORDS and token.rstrip("s") not in {
            t.rstrip("s")
            for t in re.findall(r"[a-z0-9]{4,}", str(shared_pool_query or "").casefold())
        }
    }
    _shared_scene_terms -= {
        "photo", "photograph", "photography", "historical", "history",
        "archive", "archival", "image", "images", "scene", "number",
        "famous", "most", "best", "ship", "ships", "liner", "vessel",
    }

    def _shared_pool_has_scene_anchor() -> bool:
        if not shared_pool_candidates or not _shared_scene_terms:
            return bool(not _shared_scene_terms)
        for row in shared_pool_candidates:
            metadata = " ".join(
                str(row.get(field) or "")
                for field in ("title", "tags", "description", "source_url", "page_url")
            ).casefold()
            if any(
                re.search(r"(?<![a-z0-9])" + re.escape(term) + r"s?(?![a-z0-9])", metadata)
                for term in _shared_scene_terms
            ):
                return True
        return False

    _shared_pool_scene_anchor_present = _shared_pool_has_scene_anchor()

    _shared_topic_tokens = {
        token.rstrip("s")
        for token in re.findall(r"[a-z0-9]{4,}", str(shared_pool_query or "").casefold())
        if token not in STOPWORDS and token.rstrip("s") not in {
            "photo", "photograph", "photography", "historical", "history",
            "archive", "archival", "image", "images", "scene", "number",
            "famous", "most", "best",
        }
    }
    _topic_wide_job_fallback = bool(
        re.search(r"\b(?:top|number|list|famous|most|best)\b", str(global_topic or "").casefold())
        or re.search(r"\b(?:top|number)\s*\d+\b", str(global_topic or ""), re.I)
    )
    _shared_fallback_block_terms = {
        "minecraft", "turbosquid", "wallpaper", "wallpapers", "fan art",
        "fanart", "3d model", "3d render", "digital art", "ai generated",
        "animation", "game asset", "wikia", "fandom", "stock photo",
        "shutterstock", "gettyimages", "alamy", "istockphoto", "123rf",
        "lego", "toy model", "game screenshot", "concept art", "roleplay",
        "model kit", "plastic kit", "mural", "poster",
        "radiokp", "datuopinion", "blogspot",
    }

    # The shared topic pool is a recovery lane, not a license to accept any
    # vaguely named image. Keep it permissive for imperfect provider metadata,
    # but above the clearly off-topic tail observed in the long-job benchmark.
    _shared_fallback_min_score = -5.0
    # Item-specific listicle queries are stronger evidence than a broad pool
    # even when the provider caption is terse or localized.  Let those rows
    # reach the pixel/vision gate with a wider bounded ordering floor; the
    # query is only a retrieval hint and can never self-approve an image.
    _topic_query_pending_min_score = -10.0

    def _is_youtube_thumbnail(item: Mapping[str, Any]) -> bool:
        """Keep YouTube thumbnails in the YouTube lane, never web stills."""
        if not isinstance(item, Mapping):
            return False
        source = str(item.get("source") or item.get("provider") or "").strip().casefold()
        if "youtube" in source or source in {"yt", "youtube_v2", "youtube_pool_moment"}:
            return False
        urls = " ".join(
            str(item.get(field) or "")
            for field in ("url", "image_url", "source_url", "page_url", "preview_url")
        ).casefold()
        return bool(
            re.search(r"(?:^|[/:.])(?:i\.)?ytimg\.com(?:[/:?]|$)", urls)
            or re.search(r"(?:^|[/:.])(?:www\.)?youtube\.com(?:[/:?]|$)", urls)
            or re.search(r"(?:^|[/:.])youtu\.be(?:[/:?]|$)", urls)
        )

    def _shared_topic_fallback_allowed(item: Mapping[str, Any]) -> bool:
        """Allow broad-pool rescue only for clean, topic-anchored assets."""
        if not isinstance(item, Mapping) or not item.get("_from_shared_batch"):
            return False
        metadata = " ".join(
            str(item.get(field) or "")
            for field in ("title", "url", "source_url", "page_url", "tags", "description")
        ).casefold()
        if any(term in metadata for term in _shared_fallback_block_terms):
            return False
        host = urllib.parse.urlparse(str(item.get("url") or "")).netloc.casefold()
        if "ytimg.com" in host or "youtube.com" in host:
            return False
        # A broad search caption can mention the individual words in an
        # unrelated sentence (for example an Egyptian pharaoh described as a
        # member of a ``royal family`` and a ``British`` explorer).  Treating
        # that incidental overlap as a topic hit was the reason the warm pool
        # either produced misleading stills or was later discarded wholesale.
        # Require the complete topic phrase in authored metadata, or two
        # distinct topic tokens in the compact title/tag/source fields.  The
        # longer description remains useful for ranking but cannot by itself
        # bless an unrelated image.
        compact_metadata = " ".join(
            str(item.get(field) or "")
            for field in ("title", "tags", "source_url", "page_url")
        ).casefold()
        topic_phrase = re.sub(r"\s+", " ", str(shared_pool_query or "")).strip().casefold()
        phrase_match = bool(topic_phrase and topic_phrase in metadata)
        compact_hits = sum(
            1
            for token in dict.fromkeys(_shared_topic_tokens)
            if re.search(
                r"(?<![a-z0-9])" + re.escape(token) + r"s?(?![a-z0-9])",
                compact_metadata,
            )
        )
        topic_match = bool(phrase_match or compact_hits >= min(2, len(_shared_topic_tokens)))
        # Yandex and some metasearch adapters often return localized titles
        # that omit the English subject. For documentary jobs the original
        # query is still a useful topic anchor; downstream pixel/vision gates
        # remain responsible for rejecting an actually unrelated image. Keep
        # this provenance rescue disabled for listicles, where a broad pool
        # must not substitute a generic shipwreck for a named item.
        if not topic_match and not _topic_wide_job_fallback:
            query_blob = str(item.get("query") or item.get("search_query") or "").casefold()
            topic_match = bool(_shared_topic_tokens and any(
                re.search(r"(?<![a-z0-9])" + re.escape(token) + r"s?(?![a-z0-9])", query_blob)
                for token in _shared_topic_tokens
            ))
        if not topic_match:
            return False
        return True

    def _topic_query_visual_pending(item: Mapping[str, Any]) -> bool:
        """Keep an item-specific search hit for the pixel-level gate.

        Listicle providers frequently omit the item name from a caption even
        when the request was anchored to that item. Query text is never proof;
        this marker only prevents metadata prefiltering before pixel checks.
        Generic shared-topic queries are excluded by requiring a
        discriminating item token in the query.
        """
        if not _topic_wide_job_fallback or not isinstance(item, Mapping):
            return False
        query_blob = str(item.get("query") or item.get("query_used") or "").casefold()
        subject_blob = str(main_subject or "").casefold()
        anchor_terms = _extract_discriminating_subject_terms(main_subject)
        if not anchor_terms:
            anchor_terms = {
                token for token in re.findall(r"[a-z0-9]{4,}", subject_blob)
                if token not in STOPWORDS and token not in _WEB_CONTAINER_TERMS
            }
        if not anchor_terms or not any(
            re.search(r"(?<![a-z0-9])" + re.escape(term.rstrip("s")) + r"s?(?![a-z0-9])", query_blob)
            for term in anchor_terms
        ):
            return False
        metadata = " ".join(
            str(item.get(field) or "")
            for field in ("title", "url", "source_url", "page_url", "tags", "description")
        )
        return not _web_junk_metadata(metadata, metadata, metadata, "", "")

    for q_idx, q in enumerate(queries):
        if _deadline_reached():
            _web_log(f"scene {idx + 1} retrieval deadline reached before query {q_idx + 1}")
            break
        if _web_verbose():
            _web_log(f"scene {idx + 1} trying query {q_idx + 1}/{len(queries)}: '{q}'")
        discovered_candidates.clear()
        seen_urls.clear()

        # The shared warm-up rows are the fast deterministic recovery source.
        # Inject them for semantic jobs as well: metadata is only a pending
        # handoff at this point and the downloaded pixels still have to pass
        # the coordinator's visual identity gate.  Previously semantic rows
        # skipped this pool entirely, so a successful Yandex/Flickr warm-up
        # became zero scene candidates under the V2 contract.
        # Named-person/event contracts must give their exact scene query first
        # refusal.  A broad "British Royal Family" warm pool can contain
        # trees, cars, protests, or another royal; feeding those rows into the
        # ranking pass before the exact wave made every scene spend its wall
        # vetting the wrong pool and often finish with a misleading still.
        # Keep the shared pool as a later recall fallback (or for ordinary
        # non-person scenes), after the scene-specific provider wave has had a
        # chance to return identity-bearing candidates.
        _exact_person_scene_first = bool(
            semantic_requirement
            and _is_biographical_person(main_subject)
            and shared_pool_query
            and q.casefold() != str(shared_pool_query).strip().casefold()
        )
        # A downloaded warm-pool row is already local and can be handed to
        # the authoritative vision gate immediately, even when an exact
        # person query has first refusal. This avoids re-downloading a valid
        # candidate in recovery paths and keeps the provider wave as a true
        # fallback when the local row is rejected.
        local_warm_candidate = bool(
            shared_pool_candidates
            and any(
                Path(str(row.get("path") or "")).is_file()
                for row in shared_pool_candidates
            )
        )
        if shared_pool_candidates and (
            q_idx > 0
            and q.casefold() == str(shared_pool_query).strip().casefold()
            or q_idx == 0 and (not _exact_person_scene_first or local_warm_candidate)
        ):
            _accept_source_rows("shared_batch", list(shared_pool_candidates))


        # The warmed topic pool is already the fast harvest for legacy
        # (non-semantic) scene contracts. Skipping per-scene provider setup
        # keeps the shared batch within its wall budget and prevents queued
        # workers from reaching their first query after the stage deadline.
        # A shared pool already contains the provider fan-out for this topic.
        # Avoid repeating the same network query for every scene, but if the
        # semantic gate rejected every pooled row, give this scene's specific
        # query one bounded provider attempt before moving on.
        # Topic-wide/listicle jobs need a different rule: their warmed pool is
        # intentionally broad (for example ``Famous Shipwrecks``), while each
        # scene has a concrete item (RMS Titanic, Vasa, etc.).  Treating that
        # generic pool as scene-complete suppresses the only query that can
        # return the item's actual image.  Run the scene-specific wave for
        # those jobs, but keep the shared-pool fast path for documentary jobs
        # whose active subjects are variations of the canonical title.
        _title_lower = str(global_topic or "").casefold()
        _topic_wide_job = bool(
            re.search(r"\b(?:top|number|list|famous|most|best)\b", _title_lower)
            or re.search(r"\b(?:top|number)\s*\d+\b", str(global_topic or ""), re.I)
        )
        _scene_has_specific_anchor = bool(
            str(main_subject or "").strip()
            and str(main_subject or "").strip().casefold()
            != str(shared_pool_query or "").strip().casefold()
            and str(scene_text or "").strip()
        )
        _scene_specific_wave_required = bool(
            shared_pool_candidates
            and _scene_has_specific_anchor
            and (
                q_idx == 0
                and (
                    _topic_wide_job
                    or not _shared_pool_scene_anchor_present
                    or str(main_subject or "").strip().casefold()
                    != str(shared_pool_query or "").strip().casefold()
                )
            )
            and q.casefold() != str(shared_pool_query or "").strip().casefold()
        )
        # A scripted construction/action beat can share the same named
        # subject as the warm pool while needing different pixels. Give an
        # LLM-authored scene query one bounded provider wave before accepting
        # broad establishing shots from that pool.
        if (
            prioritize_director_query
            and shared_pool_candidates
            and q_idx == 0
            and q.casefold() != str(shared_pool_query or "").strip().casefold()
        ):
            _scene_specific_wave_required = True
        # Once the warm pool has supplied a bounded shortlist, documentary
        # scenes do not need another provider wave. Listicle/topic-wide jobs
        # keep their item-specific query because the pool is intentionally
        # broad for those contracts.
        if (
            _scene_specific_wave_required
            and not _topic_wide_job
            and not prioritize_director_query
            and len(discovered_candidates) >= shared_shortlist_target
            # A broad pool can be numerically full while still containing no
            # usable identity rows for a person scene (for example a Royal
            # Family search returning palace, protest, or unrelated editorial
            # photos).  Keep the bounded subject query alive in that case so
            # the fast Yandex/Bing/Flickr lanes can supply identity-bearing
            # candidates.  Without this guard the shared pool short-circuits
            # before the first scene query and every scene later reaches the
            # media contract as neutral.
            and not (
                _is_biographical_person(main_subject)
                and (
                    # Distinct people in one documentary need a local query
                    # even when a broad shared pool happens to mention the
                    # same surname/role.  Running one bounded person wave is
                    # cheap compared with accepting a royal-family gallery
                    # image from the wrong subject when vision is degraded.
                    bool(semantic_requirement)
                    or sum(
                        1
                        for row in shared_pool_candidates
                        if _metadata_has_identity(
                            str(row.get("title") or ""),
                            str(row.get("url") or ""),
                            str(row.get("tags") or ""),
                            identity_terms,
                        )
                    ) < max(2, min(shared_shortlist_target, 3))
                )
            )
        ):
            _scene_specific_wave_required = False
        if _scene_specific_wave_required and _web_verbose():
            _web_log(
                f"scene {idx + 1} bounded scene-specific wave: "
                f"subject='{main_subject[:48]}', query='{q[:72]}'"
            )
        # Process a contract-matched warm-pool hit before starting a provider
        # wave.  In the production log, exhaustive recovery waited on every
        # provider for every scene even when the shared batch already held
        # downloaded candidates; the stage deadline then discarded all 24
        # unfinished futures before any candidate reached vision.  The warm
        # row is only a provisional candidate (the semantic/visual gates still
        # own acceptance).  If it cannot be downloaded or selected, the next
        # query below still runs the full layered fallback.  Topic-wide jobs
        # are excluded because their shared pool may belong to a different
        # list item than the scene.
        local_subject_candidate = any(
            str(item.get("path") or "")
            and Path(str(item.get("path"))).is_file()
            and _metadata_candidate_class(
                item, main_subject=main_subject, scene_text=score_scene_text,
            )[0] in {"anchored", "anchored_generic"}
            and _passes_semantic_metadata(item)
            for item in discovered_candidates
        )
        warm_candidate_first = bool(
            q_idx == 0
            and semantic_requirement
            and shared_pool_candidates
            and discovered_candidates
            # A broad topic pool is useful recall, but it must not short
            # circuit an exact named-person/event query.  The coordinator's
            # live vision gate can reject the broad row (correctly), yet the
            # resolver would then spend its recovery wall rediscovering the
            # same family/tree/protest images.  Run the bounded scene-specific
            # provider wave first whenever the immutable contract says this
            # scene has a distinct anchor.
            and (
                not _scene_specific_wave_required
                or (local_subject_candidate and not prioritize_director_query)
            )
            and not _topic_wide_job
        )
        if warm_candidate_first:
            _web_log(
                f"scene {idx + 1} warm-pool first: "
                f"{len(discovered_candidates)} contract-compatible candidate(s); "
                "deferring provider wave until after local ranking/vision handoff"
            )
        elif (
            exhaustive_fallback
            or not shared_pool_candidates
            or not discovered_candidates
            or _scene_specific_wave_required
        ):
            _run_api_harvest_single(q)

        # Exact person and director facet queries own the first handoff. A
        # shared brand pool can contain exteriors, merchandise or unrelated
        # products; those rows must not crowd out touchscreen/battery shots.
        # Let the authoritative vision gate see the exact-query pixels first,
        # then fall back to the shared pool only when the exact wave returned
        # too little recall.
        if (
            _scene_specific_wave_required
            and (_is_biographical_person(main_subject) or prioritize_director_query)
        ):
            _specific_rows = [
                row for row in discovered_candidates
                if not row.get("_from_shared_batch")
            ]
            if len(_specific_rows) >= 2:
                discovered_candidates[:] = _specific_rows

        # Pre-score all discovered URLs to select the best ones for downloading
        scored_urls = []
        for item in discovered_candidates:
            p_score = _pre_score_candidate(
                url=item["url"],
                title=item["title"],
                source=item["source"],
                query=item["query"],
                scene_text=score_scene_text,
                identity_terms=identity_terms,
                main_subject=main_subject,
                tags=item.get("tags", ""),
                description=item.get("description", ""),
                source_url=item.get("source_url", ""),
                weak_terms=weak_terms,
                allow_topic_pending=bool(item.get("topic_query_pending")),
            )
            p_score = _defer_identity_candidate(item, p_score)
            if item.get("semantic_visual_pending") and p_score <= -50.0:
                # Semantic rows have already passed the contract or been
                # explicitly marked pending. A stricter legacy topic prefilter
                # must not erase that handoff before the downloaded pixels are
                # inspected by the visual gate.
                p_score = 0.25
            if _passes_semantic_metadata(item):
                scored_urls.append((_provisional_visual_score(item, p_score), item))
            
        # Only fallback to slower sources if fast sources found nothing at all.
        # Weak fast candidates are still worth downloading before walking more queries.
        if not scored_urls and flickr_prefetch_pool:
            if _web_verbose():
                _web_log(f"scene {idx + 1} using Flickr-first broad pool before slow fallback")
            with results_lock:
                for item in flickr_prefetch_pool:
                    url = item.get("url")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        discovered_candidates.append(item)
            for item in discovered_candidates:
                p_score = _pre_score_candidate(
                    url=item["url"],
                    title=item["title"],
                    source=item["source"],
                    query=item["query"],
                    scene_text=score_scene_text,
                    identity_terms=identity_terms,
                    main_subject=main_subject,
                    tags=item.get("tags", ""),
                    description=item.get("description", ""),
                    source_url=item.get("source_url", ""),
                    weak_terms=weak_terms,
                    allow_topic_pending=bool(item.get("topic_query_pending")),
                )
                p_score = _defer_identity_candidate(item, p_score)
                if item.get("semantic_visual_pending") and p_score <= -50.0:
                    p_score = 0.25
                if _passes_semantic_metadata(item):
                    scored_urls.append((_provisional_visual_score(item, p_score), item))

        should_run_slow_recall = bool(
            not scored_urls
            and (
                exhaustive_fallback
                or not (shared_pool_candidates and discovered_candidates)
            )
            and (
                not fast_mode
                # Keep fast production bounded, but give a semantically locked
                # final query one scraper recovery window before declaring the
                # web lane exhausted.
                or (gdl and semantic_requirement and q_idx == len(queries) - 1)
            )
        )
        if should_run_slow_recall:
            if _web_verbose():
                _web_log(f"scene {idx + 1} query '{q}' did not find high-relevance fast candidates. Triggering slow fallbacks...")
            old_count = len(discovered_candidates)
            _run_slow_harvest_single(q)
            # Pre-score only the newly added slow candidates
            new_candidates = discovered_candidates[old_count:]
            for item in new_candidates:
                p_score = _pre_score_candidate(
                    url=item["url"],
                    title=item["title"],
                    source=item["source"],
                    query=item["query"],
                    scene_text=score_scene_text,
                    identity_terms=identity_terms,
                    main_subject=main_subject,
                    tags=item.get("tags", ""),
                    description=item.get("description", ""),
                    source_url=item.get("source_url", ""),
                    weak_terms=weak_terms,
                    allow_topic_pending=bool(item.get("topic_query_pending")),
                )
                p_score = _defer_identity_candidate(item, p_score)
                if _passes_semantic_metadata(item):
                    scored_urls.append((_provisional_visual_score(item, p_score), item))

        if not scored_urls:
            if _web_verbose():
                _web_log(f"scene {idx + 1} query '{q}' returned 0 candidate URLs. Trying next fallback...")
            continue
            
        # Sort descending by pre-score
        scored_urls.sort(key=lambda x: x[0], reverse=True)

        # When a broad shared topic pool is present, the pool can contain a
        # numerically strong row for a different person/event (for example a
        # generic royal-family gallery).  A bounded scene-specific wave is
        # deliberately launched for those contracts, so its rows must have
        # first refusal.  Previously ``shared_preferred_url`` was moved to
        # index zero after every wave, which meant the exact-query results
        # were downloaded only as alternates and the broad row was handed to
        # the visual gate.  Keep shared rows as recall fallbacks, but rank
        # rows discovered by the exact query ahead of them whenever the wave
        # produced at least one candidate.
        if _scene_specific_wave_required and shared_pool_candidates:
            specific_rows = [
                pair for pair in scored_urls
                if not pair[1].get("_from_shared_batch")
            ]
            shared_rows = [
                pair for pair in scored_urls
                if pair[1].get("_from_shared_batch")
            ]
            if specific_rows:
                scored_urls = specific_rows + shared_rows

        # A warmed topic pool is shared by many scene workers. Reserve the
        # strongest topic row for each scene before workers start so completion
        # order cannot make scene 17 steal scene 2's exact image. If the
        # reserved row fails to download, the normal ranked fallbacks remain.
        if shared_preferred_url and not _scene_specific_wave_required:
            for _preferred_index, (_preferred_score, _preferred_item) in enumerate(scored_urls):
                if _preferred_item.get("url") == shared_preferred_url:
                    _preferred = scored_urls.pop(_preferred_index)
                    scored_urls.insert(0, _preferred)
                    break
        if shared_pool_query and shared_pool_candidates and idx > 0 and len(scored_urls) > 1:
            # A warmed topic pool is shared by all scenes. Offset each scene's
            # ranked walk so workers do not stampede the same first URLs and
            # exhaust the host/download budget before later scenes run.
            offset = (idx * 8) % len(scored_urls)
            scored_urls = scored_urls[offset:] + scored_urls[:offset]
        if (
            shared_pool_query
            and re.sub(r"\s+", " ", q).strip().casefold()
            == re.sub(r"\s+", " ", shared_pool_query).strip().casefold()
            and len(scored_urls) > 1
        ):
            # Every scene sees the same batch rows. Rotate only candidates tied
            # at the best relevance score; rotating the entire ranked pool used
            # to push a scene's exact match behind the following scene's asset
            # (for example lifeboats and iceberg swapping places). Exact ties
            # still fan out across the pool for visual diversity, while a real
            # scene-specific relevance advantage always wins.
            top_relevance = scored_urls[0][0]
            tie_count = 1
            while (
                tie_count < len(scored_urls)
                and abs(scored_urls[tie_count][0] - top_relevance) <= 0.01
            ):
                tie_count += 1
            if tie_count > 1:
                tied = scored_urls[:tie_count]
                rotation = idx % tie_count
                scored_urls = tied[rotation:] + tied[:rotation] + scored_urls[tie_count:]
        if shared_preferred_url and not _scene_specific_wave_required:
            for _preferred_index, (_preferred_score, _preferred_item) in enumerate(scored_urls):
                if _preferred_item.get("url") == shared_preferred_url:
                    _preferred = scored_urls.pop(_preferred_index)
                    scored_urls.insert(0, _preferred)
                    break
        
        # Adaptive candidate count based on top match pre-score. A strong
        # result still keeps a full same-query page so the visual gate and
        # shot planner have real alternatives instead of one overused still.
        top_score = scored_urls[0][0] if scored_urls else -100.0
        dl_limit = _same_query_candidate_limit(max_candidate_downloads, top_score)
        if fast_mode:
            remaining_downloads = scene_download_budget - scene_downloaded_candidates
            if remaining_downloads <= 0:
                if _web_verbose():
                    _web_log(
                        f"scene {idx + 1} fast download budget exhausted "
                        f"({scene_downloaded_candidates}/{scene_download_budget})"
                    )
                break
            # Apply the scene budget to high-scoring queries too. A metadata
            # score does not authorize an unbounded download wave.
            dl_limit = min(dl_limit, fast_per_query, remaining_downloads)
        
        # Download only candidates that have not already failed the metadata
        # floor. A blank/degraded scene is safer than a known wrong image.
        strict_person_identity = _is_biographical_person(main_subject)
        eligible_candidates = [
            (score, item) for score, item in scored_urls
            if (
                not _is_youtube_thumbnail(item)
                and score > -3.0
                # Semantic rows are recall candidates for the authoritative
                # shared vision gate.  Their lexical score is only an
                # ordering hint; rejecting them here is what turned healthy
                # Yandex/Bing/Flickr pools into an empty web handoff.  Safety
                # and explicit junk checks have already run above, so let
                # these pending rows reach pixel verification.
                or (
                    semantic_requirement
                    and defer_context_verification
                    and (
                        item.get("semantic_visual_pending")
                        or item.get("semantic_context_pending")
                        or item.get("_from_shared_batch")
                    )
                )
                or (
                    item.get("_from_shared_batch")
                    and _shared_topic_fallback_allowed(item)
                    and score >= _shared_fallback_min_score
                )
                or (
                    item.get("topic_query_pending")
                    and score >= _topic_query_pending_min_score
                )
            )
            and not (
                shared_reserved_owners
                and item.get("url") in shared_reserved_owners
                and shared_reserved_owners.get(item.get("url")) != idx
            )
            and (
                not strict_person_identity
                or item.get("semantic_context_pending")
                or item.get("semantic_visual_pending")
                or item.get("nearest_match_pending")
                or item.get("topic_query_pending")
                or _metadata_has_identity(item.get("title", ""), item.get("url", ""), "", identity_terms)
            )
            and item.get("url") not in scene_attempted_urls
        ]
        # Filter before limiting. Previously a sibling's reserved URL or a
        # metadata rejection consumed a shortlist slot, hiding usable rows
        # farther down the same provider page.
        top_candidates = [item for _score, item in _diverse_download_candidates(eligible_candidates, dl_limit)]
        urls_to_download = [item["url"] for item in top_candidates]
        
        if not urls_to_download:
            if _web_verbose():
                score_values = [float(score) for score, _item in scored_urls]
                pending_count = sum(
                    1 for _score, _item in scored_urls
                    if _item.get("topic_query_pending")
                )
                _web_log(f"scene {idx + 1} query '{q}' had no candidates with acceptable pre-score. Trying next fallback...")
                _web_log(
                    f"scene {idx + 1} score diagnostics: scored={len(scored_urls)} "
                    f"range={min(score_values):.2f}..{max(score_values):.2f} "
                    f"topic_pending={pending_count} strict_person={strict_person_identity}"
                    if score_values else
                    f"scene {idx + 1} score diagnostics: scored=0"
                )
            continue
            
        if _web_verbose():
            _web_log(f"scene {idx + 1} downloading top {len(urls_to_download)} candidate images...")
        # A single dead editorial host must not hold a long job for the
        # full provider deadline; the per-URL and batch deadlines still
        # bound it.  See _web_image_download_timeout_seconds for the 60s
        # default (test 23: the old 15s timed out 50/118 attempts).
        _image_download_timeout = _web_image_download_timeout_seconds(fast_mode)
        # Shared discovery has already fetched and decoded its warm shortlist.
        # Reusing those local assets avoids a second request to the original
        # publisher URL (which commonly expires, rate-limits, or blocks hotlink
        # requests) and is the difference between a warm hit and an HTTP retry.
        download_results = []
        candidates_to_fetch = []
        for candidate in top_candidates:
            local_path = Path(str(candidate.get("path") or ""))
            if local_path.is_file():
                download_results.append((candidate["url"], local_path, ""))
            else:
                candidates_to_fetch.append(candidate)
        if candidates_to_fetch:
            download_results.extend(_download_batch_with_outage_recovery(
                candidates_to_fetch,
                scene_dir,
                outage_tracker=outage_tracker,
                cancel_event=cancel_event,
                deadline=_effective_deadline_at,
                max_concurrent=min(
                    max(2, int(os.environ.get("VIDZEO_WEB_SCENE_DOWNLOAD_CONCURRENCY", "4") or 4)),
                    len(candidates_to_fetch),
                ),
                timeout_seconds=_image_download_timeout,
                download_cache=download_cache,
                download_cache_lock=download_cache_lock,
                download_stats=scene_download_stats,
                # Round 13: in wait mode the downloads run to
                # resolution — no batch wall, no slot-wait cap.
                wait_until_complete=wait_until_complete,
            ))
        scene_downloaded_candidates += len(urls_to_download)
        scene_attempted_urls.update(urls_to_download)

        # Recover from dead publisher URLs using the page we already fetched.
        # Do not repeat an expensive provider search just because its first
        # hosts timed out. All replacements still enter the full checks below.
        while fast_mode and scene_downloaded_candidates < scene_download_budget:
            if _deadline_reached():
                break
            usable = sum(
                1 for _url, path, _err in download_results
                if path and path.is_file() and _validate_image(path) is not None
            )
            if usable >= 2:
                break
            _eff_dl = _effective_deadline_at()
            remaining_seconds = (_eff_dl - time.monotonic()) if _eff_dl else _image_download_timeout
            if remaining_seconds <= 1.0:
                break
            replacements = [
                item for _score, item in _diverse_download_candidates(
                    [(score, item) for score, item in eligible_candidates
                     if item.get("url") not in scene_attempted_urls],
                    min(fast_per_query, scene_download_budget - scene_downloaded_candidates),
                )
            ]
            if not replacements:
                break
            scene_attempted_urls.update(item["url"] for item in replacements)
            scene_downloaded_candidates += len(replacements)
            top_candidates.extend(replacements)
            download_results.extend(_download_batch_with_outage_recovery(
                replacements, scene_dir,
                outage_tracker=outage_tracker,
                cancel_event=cancel_event,
                deadline=_effective_deadline_at,
                max_concurrent=min(4, len(replacements)),
                timeout_seconds=min(_image_download_timeout, remaining_seconds),
                download_cache=download_cache, download_cache_lock=download_cache_lock,
                download_stats=scene_download_stats,
                # Round 13: in wait mode the downloads run to
                # resolution — no batch wall, no slot-wait cap.
                wait_until_complete=wait_until_complete,
            ))

        download_errors = {}
        for url, path, err in download_results:
            provider = next((item.get("source") or "unknown" for item in top_candidates if item["url"] == url), "unknown")
            event = "downloaded" if path and path.exists() else "download_failed"
            if source_telemetry is not None:
                source_telemetry.record(provider, event)
                if event == "download_failed" and str(err).startswith("HTTP "):
                    source_telemetry.record(provider, "download_http_" + str(err).split()[1])
            if event == "download_failed":
                reason = str(err or "missing file")
                download_errors[reason] = download_errors.get(reason, 0) + 1
        if download_errors:
            _summary = f"scene {idx + 1} download failures: {download_errors}"
            if scene_download_stats:
                # Medians separate slot waits from slow transfers, so
                # the next forensics pass can tell which one killed a
                # download (test 30 could not: no per-download durations).
                def _median(values: List[float]) -> float:
                    ordered = sorted(values)
                    if not ordered:
                        return 0.0
                    mid = len(ordered) // 2
                    if len(ordered) % 2:
                        return ordered[mid]
                    return (ordered[mid - 1] + ordered[mid]) / 2.0

                _transfers = [
                    float(r.get("transfer_ms") or 0.0)
                    for r in scene_download_stats
                ]
                _waits = [
                    float(r.get("slot_wait_ms") or 0.0)
                    for r in scene_download_stats
                ]
                _routes = Counter(
                    str(r.get("route") or "proxy") for r in scene_download_stats
                )
                _summary += (
                    f" (median transfer {_median(_transfers) / 1000.0:.1f}s, "
                    f"median slot-wait {_median(_waits) / 1000.0:.1f}s, "
                    f"routes proxy={_routes.get('proxy', 0)} "
                    f"direct={_routes.get('direct', 0)})"
                )
            _web_log(_summary)
        
        url_to_path = {}
        for url, path, err in download_results:
            if path and path.exists():
                url_to_path[url] = path

        # Validate, score, and evaluate candidates
        scene_phashes = set()
        validated_paths = set()
        candidates = []
        # Downloaded rows that fail the lexical gates below are kept here
        # instead of being erased: when the verified pool ends up empty,
        # the best deferred rows are promoted as vision-pending
        # alternates (proof job a57cb5c7: scene 0 downloaded 8, kept 1).
        deferred_pool = []
        
        for item in top_candidates:
            url = item["url"]
            fpath = url_to_path.get(url)
            if not fpath or not fpath.exists():
                continue
                
            dims = _validate_image(fpath)
            if dims is None:
                continue
            safe, reason = image_is_safe(
                fpath,
                title=item.get("title", ""),
                url=url,
                tags=item.get("tags", ""),
                source=item.get("source", ""),
                description=item.get("description", ""),
                source_url=item.get("source_url", ""),
                page_url=item.get("page_url", ""),
                preview_url=item.get("preview_url", ""),
            )
            if not safe:
                if _artifact_safety_may_defer(item, semantic_requirement, reason, defer_context_verification):
                    item['semantic_visual_pending'] = True
                    item['semantic_context_pending'] = True
                    evidence = dict(item.get('candidate_evidence') or {})
                    evidence.update(artifact_safety_pending=reason,
                                    requirement_hash=semantic_requirement['requirement_hash'],
                                    verification_required=True)
                    item['candidate_evidence'] = evidence
                else:
                    _web_log(f"scene {idx + 1} blocked unsafe image ({reason})")
                    continue
            w, h = dims
            
            aspect = w / max(h, 1)
            if aspect < 0.5:
                continue
                
            if fpath in validated_paths:
                continue
            validated_paths.add(fpath)
            
            phash = _compute_average_hash(str(fpath))
            if phash is not None:
                if phash in scene_phashes:
                    continue
                scene_phashes.add(phash)
                
            # Final scoring (takes dimensions and aspect ratio into account)
            score = _score_candidate(
                fpath,
                w,
                h,
                item["query"],
                score_scene_text,
                identity_terms,
                item["source"],
                item["title"],
                url,
                item.get("tags", ""),
                item.get("description", ""),
                item.get("source_url", ""),
                weak_terms=weak_terms,
                strict_subject=strict_subject,
            )
            score = _defer_identity_candidate(item, score)
            if item.get("semantic_visual_pending") and score < 0.0:
                score = 0.25
            if item.get("semantic_visual_pending") and can_defer_context_verification:
                # This is only a bounded ordering score. The URL/query still
                # cannot prove identity; visual verification is mandatory.
                score = max(0.25, score)
            
            # Provider titles may be synthetic retrieval echoes (for example
            # ``<query> result 3``).  They are deliberately excluded from
            # identity proof; only the sanitized asset metadata and filename
            # can mark a downloaded row as identity-backed.
            asset_metadata = candidate_asset_metadata(item)
            identity_failed = bool(identity_terms and not any(
                t in fpath.name.lower()
                or t in url.lower()
                or t in asset_metadata.lower()
                for t in identity_terms
            ))
            if (
                _is_biographical_person(main_subject)
                and identity_failed
                and not item.get("semantic_context_pending")
                and not item.get("semantic_visual_pending")
                and not item.get("nearest_match_pending")
                and not item.get("topic_query_pending")
                # A warm topic row can be a legitimate archival image whose
                # caption omits the person's name.  It is already safety
                # screened and explicitly marked for the pixel gate; keep it
                # in the bounded shortlist so vision can reject only a clear
                # mismatch instead of collapsing the whole web lane.
                and not (
                    item.get("_from_shared_batch")
                    and item.get("semantic_visual_pending")
                    and _shared_topic_fallback_allowed(item)
                )
            ):
                continue
            context_pending = bool(item.get("semantic_context_pending"))
            soft_visual_pending = bool(
                semantic_requirement
                and defer_context_verification
                and (
                    item.get("semantic_visual_pending")
                    or item.get("semantic_context_pending")
                    or item.get("_from_shared_batch")
                )
            )
            pending_score_ok = context_pending and score >= -3.0
            shared_topic_fallback = bool(
                _shared_topic_fallback_allowed(item)
                and score >= _shared_fallback_min_score
            )
            topic_query_pending = bool(
                item.get("topic_query_pending")
                and score >= _topic_query_pending_min_score
            )
            candidate_row = {
            "path": str(fpath),
            "width": w, "height": h,
             "url": url, "domain": item.get("domain") or item["source"],
            "relevance": score,
            "quality_score": min(w, h) / 2000.0,
            "final_score": score,
             "title": item["title"] or f"scene_{idx + 1}",
              "source_url": item.get("source_url") or item.get("page_url") or url,
              "page_url": item.get("page_url") or item.get("source_url") or url,
              "preview_url": item.get("preview_url") or item.get("thumbnail_url") or "",
              "provider": item.get("provider") or item.get("source", ""),
             "license": item.get("license", ""),
             "license_url": item.get("license_url", ""),
             "attribution": item.get("attribution", ""),
             "creator": item.get("creator", ""),
             # Kept so deferred Wikimedia rights enrichment can find the
             # Commons page at manifest time (muse-search §2.2).
             "page_id": item.get("page_id", ""),
             "query_used": item["query"],
             "tags": item.get("tags", ""),
             "description": item.get("description", ""),
             "identity_failed": identity_failed,
            "semantic_visual_pending": bool(item.get("semantic_visual_pending")),
            "semantic_context_pending": bool(item.get("semantic_context_pending")),
            "candidate_evidence": dict(item.get("candidate_evidence") or {}),
            "discovery_method": item.get("discovery_method", ""),
            "phash": phash,
            }
            if (
                score < 0.0
                and not pending_score_ok
                and not shared_topic_fallback
                and not topic_query_pending
                and not soft_visual_pending
            ):
                deferred_pool.append((score, dict(candidate_row)))
                continue
            if not _passes_web_relevance_floor(
                {**item, "path": str(fpath), "query_used": item.get("query", q)},
                score,
                main_subject,
                score_scene_text,
            ) and not shared_topic_fallback and not topic_query_pending and not soft_visual_pending:
                deferred_pool.append((score, dict(candidate_row)))
                continue
            
            # Soft gate: only reject truly terrible identity-failed candidates
            if (
                identity_failed
                and score < 0.0
                and not context_pending
                and not shared_topic_fallback
                and not soft_visual_pending
            ):
                deferred_pool.append((score, dict(candidate_row)))
                continue
            
            candidates.append((score, candidate_row))

        if not candidates and deferred_pool:
            # The metadata floor erased the whole downloaded pool (proof
            # job scene 0: 8 downloads -> 1 candidate; scene 2: 6 -> 0).
            # Promote the best deferred rows as vision-pending alternates
            # instead of leaving the scene with nothing: vision is the
            # validator and still rejects each wrong pixel, but a scene
            # with deferred candidates can now pick a DISTINCT image
            # instead of reusing a neighbour's still or going graphic.
            # Legacy-cutscene rows are never promoted.
            candidates.extend(_promote_deferred(deferred_pool, limit=3))

        if not candidates:
            if _web_verbose():
                _web_log(f"scene {idx + 1} query '{q}' had 0 validated images. Trying next fallback...")
            continue

        # Sort final candidates by score descending
        candidates.sort(key=lambda x: x[0], reverse=True)
        
        # Prefer identity-passing candidates: try to find one with score >= 2.5
        identity_passing = [(s, c) for s, c in candidates if not c.get("identity_failed", False) and s >= 2.5]
        
        # Identity metadata is a ranking hint, not a reason to throw away the
        # other downloaded shots before vision can compare their scene facets.
        candidate_pool = identity_passing + [row for row in candidates if row not in identity_passing]

        # Thin-pool top-up (proof job scene 0: pool=1, alternates=[]). The
        # empty-pool promotion above only fires when NOTHING verified; a
        # scene with one verified pick and seven deferred downloads still
        # had no alternates, so a neighbour's reuse was the only rescue
        # option. Top the pool up to 3 with the best deferred rows — as
        # alternates only: they are appended AFTER the verified rows, so
        # they can never outrank the winner, and vision still judges each
        # one (they carry semantic_visual_pending) before it renders.
        if 0 < len(candidate_pool) < 3 and deferred_pool:
            _pool_paths = {cand.get("path") for _score, cand in candidate_pool}
            for _score, _row in _promote_deferred(deferred_pool, limit=3):
                if len(candidate_pool) >= 3:
                    break
                if _row.get("path") in _pool_paths:
                    continue
                candidate_pool.append((_score, _row))
                _pool_paths.add(_row.get("path"))

        query_resolved = False
        for score, cand in candidate_pool:
            cand_path = cand["path"]
            cand_phash = cand.get("phash")
            cand_md5 = _compute_md5(cand_path)
            cand["md5"] = cand_md5
            
            reused = False
            
            # 1. Check MD5 content-hash dedup (count-based)
            if cand_md5 is not None:
                with md5_lock:
                    if global_md5s.get(cand_md5, 0) >= _max_reuse:
                        reused = True
                        
            # 2. Check Perceptual Hash dedup (count-based)
            if not reused and cand_phash is not None:
                with phash_lock:
                    # Snapshot while holding the lock.  A late scene worker
                    # may claim a hash immediately after this check; iterating
                    # a live dict made the production resolver abort with
                    # ``RuntimeError: dictionary changed size during iteration``.
                    for existing, count in list(global_phashes.items()):
                        if count >= _max_reuse and _hamming_distance(cand_phash, existing) < _cross_scene_phash_threshold():
                            reused = True
                            break
                            
            # 3. Check filename dedup
            fname = Path(cand_path).name
            if not reused:
                with file_lock:
                    if fname in used_files:
                        reused = True

            # 4. Check URL / source URL dedup
            cand_url = str(cand.get("url") or cand.get("source_url") or "").strip()
            if not reused and cand_url and used_urls is not None and url_lock is not None:
                with url_lock:
                    if cand_url in used_urls:
                        reused = True
                        
            if not reused:
                # Claim this candidate (count-based)
                if cand_md5 is not None:
                    with md5_lock:
                        global_md5s[cand_md5] = global_md5s.get(cand_md5, 0) + 1
                if cand_phash is not None:
                    with phash_lock:
                        global_phashes[cand_phash] = global_phashes.get(cand_phash, 0) + 1
                with file_lock:
                    used_files.add(fname)
                if cand_url and used_urls is not None and url_lock is not None:
                    with url_lock:
                        used_urls.add(cand_url)
                    
                selected_candidate = cand
                selected_candidate["reused_from_scene"] = False
                shortlist = [
                    dict(candidate)
                    for _candidate_score, candidate in candidate_pool[:_vision_shortlist_limit()]
                    if candidate.get("path")
                ]
                selected_candidate["_vision_shortlist"] = shortlist
                selected_candidate["candidate_pool_size"] = len(candidate_pool)
                selected_candidate["downloaded_candidate_count"] = len(top_candidates)
                query_resolved = True
                # Observability (§4.3): one per-scene download summary.
                # Failures are already logged per attempt; successes were
                # silent, so "0 logged successes" could not distinguish
                # "0 succeeded" from "successes unlogged".
                if not _download_summary_logged[0]:
                    _download_summary_logged[0] = True
                    try:
                        _dl_ok = sum(
                            1 for _u, _p, _e in download_results
                            if _p and Path(_p).is_file()
                        )
                        _dl_by_provider: Dict[str, list] = {}
                        for _u, _p, _e in download_results:
                            _prov = next(
                                (str(item.get("source") or "unknown") for item in top_candidates if item.get("url") == _u),
                                "unknown",
                            )
                            _slot = _dl_by_provider.setdefault(_prov, [0, 0])
                            _slot[1] += 1
                            if _p and Path(_p).is_file():
                                _slot[0] += 1
                        _dl_providers = ", ".join(
                            f"{_prov}: {_ok}/{_tried}"
                            for _prov, (_ok, _tried) in sorted(_dl_by_provider.items())
                        )
                        _web_log(
                            f"scene {idx + 1} downloads: {_dl_ok}/{len(download_results)} ok "
                            f"(providers: {_dl_providers or 'none'})"
                        )
                        # Three separate counts (muse-search design §2.5):
                        # test 30 misread vision-ready rows=0 as search
                        # rows=0. Log search / downloaded / vision-ready
                        # distinctly so the next diagnosis cannot repeat it.
                        _web_log(
                            f"scene {idx + 1} search_rows={len(discovered_candidates)} "
                            f"downloaded={_dl_ok} vision_ready={len(candidate_pool)}"
                        )
                    except Exception:
                        pass

                # Streaming Vision Queue: evaluate candidate immediately as it downloads.
                # Skipped when the V2 coordinator owns the final gate
                # (defer_context_verification): the transport context is
                # vision-dead by design, and an external-agent job forces
                # it enabled with a 75s fuse — the only thing stopping it
                # spending its 17 calls in-branch is that the fuse has
                # usually burned out by branch start (run 7 audit:
                # visual_deadline_exceeded, 0/17 calls).  Never spend
                # vision here on a deferred job, whatever the fuse state.
                if (
                    visual_pool is not None
                    and visual_context is not None
                    and not defer_context_verification
                    and shortlist
                ):
                    try:
                        v_future = visual_pool.submit(
                            _rank_deferred_scene_shortlist,
                            scene_index=idx,
                            scene_text=scene_text,
                            subject=main_subject,
                            global_topic=global_topic or main_subject,
                            query=str(cand.get("query_used") or q or ""),
                            candidates=shortlist,
                            visual_context=visual_context,
                        )
                        _eff_dl = _effective_deadline_at()
                        v_remaining = max(1.0, float(_eff_dl - time.monotonic()) if _eff_dl else 18.0)
                        v_timeout = min(18.0, v_remaining)
                        v_result = v_future.result(timeout=v_timeout)
                        if v_result and v_result.get("visual_outcome") == "pass":
                            selected_candidate = _preserve_downloaded_shortlist(
                                v_result, shortlist, len(candidate_pool), len(top_candidates)
                            )
                            selected_candidate["_streaming_vision_passed"] = True
                            if _web_verbose():
                                _web_log(f"scene {idx + 1} streaming vision passed on query '{q}' (score={v_result.get('visual_score')})")
                            break
                        elif v_result and v_result.get("visual_outcome") == "hard_fail":
                            if _web_verbose():
                                _web_log(f"scene {idx + 1} streaming vision rejected candidate on query '{q}'; trying next candidate")
                            streaming_vision_rejected = True
                            # Roll the claim back: a vision-rejected
                            # candidate must stay eligible for the inline
                            # general_related chain (and for later queries)
                            # — otherwise the first pass consumes every
                            # row into used_files / used_urls /
                            # global_phashes and the chain finds nothing
                            # to rescue the scene with.  The
                            # post-retrieval visual pass / coordinator
                            # gate still validates any row the chain
                            # re-picks, so nothing unvetted ships.
                            if cand_md5 is not None:
                                with md5_lock:
                                    _left = global_md5s.get(cand_md5, 0) - 1
                                    if _left > 0:
                                        global_md5s[cand_md5] = _left
                                    else:
                                        global_md5s.pop(cand_md5, None)
                            if cand_phash is not None:
                                with phash_lock:
                                    _left = global_phashes.get(cand_phash, 0) - 1
                                    if _left > 0:
                                        global_phashes[cand_phash] = _left
                                    else:
                                        global_phashes.pop(cand_phash, None)
                            with file_lock:
                                used_files.discard(fname)
                            if cand_url and used_urls is not None and url_lock is not None:
                                with url_lock:
                                    used_urls.discard(cand_url)
                            selected_candidate = None
                            query_resolved = False
                            continue
                        elif v_result and v_result.get("path"):
                            selected_candidate = _preserve_downloaded_shortlist(
                                v_result, shortlist, len(candidate_pool), len(top_candidates)
                            )
                            break
                    except Exception as ve:
                        if _web_verbose():
                            _web_log(f"scene {idx + 1} streaming vision check bypassed ({type(ve).__name__})")
                        break
                break
            if duplicate_fallback is None:
                duplicate_fallback = cand

        if query_resolved:
            if _web_verbose():
                _web_log(f"scene {idx + 1} successfully resolved with query '{q}'")
            # A pending row is only a recall handoff to the final pixel-level
            # verifier.  In a locked scene, the first query can return a
            # plausible domain reference whose metadata is merely incidental
            # (for example an alpine photo whose long description mentions a
            # railway station).  Do not let that provisional hit stop the
            # remaining canonical/action query waves.  The later waves are
            # where the provider can return an actual role/entity image, and
            # the final verifier still decides whether any downloaded pixels
            # are acceptable.
            pending_only = bool(
                semantic_requirement
                and candidate_pool
                and all(
                    bool(candidate.get("semantic_context_pending")
                         or candidate.get("semantic_visual_pending"))
                    for _candidate_score, candidate in candidate_pool
                )
            )
            if pending_only and q_idx + 1 < len(queries):
                # A pending row is already a downloaded, safety-screened
                # shortlist for the authoritative pixel gate.  Running every
                # remaining semantic query here multiplies a six-provider
                # request wave per scene; on a 24-scene job that is what
                # turned a healthy warm pool into a 120s web stall.  The
                # canonical typed query is deliberately first (portrait,
                # photo, landmark, etc.), and the coordinator retains the
                # shortlist/alternates plus a bounded cross-lane web retry if
                # vision rejects them. Keep one extra wave by default, with
                # an optional third wave for difficult archive subjects.
                pending_wave_limit = _semantic_pending_wave_limit()
                if (q_idx + 1) >= pending_wave_limit:
                    if _web_verbose():
                        _web_log(
                            f"scene {idx + 1} retaining provisional result after "
                            f"{q_idx + 1} semantic wave(s); final visual gate owns acceptance"
                        )
                    break
                # Keep a usable provisional row when the next canonical wave
                # cannot fit in the remaining retrieval budget.  A slow
                # provider (or a throttled image host) used to leave the
                # worker inside that next download wave until the stage
                # deadline, at which point the completed first result was
                # discarded with the cancelled future and the scene became
                # an empty placeholder.
                _eff_dl = _effective_deadline_at()
                if _eff_dl is not None:
                    try:
                        remaining = float(_eff_dl) - time.monotonic()
                    except (TypeError, ValueError):
                        remaining = 0.0
                    try:
                        next_wave_budget = max(
                            15.0,
                            float(query_seconds_override or 8.0),
                        )
                    except (TypeError, ValueError):
                        next_wave_budget = 8.0
                    if remaining <= next_wave_budget + 2.0:
                        if _web_verbose():
                            _web_log(
                                f"scene {idx + 1} retaining provisional result; "
                                f"only {remaining:.1f}s remain for another query wave"
                            )
                        break
                if _web_verbose():
                    _web_log(
                        f"scene {idx + 1} retained provisional pending results; "
                        "continuing canonical query waves"
                    )
                continue
            break
        else:
            if _web_verbose():
                _web_log(f"scene {idx + 1} query '{q}' yielded only duplicate images. Trying next fallback...")

    # Late-spool harvest (GTA fix, Nov 16): engines that finished after
    # their wave gave up leave rows in the universal manager's late spool,
    # keyed by exact query string — and each scene's queries are unique and
    # never repeated, so without this harvest every late row is orphaned by
    # construction. Before the broadened round (which spends fresh provider
    # budget on new queries), re-check the spool for every query this scene
    # already asked. The wait is capped by the scene's remaining wall so
    # the harvest can never push the scene past its own deadline.
    if selected_candidate is None and not _deadline_reached():
        try:
            harvest_wait = max(
                0.0,
                min(
                    300.0,
                    float(
                        os.environ.get(
                            "VIDZEO_UNIVERSAL_LATE_WAIT_SECONDS", "120"
                        )
                        or 120
                    ),
                ),
            )
        except (TypeError, ValueError):
            harvest_wait = 120.0
        _eff_dl = _effective_deadline_at()
        if _eff_dl is not None:
            try:
                harvest_wait = max(
                    0.0, min(harvest_wait, float(_eff_dl) - time.monotonic())
                )
            except (TypeError, ValueError):
                harvest_wait = 0.0
        harvested = _UNIVERSAL_SCRAPER.collect_late_results_for_queries(
            queries, wait_seconds=harvest_wait
        )
        if harvested:
            harvest_rows = [
                universal_result_to_candidate(item, query=queries[0] if queries else "")
                for item in harvested
            ]
            harvest_rows = [row for row in harvest_rows if row]
            if harvest_rows:
                _accept_source_rows("universal_scraper", harvest_rows)
                _sel = _download_and_accept_single_candidate(
                    harvest_rows[0], scene_dir, idx, scene_text,
                    identity_terms, main_subject, global_phashes, global_md5s, used_files,
                    phash_lock, md5_lock, file_lock, weak_terms)
                if _sel:
                    if _web_verbose():
                        _web_log(
                            f"scene {idx + 1} successfully rescued via "
                            "late-spool harvest!"
                        )
                    selected_candidate = _sel

    # Broadened-query round: the specific query waves above all failed.
    # Before the fallbacks, progressively drop the most specific terms
    # from the scene's own queries (the `broaden` flag is honoured here;
    # it was previously accepted but never used). The universal scraper
    # fans each broadened query out to all four engines with Yandex
    # first, so this is one bounded multi-engine wave, not a
    # per-provider retry loop. This round runs BEFORE the emergency
    # subject fallback below: the bare canonical subject must stay the
    # final query attempted (see
    # test_full_query_pack_reserves_canonical_subject).
    if selected_candidate is None and broaden and not _deadline_reached():
        try:
            from .query import _broaden_scene_queries
        except Exception:
            _broaden_scene_queries = None
        if _broaden_scene_queries:
            broadened = _broaden_scene_queries(queries, main_subject)
            for _bq in broadened:
                if _deadline_reached():
                    break
                if _web_verbose():
                    _web_log(f"scene {idx + 1} broadened query: '{_bq}'")
                _bq_rows = _fetch_source_query("universal_scraper", "universal", _bq)
                if not _bq_rows:
                    continue
                _sel = _download_and_accept_single_candidate(
                    _bq_rows[0], scene_dir, idx, scene_text,
                    identity_terms, main_subject, global_phashes, global_md5s, used_files,
                    phash_lock, md5_lock, file_lock, weak_terms)
                if _sel:
                    if _web_verbose():
                        _web_log(f"scene {idx + 1} successfully rescued via broadened query '{_bq}'!")
                    selected_candidate = _sel
                    break

    # Emergency Rescue Fallback:
    # If all query options failed, run an immediate emergency subject search across multi-engine
    # providers to guarantee we find a valid image before considering any graphic fallback.
    is_strict_person = bool(
        semantic_requirement
        and (
            str(semantic_requirement.get("subject_type") or "").strip().lower() in {"person", "royal", "celebrity"}
            or str(semantic_requirement.get("domain") or "").strip().lower() in {"person", "royal", "celebrity"}
        )
        and bool(semantic_requirement.get("strict_identity"))
    )
    rescue_subject = str((semantic_requirement.get("canonical_entity") if semantic_requirement else None) or main_subject or "").strip()
    if (
        selected_candidate is None
        and rescue_subject
        and not is_strict_person
        and not shared_pool_candidates
        and scene_downloaded_candidates < scene_download_budget
    ):
        if _web_verbose():
            _web_log(f"scene {idx + 1} trigger EMERGENCY SUBJECT FALLBACK search for subject '{rescue_subject}'...")
        try:
            from .query import _is_person_fallback_subject, _topic_fallback_queries
        except Exception:
            _is_person_fallback_subject = None
            _topic_fallback_queries = None
        is_person_subject = bool(_is_person_fallback_subject and _is_person_fallback_subject(rescue_subject))
        if is_person_subject and any(w in str(rescue_subject).lower() for w in ("train", "freight", "locomotive", "railway", "railroad", "diesel", "car", "engine")):
            is_person_subject = False
        if is_person_subject:
            emergency_queries = [f"{rescue_subject} portrait", f"{rescue_subject} photo", rescue_subject]
        else:
            scene_kws = _scene_visual_keywords(scene_text, max_kws=4) if _scene_visual_keywords else []
            emergency_queries = []
            if _topic_fallback_queries:
                emergency_queries.extend(_topic_fallback_queries(rescue_subject, scene_text, limit=6))
            if len(scene_kws) >= 2:
                emergency_queries.append(f"{rescue_subject} {' '.join(scene_kws[:2])}")
            if scene_kws:
                emergency_queries.append(f"{rescue_subject} {scene_kws[0]}")
            emergency_queries.append(f"{rescue_subject} photograph")
            emergency_queries.append(f"{rescue_subject} documentary")
            if rescue_subject:
                emergency_queries.append(rescue_subject)
        
        # De-duplicate queries we already tried
        emergency_queries = [eq for eq in emergency_queries if eq.lower() not in [q.lower() for q in queries]]
        
        for eq in emergency_queries:
            if _deadline_reached():
                _web_log(f"scene {idx + 1} retrieval deadline reached during emergency fallback")
                break
            if _web_verbose():
                _web_log(f"scene {idx + 1} trying emergency query: '{eq}'")
            discovered_candidates.clear()
            seen_urls.clear()
            _run_api_harvest_single(eq)
            
            scored_urls = []
            for item in discovered_candidates:
                p_score = _pre_score_candidate(
                    url=item["url"],
                    title=item["title"],
                    source=item["source"],
                    query=item["query"],
                    scene_text=score_scene_text,
                    identity_terms=identity_terms,
                    main_subject=main_subject,
                    tags=item.get("tags", ""),
                    description=item.get("description", ""),
                    source_url=item.get("source_url", ""),
                    weak_terms=weak_terms,
                    allow_topic_pending=bool(item.get("topic_query_pending")),
                )
                p_score = _defer_identity_candidate(item, p_score)
                if _passes_semantic_metadata(item):
                    scored_urls.append((_provisional_visual_score(item, p_score), item))
            
            if not scored_urls:
                continue
                
            scored_urls.sort(key=lambda x: x[0], reverse=True)
            # Keep the emergency rescue path on the same adaptive candidate
            # budget as the normal query path.  Some providers return only
            # filtered/metadata-pending rows, so the list can be non-empty
            # while no earlier branch has initialized ``top_score``.
            top_score = scored_urls[0][0] if scored_urls else -100.0
            # Emergency queries are still real retrieval waves. Keep several
            # unique results for visual verification instead of collapsing the
            # fallback to one arbitrary image.
            dl_limit = _same_query_candidate_limit(max_candidate_downloads, top_score)
            if fast_mode and top_score < 8.0:
                remaining_downloads = scene_download_budget - scene_downloaded_candidates
                if remaining_downloads <= 0:
                    break
                dl_limit = min(dl_limit, fast_per_query, remaining_downloads)
            top_candidates = [
                item for score, item in scored_urls[:dl_limit]
                if score > -5.0 and not _is_youtube_thumbnail(item)
            ]
            if not top_candidates and scored_urls:
                top_candidates = [
                    item for _score, item in scored_urls[:dl_limit]
                    if not _is_youtube_thumbnail(item)
                ]
            urls_to_download = [item["url"] for item in top_candidates]
            
            if not urls_to_download:
                continue
                
            download_results = download_bulk(
                urls_to_download,
                scene_dir,
                max_concurrent=len(urls_to_download),
                timeout_seconds=_web_image_download_timeout_seconds(fast_mode),
                headers_by_url=_provider_download_headers(top_candidates),
            )
            scene_downloaded_candidates += len(urls_to_download)
            
            url_to_path = {}
            for url, path, err in download_results:
                if path and path.exists():
                    url_to_path[url] = path
                    
            validated_paths = set()
            candidates = []
            for item in top_candidates:
                url = item["url"]
                fpath = url_to_path.get(url)
                if not fpath or not fpath.exists():
                    continue
                dims = _validate_image(fpath)
                if dims is None:
                    continue
                safe, reason = image_is_safe(
                    fpath,
                    title=item.get("title", ""),
                    url=url,
                    tags=item.get("tags", ""),
                    source=item.get("source", ""),
                    description=item.get("description", ""),
                    source_url=item.get("source_url", ""),
                    page_url=item.get("page_url", ""),
                    preview_url=item.get("preview_url", ""),
                )
                if not safe:
                    if _artifact_safety_may_defer(item, semantic_requirement, reason, defer_context_verification):
                        item = dict(item)
                        item['semantic_visual_pending'] = True
                        item['context_visual_pending'] = True
                        evidence = dict(item.get('semantic_evidence') or {})
                        evidence.update(artifact_safety_pending=reason,
                                        requirement_hash=semantic_requirement.get('requirement_hash'),
                                        visual_verification_required=True)
                        item['semantic_evidence'] = evidence
                    else:
                        _web_log(f"scene {idx + 1} blocked unsafe emergency image ({reason})")
                        continue
                w, h = dims
                aspect = w / max(h, 1)
                if aspect < 0.5:
                    continue
                if fpath in validated_paths:
                    continue
                validated_paths.add(fpath)
                
                phash = _compute_average_hash(str(fpath))
                score = _score_candidate(
                    fpath,
                    w,
                    h,
                    item["query"],
                    score_scene_text,
                    identity_terms,
                    item["source"],
                    item["title"],
                    url,
                    item.get("tags", ""),
                    item.get("description", ""),
                    item.get("source_url", ""),
                    weak_terms=weak_terms,
                )
                
                candidates.append((score, {
                    "path": str(fpath),
                    "width": w, "height": h,
                    "url": url, "domain": item.get("domain") or item["source"],
                    "relevance": score,
                    "quality_score": min(w, h) / 2000.0,
                    "final_score": score,
                     "title": item["title"] or f"scene_{idx + 1}",
                     "source_url": item.get("source_url") or item.get("page_url") or url,
                     "page_url": item.get("page_url") or item.get("source_url") or url,
                     "preview_url": item.get("preview_url") or item.get("thumbnail_url") or "",
                     "provider": item.get("provider") or item.get("source", ""),
                     "license": item.get("license", ""),
                     "license_url": item.get("license_url", ""),
                     "attribution": item.get("attribution", ""),
                     "creator": item.get("creator", ""),
                     "query_used": item["query"],
                     "tags": item.get("tags", ""),
                     "description": item.get("description", ""),
                     "phash": phash,
                }))
                
            if not candidates:
                continue
                
            candidates.sort(key=lambda x: x[0], reverse=True)
            for score, cand in candidates:
                cand_path = cand["path"]
                cand_phash = cand.get("phash")
                cand_md5 = _compute_md5(cand_path)
                cand["md5"] = cand_md5
                
                reused = False
                if cand_md5 is not None:
                    with md5_lock:
                        if global_md5s.get(cand_md5, 0) >= _max_reuse:
                            reused = True
                if not reused and cand_phash is not None:
                    with phash_lock:
                        for existing, count in list(global_phashes.items()):
                            if count >= _max_reuse and _hamming_distance(cand_phash, existing) < _cross_scene_phash_threshold():
                                reused = True
                                break
                fname = Path(cand_path).name
                if not reused:
                    with file_lock:
                        if fname in used_files:
                            reused = True
                            
                if not reused:
                    if cand_md5 is not None:
                        with md5_lock:
                            global_md5s[cand_md5] = global_md5s.get(cand_md5, 0) + 1
                    if cand_phash is not None:
                        with phash_lock:
                            global_phashes[cand_phash] = global_phashes.get(cand_phash, 0) + 1
                    with file_lock:
                        used_files.add(fname)
                    selected_candidate = cand
                    selected_candidate["reused_from_scene"] = False
                    selected_candidate["_vision_shortlist"] = [
                        dict(candidate)
                        for _candidate_score, candidate in candidates[:_vision_shortlist_limit()]
                    ]
                    selected_candidate["candidate_pool_size"] = len(candidates)
                    selected_candidate["downloaded_candidate_count"] = len(top_candidates)
                    break
                    
            if selected_candidate is not None:
                if _web_verbose():
                    _web_log(f"scene {idx + 1} successfully rescued via emergency fallback query '{eq}'!")
                break

    # A duplicate is not automatically a relevant fallback.  The old greedy
    # branch accepted the last downloaded row regardless of its score, which
    # is how an off-topic Flickr image could beat an empty slot (and how a
    # provider error card could be carried into a later scene).  Reuse only a
    # candidate that already cleared the semantic floor and is not an
    # identity-pending row; otherwise leave the row unresolved for the
    # coordinator's grounded graphic/verified-media fallback.  And never
    # let a duplicate pre-empt the inline chain for a vision-rejected
    # scene (see streaming_vision_rejected above): the chain is the
    # designed rescue there, and the duplicate row was never judged
    # for this scene.
    if (
        selected_candidate is None
        and duplicate_fallback is not None
        and not streaming_vision_rejected
    ):
        try:
            duplicate_score = float(
                duplicate_fallback.get("final_score")
                or duplicate_fallback.get("relevance")
                or -999.0
            )
        except (TypeError, ValueError):
            duplicate_score = -999.0
        duplicate_is_grounded = bool(
            duplicate_score >= 0.0
            and not duplicate_fallback.get("identity_failed")
            and not duplicate_fallback.get("semantic_visual_pending")
            and not duplicate_fallback.get("semantic_context_pending")
        )
        if duplicate_is_grounded and not semantic_requirement:
            selected_candidate = duplicate_fallback
            selected_candidate["reused_from_scene"] = True
            if _web_verbose():
                _web_log(
                    f"scene {idx + 1} using a verified duplicate image as last fallback "
                    f"(score={duplicate_score:.2f})"
                )

    # Absolute last resort: try bare rescue_subject across multi-engine web sources
    # (Bing, Universal Scraper, Wikimedia, Openverse, Flickr API)
    # to guarantee a real image is found before falling back to graphic placeholders.
    if (
        selected_candidate is None
        and rescue_subject
        and not is_strict_person
        and not shared_pool_candidates
    ):
        _bare_queries = [
            f"{rescue_subject} photograph",
            f"{rescue_subject} documentary",
            rescue_subject,
        ]
        for _bq in _bare_queries:
            if _deadline_reached():
                break
            if _web_verbose():
                _web_log(f"scene {idx + 1} absolute last resort query: '{_bq}'")
            _found_candidates = []
            for _prov_name, _prov_type in (
                ("bing_hybrid", "hybrid"),
                ("universal_scraper", "universal"),
                ("wikimedia", "api"),
                ("openverse", "api"),
                ("flickr_api", "flickr_api"),
            ):
                _bq_cached = _fetch_source_query(_prov_name, _prov_type, _bq)
                if _bq_cached:
                    _found_candidates.extend(_bq_cached)
            if _found_candidates:
                for _bc in _found_candidates[:4]:
                    _sel = _download_and_accept_single_candidate(
                        _bc, scene_dir, idx, scene_text,
                        identity_terms, rescue_subject, global_phashes, global_md5s, used_files,
                        phash_lock, md5_lock, file_lock, weak_terms
                    )
                    if _sel:
                        selected_candidate = _sel
                        break
                if selected_candidate:
                    break

    if selected_candidate is None:
        # ── Inline per-scene web fallback chain (run-6 fix) ──────────────
        # The perfect-image first pass above failed or was rejected.  Before
        # this scene falls through to a graphic, resolve it INLINE here, in
        # the go, from work already done — no new network search, no second
        # batch from scratch at the end:
        #   (2) general_related — an already-fetched shared-pool/discovered
        #       row that is still related to the video's topic AND fits the
        #       scene (container subject + at least one scene facet in
        #       metadata: subject/era/game), already local on disk;
        #   (3a) cached_prep — a prep-banked verified image for this scene
        #       (exact key, then the fuzzy title/narration fallback);
        #   (3b) cached_download — any already-downloaded pool row that is
        #       still on-topic and not metadata-irrelevant for this scene.
        # All three layers are local-only (no network, no vision call), so
        # they run even past the stage deadline — the outer drain may still
        # accept the result.  Vision stays ONLY the validator: chain rows
        # are marked semantic_visual_pending and still flow through the
        # live visual pass / the coordinator's final gate below; a vision
        # rejection there falls to the next layer, never to a job failure.
        # The resolving layer is recorded on the row (web_fallback_layer)
        # and surfaced in the per-scene audit.
        def _accept_inline_local_row(item, local_path, layer):
            fname = Path(local_path).name
            with file_lock:
                if fname in used_files or str(local_path) in used_files:
                    return None
            if used_urls is not None:
                with url_lock:
                    if str(item.get("url") or "") in used_urls:
                        return None
            dims = _validate_image(local_path)
            if dims is None:
                return None
            w, h = dims
            if w / max(h, 1) < 0.5:
                return None
            safe, _reason = image_is_safe(
                local_path,
                title=item.get("title", ""),
                url=str(item.get("url") or ""),
                tags=item.get("tags", ""),
                source=item.get("source", ""),
                description=item.get("description", ""),
                source_url=item.get("source_url", ""),
                page_url=item.get("page_url", ""),
                preview_url=item.get("preview_url", ""),
            )
            if not safe and not _artifact_safety_may_defer(
                item, semantic_requirement, _reason, defer_context_verification
            ):
                return None
            md5 = _compute_md5(str(local_path))
            phash = _compute_average_hash(str(local_path))
            with phash_lock:
                if phash is not None and phash in global_phashes:
                    return None
                if phash is not None:
                    global_phashes[phash] = global_phashes.get(phash, 0) + 1
            with md5_lock:
                if md5 is not None:
                    global_md5s[md5] = global_md5s.get(md5, 0) + 1
            with file_lock:
                used_files.add(fname)
                used_files.add(str(local_path))
            if used_urls is not None:
                with url_lock:
                    used_urls.add(str(item.get("url") or ""))
            score = _pre_score_candidate(
                url=str(item.get("url") or ""),
                title=str(item.get("title") or ""),
                source=str(item.get("source") or ""),
                query=str(item.get("query") or item.get("query_used") or ""),
                scene_text=score_scene_text,
                identity_terms=identity_terms,
                main_subject=main_subject,
                tags=item.get("tags", ""),
                description=item.get("description", ""),
                source_url=item.get("source_url", ""),
                weak_terms=weak_terms,
            )
            row = {
                "path": str(local_path), "width": w, "height": h,
                "url": str(item.get("url") or ""),
                "domain": item.get("domain") or item.get("source", ""),
                "relevance": score, "final_score": score,
                "reused_from_scene": False,
                "title": item.get("title", "") or f"scene_{idx + 1}",
                "source_url": item.get("source_url") or item.get("page_url") or item.get("url", ""),
                "page_url": item.get("page_url") or item.get("source_url") or item.get("url", ""),
                "preview_url": item.get("preview_url") or item.get("thumbnail_url") or "",
                "provider": item.get("provider") or item.get("source", ""),
                "license": item.get("license", ""),
                "license_url": item.get("license_url", ""),
                "attribution": item.get("attribution", ""),
                "creator": item.get("creator", ""),
                "query_used": item.get("query") or item.get("query_used") or main_subject,
                "tags": item.get("tags", ""),
                "description": item.get("description", ""),
                "web_fallback_layer": layer,
                "recovery_kind": "inline_web_fallback",
                "semantic_visual_pending": True,
                "visual_verification_required": True,
                "candidate_pool_size": 0,
                "downloaded_candidate_count": 0,
            }
            if defer_context_verification:
                row["external_visual_gate_required"] = True
            return row

        def _inline_local_path(item):
            supplied = Path(str(item.get("path") or "").strip())
            if supplied and supplied.is_file():
                return supplied
            cached = (download_cache or {}).get(str(item.get("url") or "").strip())
            if cached:
                cached_path = Path(str(cached))
                if cached_path.is_file():
                    return cached_path
            return None

        _inline_pool: List[Dict[str, Any]] = []
        _inline_seen_urls: Set[str] = set()
        for _pool_row in list(discovered_candidates or []) + list(shared_pool_candidates or []):
            if not isinstance(_pool_row, dict):
                continue
            _pool_url = str(_pool_row.get("url") or "").strip()
            if not _pool_url or _pool_url in _inline_seen_urls:
                continue
            _inline_seen_urls.add(_pool_url)
            # A shared-pool row reserved as another scene's preferred image
            # stays that scene's; this scene takes its own or an unreserved row.
            _owner = (shared_reserved_owners or {}).get(_pool_url)
            if _owner is not None and int(_owner) != int(idx):
                continue
            _inline_pool.append(_pool_row)

        def _inline_topic_tokens(text):
            return {
                token
                for token in re.findall(r"[a-z0-9]+", str(text or "").lower())
                if len(token) >= 3
            }

        _inline_topic_tokens = _inline_topic_tokens(global_topic or main_subject)
        _inline_local_rows = [
            (_row, _inline_local_path(_row))
            for _row in _inline_pool
        ]
        _inline_local_rows = [
            (_row, _path) for _row, _path in _inline_local_rows if _path is not None
        ]
        # Layer (2): general related image — topic + at least one scene
        # facet in metadata (anchored/anchored_generic), already local.
        _inline_ranked = []
        for _row, _path in _inline_local_rows:
            if _has_explicit_visual_identity_conflict(_row):
                continue
            if is_strict_person and not _metadata_has_identity(
                str(_row.get("title") or ""), str(_row.get("url") or ""),
                str(_row.get("tags") or ""), identity_terms,
            ):
                continue
            _cls, _cls_reason = _metadata_candidate_class(
                _row, main_subject=main_subject, scene_text=scene_text,
            )
            if _cls not in {"anchored", "anchored_generic"}:
                continue
            _score = _pre_score_candidate(
                url=str(_row.get("url") or ""),
                title=str(_row.get("title") or ""),
                source=str(_row.get("source") or ""),
                query=str(_row.get("query") or ""),
                scene_text=score_scene_text,
                identity_terms=identity_terms,
                main_subject=main_subject,
                tags=_row.get("tags", ""),
                description=_row.get("description", ""),
                source_url=_row.get("source_url", ""),
                weak_terms=weak_terms,
            )
            if _score > -5.0:
                _inline_ranked.append((_score, _row, _path))
        _inline_ranked.sort(key=lambda entry: entry[0], reverse=True)
        for _score, _row, _path in _inline_ranked:
            selected_candidate = _accept_inline_local_row(_row, _path, "general_related")
            if selected_candidate is not None:
                selected_candidate["candidate_pool_size"] = len(_inline_pool)
                selected_candidate["downloaded_candidate_count"] = len(_inline_local_rows)
                break
        # Layer (3a): cached prep-banked image for this scene.
        if selected_candidate is None:
            try:
                from services.advance_prep import find_prepped_web_asset

                _cached = find_prepped_web_asset(
                    str(global_topic or main_subject or ""), scene_text, list(queries or []),
                )
            except Exception:
                _cached = None
            if isinstance(_cached, dict):
                _cached_source = Path(str(_cached.get("path") or ""))
                if _cached_source.is_file():
                    _cached_dest = scene_dir / f"prep_{_cached_source.name}"
                    try:
                        if not _cached_dest.is_file():
                            shutil.copy2(_cached_source, _cached_dest)
                        _cached_item = dict(_cached)
                        _cached_item.setdefault("url", str(_cached.get("source_url") or _cached_source))
                        _cached_item.setdefault("query", str(_cached.get("prep_query") or ""))
                        _cached_item.setdefault("source", "prep_cache")
                        selected_candidate = _accept_inline_local_row(
                            _cached_item, _cached_dest, "cached_prep",
                        )
                        if selected_candidate is not None:
                            selected_candidate["prep_cache_hit"] = True
                            selected_candidate["reused_from_prep"] = True
                            selected_candidate["candidate_pool_size"] = len(_inline_pool)
                            selected_candidate["downloaded_candidate_count"] = len(_inline_local_rows)
                    except Exception:
                        selected_candidate = None
        # Layer (3b): cached download — any already-downloaded pool row that
        # is still on-topic and not metadata-irrelevant for this scene.
        if selected_candidate is None:
            _inline_cached_ranked = []
            for _row, _path in _inline_local_rows:
                if _has_explicit_visual_identity_conflict(_row):
                    continue
                if is_strict_person and not _metadata_has_identity(
                    str(_row.get("title") or ""), str(_row.get("url") or ""),
                    str(_row.get("tags") or ""), identity_terms,
                ):
                    continue
                _cls, _cls_reason = _metadata_candidate_class(
                    _row, main_subject=main_subject, scene_text=scene_text,
                )
                if _cls == "irrelevant":
                    continue
                _haystack = " ".join(
                    str(_row.get(field) or "")
                    for field in ("title", "tags", "description")
                ).lower()
                if _inline_topic_tokens and not (
                    _inline_topic_tokens
                    & {t for t in re.findall(r"[a-z0-9]+", _haystack) if len(t) >= 3}
                ):
                    continue
                _score = _pre_score_candidate(
                    url=str(_row.get("url") or ""),
                    title=str(_row.get("title") or ""),
                    source=str(_row.get("source") or ""),
                    query=str(_row.get("query") or ""),
                    scene_text=score_scene_text,
                    identity_terms=identity_terms,
                    main_subject=main_subject,
                    tags=_row.get("tags", ""),
                    description=_row.get("description", ""),
                    source_url=_row.get("source_url", ""),
                    weak_terms=weak_terms,
                )
                # Same junk floor as layer (2): a last-resort row must still
                # clear the metadata junk bar — a spam/URL-only title with a
                # topic token in it is not a cached image worth rendering.
                if _score <= -5.0:
                    continue
                _inline_cached_ranked.append((_score, _row, _path))
            _inline_cached_ranked.sort(key=lambda entry: entry[0], reverse=True)
            for _score, _row, _path in _inline_cached_ranked:
                selected_candidate = _accept_inline_local_row(_row, _path, "cached_download")
                if selected_candidate is not None:
                    selected_candidate["candidate_pool_size"] = len(_inline_pool)
                    selected_candidate["downloaded_candidate_count"] = len(_inline_local_rows)
                    break
        if selected_candidate is not None:
            _web_log(
                f"scene {idx + 1} inline web fallback resolved via "
                f"{selected_candidate.get('web_fallback_layer')} "
                f"(pool={len(_inline_pool)}, local={len(_inline_local_rows)})"
            )
    if selected_candidate is None:
        _web_log(f"scene {idx + 1} all query options and fallbacks failed to find an image")
        return None

    final_safe, final_reason = image_is_safe(
        selected_candidate.get("path", ""),
        title=selected_candidate.get("title", ""),
        url=selected_candidate.get("source_url", selected_candidate.get("url", "")),
        tags=selected_candidate.get("tags", ""),
        source=selected_candidate.get("domain", ""),
        description=selected_candidate.get("description", ""),
    )
    if not final_safe and not _artifact_safety_may_defer(
        selected_candidate, semantic_requirement, final_reason, defer_context_verification
    ):
        _web_log(f"scene {idx + 1} rejected unsafe final fallback ({final_reason})")
        return None

    # Attach this scene's universal engine audit trail (engine, count,
    # duration, error) and any wave-stall flag to the winner so the
    # per-scene audit entry carries them. Unresolved scenes have no winner;
    # their trail is read from the audit-token registry instead.
    selected_candidate["universal_engine_audits"] = [
        dict(entry) for entry in universal_engine_audits
    ]
    selected_candidate["web_wave_stalled"] = web_wave_stalled_for_scene(idx + 1)
    selected_candidate.pop("phash", None)
    selected_candidate.pop("md5", None)
    return selected_candidate


def _safe_provider_score(value) -> float:
    """Coerce a provider/metadata score to float without raising ValueError."""
    try:
        return float(value)
    except Exception:
        return 0.0


def _judge_web_candidate(
    candidate, model_row, rank, *, scene_index, scene_text, subject,
    global_topic, visual_context, client,
) -> tuple:
    """Judge one single-verified candidate with the old compare-row rules.

    Returns the (qualified, score, -rank, candidate, decision, model_row)
    tuple the winner selection below ranks on. Unchanged from the
    compare path: the same normalization, contextual soft pass,
    reject-only-irrelevant rule, and explicit-conflict vetoes.
    """
    from services.visual_intelligence.decision import normalize_scene_decision, asset_fingerprint, scene_context_hash
    cand_path = str(candidate.get("path") or "")
    reject_only_irrelevant = str(
        os.environ.get("VIDZEO_VISUAL_REJECT_ONLY_IRRELEVANT", "1")
    ).strip().lower() in {"1", "true", "yes", "on"}

    cand_fp = asset_fingerprint(cand_path) if cand_path else ""
    cand_req_hash = str(candidate.get("requirement_hash") or "").strip()
    if not cand_req_hash:
        cand_req_hash = scene_context_hash(narration=scene_text, subject=subject, topic=global_topic, media_type="image")
    decision = normalize_scene_decision(
        model_row,
        scene_index=scene_index,
        media_type="image",
        source=str(candidate.get("domain") or candidate.get("provider") or ""),
        model=str(getattr(visual_context, "model", "") or getattr(client, "model", "")),
        asset_fingerprint_value=cand_fp,
        scene_context_hash_value=cand_req_hash,
        candidate_rank=rank,
        provider_metadata_score=_safe_provider_score(candidate.get("provider_metadata_score") or candidate.get("final_score") or 0.0),
    )
    unsafe = any(
        value is True
        or str(value or "").strip().lower() in {"1", "true", "yes", "on"}
        for key in (
            "adult", "adult_content", "sexual_content", "nsfw",
            "unsafe", "unsafe_content", "suggestive", "suggestive_content",
        )
        for value in [model_row.get(key)]
    )
    domain_match = any(
        str(model_row.get(key) or "").strip().lower() in {"1", "true", "yes", "on"}
        for key in ("domain_match", "same_domain", "domain_aligned")
    )
    scene_match = any(
        str(model_row.get(key) or "").strip().lower() in {"1", "true", "yes", "on"}
        for key in ("scene_match", "scene_relevance", "contextual_match", "story_match")
    )
    fatal_flag = (
        model_row.get("fatal_mismatch") is True
        or str(model_row.get("fatal_mismatch") or "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    model_reason = str(
        model_row.get("reason")
        or model_row.get("rejection_reason")
        or ""
    ).strip().lower()
    fatal_explanation = any(
        marker in model_reason
        for marker in (
            "not visible", "not shown", "not identifiable", "cannot verify",
            "can't verify", "establishing shot", "contextual", "context",
            "near-match", "near match", "indirect", "supports the scene",
            "missing exact", "exact person is not",
        )
    )
    explicit_wrong = any(
        marker in model_reason
        for marker in (
            "wrong entity", "wrong subject", "wrong domain", "different entity",
            "different subject", "wrong species", "wildlife", "unrelated",
            "irrelevant", "contradictory event", "contradicts the scene",
        )
    )
    contextual_keep = bool(
        domain_match
        and scene_match
        and not explicit_wrong
        and not (
            model_row.get("entity_conflict") is True
            or str(model_row.get("entity_conflict") or "").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        and (not fatal_flag or fatal_explanation)
        and not unsafe
    )
    if contextual_keep:
        # Preserve the model's scores for ranking, but turn a
        # borderline/under-threshold verdict into a soft pass when it
        # explicitly says the domain and scene both fit. This keeps a
        # useful establishing shot without allowing a wrong-domain
        # image through.
        decision.status = "accepted"
        decision.rejection_reasons = [
            reason for reason in decision.rejection_reasons
            if reason not in {"low_relevance", "fatal_mismatch"}
        ]
        # Keep the normalized decision internally consistent.  A
        # contextual soft pass must not carry a fatal flag into the
        # coordinator, otherwise a later contract check can discard
        # the same candidate that this gate deliberately retained.
        decision.fatal_mismatch = False
        model_row = dict(model_row)
        model_row.update({
            "domain_match": True,
            "scene_match": True,
            "contextual_match": True,
            "nearest_match": True,
            "fatal_mismatch": False,
        })
    if reject_only_irrelevant:
        reasons = set(decision.rejection_reasons or [])
        clear_irrelevance = (
            "fatal_mismatch" in reasons
            or ("low_relevance" in reasons and decision.confidence >= 70.0)
        )
        rejected = unsafe or (clear_irrelevance and not contextual_keep)
        # Borderline/unverified is usable. Vision only vetoes a clear
        # mismatch; metadata ranking still chooses the best candidate.
        qualified = not rejected
    else:
        rejected = unsafe or (
            (decision.status == "rejected" or decision.fatal_mismatch)
            and not contextual_keep
        )
        qualified = (not rejected) and decision.status == "accepted"
    from services.visual_intelligence.semantic_guard import explicit_scene_conflict, explicit_visual_facet_conflict, subject_level_accepts_verdict
    # A high brand-relevance score cannot override an explicit pixel
    # verdict that the narrated object is absent. Reject here so the
    # resolver tries another candidate/query, rather than handing the
    # same wrong image to the final gate repeatedly.
    facet_conflict = explicit_visual_facet_conflict(scene_text, model_row.get('scene_description') or '')
    subject_accept = False
    if not contextual_keep and not facet_conflict and explicit_scene_conflict(model_row):
        # Subject-level acceptance (plan §3, web lane): an image that
        # authentically shows the scene's subject passes even when
        # vision rejected it only for literal mismatch. The model
        # row IS a fresh live vision verdict (just produced by the
        # single-verify call above), so it carries visual_checked
        # for the guard. A facet conflict is a genuine contradiction
        # and never reaches this rescue.
        try:
            _live_row = dict(model_row)
            _live_row["visual_checked"] = True
            # Only scene_text / subject / global_topic are in scope
            # here (this is the deferred-shortlist gate, not the
            # per-scene resolver): the subject string doubles as the
            # canonical entity for the guard's derivation.
            _scene = {
                "text": scene_text,
                "content_title": global_topic,
                "global_topic": global_topic,
                "subject": subject,
                "visual_requirement": {"canonical_entity": subject},
            }
            subject_accept = bool(subject_level_accepts_verdict(_live_row, _scene, candidate))
        except Exception:
            subject_accept = False
    if subject_accept:
        # Mirror the contextual soft pass above: keep the model's
        # scores for ranking, clear the fatal / literal-match
        # rejections, and let the winner selection rank this
        # candidate on its scores.
        decision.status = "accepted"
        decision.rejection_reasons = [
            reason for reason in (decision.rejection_reasons or [])
            if reason not in {"low_relevance", "fatal_mismatch", "explicit_scene_conflict"}
        ]
        decision.fatal_mismatch = False
        qualified = True
        model_row = dict(model_row)
        model_row.update({
            "fatal_mismatch": False,
            "subject_level_accepted": True,
            "accept_reason": "subject_level_accept",
        })
    elif (not contextual_keep and explicit_scene_conflict(model_row)) or facet_conflict:
        qualified = False
        decision.status = "rejected"
        decision.rejection_reasons.append("explicit_scene_conflict")
    score = decision.relevance
    return (qualified, score, -rank, candidate, decision, model_row)


def _screen_web_shortlist(candidates, *, global_topic, subject, scene_text):
    """Safety-screen downloaded candidates before vision (domain + local safety).

    Extracted verbatim from ``_rank_deferred_scene_shortlist`` so the
    cross-scene web batch driver screens the same way.
    """
    from services.visual_intelligence.semantic_guard import explicit_domain_conflict

    rows = [candidate for candidate in (candidates or []) if candidate.get("path")]
    safe_rows = []
    for candidate in rows[:_vision_shortlist_limit()]:
        source_evidence = ' '.join(str(candidate.get(key) or '') for key in
            ('title', 'description', 'source_url', 'page_url', 'domain'))
        if explicit_domain_conflict(f'{global_topic} {subject} {scene_text}', source_evidence):
            continue
        safe, _reason = image_is_safe(
            candidate.get("path", ""),
            title=candidate.get("title", ""),
            url=candidate.get("url", ""),
            tags=candidate.get("tags", ""),
            source=candidate.get("domain") or candidate.get("provider") or candidate.get("source", ""),
            description=candidate.get("description", ""),
            source_url=candidate.get("source_url", ""),
            page_url=candidate.get("page_url", ""),
            preview_url=candidate.get("preview_url", ""),
        )
        if safe:
            safe_rows.append(candidate)
    return safe_rows


def _build_web_visual_result(accepted, *, scene_text, subject, global_topic):
    """Build the winning web result from a judged candidate.

    Extracted verbatim from ``_rank_deferred_scene_shortlist`` so the
    cross-scene web batch driver judges winners by the same rules.
    Stamps the live-verdict evidence the coordinator's
    ``_late_web_branch_accept`` requires (``visual_checked`` /
    ``semantic_verified`` + ``verified_asset_sha256`` file binding +
    per-scene ``requirement_hash`` / ``scene_index`` binding).
    """
    from services.visual_intelligence.decision import asset_fingerprint, scene_context_hash

    _, _score, _neg_rank, winner, decision, model_row = max(accepted, key=lambda row: (row[1], row[2]))
    result = dict(winner)
    result["visual_decision"] = decision.to_dict()
    result["visual_decision"]["scene_description"] = str(model_row.get("scene_description") or "")
    for key in ("domain_match", "scene_match", "contextual_match", "nearest_match"):
        if key in model_row:
            result["visual_decision"][key] = bool(
                str(model_row.get(key) or "").strip().lower()
                in {"1", "true", "yes", "on"}
            )
    winner_path = str(winner.get("path") or "")
    winner_fp = asset_fingerprint(winner_path) if winner_path else ""
    winner_req_hash = str(winner.get("requirement_hash") or winner.get("verified_requirement_hash") or "").strip()
    if not winner_req_hash:
        winner_req_hash = scene_context_hash(narration=scene_text, subject=subject, topic=global_topic, media_type="image")

    result["visual_score"] = decision.relevance
    result["candidate_rank"] = decision.candidate_rank
    result["visual_outcome"] = "pass"
    result["semantic_verified"] = True
    result["visual_checked"] = True
    result["visual_decision"]["accepted"] = True
    result["visual_decision"]["status"] = "accepted"
    result["visual_decision"]["semantic_verified"] = True
    result["visual_decision"]["visual_checked"] = True
    result["visual_decision"]["visual_available"] = True
    result["visual_decision"]["visual_review_policy_version"] = 17
    if winner_fp:
        result["asset_fingerprint"] = winner_fp
        result["verified_asset_sha256"] = winner_fp
        result["visual_decision"]["asset_fingerprint"] = winner_fp
        result["visual_decision"]["verified_asset_sha256"] = winner_fp
    if winner_req_hash:
        result["requirement_hash"] = winner_req_hash
        result["verified_requirement_hash"] = winner_req_hash
        result["visual_decision"]["scene_context_hash"] = winner_req_hash
        result["visual_decision"]["verified_requirement_hash"] = winner_req_hash
    return result


def _web_visual_metadata_fallback(safe_rows, reason: str, outcome: str = "unavailable") -> Dict:
    """Keep a safe provider winner when vision transport is unavailable.

    A missing/slow Qwen response is an availability problem, not proof that
    the downloaded image is wrong. Returning an empty path here used to
    turn an otherwise valid web result into a neutral card for every strict
    scene during a vision outage. The shared coordinator still records the
    result as degraded and performs a live verification attempt when a
    client is available; explicit fatal/irrelevance verdicts remain hard
    rejects below.
    """
    # A transport/client/schema failure is not evidence that the pixels
    # are wrong.  Keep the already downloaded, safety-screened winner and
    # mark it unverified so the coordinator can retry/audit it.  Returning
    # an empty path here used to create blank text cards whenever Qwen was
    # slow or unavailable (especially for semantically locked rows).  A
    # confident model rejection still takes the explicit hard-failure path
    # below, so this fallback does not weaken factual vetoes.
    result = dict(safe_rows[0])
    result.update({
        "visual_gate_status": "unavailable",
        "visual_outcome": outcome,
        "visual_reason": reason,
        "visual_unverified": True,
        "candidate_pool_size": max(1, int(result.get("candidate_pool_size") or len(safe_rows))),
        "downloaded_candidate_count": max(
            1, int(result.get("downloaded_candidate_count") or len(safe_rows))
        ),
    })
    return result



def _web_visual_strict_failure(outcome: str, reason: str) -> Dict:
    return {
        "path": "",
        "width": 0,
        "height": 0,
        "url": "",
        "domain": "visual_gate",
        "reused_from_scene": False,
        "visual_rejected": True,
        "visual_outcome": outcome,
        "visual_reason": reason,
    }


def _web_cross_scene_prompt(blocks: List[str]) -> str:
    """One multi-scene web batch prompt: one header, one block per scene.

    The per-scene text is the existing ``_rank_deferred_scene_shortlist``
    batch prompt, moved verbatim into the per-scene block — only the
    envelope changes from 1 scene to 8. The model returns one verdict
    object per candidate, keyed by ``candidate_id``, in scene order
    (the existing ``_normalise_batch_response`` contract).
    """
    header = (
        "Judge EACH image below, in order. Return one JSON object with a "
        "\"verdicts\" array holding one verdict object per candidate, in the same "
        "order, using the candidate_id shown for each. "
        "Return domain_match and scene_match booleans per candidate. "
        "domain_match means the pixels belong to the requested domain; scene_match means "
        "the image supports this narration or is a useful establishing/near-match shot. "
        "Do not treat the retrieval query as evidence: require the required entity/type/era "
        "to be visibly supported by the pixels, and reject an ambiguous image rather than guessing. "
        "Reject ONLY a confident wrong entity/event/domain, clearly irrelevant media, "
        "or adult/unsafe content. Do not reject merely for imperfect quality, "
        "historical illustration, text, watermark, or an indirect but on-topic shot. "
        "Each verdict is JSON with relevance, quality, fatal_mismatch, adult_content, suggestive_content, "
        "synthetic_content, unsafe_content, watermark, confidence, reason, and scene_description "
        "(describe visible objects, not search metadata). "
        "Solar panel installation is not an EV charging station. Game mods "
        "and game screenshots cannot depict factual real-world footage."
    )
    return header + "\n\n" + "\n\n".join(blocks)


def _web_cross_scene_block(scene_index: int, scene_text: str, subject: str,
                           global_topic: str, query: str, candidate: Dict) -> str:
    """One per-scene block for a cross-scene web batch (one candidate)."""
    hint = " | ".join(
        str(candidate.get(key) or "").strip().replace("\n", " ")
        for key in ("title", "description", "tags", "domain", "provider", "query_used")
        if str(candidate.get(key) or "").strip()
    )
    return (
        f"Scene {scene_index} — narration: {scene_text}\n"
        f"Documentary topic: {global_topic or subject}\n"
        f"Required subject: {subject or global_topic}\n"
        f"Retrieval query: {query}\n"
        f"Candidate — ID: {candidate.get('candidate_id') or candidate.get('id') or candidate.get('path')}\n"
        "Candidate metadata (hint only — judge the pixels, not the metadata): "
        + (hint[:700] or "none")
    )


def _web_stream_flush_seconds() -> float:
    """Slow-tail flush age for the web vision streamer (design §1.4c).

    Default 60 s, clamp 15–180 s (env VIDZEO_WEB_STREAM_FLUSH_SECONDS).
    """
    try:
        return max(
            15.0,
            min(
                180.0,
                float(os.environ.get("VIDZEO_WEB_STREAM_FLUSH_SECONDS", "60") or 60),
            ),
        )
    except (TypeError, ValueError):
        return 60.0


class _WebVisionStreamer:
    """File web vision verdicts DURING retrieval instead of after it.

    Design ``work/web-stream-vision-design.md`` §1 (test 28): the
    post-retrieval visual phase is the only place web vision was ever
    filed on the V2 path, so on a slow network retrieval was still
    running when the coordinator wall expired and the web lane clock
    never started. The streamer is offered each scene as soon as the
    retrieval drain loop completes it, and files packs through the
    same ``_verify_web_scenes_cross_scene`` driver, the same 8-scene
    packs, the same lane child and scheduler — never one-by-one.

    Only the worker thread calls the driver. Filing triggers (§1.4):
    (a) a full pack (``web_vision_batch_images()``, default 8),
    (b) closing (drain empty), (c) slow-tail flush (>= 4 pending and
    the oldest pending >= flush seconds), (d) last-chance flush (lane
    budget remaining < 120 s and >= 1 pending). When the lane budget
    has expired, nothing is filed; scenes stay undecided for the drain,
    which retains them as ``web_visual_grace_deadline``.
    """

    def __init__(
        self,
        *,
        visual_context,
        lane_budget,
        scene_texts,
        scene_active_subjects,
        title,
        results,
        results_lock,
        apply_result,
    ):
        from services.visual_intelligence.lane_budget import (
            web_vision_batch_images,
        )

        self._visual_context = visual_context
        self._lane_budget = lane_budget
        self._scene_texts = list(scene_texts or [])
        self._scene_active_subjects = list(scene_active_subjects or [])
        self._title = title
        self._results = results
        self._results_lock = results_lock
        self._apply = apply_result
        self._pack_size = max(1, int(web_vision_batch_images() or 8))
        self._flush_seconds = _web_stream_flush_seconds()
        self._lock = threading.Lock()
        self._pending: Dict[int, Dict] = {}  # idx -> {"task", "offered_at"}
        self._decided: set = set()
        self._filed_candidate_ids: set = set()
        self._queue: "queue.Queue[int]" = queue.Queue()
        self._stop = threading.Event()
        self._client_missing_logged = False
        self._scheduler_missing_logged = False
        self._budget_expired_logged = False
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="web-vision-stream"
        )
        self._thread.start()

    # -- public API ------------------------------------------------------
    def decided_snapshot(self) -> set:
        """Scenes whose verdict has been emitted (streamer + drain filter)."""
        with self._lock:
            return set(self._decided)

    def offer(self, idx: int, result: Optional[Dict]) -> bool:
        """Offer one completed scene; non-blocking and infallible.

        Returns True when the scene entered the streamer (pending or
        prep-hit decided). Never raises into the drain loop; never
        files vision itself.
        """
        try:
            if self._stop.is_set():
                return False
            if not isinstance(result, dict):
                return False
            if self._lane_budget is not None:
                try:
                    if self._lane_budget.expired():
                        return False
                except Exception:
                    pass
            with self._lock:
                if idx in self._decided or idx in self._pending:
                    return False
            if _is_prep_cache_hit(result):
                # Already vision-verified when banked: no call is spent,
                # the result is stamped pass and published at once.
                with self._lock:
                    if idx in self._decided:
                        return False
                    self._decided.add(idx)
                result["visual_decision"] = _prep_verified_visual_decision()
                result["visual_outcome"] = "pass"
                result["visual_reason"] = "prep_cache_verified"
                self._apply(idx, result)
                return True
            shortlist = result.get("_vision_shortlist") or []
            if not (str(result.get("path") or "").strip() or shortlist):
                return False
            task = {
                "scene_index": idx,
                "scene_text": self._scene_texts[idx]
                if 0 <= idx < len(self._scene_texts)
                else "",
                "subject": self._scene_active_subjects[idx]
                if 0 <= idx < len(self._scene_active_subjects)
                else "",
                "global_topic": self._title,
                "query": str(result.get("query_used") or ""),
                "candidates": shortlist or [result],
            }
            with self._lock:
                if idx in self._decided or idx in self._pending:
                    return False
                self._pending[idx] = {
                    "task": task,
                    "offered_at": time.monotonic(),
                }
            self._queue.put(idx)
            return True
        except Exception as exc:
            _web_log(f"web stream vision offer failed (non-fatal): {type(exc).__name__}")
            return False

    def close(self) -> None:
        """Stop accepting offers, file the final pack-set, join bounded.

        The worker files any remaining pending scenes (trigger (b))
        and exits. The join is bounded by the remaining lane budget,
        capped at 180 s — never longer than the budget itself, never
        unbounded. If the join times out, the drain proceeds anyway:
        the decided set is re-checked before filing and any pack-set
        still in flight publishes via the thread-safe publish path.

        Round 7 (test 34): the old 60 s cap could never catch a slow
        hour's vision round trip — the pack filed ~03:06:40, answers
        landed 03:08:23/53 (~103-133 s), so the drain proceeded with
        0 web decided while 6 ACCEPTS were still in flight, and the
        plan locked without them. 180 s covers the measured slow-hour
        round trip while the lane budget still bounds the wait.
        """
        self._stop.set()
        try:
            self._queue.put(-1)
        except Exception:
            pass
        remaining = 180.0
        if self._lane_budget is not None:
            try:
                remaining = max(0.0, min(180.0, float(self._lane_budget.remaining())))
            except Exception:
                remaining = 180.0
        self._thread.join(timeout=remaining)
        if self._thread.is_alive():
            _web_log(
                "[web] stream vision: worker still filing at drain "
                "handover; drain proceeds (decided-set re-checked before filing)"
            )

    # -- worker ----------------------------------------------------------
    def _run(self) -> None:
        while True:
            try:
                self._queue.get(timeout=0.25)
            except queue.Empty:
                pass
            except Exception:
                pass
            with self._lock:
                stop = self._stop.is_set()
                has_pending = bool(self._pending)
            if stop and not has_pending:
                return
            trigger = self._trigger()
            if trigger is None:
                if stop and has_pending:
                    trigger = "b"
                else:
                    continue
            try:
                self._file_pending(trigger)
            except Exception as exc:
                _web_log(f"web stream vision filing failed (non-fatal): {type(exc).__name__}: {exc}")
            with self._lock:
                if self._stop.is_set() and not self._pending:
                    return

    def _trigger(self) -> Optional[str]:
        """First firing trigger (§1.4), or None when nothing files yet."""
        with self._lock:
            pending = dict(self._pending)
        if not pending:
            return None
        if len(pending) >= self._pack_size:
            return "a"
        if self._lane_budget is not None:
            try:
                if float(self._lane_budget.remaining()) < 120.0:
                    return "d"
            except Exception:
                pass
        if len(pending) >= 4:
            oldest = min(entry["offered_at"] for entry in pending.values())
            if time.monotonic() - oldest >= self._flush_seconds:
                return "c"
        return None

    def _on_driver_result(self, idx: int, result: Optional[Dict]) -> None:
        with self._lock:
            self._decided.add(idx)
        try:
            self._apply(idx, result)
        except Exception:
            pass

    def _file_pending(self, trigger: str) -> None:
        with self._lock:
            if not self._pending:
                return
            scene_ids = sorted(self._pending)
            entries = [self._pending.pop(idx) for idx in scene_ids]
        tasks = [entry["task"] for entry in entries]
        # Budget expiry mid-retrieval (§1.8): do not file. The scenes
        # stay undecided for the drain, which retains them as
        # web_visual_grace_deadline — never seeded as accepted.
        if self._lane_budget is not None:
            try:
                if self._lane_budget.expired():
                    if not self._budget_expired_logged:
                        self._budget_expired_logged = True
                        _web_log(
                            "[web] stream vision: lane budget expired, "
                            f"{len(tasks)} scene(s) left unverdicted for drain"
                        )
                    return
            except Exception:
                pass
        # Client is resolved lazily at first filing, not at construction:
        # in deferred mode the resolver-local probe can fail while the
        # coordinator can still initialise its client later. Hold the
        # pack (do not drop it, do not file without a client) and retry
        # on the next offer/flush; at drain time the drain's existing
        # client-unavailable path applies.
        try:
            client = self._visual_context.get_client()
        except Exception:
            client = None
        if client is None:
            if not self._stop.is_set():
                with self._lock:
                    for idx, entry in zip(scene_ids, entries):
                        self._pending.setdefault(idx, entry)
            if not self._client_missing_logged:
                self._client_missing_logged = True
                _web_log(
                    "[web] stream vision: no vision client yet; "
                    f"holding {len(tasks)} scene(s) for the next offer/flush"
                )
            return
        scheduler = getattr(self._visual_context, "lane_scheduler", None)
        if scheduler is None:
            parent = getattr(self._visual_context, "_parent", None)
            scheduler = getattr(parent, "lane_scheduler", None) if parent is not None else None
        if scheduler is None and not self._scheduler_missing_logged:
            self._scheduler_missing_logged = True
            _web_log(
                "[web] stream vision: no lane scheduler; filing directly "
                "(lane budget still gates via the driver)"
            )
        for task in tasks:
            for cand in task.get("candidates") or []:
                cid = str(
                    cand.get("candidate_id") or cand.get("id") or cand.get("path") or ""
                )
                if cid:
                    self._filed_candidate_ids.add(cid)
        _web_log(
            f"[web] stream vision: filing pack-set of {len(tasks)} "
            f"scene(s) (trigger={trigger})"
        )
        try:
            outcomes = _verify_web_scenes_cross_scene(
                tasks,
                visual_context=self._visual_context,
                client=client,
                lane_budget=self._lane_budget,
                scheduler=scheduler,
                on_scene_result=self._on_driver_result,
                allow_stragglers=False,
            )
        except Exception as exc:
            _web_log(f"web stream vision pack-set failed (non-fatal): {type(exc).__name__}: {exc}")
            return
        if isinstance(outcomes, dict):
            with self._lock:
                self._decided.update(int(k) for k in outcomes.keys())


def _verify_web_scenes_cross_scene(
    tasks: List[Dict],
    *,
    visual_context,
    client,
    lane_budget=None,
    scheduler=None,
    on_scene_result=None,
    allow_stragglers: bool = True,
) -> Dict[int, Optional[Dict]]:
    """Judge web scenes in cross-scene batches: 8 scenes x 1 candidate/request.

    Vision lanes (§3.3.1): wave 1 files each scene's top-1 live candidate
    in packs of 8 scenes (``pack_web_scenes`` — never a < 4-scene batch
    while >= 4 scenes are unverified); wave 2 files the top-2 candidate
    for scenes whose top-1 was rejected. Prep-banked candidates spend no
    vision call. Every verdict row is judged by the same
    ``_judge_web_candidate`` rules as the per-scene path, and the winner
    is built by the same ``_build_web_visual_result`` — batching changes
    only how verdicts arrive, never which images win. A scene whose
    batch returned no verdict falls back to the per-scene
    ``_rank_deferred_scene_shortlist`` (stragglers only). A lane-budget
    expiry stops new filings; undecided scenes return None and the
    caller stamps them ``web_visual_grace_deadline`` (the audit label
    for "lane budget expired"). ``on_scene_result(idx, result)`` fires
    per scene as its verdict lands, so the caller can publish the row
    without waiting for the whole batch.
    """
    from services.visual_intelligence.lane_budget import pack_web_scenes

    system_prompt = (
        "You select documentary visuals. Return JSON only. Reject exact-entity, "
        "event, action, era, ownership-watermark, or unsafe mismatches."
    )
    outcomes: Dict[int, Optional[Dict]] = {}
    decided: set = set()

    def _emit(idx: int, result: Optional[Dict]) -> None:
        outcomes[idx] = result
        decided.add(idx)
        if callable(on_scene_result) and result is not None:
            try:
                on_scene_result(idx, result)
            except Exception:
                pass

    def _no_accept_result(idx: int, task: Dict, ranked: list, safe_rows: list) -> Dict:
        visual_required = bool(getattr(visual_context, "visual_required", False)) if visual_context is not None else False
        clearly_irrelevant = (
            bool(ranked)
            and all(
                ("fatal_mismatch" in set(row[4].rejection_reasons or []))
                or ("explicit_scene_conflict" in set(row[4].rejection_reasons or []))
                or (
                    "low_relevance" in set(row[4].rejection_reasons or [])
                    and row[4].confidence >= 70.0
                )
                for row in ranked
            )
        )
        fail_closed = bool(getattr(visual_context, "fail_closed", False))
        if clearly_irrelevant or fail_closed:
            return _web_visual_strict_failure("hard_fail", "all_candidates_clearly_irrelevant" if clearly_irrelevant else "visual_fail_closed")
        return _web_visual_metadata_fallback(safe_rows, "borderline_visual_rejection", "uncertain")

    # Per-scene prep: screen, cap, prep-hit winners, wave-1 candidate.
    prepared: Dict[int, Dict] = {}
    for task in tasks or []:
        idx = int(task.get("scene_index"))
        safe_rows = _screen_web_shortlist(
            task.get("candidates") or [],
            global_topic=task.get("global_topic") or "",
            subject=task.get("subject") or "",
            scene_text=task.get("scene_text") or "",
        )
        if not safe_rows:
            _emit(idx, None)
            continue
        capped = safe_rows[:_single_verify_cap()]
        ranked: list = []
        winner_result = None
        for rank, candidate in enumerate(capped):
            if not _is_prep_cache_hit(candidate):
                continue
            judged = _judge_web_candidate(
                candidate, _prep_verified_model_row(candidate), rank,
                scene_index=idx, scene_text=task.get("scene_text") or "",
                subject=task.get("subject") or "",
                global_topic=task.get("global_topic") or "",
                visual_context=visual_context, client=client,
            )
            ranked.append(judged)
            if judged[0]:
                winner_result = _build_web_visual_result(
                    [j for j in ranked if j[0]],
                    scene_text=task.get("scene_text") or "",
                    subject=task.get("subject") or "",
                    global_topic=task.get("global_topic") or "",
                )
                break
        if winner_result is not None:
            _emit(idx, winner_result)
            continue
        live = [c for c in capped if not _is_prep_cache_hit(c)]
        if not live:
            _emit(idx, _no_accept_result(idx, task, ranked, safe_rows))
            continue
        prepared[idx] = {
            "task": task, "safe_rows": safe_rows, "capped": capped,
            "live": live, "ranked": ranked, "wave": 1,
        }

    def _file_wave(wave_scenes: Dict[int, Dict], pick: str) -> Dict[str, Dict]:
        """File one wave's packs concurrently; return {candidate_id: row}."""
        if not wave_scenes:
            return {}
        if lane_budget is not None and lane_budget.expired():
            return {}
        scene_ids = sorted(wave_scenes)
        packs = pack_web_scenes(scene_ids)
        # candidate_id -> (scene_index, candidate, block)
        by_cid: Dict[str, tuple] = {}
        for idx in scene_ids:
            entry = wave_scenes[idx]
            cand = entry["live"][0] if pick == "wave1" else entry["live"][1]
            cid = str(cand.get("candidate_id") or cand.get("id") or cand.get("path") or "")
            task = entry["task"]
            block = _web_cross_scene_block(
                idx, task.get("scene_text") or "", task.get("subject") or "",
                task.get("global_topic") or "", task.get("query") or "", cand,
            )
            by_cid[cid] = (idx, cand, block)
        pack_cids = []
        for pack in packs:
            pack_cids.append([
                str(wave_scenes[idx]["live"][0 if pick == "wave1" else 1].get("candidate_id")
                    or wave_scenes[idx]["live"][0 if pick == "wave1" else 1].get("id")
                    or wave_scenes[idx]["live"][0 if pick == "wave1" else 1].get("path") or "")
                for idx in pack
            ])

        def _call(ids: List[str]) -> Dict:
            paths = [str(by_cid[cid][1].get("path") or "") for cid in ids if cid in by_cid]
            tags = [by_cid[cid][0] for cid in ids if cid in by_cid]
            prompt = _web_cross_scene_prompt([by_cid[cid][2] for cid in ids if cid in by_cid])
            return client.analyze_images_batch(paths, prompt, system_prompt, ids, scene_tags=tags)

        merged: Dict[str, Dict] = {}
        # File one pack at a time (8 scenes/request): the scheduler's
        # retry-only-missing logic works per pack, and a pack's prompt
        # is built for exactly its scenes.
        workers = max(1, min(4, len(pack_cids)))
        if len(pack_cids) == 1:
            try:
                if scheduler is not None:
                    merged.update(scheduler.file_batch("web", f"web-wave-{pick}-0", pack_cids[0], _call) or {})
                else:
                    merged.update(_call(pack_cids[0]) or {})
            except Exception as exc:
                _web_log(f"web cross-scene {pick} batch failed ({type(exc).__name__}: {exc})")
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                if scheduler is not None:
                    futures = {
                        pool.submit(
                            scheduler.file_batch, "web",
                            f"web-wave-{pick}-{n}", ids, _call,
                        ): ids for n, ids in enumerate(pack_cids)
                    }
                else:
                    futures = {pool.submit(_call, ids): ids for ids in pack_cids}
                for future in futures:
                    try:
                        merged.update(future.result() or {})
                    except Exception as exc:
                        _web_log(f"web cross-scene {pick} batch failed ({type(exc).__name__}: {exc})")
        return merged

    # Wave 1: top-1 per scene.
    wave1 = {idx: e for idx, e in prepared.items() if e["wave"] == 1 and len(e["live"]) >= 1}
    rows1 = _file_wave(wave1, "wave1") if wave1 else {}
    wave2: Dict[int, Dict] = {}
    for idx, entry in wave1.items():
        if idx in decided:
            continue
        task = entry["task"]
        cand = entry["live"][0]
        cid = str(cand.get("candidate_id") or cand.get("id") or cand.get("path") or "")
        model_row = rows1.get(cid) if isinstance(rows1, dict) else None
        if not isinstance(model_row, dict):
            continue  # no verdict: straggler fallback below
        judged = _judge_web_candidate(
            cand, model_row, 0,
            scene_index=idx, scene_text=task.get("scene_text") or "",
            subject=task.get("subject") or "",
            global_topic=task.get("global_topic") or "",
            visual_context=visual_context, client=client,
        )
        entry["ranked"].append(judged)
        if judged[0]:
            _emit(idx, _build_web_visual_result(
                [j for j in entry["ranked"] if j[0]],
                scene_text=task.get("scene_text") or "",
                subject=task.get("subject") or "",
                global_topic=task.get("global_topic") or "",
            ))
        elif len(entry["live"]) >= 2:
            wave2[idx] = entry
        else:
            _emit(idx, _no_accept_result(idx, task, entry["ranked"], entry["safe_rows"]))
    # Wave 2: top-2 for scenes whose top-1 was rejected.
    rows2 = _file_wave(wave2, "wave2") if wave2 else {}
    for idx, entry in wave2.items():
        if idx in decided:
            continue
        task = entry["task"]
        cand = entry["live"][1]
        cid = str(cand.get("candidate_id") or cand.get("id") or cand.get("path") or "")
        model_row = rows2.get(cid) if isinstance(rows2, dict) else None
        if not isinstance(model_row, dict):
            continue  # no verdict: straggler fallback below
        judged = _judge_web_candidate(
            cand, model_row, 1,
            scene_index=idx, scene_text=task.get("scene_text") or "",
            subject=task.get("subject") or "",
            global_topic=task.get("global_topic") or "",
            visual_context=visual_context, client=client,
        )
        entry["ranked"].append(judged)
        if judged[0]:
            _emit(idx, _build_web_visual_result(
                [j for j in entry["ranked"] if j[0]],
                scene_text=task.get("scene_text") or "",
                subject=task.get("subject") or "",
                global_topic=task.get("global_topic") or "",
            ))
        else:
            _emit(idx, _no_accept_result(idx, task, entry["ranked"], entry["safe_rows"]))
    # Stragglers: scenes with no verdict at all in either wave (batch
    # returned nothing for them, or a wave was never filed). Fall back
    # to the per-scene gate, which single-verifies the missing
    # candidates — never re-judging a decided one (decided scenes
    # already emitted above). Streaming callers pass
    # allow_stragglers=False so the streamer thread returns promptly
    # to file the next pack-set; undecided scenes are left for the
    # drain, which runs this fallback as today.
    if not allow_stragglers:
        return outcomes
    for idx, entry in prepared.items():
        if idx in decided:
            continue
        if lane_budget is not None and lane_budget.expired():
            _emit(idx, None)
            continue
        task = entry["task"]
        try:
            result = _rank_deferred_scene_shortlist(
                scene_index=idx,
                scene_text=task.get("scene_text") or "",
                subject=task.get("subject") or "",
                global_topic=task.get("global_topic") or "",
                query=task.get("query") or "",
                candidates=task.get("candidates") or [],
                visual_context=visual_context,
            )
        except Exception as exc:
            _web_log(f"scene {idx + 1} cross-scene straggler fallback failed: {type(exc).__name__}")
            result = None
        _emit(idx, result)
    return outcomes


def _rank_deferred_scene_shortlist(
    *,
    scene_index: int,
    scene_text: str,
    subject: str,
    global_topic: str,
    query: str,
    candidates: List[Dict],
    visual_context,
) -> Optional[Dict]:
    """Use the opt-in visual gate on downloaded candidates.

    Retrieval remains the fast, recall-oriented stage.  When a live visual
    client is supplied, this bounded shortlist pass rejects confident wrong,
    unsafe, or synthetic picks before they become the scene winner.  Any
    client/decode/API failure fails open to the provider-ranked winner, which
    preserves the resolver's existing availability contract.
    """
    rows = [candidate for candidate in (candidates or []) if candidate.get("path")]
    if not rows:
        return None

    safe_rows = _screen_web_shortlist(rows, global_topic=global_topic, subject=subject, scene_text=scene_text)
    if not safe_rows:
        return None

    visual_required = bool(getattr(visual_context, "visual_required", False)) if visual_context is not None else False
    visual_pending_rows = any(
        bool(candidate.get("semantic_visual_pending")) for candidate in safe_rows
    )
    gaming_visual_contract = bool(re.search(
        r"\b(?:gta\s*(?:v|vi|5|6)|grand\s+theft\s+auto|gameplay|video\s+game|gaming|playstation|xbox|nintendo)\b",
        " ".join((str(subject or ""), str(global_topic or ""), str(query or ""), str(scene_text or ""))),
        re.IGNORECASE,
    ))

    try:
        client = visual_context.get_client() if visual_context is not None else None
    except Exception as exc:
        _web_log(f"scene {scene_index + 1} visual gate unavailable: {type(exc).__name__}")
        if _web_verbose():
            import traceback
            _web_log("visual gate client traceback:\n" + traceback.format_exc())
        return _web_visual_metadata_fallback(safe_rows, f"client_error:{type(exc).__name__}")
    if client is None:
        # The low-level strict gate must make the unavailable verifier state
        # explicit. The coordinator may retain its already safety-screened
        # winner as an unverified degraded result, but direct callers need a
        # fail-closed result so missing vision is never mistaken for a pass.
        if visual_required or visual_pending_rows:
            return _web_visual_strict_failure(
                "unavailable",
                "visual_pending_requires_verifier" if visual_pending_rows else "client_unavailable",
            )
        return _web_visual_metadata_fallback(safe_rows, "client_unavailable", "unavailable")

    try:
        from services.visual_intelligence.decision import normalize_scene_decision, asset_fingerprint, scene_context_hash

        # Batched vision: one request judges the whole shortlist and
        # returns one verdict row per candidate. Prep-banked candidates
        # were vision-verified when banked, so they are judged from a
        # synthetic accepted row and spend NO vision call. Live candidates
        # share a single analyze_images_batch call; only a batch failure
        # falls back to the old one-call-per-candidate loop below. Every
        # row — batched, single, or synthetic prep — is judged by the same
        # _judge_candidate rules, and the first qualifier in
        # metadata-rank order still wins, so batching changes only how
        # verdicts arrive, never which image wins.
        system_prompt = (
            "You select documentary visuals. Return JSON only. Reject exact-entity, "
            "event, action, era, ownership-watermark, or unsafe mismatches."
        )
        capped = safe_rows[:_single_verify_cap()]
        live_rows = [candidate for candidate in capped if not _is_prep_cache_hit(candidate)]
        batch_rows: dict = {}
        if live_rows:
            batch_fn = getattr(client, "analyze_images_batch", None)
            if callable(batch_fn):
                batch_prompt = (
                    f"Documentary topic: {global_topic or subject}\n"
                    f"Required subject: {subject or global_topic}\n"
                    f"Scene narration: {scene_text}\n"
                    f"Retrieval query: {query}\n\n"
                    "Judge EACH image below, in order. Return one JSON object with a "
                    "\"verdicts\" array holding one verdict object per candidate, in the same "
                    "order, using the candidate_id shown for each. "
                    "Return domain_match and scene_match booleans per candidate. "
                    "domain_match means the pixels belong to the requested domain; scene_match means "
                    "the image supports this narration or is a useful establishing/near-match shot. "
                    "Do not treat the retrieval query as evidence: require the required entity/type/era "
                    "to be visibly supported by the pixels, and reject an ambiguous image rather than guessing. "
                    "Reject ONLY a confident wrong entity/event/domain, clearly irrelevant media, "
                    "or adult/unsafe content. Do not reject merely for imperfect quality, "
                    "historical illustration, text, watermark, or an indirect but on-topic shot. "
                    "Each verdict is JSON with relevance, quality, fatal_mismatch, adult_content, suggestive_content, "
                    "synthetic_content, unsafe_content, watermark, confidence, reason, and scene_description "
                    "(describe visible objects, not search metadata). "
                    "Solar panel installation is not an EV charging station. Game mods "
                    "and game screenshots cannot depict factual real-world footage.\n\n"
                    + "\n\n".join(
                        f"Candidate {position} — ID: {candidate.get('candidate_id') or candidate.get('id') or candidate.get('path')}\n"
                        "Candidate metadata (hint only — judge the pixels, not the metadata): "
                        + (" | ".join(
                            str(candidate.get(key) or "").strip().replace("\n", " ")
                            for key in ("title", "description", "tags", "domain", "provider", "query_used")
                            if str(candidate.get(key) or "").strip()
                        )[:700] or "none")
                        for position, candidate in enumerate(live_rows)
                    )
                )
                try:
                    batch_rows = batch_fn(
                        [str(candidate.get("path") or "") for candidate in live_rows],
                        batch_prompt,
                        system_prompt,
                        [str(candidate.get("candidate_id") or candidate.get("id") or candidate.get("path") or "") for candidate in live_rows],
                    ) or {}
                except Exception as exc:
                    # Log the message, not just the type: the type alone
                    # ("VisionClientError") hid the cause of test 13's 6/6
                    # batch failures — the message named the bug. Never log
                    # only the type here.
                    _web_log(f"scene {scene_index + 1} visual gate batch verify failed ({type(exc).__name__}: {exc}); falling back to single verifies")
                    batch_rows = {}
                if not isinstance(batch_rows, Mapping):
                    batch_rows = {}
        ranked = []
        verified_any = False
        for rank, candidate in enumerate(capped):
            if _is_prep_cache_hit(candidate):
                # Already vision-verified at prep time: judge the synthetic
                # accepted row, spend no vision call.
                verified_any = True
                judged = _judge_web_candidate(candidate, _prep_verified_model_row(candidate), rank,
                    scene_index=scene_index, scene_text=scene_text, subject=subject,
                    global_topic=global_topic, visual_context=visual_context, client=client)
                ranked.append(judged)
                if judged[0]:
                    # First qualifying candidate in metadata-rank order wins; do
                    # not spend further vision requests ranking the rest.
                    break
                continue
            path = str(candidate.get("path") or "")
            cid = str(candidate.get("candidate_id") or candidate.get("id") or candidate.get("path") or "")
            model_row = batch_rows.get(cid) if isinstance(batch_rows, Mapping) else None
            if not isinstance(model_row, Mapping):
                # No batched row for this candidate (batch failed, or the
                # model skipped it): the old single verify, once.
                hint = " | ".join(
                    str(candidate.get(key) or "").strip().replace("\n", " ")
                    for key in ("title", "description", "tags", "domain", "provider", "query_used")
                    if str(candidate.get(key) or "").strip()
                )
                single_prompt = (
                    f"Documentary topic: {global_topic or subject}\n"
                    f"Required subject: {subject or global_topic}\n"
                    f"Scene narration: {scene_text}\n"
                    f"Retrieval query: {query}\n"
                    f"Candidate metadata (hint only — judge the pixels, not the metadata): {hint[:700] or 'none'}\n\n"
                    "Judge this ONE image. Return domain_match and scene_match booleans. "
                    "domain_match means the pixels belong to the requested domain; scene_match means "
                    "the image supports this narration or is a useful establishing/near-match shot. "
                    "Do not treat the retrieval query as evidence: require the required entity/type/era "
                    "to be visibly supported by the pixels, and reject an ambiguous image rather than guessing. "
                    "Reject ONLY a confident wrong entity/event/domain, clearly irrelevant media, "
                    "or adult/unsafe content. Do not reject merely for imperfect quality, "
                    "historical illustration, text, watermark, or an indirect but on-topic shot. "
                    "Return JSON with relevance, quality, fatal_mismatch, adult_content, suggestive_content, "
                    "synthetic_content, unsafe_content, watermark, confidence, reason, and scene_description "
                    "(describe visible objects, not search metadata). "
                    "Solar panel installation is not an EV charging station. Game mods "
                    "and game screenshots cannot depict factual real-world footage."
                )
                try:
                    model_row = client.analyze_image(candidate["path"], single_prompt, system_prompt)
                    model_row = model_row if isinstance(model_row, dict) else {}
                except Exception as exc:
                    _web_log(f"scene {scene_index + 1} visual gate single verify failed for candidate {rank}: {type(exc).__name__}")
                    continue
            verified_any = True
            judged = _judge_web_candidate(candidate, model_row, rank,
                    scene_index=scene_index, scene_text=scene_text, subject=subject,
                    global_topic=global_topic, visual_context=visual_context, client=client)
            ranked.append(judged)
            if judged[0]:
                # First qualifying candidate in metadata-rank order wins; do
                # not spend further vision requests ranking the rest.
                break
        if not verified_any:
            # No per-candidate evidence at all: every single verify raised,
            # so this is a transport/client failure, not a content verdict.
            # Keep a locally safety-screened candidate marked as
            # unverified, as the missing-evidence path did.
            _web_log(f"scene {scene_index + 1} visual gate: single verify unavailable; keeping uncertain candidate for final gate")
            return _web_visual_metadata_fallback(safe_rows, "single_verify_unavailable", "unavailable")

        accepted = [row for row in ranked if row[0]]
        if not accepted:
            _web_log(f"scene {scene_index + 1} visual gate rejected all downloaded candidates")
            # Even in delivery mode, a confident all-candidate identity
            # mismatch must not be shipped as a valid image. The advisory
            # behavior applies to quality/uncertainty, not wrong entities.
            clearly_irrelevant = (
                bool(ranked)
                and all(
                    ("fatal_mismatch" in set(row[4].rejection_reasons or []))
                    or ("explicit_scene_conflict" in set(row[4].rejection_reasons or []))
                    or (
                        "low_relevance" in set(row[4].rejection_reasons or [])
                        and row[4].confidence >= 70.0
                    )
                    for row in ranked
                )
            )
            fail_closed = bool(getattr(visual_context, "fail_closed", False))
            if clearly_irrelevant or fail_closed:
                return _web_visual_strict_failure("hard_fail", "all_candidates_clearly_irrelevant" if clearly_irrelevant else "visual_fail_closed")
            return _web_visual_metadata_fallback(safe_rows, "borderline_visual_rejection", "uncertain")
        return _build_web_visual_result(
            accepted, scene_text=scene_text, subject=subject, global_topic=global_topic
        )
    except Exception as exc:
        _web_log(f"scene {scene_index + 1} visual gate unavailable: {type(exc).__name__}")
        if _web_verbose():
            import traceback
            _web_log("visual gate traceback:\n" + traceback.format_exc())
        return _web_visual_metadata_fallback(safe_rows, f"{type(exc).__name__}:{exc}")


def _visual_retry_scene(
    *,
    idx: int,
    scene_text: str,
    subject: str,
    global_topic: str,
    original_queries: List[str],
    gdl: Optional[List[str]],
    output_dir: Path,
    global_phashes: Dict[int, int],
    global_md5s: Dict[str, int],
    used_files: Set[str],
    phash_lock: threading.Lock,
    md5_lock: threading.Lock,
    file_lock: threading.Lock,
    visual_context,
    fast_mode: bool = False,
    max_candidate_downloads: int = 20,
    api_worker_limit: int = 6,
    source_telemetry=None,
    deadline_at: Optional[float] = None,
    discovery_cache=None,
    defer_context_verification: bool = False,
    exhaustive_fallback: bool = False,
    outage_tracker: Optional["WebOutageTracker"] = None,
) -> Optional[Dict]:
    """Re-resolve ONE gate-rejected scene with corrected queries, then re-gate.

    The original query already produced only wrong/unacceptable candidates, so
    this pass re-queries with subject-focused fallback queries (fresh results,
    different files) and runs the visual gate on the new shortlist. Returns the
    gated winner or None.
    """
    try:
        from .query import _topic_fallback_queries
    except Exception:
        _topic_fallback_queries = None
    original_set = {
        str(q).strip().lower() for q in (original_queries or []) if str(q).strip()
    }
    queries: List[str] = []
    if _topic_fallback_queries and subject:
        for fq in _topic_fallback_queries(subject, scene_text, limit=6):
            fq = str(fq or "").strip()
            if fq and fq.lower() not in original_set and fq not in queries:
                queries.append(fq)
    if not queries and (subject or global_topic):
        anchor = subject or global_topic
        for fq in (
            f"{anchor} documentary photograph",
            f"{anchor} archival footage still",
            f"{anchor} historical event photo",
        ):
            if fq.lower() not in original_set and fq not in queries:
                queries.append(fq)
    queries = queries[:6]
    if not queries:
        return None
    if _web_verbose():
        _web_log(f"scene {idx + 1} visual refetch queries: {queries[:4]}")
    cand = _resolve_scene(
        idx, scene_text, queries, gdl, output_dir, subject,
        global_phashes, global_md5s, used_files,
        phash_lock, md5_lock, file_lock,
        fast_mode=fast_mode,
        max_candidate_downloads=max_candidate_downloads,
        api_worker_limit=api_worker_limit,
        source_telemetry=source_telemetry,
        deadline_at=deadline_at,
        discovery_cache=discovery_cache,
        allow_visual_pending=bool(
            visual_context is not None
            and getattr(visual_context, "visual_required", False)
        ),
        defer_context_verification=defer_context_verification,
        exhaustive_fallback=exhaustive_fallback,
        outage_tracker=outage_tracker,
    )
    if not cand or not cand.get("path"):
        return None
    shortlist = cand.get("_vision_shortlist") or [cand]
    return _rank_deferred_scene_shortlist(
        scene_index=idx,
        scene_text=scene_text,
        subject=subject,
        global_topic=global_topic,
        query=str(cand.get("query_used") or ""),
        candidates=shortlist,
        visual_context=visual_context,
    )


# =============================================================================
#  MAIN PIPELINE
# =============================================================================

def resolve_web_images_for_video_fast(
    script: str,
    title: str,
    scene_texts: List[str],
    scene_durations: List[float],
    api_key: str = "",
    llm_provider: str = "openrouter",
    llm_model: str = "",
    target_resolution: Tuple[int, int] = (1280, 720),
    gallery_cmd: Optional[List[str]] = None,
    scene_queries_override: Optional[List[Dict]] = None,
    progress_callback=None,
    fast_mode: bool = False,
    max_candidate_downloads: int = 20,
    max_scene_workers: Optional[int] = None,
    api_worker_limit: Optional[int] = None,
    subject_override: str = "",
    output_namespace: str = "",
    visual_context=None,
    delivery_budget=None,
    on_scene_result=None,
    defer_context_verification: bool = False,
    stage_deadline_seconds: Optional[float] = None,
    query_deadline_seconds: Optional[float] = None,
    batch_discovery_seconds: Optional[float] = None,
    enable_gallery_dl: Optional[bool] = None,
    exhaustive_fallback: bool = False,
    wait_until_complete: bool = False,
) -> Dict:
    exhaustive_fallback = bool(exhaustive_fallback)
    # Wait mode gathers all providers that finish within the same hard stage
    # budget. It is not permission for a slow or wedged provider to keep the
    # production job open indefinitely.
    wait_until_complete = bool(wait_until_complete)
    # Every invocation owns a DiscoveryCache below. Do not clear the optional
    # process cache here: one concurrent job must never invalidate another.
    fast_mode = bool(fast_mode) or str(os.environ.get("VIDZEO_WEB_FAST_MODE", "0")).strip().lower() in {"1", "true", "yes", "on"}
    if exhaustive_fallback:
        # The pre-stock tier is intentionally recall-first. Do not let a
        # process-level fast-mode switch suppress its slow-source recovery.
        fast_mode = False
    try:
        max_candidate_downloads = int(os.environ.get("VIDZEO_WEB_MAX_CANDIDATES", max_candidate_downloads) or max_candidate_downloads)
    except Exception:
        max_candidate_downloads = 20
    default_scene_workers = max(4, min(8, os.cpu_count() or 6))
    default_api_workers = max(4, min(8, os.cpu_count() or 6))
    try:
        # V2 passes an explicit lane limit after resolving the Mixed Media
        # policy.  It must win over a legacy process-level
        # ``VIDZEO_WEB_SCENE_WORKERS`` value; otherwise a packaged six-worker
        # setting silently defeats the bounded 12-worker fan-out.
        scene_worker_setting = (
            max_scene_workers
            if max_scene_workers is not None
            else os.environ.get("VIDZEO_WEB_SCENE_WORKERS", default_scene_workers)
        )
        max_scene_workers = int(scene_worker_setting or default_scene_workers)
    except Exception:
        max_scene_workers = default_scene_workers
    try:
        # Four to twelve concurrent provider requests per query wave let
        # Bing/Yandex/Flickr finish inside the query deadline without
        # stalling slower scraper lanes. Explicit V2 lane limits likewise
        # take precedence over legacy environment values.
        api_worker_setting = (
            api_worker_limit
            if api_worker_limit is not None
            else os.environ.get("VIDZEO_WEB_API_WORKERS", default_api_workers)
        )
        api_worker_limit = int(api_worker_setting or default_api_workers)
    except Exception:
        api_worker_limit = default_api_workers
    max_scene_workers = max(1, min(16, int(max_scene_workers)))
    api_worker_limit = max(1, min(12, int(api_worker_limit)))
    stage_started_at = time.monotonic()
    # Scale the finite wall with scene count. Request timeouts, the stage hard
    # cap, and the shared delivery budget still bound unhealthy providers and
    # protect the render reserve on long jobs.
    default_stage_deadline = _default_web_stage_deadline_seconds(len(scene_texts))

    try:
        # Round 13: in wait mode the stage has no hard deadline — the
        # web lane runs until every scene resolves. The delivery
        # budget is still the outer job backstop (check_job_active
        # raises when the job's live delivery deadline is reached, and
        # that deadline moves forward on genuine progress), and every
        # provider call keeps its own network timeout. The bounded
        # computation and the delivery-budget allowance clamp are both
        # skipped here: a fixed wall is exactly the kill this fix
        # removes, and the allowance would re-impose one.
        if wait_until_complete:
            stage_deadline_seconds = float("inf")
        else:
            stage_deadline_seconds = _bounded_web_seconds(
                stage_deadline_seconds
                if stage_deadline_seconds is not None
                else os.environ.get("VIDZEO_WEB_STAGE_DEADLINE_SECONDS", "")
                or default_stage_deadline,
                default_stage_deadline,
                _web_stage_hard_cap_seconds(),
            )
    except Exception:
        stage_deadline_seconds = float("inf") if wait_until_complete else default_stage_deadline
    if delivery_budget is not None and not wait_until_complete:
        try:
            budget_stage_seconds = delivery_budget.allowance(
                stage_deadline_seconds,
                reserve_after_seconds=delivery_budget.render_reserve_seconds,
            )
            if budget_stage_seconds < stage_deadline_seconds:
                _web_log(
                    f"delivery budget: retrieval wall clamped "
                    f"{stage_deadline_seconds:.0f}s -> {budget_stage_seconds:.0f}s"
                )
            stage_deadline_seconds = max(0.05, budget_stage_seconds)
        except Exception:
            pass
    if exhaustive_fallback and not wait_until_complete:
        # Recovery must get a real budget (Wright, Nov 16: the recovery
        # run got only a ~22s wall and died before any engine could
        # answer).  A recovery stage below one engine round-trip can never
        # return a candidate, so floor it at one useful wave — bounded by
        # the job's remaining wall time, never past the absolute deadline.
        # This may dip into the render reserve by at most the floor; the
        # render phase itself is progress-supervised, not wall-bound.
        #
        # Run-6 proof (job 453e5590): the old 60s floor was not the binding
        # number — the adapter's 90s recovery wall was.  90s does not fit
        # the real phase budget (shared batch ~24s + prefetch ~33s + scene
        # waves at 2 workers), so the branch died with 0/7 resolved.  Floor
        # the wall at the phase budget instead: batch + prefetch + one wave
        # per worker slot.  The delivery-budget allowance still caps the
        # floor (a nearly-spent budget gets at most its remaining time),
        # and the hard cap bounds a wedged stage.
        try:
            batch_wall = float(
                batch_discovery_seconds
                if batch_discovery_seconds is not None
                else os.environ.get("VIDZEO_WEB_BATCH_DISCOVERY_SECONDS", "45")
                or 45
            )
        except (TypeError, ValueError):
            batch_wall = 45.0
        useful_floor = _web_recovery_useful_floor_seconds(
            len(scene_texts), max_scene_workers, batch_seconds=batch_wall,
        )
        if delivery_budget is not None:
            try:
                remaining = float(delivery_budget.remaining_seconds())
            except Exception:
                remaining = 0.0
            floored = min(
                max(_web_recovery_budget_floor_seconds(stage_deadline_seconds, remaining), useful_floor),
                remaining,
                _web_stage_hard_cap_seconds(),
            )
        else:
            floored = min(
                max(stage_deadline_seconds, useful_floor),
                _web_stage_hard_cap_seconds(),
            )
        if floored != stage_deadline_seconds:
            _web_log(f"recovery budget: stage wall {stage_deadline_seconds:.0f}s -> {floored:.0f}s")
        stage_deadline_seconds = floored
    stage_deadline_at = stage_started_at + stage_deadline_seconds
    _web_log("=== WEB IMAGE PIPELINE START ===")
    _web_log(f"scenes: {len(scene_texts)}, title: {title[:60]}")
    if wait_until_complete:
        _web_log(
            "web branch wait mode: enabled; no hard stage budget — "
            "scenes resolve on provider + per-request timeouts only; "
            "the job delivery budget remains the outer backstop"
        )
    if exhaustive_fallback:
        _web_log(
            "pre-stock exhaustive recovery enabled: fast="
            f"{','.join(name for name, _kind in _FAST_SOURCES)}; "
            "slow=optional; "
            "tiered multi-source recovery; adaptive early-stop=on; stock remains terminal"
        )
    _web_log("Flickr-first broad search enabled (set VIDZEO_WEB_VERBOSE=1 for per-scene query logs)")

    n = len(scene_texts)
    semantic_query_locked = bool(
        scene_queries_override
        and any(
            isinstance(row, Mapping) and row.get("_semantic_locked")
            for row in scene_queries_override
        )
    )
    # Locked production jobs carry a real identity contract and usually need
    # more than one query wave per scene. Keep those waves fair: with the old
    # six-worker default, the first few scenes could spend the whole stage on
    # canonical retries while the remaining scenes never reached their first
    # provider query. This raises concurrency only for the contract-backed
    # path and still honors an explicit operator setting.
    if n >= 16 and fast_mode:
        # The packaged environment still carries the legacy
        # VIDZEO_WEB_SCENE_WORKERS=6 value. On a long V2 storyboard that
        # serializes four scene waves behind slow provider/download calls;
        # the stage expires before most scenes even reach a candidate. Mixed
        # Media already bounds the fan-out at twelve, so promote stale lower
        # values for every fast production pass, while preserving an explicit
        # higher operator value up to the process-safe cap.
        try:
            mixed_workers = int(
                os.environ.get("VIDZEO_MIXED_WEB_SCENE_WORKERS", "12") or 12
            )
        except (TypeError, ValueError):
            mixed_workers = 12
        max_scene_workers = max(
            max_scene_workers,
            min(12, max(1, mixed_workers), n),
        )
    # A semantically locked job has a canonical subject and a required visual
    # contract, so it can safely opt into the bounded scraper recall lane. An
    # explicit operator setting still wins for legacy jobs. URL-only scraper
    # rows are never treated as identity evidence; they remain visual-pending.
    gdl_setting = os.environ.get("VIDZEO_WEB_ENABLE_GDL")
    if enable_gallery_dl is None:
        if gdl_setting is None:
            # gallery-dl is an optional recall lane, never the default path.
            # Its subprocess can outlive a cancelled scene worker on Windows;
            # callers that explicitly want it can still opt in via the env
            # switch or an explicit gallery_cmd.
            enable_gdl = False
        else:
            enable_gdl = str(gdl_setting).strip().lower() in {"1", "true", "yes", "on"}
    else:
        enable_gdl = bool(enable_gallery_dl)
    gdl = gallery_cmd if gallery_cmd else (resolve_gallery_dl(required=False) if enable_gdl else None)
    if gdl and _web_verbose():
        _web_log(f"gallery-dl enabled: {' '.join(gdl)}")
    elif _web_verbose():
        _web_log(
            "gallery-dl unavailable — scraper recall disabled"
            if enable_gdl
            else "gallery-dl disabled for this resolver pass"
        )
    # Resolve the immutable subject before touching the legacy subject
    # extractor.  The old extractor is intentionally broad and can promote an
    # incidental phrase from a scene (for example, "Geneva Motor") into the
    # apparent topic.  When the semantic query contract is present, that
    # provisional subject must never reach disambiguation, query warming, or
    # recovery code, even briefly.
    locked_subject = ""
    if semantic_query_locked:
        locked_subjects = [
            str((row.get("visual_requirement") or {}).get("canonical_entity") or "").strip()
            for row in (scene_queries_override or [])
            if isinstance(row, Mapping) and isinstance(row.get("visual_requirement"), Mapping)
        ]
        locked_subject = next((value for value in locked_subjects if value), "")
    namespace = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(output_namespace or "").strip())[:48]
    if not namespace:
        namespace = f"run_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    # A single job can run several resolver passes (primary, stock rescue,
    # and a final web rescue).  Those passes often share ``output_namespace``.
    # Removing the directory here invalidates paths already handed to the
    # media coordinator when the next pass starts, which later surfaces as a
    # misleading ``missing_file`` MediaContractError.  Keep each pass in its
    # own directory so every selected asset remains valid until the job-level
    # cleanup runs after rendering.
    output_dir = TEMP_DIR / f"web_images_simple_{namespace}"
    if output_dir.exists():
        output_dir = TEMP_DIR / f"web_images_simple_{namespace}_{uuid.uuid4().hex[:8]}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Extract main subject ──
    from .query import _extract_main_subject, _clean_query, _extract_visual_keywords, _build_specific_queries, _extract_all_potential_subjects

    # Prefer multi-word proper names (e.g. "Virat Kohli") over single names (e.g. "Kohlis")
    main_subject = locked_subject or (subject_override or "").strip()
    if not main_subject:
        candidates = []
        for source_text in [title, script, " ".join(scene_texts)]:
            candidate = _extract_main_subject(source_text) if source_text else ""
            if candidate:
                candidates.append(candidate)
        
        # Sort candidates: prefer multi-word names, then longer names
        candidates.sort(key=lambda c: (len(c.split()) >= 2, len(c)), reverse=True)
        if candidates:
            main_subject = candidates[0]
            
    # Extract all potential biographical subjects for scene-specific dynamic mapping
    combined_text = ". ".join(filter(None, [title, script] + scene_texts))
    potential_subjects = (
        []
        if semantic_query_locked
        else _extract_all_potential_subjects(combined_text)
    )
    
    if main_subject:
        _web_log(f"main subject: '{main_subject}'")

    # Context-aware LLM Entity Expansion in Resolver:
    # If main_subject is a single-word PROPER NOUN (capitalized) and api_key is available,
    # use LLM to expand it (e.g. "Kohli" → "Virat Kohli").
    # Skip if main_subject is a common noun (lowercase) like "brain", "driving", "coffee"
    # — expanding those to person names destroys Flickr search results.
    if (
        not semantic_query_locked
        and api_key
        and main_subject
        and main_subject[0].isupper()
        and len(main_subject.split()) < 2
    ):
        try:
            disambig_response = ""
            if not disambig_response:
                from services.openrouter_llm import OpenRouterLLM
                llm = OpenRouterLLM(api_key=api_key, model=llm_model or "qwen/qwen3.5-flash-02-23")
                context_sample = (script[:1500] if script else title)[:1500]
                disambig_prompt = (
                    "You are an expert entity disambiguation system.\n"
                    "Identify the primary famous proper-noun subject (person/entity) in the following text.\n"
                    "Always return their full, natural, globally recognized proper name (e.g. 'Virat Kohli' instead of 'Kohli', 'Steve Jobs' instead of 'Jobs', 'Lionel Messi' instead of 'Messi', 'Elon Musk' instead of 'Musk').\n"
                    "Return ONLY the plain full name (2-3 words, no punctuation, no 'The main subject is', no explanation)."
                )
                disambig_response = llm.chat(
                    user_prompt=f"Text:\n{context_sample}\n\nIdentify and expand the primary subject to their full famous proper name:",
                    system_prompt=disambig_prompt,
                    temperature=0.1,
                    max_tokens=20
                ).strip()
            
            disambig_response = re.sub(r"[^\w\s\-]", "", disambig_response).strip()
            if disambig_response and len(disambig_response.split()) >= 2 and len(disambig_response) < 50:
                _web_log(f"subject detection: expanded '{main_subject}' to full proper name '{disambig_response}' using LLM world knowledge in resolver")
                main_subject = disambig_response
        except Exception as e:
            _web_log(f"LLM subject expansion in resolver failed: {e}")

    if semantic_query_locked and locked_subject:
        _web_log(f"semantic query lock: canonical resolver subject '{main_subject}'")

    # ── Build queries per scene ──
    queries_per_scene = [[] for _ in range(n)]
    scene_active_subjects = [main_subject] * n
    try:
        from .source_broker import (
            DiscoveryCache,
            SourceHealth,
            SourceTelemetry,
            fair_round_robin_indices,
            sanitize_query_pack,
        )
        source_telemetry = SourceTelemetry()
        # A single empty/timeout wave is not proof that a multisource lane is
        # down. Long jobs fan out many scene queries concurrently, so the old
        # two-failure breaker disabled the universal scraper after two
        # query-specific timeouts and starved the remaining scenes. Keep the
        # definitive Yandex robot trip immediate, but require repeated
        # failures before suppressing a broad provider lane.
        source_health = SourceHealth(failure_threshold=4, cooldown_seconds=30.0)
        # Probe provider DNS once per job.  A host that cannot resolve at all
        # (e.g. poisoned commons.wikimedia.org) is skipped without waiting for
        # every scene query to fail against it.
        for _prov_name, _prov_kind in _enabled_fast_sources():
            _prov_host = _PROVIDER_HOSTS.get(_prov_name)
            # Getty's site-filter lanes are complementary rescue sources; do
            # not let their occasionally slow DNS lookup delay the entire
            # resolver before the normal provider fan-out starts.
            if (
                _prov_host
                and _prov_name not in {"getty_bing"}
                # A short operator deadline is already a hard latency budget;
                # serial DNS probes can consume most of it before retrieval
                # starts. Provider calls remain individually fail-soft.
                and stage_deadline_seconds >= 10.0
            ):
                source_health.preflight(_prov_name, _prov_host)
        try:
            requested_provider_cap = int(
                os.environ.get("VIDZEO_WEB_PROVIDER_REQUEST_CAP", "0") or 0
            )
        except Exception:
            requested_provider_cap = 0
        # The stage deadline and bounded workers are the default wall. If an
        # operator elects a request cap, reserve at least one provider attempt
        # per scene so later list items cannot be starved by early scenes.
        provider_request_cap = (
            None if requested_provider_cap <= 0 else max(n, requested_provider_cap)
        )
        discovery_cache = DiscoveryCache(
            # An unbounded branch still needs a finite cache TTL so a long
            # running process does not retain one job's provider rows forever.
            success_ttl=(3600.0 if wait_until_complete else max(60.0, stage_deadline_seconds + 30.0)),
            empty_ttl=30.0,
            max_provider_requests=provider_request_cap,
        )
    except Exception:
        DiscoveryCache = None
        SourceTelemetry = None
        sanitize_query_pack = None
        fair_round_robin_indices = lambda values: list(range(len(list(values or []))))
        source_telemetry = None
        source_health = None
        discovery_cache = None
        provider_request_cap = 0

    global _ACTIVE_PROVIDER_HEALTH
    _prev_provider_health = _ACTIVE_PROVIDER_HEALTH
    _ACTIVE_PROVIDER_HEALTH = source_health
    # Fresh per-job Openverse budget for the muse-search wave: the
    # anonymous 200/day cap is per job here, not per process lifetime.
    reset_muse_search_job_budget()
    # Round 10: stage-scoped egress-outage circuit (job e4d610b9). One
    # tracker for the whole stage; every scene worker reports its
    # provider calls to it and sleeps through its backoffs instead of
    # hammering a dead egress. Backoff time is added to the stage
    # deadline, so an outage delays the job but never starves it.
    outage_tracker = WebOutageTracker()

    for idx in range(n):
        queries = []
        scene_text = scene_texts[idx]
        
        # Determine the active proper-noun subject of this scene
        active_subject = main_subject
        if len(potential_subjects) > 1 and scene_text:
            scene_text_lower = scene_text.lower()
            for subj in potential_subjects:
                if not _should_switch_active_subject(subj, main_subject, scene_text):
                    continue
                subj_lower = subj.lower()
                subj_words = [w for w in subj_lower.split() if len(w) >= 3]
                if subj_words and any(re.search(r'\b' + re.escape(w) + r'\b', scene_text_lower) for w in subj_words):
                    active_subject = subj
                    break
        if _is_weak_active_subject(active_subject, main_subject) and main_subject:
            active_subject = main_subject

        # Use prebuilt queries if available
        if scene_queries_override and idx < len(scene_queries_override):
            pb = scene_queries_override[idx]
            if isinstance(pb, dict):
                # Under a short operator deadline, planner-provided variants
                # are already subject-qualified. Avoid the legacy repair and
                # disambiguation passes (which can spend several seconds over
                # a long scene list) and preserve the caller's exact query
                # intent for the shared-pool resolver.
                if (
                    stage_deadline_seconds <= 5.0
                    and isinstance(pb.get("query_variants"), list)
                    and pb.get("query_variants")
                    and not pb.get("_semantic_locked")
                ):
                    active_subject = str(
                        pb.get("section_subject")
                        or pb.get("scene_subject")
                        or pb.get("subject_query")
                        or active_subject
                        or main_subject
                    ).strip()
                    queries = list(dict.fromkeys(
                        _clean_query(str(query))
                        for query in pb.get("query_variants")
                        if str(query or "").strip()
                    ))[:8]
                    queries_per_scene[idx] = [query for query in queries if len(query) > 2]
                    scene_active_subjects[idx] = active_subject
                    continue
                semantic_requirement = pb.get("visual_requirement")
                if pb.get("_semantic_locked") and isinstance(semantic_requirement, Mapping):
                    # The semantic planner has already disambiguated the
                    # scene. Do not run the legacy proper-noun switcher,
                    # listicle repair, or global-topic fallback for this row.
                    active_subject = str(
                        semantic_requirement.get("canonical_entity") or main_subject or title
                    ).strip()
                    # Nov 22 (d1ece590 stall fix): the semantic lock trusts
                    # the planner's identity — and this branch skips the
                    # legacy weak-subject rescue below, so a weak identity
                    # (a bare pronoun such as 'They', which the mechanical
                    # planner used to emit for pronoun-only narration)
                    # reached the providers unchecked and became the garbage
                    # queries 'They documentary drove photograph' etc.
                    # A weak semantic identity falls back to the film's
                    # main subject here, and the cleaning net below drops
                    # any degenerate prebuilt variants.
                    if _is_weak_active_subject(active_subject, main_subject):
                        _web_log(
                            f"scene {idx + 1}: weak semantic identity "
                            f"'{active_subject}' -> falling back to '{main_subject}'"
                        )
                        active_subject = main_subject
                        semantic_requirement = dict(semantic_requirement)
                        semantic_requirement["canonical_entity"] = main_subject
                    # V2 supplies an immutable, scene-local web query pack.
                    # Preserve it here instead of regenerating only the broad
                    # canonical variants; regeneration was the reason a
                    # scene mentioning King Charles or Buckingham Palace
                    # collapsed back to the shared "King" topic pool.
                    prebuilt_web_queries = pb.get("web_query_variants")
                    if isinstance(prebuilt_web_queries, (list, tuple)) and prebuilt_web_queries:
                        queries = [
                            _clean_query(str(query))
                            for query in prebuilt_web_queries
                            if str(query or "").strip()
                        ]
                    else:
                        queries = provider_query_variants(semantic_requirement, provider="web")
                    try:
                        from .query import clean_scene_queries
                        queries = clean_scene_queries(queries, fallback_subject=active_subject)
                    except Exception:
                        pass
                    queries_per_scene[idx] = list(dict.fromkeys(queries))[:8]
                    scene_active_subjects[idx] = active_subject
                    if _web_verbose():
                        _web_log(
                            f"scene {idx + 1} semantic lock: {queries_per_scene[idx]} "
                            f"requirement={semantic_requirement.get('requirement_hash', '')[:12]}"
                        )
                    continue
                forced_section_subject = ""
                try:
                    from services.container_guard import (
                        canonical_container_subject,
                        strip_forbidden_subjects,
                        text_mentions_subject,
                    )
                    forced_section_subject = canonical_container_subject(
                        pb.get("section_subject")
                        or pb.get("scene_subject")
                        or pb.get("subject_query")
                        or "",
                        scene_text,
                        title,
                    )
                    if forced_section_subject and _forced_subject_rejected(
                        forced_section_subject, main_subject, scene_text
                    ):
                        _web_log(
                            f"scene {idx + 1}: weak forced planner subject "
                            f"'{forced_section_subject}' -> falling back to '{main_subject}'"
                        )
                        forced_section_subject = main_subject
                    if forced_section_subject:
                        active_subject = forced_section_subject
                except Exception:
                    strip_forbidden_subjects = None
                    text_mentions_subject = None
                raw_extracted = []
                if "query_variants" in pb and isinstance(pb["query_variants"], list):
                    for q in pb["query_variants"]:
                        if q and isinstance(q, str):
                            raw_extracted.append(q)
                
                # Also fallback to individual fields if query_variants list is empty/missing
                for key in ("specific_query", "medium_query", "general_query", "search_query", "primary_query",
                           "supporting_search_query", "alt_search_query", "primary", "supporting", "fallback"):
                    q = pb.get(key, "")
                    if q and isinstance(q, str) and q not in raw_extracted:
                        raw_extracted.append(q)

                try:
                    from .query import _repair_query_pack
                except Exception:
                    _repair_query_pack = None
                if strip_forbidden_subjects:
                    forbidden_subjects = pb.get("forbidden_subjects") if isinstance(pb.get("forbidden_subjects"), list) else []
                    raw_extracted = [
                        strip_forbidden_subjects(query, forbidden_subjects)
                        for query in raw_extracted
                    ]
                if _repair_query_pack:
                    raw_extracted = _repair_query_pack(
                        raw_extracted,
                        active_subject or main_subject or title,
                        scene_text,
                        limit=5,
                        context_text=script,
                    )

                for q in raw_extracted:
                    c = _clean_query(q)
                    # Strip leading stopwords (e.g. "As Kohli batting" → "Kohli batting")
                    while c and c.lower().split() and c.lower().split()[0] in STOPWORDS:
                        c = " ".join(c.split()[1:]).strip()
                    if c and len(c) > 2:
                        # Reject if dominated by abstract/bad terms
                        if _has_bad_terms_relaxed(c):
                            continue
                            
                        # Check if this specific query references a different potential subject
                        query_active_subject = forced_section_subject or active_subject
                        if not forced_section_subject and len(potential_subjects) > 1:
                            c_lower = c.lower()
                            for subj in potential_subjects:
                                if not _should_switch_active_subject(subj, main_subject, c):
                                    continue
                                subj_lower = subj.lower()
                                subj_words = [w for w in subj_lower.split() if len(w) >= 3]
                                if subj_words and any(re.search(r'\b' + re.escape(w) + r'\b', c_lower) for w in subj_words):
                                    query_active_subject = subj
                                    break
                                    
                        # Update the overall scene active subject if a query matches a new biographical subject
                        if (
                            not forced_section_subject
                            and query_active_subject != active_subject
                            and query_active_subject != main_subject
                        ):
                            active_subject = query_active_subject

                        # Filter individual stopword-only queries like "Kohli but"
                        words = c.lower().split()
                        content_words = [w for w in words if w not in STOPWORDS and w not in BAD_QUERY_TERMS]
                        is_entity_subject = query_active_subject and not any(w in query_active_subject.lower() for w in _GENERIC_TOPIC_WORDS)
                        if is_entity_subject:
                            c_lower = c.lower()
                            subj_words = set(query_active_subject.lower().split())
                            # Check if at least one significant subject word (3+ chars) is already in the query
                            significant_subj_words = {w for w in subj_words if len(w) >= 3}
                            has_any_subject_word = any(re.search(r'\b' + re.escape(w) + r'\b', c_lower) for w in significant_subj_words)
                            
                            if query_active_subject.lower() in c_lower:
                                # Full subject already present. Clean up adjacent duplicate words.
                                words = c.split()
                                cleaned = []
                                for w in words:
                                    if not cleaned or cleaned[-1].lower() != w.lower():
                                        cleaned.append(w)
                                c = " ".join(cleaned)
                            elif has_any_subject_word:
                                # A partial name is precisely the dangerous
                                # case: ``Harry interview`` can mean a singer,
                                # while ``Kohli trophy`` is less reliable than
                                # ``Virat Kohli trophy``. Replace partial
                                # identity tokens and prepend the complete
                                # section subject instead of accepting it.
                                subject_tokens = [
                                    token for token in re.findall(r"[A-Za-z0-9]+", query_active_subject)
                                    if len(token) >= 3
                                ]
                                if len(subject_tokens) >= 2:
                                    for token in subject_tokens:
                                        c = re.sub(r"\b" + re.escape(token) + r"\b", "", c, flags=re.IGNORECASE)
                                    cleaned_partial = re.sub(r"\s+", " ", c).strip()
                                    c = f"{query_active_subject} {cleaned_partial}".strip()
                            elif content_words:
                                # No subject reference at all — prepend the full subject name
                                c = f"{query_active_subject} {c}"
                        if forced_section_subject and text_mentions_subject:
                            if strip_forbidden_subjects:
                                c = strip_forbidden_subjects(c, pb.get("forbidden_subjects") or [])
                            if not text_mentions_subject(c, forced_section_subject):
                                c = f"{forced_section_subject} {c}"
                        # Skip queries that are just subject + stopword/bad term (e.g. "Kohli but")
                        if query_active_subject and len(content_words) == 0:
                            continue
                        # Skip queries like "Kohli mental" where mental is a bad term
                        if query_active_subject and len(content_words) <= 1:
                            subj_words = set(query_active_subject.lower().split())
                            non_subj_content = [w for w in content_words if w not in subj_words]
                            if all(w in BAD_QUERY_TERMS for w in non_subj_content):
                                continue
                        # Deduplicate any doubled subjects (e.g. "Virat Kohli Virat Kohli")
                        if query_active_subject:
                            c = c.replace(f"{query_active_subject} {query_active_subject}", query_active_subject)
                        if c and len(c) > 2:
                            queries.append(c)

        # Mechanical fallback — specific, context-aware queries
        if not queries:
            scene_kws = _extract_visual_keywords(scene_text, max_kws=8)

            is_entity_subject = active_subject and not any(w in active_subject.lower() for w in _GENERIC_TOPIC_WORDS)
            if is_entity_subject:
                enrichment_terms = _pick_scene_enrichments(active_subject, scene_text, idx)
                specific_queries = _build_specific_queries(
                    active_subject, enrichment_terms, scene_text, script
                )
                queries = specific_queries if specific_queries else [active_subject]
            else:
                subject_anchor = active_subject or main_subject or title or ""
                if len(scene_kws) >= 3:
                    if subject_anchor:
                        queries.append(f"{subject_anchor} {scene_kws[0]} {scene_kws[1]}")
                        queries.append(f"{subject_anchor} {scene_kws[1]} {scene_kws[2]}")
                        if len(scene_kws) >= 4:
                            queries.append(f"{subject_anchor} {scene_kws[0]} {scene_kws[2]}")
                        queries.append(f"{subject_anchor} {scene_kws[0]}")
                    else:
                        queries.append(f"{scene_kws[0]} {scene_kws[1]}")
                        queries.append(f"{scene_kws[1]} {scene_kws[2]}")
                        if len(scene_kws) >= 4:
                            queries.append(f"{scene_kws[0]} {scene_kws[2]}")
                        queries.append(f"{scene_kws[0]} documentary photograph")
                elif len(scene_kws) == 2:
                    if subject_anchor:
                        queries.append(f"{subject_anchor} {scene_kws[0]} {scene_kws[1]}")
                        queries.append(f"{subject_anchor} {scene_kws[0]}")
                    else:
                        queries.append(f"{scene_kws[0]} {scene_kws[1]}")
                        queries.append(f"{scene_kws[0]} documentary photograph")
                elif len(scene_kws) == 1:
                    if subject_anchor:
                        queries.append(f"{subject_anchor} {scene_kws[0]}")
                        queries.append(f"{subject_anchor} documentary photograph")
                    else:
                        queries.append(f"{scene_kws[0]} documentary photograph")
                else:
                    title_kws = _extract_visual_keywords(title or "", max_kws=4) if title else []
                    if subject_anchor:
                        queries.append(f"{subject_anchor} documentary photograph")
                    elif len(title_kws) >= 2:
                        queries.append(f"{title_kws[0]} {title_kws[1]}")
                    if not queries:
                        queries = [subject_anchor or "documentary photograph"]

        queries = list(dict.fromkeys(q for q in queries if q and len(q) > 2))[:8]
        if not queries:
            if active_subject:
                try:
                    from .query import _is_person_fallback_subject, _topic_fallback_queries
                except Exception:
                    _is_person_fallback_subject = None
                    _topic_fallback_queries = None
                if _is_person_fallback_subject and _is_person_fallback_subject(active_subject):
                    queries = [active_subject, f"{active_subject} photo", f"{active_subject} portrait"]
                elif _topic_fallback_queries:
                    queries = _topic_fallback_queries(active_subject, scene_text, limit=5)
                else:
                    queries = [active_subject]
            else:
                queries = ["photo", "portrait"]
        queries = _diversify_weak_scene_queries(queries, scene_text, script, title, active_subject or main_subject, idx)
        try:
            from .query import _repair_query_pack
        except Exception:
            _repair_query_pack = None
        if _repair_query_pack:
            queries = _repair_query_pack(
                queries,
                active_subject or main_subject or title,
                scene_text,
                limit=5,
                context_text=script,
            )

        if sanitize_query_pack:
            queries, query_repairs = sanitize_query_pack(
                queries,
                subject=active_subject or main_subject or title,
                scene_text=scene_text,
                limit=8,
            )
            if source_telemetry is not None:
                source_telemetry.record_query_repairs(idx + 1, query_repairs)

        queries_per_scene[idx] = queries
        scene_active_subjects[idx] = active_subject
        if _web_verbose():
            _web_log(f"scene {idx + 1} queries: {queries[:4]} (active subject: '{active_subject}')")

    # ── Resolve all scenes in parallel ──
    # Warm a single canonical provider pool for predominantly single-topic
    # jobs. Per-scene queries remain as fallbacks, but are not all launched in
    # a provider stampede at branch start.
    shared_pool_query = _shared_batch_subject(
        main_subject,
        scene_active_subjects,
        scene_texts,
        topic_anchor=title,
    )
    shared_batch = {
        "query": "",
        "candidates": 0,
        "providers_completed": 0,
        "providers_pending": 0,
    }
    # A caller may intentionally provide a sub-second/short deadline (tests,
    # health probes, or a nearly exhausted delivery budget).  Warming a
    # multi-provider pool in that window spends the entire budget before the
    # scene workers can return their already-completed candidates.  Keep the
    # shared lane for normal production windows only.
    if (
        shared_pool_query
        and (stage_deadline_seconds >= 6.0 or len(scene_texts) > 3)
        and time.monotonic() < stage_deadline_at
    ):
        shared_batch = _warm_shared_discovery_pool(
            query=shared_pool_query,
            scene_count=n,
            gallery_cmd=gdl,
            discovery_cache=discovery_cache,
            source_telemetry=source_telemetry,
            stage_deadline_at=stage_deadline_at,
            source_health=source_health,
            batch_seconds_override=batch_discovery_seconds,
            wait_until_complete=wait_until_complete,
        )
        for scene_index, scene_queries in enumerate(queries_per_scene):
            # Only append shared_pool_query as an emergency fallback at the end of the query list.
            # Never place it before the scene's own specific entity queries!
            if scene_queries:
                queries_per_scene[scene_index] = list(dict.fromkeys(
                    list(scene_queries) + [shared_pool_query]
                ))[:8]
            else:
                queries_per_scene[scene_index] = [shared_pool_query]

    # Assign distinct warmed-pool rows to scenes up front. This is a small
    # coordination layer around the parallel resolver: each scene keeps its
    # own best matching topic image even when another worker finishes first.
    shared_preferred_by_scene: Dict[int, str] = {}
    shared_reserved_owners: Dict[str, int] = {}
    shared_rows = list(shared_batch.get("candidate_rows") or [])
    # Download the warm topic pool once, before scene workers fan out. The
    # previous per-scene transport fetched the same CDN URLs repeatedly and
    # exhausted provider limits before later scenes got a turn.
    download_cache: Dict[str, Optional[str]] = {}
    download_cache_lock = threading.Lock()
    if shared_rows and time.monotonic() < stage_deadline_at:
        try:
            # The shared topic pool is a warm start, not a substitute for
            # exact per-scene queries. A large multi-identity job used to
            # synchronously download up to 28 broad-pool assets here, burning
            # the retrieval wall before its scene workers could search for
            # William, Charles, Archie, and the other named subjects.
            semantic_entities = {
                str((row.get("visual_requirement") or {}).get("canonical_entity") or "")
                .strip()
                .casefold()
                for row in (scene_queries_override or [])
                if isinstance(row, Mapping)
                and isinstance(row.get("visual_requirement"), Mapping)
                and str((row.get("visual_requirement") or {}).get("canonical_entity") or "").strip()
            }
            multi_identity_job = bool(semantic_query_locked and len(semantic_entities) > 1)
            prefetch_limit = _shared_pool_prefetch_limit(
                n,
                multi_identity_job=multi_identity_job,
                configured_limit=(
                    os.environ.get("VIDZEO_WEB_SHARED_PREFETCH_LIMIT") or None
                ),
            )
        except (TypeError, ValueError):
            prefetch_limit = _shared_pool_prefetch_limit(
                n, multi_identity_job=bool(semantic_query_locked)
            )
        host_groups: Dict[str, List[Dict[str, Any]]] = {}
        for row in shared_rows:
            url = str(row.get("url") or "").strip()
            if not url:
                continue
            host = urllib.parse.urlparse(url).netloc.casefold()
            host_groups.setdefault(host, []).append(row)
        prefetch_rows: List[Dict[str, Any]] = []
        while host_groups and len(prefetch_rows) < prefetch_limit:
            for host in list(host_groups):
                rows_for_host = host_groups.get(host) or []
                if rows_for_host:
                    prefetch_rows.append(rows_for_host.pop(0))
                if not rows_for_host:
                    host_groups.pop(host, None)
                if len(prefetch_rows) >= prefetch_limit:
                    break
        shared_pool_dir = output_dir / "shared_pool"
        # Bounded background prefetch (run-6 proof): the blocking prefetch
        # ran two sequential download rounds plus a pending-collection wait
        # (~33s for 9 rows, 1/9 local) before any scene worker started,
        # burning a third of the 90s wall.  It only warms the shared
        # download cache — scene workers fetch their own picks through the
        # same cache, and in-flight markers let them wait briefly for a
        # download this thread owns — so run it in a daemon thread and
        # proceed after at most _web_shared_prefetch_seconds() (10s / 15%
        # of the wall).  In-flight downloads keep populating the cache.
        prefetch_holder: Dict[str, Any] = {"results": [], "error": None}
        prefetch_seconds = _web_shared_prefetch_seconds(stage_deadline_seconds)

        def _run_shared_pool_prefetch() -> None:
            try:
                prefetch_holder["results"] = _download_candidate_batch(
                    prefetch_rows,
                    shared_pool_dir,
                    max_concurrent=8,
                    # The warm pool is an accelerator, not a blocking provider
                    # stage — but its budget must still cover a real transfer
                    # (round 6: 5 s could never land a ~16-18 s healthy-hour
                    # download, so the warm pool always came back 0/15).
                    # Scene workers still have their own bounded retries, and
                    # the main thread only joins the prefetch for its seconds.
                    timeout_seconds=_shared_pool_prefetch_timeout_seconds(
                        fast_mode, stage_deadline_at
                    ),
                    download_cache=download_cache,
                    download_cache_lock=download_cache_lock,
                )
            except Exception as exc:  # noqa: BLE001 - best-effort warm-up
                prefetch_holder["error"] = exc

        try:
            prefetch_thread = JobThread(
                target=_run_shared_pool_prefetch,
                name="web-shared-prefetch",
                daemon=True,
            )
            prefetch_thread.start()
            prefetch_thread.join(timeout=prefetch_seconds)
            if prefetch_thread.is_alive():
                _web_log(
                    f"shared pool prefetch exceeded {prefetch_seconds:.0f}s; "
                    "continuing with scene workers (in-flight downloads "
                    "keep warming the shared cache)"
                )
            elif prefetch_holder["error"] is not None:
                raise prefetch_holder["error"]
            prefetch_results = list(prefetch_holder["results"] or [])
            prefetch_paths = {url: path for url, path, _error in prefetch_results if path and Path(path).is_file()}
            for row in shared_rows:
                path = prefetch_paths.get(str(row.get("url") or "").strip())
                if path:
                    row["path"] = str(path)
            _web_log(
                f"shared pool prefetch: {len(prefetch_paths)}/{len(prefetch_rows)} local assets"
            )
        except Exception as exc:
            _web_log(f"shared pool prefetch unavailable: {type(exc).__name__}: {exc}")
    if shared_pool_query and shared_rows:
        subject_tokens = {
            token.lower()
            for token in re.findall(r"[a-z0-9]+", str(shared_pool_query))
            if len(token) >= 3
        }
        generic_tokens = {
            "archival", "archive", "archived", "historical", "history",
            "photo", "photograph", "photography", "documentary", "scene",
        }
        remaining_rows = [row for row in shared_rows if row.get("url")]
        for scene_index in range(n):
            first_query = str((queries_per_scene[scene_index] or [""])[0])
            cue_tokens = [
                token.lower()
                for token in re.findall(r"[a-z0-9]+", first_query)
                if len(token) >= 3 and token.lower() not in subject_tokens and token.lower() not in generic_tokens
            ]
            if not cue_tokens:
                continue
            ranked_rows = sorted(
                remaining_rows,
                key=lambda row: sum(
                    token in " ".join(
                        str(row.get(field) or "")
                        for field in ("title", "tags", "description")
                    ).lower()
                    for token in cue_tokens
                ),
                reverse=True,
            )
            if not ranked_rows:
                break
            best = ranked_rows[0]
            haystack = " ".join(
                str(best.get(field) or "")
                for field in ("title", "tags", "description")
            ).lower()
            if not any(token in haystack for token in cue_tokens):
                continue
            best_url = str(best.get("url") or "").strip()
            if not best_url:
                continue
            shared_preferred_by_scene[scene_index] = best_url
            shared_reserved_owners[best_url] = scene_index
            remaining_rows = [row for row in remaining_rows if str(row.get("url") or "") != best_url]

    global_phashes: Dict[int, int] = {}
    global_md5s: Dict[str, int] = {}
    try:
        _max_reuse = int(os.environ.get('VIDZEO_WEB_IMAGE_REUSE_CAP', '1') or 1)
    except Exception:
        _max_reuse = 1
    used_files: Set[str] = set()
    used_urls: Set[str] = set()
    phash_lock = threading.Lock()
    md5_lock = threading.Lock()
    file_lock = threading.Lock()
    url_lock = threading.Lock()
    results = [None] * n
    # Advance prep: reuse verified web images pre-fetched while idle, keyed
    # by (title, scene narration, query).  Hit scenes skip provider search
    # entirely but still flow through the live visual pass below.  The lookup
    # also tries each scene's raw planner variants: prep records keys under
    # those, and the repair pass above rewrites most of them.
    try:
        _raw_queries_per_scene = [
            _override_raw_queries(scene_queries_override[idx])
            if scene_queries_override and idx < len(scene_queries_override)
            else []
            for idx in range(n)
        ]
        for _hit_idx, _hit_row in _prep_cache_hits_for_scenes(
            title, scene_texts, queries_per_scene, output_dir,
            raw_queries_per_scene=_raw_queries_per_scene,
        ).items():
            results[_hit_idx] = _hit_row
    except Exception as exc:
        _web_log(f"prep cache lookup failed (non-fatal): {type(exc).__name__}: {exc}")
    # One audit token per call (GTA fix, Nov 16): _resolve_scene records
    # each scene's universal engine audits under this token so the
    # per-scene audit entry carries them even for unresolved scenes.
    audit_token = uuid.uuid4().hex[:12]

    # Terms appearing in two or more scene narrations are weak discriminators
    # (e.g. "deep sea" in both the welder and cave chapters) and must not
    # satisfy the per-scene relevance gate by themselves.
    from collections import Counter as _TermCounter

    _term_counter = _TermCounter()
    for _txt in scene_texts:
        _term_counter.update(
            t
            for t in re.findall(r"[a-z]{3,}", (_txt or "").lower())
            if t not in _WEB_GENERIC_STOP and t not in _WEB_CONTAINER_TERMS
        )
    weak_terms = {t for t, count in _term_counter.items() if count >= 2}

    # Vision lanes: the web lane receives its budget from the adapter
    # (a lane child context); no context is auto-minted here. Without
    # an injected context, vision is unavailable and the existing
    # fail-open rules apply — never auto-mint a context with its own
    # N+4 / N+2 call cap, which is exactly how a second shared budget
    # used to appear.
    visual_client_available = False
    if visual_context is not None:
        try:
            visual_client_available = visual_context.get_client() is not None
        except Exception:
            visual_client_available = False

    # Vision lanes: when a lane budget is injected, the visual pool
    # workers are the web in-flight cap (4), not VIDZEO_WEB_VISION_CONCURRENCY.
    _lane_budget_now = getattr(visual_context, "_lane_budget", None) if visual_context is not None else None
    if _lane_budget_now is not None:
        visual_workers = max(1, min(8, int(getattr(_lane_budget_now, "max_inflight", 4) or 4)))
    else:
        visual_workers = max(4, min(8, int(os.environ.get("VIDZEO_WEB_VISION_CONCURRENCY", "6") or 6)))
    streaming_visual_pool = (
        ThreadPoolExecutor(max_workers=visual_workers, thread_name_prefix="web-vision-stream")
        if visual_client_available
        else None
    )

    # Shared visual-result state (design §1.6.3): one new RLock guards
    # every results[idx] write and every _published_scenes mutation,
    # because the streamer thread is a second concurrent writer to
    # both. Defined here — before the drain loop — so the streamer
    # and the drain apply results through one code path.
    _results_lock = threading.RLock()
    visually_completed = set()
    _published_scenes: set = set()

    def _publish_scene(idx: int, result) -> None:
        """Publish one scene's decided row to the caller (coordinator).

        Vision lanes (§3.5): the web loader publishes each scene's
        row as its verdict lands, so the coordinator gate sees it
        during the visual phase instead of only when the whole batch
        returns. Each scene publishes at most once.
        """
        with _results_lock:
            if not callable(on_scene_result) or result is None or idx in _published_scenes:
                return
            _published_scenes.add(idx)
        try:
            on_scene_result(idx, result)
        except Exception:
            pass

    def _apply_visual_result(idx: int, ranked_result) -> None:
        """Apply one cross-scene verdict to results[idx] and publish it.

        Shared by the streamer (during retrieval) and the drain's
        cross-scene call (after retrieval). Same availability retention
        as the legacy wait loop: a client_unavailable strict failure
        keeps the downloaded winner as degraded-unverified. A pass
        stamps _streaming_vision_passed so the drain's existing skip
        works unchanged.
        """
        with _results_lock:
            visually_completed.add(idx)
            if (
                ranked_result
                and ranked_result.get("visual_rejected")
                and not ranked_result.get("path")
                and str(ranked_result.get("visual_reason") or "") == "client_unavailable"
                and results[idx]
                and results[idx].get("path")
            ):
                retained = dict(results[idx])
                retained.update({
                    "visual_outcome": "unavailable",
                    "visual_reason": "client_unavailable",
                    "visual_unverified": True,
                })
                results[idx] = retained
            else:
                stored = ranked_result or {
                    "path": "",
                    "width": 0,
                    "height": 0,
                    "url": "",
                    "domain": "visual_gate",
                    "reused_from_scene": False,
                    "visual_rejected": True,
                }
                if isinstance(stored, dict) and stored.get("visual_outcome") == "pass":
                    stored["_streaming_vision_passed"] = True
                results[idx] = stored
            stored_result = results[idx]
        _publish_scene(idx, stored_result)

    # Web-stream vision (design §1.2): activation gate — all three must
    # hold, else behaviour is exactly today's. Gate 3 keeps the legacy
    # path safe: app.py:_resolve_web_media builds a shared context with
    # no lane budget, so no streaming happens there; only the V2 path
    # (lane child in config['_web_visual_context']) streams.
    _web_streamer = None
    if (
        defer_context_verification
        and visual_context is not None
        and _lane_budget_now is not None
    ):
        try:
            _web_streamer = _WebVisionStreamer(
                visual_context=visual_context,
                lane_budget=_lane_budget_now,
                scene_texts=scene_texts,
                scene_active_subjects=scene_active_subjects,
                title=title,
                results=results,
                results_lock=_results_lock,
                apply_result=_apply_visual_result,
            )
            _web_log("[web] stream vision: streamer active (deferred V2 lane child)")
        except Exception as exc:
            _web_log(f"[web] stream vision unavailable (non-fatal): {type(exc).__name__}")
            _web_streamer = None

    max_scene_workers = min(max(1, int(max_scene_workers or 4)), n)
    _web_log(f"parallel scene workers: {max_scene_workers} (streaming vision workers: {visual_workers if visual_client_available else 0})")
    scene_cancel_event = threading.Event()
    job_cancel = current_job().get("cancellation")
    scene_pool = ThreadPoolExecutor(max_workers=max_scene_workers)
    futures = {}
    fair_scene_indices = fair_round_robin_indices(scene_active_subjects)
    for idx in fair_scene_indices:
        # Advance prep: a cache-hit scene already has its verified image in
        # results[idx]; never file a provider search for it.
        if results[idx] is not None and results[idx].get("path"):
            continue
        f = scene_pool.submit(
            _resolve_scene, idx, scene_texts[idx],
            queries_per_scene[idx], gdl, output_dir,
            scene_active_subjects[idx], global_phashes, global_md5s, used_files,
            phash_lock, md5_lock, file_lock,
            fast_mode=fast_mode,
            max_candidate_downloads=max_candidate_downloads,
            api_worker_limit=api_worker_limit,
            source_telemetry=source_telemetry,
            deadline_at=stage_deadline_at,
            discovery_cache=discovery_cache,
            shared_pool_query=shared_pool_query,
            shared_pool_candidates=list(shared_batch.get("candidate_rows") or []),
            shared_preferred_url=shared_preferred_by_scene.get(idx, ""),
            shared_reserved_owners=shared_reserved_owners,
            cancel_event=scene_cancel_event,
            weak_terms=weak_terms,
            source_health=source_health,
            used_urls=used_urls,
            url_lock=url_lock,
            visual_requirement=(
                scene_queries_override[idx].get("visual_requirement")
                if scene_queries_override
                and idx < len(scene_queries_override)
                and isinstance(scene_queries_override[idx], Mapping)
                else None
            ),
            allow_visual_pending=bool(
                visual_context is not None
                and getattr(visual_context, "visual_required", False)
            ),
            defer_context_verification=defer_context_verification,
            query_seconds_override=query_deadline_seconds,
            visual_context=visual_context,
            visual_pool=streaming_visual_pool,
            global_topic=title or main_subject,
            download_cache=download_cache,
            download_cache_lock=download_cache_lock,
            exhaustive_fallback=exhaustive_fallback,
            wait_until_complete=wait_until_complete,
            prioritize_director_query=bool(
                scene_queries_override
                and idx < len(scene_queries_override)
                and isinstance(scene_queries_override[idx], Mapping)
                and (scene_queries_override[idx].get("director_web_query") or scene_queries_override[idx].get("_llm"))
            ),
            audit_token=audit_token,
            outage_tracker=outage_tracker,
        )
        futures[f] = idx
    completed = 0
    worker_errors: list[Dict[str, Any]] = []
    timed_out_scene_indexes: set[int] = set()
    progress_log_every = max(1, min(10, n // 5 or 1))
    pending = set(futures)
    # Ground-truth activity for the preparation-stall detector: a scene
    # completing here is real work even when the display pipeline filters
    # the progress report out (a recovery wave restarts its counter).
    # Prep-cache hits already sit in results[]: offer them to the
    # streamer so their verified rows publish without a vision call.
    if _web_streamer is not None:
        for _prep_idx, _prep_result in enumerate(results):
            if _prep_result is not None:
                _web_streamer.offer(_prep_idx, _prep_result)
    record_activity("web")
    try:
        while pending and not scene_cancel_event.is_set():
            if job_cancel is not None and job_cancel.cancelled():
                break
            # Round 10: the drain wall is the stage deadline PLUS the
            # outage extension — a scene sleeping through a backoff must
            # not be abandoned by this loop while it waits for the
            # egress to recover.
            remaining = (
                stage_deadline_at
                + outage_tracker.extension_seconds()
                - time.monotonic()
            )
            if remaining <= 0:
                break
            done, pending = wait(
                pending,
                timeout=min(0.5, remaining),
                return_when=FIRST_COMPLETED,
            )
            for future in done:
                idx = futures[future]
                completed += 1
                try:
                    with _results_lock:
                        results[idx] = future.result()
                    # Stream hook (design §1.1): a scene is complete at
                    # exactly this line — offer it for vision filing
                    # during retrieval. offer() is non-blocking and
                    # infallible; it never raises into the drain loop.
                    if _web_streamer is not None:
                        _web_streamer.offer(idx, results[idx])
                    # Round 11 (job 7d1beaba): publish the resolved scene
                    # NOW, while retrieval is still running — the
                    # coordinator's discovered set (and its discovery
                    # liveness) must see real web rows as they land, not
                    # only after the whole batch returns. Before this,
                    # scenes resolved in the web lane never reached
                    # discovered, so the verdict was taken on an empty set
                    # while downloads were still landing. _publish_scene
                    # dedups per scene and never raises into the drain.
                    if results[idx] is not None and results[idx].get("path"):
                        _publish_scene(idx, results[idx])
                    resolved_so_far = sum(1 for r in results if r is not None and r.get("path"))
                    record_activity("web")
                    if completed <= 3 or completed == n or completed % progress_log_every == 0:
                        _web_log(f"scene progress: {completed}/{n} checked, {resolved_so_far} resolved")
                    if progress_callback:
                        progress_callback(
                            0.30 + 0.50 * completed / max(1, n),
                            f"Web image scene {completed}/{n} checked ({resolved_so_far} resolved)",
                        )
                except Exception as e:
                    worker_errors.append({
                        "scene_index": idx,
                        "error_type": type(e).__name__,
                        "error": str(e)[:300],
                    })
                    _web_log(f"scene {idx + 1} error: {type(e).__name__}: {e}")
    finally:
        # The previous executor context waited for every blocked provider even
        # after the stage deadline. Preserve completed scene candidates and
        # detach unfinished cooperative workers instead of turning 120s into
        # an unbounded wait.
        if pending and wait_until_complete:
            # Preserve partial results on deadline or cancellation. Wait mode
            # is still bounded; a slow scene cannot pin the stage forever.
            timed_out_scene_indexes.update(futures[future] for future in pending)
            scene_cancel_event.set()
            _web_log(
                f"web branch deadline/cancel: retaining {completed}/{n} completed scene(s); "
                f"draining {len(pending)} worker(s)"
            )
            done, pending = wait(
                pending,
                timeout=_bounded_worker_drain_seconds(stage_deadline_seconds),
            )
            for future in done:
                idx = futures[future]
                try:
                    late_result = future.result()
                except Exception:
                    late_result = None
                if late_result and late_result.get("path"):
                    with _results_lock:
                        results[idx] = late_result
                    if _web_streamer is not None:
                        _web_streamer.offer(idx, late_result)
                    # Round 11 (job 7d1beaba): publish late-drained scenes
                    # too — a scene that lands during the deadline drain
                    # is still a real web row for the coordinator.
                    _publish_scene(idx, late_result)
            for future in pending:
                future.cancel()
        elif pending:
            timed_out_scene_indexes.update(futures[future] for future in pending)
            scene_cancel_event.set()
            _web_log(
                f"web-stage retrieval deadline: retaining {completed}/{n} completed scene(s); "
                f"cooperatively draining {len(pending)} unfinished scene(s)"
            )
            drain_seconds = _bounded_worker_drain_seconds(stage_deadline_seconds)
            drain_deadline = time.monotonic() + drain_seconds
            while pending and time.monotonic() < drain_deadline:
                done, pending = wait(
                    pending,
                    timeout=min(0.10, max(0.0, drain_deadline - time.monotonic())),
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    idx = futures[future]
                    completed += 1
                    try:
                        late_result = future.result()
                    except Exception:
                        late_result = None
                    if late_result and late_result.get("path"):
                        timed_out_scene_indexes.discard(idx)
                        with _results_lock:
                            results[idx] = late_result
                        if _web_streamer is not None:
                            _web_streamer.offer(idx, late_result)
                        # Round 11 (job 7d1beaba): publish late-drained
                        # scenes too — see the wait_until_complete drain.
                        _publish_scene(idx, late_result)
            for future in pending:
                future.cancel()
        scene_pool.shutdown(wait=not pending, cancel_futures=True)
        if streaming_visual_pool is not None:
            streaming_visual_pool.shutdown(wait=False, cancel_futures=True)

    # Streamer handover (design §1.7): close() files the final
    # pack-set and joins bounded; the drain below is a strict subset
    # operation over undecided scenes only.
    if _web_streamer is not None:
        _web_streamer.close()

    # Retrieval and visual verification have separate budgets.  Vision
    # lanes: no context is auto-minted here either (see above) — if no
    # lane budget was injected, vision stays unavailable and the
    # local-safety degraded path below preserves real downloaded images.
    if visual_context is not None:
        try:
            visual_client_available = visual_context.get_client() is not None
        except Exception:
            visual_client_available = False
    if visual_context is None:
        _web_log("vision verifier unavailable: no job visual context")
    elif visual_client_available:
        _web_log(
            "vision verifier ready: "
            f"model={getattr(visual_context, 'model', 'configured')}, "
            f"calls={getattr(visual_context, 'calls', 0)}/"
            f"{getattr(visual_context, 'max_calls', 0)}"
        )
    elif defer_context_verification:
        # The V2 coordinator owns the authoritative live vision gate for this
        # path. A resolver-local probe can be unavailable while the shared
        # coordinator is still able to initialize its client; do not label
        # that expected handoff as a failed verifier in the user-facing log.
        _web_log(
            "vision verifier deferred to V2 coordinator"
            + (
                f" (resolver probe: {getattr(visual_context, 'degraded_reason', '')})"
                if getattr(visual_context, "degraded_reason", "")
                else ""
            )
        )
    else:
        _web_log(
            "vision verifier unavailable: "
            f"{getattr(visual_context, 'degraded_reason', '') or 'client_probe_failed'}"
        )
    def _retain_pending_without_visual(result: Optional[Dict], reason: str) -> Optional[Dict]:
        """Keep a locally validated real image when the paid verifier is down.

        ``semantic_visual_pending`` means the provider had no trustworthy
        metadata, not that the downloaded pixels were rejected.  Dropping
        these rows when Qwen was unavailable converted a temporary API outage
        into blank cards for an entire web branch.  The resolver has already
        run local decode/safety checks before setting this flag; repeat only a
        cheap file/dimension check here and mark the asset unverified for the
        audit trail. A missing/corrupt path still fails closed and is eligible
        for the normal YouTube/stock rescue lane.
        """
        if not isinstance(result, dict):
            return None
        # When Qwen is unavailable, missing/terse metadata may be retained as
        # degraded recall, but an explicit identity collision must never be
        # rendered as if it were the requested subject.  With Qwen available,
        # these rows still reach the live pixel verifier before any decision.
        if _has_explicit_visual_identity_conflict(result):
            return None
        path = str(result.get("path") or "").strip()
        if not path or not Path(path).is_file():
            return None
        try:
            dims = _validate_image(Path(path))
        except Exception:
            dims = None
        if not dims:
            return None
        retained = dict(result)
        retained.update({
            "width": int(dims[0]),
            "height": int(dims[1]),
            "semantic_visual_pending": False,
            "visual_gate_status": "unavailable",
            "visual_outcome": "unavailable",
            "visual_reason": reason,
            "visual_unverified": True,
        })
        return retained

    def _retain_for_external_visual_gate(
        result: Optional[Dict], reason: str
    ) -> Optional[Dict]:
        """Keep a screened candidate for the V2 coordinator's final vision pass.

        V2 intentionally disables vision in the provider transport so web and
        YouTube do not consume separate call budgets.  When that transport has
        a pending scene-context/identity row, metadata disagreement must not
        discard it before the coordinator's live image verifier sees pixels.
        Recheck decode and local safety here; preserve the pending marker and
        let the external visual gate make the semantic decision.
        """
        if not isinstance(result, dict):
            return None
        path = str(result.get("path") or "").strip()
        if not path or not Path(path).is_file():
            return None
        try:
            dims = _validate_image(Path(path))
            if not dims:
                return None
            safe, _safety_reason = image_is_safe(
                path,
                title=str(result.get("title") or ""),
                url=str(result.get("url") or ""),
                tags=str(result.get("tags") or ""),
                source=str(result.get("provider") or result.get("domain") or ""),
                description=str(result.get("description") or ""),
                source_url=str(result.get("source_url") or ""),
                page_url=str(result.get("page_url") or ""),
            )
            if not safe:
                evidence = result.get('candidate_evidence') or {}
                expected = next((row.get('visual_requirement') for row in (scene_queries_override or [])
                                 if isinstance(row, Mapping) and isinstance(row.get('visual_requirement'), Mapping)
                                 and row['visual_requirement'].get('requirement_hash') == evidence.get('requirement_hash')), None)
                if not _artifact_safety_may_defer(result, expected, _safety_reason, defer_context_verification):
                    return None
        except Exception:
            return None
        retained = dict(result)
        retained.update({
            "width": int(dims[0]),
            "height": int(dims[1]),
            "semantic_visual_pending": True,
            "visual_gate_status": "pending_external",
            "visual_outcome": "pending_external",
            "visual_reason": reason,
            "visual_unverified": True,
            "visual_verification_required": True,
            "external_visual_gate_required": True,
        })
        evidence = dict(retained.get("candidate_evidence") or {})
        evidence.update({
            "metadata_source": evidence.get("metadata_source") or "external_visual_pending",
            "verification_required": True,
            "external_visual_gate_required": True,
        })
        retained["candidate_evidence"] = evidence
        return retained

    if not visual_client_available:
        # A missing/late Qwen verifier is an availability failure, not a
        # factual rejection. Preserve each already safety-screened real image
        # and let the manifest expose the degraded verification state.
        for _idx, _result in enumerate(results):
            if _result and (
                _result.get("semantic_visual_pending")
                or _result.get("semantic_context_pending")
                or _result.get("nearest_match_pending")
            ):
                if defer_context_verification:
                    retained = _retain_for_external_visual_gate(
                        _result, "awaiting_v2_coordinator_visual_gate"
                    )
                else:
                    retained = _retain_pending_without_visual(
                        _result, "visual_verifier_unavailable_local_safety_passed"
                    )
                results[_idx] = retained
                if retained:
                    _web_log(
                        f"scene {_idx + 1} retained screened image for "
                        f"{'external visual gate' if defer_context_verification else 'local degraded mode'}"
                    )
                else:
                    _web_log(
                        f"scene {_idx + 1} dropped invalid pending candidate: "
                        "visual verifier unavailable"
                    )
    # Vision lanes: the web visual phase is bounded by the WEB LANE's own
    # budget (max(180, 25*N) s, clock starting at first submission), never
    # by a shared grace. VIDZEO_WEB_VISUAL_GRACE_SECONDS and
    # VIDZEO_WEB_VISUAL_WINDOW_FLOOR_SECONDS are deleted: the old grace
    # collapsed onto a 240 s floor that retrieval had already consumed
    # (test 26: 6/13 scenes web_visual_grace_deadline). A direct caller
    # without an injected lane budget honors its configured provider call
    # window. The delivery-budget clamp below still bounds the wait.
    from services.visual_intelligence.lane_budget import lane_budget_seconds as _lane_budget_seconds

    _web_lane_budget = getattr(visual_context, "_lane_budget", None) if visual_context is not None else None
    if _web_lane_budget is not None:
        visual_grace_seconds = float(getattr(_web_lane_budget, "budget_seconds", 0) or 0) or _lane_budget_seconds("web", len(scene_texts))
        _web_log(f"[vision-lane] web visual phase budget: {visual_grace_seconds:.0f}s (lane clock starts at first submission)")
    else:
        call_timeout = getattr(visual_context, "call_timeout_seconds", None)
        try:
            visual_grace_seconds = float(call_timeout)
            if visual_grace_seconds <= 0 or not math.isfinite(visual_grace_seconds):
                raise ValueError("invalid timeout")
        except (TypeError, ValueError):
            visual_grace_seconds = _lane_budget_seconds("web", len(scene_texts))
    if delivery_budget is not None:
        try:
            budget_visual_seconds = delivery_budget.allowance(
                visual_grace_seconds,
                reserve_after_seconds=delivery_budget.render_reserve_seconds,
            )
            if budget_visual_seconds <= 0:
                visual_client_available = False
                visual_grace_seconds = 0.05
                _web_log("delivery budget: optional web vision skipped for render reserve")
            else:
                visual_grace_seconds = min(visual_grace_seconds, budget_visual_seconds)
        except Exception:
            pass
    if not visual_client_available and not defer_context_verification:
        for _idx, _result in enumerate(results):
            if _result and _result.get("semantic_visual_pending"):
                retained = _retain_pending_without_visual(
                    _result, "visual_budget_unavailable_local_safety_passed"
                )
                results[_idx] = retained
                if not retained:
                    _web_log(
                        f"scene {_idx + 1} dropped invalid pending candidate after "
                        "visual budget check"
                    )
    # Retrieval and paid visual verification have independent wall budgets.
    # Starting the visual clock here prevents a fast search from accidentally
    # granting vision the entire unused retrieval allowance.
    visual_deadline_at = time.monotonic() + visual_grace_seconds
    visual_timed_out = False
    visual_call_cap = 0
    visual_tasks_submitted = 0
    visual_tasks_bypassed = 0
    # visually_completed, _published_scenes and _publish_scene are
    # defined before the drain loop (shared with the streamer, §1.6.3).

    if visual_client_available:
        all_visual_tasks = []
        _streamer_decided = _web_streamer.decided_snapshot() if _web_streamer is not None else set()
        for idx, result in enumerate(results):
            if not result:
                continue
            if idx in _streamer_decided:
                continue
            # Low-level scene resolution only returns existing decoded files.
            # Keep direct test/integration doubles out of live vision instead
            # of converting their synthetic path into a false rejection.
            result_path = str(result.get("path") or "").strip()
            if result_path and not Path(result_path).is_file():
                continue
            if result.get("_streaming_vision_passed") or result.get("visual_outcome") == "pass":
                continue
            if _is_prep_cache_hit(result):
                # Already vision-verified when banked at prep time (same
                # synthetic verdict the coordinator rescue stamps): no
                # fresh vision call is spent on this scene at all, and no
                # visual task is filed for it — test 12 filed 21 such
                # re-verifications (13 + 8) and completed 0 of the first
                # 13 inside the grace window.
                result["visual_decision"] = _prep_verified_visual_decision()
                result["visual_outcome"] = "pass"
                result["visual_reason"] = "prep_cache_verified"
                continue
            shortlist = result.get("_vision_shortlist") or [result]
            all_visual_tasks.append((idx, result, shortlist))

        # V2 removed the legacy six-call ceiling. The shared visual context
        # owns the authoritative per-job budget; this branch may enqueue every
        # scene and the context will account for calls and retry reserve.
        try:
            context_remaining = int(getattr(visual_context, "max_calls", 0) or 0) - int(
                getattr(visual_context, "calls", 0) or 0
            )
            proportional_cap = max(8, int(math.ceil(len(all_visual_tasks) * 1.25)))
            configured_visual_cap = context_remaining if context_remaining > 0 else proportional_cap
            visual_call_cap = max(0, min(len(all_visual_tasks), configured_visual_cap))
        except Exception:
            visual_call_cap = len(all_visual_tasks)

        def _visual_risk_key(task):
            idx, result, _shortlist = task
            scene_terms = {
                token for token in re.findall(r"[a-z0-9]+", str(scene_texts[idx] or "").lower())
                if len(token) > 2
            }
            metadata_text = " ".join(str(result.get(key) or "") for key in (
                # ``query_used`` is retrieval intent, not asset evidence.
                # Including it here makes broad provider results look
                # relevant before the visual gate has seen their pixels.
                "title", "description", "tags", "page_url",
            )).lower()
            metadata_terms = set(re.findall(r"[a-z0-9]+", metadata_text))
            overlap = len(scene_terms & metadata_terms) / max(1, len(scene_terms))
            try:
                metadata_score = float(
                    result.get("final_score") or result.get("pre_score") or 0.0
                )
            except Exception:
                metadata_score = 0.0
            # Identity failures and weak scene/metadata agreement get the
            # scarce paid relevance vetoes first.
            return (
                0 if result.get("semantic_visual_pending") else 1,
                0 if result.get("identity_failed") else 1,
                overlap,
                metadata_score,
                idx,
            )

        visual_tasks = sorted(all_visual_tasks, key=_visual_risk_key)[:visual_call_cap]
        selected_visual_indices = {task[0] for task in visual_tasks}
        for idx, result, _shortlist in all_visual_tasks:
            if idx in selected_visual_indices:
                continue
            result["visual_outcome"] = "bypassed"
            result["visual_reason"] = "web_vision_budget_metadata_keep"
            visual_tasks_bypassed += 1
        def _stamp_visual_timeout() -> None:
            """Stamp unfinished scenes when the lane budget expired.

            ``web_visual_grace_deadline`` is kept as the audit label for
            "lane budget expired with verdicts outstanding" (vision
            lanes): a timeout is an availability failure, not a
            factual rejection, so the downloaded winner is kept
            unverified for the coordinator to audit/degrade.
            """
            if not visual_timed_out:
                return
            strict_visual = bool(getattr(visual_context, "visual_required", False))
            _web_log(
                f"web visual grace deadline: retaining {len(visually_completed)}/{len(visual_tasks)} "
                "completed decision(s)"
            )
            for idx, _result, _shortlist in visual_tasks:
                if idx in visually_completed:
                    continue
                if strict_visual:
                    # The image was already decoded, safety-screened, and
                    # ranked by the provider. A vision grace timeout is an
                    # availability failure, not a factual rejection; keep
                    # the downloaded winner and mark it unverified so the
                    # coordinator can audit/degrade it without rendering
                    # a blank card.
                    if _result and _result.get("path"):
                        retained = dict(_result)
                        retained.update({
                            "visual_outcome": "unavailable",
                            "visual_reason": "web_visual_grace_deadline",
                            "visual_unverified": True,
                        })
                        results[idx] = retained
                    else:
                        results[idx] = {
                            "path": "",
                            "width": 0,
                            "height": 0,
                            "url": "",
                            "domain": "visual_gate",
                            "reused_from_scene": False,
                            "visual_rejected": True,
                            "visual_outcome": "unavailable",
                            "visual_reason": "web_visual_grace_deadline",
                        }
                elif results[idx]:
                    results[idx]["visual_outcome"] = "unavailable"
                    results[idx]["visual_reason"] = "web_visual_grace_deadline"

        visual_tasks_submitted = len(visual_tasks)
        _cross_ran = False
        if visual_tasks and _web_lane_budget is not None:
            # Vision lanes: the injected lane budget means this is the
            # adapter's web lane — judge all scenes in cross-scene
            # batches (8 scenes x 1 candidate per request, wave 1 + wave
            # 2) instead of one analyze_images_batch per scene. The lane
            # clock starts at the first submission inside the driver.
            try:
                _lane_client = visual_context.get_client()
            except Exception:
                _lane_client = None
            if _lane_client is not None:
                _cross_ran = True
                _scheduler = getattr(visual_context, "lane_scheduler", None)
                if _scheduler is None:
                    _parent_ctx = getattr(visual_context, "_parent", None)
                    _scheduler = getattr(_parent_ctx, "lane_scheduler", None) if _parent_ctx is not None else None

                # Re-check the streamer decided set immediately before
                # filing (design §8a): a scene decided by the streamer
                # after the task list was built must not be re-filed
                # here. The scheduler's candidate-level dedup is the
                # backstop, not the mechanism.
                if _web_streamer is not None:
                    _decided_now = _web_streamer.decided_snapshot()
                    visual_tasks = [
                        task for task in visual_tasks
                        if task[0] not in _decided_now
                    ]
                _apply_cross_result = _apply_visual_result

                _cross_tasks = [
                    {
                        "scene_index": idx,
                        "scene_text": scene_texts[idx],
                        "subject": scene_active_subjects[idx],
                        "global_topic": title,
                        "query": str(result.get("query_used") or ""),
                        "candidates": shortlist,
                    }
                    for idx, result, shortlist in visual_tasks
                ]
                _cross_outcomes = _verify_web_scenes_cross_scene(
                    _cross_tasks,
                    visual_context=visual_context,
                    client=_lane_client,
                    lane_budget=_web_lane_budget,
                    scheduler=_scheduler,
                    on_scene_result=_apply_cross_result,
                )
                visual_timed_out = bool(_web_lane_budget.expired())
                _stamp_visual_timeout()
        if visual_tasks and not _cross_ran:
            visual_pool = ThreadPoolExecutor(max_workers=min(visual_workers, len(visual_tasks)))
            futures = {
                visual_pool.submit(
                    _rank_deferred_scene_shortlist,
                    scene_index=idx,
                    scene_text=scene_texts[idx],
                    subject=scene_active_subjects[idx],
                    global_topic=title,
                    query=str(result.get("query_used") or ""),
                    candidates=shortlist,
                    visual_context=visual_context,
                ): idx
                for idx, result, shortlist in visual_tasks
            }
            pending = set(futures)
            # Round 14: no-progress watchdog. With an unbounded vision
            # budget (round 12) visual_deadline_at is inf, so this wait
            # is otherwise infinite on a lost verdict — a pool worker
            # dead mid-answer would block the whole visual phase
            # silently. If nothing completes for VIDZEO_VISION_STALL_SECONDS
            # (default 300 s), cancel the stuck futures and re-submit
            # each once; on a second stall, retain the already downloaded
            # winner as degraded-unverified (the client_unavailable
            # retention shape) so the scene resolves instead of hanging.
            stall_seconds = _vision_stall_seconds()
            task_args = {
                idx: (
                    scene_texts[idx],
                    scene_active_subjects[idx],
                    str(result.get("query_used") or ""),
                    shortlist,
                )
                for idx, result, shortlist in visual_tasks
            }
            stall_retried: Set[int] = set()
            last_progress_at = time.monotonic()
            try:
                while pending:
                    remaining = visual_deadline_at - time.monotonic()
                    if remaining <= 0:
                        visual_timed_out = True
                        break
                    if stall_seconds > 0 and time.monotonic() - last_progress_at >= stall_seconds:
                        stuck = sorted(futures[f] for f in pending if f in futures)
                        _web_log(
                            f"[vision-stall] no visual verdict for {stall_seconds:.0f}s; "
                            f"stuck scenes: {[i + 1 for i in stuck]} — retrying once"
                        )
                        resubmitted: set = set()
                        for future in list(pending):
                            idx = futures.pop(future, None)
                            future.cancel()
                            if idx is None:
                                continue
                            if idx in stall_retried:
                                if results[idx] and results[idx].get("path"):
                                    retained = dict(results[idx])
                                    retained.update({
                                        "visual_outcome": "unavailable",
                                        "visual_reason": "vision_stall",
                                        "visual_unverified": True,
                                    })
                                    results[idx] = retained
                                    _publish_scene(idx, results[idx])
                                visually_completed.add(idx)
                                continue
                            stall_retried.add(idx)
                            scene_text, subject, query, shortlist = task_args[idx]
                            new_future = visual_pool.submit(
                                _rank_deferred_scene_shortlist,
                                scene_index=idx,
                                scene_text=scene_text,
                                subject=subject,
                                global_topic=title,
                                query=query,
                                candidates=shortlist,
                                visual_context=visual_context,
                            )
                            futures[new_future] = idx
                            resubmitted.add(new_future)
                        pending = resubmitted
                        last_progress_at = time.monotonic()
                        continue
                    done, pending = wait(
                        pending,
                        timeout=min(0.5, remaining),
                        return_when=FIRST_COMPLETED,
                    )
                    if done:
                        last_progress_at = time.monotonic()
                    for future in done:
                        idx = futures[future]
                        visually_completed.add(idx)
                        try:
                            ranked_result = future.result()
                        except Exception as exc:
                            _web_log(f"scene {idx + 1} visual gate error: {type(exc).__name__}")
                            continue
                        # The low-level strict helper fails closed when no
                        # verifier client exists. At the coordinator boundary
                        # that is an availability signal, so retain the
                        # already downloaded/safety-screened winner as a
                        # degraded unverified asset instead of rendering a
                        # blank card. Explicit model irrelevance remains a
                        # hard rejection and never enters this branch.
                        if (
                            ranked_result
                            and ranked_result.get("visual_rejected")
                            and not ranked_result.get("path")
                            and str(ranked_result.get("visual_reason") or "") == "client_unavailable"
                            and results[idx]
                            and results[idx].get("path")
                        ):
                            retained = dict(results[idx])
                            retained.update({
                                "visual_outcome": "unavailable",
                                "visual_reason": "client_unavailable",
                                "visual_unverified": True,
                            })
                            results[idx] = retained
                            continue
                        results[idx] = ranked_result or {
                            "path": "",
                            "width": 0,
                            "height": 0,
                            "url": "",
                            "domain": "visual_gate",
                            "reused_from_scene": False,
                            "visual_rejected": True,
                        }
                        _publish_scene(idx, results[idx])
            finally:
                if pending:
                    visual_timed_out = True
                    for future in pending:
                        future.cancel()
                visual_pool.shutdown(wait=False, cancel_futures=True)

            _stamp_visual_timeout()

    if callable(on_scene_result):
        for _idx, _result in enumerate(results):
            if _result and _result.get("visual_outcome") == "pass":
                _publish_scene(_idx, _result)

    # ── Bounded visual-refetch pass ──
    # Scenes the gate rejected get a second chance with corrected queries
    # instead of inheriting a neighbor scene's image. Fully-rejected scenes
    # stay empty (placeholder) — never filled with an unvalidated neighbor.
    if visual_client_available and time.monotonic() < visual_deadline_at:
        try:
            default_retries = "1" if bool(getattr(visual_context, "visual_required", False)) else "0"
            retry_passes = max(
                0,
                min(
                    3,
                    int(os.environ.get("VIDZEO_WEB_VISUAL_RETRIES", default_retries) or 0),
                ),
            )
        except Exception:
            retry_passes = 1
        rejected_scenes = [
            idx
            for idx, result in enumerate(results)
            if result and result.get("visual_rejected")
        ]
        for _pass in range(retry_passes):
            if time.monotonic() >= visual_deadline_at:
                _web_log("web-stage deadline reached before visual refetch")
                break
            still_rejected = [
                idx
                for idx in rejected_scenes
                if not (results[idx] and results[idx].get("path"))
            ]
            if not still_rejected:
                break
            _web_log(f"visual refetch pass {_pass + 1}/{retry_passes} for {len(still_rejected)} rejected scenes")
            # Do not use an executor context manager here.  A context manager
            # calls shutdown(wait=True), which waits for every cancelled
            # provider/download worker and defeats the visual deadline on
            # packaged Windows builds.  Detach unfinished retries exactly as
            # the primary visual pool does below.
            retry_pool = ThreadPoolExecutor(
                max_workers=min(4, len(still_rejected)),
                thread_name_prefix="web-visual-retry",
            )
            futures = {
                retry_pool.submit(
                    _visual_retry_scene,
                    idx=idx,
                    scene_text=scene_texts[idx],
                    subject=scene_active_subjects[idx],
                    global_topic=title,
                    original_queries=queries_per_scene[idx],
                    gdl=gdl,
                    output_dir=output_dir,
                    global_phashes=global_phashes,
                    global_md5s=global_md5s,
                    used_files=used_files,
                    phash_lock=phash_lock,
                    md5_lock=md5_lock,
                    file_lock=file_lock,
                    visual_context=visual_context,
                    fast_mode=fast_mode,
                    max_candidate_downloads=max_candidate_downloads,
                    api_worker_limit=api_worker_limit,
                    source_telemetry=source_telemetry,
                    deadline_at=visual_deadline_at,
                    discovery_cache=discovery_cache,
                    defer_context_verification=defer_context_verification,
                    exhaustive_fallback=exhaustive_fallback,
                    outage_tracker=outage_tracker,
                ): idx
                for idx in still_rejected
            }
            retry_pending = set(futures)
            try:
                while retry_pending and time.monotonic() < visual_deadline_at:
                    remaining = max(0.0, visual_deadline_at - time.monotonic())
                    done, retry_pending = wait(
                        retry_pending,
                        timeout=min(0.25, remaining),
                        return_when=FIRST_COMPLETED,
                    )
                    for future in done:
                        idx = futures[future]
                        try:
                            retried = future.result()
                        except Exception as exc:
                            _web_log(f"scene {idx + 1} visual refetch error: {type(exc).__name__}")
                            continue
                        if retried and retried.get("path"):
                            results[idx] = retried
                        elif retried:
                            results[idx] = retried
                        else:
                            results[idx] = {
                                "path": "",
                                "width": 0,
                                "height": 0,
                                "url": "",
                                "domain": "visual_gate",
                                "reused_from_scene": False,
                                "visual_rejected": True,
                            }
            finally:
                for future in retry_pending:
                    future.cancel()
                retry_pool.shutdown(wait=False, cancel_futures=True)

    # ── Fallback: nearest-resolved-image fill for unresolved scenes ──
    # Deterministic local recovery is an explicit legacy policy. It must never
    # turn one successful download into apparent coverage for an entire strict
    # job: the coordinator needs an empty lane so it can try YouTube, alternate
    # timestamps, or a cross-source candidate. When the policy is explicitly
    # enabled, it is still bounded and requires at least two distinct assets.
    allow_deterministic_reuse = str(
        os.environ.get("VIDZEO_WEB_ALLOW_DETERMINISTIC_REUSE", "1") or "1"
    ).strip().lower() in {"1", "true", "yes", "on"}
    try:
        configured_reuse = os.environ.get(
            "VIDZEO_WEB_MAX_REUSE_PER_ASSET",
            os.environ.get("VIDZEO_WEB_IMAGE_REUSE_CAP", "1"),
        )
        max_reuse_per_asset = max(1, int(configured_reuse or "1"))
    except Exception:
        max_reuse_per_asset = 1
    def _recovery_terms(value: str) -> Set[str]:
        ignored = STOPWORDS | _WEB_GENERIC_STOP | _WEB_CONTAINER_TERMS
        return {
            token for token in re.findall(r"[a-z0-9]+", str(value or "").lower())
            if len(token) >= 3 and token not in ignored
        }

    def _same_recovery_topic(target_index: int, candidate_index: int) -> bool:
        target = _recovery_terms(scene_active_subjects[target_index]) | _recovery_terms(scene_texts[target_index])
        candidate = _recovery_terms(scene_active_subjects[candidate_index]) | _recovery_terms(scene_texts[candidate_index])
        if target & candidate:
            return True
        anchor = _recovery_terms(main_subject) | _recovery_terms(title)
        return bool(anchor and target & anchor and candidate & anchor)

    recovery_pool = [
        (candidate_index, candidate)
        for candidate_index, candidate in enumerate(results)
        if candidate and candidate.get("path") and Path(str(candidate.get("path"))).is_file()
    ]

    def _content_identity(row: Optional[Mapping[str, Any]]) -> str:
        """Return a byte identity independent of a scene-folder copy.

        The resolver copies a selected image into ``scene_N`` directories.
        Comparing only those destination paths lets the same web image appear
        in adjacent scenes.  Use the downloaded bytes for recovery decisions,
        with the existing perceptual hash as a deterministic fallback.
        """
        if not isinstance(row, Mapping):
            return ""
        path = str(row.get("path") or "").strip()
        if not path:
            return ""
        digest = row.get("md5") or _compute_md5(path)
        if digest:
            return f"md5:{digest}"
        phash = row.get("phash")
        if phash is not None:
            return f"phash:{phash}"
        return ""

    unique_recovery_content = {
        identity
        for _, row in recovery_pool
        if (identity := _content_identity(row))
    }
    reuse_policy_enabled = bool(
        allow_deterministic_reuse and len(unique_recovery_content) >= 2
    )
    if allow_deterministic_reuse and not reuse_policy_enabled:
        _web_log(
            "deterministic recovery skipped: fewer than two distinct verified "
            "web assets are available"
        )

    deterministic_recoveries = 0
    usage_count: Dict[str, int] = {}
    for _candidate_index, _candidate in recovery_pool:
        cid = _content_identity(_candidate) or _candidate.get("path")
        usage_count[cid] = usage_count.get(cid, 0) + 1

    for i in range(n):
        if not reuse_policy_enabled or bool(results[i] and results[i].get("path")) or not recovery_pool:
            continue
        compatible = [
            pair for pair in recovery_pool
            if _same_recovery_topic(i, pair[0])
            and usage_count.get(_content_identity(pair[1]) or pair[1]["path"], 0) < max_reuse_per_asset
        ]
        if not compatible:
            continue
        seed = f"{scene_active_subjects[i]}\n{scene_texts[i]}".encode("utf-8", "replace")
        offset = int(hashlib.sha256(seed).hexdigest()[:8], 16)
        compatible.sort(
            key=lambda pair: (
                usage_count.get(_content_identity(pair[1]) or pair[1]["path"], 0),
                abs(pair[0] - i),
                pair[0],
            )
        )
        # Check against a wider window (5 previous scenes + following 2 scenes)
        recent_scene_indices = [
            neighbor for neighbor in range(max(0, i - 5), min(n, i + 3))
            if neighbor != i and 0 <= neighbor < n and results[neighbor]
        ]
        recent_ids = {
            _content_identity(results[neighbor])
            for neighbor in recent_scene_indices
            if _content_identity(results[neighbor])
        }
        fresh_compatible = [
            pair for pair in compatible
            if (_content_identity(pair[1]) or pair[1]["path"]) not in recent_ids
        ]
        # If every compatible asset is in the recent window, keep this scene
        # unresolved so the outer coordinator can ask another source. Reusing
        # the prior scene here creates the back-to-back duplicates users see.
        if not fresh_compatible:
            continue
        candidate_index, best = fresh_compatible[offset % len(fresh_compatible)]
        source_path = Path(str(best["path"]))
        destination = output_dir / f"scene_{i + 1}" / source_path.name
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)
            recovered = dict(best)
            recovered.update({
                "path": str(destination),
                "reused_from_scene": True,
                "recovery_kind": "deterministic_source_broker_reuse",
                "fallback_reason": "provider_outage_or_query_exhaustion",
                "reused_scene_number": candidate_index + 1,
            })
            results[i] = recovered
            cid = _content_identity(best) or best["path"]
            usage_count[cid] = usage_count.get(cid, 0) + 1
            deterministic_recoveries += 1
            _web_log(
                f"scene {i + 1} deterministic recovery: reused verified scene "
                f"{candidate_index + 1} media ({best.get('provider') or best.get('domain') or 'web'})"
            )
        except Exception as exc:
            _web_log(f"scene {i + 1} deterministic recovery failed: {type(exc).__name__}")

    resolved_images = [r for r in results if r is not None and r.get("path")]
    distinct_active_subjects = {
        str(subject or "").strip().casefold()
        for subject in scene_active_subjects
        if str(subject or "").strip()
    }
    multi_topic_fallback = len(distinct_active_subjects) > 1
    # Build content-identity usage count seeded by all currently resolved images
    usage_count: Dict[str, int] = {}
    for r in resolved_images:
        cid = _content_identity(r) or r["path"]
        usage_count[cid] = usage_count.get(cid, 0) + 1

    for i in range(n):
        if reuse_policy_enabled and (results[i] is None or not results[i].get("path")) and resolved_images:
            resolved_entries = [(idx, r) for idx, r in enumerate(results) if r is not None and r.get("path")]
            recent_scene_indices = [
                neighbor for neighbor in range(max(0, i - 5), min(n, i + 3))
                if neighbor != i and 0 <= neighbor < n and results[neighbor] and results[neighbor].get("path")
            ]
            forbidden_paths = {results[idx]["path"] for idx in recent_scene_indices}
            forbidden_content = {
                _content_identity(results[neighbor])
                for neighbor in recent_scene_indices
            }
            forbidden_content.discard("")

            # Enforce subject consistency for biographical proper-noun subjects
            allowed = []
            target_subject = str(scene_active_subjects[i] or "").strip().lower()
            if multi_topic_fallback and not target_subject:
                allowed = []
            for candidate_idx, r in resolved_entries:
                if multi_topic_fallback and not target_subject:
                    continue
                if multi_topic_fallback and target_subject:
                    cand_subj = str(scene_active_subjects[candidate_idx] or "").strip().lower()
                    if cand_subj != target_subject:
                        continue
                if r["path"] in forbidden_paths:
                    continue
                cid = _content_identity(r) or r["path"]
                if cid in forbidden_content:
                    continue
                if usage_count.get(cid, 0) >= max_reuse_per_asset:
                    continue
                cand_subj = str(scene_active_subjects[candidate_idx] or "").strip().lower()
                if cand_subj != target_subject:
                    is_target_person = _is_biographical_person(scene_active_subjects[i])
                    is_cand_person = _is_biographical_person(scene_active_subjects[candidate_idx])
                    if is_target_person or is_cand_person:
                        continue
                allowed.append((candidate_idx, r))

            def _sort_key(pair):
                candidate_idx, r = pair
                cid = _content_identity(r) or r["path"]
                return (usage_count.get(cid, 0), abs(candidate_idx - i))

            allowed.sort(key=_sort_key)
            if not allowed:
                is_target_person = _is_biographical_person(scene_active_subjects[i])
                if multi_topic_fallback or is_target_person:
                    results[i] = {"path": "", "width": 0, "height": 0, "url": "",
                                 "domain": "placeholder", "reused_from_scene": False}
                    continue
                else:
                    # Fallback: allow any resolved image that satisfies the reuse cap and is not in recent window
                    allowed = sorted(
                        [
                            (idx, r) for idx, r in resolved_entries
                            if r["path"] not in forbidden_paths
                            and (_content_identity(r) or r["path"]) not in forbidden_content
                            and usage_count.get(_content_identity(r) or r["path"], 0) < max_reuse_per_asset
                        ],
                        key=_sort_key,
                    )
                    if not allowed:
                        # Do not fill an exhausted recent window with a
                        # duplicate. The caller records the unresolved slot and
                        # can run a fresh provider query or grounded fallback.
                        allowed = []
            if not allowed:
                results[i] = {"path": "", "width": 0, "height": 0, "url": "",
                              "domain": "placeholder", "reused_from_scene": False}
                continue
            best_idx, best = allowed[0]
            best_cid = _content_identity(best) or best["path"]
            usage_count[best_cid] = usage_count.get(best_cid, 0) + 1
            try:
                src = Path(best["path"])
                dst = output_dir / f"scene_{i + 1}" / src.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                results[i] = dict(best, path=str(dst), reused_from_scene=True, reused_scene_number=best_idx + 1)
            except Exception:
                results[i] = {"path": "", "width": 0, "height": 0, "url": "",
                             "domain": "placeholder", "reused_from_scene": False}
        elif results[i] is None:
            results[i] = {"path": "", "width": 0, "height": 0, "url": "",
                         "domain": "placeholder", "reused_from_scene": False}

    worker_errors_by_scene = {
        int(row.get("scene_index", -1)): str(row.get("error_type") or "worker_error")
        for row in worker_errors
        if isinstance(row, Mapping)
    }
    unresolved_counts = {"retrieval_deadline": 0, "worker_error": 0, "no_candidate_found": 0}
    for scene_index, row in enumerate(results):
        if row is None:
            row = {
                "path": "",
                "width": 0,
                "height": 0,
                "url": "",
                "domain": "placeholder",
                "reused_from_scene": False,
            }
            results[scene_index] = row
        if row.get("path"):
            continue
        if scene_index in timed_out_scene_indexes:
            reason = "retrieval_deadline"
        elif scene_index in worker_errors_by_scene:
            reason = f"worker_error:{worker_errors_by_scene[scene_index]}"
        else:
            reason = "no_candidate_found"
        if not row.get("fallback_reason"):
            row["fallback_reason"] = reason
        if not row.get("retrieval_status"):
            row["retrieval_status"] = (
                "timed_out" if reason == "retrieval_deadline" else "unresolved"
            )
        count_key = reason.split(":", 1)[0]
        if count_key in unresolved_counts:
            unresolved_counts[count_key] += 1
    if any(unresolved_counts.values()):
        _web_log(
            "scene resolution audit: "
            f"deadline={unresolved_counts['retrieval_deadline']}, "
            f"worker_error={unresolved_counts['worker_error']}, "
            f"no_candidate={unresolved_counts['no_candidate_found']}"
        )

    resolved = sum(1 for r in results if r and r.get("path"))
    image_paths = [r["path"] for r in results]

    # ── Audit ──
    import datetime as _dt
    source_broker_audit = source_telemetry.snapshot() if source_telemetry is not None else {}
    if discovery_cache is not None:
        source_broker_audit["provider_request_budget"] = discovery_cache.request_budget_snapshot()
    audit = {
        "job_id": title.replace(" ", "_").replace("/", "_")[:40],
        "timestamp": _dt.datetime.now().isoformat(),
        "title": title,
        "scene_count": n,
        "resolved": resolved,
        "scenes": [],
        "freshness_mode": "streaming-live-search",
        "exhaustive_fallback": bool(exhaustive_fallback),
        "gallery_enabled": bool(gdl),
        "elapsed_seconds": round(time.monotonic() - stage_started_at, 2),
        "stage_deadline_seconds": stage_deadline_seconds,
        "deadline_reached": time.monotonic()
        >= stage_deadline_at + outage_tracker.extension_seconds(),
        # Round 10: egress-outage circuit state — how many outage
        # episodes fired, how much backoff time was granted, and how
        # much of it was added to the stage deadline.
        "outage": outage_tracker.snapshot(),
        "worker_errors": worker_errors[:32],
        "worker_error_count": len(worker_errors),
        "visual_budget": {
            "call_cap": visual_call_cap,
            "submitted": visual_tasks_submitted,
            "completed": len(visually_completed),
            "bypassed": visual_tasks_bypassed,
            "timed_out": visual_timed_out,
            "visual_grace_seconds": round(float(visual_grace_seconds), 2),
            "client_available": bool(visual_client_available),
            "context_degraded_reason": str(
                getattr(visual_context, "degraded_reason", "") or ""
            ),
            "context_calls": int(getattr(visual_context, "calls", 0) or 0),
            "context_max_calls": int(getattr(visual_context, "max_calls", 0) or 0),
        },
        "candidate_pool_policy": {
            "same_query_candidates": _same_query_candidate_limit(max_candidate_downloads, 5.0),
            "max_candidate_downloads": max_candidate_downloads,
            "vision_shortlist": _vision_shortlist_limit(),
            "accepted_only_after_visual_gate": bool(visual_client_available),
        },
        "shared_batch": shared_batch,
        # The full map is persisted even when optional providers are disabled,
        # making the fallback decision inspectable instead of looking like a
        # silent single-source failure in Mixed Media.
        "source_catalog": web_image_source_catalog(),
        "fallback_layers": web_image_fallback_layers(),
        "enabled_sources": [name for name, _kind in _enabled_fast_sources()],
        "source_broker": source_broker_audit,
        "deterministic_recoveries": deterministic_recoveries,
        "reuse_policy": {
            "requested": allow_deterministic_reuse,
            "enabled": reuse_policy_enabled,
            "max_reuse_per_asset": max_reuse_per_asset,
            "distinct_verified_assets": len(unique_recovery_content),
        },
    }
    image_metadata = []

    def _serialize_alternates(row: Dict) -> List[Dict]:
        """Expose downloaded, gated alternates to the V2 allocator.

        The resolver used to keep its shortlist private. When the coordinator's
        final visual gate rejected the single winner, it had no other web asset
        to try and fell back to reusing a neighbouring still. Keep the
        alternate evidence local to this job and pass only existing files with
        their source metadata; the adapter snapshots them before rendering.

        An alternate is never the winner under another filename: the
        shortlist is built from the same downloaded pool, so a second
        copy of the winner's bytes (same fingerprint, same MD5, same
        canonical URL) is dropped here. Proof job a57cb5c7 scene 6
        listed the key art (sha256 63a2158a…) as both winner AND
        alternate, so its "pool of 1" was really a pool of 1 counted
        twice — a duplicate that then looked like a distinct candidate
        to the allocator.
        """
        primary_path = str(row.get("path") or "")
        primary_identity = _distinct_content_identity(row)
        shortlist = row.get("_vision_shortlist") or row.get("alternates") or []
        if not isinstance(shortlist, (list, tuple)):
            return []
        seen = set()
        if primary_identity:
            seen.add(primary_identity)
        output: List[Dict] = []
        for candidate in shortlist:
            if not isinstance(candidate, dict):
                continue
            path = str(candidate.get("path") or "").strip()
            if not path or path == primary_path or not Path(path).is_file():
                continue
            identity = _distinct_content_identity(candidate)
            if not identity:
                source_url = str(
                    candidate.get("source_url") or candidate.get("page_url")
                    or candidate.get("url") or ""
                ).strip()
                fingerprint = str(
                    candidate.get("asset_fingerprint") or candidate.get("md5") or ""
                ).strip().casefold()
                identity = f"fingerprint:{fingerprint}" if fingerprint else (
                    f"url:{source_url.casefold()}" if source_url else f"path:{path.casefold()}"
                )
            if identity in seen:
                continue
            seen.add(identity)
            output.append({
                key: candidate.get(key, "")
                for key in (
                    "path", "width", "height", "url", "domain", "provider",
                    "source", "title", "source_url", "page_url", "preview_url",
                    "license", "license_url", "attribution", "creator", "tags",
                    "description", "query_used", "relevance", "quality_score",
                    "final_score", "identity_failed", "asset_fingerprint", "md5",
                    "semantic_visual_pending", "discovery_method",
                    "semantic_context_pending", "candidate_evidence",
                )
                if candidate.get(key, "") not in (None, "")
            })
            if len(output) >= 8:
                break
        return output

    # Deferred Wikimedia rights enrichment (muse-search design §2.2):
    # one batched call per job over the selected wikimedia rows (winner
    # + alternates, top 6 per scene) so the manifest carries license /
    # attribution. The hot path never spends a second API call per query
    # on rights metadata the vision gate never reads; this runs once,
    # after vision, and never raises.
    try:
        from .muse_search import enrich_validated_rows as _enrich_validated_rows
        _enrich_rows: List[Dict] = []
        for _r in (results or []):
            if not isinstance(_r, dict):
                continue
            _shortlist = [_r] + list(_r.get("_vision_shortlist") or [])
            _wiki = [
                row for row in _shortlist
                if isinstance(row, dict)
                and str(row.get("provider") or "").lower() == "wikimedia"
                and str(row.get("page_id") or "")
            ]
            _enrich_rows.extend(_wiki[:6])
        if _enrich_rows:
            _enrich_validated_rows(_enrich_rows, limit=max(6, len(_enrich_rows)))
    except Exception:
        pass

    for i, r in enumerate(results):
        alternates = _serialize_alternates(r)
        entry = {
            "scene_number": i + 1,
            "queries": queries_per_scene[i],
            "resolved": bool(r.get("path")),
            "path": r.get("path", ""),
            "width": r.get("width", 0),
            "height": r.get("height", 0),
            "domain": r.get("domain", ""),
            "reused": r.get("reused_from_scene", False),
            "active_subject": scene_active_subjects[i] if i < len(scene_active_subjects) else "",
            "visual_outcome": r.get("visual_outcome", ""),
            "visual_reason": r.get("visual_reason", ""),
            "recovery_kind": r.get("recovery_kind", ""),
            # Which inline layer resolved this scene (perfect = the existing
            # first pass; general_related / cached_prep / cached_download =
            # the inline fallback chain in _resolve_scene).  A scene should
            # almost never fall through to a graphic while a related or
            # cached image exists — this field proves which layer fired.
            "web_fallback_layer": (
                r.get("web_fallback_layer")
                or ("cached_prep" if r.get("prep_cache_hit") else ("perfect" if r.get("path") else ""))
            ),
            "fallback_reason": r.get("fallback_reason", ""),
            # Older/custom resolvers may return only a verified path.  Count
            # that winner as a one-item pool instead of reporting a misleading
            # zero-candidate audit (which made healthy media look like a blank
            # fallback in the UI).
            "candidate_pool_size": max(
                1 if r.get("path") else 0,
                int(r.get("candidate_pool_size") or 0),
            ),
            "downloaded_candidate_count": r.get("downloaded_candidate_count", 0),
            "alternates": alternates,
            # Per-engine universal trail (GTA fix, Nov 16): engine, count,
            # duration, error for every universal batch this scene ran, so
            # the next failure is diagnosable from this artifact, not a
            # log grep. web_wave_stalled flags a wave that gave up after a
            # full liveness window with zero progress.
            "universal_engine_audits": _merged_scene_universal_audits(audit_token, i, r),
            "web_wave_stalled": bool(
                r.get("web_wave_stalled") or web_wave_stalled_for_scene(i + 1)
            ),
        }
        if r.get("path"):
            entry.update({
                "query_used": r.get("query_used", ""),
                "title": r.get("title", ""),
                "source_url": r.get("source_url", ""),
                "page_url": r.get("page_url", ""),
                "preview_url": r.get("preview_url", ""),
                "provider": r.get("provider", ""),
                "license": r.get("license", ""),
                "license_url": r.get("license_url", ""),
                "attribution": r.get("attribution", ""),
                "creator": r.get("creator", ""),
                "tags": r.get("tags", ""),
                "description": r.get("description", ""),
                "pre_score": r.get("relevance", 0),
                "quality_score": r.get("quality_score", 0),
                "final_score": r.get("final_score", 0),
                "identity_failed": r.get("identity_failed", False),
                "semantic_context_pending": r.get("semantic_context_pending", False),
                "candidate_evidence": r.get("candidate_evidence") or {},
                "visual_decision": r.get("visual_decision"),
                "alternates": alternates,
            })
        image_metadata.append({
            key: r.get(key, "")
            for key in (
                "title", "url", "source_url", "page_url", "preview_url",
                "provider", "domain", "tags", "description", "source",
                "license", "license_url", "attribution", "creator",
                "visual_outcome", "visual_reason",
                "alternates",
            )
            if r.get(key, "")
        })
        audit["scenes"].append(entry)

    # Drop this call's per-scene audit registry entries now that the
    # manifest carries them; the registry is per-call by token.
    with _SCENE_UNIVERSAL_AUDITS_LOCK:
        _SCENE_UNIVERSAL_AUDITS.pop(audit_token, None)

    manifests_dir = TEMP_DIR / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    # A global ``web_image_audit.json`` made concurrent queue jobs overwrite
    # each other's evidence.  The resolver returned that path to the caller,
    # so a later job could make a successful run look empty (or attach the
    # wrong provider rows to a failure).  Persist an immutable per-run audit
    # and keep the legacy filename only as a best-effort compatibility copy.
    audit_namespace = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(output_namespace or "").strip()).strip("_")
    audit_namespace = audit_namespace[:80] or uuid.uuid4().hex[:12]
    audit_path = manifests_dir / f"web_image_audit_{audit_namespace}_{uuid.uuid4().hex[:8]}.json"
    with open(audit_path, "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2, ensure_ascii=False)
    try:
        # Preserve the old operator convenience path without making it
        # authoritative.  Atomic replace prevents readers from seeing a
        # partially-written JSON document when another job finishes.
        legacy_path = manifests_dir / "web_image_audit.json"
        legacy_tmp = manifests_dir / f".web_image_audit.{uuid.uuid4().hex}.tmp"
        with open(legacy_tmp, "w", encoding="utf-8") as f:
            json.dump(audit, f, indent=2, ensure_ascii=False)
        os.replace(legacy_tmp, legacy_path)
    except Exception:
        pass

    try:
        job_id = audit["job_id"][:40]
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        hist_dir = manifests_dir.parent / "web_image_audits"
        hist_dir.mkdir(parents=True, exist_ok=True)
        hist_path = hist_dir / f"{ts}_{job_id}_web_image_audit.json"
        with open(hist_path, "w", encoding="utf-8") as f:
            json.dump(audit, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

    # Observability (§4.4): whether the slow-network adaptation fired,
    # from the scraper's existing progress_snapshot — no new counter.
    try:
        _slow_snapshot = _UNIVERSAL_SCRAPER.progress_snapshot()
        _slow_streak = int(_slow_snapshot.get("slow_network_streak") or 0)
        _slow_factor = float(_slow_snapshot.get("slow_network_factor") or 1.0)
    except Exception:
        _slow_streak, _slow_factor = 0, 1.0
    _web_log(
        f"[web] slow-network adaptation: streak={_slow_streak} factor={_slow_factor:.2f}x"
    )
    audit["visual_budget"]["slow_network_streak"] = _slow_streak
    audit["visual_budget"]["slow_network_factor"] = round(_slow_factor, 2)

    _web_log(f"complete: {resolved}/{n} scenes resolved")
    _web_log(f"audit saved: {audit_path}")
    _web_log("=== WEB IMAGE PIPELINE END ===")

    _ACTIVE_PROVIDER_HEALTH = _prev_provider_health

    return {
        "image_paths": image_paths,
        "scene_queries": [{"scene_number": i + 1, "queries": queries_per_scene[i]}
                         for i in range(n)],
        "audit_path": str(audit_path),
        "audit": audit,
        "image_metadata": image_metadata,
    }


# =============================================================================
#  HELPER FUNCTIONS
# =============================================================================

def _has_bad_terms_relaxed(query: str) -> bool:
    """Check if a query is dominated by abstract/non-visual terms."""
    words = set(query.lower().split())
    core = words - {"the", "a", "an", "in", "on", "at", "of", "and", "or", "for", "with"}
    if not core:
        return False
    bad_count = len(core & BAD_QUERY_TERMS)
    return bad_count >= len(core) * 0.7


def _is_cricket_content(subject: str, scene_text: str) -> bool:
    """Detect if the subject or scene text is about cricket."""
    combined = f"{subject} {scene_text}".lower()
    if re.search(r"\b(fifa|football|soccer|fanbase|supporters|world cup ticket|world cup fans|2026 world cup|iranian football)\b", combined):
        return False
    cricket_keywords = {
        "cricket", "cricketer", "batsman", "bowler", "fielder", "wicket",
        "stumps", "crease", "innings", "batting", "bowling", "fielding",
        "century", "boundary",
        "bat", "ball", "pitch", "test match", "t20", "odi", "ipl",
        "cover drive", "pull shot", "straight drive", "square cut",
        "leg glance", "on drive", "sweep", "reverse sweep",
        "kohli", "sachin", "dhoni", "rohit", "sharma", "bumrah",
        "ashwin", "jadeja", "gill", "iyer", "rahul", "pant",
        "dravid", "laxman", "sehwag", "gambhir", "ganguly", "yuvraj",
        "harbhajan", "zaheer", "kumble", "shastri", "kapil",
    }
    return any(re.search(rf"\b{re.escape(kw)}\b", combined) for kw in cricket_keywords)


def _pick_scene_enrichments(subject: str, scene_text: str, scene_idx: int) -> List[str]:
    """Pick 2-3 visual enrichment words for a scene's queries.

    Extracts scene-specific nouns from scene text (e.g. "cover drive", "pull shot")
    and falls back to generic enrichments from VISUAL_ENRICHMENTS.
    When cricket content is detected, cross-sport terms are filtered out.
    """
    from .constants import VISUAL_ENRICHMENTS, NON_CRICKET_SPORTS_TERMS, CRICKET_ENRICHMENTS
    from .query import _classify_scene_type

    picked = []

    is_cricket = _is_cricket_content(subject, scene_text)
    is_football_world_cup = bool(re.search(
        r"\b(fifa|football|soccer|world cup|premier league|champions league)\b",
        f"{subject} {scene_text}".lower(),
    )) and not is_cricket
    scene_type = _classify_scene_type(scene_text, subject)

    # Use cricket-specific pool when cricket detected, otherwise generic sports
    if is_football_world_cup:
        enrichments_pool = [
            "fans stadium", "supporters flags", "empty seats",
            "football crowd", "press conference", "stadium exterior",
        ]
    elif scene_type == "gaming":
        enrichments_pool = [
            "gameplay screenshot", "official key art", "trailer still",
            "in-game environment", "character artwork", "official reveal",
        ]
    elif is_cricket and scene_type == "sports":
        enrichments_pool = list(CRICKET_ENRICHMENTS)
    else:
        if scene_type == "history":
            enrichments_pool = list(VISUAL_ENRICHMENTS.get("history", []))
        else:
            enrichments_pool = list(VISUAL_ENRICHMENTS.get(scene_type, VISUAL_ENRICHMENTS["person"]))
        # Filter out non-cricket sports terms when subject is a known cricketer
        if is_cricket:
            enrichments_pool = [w for w in enrichments_pool if w.lower() not in NON_CRICKET_SPORTS_TERMS]

    general_pool = list(VISUAL_ENRICHMENTS.get("general", []))

    subj_words = set(subject.lower().split())
    subj_phrases = set()

    if subject:
        subj_phrases.add(subject.lower())
        subj_words_list = subject.lower().split()
        for i in range(len(subj_words_list) - 1):
            subj_phrases.add(f"{subj_words_list[i]} {subj_words_list[i + 1]}")

    # Extract scene-specific multi-word action phrases (e.g. "cover drive", "pull shot")
    # Use overlapping word pairs to catch all adjacent combinations
    text_lower = scene_text.lower()
    all_words = re.findall(r'[a-z]{3,}', text_lower)
    candidate_phrases = []
    _VISUAL_ACTION_WORDS = {
        "cover", "drive", "pull", "shot", "cut", "sweep", "hook", "glance",
        "bowl", "bowling", "spin", "pace", "throw", "catch", "field",
        "dive", "six", "four", "boundary", "century", "wicket", "stump",
        "hit", "bat", "swing", "smash", "batting",
        "celebrate", "celebration", "fist", "pump", "hug", "wave",
        "practice", "training", "warmup", "stretch",
        "helmet", "pad", "glove", "kit", "uniform", "jersey",
        "trophy", "medal", "award", "ceremony", "handshake",
        "conference", "interview", "press", "speech", "talk",
        "walk", "run", "running", "sprint", "jump", "leap",
        "score", "runs", "over", "innings", "match", "cup", "test",
        "ground", "stadium", "pitch", "field", "net", "session",
        "fans", "supporters", "tickets", "visa", "border", "flags",
        "chants", "crowd", "seats", "empty", "travel", "airport",
    }
    _CONNECTOR_WORDS = {
        "against", "during", "through", "between", "without", "within",
        "about", "above", "below", "behind", "across", "around",
        "after", "before", "since", "until", "while", "because",
        "into", "onto", "upon", "under", "over", "along", "among",
        "with", "from", "that", "this", "these", "those", "which",
        "what", "when", "where", "their", "them", "they", "then",
        "very", "just", "also", "been", "being", "having",
        "made", "make", "makes", "making", "took", "take", "takes",
        "showed", "shows", "show", "showing", "given", "gives",
        "came", "come", "goes", "gone", "went",
    }
    for i in range(len(all_words) - 1):
        w1, w2 = all_words[i], all_words[i + 1]
        if w1 in STOPWORDS or w2 in STOPWORDS:
            continue
        if w1 in BAD_QUERY_TERMS or w2 in BAD_QUERY_TERMS:
            continue
        if w1 not in _VISUAL_ACTION_WORDS and w2 not in _VISUAL_ACTION_WORDS:
            continue
        if w1 not in _VISUAL_ACTION_WORDS and w1 in _CONNECTOR_WORDS:
            continue
        if w2 not in _VISUAL_ACTION_WORDS and w2 in _CONNECTOR_WORDS:
            continue
        phrase = f"{w1} {w2}"
        pl = phrase.lower()
        is_subject_match = any(ps in pl for ps in subj_phrases) or any(s in pl for s in subj_words)
        if is_subject_match:
            continue
        if is_cricket:
            ws = set(pl.split())
            if ws & set(w.lower() for w in NON_CRICKET_SPORTS_TERMS):
                continue
        # Score: +2 if both words are visual, +1 if one is visual
        visual_score = (2 if w1 in _VISUAL_ACTION_WORDS and w2 in _VISUAL_ACTION_WORDS
                       else 1)
        length_bonus = 0.5 if len(w1) >= 5 or len(w2) >= 5 else 0
        candidate_phrases.append((phrase, visual_score + length_bonus))

    # Sort by score (best first) then take top 2, filtering exact phrase duplicates
    candidate_phrases.sort(key=lambda x: x[1], reverse=True)
    seen_lower = set(w.lower() for w in subj_words)
    for action, _ in candidate_phrases:
        al = action.lower()
        if al in [p.lower() for p in picked]:
            continue
        picked.append(action)
        if len(picked) >= 2:
            break

    # Fill remaining slots from generic enrichments pool with rotation
    enrichments_pool = [w for w in enrichments_pool if w.lower() not in subj_words and w.lower() not in seen_lower]
    if enrichments_pool:
        offset = (scene_idx + len(picked)) % max(len(enrichments_pool), 1)
        for i in range(len(enrichments_pool)):
            if len(picked) >= 3:
                break
            candidate = enrichments_pool[(i + offset) % len(enrichments_pool)]
            candidate_lower = candidate.lower()
            if candidate_lower not in seen_lower:
                # Double-check: never use non-cricket sports terms for cricket subjects
                if is_cricket and candidate_lower in NON_CRICKET_SPORTS_TERMS:
                    continue
                picked.append(candidate)
                seen_lower.update(candidate.lower().split())

    # Fallback to general pool
    if len(picked) < 2:
        for g in general_pool:
            gl = g.lower()
            if gl not in subj_words and gl not in seen_lower and g not in picked:
                if is_cricket and gl in NON_CRICKET_SPORTS_TERMS:
                    continue
                picked.append(g)
            if len(picked) >= 3:
                break

    return picked[:3]
