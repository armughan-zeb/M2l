'use strict';

/**
 * museScript — the Muse-search narration engine (default script engine).
 *
 * Replaces ScriptIT as the default (see adapters/script.js; ScriptIT stays
 * available behind MUSE_SCRIPT_ENGINE=scriptit, and the scriptit/ directory
 * stays in place — the daemon's script-readiness gate in muse-agent.js
 * pauses claims when it is missing).
 *
 * Why this exists (proven live, Nov 21 2026): ScriptIT failed a CMS job
 * three times in a row. Its research-synthesis bridge request asks a pool
 * worker for a JSON research briefing — the worker never delivered, the
 * 300s synthesis budget fired, and the result was 0 sources, 0 claims,
 * ungrounded P4. Its narration then asks for a ~2100-word narration in
 * ONE bridge request; a pool worker takes >14 min to compose that, but
 * the daemon inbox wall is 900s, so the request is cancelled at the wall.
 * Three consecutive narration requests (f4bbae9c, f85358ba, 722a5e0f) all
 * failed that way, then ScriptIT's 2700s budget fired and the job failed.
 * A Muse agent doing web search + writing directly produced a grounded
 * 2711-word narration in ~1 minute — the inbox round-trip per request is
 * the bottleneck, not the writing.
 *
 * What this path changes — and what it deliberately does NOT change:
 *
 * 1. Research is REUSED, not rebuilt. ScriptIT's research pipeline
 *    (scriptit/src/research/pipeline.js runResearchJob, quick mode) does
 *    direct web retrieval — Bing, Wikipedia, DuckDuckGo, Yandex, X,
 *    Reddit, Hacker News, RSS, GDELT/OpenAlex/Crossref/USGS — in batched
 *    parallel (3-at-a-time) inside a shared deadline; it is not the slow
 *    part (the 1-min example took 42.7s). Only the synthesis bridge
 *    request and the sequential draft bridge requests are replaced. The
 *    evidence ledger is built by the pipeline itself (in-process, no
 *    worker needed) and compacted to at most MAX_LEDGER_FACTS facts so
 *    the narration prompts stay small. If the pipeline fails outright,
 *    the fallback is a direct quick gatherResearch + buildEvidenceLedger
 *    (skipping the research store); if that fails too, generation
 *    proceeds UNGROUNDED but LABELLED — the same P4 fallback ScriptIT
 *    used — never blocking. No concurrency is raised against the search
 *    engines and no throttle is bypassed.
 *
 * 2. Narration is chunked. The target word count is split into sections
 *    of at most SECTION_WORDS words (~500), each minted as a SEPARATE
 *    `scriptit.completion` bridge request carrying the shared evidence
 *    ledger + that section's brief, all issued IN PARALLEL. A ~500-word
 *    section takes ~3-4 min to compose — well inside the 900s inbox wall
 *    (each request's wall is SECTION_TIMEOUT_MS, 9 min by default).
 *    Sections are retried once on failure; a shortfall is only tolerated
 *    while the total still meets the job's minimum word count (see the
 *    floor note below), otherwise the error is loud. No duplicate
 *    requests are minted for one section in one attempt — the retry is a
 *    fresh attempt only after the first request has definitively failed.
 *
 * 3. The editorial gate runs on the FULL concatenated draft, not per
 *    section: ScriptIT's own evaluateDraft (hook, specificity, evidence-
 *    support overlap >= 0.28 per factual sentence, valid markers,
 *    similarity advisory) is reused as-is. The factual audit is NOT
 *    reused because it is disabled in ScriptIT itself
 *    (factualAudit = null in scriptWriter.js) — the "review" in meta is
 *    this lexical gate, and meta reports factualAudit: null honestly.
 *
 * Word-count floor (verified in code — lib/mediaRules.js
 * minimumScriptWords, used by adapters/script.js validateScript):
 * target = minutes x 150 wpm, allowance 200 (or 15%), floor =
 * minutes x 135, minimum 80 — so 1300 words for 10 min, 127 for 1 min (the 15% allowance rounds 22.5 up to 23).
 * NOTE: the adapter's validateScript is warn-only for a short script
 * (ok:true + warning); the floor failure in ScriptIT lives in its
 * quality gate, and in this path as a loud throw below the floor, per
 * the build order (do not silently ship a short script).
 *
 * Meta contract (same shape ScriptIT's CLI produced, because the CMS
 * completion payload reads these): publishable, quality.issues,
 * ungrounded, grounded, researchComplete, evidence, factualAudit.
 * publishable is false when the gate fails or a section is missing
 * (partial draft) — the adapter retains such narration with editorial
 * warnings, exactly as it did for ScriptIT; it does not fail the job.
 * Ungrounded scripts are publishable-by-design when the gate passes
 * (user order, Nov 15 2026: research must never block generation) —
 * the P4 label travels in meta.ungrounded / quality warnings instead.
 *
 * Narration prompt rules are carried over from the live
 * scriptit.completion request (vidnets-runtime/temp/agent-bridge/
 * f4bbae9c034d09c4f7da51ede72bffd4/request.json): grounded in the ledger,
 * plain paragraphs, no headings/markdown, no invented facts, concrete
 * dates/names/numbers, evidence markers stripped before save.
 */

