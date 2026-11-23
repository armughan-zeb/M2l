#!/usr/bin/env python3
"""Pool-supervisor liveness check (decision core of the pool-supervisor-watch hook).

Reads the inference spool and decides whether the resident pool supervisor
is dead and a respawn wake is warranted. Prints exactly one JSON object on
stdout:

    {"decision": "wake" | "silent", "reason": "<short code>",
     "payload": {...} | null, "checks": {...}}

and always exits 0 (a crash would leave the bash wrapper silent anyway;
a traceback on stdout would break the wrapper's JSON parse, so all
errors are caught and reported as a silent decision).

Decision rules (thresholds from the briefs — do not retune silently):
  - Supervisor writes pool-status.json every 30s; the parent verifies it
    is < 60s old. SUPERVISOR_STALE_SECS (default 120s) = two missed
    verify windows: only after 120s without a fresh write is the
    supervisor treated as dead.
  - The supervisor ALSO writes supervisor-alive.json every 30s (see
    POOL_SUPERVISOR_BRIEF.md "Supervisor liveness marker"). The
    supervisor is alive if EITHER file is fresh (< 120s). Dead requires
    BOTH stale/missing, so one failed write path cannot cause a duplicate
    respawn. (Older supervisors write only pool-status.json; that alone
    still counts as alive when fresh.)
  - A worker is live when its worker-alive-<workerId>.json heartbeat is
    < WORKER_DEAD_SECS (90s, from the worker brief). Live workers are
    reported in the wake payload so the respawned supervisor ADOPTS them
    instead of spawning duplicates.
  - Render ACTIVE (render-gate.js status exit 2) -> silent: never respawn
    during a render; the next poll retries. Any other gate failure fails
    OPEN (treat as idle, note it) — same convention as the inbox hook.
  - The checks dict also reports the pool scaling target for the current
    backlog (pool_scaling.scaling_target): 0 during a render, 4–8 while
    media is pending (floor 4), 2 when idle with nothing pending. The
    checker never spawns; the target is informational, for the respawned
    supervisor and for verifying the rule on the live path.
  - Pool shortfall (2026-11-20 fix): fresh liveness files prove the
    LOOP is ticking, not that the POOL is staffed — on 2026-11-20 the
    loop ticked for ~84 minutes with 0 workers and no supervisor agent
    while liveness stayed fresh, so the hook never woke. When the gate
    is provably idle and live workers are below target for longer than
    POOL_SHORTFALL_WAKE_SECS (default 300s, tracked in state dir
    `pool-shortfall.json`), wake with reason `pool_empty_idle`
    (live == 0) or `pool_below_target`, even though liveness is fresh.
    A shortfall shorter than that is normal churn (a render just ended,
    the loop is re-queueing, the self-heal cron is spawning) and stays
    silent. Never wakes during a render.
  - Scale-up signal (2026-11-21 fix): the loop writes
    `supervisor-scaleup-needed.json` into the spool the SAME tick it
    queues scale-up spawns (or finds the pool short with pending work
    and no spawn possible). A FRESH signal (< 120s) with the gate
    provably idle and live workers below target wakes at once with
    reason `pool_scaleup_needed` — no 300s shortfall wait, because the
    loop already did the confirming: the signal names the exact
    workerIds owed, and the wake payload carries them so the parent
    (the only spawner whose workers survive) can spawn directly.
    The 300s shortfall watch stays as the fallback for a dead loop
    (no fresh signal). A stale signal is ignored; a signal with the
    pool back at target wakes nothing. Cooldown (600s) still applies.
  - supervisor-parked.json present -> silent: the parent deliberately
    stopped the supervisor; delete that file to re-enable watching.
  - After a wake, a cooldown (default 600s) suppresses repeat wakes so a
    slow spawn cannot cause a wake storm. The cooldown file is written
    only on a real (non-dry-run) wake decision.

Environment overrides (used by the tests and by deployment):
  MUSE_INFERENCE_SPOOL_DIR   spool dir (default
                             ~/workspace/vidnets-runtime/temp/inference-spool)
  POOL_WATCH_STATE_DIR       state dir (default
                             ~/hooks/state/pool-supervisor-watch)
  POOL_WATCH_RENDER_GATE     render-gate.js path (default
                             ~/workspace/M2l/vidnets/scripts/render-gate.js)
  POOL_WATCH_SUPERVISOR_STALE_SECS  default 120
  POOL_WATCH_WORKER_DEAD_SECS       default 90
  POOL_WATCH_COOLDOWN_SECS          default 600
  POOL_WATCH_SHORTFALL_WAKE_SECS    default 300
  HATCH_HOOK_DRY_RUN=1       dry run: never write state (no cooldown file)

This script only READS the spool. It never touches worker files, never
spawns anything, and never writes into the spool dir. State writes go to
POOL_WATCH_STATE_DIR only.
"""

