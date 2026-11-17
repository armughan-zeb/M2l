const { test } = require('node:test');
const assert = require('node:assert/strict');
const { adaptiveConcurrency } = require('../scripts/render-remotion-manifest');

// Low-memory gate regression test (Nov 16, 2026): the render gate demanded
// 1547 MB available (reserve 555 + worker 400 + decoder 256 + floor 240 +
// margin 96), but this 7.7 GB VM peaks near 1140 MB, so the GTA job retried
// 10x without ever rendering. The a4b92db tuning (reserve 5%, decoder
// 128 MB in low-memory mode, worker 192 MB, overhead 64 MB, margin 64 MB)
// lowered the bar to ~1101 MB — still above steady state (350-650 MB).
// The working-set fix budgets the resident working set (one worker
// 550 MB + caches 160 MB + reserve ~157 MB = ~867 MB headroom) against
// available + free swap, with a 512 MB available floor. The Remotion
// 240 MB media-cache floor is kept: a 64 MB cache failed validation at
// 78% on this same job.
const composition = { width: 1920, height: 1080, durationInFrames: 3000, fps: 30 };
const manifest = { scenes: [{ media: { type: 'video' } }] };

function withAvailableMB(mb, fn, swapMb = 600) {
  const previous = process.env.VIDZEO_REMOTION_AVAILABLE_RAM_MB;
  const previousSwap = process.env.VIDZEO_REMOTION_SWAP_FREE_MB;
  process.env.VIDZEO_REMOTION_AVAILABLE_RAM_MB = String(mb);
  process.env.VIDZEO_REMOTION_SWAP_FREE_MB = String(swapMb);
  try {
    return fn();
  } finally {
    if (previous === undefined) delete process.env.VIDZEO_REMOTION_AVAILABLE_RAM_MB;
    else process.env.VIDZEO_REMOTION_AVAILABLE_RAM_MB = previous;
    if (previousSwap === undefined) delete process.env.VIDZEO_REMOTION_SWAP_FREE_MB;
    else process.env.VIDZEO_REMOTION_SWAP_FREE_MB = previousSwap;
  }
}

test('render gate passes at the VM peak (~1150 MB available)', () => {
  const plan = withAvailableMB(1150, () => adaptiveConcurrency(composition, manifest));
  assert.equal(plan.concurrency, 1);
  assert.ok(plan.media_cache_bytes >= 240 * 1024 * 1024, 'media cache keeps the 240 MB Remotion floor');
  assert.ok(plan.available_memory_mb >= 1100);
});

test('render gate still refuses when memory is truly low (~800 MB, no swap headroom)', () => {
  assert.throws(
    () => withAvailableMB(800, () => adaptiveConcurrency(composition, manifest), 0),
    (error) => error.code === 'VIDZEO_LOW_MEMORY',
  );
});

test('render gate reports the tuned threshold, not the old 1547 MB bar', () => {
  let error;
  try {
    withAvailableMB(800, () => adaptiveConcurrency(composition, manifest), 0);
  } catch (caught) {
    error = caught;
  }
  assert.ok(error, 'expected a VIDZEO_LOW_MEMORY throw at 800 MB');
  assert.equal(error.code, 'VIDZEO_LOW_MEMORY');
  const match = /needs (\d+) MB/.exec(error.message);
  assert.ok(match, `expected a "needs N MB" figure in: ${error.message}`);
  const needed = Number(match[1]);
  // The message reports the working-set headroom bar (reserve ~157 +
  // worker working set 550 + cache working set 160 = ~867 MB on this
  // 7.7 GB VM). The old bars were 992 MB after the reserve (worker 400
  // + decoder 256 + floor 240 + margin 96) and 1547 MB with the reserve.
  assert.ok(needed > 800 && needed < 950, `gate should demand ~867 MB of headroom, not ${needed} MB`);
});
