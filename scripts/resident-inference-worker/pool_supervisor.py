#!/usr/bin/env python3
"""Resident inference pool supervisor — the loop that CAN act.

Supersedes the runtime-only ``supervisor_tick.py`` (which was never in the
repo and could only log). This loop still cannot spawn a Muse agent itself
— no shell process can; only an agent session can spawn a subagent — so it
acts through a durable ACTION QUEUE instead of prose in a log:

* Every needed action (respawn a dead worker, spawn for backlog, stop an
  idle worker, replace a stuck worker, rotate a stale session) is written
  as a pending entry in ``<spool>/supervisor-actions.json`` with the full
  spawn context (workerId, brief path, spool dir, reason). The supervisor
  agent — or any agent the parent wakes — drains the queue, one action at
  a time, and marks each done. An action the loop only logged used to die
  with its owner; a queued action survives any owner and is the unit the
  next supervisor picks up first.

* Liveness is written by the thing that can act, and it STOPS vouching
  when the owner stops acting. The old loop wrote ``pool-status.json`` /
  ``supervisor-alive.json`` forever under a supervisor agent id while that
  agent spawned zero workers and acted on zero deaths for 31 hours
  (agent f4be9b5d, created 2026-11-18 ~03:19 UTC: status "running" in the
  DB, zero subagent spawns, no runtime state — while the loop logged
  WORKER DEAD worker-1 #1/#2/#3 and WORKER DEAD worker-2 #1 and nobody
  respawned anything; the workers recovered on their own). The watch hook
  never fired because the files stayed fresh. Now: while the action queue
  is empty the loop writes liveness normally (an idle owner owes nothing).
  Once an action sits pending longer than OWNER_ACTION_TIMEOUT_S (default
  600s) with no agent ack since it was queued, the loop enters
  owner-unresponsive mode: it STOPS writing both liveness files (so the
  pool-supervisor-watch hook wakes the parent within ~120s), keeps
  sweeping and queueing, and writes ``supervisor-owner-unresponsive.json``
  explaining why. An ack is only a LEASE of OWNER_ACK_LEASE_S (default
  180s): if the action is still pending when the lease expires, the
  owner is unresponsive again — an ack must never silence the tripwire
  forever, because on 2026-11-20 supervisor 761080e9 acked two pending
  spawn actions and then spawned nothing (hook-spawned agents have no
  subagent-spawn tool), and the pool sat at 0 workers for ~84 minutes
  while liveness stayed fresh. When the queue drains, liveness resumes.

* Death confirmation grace. The brief calls a worker dead at a heartbeat
  >90s stale, but a worker's poll loop ends every ~90s and its agent
  restarts it on the next turn, so a healthy worker's heartbeat can gap
  ~2 minutes between turns (2026-11-19 06:22 UTC: worker-2 was logged
  WORKER DEAD during exactly such a gap, and its own log line says "I was
  alive the whole time"). A first sighting is therefore only recorded at
  >90s stale; the death is CONFIRMED — and only then queued — when the
  heartbeat is still stale DEATH_CONFIRM_S (default 180s) after the first
  sighting. A heartbeat that freshens in between clears the sighting.
  At confirmation the loop also cross-checks worker.log for fresh
  ANSWERED lines (false-death guard): a worker that answered in the last
  10 minutes is busy, not dead. And one continuous outage counts as ONE
  death (confirmed_dead): without that, a 31h-dead worker re-confirms
  every ~3 min and trips the three-death hold on a single outage.
* Slot rule (standing policy): a dead worker is respawned only if its
  slot number is within the CURRENT target (target =
  clamp(ceil(pending/3), 2, 8)). At idle target 2, worker-3 is not
  respawned; its slot is reserved for scale-up.

* Loop handover. The loop writes ``supervisor-loop.pid`` (its pid, owner
  agent id, start time) into the state dir at startup. Each tick it
  re-reads that file: if it names a different LIVE pid, another loop has
  taken over and this one exits cleanly. A replacement supervisor takes
  over by starting its own loop, which overwrites the pid file; the old
  loop sees the foreign live pid on its next tick and exits. A pid file
  naming a dead pid, or older than 120s, is treated as a dead loop and
  overwritten.

Everything else follows POOL_SUPERVISOR_BRIEF.md: target comes from
``pool_scaling.scaling_target`` (media floor 4, idle floor 2, ceiling 8);
same-workerId respawn; render ACTIVE -> target 0 and no spawns/respawns; 60s sweep (expired requests/claims
deleted, stale claims of dead workers rescued after the 300s grace);
30s status writes (this loop ticks every 15s, so both liveness files are
rewritten every tick); supervisor never answers inference requests
(this file contains no inference code at all); a slot that dies three
times within 30 minutes is held (pool runs at target-minus-one) until a
live heartbeat reappears or an hour passes.

Run:  python3 pool_supervisor.py [--once] [--shadow]
  --once    run a single tick then exit (tests / dry runs).
  --shadow  read-only: compute and print the tick decision as JSON,
            write nothing (not even state). Safe against the live spool.
Exit: 0 normally; exits promptly if supervisor-parked.json appears
(deliberate parent stop) or another loop takes over.
"""
import glob
import json
import os
import subprocess
import sys
import time

SPOOL = os.environ.get(
    "MUSE_INFERENCE_SPOOL_DIR",
    os.path.expanduser("~/workspace/vidnets-runtime/temp/inference-spool"),
)
STATE_DIR = os.environ.get(
    "POOL_SUPERVISOR_STATE_DIR",
    os.path.expanduser("~/workspace/vidnets-runtime"),
)
RENDER_GATE = os.environ.get(
    "POOL_SUPERVISOR_RENDER_GATE",
    os.path.expanduser("~/workspace/M2l/vidnets/scripts/render-gate.js"),
)
# Agent id of the supervisor agent that owns this loop. Passed at deploy
# time (deploy script / supervisor agent). Never hardcode a past id: a
# hardcoded id is exactly how a dead agent kept "looking alive".
SUPERVISOR_ID = os.environ.get("POOL_SUPERVISOR_AGENT_ID", "unknown")
BRIEF_DIR = os.environ.get(
    "POOL_SUPERVISOR_BRIEF_DIR",
    os.path.expanduser("~/workspace/M2l/scripts/resident-inference-worker"),
)
TICK_S = float(os.environ.get("POOL_SUPERVISOR_TICK_S", "15"))
HEARTBEAT_STALE_S = 90
DEATH_CONFIRM_S = float(os.environ.get("POOL_SUPERVISOR_DEATH_CONFIRM_S", "180"))
CLAIM_RESCUE_GRACE_S = 300
OWNER_ACTION_TIMEOUT_S = float(
    os.environ.get("POOL_SUPERVISOR_OWNER_TIMEOUT_S", "600"))
