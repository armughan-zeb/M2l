'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

test('Git tracks the relative modules required by ScriptIT source', t => {
  const root = path.resolve(__dirname, '..');
  let files;
  try {
    files = execFileSync('git', ['ls-files', '-z'], { cwd: root, encoding: 'utf8' }).split('\0').filter(Boolean);
  } catch {
    t.skip('Git metadata is unavailable; run this check in a repository checkout');
    return;
  }
  const tracked = new Set(files);
  const missing = [];
  for (const file of files.filter(name => /^scriptit\/(src|cli)\//.test(name) && /\.(?:js|cjs|mjs)$/.test(name))) {
    const text = fs.readFileSync(path.join(root, file), 'utf8');
    for (const match of text.matchAll(/require\s*\(\s*['"](\.{1,2}\/[^'"]+)['"]\s*\)/g)) {
      const target = path.posix.normalize(path.posix.join(path.posix.dirname(file), match[1]));
      const candidates = [target, ...['.js', '.cjs', '.mjs', '.json', '/index.js'].map(ext => target + ext)];
      if (!candidates.some(candidate => tracked.has(candidate))) missing.push(`${file} -> ${match[1]}`);
    }
  }
  assert.deepEqual(missing, [], 'ScriptIT needs source files missing from the Git checkout');
});
