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

test('a render reporting live status can run beyond the initial command window', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'muse render progress '));
  try {
    const script = path.join(root, 'active-render.js');
    fs.writeFileSync(script, [
      "const timer = setInterval(() => console.error('active'), 150);",
      "setTimeout(() => { clearInterval(timer); console.log('complete'); }, 2400);",
    ].join('\n'));
    const result = await runCommand('node ' + quoteArg(script), {
      cwd: root,
      timeoutMs: 1800,
      onStderrLine: line => line.trim() === 'active',
    });
    assert.equal(result.stdout.trim(), 'complete');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
