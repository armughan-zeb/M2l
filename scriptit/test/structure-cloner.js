'use strict';
const assert = require('assert');
const { parseYandexResults, yandexNewsSearch } = require('../src/providers/yandex');
const { normalizeXItem, isXUrl, extractHandleFromUrl } = require('../src/providers/xSearch');
const {
  extractStructureFromVideo,
  generateScriptFromStructure,
  buildFallbackStructure
} = require('../src/agents/structureCloner');
const { generateScript } = require('../src/agents/scriptWriter');

async function testYandexScraper() {
  console.log('1. Testing Yandex real-time search and news parsers...');
  
  // Test desktop snippet HTML
  const mockDesktopHtml = `
    <ul class="serp-list">
      <li class="serp-item">
        <a class="OrganicTitle-Link" href="https://example.com/breakthrough">
          <h2 class="OrganicTitle-LinkText">Major AI Breakthrough Announced</h2>
        </a>
        <div class="OrganicText">Researchers at DeepMind unveiled a new reasoning model that solves complex logic tasks.</div>
      </li>
      <li class="serp-item">
        <a class="Link" href="https://example.com/analysis">
          <h2>Detailed Architecture Analysis</h2>
        </a>
        <div class="text-container">The new architecture combines tree search with multi-agent consensus verification.</div>
      </li>
    </ul>
  `;

  const desktopResults = parseYandexResults(mockDesktopHtml, 5);
  assert.strictEqual(desktopResults.length, 2, 'Should parse 2 desktop organic search results');
  assert.strictEqual(desktopResults[0].title, 'Major AI Breakthrough Announced');
  assert.strictEqual(desktopResults[0].url, 'https://example.com/breakthrough');
  assert.ok(desktopResults[0].snippet.includes('DeepMind'));

  // Test mobile snippet HTML
  const mockMobileHtml = `
    <div class="serp-item">
      <a class="link organic__url" href="https://mobile.example.com/news">
        <div class="organic__title">Quantum Computing Milestone Reached</div>
      </a>
      <div class="organic__text">Engineers demonstrated 1,000 logical qubits with error mitigation.</div>
    </div>
  `;
  const mobileResults = parseYandexResults(mockMobileHtml, 5);
  assert.strictEqual(mobileResults.length, 1, 'Should parse mobile search result');
  assert.strictEqual(mobileResults[0].title, 'Quantum Computing Milestone Reached');
  assert.ok(mobileResults[0].snippet.includes('1,000 logical qubits'));

  console.log('   ✓ Yandex organic and mobile parser tests passed');
}

async function testXSearchScraper() {
  console.log('2. Testing X/Twitter search discovery and parser...');

  assert.ok(isXUrl('https://x.com/elonmusk/status/1752000000000000000'), 'Should recognize x.com post URL');
  assert.ok(isXUrl('https://twitter.com/sama/status/1752000000000000001'), 'Should recognize twitter.com post URL');
  assert.strictEqual(extractHandleFromUrl('https://x.com/karpathy/status/123'), '@karpathy');

  const mockItem = {
    title: 'Elon Musk on X: "The first human patient received an implant yesterday..."',
    url: 'https://x.com/elonmusk/status/1752000000000000000',
    snippet: 'The first human patient received an implant from Neuralink yesterday and is recovering well.'
  };

  const normalized = normalizeXItem(mockItem);
  assert.strictEqual(normalized.handle, '@elonmusk');
  assert.strictEqual(normalized.category, 'social');
  assert.ok(normalized.source.includes('@elonmusk'));
  assert.ok(normalized.snippet.includes('first human patient'));

  console.log('   ✓ X search parser and URL/handle detection tests passed');
}

