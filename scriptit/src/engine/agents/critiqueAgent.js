'use strict';

/**
 * Critique & Scoring Agent.
 * 
 * Scores the script against the 4 core dimensions on a 1–5 rubric:
 * 1. Coherence (thesis focus, logical flow, fact consistency)
 * 2. Surprise & Tension (unpredictability, curious open loops, revelations)
 * 3. Pacing & Retention (pattern interrupts every ~90s, section bridges, no drag)
 * 4. Voice Authenticity (human speech cadence, zero AI-slop cliches)
 * 
 * Guides bounded revision loops (max 1–2 loops) to guarantee banger quality.
 */

const { evaluateVoiceAuthenticity } = require('./voiceAgent');

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
 * Programmatic rubric evaluation.
 */
function evaluateRubricProgrammatic({ script, topic, retentionChecklist, memoryAudit }) {
  const words = script.split(/\s+/).filter(Boolean).length;
  const voiceAudit = evaluateVoiceAuthenticity(script);

  // 1. Coherence (1-5)
  let coherence = 4;
  if (!memoryAudit?.allLoopsResolved) coherence -= 1;
  if (words < 200) coherence -= 1;

  // 2. Surprise & Tension (1-5)
  let surprise = 4;
  const questionCount = (script.match(/\?/g) || []).length;
  if (questionCount === 0) surprise -= 1;
  if (questionCount > 8) surprise -= 1; // Overuse of rhetorical questions

  // 3. Pacing (1-5)
  let pacing = 3;
  if (retentionChecklist?.hasHook) pacing += 1;
  if (retentionChecklist?.patternInterruptCount >= 1) pacing += 1;

  // 4. Voice Authenticity (1-5)
  let voiceAuthenticity = Math.max(1, Math.min(5, Math.round(voiceAudit.score / 20)));

  const totalScore = coherence + surprise + pacing + voiceAuthenticity;
  const average = Math.round((totalScore / 4) * 10) / 10;
  const passes = coherence >= 3 && surprise >= 3 && pacing >= 3 && voiceAuthenticity >= 3;

  const feedback = [];
  if (coherence < 3) feedback.push('Anchor the narrative more tightly to the central thesis and resolve lingering questions.');
  if (surprise < 3) feedback.push('Raise the dramatic stakes and emphasize non-obvious details or counter-intuitive turns.');
  if (pacing < 3) feedback.push('Add sharper pattern interrupts and tighten section transitions.');
  if (voiceAuthenticity < 3) feedback.push(`Eliminate AI-tell words and vary sentence rhythm: ${voiceAudit.issues.join(' ')}`);

  return {
    scores: {
      coherence,
      surprise,
      pacing,
      voiceAuthenticity
    },
    totalScore,
    average,
    passes,
    feedback
  };
}

/**
 * Critique Agent: Evaluates script quality and determines if a revision pass is needed.
 */
async function critiqueScript({
  script,
  topic,
  thesisStatement = '',
  retentionChecklist = {},
  memoryState = null,
  apiKey = '',
  model = '',
  chatCompletionFn = null
}) {
  const memoryAudit = memoryState ? memoryState.auditState() : null;
  const baseResult = evaluateRubricProgrammatic({ script, topic, retentionChecklist, memoryAudit });

  if (!apiKey && !chatCompletionFn) {
    return baseResult;
  }

  const prompt = `You are an elite YouTube script doctor.
Evaluate this YouTube script about "${topic}" on a strict 1 to 5 rubric across 4 dimensions:

1. COHERENCE (1-5): Does the script relentlessly advance the core thesis without drifting?
2. SURPRISE (1-5): Does it reveal unexpected details, maintain curiosity, and avoid predictable clichés?
3. PACING (1-5): Do pattern interrupts, bridges, and escalations keep momentum high without dragging?
4. VOICE AUTHENTICITY (1-5): Does it sound like an authentic human creator rather than an LLM?

SCRIPT THESIS:
"${thesisStatement || topic}"

SCRIPT EXCERPT:
${script.slice(0, 3000)}

Output JSON ONLY:
{
  "coherence": 4,
  "surprise": 4,
  "pacing": 4,
  "voiceAuthenticity": 4,
  "passes": true,
  "criticalFeedback": ["Specific bullet point if any score is below 3"]
}`;

  try {
    const fn = chatCompletionFn || require('../../providers/openrouter').chatCompletion;
    const response = await fn({
      apiKey,
      model,
      messages: [
        { role: 'system', content: 'You are an objective YouTube script evaluator. Output JSON only.' },
        { role: 'user', content: prompt }
      ],
      temperature: 0.2,
      maxTokens: 500
    });

    const parsed = parseJsonResponse(response && response.content);
    if (parsed && typeof parsed.coherence === 'number') {
      const coherence = Math.max(1, Math.min(5, Number(parsed.coherence)));
      const surprise = Math.max(1, Math.min(5, Number(parsed.surprise)));
      const pacing = Math.max(1, Math.min(5, Number(parsed.pacing)));
      const voiceAuthenticity = Math.max(1, Math.min(5, Number(parsed.voiceAuthenticity)));
      const totalScore = coherence + surprise + pacing + voiceAuthenticity;
      const average = Math.round((totalScore / 4) * 10) / 10;
      const passes = coherence >= 3 && surprise >= 3 && pacing >= 3 && voiceAuthenticity >= 3;

      return {
        scores: { coherence, surprise, pacing, voiceAuthenticity },
        totalScore,
        average,
        passes,
        feedback: Array.isArray(parsed.criticalFeedback) ? parsed.criticalFeedback : baseResult.feedback
      };
    }
  } catch (err) {
    console.warn('[CritiqueAgent] LLM critique pass failed, using programmatic rubric:', err.message);
  }

  return baseResult;
}

module.exports = {
  critiqueScript,
  evaluateRubricProgrammatic
};
