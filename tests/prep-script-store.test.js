'use strict';

/**
 * Prep script store (lib/prepScriptStore.js): key parity with the Python
 * half, save/find roundtrip, and exact-lookup semantics.
 */

const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const store = require('../lib/prepScriptStore');

function tempRoot(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'prep-store-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  process.env.VIDZEO_PREP_ROOT = dir;
  return dir;
}

test('titleKey matches the Python implementation byte for byte', (t) => {
  tempRoot(t);
  // Keys computed by vidnets/server/services/advance_prep.py title_key().
  assert.equal(
    store.titleKey('Flooded Desert', 10),
    '33e6322ded95b1cf2e75a2090047b566adba7e81b19a60bb037a48444abc8dc8'
  );
  assert.equal(
    store.titleKey('Prep Test Title', 1),
    'ffb5848f4f564b967b369443115308905566d51c5b11845844e4f1e3c5cd487e'
  );
  assert.equal(store.titleKey('  Flooded Desert ', 10.0), store.titleKey('flooded desert', 10));
});

test('normalizeTitle mirrors Python: case, punctuation, whitespace', (t) => {
  tempRoot(t);
  assert.equal(store.normalizeTitle("Amtrak's Long Run — Through the Crew's Day"), 'amtraks long run through the crews day');
  assert.equal(store.normalizeTitle('  Flooded   Desert  '), 'flooded desert');
});

test('save/find roundtrip; miss on other title or duration', (t) => {
  const dir = tempRoot(t);
  assert.equal(store.findPreppedScript('Flooded Desert', 10), null);
  const text = 'word '.repeat(200).trim();
  const saved = store.savePreppedScript('Flooded Desert', 10, text, { engine: 'scriptit' });
  assert.ok(saved && fs.existsSync(saved));
  assert.ok(saved.startsWith(path.join(dir, 'scripts')));
  assert.ok(fs.existsSync(path.join(path.dirname(saved), 'meta.json')));

  const found = store.findPreppedScript('flooded desert', 10);
  assert.ok(found);
  assert.equal(found.scriptText, text);
  assert.equal(found.meta.engine, 'scriptit');
  assert.equal(store.findPreppedScript('Flooded Desert', 8), null);
  assert.equal(store.findPreppedScript('Other Title', 10), null);
});

test('savePreppedScript refuses empty text', (t) => {
  tempRoot(t);
  assert.equal(store.savePreppedScript('T', 1, '   '), null);
});
