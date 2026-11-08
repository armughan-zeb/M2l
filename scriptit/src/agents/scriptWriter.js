function stripThinkingProcess(raw) {
  let text = String(raw || '');
  text = text.replace(/<think>[\s\S]*?<\/think>/gi, '');
  if (/^\s*Thinking Process:/i.test(text)) {
    const match = text.match(/(?:(?:\d+\.\s+)?\*\*(?:Drafting[^*]*|Final Polish|Actual Script|Final Script|Spoken Script|Narration)[^*]*\*\*:\s*|\b(?:Here is the (?:script|narration)|Spoken script):\s*)([\s\S]+)$/i);
    if (match) return match[1].trim();
    const paragraphs = text.split(/\n\s*\n/);
    const narrativeIndex = paragraphs.findIndex((p, idx) => idx > 0 && !/^\s*(?:\d+\.|\*|-|Thinking Process|Analyze|Strategy|Critique|Review)/i.test(p));
    if (narrativeIndex > 0) return paragraphs.slice(narrativeIndex).join('\n\n').trim();
  }
  return text.trim();
}

function writerOutput(content, target) {
  const stripped = stripThinkingProcess(content);
  const script = stripped.replace(/<\/?evidence(?::[^>]+)?>/gi, '').trim();
  const words = script.split(/\s+/).filter(Boolean).length;
  const issues = [];
  if (!script) issues.push('Writer returned empty narration');
  const allowance = target >= 1000 ? 200 : Math.min(200, Math.round(target * 0.15));
  const minRequired = Math.max(80, Math.min(Math.ceil(target * 0.85), target >= 1400 ? 1300 : target - allowance));
  if (words < minRequired) issues.push('Writer output is below the requested duration');
  const warnings = words > Math.max(180, Math.floor(target * 1.35)) ? ['Narration is longer than the target; continue without rewriting'] : [];
  return { script, words, issues, warnings, ok: issues.length === 0 };
}

const { chatCompletion } = require('../providers/openrouter');
const { gatherResearch } = require('./researcher');
const { buildBrief, briefToPromptText } = require('./compressor');
const { buildEvidenceLedger, ledgerToPromptText } = require('./evidence');
const { createNarrativePlan, planToPromptText } = require('./narrativePlanner');
const { recentHistory, recordGeneration } = require('./generationHistory');
const { assessScript } = require('./scriptQuality');
const { systemMessage, buildUserPrompt } = require('../prompts/scriptPrompts');
const { wordsForDuration } = require('../utils/duration');
const { buildNarratorProfile, narratorProfileToPromptText } = require('./narratorVideo');
const cache = require('../cache/cache');
const { runResearchJob } = require('../research/pipeline');
const { getResearchStore } = require('../research/store');
const { buildResearchPlan, buildCoverageReport } = require('../research/questionPlanner');
const { normalizeTopic, researchSubject } = require('../research/topicNormalizer');
const { runUniversalEngine } = require('../engine/universalEngine');
const { generateHook, buildDeterministicHooks } = require('../engine/agents/hookAgent');
const { deSlopScript, cleanVoiceRules, evaluateVoiceAuthenticity } = require('../engine/agents/voiceAgent');
const { generateScriptFromStructure } = require('./structureCloner');
const { auditNarration, auditConsistency, correctFindings } = require('./factualAudit');
const { writeSections } = require('./sectionWriter');
const { boundedCall } = require('../utils/execution');
const crypto = require('crypto');

// Bump this whenever research selection, source authority, or evidence
// shaping changes. Old cached search results may be structurally valid while
// still missing the newer first-party evidence lane.
const RESEARCH_CACHE_VERSION = 'evidence-v14';

function isReusableResearchJob(job) {
  if (!job || !job.result || !job.result.research || !job.result.brief) return false;
  // A partial result is intentionally persisted so it can be inspected and
  // resumed, but it must never become a successful cache hit. Without this
  // guard, every new generation could reopen the same incomplete job until
  // its 24-hour cache window expired.
  if (!['fast_ready', 'context_ready'].includes(String(job.status || ''))) return false;
  return job.result.evidence?.sufficient !== false;
}

function narratorTextFor(profile) {
  return narratorProfileToPromptText(profile?.profile || profile);
}

function scopeResearch(research, originalTopic) {
  if (!/\b(?:history|heritage|legacy|nostalgic|(?:hidden|forgotten|untold|secret|true|real)\s+story)\b/i.test(originalTopic)) return research;
  const scoped = { ...research };
  let keptCount = 0;
  for (const [key, items] of Object.entries(research)) {
    if (!Array.isArray(items) || key === 'errors') continue;
    scoped[key] = items.filter(item => {
      const label = `${item?.title || ''} ${item?.url || ''}`;
      if (/\b(?:configurator|used|sale|pricing|review|models|launches|forecast|trims)\b/i.test(label)) return false;
      return /\b(?:history|heritage|legacy|origin|biography|original|classic)\b/i.test(label)
        || /\b(?:18|19|20)\d{2}\b/.test(item?.articleText || '');
    });
    keptCount += scoped[key].length;
  }
  // Prevent total research collapse: if historical scoping leaves 0 items,
  // retain the original research bundle so we never produce an artificial "no attributable claims" failure
  if (keptCount === 0) return research;
  return scoped;
}

function parseJsonResponse(value) {
  const text = String(value || '').replace(/```json|```/gi, '').trim();
  const start = text.indexOf('{');
  const end = text.lastIndexOf('}');
  if (start < 0 || end <= start) return null;
  try {
    return JSON.parse(text.slice(start, end + 1));
  } catch (_) {
    return null;
  }
}

const SUPPORT_STOPWORDS = new Set([
  'about', 'after', 'again', 'also', 'because', 'before', 'being', 'between', 'could',
  'from', 'have', 'into', 'more', 'most', 'over', 'said', 'some', 'that', 'their',
  'there', 'these', 'they', 'this', 'those', 'through', 'were', 'which', 'while', 'with',
]);

