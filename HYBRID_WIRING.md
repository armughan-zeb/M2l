# Hybrid production wiring — daemon active + Muse inference for everything

Date: Nov 9, 2026. HEAD: see git log (simplification commit on top of 6779c09).
Status: simplified pipeline live; proof test job running (see below).

## Simplified pipeline — one data pass, one script, one media pass, one monitor (Nov 9, 2026)

The user asked for the architecture SIMPLER, not more layers: get the data
once, write the script once, one media pass, one monitor, no loops, quick
and high-quality. Every loop the hardening sweep left behind was audited
and capped or removed:

1. **ONE data pass.** Research fetches all providers in parallel ONCE
   under ONE shared deadline — `MUSE_RESEARCH_TIMEOUT_MS` (default
   180000) in `scriptit/src/agents/scriptWriter.js`. The pipeline call
   and any direct-research recovery share that deadline: the recovery
   gets only the time left in it, never a fresh 90s (`scriptWriter.js`
   `researchDeadlineAt`). One encyclopedic synthesis call only, and
   only if the first pass is insufficient.
2. **ONE script.** The writer makes at most `MUSE_SCRIPT_MAX_DRAFTS`
   model calls (default 2: draft 1 + ONE fix pass). The old thorough
   budget was 6 drafts (`draftBudget = quickMode ? 2 : 6`) plus a
   runtime-fit call, up to 2 revisions, and a "final grounded rewrite"
   rescue — 4–6 drafts over 17 minutes on the BNSF repro. Now:
   `draftBudget = draftBudgetMax` (env, 1–6, default 2),
   `revisionLimit = 1`, and the final-rescue block is deleted. A draft
   that still fails the editorial gate is returned non-publishable —
   the CLI exits 2 and the job fails loudly instead of looping.
   The adapter's old default (re-run the whole ScriptIT generation
   when the narration came back short) is now opt-in only
   (`adapters/script.js`: `MUSE_SCRIPT_EXPAND_ON_SHORT=1`); by default
   a short script fails loudly with a clear message.
3. **ONE media pass per scene.** One query set built from the script's
   concrete entities, one candidate fetch, one vision check, pick the
   best and move on. Media priority is YouTube clips first, then web
   images, then stock (`adapters/vidnets.js:113`
   `media_source_priority: ['youtube', 'web', 'stock']`, coverage
   target `MUSE_YOUTUBE_COVERAGE_TARGET`, production 0.8). The
   6779c09 selection fix was verified real (a downloaded cut no longer
   mints vision acceptance; the gate-approved verdict is reused) — see
   the hardening section below. The single bounded re-plan before a
   0-real-media fail fires at most ONCE PER JOB, not per wrapper run:
   `render-vidnets-linux.js` restores `replanAttempted` from the saved
   job file on re-attach, so a daemon restart after a re-plan can
   never earn a second one (`replanAlreadyUsed()`). The only other
   retry is the coordinator's bounded late-vision lane (already
   downloaded clips only, ≤60s past the wall) — part of the one pass,
   not a re-plan.
4. **ONE monitor.** `vidnets-health-monitor` (every 5 min) is the only
   monitor — the old `vidnets-recovery-watch` self-removed on Nov 9
   and no longer exists. It checks daemon alive + connected, Vidnets
   :5010 healthy + memory admission, CMS reachable + outage duration,
   hook enabled, inbox requests not expiring unanswered, disk/tmp
   space; it fixes what it can itself (restart daemon/Vidnets, expire
   stale requests) and is silent when healthy. See the monitor section
   below.
5. **ONE settings place.** All knobs live in `.env` / `.env.example`
   with sane defaults: `MUSE_SCRIPT_MAX_DRAFTS=2`,
   `MUSE_RESEARCH_TIMEOUT_MS=180000`,
   `MUSE_REPLAN_MIN_REMAINING_MS=600000`,
   `MUSE_YOUTUBE_COVERAGE_TARGET=0.8`,
   `MUSE_RENDER_CONCURRENCY=1`,
   `VIDZEO_AGENT_MIN_WALL_SECONDS=600`. Two contradictions were
   removed: `MUSE_YOUTUBE_COVERAGE_TARGET` and
   `MUSE_RENDER_CONCURRENCY` were defined twice in `.env.example`
   (0.6/4 in the rendering section vs 0.8/1 in production) — one
   definition each now. `MUSE_RENDER_TIMEOUT_MS` is 6h everywhere
   (adapters + wrapper); `MUSE_SCRIPT_TIMEOUT_MS` is 45 min for the
   default CLI, 10 min for a custom command.
