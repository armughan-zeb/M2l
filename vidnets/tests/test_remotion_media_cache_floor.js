const assert = require('node:assert/strict');
const os = require('node:os');
const {adaptiveConcurrency, waitForRenderMemory} = require('../scripts/render-remotion-manifest');

// Regression test for GTA VI ef306926 (Nov 16, 2026): the render died at 78%
// with `The minimum value for the "mediaCacheSizeInBytes" prop is 240MB
// (251658240), got: 67108864` (see checkpoints/render_failure.json).
// adaptiveConcurrency() capped the media cache at 64 MB whenever available
// memory was under 3 GB, so every low-memory render was guaranteed to fail
// Remotion validation after doing most of the work. The fix enforces the
// 240 MB floor by always passing the full 256 MB media-cache CONFIG, and
// gates admission on the resident working set against headroom (available
// + free swap) with a 512 MB available floor — it waits/retries via
// VIDZEO_LOW_MEMORY when even a serial render cannot fit, and never
// returns a plan Remotion would reject.
const MB = 1024 ** 2;
const MIN_MEDIA_CACHE_BYTES = 240 * MB;
const oldTotal = os.totalmem, oldFree = os.freemem, oldParallelism = os.availableParallelism;
const oldEnv = {...process.env};
function mockMemory(totalMb, freeMb, cpus = 4) {
  os.totalmem = () => totalMb * MB;
  os.freemem = () => freeMb * MB;
  os.availableParallelism = () => cpus;
}

