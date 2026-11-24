#!/usr/bin/env python3
"""Resident inference pool scaling target — the executable form of the
scaling rule in POOL_SUPERVISOR_BRIEF.md ("Scaling rule").

The supervisor itself is an agent following that brief; this module is
the single testable statement of the rule so the brief, the watch hook
(`pool_supervisor_check.py`, which reports the target in its checks)
and any future code loop (see `pool_supervisor.py` on the
impl/pool-supervisor-upgrade branch) cannot drift apart. If the rule
changes, change it here AND in the brief, in the same commit.

Rule (2026-11-22, vision is the bottleneck — fastest optimization):
  - Render ACTIVE -> target 0. Workers stand down for renders; the
    render gate owns the VM while it runs. Never spawn during a render.
  - Media pending (pending > 0, gate idle) -> target = MAX_WORKERS (8).
    Vision is the bottleneck: each call is a ~60s worker round trip, the
    visual gate issues up to 8 parallel checks, and a 4-worker pool
    leaves half the gate queued until its deadline expires
    (VisualBudgetExceeded). The ceiling stays 8 — NOT raised: the VM has
    1.5 shared CPUs and 7.7 GB RAM. The old pending/3 slope is gone:
    any pending media work at all fills the pool to the ceiling at once.
  - Idle with nothing pending (pending == 0, gate idle) -> target
    IDLE_FLOOR_WORKERS (4). Keep 4 warm so the next burst does not wait
    for a spawn.

No throttling lives here and none is changed by this rule: per-host
throttle slots are untouched.
"""

# Floor while media work is pending. Superseded 2026-11-22: any pending
# media work now targets the ceiling (MAX_WORKERS) at once, so the floor
# no longer sets the target. Kept for the brief and tests.
MEDIA_FLOOR_WORKERS = 4
# Floor with nothing pending: keep 4 warm, do not over-spawn at idle.
IDLE_FLOOR_WORKERS = 4
# Ceiling. Do NOT raise: 1.5 shared CPUs / 7.7 GB RAM.
MAX_WORKERS = 8

# Scale-up signal file (2026-11-21, pool scale-up fix). The supervisor
# loop writes this into the spool the SAME tick it queues scale-up
# spawns (or finds the pool short with pending work and no spawn
# possible), so the parent is summoned promptly instead of waiting out
# the 600s owner tripwire / 300s shortfall watch. The watch hook
# (pool_supervisor_check.py) wakes the parent at once on a fresh
# signal while the gate is idle. The file is the durable spawn queue's
# cover sheet: it names exactly which workerIds are owed and why, so a
# top-level parent agent — the ONLY spawner whose workers survive
# (cron-spawned workers die at the 300s timeout, 2026-11-21) — can
# spawn them directly. The loop deletes the file once the pool is back
# at target (or a render goes ACTIVE); a stale file (>120s) is ignored
# by the hook, so a dead loop cannot summon the parent forever.
SCALEUP_SIGNAL_FILENAME = "supervisor-scaleup-needed.json"
# Freshness window the hook applies to the signal (same as supervisor
# liveness staleness: the loop rewrites it every 15s tick while the
# shortfall persists).
SCALEUP_SIGNAL_FRESH_S = 120


def build_scaleup_signal(live, target, pending, spawns, blocked=None,
                         supervisor_id=None, now_ms=None):
    """Payload for SCALEUP_SIGNAL_FILENAME.

    `live`/`target`/`pending` are the pool counts at write time;
    `spawns` is a list of {action, actionId, workerId, reason} for the
    pending spawn/respawn actions; `blocked` is a list of short reasons
    the remaining shortfall could not be queued for (held slots,
    sighted slots, busy slots). `now_ms` is epoch-ms (time.time()*1000
    when None). Pure function, so the loop, the self-heal planner and
    the tests all build the same shape.
    """
    import time as _time
    if now_ms is None:
        now_ms = int(_time.time() * 1000)
    return {
        "updatedAt": int(now_ms),
        "supervisorAgentId": supervisor_id,
        "live": int(live),
        "target": int(target),
        "pending": int(pending),
        "spawns": [{
            "action": s.get("action"),
            "actionId": s.get("id") or s.get("actionId"),
            "workerId": s.get("workerId"),
            "reason": s.get("reason"),
        } for s in (spawns or [])],
        "blocked": list(blocked or []),
    }


def write_scaleup_signal(spool_dir, payload):
    """Atomically write the scale-up signal into the spool dir."""
    import json as _json
    import os as _os
    path = _os.path.join(spool_dir, SCALEUP_SIGNAL_FILENAME)
    tmp = "%s.tmp-scaleup-%d" % (path, _os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        _json.dump(payload, fh)
    _os.replace(tmp, path)
    return path


def clear_scaleup_signal(spool_dir):
    """Delete the scale-up signal (pool back at target / render ACTIVE).
    Missing file is not an error."""
    import os as _os
    try:
        _os.remove(_os.path.join(spool_dir, SCALEUP_SIGNAL_FILENAME))
    except OSError:
        pass


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
    # Any media work at all fills the pool to the ceiling at once.
    # Vision is the bottleneck (~60s per round trip); a partial pool
    # leaves the visual gate queued until its deadline expires.
    return MAX_WORKERS
