'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const log = require('../lib/log');
const { runProcess } = require('../lib/museBridgeInfer');

test('runProcess streams cleaned subprocess status lines into daemon log ring', async () => {
  await runProcess(
    process.execPath,
    ['-e', 'console.log("\\x1b[32mdownloading asset 1/3\\x1b[0m"); console.error("muxing stream ok");'],
    { label: 'live-log-probe', logIntervalMs: 0 }
  );

  const lines = log.recentLines(20);
  assert.ok(lines.some((line) => line.includes('[live-log-probe] downloading asset 1/3')));
  assert.ok(lines.some((line) => line.includes('[live-log-probe] muxing stream ok')));
});
