'use strict';

// Daemon -> CMS live progress + checkpoints that survive a reboot.
//
// ITEM 1: the render wrapper's per-poll Vidnets stage/% lines used to be
// buffered in the render command's stderr until the render finished, so
// the CMS only ever saw the 90s keepalive re-sending the stale
// "Starting the render (2%)" line. These tests pin the fix: stderr is
// streamed line by line, the adapter parses wrapper lines into live
// onProgress calls, every progress call carries the recent daemon log
// lines, and the heartbeat carries the live stage too.
//
// ITEM 2: checkpoints are atomic (temp + fsync + rename) and rewritten on
// every report(), so a daemon killed mid-render leaves an accurate
// pointer; resumeActiveJob probes the CMS with the saved stage/% instead
// of the hardcoded "script 5%", and a killed-then-restarted local job
// resumes mid-job (script reused, render re-attached) instead of
// restarting from scratch.
//
// The end-to-end tests (executeJob + kill/resume) are gated behind
// MUSE_RUN_E2E_PROGRESS_TEST=1: they generate a real 61s 1080p fixture
// with ffmpeg (~10s) and run a full local job against a stub CMS.
// Run: MUSE_RUN_E2E_PROGRESS_TEST=1 node --test tests/daemon-progress-checkpoints.test.js

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { spawn, execFile } = require('node:child_process');
const { test } = require('node:test');

// Scratch work root BEFORE requiring the worker modules: lib/config.js
// reads MUSE_WORK_DIR at load time.
const workRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'daemon-progress-work-'));
process.env.MUSE_WORK_DIR = workRoot;
process.env.MUSE_CMS_RETRY_BASE_MS = '10';
process.env.MUSE_CMS_RETRY_MAX_MS = '50';

const { runCommand, commandLine } = require('../lib/command');
const log = require('../lib/log');
const config = require('../lib/config');
const { createClient } = require('../lib/cms');
const { parseRenderProgressLine, render: renderVideo } = require('../adapters/video');
const jobRunner = require('../lib/jobRunner');
const { writeVidnetsProgressFile } = require('../render-vidnets-linux');

const RUN_E2E = process.env.MUSE_RUN_E2E_PROGRESS_TEST === '1';

function tmpDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function noTempFilesLeft(dir) {
  return fs.readdirSync(dir).filter(name => name.endsWith('.tmp')).length === 0;
}

// ---------------------------------------------------------------- ITEM 1

test('runCommand streams stderr lines to onStderrLine while the command runs', async () => {
  const dir = tmpDir('daemon-progress-stream-');
  const script = path.join(dir, 'emit-lines.js');
  fs.writeFileSync(script, [
    'let i = 0;',
    'const timer = setInterval(() => {',
    '  i += 1;',
    "  process.stderr.write(`line ${i}\\n`);",
    "  if (i >= 3) { clearInterval(timer); console.log(JSON.stringify({ ok: true })); }",
    '}, 50);',
    ''
  ].join('\n'));
  const seen = [];
  const startedAt = Date.now();
  const result = await runCommand(commandLine(process.execPath, [script]), {
    label: 'stderr stream test',
    onStderrLine: line => seen.push({ line, atMs: Date.now() - startedAt })
  });
  assert.equal(JSON.parse(result.stdout).ok, true);
  assert.deepEqual(seen.map(entry => entry.line), ['line 1', 'line 2', 'line 3']);
  // The lines arrived while the command was still running, not all at exit.
  // (Threshold includes node spawn time on a loaded VM — the spread check
  // below is the real streaming proof; this one only bounds gross delay.)
  assert.ok(seen[0].atMs < 1500, `first line arrived at ${seen[0].atMs}ms`);
  assert.ok(seen[2].atMs - seen[0].atMs >= 80, 'lines were spread over time, not dumped at exit');
  assert.match(result.stderr, /line 3/);
});

test('runCommand flushes a final stderr line without a trailing newline', async () => {
  const dir = tmpDir('daemon-progress-tail-');
  const script = path.join(dir, 'emit-tail.js');
  fs.writeFileSync(script, "process.stderr.write('tail-no-newline');\n");
  const seen = [];
  await runCommand(commandLine(process.execPath, [script]), {
    label: 'stderr tail test',
    onStderrLine: line => seen.push(line)
  });
  assert.deepEqual(seen, ['tail-no-newline']);
});

