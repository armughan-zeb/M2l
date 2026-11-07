'use strict';

const assert = require('assert');
const { buildEvidenceLedger, ledgerToPromptText } = require('../src/agents/evidence');
const { assessScript, openingHasHook } = require('../src/agents/scriptQuality');
const { systemMessage, buildUserPrompt } = require('../src/prompts/scriptPrompts');
const { classifyTopic } = require('../src/agents/classifier');
const { sanitizeUnsupportedSentences, evaluateDraft } = require('../src/agents/scriptWriter');
const { synthesizeScript } = require('../src/agents/scriptSynthesizer');

function source({ title, text, url, source, date = '2026-08-20' }) {
  return { title, articleText: text, url, source, date, articleDate: date, hasArticleBody: true };
}

const sufficientResearch = {
  query: 'Apollo mission',
  gatheredAt: '2026-08-26T00:00:00.000Z',
  rss: [source({
    title: 'Apollo mission archive',
    text: 'The Apollo mission archive records the mission timeline and the landing sequence in detail.',
    url: 'https://www.nasa.gov/apollo/archive',
    source: 'NASA',
  })],
  bing: [source({
    title: 'Apollo mission history',
    text: 'A detailed history of the Apollo mission explains the engineering decisions and the flight plan.',
    url: 'https://example.org/apollo-history',
    source: 'Example Journal',
  })],
  duckduckgo: [source({
    title: 'Apollo mission analysis',
    text: 'This reported analysis covers the Apollo mission and the consequences of its key decisions.',
    url: 'https://example.net/apollo-analysis',
    source: 'Example Review',
  })],
};

const ledger = buildEvidenceLedger(sufficientResearch);
assert.equal(ledger.sufficient, true);
assert.equal(ledger.sourceCount, 3);
assert.ok(ledger.primaryClaims >= 1);
assert.ok(ledgerToPromptText(ledger).includes('tier primary'));

const weakLedger = buildEvidenceLedger({
  query: 'Apollo mission',
  gatheredAt: '2026-08-26T00:00:00.000Z',
  bing: [{ title: 'Apollo mission', snippet: 'A search snippet about Apollo mission.', url: 'https://bing.example/apollo', source: 'Bing', hasArticleBody: false }],
});
assert.equal(weakLedger.sufficient, false);
assert.equal(weakLedger.claims.some((claim) => claim.source === 'Topic Context'), false);
assert.ok(weakLedger.sufficiencyReasons.length >= 1);

const hydratedPublisherLedger = buildEvidenceLedger({
  query: 'Apollo mission',
  gatheredAt: '2026-08-26T00:00:00.000Z',
  duckduckgo: [source({
    title: 'Apollo mission reporting',
    text: 'A full article body from a publisher covers the Apollo mission timeline and its engineering choices.',
    url: 'https://publisher.example/apollo-report',
    source: 'duckduckgo',
  })],
  duckduckgoNews: [source({
    title: 'Apollo mission follow-up',
    text: 'A second full article body explains the Apollo mission consequences and the decisions behind them.',
    url: 'https://news.example/apollo-follow-up',
    source: 'duckduckgo',
  })],
  bing: [source({
    title: 'Apollo mission evidence',
    text: 'A third full article body adds independent context on the Apollo mission flight plan and archive.',
    url: 'https://journal.example/apollo-evidence',
    source: 'bing',
  })],
});
assert.equal(hydratedPublisherLedger.sufficient, true);
assert.ok(hydratedPublisherLedger.claims.every((claim) => claim.sourceTier === 'publisher'));

const slop = assessScript({
  script: `Here is a comprehensive script:\n\nAs we delve into this fascinating topic, let's dive into the most important story. In today's fast-moving world, this changes everything. Based on the research, the numbers prove that this is a testament to unwavering dedication.\n\nIn conclusion, subscribe for more.`,
  wordTarget: 40,
});
assert.equal(slop.passes, false);
assert.ok(slop.metaMatches.length > 0 || slop.genericMatches.length > 0);

const humanDraft = `In 1969, one decision put Apollo 11 on a clock: land before the decade was over, or admit the race had been lost.\n\nThe mission was not a clean victory lap. It was a chain of engineering choices, each narrowing the margin for error. The archive records the landing sequence, while later histories explain why those choices mattered.\n\nThat is the useful part of the story. Apollo is remembered for the footprint, but the drama lived in the decisions that made the footprint possible.`;
assert.equal(openingHasHook(humanDraft), true);
assert.equal(assessScript({ script: humanDraft, wordTarget: 70 }).passes, true);

