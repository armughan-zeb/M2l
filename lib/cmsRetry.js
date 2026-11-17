'use strict';

/**
 * Transient-CMS retry policy (Nov 9, 2026 hardening).
 *
 * Overnight Nov 8-9 the CMS returned HTTP 502/530 for hours (Cloudflare
 * tunnel down on the CMS side). The daemon died on every restart because
 * resumeActiveJob() re-threw a 530 from its one-shot progress probe, so
 * the recovery watch had to restart it every 10 minutes — and every
 * restart died the same way within ~1s. The poll loop itself already
 * survived (it only warned), but the resume path before it did not.
 *
 * Rule: a transient CMS error (gateway / overload / tunnel-down) must
 * NEVER kill the daemon. Bounded retries with exponential backoff, then
 * keep polling — the saved job and its local checkpoints are retained
 * and resume cleanly when the CMS answers again.
 *
 * Terminal (never retried): 401 (credential revoked), 403, 404, 409
 * (job already settled in the CMS), 400/422 (bad request).
 * Transient: 408, 429, 500, 502, 503, 504, 530, plus transport failures
 * (connection refused/reset, DNS, timeout) which carry no HTTP status.
 */

const TRANSIENT_STATUSES = new Set([408, 429, 500, 502, 503, 504, 530]);

const TRANSIENT_MESSAGE = /could not reach the cms|did not answer in time|artifact upload timed out|fetch failed|ECONNRESET|ECONNREFUSED|ECONNABORTED|EPIPE|ETIMEDOUT|ENOTFOUND|EAI_AGAIN|socket hang up|network error|temporarily unavailable/i;

function isTransientCmsError(error) {
  if (!error) return false;
  if (typeof error.status === 'number') return TRANSIENT_STATUSES.has(error.status);
  return TRANSIENT_MESSAGE.test(String(error.message || error));
}

/** Exponential backoff: base * 2^attempt, capped. attempt is 0-based. */
function retryDelayMs(attempt, { baseMs = 2000, maxMs = 60000 } = {}) {
  const step = Math.max(0, Math.floor(Number(attempt) || 0));
  const base = Math.max(1, Number(baseMs) || 2000);
  const max = Math.max(base, Number(maxMs) || 60000);
  return Math.min(max, base * (2 ** step));
}

function clampInt(value, fallback, min, max) {
  const parsed = Math.floor(Number(value));
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
}

/** Retry tunables, overridable from the environment (see .env.example). */
function cmsRetryOptions(env = process.env) {
  return {
    baseMs: clampInt(env.MUSE_CMS_RETRY_BASE_MS, 2000, 100, 30000),
    maxMs: clampInt(env.MUSE_CMS_RETRY_MAX_MS, 60000, 1000, 300000),
    maxAttempts: clampInt(env.MUSE_CMS_RESUME_ATTEMPTS, 12, 1, 100)
  };
}

module.exports = {
  TRANSIENT_STATUSES,
  isTransientCmsError,
  retryDelayMs,
  cmsRetryOptions
};
