'use strict';

/**
 * FFmpeg/FFprobe discovery and objective local media checks.
 *
 * Muse artifacts are validated by the CMS again before publication. These
 * local checks only avoid uploading an obviously invalid master or thumbnail.
 */

const fs = require('fs');
const { randomUUID } = require('node:crypto');
const path = require('path');
const { spawn } = require('child_process');

const candidates = [];

function addCandidate(binary, values) {
  for (const value of values) {
    if (value) candidates.push({ binary, path: String(value).trim() });
  }
}

addCandidate('ffprobe', [
  process.env.FFPROBE_PATH,
  process.env.MUSE_FFMPEG_DIR && path.join(process.env.MUSE_FFMPEG_DIR, process.platform === 'win32' ? 'ffprobe.exe' : 'ffprobe'),
  process.env.MUSE_FFMPEG_DIR && path.join(process.env.MUSE_FFMPEG_DIR, 'bin', process.platform === 'win32' ? 'ffprobe.exe' : 'ffprobe'),
  process.env.MUSE_VIDNETS_ROOT && path.join(process.env.MUSE_VIDNETS_ROOT, 'resources', 'tools', 'ffmpeg', process.platform === 'win32' ? 'ffprobe.exe' : 'ffprobe'),
  path.join(__dirname, '..', 'vidnets', 'resources', 'tools', 'ffmpeg', process.platform === 'win32' ? 'ffprobe.exe' : 'ffprobe'),
  path.join(__dirname, '..', 'vidnets', 'tools', 'ffmpeg', process.platform === 'win32' ? 'ffprobe.exe' : 'ffprobe'),
  '/usr/bin/ffprobe',
  '/usr/local/bin/ffprobe',
  '/snap/bin/ffprobe',
  'ffprobe'
]);
addCandidate('ffmpeg', [
  process.env.FFMPEG_PATH,
  process.env.MUSE_FFMPEG_DIR && path.join(process.env.MUSE_FFMPEG_DIR, process.platform === 'win32' ? 'ffmpeg.exe' : 'ffmpeg'),
  process.env.MUSE_FFMPEG_DIR && path.join(process.env.MUSE_FFMPEG_DIR, 'bin', process.platform === 'win32' ? 'ffmpeg.exe' : 'ffmpeg'),
  process.env.MUSE_VIDNETS_ROOT && path.join(process.env.MUSE_VIDNETS_ROOT, 'resources', 'tools', 'ffmpeg', process.platform === 'win32' ? 'ffmpeg.exe' : 'ffmpeg'),
  path.join(__dirname, '..', 'vidnets', 'resources', 'tools', 'ffmpeg', process.platform === 'win32' ? 'ffmpeg.exe' : 'ffmpeg'),
  path.join(__dirname, '..', 'vidnets', 'tools', 'ffmpeg', process.platform === 'win32' ? 'ffmpeg.exe' : 'ffmpeg'),
  '/usr/bin/ffmpeg',
  '/usr/local/bin/ffmpeg',
  '/snap/bin/ffmpeg',
  'ffmpeg'
]);

const resolved = new Map();

function executableExists(binary) {
  if (path.isAbsolute(binary)) {
    if (!fs.existsSync(binary)) return false;
    if (process.platform !== 'win32') {
      try {
        fs.accessSync(binary, fs.constants.X_OK);
      } catch {
        try {
          fs.chmodSync(binary, 0o755);
          return true;
        } catch {
          return false;
        }
      }
    }
    return true;
  }
  return true;
}

function probeBinary(binary) {
  return new Promise(resolve => {
    let child;
    try {
      child = spawn(binary, ['-version'], { windowsHide: true, stdio: 'ignore' });
    } catch {
      resolve(false);
      return;
    }
    const timer = setTimeout(() => {
      try { child.kill('SIGKILL'); } catch { /* already gone */ }
      resolve(false);
    }, 5000);
    child.on('error', () => { clearTimeout(timer); resolve(false); });
    child.on('close', code => { clearTimeout(timer); resolve(code === 0); });
  });
}

