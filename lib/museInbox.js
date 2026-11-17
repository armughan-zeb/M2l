'use strict';

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const { writeJsonAtomic } = require('./config');
const { bridgeRoot, vidnetsRoot } = require('./museInference');
const { resolvePython } = require('./python');
const { sleep } = require('./sleep');
const { retryDelayMs } = require('./cmsRetry');

const REQUEST_ID = /^[a-f0-9]{32}$/;
const PROBE_OPERATION = 'muse.inbox_probe';
const REPORT_INTERVAL_MS = 60000;
const PROBE_WALL_MS = 120000;
// Real inference requests get a much longer transport wait than probes.
// The 2-minute probe wall was applied to ALL requests, and it failed large
// requests terminally: a grounded 12-scene query plan takes a resident
// worker ~10 minutes to compose, so the daemon recorded "Muse inference
// failed" while the worker's good answer was still being written. The
// request wall is a deadline, never a delay — a fast answer still ends the
// wait early, and it never exceeds the request's own expiry. Env
// MUSE_INBOX_REQUEST_WALL_MS overrides; default 900000 (15 min).
const REQUEST_WALL_MS = (() => {
  const raw = Number(process.env.MUSE_INBOX_REQUEST_WALL_MS);
  return Number.isFinite(raw) && raw > 0 ? raw : 900000;
})();
// Marker the spool transport (scripts/muse-inference-spool.cjs) prints when
// the resident worker is not running. A request that fails this way must
// stay pending for the host hook — it is not a failed answer.
const TRANSPORT_UNAVAILABLE = 'MUSE_TRANSPORT_UNAVAILABLE';
// Cooldown after a transport-unavailable failure: stop picking up new
// requests for this long instead of spawn-looping a dead transport.
const TRANSPORT_UNAVAILABLE_COOLDOWN_MS = 30000;
// A worker heartbeat older than this means the pool is not live (same
// threshold the spool transport uses to report MUSE_TRANSPORT_UNAVAILABLE).
const POOL_HEARTBEAT_MAX_AGE_MS = 90000;
// After the spool reports the pool dead, a fresh heartbeat alone does not
// reopen the readiness gate for this long: the report came from an actual
// transport attempt, so it outranks a heartbeat file (which a stale
// writer can keep fresh). A real answer reopens the gate immediately.
const TRANSPORT_DEAD_MEMORY_MS = 5 * 60000;

// Live state shared by the resident inbox and the readiness gate (same
// module, so the gate the daemon consults before claiming can see it
// without a caller change). Real-request answers are the liveness proof:
// workers refuse inbox probes (token echo) and a probe is NOT a liveness
// signal, so the gate must not depend on one. The inbox records the last
// real answer it delivered and the last time the spool transport reported
// the pool dead (exit 3).
const residentLiveness = {
  inboxStarted: false,
  lastRealAnswerAt: 0,
  transportDeadAt: 0,
};

function spoolDirFor(env = process.env) {
  // Same resolution rule as scripts/muse-inference-spool.cjs, so the
  // inbox and the transport agree on where the pool lives.
  const explicit = String(env.MUSE_INFERENCE_SPOOL_DIR || '').trim();
  if (explicit) return explicit;
  try { return path.join(bridgeRoot(env), '..', 'inference-spool'); }
  catch { return path.join(process.cwd(), 'inference-spool'); }
}

function poolHeartbeatAgeMs(dir, now = Date.now()) {
  // Youngest heartbeat across all pool workers (worker-alive-<id>.json,
  // plus the legacy single-worker file). Infinity when no heartbeat
  // exists or none parses — the pool is not provably live.
  let youngest = Infinity;
  let found = false;
  let entries;
  try { entries = fs.readdirSync(dir); } catch { return Infinity; }
  for (const entry of entries) {
    if (entry !== 'worker-alive.json' && !/^worker-alive-[A-Za-z0-9._-]+\.json$/.test(entry)) continue;
    try {
      const updatedAt = Number(JSON.parse(fs.readFileSync(path.join(dir, entry), 'utf8'))?.updatedAt);
      if (!Number.isFinite(updatedAt)) continue;
      found = true;
      youngest = Math.min(youngest, now - updatedAt);
    } catch { /* unreadable heartbeat — ignore it */ }
  }
  return found ? youngest : Infinity;
}

