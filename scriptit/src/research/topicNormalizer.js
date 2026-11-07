'use strict';

const PREFIXES = [
  /^(?:the\s+)?(?:hidden|forgotten|untold|secret|true|real|little-known)\s+(?:story|history)\s+(?:behind|of|about)\s+(?:the\s+)?/i,
  /^(?:everything|all)\s+(?:related\s+to|about|on)\s+/i,
  /^(?:the\s+)?(?:complete|full|ultimate|definitive)\s+(?:guide|story|breakdown)\s+(?:to|of|about)\s+/i,
  /^(?:a\s+)?(?:deep\s+dive|documentary|analysis|breakdown)\s+(?:into|of|on|about)\s+/i,
  /^(?:explain|explain\s+the)\s+/i,
];

const SUFFIXES = [
  /\s+(?:explained|breakdown|deep\s+dive|documentary|analysis|guide|story)\s*$/i,
  /\s+for\s+(?:beginners|newcomers)\s*$/i,
];

function clean(value) {
  return String(value || '')
    .replace(/[“”]/g, '"')
    .replace(/[‘’]/g, "'")
    .replace(/\s+/g, ' ')
    .trim();
}

function canonicalAlias(value) {
  const text = clean(value);
  const franchise = '(?:gta|grand theft auto)\\s*[:\\-]?\\s*';
  if (new RegExp(`\\b${franchise}(?:san andreas|sa)\\b`, 'i').test(text)) return 'Grand Theft Auto: San Andreas';
  if (new RegExp(`\\b${franchise}(?:vice city)\\b`, 'i').test(text)) return 'Grand Theft Auto: Vice City';
  if (new RegExp(`\\b${franchise}(?:v|5)\\b`, 'i').test(text)) return 'Grand Theft Auto V';
  if (new RegExp(`\\b${franchise}(?:vi|6)\\b`, 'i').test(text)) return 'Grand Theft Auto VI';
  return text;
}

function normalizeTopic(value) {
  const original = clean(value);
  let subject = original;
  for (const pattern of PREFIXES) subject = subject.replace(pattern, '');
  for (const pattern of SUFFIXES) subject = subject.replace(pattern, '');
  subject = clean(subject).replace(/^[,:;\-]+|[,:;\-]+$/g, '').trim();
  if (!subject) subject = original;
  const canonical = canonicalAlias(subject || original);
  return {
    original,
    subject,
    canonical: canonical || original,
    changed: canonical !== original,
  };
}

// Strip title packaging before searching. A title often carries a complete
// subject followed by a curiosity hook ("What Can Be Verified?"). Searching
// the whole sentence makes generic hook words dominate results and can admit
// unrelated pages that happen to repeat those words.
function researchSubject(value) {
  const original = clean(value);
  let subject = original;
  // Strip viral hooks and listicle counts with adjectives before research (preserve 4-digit years)
  subject = subject.replace(/^(?:unthinkable now|shocking now|insane now|rarely seen now|forgotten now|hard to believe now)[,:\s-]+(?:but\s+)?/i, '');
  subject = subject.replace(/^(?!\d{4}\b)(?:\d+)\s+(?:surprising|forgotten|rare|iconic|bizarre|unusual|famous|strange|notable|shocking)\s+/i, '');
  subject = subject.replace(/\s*:\s*(?:a\s+)?(?:look\s+back|closer\s+look|look\s+inside|what\s+happened(?:\s+next)?|the\s+story\s+behind|what\s+science\s+says(?:\s+we\s+feel)?|what\s+the\s+public\s+footage\s+shows|what\s+you\s+need\s+to\s+know|explained|breakdown|timeline|guide|operations)\s*$/i, '');
  subject = subject.replace(/\s*[—–-]\s*(?:how it actually starts|what happened next).*/i, '');

  const hookSeparator = subject.match(/\s*:\s*(?=(?:what\b|which\b|why\b|how\b|where\b|when\b|a closer look\b|the real question\b|what we know\b))/i);
  if (hookSeparator && typeof hookSeparator.index === 'number') {
    const left = subject.slice(0, hookSeparator.index).trim();
    const right = subject.slice(hookSeparator.index).replace(/^\s*:\s*/, '')
      .replace(/^(?:what|which|why|how|where|when)\b/i, ' ')
      .replace(/\b(?:does|do|did|has|have|had|is|are|can|could|be|been|being|the|a|an|to|we|you|still|names?|exist|show|shows|revealed?|verified?|confirm(?:ed|s)?|establish(?:ed|es)?|involve|closer|look|about|at|auction|listing|headlines?)\b/gi, ' ')
      .replace(/[^\p{L}\p{N}'’\s-]/gu, ' ')
      .replace(/\s+/g, ' ').trim();
    // Keep meaningful subject terms from the question side of the title
    // (for example "forensic science" or "crew routine") while dropping
    // the generic curiosity wording that pollutes search results.
    subject = [left, right].filter(Boolean).join(' ');
  }
  subject = subject
    .replace(/^(?:the\s+)?(?:claims?|rumou?rs?)\s+(?:of|about|around)\s+/i, '')
    .replace(/^(?:a\s+)?(?:closer\s+)?look\s+at\s+/i, '')
    .replace(/\s+headlines?\b/gi, '')
    .trim();
  return subject || original;
}

function multiTierQueries(value) {
  const primary = researchSubject(value);
  const normalized = normalizeTopic(value).canonical;
  const queries = [primary];
  if (normalized && normalized !== primary) queries.push(normalized);

  const coreWords = primary
    .replace(/[^\p{L}\p{N}\s-]/gu, ' ')
    .split(/\s+/)
    .filter(w => w.length > 2 && !/^(?:what|which|why|how|where|when|about|into|from|with|this|that|these|those)$/i.test(w));
  if (coreWords.length >= 2 && coreWords.length < primary.split(/\s+/).length) {
    queries.push(coreWords.join(' '));
  }
  return [...new Set(queries.filter(Boolean))];
}

module.exports = { normalizeTopic, canonicalAlias, researchSubject, multiTierQueries };
