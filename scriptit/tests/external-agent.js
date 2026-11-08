'use strict';
const assert = require('node:assert/strict');
const clientPath = require.resolve('../src/providers/agentInference');
const originalClient = require(clientPath);
const originalFetch = global.fetch;
let requests = [], fail = false, checkpoint;
require.cache[clientPath].exports = {
  isAgentHandle: originalClient.isAgentHandle,
  async agentChat(request) {
    requests.push(request);
    if (fail) throw Object.assign(new Error('Agent session ended'), { code: 'AGENT_SESSION_ENDED' });
    let content = Array(225).fill('Apollo').join(' ');
    const schema = request.responseFormat?.json_schema;
    if (schema) {
      assert.equal(schema.name, 'narration_beats', 'No reviewer requests in production');
      const id = schema.schema.properties.beats.items.properties.sourceIds.items.enum[0];
      content = JSON.stringify({ beats: Array.from({ length: 5 }, (_, i) => ({ purpose: 'Distinct development ' + i, sourceIds: [id] })) });
    } else if (JSON.stringify(request.messages || []).includes('OPENING HOOK')) {
      content = JSON.stringify({ variants: [
        { type: 'result-first', validateClick: 'Two men landed on the Moon.', raiseStakes: 'A third waited alone above.', openCuriosityLoop: 'How did they get home?' },
        { type: 'bold-claim', validateClick: 'Apollo 11 almost ran out of fuel.', raiseStakes: 'Seconds decided everything that day.', openCuriosityLoop: 'What saved the landing?' },
        { type: 'relatable-problem', validateClick: 'Imagine landing with no margin.', raiseStakes: 'Apollo 11 did exactly that.', openCuriosityLoop: 'Why did it still work?' },
      ] });
    } else if (request.messages[1]?.content.includes('Write ONLY beat')) content = beatText(Number(request.messages[1].content.match(/Write ONLY beat (\d+)/)?.[1] || 1));
    return { content, raw: { choices: [{ message: { role: 'assistant', content }, finish_reason: 'stop' }] }, modelUsed: 'external-agent', attempts: 1 };
  }
};

// Grounded mock narration: every sentence paraphrases the research fixture
// below, so the editorial gate's evidence-support check can verify it.
const BEAT_SENTENCES = [
  "In 1969, NASA's Apollo mission carried astronauts to the Moon and returned them to Earth.",
  'Apollo 11 launched from Kennedy Space Center on July 16, 1969, on a Saturn V rocket.',
  'Neil Armstrong commanded the mission, Buzz Aldrin piloted the Lunar Module Eagle, and Michael Collins stayed aboard the Command Module Columbia in lunar orbit.',
  'On July 20, 1969, Armstrong and Aldrin landed the Lunar Module Eagle in the Sea of Tranquility.',
  'Armstrong stepped onto the lunar surface first, and Aldrin followed him nineteen minutes later.',
  'The two astronauts walked on the lunar surface, then returned to the Lunar Module Eagle.',
  'Collins remained in the Command Module Columbia, circling the Moon alone while the landing took place.',
  'The crew splashed down in the Pacific Ocean on July 24, 1969, eight days after launch.',
  'NASA documents the Apollo mission and the lunar landing in 1969.',
  'Apollo 11 was the first crewed mission to land humans on the Moon.',
];
function beatText(beat) {
  const rotated = BEAT_SENTENCES.map((_, i) => BEAT_SENTENCES[(i + beat - 1) % BEAT_SENTENCES.length]);
  const doubled = [...rotated, ...rotated];
  return [doubled.slice(0, 7), doubled.slice(7, 14), doubled.slice(14)].map((p) => p.join(' ')).join('\n\n');
}
const BEAT_WORDS = beatText(1).split(/\s+/).filter(Boolean).length;

