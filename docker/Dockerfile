# Vidzeo Muse worker — full-repo image (Vidnets server + CMS daemon).
#
# One image, one container: the CMS daemon (muse-agent.js) spawns the Vidnets
# server itself through vidnets/VIDNETS_AGENT.sh (headless_agent.py picks a
# loopback port in 5010-5110 and records it in <runtime>/secure/agent-service.json),
# so splitting daemon and Vidnets into two containers would break the launcher
# handshake. The container therefore runs both, supervised only by
# `docker compose ... restart: unless-stopped` (no extra supervisor: the daemon
# survives CMS 502/530 outages in-process, lib/cmsRetry.js).
#
# Versions pinned to what actually runs on the reference VM (Nov 10, 2026):
#   Ubuntu 24.04, Node 24 (NodeSource node_24.x; VM: v24.22.1),
#   Python 3.12 (Ubuntu 24.04 default), Remotion 4.0.541
#   (remotion-linux lockfile; root/vidnets ranges ^4.0.526 resolve to it),
#   Chrome Headless Shell 157.0.8103.38 (verified: the download URL below
#   returned HTTP 200 on 2026-11-10),
#   yt-dlp[default] >= 2026.10.26 (vidnets/server/requirements.txt, py >= 3.11).
# KNOWN DEVIATION — ffmpeg: Ubuntu 24.04 apt ships ffmpeg 6.1.1. The reference
# VM runs a Hatch-patched 8.1.2 (7:8.1.2-2ubuntu2+hatch3) with no public apt
# source, so this image uses stock 6.1.1. If a job needs 8.x behaviour, drop a
# static ffmpeg 8.x build into the image and set FFMPEG_PATH/FFPROBE_PATH.
# KNOWN DEVIATION — /opt/hatch/bin/tts is a host-only Muse binary and is NOT
# shipped; narration TTS in the image is edge-tts from requirements.txt.
#
# BUILD IS UNVERIFIED (Nov 10, 2026): docker is not installed on the
# reference VM, so this Dockerfile has never been built. It was checked by
# syntax review + a dry-run of every COPY source against `git ls-files`
# only. Build it on a host with Docker before trusting it for production.
# See docker/README.md + docker/SETUP.md.
#
# Defaults in this image are the Nov 10, 2026 standing decisions — do not
# "fix" them back: YT coverage target 0.30 (web is the main source),
# web pool 12/scene, finalists 3, vision pool 12, relative floor 0.5,
# YT vision frames 2/clip, YT downloads 4, ScriptIT standard mode for
# <=2min videos (the CLI default), pool reuse on, thumbnails SKIPPED.

FROM ubuntu:24.04

ARG CHROME_HEADLESS_SHELL_VERSION=157.0.8103.38
ARG CHROME_HEADLESS_SHELL_URL=https://storage.googleapis.com/chrome-for-testing-public/${CHROME_HEADLESS_SHELL_VERSION}/linux64/chrome-headless-shell-linux64.zip

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # --- Muse inference is COMPULSORY. Never add an OpenRouter key here. ---
    MUSE_INFERENCE_COMPULSORY=1 \
    MUSE_AUTO_LOOP=1 \
    MUSE_RENDER_COMMAND="node /app/render-vidnets-linux.js" \
    MUSE_VIDNETS_ROOT=/app/vidnets \
    MUSE_REPO_ROOT=/app \
    MUSE_RENDER_CONCURRENCY=1 \
    # Nov 10, 2026 standing decisions (see AGENT_TRAINING.md). YT coverage
    # 0.30: web images are the main source; the old 0.8 was per-job values.
    MUSE_YOUTUBE_COVERAGE_TARGET=0.30 \
    MUSE_MEDIA_STRICT=0 \
    VIDZEO_VISUAL_TOP_CANDIDATES_PER_SCENE=12 \
    VIDZEO_VISUAL_FINALIST_LIMIT=3 \
    VIDZEO_VISUAL_GATE_WORKERS=12 \
    VIDZEO_VISUAL_RELATIVE_FLOOR=0.5 \
    VIDZEO_YT_VISUAL_GATE_FRAMES=2 \
    VIDZEO_MIXED_YT_DOWNLOAD_CONCURRENCY=4 \
    VIDZEO_YT_DOWNLOAD_CONCURRENCY=4 \
    # Thumbnails are SKIPPED: this worker delivers script + video only.
    VIDZEO_SKIP_THUMBNAILS=1 \
    # TLS: the image's own CA bundle (never a host path — none exists
    # inside the image). Node + Python both trust it.
    NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    VIDZEO_RUNTIME_ROOT=/data/runtime \
    VIDZEO_TEMP_DIR=/data/runtime/temp \
    TMPDIR=/data/runtime/temp \
    MUSE_WORK_DIR=/data/work \
    MUSE_CONFIG_FILE=/data/.muse-agent.json \
    VIDZEO_BRIDGE_ROOT=/data/runtime/temp/agent-bridge \
    MUSE_INBOX_HOOK_DEFINITION=/host-hooks/vidnets-inbox-watch.json \
    VIDNETS_PYTHON=python3

