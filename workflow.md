# WORKFLOW — the runbook. Read `rules.md` first, then follow this file.

This is the step-by-step runbook for running the Vidzeo / M2l
documentary pipeline. It assumes a fresh clone on the Linux VM (Ubuntu,
Node 24, Python 3.12, ffmpeg, yt-dlp). If a step's expected output does
not match, stop and fix that step before moving on — do not skip ahead.

## 1. Setup from a fresh clone

```bash
git clone https://github.com/armughan-zeb/M2l.git
cd M2l
git checkout codex/muse-agent-driven-reconnect
npm install            # repo root (daemon, adapters, wrapper, tests)
npm install --prefix scriptit   # ScriptIT (script engine)
cp .env.example .env   # then fill in the values below; .env is gitignored
```

Settings that must be in `.env` (see `.env.example` for the full list):
`MUSE_RENDER_COMMAND=node <absolute-repo-path>/render-vidnets-linux.js`,
`MUSE_VIDNETS_ROOT=<repo>/vidnets`,
`MUSE_YOUTUBE_COVERAGE_TARGET=0.8`,
`MUSE_MEDIA_STRICT=0` (filter-best; see §3),
`MUSE_RENDER_CONCURRENCY=1`,
`TMPDIR=<runtime>/temp` (NOT /tmp — it is a 512 MB tmpfs),
`VIDZEO_TEMP_DIR=<runtime>/temp`.
`MUSE_INFERENCE_COMMAND` or `MUSE_INFERENCE_URL` enables the resident Muse dispatcher. Hook-only installs use the host inbox hook. No API keys go in `.env`.

Start Vidnets (the server is stateful — one instance only):

```bash
bash vidnets/VIDNETS_AGENT.sh --runtime-root ~/workspace/vidnets-runtime start
# Expected: {"ok": true, ...} or {"reused": true, ...} — a second start
# reuses the live server; a real restart needs SIGTERM on the old PID first.
curl -s http://127.0.0.1:5010/api/status | python3 -m json.tool
# Expected: healthy, ffmpeg true, can_start_render true.
```

Enable the inbox-watch hook (`vidnets-inbox-watch`, poll 20s). If a
resident transport is configured with `MUSE_INFERENCE_COMMAND` or
`MUSE_INFERENCE_URL`, the dispatcher polls every second and answers in
parallel (up to eight), while the hook is the alert-only fallback. Without
that transport, the hook is the primary answerer. Its definition lives at
`~/hooks/definitions/vidnets-inbox-watch.json` (`"enabled": true`).
It wakes a Muse operator in the side chat to answer every inbox request
(script, scene queries, direction, vision) via
`VIDNETS_AGENT.sh request <id>` / `answer <id> <file>`.

Verify the bridge, then start the daemon (detached — a foreground run
dies with the shell):

```bash
node muse-agent.js doctor
# Expected: all checks PASS — Vidnets :5010 healthy, inbox writable,
# hook enabled and recently observed running, no OpenRouter key. Doctor FAILS without a verified
# bridge; do not run jobs until it passes.
nohup ./start-daemon.sh >> daemon.log 2>&1 &
node muse-agent.js status
# Expected: connected true, activeWorker true, activeJob null.
```

## 2. Job lifecycle (one CMS job, end to end)

The CMS gives the title; the daemon claims it; the worker produces
script + video, uploads to the CMS, marks completed (thumbnails are generated locally by Pikgen).

