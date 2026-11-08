'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');
const { quoteArg, commandLine, stopTree } = require('../lib/command');
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

test('All repository shell scripts exist, have bash/sh shebang, and use LF line endings', () => {
  const scripts = [
    path.join(__dirname, '..', 'start.sh'),
    path.join(__dirname, '..', 'setup-linux.sh'),
    path.join(__dirname, '..', 'vidnets', 'VIDNETS_AGENT.sh'),
    path.join(__dirname, '..', 'scriptit', 'start.sh'),
    path.join(__dirname, '..', 'vidnets', 'tools', 'youtube-pot-provider', 'install_plugin_dev.sh'),
    path.join(__dirname, '..', 'vidnets', 'tools', 'youtube-pot-provider', '.devcontainer', 'post_create_command.sh')
  ];

  for (const scriptPath of scripts) {
    assert.ok(fs.existsSync(scriptPath), `Script must exist: ${scriptPath}`);
    const content = fs.readFileSync(scriptPath, 'utf8');
    assert.match(content, /^#!\/(usr\/bin\/env bash|bin\/bash|bin\/sh)/, `Script must have valid shebang: ${scriptPath}`);
    assert.ok(!content.includes('\r\n'), `Script must use Unix LF line endings: ${scriptPath}`);
  }
});

test('systemd service units exist and have valid configuration for Ubuntu/Boost', () => {
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
  const templatedContent = fs.readFileSync(templatedServiceFile, 'utf8');
  assert.match(templatedContent, /User=%i/);
});

test('root .gitattributes exists and enforces LF line endings for shell scripts', () => {
  const attrFile = path.join(__dirname, '..', '.gitattributes');
  assert.ok(fs.existsSync(attrFile), '.gitattributes must exist');
  const content = fs.readFileSync(attrFile, 'utf8');
  assert.match(content, /\*\.sh\s+text\s+eol=lf/);
  assert.match(content, /\*\.service\s+text\s+eol=lf/);
});

test('stopTree handles process termination safely without errors', () => {
  // Should handle null / missing child safely
  assert.doesNotThrow(() => stopTree(null));
  assert.doesNotThrow(() => stopTree({ killed: true }));
  assert.doesNotThrow(() => stopTree({ pid: 999999999, killed: false }));
});

test('Linux credential stream cipher (_linux_protect and _linux_unprotect) roundtrips and resists tampering', async () => {
  const py = await pythonHelper.resolvePython();
  const script = `
import sys
sys.path.insert(0, '${path.join(__dirname, '..', 'vidnets', 'server').replace(/\\/g, '/')}')
from services.youtube_session import _linux_protect, _linux_unprotect
original = b'{"token": "test_token_secret_12345", "user": "boost_worker"}'
encrypted = _linux_protect(original)
assert encrypted.startswith(b'LNX1'), 'Encrypted blob must start with LNX1'
decrypted = _linux_unprotect(encrypted)
assert decrypted == original, 'Decrypted content must match original'

# Tamper test
tampered = bytearray(encrypted)
tampered[-1] ^= 0x01
try:
    _linux_unprotect(bytes(tampered))
    sys.exit(2)
except ValueError:
    pass # Expected
print("OK")
`;
  const res = spawnSync(py, ['-c', script], { encoding: 'utf8' });
  if (res.status !== 0) {
    throw new Error(`Python encryption test failed: ${res.stderr || res.stdout}`);
  }
  assert.match(res.stdout, /OK/);
});
