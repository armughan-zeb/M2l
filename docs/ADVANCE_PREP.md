# Advance Preparation (prep pipeline)

Prep runs while the VM is idle: for upcoming queued titles it pre-generates
the script (ScriptIT) and pre-fetches + verifies media (web images +
YouTube clips), so when the job is dispatched the live job reuses them
instead of doing that work live.

## Title source: local queue file (no CMS peek exists)

The CMS worker API exposes exactly one job-listing call —
`GET /api/muse/agent/jobs/next` (`lib/cms.js:22`) — and it **claims** the
next job. There is no peek/list endpoint, so prep cannot learn upcoming
titles from the CMS without claiming them. Prep therefore watches a local
queue file:

- Default: `~/workspace/M2l/work/prep-queue.json` (the live worker's work
  dir). Override with `VIDZEO_PREP_QUEUE`.
- Format: `{"titles": [{"title": "...", "duration": 10, "channel": "...",
  "description": "..."}]}` — see `scripts/prep/prep-queue.example.json`.
  `duration` is minutes (defaults to 10).

Add titles there (or copy the example into place) and the runner picks
them up. If the CMS ever gains a read-only peek endpoint, the runner's
`load_prep_queue` is the one place to add it.

## Running it

```bash
# One pass over the queue (stops at the first busy signal):
VIDZEO_RENDER_GATE=off ~/workspace/M2l/vidnets/.venv/bin/python \
  scripts/prep/prep-runner.py --once

# Continuous (sleep 600s between passes):
VIDZEO_RENDER_GATE=off ~/workspace/M2l/vidnets/.venv/bin/python \
  scripts/prep/prep-runner.py --loop 600

# One ad-hoc title, web only:
VIDZEO_RENDER_GATE=off ~/workspace/M2l/vidnets/.venv/bin/python \
  scripts/prep/prep-runner.py --title "Flooded Desert" --duration 10 --skip-youtube
```

Flags: `--dry-run`, `--force` (re-prep titles already `ready`),
`--skip-web`, `--skip-youtube`. The runner uses the vidnets venv python
(the worktree has no venv of its own).

## Idle gate (never starve a live job)

`services/advance_prep.py:is_idle()` must pass before each title and is
re-checked between stages (via `should_stop`). It fails **closed** — an
unreadable signal means busy. Gates:

1. **Server queue** — `~/workspace/vidnets-runtime/server/queue.json`
   (read-only): any item with status `queued`, `processing`,
   `cancellation_requested`, `paused`, or `shutdown_timeout` blocks prep
   (`services/queue_manager.py: JobStatus`).
2. **Daemon active job** — `~/workspace/M2l/work/active-job.json` exists.
3. **Inference pool** — `~/workspace/vidnets-runtime/temp/inference-spool/pool-status.json`
   must show `pending == 0` and be fresh (< 600 s old).
4. **Render in progress** — a `render-vidnets-linux.js` / Remotion render
   wrapper process running from the live repo.

If a job starts mid-prep, the current title aborts cleanly: the manifest
is marked `aborted` and everything already written stays consistent
(all prep writes are atomic temp-file + rename).

## What prep does per title

1. **Script** — `ScriptIT` headless CLI (`scriptit/cli/generate.js`,
   flags mode, the same engine the live script adapter runs), stored in
   the prep script store. A title already prepped is reused, not
   re-generated.
2. **Scenes** — `services.scene_splitter.fallback_split_by_text(script,
   "Every Beat", duration)`: the SAME deterministic splitter the live
   audio-first pipeline uses when Whisper is unavailable
   (`services/audio_first_pipeline.py`). Scene texts are therefore
   identical between prep and a Whisper-less live job.
3. **Queries** — `services.mechanical_query_bundle.build_mechanical_query_bundle`,
   the SAME derivation the live job uses
   (`vidnets/server/app.py:_generate_mechanical_shared_scene_bundle`).
   Prep never reimplements query derivation.
4. **Web images** — the live resolver
   (`services.web_image_service.resolve_web_images_for_video_fast`) runs
   once for the title; each winning image is persisted into the existing
   historical asset cache (`TEMP_DIR/web_image_asset_cache/assets.json`)
   stamped with a `prep_key`.
5. **YouTube clips** — per scene, the live search
   (`_search_youtube_uncached`), transcript fetch
   (`YouTubeTranscriptIndex`), transcript moment match
   (`match_scene_moments`), ranged download (`_download_clip`), and
   deterministic transport/overlay screens; verified cuts go into the
   existing verified-cut cache (`put_verified_cut`).
6. **Pre-scale** — every verified file is passed through
   `services.media_prescale.ensure_scaled` (downscale-only to 1080p,
   never upscale); scaled paths are recorded in the manifest. The render
   worker wires the preference side using the same
   `<stem>.scaled-1080p.<ext>` naming.

## How the live job reuses prep (wirings)

| Prep output | Live lookup | Where |
|---|---|---|
| Script (`prep/scripts/<key>/script.txt`) | `adapters/script.js:generateScript` checks the prep store after the saved-script check, before ScriptIT; logs `prep script hit` / `prep script miss` and copies a hit into the job workDir | `lib/prepScriptStore.js` |
| Web image (`prep_key` row in `web_image_asset_cache`) | `services/web_image/resolver.py:resolve_web_images_for_video_fast` pre-fills `results[i]` from `_prep_cache_hits_for_scenes` before fanning out scene workers; hit scenes file no provider search but still pass through the live visual pass | `services/web_image/resolver.py`, `services/advance_prep.py:find_prepped_web_asset` |
| YouTube cut (verified-cut cache) | Already checked by the live pipeline before downloading: `youtube_pool_renderer.py` (`get_verified_cut`) and `youtube_pipeline_v2.py`; prep only populates it | `services/youtube_media_cache.py` |