6. **A live failure found and fixed during this simplification.**
   BNSF attempt 5 (Vidnets job 23d01670) died at 07:49:46 PKT while
   its Vidnets job was still processing at 55% "Finding web images":
   a single transient status-fetch failure killed the wrapper, and
   the CMS job was failed for nothing. Fixes in
   `render-vidnets-linux.js`: the poll loop now retries the status
   fetch up to 3 times before failing, and `fail()` also writes the
   reason to stderr (it used to write only a stdout JSON the daemon
   never surfaced, so the daemon log showed only a progress line as
   the "reason"). The orphaned Vidnets job 23d01670 has no cancel
   endpoint, so it runs out on its own; its output can never be
   delivered (the CMS job already failed).
7. **Proof:** one local test job end-to-end on the simplified code
   (Amtrak, same title as 0bb3f900/ff377408/c9b66d00 for
   comparability), Vidnets job f937d6c8. Script: 8.6 min total
   (research 68s, 8 sources, 11 external claims, 0 synthetic; draft
   1 failed the gate, draft 2 passed after the 1.5 length-cap fix —
   exactly 2 model calls; 219 words). Render: submitted 03:25 UTC,
   completed 04:18 UTC (52 min; run 1 was lost to a server restart
   at 03:44, run 2 auto-requeued and finished). Video is valid
   (1920×1080, 69.3s, h264+aac, 17.7 MB) but **0/15 scenes real
   media — NOT deliverable** (wrapper rule: 0 real scenes fails
   loudly). Cause, verified in the manifest: every discovery lane
   returned 0 candidates on every scene (YouTube 0, web 0, stock 0
   — no keys), so no vision request was ever posted; the queries
   themselves were good (entity-grounded, e.g. "Amtrak trains 2006
   footage"). A live probe after the run: yt-dlp YouTube search
   works, Bing HTTP 200, but DDG fails with a DNS error on its
   backend and the server log shows "YouTube fallback skipped after
   primary transport failure". This is a search-transport problem
   the simplification did not touch — it needs its own fix
   (YouTube discovery via yt-dlp android client, DDG DNS/proxy,
   discovery retry with backoff). The other session's eda1c15
   (media-wall floor, identity ranking, vision budgets, queue
   auto-resume, YouTube timeout 8→15s/retries 1→2) was live for run
   2 and did not fix this. Job files:
   `~/workspace/vidnets-runtime/temp/test-simplify-01/`.

## Muse inference is COMPULSORY (not just wired)

Every vision call, script-writing call and Vidnets brain / LLM call must go
through Muse via the `external_agent` agent-bridge inbox, answered by the
`vidnets-inbox-watch` hook operator. This is enforced in code, not by
convention — see `lib/museInference.js`:

- `node muse-agent.js start` refuses to run unless the bridge is verified:
  no OpenRouter key in the environment, a Vidnets render path
  (`render-vidnets-linux.js`), the hook enabled, the agent-bridge inbox
  writable, the Vidnets launcher present. `MUSE_AUTO_LOOP=1` is a claim
  mode only — it never exempts the check (an earlier `lib/runtimePolicy.js`
  returned `[]` for auto-loop, which is how the daemon ran with no
  inference at all).
- `node muse-agent.js doctor` runs the same check plus a live Vidnets
  `/api/status` probe and FAILS (`Muse inference (compulsory)`) when the
  bridge is absent. It no longer passes inference with "Handled by the
  render command" without checking anything.
- `adapters/video.js` has no fallback to the native Remotion engine: with
  no Muse-inference render path it throws and the job fails closed.
- `adapters/vidnets.js` sends `openrouter_api_key: ''` and refuses to
  build/render if an OpenRouter key is set; `adapters/script.js` refuses
  before any script command runs; `scriptit/cli/generate.js` refuses an
  OpenRouter key when `MUSE_INFERENCE_COMPULSORY=1` (set in `.env` and
  `start-daemon.sh`).
- The Vidnets server itself gates external_agent jobs: in
  `build_qwen_verifier` the OpenRouter key is forced to `''` and
  `visual_fail_closed` stays `True`; the creative-review pixel audit never
  constructs a Qwen client for an external_agent job. Manual/desktop flows
  (llm_provider=openrouter) are untouched.
- `render-vidnets-linux.js` pre-flights the bridge before submitting, and
  a finished edit plan counts a scene as real only when Muse vision
  verified it (`media.visual_decision.visual_checked=true` and
  `accepted=true`). No edit plan, 0 verified real scenes, or 0 Muse
  vision verdicts at all → the job fails, never delivers graphics.

## The problem it fixes

Live CMS jobs were graphics-only: the daemon ran with no `.env`, no
`MUSE_RENDER_COMMAND`, no inference config, so `adapters/video.js` fell
through to the pure Remotion native engine (`lib/remotionRenderer`), which
fetches NO media. The chat renderer (`render-linux.js`) was good, but it is
a different engine from Vidnets; the user chose instead: daemon stays
active, Vidnets renders, and Muse answers every inference request.

## Architecture (three processes, one inbox)

1. **CMS daemon** — `node muse-agent.js start` (via `start-daemon.sh`).
   Claims jobs, runs ScriptIT for narration, renders via the wrapper below,
   validates, uploads, completes. Env in `.env` (gitignored) + exported by
   `start-daemon.sh`; `lib/config.js` loads `.env` (real env wins).
2. **Vidnets server** — already running on :5010, local mode, auth off
   (`vidnets/.venv/bin/python3 main.py`, PID 5268). Do NOT restart it with
   the daemon; `VIDNETS_AGENT.sh start` reuses it (`reused: true`).
3. **Inbox-watch hook** — `vidnets-inbox-watch`, poll 60s, re-wake 120s.
   Watches `~/workspace/vidnets-runtime/temp/agent-bridge/*/request.json`
   and wakes a Muse operator in side chat
   `38c7bbce-a978-43e6-bf4d-3f6d227d474c` to answer. This is the ONLY
   inference path. No OpenRouter key anywhere.

## Render path — why the wrapper, not adapters/vidnets.js

`MUSE_RENDER_COMMAND=node render-vidnets-linux.js` (in `.env`).
The wrapper:
- reuses `adapters/vidnets.js:buildRequest` — same job as the built-in
  path: `llm_provider=external_agent`, Mixed Media, priority
  youtube > web > stock, `visual_understanding=true`,
  `final_visual_gate_mode='run'`, `fail_on_unresolved_media=true`, and
  — since the Nov 9 filter-best fix — `visual_required=false` and
  `strict_retrieval_accuracy=false` (filter-best; see below).
  `MUSE_MEDIA_STRICT=1` restores the old strict flags;
- submits via `vidnets/VIDNETS_AGENT.sh --runtime-root
  ~/workspace/vidnets-runtime submit`, polls `job <id>` every 5s,
  retries a paused job up to 3 times;
- relies on the hook worker to answer inbox requests — the exact pattern
  proven by test jobs 0bb3f900 and ff377408 (latter: 12/15 scenes real).

`adapters/vidnets.js` was NOT used because its `serviceInbox` would call
`MUSE_INFERENCE_COMMAND` for every pending request while the hook worker
is already answering the same requests directly via `VIDNETS_AGENT.sh
answer` — the second answer fails ("already answered"). So
`MUSE_INFERENCE_COMMAND` / `MUSE_INFERENCE_URL` are deliberately UNSET.
`doctor` verifies the real bridge instead (Vidnets health, inbox writable,
hook enabled, no OpenRouter key) — it no longer passes inference just
because a render command is set.

## Filter-best — use Vidnets' native flow, do not rebuild it (Nov 9, 2026)

The user corrected us twice in one day: first "simplify", then — the
correction that stands — "stop redesigning; Vidnets is ALREADY built the
way I want; USE it properly". The native architecture is: LLM prepares
search queries from the script's scenes → Vidnets searches YouTube clips
+ web images → Vidnets FILTERS THE BEST (ranks candidates, keeps the
best real media per scene) → Remotion edits. Mixed-media powered by Muse
via the agent-bridge inbox (`external_agent` everywhere) + Remotion.

**What was misused.** Our wiring forced the whole job into
hard-required mode, stricter than Vidnets intends:

- `adapters/vidnets.js:buildRequest` sent `strict_retrieval_accuracy:
  true` and `visual_required: true`. Vidnets' own code says vision is
  advisory by default — `app.py:_visual_required_for_job` (~9048):
  "an uncertain verdict must not prevent video delivery" — and a strict
  job with ANY unresolved scene fails outright instead of degrading
  (`app.py:_repair_unresolved_media`, ~14539: `strict_real_media_job =
  strict_retrieval_accuracy OR visual_required`). Per-row
  `visual_required` then forced every web/YouTube asset to need a
  completed verdict before the renderer
  (`pipeline_v2_media_adapter.py:_visual_requirement_issue`, ~6879).
- `render-vidnets-linux.js:countRealScenes` required
  `visual_decision.visual_checked=true AND accepted=true` to count a
  scene as real, and failed the job when `verified === 0` even if the
  adapter had accepted real media — a second, stricter gate on top of
  Vidnets' own.
- The hook prompt (`~/hooks/definitions/vidnets-inbox-watch.json`,
  step 3) told the operator "accept ONLY if the frames actually show
  what the scene describes… it must match the scene" — hard-reject
  wording that threw away borderline-but-real footage.

**What Vidnets does natively (verified in code, not guessed).**
Candidates are scored (`vision_client.py`: identity/action/context/
final_score) and ranked by `final_score`
(`media_resolution_coordinator.py:573`); no hard visual-score threshold
exists. `classify_visual_outcome` (`visual_intelligence/context.py:74`)
distinguishes hard rejections (markers: replace, reject, fatal, wrong,
mismatch, unsafe, adult, suggestive, synthetic, watermark, off-topic,
irrelevant) from unavailable/uncertain — the resolver retries uncertain
and blacklists only hard rejections. YouTube clips fail OPEN when vision
is unavailable (`_youtube_fail_open_on_visual_unavailable`,
`media_resolution_coordinator.py:764`, default 1). The adapter's own
plan-media `verified` flag (`pipeline_v2_media_adapter.py:8291`) is
"not explicitly rejected AND (verified OR status accepted/downloaded
OR a qwen verdict accepted/deterministic)". Named-entity scenes still
require a live verdict (`scene_requires_visual_verification`,
`context.py:38`, `strict_identity`). The final gate
(`pipeline_v2_media_adapter.py:6992-7147`) rejects only on `accepted is
False` / `fatal_mismatch` / `entity_conflict`; pending/unchecked rows
degrade to graphics only when nothing better exists.

