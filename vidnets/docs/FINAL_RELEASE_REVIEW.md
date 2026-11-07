# Vidzeo v1 release review

Date: 30 September 2026. This review covers the previous documentary, media,
queue, voice, editor, and packaging requests. Existing workspace changes were preserved.

## Completed implementation and verification

| Requirement | Production path and result |
| --- | --- |
| Script and niche-specific direction | The film profile and LLM art director feed parallel scene chunks with story arc and neighboring narration. Crime, finance, sports, science, history and the other advertised niche grammars are connected. Live plans differ in scene types, openings, colors and variants. |
| Early parallel planning and longer films | Planning starts alongside retrieval and audio. The default chunk contains two scenes, with four bounded workers. The roughly 90 second short-film allowance expands by planning waves for longer scripts; successful chunks survive individual failures. |
| Custom layouts beyond a fixed catalog | The model can author bounded per-scene Remotion recipes containing semantic graphics, masks, media, geometry and timed copy. Live finance and science runs authored new recipes; the rendered proof uses their actual model responses. |
| Correct component mapping | Directed charts, statistics, timelines, diagrams, comparisons, maps, documents, portraits, collages, headings and image layouts reach their specialist renderers. Explanatory charts, timelines, diagrams and comparisons own their canvas. |
| No universal title opener | Both the prompt and response parser now preserve the model's grounded opening choice. A chapter title is an option, including genuine later section boundaries. |
| LLM copy and short headings | Only approved copy contracts reach directed headings/highlights. Valid emphasis offsets survive handoff. Highlights and chapters have bounded narration-aligned windows, and invalid copy is omitted. |
| Tight, readable text styling | Chapter backgrounds fit their copy and use the director's validated colors. Recipe text inherits the actual scene palette; light analytic canvases no longer receive invisible white labels. Text/evidence columns have separate space. |
| Readable mechanisms | The director can select short exact-narration node labels with evidence spans. Validation and copy synchronization preserve those nodes. Two-node networks have a readable, staged full-canvas layout. |
| Advanced data graphics | Bar growth, ranked bars, line traces, distributions, counters, deltas, timelines and comparisons are routed and rendered. Spoken financial amounts retain their magnitude, currency and qualifying context. |
| Geographic animation | Grounded route, boundary, pin, satellite and textured 3D globe paths are connected. Hydration resolves local assets before rendering and unsupported routes/source marks are rejected. |
| Media variety and beat alignment | Verified scene alternatives become actual local cuts. Opening stills receive more shots when appropriate alternatives exist; unavailable alternatives are not faked through repeated zooms. Adjacent image treatments receive variation without breaking explicit continuity. |
| Clean cut boundaries | Shot timings cover every integer frame. Cuts reset camera timing without fading to an empty canvas. Duplicate rounded boundaries are removed. Alternate stills retain their own dimensions. |
| YouTube quality and vision | HD/source-quality gates, query/identity screening, parallel vision verification, reusable long-source in-points and per-scene timestamps are covered by the retrieval and renderer tests. |
| Portraits, framing and montages | Person cutouts use a bounded local worker with an original-portrait fallback. Masks, focal framing and multi-image compositions are available to the director and rendered in the visual fixture. |
| Sound, transitions and timeline | Narration, sound cues, music, component reveals and transitions share the final edit clock. Competing cue slates are suppressed when a specialist already presents the information. |
| One export at a time | Queue, retry, editor, checkpoint resume and parallel-pipeline exports share the render gate. Concurrency tests assert a maximum of one active video export. |
| Retry and voice UI | Retry/checkpoint controls and model-advertised voice/character catalogs are connected and covered by their frontend and backend tests. |
| Original reported template failure | The pasted John Cena job failed on `keyword-reveal` approval. The shared approved catalog now includes it, and legacy plan repair replaces unsupported templates before validation; the boundary regression passes. Provider timeouts in that log use bounded rescue paths. |
| App/editor wiring | Generated videos, Canvas exports, saved-manifest resume and preview use the shared Remotion path. The desktop package includes the player, backend, fonts, graphics, audio and local runtimes. |

## Additional defects found and repaired during this review

- A sparse scene policy was flattening director-selected photographic layouts.
- Generic canvas routing intercepted specialist heading and profile components.
- Complete-sentence diagram fallback created narrow, hard-to-read labels.
- Two independent rules forced the same chapter opener on every script.
- Title-only diagram nodes were discarded during copy synchronization.
- Fractional recipe geometry could render an explanatory layer at less than one
  percent of the canvas. Consistent fractional layouts now normalize once;
  tiny semantic layers and off-canvas rectangles are rejected.
