#!/usr/bin/env python3
"""Resident inference pool scaling target — the executable form of the
scaling rule in POOL_SUPERVISOR_BRIEF.md ("Scaling rule").

The supervisor itself is an agent following that brief; this module is
the single testable statement of the rule so the brief, the watch hook
(`pool_supervisor_check.py`, which reports the target in its checks)
and any future code loop (see `pool_supervisor.py` on the
impl/pool-supervisor-upgrade branch) cannot drift apart. If the rule
changes, change it here AND in the brief, in the same commit.

Rule (2026-11-20, pool floor raised 2 -> 4 for the media phase):
  - Render ACTIVE -> target 0. Workers stand down for renders; the
    render gate owns the VM while it runs. Never spawn during a render.
  - Media pending (pending > 0, gate idle) -> target =
    clamp(ceil(pending / 3), MEDIA_FLOOR_WORKERS, MAX_WORKERS).
    The floor is 4, not 2: batched vision files requests in parallel,
    so a 2-worker floor leaves the parallel batch draining slowly.
    The ceiling stays 8 and the pending/3 slope above the floor is
    unchanged. 8 is NOT raised: the VM has 1.5 shared CPUs and 7.7 GB
    RAM, so more than 4 workers mostly fight each other for CPU; 4 is
    the sweet spot for draining a media burst.
  - Idle with nothing pending (pending == 0, gate idle) -> target
    IDLE_FLOOR_WORKERS (2). Keep 2 warm so the next burst does not wait
    for a spawn; do not hold 4 warm when there is nothing to do.

No throttling lives here and none is changed by this rule: per-host
throttle slots are untouched.
"""

import math

# Floor while media work is pending. Raised from 2 on 2026-11-20: the
# batched-vision lanes file requests in parallel and a 2-worker floor
# drains that batch too slowly.
MEDIA_FLOOR_WORKERS = 4
# Floor with nothing pending: keep 2 warm, do not over-spawn at idle.
IDLE_FLOOR_WORKERS = 2
# Ceiling. Do NOT raise: 1.5 shared CPUs / 7.7 GB RAM; above 4 workers
# the pool is CPU-bound on itself.
MAX_WORKERS = 8
# One worker drains ~3 pending requests per scaling step.
REQUESTS_PER_WORKER = 3


def scaling_target(pending, render_active):
    """Target worker count for `pending` unclaimed requests.

    `pending` is the count of unclaimed, unexpired *.request.json files
    in the spool; `render_active` is True when render-gate.js reports a
    render ACTIVE (None/unprovable fails open to False — the caller
    decides that; this function treats only True as active).
    """
    if render_active:
        return 0
    pending = max(0, int(pending))
    if pending == 0:
        return IDLE_FLOOR_WORKERS
    return max(MEDIA_FLOOR_WORKERS,
               min(MAX_WORKERS,
                   math.ceil(pending / REQUESTS_PER_WORKER)))
