const { relevanceScore } = require('./evidence');

function dedupe(items) {
  const seen = new Set();
  const out = [];
  for (const it of items || []) {
    const rawKey = typeof it === 'string' ? it : (it.url || `${it.title || ''}|${it.source || ''}`);
    const key = String(rawKey).toLowerCase().replace(/#.*$/, '').replace(/\/$/, '').trim();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(it);
  }
  return out;
}

function trim(s, n) {
  if (!s) return '';
  return s.length > n ? s.slice(0, n).trim() + '…' : s;
}

const DAY_MS = 24 * 60 * 60 * 1000;
const RECENT_WINDOW_DAYS = 31;

function normalizeDate(value) {
  if (!value) return '';
  const text = String(value);
  const iso = text.match(/(\d{4}-\d{2}-\d{2})/);
  if (iso) return iso[1];
  const parsed = Date.parse(text);
  if (!Number.isNaN(parsed)) return new Date(parsed).toISOString().slice(0, 10);
  return '';
}

function isRecentDate(value) {
  const date = normalizeDate(value);
  if (!date) return null;
  const parsed = Date.parse(date);
  if (Number.isNaN(parsed)) return null;
  return Date.now() - parsed < RECENT_WINDOW_DAYS * DAY_MS;
}

function dated(text, date) {
  const d = normalizeDate(date);
  return d ? `[${d}] ${text}` : text;
}

function isGoogleBoilerplate(item) {
  const t = String(item.title || '');
  return /^Google News - .* - Latest/i.test(t) || /Read full articles, watch videos|browse thousands of titles/i.test(t) || !t;
}

function isGoogleNewsWrapper(url) {
  return /^https:\/\/news\.google\.com\/rss\/articles\//i.test(url || '');
}

function buildBrief(research) {
  const all = dedupe([
    ...(research.duckduckgo || []),
    ...(research.duckduckgoNews || []),
    ...(research.bing || []),
    ...(research.bingNews || []),
    ...(research.yandex || []),
    ...(research.x || []),
    ...(research.wikipedia || []),
    ...(research.hackernews || []),
    ...(research.rss || []),
    ...(research.official || []),
    ...(research.reddit || []),
    ...(research.structured || []),
    ...(research.targeted || []),
  ]).filter((item) => item && item.url && !isGoogleBoilerplate(item) && !isGoogleNewsWrapper(item.url)
    && relevanceScore(item, research.query || '') > 0);

  const facts = [];
  const recentEvents = [];
  const opinions = [];
  const stats = [];
  const quotes = [];

  for (const it of all) {
    const isWiki = it.source === 'Wikipedia';
    const text = trim(`${it.title}. ${it.articleText || it.snippet || ''}`, isWiki ? 2600 : 500);
    if (!text) continue;
    const sourceDate = it.articleDate || it.date || '';
    const recent = isRecentDate(sourceDate);
    if (/\b\d{2,}(\.\d+)?(%|x|k|m|b|bn|trillion|million|billion)?\b/i.test(text)) {
      stats.push(dated(text, sourceDate));
    } else if (it.source && (it.source.startsWith('r/') || it.source.startsWith('Hacker News') || it.source === 'X')) {
      opinions.push(dated(text, sourceDate));
    } else if (recent === true) {
      recentEvents.push(dated(text, sourceDate));
    } else if (/yesterday|today|this week|this month|202[4-9]|latest|breaking|just in/i.test(text)) {
      recentEvents.push(dated(text, sourceDate));
    } else {
      facts.push(dated(text, sourceDate));
    }
    const m = text.match(/"([^"]{15,180})"/);
    if (m) quotes.push(m[1]);
  }

  return {
    query: research.query,
    gatheredAt: research.gatheredAt,
    sources: all.slice(0, 25).map((x) => ({
      title: x.title,
      url: x.url || '',
      source: x.source || '',
      date: x.date || x.articleDate || '',
      // Synthetic (model-written dossier) items carry fabricated archive
      // URLs; the evidence gate must not count them as external sources.
      synthetic: x.synthetic === true,
    })),
    facts: dedupe(facts).slice(0, 12),
    recentEvents: dedupe(recentEvents).slice(0, 10),
    opinions: dedupe(opinions).slice(0, 8),
    statistics: dedupe(stats).slice(0, 10),
    quotes: dedupe(quotes).slice(0, 6),
    errors: research.errors || [],
  };
}

function briefToPromptText(brief) {
  const lines = [];
  lines.push(`Topic: ${brief.query}`);
  lines.push(`Gathered: ${brief.gatheredAt}`);
  lines.push('');
  if (brief.recentEvents.length) {
    lines.push('RECENT EVENTS / NEWS:');
    brief.recentEvents.forEach((e, i) => lines.push(`${i + 1}. ${e}`));
    lines.push('');
  }
  if (brief.facts.length) {
    lines.push('KEY FACTS:');
    brief.facts.forEach((e, i) => lines.push(`${i + 1}. ${e}`));
    lines.push('');
  }
  if (brief.statistics.length) {
    lines.push('STATISTICS:');
    brief.statistics.forEach((e, i) => lines.push(`${i + 1}. ${e}`));
    lines.push('');
  }
  if (brief.quotes.length) {
    lines.push('QUOTES:');
    brief.quotes.forEach((e, i) => lines.push(`${i + 1}. ${e}`));
    lines.push('');
  }
  if (brief.opinions.length) {
    lines.push('PUBLIC REACTION / DISCOVERY SIGNALS (Reddit, X, forums — not standalone proof):');
    brief.opinions.forEach((e, i) => lines.push(`${i + 1}. ${e}`));
    lines.push('');
  }
  if (brief.sources.length) {
    lines.push('SOURCES:');
    brief.sources.slice(0, 12).forEach((s) => {
      const urlPart = s.url ? ` — ${s.url}` : '';
      lines.push(`- ${s.title}${urlPart} (${s.source})${s.date ? ` [${s.date}]` : ''}`);
    });
  }
  return lines.join('\n');
}

module.exports = { buildBrief, briefToPromptText };