# Ack lease (2026-11-20 root-cause fix). An ack file is a claim of intent
# ("I am draining the queue"), NOT proof of execution: on 2026-11-20 a
# hook-spawned supervisor (761080e9) acked two pending spawn actions at
# 13:29:37 UTC, the tripwire treated the ack as "owner responsive" and
# resumed liveness writes, and the hook went silent — while the acking
# agent never spawned anything (a depth-2 subagent has no subagent-spawn
# tool, can_spawn=no) and the pool sat at 0 workers through test 15's
# whole media stage. So an ack only buys OWNER_ACK_LEASE_S: if the
# action is STILL pending when the lease expires, the owner is
# unresponsive again and liveness pauses again, so the watch hook
# re-wakes the parent. Only a drained queue (or a live worker for a
# spawn) clears the tripwire for good.
OWNER_ACK_LEASE_S = float(
    os.environ.get("POOL_SUPERVISOR_OWNER_ACK_LEASE_S", "180"))
STUCK_FROZEN_S = 600
ROTATE_AGE_S = 12 * 3600
ROTATE_ANSWERS = 50
THREE_DEATHS_WINDOW_S = 30 * 60
HOLD_S = 3600
# Scale-down grace: do not stop an idle worker merely because pending
# hit 0 for a moment mid-job. Only scale down when NO worker has
# answered anything in the last SCALE_DOWN_GRACE_S seconds — i.e. the
# pool is truly idle, not between bursts of one running job. Without
# this, a media burst (workers spawned to 4) is followed by a lull
# (pending 0 while the job downloads/transcripts) and the pool scales
# back to 2, only to re-spawn for the next burst — churn that slows
# the very vision the 4-worker floor is meant to speed up.
SCALE_DOWN_GRACE_S = 300
MAX_WORKERS = 8
MIN_WORKERS = 2

# Scaling rule: pool_scaling.py is the single testable statement of the
# rule (media floor 4, idle floor 2, ceiling 8). Raised 2 -> 4 for the
# media phase on 2026-11-20 (batched vision files requests in parallel).
# Falls back to the old floor-2 rule if the module cannot be imported.
try:
    sys.path.insert(0, BRIEF_DIR)
    from pool_scaling import scaling_target as _scaling_target
