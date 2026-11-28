'use strict';

/**
 * One Muse job from claim to delivered artifacts.
 *
 * Checkpoints live in work/<jobId>/:
 *   job.json          exact dispatched payload plus its digest
 *   script.txt        accepted narration
 *   video.mp4         local copy of the rendered master
 *   thumbnail.jpg     delivered thumbnail
 *   video-meta.json   bytes/sha256/digest of the saved master
 *
 * A retry of the same run reuses these files. A materially different payload
 * archives the old folder instead of deleting it.
 */

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const config = require('./config');
const log = require('./log');
const scriptAdapter = require('../adapters/script');
const videoAdapter = require('../adapters/video');
const thumbnailAdapter = require('../adapters/thumbnail');
const ffmpeg = require('./ffmpeg');
const { validateMasterManifest } = require('./masterManifestGuard');
const { resolveVideoStyle } = require('./videoStyle');
const { sleep } = require('./sleep');
const { cleanupStage, cleanupAllStagesForJob } = require('./stageCleanup');
const { isTransientCmsError, retryDelayMs, cmsRetryOptions } = require('./cmsRetry');

function sha256File(filePath) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash('sha256');
    const stream = fs.createReadStream(filePath);
    stream.on('data', chunk => hash.update(chunk));
    stream.on('end', () => resolve(hash.digest('hex')));
    stream.on('error', reject);
  });
}

// THUMBNAILS SKIPPED (Nov 10, 2026): this worker delivers script + video
// only — no thumbnail generation, no thumbnail upload. VIDZEO_SKIP_THUMBNAILS=1
// is the package default (Dockerfile / docker-compose.yml / setup.sh .env).
// An explicit per-call `skipThumbnail` option (from --skip-thumbnail) wins
// over the env var. If the CMS ever rejects a delivery without a thumbnail,
// the fallback is a single frame-grab — see AGENT_TRAINING.md — not a
// thumbnail pipeline.
function thumbnailsSkipped() {
  const envVal = String(process.env.VIDZEO_SKIP_THUMBNAILS ?? '1').trim();
  return envVal !== '0' && envVal !== 'false';
}

function payloadDigest(job) {
  return materialDigest(job);
}

// RESUME RULE (Nov 10, 2026 — BNSF incident, job r_mw85awoskks8lh):
// same job ID = same job. The CMS re-stamped ONLY `createdAt` when it
// re-dispatched a finished job, and the old raw-JSON digest treated that
// as a different job: prepareWorkDir archived the finished work and the
// daemon re-rendered ~2.5h of video that already existed. The digest must
// therefore identify the job's MATERIAL content, never CMS bookkeeping.
// Volatile fields are stripped at every depth and keys are sorted, so two
// dispatches of the same job hash identically regardless of field order.
const VOLATILE_PAYLOAD_FIELDS = new Set([
  'createdAt', 'updatedAt', 'dispatchedAt', 'claimedAt', 'startedAt',
  'attempt', 'runId', 'resumeScript'
]);

function canonicalPayload(value) {
  if (Array.isArray(value)) return value.map(canonicalPayload);
  if (value && typeof value === 'object') {
    const out = {};
    for (const key of Object.keys(value).sort()) {
      if (VOLATILE_PAYLOAD_FIELDS.has(key)) continue;
      out[key] = canonicalPayload(value[key]);
    }
    return out;
  }
  return value;
}

function materialDigest(job) {
  return crypto.createHash('sha256').update(JSON.stringify(canonicalPayload(job)), 'utf8').digest('hex');
}

// Pre-Nov-10 digest (raw JSON, order- and timestamp-sensitive). Only used
// as a transition accept-list: work written before this change carries the
// raw digest in job.json / video-meta.json, and it must still be recognised
// as the same job until it is re-labelled by a run of the new code.
function legacyPayloadDigest(job) {
  return crypto.createHash('sha256').update(JSON.stringify(job), 'utf8').digest('hex');
}

function readJson(filePath) {
  try { return JSON.parse(fs.readFileSync(filePath, 'utf8')); }
  catch { return null; }
}

// Checkpoint writes are atomic (temp + fsync + rename, lib/config.js):
// a VM reboot mid-write used to leave a torn job.json / video-meta.json
// behind, and the resume path reads these files to decide whether to
// reuse the script and the master.
function writeJson(filePath, value) {
  config.writeJsonAtomic(filePath, value);
}

// How many recent daemon log lines ride along with every CMS progress
// call (see lib/log.js). The CMS job page shows them next to the stage.
const PROGRESS_LOG_LINES = Math.max(1, Math.min(100, Number(process.env.MUSE_PROGRESS_LOG_LINES) || 30));

function progressCheckpointFile(directory) {
  return path.join(directory, 'progress.json');
}

/**
 * The live stage checkpoint: stage/progress/detail, rewritten atomically
 * on every report() — i.e. on every stage transition AND on every Vidnets
 * poll update during the render — not just at claim/render time. A daemon
 * killed mid-render (VM reboot, SIGKILL) leaves an accurate pointer
 * behind, so the resume probe reports where the job really was instead
 * of the hardcoded "script 5%".
 */
function writeProgressCheckpoint(directory, jobId, state) {
  try {
    const prior = readJson(progressCheckpointFile(directory));
    const reportedAt = new Date().toISOString();
    const event = {
      eventId: crypto.randomUUID(),
      stage: state.stage,
      progress: state.progress,
      detail: String(state.detail || '').slice(0, 600),
      reportedAt
    };
    const history = prior?.jobId === jobId && Array.isArray(prior.events) ? prior.events : [];
    writeJson(progressCheckpointFile(directory), {
      jobId, ...event, updatedAt: reportedAt, events: [...history.slice(-31), event]
    });
    return event;
  } catch (error) {
    log.warn(`Could not write the progress checkpoint for ${jobId} (non-fatal): ${error.message}`);
  }
}

function readProgressCheckpoint(directory) {
  const saved = readJson(progressCheckpointFile(directory));
  if (!saved || typeof saved.stage !== 'string' || !Number.isFinite(Number(saved.progress))) return null;
  return {
    stage: saved.stage,
    progress: Math.max(0, Math.min(100, Number(saved.progress))),
    detail: String(saved.detail || ''),
    updatedAt: saved.updatedAt || ''
  };
}

