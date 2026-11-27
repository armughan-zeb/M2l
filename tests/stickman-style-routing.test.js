'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { resolveVideoStyle, ENGINES, STYLES } = require('../lib/videoStyle');
const { stampStickmanManifest, validateMasterManifest } = require('../lib/masterManifestGuard');
const stickman = require('../adapters/stickman');
const videoAdapter = require('../adapters/video');

function makeTempDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function clearOpenRouter(fn) {
  const saved = {};
  for (const key of ['OPENROUTER_API_KEY', 'VIDZEO_OPENROUTER_API_KEY', 'SCRIPTIT_API_KEY', 'SCRIPTIT_OPENROUTER_API_KEY']) {
    saved[key] = process.env[key];
    delete process.env[key];
  }
  return Promise.resolve()
    .then(fn)
    .finally(() => {
      for (const [k, v] of Object.entries(saved)) {
        if (v === undefined) delete process.env[k];
        else process.env[k] = v;
      }
    });
}

test('resolveVideoStyle strictly distinguishes Documentary vs 2D Stickman and rejects removed Doodle Verse or conflicts', () => {
  for (const alias of ['stickman', '2d-stickman', '2d_stickman', '2d stickman']) {
    assert.deepEqual(
      resolveVideoStyle({ productionEngine: alias }),
      { engine: ENGINES.STICKMAN, style: STYLES.STICKMAN, label: '2D Stickman', explicit: true }
    );
  }

  assert.deepEqual(
    resolveVideoStyle({ channel: { productionEngine: 'stickman', videoStyle: '2d_stickman' } }),
    { engine: ENGINES.STICKMAN, style: STYLES.STICKMAN, label: '2D Stickman', explicit: true }
  );

  for (const alias of ['vidzeo', 'documentary', 'one_minute_documentary', 'one-minute-documentary']) {
    assert.deepEqual(
      resolveVideoStyle({ productionEngine: alias }),
      { engine: ENGINES.DOCUMENTARY, style: STYLES.DOCUMENTARY, label: 'Documentary (Remotion)', explicit: true }
    );
  }

  assert.deepEqual(
    resolveVideoStyle({ title: 'Legacy payload without style fields' }),
    { engine: ENGINES.DOCUMENTARY, style: STYLES.DOCUMENTARY, label: 'Documentary (Remotion)', explicit: false }
  );

  // Removed Doodle Verse & local-only Pikgen must fail closed in Muse
  assert.throws(
    () => resolveVideoStyle({ productionEngine: 'doodle', videoStyle: 'doodle_verse' }),
    /Unsupported video style\/engine/i
  );
  assert.throws(
    () => resolveVideoStyle({ productionEngine: 'picgen' }),
    /Unsupported video style\/engine/i
  );

  assert.throws(
    () => resolveVideoStyle({ productionEngine: 'stickman', channel: { productionEngine: 'vidzeo' } }),
    /Conflicting video styles/i
  );
});

