'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const {
  resolveRemotionWorkers,
  readRequestedWorkers,
  DEFAULT_WORKERS,
} = require('../lib/remotionWorkers');
const { buildRequest } = require('../adapters/vidnets');

const SYS_ROOMY = { totalMb: 7934, availableMb: 6000, cpuCount: 8 };
const SYS_THIS_VM = { totalMb: 7934, availableMb: 750, cpuCount: 4 };
const SYS_LIVE_JOB = { totalMb: 7934, availableMb: 916, cpuCount: 4 };

test('default requested workers is the tested-safe value (2)', () => {
  assert.equal(DEFAULT_WORKERS, 2);
  assert.equal(readRequestedWorkers({}), 2);
  const plan = resolveRemotionWorkers({}, SYS_ROOMY);
  assert.equal(plan.requested, 2);
  assert.equal(plan.chosen, 2);
  assert.equal(plan.degraded, false);
});

test('MUSE_RENDER_CONCURRENCY is honoured, clamped to 1..8', () => {
  assert.equal(readRequestedWorkers({ MUSE_RENDER_CONCURRENCY: '5' }), 5);
  assert.equal(readRequestedWorkers({ MUSE_RENDER_CONCURRENCY: '0' }), 2); // 0 is invalid -> default
  assert.equal(readRequestedWorkers({ MUSE_RENDER_CONCURRENCY: '99' }), 8);
  assert.equal(readRequestedWorkers({ MUSE_RENDER_CONCURRENCY: 'banana' }), 2);
});

test('aliases VIDZEO_REMOTION_WORKERS / MUSE_REMOTION_WORKERS / VIDZEO_REMOTION_CONCURRENCY work', () => {
  assert.equal(readRequestedWorkers({ VIDZEO_REMOTION_WORKERS: '5' }), 5);
  assert.equal(readRequestedWorkers({ MUSE_REMOTION_WORKERS: '3' }), 3);
  assert.equal(readRequestedWorkers({ VIDZEO_REMOTION_CONCURRENCY: '4' }), 4);
  // First set wins: canonical name beats aliases.
  assert.equal(readRequestedWorkers({
    MUSE_RENDER_CONCURRENCY: '2', VIDZEO_REMOTION_WORKERS: '5',
  }), 2);
});

test('CPU cap: 4 vCPU allows at most 3 workers even when 5 are requested', () => {
  const plan = resolveRemotionWorkers({ MUSE_RENDER_CONCURRENCY: '5' }, SYS_ROOMY);
  // SYS_ROOMY has 8 CPUs, so no CPU degrade there; use a 4-CPU roomy box.
  assert.equal(plan.chosen, 5);
  const capped = resolveRemotionWorkers(
    { MUSE_RENDER_CONCURRENCY: '5' }, { totalMb: 16000, availableMb: 12000, cpuCount: 4 });
  assert.equal(capped.cpuLimit, 3);
  assert.equal(capped.chosen, 3);
  assert.equal(capped.degraded, true);
  assert.match(capped.reason, /cpu/);
});

test('memory auto-degrade: at live-job memory (~916 MB free) even 5 degrades to 1', () => {
  const plan = resolveRemotionWorkers({ MUSE_RENDER_CONCURRENCY: '5' }, SYS_LIVE_JOB);
  assert.equal(plan.requested, 5);
  assert.equal(plan.chosen, 1);
  assert.equal(plan.degraded, true);
  assert.match(plan.reason, /memory/);
  assert.equal(plan.estimatedWorkerMb, 400);
});

test('memory auto-degrade: ~750 MB free also degrades to 1 (never 0, never fails)', () => {
  const plan = resolveRemotionWorkers({ MUSE_RENDER_CONCURRENCY: '5' }, SYS_THIS_VM);
  assert.equal(plan.chosen, 1);
  assert.ok(plan.chosen >= 1);
});

test('5 workers are granted when memory and CPU actually allow it', () => {
  const plan = resolveRemotionWorkers(
    { MUSE_RENDER_CONCURRENCY: '5' }, { totalMb: 16000, availableMb: 12000, cpuCount: 8 });
  assert.equal(plan.chosen, 5);
  assert.equal(plan.degraded, false);
});

test('below the 384 MB floor the single-worker fallback still proceeds', () => {
  const plan = resolveRemotionWorkers(
    { MUSE_RENDER_CONCURRENCY: '5' }, { totalMb: 7934, availableMb: 200, cpuCount: 4 });
  assert.equal(plan.chosen, 1);
  assert.equal(plan.degraded, true);
  assert.match(plan.reason, /low-memory/);
});

test('Vidnets buildRequest defaults to 2 workers and passes 5 through', () => {
  const job = { jobId: 'test-job', title: 'Test', channel: { name: 'Train Crew' } };
  const saved = process.env.MUSE_RENDER_CONCURRENCY;
  try {
    delete process.env.MUSE_RENDER_CONCURRENCY;
    delete process.env.VIDZEO_REMOTION_WORKERS;
    const def = buildRequest(job, 'Some narration.', '/tmp');
    assert.equal(def.semantic_render_concurrency, 2);
    assert.equal(def.concurrency, 2);
    process.env.MUSE_RENDER_CONCURRENCY = '5';
    const five = buildRequest(job, 'Some narration.', '/tmp');
    assert.equal(five.semantic_render_concurrency, 5);
    assert.equal(five.concurrency, 5);
  } finally {
    if (saved === undefined) delete process.env.MUSE_RENDER_CONCURRENCY;
    else process.env.MUSE_RENDER_CONCURRENCY = saved;
  }
});

