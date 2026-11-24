# GTA VI Media Fix Plan — make the media pipeline work

> **Status:** PLAN ONLY. No code changed, no commit, no restart, no queue/job/server touches.
> **Repo:** `~/workspace/M2l`, branch `codex/muse-agent-driven-reconnect`, HEAD `36853b1`.
> **Evidence base:** GTA VI 1-min job `ef306926` (8 scenes), verified from
> `~/workspace/vidnets-runtime/output/GTA_VI_1Min_20261116_070312/GTA_VI_1Min_20261116_070312_c6d37ea8/checkpoints/`
> (`media_plan.json`, `provider_web_candidates.json`, `queries.json`,
> `source_query_plan.json`) and the web audit
> `~/workspace/vidnets-runtime/temp/manifests/web_image_audit_ef306926_*.json`
> plus `server-runtime.log`. Every claim below cites `file:line`.
> All paths are repo-relative unless marked `runtime` (live `~/workspace/vidnets-runtime/.env`).

## What actually happened on GTA VI (the evidence, in one place)

| Lane | Result |
|---|---|
| Web | **0 candidates, all 8 scenes** (`provider_web_candidates.json`: `candidates: {}`; 232 provider requests, all `empty_responses`) |
| YouTube | **1 candidate per scene** (0 for scenes 1–2); **~910 s for the whole batch** (not per scene — see §2 correction); only scenes 5, 6 passed vision |
| Scene budget | Exhausted by discovery; frame fallbacks existed (same-video timestamp frames ×2, verified topic frame pool ×4) but vision on them was **skipped** (`skipped_after_scene_budget`) |
| Stock | **0 candidates, all scenes** — by policy, not by provider failure |
| Terminal fallback | `pool_media_recovery` reused already-downloaded frames/clips from the job's own pool, wrapped in the `kinetic_statement` motion-graphic template |
| Final video | 8 scenes = **2 YT clips + 1 image + 5 graphic-typed**; `source_labels` all YouTube (3 clips + 5 extracted frames); `terminal_accounting: {accepted: 2, degraded: 6}`, `pipeline_quality_status: degraded_youtube_coverage` |
| Relevance | Scene 1's accepted cut is **"Grand Theft Auto: San Andreas – The Introduction"** (`daw8xhaGj2s`); scene 4 accepted a **fan video** ("GTA 6 Most Incredible Locations Gameplay | Fan Gameplay Across Leonida") twice; scenes 5, 6, 7 are all Trailer 2 re-uploads. All 6 cut-audit accepts have `semantic_verified: false, visual_checked: false`. |

### Two premise corrections (read first — the earlier diagnosis was wrong on both)

1. **The "~910 s per scene" is one shared batch counted 8 times.** The YT lane is a
   `LazyBatchLane` (`vidnets/server/services/pipeline_v2_media_adapter.py:913-930`,
   `shared_batch = True`); the coordinator runs one discovery wave for all scenes
   (`vidnets/server/services/media_resolution_coordinator.py:4078`), and every scene's
   waiter records the same loader run (all 8 `discovery elapsed_ms ~909665–909983`,
   differing by ~318 ms). Per-scene `discover()` also gets `cap = remaining` (whole
   deadline) for shared batches (`media_resolution_coordinator.py:3618`), so the 120 s
   `VIDZEO_YOUTUBE_LANE_TIMEOUT_SECONDS` (`media_resolution_coordinator.py:3589`)
   never binds. **Measure the batch, not the scene.** Live criteria in this plan use
   batch-level numbers.
2. **The 0.30 YT coverage target did NOT bind GTA.** The code default is 0.30
   (`pipeline_v2_media_adapter.py:3250`, `_mixed_media_youtube_target_ratio()`), but
   GTA ran at **target 0.8** (runtime `.env:23`
   `VIDZEO_YOUTUBE_ACCEPTED_COVERAGE_TARGET=0.8`); the job's own `media_plan.json`
   records `youtube_coverage: {target: 0.8, requested_scene_indices: [0,1,3,4,5,6,7],
   accepted_scene_indices: [0,5,6], accepted_ratio: 0.375}`. Raising the target to
   0.90 only moves requested scenes 7 → 8 (`ceil(8×0.9)`). **The binding failure is
   acceptance/recall, not the target.** The gaming-90% work (§6) is therefore mostly
   about acceptance, recall, and the weights disconnect — not the number itself.

---

## §1. Web lane returns 0 images

### Root cause

**It is not dead engines. It is a request stampede through one egress proxy,
colliding with a timeout inversion.**

At 02:45:29–30 six scenes fired their first universal batches within 2 seconds
(4 engines each), plus 12 resolver sources per scene-query; every engine then died
on `ReadTimeout (read timeout=15)` with **wall times of 53–200 s per call** (stacked
15 s timeouts: isolated-client attempt + plain-`requests` fallback + per-host
throttle queueing), so all 232 provider requests across the 8-scene batch returned
empty. The per-query wave only waits 15 s while the universal batch needs ~37 s+
under load, so the wave abandons the universal future before it can return anything
— and the late-arriving rows are orphaned because the spool is keyed by exact query
string and no scene ever re-asks the same query. The control experiment is in the
same log: at 02:57 the identical engines, same egress, same-style queries returned
**59–60 results in ~4 s per engine when run singly.**

Evidence chain:

- **Per-provider audit** (`web_image_audit_ef306926_*_51c06f18.json`, 02:29 run,
  207.27 s elapsed): 232 requests, all `empty_responses`. Per-request latency:
  `bing_hybrid` 601 s/23 ≈ 26 s, `primary_archive` 611 s/23 ≈ 26 s, `getty_bing`
  same (all three are just `search_bing` with different query strings),
  `openverse` 477 s/23 ≈ 21 s, `universal_scraper` 552 s/15 ≈ 37 s, `ddg_hybrid`
  111 s/14 ≈ 8 s, `flickr_api` 121 s/14 ≈ 9 s, `yandex` 37 s/14 ≈ 2.6 s,
  `wikimedia`/`library_of_congress`/`google_images` 60–121 s/23 ≈ 3–5 s. Same
  pattern in the 07:10 run-0001 audit (242 requests, all empty).
- **Per-engine errors** (`server-runtime.log`, 02:48–02:49):
  `yandex/bing/flickr: failed in 58201–200749ms (ReadTimeout … (read timeout=15))`,
  batches completing `results=0 engines=0/3`. The 15 s timeout is hardcoded per
  engine in `vidnets/server/services/web_image/universal_scraper.py:398,401`
  (Yandex), `:462,465` (Bing), `:724,727` (Flickr), DDG budget 15 s at `:539`;
  each engine retries via the plain-`requests` fallback on any exception, doubling
  the burn, and `_throttled_get` (`:217`) queues behind a module-global
  2-concurrent-per-host semaphore (`:129`) with 0.5 s min spacing (`:136`) — the
  queue wait sits **outside** the 15 s timeout.
- **The stampede:** log 02:45:29–30 shows six `batch start … parallel=4
  timeout=300.0s` lines in 2 s. Fan-out per scene-query: `_resolve_scene`
  (`vidnets/server/services/web_image/resolver.py:3005`, queries truncated to 4 at
  `:3053`) → `_run_api_harvest_single` (`:3998`) submits all 12 enabled
  `_FAST_SOURCES` (`:823`) via `_enabled_fast_sources()` (`:934`) in parallel; the
  universal lane fans out 4 more engines. With 6 scene workers (runtime `.env`:
  `VIDZEO_MIXED_WEB_SCENE_WORKERS=6`), that is ~6 scenes × 12 sources +
  universal's 36 transport threads at once through one proxy. Note
  `primary_archive`/`getty_bing`/`getty_ddg` are re-skins of the same
  `search_bing`/`search_ddg` (`resolver.py:1131–1148`), so Bing/DDG hosts get
  tripled load for no new coverage.
- **The timeout inversion:** wave query deadline is 15 s (`resolver.py:4110–4126`:
  `10.0 if fast_mode else 15.0`, env `VIDZEO_WEB_QUERY_DEADLINE_SECONDS` unset in
  runtime `.env`), and the wave loop explicitly waits for the universal future
  until that deadline, then cancels it (`:4200–4290`, `universal_pending` exemption
  + `future.cancel()`). But the universal batch wall is 300 s: runtime `.env` sets
  `VIDZEO_UNIVERSAL_SCRAPER_TIMEOUT_SECONDS=300`, which `resolver.py:1284` clamps
  to 300 and passes to `UniversalScraperManager.search()`, whose `engine_timeout`
  (`universal_scraper.py:1086–1133`, deadlines `engine_timeout + index * stagger`,
  stagger 5 s at `:158`) becomes 300/305/310/315 s — **bypassing the manager
  `__init__` clamp of 20 s (`:794`)**. Engines actually finish in ~30–37 s (two
  stacked 15 s timeouts + queueing), so **every universal call is abandoned by its
  wave at 15 s and finishes ~22 s later into the void**. The retry-on-empty-timeout
  (`:1170–1190`, 1.5× timeout after 2 s backoff) doubles the background burn.
- **The gate poisons the run:** DDG hit its own interstitial at 02:45:45
  (`DuckDuckGo returned no results after 2 attempts (likely bot-gate interstitial)`,
  failed in 14–26 s) and `is_bot_gate_error` (`universal_scraper.py:754`, matches
  the engine's own guessed phrase "bot-gate") gated it for **900 s** (`:845–850`;
  HTTP refusals 60 s at `:852–860`), so all later batches ran 3 engines, not 4
  (`batch complete results=0 engines=0/3`). The gate has no re-probe/reset rule.
- **The late spool cannot save it:** `collect_late_results`
  (`universal_scraper.py:896`) and the resolver's 5 s late wait
  (`resolver.py:1305–1320`, env `VIDZEO_UNIVERSAL_LATE_WAIT_SECONDS` default 5)
  only re-check the *same exact query*; each scene's queries are unique and never
  repeated, the shared pool never warmed (`shared_batch: {query: "", candidates: 0}`),
  and the resolver's `DiscoveryCache` stores the late rows under a key nobody
  re-reads. The 5 s wait is far short of the ~22 s gap.
- **What happens on empty:** `_resolve_scene` does have second/third waves
  (broadened round, universal-only, `resolver.py:5530`; emergency subject fallback,
  full harvest per query, `:5652`), but all are bounded by the same 90 s per-scene
  deadline (`:111` hard cap 90 s; `_scene_deadline` `:125–140`). The coordinator
  records `discovery_result` with `candidate_count: 0`
  (`media_resolution_coordinator.py:4033`) and, when the status is a timeout
  handoff/error, marks the scene `web_transport_failed` and skips the web recovery
  lane for it (`:4036–4043`) — correctly, since a re-run would hit the same proxy.
  The ~262 s per-scene wall is the stacking: 90 s scene deadline (main lane) + a
  second resolver invocation (audits at 02:46: 90.51 s and 07:22: 96.5 s, both
  `deadline_reached: True`) + queueing behind 6 workers for 8 scenes (stage 207 s
  ≈ 2 scene-waves × 90 s + warm-up).
