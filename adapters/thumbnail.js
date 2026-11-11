'use strict';

/**
 * Thumbnail for a Muse job.
 *
 * A configured MUSE_THUMBNAIL_COMMAND owns the design. Without one, the worker
 * extracts a representative frame from the finished 1080p master, which is a
 * real image from the video rather than an invented asset.
 */

const fs = require('fs');
const path = require('path');
const { runCommand } = require('../lib/command');
const log = require('../lib/log');
const { registerStageProcess } = require('../lib/stageCleanup');
const ffmpeg = require('../lib/ffmpeg');

function existingThumbnail(workDir) {
  for (const name of ['thumbnail.jpg', 'thumbnail.jpeg', 'thumbnail.png', 'thumbnail.webp']) {
    const file = path.join(workDir, name);
    try {
      if (fs.statSync(file).isFile() && fs.statSync(file).size >= 1024) return file;
    } catch { /* keep looking */ }
  }
  return null;
}

function copyThumbnail(source, workDir) {
  const resolved = path.resolve(String(source || ''));
  if (!fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) {
    throw new Error('The thumbnail command reported a missing file: ' + resolved);
  }
  const extension = ['.jpg', '.jpeg', '.png', '.webp'].includes(path.extname(resolved).toLowerCase())
    ? path.extname(resolved).toLowerCase()
    : '.jpg';
  const destination = path.join(workDir, `thumbnail${extension}`);
  if (path.resolve(destination) !== resolved) fs.copyFileSync(resolved, destination);
  return destination;
}

async function downloadReferenceThumbnail(job, workDir) {
  const thumbReq = job.requirements?.thumbnail || {};
  const candidates = [
    thumbReq.referenceImageUrl,
    job.titlePackage?.refThumbImageUrl,
    job.thumbnailReferences?.[0]?.imageUrl,
    job.thumbnailReferences?.[0]?.fallbackImageUrl
  ].filter(Boolean);

  if (!candidates.length) return null;
  const target = path.join(workDir, 'reference-thumbnail.jpg');
  try {
    if (fs.existsSync(target) && fs.statSync(target).size > 1024) return target;
  } catch { /* proceed */ }

  for (const url of candidates) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(10000) });
      if (!response.ok) continue;
      const buffer = Buffer.from(await response.arrayBuffer());
      if (buffer.length > 1024) {
        fs.writeFileSync(target, buffer);
        log.info(`Downloaded reference thumbnail (${Math.round(buffer.length / 1024)} KB) to ${target}`);
        return target;
      }
    } catch { /* try next candidate */ }
  }
  return null;
}

async function fromCommand(command, job, scriptText, videoPath, workDir, { signal } = {}) {
  const refImage = await downloadReferenceThumbnail(job, workDir);
  const result = await runCommand(command, {
    input: JSON.stringify({
      type: 'thumbnail',
      job,
      scriptText,
      scriptPath: path.join(workDir, 'script.txt'),
      videoPath,
      workDir,
      aspect: job.requirements?.thumbnail?.aspect || '16:9',
      concept: job.thumbnailIdea || job.titlePackage?.thumbnailIdea || '',
      referenceUrl: job.requirements?.thumbnail?.referenceUrl || job.titlePackage?.refThumb || '',
      referenceImageUrl: job.requirements?.thumbnail?.referenceImageUrl || job.titlePackage?.refThumbImageUrl || '',
      referenceThumbnailPath: refImage || ''
    }),
    cwd: workDir,
    signal,
    timeoutMs: Number(process.env.MUSE_THUMBNAIL_TIMEOUT_MS) || 10 * 60 * 1000,
    label: 'Muse thumbnail command',
    onSpawn: spawn => registerStageProcess(job.jobId, 'thumbnail', spawn),
    env: {
      MUSE_JOB_ID: job.jobId,
      MUSE_WORK_DIR: workDir,
      MUSE_STAGE: 'thumbnail',
      MUSE_VIDEO_PATH: videoPath,
      MUSE_THUMBNAIL_CONCEPT: job.thumbnailIdea || job.titlePackage?.thumbnailIdea || '',
      MUSE_THUMBNAIL_REFERENCE: refImage || ''
    }
  });
  const { parseJsonOutput } = require('../lib/command');
  const parsed = parseJsonOutput(result.stdout);
  if (!parsed || typeof parsed !== 'object') throw new Error('Muse thumbnail command returned no JSON result');
  if (parsed.error) throw new Error(String(parsed.error));
  const reported = parsed.thumbnailPath || parsed.thumbnail_path || parsed.imagePath;
  if (!reported) throw new Error('Muse thumbnail command did not report a thumbnailPath');
  return copyThumbnail(reported, workDir);
}

async function generate(job, scriptText, videoPath, workDir, { signal } = {}) {
  const saved = existingThumbnail(workDir);
  if (saved) {
    log.info('Reusing the saved thumbnail');
    return { thumbnailPath: saved, resumed: true };
  }

  const refImage = await downloadReferenceThumbnail(job, workDir);
  const command = String(process.env.MUSE_THUMBNAIL_COMMAND || '').trim();
  let thumbnailPath;
  if (command) {
    thumbnailPath = await fromCommand(command, job, scriptText, videoPath, workDir, { signal });
  } else {
    const output = path.join(workDir, 'thumbnail.jpg');
    await ffmpeg.extractFrame(videoPath, output, { atSeconds: Number(process.env.MUSE_THUMBNAIL_AT_SECONDS) || undefined });
    thumbnailPath = output;
    if (refImage) {
      log.warn(`No MUSE_THUMBNAIL_COMMAND configured; extracted fallback frame from video. Reference thumbnail saved at ${refImage} (Concept: "${job.thumbnailIdea || 'none'}")`);
    }
  }

  const check = await ffmpeg.validateThumbnail(thumbnailPath, '16:9');
  if (!check.ok) throw new Error('Generated thumbnail is not usable: ' + check.error);
  return { thumbnailPath, resumed: false, quality: check };
}

module.exports = { generate, existingThumbnail };
