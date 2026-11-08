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
const { sleep } = require('./sleep');

function sha256File(filePath) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash('sha256');
    const stream = fs.createReadStream(filePath);
    stream.on('data', chunk => hash.update(chunk));
    stream.on('end', () => resolve(hash.digest('hex')));
    stream.on('error', reject);
  });
}

function payloadDigest(job) {
  return crypto.createHash('sha256').update(JSON.stringify(job), 'utf8').digest('hex');
}

function readJson(filePath) {
  try { return JSON.parse(fs.readFileSync(filePath, 'utf8')); }
  catch { return null; }
}

function writeJson(filePath, value) {
  fs.writeFileSync(filePath, JSON.stringify(value, null, 2) + '\n', 'utf8');
}

function prepareWorkDir(job, digest) {
  const directory = config.workDir(job.jobId);
  const marker = path.join(directory, 'job.json');
  const previous = readJson(marker);
  if (previous && previous.digest && previous.digest !== digest) {
    const archiveRoot = path.join(config.WORK_ROOT, 'archive');
    fs.mkdirSync(archiveRoot, { recursive: true });
    const archive = path.join(archiveRoot, `${job.jobId}-${Date.now()}`);
    fs.renameSync(directory, archive);
    fs.mkdirSync(directory, { recursive: true });
    log.warn(`Job ${job.jobId} was re-dispatched with different content; previous worker files archived at ${archive}`);
  }
  writeJson(marker, { digest, job, savedAt: new Date().toISOString() });
  return directory;
}

function savedVideo(directory, digest) {
  const meta = readJson(path.join(directory, 'video-meta.json'));
  const file = path.join(directory, 'video.mp4');
  if (!meta || meta.digest !== digest || !Number.isFinite(Number(meta.bytes))) return null;
  try {
    const stat = fs.statSync(file);
    if (!stat.isFile() || stat.size !== Number(meta.bytes) || stat.size < 1024 * 1024) return null;
    return file;
  } catch {
    return null;
  }
}

