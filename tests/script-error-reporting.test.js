'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { generateScript } = require('../adapters/script');
const { quoteArg } = require('../lib/command');

test('a failed script command reports its JSON error rather than only progress', async () => {
  const saved = { ...process.env };
  try {
    delete process.env.OPENROUTER_API_KEY;
    delete process.env.SCRIPTIT_API_KEY;
    delete process.env.SCRIPTIT_OPENROUTER_API_KEY;
    const code = "console.error('progress only'); console.log(JSON.stringify({error:'Research source unavailable'})); process.exit(1)";
    process.env.MUSE_SCRIPT_COMMAND = `${quoteArg(process.execPath)} -e ${quoteArg(code)}`;
    await assert.rejects(
      generateScript({ jobId: 'report-error-test', targetDurationMinutes: 1 }, process.cwd()),
      /Muse script command failed: Research source unavailable/
    );
  } finally {
    for (const key of ['OPENROUTER_API_KEY', 'SCRIPTIT_API_KEY', 'SCRIPTIT_OPENROUTER_API_KEY', 'MUSE_SCRIPT_COMMAND']) {
      if (saved[key] === undefined) delete process.env[key];
      else process.env[key] = saved[key];
    }
  }
});
