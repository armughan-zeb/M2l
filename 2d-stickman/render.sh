#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

OUT=$(python3 -c "import json; print(json.load(open('script.json', encoding='utf-8')).get('output', 'out'))")

echo "[1/5] Synthesizing Edge TTS voiceover..."
python3 tools/voice_edge.py

echo "[2/5] Planning phrase captions..."
node tools/plan_captions.mjs

echo "[3/5] Running QA check & lint..."
node tools/capture.mjs check
node tools/capture.mjs lint

echo "[4/5] Mixing narration + SFX + ambience bed..."
node tools/capture.mjs cues
python3 tools/mix_audio.py

echo "[5/5] Rendering renders/${OUT}.mp4..."
node tools/capture.mjs video "renders/${OUT}.mp4" "$@"
echo "DONE -> renders/${OUT}.mp4"
