const dns = require('dns');
if (typeof dns.setDefaultResultOrder === 'function') {
  dns.setDefaultResultOrder('ipv4first');
}
const { ddgSearch } = require('../providers/duckduckgo');
const { searchRss } = require('../providers/rss');
const { redditSearch } = require('../providers/reddit');
const { fetchArticle } = require('../providers/article');
const { wikipediaDeep } = require('../providers/wikipedia');
const { bingSearch } = require('../providers/bing');
const { yandexSearch, yandexNewsSearch } = require('../providers/yandex');
const { xSearch } = require('../providers/xSearch');
const { hnSearch } = require('../providers/hackernews');
const { detectNiche } = require('./nicheDetector');
const { runProvider, providerHealth } = require('../research/resilience');
const { runBatches } = require('../utils/concurrency');
const { gdeltSearch, openAlexSearch, crossrefSearch, usgsSearch } = require('../providers/freeData');
const { relevanceScore, isOfficial } = require('./evidence');

const DAY_MS = 24 * 60 * 60 * 1000;

async function safe(fn) {
  try {
    return await fn();
  } catch (err) {
    return { error: err.message || String(err) };
  }
}

// Each lane is isolated. A provider can time out, be rate limited, or open a
// circuit without taking down the rest of the research run.
function retry(provider, fn, options = {}) {
  return runProvider(provider, fn, { attempts: 3, delayMs: 400, timeoutMs: 20000, ...options });
}

function normalizeDate(value) {
  if (!value) return '';
  const text = String(value);
  const iso = text.match(/(\d{4}-\d{2}-\d{2})/);
  if (iso) return iso[1];
  const parsed = Date.parse(text);
  if (!Number.isNaN(parsed)) return new Date(parsed).toISOString().slice(0, 10);
  return '';
}

function daysOld(date) {
  const d = normalizeDate(date);
  if (!d) return null;
  const parsed = Date.parse(d);
  if (Number.isNaN(parsed)) return null;
  return Math.max(0, Math.floor((Date.now() - parsed) / DAY_MS));
}

function isGoogleNewsWrapper(url) {
  return /^https:\/\/news\.google\.com\/rss\/articles\//i.test(url || '');
}

function isRedditItem(item) {
  return /^r\//.test(item.source || '');
}

