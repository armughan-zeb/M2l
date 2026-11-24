"""YouTube branch walls removed (Nov 17, 2026, user order).

A 900s branch wall killed the GTA VI 1-min test's clip worker while it
was still producing clips (4 clips in ~13 min through a proxy that
serialises transfers), with ~2000s of coordinator budget left; the
recovery stage's 360s cap did the same. The delivery budget (with render
reserve) is the only bound now. The same run also proved the printed
"[yt] attempt audit: {}" was empty because the timeout/salvage path never
carried the worker's per-scene attempt rows — which also broke the
recovery lane's exclusion list, so stage 2 re-tried the same age-gated
video stage 1 had already failed.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

import app as server_app
# Import from the same module object app.py uses (services.* via the
# server/ path above), not server.services.*, or isinstance checks in
# get_delivery_budget fail on the duplicate module.
from services.delivery_budget import DeliveryBudget
from services.pipeline_v2_media_adapter import _youtube_recovery_excluded_ids
from services.youtube_pipeline_v2 import _youtube_format_retry_is_futile


class FakeClock:
    def __init__(self, value=100.0):
        self.value = float(value)

    def __call__(self):
        return self.value


def _budget(total_seconds=1800, reserve_seconds=330, clock_value=100.0):
    return DeliveryBudget.create(
        total_seconds=total_seconds,
        render_reserve_seconds=reserve_seconds,
        clock=FakeClock(clock_value),
    )


def test_youtube_only_wall_is_delivery_allowance_when_budget_has_time():
    budget = _budget()
    config = {"_delivery_budget": budget, "_render_reserve_seconds": 120}
    # Fresh job: 1800s budget, 120s reserve -> 1680s allowance beats the
    # old 900s branch cap.
    assert server_app._youtube_only_wall_without_branch_cap(config, 900.0) == 1680.0


def test_youtube_only_wall_keeps_configured_wall_when_allowance_is_smaller():
    # 900s elapsed: allowance is 1800 - 900 - 120 = 780s, below the
    # configured 900s wall. The helper returns the configured wall; the
    # delivery-budget clamp below it enforces the allowance.
    clock = FakeClock(100.0)
    budget = DeliveryBudget.create(
        total_seconds=1800, render_reserve_seconds=330, clock=clock,
    )
    clock.value = 1000.0
    config = {"_delivery_budget": budget, "_render_reserve_seconds": 120}
    assert server_app._youtube_only_wall_without_branch_cap(config, 900.0) == 900.0


def test_youtube_only_wall_unchanged_without_delivery_budget():
    assert server_app._youtube_only_wall_without_branch_cap({}, 900.0) == 900.0
    assert server_app._youtube_only_wall_without_branch_cap(None, 360.0) == 360.0


def test_load_yt_attempt_audit_from_disk(tmp_path):
    (tmp_path / "youtube_scene_001.attempts.json").write_text(
        json.dumps([{"video_id": "9NpPPCvj7m8", "status": "downloaded"}]),
        encoding="utf-8",
    )
    (tmp_path / "youtube_scene_003.attempts.json").write_text(
        json.dumps([{"video_id": "0ZY_bEuJdX4", "status": "download_failed"}]),
        encoding="utf-8",
    )
    (tmp_path / "youtube_scene_005.attempts.json").write_text(
        "not json", encoding="utf-8",
    )
    (tmp_path / "unrelated.json").write_text("[]", encoding="utf-8")
    attempts = server_app._load_yt_attempt_audit_from_disk(
        {"_run_temp_dir": str(tmp_path)}
    )
    assert attempts == {
        0: [{"video_id": "9NpPPCvj7m8", "status": "downloaded"}],
        2: [{"video_id": "0ZY_bEuJdX4", "status": "download_failed"}],
    }


def test_load_yt_attempt_audit_from_disk_empty(tmp_path):
    assert server_app._load_yt_attempt_audit_from_disk({"_run_temp_dir": str(tmp_path)}) == {}
    assert server_app._load_yt_attempt_audit_from_disk({}) == {}


def test_loaded_audit_feeds_recovery_exclusion():
    # The chain the empty audit broke: stage-1 attempts exclude the
    # failed video from the recovery stage's candidates.
    audit = {
        "attempts": {
            0: [{"video_id": "0ZY_bEuJdX4", "status": "download_failed"}],
            1: [{"video_id": "9NpPPCvj7m8", "status": "downloaded"}],
        }
    }
    excluded = _youtube_recovery_excluded_ids(
        audit,
        primary_indices=[0, 1, 2, 3, 4, 5, 6, 7],
        recovery_indices=[1, 2],
    )
    assert excluded == {"9NpPPCvj7m8"}


def test_format_retry_is_futile_only_for_age_gated_format_failures():
    assert _youtube_format_retry_is_futile(
        "ERROR: [youtube] 0ZY_bEuJdX4: Requested format is not available.",
        ["authentication", "provider_warning", "format_unavailable"],
    )
    assert not _youtube_format_retry_is_futile(
        "ERROR: [youtube] abc: Requested format is not available.",
        ["format_unavailable"],
    )
    assert not _youtube_format_retry_is_futile(
        "ERROR: [youtube] abc: Sign in to confirm your age.",
        ["authentication"],
    )
    assert not _youtube_format_retry_is_futile("", [])
