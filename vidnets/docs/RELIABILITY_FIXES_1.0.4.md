# Vidnets 1.0.4 reliability audit

Date: 24 October 2026. Scope: the shipped Settings failure, scene-finding delays, narration, media handoff, and Windows delivery.

## Release decision

This is a local release candidate. Do not replace the public installer solely because an export completes. The audit separated playable output from editorial approval. Named-subject B-roll still needs human review, and the latest diagnostic used a disclosed graphic fallback for one rejected scene. Long-form, unattended production across all niches has not been certified by these short tests.

The lifetime-only access policy remains in force. Public downloads do not grant generation access; trials require an administrator grant. Existing paid subscribers are preserved.

## Confirmed defects and changes

| Area | Confirmed problem | Correction |
| --- | --- | --- |
| Fresh installs | Queue writes failed when the data directory did not exist; one shared temporary filename could collide. | Create parents, use unique temporary files, atomically replace the queue, and preserve the previous valid file on failure. |
| Settings startup | Account/pricing/catalog requests ran serially; stalled requests delayed usable controls. | Initialize independent controls concurrently, seed local voices immediately, and bound requests and response-body reads. |
| Saved keys | A failed migration could delete old settings before secure persistence succeeded. | Delete the old copy only after successful encrypted persistence. Show bridge/save failures. |
| Voice preview | Frontend expected a success field the API did not return and called a nonexistent persistence object. | Align response fields and use the actual settings controller. Stop previous audio before playing another preview. |
| Voices | Paid Flux models remained selectable. | Offer Edge TTS and Fish S2.1 Pro Free; normalize retired selections. Preview the selected voice from Video settings. |
| Provider waits | Catalog refresh and some retry/decode paths could consume repeated independent timeouts. | Serve cached/bundled catalogs immediately; refresh in the background. Share deadlines across speech requests, vision attempts, and frame extraction. |
| Desktop startup | Heavy Python import checks blocked Electron synchronously. | Run the probe asynchronously; keep full runtime checks in packaging. |
| Upgrade reuse | A static pipeline ID could accept a server from another build or folder. | Require the current release build ID, resource folder, and unchanged source fingerprint. |
| YouTube isolation | Job-specific tuning mutated and restored the whole process environment. | Pass a private environment to the owned worker process. |
| YouTube outages | Multiple source URLs repeated downloads during a provider-wide DNS failure. | Stop new YouTube transfers after distinct confirmed DNS failures; retain completed cuts and continue configured recovery. |
| Short YouTube tests | A two-scene job inherited a mandatory 900-second worker allowance. | Scale the allowance to scene count, preserve explicit limits, and publish actual worker progress. |
| Duplicate retrieval | Terminal web repair repeated the same unresolved query wave. | Record attempted slots and avoid an identical second terminal pass. |
| Media accuracy | Generic terminal stock could bypass pixel review for a named car. | Strict named subjects require a review bound to the exact asset bytes and requirement. |
| Subject extraction | Editorial title suffixes and leading articles polluted vehicle identities. | Prefer the explicitly narrated model over phrases such as “on the Road”; preserve genuine model variants. |
| Clip handoff | Renaming a sharp clip marked it as a cleaned derivative, causing a later caption-policy rejection. | Preserve the difference between a file rename and actual overlay processing. Carry source-overlay decisions through serialization. |
| Safety graphics | An intentionally grounded graphic could be rejected as an ordinary blank placeholder. | Validate its entity, narration, facts, and requirement hash under the explicit graphic-fallback policy. Do not count it as YouTube footage. |
| Late clip rejection | A final clip rejection could fail an otherwise completed job. | Perform one bounded, policy-controlled repair and revalidate. Explicit real-media-only requests remain strict. |
| Recovery calls | Two render recovery calls received an unsupported keyword. | Correct their signatures and add a check against the actual renderer functions. |
| Video verification | Clip response normalization discarded synthetic-content and presenter-confidence fields. | Preserve those fields, inspect every sampled frame, and reject AI-labelled named-subject evidence and presenter B-roll. Invalidate old cached verdicts. |
| Graphics | Recovery diagrams lost default links and could leave a sparse dark canvas. | Preserve grounded relationships, use a standalone background and appropriately sized labels, and retain spoken-word reveals. |
| Delivery claims | A technically completed export could claim publishing readiness without editorial review. | Return successful export separately from `ready_to_publish`; retain review warnings and degraded-media accounting. |
| Assets | Installed font/UI defaults could resolve against the writable data folder. | Resolve shipped assets against the resource folder. |
| Packaging | Changed backend files could remain stale in the cached runtime. | Mirror the changed files and verify source/package byte identity, backend startup, private-data exclusions, and a real bundled Remotion render. |

## Verification evidence

Evidence directory: `artifacts/reliability_20261024/`. It contains private local test logs and must not be uploaded wholesale with a public release.

