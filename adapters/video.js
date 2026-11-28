'use strict';

/**
 * Video rendering router for a Muse job.
 *
 * Strictly isolates the two Muse styles:
 *   - documentary (one_minute_documentary) -> adapters/documentary.js
 *   - 2d_stickman (2d-stickman)            -> adapters/stickman.js
 *
 * Also supports an explicit MUSE_RENDER_COMMAND override when configured.
 */

const fs = require('fs');
const path = require('path');
const { runCommand, parseJsonOutput } = require('../lib/command');
const log = require('../lib/log');
const { registerStageProcess } = require('../lib/stageCleanup');
const stickman = require('./stickman');
const documentary = require('./documentary');
const { resolveVideoStyle, ENGINES } = require('../lib/videoStyle');
const { resolveOverrideCommand } = require('../lib/museBridgeInfer');

const WRAPPER_PROGRESS_RE = /^\[render-[a-z0-9_-]+\]\s+(?:\w+)\s+(\d+)%\s*[—-]\s*(.*)$/i;
const WRAPPER_LINE_RE = /^\[render-[a-z0-9_-]+\]\s+(.*)$/i;

function parseRenderProgressLine(line) {
  const text = String(line || '').trim();
  if (!text) return null;
  const progressMatch = text.match(WRAPPER_PROGRESS_RE);
  if (progressMatch) {
    return {
      progress: Math.max(0, Math.min(100, Number(progressMatch[1]) || 0)),
      detail: (progressMatch[2] || 'Rendering').trim().slice(0, 300) || 'Rendering'
    };
  }
  const wrapperMatch = text.match(WRAPPER_LINE_RE);
  if (wrapperMatch) return { progress: null, detail: wrapperMatch[1].trim().slice(0, 300) };
  return null;
}

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

async function runRenderCommand(command, job, scriptText, workDir, { signal, onProgress, attempt } = {}) {
  const input = JSON.stringify({
    type: 'render',
    job,
    attempt,
    scriptText,
    scriptPath: path.join(workDir, 'script.txt'),
    workDir,
    outputPath: path.join(workDir, 'video.mp4'),
    requirements: job.requirements || {}
  });
  let lastRenderProgress = 0;
  const timeoutMs = Math.max(60000, Number(process.env.MUSE_RENDER_TIMEOUT_MS) || 60 * 60 * 1000);
  const result = await runCommand(command, {
    input,
    cwd: workDir,
    signal,
    timeoutMs,
    label: 'Muse render command',
    onStderrLine: line => {
      const parsed = parseRenderProgressLine(line);
      if (!parsed) return;
      log.info('[render] ' + line.trim().slice(0, 500));
      if (parsed.progress === null) {
        onProgress?.(lastRenderProgress, parsed.detail, false);
      } else {
        lastRenderProgress = parsed.progress;
        onProgress?.(parsed.progress, parsed.detail);
      }
    },
    onSpawn: spawn => registerStageProcess(job.jobId, 'render', spawn),
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

function resolveRenderCommand(command, repoRoot = path.resolve(__dirname, '..')) {
  return resolveOverrideCommand(command, { repoRoot, envName: 'MUSE_RENDER_COMMAND' });
}

async function render(job, scriptText, workDir, options = {}) {
  const style = resolveVideoStyle(job);
  log.info(`Resolved video style: ${style.label} (engine=${style.engine}, style=${style.style}, explicit=${style.explicit})`);

  if (style.engine === ENGINES.STICKMAN) {
    return stickman.render(job, scriptText, workDir, options);
  }

  if (style.engine !== ENGINES.DOCUMENTARY) {
    throw new Error(`Unsupported video engine "${style.engine}" for Muse worker; refusing fallback render.`);
  }

  const command = resolveRenderCommand(process.env.MUSE_RENDER_COMMAND);
  if (command) return runRenderCommand(command, job, scriptText, workDir, options);

  return documentary.render(job, scriptText, workDir, options);
}

module.exports = { render, copyIntoWorkDir, parseRenderProgressLine, resolveRenderCommand };
