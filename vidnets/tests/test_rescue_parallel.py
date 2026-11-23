"""Pre-stock web rescue: parallel, budget-scaled, and deadline-bounded.

Root cause (job d1ece590, Nov 22 2026): the pre-stock rescue loop ran
one scene at a time, each scene paying a live vision call (~60-76s) through
the external-agent pool, while the pool's 4 workers sat idle (pending 0).
112 unresolved scenes x ~2 min = ~3.7 hours for a 13-min video. Three
compounding defects:
* the rescue loop is sequential (media_resolution_coordinator.py, the
  ``for idx in unresolved_before_stock`` loop);
* the rescue vision budget is a fixed 300s window / 10-call headroom no
  matter how many scenes are unresolved (_grant_rescue_visual_budget);
* the rescue has no global deadline — it can grind forever instead of
  falling through to the stock/graphic fallback.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services.media_resolution_coordinator import (  # noqa: E402
    _grant_rescue_visual_budget,
    resolve_storyboard_media_v2,
)

CONTAINER = "rescue-parallel-container"


def _write(tmp_path, name, payload):
    path = tmp_path / name
    path.write_bytes(payload)
    return str(path)


def _scene(index, text):
    return {
        "scene_index": index,
        "scene_id": f"rescue-{index}",
        "order": index,
        "container_id": CONTAINER,
        "text": text,
        "positive_subjects": ["Vice City"],
        "reuse_policy": {"scope": "container", "max_uses": 2, "non_adjacent": True},
        "source_policy": {
            "primary_sources": ["youtube", "web_image"],
            "media_source_priority": ["youtube", "web", "stock"],
            "youtube_quota": True,
            "allow_verified_topic_reuse": True,
            "unique_web_assets_required": True,
            "strict_real_media": True,
        },
        "visual_requirement": {
            "canonical_entity": "Vice City",
            "subject_type": "place",
            "domain": "place",
            "requirement_hash": f"req-hash-{index}",
        },
    }


def _slow_accepting_verifier(sleep_seconds, calls):
    def verifier(scene, candidate):
        time.sleep(sleep_seconds)
        calls.append((scene.get("scene_index"), candidate.get("candidate_id")))
        return {
            "accepted": True,
            "status": "accepted",
            "visual_checked": True,
            "semantic_verified": True,
            "visual_available": True,
            "entity_conflict": False,
            "fatal_mismatch": False,
            "final_score": 70.0,
            "identity_score": 90.0,
        }

    return verifier


def test_rescue_runs_scenes_in_parallel(tmp_path):
    """8 rescue scenes with a 1.0s verifier must finish well under the
    sequential wall (17 calls x 1.0s = ~18s). Parallel rescue with 8
    workers lands around 7-9s. Sequential code takes ~18s and fails."""
    n = 8
    pool = []
    for i in range(n):
        p = _write(tmp_path, f"cand{i}.jpg", f"vice-city-candidate-{i}".encode())
        pool.append({
            "path": p,
            "source": "universal_yandex",
            "provider": "universal_yandex",
            "title": f"Vice City neon night photo {i}",
            "url": f"https://example.com/img{i}.jpg",
            "source_url": f"https://example.com/img{i}.jpg",
            "page_url": f"https://example.com/img{i}.jpg",
            "candidate_id": f"web:rescue:cand{i}",
            "score": 0.9 - i * 0.05,
            "metadata_score": 0.9 - i * 0.05,
            "final_score": 0.9 - i * 0.05,
            "container_id": CONTAINER,
        })

    def web_lane(scene, _cancel):
        if scene.get("scene_index") == 0:
            return [dict(row) for row in pool]
        return []

    calls = []
    scenes = [_scene(i, f"Vice City scene {i} neon streets") for i in range(n)]
    t0 = time.monotonic()
    results = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=lambda _scene, _cancel: [],
        web_lane=web_lane,
        visual_verifier=_slow_accepting_verifier(1.0, calls),
        neutral_factory=lambda _scene: None,
        deadline_seconds=600,
        max_workers=1,
    )
    elapsed = time.monotonic() - t0
    # All scenes resolve to real web media (7 via the rescue).
    assert len(results) == n
    for idx in range(1, n):
        assert results[idx].provider == "web"
        assert results[idx].status == "accepted"
        assert results[idx].resolution_kind == "pre_stock_web_rescue"
    # Parallel: 17 slow calls, but the main loop's sequential calls (~10s)
    # dominate; the rescue's 7 calls run concurrently (~1s). Sequential
    # rescue: ~18s. Parallel rescue: ~11-12s. The threshold sits between
    # them with margin (the rescue itself is ~1s, not 7s).
    assert elapsed < 14.0, f"rescue took {elapsed:.1f}s — still sequential"


def test_rescue_runs_scenes_in_parallel_deterministic(tmp_path):
    """Parallel rescue must be deterministic in OUTPUT: same scene gets
    the same asset regardless of completion order. Run twice and compare."""
    n = 6
    pool = []
    for i in range(n):
        p = _write(tmp_path, f"det{i}.jpg", f"vice-city-det-{i}".encode())
        pool.append({
            "path": p,
            "source": "universal_yandex",
            "provider": "universal_yandex",
            "title": f"Vice City neon night photo {i}",
            "url": f"https://example.com/det{i}.jpg",
            "source_url": f"https://example.com/det{i}.jpg",
            "page_url": f"https://example.com/det{i}.jpg",
            "candidate_id": f"web:rescue:det{i}",
            "score": 0.9 - i * 0.05,
            "metadata_score": 0.9 - i * 0.05,
            "final_score": 0.9 - i * 0.05,
            "container_id": CONTAINER,
        })

    def web_lane(scene, _cancel):
        if scene.get("scene_index") == 0:
            return [dict(row) for row in pool]
        return []

    def run_once():
        calls = []
        scenes = [_scene(i, f"Vice City scene {i} neon streets") for i in range(n)]
        results = resolve_storyboard_media_v2(
            scenes,
            youtube_lane=lambda _scene, _cancel: [],
            web_lane=web_lane,
            visual_verifier=_slow_accepting_verifier(0.05, calls),
            neutral_factory=lambda _scene: None,
            deadline_seconds=600,
            max_workers=1,
        )
        return {idx: res.selected_path for idx, res in sorted(results.items())}

    first = run_once()
    second = run_once()
    assert first == second, "parallel rescue is not deterministic in output"


class _SpentVisualContext:
    """A visual context whose call cap is already spent (the rescue case)."""

    def __init__(self):
        self.deadline_at = 0.0
        self.max_calls = 10
        self.calls = 10
        self.degraded_reason = "visual_call_budget_exceeded"
        self._lock = None

    def refresh_deadline(self, *, deadline_at):
        self.deadline_at = deadline_at


def test_rescue_visual_budget_scales_with_unresolved_count():
    """A 112-scene rescue needs far more than the fixed 10-call headroom.
    Headroom must grow with the unresolved count (capped at 64)."""
    import time as _time

    context = _SpentVisualContext()
    delivery_deadline = _time.monotonic() + 3600.0
    record = _grant_rescue_visual_budget(
        context, 112, "external_agent", delivery_deadline
    )
    assert record is not None
    assert record["headroom"] == 64, f"headroom={record['headroom']}, want 64 for 112 scenes"
    assert context.max_calls == 10 + 64


def test_rescue_visual_budget_small_job_unchanged():
    """A small rescue keeps a small headroom (one call per scene, min 1)."""
    import time as _time

    context = _SpentVisualContext()
    delivery_deadline = _time.monotonic() + 3600.0
    record = _grant_rescue_visual_budget(
        context, 3, "external_agent", delivery_deadline
    )
    assert record is not None
    assert record["headroom"] == 3
    assert context.max_calls == 10 + 3


def test_rescue_visual_window_scales_with_unresolved_count():
    """The rescue window must grow with the unresolved count so 112 scenes
    are not cut off after a fixed 300s. Window = per-scene allowance x
    unresolved, clamped to [300s, delivery headroom]."""
    import time as _time

    context = _SpentVisualContext()
    delivery_deadline = _time.monotonic() + 3600.0
    record = _grant_rescue_visual_budget(
        context, 112, "external_agent", delivery_deadline
    )
    assert record is not None
    # 112 scenes need far more than the old fixed 300s window.
    # Generous budget (user order Nov 22): 30s/scene x 112 = 3360s.
    assert record["window_seconds"] > 300.0, (
        f"window={record['window_seconds']}s, want >300s for 112 scenes"
    )
    assert record["window_seconds"] >= 3360.0, (
        f"window={record['window_seconds']}s, want >=3360s (30s/scene x 112)"
    )
    assert record["rescue_deadline"] <= delivery_deadline

def _rescue_pool(tmp_path, n):
    """A pool of n unverified web candidates (one per scene index)."""
    pool = []
    for i in range(n):
        p = _write(tmp_path, f"rt{i}.jpg", f"vice-city-retry-{i}".encode())
        pool.append({
            "path": p,
            "source": "universal_yandex",
            "provider": "universal_yandex",
            "title": f"Vice City neon night photo {i}",
            "url": f"https://example.com/rt{i}.jpg",
            "source_url": f"https://example.com/rt{i}.jpg",
            "page_url": f"https://example.com/rt{i}.jpg",
            "candidate_id": f"web:rescue:rt{i}",
            "score": 0.9 - i * 0.05,
            "metadata_score": 0.9 - i * 0.05,
            "final_score": 0.9 - i * 0.05,
            "container_id": CONTAINER,
        })
    return pool


def _accept_verdict():
    return {
        "accepted": True,
        "status": "accepted",
        "visual_checked": True,
        "semantic_verified": True,
        "visual_available": True,
        "entity_conflict": False,
        "fatal_mismatch": False,
        "final_score": 70.0,
        "identity_score": 90.0,
    }


def test_rescue_retries_transient_vision_failure(tmp_path):
    """A transient vision failure (verifier raises) is retried once and
    the scene is rescued on the retry. User order Nov 22: "add retries"
    for failed vision calls where safe."""
    n = 4
    pool = _rescue_pool(tmp_path, n)

    def web_lane(scene, _cancel):
        if scene.get("scene_index") == 0:
            return [dict(row) for row in pool]
        return []

    calls = []
    failed_once = set()

    def flaky_verifier(scene, candidate):
        key = (scene.get("scene_index"), candidate.get("candidate_id"))
        calls.append(key)
        if key not in failed_once:
            failed_once.add(key)
            raise RuntimeError("transient pool hiccup")
        return _accept_verdict()

    scenes = [_scene(i, f"Vice City scene {i} neon streets") for i in range(n)]
    results = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=lambda _scene, _cancel: [],
        web_lane=web_lane,
        visual_verifier=flaky_verifier,
        neutral_factory=lambda _scene: None,
        deadline_seconds=600,
        max_workers=1,
    )
    # The transient failure was retried (each scene's top candidate was
    # attempted twice) and the scenes were rescued anyway.
    assert len(results) == n
    rescued = [
        idx for idx in range(1, n)
        if results[idx].resolution_kind == "pre_stock_web_rescue"
    ]
    assert rescued, "no scene was rescued after the transient failure"
    # At least one scene needed the retry (2 calls for the same key).
    from collections import Counter

    counts = Counter(calls)
    assert max(counts.values()) >= 2, (
        f"verifier was never retried: counts={dict(counts)}"
    )


def test_rescue_does_not_retry_hard_vision_reject(tmp_path):
    """A hard vision REJECT is a verdict, not a failure: it is never
    retried. The verifier is called exactly once per scene."""
    n = 4
    pool = _rescue_pool(tmp_path, n)

    def web_lane(scene, _cancel):
        if scene.get("scene_index") == 0:
            return [dict(row) for row in pool]
        return []

    calls = []

    def rejecting_verifier(scene, candidate):
        calls.append((scene.get("scene_index"), candidate.get("candidate_id")))
        return {
            "accepted": False,
            "status": "rejected",
            "visual_checked": True,
            "semantic_verified": False,
            "visual_available": True,
            "entity_conflict": False,
            "fatal_mismatch": True,
            "final_score": 10.0,
            "reason": "fatal_mismatch",
        }

    scenes = [_scene(i, f"Vice City scene {i} neon streets") for i in range(n)]
    results = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=lambda _scene, _cancel: [],
        web_lane=web_lane,
        visual_verifier=rejecting_verifier,
        neutral_factory=lambda _scene: None,
        deadline_seconds=600,
        max_workers=1,
    )
    # No scene's candidate was verified twice by the rescue: a reject is
    # final. (The main loop may also call the verifier once per scene, so
    # a (scene, candidate) can appear twice overall — once from the main
    # loop, once from the rescue — but never three times, which is what a
    # rescue retry would produce.)
    from collections import Counter

    counts = Counter(calls)
    assert counts, "verifier was never called"
    assert max(counts.values()) <= 2, (
        f"a hard reject was retried: counts={dict(counts)}"
    )
    # Direct proof: the rescue's retry marker never fires for a reject.
    for idx, res in sorted(results.items()):
        for attempt in res.attempts or []:
            assert attempt.get("status") != "visual_verification_retry", (
                f"scene {idx} retried a hard vision reject: {attempt}"
            )
    assert len(results) == n


def test_rescue_verifies_scenes_concurrently(tmp_path):
    """The rescue runs 8 workers: with 7 rescue scenes and a 0.5s
    verifier, at least 6 vision calls must overlap. A 4-worker rescue
    could never exceed 4 concurrent calls. User order Nov 22: "add more
    parallel work, to be quick"."""
    import threading

    n = 8
    pool = _rescue_pool(tmp_path, n)

    def web_lane(scene, _cancel):
        if scene.get("scene_index") == 0:
            return [dict(row) for row in pool]
        return []

    lock = threading.Lock()
    state = {"in_flight": 0, "max_in_flight": 0}

    def counting_verifier(scene, candidate):
        with lock:
            state["in_flight"] += 1
            state["max_in_flight"] = max(
                state["max_in_flight"], state["in_flight"]
            )
        try:
            time.sleep(0.5)
            return _accept_verdict()
        finally:
            with lock:
                state["in_flight"] -= 1

    scenes = [_scene(i, f"Vice City scene {i} neon streets") for i in range(n)]
    results = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=lambda _scene, _cancel: [],
        web_lane=web_lane,
        visual_verifier=counting_verifier,
        neutral_factory=lambda _scene: None,
        deadline_seconds=600,
        max_workers=1,
    )
    assert len(results) == n
    assert state["max_in_flight"] >= 6, (
        f"max concurrent vision calls={state['max_in_flight']}, "
        "want >=6 (8 rescue workers)"
    )
