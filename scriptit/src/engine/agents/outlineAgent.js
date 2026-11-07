'use strict';

/**
 * Outline Agent.
 * 
 * Generates sequence-level (not scene-by-scene) outlines conditioned on the chosen format schema:
 * - Documentary: Generates explicit thesis statement + Point of Attack + 3 Acts ordered by impact.
 * - Narrative / Story: Generates character desire vs need + Dan Harmon Story Circle sequence mapping.
 * - Top 10 / List: Generates escalating items towards #1 with progress callbacks and overlay labels.
 * - Commentary: Generates stance upfront + claim-vs-truth tension progression.
 */

const { getStructureSchema, FORMATS } = require('../structure/structureSchemas');
const { wordsForDuration } = require('../../utils/duration');

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
 * Deterministic fallback generator for sequence outlines (used offline or when model is unavailable).
 */
function buildDeterministicOutline({ topic, format = 'documentary', duration = '10min', evidence = null }) {
  const totalWords = wordsForDuration(duration);
  const cleanTopic = String(topic || '').trim();
  const claims = evidence && Array.isArray(evidence.claims) ? evidence.claims : [];
  const primaryFact = claims[0]?.text || `The definitive record of ${cleanTopic}`;

  if (['story', 'narrative', 'cinematic'].includes(format)) {
    const seqWords = Math.round(totalWords / 4);
    return {
      format: FORMATS.NARRATIVE,
      framework: 'Dan Harmon Story Circle',
      thesisStatement: `The pursuit of ${cleanTopic} forced an irreversible transformation.`,
      characterWantNeed: { want: 'Immediate triumph or status', need: 'Deeper truth and sacrifice' },
      pointOfAttack: `The moment the old world becomes unlivable and the search begins.`,
      sequences: [
        {
          id: 'seq_1',
          name: 'Comfort Zone to Need',
          storyNodes: ['comfort_zone', 'need_want'],
          summary: `Introduce the ordinary world of ${cleanTopic} and the inciting desire that shatters the calm.`,
          targetWords: seqWords,
          openLoops: [{ id: 'loop_ordinary_flaw', title: 'What fatal flaw was hiding in plain sight?' }]
        },
        {
          id: 'seq_2',
          name: 'Crossing Over & The Search',
          storyNodes: ['unfamiliar_situation', 'adapt_search'],
          summary: `Entering the high-stakes crucible: early trials, costly adaptations, and escalating friction.`,
          targetWords: seqWords,
          openLoops: [{ id: 'loop_unexpected_ally_or_trap', title: 'Will the adaptation succeed or backfire?' }]
        },
        {
          id: 'seq_3',
          name: 'Finding the Prize & The Heavy Price',
          storyNodes: ['find_payoff', 'heavy_price'],
          summary: `The breakthrough moment, immediately followed by the devastating collateral cost.`,
          targetWords: seqWords,
          closeLoops: ['loop_unexpected_ally_or_trap']
        },
        {
          id: 'seq_4',
          name: 'The Return Transformed',
          storyNodes: ['return_familiar', 'changed'],
          summary: `Returning to the start, permanently altered by the ordeal and its hard-won payoff.`,
          targetWords: seqWords,
          closeLoops: ['loop_ordinary_flaw']
        }
      ]
    };
  }

  if (['listicle', 'list', 'top10'].includes(format)) {
    const itemCount = duration === '3min' ? 5 : 7;
    const itemWords = Math.round((totalWords * 0.85) / itemCount);
    const items = [];
    for (let i = itemCount; i >= 1; i--) {
      items.push({
        rank: i,
        label: `Level ${i}: The Turning Point`,
        tonalBeat: i === 1 ? 'Mind-Bending Pinnacle' : (i <= 3 ? 'High Stakes Escalation' : 'Surprising Origin'),
        summary: `Entry #${i} illustrating escalating stakes in ${cleanTopic}.`,
        targetWords: itemWords,
        progressCallback: (i % 3 === 0 && i !== 1) ? `That was #${i}. #${i - 1} is where the controversy turns severe...` : null
      });
    }
    return {
      format: FORMATS.LISTICLE,
      thesisStatement: `The greatest aspects of ${cleanTopic} escalate from baseline to the truly unfathomable #1.`,
      pointOfAttack: `Confirm the countdown premise immediately before revealing the starting entry.`,
      items,
      sequences: items.map((it) => ({
        id: `seq_item_${it.rank}`,
        name: `#${it.rank}: ${it.label}`,
        summary: it.summary,
        targetWords: it.targetWords,
        progressCallback: it.progressCallback
      }))
    };
  }

  if (['commentary', 'reaction'].includes(format)) {
    const seqWords = Math.round(totalWords / 4);
    return {
      format: FORMATS.COMMENTARY,
      thesisStatement: `The mainstream narrative around ${cleanTopic} is dangerously incomplete.`,
      stance: `Dissecting the marketing myth versus the verifiable reality.`,
      pointOfAttack: `The exact soundbite or headline that launched the public debate.`,
      sequences: [
        {
          id: 'seq_1',
          name: 'The Stance & The Public Claim',
          summary: `Directly introduce what they want you to believe versus our unambiguous counter-stance.`,
          targetWords: seqWords,
          openLoops: [{ id: 'loop_smoking_gun', title: 'What is the specific proof that dismantles the claim?' }]
        },
        {
          id: 'seq_2',
          name: 'Deconstruction of the Superficial Story',
          summary: `Examine the surface arguments and highlight the telltale inconsistencies.`,
          targetWords: seqWords
        },
        {
          id: 'seq_3',
          name: 'The Reality Gap & Smoking Gun Evidence',
          summary: `Reveal the verified facts and documents that prove what really happened.`,
          targetWords: seqWords,
          closeLoops: ['loop_smoking_gun']
        },
        {
          id: 'seq_4',
          name: 'The Greater Fallout & Decisive Verdict',
          summary: `Why this matters to the viewer and the lasting lesson for the industry.`,
          targetWords: seqWords
        }
      ]
    };
  }

  // Default: Documentary / Informational
  const seqWords = Math.round(totalWords / 4);
  return {
    format: FORMATS.DOCUMENTARY,
    thesisStatement: `Behind the myth of ${cleanTopic} lies a calculated chain of decisions that redefined everything.`,
    pointOfAttack: `The pivotal high-stakes moment where the entire outcome hung in the balance.`,
    acts: [
      { act: 1, title: 'The Point of Attack & The Illusion' },
      { act: 2, title: 'The Hidden Mechanism & Pressure' },
      { act: 3, title: 'The Reckoning & Legacy' }
    ],
    sequences: [
      {
        id: 'seq_1',
        act: 1,
        name: 'The Point of Attack & Accepted Myth',
        summary: `Enter at the crisis point; reveal what everyone thought was true before the fracture.`,
        targetWords: seqWords,
        openLoops: [{ id: 'loop_hidden_cause', title: 'What unexpected catalyst triggered the crisis?' }]
      },
      {
        id: 'seq_2',
        act: 2,
        name: 'The Hidden Machinery & Competing Forces',
        summary: `Unpack the structural forces, competing agendas, and technical realities behind the scenes.`,
        targetWords: seqWords
      },
      {
        id: 'seq_3',
        act: 2,
        name: 'The Decisive Escalation',
        summary: `The point of no return: the gamble made when all safe options were exhausted.`,
        targetWords: seqWords,
        closeLoops: ['loop_hidden_cause']
      },
      {
        id: 'seq_4',
        act: 3,
        name: 'The Fallout & Permanent Consequence',
        summary: `The long-term transformation, the hard-earned payoff, and why it matters today.`,
        targetWords: seqWords
      }
    ]
  };
}