- **Queries are secondary, not the cause.** Per-scene pack comes from the planner
  rows, truncated to 4 waves (`resolver.py:3053`); gaming fallbacks in
  `vidnets/server/services/web_image/query.py` (`_GENERIC_VISUAL_FALLBACKS`,
  "official gameplay screenshot / official trailer still / official key art") are
  reasonable, though the audit shows some garbled variants ("Rockstar Games business
  grand theft documentary photograph", "Grand Theft Auto Five photograph" — the
  latter beyond the [:4] cut, so likely never ran). Near-identical queries returned
  20 rows singly, so query quality cannot explain 0/232.

**Corrections to the background lore:** (1) "Yandex/Bing/Flickr hit 15 s read
timeouts" is true but the *wall* cost is 53–200 s per engine call, not 15 s — the
timeouts stack. (2) "Only Bing + Wikipedia + RSS return real data" is **false for
this path**: Yandex/Bing/Flickr/DDG all returned 20 rows each in the 02:57 control;
what fails is *parallel* retrieval, not the engines. (3) Openverse did **not**
return rows here (23 requests, 0, ~21 s each — 504s through the proxy per
`WEB_SOURCES.md`), and Wikimedia/LOC/Google are structurally empty for a GTA VI
topic (no relevant holdings / JS shell), so 5 of the 12 "sources" were never going
to help this job; the real lanes are the 5 transports (Bing HTML, DDG, Yandex HTML,
Flickr REST, universal fan-out reusing the same 4).

### The fix

1. **Fix the timeout inversion — `resolver.py:1284` +
   `universal_scraper.py:1086–1133`.** Clamp the passed universal timeout to ≤45 s
   (runtime `.env` `VIDZEO_UNIVERSAL_SCRAPER_TIMEOUT_SECONDS=300` → 30), and make
   `search()` respect `min(timeout_seconds, self.engine_timeout_seconds)` so the
   `__init__` 20 s clamp (`:794`) can't be bypassed. Simultaneously raise the wave
   query deadline so it exceeds real engine latency:
   `VIDZEO_WEB_QUERY_DEADLINE_SECONDS` 15 → 45 (`resolver.py:4110–4126`). Either
   side alone leaves the mismatch; do both. Also drop the double-transport fallback
   per engine (isolated client → plain `requests`,
   `universal_scraper.py:398–401` etc.) or count throttle-queue time inside the
   15 s budget, so one engine call can never cost 53–200 s.
2. **De-stampede the fan-out —
   `vidnets/server/services/pipeline_v2_media_adapter.py:592`
   (`_mixed_web_scene_worker_limit`) + `resolver.py:3998–4041`.** Cut
   `VIDZEO_MIXED_WEB_SCENE_WORKERS` 6 → 2 in runtime `.env`, and enable the tiered
   wave already written at `resolver.py:4020–4041` (first wave: universal / Yandex /
   Flickr / Bing / DDG only) for the non-exhaustive path instead of all 12 sources.
   De-duplicate transports per wave: run at most one Bing-family lane
   (`bing_hybrid` / `primary_archive` / `getty_bing`) and one DDG-family lane per
   query — they share host + proxy (`resolver.py:1131–1148`).
3. **Fix the per-run gate — `universal_scraper.py:754`, `:845–860`.** "Empty after
   2 attempts" is an interstitial guess, not a definitive refusal: route it to the
   60 s refusal skip, not the 900 s bot-gate skip, and add a re-probe rule (one
   retry per gated engine per scene-wave, or halve the skip to ≤300 s). This restores
   DDG as the 4th engine instead of losing it for the whole run 15 s in.
4. **Make late results recoverable — `universal_scraper.py:896` +
   `resolver.py:1305–1320`.** Raise `VIDZEO_UNIVERSAL_LATE_WAIT_SECONDS` 5 → 20,
   and before the broadened round (`resolver.py:5530`) re-run the scene's *first*
   query as a spool harvest — or key the spool per scene rather than per exact
   query string. Currently every late row is orphaned by construction.
5. **Persist per-engine errors — `resolver.py` `SourceTelemetry` /
   `_UNIVERSAL_LAST_AUDIT` (`:1277–1370`).** The persisted audit JSON carries only
   counts + summed latency; the per-engine error strings exist only in
   `server-runtime.log`. Write the universal audit (engine, count, duration, error)
   into each scene's audit entry so the next failure is diagnosable from the
   artifact, not a log grep.

### How to verify

- **Unit/regression** (extend `vidnets/tests/test_universal_scraper.py`,
  `test_web_image_deadlines.py`): (a) invariant test — wave query deadline ≥
  universal batch wall for the configured values (would have caught 15 s vs 300 s);
  (b) gate test — DDG empty-interstitial → 60 s skip with re-probe allowed, not
  900 s; (c) late-spool test — engine finishing after the batch deadline is
  recovered by a same-query re-check *and* by the scene-level pre-broadened
  harvest; (d) stampede test — N parallel scene queries against a fake slow
  transport, asserting total concurrent provider calls stay under the new cap.
- **Live criteria** (re-run the GTA 8-scene pack through
  `resolve_web_images_for_video_fast`): web candidates >0 on ≥6/8 scenes;
  `source_broker` shows `discovered >0` for `bing_hybrid`, `ddg_hybrid`, and
  `universal_scraper`; server log shows universal batches completing `engines ≥2/4`
  with per-engine times ≤10 s; per-scene wall <120 s; audit `elapsed_seconds` well
  under the stage deadline with `deadline_reached: False`.

### Honest limits

- **Baidu** — hard-blocked, `antiFlag: 1, "Forbid spider access"`
  (`universal_scraper.py` `BaiduEngine`); dead, keep excluded. **Openverse API** —
  504s through the egress proxy; dead unless the proxy changes. **Google Images** —
  serves a JS bootstrap shell to non-JS clients → 0 rows; needs a browser/JS render,
  not a scraper tweak. **DDG universal engine** — intermittently bot-gated on this
  egress (proven 02:45:45); treat as bonus, rely on the hybrid DDG lane
  (requests-based, worked 02:59). **Pexels/Pixabay/Brave** — need API keys, forbidden
  by operator instruction; **SearXNG** needs a configured instance
  (`VIDZEO_SEARXNG_URLS`, currently unset). **The egress proxy itself** — all 5
  working transports share the single proxy; if it congests, everything degrades
  together. No new engine fixes that; only reducing concurrent load (fix 2) and
  lengthening the wave wait (fix 1) do.

---

## §2. YouTube lane is slow (~910 s batch)

### Root cause

**The wall is priced for serial downloads that don't happen, and downloads are the
sink.**

- `_mixed_youtube_download_worker_capacity()` returns **1 on hosts < 12 GB**
  (`vidnets/server/app.py:10382-10413`; VM is 7.7 GB), explicitly "reserve serial
  transfer waves".
- `mixed_youtube_time_budget()` (`pipeline_v2_media_adapter.py:51-135`) then
  computes for 8 quota scenes at coverage 1.0 (preselected,
  `pipeline_v2_media_adapter.py:4646-4650`): `required_wall = 35 + 10 + 18 + 8
  waves × 80 + 40 = 743 s`; `absolute_cap = max(900, 743) = 900`; wall = **743 s**.
- `app.py:11343-11355` then **overwrites** `yt_wall_seconds` with that cap
  (`yt_wall_seconds = mixed_hard_cap`), discarding the 480 s default and the
  delivery-budget expansion.
- On timeout at 743 s (`app.py:11503`): executor grace 18 s (`app.py:11527-11545`),
  `finally`-block grace 45 s (default since `VIDZEO_YT_POOL_DIRECT_CLIPS_ONLY` is
  unset, `app.py:11633-11645`), two disk-recovery scans (`app.py:11547-11560`,
  `11671-11690`), plus coordinator shared-batch drain up to 30–45 s
  (`media_resolution_coordinator.py:4097-4112`). 743 + ~167 ≈ **910 s**. The actual
  downloader runs 3 workers, so the 640 s of "serial waves" in the wall is ~3×
  over-budgeted; at 3 workers the honest wall is ~263 s (2 waves × 80 + 103).

**Per-stage breakdown inside the 743 s child wall** (`get_youtube_clips`,
`vidnets/server/services/youtube_pipeline_v2.py:5539` — GTA took the direct ranged
path, not pool-first: ≤12 scenes forces `pool_first_requested = False`,
`youtube_pipeline_v2.py:5813-5825`):

| Stage | Time | Evidence |
|---|---|---|
| LLM query-planner future wait | ≤ 45 s | `youtube_pipeline_v2.py:5704` (`VIDZEO_YT_V2_QUERY_FUTURE_TIMEOUT`, default 45) |
| Search | **~111 s, up to ~291 s** | `_search_container_pools`, `youtube_pipeline_v2.py:5037-5188`. Query cap 30 for 8 scenes (L5067-5077); 8 workers (L5043); 20 results/query (L5281); per-search yt-dlp socket 15 s × retries 2 (L2114-2115) ≈ 45 s worst per query. The rounds floor **overrides** the stage-budget search value: `worst_per_search × rounds + 15` = 24 × 4 + 15 = 111 s (L5120-5145), hard cap 240, ceiling 300. Plus a full anonymous retry pass up to 180 s if the first pass times out with 0 candidates (L5157-5180). |
| Transcript / timestamp rank | **≤ 20 s** | `prepare_ranged_moments`, `vidnets/server/services/youtube_ranged_moments.py:9-84`, called with `deadline_seconds=min(20, remaining)` (`youtube_pipeline_v2.py:6003-6015`); `fetch_many` workers=3, inner cap 25 s (`vidnets/server/services/youtube_transcript_index.py:352-367`). Per-video fetch tries 2 yt-dlp clients × 8 s, then `youtube_transcript_api`, then a yt-dlp fallback, with `_fetch_resilient` retries 2 + backoff 3 s/12 s (`youtube_transcript_index.py:449-478, 508-545, 594-657`). |
| Downloads | **~550 s — the sink** | Round-robin, 1 attempt/scene/pass (`_download_scene`, `youtube_pipeline_v2.py:6026`; `attempt_budget = 1`, L6083), re-queued up to `min(max_attempts=6, candidate_limit=6)` passes (L6654-6656; mixed caps set in `pipeline_v2_media_adapter.py:4651-4690`). Workers: requested 4 (`VIDZEO_MIXED_YT_DOWNLOAD_CONCURRENCY`, `pipeline_v2_media_adapter.py:4688-4692`) → memory fit `_ranged_download_worker_limit` (`youtube_pipeline_v2.py:3570-3588`) ≈ 3 on this VM. Each `_download_clip` (L3796) is bounded by `VIDZEO_YT_PER_CLIP_TIMEOUT` = **61.25 s** (from `youtube_stage_budget`, see §4) + 20 s grace (L3829-3838), with yt-dlp socket 45 s / retries 4 / fragment retries 4 (`_youtube_transport_timeout_defaults`, L3772-3795). Up to 48 transfers ÷ 3 workers of mostly-throttled ranged transfers (`download_range_func`, L3864-3892), each followed by motion screen + overlay clean + cache put (`youtube_pipeline_v2.py:6230-6340`). The 725 s hard deadline is what stops it, not completion. |
| Vision in this path | 0 s (by design) | Branch worker passes `visual_context=None` (`vidnets/server/services/youtube_branch_worker.py:70`); adapter sets `visual_understanding=False, visual_required=False` (`pipeline_v2_media_adapter.py:4695-4697`). Vision runs later, coordinator-side — see §3c. |

