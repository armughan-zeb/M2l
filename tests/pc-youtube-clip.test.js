'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const { mkdtemp, readFile, rm } = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { fetchClip } = require('../lib/pcYoutubeClip');
const { buildRequest } = require('../adapters/vidnets');

test('CMS video source overrides the channel defaults for both Muse modes', () => {
  for (const name of require('../lib/museInference').OPENROUTER_ENV_VARS) delete process.env[name];
  const job = { jobId: 'r_media', channel: { videoOptions: { imageSource: 'YouTube Clips', youtubeCoverageTarget: 0.8 } } };
  const web = buildRequest({ ...job, museMediaMode: 'web-only' }, 'Script', 'test');
  assert.equal(web.image_source, 'Web Images');
  assert.equal(web.allow_cross_source_youtube_fallback, false);
  assert.equal(web.muse_clip_job_id, undefined);
  const mixed = buildRequest({ ...job, museMediaMode: 'pc-youtube' }, 'Script', 'test');
  assert.equal(mixed.image_source, 'Mixed Media');
  assert.equal(mixed.youtube_accepted_coverage_target, 0.8);
  assert.equal(mixed.muse_clip_job_id, job.jobId);
  assert.equal(mixed.yt_use_authenticated_session, false);
});

for (const corrupt of [false, true]) {
  test(corrupt ? 'Muse refuses corrupted bytes' : 'Muse polls and atomically saves a verified clip', async t => {
    const folder = await mkdtemp(path.join(os.tmpdir(), 'pc-clip-test-'));
    t.after(() => rm(folder, { recursive: true, force: true }));
    const output = path.join(folder, 'cut.mp4');
    const bytes = Buffer.alloc(8192, 11);
    const id = 'a'.repeat(64);
    const sha256 = corrupt ? 'b'.repeat(64) : createHash('sha256').update(bytes).digest('hex');
    const cut = { videoId: 'abcdefghijk', start: 44.15, duration: 4.5 };
    const paths = [];
    const transfer = fetchClip({ baseUrl: 'https://cms.example', token: 'muse_fixture', jobId: 'r_media', attempt: 3, cut, output, seconds: 12,
      async transport(url, options) {
        paths.push(url.pathname);
        assert.equal(options.headers['X-Muse-Job-Attempt'], '3');
        assert.equal(options.headers['X-Muse-Agent-Key'], 'muse_fixture');
        if (options.method === 'POST') {
          assert.deepEqual(JSON.parse(options.body), cut);
          return Response.json({ status: 'pending', id });
        }
        if (url.pathname.endsWith('/file')) {
          return new Response(bytes, { headers: { 'Content-Length': String(bytes.length) } });
        }
        return Response.json({ status: 'ready', id, bytes: bytes.length, sha256 });
      }
    });
    if (corrupt) {
      await assert.rejects(transfer, /checksum/);
      await assert.rejects(readFile(output), { code: 'ENOENT' });
    } else {
      await transfer;
      assert.deepEqual(paths, ['/api/muse/agent/jobs/r_media/clips', `/api/muse/agent/jobs/r_media/clips/${id}`,
        `/api/muse/agent/jobs/r_media/clips/${id}/file`]);
      assert.deepEqual(await readFile(output), bytes);
    }
    await assert.rejects(readFile(output + '.part'), { code: 'ENOENT' });
  });
}
