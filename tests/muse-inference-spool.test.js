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
