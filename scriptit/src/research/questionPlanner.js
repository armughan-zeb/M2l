'use strict';

const STOPWORDS = new Set([
  'about', 'after', 'again', 'also', 'been', 'before', 'being', 'between', 'could',
  'does', 'from', 'have', 'into', 'more', 'most', 'over', 'that', 'their', 'there',
  'these', 'they', 'this', 'those', 'through', 'what', 'when', 'which', 'while', 'with',
]);

function tokens(value) {
  return [...new Set(String(value || '').toLowerCase().match(/[a-z0-9]{3,}/g) || [])]
    .filter((token) => !STOPWORDS.has(token));
}

function question(id, kind, text, topic, extra = {}) {
  const searchTerms = [...new Set([...tokens(topic), ...tokens(text), ...(extra.searchTerms || [])])];
  const query = extra.query || `${topic} ${extra.suffix || ''}`.trim();
  return {
    id,
    kind,
    question: text,
    query,
    queries: [...new Set([query, ...(extra.queries || [])])].slice(0, 3),
    searchTerms,
    priority: extra.priority || 'normal',
    required: extra.required !== false,
  };
}

function buildResearchPlan({ topic, niche = 'general' } = {}) {
  const cleanTopic = String(topic || '').trim();
  if (!cleanTopic) throw new Error('Research topic is required');

  const questions = [
    question('identity', 'identity', `What exactly is ${cleanTopic}, and what is the central event or subject?`, cleanTopic, {
      suffix: 'explained', priority: 'high',
    }),
    question('primary-record', 'primary', `What do first-party, official, government, institutional, or original records say about ${cleanTopic}?`, cleanTopic, {
      suffix: 'official announcement statement filing report', queries: [`${cleanTopic} official`, `${cleanTopic} primary source`], priority: 'high',
    }),
    question('timeline', 'timeline', `What is the verified timeline of the important events connected to ${cleanTopic}?`, cleanTopic, {
      suffix: 'timeline dates chronology', priority: 'high',
    }),
    question('evidence', 'evidence', `Which concrete facts, figures, documents, or observations can be directly verified?`, cleanTopic, {
      suffix: 'facts figures data report', priority: 'high',
    }),
    question('counterclaims', 'counterclaim', `What credible sources disagree, qualify, or present a different explanation?`, cleanTopic, {
      suffix: 'criticism analysis disputed claims', queries: [`${cleanTopic} criticism`, `${cleanTopic} disputed claims`], priority: 'high', required: false,
    }),
    question('consequences', 'consequences', `What documented consequences or stakes follow from the verified facts?`, cleanTopic, {
      suffix: 'impact consequences analysis', required: false,
    }),
    question('unknowns', 'unknowns', `What remains unknown, unconfirmed, or impossible to conclude from the available evidence?`, cleanTopic, {
      suffix: 'what is unknown unconfirmed', required: false,
    }),
  ];

  if (['science', 'health'].includes(niche)) {
    questions.splice(4, 0, question('methods', 'methods', `How was the evidence about ${cleanTopic} measured, tested, or collected, and what are its limitations?`, cleanTopic, {
      suffix: 'study methods limitations evidence', priority: 'high',
    }));
  } else if (['finance', 'business'].includes(niche)) {
    questions.splice(4, 0, question('numbers', 'numbers', `What do the underlying filings, datasets, or financial numbers show about ${cleanTopic}?`, cleanTopic, {
      suffix: 'filing financial data results', priority: 'high',
    }));
  } else if (['history', 'biography', 'politics'].includes(niche)) {
    questions.splice(2, 0, question('records', 'records', `Which archives, public records, speeches, interviews, or contemporaneous documents establish the story of ${cleanTopic}?`, cleanTopic, {
      suffix: 'archives primary documents records', priority: 'high',
    }));
  }

  return {
    version: 1,
    topic: cleanTopic,
    niche,
    createdAt: new Date().toISOString(),
    questions,
  };
}

function matchQuestion(questionItem, claim) {
  const claimTokens = new Set(tokens(claim && (claim.text || claim.title || '')));
  const terms = questionItem.searchTerms || tokens(questionItem.question);
  if (!terms.length || !claimTokens.size) return 0;
  const overlap = terms.filter((term) => claimTokens.has(term)).length / Math.min(terms.length, 10);
  const titleTokens = new Set(tokens(claim && claim.title));
  const titleBoost = terms.filter((term) => titleTokens.has(term)).length ? 0.15 : 0;
  return Math.min(1, overlap + titleBoost);
}

function buildCoverageReport({ plan, evidence } = {}) {
  const questions = plan?.questions || [];
  const claims = evidence?.atomicClaims?.length ? evidence.atomicClaims : (evidence?.claims || []);
  const rows = questions.map((item) => {
    const matches = claims
      .map((claim) => ({ claim, score: matchQuestion(item, claim) }))
      .filter((entry) => entry.score >= 0.28)
      .sort((a, b) => b.score - a.score)
      .slice(0, 5);
    const hasPrimary = matches.some(({ claim }) => claim.authorityTier === 'P0' || claim.official);
    const status = matches.length >= 2 && (item.kind !== 'primary' || hasPrimary)
      ? 'answered'
      : matches.length ? 'partial' : 'missing';
    return {
      id: item.id,
      kind: item.kind,
      question: item.question,
      priority: item.priority,
      required: item.required,
      status,
      hasPrimary,
      claimIds: matches.map(({ claim }) => claim.id),
    };
  });
  const answered = rows.filter((row) => row.status === 'answered').length;
  const partial = rows.filter((row) => row.status === 'partial').length;
  const missing = rows.filter((row) => row.status === 'missing');
  const requiredMissing = rows.filter((row) => row.required && row.status === 'missing');
  const score = rows.length ? (answered + partial * 0.5) / rows.length : 0;
  return {
    planVersion: plan?.version || 1,
    score: Number(score.toFixed(3)),
    answered,
    partial,
    missing: missing.length,
    requiredMissing: requiredMissing.map((row) => row.id),
    rows,
    ready: Boolean(evidence?.sufficient && requiredMissing.length === 0),
  };
}

module.exports = { buildResearchPlan, buildCoverageReport, matchQuestion };
