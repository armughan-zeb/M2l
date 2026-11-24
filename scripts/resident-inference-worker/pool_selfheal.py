#!/usr/bin/env python3
"""Pool self-heal executor — the decision core for the pool-selfheal cron.

Root cause it exists for (2026-11-20): the supervisor loop
(``pool_supervisor.py``) can only QUEUE actions — no shell process can
spawn a Muse agent — and the agents the system spawns automatically to
drain that queue (hook-woken supervisors, depth 2) have NO subagent-spawn
tool (``can_spawn=no``). Supervisor be8934c6 wrote that into the action
queue itself; supervisor 761080e9 acked two pending spawn actions and
then spawned nothing, and the pool sat at 0 workers for ~84 minutes
(workers stopped 12:09 UTC for test 14's render, test 15's whole media
stage ran with no vision) while liveness stayed fresh. The ONLY agents
that have ever spawned pool workers are TOP-LEVEL agents: the parent
(spawned worker-1 + worker-2 by hand at ~13:35 UTC) — and, by the same
token, a top-level cron run (``execution.kind=agent``), which is not a
subagent of any session.

So this module is the executor's BRAIN, not its hands: it reads the
spool, the action queue, the render gate and the loop pid file, and
prints the exact plan a top-level executor agent must carry out —
which workerIds to spawn (with brief path + task line), which queued
actions to mark done/cancelled, and whether the loop needs restarting.
The cron agent (see ``POOL_SELFHEAL_CRON.md``) runs this every 2
minutes, executes the plan with its own spawn tools, and marks each
action done. This script itself never spawns an agent, never starts a
loop, never touches the spool except for the explicit ``--mark-*`` and
lock commands — ``--check`` / ``--dry-run`` are strictly read-only.

WHO EXECUTES SPAWNS (2026-11-21, pool scale-up fix): the plan's
``spawns`` are PARENT-ONLY (``spawn_executor: "parent-only"``). A
top-level cron run DOES have the spawn tool (proven 2026-11-20) — but
workers it spawns DIE at the cron run's 300s timeout (proven
2026-11-21: self-heal run f0a5782e spawned worker-1 + worker-2, both
dead at the timeout with 0 answers), so a cron run must NEVER execute
a spawn: it would staff the pool with workers that die mid-answer.
The ONLY spawner whose workers survive is a top-level PARENT agent
spawning directly. The cron's job for spawns is therefore to write
the scale-up signal (``--write-scaleup-signal`` — the same
``supervisor-scaleup-needed.json`` the loop writes, so the watch hook
wakes the parent within ~60s) and report the spawn list to the
parent, never to spawn. The same applies to the loop: a cron-started
loop dies ~1 minute after its run ends, so a cron loop restart is a
stopgap that re-queues and re-signals, and the parent must start the
loop directly for stability. Do NOT "fix" this by re-enabling cron
spawns: there is no shell mechanism that makes a cron run's
descendants survive the run.

Guards (all in ``plan()``, all tested in ``test_pool_selfheal.py``):
  - Parked (``supervisor-parked.json`` present): empty plan. A deliberate
    parent stop is never healed over.
  - Render ACTIVE: no spawns, ever. The loop may still be restarted
    (it respects the gate itself and queues stop_graceful, never spawns).
  - Duplicate-supervisor guard: the loop is restarted only when its pid
    file names a dead pid, is missing, or has not been rewritten in
    >120s (the loop rewrites it every tick; a fresh pid file naming a
    live pid means a loop is already running — starting a second one
    would fight the handover). An executor lock file
    (``selfheal-lock.json`` in the state dir, stale after 300s) keeps
    two concurrent executor runs from double-spawning the same slot.
  - Adopt, never duplicate: a workerId with a fresh heartbeat (<90s)
    is live and is never spawned over; pending spawn/respawn actions
    for it are marked done/cancelled instead.
  - Target from the one scaling rule (``pool_scaling.scaling_target``):
    idle 2, media floor 4, ceiling 8; render 0. A held slot (3 deaths
    in 30 min, in ``supervisor-state.json``) is never spawned — but it
    no longer shrinks the target (2026-11-21, count-based scale-up):
    the pool fills to the FULL target via higher free slots. A queued
    spawn/respawn is cancelled only when the pool is back at target
    (live >= target), never for its slot NUMBER: a slot above target
    is owed whenever the pool is short (a held lower slot is exactly
    how a high slot becomes owed), and a cancellation made at idle
    target 2 must not block scale-up after the target rises.
  - Scale-down grace: a ``stop_idle`` action is only executed (marked
    for the worker to see — workers self-execute stops by polling the
    queue, proven by ``worker.log`` "STOP action seen") when no worker
    has answered in the last 300s; a lull mid-job is not idle.
  - Stops are self-executing; the executor only MARKS them done once
    the worker's heartbeat has gone stale (the worker stopped). A
    ``replace_stuck`` / ``rotate`` action needs a fresh spawn of the
    same workerId AFTER the old worker stops (plus the pkill commands
    in the action's detail for a detached heartbeat loop).

Run:
  python3 pool_selfheal.py [--check | --dry-run] [--spool DIR]
                           [--state-dir DIR] [--gate PATH]
  python3 pool_selfheal.py --write-scaleup-signal [--agent-id <id>]
  python3 pool_selfheal.py --acquire-lock --agent-id <id>
  python3 pool_selfheal.py --release-lock --agent-id <id>
  python3 pool_selfheal.py --mark-done <action-id> --agent-id <id>
  python3 pool_selfheal.py --mark-cancelled <action-id> --agent-id <id>
                           --reason <why>
Exit: 0 normally; 2 when the plan says "do not act" for a hard reason
(parked, render-active spawns requested) — the plan JSON still prints.

``--write-scaleup-signal`` writes ``supervisor-scaleup-needed.json``
into the spool from the computed plan (spawns owed + what blocks the
rest) when the gate is idle and spawns are planned, and clears it
otherwise. It is how the cron summons the parent when the loop is
dead (no loop = no loop-written signal): the watch hook wakes the
parent within ~60s of a fresh signal. It writes a file, never spawns.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from pool_scaling import scaling_target
except Exception:  # pragma: no cover - fallback mirrors the old floor-2 rule
    def scaling_target(pending, render_active):
        if render_active:
            return 0
        pending = max(0, int(pending))
        if pending == 0:
            return 2
        return max(2, min(8, -(-pending // 3)))

# Signal helpers: a SEPARATE import with its own fallback, so a module
# that lacks them (staggered deploy) never nukes the scaling rule
# above — the two imports must not share one try block (2026-11-21).
try:
    from pool_scaling import (build_scaleup_signal,
                              clear_scaleup_signal, write_scaleup_signal)
except Exception:  # pragma: no cover - fallbacks mirror pool_scaling.py
    def build_scaleup_signal(live, target, pending, spawns, blocked=None,
                             supervisor_id=None, now_ms=None):
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        return {"updatedAt": int(now_ms),
                "supervisorAgentId": supervisor_id,
                "live": int(live), "target": int(target),
                "pending": int(pending),
                "spawns": [{"action": s.get("action"),
                            "actionId": s.get("id") or s.get("actionId"),
                            "workerId": s.get("workerId"),
                            "reason": s.get("reason")}
                           for s in (spawns or [])],
                "blocked": list(blocked or [])}

    def write_scaleup_signal(spool_dir, payload):
        path = os.path.join(spool_dir, "supervisor-scaleup-needed.json")
        tmp = "%s.tmp-scaleup-%d" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
        return path

    def clear_scaleup_signal(spool_dir):
        try:
            os.remove(os.path.join(
                spool_dir, "supervisor-scaleup-needed.json"))
        except OSError:
            pass


DEFAULT_SPOOL = os.path.expanduser(
    "~/workspace/vidnets-runtime/temp/inference-spool")
DEFAULT_STATE_DIR = os.path.expanduser("~/workspace/vidnets-runtime")
DEFAULT_GATE = os.path.expanduser(
    "~/workspace/M2l/vidnets/scripts/render-gate.js")
DEFAULT_BRIEF_DIR = os.path.expanduser(
    "~/workspace/M2l/scripts/resident-inference-worker")

HEARTBEAT_STALE_S = 90
LOOP_PID_STALE_S = 120
LOCK_STALE_S = 300
SCALE_DOWN_GRACE_S = 300
MAX_WORKERS = 8


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _atomic_write(path, payload):
    tmp = "%s.tmp-selfheal-%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


def render_active(gate_path):
    """True if a render is ACTIVE, False if idle, None if unprovable.

    Only exit code 2 means ACTIVE; any other failure fails OPEN to
    False with a note — a broken probe must not block pool recovery
    forever (same convention as pool_supervisor_check.py).
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


