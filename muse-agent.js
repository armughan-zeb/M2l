#!/usr/bin/env node
'use strict';

/**
 * Vidzeo Muse worker.
 *
 *   node muse-agent.js pair --cms http://HOST:3210 --code MUSE-XXXX-XXXX [--name "Studio GPU"]
 *   node muse-agent.js start
 *   node muse-agent.js claim [--wait 60]
 *   node muse-agent.js deliver --job <id> [--script file] [--video file] [--thumbnail file] [--skip-thumbnail] [--allow-all-graphic]
 *   node muse-agent.js fail --job <id> --error "reason"
 *   node muse-agent.js status
 *   node muse-agent.js doctor
 *   node muse-agent.js unpair
 *
 * The worker token is stored only in .muse-agent.json and is never printed.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');

const config = require('./lib/config');
const log = require('./lib/log');
const { createClient, publicStatus } = require('./lib/cms');
const { jsonRequest } = require('./lib/httpClient');
const jobRunner = require('./lib/jobRunner');
const { sleep } = require('./lib/sleep');
const { retryDelayMs, cmsRetryOptions } = require('./lib/cmsRetry');
const ffmpeg = require('./lib/ffmpeg');
const python = require('./lib/python');
const vidnets = require('./adapters/vidnets');

const VERSION = require('./package.json').version;
const MIN_NODE = [18, 17];

function parseArgs(argv) {
  const args = { _: [] };
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith('--')) { args._.push(token); continue; }
    const key = token.slice(2);
    const next = argv[index + 1];
    if (next !== undefined && !String(next).startsWith('--')) { args[key] = next; index += 1; }
    else args[key] = true;
  }
  return args;
}

function platform() {
  return `${process.platform}-${process.arch}`;
}

function capabilities() {
  const list = ['script', 'render', 'thumbnail', 'heartbeat', 'remotion-documentary'];
  list.push(String(process.env.MUSE_SCRIPT_COMMAND || '').trim() ? 'script-command' : 'autonomous-narration-synthesizer');
  list.push(String(process.env.MUSE_RENDER_COMMAND || '').trim() ? 'render-command' : 'remotion-native');
  list.push(String(process.env.MUSE_THUMBNAIL_COMMAND || '').trim() ? 'thumbnail-command' : 'video-frame-thumbnail');
  if (String(process.env.MUSE_INFERENCE_COMMAND || process.env.MUSE_INFERENCE_URL || '').trim()) list.push('agent-inference');
  return list;
}

function heartbeatInfo(currentJobId = '', current = null) {
  // The heartbeat is the second live channel to the CMS (every 20s):
  // besides status/currentJobId it now carries the job's current stage,
  // percent and detail, so the CMS can show live progress even if a
  // progress POST is lost to a transient outage. Unknown fields are
  // ignored by the CMS when it does not know them.
  const info = {
    status: currentJobId ? 'busy' : 'idle',
    currentJobId,
    deviceId: config.deviceId(),
    version: VERSION,
    platform: platform(),
    capabilities: capabilities()
  };
  if (currentJobId && current && typeof current.stage === 'string') {
    info.currentStage = current.stage;
    info.currentProgress = current.progress;
    info.currentDetail = current.detail;
  }
  return info;
}

function requireClient() {
  const saved = config.loadConfig();
  const envToken = String(process.env.MUSE_TOKEN || process.env.MUSE_AGENT_KEY || '').trim();
  const token = envToken || config.requireToken(saved);
  const cmsUrl = config.normalizeBaseUrl(process.env.MUSE_CMS_URL || config.cmsUrl(saved));
  return { saved, token, baseUrl: cmsUrl, client: createClient({ baseUrl: cmsUrl, token }) };
}

function loadJobPayload(jobId) {
  if (jobId) {
    const file = path.join(config.workDir(jobId), 'job.json');
    let saved;
    try { saved = JSON.parse(fs.readFileSync(file, 'utf8')); }
    catch { throw new Error(`No claimed job "${jobId}" was found. Run "node muse-agent.js claim" first.`); }
    if (!saved?.job?.jobId) throw new Error(`The saved job file for "${jobId}" is not usable. Claim the job again.`);
    return { job: saved.job, attempt: 1 };
  }
  const active = config.readActiveJob();
  if (!active) throw new Error('There is no active claimed job. Pass --job <id> or run "node muse-agent.js claim".');
  return { job: active.payload, attempt: active.attempt || 1 };
}

async function pairCommand(args) {
  const baseUrl = config.normalizeBaseUrl(args.cms || process.env.MUSE_CMS_URL || 'http://127.0.0.1:3210');
  const staticToken = String(args.token || process.env.MUSE_TOKEN || process.env.MUSE_AGENT_KEY || '').trim();
  const code = String(args.code || '').trim();
  const deviceId = config.deviceId();
  const agentName = String(args.name || '').trim().slice(0, 80) || 'Muse Worker';

  if (staticToken) {
    if (!staticToken.startsWith('muse_')) throw new Error('Static worker token must start with "muse_"');
    config.saveConfig({
      cmsUrl: baseUrl,
      token: staticToken,
      agentId: '',
      agentName,
      pairedAt: new Date().toISOString(),
      version: VERSION
    });
    const client = createClient({ baseUrl, token: staticToken });
    const heartbeat = await client.heartbeat(heartbeatInfo(''));
    if (heartbeat.agentId) {
      config.saveConfig({ agentId: heartbeat.agentId, agentName: heartbeat.agentName || agentName });
    }
    log.info(`Paired with static token as "${agentName}" at ${baseUrl}`);
    console.log(JSON.stringify({
      ok: true,
      cms: baseUrl,
      mode: 'static_token',
      agent: {
        id: heartbeat.agentId || 'configured',
        name: heartbeat.agentName || agentName,
        deviceId,
        active: heartbeat.active === true
      },
      credentialStored: path.basename(config.CONFIG_FILE),
      next: heartbeat.active
        ? 'node muse-agent.js start'
        : 'Select this worker as Active Worker in Muse agents, then run: node muse-agent.js start'
    }, null, 2));
    return;
  }

  if (!code) throw new Error('Pass pairing code (--code MUSE-XXXX-XXXX) or static worker token (--token muse_...)');
  const claimed = await jsonRequest(baseUrl, '/api/muse/pair/claim', {
    method: 'POST',
    body: {
      code,
      name: agentName,
      deviceId,
      platform: platform(),
      version: VERSION,
      capabilities: capabilities(),
      endpoint: ''
    },
    timeoutMs: 30000
  });
  if (!claimed.token || !claimed.agent) throw new Error('The CMS did not return a worker credential.');
  config.saveConfig({
    cmsUrl: baseUrl,
    token: claimed.token,
    agentId: claimed.agent.id,
    agentName: claimed.agent.name,
    pairedAt: new Date().toISOString(),
    version: VERSION
  });
  const client = createClient({ baseUrl, token: claimed.token });
  const heartbeat = await client.heartbeat(heartbeatInfo(''));
  log.info(`Paired as "${claimed.agent.name}" at ${baseUrl}`);
  console.log(JSON.stringify({
    ok: true,
    cms: baseUrl,
    agent: {
      id: claimed.agent.id,
      name: claimed.agent.name,
      deviceId: claimed.agent.deviceId || deviceId,
      active: heartbeat.active === true
    },
    credentialStored: path.basename(config.CONFIG_FILE),
    next: heartbeat.active
      ? 'node muse-agent.js start'
      : 'Select this worker as Active Worker in Muse agents, then run: node muse-agent.js start'
  }, null, 2));
}

async function statusCommand() {
  const saved = config.loadConfig();
  const active = config.readActiveJob();
  const result = {
    worker: 'vidzeo-muse',
    version: VERSION,
    node: process.versions.node,
    platform: platform(),
    cms: config.cmsUrl(saved),
    paired: Boolean(saved.token),
    agentId: saved.agentId || '',
    agentName: saved.agentName || '',
    deviceId: saved.deviceId || '',
    pairedAt: saved.pairedAt || null,
    workRoot: config.WORK_ROOT,
    capabilities: capabilities(),
    activeJob: active ? {
      jobId: active.jobId,
      title: active.payload?.title || '',
      attempt: active.attempt || 1,
      workDir: config.workDir(active.jobId),
      // Last known stage from the live progress checkpoint (progress.json),
      // so `status` shows where the job really is without asking the CMS.
      lastProgress: jobRunner.readProgressCheckpoint(config.workDir(active.jobId))
    } : null
  };
  if (saved.token) {
    try {
      const client = createClient({ baseUrl: config.cmsUrl(saved), token: saved.token });
      const heartbeat = await client.heartbeat(heartbeatInfo(active?.jobId || '', active ? {
        stage: result.activeJob?.lastProgress?.stage,
        progress: result.activeJob?.lastProgress?.progress,
        detail: result.activeJob?.lastProgress?.detail
      } : null));
      result.cmsMode = heartbeat.mode;
      result.activeWorker = heartbeat.active === true;
      result.connected = true;
    } catch (error) {
      result.connected = false;
      result.cmsError = error.message;
    }
  }
  console.log(JSON.stringify(result, null, 2));
}

function configureCommand(args) {
  const saved = config.loadConfig();
  if (!saved.token) throw new Error('This worker is not paired. Pair it before changing its CMS address.');
  const cmsUrl = config.normalizeBaseUrl(args.cms || '');
  config.saveConfig({ cmsUrl });
  console.log(JSON.stringify({
    ok: true,
    cms: cmsUrl,
    paired: true,
    agentName: saved.agentName || '',
    credentialStored: path.basename(config.CONFIG_FILE),
    next: 'Run "node muse-agent.js status" to verify the connection.'
  }, null, 2));
}

async function doctorCommand() {
  const checks = [];
  const add = (name, level, detail) => checks.push({ name, level, detail });
  const [major, minor] = process.versions.node.split('.').map(Number);
  add('Node.js', major > MIN_NODE[0] || (major === MIN_NODE[0] && minor >= MIN_NODE[1]) ? 'pass' : 'fail',
    `v${process.versions.node} (${MIN_NODE.join('.')}+ required)`);

  const saved = config.loadConfig();
  add('Worker credential', saved.token ? 'pass' : 'fail', saved.token ? 'Stored in .muse-agent.json' : 'Not paired. Run "node muse-agent.js pair".');

  let client = null;
  const cms = config.cmsUrl(saved);
  try {
    await publicStatus(cms);
    add('CMS reachable', 'pass', cms);
  } catch (error) {
    add('CMS reachable', 'fail', error.message);
  }

  if (saved.token) {
    client = createClient({ baseUrl: cms, token: saved.token });
    try {
      const heartbeat = await client.heartbeat(heartbeatInfo(''));
      add('Authenticated heartbeat', 'pass', `Muse mode: ${heartbeat.mode}`);
      add('Active worker', heartbeat.active ? 'pass' : 'warn',
        heartbeat.active ? 'This worker receives Muse jobs' : 'Select this worker as Active Worker in Muse agents');
    } catch (error) {
      add('Authenticated heartbeat', error.status === 401 ? 'fail' : 'warn', error.message);
    }
  }

  try {
    fs.mkdirSync(config.WORK_ROOT, { recursive: true });
    const probe = path.join(config.WORK_ROOT, '.doctor');
    fs.writeFileSync(probe, 'ok', 'utf8');
    fs.unlinkSync(probe);
    add('Work folder', 'pass', config.WORK_ROOT);
  } catch (error) {
    add('Work folder', 'fail', error.message);
  }

  const media = await ffmpeg.availability();
  add('FFprobe', media.ffprobe ? 'pass' : 'fail', media.ffprobe || 'Not found. Set FFPROBE_PATH or MUSE_FFMPEG_DIR.');
  add('FFmpeg', media.ffmpeg ? 'pass' : 'fail', media.ffmpeg || 'Not found. Set FFMPEG_PATH or MUSE_FFMPEG_DIR.');

  const py = await python.availability();
  add('Python 3', py.ok ? 'pass' : 'warn',
    py.ok ? `${py.binary}${py.version ? ' (v' + py.version + ')' : ''}` : 'Not found. Install python3 (e.g. sudo apt install python3) or set VIDNETS_PYTHON.');

  add('Script adapter', 'pass',
    String(process.env.MUSE_SCRIPT_COMMAND || '').trim()
    || 'Default ScriptIT CLI in agent-bridge mode (Muse inference compulsory; template fallback is opt-in only).');

  const renderCommand = String(process.env.MUSE_RENDER_COMMAND || '').trim();
  if (renderCommand) {
    add('Render adapter', 'pass', renderCommand);
  } else {
    add('Render adapter', 'fail',
      'No MUSE_RENDER_COMMAND is set. Production renders must use render-vidnets-linux.js ' +
      '(Vidnets + Muse agent-bridge inbox). The native Remotion engine is not a render path.');
  }

  // Muse inference is COMPULSORY and is verified for real: Vidnets server
  // healthy, agent-bridge inbox writable, vidnets-inbox-watch hook enabled,
  // and no OpenRouter key in the environment. A render command alone proves
  // nothing — an earlier doctor marked inference PASS with "Handled by the
  // render command" without checking any of this.
  {
    const { verifyMuseInferenceBridge } = require('./lib/museInference');
    const inference = await verifyMuseInferenceBridge(process.env);
    add('Muse inference (compulsory)', inference.ok ? 'pass' : 'fail',
      inference.ok
        ? 'Vidnets server healthy; agent-bridge inbox writable; vidnets-inbox-watch hook enabled; no OpenRouter key'
        : inference.failures.join('; '));
  }

  if (!renderCommand) {
    let remotionReady = false;
    try {
      require('@remotion/bundler');
      require('@remotion/renderer');
      remotionReady = true;
    } catch (_) {
      try {
        require(path.join(config.ROOT, 'vidnets', 'node_modules', '@remotion', 'bundler'));
        require(path.join(config.ROOT, 'vidnets', 'node_modules', '@remotion', 'renderer'));
        remotionReady = true;
      } catch (_) {}
    }

    // The native Remotion engine is NOT a render path: it fetches no media
    // and uses no Muse inference. It is listed here only for diagnosis.
    add('Remotion Engine', 'fail',
      remotionReady
        ? 'Native Remotion engine is installed, but it is not a render path — it renders graphics with no media and no Muse inference. Set MUSE_RENDER_COMMAND to render-vidnets-linux.js.'
        : 'Remotion packages not installed. Set MUSE_RENDER_COMMAND to render-vidnets-linux.js.');

    // Built-in Vidnets launcher (only relevant when Vidnets is configured)
    if (process.env.MUSE_VIDNETS_COMMAND || process.env.MUSE_VIDNETS_ROOT) {
      try {
        const command = vidnets.agentCommand();
        add('Vidnets launcher', 'pass', command);
        try {
          const ready = await vidnets.ensureReady();
          add('Vidnets service', 'pass', ready.url || 'ready');
        } catch (error) {
          add('Vidnets service', 'warn', error.message);
        }
      } catch (error) {
        add('Vidnets launcher', 'warn', error.message);
      }
    }
  }

  add('Thumbnail adapter', String(process.env.MUSE_THUMBNAIL_COMMAND || '').trim() ? 'pass' : (media.ffmpeg ? 'pass' : 'fail'),
    String(process.env.MUSE_THUMBNAIL_COMMAND || '').trim() || 'FFmpeg frame extraction from the finished master');

  const missing = ['PEXELS_API_KEY', 'PIXABAY_API_KEY'].filter(key => !String(process.env[key] || '').trim());
  add('Stock provider keys', missing.length === 0 ? 'pass' : 'warn',
    missing.length === 0 ? 'Pexels and Pixabay configured' : `Missing ${missing.join(', ')}; real stock rescue will be limited`);

  console.log(checks.map(check => `${check.level === 'pass' ? 'PASS' : check.level === 'warn' ? 'WARN' : 'FAIL'}  ${check.name}: ${log.redact(check.detail)}`).join('\n'));
  const failures = checks.filter(check => check.level === 'fail').length;
  console.log(`\n${failures ? `${failures} blocking check(s)` : 'All blocking checks passed'}.`);
  if (failures) process.exitCode = 1;
}

async function claimCommand(args) {
  const { client } = requireClient();
  if (config.readActiveJob()) {
    throw new Error('A Muse job is already active on this worker. Deliver or fail it before claiming another.');
  }
  const waitSeconds = Math.max(0, Number(args.wait) || 0);
  const deadline = Date.now() + waitSeconds * 1000;
  let claim;
  while (true) {
    claim = await client.nextJob();
    if (claim.job || Date.now() >= deadline) break;
    await sleep(Math.max(1000, Number(process.env.MUSE_POLL_MS) || 3000));
  }
  if (!claim.job) {
    console.log(JSON.stringify({ ok: true, job: null, reason: claim.reason || 'idle', message: claim.message || '' }, null, 2));
    return;
  }

  const job = claim.job;
  const digest = jobRunner.payloadDigest(job);
  // RESUME RULE (Nov 10, 2026): same job ID = same job. Before printing
  // fresh-run instructions, check whether this job already has local
  // work — a finished master is delivered, mid-run work is resumed.
  const inspected = await jobRunner.inspectClaim(job);
  const decision = inspected.decision;
  const existing = inspected.existing;
  const workDir = jobRunner.prepareWorkDir(job, digest);
  config.writeActiveJob({ jobId: job.jobId, payload: job, attempt: claim.attempt || 1, digest, startedAt: new Date().toISOString() });
  log.info(`Claimed Muse job ${job.jobId}: ${job.title} — ${decision.decision} (${decision.reason})`);
  const summary = jobRunner.jobSummary(job);
  const scriptPath = path.join(workDir, 'script.txt');
  const videoPath = path.join(workDir, 'video.mp4');
  const thumbnailPath = path.join(workDir, 'thumbnail.jpg');

  // The instructions follow the claim decision, not a fresh run: when an
  // existing master is on disk the operator delivers it; when mid-run
  // work exists the daemon re-attaches to it. Only a job with no local
  // work gets the script/render/deliver steps.
  let instructions;
  if (decision.decision === 'deliver' && existing) {
    const master = existing.masterPath || existing.vidnetsMasterPath || (existing.vidnetsMp4s && existing.vidnetsMp4s[0]) || videoPath;
    const deliverParts = [`node muse-agent.js deliver --job ${job.jobId}`, `--script ${path.join(existing.directory, 'script.txt')}`, `--video ${master}`];
    // Thumbnails are skipped by this worker (VIDZEO_SKIP_THUMBNAILS=1):
    // never ask for one, and pass --skip-thumbnail so deliver does not
    // generate or upload one.
    if (jobRunner.thumbnailsSkipped()) deliverParts.push('--skip-thumbnail');
    else if (existing.thumbnailPath) deliverParts.push(`--thumbnail ${existing.thumbnailPath}`);
    instructions = [
      `EXISTING WORK FOUND (${decision.reason}). Do NOT regenerate the script or re-render the video.`,
      `DELIVER: ${deliverParts.join(' ')}`
    ];
  } else if (decision.decision === 'resume') {
    instructions = [
      `EXISTING WORK FOUND (${decision.reason}). Do NOT start from the script stage.`,
      `RESUME: run "node muse-agent.js start" — the daemon re-attaches to the saved work and continues it.`
    ];
  } else {
    const steps = [
      `1. SCRIPT: Write complete source-grounded narration matching channel style to scriptPath (${scriptPath}). Target: ~${summary.targetWordCount} words (at least ${summary.minimumWords} words required for ${summary.targetDurationMinutes}min duration).`,
      `2. VIDEO: Render a 1080p master (1920x1080) with audio to videoPath (${videoPath}).`,
      `3. DELIVER: Once the script and master exist in workDir, run: node muse-agent.js deliver --job ${job.jobId} --skip-thumbnail (thumbnails are generated locally by Pikgen; do not generate or upload a thumbnail)`
    ];
    instructions = steps;
  }

  console.log(JSON.stringify({
    ok: true,
    job: {
      ...summary,
      attempt: claim.attempt || 1,
      workDir,
      scriptPath,
      videoPath,
      thumbnailPath,
      inferenceDir: path.join(workDir, 'inference'),
      claimDecision: decision.decision,
      claimReason: decision.reason,
      existingWorkDir: existing ? existing.directory : null,
      existingWorkSource: existing ? existing.source : null
    },
    instructions
  }, null, 2));
}

async function deliverCommand(args) {
  const { client } = requireClient();
  const { job, attempt } = loadJobPayload(args.job ? String(args.job) : '');
  const result = await jobRunner.deliverSaved({
    client,
    job,
    attempt,
    scriptFile: args.script ? String(args.script) : '',
    videoFile: args.video ? String(args.video) : '',
    thumbnailFile: args.thumbnail ? String(args.thumbnail) : '',
    // Refusing an all-graphic master is the default (BNSF incident, Nov 10,
    // 2026). Only an explicit --allow-all-graphic ships blank cards.
    allowAllGraphic: args['allow-all-graphic'] === true,
    // Thumbnails are skipped by this worker (VIDZEO_SKIP_THUMBNAILS=1 in
    // the Docker package). --skip-thumbnail forces the skip even when the
    // env var is unset; without either, a --thumbnail file is used.
    skipThumbnail: args['skip-thumbnail'] === true
  });
  console.log(JSON.stringify({
    ok: result.ok === true,
    jobId: job.jobId,
    scriptWords: result.scriptWords,
    abandoned: result.abandoned || false,
    message: result.abandoned
      ? 'The CMS no longer considers this job active; saved artifacts were kept.'
      : result.thumbnailPath
        ? 'Narration, thumbnail and master delivered. The CMS validates the master before upload.'
        : 'Narration and master delivered (thumbnail skipped). The CMS validates the master before upload.'
  }, null, 2));
}

async function failCommand(args) {
  const { client } = requireClient();
  let job;
  try {
    ({ job } = loadJobPayload(args.job ? String(args.job) : ''));
  } catch (error) {
    // The credential and the claimed job file can diverge after an unpair or a
    // manual cleanup. The CMS only needs the job id to settle the failure.
    if (!args.job) throw error;
    job = { jobId: config.safeJobId(String(args.job)) };
  }
  const reason = String(args.error || args.reason || '').trim();
  if (!reason) throw new Error('Pass --error "what failed" so the CMS records an actionable reason.');
  await client.fail(job.jobId, reason);
  config.clearActiveJob();
  log.info(`Reported failure for ${job.jobId}`);
  console.log(JSON.stringify({ ok: true, jobId: job.jobId, error: log.redact(reason) }, null, 2));
}

function alive(pid) {
  try { process.kill(pid, 0); return true; }
  catch (error) { return error.code === 'EPERM'; }
}

function acquireLock() {
  fs.mkdirSync(config.WORK_ROOT, { recursive: true });
  const file = path.join(config.WORK_ROOT, 'worker.lock');
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const descriptor = fs.openSync(file, 'wx', 0o600);
      fs.writeFileSync(descriptor, JSON.stringify({ pid: process.pid, hostname: os.hostname(), startedAt: new Date().toISOString() }));
      fs.closeSync(descriptor);
      return () => {
        try {
          const current = JSON.parse(fs.readFileSync(file, 'utf8'));
          if (current.pid === process.pid) fs.unlinkSync(file);
        } catch { /* already removed */ }
      };
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
      let holder = null;
      try { holder = JSON.parse(fs.readFileSync(file, 'utf8')); } catch { /* stale or partial */ }
      if (holder?.pid && holder.hostname === os.hostname() && alive(holder.pid)) {
        throw new Error(`Another Muse worker is already running on this machine (pid ${holder.pid}).`);
      }
      try { fs.unlinkSync(file); } catch { /* retry */ }
    }
  }
  throw new Error('Could not acquire the Muse worker lock.');
}

