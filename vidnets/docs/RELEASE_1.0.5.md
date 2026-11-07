# Vidnets Studio 1.0.5 — release verification

## Changes

- Retains the 1.0.4 Settings responsiveness, encrypted key persistence, voice previews, free Edge/Fish engines, queue persistence, bounded provider requests, and low-memory render recovery fixes.
- Rejects footage whose own visual review cannot identify the requested named car/product. Same-industry context no longer overrides an explicitly unconfirmed model identity.
- Keeps repair/restoration tutorials out of ordinary driving/interior scene matches; those sources remain eligible for repair stories.
- Distinguishes generic processes such as anti-lock braking from named vehicle models. A process explanation should not need footage of an imaginary model with the process's name.
- Carries exact scene requirements and hash-bound graphic provenance through director serialization. Subject names are retained in recovery graphics.
- Replaces unsupported clause-to-clause diagram links with legible illustrated summaries. Genuine diagrams with explicit relationships retain their explanatory layout.
- Recovery highlights fit their words; the illustration is labelled as a summary. These graphics do not claim to be authentic photographs or model-specific technical drawings.
- Retains the rejection of AI-labelled named-subject evidence and presenter B-roll, with new review-policy cache invalidation.

## Evidence

Local records: `artifacts/release_1_0_5/`.

- `clip-quality-live-review.json`: actual Qwen review rejects the unidentified workshop car and AI-labelled presenter source that exposed the previous quality problems.
- `recovery-frame-review.json`: three frame samples, no layout failures; the revised recovery remains visible at all three sample times.
- `process-identity-tests.log`: 64 passing requirement/identity regression tests.
- `production-regression.log`: 327 passing Python regression tests. `tests/test_recovery_content.js`: four passing content/layout contract checks, including orphan-word prevention.
- `packaged-settings-verification.json`: real Electron encrypted-settings round trip, responsive Settings field, guest access gate, and Edge/Fish-only options.
- `packaged-auth-verification.json`: generation, queue operations, preview, and editor rendering reject anonymous/development tokens.
- `published-build.log`: bundled runtime identity, backend startup, private-data scan, and actual packaged Remotion smoke render.
- `generation-request.json` and `generation-submission.json`: recorded single-call Mixed Media acceptance test, followed by preserved per-video source, timing, cost, and render logs.

The test narration on anti-lock braking was checked against [Bosch's ABS explanation](https://www.bosch-mobility.com/en/solutions/braking-systems/antilock-braking-system/). Text layout was checked in rendered frames; [Remotion's layout documentation](https://www.remotion.dev/docs/api) also describes the distinction between measuring text and fitting it to a box.

## Release policy

The installer is published only after its final checksum and output checks pass. Publication is a maintenance-release decision based on recorded tests; it is not a promise of zero future bugs, perfect media availability, or automatic editorial approval for every niche. The application reports graphic recovery and review warnings rather than hiding them. Users retain review of final editorial/source choices.

Lifetime Pro remains $149 once at launch. Trials remain administrator granted. Existing paid access is preserved. Provider accounts and usage remain separate from the software purchase.

## Full video acceptance record

A 58-second ABS documentary completed through api/generate: 12/12 resolved scenes, four YouTube scenes, three web-image scenes and five declared grounded graphic scenes, with narration, aligned captions and ten sound effects. The video has AAC audio and no detected sustained black intervals. A final editor render uses the same accepted media and narration with the final recovery typography. Records and video are preserved in artifacts/release_1_0_5.

Provider-reported cost for the original successful job: $0.027197365 across 82 captured generations, with one additional unpriced generation. This is not a complete billing total. Short runs can still take time on low-memory machines; this run shared resources with installer compression. No claim of automatic editorial approval or perfect retrieval is made.


## Published release

Version 1.0.5, build `0a39c855fd198822e627036b55ef6f5cb91b979e441fff44c23fb322fda67c57`.

[Public release](https://github.com/armughan-zeb/Vidnets-Studio-Releases/releases/tag/v1.0.5-build.0a39c855fd19); [website downloads](https://vidnets.com/releases.html).

Installer: 645158525 bytes. SHA-256: `07d5306f53893539f2ff292c04016496d7c1bda9e592d9fba724621d28711ecc`. GitHub verified the uploaded digest; the public download returned HTTP 200 with the matching length. The update channel and both website pages were checked for this release.

The final 58.233-second export is `artifacts/release_1_0_5/ABS-final-1.0.5.mp4`; sampled frame review found no clipping or empty scenes. Recovery illustrations repeat in this example, and contextual hardware imagery is illustrative rather than exact technical evidence. Per-project editorial review remains necessary. See `QA-SUMMARY.json` and `public-verification.json`.
