'use strict';
// Regression test for the Apollo 11 1-min ScriptIT research snag
// (2026-11-15, job apollo11-1min-final, research-mode standard, twice):
//
//   "Research pipeline failed; falling back to direct research: Completed
//    research timed out: deadline reached"
//   "Direct research fallback failed ... Research deadline reached;
//    no direct-research recovery pass"
//   "Encyclopedic synthesis added 23 grounded claims; sufficient: false"
//   -> generation failed "ScriptIT research is insufficient for
//      publishable narration"
//
// Root cause, verified against the durable research store: the pipeline's
// two 90s passes exactly consumed the writer's 180s research deadline, so
// a slow pass made the outer timeout fire, the writer discarded the
// pipeline's already-sufficient checkpointed evidence (Bing 8, Wikipedia 2
// with bodies, RSS 16 — evidence sufficient: true), and the recovery pass
// got 0ms. The fix: reliable-first provider order, a deadline-aware
// batch runner that returns partial results instead of discarding them,
// 60s pass budgets inside the 180s deadline, and a quick-mode recovery.
//
// This file replays the failure three ways:
//   1. runBatchesWithDeadline returns partial results when the budget runs
//      out (unit, deterministic, no network).
//   2. Synthetic-only evidence still fails the sufficiency gate (the
//      encyclopedic dossier must never count as publishable evidence).
//   3. A live gatherResearch on the same Apollo 11 topic returns the
//      reliable lanes inside its phase budget despite the gated providers
//      (live, needs network).
//   4. The final fallback (Nov 15, 2026 — user order): generation with
//      zero research continues WITHOUT research and produces a script
//      anyway, labelled ungrounded (P4) in the metadata — no more
//      RESEARCH_INSUFFICIENT block. The evidence ledger still reports
//      insufficient honestly (test 2/3); only the blocking is gone.
// Genuinely-empty research still reports insufficient at the ledger level;
// the writer no longer throws on it (see 4).

const assert = require('assert');
const { runBatches, runBatchesWithDeadline } = require('../src/utils/concurrency');
const { buildEvidenceLedger } = require('../src/agents/evidence');
const { gatherResearch } = require('../src/agents/researcher');

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function testPartialResults() {
  console.log('\n1. runBatchesWithDeadline keeps partial results past the budget');
  // Models the production shape: reliable providers first (resolve in
  // ~10ms), then gated providers that each hang ~800ms. The deadline fires
  // while the gated tasks are in flight; tasks never claimed must report
  // budget-exhausted, and the reliable values must survive.
  const startedAt = Date.now();
  const out = await runBatchesWithDeadline(
    ['wiki', 'bing', 'rss', 'slow-1', 'slow-2', 'slow-3', 'slow-4', 'slow-5', 'slow-6'],
    3,
    async (item) => {
      if (item.startsWith('slow')) await sleep(800);
      return { item, ok: true };
    },
    { deadlineAt: Date.now() + 200 },
  );
  const elapsed = Date.now() - startedAt;
  assert.strictEqual(out.length, 9, 'result keeps input length/order');
  assert.deepStrictEqual(out[0], { item: 'wiki', ok: true }, 'reliable lane 1 survives');
  assert.deepStrictEqual(out[1], { item: 'bing', ok: true }, 'reliable lane 2 survives');
  assert.deepStrictEqual(out[2], { item: 'rss', ok: true }, 'reliable lane 3 survives');
  const skipped = out.filter((entry) => entry && entry.provider === 'research-pass');
  assert(skipped.length >= 2, `unclaimed tasks report budget-exhausted (got ${skipped.length})`);
  assert(elapsed < 2000, `run resolves once in-flight tasks finish, not after the whole queue (took ${elapsed}ms)`);
  console.log(`  ✓ partial results in ${elapsed}ms; reliable lanes kept, ${skipped.length} tasks budget-exhausted`);

  const full = await runBatches(['a', 'b', 'c'], 3, async (item) => ({ item }));
  assert.deepStrictEqual(full, [{ item: 'a' }, { item: 'b' }, { item: 'c' }], 'plain runBatches unchanged');
  console.log('  ✓ plain runBatches unchanged');
}

