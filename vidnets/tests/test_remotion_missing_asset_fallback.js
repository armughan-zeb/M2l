const assert = require('node:assert/strict');
const path = require('node:path');
const os = require('node:os');
const { pathToFileURL } = require('node:url');
const test = require('node:test');
const { asLocalUrl, normalizeManifest } = require('../scripts/render-remotion-manifest');

const assetServer = 'http://127.0.0.1:47639';
const missingFile = path.join(os.tmpdir(), 'vidzeo-missing-scene-regression', 'lost-still.jpg');

test('missing local paths and file URLs are omitted so Remotion can draw the scene recovery graphic', () => {
  assert.equal(asLocalUrl(missingFile, assetServer), '');
  assert.equal(asLocalUrl(pathToFileURL(missingFile).href, assetServer), '');
  assert.equal(asLocalUrl('temp/recovered-scene-without-extension', assetServer), '');
  const normalized = normalizeManifest({ scenes: [{ media: { type: 'image', src: missingFile } }] }, assetServer);
  assert.equal(normalized.scenes[0].media.src, '');
});

test('external media URLs stay intact and existing local media still use the render asset server', () => {
  const remote = 'https://cdn.example.invalid/documentary/clip.mp4';
  assert.equal(asLocalUrl(remote, assetServer), remote);
  const existing = path.join(__dirname, '..', 'public', 'icon.png');
  const resolved = asLocalUrl(existing, assetServer);
  assert.match(resolved, new RegExp(`^${assetServer.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}/`));
});

test('bundled font aliases resolve and evidence directories remain metadata', () => {
  assert.match(asLocalUrl('ArchivoBlack-Regular.ttf', assetServer), /font-library\/ArchivoBlack-Regular\.ttf$/);
  const evidence = path.join(os.tmpdir(), 'vidzeo-evidence-directory');
  assert.equal(normalizeManifest({evidence_path: evidence}, assetServer).evidence_path, evidence);
});
