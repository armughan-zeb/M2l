'use strict';

// Editorial guardrails catch the most common machine-written failure modes
// before a script reaches the video renderer. They complement, rather than
// replace, a human editorial pass.
const BANNED_PATTERNS = [
  /\bimagine a world where\b/gi,
  /\bbut here is the twist\b/gi,
  /\blittle did (?:they|he|she) know\b/gi,
  /\bthis changes everything\b/gi,
  /\bit is not just .{1,60}, it is\b/gi,
  /\bin today's (?:fast[- ]moving|digital|ever[- ]changing) world\b/gi,
  /\blet's (?:dive|unpack|explore)\b/gi,
  /\bas we delve into\b/gi,
  /\bit is important to note\b/gi,
  /\bone of the most (?:important|fascinating|remarkable)\b/gi,
  /\btestament to (?:their|his|her|its)\b/gi,
  /\bunwavering (?:dedication|commitment)\b/gi,
  /\bunparalleled (?:skill|success|ability|impact)\b/gi,
  /\bin conclusion\b/gi,
];

const META_PATTERNS = [
  /\bas an (?:ai|artificial intelligence|language model)\b/gi,
  /\bi (?:can't|cannot|am unable to) (?:write|provide|confirm|verify|answer)/gi,
  /\b(?:the )?(?:research|evidence) (?:summary|ledger|provided)\b/gi,
  /\bbased on (?:the )?(?:research|evidence|sources)\b/gi,
  /\bthere (?:is|are) no (?:official|confirmed) (?:information|announcement|details)\b/gi,
  /\bhere(?:'s| is) (?:a|the) (?:summary|analysis|lecture|script)\b/gi,
  /\b(?:this|the following) (?:script|breakdown) (?:will|is going to)\b/gi,
  /\b(?:target|estimated) length\s*:/gi,
];

const INFERENCE_PATTERNS = [
  /\bbefore a single (?:copy|mission|player)\b/gi,
  /\bno delays\b/gi,
  /\b(?:the )?machine is moving\b/gi,
  /\bfirst (?:proper|actual|real) (?:look|gameplay|footage)\b/gi,
  /\b(?:the )?(?:launch )?date (?:is|was) (?:locked in|set|guaranteed)\b/gi,
  /\b(?:the )?numbers (?:suggest|prove|show)\b/gi,
  /\bsignals? confidence\b/gi,
  /\bdriven almost entirely by\b/gi,
  /\bfinished product running in[- ]engine\b/gi,
  /\b(?:betting everything|they're not nervous|they are not nervous)\b/gi,
  /\b(?:this time|finally) (?:it is|it's) actually happening\b/gi,
  /\b(?:will|would|could) likely follow\b/gi,
  /\b(?:biggest|most anticipated) (?:game|product) (?:in|of) (?:the )?history\b/gi,
  /\b(?:launch|release) (?:window|date) is (?:firm|locked|set)\b/gi,
  /\bconfirm every hope\b/gi,
  /\bdirect financial incentive\b/gi,
  /\brockstar knows (?:this|that)\b/gi,
];

const TOPIC_STOPWORDS = new Set([
  'a', 'an', 'and', 'are', 'about', 'after', 'all', 'be', 'been', 'before',
  'between', 'by', 'complete', 'could', 'details', 'did', 'does', 'for',
  'from', 'how', 'inside', 'into', 'is', 'it', 'made', 'matters', 'more',
  'most', 'of', 'on', 'or', 'over', 'review', 'secret', 'story', 'that',
  'the', 'their', 'this', 'think', 'through', 'to', 'true', 'untold', 'was',
  'what', 'when', 'where', 'which', 'why', 'with', 'you', 'your',
]);

const DOMAIN_RULES = [
  {
    name: 'royal-history',
    topic: /\b(royal|household|palace|monarch|king|queen|prince|princess|crown|court|ceremonial|routine)\b/i,
    anchors: /\b(royal|household|palace|monarch|king|queen|prince|princess|crown|court|staff|ceremony|protocol|routine|schedule|engagement|servant|correspondence)\b/gi,
    drift: /\b(engine|mechanical|prototype|blueprint|workshop|machinery|component|testing shed|formula|technical specification|chassis|horsepower)\b/gi,
  },
  {
    name: 'automotive',
    topic: /\b(car|cars|automotive|engine|jaguar|ferrari|porsche|ford|motor|racing|v8|chassis|vehicle)\b/i,
    anchors: /\b(car|automotive|engine|jaguar|ferrari|porsche|ford|motor|racing|chassis|vehicle|road|driver|horsepower|design)\b/gi,
    drift: /\b(palace|monarch|household|ceremony|court|royal staff|coronation)\b/gi,
  },
  {
    name: 'railway',
    topic: /\b(train|rail|railway|locomotive|scotsman|steam|station|track)\b/i,
    anchors: /\b(train|rail|railway|locomotive|scotsman|steam|station|track|route|driver|railroad|engine)\b/gi,
    drift: /\b(palace|monarch|household|ceremony|royal staff|coronation)\b/gi,
  },
  {
    name: 'death-afterlife',
    topic: /\b(death|dying|dead|afterlife|near[- ]death|mortality|grief|funeral)\b/i,
    anchors: /\b(death|dying|dead|afterlife|near[- ]death|mortality|grief|funeral|consciousness|survivor|bereaved)\b/gi,
    drift: /\b(engine|chassis|horsepower|palace|monarch|railway|locomotive)\b/gi,
  },
];

function normalizeForComparison(text) {
  return String(text || '').toLowerCase().replace(/[^a-z0-9\s]/g, ' ').replace(/\s+/g, ' ').trim();
}

function topicAnchors(topic) {
  return [...new Set(normalizeForComparison(topic)
    .split(' ')
    .map((word) => word.replace(/(ing|ed|es|s)$/i, ''))
    .filter((word) => word.length >= 4 && !TOPIC_STOPWORDS.has(word)))];
}

function topicFit(script, topic) {
  const normalizedScript = normalizeForComparison(script);
  const anchors = topicAnchors(topic);
  if (!anchors.length) return { ok: true, anchors: [], hits: [], openingHits: [], coverage: 1, domain: null, domainHits: [], driftHits: [], issues: [] };
  const hits = anchors.filter((anchor) => new RegExp(`\\b${anchor}\\w*\\b`, 'i').test(normalizedScript));
  const firstSection = normalizedScript.slice(0, 1400);
  const openingHits = anchors.filter((anchor) => new RegExp(`\\b${anchor}\\w*\\b`, 'i').test(firstSection));
  const coverage = hits.length / anchors.length;
  const domain = DOMAIN_RULES.find((rule) => rule.topic.test(String(topic || '')));
  const domainHits = domain ? (normalizedScript.match(domain.anchors) || []) : [];
  const driftHits = domain ? (normalizedScript.match(domain.drift) || []) : [];
  const issues = [];
  if (anchors.length >= 3 && coverage < 0.5) issues.push(`The narration does not stay focused on the requested topic (topic coverage ${Math.round(coverage * 100)}%).`);
  if (anchors.length >= 2 && openingHits.length === 0) issues.push('The opening does not establish the requested subject.');
  if (domain && driftHits.length >= 3 && driftHits.length > domainHits.length) issues.push(`The draft drifts into ${domain.name} mismatch language instead of explaining the requested subject.`);
  return {
    ok: issues.length === 0,
    anchors,
    hits,
    openingHits,
    coverage,
    domain: domain ? domain.name : null,
    domainHits,
    driftHits,
    issues,
  };
}

function shingles(text, size = 4) {
  const words = normalizeForComparison(text).split(' ').filter(Boolean);
  const output = new Set();
  for (let index = 0; index <= words.length - size; index += 1) output.add(words.slice(index, index + size).join(' '));
  return output;
}

function similarity(a, b) {
  const left = shingles(a);
  const right = shingles(b);
  if (!left.size || !right.size) return 0;
  let matches = 0;
  for (const item of left) if (right.has(item)) matches += 1;
  return matches / Math.min(left.size, right.size);
}

function sentencesOf(text) {
  return String(text || '').match(/[^.!?]+(?:[.!?](?!\d)|$)/g)?.map((s) => s.trim()).filter(Boolean) || [];
}

function paragraphsOf(text) {
  return String(text || '').split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
}

function openingHasHook(text) {
  // Spoken hooks may put each short sentence on its own paragraph. Inspect
  // the same first three sentences regardless of those pacing breaks.
  const sentences = sentencesOf(text).slice(0, 3);
  if (/^(?:today|in this video|in this episode|welcome|as we|let's|we're going to)\b/i.test(sentences[0] || '')) return false;
  let opening = '';
  for (const sentence of sentences) {
    opening = [opening, sentence].filter(Boolean).join(' ');
    const words = opening.split(/\s+/).filter(Boolean);
    if (words.length > 90) break;
    if (words.length < 8) continue;
    const tension = /\?|\bbut\b|\byet\b|\buntil\b|\bwhen\b|\bafter\b|\bbefore\b|\bonce\b|\binstead\b|\bno one\b|\bonly\b|\bpromise\b|\breveal\b|\bsecret\b|\btruth\b/i.test(opening);
    const concrete = /\b\d{4}\b|\b\d+(?:\.\d+)?\b|\b[A-Z][a-z]{2,}\b/.test(opening);
    if (tension || concrete) return true;
  }
  return false;
}

function repeatedStarts(text) {
  const starts = sentencesOf(text)
    .map((sentence) => sentence.toLowerCase().replace(/^[^a-z]+/, '').split(/\s+/).slice(0, 3).join(' '))
    .filter((start) => start.length > 5);
  const counts = new Map();
  starts.forEach((start) => counts.set(start, (counts.get(start) || 0) + 1));
  return [...counts.entries()].filter(([, count]) => count >= 3).map(([start]) => start);
}

function formatPlainNarration(text) {
  return String(text || '')
    .replace(/```[\s\S]*?```/g, '')
    .replace(/<think>[\s\S]*?<\/think>/gi, '')
    .replace(/<thought>[\s\S]*?<\/thought>/gi, '')
    // Keep newline characters intact so paragraph breaks survive cleanup and
    // the spoken-pacing gate can actually see the writer's beat structure.
    .replace(/^[ \t>*#\-–—]+/gm, '')
    .replace(/^(?:script|title|hook|intro|body|climax|outro|cta)\s*:\s*/gmi, '')
    .replace(/^(?:here(?:'s| is)|below is)\s+(?:a|the)\s+[^\n]+\n+/i, '')
    .replace(/[\[\]{}*_`~|]/g, '')
    .replace(/[\u{1F300}-\u{1FAFF}]/gu, '')
    .replace(/[“”]/g, '"')
    .replace(/[‘’]/g, "'")
    .replace(/[–—]/g, ', ')
    .replace(/[^\p{L}\p{N}\s.,!?;:'"%£€$-]/gu, '')
    .replace(/\n{3,}/g, '\n\n')
    .replace(/[ \t]{2,}/g, ' ')
    .trim();
}

function assessScript({ script, topic = '', wordTarget, previousScripts = [], contentType = 'general' }) {
  const cleaned = formatPlainNarration(script);
  const words = cleaned ? cleaned.split(/\s+/).length : 0;
  const paragraphs = paragraphsOf(cleaned);
  const sentences = sentencesOf(cleaned);
  const genericMatches = BANNED_PATTERNS.flatMap((pattern) => cleaned.match(pattern) || []);
  const metaMatches = META_PATTERNS.flatMap((pattern) => cleaned.match(pattern) || []);
  const inferenceMatches = INFERENCE_PATTERNS.flatMap((pattern) => cleaned.match(pattern) || []);
  const maxSimilarity = previousScripts.reduce((max, previous) => Math.max(max, similarity(cleaned, previous.script || previous)), 0);
  const lengthRatio = wordTarget ? words / wordTarget : 1;
  const questionCount = (cleaned.match(/\?/g) || []).length;
  const repeatedSentenceStarts = repeatedStarts(cleaned);
  const hook = openingHasHook(cleaned);
  const topicQuality = topicFit(cleaned, topic);
  const issues = [];
  const warnings = [];

  if (!cleaned) issues.push('The model returned no narration.');
  if (genericMatches.length > 1) issues.push('The draft uses repeated or generic AI phrasing.');
  if (metaMatches.length) issues.push('The draft contains process commentary instead of narration.');
  if (inferenceMatches.length) issues.push('The draft makes unsupported inferences instead of staying with sourced facts.');
  if (maxSimilarity > 0.22) issues.push('The draft is too similar to a recent script.');
  const allowance = wordTarget >= 1000 ? 200 : Math.min(200, Math.round(wordTarget * 0.15));
  const minAllowedWords = wordTarget ? Math.max(10, Math.min(Math.ceil(wordTarget * 0.85), wordTarget >= 1400 ? 1300 : wordTarget - allowance)) : 0;
  if (wordTarget && (words < minAllowedWords || lengthRatio > 1.35)) issues.push('The draft misses the requested length.');
  if (words >= 180 && paragraphs.length < 3) issues.push('The draft has no usable spoken pacing breaks.');
  if (words >= 180 && !hook) issues.push('The opening does not create a specific question, tension, or concrete scene.');
  if (questionCount > Math.max(4, Math.ceil(words / 180))) issues.push('The draft overuses rhetorical questions.');
  issues.push(...topicQuality.issues);
  if (repeatedSentenceStarts.length) warnings.push('Several sentences start the same way, creating a mechanical rhythm.');
  if (contentType === 'listicle' && words >= 180 && !/\b(?:first|second|third|top|number)\b/i.test(cleaned)) {
    issues.push('The listicle draft does not clearly move through its promised entries.');
  }

  const wordDeficit = wordTarget && words < wordTarget ? Math.max(0, wordTarget - words) : 0;
  const wordPenalty = wordDeficit > 0 ? Math.min(25, Math.round((wordDeficit / (wordTarget || 1500)) * 50)) : 0;
  const penalties = (genericMatches.length * 8) + (metaMatches.length * 20) + (inferenceMatches.length * 22)
    + (hook ? 0 : 12) + (repeatedSentenceStarts.length * 6) + wordPenalty;
  const score = Math.max(0, Math.min(100, 100 - penalties));
  return {
    script: cleaned,
    words,
    paragraphs: paragraphs.length,
    sentences: sentences.length,
    score,
    hook,
    genericMatches,
    metaMatches,
    inferenceMatches,
    questionCount,
    repeatedSentenceStarts,
    maxSimilarity,
    topicQuality,
    issues,
    warnings,
    passes: issues.length === 0,
  };
}

module.exports = { assessScript, formatPlainNarration, similarity, openingHasHook, topicAnchors, topicFit };
