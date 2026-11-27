#!/usr/bin/env bash
set -e

echo "=== Vidzeo Muse Worker Linux Setup ==="
echo ""

SUDO=""
if [ "$(id -u 2>/dev/null || echo 1)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    else
        echo "[WARN] Running as non-root user and 'sudo' is not installed."
    fi
fi

if [ -f /etc/os-release ]; then
    . /etc/os-release
    echo "[Info] Detected OS: ${NAME:-Linux} (${VERSION:-unknown})"
fi

if ! command -v node >/dev/null 2>&1; then
    for candidate_dir in /usr/local/bin /usr/bin /snap/bin "$HOME/.nvm/versions/node/$(ls -1 "$HOME/.nvm/versions/node" 2>/dev/null | tail -n 1)/bin"; do
        if [ -x "$candidate_dir/node" ]; then
            export PATH="$candidate_dir:$PATH"
            break
        fi
    done
fi

if command -v node >/dev/null 2>&1; then
    echo "[PASS] Node.js installed: $(node -v)"
else
    echo "[FAIL] Node.js 18.17+ is required."
    exit 1
fi

PKGS_TO_INSTALL=()
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
    PKGS_TO_INSTALL+=("ffmpeg")
fi
if ! command -v pkill >/dev/null 2>&1; then
    PKGS_TO_INSTALL+=("procps")
fi
if ! command -v flock >/dev/null 2>&1; then
    PKGS_TO_INSTALL+=("util-linux")
fi
if ! command -v jq >/dev/null 2>&1; then
    PKGS_TO_INSTALL+=("jq")
fi

if [ ${#PKGS_TO_INSTALL[@]} -gt 0 ] && command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update && $SUDO apt-get install -y "${PKGS_TO_INSTALL[@]}" || true
fi

if command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update -y && $SUDO apt-get install -y \
        ca-certificates fonts-liberation libasound2 libatk-bridge2.0-0 libatk1.0-0 \
        libc6 libcairo2 libcups2 libdbus-1-3 libexpat1 libfontconfig1 libgbm1 \
        libgcc1 libglib2.0-0 libgtk-3-0 libnspr4 libnss3 libpango-1.0-0 \
        libpangocairo-1.0-0 libstdc++6 libx11-6 libx11-xcb1 libxcb1 libxcomposite1 \
        libxcursor1 libxdamage1 libxext6 libxfixes3 libxi6 libxrandr2 libxrender1 \
        libxss1 libxtst6 lsb-release wget xdg-utils 2>/dev/null || true
fi

if ! command -v python3 >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update && $SUDO apt-get install -y python3 python3-pip || true
fi

if command -v python3 >/dev/null 2>&1; then
    python3 -m pip install --no-cache-dir --break-system-packages yt-dlp edge-tts curl_cffi 2>/dev/null || true
fi

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
chmod +x "$DIR/muse-agent.js" 2>/dev/null || true
find "$DIR" -type f -name "*.sh" -exec chmod +x {} + 2>/dev/null || true

(cd "$DIR" && npm install --no-audit --no-fund)
(cd "$DIR/one_minute_documentary/templates/remotion-project" && npm install --no-audit --no-fund)

if [ -f "$DIR/2d-stickman/render.sh" ]; then
    chmod +x "$DIR/2d-stickman/render.sh" 2>/dev/null || true
fi

bash "$DIR/scripts/install-inbox-hook.sh" || true

echo ""
echo "=== Running Muse Doctor ==="
node "$DIR/muse-agent.js" doctor || echo "[WARN] Setup installed dependencies, but doctor reported checks that still need attention."
echo "Setup complete! To start the worker, run: ./start.sh"
