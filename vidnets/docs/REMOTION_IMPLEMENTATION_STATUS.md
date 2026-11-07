# Remotion editing implementation status

Updated: 2026-10-01. Companion specification: [REMOTION_EDITING_SYSTEM_PLAN.md](REMOTION_EDITING_SYSTEM_PLAN.md).

## Release decision

**The full specification is not complete or release-certified.** The changes below implement important production contracts and recovery behavior. Real film reviews still expose aesthetic and media-selection failures. A technical pass must not be presented as proof of premium documentary editing.

## Implemented in this run

| Requirement | Connected implementation | Evidence |
|---|---|---|
| Distinct specialist graphics | Primary charts, maps, diagrams, timelines, comparisons and evidence own their canvas; supporting graphics retain their explicit role | Routing, manager, handoff and renderer contract tests |
| Complete semantic handoff | Nine manifest tracks: media, graphics, copy, camera, transitions, narration, captions, music and SFX | `production_contract.py`; final plan validation compiles them |
| Explicit geographical requirements | A spoken request to show a map cannot silently become a diagram | Production audit regression coverage |
| Short approved copy | Frame-local windows mirror renderer timing; exact approved emphasis remains intact; failed explanatory payloads become text-free media | Copy-window, fallback and typography tests |
| Diagram copy provenance | Directed diagram labels require approved LLM node copy; no description-to-label recovery | Diagram renderer and director contract |
| More capable custom compositions | Validated groups, masks, nesting, traced paths and independent image slots; compact composition designers can author meaningful paths | Recipe validation and rendering tests; preview bundle rebuilt |
| Actual-asset vision binding | Cached verdicts bind to actual file bytes, scene requirements and review policy; replaced assets require new vision | Replacement tests and policy versioning |
| Verification survives handoff | Shot timelines preserve visual decisions and digests; changed verified files block export; inherited video-frame provenance is distinguished | Shot planner tests; wildlife film's five shots carry asset-bound verification |
| Bounded creative revision | Sparse/excessive diagrams receive at most two repair rounds; repairs run for individual scenes in parallel with film context and a shared deadline | Tests and real technology director replay |
| Honest recovery | Failed revisions preserve verified media without generated copy; early plans can bind media after retrieval; fallback coverage remains visible | Failure, unsupported component and pre-retrieval reconciliation tests |
| Frame review before export | Opening, reveal, hold, exit and cue samples render before a full export; blank, missing and incomplete reviews cannot pass | Football preview caught real failures and subsequently passed |
| Longer review workloads | Review requests are not truncated to the first 64 frames; one browser is reused; bounded review time grows with sample count | Renderer implementation; long-film acceptance remains pending |
| Clean explanatory lead-ins | Verified media stays visible before a diagram's first narrated reveal | Football blank lead-in reproduced and corrected |
| Contextual media treatments | Ordinary imagery does not inherit a geographic oval mask unless explicitly requested | Shared media renderer |
| Event-aligned SFX | Actual PCM transient measurements, local event IDs, early-event source trimming and bounded gain based on source level | Audio alignment and PCM tests; production audio streams |
| One render across processes | OS-wide exclusive render lease, in addition to app serialization | Exclusion and recovery tests |
| Resource refusal | Standalone renderer refuses jobs that cannot fit the worker and memory reserve | Low-memory regression test |
| Atomic export | Render to a staging file, commit after successful nonempty completion, remove failed staging files | Fresh technology and wildlife exports used this path |
| Cancellation cleanup | Preview and export processes register with job cancellation; timeout/cancel reaps the Node/Chrome/FFmpeg process tree and removes the owned staging file | Shared cancellation helper and focused tests |
| Better source quality rules | Queries discourage lecture/homework material; vision classifies and rejects unrequested slides, worksheets and quizzes | Vision normalization and veto tests; requires fresh retrieval to assess impact |
| Stronger final review | A high-confidence visual defect is not automatically dismissed because pixel variance passes | Final QA path |
| Stable review artifacts | Portfolio harness preserves finished videos and plans under `artifacts`, outside disposable output folders | Technology and wildlife packages preserved |

## Test and real-run evidence

- Previous broader regression suite: **419 passed**. Latest selected post-fix regression suite: **332 passed**, plus **19** pixel/recovery checks after the subsequent image-slot guard. Nine-track editor controller checks also pass. These counts overlap and must not be added together.
- Football: a fresh `/api/generate` run plus several director/review iterations. The revised native export is 1920×1080, approximately 23.5 seconds, with video and audio streams. Its preview review has complete requested coverage and no low-information frames. Some scenes use explicitly degraded local media direction. This native review export is not a newly captioned, release-approved portfolio film.
- Technology/GPS: fresh `/api/generate` output, 27.528 seconds, 1080p and captions. Downloaded clips rejected by vision were excluded. Human frame inspection found lecture/homework slides; the new rejection policy was added afterward. **This video is a diagnostic artifact, not accepted final creative output.** A subsequent director-only replay with parallel repairs achieved complete LLM coverage, but reused older media and was not exported as a replacement film.
- Wildlife/octopus: fresh `/api/generate` output, 26.376 seconds, 1080p and captions. All five shots preserve actual-asset verification. Human samples show visually similar underwater footage across different shots and insufficient explanatory variety. **Technical pass; creative acceptance remains open.**

Review packages are under `artifacts/adaptive_system_20261001/football`, `technology` and `nature`. Source rights and provider limitations remain recorded by existing provenance contracts.

## Remaining work before the full plan can be marked complete

