'use strict';

/**
 * Agent-bridge inference provider (file-based, no model API key needed).
 *
 * Mirrors the Vidnets agent-bridge protocol (vidnets/server/services/
 * agent_bridge.py): a request is a directory under the bridge root,
 *   <bridge>/<32-hex-id>/request.json   (state: pending)
 * and the operator answers by writing
 *   <bridge>/<32-hex-id>/response.json  ({content} or {error})
 * and flipping request.json state to "answered" (the Vidnets CLI
 * `VIDNETS_AGENT.sh answer <id> <file>` does exactly that through the
 * local server, including response_format schema validation).
 *
 * The bridge root resolves from, in order:
 *   SCRIPTIT_AGENT_BRIDGE_DIR, VIDNETS_AGENT_BRIDGE_DIR,
 *   $VIDZEO_TEMP_DIR/agent-bridge,
 *   ~/workspace/vidnets-runtime/temp/agent-bridge (Muse worker default).
 *
 * A call retries internally on expiry (new request id, same prompt) while
 * its overall timeoutMs budget lasts, so one slow operator round-trip does
 * not fail the whole script generation. Throws AgentBridgeError (retryable)
 * only when the budget is exhausted or the operator returns an error.
 *
 * Per-request wall: 3600s (MAX_WALL_MS), matching the Vidnets server floor
 * (VIDZEO_AGENT_MIN_WALL_SECONDS, default 3600s, in
 * vidnets/server/services/agent_bridge.py). The hook polls every 60s and
 * composing a grounded draft takes minutes, so a 300s wall expired while
 * the operator was still writing. NOTE: the wall is only as long as the
 * caller's budget — scriptWriter caps agent-model calls at 3600s, the
 * encyclopedic synthesizer passes 3600s, and the ScriptIT CLI passes
 * 3600s, so those caps must match the wall too. The wall is a deadline,
 * not a delay: an answer ends the wait early.
 *
 * Handle format: `agent-bridge:<owner-id>` (owner is `local-user` in
 * sign-in-free local mode). Never a model provider key.
 */

const fs = require('fs');
const path = require('path');
const os = require('os');
const crypto = require('crypto');

const PREFIX = 'agent-bridge:';
const DEFAULT_OWNER = 'local-user';
// One inbox round-trip gets the same 3600s the Vidnets server floors its
// agent requests at (VIDZEO_AGENT_MIN_WALL_SECONDS, default 3600s): the
// hook polls every 60s and a grounded draft takes minutes to compose.
// A 300s wall expired mid-composition and wasted the operator's answer.
const MAX_WALL_MS = 3600000; // 60 minutes per single inbox request
const POLL_MS = 250;

function isBridgeHandle(value) {
  return String(value || '').startsWith(PREFIX);
}

function bridgeOwner(apiKey) {
  const owner = String(apiKey || '').slice(PREFIX.length).trim();
  return owner || DEFAULT_OWNER;
}

function bridgeRoot() {
  const fromEnv = process.env.SCRIPTIT_AGENT_BRIDGE_DIR
    || process.env.VIDNETS_AGENT_BRIDGE_DIR
    || process.env.VIDZEO_BRIDGE_ROOT || process.env.MUSE_BRIDGE_ROOT || process.env.SCRIPTIT_BRIDGE_ROOT
    || (process.env.VIDZEO_TEMP_DIR ? path.join(process.env.VIDZEO_TEMP_DIR, 'agent-bridge') : '');
  if (fromEnv) return fromEnv;
  if (process.env.VIDZEO_RUNTIME_ROOT) return path.join(process.env.VIDZEO_RUNTIME_ROOT, 'temp', 'agent-bridge');
  return path.join(os.homedir(), 'workspace', 'vidnets-runtime', 'temp', 'agent-bridge');
}

function atomicWriteJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const tmp = `${filePath}.${crypto.randomBytes(8).toString('hex')}.tmp`;
  try {
    fs.writeFileSync(tmp, JSON.stringify(value, null, 2), { encoding: 'utf8', mode: 0o600 });
    fs.renameSync(tmp, filePath);
  } finally {
    try { fs.unlinkSync(tmp); } catch { /* already renamed */ }
  }
}

function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch {
    return null;
  }
}

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(Object.assign(new Error('Agent bridge request cancelled'), { name: 'AbortError' }));
      return;
    }
    const abort = () => {
      clearTimeout(timer);
      reject(Object.assign(new Error('Agent bridge request cancelled'), { name: 'AbortError' }));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', abort);
      resolve();
    }, ms);
    signal?.addEventListener('abort', abort, { once: true });
  });
}

class AgentBridgeError extends Error {
  constructor(message) {
    super(message);
    this.name = 'AgentBridgeError';
    this.code = 'AGENT_BRIDGE_TIMEOUT';
    this.retryable = true;
  }
}

/**
 * One inbox round-trip: write request.json, poll for response.json.
 * Returns the operator's content string, or throws AgentBridgeError.
 */
