const BROWSER_UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36';

function decodeEntities(value) {
  return String(value || '')
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/g, "'")
    .replace(/&#(x[0-9a-f]+|\d+);/gi, (match, code) => {
      const value = code[0].toLowerCase() === 'x' ? parseInt(code.slice(1), 16) : Number(code);
      return value > 0 && value <= 0x10ffff ? String.fromCodePoint(value) : match;
    })
    .replace(/&hellip;/gi, '...')
    .replace(/\s+/g, ' ')
    .trim();
}

function clean(value) {
  return String(value || '')
    .replace(/\s+/g, ' ')
    .trim();
}

function parseDate(value) {
  if (!value) return '';
  const text = clean(String(value).replace(/&nbsp;/gi, ' '));
  const iso = text.match(/(\d{4}-\d{2}-\d{2})/);
  if (iso) return iso[1];
  const parsed = Date.parse(text);
  if (!Number.isNaN(parsed)) return new Date(parsed).toISOString().slice(0, 10);
  return '';
}

function extractDate(html) {
  const patterns = [
    /<meta[^>]+(?:property|name|itemprop)=["'](?:article:published_time|article:modified_time|datepublished|publishdate|date|dc\.date|parsely-pub-date)[^>]+content=["']([^"']+)["']/gi,
    /<meta[^>]+content=["']([^"']+)["'][^>]+(?:property|name|itemprop)=["'](?:article:published_time|datepublished|publishdate|parsely-pub-date)[^>]*>/gi,
    /<time[^>]+datetime=["']([^"']+)["'][^>]*>/gi,
    /"datePublished"\s*:\s*"([^"]+)"/g,
    /"dateModified"\s*:\s*"([^"]+)"/g,
    /"uploadDate"\s*:\s*"([^"]+)"/g,
    /"publishDate"\s*:\s*"([^"]+)"/g,
  ];
  for (const pattern of patterns) {
    const match = pattern.exec(html);
    if (match && match[1]) {
      const date = parseDate(match[1]);
      if (date) return date;
    }
  }
  return '';
}

const BODY_CLASSES = [
  'article-body', 'article-body-copy', 'article-content', 'article__body', 'article__content',
  'post-content', 'post-body', 'entry-content', 'entry-body', 'story-body', 'story-content',
  'blog-content', 'c-article-body', 'single-content', 'page-content', 'node-content',
  'rich-text', 'article-text', 'body-content', 'main-content', 'content__body', 'copy',
  'mw-parser-output', 'mw-body-content', 'post__content', 'c-entry-content', 'entry__content',
  'article-full', 'article__text', 'story__body', 'article-content__content', 'prose',
];

const SKIP_DOMAINS = new Set([
  'store.rockstargames.com', 'store.epicgames.com', 'apps.microsoft.com', 'microsoft.com',
  'xbox.com', 'www.xbox.com', 'playstation.com', 'store.playstation.com',
  'gta.fandom.com', 'r.bing.com', 'www.bing.com', 'duckduckgo.com', 'linkedin.com',
  'store.steampowered.com', 'amazon.com', 'www.amazon.com', 'bestbuy.com', 'walmart.com',
]);

function skipDomain(url) {
  try {
    return SKIP_DOMAINS.has(new URL(url).hostname.toLowerCase());
  } catch (_) {
    return true;
  }
}

function metaRefreshUrl(html) {
  const match = html.match(/<meta[^>]+http-equiv=["']?refresh["']?[^>]+content=["'][^"']*url=([^"']+)["']/i);
  if (match) {
    try {
      return new URL(match[1].trim()).href;
    } catch (_) {}
  }
  return '';
}

function bodySelectors(html) {
  const selectors = [], stack = [];
  const cleaned = html.replace(/<script[\s\S]*?<\/script>|<style[\s\S]*?<\/style>/gi, '');
  const tokens = /<\/?([a-z][\w:-]*)\b[^>]*>/gi;
  for (const match of cleaned.matchAll(tokens)) {
    const tag = match[1].toLowerCase(), closing = /^<\//.test(match[0]);
    if (closing) {
      const index = stack.map(entry => entry.tag).lastIndexOf(tag);
      if (index < 0) continue;
      const opened = stack[index];
      stack.length = index;
      if (opened.target) selectors.push(cleaned.slice(opened.start, match.index + match[0].length));
    } else if (!/^(?:area|base|br|col|embed|hr|img|input|link|meta|param|source|track|wbr)$/.test(tag) && !/\/>$/.test(match[0])) {
      const attrs = match[0];
      const target = ['article', 'main'].includes(tag) || /itemprop=["']articleBody["']/i.test(attrs)
        || BODY_CLASSES.some(cls => new RegExp(`(?:class|id)=["'][^"']*\\b${cls}\\b[^"']*["']`, 'i').test(attrs));
      stack.push({ tag, start: match.index, target });
    }
  }
  return selectors;
}

function textFromContainer(container) {
  const kept = container
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<header[\s\S]*?<\/header>/gi, ' ')
    .replace(/<nav[\s\S]*?<\/nav>/gi, ' ')
    .replace(/<footer[\s\S]*?<\/footer>/gi, ' ')
    .replace(/<aside[\s\S]*?<\/aside>/gi, ' ')
    .replace(/<form[\s\S]*?<\/form>/gi, ' ')
    .replace(/<button[\s\S]*?<\/button>/gi, ' ');
  const headings = [...kept.matchAll(/<h[1-4]\b[^>]*>([\s\S]*?)<\/h[1-4]>/gi)]
    .map((m) => decodeEntities(m[1]))
    .filter((t) => t.length > 20);
  const paragraphs = [...kept.matchAll(/<p\b[^>]*>([\s\S]*?)<\/p>/gi)]
    .map((m) => decodeEntities(m[1]))
    .filter((t) => t.length > 60);
  return [...headings, ...paragraphs].join(' ');
}