async function syncSavedProgress(client, request, active) {
  if (!active || request.jobId !== active.jobId) return;
  const saved = readJson(progressCheckpointFile(config.workDir(active.jobId)));
  const logs = saved?.jobId === active.jobId && Array.isArray(saved.events) ? saved.events : [];
  const latest = logs.at(-1) || (saved?.jobId === active.jobId && saved.updatedAt
    ? { stage: saved.stage, progress: saved.progress, detail: saved.detail, reportedAt: saved.updatedAt }
    : null);
  if (!latest) return;
  await client.sync(active.jobId, { requestId: request.id, attempt: active.attempt || 1, latest, logs });
}

function prepareWorkDir(job, digest) {
  const directory = config.workDir(job.jobId);
  const marker = path.join(directory, 'job.json');
  const previous = readJson(marker);
  // Archive ONLY when the job materially changed. A re-dispatch that only
  // re-stamps CMS bookkeeping (createdAt, attempt, ...) is the SAME job:
  // its script, checkpoints and master stay in place so the run resumes
  // instead of starting over. Markers written before the material digest
  // existed carry only the raw digest, so fall back to comparing the raw
  // digests for those — a mismatch there still archives, as before.
  const previousJob = previous && previous.job && typeof previous.job === 'object' ? previous.job : null;
  const materiallyChanged = previousJob
    ? materialDigest(previousJob) !== materialDigest(job)
    : Boolean(previous && previous.digest && previous.digest !== digest);
  if (previous && materiallyChanged) {
    const archiveRoot = path.join(config.WORK_ROOT, 'archive');
    fs.mkdirSync(archiveRoot, { recursive: true });
    const archive = path.join(archiveRoot, `${job.jobId}-${Date.now()}`);
    fs.renameSync(directory, archive);
    fs.mkdirSync(directory, { recursive: true });
    log.warn(`Job ${job.jobId} was re-dispatched with different content; previous worker files archived at ${archive}`);
  }
  writeJson(marker, { digest, materialDigest: materialDigest(job), job, savedAt: new Date().toISOString() });
  try {
    for (const entry of fs.readdirSync(directory)) {
      if (entry.endsWith('.tmp')) fs.rmSync(path.join(directory, entry), { force: true });
    }
  } catch { /* ignore */ }
  return directory;
}

function savedVideo(directory, digest, extraDigests = [], { jobId = '', engine = '' } = {}) {
  const meta = readJson(path.join(directory, 'video-meta.json'));
  const file = path.join(directory, 'video.mp4');
  // Accept the current digest plus any transition digests (a master saved
  // before the material-digest change carries the raw digest).
  if (!meta || ![digest, ...extraDigests].includes(meta.digest) || !Number.isFinite(Number(meta.bytes))) return null;
  // Never reuse a master produced by a different style/engine (e.g. documentary vs stickman).
  const recordedEngine = String(meta.engine || '').trim().toLowerCase();
  if (engine && recordedEngine && recordedEngine !== 'agent' && recordedEngine !== 'unknown' && recordedEngine !== engine) {
    const isRecordedStickman = ['stickman', '2d-stickman', '2d_stickman'].includes(recordedEngine);
    const isExpectedStickman = engine === 'stickman';
    if (isRecordedStickman !== isExpectedStickman) return null;
  }
  try {
    const stat = fs.statSync(file);
    if (!stat.isFile() || stat.size !== Number(meta.bytes) || stat.size < 1024 * 1024) return null;
    if (!validateMasterManifest(file, { jobId, engine }).ok) return null;
    return file;
  } catch {
    return null;
  }
}

function savedScript(directory) {
  return scriptAdapter.readSavedScript(directory);
}

function workDirPath(jobId) {
  return path.join(config.WORK_ROOT, config.safeJobId(jobId));
}

function thumbnailCandidates(directory) {
  return ['thumbnail.jpg', 'thumbnail.jpeg', 'thumbnail.png', 'thumbnail.webp']
    .map(name => path.join(directory, name))
    .filter(file => {
      try { return fs.statSync(file).isFile() && fs.statSync(file).size >= 1024; }
      catch { return false; }
    });
}

function readWorkDirState(directory) {
  if (!directory) return null;
  let marker = null;
  try { marker = readJson(path.join(directory, 'job.json')); } catch { marker = null; }
  const progress = readProgressCheckpoint(directory);
  const scriptText = scriptAdapter.readSavedScript(directory);
  const meta = readJson(path.join(directory, 'video-meta.json'));
  const videoFile = path.join(directory, 'video.mp4');
  let hasMaster = false;
  try {
    hasMaster = Boolean(meta) && Number.isFinite(Number(meta.bytes))
      && fs.statSync(videoFile).isFile() && fs.statSync(videoFile).size === Number(meta.bytes)
      && fs.statSync(videoFile).size >= 1024 * 1024;
  } catch { hasMaster = false; }
  const thumbnails = thumbnailCandidates(directory);
  const hasAnything = Boolean(marker || scriptText || hasMaster || progress);
  if (!hasAnything) return null;
  return {
    directory,
    payload: marker && marker.job && typeof marker.job === 'object' ? marker.job : null,
    digest: marker && typeof marker.digest === 'string' ? marker.digest : '',
    progress,
    scriptText,
    hasMaster,
    masterPath: hasMaster ? videoFile : '',
    masterMeta: meta,
    thumbnailPath: thumbnails[0] || ''
  };
}

function newestArchiveDir(jobId) {
  const archiveRoot = path.join(config.WORK_ROOT, 'archive');
  let entries = [];
  try {
    entries = fs.readdirSync(archiveRoot)
      .filter(name => name.startsWith(`${jobId}-`))
      .map(name => ({ name, full: path.join(archiveRoot, name) }))
      .filter(entry => { try { return fs.statSync(entry.full).isDirectory(); } catch { return false; } });
  } catch { return ''; }
  if (!entries.length) return '';
  entries.sort((a, b) => fs.statSync(b.full).mtimeMs - fs.statSync(a.full).mtimeMs);
  return entries[0].full;
}

function findExistingWork(jobId) {
  const live = readWorkDirState(workDirPath(jobId));
  if (live) return { ...live, source: 'work' };
  const archive = newestArchiveDir(jobId);
  if (!archive) return null;
  const archived = readWorkDirState(archive);
  if (!archived) return null;
  return { ...archived, source: 'archive' };
}

