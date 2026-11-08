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

function computeCompositionFingerprint() {
  const hash = crypto.createHash('sha256');
  hash.update(BUNDLE_VERSION);
  hash.update(String(process.env.MUSE_BUNDLE_VERSION || ''));
  const keyFiles = [
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
  for (const file of keyFiles) {
    try {
      if (fs.existsSync(file)) {
        const stat = fs.statSync(file);
        hash.update(`${file}:${stat.mtimeMs}:${stat.size}`);
      }
    } catch (_) {}
  }
  return hash.digest('hex');
}

function clearBundleCache() {
  cachedBundle = null;
  bundlingPromise = null;
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
      cachedBundle = { serveUrl, fingerprint, builtAt: Date.now() };
      bundlingPromise = null;
      return serveUrl;
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
      concurrency: Math.max(1, Math.min(4, os.cpus().length || 2)),
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
      sceneCount: manifest.scenes.length
    }
  };
}

module.exports = {
  render,
  getOrBuildBundle,
  clearBundleCache,
  computeCompositionFingerprint,
  BUNDLE_VERSION
};
