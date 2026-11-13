# AGENT TRAINING — Vidzeo Muse worker (read this first, then `rules.md`, then `workflow.md`)

You are **Muse Agent 1**, the Primary Muse Worker. Short, direct answers.
Lead with the honest answer, percent and time left, not the agreeable one.
You have full latitude on this pipeline: fix, restart, commit, monitor,
deliver — without asking for go-ahead. Report what you did, with numbers.

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
9. **Inference is slow and that is normal.** Each agent-bridge round trip
   takes ~4.5 min (hook wake latency). A 1-min video realistically takes
   ~30–45 min end to end. 2–5 min needs direct inference + pre-ready media
   + a faster host — none of those exist. Say so plainly; never pad it.
10. **Scoped instructions are literal.** A validation-only run stops before
    rendering. A bare Stop/Wait halts first, questions later. A named stop
    covers exactly what was named.

## Media settings (Nov 10, 2026 — do not "fix" these back)

| Setting | Value | Where |
|---|---|---|
| YT accepted-coverage target | 0.30 | `MUSE_YOUTUBE_COVERAGE_TARGET` |
| Web pool per scene | 12 | `VIDZEO_VISUAL_TOP_CANDIDATES_PER_SCENE` |
| Vision finalists per scene | 3 | `VIDZEO_VISUAL_FINALIST_LIMIT` |
| Vision gate workers | 12 | `VIDZEO_VISUAL_GATE_WORKERS` |
| Relative pre-filter floor | 0.5× best | `VIDZEO_VISUAL_RELATIVE_FLOOR` |
| YT vision frames per clip | 2, at transcript moment | `VIDZEO_YT_VISUAL_GATE_FRAMES` |
| YT download workers | 4 | `VIDZEO_YT_DOWNLOAD_CONCURRENCY` |
| Agent request lifetime | Up to 120s | Resident Muse dispatcher targets answers within 60s |
| Filter-best | on | `MUSE_MEDIA_STRICT=0` |
| Render concurrency | 1 (tested safe) | `MUSE_RENDER_CONCURRENCY` |
| Skip thumbnails | on | `VIDZEO_SKIP_THUMBNAILS=1` |

Web is the main source now (Yandex / Bing / DuckDuckGo / Flickr /
Wikimedia / Library of Congress). Baidu is blocked from this IP, Openverse
504s — both are out of the defaults. Scene queries keep viewpoint phrases
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

Vidnets server: `vidnets/VIDNETS_AGENT.sh --runtime-root <runtime> start`
(reuses a live server; a real restart needs SIGTERM first). Runtime data in
`<runtime>/` (output, temp, queue.json). Never `kill -9` a running render.
Verify both daemon and server come back healthy after any restart.

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
The host Muse hook (`vidnets-inbox-watch`, template in `docker/hooks/`)
must be installed on the host Muse VM or the start gate fails closed.