test('parseRenderProgressLine parses wrapper poll lines and ignores other output', () => {
  assert.deepEqual(
    parseRenderProgressLine('[render-vidnets] Vidnets 45% — Finding media'),
    { progress: 45, detail: 'Finding media' }
  );
  assert.deepEqual(
    parseRenderProgressLine('[render-vidnets] Vidnets 0% - Rendering'),
    { progress: 0, detail: 'Rendering' }
  );
  assert.deepEqual(
    parseRenderProgressLine('[render-vidnets] Vidnets 100% — Rendering'),
    { progress: 100, detail: 'Rendering' }
  );
  assert.deepEqual(
    parseRenderProgressLine('[render-vidnets] Inbox: 3 request(s) still pending for this job (hook worker answers these)'),
    { progress: null, detail: 'Inbox: 3 request(s) still pending for this job (hook worker answers these)' }
  );
  assert.deepEqual(
    parseRenderProgressLine('[render-vidnets] Re-attaching to existing Vidnets job f04c6b9f for r_test'),
    { progress: null, detail: 'Re-attaching to existing Vidnets job f04c6b9f for r_test' }
  );
  assert.equal(parseRenderProgressLine('[render-vidnets] Vidnets 120% — bogus').progress, 100, 'over-100 clamps rather than rejects');
  assert.equal(parseRenderProgressLine('some other stderr line'), null);
  assert.equal(parseRenderProgressLine(''), null);
  assert.equal(parseRenderProgressLine('[remotion] bundling...'), null);
});

test('log.recentLines returns the last lines, newest last, capped', () => {
  log.info('progress-ring-marker-a');
  log.warn('progress-ring-marker-b');
  log.info('progress-ring-marker-c');
  const lines = log.recentLines(3);
  assert.equal(lines.length, 3);
  assert.match(lines[0], /progress-ring-marker-a/);
  assert.match(lines[1], /progress-ring-marker-b/);
  assert.match(lines[2], /progress-ring-marker-c/);
  assert.ok(log.recentLines(1).length <= 1);
  assert.ok(log.recentLines(500).length <= log.RING_SIZE);
});

