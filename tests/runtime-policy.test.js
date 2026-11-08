'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { automatedRuntimeBlockers } = require('../lib/runtimePolicy');

test('automated worker refuses to claim work without a script or inference adapter', () => {
  const blockers = automatedRuntimeBlockers({});
  assert.equal(blockers.length, 2);
  assert.match(blockers[0], /MUSE_SCRIPT_COMMAND/);
  assert.match(blockers[1], /MUSE_INFERENCE_COMMAND|MUSE_RENDER_COMMAND/);
});

test('custom Muse script and renderer need no Vidnets inference bridge', () => {
  assert.deepEqual(automatedRuntimeBlockers({
    MUSE_SCRIPT_COMMAND: 'muse-script',
    MUSE_RENDER_COMMAND: 'muse-render'
  }), []);
});

test('built-in Vidnets requires an explicit Muse inference bridge', () => {
  assert.deepEqual(automatedRuntimeBlockers({
    MUSE_SCRIPT_COMMAND: 'muse-script',
    MUSE_INFERENCE_URL: 'http://127.0.0.1:9000/inference'
  }), []);
});

test('MUSE_AUTO_LOOP=1 activates auto-looping without manual intervention', () => {
  assert.deepEqual(automatedRuntimeBlockers({ MUSE_AUTO_LOOP: '1' }), []);
  assert.deepEqual(automatedRuntimeBlockers({ MUSE_AUTONOMOUS: '1' }), []);
});
