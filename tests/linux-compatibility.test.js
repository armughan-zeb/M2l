'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('fs');
const path = require('path');
const { quoteArg, commandLine } = require('../lib/command');
const pythonHelper = require('../lib/python');
const ffmpeg = require('../lib/ffmpeg');
const vidnets = require('../adapters/vidnets');
const config = require('../lib/config');

test('POSIX shell argument quoting correctly escapes strings', () => {
  // Simple alphanumeric needs no quotes
  assert.equal(quoteArg('simple'), 'simple');
  assert.equal(quoteArg('file_123.mp4'), 'file_123.mp4');

  // Spaces and quotes
  if (process.platform !== 'win32') {
    assert.equal(quoteArg('hello world'), "'hello world'");
    assert.equal(quoteArg("don't fail"), "'don'\\''t fail'");
    assert.equal(quoteArg('a "quoted" arg'), "'a \"quoted\" arg'");
    assert.equal(quoteArg('$VAR `cmd`'), "'$VAR `cmd`'");
  } else {
    assert.equal(quoteArg('hello world'), '"hello world"');
  }

  assert.equal(quoteArg(''), '""');
});

test('commandLine combines executable and quoted arguments properly', () => {
  const line = commandLine('python3', ['-u', 'script with space.py', 'arg2']);
  assert.match(line, /^python3 -u /);
  assert.match(line, /script with space\.py/);
});

test('python discovery resolves Python and detects Python 3', async () => {
  const status = await pythonHelper.availability();
  assert.ok(status.binary, 'A python binary candidate should be resolved');
  if (status.ok) {
    assert.match(status.version, /^3\./, 'Resolved Python must be Python 3');
  }
});

test('ffmpeg discovery includes Linux standard search paths', async () => {
  const media = await ffmpeg.availability();
  assert.equal(typeof media.ok, 'boolean');
});

test('Vidnets launcher resolves shell script or python on Linux', () => {
  const originalPlatform = process.platform;
  const originalCommand = process.env.MUSE_VIDNETS_COMMAND;
  delete process.env.MUSE_VIDNETS_COMMAND;

  try {
    const cmd = vidnets.agentCommand();
    assert.ok(cmd, 'Vidnets launcher command should resolve');
  } finally {
    if (originalCommand) process.env.MUSE_VIDNETS_COMMAND = originalCommand;
  }
});

test('Linux shell scripts exist and have bash shebang', () => {
  const scripts = [
    path.join(__dirname, '..', 'start.sh'),
    path.join(__dirname, '..', 'setup-linux.sh'),
    path.join(__dirname, '..', 'vidnets', 'VIDNETS_AGENT.sh'),
    path.join(__dirname, '..', 'scriptit', 'start.sh')
  ];

  for (const scriptPath of scripts) {
    assert.ok(fs.existsSync(scriptPath), `Script must exist: ${scriptPath}`);
    const content = fs.readFileSync(scriptPath, 'utf8');
    assert.ok(content.startsWith('#!/usr/bin/env bash'), `Script must have bash shebang: ${scriptPath}`);
    assert.ok(!content.includes('\r\n'), `Script must use Unix LF line endings: ${scriptPath}`);
  }
});

test('systemd service unit exists and has valid configuration', () => {
  const serviceFile = path.join(__dirname, '..', 'vidzeo-muse.service');
  assert.ok(fs.existsSync(serviceFile), 'Systemd service file must exist');
  const content = fs.readFileSync(serviceFile, 'utf8');
  assert.match(content, /\[Unit\]/);
  assert.match(content, /\[Service\]/);
  assert.match(content, /ExecStart=/);
  assert.match(content, /muse-agent\.js start/);
  assert.match(content, /Restart=always/);
});
