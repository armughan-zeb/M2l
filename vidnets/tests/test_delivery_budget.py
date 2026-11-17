from server.services.delivery_budget import DeliveryBudget, install_delivery_budget


class FakeClock:
    def __init__(self, value=100.0):
        self.value = float(value)

    def __call__(self):
        return self.value


def test_budget_is_hard_capped_at_thirty_minutes_for_long_form():
    clock = FakeClock()
    budget = DeliveryBudget.create(
        total_seconds=7200,
        render_reserve_seconds=300,
        clock=clock,
    )

    assert budget.total_seconds == 1800
    assert budget.deadline_at == 1900


def test_stage_allowance_preserves_render_reserve():
    clock = FakeClock()
    budget = DeliveryBudget.create(
        total_seconds=900,
        render_reserve_seconds=300,
        clock=clock,
    )
    clock.value += 250

    assert budget.allowance(500, reserve_after_seconds=300) == 350
    clock.value += 350
    assert budget.allowance(30, reserve_after_seconds=300) == 0
    assert not budget.optional_stage_allowed(
        reserve_after_seconds=300,
        minimum_useful_seconds=1,
    )


def test_install_reuses_one_clock_across_config_copies(monkeypatch):
    monkeypatch.setenv("VIDZEO_VIDEO_DELIVERY_BUDGET_SECONDS", "840")
    monkeypatch.setenv("VIDZEO_RENDER_RESERVE_SECONDS", "240")
    clock = FakeClock()
    config = {}

    budget = install_delivery_budget(config, clock=clock)
    copied = dict(config)
    copied_budget = install_delivery_budget(copied, clock=clock)

    assert copied_budget is budget
    assert budget.total_seconds == 840
    assert budget.render_reserve_seconds == 240
    assert config["_delivery_deadline_at"] == copied["_delivery_deadline_at"]


def test_install_without_explicit_limit_uses_safe_default(monkeypatch):
    monkeypatch.delenv("VIDZEO_VIDEO_DELIVERY_BUDGET_SECONDS", raising=False)
    monkeypatch.delenv("VIDZEO_RENDER_RESERVE_SECONDS", raising=False)
    config = {}

    budget = install_delivery_budget(config, clock=FakeClock())

    assert budget.total_seconds == 900
    assert budget.render_reserve_seconds == 330
    assert config["_delivery_deadline_at"] == 100 + 900


def test_invalid_and_non_finite_limits_fall_back_to_safe_default(monkeypatch):
    monkeypatch.delenv("VIDZEO_RENDER_RESERVE_SECONDS", raising=False)
    for value in ("not-a-number", "nan", "inf", "0", "-5"):
        monkeypatch.setenv("VIDZEO_VIDEO_DELIVERY_BUDGET_SECONDS", value)
        budget = install_delivery_budget({}, clock=FakeClock())
        assert budget.total_seconds == 900


def test_production_worst_case_stage_chain_keeps_safety_margin():
    """The configured slow path still reaches delivery before the 15m wall."""
    clock = FakeClock(0)
    budget = DeliveryBudget.create(
        total_seconds=900,
        render_reserve_seconds=330,
        clock=clock,
    )

    planning = budget.allowance(50, reserve_after_seconds=330)
    clock.value += planning
    media = budget.allowance(360, reserve_after_seconds=330)
    clock.value += media
    final_rescue = budget.allowance(180, reserve_after_seconds=330)
    clock.value += final_rescue
    semantic = budget.allowance(210, reserve_after_seconds=90)
    clock.value += semantic
    straight_recovery = budget.allowance(85, reserve_after_seconds=5)
    clock.value += straight_recovery

    assert (planning, media, final_rescue, semantic, straight_recovery) == (
        50,
        360,
        160,
        210,
        85,
    )
    assert budget.elapsed_seconds() == 865
    assert budget.remaining_seconds() == 35


