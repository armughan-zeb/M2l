# Vidzeo Muse Worker

An authenticated worker for the Vidzeo CMS **Muse mode**. It pairs with the CMS,
claims one production job at a time, produces narration plus a 1080p master,
and uploads the artifacts (thumbnails are generated locally by Pikgen). The CMS validates every artifact again
before packaging and publishing.

The worker is fully Linux/Ubuntu native (also compatible with Boost cloud, VPS,
containers, and Windows). It never uses an OpenRouter key or any provider
fallback for agent jobs. Reasoning, writing and vision belong to the Muse
environment; this repository only transports jobs, runs the operator's commands,
and performs objective media checks.

**Muse inference is compulsory.** Every vision call, script-writing call and
Vidnets brain / LLM call must go through Muse via the `external_agent`
agent-bridge inbox, answered by the `vidnets-inbox-watch` hook operator.
`node muse-agent.js start` refuses to run unless the bridge is verified
(Vidnets server healthy, agent-bridge inbox writable, hook enabled, no
OpenRouter key in the environment — see `lib/museInference.js`), and
`MUSE_AUTO_LOOP=1` never exempts that check. `node muse-agent.js doctor`
checks the same bridge and FAILS when it is absent. A job whose inference
goes unanswered fails closed; a job with 0 real-media scenes is never
delivered as graphics. Before failing for 0 real-media scenes the
render wrapper attempts one bounded media re-plan with fresh queries (see
`render-vidnets-linux.js`).

**Filter-best (Nov 9, 2026).** Vidnets' native mixed-media flow is used
as built: LLM writes queries from the scenes → search YouTube clips +
web images → filter the BEST (rank, keep the best real media; reject
only clearly-wrong footage) → Remotion edits. Vision is advisory by
default (`MUSE_MEDIA_STRICT=0`); the only hard rule is the delivery
rule above. See `rules.md`, `workflow.md`, and `HYBRID_WIRING.md`
("Filter-best").

**Simplified pipeline (Nov 9, 2026).** One data pass (all research
providers in parallel once, one shared deadline
`MUSE_RESEARCH_TIMEOUT_MS`, default 180s), one script (at most
`MUSE_SCRIPT_MAX_DRAFTS` model calls, default 2: draft 1 + one fix
pass — a draft that still fails the gate is returned non-publishable
and the job fails loudly), one media pass per scene (YouTube clips
first, then web images, then stock; one bounded re-plan per job
before a 0-real-media fail), one monitor. All knobs live in
`.env` / `.env.example`. Full flow and settings:
`HYBRID_WIRING.md` ("Simplified pipeline").

**Health monitor.** The `vidnets-health-monitor` cron runs
`~/hooks/scripts/vidnets-health-monitor.sh` every 5 minutes: it checks
the daemon (alive + connected), Vidnets `:5010` (healthy + memory
admission), the CMS (reachable + outage duration), the inbox-watch hook
(enabled), stale/past-half-TTL inbox requests, and disk/tmp space. It
fixes what it can itself (restart a dead daemon via `start-daemon.sh`,
restart a dead Vidnets server, mark expired inbox requests) and only
messages the user when it cannot fix something or a job is affected.
It never claims, delivers, or restarts a job mid-render. It is the
only monitor — the old `vidnets-recovery-watch` self-removed on Nov 9.
Full rules: `HYBRID_WIRING.md` ("CMS-outage resilience + health monitor").

**Daemon resilience.** The daemon survives CMS 502/530 outages
in-process (`lib/cmsRetry.js`: transient errors retry with exponential
backoff, capped at 60s; a saved job's resume is deferred, never dropped
on a transient error). systemd is not used as a supervisor — see
`HYBRID_WIRING.md` for why.

## Requirements

- **Linux (Ubuntu 20.04/22.04/24.04, Debian, Boost)** or Windows 10/11 / macOS.
- **Node.js 18.17 or newer** (`node -v`).
- **FFmpeg & FFprobe** installed (`sudo apt install -y ffmpeg` on Ubuntu/Debian).
- **Python 3** (`sudo apt install -y python3` on Ubuntu/Debian).
- A CMS running on port 3210 with a paired worker in **Muse agents**.
- For the built-in renderer: Vidnets (`./vidnets/VIDNETS_AGENT.sh` on Linux,
  `.\vidnets\VIDNETS_AGENT.cmd` on Windows).

