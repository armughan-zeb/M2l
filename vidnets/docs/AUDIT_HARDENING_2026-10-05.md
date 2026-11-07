# Audit hardening — October 5, 2026

> Follow-up verification: the updated backend was restarted and a fresh automatic API request was attempted on 5 October 2026. It exposed additional fixes and did not complete a video. **89 regression checks passed** after the follow-up fixes. Read `artifacts/hardening_v8_validation_20261005/VERIFICATION.md` for current evidence, costs and remaining gates. The sections below describe the earlier fixing run and preserve its original verification limits.


## Status

Source fixes are implemented for the concrete defects identified in the full-system audit. Python source syntax and the Remotion browser build pass. A fresh Windows installer was built from this revision, including the final queue fixes.

**This is not a certification that every possible bug is eliminated.** The currently running backend predates these changes. Its earlier restart was rejected by automatic approval review without a stated reason; this run did not bypass that rejection. A fresh automatic generation, live authentication checks, timeout/cancellation exercises, visual review and complete audio listening remain necessary before unattended production approval.

The earlier audit remains the historical record: `docs/FULL_SYSTEM_AUDIT_2026-10-05.md`. This document records changes made afterward. Previous test counts and the manually repaired documentary are not evidence of this revision passing an automatic generation.

## Changes by audit finding

| Finding | Source changes | Remaining proof or limitation |
| --- | --- | --- |
| F01: localhost Pro bypass | Backend local access requires an explicit local/offline/BYOK mode. Packaged distribution additionally requires its explicit local-mode allowance. Default paid distribution validates the bearer identity and active entitlement. Electron sends its selected auth mode to the backend. | Confirm unauthenticated, inactive and active users against the restarted distribution. Local mode is an intentional development/BYOK mode. |
| F02: automatic crime generation failed | Generation requests accept the earlier nested config envelope explicitly; flat keys win. Sanitized effective settings are checkpointed. Primary archive search now targets niche-relevant publishers without granting unverified provenance. Grounded recovery handles unavailable media before plan validation and rendering. | A new automatic export has not been performed. Relevant stock context cannot impersonate a named person or historical event. Repeated-media rejection and material quality gates remain active. |
| F03: inconsistent emergency graphics | Shared recovery classification is used by SmartImage and the root media layer. Non-sports emergency raster decoration was removed. Supported narrated amounts and dates can become specialist explanations; fallback assets are explicitly marked degraded and not verified source footage. An existing specialist keeps ownership. | A scene without a supported fact, approved copy, suitable media or director explanation remains unresolved. No invented fact or empty plate is certified as a premium scene. |
| F04: unreachable image layouts | Editorial crop, framed print and archival polaroid now reach distinct branches. Polaroid dimensions fit the canvas, optional typography is guarded, invented archive labels are omitted, and director captions require approved copy with a short window. | Fresh rendered comparisons of all layout variants are pending. |
| F05: deadlines and late work | Direct generation, queue callbacks and checkpoint resume receive shared job identity, cancellation and wall budget. Context propagates through preparation executors and direct background threads. Director calls inherit phase deadlines. OpenRouter text, vision and TTS waits share bounded HTTP slots and caller wall limits. Render-slot waiting has a deadline and checks the acquisition boundary. Queue expiry retains retryable checkpoint semantics; API deadline errors use a retry response rather than an input-error response. | Executing Python threads and third-party sockets cannot be forcibly terminated by cancelling a future. Late provider responses are recorded; late executor results are rejected. Some source downloads may finish cache writes before their bounded operation ends. Failure-mode and shutdown verification is pending. |
| F06: numeric grounding | Metric rendering parses whole compound number phrases, including tens, hundreds and descending magnitudes, and rejects partial compound evidence. Values require their exact narration span and supported units. Decimal counters preserve the final decimal value; labels and gaps fit their columns. | Spoken fractions, ordinal figures and spoken decimal phrases are not universally supported. Unsupported numeric parsing is rejected rather than guessed. Server extraction and renderer validation are distinct implementations; cross-layer parity needs behavioral coverage. |
| F07: stale runtime/package | Both entry points carry the v8 audit-hardening marker. Status includes loaded/current Python-source fingerprints and a restart-required flag. Electron refuses to reuse a backend reporting stale sources. A new package is built after the final compile. | The old running backend was not restarted. No fresh installer installation, signing or updater publication is certified. |
| F08: ignored nested config | Public generation and queue-add boundaries validate JSON objects, nested config, schema version, positive finite budgets, script and image list types. Queue edits validate the public patch before merging it with saved settings. Internal underscore settings cannot be injected from these public requests. | This is compatible boundary validation, not a complete rejection of all unknown optional keys. |
| F09: ignored style choices | Explicit impact text is no longer erased by panel-none precedence. Texture selections now produce bounded stable paper, halftone, scanline, grain, noise, dust or vignette treatments; clean/none omit the texture. All declared bundled font faces participate in font readiness. Metric ink follows the actual dark panel/media or explanatory canvas. | Visual taste and all theme/layout combinations still require frame and full-motion review. |
| F10: early counters | Motion cues prefer exact narration phrase timing when timed words exist. Caption-word timing can be used without claiming Whisper provenance. Sequence-local offsets prevent global timestamps from shifting reveals twice. Grounded unavailable-media previews are explicit exceptions. Caption clocks support scene-local words; captions yield during active copy and explanatory graphics. | Fresh narration/counter/SFX alignment and caption continuity review are pending. |
| F11: invented legacy data | Legacy metric layouts use supplied values instead of decorative fabricated bars and dataset labels. Routes require explicit support and display a schematic relationship rather than a fabricated geographic curve. Default verification, wireless-packet and archive claims were removed. Specialist aliases include metric grids, histograms and route progress. | Arbitrary uploaded custom recipes and every compatibility path were not exhaustively exercised. |
| F12: cost attribution | Each job attempt has a local usage ledger and cost receipt. Nested workers inherit job context. Provider responses are recorded at their HTTP boundary, including late replies; duplicate client entries were removed. Receipts are serialized and atomically replaced, preserve completion status after late charges, and report unpriced calls separately. | The ledger reports captured provider charges, not an account-balance delta. Provider calls outside the job context or isolated external processes are not guaranteed attributable. Unpriced TTS calls are not treated as free. |
| F13: plaintext secret fallback | Electron uses encrypted protected stores where available. If encryption is unavailable, credentials remain in process memory and only an opaque session reference is persisted. Auth mirrors use the same protection, older plain stores are migrated when read, and writes are atomic. | An encryption-unavailable session does not persist credentials across restart. Existing legacy files not read by the app have not been inspected or scrubbed manually. |