async function resolveBinary(name) {
  if (resolved.has(name)) return resolved.get(name);
  let found = '';
  for (const candidate of candidates) {
    if (candidate.binary !== name) continue;
    if (!executableExists(candidate.path)) continue;
    if (await probeBinary(candidate.path)) { found = candidate.path; break; }
  }
  resolved.set(name, found);
  return found;
}

async function availability() {
  const [ffprobe, ffmpeg] = await Promise.all([resolveBinary('ffprobe'), resolveBinary('ffmpeg')]);
  return { ffprobe, ffmpeg, ok: Boolean(ffprobe && ffmpeg) };
}

function run(binary, args, timeoutMs) {
  return new Promise(resolve => {
    let child;
    let stdout = '';
    let stderr = '';
    let settled = false;
    const finish = (status, error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ status, stdout, stderr: stderr || error || '' });
    };
    try {
      child = spawn(binary, args, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    } catch (error) {
      resolve({ status: -1, stdout: '', stderr: error.message });
      return;
    }
    const timer = setTimeout(() => {
      try { child.kill('SIGKILL'); } catch { /* already gone */ }
      finish(-1, 'Media command timed out');
    }, Math.max(1000, Number(timeoutMs) || 60000));
    child.stdout.on('data', chunk => { stdout = (stdout + chunk.toString('utf8')).slice(-4 * 1024 * 1024); });
    child.stderr.on('data', chunk => { stderr = (stderr + chunk.toString('utf8')).slice(-2 * 1024 * 1024); });
    child.on('error', error => finish(-1, error.message));
    child.on('close', code => finish(code));
  });
}

async function probeVideo(filePath) {
  const ffprobe = await resolveBinary('ffprobe');
  if (!ffprobe) throw new Error('FFprobe was not found. Set FFPROBE_PATH or MUSE_FFMPEG_DIR.');
  const result = await run(ffprobe, [
    '-v', 'error', '-show_entries',
    'format=duration,size:stream=index,codec_type,codec_name,duration,channels,sample_rate,width,height,avg_frame_rate',
    '-of', 'json', filePath
  ], 60000);
  if (result.status !== 0) throw new Error('FFprobe could not read the rendered master: ' + String(result.stderr || '').slice(-600));
  let data = {};
  try { data = JSON.parse(result.stdout || '{}'); } catch { throw new Error('FFprobe returned invalid metadata'); }
  const streams = Array.isArray(data.streams) ? data.streams : [];
  const video = streams.find(stream => stream.codec_type === 'video');
  const audio = streams.find(stream => stream.codec_type === 'audio');
  return {
    duration: Number(data.format && data.format.duration) || Number(video && video.duration) || 0,
    size: Number(data.format && data.format.size) || fs.statSync(filePath).size,
    width: Number(video && video.width) || 0,
    height: Number(video && video.height) || 0,
    hasVideo: Boolean(video),
    hasAudio: Boolean(audio)
  };
}

async function validateVideoMaster(filePath, expectedMinutes) {
  if (!filePath || !fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    return { ok: false, failures: ['Rendered master is missing'] };
  }
  if (fs.statSync(filePath).size < 1024 * 1024) {
    return { ok: false, failures: ['Rendered master is smaller than 1 MB'] };
  }
  const master = await probeVideo(filePath);
  const failures = [];
  const minimumDuration = Math.max(30, (Number(expectedMinutes) || 8) * 60);
  if (!master.hasVideo) failures.push('Rendered master has no video stream');
  if (master.duration + 0.5 < minimumDuration) {
    failures.push(`Rendered master is ${Math.round(master.duration)}s; at least ${minimumDuration}s is required`);
  }
  if (master.width < 1920 || master.height < 1080) {
    failures.push(`Rendered master is ${master.width}x${master.height}; 1080p minimum is required`);
  }
  if (!master.hasAudio) failures.push('Rendered master has no audio stream');

  const ffmpeg = await resolveBinary('ffmpeg');
  if (!ffmpeg) {
    failures.push('FFmpeg was not found for the full-decode check (set FFMPEG_PATH or MUSE_FFMPEG_DIR)');
  } else if (!failures.length) {
    const decode = await run(ffmpeg, ['-v', 'error', '-xerror', '-i', filePath, '-f', 'null', process.platform === 'win32' ? 'NUL' : '/dev/null'], 30 * 60 * 1000);
    if (decode.status !== 0) failures.push('Rendered master could not be fully decoded: ' + String(decode.stderr || '').slice(-500));
  }
  return { ok: failures.length === 0, failures, master };
}

