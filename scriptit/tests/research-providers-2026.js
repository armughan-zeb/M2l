'use strict';
// Regression tests for the Nov 9 2026 research-provider fixes.
// Offline: global.fetch is stubbed; RSS is tested against a localhost
// server (127.0.0.1 is in no_proxy). No live provider is hit.
// Run: node tests/research-providers-2026.js
const assert = require('assert');
const fs = require('fs');
const http = require('http');
const path = require('path');
const { runProvider, classifyProviderError, resetProviderHealth } = require('../src/research/resilience');
const { runBatches } = require('../src/utils/concurrency');
const { ddgSearch, isChallengePage } = require('../src/providers/duckduckgo');
const { yandexSearch, isYandexChallengePage } = require('../src/providers/yandex');
const { bingSearch } = require('../src/providers/bing');
const { gdeltSearch } = require('../src/providers/freeData');
const { redditSearch } = require('../src/providers/reddit');
const { fetchFeed } = require('../src/providers/rss');

const realFetch = globalThis.fetch;
function withFetch(stub, fn) {
  globalThis.fetch = stub;
  return Promise.resolve().then(fn).finally(() => { globalThis.fetch = realFetch; });
}
const fixture = (name) => fs.readFileSync(path.join(__dirname, 'fixtures', name), 'utf8');
const okHtml = (html, status = 200) => async () => ({ ok: status >= 200 && status < 300, status, text: async () => html, json: async () => ({}) });

