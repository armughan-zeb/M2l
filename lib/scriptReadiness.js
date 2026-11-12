'use strict';

const fs = require('fs');
const path = require('path');

function checkScriptReadiness({ root = path.join(__dirname, '..', 'scriptit'), resolve = require.resolve } = {}) {
  const missing = [];
  if (!fs.existsSync(path.join(root, 'cli', 'generate.js'))) missing.push('ScriptIT CLI');
  for (const name of ['rss-parser', 'sql.js']) {
    try { resolve(name, { paths: [root] }); } catch { missing.push(name); }
  }
  return { ok: missing.length === 0, missing };
}

module.exports = { checkScriptReadiness };