function restoreArchivedWork(job, archiveDir) {
  const directory = workDirPath(job.jobId);
  const digest = payloadDigest(job);
  const currentMarker = path.join(directory, 'job.json');
  let aside;
  if (fs.existsSync(directory)) {
    const archiveRoot = path.join(config.WORK_ROOT, 'archive');
    fs.mkdirSync(archiveRoot, { recursive: true });
    aside = path.join(archiveRoot, `${job.jobId}-${Date.now()}-superseded`);
    fs.renameSync(directory, aside);
  }
  try {
    fs.renameSync(archiveDir, directory);
  } catch (error) {
    if (aside && !fs.existsSync(directory)) {
      try { fs.renameSync(aside, directory); }
      catch (rollbackError) { log.warn(`Work remains at ${aside}: ${rollbackError.message}`); }
    }
    throw error;
  }
  if (aside) log.warn(`Job ${job.jobId}: partial work preserved at ${aside}`);
  writeJson(currentMarker, { digest, materialDigest: materialDigest(job), job, savedAt: new Date().toISOString() });
  const metaFile = path.join(directory, 'video-meta.json');
  const meta = readJson(metaFile);
  if (meta && meta.digest && meta.digest !== digest) {
    writeJson(metaFile, { ...meta, digest, relabelledAt: new Date().toISOString() });
  }
  return directory;
}

function decideClaim(existing, job) {
  if (!existing) return { decision: 'fresh', reason: 'no local work for this job id' };
  if (existing.payload && materialDigest(existing.payload) !== materialDigest(job)) {
    const changed = changedTopLevelKeys(existing.payload, job);
    return {
      decision: 'fresh',
      reason: `archived work is for a materially different job (changed: ${changed.join(', ') || 'payload'}) — not resuming it`
    };
  }
  if (existing.hasMaster) {
    return { decision: 'deliver', reason: `finished master in ${existing.source} work dir`, source: existing.source };
  }
  if (existing.scriptText || existing.progress) {
    return { decision: 'resume', reason: `mid-run work in ${existing.source} work dir`, source: existing.source };
  }
  return { decision: 'fresh', reason: 'local work dir has no script or progress to resume' };
}

function changedTopLevelKeys(a, b) {
  const keys = new Set([...Object.keys(a || {}), ...Object.keys(b || {})]);
  const changed = [];
  for (const key of [...keys].sort()) {
    if (VOLATILE_PAYLOAD_FIELDS.has(key)) continue;
    if (JSON.stringify(canonicalPayload((a || {})[key])) !== JSON.stringify(canonicalPayload((b || {})[key]))) changed.push(key);
  }
  return changed;
}

function jobSummary(job) {
  const duration = Math.max(1, Number(job.targetDurationMinutes) || Number(job.channel?.scriptSettings?.targetMinutes) || 8);
  const targetWords = Number(job.targetWordCount) || Math.round(duration * 150);
  const minimumWords = Number(job.requirements?.minimumWords) || Math.round(duration * 135);

  const thumbReq = job.requirements?.thumbnail || {};
  const refThumbUrl = thumbReq.referenceUrl || job.titlePackage?.refThumb || job.thumbnailReferences?.[0]?.url || '';
  const refThumbImgUrl = thumbReq.referenceImageUrl || job.titlePackage?.refThumbImageUrl || job.thumbnailReferences?.[0]?.imageUrl || '';
  const concept = job.thumbnailIdea || thumbReq.concept || job.titlePackage?.thumbnailIdea || '';

  const refTranscript = job.titlePackage?.referenceTranscript || job.channel?.scriptSettings?.referenceTranscript || (job.scriptReferences?.[0]?.transcript) || '';
  const refScriptUrl = job.titlePackage?.refScript || job.channel?.scriptSettings?.refScriptUrl || (job.scriptReferences?.[0]?.url) || '';

  const workDir = config.workDir(job.jobId);

  return {
    jobId: job.jobId,
    title: job.title,
    channel: job.channel?.name || job.channel?.id || '',
    channelConfig: job.channel || {},
    targetDurationMinutes: duration,
    targetWordCount: targetWords,
    minimumWords,
    description: job.description || '',
    premise: job.premise || '',
    thumbnailIdea: concept,
    voice: job.voice || job.channel?.videoOptions?.voice || '',
    scriptSettings: job.channel?.scriptSettings || {},
    scriptReferences: job.scriptReferences || [],
    competitors: job.competitors || [],
    thumbnailReferences: job.thumbnailReferences || [],
    titlePackage: job.titlePackage || {},
    requirements: job.requirements || {},
    sceneGuidelines: job.requirements?.sceneGuidelines || {},
    scriptStyle: {
      targetDurationMinutes: duration,
      targetWordCount: targetWords,
      minimumWords,
      tone: job.channel?.scriptSettings?.tone || 'Authoritative, engaging documentary narrative',
      structure: job.channel?.scriptSettings?.customStructure || job.requirements?.sceneGuidelines?.structure || '',
      rules: job.channel?.scriptSettings?.rules || job.requirements?.sceneGuidelines?.sourceGrounding || '',
      refScriptUrl,
      hasReferenceTranscript: Boolean(refTranscript),
      referenceTranscriptPreview: refTranscript ? refTranscript.slice(0, 300) : ''
    },
    thumbnailStyle: {
      concept,
      aspect: thumbReq.aspect || '16:9',
      referenceUrl: refThumbUrl,
      referenceImageUrl: refThumbImgUrl
    },
    outputLocations: job.outputLocations || {
      workDir,
      scriptPath: path.join(workDir, 'script.txt'),
      videoPath: path.join(workDir, 'video.mp4'),
      thumbnailPath: path.join(workDir, 'thumbnail.jpg'),
      deliveryCommand: `node muse-agent.js deliver --job ${job.jobId}`
    }
  };
}

