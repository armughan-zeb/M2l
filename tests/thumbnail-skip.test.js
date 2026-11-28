'use strict';

// Thumbnail skip (Nov 10, 2026) + upload-cap transcode helpers.
//
// This worker delivers script + video only: VIDZEO_SKIP_THUMBNAILS=1 is
// the package default, and `node muse-agent.js deliver --skip-thumbnail`
// forces the skip per call. Masters over ~95 MB are transcoded to fit the
// ~100 MB CMS/share-link cap (HTTP 413 on the 277 MB BNSF master, Nov 10)
// instead of failing delivery after a full render. Cloudflare window cap
// (Nov 28, 2026): masters that fit 30 MB at the bitrate floor are
// transcoded to 30 MB so the tunnel PUT finishes inside Cloudflare's
// 100s window (the GTA job's 49 MB master 524'd 3 times at ~127s each).

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

test('thumbnailsSkipped follows VIDZEO_SKIP_THUMBNAILS (defaults to true)', () => {
  delete process.env.VIDZEO_SKIP_THUMBNAILS;
  assert.equal(jobRunner.thumbnailsSkipped(), true);

  process.env.VIDZEO_SKIP_THUMBNAILS = '1';
  assert.equal(jobRunner.thumbnailsSkipped(), true);

  process.env.VIDZEO_SKIP_THUMBNAILS = ' 1 ';
  assert.equal(jobRunner.thumbnailsSkipped(), true);

  process.env.VIDZEO_SKIP_THUMBNAILS = '0';
  assert.equal(jobRunner.thumbnailsSkipped(), false);

  process.env.VIDZEO_SKIP_THUMBNAILS = 'false';
  assert.equal(jobRunner.thumbnailsSkipped(), false);
});

test('uploadBitrateForDuration fits the cap (74s -> 1080p 7 Mbps recipe)', () => {
  // 74s BNSF master: budget allows the full 7 Mbps 1080p recipe.
  assert.equal(ffmpeg.uploadBitrateForDuration(74), 7000);
  // 679s Cooper master: a lower bitrate is required to fit the cap.
  const cooper = ffmpeg.uploadBitrateForDuration(679);
  assert.ok(cooper >= 700 && cooper < 4000, `Cooper bitrate ${cooper} kbps`);
  // 900s (15 min): even the 700 kbps floor cannot fit -> 0 (fail closed).
  assert.equal(ffmpeg.uploadBitrateForDuration(900), 0);
  assert.equal(ffmpeg.uploadBitrateForDuration(0), 0);
  assert.equal(ffmpeg.uploadBitrateForDuration(-5), 0);
  // Cloudflare window cap (Nov 28, 2026): a 64s video fits 30 MB at
  // ~3.3 Mbps, so its upload stays inside Cloudflare's 100s window even
  // on the slow tunnel (~0.39 MB/s measured on the GTA job).
  const window64 = ffmpeg.uploadBitrateForDuration(64, ffmpeg.UPLOAD_WINDOW_LIMIT_BYTES);
  assert.ok(window64 >= 3000 && window64 < 4000, `64s window bitrate ${window64} kbps`);
  // A 679s video cannot fit 30 MB at the 700 kbps floor -> 0, so long
  // videos keep the 95 MB cap and rely on the transient retry instead.
  assert.equal(ffmpeg.uploadBitrateForDuration(679, ffmpeg.UPLOAD_WINDOW_LIMIT_BYTES), 0);
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

test('upload compression replaces corrupt cache and keeps 1080p below 4 Mbps', async t => {
  const ffmpegPath = await ffmpeg.resolveBinary('ffmpeg');
  if (!ffmpegPath || !await ffmpeg.resolveBinary('ffprobe')) {
    t.skip('FFmpeg and FFprobe are required');
    return;
  }
  const { execFileSync } = require('node:child_process');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'upload-cap-'));
  tmpDirs.push(dir);
  const big = path.join(dir, 'video.mp4');
  const reencoded = path.join(dir, 'video-upload.mp4');
  execFileSync(ffmpegPath, [
    '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=1920x1080:rate=1:duration=181',
    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=181', '-c:v', 'libx264',
    '-preset', 'ultrafast', '-c:a', 'aac', '-movflags', '+faststart', '-y', big
  ], { timeout: 120000, windowsHide: true });
  fs.truncateSync(big, ffmpeg.UPLOAD_SIZE_LIMIT_BYTES + 1024);
  fs.writeFileSync(reencoded, Buffer.alloc(1024));
  const future = new Date(fs.statSync(big).mtimeMs + 1000);
  fs.utimesSync(reencoded, future, future);

  const result = await ffmpeg.ensureUploadableMaster(big);
  assert.equal(result.path, reencoded);
  assert.equal(result.transcoded, true);
  assert.ok(result.bytes < ffmpeg.UPLOAD_SIZE_LIMIT_BYTES);
  // Nov 28, 2026: the 181s video fits the 30 MB Cloudflare window cap,
  // so the upload copy must be at or under it — not just under 95 MB.
  assert.ok(result.bytes <= ffmpeg.UPLOAD_WINDOW_LIMIT_BYTES, `upload copy ${result.bytes} bytes must fit the 30 MB window cap`);
  const output = await ffmpeg.probeVideo(reencoded);
  assert.deepEqual([output.width, output.height, output.hasAudio], [1920, 1080, true]);
  assert.ok(ffmpeg.uploadBitrateForDuration(output.duration) < 4000);
  assert.ok(Math.abs(output.duration - 181) < 0.5);
  assert.deepEqual(fs.readdirSync(dir).sort(), ['video-upload.mp4', 'video.mp4']);
  const savedMtime = fs.statSync(reencoded).mtimeMs;
  assert.deepEqual(await ffmpeg.ensureUploadableMaster(big), result);
  assert.equal(fs.statSync(reencoded).mtimeMs, savedMtime);
});