**Which deadline binds:** the 743 s mixed branch wall (`app.py:11503`), inside it
the stage-budget hard timeout 725 s (`vidnets/server/services/youtube_stage_budget.py:5-69`;
`app.py:11459-11467`). The **300 s transcript deadline does NOT bind on this path**
— it only binds `_prepare_pool_transcript_moments`
(`youtube_pipeline_v2.py:7114-7142`, capped `min(300, env)`), i.e. the
`build_clip_pools`/pool-first path GTA never took. The 120 s that *did* bind is the
coordinator's per-scene verification budget (`VIDZEO_SCENE_BUDGET_SECONDS`, default
120, `media_resolution_coordinator.py:3516-3523`) — see §3.

### The fix ("dead simple, fast" — mostly already in the code)

| Change | Exact touch point |
|---|---|
| **Fix the wall arithmetic (biggest win, ~743 → ~263 s).** Budget transfer waves with the real memory-fitted worker count, not 1. | `app.py:10382-10413` (`_mixed_youtube_download_worker_capacity` returns 1 on <12 GB) and `app.py:10414-10441` (`_mixed_youtube_branch_wall_cap_seconds` → `mixed_youtube_time_budget(download_workers=…)`); or pass `planned_download_workers` from `app.py:11459-11467` into the cap. |
| **Cap search honestly.** The rounds floor (111 s) overrides the stage-budget search value (65 s). Lower `VIDZEO_YT_V2_SEARCH_TIMEOUT_HARD_CAP` (240) / `…_MAX` (120), or make the floor `min(floor, search_timeout)`; cut `VIDZEO_YT_V2_QUERIES_PER_CONTAINER` (8) / total cap (30) for single-topic jobs. | `youtube_pipeline_v2.py:5120-5145` (`_search_container_pools`). |
| **Skip transcript/timestamp rank (already supported by flags).** `VIDZEO_YT_RANGED_MOMENTS=0` (`youtube_pipeline_v2.py:6003`) and `VIDZEO_YT_POOL_TRANSCRIPTS=0` (L7032); `_pick_timestamp` heuristic (L3169) is the fallback. Drop runtime `.env` `VIDZEO_YT_TRANSCRIPT_DEADLINE_SECONDS=300` → 25 (the code default, L7123). | Env only, plus `youtube_ranged_moments.py:9-84` if the 20 s cap is to shrink further. |
| **Stop retrying throttled transfers.** Distinguish transport timeout from content reject in `_download_scene` (L6026-6168) so a timed-out video isn't re-queued at another timestamp for 6 passes; cap `VIDZEO_MIXED_YT_DOWNLOAD_ATTEMPTS` (6) to 2–3 for ≤8-scene jobs. | `youtube_pipeline_v2.py:6654-6656`, `pipeline_v2_media_adapter.py:4651-4690`. |
| **Keep/return ≥3 ranked options per scene** instead of top-1-only, so the coordinator's vision + reuse logic has alternates without a second batch. | `_assign_candidates`, `youtube_pipeline_v2.py:5520-5535` (remove the `break`, return ranked list); `resolve_clip_for_scene` already consumes a pool (L7469-7560). |
| **Widen the pool for thin topics.** Raise `VIDZEO_YT_RESULTS_PER_QUERY` (20, L5281) and per-container queries for single-container jobs; relax the GTA V confusable −999 when the script explicitly allows supporting gameplay. | `youtube_pipeline_v2.py:5037-5077, 1490-1496, 741-767`. |
| **Vision after download is already the architecture** — the fix is coordinator-side: raise `VIDZEO_SCENE_BUDGET_SECONDS` (120) or start the clock when the batch lands, so frame fallbacks get vision. | `media_resolution_coordinator.py:3516-3523, 4504-4511`. |
| **Transcript reuse already exists** — don't rebuild it: SQLite cache with TTLs ready 30 d / missing 6 h / error 5 min + single-flight (`youtube_transcript_index.py:28-49, 20-24`), search disk cache 6 h (`youtube_pipeline_v2.py:2008-2054, 2181-2208`), verified-cut cache (`youtube_media_cache.py:96-198`). Just stop paying the 300 s deadline for cache misses. | Env only. |

### How to verify

- **Unit/regression** (existing suites: `vidnets/tests/test_youtube_pipeline_v2.py`,
  `test_pipeline_v2_media_adapter.py`, `test_youtube_query_normalizer.py`):
  (1) `mixed_youtube_time_budget(8 scenes, download_workers=3, coverage=1.0)`
  returns wall ≤ ~300 s (today: 743 s at workers=1); (2) `_search_container_pools`
  with a mocked slow yt-dlp returns within a 30 s timeout — the rounds floor must
  not inflate past it; (3) `_assign_candidates` on a GTA-style pool returns ≥3
  ranked options/scene; (4) `YouTubeTranscriptIndex.fetch_many` second call for
  the same ids hits cache with zero network calls.
- **Live criteria (batch-level, not per-scene):** YT batch wall < 300 s for 8
  scenes; search phase < 60 s; ≥3 distinct-video candidates/scene surviving
  metadata (from the `rank audit` line, `youtube_pipeline_v2.py:5557-5560`); ≤2
  download attempts/scene; transcript cache-hit rate reported in the pool audit
  (`build_clip_pools`, L7220-7227). Note "discovery <60 s/scene" is the wrong
  criterion — discovery is one shared batch; measure the batch.

### Honest limits (cannot be made fast on this VM)

- **Egress throttling is real and external.** yt-dlp searches crawl through the
  bot-gated proxy (socket 15 s × retries 2 per query; the anonymous-retry pass
  exists precisely because of it, `youtube_pipeline_v2.py:5157-5180`). Caching
  (search 6 h, transcripts 30 d, verified cuts 7 d) is the only durable mitigation;
  a fresh query on a cold cache will always be slow.
- **GTA VI content doesn't exist yet.** No real gameplay footage is on YouTube; the
  thin pool is a content fact, not just a code bug. Workaround: lean on web images
  (trailers/key art exist as images) and explicitly authorize GTA V supporting
  gameplay where the script allows it (`_gta_v_supporting_gameplay`, L741-767),
  plus reuse verified pool cuts from prior manifests (the `pool_media_recovery`
  path that already saved 6 scenes).
- **Transcript availability** depends on captions existing per video; no code can
  conjure them — the correct posture is the existing one (heuristic
  `_pick_timestamp` fallback, L3169), just reached faster by skipping the fetch.
- **Per-clip transfers through the proxy are slow regardless of parallelism** — the
  code itself notes "the egress proxy serializes concurrent connections"
  (`youtube_pipeline_v2.py:3570-3588`), so download workers beyond 3–4 buy little;
  the win is fewer, shorter attempts, not more workers.

---

## §3. YouTube lane is thin and partly irrelevant (1 candidate/scene, wrong clips)

### Root cause

Three stacked causes, in order:

**(a) Structural: the lane contracts to one clip per scene.** `_assign_candidates`
(`youtube_pipeline_v2.py:5362-5560`) iterates `ranked_options[:16]` but `break`s
after the first option that yields a timestamp (`assignments[idx] = clone`, L5530).
`get_youtube_clips` returns one path per scene; alternates are only tried
progressively after a failure (≤6 passes) and never surface as extra candidates.
"1 candidate/scene" is the design, not a search failure.

**(b) The pool is per-container, not per-scene, and GTA VI collapses it.**
`_group_rows` (L5011-5016) groups by `_row_pool_key` (L4995-5010);
`_representative_pool_queries` (L5018-5035) takes ≤ 8 queries/container
(`VIDZEO_YT_V2_QUERIES_PER_CONTAINER`, L5051). If GTA's 8 scenes share one
container, the whole job issues ~8 queries × 20 results, deduped by video id
(`_run_pool_search_pass`, L5295-5304), then re-ranked per scene. Then
`_score_candidate` (L1456-1900) applies −999 hard blocks (`_hard_block_reason`,
L1157-1203: Shorts, <12 s, age-restricted, captioned/subtitle packages,
fake/template terms; `copyright_high_risk_media_channel`, L1504-1509; overlay/text
package, L1510-1514) plus the lenient floor −179 / recall-pending −220
(L5476-5491) and requirement-match rejects (L5420-5450). GTA VI-specific: no real
GTA VI gameplay exists on YouTube (unreleased game) — the pool is trailers, GTA V
footage, news/analysis. GTA V footage is gated by `_is_gta_vi_text` (L714) /
`_gta_v_supporting_gameplay` (L741-767) and otherwise dies as confusable-entity
−999 (L~1490-1496). What survives is a handful of official trailers, which then
collide with `prefer_distinct_sources=True` (`youtube_pipeline_v2.py:5792-5796`),
`intro_unique` on scene 0 (L5740-5750), and 20 s timestamp-reuse gaps
(`_pick_timestamp`, L3169; L6125-6133) — scenes 1–2 get 0 because the same 1–2
videos are already assigned and nothing else clears −179.
`resolve_clip_for_scene` additionally dedupes by video id and caps plans at 4
(`youtube_pipeline_v2.py:7507-7520, 7635-7640`).

**(c) Reuse cap + late vision finish the job.** Coordinator
`_mixed_youtube_quota_reuse_cap` (`media_resolution_coordinator.py:1225-1233`):
default 2, runtime `.env` = 4 — one YT asset may fill at most 4 scenes (enforced
L6778-6799, L5763-5773 `rejected_asset_reuse_cap`). And the frame fallbacks were
skipped because the per-scene 120 s verification budget
(`media_resolution_coordinator.py:3516-3523`, `skipped_after_scene_budget` at
L4504-4511, L4720-4725) is consumed by the first candidates' vision rounds (each
inbox round-trip is minutes), so the ×2 timestamp frames / ×4 verified-topic frames
never get vision.