## Important additional fixes

- Corrected recovery import placement so disabling portrait cutouts does not produce a missing-name exception or accidentally run cutout hydration.
- Empty graphic-fallback objects no longer classify genuine media as recovery graphics in the browser renderer.
- Layout inspection ignores undecoded images rather than measuring their temporary dimensions as an overflow.
- Grounded recovery dates follow their explicit cue and get a readable explanatory hold; ordinary date stamps remain brief.
- Cost receipt refreshes tolerate malformed ledger lines and avoid racing concurrent append operations.
- Queue update payload shape errors return a client error rather than crashing through a missing dictionary method.
- Queue config edits are atomic against a worker claiming the same queued item, and non-object config patches are rejected before they reach the manager.

## Main paths changed

- Desktop launch, storage and backend selection: `main.js`.
- Generation, request defaults, queue handoff and checkpoint resume: `server/app.py`.
- Shared request contract: `server/services/generation_request.py`.
- Job identity and worker propagation: `server/services/job_context.py`.
- HTTP waiting: `server/services/bounded_http.py`, OpenRouter text/TTS and vision clients.
- Render serialization and recovery hydration: `server/services/remotion_render_gate.py`, `server/services/native_render_policy.py`.
- Grounded recovery: `server/services/recovery_contract.py`, shared browser recovery helpers and missing-media rendering.
- Runtime identity: `server/services/build_info.py`.
- Billing: `server/services/openrouter_usage.py`.
- Rendering: documentary composition, scene registry, numeric evidence, motion timing, metric/timeline/image/text components, textures and legacy templates.
- Retrieval: niche primary-archive query adapter and web entity normalization.

## Verification performed in this run

1. Parsed 197 Python source modules with **zero syntax errors**. Evidence: `artifacts/full_system_audit_20261005/python-syntax.json`.
2. Checked Electron main-process JavaScript syntax successfully.
3. Compiled the final Remotion browser bundle successfully: **6,306,277 bytes**.
4. Built the Windows desktop package successfully. Installer: `dist/Vidnets v1.exe`; **661421354 bytes**. SHA-256: `fe066298fbda30db91fce0ae16af9d65b5f7d67749d9a316ecf653976f31554b`. Evidence: `artifacts/full_system_audit_20261005/build-result.json`.
5. Compared 268 packaged backend/Remotion files and three desktop archive entries with current source: **zero mismatches**. The last two queue source files were also extracted from the final installer and matched exactly.

The final installer assembly reused an identity-checked payload after updating the two late queue fixes; the audit-only build helper is preserved with the evidence. No vendor files were changed. The installer is unsigned and was not installed or executed during this run.

These are syntax/build checks. **No behavioral regression suite, paid API generation, new video render, live auth failure exercise, full listening review or clean-machine installer exercise was performed in this fixing run.**

## Release gates still open

- Load the changed backend and verify runtime/source fingerprint agreement.
- Complete one fresh, correctly shaped automatic API job with preserved per-job logs and receipts, then independent crime, sports, travel, biography and science/technology trials.
- Exercise slow providers, cancellation, shared render-slot contention and checkpoint retry.
- Inspect opening, reveal, hold and exit frames, followed by full video playback and audio listening. Verify grounding, media diversity, readability, captions and SFX synchronization.
- Install the fresh package on a clean Windows environment and confirm secure auth persistence, fonts, bundled runtimes and rendering.

External provider availability and artistic quality cannot be guaranteed by a source patch. The practical goal is bounded work, preserved checkpoints, grounded output and explicit recovery rather than concealed failures.
