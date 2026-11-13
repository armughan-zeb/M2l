'use strict';

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const { writeJsonAtomic } = require('./config');
const { bridgeRoot, vidnetsRoot } = require('./museInference');
const { resolvePython } = require('./python');
const { sleep } = require('./sleep');

const REQUEST_ID = /^[a-f0-9]{32}$/;
const PROBE_OPERATION = 'muse.inbox_probe';
const REPORT_INTERVAL_MS = 60000;

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
    'import json, sys',
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
    }, 8000);
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

function startResidentInbox({ root, answer, concurrency = 8, pollMs = 1000, logger = console,
  complete = completeLocally, owner = process.env.MUSE_INBOX_OWNER || 'local-user' } = {}) {
  const running = new Map();
  const handled = new Map();
  let lastReport = Date.now();
  const expiredCounts = new Map();
  const activity = new Map();
  const observed = new Map();
  const statusFile = path.join(root, 'resident-status.json');
  let stopped = false;
  let lastStatus = 0;

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
      writeJsonAtomic(statusFile, {
        updatedAt: now, pending: pending.length, inFlight: running.size, concurrency,
        operations: Object.fromEntries(counts), expired: Object.fromEntries(expiredCounts),
        oldestMs: pending.reduce((age, row) => Math.max(age, ageMs(row, now)), 0)
      }, { mode: 0o600 });
      lastStatus = now;
    }
    for (const request of pending) {
      if (running.size >= concurrency) break;
      if (handled.has(request.id)) continue;
      const expiresAt = request.expires_at * 1000;
      handled.set(request.id, expiresAt);
      const timeoutMs = Math.min(60000, expiresAt - now - 1000);
      if (timeoutMs <= 0) continue;
      const controller = new AbortController();
      running.set(request.id, controller);
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      logger.info(`Muse inbox picked up ${request.id} (${operation(request)}) after ${ageMs(request, now)}ms`);
      void (async () => {
        try {
          const content = await answer(request, { timeoutMs, signal: controller.signal });
          if (stopped || Date.now() >= expiresAt) return;
          if (controller.signal.aborted) throw new Error('Muse did not answer within 60 seconds');
          await complete(root, request, { content });
          const op = operation(request);
          const latency = ageMs(request, Date.now());
          const samples = activity.get(op) || [];
          samples.push(latency);
          activity.set(op, samples);
          logger.info(`Muse inbox answered ${request.id} (${op}) in ${latency}ms`);
          if (latency > 60000) logger.warn(`Muse inbox request ${request.id} exceeded its 60-second answer target`);
        } catch (error) {
          if (stopped || Date.now() >= expiresAt) return;
          logger.warn(`Muse inbox request ${request.id} failed: ${error.message}`);
          try { await complete(root, request, { error: 'Muse inference failed for ' + request.id }); }
          catch (completionError) { logger.warn(`Could not record Muse request ${request.id} failure: ${completionError.message}`); }
        } finally {
          clearTimeout(timer);
          running.delete(request.id);
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
  return () => {
    stopped = true;
    clearInterval(interval);
    for (const controller of running.values()) controller.abort();
    try { fs.rmSync(statusFile, { force: true }); } catch { /* status expires by timestamp */ }
  };
}

async function probeInbox(env = process.env, { timeoutMs = 60000, pollMs = 250, signal } = {}) {
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

module.exports = { startResidentInbox, probeInbox, pendingRequests, completeLocally };
