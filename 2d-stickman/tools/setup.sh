#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[setup] Checking Node.js, Python 3, ffmpeg, and Chrome..."
command -v node >/dev/null || { echo "Missing Node.js (22+ required)"; exit 1; }
command -v python3 >/dev/null || { echo "Missing python3"; exit 1; }
command -v ffmpeg >/dev/null || { echo "Missing ffmpeg"; exit 1; }

python3 -m pip install --upgrade pip >/dev/null 2>&1 || true
python3 -m pip install edge-tts numpy soundfile

if [ ! -f sounds/whoosh.wav ]; then
  echo "[setup] Synthesizing sound effects and ambient beds..."
  python3 tools/make_sounds.py
fi

echo "[setup] Done! Run: bash render.sh"
