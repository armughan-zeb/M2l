'use strict';

// CMS progress outbox (Nov 28, 2026 — proper CMS integration).
//
// Before this change, a report() made while the CMS was down was LOST:
// report() retried 3x, logged a warning and returned, and only the latest
// stage survived in progress.json. After a hours-long outage the CMS job
// page jumped from e.g. "script 10%" straight to "video 99%", with every
// stage in between silently dropped, and nothing on reconnect replayed
// them (the only catch-up was the CMS-initiated syncSavedProgress pull).
//
// These tests pin the fix: failed reports are queued durably in
// work/<jobId>/progress-outbox.json (the file IS the queue, so a restart
// does not lose it) and flushed in order on reconnect — by the next
// report's pre-flush, by the keepalive, and by the daemon heartbeat.
// Heartbeats/keepalives never throw into job execution, and every stage
// transition emits a report (executeJob used to end at "video 99% —
// Uploading the master" with no delivered report).
//
// The end-to-end tests use FFmpeg to generate a 61s 1080p fixture and run
// the local worker against stub CMS clients. Set
// MUSE_RUN_E2E_PROGRESS_TEST=0 to omit them when running a short
// diagnostic (same gate as daemon-progress-checkpoints.test.js).

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { execFile, spawn } = require('node:child_process');
const { test } = require('node:test');

// Scratch work root BEFORE requiring the worker modules: lib/config.js
// reads MUSE_WORK_DIR at load time. Fast CMS retry sleeps so a whole
// outage run does not burn seconds per failed report.
const workRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'cms-progress-outbox-work-'));
process.env.MUSE_WORK_DIR = workRoot;
process.env.MUSE_CMS_RETRY_BASE_MS = '10';
process.env.MUSE_CMS_RETRY_MAX_MS = '50';
process.env.MUSE_PROGRESS_KEEPALIVE_MS = '4000';

const { runCommand, commandLine } = require('../lib/command');
const config = require('../lib/config');
const jobRunner = require('../lib/jobRunner');

const RUN_E2E = process.env.MUSE_RUN_E2E_PROGRESS_TEST !== '0';

function tmpDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function cmsError(status) {
  return Object.assign(new Error(`CMS returned HTTP ${status}`), { status });
}

function scriptFixture(words = 150) {
  const sentence = 'The crossing gates came down in the whiteout, and the crew held the train steady through the storm. ';
  let text = '';
  while (text.split(/\s+/).filter(Boolean).length < words) text += sentence;
  return text.trim();
}

async function ffmpegAvailable() {
  return new Promise(resolve => {
    execFile('ffmpeg', ['-version'], error => resolve(!error));
  });
}

async function makeFixtureMp4(dir) {
  const out = path.join(dir, 'fixture-master.mp4');
  await new Promise((resolve, reject) => {
    execFile('ffmpeg', [
      '-v', 'error',
      '-f', 'lavfi', '-i', 'testsrc2=size=1920x1080:rate=10:duration=61',
      '-f', 'lavfi', '-i', 'sine=frequency=440:duration=61',
      '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
      '-b:v', '1500k', '-maxrate', '2000k', '-bufsize', '4000k',
      '-c:a', 'aac', '-shortest', '-y', out
    ], { timeout: 120000 }, error => (error ? reject(error) : resolve()));
  });
  const stat = fs.statSync(out);
  assert.ok(stat.size >= 1024 * 1024, `fixture must be >= 1 MB, got ${stat.size}`);
  return out;
}

function testJob(jobId) {
  return {
    jobId,
    title: 'Progress outbox E2E test title',
    targetDurationMinutes: 1,
    targetWordCount: 150,
    channel: { name: 'Test Channel', id: 'c_test' },
    requirements: { minimumWords: 60 },
    outputLocations: {}
  };
}

