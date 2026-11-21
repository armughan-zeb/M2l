"""General-pool wall regression (GTA test 18, job 2451ee04).

Test 18's Track B pool verified 0 clips. Root cause: the Mixed Media
branch wall is scaled by scene count (``mixed_youtube_time_budget``), so
the 4-query pool build got a 240s wall — shorter than one fresh
verification cycle (search alone took ~68s, the transcript stage has a
120s deadline, then probe/download hits throttled yt-dlp timeouts).
The adapter had configured the pool wall explicitly as discovery budget
+ download drain (292s + 120s = 412s for a 1-min video), but
``_run_youtube_clips_branch`` discarded it and imposed the 240s
transfer-wave wall, killing the pool child before any clip resolved
(no downloads ready, no files on disk, no audit ledger).

These tests pin:
* the pool wall floor (discovery budget + drain) for a pool build,
* Track A's wall unchanged when the pool flag is absent,
* the exact test-18 query set (4 broad topic queries),
* ``build_general_pool`` banking only attempt rows with a live vision
  ``accepted`` verdict, and diagnosing (not silently swallowing) a
  timeout/salvage audit that carries no ``attempts`` ledger.
"""
import pytest

from app import _mixed_youtube_branch_wall_cap_seconds
from server.services.youtube_general_pool import (
    build_general_pool,
    build_general_pool_queries,
)


def _mixed_config(**overrides):
    config = {"image_source": "Mixed Media", "_youtube_quota_preselected": True}
    config.update(overrides)
    return config


def test_pool_wall_floor_is_discovery_budget_plus_drain():
    """Test 18 regression: a 4-query pool build must not get the 240s
    transfer-wave wall; it keeps the adapter-configured 292s + 120s."""
    config = _mixed_config(
        _youtube_general_pool_build=True,
        yt_soft_timeout_seconds=292,
        yt_hard_timeout_seconds=412,
    )
    assert _mixed_youtube_branch_wall_cap_seconds(config, 4) == 412.0


def test_pool_wall_floor_falls_back_to_soft_timeout_plus_drain():
    """Without an explicit hard timeout, soft timeout + 120s drain."""
    config = _mixed_config(
        _youtube_general_pool_build=True,
        yt_soft_timeout_seconds=292,
    )
    assert _mixed_youtube_branch_wall_cap_seconds(config, 4) == 412.0


def test_primary_lane_wall_unchanged_without_pool_flag():
    """No behaviour change for Track A: 4 scenes still cap at 240s,
    11 scenes at 343s (the test-18 primary wall)."""
    assert _mixed_youtube_branch_wall_cap_seconds(_mixed_config(), 4) == 240.0
    assert _mixed_youtube_branch_wall_cap_seconds(_mixed_config(), 11) == 343.0


def test_pool_wall_floor_never_shrinks_a_longer_transfer_wall():
    """When the transfer-wave wall already exceeds the pool floor
    (many scenes), the larger wall stands."""
    config = _mixed_config(
        _youtube_general_pool_build=True,
        yt_soft_timeout_seconds=292,
        yt_hard_timeout_seconds=412,
    )
    assert _mixed_youtube_branch_wall_cap_seconds(config, 11) == 412.0


def test_test18_pool_queries_are_the_four_broad_topic_queries():
    """Test 18 inputs: single-topic video, every scene subject is the
    anchor itself, so the subject loop adds nothing and the pool runs
    exactly these 4 queries."""
    queries = build_general_pool_queries(
        "Grand Theft Auto VI: Return to Vice City",
        topic_anchor="Grand Theft Auto VI",
        scene_subjects=["Grand Theft Auto VI"] * 13,
    )
    assert queries == [
        "Grand Theft Auto VI gameplay",
        "Grand Theft Auto VI official gameplay",
        "Grand Theft Auto VI trailer",
        "Grand Theft Auto VI",
    ]


class _FakeResults(list):
    """A branch result: iterable paths plus an ``.audit`` ledger."""

    def __init__(self, paths, audit):
        super().__init__(paths)
        self.audit = audit