async function executeJob({ client, job, attempt, signal, onBusy, skipThumbnail = false }) {
  if (!job || !job.jobId) throw new Error('Muse job payload has no jobId');
  const skipThumb = skipThumbnail === true || thumbnailsSkipped();
  const resolvedStyle = resolveVideoStyle(job);
  const digest = payloadDigest(job);
  const directory = prepareWorkDir(job, digest);
  config.writeActiveJob({ jobId: job.jobId, payload: job, attempt: Number(attempt) || 1, digest, startedAt: new Date().toISOString() });

  let last = { stage: 'script', progress: 5, detail: 'Muse agent claimed the job' };
  let keepaliveBusy = false;
  let settled = false;
  let pendingScriptCheckpoint = '';
  const report = async (stage, progress, detail, advancing = true) => {
    last = { stage, progress, detail };
    onBusy?.(job.jobId, stage, progress, detail);
    // Checkpoint first, CMS second: the local file is the resume source
    // of truth when the CMS is unreachable, so it must be written even
    // when the progress POST below fails or is retried.
    const event = writeProgressCheckpoint(directory, job.jobId, last);
    const logs = log.recentLines(PROGRESS_LOG_LINES);
    for (let attemptNumber = 0; attemptNumber < 3; attemptNumber += 1) {
      try {
        const response = await client.progress(job.jobId, stage, progress, detail, advancing, { ...event, logs, attempt, ...(pendingScriptCheckpoint ? { scriptText: pendingScriptCheckpoint } : {}) });
        if (response?.scriptSaved) pendingScriptCheckpoint = '';
        return;
      } catch (error) {
        // Authentication and a settled job are terminal. A transient network
        // blip must not destroy a two-hour render.
        if (error.status === 401 || error.status === 409) throw error;
        if (attemptNumber === 2) {
          log.warn(`Could not report ${stage} progress to the CMS: ${error.message}`);
          return;
        }
        await sleep(2000 * (attemptNumber + 1), signal);
      }
    }
  };

  let commandReportPromise = null;
  let queuedCommandReport = null;
  const flushCommandReport = () => {
    if (commandReportPromise || !queuedCommandReport) return;
    const next = queuedCommandReport;
    queuedCommandReport = null;
    commandReportPromise = report(next.stage, next.progress, next.detail, next.advancing)
      .catch(error => { log.warn(`Could not report ${next.stage} command progress: ${error.message}`); })
      .finally(() => {
        commandReportPromise = null;
        if (queuedCommandReport) flushCommandReport();
      });
  };
  const drainCommandReports = async () => {
    while (commandReportPromise || queuedCommandReport) {
      flushCommandReport();
      if (commandReportPromise) await commandReportPromise;
    }
  };
  const reportFromCommand = (stage, progress, detail, advancing = true) => {
    // Coalesce bursty stdout frame callbacks while preserving the latest
    // progress/detail and keeping at most one progress POST in flight.
    queuedCommandReport = { stage, progress, detail, advancing };
    flushCommandReport();
  };

  let lastKeepaliveWarnAt = 0;
  const keepalive = setInterval(() => {
    if (keepaliveBusy) return;
    keepaliveBusy = true;
    // A keepalive never counts as advancement in the CMS. It only proves the
    // worker is alive while a subprocess, renderer or upload runs between
    // stage transitions, flushing fresh log lines continuously.
    client.progress(job.jobId, last.stage, last.progress, last.detail, false, { logs: log.recentLines(PROGRESS_LOG_LINES), attempt, ...(pendingScriptCheckpoint ? { scriptText: pendingScriptCheckpoint } : {}) })
      .then(response => {
        lastKeepaliveWarnAt = 0;
        if (response?.scriptSaved) pendingScriptCheckpoint = '';
      })
      .catch(error => {
        const now = Date.now();
        if (now - lastKeepaliveWarnAt >= 60000) {
          lastKeepaliveWarnAt = now;
          log.warn(`Keepalive log sync to CMS failed: ${error.message}`);
        }
      })
      .finally(() => { keepaliveBusy = false; });
  }, Math.max(4000, Number(process.env.MUSE_PROGRESS_KEEPALIVE_MS) || 8000));

  try {
    // ---- narration ----
    let scriptText = savedScript(directory);
    if (!scriptText && typeof job.resumeScript === 'string' && job.resumeScript.trim()) {
      scriptText = job.resumeScript.trim();
      scriptAdapter.saveScript(directory, scriptText, { engine: 'cms-checkpoint' });
    }
    let scriptCheck = scriptText ? scriptAdapter.validateScript(scriptText, job) : null;
    let scriptMeta = readJson(path.join(directory, 'script-meta.json'))?.meta || {};
    if (!scriptText || !scriptCheck.ok) {
      scriptText = '';
      await report('script', 10, 'Generating narration');
      const script = await scriptAdapter.generateScript(job, directory, {
        signal,
        onDetail: detail => reportFromCommand('script', 30, detail),
        onProgress: (progress, detail) => reportFromCommand('script', progress, detail)
      });
      scriptText = script.scriptText;
      scriptMeta = script.meta || {};
      scriptCheck = scriptAdapter.validateScript(scriptText, job);
      if (!scriptCheck.ok) throw new Error(scriptCheck.error);
    }
    await drainCommandReports();
    const editorialIssues = scriptMeta.publishable === false ? (scriptMeta.quality?.issues || []).slice(0, 3) : [];
    const scriptWarnings = [...(scriptCheck.warnings || []), ...editorialIssues].filter(Boolean);
    pendingScriptCheckpoint = scriptText;
    await report('script', 100, `Narration ready (${scriptCheck.words} words)${scriptWarnings.length ? '; warnings: ' + scriptWarnings.join('; ') : ''}`);
    log.info(`Narration ready: ${scriptCheck.words} words`);
    // Stage boundary: reap anything the script command left behind
    // (grandchildren outlive their parent) before the render starts, so
    // the render's memory admission is measured against a clean stage.
    try { await cleanupStage(job.jobId, 'script'); } catch (error) { log.warn(`Script stage cleanup failed (non-fatal): ${error.message}`); }

    // ---- render ----
    // Accept the pre-material-digest raw digest as well: a master saved
    // before that change carries it in video-meta.json and must still be
    // reused instead of re-rendered.
    let videoPath = savedVideo(directory, digest, [legacyPayloadDigest(job)], { jobId: job.jobId, engine: resolvedStyle.engine });
    let renderMeta = videoPath ? (readJson(path.join(directory, 'video-meta.json')) || {}) : null;
    let renderThumbnail = '';
    if (videoPath) {
      await report('video', 100, `Reusing the saved ${resolvedStyle.label} master`);
      log.info(`Reusing the completed ${resolvedStyle.label} master from the previous attempt`);
    } else {
      await report('video', 2, `Starting the 1080p render (${resolvedStyle.label})`);
      const rendered = await videoAdapter.render(job, scriptText, directory, {
        signal,
        attempt,
        onProgress: (progress, detail, advancing) => reportFromCommand('video', progress, detail, advancing)
      });
      await drainCommandReports();
      videoPath = rendered.videoPath;
      renderThumbnail = rendered.thumbnailPath || '';
      renderMeta = {
        digest,
        engine: rendered.meta?.engine || resolvedStyle.engine,
        videoStyle: rendered.meta?.videoStyle || resolvedStyle.style,
        realScenes: rendered.meta?.realScenes,
        totalScenes: rendered.meta?.totalScenes,
        graphicScenes: rendered.meta?.graphicScenes,
        bytes: fs.statSync(videoPath).size,
        sha256: await sha256File(videoPath),
        savedAt: new Date().toISOString()
      };
      writeJson(path.join(directory, 'video-meta.json'), renderMeta);
    }
    // Stage boundary: the render wrapper has exited; reap its leftover
    // launcher/ffmpeg/Chrome grandchildren before validation/upload, so
    // they cannot hold RAM (or a stale Chrome) into the next stage.
    try { await cleanupStage(job.jobId, 'render'); } catch (error) { log.warn(`Render stage cleanup failed (non-fatal): ${error.message}`); }
    await report('video', 96, 'Checking duration, resolution, audio and full decode');
    const masterCheck = await ffmpeg.validateVideoMaster(videoPath, job.targetDurationMinutes);
    if (!masterCheck.ok) throw new Error('Rendered master failed local checks: ' + masterCheck.failures.join('; '));
    const manifestCheck = validateMasterManifest(videoPath, { jobId: job.jobId, engine: resolvedStyle.engine });
    if (!manifestCheck.ok) throw new Error('Rendered master failed media checks: ' + manifestCheck.reason);
    await report('video', 100, 'Master validated locally');

    // Upload cap (Nov 10, 2026): masters over ~95 MB are transcoded to fit
    // the ~100 MB CMS/share-link limit before upload, never after a 413.
    const uploadable = await ffmpeg.ensureUploadableMaster(videoPath);
    if (uploadable.transcoded) {
      log.info(`Master was ${Math.round(uploadable.originalBytes / 1e6)} MB; transcoded to ${Math.round(uploadable.bytes / 1e6)} MB for upload (${uploadable.path})`);
      videoPath = uploadable.path;
      renderMeta = { ...renderMeta, bytes: uploadable.bytes, sha256: await sha256File(videoPath) };
    }

    // ---- thumbnail (skipped when VIDZEO_SKIP_THUMBNAILS=1) ----
    let thumbnailPath = '';
    let thumbSource = 'skipped';
    if (skipThumb) {
      await report('thumbnail', 100, 'Thumbnail skipped — worker delivers script + video only');
      log.info('Thumbnail skipped (VIDZEO_SKIP_THUMBNAILS=1)');
    } else {
      const thumb = await thumbnailAdapter.generate(job, scriptText, videoPath, directory, { signal });
      thumbnailPath = thumb.thumbnailPath;
      thumbSource = thumb.resumed ? 'saved' : String(process.env.MUSE_THUMBNAIL_COMMAND || '').trim() ? 'command' : 'video-frame';
      await report('thumbnail', 100, 'Thumbnail ready');
      log.info('Thumbnail ready');
      // Stage boundary: reap the thumbnail command's leftovers before the
      // upload stage, which needs no subprocesses of its own.
      try { await cleanupStage(job.jobId, 'thumbnail'); } catch (error) { log.warn(`Thumbnail stage cleanup failed (non-fatal): ${error.message}`); }
    }

    // ---- deliver ----
    await report('video', 99, 'Uploading the master');
    const videoUpload = await client.upload(job.jobId, 'video', videoPath);
    log.info(`Master uploaded (${Math.round(Number(videoUpload.artifact?.bytes || 0) / (1024 * 1024))} MB)`);
    if (thumbnailPath) {
      await client.upload(job.jobId, 'thumbnail', thumbnailPath);
      log.info('Thumbnail uploaded');
    }

    await client.complete(job.jobId, {
      scriptText,
      wordCount: scriptCheck.words,
      meta: {
        worker: 'muse-agent',
        attempt: Number(attempt) || 1,
        scriptQuality: { publishable: scriptMeta.publishable !== false, warnings: scriptWarnings },
        video: {
          bytes: Number(videoUpload.artifact?.bytes) || fs.statSync(videoPath).size,
          sha256: videoUpload.artifact?.sha256 || renderMeta?.sha256 || '',
          engine: renderMeta?.engine || resolvedStyle.engine,
          videoStyle: renderMeta?.videoStyle || resolvedStyle.style
        },
        productionEngine: renderMeta?.engine || resolvedStyle.engine,
        videoStyle: renderMeta?.videoStyle || resolvedStyle.style,
        thumbnail: { sha256: '' },
        thumbnailSource: thumbSource,
        reviewPerformed: false,
        factualAudit: null
      }
    });
    log.info(`Job ${job.jobId} delivered to the CMS`);
    settled = true;
    return { ok: true, scriptWords: scriptCheck.words, videoPath, thumbnailPath };
  } catch (error) {
    if (error.status === 409) {
      log.warn(`Job ${job.jobId} is no longer active in the CMS; saved work is retained and delivery stopped`);
      settled = true;
      return { ok: false, abandoned: true, error: error.message };
    }
    throw error;
  } finally {
    clearInterval(keepalive);
    queuedCommandReport = null;
    if (commandReportPromise) await commandReportPromise.catch(() => {});
    // End-of-job sweep: reap any stage group left over by an error,
    // a timeout kill, or an abandoned (409) job, so a failed job's
    // workers cannot hold RAM into the next CMS attempt. The per-stage
    // cleanups above already handled the success path; this is a no-op
    // verify there. Cleanup never fails the job.
    try { await cleanupAllStagesForJob(job.jobId); } catch (error) { log.warn(`End-of-job stage cleanup failed (non-fatal): ${error.message}`); }
    if (settled) {
      try {
        for (const entry of fs.readdirSync(directory)) {
          if (entry.endsWith('.tmp')) fs.rmSync(path.join(directory, entry), { force: true });
        }
      } catch { /* ignore */ }
      config.clearActiveJob();
    }
  }
}

