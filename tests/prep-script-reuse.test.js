'use strict';

/**
 * Script-stage prep wiring (adapters/script.js): a pre-generated script for
 * the same title + duration is reused and logged as a "prep script hit";
 * otherwise the adapter logs a "prep script miss" and proceeds to live
 * generation (which is not exercised here — no ScriptIT run in tests).
 */

const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const { generateScript } = require('../adapters/script');
const store = require('../lib/prepScriptStore');

function makeWorkDir(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'prep-script-reuse-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const prepRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'prep-root-'));
  t.after(() => fs.rmSync(prepRoot, { recursive: true, force: true }));
  process.env.VIDZEO_PREP_ROOT = prepRoot;
  return dir;
}

const job = {
  jobId: 'prep-test-job',
  title: 'Prep Test Title',
  targetDurationMinutes: 1,
  channel: { name: 'Test Channel', niche: 'Documentary' }
};

test('prep script hit: reuses the pre-generated narration and copies it into the work dir', async (t) => {
  const workDir = makeWorkDir(t);
  const text = 'word '.repeat(200).trim();
  store.savePreppedScript(job.title, 1, text, { engine: 'scriptit' });

  const details = [];
  const result = await generateScript(job, workDir, { onDetail: (detail) => details.push(detail) });

  assert.equal(result.scriptText, text);
  assert.equal(result.meta.engine, 'prep-cache');
  assert.equal(result.resumed, true);
  assert.ok(details.some((detail) => detail.includes('prep script hit')), details.join(' | '));
  // The rest of the pipeline sees a normal saved script.
  assert.equal(fs.readFileSync(path.join(workDir, 'script.txt'), 'utf8').trim(), text);
  const meta = JSON.parse(fs.readFileSync(path.join(workDir, 'script-meta.json'), 'utf8'));
  assert.equal(meta.engine, 'prep-cache');
});

test('prep script miss: logs the miss and falls through (no saved script, failing command)', async (t) => {
  const workDir = makeWorkDir(t);
  const details = [];
  const logs = [];
  const originalInfo = console.info;
  console.info = (...args) => { logs.push(args.join(' ')); };
  t.after(() => { console.info = originalInfo; });
  // Point the script command at `false` so live generation fails fast
  // instead of running ScriptIT (no agent-bridge run in tests).
  const previousCommand = process.env.MUSE_SCRIPT_COMMAND;
  const previousFallback = process.env.MUSE_SCRIPT_ALLOW_TEMPLATE_FALLBACK;
  process.env.MUSE_SCRIPT_COMMAND = 'false';
  delete process.env.MUSE_SCRIPT_ALLOW_TEMPLATE_FALLBACK;
  t.after(() => {
    if (previousCommand === undefined) delete process.env.MUSE_SCRIPT_COMMAND;
    else process.env.MUSE_SCRIPT_COMMAND = previousCommand;
    if (previousFallback === undefined) delete process.env.MUSE_SCRIPT_ALLOW_TEMPLATE_FALLBACK;
    else process.env.MUSE_SCRIPT_ALLOW_TEMPLATE_FALLBACK = previousFallback;
  });
  await assert.rejects(
    () => generateScript(job, workDir, { onDetail: (detail) => details.push(detail) }),
    /Muse script command failed|OpenRouter/
  );
  // The miss is logged to the daemon log (console.info), never to the CMS
  // progress channel — the existing error-reporting test asserts that
  // channel's exact contents.
  assert.ok(logs.some((line) => line.includes('prep script miss')), logs.join(' | '));
  assert.ok(!details.some((detail) => detail.includes('prep script miss')), details.join(' | '));
});
