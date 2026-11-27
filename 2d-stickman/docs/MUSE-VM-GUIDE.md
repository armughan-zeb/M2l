# Running on Low-CPU VMs (e.g. Muse VM with 1.5 vCPU & No GPU)

This studio runs **100% on CPU** (no GPU required) using Headless Chrome + FFmpeg `libx264`.

## Minimum Requirements
- **CPU**: 1.5+ vCPU (works cleanly, renders at ~1.5–3 fps on 1080p)
- **RAM**: **2 GB+** recommended (Chrome + FFmpeg + Canvas buffers)
- **Software**: Node.js 22+, Python 3.9+ (`edge-tts`, `numpy`, `soundfile`), `ffmpeg`, Google Chrome or Chromium.

## Recommended Flags on Muse / Cloud VMs
1. Force software WebGL/2D rasterizer cleanly if headless Chrome prints GPU warnings:
   ```sh
   export SOFTWARE_GL=1
   ```
2. Use `--draft` for fast layout & timing checks (~2x–3x faster encoding):
   ```sh
   node tools/capture.mjs video renders/preview.mp4 --draft
   ```
3. Or render single review frames in ~2 seconds before committing to a full MP4 encode:
   ```sh
   node tools/capture.mjs frames review/check "1.5,5.0,9.5,14.5"
   ```
4. If encoding speed matters on 1.5 vCPUs, set `FPS=24` (`FPS=24 node tools/capture.mjs video renders/out.mp4`) or run `bash render.sh` inside `tmux`/`nohup`.
