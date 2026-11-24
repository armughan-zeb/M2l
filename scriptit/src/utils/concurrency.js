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

// Deadline-aware variant of runBatches.
//
// Same worker-pool shape, but workers stop claiming new tasks once the
// deadline passes (or the outer signal aborts) and the run resolves with
// the partial results instead of running to completion. Tasks that never
// started are filled with a budget-exhausted error record so callers can
// tell "skipped for time" apart from "provider failed".
//
// This exists because a research pass used to be all-or-nothing: when the
// pass hit its outer timeout, boundedCall rejected and the pipeline threw
// away everything the reliable providers had already returned — Bing,
// Wikipedia and RSS results included — and the writer failed on empty
// evidence. With this runner a slow pass still delivers its fast
// providers' data; the gated providers simply report as skipped.
function budgetExhaustedRecord() {
  return { error: 'Research pass stopped early: budget exhausted before this provider ran', provider: 'research-pass' };
}

async function runBatchesWithDeadline(items, size, fn, { deadlineAt = 0, signal } = {}) {
  const list = Array.isArray(items) ? items : [];
  const out = new Array(list.length);
  const workers = Math.max(1, Math.min(size || 3, list.length || 1));
  let next = 0;
  const pastDeadline = () => (deadlineAt > 0 && Date.now() >= deadlineAt) || signal?.aborted === true;
  async function worker() {
    for (;;) {
      if (pastDeadline()) {
        // Fill every unclaimed slot so the result keeps input order and
        // length; a later worker that already claimed work still finishes
        // its own task below.
        for (let i = next; i < list.length; i += 1) {
          if (out[i] === undefined) out[i] = budgetExhaustedRecord();
        }
        return;
      }
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
  for (let i = 0; i < list.length; i += 1) {
    if (out[i] === undefined) out[i] = budgetExhaustedRecord();
  }
  return out;
}

module.exports = { runBatches, runBatchesWithDeadline };
