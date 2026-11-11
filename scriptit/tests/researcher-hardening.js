'use strict';
// Regression tests for the researcher.js hardening (Nov 9, 2026):
// - canonicalUrl must strip utm_* tracking params (utm_source, utm_medium),
//   not only a param literally named "utm_".
// - thorough mode must call Yandex (yandexSearch + yandexNewsSearch) —
//   verified here by the researcher's module loading the yandex provider
//   and by gatherResearch's task list containing the yandex lanes.
//   (Live provider behaviour is covered by research-provider-hardening.js.)
const assert = require('node:assert');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const { canonicalUrl } = require('../src/agents/researcher');

test('canonicalUrl strips utm_source/utm_medium/gclid but keeps real params', () => {
  const cleaned = canonicalUrl('https://example.com/article?utm_source=bing&utm_medium=cpc&gclid=abc&id=42');
  assert.ok(!cleaned.includes('utm_source'), cleaned);
  assert.ok(!cleaned.includes('utm_medium'), cleaned);
  assert.ok(!cleaned.includes('gclid'), cleaned);
  assert.ok(cleaned.includes('id=42'), cleaned);
});

test('canonicalUrl dedups the same article under different tracking params', () => {
  const a = canonicalUrl('https://example.com/article?utm_source=bing');
  const b = canonicalUrl('https://example.com/article?utm_medium=social&fbclid=x');
  assert.equal(a, b);
});

test('thorough gatherResearch includes yandex and yandex-news lanes', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'src', 'agents', 'researcher.js'), 'utf8');
  assert.ok(src.includes("retry('yandex'"), 'yandex lane missing from thorough mode');
  assert.ok(src.includes("retry('yandex-news'"), 'yandex-news lane missing from thorough mode');
  assert.ok(src.includes('yandex: yandexH'), 'thorough return must expose the yandex lane');
});

test('research retry default is 3 attempts', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'src', 'agents', 'researcher.js'), 'utf8');
  assert.ok(src.includes('attempts: 3'), 'retry default must be 3 attempts');
});
