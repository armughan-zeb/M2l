#!/usr/bin/env node
'use strict';

/**
 * render-vidnets-linux.js — MUSE_RENDER_COMMAND wrapper (Linux hybrid path).
 *
 * The CMS daemon stays active and Vidnets does the rendering, but every
 * reasoning / scene / query / vision decision is answered by a Muse operator
 * through the Vidnets agent-bridge inbox (see the vidnets-inbox-watch hook).
 *
 * This wrapper deliberately does NOT use adapters/vidnets.js serviceInbox:
 * that path would call MUSE_INFERENCE_COMMAND for every pending request
 * while the hook worker is already answering the same requests directly
 * via `VIDNETS_AGENT.sh answer` — the second answer would fail with
 * "already answered". The pattern used here is the one proven by test jobs
 * 0bb3f900 and ff377408: submit via the Vidnets launcher, poll job status,
 * and let the hook worker answer the inbox. This wrapper only submits,
 * polls, and validates.
 *
 * Contract (adapters/video.js runRenderCommand):
 *   stdin:  JSON { job, scriptText, scriptPath, workDir, outputPath }
 *   stdout: exactly one JSON object {"videoPath","thumbnailPath","meta"}
 *   logs go to stderr only.
 *
 * Failure policy — fail loudly, never substitute graphics:
 *   - Vidnets job ends error / cancelled / shutdown_timeout -> throw.
 *   - Vidnets job stays paused after 3 retries -> throw.
 *   - No video file, or video < 1 MB -> throw.
 *   - The finished edit plan has 0 real-media scenes (all graphic
 *     fallbacks) -> throw instead of delivering a graphics-only video.
 *   - No fallback to the native Remotion engine or the solid-colour
 *     ffmpeg placeholder: those are what made live jobs graphics-only.
 *
 * Resume: if workDir/vidnets-job.json already holds a Vidnets job id for
 * this CMS job, re-attach to it instead of submitting a duplicate.
 */

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const { checkMuseInferenceBridge } = require('./lib/museInference');
const { writeJsonAtomic } = require('./lib/config');
const { readRenderReceipt, submitRender } = require('./lib/vidnetsSubmission');

const VIDNETS_ROOT = String(process.env.MUSE_VIDNETS_ROOT || '').trim()
  || path.join(__dirname, 'vidnets');
const RUNTIME_ROOT = String(process.env.VIDZEO_RUNTIME_ROOT || '').trim()
  || path.join(process.env.HOME || '/home/hatch', 'workspace', 'vidnets-runtime');
const LAUNCHER = path.join(VIDNETS_ROOT, 'VIDNETS_AGENT.sh');

const MINUTE = 60 * 1000;

// A media re-plan only runs when this much render budget remains: a fresh
// submit needs time to plan, download and render, and the overall deadline
// is never extended for it.
const REPLAN_MIN_REMAINING_MS = 10 * MINUTE;

/**
 * Pure decision: may the wrapper spend its one bounded media re-plan?
 * Only a finished plan with 0 Muse-verified real-media scenes earns one,
 * only once per wrapper run, and only when enough render budget remains.
 * Anything else (a broken render, a missing edit plan, a too-small video)
 * fails immediately instead — retrying those would just repeat the failure.
 */
function shouldAttemptReplan({ real, replanAttempted, msRemaining, minRemainingMs = REPLAN_MIN_REMAINING_MS }) {
  if (replanAttempted) return false;
  if (real !== 0) return false;
  return Number(msRemaining) >= Number(minRemainingMs);
}

/**
 * Fresh-query retry request. No server-side re-plan endpoint exists
 * (`retry` only resumes a paused job), so a re-plan is a fresh submit of
 * the same job. `yt_plan_cache_ttl_seconds: 0` bypasses the server's 24h
 * shared scene-plan cache (vidnets/server/app.py `_load_shared_plan_cache`),
 * so the retry gets fresh scene queries/direction instead of replaying the
 * 0-real plan; already-downloaded verified cuts may still be reused from
 * disk, which is fine — the retry's value is fresh planning and fresh
 * vision verdicts, not fresh downloads. `muse_replan_attempt` marks the
 * request for provenance; it is a plain key, never an internal `_` key.
 */
