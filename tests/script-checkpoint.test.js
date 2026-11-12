'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { materialDigest, decideClaim } = require('../lib/jobRunner');

test('receiving a CMS narration checkpoint does not change the saved job identity', () => {
  const original = { jobId: 'run-checkpoint', title: 'Test', channel: { id: 'channel' } };
  const retry = { ...original, resumeScript: 'Existing narration' };
  assert.equal(materialDigest(original), materialDigest(retry));
  assert.equal(decideClaim({ payload: original, scriptText: 'Existing narration' }, retry).decision, 'resume');
});
