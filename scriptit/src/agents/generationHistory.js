const fs = require('fs');
const path = require('path');
const electron = (() => { try { return require('electron'); } catch { return {}; } })();
const app = electron && typeof electron === 'object' ? electron.app : null;

const MAX_ENTRIES = 40;

function historyFile() {
  const dir = app && typeof app.getPath === 'function' ? app.getPath('userData') : path.join(process.cwd(), '.cache');
  try { fs.mkdirSync(dir, { recursive: true }); } catch (_) {}
  return path.join(dir, 'scriptit-generation-history.json');
}

function readHistory() {
  try {
    const parsed = JSON.parse(fs.readFileSync(historyFile(), 'utf8'));
    return Array.isArray(parsed) ? parsed : [];
  } catch (_) {
    return [];
  }
}

function recentHistory(limit = 12) {
  return readHistory().filter(entry => entry.editorialReview?.accepted !== false).slice(-limit);
}

function reviewGeneration(script, review) {
  if (typeof review?.accepted !== 'boolean' || !String(script || '').trim()) return 0;
  const entries = readHistory();
  let changed = 0;
  for (const entry of entries) if (entry.script === script) {
    entry.editorialReview = { ...review, checkedAt: new Date().toISOString() }; changed++;
  }
  if (changed) {
    const file = historyFile(); const temporary = file + '.review.tmp';
    fs.writeFileSync(temporary, JSON.stringify(entries), 'utf8');
    fs.renameSync(temporary, file);
  }
  return changed;
}

function recordGeneration(entry) {
  const entries = readHistory();
  entries.push({ ...entry, createdAt: new Date().toISOString() });
  try { fs.writeFileSync(historyFile(), JSON.stringify(entries.slice(-MAX_ENTRIES)), 'utf8'); } catch (_) {}
}

module.exports = { recentHistory, recordGeneration, reviewGeneration };
