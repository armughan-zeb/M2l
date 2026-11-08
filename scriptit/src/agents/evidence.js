const { researchSubject } = require('../research/topicNormalizer');

function cleanText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

const AGGREGATOR_SOURCES = new Set(['duckduckgo', 'bing', 'yandex', 'gdelt', 'openalex', 'crossref']);
const TRUSTED_PUBLISHER_HOSTS = [
  'apnews.com', 'reuters.com', 'bbc.com', 'bbc.co.uk', 'theguardian.com', 'nytimes.com',
  'washingtonpost.com', 'wsj.com', 'ft.com', 'bloomberg.com', 'npr.org', 'pbs.org',
  'nature.com', 'science.org', 'sciencemag.org', 'scientificamerican.com', 'wired.com',
  'arstechnica.com', 'technologyreview.com', 'techcrunch.com', 'theverge.com', 'ign.com',
  'gamespot.com', 'polygon.com', 'eurogamer.net', 'variety.com', 'hollywoodreporter.com',
  'espn.com', 'coindesk.com', 'espncricinfo.com',
];
const EXCLUDED_SENSATIONAL_HOSTS = ['radaronline.com', 'theroyalobserver.com'];
// Headline templates and editorial adjectives are weak research anchors.
// Counting them lets unrelated pages pass when they share generic phrasing
// with a title such as "the untold story behind ...".
const TOPIC_NOISE = new Set([
  'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'did', 'does', 'for', 'from', 'how', 'in', 'into', 'is', 'it',
  'of', 'on', 'or', 'that', 'the', 'this', 'to', 'was', 'were', 'what', 'when', 'where', 'which', 'who', 'why', 'with',
  'about', 'after', 'again', 'behind', 'before', 'being', 'changed', 'decades', 'details', 'everything', 'hidden', 'history',
  'important', 'later', 'matters', 'more', 'never', 'really', 'remains', 'revealed', 'secret', 'secrets', 'still', 'story',
  'than', 'think', 'turns', 'untold', 'ways', 'world', 'yet',
  'forgotten', 'shocking', 'insane', 'lost', 'mysterious', 'bizarre', 'unbelievable', 'unusual', 'strange', 'unthinkable', 'rarely',
]);
// Primary-source domains are intentionally explicit. Search engines and
// aggregators can discover a claim, but they are not the authority for it.
// Keep this list conservative; a publisher with a full article body can still
// earn a high confidence claim without being in this list.
const OFFICIAL_HOSTS = [
  'mecum.com', 'clintonlibrary.gov', 'etoncollege.com', 'mathewsons.co.uk',
  'ojp.gov', 'nist.gov', 'atsb.gov.au', 'greatsouthernrail.com.au', 'royal.uk',
  'mini.co.uk', 'miniusa.com', 'bmwgroup.com', 'royal.uk',
  'formula1.com', 'fia.com',
  'nationalarchives.gov.uk', 'historicengland.org.uk',
  'rockstargames.com',
  'take2games.com',
  'playstation.com',
  'xbox.com',
  'nintendo.com',
  'ea.com',
  'ubisoft.com',
  'bethesda.net',
  'blizzard.com',
  'nasa.gov',
  'esa.int',
  'nih.gov',
  'cdc.gov',
  'fda.gov',
  'who.int',
  'sec.gov',
  'usgs.gov',
  'federalreserve.gov',
  'bls.gov',
  'fifa.com',
  'uefa.com',
  'olympics.com',
  'congress.gov',
  'archives.gov',
  'loc.gov',
  'openai.com',
  'blog.google',
  'microsoft.com',
];

function credibleSource(item) {
  const source = cleanText(item.source).toLowerCase();
  return source
    && !source.startsWith('r/')
    && !source.startsWith('hacker news')
    && source !== 'x'
    && !AGGREGATOR_SOURCES.has(source);
}

function hostname(url) {
  try {
    return new URL(url).hostname.toLowerCase().replace(/^www\./, '');
  } catch (_) {
    return '';
  }
}