async function postOnce({ owner, jobId, operation, kind, messages, responseFormat, maxTokens, wallMs, signal }) {
  const root = bridgeRoot();
  const requestId = crypto.randomBytes(16).toString('hex');
  const dir = path.join(root, requestId);
  const requestPath = path.join(dir, 'request.json');
  const replyPath = path.join(dir, 'response.json');
  const now = Date.now();
  const row = {
    id: requestId,
    owner_id: owner,
    job_id: jobId || '',
    attempt_id: '',
    kind: kind === 'vision' ? 'vision' : 'text',
    operation: operation || 'scriptit.completion',
    state: 'pending',
    created_at: now / 1000,
    expires_at: (now + wallMs) / 1000,
    messages: Array.isArray(messages) ? messages : [],
    response_format: responseFormat || {},
    max_tokens_hint: maxTokens || null,
    instructions: 'Use your own reasoning. Ground facts in the supplied context. Return the requested schema. Never call OpenRouter for this request.',
  };
  atomicWriteJson(requestPath, row);
  const deadline = now + wallMs;
  try {
    for (;;) {
      if (signal?.aborted) throw Object.assign(new Error('Agent bridge request cancelled'), { name: 'AbortError' });
      const reply = readJson(replyPath);
      if (reply) {
        if (reply.error) throw new AgentBridgeError(`Muse request ${requestId} (${row.operation}) failed: ` + String(reply.error).slice(0, 300));
        if (typeof reply.content === 'string' && reply.content.trim()) return { content: reply.content, requestId };
        // Some agents write a parsed object as content instead of a JSON
        // string. Preserve it as JSON for structured callers; narration is
        // unwrapped later, only in the script-writing path.
        if (reply.content && typeof reply.content === 'object') return { content: JSON.stringify(reply.content), requestId };
        throw new AgentBridgeError(`Muse request ${requestId} (${row.operation}) returned an empty completion`);
      }
      if (Date.now() >= deadline) {
        throw new AgentBridgeError(`Muse request ${requestId} (${row.operation}) did not answer within this request budget.`);
      }
      await sleep(Math.min(POLL_MS, Math.max(10, deadline - Date.now())), signal);
    }
  } catch (err) {
    if (err instanceof AgentBridgeError) throw err;
    if (err && err.name === 'AbortError') throw err;
    throw new AgentBridgeError(err.message || String(err));
  } finally {
    if (!fs.existsSync(replyPath)) {
      try {
        const current = readJson(requestPath);
        if (current?.state === 'pending') atomicWriteJson(requestPath, { ...current, state: 'expired' });
      } catch { /* the original expiry still bounds abandoned requests */ }
    }
  }
}

/**
 * chatCompletion-compatible call through the agent bridge.
 * Same argument/result shape as providers/agentInference.js agentChat.
 */
async function agentBridgeChat({
  apiKey,
  messages,
  responseFormat,
  maxTokens,
  signal,
  timeoutMs = 3600000,
  operation = 'scriptit.completion',
  kind = 'text',
  jobId = '',
}) {
  if (!isBridgeHandle(apiKey)) throw new Error('An agent-bridge handle is required (agent-bridge:<owner>)');
  const owner = bridgeOwner(apiKey);
  // Correlate inbox requests with the calling job when the worker sets it.
  jobId = jobId || process.env.SCRIPTIT_JOB_ID || '';
  // Default budget is one full 3600s wall, matching the Vidnets server
  // floor; callers that pass a smaller timeoutMs still dominate (see the
  // header note — the ScriptIT writer/CLI caps must be raised to match).
  // The cap is two walls (7200000ms), not one: the retry loop below
  // re-files an expired request with a fresh id while budget lasts, and a
  // one-wall cap would leave it no second attempt — the loop would be dead
  // code. The wall is a deadline, not a delay: an answer ends the wait
  // early, so the second wall is only spent when the first round-trip
  // actually expires unanswered.
  const budget = Math.max(1000, Math.min(7200000, Number(timeoutMs) || 3600000));
  const startedAt = Date.now();
  let attempts = 0;
  let lastError = null;
  while (Date.now() - startedAt < budget) {
    if (signal?.aborted) throw Object.assign(new Error('Agent bridge request cancelled'), { name: 'AbortError' });
    const remaining = budget - (Date.now() - startedAt);
    if (remaining < 1000) break;
    attempts += 1;
    const wallMs = Math.min(MAX_WALL_MS, remaining);
    try {
      const { content, requestId } = await postOnce({
        owner, jobId, operation, kind, messages, responseFormat, maxTokens, wallMs, signal,
      });
      const raw = {
        id: 'agent-' + requestId,
        model: 'agent-bridge',
        provider: 'agent_bridge',
        choices: [{ message: { content }, finish_reason: 'stop' }],
        usage: {},
      };
      return { content, raw, modelUsed: 'agent-bridge', attempts };
    } catch (err) {
      lastError = err;
      if (err && err.name === 'AbortError') throw err;
      // Expiry / operator error: retry with a fresh request id while budget lasts.
      if (!(err instanceof AgentBridgeError)) throw err;
    }
  }
  throw lastError || new AgentBridgeError('The external agent did not answer within this request budget.');
}

module.exports = { PREFIX, isBridgeHandle, bridgeOwner, bridgeRoot, agentBridgeChat, AgentBridgeError, MAX_WALL_MS };
