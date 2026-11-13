'use strict';

/**
 * Built-in Vidnets documentary renderer.
 *
 * Vidnets runs with llm_provider=external_agent: every writing, director,
 * query and vision decision is answered by the Muse agent through the local
 * inference inbox. This worker never supplies an OpenRouter key to Vidnets.
 */

const fs = require('fs');
const path = require('path');
const config = require('../lib/config');
const log = require('../lib/log');
const { runCommand, parseJsonOutput, quoteArg } = require('../lib/command');
const { minimumVideoDurationSeconds } = require('../lib/mediaRules');
const { sleep } = require('../lib/sleep');
const { assertNoOpenRouterKey } = require('../lib/museInference');
const { readRequestedWorkers } = require('../lib/remotionWorkers');
const { readRenderReceipt, submitRender } = require('../lib/vidnetsSubmission');

const MINUTE = 60 * 1000;

function agentCommand() {
  const explicit = String(process.env.MUSE_VIDNETS_COMMAND || '').trim();
  if (explicit) return explicit;

  const vidnetsRoots = [
    path.join(config.ROOT, 'vidnets'),
    String(process.env.MUSE_VIDNETS_ROOT || '').trim()
  ].filter(Boolean);

  for (const root of vidnetsRoots) {
    if (process.platform !== 'win32') {
      const shLauncher = path.join(root, 'VIDNETS_AGENT.sh');
      if (fs.existsSync(shLauncher)) {
        try { fs.chmodSync(shLauncher, 0o755); } catch {}
        return `bash ${quoteArg(shLauncher)}`;
      }
    } else {
      const cmdLauncher = path.join(root, 'VIDNETS_AGENT.cmd');
      if (fs.existsSync(cmdLauncher)) return quoteArg(cmdLauncher);
    }
    // Direct python execution fallback for headless agent
    const headlessPy = path.join(root, 'server', 'headless_agent.py');
    const resourcesHeadlessPy = path.join(root, 'resources', 'server', 'headless_agent.py');
    const targetScript = fs.existsSync(headlessPy) ? headlessPy : (fs.existsSync(resourcesHeadlessPy) ? resourcesHeadlessPy : null);
    if (targetScript) {
      let pythonBin = process.env.VIDNETS_PYTHON || process.env.PYTHON;
      if (!pythonBin) {
        pythonBin = process.platform === 'win32'
          ? (fs.existsSync(path.join(root, 'tools', 'python', 'python.exe')) ? path.join(root, 'tools', 'python', 'python.exe') : 'python')
          : 'python3';
      }
      return `${quoteArg(pythonBin)} ${quoteArg(targetScript)}`;
    }
  }

  const expectedLauncher = process.platform === 'win32' ? 'VIDNETS_AGENT.cmd' : 'VIDNETS_AGENT.sh';
  throw new Error(
    `The Vidnets launcher was not found. Set MUSE_VIDNETS_COMMAND (for example to a complete Vidnets ${expectedLauncher}), ` +
    'or set MUSE_RENDER_COMMAND to your own renderer.'
  );
}

async function vidnets(args, { timeoutMs, signal, label } = {}) {
  const result = await runCommand(`${agentCommand()} ${args}`, {
    timeoutMs: timeoutMs || 5 * MINUTE,
    signal,
    label: label || `Vidnets ${args.split(/\s+/)[0]}`,
    cwd: config.ROOT
  });
  const parsed = parseJsonOutput(result.stdout);
  if (!parsed) {
    throw new Error(`Vidnets returned no JSON for "${args.split(/\s+/)[0]}"${result.stderr ? ': ' + result.stderr.slice(-600) : ''}`);
  }
  if (parsed.error) throw new Error(parsed.error);
  return parsed;
}

async function ensureReady({ signal } = {}) {
  const started = await vidnets('start', { timeoutMs: 2 * MINUTE, signal, label: 'Vidnets service start' });
  if (!started.ready) throw new Error('Vidnets did not report a ready local service.');
  return started;
}

