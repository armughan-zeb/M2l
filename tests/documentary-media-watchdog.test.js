'use strict';

// Per-scene watchdog + agent-lane claim-grace tests for adapters/documentary.
// Franklin job r_mwztjy7reed4a4: 19 min for 7 shots — 7/7 agent-lane
// misses at 120s each, plus a ~10 min yt-dlp retry loop on one shot.
// These tests prove a stalled scene now falls through (or to a graphic)
// instead of stalling the job, and that an unanswered lane is cheap.
//
// Order (Nov 28, user decision — direct first, agent lane as fallback):
// direct search (yt-dlp + web images) resolves a shot before the lane
// is ever filed; the lane runs only for shots direct could not resolve.
// The order tests below prove that: direct hit => lane never filed,
// direct miss => lane filed and its answer used, lane timeout on a
// fallback shot => graphic, no stall.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const documentary = require('../adapters/documentary');

function makeShots(n) {
  const beats = ['hook', 'background', 'development', 'impact', 'closing'];
  return Array.from({ length: n }, (_, i) => ({
    index: i,
    beat: beats[i % beats.length],
    kind: i % 2 === 0 ? 'video' : 'image',
    from: i * 210,
    dur: 210,
    query: `test documentary query ${i + 1} franklin`,
    sourceUrl: '',
    title: `TEST SHOT ${i + 1}`,
    sub: 'test sub',
    zoomIn: true,
    center: false,
  }));
}

function makeProjectDir() {
  const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-doc-watchdog-'));
  const projectDir = path.join(workDir, 'documentary-project');
  fs.mkdirSync(path.join(projectDir, 'public', 'footage'), { recursive: true });
  fs.mkdirSync(path.join(projectDir, 'public', 'images'), { recursive: true });
  return { workDir, projectDir };
}

// A lane that never returns on its own — it only settles when its abort
// signal fires, like a wedged yt-dlp/fetch. Proves the watchdog's abort
// actually cancels in-flight work instead of just racing past it.
function hangForever(signal) {
  return new Promise((_, reject) => {
    if (signal?.aborted) { reject(new Error('aborted')); return; }
    if (signal) signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
    // No other settlement: without the abort this never resolves.
  });
}