def _accepted_row(**overrides):
    row = {
        "video_id": "ByAS0Ak3-qA",
        "title": "Grand Theft Auto VI Gameplay Walkthrough Part 1 [GTA 6]",
        "channel": "GAMERALISIMUS",
        "source_url": "https://www.youtube.com/watch?v=ByAS0Ak3-qA",
        "timestamp": 1679.0,
        "clip_duration": 5.0,
        "visual_score": 68.0,
        "visual_status": "accepted",
    }
    row.update(overrides)
    return row


def _runner(results):
    def run(scene_texts, durations, config, title, script, **kwargs):
        assert config.get("_youtube_general_pool_build") is True
        return results

    return run


def test_build_general_pool_banks_accepted_attempt_row(tmp_path):
    pool = build_general_pool(
        _runner(_FakeResults(
            [str(tmp_path / "clip.mp4")],
            {"attempts": {"0": [_accepted_row()]}},
        )),
        queries=["Grand Theft Auto VI gameplay"],
        title="Grand Theft Auto VI",
        script="",
        run_config={},
        output_dir=str(tmp_path),
    )
    assert len(pool) == 1
    entry = pool.entries()[0]
    assert entry.video_id == "ByAS0Ak3-qA"
    assert entry.best_start == 1679.0
    assert entry.vision_score == 68.0


def test_build_general_pool_banks_branch_verified_download(tmp_path):
    """Live-repro regression (Nov 21 2026): the real branch run resolved
    4/4 clips, but every attempt row carried status='downloaded' and NO
    visual_status — transport vision is intentionally disabled in the
    child worker (youtube_contact_only), so the old visual_status-only
    rule banked 0. A downloaded row with a real cut path is the branch's
    own verification (the adapter banks exactly these rows for Track A),
    so it becomes a pool entry; vision_score falls back to metadata."""
    pool = build_general_pool(
        _runner(_FakeResults(
            [str(tmp_path / "clip.mp4")],
            {"attempts": {"0": [
                {
                    "video_id": "kow3wVkFXP0",
                    "title": "GTA 6 Gameplay Trailer 4K (No Commentary)",
                    "channel": "Punish",
                    "source_url": "https://www.youtube.com/watch?v=kow3wVkFXP0",
                    "timestamp": 284.0,
                    "clip_duration": 5.0,
                    "metadata_score": 360.0,
                    "status": "downloaded",
                    "resolution_layer": "cached",
                    "path": str(tmp_path / "clip.mp4"),
                },
            ]}},
        )),
        queries=["Grand Theft Auto VI gameplay"],
        title="Grand Theft Auto VI",
        script="",
        run_config={},
        output_dir=str(tmp_path),
    )
    assert len(pool) == 1
    entry = pool.entries()[0]
    assert entry.video_id == "kow3wVkFXP0"
    assert entry.best_start == 284.0
    assert entry.vision_score == 360.0


def test_build_general_pool_prefers_live_vision_accept_over_download(tmp_path):
    """When a row DOES carry a live vision accept, it wins over a plain
    downloaded row for the same pseudo-scene."""
    pool = build_general_pool(
        _runner(_FakeResults(
            [str(tmp_path / "clip.mp4")],
            {"attempts": {"0": [
                _accepted_row(video_id="downloaded-only", visual_status="",
                              metadata_score=500.0, status="downloaded",
                              path=str(tmp_path / "clip.mp4")),
                _accepted_row(video_id="vision-winner", visual_score=68.0),
            ]}},
        )),
        queries=["Grand Theft Auto VI gameplay"],
        title="Grand Theft Auto VI",
        script="",
        run_config={},
        output_dir=str(tmp_path),
    )
    assert len(pool) == 1
    assert pool.entries()[0].video_id == "vision-winner"


def test_build_general_pool_drops_downloaded_row_without_path(tmp_path):
    """A 'downloaded' row with no cut behind it (no selected /
    cleaned_path / path) is not banked — the entry would point at
    nothing."""
    pool = build_general_pool(
        _runner(_FakeResults(
            [str(tmp_path / "clip.mp4")],
            {"attempts": {"0": [_accepted_row(visual_status="",
                                              status="downloaded")]}},
        )),
        queries=["Grand Theft Auto VI gameplay"],
        title="Grand Theft Auto VI",
        script="",
        run_config={},
        output_dir=str(tmp_path),
    )
    assert len(pool) == 0


