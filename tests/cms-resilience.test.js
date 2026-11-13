'use strict';

// CMS-outage resilience (Nov 9, 2026): the daemon used to die on every
// restart while the CMS returned 502/530, because resumeActiveJob()
// re-threw a transient error from its one-shot progress probe. These
// tests pin the fix: transient errors are retried with backoff and then
// deferred (marker kept, no throw); only 409 clears the marker and only
// 401 / non-transient errors still throw. The health monitor is covered
// by a fixture dry-run at the bottom — it must detect a dead daemon and
// a stale inbox request without touching anything live.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFile, spawn } = require('node:child_process');
const { test } = require('node:test');

// Point the worker state at a scratch dir BEFORE requiring jobRunner:
// lib/config.js reads MUSE_WORK_DIR at load time.
const workRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'cms-resilience-work-'));
process.env.MUSE_WORK_DIR = workRoot;
process.env.MUSE_CMS_RETRY_BASE_MS = '10';
process.env.MUSE_CMS_RETRY_MAX_MS = '50';

const { isTransientCmsError, retryDelayMs, cmsRetryOptions } = require('../lib/cmsRetry');
const config = require('../lib/config');
const jobRunner = require('../lib/jobRunner');

function cmsError(status) {
  return Object.assign(new Error(`CMS returned HTTP ${status}`), { status });
}

function transientClient(failures) {
  // failures: array of errors to throw in order, then succeed.
  let calls = 0;
  return {
    get calls() { return calls; },
    async progress() {
      calls += 1;
      const next = failures[calls - 1];
      if (next) throw next;
    }
  };
}

let jobCounter = 0;

function writeActiveJob({ withCheckpoint = true } = {}) {
  // A fresh job id per test: resume tests share one work root, and a
  // leftover job.json / active marker from another test would let a
  // probe succeed into executeJob (which renders for real).
  jobCounter += 1;
  const jobId = `r_test${jobCounter}`;
  const jobDir = path.join(workRoot, jobId);
  fs.mkdirSync(jobDir, { recursive: true });
  const marker = path.join(jobDir, 'job.json');
  if (withCheckpoint) {
    fs.writeFileSync(
      marker,
      JSON.stringify({ digest: 'x', job: { jobId, title: 'Test title' } })
    );
  } else {
    try { fs.unlinkSync(marker); } catch { /* no checkpoint on purpose */ }
  }
  config.writeActiveJob({
    jobId,
    payload: { jobId, title: 'Test title' },
    attempt: 1,
    digest: 'x',
    startedAt: new Date().toISOString()
  });
  return jobId;
}

test('transient CMS errors are the gateway/overload set, terminal ones are not', () => {
  for (const status of [408, 429, 500, 502, 503, 504, 530]) {
    assert.equal(isTransientCmsError(cmsError(status)), true, `HTTP ${status} must be transient`);
  }
  for (const status of [400, 401, 403, 404, 409, 422]) {
    assert.equal(isTransientCmsError(cmsError(status)), false, `HTTP ${status} must NOT be transient`);
  }
  assert.equal(isTransientCmsError(new Error('Could not reach the CMS at https://cms.vidnets.com: fetch failed')), true);
  assert.equal(isTransientCmsError(new Error('The CMS did not answer in time; its saved job state is unchanged')), true);
  assert.equal(isTransientCmsError(new Error('something else broke')), false);
  assert.equal(isTransientCmsError(null), false);
});

test('retry delay doubles from the base and caps at the max', () => {
  assert.equal(retryDelayMs(0, { baseMs: 2000, maxMs: 60000 }), 2000);
  assert.equal(retryDelayMs(1, { baseMs: 2000, maxMs: 60000 }), 4000);
  assert.equal(retryDelayMs(2, { baseMs: 2000, maxMs: 60000 }), 8000);
  assert.equal(retryDelayMs(5, { baseMs: 2000, maxMs: 60000 }), 60000);
  assert.equal(retryDelayMs(100, { baseMs: 2000, maxMs: 60000 }), 60000);
});

test('retry tunables come from the environment with safe defaults', () => {
  assert.deepEqual(
    cmsRetryOptions({ MUSE_CMS_RESUME_ATTEMPTS: '3', MUSE_CMS_RETRY_BASE_MS: '500', MUSE_CMS_RETRY_MAX_MS: '5000' }),
    { baseMs: 500, maxMs: 5000, maxAttempts: 3 }
  );
  assert.deepEqual(cmsRetryOptions({}), { baseMs: 2000, maxMs: 60000, maxAttempts: 12 });
  assert.equal(cmsRetryOptions({ MUSE_CMS_RESUME_ATTEMPTS: '0' }).maxAttempts, 1);
});

