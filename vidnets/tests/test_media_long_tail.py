"""Long-tail guard: post-discovery vision must stop at discovery + tail.

GTA test 18 (job 2451ee04) proved the gap: the 292s discovery budget
bounds discovery only. After discovery stopped, the coordinator's
per-scene finalization loop kept issuing SEQUENTIAL inline vision calls
(quota-inline clips bypass the per-scene budget; frame fallbacks are
checked before it) against the ~3600s media wall — 17 external-agent
round trips, ~22 min, 05:30:06 -> 05:51:22, 54% of the 41-min media
stage. The stage ended by candidate exhaustion, not by any deadline.

The fix: ``total_media_budget_seconds`` (discovery budget + tail,
default 120s) is passed as ``verification_deadline_seconds`` into
``resolve_storyboard_media_v2``, which clamps the visual-gate and
web-recovery-visual deadlines to it at both computation sites (initial
and the post-discovery refresh). These tests prove:

* the total is discovery + tail, configurable via config/env;
* with a zero verification deadline NO live verifier call starts, even
  though the media wall is wide open and candidates need verification
  (the control with no deadline does call the verifier);
* with a short verification deadline and a slow verifier, the
  sequential inline tail stops at the deadline instead of running one
  round trip per scene (wall time and call count both bounded).
"""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services.media_resolution_coordinator import resolve_storyboard_media_v2
from services.youtube_discovery_budget import (
    DEFAULT_MEDIA_TAIL_SECONDS,
    media_tail_seconds,
    total_media_budget_seconds,
)


# ---------------------------------------------------------------------------
# Total media budget: discovery budget + post-discovery tail
# ---------------------------------------------------------------------------


def test_total_media_budget_is_discovery_plus_tail():
    assert total_media_budget_seconds(292.0) == 292.0 + DEFAULT_MEDIA_TAIL_SECONDS
    assert total_media_budget_seconds(300.0) == 420.0
    assert total_media_budget_seconds(1500.0) == 1620.0


def test_media_tail_is_configurable_via_config(monkeypatch):
    monkeypatch.delenv("VIDZEO_YT_MEDIA_TAIL_SECONDS", raising=False)
    assert media_tail_seconds({"yt_media_tail_seconds": 120}) == 120.0
    assert total_media_budget_seconds(292.0, config={"yt_media_tail_seconds": 120}) == 412.0


def test_media_tail_is_configurable_via_env(monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_MEDIA_TAIL_SECONDS", "300")
    assert media_tail_seconds({}) == 300.0
    assert total_media_budget_seconds(292.0) == 592.0


def test_media_tail_config_wins_over_env(monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_MEDIA_TAIL_SECONDS", "300")
    assert media_tail_seconds({"yt_media_tail_seconds": 60}) == 60.0


def test_media_tail_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_MEDIA_TAIL_SECONDS", "banana")
    assert media_tail_seconds({}) == DEFAULT_MEDIA_TAIL_SECONDS
    monkeypatch.setenv("VIDZEO_YT_MEDIA_TAIL_SECONDS", "-5")
    assert media_tail_seconds({}) == 0.0


def test_total_media_budget_invalid_discovery_falls_back():
    assert total_media_budget_seconds("nonsense") == 300.0 + DEFAULT_MEDIA_TAIL_SECONDS


# ---------------------------------------------------------------------------
# Coordinator: inline finalization tail respects the verification deadline
# ---------------------------------------------------------------------------

_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6300000002000100e5022a4b0000000049454e44ae426082"
)


def _quota_scene(index):
    # Strict quota scene (live vision required), no visual_requirement so
    # the deterministic hard gate stays lenient in the test.
    return {
        "scene_id": f"tail-{index}",
        "scene_index": index,
        "container_id": "gta",
        "text": "Grand Theft Auto VI gameplay in Vice City",
        "source_policy": {
            "youtube_quota": True,
            "visual_verification_required": True,
        },
    }


def _clip_candidate(tmp_path, index):
    path = tmp_path / f"clip-{index}.mp4"
    path.write_bytes(_PNG_1X1 + b"\x00" * 2048)
    return {
        "path": str(path),
        "source": "youtube",
        "candidate_id": f"youtube:TAILVID{index}",
        "video_id": f"TAILVID{index}",
        "title": "Grand Theft Auto VI gameplay in Vice City",
        "final_score": 90.0,
    }


def _resolve_tail(scenes, clips, verifier, **kwargs):
    """Primary lanes find nothing; the YouTube fallback lane returns one
    unverified clip per scene, so the per-scene loop must verify each
    inline — the exact sequential tail test 18 ran for ~22 min."""
    calls = {"count": 0, "starts": []}

    def counting_verifier(scene, candidate):
        calls["count"] += 1
        calls["starts"].append(time.monotonic())
        return verifier(scene, candidate)

    started = time.monotonic()
    result = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=lambda _scene, _cancel: [],
        web_lane=lambda _scene, _cancel: [],
        youtube_fallback_lane=lambda scene, _cancel: [
            clips[scene["scene_index"]]
        ],
        visual_verifier=counting_verifier,
        neutral_factory=lambda _scene: None,
        deadline_seconds=60,
        scene_budget_seconds=60,
        vision_reserve_seconds=0.5,
        adaptive_media_review=True,
        **kwargs,
    )
    return result, calls, time.monotonic() - started


