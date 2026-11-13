'use strict';

// Master-manifest delivery guard (Nov 10, 2026 — BNSF incident).
//
// The CMS job r_mw85awoskks8lh was delivered the WRONG video: a manual
// deliver picked a stale output-package file by TITLE (Vidnets job
// 876f0e84, Nov 9, 10/10 graphic fallback, 18 MB) instead of the claiming
// job's own master (Vidnets job f04c6b9f, Nov 10, 15/15 real media,
// 277 MB). The delivered manifest even said so — its 10 media_paths all
// point at temp/jobs/876f0e84/. These tests pin the guard: deliverSaved
// and pickValidatedMaster must refuse a master whose manifest belongs to
// another job, or that is 100% graphic fallback, and must deliver the
// good master.

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

// The delivered (wrong) BNSF manifest: 10 assets, all graphic fallback,
// all paths under the OTHER job's temp dir.
function wrongManifest() {
  return {
    schema_version: 1,
    asset_count: 10,
    media_paths: Array.from({ length: 10 }, (_, i) =>
      `/home/hatch/workspace/vidnets-runtime/temp/jobs/876f0e84/876f0e84-run-0001-415318d21b33/render_media/graphics/graphic_fallback_1794197179528_${i + 1}_xxxx.png`),
    source_labels: Array.from({ length: 10 }, () => 'Grounded Graphic Fallback'),
    metadata: {
      media_resolution_v2: Array.from({ length: 10 }, (_, i) => ({
        scene_index: i,
        provider: 'graphic',
        status: 'degraded',
        verification_status: 'grounded_graphic_fallback',
        resolution_kind: 'grounded_graphic_fallback'
      }))
    }
  };
}

// The good f04c6b9f manifest: 15 assets, 14 web + 1 YouTube clip, all
// paths under the claiming job's own temp dir.
function goodManifest() {
  const paths = Array.from({ length: 15 }, (_, i) =>
    `/home/hatch/workspace/vidnets-runtime/temp/jobs/f04c6b9f/f04c6b9f-run-0002-c834c4759400/render_media/web_v2_1794280371551_8e6732/scene_${String(i + 1).padStart(4, '0')}.jpg`);
  paths[4] = '/home/hatch/workspace/vidnets-runtime/temp/jobs/f04c6b9f/f04c6b9f-run-0002-c834c4759400/render_media/youtube_v2_1794281035123_ab12cd/scene_0005.mp4';
  const labels = Array.from({ length: 15 }, () => 'Web Images');
  labels[4] = 'YouTube Clips';
  return {
    schema_version: 1,
    job_id: 'f04c6b9f',
    run_id: 'f04c6b9f-run-0002-c834c4759400',
    asset_count: 15,
    media_paths: paths,
    source_labels: labels,
    metadata: {
      media_resolution_v2: Array.from({ length: 15 }, (_, i) => ({
        scene_index: i,
        provider: i === 4 ? 'youtube' : 'web',
        status: 'accepted',
        verification_status: 'verified',
        resolution_kind: 'late_real_media_fallback'
      }))
    }
  };
}

function makeJob(overrides = {}) {
  return {
    jobId: 'r_mw85awoskks8lh',
    title: 'A BNSF Whiteout Crossing, Seen From the Driver\u2019s Cab',
    channel: { name: 'Train Crew', id: 'c_test' },
    targetDurationMinutes: 1,
    targetWordCount: 150,
    voice: 'en-US-EricNeural',
    requirements: { minimumWords: 135 },
    createdAt: '2026-11-10T02:18:07.844Z',
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
    master: { duration: 74, width: 1920, height: 1080, hasVideo: true, hasAudio: true }
  });
  ffmpeg.validateThumbnail = async () => ({ ok: true, width: 1920, height: 1080, size: 2048 });
  return () => {
    ffmpeg.validateVideoMaster = originals.validateVideoMaster;
    ffmpeg.validateThumbnail = originals.validateThumbnail;
  };
}

// A fake output package dir holding a video + its sibling manifest, as
// the server writes it: <pkg>/<title>.mp4 + <pkg>/media/manifest.json.
function writePackage(pkgDir, manifest) {
  const video = path.join(pkgDir, 'A BNSF Whiteout Crossing.mp4');
  writeFile(video, Buffer.alloc(1024 * 1024 + 512));
  writeFile(path.join(pkgDir, 'media', 'manifest.json'), JSON.stringify(manifest, null, 2));
  return video;
}

test('guard refuses the BNSF wrong manifest for the claiming job f04c6b9f', () => {
  const pkg = fs.mkdtempSync(path.join(os.tmpdir(), 'guard-wrong-'));
  const video = writePackage(pkg, wrongManifest());
  const check = guard.validateMasterManifest(video, { vidnetsJobId: 'f04c6b9f' });
  assert.equal(check.ok, false);
  assert.match(check.reason, /876f0e84/);
  assert.equal(check.realCount, 0);
  assert.equal(check.graphicCount, 10);
});

test('guard refuses the same manifest on all-graphic grounds even for its own job', () => {
  const pkg = fs.mkdtempSync(path.join(os.tmpdir(), 'guard-own-'));
  const video = writePackage(pkg, wrongManifest());
  const check = guard.validateMasterManifest(video, { vidnetsJobId: '876f0e84' });
  assert.equal(check.ok, false);
  assert.match(check.reason, /100% graphic|zero real media/);
});

