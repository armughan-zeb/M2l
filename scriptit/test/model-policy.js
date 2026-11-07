'use strict';

const assert = require('node:assert/strict');
const policy = require('../src/providers/modelPolicy');

assert.equal(policy.normalizeModel(), policy.MODEL);
assert.equal(policy.normalizeModel('qwen/qwen3.5-flash-02-23'), 'qwen/qwen3.5-flash-02-23');
assert.equal(policy.normalizeModel('nvidia/nemotron-3.5-super-vl:free'), policy.NEMOTRON_MODEL);
assert.equal(policy.isAllowedModel(policy.NEMOTRON_MODEL), true);
assert.equal(policy.normalizeModel('google/gemma-4-26b-a4b-it:free'), 'google/gemma-4-26b-a4b-it:free');
assert.throws(() => policy.normalizeModel('openrouter/auto'), /Qwen/);
assert.throws(() => policy.normalizeModel('auto'), /Qwen/);
assert.equal(policy.isAllowedModel('anthropic/claude-opus-4'), false);
assert.equal(policy.isAllowedModel('openrouter/auto'), false);
assert.equal(policy.isAllowedModel('openai/gpt-4o'), false);
assert.throws(() => policy.normalizeModel('anthropic/claude-opus-4'), /Qwen 3.5 Flash or a free model/);

const body = policy.lockPayload({ model: 'google/gemma-4-26b-a4b-it:free', models: ['anthropic/claude-opus-4'], messages: [] });
assert.equal(body.model, 'google/gemma-4-26b-a4b-it:free');
assert.equal(Object.hasOwn(body, 'models'), false);
assert.throws(() => policy.lockPayload({ model: 'anthropic/claude-opus-4' }), /Qwen 3.5 Flash or a free model/);

console.log('  ✓ OpenRouter model policy preserves selected free/Qwen choices and blocks paid IDs and fallback arrays');

(async () => {
  const originalFetch = global.fetch;
  const payloads = [];
  global.fetch = async (_url, options) => {
    payloads.push(JSON.parse(options.body));
    return { ok: true, json: async () => ({ choices: [{ message: { content: 'Visible output' }, finish_reason: 'stop' }] }) };
  };
  try {
    const { chatCompletion } = require('../src/providers/openrouter');
    const request = { apiKey: 'offline-fixture', model: policy.NEMOTRON_MODEL, messages: [{ role: 'user', content: 'Fixture' }], stream: false };
    assert.equal((await chatCompletion(request)).content, 'Visible output');
    assert.deepEqual(payloads[0].reasoning, { enabled: false });
    assert.equal(payloads[0].model, policy.NEMOTRON_MODEL);
    await chatCompletion({ ...request, reasoning: { effort: 'none' } });
    assert.deepEqual(payloads[1].reasoning, { effort: 'none' });
    console.log('  ✓ selected writer defaults to visible output and preserves explicit reasoning settings');
  } finally { global.fetch = originalFetch; }
})().catch(error => { console.error(error); process.exitCode = 1; });
