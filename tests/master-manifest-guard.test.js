'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test, afterEach } = require('node:test');

const workRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'master-guard-work-'));
process.env.MUSE_WORK_DIR = workRoot;
process.env.MUSE_CMS_RETRY_BASE_MS = '10';
process.env.MUSE_CMS_RETRY_MAX_MS = '50';

const config = require('../lib/config');
const jobRunner = require('../lib/jobRunner');
const ffmpeg = require('../lib/ffmpeg');
const guard = require('../lib/masterManifestGuard');

afterEach(() => {
  try { config.clearActiveJob(); } catch { /* already clear */ }
});

function writeFile(file, content) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, content);
}

function words(n) {
  return Array.from({ length: n }, (_, i) => `word${i}`).join(' ');
}

function makeJob(overrides = {}) {
  return {
    jobId: 'r_doc_guard_1',
    title: 'One-Minute Documentary Guard Test',
    productionEngine: 'documentary',
    channel: { name: 'Documentary Channel', id: 'c_test' },
    targetDurationMinutes: 1,
    targetWordCount: 150,
    voice: 'en-US-ChristopherNeural',
    requirements: { minimumWords: 135 },
    createdAt: '2026-11-27T02:18:07.844Z',
    ...overrides
  };
}

function stubClient() {
  const calls = { progress: [], upload: [], complete: [], fail: [] };
  return {
    calls,
    async progress(jobId, stage, progress, detail) {
      calls.progress.push({ jobId, stage, progress, detail });
    },
    async upload(jobId, kind, filePath) {
      calls.upload.push({ jobId, kind, filePath });
      return { artifact: { bytes: fs.existsSync(filePath) ? fs.statSync(filePath).size : 0, sha256: 'stub' } };
    },
    async complete(jobId, body) {
      calls.complete.push({ jobId, body });
      return { ok: true };
    },
    async fail(jobId, error) {
      calls.fail.push({ jobId, error });
    }
  };
}

function patchFfmpeg() {
  const originals = {
    validateVideoMaster: ffmpeg.validateVideoMaster,
    validateThumbnail: ffmpeg.validateThumbnail
  };
  ffmpeg.validateVideoMaster = async () => ({
    ok: true, failures: [],
    master: { duration: 60, width: 1920, height: 1080, hasVideo: true, hasAudio: true }
  });
  ffmpeg.validateThumbnail = async () => ({ ok: true, width: 1920, height: 1080, size: 2048 });
  return () => {
    ffmpeg.validateVideoMaster = originals.validateVideoMaster;
    ffmpeg.validateThumbnail = originals.validateThumbnail;
  };
}

test('validateMasterManifest isolates Documentary vs 2D Stickman and rejects wrong job IDs', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'guard-doc-'));
  try {
    const video = path.join(dir, 'video.mp4');
    writeFile(video, Buffer.alloc(1024 * 1024 + 512));

    // No sidecar on manual documentary deliver passes
    const noSidecar = guard.validateMasterManifest(video, { jobId: 'r_doc_1', engine: 'documentary' });
    assert.equal(noSidecar.ok, true);

    guard.stampDocumentaryManifest(video, {
      jobId: 'r_doc_1',
      topicTag: 'BASKETBALL',
      headerText: 'MORE THAN A GAME',
      voice: 'en-US-ChristopherNeural',
      durationSeconds: 60,
      shotCount: 10,
      videoShotCount: 8,
      imageShotCount: 2,
    });

    assert.equal(guard.validateMasterManifest(video, { jobId: 'r_doc_1', engine: 'documentary' }).ok, true);
    assert.equal(guard.validateMasterManifest(video, { jobId: 'r_doc_other', engine: 'documentary' }).ok, false);
    assert.equal(guard.validateMasterManifest(video, { jobId: 'r_doc_1', engine: 'stickman' }).ok, false);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('deliverSaved refuses a documentary master stamped for a different job and delivers its own master', async () => {
  const restore = patchFfmpeg();
  try {
    const job = makeJob();
    const pkg = fs.mkdtempSync(path.join(os.tmpdir(), 'guard-deliver-'));
    const video = path.join(pkg, 'video.mp4');
    writeFile(video, Buffer.alloc(1024 * 1024 + 512));
    guard.stampDocumentaryManifest(video, {
      jobId: 'r_other_job',
      topicTag: 'TEST',
      durationSeconds: 60,
      shotCount: 8,
    });
    const scriptFile = path.join(pkg, 'script.txt');
    writeFile(scriptFile, words(150) + '\n');
    const client = stubClient();
    await assert.rejects(
      () => jobRunner.deliverSaved({ client, job, attempt: 1, scriptFile, videoFile: video, skipThumbnail: true }),
      /provenance/
    );
    assert.equal(client.calls.upload.length, 0);

    guard.stampDocumentaryManifest(video, {
      jobId: job.jobId,
      topicTag: 'TEST',
      durationSeconds: 60,
      shotCount: 8,
    });
    const res = await jobRunner.deliverSaved({ client, job, attempt: 1, scriptFile, videoFile: video, skipThumbnail: true });
    assert.equal(res.ok, true);
    assert.equal(client.calls.complete.length, 1);
    assert.equal(client.calls.complete[0].body.meta.productionEngine, 'documentary');
  } finally {
    restore();
  }
});