def live_workers(spool, now):
    """{workerId: heartbeat dict} for heartbeats <90s old."""
    out = {}
    for path in glob.glob(os.path.join(spool, "worker-alive-worker-*.json")):
        row = _read_json(path)
        if not isinstance(row, dict):
            continue
        updated = row.get("updatedAt")
        if not isinstance(updated, (int, float)):
            continue
        if -5.0 <= now - updated / 1000.0 < HEARTBEAT_STALE_S:
            wid = row.get("workerId") or os.path.basename(path)[
                len("worker-alive-"):-len(".json")]
            out[str(wid)] = row
    return out


def pending_count(spool, now):
    n = 0
    for path in glob.glob(os.path.join(spool, "*.request.json")):
        row = _read_json(path)
        if not isinstance(row, dict):
            n += 1  # unparseable: count it, the pool must see it
            continue
        inner = row.get("request") or row
        try:
            if float(inner.get("expires_at")) > now:
                n += 1
        except (TypeError, ValueError):
            n += 1
    return n


def load_actions(spool):
    doc = _read_json(os.path.join(spool, "supervisor-actions.json"))
    if isinstance(doc, dict) and isinstance(doc.get("actions"), list):
        return doc["actions"]
    return []


def held_slots(state_dir):
    doc = _read_json(os.path.join(state_dir, "supervisor-state.json"))
    if isinstance(doc, dict) and isinstance(doc.get("held_slots"), dict):
        return set(doc["held_slots"])
    return set()


