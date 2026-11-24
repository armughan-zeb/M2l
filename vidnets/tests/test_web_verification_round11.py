"""Round 11: web scenes published during retrieval, not after (job 7d1beaba).

Job 7d1beaba (GTA VI 30-sec, 8 scenes) failed at the media gate with 0
verified web / 0 YouTube / 8 graphics even though web search returned
57-66 rows per scene and downloads LANDED (scenes 3/7/6/2 fully, others
partially) — the verdict was taken while images were still downloading
("web late-wait skipped: loader still in retrieval, needing=8 scene(s),
rows=0") and the YouTube lane sat at 0/7 with no retry. Three fixes:

1. The web resolver publishes each scene the moment its download lands
   (drain loop), so the coordinator's ``discovered`` set fills during
   retrieval instead of only after the whole batch returns.
2. The late-web wait gate no longer skips a still-loading lane that can
   publish rows incrementally (``incremental_rows``); the skip survives
   only for batch lanes that cannot return rows mid-retrieval.
3. The YouTube anonymous retry keeps its own budget inside the wall cap
   (the cap used to force first-pass == hard cap, leaving the retry 0s).

T1 proves the consumer half of (1): a lane that publishes a row
mid-retrieval feeds the coordinator's plan before the loader returns.
T2 proves (2): an incremental lane waits, a batch lane still skips.
T3/T4 prove (3) and its diagnostic. T2, T3 and T4 FAIL on clean HEAD
(ef9b8fb); T1 pins the coordinator behaviour the resolver fix relies on.

Run with ``VIDZEO_RENDER_GATE=off``.
"""
import hashlib
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services.media_resolution_coordinator import resolve_storyboard_media_v2
from services.pipeline_v2_media_adapter import LazyBatchLane
from services.visual_intelligence.lane_budget import create_lane_budgets


# ---------------------------------------------------------------------------
# Shared builders (mirror the late-web harness in
# test_media_resolution_coordinator.py)
# ---------------------------------------------------------------------------

def _r11_scene(requirement_hash):
    return {
        "scene_id": "round11-scene",
        "scene_index": 0,
        "container_id": "round11-container",
        "text": "Grand Theft Auto VI official artwork scene",
        "visual_requirement": {"requirement_hash": requirement_hash},
        "source_policy": {"mixed_media": True},
    }


def _r11_row(image_path, sha, requirement_hash):
    """A web row as the resolver publishes it on download completion:
    an accepted verdict with no ``verification_status`` yet (deferred
    verification — the streamer never filed). The coordinator's primary
    path treats it as pending and verifies the pixels itself."""
    verdict = {
        "accepted": True,
        "status": "accepted",
        "visual_checked": True,
        "semantic_verified": True,
        "visual_available": True,
        "relevance": 92.0,
        "final_score": 92.0,
        "scene_index": 0,
        "verified_requirement_hash": "resolver-scene-context-hash",
        "verified_asset_sha256": sha,
    }
    return {
        "path": str(image_path),
        "source": "web",
        "provider": "web",
        "media_type": "image",
        "candidate_id": "web:round11-still",
        "title": "Grand Theft Auto VI official artwork",
        "final_score": 92.0,
        "qwen_result": dict(verdict),
        "visual_decision": dict(verdict),
        "candidate_evidence": {
            "requirement_hash": requirement_hash,
            "identity_proof": "Grand Theft Auto VI",
        },
    }


def _never_called_verifier(_scene, _candidate):
    raise AssertionError("round 11: no candidate exists here, so no live vision call may be spent")


class _AcceptingVerifier:
    """The coordinator's own live vision: a published download row carries
    no streamer verdict yet (deferred verification), so the coordinator
    verifies the pixels itself. Records every call."""

    def __init__(self):
        self.calls = []

    def __call__(self, _scene, _candidate):
        self.calls.append(str(_candidate.get("path")))
        return {"accepted": True, "status": "visual_pass", "final_score": 95.0,
                "visual_checked": True, "visual_available": True}


def _run_coordinator_with_web_lane(web_lane, budgets, deadline_seconds=2, verifier=None):
    def yt_lane(_scene, _cancel):
        return []

    return resolve_storyboard_media_v2(
        [_r11_scene("round11-scene-hash-001")],
        youtube_lane=yt_lane,
        web_lane=web_lane,
        visual_verifier=verifier if verifier is not None else _never_called_verifier,
        neutral_factory=lambda _scene: None,
        deadline_seconds=deadline_seconds,
        verification_deadline_seconds=1.0,
        vision_lane_budgets=budgets,
    )


# ---------------------------------------------------------------------------
# T1 — incremental publication feeds the plan before the loader returns
# ---------------------------------------------------------------------------

