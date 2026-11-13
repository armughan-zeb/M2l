'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');
const { once } = require('node:events');
const { test } = require('node:test');

const helper = path.join(__dirname, '..', 'scripts', 'bridge-health.py');
const candidates = process.platform === 'win32' ? ['python', 'python3'] : ['python3', 'python'];
const python = candidates.find(command => {
  const result = spawnSync(command, ['--version'], { encoding: 'utf8', timeout: 5000 });
  return result.status === 0;
});

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'bridge-health-'));
  const bridge = path.join(root, 'bridge');
  const state = path.join(root, 'state');
  fs.mkdirSync(bridge, { recursive: true });
  fs.mkdirSync(state, { recursive: true });
  return { root, bridge, state };
}

function writeRequest(bridge, id, row, response) {
  const requestDir = path.join(bridge, id);
  fs.mkdirSync(requestDir, { recursive: true });
  fs.writeFileSync(path.join(requestDir, 'request.json'), JSON.stringify({ id, ...row }));
  if (response) fs.writeFileSync(path.join(requestDir, 'response.json'), JSON.stringify(response));
  return path.join(requestDir, 'request.json');
}

function inspect(bridge, state, dryRun = true) {
  const result = spawnSync(python, [helper, bridge, state, dryRun ? '1' : '0'], {
    encoding: 'utf8',
    timeout: 10000,
    env: { ...process.env, OPENROUTER_API_KEY: '', VIDZEO_OPENROUTER_API_KEY: '' }
  });
  assert.equal(result.status, 0, `helper failed: ${result.stderr}\n${result.stdout}`);
  const lines = result.stdout.trim().split(/\r?\n/);
  const metricsLine = lines.find(line => line.startsWith('METRICS_JSON:'));
  assert.ok(metricsLine, `metrics missing: ${result.stdout}`);
  return { metrics: JSON.parse(metricsLine.slice('METRICS_JSON:'.length)), output: result.stdout };
}

test('bridge health helper reports pending age and unknown operations by operation', {
  skip: !python ? 'Python runtime unavailable' : false
}, () => {
  const { bridge, state } = fixture();
  const now = Date.now() / 1000;
  writeRequest(bridge, 'pending-request', {
    state: 'pending', operation: 'future_operation_v9', kind: 'text', job_id: 'job-a',
    created_at: now - 75, expires_at: now + 45
  });

  const result = inspect(bridge, state);
  assert.equal(result.metrics.pending, 1);
  assert.equal(result.metrics.unknown_operation_count, 1);
  assert.equal(result.metrics.operations.future_operation_v9.pending, 1);
  assert.equal(result.metrics.oldest_pending.request_id, 'pending-request');
  assert.match(result.output, /operator SLO is 60s/);
});

test('bridge health dry-run detects expiry without changing an inbox request', {
  skip: !python ? 'Python runtime unavailable' : false
}, () => {
  const { bridge, state } = fixture();
  const now = Date.now() / 1000;
  const requestPath = writeRequest(bridge, 'expired-dry-run', {
    state: 'pending', operation: 'visual_review', kind: 'vision', job_id: 'job-b',
    created_at: now - 180, expires_at: now - 60
  });

  const result = inspect(bridge, state, true);
  assert.match(result.output, /expired-dry-run.*WOULD mark expired/);
  assert.equal(result.metrics.pending, 1, 'dry-run keeps the request pending in the metrics');
  assert.equal(result.metrics.operations.visual_review.oldest_pending_age_s >= 180, true);
  assert.equal(JSON.parse(fs.readFileSync(requestPath, 'utf8')).state, 'pending');
});

test('bridge health expires overdue requests under the shared request lock and alerts once', {
  skip: !python ? 'Python runtime unavailable' : false
}, () => {
  const { bridge, state } = fixture();
  const now = Date.now() / 1000;
  const requestPath = writeRequest(bridge, 'expired-live', {
    state: 'pending', operation: 'scriptit.completion', kind: 'text', job_id: 'job-c',
    created_at: now - 180, expires_at: now - 60
  });

  const first = inspect(bridge, state, false);
  assert.match(first.output, /expired-live.*marked expired under the bridge request lock/);
  assert.equal(JSON.parse(fs.readFileSync(requestPath, 'utf8')).state, 'expired');
  const second = inspect(bridge, state, false);
  assert.doesNotMatch(second.output, /ALERT:/);
  assert.equal(second.metrics.expired, 1);
});

test('bridge health never overwrites an answer while the bridge writer holds its lock', {
  skip: !python ? 'Python runtime unavailable' : false
}, async () => {
  const { bridge, state } = fixture();
  const now = Date.now() / 1000;
  const requestPath = writeRequest(bridge, 'expired-locked', {
    state: 'pending', operation: 'visual_review', kind: 'vision', job_id: 'job-lock',
    created_at: now - 180, expires_at: now - 60
  });
  const lockPath = `${requestPath}.lock`;
  const lockCode = [
    'import os,sys,time',
    'fd=os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o600)',
    'if os.name == "nt":',
    ' import msvcrt',
    ' if os.fstat(fd).st_size == 0: os.write(fd,b"0")',
    ' os.lseek(fd,0,os.SEEK_SET); msvcrt.locking(fd,msvcrt.LK_LOCK,1)',
    'else:',
    ' import fcntl',
    ' fcntl.flock(fd,fcntl.LOCK_EX)',
    'print("LOCKED",flush=True); time.sleep(5)'
  ].join('\n');
  const holder = spawn(python, ['-c', lockCode, lockPath], { stdio: ['ignore', 'pipe', 'pipe'] });
  try {
    const [ready] = await Promise.race([
      once(holder.stdout, 'data'),
      new Promise((_, reject) => setTimeout(() => reject(new Error('request lock fixture did not start')), 5000))
    ]);
    assert.match(String(ready), /LOCKED/);
    const result = inspect(bridge, state, false);
    assert.match(result.output, /expired-locked.*could not acquire request lock; left unchanged/);
    assert.equal(JSON.parse(fs.readFileSync(requestPath, 'utf8')).state, 'pending');
  } finally {
    holder.kill();
    await once(holder, 'close').catch(() => {});
  }
});

test('bridge health reports approximate response latency from the saved response timestamp', {
  skip: !python ? 'Python runtime unavailable' : false
}, () => {
  const { bridge, state } = fixture();
  const now = Date.now() / 1000;
  const createdAt = now - 120;
  const requestDir = path.join(bridge, 'answered-request');
  writeRequest(bridge, 'answered-request', {
    state: 'answered', operation: 'scriptit.completion', kind: 'text', job_id: 'job-d',
    created_at: createdAt, expires_at: createdAt + 120
  }, { content: 'A response' });
  fs.utimesSync(path.join(requestDir, 'response.json'), createdAt + 3.2, createdAt + 3.2);

  const result = inspect(bridge, state);
  assert.equal(result.metrics.answered, 1);
  assert.ok(Math.abs(result.metrics.operations['scriptit.completion'].answer_latency_p50_ms - 3200) <= 1);
  assert.ok(Math.abs(result.metrics.operations['scriptit.completion'].answer_latency_p95_ms - 3200) <= 1);
});
