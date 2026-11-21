'use strict';

// Tests for scripts/muse-inference-spool.cjs — the resident inference
// transport (spool). No model is involved: the test itself plays the
// resident worker by writing answer/error files into the spool dir.
// Run: node --test tests/muse-inference-spool.test.js

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { test } = require('node:test');

const SPOOL = path.resolve(__dirname, '..', 'scripts', 'muse-inference-spool.cjs');

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-spool-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

function writeHeartbeat(dir, ageMs = 0) {
  fs.writeFileSync(path.join(dir, 'worker-alive.json'), JSON.stringify({
    updatedAt: Date.now() - ageMs, worker: 'test-worker', pending: 0, answered: 0
  }));
}

function writePoolHeartbeat(dir, workerId, ageMs = 0) {
  fs.writeFileSync(path.join(dir, `worker-alive-${workerId}.json`), JSON.stringify({
    updatedAt: Date.now() - ageMs, worker: 'resident-inference', workerId, pending: 0, answered: 0
  }));
}

function runSpool(dir, request, t) {
  const child = spawn(process.execPath, [SPOOL], {
    env: { ...process.env, MUSE_INFERENCE_SPOOL_DIR: dir },
    stdio: ['pipe', 'pipe', 'pipe']
  });
  let stdout = '';
  let stderr = '';
  child.stdout.on('data', data => { stdout += data; });
  child.stderr.on('data', data => { stderr += data; });
  child.stdin.end(JSON.stringify({ type: 'inference', request, instructions: '', responseFormat: {} }));
  const done = new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('close', code => resolve({ code, stdout, stderr }));
  });
  t.after(() => { try { child.kill('SIGKILL'); } catch { /* already gone */ } });
  return { child, done };
}

function requestRow(id, ttlSeconds = 60) {
  const now = Date.now() / 1000;
  return {
    id, owner_id: 'local-user', job_id: 'test-job', kind: 'text',
    operation: 'test.operation', state: 'pending',
    created_at: now, expires_at: now + ttlSeconds,
    messages: [{ role: 'user', content: 'answer me' }], response_format: {}
  };
}