function savedScript(directory) {
  return scriptAdapter.readSavedScript(directory);
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

async function executeJob({ client, job, attempt, signal, onBusy }) {
  if (!job || !job.jobId) throw new Error('Muse job payload has no jobId');
  const digest = payloadDigest(job);
  const directory = prepareWorkDir(job, digest);
  config.writeActiveJob({ jobId: job.jobId, payload: job, attempt: Number(attempt) || 1, digest, startedAt: new Date().toISOString() });

  let last = { stage: 'script', progress: 5, detail: 'Muse agent claimed the job' };
  let keepaliveBusy = false;
  let settled = false;
  const report = async (stage, progress, detail, advancing = true) => {
    last = { stage, progress, detail };
    onBusy?.(job.jobId, stage, progress, detail);
    for (let attemptNumber = 0; attemptNumber < 3; attemptNumber += 1) {
      try {
        await client.progress(job.jobId, stage, progress, detail, advancing);
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

  const keepalive = setInterval(() => {
    if (keepaliveBusy) return;
    keepaliveBusy = true;
    // A keepalive never counts as advancement in the CMS. It only proves the
    // worker is alive while a renderer or upload is working without counters.
    client.progress(job.jobId, last.stage, last.progress, last.detail, false)
      .catch(() => {})
      .finally(() => { keepaliveBusy = false; });
  }, Math.max(30000, Number(process.env.MUSE_PROGRESS_KEEPALIVE_MS) || 90000));

  try {
    // ---- narration ----
    let scriptText = savedScript(directory);
    let scriptCheck = scriptText ? scriptAdapter.validateScript(scriptText, job) : null;
    if (!scriptText || !scriptCheck.ok) {
      scriptText = '';
      await report('script', 10, 'Generating narration');
      const script = await scriptAdapter.generateScript(job, directory, {
        signal,
        onDetail: detail => report('script', 30, detail),
        onProgress: (progress, detail) => report('script', progress, detail)
      });
      scriptText = script.scriptText;
      scriptCheck = scriptAdapter.validateScript(scriptText, job);
      if (!scriptCheck.ok) throw new Error(scriptCheck.error);
    }
    await report('script', 100, `Narration ready (${scriptCheck.words} words)`);
    log.info(`Narration ready: ${scriptCheck.words} words`);

    // ---- render ----
    let videoPath = savedVideo(directory, digest);
    let renderMeta = videoPath ? (readJson(path.join(directory, 'video-meta.json')) || {}) : null;
    let renderThumbnail = '';
    if (videoPath) {
      await report('video', 100, 'Reusing the saved master');
      log.info('Reusing the completed master from the previous attempt');
    } else {
      await report('video', 2, 'Starting the 1080p render');
      const rendered = await videoAdapter.render(job, scriptText, directory, {
        signal,
        onProgress: (progress, detail) => report('video', progress, detail)
      });
      videoPath = rendered.videoPath;
      renderThumbnail = rendered.thumbnailPath || '';
      renderMeta = {
        digest,
        engine: rendered.meta?.engine || 'unknown',
        bytes: fs.statSync(videoPath).size,
        sha256: await sha256File(videoPath),
        savedAt: new Date().toISOString()
      };
      writeJson(path.join(directory, 'video-meta.json'), renderMeta);
    }
    await report('video', 96, 'Checking duration, resolution, audio and full decode');
    const masterCheck = await ffmpeg.validateVideoMaster(videoPath, job.targetDurationMinutes);
    if (!masterCheck.ok) throw new Error('Rendered master failed local checks: ' + masterCheck.failures.join('; '));
    await report('video', 100, 'Master validated locally');

    // ---- thumbnail ----
    const thumb = await thumbnailAdapter.generate(job, scriptText, videoPath, directory, { signal });
    const thumbnailPath = thumb.thumbnailPath;
    await report('thumbnail', 100, 'Thumbnail ready');
    log.info('Thumbnail ready');

    // ---- deliver ----
    await report('video', 99, 'Uploading the master');
    const videoUpload = await client.upload(job.jobId, 'video', videoPath);
    log.info(`Master uploaded (${Math.round(Number(videoUpload.artifact?.bytes || 0) / (1024 * 1024))} MB)`);
    await client.upload(job.jobId, 'thumbnail', thumbnailPath);
    log.info('Thumbnail uploaded');

    await client.complete(job.jobId, {
      scriptText,
      wordCount: scriptCheck.words,
      meta: {
        worker: 'muse-agent',
        attempt: Number(attempt) || 1,
        video: {
          bytes: Number(videoUpload.artifact?.bytes) || fs.statSync(videoPath).size,
          sha256: videoUpload.artifact?.sha256 || renderMeta?.sha256 || '',
          engine: renderMeta?.engine || 'unknown'
        },
        thumbnail: { sha256: '' },
        thumbnailSource: thumb.resumed ? 'saved' : String(process.env.MUSE_THUMBNAIL_COMMAND || '').trim() ? 'command' : 'video-frame',
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
    if (settled) config.clearActiveJob();
  }
}

/**
 * Agent-driven delivery: the operating agent already wrote script.txt and
 * rendered the artifacts. Validate, upload, complete.
 */
async function deliverSaved({ client, job, attempt, signal, scriptFile, videoFile, thumbnailFile }) {
  const digest = payloadDigest(job);
  const directory = prepareWorkDir(job, digest);
  config.writeActiveJob({ jobId: job.jobId, payload: job, attempt: Number(attempt) || 1, digest, startedAt: new Date().toISOString() });

  let settled = false;
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

    await client.progress(job.jobId, 'video', 96, 'Agent supplied artifacts; validating the master');
    const masterCheck = await ffmpeg.validateVideoMaster(videoPath, job.targetDurationMinutes);
    if (!masterCheck.ok) throw new Error('Rendered master failed local checks: ' + masterCheck.failures.join('; '));

    let thumbnailPath = thumbnailFile ? path.resolve(thumbnailFile) : '';
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

    writeJson(path.join(directory, 'video-meta.json'), {
      digest,
      engine: 'agent',
      bytes: fs.statSync(videoPath).size,
      sha256: await sha256File(videoPath),
      savedAt: new Date().toISOString()
    });

    await client.progress(job.jobId, 'video', 99, 'Uploading the master');
    const videoUpload = await client.upload(job.jobId, 'video', videoPath);
    const thumbUpload = await client.upload(job.jobId, 'thumbnail', thumbnailPath);
    await client.complete(job.jobId, {
      scriptText,
      wordCount: check.words,
      meta: {
        worker: 'muse-agent',
        attempt: Number(attempt) || 1,
        video: { bytes: Number(videoUpload.artifact?.bytes) || fs.statSync(videoPath).size, sha256: videoUpload.artifact?.sha256 || '', engine: 'agent' },
        thumbnail: { sha256: thumbUpload.artifact?.sha256 || '' },
        thumbnailSource: String(process.env.MUSE_THUMBNAIL_COMMAND || '').trim() ? 'command' : 'video-frame',
        reviewPerformed: false,
        factualAudit: null
      }
    });
    log.info(`Job ${job.jobId} delivered to the CMS (${check.words} words)`);
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
    if (settled) config.clearActiveJob();
  }
}

/**
 * Re-attach to a job saved by a crashed worker. A CMS job that has already
 * finished or failed is detected before any stage runs.
 */
async function resumeActiveJob({ client, signal, onBusy }) {
  const saved = config.readActiveJob();
  if (!saved) return null;
  const job = saved.payload;
  const directory = config.workDir(job.jobId);
  log.info(`Resuming saved Muse job ${job.jobId} (attempt ${saved.attempt || 1})`);
  try {
    await client.progress(job.jobId, 'script', 5, 'Worker reconnected; resuming saved work');
  } catch (error) {
    if (error.status === 409) {
      log.warn('The saved Muse job is no longer active in the CMS; clearing the local resume marker');
      config.clearActiveJob();
      return null;
    }
    if (error.status === 401) throw error;
    throw error;
  }
  if (!fs.existsSync(path.join(directory, 'job.json'))) {
    log.warn('Saved job has no worker checkpoint; clearing the resume marker');
    config.clearActiveJob();
    return null;
  }
  return executeJob({ client, job, attempt: saved.attempt || 1, signal, onBusy });
}

module.exports = {
  executeJob,
  deliverSaved,
  resumeActiveJob,
  payloadDigest,
  prepareWorkDir,
  savedVideo,
  sha256File,
  jobSummary
};