function buildRetryRequest(request) {
  return {
    ...request,
    yt_plan_cache_ttl_seconds: 0,
    muse_replan_attempt: 1,
  };
}

/**
 * Pure check: did a previous wrapper run already spend this job's one
 * bounded media re-plan? The saved job file records it (replanAttempted: 1)
 * when the re-plan is submitted. A resume reads the file, so the re-plan
 * is per JOB, not per wrapper run — a daemon restart after a re-plan can
 * never earn a second one.
 */
function replanAlreadyUsed(saved) {
  return Boolean(saved) && (saved.replanAttempted === 1 || saved.replanAttempted === true);
}

// /tmp is a 512 MB tmpfs on this VM and it has filled up before (stale
// scratch blocked bundle verification); Vidnets temp lives on disk under
// VIDZEO_TEMP_DIR/TMPDIR. A render that starts with no temp headroom fails
// halfway through with a cryptic error, so check both up front.
const TEMP_DIR = String(process.env.VIDZEO_TEMP_DIR || process.env.TMPDIR || '').trim()
  || path.join(RUNTIME_ROOT, 'temp');
const TEMP_MIN_FREE_MB = 512; // runtime temp: a 1080p master + intermediates
const TMP_MIN_FREE_MB = 128; // /tmp tmpfs: Chrome/Remotion spillover

/** Free megabytes of the filesystem holding `dir` (0 when it cannot be read). */
function freeMb(dir) {
  try {
    const stats = fs.statfsSync(dir);
    return Math.floor((stats.bavail * stats.bsize) / (1024 * 1024));
  } catch {
    return 0;
  }
}

/**
 * Fail loudly when temp space is too low to render, instead of starting a
 * job that dies halfway. Returns { tempDir, tempFreeMb, tmpFreeMb }.
 * Throws when a directory is missing/unreadable or below its floor.
 * `overrides` exists for tests ({ tempDir, tmpDir, tempMinMb, tmpMinMb }).
 */
function checkTempSpace(overrides = {}) {
  const tempDir = overrides.tempDir || TEMP_DIR;
  const tmpDir = overrides.tmpDir || '/tmp';
  const tempMinMb = overrides.tempMinMb ?? TEMP_MIN_FREE_MB;
  const tmpMinMb = overrides.tmpMinMb ?? TMP_MIN_FREE_MB;
  if (!fs.existsSync(tempDir)) {
    throw new Error(`Vidnets temp dir is missing: ${tempDir} (set VIDZEO_TEMP_DIR); refusing to render`);
  }
  const tempFreeMb = freeMb(tempDir);
  const tmpFreeMb = freeMb(tmpDir);
  if (tempFreeMb < tempMinMb) {
    throw new Error(
      `Temp space too low to render: ${tempDir} has ${tempFreeMb} MB free, need at least ${tempMinMb} MB. `
      + 'Free stale scratch under the Vidnets temp dir (old remotion-*/puppeteer_*/muse-plan-* dirs, edge_*.mp3) and retry.'
    );
  }
  if (tmpFreeMb < tmpMinMb) {
    throw new Error(
      `Temp space too low to render: /tmp has ${tmpFreeMb} MB free, need at least ${tmpMinMb} MB. `
      + 'Free stale scratch in /tmp and retry.'
    );
  }
  return { tempDir, tempFreeMb, tmpDir, tmpFreeMb };
}

/**
 * The wrapper-side live checkpoint: the Vidnets job's current status, %
 * and stage text, rewritten atomically on every poll change. The daemon
 * reads the Vidnets server for the same data while it is alive — this
 * file is for when it is not: a daemon killed mid-render (VM reboot)
 * leaves an accurate pointer on disk, and a resumed wrapper run can
 * report where the Vidnets job really is without waiting for the next
 * poll. Never throws: a checkpoint write must not fail a render.
 */
