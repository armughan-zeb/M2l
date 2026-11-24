/* Render gate: let a render in progress own the machine.
 *
 * Root cause (Berlin Wall, Nov 15): the render held its own slot — the
 * Node render script binds TCP port 47639 for the whole render and the
 * server holds an flock on ~/Vidnets Studio/render.lock while its render
 * call runs — but NOTHING ELSE ever checked either signal. A test suite,
 * the resident inference pool, or a hook-woken worker could start
 * mid-render and starve it for its whole duration (render crawled at 8%
 * frames with ~160 MB free; pytest had to be killed by hand). The
 * render-mode kill at render start is one-shot: work that starts AFTER
 * it runs is untouched.
 *
 * This module is the single shared "is a render running?" check, in the
 * same language-neutral terms on both sides of the pipeline:
 *
 *   1. TCP lease port (default 47639, VIDZEO_RENDER_LEASE_PORT) — held
 *      by vidnets/scripts/render-remotion-manifest.js main() for the
 *      whole Node render. A connect that succeeds means a render owns
 *      the machine right now.
 *   2. Render lock file (VIDNETS_RENDER_LOCK_FILE, else
 *      ~/Vidnets Studio/render.lock — the same rule as
 *      vidnets/server/services/remotion_render_gate.py) — held with
 *      flock by the server while its render call runs. Probed with a
 *      non-blocking `flock -n <file> -c true`: exit 0 means free,
 *      exit 1 means held. A missing file means no server render.
 *
 * Either signal firing means a render is active. Both signals are held
 * by the render itself and released by the OS on crash/reboot (the
 * socket closes, the flock dies with the fd), so there is no stale
 * status file to clean up and no new state for the render to maintain.
 * The Python mirror (vidnets/server/services/render_gate.py) probes
 * the same two signals with the same env vars.
 *
 * Probes never throw and never block a caller: an unprovable signal
 * (no `flock` binary, non-Linux host, connect timeout) is reported as
 * unknown and treated as inactive, so a broken probe degrades to
 * "rely on the other signal", never to a hung test run.
 *
 * Callers:
 *   - `npm test` / `npm run test:vidnets` run `render-gate.js wait`
 *     first: heavy test work waits (bounded) for a render to finish.
 *   - pytest waits in vidnets/conftest.py pytest_sessionstart.
 *   - the resident inference pool supervisor checks `status` each tick
 *     and parks workers while a render is active (see the brief patch
 *     in the Area 3 report — briefs are owned by Area 9).
 *   - the inbox-watch hook skips waking while a render is active
 *     (live-hook patch in the Area 3 report).
 *
 * Env:
 *   VIDZEO_RENDER_LEASE_PORT       TCP lease port (default 47639)
 *   VIDNETS_RENDER_LOCK_FILE      render lock file (default
 *                                 ~/Vidnets Studio/render.lock)
 *   VIDZEO_RENDER_GATE_WAIT_SECONDS  default wait bound (default 1200 = 20 min,
 *                                 matches the Node lease max wait)
 *   VIDZEO_RENDER_GATE=off        `wait` (and the test gates) skip
 *                                 waiting entirely; `status` still
 *                                 reports the truth.
 *
 * CLI:
 *   node render-gate.js status [--json]   exit 0 idle, 2 render active
 *   node render-gate.js wait [--wait-ms N] [--strict] [--json]
 *       exit 0 when idle; on timeout prints a warning and exits 0,
 *       unless --strict (exit 3). A timed-out wait NEVER fails the
 *       caller by default: the render is long, the work still has to
 *       run somewhere — but it runs warned, not blind.
 */
'use strict';

const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');
const { execFile } = require('child_process');

const DEFAULT_LEASE_PORT = 47639;
const DEFAULT_WAIT_MS = 20 * 60 * 1000;
const PORT_PROBE_TIMEOUT_MS = 500;
const FLOCK_TIMEOUT_MS = 5000;
const WAIT_POLL_MS = 2000;

