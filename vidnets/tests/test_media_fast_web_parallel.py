"""Fast media: pool skipped, web parallel-upfront, short tail (Nov 21, 2026).

User-ordered media redesign (voice note, Nov 21 ~09:03 PKT):

* SKIP the general YouTube pool (Track B). It verified 0 clips in GTA
  test 18 AND test 19 — the parallel search finds good candidates, but
  the pool's fresh downloads time out, so nothing banks. The pool is
  DISABLED by default (config ``yt_general_pool_enabled`` / env
  ``VIDZEO_YT_GENERAL_POOL_ENABLED`` re-enables it); the code stays so
  it can come back if download transport becomes reliable.
* WEB IMAGES IN PARALLEL UPFRONT. The coordinator already submits the
  web lane for every scene in the same single discovery wave as the
  YouTube lane (``media_resolution_coordinator``: one wave, all scenes,
  both lanes), and the web ``LazyBatchLane`` runs one all-scene batch
  in the background — so web images are resolved beside YT from the
  start, not after YT times out. These tests pin that orchestration:
  the web lane is called for all scenes while YouTube is still slow,
  and a scene whose YouTube clip never verifies still gets its web
  image.
* SHORT TAIL. Total media for a 1-min video targets ~5-8 min: 300s
  discovery + 120s tail = 420s. The tail knob
  (``yt_media_tail_seconds`` / ``VIDZEO_YT_MEDIA_TAIL_SECONDS``) still
  works; only the DEFAULT dropped from 600s to 120s. A scene cut off
  by the tail resolves to its best-so-far verdict or the already
  resolved web image — never a hang, never a blank.
"""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services.media_resolution_coordinator import resolve_storyboard_media_v2
from services.youtube_discovery_budget import total_media_budget_seconds
from services.youtube_general_pool import general_pool_enabled

_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6300000002000100e5022a4b0000000049454e44ae426082"
)


# ---------------------------------------------------------------------------
# 1. General pool disabled by default (Track B skipped)
# ---------------------------------------------------------------------------


def test_general_pool_disabled_by_default(monkeypatch):
    monkeypatch.delenv("VIDZEO_YT_GENERAL_POOL_ENABLED", raising=False)
    assert general_pool_enabled({}) is False
    assert general_pool_enabled(None) is False


