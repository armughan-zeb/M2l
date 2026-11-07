const assert = require('node:assert/strict');
const os = require('node:os');
const {adaptiveConcurrency} = require('../scripts/render-remotion-manifest');
const oldTotal = os.totalmem, oldFree = os.freemem, oldParallelism = os.availableParallelism;
const oldEnv = {...process.env};
try {
  for (const key of Object.keys(process.env)) if (key.startsWith('VIDZEO_REMOTION_')) delete process.env[key];
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
  os.freemem = () => 1500 * 1024 ** 2;
  const constrained = adaptiveConcurrency({width:1920,height:1080}, {scenes:[{media:{type:'video'}}]}, {freshMemory:true});
  assert.equal(constrained.memory_limit, 1);
  assert.equal(constrained.concurrency, 1, 'a second render worker must not exceed the live memory estimate');
  for (const totalMb of [8192, 16384, 32768]) {
    os.totalmem = () => totalMb * 1024 ** 2;
    for (const freeMb of [384, 512, 1000, 1200]) {
      os.freemem = () => freeMb * 1024 ** 2;
      const serial = adaptiveConcurrency({width:1920,height:1080}, {scenes:[{media:{type:'video'}}]});
      assert.ok(serial.concurrency >= 1);
      assert.equal(serial.minimum_available_memory_mb, 384);
      if (serial.raw_memory_limit === 0) {
        assert.equal(serial.concurrency, 1);
        assert.equal(serial.single_worker_fallback, true);
        assert.equal(serial.browser_heap_mb, 256);
      }
    }
  }
  os.freemem = () => 383 * 1024 ** 2;
  assert.throws(() => adaptiveConcurrency({width:1920,height:1080}), e => e.code === 'VIDZEO_LOW_MEMORY');
  process.env.VIDZEO_REMOTION_AVAILABLE_RAM_MB = '1200';
  assert.equal(adaptiveConcurrency({width:1920,height:1080}).concurrency, 1);
  assert.throws(() => adaptiveConcurrency({width:1920,height:1080}, {}, {freshMemory:true}), e => e.code === 'VIDZEO_LOW_MEMORY');
  delete process.env.VIDZEO_REMOTION_AVAILABLE_RAM_MB;
  os.freemem = () => 16 * 1024 ** 3;
  os.totalmem = () => 32 * 1024 ** 3;
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