**The fix (filter-best).**
- `adapters/vidnets.js:buildRequest` — `strict_retrieval_accuracy`
  and `visual_required` now follow `MUSE_MEDIA_STRICT` (default 0 =
  filter-best; 1 restores strict). Vision still RUNS
  (`visual_understanding: true`, `final_visual_gate_mode: 'run'`) and
  still ranks; only named-entity scenes still require a verdict.
- `render-vidnets-linux.js:countRealScenes` — a scene is real when
  Vidnets' adapter accepted it (`media.verified === true` in the edit
  plan, or a Muse-verified verdict); an explicit Muse rejection
  (`accepted === false`, `fatal_mismatch`, status rejected) always
  wins. The ONLY hard delivery rule is `real === 0` fails the job.
- Hook prompt step 3 — filter-best wording: accept the best real
  match, accept borderline-but-real footage with the caveat in the
  reason, reject ONLY clearly-wrong footage.
- `.env` / `.env.example` / `start-daemon.sh` — `MUSE_MEDIA_STRICT=0`
  documented as the filter-best default.
- Provider wiring re-verified: every mixed-media LLM call routes via
  the job-config key to `OpenRouterLLM`, whose constructor overrides
  the key to `external-agent:` when `llm_provider == 'external_agent'`
  (`vision_client.py:422`, `openrouter_llm.py:222`); `jev_decision_
  service` is not in the job path; `model_catalog_policy` only runs
  for non-`external_agent` providers. No OpenRouter key in shell or
  server env. Tests: `tests/muse-inference.test.js` (buildRequest
  filter-best flags + strict-mode case; countRealScenes adapter-
  accepted / explicit-rejection cases). `npm test` 60/60.