test('guard passes the wrong manifest only with an explicit all-graphic override for its own job', () => {
  const pkg = fs.mkdtempSync(path.join(os.tmpdir(), 'guard-override-'));
  const video = writePackage(pkg, wrongManifest());
  const check = guard.validateMasterManifest(video, { vidnetsJobId: '876f0e84', allowAllGraphic: true });
  assert.equal(check.ok, true);
});

test('guard delivers the good f04c6b9f manifest: 15/15 real media, own temp dir', () => {
  const pkg = fs.mkdtempSync(path.join(os.tmpdir(), 'guard-good-'));
  const video = writePackage(pkg, goodManifest());
  const check = guard.validateMasterManifest(video, { vidnetsJobId: 'f04c6b9f' });
  assert.equal(check.ok, true);
  assert.equal(check.assetCount, 15);
  assert.equal(check.realCount, 15);
  assert.equal(check.graphicCount, 0);
  assert.deepEqual(check.foreignJobs, []);
});

test('saved master delivery rejects a missing scene even when other scenes have real media', () => {
  const manifest = goodManifest();
  manifest.media_paths[4] = '';
  const check = guard.decideManifestDelivery(manifest, { vidnetsJobId: 'f04c6b9f' });
  assert.equal(check.ok, false);
  assert.equal(check.realCount, 14);
  assert.match(check.reason, /14\/15 scenes/);
});

test('guard refuses a manifest whose stamped job id disagrees with the claiming job', () => {
  const manifest = goodManifest();
  manifest.job_id = '876f0e84';
  const pkg = fs.mkdtempSync(path.join(os.tmpdir(), 'guard-stamp-'));
  const video = writePackage(pkg, manifest);
  const check = guard.validateMasterManifest(video, { vidnetsJobId: 'f04c6b9f' });
  assert.equal(check.ok, false);
  assert.match(check.reason, /belongs to Vidnets job 876f0e84/);
});

test('guard passes when no manifest sits beside the master (provenance unchecked)', () => {
  const pkg = fs.mkdtempSync(path.join(os.tmpdir(), 'guard-nomanifest-'));
  const video = path.join(pkg, 'video.mp4');
  writeFile(video, Buffer.alloc(1024 * 1024 + 512));
  const check = guard.validateMasterManifest(video, { vidnetsJobId: 'f04c6b9f' });
  assert.equal(check.ok, true);
  assert.match(check.reason, /no manifest found/);
});

test('deliverSaved refuses the BNSF wrong master and never uploads it', async () => {
  const restore = patchFfmpeg();
  try {
    const job = makeJob();
    const pkg = fs.mkdtempSync(path.join(os.tmpdir(), 'guard-deliver-wrong-'));
    const video = writePackage(pkg, wrongManifest());
    const scriptFile = path.join(pkg, 'script.txt');
    writeFile(scriptFile, words(240) + '\n');
    const thumbnailFile = path.join(pkg, 'thumb.jpg');
    writeFile(thumbnailFile, Buffer.alloc(4096));
    const client = stubClient();
    await assert.rejects(
      () => jobRunner.deliverSaved({
        client, job, attempt: 1, scriptFile, videoFile: video, thumbnailFile
      }),
      /provenance/
    );
    assert.equal(client.calls.upload.length, 0);
    assert.equal(client.calls.complete.length, 0);
  } finally {
    restore();
  }
});

test('deliverSaved delivers the good master and uploads it', async () => {
  const restore = patchFfmpeg();
  try {
    const job = makeJob();
    const pkg = fs.mkdtempSync(path.join(os.tmpdir(), 'guard-deliver-good-'));
    const video = writePackage(pkg, goodManifest());
    const scriptFile = path.join(pkg, 'script.txt');
    writeFile(scriptFile, words(240) + '\n');
    const thumbnailFile = path.join(pkg, 'thumb.jpg');
    writeFile(thumbnailFile, Buffer.alloc(4096));
    const client = stubClient();
    const result = await jobRunner.deliverSaved({
      client, job, attempt: 1, scriptFile, videoFile: video, thumbnailFile
    });
    assert.equal(result.ok, true);
    assert.deepEqual(client.calls.upload.map(call => call.kind), ['video']);
    assert.equal(client.calls.complete.length, 1);
    assert.equal(client.calls.complete[0].body.scriptText.trim(), words(240));
  } finally {
    restore();
  }
});

test('pickValidatedMaster skips an all-graphic candidate and takes a real-media one', async () => {
  const restore = patchFfmpeg();
  try {
    const job = makeJob();
    const badPkg = fs.mkdtempSync(path.join(os.tmpdir(), 'guard-pick-bad-'));
    const badVideo = writePackage(badPkg, wrongManifest());
    const goodPkg = fs.mkdtempSync(path.join(os.tmpdir(), 'guard-pick-good-'));
    const goodVideo = writePackage(goodPkg, goodManifest());
    const picked = await jobRunner.pickValidatedMaster(
      { masterPath: '', vidnetsMasterPath: '', vidnetsMp4s: [badVideo, goodVideo], vidnetsJobId: 'f04c6b9f' },
      job
    );
    assert.equal(picked, goodVideo);
    const nonePicked = await jobRunner.pickValidatedMaster(
      { masterPath: '', vidnetsMasterPath: '', vidnetsMp4s: [badVideo], vidnetsJobId: 'f04c6b9f' },
      job
    );
    assert.equal(nonePicked, '');
  } finally {
    restore();
  }
});
