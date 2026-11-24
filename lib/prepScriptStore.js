'use strict';

/**
 * Advance-prep script store — the Node half.
 *
 * Layout is shared with vidnets/server/services/advance_prep.py (the Python
 * half); keep the key derivation byte-identical:
 *   key = sha256('{"duration_minutes":<py-float>,"title":"<normalized>"}')
 *   dir = <prepRoot>/scripts/<key>/   (script.txt + meta.json)
 * Python json.dumps renders 10 as 10.0, so durations are formatted the
 * Python way here. Title normalization mirrors Python's
 * re.sub(r"[^\w\s-]", "", text) with Unicode word characters.
 */

const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');

function prepRoot() {
  if (process.env.VIDZEO_PREP_ROOT) return path.resolve(process.env.VIDZEO_PREP_ROOT);
  const temp = process.env.VIDZEO_TEMP_DIR
    || path.join(os.homedir(), 'workspace', 'vidnets-runtime', 'temp');
  return path.join(path.resolve(temp), 'prep');
}

function normalizeTitle(title) {
  const collapsed = String(title || '').replace(/\s+/g, ' ').trim().toLowerCase();
  // Keep Unicode letters/numbers/underscore, whitespace and hyphen — the JS
  // equivalent of Python's re.UNICODE \w in [^\w\s-].
  const stripped = collapsed.replace(/[^\p{L}\p{N}_\s-]/gu, '');
  return stripped.replace(/\s+/g, ' ').trim();
}

function pythonFloat(value) {
  const num = Number(value) || 0;
  if (Number.isInteger(num)) return `${num}.0`;
  return String(num);
}

function titleKey(title, durationMinutes) {
  const material = `{"duration_minutes":${pythonFloat(durationMinutes)},"title":${JSON.stringify(normalizeTitle(title))}}`;
  return crypto.createHash('sha256').update(material, 'utf8').digest('hex');
}

function scriptStoreDir(title, durationMinutes) {
  return path.join(prepRoot(), 'scripts', titleKey(title, durationMinutes));
}

function findPreppedScript(title, durationMinutes) {
  const directory = scriptStoreDir(title, durationMinutes);
  const scriptPath = path.join(directory, 'script.txt');
  let scriptText = '';
  try {
    scriptText = fs.readFileSync(scriptPath, 'utf8').trim();
  } catch {
    return null;
  }
  if (!scriptText) return null;
  let meta = {};
  try {
    meta = JSON.parse(fs.readFileSync(path.join(directory, 'meta.json'), 'utf8')) || {};
  } catch {
    meta = {};
  }
  if (meta.title && normalizeTitle(meta.title) !== normalizeTitle(title)) return null;
  if (meta.duration_minutes !== undefined && meta.duration_minutes !== null) {
    if (Number(meta.duration_minutes) !== (Number(durationMinutes) || 0)) return null;
  }
  return { scriptText, meta, path: scriptPath };
}

function savePreppedScript(title, durationMinutes, scriptText, meta = {}) {
  const text = String(scriptText || '').trim();
  if (!text) return null;
  const directory = scriptStoreDir(title, durationMinutes);
  fs.mkdirSync(directory, { recursive: true });
  const scriptPath = path.join(directory, 'script.txt');
  const tmp = `${scriptPath}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, text + '\n', 'utf8');
  fs.renameSync(tmp, scriptPath);
  const payload = {
    ...meta,
    title: String(title || ''),
    duration_minutes: Number(durationMinutes) || 0,
    words: text.split(/\s+/).filter(Boolean).length,
    engine: meta.engine || 'scriptit',
    prepped_at: new Date().toISOString()
  };
  const metaPath = path.join(directory, 'meta.json');
  const metaTmp = `${metaPath}.${process.pid}.tmp`;
  fs.writeFileSync(metaTmp, JSON.stringify(payload, null, 2) + '\n', 'utf8');
  fs.renameSync(metaTmp, metaPath);
  return scriptPath;
}

module.exports = {
  prepRoot,
  normalizeTitle,
  titleKey,
  scriptStoreDir,
  findPreppedScript,
  savePreppedScript
};
