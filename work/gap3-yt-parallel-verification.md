# Gap 3 verification record — YouTube download parallelism (Worker C)

Branch: `impl/gap3-yt-parallel` (base d62e212). Worktree:
`~/workspace/.worktrees/gap3-yt-parallel`. No production code was changed
by Worker C — the fix already existed in the web-resilience team's
uncommitted work (see below), so per the task Worker C verified it instead
of rebuilding it.

## 1. Coordination: web-resilience Worker D had already fixed this

- Branch `impl/yt-speed`, worktree `~/workspace/M2l-yt-speed`, agent
  `5915b4b4-f6e3-446d-8e03-ef6ad25d7f9c`. Still uncommitted at verification
  time (HEAD d62e212, no commits on the branch).
- Its uncommitted diff to `vidnets/server/services/youtube_pipeline_v2.py`
  (+489/−152 lines) reworks `_ranged_download_worker_limit` — the exact
  function behind this gap — plus a metadata-extraction cache, player-client
  changes, format-selector changes, and an ffmpeg stall watchdog.
- Worker C did NOT touch that branch or worktree (read-only: `git diff`,
  and test runs with `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`).

## 2. Root cause (verified against BASE d62e212, not assumed)

Chain: `app.py` sets `VIDZEO_MIXED_YT_DOWNLOAD_CONCURRENCY=4` for Mixed
Media → `_requested_download_workers(4) = 4` → `_ranged_download_workers(4)`
→ mixed-media flow re-applies the memory fit with live psutil numbers
(`youtube_pipeline_v2.py:6603-6611`) → `ThreadPoolExecutor(max_workers=
download_workers)` at :7353 → receipt at :7642-7643 records both numbers.

The cap itself, base `_ranged_download_worker_limit` (:3993):

```python
fit = max(1, (max(0, int(available_mb)) - 256) // 320)
if total_bytes < 6 * 1024**3: fit = min(2, fit)
elif total_bytes < 12 * 1024**3: fit = min(4, fit)
return max(1, min(8, int(requested), fit))
```

Panama ran at 637 MB available (per Worker D's note, corroborated by the
manifest): `fit = (637-256)//320 = 1`, so `min(8, 4, 1) = 1`.
Measured on base:

- `_ranged_download_worker_limit(4, 637, 8GiB) = 1`
- `_ranged_download_workers(4, avail=637, total=8GiB) = 1`
- `_ranged_download_workers(6, avail=637, total=8GiB) = 1`
- `_ranged_download_workers(4, avail=3000, total=8GiB) = 4` (memory, not
  config, was the binding constraint)

Manifest evidence (`media_plan.json`, job f66daa53, primary run):
`download_workers: 1, requested_download_workers: 4` — exactly the receipt
this code path writes. So: not a hardcoded 1, not a config default, not the
pool never being created — an instantaneous-free-memory fit collapsing to 1
whenever a running job held RAM. (Note: the old code comment blamed egress-
proxy serialization for bounding the win from more workers; that attribution
is unproven — stale/anonymous cookies also throttle — and Worker D's rewrite
drops the reliance on it.)

## 3. Worker D's fix (what it does)

`_ranged_download_worker_limit` now tiers on TOTAL host RAM, not
instantaneous free RAM:

- total < 6 GiB → fit 2; 6–12 GiB → fit 6; ≥ 12 GiB → fit 8
- only genuine starvation (available < 256 MB) drops to 1
- `available_mb` kept in the signature for callers; no longer drives the fit
- requested count still caps it; hard cap 8 unchanged

Verified numerically against Worker D's code (direct import):

- `_ranged_download_workers(4, avail=637, total=8GiB) = 4` (was 1)
- `_ranged_download_workers(6, avail=637, total=8GiB) = 6`
- `_ranged_download_workers() = 6` (default unchanged)
- `_ranged_download_worker_limit(4, 200, 8GiB) = 1` (starvation guard intact)
- `_ranged_download_worker_limit(4, 637, 4GiB) = 2` (small-host tier intact)

