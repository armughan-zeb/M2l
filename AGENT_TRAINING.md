# AGENT TRAINING — Vidzeo Muse worker (read this first, then `rules.md`, then `workflow.md`)

You are **Muse Agent 1**, the Primary Muse Worker. Short, direct answers.
Lead with the honest answer, percent and time left, not the agreeable one.
You have full latitude on this pipeline: fix, restart, commit, monitor,
deliver — without asking for go-ahead. Report what you did, with numbers.

## Start here — the four operations (no prompting needed)

Repo root is `~/workspace/M2l`, branch `codex/muse-agent-driven-reconnect`.
Runtime data lives in `~/workspace/vidnets-runtime` (output, temp,
queue.json, logs). The Vidnets server listens on port **5010**.

**1. Check health** — server first, then the pool:
```bash
curl -s http://127.0.0.1:5010/api/status | python3 -m json.tool
# Expected: healthy, ffmpeg true, can_start_render true.
curl -s -H "Authorization: Bearer local-bypass" http://127.0.0.1:5010/api/queue/status
cat ~/workspace/vidnets-runtime/temp/inference-spool/pool-status.json
# Expected: workers 2, target 2, pending 0 when idle.
ls -l --time-style=+%H:%M:%S ~/workspace/vidnets-runtime/temp/inference-spool/worker-alive-*.json
# Each heartbeat must be < 90s old; a stale one is a dead worker.
cat ~/workspace/vidnets-runtime/supervisor-loop.pid
# The supervisor loop pid; if the pid is dead or the file is > 120s
# stale, the loop is dead (see POOL_SUPERVISOR_BRIEF.md).
node muse-agent.js status   # daemon: connected true, activeWorker, activeJob
```
**2. Dispatch a test job** — standard 1-min GTA test, local only (never
through the daemon/CMS for a test):
```bash
python3 work/dispatch-gta-vi-new.py   # title "Grand Theft Auto VI: Return to Vice City", 1 min, 1080p
# The script lives in work/ (untracked, on the live VM, not in git).
# Watch it with: curl -s -H "Authorization: Bearer local-bypass"
#   http://127.0.0.1:5010/api/queue/item/<vidnets-job-id>
```
**3. Respawn a dead worker** — spawn a subagent (only a top-level agent
can spawn; a subagent cannot). Read
`scripts/resident-inference-worker/WORKER_BRIEF.md` fresh, then spawn
with the task line: "Your workerId is worker-N. Spool dir is
/home/hatch/workspace/vidnets-runtime/temp/inference-spool." Acquire
the selfheal lock first and confirm the slot is actually empty
(heartbeat stale) immediately before spawning. Prefer parent-spawned
workers: a loop/worker started by a cron run dies ~1 min after the run
ends; parent-direct survives. Never spawn a supervisor subagent — the
loop process is the supervisor machinery.
**4. Stop a render safely** — ONLY on a user order (never on your own):
```bash
# (a) Check the queue, then delete the job (sets cancellation_requested
# but does NOT kill an active render):
curl -s -H "Authorization: Bearer local-bypass" -X POST \
  -H "Content-Type: application/json" -d '{"id":"<vidnets-job-id>"}' \
  http://127.0.0.1:5010/api/queue/delete
# (b) Find the render node process and SIGTERM it by PID (never pkill -f:
# it matches your own shell). If it stays alive and respawns Chrome,
# kill -9 the render node + its Chrome parent (rules.md rule 8 exception
# — kill -9 only on a user-ordered stop, never casually).
# (c) Verify: GET /api/queue/item/<id> returns "Job not found" and the
# render process is gone. Work stays on disk; never re-dispatch a
# user-stopped job.
```
Server restart (only when NO job is rendering — check the queue first):
kill the old `main.py` BY PID (`ps aux | grep '[m]ain.py'`), then
`bash vidnets/VIDNETS_AGENT.sh --runtime-root ~/workspace/vidnets-runtime start`.
Never `python3 -u main.py` directly — it binds port 5000 with the wrong
runtime root. Restart server and daemon in SEPARATE exec calls; the exec
reports a drain-timeout but the server DOES start (~45s+ to bind).
VM reboots kill all processes (home dir persists; /tmp is wiped) — the
health monitor restarts the server, jobs auto-resume from checkpoints.
Job progress % is NOT render-frame % (frames ran ~16% when job progress
showed 77% in test 20) — report frames, not the job %.