async function main() {
  global.fetch = async () => { throw new Error('Agent mode must not contact OpenRouter or another network provider'); };
  const { chatCompletion, singleChatAttempt } = require('../src/providers/openrouter');
  const handle = 'external-agent:isolated-test-session';
  const messages = [{ role: 'user', content: 'Write narration' }];
  const result = await chatCompletion({ apiKey: handle, model: 'anthropic/claude-opus-4', messages,
    fallbackModels: ['openrouter/auto'], responseFormat: { type: 'json_object' }, maxTokens: 500 });
  assert.equal(result.modelUsed, 'external-agent');
  assert.equal(requests[0].maxTokens, 500);
  assert.deepEqual(requests[0].responseFormat, { type: 'json_object' });
  let chunk;
  await singleChatAttempt({ apiKey: handle, messages, stream: true, onChunk: value => { chunk = value; }, timeoutMs: 1000 });
  assert.ok(chunk);
  fail = true;
  const before = requests.length;
  await assert.rejects(chatCompletion({ apiKey: handle, messages, maxTotalAttempts: 3 }), /Agent session ended/);
  assert.equal(requests.length, before + 1, 'Failed agent session must never become an OpenRouter retry');
  fail = false;
  const { generateScript } = require('../src/agents/scriptWriter');
  const options = { apiKey: handle, topic: 'Apollo mission', duration: '10min', researchMode: 'thorough',
    research: { query: 'Apollo mission', gatheredAt: '2026-10-09T00:00:00Z', official: [{ title: 'Apollo timeline', source: 'NASA',
      url: 'https://www.nasa.gov/history/apollo/', snippet: 'NASA documents the Apollo mission and lunar landing in 1969.',
      articleText: 'NASA documents the Apollo mission and lunar landing in 1969. The mission carried astronauts to the Moon and returned them to Earth.' },
    { title: 'Apollo 11 landing', source: 'NASA', url: 'https://www.nasa.gov/history/apollo-11/',
      snippet: 'Apollo 11 launched on a Saturn V rocket on July 16, 1969.',
      articleText: 'Apollo 11 launched on a Saturn V rocket on July 16, 1969, beginning the Apollo mission to land on the Moon. Neil Armstrong and Buzz Aldrin landed the Lunar Module Eagle in the Sea of Tranquility on July 20, 1969, while Michael Collins remained in the Command Module Columbia in lunar orbit.' },
    { title: 'Apollo 11 return', source: 'NASA', url: 'https://www.nasa.gov/history/apollo-11-return/',
      snippet: 'The crew splashed down in the Pacific Ocean on July 24, 1969.',
      articleText: 'Armstrong stepped onto the lunar surface first, followed by Aldrin, during the Apollo mission. The crew splashed down in the Pacific Ocean on July 24, 1969, eight days after launch.' }] },
    useCache: false, recordHistory: false, onDraft: draft => { checkpoint = draft.meta.generationCheckpoint; } };
  const script = await generateScript(options);
  assert.ok(requests.some(request => request.timeoutMs > 90000), 'Long-form external-agent requests must not inherit the old 90-second cutoff');
  assert.equal(script.meta.model, 'external-agent');
  assert.equal(script.meta.modelUsed, 'external-agent');
  assert.equal(script.meta.publishable, true);
  assert.equal(script.meta.quality.reviewPerformed, true);
  assert.equal(script.meta.quality.factualAudit, null);
  assert.equal(script.meta.quality.words, BEAT_WORDS * 5);
  const completed = requests.length;
  const resumed = await generateScript({ ...options, generationCheckpoint: checkpoint });
  assert.equal(resumed.script, script.script);
  assert.equal(requests.length, completed, 'Matching complete agent checkpoint resumes without inference');
  await generateScript({ ...options, duration: '1min' });
  assert.ok(requests.at(-1).timeoutMs > 90000, 'External-agent narration writing must use the extended bounded request timeout');
  console.log('EXTERNAL AGENT CHECKS PASSED: no provider traffic, honest provenance, failure isolation and checkpoint reuse');
}
main().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => {
  require.cache[clientPath].exports = originalClient; global.fetch = originalFetch;
});
