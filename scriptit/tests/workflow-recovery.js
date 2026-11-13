'use strict';
const assert = require('node:assert/strict');
const { writeSections } = require('../src/agents/sectionWriter');
const { auditNarration, correctFindings } = require('../src/agents/factualAudit');
const { generateScript } = require('../src/agents/scriptWriter');
const { applySemanticReview } = require('../src/agents/scriptWriter');
const crypto = require('crypto');
const { handleGenerate } = require('../bridge/server');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const clone = value => JSON.parse(JSON.stringify(value));
const body = (beat, words = 270) => `Beat ${beat}. ${Array(words - 2).fill('documented').join(' ')} <evidence:claim-record>`;
const plan = { beats: Array.from({ length: 5 }, (_, i) => ({ purpose: `Documented story development ${i + 1}`, sourceIds: ['claim-record'] })) };
const sectionOptions = { topic: 'A documented historical subject', wordTarget: 1500, evidenceText: 'claim-record | reliable historical passage', model: 'selected-writer' };
const beatId = user => Number(user.match(/Write ONLY beat (\d+)/)?.[1]);
const batchUnits = user => JSON.parse(user.split('NUMBERED SENTENCES:\n')[1]);
const supported = units => ({ reviews: units.map(unit => ({ id: unit.id, verdict: 'supported', sourceIds: ['claim-record'], reason: 'Explicit source support' })) });