**Relevance guards that exist but failed on GTA:**
`visual_requirement.py:1322` `build_visual_requirement()` (GTA VI/V aliases
`:1470–1476`), per-scene `forbidden_subjects` (other containers),
`visual_intelligence/semantic_guard.py` (`explicit_scene_conflict` `:101`,
`explicit_domain_conflict` `:36`, `explicit_visual_facet_conflict` `:193`,
`exact_scene_identity_required` `:9`, wired in the adapter at `:820`, `:1251`,
`:2190`), `footage_signal_engine.py:score_candidate_master()` (`:987`), GTA V/VI
separation in `youtube_pipeline_v2.py:686–713` (`_GTA_VI_RE`/`_GTA_V_RE`,
`_scene_explicitly_requests_gta_v_comparison()` `:718`, enforced `:750`, `:2952`,
`:3126`, `:4694`), gaming negatives in `_DOMAIN_NEGATIVE_TERMS`
(streamer/live/reaction/news/leak/fanmade/mod/explained/podcast). What failed:
(i) `detect_niche_conflict()` (`universal_clip_classifier.py:358`) has **zero
callers** — dead code; (ii) `_dominant_domain_category()`
(`youtube_pipeline_v2.py:634`) only distinguishes disaster vs sports; (iii) the
vision gate was skipped on quota scenes — attempts show `visual_gate |
skipped_after_scene_budget` (scenes 1, 4, 5), and **all 6 cut-audit accepts have
`semantic_verified: false, visual_checked: false`**; (iv) the GTA V/VI guard only
blocks explicit GTA V strings, so a different-title same-franchise clip passes:
scene 1's accepted cut is "Grand Theft Auto: San Andreas – The Introduction"
(`daw8xhaGj2s`, Rockstar Games channel, metadata score 272) via the mechanical
query "Rockstar Games business grand theft documentary footage"; scene 4 accepted
the fan video "GTA 6 Most Incredible Locations Gameplay | Fan Gameplay Across
Leonida" (Five Star District) twice; scenes 5, 6, 7 are all Trailer 2 re-uploads.
Scene 8's neighbour reuse was correctly rejected
(`rejected_semantic_identity_mismatch`).

### The fix

- **Return ≥3 ranked options per scene** instead of top-1-only, so the
  coordinator's vision + reuse logic has alternates without a second batch:
  `_assign_candidates`, `youtube_pipeline_v2.py:5520-5535` (remove the `break`,
  return ranked list); `resolve_clip_for_scene` already consumes a pool (L7469-7560).
- **Widen the pool for thin topics:** raise `VIDZEO_YT_RESULTS_PER_QUERY` (20,
  L5281) and per-container queries for single-container jobs; relax the GTA V
  confusable −999 when the script explicitly allows supporting gameplay
  (`youtube_pipeline_v2.py:5037-5077, 1490-1496, 741-767`).
- **Hard exact-title gate for gaming jobs:** require `_GTA_VI_RE` (`:686`) (or the
  canonical-title regex) in candidate title/description before download; wire
  `detect_niche_conflict()` (`universal_clip_classifier.py:358`) into the ranker
  (currently zero callers). In the coordinator quota retention
  (`media_resolution_coordinator.py:6778`) require `semantic_verified` for quota
  scenes, and never count `pool_media_recovery` frames toward YT quota (adapter
  `:7327` already counts only moving sources — keep it, and stop labelling frames
  "YouTube Clips" in `source_labels`). Also fix the `missing_quota` bug:
  `pipeline_v2_media_adapter.py:7338–7345` lists scene 0 as missing although it is
  accepted (reported shortfall 5 vs warning's 4).

### How to verify

- **Unit/regression:** (a) `_assign_candidates` on a GTA-style pool returns ≥3
  ranked options/scene; (b) `detect_niche_conflict("Grand Theft Auto VI…",
  "Grand Theft Auto: San Andreas – The Introduction")` returns conflict;
  (c) quota math: 8 scenes at 0.90 → 8 requested, and multi-container reuse
  requires ≥7 distinct verified sources.
- **Live criteria (re-run GTA VI, same script, new job):** ≥7/8 scenes are
  **moving** YT clips (not frames); **0** clips whose title lacks GTA VI/6;
  **0** web images counted as YT; distinct YT sources ≥7.

---

## §4. Scene budget exhausted — discovery eats vision's share

### Root cause

**The clock is real, but it is not a discovery budget — and discovery runs under a
separate, larger wall, so verification starts with the wall already spent.**

- **Definition:** `media_resolution_coordinator.py:3510-3531` — per-scene budget,
  env `VIDZEO_SCENE_BUDGET_SECONDS`, **default 120 s**. Comment at :3510-3513
  states it "bounds review + finalists + recovery". The clock starts **lazily on
  first `scene_budget_open()` call** (:3526-3531), and `queue_review()` stamps it at
  :4014.
- **Two stacked walls, not one clock:** discovery runs under the separate
  coordinator wall `deadline_at = started_at + deadline_seconds` (:3477-3483;
  default 600 s, raised to 1200 s for `external_agent` in
  `pipeline_v2_media_adapter.py:3647-3650`). Vision's wall is derived from it:
  `visual_gate_deadline_at = deadline_at + grace` (:3800-3812, grace 45–180 s),
  then **capped by the delivery-budget allowance** (:3820-3835). In the default
  (non-adaptive) path, `queue_review()` for *all* scenes runs only **after every
  discovery wave finishes** (:4118), so verification starts with the global wall
  already spent. Then the 120 s scene clock starts — and a single external-agent
  vision round trip is ~4.5 min (stated in the code comment at :3214-3231), i.e.
  **one vision call is ~2.25× the entire per-scene budget**.
- **Where the skip happens:** parallel recovery worker returns `(False,
  "skipped_after_scene_budget", 0.0, {"accepted": False, "status":
  "deadline_exceeded"})` at :4504-4510; serial path does the same at :4720-4723;
  primary gate bails out entirely (`return None`, no verdict at all) at :3931.
  `consider_finalists` also has a `budget_exhausted` branch at :4411-4416 that logs
  `scene_budget_exhausted` and falls back to metadata-only
  `scene_budget_best_so_far` for non-strict scenes (:4439-4480) — **frame fallbacks
  never reach even that path, because they arrive via `consider_finalists` after
  the budget is already closed**.
- **Discovery-side budget has no vision reservation:**
  `youtube_stage_budget.py:24-33` caps search at 28% of the hard wall (max 120 s)
  and reserves only transfer time (45 s minimum transfer + 20 s grace, :52-65).
  Nothing in it reserves time for vision. `delivery_budget.py:allowance()`
  (:141-156) likewise has "deliberately no minimum floor" — a stage at zero gets
  zero.

### The fix

- **Split into two clocks** — a *discovery* clock and a *verification* clock:
  `media_resolution_coordinator.py:3510-3531` (`scene_budget_value` /
  `scene_budget_open`). Start the verification clock at `queue_review` (already
  :4014) but size it per provider: `external_agent` needs ≥1 inbox round trip
  (≥300 s), not 120 s; or replace wall-clock with a **call-count budget** (e.g. max
  6 vision starts/scene) bounded by the visual-gate deadline. Add a
  `vision_reserve_seconds` parameter threaded from the caller.
- **Clamp discovery to `total − vision_reserve`** so YT discovery cannot consume
  the vision share: `media_resolution_coordinator.py:3477-3483` +
  `pipeline_v2_media_adapter.py:3633-3743` (`_media_stage_deadline_seconds`); pass
  the reserve into `youtube_stage_budget.py` (`youtube_stage_budget()`, :1-69) so
  the YT hard wall shrinks by the same amount instead of reserving only transfer
  time.
- **Reserved frame-fallback allowance** — `media_resolution_coordinator.py:4504-4510,
  4720-4723` (`consider_finalists`): carve a reserved allowance — e.g. the last 2
  vision starts per scene, or a separate 60 s frame budget — checked *before* the
  general scene budget, so timestamp/topic-pool frames are always vision-checked.
  Cheapest variant: evaluate frame fallbacks through the existing batched path
  (`_run_scene_batch`, :3942-3968 — one `compare_images` call for up to 4 frames)
  instead of one call per frame.

### How to verify

- **Unit/regression** (extend `vidnets/tests/test_media_resolution_coordinator.py`):
  fake verifier that counts calls + a fake clock: drive a scene past the 120 s
  budget, then assert same-video frame fallbacks still receive exactly **1 batched**
  vision call and a recorded verdict (not `skipped_after_scene_budget`). Fake slow
  primary verifier (first call consumes the whole scene budget): assert the scene
  resolves via `scene_budget_best_so_far` **or** reserved frame vision — never via
  an unverified pool pick.
- **Live criteria on the next GTA-type job:** zero `skipped_after_scene_budget`
  entries for `same_video_timestamp_frame_fallback` / `verified_topic_frame_pool`
  stages in the per-scene attempts ledger; every scene's `visual_decision` carries
  a `reason` string plus `identity_score`/`final_score`.

---

## §5. Vision is the bottleneck — make it validator-only

### Root cause

**Vision is expensive, multi-call, and the gate is the only path to "real" — so it
absorbs the whole budget and still decides by score thresholds, not pixels.**

- **Per-call cost:** `vidnets/server/services/visual_intelligence/vision_client.py`
  — default timeout is floored at **90 s, default 120 s** (`_default_timeout`,
  ~:75-81); default **3 attempts** (`_default_attempts`, ~:84-89); `_post_json`
  retries with a larger token budget and a plain-text repair shot (~:700-740). For
  `external_agent`, timeout is raised to `VIDZEO_AGENT_MIN_WALL_SECONDS`, **default
  3600 s** (~:860-875), and `verdict_deadline = timeout × max(2, request_budget) +
  10` — so one call can nominally occupy far longer than any scene budget; only the
  gate deadlines stop it.
- **Calls per scene (worst case):** primary finalists top-3 (`_scene_finalist_limit`,
  :3198-3213; top-12 pool, :3174-3197) → 1 batched `compare_images` call per scene
  *if* the verifier offers `verify_batch` (`_run_scene_batch`, :3942-3968), else 1
  call per finalist; recovery parallel pool max 4 workers/scene (:4495-4500) over a
  2–4 finalist slice (:4370-4395); primary expansion +2–3 more (:4934-4960);
  cross-lane repeats; frame fallbacks 2 sources × 2 frames = up to 4 more
  (`_youtube_frame_fallback_candidates`, :557-665; env
  `VIDZEO_YT_FRAME_FALLBACK_SOURCES/FRAMES`, :572-586). Primary gate runs 12
  workers by default (`_visual_gate_workers`, :3214-3231) across scenes, but each
  *scene's* chain is serial through its 120 s clock.
- **What the two rejection reasons actually check** (in `_verification_result`,
  :2123+):
  - `insufficient_visible_subject_match` (:2476-2479):
    `semantic_guard.explicit_low_visual_match`
    (`visual_intelligence/semantic_guard.py:20-31`) — model-returned
    `identity_score < 35` **and** `final_score < 35`, only when
    `visual_checked/visual_available` is true. It is a **score threshold on the
    model's own numbers**, not a pixel measurement. (Pixel-level checks live
    elsewhere and are cheap/local: `media_visual_quality.py:inspect_still` —
    luminance/edge/letterbox; `perceptual_media.py:PixelSampler` — 20 s film-wide
    budget, 3 s per ffmpeg frame probe.)
  - `explicit_scene_conflict` (:2514-2524):
    `semantic_guard.explicit_scene_conflict` (`semantic_guard.py:101-203`) —
    deterministic veto on the verdict dict: `unsafe_content`, `domain_match is
    False`, `fatal_mismatch`/`entity_conflict`/`event|location|era|year_mismatch`
    flags, or reason-text markers (`wrong event/entity/domain/era`, `game-engine`,
    `video-game simulation`, …). In **relaxed** YouTube mode (default ON,
    `VIDZEO_YT_RELAXED_VISUAL_GATE=1`, :691-712) most mismatch tokens become
    advisory — **except** fatal relaxed markers including `"game-engine"` and
    `"video-game simulation"` (:159-181). For a GTA job this is a live trap: if the
    vision model writes "video-game simulation footage" in its `reason`, the
    relaxed gate still vetoes it, even though the topic *is* a video game. Check
    the logged `reason` text for scene 4 before assuming a true conflict.
  - Passes are equally thin: `visual_pass` is either branch-verdict reuse
    (:2395-2423) or a live accept; `deterministic_degraded` is a **no-vision**
    accept (:2293-2316, :2844-2869) — "media exists, is clean, has no semantic
    veto".
