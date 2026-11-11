'use strict';

// Bounded-parallel task runner.
//
// The VM reaches the internet through an egress proxy that serializes
// concurrent connections: sequential requests answer in 1-3s, but a burst
// of 10+ parallel fetches makes them all stall and time out (seen live:
// an 8-provider research burst where Bing, Wikipedia, Reddit and X all
// timed out at once, while the same calls succeed sequentially). Small
// batches keep every provider inside its timeout instead of collapsing
// the whole pass.
//
// Preserves input order. A task that throws yields its error at its own
// index — callers pass runProvider thunks, which already return error
// records instead of throwing, so a failing provider never aborts the
// batch.
async function runBatches(items, size, fn) {
  const list = Array.isArray(items) ? items : [];
  const out = new Array(list.length);
  const workers = Math.max(1, Math.min(size || 3, list.length || 1));
  let next = 0;
  async function worker() {
    for (;;) {
      const i = next;
      next += 1;
      if (i >= list.length) return;
      try {
        out[i] = await fn(list[i], i);
      } catch (err) {
        out[i] = err;
      }
    }
  }
  await Promise.all(Array.from({ length: workers }, () => worker()));
  return out;
}

module.exports = { runBatches };
