/* Live smoke test for ScriptIT
 * Runs DuckDuckGo, RSS, Reddit, the full research pipeline, and (optionally) OpenRouter.
 * Usage:
 *   node test/live.js                       -> test research only
 *   node test/live.js <openrouter_key>      -> also test OpenRouter with a tiny prompt
 */

const path = require('path');
const os = require('os');

async function timed(label, fn) {
  const t0 = Date.now();
  try {
    const out = await fn();
    const ms = Date.now() - t0;
    const summary = typeof out === 'string' ? out.slice(0, 120) : JSON.stringify(out).slice(0, 200);
    console.log(`  OK  ${label}  (${ms}ms)  -> ${summary}`);
    return { ok: true, out, ms };
  } catch (err) {
    const ms = Date.now() - t0;
    console.log(`  FAIL ${label}  (${ms}ms)  -> ${err.message || err}`);
    return { ok: false, err, ms };
  }
}

async function main() {
  const apiKey = process.argv[2] || process.env.OPENROUTER_API_KEY || null;
  const topic = process.argv[3] || 'Lionel Messi';
  const model = process.argv[4] || 'meta-llama/llama-3.1-8b-instruct';

  console.log('========================================');
  console.log(` ScriptIT Live Test`);
  console.log(` Topic: ${topic}`);
  console.log(` OpenRouter key: ${apiKey ? 'provided' : 'not provided'}`);
  console.log('========================================\n');

  console.log('[1] DuckDuckGo');
  const ddg = require('../src/providers/duckduckgo');
  const r1 = await timed('ddgSearch(topic)', () => ddg.ddgSearch(topic, { limit: 6 }));
  console.log(`     results: ${Array.isArray(r1.out) ? r1.out.length : 0}`);
  if (Array.isArray(r1.out) && r1.out[0]) {
    console.log(`     sample: "${r1.out[0].title}"`);
  }

  console.log('\n[2] RSS feeds');
  const rss = require('../src/providers/rss');
  const r2 = await timed('searchRss(topic)', () => rss.searchRss(topic, { limit: 8 }));
  console.log(`     results: ${Array.isArray(r2.out) ? r2.out.length : 0}`);
  if (Array.isArray(r2.out) && r2.out[0]) {
    console.log(`     top: "${r2.out[0].title}" (score=${r2.out[0].score}, src=${r2.out[0].source})`);
  }

  console.log('\n[2b] Bing search');
  const bing = require('../src/providers/bing');
  const rb = await timed('bingSearch(topic)', () => bing.bingSearch(topic, { limit: 6 }));
  console.log(`     results: ${Array.isArray(rb.out) ? rb.out.length : 0}`);
  if (Array.isArray(rb.out) && rb.out[0]) {
    console.log(`     sample: "${rb.out[0].title}" (${rb.out[0].url.slice(0, 60)})`);
  }

  console.log('\n[2c] Wikipedia (search + full-text extract)');
  const wiki = require('../src/providers/wikipedia');
  const rw = await timed('wikipediaDeep(topic)', () => wiki.wikipediaDeep(topic, { limit: 2 }));
  const wikiItems = Array.isArray(rw.out) ? rw.out : [];
  console.log(`     results: ${wikiItems.length}`);
  if (wikiItems[0]) {
    console.log(`     top: "${wikiItems[0].title}" (${(wikiItems[0].articleText || wikiItems[0].snippet || '').length} chars of text)`);
  }

  console.log('\n[3] Reddit JSON (often blocked without auth)');
  const red = require('../src/providers/reddit');
  const r3 = await timed('redditSearch(topic)', () => red.redditSearch(topic, { limit: 5 }));
  console.log(`     results: ${r3.out?.results?.length || 0}${r3.out?.rateLimited ? '  (rate limited)' : ''}${r3.out?.blocked ? '  (blocked, expected)' : ''}`);
  if (r3.out?.error) console.log(`     note: ${r3.out.error}`);
  if (r3.out?.results?.[0]) {
    console.log(`     top: "${r3.out.results[0].title}" (r/${r3.out.results[0].subreddit})`);
  }

  console.log('\n[4] Full research pipeline');
  const { gatherResearch } = require('../src/agents/researcher');
  const r4 = await timed('gatherResearch(topic)', () => gatherResearch(topic, { includeReddit: true }));
  if (r4.ok) {
    const data = r4.out;
    console.log(`     DDG: ${data.duckduckgo.length}, DDG-news: ${data.duckduckgoNews.length}, Bing: ${data.bing.length}, RSS: ${data.rss.length}, Wiki: ${data.wikipedia.length}, HN: ${data.hackernews.length}, Reddit: ${data.reddit.length}`);
    if (data.stats) console.log(`     stats: sources=${data.stats.sources} articlesFetched=${data.stats.articlesFetched} freshest=${data.stats.freshest}`);
    if (data.errors.length) console.log(`     errors: ${data.errors.join(' | ')}`);
  }

  console.log('\n[5] Compressor');
  const { buildBrief, briefToPromptText } = require('../src/agents/compressor');
  let briefText = '';
  if (r4.ok) {
    const brief = buildBrief(r4.out);
    briefText = briefToPromptText(brief);
    console.log(`  OK  buildBrief  -> facts=${brief.facts.length} events=${brief.recentEvents.length} stats=${brief.statistics.length} quotes=${brief.quotes.length} opinions=${brief.opinions.length}`);
    console.log(`  OK  briefToPromptText  -> ${briefText.length} chars`);
    if (briefText.length > 0) {
      console.log('     preview:');
      console.log(briefText.split('\n').slice(0, 6).map((l) => '       ' + l).join('\n'));
    }
  } else {
    console.log('  SKIP (no research data)');
  }

  console.log('\n[6] Niche detection');
  const { detectNiche, suggestFormat } = require('../src/agents/nicheDetector');
  const n = detectNiche(topic);
  console.log(`  OK  detectNiche  -> ${n.niche} (confidence ${n.confidence.toFixed(2)}), suggested format: ${suggestFormat(n.niche)}`);

  console.log('\n[7] Duration → words');
  const { wordsForDuration } = require('../src/utils/duration');
  for (const d of ['3min', '5min', '10min', '20min', '30min']) {
    console.log(`  OK  ${d.padEnd(6)} -> ${wordsForDuration(d)} words`);
  }

  if (apiKey) {
    console.log('\n[8] OpenRouter');
    const { chatCompletion } = require('../src/providers/openrouter');
    const r8 = await timed(`chatCompletion(${model})`, () => chatCompletion({
      apiKey,
      model,
      messages: [
        { role: 'system', content: 'You are a test. Respond in one short sentence.' },
        { role: 'user', content: `In one sentence, what is ${topic}?` },
      ],
      temperature: 0.5,
      maxTokens: 120,
    }));
    if (r8.ok) {
      console.log(`     response: ${r8.out.content}`);
      if (r8.out.usage) console.log(`     usage: ${JSON.stringify(r8.out.usage)}`);
    }
  } else {
    console.log('\n[8] OpenRouter: SKIPPED (no API key provided)');
  }

  if (apiKey && briefText) {
    console.log('\n[9] End-to-end (real generation)');
    process.env.NODE_PATH = path.resolve(__dirname, '..');
    const electron = require('electron');
    const Store = require('electron-store');
    const store = new Store({ name: 'scriptit-test', encryptionKey: 'test-key' });
    store.set('apiKey', apiKey);
    const sw = require('../src/agents/scriptWriter');
    const r9 = await timed('generateScript(end-to-end)', () => sw.generateScript({
      topic,
      contentType: 'documentary',
      voice: 'cinematic',
      duration: '3min',
      model,
      apiKey,
      useCache: false,
    }));
    if (r9.ok) {
      const { script, meta } = r9.out;
      console.log(`     content type: ${meta.contentType}, target: ${meta.wordTarget} words`);
      console.log(`     script length: ${script.length} chars`);
      console.log(`     sources used: ${meta.sources.length}`);
      console.log(`     quality: pass=${meta.quality.passes}, score=${meta.quality.score}, hook=${meta.quality.hook}, audit=${meta.quality.factualAudit}`);
      console.log('     preview:');
      console.log(script.split('\n').slice(0, 12).map((l) => '       ' + l).join('\n'));
      console.log('     ...');
    }
  } else {
    console.log('\n[9] End-to-end: SKIPPED (need API key + research)');
  }

  console.log('\n========================================');
  console.log(' Done.');
  console.log('========================================');
}

main().catch((e) => {
  console.error('FATAL:', e);
  process.exit(1);
});
