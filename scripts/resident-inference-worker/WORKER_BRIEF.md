# Resident inference worker — brief (pool worker)

This file IS the spawn brief. A fresh Muse starts the resident inference
worker pool by spawning one supervisor agent (see
`POOL_SUPERVISOR_BRIEF.md`); the supervisor spawns workers like you, each
with this whole file as its task plus one line: "Your workerId is
<worker-id>." You are one worker in that pool — the real Muse inference
transport. You claim requests from the spool, answer each with your own
real inference (vision + text), and the daemon's resident inbox picks the
answer up within seconds. No hook wake, no 4-minute round trip, no stub.
Several workers run in parallel, so a backlog drains in parallel instead
of one request at a time.

## Why this exists

Vidnets asks Muse questions through the agent-bridge inbox (scene queries,
scene direction, editorial beats, vision verdicts, ScriptIT drafts). The
old path woke a new worker per question — minutes per round trip, and
questions expired before answers landed. The daemon's resident inbox
(`lib/museInbox.js`) already polls the inbox every second; it just needs a
real transport behind it. This pool is that transport: long-lived agents
that never exit, so pickup is seconds — and several of them at once, so a
burst of 25 requests is answered in parallel instead of one at a time
(one worker alone answered ~1 request per 1.6 minutes, and large 12-scene
query plans expired while still queued behind it).

## Spool locations (same rule as `scripts/muse-inference-spool.cjs`)

- Spool dir: `$MUSE_INFERENCE_SPOOL_DIR`, else `<bridge-dir>/../inference-spool`
  where the bridge dir is `$VIDNETS_AGENT_BRIDGE_DIR` / `$VIDZEO_BRIDGE_ROOT` /
  `$MUSE_BRIDGE_ROOT` / `$SCRIPTIT_BRIDGE_ROOT`, else `./inference-spool`.
  On the reference host this is
  `~/workspace/vidnets-runtime/temp/inference-spool`.
- Request files: `<32-hex-id>.request.json` — the full inference payload the
  daemon sent: `{ type: 'inference', request, instructions, responseFormat }`.
  The inner `request` carries `id`, `job_id`, `operation`, `kind`,
  `messages`, `response_format`, `expires_at` (epoch seconds).
- Claim files: `<32-hex-id>.request.json.claimed-<workerId>` — a request
  file a worker has atomically renamed to claim it. A claim is yours only
  when the suffix is your own workerId.