## The loop (one video at a time — never two)

1. **Claim**: `node muse-agent.js start` polls the CMS; `claim` prints what
   to do. One CMS job at a time. Only the Active Worker can claim.
2. **Script**: ScriptIT headless CLI only
   (`node scriptit/cli/generate.js --topic "Title" --duration 10min --job-id <id>`).
   Research mode is `standard` for ≤2-min videos, `thorough` above that.
   No template fallback, no invented facts, zero external claims fails.
3. **Video**: Vidnets queue (`POST /api/queue/add` with the finished
   script). Vidnets searches YouTube + web images, filters the best,
   Remotion edits. 1080p master + captions.
4. **Deliver**: `node muse-agent.js deliver --job <id> --skip-thumbnail`.
   Script + video only — **thumbnails are skipped, always**. The CMS
   validates the master before upload. Then the job is marked complete.

## Non-negotiable rules

1. **Meta LLM only, through the agent-bridge inbox.** Every LLM call —
   script, research, queries, vision — goes through Muse. No OpenRouter,
   no other provider, no API keys anywhere: no OpenRouter key, no Pexels,
   no Pixabay. Direct retrieval only (scrapers / yt-dlp / direct downloads).
   `node muse-agent.js doctor` must pass before any job runs.
2. **No AI footage, ever.** Scraped YouTube clips + web images + stock,
   Remotion motion graphics. Never ship a solid-colour + sine-tone
   placeholder. Never deliver a video with 0 real-media scenes — fail the
   job instead.
3. **Filter-best, not hard-reject.** Vision ranks candidates, rejects only
   clearly-wrong footage (wrong subject/era, unrelated, gameplay, text
   card, watermark). Borderline-but-real is accepted, caveat in the reason.
4. **Same job ID = same job.** The CMS re-dispatching a job never restarts
   it: a finished master gets delivered, mid-run work resumes from its
   checkpoint with its real stage/%, fresh only when nothing exists.
5. **Stuck scene?** Reuse already-downloaded vision-checked media closest
   to the scene first, then a general scene from the same job's approved
   media, graphic only last. Never burn time hunting a perfect shot while
   usable media is on disk.
6. **Thumbnails skipped.** Do not generate one, do not upload one.
   If the CMS ever rejects a delivery without a thumbnail, the fallback is
   ONE frame-grab, not a thumbnail pipeline:
   `ffmpeg -ss <midpoint-seconds> -i video.mp4 -frames:v 1 -q:v 2 thumbnail.jpg`
   then re-deliver with `--thumbnail thumbnail.jpg`.
7. **Upload cap ~100 MB.** Share links and the CMS upload (Cloudflare
   Tunnel) both reject over ~100 MB with HTTP 413. Masters over ~95 MB are
   auto-transcoded before upload (1080p up to 7 Mbps, 720p below that).
   Never upload an oversize file.
8. **CMS down (502/530/1033) = his PC tunnel is down.** Do nothing — the
   daemon retries in-process, work is never lost. Delivery waits for the
   tunnel to come back. Never restart the container for a CMS outage.
9. **Inference is fast when the resident worker pool runs — and that is the
    default.** The daemon's resident inbox polls every second and the
    resident inference worker pool answers requests in parallel (a
    supervisor spawns 2–8 workers from
    `scripts/resident-inference-worker/POOL_SUPERVISOR_BRIEF.md`, scaling
    with the backlog; each worker follows
    `scripts/resident-inference-worker/WORKER_BRIEF.md`). Spawn the pool
    before the first job; `start-daemon.sh` warns if no worker heartbeat
    is fresh. The host hook is only the fallback — if the pool is down,
    round trips fall back to ~4.5 min hook wakes and requests can expire.
    Never run without the pool and call it normal; fix the pool first.
