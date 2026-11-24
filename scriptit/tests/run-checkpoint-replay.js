'use strict';
// Child-process runner for the (c) replay in tests/script-issues-hardening.js.
// It runs with cwd set to a fresh temp dir, so the durable research store it
// writes (./.cache/scriptit-research.sqlite) is isolated from the dev store.
//
// Replay: the research pipeline checkpoints a SUFFICIENT fast-pass bundle to
// the durable store, then hangs until the writer's research deadline fires.
// The writer must reuse that checkpointed evidence instead of discarding it
// and starting from empty. Prints one JSON line with the outcome.
const { buildBrief } = require('../src/agents/compressor');
const { buildEvidenceLedger } = require('../src/agents/evidence');
const { buildResearchPlan, buildCoverageReport } = require('../src/research/questionPlanner');
const { getResearchStore } = require('../src/research/store');
const { normalizeTopic, researchSubject } = require('../src/research/topicNormalizer');
const { generateScript } = require('../src/agents/scriptWriter');
const { LEIPZIG_TOPIC, LEIPZIG_NARRATION, leipzigResearchBundle } = require('./fixtures/script-issues-fixtures');

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function main() {
  const researchTopic = researchSubject(normalizeTopic(LEIPZIG_TOPIC).canonical);
  const bundle = leipzigResearchBundle();
  const evidence = buildEvidenceLedger(bundle);
  const plan = buildResearchPlan({ topic: researchTopic });
  const coverage = buildCoverageReport({ plan, evidence });
  evidence.coverage = coverage;
  const brief = buildBrief(bundle);
  if (!evidence.sufficient) throw new Error(`fixture bundle must be sufficient (reasons: ${evidence.sufficiencyReasons.join('; ')})`);

  // Seed the checkpoint exactly as runResearchJob does after its fast pass.
  const store = await getResearchStore();
  const job = await store.createJob({ topic: researchTopic, researchVersion: 'evidence-v14', duration: '1min', contentType: 'documentary', researchMode: 'standard' });
  await store.checkpointJob(job.id, {
    status: 'fast_ready', phase: 'fast_ready', progress: 35,
    result: { research: bundle, evidence, brief, plan, coverage },
    warnings: [], errors: [],
  });

  const startedAt = Date.now();
  const result = await generateScript({
    topic: LEIPZIG_TOPIC,
    duration: '1min',
    researchMode: 'standard',
    useCache: false,
    recordHistory: false,
    apiKey: 'fixture',
    totalDeadlineMs: 600000,
    // Pipeline hangs past the 30s research deadline (MUSE_RESEARCH_TIMEOUT_MS).
    researchJobFn: () => new Promise(() => {}),
    // Recovery must not invent evidence: it hangs too, so the only usable
    // evidence is the seeded checkpoint.
    researchRecoveryFn: () => new Promise(() => {}),
    synthesizeDossierFn: async () => ({ claims: [] }),
    chatCompletionFn: async () => ({ content: LEIPZIG_NARRATION }),
  });
  const script = String(result.script || '').trim();
  console.log(JSON.stringify({
    ok: true,
    elapsedMs: Date.now() - startedAt,
    words: script.split(/\s+/).filter(Boolean).length,
    publishable: result.meta.publishable,
    ungrounded: result.meta.ungrounded,
    researchComplete: result.meta.researchComplete,
    sufficient: result.meta.evidence.sufficient,
    externalClaims: result.meta.evidence.externalClaims,
    qualityIssues: result.meta.quality.issues,
  }));
}

main().catch((err) => {
  console.log(JSON.stringify({ ok: false, error: err.message || String(err) }));
  process.exit(1);
});
