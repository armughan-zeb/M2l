'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { resolveVideoStyle, ENGINES, STYLES } = require('../lib/videoStyle');
const { stampDoodleManifest, validateMasterManifest } = require('../lib/masterManifestGuard');
const doodle = require('../adapters/doodle');
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

test('resolveVideoStyle strictly distinguishes Documentary vs Doodle Verse and rejects conflicts or unknown engines', () => {
  assert.deepEqual(
    resolveVideoStyle({ productionEngine: 'doodle', videoStyle: 'doodle_verse' }),
    { engine: ENGINES.DOODLE, style: STYLES.DOODLE_VERSE, label: 'Doodle Verse Animation', explicit: true }
  );

  assert.deepEqual(
    resolveVideoStyle({ channel: { productionEngine: 'doodle' } }),
    { engine: ENGINES.DOODLE, style: STYLES.DOODLE_VERSE, label: 'Doodle Verse Animation', explicit: true }
  );

  assert.deepEqual(
    resolveVideoStyle({ productionEngine: 'vidzeo', videoStyle: 'documentary' }),
    { engine: ENGINES.VIDZEO, style: STYLES.DOCUMENTARY, label: 'Documentary (Vidnets)', explicit: true }
  );

  assert.deepEqual(
    resolveVideoStyle({ title: 'Legacy payload without style fields' }),
    { engine: ENGINES.VIDZEO, style: STYLES.DOCUMENTARY, label: 'Documentary (Vidnets)', explicit: false }
  );

  assert.throws(
    () => resolveVideoStyle({ productionEngine: 'doodle', channel: { productionEngine: 'vidzeo' } }),
    /Conflicting video styles/i
  );

  assert.throws(
    () => resolveVideoStyle({ productionEngine: 'picgen' }),
    /Unsupported video style\/engine/i
  );
});

