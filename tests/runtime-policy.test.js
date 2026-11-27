'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { automatedRuntimeBlockers } = require('../lib/runtimePolicy');

function bridgeFixture({ hookEnabled = true, useTransport = false } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-inference-'));
  const bridgeRoot = path.join(dir, 'agent-bridge');
  fs.mkdirSync(bridgeRoot, { recursive: true });
  const hookPath = path.join(dir, 'hook.json');
  fs.writeFileSync(hookPath, JSON.stringify({
    enabled: hookEnabled, id: 'vidnets-inbox-watch',
    delivery: { surface: 'side_chat', to: 'fixture-chat' }, script_path: '/fixture/vidnets-inbox-watch.sh'
  }));
  return {
    dir,
    env: {
      MUSE_REPO_ROOT: dir,
      MUSE_INBOX_HOOK_DEFINITION: hookPath,
      VIDZEO_BRIDGE_ROOT: bridgeRoot,
      ...(useTransport ? { MUSE_INFERENCE_COMMAND: 'node /fixture/spool.cjs' } : {})
    }
  };
}

test('automated worker refuses to start with no Muse inference bridge', () => {
  const blockers = automatedRuntimeBlockers({});
  assert.ok(blockers.length >= 1);
  assert.ok(blockers.some(blocker => /Muse inference is compulsory/i.test(blocker)));
});

test('MUSE_AUTO_LOOP=1 alone does NOT bypass the inference check', () => {
  for (const env of [{ MUSE_AUTO_LOOP: '1' }, { MUSE_AUTONOMOUS: '1' }]) {
    const blockers = automatedRuntimeBlockers(env);
    assert.ok(blockers.length >= 1, 'auto-loop must not clear the blockers');
    assert.ok(blockers.some(blocker => /Muse inference is compulsory/i.test(blocker)));
  }
});

test('verified Muse bridge (hook + inbox or transport + inbox) lets the worker start', () => {
  const { env } = bridgeFixture();
  assert.deepEqual(automatedRuntimeBlockers(env), []);
  const { env: withTransport } = bridgeFixture({ hookEnabled: false, useTransport: true });
  assert.deepEqual(automatedRuntimeBlockers(withTransport), []);
});

test('an OpenRouter key in the environment is refused even with a valid bridge', () => {
  const { env } = bridgeFixture();
  const blockers = automatedRuntimeBlockers({ ...env, OPENROUTER_API_KEY: 'sk-or-test-key' });
  assert.ok(blockers.some(blocker => /OpenRouter/i.test(blocker)));
  assert.ok(blockers.some(blocker => /Muse inference is compulsory/i.test(blocker)));
});

test('a disabled inbox-watch hook blocks the worker when no resident transport is configured', () => {
  const { env } = bridgeFixture({ hookEnabled: false });
  const blockers = automatedRuntimeBlockers(env);
  assert.ok(blockers.some(blocker => /hook is DISABLED/i.test(blocker)));
});
