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
    agent floor to 1800 + 100*35 = 5300s, while the same script without
    an agent provider keeps the audio-formula total (~2339s).
    """
    monkeypatch.delenv("VIDZEO_VIDEO_DELIVERY_BUDGET_SECONDS", raising=False)
    monkeypatch.delenv("VIDZEO_RENDER_RESERVE_SECONDS", raising=False)

    agent = install_delivery_budget(
        {"script": ("word " * 1600).strip(), "llm_provider": "external_agent"},
        clock=FakeClock(),
    )
    assert agent.total_seconds == 5300.0

    cooper_like = install_delivery_budget(
        {"script": ("word " * 2243).strip(), "llm_provider": "external_agent"},
        clock=FakeClock(),
    )
    assert 6000.0 < cooper_like.total_seconds <= 7200.0

    plain = install_delivery_budget(
        {"script": ("word " * 1600).strip()},
        clock=FakeClock(),
    )
    assert 2300.0 < plain.total_seconds < 2400.0


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
