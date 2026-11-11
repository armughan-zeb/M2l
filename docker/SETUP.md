# Vidzeo Muse worker — setup (fresh machine)

One image, one container: the CMS daemon spawns the Vidnets server itself.
You need **Docker Engine + the Compose v2 plugin** and nothing else — the
image already has Ubuntu 24.04, Node 24, Python 3.12, ffmpeg, yt-dlp,
Remotion 4.0.541 + Chrome Headless Shell 157, the scrapers, and edge-tts.

## Install

```bash
unzip vidzeo-muse-worker.zip
cd vidzeo-muse-worker            # the extracted repo root
./setup.sh                       # prompts: CMS URL + agent key, then pairs
docker compose up -d
docker compose logs -f
```

`setup.sh` asks for:

1. **CMS URL** — default `https://cms.vidnets.com`. Create the agent on the
   CMS first (Muse agents page).
2. **Agent key** — a static worker token (`muse_...`) or a one-time pairing
   code (`MUSE-XXXX-XXXX`). It is **never written to `.env`**; `pair` stores
   it in `./data/.muse-agent.json`. That file is the worker's only
   credential — keep it private, back it up, never paste it anywhere.
3. **Agent name** — default `Muse Worker`.

Re-running `./setup.sh` is safe: it never wipes `./data`, asks before
re-pairing, and **refuses to re-pair while a job is in flight**
(`data/work/active-job.json` exists — wait for the job to finish first).

If the pair output says this worker is not the Active Worker, select it as
Active Worker on the CMS (Muse agents page) before it can claim jobs.
Only one Active Worker can claim — that is a CMS limit, not a bug.

## Verify

```bash
docker compose ps
docker compose exec worker node muse-agent.js doctor   # bridge + Vidnets health — must pass before any job runs
docker compose exec worker node muse-agent.js status   # connected true, activeWorker true
docker compose exec worker curl -s http://127.0.0.1:5010/   # Vidnets answers inside the container
```

No ports are published to the host by default. To inspect Vidnets from the
host, uncomment the `ports:` block in `docker-compose.yml` and re-up.

## Host Muse hook (manual step — do this before the first job)

The daemon's start gate fails closed without it: the **vidnets-inbox-watch
hook** on the **host Muse VM** is the only thing that answers Vidnets
inference requests (vision, scene queries, ScriptIT drafts). The container
cannot answer them itself.

1. Copy `docker/hooks/vidnets-inbox-watch.json.template` to
   `$HOME/hooks/definitions/vidnets-inbox-watch.json` on the host Muse VM
   and replace the placeholders:
   - `<EXTRACTED_DIR>` — the dir you unzipped this repo into.
   - `<DATA_RUNTIME_DIR>` — `<EXTRACTED_DIR>/data/runtime`.
   - `<YOUR_SIDE_CHAT_ID>` — the chat the hook should answer in.
2. Copy `docker/hooks/vidnets-inbox-watch.sh` to
   `$HOME/hooks/scripts/vidnets-inbox-watch.sh`, edit the `BRIDGE` line to
   `<EXTRACTED_DIR>/data/runtime/temp/agent-bridge`, `chmod +x` it.
3. Enable the hook on the host Muse VM. `HOST_HOOKS_DIR` (default
   `$HOME/hooks/definitions`) is mounted read-only at `/host-hooks` so the
   gate can verify it.

**Expect ~4.5 min per inference round trip** (hook wake latency). That is
normal, not a failure. A 1-min video needs dozens of round trips; the
realistic floor is ~30–45 min, not 2–5 min.

## What this worker does (and does not do)

- **Script + video only. Thumbnails are skipped** (`VIDZEO_SKIP_THUMBNAILS=1`).
  No thumbnail is generated or uploaded. If the CMS ever rejects a delivery
  without one, the fallback is a single frame-grab — see `AGENT_TRAINING.md`.
- **Meta LLM only, no API keys.** Every vision/script/brain call goes through
  Muse via the agent-bridge inbox. Never add an OpenRouter, Pexels, or
  Pixabay key — the entrypoint refuses to start with an OpenRouter key set.
- **No AI footage.** Scraped YouTube clips + web images + stock, Remotion
  motion graphics. Never ship a graphics-only video.
- **One video at a time.** Same job ID = same job: a finished master is
  delivered, mid-run work is resumed, never restarted from scratch.
- **Upload cap ~100 MB.** Masters over ~95 MB are transcoded to fit before
  upload (1080p up to 7 Mbps, 720p below that). Never upload an oversize
  file and eat a 413.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `doctor` fails on the hook | Hook missing/disabled on the host — see "Host Muse hook" above. Worker must fail closed here. |
| `doctor` fails on Vidnets unreachable | Check `./data/runtime/logs/agent-service.log`. |
| CMS 502 / 530 / error 1033 | His PC's Cloudflare Tunnel is down. **Do nothing** — the daemon retries in-process, work is never lost. Restart the tunnel on his PC. |
| Upload HTTP 413 | Master over ~100 MB. The worker auto-transcodes before upload; a 413 means the transcode path was bypassed — check the logs, do not re-upload the same file. |
| `401` / credential revoked | Re-run `./setup.sh` and re-pair with a fresh key from the CMS. |
| Container stopped / VM rebooted | `docker compose up -d`. State lives in `./data`; the daemon resumes the saved job from its checkpoint. Never delete `./data` while a job runs. |
| Out of disk | `./data/runtime/temp` accumulates render scratch; prune it by hand. Never delete `./data/runtime/temp/agent-bridge` while a job runs. |
| Slow job / "stuck" scene | Normal: inference round trips are ~4.5 min each. The server's debugger reuses already-downloaded vision-checked media first, then same-job approved media, graphic last. Do not kill it. |
| Low-memory test/build failures | The reference box has 4 vCPU / 7.7 GB and render concurrency is pinned to 1. Do not raise it. |

## Updating

Re-extract a newer `vidzeo-muse-worker.zip` over the directory (keep `./data`
and `.env`), then `docker compose build && docker compose up -d`.

## What is still not perfect

1. **The image has never been built.** No Docker on the reference VM
   (Nov 10, 2026). First build may surface a real failure (slow/heavy
   Python requirements, `npm ci --omit=dev` in `vidnets/`, Chrome download
   at build time). Build it on a Docker host before trusting it.
2. **Host hook adaptation is designed, not tested** (see above).
3. **ffmpeg 6.1.1 vs 8.1.2.** Ubuntu 24.04 apt ships 6.1.1; the reference
   VM runs a Hatch-patched 8.1.2 with no public apt source. If a job needs
   8.x behaviour, drop a static ffmpeg 8.x build into the image and set
   `FFMPEG_PATH`/`FFPROBE_PATH`.
4. `/opt/hatch/bin/tts` is not shipped (host-only Muse binary). Narration
   TTS in the image is `edge-tts`.
5. `docker compose run` for pairing passes the token via container
   environment; it is not written to disk by setup, but it is visible to
   `docker inspect` for the lifetime of that one-off container.
