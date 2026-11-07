# Video pipeline and validation inventory

This inventory describes the current script-driven Remotion path. It replaces an older failure report whose examples predated the present director, timeline, map, and visual-variation contracts.

## Production paths

| User path | Entry point | Shared stages |
| --- | --- | --- |
| Create a script video | `POST /api/generate` in `server/app.py` | Queue/job package → parallel audio, media, and director work → grounded manifest → plan and asset validation → shared Remotion render slot → Remotion master → delivery checks |
| Queue and retry | `server/services/queue_manager.py` and `server/app.py` | Saved checkpoints and edit plan → same render slot → retry or pause on recoverable failure |
| Render from Canvas Studio | `server/app.py` editor render route | Canvas manifest conversion → shared semantic manifest → shared render slot |
| Resume a saved Remotion plan | `_resume_checkpointed_remotion_render` in `server/app.py` | Reuses narration, verified media, and saved director plan; does not restart completed generation work |
| Render a saved manifest for visual QA | `scripts/render-remotion-manifest.js` | Production composition entry, bundled local fonts and audio, memory-adaptive frame workers |
| Build desktop app | `scripts/bootstrap-runtime.ps1`, `scripts/build-full.js`, `package.json` | Bundled Python dependencies, Remotion player, local backend, static assets |

The app and editor routes use a single render semaphore. Retrieval, LLM planning, speech synthesis, and vision verification may use their own concurrency, but Remotion video exports do not overlap.

## Director-to-Remotion authoring

The director receives the selected film’s script arc, niche grammar, verified media facts, current scene, neighboring narration, the live Remotion capability schema, and a creative seed. It plans scenes in parallel chunks with bounded provider calls; completed chunks survive a failed chunk, which receives a grounded local fallback. Its budget expands with the number of planning waves and is capped at one hour by default configuration.

The director can select an approved template or author a per-scene **declarative recipe**. Version 2 recipes can combine up to 16 text, shape, image, chart, map, diagram, timeline, comparison, counter, and verified-image-montage layers, each with local timing, focal point, frame shape, orientation, and motion. These are interpreted by `DeclarativeRecipe.jsx` as Remotion elements on Remotion’s frame clock. Matching generated recipes own the specialist chart, map, diagram, timeline, or comparison canvas, so the older specialist card is not also drawn over the director’s new layout.

This solves the “fixed template catalog” limit without allowing model-generated source code. The LLM composes new visual arrangements from a validated animation language; React/Remotion remains authored, typed, and safe. The existing template catalog remains available for reviewed reusable compositions.

Facts remain constrained: generated text and labels must match the scene; chart and timeline data must be spoken; diagram nodes and link evidence must be grounded; local image references cannot escape the public asset directory; maps require an enabled grounded map shot; and a media montage can only draw from selected scene media. Invalid copy, values, assets, or routes are omitted or rejected before rendering.

## Media, maps, portraits, and sound

- Media retrieval can select video, multiple images, or either form according to the beat. YouTube alternates remain subject to live visual checks, source-quality gates, and a reuse audit.
- Each scene may have timed media shots and source in-points, so longer verified sources can contribute distinct moments. The Remotion sequence resets local motion for each cut.
- Geographic scenes can use a 3D textured globe, attributed local satellite imagery when verified coordinates support the scale, or vector boundaries and explanatory shapes. Map hydration happens before render; failed satellite downloads keep the vector map.
- Person-profile scenes can request a locally generated alpha cutout. `person_cutout_service.py` uses a bounded isolated process and the cached human-segmentation model. If the model is missing, downloads too slowly, or cannot segment the image, the original verified portrait remains the render source.
- The sound director selects from a closed local SFX catalog; scene narration, music, and sound cues share the final edit clock and pass an audio-timeline audit.

## Validation surface

- Python contracts: `tests/test_director_frame_contract.py`, `tests/test_generated_recipe_dsl.py`, `tests/test_person_cutout_service.py`, `tests/test_satellite_map_service.py`, `tests/test_globe_map_service.py`, and the Remotion manifest and pipeline test modules.
- Queue, voice, and retrieval contracts: `tests/test_queue_render_serialization.py`, `tests/test_frontend_job_progress_contract.py`, `tests/test_tts_voice_catalog_ui.py`, and the YouTube, vision, and media-quality modules.
- Remotion visual fixture: `scripts/render-adaptive-documentary-smoke.js` renders opening/reveal/hold samples for maps, charts, generated recipes, diagrams, comparisons, image montage, portraits, and documents. `--video` emits a short mixed-scene video.
- Full-production render: pass a saved manifest to `scripts/render-remotion-manifest.js` and inspect its full timeline. The script applies the same component resolution, typography, sound, media shot sequencing, and transitions as the app.
- Package path: `npm run build:dir` builds the unpacked desktop application after the player bundle is rebuilt.

Unit tests verify contracts and failure handling; they do not guarantee that an LLM provider will never time out or replace review of a full rendered film. LLM calls have bounded deadlines and fallbacks, and all render stages remain serialized.