def test_general_pool_env_off_is_disabled(monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_GENERAL_POOL_ENABLED", "0")
    assert general_pool_enabled({}) is False
    monkeypatch.setenv("VIDZEO_YT_GENERAL_POOL_ENABLED", "false")
    assert general_pool_enabled({}) is False


def test_general_pool_can_be_reenabled(monkeypatch):
    monkeypatch.delenv("VIDZEO_YT_GENERAL_POOL_ENABLED", raising=False)
    assert general_pool_enabled({"yt_general_pool_enabled": True}) is True
    assert general_pool_enabled({"yt_general_pool_enabled": 1}) is True
    monkeypatch.setenv("VIDZEO_YT_GENERAL_POOL_ENABLED", "1")
    assert general_pool_enabled({}) is True
    # Explicit config off wins over env on.
    assert general_pool_enabled({"yt_general_pool_enabled": False}) is False


# ---------------------------------------------------------------------------
# 2. Total media budget = discovery + shortened tail (default 120s)
# ---------------------------------------------------------------------------


def test_total_media_budget_is_discovery_plus_short_tail(monkeypatch):
    monkeypatch.delenv("VIDZEO_YT_MEDIA_TAIL_SECONDS", raising=False)
    # 1-min video: 300s discovery + 120s tail = 420s (~7 min).
    assert total_media_budget_seconds(300.0) == 420.0
    assert total_media_budget_seconds(292.0) == 412.0
    assert total_media_budget_seconds(1500.0) == 1620.0


def test_media_tail_knob_still_overrides_the_short_default(monkeypatch):
    monkeypatch.delenv("VIDZEO_YT_MEDIA_TAIL_SECONDS", raising=False)
    assert total_media_budget_seconds(
        300.0, config={"yt_media_tail_seconds": 600}
    ) == 900.0
    monkeypatch.setenv("VIDZEO_YT_MEDIA_TAIL_SECONDS", "600")
    assert total_media_budget_seconds(300.0) == 900.0


# ---------------------------------------------------------------------------
# Coordinator harness: web upfront + tail cut-off falls back to web
# ---------------------------------------------------------------------------


def _web_scene(index):
    return {
        "scene_id": f"web-{index}",
        "scene_index": index,
        "container_id": "gta",
        "text": "Grand Theft Auto VI gameplay in Vice City",
        "source_policy": {},
    }


def _web_image(tmp_path, index):
    path = tmp_path / f"web-{index}.png"
    path.write_bytes(_PNG_1X1 + b"\x00" * 512)
    return {
        "path": str(path),
        "source": "web",
        "candidate_id": f"web:IMG{index}",
        "title": "GTA VI Vice City gameplay",
        "final_score": 80.0,
    }


def test_web_lane_searched_upfront_for_all_scenes_while_youtube_slow(tmp_path):
    """Web is searched for ALL scenes in parallel with YouTube, from the
    start — not after YouTube times out. YouTube takes 3s per scene and
    returns nothing; the web lane must still be called for every scene
    while YouTube is still running, and every scene must end up with
    its web image as a candidate."""
    scene_count = 4
    scenes = [_web_scene(i) for i in range(scene_count)]
    images = [_web_image(tmp_path, i) for i in range(scene_count)]
    web_calls = {"starts": [], "indices": []}
    yt_calls = {"starts": []}
    started = time.monotonic()

    def slow_youtube_lane(_scene, _cancel):
        yt_calls["starts"].append(time.monotonic() - started)
        time.sleep(3.0)
        return []

    def fast_web_lane(scene, _cancel):
        web_calls["starts"].append(time.monotonic() - started)
        web_calls["indices"].append(scene["scene_index"])
        return [images[scene["scene_index"]]]

    # The real adapter wraps both lanes in LazyBatchLane (shared_batch),
    # which puts every scene in ONE discovery wave with both lanes
    # submitted together. Plain callables get per-container waves that
    # serialize scenes, so mark the stubs the same way.
    slow_youtube_lane.shared_batch = True
    fast_web_lane.shared_batch = True

    result = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=slow_youtube_lane,
        web_lane=fast_web_lane,
        visual_verifier=None,
        neutral_factory=lambda _scene: None,
        deadline_seconds=30,
        scene_budget_seconds=30,
        vision_reserve_seconds=0.5,
        adaptive_media_review=True,
    )
    # Every scene's web lane call started while YouTube was still slow
    # (all web starts < 1s in; YouTube needs 3s per scene).
    assert sorted(web_calls["indices"]) == list(range(scene_count))
    assert len(web_calls["starts"]) == scene_count
    assert max(web_calls["starts"]) < 1.0
    assert len(yt_calls["starts"]) == scene_count
    # Every scene has its web image among the discovered candidates.
    for index in range(scene_count):
        assert any(
            attempt.get("source") == "web"
            for attempt in result[index].attempts
        ), f"scene {index} has no web attempt"
    assert set(result) == set(range(scene_count))


def test_tail_cutoff_falls_back_to_resolved_web_without_stalling(tmp_path):
    """YouTube clips never verify (slow verifier, short tail) but the
    web images were already resolved upfront: every scene must still
    resolve terminally and fast, with the web image in its attempts —
    never a hang and never a blank."""
    scene_count = 4
    scenes = [_web_scene(i) for i in range(scene_count)]
    images = [_web_image(tmp_path, i) for i in range(scene_count)]

    def slow_verifier(_scene, _candidate):
        time.sleep(1.0)
        return {
            "accepted": True, "status": "visual_pass", "final_score": 90,
            "identity_score": 90, "action_score": 85, "context_score": 88,
            "visual_checked": True, "visual_available": True,
            "semantic_verified": True, "reason": "matches the scene",
        }

    def web_lane(scene, _cancel):
        return [images[scene["scene_index"]]]

    started = time.monotonic()
    result = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=lambda _scene, _cancel: [],
        web_lane=web_lane,
        visual_verifier=slow_verifier,
        neutral_factory=lambda _scene: None,
        deadline_seconds=30,
        scene_budget_seconds=30,
        vision_reserve_seconds=0.5,
        adaptive_media_review=True,
        verification_deadline_seconds=1.5,
    )
    elapsed = time.monotonic() - started
    # Bounded: the 1.5s verification deadline stops new vision calls,
    # so the stage finishes in seconds, not one round trip per scene.
    assert elapsed < 8.0
    assert set(result) == set(range(scene_count))
    for index in range(scene_count):
        assert result[index].status in {
            "accepted", "resolved", "fallback", "unverified", "degraded",
        }
        assert any(
            attempt.get("source") == "web"
            for attempt in result[index].attempts
        ), f"scene {index} lost its resolved web image at the cut-off"