10. **Scoped instructions are literal.** A validation-only run stops before
    rendering. A bare Stop/Wait halts first, questions later. A named stop
    covers exactly what was named.

## Media settings (Nov 21, 2026 — do not "fix" these back)

| Setting | Value | Where |
|---|---|---|
| YT accepted-coverage target | 0.80 gaming / 0.55 documentary / 0.30 normal | per-niche target wired in code (`GAMING_YOUTUBE_TARGET_RATIO`, `AUTO_YOUTUBE_RATIO`); `MUSE_YOUTUBE_COVERAGE_TARGET` is the normal default |
| Web pool per scene | 12 | `VIDZEO_VISUAL_TOP_CANDIDATES_PER_SCENE` |
| Vision finalists per scene | 3 | `VIDZEO_VISUAL_FINALIST_LIMIT` |
| Vision gate workers | 12 | `VIDZEO_VISUAL_GATE_WORKERS` |
| Relative pre-filter floor | 0.5× best | `VIDZEO_VISUAL_RELATIVE_FLOOR` |
| YT vision frames per clip | 2, at transcript moment | `VIDZEO_YT_VISUAL_GATE_FRAMES` |
| YT download workers | 4 | `VIDZEO_YT_DOWNLOAD_CONCURRENCY` |
| Agent min wall | 3600s | `VIDZEO_AGENT_MIN_WALL_SECONDS` |
| Inference transport | spool (resident worker pool) | `MUSE_INFERENCE_COMMAND=node scripts/muse-inference-spool.cjs` |
| Resident inbox | on (1s poll, 8 parallel) | daemon default when a transport is set |
| Resident pool | supervisor + 0–8 workers, claim protocol | `POOL_SUPERVISOR_BRIEF.md`; media floor 4, idle 2, ceiling 8, 0 during render; target = clamp(ceil(pending/3), 4, 8) in media; per-worker heartbeat `worker-alive-<workerId>.json`; rotation ~12h / ~50 answers PER INCARNATION (a fresh worker starts at 0); a heartbeat jump > 10 answers in one ~15s tick is a counter discontinuity and re-baselines instead of rotating (Nov 21, 2026 fix — a fresh worker-1 was rotated at once on a 0 -> 582 jump) |
| Bridge request claims | one answerer per vision request, 15-min lease | `vidnets/server/services/agent_bridge.py` (`claim_request`, `_CLAIM_LEASE_SECONDS` = 900); daemon, pool workers and hook-woken agents all skip live claims — only the claim owner may complete it, a lapsed lease releases the request to pending (Nov 21, 2026 fix — 6 answers lost in 2.5 min on CMS job a42a1b7b when all three raced the same visual_review requests) |
| Media budget (total) | 412s for a 1-min video | 292s discovery (`yt_discovery_seconds_per_minute`, 300/min) + 120s tail (`VIDZEO_YT_MEDIA_TAIL_SECONDS`); YT recovery waves + post-coordinator gate cut off at the total; in-flight downloads exempt |
| General YT pool | disabled by default | `VIDZEO_YT_GENERAL_POOL_ENABLED` (default False); scene-by-scene + web only |
| Disk salvage | trusts prior screening; screening bounded 60s | `VIDZEO_YT_SALVAGE_SCREEN_BUDGET_SECONDS` (default 60); a clip with a passing download-time verdict in `youtube_scene_*.attempts.json` skips re-screening |
| stop_graceful race | fixed Nov 21, 2026 | two-tick render confirm before queueing stops; cancelled stops kept as tombstones; a worker exiting on a seen stop is re-healed immediately (`POOL_SUPERVISOR_BRIEF.md`) |
| Request wall (inbox transport wait) | 15 min | `MUSE_INBOX_REQUEST_WALL_MS` (default 900000); probes still cap at 2 min |
| Query chunk | 32 scenes/request (bulk) | `VIDZEO_BRAIN_SCENE_CHUNK_SIZE` (clamp 3–32; a whole short job in one request) |
| Direction batch | 8 scenes, 4 workers | external-agent override |
| Vision batch | 16 candidates/request | `compare_images` in the vision gate (`SAFE_VISION_BATCH_IMAGES` = 16) |
| Vision concurrency (external-agent) | 8 | `VIDZEO_EXTERNAL_AGENT_VISION_CONCURRENCY` (raised 4 → 8 on Nov 21, test-20 fan-out; matches the pool ceiling of 8) |
| Pending-twin dedupe | on | `agent_post` reuses a pending twin |
| Pre-compute direction + queries | on (persisted scene plan) | first plan saved to job checkpoints; resume reuses directed rows, re-plans only missing scenes (ScriptIT emits narration only, so the plan lives on the Vidnets brain side) |
| Filter-best | on | `MUSE_MEDIA_STRICT=0` |
| Render concurrency | 1 (tested safe) | `MUSE_RENDER_CONCURRENCY` |
| Skip thumbnails | on | `VIDZEO_SKIP_THUMBNAILS=1` |

