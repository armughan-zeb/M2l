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
 * Workers run as a POOL (scripts/resident-inference-worker/
 * POOL_SUPERVISOR_BRIEF.md): several worker agents share this spool and
 * claim work by atomically renaming `<id>.request.json` to
 * `<id>.request.json.claimed-<workerId>`. This script must not care that
 * the request file was renamed — it already holds the payload from stdin,
 * and only waits for `<id>.answer.json` / `<id>.error.json`. After
 * delivery it removes the request file AND any claim file for this id.
 * Liveness is pool-wide: the pool is alive if ANY worker heartbeat
 * `worker-alive-<workerId>.json` (or the legacy single-worker
 * `worker-alive.json`) is fresh.
 * A dead worker's unexpired claim would strand the request until expiry
 * even while other workers are idle, so while waiting this script rescues
 * it: a claim older than 2 minutes whose worker has no fresh heartbeat is
 * renamed back to `<id>.request.json` — but only when another worker IS
 * alive to take it, and never when the claiming worker still heartbeats.
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
 * worker-alive-<workerId>.json (each pool worker's heartbeat, written
 * every ~10s; the legacy single-worker worker-alive.json still counts).
 * A worker claims a request by renaming its .request.json to
 * <request-id>.request.json.claimed-<workerId>; the claim is removed
 * after the answer is delivered (or when the request expires).
 *
 * The worker NEVER answers from this file: it is a dumb pipe. Any answer
 * that arrives here was composed by the resident worker agent. There is no
 * stub, no fixed score, and no auto-approve anywhere in this path.
 */

const fs = require('fs');
const path = require('path');

const UNAVAILABLE = 'MUSE_TRANSPORT_UNAVAILABLE';
const HEARTBEAT_MAX_AGE_MS = 90000;
// A claim must be this old before it can be rescued: the claiming worker
// needs time to start, write its first heartbeat, and begin composing.
const CLAIM_RESCUE_GRACE_MS = 120000;
const CLAIM_RESCUE_CHECK_MS = 2000;
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
  // Pool liveness: the youngest fresh heartbeat across all pool workers
  // (worker-alive-<workerId>.json) — plus the legacy single-worker file,
  // which still counts during rollout. Returns Infinity when no heartbeat
  // exists or none parses.
  let youngest = Infinity;
  let found = false;
  let entries;
  try { entries = fs.readdirSync(dir); } catch { return Infinity; }
  for (const entry of entries) {
    if (entry !== 'worker-alive.json' && !/^worker-alive-[A-Za-z0-9._-]+\.json$/.test(entry)) continue;
    try {
      const raw = JSON.parse(fs.readFileSync(path.join(dir, entry), 'utf8'));
      const updatedAt = Number(raw?.updatedAt);
      if (!Number.isFinite(updatedAt)) continue;
      found = true;
      youngest = Math.min(youngest, Date.now() - updatedAt);
    } catch { /* unreadable heartbeat — ignore it */ }
  }
  return found ? youngest : Infinity;
}

function fail(code, message) {
  process.stderr.write(message + '\n');
  process.exit(code);
}

function heartbeatAgeByWorker(dir) {
  // Map workerId -> heartbeat age in ms (Infinity when the file is
  // missing or unreadable). The legacy single-worker file has no id.
  const ages = new Map();
  let entries;
  try { entries = fs.readdirSync(dir); } catch { return ages; }
  for (const entry of entries) {
    const match = /^(?:worker-alive-([A-Za-z0-9._-]+)|worker-alive)\.json$/.exec(entry);
    if (!match) continue;
    const workerId = match[1] || 'legacy';
    try {
      const updatedAt = Number(JSON.parse(fs.readFileSync(path.join(dir, entry), 'utf8'))?.updatedAt);
      ages.set(workerId, Number.isFinite(updatedAt) ? Date.now() - updatedAt : Infinity);
    } catch { ages.set(workerId, Infinity); }
  }
  return ages;
}

