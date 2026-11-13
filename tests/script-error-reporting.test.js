'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { generateScript } = require('../adapters/script');
const { quoteArg } = require('../lib/command');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

test('a failed script command reports its JSON error rather than only progress', async () => {
  const saved = { ...process.env };
  const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'script-error-'));
  try {
    delete process.env.OPENROUTER_API_KEY;
    delete process.env.SCRIPTIT_API_KEY;
    delete process.env.SCRIPTIT_OPENROUTER_API_KEY;
    const code = "console.error('[scriptit-cli] 25% Research sources checked'); console.log(JSON.stringify({error:'Research source unavailable'})); process.exit(1)";
    process.env.MUSE_SCRIPT_COMMAND = `${quoteArg(process.execPath)} -e ${quoteArg(code)}`;
    const updates = [];
    await assert.rejects(
      generateScript({ jobId: 'report-error-test', targetDurationMinutes: 1 }, workDir, {
        onDetail: detail => updates.push(detail)
      }),
      /Muse script command failed: Research source unavailable/
    );
    assert.deepEqual(updates, ['Research sources checked']);
    const failure = JSON.parse(fs.readFileSync(path.join(workDir, 'script-error.json'), 'utf8'));
    assert.equal(failure.exitCode, 1);
    assert.match(failure.error, /Research source unavailable/);
  } finally {
    fs.rmSync(workDir, { recursive: true, force: true });
    for (const key of ['OPENROUTER_API_KEY', 'SCRIPTIT_API_KEY', 'SCRIPTIT_OPENROUTER_API_KEY', 'MUSE_SCRIPT_COMMAND']) {
      if (saved[key] === undefined) delete process.env[key];
      else process.env[key] = saved[key];
    }
  }
});

test('real ScriptIT CLI sends a complete structured error before exiting', () => {
  const cli = path.join(__dirname, '..', 'scriptit', 'cli', 'generate.js');
  const result = spawnSync(process.execPath, [cli], {
    input: '{broken job payload', encoding: 'utf8', timeout: 5000
  });
  assert.ifError(result.error);
  assert.equal(result.status, 1);
  assert.deepEqual(JSON.parse(result.stdout), { error: 'ScriptIT CLI: stdin is not valid JSON' });
});
