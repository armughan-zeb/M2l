'use strict';

/**
 * Muse inference is COMPULSORY for every production inference call.
 *
 * Every script-writing, documentary shot-planning, and 2D Stickman planning
 * call goes through Muse via the agent-bridge inbox, answered by a resident
 * inference transport (MUSE_INFERENCE_COMMAND / MUSE_INFERENCE_URL) or the
 * host inbox hook. No OpenRouter, no Qwen, no silent fallback.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');
const { runCommand } = require('./command');

const OPENROUTER_ENV_VARS = [
  'OPENROUTER_API_KEY',
  'VIDZEO_OPENROUTER_API_KEY',
  // ScriptIT is retired, but its key names stay on this denylist on
  // purpose: a set ScriptIT OpenRouter key must still fail the
  // compulsory-Muse gate instead of silently routing around it.
  'SCRIPTIT_API_KEY',
  'SCRIPTIT_OPENROUTER_API_KEY'
];

const COMPULSORY_ENV_VAR = 'MUSE_INFERENCE_COMPULSORY';

function homeDir(env = process.env) {
  return String(env.HOME || os.homedir() || '/home/hatch');
}

function repoRoot(env = process.env) {
  return String(env.MUSE_REPO_ROOT || '').trim() || path.resolve(__dirname, '..');
}

function openRouterKeysPresent(env = process.env) {
  return OPENROUTER_ENV_VARS.filter(name => {
    const value = String(env[name] || '').trim();
    return value !== '' && !value.startsWith('external-agent:');
  });
}

function assertNoOpenRouterKey(env = process.env) {
  const keys = openRouterKeysPresent(env);
  if (keys.length) {
    throw new Error(
      'Muse inference is compulsory: OpenRouter key(s) ' + keys.join(', ') +
      ' are set in the environment. Remove them — every script and ' +
      'planning call must go through Muse (external_agent agent-bridge inbox).'
    );
  }
}

function bridgeRoot(env = process.env) {
  // Retired Vidnets server is gone (92ab202): the ScriptIT bridge-dir
  // aliases are no longer honoured. VIDNETS_AGENT_BRIDGE_DIR stays for
  // now — the daemon itself sets it at startup (muse-agent.js) and the
  // inbox reads it via this function — until the cross-file rename to
  // MUSE_BRIDGE_ROOT lands. Inference goes direct to the agent-bridge
  // inbox; there is no server.
  const configured = String(
    env.VIDNETS_AGENT_BRIDGE_DIR ||
    env.VIDZEO_BRIDGE_ROOT || env.MUSE_BRIDGE_ROOT || ''
  ).trim();
  if (configured) return configured;
  return path.join(env.VIDZEO_TEMP_DIR || path.join(env.VIDZEO_RUNTIME_ROOT ||
    path.join(homeDir(env), 'workspace', 'vidnets-runtime'), 'temp'), 'agent-bridge');
}

function hookDefinitionPath(env = process.env) {
  const configured = String(env.MUSE_INBOX_HOOK_DEFINITION || '').trim();
  if (configured) return configured;
  return path.join(homeDir(env), 'hooks', 'definitions', 'vidnets-inbox-watch.json');
}

function inferenceConcurrency() {
  return Math.max(1, Math.min(8, Math.floor(Number(process.env.MUSE_INBOX_CONCURRENCY) || 8)));
}

function inferenceTransport(env = process.env) {
  const command = String(env.MUSE_INFERENCE_COMMAND || '').trim();
  if (command) return { kind: 'command', command };
  const url = String(env.MUSE_INFERENCE_URL || '').trim();
  if (url) return { kind: 'http', url };
  return null;
}

function parseInferenceAnswer(text, request = {}) {
  let data = text;
  if (typeof text === 'string') {
    try { data = JSON.parse(text); } catch { /* plain text answer */ }
  }
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