function isOfficial(item) {
  const host = hostname(item.url || '');
  return OFFICIAL_HOSTS.some((domain) => host === domain || host.endsWith(`.${domain}`));
}

function isExcludedSensationalSource(item) {
  const host = hostname(item.url || '');
  return EXCLUDED_SENSATIONAL_HOSTS.some(domain => host === domain || host.endsWith(`.${domain}`));
}

function isTrustedPublisher(item) {
  const source = cleanText(item.source).toLowerCase();
  const host = hostname(item.url || '');
  if (source.startsWith('r/') || source.startsWith('hacker news') || source === 'x') return false;
  const knownHost = TRUSTED_PUBLISHER_HOSTS.some((domain) => host === domain || host.endsWith(`.${domain}`));
  if (knownHost) return true;
  if (!source || AGGREGATOR_SOURCES.has(source)) return false;
  // A publisher name supplied by a direct feed is attributable, but should
  // only be treated as high-confidence when it identifies a recognizable
  // newsroom, journal, institution, or official record. Unknown domains stay
  // medium-confidence even when their HTML body was successfully extracted.
  return credibleSource(item)
    && /official|institution|journal|news|press|agency|university|institute|museum|archive|government|reporting/i.test(source);
}

function sourceTier(item) {
  // Model-written dossier/anchor claims carry fabricated archive URLs and
  // must never pass as reference-grade evidence. They are synthetic context
  // only: the writer may qualify them, the gate may never count them.
  if (item && (item.synthetic === true || cleanText(item.source).toLowerCase() === 'encyclopedic historical archive')) return 'synthetic';
  const source = cleanText(item.source).toLowerCase();
  const host = hostname(item.url || '');
  if (isOfficial(item)) return 'primary';
  if (source === 'wikipedia') return 'reference';
  if (source.startsWith('r/') || source.startsWith('hacker news') || source === 'x' || host === 'x.com' || host.endsWith('.x.com') || host === 'twitter.com' || host.endsWith('.twitter.com')) return 'community';
  // A search engine is discovery-only when it supplies a snippet. Once the
  // linked page has been hydrated, the evidence is attributable to that
  // publisher rather than to the search engine that found it.
  if (AGGREGATOR_SOURCES.has(source)) return hasBody(item) ? 'publisher' : 'discovery';
  if (source.startsWith('hacker news')) return 'community';
  return 'publisher';
}

function authorityTier(tier) {
  return ({
    primary: 'P0',
    publisher: 'P1',
    reference: 'P2',
    community: 'P3',
    discovery: 'P4',
    synthetic: 'P4',
  })[tier] || 'P4';
}

function topicPhrases(query) {
  // Treat punctuation in names as a separator. Without this, "Auto:" is
  // compared as a literal token and an exact title such as "Grand Theft Auto:
  // San Andreas" is incorrectly discarded as unrelated evidence.
  const text = cleanText(query).normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().replace(/[^a-z0-9\s]+/g, ' ');
  if (/\bgta\s*(?:vi|6)\b|\bgrand theft auto\s*(?:vi|6)\b/i.test(text)) {
    return ['gta vi', 'gta 6', 'grand theft auto vi', 'grand theft auto 6'];
  }
  return [...new Set(text.split(/\s+/).filter((word) => word.length > 2 && !TOPIC_NOISE.has(word)))];
}

