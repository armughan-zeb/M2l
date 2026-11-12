'use strict';

// Resume rule (Nov 10, 2026 — BNSF incident, job r_mw85awoskks8lh):
// the CMS re-dispatched a finished job with ONLY `createdAt` changed.
// The old raw-JSON digest treated that as a different job, archived the
// finished work, and the daemon re-rendered ~2.5h of video that already
// existed. These tests pin the fix: same job ID = same job. A re-claim
// first looks up the existing local work and delivers a finished master
// or resumes mid-run work; only a job with no local work starts fresh.
// A re-dispatch that genuinely changed the spec still starts fresh.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { test } = require('node:test');

// Point the worker state at a scratch dir BEFORE requiring jobRunner:
// lib/config.js reads MUSE_WORK_DIR at load time.
const workRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'resume-reuse-work-'));
process.env.MUSE_WORK_DIR = workRoot;
process.env.MUSE_CMS_RETRY_BASE_MS = '10';
process.env.MUSE_CMS_RETRY_MAX_MS = '50';

const config = require('../lib/config');
const jobRunner = require('../lib/jobRunner');
const ffmpeg = require('../lib/ffmpeg');
const { afterEach } = require('node:test');

// A failed executeJob leaves the active-job marker behind (only a settled
// job clears it) — exactly what production wants, but the shared scratch
// root would then make the NEXT test hit the serial guard instead of the
// path under test. Clear it between tests.
afterEach(() => {
  try { config.clearActiveJob(); } catch { /* already clear */ }
});

let jobCounter = 0;

function makeJob(overrides = {}) {
  jobCounter += 1;
  return {
    jobId: `r_reuse${jobCounter}`,
    title: 'A BNSF Whiteout Crossing, Seen From the Driver\'s Cab',
    channel: { name: 'Train Crew', id: 'c_test' },
    targetDurationMinutes: 1,
    targetWordCount: 150,
    voice: 'en-US-EricNeural',
    requirements: { minimumWords: 135 },
    createdAt: '2026-11-10T02:18:07.844Z',
    ...overrides
  };
}

function words(n) {
  return Array.from({ length: n }, (_, i) => `word${i}`).join(' ');
}

function writeFile(file, content) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, content);
}

// Build a work dir (live or archived) with the given artifacts. The video
// is a dummy >= 1 MB buffer — ffmpeg validation is patched in the tests
// that need it, so its bytes are never decoded.
function writeWorkState(directory, { job, scriptWords, master, progress, vidnetsJobId, thumbnail } = {}) {
  if (job) {
    writeFile(path.join(directory, 'job.json'), JSON.stringify({
      digest: jobRunner.payloadDigest(job),
      materialDigest: jobRunner.materialDigest(job),
      job,
      savedAt: new Date().toISOString()
    }));
  }
  if (scriptWords) writeFile(path.join(directory, 'script.txt'), words(scriptWords) + '\n');
  if (master) {
    const videoFile = path.join(directory, 'video.mp4');
    writeFile(videoFile, Buffer.alloc(1024 * 1024 + 512));
    writeFile(path.join(directory, 'video-meta.json'), JSON.stringify({
      digest: job ? jobRunner.payloadDigest(job) : 'x',
      engine: 'test',
      bytes: fs.statSync(videoFile).size,
      sha256: 'abc',
      savedAt: new Date().toISOString()
    }));
  }
  if (progress) writeFile(path.join(directory, 'progress.json'), JSON.stringify({ jobId: job?.jobId || '', ...progress, updatedAt: new Date().toISOString() }));
  if (vidnetsJobId) writeFile(path.join(directory, 'vidnets-job.json'), JSON.stringify({ id: vidnetsJobId, jobId: job?.jobId || '' }));
  if (thumbnail) writeFile(path.join(directory, 'thumbnail.jpg'), Buffer.alloc(2048));
  return directory;
}

function archiveWorkDir(jobId, state) {
  const archive = path.join(workRoot, 'archive', `${jobId}-${Date.now()}`);
  writeWorkState(archive, state);
  return archive;
}

