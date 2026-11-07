const {test} = require('node:test');
const assert = require('node:assert/strict');
const os = require('node:os');
const {waitForRenderMemory, adaptiveConcurrency, renderSectionHasher, renderBrowserSession} = require('../scripts/render-remotion-manifest');

test('memory admission releases only the owned browser before waiting', async () => {
  const free = os.freemem;
  let released = 0;
  try {
    os.freemem = () => (released ? 900 : 200) * 1024 ** 2;
    const plan = await waitForRenderMemory({width:1920,height:1080}, {}, {
      freshMemory:true, memoryWaitMs:1000, releaseOwnedResources:async () => {released++;},
    });
    assert.equal(released,1);
    assert.equal(plan.concurrency,1);
  } finally {os.freemem = free;}
});

test('one owned browser serves selection and sections, with fresh browser after resource recovery', async () => {
  let opens = 0, closes = 0;
  const session = renderBrowserSession(async () => ({id:++opens,close:async () => {closes++;}}));
  const selected = await session.get({});
  assert.equal(await session.get({}),selected);
  assert.equal(await session.get({}),selected);
  assert.equal(opens,1);
  await session.close();
  assert.notEqual(await session.get({}),selected);
  await session.close();
  await session.close();
  assert.equal(opens,2);
  assert.equal(closes,2);
});

test('saved render sections survive worker changes but invalidate changed content and encoding', () => {
  const options = {scale:1, selectedCrf:18, selectedX264Preset:'medium', frameRange:null, framesPerChunk:900};
  const hash = (payload='script-a', bundle='bundle-a', opts=options, asset='asset-a') =>
    renderSectionHasher(payload, bundle, opts).update(asset).digest('hex');
  const original = hash();
  assert.equal(hash('script-a', 'bundle-a', {...options, concurrency:1}), original);
  assert.equal(hash('script-a', 'bundle-a', {...options, concurrency:3}), original);
  assert.notEqual(hash('script-b'), original);
  assert.notEqual(hash('script-a', 'bundle-b'), original);
  assert.notEqual(hash('script-a', 'bundle-a', {...options, selectedCrf:19}), original);
  assert.notEqual(hash('script-a', 'bundle-a', options, 'asset-b'), original);
});

test('memory waits terminate with resumable checkpoints instead of an endless heartbeat', async () => {
  const free = os.freemem;
  try {
    os.freemem = () => 200 * 1024 ** 2;
    await assert.rejects(waitForRenderMemory({width:1920,height:1080}, {}, {freshMemory:true, memoryWaitMs:5}),
      error => error.code === 'VIDZEO_LOW_MEMORY' && error.retryable && /saved for resume/.test(error.message));
  } finally { os.freemem = free; }
});

test('parallel encoding follows remaining memory rather than a fixed four-GB ban', () => {
  const free = os.freemem, total = os.totalmem, cpu = os.availableParallelism;
  const saved = {...process.env};
  try {
    for (const key of Object.keys(process.env)) if (key.startsWith('VIDZEO_REMOTION_')) delete process.env[key];
    os.totalmem = () => 8 * 1024 ** 3;
    os.availableParallelism = () => 8;
    os.freemem = () => 3000 * 1024 ** 2;
    const plan = adaptiveConcurrency({width:1920,height:1080}, {scenes:[{media:{type:'video'}}]}, {freshMemory:true});
    assert.ok(plan.concurrency > 1 && plan.concurrency <= 8);
    assert.ok(plan.concurrency <= plan.memory_limit && plan.concurrency <= plan.cpu_limit);
    assert.equal(plan.disallow_parallel_encoding, false);
    assert.equal(plan.video_cache_bytes, 256 * 1024 ** 2);
    os.freemem = () => 900 * 1024 ** 2;
    const pressure = adaptiveConcurrency({width:1920,height:1080}, {}, {freshMemory:true});
    assert.equal(pressure.concurrency, 1);
    assert.equal(pressure.disallow_parallel_encoding, true);
  } finally {
    os.freemem = free; os.totalmem = total; os.availableParallelism = cpu;
    for (const key of Object.keys(process.env)) if (key.startsWith('VIDZEO_REMOTION_')) delete process.env[key];
    Object.assign(process.env, saved);
  }
});