function syntheticOnlyResearch() {
  // Mirrors what encyclopedicSynthesizer appends to research.official:
  // model-written dossier claims with fabricated archive URLs.
  const claims = Array.from({ length: 23 }, (_, i) => ({
    title: `Apollo 11 first steps on the Moon dossier claim ${i + 1}`,
    url: `https://encyclopedic-historical-archive.example/apollo-11/${i + 1}`,
    source: 'Encyclopedic Historical Archive',
    snippet: 'Apollo 11 made the first steps on the Moon on 20 July 1969 when Apollo 11 landed in the Sea of Tranquility.',
    articleText: 'Apollo 11 made the first steps on the Moon on 20 July 1969 when Apollo 11 landed in the Sea of Tranquility.',
    hasArticleBody: true,
    synthetic: true,
  }));
  return {
    query: 'Apollo 11: The First Steps on the Moon',
    gatheredAt: new Date().toISOString(),
    duckduckgo: [], duckduckgoNews: [], rss: [], bing: [], bingNews: [], yandex: [],
    x: [], wikipedia: [], hackernews: [], reddit: [], official: claims,
    structured: [], targeted: [], errors: [],
  };
}

function testSyntheticNeverCounts() {
  console.log('\n2. Synthetic-only evidence still fails the sufficiency gate');
  const ledger = buildEvidenceLedger(syntheticOnlyResearch());
  assert.strictEqual(ledger.sufficient, false, '23 synthetic dossier claims must NOT satisfy the gate');
  assert(
    ledger.sufficiencyReasons.some((reason) => /insufficient verified primary or article body claims/i.test(reason)),
    `gate reason must name the missing verified evidence (got: ${ledger.sufficiencyReasons.join('; ')})`,
  );
  assert.strictEqual(ledger.primaryClaims, 0, 'synthetic claims are never primary');
  console.log(`  ✓ 23 synthetic claims -> sufficient: false (${ledger.sufficiencyReasons.join('; ')})`);
}

function testEmptyResearchFails() {
  console.log('\n3. Genuinely-empty research still reports insufficient (ledger stays honest)');
  const empty = {
    query: 'Apollo 11: The First Steps on the Moon',
    gatheredAt: new Date().toISOString(),
    duckduckgo: [], duckduckgoNews: [], rss: [], bing: [], bingNews: [], yandex: [],
    x: [], wikipedia: [], hackernews: [], reddit: [], official: [],
    structured: [], targeted: [], errors: ['research timed out'],
  };
  const ledger = buildEvidenceLedger(empty);
  assert.strictEqual(ledger.sufficient, false, 'empty research must stay insufficient at the ledger');
  assert(ledger.sufficiencyReasons.length > 0, 'empty research must explain why');
  console.log(`  ✓ empty research -> sufficient: false (${ledger.sufficiencyReasons.join('; ')}); writer continues labelled (see 5)`);
}

async function testLiveGather() {
  console.log('\n4. Live: gatherResearch returns the reliable lanes inside budget');
  console.log('   (same Apollo 11 topic that failed twice; gated providers will still fail)');
  // The egress proxy has congestion episodes where even Bing times out on
  // every attempt. Production gets three chances (fast, deep, recovery)
  // before the ungrounded fallback, so the test retries the gather once
  // rather than failing on a single bad minute.
  let raw = null;
  let elapsedMs = 0;
  let attempts = 0;
  for (; attempts < 2; attempts += 1) {
    const startedAt = Date.now();
    raw = await gatherResearch('Apollo 11: The First Steps on the Moon', {
      includeReddit: false,
      phase: 'fast',
      phaseBudgetMs: 45000,
    });
    elapsedMs = Date.now() - startedAt;
    const ledger = buildEvidenceLedger(raw);
    if (ledger.sufficient) break;
    console.log(`   attempt ${attempts + 1}: insufficient (${ledger.sufficiencyReasons.join('; ')}), retrying once…`);
  }
  assert(raw, 'Research object should exist');
  assert(elapsedMs < 75000, `fast pass must resolve inside its 60s pipeline budget (took ${Math.round(elapsedMs / 1000)}s)`);
  const wiki = Array.isArray(raw.wikipedia) ? raw.wikipedia : [];
  const bing = Array.isArray(raw.bing) ? raw.bing : [];
  // Either reliable lane may win on a given run (Wikipedia timed out at 15s
  // on one probe while Bing returned 16 results); the requirement is that
  // at least one reliable lane lands and the evidence is sufficient.
  assert(wiki.length > 0 || bing.length > 0, 'at least one reliable lane (Bing/Wikipedia) must return results');
  const ledger = buildEvidenceLedger(raw);
  assert.strictEqual(ledger.sufficient, true, `evidence must be sufficient after ${attempts + 1} attempt(s) (reasons: ${ledger.sufficiencyReasons.join('; ')})`);
  assert(ledger.claims.length > 0, 'ledger must contain claims');
  const gated = (raw.errors || []).filter((err) => /BOT_CHALLENGE|403|429/i.test(err));
  console.log(`  ✓ ${Math.round(elapsedMs / 1000)}s (attempt ${attempts + 1}); wikipedia ${wiki.length}, bing ${bing.length}, ` +
    `claims ${ledger.claims.length}, sufficient: true; gated lanes failed as expected (${gated.length})`);
}

