'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
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

test('ScriptIT readiness detects a cache module missing from a fresh install', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-script-check-'));
  try {
    fs.mkdirSync(path.join(root, 'cli'));
    fs.writeFileSync(path.join(root, 'cli', 'generate.js'), '');
    const result = checkScriptReadiness({ root, resolve: name => name });
    assert.deepEqual(result.missing, ['ScriptIT cache module']);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