def _accepting_verifier(_scene, _candidate):
    return {
        "accepted": True, "status": "visual_pass", "final_score": 90,
        "identity_score": 90, "action_score": 85, "context_score": 88,
        "visual_checked": True, "visual_available": True,
        "semantic_verified": True, "reason": "matches the scene",
    }


def test_zero_verification_deadline_starts_no_live_vision(tmp_path):
    """Verification deadline 0: the review gate and every inline check
    are skipped even though the 60s media wall is wide open and every
    scene has an unverified quota clip waiting. Scenes still resolve
    terminally (fallback), with the skip recorded in the ledger."""
    scenes = [_quota_scene(i) for i in range(3)]
    clips = [_clip_candidate(tmp_path, i) for i in range(3)]
    result, calls, _elapsed = _resolve_tail(
        scenes, clips, _accepting_verifier, verification_deadline_seconds=0.0
    )
    assert calls["count"] == 0
    assert set(result) == {0, 1, 2}
    for index in range(3):
        assert result[index].status in {
            "accepted", "resolved", "fallback", "unverified", "degraded",
        }
        assert any(
            attempt.get("status") == "skipped_after_media_deadline"
            for attempt in result[index].attempts
        )


def test_no_verification_deadline_calls_the_verifier(tmp_path):
    """Control for the test above: without the deadline the same setup
    DOES spend live vision calls on the quota clips."""
    scenes = [_quota_scene(i) for i in range(2)]
    clips = [_clip_candidate(tmp_path, i) for i in range(2)]
    result, calls, _elapsed = _resolve_tail(scenes, clips, _accepting_verifier)
    assert calls["count"] >= 1


def test_short_verification_deadline_bounds_the_sequential_tail(tmp_path):
    """Eight quota scenes, one slow (1.0s) vision call each, all inline
    and sequential in the per-scene loop. Unbounded that tail is ~10s;
    with a 1.5s verification deadline it must stop starting new calls
    at the deadline (a call already in flight runs out, then the rest
    are skipped), so wall time and call count stay bounded."""
    scene_count = 8
    scenes = [_quota_scene(i) for i in range(scene_count)]
    clips = [_clip_candidate(tmp_path, i) for i in range(scene_count)]

    def slow_verifier(scene, candidate):
        time.sleep(1.0)
        return _accepting_verifier(scene, candidate)

    result, calls, elapsed = _resolve_tail(
        scenes, clips, slow_verifier, verification_deadline_seconds=1.5
    )
    # At most the calls that started before the 1.5s deadline: the first
    # (~0.2s in) and the second (~1.2s in). The remaining six must be
    # skipped, not verified one by one. (Elapsed also covers the
    # non-vision tail work — retention/rescue — which stays put.)
    assert calls["count"] <= 3
    assert elapsed < 6.0
    assert set(result) == set(range(scene_count))
    assert any(
        attempt.get("status") == "skipped_after_media_deadline"
        for index in range(scene_count)
        for attempt in result[index].attempts
    )


def test_short_verification_deadline_control_takes_the_full_tail(tmp_path):
    """Control for the test above: without the deadline the same slow
    verifier is called once per scene, sequentially."""
    scene_count = 8
    scenes = [_quota_scene(i) for i in range(scene_count)]
    clips = [_clip_candidate(tmp_path, i) for i in range(scene_count)]

    def slow_verifier(scene, candidate):
        time.sleep(1.0)
        return _accepting_verifier(scene, candidate)

    _result, calls, _elapsed = _resolve_tail(scenes, clips, slow_verifier)
    assert calls["count"] == scene_count