function writeVidnetsProgressFile(workDir, snapshot) {
  try {
    writeJsonAtomic(path.join(workDir, 'vidnets-progress.json'), {
      vidnetsJobId: snapshot.vidnetsJobId || '',
      cmsJobId: snapshot.cmsJobId || '',
      status: snapshot.status || '',
      progress: Math.max(0, Math.min(100, Number(snapshot.progress) || 0)),
      detail: String(snapshot.detail || '').slice(0, 300),
      updatedAt: new Date().toISOString()
    });
  } catch (error) {
    log('Could not write the Vidnets progress checkpoint (non-fatal): ' + error.message);
  }
}

function log(...args) {
  process.stderr.write('[render-vidnets] ' + args.join(' ') + '\n');
}

function fail(message, code = 1) {
  // The error JSON goes to stdout for the daemon's success parser, but the
  // daemon reports the render command's STDERR tail on failure — so a
  // fail() that only writes stdout is invisible in the daemon log (BNSF
  // attempt 5 died with only a progress line as its "reason"). Write the
  // real reason to stderr too, where the operator will actually see it.
  process.stderr.write('[render-vidnets] ERROR ' + String(message) + '\n');
  process.stdout.write(JSON.stringify({ error: String(message) }) + '\n');
  process.exit(code);
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

/** Run the Vidnets launcher once; parse its stdout JSON. */
function vidnets(args, timeoutMs = 2 * MINUTE) {
  return new Promise((resolve, reject) => {
    const cmd = String(process.env.MUSE_VIDNETS_COMMAND || '').trim();
    const argv = cmd
      ? [cmd, '--runtime-root', RUNTIME_ROOT, ...args]
      : ['bash', LAUNCHER, '--runtime-root', RUNTIME_ROOT, ...args];
    // MUSE_VIDNETS_COMMAND is a shell string in the repo convention; keep
    // the simple case (a single path) and fall back to bash -c otherwise.
    const child = cmd && /\s/.test(cmd)
      ? spawn('bash', ['-c', `${cmd} --runtime-root ${JSON.stringify(RUNTIME_ROOT)} ${args.map(a => JSON.stringify(a)).join(' ')}`], { stdio: ['ignore', 'pipe', 'pipe'] })
      : spawn(argv[0], argv.slice(1), { stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      reject(new Error(`Vidnets ${args[0]} timed out after ${timeoutMs}ms`));
    }, timeoutMs);
    child.stdout.on('data', chunk => { stdout += chunk.toString(); });
    child.stderr.on('data', chunk => { stderr += chunk.toString(); });
    child.on('error', err => { clearTimeout(timer); reject(err); });
    child.on('close', code => {
      clearTimeout(timer);
      let parsed = null;
      try { parsed = JSON.parse(stdout); } catch { /* fall through */ }
      if (code !== 0 || !parsed) {
        reject(new Error(
          `Vidnets ${args[0]} failed (exit ${code}): ${(stderr || stdout).trim().slice(-500) || 'no output'}`
        ));
        return;
      }
      if (parsed.error) reject(new Error(`Vidnets ${args[0]}: ${parsed.error}`));
      else resolve(parsed);
    });
  });
}

function resultVideoPath(item) {
  const result = item?.result || {};
  const candidates = [
    result.video_path, result.videoPath, result.video_path_disk,
    result.video, result.output_path, item?.video_path
  ];
  return candidates.map(v => String(v || '').trim())
    .find(v => v && fs.existsSync(v)) || '';
}

/**
 * Count real-media vs graphic-fallback scenes in the Vidnets edit plan.
 *
 * FILTER-BEST (Nov 9, 2026): a scene counts as real when Vidnets' own
 * adapter accepted the asset — the plan's media.verified flag, which the
 * adapter sets for every kept candidate (pipeline_v2_media_adapter.py
 * ~8291: not explicitly rejected AND (verified OR status accepted /
 * downloaded OR a qwen verdict accepted / deterministic)). That covers
 * both Muse-verified clips (visual_decision visual_checked + accepted)
 * and fail-open clips (vision unavailable, kept on download/metadata —
 * the native _youtube_fail_open_on_visual_unavailable path). An explicit
 * Muse rejection (accepted === false, fatal_mismatch, rejected status)
 * always beats the flag: a rejected asset is never real. `verified`
 * still counts Muse-verified scenes separately, for reporting.
 * The delivery rule is the ONLY hard rule: real === 0 fails the job.
 */
function countRealScenes(packageDir) {
  if (!packageDir || !fs.existsSync(packageDir)) return null;
  const plans = fs.readdirSync(packageDir)
    .filter(name => name.endsWith('_remotion_edit_plan.json'))
    .map(name => path.join(packageDir, name));
  if (!plans.length) return null;
  let real = 0;
  let graphic = 0;
  let total = 0;
  let verified = 0;
  for (const planFile of plans) {
    let plan;
    try { plan = JSON.parse(fs.readFileSync(planFile, 'utf8')); } catch { continue; }
    for (const scene of plan.scenes || []) {
      total += 1;
      const media = scene.media || {};
      const src = String(media.src || '');
      const decision = media.visual_decision || {};
      const museVerified = decision.visual_checked === true && decision.accepted === true;
      if (museVerified) verified += 1;
      const explicitlyRejected = decision.accepted === false
        || decision.fatal_mismatch === true
        || String(decision.status || '').trim().toLowerCase() === 'rejected';
      const adapterAccepted = media.verified === true || museVerified;
      if (media.type === 'graphic' || /graphic_fallback|emergency/i.test(src)) graphic += 1;
      else if ((media.type === 'image' || media.type === 'video') && src && adapterAccepted && !explicitlyRejected) real += 1;
      else graphic += 1;
    }
  }
  return total ? { real, graphic, total, verified } : null;
}

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', chunk => { data += chunk; });
    process.stdin.on('end', () => resolve(data));
    process.stdin.on('error', reject);
  });
}

