'use strict';

/**
 * Daemon log lines, redacted like everything else that leaves the worker.
 *
 * The last N lines are kept in a small in-memory ring so the CMS progress
 * calls can carry recent log lines with them (see lib/jobRunner.js
 * report()): the CMS shows live progress instead of a stale stage, and an
 * operator reading the job sees what the worker actually did last — not
 * just a percentage. The ring is deliberately small and in-memory only;
 * the durable log is still the daemon's stdout/stderr (tmux pane).
 */

const RING_SIZE = 100;
const MAX_LINE_CHARS = 500;
const ring = [];

function redact(value) {
  return String(value == null ? '' : value)
    .replace(/muse_[A-Za-z0-9_-]+/g, 'muse_***')
    .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]+/gi, 'Bearer ***')
    .replace(/\bsk-or-v1-[A-Za-z0-9_-]+/g, 'sk-or-v1-***');
}

function timestamp() {
  return new Date().toISOString();
}

/** The most recent log lines, oldest first (default 10, capped at the ring). */
function recentLines(limit = 10) {
  const count = Math.max(1, Math.min(RING_SIZE, Math.floor(Number(limit) || 10)));
  return ring.slice(-count);
}

function push(line) {
  ring.push(String(line).slice(0, MAX_LINE_CHARS));
  if (ring.length > RING_SIZE) ring.splice(0, ring.length - RING_SIZE);
}

function info(message, extra) {
  const line = `[${timestamp()}] ${redact(message)}${extra ? ' ' + redact(JSON.stringify(extra)) : ''}`;
  push(line);
  console.log(line);
}

function warn(message, extra) {
  const line = `[${timestamp()}] WARN ${redact(message)}${extra ? ' ' + redact(JSON.stringify(extra)) : ''}`;
  push(line);
  console.error(line);
}

function error(message, extra) {
  const line = `[${timestamp()}] ERROR ${redact(message)}${extra ? ' ' + redact(JSON.stringify(extra)) : ''}`;
  push(line);
  console.error(line);
}

module.exports = { redact, info, warn, error, recentLines, RING_SIZE };
