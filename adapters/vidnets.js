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
  const options = job.channel?.videoOptions || {};
  const minutes = Math.max(1, Number(job.targetDurationMinutes) || 8);
  const concurrency = Math.max(1, Math.min(8, Number(process.env.MUSE_RENDER_CONCURRENCY) || 4));
  const coverage = Number(process.env.MUSE_YOUTUBE_COVERAGE_TARGET);
  const coverageTarget = Number.isFinite(coverage) ? Math.max(0.4, Math.min(1, coverage > 1 ? coverage / 100 : coverage)) : 0.6;
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
    channel_profile_id: job.channel?.id || 'muse',
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
    visual_required: true,
    final_visual_gate_mode: 'run',
    media_sources: ['pexels', 'pixabay'],
    pexels_api_key: String(process.env.PEXELS_API_KEY || '').trim(),
    pixabay_api_key: String(process.env.PIXABAY_API_KEY || '').trim(),
    strict_retrieval_accuracy: true,
    fail_on_unresolved_media: true,
    allow_emergency_media: true,
    emergency_visuals_advisory_only: true,
    scene_mode: 'Adaptive',
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

async function runInferenceCommand(command, request, workDir) {
  const input = JSON.stringify({
    type: 'inference',
    request,
    instructions: request.instructions || '',
    responseFormat: request.response_format || {}
  });
  const result = await runCommand(command, {
    input,
    cwd: workDir,
    timeoutMs: Number(process.env.MUSE_INFERENCE_TIMEOUT_MS) || 10 * MINUTE,
    label: 'Muse inference command',
    env: {
      MUSE_INFERENCE_REQUEST_ID: request.id,
      MUSE_INFERENCE_KIND: String(request.kind || 'text'),
      MUSE_INFERENCE_OPERATION: String(request.operation || '')
    }
  });
  const text = String(result.stdout || '').trim();
  if (!text) throw new Error('The Muse inference command returned an empty answer');
  return text;
}

async function runInferenceHttp(url, request) {
  let response;
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ type: 'inference', request, instructions: request.instructions || '', responseFormat: request.response_format || {} }),
      signal: AbortSignal.timeout(Number(process.env.MUSE_INFERENCE_TIMEOUT_MS) || 10 * MINUTE),
      redirect: 'error'
    });
  } catch (error) {
    throw new Error('The Muse inference endpoint could not be reached: ' + error.message);
  }
  const text = await response.text();
  if (!response.ok) throw new Error(`The Muse inference endpoint returned HTTP ${response.status}: ${text.slice(0, 400)}`);
  let data = text;
  try { data = JSON.parse(text); } catch { /* plain text answer */ }
  const content = data && typeof data === 'object'
    ? data.content ?? data.text ?? data.answer
    : data;
  if (content === undefined || content === null || String(content).trim() === '') {
    throw new Error('The Muse inference endpoint returned no content');
  }
  return typeof content === 'string' ? content : JSON.stringify(content);
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

async function render(job, scriptText, workDir, { signal, onProgress } = {}) {
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
  fs.writeFileSync(requestFile, JSON.stringify(buildRequest(job, scriptText, packageDir), null, 2) + '\n', 'utf8');
  const submitted = await vidnets(`submit ${quoteArg(requestFile)}`, { timeoutMs: 2 * MINUTE, signal, label: 'Vidnets generation submit' });
  const vidnetsJobId = String(submitted.item?.id || '').trim();
  if (!vidnetsJobId) throw new Error('Vidnets did not return a queue job id');
  fs.writeFileSync(path.join(workDir, 'vidnets-job.json'), JSON.stringify({
    id: vidnetsJobId,
    packageDir,
    submittedAt: new Date().toISOString()
  }, null, 2) + '\n', 'utf8');
  log.info(`Vidnets job ${vidnetsJobId} submitted for "${job.title}"`);

  const activeInference = new Set();
  let lastProgress = -1;
  let lastDetail = '';
  const deadline = Date.now() + Math.max(10 * MINUTE, Number(process.env.MUSE_RENDER_TIMEOUT_MS) || 6 * 60 * MINUTE);
  const pollMs = Math.max(1000, Number(process.env.MUSE_VIDNETS_POLL_MS) || 4000);

  while (true) {
    if (signal?.aborted) throw new Error('Render cancelled by operator');
    if (Date.now() > deadline) throw new Error('Vidnets render exceeded the configured Muse render budget');

    await serviceInbox(vidnetsJobId, workDir, transport, signal, activeInference).catch(error => {
      if (signal?.aborted) throw error;
      log.warn('Vidnets inbox poll failed; retrying: ' + error.message);
    });

    const snapshot = await vidnets(`job ${vidnetsJobId}`, { timeoutMs: MINUTE, signal, label: 'Vidnets job status' });
    const item = snapshot.item || {};
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
      await serviceInbox(vidnetsJobId, workDir, transport, signal, activeInference).catch(() => {});
      onProgress?.(100, 'Vidnets render complete');
      return { videoPath, vidnetsJobId, packageDir, meta: { engine: 'vidnets', vidnetsJobId } };
    }
    if (['error', 'cancelled', 'shutdown_timeout'].includes(item.status)) {
      const reason = item.error || item.paused_reason || item.progress_text || `status ${item.status}`;
      throw new Error('Vidnets render failed: ' + String(reason).slice(0, 700));
    }
    if (item.status === 'paused') {
      // A paused retained queue can be resumed safely; the saved checkpoint is
      // preserved. Retry once automatically, then report the failure.
      await vidnets(`retry ${vidnetsJobId}`, { timeoutMs: 2 * MINUTE, signal, label: 'Vidnets job resume' });
      log.info(`Resumed saved Vidnets job ${vidnetsJobId}`);
    }

    await sleep(pollMs, signal);
  }
}

module.exports = { render, buildRequest, ensureReady, agentCommand };
