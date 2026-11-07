# Script-Driven Visual Director Rules

Vidzeo does not assemble videos by rotating finished templates. Remotion receives a semantic scene plan from the script and composes each beat from reusable visual primitives.

## 1. Script is the source of truth

- Every graphic must answer a specific script need: date, person, place, statistic, quote, comparison, cause/effect, evidence, or chapter change.
- Never add text because a scene is visually empty.
- Visible copy must come from the narration, a verified fact, or an explicit user-provided label.
- If the script does not justify a graphic, use footage, atmosphere, camera movement, or a clean hold.
- A visual beat must have one clear job. Do not combine a counter, map, quote, and headline into one overloaded card.

## 2. Modern typography by default

- Automatic projects use the verified local modern font set: Inter, DM Sans, Plus Jakarta Sans, Oswald, Archivo Black, JetBrains Mono, and Lora.
- Use one display face, one readable body face, and one optional accent face per video.
- Body copy must stay in Inter, DM Sans, or Plus Jakarta Sans. Mono and serif faces are for short, contextual accents only.
- Novelty, handwritten, horror, and decorative faces are never used for ordinary narration.
- Use hierarchy through size, weight, spacing, and placement—not by changing fonts every scene.
- Keep display text short: normally one phrase or one fact, never a paragraph.

## 3. Choose motion from meaning and media

| Script beat | Preferred visual grammar | Motion language |
| --- | --- | --- |
| Date or chronology | date marker, timeline, calendar | trace, wipe, paper reveal |
| Statistic or comparison | counter, chart, split comparison | count, bar rise, split reveal |
| Person or place | profile label, map, route | slide, pin, route draw |
| Evidence or document | source card, evidence board, redaction | stamp, paper, mask reveal |
| Cause and effect | diagram, decision tree, blueprint | line build, node reveal, directional flow |
| Quote or testimony | quote pull, source card | fade, line build, restrained slide |
| Emotional turn or climax | kinetic statement, spotlight | controlled scale, focus, impact |
| Supporting narration | full-bleed media | subtle crop, drift, or hold |

- Motion must be proportional to the beat: quiet facts get measured movement; turning points get stronger accents.
- Use focal-aware motion when a subject or safe region is available.
- Do not apply the same generic zoom, slide, or pop to every scene.
- Avoid shake, glitch, particles, light leaks, and heavy overlays unless the script and genre justify them.
- Transitions are punctuation, not decoration. Prefer a clean cut when a cut is editorially correct.

## 4. Anti-repetition budgets

- Never repeat the same graphic template in adjacent scenes.
- Do not repeat the same visual family within the previous two designed scenes when another valid family exists.
- Do not repeat the same entry motion in adjacent graphic scenes; vary direction, timing, and easing.
- Do not repeat the same transition family twice in a row.
- A single graphic template should normally stay below 9% of the scene count, with a minimum budget of one use for short videos.
- Quantitative charts are reserved for real relational data and are capped at roughly one per ten scenes.
- Concept diagrams are reserved for true causal or mechanical explanations and are capped at roughly one per seven scenes.
- Repetition checks must consider perceptual families, not only IDs: three different wipes still count as repeated push motion.
- Persist a design fingerprint between videos and regenerate when a new video collides with a recent edit fingerprint.

## 5. Composition variety

Across a video, deliberately vary at least three of these dimensions:

- graphic family;
- layout and safe-area position;
- image treatment;
- camera direction and motion family;
- transition family;
- overlay texture;
- sound accent;
- pacing and hold duration.

Variation must preserve the video's art direction. It should feel like one editor making different decisions, not a random slideshow.

## 6. Timing and readability

- Design around narration timing first; graphics enter on the relevant words, not at arbitrary scene boundaries.
- Keep most graphic moments on screen for at least 1.5 seconds and give important facts enough time to read.
- Exit graphics cleanly before the scene cut; never let a card disappear abruptly.
- Keep text inside safe regions and away from faces, key subjects, subtitles, and UI overlays.
- Use one focal point per frame and maintain generous negative space.
- Voiceover always has priority over sound effects and visual density.

## 6A. Adaptive explainer rhythm