**Proof job f459edf0 (filter-best, same inputs as the simplified proof)
— INCONCLUSIVE end-to-end, conclusive on the diagnosis.** Same proven
219-word Amtrak script as f937d6c8, submitted 04:34 UTC Nov 9 with the
filter-best flags verified in the request (`visual_required: false`,
`strict_retrieval_accuracy: false`, `llm_provider: external_agent`).
Result: **0/15 scenes real** (countRealScenes: real 0, graphic 15,
verified 0) — but NOT because of filter-best strictness: discovery
returned **0 candidates in every lane** (web candidates checkpoint:
empty dict; media plan: 15/15 graphic fallbacks; **0 vision verdicts**
in the plan — there was nothing to rank, so the filter-best gate was
never exercised). The queries were mechanical: subjects "In February"
(scenes 1–7), "Niagara" (8), "Past" (9–11), "Texas Eagle" (12–15),
e.g. search "In February February Amtrak departures photo" — the
timed-out scene plans laundered as "directed" (`source_query_plan`:
directed 15, fallback 0, 120.67s). The job was submitted ONE MINUTE
before the other session's query-planner fix went live (c91ca8b, server
restart 04:35 UTC), so its planning ran on the old planner. The render
itself then survived a VM reboot at ~04:59 UTC (server + daemon
restarted, job auto-resumed at 72% "Resuming saved Remotion edit") and
reached 94% (Remotion 83%) before an **external cancellation** killed
it ("Cancellation requested for job f459edf0", actor not identifiable
from server logs — same pattern as the earlier 8a0871ba cancellation);
the job was then deleted from the queue, so no video exists. The
0-candidate cause has since been fixed by the other session's chain
c91ca8b..ee52020 (planner agent floor, stop laundering timed-out plans,
remove query/media-fetch timeouts, proxy fan-out fix) — the live job
3790f89e on that code found 501 YouTube candidates. **Re-run the proof
on the current server code once 3790f89e finishes; do not run a second
render while it is live (low memory).** The filter-best wiring itself
is verified by flags + unit tests (60/60), not by this job.

## Inference flow per request type

All requests land as `~/workspace/vidnets-runtime/temp/agent-bridge/
<32-hex-id>/request.json` (`state: pending`, `expires_at` set by the
requester). The hook script wakes the operator, who fetches with
`VIDNETS_AGENT.sh request <id>` and answers with
`VIDNETS_AGENT.sh answer <id> <file>`.