const irrelevantLongDraft = `The Royal Household's Daily Routine is often described as a story of engineering and design, but the narration quickly turns to prototypes, engines, blueprints, workshops, mechanical specifications, components, testing sheds, horsepower, and industrial production. Every paragraph repeats the same machinery vocabulary while making no meaningful explanation of staff, protocol, schedules, ceremonies, rooms, correspondence, or the people who carry out the routine. The result sounds like a generic history of a machine rather than an account of palace work and royal obligations.\n\n${'The prototype was tested in a workshop while engineers refined the engine, revised the blueprint, measured the component, and compared the mechanical specification. '.repeat(30)}`;
const topicFitFailure = assessScript({
  script: irrelevantLongDraft,
  topic: 'Why the Royal Household\'s Daily Routine Matters More Than You Think',
  wordTarget: 900,
});
assert.equal(topicFitFailure.passes, false);
assert.ok(topicFitFailure.topicQuality.issues.some((issue) => issue.includes('mismatch')));

const royalFallback = synthesizeScript({
  topic: 'Why the Royal Household\'s Daily Routine Matters More Than You Think',
  duration: '10min',
});
const royalFallbackWords = royalFallback.script.split(/\s+/).filter(Boolean).length;
assert.ok(royalFallbackWords >= 1200);
assert.ok(/palace|staff|protocol|schedule|ceremony|household/i.test(royalFallback.script));
assert.equal(/mechanical specification|testing shed|horsepower|chassis/i.test(royalFallback.script), false);

const templated = systemMessage({ contentType: 'documentary', voice: 'calm', customStructure: 'Cold open; reveal; consequence' });
assert.ok(templated.includes('Cold open; reveal; consequence'));
assert.ok(buildUserPrompt({ title: 'Apollo', topic: 'Apollo mission', duration: '3min', brief: 'brief', plan: 'plan', evidence: 'evidence', customStructure: 'Cold open; reveal; consequence' }).includes('CHANNEL STRUCTURE BRIEF'));
assert.equal(classifyTopic('How to build a home studio').type, 'tutorial');
assert.equal(classifyTopic('A complete review of the newest camera').type, 'review');
assert.equal(classifyTopic('The untold story documentary').type, 'documentary');
assert.equal(classifyTopic('A business case study').type, 'case-study');

const gtaEvidence = {
  claims: [{
    id: 'claim-gta1',
    text: 'Grand Theft Auto V was released for PlayStation 3 and Xbox 360 in September 2013.',
  }],
};
const mismatchedDraft = `Grand Theft Auto V was released for PlayStation 3 and Xbox 360 in September 2013. <evidence:claim-gta1>\n\nIt stands as a testament to Rockstar's ability to create immersive worlds that captivate players for years, setting a high bar for its eagerly awaited successor.\n\nThese newer versions, available on platforms like PlayStation 5, offer upgraded experiences with stunning visuals, faster loading, adaptive triggers, haptic feedback, and Tempest 3D audio. <evidence:claim-gta1>`;
const sanitizedDraft = sanitizeUnsupportedSentences(mismatchedDraft, gtaEvidence);
assert.ok(sanitizedDraft.includes('released for PlayStation 3'));
assert.ok(!sanitizedDraft.includes('testament to Rockstar'));
assert.ok(!sanitizedDraft.includes('PlayStation 5'));
const sanitizedQuality = evaluateDraft(sanitizedDraft, { wordTarget: 20, previousScripts: [], evidence: gtaEvidence, contentType: 'documentary' });
assert.equal(sanitizedQuality.supportIssues.length, 0);
assert.equal(sanitizedQuality.markerIssues.length, 0);

const noisySocialDraft = `Pull off daring co-operative Heists, enter adrenaline-fueled Stunt Races, compete in unique Adversary Modes, or hang out in social spaces. <evidence:claim-gta1>\n\nIShowSpeed could not believe GTA 6 added streamers into the game pic.\n\nSome incredible NPC behaviour 👏🏿 #GTAVI`;
assert.equal(sanitizeUnsupportedSentences(noisySocialDraft, gtaEvidence).trim(), '');

console.log('Editorial quality and evidence gate checks passed');