def test_build_general_pool_drops_motion_rejected_row(tmp_path):
    """The repro's scene 4 had a first attempt rejected for motion /
    quality and a second downloaded one; only the downloaded row banks,
    never the rejected one."""
    pool = build_general_pool(
        _runner(_FakeResults(
            [str(tmp_path / "clip.mp4")],
            {"attempts": {"0": [
                {
                    "video_id": "ByAS0Ak3-qA",
                    "title": "Grand Theft Auto VI Gameplay Walkthrough Part 1 [GTA 6]",
                    "timestamp": 1642.0,
                    "clip_duration": 5.0,
                    "metadata_score": 322.0,
                    "status": "motion_or_quality_rejected",
                    "path": str(tmp_path / "rejected.mp4"),
                },
                {
                    "video_id": "F39fE1AwOyc",
                    "title": "Grand Theft Auto VI Gameplay Walkthrough Part 3 [GTA 6]",
                    "timestamp": 211.0,
                    "clip_duration": 5.0,
                    "metadata_score": 322.0,
                    "status": "downloaded",
                    "path": str(tmp_path / "clip.mp4"),
                },
            ]}},
        )),
        queries=["Grand Theft Auto VI"],
        title="Grand Theft Auto VI",
        script="",
        run_config={},
        output_dir=str(tmp_path),
    )
    assert len(pool) == 1
    assert pool.entries()[0].video_id == "F39fE1AwOyc"


def test_build_general_pool_rejects_rows_without_live_accept(capsys, tmp_path):
    """A downloaded row whose vision verdict is 'rejected' (or missing)
    is never banked as verified — and the drop is logged per query."""
    pool = build_general_pool(
        _runner(_FakeResults(
            [str(tmp_path / "clip.mp4")],
            {"attempts": {"0": [_accepted_row(visual_status="rejected")]}},
        )),
        queries=["Grand Theft Auto VI gameplay"],
        title="Grand Theft Auto VI",
        script="",
        run_config={},
        output_dir=str(tmp_path),
    )
    assert len(pool) == 0
    assert "no accepted clip" in capsys.readouterr().out


def test_build_general_pool_diagnoses_salvage_audit_without_attempts(capsys, tmp_path):
    """Timeout path: the branch returns a salvage result whose audit has
    cut_audit / retrieval_visual_decisions but NO 'attempts' ledger, so
    no vision verdict exists to bank. The pool stays empty (correct —
    salvage cuts are not vision-verified) but the log must say the
    ledger is missing instead of silently reporting '0 verified'."""
    pool = build_general_pool(
        _runner(_FakeResults(
            [str(tmp_path / "clip.mp4")],
            {
                "requested": 1,
                "resolved": 1,
                "transport_status": "degraded",
                "failure_reason": "outer_wall_salvage",
                "cut_audit": [{"status": "accepted"}],
                "retrieval_visual_decisions": {
                    "0": {"accepted": False, "status": "visual_verification_pending"}
                },
            },
        )),
        queries=["Grand Theft Auto VI gameplay"],
        title="Grand Theft Auto VI",
        script="",
        run_config={},
        output_dir=str(tmp_path),
    )
    assert len(pool) == 0
    out = capsys.readouterr().out
    assert "no 'attempts' ledger" in out


def test_build_general_pool_reports_runner_failure(capsys, tmp_path):
    """A runner that raises used to vanish silently (empty pool, no
    message). The failure type is now logged; Track A still proceeds."""
    def failing_runner(*args, **kwargs):
        raise RuntimeError("YouTube worker cancelled")

    pool = build_general_pool(
        failing_runner,
        queries=["Grand Theft Auto VI gameplay"],
        title="Grand Theft Auto VI",
        script="",
        run_config={},
        output_dir=str(tmp_path),
    )
    assert len(pool) == 0
    assert "pool build failed: RuntimeError" in capsys.readouterr().out
