'use strict';

/**
 * Local worker state. The bearer token lives only in .muse-agent.json, which is
 * gitignored. Commands that print configuration must redact it.
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const os = require('os');

const ROOT = path.resolve(__dirname, '..');
// Optional .env support without a dependency. Real environment variables win.
(function loadDotEnv() {
  try {
    const file = path.join(ROOT, '.env');
    if (!fs.existsSync(file)) return;
    for (const rawLine of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
      const line = rawLine.trim();
      if (!line || line.startsWith('#')) continue;
      const match = line.match(/^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
      if (!match || process.env[match[1]] !== undefined) continue;
      let value = match[2].trim();
      if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
        value = value.slice(1, -1);
      }
      process.env[match[1]] = value;
    }
  } catch { /* a malformed .env must not prevent status/doctor from running */ }
})();

const CONFIG_FILE = process.env.MUSE_CONFIG_FILE
  ? path.resolve(process.env.MUSE_CONFIG_FILE)
  : path.join(ROOT, '.muse-agent.json');
const WORK_ROOT = process.env.MUSE_WORK_DIR
  ? path.resolve(process.env.MUSE_WORK_DIR)
  : path.join(ROOT, 'work');

function normalizeBaseUrl(value) {
  const raw = String(value || '').trim();
  if (!raw) throw new Error('CMS URL is required (for example http://127.0.0.1:3210)');
  let url;
  try { url = new URL(raw); }
  catch { throw new Error('CMS URL is not valid'); }
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('CMS URL must use http:// or https://');
  if (url.username || url.password) throw new Error('CMS URL must not contain credentials');
  if (url.search || url.hash) throw new Error('CMS URL must not contain a query string or fragment');
  url.pathname = url.pathname.replace(/\/+$/, '');
  return url.toString().replace(/\/$/, '');
}

function writeJsonAtomic(filePath, value, { mode } = {}) {
  // Durable checkpoint write: temp file in the same directory + fsync +
  // rename, then fsync the directory so the rename itself survives a
  // crash/reboot. A plain writeFileSync can leave a torn half-written
  // JSON behind when the VM dies mid-write, and the resume path reads
  // these files — a torn active-job.json or job.json used to cost the
  // whole job. Best-effort on the directory fsync (Windows has no
  // per-directory fsync); the file fsync + rename is what matters most.
  const directory = path.dirname(filePath);
  fs.mkdirSync(directory, { recursive: true });
  const temporary = `${filePath}.${process.pid}.${Date.now()}.${Math.random().toString(36).slice(2)}.tmp`;
  const descriptor = fs.openSync(temporary, 'w', mode || 0o644);
  try {
    fs.writeFileSync(descriptor, JSON.stringify(value, null, 2) + '\n', 'utf8');
    fs.fsyncSync(descriptor);
  } finally {
    fs.closeSync(descriptor);
  }
  fs.renameSync(temporary, filePath);
  try {
    const dirDescriptor = fs.openSync(directory, 'r');
    try { fs.fsyncSync(dirDescriptor); } finally { fs.closeSync(dirDescriptor); }
  } catch { /* directory fsync is best-effort */ }
}

function loadConfig() {
  try {
    if (!fs.existsSync(CONFIG_FILE)) return {};
    const parsed = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8'));
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (error) {
    throw new Error('.muse-agent.json is unreadable. Run "node muse-agent.js unpair" and pair again. ' + error.message);
  }
}

function saveConfig(patch = {}) {
  const next = { ...loadConfig(), ...patch, updatedAt: new Date().toISOString() };
  fs.mkdirSync(path.dirname(CONFIG_FILE), { recursive: true });
  const temporary = `${CONFIG_FILE}.${process.pid}.${Date.now()}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify(next, null, 2) + '\n', { encoding: 'utf8', mode: 0o600 });
  fs.renameSync(temporary, CONFIG_FILE);
  try { fs.chmodSync(CONFIG_FILE, 0o600); } catch { /* Windows uses ACLs */ }
  return next;
}

function clearConfig() {
  try { fs.unlinkSync(CONFIG_FILE); } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
}

function deviceId() {
  const current = loadConfig();
  if (current.deviceId) return current.deviceId;
  const generated = 'muse-dev-' + crypto.randomBytes(10).toString('hex');
  saveConfig({ deviceId: generated });
  return generated;
}

function requireToken(config = loadConfig()) {
  const token = String(config.token || '').trim();
  if (!token.startsWith('muse_') || token.length < 20) {
    throw new Error('This worker is not paired. Run "node muse-agent.js pair --cms <url> --code <MUSE-...>" first.');
  }
  return token;
}

function cmsUrl(config = loadConfig()) {
  return normalizeBaseUrl(config.cmsUrl || process.env.MUSE_CMS_URL || 'http://127.0.0.1:3210');
}

function safeJobId(value) {
  const id = String(value || '');
  if (id === '.' || id === '..' || !/^[A-Za-z0-9_.:-]{1,120}$/.test(id)) throw new Error('Job id contains unsupported characters');
  return id;
}

function workDir(jobId) {
  const directory = path.join(WORK_ROOT, safeJobId(jobId));
  fs.mkdirSync(directory, { recursive: true });
  return directory;
}

function activeJobFile() {
  return path.join(WORK_ROOT, 'active-job.json');
}

function readActiveJob() {
  try {
    const parsed = JSON.parse(fs.readFileSync(activeJobFile(), 'utf8'));
    return parsed && parsed.jobId && parsed.payload ? parsed : null;
  } catch {
    return null;
  }
}

function writeActiveJob(payload) {
  fs.mkdirSync(WORK_ROOT, { recursive: true });
  writeJsonAtomic(activeJobFile(), payload, { mode: 0o600 });
}

function clearActiveJob() {
  try { fs.unlinkSync(activeJobFile()); } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
}

function assertSafeFile(file, label) {
  const resolved = path.resolve(String(file || ''));
  if (!fs.existsSync(resolved) || !fs.statSync(resolved).isFile() || fs.statSync(resolved).size === 0) {
    throw new Error(`${label || 'Artifact'} is missing or empty: ${resolved}`);
  }
  return resolved;
}

function copyIntoWorkDir(source, workDirectory, name) {
  const file = assertSafeFile(source, name);
  const destination = path.join(workDirectory, name);
  if (path.resolve(file) === path.resolve(destination)) return destination;
  fs.copyFileSync(file, destination);
  return destination;
}

function workerPlatform() {
  return {
    platform: `${process.platform}-${os.arch()}`,
    hostname: os.hostname(),
    node: process.version
  };
}

module.exports = {
  ROOT,
  CONFIG_FILE,
  WORK_ROOT,
  normalizeBaseUrl,
  loadConfig,
  saveConfig,
  clearConfig,
  writeJsonAtomic,
  deviceId,
  requireToken,
  cmsUrl,
  safeJobId,
  workDir,
  readActiveJob,
  writeActiveJob,
  clearActiveJob,
  assertSafeFile,
  copyIntoWorkDir,
  workerPlatform
};
