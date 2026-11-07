'use strict';

/**
 * Hook Agent.
 * 
 * Rules:
 * 1. Runs LAST after the body script is finished (you cannot sell a payoff that doesn't exist yet).
 * 2. Employs the 3-part formula (all 3 land within 15–20 seconds / ~35-45 words):
 *    - Part 1: Validate the click (0–3s) — confirm viewer is in the right place.
 *    - Part 2: Raise the stakes (3–8s) — why this matters right now.
 *    - Part 3: Open a curiosity loop (8–15s) — hint at the payoff without spoiling it.
 * 3. Hook sentences MUST be under 10 words each.
 * 4. Strictly bans "Hey guys", "Welcome back", credentials, or vague teases.
 * 5. Generates 3 hook variants per script:
 *    - result-first
 *    - bold-claim
 *    - relatable-problem
 * 6. Scores each variant and selects the strongest.
 */

const { formatPlainNarration } = require('../../agents/scriptQuality');

function parseJsonResponse(value) {
  if (!value) return null;
  const text = String(value).replace(/```json|```/gi, '').trim();
  const start = text.indexOf('{');
  const end = text.lastIndexOf('}');
  if (start < 0 || end <= start) return null;
  try {
    return JSON.parse(text.slice(start, end + 1));
  } catch (_) {
    return null;
  }
}

/**
 * Validates whether hook sentences adhere to the <10 words per sentence rule.
 */
function checkSentenceWordCount(text) {
  const sentences = String(text || '').match(/[^.!?]+(?:[.!?](?!\d)|$)/g) || [];
  const wordsPerSentence = sentences.map((s) => s.trim().split(/\s+/).filter(Boolean).length);
  const maxWords = Math.max(0, ...wordsPerSentence);
  const compliant = wordsPerSentence.every((w) => w <= 12); // Under 10-12 words target
  return { compliant, maxWords, sentenceCount: sentences.length };
}

/**
 * Scores a hook variant based on YouTube retention best practices.
 */
