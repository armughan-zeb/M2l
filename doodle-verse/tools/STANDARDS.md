# Finishing standards (all three styles)

Every video from this plugin ends the same way unless the user asks for something else.

## Voice and timing
- Never edit or re-time the voice. The picture follows the voice.
- **Voiceover file given:** `python -I tools/words.py voice.mp3 work/words.json` (add `tl`, `es`, `auto`, ... for other languages). If the user also gave the script, correct misheard words in `work/words.json` against it (keep the times).
- **Script only:** `python tools/tts.py script.txt voice.mp3 --words work/words.json` makes a free Microsoft Edge neural voice (default Andrew, `--voice en-US-AvaNeural` etc. for others, `--rate -5%` for slower). It writes the exact word timings, so Whisper isn't needed. Read `voice_spoken.txt` and fix anything TTS would misread (abbreviations, units, URLs), then re-run. Send the mp3 to the user straight away.
- Total frames = round(duration x 30). Everything runs at 30 fps.

## Sound: voice first
- Write the music + SFX cue sheet in `audio/audio.py` (it already has a worked example; keep the synths, rewrite the cues for the new story). It writes `music.wav` and `sfx.wav`.
- Convert the voice first: `ffmpeg -y -i voice.mp3 -ar 48000 -ac 2 audio/voice48.wav`, then inside `audio/`: `python audio.py` and `python mix.py voice48.wav music.wav sfx.wav`.
- `mix.py` sets the music bed at -16 dB under the voice with a dip at 1-4 kHz, ducks it about 5 dB more while the voice talks, keeps SFX at -11 dB with peaks below the voice, and finishes at -14 LUFS with a -1 dBFS limiter. Check the printed levels. If the user says the voice is hard to hear, lower `MUSIC` (0.12) and `DUCK` (-7).
- Lock the picture timing before the sound. Any timing change means re-running audio.py and mix.py.

## Captions
- **9:16 Shorts / Reels:** burn in word-highlight captions: `python tools/captions.py work/words.json work/captions.ass`, then add `-vf "subtitles=work/captions.ass"` to the final ffmpeg encode (run ffmpeg from the project folder so the relative path works). 1-3 words per chunk, uppercase Arial Black, the spoken word turns yellow.
- **16:9 long-form:** no burned captions. Make `out/subtitles_en.srt` with `python -I tools/srt.py work/words.json out/subtitles_en.srt` for YouTube's CC upload.
- **Vox style:** no burned captions in either format. The headlines already carry the words.

## Thumbnails: always two
- Make two curiosity thumbnails, A and B, and recommend one. 1080x1920 for Shorts, 1280x720 (or 1920x1080) for long-form.
- Background: a story frame with an expressive face or the mystery object and empty space for text.
- Text: 2-4 huge words that open a question and don't repeat the title (e.g. "THIS WAS / MEDICINE?!"). A white line plus a bigger yellow `#FFD21F` line. Keep text in the top quarter on Shorts and everything important above the bottom 20 % (the Shorts UI covers it).
- Tool for the cartoon style: `node tools/thumbnail.mjs thumb/thumb_a.json` (spec: `bg`, `out`, `lines[]`, optional `ring`, `arrow`, `badge`; see the example spec in the kit). The Vox style renders its thumbnails as Remotion `<Still>`s.

## Post text
- Finish with copy-ready **title + description/caption + hashtags** in chat and in `out/post_text.txt`. No emojis. Long-form also gets chapters (timestamps) in the description.

## Rendering on a home PC
- Render in the foreground and keep the PC awake: a sleeping PC freezes the render. On Windows, tell the user to set Sleep to "Never" while rendering, or ask for keep-awake if the app offers it.
- Send a stills contact sheet before the long render (`python tools/sheet.py <stills_dir> <sheet.jpg>`), then QA the final video with a contact sheet of about 12 frames.
- Keep the previous version as `*_v1.mp4` when re-rendering.

