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
    """One monotonic deadline shared by planning, retrieval and rendering.

    The deadline is progress-aware: every genuine progress report extends it
    by ``stall_grace_seconds`` (see :meth:`note_progress`), so a job that
    keeps moving is never killed for being slow. Only a true stall — no
    progress for a full grace window — lets the deadline expire. The absolute
    cap bounds even a steadily inching job so a hung worker cannot hold the
    queue hostage forever.
    """

    total_seconds: float
    render_reserve_seconds: float
    started_at: float
    deadline_at: float
    clock: Clock = field(default=time.monotonic, repr=False, compare=False)
    render_started: bool = False
    stall_grace_seconds: float = 1200.0
    # Spec §3.1: the shared delivery budget is capped at 7200s. The old
    # 14400s absolute cap let a steadily-progressing job run for four
    # hours; a job that needs longer than two hours of preparation is
    # not progressing fast enough to deliver, it is stuck.
    absolute_cap_seconds: float = 7200.0
    last_progress_at: Optional[float] = None

    def __post_init__(self) -> None:
        if self.last_progress_at is None:
            self.last_progress_at = self.started_at

    def note_progress(self) -> float:
        """Record genuine progress and extend the deadline by the stall grace.

        Returns the new deadline. The extension is capped at the absolute
        deadline so a job that inches forward forever still ends. Heartbeat
        re-emissions ("still working …") must NOT call this — only real
        advances (a higher fraction, a further scene count) count, otherwise
        a stalled job would keep its own deadline alive.
        """
        now = float(self.clock())
        self.last_progress_at = now
        absolute_deadline = self.started_at + max(0.0, float(self.absolute_cap_seconds))
        self.deadline_at = min(
            absolute_deadline,
            max(self.deadline_at, now + max(0.0, float(self.stall_grace_seconds))),
        )
        return self.deadline_at

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
        stall_grace_seconds: Optional[float] = None,
        absolute_cap_seconds: Optional[float] = None,
        clock: Clock = time.monotonic,
    ) -> "DeliveryBudget":
        # Spec §3.1: total = max(900, min(7200, 2.5 * T_audio + 600)) when
        # T_audio > 600, else 900. Long-form jobs (75+ scenes with semantic
        # editing) need up to ~30 minutes once media retrieval, the semantic
        # render and the straight-recovery reserve are accounted for, so a
        # configured production job may expand to 1800s by default and up to
        # 7200s via max_cap_seconds or environment. The absolute cap (below)
        # is 7200s, matching the spec formula cap.
        cap = 1800.0 if max_cap_seconds is None else max(1.0, float(max_cap_seconds))
        total = max(1.0, min(cap, float(total_seconds)))
        reserve = max(0.0, min(float(render_reserve_seconds), total * 0.60))
        started = float(clock())
        grace = _number(
            stall_grace_seconds if stall_grace_seconds is not None
            else os.environ.get("VIDZEO_DELIVERY_BUDGET_STALL_GRACE_SECONDS"),
            1200.0,
        )
        absolute_cap = _number(
            absolute_cap_seconds if absolute_cap_seconds is not None
            else os.environ.get("VIDZEO_DELIVERY_BUDGET_ABSOLUTE_CAP_SECONDS"),
            7200.0,
        )
        return cls(
            total_seconds=total,
            render_reserve_seconds=reserve,
            started_at=started,
            deadline_at=started + total,
            clock=clock,
            stall_grace_seconds=max(60.0, float(grace or 1200.0)),
            absolute_cap_seconds=max(total, float(absolute_cap or 7200.0)),
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
            "stall_grace_seconds": self.stall_grace_seconds,
            "absolute_cap_seconds": self.absolute_cap_seconds,
            "last_progress_at": self.last_progress_at,
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
            # Spec §3.1, verbatim: T_total = max(900, min(7200,
            # 2.5 * T_audio + 600)).
            total = max(900.0, min(7200.0, est_audio_seconds * 2.5 + 600.0))
        else:
            total = 900.0
        if str(config.get('llm_provider') or '') == 'external_agent':
            # Muse-inference (external_agent) jobs: the old floor was
            # 2400 + scenes*90, capped at 14400s — it assumed vision was
            # sequential, so a 140-scene job got a four-hour budget and
            # could crawl for hours instead of failing open to web/graphic
            # fallbacks. Vision is now parallel: 8 Muse workers serving
            # inbox turns, a 12-call visual gate. One wave costs ~90s for
            # 8 scenes, not 90s per scene, so the parallel allowance is
            # ceil(scenes/8)*90 on top of a 900s base, floored at 1800s
            # for short agent jobs (a 1-min job needs more than the 900s
            # audio floor once inbox round trips are counted), and capped
            # at 7200s like every other job. Explicit configured ceilings
            # remain authoritative. The deadline stays progress-aware
            # (DeliveryBudget.note_progress): a job that keeps moving is
            # never killed for being slow — only a true stall (no progress
            # for the stall grace) lets it expire.
            est_scenes = (word_count / 16.0) if word_count > 0 else 0.0
            waves = math.ceil(est_scenes / 8.0) if est_scenes > 0 else 0
            parallel_floor = min(7200.0, 900.0 + waves * 90.0)
            total = max(total, 1800.0, parallel_floor)
            total = min(7200.0, total)
        if config.get('muse_clip_job_id'):
            from .pc_youtube_clips import relay_branch_seconds
            # Leave planning, web verification and render admission headroom
            # around the PC clip lane's deadline.
            prepare = 1500.0 + relay_branch_seconds(math.ceil(word_count / 16.0))
            total = min(7200.0, max(total, prepare + min(1800.0, prepare * 0.35)))
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
        # Spec §3.1, verbatim: T_reserve = max(330, min(1800, T_total*0.35)).
        # (At T_total=900 this is 330, so the old conditional gave the same
        # answer there; the formula now applies at every total.)
        max(330.0, min(1800.0, total * 0.35)),
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
