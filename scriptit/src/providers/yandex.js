'use strict';

/**
 * Yandex Web & News Search Provider.
 * 
 * Scrapes real-time search results, dated snippets, and news from Yandex
 * with multi-selector HTML parsing and error resilience.
 */

const USER_AGENTS = [
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
  'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
];

function getRandomUserAgent() {
  return USER_AGENTS[Math.floor(Math.random() * USER_AGENTS.length)];
}

function clean(value) {
  return String(value || '')
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&#x27;/gi, "'")
    .replace(/&#0183;/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function usableUrl(value) {
  try {
    const url = new URL(String(value || ''), 'https://yandex.com');
    if (!/^https?:$/i.test(url.protocol)) return '';
    if (/(^|\.)yandex\.(?:com|ru|net)$/i.test(url.hostname)) {
      const target = url.searchParams.get('url');
      if (target && /^https?:\/\//i.test(target)) return target;
      return '';
    }
    return url.href;
  } catch (_) {
    return '';
  }
}

function extractDateFromSnippet(snippet) {
  if (!snippet) return '';
  // e.g. "15 Aug 2026", "2026-08-15", "Aug 15, 2026"
  const m1 = snippet.match(/\b(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\b/);
  if (m1) {
    const parsed = Date.parse(m1[1]);
    if (!Number.isNaN(parsed)) return new Date(parsed).toISOString().slice(0, 10);
  }
  const m2 = snippet.match(/\b(\d{4}-\d{2}-\d{2})\b/);
  if (m2) return m2[1];
  
  const m3 = snippet.match(/\b([A-Za-z]{3}\s+\d{1,2},\s+\d{4})\b/);
  if (m3) {
    const parsed = Date.parse(m3[1]);
    if (!Number.isNaN(parsed)) return new Date(parsed).toISOString().slice(0, 10);
  }
  return '';
}

function parseYandexResults(html, limit = 8) {
  const results = [];
  const seen = new Set();
  // Support both desktop (.serp-item, .Organic) and mobile (.Organic-Item) layout blocks using lookahead split
  const blocks = String(html || '').split(/(?=<(?:li|div)[^>]+class=["'][^"']*\b(?:serp-item|organic__item|news-search-story|Organic-Item|Organic)\b[^"']*["'])/i);
  
  for (const block of blocks) {
    if (results.length >= limit) break;
    if (!/\b(?:serp-item|organic__item|news-search-story|Organic-Item|Organic)\b/i.test(block)) continue;
    const link = block.match(/<a[^>]+class=["'][^"']*(?:OrganicTitle-Link|organic__url|Link|news-story__title-link)[^"']*["'][^>]+href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/i)
      || block.match(/<a[^>]+href=["'](https?:\/\/[^"']+)["'][^>]*>([\s\S]*?)<\/a>/i);
    if (!link) continue;
    const url = usableUrl(link[1]);
    const title = clean(link[2]);
    if (!url || !title || seen.has(url)) continue;

    const snippetMatch = block.match(/<(?:div|span|p)[^>]+class=["'][^"']*(?:OrganicText|OrganicTextContentSpan|organic__text|TextContainer|text-container|ExtendedText-Short|news-story__text)[^"']*["'][^>]*>([\s\S]*?)<\/(?:div|span|p)>/i)
      || block.match(/<div[^>]+class=["'][^"']*organic__content-wrapper[^"']*["'][^>]*>([\s\S]*?)<\/div>/i);
    
    const snippet = clean(snippetMatch ? snippetMatch[1] : '').slice(0, 500);
    const date = extractDateFromSnippet(snippet);
    seen.add(url);

    results.push({
      title,
      snippet,
      url,
      source: 'Yandex',
      category: 'web',
      date,
      articleDate: date
    });
  }

  return results;
}

async function yandexSearch(query, { limit = 8, timeoutMs = 7000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const url = `https://yandex.com/search/?text=${encodeURIComponent(query)}&lang=en`;
    const response = await fetch(url, {
      headers: {
        'User-Agent': getRandomUserAgent(),
        Accept: 'text/html,application/xhtml+xml',
        'Accept-Language': 'en-US,en;q=0.9',
      },
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`Yandex HTTP ${response.status}`);
    return parseYandexResults(await response.text(), limit);
  } catch (err) {
    return [];
  } finally {
    clearTimeout(timer);
  }
}

async function yandexNewsSearch(query, { limit = 6, timeoutMs = 7000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const url = `https://yandex.com/news/search?text=${encodeURIComponent(query)}&lang=en`;
    const response = await fetch(url, {
      headers: {
        'User-Agent': getRandomUserAgent(),
        Accept: 'text/html,application/xhtml+xml',
        'Accept-Language': 'en-US,en;q=0.9',
      },
      signal: controller.signal,
    });
    if (!response.ok) return [];
    return parseYandexResults(await response.text(), limit).map((r) => ({
      ...r,
      category: 'news',
      source: 'Yandex News'
    }));
  } catch (_) {
    return [];
  } finally {
    clearTimeout(timer);
  }
}

module.exports = { yandexSearch, yandexNewsSearch, parseYandexResults };