test('validateMasterManifest enforces strict separation between 2D Stickman and Documentary masters', () => {
  const dir = makeTempDir('muse-stickman-manifest-');
  try {
    const videoFile = path.join(dir, 'video.mp4');
    fs.writeFileSync(videoFile, Buffer.alloc(1024 * 1024 + 16, 1));

    const missing = validateMasterManifest(videoFile, { jobId: 'r_stick_1', engine: 'stickman' });
    assert.equal(missing.ok, false);
    assert.match(missing.reason, /stickman-manifest\.json/i);

    stampStickmanManifest(videoFile, {
      jobId: 'r_stick_1',
      profile: 'wide',
      format: 'wide',
      voice: 'en-US-ChristopherNeural',
      soundBed: 'jungle',
      durationSeconds: 30,
      lineCount: 7,
      cueCount: 11,
      inferRequests: [{ id: 'req1', operation: 'stickman_shots_plan' }],
    });

    assert.equal(validateMasterManifest(videoFile, { jobId: 'r_stick_1', engine: 'stickman' }).ok, true);

    const wrongJob = validateMasterManifest(videoFile, { jobId: 'r_stick_2', engine: 'stickman' });
    assert.equal(wrongJob.ok, false);
    assert.match(wrongJob.reason, /belongs to job r_stick_1/i);

    // Documentary validation must REFUSE a video carrying a 2D Stickman manifest
    const wrongEngine = validateMasterManifest(videoFile, { jobId: 'r_stick_1', engine: 'documentary' });
    assert.equal(wrongEngine.ok, false);
    assert.match(wrongEngine.reason, /refusing mixed-engine master/i);

    // Stickman validation must REFUSE if a Documentary manifest sits beside it
    const docManifest = path.join(dir, 'video.documentary-manifest.json');
    fs.writeFileSync(docManifest, JSON.stringify({ engine: 'documentary', style: 'documentary', job_id: 'd1', shot_count: 4, duration_seconds: 60 }), 'utf8');
    const docConflict = validateMasterManifest(videoFile, { jobId: 'r_stick_1', engine: 'stickman' });
    assert.equal(docConflict.ok, false);
    assert.match(docConflict.reason, /Documentary manifest.*refusing mixed-engine/i);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('adapters/video refuses OpenRouter and isolates 2D Stickman from Vidnets rendering', async () => {
  const dir = makeTempDir('muse-stickman-adapter-');
  const prevCmd = process.env.MUSE_RENDER_COMMAND;
  try {
    await clearOpenRouter(async () => {
      await assert.rejects(
        () => stickman.render({ jobId: 'j1', productionEngine: 'vidzeo' }, 'Narration.', dir),
        /Style isolation violation/i
      );

      // Even with a failing Vidnets MUSE_RENDER_COMMAND sentinel, 2D Stickman routes strictly to adapters/stickman
      process.env.MUSE_RENDER_COMMAND = 'node -e "process.exit(99)"';
      let stickmanCalled = 0;
      const origRender = stickman.render;
      stickman.render = async (job, scriptText, workDir) => {
        stickmanCalled += 1;
        const out = path.join(workDir, 'video.mp4');
        fs.writeFileSync(out, Buffer.alloc(1024 * 1024 + 32, 7));
        stampStickmanManifest(out, { jobId: job.jobId, profile: 'long', durationSeconds: 600, lineCount: 40 });
        return { videoPath: out, thumbnailPath: '', meta: { engine: 'stickman', videoStyle: '2d_stickman' } };
      };
      try {
        const result = await videoAdapter.render(
          { jobId: 'r_route_stick', productionEngine: 'stickman', videoStyle: '2d_stickman' },
          'A stickman stepped out onto a cliff.',
          dir
        );
        assert.equal(stickmanCalled, 1);
        assert.equal(result.meta.engine, 'stickman');
        assert.equal(result.meta.videoStyle, '2d_stickman');
        assert.equal(validateMasterManifest(result.videoPath, { jobId: 'r_route_stick', engine: 'stickman' }).ok, true);
      } finally {
        stickman.render = origRender;
      }

      // Documentary job hits MUSE_RENDER_COMMAND (failing exit 99), NEVER 2D Stickman
      await assert.rejects(
        () => videoAdapter.render(
          { jobId: 'r_route_doc', productionEngine: 'vidzeo', videoStyle: 'documentary' },
          'Documentary script.',
          dir
        ),
        /exited with code 99|Muse render command/i
      );
    });

    process.env.OPENROUTER_API_KEY = 'sk-or-forbidden';
    await assert.rejects(
      () => stickman.render({ jobId: 'j2', productionEngine: 'stickman' }, 'Narration.', dir),
      /Muse inference is compulsory/i
    );
  } finally {
    delete process.env.OPENROUTER_API_KEY;
    if (prevCmd === undefined) delete process.env.MUSE_RENDER_COMMAND;
    else process.env.MUSE_RENDER_COMMAND = prevCmd;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('stickmanRuntimeStatus confirms bundled kit/fonts/sounds and inferWithMuse routes cleanly without OpenRouter', async () => {
  const status = stickman.stickmanRuntimeStatus();
  assert.equal(status.available, true, `Expected bundled 2d-stickman files; missing=${status.missing.join(', ')}`);
  assert.equal(stickman.cleanGeneratedCode('```javascript\nexport function buildShots() {}\n```'), 'export function buildShots() {}');

  const dir = makeTempDir('muse-stickman-infer-');
  const prevBridge = process.env.VIDNETS_AGENT_BRIDGE_DIR;
  try {
    await clearOpenRouter(async () => {
      const direct = await stickman.inferWithMuse({
        operation: 'stickman_shots_plan',
        messages: [{ role: 'user', content: 'test' }],
        responseFormat: stickman.SHOTS_PLAN_SCHEMA,
        jobId: 'r_infer_s1',
        workDir: dir,
        inferFn: async (req) => {
          assert.equal(req.operation, 'stickman_shots_plan');
          return { content: JSON.stringify({ summary: 'ok', shotsJs: 'export function buildShots() { return { S: [], CUES: [] }; }' }) };
        },
      });
      assert.equal(direct.data.summary, 'ok');

      const bridgeDir = path.join(dir, 'bridge');
      process.env.VIDNETS_AGENT_BRIDGE_DIR = bridgeDir;
      const watcher = setInterval(() => {
        try {
          for (const entry of fs.readdirSync(bridgeDir)) {
            const reqFile = path.join(bridgeDir, entry, 'request.json');
            const resFile = path.join(bridgeDir, entry, 'response.json');
            if (fs.existsSync(reqFile) && !fs.existsSync(resFile)) {
              const req = JSON.parse(fs.readFileSync(reqFile, 'utf8'));
              fs.writeFileSync(resFile, JSON.stringify({
                content: JSON.stringify({ shotsJs: 'export const REPAIRED = true;', notes: `fixed ${req.id}` }),
              }));
            }
          }
        } catch { /* ignore */ }
      }, 30);

      try {
        const bridged = await stickman.inferWithMuse({
          operation: 'stickman_shots_repair',
          messages: [{ role: 'user', content: 'repair' }],
          responseFormat: stickman.SHOTS_REPAIR_SCHEMA,
          jobId: 'r_infer_s2',
          workDir: dir,
          timeoutMs: 10000,
        });
        assert.match(bridged.data.shotsJs, /REPAIRED/);
      } finally {
        clearInterval(watcher);
      }
    });
  } finally {
    if (prevBridge === undefined) delete process.env.VIDNETS_AGENT_BRIDGE_DIR;
    else process.env.VIDNETS_AGENT_BRIDGE_DIR = prevBridge;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('muse-agent capabilities advertises both one-minute-documentary and 2d-stickman and excludes removed doodle-verse', () => {
  const { capabilities } = require('../muse-agent');
  const caps = capabilities();
  assert.ok(caps.includes('one-minute-documentary'));
  assert.ok(caps.includes('remotion-documentary'));
  assert.ok(caps.includes('2d-stickman'));
  assert.ok(caps.includes('style:2d_stickman'));
  assert.ok(caps.includes('style:documentary'));
  assert.ok(caps.includes('style-routing'));
  assert.equal(caps.includes('doodle-verse'), false);
  assert.equal(caps.includes('style:doodle_verse'), false);
});