def worker_answered_recent(spool, worker_id, since_s, now):
    """True if worker.log has an ANSWERED line for this worker in the
    window (same 300s scale-down grace the loop uses)."""
    path = os.path.join(spool, "worker.log")
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - 200000))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return False
    needle = "[%s] ANSWERED" % worker_id
    cutoff = now - since_s
    for line in tail.splitlines():
        if needle not in line:
            continue
        try:
            import calendar
            stamp = calendar.timegm(time.strptime(
                line[:19], "%Y-%m-%d %H:%M:%S"))
        except (ValueError, IndexError):
            continue
        if stamp >= cutoff:
            return True
    return False


def loop_status(state_dir):
    """(alive, pid, reason). The loop rewrites its pid file every tick,
    so a pid file older than LOOP_PID_STALE_S means no live loop even
    if the pid itself is still alive (wedged); a fresh file naming a
    live pid means a loop is running and must not be duplicated."""
    pid_file = os.path.join(state_dir, "supervisor-loop.pid")
    doc = _read_json(pid_file)
    if not isinstance(doc, dict) or not isinstance(doc.get("pid"), int):
        return False, None, "no_pid_file"
    try:
        age = time.time() - os.path.getmtime(pid_file)
    except OSError:
        return False, doc.get("pid"), "no_pid_file"
    if age >= LOOP_PID_STALE_S:
        return False, doc.get("pid"), "pid_file_stale"
    if not _pid_alive(doc["pid"]):
        return False, doc.get("pid"), "pid_dead"
    return True, doc["pid"], "live"


def lock_status(state_dir, now):
    path = os.path.join(state_dir, "selfheal-lock.json")
    doc = _read_json(path)
    if isinstance(doc, dict) and isinstance(doc.get("startedAtMs"),
                                           (int, float)):
        if now - doc["startedAtMs"] / 1000.0 < LOCK_STALE_S:
            return True, doc.get("agentId")
    return False, None


