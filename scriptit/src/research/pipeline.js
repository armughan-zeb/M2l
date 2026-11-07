'use strict';

const { gatherResearch } = require('../agents/researcher');
const { buildBrief } = require('../agents/compressor');
const { buildEvidenceLedger } = require('../agents/evidence');
const { detectNiche } = require('../agents/nicheDetector');
const { getResearchStore } = require('./store');
const { buildResearchPlan, buildCoverageReport } = require('./questionPlanner');
const { normalizeTopic } = require('./topicNormalizer');
const { boundedCall } = require('../utils/execution');

function emptyResearch(query, error) {
  return {
    query,
    niche: detectNiche(query).niche,
    gatheredAt: new Date().toISOString(),
    duckduckgo: [], duckduckgoNews: [], rss: [], bing: [], bingNews: [], yandex: [], x: [], wikipedia: [], hackernews: [], reddit: [], official: [], structured: [], targeted: [],
    errors: error ? [String(error)] : [],
    stats: { sources: 0, articlesFetched: 0, datedResults: 0, distinctDomains: 0, freshest: null },
  };
}

function mergeResearch(fast, deep) {
  const merged = { ...(fast || {}), ...(deep || {}) };
  for (const key of new Set([...Object.keys(fast || {}), ...Object.keys(deep || {})])) {
    if (Array.isArray(fast?.[key]) || Array.isArray(deep?.[key])) {
      const values = [...(fast?.[key] || []), ...(deep?.[key] || [])];
      const selected = new Map();
      for (const item of values) {
        const keyValue = String(item?.url || `${item?.title || ''}|${item?.source || ''}`).toLowerCase();
        if (!keyValue) continue;
        const old = selected.get(keyValue);
        const bodySize = value => value?.hasArticleBody === true ? String(value.articleText || '').length : 0;
        if (!old || bodySize(item) >= bodySize(old)) selected.set(keyValue, item);
      }
      merged[key] = [...selected.values()];
    }
  }
  merged.gatheredAt = deep?.gatheredAt || fast?.gatheredAt || new Date().toISOString();
  merged.errors = [...new Set([...(fast?.errors || []), ...(deep?.errors || [])])];
  return merged;
}

function researchItemCount(research) {
  return Object.entries(research || {})
    .filter(([key, value]) => key !== 'errors' && Array.isArray(value))
    .reduce((total, [, items]) => total + items.length, 0);
}

async function safeStoreCall(fn) {
  try { return await fn(); } catch (err) { return { error: err.message || String(err) }; }
}

