'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');

const { buildSemanticManifest, assignSceneGrammar } = require('../lib/semanticManifest');

test('Semantic Manifest generates fast-paced scenes with documentary visual grammar', () => {
  const job = {
    jobId: 'test-job-doc-01',
    title: 'The Great Railway Race: 100 MPH Steam Titans',
    channel: { name: 'Train Crew' },
    targetDurationMinutes: 1
  };

  const scriptText = `In 1938, the legendary Mallard reached a verified 126 mph on the East Coast Main Line.
The tapered switch point rail mechanism required microscopic tolerances to prevent cataclysmic derailments.
According to the official Ministry of Transportation accident report, high speed switches caused four major incidents.
The route from London Kings Cross to Edinburgh Waverley was the ultimate testing ground for British engineering.
Every mechanical component was pushed beyond its theoretical limits to claim the world speed record.`;

  const manifest = buildSemanticManifest(job, scriptText, { fps: 30, width: 1920, height: 1080 });

  assert.equal(manifest.version, 1);
  assert.equal(manifest.fps, 30);
  assert.equal(manifest.width, 1920);
  assert.equal(manifest.height, 1080);
  assert.equal(manifest.title, job.title);
  assert.ok(manifest.scenes.length >= 4, `Expected at least 4 scenes, got ${manifest.scenes.length}`);

  // Check scene durations (fast-paced documentary: between 3 and 10 seconds per scene)
  for (const scene of manifest.scenes) {
    assert.ok(scene.duration >= 3.0, `Scene duration too short: ${scene.duration}`);
    assert.ok(scene.duration <= 12.0, `Scene duration too long: ${scene.duration}`);
    assert.ok(scene.graphic, 'Scene must have a graphic template');
    assert.ok(scene.graphic.template_id, 'Graphic must have template_id');
    assert.ok(scene.camera, 'Scene must have camera motion');
    assert.ok(scene.sound_cues && scene.sound_cues.length > 0, 'Scene must have sound cues');
  }

  // Check that specific documentary templates were assigned according to script keywords
  const templateIds = manifest.scenes.map(s => s.graphic.template_id);

  // 1. Stat card for 126 mph
  assert.ok(
    templateIds.includes('stat-card') || templateIds.includes('date-card'),
    `Expected stat-card or date-card in ${JSON.stringify(templateIds)}`
  );

  // 2. Blueprint for mechanism / rail
  assert.ok(
    templateIds.includes('technical-blueprint'),
    `Expected technical-blueprint in ${JSON.stringify(templateIds)}`
  );

  // 3. Declassified document for official Ministry report
  assert.ok(
    templateIds.includes('declassified-document'),
    `Expected declassified-document in ${JSON.stringify(templateIds)}`
  );

  // 4. Map route for Kings Cross / Edinburgh Waverley
  assert.ok(
    templateIds.includes('map-route'),
    `Expected map-route in ${JSON.stringify(templateIds)}`
  );

  // Check map route properties
  const mapScene = manifest.scenes.find(s => s.graphic.template_id === 'map-route');
  assert.ok(mapScene, 'Map scene must exist');
  assert.ok(Array.isArray(mapScene.graphic.props.routeCoords), 'Map scene must include route coordinates');
  assert.ok(mapScene.graphic.props.routeCoords.length >= 2, 'Route coordinates must have at least 2 points');
});

test('Remotion entry files, templates, and map assets exist in repository structure', () => {
  const root = path.resolve(__dirname, '..');
  const entryPath = path.join(root, 'remotion', 'entry.jsx');
  const compositionPath = path.join(root, 'src', 'remotion', 'DocumentaryComposition.jsx');
  const animatedMapPath = path.join(root, 'src', 'remotion', 'components', 'AnimatedMap.jsx');
  const wordHighlighterPath = path.join(root, 'src', 'remotion', 'components', 'WordHighlighter.jsx');
  const sfxCatalogPath = path.join(root, 'src', 'remotion', 'sfx', 'catalog.json');
  const publicDir = path.join(root, 'public');

  assert.ok(fs.existsSync(entryPath), 'remotion/entry.jsx must exist');
  assert.ok(fs.existsSync(compositionPath), 'DocumentaryComposition.jsx must exist');
  assert.ok(fs.existsSync(animatedMapPath), 'AnimatedMap.jsx must exist');
  assert.ok(fs.existsSync(wordHighlighterPath), 'WordHighlighter.jsx must exist');
  assert.ok(fs.existsSync(sfxCatalogPath), 'sfx/catalog.json must exist');
  assert.ok(fs.existsSync(publicDir), 'public/ directory must exist');
});
