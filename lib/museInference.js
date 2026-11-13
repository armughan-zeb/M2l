'use strict';

/**
 * Muse inference is COMPULSORY for every production inference call.
 *
 * Every vision call, script-writing call and Vidnets brain / LLM call must
 * go through Muse via the external_agent agent-bridge inbox, answered by
 * a resident inference transport or the vidnets-inbox-watch hook operator. No OpenRouter, no Qwen, no
 * template script, no graphics-only render without inference. When the
 * bridge is absent the worker must fail closed, never silently degrade.
 *
 * The bridge is three things, all of which must be present:
 *   1. No OpenRouter key anywhere in the environment (a key present would
 *      let a provider path be selected instead of Muse).
 *   2. A Vidnets render path that uses the agent-bridge: MUSE_RENDER_COMMAND
 *      pointing at render-vidnets-linux.js (or an explicit MUSE_VIDNETS_*
 *      launcher), or a MUSE_INFERENCE_COMMAND / MUSE_INFERENCE_URL.
 *   3. A writable inbox and either a configured transport or an enabled host hook.
 *      The daemon and doctor prove that the inbox actually receives answers.
 *
 * All filesystem paths are overridable through env vars so tests can point
 * at fixtures instead of the live wiring.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');

const OPENROUTER_ENV_VARS = [
  'OPENROUTER_API_KEY',
  'VIDZEO_OPENROUTER_API_KEY',
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

/** OpenRouter key vars that are actually set (a bare/external-agent value does not count). */
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
      ' are set in the environment. Remove them — every vision, script and ' +
      'Vidnets brain call must go through Muse (external_agent agent-bridge inbox).'
    );
  }
}