def acquire_lock(state_dir, agent_id, now):
    held, holder = lock_status(state_dir, now)
    if held and holder != agent_id:
        return False, holder
    _atomic_write(os.path.join(state_dir, "selfheal-lock.json"),
                  {"agentId": agent_id, "startedAtMs": int(now * 1000)})
    return True, agent_id


def release_lock(state_dir, agent_id):
    path = os.path.join(state_dir, "selfheal-lock.json")
    doc = _read_json(path)
    if isinstance(doc, dict) and doc.get("agentId") == agent_id:
        try:
            os.remove(path)
        except OSError:
            pass
        return True
    return False


def mark_action(spool, action_id, status, agent_id, note=""):
    """Mark one queued action done/cancelled (atomic). Returns True if
    the action existed and was pending."""
    path = os.path.join(spool, "supervisor-actions.json")
    doc = _read_json(path)
    if not isinstance(doc, dict) or not isinstance(doc.get("actions"), list):
        return False
    changed = False
    for action in doc["actions"]:
        if action.get("id") == action_id and action.get("status") == "pending":
            action["status"] = status
            action["detail"] = dict(action.get("detail") or {})
            action["detail"]["doneBy"] = "pool-selfheal %s" % agent_id
            if note:
                action["detail"]["note"] = note
            action["completedAtMs"] = int(time.time() * 1000)
            changed = True
    if changed:
        _atomic_write(path, doc)
    return changed


def slot_number(worker_id):
    try:
        if worker_id.startswith("worker-"):
            return int(worker_id[len("worker-"):])
    except ValueError:
        pass
    return None


