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

test('normalizeShots differentiates repeated planner queries so shots cannot search the same thing', () => {
  const shots = documentary.normalizeShots([
    { beat: 'hook', kind: 'video', durationSec: 6, query: 'GTA VI official trailer Vice City neon skyline night aerial', title: 'THE WAIT IS OVER', sub: 'x', zoomIn: true, center: false },
    { beat: 'background', kind: 'video', durationSec: 6, query: 'GTA VI official trailer Vice City neon skyline night aerial', title: 'BACK TO VICE CITY', sub: 'x', zoomIn: true, center: false },
    { beat: 'development', kind: 'video', durationSec: 6, query: 'GTA VI official trailer Vice City neon skyline night aerial', title: 'LUCIA CAMINOS', sub: 'x', zoomIn: true, center: false },
  ], 540);
  assert.equal(shots[0].query, 'GTA VI official trailer Vice City neon skyline night aerial');
  assert.match(shots[1].query, /back to vice city$/);
  assert.match(shots[2].query, /lucia caminos$/);
  assert.notEqual(shots[0].query, shots[1].query);
  assert.notEqual(shots[1].query, shots[2].query);
});

test('pickUnusedVideoCandidate never returns a video ID already used by an earlier shot', () => {
  const candidates = [
    { id: 'QdBZY2fkU-0', title: 'Grand Theft Auto VI Trailer 1' },
    { id: 'rz1B4B2yBZY', title: 'Grand Theft Auto VI Launch Trailer' },
    { id: '_yl5ELcmugI', title: 'Grand Theft Auto VI: Out Now' },
  ];
  const used = new Set();
  assert.equal(documentary.pickUnusedVideoCandidate(candidates, used).id, 'QdBZY2fkU-0');
  used.add('QdBZY2fkU-0');
  assert.equal(documentary.pickUnusedVideoCandidate(candidates, used).id, 'rz1B4B2yBZY');
  used.add('rz1B4B2yBZY');
  assert.equal(documentary.pickUnusedVideoCandidate(candidates, used).id, '_yl5ELcmugI');
  used.add('_yl5ELcmugI');
  assert.equal(documentary.pickUnusedVideoCandidate(candidates, used), null);

  assert.equal(documentary.normalizeYoutubeVideoId('https://www.youtube.com/watch?v=QdBZY2fkU-0'), 'QdBZY2fkU-0');
  assert.equal(documentary.normalizeYoutubeVideoId('https://youtu.be/rz1B4B2yBZY'), 'rz1B4B2yBZY');
  assert.equal(documentary.normalizeYoutubeVideoId('QdBZY2fkU-0'), 'QdBZY2fkU-0');
  assert.equal(documentary.normalizeYoutubeVideoId('https://example.com/not-a-video'), '');
});

test('pickLeastUsedFallback spreads last-resort repeats instead of re-picking the first clip', () => {
  const fallbacks = [{ assetPath: 'footage/shot-1.mp4' }, { assetPath: 'footage/shot-2.mp4' }];
  const usage = new Map([['footage/shot-1.mp4', 2], ['footage/shot-2.mp4', 0]]);
  assert.equal(documentary.pickLeastUsedFallback(fallbacks, usage).assetPath, 'footage/shot-2.mp4');
  usage.set('footage/shot-2.mp4', 1);
  assert.equal(documentary.pickLeastUsedFallback(fallbacks, usage).assetPath, 'footage/shot-2.mp4');
  usage.set('footage/shot-2.mp4', 2);
  assert.equal(documentary.pickLeastUsedFallback(fallbacks, usage).assetPath, 'footage/shot-1.mp4');
  assert.equal(documentary.pickLeastUsedFallback([], usage), null);
});

test('selectTtsEngine keeps Muse TTS primary even when a voice name is set', () => {
  assert.equal(documentary.selectTtsEngine({ explicitVoice: 'en-US-ChristopherNeural', hatchTtsAvailable: true }), 'hatch');
  assert.equal(documentary.selectTtsEngine({ explicitVoice: '', hatchTtsAvailable: true }), 'hatch');
  assert.equal(documentary.selectTtsEngine({ explicitVoice: 'en-US-ChristopherNeural', hatchTtsAvailable: false }), 'edge');
  assert.equal(documentary.selectTtsEngine({ explicitVoice: '', hatchTtsAvailable: false }), 'edge');
});