class _IncrementalWebLane:
    """A web lane whose loader publishes rows per scene as they complete.

    Mirrors ``LazyBatchLane``'s protocol (``publish_row`` releases a
    completed scene without waiting for the batch) with one scene: the
    row is published ``publish_delay`` seconds in, while the loader
    itself only returns after ``return_delay`` seconds — the 7d1beaba
    shape (downloads landing during retrieval, loader still running
    when the verdict is due).
    """

    shared_batch = True

    def __init__(self, row, publish_delay, return_delay):
        self.incremental_rows = True
        self._row = row
        self._publish_delay = publish_delay
        self._return_delay = return_delay
        self._rows = {}
        self._state = "loading"
        self._condition = threading.Condition()
        threading.Thread(target=self._load, daemon=True).start()

    def publish_row(self, index, row):
        with self._condition:
            self._rows[int(index)] = row
            self._condition.notify_all()

    def _load(self):
        time.sleep(self._publish_delay)
        self.publish_row(0, self._row)
        time.sleep(max(0.0, self._return_delay - self._publish_delay))
        with self._condition:
            self._state = "ready"
            self._condition.notify_all()

    def __call__(self, scene, cancel_event):
        index = int(scene.get("scene_index", 0))
        with self._condition:
            while (
                self._state == "loading"
                and index not in self._rows
                and not cancel_event.is_set()
            ):
                self._condition.wait(timeout=0.1)
            return self._rows.get(index)

    def get_row(self, index, timeout=0.0):
        with self._condition:
            if self._state == "loading" and timeout > 0:
                self._condition.wait_for(
                    lambda: index in self._rows or self._state != "loading",
                    timeout=timeout,
                )
            return self._rows.get(index)


def test_incremental_web_row_joins_plan_before_loader_returns(tmp_path):
    """Round 11 fix 1 (consumer half): a web row published 0.5 s into
    retrieval must be selected even though the loader only returns at
    8 s — the verdict must not wait for the batch. On a lane that
    publishes nothing until the loader returns, the same run ships a
    graphic (the 7d1beaba failure). The published row carries no
    streamer verdict yet (deferred verification), so the coordinator
    verifies the pixels itself with its own vision call."""
    image = tmp_path / "web_still.jpg"
    image.write_bytes(b"round11-web-image-bytes")
    sha = hashlib.sha256(b"round11-web-image-bytes").hexdigest()
    row = _r11_row(image, sha, "round11-scene-hash-001")
    lane = _IncrementalWebLane(row, publish_delay=0.5, return_delay=8.0)
    budgets = create_lane_budgets(1)["budgets"]
    assert budgets["web"].started_at is None  # deferred verification, as in 7d1beaba
    verifier = _AcceptingVerifier()

    started = time.monotonic()
    result = _run_coordinator_with_web_lane(lane, budgets, verifier=verifier)
    elapsed = time.monotonic() - started

    assert result[0].provider == "web"
    assert result[0].selected_path == str(image)
    assert result[0].verification_status == "verified"
    assert verifier.calls, "the published row must be verified by the coordinator's own vision"
    # Bounded: the verdict landed well before the loader's 8 s return.
    assert elapsed < 6.0, f"verdict waited for the loader to return, took {elapsed:.1f}s"


def test_incremental_row_never_published_falls_back(tmp_path, monkeypatch):
    """The same incremental lane with a publish that never comes must
    still fall back to graphic/neutral, bounded — publication is a
    second chance, not an open-ended wait."""
    monkeypatch.setenv("VIDZEO_WEB_LATE_WAIT_SECONDS", "3")
    image = tmp_path / "web_still.jpg"
    image.write_bytes(b"round11-web-image-bytes")
    sha = hashlib.sha256(b"round11-web-image-bytes").hexdigest()
    row = _r11_row(image, sha, "round11-scene-hash-001")
    lane = _IncrementalWebLane(row, publish_delay=3600.0, return_delay=3600.0)
    budgets = create_lane_budgets(1)["budgets"]

    started = time.monotonic()
    result = _run_coordinator_with_web_lane(lane, budgets)
    elapsed = time.monotonic() - started

    assert result[0].provider in {"graphic", "neutral"}
    assert result[0].selected_path != str(image)
    assert elapsed < 15


# ---------------------------------------------------------------------------
# T2 — late-web wait gate: incremental lanes wait, batch lanes still skip
# ---------------------------------------------------------------------------

def test_batch_lane_default_is_not_incremental():
    """A bare LazyBatchLane publishes nothing mid-retrieval: the T6
    skip premise still holds for it."""
    lane = LazyBatchLane(lambda: {})
    assert lane.incremental_rows is False