test('resume defers (no throw, marker kept) after bounded transient failures', async () => {
  const jobId = writeActiveJob();
  const client = transientClient([cmsError(530), cmsError(502), cmsError(530)]);
  const result = await jobRunner.resumeActiveJob({ client, maxAttempts: 3 });
  assert.deepEqual(
    { deferred: result.deferred, jobId: result.jobId },
    { deferred: true, jobId }
  );
  assert.equal(client.calls, 3, 'all bounded attempts are used');
  assert.equal(config.readActiveJob()?.jobId, jobId, 'the resume marker must survive a CMS outage');
});

test('resume retries through transient failures, then a 409 clears the marker', async () => {
  writeActiveJob();
  const client = transientClient([cmsError(530), cmsError(530), cmsError(409)]);
  const result = await jobRunner.resumeActiveJob({ client, maxAttempts: 5 });
  assert.equal(result, null);
  assert.equal(client.calls, 3);
  assert.equal(config.readActiveJob(), null, 'a settled job clears the resume marker');
});

test('resume still throws immediately on a revoked credential (401)', async () => {
  const jobId = writeActiveJob();
  const client = transientClient([cmsError(401)]);
  await assert.rejects(
    jobRunner.resumeActiveJob({ client, maxAttempts: 3 }),
    error => error.status === 401
  );
  assert.equal(client.calls, 1, '401 is terminal — no retries');
  assert.equal(config.readActiveJob()?.jobId, jobId, 'the marker is kept so a re-pair can resume');
});

test('resume still throws immediately on a non-transient error', async () => {
  writeActiveJob();
  const client = transientClient([cmsError(400)]);
  await assert.rejects(
    jobRunner.resumeActiveJob({ client, maxAttempts: 3 }),
    error => error.status === 400
  );
  assert.equal(client.calls, 1);
});

test('a successful probe with no local checkpoint clears the marker without running the job', async () => {
  writeActiveJob({ withCheckpoint: false });
  const client = transientClient([]);
  const result = await jobRunner.resumeActiveJob({ client, maxAttempts: 3 });
  assert.equal(result, null);
  assert.equal(client.calls, 1);
  assert.equal(config.readActiveJob(), null);
});

test('an interrupted delivery keeps the checkpoint and a completed CMS receipt clears it', async () => {
  const jobId = writeActiveJob();
  const delivery = await jobRunner.reportExecutionFailure({
    async fail() { assert.fail('A CMS outage must not fail the render'); }
  }, jobId, 1, cmsError(502));
  assert.equal(delivery.deferred, true);
  assert.equal(config.readActiveJob().jobId, jobId);
  const resumed = await jobRunner.resumeActiveJob({
    client: { async progress(id, stage, progress, detail, advancing, extra) {
      assert.equal(id, jobId);
      assert.equal(extra.attempt, 1);
      return { ok: true, completed: true };
    } },
    maxAttempts: 1
  });
  assert.deepEqual(resumed, { ok: true, alreadyDelivered: true });
  assert.equal(config.readActiveJob(), null);
});

test('a CMS sync request receives the saved progress history for its attempt', async () => {
  const jobId = writeActiveJob();
  const directory = config.workDir(jobId);
  jobRunner.writeProgressCheckpoint(directory, jobId, { stage: 'script', progress: 100, detail: 'Narration saved' });
  jobRunner.writeProgressCheckpoint(directory, jobId, { stage: 'video', progress: 62, detail: 'Checking media relevance 20/42' });
  const client = { async sync(id, snapshot) {
    assert.equal(id, jobId);
    assert.equal(snapshot.requestId, 'request-one');
    assert.equal(snapshot.attempt, 1);
    assert.deepEqual(snapshot.logs.map(item => item.stage), ['script', 'video']);
    assert.equal(snapshot.latest.eventId, snapshot.logs[1].eventId);
  } };
  await jobRunner.syncSavedProgress(client, { id: 'request-one', jobId }, config.readActiveJob());
  config.clearActiveJob();
});

test('completion retries a lost response without another artifact upload', async () => {
  const http = require('node:http');
  const { createClient } = require('../lib/cms');
  const attempts = [];
  const server = http.createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    attempts.push({ url: request.url, body: JSON.parse(Buffer.concat(chunks).toString('utf8')) });
    if (attempts.length === 1) {
      response.destroy();
      return;
    }
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ ok: true, duplicate: true }));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const client = createClient({ baseUrl: 'http://127.0.0.1:' + server.address().port, token: 'fixture-token' });
    const body = { scriptText: 'Saved narration for the receipt test.', meta: { attempt: 2 } };
    assert.deepEqual(await client.complete('r_receipt', body), { ok: true, duplicate: true });
    assert.deepEqual(attempts, [
      { url: '/api/muse/agent/jobs/r_receipt/complete', body },
      { url: '/api/muse/agent/jobs/r_receipt/complete', body }
    ]);
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});