function stubClient() {
  const calls = { progress: [], upload: [], complete: [], fail: [] };
  return {
    calls,
    async progress(jobId, stage, progress, detail, advancing, extra) {
      calls.progress.push({ jobId, stage, progress, detail, advancing, logs: extra?.logs });
    },
    async upload(jobId, kind, filePath) {
      calls.upload.push({ jobId, kind, filePath });
      return { artifact: { bytes: fs.existsSync(filePath) ? fs.statSync(filePath).size : 0, sha256: 'stub' } };
    },
    async complete(jobId, body) {
      calls.complete.push({ jobId, body });
      return { ok: true };
    },
    async fail(jobId, error) {
      calls.fail.push({ jobId, error });
    }
  };
}

function patchFfmpeg({ masterOk = true, thumbOk = true } = {}) {
  const originals = {
    validateVideoMaster: ffmpeg.validateVideoMaster,
    validateThumbnail: ffmpeg.validateThumbnail
  };
  ffmpeg.validateVideoMaster = async () => masterOk
    ? { ok: true, failures: [], master: { duration: 66, width: 1920, height: 1080, hasVideo: true, hasAudio: true } }
    : { ok: false, failures: ['patched: master rejected'] };
  ffmpeg.validateThumbnail = async () => thumbOk
    ? { ok: true, width: 1920, height: 1080, size: 2048 }
    : { ok: false, error: 'patched: thumbnail rejected' };
  return () => {
    ffmpeg.validateVideoMaster = originals.validateVideoMaster;
    ffmpeg.validateThumbnail = originals.validateThumbnail;
  };
}

async function withVidnetsStub(itemById, fn) {
  const server = http.createServer((req, res) => {
    const id = String(req.url || '').split('/').pop();
    const item = itemById[id];
    if (!item) {
      res.writeHead(404, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ error: 'Job not found' }));
      return;
    }
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ item, success: true }));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const previousUrl = process.env.MUSE_VIDNETS_URL;
  const previousRoot = process.env.VIDZEO_RUNTIME_ROOT;
  process.env.MUSE_VIDNETS_URL = `http://127.0.0.1:${server.address().port}`;
  process.env.VIDZEO_RUNTIME_ROOT = path.join(workRoot, 'no-such-runtime');
  try {
    await fn();
  } finally {
    if (previousUrl === undefined) delete process.env.MUSE_VIDNETS_URL;
    else process.env.MUSE_VIDNETS_URL = previousUrl;
    if (previousRoot === undefined) delete process.env.VIDZEO_RUNTIME_ROOT;
    else process.env.VIDZEO_RUNTIME_ROOT = previousRoot;
    await new Promise(resolve => server.close(resolve));
  }
}

test('materialDigest ignores CMS bookkeeping fields and key order', () => {
  const base = makeJob();
  const restamped = { ...base, createdAt: '2026-11-10T05:09:56.730Z', updatedAt: '2026-11-10T05:10:00.000Z', attempt: 8, runId: 'other-run' };
  assert.equal(jobRunner.materialDigest(base), jobRunner.materialDigest(restamped), 'only createdAt/updatedAt/attempt/runId changed');
  const reordered = {};
  for (const key of Object.keys(base).reverse()) reordered[key] = base[key];
  assert.equal(jobRunner.materialDigest(base), jobRunner.materialDigest(reordered), 'key order must not change the digest');
  const retitled = { ...base, title: 'A Different Train Story' };
  assert.notEqual(jobRunner.materialDigest(base), jobRunner.materialDigest(retitled), 'a changed title must change the digest');
  assert.notEqual(jobRunner.payloadDigest(base), jobRunner.payloadDigest(retitled));
});

test('prepareWorkDir does not archive on a createdAt-only re-dispatch, but does on a real change', () => {
  const job = makeJob();
  const dir = path.join(workRoot, job.jobId);
  writeWorkState(dir, { job, scriptWords: 200 });
  const before = fs.readdirSync(dir).sort();
  const restamped = { ...job, createdAt: '2026-11-10T05:09:56.730Z' };
  jobRunner.prepareWorkDir(restamped, jobRunner.payloadDigest(restamped));
  assert.deepEqual(fs.readdirSync(dir).sort(), before, 'the work dir must survive a bookkeeping-only re-dispatch');
  assert.equal(fs.existsSync(path.join(workRoot, 'archive')), false, 'no archive must be created');
  const retitled = { ...job, title: 'A Different Train Story' };
  jobRunner.prepareWorkDir(retitled, jobRunner.payloadDigest(retitled));
  assert.equal(fs.existsSync(path.join(workRoot, 'archive')), true, 'a materially different job must archive the old work');
  assert.equal(fs.existsSync(path.join(dir, 'script.txt')), false, 'the archived dir is replaced by a fresh one');
});