async function runResearchJob({ topic, input = {}, store, backgroundDeep = true, includeReddit = true, quick, gatherResearchFn = gatherResearch, signal } = {}) {
  signal?.throwIfAborted();
  const trimmedTopic = normalizeTopic(topic).canonical;
  if (!trimmedTopic) throw new Error('Research topic is required');
  const researchStore = store || await getResearchStore();
  const niche = detectNiche(trimmedTopic).niche;
  const researchPlan = buildResearchPlan({ topic: trimmedTopic, niche });
  const previousJob = await researchStore.latestJob(trimmedTopic).catch(() => null);
  const job = await researchStore.createJob({ ...input, topic: trimmedTopic, niche });
  const jobId = job.id;
  const warnings = [];
  const quickMode = typeof quick === 'boolean' ? quick : input.researchMode !== 'thorough';

  await safeStoreCall(() => researchStore.saveResearchPlan({ jobId, plan: researchPlan }));

  await researchStore.checkpointJob(jobId, { status: 'fast_research', phase: 'fast_research', progress: 10 });
  let fast;
  try {
    fast = await boundedCall(ownedSignal => gatherResearchFn(trimmedTopic, { includeReddit, fetchLimit: quickMode ? 5 : 6, phase: 'fast', researchPlan, quick: quickMode, signal: ownedSignal }), { signal, timeoutMs: 90000, label: 'Fast research' });
  } catch (err) {
    signal?.throwIfAborted();
    fast = emptyResearch(trimmedTopic, err.message);
    warnings.push(`Fast research failed: ${err.message}`);
  }
  signal?.throwIfAborted();
  const previousBundle = previousJob?.result;
  if (researchItemCount(fast) < 3
    && previousBundle?.research
    && previousBundle?.evidence?.sufficient !== false
    && researchItemCount(previousBundle.research) >= 3) {
    fast = mergeResearch(previousBundle.research, fast);
    fast.errors = [...new Set([...(fast.errors || []), 'Fresh providers returned too little data; reused stored evidence as a stale fallback'])];
    warnings.push('Fresh providers returned too little data; reused stored evidence as a stale fallback.');
  }
  let evidence = buildEvidenceLedger(fast);
  let coverage = buildCoverageReport({ plan: researchPlan, evidence });
  evidence.coverage = coverage;
  let brief = buildBrief(fast);
  await safeStoreCall(() => researchStore.saveResearchBundle({ jobId, research: fast, evidence, brief, plan: researchPlan, coverage }));
  await safeStoreCall(() => researchStore.saveResearchPlan({ jobId, plan: researchPlan, coverage }));
  await researchStore.checkpointJob(jobId, { status: 'fast_ready', phase: 'fast_ready', progress: 35, result: { research: fast, evidence, brief, plan: researchPlan, coverage }, warnings, errors: fast.errors || [] });

  if (quickMode) {
    const limited = !evidence.sufficient;
    await researchStore.checkpointJob(jobId, {
      status: limited ? 'partial' : 'context_ready',
      phase: 'context_ready',
      progress: 70,
      result: { research: fast, evidence, brief, plan: researchPlan, coverage },
      warnings,
      errors: fast.errors || [],
      retryable: limited,
    });
    return { jobId, research: fast, evidence, brief, plan: researchPlan, coverage, warnings, partial: limited, quick: true, deepPending: false };
  }

  const deepen = async () => {
    signal?.throwIfAborted();
    await researchStore.checkpointJob(jobId, { status: 'deep_research', phase: 'deep_research', progress: 45 });
    const openQuestions = researchPlan.questions.filter((item) => {
      const row = coverage.rows.find((candidate) => candidate.id === item.id);
      return !row || row.status !== 'answered';
    });
    const deepPlan = { ...researchPlan, questions: openQuestions.length ? openQuestions : researchPlan.questions };
    let deep;
    try {
      deep = await boundedCall(ownedSignal => gatherResearchFn(trimmedTopic, { includeReddit, fetchLimit: 18, phase: 'deep', researchPlan: deepPlan, quick: false, signal: ownedSignal }), { signal, timeoutMs: 90000, label: 'Deep research' });
    } catch (err) {
      signal?.throwIfAborted();
      warnings.push(`Deep research failed: ${err.message}`);
      deep = emptyResearch(trimmedTopic, err.message);
    }
    signal?.throwIfAborted();
    const merged = mergeResearch(fast, deep);
    evidence = buildEvidenceLedger(merged);
    coverage = buildCoverageReport({ plan: researchPlan, evidence });
    evidence.coverage = coverage;
    brief = buildBrief(merged);
    await safeStoreCall(() => researchStore.saveResearchBundle({ jobId, research: merged, evidence, brief, plan: researchPlan, coverage }));
    await safeStoreCall(() => researchStore.saveResearchPlan({ jobId, plan: researchPlan, coverage }));
    // Keep source sufficiency as the hard safety gate for now. Coverage is
    // persisted and shown to the writer/UI, but a missing optional question
    // should not discard a genuinely well-supported story.
    const ready = evidence.sufficient;
    await researchStore.checkpointJob(jobId, { status: ready ? 'context_ready' : 'partial', phase: 'context_ready', progress: 70, result: { research: merged, evidence, brief, plan: researchPlan, coverage }, warnings, errors: merged.errors || [], retryable: true });
    return { jobId, research: merged, evidence, brief, plan: researchPlan, coverage, warnings, partial: !ready };
  };

  if (backgroundDeep && evidence.sufficient) {
    // The fast result is returned immediately. The promise is deliberately
    // handled so a failed background deep pass can never become unhandled.
    setImmediate(() => deepen().catch(async (err) => {
      warnings.push(`Background deep research failed: ${err.message}`);
      await safeStoreCall(() => researchStore.checkpointJob(jobId, { status: 'partial', phase: 'deep_research', warnings, errors: [err.message], retryable: true }));
    }));
    return { jobId, research: fast, evidence, brief, warnings, partial: false, deepPending: true };
  }
  return deepen();
}

async function resumeResearchJob(jobId, { store, backgroundDeep = false } = {}) {
  const researchStore = store || await getResearchStore();
  const existing = await researchStore.getJob(jobId);
  if (!existing) throw new Error(`Research job not found: ${jobId}`);
  return runResearchJob({
    topic: existing.topic,
    input: { ...(existing.input || {}), idempotencyKey: existing.idempotency_key || `${existing.topic}|resume` },
    store: researchStore,
    backgroundDeep,
    includeReddit: Boolean(existing.input && existing.input.includeReddit),
    quick: existing.input?.researchMode !== 'thorough',
  });
}

module.exports = { runResearchJob, resumeResearchJob, mergeResearch, emptyResearch };
