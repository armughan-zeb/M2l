const { classifyTopic } = require('./classifier');

const STRATEGIES = {
  general: [
    { id: 'contradiction', opening: 'Start with the surprising contradiction.', flow: ['promise', 'context', 'evidence', 'turn', 'meaning'] },
    { id: 'consequence', opening: 'Start with a concrete consequence the viewer can picture.', flow: ['consequence', 'cause', 'evidence', 'tradeoff', 'takeaway'] },
    { id: 'misconception', opening: 'Start with the common assumption, then correct it quickly.', flow: ['assumption', 'correction', 'evidence', 'nuance', 'takeaway'] },
  ],
  timeline: [
    { id: 'turning-points', opening: 'Open at the decisive turning point, then return to the beginning.', flow: ['turning-point', 'origin', 'escalation', 'outcome', 'legacy'] },
    { id: 'before-after', opening: 'Contrast the world before with the world after.', flow: ['before', 'pressure', 'change', 'after', 'meaning'] },
  ],
  biography: [
    { id: 'decisive-choice', opening: 'Open with the choice that defined the person.', flow: ['choice', 'background', 'obstacle', 'achievement', 'legacy'] },
    { id: 'human-cost', opening: 'Open with what success cost the person.', flow: ['cost', 'early-life', 'rise', 'reversal', 'legacy'] },
  ],
  comparison: [
    { id: 'criteria', opening: 'Open with the criterion that most changes the answer.', flow: ['stakes', 'criterion-one', 'criterion-two', 'criterion-three', 'verdict'] },
    { id: 'use-case', opening: 'Open by separating the viewers who benefit from each option.', flow: ['viewer-needs', 'option-a', 'option-b', 'tradeoffs', 'verdict'] },
  ],
  listicle: [
    { id: 'escalating-evidence', opening: 'Open with an unexpected entry, not the final winner.', flow: ['surprise', 'entries', 'escalation', 'top-entry', 'takeaway'] },
    { id: 'theme-led', opening: 'Open with the quality shared by the strongest entries.', flow: ['theme', 'entries', 'contrast', 'top-entry', 'takeaway'] },
  ],
  puzzle: [
    { id: 'evidence-trail', opening: 'Open with the unanswered question and the most reliable clue.', flow: ['question', 'known-facts', 'clues', 'answer', 'uncertainty'] },
    { id: 'false-lead', opening: 'Open with the explanation that seems right but fails under evidence.', flow: ['claim', 'problem', 'evidence', 'answer', 'limits'] },
  ],
  system: [
    { id: 'one-journey', opening: 'Follow one real world input from start to finish.', flow: ['input', 'stage-one', 'stage-two', 'failure-case', 'outcome'] },
    { id: 'problem-first', opening: 'Start with the problem the system was created to solve.', flow: ['problem', 'design', 'mechanism', 'limits', 'meaning'] },
  ],
};

const STRATEGY_ALIASES = {
  category: 'listicle',
  investigation: 'puzzle',
  collision: 'comparison',
  era: 'timeline',
  documentary: 'timeline',
  explainer: 'system',
  tutorial: 'system',
  review: 'comparison',
  news: 'timeline',
  'case-study': 'timeline',
  essay: 'general',
};

function hash(value) {
  let out = 2166136261;
  for (const char of String(value || '')) {
    out ^= char.charCodeAt(0);
    out = Math.imul(out, 16777619);
  }
  return out >>> 0;
}

function createNarrativePlan({ topic, contentType, evidence, recentStrategyIds = [] }) {
  const detected = contentType && contentType !== 'auto' ? contentType : classifyTopic(topic).type;
  const strategyType = STRATEGY_ALIASES[detected] || detected;
  const pool = STRATEGIES[strategyType] || STRATEGIES.general;
  const available = pool.filter((strategy) => !recentStrategyIds.includes(strategy.id));
  const choices = available.length ? available : pool;
  const seed = hash(`${topic}:${evidence.claims.map((claim) => claim.text).join('|')}:${recentStrategyIds.length}`);
  const strategy = choices[seed % choices.length];
  const claims = evidence.claims.slice(0, 8);

  return {
    contentType: detected || 'general',
    strategy,
    angle: claims[0]?.text || `Explain the most useful and evidence-backed side of ${topic}.`,
    factPriorities: claims.map((claim) => claim.id),
  };
}

function planToPromptText(plan) {
  return [
    `Narrative mode: ${plan.contentType}`,
    `Story strategy: ${plan.strategy.id}`,
    `Opening direction: ${plan.strategy.opening}`,
    `Required flow: ${plan.strategy.flow.join(', ')}`,
    `Central angle: ${plan.angle}`,
  ].join('\n');
}

module.exports = { STRATEGIES, STRATEGY_ALIASES, createNarrativePlan, planToPromptText };
