'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const { writeJsonAtomic } = require('./config');
const { sleep } = require('./sleep');

function readRenderReceipt(filePath, jobId) {
  try {
    const receipt = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    if (!receipt || !/^[A-Za-z0-9_-]{1,128}$/.test(receipt.id || '') ||
        (receipt.jobId && receipt.jobId !== jobId)) {
      throw new Error('Saved receipt does not identify this job');
    }
    return receipt;
  } catch (error) {
    if (error.code === 'ENOENT') return null;
    throw new Error('Vidnets render receipt is unreadable; restore it before resubmitting: ' + error.message);
  }
}

async function submitRender({ jobId, filePath, request, submit, signal, retryDelayMs = 2000 }) {
  const body = { ...request };
  delete body.request_id;
  const requestId = 'muse-' + crypto.createHash('sha256')
    .update(JSON.stringify([jobId, body])).digest('hex').slice(0, 48);
  let saved;
  try {
    saved = JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  // Keep the exact body sent before a restart, even if runtime defaults changed.
  if (!saved?.request_id || saved.script !== body.script ||
      saved.output_package_dir !== body.output_package_dir) {
    writeJsonAtomic(filePath, { ...body, request_id: requestId }, { mode: 0o600 });
  }
  for (let index = 0; ; index += 1) {
    if (signal?.aborted) throw new Error('Vidnets submission cancelled');
    try {
      const result = await submit(filePath);
      const id = String(result.item?.id || '').trim();
      if (!/^[A-Za-z0-9_-]{1,128}$/.test(id)) throw new Error('Vidnets did not return a queue job id');
      return id;
    } catch (error) {
      const retryable = /already generating|timed out|timeout|budget|connection refused|connection reset|remote end closed|no queue job id|returned no JSON|no output|returned (408|429|5\d\d)\b/i.test(error.message);
      if (index >= 2 || !retryable) throw error;
      await sleep(retryDelayMs * (index + 1), signal);
    }
  }
}

const SERVICE_CONNECTION_ERROR = /connection refused|actively refused|connection reset|remote end closed|\b(?:ECONNREFUSED|ECONNRESET|ETIMEDOUT)\b|timed out/i;

function shouldRecoverService(error, consecutiveFailures) {
  return consecutiveFailures % 3 === 0 &&
    (SERVICE_CONNECTION_ERROR.test(error.message) || /start the service first/i.test(error.message));
}

function shouldRecheckRetry(error) {
  return SERVICE_CONNECTION_ERROR.test(error.message) || /returned (?:408|409|429|5\d\d)\b/i.test(error.message);
}

module.exports = { readRenderReceipt, submitRender, shouldRecoverService, shouldRecheckRetry };
