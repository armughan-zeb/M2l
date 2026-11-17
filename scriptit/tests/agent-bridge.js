'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'scriptit-bridge-test-'));
process.env.SCRIPTIT_AGENT_BRIDGE_DIR = path.join(tmpRoot, 'agent-bridge');

const { isBridgeHandle, bridgeOwner, agentBridgeChat, AgentBridgeError } = require('../src/providers/agentBridge');

function requestDirs() {
  const root = process.env.SCRIPTIT_AGENT_BRIDGE_DIR;
  if (!fs.existsSync(root)) return [];
  // Sort by request-file creation time: the checks below read .at(-1) as
  // "the newest request", but readdirSync order is filesystem order, not
  // creation order, so an unsorted listing flaked step 4 (it read an
  // earlier 'answered' request instead of the expired one).
  return fs.readdirSync(root)
    .map((id) => ({ id, dir: path.join(root, id) }))
    .filter(({ dir }) => fs.existsSync(path.join(dir, 'request.json')))
    .sort((a, b) => fs.statSync(path.join(a.dir, 'request.json')).mtimeMs - fs.statSync(path.join(b.dir, 'request.json')).mtimeMs);
}

function readRequest(dir) {
  return JSON.parse(fs.readFileSync(path.join(dir, 'request.json'), 'utf8'));
}

function answer(dir, content) {
  fs.writeFileSync(path.join(dir, 'response.json'), JSON.stringify({ content }), 'utf8');
  const row = readRequest(dir);
  row.state = 'answered';
  fs.writeFileSync(path.join(dir, 'request.json'), JSON.stringify(row), 'utf8');
}

function answerError(dir, error) {
  fs.writeFileSync(path.join(dir, 'response.json'), JSON.stringify({ error }), 'utf8');
  const row = readRequest(dir);
  row.state = 'answered';
  fs.writeFileSync(path.join(dir, 'request.json'), JSON.stringify(row), 'utf8');
}

/** Fake operator: polls the bridge dir and answers new pending requests. */
function startOperator(handler) {
  const seen = new Set();
  const timer = setInterval(() => {
    for (const { id, dir } of requestDirs()) {
      if (seen.has(id)) continue;
      const row = readRequest(dir);
      if (row.state !== 'pending') continue;
      seen.add(id);
      handler(id, dir, row);
    }
  }, 50);
  return () => clearInterval(timer);
}

async function main() {
  assert.equal(isBridgeHandle('agent-bridge:local-user'), true);
  assert.equal(isBridgeHandle('external-agent:abc'), false);
  assert.equal(isBridgeHandle('sk-123'), false);
  assert.equal(bridgeOwner('agent-bridge:local-user'), 'local-user');
  assert.equal(bridgeOwner('agent-bridge:'), 'local-user');

  // 1. Happy path: operator answers with prose.
  let stop = startOperator((id, dir) => answer(dir, 'The crew boards at dawn.'));
  const first = await agentBridgeChat({
    apiKey: 'agent-bridge:local-user',
    messages: [{ role: 'user', content: 'Write one sentence.' }],
    timeoutMs: 10000,
  });
  stop();
  assert.equal(first.content, 'The crew boards at dawn.');
  assert.equal(first.modelUsed, 'agent-bridge');
  assert.equal(first.attempts, 1);
  assert.equal(first.raw.provider, 'agent_bridge');
  const written = requestDirs().map(({ dir }) => readRequest(dir)).at(-1);
  assert.equal(written.owner_id, 'local-user');
  assert.equal(written.kind, 'text');
  assert.equal(written.operation, 'scriptit.completion');
  assert.deepEqual(written.messages, [{ role: 'user', content: 'Write one sentence.' }]);
  assert.equal(written.state, 'answered');

  // 2. JSON response format passes through as a string.
  stop = startOperator((id, dir) => answer(dir, '{"beats": []}'));
  const json = await agentBridgeChat({
    apiKey: 'agent-bridge:local-user',
    messages: [{ role: 'user', content: 'Return JSON.' }],
    responseFormat: { type: 'json_object' },
    timeoutMs: 10000,
  });
  stop();
  assert.equal(json.content, '{"beats": []}');

  // 3. Operator error on the first request retries with a fresh id.
  let calls = 0;
  stop = startOperator((id, dir) => {
    calls += 1;
    if (calls === 1) answerError(dir, 'operator busy');
    else answer(dir, 'Second attempt wins.');
  });
  const retried = await agentBridgeChat({
    apiKey: 'agent-bridge:local-user',
    messages: [{ role: 'user', content: 'Try again.' }],
    timeoutMs: 20000,
  });
  stop();
  assert.equal(retried.content, 'Second attempt wins.');
  assert.equal(retried.attempts, 2);
  assert.equal(calls, 2);

  // 4. No operator: short budget expires and the request is marked expired.
  const before = Date.now();
  await assert.rejects(
    agentBridgeChat({
      apiKey: 'agent-bridge:local-user',
      messages: [{ role: 'user', content: 'Nobody home.' }],
      timeoutMs: 1500,
    }),
    (err) => err instanceof AgentBridgeError && /did not answer/.test(err.message),
  );
  assert.ok(Date.now() - before < 8000, 'expiry must respect the short budget');
  const expired = requestDirs().map(({ dir }) => readRequest(dir)).at(-1);
  assert.equal(expired.state, 'expired');

  // 5. A non-bridge handle is rejected immediately.
  await assert.rejects(
    agentBridgeChat({ apiKey: 'sk-123', messages: [{ role: 'user', content: 'x' }] }),
    /agent-bridge handle is required/,
  );

  fs.rmSync(tmpRoot, { recursive: true, force: true });
  console.log('AGENT BRIDGE CHECKS PASSED');
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