function scoreHookVariant(variant, bodyScript = '', topic = '') {
  let score = 100;
  const fullText = `${variant.validateClick || ''} ${variant.raiseStakes || ''} ${variant.openCuriosityLoop || ''}`.trim();
  const words = fullText.split(/\s+/).filter(Boolean).length;

  // Length penalty: sweet spot is 25-50 words (12-20s speech)
  if (words < 18) score -= 25;
  if (words > 60) score -= 20;

  // Sentence length check: short sentences punch harder
  const check = checkSentenceWordCount(fullText);
  if (check.maxWords > 12) {
    score -= (check.maxWords - 12) * 3;
  }

  // Ban stock cliches
  if (/\b(welcome back|in this video|today we|hey guys|what's up|without further ado)\b/i.test(fullText)) {
    score -= 50;
  }

  // Check for tension / curiosity markers
  if (!/\?|\bwhy\b|\bhow\b|\buntil\b|\bsecret\b|\bcost\b|\bbroke\b|\breal\b|\bhappened\b/i.test(variant.openCuriosityLoop || '')) {
    score -= 15;
  }

  return Math.max(10, score);
}

/**
 * Deterministic generator for 3 hook variants (offline or fallback).
 */
function buildDeterministicHooks(topic, bodyScript = '') {
  const clean = String(topic || '').trim();
  
  // Reuse narration that already exists; fallback hooks must not invent a
  // secret, disaster, public reaction or discovery to manufacture stakes.
  const sentences = formatPlainNarration(bodyScript).match(/[^.!?]+(?:[.!?](?!\d)|$)/g) || [];
  const concise = sentences.map(sentence => sentence.trim()).filter(sentence =>
    sentence.split(/\s+/).length >= 4 && sentence.split(/\s+/).length <= 12);
  const first = concise[0] || `What explains the story of ${clean}?`;
  const last = concise.length > 1 ? concise.at(-1) : 'Follow how the details connect.';

  const resultFirst = {
    type: 'result-first',
    validateClick: first,
    raiseStakes: last,
    openCuriosityLoop: `How do these details explain ${clean}?`
  };

  const boldClaim = {
    type: 'bold-claim',
    validateClick: `What stands out in the story of ${clean}?`,
    raiseStakes: first,
    openCuriosityLoop: `How does the story reach its conclusion?`
  };

  const relatableProblem = {
    type: 'relatable-problem',
    validateClick: `What explains ${clean}?`,
    raiseStakes: last,
    openCuriosityLoop: `How do the pieces fit together?`
  };

  const variants = [resultFirst, boldClaim, relatableProblem].map((v) => {
    const combined = `${v.validateClick} ${v.raiseStakes} ${v.openCuriosityLoop}`;
    return {
      ...v,
      text: combined,
      wordCount: combined.split(/\s+/).filter(Boolean).length,
      score: scoreHookVariant(v, bodyScript, clean)
    };
  });

  variants.sort((a, b) => b.score - a.score);
  return {
    selectedHook: variants[0],
    variants
  };
}

/**
 * Hook Agent: Generates 3 hook variants conditioned on the completed body script,
 * then scores and selects the best one.
 */
async function generateHook({
  topic,
  bodyScript,
  thesisStatement = '',
  apiKey = '',
  model = '',
  chatCompletionFn = null
}) {
  if (!apiKey && !chatCompletionFn) {
    return buildDeterministicHooks(topic, bodyScript);
  }

  // We provide the first 500 characters and last 500 characters of the body script
  // so the hook can accurately validate the click and tease the actual climax/payoff!
  const scriptStart = bodyScript.slice(0, 600).trim();
  const scriptEnd = bodyScript.slice(-600).trim();

  const prompt = `You are an elite YouTube retention specialist.
Write the OPENING HOOK for a YouTube script about "${topic}".
The body of the script has already been written. Here is how it begins and how it pays off:

BODY SCRIPT OPENING:
"${scriptStart}"

BODY SCRIPT CLIMAX & PAYOFF:
"${scriptEnd}"

THESIS:
"${thesisStatement || topic}"

HOOK SPECIFICATIONS (3-Part Formula):
1. Part 1: Validate the Click (0–3 seconds) — Instantly confirm the viewer clicked the right video.
2. Part 2: Raise the Stakes (3–8 seconds) — Explain why this matters right now.
3. Part 3: Open a Curiosity Loop (8–15 seconds) — Tease the specific payoff from the script's ending without spoiling it.

CRITICAL CONSTRAINTS:
- Hook sentences MUST be under 10 words each. Punchy. Staccato. Immediate.
- NO "Hey guys", NO "Welcome back", NO "In this video", NO credentials, NO generic questions.
- Generate EXACTLY 3 hook variants:
  Variant A: "result-first" (opens with the surprising outcome or final statistic)
  Variant B: "bold-claim" (opens with a controversial thesis that challenges common belief)
  Variant C: "relatable-problem" (opens with an immediate observation or tension)

Output valid JSON ONLY in this format:
{
  "variants": [
    {
      "type": "result-first",
      "validateClick": "Under 10 words.",
      "raiseStakes": "Under 10 words.",
      "openCuriosityLoop": "Under 10 words."
    },
    {
      "type": "bold-claim",
      "validateClick": "Under 10 words.",
      "raiseStakes": "Under 10 words.",
      "openCuriosityLoop": "Under 10 words."
    },
    {
      "type": "relatable-problem",
      "validateClick": "Under 10 words.",
      "raiseStakes": "Under 10 words.",
      "openCuriosityLoop": "Under 10 words."
    }
  ]
}`;

  try {
    const fn = chatCompletionFn || require('../../providers/openrouter').chatCompletion;
    const response = await fn({
      apiKey,
      model,
      messages: [
        { role: 'system', content: 'You are an elite YouTube retention specialist. Output JSON only.' },
        { role: 'user', content: prompt }
      ],
      temperature: 0.7,
      maxTokens: 1000
    });

    const parsed = parseJsonResponse(response && response.content);
    if (parsed && Array.isArray(parsed.variants) && parsed.variants.length >= 1) {
      const scored = parsed.variants.map((v) => {
        const validateClick = formatPlainNarration(v.validateClick);
        const raiseStakes = formatPlainNarration(v.raiseStakes);
        const openCuriosityLoop = formatPlainNarration(v.openCuriosityLoop);
        const combined = `${validateClick} ${raiseStakes} ${openCuriosityLoop}`.trim();
        return {
          type: v.type || 'custom',
          validateClick,
          raiseStakes,
          openCuriosityLoop,
          text: combined,
          wordCount: combined.split(/\s+/).filter(Boolean).length,
          score: scoreHookVariant({ validateClick, raiseStakes, openCuriosityLoop }, bodyScript, topic)
        };
      });

      scored.sort((a, b) => b.score - a.score);
      return {
        selectedHook: scored[0],
        variants: scored
      };
    }
  } catch (err) {
    console.warn('[HookAgent] LLM hook generation failed, using deterministic hooks:', err.message);
  }

  return buildDeterministicHooks(topic, bodyScript);
}

module.exports = {
  generateHook,
  scoreHookVariant,
  buildDeterministicHooks
};
