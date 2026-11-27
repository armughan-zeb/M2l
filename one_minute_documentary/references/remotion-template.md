# Remotion template from the basketball film

This template works for any topic. Basketball is the worked example below. For a new topic, change TOPIC_TAG and HEADER_TEXT at the top of templates/remotion-project/src/Composition.tsx, then replace the shot titles, subtitles and asset file names with ones that fit the new topic.

Verified run: 27 Nov 2026, "Basketball: More Than a Game".
Output: basketball-documentary-60s.mp4, 60.011 seconds, 1920x1080, H.264 plus AAC, 51 MB.
Render: 1800 frames at 30 fps, about 11 minutes on this machine.
Voiceover: 141 words, 57.36 seconds, from /opt/hatch/bin/tts.
Project: ~/workspace/sports-doc/remotion/
Composition: Documentary60 in src/RemotionRoot.tsx, 1800 frames, 30 fps, 1920x1080.
Remotion version: 4.0.547, React 18.

## Shot list

Each Shot is a Sequence with an OffthreadVideo or Img, a slow zoom from 1.0 to 1.12 or 1.12 to 1.0, a 15 frame fade in and out, a dark gradient at the bottom, and a Caption with an orange tag, a large bold title and one short subtitle line. A thin orange progress bar runs along the bottom and "MORE THAN A GAME" sits at the top right in the basketball version.

- 0 to 120 frames, video s1a-shot1.mp4, title ONE BALL, subtitle One court. A million dreams.
- 120 to 240 frames, video s1b-shot2.mp4, title ONE SHOT, subtitle That sound. That swish.
- 240 to 420 frames, image naismith-hd.jpg, title 1891 SPRINGFIELD, subtitle James Naismith. A peach basket. Thirteen rules.
- 420 to 600 frames, video s2b-dribble.mp4, title FROM A PEACH BASKET, subtitle A game the whole world would love.
- 600 to 840 frames, video s3a-game.mp4, title FROM SCHOOL GYMS, subtitle Speed. Skill. Spectacle.
- 840 to 1050 frames, video s3b-lebron.mp4, title TO SOLD-OUT ARENAS, subtitle Icons who turned a sport into a culture.
- 1050 to 1260 frames, video s4a-warmup.mp4, title EVERY CONTINENT, subtitle Street courts at sunset. Packed arenas.
- 1260 to 1440 frames, video s4b-stock.mp4, title ONE LANGUAGE, subtitle The ball. The hoop. The moment.
- 1440 to 1680 frames, video s5a-game.mp4, title MORE THAN A GAME, subtitle Discipline. Teamwork. Belief.
- 1680 to 1800 frames, image dunk.jpg, centred title THE NEXT SHOT IS YOURS.

Audio: <Audio src={staticFile("audio/voiceover.mp3")} /> from frame 0. No background music was added in the basketball film.

## Chrome Headless Shell fix

The first render failed because Remotion could not download Chrome Headless Shell from remotion.media. The fix that worked was to download the 103 MB zip with curl and install it under workspace/sports-doc/remotion/node_modules/.remotion/chrome-headless-shell/158.0.8127.2-remotion-v3/linux64/chrome-headless-shell-linux64/chrome-headless-shell, verified as Chromium 158.0.8127.2. Future re-renders in that project work offline after that install.

Do not use system Chrome at /opt/meta-chromium/chrome. It was tried and cannot launch headless here.

## Footage sources used in the basketball film

Wikimedia Commons via yt-dlp:
- https://commons.wikimedia.org/wiki/File:Basketball-Basic_Types_of_Dribbling.webm
- https://commons.wikimedia.org/wiki/File:Basketball-Shot-Types-and-Shot-Success-in-Different-Levels-of-Competitive-Basketball-pone.0128885.s001.ogv
- https://commons.wikimedia.org/wiki/File:Basketball-Shot-Types-and-Shot-Success-in-Different-Levels-of-Competitive-Basketball-pone.0128885.s003.ogv
- https://commons.wikimedia.org/wiki/File:Final_partit_VCB_Fonteta.ogv
- https://commons.wikimedia.org/wiki/File:Escalfament_VBC.ogv

YouTube via yt-dlp, free stock and Creative Commons clips only:
- https://www.youtube.com/watch?v=EMyCeQG95PI
- https://www.youtube.com/watch?v=7bUNqZtwoqg
- https://www.youtube.com/watch?v=Br7LD2-Of4Q
- https://www.youtube.com/watch?v=jc0MUuMds1M

YouTube needed --impersonate chrome --extractor-args youtube:player_client=android --no-check-certificates on this VM, after curl-cffi was installed. Pexels video pages returned Cloudflare 403 to yt-dlp here, only CDN previews could be fetched.