async function testStructureExtraction() {
  console.log('3. Testing YouTube Video Structure Cloner extraction...');

  const mockTranscript = `
    00:00:01 The greatest financial heist in history didn't involve a gun.
    00:00:05 It happened through a single line of compromised code inside a central bank.
    00:00:10 And until now, the mastermind was never identified.
    00:00:30 Let's look at how the transfer requests were authenticated.
    00:01:00 In February 2016, hackers targeted Bangladesh Bank.
    00:02:30 The printer stopped working at the exact moment thirty-five transactions were initiated.
    00:04:00 But then, a single typo in a routing address stopped one billion dollars from moving.
    00:06:00 Here is what the investigators actually found when they disassembled the malware.
  `;

  // Test deterministic fallback extractor
  const fallbackStructure = buildFallbackStructure('The $1B Bank Heist', mockTranscript);
  assert.ok(fallbackStructure.hookFormula, 'Structure should include hookFormula');
  assert.ok(Array.isArray(fallbackStructure.sequences), 'Structure should include sequences array');
  assert.ok(fallbackStructure.sequences.length >= 3, 'Should have at least 3 sequence beats');
  assert.ok(fallbackStructure.pacingProfile, 'Structure should include pacingProfile');

  // Test extraction with simulated LLM
  const mockChatCompletion = async ({ messages }) => {
    return {
      content: JSON.stringify({
        referenceTitle: 'The $1B Cyber Heist',
        hookFormula: {
          type: 'result-first',
          validateClickPattern: 'State the magnitude of the heist immediately in 3 seconds',
          raiseStakesPattern: 'Reveal the fatal security gap in 8 seconds',
          openCuriosityLoopPattern: 'Tease the single typo that changed everything'
        },
        pacingProfile: {
          cadence: 'Fast cold open followed by forensic escalation',
          sentenceRhythm: 'Short declarative hooks with complex procedural beats',
          audienceAddress: 'Investigative narrator revealing private files'
        },
        sequences: [
          {
            index: 1,
            name: 'The Silent Infiltration',
            role: 'Establish the point of attack: the printer failure and invisible transfer',
            targetShare: 0.25,
            pacing: 'High tension',
            transitionMechanism: 'Ticking clock deadline',
            retentionDevice: 'Tease the typographical error'
          },
          {
            index: 2,
            name: 'The SWIFT Compromise',
            role: 'Unpack the technical mechanism of the forged credentials',
            targetShare: 0.35,
            pacing: 'Analytical and forensic',
            transitionMechanism: 'Escalating financial stakes',
            retentionDevice: 'Pattern interrupt: how the bank staff thought it was a paper jam'
          },
          {
            index: 3,
            name: 'The Typo That Saved $800M',
            role: 'The climax: the misspelled routing word and global panic',
            targetShare: 0.25,
            pacing: 'Fast and dramatic',
            transitionMechanism: 'Consequence bridge to the casino laundering',
            retentionDevice: 'Rehook on where the remaining $81M vanished'
          },
          {
            index: 4,
            name: 'The Permanent Fallout',
            role: 'Resolve the investigation and show modern banking security consequences',
            targetShare: 0.15,
            pacing: 'Reflective and chilling',
            transitionMechanism: 'Final warning',
            retentionDevice: 'Payoff of the initial mastermind mystery'
          }
        ]
      })
    };
  };

  const extracted = await extractStructureFromVideo({
    videoUrl: 'https://www.youtube.com/watch?v=mock-heist',
    transcript: mockTranscript,
    apiKey: 'test-key',
    useCache: false,
    chatCompletionFn: mockChatCompletion
  });

  assert.ok(extracted.ok, 'Extraction should succeed');
  assert.strictEqual(extracted.structure.referenceTitle, 'The $1B Cyber Heist');
  assert.strictEqual(extracted.structure.sequences.length, 4, 'Should have 4 sequences');
  assert.strictEqual(extracted.structure.hookFormula.type, 'result-first');

  console.log('   ✓ YouTube transcript structural reverse-engineering tests passed');
  return extracted.structure;
}