1. **Pixel-informed creative acceptance:** actual frame criticism now feeds bounded scene designers, presentation patches and a verified fresh-media recovery callback. Before/after evidence is retained and revised samples are reviewed again. Real GPS and travel reviews still reject some explanations; the loop is connected, but premium creative acceptance remains incomplete.
2. **Stronger explanations:** improve custom composition reliability so relevant media fallbacks are uncommon and physical mechanisms are illustrated meaningfully. Sparse node diagrams remain too generic in some valid plans.
3. **Perceptual repetition acceptance:** actual frame matching is implemented and caught the missed wildlife/travel repeats. Fresh-media repair now retains verified image alternates for repeated clips and avoids inherited asset identities. The travel diagnostic reached zero unresolved repeats; full portfolio acceptance remains.
4. **Media policy acceptance:** rerun affected technical topics with the new slide rejection policy and verify that retrieval finds suitable replacements rather than simply reducing coverage.
5. **Editable timeline acceptance:** all nine tracks are connected to the editor, preview, saved project and export, with trim, split, duplicate, delete, visibility, muting, locking and undo. Automated controller checks pass. A longer interactive desktop editing session still requires acceptance.
6. **Research acceptance:** a claim ledger, bounded source discovery/fetching, parallel independent source review and source-mark reconciliation are connected. A live NIST/GPS review succeeded. Complete evidence support for every arbitrary film remains unproven and is reported as pending.
7. **Specialist visual acceptance:** freshly render and inspect maps/globes, charts, comparisons, evidence, portraits and image assemblies, including transitions, hold frames, source text and captions. Component tests do not replace those renders.
8. **Full portfolio and long film:** complete the ten-niche real API acceptance matrix, then a longer multi-wave documentary, cancellation/recovery under load, listening review and complete-video inspection.
9. **Final accepted replacements:** release reports now separate technical correctness, creative review and factual support. The editor displays pending review. Failed diagnostic films still need reviewed final replacements.

## Additional wiring fixes from the final continuation

- A verified alternate no longer inherits its parent's fingerprint or video identity.
- Perceptual recovery can use verified still images even when the original scene preferred footage.
- Each standalone media shot selects its image/video renderer independently. A real recovered JPEG previously reached the video decoder; that crash is fixed.
- Actual preview failures retain their complete error log beside the saved plan.
- The pixel critic returns compact findings; a separate strict-schema designer authors geometry, requiring every approved actor and grounded semantic diagram layers.
- A real redesigned travel scene exposed a stale legacy graphic template ID overriding the selected generated recipe. The renderer now prefers the scene template ID, and revision patches synchronize both representations.
- Render inspection also caught fragmented contour holds. Paths now use their measured contour lengths and clear dash settings at the hold; actor geometry carries semantic bindings so significant overlap between distinct subjects is rejected.
- Copy contracts retain LLM provenance. Existing version-4 contracts can be revised only when their scene's LLM media decision and narration span establish provenance.
- Critics use the final visual DNA palette. Dark emphasis receives tight contrasting word backings; a rendered travel frame confirms the readability correction.
- Niche-aware mood defaults prevent arbitrary noir direction for travel/nature/culture.
- Fresh media replacements survive final QA, captions and project handoff. Editor exports use the same recovery callback and release report.
- Long-film frame review budgets scale with parallel scene waves. The main render allowance scales to a two-hour ceiling and respects an explicitly configured delivery deadline; this is a processing budget, not a guaranteed completion time.
- Plain publisher footer marks are screened by offline OCR alongside known ownership words and domains. OCR is imperfect; actual frame review remains necessary.

## Phase coverage

### Latest continuation repairs

- Timeline copy tracks, the scene registry, the text sanitizer and frame critic now share the LLM provenance requirement. Locally generated or unsupported headings are omitted.
- The critic reads the final scene palette, including intentional per-scene accent changes. Accepted verdicts are reused only for identical frame bytes and scene context.
- Measured low-information frame findings now reach the creative revision loop; they cannot be cached as accepted scenes.
- Initial composition objects use short reveals with holds instead of whole-scene drawing. Initial and repair designers can author validated closed, filled subject contours.
- Spoken compound amounts are parsed as a complete value. Regression coverage includes 120,000 and 1,500,000; inner hundreds cannot become the chosen stat.
- Anime/gameplay and spreadsheet tutorials are rejected before download when the actual story does not authorize them.
- HD range promotion now optionally requires a bounded real decode, connected to source-preserving and normalized YouTube cuts. Header-valid but damaged video stays unpromoted.
- Heavy HD download/decode concurrency is capped by available memory, with at most two workers on machines below 12 GB; cheaper search and LLM stages remain parallel.
- Latest combined focused regression suite: **179 passed**. A separate decode/YouTube suite completed **76 passed and one timing assertion failed under heavy memory pressure**; that concurrency check was changed to prove overlapping execution with a barrier, with its rerun pending. Counts overlap with earlier suites.
- The fresh ten-niche diagnostic run remains separate from accepted output. Science failed mapping (saved plan reconciliation now passes validation), football/crime/gaming failed creative review, wildlife hit low memory, travel lacked a verified asset, and finance exposed sparse frames and unrelated data imagery. Subsequent repairs need fresh-process reruns and rendered acceptance. These attempts are not premium output evidence.

| Specification phase | Current state |
|---|---|
| 1: correctness and visible failures | Core fixes implemented and regression-tested; broader live acceptance remains |
| 2: stronger direction and custom composition | Partial; repair orchestration and DSL expanded, complete editor UI and quality reliability remain |
| 3: premium specialists and audio | Partial; sound event alignment implemented, specialist visual acceptance and aesthetic improvements remain |
| 4: production validation and release | In progress; three subjects exercised, full acceptance set not completed |

An unavailable provider may still fail a bounded call. The implemented recovery must preserve successful work, produce explicit degraded status and avoid silently substituting unsupported data or empty graphics.