const { minimumScriptWords } = require('./mediaRules');

// ScriptIT modules are required LAZILY (inside the functions that use
// them), never at the top of this file. Two reasons: (1) the pure helpers
// in this module (chunking, ledger building, prompt building) must stay
// importable and unit-testable when ScriptIT's node_modules are absent;
// (2) adapters/script.js must keep loading — prep-cache and template
// paths included — on a machine where ScriptIT deps are missing, with a
// clear error only when the muse-search path itself is attempted.
// The daemon's script-readiness gate already pauses claims when
// ScriptIT is missing, so in production these requires always succeed.
function requireScriptIt(relativePath) {
  try {
    return require(`../scriptit/${relativePath}`);
  } catch (error) {
    if (error && error.code === 'MODULE_NOT_FOUND') {
      throw new Error(
        `Muse-search narration needs ScriptIT installed (missing ${relativePath}: ${error.message}). `
        + 'Run npm ci --omit=dev in scriptit, or rebuild the Docker image.',
      );
    }
    throw error;
  }
}

// One section must fit comfortably inside the 900s daemon inbox wall:
// ~500 words takes a pool worker ~3-4 min to compose.
const SECTION_WORDS = 500;
// The ledger carried into every section prompt: enough to ground the
// narration, small enough to keep the prompt (and the worker's read) fast.
const MAX_LEDGER_FACTS = 20;
const MAX_FACT_CHARS = 300;
// Per-section bridge wall: 9 min, inside the 900s inbox lease.
const DEFAULT_SECTION_TIMEOUT_MS = 9 * 60 * 1000;
// Whole-generation deadline: sections run in parallel, so this only
// binds when several sections each burn a retry.
const DEFAULT_TOTAL_TIMEOUT_MS = 30 * 60 * 1000;
// Spoken pace for the target word count (210 wpm, per the script contract).
const WORDS_PER_MINUTE = 210;

function countWords(text) {
  return String(text || '').split(/\s+/).filter(Boolean).length;
}

function targetWordsFor(job) {
  const minutes = Math.max(1, Number(job?.targetDurationMinutes) || 8);
  return Math.round(minutes * WORDS_PER_MINUTE);
}