test('buildShotsPlanMessages forbids repeating a query across shots', () => {
  const msgs = documentary.buildShotsPlanMessages({
    title: 'GTA VI',
    scriptText: 'Test narration',
    voiceDurationSec: 54,
    targetDurationMinutes: 1,
    channel: {},
  }).map((m) => m.content).join('\n');
  assert.match(msgs, /NEVER repeat a `query`/);
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
  // No `loop` on the OffthreadVideo element: in Remotion 4.0.547 it leaks
  // into an internal <Img> Sequence with no durationInFrames and kills the
  // render at frame 0 (proven live in job gta-vi-doc-fix-proof, 2026-11-27).
  // (The template comment mentions loop, so match the element itself.)
  const videoTag = compNoCap.match(/<OffthreadVideo[\s\S]*?\/>/)[0];
  assert.doesNotMatch(videoTag, /loop/);
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




test('relaxedImageQueries shortens a specific shot query for the image lane', () => {
  assert.deepEqual(
    documentary.relaxedImageQueries('GTA VI PS5 Xbox Series X launch box art'),
    ['GTA VI PS5 Xbox Series X launch box art', 'GTA VI PS5 Xbox', 'GTA VI'],
  );
  assert.deepEqual(documentary.relaxedImageQueries('GTA VI'), ['GTA VI']);
  assert.deepEqual(documentary.relaxedImageQueries('  '), []);
});

test('extractImageUrls pulls Yandex origUrl, Bing murl and direct URLs, never JSON-spanning garbage', () => {
  const html = [
    '{"origUrl":"https:\\/\\/i.pinimg.com\\/736x\\/78\\/89\\/92\\/788992678db43890389e3cb641c3bcda.jpg"}',
    'murl&quot;:&quot;https://static0.gamerantimages.com/wordpress/wp-content/uploads/2023/12/gta-5-cover.jpg&quot;',
    '<img src="https://images.ctfassets.net/abc/hero.jpg">',
    'https://ru.pinterest.com/pin/404268504068462670/&quot;},&quot;freshness&quot;:&quot;normal&quot;,&quot;preview&quot;:[{&quot;url&quot;:&quot;https://i.pinimg.com/736x/78/89/92/788992678db43890389e3cb641c3bcda.jpg&quot;',
  ].join('\n');
  const urls = documentary.extractImageUrls(html);
  assert.ok(urls.includes('https://i.pinimg.com/736x/78/89/92/788992678db43890389e3cb641c3bcda.jpg'));
  assert.ok(urls.includes('https://static0.gamerantimages.com/wordpress/wp-content/uploads/2023/12/gta-5-cover.jpg'));
  assert.ok(urls.includes('https://images.ctfassets.net/abc/hero.jpg'));
  assert.ok(!urls.some((u) => u.includes('freshness') || u.includes('pinterest.com/pin')));
});

test('extractImageUrls drops search-engine chrome hosts', () => {
  const html = [
    'https://yastatic.net/s3/fiji-static/_/sprite.png',
    'https://www.bing.com/sa/simg/facebook_sharing_5.png',
    'https://i.imgur.com/nEyIaWg.jpg',
  ].join('\n');
  const urls = documentary.extractImageUrls(html);
  assert.deepEqual(urls, ['https://i.imgur.com/nEyIaWg.jpg']);
});

test('documentary_media agent schema and messages carry the shot', () => {
  const schema = documentary.MEDIA_AGENT_SCHEMA;
  assert.equal(schema.type, 'json_schema');
  assert.equal(schema.json_schema.name, 'documentary_media');
  assert.deepEqual(schema.json_schema.schema.required, ['candidates']);
  assert.equal(schema.json_schema.schema.properties.candidates.type, 'array');

  const messages = documentary.buildMediaAgentMessages({
    title: 'Franklin Story',
    topicTag: 'FRANKLIN',
    shot: { beat: 'hook', kind: 'video', query: 'GTA V Franklin Clinton gameplay', dur: 210 },
  });
  const text = messages.map((m) => m.content).join('\n');
  assert.match(text, /GTA V Franklin Clinton gameplay/);
  assert.match(text, /Franklin Story/);
  assert.match(text, /hook/);
});

test('normalizeAgentMediaCandidates keeps real candidates, drops invented ones', () => {
  const out = documentary.normalizeAgentMediaCandidates({
    candidates: [
      { type: 'video', videoId: 'dQw4w9WgXcQ', title: 'Real clip' },
      { type: 'video', url: 'https://www.youtube.com/watch?v=76bdrSeL6uI', title: 'From URL' },
      { type: 'video', videoId: 'dQw4w9WgXcQ', title: 'Duplicate ID' },
      { type: 'video', videoId: 'not-an-id', title: 'Invented ID' },
      { type: 'image', url: 'https://example.com/franklin.jpg', title: 'Real image' },
      { type: 'image', url: 'https://example.com/franklin.jpg', title: 'Duplicate image' },
      { type: 'image', url: '/search?q=franklin', title: 'Search page, not an image' },
      { type: 'audio', url: 'https://example.com/x.mp3' },
      { type: 'video', videoId: 'abc12345678', title: 'Third video' },
      { type: 'video', videoId: 'xyz12345678', title: 'Fourth video (over the cap)' },
    ],
  });
  assert.equal(out.length, 4, 'caps at 4, drops dupes and invented entries');
  assert.deepEqual(out.map((c) => c.type), ['video', 'video', 'image', 'video']);
  assert.equal(out[0].videoId, 'dQw4w9WgXcQ');
  assert.equal(out[1].videoId, '76bdrSeL6uI');
  assert.equal(out[2].url, 'https://example.com/franklin.jpg');
  assert.deepEqual(documentary.normalizeAgentMediaCandidates({}), []);
  assert.deepEqual(documentary.normalizeAgentMediaCandidates(null), []);
});

test('fetchAgentMediaCandidates falls back to [] when the agent transport fails', async () => {
  const prev = process.env.MUSE_INFERENCE_URL;
  process.env.MUSE_INFERENCE_URL = 'http://127.0.0.1:1/nope';
  try {
    const out = await documentary.fetchAgentMediaCandidates({
      shot: { beat: 'hook', kind: 'video', query: 'GTA V Franklin Clinton gameplay', dur: 210 },
      title: 'Franklin Story',
      topicTag: 'FRANKLIN',
      jobId: 'test-job',
      workDir: os.tmpdir(),
      timeoutMs: 30000,
    });
    assert.deepEqual(out, []);
  } finally {
    if (prev === undefined) delete process.env.MUSE_INFERENCE_URL;
    else process.env.MUSE_INFERENCE_URL = prev;
  }
});

test('worker validator accepts a valid documentary_media answer and rejects a bad one', () => {
  const { execFileSync } = require('node:child_process');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'doc-media-validator-'));
  const requestFile = path.join(dir, 'request.json');
  const goodFile = path.join(dir, 'good.json');
  const badFile = path.join(dir, 'bad.json');
  const request = {
    type: 'inference',
    request: {
      id: 'a'.repeat(32),
      operation: 'documentary_media',
      kind: 'text',
      response_format: documentary.MEDIA_AGENT_SCHEMA,
    },
  };
  fs.writeFileSync(requestFile, JSON.stringify(request));
  fs.writeFileSync(goodFile, JSON.stringify({ candidates: [{ type: 'video', videoId: '76bdrSeL6uI', title: 'Heist' }, { type: 'image', url: 'https://example.com/f.jpg', title: 'Still' }] }));
  fs.writeFileSync(badFile, JSON.stringify({ clips: [] }));
  const venvPython = path.join(__dirname, '..', 'vidnets', '.venv', 'bin', 'python');
  const validator = path.join(__dirname, '..', 'scripts', 'resident-inference-worker', 'validate_answer.py');
  execFileSync(venvPython, [validator, goodFile, requestFile], { stdio: 'pipe' });
  assert.throws(() => execFileSync(venvPython, [validator, badFile, requestFile], { stdio: 'pipe' }));
});
