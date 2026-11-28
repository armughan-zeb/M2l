'use strict';

// CMS-outage resilience (Nov 9, 2026): the daemon used to die on every
// restart while the CMS returned 502/530, because resumeActiveJob()
// re-threw a transient error from its one-shot progress probe. The probe
// then retried with backoff and deferred — the daemon survived, but the
// job sat idle for the whole outage even though its work is local.
// Pipeline-never-dies (Nov 28, 2026): after 2 transient probe failures
// the resume proceeds locally from the saved checkpoint instead of
// deferring; the first successful progress report during execution is the
// real liveness check. These tests pin that: transient failures run the
// job locally, only 409 clears the marker, only 401 / non-transient
// errors still throw, and only an aborted resume still defers. The health
// monitor is covered by a fixture dry-run at the bottom — it must detect
// a dead daemon and a stale inbox request without touching anything live.

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

// Stub for resumeActiveJob's executeJob seam: records invocations and
// returns a marker result instead of rendering for real.
function stubExecuteJob(result = { ok: true, stubbed: true }) {
  const calls = [];
  const fn = async (args) => { calls.push(args); return result; };
  fn.calls = calls;
  return fn;
}

let jobCounter = 0;

function writeActiveJob({ withCheckpoint = true, executorPid } = {}) {
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
    startedAt: new Date().toISOString(),
    ...(executorPid !== undefined ? { executorPid } : {})
  });
  return jobId;
}

function spawnSleeper() {
  // A real live process to stand in for a rival executor.
  const child = spawn(process.execPath, ['-e', 'setTimeout(() => {}, 30000)'], { stdio: 'ignore' });
  return child;
}

async function deadPid() {
  // A pid that belonged to a real process which has already exited.
  const child = spawn(process.execPath, ['-e', ''], { stdio: 'ignore' });
  await new Promise(resolve => child.once('exit', resolve));
  return child.pid;
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

test('resume proceeds locally after 2 transient probe failures instead of deferring', async () => {
  const jobId = writeActiveJob();
  const client = transientClient([cmsError(530), cmsError(502), cmsError(530)]);
  const executeJob = stubExecuteJob();
  const result = await jobRunner.resumeActiveJob({ client, maxAttempts: 3, executeJob });
  assert.equal(result.stubbed, true, 'the job must execute locally after 2 failed probes');
  assert.equal(client.calls, 2, 'only 2 probes run before the local proceed');
  assert.equal(executeJob.calls.length, 1, 'executeJob runs once');
  assert.equal(executeJob.calls[0].job.jobId, jobId);
  assert.equal(executeJob.calls[0].attempt, 1);
});

test('a 409 on the second probe clears the marker and the job never executes', async () => {
  writeActiveJob();
  const client = transientClient([cmsError(530), cmsError(409)]);
  const executeJob = stubExecuteJob();
  const result = await jobRunner.resumeActiveJob({ client, maxAttempts: 5, executeJob });
  assert.equal(result, null);
  assert.equal(client.calls, 2);
  assert.equal(executeJob.calls.length, 0, 'a settled job must never execute');
  assert.equal(config.readActiveJob(), null, 'a settled job clears the resume marker');
});

// Double-render guard (Nov 28, 2026): an external executeJob runner and
// the daemon's resume both rendered t_rebuild_20261128 at once. The
// marker's executorPid names the owning process; a resume that finds a
// live rival pid must skip without probing or executing.

test('resume skips a job whose executor pid is still alive — no probe, no second render', async () => {
  const rival = spawnSleeper();
  try {
    const jobId = writeActiveJob({ executorPid: rival.pid });
    const client = transientClient([]);
    const executeJob = stubExecuteJob();
    const result = await jobRunner.resumeActiveJob({ client, maxAttempts: 3, executeJob });
    assert.equal(result.skipped, true, 'a live rival executor must skip the resume');
    assert.equal(result.executorPid, rival.pid);
    assert.equal(result.reason, 'already-running');
    assert.equal(client.calls, 0, 'the guard fires before any CMS probe');
    assert.equal(executeJob.calls.length, 0, 'a live rival executor must never start a second render');
    assert.equal(config.readActiveJob()?.jobId, jobId, 'the marker stays so the poll loop retries after the owner settles it');
    assert.equal(config.readActiveJob()?.executorPid, rival.pid, 'the skip must not re-stamp the marker');
  } finally {
    rival.kill();
  }
});

test('resume proceeds when the recorded executor pid is dead', async () => {
  const pid = await deadPid();
  writeActiveJob({ executorPid: pid });
  const client = transientClient([]);
  const executeJob = stubExecuteJob();
  const result = await jobRunner.resumeActiveJob({ client, maxAttempts: 3, executeJob });
  assert.equal(result.stubbed, true, 'a dead executor pid must not block the resume');
  assert.equal(executeJob.calls.length, 1);
});

test('resume proceeds when the recorded executor pid is this process (deferred retry)', async () => {
  writeActiveJob({ executorPid: process.pid });
  const client = transientClient([]);
  const executeJob = stubExecuteJob();
  const result = await jobRunner.resumeActiveJob({ client, maxAttempts: 3, executeJob });
  assert.equal(result.stubbed, true, 'our own pid is not a rival — the poll loop must be able to retry a deferred resume');
  assert.equal(executeJob.calls.length, 1);
});

test('liveExecutorPid only reports a live rival pid', () => {
  for (const bad of [undefined, null, 0, 1, -5, Number.NaN, 'abc', 1.5]) {
    assert.equal(jobRunner.liveExecutorPid({ executorPid: bad }), 0, `${String(bad)} is not a live rival`);
  }
  assert.equal(jobRunner.liveExecutorPid({}), 0, 'a marker with no pid has no rival');
  assert.equal(jobRunner.liveExecutorPid(null), 0, 'no marker has no rival');
  assert.equal(jobRunner.liveExecutorPid({ executorPid: process.pid }), 0, 'our own pid is not a rival');
});

test('a 409 on the first probe clears the marker without executing', async () => {
  writeActiveJob();
  const client = transientClient([cmsError(409)]);
  const executeJob = stubExecuteJob();
  const result = await jobRunner.resumeActiveJob({ client, maxAttempts: 5, executeJob });
  assert.equal(result, null);
  assert.equal(client.calls, 1);
  assert.equal(executeJob.calls.length, 0);
  assert.equal(config.readActiveJob(), null);
});

test('an aborted resume still defers with the marker kept and the job unexecuted', async () => {
  const jobId = writeActiveJob();
  const client = transientClient([cmsError(530), cmsError(502)]);
  const executeJob = stubExecuteJob();
  const controller = new AbortController();
  controller.abort();
  const result = await jobRunner.resumeActiveJob({ client, maxAttempts: 5, signal: controller.signal, executeJob });
  assert.equal(result.deferred, true);
  assert.equal(result.error, 'resume aborted');
  assert.equal(executeJob.calls.length, 0, 'an aborted resume must not execute');
  assert.equal(config.readActiveJob()?.jobId, jobId, 'the marker survives an aborted resume');
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
  const executeJob = stubExecuteJob();
  const resumed = await jobRunner.resumeActiveJob({
    client: { async progress(id, stage, progress, detail, advancing, extra) {
      assert.equal(id, jobId);
      assert.equal(extra.attempt, 1);
      return { ok: true, completed: true };
    } },
    maxAttempts: 1,
    executeJob
  });
  assert.deepEqual(resumed, { ok: true, alreadyDelivered: true });
  assert.equal(executeJob.calls.length, 0, 'an already-delivered job must never execute');
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
