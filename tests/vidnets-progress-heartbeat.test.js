'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { progressReportForPoll } = require('../adapters/vidnets');
const { progressDetail } = require('../lib/vidnetsSubmission');

const base = {
  progress: 61,
  detail: 'Checking media relevance',
  lastProgress: 61,
  lastDetail: 'Checking media relevance',
  lastReportAt: 1000000,
  heartbeatMs: 300000,
};

test('a changed percentage or detail is reported as a change', () => {
  const changedProgress = progressReportForPoll({ ...base, progress: 62, now: 1001000 });
  assert.deepEqual(changedProgress, { progress: 62, detail: 'Checking media relevance', changed: true });
  const changedDetail = progressReportForPoll({ ...base, detail: 'Checking media relevance: 42 reviewed', now: 1001000 });
  assert.deepEqual(changedDetail, { progress: 61, detail: 'Checking media relevance: 42 reviewed', changed: true });
});

test('an unchanged poll inside the heartbeat window reports nothing', () => {
  assert.equal(progressReportForPoll({ ...base, now: 1001000 }), null);
  assert.equal(progressReportForPoll({ ...base, now: 1299999 }), null);
});

test('an unchanged poll reports a heartbeat with the same progress', () => {
  const first = progressReportForPoll({ ...base, now: 1300000 });
  assert.equal(first.progress, 61);
  assert.equal(first.changed, false);
  assert.equal(first.detail, base.detail);
  const second = progressReportForPoll({ ...base, lastReportAt: 1300000, now: 1600000 });
  assert.deepEqual(second, first);
});

test('queue heartbeat text preserves the current stage and genuine counters', () => {
  assert.equal(progressDetail({ progress_text: 'Checking media: 3/6 - still working (43s); please wait' }), 'Checking media: 3/6');
  assert.equal(progressDetail({ progress_text: 'Checking media: 4/6' }), 'Checking media: 4/6');
});

test('the heartbeat interval is honoured from the last report, not from render start', () => {
  // Last report was 100s ago; the 300s window has not elapsed yet.
  assert.equal(progressReportForPoll({ ...base, lastReportAt: 1200000, now: 1300000 }), null);
});