except Exception:
    def _scaling_target(pending, render_active):
        if render_active:
            return 0
        pending = max(0, int(pending))
        if pending == 0:
            return MIN_WORKERS
        return max(MIN_WORKERS, min(MAX_WORKERS, -(-pending // 3)))

LOOP_LOG = os.path.join(STATE_DIR, "supervisor-loop.log")
STATE_FILE = os.path.join(STATE_DIR, "supervisor-state.json")
PID_FILE = os.path.join(STATE_DIR, "supervisor-loop.pid")


def _now_s():
    return time.time()


def _now_ms():
    return int(time.time() * 1000)


class Supervisor:
    """One supervisor loop. ``clock`` is injectable for tests."""

    def __init__(self, spool=SPOOL, state_dir=STATE_DIR,
                 render_gate=RENDER_GATE, supervisor_id=SUPERVISOR_ID,
                 brief_dir=BRIEF_DIR, clock=None):
        self.spool = spool
        self.state_dir = state_dir
        self.render_gate = render_gate
        self.supervisor_id = supervisor_id
        self.brief_dir = brief_dir
        self.clock = clock or _now_s
        self.loop_log = os.path.join(state_dir, "supervisor-loop.log")
        self.state_file = os.path.join(state_dir, "supervisor-state.json")
        self.pid_file = os.path.join(state_dir, "supervisor-loop.pid")
        self.state = self._load_state()
        self.n = 0

    # -- small helpers -------------------------------------------------
    def now_ms(self):
        return int(self.clock() * 1000)

    def log(self, msg):
        line = "%s [supervisor] %s" % (
            time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(self.clock())),
            msg,
        )
        try:
            with open(self.loop_log, "a") as f:
                f.write(line + "\n")
        except OSError:
            pass
        print(line, flush=True)

    def spool_log(self, msg):
        line = "%s [supervisor] %s" % (
            time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(self.clock())),
            msg,
        )
        try:
            with open(os.path.join(self.spool, "worker.log"), "a") as f:
                f.write(line + "\n")
        except OSError:
            pass

    @staticmethod
    def atomic_write(path, payload):
        tmp = "%s.tmp-sup-%d" % (path, os.getpid())
        with open(tmp, "w") as f:
            f.write(json.dumps(payload))
        os.rename(tmp, path)

    def _read_json(self, path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return None

    def _load_state(self):
        st = self._read_json(self.state_file)
        return st if isinstance(st, dict) else {}

    def _save_state(self):
        try:
            self.atomic_write(self.state_file, self.state)
        except OSError as e:
            self.log("state save failed: %s" % e)

    # -- spool reads ---------------------------------------------------
    def render_active(self):
        try:
            r = subprocess.run(
                ["node", self.render_gate, "status"],
                capture_output=True, text=True, timeout=20,
            )
            out = (r.stdout + r.stderr).upper()
            if "NO RENDER ACTIVE" in out:
                return False
            if "ACTIVE" in out:
                return True
            return r.returncode == 2
        except Exception as e:
            self.log("render-gate check failed (%s); assuming idle" % e)
            return False

    def heartbeats(self):
        out = {}
        for path in glob.glob(
                os.path.join(self.spool, "worker-alive-worker-*.json")):
            base = os.path.basename(path)
            wid = base[len("worker-alive-"):-len(".json")]
            data = self._read_json(path)
            if data and isinstance(data.get("updatedAt"), (int, float)):
                out[wid] = (int(data["updatedAt"]), data)
        return out

    def live_workers(self, hb):
        now = self.now_ms()
        return {wid: v for wid, v in hb.items()
                if now - v[0] < HEARTBEAT_STALE_S * 1000}

    def claim_files(self):
        return glob.glob(
            os.path.join(self.spool, "*.request.json.claimed-*"))

    def _request_expires(self, path):
        data = self._read_json(path)
        if not data:
            return None
        req = data.get("request", data)
        try:
            return float(req.get("expires_at"))
        except (TypeError, ValueError):
            return None

    def pending_count(self):
        now = self.clock()
        n = 0
        for path in glob.glob(os.path.join(self.spool, "*.request.json")):
            exp = self._request_expires(path)
            if exp is not None and exp > now:
                n += 1
        return n

    def worker_log_recent(self, worker_id, since_s, needle=None):
        """True if worker.log has a line for this worker in the window.
        With needle="ANSWERED", only answer lines count (the false-death
        guard: a worker that answered recently is busy, not dead)."""
        path = os.path.join(self.spool, "worker.log")
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                f.seek(max(0, size - 200000))
                tail = f.read().decode("utf-8", "replace")
        except OSError:
            return False
        needle = "[%s]" % worker_id if needle is None else \
            "[%s] %s" % (worker_id, needle)
        cutoff = self.clock() - since_s
        for line in tail.splitlines():
            if needle not in line:
                continue
            # Lines start "YYYY-MM-DD HH:MM:SS UTC [...]".
            try:
                import calendar
                ts = time.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
                if calendar.timegm(ts) >= cutoff:
                    return True
            except (ValueError, OverflowError):
                continue
        return False

    def worker_log_answered_recent(self, worker_id, since_s):
        """False-death guard: True if the worker ANSWERED anything in the
        window. A heartbeat starves while a worker answers back-to-back
        (seen 4x for worker-1/2), so a stale heartbeat with fresh ANSWERED
        lines is a busy worker, not a dead one."""
        return self.worker_log_recent(worker_id, since_s, needle="ANSWERED")

    def _worker_log_has_stop(self, worker_id, action_id=None,
                             since_s=STUCK_FROZEN_S):
        """True if worker.log has a STOP/STOPPED line for this worker in
        the window. With action_id, the line must name that action (a
        worker logs STOP/STOPPED with the action's id when it obeys a
        stop); without one, any stop_graceful line for the worker
        counts. One substring covers both spellings: on 2026-11-21
        worker-2 logged only STOP and worker-1 only STOPPED, and the
        brief's spelling is STOPPED.

        Note worker_log_recent() cannot do the id match: its needle is
        "[wid] STOP <id>", which is not a substring of a STOPPED line
        ("[wid] STOPPED <id>") — so this scans the tail directly.
        """
        path = os.path.join(self.spool, "worker.log")
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                f.seek(max(0, size - 200000))
                tail = f.read().decode("utf-8", "replace")
        except OSError:
            return False
        cutoff = self.clock() - since_s
        marker = "[%s]" % worker_id
        for line in tail.splitlines():
            if marker not in line or "STOP" not in line:
                continue
            if action_id is not None:
                if action_id not in line:
                    continue
            elif "stop_graceful" not in line:
                continue
            try:
                import calendar
                ts = time.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
                if calendar.timegm(ts) >= cutoff:
                    return True
            except (ValueError, OverflowError):
                continue
        return False

    def _worker_stop_seen(self, worker_id, actions):
        """True if the worker logged STOP/STOPPED for a stop_graceful
        action addressed to it (any status — the entry may already have
        been cancelled). The worker has announced its own exit."""
        ids = [a.get("id") for a in actions
               if a.get("action") == "stop_graceful"
               and a.get("workerId") == worker_id
               and isinstance(a.get("id"), str)]
        for aid in ids:
            if self._worker_log_has_stop(worker_id, aid):
                return True
        if not ids:
            return self._worker_log_has_stop(worker_id)
        return False

    @staticmethod
    def slot_number(worker_id):
        """worker-N -> N, else None. Slots are the scale-up currency: the
        pool fills lowest free slots first, up to the current target."""
        try:
            if worker_id.startswith("worker-"):
                return int(worker_id[len("worker-"):])
        except ValueError:
            pass
        return None

    # -- action queue --------------------------------------------------
    def _queue_path(self):
        return os.path.join(self.spool, "supervisor-actions.json")

    def load_actions(self):
        doc = self._read_json(self._queue_path())
        if isinstance(doc, dict) and isinstance(doc.get("actions"), list):
            return doc["actions"]
        return []

    def save_actions(self, actions):
        self.atomic_write(
            self._queue_path(),
            {"updatedAt": self.now_ms(), "actions": actions})

    def queue_action(self, actions, action, worker_id, reason, detail=None):
        """Append a pending action unless an identical one is pending."""
        for a in actions:
            if (a.get("status") == "pending"
                    and a.get("action") == action
                    and a.get("workerId") == worker_id):
                return a
        now = self.now_ms()
        entry = {
            "id": "%s-%s-%d" % (action, worker_id, now),
            "action": action,
            "workerId": worker_id,
            "reason": reason,
            "createdAtMs": now,
            "status": "pending",
            "briefPath": os.path.join(self.brief_dir, "WORKER_BRIEF.md"),
            "supervisorBriefPath": os.path.join(
                self.brief_dir, "POOL_SUPERVISOR_BRIEF.md"),
            "spoolDir": self.spool,
            "detail": detail or {},
        }
        actions.append(entry)
        self.spool_log("ACTION QUEUED %s %s: %s" % (action, worker_id, reason))
        self.log("action queued: %s %s (%s)" % (action, worker_id, reason))
        return entry

    def cancel_pending(self, actions, action, worker_id, reason):
        kept = []
        for a in actions:
            if (a.get("status") == "pending"
                    and a.get("action") == action
                    and a.get("workerId") == worker_id):
                self.spool_log("ACTION CANCELLED %s %s: %s"
                               % (action, worker_id, reason))
                continue
            kept.append(a)
        return kept

    def reconcile_actions(self, actions, live, target, active, held):
        """Cancel pending actions whose need has vanished this tick."""
        out = []
        for a in actions:
            if a.get("status") != "pending":
                # Tombstone pruning: a cancelled stop_* entry is audit,
                # not work — drop it once it is a day old so the queue
                # file does not grow one entry per render flap forever.
                # (mark_action() tombstones carry completedAtMs, not
                # cancelledAtMs, so fall back to that.)
                if a.get("status") == "cancelled" and a.get("action") \
                        in ("stop_idle", "stop_graceful"):
                    done_at = a.get("cancelledAtMs") or \
                        a.get("completedAtMs")
                    if isinstance(done_at, (int, float)) and \
                            self.now_ms() - done_at > 24 * 3600 * 1000:
                        self.spool_log(
                            "pruning cancelled %s %s tombstone (age >24h)"
                            % (a.get("action"), a.get("workerId")))
                        continue
                out.append(a)
                continue
            act, wid = a.get("action"), a.get("workerId")
            if act in ("spawn", "respawn") and wid in live:
                self.spool_log("ACTION CANCELLED %s %s: worker live again"
                               % (act, wid))
                continue
            if act in ("spawn", "respawn") and active:
                self.spool_log("ACTION CANCELLED %s %s: render active"
                               % (act, wid))
                continue
            if act == "spawn" and len(live) >= target:
                self.spool_log("ACTION CANCELLED spawn %s: pool at target"
                               % wid)
                continue
            if act in ("respawn",) and wid in held:
                self.spool_log("ACTION CANCELLED respawn %s: slot held"
                               % wid)
                continue
            if act in ("respawn",) and len(live) >= target:
                # Pool recovered to target (e.g. scale-up filled the slot):
                # a respawn for a slot above target is not owed.
                self.spool_log("ACTION CANCELLED respawn %s: pool back "
                               "at target" % wid)
                continue
            if act in ("respawn", "spawn"):
                # Slot rule: a slot above the CURRENT target is reserved
                # for scale-up. If the target fell (backlog drained), a
                # queued action for that slot is no longer owed.
                slot = self.slot_number(wid)
                if slot is not None and slot > target:
                    self.spool_log("ACTION CANCELLED %s %s: slot %d above "
                                   "current target %d (reserved for "
                                   "scale-up)" % (act, wid, slot, target))
                    continue
            if act in ("stop_idle", "stop_graceful") and not active \
                    and act == "stop_graceful":
                if self._worker_log_has_stop(wid, a.get("id")):
                    # The worker already saw this stop (STOP/STOPPED
                    # line with the action id in worker.log): it is
                    # exiting or has exited. Deleting the entry would
                    # hide the exit — only the log line would record the
                    # cancellation, and self-heal could never mark the
                    # stop done. Leave it pending; self-heal marks it
                    # done once the worker is no longer live (2026-11-21:
                    # both workers had read the stops before the render
                    # ended, and the deletion left the pool at 0 with no
                    # queued recovery).
                    self.spool_log(
                        "stop_graceful %s already seen by %s — leaving "
                        "pending for self-heal to mark done after exit"
                        % (a.get("id"), wid))
                    out.append(a)
                    continue
                # Not seen: cancel, but RETAIN the entry as a tombstone
                # (same shape mark_action() uses) instead of deleting
                # it, so the cancellation stays auditable in the queue
                # file. Scoped to stop_* only: spawn/respawn cancels
                # above still delete, so existing assertions on a
                # drained queue stay valid.
                now = self.now_ms()
                a["status"] = "cancelled"
                a["cancelledAtMs"] = now
                a["completedAtMs"] = now
                detail = dict(a.get("detail") or {})
                detail["cancelledReason"] = "render ended, worker stays"
                detail["doneBy"] = "pool-supervisor reconcile"
                a["detail"] = detail
                self.spool_log("ACTION CANCELLED stop_graceful %s: "
                               "render ended, worker stays (retained as "
                               "tombstone)" % wid)
                out.append(a)
                continue
            if act == "rotate" and wid not in live:
                continue  # worker already gone; rotation moot
            out.append(a)
        return out

    # -- owner responsiveness ------------------------------------------
    def owner_ack_doc(self):
        doc = self._read_json(
            os.path.join(self.spool, "supervisor-agent-ack.json"))
        return doc if isinstance(doc, dict) else None

    def owner_ack_ms(self):
        doc = self.owner_ack_doc()
        if doc is not None and isinstance(
                doc.get("updatedAt"), (int, float)):
            return int(doc["updatedAt"])
        return None

    def owner_unresponsive(self, actions):
        """True when a pending action is older than the timeout and no
        agent ack has arrived since it was queued — where an ack is a
        LEASE of OWNER_ACK_LEASE_S, not a cure (see the constant above).
        A fresh ack (within the lease, newer than the oldest pending
        action) means an owner is actively draining the queue, so the
        loop keeps vouching. A stale ack, an ack older than the action,
        or an ack that does not name the oldest pending action (when it
        lists actionIds) means nobody has executed anything: the
        tripwire fires again and liveness pauses, so the watch hook
        re-wakes the parent instead of staying silent on a hollow ack.
        """
        now = self.now_ms()
        pending = [a for a in actions if a.get("status") == "pending"]
        if not pending:
            return False
        oldest = min(a["createdAtMs"] for a in pending
                     if isinstance(a.get("createdAtMs"), (int, float)))
        if now - oldest < OWNER_ACTION_TIMEOUT_S * 1000:
            return False
        ack = self.owner_ack_doc()
        if ack is None:
            return True
        ack_ms = ack.get("updatedAt")
        if not isinstance(ack_ms, (int, float)):
            return True
        ack_ms = int(ack_ms)
        if ack_ms < oldest:
            return True  # ack predates the action: it cannot cover it
        if now - ack_ms > OWNER_ACK_LEASE_S * 1000:
            return True  # lease expired, action still pending: hollow ack
        ids = ack.get("actionIds")
        if isinstance(ids, list) and ids:
            oldest_id = min(
                pending,
                key=lambda a: a.get("createdAtMs", 0)).get("id")
            if oldest_id not in ids:
                return True  # ack names other actions, not the oldest
        return False

    # -- sweep -----------------------------------------------------------
    def sweep(self, hb, live):
        now = self.clock()
        for path in glob.glob(os.path.join(self.spool, "*.request.json")):
            exp = self._request_expires(path)
            if exp is not None and exp <= now:
                try:
                    os.remove(path)
                    self.spool_log("SWEEP deleted expired request %s"
                                   % os.path.basename(path))
                except OSError:
                    pass
        for path in self.claim_files():
            base = os.path.basename(path)
            wid = base.rsplit(".claimed-", 1)[-1]
            exp = self._request_expires(path)
            if exp is not None and exp <= now:
                try:
                    os.remove(path)
                    self.spool_log("SWEEP deleted expired claim %s" % base)
                except OSError:
                    pass
                continue
            if exp is None:
                continue
            try:
                age = now - os.path.getmtime(path)
            except OSError:
                continue
            if age < CLAIM_RESCUE_GRACE_S:
                continue
            worker_hb = hb.get(wid)
            worker_fresh = (worker_hb is not None
                            and self.now_ms() - worker_hb[0]
                            < HEARTBEAT_STALE_S * 1000)
            if worker_fresh or not live:
                continue
            dest = path.rsplit(".claimed-", 1)[0]
            try:
                os.rename(path, dest)
                self.spool_log(
                    "RESCUED stale claim %s (worker %s heartbeat stale, "
                    "claim %.0fs old) back to pool" % (base, wid, age))
            except OSError as e:
                self.log("rescue failed for %s: %s" % (base, e))

    # -- pid handover ----------------------------------------------------
    def claim_pid_file(self):
        """Return False if another live loop owns the pid file (exit)."""
        pid = os.getpid()
        try:
            doc = self._read_json(self.pid_file)
            if isinstance(doc, dict):
                other = doc.get("pid")
                mtime = os.path.getmtime(self.pid_file)
                if (isinstance(other, int) and other != pid
                        and self.clock() - mtime < 120):
                    try:
                        os.kill(other, 0)
                    except OSError:
                        pass  # dead pid: take over below
                    else:
                        return False
        except OSError:
            pass
        try:
            self.atomic_write(self.pid_file, {
                "pid": pid, "supervisorAgentId": self.supervisor_id,
                "startedAtMs": self.now_ms()})
        except OSError as e:
            self.log("pid file write failed: %s" % e)
        return True

    # -- the tick --------------------------------------------------------
    def tick(self, n):
        if os.path.exists(os.path.join(self.spool, "supervisor-parked.json")):
            self.log("supervisor-parked.json present — deliberate stop; "
                     "exiting loop")
            return False, {"parked": True}
        if not self.claim_pid_file():
            self.log("another live supervisor loop owns the pid file — "
                     "exiting (handover complete)")
            return False, {"handover": True}
        active = self.render_active()
        pending = self.pending_count()
        hb = self.heartbeats()
        live = self.live_workers(hb)
        claims = self.claim_files()
        claimed_by = {}
        for c in claims:
            claimed_by.setdefault(
                os.path.basename(c).rsplit(".claimed-", 1)[-1], []).append(c)
        target = _scaling_target(pending, active)

        actions = self.load_actions()
        now_ms = self.now_ms()
        deaths = self.state.setdefault("deaths", {})
        sightings = self.state.setdefault("death_sightings", {})
        held = self.state.setdefault("held_slots", {})
        # Expire holds whose hour has passed.
        for wid in list(held):
            if now_ms - held[wid].get("heldAtMs", 0) > HOLD_S * 1000:
                held.pop(wid, None)
        held_ids = set(held)

        # Death detection: sighting at >90s stale, confirm at +180s.
        # confirmed_dead: workers whose death was already counted. One
        # continuous outage is ONE death: without this, a worker dead for
        # 31h re-sights and re-confirms every ~3 min and trips the
        # three-death hold on a single outage (worker-3 did exactly this
        # 2026-11-19 10:59-11:06 UTC). A slot leaves confirmed_dead only
        # when its heartbeat goes fresh again.
        confirmed = set(self.state.setdefault("confirmed_dead", []))
        for wid in sorted(set(list(hb.keys()) + list(sightings.keys()))):
            stale = wid not in live
            has_claim = bool(claimed_by.get(wid))
            if stale and not has_claim and wid in hb:
                if wid in confirmed:
                    continue
                # A STOP/STOPPED line for a stop_graceful action is
                # positive evidence the worker exited — it outranks the
                # busy marker below (a worker can answer, then obey a
                # stop inside the busy window the false-death guard set
                # on an earlier tick).
                stop_seen = self._worker_stop_seen(wid, actions)
                busy_until = self.state.get("busy_until", {}).get(wid, 0)
                if not stop_seen and busy_until and now_ms < busy_until:
                    continue  # log guard said busy; slot not free
                # Stop-race exit (2026-11-21): a worker that logged STOP /
                # STOPPED for a stop_graceful action has announced its own
                # exit — positive evidence of death, stronger than the
                # heartbeat gap. Confirm immediately instead of waiting
                # DEATH_CONFIRM_S: the pool re-heals on this tick, not
                # ~3 min later. The false-death guard below is skipped
                # for such a worker: an ANSWERED line just before the
                # STOP line is the answer it finished before stopping.
                confirm_now = False
                if wid not in sightings:
                    if stop_seen:
                        self.log("worker %s stopped on stop_graceful "
                                 "(STOP line seen) — confirming death "
                                 "immediately, no %ds wait"
                                 % (wid, DEATH_CONFIRM_S))
                        confirm_now = True
                    else:
                        sightings[wid] = {"firstSeenMs": now_ms}
                        self.log("worker %s heartbeat stale — death "
                                 "sighting (confirm in %ds)"
                                 % (wid, DEATH_CONFIRM_S))
                elif now_ms - sightings[wid]["firstSeenMs"] \
                        >= DEATH_CONFIRM_S * 1000:
                    sightings.pop(wid, None)
                    confirm_now = True
                if confirm_now:
                    # False-death guard: a heartbeat starves while a
                    # worker answers back-to-back (seen 4x for
                    # worker-1/2). Fresh ANSWERED lines in worker.log
                    # mean busy, not dead — do not count, do not queue.
                    if not stop_seen and self.worker_log_answered_recent(
                            wid, STUCK_FROZEN_S):
                        self.log("worker %s stale heartbeat but ANSWERED "
                                 "recently in worker.log — busy, not dead; "
                                 "sighting cleared" % wid)
                        # Mark the slot busy so scale-up does not treat
                        # it as free and spawn a duplicate workerId
                        # while this worker is still answering.
                        self.state.setdefault("busy_until", {})[wid] = (
                            now_ms + STUCK_FROZEN_S * 1000)
                        continue
                    confirmed.add(wid)
                    self.state["confirmed_dead"] = sorted(confirmed)
                    # The slot's incarnation is over: rotation age is
                    # per AGENT, not per slot, so the dead worker's
                    # first_seen / rotate marker must not survive into
                    # its replacement. Without this, a respawned worker
                    # inherits the dead incarnation's ~12h / ~50 answers
                    # and is rotated at once (worker-2, 2026-11-20/21:
                    # rotated ~4s after its first heartbeat). A
                    # same-agent revival after a false death also resets
                    # its clock — a >3min heartbeat gap is treated as a
                    # new incarnation, which only delays a hygiene
                    # rotation, never a safety decision.
                    self.state.setdefault("first_seen", {}).pop(wid, None)
                    self.state.setdefault("rotate_queued", {}).pop(wid, None)
                    d = deaths.get(wid, {"count": 0, "first": now_ms})
                    d["count"] += 1
                    d["last"] = now_ms
                    deaths[wid] = d
                    recent = [t for t in d.get("times", [])
                              if now_ms - t < THREE_DEATHS_WINDOW_S * 1000]
                    recent.append(now_ms)
                    d["times"] = recent
                    queued = False
                    if len(recent) >= 3:
                        held[wid] = {"heldAtMs": now_ms,
                                     "deaths": len(recent)}
                        held_ids.add(wid)
                        self.spool_log(
                            "WORKER HELD %s: 3 deaths in %d min — no more "
                            "respawns for this slot for 1h; pool runs at "
                            "target-minus-one" % (wid,
                                                 THREE_DEATHS_WINDOW_S // 60))
                        self.log("worker %s held after 3 deaths" % wid)
                    elif wid not in held_ids:
                        pending_spawn = any(
                            a.get("status") == "pending"
                            and a.get("action") == "spawn"
                            and a.get("workerId") == wid
                            for a in actions)
                        # Slot rule (standing policy): only respawn a dead
                        # worker whose slot number is within the CURRENT
                        # target (target = clamp(ceil(pending/3), 2, 8)).
                        # A stale heartbeat alone is NOT reason to respawn
                        # a slot above target — that slot is reserved for
                        # scale-up, not for keeping every historical slot
                        # filled. Queueing for it would leave a pending
                        # action nobody should act on, which would
                        # eventually (and wrongly) trip the owner tripwire.
                        # The death is still counted (3-death hold above).
                        slot = self.slot_number(wid)
                        if (not pending_spawn and len(live) < target
                                and slot is not None and slot <= target):
                            self.queue_action(
                                actions, "respawn", wid,
                                "heartbeat stale >%ds, no claim (death #%d)"
                                % (HEARTBEAT_STALE_S, d["count"]))
                            queued = True
                        else:
                            self.log("worker %s dead (#%d) but slot %s "
                                     "above target / pool at target "
                                     "(%d/%d) — death counted, no respawn "
                                     "queued"
                                     % (wid, d["count"], wid,
                                        len(live), target))
                    if queued:
                        self.spool_log(
                            "WORKER DEAD %s (heartbeat stale, no claim; "
                            "death #%d) — respawn queued" % (wid, d["count"]))
                    else:
                        self.spool_log(
                            "WORKER DEAD %s (heartbeat stale, no claim; "
                            "death #%d) — counted, no respawn (slot above "
                            "target or pool at target)"
                            % (wid, d["count"]))
            elif not stale:
                confirmed.discard(wid)
                self.state["confirmed_dead"] = sorted(confirmed)
                self.state.get("busy_until", {}).pop(wid, None)
                if wid in sightings:
                    sightings.pop(wid, None)
                    self.log("worker %s heartbeat fresh again — death "
                             "sighting cleared (was a between-turn gap)"
                             % wid)
                if wid in deaths and deaths[wid].get("count", 0) >= 3 \
                        and wid not in held_ids:
                    deaths.pop(wid, None)
        # A live heartbeat lifts a hold.
        for wid in list(held_ids):
            if wid in live:
                held.pop(wid, None)
                held_ids.discard(wid)

        # Stuck detection (brief): fresh heartbeat, answered frozen 10 min,
        # no worker.log line in 10 min, pending existed in the window.
        # pendingSinceMs is the start of the CURRENT continuous pending
        # stretch: the brief's "pending at some tick inside that window"
        # must not be satisfied by a burst at the very end of a long
        # frozen-idle episode. (2026-11-19 11:18 UTC: a 25-probe drill
        # flipped pendingSeen true on a watch whose sinceMs was 21 min
        # old, and both live workers were falsely queued for replacement
        # while they were refusing probes in seconds. A pending stretch
        # shorter than the frozen window cannot prove stuck.)
        stuck_watch = self.state.setdefault("stuck_watch", {})
        stuck_done = self.state.setdefault("stuck_actioned", {})
        for wid in sorted(live):
            answered = live[wid][1].get("answered")
            watch = stuck_watch.get(wid)
            if watch is None:
                stuck_watch[wid] = {"answered": answered,
                                    "sinceMs": now_ms,
                                    "pendingSinceMs":
                                        now_ms if pending > 0 else None}
                continue
            if answered != watch.get("answered"):
                stuck_watch.pop(wid, None)
                stuck_done.pop(wid, None)
                continue
            if pending > 0:
                if watch.get("pendingSinceMs") is None:
                    watch["pendingSinceMs"] = now_ms
            else:
                watch["pendingSinceMs"] = None
            pending_sustained = (
                watch.get("pendingSinceMs") is not None
                and now_ms - watch["pendingSinceMs"]
                >= STUCK_FROZEN_S * 1000)
            if (now_ms - watch["sinceMs"] >= STUCK_FROZEN_S * 1000
                    and pending_sustained
                    and wid not in stuck_done
                    and not self.worker_log_recent(wid, STUCK_FROZEN_S)):
                stuck_done[wid] = now_ms
                self.queue_action(
                    actions, "replace_stuck", wid,
                    "heartbeat fresh but answered frozen at %s for 10 min "
                    "with pending work and no log line (stuck, not live)"
                    % answered,
                    detail={"pkill": [
                        "pkill -f 'worker%s/heartbeat.sh'"
                        % wid[len("worker-"):],
                        "pkill -f 'worker%s-heartbeat'"
                        % wid[len("worker-"):]]})
                self.spool_log("WORKER STUCK %s (answered frozen %s, no "
                               "log line 10 min) — replacement queued"
                               % (wid, answered))

        # Rotation: ~12h alive or ~50 answers since (re)start, when idle.
        # Rotation age is per AGENT INCARNATION, not per slot: a fresh
        # agent starts its answered count at 0, so a drop below the
        # last-seen count means the slot was respawned / rotated /
        # replaced since the last tick (fast replacement, no death gap
        # for the death-confirm reset above to catch). Reset the slot's
        # age then, or the fresh worker inherits the old incarnation's
        # ~12h and is rotated at once. Thresholds unchanged.
        first_seen = self.state.setdefault("first_seen", {})
        rotate_done = self.state.setdefault("rotate_queued", {})
        for wid in sorted(live):
            answered = live[wid][1].get("answered") or 0
            rec = first_seen.get(wid)
            if rec is None:
                first_seen[wid] = {"firstSeenMs": now_ms,
                                   "answeredAtStart": answered,
                                   "answeredLastSeen": answered}
                continue
            # Legacy records (written before answeredLastSeen existed)
            # fall back to answeredAtStart: within one incarnation the
            # count never drops below its start value, so a lower count
            # still proves a new incarnation — and it heals slots whose
            # first_seen already went stale before this fix was deployed.
            if answered < rec.get("answeredLastSeen",
                                  rec.get("answeredAtStart", answered)):
                self.log("worker %s answered count dropped %s -> %s — "
                         "new incarnation, rotation age reset"
                         % (wid, rec.get("answeredLastSeen"), answered))
                first_seen[wid] = {"firstSeenMs": now_ms,
                                   "answeredAtStart": answered,
                                   "answeredLastSeen": answered}
                rotate_done.pop(wid, None)
                continue
            rec["answeredLastSeen"] = answered
            age = now_ms - rec["firstSeenMs"]
            gained = answered - rec.get("answeredAtStart", answered)
            if (age >= ROTATE_AGE_S * 1000 or gained >= ROTATE_ANSWERS) \
                    and wid not in claimed_by \
                    and wid not in rotate_done \
                    and not any(a.get("status") == "pending"
                                and a.get("action") in
                                ("rotate", "replace_stuck")
                                for a in actions):
                rotate_done[wid] = now_ms
                self.queue_action(
                    actions, "rotate", wid,
                    "session age %.1fh / %d answers since start — rotate "
                    "when idle, same workerId"
                    % (age / 3600000.0, gained))
        for wid in list(rotate_done):
            if wid not in live:
                rotate_done.pop(wid, None)
                first_seen.pop(wid, None)

        # Scaling.
        if active:
            # Two-tick confirmation (2026-11-21 stop race): one tick's
            # ACTIVE reading must not queue stops. The gate is a
            # point-in-time read and a render can end between ticks —
            # on Nov 21 a single ACTIVE tick queued stop_graceful for
            # both workers, the render ended before the next tick, and
            # both workers had already read the stops and exited. Only
            # queue stops when the render is ACTIVE on this tick AND
            # was already ACTIVE on the previous tick; a first sighting
            # only records the time. (A re-probe in the same tick would
            # NOT have prevented Nov 21: the gate was genuinely ACTIVE
            # at queue time — the flap was between ticks.)
            prev_active_since = self.state.get("render_active_since_ms")
            if prev_active_since is None:
                self.state["render_active_since_ms"] = now_ms
                self.log("render ACTIVE first sighting — confirming on "
                         "next tick before queueing stops")
            else:
                for wid in sorted(live):
                    if wid not in claimed_by:
                        self.queue_action(actions, "stop_graceful", wid,
                                          "render ACTIVE — finish current "
                                          "answer then stop; no respawns until "
                                          "gate idle")
            self.log("render ACTIVE — target 0 (workers left to "
                     "finish, no respawns)")
        else:
            # Render ended (or never active): clear the confirmation.
            self.state.pop("render_active_since_ms", None)
            # Needed slots, net of workers already coming (pending spawn /
            # respawn), slots reserved for unconfirmed deaths (they will be
            # respawned, not replaced by another id), and held slots (the
            # brief: a held slot runs the pool at target-minus-one).
            incoming = sum(
                1 for a in actions
                if a.get("status") == "pending"
                and a.get("action") in ("spawn", "respawn"))
            eff_target = max(0, target - len(held_ids))
            needed = (eff_target - len(live) - incoming
                      - len(sightings))
            if needed > 0:
                acted_on = {a.get("workerId") for a in actions
                            if a.get("status") == "pending"}
                busy = {wid for wid, until in
                        self.state.get("busy_until", {}).items()
                        if now_ms < until}
                free = [("worker-%d" % i) for i in range(1, MAX_WORKERS + 1)
                        if i <= target
                        and ("worker-%d" % i) not in live
                        and ("worker-%d" % i) not in held_ids
                        and ("worker-%d" % i) not in sightings
                        and ("worker-%d" % i) not in claimed_by
                        and ("worker-%d" % i) not in acted_on
                        and ("worker-%d" % i) not in busy]
                for wid in free[:needed]:
                    self.queue_action(actions, "spawn", wid,
                                      "live=%d target=%d pending=%d"
                                      % (len(live), target, pending))
                if not free and needed > 0:
                    self.log("scale-up needed but no free slot (held: %s)"
                             % sorted(held_ids))
            elif len(live) > target:
                # Scale-down grace: only stop an idle worker when the
                # pool has been truly quiet (no worker answered in the
                # last SCALE_DOWN_GRACE_S). A brief pending==0 lull
                # mid-job (e.g. while the job downloads/transcripts
                # between vision batches) is not idle — more requests
                # are coming and the workers are needed.
                any_answered_recently = any(
                    self.worker_log_answered_recent(wid, SCALE_DOWN_GRACE_S)
                    for wid in live)
                if any_answered_recently:
                    self.log("scale-down deferred: a worker answered "
                             "within %ds (job still in media); holding "
                             "%d workers" % (SCALE_DOWN_GRACE_S, len(live)))
                else:
                    idle = [wid for wid in sorted(live, reverse=True)
                            if wid not in claimed_by
                            and (live[wid][1].get("pending") in (0, None))]
                    if idle:
                        self.queue_action(actions, "stop_idle", idle[0],
                                          "live=%d target=%d pending=%d"
                                          % (len(live), target, pending))

        actions = self.reconcile_actions(actions, live, target, active,
                                         held_ids)
        self.save_actions(actions)

        unresponsive = self.owner_unresponsive(actions)
        status_payload = {
            "updatedAt": now_ms,
            "workers": len(live),
            "target": target,
            "pending": pending,
            "ownerResponsive": not unresponsive,
            "pendingActions": sum(1 for a in actions
                                  if a.get("status") == "pending"),
            "heldSlots": sorted(held_ids),
        }
        if unresponsive:
            oldest = min(a["createdAtMs"] for a in actions
                         if a.get("status") == "pending")
            # Spell out the unexecuted work so the next woken agent (or
            # the self-heal cron) does not have to reconstruct it: the
            # spawn actions are the pool's recovery, verbatim.
            pending_spawns = [
                {"id": a.get("id"), "action": a.get("action"),
                 "workerId": a.get("workerId"),
                 "reason": a.get("reason"),
                 "createdAtMs": a.get("createdAtMs")}
                for a in actions
                if a.get("status") == "pending"
                and a.get("action") in (
                    "spawn", "respawn", "replace_stuck", "rotate")]
            self.atomic_write(
                os.path.join(self.spool,
                             "supervisor-owner-unresponsive.json"),
                {"updatedAt": now_ms,
                 "supervisorAgentId": self.supervisor_id,
                 "oldestPendingActionAgeS":
                     round((now_ms - oldest) / 1000.0, 1),
                 "pendingActions": status_payload["pendingActions"],
                 "pendingSpawnActions": pending_spawns,
                 "reason": "oldest queued action unacted for >%ds; "
                           "liveness writes paused so the watch hook wakes "
                           "the parent to respawn the supervisor"
                           % OWNER_ACTION_TIMEOUT_S})
            self.log("OWNER UNRESPONSIVE: oldest pending action %.0fs old "
                     "— pausing liveness writes (watch hook will wake "
                     "parent)" % ((now_ms - oldest) / 1000.0))
            # Deliberately do NOT write pool-status.json or
            # supervisor-alive.json in this mode: BOTH stale is the tripwire.
        else:
            try:
                os.remove(os.path.join(
                    self.spool, "supervisor-owner-unresponsive.json"))
            except OSError:
                pass
            self.atomic_write(os.path.join(self.spool, "pool-status.json"),
                              status_payload)
            self.atomic_write(
                os.path.join(self.spool, "supervisor-alive.json"),
                {"updatedAt": now_ms,
                 "supervisorAgentId": self.supervisor_id,
                 "workers": len(live),
                 "target": target})

        if n % 4 == 0:
            self.sweep(hb, live)
        self._save_state()
        if n % 10 == 0:
            self.log("tick %d: live=%d target=%d pending=%d render=%s "
                     "actions_pending=%d owner=%s"
                     % (n, len(live), target, pending,
                        "active" if active else "idle",
                        status_payload["pendingActions"],
                        "unresponsive" if unresponsive else "ok"))
        return True, {"live": len(live), "target": target,
                      "pending": pending, "render_active": active,
                      "actions_pending": status_payload["pendingActions"],
                      "owner_unresponsive": unresponsive}


def main(argv):
    once = "--once" in argv
    # --dry-run is the documented dry-run mode: identical to --shadow
    # (read-only decision print, writes nothing). The executor-side
    # dry run lives in pool_selfheal.py --dry-run, which plans spawns,
    # stops and loop restarts without performing any of them.
    shadow = "--shadow" in argv or "--dry-run" in argv
    sup = Supervisor()
    if shadow:
        # Read-only decision print: no writes of any kind.
        active = sup.render_active()
        pending = sup.pending_count()
        hb = sup.heartbeats()
        live = sup.live_workers(hb)
        target = _scaling_target(pending, active)
        actions = sup.load_actions()
        print(json.dumps({
            "shadow": True, "dry_run": "--dry-run" in argv,
            "live": sorted(live), "target": target,
            "pending": pending, "render_active": active,
            "pending_actions": [
                {"action": a.get("action"), "workerId": a.get("workerId"),
                 "status": a.get("status")} for a in actions],
            "owner_unresponsive": sup.owner_unresponsive(actions),
            "supervisor_id": sup.supervisor_id}, indent=2))
        return 0
    sup.log("pool supervisor starting (spool=%s, supervisor=%s)"
            % (sup.spool, sup.supervisor_id))
    n = 0
    while True:
        n += 1
        t0 = sup.clock()
        try:
            keep, _info = sup.tick(n)
        except Exception as e:
            sup.log("tick %d error: %s — continuing" % (n, e))
            keep = True
        if once or not keep:
            break
        time.sleep(max(1, TICK_S - (sup.clock() - t0)))
    sup.log("pool supervisor exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