- Direct the complete story before splitting scene work. Every LLM scene batch receives the film's opening-to-payoff story map, its global position, adjacent setup/payoff beats, and the locked visual motif so parallel decisions retain narrative context.
- Use an original Vox-inspired explainer grammar: visual arguments, clean map moves, revealing evidence marks, comparison cuts, cutouts, counters, and causal diagrams. The story and its factual payload decide which instrument appears.
- For explanatory beats with grounded information, prefer a designed Remotion reveal over another generic image hold. On longer scenes, plan a meaningful visual change every 3–5 seconds when narration timing allows, then settle for reading. Do not animate simply to fill a gap.
- Vary scale, composition, camera direction, reveal, and transition through the film. Use a sharp cold open, a changing explanation rhythm, a clear visual turn, and a payoff with room to land. Atmospheric footage, human emotion, and testimony earn quiet holds.
- The LLM chooses each on-screen highlight from the exact narration and supplies its emphasis span. The renderer only shows grounded approved copy; it does not invent a replacement keyword when the LLM phrase fails validation.
- Keep short highlight copy to roughly 45% of scenes or fewer, spaced with a scene of cooldown. This limit applies to text, not to grounded maps, diagrams, timelines, evidence, or comparisons.
- Art-direction, scene-batch, and copy-repair requests have bounded deadlines. Parallel chunks are small and context-rich; a failed chunk falls back locally without throwing away successful LLM direction for the rest of the film.

## 7. Quality gate before full render

The planner must reject or regenerate a plan when it has:

- adjacent template, layout, motion, or transition repeats;
- a graphic that is not grounded in the script;
- a chart without narrated data;
- text covering a verified subject or unsafe region;
- one visual family dominating the edit;
- a font outside the verified documentary set;
- a generic card that could be removed without losing meaning.

Draft previews should be rendered at low resolution first. Full Remotion rendering happens only after the semantic plan and variation audit pass.

## 8. Advanced documentary grammar system

The default Remotion route is `advanced-doc-v1`. It is a compositional system, not a rotating card library. The director selects one grammar per beat and the renderer builds the frame from type, media, rules, locators, annotations, and negative space.

- `cinematic_type`: claims, testimony, chapter statements, and emotional turns.
- `evidence_desk`: sourced documents, profiles, archival fragments, and verified highlights.
- `data_story`: grounded counters, plots, bars, and before/after relationships.
- `spatial_atlas`: routes, locations, systems, and causal relationships using only supported endpoints.
- `chronology`: dates, eras, and milestone sequences.
- `signal_console`: warnings, transcripts, operational logs, and technical evidence.

The grammar is chosen from the semantic role and payload. A missing payload downgrades to grounded cinematic type; it never invents a route, chart, date, or process.

## 9. Documentary art-direction rules

- Use full-canvas compositions by default. Solid rounded panels are metadata tools, not the primary composition.
- Use modern display/body/number roles: Archivo Black or Oswald for short claims, Inter/DM Sans/Plus Jakarta Sans for readable support, and JetBrains Mono for dates, coordinates, and source metadata.
- Give motion a semantic verb: trace, reveal, bracket, accumulate, pin, peel, scan, lock, drift, or dissolve.
- Do not reuse the same visual signature in adjacent scenes. Different sub-compositions within one family are allowed; identical composition and motion are not.
- Keep a long-form rhythm: footage and quiet holds must interrupt graphic emphasis. Graphics are evidence and explanation, not wallpaper.
- If a frame has low information, a missing asset, or a blank media source, the vision critic reports it and the manager blocks the long render until the asset is replaced or the scene is converted to grounded graphics.

## 10. Media-first composition rules

Source clips and photographs are the hero layer whenever a usable media asset
exists. Motion graphics support the shot; they do not replace it.

- Keep source media visible at a minimum 78% visibility budget. Do not wash it
  out with a full-screen graphic, chart background, or heavy blur.
- Route mixed media scenes through the media-first overlay compositor. Use the
  full documentary grammar only for graphic-only beats with no source media to
  protect.
- Use vision-approved `safe_text_regions` first. If none exist, place the
  overlay opposite the verified `subject_bbox`; never place text on the
  subject as a convenience fallback.
