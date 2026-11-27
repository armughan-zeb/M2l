'use strict';

/**
 * Provenance sidecar guard for rendered Muse masters.
 *
 * Strictly separates One-Minute Documentary (`documentary-manifest.json`)
 * and 2D Stickman (`stickman-manifest.json`) masters so one engine's video
 * can never be delivered for another job or style.
 */

const fs = require('node:fs');
const path = require('node:path');

function readManifest(manifestPath) {
  try {
    return JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  } catch {
    return null;
  }
}

function findSidecar(videoPath, suffix, fallbackName) {
  const video = path.resolve(String(videoPath || ''));
  const dir = path.dirname(video);
  const stem = video.replace(/\.[^.]+$/, '');
  for (const candidate of [`${stem}.${suffix}`, path.join(dir, fallbackName)]) {
    try {
      if (fs.statSync(candidate).isFile()) return candidate;
    } catch { /* not there */ }
  }
  return '';
}

function findStickmanManifest(videoPath) {
  return findSidecar(videoPath, 'stickman-manifest.json', 'stickman-manifest.json');
}

function findDocumentaryManifest(videoPath) {
  return findSidecar(videoPath, 'documentary-manifest.json', 'documentary-manifest.json');
}

function stampStickmanManifest(videoPath, {
  jobId = '',
  profile = 'wide',
  format = 'wide',
  voice = '',
  soundBed = '',
  durationSeconds = 0,
  lineCount = 0,
  cueCount = 0,
  inferRequests = [],
} = {}) {
  const video = path.resolve(String(videoPath || ''));
  const manifestPath = `${video.replace(/\.[^.]+$/, '')}.stickman-manifest.json`;
  const payload = {
    engine: 'stickman',
    style: '2d_stickman',
    job_id: String(jobId || ''),
    profile: String(profile || 'wide'),
    format: String(format || 'wide'),
    voice: String(voice || ''),
    sound_bed: String(soundBed || ''),
    duration_seconds: Number(durationSeconds) || 0,
    line_count: Number(lineCount) || 0,
    cue_count: Number(cueCount) || 0,
    infer_requests: Array.isArray(inferRequests) ? inferRequests : [],
    created_at: new Date().toISOString(),
  };
  fs.mkdirSync(path.dirname(manifestPath), { recursive: true });
  fs.writeFileSync(manifestPath, JSON.stringify(payload, null, 2), 'utf8');
  return manifestPath;
}

function stampDocumentaryManifest(videoPath, {
  jobId = '',
  topicTag = '',
  headerText = '',
  voice = '',
  durationSeconds = 0,
  shotCount = 0,
  videoShotCount = 0,
  imageShotCount = 0,
  mediaSources = [],
  inferRequests = [],
} = {}) {
  const video = path.resolve(String(videoPath || ''));
  const manifestPath = `${video.replace(/\.[^.]+$/, '')}.documentary-manifest.json`;
  const payload = {
    engine: 'documentary',
    style: 'documentary',
    skill: 'one_minute_documentary',
    job_id: String(jobId || ''),
    topic_tag: String(topicTag || ''),
    header_text: String(headerText || ''),
    voice: String(voice || ''),
    duration_seconds: Number(durationSeconds) || 0,
    shot_count: Number(shotCount) || 0,
    video_shot_count: Number(videoShotCount) || 0,
    image_shot_count: Number(imageShotCount) || 0,
    media_sources: Array.isArray(mediaSources) ? mediaSources : [],
    infer_requests: Array.isArray(inferRequests) ? inferRequests : [],
    created_at: new Date().toISOString(),
  };
  fs.mkdirSync(path.dirname(manifestPath), { recursive: true });
  fs.writeFileSync(manifestPath, JSON.stringify(payload, null, 2), 'utf8');
  return manifestPath;
}

function failResult(manifestPath, reason) {
  return {
    ok: false,
    manifestPath,
    assetCount: 0,
    realCount: 0,
    graphicCount: 0,
    stockCount: 0,
    retrievedCount: 0,
    stockIndices: [],
    foreignJobs: [],
    reason,
  };
}

