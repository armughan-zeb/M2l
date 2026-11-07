'use strict';

/**
 * Voice & De-Slop Agent.
 * 
 * Rules:
 * 1. Eliminate detectable AI-tell phrasing, stock transitions, and corporate hedging.
 * 2. Ban "it's not X, it's Y" constructions.
 * 3. Enforce varied sentence cadence (short punchy sentences alternating with rhythmic clauses).
 * 4. Remove generic recap footers ("In conclusion...", "As we look back...").
 * 5. Apply style fingerprints per channel/niche.
 */

const BANNED_AI_PATTERNS = [
  { regex: /\b(?:in today's video|in this video|welcome back to the channel)\b/gi, replacement: '' },
  { regex: /\blet's (?:dive|unpack|explore|take a look|delve)\b/gi, replacement: 'consider' },
  { regex: /\bas we delve into\b/gi, replacement: 'when looking at' },
  { regex: /\bdelve[s]?\b/gi, replacement: 'look' },
  { regex: /\bdelving\b/gi, replacement: 'looking' },
  { regex: /\bbut here(?:'s| is) the (?:thing|twist|catch)\b/gi, replacement: 'the reality was different' },
  { regex: /\bthis changes everything\b/gi, replacement: 'this shifted the entire balance' },
  { regex: /\btestament to\b/gi, replacement: 'proof of' },
  { regex: /\bunwavering (?:dedication|commitment)\b/gi, replacement: 'stubborn focus' },
  { regex: /\b(?:rich )?tapestry\b/gi, replacement: 'complex reality' },
  { regex: /\bseamlessly\b/gi, replacement: 'directly' },
  { regex: /\bleverage[s]?\b/gi, replacement: 'use' },
  { regex: /\butilize[ds]?\b/gi, replacement: 'used' },
  { regex: /\bstreamline[ds]?\b/gi, replacement: 'simplified' },
  { regex: /\brobust\b/gi, replacement: 'strong' },
  { regex: /\bpivotal\b/gi, replacement: 'crucial' },
  { regex: /\bgroundbreaking\b/gi, replacement: 'unprecedented' },
  { regex: /\bbeacon of\b/gi, replacement: 'symbol of' },
  { regex: /\bcrucible of\b/gi, replacement: 'pressure of' },
  { regex: /\bit goes without saying\b/gi, replacement: 'obviously' },
  { regex: /\bat the end of the day\b/gi, replacement: 'ultimately' },
  { regex: /\b(?:in conclusion|to sum up|all in all|in summary)[,]?\s*/gi, replacement: '' }
];

// Replaces "it's not just X, it's Y" -> "it is Y"
const NOT_X_BUT_Y_REGEX = /\bit(?:'s| is) not (?:just |merely |only )?([^,]+),\s*(?:it(?:'s| is)|rather)\s+([^.]+)/gi;

// Summary footers to strip from the very end of scripts
const SUMMARY_FOOTER_PATTERNS = [
  /\b(?:in conclusion|to sum up|all in all|in the final analysis|as we look back)[^.]*\./gi,
  /\bthe journey of [^.]+ reminds us that[^.]*\./gi
];

/**
 * Applies deterministic rule-based de-slop and style filtering.
 */
function cleanVoiceRules(text, styleFingerprint = {}) {
  let cleaned = String(text || '');

  // 1. Remove "it's not X, it's Y" AI-tell constructions
  cleaned = cleaned.replace(NOT_X_BUT_Y_REGEX, (_match, _x, y) => {
    return `it became ${y.trim()}`;
  });

  // 2. Replace banned words and phrases
  for (const { regex, replacement } of BANNED_AI_PATTERNS) {
    cleaned = cleaned.replace(regex, replacement);
  }

  // 3. Remove banned words from channel-specific style profile
  if (Array.isArray(styleFingerprint.bannedWords)) {
    for (const word of styleFingerprint.bannedWords) {
      const reg = new RegExp(`\\b${word}\\b`, 'gi');
      cleaned = cleaned.replace(reg, '');
    }
  }

  // 4. Remove summary / recap footers
  const paragraphs = cleaned.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
  if (paragraphs.length > 2) {
    let lastPara = paragraphs[paragraphs.length - 1];
    for (const pattern of SUMMARY_FOOTER_PATTERNS) {
      lastPara = lastPara.replace(pattern, '').trim();
    }
    paragraphs[paragraphs.length - 1] = lastPara;
    cleaned = paragraphs.filter(Boolean).join('\n\n');
  }

  // 5. Clean extra spaces and punctuation anomalies
  cleaned = cleaned
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/\s+([.,!?;:])/g, '$1')
    .replace(/([.,!?;:])\1+/g, '$1')
    .trim();

  return cleaned;
}

/**
 * Evaluates the human voice authenticity score of a script.
 */
function evaluateVoiceAuthenticity(text) {
  let score = 100;
  const issues = [];

  for (const { regex } of BANNED_AI_PATTERNS) {
    const matches = (text.match(regex) || []);
    if (matches.length > 0) {
      score -= matches.length * 6;
      issues.push(`Contains AI-tell phrase: "${matches[0]}"`);
    }
  }

  const notXButY = (text.match(NOT_X_BUT_Y_REGEX) || []);
  if (notXButY.length > 0) {
    score -= notXButY.length * 10;
    issues.push(`Contains banned "it's not X, it's Y" rhetorical construction.`);
  }

  // Sentence length rhythm check
  const sentences = String(text || '').match(/[^.!?]+(?:[.!?](?!\d)|$)/g) || [];
  const lengths = sentences.map((s) => s.trim().split(/\s+/).filter(Boolean).length);
  
  if (lengths.length >= 6) {
    // Check for monotonic cadence: standard deviation of sentence lengths
    const mean = lengths.reduce((a, b) => a + b, 0) / lengths.length;
    const variance = lengths.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / lengths.length;
    const stdDev = Math.sqrt(variance);

    if (stdDev < 3.0) {
      score -= 15;
      issues.push('Monotonic sentence cadence detected: lack of sentence length variation.');
    }
  }

  return {
    score: Math.max(10, Math.min(100, score)),
    passes: score >= 80,
    issues: issues.slice(0, 5)
  };
}

/**
 * Voice & De-Slop Agent: Runs both rule-based de-slop and an optional LLM humanizer pass.
 */
async function deSlopScript({
  script,
  topic = '',
  voice = 'conversational',
  styleFingerprint = {},
  apiKey = '',
  model = '',
  chatCompletionFn = null
}) {
  // Step 1: Deterministic fast cleanup
  let cleaned = cleanVoiceRules(script, styleFingerprint);
  let evalResult = evaluateVoiceAuthenticity(cleaned);

  // If score is already high or no model available, return immediately
  if (evalResult.passes || (!apiKey && !chatCompletionFn)) {
    return {
      script: cleaned,
      authenticityScore: evalResult.score,
      issues: evalResult.issues,
      rewritten: false
    };
  }

  // Step 2: LLM de-slop pass for flagged sentences
  const prompt = `You are a ruthless script editor whose only job is to de-slop YouTube narration.
Make this script sound like a REAL human creator speaking naturally, NOT an AI writing an essay.

RULES:
1. Fix any robotic cadence or "essay language".
2. Remove any stock transitions ("let's dive in", "here's the thing", "in today's video").
3. Ban any "it's not X, it's Y" constructions.
4. Vary sentence length aggressively: mix short 3-word punchy sentences with rhythmic clauses.
5. Keep all facts, names, dates, and narrative structure intact.
6. Return ONLY the polished spoken script. No commentary.

FLAGGED ISSUES TO FIX:
${evalResult.issues.join('\n')}

SCRIPT TO POLISH:
${cleaned}`;

  try {
    const fn = chatCompletionFn || require('../../providers/openrouter').chatCompletion;
    const response = await fn({
      apiKey,
      model,
      messages: [
        { role: 'system', content: 'You are an elite YouTube narration editor. Output polished script only.' },
        { role: 'user', content: prompt }
      ],
      temperature: 0.3,
      maxTokens: Math.max(1500, cleaned.split(/\s+/).length * 2)
    });

    const llmCleaned = response && response.content ? response.content.trim() : '';
    if (llmCleaned && llmCleaned.split(/\s+/).length >= cleaned.split(/\s+/).length * 0.75) {
      cleaned = cleanVoiceRules(llmCleaned, styleFingerprint);
      evalResult = evaluateVoiceAuthenticity(cleaned);
      return {
        script: cleaned,
        authenticityScore: evalResult.score,
        issues: evalResult.issues,
        rewritten: true
      };
    }
  } catch (err) {
    console.warn('[VoiceAgent] LLM de-slop pass failed, using rule-based clean version:', err.message);
  }

  return {
    script: cleaned,
    authenticityScore: evalResult.score,
    issues: evalResult.issues,
    rewritten: false
  };
}

module.exports = {
  deSlopScript,
  cleanVoiceRules,
  evaluateVoiceAuthenticity,
  BANNED_AI_PATTERNS
};
