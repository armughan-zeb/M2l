# Vidzeo Muse Worker

An authenticated worker for the Vidzeo CMS **Muse mode**. It pairs with the CMS,
claims one production job at a time, produces narration plus a 1080p master and
thumbnail, and uploads the artifacts. The CMS validates every artifact again
before packaging and publishing.

The worker is fully Linux/Ubuntu native (also compatible with Boost cloud, VPS,
containers, and Windows). It never uses an OpenRouter key or any provider
fallback for agent jobs. Reasoning, writing and vision belong to the Muse
environment; this repository only transports jobs, runs the operator's commands,
and performs objective media checks.

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
checks that its script and render/inference adapters are configured. This keeps
an incomplete worker from taking CMS jobs and immediately failing them.

The automated loop requires `MUSE_SCRIPT_COMMAND` plus either
`MUSE_RENDER_COMMAND`, or the built-in Vidnets path with
`MUSE_INFERENCE_COMMAND` / `MUSE_INFERENCE_URL`. If the Muse agent itself writes
the narration and renders in its own environment, use the agent-driven
`claim` → write/render into the returned work folder → `deliver` flow instead
of `start`.

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
work/<jobId>/thumbnail.jpg   optional 16:9 thumbnail
work/<jobId>/inference/      Vidnets reasoning/vision requests (built-in renderer)
```

After the files exist:

```bash
node muse-agent.js deliver --job <jobId>
```

`deliver` validates the narration length, the master duration/1080p/audio/full
decode, and the thumbnail size/aspect, then uploads and completes the job.
Pass `--script`, `--video` or `--thumbnail` to deliver files produced elsewhere;
they are copied into the job checkpoint. If no thumbnail is supplied, a real
frame is extracted from the finished master.

If the work cannot be finished, report an actionable reason:

```bash
node muse-agent.js fail --job <jobId> --error "Vidnets could not download the source clip: HTTP 403"
```

### 2. Automated loop

Use this when the Muse environment exposes commands. Configure the adapters in
`.env` (copy `.env.example`), then run `start` or `./start.sh`.

| Adapter | Variable | Contract |
| --- | --- | --- |
| Narration | `MUSE_SCRIPT_COMMAND` | Job JSON on stdin; print narration text or `{"scriptText":"..."}` |
| Renderer | `MUSE_RENDER_COMMAND` | JSON on stdin (`job`, `scriptText`, `scriptPath`, `workDir`, `outputPath`); print `{"videoPath":"...","thumbnailPath":"..."}` |
| Thumbnail | `MUSE_THUMBNAIL_COMMAND` | JSON on stdin (`job`, `scriptText`, `videoPath`, `workDir`); print `{"thumbnailPath":"..."}` |
| Vidnets inference | `MUSE_INFERENCE_COMMAND` or `MUSE_INFERENCE_URL` | One Vidnets request JSON on stdin (or POSTed); return the exact text/JSON answer |

Without a render command, the worker uses **Vidnets**:
- Vidnets is started through `VIDNETS_AGENT.sh` (Linux) or `VIDNETS_AGENT.cmd` (Windows) and runs with
  `llm_provider: external_agent`; it never receives an OpenRouter key.
- While the render runs, the worker polls Vidnets' local inference inbox and
  answers every writing, director, query and vision request through
  `MUSE_INFERENCE_COMMAND` or `MUSE_INFERENCE_URL`.
- Set `MUSE_VIDNETS_ROOT` if Vidnets lives outside `./vidnets`.
- Built-in Vidnets needs `PEXELS_API_KEY` and `PIXABAY_API_KEY` for stock rescue.

Without `MUSE_INFERENCE_COMMAND` or `MUSE_INFERENCE_URL`, external-agent Vidnets
jobs cannot answer their scene requests; `doctor` reports this as blocking.

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

1. Copy `vidzeo-muse.service` to `/etc/systemd/system/`:

```bash
sudo cp vidzeo-muse.service /etc/systemd/system/vidzeo-muse.service
```

2. Edit the service file to set your user and installation directory:

```bash
sudo nano /etc/systemd/system/vidzeo-muse.service
```

3. Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable vidzeo-muse
sudo systemctl start vidzeo-muse
sudo systemctl status vidzeo-muse
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