test('decideClaim: finished master delivers, Vidnets master delivers, mid-run resumes, nothing is fresh, different spec is fresh', () => {
  const job = makeJob();
  const finished = {
    source: 'archive', directory: '/tmp/x', payload: job, scriptText: words(200),
    hasMaster: true, masterPath: '/tmp/x/video.mp4', vidnetsJobId: '', vidnetsMp4s: []
  };
  assert.equal(jobRunner.decideClaim(finished, job).decision, 'deliver');
  const vidnetsDone = {
    source: 'archive', directory: '/tmp/x', payload: job, scriptText: words(200),
    hasMaster: false, masterPath: '', vidnetsJobId: 'f04c6b9f', vidnetsMp4s: [],
    vidnetsMasterPath: '/tmp/x/vidnets/film.mp4'
  };
  assert.equal(jobRunner.decideClaim(vidnetsDone, job).decision, 'deliver');
  const midRun = {
    source: 'work', directory: '/tmp/x', payload: job, scriptText: words(200),
    hasMaster: false, masterPath: '', vidnetsJobId: 'f04c6b9f', vidnetsMp4s: [],
    progress: { stage: 'video', progress: 45, detail: 'Finding media' }
  };
  assert.equal(jobRunner.decideClaim(midRun, job).decision, 'resume');
  assert.equal(jobRunner.decideClaim(null, job).decision, 'fresh');
  const differentSpec = { ...finished, payload: { ...job, title: 'A Different Train Story' } };
  const fresh = jobRunner.decideClaim(differentSpec, job);
  assert.equal(fresh.decision, 'fresh');
  assert.match(fresh.reason, /title/, 'the mismatch must name the changed field');
});

test('findExistingWork prefers the live dir and otherwise takes the newest archive', () => {
  const job = makeJob();
  const archive = archiveWorkDir(job.jobId, { job, scriptWords: 200, progress: { stage: 'script', progress: 100, detail: 'Narration ready' } });
  const found = jobRunner.findExistingWork(job.jobId);
  assert.equal(found.source, 'archive');
  assert.equal(found.directory, archive);
  assert.equal(found.scriptText.split(/\s+/).length, 200);
  writeWorkState(path.join(workRoot, job.jobId), { job, scriptWords: 210 });
  const live = jobRunner.findExistingWork(job.jobId);
  assert.equal(live.source, 'work');
  assert.equal(live.scriptText.split(/\s+/).length, 210);
});

test('resolveClaim delivers an archived finished master without starting script/render', async () => {
  const job = makeJob();
  const archive = archiveWorkDir(job.jobId, { job, scriptWords: 200, master: true, thumbnail: true });
  const client = stubClient();
  const restore = patchFfmpeg({ masterOk: true, thumbOk: true });
  try {
    const result = await jobRunner.resolveClaim({ client, job, attempt: 2, signal: undefined, onBusy: () => {} });
    assert.equal(result.decision, 'deliver');
    assert.equal(result.ok, true);
    assert.equal(client.calls.complete.length, 1, 'the job must be marked complete on the CMS');
    assert.equal(client.calls.complete[0].jobId, job.jobId);
    assert.equal(client.calls.complete[0].body.wordCount, 200);
    const videoUpload = client.calls.upload.find(call => call.kind === 'video');
    assert.ok(videoUpload, 'the master must be uploaded');
    assert.equal(videoUpload.filePath, path.join(workRoot, job.jobId, 'video.mp4'), 'the archived master is staged into the work dir and uploaded');
    assert.equal(Boolean(client.calls.upload.find(call => call.kind === 'thumbnail')),
      !jobRunner.thumbnailsSkipped(), 'respect the worker thumbnail configuration when resuming');
    assert.ok(
      client.calls.progress.some(call => call.detail.includes('Delivering the existing master')),
      'the CMS must be told an existing master is being delivered'
    );
    assert.ok(
      client.calls.progress.every(call => call.detail !== 'Generating narration' && call.detail !== 'Starting the 1080p render'),
      'the script and render stages must never start for a finished master'
    );
    assert.equal(fs.existsSync(path.join(workRoot, job.jobId, 'video.mp4')), true);
    assert.equal(config.readActiveJob(), null, 'the active marker is cleared after delivery');
    assert.equal(fs.existsSync(archive), true, 'the archive is kept as a backup, not deleted');
  } finally {
    restore();
  }
});