| Request | Who posts it | TTL / budget | Operator does |
|---|---|---|---|
| ScriptIT draft / hook / revision (`operation: scriptit.completion`, `kind: text`) | ScriptIT CLI (`scriptit/cli/generate.js` → `providers/agentBridge.js`, writes request.json directly, polls `response.json`) | up to 30 min per attempt, 45 min total budget; retries with a fresh id | Writes evidence-grounded narration / hook JSON from the prompt's evidence only; no invented facts, no generic filler |
| Vidnets text (scene direction, planning, queries) | Vidnets server, `llm_provider=external_agent` → `agent_bridge.agent_post` | 300 s (vision/text default) | Concrete searchable queries from the job's title/scene; scene-direction answers from job context |
| Vidnets vision (clip/image review) | Vidnets server, same bridge; frames attached as `local_image` files | 300 s | Looks at the frames, accepts ONLY if they show what the scene describes; rejects with a specific reason otherwise |
| Web/YT clip + image sourcing | Not inference — Vidnets pipeline does the downloading (yt-dlp, web scrapers, Pexels/Pixabay if keys exist). Vision above is the gate that lets a candidate into the final plan | — | See vision row. If no candidate is accepted, the scene falls back to a graphic |

Env that shapes sourcing (`.env` + `vidnets-runtime/.env`):
`MUSE_YOUTUBE_COVERAGE_TARGET=0.8` (80% of scenes enter the YouTube
lane), `MUSE_RENDER_CONCURRENCY=1` (low-memory VM), `TMPDIR` pointed at
`vidnets-runtime/temp` (/tmp is a 512 MB tmpfs), Linux TTS fallback in
`vidnets-runtime/.env` (`/opt/hatch/bin/tts`, Edge TTS websockets are
blocked by the egress proxy).

## Failure policy (fail loudly, never ship graphics silently)

- `render-vidnets-linux.js` throws (job fails in the CMS, nothing is
  delivered) when: the Muse inference bridge is not verified before
  submitting; Vidnets job errors / is cancelled / stays paused after
  3 retries; no video file or video < 1 MB; no edit plan is found next to
  the video (the real-media mix cannot be verified); or the finished edit
  plan (`*_remotion_edit_plan.json` next to the video) has **0
  Muse-verified real-media scenes** (a scene counts as real only if
  `media.type` is `image`/`video` with a real `src` AND
  `media.visual_decision.visual_checked=true` and `accepted=true`) or
  **0 Muse vision verdicts at all**. Partial-real jobs still deliver, with
  the counts in `meta.realScenes/totalScenes/graphicScenes/museVerifiedScenes`.
- ScriptIT: a draft that fails the editorial gate is NOT silently
  replaced with template text — the CLI exits non-zero and the worker
  fails the job. Template narration is opt-in only
  (`MUSE_SCRIPT_ALLOW_TEMPLATE_FALLBACK=1`, NOT set).
- There is no fallback to the native Remotion engine or the solid-colour
  ffmpeg placeholder anywhere in this wiring.

## CMS-outage resilience + health monitor (Nov 9, 2026)

Overnight Nov 8–9 the CMS returned HTTP 502/530 for hours (Cloudflare
tunnel down on the CMS side) and the VM rebooted repeatedly. The daemon
died on every restart: `resumeActiveJob` (`lib/jobRunner.js`) re-threw a
530 from its one-shot progress probe, so `muse-agent.js start` exited
within ~1s and only the 10-minute recovery watch restarting it kept
anything alive — each restart dying the same way (see `daemon.log`
Nov 8 21:54–Nov 9 01:34, one `ERROR CMS returned HTTP 530` per start).
The poll loop itself already survived (it only warned), but the resume
path in front of it did not. Fixed:

- `lib/cmsRetry.js` (new): transient = HTTP 408/429/500/502/503/504/530
  plus transport failures (refused/reset/DNS/timeout); terminal = 401
  (revoked credential), 403/404/409/400/422. `retryDelayMs` is
  exponential backoff, base 2s, cap 60s. Tunables in `.env.example`:
  `MUSE_CMS_RESUME_ATTEMPTS` (default 12), `MUSE_CMS_RETRY_BASE_MS`,
  `MUSE_CMS_RETRY_MAX_MS`.
- `lib/jobRunner.js:resumeActiveJob`: the saved-job probe retries with
  backoff (bounded attempts), then returns `{ deferred: true }` WITHOUT
  clearing the resume marker and WITHOUT throwing. Only 409 clears the
  marker; only 401 / non-transient errors still throw.
- `muse-agent.js:startCommand`: a deferred resume is re-attempted from
  the poll loop (one probe per iteration, backing off to 60s) and NO new
  job is claimed while a saved job is owed a resume — a previous run may
  have failed to report its failure to a down CMS, so the marker is
  still there and `executeJob` re-attaches to its checkpoints. The
  `nextJob` poll also backs off exponentially now (was a fixed 12s
  retry) and resets on success. Net effect: the daemon survives hours
  of CMS outage in-process and resumes cleanly when the CMS returns,
  with no restart. Verified live Nov 9 ~06:41 PKT: the daemon cleared
  the stale BNSF marker on a 409 once the CMS answered, instead of
  exiting.
