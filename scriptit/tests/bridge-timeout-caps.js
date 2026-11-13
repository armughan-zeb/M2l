'use strict';
// Regression tests for Muse inbox bounds. Every request is capped at 120s
// (60s answer SLO); the CLI's separate total-generation deadline is 45m.
// Offline: the bridge transports are stubbed via require.cache; no inbox,
// CMS, or model is contacted.
// Run: node tests/bridge-timeout-caps.js
const assert = require('assert');
const fs = require('fs');
const http = require('http');
const { agentChat: actualAgentChat } = require('../src/providers/agentInference');
const path = require('path');

function stubModule(resolvedPath, exportsObj) {
  require.cache[resolvedPath] = {
    id: resolvedPath,
    filename: resolvedPath,
    loaded: true,
    exports: exportsObj,
  };
}

const Dossier = JSON.stringify({
  summary: 'A short factual overview of the test subject and its significance.',
  timeline: [
    { date: '2020', event: 'First test event happened', details: 'Concrete context one.' },
    { date: '2021', event: 'Second test event happened', details: 'Concrete context two.' },
    { date: '2022', event: 'Third test event happened', details: 'Concrete context three.' },
  ],
  key_entities: [
    { name: 'Test Entity', role: 'test role' },
    { name: 'Second Entity', role: 'another role' },
  ],
  factual_claims: [
    'Test fact one about the subject.',
    'Test fact two about the subject.',
    'Test fact three about the subject.',
    'Test fact four about the subject.',
    'Test fact five about the subject.',
    'Test fact six about the subject.',
  ],
});

async function main() {
  const bridgePath = require.resolve('../src/providers/agentBridge');
  const inferencePath = require.resolve('../src/providers/agentInference');
  const seenBridge = {};
  const seenAgent = {};
  stubModule(bridgePath, {
    isBridgeHandle: (value) => String(value || '').startsWith('agent-bridge:'),
    agentBridgeChat: async (options) => {
      Object.assign(seenBridge, options);
      return { content: Dossier };
    },
  });
  stubModule(inferencePath, {
    isAgentHandle: (value) => String(value || '').startsWith('external-agent:'),
    agentChat: async (options) => {
      Object.assign(seenAgent, options);
      return { content: Dossier };
    },
  });

  const { synthesizeEncyclopedicDossier } = require('../src/research/encyclopedicSynthesizer');

  // 1. Agent-bridge synthesis never asks the inbox to wait beyond 120s.
  const bridgeResult = await synthesizeEncyclopedicDossier({
    topic: 'Amtrak locomotive 160',
    niche: 'general',
    apiKey: 'agent-bridge:local-user',
  });
  assert(bridgeResult && Array.isArray(bridgeResult.claims) && bridgeResult.claims.length > 0, 'bridge synthesis must return claims');
  assert.strictEqual(seenBridge.timeoutMs, 120000, 'agent-bridge synthesis timeoutMs must be 120000');
  assert.strictEqual(seenBridge.operation, 'scriptit.research-synthesis');
  console.log('  ✓ encyclopedic synthesis via agent-bridge is capped at 120000ms');

  // 2. External-agent synthesis uses the same cap.
  const agentResult = await synthesizeEncyclopedicDossier({
    topic: 'Amtrak locomotive 160',
    niche: 'general',
    apiKey: 'external-agent:paired-worker',
  });
  assert(agentResult && Array.isArray(agentResult.claims) && agentResult.claims.length > 0, 'agent synthesis must return claims');
  assert.strictEqual(seenAgent.timeoutMs, 120000, 'external-agent synthesis timeoutMs must be 120000');
  console.log('  ✓ encyclopedic synthesis via external-agent is capped at 120000ms');

  // 3. Writer + CLI request caps are 120s; the total deadline stays 45m.
  const writerSrc = fs.readFileSync(path.join(__dirname, '../src/agents/scriptWriter.js'), 'utf8');
  assert.match(
    writerSrc,
    /Math\.min\(120000, Math\.max\(30000, Number\(options\.timeoutMs\)/,
    'scriptWriter agent cap must be Math.min(120000, ...)',
  );
  console.log('  ✓ scriptWriter agent writer cap is 120000ms');

  const cliSrc = fs.readFileSync(path.join(__dirname, '../cli/generate.js'), 'utf8');
  assert.match(cliSrc, /const timeoutMs = 120000;/, 'CLI request timeout must be 120000');
  assert.match(cliSrc, /SCRIPTIT_TOTAL_DEADLINE_MS \|\| 2700000/, 'CLI total job deadline must still be 2700000');
  console.log('  ✓ CLI request wall is 120000ms; total deadline still 2700000ms');

  // 4. The alternate Muse HTTP transport applies the same hard cap before
  // sending a request to the paired local CMS service.
  let posted;
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => {
      posted = JSON.parse(body);
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify({ ok: true, content: 'done', requestId: 'timeout-fixture' }));
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const previousUrl = process.env.VIDZEO_CMS_AGENT_URL;
  try {
    process.env.VIDZEO_CMS_AGENT_URL = `http://127.0.0.1:${server.address().port}`;
    await actualAgentChat({ apiKey: 'external-agent:fixture', messages: [], timeoutMs: 600000 });
    assert.strictEqual(posted.timeoutMs, 120000, 'HTTP Muse request must clamp to 120000ms');
  } finally {
    if (previousUrl === undefined) delete process.env.VIDZEO_CMS_AGENT_URL;
    else process.env.VIDZEO_CMS_AGENT_URL = previousUrl;
    await new Promise(resolve => server.close(resolve));
  }
  console.log('  ✓ HTTP Muse transport clamps oversized caller deadlines to 120000ms');

  console.log('\nALL BRIDGE-TIMEOUT-CAP TESTS PASSED');
  process.exit(0);
}

main().catch((err) => {
  console.error('\nTest failed:', err);
  process.exit(1);
});
