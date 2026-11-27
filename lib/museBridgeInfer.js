'use strict';

/**
 * Shared Muse inference bridge & subprocess helpers.
 *
 * Used by both Documentary (one_minute_documentary) and 2D Stickman
 * (2d-stickman) adapters so every LLM planning/script/repair step goes
 * through compulsory Muse inference — never OpenRouter.
 */

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const log = require('./log');
const { runCommand } = require('./command');
const { writeJsonAtomic } = require('./config');
const { assertNoOpenRouterKey, bridgeRoot } = require('./museInference');
const museInbox = require('./museInbox');
const { sleep } = require('./sleep');

const MAX_WALL_MS = 3600000;
const BRIDGE_POLL_MS = 250;
const REQUEST_ID_RE = /^[a-f0-9]{32}$/;

function cleanGeneratedCode(code) {
  const text = String(code || '').trim();
  const match = text.match(/^```(?:javascript|js|mjs|json|tsx|ts)?\s*\r?\n([\s\S]*?)\r?\n```\s*$/);
  return match ? match[1].trim() : text;
}

function parseJsonAnswer(rawContent, operation) {
  let content = rawContent;
  if (content && typeof content === 'object') return content;
  let text = String(content || '').trim();
  const fenced = text.match(/^```(?:json)?\s*\r?\n([\s\S]*?)\r?\n```\s*$/);
  if (fenced) text = fenced[1].trim();
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error(`Muse ${operation} returned invalid JSON: ${error.message}`);
  }
}