// A stub MUSE_RENDER_COMMAND that behaves like render-vidnets-linux.js
// from the daemon's point of view: Vidnets poll lines on stderr (one per
// pipeline phase: voiceover, shot plan, per-shot media, render), then one
// JSON object on stdout. Lines are spaced out so each one reaches the CMS
// as its own progress report instead of coalescing.
function writeStubRender(dir, { fixture, jobId, slowMs = 0 }) {
  const stub = path.join(dir, 'stub-render.js');
  const lines = [
    '[render-vidnets] Vidnets 10% — Synthesizing voiceover',
    '[render-vidnets] Vidnets 20% — Planning documentary shots',
    '[render-vidnets] Vidnets 35% — Fetching media for shot 1/3',
    '[render-vidnets] Vidnets 50% — Media ready for shot 2/3',
    '[render-vidnets] Vidnets 70% — Rendering scenes'
  ];
  fs.writeFileSync(stub, [
    "const fs = require('fs');",
    "const path = require('path');",
    'let input = "";',
    "process.stdin.setEncoding('utf8');",
    "process.stdin.on('data', chunk => { input += chunk; });",
    "process.stdin.on('end', () => {",
    `  const lines = ${JSON.stringify(lines)};`,
    '  let i = 0;',
    '  const step = () => {',
    "    process.stderr.write(lines[i] + '\\n');",
    '    i += 1;',
    "    if (i < lines.length) { setTimeout(step, 200); return; }",
    `    if (${slowMs} > 0) { setTimeout(finish, ${slowMs}); return; }`,
    '    finish();',
    '  };',
    '  const finish = () => {',
    `    console.log(JSON.stringify({ videoPath: ${JSON.stringify(fixture)}, thumbnailPath: '', meta: { engine: 'stub' } }));`,
    '  };',
    '  setTimeout(step, 100);',
    '});'
  ].join('\n'));
  return stub;
}

// A stub CMS client whose progress endpoint can be down for a while and
// then recover. While down it throws a transient 530; while up it
// records every delivered progress call in order.
function flappingClient({ down = true } = {}) {
  const state = { down, progressCalls: [], uploads: [], completes: [], fails: [] };
  return {
    state,
    setDown(value) { state.down = value; },
    async progress(jobId, stage, progress, detail, advancing, extra) {
      if (state.down) throw cmsError(530);
      state.progressCalls.push({ jobId, stage, progress, detail, advancing, replayed: extra?.replayed === true });
      return { ok: true };
    },
    async upload(jobId, kind, filePath) {
      state.uploads.push({ jobId, kind, filePath });
      return { artifact: { bytes: fs.statSync(filePath).size, sha256: 'testsha' } };
    },
    async complete(jobId, body) {
      state.completes.push({ jobId, body });
      return { ok: true };
    },
    async fail(jobId, error) {
      state.fails.push({ jobId, error });
      return { ok: true };
    }
  };
}

// ------------------------------------------------------------------ (a)

test('queued progress replays in order and the outbox empties on reconnect', async () => {
  const dir = tmpDir('cms-progress-outbox-unit-');
  const jobId = 'r_test_outbox_unit';
  jobRunner.enqueueProgressEvent(dir, jobId, { stage: 'script', progress: 10, detail: 'Generating narration' });
  jobRunner.enqueueProgressEvent(dir, jobId, { stage: 'script', progress: 100, detail: 'Narration ready (150 words)' });
  jobRunner.enqueueProgressEvent(dir, jobId, { stage: 'video', progress: 2, detail: 'Starting the 1080p render' });
  assert.deepEqual(
    jobRunner.readProgressOutbox(dir).map(event => `${event.stage}:${event.progress}`),
    ['script:10', 'script:100', 'video:2'],
    'the outbox keeps insertion order'
  );

  // The replay stops at the first failure and keeps the rest queued.
  const recorded = [];
  let failNext = true;
  const client = {
    async progress(jobIdArg, stage, progress, detail, advancing, extra) {
      if (failNext) { failNext = false; throw cmsError(530); }
      recorded.push({ stage, progress, detail, replayed: extra?.replayed === true });
      return { ok: true };
    }
  };
  const partial = await jobRunner.flushProgressOutbox(client, dir, jobId, { force: true });
  assert.equal(partial.flushed, 0, 'the first event failed so nothing flushed');
  assert.equal(partial.remaining, 3, 'all three events stay queued');
  assert.deepEqual(recorded, [], 'no event reached the CMS');
  assert.equal(jobRunner.readProgressOutbox(dir).length, 3, 'the outbox file still holds all three');

  // On reconnect the whole queue drains in order, oldest first.
  const drained = await jobRunner.flushProgressOutbox(client, dir, jobId, { force: true });
  assert.equal(drained.flushed, 3);
  assert.equal(drained.remaining, 0);
  assert.deepEqual(
    recorded.map(entry => `${entry.stage}:${entry.progress}`),
    ['script:10', 'script:100', 'video:2'],
    'replayed in the original order'
  );
  assert.ok(recorded.every(entry => entry.replayed), 'replayed events are marked');
  assert.deepEqual(jobRunner.readProgressOutbox(dir), [], 'the outbox file is empty after the drain');
  assert.ok(!fs.existsSync(path.join(dir, 'progress-outbox.json')) || jobRunner.readProgressOutbox(dir).length === 0);
});

