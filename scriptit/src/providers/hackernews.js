const HN_API = 'https://hn.algolia.com/api/v1/search';

async function hnSearch(query, { limit = 8, timeoutMs = 8000 } = {}) {
  const params = new URLSearchParams({
    query,
    tags: 'story',
    hitsPerPage: String(limit),
  });
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${HN_API}?${params}`, {
      headers: { Accept: 'application/json', 'User-Agent': 'ScriptIT/1.0 (research agent)' },
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`HN HTTP ${res.status}`);
    const json = await res.json();
    return (json.hits || [])
      .filter((h) => h.title || h.story_title)
      .slice(0, limit)
      .map((h) => ({
        title: h.title || h.story_title || '',
        snippet: (h.story_text || h.comment_text || h.title || '')
          .replace(/<[^>]+>/g, ' ')
          .replace(/\s+/g, ' ')
          .trim()
          .slice(0, 500),
        url: h.url || (h.objectID ? `https://news.ycombinator.com/item?id=${h.objectID}` : ''),
        hnPoints: h.points || 0,
        hnComments: h.num_comments || 0,
        date: h.created_at || '',
        source: 'Hacker News',
        category: 'tech',
      }));
  } finally {
    clearTimeout(t);
  }
}

module.exports = { hnSearch };