import glob
import json
import os
import subprocess
import sys
import time

from pool_scaling import scaling_target
from pool_scaling import SCALEUP_SIGNAL_FILENAME
from pool_scaling import SCALEUP_SIGNAL_FRESH_S

DEFAULT_SPOOL = os.path.expanduser(
    "~/workspace/vidnets-runtime/temp/inference-spool")
DEFAULT_STATE_DIR = os.path.expanduser("~/hooks/state/pool-supervisor-watch")
DEFAULT_RENDER_GATE = os.path.expanduser(
    "~/workspace/M2l/vidnets/scripts/render-gate.js")
BRIEF_PATH = os.path.expanduser(
    "~/workspace/M2l/scripts/resident-inference-worker/"
    "POOL_SUPERVISOR_BRIEF.md")
RESPAWN_NOTE_PATH = os.path.expanduser(
    "~/workspace/M2l/scripts/resident-inference-worker/"
    "POOL_SUPERVISOR_RESPAWN_NOTE.md")

SUPERVISOR_STALE_SECS = int(
    os.environ.get("POOL_WATCH_SUPERVISOR_STALE_SECS", "120"))
WORKER_DEAD_SECS = int(os.environ.get("POOL_WATCH_WORKER_DEAD_SECS", "90"))
COOLDOWN_SECS = int(os.environ.get("POOL_WATCH_COOLDOWN_SECS", "600"))
SHORTFALL_WAKE_SECS = int(
    os.environ.get("POOL_WATCH_SHORTFALL_WAKE_SECS", "300"))
DRY_RUN = os.environ.get("HATCH_HOOK_DRY_RUN") == "1"


def _env_int(name, default):
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


SUPERVISOR_STALE_SECS = _env_int(
    "POOL_WATCH_SUPERVISOR_STALE_SECS", SUPERVISOR_STALE_SECS)
WORKER_DEAD_SECS = _env_int("POOL_WATCH_WORKER_DEAD_SECS", WORKER_DEAD_SECS)
COOLDOWN_SECS = _env_int("POOL_WATCH_COOLDOWN_SECS", COOLDOWN_SECS)
SHORTFALL_WAKE_SECS = _env_int(
    "POOL_WATCH_SHORTFALL_WAKE_SECS", SHORTFALL_WAKE_SECS)