function buildRequest(job, scriptText, packageDir) {
  // Muse inference is compulsory: an OpenRouter key in the environment
  // must never be handed to Vidnets, even if the server would accept it.
  assertNoOpenRouterKey(process.env);
  const options = job.channel?.videoOptions || {};
  const minutes = Math.max(1, Number(job.targetDurationMinutes) || 8);
  // Parallel Remotion workers: MUSE_RENDER_CONCURRENCY (aliases
  // VIDZEO_REMOTION_WORKERS / MUSE_REMOTION_WORKERS /
  // VIDZEO_REMOTION_CONCURRENCY), default 2 (tested safe on this VM).
  // This is the REQUESTED count; the Vidnets server + render script
  // auto-degrade it from CPU / available memory at render start
  // (vidnets/scripts/render-remotion-manifest.js adaptiveConcurrency).
  const concurrency = readRequestedWorkers(process.env);
  const coverage = Number(process.env.MUSE_YOUTUBE_COVERAGE_TARGET);
  // Use the documented 0.8 production target consistently. An explicit
  // lower value remains available for constrained media experiments.
  const coverageTarget = Number.isFinite(coverage) ? Math.max(0.3, Math.min(1, coverage > 1 ? coverage / 100 : coverage)) : 0.8;
  // FILTER-BEST (Nov 9, 2026): Vidnets' native mixed-media design ranks
  // candidates and keeps the best real media per scene — vision is ADVISORY
  // by default ("an uncertain verdict must not prevent video delivery",
  // app.py _visual_required_for_job). Our wiring used to force
  // strict_retrieval_accuracy + visual_required, which flipped the whole
  // job to hard-required mode: every web/YouTube asset needed a completed
  // verdict before the renderer, and a strict job with ANY unresolved
  // scene failed outright (app.py _repair_unresolved_media) instead of
  // degrading. That is the misuse the user corrected: filter-best, not
  // hard-reject. With both flags false, vision still RUNS (visual_
  // understanding + final_visual_gate_mode) and still RANKS (final_score);
  // only named-entity scenes (strict_identity) still require a live
  // verdict, per scene_requires_visual_verification. The ONE hard rule
  // lives in the render wrapper, not here: never DELIVER a video with 0
  // real-media scenes. MUSE_MEDIA_STRICT=1 restores the old strict flags.
  const strictMedia = String(process.env.MUSE_MEDIA_STRICT || '').trim() === '1';
  const deliveryBudget = Math.max(600, Math.round((Number(process.env.MUSE_RENDER_TIMEOUT_MS) || 6 * 60 * MINUTE) / 1000));

  return {
    title: job.title,
    name: job.title,
    content_title: job.title,
    script: scriptText,
    type: 'long',
    channel_name: job.channel?.name || '',
    niche: job.channel?.niche || '',
    category: job.channel?.niche || '',
    // The local inbox is the only inference path for an external agent job.
    llm_provider: 'external_agent',
    minimum_duration_seconds: minimumVideoDurationSeconds(minutes),
    voice: options.voice || '',
    vo_mode: 'Use Audiogram',
    tts_model: 'audiogram',
    edge_tts_options: { rate: 0, pitch: 0, volume: 0 },
    image_source: 'Mixed Media',
    media_source_priority: ['youtube', 'web', 'stock'],
    media_mode: 'automatic',
    pipeline_v2_mode: 'on',
    youtube_rights_mode: 'all_public',
    youtube_accepted_coverage_target: coverageTarget,
    yt_stop_when_coverage_target_met: true,
    allow_relevant_stock: true,
    allow_stock_last_resort: true,
    stock_last_resort_in_coordinator: true,
    stock_video_only: true,
    visual_understanding: true,
    visual_required: strictMedia,
    final_visual_gate_mode: 'run',
    media_sources: ['pexels', 'pixabay'],
    pexels_api_key: String(process.env.PEXELS_API_KEY || '').trim(),
    pixabay_api_key: String(process.env.PIXABAY_API_KEY || '').trim(),
    // Muse inference is compulsory: never send an OpenRouter key to
    // Vidnets. llm_provider=external_agent is the only provider, and the
    // server must never select Qwen for an external_agent job.
    openrouter_api_key: '',
    strict_retrieval_accuracy: strictMedia,
    fail_on_unresolved_media: true,
    allow_emergency_media: true,
    scene_mode: 'Every Beat',
    captions_enabled: options.captionsEnabled !== false,
    font: 'Montserrat-Bold',
    fontsize: 48,
    text_color: '#FFFFFF',
    highlight_color: '#FFFFFF',
    shadow_on: true,
    outline_size: 3,
    position: options.captionPosition || 'bottom',
    animation: 'Pop',
    caption_mode: 'word',
    music_volume: Math.max(0, Math.min(100, Number(options.musicVolume) || 20)),
    whisper_mode: 'local',
    quality: ['high', 'standard', 'medium', 'fast'].includes(options.quality) ? options.quality : 'standard',
    semantic_render_concurrency: concurrency,
    concurrency,
    continue_render_while_active: true,
    output_package_dir: packageDir,
    delivery_budget_seconds: deliveryBudget,
    delivery_budget_max_cap_seconds: deliveryBudget
  };
}