No external npm packages are required for the worker itself.

## Quick Start on Linux / Ubuntu / Boost

Run the automated Linux setup script to verify dependencies, configure permissions, and test health:

```bash
chmod +x setup-linux.sh start.sh
./setup-linux.sh
```

To run as a systemd service in the background on Ubuntu, see [Systemd Service](#systemd-service).

---

## Pair once

1. Open the CMS, go to **Muse agents**, and choose **Pair worker**.
2. Run the command shown by the dialog:

```bash
node muse-agent.js pair --cms http://127.0.0.1:3210 --code MUSE-XXXX-XXXX --name "Linux GPU Worker"
```

The one-time code is exchanged for a worker token. The token is written only to
`.muse-agent.json` (mode 0600 on Linux/POSIX), never printed and never sent to
`/api/settings`. The CMS stores only its SHA-256 digest.

3. In **Muse agents**, select this worker as **Active Worker**. Only the active
   worker can claim jobs.
4. Verify the installation:

```bash
node muse-agent.js doctor
```

5. Start the worker loop:

```bash
./start.sh
# or directly:
node muse-agent.js start
```

`start` registers a heartbeat, resumes a saved job after a crash, claims jobs,
reports progress and uploads each finished job. Use `--once` to process at most
one job and exit. Before it heartbeats or claims anything, the automated loop
verifies the compulsory Muse inference bridge (see above). This keeps a worker
with no inference from taking CMS jobs and delivering graphics-only videos.

The automated loop requires the Muse inference bridge: `MUSE_RENDER_COMMAND`
set to `render-vidnets-linux.js` (Vidnets + Muse agent-bridge inbox) with the
`vidnets-inbox-watch` hook enabled, or the built-in Vidnets path with
`MUSE_INFERENCE_COMMAND` / `MUSE_INFERENCE_URL`. An OpenRouter key anywhere in
the environment refuses to start. If the Muse agent itself writes the narration
and renders in its own environment, use the agent-driven
`claim` → write/render into the returned work folder → `deliver` flow instead
of `start` (that flow is a human agent in the loop, so the start gate does not
apply to it).

If the CMS address changes, update the saved endpoint without replacing the
worker credential:

```bash
node muse-agent.js configure --cms https://your-current-cms-address
node muse-agent.js status
```

---

## Two production workflows

### 1. Agent-driven (first-class)

Use this when the Muse environment is an AI agent that writes and renders with
its own tools. No `MUSE_SCRIPT_COMMAND` or `MUSE_RENDER_COMMAND` is required.

```bash
node muse-agent.js claim --wait 60
```

The command prints JSON with the job and its exact paths:

```text
work/<jobId>/script.txt      write the complete narration here
work/<jobId>/video.mp4       render the 1080p master with audio here
work/<jobId>/inference/      Vidnets reasoning/vision requests (built-in renderer)
```

After the files exist:

```bash
node muse-agent.js deliver --job <jobId> --skip-thumbnail
```

`deliver` validates the narration length and the master duration/1080p/audio/full
decode, then uploads and completes the job. Thumbnails are generated locally on the
CMS using Pikgen, so the worker delivers script + video only. Pass `--script` or
`--video` to deliver files produced elsewhere; they are copied into the job checkpoint.

If the work cannot be finished, report an actionable reason:

```bash
node muse-agent.js fail --job <jobId> --error "Vidnets could not download the source clip: HTTP 403"
```

### 2. Automated loop

Use this when the Muse environment exposes commands. Configure the adapters in
`.env` (copy `.env.example`), then run `start` or `./start.sh`.

| Adapter | Variable | Contract |
| --- | --- | --- |
| Narration | `MUSE_SCRIPT_COMMAND` (optional — defaults to ScriptIT) | Job JSON on stdin; print narration text or `{"scriptText":"..."}` |
| Renderer | `MUSE_RENDER_COMMAND` | JSON on stdin (`job`, `scriptText`, `scriptPath`, `workDir`, `outputPath`); print `{"videoPath":"...","thumbnailPath":"..."}` |
| Thumbnail | `MUSE_THUMBNAIL_COMMAND` | JSON on stdin (`job`, `scriptText`, `videoPath`, `workDir`); print `{"thumbnailPath":"..."}` |
| Vidnets inference | `MUSE_INFERENCE_COMMAND` or `MUSE_INFERENCE_URL` | One Vidnets request JSON on stdin (or POSTed); return the exact text/JSON answer |

**Narration defaults to ScriptIT** (`scriptit/cli/generate.js`, see
`scriptit/CLI.md`): it researches the title, writes narration through an
editorial gate (hook, pacing, topic fit, evidence support, concrete-entity
specificity), and scores opening-hook variants. It needs no OpenRouter key —
Muse answers its inference requests through the Vidnets agent-bridge inbox
(the `vidnets-inbox-watch` hook wakes an operator for each request). A script
is publishable only if the gate passes and at least one external evidence
claim supports it; a failed draft is left in `script-draft.json` and the job
fails honestly instead of shipping generic narration. Set
`MUSE_SCRIPT_COMMAND` to use a different writer, or
`MUSE_SCRIPT_ALLOW_TEMPLATE_FALLBACK=1` to bring back the old template
synthesizer (opt-in only, labelled generic).

Production renders use **Vidnets through `render-vidnets-linux.js`**
(`MUSE_RENDER_COMMAND`):
- Vidnets is started through `VIDNETS_AGENT.sh` (Linux) or `VIDNETS_AGENT.cmd` (Windows) and runs with
  `llm_provider: external_agent`; it never receives an OpenRouter key
  (`adapters/vidnets.js` sends `openrouter_api_key: ''`, and the server's
  visual verifier discards any environment key for external_agent jobs).
- While the render runs, every writing, director, query and vision request
  is posted to Vidnets' local agent-bridge inbox, and the
  `vidnets-inbox-watch` hook wakes a Muse operator to answer it. That is
  the only inference path in production — `MUSE_INFERENCE_COMMAND` /
  `MUSE_INFERENCE_URL` are an alternate transport and are deliberately
  unset in the production wiring (a second answerer would double-answer).
- The wrapper fails the job instead of delivering graphics if the finished
  edit plan has no Muse-verified real-media scene, or no Muse-answered
  vision verdict at all.
- Set `MUSE_VIDNETS_ROOT` if Vidnets lives outside `./vidnets`.
- Built-in Vidnets needs `PEXELS_API_KEY` and `PIXABAY_API_KEY` for stock rescue.

There is no fallback to the native Remotion engine: it fetches no media and
uses no Muse inference, so `adapters/video.js` throws instead of rendering
graphics when no Muse-inference render path is configured. Without a
verified bridge, external-agent Vidnets jobs cannot answer their scene
requests; `doctor` reports this as blocking (`Muse inference (compulsory)`).

---

## Commands

```text
node muse-agent.js pair --cms <url> --code <MUSE-XXXX-XXXX> [--name "..."]
node muse-agent.js start [--once]
node muse-agent.js claim [--wait 60]
node muse-agent.js deliver [--job <id>] [--script <file>] [--video <file>] [--thumbnail <file>]
node muse-agent.js fail --job <id> --error "reason"
node muse-agent.js status
node muse-agent.js doctor
node muse-agent.js unpair
```

- `status` shows pairing, CMS connectivity, mode and the active saved job. It
  does not print the token.
- `doctor` checks Node, Python 3, the credential, CMS reachability, the heartbeat,
  the work folder, FFmpeg/FFprobe, the adapters, the Vidnets service, inference and
  stock keys. `WARN` items are non-blocking; `FAIL` items must be fixed.
- `unpair` removes the local credential and resume marker but keeps saved work.

---

## Systemd Service

To run the Muse worker as a background service on Ubuntu / Boost:

**Option A — Standard Service:**

1. Copy `vidzeo-muse.service` to `/etc/systemd/system/`:

```bash
sudo cp vidzeo-muse.service /etc/systemd/system/vidzeo-muse.service
sudo systemctl daemon-reload
sudo systemctl enable vidzeo-muse
sudo systemctl start vidzeo-muse
sudo systemctl status vidzeo-muse
```

**Option B — Templated Per-User Service (`vidzeo-muse@<user>`):**

1. Copy `vidzeo-muse@.service` to `/etc/systemd/system/`:

```bash
sudo cp vidzeo-muse@.service /etc/systemd/system/vidzeo-muse@.service
sudo systemctl daemon-reload
# Run as your specific Ubuntu / Boost user (e.g. ubuntu):
sudo systemctl enable vidzeo-muse@ubuntu
sudo systemctl start vidzeo-muse@ubuntu
sudo systemctl status vidzeo-muse@ubuntu
```

---

## Checkpoints and recovery

Each job keeps its own folder:

```text
work/<jobId>/job.json          dispatched payload and digest
work/<jobId>/script.txt        accepted narration
work/<jobId>/video.mp4         rendered master
work/<jobId>/video-meta.json   bytes, SHA-256 and engine
work/<jobId>/thumbnail.*       delivered thumbnail
work/<jobId>/vidnets/          built-in renderer package
work/<jobId>/inference/        answers sent to Vidnets
```

- A retry of the same run reuses completed narration, the master and the
  thumbnail; it does not re-render what already passed local checks.
- A materially different payload archives the old folder under
  `work/archive/` instead of deleting it.
- `work/worker.lock` prevents two workers on the same machine. A stale lock from
  a dead process is replaced automatically.
- After a CMS restart, a claimed job is requeued and the reconnecting worker
  resumes it. The CMS keeps the run package, so nothing is published twice.
- An operator stop (`Ctrl+C` or `SIGTERM`) reports the job as failed with a
  resume hint and keeps the saved work. Child process trees are cleanly killed
  via POSIX process groups without orphan processes.

---

## CMS-side limits

- Claim window: 2 minutes after the worker is free. A second job queued behind a
  busy single worker is held without a deadline.
- Inactivity: 20 minutes without real progress. Keepalives do not extend this
  deadline; stage changes and uploaded bytes do.
- Hard limit: 6 hours per job attempt.
- Uploads: streamed raw bytes with `Content-Length`; video at least 1 MB,
  thumbnail at least 1 KB; magic-byte checked and SHA-256 recorded.
- Completion requires narration of at least 40 characters and an uploaded video
  whose byte count still matches the file on disk.

---

## Security

- The worker token is a bearer credential. Keep `.muse-agent.json`, `.env` and
  `work/` out of version control (they are already gitignored).
- On Linux, `.muse-agent.json` and work state files are written with POSIX `0600`
  permissions (owner read/write only).
- The CMS stores only the token digest. Revoking the worker in **Muse agents**
  invalidates the token immediately; run `pair` again on that machine.
- The worker redacts `muse_*`, bearer tokens and `sk-or-v1-*` values from its
  logs. It never prints the pairing token.
- Artifact paths are chosen by the CMS. The worker cannot ask the CMS to read a
  path on its machine.

---

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `This Muse agent is not the active worker` | Select the worker in **Muse agents → Active Worker**. |
| `credential was revoked` | Generate a fresh pairing code and run `pair` again. |
| `Muse Mode is enabled but the active Muse agent is offline` | Start the worker; heartbeats must arrive within 75 seconds. |
| `FFprobe was not found` | Run `sudo apt install -y ffmpeg` on Linux, or set `FFMPEG_PATH` and `FFPROBE_PATH`. |
| `Python was not found` | Run `sudo apt install -y python3` on Linux, or set `VIDNETS_PYTHON=python3`. |
| `Vidnets stalls` | Set `MUSE_INFERENCE_COMMAND`/`MUSE_INFERENCE_URL`; the external-agent inbox must be answered. |
| `Vidnets launcher was not found` | Check `vidnets/VIDNETS_AGENT.sh` exists and is executable (`chmod +x vidnets/VIDNETS_AGENT.sh`). |
| `Narration is too short` | Generate the full requested runtime, or lower the title duration in the CMS. |

No provider or OpenRouter key is stored in this repository.