def plan(spool, state_dir, gate_path, brief_dir, now=None,
         executor_id="selfheal-check"):
    """The executor plan. Pure decision: reads files, writes nothing
    (except nothing — even the lock is only *reported* here; the caller
    acquires it with acquire_lock)."""
    now = time.time() if now is None else now
    out = {
        "parked": False,
        "render_active": None,
        "render_gate_note": None,
        "live": [],
        "target": None,
        "pending": 0,
        "loop": {"alive": False, "pid": None, "restart_needed": False,
                 "restart_reason": None,
                 "start_command": None},
        "lock_held": False,
        "lock_holder": None,
        "spawns": [],
        # Spawns are PARENT-ONLY (2026-11-21): only a top-level parent
        # agent spawning directly produces workers that survive.
        # Cron-spawned workers die at the 300s timeout, so a cron run
        # must NEVER execute a spawn — it writes the scale-up signal
        # instead (--write-scaleup-signal) and reports the spawn list
        # to the parent. This field is the plan's own warning label.
        "spawn_executor": "parent-only",
        "mark_done": [],
        "mark_cancelled": [],
        "reasons": [],
    }

    if os.path.exists(os.path.join(spool, "supervisor-parked.json")):
        out["parked"] = True
        out["reasons"].append(
            "supervisor parked by the parent — deliberate stop, no healing")
        return out

    gate = render_active(gate_path)
    out["render_active"] = gate
    if gate is None:
        out["render_gate_note"] = (
            "render gate unprovable (node/gate error); failing open to idle")
    active = gate is True

    live = live_workers(spool, now)
    out["live"] = sorted(live)
    pending = pending_count(spool, now)
    out["pending"] = pending
    target = scaling_target(pending, active)
    out["target"] = target

    alive, pid, reason = loop_status(state_dir)
    out["loop"] = {"alive": alive, "pid": pid,
                   "restart_needed": not alive,
                   "restart_reason": None if alive else reason,
                   "start_command": None if alive else (
                       "cd %s && POOL_SUPERVISOR_AGENT_ID=<your-agent-id> "
                       "python3 %s" % (
                           state_dir,
                           os.path.join(brief_dir, "pool_supervisor.py")))}
    if not alive:
        out["reasons"].append("supervisor loop %s — restart needed" % reason)

    held, holder = lock_status(state_dir, now)
    out["lock_held"] = held
    out["lock_holder"] = holder
    if held and holder != executor_id:
        out["reasons"].append(
            "executor lock held by %s — this run must not spawn" % holder)
        return out  # do not plan spawns under another executor's lock

    actions = load_actions(spool)
    pending_actions = [a for a in actions if a.get("status") == "pending"]
    held_ids = held_slots(state_dir)
    brief_path = os.path.join(brief_dir, "WORKER_BRIEF.md")

    def spawn_entry(wid, action_id, why, after_stop=False, pkill=None):
        entry = {
            "workerId": wid,
            "briefPath": brief_path,
            "taskLine": "Your workerId is %s. Spool dir is %s."
                        % (wid, spool),
            "actionId": action_id,
            "reason": why,
            "after_stop": after_stop,
        }
        if pkill:
            entry["pkill_detached_heartbeat"] = pkill
        return entry

    # 1. Execute pending spawn-type actions the loop queued.
    spawned_ids = set()
    additive_spawns = 0  # spawns that ADD a worker (spawn/respawn); a
    # replace_stuck/rotate spawn replaces a live worker and does not
    # count toward the scale-up need below.
    for action in pending_actions:
        act, wid = action.get("action"), action.get("workerId")
        if not isinstance(wid, str):
            continue
        if act in ("spawn", "respawn"):
            if wid in live:
                out["mark_cancelled"].append({
                    "id": action.get("id"), "workerId": wid,
                    "reason": "worker live again — spawn no longer owed"})
                continue
            if active:
                continue  # render: reconcile will cancel; never spawn
            if wid in held_ids:
                out["mark_cancelled"].append({
                    "id": action.get("id"), "workerId": wid,
                    "reason": "slot held after 3 deaths — never spawned; "
                              "the pool fills to target via other slots"})
                continue
            if len(live) >= target:
                # Count-based (2026-11-21): a queued spawn/respawn is
                # owed whenever the pool is short, whatever its slot
                # NUMBER. The old slot-number cancel ("slot above
                # target") deleted a respawn cancelled at idle target 2
                # and blocked scale-up after the target rose — a held
                # lower slot is exactly how a high slot becomes owed.
                # Only a pool back at target cancels the action.
                out["mark_cancelled"].append({
                    "id": action.get("id"), "workerId": wid,
                    "reason": "pool at target (%d/%d) — spawn no longer "
                              "owed" % (len(live), target)})
                continue
            if wid not in spawned_ids:
                out["spawns"].append(spawn_entry(
                    wid, action.get("id"),
                    "queued %s: %s" % (act, action.get("reason"))))
                spawned_ids.add(wid)
                additive_spawns += 1
        elif act in ("replace_stuck", "rotate"):
            if wid not in live:
                # Old worker already gone: the replacement is a plain
                # (additive) spawn. Do NOT mark the action done here —
                # the executor marks it done after the replacement is
                # spawned (its actionId rides on the spawn entry), so a
                # crash between planning and spawning cannot lose the
                # replacement: the next run re-plans it from live<target.
                if not active and wid not in spawned_ids:
                    out["spawns"].append(spawn_entry(
                        wid, action.get("id"),
                        "queued %s, old worker already gone"
                        % act))
                    spawned_ids.add(wid)
                    additive_spawns += 1
            else:
                # Old worker still live: spawn AFTER it stops (the stop
                # is self-executing — the worker polls the queue).
                if not active and wid not in spawned_ids:
                    detail = action.get("detail") or {}
                    out["spawns"].append(spawn_entry(
                        wid, action.get("id"),
                        "queued %s: replacement after old worker stops"
                        % act, after_stop=True,
                        pkill=detail.get("pkill")))
                    spawned_ids.add(wid)
        elif act in ("stop_idle", "stop_graceful"):
            if wid not in live:
                out["mark_done"].append({
                    "id": action.get("id"), "workerId": wid,
                    "reason": "worker already stopped"})
            elif act == "stop_idle":
                # Scale-down grace: only stop an idle worker when the
                # pool has been truly quiet (no answer in 300s).
                if not worker_answered_recent(spool, wid,
                                              SCALE_DOWN_GRACE_S, now):
                    out["reasons"].append(
                        "stop_idle %s owed (pool quiet >300s); worker "
                        "self-executes the stop from the queue" % wid)
                else:
                    out["mark_cancelled"].append({
                        "id": action.get("id"), "workerId": wid,
                        "reason": "scale-down grace (300s): a worker "
                                  "answered recently; job still in media"})

    # 2. Scale-up need beyond the queued actions (e.g. loop dead, or a
    # render just ended and the loop has not re-queued yet).
    if not active:
        incoming = additive_spawns
        # Count-based scale-up (2026-11-21): fill to the FULL target via
        # any free non-held slot. A held slot is skipped, never
        # subtracted from the target — the old target-minus-one rule
        # plus the slot-number cap let a held slot + a flapping worker
        # leave the pool short with nothing queued.
        needed = target - len(live) - incoming
        if needed > 0:
            for i in range(1, MAX_WORKERS + 1):
                if needed <= 0:
                    break
                wid = "worker-%d" % i
                if (wid not in live
                        and wid not in held_ids
                        and wid not in spawned_ids):
                    out["spawns"].append(spawn_entry(
                        wid, None,
                        "live=%d target=%d pending=%d — scale-up"
                        % (len(live), target, pending)))
                    spawned_ids.add(wid)
                    needed -= 1

    # 3. Freshness check: re-probe the render gate right before
    # finalizing any spawn. The gate is a point-in-time read and a
    # render can start (or resume after a reboot) between the first
    # probe and the spawn decision — the 08:46 Nov 21 run read
    # render_active=false while test 19's render was resuming and
    # spawned two workers the loop then had to stop. If the fresh
    # probe says a render is active, drop every spawn; the render
    # owns the machine and the next run re-plans from live<target.
    if out["spawns"]:
        fresh_gate = render_active(gate_path)
        if fresh_gate is True:
            out["render_active"] = True
            out["render_gate_note"] = (
                "render gate re-probe before spawn says ACTIVE — "
                "dropping %d planned spawn(s); render owns the machine"
                % len(out["spawns"]))
            out["spawns"] = []
            out["reasons"].append(
                "freshness re-probe: render started since the first "
                "gate read — no spawns this run")

    if out["spawns"]:
        out["reasons"].append(
            "spawns are parent-only: a top-level parent agent must "
            "execute them — cron-spawned workers die at the 300s "
            "timeout (2026-11-21), so a cron run must NOT spawn "
            "workers; it writes the scale-up signal instead "
            "(--write-scaleup-signal)")

    if not out["spawns"] and not out["mark_done"] \
            and not out["mark_cancelled"] and alive:
        out["reasons"].append("pool healthy: live=%d target=%d pending=%d"
                              % (len(live), target, pending))
    return out


