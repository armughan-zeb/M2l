'use strict';

// Regression test for the live CMS job r_mw85awoskks8lh failure
// ("A BNSF Whiteout Crossing, Seen From the Driver's Cab").
//
// Root cause: scriptWriter rewrites the model to the literal string
// 'agent-bridge' for an agent-bridge handle, but the encyclopedic
// synthesis path only recognised the 'external-agent:' handle and passed
// the model string to OpenRouter's normalizeModel, which throws
// "OpenRouter model must be Qwen 3.5 Flash or a free model ID ending in
// :free". The synthesis died before Muse was ever asked, web research had
// 0 attributable claims, and the writer threw RESEARCH_INSUFFICIENT.
//
// Both agent handle formats must route to Muse, never through the model
// policy — and synthetic dossier claims must never open the evidence gate
// on their own (they carry fabricated archive URLs).

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');

const { synthesizeEncyclopedicDossier } = require('../scriptit/src/research/encyclopedicSynthesizer');
const { buildNarratorProfile } = require('../scriptit/src/agents/narratorVideo');
const { buildBrief } = require('../scriptit/src/agents/compressor');
const { buildEvidenceLedger } = require('../scriptit/src/agents/evidence');

const DOSSIER = {
  summary: 'BNSF Railway operates freight trains across the United States, including winter operations in snow.',
  timeline: [
    { date: '1996', event: 'Burlington Northern and Santa Fe merged to form BNSF Railway', details: 'The merger created one of the largest freight railroads in North America.' },
    { date: '2005', event: 'BNSF introduced winter operating procedures for heavy snow', details: 'Crews follow snow-clearing and visibility rules.' },
    { date: '2010', event: 'BNSF expanded its locomotive fleet for cold-weather service', details: 'Additional units were assigned to northern routes.' },
  ],
  key_entities: [{ name: 'BNSF Railway', role: 'Freight railroad operator' }],
  factual_claims: [
    'BNSF Railway was formed in 1996 from the merger of Burlington Northern and the Santa Fe Railway.',
    'BNSF operates freight trains across the northern United States in winter snow conditions.',
    'Locomotive cabs give engineers a forward view of the track during snow crossings.',
    'Whiteout conditions reduce visibility for train crews and require reduced speeds.',
    'BNSF crews follow operating rules for crossings during severe winter weather.',
    'Snowplow-equipped locomotives clear the track ahead of freight trains in blizzards.',
  ],
};

// A stub inbox operator: answers every pending bridge request with the
// canned dossier, the way the vidnets-inbox-watch hook operator does.
function startStubOperator(bridgeRoot, seen) {
  return setInterval(() => {
    let dirs = [];
    try { dirs = fs.readdirSync(bridgeRoot); } catch { return; }
    for (const dir of dirs) {
      const requestPath = path.join(bridgeRoot, dir, 'request.json');
      const replyPath = path.join(bridgeRoot, dir, 'response.json');
      if (fs.existsSync(replyPath) || !fs.existsSync(requestPath)) continue;
      try {
        const request = JSON.parse(fs.readFileSync(requestPath, 'utf8'));
        seen.push(request);
        fs.writeFileSync(replyPath, JSON.stringify({ content: JSON.stringify(DOSSIER) }));
        request.state = 'answered';
        fs.writeFileSync(requestPath, JSON.stringify(request, null, 2));
      } catch { /* race with the writer; next tick retries */ }
    }
  }, 25);
}

