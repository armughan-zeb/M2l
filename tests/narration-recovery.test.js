'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { normalizeNarration, writerOutput } = require('../scriptit/src/agents/scriptWriter');
const { agentBridgeChat } = require('../scriptit/src/providers/agentBridge');
const { validateScript } = require('../adapters/script');

test('JSON-wrapped narration retains paragraph breaks; normal text stays unchanged', () => {
  const text = 'A train enters the tunnel.\n\nThe crossing tells the rest of the story.';
  for (const value of [text, JSON.stringify({ script: text }), JSON.stringify({ result: { narration: text } }), '```json\n' + JSON.stringify({ scriptText: text }) + '\n```']) {
    assert.equal(normalizeNarration(value), text);
    assert.equal(writerOutput(value, 100).script, text);
  }
  const structured = JSON.stringify({ factual_claims: ['one'] });
  assert.equal(normalizeNarration(structured), structured);
});

test('short narration is retained with a warning, empty narration is rejected', () => {
  const short = validateScript('One complete sentence.', { targetDurationMinutes: 2 });
  assert.equal(short.ok, true);
  assert.ok(short.warnings.length);
  assert.equal(validateScript(' ', { targetDurationMinutes: 1 }).ok, false);
});

test('the CLI returns a draft with its quality warnings instead of failing', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-cli-draft-'));
  try {
    const preload = path.join(dir, 'writer-stub.cjs');
    fs.writeFileSync(preload, `const Module=require('module'); const load=Module._load; Module._load=function(id,...args){ if(id.endsWith('/agents/scriptWriter')) return {generateScript:async()=>({script:'A first beat.\\n\\nA second beat.',meta:{publishable:false,quality:{issues:['pacing']}}})}; return load.call(this,id,...args); };`);
    const env = { ...process.env, NODE_OPTIONS: '' };
    for (const name of ['OPENROUTER_API_KEY', 'VIDZEO_OPENROUTER_API_KEY', 'SCRIPTIT_API_KEY', 'SCRIPTIT_OPENROUTER_API_KEY']) delete env[name];
    const cli = path.join(__dirname, '..', 'scriptit', 'cli', 'generate.js');
    const child = spawnSync(process.execPath, ['--require', preload, cli, '--topic', 'Test'], { input: '', encoding: 'utf8', env });
    assert.equal(child.status, 0, child.stderr);
    const result = JSON.parse(child.stdout);
    assert.equal(result.scriptText, 'A first beat.\n\nA second beat.');
    assert.equal(result.meta.publishable, false);
    assert.deepEqual(result.meta.quality.issues, ['pacing']);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('the bridge accepts an object response and preserves structured JSON', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-bridge-object-'));
  const previous = process.env.SCRIPTIT_AGENT_BRIDGE_DIR;
  process.env.SCRIPTIT_AGENT_BRIDGE_DIR = root;
  const timer = setInterval(() => {
    for (const dir of fs.readdirSync(root)) {
      const folder = path.join(root, dir);
      const request = path.join(folder, 'request.json');
      const reply = path.join(folder, 'response.json');
      if (fs.existsSync(request) && !fs.existsSync(reply)) fs.writeFileSync(reply, JSON.stringify({ content: { script: 'First beat.\n\nSecond beat.' } }));
    }
  }, 25);
  try {
    const result = await agentBridgeChat({ apiKey: 'agent-bridge:local-user', messages: [], timeoutMs: 5000 });
    assert.equal(normalizeNarration(result.content), 'First beat.\n\nSecond beat.');
    assert.deepEqual(JSON.parse(result.content), { script: 'First beat.\n\nSecond beat.' });
  } finally {
    clearInterval(timer);
    if (previous === undefined) delete process.env.SCRIPTIT_AGENT_BRIDGE_DIR;
    else process.env.SCRIPTIT_AGENT_BRIDGE_DIR = previous;
    fs.rmSync(root, { recursive: true, force: true });
  }
});