function extractArticle(html) {
  const date = extractDate(html);

  // Prefer structured data (articleBody is usually the cleanest, most current text).
  const ldBlocks = [...html.matchAll(/<script[^>]+type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi)];
  let articleBody = '';
  for (const block of ldBlocks) {
    try {
      const parsed = JSON.parse(clean(block[1]));
      const collect = (node) => {
        if (!node) return '';
        if (Array.isArray(node)) return node.map(collect).join(' ');
        if (typeof node === 'object') {
          if (node['@graph']) return collect(node['@graph']);
          // A short SEO description is discovery text, not the article body.
          // Let the visible article win when structured data has no body.
          const body = node.articleBody || '';
          const rest = Object.values(node).map(collect).join(' ');
          return `${body} ${rest}`.trim();
        }
        return '';
      };
      const body = decodeEntities(collect(parsed));
      if (body.length > 200) {
        articleBody = body;
        break;
      }
    } catch (_) {}
  }

  let text = articleBody;
  if (!text) {
    // Container-based extraction: score candidate containers and keep the fullest one.
    const containers = bodySelectors(html);
    const scored = containers
      .map((chunk) => ({ chunk, text: textFromContainer(chunk) }))
      .filter((entry) => entry.text.length > 180)
      .sort((a, b) => b.text.length - a.text.length);
    if (scored.length) text = scored[0].text;
  }
  if (!text) {
    text = textFromContainer(html);
  }

  return { text: clean(text), date };
}

async function fetchArticle(url, { timeoutMs = 8000, maxChars = 7000, signal, _deadlineAt = Date.now() + timeoutMs, _attempt = 0, _visited = [] } = {}) {
  signal?.throwIfAborted();
  const remaining = Math.min(timeoutMs, _deadlineAt - Date.now());
  if (remaining <= 0) return null;
  if (_visited.includes(url) || _visited.length >= 3) return null;
  if (!/^https?:\/\//i.test(url || '')) return null;
  if (skipDomain(url)) return null;
  // Google News RSS links use a client-side redirect. Adding ?oc=5 forces a
  // server-side 302 to the actual article, which can then be fetched directly.
  const target = /^https:\/\/news\.google\.com\/rss\/articles\//i.test(url) && !url.includes('?')
    ? `${url}?oc=5`
    : url;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), remaining);
  const ownedSignal = signal ? AbortSignal.any([signal, controller.signal]) : controller.signal;
  try {
    const response = await fetch(target, {
      headers: {
        'User-Agent': BROWSER_UA,
        Accept: 'text/html,application/xhtml+xml',
        'Accept-Language': 'en-US,en;q=0.9',
      },
      redirect: 'follow',
      signal: ownedSignal,
    });
    if (!response.ok) return null;
    const type = response.headers.get('content-type') || '';
    if (type && !type.toLowerCase().includes('html')) return null;
    const html = await response.text();
    const { text, date } = extractArticle(html);
    const clipped = text.slice(0, maxChars);
    if (clipped.length >= 180) return { text: clipped, date, url };
    // Some Google News hops only serve a meta-refresh stub. Chase the real URL.
    const next = metaRefreshUrl(html);
    if (next && next !== target && next !== url) {
      return fetchArticle(next, { timeoutMs, maxChars, signal, _deadlineAt, _visited: [..._visited, url] });
    }
    return null;
  } catch (error) {
    signal?.throwIfAborted();
    if (_attempt === 0 && /abort|timed?out|EAI_AGAIN|ECONNRESET|ETIMEDOUT|fetch failed/i.test(String(error.name || '') + ' ' + error.message + ' ' + (error.cause?.code || ''))) {
      return fetchArticle(url, { timeoutMs, maxChars, signal, _deadlineAt, _attempt: 1, _visited });
    }
    return null;
  } finally {
    clearTimeout(timer);
  }
}

module.exports = { fetchArticle, extractArticle };
