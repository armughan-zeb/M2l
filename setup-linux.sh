#!/usr/bin/env bash
# ==============================================================================
# Setup script for Vidzeo Muse Worker on Linux (Ubuntu / Debian / Boost)
# ==============================================================================
set -e

echo "=== Vidzeo Muse Worker Linux Setup ==="
echo ""

# 1. OS & Distribution check
if [ -f /etc/os-release ]; then
    . /etc/os-release
    echo "[Info] Detected OS: $NAME ($VERSION)"
fi

# 2. Check Node.js
if command -v node >/dev/null 2>&1; then
    NODE_VERSION=$(node -v)
    echo "[PASS] Node.js installed: $NODE_VERSION"
else
    echo "[FAIL] Node.js not found. Please install Node.js 18.17+:"
    echo "       curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -"
    echo "       sudo apt install -y nodejs"
    exit 1
fi

# 3. Check FFmpeg and FFprobe
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
    echo "[PASS] FFmpeg and FFprobe installed"
else
    echo "[WARN] FFmpeg or FFprobe not found. Installing via apt if available:"
    if command -v apt-get >/dev/null 2>&1; then
        echo "       Running: sudo apt-get update && sudo apt-get install -y ffmpeg"
        sudo apt-get update && sudo apt-get install -y ffmpeg || true
    else
        echo "       Please install ffmpeg package on your distribution."
    fi
fi

# 4. Check Python 3
if command -v python3 >/dev/null 2>&1; then
    echo "[PASS] Python 3 installed: $(python3 --version 2>&1)"
else
    echo "[WARN] Python 3 not found. Installing via apt if available:"
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update && sudo apt-get install -y python3 python3-pip || true
    fi
fi

# 5. Make all scripts and CLI executable
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
chmod +x "$DIR/muse-agent.js" 2>/dev/null || true
chmod +x "$DIR/start.sh" 2>/dev/null || true
chmod +x "$DIR/setup-linux.sh" 2>/dev/null || true
if [ -f "$DIR/vidnets/VIDNETS_AGENT.sh" ]; then
    chmod +x "$DIR/vidnets/VIDNETS_AGENT.sh" 2>/dev/null || true
fi
if [ -f "$DIR/scriptit/start.sh" ]; then
    chmod +x "$DIR/scriptit/start.sh" 2>/dev/null || true
fi
echo "[PASS] Set executable permissions on all shell scripts and binaries"

# 6. Run doctor
echo ""
echo "=== Running Muse Doctor ==="
node "$DIR/muse-agent.js" doctor || true

echo ""
echo "Setup complete! To start the worker, run: ./start.sh"
