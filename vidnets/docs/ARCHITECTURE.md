# Vidzeo video production architecture

## Runtime entry points

- `main.js` starts Electron and the local Python service (`main.py`). The renderer uses the narrow bridge in `preload.js` and the UI under `src/`.
- Script generation enters through `POST /api/generate` in `server/app.py`, which normalizes the request, locks one creative variation seed, prepares a job package, and calls `_run_video_pipeline`.
- Queued jobs enter through `server/services/queue_manager.py` and the queue callback registered in `server/app.py`. The queue owns job state, retry/checkpoint policy, and cancellation.
- Canvas Studio has its own editable project path. It validates a Canvas manifest, converts it through `_canvas_project_to_semantic_manifest`, then joins the shared renderer. Its workflow and editor-specific contract are documented in `docs/EDITOR_ARCHITECTURE.md`.
- Saved Remotion plans can resume through `_resume_checkpointed_remotion_render`; they skip media and narration work and reuse the persisted plan.

## Canonical generated-video flow

`_run_video_pipeline` is the only authority for script-driven exports. It fixes `semantic_variation_seed` and builds a deterministic `editing_profile` before launching the independent work. That profile locks the classified niche, vocabulary, approved palette/font families, and safe component choices; it does not make a second LLM request.

The pipeline overlaps these branches:

1. **Audio and timing:** TTS and Whisper produce scene narration, final durations, and word-level clocks.
2. **Media retrieval:** source policy selects web, stock, YouTube, or mixed-media retrieval. Scene query planning and source-specific resolvers search and verify footage/images while audio is being prepared.
3. **Visual direction:** `_run_director_plan` starts from the script scene plan with the same seed and niche profile. `EditingDirector.plan_documentary` builds a film-level art direction, then plans bounded scene chunks in parallel. Each chunk keeps the story arc and neighboring beats, but only receives its relevant media/geography facts. Successful chunks are retained if another chunk fails; local fallback is grounded and text-free.

After the final audio scene plan is ready, `_reconcile_parallel_director_plan` binds the early decisions to narration timing and verified media. `_render_video_with_semantic_editing` creates the baseline semantic manifest, merges the director's approved scene decisions, repairs unsupported data/routes and repeated treatments, and validates the combined plan. `media_shot_plan.py` then turns each director media choice into a set of distinct, scene-local cuts aligned to word timing when available.

## One editing timeline contract

The version 4 Remotion manifest is the handoff between director, semantic planner, QA, and renderer. Production exports set `timeline_contract_version: 1` after final audio and media timing are reconciled:

- **Film clock:** scene `start`, `duration`, and optional `end` are absolute seconds; scenes remain ordered and do not overlap.
- **Scene visual lane:** the LLM-selected primary component, safe variant, grounded data, approved template/recipe, graphic theme, and visual-DNA palette.
- **Media lane:** verified media and distinct `media.shots`; each shot uses a scene-local `shot_start`/`shot_end`, plus an optional source in-point for reused long footage.
- **Motion lane:** up to three exact-narration `motion_cues`; `time_local` and `duration_seconds` are local to the scene and cannot escape its bounds.
- **Copy lane:** the LLM's approved short copy, source span, emphasis offsets, and safe placement. Empty copy stays empty; renderers do not reconstruct a title from narration in director-led scenes.
- **Sound lane:** licensed music and sound cues are attached to narrative/graphic events and timed on the film clock.
- **Transitions and captions:** transition durations and word timing are carried with the timeline; captions are rendered inside the Remotion composition from scene-local timed words. Subject-safe placement uses the scene's approved safe region, and captions yield while a text-bearing map, diagram, chart, or overlay is on screen. Impact emphasis highlights only the active word; other caption styles remain open over the footage. User controls select visibility, automatic or fixed position, style and bundled font. SRT files may remain as transcript sidecars, but the delivered picture is not captioned with an FFmpeg subtitle pass.
- **Graphics coverage:** the director aims for explanatory graphics on about 30% of scene time. It can move above or below that target when the story's evidence density or emotional pacing calls for it; subtitles and repeated decorative labels do not count.

Every lane is auditable. `_validate_remotion_edit_plan` checks the scene/component contract, script-grounded copy, route/source/data payloads, media files, and director visual critique. `visual_critic.audit_manifest` also checks scene, media-shot, graphic, and motion-cue bounds. A failed preflight is saved for correction/retry instead of delivered as a clean master. Rendered frames receive a separate visual QA pass.

## Creative responsibility and adaptive variation