def write_scaleup_signal_from_plan(spool, state_dir, gate_path, brief_dir,
                                   executor_id="selfheal-check", now=None):
    """Write (or clear) the scale-up signal from a computed plan.

    How the cron summons the parent when the loop is dead (no loop =
    no loop-written signal): if the plan has spawns and the gate is
    idle, write ``supervisor-scaleup-needed.json`` with the owed
    workerIds; otherwise clear it. Writes a file, never spawns.
    Returns the signal payload (plus ``signalled``) that was written,
    or ``{"signalled": False, ...}`` when cleared.
    """
    now = time.time() if now is None else now
    result = plan(spool, state_dir, gate_path, brief_dir, now=now,
                  executor_id=executor_id)
    if result["parked"] or result["render_active"] is True \
            or not result["spawns"]:
        clear_scaleup_signal(spool)
        return {"signalled": False,
                "parked": result["parked"],
                "render_active": result["render_active"],
                "live": result["live"], "target": result["target"],
                "pending": result["pending"],
                "reason": "no spawns owed — signal cleared"}
    queued = {a.get("id"): a.get("action")
              for a in load_actions(spool)}
    signal_spawns = []
    for spawn in result["spawns"]:
        action = queued.get(spawn.get("actionId")) or "spawn"
        signal_spawns.append({
            "action": action,
            "id": spawn.get("actionId"),
            "workerId": spawn.get("workerId"),
            "reason": spawn.get("reason"),
        })
    blocked = []
    shortfall = (result["target"] - len(result["live"])
                 - len(result["spawns"]))
    if shortfall > 0:
        held = held_slots(state_dir)
        if held:
            blocked.append("held slots (3 deaths in 30 min): %s"
                           % ", ".join(sorted(held)))
    payload = build_scaleup_signal(
        len(result["live"]), result["target"], result["pending"],
        signal_spawns, blocked=blocked, supervisor_id=executor_id,
        now_ms=int(now * 1000))
    write_scaleup_signal(spool, payload)
    payload["signalled"] = True
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Pool self-heal executor (decision core).")
    parser.add_argument("--check", action="store_true",
                        help="print the plan (read-only, default)")
    parser.add_argument("--dry-run", action="store_true",
                        help="alias for --check: print the plan, change "
                             "nothing")
    parser.add_argument("--spool", default=None)
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--gate", default=None)
    parser.add_argument("--brief-dir", default=None)
    parser.add_argument("--agent-id", default=None)
    parser.add_argument("--acquire-lock", action="store_true")
    parser.add_argument("--release-lock", action="store_true")
    parser.add_argument("--write-scaleup-signal", action="store_true",
                        help="write supervisor-scaleup-needed.json from "
                             "the computed plan (cron summoning the "
                             "parent when the loop is dead); writes a "
                             "file, never spawns")
    parser.add_argument("--mark-done", default=None, metavar="ACTION-ID")
    parser.add_argument("--mark-cancelled", default=None, metavar="ACTION-ID")
    parser.add_argument("--reason", default="")
    parser.add_argument("--note", default="")
    args = parser.parse_args(argv)

    spool = args.spool or os.environ.get(
        "MUSE_INFERENCE_SPOOL_DIR") or DEFAULT_SPOOL
    state_dir = args.state_dir or os.environ.get(
        "POOL_SUPERVISOR_STATE_DIR") or DEFAULT_STATE_DIR
    gate = args.gate or os.environ.get(
        "POOL_WATCH_RENDER_GATE") or DEFAULT_GATE
    brief_dir = args.brief_dir or os.environ.get(
        "POOL_SUPERVISOR_BRIEF_DIR") or DEFAULT_BRIEF_DIR
    now = time.time()

    if args.acquire_lock:
        if not args.agent_id:
            parser.error("--acquire-lock needs --agent-id")
        ok, holder = acquire_lock(state_dir, args.agent_id, now)
        print(json.dumps({"acquired": ok, "holder": holder}, indent=2))
        return 0 if ok else 1
    if args.release_lock:
        if not args.agent_id:
            parser.error("--release-lock needs --agent-id")
        print(json.dumps({"released": release_lock(state_dir,
                                                  args.agent_id)}, indent=2))
        return 0
    if args.mark_done:
        if not args.agent_id:
            parser.error("--mark-done needs --agent-id")
        print(json.dumps({"marked": mark_action(
            spool, args.mark_done, "done", args.agent_id, args.note)},
            indent=2))
        return 0
    if args.mark_cancelled:
        if not args.agent_id:
            parser.error("--mark-cancelled needs --agent-id")
        print(json.dumps({"marked": mark_action(
            spool, args.mark_cancelled, "cancelled", args.agent_id,
            args.reason)}, indent=2))
        return 0
    if args.write_scaleup_signal:
        print(json.dumps(write_scaleup_signal_from_plan(
            spool, state_dir, gate, brief_dir,
            executor_id=args.agent_id or "selfheal-check", now=now),
            indent=2))
        return 0

    result = plan(spool, state_dir, gate, brief_dir, now=now,
                  executor_id=args.agent_id or "selfheal-check")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
