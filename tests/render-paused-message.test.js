'use strict';

// The wrapper used to blame every non-memory pause on unanswered inference
// requests, including preparation_stall (twice on Broken Rail, inbox empty)
// and backend_restart (server still in startup recovery). The failure
// message must name the real paused_reason instead.

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { describePausedFailure } = require('../render-vidnets-linux.js');

test('preparation stall names the detector and never blames inference', () => {
  const message = describePausedFailure({
    vidnetsJobId: 'vid-1',
    reasonText: 'preparation_stall',
  });
  assert.match(message, /preparation_stall/);
  assert.doesNotMatch(message, /unanswered inference/i);
  assert.doesNotMatch(message, /inbox-watch/i);
});

test('backend restart names the restart and never blames inference', () => {
  const message = describePausedFailure({
    vidnetsJobId: 'vid-1',
    reasonText: 'backend_restart',
  });
  assert.match(message, /backend_restart/);
  assert.doesNotMatch(message, /unanswered inference/i);
});

test('an unknown pause still names its reason without blaming inference', () => {
  const message = describePausedFailure({
    vidnetsJobId: 'vid-1',
    reasonText: 'something_broke',
  });
  assert.match(message, /something_broke/);
  assert.doesNotMatch(message, /unanswered inference/i);
});

test('a memory pause keeps the memory guidance', () => {
  const message = describePausedFailure({
    vidnetsJobId: 'vid-1',
    reasonText: 'waiting_for_memory',
    memoryNote: ' Vidnets memory: held (100 MB available, render needs ~384 MB).',
  });
  assert.match(message, /held for memory/);
  assert.match(message, /100 MB available/);
});

test('a media contract pause keeps the media recovery message', () => {
  const message = describePausedFailure({
    vidnetsJobId: 'vid-1',
    reasonText: 'media_contract',
    detail: 'Real media is missing for scene 3.',
  });
  assert.match(message, /media recovery exhausted/);
  assert.match(message, /Real media is missing for scene 3/);
});
