'use strict';

const fs = require('node:fs');
const { createHash } = require('node:crypto');
const { Readable, Transform } = require('node:stream');
const { pipeline } = require('node:stream/promises');
const { setTimeout: wait } = require('node:timers/promises');

const MAX_BYTES = 32 * 1024 * 1024;

async function fetchClip({ baseUrl, token, jobId, attempt, cut, output, seconds, transport = fetch }) {
  const base = new URL(baseUrl);
  if (base.protocol !== 'https:' &&
      !(base.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(base.hostname))) {
    throw new Error('YouTube via this PC requires the HTTPS CMS tunnel');
  }
  const deadline = performance.now() + seconds * 1000;
  const endpoint = `/api/muse/agent/jobs/${encodeURIComponent(jobId)}/clips`;
  const headers = { 'X-Muse-Agent-Key': token, 'X-Muse-Job-Attempt': String(attempt) };

  const request = async (suffix, method = 'GET', body, cap = 12_000) => {
    const left = Math.floor(deadline - performance.now());
    if (left <= 0) throw new Error('PC clip transfer timed out');
    const signal = AbortSignal.timeout(Math.min(left, cap));
    const response = await transport(new URL(endpoint + suffix, base), {
      method, headers: { ...headers, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
      redirect: 'error', signal,
    });
    if (!response.ok) {
      await response.body?.cancel();
      throw Object.assign(new Error(`CMS clip relay returned HTTP ${response.status}`), { status: response.status });
    }
    return response;
  };

  async function retry(task) {
    for (let index = 0; ; index++) {
      try { return await task(); }
      catch (error) {
        if (index === 2 || performance.now() >= deadline ||
            (error.status && error.status !== 429 && error.status < 500)) throw error;
        await wait(1000 * (index + 1));
      }
    }
  }

  const submit = () => retry(async () => (await request('', 'POST', cut)).json());
  let receipt = await submit();
  let resubmitted = false;
  while (receipt.status === 'pending' && performance.now() < deadline) {
    if (!/^[a-f0-9]{64}$/.test(receipt.id)) throw new Error('Invalid PC clip receipt');
    await wait(Math.min(1500, Math.max(0, deadline - performance.now())));
    try { receipt = await retry(async () => (await request('/' + receipt.id)).json()); }
    catch (error) {
      if (error.status !== 404 || resubmitted) throw error;
      resubmitted = true;
      receipt = await submit();
    }
  }
  if (receipt.status === 'failed') {
    const error = new Error(receipt.error || 'Local PC clip download failed');
    error.clipFailed = true;
    throw error;
  }
  if (receipt.status !== 'ready' || !/^[a-f0-9]{64}$/.test(receipt.id) ||
      !/^[a-f0-9]{64}$/.test(receipt.sha256) || !Number.isInteger(receipt.bytes) ||
      receipt.bytes < 4096 || receipt.bytes > MAX_BYTES) {
    throw new Error('PC clip was unavailable before its deadline');
  }

  const partial = output + '.part';
  try {
    await retry(async () => {
      const response = await request('/' + receipt.id + '/file', 'GET', undefined, 65_000);
      if (Number(response.headers.get('content-length')) !== receipt.bytes || !response.body) {
        await response.body?.cancel();
        throw new Error('PC clip response has an unexpected length');
      }
      const digest = createHash('sha256');
      let bytes = 0;
      const verify = new Transform({
        transform(chunk, _encoding, next) {
          bytes += chunk.length;
          if (bytes > receipt.bytes) return next(new Error('PC clip exceeded the receipt size'));
          digest.update(chunk);
          next(null, chunk);
        }
      });
      await pipeline(Readable.fromWeb(response.body), verify, fs.createWriteStream(partial));
      if (bytes !== receipt.bytes || digest.digest('hex') !== receipt.sha256) throw new Error('PC clip checksum did not match its receipt');
    });
    fs.renameSync(partial, output);
  } finally {
    fs.rmSync(partial, { force: true });
  }
}

if (require.main === module) {
  (async () => {
    const [jobId, videoId, start, duration, output, seconds] = process.argv.slice(2);
    const config = require('./config');
    const active = config.readActiveJob();
    if (active?.jobId !== jobId || active.payload.museMediaMode !== 'pc-youtube') throw new Error('Local PC clips require the active CMS Muse job');
    const saved = config.loadConfig();
    await fetchClip({
      baseUrl: config.normalizeBaseUrl(process.env.MUSE_CMS_URL || config.cmsUrl(saved)),
      token: process.env.MUSE_TOKEN || process.env.MUSE_AGENT_KEY || config.requireToken(saved),
      jobId, attempt: Number(active.attempt) || 1, cut: { videoId, start: Number(start), duration: Number(duration) },
      output, seconds: Number(seconds),
    });
  })().catch(error => {
    // Only report controlled relay errors. Transport exceptions can contain
    // private endpoint information.
    const message = error.message === 'Connect YouTube on the local PC in CMS Settings'
      ? error.message
      : error.clipFailed || error.status === 429 ? 'Local PC clip unavailable' : 'Local PC clip relay is unavailable';
    process.stderr.write(message + '\n');
    process.exitCode = 1;
  });
}

module.exports = { fetchClip };
