'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {
  classifyProcess, extractJobTokens, isCandidate, discoverLiveJobIds,
} = require('../scripts/cleanup-orphans');

const LIVE = new Set(['f04c6b9f', 'r_mw85awoskks8lh']);
const ALL_PIDS = new Set([1, 100, 101, 200, 201, 300]);

function row(overrides) {
  return {
    pid: 100, ppid: 1, pgid: 100, etimes: 3600, rssKb: 15000,
    comm: 'ffmpeg', args: 'ffmpeg -i /tmp/jobs/deadbeef1234/clip.mp4', ...overrides,
  };
}

test('a live-job ffmpeg (reparented, PPID 1) is spared by its job id', () => {
  const result = classifyProcess(row({ args: 'ffmpeg -i /tmp/jobs/f04c6b9f/clip.mp4' }), { liveJobIds: LIVE, pids: ALL_PIDS });
  assert.equal(result.verdict, 'live');
  assert.match(result.reason, /f04c6b9f/);
});

test('a stale-job ffmpeg with a dead parent is an orphan', () => {
  const result = classifyProcess(row({}), { liveJobIds: LIVE, pids: ALL_PIDS });
  assert.equal(result.verdict, 'orphan');
  assert.match(result.reason, /stale-job:deadbeef1234/);
});

test('a stale-job ffmpeg with a live parent is left alone', () => {
  const result = classifyProcess(row({ ppid: 200 }), { liveJobIds: LIVE, pids: ALL_PIDS });
  assert.equal(result.verdict, 'leave');
  assert.equal(result.reason, 'parent-alive');
});

test('an old unattributed ffmpeg with a dead parent is an orphan; a young one is left alone', () => {
  const old = classifyProcess(row({ args: 'ffmpeg -f lavfi -i nullsrc -f null -', etimes: 3600 }), { liveJobIds: LIVE, pids: ALL_PIDS });
  assert.equal(old.verdict, 'orphan');
  assert.match(old.reason, /unattributed-age/);
  const young = classifyProcess(row({ args: 'ffmpeg -f lavfi -i nullsrc -f null -', etimes: 60 }), { liveJobIds: LIVE, pids: ALL_PIDS });
  assert.equal(young.verdict, 'leave');
  assert.match(young.reason, /young-unattributed/);
});

test('only job-worker kinds are candidates', () => {
  assert.equal(isCandidate(row({})), true);
  assert.equal(isCandidate(row({ comm: 'chrome', args: 'chrome --headless --disable-gpu' })), true);
  assert.equal(isCandidate(row({ comm: 'node', args: 'node /home/hatch/workspace/M2l/render-vidnets-linux.js' })), true);
  assert.equal(isCandidate(row({ comm: 'node', args: 'node /home/hatch/workspace/M2l/vidnets/server/headless_agent.py' })), true);
  assert.equal(isCandidate(row({ comm: 'node', args: 'node server.js' })), false);
  assert.equal(isCandidate(row({ comm: 'sleep', args: 'sleep 600' })), false);
  // Infrastructure is never a candidate, even though node/python match.
  assert.equal(isCandidate(row({ comm: 'node', args: 'node muse-agent.js start' })), false);
  assert.equal(isCandidate(row({ comm: 'python3', args: '/home/hatch/workspace/M2l/vidnets/.venv/bin/python3 -u /home/hatch/workspace/M2l/vidnets/main.py' })), false);
});

test('extractJobTokens finds job ids in temp paths and jobId= forms', () => {
  assert.deepEqual([...extractJobTokens('ffmpeg -i /tmp/jobs/f04c6b9f/clip.mp4')], ['f04c6b9f']);
  assert.deepEqual([...extractJobTokens('node render-vidnets-linux.js --job-id=r_mw85awoskks8lh')], ['r_mw85awoskks8lh']);
  assert.deepEqual([...extractJobTokens('ffmpeg -i /tmp/other/file.mp4')], []);
});

test('discoverLiveJobIds reads the daemon active-job.json', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'orphan-work-'));
  try {
    fs.writeFileSync(path.join(dir, 'active-job.json'), JSON.stringify({ jobId: 'r_mw85awoskks8lh', payload: {} }));
    const ids = discoverLiveJobIds({ extraIds: ['f04c6b9f'], workRoot: dir, processes: [] });
    assert.ok(ids.has('r_mw85awoskks8lh'));
    assert.ok(ids.has('f04c6b9f'));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
