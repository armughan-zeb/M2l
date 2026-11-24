const {test} = require('node:test');
const assert = require('node:assert/strict');
const os = require('node:os');
const {waitForRenderMemory, adaptiveConcurrency, renderSectionHasher, renderBrowserSession} = require('../scripts/render-remotion-manifest');

test('memory admission releases only the owned browser before waiting', async () => {
  const free = os.freemem, total = os.totalmem, cpu = os.availableParallelism;
  const saved = {...process.env};
  let released = 0;
  try {
    for (const key of Object.keys(process.env)) if (key.startsWith('VIDZEO_REMOTION_')) delete process.env[key];
    os.totalmem = () => 8 * 1024 ** 3;
    os.availableParallelism = () => 8;
    // Nov 16, 2026 (GTA VI ef306926): the post-release level used to be
    // 900 MB, which admitted a serial render with a 64 MB media cache
    // Remotion rejects. After the low-memory gate tuning (reserve 5%,
    // decoder 128 MB, worker 192 MB, overhead/margin 64 MB) the bar is
    // ~1114 MB on an 8 GB machine, so 1150 MB admits a serial 1080p
    // render with the 240 MB media-cache floor met.
    os.freemem = () => (released ? 1150 : 200) * 1024 ** 2;
    const plan = await waitForRenderMemory({width:1920,height:1080}, {}, {
      freshMemory:true, memoryWaitMs:1000, releaseOwnedResources:async () => {released++;},
    });
    assert.equal(released,1);
    assert.equal(plan.concurrency,1);
    assert.ok(plan.media_cache_bytes >= 240 * 1024 ** 2);
  } finally {
    os.freemem = free; os.totalmem = total; os.availableParallelism = cpu;
    for (const key of Object.keys(process.env)) if (key.startsWith('VIDZEO_REMOTION_')) delete process.env[key];
    Object.assign(process.env, saved);
  }
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
    // Nov 16, 2026 (GTA VI ef306926, working-set fix): 900 MB free now
    // admits a serial render — the media cache CONFIG is always the full
    // 256 MB cap and the working set fits in headroom (900 available +
    // free swap against the ~873 MB bar) — but parallel encoding stays
    // disallowed under pressure. Only levels below the 512 MB floor wait.
    os.freemem = () => 900 * 1024 ** 2;
    const tight = adaptiveConcurrency({width:1920,height:1080}, {}, {freshMemory:true});
    assert.equal(tight.concurrency, 1);
    assert.equal(tight.disallow_parallel_encoding, true);
    assert.ok(tight.media_cache_bytes >= 240 * 1024 ** 2);
    os.freemem = () => 400 * 1024 ** 2;
    assert.throws(() => adaptiveConcurrency({width:1920,height:1080}, {}, {freshMemory:true}),
      e => e.code === 'VIDZEO_LOW_MEMORY');
    os.freemem = () => 1200 * 1024 ** 2;
    const pressure = adaptiveConcurrency({width:1920,height:1080}, {}, {freshMemory:true});
    assert.equal(pressure.concurrency, 1);
    assert.equal(pressure.disallow_parallel_encoding, true);
    assert.ok(pressure.media_cache_bytes >= 240 * 1024 ** 2);
  } finally {
    os.freemem = free; os.totalmem = total; os.availableParallelism = cpu;
    for (const key of Object.keys(process.env)) if (key.startsWith('VIDZEO_REMOTION_')) delete process.env[key];
    Object.assign(process.env, saved);
  }
});