`EditingDirector` is the single LLM creative director for the Remotion export. It reads the whole script, selects a distinct niche grammar, creates one coherent film direction, and decides per beat whether to use full-frame maps, diagrams, comparisons, charts, timelines, documents, portraits, imagery, or a quiet hold. Niche guidance is an evidence vocabulary, never a fixed component sequence or quota. Sports and gaming have separate classifications and render presets; explicit categories take priority over ambiguous words in a title (for example, “sports car” is automotive).

`visual_dna.py`, `editing_libraries.py`, and `documentary_template_system.py` hold constrained visual systems. They define safe palettes, font roles, motion families, and allowed treatments. They do not substitute a repeated template plan for the director's scene decisions. The renderer resolves those decisions into Remotion components and scene-local frame clocks.

For a beat that needs a new composition, the director can author a validated declarative recipe instead of selecting a prebuilt card. The recipe DSL combines up to 16 timed text, shape, image, chart, map, diagram, timeline, comparison, counter, and media-montage layers. `DeclarativeRecipe.jsx` interprets that data as authored Remotion components; the model never supplies executable React or JavaScript. The director may mix these layers with specialist components while the manifest still enforces scene evidence, local media, exact copy, and timing bounds. Niche rules change the available visual vocabulary and palette, while the beat and verified media decide which instrument is used.

Portrait cutouts are an optional local render preparation step. `person_cutout_service.py` runs the human-segmentation model in a separate process with a hard wall timeout and a cache. A missing model or failed inference leaves the original verified image in place so portrait styling cannot block a render.

## Remotion and export boundary

`native_render_policy.render_semantic_documentary` selects the required Remotion export. `server/app.py::_run_with_remotion_render_slot` shares one render gate across queue, retry, and editor renders so two Remotion exports do not compete. The Remotion runtime entry is `src/remotion/DocumentaryComposition.jsx`; it composes scene media, `MediaShotSequence`, primary scene components, director motion graphics, text treatments, map components, transitions, captions, and sound. `server/services/remotion_renderer.py` packages and invokes the renderer, then returns the master for delivery.

The Remotion component catalog is under `src/remotion/components/`. `src/remotion/engine/SceneRegistry.js` maps versioned visual types to specialist components; maps, charts, timelines, diagrams, evidence, comparisons, portraits, and media each use their own renderer. `EditorialSceneCanvas.jsx` is a compatibility renderer for approved compositions, not the main router for every visual type.

Approved typography templates selected over footage keep their own motion grammar: `production_contract.py` carries validated template, variant, placement, and palette choices into `scene.text`; `DocumentaryComposition.jsx` sends `kinetic-statement`, `headline-center`, and `keyword-reveal` to `KineticText`, and `chapter-opener` to `ChapterTitle`. The exact approved copy contract supplies the words and emphasis spans. `EditorialType.jsx` sizes the impact slab to the scene's media-safe region so its backing stays tight and avoids known subjects. Diagram labels similarly need an approved LLM node contract or exact script clauses from the local clause extractor; the Remotion renderer displays only validated labels.

## Main source map

| Responsibility | Owner |
| --- | --- |
| Desktop lifecycle and renderer bridge | `main.js`, `preload.js`, `src/` |
| HTTP contracts and pipeline coordination | `server/app.py` |
| Job queue, retry, cancellation, and checkpoint | `server/services/queue_manager.py`, `server/app.py` |
| Parallel scene queries and media resolution | `server/services/scene_query_brain.py`, `server/services/youtube_pipeline_v2.py`, `server/services/web_image/`, `server/services/real_media_service.py` |
| Niche classification and constrained style seed | `server/services/editing_profile.py`, `server/services/editing_libraries.py`, `server/services/documentary_template_system.py` |
| LLM scene and film director | `server/services/editorial_director/director.py` |
| Declarative Remotion recipe validation and grounding | `server/services/template_library.py`, `src/remotion/components/DeclarativeRecipe.jsx` |
| Local portrait segmentation with original-image fallback | `server/services/person_cutout_service.py`, `server/services/native_render_policy.py` |
| Visual-DNA genres and palettes | `server/services/editorial_director/visual_dna.py`, `server/services/editorial_director/contract.py` |
| Semantic manifest and supporting edit lanes | `server/services/semantic_edit_engine.py`, `server/services/media_shot_plan.py` |
| Grounding, timeline, and frame critique | `server/services/editorial_director/visual_critic.py`, `server/app.py::_validate_remotion_edit_plan` |
| Remotion renderer and frame composition | `server/services/remotion_renderer.py`, `src/remotion/DocumentaryComposition.jsx`, `src/remotion/components/` |

## Validation commands

Run the focused director, edit-profile, and Remotion-manifest test modules before changing renderer contracts. For visual changes, render a short mixed-scene fixture and inspect the opening, cue-reveal, hold, and exit frames as well as the complete output. The deterministic preflight does not replace visual review of the final video.
