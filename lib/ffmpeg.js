'use strict';

/**
 * FFmpeg/FFprobe discovery and objective local media checks.
 *
 * Muse artifacts are validated by the CMS again before publication. These
 * local checks only avoid uploading an obviously invalid master or thumbnail.
 */

const fs = require('fs');
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
  process.env.MUSE_VIDNETS_ROOT && path.join(process.env.MUSE_VIDNETS_ROOT, 'resources', 'tools', 'ffmpeg', process.platform === 'win32' ? 'ffprobe.exe' : 'ffprobe'),
  path.join(__dirname, '..', 'vidnets', 'resources', 'tools', 'ffmpeg', process.platform === 'win32' ? 'ffprobe.exe' : 'ffprobe'),
  path.join(__dirname, '..', 'vidnets', 'tools', 'ffmpeg', process.platform === 'win32' ? 'ffprobe.exe' : 'ffprobe'),
  'ffprobe'
]);
addCandidate('ffmpeg', [
  process.env.FFMPEG_PATH,
  process.env.MUSE_FFMPEG_DIR && path.join(process.env.MUSE_FFMPEG_DIR, process.platform === 'win32' ? 'ffmpeg.exe' : 'ffmpeg'),
  process.env.MUSE_VIDNETS_ROOT && path.join(process.env.MUSE_VIDNETS_ROOT, 'resources', 'tools', 'ffmpeg', process.platform === 'win32' ? 'ffmpeg.exe' : 'ffmpeg'),
  path.join(__dirname, '..', 'vidnets', 'resources', 'tools', 'ffmpeg', process.platform === 'win32' ? 'ffmpeg.exe' : 'ffmpeg'),
  path.join(__dirname, '..', 'vidnets', 'tools', 'ffmpeg', process.platform === 'win32' ? 'ffmpeg.exe' : 'ffmpeg'),
  'ffmpeg'
]);

const resolved = new Map();

function executableExists(binary) {
  if (path.isAbsolute(binary)) return fs.existsSync(binary);
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

module.exports = {
  availability,
  resolveBinary,
  probeVideo,
  validateVideoMaster,
  validateThumbnail,
  extractFrame
};