- Tests: `tests/cms-resilience.test.js` (9 tests) pins the
  transient/terminal classification, the backoff curve, deferral with
  the marker kept, 409-after-retries clearing, 401/400 throwing
  immediately, and a monitor fixture dry-run.

## Post-reboot recovery — what actually restarts things

- **Today, the permanent monitor cron below.** The overnight
  recovery watch (`vidnets-recovery-watch`, every 10 min) SELF-REMOVED
  once the pipeline recovered (~06:44 PKT Nov 9). The monitor cron
  `vidnets-health-monitor` (every 5 min, created Nov 9, 2026) replaces
  it permanently — a VM reboot is picked up at the next 5-minute tick.
- **systemd is NOT the answer here, even though PID 1 is systemd 255
  and `systemctl is-system-running` says `running`.** A system unit
  CAN start in this boot (`systemd-run --scope /bin/true` works), but
  `/etc` lives on an overlay whose upper layer is under `/run`
  (tmpfs-backed), so a unit installed in `/etc/systemd/system` would
  NOT survive the very reboot it is meant to recover from. The repo
  templates `vidzeo-muse.service` / `vidzeo-muse@.service` are also
  stale (paths point at `/opt/vidzeo/muse-tools`, no `start-daemon.sh`
  env) — they need correcting before any systemd use, and that file is
  outside the hardening sweep's file set, so it is reported here, not
  edited. The durable supervisor is the Muse cron system (definitions
  live outside the VM) driving a script under `$HOME` (persistent
  disk): that is the monitor below.
- **`start-daemon.sh` is not a supervisor** and says so in its header:
  if the daemon exits, nothing in that script restarts it.

## Health monitor — `~/hooks/scripts/vidnets-health-monitor.sh`, every 5 min

Replaces the self-removed recovery watch (same job, permanent, wider).
Cron definition to create (coordinator applies it via the cron tools):

- id `vidnets-health-monitor`, owner `goal:documentary-video-render-pipeline`,
  mode `task`, enabled, schedule interval every `5m`, body: run
  `bash ~/hooks/scripts/vidnets-health-monitor.sh`, silent when the
  summary says `result=ok`; when it says `result=needs_user`, message
  the user with the exact ALERT lines and what was already tried/fixed.
  Never claim, deliver, or restart a job; never edit the hook
  definition or `.muse-agent.json`.

Checks, in fix order (Vidnets before the daemon — the daemon's start
gate refuses to start while Vidnets is down): disk/tmp space; Vidnets
:5010 healthy + memory admission state; inbox-watch hook enabled; CMS
reachable + outage duration; daemon alive + connected
(`node muse-agent.js status`); agent-bridge inbox stale / past-half-TTL
requests; Vidnets queue-job stage while a render is in progress.

Fix-vs-report rules (also in the script header): it FIXES a dead
daemon (`start-daemon.sh`, detached) and a dead Vidnets server
(`VIDNETS_AGENT.sh start` — reuses the live :5010 server when healthy,
verified `reused: true` Nov 9) and marks inbox requests past
`expires_at` as expired on disk (the same state the Vidnets server
itself writes when a waiter gives up). It NEVER restarts Vidnets while
a render is in progress (active job + `work/<job>/vidnets-job.json`
exists) — it only reports — and never claims, delivers, restarts a job
mid-render, edits the hook definition, or touches credentials. It
REPORTS (ALERT, exit 2) when a fix failed, Vidnets is down during a
possible render, the hook is disabled, the daemon is up but not
connected while the CMS is up, the CMS is down > 30 min (or > 5 min
with a job active, alerted once per outage), disk is critically low,
or a render is held for memory (`admission_state=waiting`) with a job
active. NOTE lines (low_memory admission — the steady state on this
7.7 GB VM, currently ~466–719 MB available; a transient CMS blip; a
request past half its TTL the hook may still answer) never wake the
user. Silent when healthy except one summary line; runs are logged to
`~/hooks/state/vidnets-health-monitor/monitor.log` (last 500 lines)
and overlap is prevented with `flock`. Dry-run: `VIDMON_DRY_RUN=1`
prints `WOULD …` instead of acting; paths/endpoints are overridable
via `VIDMON_*` env vars for fixtures (see the test). Live dry-run
Nov 9: exit 0, all OK, one NOTE (low_memory).

## Memory-admission visibility