- **Logging:** reasons *are* recorded per scene in the attempts ledger
  (`review_scene` appends `stage: visual_gate, status: outcome[3]` at :3970-4005;
  `consider_finalists` at :4725-4735; `SceneResolution.to_dict` carries `attempts`
  + `visual_decision` + `candidate_ledger`). But a skip records only `{"accepted":
  False, "status": "deadline_exceeded"}` with **no verdict dict** — indistinguishable
  in the ledger from a failed check except by the reason string itself.

### The fix (validator-only vision)

- **Bounded, batched, explicit.** `vision_client.py` (`_default_attempts`,
  `_default_timeout`, `_post`): add a `fast_mode`/single-attempt path for
  recovery/frame candidates (attempts=1, no plain-text repair retry); the
  `fast_mode` flag already exists in `__init__` and `_post_json` — wire it through
  `_verification_result` for fallback candidates.
- **`media_resolution_coordinator.py:2123` (`_verification_result`):** keep as the
  single entry point; enforce the return contract `(accepted, reason, score,
  verdict)` with a **mandatory non-empty `reason`** (model `reason` text preserved,
  not just the code) — and for skips, record `reason: skipped_after_scene_budget`
  *plus* `visual_checked: False` explicitly so "never checked" is distinguishable
  from "rejected" in the ledger.
- **Reuse the existing cache** (`_cached_verification_result`, :1070-1117) — already
  job-scoped, live-verdicts-only — and extend `_run_scene_batch` to frame-fallback
  and topic-pool candidates so the whole fallback shortlist costs one call per
  scene.
- **Fix the gaming trap:** the `"game-engine"` / `"video-game simulation"` fatal
  relaxed markers (`semantic_guard.py:159-181`) must not veto when the scene's own
  topic is a video game — scope them to non-gaming topics (read the scene's
  `domain_category` / gaming flag from §8; if gaming, those markers are advisory,
  not fatal).

### How to verify

- **Unit/regression:** (a) fake verifier counting calls: a scene's full chain
  (primary 3 + frame fallbacks 4) costs ≤2 batched calls; (b) fast-mode path makes
  exactly 1 attempt with no repair retry; (c) a skip records `visual_checked:
  False` + reason, never a bare `deadline_exceeded`; (d) gaming scene: verdict
  with reason "video-game simulation footage" does **not** veto.
- **Live criteria:** every scene's `visual_decision` carries a `reason` string plus
  `identity_score`/`final_score`; vision calls/scene ≤2 batched calls in the
  ledger; zero `skipped_after_scene_budget` for frame fallbacks (§4).

### Risks (and mitigations)

- **Demoting vision lets wrong clips through.** The `insufficient_visible_subject_match`
  (<35/<35) and `explicit_scene_conflict` vetoes are the only things stopping a
  metadata-plausible but wrong clip (wrong game, wrong event, real-world footage
  standing in for gameplay or vice versa). The relaxed YT gate already softens most
  mismatches; the `game-engine`/`video-game simulation` markers are doing real work
  for gaming topics. **Mitigation:** keep the deterministic `explicit_scene_conflict`
  check (no model needed, `semantic_guard.py:101-203`) on *every* metadata/best-so-far
  accept; keep live vision mandatory for strict-identity scenes
  (`scene_requires_visual_verification`); restrict no-vision accepts to
  same-video/same-topic provenance (frames cut from an already-downloaded clip,
  sibling-scene verified media) — never fresh web results; and keep the
  `deterministic_degraded` label so downstream audit can tell a degraded accept from
  a vision pass.
- **The batching assumption is unproven for frames.** `compare_images` (vision_client,
  1 call for ≤4 candidates) is proven for primary finalists; using it for timestamp
  frames assumes the model can judge 4 near-identical frames from one clip
  distinctly. If it collapses them, fall back to 1 call per frame *within the
  reserved allowance*.
- **Budget growth has a real cost.** Sizing the scene clock to fit an external-agent
  round trip (~4.5 min/call) multiplies preparation time across 8 scenes unless
  batching holds; the honest trade is batched vision (1 call/scene/layer) +
  call-count caps, not an unbounded wall-clock increase. If batching fails, the
  alternative is accepting `deterministic_degraded` for frame fallbacks with the
  deterministic conflict check — a deliberate quality-for-speed trade, not a bug fix.

---

## §6. Bad queries (not scene-accurate)

### Root cause

**The LLM emitted the fields, nothing enforced them.**

Queries are written by `VidzeoBrain.plan_scenes()` / `_plan_scenes_impl()`
(`vidnets/server/services/vidzeo_brain.py:831`, `:1231`), bulk chunk size 32
(`:1254–1267`, `VIDZEO_BRAIN_SCENE_CHUNK_SIZE`), container chunk 18 (`:927`), 4
workers (`:1273`). GTA was 8 scenes = 1 chunk, external-agent, `directed 8/8,
fallback 0` (`checkpoints/source_query_plan.json`) — so the **LLM, not the
mechanical fallback**, produced these rows. The prompts demand the right thing
(`vidzeo_brain.py:338–434`, incl. `:434` "For gaming scripts, every query must
include the exact game title followed by raw gameplay or official trailer terms";
`groq_query_brain.py:33–40` gaming rules), but the validator
`_is_directed_scene_row()` (`vidzeo_brain.py:695–720`) only checks
`container_subject`/`scene_subject`/`visual_intent` non-empty plus any one query
per source. Empty `domain_category`, empty `retrieval_positive/negative_terms`,
empty `youtube_query_intents` all pass. In `queries.json` **every one of the 8
rows** has `domain_category: ""`, `retrieval_positive_terms: []`,
`retrieval_negative_terms: []`, `youtube_query_intents: []`, and
`scene_subject_type: "person"` even for Vice City, Leonida Keys and Rockstar Games.

Actual GTA queries (from `checkpoints/queries.json`):

- Sc.2: `"Vice City Grand Theft Auto VI neon streets night footage"` — no
  viewpoint/action beyond "neon streets".
- Sc.4: `"Lucia Caminos and Jason Duval Lucia Caminos Jason Duval Grand Theft Auto
  VI characters"` — name repeated 3×, zero action words.
- Sc.5: `"Leonida Grand Theft Auto VI world showcase"` — generic.
- Sc.7: `"video game official trailer gameplay Grand Theft Auto VI PlayStation 5"`
  — a **format phrase became the scene subject** (`scene_subject: "video game
  official trailer gameplay"`); Sc.5's subject is a narration fragment (`"Leonida
  revealed by the record-breaking first"`); scenes 1, 6, 8 share the same Rockstar
  Games container. Web queries are worse for a fictional world: `"Vice City Miami
  South Beach neon night photo"`, `"Leonida Keys Florida Keys aerial photo"` —
  real-Florida photos, not game footage.
- Scene 1 (verified directly): `search_query: "Rockstar Games logo studio"`,
  supporting `"Rockstar Games Grand Theft Auto VI key art official"`,
  `visual_intent: "Rockstar Games logo and studio imagery with GTA VI key art
  reveal"`, `retrieval_positive_terms: []`, `youtube_query_intents: []`.

**Where viewpoint/action words die:** `media_query_strategy.py:enrich_scene_queries()`
(called from `pipeline_v2_media_adapter.py:4371–4380`) extracts viewpoints only via
`_VIEWPOINT_PATTERNS` (`media_query_strategy.py:~95–110`, hardcoded to
`"from/inside the cab"`), and its `_VISIBLE` word list is train-centric. The rich
viewpoint lives in `visual_intent` ("Aerial and street-level shots of neon-lit Vice
City streets…") but is never a query input. Action words come only from the
mechanical path `_scene_terms_for_query()` (`vidzeo_brain.py:136–150`, first 3 long
tokens) — not used on the LLM path.
`visual_requirement.py:1497–1501` builds `action_terms` as the first 6 non-stopword
tokens ≥4 chars: GTA scene 1 got `['grand','theft','auto','most','anticipated',
'video']`, scene 5 `['trailer','arrived','2023','broke','viewing','records']`.
`confusable_entities` is `[]` on all 8 visual requirements.
(`editorial_director/director.py` writes no queries — `plan_documentary()` at
`:6002` is art direction; `media_director.py:232` `plan_media_composition()` is a
render contract.)

### The fix

1. **Query builder upgrade** — `vidzeo_brain.py:_is_directed_scene_row()` (`:695`):
   reject rows with empty `domain_category`, <3 `retrieval_positive_terms`, empty
   `youtube_query_intents`, or a `scene_subject` that equals a format phrase / has
   wrong `scene_subject_type`; reject on the fresh path in `_plan_scenes_impl()`
   (`:1231`) too, forcing mechanical re-plan. Extend
   `media_query_strategy.py:_VIEWPOINT_PATTERNS` + `_VISIBLE` with generic shot
   phrases (aerial, street-level, close-up, prison-gates, neon streets, driving,
   police chase, open world, mission) sourced from `visual_intent` + narration
   verbs, not just "from the cab". Fix `visual_requirement.py:1497–1501`
   `action_terms` to extract verbs/visible nouns instead of the first 6 tokens.
   Mechanical bundle's gaming override (`mechanical_query_bundle.py:~330–336`,
   `"{active} {facet} gameplay no commentary"`) is already correct — use it as the
   validator/fallback template. Note its gaming facet list (`:90–100`) wrongly
   includes `"leak"` — remove it (contradicts the groq negative list).
