'use strict';

/**
 * Old-Vidnets-method removal (rebuild item 2).
 *
 * Guards the 92ab202 retirement: the Python server under vidnets/server/
 * is deleted, and lib/museInference.js no longer honours the retired
 * ScriptIT bridge-dir aliases. Inference goes direct to the agent-bridge
 * inbox (Muse method) — no server, no :5010, no VIDNETS_AGENT.sh.
 *
 * Deliberately NOT asserted here:
 * - VIDNETS_AGENT_BRIDGE_DIR is still honoured: the daemon itself sets it
 *   at startup (muse-agent.js) and the inbox reads it via bridgeRoot().
 *   Renaming it to MUSE_BRIDGE_ROOT is a cross-file change for the
 *   owning agent, not this removal.
 * - SCRIPTIT_API_KEY / SCRIPTIT_OPENROUTER_API_KEY stay on the
 *   OpenRouter denylist on purpose: a set ScriptIT key must still fail
 *   the compulsory-Muse gate instead of silently routing around it.
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const {
  bridgeRoot,
  checkMuseInferenceBridge,
  openRouterKeysPresent
} = require('../lib/museInference');

const repoRoot = path.join(__dirname, '..');

function tempDir(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'old-vidnets-removal-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

test('bridgeRoot ignores the retired ScriptIT bridge-dir aliases', t => {
  const home = tempDir(t);
  const scriptitDir = path.join(tempDir(t), 'agent-bridge');
  const liveDir = path.join(tempDir(t), 'agent-bridge');

  // Only a ScriptIT var set: must fall through to the default inbox,
  // never to the retired ScriptIT location.
  const fallback = bridgeRoot({ HOME: home, SCRIPTIT_AGENT_BRIDGE_DIR: scriptitDir });
  assert.notEqual(path.resolve(fallback), path.resolve(scriptitDir));
  const fallback2 = bridgeRoot({ HOME: home, SCRIPTIT_BRIDGE_ROOT: scriptitDir });
  assert.notEqual(path.resolve(fallback2), path.resolve(scriptitDir));

  // A live var wins over a ScriptIT var pointing elsewhere.
  assert.equal(
    path.resolve(bridgeRoot({ HOME: home, VIDZEO_BRIDGE_ROOT: liveDir, SCRIPTIT_AGENT_BRIDGE_DIR: scriptitDir })),
    path.resolve(liveDir)
  );
});

test('bridgeRoot still honours the live bridge-dir variables', t => {
  const home = tempDir(t);
  const dir = path.join(tempDir(t), 'agent-bridge');
  for (const key of ['VIDNETS_AGENT_BRIDGE_DIR', 'VIDZEO_BRIDGE_ROOT', 'MUSE_BRIDGE_ROOT']) {
    assert.equal(path.resolve(bridgeRoot({ HOME: home, [key]: dir })), path.resolve(dir));
  }
});

test('bridge consistency check ignores ScriptIT vars but still flags a live mismatch', t => {
  const inbox = tempDir(t);
  const other = tempDir(t);
  const base = {
    HOME: tempDir(t),
    MUSE_INFERENCE_COMMAND: 'true', // transport present: skip the hook check
    VIDZEO_BRIDGE_ROOT: inbox
  };
  // Divergent ScriptIT vars are no longer examined at all.
  const ignored = checkMuseInferenceBridge({
    ...base,
    SCRIPTIT_AGENT_BRIDGE_DIR: other,
    SCRIPTIT_BRIDGE_ROOT: other
  });
  assert.equal(ignored.ok, true, ignored.failures.join('; '));

  // A divergent live var is still flagged.
  const flagged = checkMuseInferenceBridge({ ...base, MUSE_BRIDGE_ROOT: other });
  assert.equal(flagged.ok, false);
  assert.ok(flagged.failures.some(f => f.startsWith('MUSE_BRIDGE_ROOT points to a different inference inbox')));
});

test('ScriptIT OpenRouter keys are still blocked by the compulsory-Muse gate', () => {
  assert.deepEqual(
    openRouterKeysPresent({ SCRIPTIT_API_KEY: 'sk-or-x' }),
    ['SCRIPTIT_API_KEY']
  );
  assert.deepEqual(
    openRouterKeysPresent({ SCRIPTIT_OPENROUTER_API_KEY: 'sk-or-x' }),
    ['SCRIPTIT_OPENROUTER_API_KEY']
  );
});

test('lib/museInference.js and lib/log.js carry no old-Vidnets-server references', () => {
  const forbidden = [
    'vidnets/server',
    ':5010',
    'VIDNETS_AGENT.sh',
    'muse-inference-engine',
    'adapters/vidnets',
    'museScript',
    'sfxRegistry',
    'remotionRenderer',
    'SCRIPTIT_AGENT_BRIDGE_DIR',
    'SCRIPTIT_BRIDGE_ROOT',
    'run_startup_maintenance',
    'AUTO_REQUEUE_INTERRUPTED',
    'render-vidnets-linux'
  ];
  for (const file of ['lib/museInference.js', 'lib/log.js']) {
    const text = fs.readFileSync(path.join(repoRoot, file), 'utf8');
    for (const needle of forbidden) {
      assert.ok(!text.includes(needle), `${file} still references ${needle}`);
    }
  }
});

test('retired vidnets/server tree is gone; credentials and the repo venv are preserved', () => {
  const serverDir = path.join(repoRoot, 'vidnets', 'server');
  assert.equal(fs.existsSync(path.join(serverDir, 'queue.json')), false);
  assert.equal(fs.existsSync(path.join(serverDir, 'queue.json.bak')), false);
  assert.equal(fs.existsSync(path.join(serverDir, 'services')), false);
  assert.equal(fs.existsSync(path.join(serverDir, 'web_pipeline')), false);
  assert.equal(fs.existsSync(path.join(serverDir, '__pycache__')), false);

  // Credential-adjacent files are never deleted by this removal.
  assert.equal(fs.existsSync(path.join(serverDir, 'cookies.txt')), true);
  assert.equal(fs.existsSync(path.join(repoRoot, 'vidnets', 'cookies.txt')), true);
  assert.equal(fs.existsSync(path.join(repoRoot, 'vidnets', 'cookies.txt.bak')), true);

  // The repo venv is the only python with yt_dlp; the documentary
  // adapter depends on it (see lib/python.js).
  assert.equal(fs.existsSync(path.join(repoRoot, 'vidnets', '.venv', 'bin', 'python')), true);
});
