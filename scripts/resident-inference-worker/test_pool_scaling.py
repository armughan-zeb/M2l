"""Tests for the pool scaling target (pool_scaling.scaling_target).

Proves the 2026-11-22 rule: any media work pending at all fills the pool
to the ceiling (8) at once — vision is the bottleneck and a partial pool
leaves the visual gate queued until its deadline expires
(VisualBudgetExceeded). The render gate still forces target 0, the
ceiling stays 8, and idle with nothing pending keeps 4 warm.

Run:  <repo>/vidnets/.venv/bin/python -m pytest \
        scripts/resident-inference-worker/test_pool_scaling.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pool_scaling import (
    IDLE_FLOOR_WORKERS,
    MAX_WORKERS,
    MEDIA_FLOOR_WORKERS,
    scaling_target,
)


def test_media_pending_targets_ceiling_at_once():
    # Any pending work at all — even a single request — fills the pool to
    # the ceiling (8) at once. Vision is the bottleneck (~60s per round
    # trip); the old pending/3 slope needed 22+ pending to reach 8, which
    # left the visual gate queued until its deadline expired.
    for pending in (1, 2, 3, 6, 9, 12, 13, 16, 21, 24):
        assert scaling_target(pending, False) == 8, pending


def test_ceiling_stays_eight():
    # No target above 8: the VM (1.5 shared CPUs, 7.7 GB RAM) cannot use
    # more workers than this.
    assert MAX_WORKERS == 8
    for pending in (25, 30, 100, 1000):
        assert scaling_target(pending, False) == 8, pending


def test_media_floor_constant_kept():
    # The floor constant is kept for the brief and for callers that
    # reference it; the target no longer uses the slope.
    assert MEDIA_FLOOR_WORKERS == 4


def test_render_active_target_zero():
    # The render gate still sets target 0 — workers stand down for
    # renders — no matter how much media work is pending.
    for pending in (0, 1, 3, 12, 24, 100):
        assert scaling_target(pending, True) == 0, pending


def test_idle_with_nothing_pending_does_not_over_spawn():
    # Idle keeps 4 warm (raised 2 -> 4 on 2026-11-22), not the ceiling 8.
    assert IDLE_FLOOR_WORKERS == 4
    assert scaling_target(0, False) == 4


def test_negative_pending_treated_as_idle():
    assert scaling_target(-5, False) == 4


def test_render_active_none_fails_open_to_idle_rule():
    # Only True counts as render-active; an unprovable gate (None) is
    # the caller's fail-open, and here behaves like idle.
    assert scaling_target(3, None) == 8
    assert scaling_target(0, None) == 4


# --- scale-up signal (2026-11-21) ---------------------------------------


def test_scaleup_signal_payload_shape():
    from pool_scaling import SCALEUP_SIGNAL_FILENAME, build_scaleup_signal
    assert SCALEUP_SIGNAL_FILENAME == "supervisor-scaleup-needed.json"
    payload = build_scaleup_signal(
        2, 4, 6,
        [{"action": "respawn", "id": "respawn-worker-3-1",
          "workerId": "worker-3", "reason": "dead"},
         {"action": "spawn", "id": None, "workerId": "worker-4",
          "reason": "live=2 target=4 pending=6"}],
        blocked=["held slots (3 deaths in 30 min): worker-3"],
        supervisor_id="sup-1", now_ms=1700000000000)
    assert payload == {
        "updatedAt": 1700000000000,
        "supervisorAgentId": "sup-1",
        "live": 2, "target": 4, "pending": 6,
        "spawns": [
            {"action": "respawn", "actionId": "respawn-worker-3-1",
             "workerId": "worker-3", "reason": "dead"},
            {"action": "spawn", "actionId": None,
             "workerId": "worker-4", "reason": "live=2 target=4 pending=6"},
        ],
        "blocked": ["held slots (3 deaths in 30 min): worker-3"],
    }


def test_scaleup_signal_defaults_empty(tmp_path):
    # No spawns, no blocked reasons, no supervisor id: still a valid
    # payload (the blocked-only shape the loop writes when no spawn
    # could be queued).
    import json
    from pool_scaling import (build_scaleup_signal,
                              clear_scaleup_signal, write_scaleup_signal)
    payload = build_scaleup_signal(2, 4, 6, [], now_ms=1700000000000)
    assert payload["spawns"] == [] and payload["blocked"] == []
    assert payload["supervisorAgentId"] is None
    path = write_scaleup_signal(str(tmp_path), payload)
    with open(path) as f:
        assert json.load(f)["updatedAt"] == 1700000000000
    clear_scaleup_signal(str(tmp_path))
    assert not path.endswith("/") and not __import__("os").path.exists(path)
    clear_scaleup_signal(str(tmp_path))  # missing file is not an error
