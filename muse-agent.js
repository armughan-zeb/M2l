#!/usr/bin/env node
'use strict';

/**
 * Vidzeo Muse worker.
 *
 *   node muse-agent.js pair --cms http://HOST:3210 --code MUSE-XXXX-XXXX [--name "Studio GPU"]
 *   node muse-agent.js start
 *   node muse-agent.js claim [--wait 60]
 *   node muse-agent.js deliver --job <id> [--script file] [--video file] [--thumbnail file]
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
const ffmpeg = require('./lib/ffmpeg');
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
  const list = ['script', 'render', 'thumbnail', 'heartbeat'];
  list.push(String(process.env.MUSE_SCRIPT_COMMAND || '').trim() ? 'script-command' : 'agent-written-script');
  list.push(String(process.env.MUSE_RENDER_COMMAND || '').trim() ? 'render-command' : 'vidnets');
  list.push(String(process.env.MUSE_THUMBNAIL_COMMAND || '').trim() ? 'thumbnail-command' : 'video-frame-thumbnail');
  if (String(process.env.MUSE_INFERENCE_COMMAND || process.env.MUSE_INFERENCE_URL || '').trim()) list.push('agent-inference');
  return list;
}

function heartbeatInfo(currentJobId = '') {
  return {
    status: currentJobId ? 'busy' : 'idle',
    currentJobId,
    deviceId: config.deviceId(),
    version: VERSION,
    platform: platform(),
    capabilities: capabilities()
  };
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
      workDir: config.workDir(active.jobId)
    } : null
  };
  if (saved.token) {
    try {
      const client = createClient({ baseUrl: config.cmsUrl(saved), token: saved.token });
      const heartbeat = await client.heartbeat(heartbeatInfo(active?.jobId || ''));
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

  add('Script adapter', String(process.env.MUSE_SCRIPT_COMMAND || '').trim() ? 'pass' : 'warn',
    String(process.env.MUSE_SCRIPT_COMMAND || '').trim() || 'Automated mode needs MUSE_SCRIPT_COMMAND. Agent-driven mode writes script.txt and uses "deliver".');

  const renderCommand = String(process.env.MUSE_RENDER_COMMAND || '').trim();
  add('Render adapter', renderCommand ? 'pass' : 'warn', renderCommand || 'No MUSE_RENDER_COMMAND; the built-in Vidnets path will be used.');
  let vidnetsOk = false;
  if (!renderCommand) {
    try {
      const command = vidnets.agentCommand();
      add('Vidnets launcher', 'pass', command);
      try {
        const ready = await vidnets.ensureReady();
        vidnetsOk = true;
        add('Vidnets service', 'pass', ready.url || 'ready');
      } catch (error) {
        add('Vidnets service', 'fail', error.message);
      }
    } catch (error) {
      add('Vidnets launcher', 'fail', error.message);
    }
    add('Muse inference', String(process.env.MUSE_INFERENCE_COMMAND || process.env.MUSE_INFERENCE_URL || '').trim() ? 'pass' : 'fail',
      String(process.env.MUSE_INFERENCE_COMMAND || process.env.MUSE_INFERENCE_URL || '').trim()
        ? 'Vidnets requests will use the Muse agent'
        : 'Vidnets stalls without MUSE_INFERENCE_COMMAND or MUSE_INFERENCE_URL');
  } else {
    add('Muse inference', 'pass', 'Handled by the render command');
  }

  add('Thumbnail adapter', String(process.env.MUSE_THUMBNAIL_COMMAND || '').trim() ? 'pass' : (media.ffmpeg ? 'pass' : 'fail'),
    String(process.env.MUSE_THUMBNAIL_COMMAND || '').trim() || 'FFmpeg frame extraction from the finished master');

  if (!renderCommand) {
    const missing = ['PEXELS_API_KEY', 'PIXABAY_API_KEY'].filter(key => !String(process.env[key] || '').trim());
    add('Stock provider keys', missing.length === 0 ? 'pass' : 'warn',
      missing.length === 0 ? 'Pexels and Pixabay configured' : `Missing ${missing.join(', ')}; real stock rescue will be limited`);
    add('Vidnets pairing', vidnetsOk ? 'pass' : 'warn', vidnetsOk ? 'Local agent service is ready' : 'Fix the Vidnets service check above');
  }

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
  const workDir = jobRunner.prepareWorkDir(job, digest);
  config.writeActiveJob({ jobId: job.jobId, payload: job, attempt: claim.attempt || 1, digest, startedAt: new Date().toISOString() });
  log.info(`Claimed Muse job ${job.jobId}: ${job.title}`);
  console.log(JSON.stringify({
    ok: true,
    job: {
      ...jobRunner.jobSummary(job),
      attempt: claim.attempt || 1,
      workDir,
      scriptPath: path.join(workDir, 'script.txt'),
      videoPath: path.join(workDir, 'video.mp4'),
      thumbnailPath: path.join(workDir, 'thumbnail.jpg'),
      inferenceDir: path.join(workDir, 'inference')
    },
    instructions: [
      'Research and write the complete narration to scriptPath (plain text).',
      'Render a 1080p master with audio to videoPath, or pass --video to deliver an existing file.',
      'Create a 16:9 thumbnail, or omit it to extract a frame from the finished master.',
      `Then run: node muse-agent.js deliver --job ${job.jobId}`
    ]
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
    thumbnailFile: args.thumbnail ? String(args.thumbnail) : ''
  });
  console.log(JSON.stringify({
    ok: result.ok === true,
    jobId: job.jobId,
    scriptWords: result.scriptWords,
    abandoned: result.abandoned || false,
    message: result.abandoned
      ? 'The CMS no longer considers this job active; saved artifacts were kept.'
      : 'Narration, thumbnail and master delivered. The CMS validates the master before upload.'
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
      const response = await client.heartbeat(heartbeatInfo(current?.jobId || ''));
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
      try {
        await client.fail(current.jobId, 'Muse worker stopped by the operator; saved work is retained; retry the run to resume.');
        config.clearActiveJob();
        log.info(`Reported the operator stop for job ${current.jobId}`);
      } catch (error) {
        log.warn('Could not report the operator stop; the CMS will retain the job state: ' + error.message);
      }
    }
    releaseLock();
    process.exit(code);
  };

  process.on('SIGINT', () => { shutdown(0); });
  process.on('SIGTERM', () => { shutdown(0); });

  log.info(`Muse worker ${VERSION} starting for "${saved.agentName || 'Muse agent'}" at ${config.cmsUrl(saved)}`);
  await heartbeat();
  if (fatal) throw fatal;

  const resumed = await jobRunner.resumeActiveJob({
    client,
    signal: abort.signal,
    onBusy: (jobId, stage, progress, detail) => { current = { jobId, stage, progress, detail }; }
  });
  if (resumed) {
    log.info(`Saved job ${resumed.ok ? 'delivered' : 'settled without delivery'}`);
    current = null;
  }

  timer = setInterval(() => { heartbeat(); }, heartbeatMs);
  timer.unref?.();

  while (!stopping) {
    if (fatal) break;
    let claim;
    try {
      claim = await client.nextJob();
    } catch (error) {
      if (error.status === 401) throw new Error('This worker credential was revoked. Run "node muse-agent.js pair".');
      log.warn('Muse job poll failed; retrying: ' + error.message);
      await sleep(Math.min(30000, pollMs * 4), abort.signal);
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
      const result = await jobRunner.executeJob({
        client,
        job,
        attempt: claim.attempt || 1,
        signal: abort.signal,
        onBusy: (jobId, stage, progress, detail) => { current = { jobId, stage, progress, detail }; }
      });
      if (result.abandoned) log.warn(`Job ${job.jobId} is no longer active in the CMS; saved work kept`);
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
  node muse-agent.js deliver [--job <id>] [--script <file>] [--video <file>] [--thumbnail <file>]
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