// Narrative arc briefs, one per section. The first section hooks, the
// last resolves, the middle sections advance the story in chronology and
// each ends by setting up the next beat — the same shape ScriptIT's
// turning-points strategy used (turning-point, origin, escalation,
// outcome, legacy), sized to however many sections the target needs.
function sectionBrief(index, total) {
  if (total <= 1) {
    return 'Complete narration in one continuous pass: open with a specific, source-supported hook, establish context, develop the story through distinct evidence-supported beats, and resolve the title promise with a clean conclusion.';
  }
  if (index === 0) {
    return 'OPENING HOOK (first ~30 seconds): open with a specific, source-supported reason to care and a concise promise of the documented answer the story will reach. Establish the subject and context efficiently. Do not resolve the story here.';
  }
  if (index === total - 1) {
    return 'RESOLUTION AND CLOSING: resolve the opening promise with the documented conclusion. Do not introduce new threads or restart the introduction. End on a specific, decisive line — no recap summary, no "in conclusion".';
  }
  return `CORE BEAT ${index} of ${total - 2}: advance the story one distinct, evidence-supported development in chronological order. Each beat must change what the viewer understands. End the section by naturally setting up the next development.`;
}

// Split the target into the fewest sections of at most maxSectionWords,
// sized as evenly as possible so no section is a stub (2100 words ->
// 5 x 420, not 4 x 500 + 1 x 100).
function planSections(totalWords, maxSectionWords = SECTION_WORDS) {
  const total = Math.max(1, Math.round(totalWords) || 1);
  const count = Math.max(1, Math.ceil(total / Math.max(1, maxSectionWords)));
  const base = Math.floor(total / count);
  const remainder = total - base * count;
  const sections = [];
  for (let i = 0; i < count; i += 1) {
    sections.push({
      index: i,
      targetWords: base + (i < remainder ? 1 : 0),
      brief: sectionBrief(i, count),
    });
  }
  return sections;
}

// Compact the (already relevance/confidence-sorted) ledger claims into a
// small fact list for the section prompts. Claim text is truncated: the
// ledger's full claim bodies (up to 6000 chars) would bloat every prompt.
function buildLedgerFacts(ledger, maxFacts = MAX_LEDGER_FACTS) {
  const claims = Array.isArray(ledger?.claims) ? ledger.claims : [];
  return claims.slice(0, Math.max(0, maxFacts)).map((claim) => ({
    id: String(claim.id || ''),
    text: String(claim.text || '').replace(/\s+/g, ' ').trim().slice(0, MAX_FACT_CHARS),
    source: String(claim.source || claim.publisher || 'Unknown source'),
    url: String(claim.url || ''),
    publishedAt: String(claim.publishedAt || ''),
  })).filter((fact) => fact.id && fact.text);
}

function ledgerFactsText(facts, grounded) {
  if (!grounded || !facts.length) {
    return 'NO VERIFIED RESEARCH AVAILABLE — UNGROUNDED SCRIPT (P4):\n'
      + 'No attributable external evidence could be retrieved for this topic. Write a general narration only: '
      + 'do not state specific dates, numbers, names of people or places beyond the topic itself, quotations, or causal claims. '
      + 'Keep every sentence generic enough to be true without a source. This script is labelled ungrounded and requires fact-checking before production.';
  }
  const lines = [
    'EVIDENCE LEDGER — only use factual claims supported by the facts below.',
    'The date in [brackets] is when the source was published. Prefer the most recent dated claims for anything time-sensitive.',
    'Official-source claims outrank search snippets. Headline-only claims may establish that an event was reported, but do not add details that the headline does not support.',
    'If the ledger does not support a detail, omit it or state uncertainty plainly.',
    '',
  ];
  for (const fact of facts) {
    const when = fact.publishedAt ? ` [published ${fact.publishedAt}]` : '';
    lines.push(`${fact.id}: ${fact.text} (${fact.source}${when})`);
  }
  return lines.join('\n');
}