function requestId(value) {
  const id = String(value || '');
  if (!/^[0-9a-f]{32}$/.test(id)) throw new Error('Vidnets returned an invalid agent request id');
  return id;
}

async function runInferenceCommand(command, request, workDir, { timeoutMs = 120000, signal } = {}) {
  const input = JSON.stringify({
    type: 'inference',
    request,
    instructions: request.instructions || '',
    responseFormat: request.response_format || {}
  });
  const result = await runCommand(command, {
    input,
    cwd: workDir,
    timeoutMs,
    signal,
    label: 'Muse inference command',
    env: {
      MUSE_INFERENCE_REQUEST_ID: request.id,
      MUSE_INFERENCE_KIND: String(request.kind || 'text'),
      MUSE_INFERENCE_OPERATION: String(request.operation || '')
    }
  });
  return parseInferenceAnswer(result.stdout, request);
}

function parseInferenceAnswer(text, request) {
  let data = text;
  try { data = JSON.parse(text); } catch { /* plain text answer */ }
  const structured = Boolean(request.response_format?.type || request.response_format?.json_schema);
  if (data?.error && !structured) throw new Error('The Muse inference transport reported an error');
  const content = structured
    ? data?.content ?? data
    : data?.content ?? data?.text ?? data?.answer ?? data;
  if (content === undefined || content === null || String(content).trim() === '') {
    throw new Error('The Muse inference transport returned no content');
  }
  return typeof content === 'string' ? content : JSON.stringify(content);
}

async function runInferenceHttp(url, request, { timeoutMs = 120000, signal } = {}) {
  let response;
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ type: 'inference', request, instructions: request.instructions || '', responseFormat: request.response_format || {} }),
      signal: signal || AbortSignal.timeout(timeoutMs),
      redirect: 'error'
    });
  } catch (error) {
    throw new Error('The Muse inference endpoint could not be reached: ' + error.message);
  }
  if (!response.ok) throw new Error(`The Muse inference endpoint returned HTTP ${response.status}`);
  return parseInferenceAnswer(await response.text(), request);
}

function inferenceTransport() {
  const command = String(process.env.MUSE_INFERENCE_COMMAND || '').trim();
  if (command) return { kind: 'command', command };
  const url = String(process.env.MUSE_INFERENCE_URL || '').trim();
  if (url) {
    let parsed;
    try { parsed = new URL(url); } catch { throw new Error('MUSE_INFERENCE_URL is not a valid URL'); }
    if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('MUSE_INFERENCE_URL must use http:// or https://');
    return { kind: 'http', url: parsed.toString() };
  }
  return null;
}