test('validateMasterManifest enforces strict separation between Doodle Verse and Documentary masters', () => {
  const dir = makeTempDir('muse-style-manifest-');
  try {
    const videoFile = path.join(dir, 'video.mp4');
    fs.writeFileSync(videoFile, Buffer.alloc(1024 * 1024 + 16, 1));

    // Missing doodle manifest fails fail-closed
    const missing = validateMasterManifest(videoFile, { jobId: 'r_doodle_1', engine: 'doodle' });
    assert.equal(missing.ok, false);
    assert.match(missing.reason, /doodle-manifest\.json/i);

    // Stamp valid doodle manifest
    stampDoodleManifest(videoFile, {
      jobId: 'r_doodle_1',
      environment: 'office',
      voice: 'en-US-AndrewNeural',
      durationSeconds: 30,
      framesExpected: 900,
      framesRendered: 900,
      inferRequests: [{ id: 'req1', operation: 'doodle_verse_plan' }],
    });

    assert.equal(validateMasterManifest(videoFile, { jobId: 'r_doodle_1', engine: 'doodle' }).ok, true);

    // Wrong job id refuses cross-job reuse
    const wrongJob = validateMasterManifest(videoFile, { jobId: 'r_doodle_2', engine: 'doodle' });
    assert.equal(wrongJob.ok, false);
    assert.match(wrongJob.reason, /belongs to job r_doodle_1/i);

    // Documentary validation must REFUSE a video carrying a Doodle manifest
    const wrongEngine = validateMasterManifest(videoFile, { jobId: 'r_doodle_1', engine: 'vidzeo' });
    assert.equal(wrongEngine.ok, false);
    assert.match(wrongEngine.reason, /refusing mixed-engine master/i);

    // Doodle validation must REFUSE if a Vidnets manifest is alongside it
    const vidnetsManifest = path.join(dir, 'video.manifest.json');
    fs.writeFileSync(vidnetsManifest, JSON.stringify({ job_id: 'v1', media_paths: ['a.jpg'], asset_count: 1 }), 'utf8');
    const vidnetsConflict = validateMasterManifest(videoFile, { jobId: 'r_doodle_1', engine: 'doodle' });
    assert.equal(vidnetsConflict.ok, false);
    assert.match(vidnetsConflict.reason, /Vidnets manifest.*refusing mixed-engine/i);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('adapters/video refuses OpenRouter and blocks cross-style execution', async () => {
  const dir = makeTempDir('muse-style-adapter-');
  try {
    // Calling doodle.render with a Documentary job refuses immediately
    await clearOpenRouter(async () => {
      await assert.rejects(
        () => doodle.render({ jobId: 'j1', productionEngine: 'vidzeo' }, 'Narration.', dir),
        /Style isolation violation/i
      );
    });

    // Setting an OpenRouter key fails closed in doodle.render
    process.env.OPENROUTER_API_KEY = 'sk-or-forbidden';
    await assert.rejects(
      () => doodle.render({ jobId: 'j2', productionEngine: 'doodle' }, 'Narration.', dir),
      /Muse inference is compulsory/i
    );
    delete process.env.OPENROUTER_API_KEY;
  } finally {
    delete process.env.OPENROUTER_API_KEY;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('doodle.doodleRuntimeStatus confirms bundled kit + tools in M2l and cleanGeneratedCode strips markdown fences', () => {
  const status = doodle.doodleRuntimeStatus();
  assert.equal(status.available, true, `Expected bundled doodle-verse files; missing=${status.missing.join(', ')}`);
  assert.equal(doodle.cleanGeneratedCode('```javascript\nexport const N = 60;\n```'), 'export const N = 60;');
  assert.equal(doodle.cleanGeneratedCode('```python\nprint(1)\n```'), 'print(1)');
});

test('adapters/video routes Doodle jobs exclusively to Doodle Verse without touching Vidnets render command', async () => {
  const dir = makeTempDir('muse-doodle-route-');
  const prevCmd = process.env.MUSE_RENDER_COMMAND;
  try {
    await clearOpenRouter(async () => {
      // Even if MUSE_RENDER_COMMAND points to a failing sentinel for Vidnets Documentary,
      // a Doodle job MUST never execute it.
      process.env.MUSE_RENDER_COMMAND = 'node -e "process.exit(99)"';
      // Stub doodle.render via module cache monkey-patch for routing proof without launching Chromium
      let doodleCalled = 0;
      const origRender = doodle.render;
      doodle.render = async (job, scriptText, workDir) => {
        doodleCalled += 1;
        const out = path.join(workDir, 'video.mp4');
        fs.writeFileSync(out, Buffer.alloc(1024 * 1024 + 32, 7));
        stampDoodleManifest(out, { jobId: job.jobId, environment: 'lab', framesExpected: 60, framesRendered: 60 });
        return { videoPath: out, thumbnailPath: '', meta: { engine: 'doodle', videoStyle: 'doodle_verse' } };
      };
      try {
        const result = await videoAdapter.render(
          { jobId: 'r_route_doodle', productionEngine: 'doodle', videoStyle: 'doodle_verse' },
          'A scientist tested a glowing machine.',
          dir
        );
        assert.equal(doodleCalled, 1);
        assert.equal(result.meta.engine, 'doodle');
        assert.equal(result.meta.videoStyle, 'doodle_verse');
        assert.equal(validateMasterManifest(result.videoPath, { jobId: 'r_route_doodle', engine: 'doodle' }).ok, true);
      } finally {
        doodle.render = origRender;
      }

      // Now confirm Vidnets Documentary job hits MUSE_RENDER_COMMAND (failing exit 99), NEVER Doodle
      await assert.rejects(
        () => videoAdapter.render(
          { jobId: 'r_route_doc', productionEngine: 'vidzeo', videoStyle: 'documentary' },
          'Documentary script.',
          dir
        ),
        /exited with code 99|Muse render command/i
      );
    });
  } finally {
    if (prevCmd === undefined) delete process.env.MUSE_RENDER_COMMAND;
    else process.env.MUSE_RENDER_COMMAND = prevCmd;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('doodle.inferWithMuse routes through local bridge or command without OpenRouter and helper functions format inputs safely', async () => {
  const dir = makeTempDir('muse-doodle-infer-');
  const prevBridge = process.env.VIDNETS_AGENT_BRIDGE_DIR;
  try {
    await clearOpenRouter(async () => {
      // 1. Custom inferFn path (used in tests/harness)
      const direct = await doodle.inferWithMuse({
        operation: 'doodle_verse_plan',
        messages: [{ role: 'user', content: 'test' }],
        responseFormat: doodle.PLAN_SCHEMA,
        jobId: 'r_infer_1',
        workDir: dir,
        inferFn: async (req) => {
          assert.equal(req.operation, 'doodle_verse_plan');
          return { content: JSON.stringify({ environment: 'office', planSummary: 'ok', customOutfits: {}, storyJs: 'export const N = 30;', audioPy: 'pass', thumbSpec: {} }) };
        },
      });
      assert.equal(direct.data.environment, 'office');

      // 2. File bridge inbox path (answered as if by resident spool / hook)
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
                content: JSON.stringify({ storyJs: 'export const FIXED = true;', notes: `repaired ${req.id}` }),
              }));
            }
          }
        } catch { /* ignore */ }
      }, 30);

      try {
        const bridged = await doodle.inferWithMuse({
          operation: 'doodle_verse_repair',
          messages: [{ role: 'user', content: 'repair' }],
          responseFormat: doodle.REPAIR_SCHEMA,
          jobId: 'r_infer_2',
          workDir: dir,
          timeoutMs: 10000,
        });
        assert.equal(bridged.data.storyJs, 'export const FIXED = true;');
      } finally {
        clearInterval(watcher);
      }

      // 3. groupWordBeats + mergeCustomOutfitsIntoCast
      const beats = doodle.groupWordBeats(
        [
          { w: 'One', s: 0.0, e: 0.5 },
          { w: 'two', s: 0.5, e: 1.0 },
          { w: 'three', s: 1.0, e: 5.2 },
        ],
        6.0
      );
      assert.equal(beats.length, 1);
      assert.equal(beats[0].startFrame, 0);
      assert.equal(beats[0].endFrame, 156);

      const castFile = path.join(dir, 'cast.js');
      fs.writeFileSync(castFile, 'export const OUT = {\n  leo: { jacket: "#111" },\n};\n');
      doodle.mergeCustomOutfitsIntoCast(castFile, { astronaut: { jacket: '#fff', hat: 'helmet' } });
      const updatedCast = fs.readFileSync(castFile, 'utf8');
      assert.match(updatedCast, /astronaut:/);
    });
  } finally {
    if (prevBridge === undefined) delete process.env.VIDNETS_AGENT_BRIDGE_DIR;
    else process.env.VIDNETS_AGENT_BRIDGE_DIR = prevBridge;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('muse-agent capabilities includes doodle-verse and style-routing markers', () => {
  const { capabilities } = require('../muse-agent');
  const caps = capabilities();
  assert.ok(caps.includes('doodle-verse'));
  assert.ok(caps.includes('style-routing'));
  assert.ok(caps.includes('style:documentary'));
  assert.ok(caps.includes('style:doodle_verse'));
});
