'use strict';
// Regression tests for ScriptIT script issues (Area 8, 2026-11-16).
//
// (b) SIMILARITY GATE — the Apollo 1-min script was blocked as "too similar
//     to a recent script": the Apollo 2-min script from the same morning.
//     Root cause: assessScript compared every draft against the last 12
//     generations regardless of topic or duration, and similarity() divides
//     by the SMALLER shingle set — so a shorter script reusing the same
//     sentences scores 1.000 (a subset). A different-duration script is a
//     different deliverable and must never be blocked by an older one.
// (a) RESEARCH STILL BLOCKING — two remaining paths: (1) when the direct
//     research recovery itself timed out, the writer rethrew
//     SCRIPTIT_TIMEOUT instead of degrading to empty/ungrounded, so no
//     script was produced at all; (2) the encyclopedic synthesis ran with
//     no deadline of its own (up to 3600s on the agent bridge) and could
//     eat the whole job budget, killing generation with no script.
// (c) DEADLINE DISCARDS EVIDENCE — when the writer's research deadline
//     fired mid-pipeline, the writer discarded the pipeline's already
//     checkpointed fast-pass evidence and started from empty, even when
//     that checkpoint was sufficient (the exact Apollo snag behind 7ad07e1).
//
// Rule under test: a script must ALWAYS be producible — research ->
// synthesis -> ungrounded-labelled, never blocked.
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { assessScript, similarity } = require('../src/agents/scriptQuality');
const fixtures = require('./fixtures/script-issues-fixtures');

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function testSimilarityGate() {
  console.log('\n1. Similarity gate: a fresh Apollo 1-min draft is never blocked by the Apollo 2-min script');
  const prev2min = { topic: fixtures.APOLLO_TOPIC, duration: '2min', script: fixtures.APOLLO_2MIN_SCRIPT };
  const prev1min = { topic: fixtures.APOLLO_TOPIC, duration: '1min', script: fixtures.APOLLO_2MIN_SCRIPT };
  const legacy = { topic: fixtures.APOLLO_TOPIC, script: fixtures.APOLLO_2MIN_SCRIPT };

  // Replay fidelity: the subset draft reuses the 2-min script's sentences,
  // so the raw measure scores it 1.000 — this is what the old gate saw.
  const raw = similarity(fixtures.APOLLO_1MIN_SUBSET_DRAFT, fixtures.APOLLO_2MIN_SCRIPT);
  assert.ok(raw > 0.9, `subset draft must score ~1.0 against the 2-min script (got ${raw})`);
  console.log(`  ✓ replay faithful: subset draft similarity = ${raw.toFixed(3)} (old gate blocked at > 0.22)`);

  // (b1) Different duration: not comparable at all — no issue, no warning.
  const crossDuration = assessScript({
    script: fixtures.APOLLO_1MIN_SUBSET_DRAFT, topic: fixtures.APOLLO_TOPIC,
    wordTarget: 210, duration: '1min', previousScripts: [prev2min],
  });
  assert.strictEqual(crossDuration.maxSimilarity, 0, 'cross-duration scripts must not be compared');
  assert.ok(!crossDuration.issues.some((i) => /similar/i.test(i)), 'cross-duration draft must carry no similarity issue');
  assert.ok(!crossDuration.warnings.some((w) => /similar/i.test(w)), 'cross-duration draft must carry no similarity warning');
  assert.ok(crossDuration.passes, `cross-duration draft must pass (issues: ${crossDuration.issues.join(' ')})`);
  console.log('  ✓ 1-min subset draft vs 2-min history: maxSimilarity 0, passes');

  // (b2) The reworded fresh draft passes too (it always did — the block only
  // ever fired on reused sentences).
  const reworded = assessScript({
    script: fixtures.APOLLO_1MIN_REWORDED_DRAFT, topic: fixtures.APOLLO_TOPIC,
    wordTarget: 210, duration: '1min', previousScripts: [prev2min],
  });
  assert.ok(reworded.passes, `reworded fresh draft must pass (issues: ${reworded.issues.join(' ')})`);
  console.log('  ✓ reworded fresh 1-min draft passes');

  // (b3) Same topic + same duration: similarity is ADVISORY only — a
  // near-identical draft warns but never hard-blocks a legitimate new run
  // (e.g. a corrected re-run, which is mostly the same text by design).
  const sameDuration = assessScript({
    script: fixtures.APOLLO_1MIN_SUBSET_DRAFT, topic: fixtures.APOLLO_TOPIC,
    wordTarget: 210, duration: '1min', previousScripts: [prev1min],
  });
  assert.ok(sameDuration.maxSimilarity > 0.85, `same-duration subset must still measure high (got ${sameDuration.maxSimilarity})`);
  assert.ok(sameDuration.warnings.some((w) => /similar/i.test(w)), 'same-duration near-duplicate must warn');
  assert.ok(!sameDuration.issues.some((i) => /similar/i.test(i)), 'similarity must never be a hard issue');
  assert.ok(sameDuration.passes, `same-duration draft must pass despite the warning (issues: ${sameDuration.issues.join(' ')})`);
  console.log(`  ✓ same topic+duration near-duplicate: similarity ${sameDuration.maxSimilarity.toFixed(3)} warns, does not block`);

  // (b4) Legacy history entries (no duration recorded, e.g. the real Apollo
  // 2-min entry from 2026-11-15) cannot prove they are the same deliverable,
  // so they never count against a new run.
  const legacyCheck = assessScript({
    script: fixtures.APOLLO_1MIN_SUBSET_DRAFT, topic: fixtures.APOLLO_TOPIC,
    wordTarget: 210, duration: '1min', previousScripts: [legacy],
  });
  assert.strictEqual(legacyCheck.maxSimilarity, 0, 'duration-less history entries must not be compared');
  assert.ok(legacyCheck.passes, 'legacy-entry draft must pass');
  console.log('  ✓ legacy entry without a duration never blocks');
}