- Keep mixed-media scenes to one restrained editorial instrument by default,
  with a maximum overlay area of 28% and maximum opacity of 0.88.
- Prefer field captions, route locators, chronology strips, evidence
  annotations, and corner data insets. Avoid giant title cards, wallpaper
  grids, centered panels, and narration dumps over footage.
- Headings, captions, labels, and ordinary text stay in the selected corner
  safe region and never expand beyond the rendered text range. A chart or
  graph is the exception: it may use the central safe region only when the
  script supplies grounded relational data.
- Location beats use coordinate-backed locator points, visible pointer pins,
  and a traced route only when the script supplies real endpoints or
  locations. Unknown place names are rejected rather than guessed.
- Centered charts keep the source media recognizable underneath a restrained
  blur and soft contrast wash. They never use a thick opaque card as a
  background, and they yield to a media hold when the script does not ask for
  data visualization.
- Use open compositions: no thick filled rectangles behind ordinary text.
  Hairlines, brackets, dots, underlines, and small text underlays may support
  contrast, but every rule must terminate at the rendered text range.
- The overlay's visual footprint must be content-sized. Safe regions constrain
  placement; they do not authorize a background panel that spans the entire
  region.
- Let footage carry camera movement. Graphics use short trace, snap, or rise
  motions and then settle; they must not compete with the shot's subject.
- For video, preserve continuous action and avoid transitions that freeze,
  crop, or obscure the subject. For stills, use restrained camera motion while
  keeping focal content and source texture legible.
- The manager rejects a long render when media is not the hero layer, an
  overlay has no safe region, or a graphic collides with a verified subject.

## 11. Agent responsibilities

- Director: reads the script, assigns semantic intent, payload, grammar, typography roles, timing, and motion verbs.
- Composer: renders only closed-vocabulary Remotion systems and preserves the scene contract.
- Vision critic: samples rendered frames and checks low-information output, safe placement, semantic payloads, and repeated signatures.
- Manager: owns the rules, converts critic findings into revision instructions, and decides whether a full render is ready.

The manager is exposed in the manifest under `quality_control`; the visual system and research memo are recorded under `visual_system` and `docs/DOCUMENTARY_MOTION_RESEARCH.md`.

## 12. Documentary director swarm

The media-led route runs a deterministic swarm before Remotion renders:

`source_analyzer → media_editor → graphic_director → motion_designer → vision_critic → investigative_agents → manager`

- `source_analyzer` identifies whether the beat is a quote, stat, chapter, locator, archive, portrait, evidence, analytic, or field-caption beat.
- `media_editor` makes the clip/photo the hero, chooses a safe region, and records the subject collision policy.
- `graphic_director` chooses an open semantic grammar: `quote_pull`, `chapter_slate`, `data_inset`, `locator_inset`, `archive_contact_sheet`, `portrait_id`, `evidence_annotation`, `analytic_callout`, or `field_caption`.
- `motion_designer` assigns a semantic motion family such as `quote_reveal`, `route_draw`, `contact_sheet_slide`, `portrait_pin`, `bracket_trace`, or `quiet_hold`.
- `vision_critic` checks the plan and sampled frames for low information, unsafe overlays, media visibility, and repetition.
- `investigative_agents` challenge the director: media-only beats must remain
  media holds, maps must have real payloads, charts must have grounded data,
  and placement must match the grammar (corner text, centered charts with
  blurred underlay).
- `manager` blocks long renders until all of those checks pass.

The swarm keeps one clear graphic job per scene, a short cooldown for high-salience graphics, and transparent/open geometry by default. If a preferred grammar would repeat immediately, it chooses another supported composition or lets the footage breathe; it should not replace a meaningful beat with a generic field caption.

The reference-inspired media grammars are deliberately small and compositional: date/location slates, quote pulls, editorial stats, route locators, archive strips, and portrait IDs. They are placed around the footage and remain inside the main text range; they are not background panels or full-frame replacements for usable media.

## Operating principle

Reusable components provide speed and reliability. The script, semantic role, media evidence, and variation audit decide how those components are composed. The system should produce a different edit grammar for each story while keeping typography and brand direction coherent.