function rescueStaleClaim(dir, id, requestFile) {
  // A dead worker's claim strands the request: no other worker can claim
  // it, so it waits until expiry even though the pool is alive and idle.
  // Rescue it by renaming the claim back to the unclaimed request file —
  // but only when the claiming worker is provably not heartbeating, the
  // claim is old enough that the worker had time to start, and another
  // worker IS alive to take it. Never steal a claim from a live worker
  // (a slow worker on a big answer keeps a fresh heartbeat), and never
  // rescue when nobody else could take the work — the request then falls
  // back to the hook through the spool's own heartbeat check instead.
  let claimFile = null;
  let claimingWorker = null;
  try {
    for (const entry of fs.readdirSync(dir)) {
      if (!entry.startsWith(id + '.request.json.claimed-')) continue;
      claimFile = path.join(dir, entry);
      claimingWorker = entry.slice((id + '.request.json.claimed-').length);
      break;
    }
  } catch { return; }
  if (!claimFile || !claimingWorker) return;
  let claimAgeMs = Infinity;
  try { claimAgeMs = Date.now() - fs.statSync(claimFile).mtimeMs; } catch { return; }
  if (claimAgeMs < CLAIM_RESCUE_GRACE_MS) return;
  const ages = heartbeatAgeByWorker(dir);
  // A fresh legacy heartbeat cannot be attributed to a worker id, so it
  // blocks the rescue: the claiming worker might be the legacy one.
  if (ages.has('legacy') && ages.get('legacy') <= HEARTBEAT_MAX_AGE_MS) return;
  const claimingAge = ages.get(claimingWorker);
  if (claimingAge !== undefined && claimingAge <= HEARTBEAT_MAX_AGE_MS) return;
  let otherLive = false;
  for (const [workerId, age] of ages) {
    if (workerId !== claimingWorker && workerId !== 'legacy' && age <= HEARTBEAT_MAX_AGE_MS) {
      otherLive = true;
      break;
    }
  }
  if (!otherLive) return;
  try {
    fs.renameSync(claimFile, requestFile);
    process.stderr.write(`Muse inference spool: rescued request ${id} from dead worker ${claimingWorker} ` +
      `(claim ${Math.round(claimAgeMs / 1000)}s old); re-filed for the pool\n`);
  } catch { /* claim vanished mid-rescue — the worker finished or died; move on */ }
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
    fail(3, `${UNAVAILABLE}: resident inference worker pool heartbeat is stale or missing ` +
      `(last ${Number.isFinite(firstAge) ? Math.round(firstAge / 1000) + 's' : 'never'} ago). ` +
      `Start the pool — see scripts/resident-inference-worker/POOL_SUPERVISOR_BRIEF.md.`);
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
    // A pool worker claims by renaming the request file to
    // <id>.request.json.claimed-<workerId>; remove its claim too so a
    // dead worker's claim never blocks a re-file. Best-effort: the
    // worker also deletes its own claim after writing the answer.
    try {
      for (const entry of fs.readdirSync(dir)) {
        if (entry.startsWith(id + '.request.json.claimed-')) {
          try { fs.unlinkSync(path.join(dir, entry)); } catch { /* already gone */ }
        }
      }
    } catch { /* spool dir unreadable */ }
  };

  let lastRescueCheck = 0;
  while (true) {
    // The worker may have died mid-wait: stop holding the slot and let the
    // bridge request fall back to the hook.
    const age = heartbeatAgeMs(dir);
    if (!Number.isFinite(age) || age > HEARTBEAT_MAX_AGE_MS) {
      cleanup();
      fail(3, `${UNAVAILABLE}: resident inference worker pool heartbeat went stale while waiting for ${id}.`);
    }
    // A dead worker's unexpired claim strands this request until expiry
    // even when other workers are idle — rescue it back to the pool.
    const nowMs = Date.now();
    if (nowMs - lastRescueCheck >= CLAIM_RESCUE_CHECK_MS) {
      lastRescueCheck = nowMs;
      rescueStaleClaim(dir, id, requestFile);
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
