'use strict';

// Integration test: the daemon's resident inbox + the spool transport +
// a worker answering from the spool — the exact chain a fresh Muse relies
// on. The "worker" here is a scripted echo loop that ONLY answers the
// daemon's own liveness probe (muse.inbox_probe) with the exact token the
// probe asks for. That is the designed liveness check, not inference: it
// proves pickup, transport, and completion end to end. Real inference
// (vision, direction, queries) is the worker agent's job and is never
// scripted here.
// Run: node --test tests/muse-resident-spool-integration.test.js

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { startResidentInbox, probeInbox } = require('../lib/museInbox');
const { runInferenceCommand } = require('../lib/museInference');
const { sleep } = require('../lib/sleep');

const SPOOL_SCRIPT = path.resolve(__dirname, '..', 'scripts', 'muse-inference-spool.cjs');
const logger = { info() {}, warn() {} };

test('resident inbox + spool transport answers the daemon probe in seconds', async t => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-resident-'));
  t.after(() => fs.rmSync(base, { recursive: true, force: true }));
  const bridge = path.join(base, 'bridge');
  const spool = path.join(base, 'spool');
  fs.mkdirSync(bridge, { recursive: true });
  fs.mkdirSync(spool, { recursive: true });

  // The test plays the resident worker: heartbeat + a probe-echo loop.
  // Only the daemon's own liveness probe is ever answered here. The
  // heartbeat uses the per-worker pool file (worker-alive-worker-1.json),
  // so this also proves the spool's pool-liveness check end to end.
  let answered = 0;
  const done = new Set();
  const writeHeartbeat = () => fs.writeFileSync(path.join(spool, 'worker-alive-worker-1.json'), JSON.stringify({
    updatedAt: Date.now(), worker: 'test-worker', workerId: 'worker-1', pending: 0, answered
  }));
  writeHeartbeat();
  const heartbeatTimer = setInterval(writeHeartbeat, 100);
  const workerTimer = setInterval(() => {
    for (const file of fs.readdirSync(spool)) {
      if (!file.endsWith('.request.json')) continue;
      const requestFile = path.join(spool, file);
      let payload;
      try { payload = JSON.parse(fs.readFileSync(requestFile, 'utf8')); } catch { continue; }
      const request = payload?.request || {};
      if (request.operation !== 'muse.inbox_probe') continue;
      const text = String(request.messages?.[0]?.content || '');
      const token = text.split(' ').at(-1);
      if (!token || done.has(request.id)) continue;
      done.add(request.id);
      const id = request.id;
      const tmp = path.join(spool, id + '.answer.json.tmp-test');
      fs.writeFileSync(tmp, token);
      fs.renameSync(tmp, path.join(spool, id + '.answer.json'));
      answered += 1;
    }
  }, 50);
  t.after(() => { clearInterval(heartbeatTimer); clearInterval(workerTimer); });

  const previousSpool = process.env.MUSE_INFERENCE_SPOOL_DIR;
  process.env.MUSE_INFERENCE_SPOOL_DIR = spool;
  t.after(() => {
    if (previousSpool === undefined) delete process.env.MUSE_INFERENCE_SPOOL_DIR;
    else process.env.MUSE_INFERENCE_SPOOL_DIR = previousSpool;
  });

  const stop = startResidentInbox({
    root: bridge, pollMs: 20, logger,
    answer: (request, options) => runInferenceCommand(`node "${SPOOL_SCRIPT}"`, request, base, options)
  });
  try {
    const start = Date.now();
    const proof = await probeInbox({ VIDNETS_AGENT_BRIDGE_DIR: bridge }, { timeoutMs: 15000, pollMs: 25 });
    const elapsedMs = Date.now() - start;
    assert.match(proof.id, /^[a-f0-9]{32}$/);
    assert.ok(elapsedMs < 15000, `probe answered in ${elapsedMs}ms`);
    assert.equal(answered, 1, 'the worker answered exactly once');
    const status = JSON.parse(fs.readFileSync(path.join(bridge, 'resident-status.json'), 'utf8'));
    assert.equal(status.pending, 0);
  } finally { stop(); }
});