function operation(row) {
  return String(row.operation || row.kind || 'unspecified').slice(0, 100);
}

function ageMs(row, now) {
  return Math.max(0, Math.round(now - (Number(row.created_at) * 1000 || now)));
}

function readJson(file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); }
  catch { return null; }
}

function pendingRequests(root, now = Date.now()) {
  return fs.readdirSync(root).filter(id => REQUEST_ID.test(id)).map(id => {
    const dir = path.join(root, id);
    if (fs.existsSync(path.join(dir, 'response.json'))) return null;
    const request = readJson(path.join(dir, 'request.json'));
    if (request?.id !== id || request.state !== 'pending' ||
        !(Number(request.expires_at) * 1000 > now)) return null;
    return request;
  }).filter(Boolean).sort((a, b) => a.expires_at - b.expires_at);
}

async function pendingSummary(root, jobId, owner = process.env.MUSE_INBOX_OWNER || 'local-user') {
  const start = Date.now();
  const ids = (await fs.promises.readdir(root)).filter(id => REQUEST_ID.test(id));
  const rows = [];
  let next = 0;
  async function scan() {
    while (next < ids.length) {
      const id = ids[next++];
      const dir = path.join(root, id);
      try {
        await fs.promises.access(path.join(dir, 'response.json'));
        continue;
      } catch (error) {
        if (error.code !== 'ENOENT') throw error;
      }
      try {
        const row = JSON.parse(await fs.promises.readFile(path.join(dir, 'request.json'), 'utf8'));
        if (row.id === id && row.owner_id === owner && row.state === 'pending' &&
            String(row.job_id || '') === String(jobId) && Number(row.expires_at) * 1000 > Date.now()) {
          rows.push(row);
        }
      } catch (error) {
        if (error.code !== 'ENOENT' && !(error instanceof SyntaxError)) throw error;
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(16, ids.length) }, scan));
  rows.sort((a, b) => Number(a.created_at) - Number(b.created_at));
  return { rows, elapsedMs: Date.now() - start };
}

async function completeLocally(root, request, reply) {
  const installRoot = vidnetsRoot();
  const runtimeRoot = fs.existsSync(path.join(installRoot, 'resources', 'server', 'headless_agent.py'))
    ? path.join(installRoot, 'resources') : installRoot;
  const bundledPython = [
    'tools/python/bin/python3', 'tools/python/python', '.venv/bin/python3', 'venv/bin/python3',
    'tools/python/python.exe', '.venv/Scripts/python.exe', 'venv/Scripts/python.exe'
  ].map(file => path.join(runtimeRoot, file)).find(file => fs.existsSync(file));
  const python = process.env.VIDNETS_PYTHON || process.env.PYTHON || process.env.MUSE_PYTHON ||
    bundledPython || await resolvePython();
  const source = [
    'import json, os, sys',
    'sys.path.insert(0, os.getcwd())',
    'from services.agent_bridge import complete_request',
    'row = json.load(sys.stdin)',
    'complete_request(row["id"], row["owner"], row.get("content"), row.get("error", ""))'
  ].join('\n');
  return new Promise((resolve, reject) => {
    const child = spawn(python, ['-c', source], {
      cwd: path.join(runtimeRoot, 'server'),
      env: { ...process.env, VIDNETS_AGENT_BRIDGE_DIR: root, PYTHONIOENCODING: 'utf-8' },
      stdio: ['pipe', 'ignore', 'pipe'],
      windowsHide: true
    });
    let stderr = '';
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error('Muse answer validation timed out'));
    }, 20000);
    child.stderr.on('data', data => { stderr = (stderr + data).slice(-1000); });
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('close', code => {
      clearTimeout(timer);
      if (code === 0) resolve();
      else reject(new Error('Muse answer rejected: ' + stderr.split('\n').filter(Boolean).at(-1)));
    });
    child.stdin.on('error', () => {});
    child.stdin.end(JSON.stringify({ id: request.id, owner: request.owner_id, ...reply }));
  });
}