`/api/status` already reports it — `memory.admission_state` is
`ready` / `low_memory` / `waiting` / `unknown`, with `available_mb`,
`render_blocked`, `can_start_render` and a human `message`
(`vidnets/server/services/render_resource_manager.py:render_memory_status`,
surfaced by `vidnets/server/app.py:_memory_status_snapshot`). The hold
itself is in `vidnets/server/services/native_render_policy.py:1756`:
`prepare_render_resources` raises `RenderMemoryLowError` below 384 MB
available and the render loop retries for up to
`VIDZEO_REMOTION_MEMORY_WAIT_SECONDS` (default 120s), printing
`[render-memory] Waiting for render memory (X MB available); saved
edit retained` and reporting stage `waiting_for_memory` on the queue
job (`vidnets/server/app.py:20761,22525,22975,23576`). What was missing
was OUR side surfacing it instead of a silent stall, so the monitor
now reads `admission_state` from `/api/status` on every run and
reports a `waiting` hold with the available/minimum MB. The wrapper
side is now fixed too (Nov 9 hardening sweep): `render-vidnets-linux.js`
logs on progress-TEXT change as well as % change, and when a job stays
paused it checks `/api/status` memory and names a memory hold (with
available/needed MB) instead of blaming unanswered inference. No
Vidnets server change is needed: the admission state is already in
`/api/status`; the server only needs no new field.

## Recovery — what happens on restart / crash

- **Daemon restart, no active job:** normal start; it heartbeats and
  polls the CMS queue. Nothing to resume.
- **Daemon restart, job claimed but not delivered:** `muse-agent.js
  start` calls `jobRunner.resumeActiveJob` before polling. It reads
  `work/active-job.json`, re-attaches to the CMS job (409 = job already
  settled in the CMS → clear the marker, do not re-deliver; 401 =
  credential revoked → abort), then re-runs `executeJob`:
  saved `script.txt` is reused if it passes validation; a saved
  `video.mp4` + `video-meta.json` matching the job digest is reused and
  NOT re-rendered; otherwise the render stage runs again.
- **Render wrapper re-invoked for the same job:** it reads
  `work/<jobId>/vidnets-job.json` and re-attaches to the same Vidnets
  job id instead of submitting a duplicate.
- **Vidnets job paused mid-render:** the wrapper retries it (up to 3
  times); a Vidnets retry resumes from its saved checkpoint, it does
  not restart generation. The Vidnets server keeps the queue job, so a
  daemon restart does not lose the Vidnets-side job either.
- **Daemon killed mid-render (SIGTERM/SIGINT):** the shutdown handler
  reports the job as failed to the CMS with "saved work is retained;
  retry the run to resume" and clears the active-job marker; the CMS
  requeues the job and the next daemon start resumes it as above.
  A hard kill (SIGKILL) skips the report, but the CMS inactivity
  deadline requeues the job and the same resume path applies.
  No path double-delivers: `deliver`/`complete` runs only after the
  master passes local validation, and a 409 from the CMS settles the
  job without delivery.
- **Hook worker misses a request (TTL expiry):** the requester fails
  that attempt closed (Vidnets: candidate discarded / job pauses;
  ScriptIT: retries with a fresh request id inside its 45-min budget).
  The hook re-wakes every 120s for still-pending requests, so a missed
  first wake is retried while the TTL lasts. If the hook itself is
  disabled, live jobs will stall and then fail — check
  `hooks.logs vidnets-inbox-watch` and re-enable it before restarting
  the daemon.
- **Vidnets server dies:** the wrapper's `start` call relaunches it
  (same port range 5010–5110, same runtime root); the daemon does not
  need a restart for that. If the server is down for long, in-flight
  renders fail loudly as above.

## Restart procedure (what was done Nov 8, ~22:30 PKT)

1. `npm test` — 24/24.
2. Verified no active job (`work/active-job.json` absent, CMS
   `currentJobId` empty) and the Shinkansen job already delivered.
3. Killed the old daemon (PID 5324, started 17:22 on pre-ScriptIT code)
   with SIGTERM; Vidnets server (PID 5268) left running.
4. `nohup ./start-daemon.sh >> daemon.log 2>&1 &` — new daemon on HEAD
   e1dc050 with the wiring above.
5. `node muse-agent.js doctor` + `node muse-agent.js status` — see the
   restart report for results.

## Nov 9, 2026 hardening sweep (fixes for every repeated failure)

- **Thin research:** ScriptIT providers now throw instead of silently
  returning `[]` (DDG/Yandex/Bing `NO_PARSEABLE_RESULTS`), with retries
  (3 attempts, exponential backoff + jitter, 429 `Retry-After` honoured)
  and per-provider failure names in the job warnings
  (`scriptit/src/research/resilience.js`, `providers/duckduckgo.js`,
  `yandex.js`, `bing.js`, `freeData.js`, `rss.js`, `research/pipeline.js`).
  `researcher.js` now calls Yandex in thorough mode too (was quick-only),
  calls `yandexNewsSearch` (was never called), and its `canonicalUrl`
  actually strips `utm_source`/`utm_medium` (the old regex only matched
  a param literally named `utm_`). Live probe Nov 9: DDG 0, Bing 5,
  Yandex 0, Wikipedia 2, GDELT 429 — still thin on this VM, but now
  visible and retried instead of silent. No Google web search exists in
  ScriptIT (only Google News RSS); adding one needs a key or a scraper
  Google will block.
