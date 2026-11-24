'use strict';

/**
 * Render tweaks (Nov 18 2026): persistent Remotion bundle cache + pre-scaled
 * 1080p media preference. Unit tests only — the live bundle timing
 * (cold vs warm) is measured separately against the real bundle() call.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const renderManifest = require('../vidnets/scripts/render-remotion-manifest');
const remotionRenderer = require('../lib/remotionRenderer');

function hashFiles(files, baseDir, mutate = null) {
  const hash = crypto.createHash('sha256');
  for (const file of [...files].sort()) {
    hash.update(path.relative(baseDir, file).replace(/\\/g, '/'));
    let bytes = fs.readFileSync(file);
    if (mutate && mutate.file === file) bytes = Buffer.from(mutate.bytes);
    hash.update(bytes);
  }
  return hash.digest('hex');
}

test('bundle key: import graph reaches files outside the walked source roots', () => {
  const entryPath = path.join(root, 'vidnets', 'remotion', 'entry.jsx');
  const graph = renderManifest.resolveCompositionImportGraph(entryPath);
  assert.ok(graph.length > 10, `import graph should cover the composition, got ${graph.length} files`);
  // DocumentaryComposition.jsx imports ../production-timeline, which lives
  // outside remotion/ and src/remotion/. The old directory-walk key missed
  // it, so a change there served a stale bundle.
  const timeline = path.join(root, 'vidnets', 'src', 'production-timeline.js');
  assert.ok(graph.includes(timeline), 'import graph must include vidnets/src/production-timeline.js');
  assert.ok(graph.includes(entryPath), 'import graph must include the entry itself');

  // Content-sensitive: changing a graph file changes the key, changing an
  // unrelated file does not.
  const keyBefore = hashFiles(graph, path.join(root, 'vidnets'));
  const keyChanged = hashFiles(graph, path.join(root, 'vidnets'), {
    file: timeline,
    bytes: '// changed production-timeline',
  });
  assert.notEqual(keyChanged, keyBefore, 'changing a graph file must change the bundle key');
  const unrelated = path.join(root, 'vidnets', 'scripts', 'render-remotion-manifest.js');
  assert.ok(!graph.includes(unrelated), 'render runner itself is not part of the composition graph');
});

test('bundle key: repo-root renderer graph also reaches production-timeline', () => {
  const entryPath = path.join(root, 'remotion', 'entry.jsx');
  const graph = remotionRenderer.resolveCompositionImportGraph(entryPath);
  assert.ok(
    graph.includes(path.join(root, 'src', 'production-timeline.js')),
    'repo-root import graph must include src/production-timeline.js'
  );
  const fp1 = remotionRenderer.computeCompositionFingerprint();
  const fp2 = remotionRenderer.computeCompositionFingerprint();
  assert.equal(fp1.length, 64, 'fingerprint must be a 64-char sha256 hex');
  assert.equal(fp1, fp2, 'fingerprint must be deterministic when sources are unchanged');
});

test('bundle manifest: write/read round-trip and usability matrix', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vidzeo-bundle-manifest-'));
  try {
    const fingerprint = 'a'.repeat(64);
    const cached = path.join(dir, 'bundle-aaaaaaaaaaaaaaaaaaaa');
    fs.mkdirSync(cached, { recursive: true });
    fs.writeFileSync(path.join(cached, 'index.html'), '<html></html>');

    // No manifest yet: not usable (pre-manifest caches rebuild once).
    assert.equal(renderManifest.cachedBundleIsUsable(cached, fingerprint), false);

    renderManifest.writeBundleManifest(cached, fingerprint);
    const manifest = renderManifest.readBundleManifest(cached);
    assert.equal(manifest.hash, fingerprint);
    assert.equal(manifest.remotionVersion, renderManifest.remotionVersion());
    assert.equal(renderManifest.cachedBundleIsUsable(cached, fingerprint), true);

    // Hash mismatch -> rebuild.
    assert.equal(renderManifest.cachedBundleIsUsable(cached, 'b'.repeat(64)), false);

    // Missing index.html -> rebuild even with a valid manifest.
    fs.rmSync(path.join(cached, 'index.html'));
    assert.equal(renderManifest.cachedBundleIsUsable(cached, fingerprint), false);
    fs.writeFileSync(path.join(cached, 'index.html'), '<html></html>');

    // Remotion version mismatch -> rebuild.
    const tampered = { ...manifest, remotionVersion: '0.0.0-test' };
    fs.writeFileSync(
      path.join(cached, 'bundle-manifest.json'),
      JSON.stringify(tampered),
      'utf8'
    );
    assert.equal(renderManifest.cachedBundleIsUsable(cached, fingerprint), false);

    // Corrupt manifest -> rebuild, never crash.
    fs.writeFileSync(path.join(cached, 'bundle-manifest.json'), '{not json', 'utf8');
    assert.equal(renderManifest.readBundleManifest(cached), null);
    assert.equal(renderManifest.cachedBundleIsUsable(cached, fingerprint), false);

    // Same helpers on the repo-root renderer module.
    const cached2 = path.join(dir, 'bundle-bbbbbbbbbbbbbbbbbbbb');
    fs.mkdirSync(cached2, { recursive: true });
    fs.writeFileSync(path.join(cached2, 'index.html'), '<html></html>');
    remotionRenderer.writeBundleManifest(cached2, fingerprint);
    assert.equal(remotionRenderer.cachedBundleIsUsable(cached2, fingerprint), true);
    assert.equal(remotionRenderer.bundleCachePath(fingerprint).endsWith(`bundle-${fingerprint.slice(0, 20)}`), true);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('scaled-1080p preference: sibling used when present, original otherwise', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vidzeo-scaled-'));
  try {
    const photo = path.join(dir, 'photo.jpg');
    const scaledPhoto = path.join(dir, 'photo.scaled-1080p.jpg');
    fs.writeFileSync(photo, 'orig');
    assert.equal(renderManifest.scaled1080pPathFor(photo), scaledPhoto);
    assert.equal(renderManifest.preferScaled1080p(photo), photo, 'absent sibling -> original');
    fs.writeFileSync(scaledPhoto, 'scaled');
    assert.equal(renderManifest.preferScaled1080p(photo), scaledPhoto, 'present sibling -> scaled');

    const clip = path.join(dir, 'scene.mp4');
    const scaledClip = path.join(dir, 'scene.scaled-1080p.mp4');
    fs.writeFileSync(clip, 'clip');
    assert.equal(renderManifest.preferScaled1080p(clip), clip);
    fs.writeFileSync(scaledClip, 'scaled-clip');
    assert.equal(renderManifest.preferScaled1080p(clip), scaledClip);

    // Audio and fonts are never swapped, even if a sibling exists.
    const audio = path.join(dir, 'voice.mp3');
    fs.writeFileSync(audio, 'audio');
    fs.writeFileSync(path.join(dir, 'voice.scaled-1080p.mp3'), 'scaled-audio');
    assert.equal(renderManifest.preferScaled1080p(audio), audio);
    assert.equal(renderManifest.preferScaled1080p(''), '');
    assert.equal(renderManifest.preferScaled1080p(null), null);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('asLocalUrl resolves a scene asset to its pre-scaled 1080p sibling', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vidzeo-asset-url-'));
  const baseUrl = 'http://127.0.0.1:9';
  try {
    const photo = path.join(dir, 'still.jpg');
    fs.writeFileSync(photo, 'orig');
    const plain = renderManifest.asLocalUrl(photo, baseUrl);
    assert.ok(plain.includes('still.jpg'), `expected original in URL, got ${plain}`);
    assert.ok(!plain.includes('scaled-1080p'), `no sibling yet, got ${plain}`);

    fs.writeFileSync(path.join(dir, 'still.scaled-1080p.jpg'), 'scaled');
    const scaled = renderManifest.asLocalUrl(photo, baseUrl);
    assert.ok(scaled.includes('still.scaled-1080p.jpg'), `expected scaled sibling in URL, got ${scaled}`);

    // Missing asset still blanks (scene recovery), never a broken URL.
    assert.equal(renderManifest.asLocalUrl(path.join(dir, 'gone.jpg'), baseUrl), '');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('import-graph fixture: hit on unchanged sources, miss on a changed import', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vidzeo-graph-fixture-'));
  try {
    const lib = path.join(dir, 'lib');
    fs.mkdirSync(lib, { recursive: true });
    const entry = path.join(dir, 'entry.jsx');
    const helper = path.join(lib, 'helper.js');
    const other = path.join(dir, 'unrelated.js');
    fs.writeFileSync(entry, "import { x } from './lib/helper';\nexport default x;\n");
    fs.writeFileSync(helper, 'export const x = 1;\n');
    fs.writeFileSync(other, 'export const y = 2;\n');

    const graph = renderManifest.resolveCompositionImportGraph(entry, dir);
    assert.deepEqual(graph, [entry, helper].sort());
    const key1 = hashFiles(graph, dir);
    // Unrelated file change: key unchanged (cache hit).
    fs.writeFileSync(other, 'export const y = 3;\n');
    assert.equal(hashFiles(renderManifest.resolveCompositionImportGraph(entry, dir), dir), key1);
    // Imported helper change: key changes (cache miss -> rebuild).
    fs.writeFileSync(helper, 'export const x = 2;\n');
    assert.notEqual(hashFiles(renderManifest.resolveCompositionImportGraph(entry, dir), dir), key1);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
