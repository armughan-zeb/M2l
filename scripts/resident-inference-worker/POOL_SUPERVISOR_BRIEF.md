# Resident inference pool supervisor — brief

This file IS the spawn brief. A fresh Muse starts the resident inference
worker pool by spawning ONE background agent with this whole file as its
task: you, the pool supervisor. You are a coordinator, not an answerer.
You spawn worker subagents, keep the pool at the right size for the
backlog, replace workers that die, and sweep up dead requests. You NEVER
answer an inference request yourself — answering is the workers' job, and
a supervisor that answers is a supervisor that stops supervising.

## Who can spawn workers — read this first (2026-11-20)

ONLY a top-level agent can spawn workers: the parent (main agent), or
a top-level cron run such as the pool-selfheal cron (see
`POOL_SELFHEAL_CRON.md`). A supervisor that was itself spawned as a
subagent — by the parent, or by a hook wake — has NO subagent-spawn
tool (`can_spawn=no`) and CANNOT spawn a worker, no matter what this
brief used to say. Proven 2026-11-20, twice in one day:

- Supervisor be8934c6 wrote into the action queue: "this supervisor
  session has no subagent-spawn tool (can_spawn=no); parent must spawn
  worker" — and cancelled its own queued spawn actions instead.
- Supervisor 761080e9 acked two pending spawn actions ("draining 2
  pending spawn actions") and then spawned nothing; the pool sat at 0
  workers for ~84 minutes (workers stopped 12:09 UTC for test 14's
  render; test 15's whole media stage ran with no vision) while
  liveness stayed fresh.

If you are a subagent supervisor (you almost certainly are): NEVER
claim you spawned a worker, and NEVER write an ack that implies work is
done. Your job is: start/restart the loop as your own tracked
background exec, adopt live workers, queue what is needed, write an
honest ack (your agent id + the action ids you are DRAINING — the loop
treats an ack as a 180-second lease, not a cure: if the action is still
pending when the lease expires, the tripwire fires again), log to
`worker.log`, and ESCALATE the pending spawn actions to the parent (or
to the self-heal cron) with the exact workerIds and brief paths. The
spawn itself is done by a top-level agent — that is the design, not a
failure of yours. Spawning a "replacement supervisor" subagent does not
fix a dead pool: the replacement cannot spawn either.

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
- Action queue: `<spool>/supervisor-actions.json`, written by your tick
  loop (`scripts/resident-inference-worker/pool_supervisor.py` — the loop
  is the timer, you are the actor: a shell loop cannot spawn a subagent,
  so it never spawns directly). Every action the pool needs — respawn a
  dead worker, spawn for backlog, stop an idle worker, replace a stuck
  worker, rotate a stale session — is a pending entry there with the
  workerId, reason, and brief paths. Your job each turn is to DRAIN it
  (see "The action queue" below) and then acknowledge it — see
  `supervisor-agent-ack.json` next.
- Owner ack: `<spool>/supervisor-agent-ack.json`, written by YOU (not the
  loop) every time you drain the queue:
  `{"updatedAt": <epoch-ms>, "supervisorAgentId": "<your agent id>",
  "actionsDone": ["<action-id>", ...]}`. The loop watches this file: an
  action that sits pending for more than 10 minutes with no ack since it
  was queued means you have stopped acting, and the loop then PAUSES its
  liveness writes so the watcher hook wakes the parent to respawn you
  (see "Owner liveness tripwire"). When the queue is empty you owe
  nothing — an idle owner with nothing to do is a healthy owner.
- Supervisor liveness marker: `<spool>/supervisor-alive.json`, written
  by you every ~30s alongside the pool status (see "Supervisor liveness
  marker" below). A watcher hook treats you as alive while EITHER file
  is fresh (< 120s) and wakes the parent to respawn you only when BOTH
  go stale — so keep both fresh, and never let a slow answer delay them.
- Worker log: `<spool>/worker.log` (workers append; you may read it, do
  not truncate it).

## Spawning a worker (top-level executor only)

This section applies ONLY when you are a top-level agent (the parent,
or a pool-selfheal cron run). If you are a subagent supervisor, see
"Who can spawn workers" above — do not attempt this.

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

## Starting up — adopt live workers (including after a respawn)

On startup, first read every `worker-alive-<workerId>.json` heartbeat in
the spool. Any workerId whose heartbeat is less than 90 seconds old is a
LIVE worker: adopt it — record it as a running worker you own and do NOT
spawn a replacement for that slot. A fresh heartbeat alone is not proof
the agent behind it is working: before adopting, check the worker has
answered recently — its heartbeat `answered` count advanced, or it has
a line in `<spool>/worker.log`, within the last 15 minutes (or the spool
has been empty that whole time). A worker with a fresh heartbeat but no
recent activity is STUCK, not live: do not adopt it — replace it per
"Replacing stuck workers" above. Only spawn workers for workerIds with
no fresh heartbeat, up to the scaling target. This is what makes a
respawn after a reboot safe: the pool's workers usually survive the
supervisor (they are separate agents), and respawning over them would
double the pool and double-claim work. A worker with a live claim file
may just be slow on a big answer — never respawn over it either.

## Scaling rule (every ~30 seconds)

Render check (every tick, before scaling): run
`node ~/workspace/M2l/vidnets/scripts/render-gate.js status`. While it
reports a render ACTIVE, target 0 workers: tell each worker to finish its
current answer then stop (never stop one mid-answer), and do not spawn
replacements or respawn dead workers until the gate reports idle. Resume
normal scaling once idle: 4–8 while media is pending, 2 when idle with
nothing pending (see step 2). A render files ~no inference
requests, so nothing starves; idle workers cost the render RAM just by
existing. (Gate: `vidnets/scripts/render-gate.js`, Area 3 hardening.)
Two-tick confirmation (Nov 21, 2026 — stop_graceful race fix): a single
ACTIVE reading does NOT queue stops. The loop records the first ACTIVE
sighting and only queues `stop_graceful` when the gate still reads ACTIVE
on the next tick; an idle tick clears the pending-active state. A render
that flaps ACTIVE for one tick (15s) must never empty the pool — that
flap emptied it mid-media on Nov 21 while test 20 was at 62%.

1. Count pending work: unclaimed `<spool>/*.request.json` files whose
   inner request `expires_at` is still in the future. Do NOT count
   `.claimed-*` files (already being worked) and do NOT count expired
   files (dead — the sweep below removes them).
2. Target workers:
   - Render ACTIVE → 0 (above — workers stand down for renders).
   - Media pending (`pending > 0`, gate idle) → target =
     clamp(ceil(pending / 3), 4, 8). The floor while media is pending
     is 4, not 2: batched vision files requests in parallel, so a
     2-worker floor drains the batch too slowly. The ceiling stays 8
     and the pending/3 slope above the floor is unchanged. Do NOT raise
     the floor or the ceiling to 8-as-floor: the VM has 1.5 shared CPUs
     and 7.7 GB RAM, so more than 4 workers mostly fight each other for
     CPU — 4 is the sweet spot for draining a media burst.
   - Idle with nothing pending (`pending == 0`, gate idle) → target 2.
     Keep 2 warm — the next burst must not wait for a spawn — but do
     not hold 4 warm when there is nothing to do.
   Executable form of this rule (same numbers, keep in sync):
   `scripts/resident-inference-worker/pool_scaling.py:scaling_target`.
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

## Replacing stuck workers (heartbeat fresh, no answers)

A fresh heartbeat does NOT prove a worker is working. Workers have run
their heartbeat from a detached shell loop that keeps updating the file
after the worker's agent session is gone — on 2026-11-18 worker-3's
agent produced no exec activity and no answer for 30+ minutes,
including a full 8-request burst it took zero of, while its detached
loop kept its heartbeat fresh and its `answered` count frozen at 26.
The pool counted 3 live workers; only 2 answered.

A worker is STUCK when ALL of these hold:
- its `worker-alive-<workerId>.json` heartbeat is fresh (< 90s), AND
- its heartbeat `answered` count has not advanced in 10 minutes, AND
- it has no line in `<spool>/worker.log` in the last 10 minutes, AND
- unclaimed requests were pending at some tick inside that window
  (a worker with nothing to do is idle, not stuck — check the
  `pending` counts in your own pool-status writes).

On stuck: treat it exactly like a dead worker, except first kill its
detached heartbeat loop, or the replacement inherits a zombie
heartbeat: `pkill -f 'worker<N>/heartbeat.sh'` (worker-3's loop) and
`pkill -f 'worker<N>-heartbeat'` (worker-1's loop), then confirm the
heartbeat file goes stale before respawning. Respawn a replacement
with the SAME workerId, log the restart to `<spool>/worker.log` with a
`[supervisor]` prefix, and note it was stuck, not heartbeat-dead.
Never adopt a stuck worker as live on startup (see below).

## Rotating worker sessions (fresh sessions are measurably faster)

Long-lived worker sessions slow down. Measured 2026-11-18 on identical
8-scene query chunks in one burst: a 1-hour-old worker composed in
~10s median; 23–27-hour-old workers composed in 26–30s median, and
one took 73.6s and 136.3s on the same chunks another worker did in
16.5–34.5s; both old workers had also slowed since the previous day
(~20s median Nov 17 → ~44–55s Nov 18). Retire a worker session after
~12 hours alive or ~50 answers since (re)start, whichever comes first:
when the worker is IDLE (no claim file of its own in the spool and no
answer in progress), tell it to stop, wait until its heartbeat goes
quiet (or 60 seconds, whichever comes first), then spawn a fresh
session with the SAME workerId on the current brief. Rotate one worker
at a time — never rotate the whole pool at once. A rotated worker
re-reads the current brief, so rotation is also how brief fixes
(one-claim rule, claim logging) reach live workers whose sessions
predate them.

## The action queue — how the loop asks you to act

Your tick loop (`pool_supervisor.py`, run as your own tracked background
exec — never detached, never `setsid`/`nohup`) does the watching: it
counts pending work, checks heartbeats, sweeps, and writes the status
files every ~15s. It does NOT spawn, stop, or respawn anything itself —
it cannot. Instead it writes every needed action into
`<spool>/supervisor-actions.json` (one entry per action, with
`action`, `workerId`, `reason`, `createdAtMs`, `status`, and the brief
paths). On EVERY turn you:

1. Read the queue. For each `pending` entry, oldest first, claim it by
   rewriting the file with that entry's `status` set to `claimed` and
   your agent id in `claimedBy` — then act on it, then mark it `done`
   (or `failed` with the reason in `detail`). Never claim two at once
   and never act on an entry you have not claimed: the queue is the one
   place where a duplicate spawn would come from.
2. Act exactly as the brief says for that action kind:
   - `respawn` / `spawn`: spawn ONE worker subagent with the full
     `WORKER_BRIEF.md` plus `Your workerId is <workerId>. Spool dir is
     <spool-dir>.` Confirm its heartbeat file appears fresh before
     claiming the next spawn action — a burst of simultaneous spawns is
     the slowest way to get a pool.
   - `stop_idle` / `stop_graceful`: tell that worker (you know its agent
     id — record it at spawn) to finish its current answer then stop.
     Never stop one mid-answer.
   - `replace_stuck`: first run the `pkill` commands in the entry's
     `detail` (they kill the dead worker's detached heartbeat loop, or
     the replacement inherits a zombie heartbeat), confirm the heartbeat
     file goes stale, then spawn the replacement with the SAME workerId.
   - `rotate`: tell the worker to stop when idle, wait until its
     heartbeat goes quiet (or 60 seconds), then spawn a fresh session
     with the SAME workerId.
3. After draining, write `supervisor-agent-ack.json` (see "The pieces")
   with the ids you completed. The ack is what proves to the loop — and
   to the parent — that you are acting, not just alive. If you spawn
   nothing because the queue is empty, no ack is owed.

The loop reconciles the queue every tick: a pending action whose need has
vanished (the worker's heartbeat is fresh again, the pool is back at
target, the render ended) is cancelled by the loop itself — do not
re-queue it, and do not act on a cancelled entry. A `claimed` entry is
yours: the loop never touches it; if you cannot complete it, mark it
`failed` (with the reason) rather than leaving it claimed forever.
Cancelled `stop_*` actions are RETAINED as tombstones (`status:
"cancelled"` + `cancelledAtMs`, pruned after 24h), not deleted — a worker
that already read the stop cannot tell a deleted entry from one that
never existed. A stop the worker has already seen (a `STOP`/`STOPPED`
line naming its action id in `worker.log`) is never cancelled: it stays
pending until self-heal marks it done after the worker exits. A worker
that exits on a seen stop is re-healed immediately (its death is
confirmed on that tick, no 180s wait).

## Owner liveness tripwire

Liveness files must be written by the thing that can act — not by a loop
that can only log. The failure this fixes: from 2026-11-18 ~03:19 UTC a
supervisor agent (f4be9b5d) spawned zero workers and acted on zero deaths
for 31 hours — its loop logged WORKER DEAD worker-1 #1/#2/#3 and WORKER
DEAD worker-2 #1 while the workers recovered on their own — yet
`pool-status.json` stayed fresh the whole time, so the watcher hook never
fired. The loop now enforces the deal: while the queue is empty it writes
liveness normally, but once an action sits pending for more than 10
minutes with no agent ack since it was queued, the loop STOPS writing
both `pool-status.json` and `supervisor-alive.json`, keeps sweeping and
queueing, and writes `supervisor-owner-unresponsive.json` in the spool
explaining why. Both files stale is the watch hook's tripwire: the parent
is woken and spawns a replacement supervisor, whose first job is to drain
the queue (its entries carry the full context). When an ack arrives or
the queue drains, the loop resumes writing both files and deletes the
marker. Never "fix" a tripwire by deleting the marker or hand-writing
fresh liveness files: act on the queue, or let the parent replace you.

## Loop handover

The loop writes `supervisor-loop.pid` (its pid, your agent id, start
time) into the runtime dir at startup. If you are a replacement
supervisor taking over from a previous loop, start your own loop from
`pool_supervisor.py` — it overwrites the pid file, and the old loop sees
a different live pid on its next tick and exits cleanly. Never run two
loops: if your loop exits immediately with a handover note, another loop
is already live — do not start a second one.

## Replacing dead workers

A worker is dead when its `worker-alive-<workerId>.json` heartbeat is
older than 90 seconds AND it has no claim file in the spool (a worker
with a live claim may just be slow on a big answer — do not respawn
over it; its claim will expire and the sweep will clear it). On death,
respawn a replacement with the SAME workerId and log the restart to
`<spool>/worker.log` with a `[supervisor]` prefix — but only when the
slot is within the CURRENT target (see the slot rule below). If a worker dies
three times in a row within a few minutes, stop respawning it and keep
the pool at target-minus-one — something is wrong with that slot, and
a respawn loop helps nobody. (The parent investigates; your job is to
keep the rest of the pool running.)

Death confirmation grace (why the loop waits): a first sighting at >90s
stale is only a sighting. A worker's poll loop ends every ~90s and its
agent restarts it on the next turn, so a healthy worker's heartbeat can
gap ~2 minutes between turns — on 2026-11-19 06:22 UTC worker-2 was
logged WORKER DEAD during exactly such a gap while it was alive the
whole time. The loop therefore confirms a death only when the heartbeat
is still stale 180s after the first sighting, and queues the `respawn`
action then. A heartbeat that freshens in between clears the sighting
and cancels nothing — no action was ever queued.

Slot rule (standing policy — a stale heartbeat alone is NOT enough):
only respawn a dead worker if its slot number is within the CURRENT
target (target = clamp(ceil(pending/3), 2, 8)). At pending 0 the target
is 2, so worker-3 (slot 3) is NOT respawned — its slot is reserved for
scale-up, not for keeping every historical slot filled. Worker-3 died
2026-11-18 ~02:38 UTC (26 answers) and was deliberately left unfilled
at idle; when backlog grows and the target rises to 3+, the pool spawns
worker-3 (lowest free slot inside the new target). The same rule cancels
a queued respawn/spawn whose slot falls above the target when the
backlog drains. A death above target is still counted toward the
three-death hold — it just never queues an action nobody should act on
(an unacted action would eventually, and wrongly, trip the owner
tripwire).

False-death guard: before confirming a death, cross-check `worker.log`
for fresh ANSWERED lines from that worker (last 10 minutes). A
heartbeat starves while a worker answers back-to-back — seen 4x for
worker-1/2 — so a stale heartbeat with fresh ANSWERED lines is a busy
worker, not a dead one: clear the sighting, count nothing, queue
nothing, and treat the slot as busy (do not spawn a duplicate workerId
into it) until its heartbeat goes fresh again.

One continuous outage is one death: after a death is confirmed, the
slot is not re-sighted until its heartbeat goes fresh again. Without
this, a worker dead for 31h re-confirms every ~3 minutes and trips the
three-death hold on a single outage — worker-3 did exactly this
2026-11-19 10:59–11:06 UTC (deaths #2, #3, #4 were one outage), which
held the slot until the hold was corrected. Three deaths means three
separate deaths with a live heartbeat in between.

## Sweep (every ~60 seconds)

Delete, best-effort, in the spool dir only:
- `*.request.json` files whose inner request `expires_at` is past.
- `*.request.json.claimed-*` files whose inner request `expires_at` is
  past (a dead worker's claim — the request is dead either way).

Rescue, best-effort, in the spool dir only (the spool transport does
this too while it waits — this is the backup for requests no transport
is currently waiting on):
- A `*.request.json.claimed-<workerId>` file whose inner request is
  still UNEXPIRED, whose claim file is older than ~5 minutes, and whose
  worker's `worker-alive-<workerId>.json` heartbeat is older than 90
  seconds (or missing) while ANOTHER worker's heartbeat is fresh: rename
  it back to `<id>.request.json` — do NOT delete it, the request is still
  live and a live worker must be able to claim it. Never rescue a claim
  whose worker still heartbeats (a slow worker on a big answer is alive),
  and never rescue when no other worker is alive to take the work.
  The 5-minute threshold matches the spool's CLAIM_RESCUE_GRACE_MS:
  measured composition runs 95-429s per request and a worker composing
  a 32-scene plan cannot heartbeat mid-turn, so a 2-minute threshold
  "rescues" live workers and discards their work (job 89b01295, ~160s
  lost on one request).
  Log every rescue to `<spool>/worker.log` with a `[supervisor]` prefix.
Never delete unexpired requests or unexpired claims. Never touch the
bridge directory itself — the spool is your only channel.

## Pool status (every ~30 seconds)

Write `<spool>/pool-status.json` atomically (tmp file + rename):
`{"updatedAt": <epoch-ms>, "workers": <live count>, "target": <target>,
"pending": <unclaimed pending count>}`.

## Supervisor liveness marker

Every ~30 seconds, alongside the pool status, write
`<spool>/supervisor-alive.json` atomically (tmp file + rename):
`{"updatedAt": <epoch-ms>, "supervisorAgentId": "<your agent id>",
"workers": <live count>, "target": <target>}`.
A watcher hook (polling every 60s) treats you as alive while EITHER
`pool-status.json` or this marker is fresh (updatedAt < 120s old) and
wakes the parent to respawn a replacement supervisor only when BOTH are
stale — that is how the pool survives a VM reboot without a manual
respawn, and also why a single failed write cannot cause a duplicate
supervisor. Write both files on a timer that keeps running while you
work; never let composing, spawning, or a slow tick delay them.
The hook ALSO watches the pool itself: live workers below target for
> 300s while the gate is idle wakes the parent even with fresh liveness
(fresh files prove the loop ticks, not that the pool is staffed —
2026-11-20). And an ack is a 180-second lease, not a cure: when you
write `supervisor-agent-ack.json`, list the `actionIds` you are
draining, and if a queued action is still pending when the lease
expires the loop stops vouching again — so ack only actions you are
actually executing, and mark each `done` in `supervisor-actions.json`
the moment its effect is visible (a fresh worker heartbeat for a spawn,
a stale heartbeat for a stop).

On a deliberate stop (only when the parent tells you to): write the
final `pool-status.json` as above, DELETE `supervisor-alive.json`, and
write `<spool>/supervisor-parked.json`
`{"parkedAt": <epoch-ms>, "reason": "<why the parent stopped you>"}`.
The parked file tells the watcher the stop was deliberate, so it stays
silent until the parent deletes that file. Never write the parked file
for a crash or a reboot — only for a parent-ordered stop.

## What you must never do

- Never answer an inference request yourself, in the spool or anywhere
  else. If you find yourself composing a scene plan, stop: spawn or
  wake a worker instead.
- Never spawn more than 8 workers, and never run two workers with the
  same workerId — including on startup: a workerId with a fresh
  heartbeat (< 90s) is a live worker you adopt, not a slot to fill.
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
  `workers` is 0 during a render, 4–8 while media is pending, 2 when
  idle with nothing pending, and tracks `pending` (more pending → more
  workers, up to 8).
- `<spool>/supervisor-alive.json` `updatedAt` is less than 60 seconds
  old (same tick as the pool status — the watcher accepts either).
- Every live worker has a fresh `worker-alive-<workerId>.json`
  (updatedAt < 90s old).
- No worker's heartbeat `answered` count sits frozen for 10+ minutes
  while requests were pending — a frozen count with a fresh heartbeat
  is a stuck worker, not a live one.
- Worker sessions are rotated after ~12h alive or ~50 answers, so no
  worker runs a multi-day session.
- Pending `*.request.json` files drain while jobs run; `worker.log`
  shows one line per answered request with its workerId.
