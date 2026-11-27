FROM ubuntu:24.04

ARG CHROME_HEADLESS_SHELL_VERSION=158.0.8127.2
ARG CHROME_HEADLESS_SHELL_URL=https://storage.googleapis.com/chrome-for-testing-public/${CHROME_HEADLESS_SHELL_VERSION}/linux64/chrome-headless-shell-linux64.zip

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MUSE_INFERENCE_COMPULSORY=1 \
    MUSE_AUTO_LOOP=1 \
    MUSE_REPO_ROOT=/app \
    VIDZEO_SKIP_THUMBNAILS=1 \
    NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    VIDZEO_RUNTIME_ROOT=/data/runtime \
    VIDZEO_TEMP_DIR=/data/runtime/temp \
    TMPDIR=/data/runtime/temp \
    MUSE_WORK_DIR=/data/work \
    MUSE_CONFIG_FILE=/data/.muse-agent.json \
    VIDZEO_BRIDGE_ROOT=/data/runtime/temp/agent-bridge \
    MUSE_INBOX_HOOK_DEFINITION=/host-hooks/vidnets-inbox-watch.json \
    MUSE_INBOX_HOOK_STATE_DIR=/host-hook-state \
    REMOTION_BROWSER_EXECUTABLE=/opt/chrome-headless-shell-linux64/chrome-headless-shell

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git gnupg unzip jq \
        ffmpeg \
        python3 python3-pip \
        fonts-liberation fonts-dejavu-core \
        libasound2t64 libatk-bridge2.0-0 libatk1.0-0 libc6 libcairo2 libcups2 \
        libdbus-1-3 libexpat1 libfontconfig1 libgbm1 libgcc-s1 libglib2.0-0 \
        libgtk-3-0 libnspr4 libnss3 libpango-1.0-0 libpangocairo-1.0-0 \
        libstdc++6 libx11-6 libx11-xcb1 libxcb1 libxcomposite1 libxcursor1 \
        libxdamage1 libxext6 libxfixes3 libxi6 libxrandr2 libxrender1 \
        libxss1 libxtst6 lsb-release wget xdg-utils procps \
    && curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /tmp/chrome-headless-shell-linux64.zip "${CHROME_HEADLESS_SHELL_URL}" \
    && unzip -q /tmp/chrome-headless-shell-linux64.zip -d /opt \
    && rm /tmp/chrome-headless-shell-linux64.zip \
    && chmod +x /opt/chrome-headless-shell-linux64/chrome-headless-shell

RUN python3 -m pip install --no-cache-dir --break-system-packages yt-dlp edge-tts curl_cffi

WORKDIR /app

COPY package.json ./
COPY one_minute_documentary/templates/remotion-project/package.json ./one_minute_documentary/templates/remotion-project/
RUN npm install --no-audit --no-fund \
    && (cd one_minute_documentary/templates/remotion-project && npm install --no-audit --no-fund)

COPY . .

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["start"]
