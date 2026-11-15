'use strict';

/**
 * muse-inference-spool.cjs — resident Muse inference transport (spool).
 *
 * This is the MUSE_INFERENCE_COMMAND a fresh Muse worker points the daemon
 * at. It is NOT an inference engine: it cannot judge anything itself. It is
 * a rendezvous point between the daemon's resident inbox
 * (lib/museInbox.js, 1s poll + fs.watch, concurrency 8) and the resident
 * inference worker — a long-lived Muse agent that polls this spool and
 * answers each request with its own real inference (vision + text).
 *
 *   daemon --resident inbox--> this script --spool files--> worker agent
 *          <--answer (stdout)--- this script <--answer file-- worker agent
 *
 * Protocol (matches adapters/vidnets.js runInferenceCommand):
 *   stdin:  { type: 'inference', request, instructions, responseFormat }
 *   stdout: the answer content (plain text, or JSON when the request's
 *           response_format is structured). Printed verbatim from the
 *           worker's answer file.
 *
 * Exit codes:
 *   0  answer delivered on stdout.
 *   1  the request expired, the worker reported an error (see
 *      <id>.error.json), or the input was unusable. The resident inbox
 *      records this as a failed request, exactly as it would for any
 *      transport failure.
 *   3  MUSE_TRANSPORT_UNAVAILABLE — the resident worker is not running
 *      (its heartbeat is missing or stale). The resident inbox treats
 *      this specially: it leaves the bridge request PENDING so the host
 *      hook can still answer it, instead of failing the request.
 *
 * Spool location (same rule the worker brief uses, so both sides agree):
 *   MUSE_INFERENCE_SPOOL_DIR, else <bridge-dir>/../inference-spool where
 *   the bridge dir comes from VIDNETS_AGENT_BRIDGE_DIR / VIDZEO_BRIDGE_ROOT
 *   / MUSE_BRIDGE_ROOT / SCRIPTIT_BRIDGE_ROOT, else ./inference-spool.
 * Files are <request-id>.request.json / .answer.json / .error.json plus
 * worker-alive.json (the worker's heartbeat, written every ~10s).
 *
 * The worker NEVER answers from this file: it is a dumb pipe. Any answer
 * that arrives here was composed by the resident worker agent. There is no
 * stub, no fixed score, and no auto-approve anywhere in this path.
 */

const fs = require('fs');
const path = require('path');

const UNAVAILABLE = 'MUSE_TRANSPORT_UNAVAILABLE';
const HEARTBEAT_MAX_AGE_MS = 90000;
const POLL_MS = 250;
const REQUEST_ID = /^[0-9a-f]{32}$/;

function spoolDir() {
  const explicit = String(process.env.MUSE_INFERENCE_SPOOL_DIR || '').trim();
  if (explicit) return explicit;
  for (const key of ['VIDNETS_AGENT_BRIDGE_DIR', 'VIDZEO_BRIDGE_ROOT', 'MUSE_BRIDGE_ROOT', 'SCRIPTIT_BRIDGE_ROOT']) {
    const bridge = String(process.env[key] || '').trim();
    if (bridge) return path.resolve(path.join(bridge, '..', 'inference-spool'));
  }
  return path.resolve(path.join(process.cwd(), 'inference-spool'));
}

function atomicWrite(file, text) {
  const tmp = file + '.tmp-' + process.pid;
  fs.writeFileSync(tmp, text, { encoding: 'utf8', mode: 0o600 });
  fs.renameSync(tmp, file);
}

function heartbeatAgeMs(dir) {
  try {
    const raw = JSON.parse(fs.readFileSync(path.join(dir, 'worker-alive.json'), 'utf8'));
    const updatedAt = Number(raw?.updatedAt);
    if (!Number.isFinite(updatedAt)) return Infinity;
    return Date.now() - updatedAt;
  } catch {
    return Infinity;
  }
}

function fail(code, message) {
  process.stderr.write(message + '\n');
  process.exit(code);
}

async function main() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  let input;
  try {
    input = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    fail(1, 'Muse inference spool: stdin was not valid JSON');
  }
  const request = input?.request || {};
  const id = String(request.id || '');
  if (!REQUEST_ID.test(id)) {
    fail(1, 'Muse inference spool: request has no valid 32-hex id');
  }
  const dir = spoolDir();
  fs.mkdirSync(dir, { recursive: true });

  // Fail fast when the worker is not running: the resident inbox leaves
  // the bridge request pending for the hook instead of failing it.
  const firstAge = heartbeatAgeMs(dir);
  if (!Number.isFinite(firstAge) || firstAge > HEARTBEAT_MAX_AGE_MS) {
    fail(3, `${UNAVAILABLE}: resident inference worker heartbeat is stale or missing ` +
      `(last ${Number.isFinite(firstAge) ? Math.round(firstAge / 1000) + 's' : 'never'} ago). ` +
      `Start the worker — see scripts/resident-inference-worker/WORKER_BRIEF.md.`);
  }

  const requestFile = path.join(dir, id + '.request.json');
  const answerFile = path.join(dir, id + '.answer.json');
  const errorFile = path.join(dir, id + '.error.json');
  atomicWrite(requestFile, JSON.stringify(input));

  const expiresAtMs = Number(request.expires_at) * 1000;
  const cleanup = () => {
    for (const file of [requestFile, answerFile, errorFile]) {
      try { fs.unlinkSync(file); } catch { /* already gone */ }
    }
  };

  while (true) {
    // The worker may have died mid-wait: stop holding the slot and let the
    // bridge request fall back to the hook.
    const age = heartbeatAgeMs(dir);
    if (!Number.isFinite(age) || age > HEARTBEAT_MAX_AGE_MS) {
      cleanup();
      fail(3, `${UNAVAILABLE}: resident inference worker heartbeat went stale while waiting for ${id}.`);
    }
    if (fs.existsSync(errorFile)) {
      let detail = '';
      try { detail = String(JSON.parse(fs.readFileSync(errorFile, 'utf8'))?.error || ''); } catch { /* ignore */ }
      cleanup();
      fail(1, 'Resident inference worker failed request ' + id + (detail ? ': ' + detail.slice(0, 500) : ''));
    }
    if (fs.existsSync(answerFile)) {
      const answer = fs.readFileSync(answerFile, 'utf8');
      cleanup();
      if (!answer.trim()) fail(1, 'Resident inference worker returned an empty answer for ' + id);
      process.stdout.write(answer);
      return;
    }
    if (Number.isFinite(expiresAtMs) && Date.now() >= expiresAtMs) {
      cleanup();
      fail(1, 'Muse inference spool: request ' + id + ' expired while waiting for the resident worker');
    }
    await new Promise(resolve => setTimeout(resolve, POLL_MS));
  }
}

main().catch(error => fail(1, 'Muse inference spool failed: ' + (error?.message || String(error))));