async function main() {
  let planningCheckpoint;
  await assert.rejects(writeSections({ ...sectionOptions,
    onCheckpoint: value => { planningCheckpoint = clone(value); },
    complete: async () => { throw new Error('Output allowance exhausted before visible completion'); }
  }), /Output allowance exhausted/);
  assert.equal(planningCheckpoint.phase, 'planning');
  assert.equal(planningCheckpoint.planAttempts, 1);
  let resumedPlanCalls = 0;
  await writeSections({ ...sectionOptions, initialCheckpoint: planningCheckpoint,
    onCheckpoint: value => { planningCheckpoint = clone(value); },
    complete: async request => {
      if (!beatId(request.user)) { resumedPlanCalls++; return { content: JSON.stringify(plan) }; }
      return { content: body(beatId(request.user)), modelUsed: 'selected-writer' };
    }
  });
  assert.equal(resumedPlanCalls, 1);
  assert.equal(planningCheckpoint.planAttempts, 2, 'outline attempts survive checkpoint resume');
  await assert.rejects(writeSections({ ...sectionOptions,
    initialCheckpoint: { ...planningCheckpoint, plan: null, planAttempts: 3 },
    complete: async () => { throw new Error('Attempt limit must prevent another provider request'); }
  }), /after three bounded requests/);
  let plans = 0;
  const fixedPlan = await writeSections({ ...sectionOptions, complete: async request => {
    if (!beatId(request.user)) {
      plans++;
      assert.equal(request.responseFormat.type, 'json_schema');
      return { content: plans === 1 ? '{"beats":[]}' : JSON.stringify(plan) };
    }
    return { content: body(beatId(request.user)), modelUsed: 'selected-writer' };
  } });
  assert.equal(plans, 2, 'only the invalid beat plan is repaired');
  assert.equal(fixedPlan.modelUsed, 'selected-writer');

  let activeWriters = 0, peakWriters = 0;
  const contextSafe = await writeSections({ ...sectionOptions, complete: async request => {
    if (!beatId(request.user)) return { content: JSON.stringify(plan) };
    const beat = beatId(request.user);
    activeWriters++;
    peakWriters = Math.max(peakWriters, activeWriters);
    try {
      if (beat > 1) {
        assert.ok(request.user.includes('ACTUAL PRECEDING NARRATION'), 'every chunk receives actual previous narration');
        for (let previous = 1; previous < beat; previous++) {
          assert.ok(request.user.includes(`Beat ${previous}.`), `beat ${beat} retains completed beat ${previous}`);
        }
      }
      return { content: body(beat), modelUsed: 'selected-writer' };
    } finally { activeWriters--; }
  } });
  assert.equal(peakWriters, 1, 'long-form chunks run in order to preserve exact narrative continuity');
  assert.ok(contextSafe.content.indexOf('Beat 1.') < contextSafe.content.indexOf('Beat 5.'));

  const uneven = await writeSections({ ...sectionOptions, complete: async ({ user }) => {
    const beat = beatId(user);
    return { content: beat ? body(beat, beat === 1 ? 252 : 270) : JSON.stringify(plan), modelUsed: 'selected-writer' };
  } });
  assert.ok(uneven.content.split(/\s+/).filter(Boolean).length >= 1275, 'slightly uneven beats pass only when the whole duration requirement is met');
  let expansions = 0;
  const expanded = await writeSections({ ...sectionOptions, complete: async ({ user }) => {
    const beat = beatId(user);
    if (!beat) return { content: JSON.stringify(plan) };
    if (user.includes('complete story is')) { expansions++; return { content: body(beat, 340) }; }
    return { content: body(beat, 245) };
  } });
  assert.equal(expansions, 1, 'repair the whole duration deficit in one source-grounded beat instead of rewriting the story');
  assert.ok(expanded.content.split(/\s+/).filter(Boolean).length >= 1275);

  let sectionCheckpoint;
  await assert.rejects(writeSections({ ...sectionOptions, onCheckpoint: value => { sectionCheckpoint = clone(value); }, complete: async ({ user }) => {
    const beat = beatId(user);
    if (!beat) return { content: JSON.stringify(plan) };
    if (beat === 2) { await wait(5); throw new Error('temporary disconnect'); }
    if (beat > 2) await wait(15);
    return { content: body(beat), modelUsed: 'selected-writer' };
  } }), /temporary disconnect/);
  const completedBeats = sectionCheckpoint.sections.flatMap((section, index) => section?.content ? [index + 1] : []);
  assert.ok(completedBeats.includes(1));
  const resumedBeats = [];
  const resumedSections = await writeSections({ ...sectionOptions, initialCheckpoint: sectionCheckpoint, complete: async ({ user }) => {
    const beat = beatId(user);
    assert.ok(beat, 'the saved valid beat plan is reused');
    resumedBeats.push(beat);
    return { content: body(beat), modelUsed: 'selected-writer' };
  } });
  assert.ok(completedBeats.every(beat => !resumedBeats.includes(beat)), 'completed beats must not consume another provider request');
  assert.ok(resumedSections.content.indexOf('Beat 1.') < resumedSections.content.indexOf('Beat 5.'));

  let timedOutProviderSignal;
  const timeoutStarted = Date.now();
  await assert.rejects(writeSections({ ...sectionOptions, requestTimeoutMs: 20, complete: request => {
    if (!beatId(request.user)) return Promise.resolve({ content: JSON.stringify(plan) });
    timedOutProviderSignal = request.signal;
    return new Promise(() => {});
  } }), /deadline reached/);
  assert.equal(timedOutProviderSignal.aborted, true, 'provider receives cancellation when its request deadline expires');
  assert.ok(Date.now() - timeoutStarted < 500, 'a silent writer settles at the configured request deadline');
  let changedPlans = 0;
  await writeSections({ ...sectionOptions, model: 'changed-writer', initialCheckpoint: sectionCheckpoint, complete: async ({ user }) => {
    const beat = beatId(user);
    if (!beat) { changedPlans++; return { content: JSON.stringify(plan) }; }
    return { content: body(beat), modelUsed: 'changed-writer' };
  } });
  assert.equal(changedPlans, 1, 'changed inputs invalidate the saved section context');

  let completeStoryReviews = 0, continuityCheckpoint;
  const beatRequests = new Map();
  const coherent = { reviews: Array.from({ length: 5 }, (_, index) => ({ beat: index + 1, passes: true, issues: [] })) };
  const continuous = await writeSections({ ...sectionOptions, continuityReview: true, onCheckpoint: value => { continuityCheckpoint = clone(value); }, complete: async request => {
    if (request.responseFormat?.json_schema?.name === 'story_continuity') {
      completeStoryReviews++;
      assert.ok(request.user.includes('Beat 1.') && request.user.includes('Beat 5.'), 'review receives the actual entire story');
      const review = clone(coherent);
      if (completeStoryReviews === 1) review.reviews[2] = { beat: 3, passes: false, issues: ['Repeats the opening instead of continuing the established story.'] };
      return { content: JSON.stringify(review) };
    }
    const beat = beatId(request.user);
    if (!beat) return { content: JSON.stringify(plan) };
    beatRequests.set(beat, (beatRequests.get(beat) || 0) + 1);
    if (beat === 5) assert.ok(request.user.includes('ACTUAL PRECEDING NARRATION') && request.user.includes('Beat 1.'), 'later writing retains actual earlier narration');
    if (beatRequests.get(beat) > 1) assert.ok(request.user.includes('COMPLETE CURRENT STORY') && request.user.includes('Repeats the opening'));
    return { content: beatRequests.get(beat) > 1 ? body(beat).replace(`Beat ${beat}.`, `Beat ${beat} continues.`) : body(beat), modelUsed: 'selected-writer' };
  } });
  assert.equal(completeStoryReviews, 2);
  assert.equal(beatRequests.get(3), 2);
  assert.ok([...beatRequests].filter(([beat]) => beat !== 3).every(([, count]) => count === 1), 'continuity repair preserves every unaffected beat');
  assert.equal(continuous.continuity.scriptHash, crypto.createHash('sha256').update(continuous.content).digest('hex'));
  await writeSections({ ...sectionOptions, continuityReview: true, initialCheckpoint: continuityCheckpoint, complete: async () => { throw new Error('valid whole-story checkpoint must be reused'); } });

  const exactNarration = 'A source-supported paraphrase of the documented story.';
  const lexicalQuality = { script: exactNarration, structuralPasses: true, editorialIssues: [], issues: ['Low lexical overlap'], supportIssues: ['Low lexical overlap'], passes: false };
  const exactHash = crypto.createHash('sha256').update(exactNarration).digest('hex');
  assert.equal(applySemanticReview(lexicalQuality, { passes: false, scriptHash: exactHash }), false);
  assert.equal(applySemanticReview(lexicalQuality, { passes: true, scriptHash: 'other-draft' }), false);
  assert.equal(lexicalQuality.passes, false);
  assert.equal(applySemanticReview(lexicalQuality, { passes: true, scriptHash: exactHash }), true, 'actual full semantic review outranks superficial word overlap');
  assert.ok(lexicalQuality.warnings.includes('Low lexical overlap'));

  const script = Array.from({ length: 17 }, (_, index) => `The record documents event ${index + 1}.`).join(' ');
  const auditOptions = { script, topic: 'Historical events', evidenceText: 'claim-record | original events record', reviewKey: 'selected-reviewer' };
  const batchCalls = new Map();
  const repairedReview = await auditNarration({ ...auditOptions, complete: async ({ user }) => {
    const units = batchUnits(user), first = units[0].id;
    batchCalls.set(first, (batchCalls.get(first) || 0) + 1);
    if (first === 1 && batchCalls.get(first) === 1) return { reviews: [] };
    return supported(units);
  } });
  assert.equal(repairedReview.passes, true);
  assert.equal(repairedReview.checkedSentences, 17);
  assert.equal(batchCalls.get(1), 2);
  assert.equal(batchCalls.get(17), 1, 'valid sibling review is never repeated to fix another batch');

  let auditCheckpoint;
  await assert.rejects(auditNarration({ ...auditOptions, onCheckpoint: value => { auditCheckpoint = clone(value); }, complete: async ({ user }) => {
    const units = batchUnits(user);
    if (units[0].id === 17) { await wait(5); throw new Error('review transport timeout'); }
    return supported(units);
  } }), /review transport timeout/);
  assert.equal(auditCheckpoint.reports[0].invalid, false);
  const resumedBatches = [];
  const resumedAudit = await auditNarration({ ...auditOptions, initialCheckpoint: auditCheckpoint, complete: async ({ user }) => {
    const units = batchUnits(user); resumedBatches.push(units[0].id); return supported(units);
  } });
  assert.equal(resumedAudit.passes, true);
  assert.deepEqual(resumedBatches, [17], 'resume reviews only the unfinished batch');
  assert.equal(resumedAudit.scriptHash, repairedReview.scriptHash);

  let invalidCalls = 0, exhaustedCheckpoint;
  const invalid = await auditNarration({ script: 'An unsupported claim.', evidenceText: 'claim-record', onCheckpoint: value => { exhaustedCheckpoint = clone(value); }, complete: async () => { invalidCalls++; return { reviews: [null] }; } });
  assert.equal(invalid, null);
  assert.equal(invalidCalls, 3);
  await auditNarration({ script: 'An unsupported claim.', evidenceText: 'claim-record', initialCheckpoint: exhaustedCheckpoint, complete: async () => { throw new Error('must not request beyond exhaustion'); } });
  let rejectedCalls = 0;
  const rejected = await auditNarration({ script: 'An unsupported claim.', evidenceText: 'claim-record', complete: async () => {
    rejectedCalls++; return { reviews: [{ id: 1, verdict: 'unsupported', sourceIds: [], reason: 'No support for this assertion' }] };
  } });
  assert.equal(rejected.passes, false);
  assert.equal(rejectedCalls, 1, 'actual factual rejections receive correction, not a repeated review hoping to pass');

  const started = Date.now();
  await assert.rejects(auditNarration({ script: 'The record documents an event.', evidenceText: 'claim-record', requestTimeoutMs: 20, complete: () => new Promise(() => {}) }), /deadline reached/);
  assert.ok(Date.now() - started < 500, 'a silent review must settle on its actual deadline');
  const repeated = 'The record is clear. The record is clear.';
  const corrected = await correctFindings({ script: repeated, findings: [{ id: 2, sentence: 'The record is clear.' }], evidenceText: 'claim-record', complete: async () => ({ replacements: [{ id: 2, oldSentence: 'The record is clear.', newSentence: 'The second claim remains uncertain.' }] }) });
  assert.equal(corrected, 'The record is clear. The second claim remains uncertain.', 'correction must target the numbered occurrence, not the first matching text');

  let researchSignal, lateDrafts = 0;
  const generation = generateScript({ topic: 'Bounded historical subject', researchMode: 'thorough', useCache: false, recordHistory: false, apiKey: 'fixture', totalDeadlineMs: 20,
    researchJobFn: options => { researchSignal = options.signal; return new Promise(() => {}); }, onDraft: () => { lateDrafts++; } });
  await assert.rejects(generation, /deadline reached/);
  assert.equal(researchSignal.aborted, true);
  assert.equal(lateDrafts, 0);

  // Explicit expansion gives one complete writing request the saved draft.
  const priorDraft = Array(975).fill('historical').join(' ');
  let writingCalls = 0;
  const options = {
    topic: 'Historical subject', duration: '10min', apiKey: 'fixture',
    researchMode: 'thorough', useCache: false, recordHistory: false,
    initialDraft: priorDraft,
    chatCompletionFn: async request => {
      writingCalls++;
      assert.ok(request.messages[1].content.includes(priorDraft));
      return { content: priorDraft };
    }
  };
  await assert.rejects(generateScript({ ...options,
    research: { query: 'Historical subject', official: [] }
  }), /research is insufficient/i);
  assert.equal(writingCalls, 0);
  await generateScript({ ...options, research: {
    query: 'Historical subject', gatheredAt: new Date().toISOString(), official: [{
      title: 'Historical subject archive',
      articleText: 'This institutional archive documents the historical subject and its verified timeline in an attributable record.',
      url: 'https://www.nationalarchives.gov.uk/example-record',
      source: 'The National Archives', hasArticleBody: true
    }]
  } });
  assert.equal(writingCalls, 1);

  const rejectedDraft = { script: 'Preserve this rejected narration exactly.', meta: { publishable: false, quality: { passes: false, issues: ['Specific factual rejection'] } } };
  let syntheses = 0;
  const bridgeResult = await handleGenerate({ topic: 'Historical subject', apiKey: 'fixture' }, { generateScript: async options => {
    assert.equal(options.researchMode, 'thorough'); return rejectedDraft;
  }, synthesizeScript: () => { syntheses++; throw new Error('cannot replace rejection'); } });
  assert.equal(bridgeResult.script, rejectedDraft.script);
  assert.equal(bridgeResult.meta, rejectedDraft.meta);
  assert.equal(syntheses, 0);
  await assert.rejects(handleGenerate({ topic: 'Historical subject', credentialSource: 'cms' }, {}), /Missing OpenRouter API key/);
  await assert.rejects(handleGenerate({ topic: 'Historical subject', apiKey: 'fixture' }, { generateScript: async () => { throw new Error('provider unavailable'); } }), /provider unavailable/);
  const preview = await handleGenerate({ topic: 'Historical subject', credentialSource: 'cms', allowOfflineSynthesis: true }, { synthesizeScript: () => ({ script: 'Explicit preview only.', meta: { publishable: true } }) });
  assert.equal(preview.meta.publishable, false);
  assert.equal(preview.meta.quality.factualAudit, false);
  console.log('WORKFLOW RECOVERY CHECKS PASSED: bounded deadlines, section/review resumption, exact corrections, and bridge quality contracts');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
