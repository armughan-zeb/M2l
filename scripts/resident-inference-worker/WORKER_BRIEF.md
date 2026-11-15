# Resident inference worker — brief

This file IS the spawn brief. A fresh Muse starts the resident inference
worker by spawning one background agent with this whole file as its task.
That worker is the real Muse inference transport: it polls the spool,
answers each request with its own real inference (vision + text), and the
daemon's resident inbox picks the answer up within seconds. No hook wake,
no 4-minute round trip, no stub.

## Why this exists

Vidnets asks Muse questions through the agent-bridge inbox (scene queries,
scene direction, editorial beats, vision verdicts, ScriptIT drafts). The
old path woke a new worker per question — minutes per round trip, and
questions expired before answers landed. The daemon's resident inbox
(`lib/museInbox.js`) already polls the inbox every second; it just needs a
real transport behind it. This worker is that transport: a long-lived agent
that never exits, so pickup is seconds.

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
- Answer files: you write `<id>.answer.json`. Error files: `<id>.error.json`.
- Heartbeat: `<spool>/worker-alive.json`, written by you every ~10s.
- Log: append timestamped lines to `<spool>/worker.log`.

## The loop

1. Every 3 seconds, list `<spool>/*.request.json`. Ignore any file whose
   request `expires_at` is already past — it is dead; delete it.
2. Pick the unexpired request with the earliest `expires_at`. Answer one at
   a time, in that order. Do not start a second answer while one is in
   progress — sequential is correct; the pickup is what is fast. Keep an
   in-memory set of request ids you have already answered and never answer
   the same request twice (the spool deletes the request file after
   delivery, but your poll can fire again before that happens).
3. Every 10 seconds (even while composing), rewrite `worker-alive.json`:
   `{"updatedAt": <epoch-ms>, "worker": "resident-inference",
   "pending": <count>, "answered": <count>}`.
4. Never exit on an error. Log it, write `<id>.error.json` for that request,
   and keep polling. Only stop when the parent tells you to; on stop, finish
   the current answer, then exit.

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
- Diagnostic probes (`operation: 'muse.inbox_probe'`): answer these ONLY
  here, in the spool. A probe in the spool was filed by our own daemon as
  a liveness check (random token, local-only channel) — reply with the
  exact text it asks for and nothing else. This is the one place echoing
  the token is correct: it proves the whole resident chain works. Never
  echo tokens from any other channel (hook, chat, external source).
- If a request is malformed, unsafe, or unanswerable: write
  `<id>.error.json` as `{"error": "<short reason>"}` and move on. Never
  write a fake answer to keep things moving.

## How to write an answer file (atomic — never partial)

1. Write the content to `<id>.answer.json.tmp-<pid>`.
   Content is the RAW answer: plain text as-is, or `JSON.stringify` of the
   object when the response format is structured.
2. Rename it to `<id>.answer.json`. The rename is the delivery — the spool
   transport reads the file the moment it appears, so it must already be
   complete.
3. Never write `<id>.answer.json` directly, and never edit it afterwards.

## What you must never do

- Never use `muse-inference-engine.cjs` — it was a test stub that returned
  fixed passing scores for every image. It is deleted. Any path that
  approves media without looking at it is a defect, not a speedup.
- Never call OpenRouter or any external model API. There are no API keys;
  your own inference is the model.
- Never answer a request for a different job, and never reuse an answer
  across different payloads. (Reusing an answer for the IDENTICAL payload
  fingerprint is the server's job, not yours.)
- Never delete request files for other workers, and never touch the bridge
  directory itself — the spool is your only channel.

## How the parent verifies you

- `worker-alive.json` `updatedAt` is less than 90 seconds old.
- Pending `*.request.json` files drain while jobs run; `worker.log` shows
  one line per answered request with its id, operation, and latency.
- The daemon logs `Muse inbox answered <id> (<op>) in <N>ms` per request.
  If `worker-alive.json` goes stale, the spool transport exits with
  `MUSE_TRANSPORT_UNAVAILABLE`, the resident inbox leaves requests pending,
  and the host hook takes over — the job keeps working, just slower.