2. **Query shape contract (enforce in the validator):** every scene row must carry
   `entity` (exact title/name) + `viewpoint` (shot words from `visual_intent`) +
   `action` (verbs/visible nouns from the narration) in each of its YT and web
   queries; `youtube_query_intents` non-empty; `domain_category` set (e.g.
   `"gaming"`). A row that fails the contract is re-planned mechanically, never
   shipped with empty fields.

### How to verify

- **Unit/regression:** (a) `_is_directed_scene_row` rejects the GTA scene-7 row
  (empty `domain_category`/intents, format-phrase subject); (b)
  `enrich_scene_queries` adds "aerial"/"prison gates" from narration +
  `visual_intent`; (c) `action_terms` for GTA scene 1 contains verbs/visible nouns,
  not `['grand','theft','auto','most','anticipated','video']`.
- **Live criteria (re-run GTA VI, same script, new job):** `queries.json` rows have
  `domain_category="gaming"`, non-empty `retrieval_positive_terms` and
  `youtube_query_intents`, and each YT query contains entity + viewpoint/action
  words.

---

## §7. Fallback chain gives up on real media too early + stock empty

### Root cause

**The chain order is fine; the re-verification step is missing — and stock is
empty by policy, while the coordinator still pays for a stock window.**

Ordered chain in `resolve_storyboard_media_v2`
(`media_resolution_coordinator.py`):

| # | Step | Location |
|---|---|---|
| 1 | Primary YT+web discovery waves; rank top-12, vision top-3 | :4055-4143, :4007-4025 |
| 2 | Rejected-quota YT content recovery | :4875-4883 |
| 3 | Primary-finalist expansion (retained top-12, +2–3) | :4934-4960 |
| 4 | Cross-lane fallback (YT↔web) | :4941-4953 |
| 5 | Spare-pool reuse, tier (a) — **duplicated block, runs twice** | :4971, :4991 |
| 6 | Same-video timestamp frames (2 sources × 2 frames) | :5007-5030 |
| 7 | Debugger-advance on-disk reuse (watchdog-flagged scenes only) | :5064-5098 |
| 8 | Watermark drop of accepted finalist | :5104 |
| 9 | Verified-topic web reuse | :5608-5652 |
| 10 | Verified-topic frame pool (sibling-scene YT clips → frames) | :5665-5730 |
| 11 | Nearest-verified-neighbour reuse | :5753-5813 |
| 12 | Graphic/neutral placeholder row assigned (**no path yet** if `stock_lane` exists) | :5818-5880 |
| 13 | Pre-stock web-image rescue | :7068-7322 |
| 14 | Stock last resort (parallel, ≤4 workers) | :7328-7460 |
| 15 | **`pool_media_recovery`** — terminal | :7465-7550 |
| 16 | Graphic factory fills remaining empty slots | :7551-7576 |

- **The defect is at steps 6/10 → 15, not the order:** frame candidates are *built*
  locally (ffmpeg extract, cheap) and then handed to the same `consider_finalists`
  that enforces the scene budget — so they are constructed and then vision-skipped.
  The chain never re-verifies them.
- **`pool_media_recovery` (:7465-7550) performs no vision at all.** It only excludes
  candidates whose stored verdict has `unsafe_content`/`fatal_mismatch` (:7477-7480)
  and checks `_strict_reuse_identity_compatible` (:7495-7500); it then stamps
  `resolution_kind = "pool_media_recovery"`, `verification_status =
  "deterministic_degraded"`, `status = "degraded"` (:7536-7541). The recovered
  media is therefore *real* but **never vision-checked for this scene** — and because
  `status` is `degraded`, it renders under the scene's graphic template
  (`fallback_template` defaults to `kinetic_statement`,
  `visual_requirement.py:1228`; template mapped in
  `semantic_edit_engine.py:468,1909`). That is exactly the GTA symptom: recovered
  YT media under a graphic (scenes 0, 3) and graphic-led scenes (1, 2, 4).
- **Late-gate alternate exists but is downstream and vision-free:**
  `_try_late_gate_alternate` (`pipeline_v2_media_adapter.py:7868-7990`) only runs
  inside `validate_media_plan_for_render_v2` after a late rejection, iterates the
  scene's retained `alternates` (max 8), validates decodable + ownership-clean +
  real-hash fingerprint — but **stamps `accepted=True, visual_checked=True`
  unconditionally** (:7951-7952) and trusts the stored decision. It never calls
  live vision. It also cannot help scenes that reached `pool_media_recovery`,
  because those were never late-gate-rejected.
- **Stock empty — by policy, not by provider failure:**
  `pipeline_v2_media_adapter.py:6816-6845` (`guarded_stock_lane`): if
  `subject_type`/`domain_category` is gaming **and** the title contains GTA/game
  terms, it `return None` **before any provider call** — "Real-world stock cannot
  depict a named game's world." On GTA VI, stock returning 0 candidates is the
  *intended* outcome of that guard. The coordinator side still opens a stock window
  for degraded scenes (:7328-7350: up to 100 s/wave, ≤4 workers) and
  `_final_stock_resolution` (:5858+) requires `stock_allowed`/
  `stock_last_resort_allowed` and then applies `_stock_terminal_metadata_gate`
  (:1990-2102, no-vision deterministic gate — named/event subjects need explicit
  metadata) plus live `_verification_result` (:6117-6118). So the window is spent
  to learn what the adapter already knew. Providers, for completeness: with **no
  API keys**, both fall back to HTML scraping — `sources/pexels_source.py`
  (`_scrape`, 7 s timeout, semaphore 2; API only `if self.api_key`) and
  `sources/pixabay_source.py` (`_scrape_next_data`/`_scrape_html`, 12 s timeout).
  Scraped stock is real-world photo/video libraries; it cannot depict game-engine
  footage anyway, and the metadata gate would reject it for a named game.

### The fix

- **Re-verification before pool recovery / graphic** —
  `media_resolution_coordinator.py:7465-7550` (`pool_media_recovery`): insert a
  re-verification step between :7550 and :7551 — one batched vision pass over the
  recovered selections (same `_run_scene_batch` mechanism from §5); a recovered
  asset that fails `explicit_scene_conflict` stays out, one that passes is
  re-stamped `verified_reuse` instead of `deterministic_degraded`, and only a
  vision-unavailable/timeout case keeps the degraded stamp. This closes the exact
  GTA hole (recovered media never checked for its scene).
- **Frame fallbacks:** move the scene-budget check *out* of the frame-fallback path
  (see §4) rather than moving the steps — their order is already correct (frames
  before stock/graphic). No reorder needed except: de-duplicate the spare-pool block
  (:4971 vs :4991, identical code running twice).
- **Stock:** hoist the gaming guard into the coordinator so degraded gaming scenes
  skip the stock window entirely instead of opening it and getting `None` back
  (`media_resolution_coordinator.py:5858` (`_final_stock_resolution`) /
  `pipeline_v2_media_adapter.py:6816`); log `stock_skipped_gaming_topic` in the
  ledger so "0 candidates" reads as policy, not failure. Stock should not run for
  gaming topics — the adapter guard is correct; the waste is that the coordinator
  doesn't know it earlier.
- **Late gate:** `_try_late_gate_alternate` (`pipeline_v2_media_adapter.py:7868`):
  do **not** fabricate `visual_checked=True` (:7951-7952) for alternates whose
  stored decision was `deterministic_degraded`/unverified — require a stored live
  verdict or route the alternate through the batched vision pass.

### How to verify

- **Unit/regression** (extend `vidnets/tests/test_media_resolution_coordinator.py`;
  stock policy already has `vidnets/tests/test_stock_last_resort_policy.py` and
  `test_visual_gate_windows_and_stock_fixes.py`): (a) pool-recovery test — seed a
  job pool with a YT clip whose stored verdict is clean, force a graphic-bound
  scene through `pool_media_recovery`, assert the result carries a vision verdict
  for *this* scene (or an explicit `vision_unavailable` reason), never a bare
  `deterministic_degraded` with no reason; (b) gaming-stock test — GTA-typed scene
  asserts `guarded_stock_lane` returns `None` *and* no stock window is opened
  (ledger contains `stock_skipped_gaming_topic`); (c) spare-pool dedupe test —
  tier (a) runs once.
- **Live criteria:** graphic-led scenes (`resolution_kind == graphic_fallback` with
  a path) == 0 unless pool + stock are both genuinely empty *and* the ledger shows
  the frame fallbacks were vision-rejected (not skipped); `pool_media_recovery`
  rows carry a vision verdict or `vision_unavailable`, never bare
  `deterministic_degraded`.

---

## §8. Gaming rule: 90% YouTube clips

### How gaming is detected today (no new detector needed)

Detection exists in 4 places, but there is **no `is_gaming` flag on config and no
mode switch** that sets a 0.90 target, lane order, or budgets:

- `mixed_media_planner.py:classify_mixed_media_topic()` (`:595`, explicit category
  authoritative, else `_GAMING_TOPIC_TERMS` at `:297` incl. `gta`, `grand theft
  auto`, `gta vi`), `_gta_vi_story()` (`:427–428`, `_GTA_VI_POLICY_RE` `:227`),
  `resolve_media_policy()` (`:448`, forces `niche="gaming"` for GTA VI).
- `universal_clip_classifier.py:detect_niche()` (`:401`, gaming anchors incl.
  `grand theft auto`, `gta vi`, `rockstar games`, `vice city` → `gaming, 20`).
- `vidzeo_brain.py:_fallback_retrieval_context()` (`:85–117`, gaming by terms,
  mechanical path only).
- `pipeline_v2_media_adapter.py:1529–1530` (`game_scene` regex) and `:6832–6835`
  (`is_game_title` stock block).

### What changes in gaming mode (exact knobs)