function withBudgets(t, { shotMs, ytMs, claimMs }) {
  const prev = {};
  const set = (name, value) => {
    prev[name] = process.env[name];
    if (value === undefined) delete process.env[name];
    else process.env[name] = String(value);
  };
  set('MUSE_DOCUMENTARY_SHOT_BUDGET_MS', shotMs);
  set('MUSE_DOCUMENTARY_YT_PHASE_BUDGET_MS', ytMs);
  if (claimMs !== undefined) set('MUSE_DOCUMENTARY_MEDIA_CLAIM_GRACE_MS', claimMs);
  t.after(() => {
    for (const [name, value] of Object.entries(prev)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  });
}

function resolveMedia(projectDir, workDir, shots, overrides) {
  return documentary.resolveDocumentaryMedia({
    shots,
    projectDir,
    workDir,
    job: { jobId: 'test-watchdog', title: 'Watchdog Test' },
    topicTag: 'TEST',
    signal: undefined,
    pythonBin: 'python3',
    renderWidth: 1920,
    renderHeight: 1080,
    onProgress: () => {},
    mediaCheckpoints: {},
    mediaCheckpointPath: path.join(projectDir, 'media-checkpoint.json'),
    inputDigest: 'test-digest',
    overrides,
  });
}

test('watchdog: every lane stalled still completes all shots as graphics', async (t) => {
  withBudgets(t, { shotMs: 3000, ytMs: 1500 });
  const { workDir, projectDir } = makeProjectDir();
  try {
    const startedAt = Date.now();
    const result = await resolveMedia(projectDir, workDir, makeShots(3), {
      agentMediaFn: async ({ signal }) => hangForever(signal),
      videoClipFn: async (...args) => hangForever(args[4]?.signal),
      imageAssetFn: async (...args) => hangForever(args[3]?.signal),
    });
    const elapsedMs = Date.now() - startedAt;

    assert.equal(result.resolvedShots.length, 3, 'all 3 shots resolved');
    assert.equal(result.graphicShotCount, 3, 'all 3 fell back to graphics');
    assert.equal(result.videoShotCount, 0);
    assert.equal(result.imageShotCount, 0);
    for (const shot of result.resolvedShots) {
      assert.equal(shot.isGraphic, true);
      assert.equal(shot.assetType, 'image');
      const disk = path.join(projectDir, 'public', shot.assetPath);
      assert.ok(fs.existsSync(disk), `graphic file exists: ${shot.assetPath}`);
      assert.ok(fs.statSync(disk).size >= 1024, 'graphic is a real PNG, not empty');
      assert.equal(fs.readFileSync(disk).subarray(0, 8).toString('hex'), '89504e470d0a1a0a', 'graphic is a PNG');
    }
    // One shot budget (3s) + graphic time, not 3 x unbounded stalls.
    assert.ok(elapsedMs < 20000, `watchdog completed in ${elapsedMs}ms, not minutes`);
    assert.ok(elapsedMs >= 2000, `watchdog waited out the shot budget (${elapsedMs}ms)`);
    const checkpoint = JSON.parse(fs.readFileSync(path.join(projectDir, 'media-checkpoint.json'), 'utf8'));
    assert.equal(Object.keys(checkpoint.shots).length, 3);
    assert.equal(checkpoint.shots['0'].isGraphic, true);
  } finally {
    fs.rmSync(workDir, { recursive: true, force: true });
  }
});

test('watchdog: a hung YouTube phase falls through to a fast image lane', async (t) => {
  withBudgets(t, { shotMs: 3000, ytMs: 1500 });
  const { workDir, projectDir } = makeProjectDir();
  try {
    const jpg = Buffer.concat([Buffer.from([0xff, 0xd8, 0xff, 0xe0]), Buffer.alloc(1200, 1)]);
    const startedAt = Date.now();
    const result = await resolveMedia(projectDir, workDir, makeShots(2), {
      agentMediaFn: async () => [],
      videoClipFn: async (...args) => hangForever(args[4]?.signal),
      imageAssetFn: async (query, sourceUrl, destStem) => {
        const dest = `${destStem}.jpg`;
        fs.writeFileSync(dest, jpg);
        return { path: dest, source: 'https://example.com/test.jpg' };
      },
    });
    const elapsedMs = Date.now() - startedAt;

    assert.equal(result.resolvedShots.length, 2);
    assert.equal(result.imageShotCount, 2, 'both shots landed real images, no graphics');
    assert.equal(result.graphicShotCount, 0);
    for (const shot of result.resolvedShots) {
      assert.equal(shot.isGraphic, undefined);
      assert.ok(fs.existsSync(path.join(projectDir, 'public', shot.assetPath)));
    }
    // The YT phase was genuinely attempted (1.5s budget) then abandoned,
    // not skipped — and the shot did not wait out the full shot budget.
    assert.ok(elapsedMs >= 1000, `YT phase budget was respected (${elapsedMs}ms)`);
    assert.ok(elapsedMs < 15000, `fell through in ${elapsedMs}ms`);
  } finally {
    fs.rmSync(workDir, { recursive: true, force: true });
  }
});

test('mediaBudgets defaults bound YouTube and the lane wait', (t) => {
  const prev = {};
  for (const name of [
    'MUSE_DOCUMENTARY_MEDIA_AGENT_TIMEOUT_MS', 'MUSE_DOCUMENTARY_MEDIA_CLAIM_GRACE_MS',
    'MUSE_DOCUMENTARY_YT_SEARCH_TIMEOUT_MS', 'MUSE_DOCUMENTARY_YT_DOWNLOAD_TIMEOUT_MS',
    'MUSE_DOCUMENTARY_YT_FRAGMENT_RETRIES', 'MUSE_DOCUMENTARY_YT_SOCKET_TIMEOUT_S',
    'MUSE_DOCUMENTARY_SHOT_BUDGET_MS', 'MUSE_DOCUMENTARY_YT_PHASE_BUDGET_MS',
  ]) {
    prev[name] = process.env[name];
    delete process.env[name];
  }
  t.after(() => {
    for (const [name, value] of Object.entries(prev)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  });
  const b = documentary.mediaBudgets();
  assert.equal(b.agentTimeoutMs, 120000);
  assert.equal(b.agentClaimGraceMs, 20000, 'unanswered lane costs ~20s, not 120s');
  assert.equal(b.ytSearchTimeoutMs, 60000);
  assert.equal(b.ytDownloadTimeoutMs, 120000);
  assert.equal(b.ytFragmentRetries, 2, 'kills the 10-retry curl-56 loop');
  assert.equal(b.ytSocketTimeoutSec, 15);
  assert.equal(b.ytPhaseBudgetMs, 300000);
  assert.equal(b.shotBudgetMs, 480000);

  process.env.MUSE_DOCUMENTARY_YT_FRAGMENT_RETRIES = '3';
  assert.equal(documentary.mediaBudgets().ytFragmentRetries, 3, 'budgets re-read env per call');
});

test('makeGraphicFallback produces a real titled PNG title card', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-doc-graphic-'));
  try {
    const dest = path.join(dir, 'shot-1-graphic.png');
    const out = await documentary.makeGraphicFallback(
      { title: 'THE WAIT IS OVER', query: 'test query' },
      dest,
      { width: 1920, height: 1080 },
    );
    assert.equal(out, dest);
    assert.ok(fs.existsSync(dest));
    assert.ok(fs.statSync(dest).size >= 1024);
    assert.equal(fs.readFileSync(dest).subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('resolveDocumentaryTemplate delegates to the helper contract when present, else falls back to default', () => {
  // Contract (built by another agent): lib/documentaryTemplates.js
  // exports resolveDocumentaryTemplate(job) -> { id, dir, label }.
  // The adapter delegates when the helper exists and falls back to the
  // default template dir when it does not (or the picked dir is missing).
  const helperPath = path.join(__dirname, '..', 'lib', 'documentaryTemplates.js');
  const defaultDir = path.join(__dirname, '..', 'one_minute_documentary', 'templates', 'remotion-project');
  if (!fs.existsSync(helperPath)) {
    const picked = documentary.resolveDocumentaryTemplate({ jobId: 'x', title: 'T', documentaryOptions: { template: 'crime' } });
    assert.equal(picked.id, 'default');
    assert.equal(picked.dir, defaultDir);
    assert.ok(fs.existsSync(picked.dir));
    assert.equal(documentary.checkTemplateDir(picked.dir).available, true);
    return;
  }
  const helper = require('../lib/documentaryTemplates');
  const franklin = documentary.resolveDocumentaryTemplate({ jobId: 'r_mwztjy7reed4a4', title: "Franklin's ENTIRE Story Explained", channel: { niche: 'Gaming' } });
  assert.deepEqual(franklin, helper.resolveDocumentaryTemplate({ jobId: 'r_mwztjy7reed4a4', title: "Franklin's ENTIRE Story Explained", channel: { niche: 'Gaming' } }));
  assert.equal(franklin.id, 'standard');
  assert.equal(franklin.dir, defaultDir);
  const crime = documentary.resolveDocumentaryTemplate({ jobId: 'x', title: 'The Vanished Flight', channel: { niche: 'Documentary' } });
  assert.equal(crime.id, 'crime');
  assert.ok(fs.existsSync(crime.dir), 'crime template dir exists');
  assert.equal(documentary.checkTemplateDir(crime.dir).available, true);
});

test('mediaPoolLiveness reads worker heartbeats, not just pool-status', (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-doc-spool-'));
  const prevSpool = process.env.MUSE_INFERENCE_SPOOL_DIR;
  const prevBridge = process.env.MUSE_BRIDGE_ROOT;
  process.env.MUSE_INFERENCE_SPOOL_DIR = dir;
  process.env.MUSE_BRIDGE_ROOT = path.join(dir, 'bridge');
  t.after(() => {
    if (prevSpool === undefined) delete process.env.MUSE_INFERENCE_SPOOL_DIR;
    else process.env.MUSE_INFERENCE_SPOOL_DIR = prevSpool;
    if (prevBridge === undefined) delete process.env.MUSE_BRIDGE_ROOT;
    else process.env.MUSE_BRIDGE_ROOT = prevBridge;
  });
  try {
    assert.equal(documentary.mediaPoolLiveness().live, false, 'no heartbeats: not live');
    const beat = path.join(dir, 'worker-alive-worker-1.json');
    fs.writeFileSync(beat, JSON.stringify({ worker: 'worker-1' }));
    assert.equal(documentary.mediaPoolLiveness().live, true, 'fresh heartbeat: live');
    const staleAt = new Date(Date.now() - 10 * 60 * 1000);
    fs.utimesSync(beat, staleAt, staleAt);
    assert.equal(documentary.mediaPoolLiveness().live, false, '10-min-old heartbeat: not live');
    fs.writeFileSync(path.join(dir, 'pool-status.json'), JSON.stringify({ updatedAt: Date.now(), workers: 4, ownerResponsive: true }));
    assert.equal(documentary.mediaPoolLiveness().live, true, 'fresh pool-status is a fallback liveness signal');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('waitForMediaPoolClaim: unclaimed request resolves false at the grace, claimed resolves true fast', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-doc-claim-'));
  const prevSpool = process.env.MUSE_INFERENCE_SPOOL_DIR;
  const prevBridge = process.env.MUSE_BRIDGE_ROOT;
  process.env.MUSE_INFERENCE_SPOOL_DIR = dir;
  process.env.MUSE_BRIDGE_ROOT = path.join(dir, 'bridge');
  t.after(() => {
    if (prevSpool === undefined) delete process.env.MUSE_INFERENCE_SPOOL_DIR;
    else process.env.MUSE_INFERENCE_SPOOL_DIR = prevSpool;
    if (prevBridge === undefined) delete process.env.MUSE_BRIDGE_ROOT;
    else process.env.MUSE_BRIDGE_ROOT = prevBridge;
  });
  try {
    const id = 'a'.repeat(32);
    const body = JSON.stringify({ type: 'inference', request: { id, operation: 'documentary_media' } });

    // Unclaimed: the Franklin miss — a request nobody picks up.
    fs.writeFileSync(path.join(dir, `${id}.request.json`), body);
    const sinceMs = Date.now();
    const seen = documentary.scanMediaPoolClaims({ spoolDir: dir, bridgeDir: '', sinceMs });
    assert.deepEqual(seen, { unclaimed: 1, claimed: 0, answered: 0 });
    const missStarted = Date.now();
    assert.equal(await documentary.waitForMediaPoolClaim({ sinceMs, graceMs: 1200 }), false);
    const missMs = Date.now() - missStarted;
    assert.ok(missMs >= 1000 && missMs < 8000, `unclaimed miss resolved in ${missMs}ms`);

    // Claimed: a worker renamed the request file.
    fs.rmSync(path.join(dir, `${id}.request.json`), { force: true });
    fs.writeFileSync(path.join(dir, `${id}.request.json.claimed-worker-1`), body);
    const hitStarted = Date.now();
    assert.equal(await documentary.waitForMediaPoolClaim({ sinceMs: Date.now(), graceMs: 5000 }), true);
    assert.ok(Date.now() - hitStarted < 3000, 'claimed request returns immediately');

    // Nothing fresh posted: never aborts on absence alone — it waits
    // until the caller aborts the watch.
    fs.rmSync(path.join(dir, `${id}.request.json.claimed-worker-1`), { force: true });
    const controller = new AbortController();
    setTimeout(() => controller.abort(), 300);
    const absentStarted = Date.now();
    assert.equal(await documentary.waitForMediaPoolClaim({ sinceMs: Date.now(), graceMs: 5000, signal: controller.signal }), false);
    assert.ok(Date.now() - absentStarted < 3000, 'absence waits for the caller abort, not the grace');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('fetchAgentMediaCandidates aborts an unclaimed lane at the claim grace instead of the full budget', async (t) => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-doc-lane-'));
  const spoolDir = path.join(base, 'spool');
  const bridgeDir = path.join(base, 'bridge');
  fs.mkdirSync(spoolDir, { recursive: true });
  fs.mkdirSync(bridgeDir, { recursive: true });
  const saved = {};
  const setEnv = (name, value) => {
    saved[name] = process.env[name];
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  };
  setEnv('MUSE_INFERENCE_SPOOL_DIR', spoolDir);
  setEnv('MUSE_BRIDGE_ROOT', bridgeDir);
  setEnv('MUSE_INFERENCE_URL', undefined);
  setEnv('MUSE_INFERENCE_COMMAND', undefined);
  setEnv('OPENROUTER_API_KEY', undefined);
  setEnv('MUSE_DOCUMENTARY_MEDIA_CLAIM_GRACE_MS', '1500');
  t.after(() => {
    for (const [name, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  });
  try {
    // Pool looks live (fresh heartbeat) but nobody claims the op — the
    // Franklin miss, replayed against a fake spool.
    fs.writeFileSync(path.join(spoolDir, 'worker-alive-worker-1.json'), JSON.stringify({ worker: 'worker-1' }));
    const id = 'b'.repeat(32);
    fs.writeFileSync(
      path.join(spoolDir, `${id}.request.json`),
      JSON.stringify({ type: 'inference', request: { id, operation: 'documentary_media' } }),
    );

    const startedAt = Date.now();
    const out = await documentary.fetchAgentMediaCandidates({
      shot: { beat: 'hook', kind: 'video', query: 'Franklin Clinton gameplay', dur: 210 },
      title: 'Franklin Story',
      topicTag: 'FRANKLIN',
      jobId: 'test-lane-miss',
      workDir: os.tmpdir(),
      timeoutMs: 60000,
    });
    const elapsedMs = Date.now() - startedAt;
    assert.deepEqual(out, []);
    assert.ok(elapsedMs < 15000, `unclaimed lane fell through in ${elapsedMs}ms, not the 60s budget`);
    assert.ok(elapsedMs >= 1200, `claim grace was respected (${elapsedMs}ms)`);
    // The bridge request was posted and then marked expired on abort.
    const posted = fs.readdirSync(bridgeDir);
    assert.equal(posted.length, 1, 'one bridge request was posted');
    const postedReq = JSON.parse(fs.readFileSync(path.join(bridgeDir, posted[0], 'request.json'), 'utf8'));
    assert.equal(postedReq.state, 'expired');
    assert.equal(postedReq.operation, 'documentary_media');
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test('fetchAgentMediaCandidates does NOT abort early when a worker has claimed the op', async (t) => {  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-doc-lane-hit-'));
  const spoolDir = path.join(base, 'spool');
  const bridgeDir = path.join(base, 'bridge');
  fs.mkdirSync(spoolDir, { recursive: true });
  fs.mkdirSync(bridgeDir, { recursive: true });
  const saved = {};
  const setEnv = (name, value) => {
    saved[name] = process.env[name];
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  };
  setEnv('MUSE_INFERENCE_SPOOL_DIR', spoolDir);
  setEnv('MUSE_BRIDGE_ROOT', bridgeDir);
  setEnv('MUSE_INFERENCE_URL', undefined);
  setEnv('MUSE_INFERENCE_COMMAND', undefined);
  setEnv('OPENROUTER_API_KEY', undefined);
  setEnv('MUSE_DOCUMENTARY_MEDIA_CLAIM_GRACE_MS', '1500');
  t.after(() => {
    for (const [name, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  });
  try {
    fs.writeFileSync(path.join(spoolDir, 'worker-alive-worker-1.json'), JSON.stringify({ worker: 'worker-1' }));
    // A claimed request for this op is already visible: the pool knows
    // it, so the lane runs to its own budget (3s here) instead of
    // being cut at the 1.5s grace.
    const id = 'c'.repeat(32);
    fs.writeFileSync(
      path.join(spoolDir, `${id}.request.json.claimed-worker-1`),
      JSON.stringify({ type: 'inference', request: { id, operation: 'documentary_media' } }),
    );

    const startedAt = Date.now();
    const out = await documentary.fetchAgentMediaCandidates({
      shot: { beat: 'hook', kind: 'video', query: 'Franklin Clinton gameplay', dur: 210 },
      title: 'Franklin Story',
      topicTag: 'FRANKLIN',
      jobId: 'test-lane-hit',
      workDir: os.tmpdir(),
      timeoutMs: 3000,
    });
    const elapsedMs = Date.now() - startedAt;
    assert.deepEqual(out, []);
    assert.ok(elapsedMs >= 2500, `claimed lane ran to its budget (${elapsedMs}ms), not the claim grace`);
    assert.ok(elapsedMs < 15000, `claimed lane still bounded (${elapsedMs}ms)`);
  } finally {
    fs.rmSync(base, { recursive: true, force: true });
  }
});

test('order: direct hit never files the agent lane', async (t) => {
  // Direct-first (Nov 28): a shot direct resolves must never touch the
  // pool. The lane spy would throw if called — a filed request is the
  // failure, not just a slow answer.
  const { workDir, projectDir } = makeProjectDir();
  try {
    let laneCalls = 0;
    const mp4 = Buffer.alloc(2048, 2);
    const jpg = Buffer.concat([Buffer.from([0xff, 0xd8, 0xff, 0xe0]), Buffer.alloc(1200, 1)]);
    const result = await resolveMedia(projectDir, workDir, makeShots(2), {
      agentMediaFn: async () => { laneCalls += 1; return []; },
      videoClipFn: async (query, sourceUrl, destPath) => {
        fs.writeFileSync(destPath, mp4);
        return { source: 'https://example.com/direct-clip', videoId: 'direct-vid-1' };
      },
      imageAssetFn: async (query, sourceUrl, destStem) => {
        const dest = `${destStem}.jpg`;
        fs.writeFileSync(dest, jpg);
        return { path: dest, source: 'https://example.com/direct.jpg' };
      },
    });

    assert.equal(laneCalls, 0, 'agent lane was never filed for direct-resolved shots');
    assert.equal(result.resolvedShots.length, 2);
    assert.equal(result.videoShotCount, 1, 'video shot landed a direct clip');
    assert.equal(result.imageShotCount, 1, 'image shot landed a direct image');
    assert.equal(result.graphicShotCount, 0);
    assert.equal(result.resolvedShots[0].assetType, 'video');
    assert.equal(result.resolvedShots[0].videoId, 'direct-vid-1');
    assert.equal(result.resolvedShots[1].assetType, 'image');
    assert.ok(fs.existsSync(path.join(projectDir, 'public', result.resolvedShots[0].assetPath)));
    assert.ok(fs.existsSync(path.join(projectDir, 'public', result.resolvedShots[1].assetPath)));
  } finally {
    fs.rmSync(workDir, { recursive: true, force: true });
  }
});

test('order: direct miss files the lane and its answer is used', async (t) => {
  // Direct-first (Nov 28): when direct engines resolve nothing, the
  // agent lane runs as the last hope and its candidate wins the shot.
  const { workDir, projectDir } = makeProjectDir();
  try {
    let laneCalls = 0;
    const mp4 = Buffer.alloc(2048, 2);
    const result = await resolveMedia(projectDir, workDir, makeShots(1), {
      agentMediaFn: async () => {
        laneCalls += 1;
        return [{ type: 'video', videoId: 'agent-vid-1' }];
      },
      videoClipFn: async (query, sourceUrl, destPath) => {
        // Direct search (empty sourceUrl) finds nothing; the agent
        // candidate's direct-ID download lands the clip.
        if (!sourceUrl || !sourceUrl.includes('watch?v=')) return null;
        fs.writeFileSync(destPath, mp4);
        return { source: sourceUrl, videoId: 'agent-vid-1' };
      },
      imageAssetFn: async () => null,
    });

    assert.equal(laneCalls, 1, 'agent lane was filed once the direct engines missed');
    assert.equal(result.resolvedShots.length, 1);
    assert.equal(result.videoShotCount, 1, 'lane candidate became the shot clip');
    assert.equal(result.graphicShotCount, 0);
    assert.equal(result.resolvedShots[0].assetType, 'video');
    assert.equal(result.resolvedShots[0].videoId, 'agent-vid-1');
    assert.ok(fs.existsSync(path.join(projectDir, 'public', result.resolvedShots[0].assetPath)));
  } finally {
    fs.rmSync(workDir, { recursive: true, force: true });
  }
});

test('order: lane timeout on a fallback shot falls back to a graphic, no stall', async (t) => {
  // Direct-first (Nov 28): direct misses, the lane is filed but hangs
  // (claimed-but-unanswered) — the shot must still complete as a
  // graphic within its budget, not hang the job.
  withBudgets(t, { shotMs: 3000, ytMs: 1500 });
  const { workDir, projectDir } = makeProjectDir();
  try {
    let laneCalls = 0;
    const startedAt = Date.now();
    const result = await resolveMedia(projectDir, workDir, makeShots(1), {
      agentMediaFn: async ({ signal }) => { laneCalls += 1; return hangForever(signal); },
      videoClipFn: async () => null,
      imageAssetFn: async () => null,
    });
    const elapsedMs = Date.now() - startedAt;

    assert.equal(laneCalls, 1, 'agent lane was filed as the fallback');
    assert.equal(result.resolvedShots.length, 1);
    assert.equal(result.graphicShotCount, 1, 'hung lane fell back to a graphic');
    assert.equal(result.videoShotCount, 0);
    assert.equal(result.imageShotCount, 0);
    assert.equal(result.resolvedShots[0].isGraphic, true);
    assert.ok(fs.existsSync(path.join(projectDir, 'public', result.resolvedShots[0].assetPath)));
    // Shot budget (3s) + graphic time, not an unbounded lane wait.
    assert.ok(elapsedMs >= 2000, `shot budget was respected (${elapsedMs}ms)`);
    assert.ok(elapsedMs < 20000, `lane timeout did not stall the job (${elapsedMs}ms)`);
  } finally {
    fs.rmSync(workDir, { recursive: true, force: true });
  }
});
