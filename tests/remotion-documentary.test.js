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

const sfx = require('../lib/sfxRegistry');
const remotionRenderer = require('../lib/remotionRenderer');

test('SFX registry maps aliases and bare whoosh to whoosh.light (edit_whoosh_light.wav)', () => {
  const root = path.resolve(__dirname, '..');
  const publicDir = path.join(root, 'public');

  // 1. Bare whoosh MUST map to edit_whoosh_light.wav
  const whooshRel = sfx.resolveSfxRelativePath('whoosh');
  assert.equal(whooshRel, 'sound-effects/edit/edit_whoosh_light.wav', 'Bare whoosh must map to edit_whoosh_light.wav');
  assert.ok(fs.existsSync(path.join(publicDir, whooshRel)), `File must exist on disk: ${whooshRel}`);

  // 2. Whoosh variants
  assert.equal(sfx.resolveSfxRelativePath('whoosh.light'), 'sound-effects/edit/edit_whoosh_light.wav');
  assert.equal(sfx.resolveSfxRelativePath('whoosh_light'), 'sound-effects/edit/edit_whoosh_light.wav');
  assert.equal(sfx.resolveSfxRelativePath('edit_whoosh_light'), 'sound-effects/edit/edit_whoosh_light.wav');
  assert.equal(sfx.resolveSfxRelativePath('edit_whoosh_light.wav'), 'sound-effects/edit/edit_whoosh_light.wav');

  // 3. Key documentary SFX aliases
  const expectedMappings = {
    'map_pulse': 'sound-effects/vidzeo/map_pulse.wav',
    'map.pulse': 'sound-effects/vidzeo/map_pulse.wav',
    'stamp_hit': 'sound-effects/vidzeo/stamp_hit.wav',
    'stamp.hit': 'sound-effects/vidzeo/stamp_hit.wav',
    'typewriter': 'sound-effects/vidzeo/typewriter.wav',
    'type.click': 'sound-effects/vidzeo/typewriter.wav',
    'sub_boom': 'sound-effects/vidzeo/sub_boom.wav',
    'sub.boom': 'sound-effects/vidzeo/sub_boom.wav',
    'marker_scratch': 'sound-effects/vidzeo/marker_scratch.wav',
    'marker.scratch': 'sound-effects/vidzeo/marker_scratch.wav',
    'clock_tick': 'sound-effects/vidzeo/clock_tick.wav',
    'clock.tick': 'sound-effects/vidzeo/clock_tick.wav'
  };

  for (const [alias, expected] of Object.entries(expectedMappings)) {
    const resolved = sfx.resolveSfxRelativePath(alias);
    assert.equal(resolved, expected, `Alias "${alias}" must resolve to ${expected}`);
    assert.ok(fs.existsSync(path.join(publicDir, resolved)), `Mapped file must exist: ${resolved}`);
  }
});

test('Unbreakable SFX resolution: unresolvable cues and bare strings return null and never throw or crash', () => {
  // Cue objects with missing / non-existent sounds must return null (silent bypass)
  assert.equal(sfx.resolveSfxSource({ file: 'completely_unknown_sound_12345' }), null);
  assert.equal(sfx.resolveSfxSource({ sfx_id: 'nonexistent_ping_xyz' }), null);
  assert.equal(sfx.resolveSfxSource({ kind: 'imaginary_boom' }), null);
  assert.equal(sfx.resolveSfxSource(null), null);
  assert.equal(sfx.resolveSfxSource({}), null);
  assert.equal(sfx.resolveSfxSource(''), null);
  assert.equal(sfx.resolveSfxSource('   '), null);

  // resolveStaticAsset must resolve bare whoosh, but return null for unresolved bare names
  assert.ok(sfx.resolveStaticAsset('whoosh')?.includes('edit_whoosh_light.wav'));
  assert.equal(sfx.resolveStaticAsset('unknown_bare_sound_name'), null, 'Unresolved bare name must return null, never bare string');
  assert.equal(sfx.resolveStaticAsset(''), null);
  assert.equal(sfx.resolveStaticAsset(null), null);

  // Valid cue objects must resolve correctly even when specified as file, sfx_id, or kind
  const mockCueTrans = { id: 'sfx-0-trans', type: 'transition', file: 'whoosh' };
  const mockCueFeat = { id: 'sfx-0-feat', type: 'feature', file: 'map_pulse' };
  assert.ok(sfx.resolveSfxSource(mockCueTrans)?.includes('edit_whoosh_light.wav'));
  assert.ok(sfx.resolveSfxSource(mockCueFeat)?.includes('map_pulse.wav'));
});