const SECTION_SYSTEM_PROMPT = [
  'You are a careful YouTube narration writer. Write a distinct, engaging script that sounds like a thoughtful human creator, not a content factory.',
  '',
  'GROUNDING RULES',
  'Use only facts supported by the evidence ledger. Do not invent dates, quotes, statistics, motivations, events, names, or outcomes. Do not turn a headline into a certainty. Do not infer causation from a number or turn several separate claims into a new claim. Every factual sentence must be directly traceable to one or more ledger claims. If evidence is incomplete or disputed, use plain qualifying language or leave it out.',
  'Your training memory is older than the supplied research. When the evidence ledger already contains a detail (a release date, a trailer, a price, a stat, an event, a plot point, a name), report that detail — never claim it is "undisclosed", "not yet revealed", "unknown", or "waiting for official confirmation". Those cop-outs are only acceptable when the supplied research genuinely has no relevant material on the point.',
  'Treat the evidence ledger as a closed world: if a sentence cannot be mapped to one or more ledger claims, remove it or turn it into a neutral connective sentence. Do not invent audience reactions, what "everyone" assumes, or what a detail supposedly means.',
  '',
  'INTERNAL EVIDENCE MARKERS',
  'While drafting, append a real marker such as <evidence:claim-a1b2c3> to every sentence that contains factual claims. Use only claim IDs that exist in the evidence ledger; multiple IDs may be comma-separated. These markers are removed automatically before the script reaches the viewer; never explain them or invent IDs. A sentence with unsupported facts must be rewritten or removed.',
  '',
  'AUTHENTICITY & VOICE CONSTRAINTS',
  'Never use "in today\'s video", "let\'s dive in", "let\'s unpack", "without further ado", or "here\'s the thing". Never use the cliché "it\'s not X, it\'s Y". Do not use leverage, utilize, streamline, robust, delve, testament to, unwavering, tapestry, or crucible. Vary sentence length aggressively: alternate punchy 3–6 word statements with longer rhythmic clauses. Use concrete names, places, dates, objects, or numbers only when the ledger supports them.',
  '',
  'OUTPUT RULES',
  'Return the finished spoken narration as plain text, with blank lines separating natural narration beats. Do not wrap narration in JSON, headings, production notes, or markdown. No headings, labels, lists, brackets, asterisks, emojis, source links, metadata, or preamble. The temporary <evidence:claim-N> markers are the only exception. Use at most three rhetorical questions in the entire section. Do not explain your assignment, research process, or limitations. Start directly with the story.',
].join('\n');

function buildSectionMessages({ job, facts, section, totalSections, totalTargetWords, grounded }) {
  const title = String(job?.title || 'Documentary Investigation').trim();
  const description = String(job?.premise || job?.description || '').trim();
  const channelName = String(job?.channel?.name || '').trim();
  const niche = String(job?.channel?.niche || 'Documentary').trim();
  const userLines = [
    `TITLE: ${title}`,
    `TOPIC: ${title}`,
  ];
  if (description) userLines.push(`TITLE CONTEXT / DESCRIPTION:\n${description}\nUse this as the editorial promise and scope for the video. It is a planning brief, not evidence: do not treat its claims as verified facts, and do not add details that the research ledger does not support.`);
  if (channelName) userLines.push(`CHANNEL: ${channelName}`);
  userLines.push(`CONTENT TYPE: documentary (${niche}).`);
  userLines.push('');
  userLines.push(`THIS SECTION: section ${section.index + 1} of ${totalSections}. Write about ${section.targetWords} words for this section (part of a ${totalTargetWords}-word narration).`);
  userLines.push(`SECTION BRIEF: ${section.brief}`);
  userLines.push('');
  userLines.push(ledgerFactsText(facts, grounded));
  return [
    { role: 'system', content: SECTION_SYSTEM_PROMPT },
    { role: 'user', content: userLines.join('\n') },
  ];
}