test('a terminal error stops the replay without dropping the queue', async () => {
  const dir = tmpDir('cms-progress-outbox-terminal-');
  const jobId = 'r_test_outbox_terminal';
  jobRunner.enqueueProgressEvent(dir, jobId, { stage: 'script', progress: 100, detail: 'Narration ready' });
  jobRunner.enqueueProgressEvent(dir, jobId, { stage: 'video', progress: 2, detail: 'Starting the 1080p render' });
  const client = {
    async progress() { throw cmsError(409); }
  };
  const result = await jobRunner.flushProgressOutbox(client, dir, jobId, { force: true });
  assert.equal(result.terminal, 409, 'a settled job stops the replay');
  assert.equal(result.flushed, 0);
  assert.equal(jobRunner.readProgressOutbox(dir).length, 2, 'terminal errors never drop queued events');
});

test('e2e: a whole run during a CMS outage queues every stage and replays it in order on reconnect', { skip: !RUN_E2E }, async t => {
  if (!(await ffmpegAvailable())) {
    t.skip('FFmpeg is not available');
    return;
  }
  const dir = tmpDir('cms-progress-outbox-e2e-');
  const fixture = await makeFixtureMp4(dir);
  const jobId = 'r_test_outbox_e2e';
  const job = testJob(jobId);
  const jobDir = path.join(workRoot, jobId);
  fs.mkdirSync(jobDir, { recursive: true });
  fs.writeFileSync(path.join(jobDir, 'script.txt'), scriptFixture(150) + '\n', 'utf8');
  const stub = writeStubRender(dir, { fixture, jobId });
  const previous = process.env.MUSE_RENDER_COMMAND;
  process.env.MUSE_RENDER_COMMAND = commandLine(process.execPath, [stub]);
  try {
    // The CMS is down for the entire run: every progress call fails,
    // but upload/complete (the delivery itself) succeed.
    const client = flappingClient({ down: true });
    const result = await jobRunner.executeJob({
      client, job, attempt: 1, signal: undefined, onBusy: () => {}
    });
    assert.equal(result.ok, true, 'the job still delivers while the CMS progress endpoint is down');
    assert.equal(client.state.completes.length, 1, 'completion reached the CMS');
    assert.equal(client.state.fails.length, 0);
    assert.deepEqual(client.state.progressCalls, [], 'no progress call landed while the CMS was down');

    // Every stage the run went through is in the outbox, in order —
    // nothing was silently dropped.
    const queued = jobRunner.readProgressOutbox(jobDir);
    assert.ok(queued.length >= 8, `expected the full stage run queued, got ${queued.length}: ${queued.map(e => `${e.stage}:${e.progress}`).join(', ')}`);
    const milestones = [
      ['script', 100, 'Narration ready'],
      ['video', 2, 'Starting the 1080p render'],
      ['video', 96, 'Checking duration'],
      ['video', 100, 'Master validated locally'],
      ['thumbnail', 100, 'Thumbnail skipped'],
      ['video', 99, 'Uploading the master'],
      ['video', 100, 'Delivered to the CMS']
    ];
    let searchFrom = 0;
    for (const [stage, progress, detailStart] of milestones) {
      const at = queued.findIndex((event, index) => index >= searchFrom
        && event.stage === stage && event.progress === progress && event.detail.startsWith(detailStart));
      assert.ok(at >= 0, `outbox holds ${stage} ${progress}% "${detailStart}" (queued: ${queued.map(e => `${e.stage}:${e.progress}:${e.detail.slice(0, 30)}`).join(' | ')})`);
      searchFrom = at + 1;
    }
    // The per-shot media lines rode along as video-stage progress too.
    // Poll lines arrive 200ms apart while a failed report takes ~300ms
    // (retries + durable writes), so the coalescer keeps the latest and
    // intermediate poll values may merge — but at least one per-shot
    // media line always survives, and the stage transitions themselves
    // (direct awaited report() calls, never coalesced) all survive.
    assert.ok(queued.some(event => event.detail.includes('Synthesizing voiceover')), 'voiceover progress was queued');
    assert.ok(queued.some(event => event.detail.includes('media for shot') || event.detail.includes('Media ready for shot')), 'per-shot media progress was queued');

    // The CMS comes back: the whole queue replays in the original order.
    client.setDown(false);
    // The run is over, so no keepalive/report will drain it — the daemon
    // heartbeat drain uses force (the CMS was marked down during the
    // outage), and this is that same call.
    const drained = await jobRunner.flushProgressOutbox(client, jobDir, jobId, { attempt: 1, force: true });
    assert.equal(drained.flushed, queued.length, 'the whole backlog drained');
    assert.equal(drained.remaining, 0);
    assert.deepEqual(
      client.state.progressCalls.map(call => `${call.stage}:${call.progress}:${call.detail}`),
      queued.map(event => `${event.stage}:${event.progress}:${event.detail}`),
      'replayed in exactly the queued order'
    );
    assert.ok(client.state.progressCalls.every(call => call.replayed), 'replayed events are marked');
    assert.deepEqual(jobRunner.readProgressOutbox(jobDir), [], 'the outbox file is empty after the drain');
    assert.equal(config.readActiveJob(), null, 'active marker cleared after delivery');
  } finally {
    if (previous === undefined) delete process.env.MUSE_RENDER_COMMAND;
    else process.env.MUSE_RENDER_COMMAND = previous;
  }
});

