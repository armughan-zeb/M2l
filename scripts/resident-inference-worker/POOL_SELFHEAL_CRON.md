# Pool self-heal cron (job id `pool-selfheal`)

The executor that closes the 2026-11-20 gap — and, since 2026-11-21,
the job that must NOT spawn workers. The supervisor loop
(`pool_supervisor.py`) can only QUEUE actions; hook-woken supervisors
(depth 2) have no subagent-spawn tool and cannot execute them. The only
agents that can spawn pool workers are TOP-LEVEL agents — the parent,
and a top-level cron run. This cron IS a top-level run — but workers it
spawns DIE at the cron run's 300s timeout (proven 2026-11-21: self-heal
run f0a5782e spawned worker-1 + worker-2, both dead at the timeout with
0 answers). So this cron is the loop-restarter, the queue hygienist,
and the SIGNALLER — never the spawner. Its spawns go to the parent via
the scale-up signal (`supervisor-scaleup-needed.json`), and the parent
spawns them directly. A cron run that spawns workers staffs the pool
with workers that die mid-answer: worse than an empty pool, because
each death re-triggers death handling and loses the in-flight answer.

Proven 2026-11-20: a one-off probe cron spawned a subagent that wrote
`/tmp/spawn-probe/marker.txt` at 13:47:24 UTC (run b9a71cf1, subagent
a34b19f2) — a top-level cron run DOES have the spawn tool. Having the
tool is not the same as the spawn surviving: the 2026-11-21 proof is
that the spawned workers die with the run. The probe job was runonce
and is spent; this is the permanent job.

## Registration (parent/coordinator decision — NOT automatic)

Register with `cron.add` (or run `install-pool-selfheal-cron.sh`,
which prints these exact parameters and does nothing else):

- id: `pool-selfheal`
- title: `Pool self-heal (inference pool executor)`
- owner: `goal:documentary-video-render-pipeline`
- mode: `task`
- schedule: interval, every `2m`, timezone `Asia/Karachi`
- timeout: default (a run is seconds of deciding plus a loop restart;
  this run NEVER spawns workers — spawns are parent-only, signalled
  via `--write-scaleup-signal`, because cron-spawned workers die at
  the 300s timeout)

## Run body (verbatim — this is the job's `body`)