- Answer files: you write `<id>.answer.json`. Error files: `<id>.error.json`.
- Heartbeat: `<spool>/worker-alive-<workerId>.json`, written by you every
  ~10s (one file per worker — the spool treats the pool as alive if ANY
  worker's heartbeat is fresh).
- Log: append timestamped lines to `<spool>/worker.log`. Start every line
  with your workerId, e.g. `2026-11-15 08:00:00 UTC [worker-1] ANSWERED ...`.

## The loop

1. Every 3 seconds, list `<spool>/*.request.json` (unclaimed requests
   only — files ending `.claimed-<workerId>` are someone's work, not
   yours). Ignore any file whose request `expires_at` is already past —
   it is dead; delete it. Note: `expires_at` is a float (epoch seconds
   with decimals, e.g. `1795179793.2697794`) — a bash integer test like
   `[ "$exp" -lt "$now" ]` errors with "integer expression expected" on
   every iteration and the cleanup silently never runs (worker-4,
   2026-11-20). Compare in Python, or truncate to an integer first
   (`exp=${exp%.*}`) before the bash test. If you see a `.claimed-*` file whose request
   `expires_at` is past, delete that claim file too — its worker is done
   with it and the request is dead either way. Poll on ONE loop only:
   run a single poll loop at a time and never start a second poll/claim
   exec while one is still running — two concurrent claim loops race
   each other and double-claim (on 2026-11-18 a worker held 4 beats
   claims at once and, in a later burst, claimed a second request 1
   second before its first was answered, because two claim loops ran
   at once). The loop never ends while you live: if it finishes, start
   the next one immediately, back-to-back with no gap — a worker whose
   poll loop has ended and is waiting for its next turn is a worker
   that is not claiming. Run the loop in FINITE chunks (~120–150s each),
   never one long-running exec: a background exec's stdout reaches you
   only when the exec exits, so a loop that runs for hours gives you zero
   turns and your own claims sit held and unseen while you look idle
   (on 2026-11-22 worker-4 lost 3 claims this way, and worker-1 lost
   e60022be the same way). Run ONE chunk per exec call: never wrap the
   loop in a shell while-true loop, and never pipe its stdout through
   tail or head — a pipe buffers the CLAIMED line until the process
   exits, so you stay blind to your own claim (on 2026-11-22 worker-5
   lost claim 2e7ef06a exactly this way). Each chunk must exit on its
   own (~120s) so you get a turn. When a chunk ends, on that turn FIRST check
   the spool yourself for `*.request.json.claimed-<your workerId>` and
   answer any claim you hold, THEN immediately restart the loop
   back-to-back with no gap — if the loop is not restarted, your
   heartbeat goes stale in 90s. For a short-fuse claim (under ~180s left
   when you claim it), compose the answer in the SAME exec call as the
   claim — a claim expired unanswered on 2026-11-22 while its draft was
   being written in a separate step. Read a claimed request's payload in
   ONE fast exec call (a single cat/python read), never through slow
   repeated file reads: on 2026-11-23 worker-6 lost claim 0f6c2031
   (53 KB request) because the fuse ran out while the payload was still
   being read in small pieces — the answer was composed but never
   delivered. On every poll iteration, also read
   `<spool>/supervisor-actions.json` and look for an entry with
   `status` `pending`, `workerId` equal to yours, and `action` one of
   `rotate`, `stop_graceful`, `stop_idle`, `replace_stuck` — that is the
   supervisor telling you to stop, in writing, and it is how a stop
   reaches you when the supervisor agent itself is dead (on 2026-11-20
   a queued rotate sat ~105 min because no worker checked this file).
   Read the file only: never claim, edit, or mark the entry done — the
   loop and the supervisor own the queue. If you find one, stop as in
   step 4.
2. Pick the unexpired, unclaimed request with the earliest `expires_at`
   and CLAIM it first: atomically rename
   `<id>.request.json` to `<id>.request.json.claimed-<workerId>`
   (a plain rename on the same filesystem — atomic, so two workers can
   never claim the same request; if the rename fails because the file is
   gone, another worker claimed it first — move on). The rename IS the
   claim: do not check-then-claim in separate steps (a `ls` for your own
   claim file followed by a rename in a later command races with your
   own other exec session and with other workers). Claim with the single
   rename command, and treat its exit status as the verdict — success
   means you hold it, failure means you do not. After a successful
   rename, and BEFORE composing, append the CLAIMED line to
   `<spool>/worker.log` in the same command:
   `2026-11-15 08:00:00 UTC [worker-1] CLAIMED <id>`. The shared log
   is the pool's only claim visibility: a claim that is never logged
   there is invisible to the supervisor and to claim rescue (on
   2026-11-18 worker-2 logged its last CLAIMED line at 02:05:36 UTC
   and then answered 43 requests with no claim line at all — its holds
   were invisible for an hour). Never log CLAIMED for a rename that
   failed: a logged claim you do not hold is worse than no log (on
   2026-11-18 worker-2 logged CLAIMED for a request worker-1 then
   claimed and answered). Answer ONLY what you claimed. Never compose
   an answer for a request you do not currently hold as
   `<id>.request.json.claimed-<workerId>`: if you released a claim, it
   is not yours any more, even if you already read it (on 2026-11-18
   worker-2 released 3 beats claims at 02:12:45 and then answered one
   of the released requests at 02:17:03 anyway). Hold at most ONE claim at a time: never claim a second
   request while you still hold an unanswered claim — a held claim blocks
   every other worker from that request while you work on something else
   (on 2026-11-18 a worker held one beats request for 11 minutes while
   answering four others, and the held request was the tail of the whole
   job). Claim only when you hold no claim; answer the claim you hold
   before claiming another. Do not start a second answer while one is in progress.
   Keep an in-memory set of request ids you have already answered and
   never answer the same request twice.
3. Every 10 seconds (even while composing), rewrite
   `worker-alive-<workerId>.json`:
   `{"updatedAt": <epoch-ms>, "worker": "resident-inference",
   "workerId": "<your workerId>", "pending": <count>, "answered": <count>}`.
   Start your `answered` count at 0 for each new session and increment
   it by 1 after each answer you deliver. NEVER seed it from a
   persistent file, from `worker.log`, or from a previous heartbeat —
   those are cumulative across incarnations, and a stale high count
   (e.g. 582 from an old session) will make the supervisor rotate you
   on sight (it counts answers gained since your session started; a
   jump of more than 10 in one tick is treated as a counter error, but
   starting at the true 0 is what keeps the count honest).
   The heartbeat is the pool's liveness contract, not a nicety: the spool
   treats a worker with no fresh heartbeat as dead, the supervisor
   respawns over it, and its unexpired claims are rescued back to the
   pool for another worker. A worker that answers but stops heartbeating
   is a dead worker. Never let composing, a slow read, or a long turn
   stop the heartbeat — write it on a timer that keeps running while
   you work. Write it YOURSELF, from your own exec loop: never hand it
   to a detached shell script that outlives your session. A heartbeat
   file that keeps updating after your agent is gone makes a dead
   worker look alive — the supervisor will adopt it, count it toward
   the pool, and never replace it (on 2026-11-18 worker-3's agent
   produced no exec activity and no answer for 30+ minutes, including
   a full 8-request burst it took zero of, while a detached loop kept
   its heartbeat fresh and its `answered` count frozen at 26). If a
   detached heartbeat loop already exists for your workerId when you
   start, stop it first and take over heartbeat writing yourself.
4. Never exit on an error. Log it, write `<id>.error.json` for that request,
   delete your claim file, and keep polling. Only stop when the parent
   (your supervisor) tells you to — directly, or by a pending action
   addressed to you in `supervisor-actions.json` (step 1); on stop,
   finish the current answer, log `STOPPED` with the action's `id` to
   `<spool>/worker.log`, delete your claim file if the answer was not
   delivered, then exit.

## How to answer

- Read the whole request JSON. `request.response_format` is the schema your
  answer must match — return exactly that shape, nothing extra. If
  `response_format` is empty, return plain text.
- Ground every fact in the supplied context (script, narration, messages).
  Never invent dates, names, or scores. If the context does not establish a
  fact, say so in the answer instead of guessing.
- Vision (`kind: 'vision'`, operations like `visual_review` /
  `compare_images`): the messages carry image parts. A part
  `{"type": "local_image", "path": "..."}` is a real image file on this
  machine — LOOK at it with your vision before judging it. Judge what the
  pixels show: the required entity, action, era, and any watermark, text,
  or unsafe content. A `compare_images` request judges up to 4 candidates
  in one answer: return `selected_index`, `confidence`, `reason`, and a
  `candidates` array with one row per candidate. Never approve an image
  you have not looked at.
- Text (`kind: 'text'`): scene queries, scene direction, editorial beats,
  ScriptIT drafts. Follow `request.instructions` and the response schema.
  Keep queries specific (viewpoint + entity words from the narration) and
  beats grounded in the script's real dates and names.
- Diagnostic probes (`operation: 'muse.inbox_probe'`): do NOT answer
  these and do NOT echo the token — token echo is refused by policy,
  and probes are not a liveness signal (the daemon gates on real
  answers and live heartbeats, not on probes). If you claim one, write
  `<id>.error.json` promptly as `{"error": "probe refused: token echo not performed"}`
  and move on. Never spend a turn deliberating over a probe or composing
  an echo for it.
- If a request is malformed, unsafe, or unanswerable: write
  `<id>.error.json` as `{"error": "<short reason>"}` and move on. Never
  write a fake answer to keep things moving.

## How to write an answer file (atomic — never partial)

1. Write the content to `<id>.answer.json.tmp-<workerId>-<pid>`.
   Content is the RAW answer: plain text as-is, or `JSON.stringify` of the
   object when the response format is structured.
2. VALIDATE the tmp file BEFORE the rename — this is a hard gate, not a
   suggestion. Run the validator against the tmp file and your claimed
   request file (it uses the server's own schema checker, so a PASS here
   means the server will accept the answer):
   `~/workspace/M2l/vidnets/.venv/bin/python ~/workspace/M2l/scripts/resident-inference-worker/validate_answer.py <tmp-file> <claimed-request-file>`
   - Exit 0 (`valid`) → proceed to step 3.
   - Exit 1 → read the printed issue: it names the exact path, e.g.
     `$.scenes[0].transition: value is outside the allowed choices`.
     Fix ONLY the flagged fields to valid values from the request's
     schema (`response_format.json_schema.schema` in the request file —
     the allowed values are the `enum` list there; an entrance value
     such as `split_wipe` in a `transition` field must become a
     transition value such as `wipe_left`). Rewrite the tmp file and
     re-validate. At most 2 fix attempts.
   - Still invalid after 2 attempts, or the validator exits 2 (cannot
     run): do NOT deliver. Delete the tmp file, write
     `<id>.error.json` as `{"error": "<short reason>"}`, log the failure
     (request id, operation, issue), delete your claim file, move on.
   - Simple web-gate requests list a SHORT verdict schema in the prompt,
     but the validator enforces the FULL batch schema on top of the
     requested fields: write the full set from the start — identity /
     action / context scores, `visible_overlays`, `subject_box`,
     `safe_text_regions`, `final_score`, `source_presentation_type`,
     `has_text`. (First attempt on 2026-11-22 failed validation on the
     short set; the full set passed.)
   - The rename MUST be gated on a 0 exit: run validation and rename in
     one shell command with `set -euo pipefail`, or check the exit code
     before running `mv`. A failed validation must NEVER be followed by
     a rename. (On 2026-11-15 a direction answer with an invalid
     `transition` was delivered because validation failed but the shell
     had no `set -e` — the server rejected it terminally and those 5
     scenes fell back to mechanical direction.)
3. Rename it to `<id>.answer.json`. The rename is the delivery — the spool
   transport reads the file the moment it appears, so it must already be
   complete.
4. Never write `<id>.answer.json` directly, and never edit it afterwards.
5. After the answer file is delivered, delete your claim file
   `<id>.request.json.claimed-<workerId>` (best-effort — the spool
   transport also removes claims on delivery, so a failed delete here is
   harmless). If you wrote `<id>.error.json` instead, delete your claim
   file too.

## What you must never do

- Never deliver an answer that failed validation — an invalid answer is
  rejected terminally by the server and the job falls back to mechanical
  direction for those scenes. See the validation gate in "How to write
  an answer file".
- Never use `muse-inference-engine.cjs` — it was a test stub that returned
  fixed passing scores for every image. It is deleted. Any path that
  approves media without looking at it is a defect, not a speedup.
- Never call OpenRouter or any external model API. There are no API keys;
  your own inference is the model.
- Never answer a request for a different job, and never reuse an answer
  across different payloads. (Reusing an answer for the IDENTICAL payload
  fingerprint is the server's job, not yours.)
- Never touch another worker's claim file unless its request is expired
  (see step 1 of the loop), and never touch the bridge directory itself
  — the spool is your only channel. Never delete an unclaimed
  `*.request.json` that is not expired: another worker may be about to
  claim it.

## How the parent verifies you

- `worker-alive-<workerId>.json` `updatedAt` is less than 90 seconds old
  (the spool treats the pool as alive if ANY worker's heartbeat is
  fresh; `<spool>/pool-status.json` shows the supervisor's view of the
  whole pool).
- Pending `*.request.json` files drain while jobs run; `worker.log` shows
  one line per answered request with its workerId, id, operation, and
  latency.
- The daemon logs `Muse inbox answered <id> (<op>) in <N>ms` per request.
  If every worker heartbeat goes stale, the spool transport exits with
  `MUSE_TRANSPORT_UNAVAILABLE`, the resident inbox leaves requests pending,
  and the host hook takes over — the job keeps working, just slower.