function renderLeasePort(env = process.env) {
  const raw = Number(String(env.VIDZEO_RENDER_LEASE_PORT || '').trim());
  return Number.isFinite(raw) && raw > 0 && raw < 65536 ? Math.floor(raw) : DEFAULT_LEASE_PORT;
}

function defaultLockFile() {
  const base = process.platform === 'win32'
    ? (process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'))
    : os.homedir();
  return path.join(base, 'Vidnets Studio', 'render.lock');
}

function renderLockFile(env = process.env) {
  const explicit = String(env.VIDNETS_RENDER_LOCK_FILE || '').trim();
  return explicit || defaultLockFile();
}

function renderGateWaitMs(env = process.env) {
  const raw = Number(String(env.VIDZEO_RENDER_GATE_WAIT_SECONDS || '').trim());
  if (Number.isFinite(raw) && raw > 0) return Math.floor(raw * 1000);
  return DEFAULT_WAIT_MS;
}

function renderGateEnabled(env = process.env) {
  return String(env.VIDZEO_RENDER_GATE || '').trim().toLowerCase() !== 'off';
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Tri-state port probe: true = something listens (render active),
// false = connection refused (idle), null = could not tell.
function probePort(port, timeoutMs = PORT_PROBE_TIMEOUT_MS) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      try { socket.destroy(); } catch (_) { /* already closed */ }
      resolve(value);
    };
    socket.setTimeout(timeoutMs);
    socket.once('connect', () => done(true));
    socket.once('timeout', () => done(null));
    socket.once('error', () => done(false));
    try {
      socket.connect({ host: '127.0.0.1', port });
    } catch (_) {
      done(null);
    }
  });
}

// Tri-state flock probe: true = lock held (render active), false = free,
// null = could not tell (no flock binary, non-Linux, timeout).
// Never creates the file: a missing lock file means no server render.
function probeLockFile(lockFile, timeoutMs = FLOCK_TIMEOUT_MS) {
  return new Promise((resolve) => {
    if (process.platform === 'win32') {
      resolve(null);
      return;
    }
    let exists = false;
    try { exists = fs.existsSync(lockFile); } catch (_) { exists = false; }
    if (!exists) {
      resolve(false);
      return;
    }
    let child;
    try {
      child = execFile('flock', ['--nonblock', '--exclusive', lockFile, '-c', 'true'],
        { timeout: timeoutMs, windowsHide: true }, (error) => {
          if (!error) return resolve(false); // exit 0: lock was free
          if (typeof error.code === 'number') {
            // util-linux flock exits 1 when --nonblock cannot take the lock.
            return resolve(error.code === 1 ? true : null);
          }
          return resolve(null);
        });
    } catch (_) {
      resolve(null);
      return;
    }
    child.once('error', () => resolve(null));
  });
}

async function isRenderActive(options = {}) {
  const env = options.env || process.env;
  const port = options.port !== undefined ? options.port : renderLeasePort(env);
  const lockFile = options.lockFile !== undefined ? options.lockFile : renderLockFile(env);
  const errors = [];
  let portActive = null;
  let lockActive = null;
  try {
    portActive = await probePort(port, options.portTimeoutMs);
  } catch (error) {
    errors.push(`port probe: ${error.message}`);
    portActive = null;
  }
  try {
    lockActive = await probeLockFile(lockFile, options.flockTimeoutMs);
  } catch (error) {
    errors.push(`lock probe: ${error.message}`);
    lockActive = null;
  }
  const signals = [];
  if (portActive === true) signals.push(`lease port ${port} listening`);
  if (lockActive === true) signals.push(`render lock ${lockFile} held`);
  return {
    active: portActive === true || lockActive === true,
    portActive,
    lockActive,
    port,
    lockFile,
    signals,
    errors,
  };
}