async function testScriptGenerationFromClonedStructure(clonedStructure) {
  console.log('4. Testing script generation from cloned reference structure...');

  const mockEvidence = {
    sourceCount: 3,
    externalClaims: 5,
    primaryClaims: 5,
    coverage: { score: 1.0 },
    sufficient: true,
    claims: [
      { id: 'claim-1', text: 'In 1995, an amateur investor named Nick Leeson hid 800 million pounds in losses.' },
      { id: 'claim-2', text: 'Barings Bank was Britain oldest merchant bank, founded in 1762.' },
      { id: 'claim-3', text: 'Leeson used an error account numbered 88888 to hide unauthorized derivatives trades.' },
      { id: 'claim-4', text: 'The Kobe earthquake in 1995 caused the Nikkei to plummet, collapsing the position.' },
      { id: 'claim-5', text: 'Barings Bank collapsed and was sold to ING for one single pound.' }
    ]
  };

  const result = await generateScriptFromStructure({
    referenceStructure: clonedStructure,
    newTitle: 'How One Trader Destroyed a 233-Year-Old Bank',
    duration: '5min',
    voice: 'dramatic',
    evidence: mockEvidence
  });

  assert.ok(result.script, 'Should return generated script');
  assert.ok(result.meta, 'Should return generation meta');
  assert.strictEqual(result.meta.topic, 'How One Trader Destroyed a 233-Year-Old Bank');
  assert.strictEqual(result.meta.publishable, true);
  assert.ok(result.meta.hookVariants, 'Should include 3 hook variants');
  assert.ok(result.meta.wordCount >= 400, `Script should meet word target for 5min: got ${result.meta.wordCount}`);

  // Validate that the script does not contain cliches or banned stock transitions
  assert.ok(!result.script.includes('In today\'s fast-paced world'), 'Should not contain generic cliches');
  assert.ok(!result.script.includes('At the end of the day'), 'Should not contain banned phrases');

  // Verify retention metadata & annotations
  assert.ok(result.meta.retentionChecklist.hasHook, 'Should record hook in retention checklist');
  assert.ok(result.meta.retentionBlocks && result.meta.retentionBlocks.length >= 3, 'Should produce annotated retention blocks');
  assert.ok(result.meta.retentionBlocks.some((b) => b.type === 'hook'), 'Should contain hook retention block');

  console.log('   ✓ Script generation matching cloned sequences passed');
}

async function testScriptWriterIntegration(clonedStructure) {
  console.log('5. Testing scriptWriter generateScript with referenceStructure option...');

  const mockEvidence = {
    sourceCount: 2,
    externalClaims: 3,
    primaryClaims: 3,
    coverage: { score: 0.9 },
    sufficient: true,
    claims: [
      { id: 'claim-1', text: 'Enron was named America Most Innovative Company six consecutive years.' },
      { id: 'claim-2', text: 'Special purpose entities were used to conceal billions in toxic debt.' },
      { id: 'claim-3', text: 'In December 2001, Enron filed for Chapter 11 bankruptcy.' }
    ]
  };

  const generated = await generateScript({
    topic: 'The Fall of Enron',
    duration: '5min',
    referenceStructure: clonedStructure,
    research: {
      query: 'The Fall of Enron',
      gatheredAt: new Date().toISOString(),
      rss: [],
      duckduckgo: [],
      duckduckgoNews: [],
      bing: [],
      yandex: [],
      x: [],
      wikipedia: []
    }
  });

  assert.ok(generated.script, 'Integration generateScript should return script');
  assert.ok(generated.meta.engineName.includes('strategy'), 'reference guidance uses the shared writer path');
  assert.strictEqual(generated.meta.status, 'draft_ready');
  assert.strictEqual(generated.meta.publishable, false, 'reference adaptation with no evidence or credentials must never be publishable');
  assert.notStrictEqual(generated.meta.quality.factualAudit, true, 'an unexecuted factual review cannot be certified');
  assert.ok(generated.meta.hookVariants, 'Should provide hook variants in meta');
  assert.ok(generated.meta.clonedStructure, 'Should record cloned structure in meta');

  console.log('   ✓ scriptWriter integration with reference structure passed');
}

async function runAll() {
  console.log('--- Testing YouTube Video Structure Cloner & Real-time Scrapers ---');
  await testYandexScraper();
  await testXSearchScraper();
  const structure = await testStructureExtraction();
  await testScriptGenerationFromClonedStructure(structure);
  await testScriptWriterIntegration(structure);
  console.log('All Structure Cloner & Real-time Scraper tests passed successfully!\n');
}

runAll().catch((err) => {
  console.error('Test failed:', err);
  process.exit(1);
});
