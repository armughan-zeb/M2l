'use strict';

function retryAfter(headers) {
  const value = headers?.get?.('retry-after');
  if (!value) return 0;
  const seconds = Number(value);
  if (Number.isFinite(seconds)) return Math.max(0, seconds * 1000);
  const date = Date.parse(value);
  return Number.isFinite(date) ? Math.max(0, date - Date.now()) : 0;
}

function providerError(status, data = {}, headers) {
  const originalStatus = Number(data.error?.code) || Number(status) || 502;
  const limitSource = data.error?.metadata?.limit_source;
  const retryAfterMs = retryAfter(headers);
  const reservation = originalStatus === 402 && limitSource === 'openrouter_in_flight_budget'
    && data.error?.metadata?.reason === 'in_flight_budget_exhausted' && retryAfterMs > 0;
  const effectiveStatus = reservation ? 429 : originalStatus;
  const message = reservation ? 'OpenRouter temporarily busy: in-flight budget reservation (429)'
    : 'OpenRouter ' + effectiveStatus + ': ' + (data.error?.message || 'Provider request failed');
  const error = new Error(message);
  Object.assign(error, { status: effectiveStatus, originalStatus, limitSource, retryAfterMs, budgetReservation: reservation });
  return error;
}

function waitForRetry(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason || new Error('Provider retry cancelled'));
    const finish = () => { signal?.removeEventListener('abort', abort); resolve(); };
    const abort = () => { clearTimeout(timer); signal?.removeEventListener('abort', abort); reject(signal.reason || new Error('Provider retry cancelled')); };
    const timer = setTimeout(finish, ms);
    signal?.addEventListener('abort', abort, { once: true });
  });
}

module.exports = { providerError, retryAfter, waitForRetry };
