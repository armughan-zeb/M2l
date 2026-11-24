const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { execFile, spawn } = require('node:child_process');
const {
  isRenderActive,
  waitForRenderIdle,
  probePort,
  probeLockFile,
  renderLeasePort,
  renderGateWaitMs,
  DEFAULT_LEASE_PORT,
} = require('../scripts/render-gate');

const GATE_PATH = path.join(__dirname, '..', 'scripts', 'render-gate.js');

function tmpLockFile() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vidzeo-render-gate-'));
  return path.join(dir, 'render.lock');
}

function listenEphemeral() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port }));
  });
}

function closeServer(server) {
  return new Promise((resolve) => server.close(() => resolve()));
}

async function pollUntil(fn, timeoutMs = 2000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (await fn()) return true;
    if (Date.now() >= deadline) return false;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

function runCli(args, env) {
  return new Promise((resolve) => {
    execFile(process.execPath, [GATE_PATH, ...args], { env: { ...process.env, ...env } },
      (error, stdout, stderr) => resolve({ code: error ? error.code : 0, stdout, stderr }));
  });
}

test('idle when the lease port is closed and the lock file is missing', async () => {
  const { server, port } = await listenEphemeral();
  await closeServer(server);
  const lockFile = path.join(os.tmpdir(), 'vidzeo-render-gate-no-such-file.lock');
  try { fs.unlinkSync(lockFile); } catch (_) { /* already absent */ }
  const status = await isRenderActive({ port, lockFile });
  assert.equal(status.active, false);
  assert.equal(status.portActive, false);
  assert.equal(status.lockActive, false);
  assert.deepEqual(status.signals, []);
});

test('a listener on the lease port means a render is active', async () => {
  const { server, port } = await listenEphemeral();
  const lockFile = tmpLockFile();
  try {
    assert.equal(await probePort(port), true);
    const status = await isRenderActive({ port, lockFile });
    assert.equal(status.active, true);
    assert.equal(status.portActive, true);
    assert.equal(status.lockActive, false);
    assert.match(status.signals.join(' '), new RegExp(`lease port ${port}`));
  } finally {
    await closeServer(server);
  }
  const after = await isRenderActive({ port, lockFile });
  assert.equal(after.active, false);
});

test('a held render lock file means a render is active', async () => {
  const lockFile = tmpLockFile();
  fs.writeFileSync(lockFile, '0');
  const { server, port } = await listenEphemeral();
  await closeServer(server);
  // Hold the lock in a child process, exactly like the server's flock.
  // Detached so the SIGKILL below hits the whole process group: `flock`
  // forks `sleep`, and the child inherits the lock fd — killing `flock`
  // alone would leave `sleep` holding the lock for 30s.
  const holder = spawn('flock', [lockFile, 'sleep', '30'], { detached: true, stdio: 'ignore' });
  try {
    assert.equal(await pollUntil(async () => (await probeLockFile(lockFile)) === true), true,
      'flock holder did not take the lock');
    const status = await isRenderActive({ port, lockFile });
    assert.equal(status.active, true);
    assert.equal(status.lockActive, true);
    assert.equal(status.portActive, false);
    assert.match(status.signals.join(' '), /render lock .* held/);
  } finally {
    try { process.kill(-holder.pid, 'SIGKILL'); } catch (_) { holder.kill('SIGKILL'); }
    assert.equal(await pollUntil(async () => (await probeLockFile(lockFile)) === false), true,
      'lock was not released after killing the holder');
  }
  const after = await isRenderActive({ port, lockFile });
  assert.equal(after.active, false);
});

test('waitForRenderIdle returns at once when idle and times out while busy', async () => {
  const { server, port } = await listenEphemeral();
  const lockFile = tmpLockFile();
  try {
    const busy = await waitForRenderIdle({ port, lockFile, waitMs: 300, pollMs: 50 });
    assert.equal(busy.idle, false);
    assert.ok(busy.waitedMs >= 250, `waited ${busy.waitedMs}ms, expected >= 250ms`);
  } finally {
    await closeServer(server);
  }
  const idle = await waitForRenderIdle({ port, lockFile, waitMs: 1000, pollMs: 50 });
  assert.equal(idle.idle, true);
  assert.ok(idle.waitedMs < 1000);
});

test('env parsing falls back to defaults on bad values', () => {
  assert.equal(renderLeasePort({ VIDZEO_RENDER_LEASE_PORT: 'not-a-port' }), DEFAULT_LEASE_PORT);
  assert.equal(renderLeasePort({ VIDZEO_RENDER_LEASE_PORT: '70000' }), DEFAULT_LEASE_PORT);
  assert.equal(renderLeasePort({ VIDZEO_RENDER_LEASE_PORT: '47639' }), 47639);
  assert.equal(renderGateWaitMs({ VIDZEO_RENDER_GATE_WAIT_SECONDS: '60' }), 60000);
  assert.equal(renderGateWaitMs({ VIDZEO_RENDER_GATE_WAIT_SECONDS: 'junk' }), 20 * 60 * 1000);
});

test('CLI status exits 2 while a render holds the lease, 0 when idle', async () => {
  const { server, port } = await listenEphemeral();
  const lockFile = tmpLockFile();
  const env = {
    VIDZEO_RENDER_LEASE_PORT: String(port),
    VIDNETS_RENDER_LOCK_FILE: lockFile,
  };
  try {
    const busy = await runCli(['status'], env);
    assert.equal(busy.code, 2);
    assert.match(busy.stdout, /render ACTIVE/);
    const json = await runCli(['status', '--json'], env);
    assert.equal(json.code, 2);
    assert.equal(JSON.parse(json.stdout).active, true);
  } finally {
    await closeServer(server);
  }
  const idle = await runCli(['status'], env);
  assert.equal(idle.code, 0);
  assert.match(idle.stdout, /no render active/);
});

test('CLI wait times out loudly: exit 0 by default, 3 with --strict', async () => {
  const { server, port } = await listenEphemeral();
  const lockFile = tmpLockFile();
  const env = {
    VIDZEO_RENDER_LEASE_PORT: String(port),
    VIDNETS_RENDER_LOCK_FILE: lockFile,
  };
  try {
    const lenient = await runCli(['wait', '--wait-ms=300'], env);
    assert.equal(lenient.code, 0);
    assert.match(lenient.stdout, /WARNING: render still active/);
    const strict = await runCli(['wait', '--wait-ms=300', '--strict'], env);
    assert.equal(strict.code, 3);
    assert.match(strict.stdout, /WARNING: render still active/);
  } finally {
    await closeServer(server);
  }
  const idle = await runCli(['wait', '--wait-ms=300'], env);
  assert.equal(idle.code, 0);
  assert.match(idle.stdout, /machine is free/);
});
