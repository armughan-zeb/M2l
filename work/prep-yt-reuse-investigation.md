# Prep YT-cut reuse: investigation (Worker B)

Branch: `impl/speed-prep-yt-reuse` (worktree `~/.worktrees/speed-prep-yt-reuse`),
base `31fc81a`. No production-code change — see "Verdict" below.
Regression tests: `vidnets/tests/test_prep_yt_cut_still_vision_gated.py`.

## Question

Does a live job re-verify prep-banked YouTube cuts (search / transcript /
probe / vision) instead of trusting them, the way the web lane trusts
`cached_prep` (0 fresh vision calls)?

## Finding, short version

- **Download: already trusted.** The 0e4ff6a restore works: in tests 12, 14
  and 15, 5/5 mapped prep cuts were restored from disk in 0.03–0.2 s each
  (vs ~70 s for a live download). No re-download.
- **Contact probe: does not run live.** 0 `contact_probe` rows in all three
  jobs' attempt audits.
- **Scene visual gate: re-verified, and the re-verification is load-bearing.**
  Every prep-restored clip gets a full live vision call in the coordinator's
  "Checking media relevance" phase (`_verification_result` → live verifier;
  the worker certifies restored cuts `visual_checked=False`,
  `semantic_verified=False` by design).
- **The premise is false: prep YT cuts were never vision-verified.**
  `_prep_one_youtube_cut` (advance_prep.py) is search → transcript moment
  match → ranged download → deterministic transport/overlay screens →
  `put_verified_cut(verification="deterministic"|"cache")`. Its docstring:
  "No vision here — the live job still runs its scene-specific visual gate
  on a cache hit". The GTA manifest's 7 entries are 4×`"cache"`,
  3×`"deterministic"` — none is a vision verdict. The web lane's trust is
  justified because prep runs the *same vision-gating web lane*; the YT lane
  has no equivalent. Text overlap + transcript match is not pixel proof.
- **Proof it matters — test 15, scene 0.** The prep clip `c5qhgwwLY50`
  (prep scene 1, t=0.0, 4.5 s) is byte-identical in tests 12 and 15
  (sha `da04c0510c…`, same scene, requirement hash `d4f3ea2518`). Test 12:
  live vision ACCEPTED it as a clip. Test 15: live vision REJECTED it
  (`insufficient_visible_subject_match`, `entity_conflict`, identity 30) —
  its opening frames are GTA V title cards ("SEPTEMBER 17, 2013 / GRAND
  THE…"), wrong-subject pixels for a GTA VI scene. The scene fell back to a
  frame from the same video. Trusting the prep cut would have shipped GTA V
  title cards in a GTA VI video. The same (scene, bytes) gets opposite
  verdicts across runs (frame sampling), so even banking a live PASS across
  jobs would ship the same bad pixels.
- **The time saving is illusory anyway.** The gate runs up to 12 parallel
  vision workers (`_visual_gate_workers`, default 12) over 13 scenes — about
  one wave. Removing 5 prep scenes from a 13-scene parallel wave saves ~0 s
  of wall time (the wave is set by the slowest call, not the count).
  Test-12 gate window: ~240 s for all 13 scenes; prep share ≈ 90 s of
  *parallel* work, ≈ 0 s of wall time.

## Counts (from attempt audits + checkpoints)

| job | prep restores | restore time each | live downloads (rejected) | injected, never attempted |
|-----|--------------|-------------------|---------------------------|---------------------------|
| test 12 `5137cb34` | 5/7 manifest cuts | 0.03–0.20 s | 1 × 68 s (overlay-rejected) | `TgtfearX668` (prep scene 3) |
| test 14 `25130950` att. 2 | 5/7 | 0.07–0.19 s | 1 × 52 s (overlay-rejected) | — (not in audit) |
| test 15 `9b9d79a2` | 5/7 | 0.05–0.16 s | 2 × 66 s + 49 s (overlay-rejected) | `TgtfearX668` (prep scene 3) |

7th manifest cut `Qa40EwhWAFw` (prep scene 12) maps to a scene outside the
11-scene YT quota in all three jobs — never injected, never attempted.

Final mixes: test 12 — 6 YT clips + 2 YT frames / 5 web / 0 graphics;
test 14 — 6 clips + 5 frames / 1 web + 1 rescue / 0 graphics;
test 15 — 5 clips + 6 frames / 2 web / 0 graphics.

## Verdict: no trust fix

Skipping live vision for prep YT cuts would remove the only pixel check on
those clips, and the test-15 scene-0 replay proves a text-matched,
transcript-matched prep cut can still be wrong-subject pixels. The task's
own guardrail ("never accept a wrong-subject cut") cannot be satisfied by
the recorded subject/scene match alone, and the expected saving is ~0 min
of wall time. So: **no carve-out in `_verification_result`, no synthetic
vision verdict on prep cuts, no change to the 80/55/30 targets, throttles,
budgets, or batch caps.** The regression tests pin this: a prep-restored
candidate must still reach the live verifier, and a vision rejection of one
must stand.

## What would make trust safe (follow-up, not built)

Run vision *at prep time* and bank the verdict with the cut — but a banked
PASS is only reusable if the verdict is stable for (scene, bytes), and the
test-12 PASS vs test-15 REJECT on identical inputs shows it is not (frame
sampling). A safe version would need prep-time vision over several frames
spread across the cut (e.g. 0/25/50/75/100%), all subject-passing, with the
sampled frame set recorded — a new prep capability, not a gate shortcut.

## Secondary gap found (not fixed; quality, not speed)

`TgtfearX668` (prep scene 3, "brings the series back to Vice City") was
injected into live scene 0's pool in tests 12 and 15 and never attempted in
either. In test 15, scene 0's primary prep clip was vision-rejected and the
scene fell back to a *frame* from the rejected clip while this unused,
prep-verified alternate from a different video sat on disk. Cause: vision
runs after downloads finish, so a vision rejection cannot trigger a
download of an already-restored alternate, and once a scene resolves via a
frame the YT recovery wave never runs for it. Fix direction: download
injected prep alternates eagerly (a restore costs ~0.05 s) so the
coordinator has them when the primary fails vision. Left for a follow-up:
it changes the mix (clip instead of frame), not the wall time.

## How to measure on the next proof run

- Media wall, total wall, mix (as before).
- Attempt audits: `prep_cache_restored` count should stay 5/5 mapped cuts;
  `visual_checked=False` on worker rows (worker still certifies nothing).
- Coordinator gate window ("Checking media relevance" heartbeats): expect
  no change from this work (no code change).
- If a prep clip is ever vision-rejected again, confirm the scene does not
  ship the clip — that is the guardrail working, not a failure.
