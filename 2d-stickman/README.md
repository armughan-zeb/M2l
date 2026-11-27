# Stickman Cartoon Studio

A complete, standalone 2D Cartoon Stickman animation repository recreating the clean, bright **2D Stickman Cartoon Shorts** aesthetic (cel-shaded `'white'` gloved pure stickmen & `'black'` t-shirt/shorts stickmen inside bright illustrated 2D environments).

- **Zero npm packages** (pure Node.js built-ins + HTML5 Canvas 2D + Headless Chrome CDP + FFmpeg).
- **Free zero-silence narration** via Microsoft Edge TTS public endpoints (`tools/voice_edge.py`).
- **Dual native formats**: full-bleed **Vertical Shorts (`1080×1920`)** and **Landscape (`1920×1080`)**.
- **Low-CPU VM compatible**: 100% CPU-based rendering (runs on 1.5-core Muse/cloud VMs with `SOFTWARE_GL=1`, `--draft` mode, and zero GPU required — see [`docs/MUSE-VM-GUIDE.md`](docs/MUSE-VM-GUIDE.md)).

## Repository Structure

```
stickman-cartoon-studio/
├── SKILL.md                          # AI agent / creator workflow instructions
├── STYLE.md                          # Locked 2D stickman visual specification
├── README.md
├── package.json
├── index.html
├── render.sh                         # End-to-end render driver (voice -> captions -> lint -> mix -> mp4)
├── script.json                       # Active narration script, format profile, voice & bed config
├── src/
│   ├── stickman.js                   # Stickman rig ('white', 'black', 'casual'), gloves, shoes, cartoonTiger
│   ├── environments.js               # Tropical island beach, palm hut, sunny/dusk jungle, bamboo, Sundarbans delta
│   ├── shots.js                      # Active scene choreography
│   ├── main.js                       # Full-bleed canvas renderer & capture API
│   └── core/                         # lib, timing, profiles, captions, capplan, sound
├── tools/
│   ├── setup.sh                      # One-step dependency setup & audio synthesis check
│   ├── voice_edge.py                 # Edge TTS synthesis + silence trimming -> data/mix.wav, lines.json, words.json
│   ├── plan_captions.mjs             # Auto phrase splitting + yellow *keyword highlights
│   ├── capture.mjs                   # Chrome CDP check, lint, cues, frames, video encoder
│   ├── lint.mjs                      # Pre-render QA gate
│   ├── mix_audio.py                  # FFmpeg SFX + ambience bed + narration mixer
│   └── make_sounds.py                # Procedural WAV generator for SFX & beds
├── docs/
│   └── MUSE-VM-GUIDE.md              # Running smoothly on 1.5 vCPU cloud VMs without a GPU
├── examples/
│   ├── 01-island-shorts/             # 1080x1920 Vertical Short ("2 Stickmen Stranded on an Island")
│   └── 02-tiger-survival-wide/       # 1920x1080 Landscape ("How to Survive a Tiger Attack")
├── sounds/                           # Pre-synthesized SFX & ambient beds (.wav)
└── fonts/                            # Inter-Black & Caladea-Bold
```

## Quickstart

```sh
# 1. Install Python packages (edge-tts, numpy, soundfile) & verify ffmpeg/node
bash tools/setup.sh

# 2. Generate voiceover, captions, QA lint, audio mix, and final MP4
bash render.sh
```

Or run steps individually:

```sh
python3 tools/voice_edge.py --force
node tools/plan_captions.mjs --force
node tools/capture.mjs check
node tools/capture.mjs lint
node tools/capture.mjs cues
python3 tools/mix_audio.py
node tools/capture.mjs video renders/out.mp4
```

### Switching Examples
To switch between the included examples, copy an example's `script.json` and `shots.js` into the root / `src/` (fixing the relative import in `shots.js` back to `./`) and run `bash render.sh`.
