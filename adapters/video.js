'use strict';

/**
 * Video rendering for a Muse job.
 *
 * Precedence:
 *   1. MUSE_RENDER_COMMAND - the Muse environment's own renderer.
 *   2. Built-in Vidnets documentary renderer.
 */

const fs = require('fs');
const path = require('path');
const { runCommand, parseJsonOutput } = require('../lib/command');
const config = require('../lib/config');
const log = require('../lib/log');
const remotionRenderer = require('../lib/remotionRenderer');
const vidnets = require('./vidnets');

function copyIntoWorkDir(source, workDir, name) {
  const resolved = path.resolve(String(source || ''));
  if (!fs.existsSync(resolved) || !fs.statSync(resolved).isFile() || fs.statSync(resolved).size === 0) {
    throw new Error(`The renderer reported a missing or empty ${name}: ${resolved}`);
  }
  const destination = path.join(workDir, name);
  if (path.resolve(destination) !== resolved) {
    fs.copyFileSync(resolved, destination);
    return destination;
  }
  return resolved;
}

async function runRenderCommand(command, job, scriptText, workDir, { signal, onProgress } = {}) {
  const input = JSON.stringify({
    type: 'render',
    job,
    scriptText,
    scriptPath: path.join(workDir, 'script.txt'),
    workDir,
    outputPath: path.join(workDir, 'video.mp4'),
    requirements: job.requirements || {}
  });
  const result = await runCommand(command, {
    input,
    cwd: workDir,
    signal,
    timeoutMs: Number(process.env.MUSE_RENDER_TIMEOUT_MS) || 6 * 60 * 60 * 1000,
    label: 'Muse render command',
    env: {
      MUSE_JOB_ID: job.jobId,
      MUSE_WORK_DIR: workDir,
      MUSE_STAGE: 'video',
      MUSE_OUTPUT_PATH: path.join(workDir, 'video.mp4')
    }
  });
  const parsed = parseJsonOutput(result.stdout);
  if (!parsed || typeof parsed !== 'object') {
    throw new Error('Muse render command returned no JSON result (expected {"videoPath":"..."})');
  }
  if (parsed.error) throw new Error(String(parsed.error));
  const reported = parsed.videoPath || parsed.video_path;
  if (!reported) throw new Error('Muse render command did not report a videoPath');
  onProgress?.(100, 'Render command finished');
  const videoPath = copyIntoWorkDir(reported, workDir, 'video.mp4');
  let thumbnailPath = '';
  const reportedThumbnail = parsed.thumbnailPath || parsed.thumbnail_path;
  if (reportedThumbnail && fs.existsSync(String(reportedThumbnail))) {
    const extension = path.extname(String(reportedThumbnail)).toLowerCase() || '.jpg';
    thumbnailPath = copyIntoWorkDir(reportedThumbnail, workDir, `thumbnail${extension}`);
  }
  return {
    videoPath,
    thumbnailPath,
    meta: { engine: 'command', ...(parsed.meta && typeof parsed.meta === 'object' ? parsed.meta : {}) }
  };
}

async function render(job, scriptText, workDir, options = {}) {
  const command = String(process.env.MUSE_RENDER_COMMAND || '').trim();
  if (command) return runRenderCommand(command, job, scriptText, workDir, options);

  // If external Vidnets launcher is explicitly configured, use it
  if (process.env.MUSE_VIDNETS_COMMAND || process.env.MUSE_VIDNETS_ROOT) {
    try {
      const result = await vidnets.render(job, scriptText, workDir, options);
      const videoPath = copyIntoWorkDir(result.videoPath, workDir, 'video.mp4');
      log.info(`Master copied into worker job folder (${Math.round(fs.statSync(videoPath).size / (1024 * 1024))} MB)`);
      return { ...result, videoPath };
    } catch (err) {
      log.warn(`External Vidnets service failed: ${err.message}. Using native Remotion documentary engine.`);
    }
  }

  // Pure Remotion Documentary Video Engine (Animated Maps, Word Highlights, Blueprints, Timelines, Stat Cards, SFX)
  return remotionRenderer.render(job, scriptText, workDir, options);
}

module.exports = { render, copyIntoWorkDir };
