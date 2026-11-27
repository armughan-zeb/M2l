'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const documentary = require('../adapters/documentary');
const stickman = require('../adapters/stickman');
const scriptAdapter = require('../adapters/script');
const { createFrameProgressReporter, readStageCheckpoint, writeStageCheckpoint } = require('../lib/museBridgeInfer');

test('one_minute_documentary skill assets and Remotion template are bundled', () => {
  const status = documentary.documentaryRuntimeStatus();
  assert.equal(status.available, true, `missing files: ${status.missing.join(', ')}`);
  assert.match(status.root, /one_minute_documentary$/);
  assert.equal(documentary.FPS, 30);
  assert.equal(documentary.WIDTH, 1920);
  assert.equal(documentary.HEIGHT, 1080);
});

test('normalizeShots packs 5 documentary beats into exact totalFrames without gaps', () => {
  const totalFrames = 1800;
  const shots = documentary.normalizeShots([
    { beat: 'hook', kind: 'video', durationSec: 8, query: 'michael jordan dunk 1988', title: 'THE ICON', sub: 'Six championships.', zoomIn: true, center: false },
    { beat: 'background', kind: 'image', durationSec: 12, query: 'chicago stadium 1984', title: 'THE DRAFT', sub: 'Pick number three.', zoomIn: false, center: false },
    { beat: 'development', kind: 'video', durationSec: 16, query: 'chicago bulls 1991 finals', title: 'DYNASTY', sub: 'Three straight rings.', zoomIn: true, center: false },
    { beat: 'impact', kind: 'image', durationSec: 14, query: 'air jordan 1 sneaker', title: 'THE BRAND', sub: 'Changed sportswear forever.', zoomIn: true, center: false },
    { beat: 'closing', kind: 'video', durationSec: 10, query: 'basketball hoop silhouette', title: 'NO. 23', sub: 'The standard.', zoomIn: false, center: true },
  ], totalFrames);

  assert.equal(shots.length, 5);
  let cursor = 0;
  for (const shot of shots) {
    assert.equal(shot.from, cursor);
    assert.ok(shot.dur >= 60);
    cursor += shot.dur;
  }
  assert.equal(cursor, totalFrames);
  assert.equal(shots[4].center, true);
});

test('generateCompositionTsx and generateRemotionRootTsx emit 1080p@30fps Documentary TSX', () => {
  const tsx = documentary.generateCompositionTsx({
    topicTag: 'BASKETBALL',
    headerText: 'THE MICHAEL JORDAN STORY',
    shots: [
      { from: 0, dur: 240, assetType: 'video', assetPath: 'clips/shot01.mp4', title: 'SIX RINGS', sub: 'An unmatched run.', zoomIn: true, center: false },
      { from: 240, dur: 240, assetType: 'image', assetPath: 'images/shot02.jpg', title: 'NO. 23', sub: '', zoomIn: false, center: true },
    ],
  });
  assert.match(tsx, /TOPIC_TAG = "BASKETBALL"/);
  assert.match(tsx, /HEADER_TEXT = "THE MICHAEL JORDAN STORY"/);
  assert.match(tsx, /video="clips\/shot01\.mp4"/);
  assert.match(tsx, /image="images\/shot02\.jpg"/);

  const root = documentary.generateRemotionRootTsx(1800);
  assert.match(root, /id="Documentary"/);
  assert.match(root, /durationInFrames=\{1800\}/);
  assert.match(root, /fps=\{30\}/);
  assert.match(root, /width=\{1920\}/);
  assert.match(root, /height=\{1080\}/);
});

test('isValidImageBuffer recognizes JPEG/PNG/WEBP >= 1KB and rejects tiny or HTML payloads', () => {
  const jpg = Buffer.concat([Buffer.from([0xff, 0xd8, 0xff, 0xe0]), Buffer.alloc(1200, 1)]);
  const png = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(1200, 1)]);
  const html = Buffer.from('<html><body>blocked</body></html>' + 'x'.repeat(1200));
  const tiny = Buffer.from([0xff, 0xd8, 0xff, 0xe0]);

  assert.equal(documentary.isValidImageBuffer(jpg), '.jpg');
  assert.equal(documentary.isValidImageBuffer(png), '.png');
  assert.equal(documentary.isValidImageBuffer(html), '');
  assert.equal(documentary.isValidImageBuffer(tiny), '');
});

test('adapters/documentary and adapters/stickman enforce strict style isolation', async () => {
  const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-style-isolation-'));
  try {
    await assert.rejects(
      () => documentary.render({ jobId: 'j_doc_reject', productionEngine: 'stickman' }, 'Hello world.', workDir),
      /Style isolation violation/
    );
    await assert.rejects(
      () => stickman.render({ jobId: 'j_stick_reject', productionEngine: 'vidzeo' }, 'Hello world.', workDir),
      /Style isolation violation/
    );
  } finally {
    fs.rmSync(workDir, { recursive: true, force: true });
  }
});

