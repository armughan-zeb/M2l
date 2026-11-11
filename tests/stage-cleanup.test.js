'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const {
  registerStageProcess, cleanupStage, cleanupAllStagesForJob,
  registeredStages, processStartMs, groupPids, isAlive,
} = require('../lib/stageCleanup');
const { runCommand } = require('../lib/command');

const JOB = 'test-stage-cleanup-job';

function spawnDetachedGroup(command, args = []) {
  const child = spawn(command, args, { detached: true, stdio: 'ignore' });
  child.unref();
  return child;
}

test('cleanupStage reaps a stage group and verifies it is gone', async () => {
  // A detached `bash -c 'sleep 300'` leaves bash + sleep in the new group.
  const child = spawnDetachedGroup('bash', ['-c', 'sleep 300']);
  const startedAtMs = Date.now();
  assert.equal(registerStageProcess(JOB, 'render', { pid: child.pid, pgid: child.pid, startedAtMs }), true);
  await new Promise(resolve => setTimeout(resolve, 300));
  const members = groupPids(child.pid);
  // bash may exec sleep (1 member) or fork it (2 members); either way
  // the group is non-empty and owned by the stage.
  assert.ok(members.length >= 1, `expected a live group ${child.pid}, got ${members}`);
  const report = await cleanupStage(JOB, 'render', { graceMs: 2000 });
  assert.equal(report.verifiedGone, true);
  assert.ok(report.terminated.length >= 1, 'SIGTERM should have exited the group');
  assert.deepEqual(report.survivors, []);
  for (const pid of members) assert.equal(isAlive(pid), false, `pid ${pid} should be gone`);
});

test('cleanupStage SIGKILLs a member that ignores SIGTERM', async () => {
  const child = spawnDetachedGroup('bash', ['-c', 'trap "" TERM; sleep 300']);
  const startedAtMs = Date.now();
  assert.equal(registerStageProcess(JOB, 'render', { pid: child.pid, pgid: child.pid, startedAtMs }), true);
  await new Promise(resolve => setTimeout(resolve, 300));
  const report = await cleanupStage(JOB, 'render', { graceMs: 500 });
  assert.equal(report.verifiedGone, true);
  assert.ok(report.killed.length >= 1, 'the TERM-ignoring process should be SIGKILLed');
  assert.equal(isAlive(child.pid), false);
});

test('cleanupStage reaps a non-detached grandchild left behind by an exited stage', async () => {
  // The real scenario: the stage command exits, but a child it spawned
  // (in its group, not detached) lingers. runCommand reports the spawn;
  // the stage registers it; cleanup reaps the leftover.
  let spawnInfo = null;
  const result = await runCommand(
    `node -e "const {spawn}=require('child_process');spawn('sleep',['300'],{stdio:'ignore'}).unref();console.log(JSON.stringify({ok:1}))"`,
    {
      label: 'stage with leftover grandchild',
      onSpawn: info => { spawnInfo = info; },
    },
  );
  assert.equal(JSON.parse(result.stdout).ok, 1);
  assert.ok(spawnInfo && spawnInfo.pgid > 1, 'onSpawn must report the stage group');
  assert.equal(result.pgid, spawnInfo.pgid);
  assert.equal(registerStageProcess(JOB, 'render', spawnInfo), true);
  const report = await cleanupStage(JOB, 'render', { graceMs: 2000 });
  assert.equal(report.verifiedGone, true);
  assert.ok(report.terminated.length >= 1, 'the leftover sleep grandchild should be reaped');
  assert.deepEqual(registeredStages(JOB), []);
});

test('registerStageProcess refuses the own group, PID 1, and the daemon PID', () => {
  const { execFileSync } = require('node:child_process');
  const ownPgid = Number(execFileSync('ps', ['-o', 'pgid=', '-p', String(process.pid)], { encoding: 'utf8' }).trim());
  assert.ok(Number.isFinite(ownPgid) && ownPgid > 1, 'own pgid must be readable');
  assert.equal(registerStageProcess(JOB, 'x', { pid: 1, pgid: 1, startedAtMs: Date.now() }), false);
  assert.equal(registerStageProcess(JOB, 'x', { pid: 12345, pgid: ownPgid, startedAtMs: Date.now() }), false);
  assert.equal(registerStageProcess(JOB, 'x', { pid: process.pid, pgid: process.pid, startedAtMs: Date.now() }), false);
  assert.deepEqual(registeredStages(JOB), []);
});

test('cleanupStage with no record is a verified no-op', async () => {
  const report = await cleanupStage(JOB, 'never-registered');
  assert.equal(report.skipped, 'no-record');
  assert.equal(report.verifiedGone, true);
});

test('cleanupStage spares a group member that predates the stage (PID reuse)', async () => {
  const child = spawnDetachedGroup('bash', ['-c', 'sleep 300']);
  await new Promise(resolve => setTimeout(resolve, 300));
  // Register with a start time 10 minutes in the FUTURE: every real
  // member predates it, so nothing may be signalled.
  assert.equal(registerStageProcess(JOB, 'render', {
    pid: child.pid, pgid: child.pid, startedAtMs: Date.now() + 10 * 60 * 1000,
  }), true);
  const report = await cleanupStage(JOB, 'render', { graceMs: 500 });
  assert.equal(report.verifiedGone, true);
  assert.deepEqual(report.terminated, []);
  assert.ok(report.spared.length >= 1, 'the pre-existing member must be spared');
  assert.equal(isAlive(child.pid), true, 'the spared process must still be alive');
  // Clean up the survivor directly: it was our test process.
  try { process.kill(-child.pid, 'SIGKILL'); } catch { /* already gone */ }
});

test('cleanupAllStagesForJob sweeps every registered stage', async () => {
  const a = spawnDetachedGroup('bash', ['-c', 'sleep 300']);
  const b = spawnDetachedGroup('bash', ['-c', 'sleep 300']);
  const now = Date.now();
  assert.equal(registerStageProcess(JOB, 'script', { pid: a.pid, pgid: a.pid, startedAtMs: now }), true);
  assert.equal(registerStageProcess(JOB, 'render', { pid: b.pid, pgid: b.pid, startedAtMs: now }), true);
  await new Promise(resolve => setTimeout(resolve, 300));
  const reports = await cleanupAllStagesForJob(JOB, { graceMs: 2000 });
  assert.equal(reports.length, 2);
  assert.ok(reports.every(report => report.verifiedGone));
  assert.equal(isAlive(a.pid), false);
  assert.equal(isAlive(b.pid), false);
});

test('processStartMs reads a live process start near now', () => {
  const started = processStartMs(process.pid);
  assert.ok(Number.isFinite(started), 'start time must be readable');
  assert.ok(Math.abs(Date.now() - started) < 24 * 60 * 60 * 1000, 'start time must be recent');
  assert.equal(processStartMs(999999), null, 'a missing PID has no start time');
});
