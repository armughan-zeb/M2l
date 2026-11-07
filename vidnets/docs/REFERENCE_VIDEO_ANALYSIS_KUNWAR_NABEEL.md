# Reference video analysis: media-led documentary grammar

## Source and inspection record

- Source: [Facebook video](https://www.facebook.com/share/v/19Zy7aMZN3/)
- Resolved public post: Kunwar Nabeel, “Officially killed Vidrush supremacy from just topic to the final video everything was automated in just 0.20$ PKR 55 rupees”
- Runtime inspected: 2:11.33
- Delivery inspected: 1920×1080 VP9/AAC
- Local inspection copy: `output/research/facebook_documentary/kunwar_nabeel_documentary.mp4`
- Caption transcript: `output/research/facebook_documentary/kunwar_nabeel_documentary.en_US.srt`
- Contact sheets: `output/research/facebook_documentary/storyboard_4s.jpg` and `storyboard_8s.jpg`

This is a visual reference study, not a request to copy the creator’s footage, wording, branding, or exact compositions. The useful lesson is the relationship between source media, narration, and restrained graphic emphasis.

## What the edit is doing well

The video keeps the documentary media in charge. Interviews, conference footage, archival factory imagery, phones, portraits, maps, and product details carry the story. Graphics arrive as editorial evidence around the shot, not as opaque cards that replace it.

The visual grammar observed in the sampled frames is:

1. **Locator/date slate** — a small place and date label establishes where and when the story begins.
2. **Media-led narration** — a real shot holds while narration supplies context; the frame is allowed to breathe.
3. **Editorial statistic** — numbers such as `-14%`, `14 YEARS`, and `40%` are large enough to read but remain anchored to the image.
4. **Quote pull** — a short source quote is set over the relevant footage with an attribution, without a filled panel.
5. **Chapter marker** — a restrained chapter title creates structure without becoming a full-screen interruption every time.
6. **Spatial locator** — a map, route, or place marker clarifies geography while preserving the media context.
7. **Archive/contact-sheet beat** — several related products or archival moments are arranged as a quick visual sequence rather than repeated single cards.
8. **Portrait identification** — a person is introduced with a concise name/role treatment, not a large dossier card.

The footage treatment is cinematic but functional: darkened enough for type contrast, never so dark that the source becomes background wallpaper. Accent color is used as a signal—locator, underline, number, or marker—rather than as a permanent decoration. Typography is mostly short, high-contrast editorial text with compact metadata and occasional quote styling.

## Pacing and motion findings

The 4-second and 8-second contact sheets show a varied cadence: interview holds, archive holds, product closeups, stat beats, maps, and chapter structure alternate. The edit does not ask every shot to carry a new animation. Motion comes from the source footage, cut timing, small camera treatment, and occasional graphic reveal.

The system should therefore treat motion as a budget:

- use one dominant motion idea per scene;
- let camera movement in the clip count as motion;
- use a graphic reveal only when it clarifies the narration;
- do not repeat the same grammar or motion verb in adjacent scenes;
- keep a three-scene cooldown for high-salience graphics such as quotes, maps, and stats;
- prefer a quiet hold after a dense graphic beat;
- keep overlay footprint content-sized and inside a vision-approved safe region;
- never let a graphic cover the verified subject or consume the media frame.

## Adopt for Vidzeo

- Media-first scene planning before graphic selection.
- Semantic graphic roles: locator, stat, quote, chapter, archive strip, portrait ID, evidence annotation.
- Open geometry: rules, brackets, pins, routes, underlines, and edge markers instead of thick rectangles.
- A small number of type families: modern grotesk for labels and facts, mono for metadata/numbers, editorial serif only when the quote benefits from it.
- Layout decisions based on subject bounding boxes and safe text regions.
- Deterministic diversity: grammar cooldowns, motion-family cooldowns, and scene-role budgets.
- A visual critic that checks media visibility, text footprint, low-information frames, and repetition before long renders.

## Do not adopt

- Full-frame title cards over usable source media.
- Generic centered cards for every narration beat.
- Decorative particles, light leaks, or gradients that compete with the footage.
- Giant text that extends beyond its main text range.
- Repeating the same stat/quote/map treatment because the template happens to be available.
- Fake charts, maps, routes, or numbers without grounded script payloads.

## Implementation target

Vidzeo’s documentary compositor now uses a director swarm with five explicit roles:

`source_analyzer → media_editor → graphic_director → motion_designer → vision_critic → manager`

The first four create a scene-level plan. The critic audits the plan and any sampled render frames. The manager is the final gate: an unresolved media-visibility, grounding, collision, or repetition issue prevents a long render from being marked ready.

