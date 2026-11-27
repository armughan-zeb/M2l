---
name: stickman-cartoon-studio
description: Create 2D cartoon stickman shorts (1080x1920) and landscape explainer videos (1920x1080) using procedural HTML5 Canvas 2D vector characters ('white' gloved pure stickman and 'black' clothed stickman), bright cel-shaded environments, Edge TTS voiceover, SFX mixing, and zero-dep headless Chrome rendering.
---

# Stickman Cartoon Studio — Agent & Production Workflow

Use this skill whenever generating a new 2D Stickman Cartoon video (vertical Shorts or 16:9 Landscape) in this repository.

## 1. Choose Format & Profile (`script.json`)
- **Landscape 16:9 (`1920×1080`)**: `"profile": "wide"`, `"format": "wide"`, `"captions": "phrase"`
- **Vertical Shorts 9:16 (`1080×1920`)**: `"profile": "short"`, `"format": "vertical"`, `"captions": "off"` (or `"phrase"`)
- **Voice**: Microsoft Edge TTS public endpoints (`"voice": "en-US-ChristopherNeural"` or `"en-GB-RyanNeural"`).
- **Ambience Bed**: `"sound": { "mode": "auto", "bed": "jungle" | "drone" | "tense" | "wind", "transitions": true }`

## 2. Step-by-Step Build Pipeline
1. **Write `script.json`** with punchy short lines (~8–13 words per line; ~2.5s–4s each).
2. **Synthesize Voiceover**:
   ```sh
   python tools/voice_edge.py --force
   ```
   Produces `data/mix.wav` (silence-trimmed), `data/lines.json`, and `data/words.json`.
3. **Plan Captions** (if `captions` is `"phrase"`):
   ```sh
   node tools/plan_captions.mjs --force
   ```
4. **Choreograph `src/shots.js`**:
   - Import `stickman`, `cartoonTiger`, etc. from `./stickman.js` and backgrounds from `./environments.js`.
   - Use `L(i)` and `B(i, frac)` from `./core/timing.js` so cuts land between `1.5–4.5 s`.
   - Give every shot a `motion` (`draw`, `sweep`, `impact`, `zoom`, `rise`, `glow`, `stamp`).
   - Add timed `CUES.push({ name, at })` using sounds in `sounds/` (`whoosh`, `swish`, `pop`, `boom`, `rumble`, `riser`, `ding`, `click`, `tick`, `stamp`).
5. **Run QA Checks (Mandatory Before Render)**:
   ```sh
   node tools/capture.mjs check
   node tools/capture.mjs lint
   ```
6. **Mix Audio & Verify Broadcast Level**:
   ```sh
   node tools/capture.mjs cues
   python tools/mix_audio.py
   ```
   Verify `data/mixed.wav` sits cleanly around `-24` to `-19 dB` mean volume with zero clipping.
7. **Render Final MP4**:
   ```sh
   node tools/capture.mjs video renders/out.mp4
   ```
   On low-CPU cloud VMs (1.5 vCPUs, no GPU), run with `SOFTWARE_GL=1` and optionally `--draft` for previews (see `docs/MUSE-VM-GUIDE.md`).
8. **Inspect Rendered Frames**:
   ```sh
   node tools/capture.mjs frames review/check "1.5,5.0,10.0"
   ```
   Inspect extracted JPEGs visually to confirm zero text overlap, clean gloves/hands alignment, and crisp character framing.

## 3. Durable Checkpoints & Resume (Never Lose Completed Work)
When running under `muse-agent.js` or agent-driven production (`work/<jobId>/stickman-project/`), always persist and check atomic stage checkpoints keyed by `inputDigest` before repeating heavy steps:
1. **`../script.txt` & `data/stickman-input.json`**: tracks job digest (`title`, `narration`, `voice`, `profile`, `format`, `captions`). Clear stale checkpoints only if this digest changes.
2. **`script.json`**: reuse existing structured lines on resume instead of re-calling `stickman_script_json`.
3. **`data/mix.wav`, `data/lines.json`, `data/words.json`**: skip `voice_edge.py` and `plan_captions.mjs` when verified on disk for the active digest.
4. **`data/shots-plan.json` & `src/shots.js`**: reuse planned scenes (`stickman_shots_plan` / `stickman_shots_repair`) across restarts.
5. **`data/checkpoint.json`**: tracks `qaPassed` and `audioMixed` keyed by `sha256(shots.js)`, skipping `capture.mjs check/lint` and `mix_audio.py` when already verified against `data/mixed.wav`.
6. **`../video.mp4.stickman-manifest.json`**: stamped upon completion alongside `../video.mp4`.