**Cache keys** — one implementation, both sides
(`services/advance_prep.py:prep_cache_key`, imported by the resolver):
`sha256({title: normalized, narration_sha256: sha256(normalized narration),
query: normalized query})`. A prepped entry is found only for the same
(title, narration, query) triple; anything else misses gracefully and
the live job fetches as before.

**Manifest** — per title, `prep/titles/<title_key>/manifest.json`
(under `VIDZEO_PREP_ROOT`, default
`~/workspace/vidnets-runtime/temp/prep`): script path, per-scene media
entries + verification status, scaled files, timestamps, and a
`reuse_log` the live web resolver appends to on a hit. This is what the
later end-to-end test run uses to measure the reuse rate.

**Script store key** — `sha256({"duration_minutes": <float>,
"title": <normalized>})`, implemented identically in Python
(`services/advance_prep.py:title_key`) and Node
(`lib/prepScriptStore.js:titleKey`); cross-language parity is covered by
`tests/prep-script-store.test.js` with keys computed by the Python side.

## Honest limits

- **Scene texts**: prep derives scenes with the Whisper-less fallback
  splitter. If the live job gets Whisper scene texts, its scene groupings
  differ from prep's, the narration hash differs, and web cache keys
  miss — the live job then fetches live (correct, just not faster).
  Script reuse is unaffected (keyed on title + duration only).
- **Web verification in prep**: prep runs the resolver without a vision
  key, so prepped web images pass local safety/decode checks only, not
  vision. The live job's visual pass still gates every cached image
  before use — a cached image is never trusted blindly.
- **YouTube in prep**: search/download run without the authenticated
  session jar (anonymous), so age-gated videos cannot be prepped; the
  live job can still fetch them. Prep cuts pass deterministic
  quality/overlay screens only; the live job still runs its
  scene-specific visual gate on a cache hit (per the
  `youtube_media_cache` docstring).
- **Mechanical planner drift**: coordinator 1abfdcca is changing query
  derivation on its own branch. Prep calls the derivation function at
  its own base (7565264); if the planner changes query strings, older
  prepped entries miss on the query component and are simply re-fetched
  live. Keys are deterministic on the narration text, so a derivation
  change never produces a *wrong* hit.
- **Web cache lookup vs the resolver's repair pass (fixed, Worker V)**:
  the live resolver repairs/regenerates planner queries before search
  (subject prepends, forbidden-subject strips, sanitize pass), so at a
  production stage deadline the repaired strings generally differ from
  the bundle variants prep recorded keys under — the pre-fix hit rate
  was 3/8 scenes on a throwaway script (8/8 only on the 5s verbatim
  branch the old test used). The lookup now also tries each scene's raw
  planner variants (`resolver._override_raw_queries`), and a cached row
  now accumulates every variant key in `prep_keys` (the asset cache
  dedupes by source URL, so only the last variant's key survived
  before). Regression tests: `test_resolver_prep_hit_at_production_deadline`
  (8/8 at 60s) and `test_record_keeps_all_variant_keys`.
- **YouTube cut lookup is overlap-tolerant (fixed, Gap 2)**: the live
  lookup used to be exact-key only — `(video_id, start ±0.01s, duration
  ±0.01s)` — so a regrouped live scene (Whisper split) requesting the
  same source moment with a different duration always missed the prep
  cut. `get_verified_cut` now falls back, on an exact miss, to a
  same-video, same-schema (`youtube-cut-v4`) cut whose window covers the
  requested window (start within 0.5s, end at or past the requested
  end). A cached cut is never returned shorter than requested, matching
  the fresh-download contract (downloads are rejected below the
  requested duration; the render has no pad/loop step for short clips).
  Panama replay (job f66daa53, real matcher + cached transcripts, real
  33-row cache): 0/6 → 3/6. The 3 remaining misses are not key
  failures: re-matching the regrouped scene text picks a different
  transcript moment (0% window overlap), so no key rule can hit them —
  only a prep-manifest-first lookup (live scene → prep scene by text
  overlap, bypassing re-match) would reach those, and it is not built.
  Note the same replay caveat as the web lane: the live Panama run
  searched different videos entirely and hard-timed-out after 4
  attempts, so tolerance alone would not have changed that run — the
  second proof run still needs the live search to return the prep
  videos. Regression tests: `test_youtube_prep_cache_reuse.py`
  (10 tests; 3 fail on the base commit). Replay script (not committed,
  gitignored `work/`): `work/panama-yt-cache-replay.py`.
- **Named-pair grounding (known limit, not fixed)**: the live job
  rewrites queries for explicit named-pair scenes ("Denver vs Chicago")
  via `app._ground_exact_matchup_queries`, which prep does not apply
  (the function lives in `app.py`; importing the server module from
  prep would pull its whole init). Those scenes miss the web prep cache
  and fetch live — fail-soft, never a wrong hit
  (`test_named_pair_grounding_misses_fail_soft`).
- **No end-to-end proof yet**: the hit paths are unit-tested (a prepped
  web entry is found by the real resolver with the scene worker
  stubbed to fail if called; script hit/miss through the real adapter),
  but the full "prep a title, dispatch it, measure reuse" run has not
  happened — that is the later test run.
- **Ratios untouched**: Documentary 55% / Gaming 90% media ratios were
  not changed; the validate-before-deliver gate was not touched.