/**
 * Outline Agent: LLM-driven sequence outline generator with schema enforcement.
 */
async function generateOutline({
  topic,
  format = 'documentary',
  duration = '10min',
  evidence = null,
  briefText = '',
  apiKey = '',
  model = '',
  chatCompletionFn = null
}) {
  const schema = getStructureSchema(format);
  const targetWords = wordsForDuration(duration);

  // If no LLM available, return rich deterministic outline immediately
  if (!apiKey && !chatCompletionFn) {
    return buildDeterministicOutline({ topic, format, duration, evidence });
  }

  const prompt = `You are an elite YouTube showrunner and master story architect.
Design a SEQUENCE-LEVEL OUTLINE (not a laundry list of scenes) for a high-retention YouTube video about: "${topic}".
Target duration: ${duration} (~${targetWords} words total).
Format category: ${format}.

STRICT FORMAT RULES:
${format === 'documentary' ? `
1. Generate a single bold THESIS STATEMENT before anything else. Every sequence must test or prove this thesis.
2. Define a POINT OF ATTACK: the exact high-drama moment/frame the video enters the story (do NOT start at the chronological beginning).
3. Structure in 3 Acts ordered by IMPACT, not chronology.
4. Output 4 to 5 sequences maximum, each with a clear target word count summing to ~${targetWords}.
` : ''}
${format === 'narrative' || format === 'story' ? `
1. Use Dan Harmon's 8-step Story Circle (comfort zone -> want -> unfamiliar -> adapt -> find -> pay price -> return -> changed).
2. Group into 4 distinct dramatic sequences.
3. Identify the protagonist's outer want vs inner need.
` : ''}
${format === 'listicle' ? `
1. Rank entries escalating towards #1 (anti-front-loading: strongest, most controversial point at the end).
2. For each item: short text-overlay label, distinct visual/tonal beat, and progress callback phrases every 2-3 items.
` : ''}
${format === 'commentary' ? `
1. State the controversial STANCE early.
2. Outline the continuous tension between "What they claim" vs "What is actually true".
` : ''}

OPEN LOOPS:
Identify at least 1-2 open curiosity loops that get planted in Sequence 1 or 2, and specify exactly which sequence must close them.

Output ONLY valid JSON in this exact structure:
{
  "format": "${format}",
  "thesisStatement": "One sentence core thesis or driving argument",
  "pointOfAttack": "Specific visual/narrative moment where video enters",
  "sequences": [
    {
      "id": "seq_1",
      "name": "Sequence Name",
      "summary": "What happens and what changes",
      "targetWords": ${Math.round(targetWords / 4)},
      "openLoops": [{"id": "loop_1", "title": "Specific question posed to viewer"}],
      "closeLoops": []
    }
  ]
}`;

  try {
    const fn = chatCompletionFn || require('../../providers/openrouter').chatCompletion;
    const response = await fn({
      apiKey,
      model,
      messages: [
        { role: 'system', content: 'You are an expert YouTube script architect. Output JSON only.' },
        { role: 'user', content: prompt }
      ],
      temperature: 0.4,
      maxTokens: 1500
    });

    const parsed = parseJsonResponse(response && response.content);
    if (parsed && Array.isArray(parsed.sequences) && parsed.sequences.length >= 2) {
      return parsed;
    }
  } catch (err) {
    console.warn('[OutlineAgent] LLM outline generation failed, falling back to deterministic outline:', err.message);
  }

  return buildDeterministicOutline({ topic, format, duration, evidence });
}

module.exports = {
  generateOutline,
  buildDeterministicOutline
};