async function main() {
  const input = JSON.parse(await readStdin());
  const job = input.job || {};
  const scriptText = String(input.scriptText || '').trim();
  const workDir = String(input.workDir || '').trim();
  if (!job.jobId || !scriptText || !workDir) {
    fail('render-vidnets-linux needs job.jobId, scriptText and workDir on stdin');
  }
  // Muse inference is compulsory: refuse to submit a job when the
  // agent-bridge is absent (no hook operator, no inbox, or an OpenRouter
  // key in the environment). The job fails here, before any render work,
  // instead of completing with unanswered inference requests.
  const bridge = checkMuseInferenceBridge(process.env);
  if (!bridge.ok) {
    fail('Muse inference bridge is not available: ' + bridge.failures.join('; '));
  }

  // Temp headroom first: a render that starts with a full /tmp or temp dir
  // dies halfway with a cryptic error. Fail here, before submitting.
  try {
    const space = checkTempSpace();
    log(`Temp space OK: ${space.tempDir} ${space.tempFreeMb} MB free, /tmp ${space.tmpFreeMb} MB free`);
  } catch (err) {
    fail(err.message);
  }

  fs.mkdirSync(workDir, { recursive: true });
  const packageDir = path.join(workDir, 'vidnets');
  fs.mkdirSync(packageDir, { recursive: true });

  // Reuse the repo's own request builder so the Vidnets job is identical
  // to the one adapters/vidnets.js would submit (external_agent, Mixed
  // Media, youtube/web/stock priority, strict retrieval).
  const { buildRequest } = require('./adapters/vidnets');
  const request = buildRequest(job, scriptText, packageDir);
  const requestFile = path.join(workDir, 'vidnets-request.json');
  const jobFile = path.join(workDir, 'vidnets-job.json');
  let receipt = readRenderReceipt(jobFile, job.jobId);
  let vidnetsJobId = receipt?.id || '';
  let replanAttempted = replanAlreadyUsed(receipt);
  const cmsAttempt = Math.max(1, Number(input.attempt) || 1);
  const submit = filePath => vidnets(['submit', filePath], 2 * MINUTE);
  const saveReceipt = fields => {
    receipt = { ...receipt, ...fields, id: vidnetsJobId, jobId: job.jobId, packageDir };
    writeJsonAtomic(jobFile, receipt);
  };
  if (vidnetsJobId) log(`Re-attaching to existing Vidnets job ${vidnetsJobId} for ${job.jobId}`);

  log('Ensuring Vidnets service is ready');
  await vidnets(['start'], 2 * MINUTE);

  if (!vidnetsJobId) {
    vidnetsJobId = await submitRender({ jobId: job.jobId, filePath: requestFile, request, submit });
    saveReceipt({ cmsAttempt, submittedAt: new Date().toISOString() });
    log(`Vidnets job ${vidnetsJobId} submitted for "${job.title}"`);
  }

  const deadline = Date.now()
    + Math.max(10 * MINUTE, Number(process.env.MUSE_RENDER_TIMEOUT_MS) || 6 * 60 * MINUTE);
  const pollMs = Math.max(2000, Number(process.env.MUSE_VIDNETS_POLL_MS) || 5000);
  let retries = Number(receipt.cmsAttempt) === cmsAttempt ? (Number(receipt.pausedRetries) || 0) : 0;
  // replanAttempted is declared above (restored from the saved job file on
  // re-attach) so a resume cannot earn a second re-plan.
  let lastProgress = -1;
  let lastDetail = '';
  let lastPendingCheck = 0;
  let pendingCheck = null;
  let statusFailures = 0;

  while (true) {
    if (Date.now() > deadline) {
      fail(`Vidnets render exceeded the configured budget for job ${vidnetsJobId}`);
    }

    // A lost status response does not establish that the renderer stopped.
    let snapshot;
    try {
      snapshot = await vidnets(['job', vidnetsJobId], MINUTE);
      if (!snapshot.item?.status) throw new Error('Vidnets returned no job status');
      statusFailures = 0;
    } catch (error) {
      if (/returned (400|401|403|404)\b/i.test(error.message)) throw error;
      statusFailures += 1;
      const delayMs = Math.min(30000, pollMs * 2 ** Math.min(statusFailures - 1, 5));
      log(`Vidnets status unavailable for ${vidnetsJobId}; will recheck the same render in ${Math.round(delayMs / 1000)}s: ${error.message}`);
      await sleep(delayMs);
      continue;
    }

    // Inbox diagnostics must not delay progress polling.
    if (!pendingCheck && Date.now() - lastPendingCheck > MINUTE) {
      lastPendingCheck = Date.now();
      pendingCheck = vidnets(['pending'], 8000).then(pending => {
        const rows = Array.isArray(pending.requests) ? pending.requests : [];
        const mine = rows.filter(row => String(row.job_id || '') === vidnetsJobId);
        if (mine.length) log(`Inbox: ${mine.length} request(s) still pending for this job (hook worker answers these)`);
      }).catch(error => log('Inbox pending check failed (non-fatal): ' + error.message))
        .finally(() => { pendingCheck = null; });
    }
    const item = snapshot.item || {};
    const progress = Math.max(0, Math.min(100, Number(item.progress) || 0));
    const detail = String(item.progress_text || item.status || '').replace(/\s+/g, ' ').trim().slice(0, 300) || 'Rendering';
    // Log on stage-text change as well as % change: a render held for
    // memory sits at 0% for minutes, and a % only log goes silent.
    if (progress !== lastProgress || detail !== lastDetail) {
      lastProgress = progress;
      lastDetail = detail;
      log(`Vidnets ${progress}% — ${detail}`);
      // Live checkpoint on every poll change: the daemon streams this
      // same line to the CMS, and the file is the durable copy for a
      // daemon that dies mid-render.
      writeVidnetsProgressFile(workDir, {
        vidnetsJobId, cmsJobId: job.jobId, status: item.status, progress, detail
      });
    }

    if (item.status === 'completed') {
      const videoPath = resultVideoPath(item);
      if (!videoPath) fail('Vidnets completed the job without a readable video_path');
      const size = fs.statSync(videoPath).size;
      if (size < 1024 * 1024) fail(`Vidnets video is too small to be a real master (${size} bytes)`);
      const mediaCount = countRealScenes(path.dirname(videoPath));
      if (!mediaCount) {
        // No edit plan next to the video means the real-media mix cannot
        // be verified at all — fail closed instead of accepting on file
        // size and decode checks alone.
        fail('Vidnets completed without an edit plan next to the video — the real-media mix cannot be verified. Failing instead of delivering an unverified video.');
      }
      log(`Edit plan: ${mediaCount.real}/${mediaCount.total} scenes real media (${mediaCount.verified} Muse-verified), ${mediaCount.graphic} graphic fallback(s)`);
      if (mediaCount.real === 0) {
        // The ONLY hard delivery rule: never deliver a video with 0
        // real-media scenes. Filter-best means a scene can be real via
        // the adapter's own acceptance (verified flag) even when Muse
        // never answered a vision request for it — so there is no
        // separate "no Muse verdicts at all" failure any more. The
        // compulsory-inference gate still applies at STARTUP (the job
        // must have the bridge available), but a missing verdict on a
        // scene the adapter accepted on download/metadata is not a
        // reason to throw the video away.
        const reason = 'Vidnets completed with 0 real-media scenes (all graphic fallbacks) — no media was accepted. Failing instead of delivering a graphics-only video.';
        // Bounded media re-plan: one fresh submit with fresh queries, inside
        // the same overall render budget. The first attempt delivered
        // nothing (this branch only runs when the plan is 0-real, so the
        // wrapper has returned no success), and the job file is overwritten
        // with the new id, so a later resume re-attaches to the re-plan —
        // never to the dead attempt — and a delivery can only happen once,
        // from the single success output at the end of this run.
        const msRemaining = deadline - Date.now();
        const minRemainingMs = Math.max(0, Number(process.env.MUSE_REPLAN_MIN_REMAINING_MS) || REPLAN_MIN_REMAINING_MS);
        if (shouldAttemptReplan({ real: mediaCount.real, replanAttempted, msRemaining, minRemainingMs })) {
          replanAttempted = true;
          const previousId = vidnetsJobId;
          const retryRequest = buildRetryRequest(request);
          const retryRequestFile = path.join(workDir, 'vidnets-request-replan.json');
          log(`0 real-media scenes — attempting the one bounded media re-plan (fresh queries, plan cache bypassed) with ${Math.round(msRemaining / 60000)} min left in the render budget`);
          vidnetsJobId = await submitRender({ jobId: job.jobId, filePath: retryRequestFile, request: retryRequest, submit });
          saveReceipt({
            cmsAttempt, pausedRetries: 0, submittedAt: new Date().toISOString(),
            replanOf: previousId, replanAttempted: 1
          });
          log(`Media re-plan submitted as Vidnets job ${vidnetsJobId} (re-plan of ${previousId})`);
          retries = 0;
          lastProgress = -1;
          lastPendingCheck = 0;
          continue;
        }
        fail(replanAttempted ? `${reason} (after one bounded media re-plan)` : reason);
      }
      let thumbnailPath = '';
      const dir = path.dirname(videoPath);
      try {
        const thumb = fs.readdirSync(dir)
          .find(n => /thumb/i.test(n) && /\.(jpe?g|png|webp)$/i.test(n));
        if (thumb) thumbnailPath = path.join(dir, thumb);
      } catch { /* thumbnail is optional; the worker extracts a frame */ }
      process.stdout.write(JSON.stringify({
        videoPath,
        thumbnailPath,
        meta: {
          engine: 'vidnets-linux',
          vidnetsJobId,
          replanAttempts: replanAttempted ? 1 : 0,
          realScenes: mediaCount.real,
          totalScenes: mediaCount.total,
          graphicScenes: mediaCount.graphic,
          museVerifiedScenes: mediaCount.verified
        }
      }) + '\n');
      return;
    }
    if (['error', 'cancelled', 'shutdown_timeout'].includes(item.status)) {
      if (item.status !== 'cancelled' && cmsAttempt > Number(receipt.cmsAttempt || 1)) {
        log(`CMS attempt ${cmsAttempt}: resuming failed Vidnets job ${vidnetsJobId} from its checkpoint`);
        await vidnets(['retry', vidnetsJobId], 2 * MINUTE);
        saveReceipt({ cmsAttempt, pausedRetries: 0 });
        retries = 0;
        continue;
      }
      fail('Vidnets render failed: ' + String(item.error || item.paused_reason || item.progress_text || `status ${item.status}`).slice(0, 700));
    }
    if (item.status === 'paused') {
      retries += 1;
      if (retries > 3) {
        // Name the cause instead of blaming inference: a memory hold
        // (waiting_for_memory / HELD) looks identical in the job item.
        const reasonText = String(item.paused_reason || item.progress_text || 'no reason given');
        let memoryNote = '';
        if (/memory|held|waiting_for_memory/i.test(reasonText)) {
          try {
            const res = await fetch('http://127.0.0.1:5010/api/status', { signal: AbortSignal.timeout(5000) });
            const status = await res.json();
            const mem = status?.memory || {};
            memoryNote = ` Vidnets memory: ${mem.admission_state || 'unknown'} (${mem.available_mb ?? '?'} MB available, render needs ~${mem.estimated_worker_mb ?? 384} MB). Free memory or wait for the hold to clear; it is not an inference problem.`;
          } catch { /* memory note stays empty; the reason still names it */ }
          fail(`Vidnets job ${vidnetsJobId} stayed paused after 3 retries (${reasonText}) — render held for memory.${memoryNote}`);
        }
        fail(`Vidnets job ${vidnetsJobId} stayed paused after 3 retries (${reasonText}) — likely unanswered inference requests. Check the inbox-watch hook.`);
      }
      // A memory pause is not an inference failure and must not be
      // retried immediately: the server-side memory wait only pauses
      // after its (worker-extended) budget is spent, so an instant retry
      // just re-enters the same wait and burns one of the 3 retries
      // while the media workers are still freeing RAM. Wait first
      // (MUSE_VIDNETS_MEMORY_RETRY_DELAY_MS, default 30s) so the retry
      // lands after workers have had a chance to exit.
      const isMemoryPause = /memory|held|waiting_for_memory/i.test(
        String(item.paused_reason || item.progress_text || ''));
      if (isMemoryPause) {
        const delayMs = Math.max(0, Number(process.env.MUSE_VIDNETS_MEMORY_RETRY_DELAY_MS) || 30000);
        let memNote = '';
        try {
          const res = await fetch('http://127.0.0.1:5010/api/status', { signal: AbortSignal.timeout(5000) });
          const status = await res.json();
          const mem = status?.memory || {};
          memNote = ` (${mem.available_mb ?? '?'} MB available, render needs ~${mem.estimated_worker_mb ?? 384} MB)`;
        } catch { /* memory note stays empty; the retry still waits */ }
        log(`Vidnets job paused for memory (attempt ${retries}/3); waiting ${Math.round(delayMs / 1000)}s for media workers to free RAM before retrying${memNote}`);
        await sleep(delayMs);
      } else {
        log(`Vidnets job paused (attempt ${retries}/3); retrying`);
      }
      saveReceipt({ cmsAttempt, pausedRetries: retries });
      await vidnets(['retry', vidnetsJobId], 2 * MINUTE);
    }

    await sleep(pollMs);
  }
}

if (require.main === module) {
  main().catch(err => fail(err && err.message ? err.message : String(err)));
}

module.exports = { countRealScenes, checkTempSpace, TEMP_MIN_FREE_MB, TMP_MIN_FREE_MB, shouldAttemptReplan, buildRetryRequest, replanAlreadyUsed, REPLAN_MIN_REMAINING_MS, writeVidnetsProgressFile };