function startResidentInbox({ root, answer, concurrency = 8, pollMs = 1000, logger = console, onExpiryLimit,
  complete = completeLocally, owner = process.env.MUSE_INBOX_OWNER || 'local-user',
  transportUnavailableCooldownMs = TRANSPORT_UNAVAILABLE_COOLDOWN_MS } = {}) {
  const running = new Map();
  const handled = new Map();
  let lastReport = Date.now();
  const expiredCounts = new Map();
  const activity = new Map();
  const observed = new Map();
  const statusFile = path.join(root, 'resident-status.json');
  let stopped = false;
  let lastStatus = 0;
  let refill;
  let consecutiveExpiries = 0;
  residentLiveness.inboxStarted = true;
  // The spool transport exits 3 with this marker when the resident worker
  // is not running. That is NOT a failed request: the request must stay
  // pending so the host hook can still answer it. While the transport is
  // down, stop picking up new requests for a cooldown instead of
  // spawn-looping a dead transport once a second.
  let transportUnavailableUntil = 0;
  const isTransportUnavailable = error =>
    error?.code === 3 || String(error?.message || '').includes(TRANSPORT_UNAVAILABLE);

  const tick = () => {
    if (stopped) return;
    const now = Date.now();
    for (const [id, expiry] of handled) if (expiry <= now) handled.delete(id);
    const pending = pendingRequests(root, now).filter(request => request.owner_id === owner);
    pending.sort((a, b) => Number(operation(b).startsWith('scriptit.')) - Number(operation(a).startsWith('scriptit.')) ||
      a.expires_at - b.expires_at);
    const counts = new Map();
    for (const row of pending) {
      const op = operation(row);
      const entry = counts.get(op) || { pending: 0, oldestMs: 0 };
      entry.pending += 1;
      entry.oldestMs = Math.max(entry.oldestMs, ageMs(row, now));
      counts.set(op, entry);
      const first = observed.get(row.id);
      if (!first) observed.set(row.id, {
        row: { operation: row.operation, kind: row.kind, job_id: row.job_id, expires_at: row.expires_at },
        warned: false
      });
      if (first && !first.warned && ageMs(row, now) >= 60000) {
        first.warned = true;
        logger.warn(`Muse inbox request ${row.id} (${op}) for ${row.job_id || 'diagnostics'} has waited ${ageMs(row, now)}ms`);
      }
    }
    const pendingIds = new Set(pending.map(row => row.id));
    for (const [id, { row }] of observed) {
      if (pendingIds.has(id)) continue;
      observed.delete(id);
      const dir = path.join(root, id);
      const state = readJson(path.join(dir, 'request.json'))?.state;
      if (state === 'answered' || fs.existsSync(path.join(dir, 'response.json')) ||
          (state === 'expired' && now < row.expires_at * 1000)) continue;
      if (now >= row.expires_at * 1000) {
        const op = operation(row);
        expiredCounts.set(op, (expiredCounts.get(op) || 0) + 1);
        logger.warn(`Muse inbox request ${id} (${op}) expired for ${row.job_id || 'diagnostics'}`);
        if (op !== PROBE_OPERATION && ++consecutiveExpiries === 3) onExpiryLimit?.();
      }
    }
    if (now - lastReport >= REPORT_INTERVAL_MS) {
      const operations = new Set([...counts.keys(), ...activity.keys(), ...expiredCounts.keys()]);
      const metrics = [...operations].map(op => {
        const latencies = (activity.get(op) || []).sort((a, b) => a - b);
        const percentile = p => latencies[Math.ceil(latencies.length * p) - 1] ?? null;
        return {
          operation: op, ...(counts.get(op) || { pending: 0, oldestMs: 0 }),
          answered: latencies.length, p50Ms: percentile(.5), p95Ms: percentile(.95),
          expiredTotal: expiredCounts.get(op) || 0
        };
      });
      if (metrics.length) logger.info('Muse inbox metrics: ' + JSON.stringify({ concurrency, inFlight: running.size, metrics }));
      activity.clear();
      lastReport = now;
    }
    if (now - lastStatus >= 5000) {
      lastStatus = now;
      try {
        writeJsonAtomic(statusFile, {
          updatedAt: now, pending: pending.length, inFlight: running.size, concurrency,
          operations: Object.fromEntries(counts), expired: Object.fromEntries(expiredCounts),
          oldestMs: pending.reduce((age, row) => Math.max(age, ageMs(row, now)), 0),
          lastRealAnswerAt: residentLiveness.lastRealAnswerAt || null
        }, { mode: 0o600 });
      } catch (error) {
        logger.warn('Could not write Muse inbox status: ' + error.message);
      }
    }
    for (const request of pending) {
      if (running.size >= concurrency) break;
      if (handled.has(request.id)) continue;
      // The transport is down (resident worker not running): do not pick
      // up new requests — they stay pending for the host hook instead of
      // burning a spawn per second on a dead transport.
      if (now < transportUnavailableUntil) break;
      const expiresAt = request.expires_at * 1000;
      handled.set(request.id, expiresAt);
      // Probes are a 2-minute liveness check; real requests get the full
      // request wall so a multi-scene answer can finish composing.
      const wallMs = operation(request) === PROBE_OPERATION ? PROBE_WALL_MS : REQUEST_WALL_MS;
      const remainingMs = Math.min(wallMs, expiresAt - now);
      const timeoutMs = Math.floor(remainingMs - Math.min(20000, remainingMs / 3));
      if (timeoutMs <= 0) continue;
      const controller = new AbortController();
      running.set(request.id, controller);
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      logger.info(`Muse inbox picked up ${request.id} (${operation(request)}) after ${ageMs(request, now)}ms`);
      void (async () => {
        try {
          const content = await answer(request, { timeoutMs, signal: controller.signal });
          if (stopped || Date.now() >= expiresAt) return;
          if (controller.signal.aborted) throw new Error('Muse did not answer within the request budget');
          await complete(root, request, { content });
          consecutiveExpiries = 0;
          // A delivered REAL answer is the liveness proof the readiness
          // gate uses. Probes never count: workers refuse them (token
          // echo) and a refused probe proves nothing about inference.
          if (operation(request) !== PROBE_OPERATION) residentLiveness.lastRealAnswerAt = Date.now();
          const op = operation(request);
          const latency = ageMs(request, Date.now());
          const samples = activity.get(op) || [];
          samples.push(latency);
          activity.set(op, samples);
          logger.info(`Muse inbox answered ${request.id} (${op}) in ${latency}ms`);
          if (latency > 60000) logger.warn(`Muse inbox request ${request.id} exceeded its 60-second answer target`);
        } catch (error) {
          if (stopped || Date.now() >= expiresAt) return;
          if (isTransportUnavailable(error)) {
            // The resident worker is not running. Leave the request pending
            // for the host hook and back off pickup for a cooldown — do NOT
            // record a failure: nothing about this request is wrong, and
            // failing it would destroy the hook fallback. Record the death
            // for the readiness gate: a fresh heartbeat alone must not
            // reopen claims for a while after an actual transport attempt
            // proved the pool dead (a real answer still reopens it at once).
            residentLiveness.transportDeadAt = Date.now();
            transportUnavailableUntil = Date.now() + transportUnavailableCooldownMs;
            handled.delete(request.id);
            logger.warn(`Muse inbox transport unavailable for ${request.id} (${operation(request)}): ` +
              'resident worker not running — request left pending for the hook; pickup paused for ' +
              `${transportUnavailableCooldownMs / 1000}s`);
            return;
          }
          logger.warn(`Muse inbox request ${request.id} failed: ${error.message}`);
          try { await complete(root, request, { error: 'Muse inference failed for ' + request.id }); }
          catch (completionError) { logger.warn(`Could not record Muse request ${request.id} failure: ${completionError.message}`); }
        } finally {
          clearTimeout(timer);
          running.delete(request.id);
          // Refill a freed slot immediately instead of waiting for the next scan.
          if (!stopped && !refill) {
            refill = setImmediate(() => {
              refill = undefined;
              safeTick();
            });
          }
        }
      })();
    }
  };
  const safeTick = () => {
    try { tick(); } catch (error) { logger.warn('Muse inbox scan failed: ' + error.message); }
  };
  safeTick();
  const interval = setInterval(safeTick, pollMs);
  interval.unref?.();
  // Watch for new request directories as a fast path. Some filesystems do not
  // support watching reliably, and request.json can arrive after its directory,
  // so the periodic scan remains the authoritative fallback.
  let watcher;
  let scheduled;
  try {
    watcher = fs.watch(root, (_event, filename) => {
      if (filename && !REQUEST_ID.test(filename)) return;
      if (scheduled || stopped) return;
      scheduled = setTimeout(() => { scheduled = undefined; safeTick(); }, 25);
    });
    watcher.on('error', error => {
      logger.warn('Muse inbox watch failed; polling remains active: ' + error.message);
      watcher.close();
      watcher = undefined;
    });
    watcher.unref?.();
  } catch (error) {
    logger.warn('Muse inbox watch unavailable; polling remains active: ' + error.message);
  }
  return () => {
    stopped = true;
    clearInterval(interval);
    clearImmediate(refill);
    clearTimeout(scheduled);
    watcher?.close();
    for (const controller of running.values()) controller.abort();
    try { fs.rmSync(statusFile, { force: true }); } catch { /* status expires by timestamp */ }
  };
}