const SOCIAL_REACTION_PATTERN = /\b(?:ishowspeed|streamers?|fans?|the internet reacts?|what did i just watch|i['’]?ve ever prayed|whet your appetite)\b|#gtavi|\bpic\b|👏|[\u{1F300}-\u{1FAFF}]/iu;
const GTA_ONLINE_NOISE_PATTERN = /\b(?:co-?operative heists?|stunt races?|adversary modes?|social spaces?|gta online)\b/i;

function supportTokens(value) {
  return new Set(String(value || '').toLowerCase().match(/[a-z0-9]+/g) || []);
}

function factualSentence(sentence) {
  const factualLanguage = /\b\d{4}\b|\b\d+(?:\.\d+)?\s*(?:billion|million|percent|%)?\b|\b(?:released?|launched?|announced|confirmed|reported|published|founded|created|developed|acquired|sold|earned|revenue|sales|financial|runtime|available|exclusive|official|playstation|xbox|nintendo|netflix|steam)\b|\b(?:testament|captivat\w*|eagerly awaited|stunning visuals|high bar)\b/i.test(sentence);
  const namedEntity = /\b[A-Z][A-Za-z0-9'-]+(?:\s+[A-Z][A-Za-z0-9'-]+){1,3}\b/.test(sentence);
  return factualLanguage || namedEntity;
}

function meaningfulSupportWords(sentence) {
  return [...supportTokens(sentence)].filter((word) => word.length > 3 && !SUPPORT_STOPWORDS.has(word));
}

function claimOverlap(words, claimWords) {
  if (!words.length || !claimWords || !claimWords.size) return 0;
  return words.filter((word) => claimWords.has(word)).length / words.length;
}

function evidenceSentences(value) {
  // Writers append markers after sentence punctuation. Move those markers
  // inside the sentence for analysis so they cannot cite the next sentence.
  const grouped = String(value || '').replace(/<\/evidence(?::[^>]+)?>/gi, '').replace(/([.!?])([ \t]*(?:<evidence:\s*[^>]+>[ \t]*)+)/gi, '$2$1');
  return grouped.match(/(?:[^.!?]|\.(?=\d))+(?:[.!?](?!\d)|$)/g) || [];
}

function unsupportedSentenceIssues(script, evidence) {
  // Synthetic (model-written dossier) claims are context only, never support.
  const claims = (evidence.claims || []).filter((claim) => !claim.synthetic).map((claim) => supportTokens(claim.text));
  if (!claims.length) return [];
  const sentences = evidenceSentences(script);
  const issues = [];
  for (const sentence of sentences) {
    const words = meaningfulSupportWords(sentence);
    const factLike = factualSentence(sentence);
    if (!factLike || words.length < 4 || (sentence.trim().endsWith('?') && !/\b\d{4}\b|\b\d+(?:\.\d+)?\b/i.test(sentence))) continue;
    const overlap = Math.max(...claims.map((claimWords) => words.filter((word) => claimWords.has(word)).length / words.length));
    if (overlap < 0.28) {
      issues.push(`Unsupported factual sentence: "${sentence.trim().slice(0, 180)}"`);
      if (issues.length >= 4) break;
    }
  }
  return issues;
}

function sanitizeUnsupportedSentences(raw, evidence = {}) {
  const claimById = new Map((evidence.claims || []).filter((claim) => !claim.synthetic).map((claim) => [claim.id, supportTokens(claim.text)]));
  const allClaims = [...claimById.values()];
  if (!allClaims.length) return String(raw || '');

  return String(raw || '')
    .split(/\n\s*\n/)
    .map((paragraph) => {
      const sentences = evidenceSentences(paragraph);
      return sentences.filter((rawSentence) => {
        const sentence = rawSentence.replace(/<evidence:\s*[^>]+>/gi, '').trim();
        if (!sentence) return false;
        if (SOCIAL_REACTION_PATTERN.test(sentence)) return false;
        if (!factualSentence(sentence)) return true;
        if (sentence.endsWith('?') && !/\b\d{4}\b|\b\d+(?:\.\d+)?\b/i.test(sentence)) return true;
        const words = meaningfulSupportWords(sentence);
        if (words.length < 4) return true;
        const globalOverlap = Math.max(0, ...allClaims.map((claimWords) => claimOverlap(words, claimWords)));
        const ids = (rawSentence.match(/claim-[a-z0-9]+/gi) || []).filter((id) => claimById.has(id));
        const citedWords = ids.length ? new Set(ids.flatMap((id) => [...claimById.get(id)])) : null;
        const citedOverlap = citedWords ? claimOverlap(words, citedWords) : 1;
        return globalOverlap >= 0.28 && citedOverlap >= 0.24;
      }).join(' ').trim();
    })
    .filter(Boolean)
    .join('\n\n')
    .trim();
}

function mergeSafeDrafts(primary, supplement, wordTarget) {
  const target = Math.max(120, Number(wordTarget) || 500);
  const maxWords = Math.ceil(target * 1.08);
  const output = String(primary || '').split(/\n\s*\n/).map((part) => part.trim()).filter(Boolean);
  const tokenSets = output.map((part) => supportTokens(part));
  let words = output.join(' ').split(/\s+/).filter(Boolean).length;

  for (const paragraph of String(supplement || '').split(/\n\s*\n/).map((part) => part.trim()).filter(Boolean)) {
    if (words >= target * 0.95) break;
    const paragraphWords = paragraph.replace(/<evidence:\s*[^>]+>/gi, '').split(/\s+/).filter(Boolean);
    if (!paragraphWords.length || words + paragraphWords.length > maxWords) continue;
    const candidate = supportTokens(paragraph);
    const duplicate = tokenSets.some((existing) => {
      if (!candidate.size || !existing.size) return false;
      let shared = 0;
      for (const token of candidate) if (existing.has(token)) shared += 1;
      return shared / Math.min(candidate.size, existing.size) >= 0.82;
    });
    if (duplicate) continue;
    output.push(paragraph);
    tokenSets.push(candidate);
    words += paragraphWords.length;
  }
  return output.join('\n\n').trim();
}

function markerSupportIssues(raw, evidence) {
  const claimById = new Map((evidence.claims || []).filter((claim) => !claim.synthetic).map((claim) => [claim.id, supportTokens(claim.text)]));
  const sentences = evidenceSentences(raw);
  const issues = [];
  for (const rawSentence of sentences) {
    const sentence = rawSentence.replace(/<evidence:\s*[^>]+>/gi, '').trim();
    const words = meaningfulSupportWords(sentence);
    const factLike = factualSentence(sentence);
    // A neutral hook question such as "What exactly has been confirmed?"
    // contains audit vocabulary but makes no factual claim. It should not be
    // forced to match a ledger claim or lose its narrative function.
    if (sentence.endsWith('?') && !/\b\d{4}\b|\b\d+(?:\.\d+)?\b/i.test(sentence)) continue;
    if (!factLike || words.length < 4) continue;
    const ids = (rawSentence.match(/claim-[a-z0-9]+/gi) || []).filter((id) => claimById.has(id));
    // Markers are an internal drafting aid. Some models omit them, so their
    // absence must not make a directly supportable sentence fail the gate.
    if (!ids.length) continue;
    const citedWords = new Set(ids.flatMap((id) => [...claimById.get(id)]));
    const overlap = claimOverlap(words, citedWords);
    if (overlap < 0.24) {
      issues.push(`Sentence does not match its cited evidence: "${sentence.slice(0, 180)}"`);
      if (issues.length >= 4) break;
    }
  }
  return issues;
}

function repairEvidenceMarkers(raw, evidence) {
  const claims = (evidence.claims || []).filter((claim) => !claim.synthetic).map(claim => ({ id: claim.id, words: supportTokens(claim.text) }));
  return String(raw || '').split(/\n\s*\n/).map(paragraph => evidenceSentences(paragraph).map(sentence => {
    const markers = sentence.match(/<evidence:\s*[^>]+>/gi);
    if (!markers) return sentence.trim();
    const words = meaningfulSupportWords(sentence.replace(/<evidence:\s*[^>]+>/gi, ''));
    const ids = markers.join(' ').match(/claim-[a-z0-9]+/gi) || [];
    const cited = claims.filter(claim => ids.includes(claim.id));
    if (cited.some(claim => claimOverlap(words, claim.words) >= 0.24)) return sentence.trim();
    const best = claims.map(claim => ({ id: claim.id, overlap: claimOverlap(words, claim.words) })).sort((a, b) => b.overlap - a.overlap)[0];
    if (!best || best.overlap < 0.36) return sentence.trim();
    // This fixes a mismatched drafting marker, not the truth of its sentence.
    // The complete semantic factual audit still reviews every subclaim.
    return sentence.replace(/<evidence:\s*[^>]+>/gi, `<evidence:${best.id}>`).trim();
  }).join(' ')).join('\n\n');
}

function buildEvidenceFallbackScript(topic, evidence, wordTarget) {
  const candidateClaims = evidence.atomicClaims && evidence.atomicClaims.length
    ? evidence.atomicClaims
    : (evidence.claims || []);
  const topicText = String(topic || '').toLowerCase();
  const claims = candidateClaims
    .filter((claim) => ['high', 'medium'].includes(claim.confidence)
      && ((claim.official && claim.relevance > 0) || claim.relevance >= 1)
      && !/^Hacker News/i.test(claim.source || '')
      && claim.sourceTier !== 'community'
      && claim.sourceTier !== 'discovery'
      && !SOCIAL_REACTION_PATTERN.test(`${claim.title || ''} ${claim.text || ''}`)
      && (!/\bgta\s*(?:6|vi)\b/i.test(topicText)
        || /\bgta\s*(?:6|vi)\b|grand theft auto vi|jason|lucia|leonida|vice city/i.test(`${claim.title || ''} ${claim.text || ''}`))
      && (!/\bsan andreas\b/i.test(topicText)
        || /\bsan andreas\b|carl johnson|los santos|cj\b/i.test(`${claim.title || ''} ${claim.text || ''}`)))
    .sort((a, b) => {
      if (b.official !== a.official) return Number(b.official) - Number(a.official);
      if (b.hasArticleBody !== a.hasArticleBody) return Number(b.hasArticleBody) - Number(a.hasArticleBody);
      if (b.relevance !== a.relevance) return b.relevance - a.relevance;
      return String(b.publishedAt || '').localeCompare(String(a.publishedAt || ''));
    })
    .slice(0, 80);
  const intro = `What is actually confirmed about ${topic}? Start with the details that can be stated plainly, then follow the record one beat at a time.`;
  const paragraphs = [intro];
  const seen = new Set();
  const target = Math.max(120, Number(wordTarget) || 500);
  const maxFallbackWords = Math.ceil(target * 1.08);
  const transitions = [
    'The first detail sets the frame.',
    'The timeline becomes clearer with the next point.',
    'Now the story turns to the detail that changes the context.',
    'A second line of evidence adds another piece.',
    'The distinction matters here.',
    'That leads to a different part of the story.',
    'Another documented detail sharpens the picture.',
    'From there, the sequence moves forward.',
    'The next development adds useful context.',
    'One more detail changes how the earlier point reads.',
    'Seen in order, the following moment becomes important.',
    'The record then shifts to another part of the world.',
    'A separate thread now enters the story.',
    'What follows gives that earlier detail more weight.',
    'The broader picture comes into focus through the next event.',
    'This is where another part of the timeline connects.',
    'The story widens with a further documented point.',
    'Next comes a detail with a different consequence.',
    'The sequence continues through another clear development.',
    'Finally, one more piece helps complete the picture.',
  ];
  let words = intro.split(/\s+/).length;
  const claimLead = (claim) => {
    if (claim.official || claim.sourceTier === 'primary') return 'The primary record states:';
    if (claim.sourceTier === 'reference') return 'The established reference explains:';
    if (claim.sourceTier === 'publisher' && claim.hasArticleBody) return 'Independent reporting adds:';
    return 'Another attributable source records:';
  };
  for (let index = 0; index < claims.length && words < target * 1.1; index += 1) {
    const claim = claims[index];
    let text = String(claim.text || '')
      .replace(/&#x27;|&#39;/gi, "'")
      .replace(/&quot;/gi, '"')
      .replace(/&#32;|&nbsp;/gi, ' ')
      .replace(/&amp;/gi, '&');
    if (claim.title && text.toLowerCase().startsWith(`${String(claim.title).toLowerCase()}.`)) {
      text = text.slice(String(claim.title).length + 1).trim();
    }
    // Protect initials such as "p.m." before splitting. A naive sentence
    // regex would turn a clean time into "3 p." and "m.", which sounds
    // broken when the evidence rescue is read aloud.
    const sentenceText = text.replace(/\b([ap])\.\s*m\./gi, '$1m');
    const sentences = sentenceText.match(/[^.!?]+(?:[.!?](?!\d)|$)/g) || [];
    const selected = sentences
      .map((sentence) => sentence.trim())
      .filter((sentence) => sentence.length >= 35)
      .filter((sentence) => !SOCIAL_REACTION_PATTERN.test(sentence))
      .filter((sentence) => !(/\bgta\s*(?:6|vi)\b/i.test(topicText) && GTA_ONLINE_NOISE_PATTERN.test(sentence) && !/\bgta\s*(?:6|vi)\b|grand theft auto vi/i.test(sentence)))
      .slice(0, 6);
    const key = selected.join(' ').toLowerCase().slice(0, 260);
    if (!selected.length || seen.has(key)) continue;
    seen.add(key);
    const marked = selected
      .map((sentence) => `${sentence} <evidence:${claim.id}>`)
      .join(' ');
    const candidate = `${transitions[index % transitions.length]} ${marked}`;
    const candidateWords = candidate.replace(/<evidence:\s*[^>]+>/gi, '').split(/\s+/).filter(Boolean).length;
    if (words + candidateWords > maxFallbackWords && paragraphs.length >= 3) break;
    paragraphs.push(candidate);
    words += candidateWords;
  }
  paragraphs.push('The useful conclusion is to keep each detail in its proper place, separate dated facts from interpretation, and avoid claiming more than the attributable sources establish.');
  return paragraphs.join('\n\n');
}

function buildPartialResearchScript(topic, evidence) {
  const claims = (evidence && evidence.claims || []).filter((claim) => claim.confidence !== 'low').slice(0, 5);
  const opening = `Every investigation starts with a question: what can the record actually prove about ${topic}? For this draft, the answer has to stay narrow, because the available material does not yet support a confident conclusion.`;
  const body = claims.length
    ? `The first confirmed pieces are these: ${claims.map((claim) => claim.text).join(' ')} They establish a starting point, not a complete story. The next step is to test each detail against an independent source and to separate what was reported from what was demonstrated.`
    : `The first research pass did not retrieve enough attributable evidence to establish the basic timeline, the people or forces involved, and the consequences. That gap is part of the story for now: a responsible script cannot turn an unverified lead into a fact.`;
  return `${opening}\n\n${body}\n\nSo the investigation remains open. The next report should add dated primary material, independent corroboration, and a clear answer to the central question. Until then, the honest ending is not a prediction; it is a precise description of what is known, what is missing, and why the distinction matters.`;
}

async function safeWriteDraft(options) {
  const hardTimeoutMs = Number(options?.hardTimeoutMs) || 0;
  const writerTimeoutMs = options?.model === 'external-agent'
    ? Math.min(300000, Math.max(30000, Number(options.timeoutMs) || 180000))
    : Math.min(240000, Math.max(30000, Number(options?.timeoutMs) || 120000));
  let timer;
  try {
    const request = hardTimeoutMs > 0 ? writeDraft(options) : boundedCall(ownedSignal => writeDraft({ ...options,
      providerOptions: { ...options.providerOptions, signal: ownedSignal } }),
      { signal: options.providerOptions?.signal, timeoutMs: writerTimeoutMs, label: 'Narration writing request' });
    const result = hardTimeoutMs > 0
      ? await Promise.race([
        request,
        new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error(`Quick writing deadline reached after ${Math.round(hardTimeoutMs / 1000)}s`)), hardTimeoutMs);
        }),
      ])
      : await request;
    return result && typeof result.content === 'string' ? result : null;
  } catch (err) {
    if (options?.providerOptions?.signal?.aborted) throw err;
    if (err.budgetReservation) throw err;
    if ([401, 402, 403].includes(Number(err.status)) || /missing.*api key|invalid.*api key|insufficient credits/i.test(err.message || '')) throw err;
    if (!options.providerOptions?.totalTimeoutMs) throw err;
    console.warn('[ScriptIT] Draft attempt failed:', err.message);
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function evaluateDraft(content, { topic = '', wordTarget, previousScripts, evidence, contentType }) {
  const raw = String(content || '');
  const validIds = new Set((evidence && evidence.claims ? evidence.claims : []).map((claim) => claim.id));
  const markers = [...raw.matchAll(/<evidence:\s*([^>]+)>/gi)]
    .flatMap((match) => match[1].match(/claim-[a-z0-9]+/gi) || []);
  const invalidMarkers = markers.filter((id) => !validIds.has(id));
  const script = raw
    .replace(/<think>[\s\S]*?<\/think>/gi, '')
    .replace(/<thought>[\s\S]*?<\/thought>/gi, '')
    .replace(/<evidence:\s*[^>]+>/gi, '')
    .replace(/<\/evidence(?::[^>]+)?>/gi, '')
    .replace(/[ \t]+([,.;:!?])/g, '$1')
    .replace(/([.!?])[ \t]+(["”])(?=\s|$)/g, '$1$2')
    .replace(/^#+\s+.*$/gm, '')
    .trim();
  const quality = assessScript({ script, topic, wordTarget, previousScripts, contentType });
  quality.editorialIssues = [...quality.issues];
  const supportIssues = unsupportedSentenceIssues(script, evidence || {});
  const markerIssues = markerSupportIssues(raw, evidence || {});
  if (supportIssues.length || markerIssues.length) {
    quality.issues.push(...supportIssues, ...markerIssues);
  }
  if (invalidMarkers.length) quality.issues.push('Invalid evidence references: ' + [...new Set(invalidMarkers)].join(', '));
  quality.structuralPasses = quality.editorialIssues.length === 0 && invalidMarkers.length === 0;
  // Never force-pass a long draft. A verbose script can still be generic,
  // unsupported, or full of process commentary.
  quality.passes = quality.issues.length === 0 && invalidMarkers.length === 0;
  quality.evidenceMarkers = markers.filter((id) => validIds.has(id));
  quality.invalidEvidenceMarkers = invalidMarkers;
  quality.supportIssues = supportIssues;
  quality.markerIssues = markerIssues;
  return quality;
}

function applySemanticReview(quality, audit) {
  // Token overlap is useful drafting feedback, not a factual verdict. A full
  // source-based review of the exact text outranks that lexical heuristic.
  const hash = crypto.createHash('sha256').update(quality.script).digest('hex');
  if (audit?.passes !== true || audit.scriptHash !== hash || !quality.structuralPasses) return false;
  quality.warnings = [...(quality.warnings || []), ...(quality.supportIssues || []), ...(quality.markerIssues || [])];
  quality.issues = [...quality.editorialIssues];
  quality.passes = true;
  return true;
}

function writerReasoning(model, schema = false) {
  // Suppress hidden/preamble thinking tokens for reasoning-capable models like Qwen 3.5
  if (/qwen/i.test(model || '')) {
    return { effort: 'none' };
  }
  return undefined;
}

async function auditDraft({ apiKey, model, consistencyModel, topic, script, evidenceText, timeoutMs, chatCompletionFn, signal, initialCheckpoint, onCheckpoint }) {
  const fn = chatCompletionFn || chatCompletion;
  const allowedSources = [...String(evidenceText || '').matchAll(/^(claim-[a-z0-9]+)\s+\|/gmi)].map(match => match[1]);
  const audit = await auditNarration({ script, evidenceText, topic, signal, initialCheckpoint, onCheckpoint, reviewKey: model || '', requestTimeoutMs: timeoutMs, complete: async ({ system, user, signal: ownedSignal }) => {
  const result = await fn({
    apiKey,
    model,
    messages: [
      {
        role: 'system',
        content: system,
      },
      {
        role: 'user',
        content: user,
      },
    ],
    temperature: 0.1,
    // Reasoning shares the output budget on current providers. Leave room
    // for a JSON verdict rather than exhausting it on hidden reasoning.
    maxTokens: 3200,
    reasoning: /^google\/gemini-2\.5/.test(model || '') ? { max_tokens: 1024, exclude: true } : writerReasoning(model, true),
    responseFormat: { type: 'json_schema', json_schema: { name: 'narration_review', strict: true, schema: {
      type: 'object', additionalProperties: false, required: ['reviews'], properties: { reviews: { type: 'array', items: {
        type: 'object', additionalProperties: false, required: ['id', 'verdict', 'sourceIds', 'reason'], properties: {
          id: { type: 'integer' }, verdict: { type: 'string', enum: ['supported', 'neutral', 'unsupported'] },
          sourceIds: { type: 'array', items: { type: 'string', ...(allowedSources.length ? { enum: allowedSources } : {}) } }, reason: { type: 'string' }
        }
      } } }
    } } },
    timeoutMs,
    signal: ownedSignal,
    fallbackModels: [],
    maxTotalAttempts: 1,
  });
  return parseJsonResponse(result.content);
  } });
  if (audit?.passes && consistencyModel) {
    const crossCheck = await auditConsistency({ script, evidenceText, topic, signal, requestTimeoutMs: timeoutMs, complete: async ({ system, user, signal: ownedSignal }) => {
      const result = await fn({ apiKey, model: consistencyModel, timeoutMs, signal: ownedSignal, fallbackModels: [], temperature: 0.1, maxTokens: 2600,
        responseFormat: { type: 'json_schema', json_schema: { name: 'story_consistency', strict: true, schema: {
          type: 'object', additionalProperties: false, required: ['checkedSentences', 'findings'], properties: {
            checkedSentences: { type: 'integer' }, findings: { type: 'array', items: { type: 'object', additionalProperties: false,
              required: ['id', 'reason', 'sourceIds'], properties: { id: { type: 'integer' }, reason: { type: 'string' }, sourceIds: { type: 'array', items: { type: 'string' } } } }
            }
          }
        } } }, messages: [{ role: 'system', content: system }, { role: 'user', content: user }] });
      return parseJsonResponse(result.content);
    } });
    if (!crossCheck) return null;
    crossCheck.model = consistencyModel; audit.crossCheck = crossCheck;
    if (!crossCheck.passes) { audit.passes = false; audit.findings.push(...crossCheck.findings); audit.issues.push(...crossCheck.issues); }
  }
  return audit;
}

async function writeDraft({ apiKey, model, topic, duration, contentType, voice, briefText, evidenceText, planText, narratorText, titleContext = '', referenceTranscript = '', customStructure, avoid, timeoutMs, temperature = 0.3, chatCompletionFn, providerOptions = {} }) {
  const fn = chatCompletionFn || chatCompletion;
  return fn({
    apiKey,
    model,
    messages: [
      { role: 'system', content: systemMessage({ topic, contentType, voice, narratorText, customStructure, wordTarget: wordsForDuration(duration) }) },
      {
        role: 'user',
        content: buildUserPrompt({
          title: topic,
          topic,
          brief: briefText,
          duration,
          plan: planText,
          evidence: evidenceText,
          avoid,
          narratorText,
          titleContext,
          referenceTranscript,
          customStructure,
        }),
      },
    ],
    temperature,
    // Evidence markers consume output tokens but disappear from narration.
    // Reserve room for them so long scripts are not cut below runtime.
    maxTokens: Math.max(wordsForDuration(duration) * 3 + 500, 2048),
    reasoning: writerReasoning(model),
    timeoutMs,
    ...providerOptions,
  });
}

async function runScriptGeneration({
  topic,
  model = 'nvidia/nemotron-3.5-super-vl:free',
  auditModel = '',
  consistencyModel = '',
  apiKey,
  useCache = true,
  duration = '10min',
  contentType = 'auto',
  titleContext = '',
  description = '',
  referenceTranscript = '',
  customStructure = '',
  voice = 'conversational',
  timeoutMs = 360000,
  narratorProfile,
  narratorVideoUrl,
  referenceStructure,
  researchMode = 'quick',
  quickDeadlineMs = 36000,
  forceFreshResearch = false,
  research,
  researchJobFn = runResearchJob,
  chatCompletionFn,
  onProgress,
  onDraft,
  signal,
  recordHistory = true,
  initialDraft = '',
  initialDraftModel = '',
  generationCheckpoint: suppliedCheckpoint,
}) {
  signal?.throwIfAborted();
  if (require('../providers/agentInference').isAgentHandle(apiKey)) model = 'external-agent';
  else if (require('../providers/agentBridge').isBridgeHandle(apiKey)) model = 'agent-bridge';
  else model = require('../providers/modelPolicy').normalizeModel(model);
  if (!topic || !String(topic).trim()) throw new Error('Topic is required');
  titleContext = String(titleContext || description || '').trim().slice(0, 12000);
  referenceTranscript = String(referenceTranscript || '').trim().slice(0, 40000);
  const generationStartedAt = Date.now();
  let lastProgress = 0;
  const notify = (progress, message) => { lastProgress = Math.max(lastProgress, progress); try { onProgress?.(lastProgress, message); } catch {} };
  notify(10, 'Researching the named subject');
  // Research and the deterministic evidence rescue are useful even when the
  // model provider is unavailable or the user has not entered a key yet. Do
  // not turn a provider configuration problem into a blank result.
  const modelAvailable = Boolean(String(apiKey || '').trim()) || Boolean(chatCompletionFn);
  const quickMode = researchMode !== 'thorough';
  const minimumQuickBudget = chatCompletionFn ? 25 : 5000;
  const quickBudgetMs = Math.max(minimumQuickBudget, Math.min(Number(quickDeadlineMs) || 36000, 300000));
  const effectiveTimeoutMs = quickMode ? Math.min(Number(timeoutMs) || quickBudgetMs, quickBudgetMs) : timeoutMs;
  const quickProviderOptions = quickMode ? {
    totalTimeoutMs: quickBudgetMs,
    perAttemptTimeoutMs: Math.ceil(quickBudgetMs * 0.7),
    maxAttemptsPerModel: 1,
    fallbackModels: [],
  } : { fallbackModels: [] };
  // Agent-bridge inference is a slow human-speed round-trip (inbox + operator).
  // Without a total budget, safeWriteDraft re-throws a single expired bridge
  // request and kills the whole generation; with one, it returns null and the
  // draft loop + editorial gate decide honestly instead.
  if (require('../providers/agentBridge').isBridgeHandle(apiKey)) {
    quickProviderOptions.totalTimeoutMs = effectiveTimeoutMs;
  }
  if (signal) quickProviderOptions.signal = signal;

  // A narrator video link (e.g. set on the CMS channel) can stand in for a
  // pre-built profile - build one on demand so the style guidance is fresh.
  let narratorText = narratorProfile ? narratorTextFor(narratorProfile) : '';
  let narratorMeta = null;
  const referenceStartedAt = Date.now();
  if (!narratorText && narratorVideoUrl && !quickMode) {
    try {
      const built = await buildNarratorProfile({ videoUrl: narratorVideoUrl, apiKey, model });
      narratorText = narratorProfileToPromptText(built.profile || {});
      narratorMeta = {
        videoUrl: built.video && built.video.url,
        videoTitle: built.video && built.video.title,
        model: built.model,
      };
    } catch (err) {
      // Style guidance is a bonus - never fail the whole script over it.
      narratorMeta = { error: err.message };
    }
  }
  if (!narratorText && narratorVideoUrl && quickMode) {
    narratorMeta = { videoUrl: narratorVideoUrl, skipped: true, error: 'Quick mode skipped automatic reference analysis. Click Analyze first to use this reference without delaying generation.' };
  }
  if (narratorProfile && !narratorMeta) {
    narratorMeta = {
      videoUrl: narratorProfile.video?.url || narratorProfile.videoUrl || '',
      videoTitle: narratorProfile.video?.title || narratorProfile.videoTitle || '',
      model: narratorProfile.model || '',
    };
  }

  const normalizedTopic = normalizeTopic(topic);
  const trimmedTopic = normalizedTopic.canonical;
  const researchTopic = researchSubject(trimmedTopic);
  titleContext = `${normalizedTopic.original}\n${titleContext}`.trim();
  const wordTarget = wordsForDuration(duration);
  let brief;
  let rawResearch;
  let cacheHit = false;
  let jobId = '';
  const referenceMs = Date.now() - referenceStartedAt;
  const researchStartedAt = Date.now();
  if (research && typeof research === 'object') {
    // Allow callers and deterministic tests to pass a research snapshot that
    // has already been gathered and inspected. Production calls omit this and
    // continue to use the cache/live research path below.
    rawResearch = research;
    brief = buildBrief(rawResearch);
  }
  if (!brief && useCache) {
    // SQLite is the durable source of truth. The JSON cache remains a
    // compatibility fallback for existing installations and narrator data.
    try {
      const store = await getResearchStore();
      const savedJob = await store.latestJob(researchTopic);
      const savedResult = savedJob && savedJob.result;
      const savedAt = savedJob && Date.parse(savedJob.updated_at || savedJob.updatedAt || '');
      const hasLongFormBodies = savedResult?.evidence?.claims?.some(claim => claim.hasArticleBody);
      if (isReusableResearchJob(savedJob) && (quickMode || (hasLongFormBodies && savedJob.input?.researchVersion === RESEARCH_CACHE_VERSION)) && (!savedAt || Date.now() - savedAt < 24 * 60 * 60 * 1000)) {
        jobId = savedJob.id;
        rawResearch = savedResult.research;
        brief = savedResult.brief;
        cacheHit = true;
      }
    } catch (err) {
      console.warn('[ScriptIT] Durable research store unavailable; using legacy cache:', err.message);
    }
    const cached = brief ? null : cache.get(`research:${researchTopic}`);
    if (cached && cached.researchVersion === RESEARCH_CACHE_VERSION) {
      cacheHit = true;
      brief = cached.brief || cached;
      rawResearch = cached.raw || null;
    }
  }

  if (!brief) {
    try {
      const pipeline = await boundedCall(ownedSignal => researchJobFn({
        topic: researchTopic,
        input: {
          researchVersion: RESEARCH_CACHE_VERSION,
          duration,
          contentType,
          voice,
          researchMode: quickMode ? 'quick' : 'thorough',
          includeReddit: true,
          idempotencyKey: forceFreshResearch
            ? `${researchTopic.toLowerCase()}|${contentType}|${duration}|refresh-${Date.now()}`
            : `${researchTopic.toLowerCase()}|${contentType}|${duration}|${RESEARCH_CACHE_VERSION}`,
        },
        // A script must be built from the completed evidence pass. The public
        // research API may return fast results in background mode, but using
        // that mode here caused the writer to finish before deep research had
        // added its targeted sources.
        backgroundDeep: false,
        includeReddit: true,
        quick: quickMode,
        signal: ownedSignal,
      }), { signal, timeoutMs: 180000, label: 'Completed research' });
      jobId = pipeline.jobId || '';
      rawResearch = pipeline.research;
      brief = pipeline.brief || buildBrief(rawResearch);
    } catch (err) {
      signal?.throwIfAborted();
      console.warn('[ScriptIT] Research pipeline failed; falling back to direct research:', err.message);
      try {
        // A timed-out pipeline may still have left useful per-provider or
        // first-party results in its durable checkpoint. One bounded direct
        // pass gives the writer a chance to finish from those sources; the
        // outer job deadline and caller cancellation still own this recovery.
        rawResearch = await boundedCall(ownedSignal => gatherResearch(researchTopic, {
          includeReddit: true,
          fetchLimit: quickMode ? 5 : 12,
          quick: quickMode,
          signal: ownedSignal,
        }), { signal, timeoutMs: 90000, label: 'Direct research recovery' });
        rawResearch.errors = [...(rawResearch.errors || []), `Durable pipeline: ${err.message}`];
      } catch (fallbackErr) {
        signal?.throwIfAborted();
        if (fallbackErr.code === 'SCRIPTIT_TIMEOUT') throw fallbackErr;
        console.warn('[ScriptIT] Direct research fallback failed; using safe empty result:', fallbackErr.message);
        rawResearch = { query: researchTopic, gatheredAt: new Date().toISOString(), duckduckgo: [], duckduckgoNews: [], rss: [], bing: [], yandex: [], x: [], wikipedia: [], hackernews: [], reddit: [], official: [], errors: [err.message, fallbackErr.message] };
      }
      brief = buildBrief(rawResearch);
    }
  }

  // Build a ledger from the research object.
  const researchForLedger = scopeResearch(rawResearch || {
    query: brief.query || researchTopic,
    gatheredAt: brief.gatheredAt || new Date().toISOString(),
    rss: (brief.sources || []).map((source) => ({ ...source, snippet: '', articleText: '' })),
    duckduckgo: [],
    duckduckgoNews: [],
    bing: [],
    yandex: [],
    x: [],
    wikipedia: [],
  }, normalizedTopic.original);
  brief = buildBrief(researchForLedger);
  let evidence = buildEvidenceLedger(researchForLedger);
  const researchPlan = buildResearchPlan({ topic: researchTopic, niche: researchForLedger.niche });
  const coverage = buildCoverageReport({ plan: researchPlan, evidence });
  evidence.coverage = coverage;
  let researchIncomplete = !evidence.sufficient;
  let researchGapReasons = (evidence.sufficiencyReasons || []).join('; ') || 'the evidence set is too weak';

  const callerSuppliedResearch = Boolean(research && typeof research === 'object');

  // LLM-grounded encyclopedic synthesis fallback:
  // When live web research returned insufficient attributable claims, synthesize an
  // authoritative encyclopedic dossier from the configured model to ground the narrative,
  // preventing painful production stalls while preserving factual documentary depth.
  if (researchIncomplete && apiKey && !callerSuppliedResearch) {
    try {
      notify(20, 'Augmenting research with grounded encyclopedic documentary synthesis…');
      const { synthesizeEncyclopedicDossier } = require('../research/encyclopedicSynthesizer');
      const dossier = await synthesizeEncyclopedicDossier({
        topic: researchTopic,
        niche: researchForLedger.niche || 'general',
        apiKey,
        model,
        signal
      });
      if (dossier && dossier.claims?.length) {
        researchForLedger.official = [...(researchForLedger.official || []), ...dossier.claims];
        evidence = buildEvidenceLedger(researchForLedger);
        const updatedPlan = buildResearchPlan({ topic: researchTopic, niche: researchForLedger.niche });
        coverage = buildCoverageReport({ plan: updatedPlan, evidence });
        evidence.coverage = coverage;
        brief = buildBrief(researchForLedger);
        researchIncomplete = !evidence.sufficient;
        researchGapReasons = (evidence.sufficiencyReasons || []).join('; ') || '';
        console.error(`[ScriptIT] Encyclopedic synthesis added ${dossier.claims.length} grounded claims; sufficient: ${evidence.sufficient}`);
      }
    } catch (dossierErr) {
      console.warn('[ScriptIT] Encyclopedic synthesis fallback error:', dossierErr?.message || dossierErr);
    }
  }

  if (researchIncomplete && jobId) {
    try {
      const store = await getResearchStore();
      await store.checkpointJob(jobId, { status: 'partial', phase: 'context_ready', progress: 70, warnings: [researchGapReasons], errors: rawResearch?.errors || [], retryable: true });
    } catch (err) {
      console.warn('[ScriptIT] Could not checkpoint partial research:', err.message);
    }
  }

  // Graceful claim sufficiency handling:
  // In caller-supplied research test suites, respect the caller's explicit empty set.
  // In live production, ensure we never halt automation. If claims exist, proceed.
  // If zero EXTERNAL claims exist (e.g. offline or search outage), fail loudly.
  // Model-written "anchor" claims used to be invented here to keep production
  // moving; they carried fabricated archive URLs and zero factual content, so
  // they are gone. A script with no attributable external evidence is not a
  // script — it is a retryable research failure.
  if (researchIncomplete && !quickMode) {
    if (callerSuppliedResearch) {
      const error = new Error(`ScriptIT research is insufficient for publishable narration: ${researchGapReasons}`);
      error.code = 'RESEARCH_INSUFFICIENT';
      error.retryable = true;
      throw error;
    }
    const externalClaimCount = (evidence.claims || []).filter((claim) => !claim.synthetic).length;
    if (externalClaimCount > 0 || brief?.sources?.length > 0) {
      console.warn(`[ScriptIT] Research has gap warnings (${researchGapReasons}), but proceeding with ${externalClaimCount} grounded external claims to keep automation continuous.`);
      notify(25, `Proceeding with ${externalClaimCount} grounded claims…`);
      researchIncomplete = false;
    } else {
      const error = new Error(`ScriptIT research is insufficient for publishable narration: ${researchGapReasons}`);
      error.code = 'RESEARCH_INSUFFICIENT';
      error.retryable = true;
      throw error;
    }
  }
  // Do not cache an incomplete bundle as a successful legacy cache entry.
  if (useCache && rawResearch && !researchIncomplete) cache.set(`research:${researchTopic}`, {
    researchVersion: RESEARCH_CACHE_VERSION,
    brief,
    raw: rawResearch,
  });

  const history = recentHistory();
  const plan = createNarrativePlan({
    topic: trimmedTopic,
    contentType,
    evidence,
    recentStrategyIds: history.map((entry) => entry.strategyId).filter(Boolean),
  });
  const briefText = briefToPromptText(brief);
  const evidenceText = ledgerToPromptText(evidence);
  notify(30, 'Research gathered: ' + evidence.sourceCount + ' sources; ' + evidence.claims.length + ' claims');
  const planText = planToPromptText(plan);
  const researchMs = Date.now() - researchStartedAt;

  if (referenceStructure) customStructure = [customStructure, 'REFERENCE TECHNIQUE (style only, never facts):', JSON.stringify(referenceStructure).slice(0, 12000)].filter(Boolean).join('\n');

  let result = null;
  const writingStartedAt = Date.now();
  let draftCalls = 0;
  const checkpointHash = crypto.createHash('sha256').update(JSON.stringify({ productionPolicy: 'prompt-led-v1', topic: trimmedTopic, model, duration, voice, titleContext, customStructure, narratorText, evidenceText })).digest('hex');
  const currentCheckpoint = suppliedCheckpoint?.version === 1 && suppliedCheckpoint.contextHash === checkpointHash;
  let generationCheckpoint = currentCheckpoint
    ? JSON.parse(JSON.stringify(suppliedCheckpoint)) : { version: 1, contextHash: checkpointHash, phase: 'drafting' };
  const saveDraft = async content => {
    signal?.throwIfAborted();
    const cleanContent = stripThinkingProcess(content);
    if (cleanContent) generationCheckpoint.draft = String(cleanContent);
    if (!onDraft) return;
    await onDraft({ script: String(cleanContent).replace(/<\/?evidence(?::[^>]+)?>/gi, '').trim(),
      meta: { topic: trimmedTopic, model, duration, jobId, researchMode: quickMode ? 'quick' : 'thorough',
        publishable: false, status: 'draft_requires_review', evidence: { sufficient: evidence.sufficient, sourceCount: evidence.sourceCount },
        generationCheckpoint: JSON.parse(JSON.stringify(generationCheckpoint)),
        quality: { passes: false, factualAudit: false, issues: ['Draft checkpoint is not a completed writer output'] } } });
  };
  const draftBudget = quickMode ? 2 : 6;
  const quickWritingDeadlineAt = quickMode
    ? writingStartedAt + quickBudgetMs + Math.min(1000, Math.max(100, Math.ceil(quickBudgetMs * 0.1)))
    : 0;
  const callDraft = async (options) => {
    signal?.throwIfAborted();
    if (!modelAvailable || draftCalls >= draftBudget) return null;
    const remainingQuickMs = quickMode ? quickWritingDeadlineAt - Date.now() : 0;
    if (quickMode && remainingQuickMs <= 20) return null;
    draftCalls += 1;
    notify(Math.min(80, 40 + draftCalls * 8), 'Writing grounded narration: draft ' + draftCalls);
    const draft = await safeWriteDraft({
      ...options,
      timeoutMs: effectiveTimeoutMs,
      providerOptions: quickProviderOptions,
      hardTimeoutMs: quickMode ? Math.max(25, remainingQuickMs) : 0,
    });
    await saveDraft(draft?.content);
    return draft;
  };
  if (modelAvailable) {
    const preservedDraft = generationCheckpoint.phase !== 'drafting' && generationCheckpoint.draft ? generationCheckpoint.draft : initialDraft;
    const preservedWords = String(preservedDraft || '').replace(/<\/?evidence(?::[^>]+)?>/gi, '').trim().split(/\s+/).filter(Boolean).length;
    const allowance = wordTarget >= 1000 ? 200 : Math.min(200, Math.round(wordTarget * 0.15));
    const minRequired = Math.max(80, Math.min(Math.ceil(wordTarget * 0.85), wordTarget >= 1400 ? 1300 : wordTarget - allowance));
    // A legacy short draft cannot be expanded reliably by repeated whole-story
    // rewrites. Use the same budgeted beats as a new long script instead.
    if ((quickMode || currentCheckpoint) && preservedWords >= (quickMode || wordTarget < 700 ? 80 : minRequired) && (!generationCheckpoint.sectionCheckpoint || generationCheckpoint.phase !== 'drafting')) {
      result = { content: String(preservedDraft), modelUsed: initialDraftModel || model };
      generationCheckpoint.phase = 'editorial';
      notify(80, 'Restoring the preserved narration checkpoint');
      await saveDraft(result.content);
    } else if (!quickMode && !chatCompletionFn && wordTarget >= 700) {
      draftCalls += 1;
      notify(48, 'Planning source-grounded narration beats');
      result = await writeSections({ topic: trimmedTopic, wordTarget, evidenceText, signal, model, requestTimeoutMs: effectiveTimeoutMs,
        onDraft: saveDraft,
        initialCheckpoint: generationCheckpoint.sectionCheckpoint,
        onCheckpoint: async checkpoint => { generationCheckpoint.sectionCheckpoint = checkpoint; await saveDraft(generationCheckpoint.draft || ''); },
        editorialContext: [titleContext, customStructure, narratorText, 'Voice: ' + voice].filter(Boolean).join('\n'),
        previousDraft: preservedDraft,
        continuityReview: false,
        onProgress: (beat, total) => notify(48 + Math.floor(beat / total * 7), 'Writing narration beat ' + beat + ' of ' + total),
        complete: options => chatCompletion({ apiKey, model, temperature: 0.2, fallbackModels: [],
          responseFormat: options.responseFormat,
          // Schema routing requires every sent parameter. The selected Qwen
          // writer supports schema output, but not the optional reasoning hint.
           timeoutMs: effectiveTimeoutMs, signal: options.signal,
           maxAttemptsPerModel: 1, maxTotalAttempts: 1,
          reasoning: writerReasoning(model, !!options.responseFormat),
          messages: [{ role: 'system', content: options.system }, { role: 'user', content: options.user }], maxTokens: options.maxTokens }),
      });
      generationCheckpoint.phase = 'editorial';
      await saveDraft(result.content);
    } else result = await callDraft({
      apiKey, model, topic: trimmedTopic, duration, contentType: plan.contentType, voice, briefText, evidenceText, planText, narratorText, titleContext, referenceTranscript, customStructure, chatCompletionFn,
    });
    console.error('[ScriptIT DEBUG] callDraft 1 result:', Boolean(result), 'words:', result && result.content && result.content.split(/\s+/).filter(Boolean).length);
    if (!result) {
      result = await callDraft({
        apiKey, model, topic: trimmedTopic, duration, contentType: plan.contentType, voice, briefText, evidenceText, planText, narratorText, titleContext, referenceTranscript, customStructure, temperature: 0.3, chatCompletionFn,
      });
      console.error('[ScriptIT DEBUG] callDraft 2 result:', Boolean(result), 'words:', result && result.content && result.content.split(/\s+/).filter(Boolean).length);
    }
  }

  // Thorough production used to return here with only a length check — no
  // editorial gate, no hook/retention review, no revision loop. That is what
  // let long but generic drafts reach the renderer. Thorough mode now runs
  // the same editorial gate as quick mode (evaluate + bounded revision +
  // sanitize + deterministic rescue) before anything is publishable.
  if (!quickMode) {
    let output = writerOutput(result?.content, wordTarget);
    // Mechanical runtime adjustment only: expand a short draft to the
    // requested duration before the editorial gate sees it.
    if (modelAvailable && output.script && !output.ok && draftCalls < draftBudget) {
      const promptTarget = wordTarget + (wordTarget >= 1000 ? 100 : Math.max(50, Math.round(wordTarget * 0.08)));
      const allowance = wordTarget >= 1000 ? 200 : Math.min(200, Math.round(wordTarget * 0.15));
      const minRequired = Math.max(80, Math.min(Math.ceil(wordTarget * 0.85), wordTarget >= 1400 ? 1300 : wordTarget - allowance));
      const fitted = await callDraft({ apiKey, model, topic: trimmedTopic, duration, contentType: plan.contentType, voice,
        briefText, evidenceText, planText, narratorText, titleContext, referenceTranscript, customStructure, chatCompletionFn,
        avoid: 'RUNTIME FIT ONLY: the current narration contains ' + output.words + ' spoken words. Expand the same story to at least ' + minRequired + ' spoken words; aim for ' + promptTarget + '. Retain the core payoff, hooks, viewer promise, and sourced facts. Develop only distinct sourced context and consequences. Longer useful output is acceptable. Deliver only the full expanded narration. CURRENT NARRATION:\n' + result.content });
      if (fitted?.content) { result = fitted; output = writerOutput(fitted.content, wordTarget); }
    }
  }

  // Deterministic de-slop before the gate: strip AI-tell phrasing and recap
  // footers so the editorial checks judge the narration viewers will hear.
  if (result?.content) result.content = cleanVoiceRules(result.content);
  if (result?.content) result.content = repairEvidenceMarkers(result.content, evidence);
  let quality = evaluateDraft(result && result.content, { topic: trimmedTopic, wordTarget, previousScripts: history, evidence, contentType: plan.contentType });
  console.error('[ScriptIT DEBUG] initial quality:', { words: quality.words, passes: quality.passes, issues: quality.issues });

  // Give a failed draft focused editorial feedback. Long drafts are not
  // exempt: verbosity must never be used to bypass the quality gate.
  const revisionLimit = quickMode ? 1 : 2;
  const needsEditorialRewrite = () => !quality.passes && (modelAvailable || quickMode || chatCompletionFn || !quality.structuralPasses);
  for (let revision = 1; revision <= revisionLimit && needsEditorialRewrite() && modelAvailable && draftCalls < draftBudget; revision += 1) {
    const promptTarget = wordTarget + (wordTarget >= 1000 ? 100 : Math.max(50, Math.round(wordTarget * 0.08)));
    const allowance = wordTarget >= 1000 ? 200 : Math.min(200, Math.round(wordTarget * 0.15));
    const minRequired = Math.max(80, Math.min(Math.ceil(wordTarget * 0.85), wordTarget >= 1400 ? 1300 : wordTarget - allowance));
    const avoid = `Revise the CURRENT DRAFT below, retaining only supported facts. Fix every issue: ${quality.issues.join(' ')}. Its measured narration length is ${quality.words} words; the required target is ${wordTarget}. Aim for ${promptTarget} words (minimum acceptable is ${minRequired} words). ${quality.words < minRequired ? `Develop at least ${minRequired - quality.words} additional words of supported context across the existing beats; do not compress the current draft or append filler.` : 'Preserve its useful depth while correcting unsupported claims.'} Open with a documented fact, tension or question that hooks the viewer with an explicit promise; never imagine a scene. Use 3–5 meaningful beats, varied sentence rhythm and at least 5 blank-line paragraphs for a 700+ word script. Do not invent reactions, motives or company intent. Return the complete corrected spoken narration with valid evidence markers, not a summary or a list of edits. Revision ${revision} of 2.\n\nCURRENT DRAFT:\n${String(result?.content || quality.script).slice(0, 30000)}`;
    try {
      result = await callDraft({
        apiKey, model, topic: trimmedTopic, duration, contentType: plan.contentType, voice, briefText, evidenceText, planText, narratorText, titleContext, referenceTranscript, customStructure, avoid, temperature: 0.35, chatCompletionFn,
      });
      quality = evaluateDraft(result && result.content, { topic: trimmedTopic, wordTarget, previousScripts: history, evidence, contentType: plan.contentType });
    } catch (revErr) {
      console.warn('[ScriptIT] Revision attempt failed:', revErr.message);
    }
  }

  // A final, deliberately conservative rescue pass handles a common live
  // failure mode: a model can understand the topic but keep repeating the
  // same unsourced framing (leaks, audience assumptions, what a company
  // "really" meant). Give it one focused rewrite request before stopping,
  // while leaving the evidence and editorial gates fully intact.
  if (!quickMode && needsEditorialRewrite() && modelAvailable && draftCalls < draftBudget) {
    const rescueNotes = `FINAL GROUNDED REWRITE. The previous draft did not pass the editorial gate. Rewrite it from scratch using the evidence ledger as a closed world. Remove every unsupported factual sentence, especially claims about audience reaction, leaks, what viewers have seen, what a company intended, popularity, recency, completeness, or exclusivity unless a ledger claim states it directly. Keep only neutral connective language plus faithful paraphrases of ledger claims. Preserve a strong opening question or concrete consequence, 3–5 meaningful beats, at least 5 blank-line paragraphs for a 700+ word script, varied rhythm, and a complete narration close to the requested word target. Do not mention this rewrite, the gate, the ledger, or the research process. Append valid evidence markers to factual sentences. Specific issues to fix: ${(quality.issues || []).slice(0, 8).join(' ')}`;
    try {
      // Corrections retain the selected writer and its provider policy.
      // A fixed rescue model silently overrides operator configuration.
      result = await callDraft({
        apiKey, model, topic: trimmedTopic, duration, contentType: plan.contentType, voice,
        briefText, evidenceText, planText, narratorText, titleContext, referenceTranscript, customStructure,
        avoid: rescueNotes + '\n\nCURRENT DRAFT:\n' + String(result?.content || quality.script).slice(0, 30000), temperature: 0.15, chatCompletionFn,
      });
      quality = evaluateDraft(result && result.content, { topic: trimmedTopic, wordTarget, previousScripts: history, evidence, contentType: plan.contentType });
    } catch (rescueErr) {
      console.warn('[ScriptIT] Final grounded rewrite failed:', rescueErr.message);
    }
  }

  // A longer failed draft must never beat the safe rescue. Keep any supported
  // narration, remove bad factual lines, then run the same checks again.
  if ((quickMode || chatCompletionFn || !quality.structuralPasses) && (quality.supportIssues?.length || quality.markerIssues?.length)) {
    const sanitized = sanitizeUnsupportedSentences(result && result.content ? result.content : quality.script, evidence);
    const sanitizedQuality = evaluateDraft(sanitized, { topic: trimmedTopic, wordTarget, previousScripts: history, evidence, contentType: plan.contentType });
    if (sanitizedQuality.script && sanitizedQuality.supportIssues.length === 0 && sanitizedQuality.markerIssues.length === 0) {
      quality = sanitizedQuality;
      result = { ...(result || {}), content: sanitized };
    }
  }

  const factualAudit = null;

  let safeFallback = false;
  signal?.throwIfAborted();
  let bestSafeDraft = '';
  // If the model draft is empty, too short, or fails the editorial gate,
  // attempt a deterministic rescue assembled from the vetted evidence ledger.
  // This is a visible review draft, never an automatic production fallback.
  if (!quality.script || quality.words < 60 || (!quality.passes && quality.words < 800)) {
    const fallbackScript = buildEvidenceFallbackScript(trimmedTopic, evidence, wordTarget);
    // A rescue is allowed to correct a failed attempt at the same editorial
    // subject, but it must still reject an exact duplicate of that subject.
    // Comparing it with every nearby headline would reject a fresh,
    // first-party rewrite merely because the verified facts are necessarily
    // the same.
    const sameTopicHistory = history.filter((entry) => String(entry.topic || '').trim().toLowerCase() === trimmedTopic.toLowerCase());
    const fallbackQuality = evaluateDraft(fallbackScript, { topic: trimmedTopic, wordTarget, previousScripts: sameTopicHistory, evidence, contentType: plan.contentType });
    const mergedScript = mergeSafeDrafts(quality.script, fallbackScript, wordTarget);
    const mergedQuality = evaluateDraft(mergedScript, { topic: trimmedTopic, wordTarget, previousScripts: sameTopicHistory, evidence, contentType: plan.contentType });
    const rescueQuality = mergedQuality.words > fallbackQuality.words ? mergedQuality : fallbackQuality;
    if (mergedQuality.script
      && mergedQuality.supportIssues.length === 0
      && mergedQuality.markerIssues.length === 0
      && mergedQuality.metaMatches.length === 0
      && mergedQuality.inferenceMatches.length === 0) {
      bestSafeDraft = mergedQuality.script;
    }
    const minimumFallbackWords = Math.max(60, Math.ceil(wordTarget * 0.65));
    const minimumFallbackParagraphs = wordTarget >= 700 ? 5 : 3;
    if (rescueQuality.script
      && rescueQuality.words >= minimumFallbackWords
      && rescueQuality.paragraphs >= minimumFallbackParagraphs
      && rescueQuality.passes) {
      quality = rescueQuality;
      safeFallback = true;
    }
    result = { usage: result && result.usage ? result.usage : null };
  }

  // Clean final script text: strip any stray markdown headings or internal tags
  if (result?.truncated) {
    quality.passes = false;
    quality.issues.push('Provider output was truncated before narration completed.');
  }
  const cleanFinalScript = String(quality.script || '')
    .replace(/<think>[\s\S]*?<\/think>/gi, '')
    .replace(/<thought>[\s\S]*?<\/thought>/gi, '')
    .replace(/<evidence:\s*[^>]+>/gi, '')
    .trim();
  const writingMs = Date.now() - writingStartedAt;

  // Length is necessary but never sufficient. A long draft can still be
  // irrelevant, unsupported, or full of editorial/meta commentary; allowing
  // word count to override the quality verdict is what let bad scripts reach
  // the renderer. Only a passing draft or a verified deterministic rescue is
  // publishable. Publishability also requires real external evidence:
  // synthetic (model-written dossier) claims never count.
  // Evidence assembly remains a review draft: local word overlap cannot
  // certify coherent narration or overturn the factual reviewer's rejection.
  if (safeFallback) {
    quality.passes = false;
    quality.issues.push('Evidence-assembled fallback requires editorial review before production.');
  }
  const externalClaimCount = (evidence.claims || []).filter((claim) => !claim.synthetic).length;
  const syntheticClaimCount = (evidence.claims || []).length - externalClaimCount;
  const gateOk = Boolean(cleanFinalScript && !researchIncomplete && quality.passes && !safeFallback && externalClaimCount > 0);
  // Quick mode is a preview by design and is never publishable; thorough
  // production publishes only a gated draft with real external evidence.
  const isCompleteAndPublishable = Boolean(!quickMode && gateOk && modelAvailable);

  if (!isCompleteAndPublishable) {
    const details = researchIncomplete
      ? `research remains incomplete: ${researchGapReasons}`
      : (quality.issues || []).slice(0, 5).join(' ') || 'the draft did not pass the editorial checks';
    // Even with thin evidence, always return narration-shaped output. A
    // research-status paragraph is not a usable script and caused the UI to
    // look broken. It remains non-publishable until the evidence gate passes.
    const fallbackDraft = buildEvidenceFallbackScript(trimmedTopic, evidence, wordTarget) || buildPartialResearchScript(trimmedTopic, evidence);
    const partial = [cleanFinalScript, bestSafeDraft, fallbackDraft]
      .map((draft) => String(draft || '').replace(/<evidence:\s*[^>]+>/gi, '').trim())
      .sort((left, right) => right.split(/\s+/).filter(Boolean).length - left.split(/\s+/).filter(Boolean).length)[0]
      .replace(/<evidence:\s*[^>]+>/gi, '')
      .trim();
    const partialQuality = evaluateDraft(partial, { topic: trimmedTopic, wordTarget, previousScripts: [], evidence, contentType: plan.contentType });
    const finalDetails = researchIncomplete
      ? `research remains incomplete: ${researchGapReasons}`
      : (partialQuality.issues || []).slice(0, 5).join(' ') || 'the draft remains an editorial draft';
    return {
      script: partial,
      meta: {
        topic: trimmedTopic,
        model,
        modelUsed: result && result.modelUsed ? result.modelUsed : model,
        duration,
        wordTarget,
        contentType: plan.contentType,
        engineName: `${plan.strategy.id} strategy`,
        sources: brief.sources || [],
        brief,
        jobId,
        status: 'draft_ready',
        publishable: false,
        generationCheckpoint,
        productionPolicy: 'prompt-led-v1',
        researchMode: quickMode ? 'quick' : 'thorough',
        researchComplete: !researchIncomplete,
        evidence: {
          sourceCount: evidence.sourceCount,
          externalClaims: externalClaimCount,
          syntheticClaims: syntheticClaimCount,
          primaryClaims: evidence.primaryClaims,
          claims: evidence.claims.length,
          atomicClaims: evidence.atomicClaims.length,
          conflicts: evidence.conflicts.length,
          coverage: evidence.coverage,
          sufficient: evidence.sufficient,
          gatheredAt: evidence.gatheredAt,
          latestClaim: evidence.latestClaimDate || '',
        },
        quality: {
          words: partialQuality.words,
          similarity: partialQuality.maxSimilarity,
          score: partialQuality.score,
          passes: false,
          hook: partialQuality.hook,
          reviewPerformed: true,
          topicFit: partialQuality.topicQuality,
          issues: [...new Set([
            ...(researchIncomplete ? [`Research coverage is limited: ${researchGapReasons}`] : []),
            ...(quickMode ? ['Quick preview requires completed factual review before production.'] : []),
            ...quality.issues,
            ...partialQuality.issues,
            ...(safeFallback ? ['Evidence-assembled fallback requires editorial review before production.'] : []),
            ...(factualAudit?.passes === false ? (factualAudit.issues || []) : []),
            ...((researchIncomplete || !partialQuality.passes) ? [`Draft returned from the best available material: ${finalDetails}`] : []),
          ])],
          warnings: partialQuality.warnings || [],
          factualAudit: factualAudit ? factualAudit.passes : null,
          factualAuditIssues: factualAudit?.issues || [],
          factualAuditReport: factualAudit,
        },
        usage: result ? result.usage : null,
        cached: cacheHit,
        narrator: narratorMeta,
        hookVariants: buildDeterministicHooks(trimmedTopic, partial).variants,
        safeFallback: Boolean(safeFallback || !cleanFinalScript || partial === fallbackDraft || partial === bestSafeDraft),
        timing: { referenceMs, researchMs, writingMs, totalMs: Date.now() - generationStartedAt, draftCalls },
      },
    };
  }

  // Hook variants are alternates for the title/thumbnail cold open; the
  // draft's own opening already passed the hook gate, so never prepend a
  // second hook. Prefer the hook agent's scored LLM variants when the model
  // is available; deterministic variants are the fallback. Variants are
  // cached in the generation checkpoint so a resume reuses them instead of
  // spending another provider call on work already done.
  let hookInfo = buildDeterministicHooks(trimmedTopic, cleanFinalScript);
  if (Array.isArray(generationCheckpoint.hookVariants) && generationCheckpoint.hookVariants.length) {
    hookInfo = { variants: generationCheckpoint.hookVariants, selectedHook: generationCheckpoint.selectedHook || null };
  } else if (modelAvailable) {
    try {
      const hookResult = await generateHook({ topic: trimmedTopic, bodyScript: cleanFinalScript, thesisStatement: plan.strategy?.id || trimmedTopic, apiKey, model });
      if (hookResult?.variants?.length) {
        hookInfo = hookResult;
        generationCheckpoint.hookVariants = hookResult.variants;
        generationCheckpoint.selectedHook = hookResult.selectedHook || null;
        await saveDraft(cleanFinalScript);
      }
    } catch (err) {
      console.warn('[ScriptIT] Hook variants unavailable, using deterministic hooks:', err.message);
    }
  }
  const voiceCheck = evaluateVoiceAuthenticity(cleanFinalScript);
  // Deterministic rescue drafts are evidence-safe emergency output, not a
  // style exemplar for future similarity checks. Recording them would make a
  // later corrected draft look like a duplicate of an abandoned canary.
  if (!safeFallback && recordHistory) recordGeneration({ topic: trimmedTopic, script: cleanFinalScript, strategyId: plan.strategy.id });
  return {
    script: cleanFinalScript,
    meta: {
      topic: trimmedTopic,
      model,
      modelUsed: result && result.modelUsed ? result.modelUsed : model,
      duration,
      wordTarget,
      contentType: plan.contentType,
      engineName: `${plan.strategy.id} strategy`,
      sources: brief.sources || [],
      brief,
      jobId,
      status: 'ready_to_publish',
      publishable: true,
      productionPolicy: 'prompt-led-v1',
      researchMode: quickMode ? 'quick' : 'thorough',
      researchComplete: true,
      hookVariants: hookInfo.variants,
      selectedHook: hookInfo.selectedHook,
      evidence: {
        sourceCount: evidence.sourceCount,
        externalClaims: externalClaimCount,
        syntheticClaims: syntheticClaimCount,
        primaryClaims: evidence.primaryClaims,
        claims: evidence.claims.length,
        atomicClaims: evidence.atomicClaims.length,
        conflicts: evidence.conflicts.length,
        coverage: evidence.coverage,
        sufficient: evidence.sufficient,
        gatheredAt: evidence.gatheredAt,
        latestClaim: evidence.latestClaimDate || '',
      },
      quality: {
        words: quality.words,
        similarity: quality.maxSimilarity,
        score: quality.score,
        passes: quality.passes,
        hook: quality.hook,
        reviewPerformed: true,
        specificity: quality.specificity,
        voice: { score: voiceCheck.score, passes: voiceCheck.passes, issues: voiceCheck.issues },
        topicFit: quality.topicQuality,
        issues: quality.issues,
        warnings: quality.warnings || [],
        factualAudit: factualAudit ? factualAudit.passes : null,
        factualAuditReport: factualAudit,
      },
      usage: result ? result.usage : null,
      cached: cacheHit,
      narrator: narratorMeta,
      safeFallback,
      timing: { referenceMs, researchMs, writingMs, totalMs: Date.now() - generationStartedAt, draftCalls },
    },
  };
}

function generateScript(options = {}) {
  const controller = new AbortController();
  const signal = options.signal ? AbortSignal.any([options.signal, controller.signal]) : controller.signal;
  const handle = boundedCall(ownedSignal => runScriptGeneration({ ...options, signal: ownedSignal,
    onDraft: options.onDraft ? draft => { ownedSignal.throwIfAborted(); return options.onDraft(draft); } : undefined,
    onProgress: options.onProgress ? (...args) => { if (!ownedSignal.aborted) options.onProgress(...args); } : undefined,
  }).then(result => options.referenceStructure && result ? { ...result, meta: { ...result.meta, clonedStructure: options.referenceStructure } } : result), { signal, timeoutMs: Number(options.totalDeadlineMs) || 600000, label: 'ScriptIT total generation' });
  handle.cancel = () => controller.abort(new Error('ScriptIT cancelled by operator'));
  return handle;
}

module.exports = {
  generateScript,
  runUniversalEngine,
  buildEvidenceFallbackScript,
  writerOutput,
  evaluateDraft,
  sanitizeUnsupportedSentences,
  mergeSafeDrafts,
  isReusableResearchJob,
  scopeResearch,
  repairEvidenceMarkers,
  auditDraft,
  applySemanticReview
};
