'use strict';
const assert = require('assert');
const { gatherResearch } = require('../src/agents/researcher');
const { buildEvidenceLedger } = require('../src/agents/evidence');
const { generateScript } = require('../src/agents/scriptWriter');

async function testTopic(topic) {
  console.log(`\nTesting topic: "${topic}"`);
  const raw = await gatherResearch(topic, { includeReddit: false });
  assert(raw, 'Research object should exist');
  assert(Array.isArray(raw.duckduckgo), 'duckduckgo should be array');
  assert(Array.isArray(raw.bing), 'bing should be array');
  assert(Array.isArray(raw.wikipedia), 'wikipedia should be array');
  
  const ledger = buildEvidenceLedger(raw);
  assert(Array.isArray(ledger.sufficiencyReasons), 'Evidence ledger must explain its quality state');
  if (!ledger.sufficient) {
    assert(ledger.sufficiencyReasons.length > 0, 'Insufficient evidence must include a reason');
    console.log(`  ✓ Research gathered (${ledger.claims.length} claims, generation correctly gated: ${ledger.sufficiencyReasons.join('; ')})`);
    return;
  }
  assert(ledger.claims.length > 0, 'Evidence ledger must contain claims');
  console.log(`  ✓ Research gathered (${ledger.claims.length} claims, sufficient: true)`);

  const mockCompletion = async ({ messages }) => {
    return {
      content: `Welcome to this comprehensive breakdown on ${topic}.\n\nIn this video, we explore the essential pillars that define this area. First, understanding the foundational mechanics is critical for long-term execution. Second, systematic automation eliminates friction and accelerates output. Finally, maintaining quality assurance guarantees consistent results.\n\nSubscribe for more deep dives into advanced automation and production strategies.`
    };
  };

  const scriptResult = await generateScript({
    topic,
    apiKey: 'mock-key',
    useCache: false,
    research: raw,
    chatCompletionFn: mockCompletion
  });

  assert(scriptResult.ok !== false, 'Script generation should succeed');
  assert(typeof scriptResult.script === 'string' && scriptResult.script.length > 100, 'Script text must be valid');
  assert(!scriptResult.script.includes('<evidence:'), 'Script should not have evidence tags');
  console.log(`  ✓ Script generated (${scriptResult.meta.quality.words} words, 0 tag leaks)`);
}

async function main() {
  console.log('====================================================');
  console.log('  SCRIPTIT RESEARCH & WRITER RESILIENCE TEST');
  console.log('====================================================');

  const topics = [
    'The Secret AI Strategy That Nobody Talks About',
    'How I Built a 7-Figure Media Empire with Automation',
    '10 Quantum Computing Breakthroughs for 2026',
    'Grand Theft Auto VI Release Analysis'
  ];

  for (const topic of topics) {
    await testTopic(topic);
  }

  console.log('\n====================================================');
  console.log('  ALL SCRIPTIT RESILIENCE TESTS PASSED (0 failures)');
  console.log('====================================================\n');
  // RSS/network clients can keep sockets alive after the assertions finish.
  // This suite is a finite contract check, so terminate explicitly.
  process.exit(0);
}

main().catch(err => {
  console.error('\nTest failed:', err);
  process.exit(1);
});
