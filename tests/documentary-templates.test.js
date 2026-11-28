'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const { listTemplates, resolveDocumentaryTemplate } = require('../lib/documentaryTemplates');

test('listTemplates returns all three templates with existing dirs', () => {
  const templates = listTemplates();
  assert.equal(templates.length, 3);
  assert.deepEqual(templates.map((t) => t.id), ['standard', 'crime', 'history']);
  for (const t of templates) {
    assert.equal(path.isAbsolute(t.dir), true, `${t.id} dir must be absolute`);
    assert.equal(fs.existsSync(path.join(t.dir, 'package.json')), true, `${t.id} missing package.json`);
    assert.equal(fs.existsSync(path.join(t.dir, 'src', 'Composition.tsx')), true, `${t.id} missing Composition.tsx`);
    assert.equal(fs.existsSync(path.join(t.dir, 'src', 'RemotionRoot.tsx')), true, `${t.id} missing RemotionRoot.tsx`);
    assert.equal(fs.existsSync(path.join(t.dir, 'src', 'index.ts')), true, `${t.id} missing index.ts`);
    assert.equal(typeof t.label, 'string');
    assert.ok(t.label.length > 0);
  }
  // The two new variants also ship a machine-readable style/SFX config.
  for (const id of ['crime', 'history']) {
    const dir = templates.find((t) => t.id === id).dir;
    const config = JSON.parse(fs.readFileSync(path.join(dir, 'template-config.json'), 'utf8'));
    assert.equal(config.id, id);
    assert.equal(config.dirName, path.basename(dir));
    assert.ok(Array.isArray(config.keywords) && config.keywords.length > 0);
    assert.ok(config.sfx && typeof config.sfx.bed.gainDb === 'number');
    assert.ok(config.palette && config.typography && config.motion);
  }
  // Same dependency versions as the standard template.
  const standardPkg = JSON.parse(fs.readFileSync(path.join(templates[0].dir, 'package.json'), 'utf8'));
  for (const t of templates.slice(1)) {
    const pkg = JSON.parse(fs.readFileSync(path.join(t.dir, 'package.json'), 'utf8'));
    assert.deepEqual(pkg.dependencies, standardPkg.dependencies, `${t.id} deps must match standard`);
  }
});

test('explicit genre selects the variant', () => {
  assert.equal(resolveDocumentaryTemplate({ documentaryOptions: { genre: 'crime' } }).id, 'crime');
  assert.equal(resolveDocumentaryTemplate({ documentaryOptions: { genre: 'Mystery' } }).id, 'crime');
  assert.equal(resolveDocumentaryTemplate({ documentaryOptions: { genre: 'true crime' } }).id, 'crime');
  assert.equal(resolveDocumentaryTemplate({ genre: 'history' }).id, 'history');
  assert.equal(resolveDocumentaryTemplate({ documentaryOptions: { genre: 'expedition' } }).id, 'history');
  assert.equal(resolveDocumentaryTemplate({ documentaryOptions: { genre: 'historical' } }).id, 'history');
});

test('title keywords select the variant', () => {
  assert.equal(resolveDocumentaryTemplate({ title: 'The Vanishing of Flight 19' }).id, 'crime');
  assert.equal(resolveDocumentaryTemplate({ title: 'The Disappearance of the Mary Celeste' }).id, 'crime');
  assert.equal(resolveDocumentaryTemplate({ title: 'Cold Case: The Black Box Tapes' }).id, 'crime');
  assert.equal(resolveDocumentaryTemplate({ title: 'Building the Iron Road' }).id, 'history');
  assert.equal(resolveDocumentaryTemplate({ title: 'Lost Expeditions of the Amazon' }).id, 'history');
  assert.equal(resolveDocumentaryTemplate({ title: 'The Ancient Railway Pioneers' }).id, 'history');
});

test('channel name and videoStyle keywords select the variant', () => {
  assert.equal(resolveDocumentaryTemplate({ title: 'Episode 4', channel: { name: 'Black Box' } }).id, 'crime');
  assert.equal(resolveDocumentaryTemplate({ title: 'Episode 4', channel: { name: 'Vanished at Sea' } }).id, 'crime');
  assert.equal(resolveDocumentaryTemplate({ title: 'Episode 4', channel: { name: 'Iron Road' } }).id, 'history');
  assert.equal(resolveDocumentaryTemplate({ title: 'Episode 4', channel: { name: 'Lost Expeditions' } }).id, 'history');
  assert.equal(resolveDocumentaryTemplate({ title: 'Episode 4', videoStyle: 'mystery' }).id, 'crime');
  assert.equal(resolveDocumentaryTemplate({ title: 'Episode 4', videoStyle: 'expedition' }).id, 'history');
});

test('crime keywords win when a title matches both variants', () => {
  assert.equal(resolveDocumentaryTemplate({ title: 'The Vanished Expedition' }).id, 'crime');
});

test('default fallback: everything else and unknown genres resolve to standard', () => {
  assert.equal(resolveDocumentaryTemplate({ title: 'How Bees Make Honey' }).id, 'standard');
  assert.equal(resolveDocumentaryTemplate({ title: 'When Structures Fail: The Bridge' }).id, 'standard');
  assert.equal(resolveDocumentaryTemplate({ documentaryOptions: { genre: 'cooking' } }).id, 'standard');
  assert.equal(resolveDocumentaryTemplate({ genre: 'sports' }).id, 'standard');
  assert.equal(resolveDocumentaryTemplate({}).id, 'standard');
  assert.equal(resolveDocumentaryTemplate().id, 'standard');
  assert.equal(resolveDocumentaryTemplate(null).id, 'standard');
});

test('resolveDocumentaryTemplate is pure: same input, same output', () => {
  const job = { title: 'The Vanishing of Flight 19', channel: { name: 'Black Box' } };
  assert.deepEqual(resolveDocumentaryTemplate(job), resolveDocumentaryTemplate(job));
  assert.deepEqual(resolveDocumentaryTemplate(job), resolveDocumentaryTemplate({ ...job }));
});
