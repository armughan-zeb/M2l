# Vidzeo Muse worker — Docker distribution

Run the full Vidzeo/M2l worker (Vidnets server + CMS daemon) from a zip, with
no setup from scratch. One image, one container: the daemon spawns Vidnets
itself through `vidnets/VIDNETS_AGENT.sh`, so there is deliberately no
separate Vidnets service.

Full fresh-machine guide: **`docker/SETUP.md`**. Agent behaviour (what the
worker does with the CMS, end to end): **`AGENT_TRAINING.md`** at the repo
root. This README is the technical reference for the image itself.

> **BUILD UNVERIFIED.** Docker is not installed on the reference VM, so this
> image has never been built or run. It was validated by YAML/shell syntax
> checks and a dry-run review of every `COPY`/`RUN` step against the real
> files — not by a build. Build it on a host with Docker before trusting it
> for production. See "What is still not perfect" below.

## Quickstart (another Muse VM)

```bash
unzip M2l-docker-<head>.zip
cd M2l-docker-<head>        # the extracted repo root
./setup.sh                  # prompts: CMS URL + agent key (+ agent name)
docker compose up -d
docker compose logs -f
```

`setup.sh` asks for:

1. **CMS URL** — default `https://cms.vidnets.com`. Create the agent on the
   CMS first (Muse agents page).
2. **Agent key** — paste the static worker token (`muse_...`) or a one-time
   pairing code (`MUSE-XXXX-XXXX`). The key is **never written to `.env` or
   any file by setup.sh**; it is handed to `node muse-agent.js pair`, which
   stores it in `./data/.muse-agent.json` (the data volume). That file is
   the worker's only credential — keep it private and back it up.
3. **Agent name** — default `Muse Worker`.

Then `docker compose up -d` starts the daemon. If the pair output says the
worker is not the Active Worker, select it as Active Worker on the CMS
(Muse agents page) before it can claim jobs.

Useful commands:

```bash
docker compose ps
docker compose logs -f                      # daemon log
docker compose exec worker node muse-agent.js status
docker compose exec worker node muse-agent.js doctor   # bridge + Vidnets health
docker compose down                        # stop (state stays in ./data)
docker compose down -v                     # DANGER: also deletes ./data? No —
                                           # ./data is a bind mount, not a volume,
                                           # so it survives. Delete it by hand only.
```

## What is inside

| Piece | Source of truth |
|---|---|
| Base | Ubuntu 24.04 |
| Node | 24 via NodeSource `node_24.x` (reference VM: v24.22.1) |
| Python | 3.12 (Ubuntu 24.04 default) |
| ffmpeg | apt (see deviation below) |
| yt-dlp, scrapers, TTS | `vidnets/server/requirements.txt` — `yt-dlp[default]>=2026.10.26`, `edge-tts`, `primp`, `ddgs`, `curl-cffi`, `faster-whisper`, `rembg`, `opencv-headless` |
| Remotion | 4.0.541 (`remotion-linux` lockfile; root/vidnets `^4.0.526` resolves to it) |
| Chrome Headless Shell | **157.0.8103.38**, pinned download from Chrome-for-Testing (URL verified HTTP 200 on 2026-11-10). Installed at `/opt/chrome-headless-shell-linux64`, linked to the path `remotion-linux/remotion.config.ts` hardcodes (`/home/hatch/chrome-headless-shell-linux64`) and into `vidnets/node_modules/.remotion/...` where `vidnets/server/app.py` resolves the browser. |
| Vidnets data root | `/data/runtime` (bind-mounted to `./data/runtime`): output, temp, `secure/agent-service.json`, queue. Vidnets temp lives on disk, not in the container's `/tmp`. |
| Worker state | `/data/work` (claimed-job state) and `/data/.muse-agent.json` (credential), bind-mounted to `./data`. |

### Rules that stay true in the image

