# Vidzeo Muse Worker (`M2l`)

Remote Muse worker for Vidzeo CoSystems CMS, combining two strictly isolated video production engines:

1. **One-Minute Documentary** ([`one_minute_documentary/SKILL.md`](./one_minute_documentary/SKILL.md))
   - **Media**: `yt-dlp` footage clips (trimmed 1920x1080@30fps) + verified Wikimedia/web still images.
   - **Voiceover**: `/opt/hatch/bin/tts` or `edge-tts`.
   - **Renderer**: Remotion (`one_minute_documentary/templates/remotion-project/`), 1920x1080@30fps H.264 + AAC.
2. **2D Stickman Cartoon Studio** ([`2d-stickman/SKILL.md`](./2d-stickman/SKILL.md))
   - **Visuals**: Procedural HTML5 Canvas 2D vector stickmen, cel-shaded environments, phrase captions.
   - **Audio**: Edge TTS + SFX bed (`tools/mix_audio.py`).
   - **Renderer**: Headless Chrome CDP (`tools/capture.mjs video`).

## One-Shot Setup (fresh Muse account / fresh device)

One command installs everything the daemon needs, in order. Run as root
(the systemd unit runs as `User=root`; there is no `hatch` user on the VM):

```bash
./setup-muse.sh           # full setup: verify, install, enable, start
./setup-muse.sh --check   # dry run: verify only, install/start nothing
```

What it does, in order:

1. **Prerequisites** — node 18.17+, python3, ffmpeg/ffprobe, npm, curl,
   systemctl, executable `start-daemon.sh`, and yt-dlp importable (the repo
   venv python is the only one with it; system python3 lacks it).
2. **Remotion template deps, OFFLINE only** — `npm install --offline` in
   `one_minute_documentary/templates/remotion-project/`, verified by
   `node_modules/@remotion/cli` and `node_modules/.bin/remotion`. If the npm
   cache is empty it exits non-zero with instructions to populate it on a
   registry-reachable machine — it never falls back to a network install.
3. **Repo venv with yt-dlp** — creates `vidnets/.venv` if missing, installs
   yt-dlp into it, verifies `vidnets/.venv/bin/python -c "import yt_dlp"`.
4. **Systemd unit** — installs it from the persistent copy
   `~/workspace/vidnets-runtime/secure/muse-daemon.service` (VM replacements
   wipe `/etc`), `daemon-reload`, `enable`. The version-controlled copy is
   `systemd/muse-daemon.service` in this repo — keep it byte-identical to
   the persistent copy; it carries the `MUSE_INFERENCE_SPOOL_DIR` fix (the
   daemon runs as root, so without the pin it looks for the inference
   spool under `/root/workspace` instead of the real pool dir). The unit runs as `User=root` via
   `start-daemon.sh`, never bare `node muse-agent.js start`.
5. **CMS connectivity** — one cheap `GET https://cms.vidnets.com/api/muse`.
   Warn-only: the CMS is often down (HTTP 530) and the daemon runs jobs
   locally and syncs best-effort anyway.
6. **No old jobs** — refuses to start if `work/active-job.json` exists and
   does not name the job the live daemon is currently working, or if legacy
   server-queue state (`~/workspace/vidnets-runtime/server/queue.json`,
   `queue.json.bak`, `.queue-*.tmp`) is still in the live path. It never
   deletes job state: it aborts and tells the operator how to archive it
   first.
7. **Start** — `systemctl start muse-daemon` (leaves it running if already
   active), then verifies it is active, its pid runs `node muse-agent.js
   start`, and `daemon.log` is being written.

Every step logs what it did; the script exits non-zero on the first hard
failure with the exact reason. Always run `--check` first on a live machine —
the full run starts the daemon.

## Quick Start

```bash
# 1. Install dependencies (Node 18.17+, Python 3, ffmpeg, yt-dlp, edge-tts)
bash setup-linux.sh

# 2. Pair with your Vidzeo CMS
node muse-agent.js pair --cms https://your-cms-host --code MUSE-XXXX-XXXX --name "Muse Worker"

# 3. Verify setup
node muse-agent.js doctor

# 4. Run automated worker loop (with Muse inference bridge) OR agent-driven claim/deliver
node muse-agent.js start
# or agent-driven:
node muse-agent.js claim --wait 60
node muse-agent.js deliver --job <jobId> --skip-thumbnail
```

## Verification

```bash
npm test
```