function runProcess(binary, args, { cwd, env, timeoutMs = 15 * 60 * 1000, signal, label = 'muse-step', onStdoutLine, onStderrLine } = {}) {
  if (signal?.aborted) return Promise.reject(new Error(`${label} was cancelled`));
  return new Promise((resolve, reject) => {
    let settled = false;
    let stdout = '';
    let stderr = '';
    let child;
    const finish = (err, result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener('abort', onAbort);
      if (err) reject(err);
      else resolve(result);
    };
    const onAbort = () => {
      if (child && !child.killed) {
        try { child.kill('SIGTERM'); } catch { /* ignore */ }
      }
      finish(new Error(`${label} was cancelled`));
    };
    try {
      child = spawn(binary, args, { cwd, env: { ...process.env, ...env }, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    } catch (error) {
      reject(error);
      return;
    }
    const timer = setTimeout(() => {
      try { child.kill('SIGKILL'); } catch { /* ignore */ }
      finish(new Error(`${label} timed out after ${Math.round(timeoutMs / 1000)}s`));
    }, Math.max(1000, Number(timeoutMs) || 600000));
    signal?.addEventListener('abort', onAbort, { once: true });

    let outBuf = '';
    let errBuf = '';
    child.stdout.on('data', (chunk) => {
      const text = chunk.toString('utf8');
      stdout = (stdout + text).slice(-2 * 1024 * 1024);
      if (onStdoutLine) {
        outBuf += text;
        const lines = outBuf.split(/[\r\n]+/);
        outBuf = lines.pop();
        for (const line of lines) if (line.trim()) onStdoutLine(line.trim());
      }
    });
    child.stderr.on('data', (chunk) => {
      const text = chunk.toString('utf8');
      stderr = (stderr + text).slice(-2 * 1024 * 1024);
      if (onStderrLine) {
        errBuf += text;
        const lines = errBuf.split(/[\r\n]+/);
        errBuf = lines.pop();
        for (const line of lines) if (line.trim()) onStderrLine(line.trim());
      }
    });
    child.on('error', (error) => finish(error));
    child.on('close', (code) => {
      if (outBuf.trim() && onStdoutLine) onStdoutLine(outBuf.trim());
      if (errBuf.trim() && onStderrLine) onStderrLine(errBuf.trim());
      if (code !== 0) {
        finish(new Error(`${label} exited with code ${code}: ${String(stderr || stdout).trim().slice(-800)}`));
      } else {
        finish(null, { code, stdout, stderr });
      }
    });
  });
}

async function postBridgeInference({ request, timeoutMs, signal }) {
  const root = bridgeRoot();
  fs.mkdirSync(root, { recursive: true });
  const id = REQUEST_ID_RE.test(String(request.id || '')) ? request.id : crypto.randomBytes(16).toString('hex');
  const dir = path.join(root, id);
  const requestPath = path.join(dir, 'request.json');
  const replyPath = path.join(dir, 'response.json');
  const startedAt = Date.now();
  const wallMs = Math.max(5000, Math.min(MAX_WALL_MS, Number(timeoutMs) || 15 * 60 * 1000));
  const row = {
    ...request,
    id,
    state: 'pending',
    created_at: startedAt / 1000,
    expires_at: (startedAt + wallMs) / 1000,
  };
  writeJsonAtomic(requestPath, row, { mode: 0o600 });
  const deadline = startedAt + wallMs;
  try {
    while (true) {
      if (signal?.aborted) throw new Error(`Muse ${row.operation || 'inference'} cancelled`);
      if (fs.existsSync(replyPath)) {
        let reply = null;
        try { reply = JSON.parse(fs.readFileSync(replyPath, 'utf8')); } catch { reply = null; }
        if (reply) {
          if (reply.error) {
            throw new Error(`Muse ${row.operation} request ${id} failed: ${String(reply.error).slice(0, 300)}`);
          }
          const content = reply.content ?? reply.text ?? reply.answer;
          if (content === undefined || content === null || String(content).trim() === '') {
            throw new Error(`Muse ${row.operation} request ${id} returned an empty response`);
          }
          return { id, content };
        }
      }
      if (Date.now() >= deadline) {
        throw new Error(`Muse ${row.operation} request ${id} timed out after ${Math.round(wallMs / 1000)}s`);
      }
      await sleep(Math.min(BRIDGE_POLL_MS, Math.max(10, deadline - Date.now())), signal);
    }
  } finally {
    if (!fs.existsSync(replyPath)) {
      try {
        const cur = JSON.parse(fs.readFileSync(requestPath, 'utf8'));
        if (cur && cur.state === 'pending') writeJsonAtomic(requestPath, { ...cur, state: 'expired' }, { mode: 0o600 });
      } catch { /* ignore */ }
    }
  }
}

async function inferWithMuse({ operation, kind = 'text', messages, responseFormat, jobId = '', workDir, timeoutMs = 15 * 60 * 1000, signal, inferFn, json = true }) {
  assertNoOpenRouterKey();
  const id = crypto.randomBytes(16).toString('hex');
  const nowSec = Date.now() / 1000;
  const wallSec = Math.max(30, Math.ceil((Number(timeoutMs) || 900000) / 1000));
  const request = {
    id,
    owner_id: String(process.env.MUSE_INBOX_OWNER || 'local-user').trim(),
    job_id: String(jobId || ''),
    attempt_id: '',
    kind,
    operation,
    messages,
    response_format: responseFormat || {},
    created_at: nowSec,
    expires_at: nowSec + wallSec,
    instructions: json
      ? 'Compose a schema-valid JSON response using your own reasoning. Never call OpenRouter.'
      : 'Compose the requested text response using your own reasoning. Never call OpenRouter.',
  };

  const formatOutput = (raw) => (json ? parseJsonAnswer(raw, operation) : String(raw || '').trim());

  if (typeof inferFn === 'function') {
    const raw = await inferFn(request, { timeoutMs, signal });
    const content = raw && typeof raw === 'object' && 'content' in raw ? raw.content : raw;
    return { id, data: formatOutput(content) };
  }

  const url = String(process.env.MUSE_INFERENCE_URL || '').trim();
  if (url) {
    const timeout = AbortSignal.timeout(timeoutMs);
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ type: 'inference', request, instructions: request.instructions, responseFormat: request.response_format }),
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
      redirect: 'error',
    });
    if (!res.ok) throw new Error(`Muse inference endpoint returned HTTP ${res.status} for ${operation}`);
    const payload = await res.json();
    if (payload?.error) throw new Error(`Muse inference endpoint failed ${operation}: ${payload.error}`);
    return { id, data: formatOutput(payload?.content ?? payload?.text ?? payload) };
  }

  const command = String(process.env.MUSE_INFERENCE_COMMAND || '').trim();
  if (command && !museInbox.isResidentInboxRunning()) {
    try {
      const result = await runCommand(command, {
        input: JSON.stringify({ type: 'inference', request, instructions: request.instructions, responseFormat: request.response_format }),
        cwd: workDir || process.cwd(),
        timeoutMs,
        signal,
        label: `Muse ${operation} command`,
        env: {
          MUSE_INFERENCE_REQUEST_ID: id,
          MUSE_INFERENCE_KIND: kind,
          MUSE_INFERENCE_OPERATION: operation,
        },
      });
      return { id, data: formatOutput(result.stdout) };
    } catch (error) {
      if (!String(error?.message || '').includes('MUSE_TRANSPORT_UNAVAILABLE')) {
        throw error;
      }
      log.warn(`Resident inference transport unavailable for ${operation}; falling back to bridge inbox`);
    }
  }

  const reply = await postBridgeInference({ request, timeoutMs, signal });
  return { id, data: formatOutput(reply.content) };
}

function createFrameProgressReporter(onProgress, { prefix, basePct, spanPct, intervalMs = 2500, logger = log } = {}) {
  let lastReportedAt = 0;
  let lastPct = basePct;
  return (done, total) => {
    const safeTotal = Math.max(1, Number(total) || 1);
    const safeDone = Math.max(0, Math.min(safeTotal, Number(done) || 0));
    const pct = basePct + Math.min(spanPct, Math.round((safeDone / safeTotal) * spanPct));
    const now = Date.now();
    if (safeDone < safeTotal && pct <= lastPct && now - lastReportedAt < intervalMs) return false;
    lastReportedAt = now;
    lastPct = pct;
    const message = `${prefix} Rendered frame ${safeDone}/${safeTotal} (${pct}%)`;
    logger?.info?.(message);
    onProgress?.(pct, message, true);
    return true;
  };
}

module.exports = {
  cleanGeneratedCode,
  parseJsonAnswer,
  runProcess,
  postBridgeInference,
  inferWithMuse,
  createFrameProgressReporter,
};
