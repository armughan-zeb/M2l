const UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36';

// Bing soft rate-limits this egress IP: after several rapid searches it
// keeps answering HTTP 200 but serves decoy results — for "D B Cooper"
// it returned ten letter-"D" pages ("D - Wikipedia", "Letter D ...") with
// zero query relevance, and real results came back after a 45s pause
// (verified live 2026-11-09). Two defenses:
//   1. Pace Bing calls: at least 5s between fetches, chained so parallel
//      callers serialize instead of bursting.
//   2. Relevance check: if none of the parsed results contains any query
//      token, the page is a decoy — throw a retryable error (with a long
//      Retry-After hint) instead of returning junk the researcher would
//      silently drop as irrelevant.
let lastBingAt = 0;
let bingQueue = Promise.resolve();
const MIN_BING_GAP_MS = 5000;
function throttleBing() {
  const run = bingQueue.then(async () => {
    const wait = MIN_BING_GAP_MS - (Date.now() - lastBingAt);
    if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
    lastBingAt = Date.now();
  });
  bingQueue = run.catch(() => {});
  return run;
}

const BING_STOPWORDS = new Set([
  'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can', 'her', 'was', 'one',
  'our', 'out', 'day', 'get', 'has', 'him', 'his', 'how', 'its', 'may', 'new', 'now',
  'old', 'see', 'two', 'way', 'who', 'boy', 'did', 'let', 'say', 'she', 'too', 'use',
  'with', 'this', 'that', 'from', 'have', 'had', 'said', 'will', 'they', 'their',
  'them', 'been', 'than', 'what', 'when', 'your', 'into', 'more', 'some', 'time',
  'over', 'just', 'also', 'after', 'year', 'years', 'first', 'last', 'long', 'great',
  'still', 'most', 'back', 'much', 'such', 'best', 'make', 'made', 'here', 'where',
  'about', 'would', 'could', 'should', 'these', 'those', 'every', 'being', 'because',
  'while', 'other', 'around', 'between', 'through', 'without', 'against', 'another',
]);

function queryTokens(query) {
  return String(query || '')
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .split(/\s+/)
    .filter((w) => w.length > 2 && !BING_STOPWORDS.has(w));
}

function resultsMatchQuery(results, query) {
  const tokens = queryTokens(query);
  if (!tokens.length) return true;
  return results.some((r) => {
    const haystack = `${r.title || ''} ${r.snippet || ''} ${r.url || ''}`.toLowerCase();
    return tokens.some((t) => haystack.includes(t));
  });
}

function clean(value) {
  return String(value || '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, ' ')
    .replace(/&hellip;/g, '...')
    .replace(/&#0183;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function decodeBingUrl(href) {
  if (!href) return '';
  const um = href.match(/[?&]u=a1([a-zA-Z0-9+/=_-]+)/);
  if (um) {
    try {
      const raw = um[1].replace(/-/g, '+').replace(/_/g, '/');
      const padded = raw + '='.repeat((4 - (raw.length % 4)) % 4);
      const decoded = Buffer.from(padded, 'base64').toString('utf8');
      if (/^https?:\/\//i.test(decoded)) return decoded;
    } catch (_) {}
  }
  // Fallback: extract from the cite-style marker inside the redirect URL.
  const fm = href.match(/&u=a1([a-zA-Z0-9+/=_-]+)/);
  if (fm) {
    try {
      const raw = fm[1].replace(/-/g, '+').replace(/_/g, '/');
      const padded = raw + '='.repeat((4 - (raw.length % 4)) % 4);
      const decoded = Buffer.from(padded, 'base64').toString('utf8');
      if (/^https?:\/\//i.test(decoded)) return decoded;
    } catch (_) {}
  }
  return '';
}

function citeToUrl(cite) {
  if (!cite) return '';
  // <cite> renders like:  https://www.site.com › path › page
  return clean(cite).replace(/›/g, '/').replace(/\s+/g, '');
}

function parseDateInSnippet(snippet) {
  const m = snippet.match(/^([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})/);
  if (m) {
    const parsed = Date.parse(m[1]);
    if (!Number.isNaN(parsed)) return new Date(parsed).toISOString().slice(0, 10);
  }
  const ago = snippet.match(/(\d+)\s+(day|days|hour|hours|week|weeks|month|months)\s+ago/i);
  if (ago) {
    const n = parseInt(ago[1], 10);
    const unit = ago[2].toLowerCase();
    let ms = 0;
    if (unit.startsWith('day')) ms = n * 86400000;
    else if (unit.startsWith('hour')) ms = n * 3600000;
    else if (unit.startsWith('week')) ms = n * 7 * 86400000;
    else if (unit.startsWith('month')) ms = n * 30 * 86400000;
    if (ms) return new Date(Date.now() - ms).toISOString().slice(0, 10);
  }
  return '';
}

async function bingSearch(query, { limit = 8, timeoutMs = 12000 } = {}) {
  const url = `https://www.bing.com/search?q=${encodeURIComponent(query)}&count=${Math.min(limit + 4, 20)}&setlang=en`;
  await throttleBing();
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      headers: {
        'User-Agent': UA,
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'en-US,en;q=0.9',
      },
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`Bing HTTP ${res.status}`);
    const html = await res.text();
    const results = parseBingResults(html, limit);
    // An HTTP 200 page with zero parseable results is a markup change or a
    // bot challenge, not a genuine "no results" — surface it so the
    // resilience layer retries and the research log names the failure.
    if (!results.length) {
      const err = new Error('Bing returned no parseable results (markup changed or bot challenge)');
      err.code = 'NO_PARSEABLE_RESULTS';
      throw err;
    }
    // Decoy page: parseable, but nothing in it is about the query (Bing
    // serves letter-"D" pages for "D B Cooper" when rate-limiting this
    // IP). Returning junk wastes the research pass; retry after a pause.
    if (!resultsMatchQuery(results, query)) {
      const err = new Error('Bing returned no results about the query (decoy/rate-limited page)');
      err.code = 'BING_LOW_RELEVANCE';
      err.retryAfterMs = 15000;
      throw err;
    }
    return results;
  } finally {
    clearTimeout(t);
  }
}

function parseBingResults(html, limit) {
  const results = [];
  const algoRe = /<li class="b_algo"[\s\S]*?<\/li>/g;
  let m;
  while ((m = algoRe.exec(html)) && results.length < limit) {
    const block = m[0];
    const h2a = block.match(/<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/);
    const title = h2a ? clean(h2a[2]) : '';
    if (!title) continue;
    const href = (h2a && h2a[1]) || '';
    let url = decodeBingUrl(href);
    if (!url && /^https?:\/\//i.test(href) && !/^https?:\/\/[^/]*bing\.com\//i.test(href)) {
      url = href.replace(/&amp;/gi, '&');
    }
    if (!url) {
      const cite = (block.match(/<cite[^>]*>([\s\S]*?)<\/cite>/) || [])[1] || '';
      url = citeToUrl(cite);
    }
    if (!url) continue;
    const snippet = (block.match(/<p[^>]*>([\s\S]*?)<\/p>/) || [])[1] || '';
    const snipText = clean(snippet);
    const date = parseDateInSnippet(snipText);
    results.push({ title, snippet: snipText.slice(0, 500), url, source: 'Bing', category: 'web', date });
  }
  return results;
}

module.exports = { bingSearch };
