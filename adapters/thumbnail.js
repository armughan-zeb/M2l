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

async function fromCommand(command, job, scriptText, videoPath, workDir, { signal } = {}) {
  const result = await runCommand(command, {
    input: JSON.stringify({
      type: 'thumbnail',
      job,
      scriptText,
      scriptPath: path.join(workDir, 'script.txt'),
      videoPath,
      workDir,
      aspect: '16:9'
    }),
    cwd: workDir,
    signal,
    timeoutMs: Number(process.env.MUSE_THUMBNAIL_TIMEOUT_MS) || 10 * 60 * 1000,
    label: 'Muse thumbnail command',
    env: {
      MUSE_JOB_ID: job.jobId,
      MUSE_WORK_DIR: workDir,
      MUSE_STAGE: 'thumbnail',
      MUSE_VIDEO_PATH: videoPath
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

  const command = String(process.env.MUSE_THUMBNAIL_COMMAND || '').trim();
  let thumbnailPath;
  if (command) {
    thumbnailPath = await fromCommand(command, job, scriptText, videoPath, workDir, { signal });
  } else {
    const output = path.join(workDir, 'thumbnail.jpg');
    await ffmpeg.extractFrame(videoPath, output, { atSeconds: Number(process.env.MUSE_THUMBNAIL_AT_SECONDS) || undefined });
    thumbnailPath = output;
  }

  const check = await ffmpeg.validateThumbnail(thumbnailPath, '16:9');
  if (!check.ok) throw new Error('Generated thumbnail is not usable: ' + check.error);
  return { thumbnailPath, resumed: false, quality: check };
}

module.exports = { generate, existingThumbnail };
