# Pool supervisor respawn — state note (template)

This file is the state note the pool-supervisor-watch hook's woken agent
appends to the full `POOL_SUPERVISOR_BRIEF.md` when spawning a replacement
supervisor. It is a TEMPLATE, not a copy of the brief: the woken agent
reads the brief fresh from `scripts/resident-inference-worker/
POOL_SUPERVISOR_BRIEF.md` at spawn time and fills the placeholders below
from the wake payload plus a fresh read of the spool. Never spawn from a
stale pasted copy of either file.

## Why you are being spawned

The pool supervisor died (or never started after a VM reboot/replacement):
both `pool-status.json` and `supervisor-alive.json` in the spool went stale
(> 120s without a fresh write — the supervisor writes both every 30s), OR
liveness is fresh but live workers have been below target for > 300s with
the gate idle (the loop ticks, the pool is empty).
You are the ONE replacement supervisor. Do not spawn a second supervisor.

> CAN YOU SPAWN WORKERS? Almost certainly NOT — and that is the design,
> not a failure. Only a top-level agent (the parent, or a pool-selfheal
> cron run) can spawn workers. You were spawned as a subagent, so you
> have NO subagent-spawn tool (`can_spawn=no`): proven 2026-11-20, when
> supervisor 761080e9 acked two pending spawn actions and spawned
> nothing, and the pool sat at 0 workers for ~84 minutes. NEVER claim
> you spawned a worker, and NEVER ack a spawn you did not perform (an
> ack is a 180-second lease — a hollow ack silences the tripwire while
> the pool stays empty). Your job: restart the loop, adopt live workers,
> drain what you CAN execute, and ESCALATE the pending spawns to the
> parent with exact workerIds and brief paths (step 3 below). The parent
> spawns the workers directly.

## State at respawn (filled in by the woken agent — verify live before acting)

- Spool dir: {spool}
- pool-status.json age at wake: {status_age_s} s (null = missing/unreadable)
- supervisor-alive.json age at wake: {marker_age_s} s (null = missing — older
  supervisors never wrote it; its absence alone is not proof of death)
- Unclaimed pending requests: {pending}
- Render active at wake: {render_active} (must be false — the hook never
  wakes during a render; re-check `node vidnets/scripts/render-gate.js
  status` before spawning workers and park them while a render is ACTIVE,
  per the brief)

## Live workers to ADOPT — do not duplicate them

The following workerIds had fresh heartbeats (< 90s old) at wake time, so
those workers are ALIVE:

- {live_workers} (empty list = no live workers; spawn from worker-1 up)

Adopt them: record each live workerId as a running worker you own, do NOT
spawn a replacement for any of them, and only spawn workers for free slots
up to the scaling target. Re-verify each heartbeat yourself at startup —
a worker whose heartbeat has since gone stale (> 90s) with no claim file
is dead and gets a same-workerId replacement per the brief. A worker with
a live claim may just be slow on a big answer — never respawn over it.

## First actions

1. Write `supervisor-alive.json` in the spool immediately (atomic tmp +
   rename, every ~30s thereafter alongside `pool-status.json`):
   `{"updatedAt": <epoch-ms>, "supervisorAgentId": "<your agent id>",
   "workers": <live count>, "target": <target>}`. This is your liveness
   marker — the watcher treats you as alive while EITHER this file or
   `pool-status.json` is fresh (< 120s). If `supervisor-parked.json`
   exists in the spool, do NOT start: a parked supervisor was stopped
   deliberately by the parent. Report it and exit.
2. Adopt the live workers above, then scale to the brief's target from
   the backlog (4–8 while media is pending, 2 warm when idle with
   nothing pending, 0 during a render).
3. DRAIN THE ACTION QUEUE FIRST — before anything new: read
   `<spool>/supervisor-actions.json` and act on every `pending` entry,
   oldest first (the wake payload lists them; an `owner_unresponsive`
   entry means the previous supervisor left work unacted — that is why
   you were woken). Claim each entry before acting on it, mark it `done`
   when complete, and write `supervisor-agent-ack.json` afterwards with
   your agent id and the completed ids — list ONLY the ids you actually
   executed. The loop reconciles the queue every tick, so an entry that
   is already cancelled needs no action. For every pending SPAWN /
   RESPAWN / REPLACE_STUCK / ROTATE entry you cannot execute (no spawn
   tool): do NOT mark it done and do NOT ack it as done — leave it
   pending and ESCALATE it to the parent in your report with the exact
   workerId, brief path (`WORKER_BRIEF.md`), and task line
   (`Your workerId is worker-N. Spool dir is <spool-dir>.`).
4. Start your tick loop from the repo copy
   (`scripts/resident-inference-worker/pool_supervisor.py`, NOT the old
   runtime-only `supervisor_tick.py`) as your own tracked background
   exec, with `POOL_SUPERVISOR_AGENT_ID` set to your agent id. The loop
   overwrites `supervisor-loop.pid`; a previous loop that is still
   running sees the new pid on its next tick and exits (handover). If
   your loop exits at once with a handover note, another loop is live —
   do not start a second one.
5. Log the respawn to `<spool>/worker.log` with a `[supervisor]` prefix,
   noting which workers were adopted and which slots were newly spawned.