async function probeInbox(env = process.env, { timeoutMs = PROBE_WALL_MS, pollMs = 250, signal } = {}) {
  if (signal?.aborted) throw new Error('Muse inbox probe cancelled');
  const root = bridgeRoot(env);
  const id = crypto.randomBytes(16).toString('hex');
  const directory = path.join(root, id);
  const file = path.join(directory, 'request.json');
  const start = Date.now();
  const expected = 'MUSE_READY_' + crypto.randomBytes(6).toString('hex');
  const request = {
    id, owner_id: env.MUSE_INBOX_OWNER || 'local-user', job_id: '', attempt_id: '', kind: 'text', operation: PROBE_OPERATION,
    state: 'pending', created_at: start / 1000, expires_at: (start + timeoutMs) / 1000,
    messages: [{ role: 'user', content: 'Reply with this exact text and nothing else: ' + expected }],
    instructions: 'This is a diagnostic request. Reply as plain text.',
    response_format: {}, max_tokens_hint: 40
  };
  writeJsonAtomic(file, request, { mode: 0o600 });
  while (!signal?.aborted && Date.now() < start + timeoutMs) {
    const reply = readJson(path.join(directory, 'response.json'));
    if (reply) {
      if (reply.error) throw new Error(`Muse inbox probe ${id} failed: ${String(reply.error).slice(0, 200)}`);
      if (String(reply.content || '').trim() !== expected) throw new Error(`Muse inbox probe ${id} returned an invalid answer`);
      return { id, elapsedMs: Date.now() - start };
    }
    await sleep(Math.min(pollMs, Math.max(0, start + timeoutMs - Date.now())), signal);
  }
  throw new Error(`Muse inbox probe ${id} did not receive an answer within ${timeoutMs}ms`);
}