function validateStickmanManifest(videoPath, { jobId = '' } = {}) {
  const docConflict = findDocumentaryManifest(videoPath);
  if (docConflict) {
    return failResult(docConflict, `Documentary manifest (${docConflict}) found beside 2D Stickman video — refusing mixed-engine master`);
  }
  const manifestPath = findStickmanManifest(videoPath);
  if (!manifestPath) {
    return failResult('', '2D Stickman master is missing its stickman-manifest.json provenance sidecar');
  }
  const manifest = readManifest(manifestPath);
  if (!manifest || typeof manifest !== 'object') {
    return failResult(manifestPath, `2D Stickman manifest at ${manifestPath} is invalid JSON`);
  }
  if (String(manifest.engine || '').trim().toLowerCase() !== 'stickman' ||
      String(manifest.style || '').trim().toLowerCase() !== '2d_stickman') {
    return failResult(manifestPath, `2D Stickman manifest at ${manifestPath} has wrong engine/style (${manifest.engine}/${manifest.style})`);
  }
  const stampedJobId = String(manifest.job_id || '').trim();
  const expectedJobId = String(jobId || '').trim();
  if (stampedJobId && expectedJobId && stampedJobId !== expectedJobId) {
    return failResult(manifestPath, `2D Stickman manifest belongs to job ${stampedJobId}, not ${expectedJobId}`);
  }
  const lineCount = Number(manifest.line_count) || 0;
  const durationSeconds = Number(manifest.duration_seconds) || 0;
  if (lineCount <= 0 || durationSeconds <= 0) {
    return failResult(manifestPath, `2D Stickman manifest is incomplete (lines=${lineCount}, duration=${durationSeconds}s)`);
  }
  return {
    ok: true,
    manifestPath,
    assetCount: lineCount,
    realCount: lineCount,
    graphicCount: 0,
    stockCount: 0,
    retrievedCount: 0,
    stockIndices: [],
    foreignJobs: [],
    reason: '',
  };
}

function validateDocumentaryManifest(videoPath, { jobId = '', requireSidecar = false } = {}) {
  const stickmanConflict = findStickmanManifest(videoPath);
  if (stickmanConflict) {
    return failResult(stickmanConflict, `2D Stickman manifest (${stickmanConflict}) found beside Documentary video — refusing mixed-engine master`);
  }
  const manifestPath = findDocumentaryManifest(videoPath);
  if (!manifestPath) {
    if (requireSidecar) {
      return failResult('', 'Documentary master is missing its documentary-manifest.json provenance sidecar');
    }
    return {
      ok: true,
      manifestPath: '',
      assetCount: 0,
      realCount: 0,
      graphicCount: 0,
      stockCount: 0,
      retrievedCount: 0,
      stockIndices: [],
      foreignJobs: [],
      reason: 'no documentary sidecar found beside manually delivered master',
    };
  }
  const manifest = readManifest(manifestPath);
  if (!manifest || typeof manifest !== 'object') {
    return failResult(manifestPath, `Documentary manifest at ${manifestPath} is invalid JSON`);
  }
  if (!['documentary', 'vidzeo'].includes(String(manifest.engine || '').trim().toLowerCase()) ||
      String(manifest.style || '').trim().toLowerCase() !== 'documentary') {
    return failResult(manifestPath, `Documentary manifest at ${manifestPath} has wrong engine/style (${manifest.engine}/${manifest.style})`);
  }
  const stampedJobId = String(manifest.job_id || '').trim();
  const expectedJobId = String(jobId || '').trim();
  if (stampedJobId && expectedJobId && stampedJobId !== expectedJobId) {
    return failResult(manifestPath, `Documentary manifest belongs to job ${stampedJobId}, not ${expectedJobId}`);
  }
  const shotCount = Number(manifest.shot_count) || 0;
  const durationSeconds = Number(manifest.duration_seconds) || 0;
  if (shotCount <= 0 || durationSeconds <= 0) {
    return failResult(manifestPath, `Documentary manifest is incomplete (shots=${shotCount}, duration=${durationSeconds}s)`);
  }
  return {
    ok: true,
    manifestPath,
    assetCount: shotCount,
    realCount: shotCount,
    graphicCount: 0,
    stockCount: 0,
    retrievedCount: shotCount,
    stockIndices: [],
    foreignJobs: [],
    reason: '',
  };
}

function validateMasterManifest(videoPath, { jobId = '', engine = '', requireDocumentarySidecar = false } = {}) {
  const normalizedEngine = String(engine || '').trim().toLowerCase();
  if (['stickman', '2d-stickman', '2d_stickman', '2d stickman'].includes(normalizedEngine)) {
    return validateStickmanManifest(videoPath, { jobId });
  }
  return validateDocumentaryManifest(videoPath, { jobId, requireSidecar: requireDocumentarySidecar });
}

module.exports = {
  findStickmanManifest,
  findDocumentaryManifest,
  stampStickmanManifest,
  stampDocumentaryManifest,
  validateStickmanManifest,
  validateDocumentaryManifest,
  validateMasterManifest,
};