async function testRecoveryTimeoutDegrades() {
  console.log('\n2. Recovery timeout degrades to ungrounded (never kills generation)');
  const { generateScript } = require('../src/agents/scriptWriter');
  const prevTimeout = process.env.MUSE_RESEARCH_TIMEOUT_MS;
  process.env.MUSE_RESEARCH_TIMEOUT_MS = '30000';
  try {
    const startedAt = Date.now();
    // Pipeline consumes ~25s of the 30s research deadline, then throws;
    // the recovery gets ~5s and hangs past it, so its boundedCall fires
    // SCRIPTIT_TIMEOUT. Pre-fix the writer rethrew that and the whole
    // generation died with no script.
    const result = await generateScript({
      topic: fixtures.LEIPZIG_TOPIC,
      duration: '1min',
      researchMode: 'standard',
      useCache: false,
      recordHistory: false,
      apiKey: 'fixture',
      totalDeadlineMs: 600000,
      researchJobFn: async () => { await sleep(25000); throw new Error('Completed research timed out: deadline reached'); },
      researchRecoveryFn: () => new Promise(() => {}),
      synthesizeDossierFn: async () => ({ claims: [] }),
      chatCompletionFn: async () => ({ content: fixtures.LEIPZIG_NARRATION }),
    });
    const elapsed = Date.now() - startedAt;
    const script = String(result.script || '').trim();
    const words = script.split(/\s+/).filter(Boolean).length;
    assert.ok(script.length > 100, 'recovery timeout must still produce narration');
    assert.ok(words >= 170 && words <= 320, `script must meet the 1-min length (got ${words})`);
    assert.strictEqual(result.meta.publishable, true, 'degraded script must come out publishable, not blocked');
    assert.strictEqual(result.meta.ungrounded, true, 'script must be labelled ungrounded (P4)');
    assert.ok(elapsed < 120000, `run must settle on the research deadline, not the job deadline (took ${Math.round(elapsed / 1000)}s)`);
    console.log(`  ✓ script out (${words} words, publishable, ungrounded) in ${Math.round(elapsed / 1000)}s despite the recovery timing out`);
  } finally {
    if (prevTimeout === undefined) delete process.env.MUSE_RESEARCH_TIMEOUT_MS;
    else process.env.MUSE_RESEARCH_TIMEOUT_MS = prevTimeout;
  }
}

function testCheckpointReuse() {
  console.log('\n3. Writer deadline reuses the pipeline checkpoint (never discards sufficient evidence)');
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'scriptit-checkpoint-'));
  try {
    const run = spawnSync(process.execPath, [path.join(__dirname, 'run-checkpoint-replay.js')], {
      cwd: tmpDir,
      env: { ...process.env, MUSE_RESEARCH_TIMEOUT_MS: '30000' },
      encoding: 'utf8',
      timeout: 120000,
    });
    const lastLine = String(run.stdout || '').trim().split('\n').filter(Boolean).pop() || '';
    let out;
    try { out = JSON.parse(lastLine); } catch { out = null; }
    assert.ok(out && out.ok, `checkpoint replay must complete (stdout: ${run.stdout} stderr: ${run.stderr})`);
    assert.strictEqual(out.ungrounded, false, 'checkpointed sufficient evidence must keep the script grounded');
    assert.strictEqual(out.researchComplete, true, 'research must count as complete from the checkpoint');
    assert.strictEqual(out.sufficient, true, 'evidence ledger must report sufficient from the checkpoint');
    assert.ok(out.externalClaims >= 2, `checkpoint claims must reach the writer (got ${out.externalClaims})`);
    assert.strictEqual(out.publishable, true, `checkpoint-grounded script must be publishable (issues: ${(out.qualityIssues || []).join(' ')})`);
    assert.ok(out.words >= 170 && out.words <= 320, `script must meet the 1-min length (got ${out.words})`);
    console.log(`  ✓ grounded publishable script (${out.words} words, ${out.externalClaims} external claims) from the checkpoint in ${Math.round(out.elapsedMs / 1000)}s`);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
}

