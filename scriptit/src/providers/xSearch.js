'use strict';

/**
 * Real-Time X / Twitter Search Provider.
 * 
 * Discovers live tweets, quotes, and community reactions on X / Twitter
 * using multi-engine targeted site queries (Bing, DuckDuckGo, Yandex).
 */

const { bingSearch } = require('./bing');
const { ddgSearch } = require('./duckduckgo');
const { yandexSearch } = require('./yandex');

function isXUrl(url) {
  return /(?:x\.com|twitter\.com)\/([a-zA-Z0-9_]+)\/status\/(\d+)/i.test(String(url || ''));
}

function extractHandleFromUrl(url) {
  const m = /(?:x\.com|twitter\.com)\/([a-zA-Z0-9_]+)/i.exec(String(url || ''));
  return m && m[1] && !['home', 'explore', 'search', 'intent'].includes(m[1].toLowerCase()) ? `@${m[1]}` : 'X';
}

function normalizeXItem(item) {
  const handle = extractHandleFromUrl(item.url);
  const cleanSnippet = String(item.snippet || item.title || '')
    .replace(/^["']|["']$/g, '')
    .replace(/on X:\s*["']?/i, '')
    .replace(/on Twitter:\s*["']?/i, '')
    .trim();

  return {
    title: item.title || `Post by ${handle}`,
    snippet: cleanSnippet,
    url: item.url,
    handle,
    source: `X (${handle})`,
    category: 'social',
    date: item.date || item.articleDate || '',
    articleDate: item.date || item.articleDate || ''
  };
}

async function xSearch(query, { limit = 6, timeoutMs = 7000 } = {}) {
  const siteQuery = `${query} (site:x.com OR site:twitter.com)`;
  const results = [];
  const seenUrls = new Set();

  // Query search engines in parallel for live X posts
  const [bingResults, ddgResults, yandexResults] = await Promise.allSettled([
    bingSearch(siteQuery, { limit: limit + 2, timeoutMs }).catch(() => []),
    ddgSearch(siteQuery, { limit: limit + 2, timeoutMs }).catch(() => []),
    yandexSearch(siteQuery, { limit: limit + 2, timeoutMs }).catch(() => [])
  ]);

  const candidates = [
    ...(bingResults.status === 'fulfilled' && Array.isArray(bingResults.value) ? bingResults.value : []),
    ...(ddgResults.status === 'fulfilled' && Array.isArray(ddgResults.value) ? ddgResults.value : []),
    ...(yandexResults.status === 'fulfilled' && Array.isArray(yandexResults.value) ? yandexResults.value : [])
  ];

  for (const item of candidates) {
    if (results.length >= limit) break;
    if (!item || !item.url) continue;

    // Filter to genuine post or profile URLs on X / Twitter
    if (/(^|\.)(?:x\.com|twitter\.com)$/i.test(new URL(item.url, 'https://x.com').hostname)) {
      const canonical = item.url.split('?')[0].replace(/\/$/, '');
      if (!seenUrls.has(canonical)) {
        seenUrls.add(canonical);
        results.push(normalizeXItem(item));
      }
    }
  }

  return results;
}

module.exports = {
  xSearch,
  isXUrl,
  extractHandleFromUrl,
  normalizeXItem
};
