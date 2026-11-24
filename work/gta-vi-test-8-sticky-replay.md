# Sticky prior_fatal_mismatch_rejection — offline replay on real run-8 data

Replay script: `work/sticky-fatal-replay.py` (run with the repo venv python;
reads the live ledger and run-8 manifest, never writes to either).
Code: branch `impl/sticky-fatal-mismatch` (job-scoped fatal ledger).

## Root cause (proven, not inferred)

- The file ledger `~/workspace/vidnets-runtime/temp/agent-bridge/_verdict_conflicts.json`
  had **no `job_id`, no expiry, append-only**. Two writers fed it:
  `agent_bridge._record_late_fatal_reject` (late bridge answers, 15 records)
  and `pipeline_v2_media_adapter._record_raw_fatal_verdict` (**every** raw
  fatal vision verdict in every job, 118 `adapter-fatal:` records).
- The coordinator `_seed_fatal_rejections()` loaded **all** records ever at
  the start of every `resolve_storyboard_media_v2`, keyed
  `(scene_index, video_id)` / `("requirement_hash", hash, video_id)` /
  `(None, video_id)`.
- Runs 6/7/8 share the same 265-word script, so scene indices and
  requirement hashes repeat exactly. A run-7 record therefore vetoed run 8
  before any fresh vision read, at the visual gate, the viable-selection
  defence, and the quota-retention upgrade — all returning
  `prior_fatal_mismatch_rejection`, score 0.0, verifier never called.
- Live ledger at replay time: 139 records (133 at session start; the 6-7
  added since are `web:scn_*` records timestamped 14:04–14:07 UTC Nov 19,
  written by pre-existing test suites that do not isolate the bridge dir —
  see "Ledger pollution" below).

## The two sticky vetoes, proven by hash

Run-8 scene hashes come from `GTA_VI_1Min.manifest.json` visual_requirements.

| scene | clip | vetoing record | hash match |
|---|---|---|---|
| 0 | c5qhgwwLY50 | scene-less LATE bridge record, 2026-11-19T09:09:29Z, filed during run 7 (job 6624244a, 08:59–10:05Z), req db396daf… | record hash `d4f3ea25…` == run-8 scene 0 hash `d4f3ea25…` exactly |
| 10 | MArh6jOUu4g | adapter-fatal record, 2026-11-19T09:08:41Z, filed during run 7 | key `(10, MArh6jOUu4g)` direct |

Scene 0 is the wrongful block: run 7 **accepted** the identical clip
(same md5 `33a924ae…`, same scene, same narration) at visual final 82.0,
and the vetoing record is a late verdict that contradicts that stored
accept. Scene 10's record is a genuine run-7 live verdict
(explicit_scene_conflict: police shootout shows no preorders) on the same
pixels — stale by age, but verdict-consistent.

## Replay results (real code paths, OLD seed vs NEW seed)

OLD seed keys: 123. NEW seed keys (job 62d7671a only): 25.

| scene | clip | OLD | NEW | provenance |
|---|---|---|---|---|
| 0 | c5qhgwwLY50 | VETO | — | prior-job — **STALE** (prior ACCEPT contradicts it) |
| 1 | fRmoCG4rTmM | — | — | no record (clip accepted) |
| 2 | Bhwj-_9Xz6M | — | — | no record (clip accepted) |
| 3 | 0ZY_bEuJdX4 | VETO | VETO | same-job run-8 live verdict — preserved |
| 4 | ByAS0Ak3-qA | VETO | VETO | same-job run-8 live verdict — preserved |
| 5 | ZGyKOjovCOI | VETO | — | prior-job — genuine-equivalent (run 7 live reject + run 8 live watermark_detected) |
| 6 | F39fE1AwOyc | VETO | VETO | same-job run-8 live verdict — preserved |
| 7 | 3CqKzYdk-XM | — | — | no record (live visual_rejected) |
| 9 | ZGyKOjovCOI | — | — | no record (clip accepted) |
| 10 | MArh6jOUu4g | VETO | — | prior-job — genuine-equivalent (run 7 live reject, unverified in run 8) |
| 11 | GDuSN0-brZs | VETO | VETO | same-job run-8 live verdict — preserved |
| 12 | F39fE1AwOyc | VETO | VETO | same-job run-8 live verdict — preserved |