| Step | What happens | "Done" looks like | If it fails |
|---|---|---|---|
| Claim | Daemon polls the CMS, claims one job, becomes activeWorker with activeJob set | `status` shows the job id | CMS 502/530: daemon retries in-process with backoff — do NOT restart it |
| Research (once) | All providers in parallel ONCE under one shared deadline (`MUSE_RESEARCH_TIMEOUT_MS`, default 180s); one encyclopedic synthesis call only if the first pass is insufficient | Research log lists sources + external claims; failures name the provider and why | Zero external claims → `RESEARCH_INSUFFICIENT`, job fails loudly. Do not re-run research |
| Script (once) | ScriptIT CLI, agent-bridge mode, at most `MUSE_SCRIPT_MAX_DRAFTS` calls (default 2: draft 1 + ONE fix pass) | Publishable script, words ≈ minutes × 150 (floor minutes × 135), external claims ≥ 1, 0 synthetic | Draft still fails the gate → CLI exits 2, job fails loudly. No more drafts |
| Vidnets mixed-media | LLM writes queries from the scenes → search YouTube clips + web images → FILTER THE BEST (rank, keep the best real media; reject only clearly-wrong footage) → vision ranks (scores) → Remotion edits | Most scenes real, most of them clips where clips exist | See §5. One bounded media re-plan per job, before a 0-real fail only |
| Render | Remotion renders the 1080p master (6 h CMS hard limit per attempt; 20 min inactivity limit) | Video ≥ 1920×1080, ≥ target minutes, h264+aac, ≥ 1 MB, audio stream present | Render stalls: check memory admission (`:5010` status) and the wrapper log — do not restart mid-render |
| Validate | Wrapper checks the edit plan and the worker validates the master | `Edit plan: N/total scenes real media` with N equal to total | Incomplete coverage can trigger one bounded re-plan before delivery is blocked |
| Deliver | Worker uploads video + script to the CMS (--skip-thumbnail), marks completed | CMS shows the job completed (Pikgen handles thumbnails locally) | Delivery validation fails → fix the artifact, do not weaken the checks |

## 3. How filter-best works (the architecture, in one paragraph)

Vidnets' native flow: the LLM prepares search queries from the script's
scenes, searches YouTube clips and web images, and filters the BEST —
candidates are ranked by final score and the best real media per scene
is kept. Vision is advisory by default: an uncertain verdict must not
prevent delivery. Only clearly-wrong footage is rejected (wrong subject,
wrong era, unrelated content, gameplay, text card, watermark); named-
entity scenes (`strict_identity`) still require a live verdict, and a
YouTube clip whose vision is unavailable fails OPEN (kept on
download/metadata). Our wiring sets this with two flags in
`adapters/vidnets.js:buildRequest` — `strict_retrieval_accuracy` and
`visual_required`, both false by default (`MUSE_MEDIA_STRICT=0`).
`MUSE_MEDIA_STRICT=1` restores the old hard-required flags; do not set
it without a reason recorded in the commit message. The render wrapper
(`render-vidnets-linux.js:countRealScenes`) counts a scene as real when
Vidnets' own adapter accepted it (`media.verified` in the edit plan, or
a Muse-verified verdict) — an explicit Muse rejection always wins.
Delivery requires accepted real media in every scene.

## 4. Settings

Every knob lives in `.env` / `.env.example`. Defaults are production.

| Setting | Default | What it does |
|---|---|---|
| `MUSE_YOUTUBE_COVERAGE_TARGET` | 0.8 | Fraction of scenes the planner tries to fill with accepted YouTube clips |
| `MUSE_MEDIA_STRICT` | 0 | 0 = filter-best (vision advisory); 1 = hard-required (strict retrieval + visual required) |
| `MUSE_SCRIPT_MAX_DRAFTS` | 2 | Script model calls max (draft 1 + one fix pass) |
| `MUSE_RESEARCH_TIMEOUT_MS` | 180000 | One shared research deadline for all providers |
| `MUSE_REPLAN_MIN_REMAINING_MS` | 600000 | Render budget left required to attempt the one bounded media re-plan |
| `MUSE_RENDER_CONCURRENCY` | 2 (code) / 1 (live `.env`) | Workers sharding ONE video's frames (see §8.4); live pins 1, the tested safe count |
| `MUSE_RENDER_TIMEOUT_MS` | 21600000 | Overall render budget per job (6 h, matches the CMS hard limit) |
| `TMPDIR` / `VIDZEO_TEMP_DIR` | `<runtime>/temp` | Scratch/temp root (never /tmp) |
| `VIDZEO_YOUTUBE_ACCEPTED_COVERAGE_TARGET` | 0.8 | Server-side YouTube coverage target (runtime `.env`) |
| `VIDZEO_AUTO_REQUEUE_INTERRUPTED` | 1 | Server auto-resumes jobs interrupted by a VM reboot |