## Content rules
- Stories, characters and shots are original. Never recreate another creator's video, characters or branding.
- No real living people as cartoon characters, no brand logos or mascots.
- If the video presents something as true, check the key facts with a web search and tell the user about anything that's off.

## Script-First Environment & Character Uniqueness (Zero Template Reuse)
- **Zero Template Reuse:** Reusing the default park environment, bench, pier, or generic Leo in non-park scripts is an automatic catastrophic defect. The only thing preserved across videos is the doodle visual style (hand-painted textures, ink outlines, flat 2D card characters).
- **Environment Matching:** The 3D world must be constructed or configured to represent the exact physical setting of the narration:
  - **Corporate/Tech/Business:** Modern indoor office with desks, laptops, monitors, whiteboard, swivel chairs, city window, filing cabinets (`ENVIRONMENT = 'office'`).
  - **Animals/Nature/Wild:** Dense jungle canopy with tall trees, hanging vines, mossy rocks, ferns, creek bed (`ENVIRONMENT = 'jungle'`).
  - **War/Military/History:** Battlefield trenches with duckboards, sandbags, barbed wire, ruined brick walls, bunker (`ENVIRONMENT = 'battlefield'`).
  - **Cold/Ice Age/Survival:** Snow tundra with drifts, cave overhang, stone campfire with animated flame cones, pine trees (`ENVIRONMENT = 'tundra'`).
  - **Science/Tech:** High-tech laboratory with server racks and blinking LEDs (`ENVIRONMENT = 'lab'`).
- **Character Role Design:** Every character in `cast.js` must be uniquely dressed and outfitted for their story role (suits, ties, glasses, military helmets, safari pith helmets, prehistoric pelts, lab coats).
- **Narrative Action Choreography:** Character poses and actions must enact what is spoken beat-for-beat (typing, phone call, pointing at chart, aiming tool, shivering with cold, celebrating), never defaulting to sitting idle.

## Multi-Angle Characters & Cinematic Framing
- **Rear Views (`view: 'back'`):** When an actor is facing away from the camera or walking into the scene, render with `bodyBack`. Features back hair covering to the collar, vertical coat/jacket back seam, shoulder yoke, flat heels, and `onBackHead` hook (for rear-strapped masks, packs, or gear).
- **Side Views (`view: 'side'` or `facing: -1 / +1`):** Characters must support side profiles with profile eye, nose bump, and proper arm swing.
- **3D Target Facing (`target: 'actor_id' | [x,y,z]`):** Characters automatically turn their body and head to face their interaction partner or subject. Angle between camera vector and target vector determines whether `front`, `side`, or `back` is selected.
- **Camera Cut & Framing Standards:**
  - Closeups (`camCloseup`) must never be sliced through by foreground bench slats, fences, or foliage. Adjust camera Y and Z to frame characters above foreground obstructions.
  - Over-the-shoulder (`camOTS`) should clearly establish the spatial relationship between two actors.

## Universal Ground Anchoring (No Floating Entities)
- **Zero Floating Policy:** Quadrupeds (tigers, horses, wolves), vehicles (tanks, carts, cars), and ground props must sit firmly on the ground surface, never hover in mid-air.
- **Contact Point Correction (`contactY`):**
  - In 2.5D card canvas ($1000 \times 1000$), the default card center corresponds to $Y = 975$.
  - Any entity drawn higher on the canvas must set `canvas.contactY = <lowest_pixel>` (e.g., $715$ for stalking paws, $540$ for sprinting paws).
  - The runtime automatically offsets the 3D card mesh by $(975 - \text{contactY}) \times \text{UNIT} \times s$ so paws/wheels touch the 3D terrain.
- **Dynamic Terrain Following (`window.getGroundHeight(x, z)`):** All grounded entities sample the 3D terrain height at their world coordinates.
- **Ground Contact Shadows:** Set `grounded: true`, `blobAspect` (e.g. 1.8 for elongated animals/vehicles), and `blobScale` to render an anchored ground shadow blob beneath the entity.