test('encyclopedic synthesis with an agent-bridge handle routes to the bridge, not OpenRouter', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'scriptit-bridge-'));
  const bridgeRoot = path.join(dir, 'agent-bridge');
  fs.mkdirSync(bridgeRoot, { recursive: true });
  const prev = process.env.SCRIPTIT_AGENT_BRIDGE_DIR;
  process.env.SCRIPTIT_AGENT_BRIDGE_DIR = bridgeRoot;
  const seen = [];
  const operator = startStubOperator(bridgeRoot, seen);
  try {
    const dossier = await synthesizeEncyclopedicDossier({
      topic: "A BNSF Whiteout Crossing, Seen From the Driver's Cab",
      niche: 'general',
      apiKey: 'agent-bridge:local-user',
      model: 'agent-bridge',
    });
    assert.ok(dossier.claims.length >= 6, `expected dossier claims, got ${dossier.claims.length}`);
    assert.ok(dossier.claims.every((claim) => claim.synthetic === true), 'dossier claims stay labelled synthetic');
    assert.equal(seen.length, 1, 'exactly one bridge request was posted');
    assert.equal(seen[0].operation, 'scriptit.research-synthesis');
    assert.equal(seen[0].job_id, '');
  } finally {
    clearInterval(operator);
    if (prev === undefined) delete process.env.SCRIPTIT_AGENT_BRIDGE_DIR;
    else process.env.SCRIPTIT_AGENT_BRIDGE_DIR = prev;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('narrator profile with an agent-bridge handle keeps model agent-bridge', async () => {
  const modelsSeen = [];
  const fakeChat = async ({ messages, model }) => {
    modelsSeen.push(model);
    return { content: '{"structure":"A","tonality":"B","scriptFlow":"C","audienceBehaviour":"D","hookStyle":"E","pacing":"F","sentenceRhythm":"G","retentionTricks":"H","signaturePhrases":["x"],"styleNotes":"I"}' };
  };
  const fakeFetch = async () => ({
    ok: true,
    video: { title: 'T', channel: 'C', durationSec: 90, url: 'https://www.youtube.com/watch?v=abc' },
    transcript: 'sample transcript words here',
    transcriptChars: 28,
    subtitleKind: 'auto',
  });
  const built = await buildNarratorProfile({
    videoUrl: 'https://www.youtube.com/watch?v=abc',
    apiKey: 'agent-bridge:local-user',
    model: 'agent-bridge',
    useCache: false,
    chatCompletionFn: fakeChat,
    fetchTranscriptFn: fakeFetch,
  });
  assert.ok(built.ok);
  assert.equal(built.model, 'agent-bridge');
  assert.ok(modelsSeen.every((model) => model === 'agent-bridge'));
});

test('brief sources flag synthetic dossier items so the gate can exclude them', () => {
  const query = 'BNSF whiteout crossing';
  const research = {
    query,
    gatheredAt: new Date().toISOString(),
    official: [
      {
        title: 'BNSF whiteout crossing: Historical Record',
        url: 'https://encyclopedia.archive.org/entry/BNSF#fact-1',
        source: 'Encyclopedic Historical Archive',
        synthetic: true,
        articleText: 'BNSF whiteout crossing BNSF snow BNSF railway crossing in a whiteout.',
      },
      {
        title: 'BNSF whiteout crossing report',
        url: 'https://example.com/bnsf-whiteout',
        source: 'Railway Gazette',
        articleText: 'BNSF whiteout crossing BNSF snow BNSF railway crossing in a whiteout.',
      },
    ],
  };
  const brief = buildBrief(research);
  assert.equal(brief.sources.length, 2);
  const synthetic = brief.sources.find((s) => s.source === 'Encyclopedic Historical Archive');
  const real = brief.sources.find((s) => s.source === 'Railway Gazette');
  assert.equal(synthetic.synthetic, true, 'synthetic dossier source is flagged');
  assert.equal(real.synthetic, false, 'a real source is not flagged synthetic');
  // The gate's escape hatch counts only non-synthetic brief sources.
  assert.equal(brief.sources.filter((s) => !s.synthetic).length, 1);
  // And a brief with only synthetic sources offers the gate nothing.
  const syntheticOnly = buildBrief({ ...research, official: [research.official[0]] });
  assert.equal(syntheticOnly.sources.filter((s) => !s.synthetic).length, 0);
});

test('evidence ledger with only synthetic claims stays insufficient', () => {
  const ledger = buildEvidenceLedger({
    query: 'BNSF whiteout crossing',
    gatheredAt: new Date().toISOString(),
    official: [
      {
        title: 'BNSF whiteout crossing: Historical Record',
        url: 'https://encyclopedia.archive.org/entry/BNSF#fact-1',
        source: 'Encyclopedic Historical Archive',
        synthetic: true,
        articleText: 'BNSF whiteout crossing BNSF snow BNSF railway crossing in a whiteout.',
      },
    ],
  });
  assert.equal(ledger.sufficient, false, 'synthetic-only evidence is never sufficient');
  assert.ok((ledger.claims || []).every((claim) => claim.synthetic === true));
});

test('generateScript with zero web claims asks Muse to synthesize and still fails RESEARCH_INSUFFICIENT — without the const-coverage crash', async () => {
  // Regression test for a second bug found during the live BNSF repro:
  // `coverage` was declared `const` but the synthesis rescue reassigned it,
  // so every successful synthesis threw "Assignment to constant variable",
  // the catch swallowed it, and the brief / researchIncomplete stayed stale.
  // The "added N grounded claims" log line only prints once the coverage
  // rebuild succeeds, so capturing it proves the rescue ran to completion.
  const { generateScript } = require('../scriptit/src/agents/scriptWriter');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'scriptit-bridge-'));
  const bridgeRoot = path.join(dir, 'agent-bridge');
  fs.mkdirSync(bridgeRoot, { recursive: true });
  const prev = process.env.SCRIPTIT_AGENT_BRIDGE_DIR;
  process.env.SCRIPTIT_AGENT_BRIDGE_DIR = bridgeRoot;
  const seen = [];
  const operator = startStubOperator(bridgeRoot, seen);
  const logged = [];
  const origError = console.error;
  const origWarn = console.warn;
  console.error = (...args) => { logged.push(args.join(' ')); };
  console.warn = (...args) => { logged.push(args.join(' ')); };
  try {
    const emptyResearch = {
      query: 'BNSF whiteout crossing',
      gatheredAt: new Date().toISOString(),
      duckduckgo: [], duckduckgoNews: [], bing: [], yandex: [], x: [],
      wikipedia: [], hackernews: [], reddit: [], rss: [], official: [],
      errors: [],
    };
    const error = await generateScript({
      topic: 'BNSF whiteout crossing',
      duration: '1min',
      researchMode: 'thorough',
      apiKey: 'agent-bridge:local-user',
      model: 'agent-bridge',
      useCache: false,
      recordHistory: false,
      researchJobFn: async () => ({ jobId: 'test-job', research: emptyResearch }),
    }).then(() => null, (err) => err);
    assert.ok(error, 'expected generateScript to throw with zero external claims');
    assert.equal(error.code, 'RESEARCH_INSUFFICIENT');
    assert.equal(error.retryable, true);
    assert.equal(seen.length, 1, 'Muse was asked via the bridge before failing');
    assert.equal(seen[0].operation, 'scriptit.research-synthesis');
    assert.ok(
      logged.some((line) => /Encyclopedic synthesis added \d+ grounded claims/.test(line)),
      'synthesis rescue ran to completion (coverage rebuild did not throw); logged: ' + logged.join(' | '),
    );
    assert.ok(
      !logged.some((line) => /Assignment to constant variable/.test(line)),
      'no const-assignment crash in the synthesis rescue',
    );
  } finally {
    console.error = origError;
    console.warn = origWarn;
    clearInterval(operator);
    if (prev === undefined) delete process.env.SCRIPTIT_AGENT_BRIDGE_DIR;
    else process.env.SCRIPTIT_AGENT_BRIDGE_DIR = prev;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('agent-bridge round-trip wall is 600s, matching the Vidnets server floor', async () => {
  // Regression: the file-based bridge gave each request a 300s wall while
  // the Vidnets server floors its agent requests at 600s
  // (VIDZEO_AGENT_MIN_WALL_SECONDS). The hook polls every 60s and a
  // grounded draft takes minutes, so the shorter wall expired
  // mid-composition and wasted the operator's answer. The request row
  // written to disk must carry the full 600s window.
  const { agentBridgeChat, MAX_WALL_MS } = require('../scriptit/src/providers/agentBridge');
  assert.equal(MAX_WALL_MS, 600000, 'per-request wall must be 600s');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'scriptit-bridge-wall-'));
  const bridgeRoot = path.join(dir, 'agent-bridge');
  fs.mkdirSync(bridgeRoot, { recursive: true });
  const prev = process.env.SCRIPTIT_AGENT_BRIDGE_DIR;
  process.env.SCRIPTIT_AGENT_BRIDGE_DIR = bridgeRoot;
  const seen = [];
  const operator = startStubOperator(bridgeRoot, seen);
  try {
    const result = await agentBridgeChat({
      apiKey: 'agent-bridge:local-user',
      messages: [{ role: 'user', content: 'Return {"ok":true}.' }],
      responseFormat: { type: 'json_object' },
      timeoutMs: 600000,
      operation: 'scriptit.completion',
    });
    assert.equal(result.attempts, 1, 'answered on the first attempt');
    assert.equal(seen.length, 1, 'exactly one bridge request was posted');
    const row = seen[0];
    const wallSeconds = row.expires_at - row.created_at;
    assert.ok(
      wallSeconds >= 599 && wallSeconds <= 601,
      `expected a ~600s window, got ${wallSeconds}s`,
    );
  } finally {
    clearInterval(operator);
    if (prev === undefined) delete process.env.SCRIPTIT_AGENT_BRIDGE_DIR;
    else process.env.SCRIPTIT_AGENT_BRIDGE_DIR = prev;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('a completed Muse draft is retained without a second writing request', async () => {
  // Nov 9, 2026 simplification: draft 1 + one fix pass, never a 4–6 draft
  // loop over 17 minutes. A stub writer returns a short generic draft
  // every time, so the gate can never pass; the generation must stop
  // after exactly 2 model calls and return publishable:false (the CLI
  // exits 2 and the job fails) instead of looping through rewrites.
  const { generateScript } = require('../scriptit/src/agents/scriptWriter');
  const prevDrafts = process.env.MUSE_SCRIPT_MAX_DRAFTS;
  delete process.env.MUSE_SCRIPT_MAX_DRAFTS;
  let calls = 0;
  const fakeChat = async () => {
    calls += 1;
    return { content: 'This is a story about trains. Trains are interesting. The end.' };
  };
  const realResearch = {
    query: 'Amtrak locomotive 160',
    gatheredAt: new Date().toISOString(),
    duckduckgo: [], duckduckgoNews: [], rss: [], yandex: [], x: [],
    wikipedia: [], hackernews: [], reddit: [], official: [],
    bing: [{
      title: 'Amtrak locomotive 160 leads a train',
      url: 'https://example.com/amtrak-160',
      source: 'Example News',
      snippet: 'Amtrak locomotive 160 leads a passenger train through the station.',
      articleText: 'Amtrak locomotive 160 leads a passenger train through the station on a clear morning.',
      hasArticleBody: true,
      date: '2026-11-01',
    }],
    errors: [],
  };
  try {
    const out = await generateScript({
      topic: 'Amtrak locomotive 160',
      duration: '1min',
      researchMode: 'thorough',
      apiKey: 'agent-bridge:local-user',
      model: 'agent-bridge',
      useCache: false,
      recordHistory: false,
      research: realResearch,
      chatCompletionFn: fakeChat,
    });
    assert.equal(calls, 1, `expected exactly one complete draft call, got ${calls}`);
    assert.equal(out.meta.timing.draftCalls, 1, 'meta must report one draft call');
    assert.ok(out.script.trim(), 'the written narration is retained');
    assert.equal(out.meta.publishable, false, 'a gate-failing draft must not be publishable');
    assert.ok((out.meta.quality.issues || []).length > 0, 'the failure must name its reasons');
  } finally {
    if (prevDrafts === undefined) delete process.env.MUSE_SCRIPT_MAX_DRAFTS;
    else process.env.MUSE_SCRIPT_MAX_DRAFTS = prevDrafts;
  }
});
