'use strict';

// Regression tests for the render wrapper's temp-space preflight
// (render-vidnets-linux.js checkTempSpace): /tmp is a 512 MB tmpfs and it
// has filled up before, so a render must fail up front with a clear
// message instead of dying halfway through with a cryptic error.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');

const { checkTempSpace, TEMP_MIN_FREE_MB, TMP_MIN_FREE_MB } = require('../render-vidnets-linux.js');

test('temp preflight passes on this machine and reports both dirs', () => {
  assert.ok(TEMP_MIN_FREE_MB >= 512, 'runtime temp floor must be at least 512 MB');
  assert.ok(TMP_MIN_FREE_MB >= 128, 'tmpfs floor must be at least 128 MB');
  const space = checkTempSpace();
  assert.ok(space.tempDir.length > 0, 'temp dir reported');
  assert.equal(space.tmpDir, '/tmp');
  assert.ok(space.tempFreeMb >= TEMP_MIN_FREE_MB, `runtime temp free: ${space.tempFreeMb} MB`);
  assert.ok(space.tmpFreeMb >= TMP_MIN_FREE_MB, `/tmp free: ${space.tmpFreeMb} MB`);
});

test('temp preflight fails clearly when runtime temp is (simulated) full', () => {
  assert.throws(
    () => checkTempSpace({ tempMinMb: 99999999 }),
    /Temp space too low to render: .* has \d+ MB free, need at least 99999999 MB/,
  );
});

test('temp preflight fails clearly when /tmp is (simulated) full', () => {
  assert.throws(
    () => checkTempSpace({ tmpMinMb: 99999999 }),
    /Temp space too low to render: \/tmp has \d+ MB free, need at least 99999999 MB/,
  );
});

test('temp preflight fails clearly when the temp dir is missing', () => {
  assert.throws(
    () => checkTempSpace({ tempDir: path.join(os.tmpdir(), 'vidnets-temp-missing-xyz') }),
    /Vidnets temp dir is missing/,
  );
});

test('temp preflight honours an explicit temp dir', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vidnets-temp-check-'));
  try {
    const space = checkTempSpace({ tempDir: dir, tempMinMb: 1, tmpMinMb: 1 });
    assert.equal(space.tempDir, dir);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