| Knob | Now | Gaming mode | Location |
|---|---|---|---|
| YT coverage target | 0.30 default (0.8 via runtime `.env:23`) | **0.90** | `pipeline_v2_media_adapter.py:3200–3250` (`_mixed_media_youtube_target_ratio()`); single choke point `app.py:_resolve_mixed_media_niche_policy()` (`:4508`) |
| Weights into quota math | Gaming weights (YT 0.80) computed but **never copied** into `config['mixed_media_weights']` — the only weights source the ratio function reads | Copy `policy['weights']` into `config['mixed_media_weights']` when `policy['niche']=='gaming'` | `app.py:4508–4600` (stores policy only in `config['_niche_media_policy']`, copies just retrieval knobs via `setdefault`); `mixed_media_planner.py:106–122` (`_media_share(.15, .80, .05)`), `:664` (`_raise_automatic_youtube_floor()`, YT cap 0.85) |
| Lane order | YT-first already (`media_source_priority: ['youtube','web','stock']`; YT primary lane `pipeline_v2_media_adapter.py:~4588–4640`, `lane_config["youtube_accepted_coverage_target"] = 1.0` at `:4639`) | YT primary for **all** scenes; web rescue-only — gate `web_indices_ref` (`:5928`) on YT quota misses | `pipeline_v2_media_adapter.py:5928`, `:5747+` (cross-lane retry) |
| Download workers in wall | Wall call hardcodes `download_workers=1` (`:3494–3495`) | Pass `download_workers=2` | `pipeline_v2_media_adapter.py:3494–3495` |
| Transcript deadline | Effective **18 s** for mixed jobs (runtime `.env:30` = 300 s is **shadowed** by `app.py:10845–10848` → `config.yt_transcript_deadline_seconds` or `VIDZEO_MIXED_YT_TRANSCRIPT_DEADLINE_SECONDS` or `"18"`; `youtube_pipeline_v2.py:7117–7127` clamps `min(300, env default 25)`; pool pre-probe 60 s, `youtube_pool_renderer.py:2490`) | Gaming transcript deadline **120 s** via `app.py:10845` (`VIDZEO_MIXED_YT_TRANSCRIPT_DEADLINE_SECONDS`) | `app.py:10845–10848` |
| YT download candidate limit | GTA got **6** (not the gaming 10) because `setdefault` kept a pre-existing value | Enforce gaming `yt_download_candidate_limit=10` explicitly in the niche-policy application | `mixed_media_planner.py:146–153` (gaming retrieval policy: web DL 18, YT cand 10, YT attempts 6, clip max 7.0 s, still 2.8 s, beat 2.6 s) |
| Gaming detection + switch | None | Add `gaming_mode_for()` in `mixed_media_planner.py` reusing `_gta_vi_story()` (`:427`) + `classify_mixed_media_topic()` (`:595`); call it from `app.py:_resolve_mixed_media_niche_policy()` and store `config['_gaming_mode']=True`; adapter reads that flag in `_mixed_media_youtube_target_ratio()` and `_source_lane_policy()` (`:3100+`) | `mixed_media_planner.py`, `app.py:4508`, `pipeline_v2_media_adapter.py:3200, 3100+` |

Other current values the mode must respect (do not change blindly): web pool /
vision pool — `web_image/resolver.py:2211,3058` (12/query);
`media_resolution_coordinator.py:3174` top-12, `:3198` finalists 3, `:3214` vision
workers 12. YT search — `youtube_pipeline_v2.py:2075` max_results 12; `:5281`
results/query 20; `:5051` queries/container 8; `:7040–7041` source shortlist 4 +
reserves 2; `:5992` candidate limit clamp 1–12 (GTA config: 6). YT reuse cap —
`media_resolution_coordinator.py:1225–1235` (env default 2; runtime `.env:31` = 4);
effective cap `:6778`; render-level YT video cap 1 per identical bytes
(`media_asset_policy.py:52`); GTA config `max_final_video_reuse_per_clip=1`. Web
reuse cap — `media_resolution_coordinator.py:5035` (env default 1; runtime `.env:32`
= 2); `pipeline_v2_media_adapter.py:9076` max 2. YT time budget —
`pipeline_v2_media_adapter.py:51–120`; GTA effective branch wall 663 s
(= 35+10+18+7×80+40). Ranged download workers —
`youtube_pipeline_v2.py:3590–3608` requested default 6 cap 8; `:3570–3588` memory
fit: 7.7 GB host → `fit=(avail−256)//320 ≈ 3`, total <12 GB → `min(4, fit)` ≈ **3**.
**Anomaly to check while in there:** `media_retrieval_settings` records
`download_workers: 0` — audit at `:6888–6889`, floor is 1. Scene timing —
`media_plan.json` `scene_timing_windows`: 7.15–12.72 s/scene, 78.9 s total, 2368
frames; clip max 7.0 s ⇒ ≥2 clips or clip+frames for 12.72 s scenes; ~12 clip
segments for full YT coverage.

### What breaks at 90% YT (quantified, with mitigations)

- **Reuse cap:** arithmetic minimum is `ceil(8/4)=2` distinct clips, but the
  multi-container reuse gate (`media_resolution_coordinator.py:6778+`: same
  `container_id` + `_strict_reuse_identity_compatible` `:219` +
  `_semantic_reuse_compatible` `:160` + distinct vision-bound timestamp +
  non-adjacent) plus render-level cap 1 (`media_asset_policy.py:52`) mean
  realistically **7–8 distinct verified clips** for 8 distinct containers. GTA's
  pool had only 4 distinct sources (San Andreas, fan gameplay, 2× Trailer 2).
  **Mitigation:** raise source shortlist 4→8 (`youtube_pipeline_v2.py:7040`),
  queries/container 8→12 (`:5051`), enforce candidate limit 10; add a
  distinct-source floor that downgrades the 90% claim (not the render —
  render-never-stops still holds; 0-real still hard-fails) with web rescue for the
  remainder.
- **Transcript deadline:** effective 18 s for mixed jobs risks heuristic timestamps
  for 8 scenes. **Mitigation:** 120 s gaming transcript deadline; pool pre-probe
  already 60 s.
- **Download parallelism:** ~3 effective ranged workers on this VM; 8 scenes × 6
  attempts = up to 48 downloads; branch wall grows 663 s → 743 s at quota 8 with
  workers=1 (`mixed_youtube_time_budget` formula), 423 s at workers=2.
  **Mitigation:** pass `download_workers=2` into the wall call; investigate the
  recorded `download_workers: 0` anomaly.
- **Section timing:** 7 s clip cap vs 7.15–12.72 s scenes ⇒ ~12 clip segments for
  79 s; vision load 8 scenes × 3 finalists = 24 calls minimum (budget was 56, 6
  used — headroom exists, but each inbox round trip is ~4.5 min, so keep
  `_visual_gate_workers=12`). **Mitigation:** chain 2 timestamp moments per long
  scene from one verified source.
- **Source scarcity:** only 2 official GTA VI trailers exist, so 90% YT *will*
  reuse Trailer 2 across scenes (already happened: scenes 5, 6, 7) —
  distinct-*moment* (transcript/timestamp) diversity, not distinct-video diversity,
  is the achievable bar; fan gameplay is the only other pool and must pass the new
  exact-title gate, not a "fanmade" blanket ban.

### How to verify

- **Unit/regression:** (a) `_mixed_media_youtube_target_ratio` returns 0.90 for a
  gaming config; (b) `_is_directed_scene_row` rejects the GTA scene-7 row (§6);
  (c) `enrich_scene_queries` adds "aerial"/"prison gates" from narration +
  `visual_intent`; (d) `detect_niche_conflict("Grand Theft Auto VI…", "Grand Theft
  Auto: San Andreas – The Introduction")` returns conflict; (e) quota math: 8
  scenes at 0.90 → 8 requested, and multi-container reuse requires ≥7 distinct
  verified sources.
- **Live criteria (re-run GTA VI, same script, new job):** ≥7/8 scenes are **moving**
  YT clips (not frames); **0** clips whose title lacks GTA VI/6; **0** web images
  counted as YT; `queries.json` rows have `domain_category="gaming"`, non-empty
  `retrieval_positive_terms` and `youtube_query_intents`, and each YT query contains
  entity + viewpoint/action words; distinct YT sources ≥7.

---

## §9. Sequenced build order

Work in this order. Items marked ‖ can go in parallel (different files, no shared
state); items marked → depend on the previous item.

### Phase 0 — config-only, no code (ship first, needs a server restart to take effect)

| # | Change | Where |
|---|---|---|
| 0.1 | `VIDZEO_UNIVERSAL_SCRAPER_TIMEOUT_SECONDS` 300 → **30** | runtime `.env` |
| 0.2 | Set `VIDZEO_WEB_QUERY_DEADLINE_SECONDS=45` (currently unset → 15) | runtime `.env` |
| 0.3 | `VIDZEO_MIXED_WEB_SCENE_WORKERS` 6 → **2** | runtime `.env` |
| 0.4 | `VIDZEO_UNIVERSAL_LATE_WAIT_SECONDS` 5 → **20** (currently default 5) | runtime `.env` |
| 0.5 | `VIDZEO_YT_TRANSCRIPT_DEADLINE_SECONDS` 300 → **25** (code default); set `VIDZEO_YT_RANGED_MOMENTS=0` and `VIDZEO_YT_POOL_TRANSCRIPTS=0` (flags already exist — §2) | runtime `.env` |
| 0.6 | Set `VIDZEO_MIXED_YT_TRANSCRIPT_DEADLINE_SECONDS=120` (gaming transcript deadline; §8) | runtime `.env` |

Verify Phase 0 alone on a GTA re-run before writing code: if web candidates stay 0,
the code fixes in Phase 1 are mandatory, not optional. (Expect partial improvement
only — the 15 s vs 300 s inversion is also in code; §1 fix 1.)

### Phase 1 — foundation ‖ (three independent tracks)

| Track | Work | Files |
|---|---|---|
| 1A. Web | Timeout-inversion clamp (§1.1); de-stampede + dedupe transports (§1.2); gate fix + re-probe (§1.3); late-spool recovery (§1.4); per-engine error persistence (§1.5) | `web_image/resolver.py`, `web_image/universal_scraper.py`, `pipeline_v2_media_adapter.py:592` |
| 1B. YT speed | Wall arithmetic with real worker count (§2); search cap (§2); stop retrying throttled transfers (§2); ≥3 ranked options per scene (§3) | `app.py:10382-10441, 11343-11503, 11459-11467`, `youtube_pipeline_v2.py:5120-5145, 5520-5535, 6026-6168, 6654-6656`, `pipeline_v2_media_adapter.py:4651-4690` |
| 1C. Query builder | Validator rejects empty-field rows (§6.1); viewpoint/action extraction from `visual_intent` + narration (§6.1); `action_terms` fix; remove `"leak"` from gaming facets | `vidzeo_brain.py:695-720, 1231`, `media_query_strategy.py:~95-110`, `visual_requirement.py:1497-1501`, `mechanical_query_bundle.py:90-100` |