- **Muse inference compulsory** (`MUSE_INFERENCE_COMPULSORY=1` in the image
  and `.env`): every vision/script/brain call goes through Muse via the
  Vidnets agent-bridge inbox. The entrypoint refuses to start if any
  OpenRouter key (`OPENROUTER_API_KEY`, `VIDZEO_OPENROUTER_API_KEY`,
  `SCRIPTIT_API_KEY`, `SCRIPTIT_OPENROUTER_API_KEY`) is set, and the
  daemon start gate re-checks. **Never add an OpenRouter key.**
- **Filter-best media** (`MUSE_MEDIA_STRICT=0`, `MUSE_YOUTUBE_COVERAGE_TARGET=0.8`):
  Vidnets ranks candidates and keeps the best real media per scene. Web
  pool 12/scene, finalists 3, vision pool 12, relative floor 0.5, YT
  vision frames 2/clip at the transcript moment, YT downloads 4,
  ScriptIT `standard` mode for ≤2-min videos, pool reuse on.
- **Thumbnails are skipped** (`VIDZEO_SKIP_THUMBNAILS=1`): the worker
  delivers script + video only. No thumbnail is generated or uploaded
  (`node muse-agent.js deliver --skip-thumbnail`). If the CMS ever rejects
  a delivery without one, the fallback is a single ffmpeg frame-grab —
  see `AGENT_TRAINING.md` — not a thumbnail pipeline.
- **Upload cap ~100 MB**: masters over ~95 MB are transcoded to fit
  before upload (`lib/ffmpeg.js` `ensureUploadableMaster`: 1080p up to
  7 Mbps, 720p below that), never after an HTTP 413.
- **Never deliver a 0-real-media video**: `render-vidnets-linux.js` fails
  the job (after one bounded re-plan) instead of delivering graphics-only.
  The manifest guard also refuses a master from another job or a 100%
  graphic master.
- **No template scripts**: ScriptIT headless CLI is the only script path
  (`MUSE_SCRIPT_ALLOW_TEMPLATE_FALLBACK` is unset); no invented facts, no
  OpenRouter.

### What setup.sh does (and does not do)

- Checks `docker` + `docker compose` (v2 plugin).
- Writes/updates `.env` (CMS URL + production defaults, Nov 10, 2026
  standing decisions). It does **not** write the agent key anywhere.
- Refuses to re-pair while a job is in flight
  (`data/work/active-job.json` exists); re-running with no job running is
  safe and keeps `./data`.
- Creates `./data/runtime/temp/agent-bridge` and `./data/work`.
- Warns if the host `vidnets-inbox-watch` hook definition is missing
  (points at `docker/hooks/` + `docker/SETUP.md` for the install steps).
- Runs `docker compose build` (first build is slow: Python ML wheels + npm).
- Runs `node muse-agent.js pair` in a one-off container. Re-pairing asks
  first if `./data/.muse-agent.json` already exists.
- It does **not** claim a CMS job, deliver anything, or start the daemon —
  that is `docker compose up -d`.

## Host Muse hook integration — read this

The daemon's start gate requires three things (`lib/museInference.js`):

1. No OpenRouter key — handled in the image.
2. `MUSE_RENDER_COMMAND` pointing at `render-vidnets-linux.js` — handled.
3. The **vidnets-inbox-watch hook** enabled on the **host Muse VM**, and
   the agent-bridge inbox directory writable.

In Docker the inbox lives at `./data/runtime/temp/agent-bridge` on the host
filesystem (bind-mounted into the container). The hook definition is mounted
read-only at `/host-hooks` so the gate can verify it
(`HOST_HOOKS_DIR`, default `$HOME/hooks/definitions`).

**Templates ship in this repo:** `docker/hooks/vidnets-inbox-watch.json.template`
(definition — replace the `<PLACEHOLDERS>`) and
`docker/hooks/vidnets-inbox-watch.sh` (watcher — edit the `BRIDGE` line).
Install steps are in `docker/SETUP.md` ("Host Muse hook").

**Manual step, not yet tested:** the host hook operator answers inbox
requests by running `VIDNETS_AGENT.sh answer <id> <file>`. Against a
containerised worker that command becomes:

