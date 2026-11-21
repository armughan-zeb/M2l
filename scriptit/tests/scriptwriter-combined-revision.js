'use strict';
// P9 (fewer-requests): a short draft that also fails the editorial gate
// must get ONE combined runtime-fit call carrying both the length target
// and the quality issues — not a fit call followed by a separate revision.
const assert = require('node:assert/strict');
const { generateScript } = require('../src/agents/scriptWriter');

// Grounded mock narration: every sentence paraphrases the research fixture
// below, so the editorial gate's evidence-support check can verify it.
// (Copied from tests/external-agent.js.)
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
const FULL_DRAFT = Array.from({ length: 5 }, (_, index) => beatText(index + 1)).join('\n\n');
// One factual sentence with no support in the research: the gate flags it
// as "Unsupported factual sentence: ...".
const UNSUPPORTED_SENTENCE = 'The mission cost 500 billion dollars.';
const SHORT_DRAFT = beatText(1).split('\n\n')[0] + ' ' + UNSUPPORTED_SENTENCE;

const RESEARCH = {
  query: 'Apollo mission',
  gatheredAt: '2026-10-09T00:00:00Z',
  official: [{
    title: 'Apollo timeline', source: 'NASA',
    url: 'https://www.nasa.gov/history/apollo/',
    snippet: 'NASA documents the Apollo mission and lunar landing in 1969.',
    articleText: 'NASA documents the Apollo mission and lunar landing in 1969. The mission carried astronauts to the Moon and returned them to Earth.',
  }, {
    title: 'Apollo 11 landing', source: 'NASA', url: 'https://www.nasa.gov/history/apollo-11/',
    snippet: 'Apollo 11 launched on a Saturn V rocket on July 16, 1969.',
    articleText: 'Apollo 11 launched on a Saturn V rocket on July 16, 1969, beginning the Apollo mission to land on the Moon. Neil Armstrong and Buzz Aldrin landed the Lunar Module Eagle in the Sea of Tranquility on July 20, 1969, while Michael Collins remained in the Command Module Columbia in lunar orbit.',
  }, {
    title: 'Apollo 11 return', source: 'NASA', url: 'https://www.nasa.gov/history/apollo-11-return/',
    snippet: 'The crew splashed down in the Pacific Ocean on July 24, 1969.',
    articleText: 'Armstrong stepped onto the lunar surface first, followed by Aldrin, during the Apollo mission. The crew splashed down in the Pacific Ocean on July 24, 1969, eight days after launch.',
  }],
};

async function main() {
  const previousBudget = process.env.MUSE_SCRIPT_MAX_DRAFTS;
  // Budget 3: the old flow (fit, then revision) could spend 3 calls, so a
  // 2-call result proves the merge instead of a budget cap.
  process.env.MUSE_SCRIPT_MAX_DRAFTS = '3';
  try {
    const calls = [];
    const fakeCompletion = async ({ messages }) => {
      const user = (messages.find((message) => message.role === 'user') || {}).content || '';
      calls.push(user);
      if (calls.length === 1) return { content: SHORT_DRAFT, modelUsed: 'test-model' };
      // The combined call is told about the unsupported sentence, so the
      // model can fix it; a fit-only prompt is not, so the flaw survives
      // and the retained revision loop has to spend a third call.
      if (user.includes('Unsupported factual sentence:')) return { content: FULL_DRAFT, modelUsed: 'test-model' };
      return { content: FULL_DRAFT + '\n\n' + UNSUPPORTED_SENTENCE, modelUsed: 'test-model' };
    };
    const script = await generateScript({
      apiKey: 'test-key',
      topic: 'Apollo mission',
      duration: '10min',
      researchMode: 'thorough',
      research: RESEARCH,
      useCache: false,
      recordHistory: false,
      chatCompletionFn: fakeCompletion,
    });
    assert.equal(calls.length, 2, `short+failing draft must take 2 calls (draft + combined), took ${calls.length}`);
    assert.ok(calls[1].includes('RUNTIME FIT ONLY'), 'second call is the runtime fit');
    assert.ok(calls[1].includes('Also fix every editorial issue:'), 'fit call carries the gate issues');
    assert.ok(calls[1].includes('Unsupported factual sentence:'), 'fit call names the unsupported sentence');
    assert.equal(script.meta.publishable, true);
    assert.equal(script.meta.quality.words, BEAT_WORDS * 5);
    console.log('P9 CHECKS PASSED: short+failing draft merged into one combined call (2 scriptit.completion calls, was 3)');
  } finally {
    if (previousBudget === undefined) delete process.env.MUSE_SCRIPT_MAX_DRAFTS;
    else process.env.MUSE_SCRIPT_MAX_DRAFTS = previousBudget;
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