async function startCommand(args) {
  if (args.auto === true || args.autonomous === true || args['auto-loop'] === true) {
    process.env.MUSE_AUTO_LOOP = '1';
  }
  const blockers = require('./lib/runtimePolicy').automatedRuntimeBlockers();
  if (blockers.length) {
    throw new Error('Automated Muse worker was not started, so it cannot claim and fail production jobs. ' + blockers.join(' '));
  }
  const { saved, client } = requireClient();
  const releaseLock = acquireLock();
  const abort = new AbortController();
  let current = null;
  let stopping = false;
  let fatal = null;
  const once = args.once === true;
  const heartbeatMs = Math.max(10000, Number(process.env.MUSE_HEARTBEAT_MS) || 20000);
  const pollMs = Math.max(1000, Number(process.env.MUSE_POLL_MS) || 3000);
  let heartbeatBusy = false;
  let timer = null;

  const heartbeat = async () => {
    if (heartbeatBusy || stopping) return;
    heartbeatBusy = true;
    try {
      // `current` is kept fresh by executeJob's onBusy on every report(),
      // so the heartbeat carries the live stage/%/detail to the CMS.
      const response = await client.heartbeat(heartbeatInfo(current?.jobId || '', current));
      if (response.active === false) {
        log.warn('This worker is paired but not the Active Worker. Select it in Muse agents to receive jobs.');
      }
    } catch (error) {
      if (error.status === 401) {
        fatal = new Error('This worker credential was revoked. Run "node muse-agent.js pair" with a fresh code.');
        abort.abort();
      } else {
        log.warn('Heartbeat failed; the worker keeps its saved state: ' + error.message);
      }
    } finally {
      heartbeatBusy = false;
    }
  };

  const shutdown = async (code = 0) => {
    if (stopping) return;
    stopping = true;
    clearInterval(timer);
    abort.abort();
    if (current?.jobId) {
      // Never fail the job or clear the resume marker on shutdown: a SIGTERM
      // from a VM reboot (or an operator restart) must leave the job
      // resumable — the next start re-attaches to its checkpoints and
      // continues. Failing here used to destroy the resume on every reboot.
      log.info(`Worker stopping with job ${current.jobId} in flight; saved work retained for resume`);
    }
    releaseLock();
    process.exit(code);
  };

  process.on('SIGINT', () => { shutdown(0); });
  process.on('SIGTERM', () => { shutdown(0); });

  log.info(`Muse worker ${VERSION} starting for "${saved.agentName || 'Muse agent'}" at ${config.cmsUrl(saved)}`);
  await heartbeat();
  if (fatal) throw fatal;

  // A deferred resume means the CMS was unreachable while probing the
  // saved job: the marker is kept and the poll loop below re-attempts
  // the resume (single probe per iteration, backing off) instead of
  // claiming a new job or exiting. See lib/jobRunner.js resumeActiveJob.
  // The initial probe is guarded like the loop's: only a 401 is fatal —
  // any other throw defers instead of killing the daemon on boot.
  let resumed = null;
  try {
    resumed = await jobRunner.resumeActiveJob({
      client,
      signal: abort.signal,
      onBusy: (jobId, stage, progress, detail) => { current = { jobId, stage, progress, detail }; }
    });
  } catch (error) {
    if (error.status === 401) {
      fatal = new Error('This worker credential was revoked. Run "node muse-agent.js pair".');
      throw fatal;
    }
    const savedJob = config.readActiveJob();
    log.warn(`Saved-job resume probe failed at startup (${error.message}); daemon keeps polling and will resume it when the CMS answers`);
    resumed = { deferred: true, jobId: savedJob?.jobId };
  }
  if (resumed?.deferred) {
    log.warn(`Resume of saved job ${resumed.jobId} deferred (CMS unavailable); daemon keeps polling and will resume it when the CMS answers`);
  } else if (resumed) {
    log.info(`Saved job ${resumed.ok ? 'delivered' : 'settled without delivery'}`);
    current = null;
  }

  timer = setInterval(() => { heartbeat(); }, heartbeatMs);
  timer.unref?.();

  // Exponential backoff state for the two polling paths. Both reset on
  // success, so a brief blip costs seconds and a hours-long outage
  // settles at one probe a minute instead of hammering a down CMS.
  const retryOptions = cmsRetryOptions();
  let pollFailures = 0;
  let resumeFailures = 0;

  while (!stopping) {
    if (fatal) break;

    // Never claim a new job while a saved job is still owed a resume:
    // the previous run may have failed to report its failure to a down
    // CMS, so the marker is still there and the job must be re-attached
    // (executeJob reuses its checkpoints) before anything new is claimed.
    if (config.readActiveJob()) {
      let retry = null;
      try {
        retry = await jobRunner.resumeActiveJob({
          client,
          signal: abort.signal,
          maxAttempts: 1,
          onBusy: (jobId, stage, progress, detail) => { current = { jobId, stage, progress, detail }; }
        });
      } catch (error) {
        if (error.status === 401) {
          fatal = new Error('This worker credential was revoked. Run "node muse-agent.js pair".');
          abort.abort();
          break;
        }
        resumeFailures += 1;
        const delay = retryDelayMs(resumeFailures - 1, { baseMs: pollMs, maxMs: retryOptions.maxMs });
        log.warn(`Saved-job resume probe failed (attempt ${resumeFailures}, ${error.message}); retrying in ${Math.round(delay / 1000)}s — saved work retained`);
        await sleep(delay, abort.signal);
        continue;
      }
      if (retry?.deferred) {
        resumeFailures += 1;
        const delay = retryDelayMs(resumeFailures - 1, { baseMs: pollMs, maxMs: retryOptions.maxMs });
        log.warn(`CMS still unavailable for saved job ${retry.jobId}; resume deferred, re-probing in ${Math.round(delay / 1000)}s — saved work retained`);
        await sleep(delay, abort.signal);
        continue;
      }
      resumeFailures = 0;
      if (retry) {
        log.info(`Saved job ${retry.ok ? 'delivered' : 'settled without delivery'}`);
        current = null;
        continue;
      }
      // retry === null: the marker was cleared (409 / no checkpoint) —
      // fall through to normal queue polling below.
    }

    let claim;
    try {
      claim = await client.nextJob();
      pollFailures = 0;
    } catch (error) {
      if (error.status === 401) throw new Error('This worker credential was revoked. Run "node muse-agent.js pair".');
      pollFailures += 1;
      const delay = retryDelayMs(pollFailures - 1, { baseMs: pollMs, maxMs: retryOptions.maxMs });
      log.warn(`Muse job poll failed (attempt ${pollFailures}, ${error.message}); retrying in ${Math.round(delay / 1000)}s`);
      await sleep(delay, abort.signal);
      continue;
    }

    if (!claim.job) {
      if (once) break;
      if (claim.reason === 'not_active' && claim.message) log.warn(claim.message);
      await sleep(pollMs, abort.signal);
      continue;
    }

    const job = claim.job;
    current = { jobId: job.jobId };
    log.info(`Claimed Muse job ${job.jobId}: ${job.title}`);
    try {
      // RESUME RULE (Nov 10, 2026 — BNSF incident): same job ID = same
      // job. A claim is never a fresh run by default: resolveClaim first
      // looks up the existing local work for this job id and delivers or
      // resumes it. Only a job with no local work at all starts fresh.
      const result = await jobRunner.resolveClaim({
        client,
        job,
        attempt: claim.attempt || 1,
        signal: abort.signal,
        onBusy: (jobId, stage, progress, detail) => { current = { jobId, stage, progress, detail }; }
      });
      if (result.decision === 'busy') {
        log.warn(`Job ${job.jobId} not started: ${result.activeJobId} is still active on this worker — one video at a time`);
      } else if (result.abandoned) log.warn(`Job ${job.jobId} is no longer active in the CMS; saved work kept`);
    } catch (error) {
      if (error.status === 401) {
        fatal = new Error('This worker credential was revoked. Run "node muse-agent.js pair".');
        abort.abort();
        break;
      }
      if (abort.signal.aborted) break;
      log.error(`Job ${job.jobId} failed: ${error.message}`);
      try {
        await client.fail(job.jobId, error.message);
        config.clearActiveJob();
      } catch (failError) {
        log.warn('Could not report the job failure; saved work is retained: ' + failError.message);
      }
    } finally {
      current = null;
    }
    if (once) break;
  }

  if (fatal) {
    releaseLock();
    throw fatal;
  }
  await shutdown(0);
}