test('cms progress call carries the recent log lines in a logs field', async () => {
  const bodies = [];
  const server = http.createServer((req, res) => {
    let data = '';
    req.on('data', chunk => { data += chunk; });
    req.on('end', () => {
      bodies.push({ url: req.url, body: JSON.parse(data || '{}') });
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const client = createClient({ baseUrl: `http://127.0.0.1:${server.address().port}`, token: 'muse_test_token_for_progress_logs' });
    log.info('cms-logs-marker-line');
    await client.progress('r_test_logs', 'video', 45, 'Finding media', true, { logs: log.recentLines(10) });
    assert.equal(bodies.length, 1);
    assert.equal(bodies[0].url, '/api/muse/agent/jobs/r_test_logs/progress');
    assert.equal(bodies[0].body.stage, 'video');
    assert.equal(bodies[0].body.progress, 45);
    assert.equal(bodies[0].body.detail, 'Finding media');
    assert.equal(bodies[0].body.advancing, true);
    assert.ok(Array.isArray(bodies[0].body.logs), 'logs field is present');
    assert.ok(bodies[0].body.logs.some(line => line.includes('cms-logs-marker-line')), 'logs carry the recent daemon line');
    // Without logs the field is absent entirely (old CMS behaviour).
    await client.progress('r_test_logs', 'video', 46, 'Still finding media');
    assert.equal(bodies[1].body.logs, undefined);
  } finally {
    await new Promise(resolve => server.close(resolve));
  }
});

// The adapter test below is the real streaming test: it sets the env var
// and runs the same code path the daemon uses.
test('video adapter streams wrapper stderr into live onProgress calls (via MUSE_RENDER_COMMAND)', async () => {
  const dir = tmpDir('daemon-progress-adapter-');
  const fakeVideo = path.join(dir, 'stub-master.mp4');
  fs.writeFileSync(fakeVideo, Buffer.alloc(2 * 1024 * 1024, 0));
  const stub = path.join(dir, 'stub-render.js');
  fs.writeFileSync(stub, [
    "process.stdin.resume(); process.stdin.on('end', () => {",
    "  const lines = [",
    "    '[render-vidnets] Vidnets 12% — Finding media',",
    "    '[render-vidnets] Vidnets 45% — Finding media',",
    "    '[render-vidnets] Inbox: 2 request(s) still pending for this job (hook worker answers these)',",
    "    '[render-vidnets] Vidnets 78% — Rendering scenes',",
    "  ];",
    "  let i = 0;",
    "  const timer = setInterval(() => {",
    "    process.stderr.write(lines[i] + '\\n');",
    "    i += 1;",
    "    if (i >= lines.length) {",
    "      clearInterval(timer);",
    "      console.log(JSON.stringify({ videoPath: process.argv[2], thumbnailPath: '', meta: { engine: 'stub' } }));",
    "    }",
    "  }, 60);",
    "});"
  ].join('\n'));
  const previous = process.env.MUSE_RENDER_COMMAND;
  process.env.MUSE_RENDER_COMMAND = commandLine(process.execPath, [stub, fakeVideo]);
  try {
    const job = { jobId: 'r_test_adapter', title: 'Adapter streaming test', targetDurationMinutes: 1 };
    const seen = [];
    const startedAt = Date.now();
    const result = await renderVideo(job, 'word '.repeat(150).trim(), dir, {
      onProgress: (progress, detail) => seen.push({ progress, detail, atMs: Date.now() - startedAt })
    });
    assert.ok(result.videoPath.endsWith('video.mp4'));
    assert.ok(fs.existsSync(path.join(dir, 'video.mp4')));
    const streamed = seen.filter(entry => entry.progress !== 100);
    assert.deepEqual(streamed.map(entry => entry.progress), [12, 45, 45, 78],
      'wrapper poll lines arrive live; a non-progress wrapper line keeps the last %');
    assert.equal(streamed[0].detail, 'Finding media');
    assert.match(streamed[2].detail, /Inbox: 2 request/);
    assert.equal(streamed[3].detail, 'Rendering scenes');
    // Live, not dumped at exit: the first poll arrived well before the end.
    assert.ok(streamed[0].atMs < streamed[streamed.length - 1].atMs, 'progress arrived spread over the run');
    assert.ok(seen.some(entry => entry.progress === 100 && entry.detail === 'Render command finished'));
  } finally {
    if (previous === undefined) delete process.env.MUSE_RENDER_COMMAND;
    else process.env.MUSE_RENDER_COMMAND = previous;
  }
});

// ---------------------------------------------------------------- ITEM 2

test('writeJsonAtomic leaves a valid file and no temp files behind', () => {
  const dir = tmpDir('daemon-progress-atomic-');
  const file = path.join(dir, 'sub', 'job.json');
  config.writeJsonAtomic(file, { jobId: 'r_test_atomic', digest: 'abc' });
  assert.deepEqual(JSON.parse(fs.readFileSync(file, 'utf8')), { jobId: 'r_test_atomic', digest: 'abc' });
  assert.ok(noTempFilesLeft(dir));
  assert.ok(noTempFilesLeft(path.join(dir, 'sub')));
  config.writeJsonAtomic(file, { jobId: 'r_test_atomic', digest: 'def' });
  assert.equal(JSON.parse(fs.readFileSync(file, 'utf8')).digest, 'def');
  assert.ok(noTempFilesLeft(path.join(dir, 'sub')));
});

test('progress checkpoint round-trips and rejects garbage', () => {
  const dir = tmpDir('daemon-progress-checkpoint-');
  assert.equal(jobRunner.readProgressCheckpoint(dir), null);
  jobRunner.writeProgressCheckpoint(dir, 'r_test_cp', { stage: 'video', progress: 45, detail: 'Finding media' });
  assert.deepEqual(jobRunner.readProgressCheckpoint(dir), {
    stage: 'video',
    progress: 45,
    detail: 'Finding media',
    updatedAt: jobRunner.readProgressCheckpoint(dir).updatedAt
  });
  assert.ok(noTempFilesLeft(dir));
  fs.writeFileSync(path.join(dir, 'progress.json'), '{not json', 'utf8');
  assert.equal(jobRunner.readProgressCheckpoint(dir), null);
  fs.writeFileSync(path.join(dir, 'progress.json'), JSON.stringify({ stage: 'video', progress: 'nope' }), 'utf8');
  assert.equal(jobRunner.readProgressCheckpoint(dir), null);
});

test('resumeActiveJob probes the CMS with the saved stage/% instead of script 5%', async () => {
  const jobId = 'r_test_resume_probe';
  const jobDir = path.join(workRoot, jobId);
  fs.mkdirSync(jobDir, { recursive: true });
  // No job.json on purpose: after the probe, resume returns null (no
  // checkpoint) instead of running executeJob — the probe itself is what
  // this test asserts.
  jobRunner.writeProgressCheckpoint(jobDir, jobId, { stage: 'video', progress: 45, detail: 'Finding media' });
  const payload = { jobId, title: 'Resume probe test', targetDurationMinutes: 1 };
  config.writeActiveJob({ jobId, payload, attempt: 7, digest: 'x', startedAt: new Date().toISOString() });
  const calls = [];
  const client = {
    async progress(jobIdArg, stage, progress, detail, advancing, extra) {
      calls.push({ jobIdArg, stage, progress, detail, advancing, extra });
      return { ok: true };
    }
  };
  const result = await jobRunner.resumeActiveJob({ client, maxAttempts: 1 });
  assert.equal(result, null, 'no job.json means nothing to resume');
  assert.equal(calls.length, 1, 'exactly one probe');
  assert.equal(calls[0].stage, 'video');
  assert.equal(calls[0].progress, 45);
  assert.match(calls[0].detail, /resuming saved work/);
  assert.match(calls[0].detail, /Finding media/);
  assert.ok(Array.isArray(calls[0].extra.logs), 'probe carries recent log lines');
  assert.equal(config.readActiveJob(), null, 'marker cleared when the saved job has no checkpoint');
});

test('wrapper progress file is written atomically and parses', () => {
  const dir = tmpDir('daemon-progress-wrapper-');
  writeVidnetsProgressFile(dir, {
    vidnetsJobId: 'f04c6b9f', cmsJobId: 'r_test_wrapper',
    status: 'processing', progress: 45, detail: 'Finding media'
  });
  const saved = JSON.parse(fs.readFileSync(path.join(dir, 'vidnets-progress.json'), 'utf8'));
  assert.equal(saved.vidnetsJobId, 'f04c6b9f');
  assert.equal(saved.cmsJobId, 'r_test_wrapper');
  assert.equal(saved.status, 'processing');
  assert.equal(saved.progress, 45);
  assert.equal(saved.detail, 'Finding media');
  assert.ok(saved.updatedAt);
  assert.ok(noTempFilesLeft(dir));
});

test('vidnets state.json is written atomically (exact server code, extracted)', async () => {
  // The Vidnets server is too heavy to import in a unit test, so this
  // extracts _write_generation_checkpoint + its _checkpoint_safe helper
  // from server/app.py verbatim and runs them against a scratch package.
  const python = await new Promise(resolve => {
    execFile('python3', ['--version'], error => resolve(!error));
  });
  if (!python) {
    test.skip('python3 is not available');
    return;
  }
  const appSource = fs.readFileSync(path.join(__dirname, '..', 'vidnets', 'server', 'app.py'), 'utf8');
  const start = appSource.indexOf('def _write_generation_checkpoint(');
  const end = appSource.indexOf('\n\ndef ', start);
  assert.ok(start !== -1 && end !== -1, 'could not locate _write_generation_checkpoint in server/app.py');
  const fnSource = appSource.slice(start, end);
  assert.ok(fnSource.includes('os.fsync'), 'checkpoint write must fsync');
  assert.ok(fnSource.includes('os.replace(state_temp, state_target)'), 'state.json must be atomically replaced');
  const safeStart = appSource.indexOf('def _checkpoint_safe(');
  const safeEnd = appSource.indexOf('\n\ndef ', safeStart);
  const safeSource = appSource.slice(safeStart, safeEnd);
  const dir = tmpDir('daemon-progress-py-');
  const driver = path.join(dir, 'check_state.py');
  fs.writeFileSync(driver, [
    'import json, os, secrets, sys, time',
    'from pathlib import Path',
    'sys.path.insert(0, ' + JSON.stringify(path.join(__dirname, '..', 'vidnets', 'server')) + ')',
    safeSource,
    fnSource,
    'package = ' + JSON.stringify(path.join(dir, 'pkg')) + ';',
    "os.makedirs(os.path.join(package, 'checkpoints'), exist_ok=True)",
    "config = {'_output_package_dir': package}",
    "_write_generation_checkpoint(config, 'source_query_plan.json', {'queries': ['a']}, stage='source_queries_planned', status='saved')",
    "state = json.load(open(os.path.join(package, 'checkpoints', 'state.json')))",
    "assert state['stage'] == 'source_queries_planned', state",
    "assert state['status'] == 'saved', state",
    "left = [n for n in os.listdir(os.path.join(package, 'checkpoints')) if n.endswith('.part')]",
    'assert not left, left',
    "print('STATE_JSON_ATOMIC_OK')",
    ''
  ].join('\n'));
  const output = await new Promise((resolve, reject) => {
    execFile('python3', [driver], { timeout: 60000 }, (error, stdout, stderr) => {
      if (error) reject(new Error(`state.json atomic check failed: ${stderr || error.message}`));
      else resolve(stdout);
    });
  });
  assert.match(output, /STATE_JSON_ATOMIC_OK/);
});

// ------------------------------------------------- end-to-end (gated)

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

function recordingClient() {
  const calls = { progress: [], uploads: [], completes: [], fails: [] };
  return {
    calls,
    async progress(jobId, stage, progress, detail, advancing, extra) {
      calls.progress.push({ jobId, stage, progress, detail, advancing, logs: extra?.logs || null });
      return { ok: true };
    },
    async upload(jobId, kind, filePath) {
      calls.uploads.push({ jobId, kind, filePath });
      return { artifact: { bytes: fs.statSync(filePath).size, sha256: 'testsha' } };
    },
    async complete(jobId, body) {
      calls.completes.push({ jobId, body });
      return { ok: true };
    },
    async fail(jobId, error) {
      calls.fails.push({ jobId, error });
      return { ok: true };
    }
  };
}

function testJob(jobId) {
  return {
    jobId,
    title: 'Progress E2E test title',
    targetDurationMinutes: 1,
    targetWordCount: 150,
    channel: { name: 'Test Channel', id: 'c_test' },
    requirements: { minimumWords: 60 },
    outputLocations: {}
  };
}

function writeStubRender(dir, { fixture, jobId, slowMs = 0, reattachId = '' }) {
  // A stub MUSE_RENDER_COMMAND that behaves like render-vidnets-linux.js
  // from the daemon's point of view: Vidnets poll lines on stderr, then
  // one JSON object on stdout. The slow variant writes a fake
  // vidnets-job.json first (as the real wrapper does on submit) so the
  // resume run can prove it re-attached instead of submitting fresh.
  const stub = path.join(dir, 'stub-render.js');
  const lines = [
    '[render-vidnets] Vidnets 12% — Finding media',
    '[render-vidnets] Vidnets 45% — Finding media',
    '[render-vidnets] Vidnets 78% — Rendering scenes'
  ];
  if (reattachId) lines.unshift(`[render-vidnets] Re-attaching to existing Vidnets job ${reattachId} for ${jobId}`);
  fs.writeFileSync(stub, [
    "const fs = require('fs');",
    "const path = require('path');",
    'let input = "";',
    "process.stdin.setEncoding('utf8');",
    "process.stdin.on('data', chunk => { input += chunk; });",
    "process.stdin.on('end', () => {",
    `  const request = JSON.parse(input || '{}');`,
    `  if (${slowMs} > 0) {`,
    "    fs.writeFileSync(path.join(request.workDir, 'vidnets-job.json'), JSON.stringify({ id: 'stub-vidnets-1', jobId: request.job.jobId }) + '\\n');",
    "    process.stderr.write('[render-vidnets] Vidnets job stub-vidnets-1 submitted for test\\n');",
    '  }',
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

test('e2e: executeJob reports live Vidnets progress with log lines to the CMS', { skip: !RUN_E2E }, async () => {
  if (!(await ffmpegAvailable())) {
    test.skip('ffmpeg is not available');
    return;
  }
  const dir = tmpDir('daemon-progress-e2e-');
  const fixture = await makeFixtureMp4(dir);
  const jobId = 'r_test_e2e';
  const job = testJob(jobId);
  const jobDir = path.join(workRoot, jobId);
  fs.mkdirSync(jobDir, { recursive: true });
  fs.writeFileSync(path.join(jobDir, 'script.txt'), scriptFixture(150) + '\n', 'utf8');
  const stub = writeStubRender(dir, { fixture, jobId });
  const previous = process.env.MUSE_RENDER_COMMAND;
  process.env.MUSE_RENDER_COMMAND = commandLine(process.execPath, [stub]);
  try {
    const client = recordingClient();
    const result = await jobRunner.executeJob({
      client, job, attempt: 1, signal: undefined,
      onBusy: () => {}
    });
    assert.equal(result.ok, true, 'local test job delivered');
    const videoProgress = client.calls.progress.filter(call => call.stage === 'video');
    const percents = videoProgress.map(call => call.progress);
    assert.ok(percents.includes(12), `CMS saw video 12% (got ${percents.join(',')})`);
    assert.ok(percents.includes(45), `CMS saw video 45% (got ${percents.join(',')})`);
    assert.ok(percents.includes(78), `CMS saw video 78% (got ${percents.join(',')})`);
    const at45 = videoProgress.find(call => call.progress === 45);
    assert.equal(at45.detail, 'Finding media');
    assert.ok(Array.isArray(at45.logs) && at45.logs.length > 0, 'progress call carried recent log lines');
    assert.ok(at45.logs.some(line => line.includes('Vidnets 45%')), 'log lines include the streamed wrapper line');
    assert.equal(client.calls.completes.length, 1, 'job completed');
    assert.equal(client.calls.fails.length, 0);
    const saved = jobRunner.readProgressCheckpoint(jobDir);
    assert.equal(saved.stage, 'video');
    assert.ok(saved.progress >= 99, `final checkpoint is the upload stage (got ${saved.progress})`);
    assert.ok(noTempFilesLeft(jobDir), 'no temp files left in the job dir');
    assert.equal(config.readActiveJob(), null, 'active marker cleared after delivery');
  } finally {
    if (previous === undefined) delete process.env.MUSE_RENDER_COMMAND;
    else process.env.MUSE_RENDER_COMMAND = previous;
  }
});

test('e2e: daemon killed mid-render resumes mid-job instead of restarting', { skip: !RUN_E2E }, async () => {
  if (!(await ffmpegAvailable())) {
    test.skip('ffmpeg is not available');
    return;
  }
  const dir = tmpDir('daemon-progress-kill-');
  const fixture = await makeFixtureMp4(dir);
  const jobId = 'r_test_kill_resume';
  const job = testJob(jobId);
  const jobDir = path.join(workRoot, jobId);
  fs.mkdirSync(jobDir, { recursive: true });
  fs.writeFileSync(path.join(jobDir, 'script.txt'), scriptFixture(150) + '\n', 'utf8');

  // The driver runs executeJob in its own process and logs every CMS
  // progress call to a file, so the parent can see what the killed run
  // reported before it died.
  const driver = path.join(dir, 'driver.js');
  const progressLog = path.join(dir, 'driver-progress.jsonl');
  const slowStub = writeStubRender(dir, { fixture, jobId, slowMs: 20000 });
  fs.writeFileSync(driver, [
    "const fs = require('fs');",
    "const jobRunner = require(" + JSON.stringify(path.join(__dirname, '..', 'lib', 'jobRunner.js')) + ");",
    `const job = ${JSON.stringify(job)};`,
    `const progressLog = ${JSON.stringify(progressLog)};`,
    'const client = {',
    "  async progress(jobId, stage, progress, detail, advancing, extra) {",
    "    fs.appendFileSync(progressLog, JSON.stringify({ stage, progress, detail, logs: (extra && extra.logs) || null }) + '\\n');",
    '    return { ok: true };',
    '  },',
    "  async upload(jobId, kind, filePath) { return { artifact: { bytes: 1, sha256: 'x' } }; },",
    "  async complete(jobId, body) { return { ok: true }; },",
    "  async fail(jobId, error) { return { ok: true }; }",
    '};',
    'jobRunner.executeJob({ client, job, attempt: 1, signal: undefined, onBusy: () => {} })',
    "  .then(result => { console.log('DRIVER_DONE ' + JSON.stringify(result)); process.exit(0); })",
    "  .catch(error => { console.error('DRIVER_ERROR ' + error.message); process.exit(1); });",
    ''
  ].join('\n'));
  const env = {
    ...process.env,
    MUSE_WORK_DIR: workRoot,
    MUSE_RENDER_COMMAND: commandLine(process.execPath, [slowStub]),
    MUSE_PROGRESS_KEEPALIVE_MS: '90000'
  };
  const child = spawn('node', [driver], { env, stdio: ['ignore', 'pipe', 'pipe'] });
  let childOut = '';
  child.stdout.on('data', chunk => { childOut += chunk; });
  child.stderr.on('data', chunk => { childOut += chunk; });
  // Wait until the killed run has reported live video progress, then
  // SIGKILL it — the VM-reboot case: no cleanup handlers run.
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, 200));
    try {
      const lines = fs.readFileSync(progressLog, 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line));
      if (lines.some(entry => entry.stage === 'video' && entry.progress === 45)) break;
    } catch { /* log file not written yet */ }
  }
  assert.ok(fs.existsSync(progressLog), 'killed run reported progress before dying');
  const killedLines = fs.readFileSync(progressLog, 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line));
  assert.ok(killedLines.some(entry => entry.stage === 'video' && entry.progress === 45),
    'killed run reached video 45% live (got ' + killedLines.map(entry => `${entry.stage}:${entry.progress}`).join(',') + ')');
  assert.ok(!killedLines.some(entry => entry.detail === 'Generating narration'),
    'script was pre-seeded, so the killed run never regenerated it');
  child.kill('SIGKILL');
  await new Promise(resolve => child.on('close', resolve));
  assert.ok(!childOut.includes('DRIVER_DONE'), 'driver was killed before finishing');

  // The checkpoints must have survived the kill.
  const saved = config.readActiveJob();
  assert.ok(saved && saved.jobId === jobId, 'active-job.json survived the kill');
  assert.ok(fs.existsSync(path.join(jobDir, 'vidnets-job.json')), 'vidnets-job.json survived the kill');
  const progress = jobRunner.readProgressCheckpoint(jobDir);
  // The kill lands just after 45% is observed, so 78% may or may not
  // have been reported first — either way the checkpoint is mid-render.
  assert.ok(progress && progress.stage === 'video' && progress.progress >= 45,
    `progress checkpoint is mid-render after the kill (got ${JSON.stringify(progress)})`);

  // Restart: resumeActiveJob with a fast stub that re-attaches to the
  // saved Vidnets job id instead of submitting fresh.
  const fastStub = writeStubRender(dir, { fixture, jobId, reattachId: 'stub-vidnets-1' });
  const previous = process.env.MUSE_RENDER_COMMAND;
  process.env.MUSE_RENDER_COMMAND = commandLine(process.execPath, [fastStub]);
  try {
    const client = recordingClient();
    const resumed = await jobRunner.resumeActiveJob({ client, maxAttempts: 1 });
    assert.ok(resumed && resumed.ok, `resume delivered the job (got ${JSON.stringify(resumed)})`);
    const probe = client.calls.progress[0];
    assert.equal(probe.stage, progress.stage);
    assert.equal(probe.progress, progress.progress);
    assert.match(probe.detail, /resuming saved work/);
    assert.match(probe.detail, /Finding media|Rendering scenes/);
    assert.ok(!client.calls.progress.some(call => call.detail === 'Generating narration'),
      'resume reused the saved script instead of regenerating it');
    assert.ok(client.calls.progress.some(call => call.detail.includes('Re-attaching to existing Vidnets job stub-vidnets-1')),
      'resume run re-attached to the saved Vidnets job and reported it to the CMS');
    assert.equal(client.calls.completes.length, 1, 'resumed job completed');
    assert.equal(client.calls.fails.length, 0);
    assert.equal(config.readActiveJob(), null, 'active marker cleared after the resumed delivery');
    assert.ok(noTempFilesLeft(jobDir), 'no temp files left in the job dir after resume');
  } finally {
    if (previous === undefined) delete process.env.MUSE_RENDER_COMMAND;
    else process.env.MUSE_RENDER_COMMAND = previous;
  }
});
