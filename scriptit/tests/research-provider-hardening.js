'use strict';
// Regression tests for the research/scraper hardening sweep (Nov 9 2026).
// Offline: network is stubbed via global.fetch — no live provider is hit.
// Run: node tests/research-provider-hardening.js
const assert = require('assert');
const { runProvider, classifyProviderError, resetProviderHealth } = require('../src/research/resilience');
const { mergeResearch } = require('../src/research/pipeline');
const { buildResearchPlan } = require('../src/research/questionPlanner');
const { ddgSearch } = require('../src/providers/duckduckgo');
const { bingSearch } = require('../src/providers/bing');
const { yandexSearch } = require('../src/providers/yandex');
const { gdeltSearch } = require('../src/providers/freeData');

const realFetch = globalThis.fetch;
function withFetch(stub, fn) {
  globalThis.fetch = stub;
  return Promise.resolve()
    .then(fn)
    .finally(() => { globalThis.fetch = realFetch; });
}
const http403 = async () => ({ ok: false, status: 403, text: async () => '', json: async () => ({}) });

async function main() {
  // 1. Retry on transient abort: fails twice with AbortError, then succeeds.
  resetProviderHealth();
  let calls = 0;
  const flaky = async () => {
    calls += 1;
    if (calls < 3) {
      const err = new Error('This operation was aborted');
      err.name = 'AbortError';
      throw err;
    }
    return [{ title: 'ok', url: 'https://example.com/ok' }];
  };
  const retried = await runProvider('flaky-test', flaky, { attempts: 3, delayMs: 1, timeoutMs: 5000 });
  assert(Array.isArray(retried) && retried.length === 1, 'transient AbortError must be retried until success');
  assert.strictEqual(calls, 3, 'expected 3 attempts for abort-then-success');
  console.log('  ✓ retry on abort (3 attempts, then success)');

  // 2. Fail fast on HTTP 403: a block is not transient, so no retries.
  resetProviderHealth();
  let blockedCalls = 0;
  const blocked = await runProvider('blocked-test', async () => {
    blockedCalls += 1;
    throw new Error('Bing HTTP 403');
  }, { attempts: 3, delayMs: 1, timeoutMs: 5000 });
  assert(blocked && blocked.error, 'exhausted provider must return an error record');
  assert.strictEqual(blockedCalls, 1, 'HTTP 403 must not be retried');
  assert.match(blocked.error, /\[HTTP403\]/, 'error record must carry the error class');
  console.log('  ✓ fail fast on HTTP 403 (1 attempt, [HTTP403] in error)');

  // 3. Exhausted retries: error record names provider + error class.
  resetProviderHealth();
  const dead = await runProvider('dead-test', async () => {
    const err = new Error('socket hang up');
    err.name = 'AbortError';
    throw err;
  }, { attempts: 2, delayMs: 1, timeoutMs: 5000 });
  assert.strictEqual(dead.provider, 'dead-test', 'error record must name the provider');
  assert.match(dead.error, /\[AbortError\]/, 'error record must carry the AbortError class');
  console.log('  ✓ exhausted retries return provider + [AbortError] error record');

  // 4. Provider fallback: one provider throwing must not stop the others.
  resetProviderHealth();
  const [boom, good] = await Promise.all([
    runProvider('boom-test', async () => { throw new Error('DuckDuckGo search failed (html: boom)'); }, { attempts: 1, delayMs: 1, timeoutMs: 5000 }),
    runProvider('good-test', async () => [{ title: 'kept', url: 'https://example.com/kept' }], { attempts: 1, delayMs: 1, timeoutMs: 5000 }),
  ]);
  assert(boom && boom.error, 'failing provider must yield an error record, not throw the batch');
  assert(Array.isArray(good) && good.length === 1, 'succeeding provider results must survive a sibling failure');
  console.log('  ✓ provider fallback: failing provider does not short-circuit the rest');

  // 5. Error classification.
  assert.strictEqual(classifyProviderError(new Error('HTTP 429 from api.gdeltproject.org')).retryable, true, '429 must be retryable');
  assert.strictEqual(classifyProviderError(new Error('HTTP 403')).retryable, false, '403 must not be retryable');
  assert.strictEqual(classifyProviderError(Object.assign(new Error('x'), { status: 503 })).retryable, true, '503 must be retryable');
  assert.strictEqual(classifyProviderError(new Error('HTTP 404')).kind, 'HTTP404', 'kind must name the HTTP status');
  // undici DOMExceptions carry a NUMERIC code (AbortError has code 20):
  // the class must be the name, never "[20]".
  const domAbort = new Error('This operation was aborted');
  domAbort.name = 'AbortError';
  domAbort.code = 20;
  assert.strictEqual(classifyProviderError(domAbort).kind, 'AbortError', 'numeric err.code must not become the error class');
  assert.strictEqual(classifyProviderError(domAbort).retryable, true, 'abort must be retryable');
  // A narrative "HTTP 200" inside the message is not the failure's status.
  assert.strictEqual(classifyProviderError(new Error('HTTP 200 but no parseable results')).kind, 'Error', 'HTTP 200 must not become an error class');
  assert.strictEqual(classifyProviderError(Object.assign(new Error('no parseable results'), { code: 'NO_PARSEABLE_RESULTS' })).kind, 'NO_PARSEABLE_RESULTS', 'string err.code must become the error class');
  console.log('  ✓ error classification (429/503 retryable, 403/404 not, kind=HTTP status)');

  // 6. mergeResearch dedup: same URL twice keeps one row (larger body wins).
  const merged = mergeResearch(
    { bing: [{ url: 'https://example.com/a', title: 'A', articleText: 'short' }], errors: [] },
    { bing: [{ url: 'https://example.com/a?utm_source=x', title: 'A', articleText: 'a much longer body text' }, { url: 'https://example.com/b', title: 'B' }], errors: [] },
  );
  assert.strictEqual(merged.bing.length, 2, 'duplicate URL must be deduped to one row');
  assert.strictEqual(merged.bing.find((i) => i.url.includes('/a')).articleText, 'a much longer body text', 'dedup must keep the larger body');
  console.log('  ✓ mergeResearch dedup drops duplicate URLs, keeps larger body');

  // 7. Research plan: identity question carries a quoted exact-phrase query
  // so niche titles are searched verbatim, not only with generic suffixes.
  const plan = buildResearchPlan({ topic: 'A BNSF Whiteout Crossing', niche: 'general' });
  const identity = plan.questions.find((q) => q.id === 'identity');
  assert(identity.queries.some((q) => q === '"A BNSF Whiteout Crossing"'), 'identity queries must include the quoted exact-phrase topic');
  console.log('  ✓ research plan identity question includes quoted exact-phrase query');

  // 8. DuckDuckGo: total failure throws (visible + retryable); a parseable
  // HTML page returns results.
  await withFetch(http403, async () => {
    await assert.rejects(() => ddgSearch('Amtrak locomotive 160', { limit: 5 }), /DuckDuckGo search failed.*403/);
  });
  console.log('  ✓ ddgSearch throws (not silent []) when all stages fail');
  await withFetch(async () => ({ ok: true, status: 200, text: async () => '<html><body>challenge</body></html>' }), async () => {
    await assert.rejects(
      () => ddgSearch('Amtrak locomotive 160', { limit: 5 }),
      (err) => err.code === 'NO_PARSEABLE_RESULTS',
    );
  });
  console.log('  ✓ ddgSearch throws NO_PARSEABLE_RESULTS on 200-with-no-results pages');
  const ddgHtml = '<a class="result__a" href="https://example.com/amtrak-160">Amtrak locomotive 160</a>'
    + '<a class="result__snippet">A photo of the locomotive</a>';
  await withFetch(async () => ({ ok: true, status: 200, text: async () => ddgHtml }), async () => {
    const rows = await ddgSearch('Amtrak locomotive 160', { limit: 5 });
    assert.strictEqual(rows.length, 1, 'parseable DDG HTML must return rows');
    assert.strictEqual(rows[0].url, 'https://example.com/amtrak-160');
  });
  console.log('  ✓ ddgSearch parses a normal HTML results page');

  // 9. Bing: HTTP 200 with no parseable results throws; a b_algo block parses.
  await withFetch(async () => ({ ok: true, status: 200, text: async () => '<html><body>challenge</body></html>' }), async () => {
    await assert.rejects(
      () => bingSearch('Amtrak locomotive 160', { limit: 5 }),
      (err) => err.code === 'NO_PARSEABLE_RESULTS',
    );
  });
  console.log('  ✓ bingSearch throws on 200-with-no-parseable-results (bot challenge page)');
  const bingHtml = '<li class="b_algo"><h2><a href="https://example.com/amtrak-160">Amtrak locomotive 160</a></h2><p>Photo of the Amtrak locomotive 160</p></li>';
  await withFetch(async () => ({ ok: true, status: 200, text: async () => bingHtml }), async () => {
    const rows = await bingSearch('Amtrak locomotive 160', { limit: 5 });
    assert.strictEqual(rows.length, 1);
    assert.strictEqual(rows[0].url, 'https://example.com/amtrak-160');
  });
  console.log('  ✓ bingSearch parses a normal b_algo block');

  // 10. Yandex: HTTP 403 throws; a serp-item block parses.
  await withFetch(http403, async () => {
    await assert.rejects(() => yandexSearch('Amtrak locomotive 160', { limit: 5 }), /Yandex HTTP 403/);
  });
  console.log('  ✓ yandexSearch throws on HTTP 403 (not silent [])');
  const yandexHtml = '<li class="serp-item"><a class="OrganicTitle-Link" href="https://example.com/y">Y Title</a>'
    + '<div class="OrganicText">Y snip</div></li>';
  await withFetch(async () => ({ ok: true, status: 200, text: async () => yandexHtml }), async () => {
    const rows = await yandexSearch('Amtrak locomotive 160', { limit: 5 });
    assert.strictEqual(rows.length, 1);
    assert.strictEqual(rows[0].url, 'https://example.com/y');
  });
  console.log('  ✓ yandexSearch parses a normal serp-item block');

  // 11. GDELT: 429 surfaces status + Retry-After; a 200 JSON page maps.
  await withFetch(async () => ({
    ok: false, status: 429,
    headers: { get: (name) => (String(name).toLowerCase() === 'retry-after' ? '5' : null) },
    text: async () => '',
  }), async () => {
    await assert.rejects(
      () => gdeltSearch('Amtrak', { limit: 5 }),
      (err) => err.status === 429 && err.retryAfterMs === 5000,
    );
  });
  console.log('  ✓ gdeltSearch 429 carries status + retryAfterMs (5000)');
  await withFetch(async () => ({
    ok: true, status: 200, headers: { get: () => null },
    json: async () => ({ articles: [{ title: 'T', url: 'https://example.com/g', seendate: '20261108000000', domain: 'example.com' }] }),
  }), async () => {
    const rows = await gdeltSearch('Amtrak', { limit: 5 });
    assert.strictEqual(rows.length, 1);
    assert.strictEqual(rows[0].url, 'https://example.com/g');
  });
  console.log('  ✓ gdeltSearch maps a normal JSON page');

  console.log('\nALL RESEARCH-PROVIDER-HARDENING TESTS PASSED');
  process.exit(0);
}

main().catch((err) => {
  console.error('\nTest failed:', err);
  globalThis.fetch = realFetch;
  process.exit(1);
});