> Every 2 minutes, heal the resident inference pool. You are a TOP-LEVEL
> agent run: you CAN spawn subagents (a probe on 2026-11-20 proved a cron
> run has the spawn tool) — but you must NOT spawn pool workers with it:
> workers spawned by a cron run DIE at the run's 300s timeout (proven
> 2026-11-21: self-heal run f0a5782e spawned worker-1 + worker-2, both
> dead at the timeout with 0 answers). Only a PARENT-DIRECT spawn
> survives. Your job for spawns is to SIGNAL them to the parent, never
> to execute them. You are the pool's loop-restarter, queue hygienist
> and signaller for this run.
>
> 1. Run the planner (read-only — it changes nothing):
>    `python3 ~/workspace/M2l/scripts/resident-inference-worker/pool_selfheal.py --check`
>    It prints a JSON plan: parked?, render_active, live workers, target
>    (idle 2, media floor 4, ceiling 8), loop state, lock state, spawns
>    (workerId + brief path + task line — PARENT-ONLY, see step 6),
>    mark_done / mark_cancelled.
> 2. If `parked` is true: do nothing and exit. A deliberate parent stop
>    is never healed over.
> 3. If the lock is held by another executor: do nothing and exit (do
>    not double-spawn). Otherwise acquire it:
>    `pool_selfheal.py --acquire-lock --agent-id <your-agent-id>`
>    and release it at the end (`--release-lock`). If the lock is stale
>    (> 300s) the planner already reports it free.
> 4. If the loop needs restarting (`loop.restart_needed`): start it as
>    YOUR OWN tracked background exec (never `nohup` — the loop must be
>    owned by a live agent session so it can be seen and stopped):
>    `cd ~/workspace/vidnets-runtime && POOL_SUPERVISOR_AGENT_ID=<your-agent-id> python3 ~/workspace/M2l/scripts/resident-inference-worker/pool_supervisor.py`
>    The loop's pid-file handover exits any older loop. If the loop exits
>    at once with a handover note, a loop is already live — do not start
>    a second one. NEVER start a second loop while `loop.alive` is true.
>    WARNING: a loop you start dies ~1 minute after this run ends (the
>    runtime reaps cron-run descendants) — it is a stopgap that
>    re-queues actions and re-writes the scale-up signal until the
>    parent starts a loop directly. Say so in your report when you
>    restart it.
> 5. If `render_active` is true: spawn NOTHING. (The loop may still be
>    restarted — it respects the gate itself.) Exit after the loop check.
> 6. SPAWNS ARE PARENT-ONLY — DO NOT SPAWN WORKERS. If the plan has
>    `spawns`, write the scale-up signal so the watch hook wakes the
>    parent within ~60s:
>    `pool_selfheal.py --write-scaleup-signal --agent-id <your-agent-id>`
>    and report the spawn list (workerIds, brief path, task lines) to
>    the parent as the work owed — the parent spawns them directly.
>    If the plan has no spawns, the signal call clears any stale
>    signal. NEVER spawn a worker subagent yourself on this run, even
>    though you have the tool: the worker would die at this run's
>    300s timeout. (For a spawn with `after_stop: true`
>    (replace_stuck / rotate): do NOT run its `pkill_detached_heartbeat`
>    commands either — that half of the replacement belongs to the
>    parent's spawn run, which does the pkill, waits for the old
>    heartbeat to go stale, then spawns the SAME workerId.)
> 7. Mark each executed action done (only after its effect is visible):
>    `pool_selfheal.py --mark-done <action-id> --agent-id <your-agent-id>`,
>    and each `mark_cancelled` entry:
>    `pool_selfheal.py --mark-cancelled <action-id> --agent-id <your-agent-id> --reason "<reason>"`.
>    Never mark a spawn done before its worker heartbeat is fresh —
>    and since this run never spawns, a spawn action is marked done
>    only when its worker is ALREADY live (adopted), never on the
>    strength of this run.
> 8. Log one line to `<spool>/worker.log` with a `[selfheal]` prefix:
>    what you restarted, signalled, or marked, and the pool state after.
>    Release the lock. Report briefly (signal written / spawn list
>    escalated, loop state, live/target/pending) — stay silent to the
>    user unless the pool could NOT be healed (loop will not start, or
>    the plan says parked).
>
> Never answer an inference request yourself. Never touch job work dirs,
> never restart the server or daemon, never dispatch a job, never stop a
> worker mid-answer (stops are self-executing: workers poll the action
> queue; you only mark them done once their heartbeat goes stale).

## Why 2 minutes

The loop ticks every 15s and queues within a tick of a death or a
target rise, writing the scale-up signal the same tick; the watch hook
polls every 60s and wakes the parent on a fresh signal. The self-heal
run picks up a dead loop within ~2 minutes and re-queues + re-signals
on the same run. The old path (loop tripwire 600s + hook wake + a
supervisor that could not spawn) took ~84 minutes to fail. Worst case
now: a worker dies mid-media and the parent is woken within ~75s of
the target rise (one loop tick + one hook poll); the spawn itself then
takes as long as the parent needs to start the worker agents. The
300s scale-down grace and the 180s ack lease in the loop keep this
from churning: a run that finds the pool healthy does nothing.

## Verification (next proof run)

- During media: `pool-status.json` `workers` > 0 (4–8 while media is
  pending), `ownerResponsive` true, `pendingActions` 0.
- While the pool is short with the gate idle,
  `supervisor-scaleup-needed.json` in the spool is fresh (< 120s) and
  names the owed workerIds; it is gone once the pool is back at target.
- A self-heal run NEVER spawns a worker: its `[selfheal]` log lines
  say "signal written" / "escalated", never "spawned".
- No `deterministic_degraded` scenes caused by pool outage (vision
  answers every verdict).
- `worker.log` shows `[selfheal]` lines only when healing was needed;
  a healthy pool produces no spawns and no noise.
