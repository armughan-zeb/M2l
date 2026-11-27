# Remotion project template

Copy this whole folder to a new project folder for each new topic, then run `npm install` in it. Do not edit this template in place.

## What to replace for any topic

- `TOPIC_TAG` and `HEADER_TEXT` at the top of `src/Composition.tsx` — set them to the new topic and its short title. Basketball is the example, with `BASKETBALL` and `MORE THAN A GAME`.
- `src/Composition.tsx` — update the shot titles, subtitles and asset file names for the new topic. Keep the composition at 1920x1080, 30 fps and 1800 frames for 60 seconds.
- `public/footage/` — put your trimmed clips here, 8 to 12 seconds each, 1920x1080, 30 fps, no audio.
- `public/images/` — put your still images here.
- `public/audio/voiceover.mp3` — put the recorded voiceover here. Aim for 52 to 60 seconds.
- `src/RemotionRoot.tsx` — the composition id is `Documentary60`. Rename it only when you need a distinct name for the film, and use the same id in the render command.

## Render

From this folder, run:

`npx remotion render src/index.ts Documentary60 --output=../<name>-60s.mp4 --codec=h264 --overwrite`

Replace `<name>` with your film name.

If the render fails because Remotion cannot download Chrome Headless Shell, see `../../references/remotion-template.md` for the curl install that fixed this on 27 Nov 2026.