const healthMonitor = path.join(__dirname, '..', 'scripts', 'vidnets-health-monitor.sh');
test('health monitor dry-run detects a dead daemon and a stale inbox request without acting', {
  skip: process.platform !== 'linux' || !fs.existsSync(healthMonitor) ? 'requires Linux bash' : false
}, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vidnets-monitor-'));
  const repo = path.join(dir, 'repo');
  const runtime = path.join(dir, 'runtime');
  const bridge = path.join(runtime, 'temp', 'agent-bridge');
  const stateDir = path.join(dir, 'state');
  fs.mkdirSync(path.join(repo, 'work'), { recursive: true });
  fs.mkdirSync(path.join(repo, 'vidnets'), { recursive: true });
  fs.mkdirSync(bridge, { recursive: true });
  fs.mkdirSync(stateDir, { recursive: true });
  // No work/active-job.json: no render can be in progress in the fixture.

  const hookDef = path.join(dir, 'hook.json');
  fs.writeFileSync(hookDef, JSON.stringify({ enabled: false, id: 'vidnets-inbox-watch' }));

  const now = Math.floor(Date.now() / 1000);
  const stale = path.join(bridge, 'stale00000000000000000000000001');
  fs.mkdirSync(stale, { recursive: true });
  fs.writeFileSync(path.join(stale, 'request.json'), JSON.stringify({
    id: 'stale00000000000000000000000001', state: 'pending',
    created_at: now - 3600, expires_at: now - 60, kind: 'vision', operation: 'review', job_id: ''
  }));
  const halfTtl = path.join(bridge, 'half000000000000000000000000002');
  fs.mkdirSync(halfTtl, { recursive: true });
  fs.writeFileSync(path.join(halfTtl, 'request.json'), JSON.stringify({
    id: 'half000000000000000000000000002', state: 'pending',
    created_at: now - 400, expires_at: now + 400, kind: 'text', operation: 'plan', job_id: ''
  }));

  const output = await new Promise((resolve, reject) => {
    execFile('bash', [healthMonitor], {
      timeout: 60000,
      env: {
        ...process.env,
        VIDMON_DRY_RUN: '1',
        VIDMON_REPO: repo,
        VIDMON_RUNTIME_ROOT: runtime,
        VIDMON_BRIDGE_DIR: bridge,
        VIDMON_VIDNETS_URL: 'http://127.0.0.1:9',
        VIDMON_CMS_URL: 'http://127.0.0.1:9',
        VIDMON_HOOK_DEF: hookDef,
        VIDMON_STATE_DIR: stateDir,
        VIDMON_DAEMON_PATTERN: 'definitely-no-such-daemon-xyz'
      }
    }, (error, stdout, stderr) => {
      if (error && error.code !== 2) return reject(new Error(`monitor failed: ${error.message}\n${stdout}\n${stderr}`));
      resolve({ code: error ? error.code : 0, stdout: String(stdout) });
    });
  });

  assert.equal(output.code, 2, 'fixture faults must make the monitor report needs_user');
  assert.match(output.stdout, /vidnets: DOWN; WOULD restart/);
  assert.match(output.stdout, /daemon: NOT RUNNING and Vidnets is down/);
  assert.match(output.stdout, /hook: vidnets-inbox-watch is DISABLED/);
  assert.match(output.stdout, /inbox: request stale00000000000000000000000001 expired.*WOULD mark expired/);
  assert.match(output.stdout, /half000000000000000000000000002.*operator SLO is 60s/);
  assert.match(output.stdout, /HEALTH_MONITOR result=needs_user/);
  // Dry-run must not have changed the fixture: the stale request is untouched.
  const staleAfter = JSON.parse(fs.readFileSync(path.join(stale, 'request.json'), 'utf8'));
  assert.equal(staleAfter.state, 'pending', 'dry-run must not mark requests expired');
});