- The final main regression suite passed **320 tests** (`release-candidate-regression.log`).
- Node checks cover UI deadlines, successful/failed key migration, voice preview persistence, render memory waits, removed RAM warnings, security literals, and backend build identity.
- Real Edge and Fish voice previews returned audio successfully (`voice-preview-results.json`).
- Final packaged Electron verification opened the window in **2.14 seconds** and saved/reloaded an encrypted test key in **44.6 ms**. The actual Settings input responded, the guest generation controls remained hidden, and the shipped voice choices contain only Edge and Fish Free. See `packaged-settings-verification.json` and `packaged-settings-screen.png`.
- Packaged production-auth checks reject anonymous and development-token calls to generation, queue start/resume, voice preview, and editor render with **401** (`packaged-auth-verification.json`). These checks do not simulate a paid billing transaction.
- The bundled runtime passes a backend startup check and an actual Remotion smoke render. Final installer and delivery-folder verification are recorded in the build log and integrity files.
- After the final backend-identity change, the focused post-freeze suite passed **25 tests**; the Node backend-identity and Settings checks passed again. This is a separate overlapping suite, not 25 additional unique tests added to the 320 above.

## Live generation tests

| Job | Result | What it established |
| --- | --- | --- |
| fde8a326 | Interrupted | Environment/process interruption; not counted as a successful run. |
| b38177ab | Exported, manually rejected | Audible 20.28-second Mixed Media car export; wrong generic cars exposed the stock identity bypass. |
| d6ff5642 | Exported, technical pass | Audible 20.793-second stock/web crime explainer. This was a generic investigative-process script, not certification of a real crime documentary. |
| aac41648 | Failed | Two YouTube downloads, one accepted scene; exposed the missing-scene YouTube fallback exclusion. |
| 1cdd97f4 | Failed | Both YouTube clips downloaded and passed relevance; exposed the renamed-clip overlay handoff bug. |
| b32ab006 | Exported, editorial review required | 9.482-second YouTube-backed export with narration and Remotion captions. Manual review found AI-labelled/presenter content in a source clip. |
| a2560bda | Exported, editorial review required | 9.352-second export after the new verifier rejected that clip; one accepted YouTube scene and one declared grounded graphic. No rejected clip was silently counted as footage. |

The b32ab006 export had H.264 video and AAC stereo audio, mean volume −15.6 dB and peak −3.8 dB. Black-frame detection found no intervals meeting its configured threshold. This is a signal check, not proof of cinematic quality or factual accuracy.

The live recheck in `clip-quality-live-review.json` correctly rejected the source labelled “AI Generated Representative Image” and identified its presenter segment. The restoration-workshop shot was still accepted as contextual automotive imagery; exact model identification and shot suitability remain editorial review tasks.

## Cost records

- b38177ab: provider-reported **$0.00561782**, 19 captured generations, no unpriced generation in its receipt.
- b32ab006: provider-reported **$0.001520155**, seven captured generations, **one unpriced generation**.
- a2560bda: provider-reported **$0.00238875**, ten captured generations, **one unpriced generation**.

These are the reported amounts in individual receipts, not an all-inclusive audit total. Failed attempts and separate live verification requests can also incur charges. Do not represent an unpriced request as free.

## Operating expectations

Normal operation uses real YouTube/web/stock media and verifies scene relevance. The configured last recovery is an accurate, disclosed motion graphic. Recovery is a safety measure and does not make missing footage count as retrieved footage. Requests, worker processes, and media phases are bounded and report progress; a network provider or a corrupt source can still be unavailable.

Low-memory rendering uses fewer workers and preserves section progress. It can take longer. Offline voice recovery is spoken narration, not a silent placeholder; selected online voice previews fail explicitly when that voice cannot be fetched.

No promise of zero failures or automatic human-editor-level quality is made by this audit. Release approval requires review of the actual current output, not just successful unit tests or a completed MP4.

## Local delivery

- Installer: `dist/Vidnets v1.exe` (alias `dist/Vidnets.exe`), Windows product/file version **1.0.4**, 645,130,161 bytes.
- Portable app: `dist/Vidnets/Start Vidnets.cmd`.
- Original editable app: `dist/Vidnets Clean Working Folder/Start Vidnets.cmd`.
- Build: `ec4b0c367bb512e6aa87c779f327319560cc88dad6d3ec26d5f10eb17c36bb97`.
- Installer SHA-256: `b4093c299abd71d3234782837f68b0035a38030f8320e9be23c6a5139226dfd9`.
- Machine-readable summary: `artifacts/reliability_20261024/QA-SUMMARY.json`.

The final a2560bda diagnostic had **zero unresolved scenes**, audible narration (mean −20.8 dB, peak −5.3 dB), Whisper/script-aligned timing for 23 words, and no black intervals meeting the signal detector threshold. It remains marked for editorial review; its contextual restoration footage and sparse graphic do not establish professional documentary quality. No public website/release deployment was performed.