### Phase 2 — depends on Phase 1 →

| # | Work | Depends on | Files |
|---|---|---|---|
| 2A. Budget restructure | Split discovery/verification clocks; clamp discovery to `total − vision_reserve`; reserved frame-fallback allowance (§4) | 1B (wall numbers must be honest first, or the reserve is computed from a fantasy wall) | `media_resolution_coordinator.py:3477-3483, 3510-3531, 3800-3835, 4014, 4118, 4504-4510, 4720-4723`, `pipeline_v2_media_adapter.py:3633-3743`, `youtube_stage_budget.py:1-69` |
| 2B. Validator-only vision | Fast-mode single-attempt path; verdict contract with mandatory reason; cache + batch extension to frame fallbacks; gaming trap fix for `game-engine` markers (§5) | 2A (reserved allowance is what the batched call spends) | `visual_intelligence/vision_client.py`, `media_resolution_coordinator.py:2123, 1070-1117, 3942-3968`, `visual_intelligence/semantic_guard.py:159-181` |
| 2C. Gaming mode + relevance | `gaming_mode_for()` + niche-policy copy + ratio override (§8); exact-title gate + wire `detect_niche_conflict()` (§3); quota `semantic_verified` requirement (§3) | 1C (query validator must exist before gaming queries are trusted); 2B (vision verdicts are what the quota gate reads) | `mixed_media_planner.py`, `app.py:4508-4600, 10845-10848`, `pipeline_v2_media_adapter.py:3200-3250, 3100+, 5928, 3494-3495`, `youtube_pipeline_v2.py:686, 741-767`, `universal_clip_classifier.py:358`, `media_resolution_coordinator.py:6778` |
| 2D. Fallback chain | Re-verification before pool recovery; dedupe spare-pool block; hoist gaming stock guard; late-gate `visual_checked` fix (§7) | 2B (re-verification reuses the batched vision pass) | `media_resolution_coordinator.py:4971, 4991, 5858, 7465-7550`, `pipeline_v2_media_adapter.py:6816-6845, 7868-7990` |

### Phase 3 — verification (no new behaviour, proves Phases 0–2)

1. **Full test suites green with stash-checks** (Node 203/204 baseline with the one
   known pre-existing `scriptit-agent-bridge` failure; Python baseline 2641 passed /
   69 failed / 18 skipped / 1 error — prove zero new failures set-by-set).
2. **New regression tests** listed at the end of §1, §2, §4, §5, §6, §7, §8.
3. **Live GTA VI re-run** (same script, new job ID) against the acceptance table in
   §10. If it fails any row, the failing section's fix is incomplete — do not
   re-run the whole plan, fix that section.
4. **Second live check on a non-gaming documentary job** (e.g. a history title) to
   prove gaming mode didn't leak into documentaries (YT target back to 0.8/0.30,
   web lane still primary, stock still runs).

## §10. Live acceptance criteria (GTA VI re-run, same script, new job)

| # | Criterion | Target | Evidence source |
|---|---|---|---|
| 1 | Web candidates >0 | ≥6/8 scenes | `provider_web_candidates.json`, per-scene audit |
| 2 | YT batch wall | <300 s for 8 scenes (was ~910 s) | `app.py` branch-wall log / attempts ledger |
| 3 | YT search phase | <60 s | `_search_container_pools` timing |
| 4 | YT candidates surviving metadata | ≥3 distinct videos / scene | `rank audit` line (`youtube_pipeline_v2.py:5557-5560`) |
| 5 | YT download attempts | ≤2 / scene | attempts ledger |
| 6 | Vision verdicts | Every scene has `visual_decision` with `reason` + `identity_score`/`final_score`; **0** `skipped_after_scene_budget` for frame fallbacks | per-scene attempts ledger |
| 7 | Vision cost | ≤2 batched vision calls / scene | ledger call counts |
| 8 | Queries | All rows `domain_category="gaming"`, non-empty `retrieval_positive_terms` + `youtube_query_intents`; each YT query has entity + viewpoint/action words | `checkpoints/queries.json` |
| 9 | Gaming YT share | ≥7/8 scenes are **moving** YT clips (not frames) | final media plan |
| 10 | Relevance | **0** clips whose title lacks GTA VI/6; **0** web images counted as YT; distinct YT sources ≥7 | final media plan + `source_labels` |
| 11 | Graphics | **0** graphic-led scenes unless pool + stock are genuinely empty **and** the ledger shows frame fallbacks vision-*rejected* (not skipped) | final media plan |
| 12 | No regressions | Documentary (non-gaming) job still resolves web-first with stock available | second live check (§9 Phase 3.4) |

## §11. Honest limits (what genuinely cannot be fixed on this VM)

- **Egress proxy congestion is external.** All 5 working web transports and all
  yt-dlp traffic share one bot-gated proxy. When it congests, everything degrades
  together. The fixes reduce concurrent load and lengthen waits so the proxy can
  answer — they cannot make the proxy faster. A fresh query on a cold cache will
  always be slow; caching (search 6 h, transcripts 30 d, verified cuts 7 d) is the
  only durable mitigation.
- **GTA VI gameplay does not exist yet.** No real gameplay footage is on YouTube;
  the thin pool is a content fact. The achievable bar is distinct-*moment*
  (transcript/timestamp) diversity across the 2 official trailers + vetted fan
  gameplay, plus web images of trailers/key art — not 8 distinct official videos.
  The plan's distinct-source floor (§8) must downgrade the 90% *claim* when sources
  run out, never the render.
- **Transcript availability depends on captions existing per video.** No code can
  conjure them; the heuristic `_pick_timestamp` fallback
  (`youtube_pipeline_v2.py:3169`) is the correct posture, reached faster by skipping
  the fetch (§2).
- **Dead engines stay dead:** Baidu (hard-blocked), Openverse (504s through the
  proxy), Google Images (JS bootstrap shell — needs a browser, not a scraper tweak),
  Pexels/Pixabay/Brave (need API keys — forbidden), SearXNG (no configured
  instance). DDG universal engine is intermittently bot-gated; treat as bonus.
- **Download workers beyond 3–4 buy little** — the code notes the egress proxy
  serializes concurrent connections (`youtube_pipeline_v2.py:3570-3588`). The win
  is fewer, shorter attempts, not more workers.
- **Budget growth has a real cost.** Sizing the scene clock to fit an external-agent
  round trip (~4.5 min/call) multiplies preparation time across 8 scenes unless
  batching holds. The plan's answer is batched vision + call-count caps, not an
  unbounded wall-clock increase (§5).

## §12. What NOT to change (standing rules)

- **No API keys.** Direct retrieval only — Pexels/Pixabay/Brave stay keyless (HTML
  scrape) or unused; SearXNG stays unset unless the operator configures an instance.
- **No AI footage.** Real YouTube clips, web images, stock — never generated video.
- **Render never stops for one image.** Single-scene defects still route to the next
  ranked alternate (or a close re-download), never a hard fail. The distinct-source
  floor (§8) downgrades the 90% claim, never the render.
- **0-real-media still hard-fails.** Partial-real still delivers with advisory unless
  `MUSE_MEDIA_STRICT=1` (unchanged).
- **One video at a time.** Nothing in this plan changes queue concurrency.
- **Vision stays a gate, not a generator.** It validates what retrieval found; it
  never invents media, and its verdicts stay recorded per scene with reasons.
- **Do not "fix" the 910 s by deleting the wall.** The wall exists to bound the
  stage; the fix is honest arithmetic (§2), not removing the bound. Same for the
  120 s scene budget: split it (§4), don't delete it.

## §13. Risks of each fix (and mitigations)

| Fix | Risk | Mitigation |
|---|---|---|
| Web: longer wave wait (15→45 s) + fewer workers (6→2) | Media stage gets slower per scene (fewer parallel scenes) | Tiered wave (§1.2) fetches the 5 real transports first; total stage time falls because retries/orphans disappear. Measure per-scene wall <120 s (§1). |
| Web: clamp universal timeout 300→30 | A genuinely slow engine gets cut before returning | Control experiment shows engines return in ~4 s singly; 30 s is 7× headroom. Late-spool (§1.4) recovers stragglers. |
| YT: honest wall (~743→~263 s) | A slow batch now times out instead of limping to 910 s | The timeout is honest: at 3 workers the work genuinely doesn't fit in 263 s only if downloads stall — and stalled downloads are now capped at 2–3 attempts (§2) instead of 6. Driver retries from checkpoint as before. |
| YT: skip transcript/timestamp rank | Timestamps become heuristic (`_pick_timestamp`) — a clip may start at a less relevant moment | Heuristic is the existing fallback and is timestamp-checked by vision on frames; transcript cache still serves hits. Re-enable per-topic if heuristic timestamps fail vision repeatedly. |
| YT: ≥3 ranked options/scene | More downloads per scene (3× transfer load) | Options are ranked, not all downloaded: download stays 1 attempt/scene/pass; alternates download only after a failure. |
| Vision: validator-only + batched calls | Wrong clip slips through (metadata-plausible, visually wrong) | Keep deterministic `explicit_scene_conflict` on every accept; live vision mandatory for strict-identity scenes; no-vision accepts restricted to same-video/same-topic provenance (§5). |
| Vision: `game-engine` markers advisory for gaming | A non-gameplay video (e.g. a reaction video *about* the game) could pass as gameplay | Exact-title gate (§3/§8) + `_DOMAIN_NEGATIVE_TERMS` (reaction/streamer/news) still apply; vision still scores identity <35 as a reject. |
| Queries: strict validator rejects LLM rows | More mechanical re-plans (slower query planning, blander queries) | Mechanical gaming template is already correct (§6); validator rejects only empty/malformed rows, and the re-plan is per-scene, not per-job. |
| Gaming 90%: distinct-source floor | A gaming job may deliver with <90% YT and the claim downgraded — looks like a failure | The downgrade is explicit in the manifest (`youtube_coverage.accepted_ratio`), not silent; web rescue fills the remainder; 0-real still hard-fails. |
| Fallback: re-verify pool recovery | +1 batched vision call per degraded scene; if vision is down, scenes stay degraded | Timeout keeps the degraded stamp (§7) — no new failure mode, and the ledger now says *why*. |
| Stock: skip window for gaming | A gaming scene that *could* use a real-world still (e.g. a Florida beach for Leonida) loses it | Stock is rescue-only for gaming and the guard only fires on named-game titles; generic real-world scenes keep the stock lane. |