function relevanceScore(item, query) {
  const normalize = value => cleanText(value).normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();
  const titleText = normalize(item.title || '');
  const text = normalize(`${item.title || ''} ${item.articleText || item.snippet || ''}`);
  const queryText = researchSubject(query).toLowerCase();
  // A seeded primary cross-check may concern a related person/team rather
  // than repeat the full product name. Admit it only after fetching a body
  // that actually names the explicitly linked entity.
  if (isOfficial(item) && hasBody(item)
    && cleanText(item.contextForTopic).toLowerCase() === queryText
    && cleanText(item.contextEntity).length > 2
    && cleanText(item.articleText).toLowerCase().split(/[^a-z0-9]+/).includes(cleanText(item.contextEntity).toLowerCase())) return 1;
  if (/\bgta\s*(?:vi|6)\b|\bgrand theft auto\s*(?:vi|6)\b/i.test(queryText)) {
    const target = /\b(?:gta|grand theft auto)\s*(?:vi|6)\b/i.test(text);
    return target ? 2 + (isOfficial(item) ? 1 : 0) : 0;
  }
  const phrases = topicPhrases(queryText);
  if (!phrases.length) return 1;
  const words = phrases.flatMap((phrase) => phrase.split(/\s+/)).filter(w => w.length > 2);
  if (!words.length) return 1;
  const matched = words.filter((word) => new RegExp(`\\b${word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'i').test(text)).length;
  // Four-or-more anchor tokens need three matches. This rejects broad
  // collisions such as "Great Britain" or "Great Balls of Fire" for a
  // title about the Great Fire of London, while preserving the real event.
  const requiredMatches = words.length >= 4 ? 3 : words.length >= 2 ? 2 : 1;
  if (matched < requiredMatches) return 0;
  // A single generic word in a page title plus two scattered body matches is
  // not enough for multi-term subjects. Require either two title anchors or
  // three total anchors, so pages like the Wikipedia disambiguation "Past"
  // cannot qualify as research for British beer-history lists.
  const titleTokens = new Set(titleText.split(/[^a-z0-9]+/).filter(Boolean));
  const titleMatched = words.filter((word) => titleTokens.has(word)).length;
  const corePhrase = words.slice(0, 2).join(' ');
  if (words.length >= 3 && titleMatched < 2 && matched < 3 && !text.includes(corePhrase)) return 0;
  if (matched > 0) return (matched / words.length) + (isOfficial(item) ? 1 : 0);
  return 0;
}

function hasBody(item) {
  // Older fixtures do not have hasArticleBody; preserve their articleText semantics.
  return item.hasArticleBody === false ? false : Boolean(cleanText(item.articleText));
}

function claimConfidence(item) {
  const tier = sourceTier(item);
  if (tier === 'synthetic') return 'low';
  // Search/academic index metadata is a pointer, not a claim-bearing source.
  // Treating a long generated snippet as medium confidence let unrelated
  // Crossref/OpenAlex records satisfy the minimum evidence gate.
  if (tier === 'community') return 'low';
  if (tier === 'discovery') {
    const src = cleanText(item.source).toLowerCase();
    if (src === 'crossref' || src === 'openalex' || src === 'gdelt') return 'low';
    return cleanText(item.snippet || item.articleText).length >= 50 ? 'medium' : 'low';
  }
  if (tier === 'primary') return 'high';
  const src = cleanText(item.source).toLowerCase();
  if (tier === 'reference' || src.includes('wiki')) return 'high';
  if (isTrustedPublisher(item) || (src.includes('news') && !src.startsWith('hacker news'))) return 'high';
  if (cleanText(item.snippet || item.articleText).length > 20) return 'medium';
  return 'medium';
}

function claimKey(text) {
  return cleanText(text).toLowerCase().replace(/[^a-z0-9 ]/g, '').slice(0, 180);
}

function normalizeDate(value) {
  if (!value) return '';
  const text = String(value);
  const iso = text.match(/(\d{4}-\d{2}-\d{2})/);
  if (iso) return iso[1];
  const parsed = Date.parse(text);
  if (!Number.isNaN(parsed)) return new Date(parsed).toISOString().slice(0, 10);
  return '';
}

function isGoogleBoilerplate(item) {
  const t = String(item.title || '');
  return /^Google News - .* - Latest/i.test(t) || /Read full articles, watch videos|browse thousands of titles/i.test(t) || !t;
}

function isGoogleNewsWrapper(url) {
  return /^https:\/\/news\.google\.com\/rss\/articles\//i.test(url || '');
}

function splitSentences(value) {
  return String(value || '')
    .replace(/\s+/g, ' ')
    .match(/[^.!?]+(?:[.!?](?!\d)|$)/g) || [];
}

function stableId(value) {
  let hash = 2166136261;
  for (const char of String(value || '')) {
    hash ^= char.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return `fact-${(hash >>> 0).toString(16)}`;
}

function buildAtomicClaims(items, sourceClaims) {
  const claimByUrl = new Map(sourceClaims.map((claim) => [claim.url, claim]));
  const seen = new Set();
  const atomic = [];
  for (const item of items) {
    const sourceClaim = claimByUrl.get(item.url);
    if (!sourceClaim) continue;
    const body = cleanText(item.articleText || item.snippet || item.title);
    for (const rawSentence of splitSentences(body).slice(0, 12)) {
      const sentence = cleanText(rawSentence);
      if (sentence.length < 35 || sentence.length > 900) continue;
      const fingerprint = claimKey(sentence);
      if (!fingerprint || seen.has(fingerprint)) continue;
      seen.add(fingerprint);
      atomic.push({
        id: stableId(`${item.url}|${fingerprint}`),
        text: sentence,
        excerpt: sentence,
        sourceClaimId: sourceClaim.id,
        title: sourceClaim.title,
        url: sourceClaim.url,
        source: sourceClaim.source,
        publishedAt: sourceClaim.publishedAt,
        retrievedAt: sourceClaim.retrievedAt,
        confidence: sourceClaim.confidence,
        official: sourceClaim.official,
        sourceTier: sourceClaim.sourceTier,
        authorityTier: sourceClaim.authorityTier,
        hasArticleBody: sourceClaim.hasArticleBody,
        relevance: sourceClaim.relevance,
      });
      if (atomic.length >= 96) return atomic;
    }
  }
  return atomic;
}

function markerSet(text) {
  return new Set([
    ...(String(text || '').match(/\b(?:19|20)\d{2}\b/g) || []),
    ...(String(text || '').match(/\b\d+(?:\.\d+)?\s*(?:%|percent|million|billion|thousand|km|miles|years?)\b/gi) || [])
      .map((value) => value.toLowerCase().replace(/\s+/g, ' ')),
  ]);
}

function wordOverlap(left, right) {
  const a = new Set(cleanText(left).toLowerCase().match(/[a-z0-9]{4,}/g) || []);
  const b = new Set(cleanText(right).toLowerCase().match(/[a-z0-9]{4,}/g) || []);
  if (!a.size || !b.size) return 0;
  const shared = [...a].filter((word) => b.has(word)).length;
  return shared / Math.min(a.size, b.size);
}

function detectConflicts(claims) {
  const conflicts = [];
  for (let i = 0; i < claims.length; i += 1) {
    for (let j = i + 1; j < claims.length; j += 1) {
      const left = claims[i];
      const right = claims[j];
      if (!left.url || left.url === right.url) continue;
      const overlap = wordOverlap(left.text, right.text);
      if (overlap < 0.62) continue;
      const leftMarkers = markerSet(left.text);
      const rightMarkers = markerSet(right.text);
      const differentMarkers = leftMarkers.size && rightMarkers.size
        && [...leftMarkers].some((marker) => !rightMarkers.has(marker));
      const leftNegated = /\b(?:not|never|denied|disputed|cancelled|canceled|delayed)\b/i.test(left.text);
      const rightNegated = /\b(?:not|never|denied|disputed|cancelled|canceled|delayed)\b/i.test(right.text);
      if (!differentMarkers && leftNegated === rightNegated) continue;
      conflicts.push({
        claimId: left.id,
        conflictingClaimId: right.id,
        reason: differentMarkers ? 'similar claims contain different dates or numeric values' : 'similar claims use opposing certainty or negation',
        overlap: Number(overlap.toFixed(3)),
      });
    }
  }
  return conflicts.slice(0, 40);
}

function buildEvidenceLedger(research) {
  const rawItems = [
    ...(research.rss || []),
    ...(research.official || []),
    ...(research.wikipedia || []),
    ...(research.hackernews || []),
    ...(research.duckduckgo || []),
    ...(research.duckduckgoNews || []),
    ...(research.bing || []),
    ...(research.bingNews || []),
    ...(research.yandex || []),
    ...(research.x || []),
    ...(research.reddit || []),
    ...(research.structured || []),
    ...(research.targeted || []),
  ].filter((item) => item && item.url && !isGoogleBoilerplate(item) && !isGoogleNewsWrapper(item.url));
  const seen = new Set();
  const seenUrls = new Set();
  const claims = [];

  // The same URL may appear as a snippet in one lane and a hydrated article
  // in another. Never let discovery text shadow the body evidence.
  rawItems.sort((a, b) => Number(hasBody(b)) - Number(hasBody(a)) || String(b.articleText || '').length - String(a.articleText || '').length);
  for (const item of rawItems) {
    if (isExcludedSensationalSource(item)) continue;
    const isWiki = cleanText(item.source) === 'Wikipedia';
    const text = cleanText(`${item.title || ''}. ${item.articleText || item.snippet || ''}`);
    const key = claimKey(text);
    const urlKey = item.url || key;
    if (!key || seen.has(key) || seenUrls.has(urlKey) || text.length < 25) continue;
    seen.add(key);
    seenUrls.add(urlKey);
    const publishedAt = normalizeDate(item.articleDate || item.date || '');
    const relevance = typeof item.relevance === 'number' && item.relevance > 0 ? item.relevance : relevanceScore(item, research.query || '');
    const official = isOfficial(item);
    const confidence = claimConfidence(item);
    const tier = sourceTier(item);
    // A search hit that does not mention the topic is discovery noise, not
    // evidence. Keep reference pages only when their title/body is relevant.
    if (relevance <= 0) continue;
    claims.push({
      // Claim IDs must survive retries and app restarts. Sequential IDs make
      // claim_support and conflict records point at the wrong fact after a
      // new job is inserted ahead of an older one.
      id: `claim-${stableId(`${item.url}|${key}`).slice(5)}`,
      // First-party pages are the cleanest material for a deterministic
      // rescue draft, so retain more of their body than a normal search hit.
      text: text.slice(0, hasBody(item) ? 6000 : (isWiki ? 3200 : 1000)),
      title: item.title || '',
      url: item.url || '',
      source: item.source || 'Unknown source',
      publishedAt,
      retrievedAt: research.gatheredAt,
      confidence,
      official,
      synthetic: item.synthetic === true,
      sourceTier: tier,
      authorityTier: authorityTier(tier),
      sourceTierCode: authorityTier(tier),
      relevance,
      hasArticleBody: hasBody(item),
      fingerprint: claimKey(text),
      publisher: item.source || item.feedSource || 'Unknown source',
      trustedPublisher: isTrustedPublisher(item),
    });
  }

  claims.sort((a, b) => {
    const confidenceRank = { high: 3, medium: 2, low: 0 };
    if (confidenceRank[b.confidence] !== confidenceRank[a.confidence]) {
      return confidenceRank[b.confidence] - confidenceRank[a.confidence];
    }
    if (b.official !== a.official) return Number(b.official) - Number(a.official);
    if (b.relevance !== a.relevance) return b.relevance - a.relevance;
    if (a.publishedAt && b.publishedAt) return b.publishedAt.localeCompare(a.publishedAt);
    if (a.publishedAt) return -1;
    if (b.publishedAt) return 1;
    return 0;
  });

  const externalClaims = claims.filter((claim) => claim.url);
  const sourceKeys = new Set(externalClaims.map((claim) => hostname(claim.url) || cleanText(claim.source).toLowerCase()));
  const primaryClaims = claims.filter((claim) => claim.sourceTier === 'primary');
  const bodyClaims = claims.filter((claim) => claim.hasArticleBody && (claim.sourceTier === 'publisher' || claim.sourceTier === 'reference'));
  const credibleClaims = claims.filter((claim) => claim.confidence === 'high' || claim.confidence === 'medium');
  const atomicClaims = buildAtomicClaims(rawItems, claims);
  const conflicts = detectConflicts(claims);
  const sufficiencyReasons = [];
  if (externalClaims.length === 0 && claims.length === 0) {
    sufficiencyReasons.push('no attributable external claims');
  } else if (primaryClaims.length === 0 && bodyClaims.length === 0 && credibleClaims.length < 2) {
    sufficiencyReasons.push('insufficient verified primary or article body claims');
  }

  return {
    gatheredAt: research.gatheredAt || new Date().toISOString(),
    claims: claims.slice(0, 24),
    atomicClaims,
    conflicts,
    sourceCount: sourceKeys.size,
    externalClaims: externalClaims.length,
    primaryClaims: primaryClaims.length,
    sufficient: sufficiencyReasons.length === 0,
    sufficiencyReasons,
    latestClaimDate: claims
      .map((claim) => claim.publishedAt)
      .filter(Boolean)
      .sort()
      .pop() || '',
  };
}

function ledgerToPromptText(ledger) {
  const lines = [
    `Evidence gathered: ${ledger.gatheredAt}`,
    'Only use factual claims supported by the evidence ledger below.',
    'The date in [brackets] is when the source was published. Prefer the most recent dated claims for anything time-sensitive.',
    'Do not present old information as current news. If the freshest evidence is months old, qualify it explicitly.',
    'If the ledger does not support a detail, omit it or state uncertainty plainly.',
    'Official-source claims outrank search snippets. Headline-only claims may establish that an event was reported, but do not add details that the headline does not support.',
    'For historical dates, contemporaneous dated announcements and original records outrank later marketing retrospectives. Newer publication alone does not make a historical account more accurate. Resolve chronology conflicts from the original record or omit the disputed detail; never combine contradictory dates.',
    `Research quality gate: ${ledger.sufficient ? 'passed' : 'failed'}${ledger.sufficiencyReasons?.length ? ` (${ledger.sufficiencyReasons.join('; ')})` : ''}.`,
    '',
  ];
  if (ledger.coverage) {
    lines.push(`Research coverage score: ${Math.round((ledger.coverage.score || 0) * 100)} percent. Answered ${ledger.coverage.answered || 0}, partial ${ledger.coverage.partial || 0}, missing ${ledger.coverage.missing || 0}.`);
    const missing = (ledger.coverage.rows || []).filter((row) => row.status !== 'answered').map((row) => row.question).slice(0, 5);
    if (missing.length) lines.push(`Open research questions: ${missing.join(' | ')}`);
    lines.push('');
  }
  if (ledger.conflicts && ledger.conflicts.length) {
    lines.push('CONFLICTING CLAIMS — do not silently merge these; prefer the stronger/newer source or qualify the difference:');
    const claimById = new Map((ledger.claims || []).map((claim) => [claim.id, claim]));
    for (const conflict of ledger.conflicts.slice(0, 12)) {
      const left = claimById.get(conflict.claimId);
      const right = claimById.get(conflict.conflictingClaimId);
      lines.push(`${conflict.claimId} vs ${conflict.conflictingClaimId} | ${conflict.reason} | A: ${left?.text || ''} | B: ${right?.text || ''}`);
    }
    lines.push('');
  }
  for (const claim of ledger.claims) {
    const when = claim.publishedAt ? ` [published ${claim.publishedAt}]` : '';
    const authority = claim.official ? 'official source' : claim.trustedPublisher ? 'trusted publisher article' : claim.hasArticleBody ? 'unverified publisher article' : 'headline/snippet';
    const url = claim.url ? ` | source ${claim.url}` : '';
    const passages = (ledger.atomicClaims || [])
      .filter((fact) => fact.sourceClaimId === claim.id)
      // Long-form writing needs enough grounded detail to reach its requested
      // runtime without padding or invention. Five concise passages per source
      // remains a small context pack while giving the writer real material.
      .slice(0, 5)
      .map((fact) => fact.excerpt)
      .join(' / ');
    lines.push(`${claim.id} | confidence ${claim.confidence} | tier ${claim.sourceTier || 'unknown'} [${claim.authorityTier || authorityTier(claim.sourceTier)}] | ${authority}${when}${url} | ${claim.text}${passages ? ` | supporting passages: ${passages}` : ''}`);
  }
  return lines.join('\n');
}

module.exports = { buildEvidenceLedger, ledgerToPromptText, relevanceScore, isOfficial };