async function main() {
  // 1. DuckDuckGo anomaly interstitial (live sample 2026-11-09, HTTP 202):
  // BOT_CHALLENGE, and fail fast — exactly 1 fetch, no lite/API stages.
  const ddgAnomaly = fixture('ddg-anomaly-2026-11-09.html');
  let ddgCalls = 0;
  await withFetch(async () => { ddgCalls += 1; return { ok: true, status: 202, text: async () => ddgAnomaly }; }, async () => {
    await assert.rejects(
      () => ddgSearch('D.B. Cooper hijacking', { limit: 5 }),
      (err) => err.code === 'BOT_CHALLENGE',
    );
  });
  assert.strictEqual(ddgCalls, 1, 'bot challenge must fail fast after the html stage (no lite/API retries)');
  assert.strictEqual(isChallengePage(ddgAnomaly, 202), true, 'live anomaly sample must detect as challenge');
  assert.strictEqual(isChallengePage(ddgAnomaly, 200), true, 'live anomaly sample must detect as challenge on 200 too');
  assert.strictEqual(
    isChallengePage('<a class="result__a" href="https://example.com/a">Anomaly detection explained</a>', 200),
    false,
    'a real results page mentioning "anomaly" must not detect as challenge',
  );
  console.log('  ✓ ddgSearch fails fast with BOT_CHALLENGE on the live anomaly interstitial (1 fetch)');

  // 2. DuckDuckGo lite anomaly: html stage empty, lite stage challenged —
  // BOT_CHALLENGE after 2 fetches, instant-answer stage never attempted.
  let liteCalls = 0;
  await withFetch(async (url) => {
    liteCalls += 1;
    if (String(url).includes('lite.duckduckgo.com')) return { ok: true, status: 200, text: async () => ddgAnomaly };
    return { ok: true, status: 200, text: async () => '<html><body></body></html>' };
  }, async () => {
    await assert.rejects(
      () => ddgSearch('D.B. Cooper hijacking', { limit: 5 }),
      (err) => err.code === 'BOT_CHALLENGE',
    );
  });
  assert.strictEqual(liteCalls, 2, 'lite challenge must stop before the instant-answer stage');
  console.log('  ✓ ddgSearch fails fast with BOT_CHALLENGE on the lite anomaly page (2 fetches)');

  // 3. DuckDuckGo uddg redirect links still resolve through the parser.
  const ddgHtml = '<a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fcooper&amp;rut=abc">D.B. Cooper</a>'
    + '<a class="result__snippet">The hijacker vanished</a>';
  await withFetch(okHtml(ddgHtml), async () => {
    const rows = await ddgSearch('D.B. Cooper', { limit: 5 });
    assert.strictEqual(rows.length, 1);
    assert.strictEqual(rows[0].url, 'https://example.com/cooper', 'uddg redirect must unwrap to the target URL');
  });
  console.log('  ✓ ddgSearch still parses real results and unwraps uddg redirects');

  // 4. Yandex SmartCaptcha (live sample 2026-11-09): BOT_CHALLENGE, 1 fetch.
  const yandexCaptcha = fixture('yandex-captcha-2026-11-09.html');
  let yandexCalls = 0;
  await withFetch(async () => { yandexCalls += 1; return { ok: true, status: 200, text: async () => yandexCaptcha }; }, async () => {
    await assert.rejects(
      () => yandexSearch('D.B. Cooper hijacking', { limit: 5 }),
      (err) => err.code === 'BOT_CHALLENGE',
    );
  });
  assert.strictEqual(yandexCalls, 1, 'captcha page must fail fast (no parse attempt)');
  assert.strictEqual(isYandexChallengePage(yandexCaptcha), true, 'live captcha sample must detect as challenge');
  assert.strictEqual(
    isYandexChallengePage('<li class="serp-item"><a class="OrganicTitle-Link" href="https://example.com/y">captcha systems compared</a></li>'),
    false,
    'a real results page mentioning "captcha" must not detect as challenge',
  );
  console.log('  ✓ yandexSearch fails fast with BOT_CHALLENGE on the live SmartCaptcha page (1 fetch)');

  // 5. Bing parses the live result blocks (saved 2026-11-09).
  const bingHtml = fixture('bing-results-2026-11-09.html');
  await withFetch(okHtml(bingHtml), async () => {
    const rows = await bingSearch('D.B. Cooper hijacking', { limit: 5 });
    assert.strictEqual(rows.length, 2, 'live Bing fixture must yield both result blocks');
    assert.ok(rows.every((r) => /^https?:\/\//.test(r.url)), 'all Bing rows must have real URLs');
    assert.ok(rows.some((r) => /cooper/i.test(r.title + r.snippet + r.url)), 'Bing rows must be about Cooper');
  });
  console.log('  ✓ bingSearch parses the live Bing result blocks');

  // 5b. Bing decoy page (letter-"D" results for a Cooper query, as served
  // when Bing rate-limits this IP): BING_LOW_RELEVANCE with a Retry-After
  // hint, not silent junk rows.
  const decoyHtml = '<li class="b_algo"><h2><a href="https://en.wikipedia.org/wiki/D">D - Wikipedia</a></h2><p>D is the fourth letter</p></li>'
    + '<li class="b_algo"><h2><a href="https://www.youtube.com/watch?v=x">Letter D song</a></h2><p>Learn the letter D</p></li>';
  await withFetch(okHtml(decoyHtml), async () => {
    await assert.rejects(
      () => bingSearch('D B Cooper', { limit: 5 }),
      (err) => err.code === 'BING_LOW_RELEVANCE' && err.retryAfterMs === 15000,
    );
  });
  console.log('  ✓ bingSearch rejects decoy pages with BING_LOW_RELEVANCE + Retry-After');

  // 5c. Bing pacing: two rapid calls serialize with at least a 5s gap.
  const paceStart = Date.now();
  await withFetch(okHtml(bingHtml), async () => {
    const [a, b] = await Promise.all([
      bingSearch('D B Cooper', { limit: 2 }),
      bingSearch('D B Cooper', { limit: 2 }),
    ]);
    assert.strictEqual(a.length, 2);
    assert.strictEqual(b.length, 2);
  });
  const paceElapsed = Date.now() - paceStart;
  assert.ok(paceElapsed >= 5000, `second Bing call must wait >= 5s after the first, took ${paceElapsed}ms`);
  console.log(`  ✓ bingSearch paces calls (>= 5s gap, took ${paceElapsed}ms)`);

  // 6. BOT_CHALLENGE is non-retryable: runProvider calls the fn once.
  resetProviderHealth();
  assert.strictEqual(classifyProviderError(Object.assign(new Error('x'), { code: 'BOT_CHALLENGE' })).retryable, false, 'BOT_CHALLENGE must not be retryable');
  let challengeCalls = 0;
  const challenged = await runProvider('challenge-test', async () => {
    challengeCalls += 1;
    const err = new Error('DuckDuckGo bot challenge on html');
    err.code = 'BOT_CHALLENGE';
    throw err;
  }, { attempts: 3, delayMs: 1, timeoutMs: 5000 });
  assert(challenged && challenged.error, 'exhausted provider must return an error record');
  assert.strictEqual(challengeCalls, 1, 'BOT_CHALLENGE must not be retried');
  assert.match(challenged.error, /\[BOT_CHALLENGE\]/, 'error record must carry the BOT_CHALLENGE class');
  console.log('  ✓ BOT_CHALLENGE is non-retryable (1 attempt, class in error record)');

  // 7. runBatches preserves order and caps concurrency at 3.
  let active = 0;
  let peak = 0;
  const jobs = Array.from({ length: 7 }, (_, i) => async () => {
    active += 1;
    peak = Math.max(peak, active);
    await new Promise((resolve) => setTimeout(resolve, 30));
    active -= 1;
    return i * 10;
  });
  const ordered = await runBatches(jobs, 3, (fn) => fn());
  assert.ok(peak <= 3, `peak concurrency must be <= 3, saw ${peak}`);
  assert.ok(peak >= 2, `batching must actually parallelize, peak was ${peak}`);
  assert.deepStrictEqual(ordered, [0, 10, 20, 30, 40, 50, 60], 'runBatches must preserve input order');
  console.log('  ✓ runBatches caps concurrency at 3 and preserves order');

  // 8. GDELT timeout is threaded: a hanging fetch aborts at the given budget.
  const hanging = async (url, options = {}) => new Promise((_, reject) => {
    options.signal.addEventListener('abort', () => {
      const err = new Error('This operation was aborted');
      err.name = 'AbortError';
      reject(err);
    });
  });
  const gdeltStart = Date.now();
  await withFetch(hanging, async () => {
    await assert.rejects(() => gdeltSearch('D.B. Cooper', { limit: 5, timeoutMs: 60 }), (err) => err.name === 'AbortError');
  });
  assert.ok(Date.now() - gdeltStart < 2000, 'gdeltSearch must abort near its 60ms budget, not hang');
  console.log('  ✓ gdeltSearch aborts at its threaded timeout (no silent hang)');

  // 9. Reddit fails soft on 403: no throw, blocked flag, empty results.
  let rssCalls = 0;
  const rssStub = async () => { rssCalls += 1; return []; };
  await withFetch(async () => ({
    ok: false, status: 403,
    headers: { get: () => 'application/json' },
    text: async () => '', json: async () => ({}),
  }), async () => {
    const out = await redditSearch('D.B. Cooper', { limit: 5, timeoutMs: 8000, rssSearchFn: rssStub });
    assert.strictEqual(out.blocked, true, '403 must be reported as blocked');
    assert.deepStrictEqual(out.results, [], 'blocked Reddit must yield no results');
    assert.strictEqual(rssCalls, 1, 'RSS fallback must be attempted while budget remains');
  });
  console.log('  ✓ redditSearch fails soft on 403 (blocked flag, no throw)');

  // 10. Reddit RSS fallback is skipped once the budget is mostly consumed.
  rssCalls = 0;
  await withFetch(async (url, options = {}) => {
    await new Promise((resolve) => {
      const timer = setTimeout(resolve, 150);
      options.signal?.addEventListener('abort', () => { clearTimeout(timer); resolve(); });
    });
    const err = new Error('This operation was aborted');
    err.name = 'AbortError';
    throw err;
  }, async () => {
    const out = await redditSearch('D.B. Cooper', { limit: 5, timeoutMs: 100, rssSearchFn: rssStub });
    assert.deepStrictEqual(out.results, []);
    assert.strictEqual(rssCalls, 0, 'RSS fallback must be skipped when the JSON attempt consumed the budget');
  });
  console.log('  ✓ redditSearch skips the RSS fallback when the budget is consumed');

  // 11. RSS fetchFeed honours its timeout against a localhost server:
  // a fast feed parses, a slow feed records an error instead of hanging.
  const feedXml = '<?xml version="1.0"?><rss version="2.0"><channel><title>Local</title>'
    + '<item><title>Cooper hijacking</title><link>https://example.com/cooper</link><description>Hijacker vanished</description></item>'
    + '</channel></rss>';
  const server = http.createServer((req, res) => {
    if (req.url === '/slow.xml') {
      setTimeout(() => { res.writeHead(200, { 'Content-Type': 'application/rss+xml' }); res.end(feedXml); }, 1500);
      return;
    }
    res.writeHead(200, { 'Content-Type': 'application/rss+xml' });
    res.end(feedXml);
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  try {
    const fast = await fetchFeed({ name: 'local', url: `http://127.0.0.1:${port}/feed.xml` }, 5000);
    assert.strictEqual(fast.items.length, 1, 'fast local feed must parse');
    assert.strictEqual(fast.items[0].title, 'Cooper hijacking');
    const slow = await fetchFeed({ name: 'slow', url: `http://127.0.0.1:${port}/slow.xml` }, 300);
    assert.strictEqual(slow.items.length, 0, 'slow feed must yield no items within its timeout');
    assert.ok(slow.error, 'slow feed must record its timeout error');
    console.log('  ✓ RSS fetchFeed honours its timeout (fast parses, slow errors)');
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }

  console.log('\nALL RESEARCH-PROVIDERS-2026 TESTS PASSED');
  process.exit(0);
}

main().catch((err) => {
  console.error('\nTest failed:', err);
  globalThis.fetch = realFetch;
  process.exit(1);
});
