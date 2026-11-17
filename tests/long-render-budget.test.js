'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { renderBudgetMs } = require('../lib/vidnetsSubmission');
const { buildRequest } = require('../adapters/vidnets');

test('long videos receive the same duration-scaled budget through the worker and Vidnets', () => {
  const job = { title: 'Example', targetDurationMinutes: 40, channel: {} };
  assert.equal(renderBudgetMs(job, {}), 30 * 60 * 60 * 1000);
  assert.equal(renderBudgetMs({ targetDurationMinutes: 1 }, {}), 6 * 60 * 60 * 1000);
  assert.equal(renderBudgetMs(job, { MUSE_RENDER_TIMEOUT_MS: '3600000' }), 3600000);
  const previous = process.env.MUSE_RENDER_TIMEOUT_MS;
  const keyNames = require('../lib/museInference').OPENROUTER_ENV_VARS;
  const keys = keyNames.map(name => [name, process.env[name]]);
  for (const name of keyNames) delete process.env[name];
  delete process.env.MUSE_RENDER_TIMEOUT_MS;
  try {
    assert.equal(buildRequest(job, 'narration', 'fixture').delivery_budget_seconds, 30 * 60 * 60);
  } finally {
    for (const [name, value] of keys) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
    if (previous === undefined) delete process.env.MUSE_RENDER_TIMEOUT_MS;
    else process.env.MUSE_RENDER_TIMEOUT_MS = previous;
  }
});
