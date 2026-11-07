'use strict';

// Abort-aware settlement also covers injected adapters that ignore their
// signal. Late resolutions cannot start new work or save production results.
function boundedCall(operation, { signal, timeoutMs = 90000, label = 'ScriptIT operation' } = {}) {
  const controller = new AbortController();
  const ownedSignal = signal ? AbortSignal.any([signal, controller.signal]) : controller.signal;
  const timer = setTimeout(() => {
    const error = new Error(`${label} timed out: deadline reached`);
    error.code = 'SCRIPTIT_TIMEOUT';
    controller.abort(error);
  }, Math.max(1, Number(timeoutMs) || 90000));
  let abort;
  const interrupted = new Promise((_, reject) => {
    abort = () => reject(ownedSignal.reason || new Error(`${label} cancelled`));
    if (ownedSignal.aborted) abort();
    else ownedSignal.addEventListener('abort', abort, { once: true });
  });
  const work = Promise.resolve().then(() => {
    ownedSignal.throwIfAborted();
    return operation(ownedSignal);
  });
  return Promise.race([work, interrupted]).finally(() => {
    clearTimeout(timer);
    ownedSignal.removeEventListener('abort', abort);
  });
}

module.exports = { boundedCall };