# ---------------------------------------------------------------------------
# 4. Parallel pre-verification: inline quota vision issued concurrently
# ---------------------------------------------------------------------------


def _quota_scene(index):
    return {
        "scene_id": f"quota-{index}",
        "scene_index": index,
        "container_id": "gta",
        "text": "Grand Theft Auto VI gameplay in Vice City",
        "source_policy": {
            "youtube_quota": True,
            "visual_verification_required": True,
        },
    }


def _quota_clip(tmp_path, scene_index, rank):
    path = tmp_path / f"quota-{scene_index}-{rank}.mp4"
    path.write_bytes(_PNG_1X1 + b"\x00" * 2048)
    return {
        "path": str(path),
        "source": "youtube",
        "candidate_id": f"youtube:QVID{scene_index}{rank}",
        "video_id": f"QVID{scene_index}{rank}",
        "title": "Grand Theft Auto VI gameplay in Vice City",
        "final_score": 90.0 - rank * 5.0,
    }


def test_inline_quota_candidates_verified_concurrently_across_scenes(tmp_path):
    """The test-18 bottleneck: quota clips that miss the parallel gate
    (here the 4th candidate per scene — the gate only verifies the top
    3 finalists) used to be verified INLINE, one at a time, inside the
    sequential per-scene loop (~82s apart, ~17 min for 13 scenes).
    The pre-verification pass must issue them CONCURRENTLY across
    scenes, so all of them are in flight at once and the pool's workers
    answer in parallel.

    Proof: with a 2s verifier, the 4th-candidate verifications for all
    scenes must OVERLAP — every one starts before the first finishes.
    Sequential inline verification would start each only after the
    previous finished."""
    scene_count = 4
    scenes = [_quota_scene(i) for i in range(scene_count)]
    clips = {
        i: [_quota_clip(tmp_path, i, rank) for rank in range(4)]
        for i in range(scene_count)
    }
    fourth_paths = {str(clips[i][3]["path"]) for i in range(scene_count)}
    spans = {}

    def slow_verifier(scene, candidate):
        path = str(candidate.get("path") or "")
        start = time.monotonic()
        time.sleep(2.0)
        if path in fourth_paths:
            spans[path] = (start, time.monotonic())
        return {
            "accepted": True, "status": "visual_pass", "final_score": 90,
            "identity_score": 90, "action_score": 85, "context_score": 88,
            "visual_checked": True, "visual_available": True,
            "semantic_verified": True, "reason": "matches the scene",
        }

    def youtube_lane(scene, _cancel):
        return list(clips[scene["scene_index"]])

    started = time.monotonic()
    result = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=youtube_lane,
        web_lane=lambda _scene, _cancel: [],
        visual_verifier=slow_verifier,
        neutral_factory=lambda _scene: None,
        deadline_seconds=60,
        scene_budget_seconds=60,
        vision_reserve_seconds=0.5,
        adaptive_media_review=True,
    )
    elapsed = time.monotonic() - started
    assert set(result) == set(range(scene_count))
    # All four 4th-candidate verifications ran: the pre-pass found them.
    assert set(spans) == fourth_paths
    # They overlapped: the last one started before the first finished.
    # Sequential inline would have them strictly one after another.
    assert max(start for start, _end in spans.values()) < min(
        end for _start, end in spans.values()
    ), "4th-candidate verifications did not overlap — still sequential"
    # And the whole stage stayed far under the sequential cost
    # (4 scenes x 2s inline + gate time would be ~10s+).
    assert elapsed < 12.0