test('server wiring: native_render_policy forwards the worker count to the Node script', () => {
  // The wiring gap (Nov 10, 2026): the worker sends `concurrency` AND
  // `semantic_render_concurrency`, but the server only forwarded
  // semantic_render_concurrency into the render config. The policy must
  // read all three keys and set VIDZEO_REMOTION_CONCURRENCY on the Node
  // subprocess env — otherwise the requested count never reaches the
  // render script and it silently uses its own default.
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(
    path.join(__dirname, '..', 'vidnets', 'server', 'services', 'native_render_policy.py'), 'utf8');
  assert.ok(src.includes('cfg.get("semantic_render_concurrency")'),
    'policy must read semantic_render_concurrency from the render config');
  assert.ok(src.includes('cfg.get("concurrency")'),
    'policy must also read the plain concurrency key');
  assert.ok(src.includes('env["VIDZEO_REMOTION_CONCURRENCY"]'),
    'policy must put the count into VIDZEO_REMOTION_CONCURRENCY for the Node script');
});

test('one video, N workers: the manifest script shards ONE composition via renderMedia concurrency', () => {
  // Remotion renderMedia `concurrency` = N Chromium workers rendering
  // frames of ONE composition, stitched into ONE output file. It is NOT
  // N separate renders. The main path builds ONE renderOptions object
  // carrying `concurrency` and hands it to a single renderMedia call;
  // the sectioned fallback renders frame ranges of the same video and
  // joins the sections ("joining saved video sections").
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(
    path.join(__dirname, '..', 'vidnets', 'scripts', 'render-remotion-manifest.js'), 'utf8');
  const renderOptions = src.indexOf('const renderOptions = {');
  assert.ok(renderOptions > 0, 'main path must build a single renderOptions object');
  const optionsBlock = src.slice(renderOptions, renderOptions + 2000);
  assert.ok(/^\s*concurrency,$/m.test(optionsBlock),
    'renderOptions must carry the single concurrency value into renderMedia');
  assert.ok(src.includes('joining saved video sections'),
    'sectioned fallback must join sections of the same video');
  assert.ok(src.includes('frameRange: [start, end], concurrency: workers'),
    'section renders must shard frame ranges with the worker count');
});

test('requested 5 can never run as 5 on this 4-vCPU box (CPU cap is 3)', () => {
  // Both resolvers cap by CPU: 4 vCPU -> cpuLimit 3. Requesting 5 is
  // allowed, but the honest effective maximum here is 3 — and only
  // when ~1.8 GB is actually free at render start.
  const plan = resolveRemotionWorkers(
    { MUSE_RENDER_CONCURRENCY: '5' }, { totalMb: 16000, availableMb: 12000, cpuCount: 4 });
  assert.equal(plan.cpuLimit, 3);
  assert.equal(plan.chosen, 3);
  const { adaptiveConcurrency } = require('../vidnets/scripts/render-remotion-manifest');
  const os = require('node:os');
  const savedParallelism = os.availableParallelism;
  const saved = { ...process.env };
  try {
    os.availableParallelism = () => 4;
    process.env.MUSE_RENDER_CONCURRENCY = '5';
    process.env.VIDZEO_REMOTION_AVAILABLE_RAM_MB = '12000';
    const five = adaptiveConcurrency({ width: 1920, height: 1080 }, { scenes: [] }, {});
    assert.ok(five.concurrency <= 3, `script must cap 5 at 3 on 4 vCPU, got ${five.concurrency}`);
    assert.equal(five.cpu_limit, 3);
  } finally {
    os.availableParallelism = savedParallelism;
    process.env = saved;
  }
});

test('Vidnets render script honours MUSE_RENDER_CONCURRENCY and defaults to 2', () => {
  // adaptiveConcurrency is exported by render-remotion-manifest.js.
  const { adaptiveConcurrency } = require('../vidnets/scripts/render-remotion-manifest');
  const comp = { width: 1920, height: 1080 };
  const saved = { ...process.env };
  try {
    for (const k of ['VIDZEO_REMOTION_CONCURRENCY', 'MUSE_RENDER_CONCURRENCY',
      'VIDZEO_REMOTION_WORKERS', 'MUSE_REMOTION_WORKERS']) delete process.env[k];
    const def = adaptiveConcurrency(comp, { scenes: [] },
      {});
    // 8 GB box, plenty free in this stubbed env? No: uses real freemem(),
    // so only assert the request path degrades safely, never above 2 by default.
    assert.ok(def.concurrency >= 1 && def.concurrency <= 2,
      `default concurrency must be 1-2, got ${def.concurrency}`);
    process.env.MUSE_RENDER_CONCURRENCY = '5';
    const five = adaptiveConcurrency(comp, { scenes: [] }, {});
    assert.ok(five.concurrency >= 1 && five.concurrency <= 5,
      `requested 5 must yield 1-5, got ${five.concurrency}`);
  } finally {
    process.env = saved;
  }
});