/**
 * Agent-driven delivery: the operating agent already wrote script.txt and
 * rendered the artifacts. Validate, upload, complete.
 */
async function deliverSaved({ client, job, attempt, signal, scriptFile, videoFile, thumbnailFile, skipThumbnail = false }) {
  const skipThumb = skipThumbnail === true || thumbnailsSkipped();
  const resolvedStyle = resolveVideoStyle(job);
  const digest = payloadDigest(job);
  const directory = prepareWorkDir(job, digest);
  config.writeActiveJob({ jobId: job.jobId, payload: job, attempt: Number(attempt) || 1, digest, startedAt: new Date().toISOString() });

  let settled = false;
  // Real-time logs, mirroring executeJob: every step reports stage/%/
  // detail + recent daemon log lines to the CMS, and writes the local
  // progress checkpoint, so the CMS job page shows the true local state
  // on the deliver path instead of going quiet until completion.
  const report = async (stage, progress, detail) => {
    const event = writeProgressCheckpoint(directory, job.jobId, { stage, progress, detail });
    for (let attemptNumber = 0; attemptNumber < 3; attemptNumber += 1) {
      try {
        await client.progress(job.jobId, stage, progress, detail, true, {
          ...event, attempt, logs: log.recentLines(PROGRESS_LOG_LINES)
        });
        return;
      } catch (error) {
        if (error.status === 401 || error.status === 409) throw error;
        if (attemptNumber === 2) {
          log.warn(`Could not report ${stage} progress to the CMS: ${error.message}`);
          return;
        }
        await sleep(2000 * (attemptNumber + 1), signal);
      }
    }
  };
  try {
    const scriptPath = scriptFile ? path.resolve(scriptFile) : path.join(directory, 'script.txt');
    let scriptText = '';
    try { scriptText = fs.readFileSync(scriptPath, 'utf8').trim(); }
    catch { throw new Error(`Narration file is missing: ${scriptPath}`); }
    const check = scriptAdapter.validateScript(scriptText, job);
    if (!check.ok) throw new Error(check.error);
    scriptAdapter.saveScript(directory, scriptText, { engine: 'agent', words: check.words });

    let videoPath = '';
    if (videoFile) {
      const source = path.resolve(videoFile);
      if (!fs.existsSync(source) || !fs.statSync(source).isFile()) throw new Error('Video file is missing: ' + source);
      if (source !== path.join(directory, 'video.mp4')) fs.copyFileSync(source, path.join(directory, 'video.mp4'));
      videoPath = path.join(directory, 'video.mp4');
    } else {
      videoPath = path.join(directory, 'video.mp4');
      if (!fs.existsSync(videoPath)) throw new Error('video.mp4 is missing. Render the master or pass --video <file>.');
    }

    await report('video', 96, 'Agent supplied artifacts; validating the master');
    const masterCheck = await ffmpeg.validateVideoMaster(videoPath, job.targetDurationMinutes);
    if (!masterCheck.ok) throw new Error('Rendered master failed local checks: ' + masterCheck.failures.join('; '));

    // BNSF incident (Nov 10, 2026): a manual deliver shipped another
    // job's all-graphic master because the container checks above cannot
    // see what is IN the video. The manifest beside the master is the
    // provenance: verify ownership and the manifest's scene assignments.
    // Check the OPERATOR-SUPPLIED source file, not the work-dir copy —
    // the manifest sits beside the source, in its output package.
    // The claiming Vidnets job id comes from the work dir's vidnets-job.json.
    const manifestVideoPath = videoFile ? path.resolve(videoFile) : videoPath;
    const manifestCheck = validateMasterManifest(manifestVideoPath, { jobId: job.jobId, engine: resolvedStyle.engine });
    if (manifestCheck.manifestPath) {
      log.info(`Master manifest ${manifestCheck.manifestPath}: ${manifestCheck.realCount}/${manifestCheck.assetCount} verified scenes`);
    } else if (manifestCheck.reason) {
      log.warn(`Master manifest check: ${manifestCheck.reason}`);
    }
    if (!manifestCheck.ok) throw new Error('Rendered master failed provenance checks: ' + manifestCheck.reason);

    // Keep the local master receipt tied to the original bytes.
    const masterPath = videoPath;
    // Upload cap (Nov 10, 2026): masters over ~95 MB are transcoded to fit
    // the ~100 MB CMS/share-link limit before upload, never after a 413.
    const uploadable = await ffmpeg.ensureUploadableMaster(videoPath);
    if (uploadable.transcoded) {
      log.info(`Master was ${Math.round(uploadable.originalBytes / 1e6)} MB; transcoded to ${Math.round(uploadable.bytes / 1e6)} MB for upload (${uploadable.path})`);
      videoPath = uploadable.path;
    }

    // Thumbnail: skipped when VIDZEO_SKIP_THUMBNAILS=1 or --skip-thumbnail.
    // A --thumbnail file still wins when thumbnails are NOT skipped.
    let thumbnailPath = '';
    let thumbSource = 'skipped';
    if (skipThumb) {
      await report('thumbnail', 100, 'Thumbnail skipped — worker delivers script + video only');
      log.info('Thumbnail skipped (VIDZEO_SKIP_THUMBNAILS=1 or --skip-thumbnail)');
    } else {
      thumbnailPath = thumbnailFile ? path.resolve(thumbnailFile) : '';
      if (thumbnailPath) {
        if (!fs.existsSync(thumbnailPath)) throw new Error('Thumbnail file is missing: ' + thumbnailPath);
        const extension = ['.jpg', '.jpeg', '.png', '.webp'].includes(path.extname(thumbnailPath).toLowerCase())
          ? path.extname(thumbnailPath).toLowerCase() : '.jpg';
        const destination = path.join(directory, `thumbnail${extension}`);
        if (destination !== thumbnailPath) fs.copyFileSync(thumbnailPath, destination);
        thumbnailPath = destination;
      } else {
        thumbnailPath = (await thumbnailAdapter.generate(job, scriptText, videoPath, directory, { signal })).thumbnailPath;
      }
      const thumbCheck = await ffmpeg.validateThumbnail(thumbnailPath, '16:9');
      if (!thumbCheck.ok) throw new Error('Thumbnail failed local checks: ' + thumbCheck.error);
      thumbSource = String(process.env.MUSE_THUMBNAIL_COMMAND || '').trim() ? 'command' : 'video-frame';
    }

    writeJson(path.join(directory, 'video-meta.json'), {
      digest,
      engine: resolvedStyle.explicit ? resolvedStyle.engine : 'agent',
      videoStyle: resolvedStyle.style,
      bytes: fs.statSync(masterPath).size,
      sha256: await sha256File(masterPath),
      savedAt: new Date().toISOString()
    });

    await report('video', 99, 'Uploading the master');
    const videoUpload = await client.upload(job.jobId, 'video', videoPath);
    let thumbUpload = null;
    if (thumbnailPath) thumbUpload = await client.upload(job.jobId, 'thumbnail', thumbnailPath);
    await client.complete(job.jobId, {
      scriptText,
      wordCount: check.words,
      meta: {
        worker: 'muse-agent',
        attempt: Number(attempt) || 1,
        productionEngine: resolvedStyle.engine,
        videoStyle: resolvedStyle.style,
        video: {
          bytes: Number(videoUpload.artifact?.bytes) || fs.statSync(videoPath).size,
          sha256: videoUpload.artifact?.sha256 || '',
          engine: resolvedStyle.explicit ? resolvedStyle.engine : 'agent',
          videoStyle: resolvedStyle.style
        },
        thumbnail: { sha256: thumbUpload?.artifact?.sha256 || '' },
        thumbnailSource: thumbSource,
        reviewPerformed: false,
        factualAudit: null
      }
    });
    log.info(`Job ${job.jobId} delivered to the CMS (${check.words} words)`);
    await report('video', 100, 'Delivered to the CMS');
    settled = true;
    return { ok: true, scriptWords: check.words, videoPath, thumbnailPath };
  } catch (error) {
    if (error.status === 409) {
      log.warn(`Job ${job.jobId} is no longer active in the CMS; saved work is retained`);
      settled = true;
      return { ok: false, abandoned: true, error: error.message };
    }
    throw error;
  } finally {
    // Same end-of-job sweep as executeJob: a thumbnail command run by
    // deliverSaved registers its group, and it must not outlive delivery.
    try { await cleanupAllStagesForJob(job.jobId); } catch (error) { log.warn(`End-of-job stage cleanup failed (non-fatal): ${error.message}`); }
    if (settled) config.clearActiveJob();
  }
}

