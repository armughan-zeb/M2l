# Adaptive documentary editing revision

## Scope

This revision repairs shared planning and rendering paths identified in the D.B. Cooper test. It is an implementation record, not a claim that every future film will meet a subjective editing standard.

## Problems found

- Hijacking, ransom and FBI narration could receive a general or aviation editing profile while media planning classified it as crime.
- The canonical subject parser shortened `D.B. Cooper` to `D`, contaminating searches and media contracts.
- Scene splitting broke initials and calendar dates into separate beats. A date could become repeated disconnected timeline cards.
- Automatic source planning retained an old web-heavy policy and could eliminate useful stock context. Explicit user percentages and automatic preferences were not clearly separated.
- Missing media drew unrelated rings, lines and other decorative geometry. Those elements did not explain a narrated fact.
- Text was measured before bundled fonts finished loading. Some numeric/date/name layouts overflowed despite passing manifest checks.
- A successful bespoke diagram could lose its recipe during final frame validation because the validator required a second stock diagram payload.
- Rejected custom diagrams could become empty image scenes even where the narration supported a grounded physical mechanism.
- The real API test exposed a render-slot collision between a separate frame review and an accepted generation. The command-line renderer now waits with progress updates and a bounded deadline; exclusivity remains enforced.
- Cleaned YouTube files were visible to disk recovery while the encoder was still writing. Cleanup now writes `.part.mp4` and atomically publishes the completed result; recovery ignores partial files.

## Connected paths

| Stage | Owner | Behavior |
| --- | --- | --- |
| Niche and grammar | `server/services/editing_profile.py` | Shared script classifier, editorial principles, palette and motion vocabulary |
| Style families | `server/services/editing_libraries.py`, `editorial_director/visual_dna.py` | Supported style/genre mapping, restrained palette defaults and valid brand colors |
| Source allocation | `server/services/mixed_media_planner.py` | Bounded LLM allocation, semantic source preferences, user quota preservation |
| Source execution | `server/app.py` | Actual grouped source assignments; automatic policies bypass the obsolete web minimum |
| Identity | `storyboard_v2/script_parser.py`, `web_image/query.py`, `youtube_pipeline_v2.py` | Full initialed names, aliases and namesake rejection |
| Narration beats | `server/services/scene_splitter.py` | Protected initials and calendar dates in script and word timing paths |
| Creative direction | `editorial_director/director.py` | Film context, parallel scene batches, niche-specific instructions, grounded actors/links and approved copy |
| Original layouts | Validated declarative recipe compiler | LLM-authored geometry, paths and timing inside a bounded Remotion vocabulary; no arbitrary generated JavaScript |
| Render routing | `src/remotion/DocumentaryComposition.jsx`, `engine/SceneRegistry.js` | Specialist components and validated recipes retain ownership of their graphics |
| Typography | `src/remotion/fonts.jsx`, `engine/DocumentaryTypography.js` | Wait for fonts before measuring; fit full phrases; select readable marker ink |
| Visual review | `engine/DocumentaryLayoutAudit.jsx`, `scripts/render-remotion-manifest.js` | Opt-in frame review reports visible text overflow and canvas bounds failures |

## Niche direction

Crime, history, royal, biography, sports, gaming, travel, technology, science, finance, politics, automotive, disaster, health, wildlife, lifestyle and general modes remain supported. Aviation, maritime, engineering, business, food, architecture and education now have explicit grammar/style/source mappings as well.

Crime uses charcoal backgrounds, warm white copy and restrained crimson accents. Evidence, chronology, exact locations and identities determine graphics; generic blue HUD treatments are not crime defaults. Other niches retain their own palettes and explanatory priorities. The grammar instructs the director to choose media holds for atmosphere, data components for comparable numbers, maps for verified geography and diagrams for stated mechanisms.

Automatic source shares are preferences, not promises about successful retrieval:

| Niche | Web images | YouTube | Stock |
| --- | ---: | ---: | ---: |
| Crime | 40% | 40% | 20% |
| Sports | 20% | 75% | 5% |
| Gaming | 15% | 80% | 5% |
| Travel | 15% | 45% | 40% |
| Biography | 50% | 45% | 5% |
| Aviation | 20% | 60% | 20% |
| Maritime | 20% | 55% | 25% |
| Science | 35% | 35% | 30% |
| General documentary | 35% | 45% | 20% |

The LLM may revise these shares with script evidence. Moving action favors clips; named identity and evidence favor archival imagery/footage; generic context can use stock. A stock actor must not impersonate a named subject. Explicit user allocations remain authoritative. Retrieval relevance can change final coverage; rejected or unavailable sources are recorded rather than counted as used.

## Graphic and copy behavior

