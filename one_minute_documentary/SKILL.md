# One-Minute Documentary

## Confirm the topic

- Ask the user which topic the documentary covers when they have not named one.

- Offer a short list of example topics with `muse.create_options` and wait for the answer before you source footage.

- Accept any topic, such as a sport, a person, a place, an animal, an invention, a science idea, a food or a historic event.

- Agree the title and the five beats before you build anything.

- Use the five beats Hook 0 to 8 seconds, Background 8 to 20 seconds, Development 20 to 35 seconds, Impact 35 to 48 seconds and Closing 48 to 60 seconds.

- Adapt the beats to the topic, so Background can be origins for a sport, early life for a person or the first discovery for a science idea.

## Source footage and images

- Install yt-dlp with `pip3 install --break-system-packages yt-dlp` when the command is missing.

- Build search terms from the topic, such as the topic name plus history, highlights, nature, cooking or street scenes.

- Prefer Wikimedia Commons, Creative Commons and free stock footage over full broadcasts, films or matches.

- Download 4 to 6 clips with `yt-dlp` and keep the source URL for every clip.

- Use `--impersonate chrome --extractor-args youtube:player_client=android --no-check-certificates` for YouTube when a plain download fails with a bot check.

- Trim each clip with `ffmpeg` to 8 to 12 seconds at 1920 by 1080 and 30 frames per second with no audio.

- Check every clip with `ffprobe` and confirm it has video, a sensible duration and a real file size.

- Find 3 to 4 still images with `/opt/hatch/bin/image-search` and download each one with `curl`.

- Check each image with `file` and keep only real JPEG or PNG files.

- Record every footage and image source URL in the final report.

## Write the script and record the voiceover

- Write a script of 135 to 150 words in natural spoken English.

- Follow the five beats in order and give each beat one clear idea.

- Spell numbers and dates as they should sound.

- Save the script as `script.txt` in the project folder.

- Generate the voiceover with `/opt/hatch/bin/tts speak --output <path>/voiceover.mp3 --text-stdin` and pass the script through stdin.

- Check the voiceover with `ffprobe` and aim for 52 to 60 seconds.

- Shorten the script and record it again when the voiceover runs long.

- Add a sentence and record it again when the voiceover runs under 50 seconds.

## Build the Remotion edit

- Copy the working project in `templates/remotion-project/` as the starting point for a new topic.

- Replace the footage, images and voiceover in `remotion/public/` with the new assets.

- Set `TOPIC_TAG` and `HEADER_TEXT` at the top of `src/Composition.tsx` to the new topic and its short title.

- Replace the shot titles and subtitles in `src/Composition.tsx` with titles that fit the new topic.

- Rename the composition from `Documentary60` in `src/RemotionRoot.tsx` only when you need a distinct name for the film.

- Keep the composition at 1920 by 1080, 30 frames per second and 1800 frames for 60 seconds.

- Use `Sequence` blocks for the five beats with slow zoom, opacity fades and dark gradient lower thirds.

- Add the voiceover with an `Audio` element that starts at frame 0.

- Keep on screen text short and readable with a bold title and one short line below it.

- Read `references/remotion-template.md` for the shot list and the layout used in the basketball film.

- Read `examples/basketball-script.txt` for a worked example of the script length and tone.

## Render and verify

- Render with `npx remotion render src/index.ts Documentary60 --output=<path>/<name>-60s.mp4 --codec=h264 --overwrite` from the Remotion project folder.

- Rename the composition id in the command when you rename the composition.

- Install the Chrome Headless Shell with `curl` under `node_modules/.remotion/chrome-headless-shell/` when the Remotion download fails.

- Do not use the system Chrome at `/opt/meta-chromium/chrome` because it cannot launch headless here.

- Check the finished file with `ffprobe` and confirm a duration of 59 to 61 seconds, H.264 video at 1920 by 1080 and AAC audio.

- Extract frames at 2 seconds and 25 seconds with `ffmpeg` and confirm the titles and images are visible and the frames are not black.

- Allow about 11 minutes for the render on this machine and say so when the user asks for a time.

## Deliver the video

- Copy the finished file to `~/workspace/your_files/` and to the project folder.

- Attach the file in chat on its own line with a short note that names the topic, the length and the resolution.

- Offer a public link only when the user asks for one.

- Upload with `/opt/hatch/bin/remote-storage upload-file --path <path>/<name>-60s.mp4` and share the returned URL with its expiry time.

- Say that anyone with the link can open it and that the link lasts up to 48 hours.

- Invite changes to the topic, pacing, narration or music after delivery.

## Available connectors

- Use `muse.connector_search` to find other connectors and `muse.skill_search` to find related skills.

- If nothing here fits, look on the web for a maintained API or MCP server and set it up with [Custom Connectors and MCPs](/opt/muse/skills/custom-connector-setup/SKILL.md), or build a custom connector.

## Websites

- Use Wikimedia Commons for free footage and stills that can be reused.

- Use YouTube Creative Commons and free stock channels for short clips and keep the source URL.

- Use Pexels for free stock video and stills when a direct download works.

## Alternatives for better results

- Ask the user for a favourite person, place or moment when the topic alone feels too broad.

- Pick one clear angle for a broad topic, such as one invention, one journey or one turning point.

- Use the `ffmpeg` fallback only when the Remotion render keeps failing after the Chrome fix and tell the user before you switch.

- Add background music only when the user asks for it and keep it low under the voiceover.
