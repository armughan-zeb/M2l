"""Integration tests for the two-track YouTube discovery system.

Track A (scene-by-scene) is the existing pipeline; Track B (general
pool) is the new shared pool of vision-verified topic clips. These tests
prove the pool is claimed by quota scenes with no scene-specific clip,
that the reuse cap binds across scenes, and that a fatal ledger entry
still vetoes a pool clip (the pool must not launder a rejected video
back into the video).

The discovery budget itself (300s per minute of video, no cap) and the
pool builder are unit-tested in test_youtube_two_track_discovery.py.
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services.media_resolution_coordinator import resolve_storyboard_media_v2
from services.youtube_general_pool import GeneralPoolEntry, YouTubeGeneralPool

_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6300000002000100e5022a4b0000000049454e44ae426082"
)


def _padded_clip_file(tmp_path, name, size=2048):
    """A fake clip file big enough for the spare-pool size gate (>1024 B)."""
    path = tmp_path / name
    blob = _PNG_1X1 + b"\x00" * (size - len(_PNG_1X1))
    path.write_bytes(blob)
    return str(path)


def _quota_scene(index=0, text="Grand Theft Auto VI gameplay in Vice City",
                 container_id="gta"):
    return {
        "scene_id": f"gta-{index}",
        "scene_index": index,
        "container_id": container_id,
        "text": text,
        "domain_category": "gaming",
        "positive_subjects": ["Grand Theft Auto VI"],
        "forbidden_subjects": [],
        "source_policy": {"youtube_quota": True, "visual_verification_required": False},
        "visual_requirement": {
            "requirement_hash": f"req-{index}",
            "canonical_entity": "Vice City",
            "domain": "gaming",
        },
    }


def _pool_entry(path, video_id="POOLVID1", subjects=("GTA VI gameplay",),
                vision_score=80.0):
    return GeneralPoolEntry(
        video_id=video_id,
        title="GTA VI Vice City gameplay",
        channel="Rockstar Games",
        url=f"https://www.youtube.com/watch?v={video_id}",
        best_start=10.0,
        best_end=40.0,
        vision_score=vision_score,
        subjects=list(subjects),
        candidate_id=video_id,
        source_path=path,
    )


def _resolve_with_pool(scenes, pool, job_id=None, **kwargs):
    return resolve_storyboard_media_v2(
        scenes,
        youtube_lane=lambda _scene, _cancel: [],
        web_lane=lambda _scene, _cancel: [],
        visual_verifier=lambda _scene, _candidate: {
            "accepted": True, "status": "accepted", "final_score": 90,
            "identity_score": 90, "action_score": 85, "context_score": 88,
            "visual_checked": True, "visual_available": True,
            "semantic_verified": True, "reason": "matches the scene",
        },
        neutral_factory=lambda _scene: None,
        deadline_seconds=30,
        scene_budget_seconds=30,
        vision_reserve_seconds=0.5,
        job_id=job_id,
        youtube_general_pool=pool,
        **kwargs,
    )


def test_quota_scene_with_no_youtube_clip_claims_general_pool_clip(tmp_path):
    """A quota scene Track A could not resolve gets a vision-verified
    general-pool clip through quota retention, not a graphic."""
    clip = _padded_clip_file(tmp_path, "pool-clip.mp4")
    pool = YouTubeGeneralPool(reuse_cap=3)
    pool.add(_pool_entry(clip))

    result = _resolve_with_pool([_quota_scene(0)], pool)

    assert result[0].provider == "youtube"
    assert result[0].selected_path == clip
    assert result[0].resolution_kind == "youtube_quota_upgrade"
    assert any(
        attempt.get("general_pool") is True for attempt in result[0].attempts
    )
    assert result[0].visual_decision.get("source") == "general_pool"


def test_general_pool_reuse_cap_respected_across_scenes(tmp_path):
    """One pool clip covers up to reuse_cap scenes and no more (2 here).
    Quota scenes are non-adjacent (scenes 1 and 3 are not quota): the
    physical-asset diversity rule never replays one asset in two
    adjacent scenes, so the cap is measured across scenes 0, 2 and 4.
    The 3rd quota scene falls through instead of replaying the clip."""
    clip = _padded_clip_file(tmp_path, "pool-clip.mp4")
    pool = YouTubeGeneralPool(reuse_cap=2)
    pool.add(_pool_entry(clip))
    scenes = [_quota_scene(i, container_id=f"gta-{i}") for i in range(5)]
    for i in (1, 3):
        scenes[i]["source_policy"] = {}

    result = _resolve_with_pool(scenes, pool)

    assert result[0].selected_path == clip
    assert result[2].selected_path == clip
    assert result[4].selected_path == "", "3rd quota scene must not replay the capped clip"
    assert result[4].provider in {"neutral", "graphic"}


def test_general_pool_clip_never_replays_in_adjacent_scenes(tmp_path):
    """Boundary: the pool reuse cap allows up to 3 scenes, but the
    physical-asset diversity rule still forbids the same footage in two
    adjacent scenes (same rule as lane clips). Two adjacent quota scenes
    share one pool clip only on paper — the second keeps its neutral
    slot instead of showing the same footage back-to-back."""
    clip = _padded_clip_file(tmp_path, "pool-clip.mp4")
    pool = YouTubeGeneralPool(reuse_cap=3)
    pool.add(_pool_entry(clip))
    scenes = [_quota_scene(i, container_id=f"gta-{i}") for i in range(2)]

    result = _resolve_with_pool(scenes, pool)

    assert result[0].selected_path == clip
    assert result[1].selected_path == "", "adjacent scene must not replay the same clip"
    assert any(
        attempt.get("stage") == "physical_asset_diversity"
        for attempt in result[1].attempts
    )


def test_general_pool_clip_with_fatal_ledger_entry_is_vetoed(tmp_path):
    """A pool clip vision fatally rejected for this scene (filed in the
    job's verdict ledger) must NOT be minted by quota retention — the
    pool must not launder a rejected video back into the video."""
    from services.verdict_conflicts import record_late_fatal_reject

    clip = _padded_clip_file(tmp_path, "pool-clip.mp4")
    pool = YouTubeGeneralPool(reuse_cap=3)
    pool.add(_pool_entry(clip, video_id="POOLVID9"))
    job_id = "test-pool-veto-job"
    record_late_fatal_reject(tmp_path, {
        "request_id": "adapter-fatal:0:POOLVID9",
        "candidate_id": "youtube:POOLVID9",
        "video_id": "POOLVID9",
        "asset_sha256": None,
        "scene_index": 0,
        "requirement_hash": "req-0",
        "job_id": job_id,
        "verdict": {
            "candidate_id": "youtube:POOLVID9",
            "identity_score": 15, "action_score": 20, "context_score": 25,
            "quality": 55, "final_score": 20, "entity_conflict": True,
            "fatal_mismatch": True, "domain_match": False, "scene_match": False,
            "reason": "Wrong subject for this scene.",
            "accepted": False, "status": "rejected",
            "visual_checked": True, "visual_available": True,
        },
        "received_at": "2026-11-21T00:00:00+00:00",
        "winner": None,
    })

    with mock.patch.dict(
        os.environ, {"VIDNETS_AGENT_BRIDGE_DIR": str(tmp_path)}, clear=False
    ):
        result = _resolve_with_pool([_quota_scene(0)], pool, job_id=job_id)

    assert result[0].selected_path != clip
    assert result[0].provider in {"neutral", "graphic", "web"}
    assert any(
        attempt.get("status") == "prior_fatal_mismatch_rejection"
        for attempt in result[0].attempts
    )


def test_resolve_and_build_media_plan_passes_pool_through(tmp_path):
    """The adapter's plan builder forwards the pool to the coordinator,
    so a pool-resolved scene lands in the media plan."""
    from services.pipeline_v2_media_adapter import resolve_and_build_media_plan

    clip = _padded_clip_file(tmp_path, "pool-clip.mp4")
    pool = YouTubeGeneralPool(reuse_cap=3)
    pool.add(_pool_entry(clip))

    media_plan, resolutions = resolve_and_build_media_plan(
        [_quota_scene(0)],
        youtube_lane=lambda _scene, _cancel: [],
        web_lane=lambda _scene, _cancel: [],
        stock_lane=None,
        visual_verifier=lambda _scene, _candidate: {
            "accepted": True, "status": "accepted", "final_score": 90,
            "visual_checked": True, "visual_available": True,
            "semantic_verified": True, "reason": "matches the scene",
        },
        neutral_factory=lambda _scene: None,
        deadline_seconds=30,
        youtube_general_pool=pool,
        youtube_discovery_budget_seconds=300.0,
    )

    assert resolutions[0].provider == "youtube"
    assert resolutions[0].selected_path == clip
    assert media_plan["media_paths"][0] == clip