test('resolveClaim resumes mid-run work, reports the real progress, and reuses the saved narration', async () => {
  const job = makeJob();
  const archive = archiveWorkDir(job.jobId, {
    job,
    scriptWords: 200,
    progress: { stage: 'video', progress: 45, detail: 'Finding media' },
    vidnetsJobId: 'vid-midrun-1'
  });
  const client = stubClient();
  const previousRenderCommand = process.env.MUSE_RENDER_COMMAND;
  process.env.MUSE_RENDER_COMMAND = 'node -e "process.exit(1)"';
  try {
    await withVidnetsStub({
      'vid-midrun-1': { id: 'vid-midrun-1', status: 'processing', progress: 45, result: {} }
    }, async () => {
      await assert.rejects(
        jobRunner.resolveClaim({ client, job, attempt: 7, signal: undefined, onBusy: () => {} }),
        /Muse render command exited with code 1/
      );
    });
    const details = client.calls.progress.map(call => `${call.stage} ${call.progress}% — ${call.detail}`);
    assert.ok(
      details.some(detail => detail.includes('Resuming existing work') && detail.includes('video 45%')),
      `the CMS must first hear the real saved progress, got: ${details.join(' | ')}`
    );
    assert.ok(details.some(detail => detail.includes('Narration ready (200 words)')), 'the saved narration must be reused, not regenerated');
    assert.ok(details.some(detail => detail.includes('Starting the 1080p render')), 'the render stage is re-entered from the checkpoint');
    assert.equal(fs.existsSync(path.join(workRoot, job.jobId, 'script.txt')), true, 'the archived work must be restored into the live work dir');
    assert.equal(fs.existsSync(archive), false, 'the archive itself is moved, not copied');
  } finally {
    if (previousRenderCommand === undefined) delete process.env.MUSE_RENDER_COMMAND;
    else process.env.MUSE_RENDER_COMMAND = previousRenderCommand;
  }
});

test('resolveClaim starts fresh when there is no local work at all', async () => {
  const job = makeJob();
  const client = stubClient();
  const previousRenderCommand = process.env.MUSE_RENDER_COMMAND;
  const previousScriptCommand = process.env.MUSE_SCRIPT_COMMAND;
  process.env.MUSE_SCRIPT_COMMAND = 'node -e "process.exit(1)"';
  delete process.env.MUSE_RENDER_COMMAND;
  try {
    await assert.rejects(
      jobRunner.resolveClaim({ client, job, attempt: 1, signal: undefined, onBusy: () => {} }),
      /Muse script command exited with code 1/
    );
    assert.ok(
      client.calls.progress.some(call => call.stage === 'script' && call.detail === 'Generating narration'),
      'a fresh run starts at the script stage'
    );
    assert.ok(
      client.calls.progress.every(call => !call.detail.includes('existing')),
      'nothing about existing work may be reported when there is none'
    );
    assert.equal(client.calls.upload.length, 0);
    assert.equal(client.calls.complete.length, 0);
  } finally {
    if (previousRenderCommand === undefined) delete process.env.MUSE_RENDER_COMMAND;
    else process.env.MUSE_RENDER_COMMAND = previousRenderCommand;
    if (previousScriptCommand === undefined) delete process.env.MUSE_SCRIPT_COMMAND;
    else process.env.MUSE_SCRIPT_COMMAND = previousScriptCommand;
  }
});

test('archived script failing validation against new targets falls through to a fresh run', async () => {
  const job = makeJob();
  archiveWorkDir(job.jobId, { job, scriptWords: 200, master: true, thumbnail: true });
  const longerJob = { ...job, targetDurationMinutes: 10, targetWordCount: 1500 };
  const client = stubClient();
  const previousScriptCommand = process.env.MUSE_SCRIPT_COMMAND;
  process.env.MUSE_SCRIPT_COMMAND = 'node -e "process.exit(1)"';
  const restore = patchFfmpeg({ masterOk: true, thumbOk: true });
  try {
    await assert.rejects(
      jobRunner.resolveClaim({ client, job: longerJob, attempt: 2, signal: undefined, onBusy: () => {} }),
      /Muse script command exited with code 1/
    );
    assert.equal(client.calls.upload.length, 0, 'a mismatched master must never be uploaded');
    assert.equal(client.calls.complete.length, 0, 'a mismatched job must never be marked complete');
    assert.ok(
      client.calls.progress.some(call => call.stage === 'script' && call.detail === 'Generating narration'),
      'the fresh run starts at the script stage'
    );
  } finally {
    restore();
    if (previousScriptCommand === undefined) delete process.env.MUSE_SCRIPT_COMMAND;
    else process.env.MUSE_SCRIPT_COMMAND = previousScriptCommand;
  }
});

