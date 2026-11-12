'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { resolveRenderCommand } = require('../adapters/video');
const { runCommand } = require('../lib/command');

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
