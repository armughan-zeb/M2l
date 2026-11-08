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
}, {
  title: 'Apollo 11 landing', source: 'NASA', url: 'https://www.nasa.gov/history/apollo-11/',
  snippet: 'Apollo 11 launched on a Saturn V rocket on July 16, 1969.',
  articleText: 'Apollo 11 launched on a Saturn V rocket on July 16, 1969, beginning the Apollo mission to land on the Moon. Neil Armstrong and Buzz Aldrin landed the Lunar Module Eagle in the Sea of Tranquility on July 20, 1969, while Michael Collins remained in the Command Module Columbia in lunar orbit.'
}, {
  title: 'Apollo 11 return', source: 'NASA', url: 'https://www.nasa.gov/history/apollo-11-return/',
  snippet: 'The crew splashed down in the Pacific Ocean on July 24, 1969.',
  articleText: 'Armstrong stepped onto the lunar surface first, followed by Aldrin, during the Apollo mission. The crew splashed down in the Pacific Ocean on July 24, 1969, eight days after launch.'
}] };

// Grounded mock narration: every sentence paraphrases the research fixture
// above, so the editorial gate's evidence-support check can verify it. Each
// beat rotates the same sentence pool and is split into three paragraphs.
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

// 187 words: longer than the 1-minute target (160) so it exercises the
// "longer narration must not trigger another provider request" rule, but
// inside the gate's 1.35x length band.
const ONE_MINUTE_SCRIPT = [
  'On July 20, 1969, Neil Armstrong stepped onto the Sea of Tranquility and became the first person to walk on the Moon. Buzz Aldrin followed him nineteen minutes later, while Michael Collins waited alone in the Command Module Columbia above them.',
  'Apollo 11 had launched from Kennedy Space Center on July 16, 1969, on a Saturn V rocket. Armstrong commanded the mission, Aldrin piloted the Lunar Module Eagle, and Collins stayed in lunar orbit while the other two descended to the surface.',
  'Armstrong and Aldrin walked on the lunar surface, then returned to the Lunar Module Eagle. The crew splashed down in the Pacific Ocean on July 24, 1969, eight days after launch.',
  'NASA documents the Apollo mission and the lunar landing in 1969. The mission carried astronauts to the Moon and returned them to Earth. Apollo 11 was the first crewed mission to land humans on the Moon.',
  'The Saturn V rocket that carried Apollo 11 was the most powerful rocket NASA had ever flown. Its three stages lifted the spacecraft toward the Moon, and the Command Module Columbia carried the crew home after the landing.',
].join('\n\n');
const HOOK_JSON = JSON.stringify({
  variants: [
    { type: 'result-first', validateClick: 'Two men landed on the Moon.', raiseStakes: 'A third waited alone above.', openCuriosityLoop: 'How did they get home?' },
    { type: 'bold-claim', validateClick: 'Apollo 11 almost ran out of fuel.', raiseStakes: 'Seconds decided everything that day.', openCuriosityLoop: 'What saved the landing?' },
    { type: 'relatable-problem', validateClick: 'Imagine landing with no margin.', raiseStakes: 'Apollo 11 did exactly that.', openCuriosityLoop: 'Why did it still work?' },
  ],
});
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
    // The publishable branch asks the hook agent for scored opening variants.
    if (user.includes('OPENING HOOK')) return { content: HOOK_JSON };
    assert.ok(system.includes('WRITING CONTRACT') && system.includes('SOURCE TIER RULES'));
    assert.ok(system.includes('do not invent stakes or manufacture mystery'), 'retention guidance must stay grounded in the evidence');
    assert.ok(system.includes('VISUAL EVIDENCE-AWARE STORY CHOICE') && system.includes('A similar-era image or a neighboring event does not verify the claim.'), 'specific visual beats must be grounded in the same event as the claim');
    assert.ok(user.includes('REFERENCE TECHNIQUE') && user.includes('chronological escalation'));
    const beat = Number(user.match(/Write ONLY beat (\d+)/)?.[1]);
    assert.ok(beat >= 1 && beat <= 5);
    if (beat >= 3) assert.ok(user.includes('Apollo mission'), 'preceding saved narration is present');
    return { content: beatText(beat) };
  };
  const writerPath = require.resolve('../src/agents/scriptWriter');
  delete require.cache[writerPath];
  const { generateScript, writerOutput } = require(writerPath);
  const options = { topic: 'Apollo mission', duration: '10min', apiKey: 'offline-fixture', researchMode: 'thorough', model: selectedModel,
    research, useCache: false, recordHistory: false, referenceStructure: { technique: 'chronological escalation' },
    onDraft: draft => { checkpoint = draft.meta.generationCheckpoint; } };
  const result = await generateScript(options);
  assert.equal(requests, 7, 'one outline, five writing calls, and one hook-variant call; no audits or rewrites');
  assert.equal(result.meta.publishable, true);
  assert.equal(result.meta.model, selectedModel);
  assert.ok(observedModels.length > 0 && observedModels.every(model => model === selectedModel), 'outline and narration use the selected free model');
  assert.equal(result.meta.productionPolicy, 'prompt-led-v1');
  assert.equal(result.meta.quality.reviewPerformed, true);
  assert.equal(result.meta.quality.factualAudit, null, 'never fabricate review evidence');
  assert.equal(result.meta.quality.words, BEAT_WORDS * 5);
  assert.equal(result.meta.quality.factualAudit, null);
  assert.equal(result.meta.quality.reviewPerformed, true);
  assert.ok(Array.isArray(result.meta.hookVariants) && result.meta.hookVariants.length >= 1, 'scored hook variants attached');
  const resumed = await generateScript({ ...options, generationCheckpoint: checkpoint });
  assert.equal(resumed.script, result.script);
  assert.equal(requests, 7, 'completed narration resumes without provider requests');
  let fitCalls = 0;
  const short = await generateScript({ ...options, duration: '1min', onDraft: undefined,
    chatCompletionFn: async request => {
      fitCalls++;
      assert.ok(request.messages[0].content.includes('RUNTIME CONTRACT'));
      if (fitCalls === 2) assert.ok(request.messages[1].content.includes('RUNTIME FIT ONLY'));
      return { content: fitCalls === 1 ? ONE_MINUTE_SCRIPT : Array(150).fill('Apollo').join(' ') };
    } });
  assert.equal(fitCalls, 1, 'longer narration must not trigger another provider request');
  assert.equal(short.meta.publishable, true);
  assert.equal(short.meta.quality.words, ONE_MINUTE_SCRIPT.split(/\s+/).filter(Boolean).length);
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
  console.log('PROMPT-LED CHECKS PASSED: editorial gate, shared prompts, seven-call writing, checkpoint reuse, honest metadata and output bounds');
}
main().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => { provider.chatCompletion = original; global.fetch = originalFetch; });
