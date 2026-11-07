const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const test = require('node:test');
const file = path.resolve(__dirname, '../src/remotion/engine/RecoveryContent.js');
const moduleFixture = new Module(file, module);
moduleFixture._compile(require('esbuild').transformSync(fs.readFileSync(file, 'utf8'), {format: 'cjs'}).code, file);
const {recoveryContent, recoveryIsBackdrop, recoveryVisualGrammar, timedRecoveryNarration} = moduleFixture.exports;

test('recovery uses all scene-local narration and never adjacent context or generic intent', () => {
  const narration = 'This scene retains its own facts. Another statement explains the evidence without inventing a chart.';
  assert.equal(recoveryContent({narration}).join(' '), narration);
  assert.deepEqual(recoveryContent({recovery_context: 'Wrong adjacent person', visual_intent: 'Generic title'}), []);
  assert.deepEqual(recoveryContent({media: {graphic_fallback: {text: 'Untrusted', grounded: false}}}), []);
  assert.deepEqual(recoveryContent({media: {graphic_fallback: {text: 'Sourced narration', grounded: true}}}), ['Sourced narration']);
});

test('overflow does not strand a short phrase or change narration', () => {
  const narration = 'The process connects three parts: the wheel-speed sensors, the electronic controller, and the hydraulic unit.';
  const beats = recoveryContent({narration});
  assert.equal(beats.join(' '), narration);
  assert.ok(beats.every(beat => beat.split(' ').length >= 4));
  assert.deepEqual(recoveryContent({narration: 'This is a complete sentence with several words. Stop.'}), ['This is a complete sentence with several words.', 'Stop.']);
});

test('specialists and director recipes keep their canvas without duplicate recovery text', () => {
  for (const type of ['Chart', 'Timeline', 'Diagram', 'Comparison', 'AnimatedMap', 'PersonProfile']) {
    assert.equal(Boolean(recoveryIsBackdrop({primary_visual: {type}})), true);
  }
  assert.equal(Boolean(recoveryIsBackdrop({primary_visual: {type: 'SmartImage'}})), false);
  assert.equal(Boolean(recoveryIsBackdrop({template_id: 'generated-recipe', template_recipe: {layers: [{kind: 'diagram'}]}})), true);
});

test('gap narration uses only words inside the missing interval', () => {
  const manifest = {word_timings: [{word: 'Before', start: 0, end: .5}, {word: 'Here', start: 1, end: 1.5}, {word: 'After', start: 2, end: 2.5}]};
  assert.equal(timedRecoveryNarration(manifest, 30, 30, 30), 'Here');
});

test('recovery selects script-aware graphics and refuses invented map or chart data', () => {
  assert.equal(recoveryVisualGrammar({narration: 'The suspect left the courthouse.'}, 'crime'), 'evidence');
  assert.equal(recoveryVisualGrammar({scene_index: 1, narration: 'The court opened the hearing.'}, 'crime'), 'archive');
  assert.equal(recoveryVisualGrammar({narration: 'The Toyota Corolla used a new body design.', visual_requirement: {domain: 'automobile'}}, ''), 'blueprint');
  assert.equal(recoveryVisualGrammar({narration: 'The campaign changed the election.'}, 'politics'), 'headline');
  assert.equal(recoveryVisualGrammar({narration: 'The journey crossed the region.'}, 'travel'), 'archive');
  assert.equal(recoveryVisualGrammar({narration: 'The journey started near London.', map_shot: {from: 'London'}}, 'travel'), 'archive');
  assert.equal(recoveryVisualGrammar({narration: 'The journey crossed the region.', map_shot: {location_names: ['London']}}, 'travel'), 'map');
  assert.equal(recoveryVisualGrammar({narration: 'The result changed over time.'}, 'sports'), 'headline');
  assert.equal(recoveryVisualGrammar({narration: 'The result changed over time.', primary_visual: {type: 'Chart', props: {data: [{label: 'Before', value: 2}, {label: 'After', value: 4}]}}}, 'sports'), 'chart');
});