// Wait until no render is active, or the bound expires. Never throws:
// a probe failure inside the loop is reported on the returned status.
async function waitForRenderIdle(options = {}) {
  const env = options.env || process.env;
  const waitMs = options.waitMs !== undefined ? options.waitMs : renderGateWaitMs(env);
  const pollMs = options.pollMs !== undefined ? options.pollMs : WAIT_POLL_MS;
  const onWait = typeof options.onWait === 'function' ? options.onWait : null;
  const startedAt = Date.now();
  let lastStatus = await isRenderActive(options);
  while (lastStatus.active) {
    const waitedMs = Date.now() - startedAt;
    if (waitedMs >= waitMs) {
      return { idle: false, waitedMs, waitMs, status: lastStatus };
    }
    if (onWait) {
      try { onWait(lastStatus, waitedMs); } catch (_) { /* logging must not break the wait */ }
    }
    await sleep(Math.min(pollMs, Math.max(0, waitMs - waitedMs)));
    lastStatus = await isRenderActive(options);
  }
  return { idle: true, waitedMs: Date.now() - startedAt, waitMs, status: lastStatus };
}

function printStatusHuman(status) {
  if (status.active) {
    console.log(`[RenderGate] render ACTIVE: ${status.signals.join('; ')} — background work must yield or wait`);
  } else {
    console.log('[RenderGate] no render active');
  }
  for (const error of status.errors) {
    console.log(`[RenderGate] probe note: ${error}`);
  }
}

async function main(argv) {
  const args = argv.slice(2);
  const command = (args[0] || 'status').toLowerCase();
  const json = args.includes('--json');
  const strict = args.includes('--strict');
  const waitMsArg = args.find((a) => a.startsWith('--wait-ms='));
  const waitSecondsArg = args.find((a) => a.startsWith('--wait-seconds='));
  if (command === 'status') {
    const status = await isRenderActive();
    if (json) console.log(JSON.stringify(status, null, 2));
    else printStatusHuman(status);
    process.exitCode = status.active ? 2 : 0;
    return;
  }
  if (command === 'wait') {
    if (!renderGateEnabled()) {
      console.log('[RenderGate] gate disabled (VIDZEO_RENDER_GATE=off); not waiting');
      process.exitCode = 0;
      return;
    }
    let waitMs;
    if (waitMsArg) waitMs = Number(waitMsArg.split('=')[1]);
    else if (waitSecondsArg) waitMs = Number(waitSecondsArg.split('=')[1]) * 1000;
    else waitMs = renderGateWaitMs();
    if (!Number.isFinite(waitMs) || waitMs <= 0) waitMs = renderGateWaitMs();
    let lastNotice = 0;
    const result = await waitForRenderIdle({
      waitMs,
      onWait: (status, waitedMs) => {
        if (Date.now() - lastNotice >= 30000) {
          lastNotice = Date.now();
          console.log(`[RenderGate] render active (${status.signals.join('; ')}) — waiting ${Math.round(waitedMs / 1000)}s of ${Math.round(waitMs / 1000)}s`);
        }
      },
    });
    if (json) console.log(JSON.stringify(result, null, 2));
    if (result.idle) {
      console.log(`[RenderGate] render finished after ${Math.round(result.waitedMs / 1000)}s; machine is free`);
      process.exitCode = 0;
      return;
    }
    console.log(`[RenderGate] WARNING: render still active after ${Math.round(result.waitedMs / 1000)}s (${result.status.signals.join('; ')}) — proceeding anyway; the render will stay starved, re-check before heavy work`);
    process.exitCode = strict ? 3 : 0;
    return;
  }
  console.error('usage: node render-gate.js status [--json] | wait [--wait-ms N | --wait-seconds N] [--strict] [--json]');
  process.exitCode = 1;
}

if (require.main === module) {
  main(process.argv).catch((error) => {
    console.error(`[RenderGate] failed: ${error.message}`);
    process.exitCode = 1;
  });
}

module.exports = {
  isRenderActive,
  waitForRenderIdle,
  probePort,
  probeLockFile,
  renderLeasePort,
  renderLockFile,
  renderGateWaitMs,
  renderGateEnabled,
  DEFAULT_LEASE_PORT,
  DEFAULT_WAIT_MS,
};
