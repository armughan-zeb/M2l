'use strict';

// Master-manifest delivery guard (Nov 10, 2026 — BNSF incident).
//
// Root cause: the CMS job r_mw85awoskks8lh was delivered the WRONG video.
// Two Vidnets jobs shared the title "A BNSF Whiteout Crossing, Seen From
// the Driver's Cab":
//   * 876f0e84 (local-queue job, Nov 9, 25 min, director failed ->
//     local director fallback): 10 scenes, 10/10 "Grounded Graphic
//     Fallback", 18 MB master in output/..._20261109_090133_cb5e09d2/.
//   * f04c6b9f (the CMS job's claim, Nov 10, 2h37m): 15 scenes, 15/15
//     real media (14 web + 1 YouTube clip), 277 MB master in the daemon
//     work dir.
// The manual deliver picked the stale output-package file by TITLE
// instead of the claiming job's own master. The delivered manifest even
// said so: its 10 media_paths all point at temp/jobs/876f0e84/... —
// another job's temp dir — while the claiming job was f04c6b9f. And the
// video itself passed every check deliverSaved ran, because those checks
// only look at the container (codec/duration), never at what is IN it.
//
// This guard runs before any upload. It finds the manifest sitting beside
// the master and refuses delivery when:
//   1. the manifest's media_paths reference ANOTHER Vidnets job's temp
//      dir (temp/jobs/<other-id>/ while the claiming job is <id>) —
//      always refused, no override: a master can never belong to a
//      different job; or
//   2. the manifest's stamped job_id disagrees with the claiming Vidnets
//      job id — always refused, no override; or
//   3. the master is 100% graphic fallback (every scene a generated
//      card, zero real media) — refused unless the operator explicitly
//      passes --allow-all-graphic, because a Mixed Media job completed
//      with blank cards is a failed job, not a delivery.
//
// When no manifest sits beside the master (older videos predate the
// manifest), the guard passes with a warning: provenance cannot be
// checked, but the video itself still passed the ffmpeg master checks.

const fs = require('node:fs');
const path = require('node:path');

const GRAPHIC_LABEL_TOKENS = ['graphic fallback', 'grounded graphic', 'generated graphic'];
const JOB_DIR_RE = /temp\/jobs\/([A-Za-z0-9._-]+)\//g;

function isGraphicLabel(label) {
  const text = String(label || '').trim().toLowerCase();
  return GRAPHIC_LABEL_TOKENS.some((token) => text.includes(token));
}

function resolutionIsGraphic(row) {
  if (!row || typeof row !== 'object') return false;
  const kind = String(row.resolution_kind || '').trim().toLowerCase();
  if (kind === 'graphic_fallback' || kind === 'grounded_graphic_fallback') return true;
  return String(row.provider || '').trim().toLowerCase() === 'graphic';
}

function foreignJobReferences(mediaPaths, jobId) {
  const own = String(jobId || '').trim();
  const found = [];
  for (const raw of mediaPaths || []) {
    JOB_DIR_RE.lastIndex = 0;
    let match;
    while ((match = JOB_DIR_RE.exec(String(raw || ''))) !== null) {
      const other = String(match[1] || '').trim();
      if (other && other !== own && !found.includes(other)) found.push(other);
    }
  }
  return found;
}

function summarizeManifest(manifest) {
  if (!manifest || typeof manifest !== 'object') {
    return { assetCount: 0, realCount: 0, graphicCount: 0, foreignJobs: [] };
  }
  const paths = Array.isArray(manifest.media_paths) ? manifest.media_paths : [];
  const labels = Array.isArray(manifest.source_labels) ? manifest.source_labels : [];
  const metadata = manifest.metadata && typeof manifest.metadata === 'object' ? manifest.metadata : {};
  const rows = Array.isArray(metadata.media_resolution_v2) ? metadata.media_resolution_v2 : [];
  const assetCount = Number(manifest.asset_count) || paths.length || labels.length || 0;
  let graphicCount = 0;
  const total = Math.max(paths.length, labels.length, rows.length);
  for (let index = 0; index < total; index += 1) {
    const row = rows[index] && typeof rows[index] === 'object' ? rows[index] : null;
    const graphic = row ? resolutionIsGraphic(row) : isGraphicLabel(labels[index]);
    if (graphic) graphicCount += 1;
  }
  return {
    assetCount,
    realCount: assetCount ? Math.max(0, assetCount - graphicCount) : 0,
    graphicCount,
    foreignJobs: foreignJobReferences(paths, String(manifest.job_id || ''))
  };
}