async function unpairCommand() {
  const saved = config.loadConfig();
  config.clearConfig();
  config.clearActiveJob();
  console.log(JSON.stringify({
    ok: true,
    removedCredential: Boolean(saved.token),
    message: 'The local worker credential was removed. Saved work folders were kept.'
  }, null, 2));
}

function usage() {
  console.log(`Vidzeo Muse worker ${VERSION}

Usage:
  node muse-agent.js pair --cms <url> --code <MUSE-XXXX-XXXX> [--name "Studio GPU"]
  node muse-agent.js configure --cms <url>
  node muse-agent.js start [--once]
  node muse-agent.js claim [--wait 60]
  node muse-agent.js deliver [--job <id>] [--script <file>] [--video <file>] [--thumbnail <file>] [--skip-thumbnail] [--allow-all-graphic]
  node muse-agent.js fail --job <id> --error "reason"
  node muse-agent.js status
  node muse-agent.js doctor
  node muse-agent.js unpair

Environment (see .env.example):
  MUSE_SCRIPT_COMMAND     Muse agent command that writes narration
  MUSE_RENDER_COMMAND     Muse renderer command
  MUSE_THUMBNAIL_COMMAND  Muse thumbnail command
  MUSE_INFERENCE_COMMAND  Muse agent command that answers Vidnets reasoning/vision requests
  MUSE_INFERENCE_URL      HTTP equivalent of the inference command

No provider API key is stored in this repository.`);
}

async function main() {
  const argv = process.argv.slice(2);
  const args = parseArgs(argv);
  const command = args._[0] || (args.help ? 'help' : '');

  switch (command) {
    case 'pair': await pairCommand(args); break;
    case 'configure': configureCommand(args); break;
    case 'start': await startCommand(args); break;
    case 'status': await statusCommand(); break;
    case 'doctor': await doctorCommand(); break;
    case 'claim': await claimCommand(args); break;
    case 'deliver': await deliverCommand(args); break;
    case 'fail': await failCommand(args); break;
    case 'unpair': await unpairCommand(); break;
    case 'help':
    case '--help':
    case '-h':
      usage();
      break;
    default:
      usage();
      if (command) process.exitCode = 1;
  }
}

main().catch(error => {
  log.error(error.message);
  if (String(process.env.MUSE_LOG_LEVEL).toLowerCase() === 'debug' && error.stack) console.error(log.redact(error.stack));
  process.exit(1);
});
