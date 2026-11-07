const PATTERNS = [
  { type: 'listicle', regex: /\b(top|best|greatest|worst|most|ultimate)\s+\d+/i, label: 'Ranked List' },
  { type: 'listicle', regex: /\d+\s+(best|greatest|worst|most)/i, label: 'Ranked List' },
  { type: 'listicle', regex: /\b(top|best|greatest|worst|ultimate)\b.*\d+/i, label: 'Ranked List' },

  { type: 'category', regex: /\b(every|all|types?\s+of|kinds?\s+of|list\s+of|different\s+types)\b/i, label: 'Category Breakdown' },

  { type: 'puzzle', regex: /^why\b/i, label: 'Question Investigation' },
  { type: 'puzzle', regex: /\b(why\s+(did|does|is|are|was|were|can|don)|mystery\s+of|what\s+if)\b/i, label: 'Question Investigation' },

  { type: 'system', regex: /^how\s+(\w+\s+)?(works|is|are|does|built|made|function|structured)/i, label: 'System Explanation' },

  { type: 'timeline', regex: /\b(history|evolution|origin|origins|story|timeline|chronicle)\s+of\b/i, label: 'Timeline' },
  { type: 'timeline', regex: /\b(history|evolution|rise|development)\s+of\b/i, label: 'Timeline' },

  { type: 'biography', regex: /\b(rise\s+and\s+fall|life\s+(and|of)|who\s+(is|was)|biography\s+of|story\s+of)\b/i, label: 'Rise & Fall Story' },

  { type: 'comparison', regex: /\b(vs|versus|compared?\s+(to|with)|difference\s+between|or\s+\w+\s+better)\b/i, label: 'Comparison' },

  { type: 'collision', regex: /\b(rival|clash|feud|war\s+(between|of)|rivalry)/i, label: 'Collision Story' },

  { type: 'era', regex: /\b(era|age\s+of|golden\s+(age|era)|decade|generation)\b/i, label: 'Era Portrait' },

  { type: 'investigation', regex: /\b(scandal|dark\s+side|inside\s+(the|a|of)|truth\s+about|corruption|exposed)\b/i, label: 'Investigation' },
  { type: 'tutorial', regex: /^(?:how\s+to|step[- ]by[- ]step|beginner(?:'s)?\s+guide)\b/i, label: 'Tutorial' },
  { type: 'review', regex: /\b(review|worth\s+it|pros\s+and\s+cons|should\s+you\s+buy)\b/i, label: 'Review' },
  { type: 'explainer', regex: /\b(explained|what\s+is|complete\s+guide|everything\s+you\s+need)\b/i, label: 'Explainer' },
  { type: 'documentary', regex: /\b(documentary|untold\s+story|full\s+story)\b/i, label: 'Documentary' },
  { type: 'case-study', regex: /\b(case\s+study|how\s+.+\s+(?:won|failed|grew|collapsed))\b/i, label: 'Case Study' },
  { type: 'essay', regex: /\b(video\s+essay|essay\s+on)\b/i, label: 'Video Essay' },
  { type: 'story', regex: /\b(tale\s+of|character\s+arc|legend\s+of)\b/i, label: 'Character Story' },
  { type: 'cinematic', regex: /\b(cinematic|epic\s+tale)\b/i, label: 'Cinematic Story' },
];

const CORE_FORMAT_MAPPING = {
  listicle: 'listicle',
  category: 'listicle',
  puzzle: 'documentary',
  system: 'explainer',
  timeline: 'documentary',
  biography: 'narrative',
  comparison: 'commentary',
  collision: 'commentary',
  era: 'documentary',
  investigation: 'documentary',
  general: 'documentary',
  tutorial: 'explainer',
  review: 'commentary',
  explainer: 'explainer',
  documentary: 'documentary',
  news: 'documentary',
  'case-study': 'narrative',
  essay: 'commentary',
  story: 'narrative',
  cinematic: 'narrative',
};

const TYPE_LABELS = {
  listicle: 'Listicle',
  category: 'Category',
  puzzle: 'Puzzle',
  system: 'System',
  timeline: 'Timeline',
  biography: 'Biography',
  comparison: 'Comparison',
  collision: 'Collision',
  era: 'Era',
  investigation: 'Investigation',
  general: 'General Story',
  tutorial: 'Tutorial',
  review: 'Review',
  explainer: 'Explainer',
  documentary: 'Documentary',
  news: 'News Analysis',
  'case-study': 'Case Study',
  essay: 'Video Essay',
  story: 'Character Story',
  cinematic: 'Cinematic Story',
};

function classifyTopic(topic) {
  if (!topic || !topic.trim()) {
    return { type: 'general', engine: 'General Story', coreFormat: 'documentary', structureTemplate: 'Documentary 3-Act Thesis' };
  }

  const t = topic.trim();
  for (const p of PATTERNS) {
    if (p.regex.test(t)) {
      const coreFormat = CORE_FORMAT_MAPPING[p.type] || 'documentary';
      return {
        type: p.type,
        engine: p.label,
        coreFormat,
        structureTemplate: coreFormat === 'narrative' ? 'Dan Harmon Story Circle'
          : (coreFormat === 'listicle' ? 'Escalating Top 10 List'
          : (coreFormat === 'commentary' ? 'Commentary Stance & Reality Gap'
          : (coreFormat === 'explainer' ? 'System Input-to-Output Journey'
          : 'Documentary 3-Act Thesis')))
      };
    }
  }

  return { type: 'general', engine: 'General Story', coreFormat: 'documentary', structureTemplate: 'Documentary 3-Act Thesis' };
}

module.exports = { classifyTopic, TYPE_LABELS, PATTERNS, CORE_FORMAT_MAPPING };