// Strip the temporary <evidence:...> markers and any markdown/headings a
// worker may have added despite the prompt, so the saved script is clean
// spoken narration. Runs AFTER the editorial gate (the gate validates the
// markers), never before it.
function cleanSectionText(text) {
  return String(text || '')
    .replace(/<evidence:[^>]*>/g, '')
    .replace(/^#{1,6}\s+.*$/gm, '')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
    .replace(/^\s*(Section|Part)\s+\d+\s*[:.]\s*/gim, '')
    .split('\n')
    .map((line) => line.trim())
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function concatenateSections(results) {
  return results
    .slice()
    .sort((a, b) => a.index - b.index)
    .map((result) => String(result.text || '').trim())
    .filter(Boolean)
    .join('\n\n');
}

function defaultBridgeFn({ messages, jobId, signal, timeoutMs }) {
  const { agentBridgeChat } = requireScriptIt('src/providers/agentBridge');
  const apiKey = String(process.env.SCRIPTIT_AGENT_KEY || 'agent-bridge:local-user').trim();
  return agentBridgeChat({
    apiKey,
    messages,
    operation: 'scriptit.completion',
    kind: 'text',
    jobId: jobId || '',
    timeoutMs,
    // ~500 words out: the live single-shot request used a 6800-token hint
    // for 2100 words, so ~1600 here; 2000 leaves headroom.
    maxTokens: 2000,
    signal,
  });
}

// Primary research: the full ScriptIT research pipeline, fast pass only
// (quick mode returns after the fast pass; backgroundDeep off so the call
// resolves with the fast result instead of returning early with a deep
// pass pending). Fallback: the same providers called directly, with the
// ledger built in-process — no research store involved, so a broken or
// locked store can never take the script stage down with it.
async function defaultResearchFn(topic, { signal } = {}) {
  const { runResearchJob } = requireScriptIt('src/research/pipeline');
  try {
    const result = await runResearchJob({
      topic, quick: true, backgroundDeep: false, includeReddit: false, signal,
    });
    if (result?.evidence) {
      return { evidence: result.evidence, research: result.research || null, warnings: result.warnings || [] };
    }
  } catch (error) {
    if (signal?.aborted) throw error;
    // Fall through to the direct gather below.
  }
  const { gatherResearch } = requireScriptIt('src/agents/researcher');
  const { buildEvidenceLedger } = requireScriptIt('src/agents/evidence');
  const research = await gatherResearch(topic, { quick: true, fetchLimit: 5, includeReddit: false, signal });
  return { evidence: buildEvidenceLedger(research || {}), research, warnings: [] };
}

function isMissingDepsError(error) {
  return String(error?.message || '').includes('Muse-search narration needs ScriptIT installed');
}

function combineSignals(signals) {
  const live = signals.filter(Boolean);
  if (!live.length) return undefined;
  if (typeof AbortSignal.any === 'function') return AbortSignal.any(live);
  return live[0];
}

async function generateMuseScript(job, workDir, {
  signal,
  onDetail,
  onProgress,
  researchFn = defaultResearchFn,
  bridgeFn = defaultBridgeFn,
  sectionWords = SECTION_WORDS,
  sectionTimeoutMs = Number(process.env.MUSE_SCRIPT_SECTION_TIMEOUT_MS) || DEFAULT_SECTION_TIMEOUT_MS,
  totalTimeoutMs = Number(process.env.MUSE_SCRIPT_TOTAL_TIMEOUT_MS) || DEFAULT_TOTAL_TIMEOUT_MS,
} = {}) {
  const jobId = String(job?.jobId || '');
  const title = String(job?.title || 'Documentary Investigation').trim();
  const minutes = Math.max(1, Number(job?.targetDurationMinutes) || 8);
  const totalTargetWords = targetWordsFor(job);
  const minimum = minimumScriptWords(minutes);
  const sections = planSections(totalTargetWords, sectionWords);

  onProgress?.(5, `Researching "${title}" with direct web search`);
  let evidence = null;
  let facts = [];
  let researchSources = 0;
  let researchClaims = 0;
  let researchSufficient = false;
  let researchError = '';
  try {
    const result = await researchFn(title, { signal });
    evidence = result?.evidence || null;
    if (evidence) {
      try {
        facts = buildLedgerFacts(evidence);
      } catch (ledgerError) {
        researchError = `evidence ledger failed: ${ledgerError.message}`;
        evidence = null;
      }
      if (evidence) {
        researchSources = Number(evidence.sourceCount) || 0;
        researchClaims = (evidence.claims || []).length;
        researchSufficient = evidence.sufficient === true;
      }
    }
  } catch (error) {
    if (signal?.aborted) throw error;
    // Missing ScriptIT deps must fail loudly, not masquerade as "no
    // research found" (which would silently produce an ungrounded script).
    if (isMissingDepsError(error)) throw error;
    researchError = error.message || String(error);
  }
  const grounded = facts.length > 0;
  if (grounded) {
    onDetail?.(`Research found ${researchClaims} ledger claims from ${researchSources} sources; writing ${sections.length} sections in parallel`);
  } else {
    onDetail?.(`Research returned no usable evidence (${researchError || 'no attributable claims'}); writing UNGROUNDED narration, labelled ungrounded`);
  }

  // One shared deadline for the whole narration: sections run in parallel
  // and each aborts when it fires, so a stuck section cannot hang the job.
  const deadlineController = new AbortController();
  const deadlineTimer = setTimeout(() => deadlineController.abort(), Math.max(1000, totalTimeoutMs));
  const combinedSignal = combineSignals([signal, deadlineController.signal]);
  try {
    const attemptSections = async (pending) => Promise.allSettled(pending.map(async (section) => {
      const messages = buildSectionMessages({
        job, facts, section, totalSections: sections.length, totalTargetWords, grounded,
      });
      const answer = await bridgeFn({ messages, jobId, section, signal: combinedSignal, timeoutMs: sectionTimeoutMs });
      const text = String(typeof answer === 'string' ? answer : answer?.content || '').trim();
      if (!text) throw new Error(`Section ${section.index + 1} returned empty narration`);
      return { index: section.index, text };
    }));

    let results = await attemptSections(sections);
    // A missing-deps failure in the bridge must fail loudly, not surface
    // later as a misleading "narration too short" error.
    const missingDeps = results.find((r) => r.status === 'rejected' && isMissingDepsError(r.reason));
    if (missingDeps) throw missingDeps.reason;
    let failed = sections.filter((_, i) => results[i].status === 'rejected');
    if (failed.length && !combinedSignal?.aborted) {
      onDetail?.(`${failed.length} of ${sections.length} sections failed; retrying each once`);
      const retries = await attemptSections(failed);
      retries.forEach((retry, i) => {
        if (retry.status === 'fulfilled') results[sections.indexOf(failed[i])] = retry;
      });
      failed = sections.filter((_, i) => results[i].status === 'rejected');
    }

    const succeeded = results.filter((r) => r.status === 'fulfilled').map((r) => r.value);
    // The gate runs on the RAW concatenation (markers intact — it
    // validates them), exactly as ScriptIT gated its single draft.
    const rawScript = concatenateSections(succeeded);
    const failedSections = failed.map((s) => s.index + 1);
    if (failedSections.length) {
      onDetail?.(`Narration sections failed after retry: ${failedSections.join(', ')} (${succeeded.length}/${sections.length} sections delivered)`);
    }
    const scriptText = cleanSectionText(rawScript);
    const words = countWords(scriptText);
    if (words < minimum) {
      throw new Error(
        `Muse-search narration is too short: ${words} words, minimum ${minimum} for the requested ${minutes}-minute runtime`
        + (failedSections.length ? ` (${failedSections.length} of ${sections.length} sections failed: ${failedSections.join(', ')})` : '')
        + '. Shorten the requested runtime in the CMS or generate more narration. No rendered master was produced.',
      );
    }

    // Editorial gate on the full draft (hook, specificity, evidence
    // support, valid markers). Ungrounded drafts get the same
    // specificity waiver ScriptIT applied (evaluateDraft handles it).
    // A gate failure never throws: it is reported honestly in meta and
    // the adapter retains the narration with editorial warnings.
    let quality = null;
    try {
      const { evaluateDraft } = requireScriptIt('src/agents/scriptWriter');
      quality = evaluateDraft(rawScript, {
        topic: title,
        wordTarget: totalTargetWords,
        previousScripts: [],
        evidence: evidence || { claims: [] },
        contentType: 'documentary',
        duration: String(minutes),
      });
    } catch (gateError) {
      onDetail?.(`Editorial gate could not run (${gateError.message}); delivering narration without a gate verdict`);
    }
    const gateIssues = [...(quality?.issues || [])];
    const gateWarnings = [...(quality?.warnings || [])];
    if (failedSections.length) {
      gateIssues.push(`Narration assembled from ${succeeded.length} of ${sections.length} sections; sections ${failedSections.join(', ')} failed after retry.`);
    }
    if (!grounded) {
      gateWarnings.push('Ungrounded script (P4): no verified research — verify facts before production.');
    }
    const publishable = (quality ? quality.passes : true) && failedSections.length === 0;
    onProgress?.(90, `Narration ready (${words} words, ${succeeded.length}/${sections.length} sections${grounded ? '' : ', UNGROUNDED'}${publishable ? '' : ', non-publishable draft'})`);
    return {
      scriptText,
      words,
      meta: {
        engine: 'muse-search',
        grounded,
        ungrounded: !grounded,
        publishable,
        researchComplete: grounded && researchSufficient,
        researchSources,
        researchClaims,
        researchSufficient,
        researchError: researchError || undefined,
        sections: sections.length,
        sectionsFailed: failedSections.length,
        failedSections,
        targetWords: totalTargetWords,
        evidence: evidence ? {
          sourceCount: researchSources,
          claims: researchClaims,
          sufficient: researchSufficient,
          coverage: evidence.coverage || null,
          gatheredAt: evidence.gatheredAt || '',
          latestClaim: evidence.latestClaimDate || '',
        } : { sourceCount: 0, claims: 0, sufficient: false, coverage: null, gatheredAt: '', latestClaim: '' },
        quality: quality ? {
          words: quality.words ?? words,
          similarity: quality.maxSimilarity ?? 0,
          score: quality.score ?? null,
          passes: quality.passes,
          hook: quality.hook,
          reviewPerformed: true,
          specificity: quality.specificity || null,
          topicFit: quality.topicQuality || null,
          issues: gateIssues,
          warnings: gateWarnings,
          invalidEvidenceMarkers: quality.invalidEvidenceMarkers || [],
          supportIssues: quality.supportIssues || [],
          markerIssues: quality.markerIssues || [],
          // The factual audit is disabled in ScriptIT itself
          // (factualAudit = null in scriptWriter.js); the review in this
          // meta is the lexical gate above, not a model audit.
          factualAudit: null,
          factualAuditReport: null,
        } : {
          words,
          similarity: 0,
          score: null,
          passes: false,
          hook: false,
          reviewPerformed: false,
          specificity: null,
          topicFit: null,
          issues: [...gateIssues, 'Editorial gate could not run'],
          warnings: gateWarnings,
          factualAudit: null,
          factualAuditReport: null,
        },
      },
    };
  } finally {
    clearTimeout(deadlineTimer);
  }
}

module.exports = {
  SECTION_WORDS,
  MAX_LEDGER_FACTS,
  WORDS_PER_MINUTE,
  countWords,
  targetWordsFor,
  planSections,
  sectionBrief,
  buildLedgerFacts,
  ledgerFactsText,
  buildSectionMessages,
  cleanSectionText,
  concatenateSections,
  generateMuseScript,
};