async function testSynthesisBounded() {
  console.log('\n4. Encyclopedic synthesis is bounded (cannot eat the job budget)');
  const { generateScript } = require('../src/agents/scriptWriter');
  const prevTimeout = process.env.MUSE_SYNTHESIS_TIMEOUT_MS;
  process.env.MUSE_SYNTHESIS_TIMEOUT_MS = '2000';
  // Force the real synthesis path to hang: the writer requires the
  // synthesizer lazily at call time, so patching the module makes the
  // ORIGINAL code hang too (otherwise the pre-fix run would fail fast on a
  // fake OpenRouter key and the test would pass vacuously). The injected
  // synthesizeDossierFn is what the fixed code bounds.
  const synthesizer = require('../src/research/encyclopedicSynthesizer');
  const originalSynthesize = synthesizer.synthesizeEncyclopedicDossier;
  synthesizer.synthesizeEncyclopedicDossier = () => new Promise(() => {});
  try {
    const startedAt = Date.now();
    // All research fails, then synthesis hangs forever. Pre-fix the
    // synthesis awaited with no deadline (3600s on the agent bridge), so
    // the job died on its total deadline with no script.
    const result = await generateScript({
      topic: fixtures.LEIPZIG_TOPIC,
      duration: '1min',
      researchMode: 'standard',
      useCache: false,
      recordHistory: false,
      apiKey: 'fixture',
      totalDeadlineMs: 30000,
      researchJobFn: async () => ({
        jobId: 'test-synth',
        research: fixtures.emptyResearchBundle(fixtures.LEIPZIG_TOPIC),
      }),
      synthesizeDossierFn: () => new Promise(() => {}),
      chatCompletionFn: async () => ({ content: fixtures.LEIPZIG_NARRATION }),
    });
    const elapsed = Date.now() - startedAt;
    const script = String(result.script || '').trim();
    assert.ok(script.length > 100, 'hung synthesis must still produce narration');
    assert.strictEqual(result.meta.publishable, true, 'script must come out publishable');
    assert.strictEqual(result.meta.ungrounded, true, 'script must be labelled ungrounded (P4)');
    assert.ok(elapsed < 20000, `hung synthesis must be cut off in ~2s, not eat the budget (took ${Math.round(elapsed / 1000)}s)`);
    console.log(`  ✓ script out (publishable, ungrounded) in ${Math.round(elapsed / 1000)}s despite the hung synthesis`);
  } finally {
    synthesizer.synthesizeEncyclopedicDossier = originalSynthesize;
    if (prevTimeout === undefined) delete process.env.MUSE_SYNTHESIS_TIMEOUT_MS;
    else process.env.MUSE_SYNTHESIS_TIMEOUT_MS = prevTimeout;
  }
}

async function testCheckpointLoader() {
  console.log('\n5. Checkpoint loader: fresh partial evidence in, stale/empty evidence out');
  const { loadCheckpointedResearch } = require('../src/agents/scriptWriter');
  const bundle = fixtures.leipzigResearchBundle();
  const freshJob = {
    updated_at: new Date().toISOString(),
    result: { research: bundle },
  };
  const staleJob = {
    updated_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    result: { research: bundle },
  };
  const emptyJob = {
    updated_at: new Date().toISOString(),
    result: { research: fixtures.emptyResearchBundle(fixtures.LEIPZIG_TOPIC) },
  };
  const fakeStore = (job) => ({ latestJob: async () => job });
  assert.strictEqual(await loadCheckpointedResearch(fixtures.LEIPZIG_TOPIC, fakeStore(freshJob)), bundle, 'fresh checkpoint must load');
  assert.strictEqual(await loadCheckpointedResearch(fixtures.LEIPZIG_TOPIC, fakeStore(staleJob)), null, 'stale (>1h) checkpoint must not load');
  assert.strictEqual(await loadCheckpointedResearch(fixtures.LEIPZIG_TOPIC, fakeStore(emptyJob)), null, 'empty checkpoint must not load');
  assert.strictEqual(await loadCheckpointedResearch(fixtures.LEIPZIG_TOPIC, fakeStore(null)), null, 'missing job must not load');
  assert.strictEqual(await loadCheckpointedResearch(fixtures.LEIPZIG_TOPIC, { latestJob: async () => { throw new Error('store down'); } }), null, 'store failure must degrade to null, not throw');
  console.log('  ✓ fresh loads; stale, empty, missing and failing stores all degrade to null');
}

async function main() {
  console.log('====================================================');
  console.log('  SCRIPTIT SCRIPT ISSUES — REGRESSION (Area 8)');
  console.log('  (Apollo similarity gate, research blocking,');
  console.log('   deadline discarding checkpointed evidence)');
  console.log('====================================================');
  testSimilarityGate();
  await testRecoveryTimeoutDegrades();
  testCheckpointReuse();
  await testSynthesisBounded();
  await testCheckpointLoader();
  console.log('\nAll script-issues-hardening checks passed.');
}

if (require.main === module) {
  main().catch((err) => {
    console.error('\nFAILED:', err && err.message ? err.message : err);
    process.exit(1);
  });
}

module.exports = { testSimilarityGate, testRecoveryTimeoutDegrades, testCheckpointReuse, testSynthesisBounded, testCheckpointLoader };
