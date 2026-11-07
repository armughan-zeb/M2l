# Vidzeo Editor Architecture

## Canonical flow

The active Canvas Studio is `src/index.html`, `src/app-shell.js`, and `src/renderer.js`. The browser editor stores an editable Canvas project, builds a versioned manifest, validates it through `/api/editor/validate-manifest`, and submits the same project to `/api/queue/add` for rendering.

The queue callback detects `editor_manifest`, converts Canvas projects through `_canvas_project_to_semantic_manifest`, validates the semantic manifest, and sends the export through the shared Remotion render slot. Generated/script projects use the audio-first pipeline, then join the same Remotion export boundary. The Canvas editing UI remains a separate project workflow; it does not replace `EditingDirector` for generated documentaries.

## Manifest rules

Every semantic manifest contains ordered scenes with `start`, `duration`, and `end`; grounded narration; media provenance; a canonical graphic template; declared image treatment; transition timing; and bounded sound cues. Validation rejects missing narration/templates, inconsistent scene ends, graphics or cues outside scene bounds, ungrounded editorial card text, unsafe card positions, and media without rights status. It records non-fatal adjacent-template/treatment repeats as warnings. Generated version 4 manifests set `timeline_contract_version: 1` after final timing reconciliation; those plans also check absolute scene order, local shot cuts, and narration-anchored motion cues before Remotion starts. Older saved version 4 plans remain repairable without assumed missing start offsets.

Visible text is selected from exact narration, dates, numbers, quotes, or structured grounded props. The final audio-synced pass uses Whisper word timing when available and drops unmatched LLM phrases rather than estimating a misleading time.

## Registry and aliases

`server/services/editing_libraries.py` is the automatic-design registry. Browser catalogs are filtered to canonical IDs. Legacy IDs are accepted only as adapter aliases. Image treatments are normalized through `IMAGE_TREATMENT_ALIASES`; Remotion and native rendering consume the resulting canonical treatment.

The editor avoids duplicate browser entries by normalizing IDs across featured, current, and saved templates. Automatic selection uses semantic role, media requirements, safe-area rules, recent usage, and structural variation audits.

## Motion grammar

Scene motion is declared in `scene.camera.motion` and `scene.design.image_treatment`. Remotion no longer adds heavy motion based on scene index. Supported treatments include hold, editorial crop, subject slide, split mask, paper reveal, blurred stage, film gate, map window, and verified focal spotlight/iris. Stronger shake, particles, and light leaks are limited to explicit hero scenes and atmosphere layers.

## Query contract

`semantic_query_agent.normalize_agent_queries` keeps a full section subject at the front of every section query, removes conflicting global domain terms, bounds query length, and records an audit contract for anchoring, concrete terms, removed noise, and quality. Intro/outro scenes use the domain anchor. Container detection and rescue paths remain section-aware.

## Appearance and delivery

The shell persists `vidzeo_ui_theme` with light, dark, or system selection. UI appearance is separate from the video canvas palette. `vidzeo_auto_render` persists the script-bar delivery preference; enabled home generation queues a real job, while the editor Render button performs manifest preflight and submits the editable project to the same queue.
