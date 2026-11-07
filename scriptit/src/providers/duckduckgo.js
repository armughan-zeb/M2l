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

async function ddgSearch(query, { limit = 8, timeoutMs = 8000 } = {}) {
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const url = `https://html.duckduckgo.com/html/?q=${encodeURIComponent(query)}&kl=us-en`;
    const res = await fetch(url, {
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
      signal: controller.signal,
    }).catch(() => null);

    if (res && res.ok) {
      const html = await res.text();
      const parsed = parseDdgResults(html, limit);
      if (parsed.length) return parsed;
    }

    // Fallback 1: DuckDuckGo Lite HTML
    const liteUrl = `https://lite.duckduckgo.com/lite/?q=${encodeURIComponent(query)}`;
    const liteRes = await fetch(liteUrl, {
      headers: { 'User-Agent': DEFAULT_UA },
      signal: controller.signal
    }).catch(() => null);
    if (liteRes && liteRes.ok) {
      const liteHtml = await liteRes.text();
      const liteParsed = parseDdgLiteResults(liteHtml, limit);
      if (liteParsed.length) return liteParsed;
    }

    // Fallback 2: DuckDuckGo Instant Answer API
    const apiUrl = `https://api.duckduckgo.com/?q=${encodeURIComponent(query)}&format=json&no_html=1&skip_disambig=1`;
    const apiRes = await fetch(apiUrl, {
      headers: { 'User-Agent': DEFAULT_UA },
      signal: controller.signal
    }).catch(() => null);
    if (apiRes && apiRes.ok) {
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
    }

    return [];
  } catch (_) {
    return [];
  } finally {
    clearTimeout(t);
  }
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

module.exports = { ddgSearch };
