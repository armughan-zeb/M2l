"""Tests for the pool scaling target (pool_scaling.scaling_target).

Proves the 2026-11-20 floor change: the media-phase floor is 4 (was 2),
the render gate still forces target 0, the ceiling stays 8, the
pending/3 slope above the floor is unchanged, and idle with nothing
pending keeps 2 warm instead of over-spawning to 4.

Run:  <repo>/vidnets/.venv/bin/python -m pytest \
        scripts/resident-inference-worker/test_pool_scaling.py -v
"""

import pytest

from pool_scaling import (
    IDLE_FLOOR_WORKERS,
    MAX_WORKERS,
    MEDIA_FLOOR_WORKERS,
    scaling_target,
)


def test_media_floor_is_four_not_two():
    # Any pending work at all — even a single request — holds 4 workers.
    # This is the floor change: pending=1 used to target 2.
    for pending in (1, 2, 3, 6, 9, 12):
        assert scaling_target(pending, False) == 4, pending


def test_media_floor_beats_small_pending_over_three():
    # ceil(pending/3) is 1..4 for pending 1..12; the floor, not the
    # slope, sets the target across that whole range.
    assert MEDIA_FLOOR_WORKERS == 4
    assert scaling_target(3, False) == 4   # ceil(3/3) = 1 -> floor 4
    assert scaling_target(12, False) == 4  # ceil(12/3) = 4 -> floor 4


def test_pending_over_three_slope_above_floor():
    # Above the floor the old pending/3 rule is unchanged.
    assert scaling_target(13, False) == 5  # ceil(13/3) = 5
    assert scaling_target(15, False) == 5
    assert scaling_target(16, False) == 6
    assert scaling_target(21, False) == 7
    assert scaling_target(24, False) == 8  # ceil(24/3) = 8


def test_ceiling_stays_eight():
    # No raising to 8-as-floor and no target above 8: the VM (1.5 shared
    # CPUs, 7.7 GB RAM) cannot use more workers than this.
    assert MAX_WORKERS == 8
    for pending in (25, 30, 100, 1000):
        assert scaling_target(pending, False) == 8, pending


def test_render_active_target_zero():
    # The render gate still sets target 0 — workers stand down for
    # renders — no matter how much media work is pending.
    for pending in (0, 1, 3, 12, 24, 100):
        assert scaling_target(pending, True) == 0, pending


def test_idle_with_nothing_pending_does_not_over_spawn():
    # Idle keeps 2 warm (the old floor), not the media floor of 4.
    assert IDLE_FLOOR_WORKERS == 2
    assert scaling_target(0, False) == 2


def test_negative_pending_treated_as_idle():
    assert scaling_target(-5, False) == 2


def test_render_active_none_fails_open_to_idle_rule():
    # Only True counts as render-active; an unprovable gate (None) is
    # the caller's fail-open, and here behaves like idle.
    assert scaling_target(3, None) == 4
    assert scaling_target(0, None) == 2