test('resume proceeds locally after 2 failed probes and queues the probe event', async () => {
  const jobId = 'r_test_outbox_probe';
  const jobDir = path.join(workRoot, jobId);
  fs.mkdirSync(jobDir, { recursive: true });
  fs.writeFileSync(
    path.join(jobDir, 'job.json'),
    JSON.stringify({ digest: 'x', job: { jobId, title: 'Probe outbox test' } })
  );
  jobRunner.writeProgressCheckpoint(jobDir, jobId, { stage: 'video', progress: 45, detail: 'Finding media' });
  config.writeActiveJob({
    jobId, payload: { jobId, title: 'Probe outbox test' }, attempt: 1, digest: 'x', startedAt: new Date().toISOString()
  });
  const client = {
    async progress() { throw cmsError(530); }
  };
  const executeJob = async () => ({ ok: true, stubbed: true });
  const result = await jobRunner.resumeActiveJob({ client, maxAttempts: 3, executeJob });
  assert.equal(result.stubbed, true, 'the job still runs locally after 2 failed probes');
  const queued = jobRunner.readProgressOutbox(jobDir);
  assert.equal(queued.length, 1, 'the lost probe is queued, not dropped');
  assert.equal(queued[0].stage, 'video');
  assert.equal(queued[0].progress, 45);
  assert.match(queued[0].detail, /resuming saved work/);
  assert.match(queued[0].detail, /Finding media/);
  config.clearActiveJob();
});

// ------------------------------------------------------------------ (b)

