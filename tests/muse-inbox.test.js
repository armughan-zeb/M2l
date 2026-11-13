'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { once } = require('node:events');
const { spawn } = require('node:child_process');
const { test } = require('node:test');
const { writeJsonAtomic } = require('../lib/config');
const { startResidentInbox, probeInbox, pendingRequests, completeLocally, createInboxReadiness } = require('../lib/museInbox');
const { runInferenceHttp } = require('../adapters/vidnets');
const { sleep } = require('../lib/sleep');

const logger = { info() {}, warn() {} };

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-inbox-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

function request(root, index, overrides = {}) {
  const id = index.toString(16).padStart(32, '0');
  const now = Date.now() / 1000;
  const row = {
    id, owner_id: 'local-user', state: 'pending', kind: 'text',
    operation: 'unrecognized.operation', created_at: now, expires_at: now + 60,
    messages: [], response_format: {}, ...overrides
  };
  writeJsonAtomic(path.join(root, id, 'request.json'), row);
  return row;
}

async function until(check, timeoutMs = 4000) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    if (check()) return;
    await sleep(10);
  }
  assert.fail('Condition did not become true');
}

test('resident answers a probe through the actual Python inbox completion path', async t => {
  const root = fixture(t);
  const env = { VIDNETS_AGENT_BRIDGE_DIR: root };
  let count = 0;
  const stop = startResidentInbox({
    root, pollMs: 20, logger,
    answer: async row => {
      count += 1;
      return row.messages[0].content.split(' ').at(-1);
    }
  });
  try {
    const proof = await probeInbox(env, { timeoutMs: 6000, pollMs: 25 });
    assert.equal(count, 1);
    assert.match(proof.id, /^[a-f0-9]{32}$/);
    assert.equal(pendingRequests(root).length, 0);
    assert.equal(JSON.parse(fs.readFileSync(path.join(root, proof.id, 'request.json'))).state, 'answered');
  } finally { stop(); }
});

test('an enabled hook without an answer fails the readiness probe', async t => {
  const root = fixture(t);
  const hook = path.join(root, 'hook.json');
  writeJsonAtomic(hook, { enabled: true });
  await assert.rejects(probeInbox({
    VIDNETS_AGENT_BRIDGE_DIR: root, MUSE_INBOX_HOOK_DEFINITION: hook
  }, { timeoutMs: 60, pollMs: 10 }), /did not receive an answer/);
  assert.equal(pendingRequests(root).length, 0);
});

test('readiness requires a first answer, refreshes without blocking claims, and backs off after failure', async () => {
  let time = 1;
  let probes = 0;
  let finish;
  const ready = createInboxReadiness({
    now: () => time,
    probe: () => {
      probes += 1;
      return new Promise(resolve => { finish = resolve; });
    },
    onReady() {},
    onFailure() {}
  });
  const startup = ready();
  const anotherCaller = ready();
  await Promise.resolve();
  assert.equal(probes, 1);
  finish({ elapsedMs: 10 });
  assert.deepEqual(await Promise.all([startup, anotherCaller]), [true, true]);

  time += 4 * 60000;
  assert.equal(await ready(), true);
  assert.equal(probes, 2);
  assert.equal(await ready(), true);
  finish(Promise.reject(new Error('Muse is down')));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(await ready(), false);
  assert.equal(probes, 2);

  time += 5000;
  const retry = ready();
  await Promise.resolve();
  assert.equal(probes, 3);
  finish({ elapsedMs: 10 });
  assert.equal(await retry, true);
});

test('concurrent dispatch handles unknown kinds, skips expired and foreign rows and never dispatches twice', async t => {
  const root = fixture(t);
  const rows = Array.from({ length: 7 }, (_, i) => request(root, i + 1, {
    kind: i % 2 ? 'tts' : 'vision'
  }));
  const expired = request(root, 30, { expires_at: 0 });
  const foreign = request(root, 31, { owner_id: 'different-user' });
  const previouslyAnswered = request(root, 32);
  writeJsonAtomic(path.join(root, previouslyAnswered.id, 'response.json'), { content: 'saved' });
  const seen = new Map();
  let active = 0;
  let peak = 0;
  const stop = startResidentInbox({
    root, concurrency: 3, pollMs: 10, logger,
    complete: async (root, row, reply) => writeJsonAtomic(path.join(root, row.id, 'response.json'), reply),
    answer: async row => {
      seen.set(row.id, (seen.get(row.id) || 0) + 1);
      peak = Math.max(peak, ++active);
      await sleep(40);
      active -= 1;
      return 'answer';
    }
  });
  try {
    await until(() => rows.every(row => fs.existsSync(path.join(root, row.id, 'response.json'))));
    assert.equal(peak, 3);
    assert.equal(seen.size, 7);
    assert.deepEqual([...seen.values()], Array(7).fill(1));
    assert.equal(seen.has(expired.id) || seen.has(foreign.id) || seen.has(previouslyAnswered.id), false);
  } finally { stop(); }
});

