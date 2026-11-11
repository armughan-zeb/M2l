# RULES — non-negotiable. Read first, follow verbatim.

These rules are the standing orders for this repo (Vidzeo / M2l, the
documentary production pipeline). They are absolute. Do not reinterpret
them, do not work around them, and do not ask the user to re-approve them.

## 1. The architecture is Vidnets as built — never rebuild it

LLM writes search queries from the script's scenes → Vidnets searches
YouTube clips + web images → Vidnets FILTERS THE BEST (ranks candidates,
keeps the best real media per scene) → Remotion edits. That is the whole
architecture. Never redesign the flow, never add a parallel media path,
never replace a Vidnets stage with your own code. If a stage misbehaves,
fix how we USE it (flags, prompts, settings), not the stage itself.
`workflow.md` documents the flow; `HYBRID_WIRING.md` documents what was
misused in the past and what the correct use is.

## 2. All LLM work is Muse (Meta LLM) through the agent-bridge inbox

Every LLM call — script, research, scene queries, scene direction,
classification, editorial beats, vision — goes through the agent-bridge
inbox with `llm_provider: external_agent`. No OpenRouter, no Qwen, no
other provider, no other key — anywhere, including tests and fallback
chains. `node muse-agent.js doctor` must pass before any job runs.
`MUSE_INFERENCE_COMMAND` stays UNSET (the hook operator answers inbox
requests; a second answerer double-answers and fails).

## 3. No AI-generated footage, ever

Documentaries use scraped YouTube clips + web images + Pexels/Pixabay
stock with Remotion motion graphics. Never generate footage, never ship
a solid-colour + sine-tone placeholder, never ship a graphics-only video.

## 4. No template scripts; no invented facts

ScriptIT CLI in agent-bridge mode is the ONLY script engine
(`scriptit/cli/generate.js`). Template fallback is opt-in only
(`MUSE_SCRIPT_ALLOW_TEMPLATE_FALLBACK=1`) and must never be enabled in
production. Zero external claims fails `RESEARCH_INSUFFICIENT`.
Synthetic dossier claims are context only — they never satisfy the
evidence gate and never count toward publishability.

## 5. Filter-best, not hard-reject — one hard delivery rule

Vision RANKS candidates and only rejects clearly-wrong footage (wrong
subject, wrong era, unrelated content, gameplay, text card, watermark).
Borderline-but-real footage is ACCEPTED, with the caveat in the reason.
Per-candidate strictness stays filter-best (`MUSE_MEDIA_STRICT=0`).
The ONLY hard rule is delivery: **never deliver a video with 0
real-media scenes — fail the job instead.** That is a delivery rule,
not a per-candidate rejection rule.

## 6. Never touch `.muse-agent.json`

It holds the worker identity and pairing. Never read its token, never
print it, never edit it, never commit it.

## 7. Never claim a live CMS job in tests

Test jobs are local only (`render-vidnets-linux.js` with a test jobId,
submitted straight to the Vidnets queue — never through the daemon, never
through the CMS). Never deliver anything to the CMS from a test. Never
restart, kill, or interfere with a live job, the daemon, or the Vidnets
server while a job is running.

## 8. Restarts only the documented way

Daemon: `nohup ./start-daemon.sh >> daemon.log 2>&1 &` (detached —
foreground runs die with the shell). Vidnets server: `VIDNETS_AGENT.sh
start` (reuses a live server; a real restart needs SIGTERM first).
Never `kill -9` a running render. Verify both come back healthy after
any restart (doctor + `:5010` status + daemon connected).

## 9. Secrets only via secure entry, never in the repo

GitHub tokens, Pixabay/Pexels keys, and any API key come only via the
secure entry flow. Never write a key into a file, a log, a commit, or a
message. `.env.example` holds key NAMES only; `.env` is gitignored.

## 10. Act autonomously and report

On this pipeline you have full latitude: fix, restart, commit, monitor,
deliver — without asking for go-ahead. Report what was done, with
numbers (script words, real-media scenes, vision accepted/rejected,
time). Say plainly what is still not perfect. Never pad a bad result.

## 11. Parallel renders mean ONE video, N workers — never N videos

`MUSE_RENDER_CONCURRENCY=N` shards ONE video's frames across N
Chromium workers inside a single `renderMedia` call; Remotion stitches
them into the one output file. It is NEVER "N videos at once". The
count is capped at render start by CPU (`vCPU−1`, so max 3 on this
4-vCPU box — 5 can never run as 5 here) and by available memory
(~400 MB per 1080p worker, 384 MB reserve): a request the box cannot
hold auto-degrades, is logged, and still proceeds — never silently
fails, never OOMs a live job. Code default 2 is only a request — the live
`.env` pins **1**, the TESTED safe count: the Nov 10, 2026 benchmark
(`scripts/benchmark-remotion-workers.js`, same 30 s 1080p comp at
1/2/3/5) degraded every level to effective 1 worker (only ~1.1–1.2 GB
actually free at render start), so 2/3/5 are still UNPROVEN in a live
render — do not claim them. Test numbers live in `workflow.md` §8.4;
re-run the benchmark on a freer box before raising the count.

## 12. Docker distribution — one image, one container

`docker/Dockerfile` + `docker-compose.yml` + `setup.sh` distribute the
whole worker. One image, one container: the daemon spawns Vidnets itself
via `VIDNETS_AGENT.sh --runtime-root /data/runtime` — never split daemon
and Vidnets into separate services. `setup.sh` is the only supported
first-run path: it prompts for the CMS URL (default
https://cms.vidnets.com) + agent key (`muse_...` token or `MUSE-...`
code) and pairs via `node muse-agent.js pair` in a one-off container.
The agent key is NEVER written to `.env` or any file by setup; it lives
only in `./data/.muse-agent.json`. Muse inference stays compulsory in
the image (`MUSE_INFERENCE_COMPULSORY=1`; entrypoint refuses to start
with any OpenRouter key). Known deviations from the reference VM: ffmpeg
6.1.1 (apt) vs Hatch-patched 8.1.2; `/opt/hatch/bin/tts` not shipped
(edge-tts instead); `start-daemon.sh` is NOT used in the container
(`docker/entrypoint.sh` replaces it). BUILD UNVERIFIED as of Nov 10,
2026: docker is not installed on the reference VM; the image has never
been built. Build on a Docker host before trusting it for production.