async function validateThumbnail(filePath, aspect = '16:9') {
  if (!filePath || !fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    return { ok: false, error: 'Thumbnail file is missing' };
  }
  if (fs.statSync(filePath).size < 1024) return { ok: false, error: 'Thumbnail is smaller than 1 KB' };
  const ffprobe = await resolveBinary('ffprobe');
  if (!ffprobe) return { ok: false, error: 'FFprobe was not found to validate the thumbnail' };
  const result = await run(ffprobe, ['-v', 'error', '-show_entries', 'stream=width,height', '-of', 'json', filePath], 20000);
  let stream;
  try { stream = JSON.parse(result.stdout).streams[0]; } catch { /* handled below */ }
  if (result.status !== 0 || !stream) return { ok: false, error: 'Thumbnail cannot be decoded as an image' };
  const width = Number(stream.width);
  const height = Number(stream.height);
  if (Math.min(width, height) < 360 || Math.max(width, height) < 640) {
    return { ok: false, error: `Thumbnail resolution is too small (${width}x${height})` };
  }
  const [a, b] = String(aspect || '16:9').split(':').map(Number);
  if (a && b && Math.abs(width / height - a / b) > 0.1) {
    return { ok: false, error: `Thumbnail aspect ${width}x${height} does not match ${aspect}` };
  }
  return { ok: true, width, height, size: fs.statSync(filePath).size };
}

async function extractFrame(videoPath, destination, { atSeconds } = {}) {
  const ffmpeg = await resolveBinary('ffmpeg');
  if (!ffmpeg) throw new Error('FFmpeg was not found to extract a thumbnail frame. Set FFMPEG_PATH or MUSE_FFMPEG_DIR, or configure MUSE_THUMBNAIL_COMMAND.');
  let position = Number(atSeconds);
  if (!Number.isFinite(position) || position <= 0) {
    const master = await probeVideo(videoPath);
    position = Math.max(1, Math.min(master.duration * 0.08, 45));
  }
  const result = await run(ffmpeg, [
    '-v', 'error', '-ss', position.toFixed(2), '-i', videoPath,
    '-frames:v', '1', '-q:v', '2', '-y', destination
  ], 120000);
  if (result.status !== 0 || !fs.existsSync(destination) || fs.statSync(destination).size < 1024) {
    throw new Error('Could not extract a thumbnail frame from the master: ' + String(result.stderr || '').slice(-500));
  }
  return destination;
}

// --- CMS / share-link upload cap (Nov 10, 2026) ---------------------------
// Both the remote-storage share link and the CMS upload through the
// Cloudflare Tunnel reject files over ~100 MB (HTTP 413 — seen on the
// 277 MB BNSF master, Nov 10 15:05 PKT). Masters over the limit are
// transcoded to fit ~95 MB before upload instead of failing delivery.
const UPLOAD_SIZE_LIMIT_BYTES = 95_000_000;
const UPLOAD_AUDIO_KBPS = 128;

// Video bitrate (kbps) that fits `durationSeconds` under the upload cap:
// total budget minus audio, capped at 7 Mbps (the BNSF 1080p recipe),
// floored at 700 kbps to avoid delivering an unusably low bitrate.
// Returns 0 when even the floor cannot fit the duration.
function uploadBitrateForDuration(durationSeconds) {
  const duration = Number(durationSeconds) || 0;
  if (duration <= 0) return 0;
  const totalKbps = (UPLOAD_SIZE_LIMIT_BYTES * 8 * 0.92) / duration / 1000;
  const videoKbps = Math.floor(totalKbps - UPLOAD_AUDIO_KBPS);
  if (videoKbps < 700) return 0;
  return Math.min(7000, videoKbps);
}

