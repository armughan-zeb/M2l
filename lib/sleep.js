'use strict';

/** Abortable delay that always removes its listener. */

function sleep(ms, signal) {
  if (signal?.aborted) return Promise.resolve();
  return new Promise(resolve => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener?.('abort', finish);
      resolve();
    };
    const timer = setTimeout(finish, Math.max(0, Number(ms) || 0));
    signal?.addEventListener?.('abort', finish, { once: true });
  });
}

module.exports = { sleep };
