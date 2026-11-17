const assert = require('node:assert/strict');
const os = require('node:os');
const {adaptiveConcurrency, waitForRenderMemory} = require('../scripts/render-remotion-manifest');

// Regression test for GTA VI ef306926 (Nov 16, 2026): the job stuck in a
// resume -> wait-for-memory -> fail -> pause -> driver-resume loop because
// the old gate summed the Remotion CONFIG caps (worker 400 + decoder 256 +
// media floor 240 + margin 96 + reserve 555 = 1547 MB, later 1101 MB) as
// if all were resident at once, on a VM whose steady-state available
// memory is ~350-650 MB. The gate now budgets the resident working set
// (one worker 550 MB + caches 160 MB + reserve) against headroom
// (available + free swap) with a 512 MB available floor: a serial render
// must be able to START and COMPLETE sections at ~500-650 MB available,
// or fail fast with a bounded, retryable VIDZEO_LOW_MEMORY — never loop
// forever. The 240 MB Remotion media-cache floor is a CONFIG minimum and
// is untouched: every returned plan carries a 256 MB media cache.
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
    const manifest = {scenes: [{media: {type: 'video'}}]};

    // 1. Exact GTA steady-state replay: 7.7 GB box, 600 MB available,
    // 600 MB swap free. The old gate demanded ~1101 MB and waited
    // forever; the new gate admits one serial worker.
    mockMemory(7880, 600);
    const steady = adaptiveConcurrency({width: 1920, height: 1080}, manifest, {freshMemory: true});
    assert.equal(steady.concurrency, 1, 'steady state must admit one serial worker');
    assert.equal(steady.media_cache_bytes, 256 * MB, 'media cache CONFIG stays at the full 256 MB cap');
    assert.equal(steady.video_cache_bytes, 256 * MB);
    assert.equal(steady.minimum_available_memory_mb, 512, 'admission floor is 512 MB, not 768');
    assert.ok(steady.headroom_mb >= steady.preferred_available_memory_mb,
      `headroom ${steady.headroom_mb} MB must clear the working-set bar ${steady.preferred_available_memory_mb} MB`);
    assert.equal(steady.preferred_available_memory_mb, steady.reserve_memory_mb + 550 + 160,
      'preferred headroom is reserve + worker working set + cache working set');

    // 2. Bottom of the band: 512 MB available is the floor — it admits.
    mockMemory(7880, 512);
    assert.equal(adaptiveConcurrency({width: 1920, height: 1080}, manifest, {freshMemory: true}).concurrency, 1);

    // 3. Below the floor the browser cannot even load: 500 MB waits,
    // even with swap free. The error must name the 512 MB floor.
    mockMemory(7880, 500);
    assert.throws(
      () => adaptiveConcurrency({width: 1920, height: 1080}, manifest, {freshMemory: true}),
      (error) => error.code === 'VIDZEO_LOW_MEMORY' && /512 MB needed/.test(error.message),
      '500 MB available must wait on the floor, not start a browser it cannot back',
    );

    // 4. No swap headroom, no admission: 600 MB available alone is below
    // the 867 MB working-set bar. The error must name the headroom bar.
    process.env.VIDZEO_REMOTION_SWAP_FREE_MB = '0';
    mockMemory(7880, 600);
    assert.throws(
      () => adaptiveConcurrency({width: 1920, height: 1080}, manifest, {freshMemory: true}),
      (error) => error.code === 'VIDZEO_LOW_MEMORY' && /needs \d+ MB of headroom/.test(error.message),
      '600 MB available with no free swap must wait for headroom',
    );
    process.env.VIDZEO_REMOTION_SWAP_FREE_MB = '600';

    // 5. Low end of the band: 350 MB available waits even with swap —
    // the floor is about the browser loading resident, not headroom.
    mockMemory(7880, 350);
    assert.throws(
      () => adaptiveConcurrency({width: 1920, height: 1080}, manifest, {freshMemory: true}),
      (error) => error.code === 'VIDZEO_LOW_MEMORY' && /512 MB needed/.test(error.message),
      '350 MB available must wait on the floor',
    );

    // 6. The old media-cache-floor replay case (1400 MB free) now admits
    // instead of waiting: the 240 MB CONFIG floor is met by the constant
    // 256 MB cache, and the working set fits.
    mockMemory(7880, 1400);
    const peak = adaptiveConcurrency({width: 1920, height: 1080}, manifest, {freshMemory: true});
    assert.equal(peak.concurrency, 1);
    assert.ok(peak.media_cache_bytes >= MIN_MEDIA_CACHE_BYTES);

    // 7. Start gate == section gate: the same memory gives the same
    // outcome on repeated calls (the per-section gate is this same
    // function, so the thresholds cannot drift apart).
    for (const [totalMb, freeMb, swapMb] of [[7880, 600, 600], [7880, 500, 600], [8192, 512, 600], [16384, 900, 0]]) {
      process.env.VIDZEO_REMOTION_SWAP_FREE_MB = String(swapMb);
      mockMemory(totalMb, freeMb);
      const outcomes = [];
      for (let i = 0; i < 2; i++) {
        try {
          const plan = adaptiveConcurrency({width: 1920, height: 1080}, manifest, {freshMemory: true});
          outcomes.push(`admit:${plan.concurrency}`);
        } catch (error) {
          outcomes.push(`wait:${error.code}`);
        }
      }
      assert.equal(outcomes[0], outcomes[1],
        `total ${totalMb} free ${freeMb} swap ${swapMb}: start and section gates must agree (${outcomes})`);
    }
    process.env.VIDZEO_REMOTION_SWAP_FREE_MB = '600';

    // 8. Invariant sweep: no returned plan, at any memory level, may
    // carry a media or offthread-video cache below the 240 MB Remotion
    // floor; a throw must be VIDZEO_LOW_MEMORY only; and every admitted
    // plan must satisfy the working-set inequality it was sized from.
    for (const totalMb of [4096, 7880, 8192, 16384, 32768]) {
      for (const freeMb of [384, 500, 512, 600, 900, 1200, 1400, 2000, 4096]) {
        if (freeMb > totalMb) continue;
        mockMemory(totalMb, freeMb);
        for (const m of [manifest, {scenes: [{media: {type: 'image'}}]}, {}]) {
          let plan = null;
          let thrown = null;
          try {
            plan = adaptiveConcurrency({width: 1920, height: 1080}, m, {freshMemory: true});
          } catch (error) { thrown = error; }
          if (thrown) {
            assert.equal(thrown.code, 'VIDZEO_LOW_MEMORY', `total ${totalMb} free ${freeMb} must fail only by waiting for memory, got ${thrown.code}`);
          } else {
            assert.ok(plan.concurrency >= 1, `total ${totalMb} free ${freeMb}`);
            assert.equal(plan.media_cache_bytes, 256 * MB, `total ${totalMb} free ${freeMb}: media cache CONFIG must be the full 256 MB cap`);
            assert.ok(plan.video_cache_bytes >= MIN_MEDIA_CACHE_BYTES,
              `total ${totalMb} free ${freeMb}: video cache ${plan.video_cache_bytes} below the 240 MB floor`);
            assert.ok(plan.headroom_mb >= plan.reserve_memory_mb + plan.worker_working_set_mb + plan.cache_working_set_mb,
              `total ${totalMb} free ${freeMb}: headroom ${plan.headroom_mb} must cover reserve + working sets`);
            assert.ok(plan.available_memory_mb >= plan.minimum_available_memory_mb,
              `total ${totalMb} free ${freeMb}: available below the floor in a returned plan`);
          }
        }
      }
    }

    // 9. Wait/retry path: memory below the floor frees up mid-wait and
    // the render is admitted with a legal cache instead of looping.
    let reads = 0;
    os.freemem = () => (++reads < 3 ? 400 : 650) * MB;
    os.totalmem = () => 7880 * MB;
    const plan = await waitForRenderMemory({width: 1920, height: 1080}, manifest,
      {freshMemory: true, memoryWaitMs: 30000});
    assert.equal(plan.concurrency, 1);
    assert.ok(plan.media_cache_bytes >= MIN_MEDIA_CACHE_BYTES);

    // 10. Never-recovers path (the strengthened bar, Nov 16, 2026): memory
    // pinned below the floor must exit BOUNDEDLY with the retryable
    // low-memory signal — never spin in the wait loop forever. The wait
    // deadline (3s here) caps it; the error carries retryable=true and
    // names the saved sections, which is what the driver parks as a
    // low_memory pause and retries from checkpoint.
    process.env.VIDZEO_REMOTION_SWAP_FREE_MB = '0';
    os.freemem = () => 400 * MB;
    const started = Date.now();
    await assert.rejects(
      waitForRenderMemory({width: 1920, height: 1080}, manifest,
        {freshMemory: true, memoryWaitMs: 3000}),
      (error) => error.code === 'VIDZEO_LOW_MEMORY'
        && error.retryable === true
        && /deadline reached/.test(error.message)
        && /saved for resume/.test(error.message),
      'pinned low memory must exit boundedly with a retryable low-memory signal',
    );
    const waitedMs = Date.now() - started;
    assert.ok(waitedMs < 10000, `never-recovers wait must exit in ~3s, took ${waitedMs}ms`);
    console.log('Steady-state render gate: GTA VI replay, floor, headroom, start==section, wait-for-memory and never-recovers passed');
  } finally {
    os.totalmem = oldTotal; os.freemem = oldFree; os.availableParallelism = oldParallelism;
    for (const key of Object.keys(process.env)) if (key.startsWith('VIDZEO_REMOTION_')) delete process.env[key];
    Object.assign(process.env, oldEnv);
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