- Target approximately 30% purposeful graphics, adapting to script density. Do not manufacture a graph to hit a quota.
- Remove unrelated decorative fallback geometry. A failed media source must not fabricate evidence, routes, charts or source marks.
- Preserve distinct specialist ownership so a date/stat is not displayed twice by separate layers.
- Fit dates, currency and comparison values at the render canvas size. Preserve the monetary prefix; exclude years from standalone statistic extraction.
- Preserve approved LLM emphasis spans and contrast. Name reveals finish quickly enough to leave a readable hold within short highlight windows.
- Histograms require real ordered, equal-width non-overlapping bins and frequencies. Arbitrary numbers are not a distribution.
- Diagram actors can use grounded aircraft, ship, port, cargo, passenger, parachute, document, cash and mechanism glyphs. Unknown actors use labeled process stages rather than invented anatomy or machinery.
- Solar alignment/shadow grammars require their exact narrated actors and physical relationship. They are explicitly schematic, not to scale.
- Custom layouts must pass grounding, safe geometry, overlap, copy space and timing checks. Valid layouts survive the final handoff. Rejected layouts may recover through an appropriate grounded specialist; this recovery is recorded separately.

## Research used

- [Remotion text fitting](https://www.remotion.dev/docs/layout-utils/fit-text): measure after font loading and account for layout space.
- [Remotion timing](https://www.remotion.dev/docs/timing): local frame clocks and sequences for staged animation.
- [Vox's Explained production description](https://www.voxmedia.com/about-vox-media/2018/5/23/17385246/vox-and-netflix-launch-original-docuseries-explained/): information-led animation and narrative visual explanation.
- [FBI D.B. Cooper case history](https://www.fbi.gov/history/cases-and-criminals/db-cooper-hijacking): grounding for the crime test script.

## Verification evidence

Evidence is preserved in `artifacts/niche_editing_revision`:

- `targeted-tests.log`: 260 Python tests passed. Coverage includes niche classification, allocation, identity, exact source spans, copy contracts, handoff and recovery.
- Missing asset renderer and exclusive render-slot regressions: four Node tests passed, including bounded waiting and slot ownership.
- `frame-review.json`: ten requested component frames, zero tagged text overflow/bounds failures. `phase-frame-review.json`: 41 opening/reveal/hold/exit samples, zero tagged text layout failures.
- `visual-review.jpg`: labeled rendered samples, manually inspected.
- `component-motion-review.mp4`: 40-second low-resolution component review. This is a silent fixture reel with illustrative chart data, not a researched documentary.
- Live director plans for crime, travel and science preserve model routing, stage timing and composition decisions. The science check exposed genuine custom-layout failures; corrected paths retain supported explanations without silently accepting invalid geometry.
- `director-api-summary.json`: live application director endpoint returned HTTP 200, three intact narration scenes, crime palette and five planned sound cues in 41.22 seconds. This checks planning/handoff, not audio mixing.
- `backend.final.stdout.log`, `api-smoke/response.json`: the first full API attempt returned HTTP 409 because the separate frame review owned the render slot. This failure is retained rather than hidden.
- `api-smoke/response-retry.json`: HTTP 200 after checkpoint resume, 226.8 seconds. Narration and retrieval were reused. Reviewing the export revealed the additional immutable-storyboard initials splitter; that parser is now fixed with exact-offset regression coverage.
- `backend.production.stdout.log`, `backend.production.stderr.log`, `api-smoke/request-final.json`, `api-smoke/response-final.json`: corrected fresh three-scene generation evidence. Read its final response and visual review before accepting its export.

### Corrected fresh export

The final `api/generate` attempt returned HTTP 200 after 420.78 seconds. The preserved export is `api-smoke/generation-package-final/Adaptive Crime Pipeline Final Test.mp4`: 16.32 seconds, 854×480 H.264 with AAC audio, three intact narration scenes. It uses image/video/image media, a dark crimson crime palette, a brief name tag, a YouTube segment and a grounded $200,000 ransom statistic. The two sound cue assets exist: zoom whirr at 12.22s and cash counter at 14.757s. The sound audit reports zero exact-repeat/category conflicts and zero transient alignment error. This is metadata/file verification, not a listening review. `final-video-summary.json` preserves the stream and scene checks; `final-video-contact-sheet.jpg` preserves six inspected moments.

This short film is a pipeline smoke test. It does not certify a full-length crime film or all niche aesthetics. The YouTube segment includes an embedded TV/map treatment from its source documentary; that remains a source-selection quality consideration. The approximately seven-minute end-to-end processing time also remains a performance consideration. There is no basis here to promise faster-than-human or better-than-every-editor results.

## Quality limits

Structural checks do not prove media identity or artistic quality by themselves. Frame inspection and source evidence remain necessary. An unavailable provider, blocked media source or unsuitable footage cannot be made infallible. Emergency recovery can preserve exportability while reducing richness; it must remain visible in diagnostics. Production acceptance should use completed retrieval-to-export runs across multiple niches, with final frame and audio review, not only this targeted regression suite.
