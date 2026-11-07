const UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36';

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

async function bingSearch(query, { limit = 8, timeoutMs = 8000 } = {}) {
  const url = `https://www.bing.com/search?q=${encodeURIComponent(query)}&count=${Math.min(limit + 4, 20)}&setlang=en`;
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
    return parseBingResults(html, limit);
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
