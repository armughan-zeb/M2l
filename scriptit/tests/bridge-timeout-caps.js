'use strict';
// Regression tests for the bridge-wall timeout caps (Nov 9 2026 follow-up).
// The Vidnets inbox wall is 600s, but three ScriptIT caller caps expired
// synthesis/writer requests first (180s / 300s / 300s). These tests prove
// the callers now wait the full 600s — and that the 45-minute total job
// deadline (2700000ms) was NOT changed.
// Offline: the bridge transports are stubbed via require.cache; no inbox,
// CMS, or model is contacted.
// Run: node tests/bridge-timeout-caps.js
const assert = require('assert');
const fs = require('fs');
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

  // 1. agent-bridge synthesis waits the full 600s wall (was 180000).
  const bridgeResult = await synthesizeEncyclopedicDossier({
    topic: 'Amtrak locomotive 160',
    niche: 'general',
    apiKey: 'agent-bridge:local-user',
  });
  assert(bridgeResult && Array.isArray(bridgeResult.claims) && bridgeResult.claims.length > 0, 'bridge synthesis must return claims');
  assert.strictEqual(seenBridge.timeoutMs, 600000, 'agent-bridge synthesis timeoutMs must be 600000');
  assert.strictEqual(seenBridge.operation, 'scriptit.research-synthesis');
  console.log('  ✓ encyclopedic synthesis via agent-bridge waits 600000ms (was 180000)');

  // 2. external-agent synthesis (same request, other Muse handle) also 600s.
  const agentResult = await synthesizeEncyclopedicDossier({
    topic: 'Amtrak locomotive 160',
    niche: 'general',
    apiKey: 'external-agent:paired-worker',
  });
  assert(agentResult && Array.isArray(agentResult.claims) && agentResult.claims.length > 0, 'agent synthesis must return claims');
  assert.strictEqual(seenAgent.timeoutMs, 600000, 'external-agent synthesis timeoutMs must be 600000');
  console.log('  ✓ encyclopedic synthesis via external-agent waits 600000ms (was 180000)');

  // 3. Writer + CLI caps: the agent writer bound and the CLI bridge
  // timeout must be 600000, and the total job deadline must still be
  // 2700000 (45 minutes) — the per-request caps move, the job cap does not.
  const writerSrc = fs.readFileSync(path.join(__dirname, '../src/agents/scriptWriter.js'), 'utf8');
  assert.match(
    writerSrc,
    /Math\.min\(600000, Math\.max\(30000, Number\(options\.timeoutMs\)/,
    'scriptWriter agent cap must be Math.min(600000, ...)',
  );
  console.log('  ✓ scriptWriter agent writer cap is 600000ms (was 300000)');

  const cliSrc = fs.readFileSync(path.join(__dirname, '../cli/generate.js'), 'utf8');
  assert.match(cliSrc, /const timeoutMs = bridgeMode \? 600000 : 120000;/, 'CLI bridge timeoutMs must be 600000');
  assert.match(cliSrc, /SCRIPTIT_TOTAL_DEADLINE_MS \|\| 2700000/, 'CLI total job deadline must still be 2700000');
  console.log('  ✓ CLI bridge timeoutMs is 600000ms (was 300000); total deadline still 2700000ms');

  console.log('\nALL BRIDGE-TIMEOUT-CAP TESTS PASSED');
  process.exit(0);
}

main().catch((err) => {
  console.error('\nTest failed:', err);
  process.exit(1);
});
