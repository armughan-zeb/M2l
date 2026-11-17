'use strict';

/**
 * Minimal process-local cache for ScriptIT agents.
 *
 * This module was required by scriptWriter, structureCloner and
 * narratorVideo but was never committed to the repo, so every path that
 * loaded those agents crashed with MODULE_NOT_FOUND. This is the whole
 * contract those callers use: get/set (plus has/delete/clear for
 * completeness). It is intentionally in-memory only:
 *
 *  - Research persistence already lives in the SQLite research store
 *    (src/research/store.js), so a second file cache would only add
 *    stale-read bugs.
 *  - Tests and one-shot CLI runs must not leak cached research into
 *    the next run.
 *
 * Callers treat a miss as a falsy return, so get() returns undefined
 * for unknown keys.
 */

const store = new Map();

function get(key) {
  return store.get(key);
}

function set(key, value) {
  store.set(key, value);
  return value;
}

function has(key) {
  return store.has(key);
}

function del(key) {
  return store.delete(key);
}

function clear() {
  store.clear();
}

module.exports = { get, set, has, delete: del, clear };
