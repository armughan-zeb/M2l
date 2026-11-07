"""Shared wall-clock budget for production video delivery.

Preparation stages share a deadline. Once local rendering begins, progress and
resource supervision replace the elapsed-time cutoff. Independent stage timeouts
can otherwise add up to an hour even though every individual timeout looks
reasonable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import math
import time
from typing import Any, Callable, MutableMapping, Optional


Clock = Callable[[], float]


def render_stage_allowance(budget, *, backend: str, legacy_recovery_reserve: float = 150.0) -> float:
    """Reserve only work that can actually follow the selected compositor."""
    remotion = str(backend or '').strip().lower() in {'remotion', 'react'}
    tail = 5.0 if remotion else max(0.0, float(legacy_recovery_reserve)) + 5.0
    return budget.allowance(None, reserve_after_seconds=tail)


def _number(value: Any, default: Optional[float]) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) and parsed > 0 else default


@dataclass
class DeliveryBudget:
    """One monotonic deadline shared by planning, retrieval and rendering."""

    total_seconds: float
    render_reserve_seconds: float
    started_at: float
    deadline_at: float
    clock: Clock = field(default=time.monotonic, repr=False, compare=False)
    render_started: bool = False

    def begin_render_phase(self) -> None:
        """Local rendering uses progress/stall supervision, not a delivery wall."""
        if not self.render_started and self.remaining_seconds() <= 0:
            raise TimeoutError('Preparation deadline expired before rendering started.')
        self.render_started = True

    @classmethod
    def create(
        cls,
        *,
        total_seconds: float = 900.0,
        render_reserve_seconds: float = 330.0,
        max_cap_seconds: Optional[float] = None,
        clock: Clock = time.monotonic,
    ) -> "DeliveryBudget":
        # Historical contract: maximum fifteen minutes.  Long-form jobs (75+
        # scenes with semantic editing) need up to ~30 minutes once media
        # retrieval, the semantic render and the straight-recovery reserve are
        # accounted for, so configured production jobs may expand to 1800s;
        # the default stays 900s. Long-form documentary jobs (30+ minutes) can
        # expand up to 7200s via max_cap_seconds or environment.
        cap = 1800.0 if max_cap_seconds is None else max(1.0, float(max_cap_seconds))
        total = max(1.0, min(cap, float(total_seconds)))
        reserve = max(0.0, min(float(render_reserve_seconds), total * 0.60))
        started = float(clock())
        return cls(
            total_seconds=total,
            render_reserve_seconds=reserve,
            started_at=started,
            deadline_at=started + total,
            clock=clock,
        )

    def elapsed_seconds(self) -> float:
        return max(0.0, float(self.clock()) - self.started_at)

    def remaining_seconds(self) -> float:
        if self.render_started:
            return math.inf
        return max(0.0, self.deadline_at - float(self.clock()))

    def allowance(
        self,
        requested_seconds: Optional[float],
        *,
        reserve_after_seconds: float = 0.0,
    ) -> float:
        """Return how much wall time a stage may consume right now.

        Zero means the stage must fail open immediately.  There is deliberately
        no minimum floor: a floor would violate the shared deadline when the
        pipeline is already close to its render reserve.
        """

        available = max(0.0, self.remaining_seconds() - max(0.0, reserve_after_seconds))
        if requested_seconds is None:
            return available
        return max(0.0, min(float(requested_seconds), available))

    def optional_stage_allowed(
        self,
        *,
        reserve_after_seconds: float = 0.0,
        minimum_useful_seconds: float = 1.0,
    ) -> bool:
        return self.allowance(None, reserve_after_seconds=reserve_after_seconds) >= max(
            0.0, float(minimum_useful_seconds)
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "total_seconds": round(self.total_seconds, 3),
            "render_reserve_seconds": round(self.render_reserve_seconds, 3),
            "elapsed_seconds": round(self.elapsed_seconds(), 3),
            "remaining_seconds": None if self.render_started else round(self.remaining_seconds(), 3),
            "render_progress_supervised": self.render_started,
        }


def install_delivery_budget(
    config: MutableMapping[str, Any],
    *,
    clock: Clock = time.monotonic,
) -> DeliveryBudget:
    """Create (or return) the job's shared delivery budget."""

    existing = config.get("_delivery_budget")
    if isinstance(existing, DeliveryBudget):
        return existing

    configured_total = _number(
        config.get("delivery_budget_seconds")
        or os.environ.get("VIDZEO_VIDEO_DELIVERY_BUDGET_SECONDS"),
        None,
    )
    if configured_total is None:
        script = str(config.get("script") or "").strip()
        word_count = len(script.split()) if script else 0
        est_audio_seconds = (word_count / 2.3) if word_count > 0 else 0.0
        if est_audio_seconds > 600.0:
            total = max(900.0, min(7200.0, est_audio_seconds * 2.5 + 600.0))
        else:
            total = 900.0
        if str(config.get('llm_provider') or '') == 'external_agent':
            # One connected host services bounded scene/vision inbox turns.
            # Explicit configured ceilings remain authoritative.
            total = max(total, 1800.0)
    else:
        total = configured_total

    max_cap = _number(
        config.get("delivery_budget_max_cap_seconds")
        or os.environ.get("VIDZEO_DELIVERY_BUDGET_MAX_SECONDS"),
        max(1800.0, total),
    )
    reserve = _number(
        config.get("render_reserve_seconds")
        or os.environ.get("VIDZEO_RENDER_RESERVE_SECONDS"),
        max(330.0, min(1800.0, total * 0.35)) if configured_total is None and total > 900.0 else 330.0,
    )
    budget = DeliveryBudget.create(
        total_seconds=total,
        render_reserve_seconds=reserve,
        max_cap_seconds=max_cap,
        clock=clock,
    )
    config["_delivery_budget"] = budget
    config["_pipeline_started_at"] = budget.started_at
    config["_delivery_deadline_at"] = budget.deadline_at
    config["_delivery_budget_seconds"] = budget.total_seconds
    config["_render_reserve_seconds"] = budget.render_reserve_seconds
    return budget


def get_delivery_budget(config: Any) -> Optional[DeliveryBudget]:
    if not isinstance(config, MutableMapping):
        return None
    budget = config.get("_delivery_budget")
    return budget if isinstance(budget, DeliveryBudget) else None