def _read_updated_at_age(path, now):
    """Age in seconds of a JSON file's updatedAt (epoch ms), or None."""
    try:
        with open(path, encoding="utf-8") as fh:
            row = json.load(fh)
        updated_ms = float(row["updatedAt"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return now - updated_ms / 1000.0


def _is_fresh(age):
    """Fresh = written within the staleness window. A few seconds in the
    future (clock skew) still counts; anything older, or more than 5s in
    the future, does not."""
    return age is not None and -5.0 <= age < SUPERVISOR_STALE_SECS


def _live_workers(spool, now):
    """workerIds whose heartbeat is < WORKER_DEAD_SECS old."""
    live = []
    for path in sorted(glob.glob(
            os.path.join(spool, "worker-alive-worker-*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                row = json.load(fh)
            worker_id = str(row.get("workerId") or "")
            age = now - float(row["updatedAt"]) / 1000.0
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if not worker_id:
            worker_id = os.path.basename(path)[len("worker-alive-"):-len(".json")]
        if -5.0 <= age < WORKER_DEAD_SECS:
            live.append({"workerId": worker_id, "age_s": round(age, 1)})
    return live


def _pending_count(spool, now):
    """Unclaimed *.request.json files that are not expired."""
    count = 0
    for path in glob.glob(os.path.join(spool, "*.request.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                row = json.load(fh)
            inner = row.get("request") or row
            expires = float(inner.get("expires_at"))
            if expires <= now:
                continue  # expired: dead, not pending
        except (OSError, ValueError, KeyError, TypeError):
            pass  # unparseable: count it — the respawned pool must see it
        count += 1
    return count


def _render_active(gate_path):
    """True if a render is ACTIVE, False if idle, None if unprovable.

    Only exit code 2 from `render-gate.js status` means ACTIVE. Any other
    failure (missing node, missing script, timeout) fails OPEN to False
    with a note — a broken probe must not block pool recovery forever.
    """
    try:
        proc = subprocess.run(
            ["node", gate_path, "status"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode == 2:
        return True
    if proc.returncode == 0:
        return False
    return None


def _cooldown_remaining(state_dir, now):
    """Seconds left on the post-wake cooldown, or 0."""
    try:
        with open(os.path.join(state_dir, "last-wake.json"),
                  encoding="utf-8") as fh:
            woke_at = float(json.load(fh)["woke_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    return max(0, COOLDOWN_SECS - (now - woke_at))


def _write_cooldown(state_dir, now):
    if DRY_RUN:
        return
    try:
        os.makedirs(state_dir, exist_ok=True)
        tmp = os.path.join(state_dir, "last-wake.json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"woke_at": now}, fh)
        os.replace(tmp, os.path.join(state_dir, "last-wake.json"))
    except OSError:
        pass  # a missed cooldown write only risks one extra wake


def _shortfall_first_seen(state_dir):
    """Epoch seconds the current below-target shortfall started, or
    None. Tracked in the state dir so it survives across hook polls."""
    try:
        with open(os.path.join(state_dir, "pool-shortfall.json"),
                  encoding="utf-8") as fh:
            return float(json.load(fh)["first_seen_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_shortfall(state_dir, now):
    if DRY_RUN:
        return
    try:
        os.makedirs(state_dir, exist_ok=True)
        tmp = os.path.join(state_dir, "pool-shortfall.json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"first_seen_at": now}, fh)
        os.replace(tmp, os.path.join(state_dir, "pool-shortfall.json"))
    except OSError:
        pass  # a missed shortfall write only delays one wake


def _clear_shortfall(state_dir):
    if DRY_RUN:
        return
    try:
        os.remove(os.path.join(state_dir, "pool-shortfall.json"))
    except OSError:
        pass


def _pending_actions(spool, now):
    """Pending entries of the supervisor action queue (pool_supervisor.py),
    oldest first, with their age in seconds. Read-only."""
    try:
        with open(os.path.join(spool, "supervisor-actions.json"),
                  encoding="utf-8") as fh:
            doc = json.load(fh)
        actions = doc.get("actions") or []
    except (OSError, ValueError, AttributeError, TypeError):
        return []
    out = []
    for row in actions:
        if not isinstance(row, dict) or row.get("status") != "pending":
            continue
        try:
            created = float(row.get("createdAtMs", 0)) / 1000.0
        except (TypeError, ValueError):
            continue
        out.append({"action": row.get("action"),
                    "workerId": row.get("workerId"),
                    "reason": row.get("reason"),
                    "age_s": round(now - created, 1)})
    out.sort(key=lambda r: -r["age_s"])
    return out


def _owner_unresponsive(spool):
    """True when the loop has paused liveness writes because its owner
    agent left queued actions unacted (see pool_supervisor.py)."""
    path = os.path.join(spool, "supervisor-owner-unresponsive.json")
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        return {"age_s": doc.get("oldestPendingActionAgeS"),
                "pending_actions": doc.get("pendingActions")}
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def _scaleup_signal(spool, now):
    """The loop's (or self-heal's) scale-up signal, or None.

    Returns {"age_s", "spawns", "blocked", "live", "target",
    "pending"} when supervisor-scaleup-needed.json parses and is
    FRESH (written within SCALEUP_SIGNAL_FRESH_S — the loop rewrites
    it every 15s tick while the shortfall persists, so a fresh file
    means the pool is short RIGHT NOW). A stale or unreadable file
    is None: a dead loop must not summon the parent forever, and the
    300s shortfall watch is the fallback for that case.
    """
    path = os.path.join(spool, SCALEUP_SIGNAL_FILENAME)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        updated_ms = float(doc["updatedAt"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    age = now - updated_ms / 1000.0
    if not -5.0 <= age < SCALEUP_SIGNAL_FRESH_S:
        return None
    spawns = doc.get("spawns")
    return {
        "age_s": round(age, 1),
        "spawns": spawns if isinstance(spawns, list) else [],
        "blocked": doc.get("blocked") if isinstance(
            doc.get("blocked"), list) else [],
        "live": doc.get("live"),
        "target": doc.get("target"),
        "pending": doc.get("pending"),
    }


def decide(spool, state_dir, gate_path, now=None):
    """Core decision. Returns the dict printed on stdout."""
    now = time.time() if now is None else now
    checks = {
        "spool": spool,
        "spool_exists": os.path.isdir(spool),
        "status_age_s": None,
        "marker_age_s": None,
        "supervisor_alive": False,
        "live_workers": [],
        "pending": 0,
        "render_active": None,
        "target": None,
        "render_gate_note": None,
        "parked": False,
        "cooldown_remaining_s": 0,
        "pending_actions": [],
        "owner_unresponsive": None,
        "scaleup_signal": None,
        "shortfall_s": None,
    }

    def silent(reason):
        return {"decision": "silent", "reason": reason,
                "payload": None, "checks": checks}

    if checks["spool_exists"]:
        checks["status_age_s"] = _read_updated_at_age(
            os.path.join(spool, "pool-status.json"), now)
        checks["marker_age_s"] = _read_updated_at_age(
            os.path.join(spool, "supervisor-alive.json"), now)
        checks["live_workers"] = _live_workers(spool, now)
        checks["pending"] = _pending_count(spool, now)
        checks["parked"] = os.path.exists(
            os.path.join(spool, "supervisor-parked.json"))
        checks["pending_actions"] = _pending_actions(spool, now)
        checks["owner_unresponsive"] = _owner_unresponsive(spool)
        checks["scaleup_signal"] = _scaleup_signal(spool, now)

    if checks["parked"]:
        return silent("supervisor_parked")

    render = _render_active(gate_path)
    checks["render_active"] = render
    # Target for the current backlog, from the one scaling rule
    # (pool_scaling.py). Set before the render early-return so the
    # render-active target (0) is visible in the output too. An
    # unprovable gate (None) fails open to idle, as above.
    checks["target"] = scaling_target(checks["pending"], render is True)
    if render is None:
        checks["render_gate_note"] = (
            "render gate unprovable (node/gate error); failing open to idle")
    if render is True:
        return silent("render_active")

    # Fast scale-up wake (2026-11-21): a fresh signal means the loop
    # (or self-heal, when the loop is dead) found the pool short with
    # the gate idle and named the owed workerIds THIS tick — wake at
    # once, no 300s shortfall wait. An unprovable gate (None) never
    # wakes on the signal: it fails open, so it cannot prove idle.
    # Cooldown still applies, so a slow spawn cannot storm wakes.
    if render is False and checks["scaleup_signal"] is not None \
            and checks["target"] is not None \
            and len(checks["live_workers"]) < checks["target"]:
        checks["cooldown_remaining_s"] = _cooldown_remaining(
            state_dir, now)
        if checks["cooldown_remaining_s"] > 0:
            return silent("respawn_cooldown")
        payload = {
            "status_age_s": checks["status_age_s"],
            "marker_age_s": checks["marker_age_s"],
            "live_workers": [w["workerId"]
                             for w in checks["live_workers"]],
            "live_worker_detail": checks["live_workers"],
            "pending": checks["pending"],
            "target": checks["target"],
            "render_active": False,
            "spool": spool,
            "brief_path": BRIEF_PATH,
            "respawn_note_path": RESPAWN_NOTE_PATH,
            "action": "spawn_missing_workers",
            "scaleup_spawns": checks["scaleup_signal"]["spawns"],
            "scaleup_blocked": checks["scaleup_signal"]["blocked"],
            "signal_age_s": checks["scaleup_signal"]["age_s"],
            "pending_actions": checks["pending_actions"],
            "owner_unresponsive": checks["owner_unresponsive"],
            "generated_at": now,
        }
        _write_cooldown(state_dir, now)
        return {"decision": "wake", "reason": "pool_scaleup_needed",
                "payload": payload, "checks": checks}

    checks["supervisor_alive"] = (
        _is_fresh(checks["status_age_s"])
        or _is_fresh(checks["marker_age_s"]))
    if checks["supervisor_alive"]:
        # Fresh liveness proves the LOOP ticks, not that the POOL is
        # staffed. Watch the pool itself: gate provably idle and live
        # workers below target for longer than SHORTFALL_WAKE_SECS is a
        # wake even with fresh liveness (2026-11-20: 0 workers, fresh
        # liveness, ~84 min, no wake). A shorter shortfall is normal
        # churn (render just ended, loop re-queueing, self-heal cron
        # spawning) and stays silent. An unprovable gate (None) never
        # wakes on shortfall — it fails open, so it cannot prove idle.
        if render is False and checks["target"] is not None \
                and len(checks["live_workers"]) < checks["target"]:
            first_seen = _shortfall_first_seen(state_dir)
            if first_seen is None:
                _write_shortfall(state_dir, now)
                checks["shortfall_s"] = 0
                return silent("pool_shortfall_watch")
            checks["shortfall_s"] = round(now - first_seen, 1)
            if checks["shortfall_s"] >= SHORTFALL_WAKE_SECS:
                checks["cooldown_remaining_s"] = _cooldown_remaining(
                    state_dir, now)
                if checks["cooldown_remaining_s"] > 0:
                    return silent("respawn_cooldown")
                payload = {
                    "status_age_s": checks["status_age_s"],
                    "marker_age_s": checks["marker_age_s"],
                    "live_workers": [w["workerId"]
                                     for w in checks["live_workers"]],
                    "live_worker_detail": checks["live_workers"],
                    "pending": checks["pending"],
                    "target": checks["target"],
                    "shortfall_s": checks["shortfall_s"],
                    "render_active": False,
                    "spool": spool,
                    "brief_path": BRIEF_PATH,
                    "respawn_note_path": RESPAWN_NOTE_PATH,
                    "action": "spawn_missing_workers",
                    "pending_actions": checks["pending_actions"],
                    "owner_unresponsive": checks["owner_unresponsive"],
                    "generated_at": now,
                }
                _write_cooldown(state_dir, now)
                reason = ("pool_empty_idle"
                          if not checks["live_workers"]
                          else "pool_below_target")
                return {"decision": "wake", "reason": reason,
                        "payload": payload, "checks": checks}
            return silent("pool_shortfall_watch")
        _clear_shortfall(state_dir)
        return silent("supervisor_alive")

    # Supervisor dead (both status and marker stale/missing, or spool gone).
    checks["cooldown_remaining_s"] = _cooldown_remaining(state_dir, now)
    if checks["cooldown_remaining_s"] > 0:
        return silent("respawn_cooldown")

    payload = {
        "status_age_s": checks["status_age_s"],
        "marker_age_s": checks["marker_age_s"],
        "live_workers": [w["workerId"] for w in checks["live_workers"]],
        "live_worker_detail": checks["live_workers"],
        "pending": checks["pending"],
        "render_active": False,
        "spool": spool,
        "brief_path": BRIEF_PATH,
        "respawn_note_path": RESPAWN_NOTE_PATH,
        "action": "adopt_live_workers",
        "pending_actions": checks["pending_actions"],
        "owner_unresponsive": checks["owner_unresponsive"],
        "generated_at": now,
    }
    _write_cooldown(state_dir, now)
    return {"decision": "wake", "reason": "supervisor_dead",
            "payload": payload, "checks": checks}


def main():
    spool = (os.environ.get("MUSE_INFERENCE_SPOOL_DIR")
             or os.environ.get("POOL_SPOOL_DIR")
             or DEFAULT_SPOOL)
    state_dir = os.environ.get("POOL_WATCH_STATE_DIR") or DEFAULT_STATE_DIR
    gate_path = os.environ.get("POOL_WATCH_RENDER_GATE") or DEFAULT_RENDER_GATE
    try:
        result = decide(spool, state_dir, gate_path)
    except Exception as exc:  # never break the wrapper's JSON parse
        result = {"decision": "silent", "reason": "check_failed",
                  "payload": None,
                  "checks": {"error": "%s: %s" % (type(exc).__name__, exc)}}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
