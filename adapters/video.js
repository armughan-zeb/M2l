'use strict';

/**
 * Video rendering for a Muse job.
 *
 * Muse inference is COMPULSORY. Precedence:
 *   1. MUSE_RENDER_COMMAND - the Muse environment's own renderer
 *      (production: render-vidnets-linux.js — Vidnets + Muse agent-bridge
 *      inbox; the start gate in lib/runtimePolicy.js enforces this).
 *   2. Built-in Vidnets documentary renderer (llm_provider=external_agent).
 *
 * There is deliberately NO fallback to the native Remotion engine: it
 * fetches no media, asks no Muse inference, and is what made live jobs
 * graphics-only. If no Muse-inference render path is configured, render()
 * throws and the job fails closed instead of silently degrading.
 */

const fs = require('fs');
const path = require('path');
const { runCommand, parseJsonOutput, quoteArg } = require('../lib/command');
const config = require('../lib/config');
const log = require('../lib/log');
const { registerStageProcess } = require('../lib/stageCleanup');
const vidnets = require('./vidnets');

function resolveRenderCommand(command, root = config.ROOT) {
  // Job processes run in their own work directory. Resolve only our known
  // bundled renderer, leaving custom commands and their arguments untouched.
  return String(command).replace(/^(\s*(?:node|node\.exe)\s+)(?:["'](?:\.\/)?render-vidnets-linux\.js["']|(?:\.\/)?render-vidnets-linux\.js)(?=\s|$)/i,
    (_match, prefix) => prefix + quoteArg(path.join(root, 'render-vidnets-linux.js')));
}

/**
 * Parse one stderr line from render-vidnets-linux.js into live progress.
 *
 * The wrapper polls Vidnets every few seconds and logs
 *   [render-vidnets] Vidnets 45% — Finding media
 * plus operational lines (inbox pending, re-plan submitted, re-attach).
 * Returns { progress, detail } for a poll line, { progress: null, detail }
 * for other wrapper lines (the caller keeps the last known %), or null
 * for lines from any other renderer — those are ignored, so a different
 * MUSE_RENDER_COMMAND keeps working unchanged.
 */
const WRAPPER_PROGRESS_RE = /^\[render-vidnets\]\s+Vidnets\s+(\d+)%\s*[—-]\s*(.*)$/;
const WRAPPER_LINE_RE = /^\[render-vidnets\]\s+(.*)$/;

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
  // Live progress: the wrapper's per-poll Vidnets stage/% lines arrive on
  // stderr while the render runs. Stream them into onProgress so the
  // daemon reports them to the CMS on every poll instead of only
  // re-sending the stale "Starting the render" line from the keepalive.
  // The lines are also written to the daemon log, which is where the CMS
  // progress call's recent-log-lines come from (lib/log.js ring).
  let lastRenderProgress = 0;
  const result = await runCommand(command, {
    input,
    cwd: workDir,
    signal,
    timeoutMs: require('../lib/vidnetsSubmission').renderBudgetMs(job),
    label: 'Muse render command',
    onStderrLine: line => {
      const parsed = parseRenderProgressLine(line);
      if (!parsed) return;
      log.info('[render] ' + line.trim().slice(0, 500));
      if (parsed.progress === null) {
        onProgress?.(lastRenderProgress, parsed.detail);
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

async function render(job, scriptText, workDir, options = {}) {
  const command = String(process.env.MUSE_RENDER_COMMAND || '').trim();
  if (command) return runRenderCommand(resolveRenderCommand(command), job, scriptText, workDir, options);

  // If external Vidnets launcher is explicitly configured, use it. A
  // Vidnets failure is terminal: falling back to the native Remotion
  // engine would render graphics with no media and no Muse inference.
  if (process.env.MUSE_VIDNETS_COMMAND || process.env.MUSE_VIDNETS_ROOT) {
    const result = await vidnets.render(job, scriptText, workDir, options);
    const videoPath = copyIntoWorkDir(result.videoPath, workDir, 'video.mp4');
    log.info(`Master copied into worker job folder (${Math.round(fs.statSync(videoPath).size / (1024 * 1024))} MB)`);
    return { ...result, videoPath };
  }

  throw new Error(
    'No Muse-inference render path is configured for this job. Set MUSE_RENDER_COMMAND ' +
    'to render-vidnets-linux.js (Vidnets + Muse agent-bridge inbox) or configure ' +
    'MUSE_VIDNETS_ROOT with a Muse inference bridge. Refusing to render with the ' +
    'native Remotion engine: it fetches no media and uses no Muse inference.'
  );
}

module.exports = { render, copyIntoWorkDir, parseRenderProgressLine, resolveRenderCommand };
