# Remotion project template — history / expedition variant

Copy this whole folder to a new project folder for each new topic, then run `npm install` in it (or point the project at this folder's already-installed `node_modules`). Do not edit this template in place.

Use for history, expedition and journey stories — the Iron Road / Lost Expeditions / Echoes of History look. The standard `remotion-project` template stays the default for everything else; `remotion-crime` covers crime and mystery.

## What makes it distinct from the standard template

- **Palette:** warm archival — parchment `#e8dcc0`, sepia `#6b4a2b`, brass `#b98a2f`, deep ink `#2a2118` (standard: orange `#ff7a1a` on black).
- **Typography:** Georgia serif throughout, small-caps era labels, italic subtitles (standard: Arial bold).
- **Motion:** ken-burns pan (slow zoom 1.08–1.16 plus horizontal drift), sepia grade + warm wash, brass double-rule plate frame around every shot (standard: plain zoom, dark gradient lower third).
- **Text:** full-screen parchment era plates at the opening and closing only, plus one parchment caption plate per key beat. Every other shot carries just a small serif plate tag (`PLATE III — ...`), never a caption.
- **Audio:** warm SFX profile in `template-config.json` — strings bed (−26 dB), paper-rustle transitions (−18 dB), distant steam-train ambience (−22 dB), soft chime on era plates (−14 dB); voiceover at full level. No real SFX files ship in the repo — see below.
- **Chrome:** brass progress bar.

## What to replace for any topic

- `TOPIC_TAG` and `HEADER_TEXT` at the top of `src/Composition.tsx` — set them to the new topic and its short title. The Iron Road is the example, with `IRON ROAD` and `THE RAILWAY THAT BOUND A CONTINENT`.
- `src/Composition.tsx` — update the plate labels, captions, era text and asset file names for the new topic. Keep the composition at 1920x1080, 30 fps and 1800 frames for 60 seconds.
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
