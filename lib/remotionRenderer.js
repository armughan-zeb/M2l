'use strict';

/**
 * Native Remotion Documentary Video Renderer.
 *
 * Implements pure Remotion-based rendering for documentary masters:
 * - Animated Maps with route paths and camera zooms
 * - Word & Text Highlights synced to narration
 * - Technical Blueprints & Declassified Documents
 * - Historical Timelines and Milestone reveals
 * - Stat Cards and Metric Counters
 * - Camera motion, transitions, overlays and SFX cues
 * - Headless Chrome / Chromium rendering on Linux and Windows
 */

const fs = require('fs');
const path = require('path');
const os = require('os');
const crypto = require('crypto');
const { buildSemanticManifest } = require('./semanticManifest');
const { resolveRemotionWorkers } = require('./remotionWorkers');
const log = require('./log');
const ffmpeg = require('./ffmpeg');

function getRemotionBundler() {
  try {
    return require('@remotion/bundler');
  } catch (err) {
    try {
      return require(path.join(ROOT, 'vidnets', 'node_modules', '@remotion', 'bundler'));
    } catch {
      throw new Error('Remotion bundler (@remotion/bundler) is not installed. Run "npm install" in the worker directory: ' + err.message);
    }
  }
}

function getRemotionRenderer() {
  try {
    return require('@remotion/renderer');
  } catch (err) {
    try {
      return require(path.join(ROOT, 'vidnets', 'node_modules', '@remotion', 'renderer'));
    } catch {
      throw new Error('Remotion renderer (@remotion/renderer) is not installed. Run "npm install" in the worker directory: ' + err.message);
    }
  }
}

const ROOT = path.resolve(__dirname, '..');
const BUNDLE_VERSION = '1.3.0-unbreakable-sfx-v4';
let cachedBundle = null; // { serveUrl, fingerprint, builtAt }
let bundlingPromise = null;

// Persistent bundle cache: a render process restarts between jobs, so the
// in-memory cache above never survives to the next render. Bundles are
// stored under the system temp dir keyed by the content fingerprint, with
// a manifest proving the bundle matches these sources and this Remotion
// version. Same layout as vidnets/scripts/render-remotion-manifest.js.
const BUNDLE_CACHE_DIR = path.join(os.tmpdir(), 'vidzeo-remotion-bundle-cache');
const BUNDLE_MANIFEST_FILENAME = 'bundle-manifest.json';
const BUNDLE_MANIFEST_VERSION = 1;

// Files the fingerprint covers even if the import graph ever stops
// reaching them (a dropped import must fail closed toward a rebuild, not
// toward serving a stale bundle).
const FINGERPRINT_KEY_FILES = [
  path.join(ROOT, 'remotion', 'entry.jsx'),
  path.join(ROOT, 'src', 'remotion', 'DocumentaryComposition.jsx'),
  path.join(ROOT, 'src', 'remotion', 'sfx', 'catalog.json'),
  path.join(ROOT, 'src', 'remotion', 'components', 'AnimatedMap.jsx'),
  path.join(ROOT, 'src', 'remotion', 'components', 'SatelliteMap.jsx'),
  path.join(ROOT, 'src', 'remotion', 'components', 'Globe3D.jsx'),
  path.join(ROOT, 'lib', 'sfxRegistry.js'),
  path.join(ROOT, 'lib', 'semanticManifest.js'),
  path.join(ROOT, 'package.json')
];