- **Junk candidates:** Vidnets `search_hybrid` no longer lets a DDG
  throw discard Bing's rows (per-engine isolation +
  `hybrid_search.py`); scene queries are now entity-grounded
  (`query.py:_extract_scene_identifiers` — locomotive/unit numbers
  like `locomotive 160`, `BNSF 1555`, named places like `Cajon Pass`;
  bare numbers never accepted); same image under different signed URLs
  dedups (`_canonical_image_key`).
- **Accepted clips dropped:** the coordinator minted vision acceptance
  from a merely-downloaded cut (`media_resolution_coordinator.py`) and
  then re-read the raw pending decision, so the stored row claimed
  `accepted` while its `visual_decision` said pending — downstream
  correctly rejected it (`pipeline_v2_media_adapter.py:6992`). Fixed:
  a downloaded cut no longer mints acceptance, the gate-approved
  verdict is reused, quota-inline skips are recorded so the late lane
  retries them, and the late-vision deadline is floored at now+60s when
  the delivery wall is spent. Honest correction: in ff377408 only 1 of
  the 4 retained clips ever had a live vision verdict — the other 3
  were promoted on "downloaded" alone. YouTube discovery also ate 954s
  of the 1033s media deadline, starving vision — a scheduling problem
  the selection fix does not solve.
- **Inbox loop:** TTL floor 600s verified live (a 20s request got
  600.0s). ScriptIT bridge wall raised 300s → 600s
  (`agentBridge.js`, `agentInference.js`) and the caller caps that
  dominated it — synthesis 180s → 600s, writer 300s → 600s, CLI
  300s → 600s. Hook re-wakes `scriptit.*` requests every 60s (was
  120s). Stale requests: 0 live pending, 15 expired, hook log silent.
- **Memory:** render worker estimate 400 → 392 MB, 1 frame buffer
  below 1024 MB available; the 384 MB hold floor is unchanged
  (available RAM swings 394–655 MB — tight). `held_reason` is now in
  `render_memory_status()` and a HELD line is printed; the wrapper
  names a memory hold instead of blaming inference.
- **/tmp:** `checkTempSpace()` in the wrapper fails loudly if runtime
  temp < 512 MB or /tmp < 128 MB free; `start-daemon.sh` prunes stale
  scratch older than 7 days on every start.
- **Re-plan retry:** before failing a job for 0 real-media scenes the
  wrapper does ONE fresh submit with fresh queries (plan cache
  bypassed, `yt_plan_cache_ttl_seconds: 0`), only with ≥10 min budget
  left; after it, still-0-real fails loudly as before.
- **Daemon resilience + monitor:** see the sections above.
- Tests: `npm test` 58/58; Vidnets `test_external_agent` 36/36,
  `test_media_resolution_coordinator` 37/37,
  `test_scene_query_identifiers` 7/7. One test-infra fix:
  `vidnets/pytest.ini` basetemp moved to `/tmp/vidnets-pytest`
  (the old `temp/pytest` broke the whole suite whenever `temp/` was
  cleaned).

## Still unproven

- The first live CMS job on this wiring (script via ScriptIT inbox,
  render via wrapper, vision via hook, deliver). Watch its daemon log
  and hook answers; the known weak spots are: search returning junk
  candidates (only 8 reached vision in 0bb3f900), YouTube clips accepted
  by vision but dropped from the final plan (4 accepted, 1 kept in
  ff377408), and generic narration if ScriptIT research finds no
  evidence (it then fails with RESEARCH_INSUFFICIENT instead of
  inventing facts).
- Pexels/Pixabay stock rescue is unwired: no keys in env, code present
  but untested. Without keys, stock is simply unavailable.
- The wrapper's real-media check depends on the edit-plan file being
  next to the video; if Vidnets changes its output layout the job now
  fails closed (no plan = cannot verify) instead of falling back to
  file + decode checks.
- Tests: `npm test` is 60/60 (Nov 9, 2026 simplification) — the 58
  above plus the draft-budget test (a gate-failing draft stops after
  exactly 2 model calls, publishable:false) and the re-plan-resume
  test (a saved `replanAttempted: 1` blocks a second re-plan); the
  earlier 33/33 proved the compulsory gate fail-closed (see
  `tests/muse-inference.test.js` and `tests/runtime-policy.test.js`).
- BNSF attempt 5 (Nov 9, 07:32–07:49 PKT) FAILED on a wrapper bug, not
  on media: script was good (213 words, 07:42), but the wrapper died
  on one transient status-fetch failure while Vidnets job 23d01670
  was still at 55%. Fixed in the simplification (status retry ×3,
  `fail()` also to stderr). The title needs a fresh re-queue (attempt
  6) in the CMS.
- Amtrak proof job c9b66d00 (old code, 6779c09) is still processing at
  63% "Finding scene 15/15" after 2165s — the YouTube-discovery
  starvation the hardening sweep did not solve. It is left running;
  its outcome is recorded separately, not used as proof for the
  simplified code.