def test_late_web_wait_runs_for_incremental_lane(monkeypatch, capsys):
    """Round 11 fix 2: a still-loading lane that CAN publish rows
    incrementally must not have its late-web wait skipped, even when
    the web vision clock never started. The wait runs bounded (the
    3 s cap) and then falls back — it is not skipped in under 8 s the
    way the T6 batch lane is."""
    monkeypatch.setenv("VIDZEO_WEB_LATE_WAIT_SECONDS", "3")
    budgets = create_lane_budgets(1)["budgets"]
    assert budgets["web"].started_at is None

    def slow_loader():
        time.sleep(120.0)
        return {}

    lane = LazyBatchLane(slow_loader)
    lane.incremental_rows = True
    started = time.monotonic()
    result = _run_coordinator_with_web_lane(lane, budgets)
    elapsed = time.monotonic() - started
    out = capsys.readouterr().out
    assert "web late-wait skipped" not in out
    assert result[0].provider in {"graphic", "neutral"}
    # The wait ran: ~2 s wall + ~3 s wait, not the < 8 s skip path and
    # not an open-ended hang.
    assert elapsed > 3.5, f"wait was skipped, run took only {elapsed:.1f}s"
    assert elapsed < 15


# ---------------------------------------------------------------------------
# T3/T4 — YouTube anonymous retry keeps its budget inside the wall cap
# ---------------------------------------------------------------------------

def _r11_yt_groups():
    """The 7d1beaba search shape: 3 container pools, 7 scenes, 8 queries
    per pool -> 24 jobs, 6 rounds at 4-way concurrency, rounds floor
    195 s, wall cap 150 s. On clean HEAD the clamp forced
    hard cap == first-pass timeout, so the retry budget was 0 s."""
    rows = [{"queries": [f"query-{pool}-{k}" for k in range(8)]} for pool in range(3)]
    return {
        "pool-a": [dict(rows[0]), dict(rows[0]), dict(rows[0])],
        "pool-b": [dict(rows[1]), dict(rows[1])],
        "pool-c": [dict(rows[2]), dict(rows[2])],
    }


def _r11_yt_env(monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_SEARCH_CONCURRENCY", "8")
    monkeypatch.setenv("VIDZEO_YT_SEARCH_MAX_CONCURRENCY", "4")
    monkeypatch.setenv("VIDZEO_YT_V2_QUERIES_PER_CONTAINER", "8")
    monkeypatch.setenv("VIDZEO_YT_V2_QUERY_TOTAL_CAP", "0")
    monkeypatch.setenv("VIDZEO_YT_SEARCH_ANON_RETRY_SECONDS", "30")


def test_anonymous_retry_runs_under_wall_cap(monkeypatch):
    """Round 11 fix 3: with the first pass floored to the 150 s wall cap,
    the anonymous retry must still run (first pass shrunk to 120 s,
    retry keeps 30 s) instead of being starved to 0 s. Fails on clean
    HEAD: only one search pass runs and the pools come back empty."""
    import services.youtube_pipeline_v2 as yt

    _r11_yt_env(monkeypatch)
    calls = []
    candidate = {"video_id": "retry-recovered", "title": "GTA VI gameplay"}

    def fake_pass(jobs, **kwargs):
        calls.append({"jobs": list(jobs), **kwargs})
        if len(calls) == 1:
            return True, {}
        return False, {"pool-a": [candidate]}

    monkeypatch.setattr(yt, "_run_pool_search_pass", fake_pass)
    pools = yt._search_container_pools(
        _r11_yt_groups(),
        search_timeout_seconds=40,
        session_jar=object(),
        wall_cap_seconds=150,
    )
    assert len(calls) == 2, f"anonymous retry never ran: {len(calls)} pass(es)"
    assert calls[0]["search_timeout"] == pytest.approx(120.0)
    assert calls[1]["session_jar"] is None
    assert calls[1]["search_timeout"] == pytest.approx(30.0)
    assert (
        calls[0]["search_timeout"] + calls[1]["search_timeout"]
        <= 150.0
    ), "search must stay inside the wall cap"
    assert pools == {"pool-a": [candidate]}


def test_anonymous_retry_skip_is_diagnosed(monkeypatch, capsys):
    """Round 11 fix 3 (surfacing): when the first pass was already
    anonymous, no retry runs — and the worker log must now say so
    instead of leaving an empty attempt audit behind a bare timeout."""
    import services.youtube_pipeline_v2 as yt

    _r11_yt_env(monkeypatch)
    calls = []

    def fake_pass(jobs, **kwargs):
        calls.append({"jobs": list(jobs), **kwargs})
        return True, {}

    monkeypatch.setattr(yt, "_run_pool_search_pass", fake_pass)
    pools = yt._search_container_pools(
        _r11_yt_groups(),
        search_timeout_seconds=40,
        session_jar=None,
        wall_cap_seconds=150,
    )
    assert pools == {}
    assert len(calls) == 1
    out = capsys.readouterr().out
    assert "anonymous retry did not run" in out
    assert "session_jar=none" in out
