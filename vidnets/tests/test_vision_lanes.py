"""Separate vision lanes: per-lane budgets, clocks, batching, drain-only gate.

Vision lanes (2026-11-22): test 26 proved one shared vision deadline
starves the web lane (its clock started at context construction; web
does retrieval first, so by its visual phase the remaining budget was
-439s). Each lane now gets its own call budget, its own clock (starting
at that lane's first vision submission), and its own in-flight cap,
under one global cap of 8. Web judges 8 scenes x 1 candidate per
request; YouTube judges 4 candidates x 2 frames per request. Once a
lane's budget closes, the gate files zero new calls for it (drain-only).

Every test here must FAIL on clean HEAD (no lane budgets, no
cross-scene batching, no drain-only gate) and PASS with the fix.
"""
import copy
import hashlib
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services.job_context import job_scope
from services.visual_intelligence.lane_budget import (
    LaneScheduler,
    LaneVisionBudget,
    create_lane_budgets,
    lane_budget_expired_outcome,
    lane_budget_seconds,
    pack_web_scenes,
    split_call_budgets,
    web_wave_count,
    yt_request_count,
)
from services.visual_intelligence.vision_client import (
    QwenVisionClient,
    _normalise_clip_response,
)
from services.visual_intelligence.prompts import (
    build_clip_batch_prompt,
    CLIP_BATCH_RESPONSE_FORMAT,
    CLIP_BATCH_SYSTEM_PROMPT,
)
from services.web_image.resolver import _verify_web_scenes_cross_scene
from services.media_resolution_coordinator import _late_web_branch_accept
from services.pipeline_v2_media_adapter import LazyBatchLane


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeClock:
    def __init__(self, now=1000.0):
        self.now = float(now)

    def __call__(self):
        return self.now


def _passing_model_row(candidate_id):
    """A live vision verdict that accepts: high scores, no conflicts."""
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


def _image(tmp_path, name, color=(12, 34, 56)):
    path = tmp_path / name
    Image.new("RGB", (64, 48), color).save(path, "JPEG", quality=70)
    return str(path)


