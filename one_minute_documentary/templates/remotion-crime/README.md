# Remotion project template — crime / mystery variant

Copy this whole folder to a new project folder for each new topic, then run `npm install` in it (or point the project at this folder's already-installed `node_modules`). Do not edit this template in place.

Use for crime, mystery and disappearance stories — the Black Box / Vanished at Sea look. The standard `remotion-project` template stays the default for everything else; `remotion-history` covers history and expeditions.

## What makes it distinct from the standard template

- **Palette:** near-black navy `#07090d`, cold steel `#7fa8c9`, evidence red `#c8102e`, off-white `#e8e6e1` (standard: orange `#ff7a1a` on black).
- **Typography:** condensed `'Arial Narrow'` bold uppercase titles + `'Courier New'` mono evidence labels (standard: Arial bold + orange tag).
- **Motion:** hard cuts (6-frame dip, no crossfade), slow push-in only, cold desaturated grade + vignette (standard: 15-frame fades, ken-burns-style zoom).
- **Text:** full-screen case-file title cards at the opening and closing only, plus one evidence lower third per key beat. Every other shot carries just a small mono exhibit tag (`EXHIBIT 03 — ...`), never a caption.
- **Audio:** tense SFX profile in `template-config.json` — low drone bed (−24 dB), tape-stop cuts (−12 dB), heartbeat sting at the close (−10 dB), paper shuffle on cards (−16 dB); voiceover at 0.92. No real SFX files ship in the repo — see below.
- **Chrome:** red progress bar and a mono timecode next to the header.

## What to replace for any topic

- `TOPIC_TAG` and `HEADER_TEXT` at the top of `src/Composition.tsx` — set them to the new topic and its short title. The Mary Celeste is the example, with `MARY CELESTE` and `VANISHED AT SEA`.
- `src/Composition.tsx` — update the exhibit labels, evidence kickers, titles, subtitles and asset file names for the new topic. Keep the composition at 1920x1080, 30 fps and 1800 frames for 60 seconds.
- `public/footage/` — put your trimmed clips here, 8 to 12 seconds each, 1920x1080, 30 fps, no audio.
- `public/images/` — put your still images here.
- `public/audio/voiceover.mp3` — put the recorded voiceover here. Aim for 52 to 60 seconds.
- `src/RemotionRoot.tsx` — the composition id is `Documentary60`. Rename it only when you need a distinct name for the film, and use the same id in the render command.

## SFX

`template-config.json` defines the per-template SFX profile (file slots + gains) and `src/Composition.tsx` carries the same profile as `SFX_PROFILE`. No SFX audio files ship in the repo. To use real SFX: drop the files under `public/audio/sfx/` with the names in the profile and set `SFX_ENABLED = true` in `src/Composition.tsx`. Until then the voiceover renders at the profile's level with no bed.

## Render

From this folder, run:

`npx remotion render src/index.ts Documentary60 --output=../<name>-60s.mp4 --codec=h264 --overwrite`

Replace `<name>` with your film name.

If the render fails because Remotion cannot download Chrome Headless Shell, see `../../references/remotion-template.md` for the curl install that fixed this on 27 Nov 2026.