test('adapters/script routes documentary and stickman jobs via Muse inference without OpenRouter', async () => {
  const prevCommand = process.env.MUSE_SCRIPT_COMMAND;
  const prevOpenRouter = process.env.OPENROUTER_API_KEY;
  delete process.env.MUSE_SCRIPT_COMMAND;
  delete process.env.OPENROUTER_API_KEY;

  const docWork = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-doc-script-'));
  const stickWork = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-stick-script-'));
  const seenOps = [];
  const sampleNarration = Array(150).fill('word').join(' ');

  try {
    const docRes = await scriptAdapter.generateScript(
      { jobId: 'j_doc_1', title: 'How Concrete Built Rome', productionEngine: 'vidzeo', targetDurationMinutes: 1 },
      docWork,
      {
        inferFn: async (opts) => {
          seenOps.push(opts.operation);
          return { id: 'inf_doc_1', data: sampleNarration };
        }
      }
    );
    assert.equal(docRes.meta.operation, 'documentary_script');
    assert.equal(fs.existsSync(path.join(docWork, 'script.txt')), true);

    const stickRes = await scriptAdapter.generateScript(
      { jobId: 'j_stick_1', title: 'Why Meetings Waste Time', productionEngine: 'stickman', targetDurationMinutes: 1 },
      stickWork,
      {
        inferFn: async (opts) => {
          seenOps.push(opts.operation);
          return { id: 'inf_stick_1', data: sampleNarration };
        }
      }
    );
    assert.equal(stickRes.meta.operation, 'stickman_script');
    assert.deepEqual(seenOps, ['documentary_script', 'stickman_script']);
  } finally {
    if (prevCommand !== undefined) process.env.MUSE_SCRIPT_COMMAND = prevCommand;
    if (prevOpenRouter !== undefined) process.env.OPENROUTER_API_KEY = prevOpenRouter;
    fs.rmSync(docWork, { recursive: true, force: true });
    fs.rmSync(stickWork, { recursive: true, force: true });
  }
});

test('createFrameProgressReporter throttles noise and formats CMS frame counters cleanly', () => {
  const events = [];
  const logs = [];
  const reportFrame = createFrameProgressReporter(
    (pct, msg, adv) => events.push({ pct, msg, adv }),
    { prefix: '[documentary]', basePct: 70, spanPct: 25, intervalMs: 60000, logger: { info: m => logs.push(m) } }
  );
  assert.equal(reportFrame(1, 1800), true);   // initial frame reports immediately
  assert.equal(reportFrame(2, 1800), false);  // same percent within intervalMs is throttled
  assert.equal(reportFrame(180, 1800), true); // pct jumped -> 73%
  assert.equal(reportFrame(1800, 1800), true); // final frame always emits
  assert.equal(events.length, 3);
  assert.match(events[1].msg, /\bframe 180\/1800\b/);
  assert.equal(events[2].pct, 95);
  assert.equal(logs.length, 3);
});

test('documentary scales cleanly to 20-minute CMS duration, voice, 9:16 aspect, and captions toggle', () => {
  const { defaultVoiceForStyle } = require('../lib/videoStyle');
  const longJob = {
    jobId: 'j_20min',
    title: 'How the Panama Canal Was Built',
    productionEngine: 'vidzeo',
    targetDurationMinutes: 20,
    targetWordCount: 3000,
    voice: 'en-GB-RyanNeural',
    documentaryOptions: { aspect: '9:16', captionsEnabled: false, width: 1080, height: 1920 },
    channel: { name: 'History Deep Dive', niche: 'Engineering', scriptSettings: { tone: 'Cinematic', rules: 'Dates verified' } },
  };
  assert.equal(defaultVoiceForStyle(longJob), 'en-GB-RyanNeural');

  const scriptMsgs = scriptAdapter.buildScriptMessages(longJob).map(m => m.content).join('\n');
  assert.match(scriptMsgs, /20-minute/);
  assert.match(scriptMsgs, /3000 words/);
  assert.match(scriptMsgs, /Dates verified/);

  const shotMsgs = documentary.buildShotsPlanMessages({
    title: longJob.title,
    scriptText: 'Test narration',
    voiceDurationSec: 1200,
    targetDurationMinutes: 20,
    channel: longJob.channel,
  }).map(m => m.content).join('\n');
  assert.match(shotMsgs, /1200\.00s/);
  assert.match(shotMsgs, /~171 shots/);

  const rootVertical = documentary.generateRemotionRootTsx(36000, { width: 1080, height: 1920 });
  assert.match(rootVertical, /durationInFrames=\{36000\}/);
  assert.match(rootVertical, /width=\{1080\}/);
  assert.match(rootVertical, /height=\{1920\}/);

  const compNoCap = documentary.generateCompositionTsx({
    topicTag: 'HISTORY',
    headerText: 'PANAMA CANAL',
    shots: [{ from: 0, dur: 240, assetType: 'video', assetPath: 'footage/shot-1.mp4', title: 'THE CUT', sub: '', zoomIn: true, center: false }],
    captionsEnabled: false,
  });
  assert.match(compNoCap, /loop/);
  assert.doesNotMatch(compNoCap, /<Caption /);
});

test('readStageCheckpoint and writeStageCheckpoint persist atomic digest-scoped checkpoints', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-ckpt-'));
  const file = path.join(dir, 'checkpoint.json');
  try {
    assert.equal(readStageCheckpoint(file, 'digest_a'), null);
    writeStageCheckpoint(file, 'digest_a', { tts: { durationSec: 1200 } });
    writeStageCheckpoint(file, 'digest_a', { shotsPlan: { count: 171 } });
    const loaded = readStageCheckpoint(file, 'digest_a');
    assert.equal(loaded.stages.tts.durationSec, 1200);
    assert.equal(loaded.stages.shotsPlan.count, 171);
    // Digest change invalidates prior checkpoint cleanly
    assert.equal(readStageCheckpoint(file, 'digest_b'), null);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});