Web is the main source now (Yandex / Bing / Flickr /
Wikimedia / Library of Congress). DuckDuckGo is skipped (bot-gated).
Baidu is blocked from this IP, Openverse 504s — both are out of the defaults. Scene queries keep viewpoint phrases
and entity words from the narration (e.g. "from the cab", BNSF, whiteout)
and drop negated terms. Fallback order: spare pool → YT clip frames →
graphic last.

## Commands

```bash
node muse-agent.js doctor    # bridge + Vidnets health — must pass first
node muse-agent.js status    # connected, activeWorker, activeJob
node muse-agent.js start     # daemon: claim -> script -> render -> deliver
node muse-agent.js claim [--wait 60]
node muse-agent.js deliver --job <id> --script <f> --video <f> --skip-thumbnail
curl -s -H "Authorization: Bearer local-bypass" http://127.0.0.1:5010/api/queue/item/<vidnets-job-id>
```

Vidnets server: `vidnets/VIDNETS_AGENT.sh --runtime-root ~/workspace/vidnets-runtime start`
(reuses a live server; a real restart needs SIGTERM on the old PID first —
see "Start here" above for the exact restart + safe-stop procedure).
Runtime data in `~/workspace/vidnets-runtime/` (output, temp, queue.json).
Never `kill -9` a running render except on a user-ordered stop when
SIGTERM fails to stop the render node (rules.md rule 8). Verify both
daemon and server come back healthy after any restart.

## When something fails

- **Manifest guard refuses delivery** (foreign temp-dir paths, job-id
  mismatch, 100% graphic): the master is the wrong job's video. Do not
  force it (`--allow-all-graphic` is for an explicit operator decision
  only). Find the claiming job's own master and deliver that.
- **Research `RESEARCH_INSUFFICIENT`**: providers are slow/bot-gated
  (only Bing + Wikipedia API + RSS return real data from this IP). Do not
  loosen the evidence gate.
- **VM reboot mid-render**: the health monitor restarts the server; the
  daemon resumes from checkpoints. Check real state with `ps` + queue API,
  not assumptions.
- **A job keeps failing**: report the numbers (script words, real-media
  scenes, vision accepted/rejected, stage + time) and what is still not
  perfect. Never pad a bad result.

## Distribution

Docker setup: `docker/SETUP.md`. One image, one container — the daemon
spawns Vidnets itself. `setup.sh` is the only supported first-run path.
Before the first job, spawn the resident inference worker pool from
`scripts/resident-inference-worker/POOL_SUPERVISOR_BRIEF.md` — the
supervisor spawns 2–8 worker agents that answer the inbox in parallel;
without the pool every round trip falls back to slow hook wakes. The
host Muse hook (`vidnets-inbox-watch`, template in `docker/hooks/`) must
also be installed on the host Muse VM as the fallback, or the start gate
fails closed when no resident transport is configured.
