const assert = require('node:assert/strict');
const os = require('node:os');
const {adaptiveConcurrency} = require('../scripts/render-remotion-manifest');
const oldTotal = os.totalmem, oldFree = os.freemem, oldParallelism = os.availableParallelism;
const oldEnv = {...process.env};
try {
  for (const key of Object.keys(process.env)) if (key.startsWith('VIDZEO_REMOTION_')) delete process.env[key];
  // Pin swap for determinism: the real /proc/meminfo value drifts.
  // 600 MB free swap is this VM's steady state (free -m, Nov 16).
  process.env.VIDZEO_REMOTION_SWAP_FREE_MB = '600';
  os.totalmem = () => 8 * 1024 ** 3;
  os.availableParallelism = () => 16;
  os.freemem = () => 1770 * 1024 ** 2;
  const plan = adaptiveConcurrency({width:640,height:360}, {scenes:[{media:{type:'video'}}]});
  assert.ok(plan.concurrency >= 1);
  assert.equal(plan.offthread_video_threads, 1);
  assert.equal(plan.video_cache_bytes, 256 * 1024 ** 2);
  assert.ok(plan.video_cache_bytes + plan.media_cache_bytes + plan.estimated_worker_memory_mb * 1024 ** 2
    < (plan.available_memory_mb - plan.reserve_memory_mb) * 1024 ** 2);
  os.freemem = () => 4 * 1024 ** 3;
  const larger = adaptiveConcurrency({width:1920,height:1080}, {scenes:[{media:{shots:[{media_type:'video'}]}}]});
  assert.ok(larger.concurrency > plan.concurrency && larger.concurrency <= larger.cap);
  assert.ok(larger.concurrency <= larger.memory_limit && larger.concurrency <= larger.cpu_limit);
  assert.equal(larger.video_cache_bytes, 256 * 1024 ** 2);
  // Nov 16, 2026 (GTA VI ef306926, working-set fix): 1500 MB free now
  // admits a serial 1080p render — the media cache CONFIG is always the
  // full 256 MB cap (never the old 64 MB Remotion rejects), and the
  // resident working set fits in headroom (1500 available + 600 swap
  // against the ~873 MB bar on this box). Serial admission now starts at
  // the 512 MB floor, not 1700 MB.
  os.freemem = () => 1500 * 1024 ** 2;
  const fifteenHundred = adaptiveConcurrency({width:1920,height:1080}, {scenes:[{media:{type:'video'}}]}, {freshMemory:true});
  assert.equal(fifteenHundred.concurrency, 1);
  assert.equal(fifteenHundred.media_cache_bytes, 256 * 1024 ** 2);
  os.freemem = () => 1700 * 1024 ** 2;
  const constrained = adaptiveConcurrency({width:1920,height:1080}, {scenes:[{media:{type:'video'}}]}, {freshMemory:true});
  // memory_limit 3 is the raw capacity estimate at 1700 MB
  // ((1700 - 163 reserve - 224 shared) / (272 worker + 64) = 3); the
  // sub-2GB serial guard is what holds the render to one worker — that
  // is the safety assertion, and it is what keeps the GTA VI job from
  // launching a second worker the VM cannot back.
  assert.equal(constrained.memory_limit, 3);
  assert.equal(constrained.concurrency, 1, 'a second render worker must not exceed the live memory estimate');
  assert.equal(constrained.minimum_available_memory_mb, 512);
  assert.ok(constrained.media_cache_bytes >= 240 * 1024 ** 2);
  // Deliberate change, Nov 16, 2026: only levels below the 512 MB floor
  // wait for memory. 512 MB and up admit a serial render — the working
  // set fits in headroom (available + the pinned 600 MB swap) — with the
  // full 256 MB media-cache CONFIG, so the old sub-floor waiting
  // assertions now apply only to 384 MB (and the 383 MB case below).
  for (const totalMb of [8192, 16384, 32768]) {
    os.totalmem = () => totalMb * 1024 ** 2;
    os.freemem = () => 384 * 1024 ** 2;
    assert.throws(() => adaptiveConcurrency({width:1920,height:1080}, {scenes:[{media:{type:'video'}}]}),
      e => e.code === 'VIDZEO_LOW_MEMORY', `total ${totalMb} MB free 384 MB must wait for memory`);
    for (const freeMb of [512, 768, 1000, 1200]) {
      os.freemem = () => freeMb * 1024 ** 2;
      const admitted = adaptiveConcurrency({width:1920,height:1080}, {scenes:[{media:{type:'video'}}]});
      assert.equal(admitted.concurrency, 1, `total ${totalMb} MB free ${freeMb} MB must admit one serial worker`);
      assert.ok(admitted.media_cache_bytes >= 240 * 1024 ** 2, `total ${totalMb} MB free ${freeMb} MB: media cache below the 240 MB floor`);
    }
  }
  os.freemem = () => 383 * 1024 ** 2;
  assert.throws(() => adaptiveConcurrency({width:1920,height:1080}), e => e.code === 'VIDZEO_LOW_MEMORY');
  // Same serial-admission level as the 1700 MB case above, via the env
  // override instead of freemem (total is reset: the loop above left it
  // at 32 GB, whose 2 GB reserve would not admit 1700 MB).
  os.totalmem = () => 8192 * 1024 ** 2;
  process.env.VIDZEO_REMOTION_AVAILABLE_RAM_MB = '1700';
  assert.equal(adaptiveConcurrency({width:1920,height:1080}).concurrency, 1);
  assert.throws(() => adaptiveConcurrency({width:1920,height:1080}, {}, {freshMemory:true}), e => e.code === 'VIDZEO_LOW_MEMORY');
  delete process.env.VIDZEO_REMOTION_AVAILABLE_RAM_MB;
  os.freemem = () => 16 * 1024 ** 3;
  os.totalmem = () => 32 * 1024 ** 3;
  // The 8-worker scaling case used to rely on ambient env requesting 8
  // workers (default request is 2, so it returned 2 in a bare shell);
  // request 8 explicitly so the test is hermetic.
  process.env.VIDZEO_REMOTION_CONCURRENCY = '8';
  assert.equal(adaptiveConcurrency({width:1920,height:1080}).concurrency, 8);
  process.env.VIDZEO_REMOTION_CONCURRENCY = '2';
  assert.equal(adaptiveConcurrency({width:1920,height:1080}).concurrency, 2);
  os.availableParallelism = () => 1;
  assert.equal(adaptiveConcurrency({width:1920,height:1080}).concurrency, 1);
  console.log('Serial memory floor, HD cache bounds, fresh admission and parallel scaling passed');
} finally {
  os.totalmem = oldTotal; os.freemem = oldFree; os.availableParallelism = oldParallelism;
  for (const key of Object.keys(process.env)) if (key.startsWith('VIDZEO_REMOTION_')) delete process.env[key];
  Object.assign(process.env, oldEnv);
}
