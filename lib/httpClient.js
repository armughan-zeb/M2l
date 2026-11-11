'use strict';

const fs = require('fs');
const http = require('http');
const https = require('https');

const DEFAULT_JSON_TIMEOUT_MS = 30_000;
const DEFAULT_UPLOAD_TIMEOUT_MS = Number(process.env.MUSE_UPLOAD_TIMEOUT_MS) || 30 * 60 * 1000;

function safeError(data, status) {
  const message = data && typeof data.error === 'string' ? data.error : `CMS returned HTTP ${status}`;
  return Object.assign(new Error(message), { status, code: data?.code });
}

async function jsonRequest(baseUrl, pathname, { method = 'GET', token, body, timeoutMs } = {}) {
  const url = new URL(pathname, baseUrl + '/');
  const headers = { Accept: 'application/json' };
  if (token) headers['X-Muse-Agent-Key'] = token;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(Number(timeoutMs) || DEFAULT_JSON_TIMEOUT_MS),
      redirect: 'error'
    });
  } catch (error) {
    if (error.name === 'TimeoutError') throw new Error('The CMS did not answer in time; its saved job state is unchanged');
    throw new Error('Could not reach the CMS at ' + baseUrl + ': ' + error.message);
  }
  const text = await response.text();
  let data = {};
  if (text) {
    try { data = JSON.parse(text); }
    catch { data = { error: text.slice(0, 300) }; }
  }
  if (!response.ok) throw safeError(data, response.status);
  return data;
}

function uploadFile(baseUrl, pathname, filePath, { token, contentType, timeoutMs } = {}) {
  // One PUT of a whole master file with no retry used to fail the entire
  // delivery on any transient blip — a dropped connection halfway through a
  // 500 MB upload meant starting over from zero. Retry transient failures
  // (network errors, timeouts, 5xx) up to 3 attempts with backoff; 4xx
  // errors fail fast because re-sending will not heal them. True resume
  // needs the CMS to support Content-Range, which it does not advertise.
  const attempts = 3;
  const backoffMs = [5000, 15000];
  const attemptOnce = () => new Promise((resolve, reject) => {
    let stat;
    try { stat = fs.statSync(filePath); }
    catch (error) { reject(new Error('Artifact file is not readable: ' + error.message)); return; }
    if (!stat.isFile() || stat.size === 0) {
      reject(new Error('Artifact file is empty: ' + filePath));
      return;
    }
    const url = new URL(pathname, baseUrl + '/');
    const transport = url.protocol === 'https:' ? https : http;
    const headers = {
      'Content-Type': contentType || 'application/octet-stream',
      'Content-Length': stat.size,
      Accept: 'application/json'
    };
    if (token) headers['X-Muse-Agent-Key'] = token;
    const request = transport.request(url, { method: 'PUT', headers, timeout: Number(timeoutMs) || DEFAULT_UPLOAD_TIMEOUT_MS }, response => {
      const chunks = [];
      let size = 0;
      response.on('data', chunk => {
        size += chunk.length;
        if (size <= 1024 * 1024) chunks.push(chunk);
      });
      response.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        let data = {};
        if (text) {
          try { data = JSON.parse(text); }
          catch { data = { error: text.slice(0, 300) }; }
        }
        if (response.statusCode >= 200 && response.statusCode < 300) resolve(data);
        else reject(safeError(data, response.statusCode));
      });
    });
    request.on('timeout', () => request.destroy(new Error('Artifact upload timed out; the CMS keeps the run checkpoint')));
    request.on('error', reject);
    const source = fs.createReadStream(filePath);
    source.on('error', error => request.destroy(error));
    source.pipe(request);
  });
  const retryable = (error) => {
    if (!error) return false;
    if (typeof error.status === 'number') return error.status >= 500;
    return true; // network error, timeout, destroyed stream: retry
  };
  return (async () => {
    let lastError;
    for (let attempt = 1; attempt <= attempts; attempt += 1) {
      try {
        return await attemptOnce();
      } catch (error) {
        lastError = error;
        if (attempt === attempts || !retryable(error)) throw error;
        await new Promise(resolve => setTimeout(resolve, backoffMs[attempt - 1] || 15000));
      }
    }
    throw lastError;
  })();
}

module.exports = { jsonRequest, uploadFile };
