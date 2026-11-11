'use strict';

/**
 * Benchmark: ONE video rendered by N Remotion workers in parallel.
 *
 * Semantics under test (do not confuse with running N separate renders):
 * Remotion's renderMedia `concurrency` shards ONE composition's frames
 * across N Chromium workers and stitches them into a single output file.
 * This script renders the SAME 30s 1080p composition (900 frames, built by
 * lib/semanticManifest from a 0.5-minute job) at requested concurrency
 * 1, 2, 3 and 5, in one process so the Remotion bundle is built once and
 * reused, and reports wall time, speedup vs 1 worker, peak memory used
 * and whether each level completed. The effective worker count is capped
 * by CPU (4 vCPU -> max 3) and by available memory (~400 MB/worker) —
 * the logged effective count is the honest number.
 *
 * Usage:
 *   node scripts/benchmark-remotion-workers.js [1,2,3,5]
 * Output: work/remotion-worker-benchmark/results.json + a table on stdout.
 * Safe only when the box is idle (no live Vidnets job, no live render):
 * a 3-worker render needs ~1.8 GB actually free at render start.
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const OUT_DIR = path.join(ROOT, 'work', 'remotion-worker-benchmark');
fs.mkdirSync(OUT_DIR, { recursive: true });

const LEVELS = (process.argv[2] || '1,2,3,5').split(',').map((s) => Number(s)).filter(Boolean);

// 30s 1080p: 0.5 target minutes -> buildSemanticManifest floors at 30s.
const JOB = {
  jobId: 'benchmark-workers',
  title: 'Remotion worker benchmark: one 1080p video, N workers',
  targetDurationMinutes: 0.5,
  channel: { name: 'Benchmark' },
};
const SCRIPT = [
  'The benchmark train leaves the station at dawn, crossing the river bridge as the city wakes.',
  'Cameras follow the locomotive through the valley, past signal boxes, sidings and freight yards.',
  'Engineers check the brakes, the dispatcher clears the route, and the express gathers speed.',
  'By noon the train reaches the mountain pass, where snow fences guard the single track.',
  'The journey ends at the terminus, on time, after thirty seconds of documentary footage.',
].join(' ');

function freeAvailableMb() {
  const out = execSync('free -m', { encoding: 'utf8' });
  const line = out.split('\n').find((l) => l.startsWith('Mem:'));
  return Number(line.trim().split(/\s+/)[6]);
}

/** Peak RSS (MB) of this process tree, sampled while rendering. */
function sampleTreeRssMb(rootPid) {
  try {
    const out = execSync('ps -eo pid,ppid,rss --no-headers', { encoding: 'utf8' });
    const rows = out.trim().split('\n').map((l) => l.trim().split(/\s+/).map(Number));
    const byPpid = new Map();
    for (const [pid, ppid, rss] of rows) {
      if (!byPpid.has(ppid)) byPpid.set(ppid, []);
      byPpid.get(ppid).push(pid);
    }
    const seen = new Set([rootPid]);
    const queue = [rootPid];
    const rssByPid = new Map(rows.map(([p, , r]) => [p, r]));
    let totalKb = 0;
    while (queue.length) {
      const pid = queue.shift();
      totalKb += rssByPid.get(pid) || 0;
      for (const child of byPpid.get(pid) || []) {
        if (!seen.has(child)) { seen.add(child); queue.push(child); }
      }
    }
    return Math.round(totalKb / 1024);
  } catch (_) {
    return null;
  }
}

async function runLevel(render, requested) {
  const workDir = path.join(OUT_DIR, `conc-${requested}`);
  fs.mkdirSync(workDir, { recursive: true });
  process.env.MUSE_RENDER_CONCURRENCY = String(requested);
  delete process.env.VIDZEO_REMOTION_WORKERS;
  delete process.env.MUSE_REMOTION_WORKERS;
  delete process.env.VIDZEO_REMOTION_CONCURRENCY;

  const startedAt = Date.now();
  const availBefore = freeAvailableMb();
  let minAvail = availBefore;
  let peakTreeMb = sampleTreeRssMb(process.pid) || 0;
  const sampler = setInterval(() => {
    const avail = freeAvailableMb();
    if (avail < minAvail) minAvail = avail;
    const tree = sampleTreeRssMb(process.pid);
    if (tree !== null && tree > peakTreeMb) peakTreeMb = tree;
  }, 2000);
  let res = null;
  let error = '';
  try {
    res = await render(JOB, SCRIPT, workDir, {});
  } catch (e) {
    error = String((e && e.message) || e);
  }
  clearInterval(sampler);
  const wallMs = Date.now() - startedAt;
  return {
    requested,
    effective: res?.meta?.concurrency ?? null,
    wallSeconds: Math.round(wallMs / 100) / 10,
    ok: !!res,
    videoMb: res ? Math.round(fs.statSync(res.videoPath).size / (1024 * 1024) * 10) / 10 : null,
    availableBeforeMb: availBefore,
    minAvailableMb: minAvail,
    peakRenderTreeMb: peakTreeMb,
    approxPeakUsedMb: availBefore - minAvail,
    error: error ? error.slice(0, 300) : '',
  };
}

(async () => {
  const { render } = require(path.join(ROOT, 'lib', 'remotionRenderer.js'));
  console.log(`Benchmark: one 30s 1080p video, requested workers ${LEVELS.join(', ')}`);
  console.log(`free available at start: ${freeAvailableMb()} MB`);
  const results = [];
  for (const level of LEVELS) {
    console.log(`\n--- requested ${level} ---`);
    const r = await runLevel(render, level);
    console.log(`effective=${r.effective} wall=${r.wallSeconds}s ok=${r.ok} video=${r.videoMb}MB peakUsed~${r.approxPeakUsedMb}MB peakTree=${r.peakTreeMb}MB${r.error ? ' ERROR: ' + r.error : ''}`);
    results.push(r);
  }
  const base = results.find((r) => r.requested === 1)?.wallSeconds;
  const table = results.map((r) => ({
    ...r,
    speedupVs1: base && r.ok ? Math.round((base / r.wallSeconds) * 100) / 100 : null,
  }));
  fs.writeFileSync(path.join(OUT_DIR, 'results.json'), JSON.stringify(table, null, 2));
  console.log('\nworkers | effective | wall(s) | speedup vs 1 | peakUsed~(MB) | result');
  for (const r of table) {
    console.log(`${r.requested} | ${r.effective} | ${r.wallSeconds} | ${r.speedupVs1 ?? '-'} | ${r.approxPeakUsedMb} | ${r.ok ? 'OK' : 'FAILED: ' + r.error}`);
  }
  console.log('\nSaved: ' + path.join(OUT_DIR, 'results.json'));
})().catch((e) => { console.error(e); process.exit(1); });
