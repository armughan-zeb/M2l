const WIKI_API = 'https://en.wikipedia.org/w/api.php';
const WIKI_REST = 'https://en.wikipedia.org/api/rest_v1/page/summary';
const { normalizeTopic } = require('../research/topicNormalizer');

const UA = 'ScriptIT/1.0 (research agent; contact: user@example.com)';

function clean(value) {
  return String(value || '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

const STOPWORDS = new Set([
  'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can', 'her', 'was', 'one', 'our',
  'out', 'day', 'get', 'has', 'him', 'his', 'how', 'its', 'may', 'new', 'now', 'old', 'see',
  'two', 'way', 'who', 'boy', 'did', 'let', 'say', 'she', 'too', 'use', 'with', 'this', 'that',
  'from', 'have', 'had', 'said', 'will', 'they', 'their', 'them', 'been', 'than', 'what', 'when',
  'your', 'into', 'more', 'some', 'time', 'over', 'just', 'also', 'after', 'year', 'years', 'first',
  'last', 'long', 'great', 'still', 'most', 'back', 'much', 'such', 'best', 'make', 'made', 'here',
  'where', 'about', 'would', 'could', 'should', 'these', 'those', 'every', 'being', 'because', 'while',
  'other', 'around', 'between', 'through', 'without', 'against', 'another',
]);

function queryTokens(query) {
  return String(query || '')
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .split(/\s+/)
    .filter((w) => w.length > 2 && !STOPWORDS.has(w));
}

const SYNONYMS = {
  gta: ['grand theft auto', 'gta'],
  ai: ['artificial intelligence', 'ai'],
  nasa: ['nasa'],
  covid: ['covid', 'covid-19', 'coronavirus'],
  ww2: ['world war ii', 'second world war'],
  'us': ['united states', 'america'],
};

function isRelevant(title, extract, query) {
  const tokens = queryTokens(query);
  if (!tokens.length) return true;
  const haystack = `${title} ${extract}`.toLowerCase();
  let hits = 0;
  for (const t of tokens) {
    if (haystack.includes(t)) { hits += 1; continue; }
    const syn = SYNONYMS[t];
    if (syn && syn.some((s) => haystack.includes(s))) hits += 1;
  }
  // Single-token queries (e.g. "GTA") are common and can match a broad page;
  // accept them when the title itself contains the token or a synonym.
  if (tokens.length === 1) {
    const t = tokens[0];
    const titleLower = title.toLowerCase();
    const syn = SYNONYMS[t];
    return titleLower.includes(t) || (syn && syn.some((s) => titleLower.includes(s)));
  }
  return hits >= Math.max(1, Math.ceil(tokens.length * 0.5));
}

function isLikelyEnglish(value) {
  const text = String(value || '');
  const letters = text.match(/[A-Za-z\u00C0-\u024F\u1E00-\u1EFF]/g) || [];
  const cyrillic = text.match(/[\u0400-\u04FF]/g) || [];
  return letters.length === 0 || cyrillic.length / Math.max(1, letters.length + cyrillic.length) < 0.12;
}

function fetchWithTimeout(url, options = {}, timeoutMs = 8000) {
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeoutMs);
  return fetch(url, { ...options, signal: controller.signal }).finally(() => clearTimeout(t));
}

function wikiTitleToUrl(title) {
  return `https://en.wikipedia.org/wiki/${encodeURIComponent(String(title).replace(/ /g, '_'))}`;
}

async function fetchSummary(title) {
  const url = `${WIKI_REST}/${encodeURIComponent(String(title).replace(/ /g, '_'))}`;
  const res = await fetchWithTimeout(url, { headers: { 'User-Agent': UA, Accept: 'application/json' } });
  if (!res.ok) return null;
  const json = await res.json();
  const extract = clean(json.extract || '');
  if (extract.length < 120) return null;
  return {
    title: json.title || title,
    description: clean(json.description || ''),
    extract: extract.slice(0, 2400),
    url: json.content_urls && json.content_urls.desktop ? json.content_urls.desktop.page : wikiTitleToUrl(title),
    image: json.thumbnail ? json.thumbnail.source : '',
    timestamp: json.extract ? json.extract_timestamp || '' : '',
  };
}

async function searchTitles(query, limit = 5) {
  const params = new URLSearchParams({
    action: 'query',
    list: 'search',
    srsearch: query,
    format: 'json',
    srlimit: String(limit),
    srprop: 'snippet',
    utf8: '1',
  });
  const url = `${WIKI_API}?${params}`;
  const res = await fetchWithTimeout(url, { headers: { 'User-Agent': UA, Accept: 'application/json' } });
  if (!res.ok) throw new Error(`Wikipedia search HTTP ${res.status}`);
  const json = await res.json();
  return (json.query && json.query.search) || [];
}

async function wikipediaSearch(query, { limit = 5, timeoutMs = 8000 } = {}) {
  const normalized = normalizeTopic(query).canonical;

  // Search engines often rank a franchise or a newer related title above the
  // requested entity. Try the canonical page first so exact topics do not get
  // silently replaced by a merely related result.
  const candidatePhrases = [
    normalized,
    ...normalized.split(/\s+(?:and|on|in|of|around|about|from|for|at|during|vs\.?)\s+|[:,\-–—]/i)
      .map((s) => s.replace(/^(?:the|a|an)\s+/i, '').replace(/['’]s\b/gi, '').trim())
      .filter((s) => s.length > 3 && s !== normalized),
  ];

  let exact = null;
  for (const phrase of candidatePhrases) {
    exact = await fetchSummary(phrase).catch(() => null);
    if (exact && (isRelevant(exact.title, exact.extract, normalized) || isRelevant(exact.title, exact.extract, phrase)) && isLikelyEnglish(`${exact.title} ${exact.extract}`)) {
      break;
    }
    exact = null;
  }
  if (exact) {
    const exactResult = {
      title: exact.title,
      snippet: exact.extract,
      url: exact.url,
      source: 'Wikipedia',
      category: 'encyclopedia',
      date: exact.timestamp || '',
      searchSnippet: '',
      description: exact.description,
    };
    const remaining = Math.max(0, limit - 1);
    if (!remaining) return [exactResult];
    const searchResults = await searchTitles(exact.title || normalized, Math.max(remaining + 2, 3)).catch(() => []);
    const related = searchResults
      .filter((result) => result.title !== exact.title)
      .slice(0, Math.min(remaining, 4));
    const summaries = await Promise.all(related.map((result) => fetchSummary(result.title).catch(() => null)));
    const byTitle = new Map(related.map((result) => [result.title, result]));
    const relatedResults = [];
    for (let index = 0; index < related.length; index += 1) {
      const summary = summaries[index];
      if (!summary || (!isRelevant(summary.title, summary.extract, normalized) && !isRelevant(summary.title, summary.extract, exact.title)) || !isLikelyEnglish(`${summary.title} ${summary.extract}`)) continue;
      relatedResults.push({
        title: summary.title,
        snippet: summary.extract,
        url: summary.url,
        source: 'Wikipedia',
        category: 'encyclopedia',
        date: summary.timestamp || '',
        searchSnippet: clean(byTitle.get(summary.title)?.snippet || ''),
        description: summary.description,
      });
    }
    return [exactResult, ...relatedResults].slice(0, limit);
  }

  let searchResults = await searchTitles(normalized, Math.max(limit, 3)).catch(() => []);
  const tokens = queryTokens(normalized);

  async function evaluateTitles(titles, sourceSearch = searchResults) {
    const top = titles.slice(0, Math.min(limit, 5));
    const summaries = await Promise.all(top.map((t) => fetchSummary(t).catch(() => null)));
    const byTitle = new Map((sourceSearch || []).map((r) => [r.title, r]));
    const matched = [];
    for (let index = 0; index < top.length; index += 1) {
      const summary = summaries[index];
      if (!summary || !isRelevant(summary.title, summary.extract, normalized) || !isLikelyEnglish(`${summary.title} ${summary.extract}`)) continue;
      matched.push({
        title: summary.title,
        snippet: summary.extract,
        url: summary.url,
        source: 'Wikipedia',
        category: 'encyclopedia',
        date: summary.timestamp || '',
        searchSnippet: clean(byTitle.get(summary.title)?.snippet || ''),
        description: summary.description,
      });
    }
    return matched;
  }

  let results = await evaluateTitles(searchResults.map((r) => r.title), searchResults);

  if (!results.length) {
    const fallbackQueries = [
      ...candidatePhrases.slice(1),
      tokens.length >= 2 ? tokens.slice(0, 3).join(' ') : (tokens[0] || ''),
    ].filter(Boolean);

    for (const fq of fallbackQueries) {
      const fbSearch = await searchTitles(fq, Math.max(limit, 3)).catch(() => []);
      if (fbSearch.length) {
        results = await evaluateTitles(fbSearch.map((r) => r.title), fbSearch);
        if (results.length) break;
      }
    }
  }

  return results.slice(0, limit);
}

async function wikipediaExtract(title, { maxChars = 14000, timeoutMs = 12000 } = {}) {
  if (!title || !title.trim()) return null;
  const params = new URLSearchParams({
    action: 'query',
    prop: 'extracts',
    explaintext: '1',
    exsectionformat: 'plain',
    redirects: '1',
    titles: title,
    format: 'json',
    origin: '*',
  });
  const res = await fetchWithTimeout(`${WIKI_API}?${params}`, { headers: { 'User-Agent': UA, Accept: 'application/json' } }, timeoutMs);
  if (!res.ok) return null;
  const json = await res.json();
  const pages = (json.query && json.query.pages) || {};
  const page = pages[Object.keys(pages)[0]];
  if (!page || page.missing) return null;
  const text = clean(page.extract);
  const clipped = text.slice(0, maxChars);
  if (clipped.length < 180) return null;
  return {
    title: page.title || title,
    text: clipped,
    url: wikiTitleToUrl(page.title || title),
    source: 'Wikipedia',
    category: 'encyclopedia',
    date: '',
  };
}

// Fetches full plaintext articles for the top matching topics so the writer gets
// dense, current material (a summary alone is too thin for biography/gaming/etc.).
async function wikipediaDeep(query, { limit = 2, timeoutMs = 12000 } = {}) {
  const search = await wikipediaSearch(query, { limit: Math.max(limit + 1, 4), timeoutMs });
  if (!search.length) return [];
  const chosen = search.slice(0, limit);
  const deep = await Promise.all(
    chosen.map(async (hit) => {
      const article = await wikipediaExtract(hit.title, { timeoutMs }).catch(() => null);
      if (!article) return hit;
      return { ...hit, articleText: article.text, hasArticleBody: Boolean(article.text), isWiki: true };
    }),
  );
  return deep;
}

module.exports = { wikipediaSearch, wikipediaExtract, wikipediaDeep };