- Vision normalization discarded scene/domain decisions. Those optional decisions
  now survive both image and clip handoffs, and explicit competing-event,
  location, domain and game-footage contradictions cannot become soft passes.
- Shot planning previously trusted a stale verified flag over newer pixel
  rejection. Conflicting alternates are now excluded before local cuts.
- Zero and negative chart values previously used positive-only geometry. Signed
  bars now share a real zero axis and preserve their direction and magnitude.
- The final FFmpeg directory guard interpreted the argument after `-y` as a
  filename, but Fast Start inserts `-f mp4` there. It now creates the actual
  final output parent before muxing, with a behavioral regression test.
- The release helper searched obsolete installer names. It now selects the
  configured current Windows installer.
- Recipe labels inherited the global theme rather than the current scene palette.
- Later recipe layers could appear before their authored start.
- The visual fixture repeatedly launched browsers and copied unnecessary public
  assets. It now reuses one browser and stages only production render assets.

## Evidence

- Full Python regression suite: **1,350 passed, 1 skipped, 6 subtests passed**
  in 283.76s. The skip is the opt-in paid capability probe; actual image event
  verification was separately exercised live.
- Finalization, Remotion and queue follow-up suite: **56 passed**, including the
  new missing-mux-parent regression.
- Live one-minute planning fixtures: crime 32.31s, finance 62.30s, sports 53.59s,
  science 30.98s; all six scenes per fixture mapped successfully. These are
  fictional validation scripts, not factual research about real events.
- Actual model-authored recipe proof:
  `output/release-visual-audit/model-authored-recipes.mp4`.
- Mixed component stills and short video: production Remotion entry, maps,
  data graphics, diagrams, comparison, evidence, montages and portrait framing.
- Full pipeline Apollo run: audio, retrieval, all six director scenes, verification,
  manifest and 1080p export completed. Its saved verified assets and final plan
  were subsequently rerendered to inspect the repaired cut/graphic paths. Final
  output passed muxing: H.264 1920×1080 at 30 fps, AAC 48 kHz stereo, 32.256s.
  Opening/hold/cut/exit frames were inspected; the competing ocean recovery
  image was removed and a live-confirmed same-event primary retained.
- Packaged runtime bootstrap passed. The bundled Python backend started in an
  isolated writable folder: `/api/status`, `/` and `/remotion-player.js` returned
  HTTP 200. All 248 compared runtime source files matched the package.
- Package credential scan: 10,049 files, zero private credentials or private
  environment files. One advisory names an admin variable; packaged mode
  disables its service-role fallback by default.
- Refreshed Windows installer built successfully: `dist/Vidnets v1.exe`,
  561,924,327 bytes. Embedded archive integrity passed (29,650 files).
  ASAR main process, preload, renderer and Remotion player match the source.
  SHA-256: `549ecd582d8858d58813420b0dca0a55ddbfe28fb293fc04c24a2aece0008426`.
- Checksum sidecar: `dist/Vidnets v1.exe.sha256`. Package evidence is saved in
  `output/release-package-audit/installer.json` and `package-runtime.json`.
  The local installer is unsigned and has not been published to the updater
  channel. A fresh OS installation was not performed; the actual bundled
  backend, runtime paths and archive were verified separately.

## Practical limits

- External LLM, voice and media services can be unavailable, rate-limited or slow.
  Bounded deadlines and recoverable states prevent indefinite blocking; no claim
  is made that external services can never fail.
- The director composes layouts through the validated Remotion language. It does
  not compile arbitrary model-generated JavaScript inside a render.
- Main-path live rendering was exercised with a short history documentary;
  four other niches received live director checks. A complete multi-hour export
  was not run. Longer-film budgeting and timeline handling have regression tests.
- Retrieved media is screened for relevance/quality, but publication rights need
  source review. The Apollo assets lack complete license evidence; its media
  audit marks them for review rather than claiming clearance. Live event review
  excluded the ocean recovery alternate from the lunar-landing scene.
- Visual grounding checks consistency with the supplied script; it is not an
  independent fact-check of every narration claim. The Apollo technical fixture
  and fictional niche fixtures are renderer/planning evidence, not researched
  publication-ready documentaries.
- The opt-in paid Qwen capability probe remains separate from the ordinary suite.
- Visual quality is an editorial judgment. The checked previews are available
  for review; this report does not claim every future script will be flawless.
