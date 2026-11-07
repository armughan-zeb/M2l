const { gatherResearch } = require('../src/agents/researcher');
const { buildBrief, briefToPromptText } = require('../src/agents/compressor');
const { buildEvidenceLedger, ledgerToPromptText } = require('../src/agents/evidence');

(async () => {
  const t0 = Date.now();
  const topic = process.argv[2] || 'GTA VI';
  const data = await gatherResearch(topic, { includeReddit: true });
  const ms = Date.now() - t0;
  console.log('=== RESEARCH:', topic, `(${ms}ms) ===`);
  console.log('niche:', data.niche);
  console.log('DDG:', data.duckduckgo.length, '| DDG-news:', data.duckduckgoNews.length, '| Bing:', data.bing.length, '| Wiki:', data.wikipedia.length, '| HN:', data.hackernews.length, '| RSS:', data.rss.length, '| Reddit:', data.reddit.length, '| redditViaRss:', data.redditViaRss);
  console.log('stats:', JSON.stringify(data.stats));
  console.log('errors:', data.errors);

  const brief = buildBrief(data);
  console.log('\n=== BRIEF ===');
  console.log('facts:', brief.facts.length, 'events:', brief.recentEvents.length, 'stats:', brief.statistics.length, 'quotes:', brief.quotes.length, 'opinions:', brief.opinions.length);
  console.log('\n--- KEY FACTS (first 6) ---');
  brief.facts.slice(0, 6).forEach((f, i) => console.log(`${i + 1}. ${f}`));
  console.log('\n--- RECENT EVENTS (first 6) ---');
  brief.recentEvents.slice(0, 6).forEach((e, i) => console.log(`${i + 1}. ${e}`));
  console.log('\n--- STATS (first 6) ---');
  brief.statistics.slice(0, 6).forEach((s, i) => console.log(`${i + 1}. ${s}`));

  const evidence = buildEvidenceLedger(data);
  console.log('\n=== EVIDENCE ===');
  console.log('claims:', evidence.claims.length, '| sufficient:', evidence.sufficient, '| sourceCount:', evidence.sourceCount, '| latest:', evidence.latestClaimDate);
  evidence.claims.slice(0, 5).forEach((c) => console.log(`[${c.confidence}] ${c.text.slice(0, 160)}`));

  console.log('\nbriefToPromptText length:', briefToPromptText(brief).length);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