test('health monitor permits checkpoint-safe daemon recovery when Vidnets is healthy', {
  skip: process.platform !== 'linux' || !fs.existsSync(healthMonitor) ? 'requires Linux bash' : false
}, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vidnets-checkpoint-monitor-'));
  const repo = path.join(dir, 'repo');
  const runtime = path.join(dir, 'runtime');
  const bridge = path.join(runtime, 'temp', 'agent-bridge');
  const stateDir = path.join(dir, 'state');
  const hookStateDir = path.join(dir, 'hook-state');
  const binDir = path.join(dir, 'bin');
  fs.mkdirSync(path.join(repo, 'work'), { recursive: true });
  fs.mkdirSync(bridge, { recursive: true });
  fs.mkdirSync(stateDir, { recursive: true });
  fs.mkdirSync(hookStateDir, { recursive: true });
  fs.mkdirSync(binDir, { recursive: true });
  fs.writeFileSync(path.join(repo, 'work', 'active-job.json'), JSON.stringify({
    jobId: 'r_checkpoint_resume', payload: { jobId: 'r_checkpoint_resume' }
  }));
  const hookDef = path.join(dir, 'hook.json');
  fs.writeFileSync(hookDef, JSON.stringify({ enabled: true, id: 'vidnets-inbox-watch' }));
  fs.writeFileSync(path.join(hookStateDir, 'last-run'), String(Math.floor(Date.now() / 1000)));

  const fakeCurl = path.join(binDir, 'curl');
  fs.writeFileSync(fakeCurl, [
    '#!/bin/sh',
    'case "$*" in',
    '  */api/status*) printf \'%s\\n\' \'{"ready":true,"memory":{"admission_state":"normal","available_mb":2048,"minimum_available_mb":384}}\' ;;',
    '  *http_code*) printf 200 ;;',
    '  *) exit 22 ;;',
    'esac',
    ''
  ].join('\n'));
  fs.chmodSync(fakeCurl, 0o755);

  const output = await new Promise((resolve, reject) => {
    execFile('bash', [healthMonitor], {
      timeout: 60000,
      env: {
        ...process.env,
        PATH: `${binDir}${path.delimiter}${process.env.PATH || ''}`,
        VIDMON_DRY_RUN: '1',
        VIDMON_REPO: repo,
        VIDMON_RUNTIME_ROOT: runtime,
        VIDMON_BRIDGE_DIR: bridge,
        VIDMON_VIDNETS_URL: 'http://vidnets.test',
        VIDMON_CMS_URL: 'https://cms.test',
        VIDMON_HOOK_DEF: hookDef,
        VIDMON_HOOK_STATE_DIR: hookStateDir,
        VIDMON_STATE_DIR: stateDir,
        VIDMON_DAEMON_PATTERN: 'definitely-no-such-daemon-checkpoint-xyz'
      }
    }, (error, stdout, stderr) => {
      if (error && error.code !== 2) return reject(new Error(`monitor failed: ${error.message}\n${stdout}\n${stderr}`));
      resolve({ code: error ? error.code : 0, stdout: String(stdout) });
    });
  });

  assert.equal(output.code, 2, 'a missing daemon is an actionable monitor finding');
  assert.match(output.stdout, /daemon: NOT RUNNING; WOULD restart and resume the saved job checkpoint/);
  assert.doesNotMatch(output.stdout, /restart withheld to avoid duplicate work/);
});

test('health monitor restarts Vidnets for a checkpoint only after proving its server process is gone', {
  skip: process.platform !== 'linux' || !fs.existsSync(healthMonitor) ? 'requires Linux bash and /proc' : false
}, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vidnets-dead-server-monitor-'));
  const repo = path.join(dir, 'repo');
  const runtime = path.join(dir, 'runtime');
  const bridge = path.join(runtime, 'temp', 'agent-bridge');
  const stateDir = path.join(dir, 'state');
  const binDir = path.join(dir, 'bin');
  fs.mkdirSync(path.join(repo, 'work'), { recursive: true });
  fs.mkdirSync(path.join(runtime, 'secure'), { recursive: true });
  fs.mkdirSync(bridge, { recursive: true });
  fs.mkdirSync(stateDir, { recursive: true });
  fs.mkdirSync(binDir, { recursive: true });
  fs.writeFileSync(path.join(repo, 'work', 'active-job.json'), JSON.stringify({
    jobId: 'r_server_recovery', payload: { jobId: 'r_server_recovery' }
  }));
  fs.writeFileSync(path.join(runtime, 'secure', 'agent-service.json'), JSON.stringify({
    pid: 2147483647, port: 5010, resource_root: path.join(repo, 'vidnets')
  }));
  const fakeCurl = path.join(binDir, 'curl');
  fs.writeFileSync(fakeCurl, '#!/bin/sh\nexit 22\n');
  fs.chmodSync(fakeCurl, 0o755);

  const output = await new Promise((resolve, reject) => {
    execFile('bash', [healthMonitor], {
      timeout: 60000,
      env: {
        ...process.env,
        PATH: `${binDir}${path.delimiter}${process.env.PATH || ''}`,
        VIDMON_DRY_RUN: '1',
        VIDMON_REPO: repo,
        VIDMON_RUNTIME_ROOT: runtime,
        VIDMON_BRIDGE_DIR: bridge,
        VIDMON_VIDNETS_URL: 'http://vidnets.test',
        VIDMON_CMS_URL: 'https://cms.test',
        VIDMON_HOOK_DEF: path.join(dir, 'missing-hook.json'),
        VIDMON_STATE_DIR: stateDir,
        VIDMON_DAEMON_PATTERN: 'definitely-no-such-daemon-server-xyz'
      }
    }, (error, stdout, stderr) => {
      if (error && error.code !== 2) return reject(new Error(`monitor failed: ${error.message}\n${stdout}\n${stderr}`));
      resolve({ code: error ? error.code : 0, stdout: String(stdout) });
    });
  });

  assert.equal(output.code, 2, 'an unavailable Vidnets server is an actionable monitor finding');
  assert.match(output.stdout, /Vidnets DOWN; service process is confirmed dead; WOULD restart and reattach the saved Vidnets job/);
});

