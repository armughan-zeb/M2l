'use strict';

// Tests for the museScript engine (lib/museScript.js) — the Muse-search
// replacement for ScriptIT as the default narration engine — and for the
// engine routing in adapters/script.js.
//
// Research and bridge calls are injected mocks: no network, no inbox, no
// ScriptIT CLI is touched by the muse-path tests. The bridge mock answers
// with grounded sentences copied from the mock ledger claims (with valid
// evidence markers), so the reused ScriptIT editorial gate can pass.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const {
  SECTION_WORDS,
  countWords,
  targetWordsFor,
  planSections,
  buildLedgerFacts,
  ledgerFactsText,
  buildSectionMessages,
  cleanSectionText,
  concatenateSections,
  generateMuseScript,
} = require('../lib/museScript');

const CLAIMS = [
  {
    id: 'claim-aaa111',
    text: 'Rockstar Games confirmed Grand Theft Auto VI on February 4 2022 after preliminary work began in 2014.',
    source: 'Rockstar Games',
    url: 'https://www.rockstargames.com/newswire/article/123',
    publishedAt: '2022-02-04',
  },
  {
    id: 'claim-bbb222',
    text: 'Grand Theft Auto VI is set in Leonida, a fictional state inspired by Florida, with Vice City at its centre.',
    source: 'IGN',
    url: 'https://www.ign.com/articles/gta-6-map',
    publishedAt: '2023-12-05',
  },
  {
    id: 'claim-ccc333',
    text: 'The map of Leonida covers Vice City and the surrounding wetlands, countryside and coast in detail.',
    source: 'IGN',
    url: 'https://www.ign.com/articles/gta-6-leonida-map',
    publishedAt: '2023-12-05',
  },
];

const EVIDENCE = {
  claims: CLAIMS,
  sufficient: true,
  sourceCount: 2,
  coverage: null,
  gatheredAt: '2026-11-21T00:00:00.000Z',
  latestClaimDate: '2023-12-05',
};

const EMPTY_EVIDENCE = {
  claims: [],
  sufficient: false,
  sourceCount: 0,
  coverage: null,
  gatheredAt: '2026-11-21T00:00:00.000Z',
  latestClaimDate: '',
};

const TITLE = 'How Rockstar Built the Most Detailed Map in Gaming History';

function makeJob(minutes) {
  return {
    jobId: 'test-muse-script',
    title: TITLE,
    premise: 'Grand Theft Auto VI represents over a decade of continuous engineering and map design.',
    targetDurationMinutes: minutes,
    channel: { name: 'Test Channel', niche: 'Documentary' },
  };
}

// Grounded section prose: the ledger claim sentences, cycled across three
// paragraphs so the editorial gate sees hook + concrete entities +
// paragraph breaks. Each sentence carries its valid evidence marker.
const SENTENCES = [
  'Rockstar Games confirmed Grand Theft Auto VI on February 4 2022 after preliminary work began in 2014. <evidence:claim-aaa111>',
  'Grand Theft Auto VI is set in Leonida, a fictional state inspired by Florida, with Vice City at its centre. <evidence:claim-bbb222>',
  'The map of Leonida covers Vice City and the surrounding wetlands, countryside and coast in detail. <evidence:claim-ccc333>',
];

function sectionText(targetWords) {
  const paragraphs = [[], [], []];
  let words = 0;
  let i = 0;
  while (words < targetWords) {
    const sentence = SENTENCES[i % SENTENCES.length];
    paragraphs[i % 3].push(sentence);
    words += sentence.split(/\s+/).filter(Boolean).length;
    i += 1;
  }
  return paragraphs.map((p) => p.join(' ')).join('\n\n');
}

function mockResearch(evidence = EVIDENCE) {
  let calls = 0;
  const fn = async () => {
    calls += 1;
    return { evidence, research: { query: TITLE }, warnings: [] };
  };
  fn.calls = () => calls;
  return fn;
}