async function main() {
  try {
    for (const key of Object.keys(process.env)) if (key.startsWith('VIDZEO_REMOTION_')) delete process.env[key];
    // Pin swap for determinism: the real /proc/meminfo value drifts.
    // 600 MB free swap is this VM's steady state (free -m, Nov 16).
    process.env.VIDZEO_REMOTION_SWAP_FREE_MB = '600';

    // 1. Exact replay of the live failure: VM-sized machine (7.7 GB total),
    // 1400 MB free, 1080p video scenes. The old code returned
    // media_cache_bytes 67108864 here; the new code must never do that —
    // it admits the render with the full 256 MB media-cache CONFIG.
    mockMemory(7880, 1400);
    const replay = adaptiveConcurrency({width: 1920, height: 1080}, {scenes: [{media: {type: 'video'}}]}, {freshMemory: true});
    assert.equal(replay.concurrency, 1, 'GTA VI conditions must admit one serial worker');
    assert.equal(replay.media_cache_bytes, 256 * MB, 'media cache CONFIG is the full 256 MB cap, never 64 MB');

    // 2. Invariant sweep: no returned plan, at any memory level, may carry a
    // media or offthread-video cache below the 240 MB Remotion floor — or the
    // exact 67108864 bytes from the live failure. Either it fits the floor
    // or it throws VIDZEO_LOW_MEMORY; there is no third outcome.
    for (const totalMb of [4096, 7880, 8192, 16384, 32768]) {
      for (const freeMb of [384, 512, 900, 1200, 1400, 1500, 1566, 1700, 2000, 3000, 4096, 8192, 16384]) {
        if (freeMb > totalMb) continue;
        mockMemory(totalMb, freeMb);
        for (const manifest of [{scenes: [{media: {type: 'video'}}]}, {scenes: [{media: {type: 'image'}}]}, {}]) {
          let plan = null;
          let thrown = null;
          try {
            plan = adaptiveConcurrency({width: 1920, height: 1080}, manifest, {freshMemory: true});
          } catch (error) { thrown = error; }
          if (thrown) {
            assert.equal(thrown.code, 'VIDZEO_LOW_MEMORY', `total ${totalMb} free ${freeMb} must fail only by waiting for memory, got ${thrown.code}`);
          } else {
            assert.ok(plan.concurrency >= 1, `total ${totalMb} free ${freeMb}`);
            assert.ok(plan.media_cache_bytes >= MIN_MEDIA_CACHE_BYTES,
              `total ${totalMb} free ${freeMb}: media cache ${plan.media_cache_bytes} below the 240 MB Remotion floor`);
            assert.ok(plan.media_cache_bytes <= 256 * MB, `total ${totalMb} free ${freeMb}: media cache above the 256 MB cap`);
            assert.notEqual(plan.media_cache_bytes, 67108864, `total ${totalMb} free ${freeMb}: the exact GTA VI failure value`);
            assert.ok(plan.video_cache_bytes >= MIN_MEDIA_CACHE_BYTES,
              `total ${totalMb} free ${freeMb}: video cache ${plan.video_cache_bytes} below the 240 MB floor`);
            // The plan must be self-consistent: the working set (reserve
            // + worker + caches) fits inside the headroom it was sized
            // from, and available clears the admission floor.
            assert.ok(plan.headroom_mb >= plan.reserve_memory_mb + plan.worker_working_set_mb + plan.cache_working_set_mb,
              `total ${totalMb} free ${freeMb}: headroom ${plan.headroom_mb} below the working-set bar`);
            assert.ok(plan.available_memory_mb >= plan.minimum_available_memory_mb,
              `total ${totalMb} free ${freeMb}: available below the admission floor in a returned plan`);
          }
        }
      }
    }

    // 3. Degrade, don't die: at 1770 MB free the 640x360 video plan used to
    // run 2 workers on a 64 MB media cache. It now sheds to 1 worker and
    // still renders, with a legal 256 MB media cache.
    mockMemory(8192, 1770, 16);
    const shed = adaptiveConcurrency({width: 640, height: 360}, {scenes: [{media: {type: 'video'}}]});
    assert.equal(shed.concurrency, 1, 'workers shed to afford the media-cache floor');
    assert.equal(shed.media_cache_bytes, 256 * MB);
    assert.equal(shed.video_cache_bytes, 256 * MB);

    // 4. Serial admission just above the floor: 1700 MB free on an 8 GB box
    // admits one 1080p worker with the floor met and the preferred-memory
    // figure honestly naming the headroom bar (~870 MB: reserve 163 +
    // worker working set 550 + cache working set 160), not the old
    // sum-of-caps figure (~1.5 GB).
    mockMemory(8192, 1700);
    const serial = adaptiveConcurrency({width: 1920, height: 1080}, {scenes: [{media: {type: 'video'}}]}, {freshMemory: true});
    assert.equal(serial.concurrency, 1);
    assert.ok(serial.media_cache_bytes >= MIN_MEDIA_CACHE_BYTES);
    assert.ok(serial.preferred_available_memory_mb >= 800 && serial.preferred_available_memory_mb <= 950,
      `preferred available memory ${serial.preferred_available_memory_mb} MB should name the true serial admission bar`);

    // 5. Wait/retry path: memory that frees up mid-wait admits the render
    // with a legal cache instead of launching while starved. The first
    // reads are below the 512 MB floor, so the wait must actually wait.
    let reads = 0;
    os.freemem = () => (++reads < 3 ? 400 : 1700) * MB;
    os.totalmem = () => 8192 * MB;
    const plan = await waitForRenderMemory({width: 1920, height: 1080}, {scenes: [{media: {type: 'video'}}]},
      {freshMemory: true, memoryWaitMs: 30000});
    assert.equal(plan.concurrency, 1);
    assert.ok(plan.media_cache_bytes >= MIN_MEDIA_CACHE_BYTES);
    console.log('Remotion media-cache floor: GTA VI replay, invariant sweep, worker shedding, serial admission and wait-for-memory passed');
  } finally {
    os.totalmem = oldTotal; os.freemem = oldFree; os.availableParallelism = oldParallelism;
    for (const key of Object.keys(process.env)) if (key.startsWith('VIDZEO_REMOTION_')) delete process.env[key];
    Object.assign(process.env, oldEnv);
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