test('health monitor withholds Vidnets restart when the active server process is still alive', {
  skip: process.platform !== 'linux' || !fs.existsSync(healthMonitor) ? 'requires Linux bash and /proc' : false
}, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vidnets-live-server-monitor-'));
  const repo = path.join(dir, 'repo');
  const runtime = path.join(dir, 'runtime');
  const bridge = path.join(runtime, 'temp', 'agent-bridge');
  const stateDir = path.join(dir, 'state');
  const binDir = path.join(dir, 'bin');
  const resourceRoot = path.join(repo, 'vidnets');
  fs.mkdirSync(path.join(repo, 'work'), { recursive: true });
  fs.mkdirSync(path.join(runtime, 'secure'), { recursive: true });
  fs.mkdirSync(bridge, { recursive: true });
  fs.mkdirSync(stateDir, { recursive: true });
  fs.mkdirSync(binDir, { recursive: true });
  fs.mkdirSync(resourceRoot, { recursive: true });
  fs.writeFileSync(path.join(repo, 'work', 'active-job.json'), JSON.stringify({
    jobId: 'r_server_still_alive', payload: { jobId: 'r_server_still_alive' }
  }));
  const serverMain = path.join(resourceRoot, 'main.py');
  fs.writeFileSync(serverMain, 'import time\ntime.sleep(60)\n');
  const server = spawn('python3', ['-u', serverMain], { stdio: 'ignore' });
  const fakeCurl = path.join(binDir, 'curl');
  fs.writeFileSync(fakeCurl, '#!/bin/sh\nexit 22\n');
  fs.chmodSync(fakeCurl, 0o755);
  try {
    await new Promise(resolve => setTimeout(resolve, 150));
    fs.writeFileSync(path.join(runtime, 'secure', 'agent-service.json'), JSON.stringify({
      pid: server.pid, port: 5010, resource_root: resourceRoot
    }));

    const output = await new Promise((resolve, reject) => {
      execFile('bash', [healthMonitor], {
        timeout: 60000,
        env: {
          ...process.env,
          PATH: `${binDir}${path.delimiter}${process.env.PATH || ''}`,
          VIDMON_DRY_RUN: '1',
          VIDMON_REPO: repo,
          VIDMON_RUNTIME_ROOT: runtime,
          VIDMON_BRIDGE_DIR: bridge,
          VIDMON_VIDNETS_URL: 'http://vidnets.test',
          VIDMON_CMS_URL: 'https://cms.test',
          VIDMON_HOOK_DEF: path.join(dir, 'missing-hook.json'),
          VIDMON_STATE_DIR: stateDir,
          VIDMON_DAEMON_PATTERN: 'definitely-no-such-daemon-live-xyz'
        }
      }, (error, stdout, stderr) => {
        if (error && error.code !== 2) return reject(new Error(`monitor failed: ${error.message}\n${stdout}\n${stderr}`));
        resolve({ code: error ? error.code : 0, stdout: String(stdout) });
      });
    });

    assert.equal(output.code, 2, 'the unhealthy API remains an actionable monitor finding');
    assert.match(output.stdout, /service process state is alive; restart withheld because a live render cannot be ruled out/);
    assert.doesNotMatch(output.stdout, /WOULD restart and reattach the saved Vidnets job/);
  } finally {
    server.kill('SIGTERM');
  }
});
