# Pool self-heal cron (job id `pool-selfheal`)

The executor that closes the 2026-11-20 gap. The supervisor loop
(`pool_supervisor.py`) can only QUEUE actions; hook-woken supervisors
(depth 2) have no subagent-spawn tool and cannot execute them. The only
agents that can spawn pool workers are TOP-LEVEL agents — the parent,
and a top-level cron run. This cron IS that top-level executor: every 2
minutes it drains the action queue itself, so the pool comes back
without a human or the parent noticing it died.

Proven 2026-11-20: a one-off probe cron spawned a subagent that wrote
`/tmp/spawn-probe/marker.txt` at 13:47:24 UTC (run b9a71cf1, subagent
a34b19f2) — a top-level cron run DOES have the spawn tool. The probe job
was runonce and is spent; this is the permanent job.

## Registration (parent/coordinator decision — NOT automatic)

Register with `cron.add` (or run `install-pool-selfheal-cron.sh`,
which prints these exact parameters and does nothing else):

- id: `pool-selfheal`
- title: `Pool self-heal (inference pool executor)`
- owner: `goal:documentary-video-render-pipeline`
- mode: `task`
- schedule: interval, every `2m`, timezone `Asia/Karachi`
- timeout: default (a run is seconds of deciding plus worker spawns;
  spawning is one subagent spawn per missing worker, lowest free slots
  first, one at a time)

## Run body (verbatim — this is the job's `body`)

> Every 2 minutes, heal the resident inference pool. You are a TOP-LEVEL
> agent run: you CAN spawn subagents (a probe on 2026-11-20 proved a cron
> run has the spawn tool). You are the pool's executor for this run.
>
> 1. Run the planner (read-only — it changes nothing):
>    `python3 ~/workspace/M2l/scripts/resident-inference-worker/pool_selfheal.py --check`
>    It prints a JSON plan: parked?, render_active, live workers, target
>    (idle 2, media floor 4, ceiling 8), loop state, lock state, spawns
>    (workerId + brief path + task line), mark_done / mark_cancelled.
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
> 5. If `render_active` is true: spawn NOTHING. (The loop may still be
>    restarted — it respects the gate itself.) Exit after the loop check.
> 6. Execute every `spawns` entry, lowest workerId first, one at a time:
>    spawn a subagent whose task is the FULL text of `WORKER_BRIEF.md`
>    (read it fresh from the `briefPath` in the plan — never a pasted or
>    remembered copy) plus the `taskLine` from the plan
>    (`Your workerId is worker-N. Spool dir is <spool-dir>.`). Confirm
>    the worker's heartbeat file appears (`worker-alive-<workerId>.json`
>    fresh < 90s) before spawning the next. Never spawn more than 8
>    workers, never two with the same workerId, never a workerId with a
>    fresh heartbeat (adopt it instead), never a held slot.
>    For a spawn with `after_stop: true` (replace_stuck / rotate): first
>    run its `pkill_detached_heartbeat` commands (if any), then wait
>    until the old worker's heartbeat goes stale (or 60s, whichever
>    comes first), then spawn the replacement with the SAME workerId.
> 7. Mark each executed action done (only after its effect is visible):
>    `pool_selfheal.py --mark-done <action-id> --agent-id <your-agent-id>`,
>    and each `mark_cancelled` entry:
>    `pool_selfheal.py --mark-cancelled <action-id> --agent-id <your-agent-id> --reason "<reason>"`.
>    Never mark a spawn done before its worker heartbeat is fresh.
> 8. Log one line to `<spool>/worker.log` with a `[selfheal]` prefix:
>    what you spawned, restarted, or marked, and the pool state after.
>    Release the lock. Report briefly (spawned workerIds, loop state,
>    live/target/pending) — stay silent to the user unless the pool
>    could NOT be healed (spawns failed twice in a row, loop will not
>    start, or the plan says parked).
>
> Never answer an inference request yourself. Never touch job work dirs,
> never restart the server or daemon, never dispatch a job, never stop a
> worker mid-answer (stops are self-executing: workers poll the action
> queue; you only mark them done once their heartbeat goes stale).

## Why 2 minutes

The loop ticks every 15s and queues within a tick of a death; the
self-heal run picks it up within ~2 minutes. The old path (loop
tripwire 600s + hook wake + a supervisor that could not spawn) took
~84 minutes to fail. Worst case now: a worker dies mid-media and is
back in ~2–4 minutes (one cron cadence + one spawn), against ~19
minutes of media time lost on test 15 — and a dead loop is restarted
on the same run. The 300s scale-down grace and the 180s ack lease in
the loop keep this from churning: a run that finds the pool healthy
does nothing.

## Verification (next proof run)

- During media: `pool-status.json` `workers` > 0 (4–8 while media is
  pending), `ownerResponsive` true, `pendingActions` 0.
- No `deterministic_degraded` scenes caused by pool outage (vision
  answers every verdict).
- `worker.log` shows `[selfheal]` lines only when healing was needed;
  a healthy pool produces no spawns and no noise.