/**
 * Re-attach to a job saved by a crashed worker. A CMS job that has already
 * finished or failed is detected before any stage runs.
 *
 * CMS-outage resilience (Nov 9, 2026): a transient CMS error (502/503/530,
 * transport failure) while probing the saved job used to throw out of
 * start-up and kill the daemon within ~1s of every restart, for hours.
 * Now the probe retries with exponential backoff (bounded: maxAttempts,
 * default 12, env MUSE_CMS_RESUME_ATTEMPTS) and then returns
 * { deferred: true } WITHOUT clearing the resume marker and WITHOUT
 * throwing — the caller keeps polling and re-attempts the resume, so the
 * daemon survives hours of CMS outage and resumes cleanly when the CMS
 * returns. Only 409 (job settled in the CMS) clears the marker and only
 * 401 / non-transient errors still throw.
 */
async function resumeActiveJob({ client, signal, onBusy, maxAttempts }) {
  const saved = config.readActiveJob();
  if (!saved) return null;
  const job = saved.payload;
  const directory = config.workDir(job.jobId);
  // Resume from the live stage checkpoint, not from the start: the
  // progress.json written on every report() says where the job really
  // was when the daemon died (e.g. video 45% — Finding media). Without
  // it the CMS job page jumps back to "script 5%" on every restart and
  // the operator cannot tell a resume from a fresh claim.
  const savedProgress = readProgressCheckpoint(directory);
  const probeStage = savedProgress?.stage || 'script';
  const probeProgress = savedProgress ? savedProgress.progress : 5;
  const probeDetail = savedProgress?.detail
    ? `Worker reconnected; resuming saved work (was ${savedProgress.stage} ${savedProgress.progress}% — ${savedProgress.detail})`
    : 'Worker reconnected; resuming saved work';
  log.info(`Resuming saved Muse job ${job.jobId} (attempt ${saved.attempt || 1})${savedProgress ? ` from ${savedProgress.stage} ${savedProgress.progress}%` : ''}`);
  const options = cmsRetryOptions();
  const attempts = Math.max(1, Math.floor(Number(maxAttempts) || options.maxAttempts));
  let probed = false;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const response = await client.progress(job.jobId, probeStage, probeProgress, probeDetail, true, {
        attempt: saved.attempt || 1, logs: log.recentLines(PROGRESS_LOG_LINES)
      });
      if (response?.completed) {
        config.clearActiveJob();
        return { ok: true, alreadyDelivered: true };
      }
      probed = true;
      break;
    } catch (error) {
      if (error.status === 409) {
        log.warn('The saved Muse job is no longer active in the CMS; clearing the local resume marker');
        config.clearActiveJob();
        return null;
      }
      if (error.status === 401) throw error;
      if (!isTransientCmsError(error)) throw error;
      if (attempt === attempts - 1) {
        log.warn(`CMS still unavailable after ${attempts} resume attempt(s) for job ${job.jobId} (${error.message}); deferring resume — daemon keeps polling, saved work retained`);
        return { deferred: true, jobId: job.jobId, attempts, error: error.message };
      }
      const delay = retryDelayMs(attempt, options);
      log.warn(`CMS unavailable while resuming job ${job.jobId} (attempt ${attempt + 1}/${attempts}, ${error.message}); retrying in ${Math.round(delay / 1000)}s — saved work retained`);
      await sleep(delay, signal);
      if (signal?.aborted) {
        return { deferred: true, jobId: job.jobId, attempts: attempt + 1, error: 'resume aborted' };
      }
    }
  }
  if (!probed) {
    return { deferred: true, jobId: job.jobId, attempts, error: 'resume probe did not complete' };
  }
  if (!fs.existsSync(path.join(directory, 'job.json'))) {
    log.warn('Saved job has no worker checkpoint; clearing the resume marker');
    config.clearActiveJob();
    return null;
  }
  try {
    return await executeJob({ client, job, attempt: saved.attempt || 1, signal, onBusy });
  } catch (error) {
    return reportExecutionFailure(client, job.jobId, saved.attempt || 1, error, signal);
  }
}