## 5. Monitoring

`setup.sh` installs the `vidnets-health-monitor` cron at
`~/hooks/scripts/vidnets-health-monitor.sh` and schedules it every minute,
so inbox age is checked within the 60-second operator SLO. Existing installs
must rerun setup to install or refresh it. It checks:
daemon alive + connected, Vidnets `:5010` healthy + memory admission,
CMS reachable + outage duration, inbox-watch hook enabled and recently run, no inbox
request sitting pending past half its TTL, disk/tmp space, and Vidnets memory
admission. It FIXES what
it can itself — restart a dead daemon via `start-daemon.sh`, restart a
dead Vidnets server, mark expired inbox requests — and is SILENT when
healthy. It only messages the user when it cannot fix something or a
job is affected, with the exact problem and what was done. It NEVER
claims, delivers, or restarts a job mid-render.

## 6. Troubleshooting

| Failure | Cause (seen) | Fix |
|---|---|---|
| Script `RESEARCH_INSUFFICIENT` | Zero external claims; providers thin for a niche title | Fail loudly; re-queue a different title or add sources. Do not loop research |
| Inbox request expires unanswered | Muse did not answer within the 120-second request lifetime or the remaining job budget | Run `node muse-agent.js doctor` and check the resident transport or hook operator |
| CMS 502/530 | CMS tunnel flapping | Daemon survives in-process with backoff — wait; do not restart it. Monitor reports outage duration |
| VM reboot | VM restarts kill daemon + server | Health monitor restarts both; server auto-requeues interrupted jobs (`VIDZEO_AUTO_REQUEUE_INTERRUPTED=1`) |
| Low memory / render held | Vidnets holds renders when available memory < ~384 MB estimate | Concurrency 1; wait for memory to free; never raise concurrency on this VM |
| `/tmp` full (512 MB tmpfs) | Scratch landed in /tmp | `TMPDIR`/`VIDZEO_TEMP_DIR` point at `<runtime>/temp`; `start-daemon.sh` prunes stale scratch on start |
| 0 real media / all graphics | Discovery returned 0 candidates (search transport) OR strict flags hard-rejected everything | Check the manifest: if every lane returned 0 candidates, it is a transport problem (DNS/proxy), not strictness. If candidates existed but none survived, check `MUSE_MEDIA_STRICT` and the vision verdicts — filter-best must be on |
| Wrapper fails "0 real-media scenes" | All scenes graphic fallback | One bounded re-plan already ran (or was skipped for lack of budget). Fail the job; re-queue the title only after the cause above is fixed |
| Vision rejects a correct clip | Operator prompt too strict, or a watermarked/wrong-operator frame | The hook prompt is filter-best (reject only clearly-wrong). A wrong-operator frame (e.g. CSX illustrating Amtrak) is correctly rejected |

## 7. Distribution checklist (handing the repo to the next agent)

1. Commit on top of the current HEAD; working tree clean.
2. Package a bundle: `git bundle create ~/workspace/your_files/M2l-<name>.bundle <base>..HEAD` (base = the last commit the user already pushed).
3. Verify in a FRESH scratch clone (NOT /tmp — use `~/workspace/`).
   A range bundle (`base..HEAD`) does NOT contain the base commit, so
   you cannot `git clone` the bundle itself — clone the repo (or copy
   an existing clone), then fetch the bundle into it:
   ```bash
   git clone https://github.com/armughan-zeb/M2l.git <scratch> && cd <scratch>
   git fetch <bundle> HEAD:verify && git checkout verify
   npm install && npm install --prefix scriptit
   npm test          # must pass (60/60 at last check)
   node muse-agent.js doctor   # must RUN its checks; it FAILS in the
   # scratch clone (no .env, no live Vidnets/hook from that path) —
   # that is expected. In the live repo it must PASS.
   ```
4. Give the user the exact two push commands:
   ```bash
   git fetch <bundle> HEAD:<branch> && git push origin <branch>:main
   ```
   (branch name = the bundle name; push order matters if earlier
   bundles are still unpushed — oldest first.)