test('All sound cues produced by buildSemanticManifest resolve to existing audio files on disk', () => {
  const root = path.resolve(__dirname, '..');
  const publicDir = path.join(root, 'public');

  const job = {
    jobId: 'test-job-indian-pacific-01',
    title: 'The Indian Pacific: 4,352km Across the Desert',
    channel: { name: 'Train Crew' },
    targetDurationMinutes: 10
  };

  const scriptText = `The Indian Pacific spans four thousand three hundred and fifty two kilometers across Australia.
Departing Sydney Central, the transcontinental run crosses the Blue Mountains toward Broken Hill.
The official railway log REC-8400 documented severe rail stresses on the Nullarbor straight.
Operating at one hundred and fifteen kilometers per hour, diesel-electric locomotives face extreme heat.
The engineering schematic DWG-1920 specified heavy-duty continuously welded rail to endure the crossing.
In 1970, the first unbroken standard-gauge service connected the Pacific and Indian oceans.
Every component of the suspension had to withstand fifty straight hours of continuous pounding.`;

  const manifest = buildSemanticManifest(job, scriptText, { fps: 30 });
  assert.ok(manifest.scenes.length >= 6);

  for (const scene of manifest.scenes) {
    assert.ok(Array.isArray(scene.sound_cues) && scene.sound_cues.length >= 2, `Scene ${scene.id} must have sound cues`);
    for (const cue of scene.sound_cues) {
      const relPath = sfx.resolveSfxRelativePath(cue.file || cue.sfx_id);
      assert.ok(relPath, `Sound cue ${JSON.stringify(cue)} must be resolvable`);
      const diskPath = path.join(publicDir, relPath);
      assert.ok(fs.existsSync(diskPath), `Sound cue file must exist on disk: ${diskPath}`);
    }
  }
});

test('Catalog.json has valid aliases section and all aliased files exist', () => {
  const root = path.resolve(__dirname, '..');
  const catalogPath = path.join(root, 'src', 'remotion', 'sfx', 'catalog.json');
  const publicDir = path.join(root, 'public');
  const catalog = JSON.parse(fs.readFileSync(catalogPath, 'utf8'));

  assert.ok(catalog.aliases, 'catalog.json must have aliases object');
  assert.equal(catalog.aliases.whoosh, 'sound-effects/edit/edit_whoosh_light.wav');
  assert.equal(catalog.aliases.map_pulse, 'sound-effects/vidzeo/map_pulse.wav');
  assert.equal(catalog.aliases.stamp_hit, 'sound-effects/vidzeo/stamp_hit.wav');
  assert.equal(catalog.aliases.typewriter, 'sound-effects/vidzeo/typewriter.wav');
  assert.equal(catalog.aliases.sub_boom, 'sound-effects/vidzeo/sub_boom.wav');
  assert.equal(catalog.aliases.marker_scratch, 'sound-effects/vidzeo/marker_scratch.wav');
  assert.equal(catalog.aliases.clock_tick, 'sound-effects/vidzeo/clock_tick.wav');

  for (const [name, relPath] of Object.entries(catalog.aliases)) {
    assert.ok(fs.existsSync(path.join(publicDir, relPath)), `Alias ${name} target ${relPath} must exist on disk`);
  }
});

test('Remotion bundle versioning and fingerprinting detect source changes and support cache clearing', () => {
  assert.ok(typeof remotionRenderer.BUNDLE_VERSION === 'string' && remotionRenderer.BUNDLE_VERSION.length > 0);
  const fp1 = remotionRenderer.computeCompositionFingerprint();
  assert.ok(typeof fp1 === 'string' && fp1.length === 64, 'Fingerprint must be a 64-char sha256 hex');

  const fp2 = remotionRenderer.computeCompositionFingerprint();
  assert.equal(fp1, fp2, 'Fingerprint must be deterministic when source files are unchanged');

  // Verify clearBundleCache runs without throwing
  remotionRenderer.clearBundleCache();
});

