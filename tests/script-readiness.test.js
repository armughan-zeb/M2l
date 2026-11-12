'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { checkScriptReadiness } = require('../lib/scriptReadiness');

test('ScriptIT readiness reports missing runtime dependencies', () => {
  const root = path.join(__dirname, '..', 'scriptit');
  const result = checkScriptReadiness({ root, resolve(name) {
    if (name === 'rss-parser') throw new Error('not installed');
    return name;
  } });
  assert.equal(result.ok, false);
  assert.deepEqual(result.missing, ['rss-parser']);
});

test('ScriptIT readiness accepts the CLI and both runtime dependencies', () => {
  const root = path.join(__dirname, '..', 'scriptit');
  assert.equal(checkScriptReadiness({ root, resolve: name => name }).ok, true);
});