async function reportExecutionFailure(client, jobId, attempt, error, signal) {
  if (signal?.aborted || error.status === 401) throw error;
  if (isTransientCmsError(error)) {
    return { deferred: true, jobId, error: error.message };
  }
  try {
    await client.fail(jobId, error.message, attempt);
  } catch (reportError) {
    if (reportError.status === 401) throw reportError;
    if (reportError.status !== 409) {
      return { deferred: true, jobId, error: reportError.message };
    }
  }
  config.clearActiveJob();
  return { ok: false, error: error.message };
}

async function pickValidatedMaster(existing, job) {
  const candidates = [];
  if (existing.masterPath) candidates.push(existing.masterPath);
  for (const candidate of candidates) {
    try {
      const check = await ffmpeg.validateVideoMaster(candidate, job.targetDurationMinutes);
      if (!check.ok) {
        log.warn(`Candidate master ${candidate} failed validation for ${job.jobId}: ${check.failures.join('; ')}`);
        continue;
      }
      const resolvedStyle = resolveVideoStyle(job);
      const manifestCheck = validateMasterManifest(candidate, { jobId: job.jobId, engine: resolvedStyle.engine });
      if (!manifestCheck.ok) {
        log.warn(`Candidate master ${candidate} failed provenance checks for ${job.jobId}: ${manifestCheck.reason}`);
        continue;
      }
      return candidate;
    } catch (error) {
      log.warn(`Could not validate candidate master ${candidate} for ${job.jobId}: ${error.message}`);
    }
  }
  return '';
}

