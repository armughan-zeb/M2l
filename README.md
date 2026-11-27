# Vidzeo Muse Worker (`M2l`)

Remote Muse worker for Vidzeo CoSystems CMS, combining two strictly isolated video production engines:

1. **One-Minute Documentary** ([`one_minute_documentary/SKILL.md`](./one_minute_documentary/SKILL.md))
   - **Media**: `yt-dlp` footage clips (trimmed 1920x1080@30fps) + verified Wikimedia/web still images.
   - **Voiceover**: `/opt/hatch/bin/tts` or `edge-tts`.
   - **Renderer**: Remotion (`one_minute_documentary/templates/remotion-project/`), 1920x1080@30fps H.264 + AAC.
2. **2D Stickman Cartoon Studio** ([`2d-stickman/SKILL.md`](./2d-stickman/SKILL.md))
   - **Visuals**: Procedural HTML5 Canvas 2D vector stickmen, cel-shaded environments, phrase captions.
   - **Audio**: Edge TTS + SFX bed (`tools/mix_audio.py`).
   - **Renderer**: Headless Chrome CDP (`tools/capture.mjs video`).

## Quick Start

```bash
# 1. Install dependencies (Node 18.17+, Python 3, ffmpeg, yt-dlp, edge-tts)
bash setup-linux.sh

# 2. Pair with your Vidzeo CMS
node muse-agent.js pair --cms https://your-cms-host --code MUSE-XXXX-XXXX --name "Muse Worker"

# 3. Verify setup
node muse-agent.js doctor

# 4. Run automated worker loop (with Muse inference bridge) OR agent-driven claim/deliver
node muse-agent.js start
# or agent-driven:
node muse-agent.js claim --wait 60
node muse-agent.js deliver --job <jobId> --skip-thumbnail
```

## Verification

```bash
npm test
```