test('e2e: a failed keepalive never aborts a running job', { skip: !RUN_E2E }, async t => {
  if (!(await ffmpegAvailable())) {
    t.skip('FFmpeg is not available');
    return;
  }
  const dir = tmpDir('cms-progress-outbox-keepalive-');
  const fixture = await makeFixtureMp4(dir);
  const jobId = 'r_test_outbox_keepalive';
  const job = testJob(jobId);
  const jobDir = path.join(workRoot, jobId);
  fs.mkdirSync(jobDir, { recursive: true });
  fs.writeFileSync(path.join(jobDir, 'script.txt'), scriptFixture(150) + '\n', 'utf8');
  // Slow render (5s) so at least one 4s keepalive tick fires mid-render
  // while the CMS is down — it must fail quietly, not kill the job.
  const stub = writeStubRender(dir, { fixture, jobId, slowMs: 5000 });
  const previous = process.env.MUSE_RENDER_COMMAND;
  process.env.MUSE_RENDER_COMMAND = commandLine(process.execPath, [stub]);
  try {
    const client = flappingClient({ down: true });
    const startedAt = Date.now();
    const result = await jobRunner.executeJob({
      client, job, attempt: 1, signal: undefined, onBusy: () => {}
    });
    assert.ok(Date.now() - startedAt >= 5000, 'the render ran long enough for a keepalive tick');
    assert.equal(result.ok, true, 'the job delivered despite every progress/keepalive call failing');
    assert.equal(client.state.completes.length, 1);
    assert.equal(client.state.fails.length, 0);
    assert.ok(jobRunner.readProgressOutbox(jobDir).length > 0, 'failed reports were queued, not lost');
  } finally {
    if (previous === undefined) delete process.env.MUSE_RENDER_COMMAND;
    else process.env.MUSE_RENDER_COMMAND = previous;
  }
});

test('daemon: a failed heartbeat at startup never kills the daemon', { timeout: 60000 }, async t => {
  // Spawn the real daemon against a stub CMS that 530s the heartbeat
  // and reports no job, with a stub inference command that answers the
  // inbox probe. `start --once` must exit 0: a dead CMS is a warning,
  // never a fatal startup error (only a 401 revokes the credential).
  const dir = tmpDir('cms-progress-outbox-daemon-');
  const configFile = path.join(dir, 'muse-agent.json');
  const bridgeRoot = path.join(dir, 'bridge');
  const answerStub = path.join(dir, 'answer-stub.js');
  fs.writeFileSync(answerStub, [
    "let data = '';",
    "process.stdin.setEncoding('utf8');",
    "process.stdin.on('data', chunk => { data += chunk; });",
    "process.stdin.on('end', () => {",
    "  const input = JSON.parse(data || '{}');",
    "  const content = input.request?.messages?.[0]?.content || '';",
    "  const match = content.match(/Reply with this exact text and nothing else: (\\S+)/);",
    "  process.stdout.write(match ? match[1] : 'no-token-found');",
    '});',
    ''
  ].join('\n'));

  const server = http.createServer((request, response) => {
    if (request.url.startsWith('/api/muse/agent/heartbeat')) {
      response.writeHead(530, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ error: 'CMS tunnel down (test stub)' }));
      return;
    }
    if (request.url.startsWith('/api/muse/agent/jobs/next')) {
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ job: null, reason: 'idle' }));
      return;
    }
    response.writeHead(404, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ error: 'not found' }));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const env = {
      ...process.env,
      MUSE_WORK_DIR: path.join(dir, 'work'),
      MUSE_CONFIG_FILE: configFile,
      MUSE_TOKEN: 'muse_test_token_for_heartbeat',
      MUSE_CMS_URL: `http://127.0.0.1:${server.address().port}`,
      MUSE_INFERENCE_COMMAND: commandLine(process.execPath, [answerStub]),
      VIDZEO_BRIDGE_ROOT: bridgeRoot,
      MUSE_HEARTBEAT_MS: '20000',
      MUSE_POLL_MS: '1000'
    };
    const child = spawn(process.execPath, [path.join(__dirname, '..', 'muse-agent.js'), 'start', '--once'], {
      env, stdio: ['ignore', 'pipe', 'pipe']
    });
    let output = '';
    child.stdout.on('data', chunk => { output += chunk; });
    child.stderr.on('data', chunk => { output += chunk; });
    const exitCode = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => { child.kill('SIGKILL'); reject(new Error('daemon did not exit within 60s; output: ' + output)); }, 60000);
      child.on('error', reject);
      child.on('close', code => { clearTimeout(timer); resolve(code); });
    });
    assert.equal(exitCode, 0, `daemon must exit 0 despite the 530 heartbeat (output: ${output})`);
    assert.match(output, /Heartbeat failed/, 'the failed heartbeat is logged as a warning, not a crash');
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
});

