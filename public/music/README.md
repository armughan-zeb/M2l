# Background Music (BGM) Assets Directory

This directory stores royalty-free background music tracks for Vidzeo CoSystems video production.

## Resolution Priority

When CMS dispatches a video generation job to Vidnets, [`tools.js`](../../../Management/YT%20CMS/lib/tools.js) resolves background music in the following order:

1. **Explicit Setting**: `channel.videoOptions.musicPath` (if configured and exists on disk).
2. **Channel Slug**: `<channel_slug>.mp3` (e.g. `after-death.mp3`, `train-crew.mp3`, `classic-wheels.mp3`).
3. **Genre Preset**: `<genre_preset>.mp3`:
   - `forensic-noir.mp3` (for After Death / crime / forensic documentary)
   - `engineering-blueprint.mp3` (for Train Crew / engineering / rail / machinery)
   - `archival-dossier.mp3` (for Classic Wheels / vintage / automotive history)
   - `broadsheet-heritage.mp3` (for Nostalgic Brit / British heritage)
   - `royal-prestige.mp3` (for Crown Royal, Crown Zone, Royal Celeb)

## Audio Processing Contract

Tracks placed here are automatically:
- Sample-rate converted to **48 kHz stereo** (no pitch or speed changes).
- Seamlessly looped with `-stream_loop -1` across any video length (1 to 20+ minutes).
- Ducked automatically under voiceover narration (`vol * 0.55`).
- Filtered through high-pass (45 Hz) and low-pass (14.5 kHz) EQ curves to prevent frequency masking with narration.
- Mastered to EBU R128 standard loudness (**-16 LUFS**, True Peak **-1.5 dBTP**).
- Faded in smoothly (1.5s) and faded out gently over the last 3 seconds.

## Recommended Source

Source royalty-free `.mp3` tracks directly from the **YouTube Audio Library** (YouTube Studio > Audio Library) to guarantee zero Content ID copyright claims or monetization strikes.
