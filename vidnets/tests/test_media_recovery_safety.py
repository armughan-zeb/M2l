"""Recovery must preserve the agent host and give each query wave a turn."""
import sys
from types import SimpleNamespace

import pytest

from services.pipeline_v2_media_adapter import (
    _web_recovery_hard_cap_seconds,
    _web_recovery_per_scene_seconds,
    _web_recovery_stage_seconds,
)
from services import render_resource_manager as resources


def test_memory_cleanup_never_enumerates_or_closes_user_apps(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("A render must not scan or terminate user applications")
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(process_iter=forbidden))
    result = resources._close_visible_user_apps_until_headroom(2560)
    assert result["apps_requested_to_close"] == []
    assert result["apps_force_terminated"] == []
    assert result["skipped_reason"] == "user_apps_are_not_owned_render_resources"


def test_recovery_budget_scales_with_scene_count_and_stays_bounded(monkeypatch):
    # The old fixed 180s ceiling cut a 121-scene job off after ~24 scenes.
    # The recovery wall is now 12s per scene (completion-scaled), floored at
    # 90s and capped at 1800s, matching the resolver's own scene-scaled
    # default so the explicit value and the resolver default agree.
    monkeypatch.delenv("VIDZEO_WEB_RECOVERY_PER_SCENE_SECONDS", raising=False)
    monkeypatch.delenv("VIDZEO_WEB_RECOVERY_HARD_CAP_SECONDS", raising=False)
    assert _web_recovery_stage_seconds(1, 12) == 90
    assert _web_recovery_stage_seconds(2, 12) == 90
    assert _web_recovery_stage_seconds(48, 12) == 576
    assert _web_recovery_stage_seconds(48, 6) == 576
    assert _web_recovery_stage_seconds(121, 12) == 1452
    assert _web_recovery_stage_seconds(1000, 1) == 1800
    assert _web_recovery_stage_seconds(500, 12) == 1800


def test_recovery_wall_respects_the_delivery_budget_allowance(monkeypatch):
    # ``configured`` is the delivery-budget allowance: a ceiling, not a
    # target. The wall never spends the render reserve.
    monkeypatch.delenv("VIDZEO_WEB_RECOVERY_PER_SCENE_SECONDS", raising=False)
    monkeypatch.delenv("VIDZEO_WEB_RECOVERY_HARD_CAP_SECONDS", raising=False)
    assert _web_recovery_stage_seconds(48, 12, 90) == 90
    assert _web_recovery_stage_seconds(121, 12, 300) == 300
    assert _web_recovery_stage_seconds(121, 12, 60) == 60
    assert _web_recovery_stage_seconds(48, 12, 9999) == 576
    assert _web_recovery_stage_seconds(48, 12, "invalid") == 576
    assert _web_recovery_stage_seconds(48, 12, 0) == 576


def test_recovery_wall_scaling_is_env_tunable(monkeypatch):
    monkeypatch.setenv("VIDZEO_WEB_RECOVERY_PER_SCENE_SECONDS", "6")
    monkeypatch.delenv("VIDZEO_WEB_RECOVERY_HARD_CAP_SECONDS", raising=False)
    assert _web_recovery_per_scene_seconds() == 6.0
    assert _web_recovery_stage_seconds(121, 12) == 726
    monkeypatch.setenv("VIDZEO_WEB_RECOVERY_PER_SCENE_SECONDS", "999")
    assert _web_recovery_per_scene_seconds() == 30.0
    monkeypatch.setenv("VIDZEO_WEB_RECOVERY_PER_SCENE_SECONDS", "invalid")
    assert _web_recovery_per_scene_seconds() == 12.0
    monkeypatch.delenv("VIDZEO_WEB_RECOVERY_PER_SCENE_SECONDS", raising=False)
    monkeypatch.setenv("VIDZEO_WEB_RECOVERY_HARD_CAP_SECONDS", "600")
    assert _web_recovery_hard_cap_seconds() == 600.0
    assert _web_recovery_stage_seconds(121, 12) == 600
    monkeypatch.setenv("VIDZEO_WEB_RECOVERY_HARD_CAP_SECONDS", "60")
    assert _web_recovery_hard_cap_seconds() == 180.0