def test_agent_job_budget_scales_with_scene_count(monkeypatch):
    """Cooper got 3037s from the audio formula and its tail fell back.

    Agent preparation is per-scene inbox round trips, so the budget must
    scale with scene count: a 1600-word script (~100 scenes) lifts the
    agent floor to 2400 + 100*90 = 11400s, while the same script without
    an agent provider keeps the audio-formula total (~2339s). Cooper
    (2243 words, ~140 scenes) would need ~15017s and is capped at 14400s.
    """
    monkeypatch.delenv("VIDZEO_VIDEO_DELIVERY_BUDGET_SECONDS", raising=False)
    monkeypatch.delenv("VIDZEO_RENDER_RESERVE_SECONDS", raising=False)

    agent = install_delivery_budget(
        {"script": ("word " * 1600).strip(), "llm_provider": "external_agent"},
        clock=FakeClock(),
    )
    assert agent.total_seconds == 11400.0

    cooper_like = install_delivery_budget(
        {"script": ("word " * 2243).strip(), "llm_provider": "external_agent"},
        clock=FakeClock(),
    )
    assert cooper_like.total_seconds == 14400.0

    plain = install_delivery_budget(
        {"script": ("word " * 1600).strip()},
        clock=FakeClock(),
    )
    assert 2300.0 < plain.total_seconds < 2400.0


def test_wright_replay_progressing_job_survives_old_budget(monkeypatch):
    """Wright Brothers (270 words, 14 scenes) died at 2419s under the old
    1800 + scenes*35 formula (~2395s). A media stage that keeps progressing
    must NOT be cancelled when the old budget would have fired; only a true
    stall (no progress for the grace window) lets the deadline expire."""
    monkeypatch.delenv("VIDZEO_VIDEO_DELIVERY_BUDGET_SECONDS", raising=False)
    monkeypatch.delenv("VIDZEO_RENDER_RESERVE_SECONDS", raising=False)
    clock = FakeClock(0)
    budget = install_delivery_budget(
        {"script": ("word " * 270).strip(), "llm_provider": "external_agent"},
        clock=clock,
    )
    assert budget.total_seconds == 3918.75

    # Genuine progress every 300s (a scene resolving, a fraction advancing).
    for step in range(14):
        clock.value += 300
        budget.note_progress()
        assert budget.remaining_seconds() > 0, f"killed at t={clock.value}"
    # t=4200: past the initial 3918s total AND the old ~2395s wall, still
    # alive because progress kept arriving.
    assert clock.value == 4200
    assert budget.remaining_seconds() > 0

    # Now go quiet: after a full grace window (1200s) with no progress the
    # deadline must expire — a truly hung job is still caught.
    clock.value += 1200
    assert budget.remaining_seconds() == 0


def test_progress_extension_is_capped_by_absolute_cap():
    clock = FakeClock(0)
    budget = DeliveryBudget.create(
        total_seconds=1800,
        render_reserve_seconds=300,
        absolute_cap_seconds=2000,
        clock=clock,
    )
    for _ in range(20):
        clock.value += 300
        budget.note_progress()
    assert budget.deadline_at == 2000.0
    assert budget.remaining_seconds() == 0


def test_note_progress_extends_deadline_by_stall_grace():
    clock = FakeClock(100)
    budget = DeliveryBudget.create(
        total_seconds=900, render_reserve_seconds=300, clock=clock
    )
    assert budget.deadline_at == 1000.0
    clock.value = 500
    assert budget.note_progress() == 1700.0
    assert budget.remaining_seconds() == 1200.0
    assert budget.last_progress_at == 500.0


def test_remaining_seconds_reads_live_budget_deadline():
    """job_context must see the extended deadline, not the start snapshot."""
    from services.job_context import job_scope, remaining_seconds

    clock = FakeClock(100)
    budget = DeliveryBudget.create(
        total_seconds=900, render_reserve_seconds=300, clock=clock
    )
    import time as _time

    with job_scope(
        {
            "delivery_budget": budget,
            "deadline_at": _time.monotonic() + 900,
        }
    ):
        assert remaining_seconds() == 900.0
        clock.value = 800
        budget.note_progress()
        # The snapshot said 100s left; the live budget says 1200s.
        assert remaining_seconds() == 1200.0
        # A tighter phase deadline set by phase_scope still binds.
        with job_scope({"phase_deadline_at": _time.monotonic() + 30}):
            assert remaining_seconds() <= 31.0


def test_remotion_uses_remaining_time_without_unreachable_legacy_reserve():
    from server.services.delivery_budget import render_stage_allowance
    clock = FakeClock(0)
    budget = DeliveryBudget.create(total_seconds=600, render_reserve_seconds=150, clock=clock)
    clock.value = 510
    assert render_stage_allowance(budget, backend='remotion') == 85
    assert render_stage_allowance(budget, backend='react') == 85
    assert render_stage_allowance(budget, backend='ffmpeg') == 0
    clock.value = 601
    assert render_stage_allowance(budget, backend='remotion') == 0
