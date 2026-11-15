'use strict';

// Regression tests for the render wrapper's bounded media re-plan
// (render-vidnets-linux.js): a finished plan with 0 Muse-verified
// real-media scenes used to fail immediately. It now gets exactly one
// fresh submit with fresh queries before the job fails loudly, inside the
// same overall render budget — never a second re-plan, never a delivery
// from the dead first attempt.

const assert = require('node:assert/strict');
const { test } = require('node:test');

const {
  shouldAttemptReplan,
  buildRetryRequest,
  REPLAN_MIN_REMAINING_MS,
} = require('../render-vidnets-linux.js');

test('a 0-real plan earns exactly one re-plan while budget remains', () => {
  assert.equal(REPLAN_MIN_REMAINING_MS, 10 * 60 * 1000);
  assert.equal(
    shouldAttemptReplan({ real: 0, replanAttempted: false, msRemaining: 30 * 60 * 1000 }),
    true,
  );
});

test('the re-plan never runs twice: a second 0-real plan fails loudly', () => {
  assert.equal(
    shouldAttemptReplan({ real: 0, replanAttempted: true, msRemaining: 30 * 60 * 1000 }),
    false,
  );
});

test('the re-plan never runs when the plan already has real media', () => {
  assert.equal(
    shouldAttemptReplan({ real: 3, replanAttempted: false, msRemaining: 30 * 60 * 1000 }),
    false,
  );
});

test('the re-plan never runs when the render budget is nearly spent', () => {
  assert.equal(
    shouldAttemptReplan({ real: 0, replanAttempted: false, msRemaining: 5 * 60 * 1000 }),
    false,
  );
  assert.equal(
    shouldAttemptReplan({
      real: 0, replanAttempted: false, msRemaining: 5 * 60 * 1000, minRemainingMs: 60 * 1000,
    }),
    true,
  );
});

test('the retry request bypasses the shared plan cache for fresh queries', () => {
  const request = {
    title: 'Test title',
    script: 'Test script',
    youtube_accepted_coverage_target: 0.8,
    output_package_dir: '/tmp/pkg',
  };
  const retry = buildRetryRequest(request);
  assert.equal(retry.yt_plan_cache_ttl_seconds, 0);
  assert.equal(retry.muse_replan_attempt, 1);
  assert.equal(retry.title, 'Test title');
  assert.equal(retry.youtube_accepted_coverage_target, 0.8);
  assert.equal(retry.output_package_dir, '/tmp/pkg');
  // The original request is untouched: the first attempt's record stays
  // exactly as submitted.
  assert.equal(request.yt_plan_cache_ttl_seconds, undefined);
  assert.equal(request.muse_replan_attempt, undefined);
  assert.notEqual(retry, request);
});

test('a resume after a re-plan never earns a second one (saved flag is honoured)', () => {
  // Nov 9, 2026 simplification: the re-plan is per JOB, not per wrapper
  // run. The first run writes replanAttempted: 1 to vidnets-job.json when
  // it submits the re-plan; a resume that re-attaches to that file must
  // see the flag and refuse a second re-plan, or a daemon restart after
  // a re-plan would loop through fresh submits forever.
  const { replanAlreadyUsed, shouldAttemptReplan } = require('../render-vidnets-linux.js');
  assert.equal(replanAlreadyUsed(null), false);
  assert.equal(replanAlreadyUsed({}), false);
  assert.equal(replanAlreadyUsed({ replanAttempted: 0 }), false);
  assert.equal(replanAlreadyUsed({ replanAttempted: 1 }), true);
  assert.equal(replanAlreadyUsed({ replanAttempted: true }), true);
  // And the decision function refuses when the flag is set, even with
  // a 0-real plan and plenty of budget left.
  assert.equal(
    shouldAttemptReplan({ real: 0, replanAttempted: true, msRemaining: 60 * 60 * 1000 }),
    false,
  );
});
