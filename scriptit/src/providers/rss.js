const Parser = require('rss-parser');

const STATIC_FEEDS = [
  { name: 'Hacker News', url: 'https://hnrss.org/frontpage', category: 'tech' },
  { name: 'TechCrunch', url: 'https://techcrunch.com/feed/', category: 'tech' },
  { name: 'The Verge', url: 'https://www.theverge.com/rss/index.xml', category: 'tech' },
  { name: 'ESPN', url: 'https://www.espn.com/espn/rss/news', category: 'sports' },
  { name: 'BBC Sport', url: 'http://feeds.bbci.co.uk/sport/rss.xml', category: 'sports' },
  { name: 'CoinDesk', url: 'https://www.coindesk.com/arc/outboundfeeds/rss/', category: 'finance' },
  { name: 'NASA', url: 'https://www.nasa.gov/news-release/feed/', category: 'science' },
  { name: 'ScienceDaily Top', url: 'https://www.sciencedaily.com/rss/top.xml', category: 'science' },
  { name: 'Nature', url: 'https://www.nature.com/nature.rss', category: 'science' },
  { name: 'IGN', url: 'https://feeds.ign.com/ign/games-all', category: 'gaming' },
  { name: 'Polygon', url: 'https://www.polygon.com/rss/index.xml', category: 'gaming' },
  { name: 'GameSpot', url: 'https://www.gamespot.com/feeds/mashup/', category: 'gaming' },
  { name: 'Eurogamer', url: 'https://www.eurogamer.net/feed', category: 'gaming' },
  { name: 'Variety', url: 'https://variety.com/feed/', category: 'entertainment' },
  { name: 'The Hollywood Reporter', url: 'https://www.hollywoodreporter.com/feed/', category: 'entertainment' },
  { name: 'TechRadar', url: 'https://www.techradar.com/rss', category: 'tech' },
];

const NICHE_FEED_MAP = {
  gaming: ['gaming'],
  entertainment: ['entertainment', 'tech'],
  movies: ['entertainment'],
  tech: ['tech', 'gaming'],
  science: ['science'],
  sports: ['sports'],
  finance: ['finance', 'tech'],
  health: ['science', 'tech'],
  history: [],
  crime: [],
  politics: [],
  biography: ['entertainment'],
};

function topicFeeds(query) {
  const q = encodeURIComponent(query);
  return [
    { name: `Google News (today): ${query}`, url: `https://news.google.com/rss/search?q=${q}+when:1d&hl=en-US&gl=US&ceid=US:en`, category: 'news' },
    { name: `Google News (3 days): ${query}`, url: `https://news.google.com/rss/search?q=${q}+when:3d&hl=en-US&gl=US&ceid=US:en`, category: 'news' },
    { name: `Google News (week): ${query}`, url: `https://news.google.com/rss/search?q=${q}+when:7d&hl=en-US&gl=US&ceid=US:en`, category: 'news' },
    { name: `Google News (month): ${query}`, url: `https://news.google.com/rss/search?q=${q}+when:1m&hl=en-US&gl=US&ceid=US:en`, category: 'news' },
    { name: `Google News: ${query}`, url: `https://news.google.com/rss/search?q=${q}&hl=en-US&gl=US&ceid=US:en`, category: 'news' },
    { name: `Hacker News: ${query}`, url: `https://hnrss.org/newest?q=${q}`, category: 'tech' },
  ];
}

function feedsForNiche(niche) {
  const wanted = NICHE_FEED_MAP[niche] || ['tech', 'sports', 'science', 'finance', 'gaming', 'entertainment'];
  return STATIC_FEEDS.filter((feed) => wanted.includes(feed.category)).slice(0, 5);
}

const parser = new Parser({
  timeout: 8000,
  customFields: { item: ['media:content', 'description', 'source'] },
});

const STOPWORDS = new Set([
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

function tokenize(s) {
  return (s || '')
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .split(/\s+/)
    .filter((w) => w.length > 2 && !STOPWORDS.has(w));
}

function stripHtml(s) {
  if (!s) return '';
  return s.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
}

function publisherName(item, feed) {
  const source = item && item.source;
  if (source && typeof source === 'object' && source.title) return stripHtml(source.title);
  if (typeof source === 'string' && source.trim()) return stripHtml(source);
  const title = stripHtml(item && item.title);
  const match = title.match(/\s[-|]\s([^-|]+)$/);
  return match ? match[1].trim() : feed.name;
}

function scoreItem(item, queryTokens) {
  if (!queryTokens.length) return 1;
  const title = `${item.title || ''}`.toLowerCase();
  const text = `${title} ${item.contentSnippet || ''} ${item.content || ''}`.toLowerCase();
  let score = 0;
  for (const t of queryTokens) {
    if (title.includes(t)) score += 2;
    else if (text.includes(t)) score += 1;
  }
  return score;
}

async function fetchFeed(feed, timeoutMs = 8000) {
  try {
    const out = await parser.parseURL(feed.url);
    return { feed, items: (out.items || []).slice(0, 25) };
  } catch (err) {
    return { feed, items: [], error: err.message };
  }
}

async function searchRss(query, { limit = 16, timeoutMs = 8000, niche = 'general' } = {}) {
  const qTokens = tokenize(query);
  const feedsToTry = [
    ...topicFeeds(query),
    ...feedsForNiche(niche),
  ];

  const settled = await Promise.allSettled(feedsToTry.map((f) => fetchFeed(f, timeoutMs)));
  const all = [];
  const seenUrls = new Set();
  for (const r of settled) {
    if (r.status !== 'fulfilled') continue;
    const { items } = r.value;
    for (const it of items) {
      const url = it.link || it.guid || '';
      if (url && seenUrls.has(url)) continue;
      if (url) seenUrls.add(url);
      const snippet = stripHtml(it.contentSnippet || it.content || it.description || it.summary || '').slice(0, 500);
      const date = it.pubDate || it.isoDate || '';
      const score = scoreItem(it, qTokens);
      const isDated = !!(date && !Number.isNaN(Date.parse(date)));
      const publisher = publisherName(it, r.value.feed);
      all.push({
        title: it.title || '',
        snippet: snippet.slice(0, 500),
        url,
        date,
        source: publisher,
        feedSource: r.value.feed.name,
        aggregator: /^Google News/i.test(r.value.feed.name),
        category: r.value.feed.category,
        score,
        isDated,
      });
    }
  }
  all.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    if (a.isDated !== b.isDated) return a.isDated ? -1 : 1;
    return new Date(b.date || 0) - new Date(a.date || 0);
  });
  return all.slice(0, limit);
}

module.exports = { searchRss, STATIC_FEEDS, topicFeeds, feedsForNiche };