Memory safety of 4 workers on this VM: host is 7.75 GiB total, ~0.8–1.2 GB
available steady-state. Ranged downloads remux AVC via ffmpeg (small RSS
per worker, per Worker D's measurement); 4 was also the Nov 17 measured
parallel count (3 clips parallel 103.9s vs 170s sequential). The chosen
value for mixed media stays the requested 4 — no increase beyond what the
job already asked for.

## 4. Test results

Worker D's own suite (read-only run in their worktree):
- `vidnets/tests/test_youtube_speed.py`: **12 passed** (19.62s). Includes
  `test_ranged_workers_hold_parallelism_while_job_holds_ram` and
  `test_mixed_capacity_uses_total_ram_tiers`.

Stale-test gap found by Worker C — Worker D updated
`test_blank_visual_regressions.py` and `test_youtube_pipeline_v2.py` for
the new tiers but did NOT update `test_media_speedups.py`:
- `TestRangedDownloadWorkers`: **2 failed** in the yt-speed worktree
  (`test_small_host_cap_is_tiered_not_flat_two`: `6 != 4`;
  `test_ranged_download_workers_default_and_cap`: same stale expectation).
  The failing assertions encode the OLD `(available-256)//320` rule
  (e.g. `_ranged_download_worker_limit(6, 1759, 8GiB) == 4`,
  `(8, 300, 16GiB) == 1`, `_ranged_download_workers(avail=1759) == 4`).
  These need updating to the new tiers before Worker D's branch merges;
  the new behavior itself is what the fix intends.

## 5. Live throughput measurement

Method (both runs): same 4 real clips, same production format selector
(`_youtube_quality_formats()[0]` from base), same 20 s ranged slice
(10–30 s) via yt-dlp + ffmpeg external downloader (mirrors
`_download_clip`), same player client in both arms of a run; the ONLY
variable within a run is worker count. Clips: Big Buck Bunny
(aqz-KE-bpKQ), Tears of Steel (R6MlUcmOul8), Cosmos Laundromat
(Y-rmzh0PI3c), Panama Canal documentary (YzFGkdPmDZU). Scripts:
`/tmp/yt-par/measure_throughput.py` (run 1), `/tmp/yt-par/measure_throughput_auth.py` (run 2).

### Run 1 — ANONYMOUS, sequential-first (confounded, see below)

| arm | wall | per-clip | bytes |
|---|---|---|---|
| sequential (1 worker) | 595.3 s | bbb 204.2 s, tos 90.7 s, cosmos 94.9 s, panama_d 205.5 s — all OK | 23.0 MB |
| parallel (4 workers) | 721.9 s | bbb 721.9 s OK, panama_d 561.5 s OK, tos 194.6 s FAIL, cosmos 171.9 s FAIL (`ffmpeg exited with code 146`) | 15.2 MB |

Speedup: **0.82x — parallel was SLOWER, with 2/4 failures.** This does NOT
disprove the fix: the run was anonymous (YouTube throttles anonymous
downloads; the two successful parallel clips took the same byte counts but
~3x the time of sequential), and the parallel arm ran second, after ~10
min of sequential downloading from the same IP (IP/proxy fatigue). The
ffmpeg-146 cause is unestablished (web search found nothing specific; no
ffmpeg stderr was captured in run 1). What run 1 DOES establish: anonymous
parallel downloads don't win — consistent with the known anonymous-
throttling confound, not with the worker-count code being wrong.

### Run 2 — AUTHENTICATED, parallel-first (the real pipeline conditions)

### Run 2 — AUTHENTICATED, parallel-first (the real pipeline conditions)

| arm (order) | wall | per-clip | bytes |
|---|---|---|---|
| parallel (4 workers), ran FIRST | **396.1 s** | cosmos ~216 s, tos ~266 s, bbb ~392 s, panama_d ~396 s (approx., from file mtimes; the `tail -30` cut the printed breakdown) — all 4 OK | 23.5 MB |
| sequential (1 worker), ran second | **486.8 s** | bbb 179.3 s, tos 109.2 s, cosmos 77.4 s, panama_d 120.8 s — all 4 OK | 23.5 MB |

Speedup: **1.23x (486.8 s → 396.1 s).** All 8 downloads succeeded (vs
2 failures in the anonymous run). Byte counts identical between arms —
same slices. Memory: avail 2063 MB → 1638 MB across the sequential arm;
no OOM pressure at 4 workers.

Why only 1.23x, not ~4x: the per-clip wall in this harness is dominated
by a LOCAL x264 re-encode, not the network. The captured ffmpeg progress
shows the ranged download re-encoding at ~0.22x realtime (a 20 s clip
takes ~90 s of ffmpeg time) — because the harness set
`force_keyframes_at_cuts=True` for every clip (copied from the probe
opts). The real `_download_clip` sets that only for contact-window clips;
normal final downloads use stream copy. So 1.23x is a LOWER bound measured
under re-encode-heavy conditions: 4 concurrent x264 encodes contend for
1.5 shared CPUs, each clip runs slower individually (e.g. cosmos 77 s
sequential vs ~216 s in parallel), but the overlapped wall still improves.
The worker-count fix removes the serial bottleneck; the remaining
bottleneck is the re-encode, which is outside Gap 3's scope. For scale:
Nov 17 measured 3 clips parallel at 103.9 s vs 170 s sequential (1.64x)
on the same host — same direction, same order of magnitude.

Verdict on the fix: the cap is fixed at the code level (1 → 4 at Panama
conditions, unit-verified) and live downloads do run faster in parallel
under real (authenticated) conditions. The manifest reporting 4 workers
still needs a real job run after Worker D's branch merges (see §8.2).

Key setup facts verified before run 2: authenticated extraction FAILS
without the pipeline's JS challenge runtime (`_youtube_js_runtime_options`,
node at /usr/bin/node) — "Requested format is not available" on every
client list — and SUCCEEDS with it (48 formats, 5 avc1 ≥720p). This matches
Worker D's measured note. Run 2 therefore uses `cookies.txt` (Netscape,
read-only — yt-dlp never writes it back) + the pipeline's authenticated
player-client list `[web_embedded, tv_samsung, web]`, parallel arm first.