async function pickArchivedThumbnail(existing) {
  if (!existing.thumbnailPath) return '';
  try {
    const check = await ffmpeg.validateThumbnail(existing.thumbnailPath, '16:9');
    if (check.ok) return existing.thumbnailPath;
    log.warn(`Archived thumbnail ${existing.thumbnailPath} failed validation (${check.error}); a frame will be grabbed from the master instead`);
  } catch (error) {
    log.warn(`Could not validate archived thumbnail ${existing.thumbnailPath}: ${error.message}`);
  }
  return '';
}

async function resolveClaim({ client, job, attempt, signal, onBusy }) {
  if (!job || !job.jobId) throw new Error('Muse job payload has no jobId');
  const active = config.readActiveJob();
  if (active && active.jobId !== job.jobId) {
    log.warn(`Job ${job.jobId} claimed while ${active.jobId} is still active on this worker; not starting it — one video at a time`);
    return { ok: false, decision: 'busy', jobId: job.jobId, activeJobId: active.jobId };
  }

  const directory = workDirPath(job.jobId);
  const report = async (stage, progress, detail) => {
    const event = writeProgressCheckpoint(directory, job.jobId, { stage, progress, detail });
    onBusy?.(job.jobId, stage, progress, detail);
    try {
      await client.progress(job.jobId, stage, progress, detail, true, {
        ...event, attempt, logs: log.recentLines(PROGRESS_LOG_LINES)
      });
    } catch (error) {
      log.warn(`Could not report ${stage} progress to the CMS: ${error.message}`);
    }
  };

  const existing = findExistingWork(job.jobId);
  let mode = 'fresh';
  let reason = 'no local work for this job id';
  let source = '';
  if (existing) {
    const decision = decideClaim(existing, job);
    mode = decision.decision;
    reason = decision.reason;
    source = decision.source || '';
  }
  log.info(`Claim for ${job.jobId}: ${mode} — ${reason}`);

  if (mode === 'deliver') {
    const scriptCheck = scriptAdapter.validateScript(existing.scriptText, job);
    if (!scriptCheck.ok) {
      log.warn(`Archived narration for ${job.jobId} does not validate against the current job (${scriptCheck.error}); starting a fresh run`);
      mode = 'fresh';
      reason = `archived script failed validation: ${scriptCheck.error}`;
    } else {
      const master = await pickValidatedMaster(existing, job);
      if (master) {
        await report('video', 90, `Delivering the existing master for ${job.jobId} (${reason}) — no re-render`);
        log.info(`Delivering existing master for ${job.jobId} from ${master}`);
        const thumbnailFile = await pickArchivedThumbnail(existing);
        const delivered = await deliverSaved({
          client,
          job,
          attempt,
          signal,
          scriptFile: path.join(existing.directory, 'script.txt'),
          videoFile: master,
          thumbnailFile
        });
        return { ...delivered, decision: 'deliver', source, videoPath: delivered.videoPath };
      }
      log.warn(`Recorded master for ${job.jobId} failed validation; resuming from the saved narration instead of re-rendering from scratch`);
      mode = 'resume';
      reason = 'recorded master failed validation; resuming from the saved narration';
    }
  }

  if (mode === 'resume') {
    let resumeDir = existing.directory;
    if (existing.source === 'archive') {
      resumeDir = restoreArchivedWork(job, existing.directory);
    }
    const savedProgress = readProgressCheckpoint(resumeDir);
    const from = savedProgress
      ? `${savedProgress.stage} ${savedProgress.progress}% — ${savedProgress.detail}`
      : 'the saved narration';
    await report(
      savedProgress?.stage || 'script',
      savedProgress ? savedProgress.progress : 5,
      `Resuming existing work for ${job.jobId} from ${from} — same job, not a fresh run`
    );
    log.info(`Resuming existing work for ${job.jobId} from ${from}`);
    const result = await executeJob({ client, job, attempt, signal, onBusy });
    return { ...result, decision: 'resume', source };
  }

  log.info(`No local work for ${job.jobId}; starting a fresh run`);
  const result = await executeJob({ client, job, attempt, signal, onBusy });
  return { ...result, decision: 'fresh' };
}

async function inspectClaim(job) {
  const existing = findExistingWork(job.jobId);
  const decision = decideClaim(existing, job);
  return { existing, decision };
}

module.exports = {
  executeJob,
  deliverSaved,
  thumbnailsSkipped,
  pickValidatedMaster,
  resumeActiveJob,
  reportExecutionFailure,
  syncSavedProgress,
  resolveClaim,
  inspectClaim,
  findExistingWork,
  decideClaim,
  restoreArchivedWork,
  payloadDigest,
  materialDigest,
  legacyPayloadDigest,
  canonicalPayload,
  prepareWorkDir,
  savedVideo,
  sha256File,
  jobSummary,
  readProgressCheckpoint,
  writeProgressCheckpoint,
  PROGRESS_LOG_LINES
};