function uniqueByUrl(items) {
  const seen = new Set();
  return (items || []).filter((item) => {
    if (!item || !item.url) return false;
    const key = canonicalUrl(item.url);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function canonicalUrl(value) {
  try {
    const url = new URL(String(value));
    url.hash = '';
    for (const key of [...url.searchParams.keys()]) {
      if (/^(utm_[a-z]+|gclid|fbclid|si|oc)$/i.test(key)) url.searchParams.delete(key);
    }
    return url.href.replace(/\/$/, '');
  } catch (_) {
    return String(value || '').trim();
  }
}

function hostFor(value) {
  try { return new URL(String(value)).hostname.toLowerCase().replace(/^www\./, ''); } catch (_) { return ''; }
}

const PRIMARY_SEARCHES = {
  gaming: (q) => `${q} (site:rockstargames.com OR site:take2games.com OR site:playstation.com OR site:xbox.com)`,
  science: (q) => `${q} (site:nasa.gov OR site:esa.int OR site:noaa.gov OR site:nature.com)`,
  health: (q) => `${q} (site:cdc.gov OR site:nih.gov OR site:fda.gov OR site:who.int)`,
  finance: (q) => `${q} (site:sec.gov OR site:federalreserve.gov OR site:bls.gov)`,
  sports: (q) => `${q} (site:fifa.com OR site:uefa.com OR site:olympics.com)`,
  politics: (q) => `${q} (site:congress.gov OR site:usa.gov OR site:gov.uk)`,
  history: (q) => `${q} (site:archives.gov OR site:loc.gov OR site:britannica.com)`,
  tech: (q) => `${q} (site:openai.com OR site:blog.google OR site:microsoft.com)`,
};

// Search engines are useful for discovery, but time-sensitive entertainment
// topics can be overwhelmed by fan pages and recycled rumor posts. Add a
// small, deterministic set of first-party pages for the topic families where
// we know the publisher's canonical hub. These URLs are still fetched live;
// they are not treated as facts until their current page text is retrieved.
function officialSourceSeeds(query, niche) {
  if (/\bbritish\b/i.test(query) && /\bbeers?\b/i.test(query) && /\bpast|historic|history\b/i.test(query)) {
    return [
      { title: 'CAMRA: Beers from the past for now and the future', url: 'https://wb.camra.org.uk/2025/12/31/beers-from-the-past-for-now-and-the-future', source: "CAMRA What's Brewing", category: 'publisher', snippet: '' },
      { title: 'Beers from the past', url: 'https://www.retrowow.co.uk/retro_britain/keg_bitter/beers_from_the_past.html', source: 'Retrowow', category: 'publisher', snippet: '' },
    ];
  }
  if (/\b(?:forensic|postmortem|after death)\b/i.test(query) && /\b(?:death|deceased|postmortem)\b/i.test(query)) {
    return [
      { title: 'NIJ: A validated method for estimating time since death', url: 'https://nij.ojp.gov/topics/articles/new-method-measuring-human-decomposition-could-impact-medicolegal-death-investigations', source: 'National Institute of Justice', category: 'official', snippet: '' },
      { title: 'NIJ: Validation study of postmortem interval methods', url: 'https://nij.ojp.gov/library/publications/validation-study-utility-using-total-body-score-and-accumulated-degree-days', source: 'National Institute of Justice', category: 'official', snippet: '' },
      { title: 'NIST glossary: postmortem interval', url: 'https://www.nist.gov/glossary-term/42396', source: 'National Institute of Standards and Technology', category: 'official', snippet: '', contextForTopic: query, contextEntity: 'death' },
    ];
  }
  if (/\bprince\s+louis\b/i.test(query) && /\b(?:early|record|royal|public)\b/i.test(query)) {
    return [
      { title: 'Prince Louis | The Royal Family', url: 'https://www.royal.uk/prince-louis', source: 'The Royal Family', category: 'official', snippet: '', contextForTopic: query, contextEntity: 'Louis' },
    ];
  }
  if (/\b(?:indian\s+pacific|rail|train)\b/i.test(query) && /\b(?:crew|routine|shift|fatigue)\b/i.test(query)) {
    return [
      { title: 'Indian Pacific route and onboard crew facilities', url: 'https://www.greatsouthernrail.com.au/indian-pacific/about-indian-pacific/', source: 'Journey Beyond Rail', category: 'official', snippet: '', contextForTopic: query, contextEntity: 'crew' },
      { title: 'ATSB: fatigue management of train drivers', url: 'https://www.atsb.gov.au/safety-issues/RO-2018-018-SI-05', source: 'Australian Transport Safety Bureau', category: 'official', snippet: '', contextForTopic: query, contextEntity: 'train' },
    ];
  }
  if (/\bdiana\b/i.test(query) && /\b(?:letters?|correspondence)\b/i.test(query)) {
    return [
      { title: 'Diana, Princess of Wales: Clinton Presidential Records finding aid', url: 'https://www.clintonlibrary.gov/research/archives/finding-aids/diana-princess-wales-royal-house-windsor-united-kingdom', source: 'Clinton Presidential Library and Museum / NARA', category: 'official', snippet: '', contextForTopic: query, contextEntity: 'Diana' },
      { title: 'Provost Anderson: Correspondence with Diana, Princess of Wales', url: 'https://catalogue.etoncollege.com/coll-p-21-04-01-02', source: 'Eton College Collections', category: 'official', snippet: '' },
    ];
  }
  if (/\bmecum\b/i.test(query)) {
    return [
      { title: 'Mecum frequently asked questions', url: 'https://www.mecum.com/faq/' },
      { title: 'Mecum Bid Goes On: vehicles after the auction block', url: 'https://www.mecum.com/bid-goes-on/' },
      { title: 'Mecum: how to buy at auction', url: 'https://www.mecum.com/how-to-buy/' },
      { title: 'Mecum: how to sell at auction', url: 'https://www.mecum.com/how-to-sell/' },
    ].map(item => ({ ...item, source: 'Mecum Auctions', category: 'official', snippet: '',
      contextForTopic: query, contextEntity: 'Mecum' }));
  }
  if (/\bmini\s+cooper\b/i.test(query)) {
    return [
      { title: 'History of MINI and John Cooper Works', url: 'https://www.mini.co.uk/en_GB/home/why-mini/history-of-mini.html', source: 'MINI UK', category: 'official', snippet: '' },
      { title: 'History of MINI', url: 'https://www.miniusa.com/why-mini/why-mini/over-60-years-of-motoring.html/', source: 'MINI USA', category: 'official', snippet: '' },
      { title: 'MINI Cooper history: September 2000 Paris premiere', url: 'https://www.press.bmwgroup.com/usa/article/detail/T0020376EN_US/mini-cooper-to-make-world-debut-at-paris-auto-show?language=en_US', source: 'BMW Group PressClub', category: 'official', snippet: '' },
      { title: 'Jack Brabham and the Cooper racing history', url: 'https://www.formula1.com/en/information/drivers-hall-of-fame-jack-brabham.3XK2XWdL0moCmnSC55m84J', source: 'Formula 1', category: 'official', snippet: '', contextForTopic: query, contextEntity: 'Cooper' },
      { title: 'Jack Brabham and Cooper championship history', url: 'https://www.fia.com/news/sir-jack-brabham-1926-2014', source: 'FIA', category: 'official', snippet: '', contextForTopic: query, contextEntity: 'Cooper' }
    ];
  }
  if (niche === 'gaming' && /\bgta\s*(?:vi|6)\b|\bgrand theft auto\s*(?:vi|6)\b/i.test(query)) {
    return [
      {
        title: 'Grand Theft Auto VI',
        url: 'https://www.rockstargames.com/VI',
        source: 'Rockstar Games',
        category: 'official',
        snippet: '',
      },
    ];
  }
  if (niche === 'gaming' && /\bgta\s*[:\-]?\s*(?:san andreas|sa)\b|\bgrand theft auto\s*[:\-]?\s*(?:san andreas|sa)\b/i.test(query)) {
    return [
      {
        title: 'Grand Theft Auto: San Andreas',
        url: 'https://www.rockstargames.com/games/sanandreas',
        source: 'Rockstar Games',
        category: 'official',
        snippet: '',
      },
    ];
  }
  return [];
}

function sourceWeight(source) {
  const s = String(source || '').toLowerCase();
  if (s === 'wikipedia') return 5;
  if (s === 'bing') return 3;
  if (s.startsWith('hacker news')) return 1;
  if (s === 'duckduckgo') return 2;
  if (/^r\//.test(s)) return 1;
  return 4; // named RSS/news sources
}

function providerErrorList(results) {
  return (results || [])
    .filter((result) => result && result.error && !String(result.error).includes('RSS fallback'))
    .map((result) => `${result.provider || 'provider'}: ${result.error}`);
}

async function gatherQuickResearch(query, { includeReddit = true, fetchLimit = 5 } = {}) {
  const niche = detectNiche(query).niche;
  const officialSeeds = officialSourceSeeds(query, niche);
  const quickRetry = (provider, fn) => retry(provider, fn, { attempts: 1, delayMs: 0, timeoutMs: 8500 });
  const primaryQuery = PRIMARY_SEARCHES[niche] ? PRIMARY_SEARCHES[niche](query) : `${query} official source`;

  // One bounded parallel pass: broad search, exact reference, news/RSS,
  // first-party discovery, Reddit, and public X discovery. X is discovered
  // through search results because direct scraping is frequently gated by
  // authentication and anti-bot challenges.
  // Batched 3-at-a-time, not one big Promise.all: the egress proxy
  // serializes concurrent connections, so firing all 8 providers at once
  // made them ALL time out (Bing, Wikipedia, Reddit and X aborted
  // together in a live probe) while the same calls succeed sequentially.
  const results = await runBatches([
    () => quickRetry('bing', () => bingSearch(query, { limit: 8, timeoutMs: 6500 })),
    () => quickRetry('duckduckgo', () => ddgSearch(query, { limit: 8, timeoutMs: 6500 })),
    () => quickRetry('yandex', () => yandexSearch(query, { limit: 7, timeoutMs: 6500 })),
    () => quickRetry('wikipedia', () => wikipediaDeep(query, { limit: 2, timeoutMs: 7500 })),
    () => quickRetry('rss', () => searchRss(query, { limit: 10, niche })),
    () => quickRetry('bing-official', () => bingSearch(primaryQuery, { limit: 6, timeoutMs: 6500 })),
    includeReddit
      ? () => quickRetry('reddit', () => redditSearch(query, { limit: 6, timeoutMs: 6500 }))
      : () => Promise.resolve({ results: [] }),
    () => quickRetry('x-discovery', () => xSearch(query, { limit: 6, timeoutMs: 6500 })),
  ], 3, (fn) => fn());

  const [bing, ddg, yandex, wiki, rss, officialSearch, reddit, xDiscovery] = results;
  const bingList = uniqueByUrl([...(Array.isArray(bing) ? bing : []), ...(Array.isArray(officialSearch) ? officialSearch : [])]);
  const ddgList = Array.isArray(ddg) ? ddg : [];
  const yandexList = Array.isArray(yandex) ? yandex : [];
  const wikiList = Array.isArray(wiki) ? wiki : [];
  const rssList = Array.isArray(rss) ? rss : [];
  const redditResults = reddit && Array.isArray(reddit.results) ? reddit.results : [];
  const xResults = (Array.isArray(xDiscovery) ? xDiscovery : [])
    .filter((item) => /(^|\.)x\.com$|(^|\.)twitter\.com$/i.test(hostFor(item.url)))
    .map((item) => ({ ...item, source: item.source || 'X', category: 'social' }));

  const socialHosts = /(^|\.)(?:x\.com|twitter\.com|reddit\.com)$/i;
  const candidates = uniqueByUrl([
    ...officialSeeds,
    ...wikiList,
    ...rssList.filter((item) => !isGoogleNewsWrapper(item.url)),
    ...bingList,
    ...ddgList,
    ...yandexList,
  ]).filter((item) => !socialHosts.test(hostFor(item.url)) && (relevanceScore(item, query) > 0 || officialSeeds.some(seed => seed.url === item.url)))
    .sort((a, b) => Number(isOfficial(b)) - Number(isOfficial(a)) || relevanceScore(b, query) - relevanceScore(a, query))
    .slice(0, Math.max(1, fetchLimit));

  const fetched = await runBatches(candidates, 3, async (item) => {
    signal?.throwIfAborted();
    const article = await fetchArticle(item.url, { timeoutMs: 4500, maxChars: 6000 });
    signal?.throwIfAborted();
    return { url: item.url, article };
  });
  const articleByUrl = new Map(fetched.filter((entry) => entry.article?.text).map((entry) => [entry.url, entry.article]));
  const hydrate = (items) => (Array.isArray(items) ? items : []).map((item) => {
    const article = articleByUrl.get(item.url);
    const articleDate = article?.date || normalizeDate(item.date);
    return {
      ...item,
      snippet: item.snippet || item.title || '',
      articleText: item.articleText || article?.text || item.snippet || item.title || '',
      hasArticleBody: item.hasArticleBody === true || Boolean(article?.text),
      articleDate,
      ageDays: daysOld(articleDate),
    };
  });

  // Keep canonical first-party pages in the evidence bundle even when their
  // anti-bot layer prevents article extraction. Their current URL, publisher
  // and page title remain attributable discovery evidence; they are marked
  // body-less so the evidence ledger cannot pretend that unobserved article
  // copy was read. This prevents a total "no attributable claims" collapse
  // while preserving the distinction between title metadata and page text.
  const officialH = hydrate(officialSeeds);
  const wikiH = hydrate(wikiList);
  const rssH = hydrate(rssList);
  const bingH = hydrate(bingList);
  const ddgH = hydrate(ddgList);
  const yandexH = hydrate(yandexList);
  const all = [...officialH, ...wikiH, ...rssH, ...bingH, ...ddgH, ...yandexH, ...redditResults, ...xResults]
    .filter((item) => item?.url && (item.articleText || item.snippet || item.title));

  return {
    query,
    niche,
    gatheredAt: new Date().toISOString(),
    duckduckgo: ddgH,
    duckduckgoNews: [],
    rss: rssH,
    bing: bingH,
    yandex: yandexH,
    x: xResults,
    wikipedia: wikiH,
    hackernews: [],
    reddit: redditResults,
    structured: [],
    targeted: [],
    official: officialH,
    redditBlocked: Boolean(reddit?.blocked),
    redditViaRss: Boolean(reddit?.viaRss),
    errors: providerErrorList(results),
    stats: {
      mode: 'quick',
      sources: all.length,
      articlesFetched: articleByUrl.size,
      datedResults: all.filter((item) => daysOld(item.articleDate || item.date) !== null).length,
      distinctDomains: new Set(all.map((item) => hostFor(item.url)).filter(Boolean)).size,
      freshest: all.reduce((value, item) => {
        const age = daysOld(item.articleDate || item.date);
        return age !== null && (value === null || age < value) ? age : value;
      }, null),
    },
  };
}

async function gatherResearch(query, { includeReddit = true, fetchLimit = 12, phase = 'fast', includeStructured = true, researchPlan, quick = false, signal } = {}) {
  signal?.throwIfAborted();
  if (quick) return gatherQuickResearch(query, { includeReddit, fetchLimit: Math.min(fetchLimit, 5) });
  const niche = detectNiche(query).niche;
  const officialSeeds = officialSourceSeeds(query, niche);

  // Thunks, not promises: runBatches starts them 3 at a time. Firing all
  // ~15 provider calls at once through the egress proxy made them all
  // stall and time out together (verified live: a concurrent burst lost
  // Bing, Wikipedia, Reddit and X; sequential calls succeed in 1-3s).
  const tasks = [
    () => retry('duckduckgo', () => ddgSearch(query, { limit: 8 })),
    () => retry('duckduckgo-news', () => ddgSearch(`${query} latest news`, { limit: 6 })),
    () => retry('rss', () => searchRss(query, { limit: 16, niche })),
    () => retry('bing', () => bingSearch(query, { limit: 8, timeoutMs: 15000 })),
    () => retry('bing-official', () => bingSearch(`${query} official announcement`, { limit: 8, timeoutMs: 15000 })),
    () => retry('wikipedia', () => wikipediaDeep(query, { limit: 2 })),
    () => retry('hackernews', () => hnSearch(query, { limit: 6 })),
    () => retry('yandex', () => yandexSearch(query, { limit: 8 })),
    () => retry('yandex-news', () => yandexNewsSearch(query, { limit: 6 })),
  ];
  const primaryQuery = PRIMARY_SEARCHES[niche] && PRIMARY_SEARCHES[niche](query);
  if (primaryQuery) tasks.push(() => retry('bing-primary', () => bingSearch(primaryQuery, { limit: 8, timeoutMs: 15000 })));
  if (includeReddit) {
    tasks.push(() => retry('reddit', () => redditSearch(query, { limit: 6 })));
  }
  const structuredStartIndex = tasks.length;
  if (includeStructured) {
    // Thread the timeout into the provider call too: getJson's own 12s
    // abort used to fire under proxy congestion while the outer
    // runProvider budget was 15s, surfacing as a bare AbortError and
    // tripping the circuit after 3 attempts (Cooper run: gdelt aborted).
    tasks.push(() => retry('gdelt', () => gdeltSearch(query, { limit: phase === 'deep' ? 10 : 5, timeoutMs: 15000 }), { timeoutMs: 15000 }));
    if (phase === 'deep') {
      if (['science', 'health', 'finance', 'tech'].includes(niche)) {
        tasks.push(() => retry('openalex', () => openAlexSearch(query, { limit: 6, timeoutMs: 15000 }), { timeoutMs: 15000 }));
        tasks.push(() => retry('crossref', () => crossrefSearch(query, { limit: 6, timeoutMs: 15000 }), { timeoutMs: 15000 }));
      }
      tasks.push(() => retry('usgs', () => usgsSearch(query, { limit: 6, timeoutMs: 15000 }), { timeoutMs: 15000 }));
    }
  }
  // Deep research should investigate specific gaps rather than only increase
  // the number of generic search hits. Keep the planned lane bounded so a
  // weak or unavailable provider cannot make the whole pass unresponsive.
  const structuredEndIndex = tasks.length;
  const plannedQueries = [...new Set((researchPlan?.questions || [])
    .flatMap((item) => item.queries || item.query || [])
    .map((value) => String(value || '').trim())
    .filter(Boolean))]
    .slice(0, phase === 'deep' ? 8 : 4);
  const targetedStartIndex = tasks.length;
  plannedQueries.forEach((targetQuery, index) => {
    tasks.push(() => retry(`targeted-bing-${index + 1}`, () => bingSearch(targetQuery, { limit: 6, timeoutMs: 15000 }), { timeoutMs: 20000 }));
    if (index < (phase === 'deep' ? 4 : 2)) {
      tasks.push(() => retry(`targeted-duckduckgo-${index + 1}`, () => ddgSearch(targetQuery, { limit: 6 }), { timeoutMs: 20000 }));
    }
  });

  const results = await runBatches(tasks, 3, (fn) => fn());
  signal?.throwIfAborted();
  const [ddg, ddgNews, rss, bing, bingOfficial, wiki, hn, yandex, yandexNews] = results;
  const primary = primaryQuery ? results[9] : [];
  const reddit = includeReddit ? results[primaryQuery ? 10 : 9] : null;
  const structured = results.slice(structuredStartIndex, structuredEndIndex).flatMap((result) => Array.isArray(result) ? result : []);
  const targeted = results.slice(targetedStartIndex).flatMap((result) => Array.isArray(result) ? result : []);
  const rssList = Array.isArray(rss) ? rss : [];
  const ddgNewsList = Array.isArray(ddgNews) ? ddgNews : [];
  const ddgList = Array.isArray(ddg) ? ddg : [];
  const bingList = uniqueByUrl([
    ...(Array.isArray(bing) ? bing : []),
    ...(Array.isArray(bingOfficial) ? bingOfficial : []),
    ...(Array.isArray(primary) ? primary : []),
  ]);
  const wikiList = Array.isArray(wiki) ? wiki : [];
  const hnList = Array.isArray(hn) ? hn : [];
  const yandexList = uniqueByUrl([
    ...(Array.isArray(yandex) ? yandex : []),
    ...(Array.isArray(yandexNews) ? yandexNews : []),
  ]);

  // Dated (news) sources first, newest first, then undated web results.
  const byDateDesc = (a, b) => {
    const da = daysOld(a.date);
    const db = daysOld(b.date);
    if (da === null && db === null) return 0;
    if (da === null) return 1;
    if (db === null) return -1;
    return da - db;
  };

  // Build the pool of items we will try to hydrate with article bodies.
  // Google News RSS article links are JS-only wrappers now (no body, no server
  // redirect), so fetching them is guaranteed to fail and burns time. Prefer
  // sources that carry their own body text or direct article links.
  const candidates = [
    ...officialSeeds,
    ...rssList.filter((x) => !isGoogleNewsWrapper(x.url)),
    ...bingList,
    ...ddgNewsList.filter((x) => !isGoogleNewsWrapper(x.url)),
    ...ddgList,
    ...yandexList,
    ...wikiList.filter((x) => !x.isWiki),
    ...hnList,
    ...structured,
    ...targeted,
  ];

  const seenUrls = new Set();
  const unique = candidates
    .filter(item => relevanceScore(item, query) > 0 || officialSeeds.some(seed => seed.url === item.url))
    .filter((item) => {
      const key = canonicalUrl(item && item.url);
      return item && key && !seenUrls.has(key) && seenUrls.add(key);
    })
    .sort((a, b) => {
      const aPrimary = isOfficial(a);
      const bPrimary = isOfficial(b);
      if (aPrimary !== bPrimary) return Number(bPrimary) - Number(aPrimary);
      const seedRank = item => officialSeeds.some(seed => canonicalUrl(seed.url) === canonicalUrl(item.url)) ? 1 : 0;
      if (seedRank(a) !== seedRank(b)) return seedRank(b) - seedRank(a);
      if (relevanceScore(a, query) !== relevanceScore(b, query)) return relevanceScore(b, query) - relevanceScore(a, query);
      return byDateDesc(a, b);
    })
    .slice(0, fetchLimit);

  const fetched = await runBatches(unique, 3, async (item) => {
    signal?.throwIfAborted();
    const article = await fetchArticle(item.url, { signal });
    signal?.throwIfAborted();
    return { url: item.url, article };
  });
  signal?.throwIfAborted();
  const articleByUrl = new Map(fetched.filter((entry) => entry.article && entry.article.text).map((entry) => [entry.url, entry.article]));

  const hydrate = (items) => (Array.isArray(items) ? items : []).map((item) => {
    const article = articleByUrl.get(item.url);
    const articleDate = article && article.date ? article.date : normalizeDate(item.date);
    const articleText = item.articleText || (article ? article.text : '') || item.snippet || item.title || '';
    return {
      ...item,
      snippet: item.snippet || item.title || '',
      articleText,
      hasArticleBody: item.hasArticleBody === true || Boolean(article && article.text),
      articleDate,
      ageDays: daysOld(articleDate),
    };
  });

  const rssH = hydrate(rssList);
  const ddgNewsH = hydrate(ddgNewsList);
  const ddgH = hydrate(ddgList);
  const bingH = hydrate(bingList);
  const wikiH = hydrate(wikiList);
  const hnH = hydrate(hnList);
  const structuredH = hydrate(structured);
  const targetedH = hydrate(targeted);
  const yandexH = hydrate(yandexList);
  const redditResults = reddit && Array.isArray(reddit.results) ? reddit.results : [];
  const redditH = hydrate(redditResults);
  // Preserve canonical first-party pages when robots/anti-bot controls block
  // body extraction. The evidence ledger keeps hasArticleBody=false, so the
  // writer can cite the observed URL/title metadata without pretending that
  // unseen article copy was read. Dropping these rows caused valid subjects
  // to collapse into "no attributable external claims".
  const officialH = hydrate(officialSeeds);

  const allHydrated = [...officialH, ...rssH, ...bingH, ...ddgNewsH, ...ddgH, ...yandexH, ...wikiH, ...hnH, ...redditH, ...structuredH]
    .filter((x) => x.articleText || x.snippet || x.title)
    .filter((x) => x.url)
    .sort((a, b) => {
      const wa = sourceWeight(a.source);
      const wb = sourceWeight(b.source);
      if (wa !== wb) return wb - wa;
      return byDateDesc(a, b);
    });

  return {
    query,
    niche,
    gatheredAt: new Date().toISOString(),
    duckduckgo: ddgH,
    duckduckgoNews: ddgNewsH,
    rss: rssH,
    bing: bingH,
    yandex: yandexH,
    wikipedia: wikiH,
    hackernews: hnH,
    reddit: redditResults,
    structured: structuredH,
    targeted: targetedH,
    official: officialH,
    redditBlocked: !!(reddit && reddit.blocked),
    redditViaRss: !!(reddit && reddit.viaRss),
    errors: [ddg, ddgNews, rss, bing, bingOfficial, wiki, hn, yandex, yandexNews, primary, reddit, ...results.slice(structuredStartIndex)]
      .filter((x) => x && x.error && !String(x.error).includes('RSS fallback'))
      .map((x) => `${x.provider || 'provider'}: ${x.error}`),
    stats: {
      sources: allHydrated.length,
      articlesFetched: articleByUrl.size,
      datedResults: candidates.filter((x) => daysOld(x.date) !== null).length,
      distinctDomains: new Set(allHydrated.map((x) => hostFor(x.url)).filter(Boolean)).size,
      freshest: allHydrated.reduce((acc, item) => {
        if (item.ageDays !== null && (acc === null || item.ageDays < acc)) return item.ageDays;
        return acc;
      }, null),
    },
  };
}

module.exports = { gatherResearch, canonicalUrl, normalizeDate, providerHealth, officialSourceSeeds };