async function answerOne(requestMeta, workDir, transport, signal) {
  if (signal?.aborted) throw new Error('Render cancelled by operator');
  const request = await vidnets(`request ${requestId(requestMeta.id)}`, { timeoutMs: MINUTE, signal, label: 'Vidnets agent request read' });
  request.id = requestMeta.id;
  let content;
  if (transport.kind === 'command') content = await runInferenceCommand(transport.command, request, workDir);
  else content = await runInferenceHttp(transport.url, request);

  const answerDir = path.join(workDir, 'inference');
  fs.mkdirSync(answerDir, { recursive: true });
  let parsedJson = null;
  try { parsedJson = JSON.parse(content); } catch { /* text response */ }
  const answerFile = path.join(answerDir, `${request.id}.${parsedJson ? 'json' : 'txt'}`);
  fs.writeFileSync(answerFile, parsedJson ? JSON.stringify(parsedJson) : content, 'utf8');
  await vidnets(`answer ${request.id} ${quoteArg(answerFile)}`, { timeoutMs: MINUTE, signal, label: 'Vidnets agent answer' });
  log.info(`Answered Vidnets ${request.kind || 'text'} request${request.operation ? ` (${request.operation})` : ''}`);
}

async function serviceInbox(vidnetsJobId, workDir, transport, signal, active) {
  if (!transport || !vidnetsJobId) return;
  const pending = await vidnets('pending', { timeoutMs: MINUTE, signal, label: 'Vidnets agent inbox' });
  const rows = (Array.isArray(pending.requests) ? pending.requests : [])
    .filter(row => String(row.job_id || '') === String(vidnetsJobId))
    .filter(row => !active.has(row.id));
  for (const row of rows) active.add(row.id);
  await Promise.all(rows.map(async row => {
    try {
      await answerOne(row, workDir, transport, signal);
    } catch (error) {
      if (signal?.aborted) throw error;
      log.warn(`Vidnets inference request ${String(row.id).slice(0, 8)} failed: ${error.message}`);
    } finally {
      active.delete(row.id);
    }
  }));
}

function resultVideoPath(item) {
  const result = item?.result || {};
  const candidates = [
    result.video_path,
    result.videoPath,
    result.video_path_disk,
    result.video,
    result.output_path,
    item?.video_path
  ];
  return candidates.map(value => String(value || '').trim()).find(value => value && fs.existsSync(value)) || '';
}

