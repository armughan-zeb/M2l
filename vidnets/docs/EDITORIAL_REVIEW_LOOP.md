# Director preview review and correction loop

Implemented October 5, 2026. Uses the existing director and configured vision provider; no additional model integration is required.

## Main pipeline

1. The director plans scene treatments using the script, film style, supported components and grounded assets.
2. Production timeline compilation chooses actual review frames.
3. Remotion renders preview frames and measures font-loaded text bounds automatically. The final video export does not run the measurement hook.
4. Parallel scene reviewers receive narration, the story arc, neighboring visual choices, niche grammar, approved copy, narration opportunities and rendered text measurements.
5. The reviewer returns a verdict and a narrow presentation patch. Unsupported evidence, copy, geometry and components are rejected.
6. Accepted design changes are compiled into the production timeline, with revised sound cues. Media repair uses a private snapshot and commits only when the bounded callback completes successfully.
7. Revised scenes are previewed again. Identical accepted pixels and context reuse a cached verdict. Changed neighboring layouts invalidate that context.
8. The final review is assessment only. A proposed or applied patch is never itself evidence of creative acceptance.

The implementation extends the existing review loop rather than creating another competing director.

## Changes

- Film niche now comes from the resolved editing profile or palette before the broad category.
- Neighbor briefs include component, variant, copy placement, role and recipe geometry. Matching adjacent explanatory treatments are review risks, not automatic failures; continuous explanations may retain their layout.
- Exact date, measurement and relationship spans identify opportunities for useful explanation. These are hints, not approved overlay copy or a requirement to fill every scene with graphics.
- Placement and oversized-background findings are no longer dismissed just because intended props describe a compact, correctly positioned title.
- Actual overflow findings cannot be accepted by an otherwise positive vision verdict.
- Complete preview frames with layout failures reach the reviewer even when the renderer returns a nonzero exit code.
- Preview retries respect the remaining phase deadline, and stale preview reports are removed before each attempt.
- Review workers use one immutable film snapshot. Slow media callbacks cannot mutate the export plan after their deadline.
- New unresolved feedback replaces stale feedback. Healthy frame verdicts remain cached by pixels and context.
- Valid chart, comparison and timeline recipes can reach their registered semantic components during reviewer-directed correction. Unsupported data still fails grounding checks.
- Review reports record sample frames, niche, neighboring treatment risks, opportunity spans, coverage and whether the LLM review actually accepted the scene set.
- Progress messages distinguish acceptance from completion with advisories.

## Controls

`creative_review_budget_seconds`: total preview/review phase, clamped to 30–300 seconds. Its automatic budget scales with scene/frame count.

`creative_revision_rounds`: 0–2 correction rounds, followed by final assessment. Default: 2.

The final assessment receives reserved time. Review/provider failures retain explicit incomplete or advisory status under the existing non-strict delivery policy. They are not silently certified as creative passes.

## Verification

- 139 targeted tests passed: review context, frame revision, production contracts, immutable timeout behavior, grounding and semantic component handoff.
- JavaScript renderer syntax check passed.
- Five actual Remotion preview frames rendered: kinetic copy, complete date, currency counter, comparison and chart. Zero measured text overflow/bounds failures; three tagged text regions supplied measured bounds.
- Live configured vision review assessed the three crime scenes with complete coverage, no unresolved scenes, and acceptance in 5.297 seconds.
- Evidence is preserved under `artifacts/editorial_review_loop`: preview manifest, frame PNGs, layout report, live review and test log.

These checks verify the new review path. They do not demonstrate a new complete documentary export, audio listening review, universal artistic quality, or acceptance across every niche. No new full video generation was run for this change.
