'use strict';

const states = new Map();

function stateFor(name) {
  const key = String(name || 'unknown').toLowerCase();
  if (!states.has(key)) {
    states.set(key, {
      provider: key,
      successes: 0,
      failures: 0,
      lastError: '',
      lastSuccessAt: '',
      lastFailureAt: '',
      openUntil: 0,
      consecutiveFailures: 0,
    });
  }
  return states.get(key);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, Math.max(0, ms || 0)));
}

// Classify a provider failure so the log names it and the retry loop spends
// its budget where a retry can help. Transient: socket aborts (the undici +
// egress-proxy aborts seen on this VM), timeouts, 429/408 and 5xx. Not
// retryable: other 4xx — a 403/404 from a scraper is a block or a dead
// endpoint, and retrying it twice only burns the research budget.
function classifyProviderError(err) {
  const message = String((err && err.message) || err || '');
  const status = Number(err && err.status);
  const httpMatch = message.match(/HTTP\s+(\d{3})/);
  const rawStatus = Number.isFinite(status) && status ? status : (httpMatch ? Number(httpMatch[1]) : 0);
  // Only real error statuses classify: messages like "HTTP 200 but no
  // parseable results" mention 200 as narrative, not as the failure.
  const httpStatus = rawStatus >= 400 ? rawStatus : 0;
  // Prefer err.name: undici DOMExceptions carry a NUMERIC code (AbortError
  // has code 20), and using it raw produced error classes like "[20]".
  // A string code (PROVIDER_TIMEOUT, ECONNRESET, NO_PARSEABLE_RESULTS) is
  // still meaningful.
  let kind = (err && err.name) || 'Error';
  if (err && typeof err.code === 'string' && err.code) kind = err.code;
  if (httpStatus) kind = `HTTP${httpStatus}`;
  else if (/timed out after/i.test(message)) kind = 'PROVIDER_TIMEOUT';
  else if (kind === 'Error' && /aborted|abort/i.test(message)) kind = 'AbortError';
  const retryable = !(
    (httpStatus >= 400 && httpStatus < 500 && httpStatus !== 408 && httpStatus !== 429)
    || /circuit open/i.test(message)
    // A bot-challenge page (DuckDuckGo anomaly interstitial, Yandex
    // SmartCaptcha) is the egress IP being gated, not a transient error:
    // retrying it 3x only burns the research budget and hammers the
    // proxy. Fail fast; the other providers still run.
    || kind === 'BOT_CHALLENGE'
  );
  return { kind, httpStatus: httpStatus || null, retryable };
}

function errorRecord(provider, err, retryable = true) {
  const { kind } = classifyProviderError(err);
  return {
    // Keep the `[Kind]` prefix in the message itself: the researcher formats
    // provider errors as `${provider}: ${error}`, so the class must travel
    // inside that string to be visible in research.errors / the CLI log.
    error: `[${kind}] ${err && err.message ? err.message : String(err || 'provider unavailable')}`,
    provider,
    retryable,
  };
}

function timeoutError(provider, timeoutMs) {
  const err = new Error(`${provider} timed out after ${Math.round(timeoutMs / 1000)}s`);
  err.code = 'PROVIDER_TIMEOUT';
  return err;
}

async function callWithTimeout(fn, timeoutMs, provider) {
  if (!timeoutMs || timeoutMs <= 0) return fn();
  let timer;
  try {
    return await Promise.race([
      Promise.resolve().then(fn),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(timeoutError(provider, timeoutMs)), timeoutMs);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

async function runProvider(name, fn, {
  attempts = 3,
  delayMs = 500,
  timeoutMs = 20000,
  openAfter = 3,
  cooldownMs = 30000,
} = {}) {
  const provider = String(name || 'unknown').toLowerCase();
  const state = stateFor(provider);
  if (state.openUntil > Date.now()) {
    return errorRecord(provider, new Error(`circuit open until ${new Date(state.openUntil).toISOString()}`));
  }

  let lastError = null;
  const count = Math.max(1, Number(attempts) || 1);
  for (let attempt = 0; attempt < count; attempt += 1) {
    try {
      const result = await callWithTimeout(fn, timeoutMs, provider);
      if (Array.isArray(result) || (result && typeof result === 'object' && (!result.error || Array.isArray(result.results)))) {
        state.successes += 1;
        state.consecutiveFailures = 0;
        state.openUntil = 0;
        state.lastSuccessAt = new Date().toISOString();
        return result;
      }
      throw new Error('provider returned an empty or malformed result');
    } catch (err) {
      lastError = err;
      state.failures += 1;
      state.consecutiveFailures += 1;
      state.lastError = err && err.message ? err.message : String(err);
      state.lastFailureAt = new Date().toISOString();
      if (state.consecutiveFailures >= openAfter) state.openUntil = Date.now() + cooldownMs;
      const { retryable } = classifyProviderError(err);
      if (!retryable || attempt >= count - 1) break;
      // Exponential backoff with jitter. A 429 may carry the server's own
      // Retry-After (attached as retryAfterMs by freeData.getJson); honour
      // it, capped so one slow provider cannot eat the whole research pass.
      const backoff = delayMs * (2 ** attempt) + Math.random() * 250;
      const retryAfter = Number(err && err.retryAfterMs);
      const waitMs = Number.isFinite(retryAfter) && retryAfter > 0
        ? Math.max(backoff, Math.min(retryAfter, 10000))
        : backoff;
      await sleep(waitMs);
    }
  }
  return errorRecord(provider, lastError);
}

function providerHealth() {
  return [...states.values()].map((state) => ({
    ...state,
    circuit: state.openUntil > Date.now() ? 'open' : 'closed',
  }));
}

function resetProviderHealth() {
  states.clear();
}

module.exports = { runProvider, providerHealth, resetProviderHealth, classifyProviderError };