5. Report: commit hash, bundle path, test results, what changed, what
   is still not perfect. Point the next agent at `rules.md` first.

## 8. Hardening (Nov 10, 2026 — live CMS logs, checkpoints, hosts, workers, RAM, Docker)

### 8.1 Live CMS progress

The wrapper polls Vidnets every ~5 s; its stderr lines are streamed
live (`lib/command.js:onStderrLine`), parsed by
`adapters/video.js:parseRenderProgressLine` (`Vidnets N% — detail`), and
sent to the CMS on every `report()` as stage/%/detail + the last 10
daemon log lines (`MUSE_PROGRESS_LOG_LINES`, max 50, in `lib/cms.js`).
The 20 s heartbeat carries stage/%/detail as a second channel, and the
90 s keepalive re-sends the last reported values with
`advancing=false`. Caveat: the CMS must store the `logs` field — if it
does not, stage/%/detail still work but the log lines need a CMS-side
change. The wrapper's first line can step the CMS 2% → 0% (cosmetic).

### 8.2 Resume contract

Local checkpoints, all atomic (temp file + fsync + rename,
`lib/config.js:writeJsonAtomic`): `work/active-job.json` is the claim
marker — written at claim, cleared only on delivery, CMS 409 (job
settled), or unpair; never cleared on shutdown, crash, or CMS outage.
`work/<job>/job.json` holds payload + digest (a re-dispatch with a
different digest archives the old folder). `work/<job>/progress.json`
is the live stage pointer, rewritten on every progress report (every
stage transition and every Vidnets poll update):
`{jobId, stage, progress, detail, updatedAt}`.
`work/<job>/script.txt` (+ `video.mp4` + `video-meta.json`
bytes/sha256/digest) are reused on resume after validation.
`work/<job>/vidnets-job.json` holds the Vidnets queue id (+
`replanAttempted`); the wrapper re-attaches to it instead of submitting
a duplicate. `work/<job>/vidnets-progress.json` is the wrapper-side
live Vidnets snapshot, rewritten on every poll change. Vidnets server
checkpoints under `work/<job>/vidnets/checkpoints/` (`media_plan.json`,
`pre_render.json`, `render_pending_validation.json`,
`render_complete.json`, `state.json`) are written atomically;
`state.json` is the resume pointer the server gates on. **Resume flow:**
on start, and before claiming anything new, the daemon probes the CMS
with the saved progress from `progress.json`; transient CMS errors
(502/503/530/transport) retry with backoff then defer (marker kept,
daemon keeps polling); only 409 clears the marker, only 401 is fatal.
`executeJob` then reuses the saved script/master and the wrapper
re-attaches to the saved Vidnets job — nothing is re-fetched or
re-rendered from scratch when a checkpoint exists. A CMS reboot/outage
mid-render does not stop the local Vidnets job. SIGTERM/VM reboot never
fails the job and never clears the marker. (Proven: kill/resume test —
SIGKILL at video 45% → resume probed with video 45%, reused the saved
script, re-attached to the saved Vidnets job, delivered.)

### 8.3 Seen-host allowlist (approval cards)

The runtime pops one network approval card per new image host. The
pipeline persists already-fetched hosts in
`~/workspace/vidnets-runtime/seen_hosts.json`
(`VIDZEO_SEEN_HOSTS_PATH` override; seeded with the 15 BNSF-verified
hosts). The universal scraper tags rows `host_seen`; the download
shortlist prefers seen hosts within relevance slack (relevance still
wins); preview/thumbnail rescue only fetches from seen hosts;
successful downloads are recorded back into the store. Demoted from
defaults: `openverse` (504s via proxy), `getty_bing`/`getty_ddg` (1/51
BNSF rows, watermarked) — re-enable via
`VIDZEO_ENABLE_LEGACY_WEB_SEARCH=1`. Proxy routing does **not** avoid
approval cards (search traffic was already proxied when the cards
appeared; pixel downloads go direct via aiohttp `trust_env=False`) — do
not "fix" this by forcing traffic through the proxy. Measured on the
BNSF job: 15 distinct hosts in candidate records (5 pixel hosts, 5
thumbnail-only CDNs); projected 0 new media-phase hosts per fresh job
in the common case. The 25 already-pending cards are NOT cleared by
this — only new ones are prevented. `googlevideo.com` (first YouTube
clip download) and genuinely new publisher hosts can still pop a card.