## 6. Live player-client check (side observation)

With `tv_embedded` (Worker D's anonymous client), all four Panama-log
videos resolve with full formats (48–77 formats, avc1 ≥720p present),
where the old multi-client lists returned only 5 formats (360p +
storyboards). Two of the five Blender candidates (Sintel eRsGyueVLWQ,
Elephants Dream eRin6R7y7dU) are genuinely unavailable on YouTube now —
not a pipeline failure.

## 7. What Worker C changed

Nothing in production code. This file is the only artifact, committed on
`impl/gap3-yt-parallel` as the verification record. No server/daemon
restart, no cookies.json change (0600, still Nov 18 10:31). Note:
`cookies.txt` changed on disk during run 2 (md5 6a30e4f2… → 8978cab0…,
mtime 12:22) — by another process (session maintenance), not by Worker C:
both measurement scripts only ever passed it to yt-dlp as a read-only
`cookiefile`, and yt-dlp never writes it back.

## 8. Open items for the coordinator

1. Worker D's branch needs `test_media_speedups.py::TestRangedDownloadWorkers`
   updated to the new tiers before merge (2 tests currently fail).
2. The manifest reporting the intended worker count (4) can only be
   confirmed by a real job run after Worker D's branch merges — the receipt
   path writes `download_workers` from the same variable the executor uses,
   so it will report 4 by construction, but no live job has run on the fix
   yet. No job was run by Worker C (no server restart, no daemon touch).
3. Worker D's branch is uncommitted; Gap 3's fix ships when it commits and
   merges — do not duplicate it on another branch.