function createInboxReadiness({ probe, onReady, onFailure, now = Date.now, spoolDir } = {}) {
  const refreshMs = 4 * 60000;
  const validMs = 5 * 60000;
  let readyUntil = 0;
  let checkAfter = 0;
  let failures = 0;
  let pending;
  let generation = 0;
  // Set by invalidate() (three consecutive real expiries): the gate must
  // not reopen on transport optimism alone — only a fresh real answer or
  // a passing probe clears it. A real answer clears it inline below.
  let needsFreshProof = false;

  function grantReadiness(current) {
    failures = 0;
    needsFreshProof = false;
    readyUntil = current + validMs;
    checkAfter = current + refreshMs;
  }

  async function isReady() {
    const current = now();
    // 1. A recent REAL answer is the liveness proof. Probes are refused
    // by workers (they will not echo tokens) and are NOT a liveness
    // signal, so a refused probe must never keep claims paused while
    // real inference is demonstrably working. No probe is filed here:
    // filing one only burns a worker turn on a refusal.
    if (residentLiveness.lastRealAnswerAt &&
        current - residentLiveness.lastRealAnswerAt < validMs) {
      grantReadiness(current);
      return true;
    }
    // 2. A live resident pool (fresh worker heartbeat) with no recent
    // transport-death report means inference is available: the first
    // real request is the proof, and a dead pool is detected within
    // seconds by the spool's own heartbeat check (exit 3), which closes
    // the gate again. Skipped after invalidate() until fresh proof, and
    // skipped when no inbox is running (unit tests, hook-only hosts).
    if (!needsFreshProof && residentLiveness.inboxStarted) {
      const deadRecently = residentLiveness.transportDeadAt &&
        current - residentLiveness.transportDeadAt < TRANSPORT_DEAD_MEMORY_MS;
      if (!deadRecently) {
        let dir = spoolDir;
        if (!dir) { try { dir = spoolDirFor(); } catch { dir = ''; } }
        if (dir && poolHeartbeatAgeMs(dir, current) <= POOL_HEARTBEAT_MAX_AGE_MS) {
          grantReadiness(current);
          return true;
        }
      }
    }
    // 3. Otherwise fall back to the probe: the hook path may still
    // answer it. This is the only path that files a probe, so a live
    // pool is never spammed with probes it will refuse.
    if (!pending && current >= checkAfter) {
      const started = generation;
      pending = Promise.resolve().then(probe).then(proof => {
        if (started !== generation) return false;
        grantReadiness(now());
        onReady(proof);
        return true;
      }, error => {
        if (started !== generation) return false;
        readyUntil = 0;
        failures += 1;
        checkAfter = now() + retryDelayMs(failures - 1, { baseMs: 5000 });
        onFailure(error);
        return false;
      }).finally(() => { pending = undefined; });
    }
    if (current < readyUntil) return true;
    return pending ? await pending : false;
  }
  isReady.invalidate = () => {
    generation += 1;
    readyUntil = 0;
    checkAfter = 0;
    needsFreshProof = true;
  };
  return isReady;
}

module.exports = { startResidentInbox, probeInbox, pendingRequests, pendingSummary, completeLocally, createInboxReadiness, poolHeartbeatAgeMs, spoolDirFor, POOL_HEARTBEAT_MAX_AGE_MS, PROBE_WALL_MS, REQUEST_WALL_MS,
  // Test-only: reset the shared liveness state between readiness scenarios
  // in one process. Production never calls this.
  resetResidentLivenessForTests() {
    residentLiveness.inboxStarted = false;
    residentLiveness.lastRealAnswerAt = 0;
    residentLiveness.transportDeadAt = 0;
  } };
