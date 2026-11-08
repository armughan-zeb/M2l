#!/usr/bin/env bash
# ==============================================================================
# Setup script for Vidzeo Muse Worker on Linux (Ubuntu / Debian / Boost)
# ==============================================================================
set -e

echo "=== Vidzeo Muse Worker Linux Setup ==="
echo ""

# Detect privilege level: do not use sudo if already root or in container without sudo
SUDO=""
if [ "$(id -u 2>/dev/null || echo 1)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    else
        echo "[WARN] Running as non-root user and 'sudo' is not installed."
    fi
fi

# 1. OS & Distribution check
if [ -f /etc/os-release ]; then
    . /etc/os-release
    echo "[Info] Detected OS: ${NAME:-Linux} (${VERSION:-unknown})"
fi

# 2. Check Node.js
if ! command -v node >/dev/null 2>&1; then
    for candidate_dir in /usr/local/bin /usr/bin /snap/bin "$HOME/.nvm/versions/node/$(ls -1 "$HOME/.nvm/versions/node" 2>/dev/null | tail -n 1)/bin"; do
        if [ -x "$candidate_dir/node" ]; then
            export PATH="$candidate_dir:$PATH"
            break
        fi
    done
fi

if command -v node >/dev/null 2>&1; then
    NODE_VERSION=$(node -v)
    echo "[PASS] Node.js installed: $NODE_VERSION"
else
    echo "[FAIL] Node.js not found. Please install Node.js 18.17+:"
    echo "       curl -fsSL https://deb.nodesource.com/setup_20.x | $SUDO bash -"
    echo "       $SUDO apt-get install -y nodejs"
    exit 1
fi

# 3. Check FFmpeg, FFprobe, and procps (pkill/ps)
PKGS_TO_INSTALL=()
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
    PKGS_TO_INSTALL+=("ffmpeg")
fi
if ! command -v pkill >/dev/null 2>&1; then
    PKGS_TO_INSTALL+=("procps")
fi

if [ ${#PKGS_TO_INSTALL[@]} -eq 0 ]; then
    echo "[PASS] FFmpeg, FFprobe, and procps installed"
else
    echo "[WARN] Missing packages: ${PKGS_TO_INSTALL[*]}. Installing via apt if available:"
    if command -v apt-get >/dev/null 2>&1; then
        echo "       Running: $SUDO apt-get update && $SUDO apt-get install -y ${PKGS_TO_INSTALL[*]}"
        $SUDO apt-get update && $SUDO apt-get install -y "${PKGS_TO_INSTALL[@]}" || true
    else
        echo "       Please install ${PKGS_TO_INSTALL[*]} package(s) on your distribution."
    fi
fi

# 4. Install Chromium libraries for Remotion Headless Rendering
echo "[Info] Ensuring Chrome / Chromium sandbox libraries for Remotion..."
if command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update -y && $SUDO apt-get install -y \
        ca-certificates fonts-liberation libasound2 libatk-bridge2.0-0 libatk1.0-0 \
        libc6 libcairo2 libcups2 libdbus-1-3 libexpat1 libfontconfig1 libgbm1 \
        libgcc1 libglib2.0-0 libgtk-3-0 libnspr4 libnss3 libpango-1.0-0 \
        libpangocairo-1.0-0 libstdc++6 libx11-6 libx11-xcb1 libxcb1 libxcomposite1 \
        libxcursor1 libxdamage1 libxext6 libxfixes3 libxi6 libxrandr2 libxrender1 \
        libxss1 libxtst6 lsb-release wget xdg-utils chromium-browser 2>/dev/null || true
fi

# 5. Check Python 3
if command -v python3 >/dev/null 2>&1; then
    echo "[PASS] Python 3 installed: $(python3 --version 2>&1)"
elif [ -x /opt/conda/bin/python3 ]; then
    echo "[PASS] Python 3 installed (Conda): $(/opt/conda/bin/python3 --version 2>&1)"
    export PATH="/opt/conda/bin:$PATH"
else
    echo "[WARN] Python 3 not found. Installing via apt if available:"
    if command -v apt-get >/dev/null 2>&1; then
        $SUDO apt-get update && $SUDO apt-get install -y python3 python3-pip || true
    fi
fi

# 5. Make all scripts and CLI executable
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
chmod +x "$DIR/muse-agent.js" 2>/dev/null || true
find "$DIR" -type f -name "*.sh" -exec chmod +x {} + 2>/dev/null || true
if [ -f "$DIR/scriptit/scripts/yt_transcript.py" ]; then
    chmod +x "$DIR/scriptit/scripts/yt_transcript.py" 2>/dev/null || true
fi
echo "[PASS] Set executable permissions on all shell scripts and binaries"

# 6. Run doctor
echo ""
echo "=== Running Muse Doctor ==="
node "$DIR/muse-agent.js" doctor || true

echo ""
echo "Setup complete! To start the worker, run: ./start.sh"
