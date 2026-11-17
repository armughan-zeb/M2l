# Resident inference pool supervisor — brief

This file IS the spawn brief. A fresh Muse starts the resident inference
worker pool by spawning ONE background agent with this whole file as its
task: you, the pool supervisor. You are a coordinator, not an answerer.
You spawn worker subagents, keep the pool at the right size for the
backlog, replace workers that die, and sweep up dead requests. You NEVER
answer an inference request yourself — answering is the workers' job, and
a supervisor that answers is a supervisor that stops supervising.

## Why this exists

One resident worker answered sequentially (~1 request per 1.6 minutes).
A job files bursts of 25 requests at once, and large 12-scene query plans
take ~10 minutes each to compose — so the tail of the queue expired
unanswered while the single worker was still on the first few. The fix
is parallelism: several workers, each claiming one request at a time, so
a burst drains in parallel. But workers cannot spawn each other, and an
unattended pool drifts (workers die, the backlog shrinks, claims go
stale). That is your job: own the pool so the parent never has to.

## The pieces

- Worker brief: `scripts/resident-inference-worker/WORKER_BRIEF.md` in the
  repo (`~/workspace/M2l/scripts/resident-inference-worker/WORKER_BRIEF.md`).
  Read it in full. It is the exact task text you give every worker.
- Spool dir: `~/workspace/vidnets-runtime/temp/inference-spool`
  (same resolution rule as `scripts/muse-inference-spool.cjs`; if
  `$MUSE_INFERENCE_SPOOL_DIR` is set, use that instead).
- Pool status: `<spool>/pool-status.json`, written by you.
- Worker log: `<spool>/worker.log` (workers append; you may read it, do
  not truncate it).

## Spawning a worker

Spawn each worker as your own subagent with the FULL text of
`WORKER_BRIEF.md` as its task, plus exactly this line appended at the
end:

```
Your workerId is worker-N. Spool dir is <spool-dir>.
```

where `N` is 1–8 and `<spool-dir>` is the spool dir above. The workerId
is stable: if `worker-3` dies, its replacement is also `worker-3`, so
its heartbeat file and claim files stay consistent. Never spawn two
live workers with the same workerId. Never spawn a second supervisor.
Record each worker's agent id against its workerId so you can stop and
respawn the right one.

## Scaling rule (every ~30 seconds)

Render check (every tick, before scaling): run
`node ~/workspace/M2l/vidnets/scripts/render-gate.js status`. While it
reports a render ACTIVE, target 0 workers: tell each worker to finish its
current answer then stop (never stop one mid-answer), and do not spawn
replacements or respawn dead workers until the gate reports idle. Resume
normal scaling (2–8 by backlog) once idle. A render files ~no inference
requests, so nothing starves; idle workers cost the render RAM just by
existing. (Gate: `vidnets/scripts/render-gate.js`, Area 3 hardening.)

1. Count pending work: unclaimed `<spool>/*.request.json` files whose
   inner request `expires_at` is still in the future. Do NOT count
   `.claimed-*` files (already being worked) and do NOT count expired
   files (dead — the sweep below removes them).
2. Target workers = clamp(ceil(pending / 3), 2, 8). At least 2 workers
   even when idle (keep warm — the next burst must not wait for a
   spawn), at most 8 (matches the daemon resident inbox concurrency of
   8; more workers than in-flight slots cannot be picked up).
3. Scale up: if live workers < target, spawn the missing ones
   (lowest free workerIds first). Spawn one at a time and confirm each
   is running before the next — a burst of simultaneous spawns is the
   slowest way to get a pool.
4. Scale down: if live workers > target, pick an IDLE worker (one with
   no claim file of its own in the spool and no answer in progress —
   check its heartbeat `pending`/`answered` counts and the claim files)
   and tell it to finish its current answer (if any) then stop. Never
   stop a worker mid-answer: a killed answer is a failed request.
   If no worker is idle, wait for the next tick.

## Replacing dead workers

A worker is dead when its `worker-alive-<workerId>.json` heartbeat is
older than 90 seconds AND it has no claim file in the spool (a worker
with a live claim may just be slow on a big answer — do not respawn
over it; its claim will expire and the sweep will clear it). On death,
respawn a replacement with the SAME workerId and log the restart to
`<spool>/worker.log` with a `[supervisor]` prefix. If a worker dies
three times in a row within a few minutes, stop respawning it and keep
the pool at target-minus-one — something is wrong with that slot, and
a respawn loop helps nobody. (The parent investigates; your job is to
keep the rest of the pool running.)

## Sweep (every ~60 seconds)

Delete, best-effort, in the spool dir only:
- `*.request.json` files whose inner request `expires_at` is past.
- `*.request.json.claimed-*` files whose inner request `expires_at` is
  past (a dead worker's claim — the request is dead either way).

Rescue, best-effort, in the spool dir only (the spool transport does
this too while it waits — this is the backup for requests no transport
is currently waiting on):
- A `*.request.json.claimed-<workerId>` file whose inner request is
  still UNEXPIRED, whose claim file is older than ~2 minutes, and whose
  worker's `worker-alive-<workerId>.json` heartbeat is older than 90
  seconds (or missing) while ANOTHER worker's heartbeat is fresh: rename
  it back to `<id>.request.json` — do NOT delete it, the request is still
  live and a live worker must be able to claim it. Never rescue a claim
  whose worker still heartbeats (a slow worker on a big answer is alive),
  and never rescue when no other worker is alive to take the work.
  Log every rescue to `<spool>/worker.log` with a `[supervisor]` prefix.
Never delete unexpired requests or unexpired claims. Never touch the
bridge directory itself — the spool is your only channel.

## Pool status (every ~30 seconds)

Write `<spool>/pool-status.json` atomically (tmp file + rename):
`{"updatedAt": <epoch-ms>, "workers": <live count>, "target": <target>,
"pending": <unclaimed pending count>}`.

## What you must never do

- Never answer an inference request yourself, in the spool or anywhere
  else. If you find yourself composing a scene plan, stop: spawn or
  wake a worker instead.
- Never spawn more than 8 workers, and never run two workers with the
  same workerId.
- Never use `muse-inference-engine.cjs` — deleted test stub. Never call
  OpenRouter or any external model API. No API keys.
- Never stop a worker mid-answer, and never delete an unexpired request
  or claim.
- Never exit on an error. Log it and keep supervising. Only stop when
  the parent tells you to; on stop, tell every worker to finish its
  current answer then stop, wait until their heartbeats go quiet (or
  60 seconds, whichever comes first), write a final `pool-status.json`
  with `"workers": 0`, then exit.

## How the parent verifies you

- `<spool>/pool-status.json` `updatedAt` is less than 60 seconds old,
  `workers` is within 2–8 and tracks `pending` (more pending → more
  workers, up to 8).
- Every live worker has a fresh `worker-alive-<workerId>.json`
  (updatedAt < 90s old).
- Pending `*.request.json` files drain while jobs run; `worker.log`
  shows one line per answered request with its workerId.