```bash
docker exec vidzeo-muse-worker bash /app/vidnets/VIDNETS_AGENT.sh \
  --runtime-root /data/runtime answer <id> <file>
```

and the hook's inbox path must point at
`<extracted-dir>/data/runtime/temp/agent-bridge`, not
`~/workspace/vidnets-runtime/temp/agent-bridge`. Until the host hook is
adapted, the daemon will fail closed at the start gate (by design — a job
with no Muse-answered verdicts is failed, never delivered as graphics).

## Known deviations from the reference VM

- **ffmpeg 6.1.1, not 8.1.2.** Ubuntu 24.04 apt ships 6.1.1. The reference
  VM runs a Hatch-patched 8.1.2 (`7:8.1.2-2ubuntu2+hatch3`) with no public
  apt source, so it cannot be reproduced here. If a job needs 8.x, drop a
  static ffmpeg 8.x build into the image and set `FFMPEG_PATH`/`FFPROBE_PATH`.
- **Node patch version floats** within the 24.x line (NodeSource); the VM
  runs exactly v24.22.1.
- **`/opt/hatch/bin/tts` is not shipped** (host-only Muse binary). Narration
  TTS in the image is `edge-tts` from `requirements.txt`.
- **`start-daemon.sh` is not used** inside the container: it hardcodes
  `/home/hatch/...` paths. `docker/entrypoint.sh` exports the same values
  with container paths (`/app`, `/data`) and then execs the daemon. The
  7-day stale-scratch prune from `start-daemon.sh` does not run in the
  container; prune `./data/runtime/temp` by hand if it grows.
- **`vidnets` npm install uses `--omit=dev`** to skip Electron 45
  (desktop-only). The doctor check needs only the production
  `@remotion/{bundler,renderer}` packages, which are installed.
- **No ports are published** by default. Vidnets binds `127.0.0.1:5010–5110`
  inside the container; the daemon talks to it over container loopback.
  Uncomment the `ports:` block in `docker-compose.yml` to inspect it.

## Troubleshooting

- `docker compose logs -f` first, always.
- `doctor` fails on the hook: the host hook is missing/disabled — see
  "Host Muse hook integration" above. The worker must fail closed here.
- `doctor` fails on Vidnets unreachable: check the Vidnets log at
  `./data/runtime/logs/agent-service.log`.
- `401` / credential revoked: re-run `./setup.sh` and re-pair with a fresh
  key from the CMS (it asks before overwriting the existing pairing, and
  refuses while a job is in flight).
- CMS down (502/530): the daemon retries in-process and keeps polling;
  do not restart the container for a CMS outage.
- Upload HTTP 413: master over ~100 MB. The worker auto-transcodes before
  upload; a 413 means the transcode path was bypassed — check the logs,
  do not re-upload the same file.
- Out of disk: `./data/runtime/temp` accumulates render scratch; the
  container does not prune it (see above). Never delete
  `./data/runtime/temp/agent-bridge` while a job is running.

## Updating

Re-extract a newer `M2l-docker-<head>.zip` over the directory (keep `./data`
and `.env`), then `docker compose build && docker compose up -d`.
Never copy `.env` or `./data/.muse-agent.json` into a chat, ticket, or repo.

## What is still not perfect

1. **The image has never been built.** No Docker on the reference VM.
   First build may surface a real failure (slow/heavy Python requirements,
   `npm ci --omit=dev` in `vidnets/`, Chrome download at build time).
2. **Host hook adaptation is designed, not tested** (see above).
3. **ffmpeg 6.1.1 vs 8.1.2** (see above).
4. `docker compose run` for pairing passes the token via container
   environment; it is not written to disk by setup, but it is visible to
   `docker inspect` for the lifetime of that one-off container.
5. Resource limits (`4 CPU / 7 GB`) copy the reference VM; a smaller host
   will OOM on long renders — lower `MUSE_RENDER_CONCURRENCY` is already 1,
   but the limits in `docker-compose.yml` may need adjusting.
