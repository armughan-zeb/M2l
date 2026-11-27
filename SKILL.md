---
name: vidzeo-muse-worker
description: Unified Muse video production worker supporting One-Minute Documentary (`one_minute_documentary/SKILL.md`) and 2D Stickman Cartoon Studio (`2d-stickman/SKILL.md`), strictly routed by CMS channel style without Vidnets or OpenRouter.
---

# Vidzeo Muse Production Worker — Combined Skills

This repository combines **two isolated video production styles** for the Vidzeo CMS Muse worker (`muse-agent.js`), both driven dynamically by CMS channel settings (`targetDurationMinutes` from 1 min to 20+ min, neural voice, aspect ratio, captions, and script rules) with full stage/shot checkpoints:

1. **Documentary Style (`documentary`)** — implemented by [`one_minute_documentary/SKILL.md`](./one_minute_documentary/SKILL.md)
   - Scales shot counts and narration cleanly to the full CMS `targetDurationMinutes` (e.g. ~9 shots for 1 min, ~170 shots for 20 min).
   - Sources real video clips (`yt-dlp`, Wikimedia Commons, Creative Commons, Pexels) and verified still images with per-shot resume checkpoints (`media-checkpoint.json`).
   - Generates spoken narration (`script.txt`) and voiceover (`voiceover.mp3` via `edge-tts` using the CMS channel voice, or `/opt/hatch/bin/tts`).
   - Builds and renders a 1080p@30fps Remotion project from [`one_minute_documentary/templates/remotion-project/`](./one_minute_documentary/templates/remotion-project/).
   - Adapter: `adapters/documentary.js` (checkpoints: `documentary-input.json`, `checkpoint.json`, `media-checkpoint.json`, `shots-plan.json`, sidecar `video.mp4.documentary-manifest.json`).

2. **2D Stickman Style (`2d_stickman`)** — implemented by [`2d-stickman/SKILL.md`](./2d-stickman/SKILL.md)
   - Structures narration into `script.json`, synthesizes Edge TTS (`tools/voice_edge.py`), plans phrase captions (`tools/plan_captions.mjs`), choreographs procedural HTML5 Canvas 2D scenes (`src/shots.js`), passes `capture.mjs check` and `lint`, mixes SFX + ambience (`tools/mix_audio.py`), and renders 1080p MP4 via headless Chrome CDP (`tools/capture.mjs video`).
   - Adapter: `adapters/stickman.js` (checkpoints: `data/stickman-input.json`, `data/checkpoint.json`, `script.json`, `data/mix.wav`, `data/shots-plan.json`, `data/mixed.wav`, sidecar `video.mp4.stickman-manifest.json`).

## Daemon & Durable Checkpoints
- `muse-agent.js start` maintains live CMS heartbeats, streams real-time step/frame logs to the CMS, and resumes in-flight work (`active-job.json`, `progress.json`, stage & per-shot checkpoints) across restarts or network drops without regenerating finished audio/plans/media.

## Style Isolation Rules
- `lib/videoStyle.js` resolves every job strictly to `documentary` or `2d_stickman` (`stickman`), failing closed on conflicting or unsupported styles.
- `lib/masterManifestGuard.js` rejects mixed-engine deliveries.
- **No OpenRouter / No Vidnets**: all LLM planning and narration run strictly via compulsory Muse inference (`lib/museInference.js` / `lib/museBridgeInfer.js`) or direct agent `claim` + `deliver`.
