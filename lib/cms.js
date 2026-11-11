'use strict';

/** Authenticated transport for the CMS Muse worker API. */

const { jsonRequest, uploadFile } = require('./httpClient');

function createClient({ baseUrl, token }) {
  if (!baseUrl) throw new Error('CMS URL is required');
  if (!token) throw new Error('Muse worker token is required');

  const call = (pathname, options = {}) => jsonRequest(baseUrl, pathname, { ...options, token });
  const jobPath = (jobId, suffix) => `/api/muse/agent/jobs/${encodeURIComponent(jobId)}${suffix || ''}`;

  return {
    baseUrl,
    heartbeat(info = {}) {
      return call('/api/muse/agent/heartbeat', { method: 'POST', body: info });
    },
    nextJob() {
      return call('/api/muse/agent/jobs/next');
    },
    // Live progress (Nov 10, 2026): the CMS used to show a stale stage
    // because the daemon only ever sent stage/progress/detail — the last
    // few daemon log lines travel with every progress call now, so the
    // job page shows what the worker is actually doing, not just a %.
    // `extra` is forward-compatible: unknown CMS fields are ignored by
    // the server, so a strict CMS simply drops `logs` and nothing breaks.
    progress(jobId, stage, progress, detail, advancing = true, extra = {}) {
      const body = { stage, progress, detail, advancing: advancing !== false };
      if (Array.isArray(extra.logs) && extra.logs.length) {
        body.logs = extra.logs.map(line => String(line).slice(0, 500)).slice(0, 50);
      }
      for (const [key, value] of Object.entries(extra || {})) {
        if (key !== 'logs' && value !== undefined) body[key] = value;
      }
      return call(jobPath(jobId, '/progress'), {
        method: 'POST',
        body
      });
    },
    upload(jobId, kind, filePath) {
      if (!['video', 'thumbnail'].includes(kind)) throw new Error('Artifact kind must be video or thumbnail');
      return uploadFile(baseUrl, jobPath(jobId, `/artifacts/${kind}`), filePath, {
        token,
        contentType: kind === 'video' ? 'video/mp4' : contentTypeFor(filePath),
        timeoutMs: Number(process.env.MUSE_UPLOAD_TIMEOUT_MS) || 30 * 60 * 1000
      });
    },
    complete(jobId, body) {
      return call(jobPath(jobId, '/complete'), { method: 'POST', body });
    },
    fail(jobId, error) {
      return call(jobPath(jobId, '/fail'), { method: 'POST', body: { error } });
    }
  };
}

function contentTypeFor(filePath) {
  switch (String(filePath || '').toLowerCase().split('.').pop()) {
    case 'png': return 'image/png';
    case 'webp': return 'image/webp';
    default: return 'image/jpeg';
  }
}

async function publicStatus(baseUrl) {
  return jsonRequest(baseUrl, '/api/muse');
}

module.exports = { createClient, publicStatus };