async function runInferenceHttp(url, request, { timeoutMs = 120000, signal } = {}) {
  const timeout = AbortSignal.timeout(timeoutMs);
  const combinedSignal = signal ? AbortSignal.any([signal, timeout]) : timeout;
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({
      type: 'inference',
      request,
      instructions: request.instructions || '',
      responseFormat: request.response_format || {}
    }),
    signal: combinedSignal,
    redirect: 'error'
  });
  if (!response.ok) throw new Error(`Muse inference endpoint returned HTTP ${response.status}`);
  const text = await response.text();
  return parseInferenceAnswer(text, request);
}

/**
 * Synchronous bridge check (no network). Used by the start gate and doctor.
 */
function checkMuseInferenceBridge(env = process.env) {
  const failures = [];

  const keys = openRouterKeysPresent(env);
  if (keys.length) {
    failures.push(
      'OpenRouter key(s) ' + keys.join(', ') + ' are set in the environment — ' +
      'remove them: all script/planning calls must go through Muse ' +
      'via the external_agent agent-bridge inbox'
    );
  }

  const transport = inferenceTransport(env);
  const hookPath = hookDefinitionPath(env);
  if (!transport) {
    try {
      const definition = JSON.parse(fs.readFileSync(hookPath, 'utf8'));
      if (definition.enabled !== true) {
        failures.push(
          'inbox-watch hook is DISABLED in ' + hookPath +
          ' — no Muse operator will answer inference requests. Enable it or configure MUSE_INFERENCE_COMMAND/MUSE_INFERENCE_URL.'
        );
      } else {
        const deliveryTarget = String(definition.delivery?.to || definition.delivery?.target || '').trim();
        if (!deliveryTarget || /<[^>]+>|YOUR_SIDE_CHAT_ID/i.test(deliveryTarget)) {
          failures.push('inbox-watch hook has no real Muse delivery target; replace the template placeholder before starting jobs.');
        }
        const hookScript = String(definition.script_path || '').trim();
        if (!hookScript || /<EXTRACTED_DIR>|<[^>]+>/.test(hookScript)) {
          failures.push('inbox-watch hook has no configured script_path; install the repository hook on the host Muse VM.');
        }
      }
    } catch (error) {
      failures.push(
        'No Muse inference transport (MUSE_INFERENCE_COMMAND / MUSE_INFERENCE_URL) or enabled inbox hook at ' +
        hookPath + ' (' + error.message + ').'
      );
    }
  }

  const inbox = bridgeRoot(env);
  for (const key of ['VIDNETS_AGENT_BRIDGE_DIR', 'VIDZEO_BRIDGE_ROOT', 'MUSE_BRIDGE_ROOT']) {
    if (env[key] && path.resolve(env[key]) !== path.resolve(inbox)) failures.push(key + ' points to a different inference inbox');
  }
  try {
    fs.accessSync(inbox, fs.constants.W_OK);
  } catch (error) {
    failures.push(
      'Agent-bridge inbox directory is not writable: ' + inbox +
      ' (' + error.message + ') — cannot post inference requests for Muse to answer.'
    );
  }

  return { ok: failures.length === 0, failures, bridgeRoot: inbox, hookDefinition: hookPath };
}

async function verifyMuseInferenceBridge(env = process.env) {
  const checked = checkMuseInferenceBridge(env);
  const failures = [...checked.failures];
  const transport = inferenceTransport(env);
  if (transport && transport.kind === 'http') {
    try {
      const parsed = new URL(transport.url);
      const healthUrl = `${parsed.origin}/health`;
      await fetch(healthUrl, { signal: AbortSignal.timeout(3000) }).catch(() => null);
    } catch { /* probeInbox tests actual inference */ }
  }
  return { ok: failures.length === 0, failures, bridgeRoot: checked.bridgeRoot, hookDefinition: checked.hookDefinition };
}

module.exports = {
  OPENROUTER_ENV_VARS,
  COMPULSORY_ENV_VAR,
  openRouterKeysPresent,
  assertNoOpenRouterKey,
  bridgeRoot,
  hookDefinitionPath,
  inferenceConcurrency,
  inferenceTransport,
  parseInferenceAnswer,
  runInferenceCommand,
  runInferenceHttp,
  checkMuseInferenceBridge,
  verifyMuseInferenceBridge
};
