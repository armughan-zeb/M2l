"""Recovery must preserve the agent host and give each query wave a turn."""
import sys
from types import SimpleNamespace

from services.pipeline_v2_media_adapter import _web_recovery_stage_seconds
from services import render_resource_manager as resources


def test_memory_cleanup_never_enumerates_or_closes_user_apps(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("A render must not scan or terminate user applications")
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(process_iter=forbidden))
    result = resources._close_visible_user_apps_until_headroom(2560)
    assert result["apps_requested_to_close"] == []
    assert result["apps_force_terminated"] == []
    assert result["skipped_reason"] == "user_apps_are_not_owned_render_resources"


def test_recovery_budget_scales_by_concurrent_waves_and_stays_bounded():
    assert _web_recovery_stage_seconds(1, 12) == 90
    assert _web_recovery_stage_seconds(48, 12) == 252
    assert _web_recovery_stage_seconds(48, 6) == 300
    assert _web_recovery_stage_seconds(1000, 1) == 300
    assert _web_recovery_stage_seconds(48, 12, 90) == 90
    assert _web_recovery_stage_seconds(48, 12, 9999) == 300
    assert _web_recovery_stage_seconds(48, 12, "invalid") == 252
    assert _web_recovery_stage_seconds(48, 12, 0) == 252