test('spool delivers a worker answer on stdout', async t => {
  const dir = fixture(t);
  writeHeartbeat(dir);
  const id = 'a'.repeat(32);
  const { done } = runSpool(dir, requestRow(id), t);
  // The test plays the resident worker: the request file must appear, then
  // the answer file is written atomically (tmp + rename, as the brief says).
  const requestFile = path.join(dir, id + '.request.json');
  for (let i = 0; i < 100 && !fs.existsSync(requestFile); i++) {
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  assert.ok(fs.existsSync(requestFile), 'spool wrote the request file');
  const answer = JSON.stringify({ content: 'hello from the worker' });
  fs.writeFileSync(path.join(dir, id + '.answer.json.tmp-test'), answer);
  fs.renameSync(path.join(dir, id + '.answer.json.tmp-test'), path.join(dir, id + '.answer.json'));
  const result = await done;
  assert.equal(result.code, 0);
  assert.equal(result.stdout, answer);
  assert.equal(fs.existsSync(requestFile), false, 'spool cleaned up the request file');
});

test('spool treats a fresh per-worker pool heartbeat as alive (no legacy file)', async t => {
  const dir = fixture(t);
  writePoolHeartbeat(dir, 'worker-1');
  const id = 'e'.repeat(32);
  const { done } = runSpool(dir, requestRow(id), t);
  const requestFile = path.join(dir, id + '.request.json');
  for (let i = 0; i < 100 && !fs.existsSync(requestFile); i++) {
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  assert.ok(fs.existsSync(requestFile), 'spool wrote the request file');
  fs.writeFileSync(path.join(dir, id + '.answer.json'), 'pool answer');
  const result = await done;
  assert.equal(result.code, 0);
  assert.equal(result.stdout, 'pool answer');
});

test('spool exits 3 when only stale pool heartbeats exist', async t => {
  const dir = fixture(t);
  writePoolHeartbeat(dir, 'worker-1', 120000);
  writePoolHeartbeat(dir, 'worker-2', 120000);
  const id = '1'.repeat(32);
  const { done } = runSpool(dir, requestRow(id), t);
  const result = await done;
  assert.equal(result.code, 3);
  assert.match(result.stderr, /MUSE_TRANSPORT_UNAVAILABLE/);
});

test('spool delivers when a worker claimed the request file, and cleans up the claim', async t => {
  const dir = fixture(t);
  writeHeartbeat(dir);
  const id = 'f'.repeat(32);
  const { done } = runSpool(dir, requestRow(id), t);
  const requestFile = path.join(dir, id + '.request.json');
  for (let i = 0; i < 100 && !fs.existsSync(requestFile); i++) {
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  assert.ok(fs.existsSync(requestFile), 'spool wrote the request file');
  // A pool worker claims by atomic rename; the spool must keep waiting
  // for the answer (it already holds the payload from stdin) and remove
  // the claim file on delivery.
  const claimFile = path.join(dir, id + '.request.json.claimed-worker-1');
  fs.renameSync(requestFile, claimFile);
  fs.writeFileSync(path.join(dir, id + '.answer.json.tmp-test'), 'claimed answer');
  fs.renameSync(path.join(dir, id + '.answer.json.tmp-test'), path.join(dir, id + '.answer.json'));
  const result = await done;
  assert.equal(result.code, 0);
  assert.equal(result.stdout, 'claimed answer');
  assert.equal(fs.existsSync(requestFile), false, 'spool cleaned up the request file');
  assert.equal(fs.existsSync(claimFile), false, 'spool cleaned up the claim file');
});

test('spool exits 3 with the unavailable marker when the worker heartbeat is stale', async t => {
  const dir = fixture(t);
  writeHeartbeat(dir, 120000); // 2 minutes old — past the 90s limit
  const id = 'b'.repeat(32);
  const { done } = runSpool(dir, requestRow(id), t);
  const result = await done;
  assert.equal(result.code, 3);
  assert.match(result.stderr, /MUSE_TRANSPORT_UNAVAILABLE/);
  assert.equal(fs.existsSync(path.join(dir, id + '.request.json')), false);
});

test('spool exits 3 when no worker heartbeat exists at all', async t => {
  const dir = fixture(t);
  const id = 'c'.repeat(32);
  const { done } = runSpool(dir, requestRow(id), t);
  const result = await done;
  assert.equal(result.code, 3);
  assert.match(result.stderr, /MUSE_TRANSPORT_UNAVAILABLE/);
});

test('spool surfaces a worker error file as exit 1', async t => {
  const dir = fixture(t);
  writeHeartbeat(dir);
  const id = 'd'.repeat(32);
  const { done } = runSpool(dir, requestRow(id), t);
  const requestFile = path.join(dir, id + '.request.json');
  for (let i = 0; i < 100 && !fs.existsSync(requestFile); i++) {
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  assert.ok(fs.existsSync(requestFile), 'spool wrote the request file');
  fs.writeFileSync(path.join(dir, id + '.error.json'), JSON.stringify({ error: 'cannot judge this image' }));
  const result = await done;
  assert.equal(result.code, 1);
  assert.match(result.stderr, /cannot judge this image/);
});

test('spool rejects a request id that is not 32 hex characters', async t => {
  const dir = fixture(t);
  writeHeartbeat(dir);
  const { done } = runSpool(dir, requestRow('not-an-id'), t);
  const result = await done;
  assert.equal(result.code, 1);
  assert.match(result.stderr, /no valid 32-hex id/);
});

// Regression (2026-11-16): a worker that claimed a request and then died
// stranded it until expiry — no other worker could claim it, even with
// the pool alive and idle. The spool rescues a dead worker's claim back
// to the pool; it must never steal a claim from a live worker or rescue
// a claim that is still young.

test('spool rescues a request from a dead worker claim when another worker is alive', async t => {
  const dir = fixture(t);
  writePoolHeartbeat(dir, 'worker-1', 300000); // dead: 5 minutes old
  writePoolHeartbeat(dir, 'worker-2'); // alive
  const id = '9'.repeat(32);
  const { done } = runSpool(dir, requestRow(id), t);
  const requestFile = path.join(dir, id + '.request.json');
  for (let i = 0; i < 100 && !fs.existsSync(requestFile); i++) {
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  assert.ok(fs.existsSync(requestFile), 'spool wrote the request file');
  // worker-1 claims it, then dies: backdate the claim past the 5-minute grace.
  const claimFile = path.join(dir, id + '.request.json.claimed-worker-1');
  fs.renameSync(requestFile, claimFile);
  const old = new Date(Date.now() - 310000);
  fs.utimesSync(claimFile, old, old);
  // The spool must rescue the claim back to the pool instead of waiting
  // for the request to expire; worker-2 then answers it.
  for (let i = 0; i < 100 && !fs.existsSync(requestFile); i++) {
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.ok(fs.existsSync(requestFile), 'spool rescued the dead claim back to the pool');
  fs.writeFileSync(path.join(dir, id + '.answer.json'), 'rescued answer');
  const result = await done;
  assert.equal(result.code, 0);
  assert.equal(result.stdout, 'rescued answer');
  assert.match(result.stderr, /rescued request .* from dead worker worker-1/);
});

test('spool does not rescue a claim from a heartbeating worker', async t => {
  const dir = fixture(t);
  writePoolHeartbeat(dir, 'worker-1'); // alive
  writePoolHeartbeat(dir, 'worker-2'); // alive
  const id = '8'.repeat(32);
  const { done } = runSpool(dir, requestRow(id), t);
  const requestFile = path.join(dir, id + '.request.json');
  for (let i = 0; i < 100 && !fs.existsSync(requestFile); i++) {
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  const claimFile = path.join(dir, id + '.request.json.claimed-worker-1');
  fs.renameSync(requestFile, claimFile);
  const old = new Date(Date.now() - 130000);
  fs.utimesSync(claimFile, old, old);
  // Give the rescue checks time to run: the claim must stay put while
  // worker-1 still heartbeats, even though the claim is old.
  await new Promise(resolve => setTimeout(resolve, 3000));
  assert.equal(fs.existsSync(requestFile), false, 'live claim is not rescued');
  assert.equal(fs.existsSync(claimFile), true);
  fs.writeFileSync(path.join(dir, id + '.answer.json'), 'slow worker answer');
  const result = await done;
  assert.equal(result.code, 0);
  assert.equal(result.stdout, 'slow worker answer');
  assert.doesNotMatch(result.stderr, /rescued request/);
});

test('spool does not rescue a young claim even when its worker is dead', async t => {
  const dir = fixture(t);
  writePoolHeartbeat(dir, 'worker-1', 300000); // dead
  writePoolHeartbeat(dir, 'worker-2'); // alive
  const id = '7'.repeat(32);
  const { done } = runSpool(dir, requestRow(id), t);
  const requestFile = path.join(dir, id + '.request.json');
  for (let i = 0; i < 100 && !fs.existsSync(requestFile); i++) {
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  const claimFile = path.join(dir, id + '.request.json.claimed-worker-1');
  fs.renameSync(requestFile, claimFile);
  // Fresh claim: the worker may still be starting, so no rescue yet.
  await new Promise(resolve => setTimeout(resolve, 3000));
  assert.equal(fs.existsSync(requestFile), false, 'young claim is not rescued');
  assert.equal(fs.existsSync(claimFile), true);
  fs.writeFileSync(path.join(dir, id + '.answer.json'), 'eventual answer');
  const result = await done;
  assert.equal(result.code, 0);
  assert.equal(result.stdout, 'eventual answer');
  assert.doesNotMatch(result.stderr, /rescued request/);
});

// Regression (2026-11-18, job 89b01295): a worker composing a 32-scene
// plan cannot heartbeat mid-turn, so its heartbeat goes stale while it is
// still working. At the old 120s grace the spool "rescued" its live claim
// and discarded ~160s of work. A claim 180s old is past the old grace but
// inside the new 300s grace: it must NOT be rescued.
test('spool does not rescue a 3-minute-old claim from a composing worker', async t => {
  const dir = fixture(t);
  writePoolHeartbeat(dir, 'worker-1', 300000); // stale: composing, not heartbeating
  writePoolHeartbeat(dir, 'worker-2'); // alive
  const id = '6'.repeat(32);
  const { done } = runSpool(dir, requestRow(id), t);
  const requestFile = path.join(dir, id + '.request.json');
  for (let i = 0; i < 100 && !fs.existsSync(requestFile); i++) {
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  const claimFile = path.join(dir, id + '.request.json.claimed-worker-1');
  fs.renameSync(requestFile, claimFile);
  const old = new Date(Date.now() - 180000);
  fs.utimesSync(claimFile, old, old);
  // Give the rescue checks time to run: the claim must stay put.
  await new Promise(resolve => setTimeout(resolve, 3000));
  assert.equal(fs.existsSync(requestFile), false, 'composing worker claim is not rescued');
  assert.equal(fs.existsSync(claimFile), true);
  fs.writeFileSync(path.join(dir, id + '.answer.json'), 'composed answer');
  const result = await done;
  assert.equal(result.code, 0);
  assert.equal(result.stdout, 'composed answer');
  assert.doesNotMatch(result.stderr, /rescued request/);
});