function mockBridge({ failSections = new Map(), delayMs = 20 } = {}) {
  const calls = [];
  let active = 0;
  let maxActive = 0;
  const fn = async ({ section }) => {
    active += 1;
    maxActive = Math.max(maxActive, active);
    calls.push(section.index);
    try {
      await new Promise((resolve) => setTimeout(resolve, delayMs));
      const failsLeft = failSections.get(section.index) || 0;
      if (failsLeft > 0) {
        failSections.set(section.index, failsLeft - 1);
        throw new Error(`mock bridge failure for section ${section.index + 1}`);
      }
      return { content: sectionText(section.targetWords + 60) };
    } finally {
      active -= 1;
    }
  };
  fn.calls = calls;
  fn.maxActive = () => maxActive;
  return fn;
}

// --- unit: chunking ---

test('planSections splits 2100 words into 5 even sections of 420', () => {
  const sections = planSections(2100);
  assert.equal(sections.length, 5);
  assert.deepEqual(sections.map((s) => s.targetWords), [420, 420, 420, 420, 420]);
  assert.equal(sections.reduce((n, s) => n + s.targetWords, 0), 2100);
  assert.match(sections[0].brief, /HOOK/);
  assert.match(sections[4].brief, /RESOLUTION/);
  assert.match(sections[2].brief, /CORE BEAT/);
});

test('planSections never leaves a stub section', () => {
  assert.deepEqual(planSections(500).map((s) => s.targetWords), [500]);
  assert.deepEqual(planSections(501).map((s) => s.targetWords), [251, 250]);
  assert.deepEqual(planSections(210).map((s) => s.targetWords), [210]);
  assert.equal(planSections(2100, SECTION_WORDS).length, 5);
});

test('targetWordsFor uses the 210 wpm script pace', () => {
  assert.equal(targetWordsFor({ targetDurationMinutes: 10 }), 2100);
  assert.equal(targetWordsFor({ targetDurationMinutes: 1 }), 210);
  assert.equal(targetWordsFor({ targetDurationMinutes: 8 }), 1680);
});

// --- unit: ledger builder ---

test('buildLedgerFacts caps at 20 facts and truncates long claim text', () => {
  const many = Array.from({ length: 25 }, (_, i) => ({
    id: `claim-${String(i).padStart(6, '0')}`,
    text: `Fact number ${i} `.repeat(40),
    source: 'Test Source',
    url: `https://example.com/${i}`,
    publishedAt: '2024-01-01',
  }));
  const facts = buildLedgerFacts({ claims: many });
  assert.equal(facts.length, 20);
  assert.ok(facts.every((f) => f.text.length <= 300));
  assert.equal(facts[0].id, 'claim-000000');
  assert.equal(facts[0].source, 'Test Source');
});

test('buildLedgerFacts drops claims without an id or text', () => {
  const facts = buildLedgerFacts({ claims: [{ id: '', text: 'x' }, { id: 'claim-ok1', text: '' }, CLAIMS[0]] });
  assert.deepEqual(facts.map((f) => f.id), ['claim-aaa111']);
});

test('ledgerFactsText carries claim ids when grounded and the P4 label when not', () => {
  const facts = buildLedgerFacts(EVIDENCE);
  const grounded = ledgerFactsText(facts, true);
  assert.match(grounded, /claim-aaa111/);
  assert.match(grounded, /Rockstar Games/);
  const ungrounded = ledgerFactsText([], false);
  assert.match(ungrounded, /UNGROUNDED SCRIPT \(P4\)/);
  assert.match(ungrounded, /do not state specific dates/);
});

// --- unit: section prompts ---

