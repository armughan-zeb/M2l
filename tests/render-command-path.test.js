'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { resolveRenderCommand } = require('../adapters/video');
const { resolveOverrideCommand } = require('../lib/museBridgeInfer');
const { quoteArg, runCommand } = require('../lib/command');

test('bundled relative renderer runs from a different job folder', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'muse render path '));
  try {
    const job = path.join(root, 'job');
    fs.mkdirSync(job);
    fs.writeFileSync(path.join(root, 'custom-local-render.js'), "console.log(JSON.stringify({ok:true}))");
    const result = await runCommand(resolveRenderCommand('node ./custom-local-render.js', root), { cwd: job, timeoutMs: 5000 });
    assert.equal(JSON.parse(result.stdout).ok, true);
    assert.equal(resolveRenderCommand('node custom-render.js --flag', root), 'node custom-render.js --flag');
    assert.equal(resolveRenderCommand('node ./render-vidnets-linux.js', root), '');
    assert.equal(resolveRenderCommand('node ./missing-old-wrapper.js', root), '');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('resolveOverrideCommand keeps an absolute binary plus absolute script plus args', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-override-path-'));
  try {
    const stub = path.join(root, 'stub-render.js');
    fs.writeFileSync(stub, "console.log(JSON.stringify({ok:true}))");
    // The daemon-progress case: `/usr/bin/node /abs/path/stub-render.js`
    // used to be parsed as one "path" (binary + space + script), so
    // existsSync failed and the override was silently discarded.
    const command = `${process.execPath} ${stub} --flag`;
    assert.equal(resolveOverrideCommand(command, { warnOnStale: false }), command);
    assert.equal(resolveRenderCommand(command, root), command);
    const result = await runCommand(resolveRenderCommand(command, root), { timeoutMs: 5000 });
    assert.equal(JSON.parse(result.stdout).ok, true);

    // Missing script -> override discarded, built-in adapter used.
    assert.equal(resolveOverrideCommand(`${process.execPath} ${path.join(root, 'nope.js')}`, { warnOnStale: false }), '');
    // Quoted path with spaces still works.
    const spaced = path.join(root, 'my dir');
    fs.mkdirSync(spaced);
    const spacedStub = path.join(spaced, 'render.js');
    fs.writeFileSync(spacedStub, 'x');
    const quoted = `node "${spacedStub}" --flag`;
    assert.equal(resolveOverrideCommand(quoted, { warnOnStale: false }), quoted);
    // Bare absolute script path with args.
    assert.equal(resolveOverrideCommand(`${stub} --flag`, { warnOnStale: false }), `${stub} --flag`);
    // Retired wrappers and empty input still drop.
    assert.equal(resolveOverrideCommand('node ./render-vidnets-linux.js', { repoRoot: root, warnOnStale: false }), '');
    assert.equal(resolveOverrideCommand('', { warnOnStale: false }), '');
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
