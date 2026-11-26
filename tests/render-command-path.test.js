'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { resolveRenderCommand } = require('../adapters/video');
const { quoteArg, runCommand } = require('../lib/command');

test('bundled relative renderer runs from a different job folder', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'muse render path '));
  try {
    const job = path.join(root, 'job');
    fs.mkdirSync(job);
    fs.writeFileSync(path.join(root, 'render-vidnets-linux.js'), "console.log(JSON.stringify({ok:true}))");
    const result = await runCommand(resolveRenderCommand('node ./render-vidnets-linux.js', root), { cwd: job, timeoutMs: 5000 });
    assert.equal(JSON.parse(result.stdout).ok, true);
    assert.equal(resolveRenderCommand('node custom-render.js --flag', root), 'node custom-render.js --flag');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('a backend-supervised render can be quiet beyond the command timeout floor', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'muse render progress '));
  try {
    const script = path.join(root, 'active-render.js');
    fs.writeFileSync(script, "setTimeout(() => console.log('complete'), 1400);\n");
    const result = await runCommand('node ' + quoteArg(script), {
      cwd: root,
      timeoutMs: null,
    });
    assert.equal(result.stdout.trim(), 'complete');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
