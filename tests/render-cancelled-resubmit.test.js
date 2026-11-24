'use strict';

// CMS resume keeps the same job: when the CMS re-dispatches a job, the
// daemon must resume the SAME video job (same Vidnets id) from the receipt
// in vidnets-job.json — never submit a fresh one while the recorded job is
// still alive. The one exception is a user-cancelled video job: the server
// never retries it, so the resume must start a FRESH video job for the same
// CMS job (same script, same work dir, same checkpoints) instead of looping
// on the dead id. This test proves the exception path.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { render } = require('../adapters/vidnets');

function work(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-cancelled-resubmit-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

test('a cancelled video job starts a fresh video job for the same CMS job (once)', async t => {
  const dir = work(t);
  const jobFile = path.join(dir, 'vidnets-job.json');
  const logFile = path.join(dir, 'fake-vidnets.log');
  const deadId = 'old-dead-job';
  const freshId = 'fresh-job-1';
  // The receipt the daemon recorded for the CMS job: the video job that the
  // user cancelled. A resume must NOT keep following this id.
  fs.writeFileSync(jobFile, JSON.stringify({
    id: deadId, jobId: 'cms-job', cmsAttempt: 2, submittedAt: '2026-11-21T16:05:00.000Z'
  }));

  // Fake `vidnets` CLI: start ok, submit mints the fresh id, the dead job
  // reports cancelled, and the fresh job reports cancelled too (so the
  // once-only guard is exercised — it must NOT submit a second fresh job).
  const fakeCli = path.join(dir, 'fake-vidnets.js');
  fs.writeFileSync(fakeCli, [
    "'use strict';",
    "const fs = require('node:fs');",
    `const logFile = ${JSON.stringify(logFile)};`,
    `const deadId = ${JSON.stringify(deadId)};`,
    `const freshId = ${JSON.stringify(freshId)};`,
    "const args = process.argv.slice(2);",
    "const cmd = args[0];",
    "fs.appendFileSync(logFile, cmd + ' ' + args.slice(1).join(' ') + '\\n');",
    "const out = obj => { process.stdout.write(JSON.stringify(obj) + '\\n'); };",
    "if (cmd === 'start') { out({ ready: true }); }",
    "else if (cmd === 'submit') { out({ item: { id: freshId } }); }",
    "else if (cmd === 'job' && args[1] === deadId) { out({ item: { id: deadId, status: 'cancelled', progress: 24, progress_text: 'Cancelled by user' } }); }",
    "else if (cmd === 'job' && args[1] === freshId) { out({ item: { id: freshId, status: 'cancelled', progress: 5, progress_text: 'Cancelled by user' } }); }",
    "else if (cmd === 'retry') { out({ ok: true }); }",
    "else { out({ error: 'unknown fake command ' + cmd }); process.exitCode = 1; }"
  ].join('\n'));

  const job = {
    jobId: 'cms-job',
    title: 'How Rockstar Built the Most Detailed Map in Gaming History',
    targetDurationMinutes: 1,
    channel: { name: 'Test Channel', niche: 'documentary' }
  };
  const scriptText = 'A short test narration. '.repeat(30);
  const env = {
    ...process.env,
    MUSE_VIDNETS_COMMAND: `node ${fakeCli}`,
    MUSE_INFERENCE_COMMAND: 'true',
    MUSE_RESIDENT_INBOX: '1',
    MUSE_VIDNETS_POLL_MS: '500'
  };
  // run render in-process with the fake CLI on the environment
  const previousEnv = { ...process.env };
  Object.assign(process.env, env);
  try {
    await assert.rejects(
      render(job, scriptText, dir, { attempt: 3, onProgress: () => {} }),
      /Vidnets render failed:.*[Cc]ancelled/
    );
  } finally {
    for (const key of Object.keys(env)) {
      if (previousEnv[key] === undefined) delete process.env[key];
      else process.env[key] = previousEnv[key];
    }
  }

  const calls = fs.readFileSync(logFile, 'utf8').trim().split('\n').filter(Boolean);
  const submits = calls.filter(line => line.startsWith('submit '));
  // Exactly one fresh submit: the dead id was abandoned, the fresh id was
  // followed, and when the fresh id was ALSO cancelled the guard refused
  // a second fresh submit (it failed loudly instead of looping).
  assert.equal(submits.length, 1, `expected exactly 1 submit, got ${submits.length}: ${calls.join(' | ')}`);
  assert.ok(calls.some(line => line === `job ${deadId}`), 'the dead job must have been polled first');
  assert.ok(calls.some(line => line === `job ${freshId}`), 'the fresh job must have been followed');

  // The receipt now points at the fresh video job and records where it came
  // from; the dead receipt is backed up, never lost.
  const receipt = JSON.parse(fs.readFileSync(jobFile, 'utf8'));
  assert.equal(receipt.id, freshId);
  assert.equal(receipt.jobId, 'cms-job');
  assert.equal(receipt.resubmittedFrom, deadId);
  assert.equal(receipt.cmsAttempt, 3);
  assert.ok(fs.existsSync(`${jobFile}.bak-${deadId}`), 'dead receipt backup must exist');
  const backup = JSON.parse(fs.readFileSync(`${jobFile}.bak-${deadId}`, 'utf8'));
  assert.equal(backup.id, deadId);
});

test('a live recorded video job is followed, not re-submitted', async t => {
  const dir = work(t);
  const jobFile = path.join(dir, 'vidnets-job.json');
  const logFile = path.join(dir, 'fake-vidnets.log');
  const liveId = 'live-job-1';
  fs.writeFileSync(jobFile, JSON.stringify({
    id: liveId, jobId: 'cms-job', cmsAttempt: 2, submittedAt: '2026-11-22T01:08:33.000Z'
  }));

  // The live job stays processing, then the operator aborts the render.
  // The point is only that NO submit happens while the recorded job is
  // alive — the same CMS job keeps the same video job.
  const fakeCli = path.join(dir, 'fake-vidnets.js');
  fs.writeFileSync(fakeCli, [
    "'use strict';",
    "const fs = require('node:fs');",
    `const logFile = ${JSON.stringify(logFile)};`,
    `const liveId = ${JSON.stringify(liveId)};`,
    "const args = process.argv.slice(2);",
    "const cmd = args[0];",
    "fs.appendFileSync(logFile, cmd + ' ' + args.slice(1).join(' ') + '\\n');",
    "const out = obj => { process.stdout.write(JSON.stringify(obj) + '\\n'); };",
    "if (cmd === 'start') { out({ ready: true }); }",
    "else if (cmd === 'submit') { out({ item: { id: 'should-never-be-created' } }); }",
    "else if (cmd === 'job' && args[1] === liveId) { out({ item: { id: liveId, status: 'processing', progress: 60, progress_text: 'Finding scene 2/124' } }); }",
    "else if (cmd === 'retry') { out({ ok: true }); }",
    "else { out({ error: 'unknown fake command ' + cmd }); process.exitCode = 1; }"
  ].join('\n'));

  const job = {
    jobId: 'cms-job',
    title: 'How Rockstar Built the Most Detailed Map in Gaming History',
    targetDurationMinutes: 1,
    channel: { name: 'Test Channel', niche: 'documentary' }
  };
  const scriptText = 'A short test narration. '.repeat(30);
  const controller = new AbortController();
  // Abort after the first poll so the test ends without a completed master.
  const abortTimer = setTimeout(() => controller.abort(), 1500);
  const env = {
    ...process.env,
    MUSE_VIDNETS_COMMAND: `node ${fakeCli}`,
    MUSE_INFERENCE_COMMAND: 'true',
    MUSE_RESIDENT_INBOX: '1',
    MUSE_VIDNETS_POLL_MS: '500'
  };
  const previousEnv = { ...process.env };
  Object.assign(process.env, env);
  try {
    await assert.rejects(
      render(job, scriptText, dir, { attempt: 3, signal: controller.signal, onProgress: () => {} }),
      /cancelled by operator|was cancelled|aborted/i
    );
  } finally {
    clearTimeout(abortTimer);
    for (const key of Object.keys(env)) {
      if (previousEnv[key] === undefined) delete process.env[key];
      else process.env[key] = previousEnv[key];
    }
  }

  const calls = fs.readFileSync(logFile, 'utf8').trim().split('\n').filter(Boolean);
  assert.equal(calls.filter(line => line.startsWith('submit ')).length, 0,
    `no submit must happen for a live recorded job, got: ${calls.join(' | ')}`);
  assert.ok(calls.some(line => line === `job ${liveId}`), 'the live job must have been polled');
  // The receipt is untouched — same CMS job, same video job.
  const receipt = JSON.parse(fs.readFileSync(jobFile, 'utf8'));
  assert.equal(receipt.id, liveId);
  assert.equal(receipt.resubmittedFrom, undefined);
});