async function render(job, scriptText, workDir, { signal, onProgress, attempt } = {}) {
  // Muse inference is compulsory: refuse before any Vidnets call if an
  // OpenRouter key is present in the environment.
  assertNoOpenRouterKey(process.env);
  const transport = inferenceTransport();
  if (!transport) {
    throw new Error(
      'Vidnets needs the Muse agent\'s own reasoning and vision. Set MUSE_INFERENCE_COMMAND (a command that answers one JSON request on stdin) ' +
      'or MUSE_INFERENCE_URL, or set MUSE_RENDER_COMMAND to a renderer that does not need per-scene inference.'
    );
  }

  const packageDir = path.join(workDir, 'vidnets');
  fs.mkdirSync(packageDir, { recursive: true });
  await ensureReady({ signal });
  onProgress?.(2, 'Vidnets service ready');

  const requestFile = path.join(workDir, 'vidnets-request.json');
  const jobFile = path.join(workDir, 'vidnets-job.json');
  let receipt = readRenderReceipt(jobFile, job.jobId);
  const cmsAttempt = Math.max(1, Number(attempt) || 1);
  let vidnetsJobId = receipt?.id;
  const saveReceipt = fields => {
    receipt = { ...receipt, ...fields, id: vidnetsJobId, jobId: job.jobId, packageDir };
    config.writeJsonAtomic(jobFile, receipt);
  };
  if (!vidnetsJobId) {
    vidnetsJobId = await submitRender({
      jobId: job.jobId,
      filePath: requestFile,
      request: buildRequest(job, scriptText, packageDir),
      signal,
      submit: file => vidnets(`submit ${quoteArg(file)}`, { timeoutMs: 2 * MINUTE, signal, label: 'Vidnets generation submit' })
    });
    saveReceipt({ cmsAttempt, submittedAt: new Date().toISOString() });
  }
  log.info(`Following Vidnets job ${vidnetsJobId} for "${job.title}"`);

  const activeInference = new Set();
  let lastProgress = -1;
  let lastDetail = '';
  const deadline = Date.now() + Math.max(10 * MINUTE, Number(process.env.MUSE_RENDER_TIMEOUT_MS) || 6 * 60 * MINUTE);
  const pollMs = Math.max(1000, Number(process.env.MUSE_VIDNETS_POLL_MS) || 4000);
  let statusFailures = 0;

  while (true) {
    if (signal?.aborted) throw new Error('Render cancelled by operator');
    if (Date.now() > deadline) throw new Error('Vidnets render exceeded the configured Muse render budget');

    if (!process.env.MUSE_RESIDENT_INBOX) await serviceInbox(vidnetsJobId, workDir, transport, signal, activeInference).catch(error => {
      if (signal?.aborted) throw error;
      log.warn('Vidnets inbox poll failed; retrying: ' + error.message);
    });

    let item;
    try {
      const snapshot = await vidnets(`job ${vidnetsJobId}`, { timeoutMs: MINUTE, signal, label: 'Vidnets job status' });
      item = snapshot.item;
      if (!item?.status) throw new Error('Vidnets returned no job status');
      statusFailures = 0;
    } catch (error) {
      if (signal?.aborted || /returned (400|401|403|404)\b/i.test(error.message)) throw error;
      statusFailures += 1;
      log.warn(`Vidnets status unavailable for ${vidnetsJobId}: ${error.message}`);
      await sleep(Math.min(30000, pollMs * 2 ** Math.min(statusFailures - 1, 5)), signal);
      continue;
    }
    const progress = Math.max(0, Math.min(100, Number(item.progress) || 0));
    const detail = String(item.progress_text || item.status || '').replace(/\s+/g, ' ').trim().slice(0, 400) || 'Rendering';
    if (progress !== lastProgress || detail !== lastDetail) {
      lastProgress = progress;
      lastDetail = detail;
      onProgress?.(progress, detail);
    }

    if (item.status === 'completed') {
      const videoPath = resultVideoPath(item);
      if (!videoPath) throw new Error('Vidnets completed the job without a readable video_path');
      if (!process.env.MUSE_RESIDENT_INBOX) await serviceInbox(vidnetsJobId, workDir, transport, signal, activeInference).catch(() => {});
      onProgress?.(100, 'Vidnets render complete');
      return { videoPath, vidnetsJobId, packageDir, meta: { engine: 'vidnets', vidnetsJobId } };
    }
    if (['error', 'cancelled', 'shutdown_timeout'].includes(item.status)) {
      if (item.status !== 'cancelled' && cmsAttempt > Number(receipt.cmsAttempt || 1)) {
        await vidnets(`retry ${vidnetsJobId}`, { timeoutMs: 2 * MINUTE, signal, label: 'Vidnets job resume' });
        saveReceipt({ cmsAttempt, pausedRetries: 0 });
        continue;
      }
      const reason = item.error || item.paused_reason || item.progress_text || `status ${item.status}`;
      throw new Error('Vidnets render failed: ' + String(reason).slice(0, 700));
    }
    if (item.status === 'paused') {
      const pausedRetries = Number(receipt.cmsAttempt) === cmsAttempt ? Number(receipt.pausedRetries) || 0 : 0;
      if (pausedRetries >= 3) throw new Error(`Vidnets job ${vidnetsJobId} remained paused after three retries: ${detail}`);
      saveReceipt({ cmsAttempt, pausedRetries: pausedRetries + 1 });
      await vidnets(`retry ${vidnetsJobId}`, { timeoutMs: 2 * MINUTE, signal, label: 'Vidnets job resume' });
      log.info(`Resumed saved Vidnets job ${vidnetsJobId}`);
    }

    await sleep(pollMs, signal);
  }
}

module.exports = { render, buildRequest, ensureReady, agentCommand, inferenceTransport, runInferenceCommand, runInferenceHttp };
