const DEFAULT_UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36';

function decodeHtmlEntities(s) {
  if (!s) return '';
  return s
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&apos;/g, "'")
    .replace(/&#x27;/gi, "'")
    .replace(/&#x2F;/gi, '/')
    .replace(/&hellip;/g, '...')
    .replace(/\s+/g, ' ')
    .trim();
}

function stripHtml(s) {
  return decodeHtmlEntities(s);
}

function botChallengeError(stage) {
  // DuckDuckGo serves its "anomaly" interstitial (HTTP 202) instead of
  // results when the egress IP is gated: the page contains no result
  // links at all, so no parser update can fix it — and the Lite and
  // Instant Answer endpoints are gated on the same IP. Fail fast with a
  // distinct, non-retryable code instead of burning the retry budget on
  // the remaining stages (verified live 2026-11-09: html=202 anomaly,
  // lite=202 anomaly, api=test stub).
  const err = new Error(`DuckDuckGo bot challenge on ${stage} (anomaly interstitial; egress IP gated)`);
  err.code = 'BOT_CHALLENGE';
  return err;
}

function isChallengePage(html, status) {
  // The interstitial is unmistakable: HTTP 202 plus the anomaly
  // detection bundle repeated through the page, and zero result links.
  // A genuine results page is HTTP 200 and never mentions "anomaly"
  // without also containing result links, so the no-results condition
  // is what guards against false positives on anomaly-related queries.
  if (status === 202) return true;
  const text = String(html || '');
  return text.length > 0 && (text.match(/anomaly/gi) || []).length >= 2
    && !/result__a|result-link/i.test(text);
}

async function ddgSearch(query, { limit = 8, timeoutMs = 12000 } = {}) {
  // One shared deadline for all three stages: the old code gave every fetch
  // the same AbortController, so a slow first stage starved the fallbacks,
  // and every failure path returned [] — the caller (runProvider) saw a
  // "success" and never retried, and research.errors stayed empty while the
  // provider was actually dead. Now each stage gets the time that is left,
  // and total failure THROWS a classified error so the resilience layer can
  // retry it and the research log can name it. A genuine zero-result query
  // also throws here; that is deliberate — the researcher treats a provider
  // error as non-fatal and keeps the other providers' results.
  const deadline = Date.now() + Math.max(2000, timeoutMs);
  const remainingMs = () => Math.max(1500, deadline - Date.now());
  const failures = [];
  async function fetchStage(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), remainingMs());
    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } finally {
      clearTimeout(timer);
    }
  }
  const describe = (err) => `${err && err.name ? err.name : 'Error'}: ${err && err.message ? err.message : String(err)}`;

  // Stage 1: HTML endpoint
  try {
    const url = `https://html.duckduckgo.com/html/?q=${encodeURIComponent(query)}&kl=us-en`;
    const res = await fetchStage(url, {
      method: 'POST',
      headers: {
        'User-Agent': DEFAULT_UA,
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'en-US,en;q=0.9',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': 'https://html.duckduckgo.com',
        'Referer': 'https://html.duckduckgo.com/',
      },
      body: `q=${encodeURIComponent(query)}&kl=us-en`,
    });
    if (!res.ok) throw new Error(`DuckDuckGo HTML HTTP ${res.status}`);
    const htmlText = await res.text();
    if (isChallengePage(htmlText, res.status)) throw botChallengeError('html');
    const parsed = parseDdgResults(htmlText, limit);
    if (parsed.length) return parsed;
    failures.push('html: HTTP 200 but no parseable results (markup changed or bot challenge)');
  } catch (err) {
    if (err && err.code === 'BOT_CHALLENGE') throw err;
    failures.push(`html: ${describe(err)}`);
  }

  // Fallback 1: DuckDuckGo Lite HTML
  try {
    const liteUrl = `https://lite.duckduckgo.com/lite/?q=${encodeURIComponent(query)}`;
    const liteRes = await fetchStage(liteUrl, { headers: { 'User-Agent': DEFAULT_UA } });
    if (!liteRes.ok) throw new Error(`DuckDuckGo Lite HTTP ${liteRes.status}`);
    const liteText = await liteRes.text();
    if (isChallengePage(liteText, liteRes.status)) throw botChallengeError('lite');
    const liteParsed = parseDdgLiteResults(liteText, limit);
    if (liteParsed.length) return liteParsed;
    failures.push('lite: HTTP 200 but no parseable results');
  } catch (err) {
    if (err && err.code === 'BOT_CHALLENGE') throw err;
    failures.push(`lite: ${describe(err)}`);
  }

  // Fallback 2: DuckDuckGo Instant Answer API
  try {
    const apiUrl = `https://api.duckduckgo.com/?q=${encodeURIComponent(query)}&format=json&no_html=1&skip_disambig=1`;
    const apiRes = await fetchStage(apiUrl, { headers: { 'User-Agent': DEFAULT_UA } });
    if (!apiRes.ok) throw new Error(`DuckDuckGo API HTTP ${apiRes.status}`);
    const data = await apiRes.json().catch(() => null);
    if (data) {
      const out = [];
      if (data.AbstractText) {
        out.push({ title: data.Heading || query, snippet: data.AbstractText, url: data.AbstractURL || '', source: 'duckduckgo' });
      }
      if (Array.isArray(data.RelatedTopics)) {
        for (const topic of data.RelatedTopics.slice(0, limit)) {
          if (topic.Text) out.push({ title: topic.Text.slice(0, 80), snippet: topic.Text, url: topic.FirstURL || '', source: 'duckduckgo' });
        }
      }
      if (out.length) return out;
    }
    failures.push('instant-answer: HTTP 200 but no usable topics');
  } catch (err) {
    failures.push(`instant-answer: ${describe(err)}`);
  }

  const emptyError = new Error(`DuckDuckGo search failed (${failures.join('; ')})`);
  emptyError.code = 'NO_PARSEABLE_RESULTS';
  throw emptyError;
}

function parseDdgLiteResults(html, limit) {
  const results = [];
  const re = /<a[^>]+class="result-link"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>[\s\S]*?<td[^>]+class="result-snippet"[^>]*>([\s\S]*?)<\/td>/gi;
  let m;
  while ((m = re.exec(html)) && results.length < limit) {
    const title = stripHtml(m[2]);
    const snippet = stripHtml(m[3]);
    if (title) results.push({ title, snippet, url: resolveDdgUrl(m[1]), source: 'duckduckgo' });
  }
  return results;
}

function parseDdgResults(html, limit) {
  const results = [];
  const blockRe = /<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>[\s\S]*?<a[^>]+class="result__snippet"[^>]*>([\s\S]*?)<\/a>/g;
  let m;
  while ((m = blockRe.exec(html)) && results.length < limit) {
    const title = stripHtml(m[2]);
    const snippet = stripHtml(m[3]);
    if (title) results.push({ title, snippet, url: resolveDdgUrl(m[1]), source: 'duckduckgo' });
  }
  if (results.length === 0) {
    const fallback = /<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/g;
    let m2;
    while ((m2 = fallback.exec(html)) && results.length < limit) {
      const title = stripHtml(m2[2]);
      if (title) results.push({ title, snippet: '', url: resolveDdgUrl(m2[1]), source: 'duckduckgo' });
    }
  }
  return results;
}

function resolveDdgUrl(value) {
  try {
    const url = new URL(decodeHtmlEntities(value), 'https://duckduckgo.com');
    return url.searchParams.get('uddg') || url.href;
  } catch (_) {
    return '';
  }
}

module.exports = { ddgSearch, isChallengePage };
