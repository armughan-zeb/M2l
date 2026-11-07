'use strict';
const assert = require('node:assert/strict');
const provider = require('../src/providers/openrouter');
const original = provider.chatCompletion;
const originalFetch = global.fetch;
let requests = 0, checkpoint, transientFailure = false;
const selectedModel = 'google/gemma-4-26b-a4b-it:free';
const observedModels = [];
const research = { query: 'Apollo mission', gatheredAt: '2026-10-08T00:00:00Z', official: [{
  title: 'Apollo mission timeline', source: 'NASA', url: 'https://www.nasa.gov/history/apollo/',
  snippet: 'NASA documents the Apollo mission and lunar landing in 1969.',
  articleText: 'NASA documents the Apollo mission and lunar landing in 1969. The mission carried astronauts to the Moon and returned them to Earth.'
}] };
async function main() {
  const { wordsForDuration } = require('../src/utils/duration');
  assert.equal(wordsForDuration('1min'), 160);
  assert.equal(wordsForDuration('10min'), 1600);
  global.fetch = async () => { throw new Error('Offline test cannot make a network request'); };
  provider.chatCompletion = async options => {
    observedModels.push(options.model);
    if (transientFailure) throw Object.assign(new Error('Provider temporarily unavailable'), { status: 429 });
    requests++;
    const schema = options.responseFormat?.json_schema;
    if (schema) {
      assert.equal(schema.name, 'narration_beats', 'production must never call review schemas');
      const ids = schema.schema.properties.beats.items.properties.sourceIds.items.enum;
      return { content: JSON.stringify({ beats: Array.from({ length: 5 }, (_, i) => ({ purpose: 'Distinct development ' + i, sourceIds: [ids[0]] })) }) };
    }
    const system = options.messages[0].content;
    const user = options.messages[1].content;
    assert.ok(system.includes('WRITING CONTRACT') && system.includes('SOURCE TIER RULES'));
    assert.ok(system.includes('do not invent stakes or manufacture mystery'), 'retention guidance must stay grounded in the evidence');
    assert.ok(system.includes('VISUAL EVIDENCE-AWARE STORY CHOICE') && system.includes('A similar-era image or a neighboring event does not verify the claim.'), 'specific visual beats must be grounded in the same event as the claim');
    assert.ok(user.includes('REFERENCE TECHNIQUE') && user.includes('chronological escalation'));
    const beat = Number(user.match(/Write ONLY beat (\d+)/)?.[1]);
    assert.ok(beat >= 1 && beat <= 5);
    if (beat >= 3) assert.ok(user.includes('Apollo mission'), 'preceding saved narration is present');
    return { content: Array.from({ length: 64 }, () => 'Apollo mission context advances clearly.').join(' ') };
  };
  const writerPath = require.resolve('../src/agents/scriptWriter');
  delete require.cache[writerPath];
  const { generateScript, writerOutput } = require(writerPath);
  const options = { topic: 'Apollo mission', duration: '10min', apiKey: 'offline-fixture', researchMode: 'thorough', model: selectedModel,
    research, useCache: false, recordHistory: false, referenceStructure: { technique: 'chronological escalation' },
    onDraft: draft => { checkpoint = draft.meta.generationCheckpoint; } };
  const result = await generateScript(options);
  assert.equal(requests, 6, 'one outline and five writing calls; no audits or rewrites');
  assert.equal(result.meta.publishable, true);
  assert.equal(result.meta.model, selectedModel);
  assert.ok(observedModels.length > 0 && observedModels.every(model => model === selectedModel), 'outline and narration use the selected free model');
  assert.equal(result.meta.productionPolicy, 'prompt-led-v1');
  assert.equal(result.meta.quality.reviewPerformed, false);
  assert.equal(result.meta.quality.factualAudit, null, 'never fabricate review evidence');
  assert.equal(result.meta.quality.words, 1600);
  assert.equal(result.meta.quality.factualAudit, null);
  assert.equal(result.meta.quality.reviewPerformed, false);
  const resumed = await generateScript({ ...options, generationCheckpoint: checkpoint });
  assert.equal(resumed.script, result.script);
  assert.equal(requests, 6, 'completed narration resumes without provider requests');
  let fitCalls = 0;
  const short = await generateScript({ ...options, duration: '1min', onDraft: undefined,
    chatCompletionFn: async request => {
      fitCalls++;
      assert.ok(request.messages[0].content.includes('RUNTIME CONTRACT'));
      if (fitCalls === 2) assert.ok(request.messages[1].content.includes('RUNTIME FIT ONLY'));
      return { content: Array(fitCalls === 1 ? 225 : 150).fill('Apollo').join(' ') };
    } });
  assert.equal(fitCalls, 1, 'longer narration must not trigger another provider request');
  assert.equal(short.meta.publishable, true);
  assert.equal(short.meta.quality.words, 225);
  let longBeatCalls = 0;
  const longBeats = await require('../src/agents/sectionWriter').writeSections({ topic: 'Apollo mission', wordTarget: 1500,
    evidenceText: 'claim-a | Apollo mission source record', complete: async request => {
      longBeatCalls++;
      if (request.responseFormat) return { content: JSON.stringify({ beats: Array.from({ length: 5 }, () => ({ purpose: 'Documented context', sourceIds: ['claim-a'] })) }) };
      return { content: Array(500).fill('Apollo').join(' ') };
    } });
  assert.equal(longBeatCalls, 6, 'longer beats must not trigger compression or retries');
  assert.equal(writerOutput(longBeats.content, 1500).words, 2500);
  transientFailure = true;
  await assert.rejects(generateScript({ ...options, duration: '1min', onDraft: undefined }), error => error.status === 429,
    'transport failures retain their retryable classification instead of becoming empty-script rejection');
  assert.equal(writerOutput('', 1500).ok, false);
  assert.equal(writerOutput('short incomplete output', 1500).ok, false);
  assert.equal(writerOutput(Array(1420).fill('documented').join(' '), 1600).ok, true, 'a useful long script above the supported minimum passes without a needless expansion');
  assert.equal(writerOutput(Array(2100).fill('word').join(' '), 1500).ok, true);
  console.log('PROMPT-LED CHECKS PASSED: no reviewers, shared prompts, six-call writing, checkpoint reuse, honest metadata and output bounds');
}
main().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => { provider.chatCompletion = original; global.fetch = originalFetch; });