// ------------------------------------------------------------------ (c)

test('e2e: every stage of a job emits at least one progress report', { skip: !RUN_E2E }, async t => {
  if (!(await ffmpegAvailable())) {
    t.skip('FFmpeg is not available');
    return;
  }
  const dir = tmpDir('cms-progress-outbox-stages-');
  const fixture = await makeFixtureMp4(dir);
  const jobId = 'r_test_outbox_stages';
  const job = testJob(jobId);
  const jobDir = path.join(workRoot, jobId);
  fs.mkdirSync(jobDir, { recursive: true });
  fs.writeFileSync(path.join(jobDir, 'script.txt'), scriptFixture(150) + '\n', 'utf8');
  const stub = writeStubRender(dir, { fixture, jobId });
  const previous = process.env.MUSE_RENDER_COMMAND;
  process.env.MUSE_RENDER_COMMAND = commandLine(process.execPath, [stub]);
  try {
    const client = flappingClient({ down: false });
    const result = await jobRunner.executeJob({
      client, job, attempt: 1, signal: undefined, onBusy: () => {}
    });
    assert.equal(result.ok, true, 'local test job delivered');
    assert.equal(client.state.completes.length, 1, 'job completed');
    assert.equal(client.state.fails.length, 0);
    const calls = client.state.progressCalls;
    const seen = (stage, progress, detailStart) => calls.some(call =>
      call.stage === stage && call.progress === progress && call.detail.startsWith(detailStart));
    // Script stage.
    assert.ok(seen('script', 100, 'Narration ready'), 'script stage reports completion');
    // Render stage, including the adapter phases that surface as
    // video-stage detail lines (voiceover, shot plan, per-shot media).
    assert.ok(seen('video', 2, 'Starting the 1080p render'), 'render stage reports its start');
    assert.ok(calls.some(call => call.stage === 'video' && call.detail.includes('Synthesizing voiceover')), 'voiceover phase reports');
    assert.ok(calls.some(call => call.stage === 'video' && call.detail.includes('Planning documentary shots')), 'shot-plan phase reports');
    assert.ok(calls.some(call => call.stage === 'video' && call.detail.includes('Fetching media for shot 1/3')), 'per-shot media phase reports');
    assert.ok(calls.some(call => call.stage === 'video' && call.detail.includes('Media ready for shot 2/3')), 'per-shot media completion reports');
    assert.ok(calls.some(call => call.stage === 'video' && call.detail.includes('Rendering scenes')), 'render phase reports');
    assert.ok(seen('video', 96, 'Checking duration'), 'validation reports');
    assert.ok(seen('video', 100, 'Master validated locally'), 'validation completion reports');
    // Thumbnail stage (skipped by this worker).
    assert.ok(seen('thumbnail', 100, 'Thumbnail skipped'), 'thumbnail stage reports even when skipped');
    // Delivery stage.
    assert.ok(seen('video', 99, 'Uploading the master'), 'upload reports');
    assert.ok(seen('video', 100, 'Delivered to the CMS'), 'delivery completion reports');
    // The live checkpoint ends at the delivery, not at the upload.
    const saved = jobRunner.readProgressCheckpoint(jobDir);
    assert.equal(saved.stage, 'video');
    assert.equal(saved.progress, 100);
    assert.equal(saved.detail, 'Delivered to the CMS');
    assert.deepEqual(jobRunner.readProgressOutbox(jobDir), [], 'nothing queued when the CMS is up');
    assert.equal(config.readActiveJob(), null, 'active marker cleared after delivery');
  } finally {
    if (previous === undefined) delete process.env.MUSE_RENDER_COMMAND;
    else process.env.MUSE_RENDER_COMMAND = previous;
  }
});
