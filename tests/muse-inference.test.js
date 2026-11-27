'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const {
  openRouterKeysPresent,
  assertNoOpenRouterKey,
  checkMuseInferenceBridge,
  verifyMuseInferenceBridge
} = require('../lib/museInference');

const PROVIDER_KEY_NAMES = ['OPENROUTER_API_KEY', 'VIDZEO_OPENROUTER_API_KEY', 'SCRIPTIT_API_KEY', 'SCRIPTIT_OPENROUTER_API_KEY'];
const savedProviderKeys = Object.fromEntries(PROVIDER_KEY_NAMES.map(name => [name, process.env[name]]));
test.before(() => { for (const name of PROVIDER_KEY_NAMES) delete process.env[name]; });
test.after(() => {
  for (const [name, value] of Object.entries(savedProviderKeys)) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
});

function bridgeFixture({ hookEnabled = true } = {}) {
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
      VIDZEO_BRIDGE_ROOT: bridgeRoot
    }
  };
}

test('OpenRouter keys are detected, external-agent handles are not', () => {
  assert.deepEqual(openRouterKeysPresent({ OPENROUTER_API_KEY: 'sk-or-x' }), ['OPENROUTER_API_KEY']);
  assert.deepEqual(openRouterKeysPresent({ VIDZEO_OPENROUTER_API_KEY: 'sk-or-x', SCRIPTIT_API_KEY: '' }), ['VIDZEO_OPENROUTER_API_KEY']);
  assert.deepEqual(openRouterKeysPresent({ OPENROUTER_API_KEY: 'external-agent:local-user' }), []);
  assert.deepEqual(openRouterKeysPresent({}), []);
  assert.throws(() => assertNoOpenRouterKey({ OPENROUTER_API_KEY: 'sk-or-x' }), /Muse inference is compulsory/i);
});

test('bridge check passes for valid wiring and fails without inbox or transport', async () => {
  const { env } = bridgeFixture();
  assert.equal(checkMuseInferenceBridge(env).ok, true);
  assert.equal((await verifyMuseInferenceBridge(env)).ok, true);

  const noInbox = checkMuseInferenceBridge({
    ...env,
    VIDZEO_BRIDGE_ROOT: path.join(env.VIDZEO_BRIDGE_ROOT, 'does-not-exist')
  });
  assert.equal(noInbox.ok, false);
  assert.ok(noInbox.failures.some(failure => /inbox/i.test(failure)));

  const keySet = checkMuseInferenceBridge({ ...env, OPENROUTER_API_KEY: 'sk-or-x' });
  assert.equal(keySet.ok, false);
  assert.ok(keySet.failures.some(failure => /OpenRouter/i.test(failure)));
});

test('hook placeholders are rejected even when enabled', () => {
  const { env } = bridgeFixture();
  fs.writeFileSync(env.MUSE_INBOX_HOOK_DEFINITION, JSON.stringify({
    id: 'vidnets-inbox-watch', enabled: true,
    delivery: { surface: 'side_chat', to: '<YOUR_SIDE_CHAT_ID>' },
    script_path: '<EXTRACTED_DIR>/docker/hooks/vidnets-inbox-watch.sh'
  }));
  const result = checkMuseInferenceBridge(env);
  assert.equal(result.ok, false);
  assert.ok(result.failures.some(failure => /real Muse delivery target/i.test(failure)));
  assert.ok(result.failures.some(failure => /script_path/i.test(failure)));
});

test('documentary and stickman adapters both refuse OpenRouter keys', async () => {
  const documentary = require('../adapters/documentary');
  const stickman = require('../adapters/stickman');
  const prev = process.env.OPENROUTER_API_KEY;
  process.env.OPENROUTER_API_KEY = 'sk-or-forbidden';
  try {
    await assert.rejects(
      () => documentary.render({ jobId: 'j_doc', productionEngine: 'documentary' }, 'Narration.', os.tmpdir()),
      /Muse inference is compulsory/i
    );
    await assert.rejects(
      () => stickman.render({ jobId: 'j_stick', productionEngine: 'stickman' }, 'Narration.', os.tmpdir()),
      /Muse inference is compulsory/i
    );
  } finally {
    if (prev === undefined) delete process.env.OPENROUTER_API_KEY;
    else process.env.OPENROUTER_API_KEY = prev;
  }
});