# --- System packages: ffmpeg, Python 3.12, Chrome/Remotion shared libs ------
# Chrome lib set mirrors setup-linux.sh. No chromium-browser package: the
# render path uses the pinned Chrome Headless Shell download below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git gnupg unzip \
        ffmpeg \
        python3 python3-pip python3-dev build-essential pkg-config \
        fonts-liberation fonts-dejavu-core \
        libasound2 libatk-bridge2.0-0 libatk1.0-0 libc6 libcairo2 libcups2 \
        libdbus-1-3 libexpat1 libfontconfig1 libgbm1 libgcc1 libglib2.0-0 \
        libgtk-3-0 libnspr4 libnss3 libpango-1.0-0 libpangocairo-1.0-0 \
        libstdc++6 libx11-6 libx11-xcb1 libxcb1 libxcomposite1 libxcursor1 \
        libxdamage1 libxext6 libxfixes3 libxi6 libxrandr2 libxrender1 \
        libxss1 libxtst6 lsb-release wget xdg-utils procps \
    && curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && node --version && python3 --version && ffmpeg -version | head -1 \
    && rm -rf /var/lib/apt/lists/*

# --- Chrome Headless Shell (pinned; URL verified HTTP 200 on 2026-11-10) ----
# Installed at /opt/chrome-headless-shell-linux64 and linked to the path
# remotion-linux/remotion.config.ts hardcodes
# (/home/hatch/chrome-headless-shell-linux64/chrome-headless-shell), plus
# into vidnets/node_modules/.remotion/... where vidnets/server/app.py looks
# for the browser binary (linked after `npm ci` below).
RUN curl -fsSL -o /tmp/chrome-headless-shell-linux64.zip "${CHROME_HEADLESS_SHELL_URL}" \
    && unzip -q /tmp/chrome-headless-shell-linux64.zip -d /opt \
    && rm /tmp/chrome-headless-shell-linux64.zip \
    && chmod +x /opt/chrome-headless-shell-linux64/chrome-headless-shell \
    && mkdir -p /home/hatch \
    && ln -sfn /opt/chrome-headless-shell-linux64 /home/hatch/chrome-headless-shell-linux64 \
    && /opt/chrome-headless-shell-linux64/chrome-headless-shell --version

WORKDIR /app

# --- Node dependencies (lockfiles first, for layer caching) ------------------
COPY package.json package-lock.json ./
COPY remotion-linux/package.json remotion-linux/package-lock.json ./remotion-linux/
COPY vidnets/package.json vidnets/package-lock.json ./vidnets/
COPY scriptit/package.json scriptit/package-lock.json ./scriptit/
# Root + remotion-linux: Remotion 4.0.541 render engine.
# vidnets --omit=dev: skips Electron 45 (desktop-only devDependency);
#   the doctor check needs vidnets/node_modules/@remotion/{bundler,renderer},
#   which are production dependencies and are installed.
# scriptit --omit=dev: skips Electron/electron-builder; the headless CLI
#   (scriptit/cli/generate.js) needs only the production dependencies.
RUN npm ci --no-audit --no-fund \
    && (cd remotion-linux && npm ci --no-audit --no-fund) \
    && (cd vidnets && npm ci --omit=dev --no-audit --no-fund) \
    && (cd scriptit && npm ci --omit=dev --no-audit --no-fund)

# --- Python dependencies (Vidnets server: Flask, yt-dlp, scrapers, TTS) -----
# vidnets/server/requirements.txt pins yt-dlp[default]>=2026.10.26 (py>=3.11)
# plus edge-tts (narration), faster-whisper, rembg, opencv-headless,
# rapidocr, gallery-dl. This is the slowest layer (large ML wheels).
COPY vidnets/server/requirements.txt ./vidnets/server/requirements.txt
RUN python3 -m pip install --no-cache-dir --break-system-packages \
        -r vidnets/server/requirements.txt \
    && python3 -m yt_dlp --version \
    # certifi fix (same as the reference VM's venv): point certifi's bundle
    # at the system CA bundle, so Python HTTPS (scrapers, yt-dlp) trusts
    # the same roots as the rest of the image. No-op if already a link.
    && python3 -c "import certifi, os; p=certifi.where(); [os.remove(p), os.symlink('/etc/ssl/certs/ca-certificates.crt', p)] if os.path.exists(p) and not os.path.islink(p) else None" \
    && python3 -c "import certifi; print('certifi bundle:', certifi.where())"

# --- Repo --------------------------------------------------------------------
COPY . .
# Browser binary where vidnets/server/app.py resolves it:
# <resource_root>/node_modules/.remotion/chrome-headless-shell/linux64/
#   chrome-headless-shell-linux64/chrome-headless-shell
RUN mkdir -p vidnets/node_modules/.remotion/chrome-headless-shell/linux64 \
    && ln -sfn /opt/chrome-headless-shell-linux64 \
        vidnets/node_modules/.remotion/chrome-headless-shell/linux64/chrome-headless-shell-linux64 \
    && chmod +x docker/entrypoint.sh vidnets/VIDNETS_AGENT.sh \
    && mkdir -p /data/runtime/temp/agent-bridge /data/work \
    && useradd --uid 10001 --create-home --home-dir /home/vidzeo vidzeo \
    && chown -R 10001:10001 /app /data /home/hatch/chrome-headless-shell-linux64 /opt/chrome-headless-shell-linux64

USER 10001:10001

# Vidnets binds 127.0.0.1:5010-5110 INSIDE the container (picked by
# headless_agent.py); nothing is published to the host by default.
EXPOSE 5010

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["start"]