test('shutdown aborts in-flight composition and the next dispatcher takes the still-live row', async t => {
  const root = fixture(t);
  const row = request(root, 1);
  const options = {
    root, pollMs: 10, logger,
    complete: async (root, row, reply) => writeJsonAtomic(path.join(root, row.id, 'response.json'), reply)
  };
  let signal;
  const stop = startResidentInbox({
    ...options,
    answer: async (_, opts) => {
      signal = opts.signal;
      await sleep(30000, signal);
      return 'stale';
    }
  });
  stop();
  assert.equal(signal.aborted, true);
  const stopNext = startResidentInbox({ ...options, answer: async () => 'fresh' });
  try {
    const reply = path.join(root, row.id, 'response.json');
    await until(() => fs.existsSync(reply));
    assert.equal(JSON.parse(fs.readFileSync(reply)).content, 'fresh');
  } finally { stopNext(); }
});

test('resident validation rejects an incorrect schema and accepts an exact structured answer', async t => {
  const root = fixture(t);
  const row = request(root, 1, {
    response_format: { type: 'json_schema', json_schema: {
      schema: { type: 'object', properties: { approved: { type: 'boolean' } }, required: ['approved'] }
    } }
  });
  await assert.rejects(completeLocally(root, row, { content: { approved: 'yes' } }), /does not match the schema/);
  await completeLocally(root, row, { content: { approved: true } });
  await assert.rejects(completeLocally(root, row, { content: { approved: false } }), /already answered/);
  const reply = JSON.parse(fs.readFileSync(path.join(root, row.id, 'response.json')));
  assert.deepEqual(JSON.parse(reply.content), { approved: true });
});

test('HTTP transport preserves a structured verdict and unwraps a multiline narration', async t => {
  const script = 'Opening hook.\n\nThe story continues.';
  const server = http.createServer((req, res) => {
    res.setHeader('Content-Type', 'application/json');
    const payload = req.url === '/verdict' ? { answer: 'approved', approved: true } : { content: script };
    req.resume();
    res.end(JSON.stringify(payload));
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => { server.closeAllConnections(); server.close(); });
  const root = 'http://127.0.0.1:' + server.address().port;
  assert.deepEqual(JSON.parse(await runInferenceHttp(root + '/verdict', {
    response_format: { type: 'json_object' }
  })), { answer: 'approved', approved: true });
  assert.equal(await runInferenceHttp(root + '/script', {}), script);
});

test('HTTP transport enforces its deadline when a caller also supplies a cancellation signal', async t => {
  const server = http.createServer((req, res) => {
    req.resume();
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.flushHeaders();
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => { server.closeAllConnections(); server.close(); });
  const controller = new AbortController();
  const safetyTimer = setTimeout(() => controller.abort(new Error('fixture safety deadline')), 2000);
  try {
    await assert.rejects(runInferenceHttp('http://127.0.0.1:' + server.address().port, {}, {
      timeoutMs: 250, signal: controller.signal
    }));
    assert.equal(controller.signal.aborted, false);
  } finally { clearTimeout(safetyTimer); }
});

test('daemon start proves the configured transport before its first CMS claim', async t => {
  const root = fixture(t);
  const bridge = path.join(root, 'bridge');
  const events = [];
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => {
      res.setHeader('Content-Type', 'application/json');
      if (req.url === '/answer') {
        const { request } = JSON.parse(body);
        events.push(request.operation);
        res.end(JSON.stringify({ content: request.messages[0].content.split(' ').at(-1) }));
      } else {
        events.push(req.url);
        res.end(JSON.stringify({ ok: true, active: true, job: null }));
      }
    });
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => { server.closeAllConnections(); server.close(); });
  const base = 'http://127.0.0.1:' + server.address().port;
  const repo = path.join(__dirname, '..');
  const child = spawn(process.execPath, ['muse-agent.js', 'start', '--once'], {
    cwd: repo,
    env: {
      ...process.env, MUSE_CMS_URL: base, MUSE_TOKEN: 'fixture-token',
      MUSE_CONFIG_FILE: path.join(root, '.muse-agent.json'),
      MUSE_WORK_DIR: path.join(root, 'work'), MUSE_REPO_ROOT: repo,
      MUSE_VIDNETS_ROOT: path.join(repo, 'vidnets'), MUSE_VIDNETS_URL: base,
      MUSE_RENDER_COMMAND: `node "${path.join(repo, 'render-vidnets-linux.js')}"`, MUSE_SCRIPT_COMMAND: 'fixture-script',
      MUSE_INFERENCE_COMMAND: '', MUSE_INFERENCE_URL: base + '/answer',
      VIDNETS_AGENT_BRIDGE_DIR: bridge, SCRIPTIT_AGENT_BRIDGE_DIR: bridge,
      VIDZEO_BRIDGE_ROOT: bridge, MUSE_BRIDGE_ROOT: bridge, SCRIPTIT_BRIDGE_ROOT: bridge,
      OPENROUTER_API_KEY: '', VIDZEO_OPENROUTER_API_KEY: '',
      SCRIPTIT_API_KEY: '', SCRIPTIT_OPENROUTER_API_KEY: ''
    },
    stdio: ['ignore', 'pipe', 'pipe']
  });
  let diagnostics = '';
  child.stdout.on('data', data => { diagnostics += data; });
  child.stderr.on('data', data => { diagnostics += data; });
  const timer = setTimeout(() => child.kill(), 15000);
  try {
    const [code] = await once(child, 'close');
    assert.equal(code, 0, diagnostics);
    assert.deepEqual(events, [
      '/api/muse/agent/heartbeat', 'muse.inbox_probe', '/api/muse/agent/jobs/next?recoverClaim=1'
    ]);
    assert.equal(fs.existsSync(path.join(bridge, 'resident.lock')), false);
  } finally { clearTimeout(timer); }
});
