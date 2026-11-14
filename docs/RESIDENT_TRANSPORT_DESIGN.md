# Resident answerer transport — design note

Status: **design only, not implemented.** This note scopes what is missing
before the resident inbox can answer Vidnets requests from Muse locally,
so a build decision can be made without guessing at the contract.

## What exists today

- `lib/museInbox.js` `startResidentInbox({ root, answer, concurrency = 8,
  pollMs = 1000 })` polls the bridge dir (plus `fs.watch`), answers pending
  requests with a 60s target, and writes `resident-status.json` every 5s.
  `completeLocally` submits an answer by spawning Python to call
  `complete_request` directly (20s budget, no HTTP round-trip).
- `muse-agent.js:566` builds `transport = vidnets.inferenceTransport()` and
  starts the resident inbox **only `if (transport)`** (lines 577–591).
  With no transport configured, the resident inbox never runs and the
  1-minute cron answerer is the only answerer — serial, against a 120s wall.
- `adapters/vidnets.js` `inferenceTransport()` reads the environment:
  `MUSE_INFERENCE_COMMAND` wins; else `MUSE_INFERENCE_URL` (http/https only);
  else `null`. Neither is set on this VM.

## The contract a transport must satisfy

`runInferenceCommand(command, request, workDir, { timeoutMs = 120000 })`
spawns the command once per request:

- stdin: one JSON object `{ type: 'inference', request, instructions,
  responseFormat }`. `request` is the full bridge request (id, job_id,
  operation, kind, messages, response_format). Vision (`visual_review`)
  messages carry image parts that reference local files under the Vidnets
  runtime temp dir — the command must be able to read them.
- environment: `MUSE_INFERENCE_REQUEST_ID`, `MUSE_INFERENCE_KIND`,
  `MUSE_INFERENCE_OPERATION`.
- stdout: the answer. `parseInferenceAnswer` tries `JSON.parse` first. For
  a structured request (`response_format.type` or `json_schema`) the content
  is `data.content ?? data`; for a text request it is `data.content ??
  data.text ?? data.answer ?? data`. It must be non-empty, or the transport
  is treated as having returned nothing.
- deadline: the caller passes `timeoutMs = remaining() - 5000` where
  `remaining()` is the milliseconds left on the request's 120s wall, so the
  command has at most ~115s and should aim for under 60s.
- `runInferenceHttp(url, request, ...)` posts the same JSON body and parses
  the response text the same way.

`answerOne` then writes the answer to `workDir/inference/<id>.txt|json` and
submits it with `vidnets answer <id> <file>`.

## What is missing

There is **no local Muse-answering CLI today**: nothing on this VM reads a
request JSON on stdin and returns a Muse answer on stdout. The cron
answerer works by having an agent read the bridge dir and compose answers
by hand; that is not a command the worker can spawn.

## Options

1. **Command transport (`MUSE_INFERENCE_COMMAND`)** — a small CLI (Node or
   Python) that reads the request JSON on stdin, calls Muse's model with
   the request's messages (text + local image files for vision), and prints
   the answer. Simplest to deploy: one env var, no service to keep alive.
   The CLI process is spawned per request, so model-session reuse and
   startup cost matter for the 60s target.
2. **HTTP transport (`MUSE_INFERENCE_URL`)** — a long-lived local service
   (e.g. on 127.0.0.1) that accepts the same POST body and returns the
   answer. Better for warm model sessions and for vision (images can be
   served or read from disk by the service), but it is another process to
   supervise, and its failure mode must be fail-closed: if it is down, the
   resident inbox must not claim to be healthy.
3. **Keep the cron** as the fallback in all cases. The resident inbox and
   the cron answer through the same `complete_request` path, so a request
   answered by one is invisible to the other (400 "already answered" is
   harmless). Do not remove the cron until the transport has answered a
   full production job without an expiry.

## Decisions needed before building

- **Where does the model call come from?** A Muse API key/credential the
  worker may use, a local model endpoint, or the Hatch agent runtime
  itself? This decides option 1 vs 2 and who owns the credential. The
  worker must never print or persist that credential, and the transport
  command must not receive the CMS worker token in its environment.
- **Vision input.** `visual_review` requests carry local image paths.
  Confirm the transport can read those files (same filesystem, same
  permissions) and what image size/count per request keeps a verdict under
  60s.
- **Concurrency vs the 8 GB box.** The resident inbox runs up to 8 answers
  at once; each transport invocation spawns a process. Bound the transport
  concurrency to what the box can hold alongside the Vidnets server and a
  render (2–4 is the conservative starting point, not 8).
- **Health signal.** `refreshInboxHealth` gates CMS claiming on the probe
  being answered. The probe is a plain nonce echo; the transport must
  answer it immediately (no model call) or the loop will pause claiming
  on every probe cycle.
- **Fallback order.** If the transport errors or times out on a request,
  the resident inbox should leave it pending for the cron (or a retry)
  rather than answering with a generic fallback — a wrong vision verdict
  or a template narration is worse than a late answer.

## Acceptance test (when built)

Run a production job with the cron disabled and `MUSE_INFERENCE_COMMAND`
(or `_URL`) set: zero request expiries, zero probe expiries, and the CMS
claiming never paused. Then re-enable the cron as a fallback and repeat.
