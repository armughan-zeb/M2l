'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { commandLine, runCommand } = require('../lib/command');
const { submitRender, readRenderReceipt } = require('../lib/vidnetsSubmission');

function work(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-render-recovery-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

test('Vidnets submission retries a lost reply and replays the same request after restart', async t => {
  const dir = work(t);
  const filePath = path.join(dir, 'vidnets-request.json');
  const request = { script: 'Saved narration', output_package_dir: path.join(dir, 'output'), concurrency: 1 };
  const sent = [];
  const submit = async file => {
    sent.push(JSON.parse(fs.readFileSync(file, 'utf8')));
    if (sent.length === 1) throw new Error('Vidnets submit timed out');
    return { item: { id: 'render-1' } };
  };
  const options = { jobId: 'cms-job', filePath, request, submit, retryDelayMs: 1 };
  assert.equal(await submitRender(options), 'render-1');
  assert.equal(await submitRender({ ...options, request: { ...request, concurrency: 2 } }), 'render-1');
  assert.match(sent[0].request_id, /^muse-[a-f0-9]+$/);
  assert.deepEqual(sent, [sent[0], sent[0], sent[0]]);

  const receipt = path.join(dir, 'vidnets-job.json');
  fs.writeFileSync(receipt, JSON.stringify({ id: 'render-1', jobId: 'cms-job' }));
  assert.equal(readRenderReceipt(receipt, 'cms-job').id, 'render-1');
  assert.throws(() => readRenderReceipt(receipt, 'different-job'), /receipt is unreadable/);
});

test('an already cancelled stage starts no subprocess', async () => {
  let spawned = false;
  await assert.rejects(runCommand('unused-command', {
    signal: AbortSignal.abort(),
    onSpawn: () => { spawned = true; }
  }), /cancelled/);
  assert.equal(spawned, false);
});

test('long renderer progress output is retained as a tail and does not fail the stage', async t => {
  const script = path.join(work(t), 'renderer.js');
  fs.writeFileSync(script, [
    "process.stderr.write(('x'.repeat(510) + '\\n').repeat(5000));",
    "process.stderr.write('render complete\\n');",
    "console.log(JSON.stringify({ ok: true }));"
  ].join('\n'));
  let lastLine;
  const result = await runCommand(commandLine(process.execPath, [script]), {
    onStderrLine: line => { lastLine = line; },
    timeoutMs: 15000
  });
  assert.equal(lastLine, 'render complete');
  assert.equal(result.stdout, '{"ok":true}');
  assert.ok(result.stderr.length <= 2 * 1024 * 1024);
});