test('buildSectionMessages grounds each section in the ledger with its brief', () => {
  const facts = buildLedgerFacts(EVIDENCE);
  const sections = planSections(2100);
  const [system, user] = buildSectionMessages({
    job: makeJob(10), facts, section: sections[0], totalSections: 5, totalTargetWords: 2100, grounded: true,
  });
  assert.match(system.content, /GROUNDING RULES/);
  assert.match(system.content, /EVIDENCE MARKERS/);
  assert.match(system.content, /Do not wrap narration in JSON/);
  assert.match(user.content, new RegExp(TITLE.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  assert.match(user.content, /section 1 of 5/);
  assert.match(user.content, /Write about 420 words/);
  assert.match(user.content, /OPENING HOOK/);
  assert.match(user.content, /claim-bbb222/);
});

test('buildSectionMessages uses the ungrounded block when research found nothing', () => {
  const sections = planSections(210);
  const [, user] = buildSectionMessages({
    job: makeJob(1), facts: [], section: sections[0], totalSections: 1, totalTargetWords: 210, grounded: false,
  });
  assert.match(user.content, /NO VERIFIED RESEARCH AVAILABLE/);
  assert.doesNotMatch(user.content, /claim-aaa111/);
});

// --- unit: cleaning / concatenation ---

test('cleanSectionText strips evidence markers and markdown, keeps the words', () => {
  const raw = '# Section 1\n\nFirst sentence here. <evidence:claim-aaa111>\n\n**Bold claim** with more words here.';
  const cleaned = cleanSectionText(raw);
  assert.doesNotMatch(cleaned, /evidence:/);
  assert.doesNotMatch(cleaned, /#/);
  assert.doesNotMatch(cleaned, /\*\*/);
  assert.match(cleaned, /First sentence here\./);
  assert.equal(countWords(cleaned), countWords('First sentence here. Bold claim with more words here.'));
});

test('concatenateSections orders by section index regardless of arrival order', () => {
  const joined = concatenateSections([
    { index: 2, text: 'third.' },
    { index: 0, text: 'first.' },
    { index: 1, text: 'second.' },
  ]);
  assert.equal(joined, 'first.\n\nsecond.\n\nthird.');
});

// --- integration: generateMuseScript with mocked research + bridge ---

test('generateMuseScript returns a valid grounded script without invoking ScriptIT', async () => {
  const research = mockResearch();
  const bridge = mockBridge();
  const job = makeJob(1);
  const result = await generateMuseScript(job, os.tmpdir(), { researchFn: research, bridgeFn: bridge });
  assert.equal(result.meta.engine, 'muse-search');
  assert.equal(result.meta.grounded, true);
  assert.equal(result.meta.ungrounded, false);
  assert.equal(result.meta.sections, 1);
  assert.equal(result.meta.sectionsFailed, 0);
  assert.equal(result.meta.researchClaims, 3);
  assert.equal(result.meta.researchSources, 2);
  assert.equal(research.calls(), 1);
  assert.equal(bridge.calls.length, 1);
  assert.ok(result.words >= 127, `expected >= 127 words, got ${result.words}`);
  assert.doesNotMatch(result.scriptText, /evidence:/);
  assert.match(result.scriptText, /Rockstar Games/);
  // The reused ScriptIT editorial gate ran on the full draft.
  assert.equal(result.meta.quality.reviewPerformed, true);
  assert.equal(result.meta.quality.factualAudit, null);
});

test('generateMuseScript issues section requests in parallel', async () => {
  const research = mockResearch();
  const bridge = mockBridge({ delayMs: 30 });
  const job = makeJob(3); // 630 words -> 2 sections
  const result = await generateMuseScript(job, os.tmpdir(), { researchFn: research, bridgeFn: bridge });
  assert.equal(result.meta.sections, 2);
  assert.equal(bridge.calls.length, 2);
  assert.ok(bridge.maxActive() > 1, `expected parallel bridge calls, maxActive=${bridge.maxActive()}`);
  assert.ok(result.words >= 382, `expected >= 382 words, got ${result.words}`);
});

test('generateMuseScript retries a failed section once and completes', async () => {
  const research = mockResearch();
  const bridge = mockBridge({ failSections: new Map([[0, 1]]) });
  const result = await generateMuseScript(makeJob(1), os.tmpdir(), { researchFn: research, bridgeFn: bridge });
  assert.equal(bridge.calls.length, 2);
  assert.equal(result.meta.sectionsFailed, 0);
  assert.ok(result.words >= 127);
});

test('generateMuseScript proceeds with surviving sections when one still fails, if the floor is met', async () => {
  const research = mockResearch();
  // 5 min -> 3 sections of 350; fail section 3 permanently. Two surviving
  // sections (~700+ words) still clear the 637-word floor for 5 min.
  const bridge = mockBridge({ failSections: new Map([[2, 99]]) });
  const result = await generateMuseScript(makeJob(5), os.tmpdir(), { researchFn: research, bridgeFn: bridge });
  assert.equal(result.meta.sections, 3);
  assert.equal(result.meta.sectionsFailed, 1);
  assert.deepEqual(result.meta.failedSections, [3]);
  assert.equal(result.meta.publishable, false);
  assert.ok(result.meta.quality.issues.some((i) => /sections 3.*failed/.test(i)));
  assert.ok(result.words >= 637, `expected >= 637 words, got ${result.words}`);
  assert.equal(bridge.calls.length, 4); // 3 first attempts + 1 retry
});

test('generateMuseScript throws loudly when the total falls below the word floor', async () => {
  const research = mockResearch();
  const bridge = mockBridge({ failSections: new Map([[0, 99]]) });
  await assert.rejects(
    () => generateMuseScript(makeJob(1), os.tmpdir(), { researchFn: research, bridgeFn: bridge }),
    /too short: 0 words, minimum 127/,
  );
});

test('generateMuseScript throws loudly when the bridge returns short text', async () => {
  const research = mockResearch();
  const bridge = async () => ({ content: 'Too short to narrate anything at all.' });
  await assert.rejects(
    () => generateMuseScript(makeJob(1), os.tmpdir(), { researchFn: research, bridgeFn: bridge }),
    /too short/,
  );
});

test('generateMuseScript proceeds UNGROUNDED and labelled when research finds nothing', async () => {
  const research = mockResearch(EMPTY_EVIDENCE);
  const bridge = mockBridge();
  const result = await generateMuseScript(makeJob(1), os.tmpdir(), { researchFn: research, bridgeFn: bridge });
  assert.equal(result.meta.grounded, false);
  assert.equal(result.meta.ungrounded, true);
  assert.equal(result.meta.researchClaims, 0);
  assert.ok(result.words >= 127);
  assert.ok(result.meta.quality.warnings.some((w) => /Ungrounded script \(P4\)/.test(w)));
});

test('generateMuseScript proceeds UNGROUNDED when the research step throws', async () => {
  const research = async () => { throw new Error('search engines all failed'); };
  const bridge = mockBridge();
  const result = await generateMuseScript(makeJob(1), os.tmpdir(), { researchFn: research, bridgeFn: bridge });
  assert.equal(result.meta.grounded, false);
  assert.equal(result.meta.ungrounded, true);
  assert.equal(result.meta.researchError, 'search engines all failed');
  assert.ok(result.words >= 127);
});

test('editorial gate flags an invalid evidence marker on the full draft', async () => {
  const research = mockResearch();
  const badText = sectionText(300).replace('<evidence:claim-aaa111>', '<evidence:claim-zzz999>');
  const bridge = async () => ({ content: badText });
  const result = await generateMuseScript(makeJob(1), os.tmpdir(), { researchFn: research, bridgeFn: bridge });
  assert.equal(result.meta.publishable, false);
  assert.ok(result.meta.quality.issues.some((i) => /Invalid evidence references/.test(i)));
  assert.ok(result.meta.quality.invalidEvidenceMarkers?.includes('claim-zzz999')
    || result.meta.quality.issues.join(' ').includes('claim-zzz999'));
});

test('editorial gate passes a grounded draft with valid markers', async () => {
  const research = mockResearch();
  const bridge = mockBridge();
  const result = await generateMuseScript(makeJob(1), os.tmpdir(), { researchFn: research, bridgeFn: bridge });
  assert.equal(result.meta.quality.passes, true);
  assert.equal(result.meta.publishable, true);
  assert.deepEqual(result.meta.quality.invalidEvidenceMarkers, []);
});

// --- adapter routing: generateScript defaults to muse-search ---

test('adapter generateScript defaults to muse-search and never runs the ScriptIT CLI', async (t) => {
  const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-script-adapter-'));
  t.after(() => fs.rmSync(workDir, { recursive: true, force: true }));
  const savedEnv = {
    MUSE_SCRIPT_ENGINE: process.env.MUSE_SCRIPT_ENGINE,
    MUSE_SCRIPT_COMMAND: process.env.MUSE_SCRIPT_COMMAND,
    MUSE_SCRIPT_ALLOW_TEMPLATE_FALLBACK: process.env.MUSE_SCRIPT_ALLOW_TEMPLATE_FALLBACK,
  };
  t.after(() => {
    for (const [key, value] of Object.entries(savedEnv)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  });
  delete process.env.MUSE_SCRIPT_ENGINE;
  delete process.env.MUSE_SCRIPT_COMMAND;
  delete process.env.MUSE_SCRIPT_ALLOW_TEMPLATE_FALLBACK;

  // Stub lib/museScript in the require cache: the adapter lazy-requires
  // it inside the muse-search branch, so this intercepts exactly that
  // branch without touching any other module.
  const museScriptPath = require.resolve('../lib/museScript');
  const realCacheEntry = require.cache[museScriptPath];
  let stubCalls = 0;
  const stubText = sectionText(300);
  require.cache[museScriptPath] = {
    id: museScriptPath,
    filename: museScriptPath,
    loaded: true,
    exports: {
      generateMuseScript: async () => {
        stubCalls += 1;
        return {
          scriptText: cleanSectionText(stubText),
          words: countWords(cleanSectionText(stubText)),
          meta: {
            engine: 'muse-search', grounded: true, ungrounded: false,
            publishable: true, quality: { issues: [], warnings: [] },
          },
        };
      },
    },
  };
  t.after(() => {
    if (realCacheEntry) require.cache[museScriptPath] = realCacheEntry;
    else delete require.cache[museScriptPath];
  });

  const adapter = require('../adapters/script');
  assert.equal(adapter.resolveScriptEngine(), 'muse-search');
  assert.equal(adapter.resolveScriptEngine({ MUSE_SCRIPT_ENGINE: 'scriptit' }), 'scriptit');
  assert.equal(adapter.resolveScriptEngine({ MUSE_SCRIPT_ENGINE: 'muse-search' }), 'muse-search');
  assert.equal(adapter.resolveScriptEngine({ MUSE_SCRIPT_COMMAND: 'node fake.js' }), 'command');

  const result = await adapter.generateScript(makeJob(1), workDir, {});
  assert.equal(stubCalls, 1);
  assert.equal(result.meta.engine, 'muse-search');
  assert.ok(result.words >= 127);
  assert.equal(fs.readFileSync(path.join(workDir, 'script.txt'), 'utf8').trim(), result.scriptText);
  const savedMeta = JSON.parse(fs.readFileSync(path.join(workDir, 'script-meta.json'), 'utf8'));
  assert.equal(savedMeta.engine, 'muse-search');
  assert.equal(savedMeta.meta.publishable, true);
  assert.equal(savedMeta.meta.ungrounded, false);
});

test('adapter generateScript still honours an explicit MUSE_SCRIPT_COMMAND', async (t) => {
  const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-script-cmd-'));
  t.after(() => fs.rmSync(workDir, { recursive: true, force: true }));
  const savedCommand = process.env.MUSE_SCRIPT_COMMAND;
  const savedFallback = process.env.MUSE_SCRIPT_ALLOW_TEMPLATE_FALLBACK;
  t.after(() => {
    if (savedCommand === undefined) delete process.env.MUSE_SCRIPT_COMMAND;
    else process.env.MUSE_SCRIPT_COMMAND = savedCommand;
    if (savedFallback === undefined) delete process.env.MUSE_SCRIPT_ALLOW_TEMPLATE_FALLBACK;
    else process.env.MUSE_SCRIPT_ALLOW_TEMPLATE_FALLBACK = savedFallback;
  });
  delete process.env.MUSE_SCRIPT_ALLOW_TEMPLATE_FALLBACK;
  // A fake script command complying with the stdin/stdout contract:
  // exactly one JSON object on stdout, logs to stderr. Written to a
  // file to avoid nested shell quoting.
  const fakeCmd = path.join(workDir, 'fake-script-command.js');
  fs.writeFileSync(fakeCmd, [
    "const words = Array.from({ length: 150 }, (_, i) => `word${i}`).join(' ');",
    "process.stdout.write(JSON.stringify({ scriptText: words, meta: { engine: 'command', publishable: true, quality: { issues: [] } } }) + '\\n');",
    '',
  ].join('\n'));
  process.env.MUSE_SCRIPT_COMMAND = `node ${fakeCmd}`;

  const adapter = require('../adapters/script');
  assert.equal(adapter.resolveScriptEngine(), 'command');
  const result = await adapter.generateScript(makeJob(1), workDir, {});
  assert.equal(result.meta.engine, 'command');
  assert.equal(result.words, 150);
});
