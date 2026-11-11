'use strict';

// Muse inference is compulsory: the automated worker may only start when
// the agent-bridge is verified. MUSE_AUTO_LOOP=1 is a claim mode only —
// it never exempts the inference check (an earlier version returned []
// for it, which is how the daemon ran with no inference bridge at all).

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { automatedRuntimeBlockers } = require('../lib/runtimePolicy');

function bridgeFixture({ hookEnabled = true } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-inference-'));
  const bridgeRoot = path.join(dir, 'agent-bridge');
  fs.mkdirSync(bridgeRoot, { recursive: true });
  const vidnetsRoot = path.join(dir, 'vidnets');
  fs.mkdirSync(vidnetsRoot, { recursive: true });
  fs.writeFileSync(path.join(vidnetsRoot, 'VIDNETS_AGENT.sh'), '#!/bin/sh\n');
  const hookPath = path.join(dir, 'hook.json');
  fs.writeFileSync(hookPath, JSON.stringify({ enabled: hookEnabled, id: 'vidnets-inbox-watch' }));
  return {
    dir,
    env: {
      MUSE_RENDER_COMMAND: 'node /home/hatch/workspace/M2l/render-vidnets-linux.js',
      MUSE_VIDNETS_ROOT: vidnetsRoot,
      MUSE_INBOX_HOOK_DEFINITION: hookPath,
      VIDZEO_BRIDGE_ROOT: bridgeRoot
    }
  };
}

test('automated worker refuses to start with no Muse inference bridge', () => {
  const blockers = automatedRuntimeBlockers({});
  assert.ok(blockers.length >= 1);
  assert.ok(blockers.some(blocker => /Muse inference is compulsory/i.test(blocker)));
  assert.ok(blockers.some(blocker => /render path/i.test(blocker)));
});

test('MUSE_AUTO_LOOP=1 alone does NOT bypass the inference check', () => {
  for (const env of [{ MUSE_AUTO_LOOP: '1' }, { MUSE_AUTONOMOUS: '1' }]) {
    const blockers = automatedRuntimeBlockers(env);
    assert.ok(blockers.length >= 1, 'auto-loop must not clear the blockers');
    assert.ok(blockers.some(blocker => /Muse inference is compulsory/i.test(blocker)));
  }
});

test('verified Muse bridge (Vidnets wrapper + hook + inbox) lets the worker start', () => {
  const { env } = bridgeFixture();
  assert.deepEqual(automatedRuntimeBlockers(env), []);
});

test('an OpenRouter key in the environment is refused even with a valid bridge', () => {
  const { env } = bridgeFixture();
  const blockers = automatedRuntimeBlockers({ ...env, OPENROUTER_API_KEY: 'sk-or-test-key' });
  assert.ok(blockers.some(blocker => /OpenRouter/i.test(blocker)));
  assert.ok(blockers.some(blocker => /Muse inference is compulsory/i.test(blocker)));
});

test('a disabled inbox-watch hook blocks the worker', () => {
  const { env } = bridgeFixture({ hookEnabled: false });
  const blockers = automatedRuntimeBlockers(env);
  assert.ok(blockers.some(blocker => /hook is DISABLED/i.test(blocker)));
});

test('a non-Vidnets render command does not satisfy compulsory Muse inference', () => {
  const { env } = bridgeFixture();
  const blockers = automatedRuntimeBlockers({ ...env, MUSE_RENDER_COMMAND: 'node render-linux.js' });
  assert.ok(blockers.some(blocker => /render-vidnets-linux\.js/i.test(blocker)));
});