// Static-import scan of the composition entry's local import graph. The
// fixed key-file list above misses files the composition imports from
// outside it — DocumentaryComposition.jsx imports ../production-timeline —
// so a change to such a file would leave the fingerprint unchanged and
// serve a stale bundle. Only relative specifiers are followed; package
// imports are pinned by package.json, which is hashed.
const LOCAL_IMPORT_EXTENSIONS = ['.js', '.jsx', '.ts', '.tsx', '.json', '.css'];
const IMPORT_SPECIFIER_RE = /(?:import\s+(?:[^'"]*?\s+from\s+)?|export\s+(?:[^'"]*?\s+from\s+)?|require\s*\(\s*|import\s*\(\s*)['"](\.[^'"]+)['"]/g;

function resolveLocalImport(importerPath, specifier) {
  const base = path.resolve(path.dirname(importerPath), specifier);
  const candidates = [base];
  for (const ext of LOCAL_IMPORT_EXTENSIONS) candidates.push(base + ext);
  for (const ext of LOCAL_IMPORT_EXTENSIONS) candidates.push(path.join(base, `index${ext}`));
  for (const candidate of candidates) {
    try {
      if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
    } catch (_) {}
  }
  return null;
}

function resolveCompositionImportGraph(entryPath) {
  const checkout = path.resolve(ROOT);
  const seen = new Set();
  const queue = [path.resolve(entryPath)];
  const files = [];
  while (queue.length) {
    const current = queue.shift();
    if (seen.has(current)) continue;
    seen.add(current);
    let text;
    try {
      if (!fs.existsSync(current) || !fs.statSync(current).isFile()) continue;
      text = fs.readFileSync(current, 'utf8');
    } catch (_) { continue; }
    files.push(current);
    IMPORT_SPECIFIER_RE.lastIndex = 0;
    let match;
    while ((match = IMPORT_SPECIFIER_RE.exec(text)) !== null) {
      const resolved = resolveLocalImport(current, match[1]);
      if (resolved && resolved.startsWith(checkout + path.sep) && !seen.has(resolved)) {
        queue.push(resolved);
      }
    }
  }
  return files.sort();
}

function remotionVersion() {
  try {
    return String(require('@remotion/bundler/package.json').version || 'unknown');
  } catch (_) {}
  try {
    const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));
    const deps = { ...(pkg.dependencies || {}), ...(pkg.devDependencies || {}) };
    return String(deps['@remotion/bundler'] || deps.remotion || 'unknown');
  } catch (_) {}
  return 'unknown';
}

function computeCompositionFingerprint() {
  // Content hash, not mtime/size: timestamps alone can collide after
  // restores, copies, or coarse-resolution file systems.
  const hash = crypto.createHash('sha256');
  hash.update(BUNDLE_VERSION);
  hash.update(String(process.env.MUSE_BUNDLE_VERSION || ''));
  hash.update(remotionVersion());
  const files = new Set(FINGERPRINT_KEY_FILES);
  for (const file of resolveCompositionImportGraph(path.join(ROOT, 'remotion', 'entry.jsx'))) {
    files.add(file);
  }
  for (const file of [...files].sort()) {
    try {
      if (fs.existsSync(file)) {
        hash.update(path.relative(ROOT, file).replace(/\\/g, '/'));
        hash.update(fs.readFileSync(file));
      }
    } catch (_) {}
  }
  return hash.digest('hex');
}

function bundleCachePath(fingerprint) {
  return path.join(BUNDLE_CACHE_DIR, `bundle-${fingerprint.slice(0, 20)}`);
}

function writeBundleManifest(cachedBundlePath, fingerprint) {
  const manifest = {
    version: BUNDLE_MANIFEST_VERSION,
    hash: fingerprint,
    remotionVersion: remotionVersion(),
    createdAt: new Date().toISOString(),
  };
  fs.mkdirSync(cachedBundlePath, { recursive: true });
  fs.writeFileSync(path.join(cachedBundlePath, BUNDLE_MANIFEST_FILENAME), JSON.stringify(manifest, null, 2), 'utf8');
  return manifest;
}

function readBundleManifest(cachedBundlePath) {
  try {
    const raw = JSON.parse(fs.readFileSync(path.join(cachedBundlePath, BUNDLE_MANIFEST_FILENAME), 'utf8'));
    if (raw && raw.version === BUNDLE_MANIFEST_VERSION && typeof raw.hash === 'string') return raw;
  } catch (_) {}
  return null;
}

// A cached bundle is reusable only when its manifest proves it was built
// from exactly these sources with this Remotion version.
function cachedBundleIsUsable(cachedBundlePath, fingerprint) {
  if (!fs.existsSync(path.join(cachedBundlePath, 'index.html'))) return false;
  const manifest = readBundleManifest(cachedBundlePath);
  if (!manifest) return false;
  if (manifest.hash !== fingerprint) return false;
  if (manifest.remotionVersion !== remotionVersion()) return false;
  return true;
}

function promoteBundleToCache(serveUrl, cachedBundlePath, fingerprint) {
  fs.mkdirSync(path.dirname(cachedBundlePath), { recursive: true });
  if (cachedBundleIsUsable(cachedBundlePath, fingerprint)) return cachedBundlePath;
  if (fs.existsSync(cachedBundlePath)) fs.rmSync(cachedBundlePath, { recursive: true, force: true });
  const stagedCachePath = `${cachedBundlePath}.tmp-${process.pid}-${crypto.randomBytes(4).toString('hex')}`;
  try {
    try {
      // Remotion and the cache both live under the system temp directory
      // on a normal install, so a rename avoids copying the webpack output.
      fs.renameSync(serveUrl, stagedCachePath);
    } catch (error) {
      if (error.code !== 'EXDEV') throw error;
      fs.cpSync(serveUrl, stagedCachePath, { recursive: true });
    }
    try {
      fs.renameSync(stagedCachePath, cachedBundlePath);
    } catch (error) {
      // A concurrent render may have installed the same immutable bundle.
      if (!fs.existsSync(path.join(cachedBundlePath, 'index.html'))) throw error;
      fs.rmSync(stagedCachePath, { recursive: true, force: true });
    }
  } catch (error) {
    fs.rmSync(stagedCachePath, { recursive: true, force: true });
    if (error.code === 'EEXIST' && fs.existsSync(path.join(cachedBundlePath, 'index.html'))) {
      return cachedBundlePath;
    }
    throw error;
  }
  try {
    writeBundleManifest(cachedBundlePath, fingerprint);
  } catch (error) {
    log.warn('Remotion bundle manifest could not be saved: ' + error.message);
  }
  return cachedBundlePath;
}

function clearBundleCache() {
  cachedBundle = null;
  bundlingPromise = null;
  // The disk entry is dropped too: this is called when a bundle is
  // suspect (render failed with an asset/bundle error), and reusing the
  // same on-disk bundle would just fail the same way again. A missing
  // entry costs one rebuild, never a stale render.
  try {
    const diskPath = bundleCachePath(computeCompositionFingerprint());
    if (fs.existsSync(diskPath)) fs.rmSync(diskPath, { recursive: true, force: true });
  } catch (_) {}
  log.info('Remotion bundle cache cleared');
}

async function getOrBuildBundle(publicDir, options = {}) {
  const forceRebuild = options.forceRebuild === true
    || process.env.MUSE_FORCE_REBUNDLE === '1'
    || process.env.MUSE_REBUILD_BUNDLE === '1';

  const fingerprint = computeCompositionFingerprint();

  if (!forceRebuild && cachedBundle && cachedBundle.fingerprint === fingerprint && fs.existsSync(cachedBundle.serveUrl)) {
    return cachedBundle.serveUrl;
  }

  // Cross-process cache: a render process restarts between jobs, so the
  // in-memory entry above never survives. Reuse the on-disk bundle when
  // its manifest proves it matches these sources and this Remotion.
  if (!forceRebuild) {
    const diskPath = bundleCachePath(fingerprint);
    if (cachedBundleIsUsable(diskPath, fingerprint)) {
      log.info(`Reusing cached Remotion bundle: ${fingerprint.slice(0, 8)} (manifest-verified)`);
      cachedBundle = { serveUrl: diskPath, fingerprint, builtAt: Date.now() };
      return diskPath;
    }
  }

  if (cachedBundle && (cachedBundle.fingerprint !== fingerprint || forceRebuild)) {
    log.info(`Remotion composition source changed or rebundle forced (old: ${cachedBundle.fingerprint?.slice(0, 8)}, new: ${fingerprint.slice(0, 8)}); rebuilding bundle...`);
    cachedBundle = null;
  }

  if (bundlingPromise) return bundlingPromise;

  bundlingPromise = (async () => {
    try {
      const entryPoint = path.join(ROOT, 'remotion', 'entry.jsx');
      if (!fs.existsSync(entryPoint)) {
        throw new Error(`Remotion entry point missing: ${entryPoint}`);
      }

      const { bundle } = getRemotionBundler();
      log.info(`Bundling Remotion documentary composition (v${BUNDLE_VERSION}, fp: ${fingerprint.slice(0, 8)})...`);
      const serveUrl = await bundle({
        entryPoint,
        rootDir: ROOT,
        publicDir: fs.existsSync(publicDir) ? publicDir : undefined,
        webpackOverride: (config) => {
          // Ensure resolve fallback for modern environments
          config.resolve = config.resolve || {};
          config.resolve.extensions = config.resolve.extensions || ['.js', '.jsx', '.ts', '.tsx', '.json', '.mjs'];
          config.module = config.module || {};
          config.module.rules = config.module.rules || [];

          // Allow ES module import/export in all js/jsx files
          config.module.rules.unshift({
            test: /\.(js|jsx|mjs)$/,
            type: 'javascript/auto',
            resolve: {
              fullySpecified: false
            }
          });

          return config;
        }
      });

      log.info(`Remotion bundle ready at: ${serveUrl}`);
      const cachedPath = promoteBundleToCache(serveUrl, bundleCachePath(fingerprint), fingerprint);
      log.info(`Remotion bundle cached at: ${cachedPath}`);
      cachedBundle = { serveUrl: cachedPath, fingerprint, builtAt: Date.now() };
      bundlingPromise = null;
      return cachedPath;
    } catch (err) {
      cachedBundle = null;
      bundlingPromise = null;
      throw err;
    }
  })();

  return bundlingPromise;
}

async function render(job, scriptText, workDir, options = {}) {
  const { onProgress, signal } = options;
  if (signal?.aborted) throw new Error('Render cancelled by operator');

  const { renderMedia, selectComposition } = getRemotionRenderer();
  const publicDir = path.join(ROOT, 'public');
  const manifestFile = path.join(workDir, 'remotion-manifest.json');
  const outputFile = path.join(workDir, 'video.mp4');

  // 1. Build fast-paced documentary edit manifest with visual grammar
  onProgress?.(5, 'Assembling documentary edit manifest (maps, text highlights, blueprints, sfx)');
  log.info(`Generating semantic manifest for "${job.title}"`);
  const manifest = buildSemanticManifest(job, scriptText, {
    fps: 30,
    width: 1920,
    height: 1080
  });

  fs.writeFileSync(manifestFile, JSON.stringify(manifest, null, 2), 'utf8');

  // 2. Bundle Remotion composition
  onProgress?.(15, 'Preparing Remotion template bundle');
  const serveUrl = await getOrBuildBundle(publicDir, options);

  if (signal?.aborted) throw new Error('Render cancelled by operator');

  // 3. Select Composition
  onProgress?.(25, 'Configuring Remotion composition');
  const composition = await selectComposition({
    serveUrl,
    id: 'VidzeoDocumentary',
    inputProps: { manifest }
  });

  log.info(`Selected composition "${composition.id}": ${composition.durationInFrames} frames (${Math.round(composition.durationInFrames / composition.fps)}s) at ${composition.width}x${composition.height}`);

  if (signal?.aborted) throw new Error('Render cancelled by operator');

  // 4. Render Media
  onProgress?.(30, 'Rendering 1080p documentary video master with Remotion');
  let lastReportedPct = 30;

  // Linux container headless Chrome arguments
  const chromiumArgs = [
    '--no-sandbox',
    '--disable-setuid-sandbox',
    '--disable-dev-shm-usage',
    '--disable-accelerated-2d-canvas',
    '--disable-gpu',
    '--allow-file-access-from-files',
    '--window-size=1920,1080'
  ];

  // Parallel workers: MUSE_RENDER_CONCURRENCY (aliases VIDZEO_REMOTION_WORKERS /
  // MUSE_REMOTION_WORKERS / VIDZEO_REMOTION_CONCURRENCY), default 2 (tested safe).
  // Auto-degrades from available memory / CPU at render start; an explicit
  // options.concurrency (tests/callers) bypasses the env read but not the caps.
  const workerPlan = resolveRemotionWorkers(process.env);
  const concurrency = options.concurrency
    ? Math.max(1, Math.min(workerPlan.chosen, Math.floor(Number(options.concurrency) || workerPlan.chosen)))
    : workerPlan.chosen;
  log.info(`Remotion workers: requested=${workerPlan.requested} chosen=${concurrency}`
    + ` (cpu cap ${workerPlan.cpuLimit}, memory cap ${Math.max(1, workerPlan.memoryLimit)},`
    + ` ${workerPlan.availableMb} MB available, ~${workerPlan.estimatedWorkerMb} MB/worker)`
    + (workerPlan.degraded ? ` — DEGRADED: ${workerPlan.reason}` : ''));
  if (workerPlan.degraded) onProgress?.(30, `Remotion workers degraded to ${concurrency}: ${workerPlan.reason}`);

  try {
    await renderMedia({
      composition,
      serveUrl,
      codec: 'h264',
      outputLocation: outputFile,
      inputProps: { manifest },
      chromiumOptions: {
        args: chromiumArgs
      },
      concurrency,
      onProgress: ({ progress }) => {
        // Scale progress between 30% and 92%
        const currentPct = 30 + Math.floor(progress * 62);
        if (currentPct > lastReportedPct) {
          lastReportedPct = currentPct;
          const frame = Math.floor(progress * composition.durationInFrames);
          onProgress?.(currentPct, `Remotion rendering frame ${frame}/${composition.durationInFrames} (${Math.floor(progress * 100)}%)`);
        }
      }
    });
  } catch (renderErr) {
    const errMsg = String(renderErr?.message || renderErr || '');
    const isAssetOrBundleErr = errMsg.includes('404')
      || errMsg.includes('download')
      || errMsg.includes('whoosh')
      || errMsg.includes('webpack')
      || errMsg.includes('bundle')
      || errMsg.includes('Cannot find')
      || errMsg.includes('Failed to fetch')
      || errMsg.includes('ENOENT');

    if (!options.isRetry && isAssetOrBundleErr) {
      log.warn(`Remotion render encountered asset/bundle error: ${errMsg}. Clearing bundle cache and retrying with fresh bundle...`);
      clearBundleCache();
      return render(job, scriptText, workDir, { ...options, isRetry: true, forceRebuild: true });
    }
    throw renderErr;
  }

  if (!fs.existsSync(outputFile) || fs.statSync(outputFile).size < 1024 * 1024) {
    throw new Error('Remotion render completed without creating a valid master MP4 (>1MB)');
  }

  onProgress?.(95, 'Master encoded; extracting 16:9 thumbnail');
  log.info(`Remotion 1080p master render complete (${Math.round(fs.statSync(outputFile).size / (1024 * 1024))} MB)`);

  // 5. Extract thumbnail from high-impact scene
  const thumbnailPath = path.join(workDir, 'thumbnail.jpg');
  try {
    await ffmpeg.extractFrame(outputFile, thumbnailPath, { atSeconds: 3 });
  } catch (err) {
    log.warn('Could not extract frame for thumbnail: ' + err.message);
  }

  onProgress?.(100, 'Remotion documentary render complete');

  return {
    videoPath: outputFile,
    thumbnailPath: fs.existsSync(thumbnailPath) ? thumbnailPath : '',
    manifestPath: manifestFile,
    meta: {
      engine: 'remotion-documentary',
      duration: manifest.duration,
      frames: manifest.duration_in_frames,
      width: manifest.width,
      height: manifest.height,
      sceneCount: manifest.scenes.length,
      remotionWorkers: workerPlan,
      concurrency
    }
  };
}

module.exports = {
  render,
  getOrBuildBundle,
  clearBundleCache,
  computeCompositionFingerprint,
  resolveCompositionImportGraph,
  remotionVersion,
  bundleCachePath,
  readBundleManifest,
  writeBundleManifest,
  cachedBundleIsUsable,
  BUNDLE_VERSION
};