### 8.4 Parallel Remotion workers — ONE video, N workers

**Semantics (the correction, Nov 10, 2026):** `MUSE_RENDER_CONCURRENCY=N`
shards ONE video's frames across N Chromium workers inside a single
`renderMedia` call; Remotion stitches them into the one output file.
It is NOT "N videos at once" — the Nov 7 number (2 parallel 1080p
renders in 42 s) was separate renders and does not answer this.
Verified in code, Nov 10, 2026:

- `adapters/vidnets.js:98,173-174` — `buildRequest` sends the request
  as BOTH `concurrency` and `semantic_render_concurrency`.
- `vidnets/server/app.py:20728,20807` — the server reads
  `semantic_render_concurrency` from the job config and forwards it
  into the render config.
- `vidnets/server/services/native_render_policy.py:857-864` — the
  policy reads `concurrency` / `remotion_concurrency` /
  `semantic_render_concurrency` and sets `VIDZEO_REMOTION_CONCURRENCY`
  on the Node render subprocess env. (Wiring-gap fix: before it, the
  server only forwarded `semantic_render_concurrency` into the render
  config, so the count never reached the Node script. The running
  server only picks this up on its next restart.)
- `vidnets/scripts/render-remotion-manifest.js:529-546,1024` — the Node
  script reads `VIDZEO_REMOTION_CONCURRENCY` first, then the
  `MUSE_RENDER_CONCURRENCY` aliases, caps by CPU + memory, and makes
  ONE `renderMedia({ ..., concurrency })` call for the composition.
  The sectioned fallback renders frame ranges of the same video
  (`frameRange: [start, end], concurrency: workers`, line 1132-1135)
  and joins the sections ("joining saved video sections").
- `lib/remotionRenderer.js` (native path) has the same semantics:
  one `renderMedia` call with `concurrency` from
  `lib/remotionWorkers.js`.

**Caps (hard, on this 4 vCPU / 7.7 GB VM):** CPU `vCPU−1`, so max **3**
— requesting 5 can never run as 5 here (both resolvers cap it; see
`tests/remotion-workers.test.js`). Memory ~400 MB per 1080p worker
plus a 384–555 MB reserve: 2 workers need ~1.4 GB actually free at
render start, 3 need ~1.8 GB. The chosen count is logged
(`Remotion workers: requested=… chosen=…`); a degraded render still
proceeds, never silently fails. Set
`MUSE_RENDER_CONCURRENCY=N` (aliases: `VIDZEO_REMOTION_WORKERS`,
`MUSE_REMOTION_WORKERS`, `VIDZEO_REMOTION_CONCURRENCY`); code default
**2** (a request only), live `.env` pins **1**, the tested safe count.

**Measured (Nov 10, 2026 — same 30 s 1080p composition, 900 frames,
`scripts/benchmark-remotion-workers.js`, guard
`scripts/wait-and-benchmark-workers.sh`, results in
`work/remotion-worker-benchmark/results.json`):**

| Requested | Effective | Wall (s) | Speedup vs 1 | Peak used (~MB) | Result |
|---|---|---|---|---|---|
| 1 | 1 | 489.7 | 1.00× | 1114 | OK |
| 2 | 1 | 403.6 | 1.21× | 800 | OK — degraded to 1 |
| 3 | 1 | 387.5 | 1.26× | 670 | OK — degraded to 1 |
| 5 | 1 | 384.1 | 1.27× | 992 | OK — degraded to 1 |