test('second claim while one job is active is refused, never started', async () => {
  const activeJob = makeJob();
  config.writeActiveJob({
    jobId: activeJob.jobId,
    payload: activeJob,
    attempt: 1,
    digest: jobRunner.payloadDigest(activeJob),
    startedAt: new Date().toISOString()
  });
  const otherJob = makeJob();
  const client = stubClient();
  try {
    const result = await jobRunner.resolveClaim({ client, job: otherJob, attempt: 1, signal: undefined, onBusy: () => {} });
    assert.equal(result.decision, 'busy');
    assert.equal(result.activeJobId, activeJob.jobId);
    assert.equal(result.ok, false);
    assert.equal(client.calls.progress.length, 0, 'no progress may be reported for a refused claim');
    assert.equal(client.calls.upload.length, 0);
    assert.equal(config.readActiveJob()?.jobId, activeJob.jobId, 'the active marker is untouched');
  } finally {
    config.clearActiveJob();
  }
});

test('findVidnetsMaster reads the queue API, then falls back to the queue file, then gives up', async () => {
  const masterFile = path.join(workRoot, 'vidnets-master.mp4');
  writeFile(masterFile, Buffer.alloc(2048));
  await withVidnetsStub({
    'vid-done-1': { id: 'vid-done-1', status: 'completed', progress: 100, result: { video: masterFile } },
    'vid-busy-1': { id: 'vid-busy-1', status: 'processing', progress: 45, result: {} }
  }, async () => {
    const done = await jobRunner.findVidnetsMaster('vid-done-1');
    assert.equal(done.status, 'completed');
    assert.equal(done.videoPath, masterFile);
    const busy = await jobRunner.findVidnetsMaster('vid-busy-1');
    assert.equal(busy.status, 'processing');
    assert.equal(busy.videoPath, '');
    assert.equal(await jobRunner.findVidnetsMaster('vid-missing-1'), null, 'an unknown id is not an error, just no master');
  });
  // No server at all and no queue file: fail-soft, no throw.
  const previousUrl = process.env.MUSE_VIDNETS_URL;
  const previousRoot = process.env.VIDZEO_RUNTIME_ROOT;
  process.env.MUSE_VIDNETS_URL = 'http://127.0.0.1:1';
  process.env.VIDZEO_RUNTIME_ROOT = path.join(workRoot, 'no-such-runtime');
  try {
    assert.equal(await jobRunner.findVidnetsMaster('vid-done-1'), null);
  } finally {
    if (previousUrl === undefined) delete process.env.MUSE_VIDNETS_URL;
    else process.env.MUSE_VIDNETS_URL = previousUrl;
    if (previousRoot === undefined) delete process.env.VIDZEO_RUNTIME_ROOT;
    else process.env.VIDZEO_RUNTIME_ROOT = previousRoot;
  }
});

test('restoreArchivedWork moves the archive into place and re-labels the master', () => {
  const job = makeJob();
  const archive = archiveWorkDir(job.jobId, { job, scriptWords: 200, master: true, progress: { stage: 'video', progress: 45, detail: 'Finding media' } });
  const restored = jobRunner.restoreArchivedWork(job, archive);
  assert.equal(restored, path.join(workRoot, job.jobId));
  assert.equal(fs.existsSync(archive), false);
  assert.equal(fs.existsSync(path.join(restored, 'script.txt')), true);
  assert.equal(fs.existsSync(path.join(restored, 'video.mp4')), true);
  assert.equal(fs.existsSync(path.join(restored, 'progress.json')), true);
  const meta = JSON.parse(fs.readFileSync(path.join(restored, 'video-meta.json'), 'utf8'));
  assert.equal(meta.digest, jobRunner.payloadDigest(job), 'the saved master is re-labelled to the current dispatch');
  const marker = JSON.parse(fs.readFileSync(path.join(restored, 'job.json'), 'utf8'));
  assert.equal(marker.digest, jobRunner.payloadDigest(job));
});