function bridgeRoot(env = process.env) {
  const configured = String(
    env.VIDNETS_AGENT_BRIDGE_DIR || env.SCRIPTIT_AGENT_BRIDGE_DIR ||
    env.VIDZEO_BRIDGE_ROOT || env.MUSE_BRIDGE_ROOT || env.SCRIPTIT_BRIDGE_ROOT || ''
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

function hookStateDir(env = process.env) {
  const configured = String(env.MUSE_INBOX_HOOK_STATE_DIR || '').trim();
  if (configured) return configured;
  return path.join(homeDir(env), 'hooks', 'state', 'vidnets-inbox-watch');
}

function vidnetsRoot(env = process.env) {
  const configured = String(env.MUSE_VIDNETS_ROOT || '').trim();
  if (configured) return configured;
  return path.join(repoRoot(env), 'vidnets');
}

function vidnetsStatusUrl(env = process.env) {
  const configured = String(env.MUSE_VIDNETS_URL || '').trim();
  if (configured) return configured.replace(/\/+$/, '');
  return 'http://127.0.0.1:5010';
}

function renderCommand(env = process.env) {
  return String(env.MUSE_RENDER_COMMAND || '').trim();
}

function isVidnetsRenderCommand(env = process.env) {
  return path.basename(renderEntry(renderCommand(env)).replace(/\\/g, '/')) === 'render-vidnets-linux.js';
}

function renderEntry(command) {
  const match = String(command || '').match(/^\s*(?:node|node\.exe)\s+(?:"([^"]+)"|'([^']+)'|([^\s]+))/i);
  return match?.slice(1).find(Boolean) || '';
}

/**
 * Synchronous bridge check (no network). Used by the start gate, the
 * render wrapper pre-flight and the script adapter — all places that must
 * fail closed before any job is claimed or rendered.
 */
function checkMuseInferenceBridge(env = process.env) {
  const failures = [];

  const keys = openRouterKeysPresent(env);
  if (keys.length) {
    failures.push(
      'OpenRouter key(s) ' + keys.join(', ') + ' are set in the environment — ' +
      'remove them: all vision/script/brain calls must go through Muse ' +
      'via the external_agent agent-bridge inbox'
    );
  }

  const command = renderCommand(env);
  const inferenceTransport = String(env.MUSE_INFERENCE_COMMAND || env.MUSE_INFERENCE_URL || '').trim();
  const vidnetsConfigured = Boolean(
    String(env.MUSE_VIDNETS_COMMAND || '').trim() || String(env.MUSE_VIDNETS_ROOT || '').trim()
  );
  if (command) {
    // A render command wins over the Vidnets adapter, so a configured
    // MUSE_VIDNETS_ROOT does not rescue a non-Vidnets renderer: the
    // renderer itself must use Muse inference (or an explicit transport
    // must answer inference requests).
    if (!isVidnetsRenderCommand(env) && !inferenceTransport) {
      failures.push(
        'MUSE_RENDER_COMMAND is set to a renderer that does not use Muse inference ' +
        '("' + command + '"). Production renders must use render-vidnets-linux.js ' +
        '(Vidnets + Muse agent-bridge inbox) or provide MUSE_INFERENCE_COMMAND / MUSE_INFERENCE_URL.'
      );
    }
  } else if (!inferenceTransport || !vidnetsConfigured) {
    // Without a render command the Vidnets adapter is the render path, and
    // it can only answer inference through an explicit transport — a
    // launcher alone leaves every request unanswered.
    failures.push(
      'No Muse-inference render path is configured: set MUSE_RENDER_COMMAND to ' +
      'render-vidnets-linux.js (Vidnets + Muse agent-bridge inbox) or configure ' +
      'MUSE_VIDNETS_ROOT together with MUSE_INFERENCE_COMMAND / MUSE_INFERENCE_URL. ' +
      'The native Remotion engine is not a render path — it fetches no media and uses no Muse inference.'
    );
  }

  // A configured resident transport owns the inbox. Otherwise use the host hook.
  const hookPath = hookDefinitionPath(env);
  if (!inferenceTransport) try {
    const definition = JSON.parse(fs.readFileSync(hookPath, 'utf8'));
    if (definition.enabled !== true) {
      failures.push(
        'vidnets-inbox-watch hook is DISABLED in ' + hookPath +
        ' — no Muse operator will answer Vidnets inference requests. Enable it before starting the daemon.'
      );
    } else {
      const deliveryTarget = String(definition.delivery?.to || definition.delivery?.target || '').trim();
      if (!deliveryTarget || /<[^>]+>|YOUR_SIDE_CHAT_ID/i.test(deliveryTarget)) {
        failures.push('vidnets-inbox-watch has no real Muse delivery target; replace the template placeholder before starting jobs.');
      }
      const hookScript = String(definition.script_path || '').trim();
      if (!hookScript || /<EXTRACTED_DIR>|<[^>]+>/.test(hookScript)) {
        failures.push('vidnets-inbox-watch has no configured script_path; install the repository hook on the host Muse VM.');
      }
      const heartbeatPath = path.join(hookStateDir(env), 'last-run');
      const lastRun = Number(fs.readFileSync(heartbeatPath, 'utf8').trim());
      const ageMs = Date.now() - lastRun * 1000;
      const maxAgeMs = Math.max(20000, Number(env.MUSE_INBOX_HOOK_MAX_AGE_MS) || 120000);
      if (!Number.isFinite(lastRun) || lastRun <= 0 || ageMs < -30000 || ageMs > maxAgeMs) {
        failures.push(
          'vidnets-inbox-watch is enabled but has no recent execution proof at ' + heartbeatPath +
          ' (expected a run within ' + Math.round(maxAgeMs / 1000) + 's). The hook definition alone does not prove it fires.'
        );
      }
    }
  } catch (error) {
    failures.push(
      'Cannot verify the vidnets-inbox-watch hook definition/execution proof at ' + hookPath +
      ' (' + error.message + ') — the Muse inference operator cannot be confirmed.'
    );
  }

  const inbox = bridgeRoot(env);
  for (const key of ['VIDNETS_AGENT_BRIDGE_DIR', 'SCRIPTIT_AGENT_BRIDGE_DIR', 'VIDZEO_BRIDGE_ROOT', 'MUSE_BRIDGE_ROOT', 'SCRIPTIT_BRIDGE_ROOT']) {
    if (env[key] && path.resolve(env[key]) !== path.resolve(inbox)) failures.push(key + ' points to a different inference inbox');
  }
  const entry = renderEntry(command);
  if (entry && path.basename(entry.replace(/\\/g, '/')) === 'render-vidnets-linux.js') {
    const absolute = path.isAbsolute(entry) || path.win32.isAbsolute(entry);
    if (!absolute) {
      failures.push('MUSE_RENDER_COMMAND must use an absolute path to render-vidnets-linux.js; the worker runs render commands from inside each job directory: ' + entry);
    } else if (!fs.existsSync(entry)) {
      failures.push('Configured Vidnets render entry point does not exist: ' + entry);
    }
  }
  try {
    fs.accessSync(inbox, fs.constants.W_OK);
  } catch (error) {
    failures.push(
      'Agent-bridge inbox directory is not writable: ' + inbox +
      ' (' + error.message + ') — Vidnets cannot post inference requests for Muse to answer.'
    );
  }

  // The Vidnets launcher is what submits jobs to the server that owns the inbox.
  if (command || vidnetsConfigured) {
    const launcher = path.join(vidnetsRoot(env), 'VIDNETS_AGENT.sh');
    const windowsLauncher = path.join(vidnetsRoot(env), 'VIDNETS_AGENT.cmd');
    if (!fs.existsSync(launcher) && !fs.existsSync(windowsLauncher)) {
      failures.push(
        'Vidnets launcher not found under ' + vidnetsRoot(env) +
        ' — the Vidnets server that hosts the Muse inference inbox cannot be started.'
      );
    }
  }

  return { ok: failures.length === 0, failures, bridgeRoot: inbox, hookDefinition: hookPath };
}

/** Full bridge check including a live Vidnets server health probe (doctor). */
async function verifyMuseInferenceBridge(env = process.env) {
  const checked = checkMuseInferenceBridge(env);
  const failures = [...checked.failures];
  const url = vidnetsStatusUrl(env) + '/api/status';
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(5000) });
    if (!response.ok) {
      failures.push('Vidnets server is not healthy: GET ' + url + ' returned HTTP ' + response.status);
    } else {
      const status = await response.json().catch(() => ({}));
      if (status && status.ffmpeg === false) {
        failures.push('Vidnets server reports ffmpeg unavailable — it cannot render.');
      }
    }
  } catch (error) {
    failures.push('Vidnets server is unreachable at ' + vidnetsStatusUrl(env) + ' (' + error.message + ') — the Muse inference inbox lives on that server.');
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
  hookStateDir,
  vidnetsRoot,
  vidnetsStatusUrl,
  renderCommand,
  isVidnetsRenderCommand,
  renderEntry,
  checkMuseInferenceBridge,
  verifyMuseInferenceBridge
};
