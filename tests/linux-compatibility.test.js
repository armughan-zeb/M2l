'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('fs');
const path = require('path');
const { quoteArg, commandLine, stopTree } = require('../lib/command');
const pythonHelper = require('../lib/python');
const ffmpeg = require('../lib/ffmpeg');

test('POSIX shell argument quoting correctly escapes strings', () => {
  assert.equal(quoteArg('simple'), 'simple');
  assert.equal(quoteArg('file_123.mp4'), 'file_123.mp4');
  if (process.platform !== 'win32') {
    assert.equal(quoteArg('hello world'), "'hello world'");
    assert.equal(quoteArg("don't fail"), "'don'\\''t fail'");
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

test('ffmpeg discovery runs cleanly', async () => {
  const media = await ffmpeg.availability();
  assert.equal(typeof media.ok, 'boolean');
});

test('All repository shell scripts exist, have bash/sh shebang, and use LF line endings', () => {
  const scripts = [
    path.join(__dirname, '..', 'start.sh'),
    path.join(__dirname, '..', 'start-daemon.sh'),
    path.join(__dirname, '..', 'setup.sh'),
    path.join(__dirname, '..', 'setup-linux.sh'),
    path.join(__dirname, '..', 'docker', 'entrypoint.sh')
  ];

  for (const scriptPath of scripts) {
    assert.ok(fs.existsSync(scriptPath), `Script must exist: ${scriptPath}`);
    const content = fs.readFileSync(scriptPath, 'utf8');
    assert.match(content, /^#!\/(usr\/bin\/env bash|bin\/bash|bin\/sh)/, `Script must have valid shebang: ${scriptPath}`);
    assert.ok(!content.includes('\r\n'), `Script must use Unix LF line endings: ${scriptPath}`);
  }
});

test('systemd service units exist and have valid configuration', () => {
  const serviceFile = path.join(__dirname, '..', 'vidzeo-muse.service');
  assert.ok(fs.existsSync(serviceFile), 'Systemd service file must exist');
  const content = fs.readFileSync(serviceFile, 'utf8');
  assert.match(content, /\[Unit\]/);
  assert.match(content, /\[Service\]/);
  assert.match(content, /ExecStart=/);
  assert.match(content, /muse-agent\.js start/);
  assert.match(content, /Restart=always/);
  assert.ok(!content.includes('User=%I'), 'Non-templated unit must not have User=%I');

  const templatedServiceFile = path.join(__dirname, '..', 'vidzeo-muse@.service');
  assert.ok(fs.existsSync(templatedServiceFile), 'Templated systemd service file must exist');
  assert.match(fs.readFileSync(templatedServiceFile, 'utf8'), /User=%i/);
});

test('root .gitattributes exists and enforces LF line endings for shell scripts', () => {
  const attrFile = path.join(__dirname, '..', '.gitattributes');
  assert.ok(fs.existsSync(attrFile), '.gitattributes must exist');
  const content = fs.readFileSync(attrFile, 'utf8');
  assert.match(content, /\*\.sh\s+text\s+eol=lf/);
  assert.match(content, /\*\.service\s+text\s+eol=lf/);
});

test('stopTree handles process termination safely without errors', () => {
  assert.doesNotThrow(() => stopTree(null));
  assert.doesNotThrow(() => stopTree({ killed: true }));
  assert.doesNotThrow(() => stopTree({ pid: 999999999, killed: false }));
});