def _web_task(idx, tmp_path, n_candidates=1):
    candidates = []
    for j in range(n_candidates):
        candidates.append({
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
    return {
        "scene_index": idx,
        "scene_text": f"Grand Theft Auto VI street scene {idx}",
        "subject": "Grand Theft Auto VI",
        "global_topic": "Grand Theft Auto VI",
        "query": "GTA VI street",
        "candidates": candidates,
    }


class FakeWebClient:
    """Records analyze_images_batch requests; returns passing verdicts."""

    def __init__(self, rows=None):
        self.calls = []
        self.lock = threading.Lock()
        self.rows = rows  # optional {candidate_id: row} override
        self.model = "qwen/test"

    def analyze_images_batch(self, paths, prompt, system, ids, scene_tags=None):
        with self.lock:
            self.calls.append({"paths": list(paths), "ids": list(ids),
                               "scene_tags": list(scene_tags or [])})
        out = {}
        for cid in ids:
            if self.rows is not None and cid in self.rows:
                out[cid] = dict(self.rows[cid])
            else:
                out[cid] = _passing_model_row(cid)
        return out


def _fake_visual_context(**overrides):
    ctx = SimpleNamespace(model="qwen/test", visual_required=False, fail_closed=False)
    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx


# ---------------------------------------------------------------------------
# T1 — lane clock starts at first submission
# ---------------------------------------------------------------------------

def test_lane_clock_starts_at_first_submission(monkeypatch):
    """T1: retrieval time before first submission consumes nothing.

    A LaneVisionBudget constructed at T0 but first submitted at T0+500s
    has deadline_at ≈ T0+500+budget, not T0+budget. HEAD has no lane
    budget; the shared context deadline is set at construction.
    """
    monkeypatch.setenv("VIDZEO_VISION_LANE_WEB_SECONDS_PER_SCENE", "25")
    monkeypatch.setenv("VIDZEO_VISION_LANE_BUDGET_FLOOR_SECONDS", "180")
    clock = FakeClock(1000.0)
    budget = LaneVisionBudget("web", scene_count=13, clock=clock)
    assert budget.budget_seconds == 325.0  # max(180, 25*13)
    clock.now = 1500.0  # 500s of retrieval before the first vision call
    assert budget.remaining() == 325.0  # unstarted: full budget remains
    assert not budget.expired()
    deadline = budget.start_if_needed()
    assert budget.started_at == 1500.0
    assert deadline == pytest.approx(1500.0 + 325.0)
    assert budget.remaining() == pytest.approx(325.0)


# ---------------------------------------------------------------------------
# T2 — YT spend cannot starve the web floor
# ---------------------------------------------------------------------------

def test_yt_spend_cannot_starve_web_floor(monkeypatch):
    """T2: YT spends its whole budget; web still gets its full floor.

    Fake clock + fake pool: the YT lane spends its entire call budget
    (own 3N+4 plus the whole shared reserve) and its full time budget;
    the web lane still gets its full floor (calls + seconds + >=3
    in-flight slots) and completes its wave-1 batches. HEAD: one shared
    deadline/counter; YT-first spend leaves web the 240s floor.
    """
    monkeypatch.setenv("VIDZEO_VISION_LANE_YT_SECONDS_PER_SCENE", "30")
    monkeypatch.setenv("VIDZEO_VISION_LANE_WEB_SECONDS_PER_SCENE", "25")
    monkeypatch.setenv("VIDZEO_VISION_LANE_BUDGET_FLOOR_SECONDS", "180")
    clock = FakeClock(1000.0)
    created = create_lane_budgets(13, clock=clock)
    budgets = created["budgets"]
    split = split_call_budgets(13)
    assert split == {"youtube": 43, "web": 30, "reserve": 13, "total": 86}
    assert budgets["youtube"].max_calls == 43
    assert budgets["web"].max_calls == 30

    yt = budgets["youtube"]
    spent = sum(1 for _ in range(60) if yt.try_record_call())
    assert spent == 43 + 13  # own budget + entire shared reserve
    assert yt.try_record_call() is False  # nothing left for YT

    clock.now = 1400.0  # past the YT deadline (1000 + 390)
    assert yt.expired()
    web = budgets["web"]
    assert not web.expired()  # web clock never started
    assert web.remaining() == pytest.approx(325.0)
    assert web.try_record_call() is True  # web's own calls untouched
    assert web.started_at == 1400.0
    assert web.remaining() == pytest.approx(325.0)

    # In-flight floor: YT holds its 5 slots; web still gets >= 3.
    scheduler = LaneScheduler()
    assert all(scheduler.acquire("youtube", timeout=0.1) for _ in range(5))
    assert scheduler.acquire("youtube", timeout=0.0) is False  # YT cap
    assert all(scheduler.acquire("web", timeout=0.1) for _ in range(3))
    assert scheduler.peak_global == 8
    assert scheduler.acquire("web", timeout=0.0) is False  # global cap
    scheduler.release("youtube")
    assert scheduler.acquire("web", timeout=0.1) is True  # slot floats to web


# ---------------------------------------------------------------------------
# T3 — web batch covers 8 scenes in one request
# ---------------------------------------------------------------------------

def test_web_batch_covers_8_scenes_in_one_request(tmp_path):
    """T3: 13 web scenes, wave 1 -> exactly 2 requests (8+5).

    Each request carries distinct scene_index values, one verdict row
    per candidate. HEAD files 13 per-scene analyze_images_batch calls.
    """
    tasks = [_web_task(idx, tmp_path) for idx in range(13)]
    client = FakeWebClient()
    clock = FakeClock(1000.0)
    budgets = create_lane_budgets(13, clock=clock)["budgets"]
    scheduler = LaneScheduler()
    published = []
    outcomes = _verify_web_scenes_cross_scene(
        tasks,
        visual_context=_fake_visual_context(),
        client=client,
        lane_budget=budgets["web"],
        scheduler=scheduler,
        on_scene_result=lambda idx, result: published.append(idx),
    )
    assert len(client.calls) == 2, f"wave 1 must file 2 requests, got {len(client.calls)}"
    sizes = sorted(len(call["ids"]) for call in client.calls)
    assert sizes == [5, 8], f"packs must be 8+5, got {sizes}"
    all_tags = [tag for call in client.calls for tag in call["scene_tags"]]
    assert sorted(all_tags) == list(range(13)), "each scene judged exactly once"
    assert sorted(published) == list(range(13)), "on_scene_result fires per scene"
    assert len(outcomes) == 13
    for idx, result in outcomes.items():
        assert result is not None and result.get("path"), f"scene {idx} must have a winner"
        assert result.get("visual_outcome") == "pass"


# ---------------------------------------------------------------------------
# T4 — batch partial failure retries only missing candidates
# ---------------------------------------------------------------------------

def test_batch_partial_failure_retries_only_missing():
    """T4: a failed batch retries only the missing candidates.

    Fake client returns verdicts for 6/8 candidates, then fails the
    batch; the scheduler retries only the 2 missing, never re-files
    the 6 decided ones (asserted from the fake client's call log).
    HEAD: a failed batch raises and the caller falls back to sequential
    singles for the whole scene.
    """
    scheduler = LaneScheduler()
    ids = [f"web:{i}" for i in range(8)]
    calls = []

    def flaky_call(group):
        calls.append(list(group))
        if len(calls) == 1:
            return {cid: {"verdict": "accept"} for cid in group[:6]}
        raise RuntimeError("batch transport failed")

    results = scheduler.file_batch("web", "web-wave-1-0", ids, flaky_call)
    assert set(results) == set(ids[:6])
    assert calls[0] == ids, "first filing covers all 8"
    retried = [cid for call in calls[1:] for cid in call]
    assert set(retried) == set(ids[6:]), f"only the 2 missing are retried, got {retried}"
    assert not (set(ids[:6]) & set(retried)), "a decided candidate is never re-judged"


# ---------------------------------------------------------------------------
# T5 — YT batch 4 candidates x 2 frames
# ---------------------------------------------------------------------------

def _batch_row(candidate_id, verdict="accept", score=80.0, **patch):
    row = {
        "candidate_id": candidate_id,
        "verdict": verdict,
        "reason": "shows the documentary subject",
        "score": score,
        "story_relevance": score,
        "confidence": 85.0,
        "fatal_mismatch": False,
        "talking_head": False,
    }
    row.update(patch)
    return row


def test_yt_batch_4_candidates_2_frames(tmp_path, monkeypatch):
    """T5: 4 candidates x 2 frames -> exactly 1 request, 8 images.

    Four verdict rows keyed by candidate_id; frame groups preserved
    per candidate. HEAD sends 1 frame per candidate and chunks at 16
    candidates.
    """
    frames = [
        [_image(tmp_path, f"c{i}f1.jpg", (10 + i, 20, 30)),
         _image(tmp_path, f"c{i}f2.jpg", (200, 30 + i, 30))]
        for i in range(4)
    ]
    payload = {"candidates": [_batch_row(f"clip{i}", score=82.0) for i in range(4)]}
    calls = []

    def fake_post_json(self, content, **kwargs):
        calls.append((content, kwargs))
        return json.dumps(payload)

    monkeypatch.setattr(QwenVisionClient, "_post_json", fake_post_json)
    with job_scope({"llm_cache_dir": str(tmp_path / "cache")}):
        client = QwenVisionClient("account-a", model="qwen/test")
        out = client.analyze_clip_frames_batch(
            [{"candidate_id": f"clip{i}", "frame_paths": frames[i]} for i in range(4)],
            user_prompt=build_clip_batch_prompt("t", "n", "s", [f"clip{i}" for i in range(4)]),
            system_prompt=CLIP_BATCH_SYSTEM_PROMPT,
        )
    assert len(calls) == 1, "4 candidates x 2 frames fit in one request"
    content, _kwargs = calls[0]
    image_parts = [c for c in content if c.get("type") == "image_url"]
    assert len(image_parts) == 8, "2 frames per candidate, 8 images total"
    assert set(out) == {f"clip{i}" for i in range(4)}
    assert all(out[f"clip{i}"]["batch_verdict"] == "accept" for i in range(4))


# ---------------------------------------------------------------------------
# T6 — deterministic stamp never ships web where a verdict was required
# ---------------------------------------------------------------------------

def _deterministic_candidate(path, status):
    return {
        "path": path,
        "source": "web",
        "qwen_result": {
            "accepted": True,
            "status": "accepted",
            "verification_status": status,
            "final_score": 80.0,
            "visual_checked": False,
            "semantic_verified": False,
        },
    }


def test_deterministic_stamp_never_ships_web_where_verdict_required(tmp_path):
    """T6: strict scene + expired lane budget -> unavailable, never pass.

    A lane-budget expiry outcome is `unavailable`/`visual_unverified`,
    and `_late_web_branch_accept` refuses a row whose verdict status is
    deterministic_web_image / deterministic_only / deterministic_degraded.
    HEAD: the seeding half is already correct (d0f80b7) — this pins the
    new cross-scene row shape against the same rule; it fails on HEAD
    because cross-scene rows do not exist.
    """
    outcome = lane_budget_expired_outcome(visual_required=True)
    assert outcome.get("visual_outcome") == "unavailable"
    assert outcome.get("visual_unverified") is True
    assert outcome.get("visual_outcome") != "pass"

    still = _image(tmp_path, "still.jpg")
    scene = {"scene_index": 0, "text": "Grand Theft Auto VI street scene",
             "visual_requirement": {"strict_identity": True}}
    for status in ("deterministic_web_image", "deterministic_only", "deterministic_degraded"):
        assert _late_web_branch_accept(scene, _deterministic_candidate(still, status)) is None, \
            f"{status} must never ship where a live verdict was required"


# ---------------------------------------------------------------------------
# T7 — late-web promotion seeds a cross-scene batch row
# ---------------------------------------------------------------------------

def test_late_web_promotion_seeds_cross_scene_batch(tmp_path):
    """T7: a late web row from a cross-scene batch is seeded; a row with
    another scene's hash is refused.

    The row is a real `_build_web_visual_result` row from the cross-scene
    driver (per-row scene_index + requirement_hash + live evidence).
    """
    from services.visual_intelligence.decision import scene_context_hash

    tasks = [_web_task(0, tmp_path)]
    client = FakeWebClient()
    outcomes = _verify_web_scenes_cross_scene(
        tasks,
        visual_context=_fake_visual_context(),
        client=client,
        lane_budget=None,
        scheduler=None,
    )
    row = outcomes[0]
    assert row is not None and row.get("visual_outcome") == "pass"
    req_hash = str(row.get("requirement_hash") or "")
    assert req_hash == scene_context_hash(
        narration=tasks[0]["scene_text"], subject="Grand Theft Auto VI",
        topic="Grand Theft Auto VI", media_type="image")

    scene = {
        "scene_index": 0,
        "text": tasks[0]["scene_text"],
        "visual_requirement": {"requirement_hash": req_hash},
    }
    accepted = _late_web_branch_accept(scene, row)
    assert accepted is not None and accepted[0] is True, "live cross-scene row must seed"

    other_scene_row = copy.deepcopy(row)
    other_scene_row["requirement_hash"] = "hash-from-another-scene"
    other_scene_row["visual_decision"] = dict(row["visual_decision"])
    other_scene_row["visual_decision"]["verified_requirement_hash"] = "hash-from-another-scene"
    other_scene_row["visual_decision"]["scene_context_hash"] = "hash-from-another-scene"
    assert _late_web_branch_accept(scene, other_scene_row) is None, \
        "a row bound to another scene must be refused"


# ---------------------------------------------------------------------------
# T8 — long-video scaling
# ---------------------------------------------------------------------------

def test_long_video_scaling(monkeypatch):
    """T8: N=70 web wave-1 requests <= 9, waves at 4 in flight <= 3.

    Media-seconds-per-scene (fake latency L per request) at N=70 is
    below N=13 for the web lane; YT requests stay <= N (early exit
    respected). HEAD files N requests per lane regardless of N.
    """
    monkeypatch.setenv("VIDZEO_WEB_VISION_BATCH_IMAGES", "8")
    packs_70 = pack_web_scenes(range(70))
    assert len(packs_70) == 9, f"70 scenes pack into 9 batches, got {len(packs_70)}"
    assert all(len(pack) >= 4 for pack in packs_70), "never a <4 pack while >=4 unverified"
    assert web_wave_count(70, inflight=4) == 3
    assert web_wave_count(13, inflight=4) == 1
    # Fake latency L per request: seconds-per-scene falls with N.
    assert (web_wave_count(70, inflight=4) / 70.0) < (web_wave_count(13, inflight=4) / 13.0)
    assert yt_request_count(70) == 70
    assert lane_budget_seconds("web", 70) == max(180, 25 * 70)
    assert lane_budget_seconds("youtube", 70) == max(180, 30 * 70)


# ---------------------------------------------------------------------------
# T9 — gate grace is drain-only
# ---------------------------------------------------------------------------

def test_gate_grace_is_drain_only(tmp_path):
    """T9: after both lane budgets close, the gate files zero new calls.

    The coordinator collects already-in-flight verdicts only; the fake
    verifier's call log stays empty. HEAD's gate files new
    verify_primary calls until visual_gate_deadline_at — and HEAD has
    no vision_lane_budgets kwarg at all, so this fails there outright.
    """
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    clip = tmp_path / "yt_clip.mp4"
    clip.write_bytes(b"\x00" * 1024)
    still = tmp_path / "web_still.jpg"
    still.write_bytes(b"\x00" * 1024)
    calls = []

    def yt_lane(_scene, _cancel):
        return [{"path": str(clip), "source": "youtube", "provider": "youtube",
                 "media_type": "video", "candidate_id": "youtube:first",
                 "title": "Grand Theft Auto VI gameplay footage", "metadata_score": 0.0}]

    def web_lane(_scene, _cancel):
        return [{"path": str(still), "source": "web", "provider": "web",
                 "media_type": "image", "candidate_id": "web:still",
                 "title": "Grand Theft Auto VI official screenshot", "final_score": 80.0}]

    class CountingVerifier:
        def verify_batch(self, _scene, _candidates):
            calls.append(("batch", [str(c.get("path")) for c in _candidates]))
            return {}

        def __call__(self, _scene, _candidate):
            calls.append(("single", str(_candidate.get("path"))))
            return {"accepted": True, "status": "visual_pass", "final_score": 95.0,
                    "visual_checked": True, "visual_available": True}

    clock = FakeClock(1000.0)
    budgets = create_lane_budgets(1, clock=clock)["budgets"]
    for budget in budgets.values():
        budget.start_if_needed()
    clock.now = 2000.0  # past both lane deadlines (180s floors)
    assert all(b.expired() for b in budgets.values())

    result = resolve_storyboard_media_v2(
        [{"scene_index": 0, "scene_id": "gta", "text": "Grand Theft Auto VI street scene"}],
        youtube_lane=yt_lane,
        web_lane=web_lane,
        visual_verifier=CountingVerifier(),
        neutral_factory=lambda _scene: None,
        deadline_seconds=5,
        vision_lane_budgets=budgets,
    )
    assert calls == [], f"drain-only gate must file zero new vision calls, got {calls}"
    assert 0 in result  # the scene still resolves (degraded), never crashes


# ---------------------------------------------------------------------------
# T10 — pool ceiling never exceeded
# ---------------------------------------------------------------------------

def test_pool_ceiling_never_exceeded():
    """T10: both lanes busy with deep queues -> in-flight <= 8 always,
    and each lane holds >= 3 while both have pending work. HEAD has no
    global in-flight accounting across lanes (gate 8 + web 6 + YT 8 can
    be filed concurrently; only per-context semaphores bind).
    """
    scheduler = LaneScheduler()
    assert all(scheduler.acquire("youtube", timeout=0.1) for _ in range(5))
    assert all(scheduler.acquire("web", timeout=0.1) for _ in range(3))
    assert scheduler.peak_global == 8
    assert scheduler.peak_lane == {"youtube": 5, "web": 3}
    assert scheduler.acquire("youtube", timeout=0.0) is False
    assert scheduler.acquire("web", timeout=0.0) is False

    # A waiting web batch gets the next freed slot; YT cannot take it
    # past its cap while web is still below its floor.
    acquired = []

    def wait_web():
        if scheduler.acquire("web", timeout=5.0):
            acquired.append("web")

    waiter = threading.Thread(target=wait_web, daemon=True)
    waiter.start()
    time.sleep(0.2)  # let the waiter register below the global cap
    assert scheduler.acquire("youtube", timeout=0.0) is False
    scheduler.release("youtube")
    waiter.join(timeout=5.0)
    assert acquired == ["web"], "the freed slot floats to the waiting web lane"
    assert scheduler.inflight == {"youtube": 4, "web": 4}
    assert scheduler.peak_global <= 8


# ---------------------------------------------------------------------------
# T11 — web loader publishes a row per scene
# ---------------------------------------------------------------------------

def test_web_loader_publishes_row_per_scene(tmp_path):
    """T11: a scene's verdicts publish while other scenes still load.

    `LazyBatchLane.publish_row` wakes a scene waiter without waiting
    for the whole batch, and the resolver's `on_scene_result` callback
    fires per scene as verdicts land (the wiring the web loader uses).
    HEAD: the web loader never calls publish_row, and the resolver has
    no on_scene_result parameter at all.
    """
    import inspect
    from services.web_image.resolver import resolve_web_images_for_video_fast

    assert "on_scene_result" in inspect.signature(
        resolve_web_images_for_video_fast).parameters, \
        "resolver must accept the per-scene publish callback"

    holder = {}
    published = {}

    def slow_loader():
        holder["lane"].publish_row(3, {"path": "scene3.jpg", "source": "web"})
        published[3] = True
        time.sleep(2.0)  # scenes 1-2 still loading
        return {3: {"path": "scene3.jpg", "source": "web"}}

    lane = LazyBatchLane(slow_loader)
    holder["lane"] = lane
    got = {}

    def wait_scene_3():
        got["row"] = lane({"scene_index": 3}, threading.Event())

    waiter = threading.Thread(target=wait_scene_3, daemon=True)
    started = time.monotonic()
    waiter.start()
    waiter.join(timeout=5.0)
    elapsed = time.monotonic() - started
    assert got.get("row") == {"path": "scene3.jpg", "source": "web"}
    assert elapsed < 1.5, f"scene 3 must publish before the batch finishes (took {elapsed:.1f}s)"

    # The cross-scene driver fires on_scene_result per decided scene.
    tasks = [_web_task(idx, tmp_path) for idx in range(3)]
    seen = []
    outcomes = _verify_web_scenes_cross_scene(
        tasks,
        visual_context=_fake_visual_context(),
        client=FakeWebClient(),
        lane_budget=None,
        scheduler=None,
        on_scene_result=lambda idx, result: seen.append(idx),
    )
    assert sorted(seen) == [0, 1, 2]
    assert all(outcomes[idx] is not None for idx in range(3))