- Scenes unblocked (clip veto removed): **3 of 13** — scenes 0, 5, 10.
- Stale/wrongful: **scene 0 only**. Genuine-equivalent (fresh read expected
  to re-reject on the same pixels): scenes 5, 10.
- Same-job vetoes preserved: **5 quota scenes** (3, 4, 6, 11, 12) — the
  vision gate still bites where vision actually looked in run 8.
- Of the 7 run-8 quota `prior_fatal_mismatch_rejection` skips, 2 were
  prior-job sticky (scenes 0, 10); 5 were same-job genuine.

## Ratio math (honest)

- Run 8 actual YT-clip ratio: **3/13 = 0.231**.
- Replay, evidence-based: **4/13 = 0.308** — scene 0 keeps its clip on its
  run-7 accept (visual 82.0, same md5); scenes 5/10 expected to re-reject.
- Replay, ceiling: **6/13 = 0.462** — only if scenes 5 and 10 also passed a
  fresh read, which no evidence supports.
- Target 80% (10.4/13) is **NOT reached by this fix alone**. The remaining
  gap is genuine live vision rejections (scenes 3, 4, 6, 7, 11, 12 clips
  rejected live in run 8 on explicit_scene_conflict / insufficient match /
  watermark), not the sticky veto.
- Real-YT ratio (clips + frames): 7/13 = 0.538 → **8/13 = 0.615**
  evidence-based.

## Frame fallback check

- A clip veto never blocks its frames: frame candidates are
  `youtube:<vid>:frame:N`, a different identifier from the clip's
  `youtube:<vid>`. Scene 0's frame:1 was never vetoed (old or new seed).
- But run-7 **frame-level** records (e.g. MArh frames 1–4, scene 10)
  DID veto those exact frames under the old seed; the fix unblocks them
  too. Same-job frame records (e.g. scene 3 0ZY frame:1, run 8 12:52Z)
  still veto within run 8.
- A scene whose clip is wrongly rejected gets its chance at the clip back
  (veto gone → primary gate / quota upgrade can take it) before frame
  fallback is needed; different clips for the same scene were never
  blocked by a (clip, scene) veto — the veto is identifier-scoped.

## What the fix does NOT do (NOT fixed)

- It does not weaken the vision gate: a live fatal verdict in the current
  job still vetoes re-presentation in that job (in-memory ledger +
  same-job file records), and the quota-retention / viable-selection
  defences still consult it.
- It does not reach the 80% gaming mix by itself (see ratio math above).
- It does not fix the remaining live vision rejections, the
  distinct-segment / same-master-cut limits, the web perfect/general
  layers, or the transcript ranged-evidence rate.
- It does not clean the live ledger: the 133+ pre-fix records (including
  pytest tmp-path pollution and the 7 `web:scn_*` records written during
  this session's broad test runs, 14:04–14:07 UTC Nov 19) stay on disk
  but are inert — with no `job_id` they never seed any job. A ledger
  hygiene pass (prune records with no `job_id`) is a separate, optional
  cleanup for the parent to approve.

## Ledger pollution note (this session)

Running the broad adjacent test suites wrote 7 `web:scn_*`
adapter-fatal records into the live ledger (14:04–14:07 UTC Nov 19),
because pre-existing tests in those suites do not set
`VIDNETS_AGENT_BRIDGE_DIR` to a tmp dir (the live ledger already
contained the same class of pollution: `/tmp/vidnets-pytest/...` paths).
No test in the new suite writes to the live ledger (all set the env var
to tmp_path). Under this fix the polluting records are inert (no job_id
→ never seeded). Left in place; not deleted.