**Read this table honestly: it does NOT prove multi-worker rendering
works.** Every level ran with effective **1** worker — at each render
start only 1,063–1,223 MB was actually free (daemon + freshly
restarted server resident), so the memory cap degraded every request
to 1. The 1.21–1.27× "speedup" is NOT parallelism: all four runs were
single-worker, and the first run was cold (Chrome Headless Shell
download + bundle build inside its 489.7 s); the later runs were warm.
The render process tree peaked at ~1,219–1,273 MB even with 1 worker,
so on this box as loaded, 2 workers (~1.4 GB free needed) is just out
of reach and 3 (~1.8 GB) further still. **Tested safe count: 1** —
that is what the live `.env` pins, and it stays. 2/3/5 remain
**unproven** on this VM; re-run the benchmark on a freer box (stop the
daemon first, or a bigger box) before claiming any higher count works.
The CPU cap (max 3 here, 5 can never run as 5) is still only proven in
unit tests, not in a live render.

### 8.5 RAM hygiene

- **Stage cleanup:** each daemon stage (script/render/thumbnail) runs
  in its own process group (`lib/command.js` reports it via
  `onSpawn`); when the stage ends, `lib/stageCleanup.js` SIGTERMs the
  group, SIGKILLs survivors after 5 s, verifies all members are gone,
  and logs the result. An end-of-job sweep runs in `jobRunner`'s
  `finally` blocks. Only recorded groups are ever signalled, and only
  members whose start time is at/after the stage start — PID reuse is
  spared, never killed.
- **Orphan guard:** `node scripts/cleanup-orphans.js` (dry-run by
  default) reaps ffmpeg/Chrome/yt-dlp/Vidnets workers reparented to
  PID 1. It kills only processes provably not owned by a live job:
  dead parent, no live job id in the cmdline, and a stale job id or age
  over `--min-age-minutes` (default 30). Live ids come from
  `--live-job-id`, `MUSE_LIVE_JOB_IDS`, `active-job.json`, or a running
  wrapper. `--kill` with no known live job refuses unless
  `--allow-no-live-job` is passed.
- **Render memory wait:** `wait_for_render_memory()` in
  `vidnets/server/services/render_resource_manager.py` re-checks
  admission every 0.5s (`VIDZEO_REMOTION_MEMORY_POLL_SECONDS`),
  extends the wait in 30 s steps while media workers are alive
  (`VIDZEO_REMOTION_MEMORY_WORKER_EXTEND_SECONDS`, capped by the
  delivery budget), and fails fast 10 s after the last media worker
  exits (`VIDZEO_REMOTION_MEMORY_NO_WORKER_GRACE_SECONDS`). The 384 MB
  floor is unchanged. A memory pause in the wrapper waits 30 s
  (`MUSE_VIDNETS_MEMORY_RETRY_DELAY_MS`) before retrying. If a render
  still pauses with no media worker alive, memory is genuinely short —
  free RAM or wait for another job; retrying will not help.

### 8.6 Docker quickstart (another Muse VM)

1. Unzip `M2l-docker-<head>.zip`, `cd` into the extracted repo root.
2. `./setup.sh` — paste the CMS URL and the agent key created on the
   CMS (Muse agents page). It writes `.env`, builds the image, and
   pairs the worker (`./data/.muse-agent.json`). If already paired it
   asks before re-pairing.
3. `docker compose up -d`, then `docker compose logs -f`.
4. If the worker is not the Active Worker, select it as Active Worker
   on the CMS before it can claim jobs.
5. Health: `docker compose exec worker node muse-agent.js doctor`.
   Stop: `docker compose down` (state survives in `./data`).
- Host hook step (manual, untested): the host Muse VM's
  `vidnets-inbox-watch` hook must answer inbox requests via
  `docker exec vidzeo-muse-worker bash /app/vidnets/VIDNETS_AGENT.sh --runtime-root /data/runtime answer <id> <file>`
  with its inbox path set to `<extracted>/data/runtime/temp/agent-bridge`.
  Until then the daemon fails closed at the start gate — expected, not
  a bug.
- Troubleshooting: logs first; Vidnets log at
  `./data/runtime/logs/agent-service.log`; 401 means re-pair with a
  fresh key (`./setup.sh`); CMS 502/530 needs no restart.
- BUILD UNVERIFIED as of Nov 10, 2026 (no docker on the reference VM).