function findSiblingManifest(videoPath) {
  const video = path.resolve(String(videoPath || ''));
  const dir = path.dirname(video);
  const stem = video.replace(/\.[^.]+$/, '');
  const candidates = [
    `${stem}.manifest.json`,
    path.join(dir, 'media', 'manifest.json'),
    path.join(dir, 'vidnets', 'media', 'manifest.json')
  ];
  for (const candidate of candidates) {
    try {
      if (fs.statSync(candidate).isFile()) return candidate;
    } catch { /* not there */ }
  }
  return '';
}

function readManifest(manifestPath) {
  try {
    return JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  } catch {
    return null;
  }
}

// Pure decision step, exported for tests: given a parsed manifest and the
// claiming Vidnets job id, decide whether the master may be delivered.
function decideManifestDelivery(manifest, { vidnetsJobId = '', allowAllGraphic = false } = {}) {
  const summary = summarizeManifest(manifest);
  const stampedJobId = manifest && typeof manifest.job_id === 'string' ? manifest.job_id.trim() : '';
  if (stampedJobId && vidnetsJobId && stampedJobId !== String(vidnetsJobId).trim()) {
    return {
      ok: false,
      ...summary,
      reason: `manifest belongs to Vidnets job ${stampedJobId}, not the claiming job ${vidnetsJobId} — refusing to deliver another job's video`
    };
  }
  // Foreign-job check: a path under temp/jobs/<other>/ belongs to another
  // job. When the claiming job is unknown AND the manifest carries no
  // stamp, there is nothing to compare against — pass this check (the
  // all-graphic check below is still the backstop) rather than refusing
  // a legitimate old master whose provenance cannot be judged.
  const own = String(vidnetsJobId || '').trim() || stampedJobId;
  const foreign = own ? foreignJobReferences(
    Array.isArray(manifest && manifest.media_paths) ? manifest.media_paths : [],
    own
  ) : [];
  if (foreign.length) {
    return {
      ok: false,
      ...summary,
      foreignJobs: foreign,
      reason: `manifest media_paths reference another job's temp dir (temp/jobs/${foreign.join(', temp/jobs/')}/) while the claiming job is ${own || 'unknown'} — refusing to deliver another job's video`
    };
  }
  if (summary.assetCount > 0 && summary.realCount === 0 && !allowAllGraphic) {
    return {
      ok: false,
      ...summary,
      reason: `master is ${summary.graphicCount}/${summary.assetCount} graphic fallback with zero real media — refusing to deliver blank cards as a completed video (pass --allow-all-graphic only if this is genuinely intended)`
    };
  }
  return { ok: true, ...summary, reason: '' };
}

function validateMasterManifest(videoPath, { vidnetsJobId = '', allowAllGraphic = false } = {}) {
  const manifestPath = findSiblingManifest(videoPath);
  if (!manifestPath) {
    return {
      ok: true,
      manifestPath: '',
      assetCount: 0,
      realCount: 0,
      graphicCount: 0,
      foreignJobs: [],
      reason: 'no manifest found beside the master; media composition unchecked'
    };
  }
  const manifest = readManifest(manifestPath);
  if (!manifest) {
    return {
      ok: true,
      manifestPath,
      assetCount: 0,
      realCount: 0,
      graphicCount: 0,
      foreignJobs: [],
      reason: `manifest at ${manifestPath} is unreadable; media composition unchecked`
    };
  }
  return { manifestPath, ...decideManifestDelivery(manifest, { vidnetsJobId, allowAllGraphic }) };
}

module.exports = {
  findSiblingManifest,
  summarizeManifest,
  foreignJobReferences,
  isGraphicLabel,
  decideManifestDelivery,
  validateMasterManifest
};
