'use strict';

function redact(value) {
  return String(value == null ? '' : value)
    .replace(/muse_[A-Za-z0-9_-]+/g, 'muse_***')
    .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]+/gi, 'Bearer ***')
    .replace(/\bsk-or-v1-[A-Za-z0-9_-]+/g, 'sk-or-v1-***');
}

function timestamp() {
  return new Date().toISOString();
}

function info(message, extra) {
  console.log(`[${timestamp()}] ${redact(message)}${extra ? ' ' + redact(JSON.stringify(extra)) : ''}`);
}

function warn(message, extra) {
  console.error(`[${timestamp()}] WARN ${redact(message)}${extra ? ' ' + redact(JSON.stringify(extra)) : ''}`);
}

function error(message, extra) {
  console.error(`[${timestamp()}] ERROR ${redact(message)}${extra ? ' ' + redact(JSON.stringify(extra)) : ''}`);
}

module.exports = { redact, info, warn, error };