// Return the file to upload: the master itself when it fits the cap,
// otherwise a re-encode beside it (video-upload.mp4) sized to fit.
// Reuses a previous re-encode when it is newer than the master and fits.
// Throws (fail closed) when the master cannot be made to fit — never
// upload an oversize file and eat a 413 after a full render.
async function ensureUploadableMaster(filePath) {
  const originalBytes = fs.statSync(filePath).size;
  if (originalBytes <= UPLOAD_SIZE_LIMIT_BYTES) {
    return { path: filePath, transcoded: false, originalBytes, bytes: originalBytes };
  }
  const directory = path.dirname(filePath);
  const destination = path.join(directory, 'video-upload.mp4');
  const master = await probeVideo(filePath);
  const matchesMaster = async candidate => {
    try {
      const video = await probeVideo(candidate);
      return video.hasVideo && video.hasAudio && video.width >= 1920 && video.height >= 1080 &&
        Math.abs(video.duration - master.duration) <= 0.5;
    } catch {
      return false;
    }
  };
  if (fs.existsSync(destination)) {
    const destStat = fs.statSync(destination);
    if (destStat.size <= UPLOAD_SIZE_LIMIT_BYTES && destStat.mtimeMs >= fs.statSync(filePath).mtimeMs &&
        await matchesMaster(destination)) {
      return { path: destination, transcoded: true, originalBytes, bytes: destStat.size };
    }
  }
  const ffmpeg = await resolveBinary('ffmpeg');
  if (!ffmpeg) throw new Error(`Rendered master is ${Math.round(originalBytes / 1e6)} MB, over the ~100 MB upload cap, and FFmpeg was not found to transcode it. Set FFMPEG_PATH or MUSE_FFMPEG_DIR.`);
  const videoKbps = uploadBitrateForDuration(master.duration);
  if (!videoKbps) {
    throw new Error(`Rendered master is ${Math.round(originalBytes / 1e6)} MB / ${Math.round(master.duration)}s; a 1080p re-encode at the bitrate floor cannot fit the ~100 MB upload cap. Split the video or deliver it outside the CMS.`);
  }
  // CMS requires 1080p. Publish the compressed copy only after it is complete.
  const temporary = path.join(directory, `video-upload-${randomUUID()}.part.mp4`);
  try {
    const result = await run(ffmpeg, [
      '-v', 'error', '-i', filePath,
      '-vf', 'scale=1920:1080',
      '-c:v', 'libx264', '-preset', 'veryfast',
      '-b:v', `${videoKbps}k`, '-maxrate', `${videoKbps}k`, '-bufsize', `${videoKbps * 2}k`,
      '-pix_fmt', 'yuv420p',
      '-c:a', 'aac', '-b:a', `${UPLOAD_AUDIO_KBPS}k`,
      '-movflags', '+faststart', '-y', temporary
    ], 30 * 60 * 1000);
    if (result.status !== 0 || !fs.existsSync(temporary)) {
      throw new Error('Could not transcode the oversize master for upload: ' + String(result.stderr || '').slice(-500));
    }
    const bytes = fs.statSync(temporary).size;
    if (bytes > UPLOAD_SIZE_LIMIT_BYTES) {
      throw new Error(`Transcoded master is still ${Math.round(bytes / 1e6)} MB, over the ~100 MB upload cap. Deliver it outside the CMS.`);
    }
    if (!await matchesMaster(temporary)) throw new Error('Upload copy must preserve 1080p video, audio and master duration');
    fs.renameSync(temporary, destination);
    return { path: destination, transcoded: true, originalBytes, bytes };
  } finally {
    try { fs.rmSync(temporary, { force: true }); } catch { /* keep the primary encode error */ }
  }
}

module.exports = {
  availability,
  resolveBinary,
  probeVideo,
  validateVideoMaster,
  validateThumbnail,
  extractFrame,
  ensureUploadableMaster,
  uploadBitrateForDuration,
  UPLOAD_SIZE_LIMIT_BYTES
};
