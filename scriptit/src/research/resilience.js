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

function errorRecord(provider, err, retryable = true) {
  return {
    error: err && err.message ? err.message : String(err || 'provider unavailable'),
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
  attempts = 2,
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
      if (attempt < count - 1) await sleep(delayMs * (attempt + 1));
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

module.exports = { runProvider, providerHealth, resetProviderHealth };
