const BROWSER_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';

function mapRssItem(it) {
  const title = it.title || '';
  const link = it.link || it.guid || '';
  return {
    title,
    snippet: (it.contentSnippet || it.content || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 500),
    url: link,
    subreddit: /\/r\/([^/]+)/i.exec(link || '')?.[1] || '',
    score: 0,
    comments: 0,
    date: it.isoDate || it.pubDate || '',
    source: `r/${(/\/r\/([^/]+)/i.exec(link || '')?.[1] || 'Reddit')}`,
  };
}

async function redditRssSearch(query, { limit = 8, sort = 'new', timeoutMs = 8000 } = {}) {
  const Parser = require('rss-parser');
  const url = `https://old.reddit.com/search.rss?q=${encodeURIComponent(query)}&sort=${sort}&limit=${Math.min(limit, 25)}`;
  // Per-call parser so the caller's budget is real (the module-level
  // parser's fixed 8000ms ignored it). old.reddit is also gated from
  // this egress IP (socket hang-up, 2026-11-09), so this usually fails
  // fast; it must never eat the JSON attempt's remaining budget.
  const feedParser = new Parser({ timeout: Math.max(1000, timeoutMs) });
  const feed = await feedParser.parseURL(url);
  return (feed.items || []).slice(0, limit).map(mapRssItem);
}

async function redditJsonSearch(query, { limit = 8, sort = 'relevance', timeoutMs = 8000 } = {}) {
  const url = `https://www.reddit.com/search.json?q=${encodeURIComponent(query)}&limit=${Math.min(limit, 25)}&sort=${sort}&restrict_sr=&t=`;
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      headers: {
        'User-Agent': BROWSER_UA,
        'Accept': 'application/json, text/html',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://www.reddit.com/',
      },
      signal: controller.signal,
    });
    if (res.status === 403 || res.status === 401) {
      return { rateLimited: false, blocked: true, results: [], error: `Reddit ${res.status} (auth required)` };
    }
    if (res.status === 429) {
      return { rateLimited: true, results: [], error: 'Reddit rate limited' };
    }
    if (!res.ok) {
      throw new Error(`Reddit HTTP ${res.status}`);
    }
    const ct = res.headers.get('content-type') || '';
    if (!ct.includes('json')) {
      return { rateLimited: false, blocked: true, results: [], error: 'Reddit returned non-JSON (likely anti-bot)' };
    }
    const json = await res.json();
    const children = (json && json.data && json.data.children) || [];
    const results = children
      .map((c) => c.data)
      .filter(Boolean)
      .map((d) => ({
        title: d.title || '',
        snippet: d.selftext ? d.selftext.slice(0, 500) : '',
        url: d.url_overridden_by_dest || `https://www.reddit.com${d.permalink}`,
        subreddit: d.subreddit,
        score: d.score || 0,
        comments: d.num_comments || 0,
        date: new Date((d.created_utc || 0) * 1000).toISOString(),
        source: `r/${d.subreddit}`,
      }));
    return { rateLimited: false, blocked: false, results };
  } catch (err) {
    return { rateLimited: false, blocked: false, results: [], error: err.message };
  } finally {
    clearTimeout(t);
  }
}

async function redditSearch(query, { limit = 8, sort = 'relevance', timeoutMs = 8000, rssSearchFn = redditRssSearch } = {}) {
  // Reddit needs auth from this egress IP: the JSON API answers 403 and
  // the old.reddit RSS fallback hangs up. That is a fail-soft lane, not
  // a fatal error — return the block, never throw, and never trip a
  // circuit for the other providers. The RSS fallback only runs while
  // budget remains: the old code let it consume a second full timeout
  // after a slow JSON attempt, so the researcher's outer timeout killed
  // the whole provider call as PROVIDER_TIMEOUT instead of the honest
  // "Reddit blocked" record.
  const started = Date.now();
  const json = await redditJsonSearch(query, { limit, sort, timeoutMs });
  if (json.results && json.results.length) return json;

  const elapsed = Date.now() - started;
  if (elapsed > timeoutMs * 0.6) return json;
  // JSON API blocked / empty → fall back to old.reddit RSS, which is public and works.
  try {
    const rssResults = await rssSearchFn(query, { limit, sort: 'new', timeoutMs: Math.max(1000, timeoutMs - elapsed) });
    if (rssResults.length) {
      return {
        rateLimited: json.rateLimited,
        blocked: false,
        results: rssResults,
        error: json.blocked ? 'JSON blocked; used RSS fallback' : '',
        viaRss: true,
      };
    }
  } catch (_) {}
  return json;
}

module.exports = { redditSearch, redditJsonSearch, redditRssSearch };