async function testUngroundedFallback() {
  console.log('\n5. Final fallback: zero research still produces a labelled script (no block)');
  const { generateScript } = require('../src/agents/scriptWriter');
  // Generic narration (~213 words, 5 paragraphs): no dates, numbers, names
  // or quotations — exactly what the ungrounded prompt requires — with a
  // question hook and the topic words (Leipzig / balloon / expedition).
  const narration = [
    'What lifts a person above the rooftops and lets the wind decide where the journey ends?',
    'A balloon flight begins long before the basket leaves the ground. The crew spreads the envelope across the field, checks every seam and line, and fills the great cloth bag with warm air until it strains upward against its ropes. The basket waits below, loaded with ballast and instruments, while the pilot watches the wind and waits for the right moment to let go.',
    'Once airborne, the world goes quiet. There is no engine hum, no vibration, only the soft rush of air past the basket and the slow turn of the landscape below. Fields, rivers and rooftops drift past at walking pace, and the crew learns to read the sky the way sailors read the sea.',
    'The Leipzig expedition showed what such patience could achieve. Day after day the crew tracked the winds, chose their launches with care, and proved that a balloon could travel farther than anyone had dared to hope. Their journey became a lesson in planning, teamwork and trust in the weather.',
    'Ballooning still rewards the same qualities today. The pilot who respects the wind, prepares the craft with care and accepts what the sky allows will always find the journey worth making.',
  ].join('\n\n');
  let sawNoResearchInstruction = false;
  let calls = 0;
  const mockCompletion = async (request) => {
    calls += 1;
    const prompt = (request.messages || []).map((m) => m.content || '').join('\n');
    if (prompt.includes('NO VERIFIED RESEARCH AVAILABLE')) sawNoResearchInstruction = true;
    return { content: narration };
  };
  const result = await generateScript({
    topic: 'The 1923 Leipzig Balloon Expedition',
    duration: '1min',
    researchMode: 'standard',
    useCache: false,
    recordHistory: false,
    apiKey: 'fixture',
    research: { query: 'The 1923 Leipzig Balloon Expedition', official: [] },
    chatCompletionFn: mockCompletion,
  });
  assert.ok(calls > 0, 'ungrounded run must call the writer');
  assert.ok(sawNoResearchInstruction, 'draft prompt must state that no verified research exists');
  const script = String(result.script || '').trim();
  assert.ok(script.length > 100, 'ungrounded run must produce narration');
  const words = script.split(/\s+/).filter(Boolean).length;
  assert(words >= 170 && words <= 320, `script must meet the 1-min length (~213 words, got ${words})`);
  assert.strictEqual(result.meta.publishable, true, 'ungrounded script must come out publishable, not blocked');
  assert.strictEqual(result.meta.ungrounded, true, 'script must be labelled ungrounded (P4)');
  assert.strictEqual(result.meta.grounded, false, 'grounded must be false');
  assert.strictEqual(result.meta.researchComplete, false, 'researchComplete must stay false');
  assert.strictEqual(result.meta.evidence.sufficient, false, 'evidence ledger must still report insufficient');
  assert.ok(
    (result.meta.quality.warnings || []).some((w) => /Ungrounded script/i.test(w)),
    'quality warnings must carry the ungrounded label',
  );
  console.log(`  ✓ script out (${words} words, publishable: true, ungrounded: true, grounded: false); ` +
    `prompt carried the no-research instruction; ledger still insufficient`);
}

async function main() {
  console.log('====================================================');
  console.log('  SCRIPTIT RESEARCH TIMEOUT RECOVERY — REGRESSION');
  console.log('  (Apollo 11 1-min snag, 2026-11-15)');
  console.log('====================================================');
  await testPartialResults();
  testSyntheticNeverCounts();
  testEmptyResearchFails();
  await testLiveGather();
  await testUngroundedFallback();
  console.log('\nAll research-timeout-recovery checks passed.');
}

if (require.main === module) {
  main().catch((err) => {
    console.error('\nFAILED:', err && err.message ? err.message : err);
    process.exit(1);
  });
}

module.exports = { testPartialResults, testSyntheticNeverCounts, testEmptyResearchFails, testLiveGather, testUngroundedFallback };
