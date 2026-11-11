'use strict';

// Thumbnail skip (Nov 10, 2026) + upload-cap transcode helpers.
//
// This worker delivers script + video only: VIDZEO_SKIP_THUMBNAILS=1 is
// the package default, and `node muse-agent.js deliver --skip-thumbnail`
// forces the skip per call. Masters over ~95 MB are transcoded to fit the
// ~100 MB CMS/share-link cap (HTTP 413 on the 277 MB BNSF master, Nov 10)
// instead of failing delivery after a full render.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test, afterEach } = require('node:test');

const ffmpeg = require('../lib/ffmpeg');
const jobRunner = require('../lib/jobRunner');

const tmpDirs = [];
afterEach(() => {
  delete process.env.VIDZEO_SKIP_THUMBNAILS;
  for (const dir of tmpDirs.splice(0)) fs.rmSync(dir, { recursive: true, force: true });
});

test('thumbnailsSkipped follows VIDZEO_SKIP_THUMBNAILS', () => {
  delete process.env.VIDZEO_SKIP_THUMBNAILS;
  assert.equal(jobRunner.thumbnailsSkipped(), false);

  process.env.VIDZEO_SKIP_THUMBNAILS = '1';
  assert.equal(jobRunner.thumbnailsSkipped(), true);

  process.env.VIDZEO_SKIP_THUMBNAILS = ' 1 ';
  assert.equal(jobRunner.thumbnailsSkipped(), true);

  process.env.VIDZEO_SKIP_THUMBNAILS = '0';
  assert.equal(jobRunner.thumbnailsSkipped(), false);

  process.env.VIDZEO_SKIP_THUMBNAILS = '';
  assert.equal(jobRunner.thumbnailsSkipped(), false);
});

test('uploadBitrateForDuration fits the cap (74s -> 1080p 7 Mbps recipe)', () => {
  // 74s BNSF master: budget allows the full 7 Mbps 1080p recipe.
  assert.equal(ffmpeg.uploadBitrateForDuration(74), 7000);
  // 679s Cooper master: ~901 kbps, so 720p, still fits.
  const cooper = ffmpeg.uploadBitrateForDuration(679);
  assert.ok(cooper >= 700 && cooper < 4000, `Cooper bitrate ${cooper} kbps`);
  // 900s (15 min): even the 700 kbps floor cannot fit -> 0 (fail closed).
  assert.equal(ffmpeg.uploadBitrateForDuration(900), 0);
  assert.equal(ffmpeg.uploadBitrateForDuration(0), 0);
  assert.equal(ffmpeg.uploadBitrateForDuration(-5), 0);
});

test('ensureUploadableMaster passes a small master through untouched', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'upload-cap-'));
  tmpDirs.push(dir);
  const small = path.join(dir, 'video.mp4');
  fs.writeFileSync(small, Buffer.alloc(2048));
  const result = await ffmpeg.ensureUploadableMaster(small);
  assert.equal(result.path, small);
  assert.equal(result.transcoded, false);
  assert.equal(result.bytes, 2048);
  assert.equal(fs.existsSync(path.join(dir, 'video-upload.mp4')), false);
});

test('ensureUploadableMaster reuses a fresh fitting re-encode', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'upload-cap-'));
  tmpDirs.push(dir);
  const big = path.join(dir, 'video.mp4');
  const reencoded = path.join(dir, 'video-upload.mp4');
  // A >95 MB master is faked with a sparse file so no real transcode runs:
  // the existing video-upload.mp4 is newer and fits, so it is reused.
  const fd = fs.openSync(big, 'w');
  fs.ftruncateSync(fd, ffmpeg.UPLOAD_SIZE_LIMIT_BYTES + 1024);
  fs.closeSync(fd);
  fs.writeFileSync(reencoded, Buffer.alloc(1024));
  const bigTime = fs.statSync(big).mtimeMs;
  fs.utimesSync(reencoded, new Date(bigTime + 1000), new Date(bigTime + 1000));
  const result = await ffmpeg.ensureUploadableMaster(big);
  assert.equal(result.path, reencoded);
  assert.equal(result.transcoded, true);
  assert.equal(result.originalBytes, ffmpeg.UPLOAD_SIZE_LIMIT_BYTES + 1024);
});
