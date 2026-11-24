/* Render a saved SemanticEditManifest through the production Remotion entry. */
const path = require('path');
const fs = require('fs');
const http = require('http');
const os = require('os');
const crypto = require('crypto');
const net = require('net');
const { fileURLToPath } = require('url');
const { joinRemotionSections, promoteExport } = require('./remotion-section-join');
const root = path.resolve(__dirname, '..');
const warnedMissingAssets = new Set();
// Admission floor, evidence-backed (Nov 16, 2026): the Wright render was
// admitted at 615 MB free and completed (~23x real-time, 409 MB swap in
// use); GTA VI progressed at 830 MB. The old 768 MB floor sat above this
// VM's steady-state available memory (~350-650 MB), so the GTA job waited
// forever: a render needs the browser tree (~353 MB RSS, measured on a
// trivial 1080p page) plus the render Node process and ffmpeg (~150 MB)
// just to load — about 500 MB. 512 sits just above that load minimum and
// below the proven-completion band (615 MB). Below the floor a job waits
// (retryable) instead of starting a browser it cannot back. Keep in step
// with render_resource_manager.py.
const MIN_REMOTION_AVAILABLE_MB = 512;
// Remotion (@remotion/media) rejects mediaCacheSizeInBytes below 240 MB
// (251658240 bytes). GTA VI ef306926 died at 78% with exactly that error
// after the low-memory path handed it 64 MB (67108864). This floor is a
// hard Remotion minimum, never a tuning knob.
const MIN_MEDIA_CACHE_MB = 240;
const MIN_MEDIA_CACHE_BYTES = MIN_MEDIA_CACHE_MB * 1024 ** 2;
// Honest resident working set of one serial 1080p worker (Nov 16, 2026):
// the measured ~353 MB headless-shell browser tree, plus ~150 MB for the
// render Node process and the ffmpeg encoder, plus ~50 MB of headroom.
// The old budget summed the CONFIG caps (media 240 + decoder 256 +
// worker 400 + reserve 555 + margin 96 = 1547 MB) as if all were resident
// at once; they are not — mediaCacheSizeInBytes and the decoder cache are
// caps on caches that only grow to what the current section decodes, so
// summing them priced every steady-state render out of existence. The
// gate budgets the working set instead; swap absorbs the difference
// (Wright completed with 409 MB of swap in use). Never below the Remotion
// floors: the CONFIG values passed to Remotion stay at 240/256 MB.
const WORKER_WORKING_SET_MB = 550;
// Resident cache working set for the section being rendered: 64 MB of
// decoder cache (8 decoded 1080p RGBA frames — serial rendering decodes
// near the current timestamp) + 96 MB of media cache. Risk, stated
// plainly: a scene with many large images can grow the media cache past
// this allowance; the excess pages to swap (Wright proved it finishes)
// and, in the worst case, a frame is re-decoded rather than served from
// cache. The 240 MB CONFIG floor is untouched.
const CACHE_WORKING_SET_MB = 160;

function renderSectionHasher(payloadText, bundleHash, options) {
  // Version the output contract, not this runner's entire source. Admission,
  // logging and worker-count fixes must not discard already encoded sections.
  // Bundle/manifest/assets and pixel/audio settings still invalidate them.
  const {scale, selectedCrf, selectedX264Preset, frameRange, framesPerChunk} = options;
  return crypto.createHash('sha256').update('vidzeo-render-sections-v1')
    .update(payloadText).update(bundleHash).update(JSON.stringify({
      scale, selectedCrf, selectedX264Preset, frameRange, framesPerChunk,
      codec:'h264', sectionAudio:'pcm-48000', gl:'angle',
    }));
}

function renderBrowserSession(openBrowser) {
  let browser = null;
  return {
    async get(options) {
      // A browser killed out from under us (guard, crash, OOM) must
      // never be handed back: Remotion hangs on its dead pages instead
      // of erroring, so the section render stalls at 0 frames until
      // the 300 s watchdog kills the whole attempt (GTA VI ef306926).
      // Reopen when the cached browser is gone or reports disconnected.
      if (browser && typeof browser.isConnected === 'function' && !browser.isConnected()) {
        browser = null;
      }
      if (!browser) browser = await openBrowser('chrome', options);
      return browser;
    },
    async close() {
      const owned = browser;
      browser = null;
      if (owned) await owned.close({silent:true});
    },
  };
}

function ensureFfmpegOutputDirectory(args) {
  if (!Array.isArray(args)) return;
  // FFmpeg's output is the final argument. -y is a flag and may be followed
  // by -f/mp4 rather than by a filename, especially in the Fast Start pass.
  const output = args[args.length - 1];
  if (typeof output !== 'string' || !output || output === '-' || output.startsWith('-') || /^pipe:/.test(output)) return;
  fs.mkdirSync(path.dirname(path.resolve(output)), { recursive: true });
}

async function finalizeMp4Audio(file, callFfOverride) {
  // Checkpoint sections use PCM to avoid AAC padding at every section seam.
  // The delivered MP4 still needs AAC for browser and channel compatibility.
  // Copy the finished video stream; encode continuous audio exactly once.
  if (path.extname(file).toLowerCase() !== '.mp4') return;
  file = path.resolve(file);
  const callFf = callFfOverride || require(path.join(root, 'node_modules', '@remotion', 'renderer', 'dist', 'call-ffmpeg.js')).callFf;
  const compatible = `${file}.compatible.mp4`;
  try {
    await callFf({ bin: 'ffmpeg', indent: false, logLevel: 'error',
      args: ['-hide_banner', '-loglevel', 'error', '-y', '-i', file,
        '-map', '0:v:0', '-map', '0:a:0', '-c:v', 'copy', '-c:a', 'aac',
        '-b:a', '160k', '-movflags', '+faststart', '-f', 'mp4', compatible] });
    if (!fs.existsSync(compatible) || fs.statSync(compatible).size < 4096) {
      throw new Error('Compatible MP4 export is missing or empty; section checkpoints remain available');
    }
    fs.renameSync(compatible, file);
  } finally {
    if (fs.existsSync(compatible)) fs.unlinkSync(compatible);
  }
}

// Vidzeo Remotion Stability Guards:
// Windows race condition defense: protect temporary download map, audio mixing,
// and ffmpeg output target directories from premature cleanup or missing parents.
function installRemotionStabilityGuards() {
  try {
    const callFfPath = path.join(root, 'node_modules', '@remotion', 'renderer', 'dist', 'call-ffmpeg.js');
    if (fs.existsSync(callFfPath)) {
      const callFfModule = require(callFfPath);
      const origCallFf = callFfModule.callFf;
      const origCallFfNative = callFfModule.callFfNative;

      const ensureOutputDirs = ensureFfmpegOutputDirectory;

      if (origCallFf && !origCallFf.__vidzeo_patched) {
        callFfModule.callFf = function(options) {
          if (options && options.args) {
            ensureOutputDirs(options.args);
          }
          return origCallFf.apply(this, arguments);
        };
        callFfModule.callFf.__vidzeo_patched = true;
      }

      if (origCallFfNative && !origCallFfNative.__vidzeo_patched) {
        callFfModule.callFfNative = function(options) {
          if (options && options.args) {
            ensureOutputDirs(options.args);
          }
          return origCallFfNative.apply(this, arguments);
        };
        callFfModule.callFfNative.__vidzeo_patched = true;
      }
    }

    const dmPath = path.join(root, 'node_modules', '@remotion', 'renderer', 'dist', 'assets', 'download-map.js');
    if (fs.existsSync(dmPath)) {
      const dmModule = require(dmPath);
      const origMakeDownloadMap = dmModule.makeDownloadMap;
      const origCleanDownloadMap = dmModule.cleanDownloadMap;

      if (origMakeDownloadMap && !origMakeDownloadMap.__vidzeo_patched) {
        dmModule.makeDownloadMap = function(...args) {
          const map = origMakeDownloadMap.apply(this, args);
          let prevented = true;
          map.preventCleanup = () => { prevented = true; };
          map.allowCleanup = () => { prevented = false; };
          map.isPreventedFromCleanup = () => prevented;
          const ensureDirs = () => {
            const dirs = [map.assetDir, map.downloadDir, map.complexFilter, map.preEncode, map.audioMixing, map.audioPreprocessing, map.stitchFrames, map.compositingDir];
            for (const d of dirs) {
              if (d && !fs.existsSync(d)) {
                try { fs.mkdirSync(d, { recursive: true }); } catch (_) {}
              }
            }
          };
          ensureDirs();
          map.ensureAllDirs = ensureDirs;
          return map;
        };
        dmModule.makeDownloadMap.__vidzeo_patched = true;
      }

      if (origCleanDownloadMap && !origCleanDownloadMap.__vidzeo_patched) {
        dmModule.cleanDownloadMap = function(downloadMap) {
          if (downloadMap && typeof downloadMap.isPreventedFromCleanup === 'function' && downloadMap.isPreventedFromCleanup()) {
            return;
          }
          return origCleanDownloadMap.apply(this, arguments);
        };
        dmModule.cleanDownloadMap.__vidzeo_patched = true;
      }
    }

    const createAudioPath = path.join(root, 'node_modules', '@remotion', 'renderer', 'dist', 'create-audio.js');
    if (fs.existsSync(createAudioPath)) {
      const createAudioModule = require(createAudioPath);
      const origCreateAudio = createAudioModule.createAudio;
      if (origCreateAudio && !origCreateAudio.__vidzeo_patched) {
        createAudioModule.createAudio = async function(options) {
          if (options && options.downloadMap) {
            const dm = options.downloadMap;
            for (const d of [dm.audioMixing, dm.audioPreprocessing, dm.complexFilter, dm.assetDir]) {
              if (d && !fs.existsSync(d)) {
                try { fs.mkdirSync(d, { recursive: true }); } catch (_) {}
              }
            }
          }
          return origCreateAudio.apply(this, arguments);
        };
        createAudioModule.createAudio.__vidzeo_patched = true;
      }
    }

    const preprocessPath = path.join(root, 'node_modules', '@remotion', 'renderer', 'dist', 'preprocess-audio-track.js');
    if (fs.existsSync(preprocessPath)) {
      const preprocessModule = require(preprocessPath);
      const origPreprocess = preprocessModule.preprocessAudioTrack;
      if (origPreprocess && !origPreprocess.__vidzeo_patched) {
        preprocessModule.preprocessAudioTrack = function(options) {
          if (options && options.outName) {
            try {
              const outDir = path.dirname(path.resolve(options.outName));
              if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });
            } catch (_) {}
          }
          return origPreprocess.apply(this, arguments);
        };
        preprocessModule.preprocessAudioTrack.__vidzeo_patched = true;
      }
    }
  } catch (err) {
    console.warn('[Remotion] Note: could not apply runtime stability guards:', err.message);
  }
}


const manifestPath = path.resolve(process.argv[2] || path.join(root, 'output', 'semantic_400_rms_titanic_manifest.json'));
const outputPath = path.resolve(process.argv[3] || path.join(root, 'output', 'semantic_400_rms_titanic_remotion.mp4'));

function parseAssetRange(header, fileSize) {
  const match = /^bytes=(\d*)-(\d*)$/i.exec(String(header || '').trim());
  if (!match || (!match[1] && !match[2]) || fileSize <= 0) return null;
  const first = match[1] ? Number(match[1]) : null;
  const last = match[2] ? Number(match[2]) : null;
  if ((first !== null && !Number.isSafeInteger(first)) || (last !== null && !Number.isSafeInteger(last))) return null;
  const start = first === null ? Math.max(0, fileSize - last) : first;
  const end = first === null || last === null ? fileSize - 1 : Math.min(last, fileSize - 1);
  if (start < 0 || start >= fileSize || start > end || (first === null && last === 0)) return null;
  return {start, end};
}

function startLocalAssetServer() {
  return new Promise((resolve, reject) => {
    const mimeTypes = {
      '.mp3': 'audio/mpeg',
      '.wav': 'audio/wav',
      '.m4a': 'audio/mp4',
      '.mp4': 'video/mp4',
      '.webm': 'video/webm',
      '.jpg': 'image/jpeg',
      '.jpeg': 'image/jpeg',
      '.png': 'image/png',
      '.webp': 'image/webp',
      '.ttf': 'font/ttf',
      '.otf': 'font/otf',
      '.woff': 'font/woff',
      '.woff2': 'font/woff2',
      '.json': 'application/json',
    };

    const server = http.createServer((req, res) => {
      if (req.method === 'OPTIONS') {
        res.writeHead(204, {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
          'Access-Control-Allow-Headers': 'Range, Content-Type, Accept',
          'Access-Control-Max-Age': '86400',
        });
        return res.end();
      }

      try {
        let pathname = req.url;
        try {
          const parsed = new URL(req.url, 'http://127.0.0.1');
          pathname = parsed.pathname;
        } catch (_) {}

        const rawPath = decodeURIComponent(pathname.replace(/^\/+/, ''));
        let resolved = path.resolve(rawPath);
        if (!fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) {
          // POSIX absolute paths (e.g. /home/.../narration.mp3) lose their
          // leading slash in the URL; try it back before the project roots.
          // On Windows this is a no-op (path.resolve('/C:/...') == C:\...).
          resolved = path.resolve('/' + rawPath);
        }
        if (!fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) {
          resolved = path.resolve(path.join(root, rawPath));
        }
        if (!fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) {
          resolved = path.resolve(path.join(root, 'public', rawPath));
        }
        if (fs.existsSync(resolved) && fs.statSync(resolved).isFile()) {
          const stat = fs.statSync(resolved);
          const fileSize = stat.size;
          const ext = path.extname(resolved).toLowerCase();
          const contentType = mimeTypes[ext] || 'application/octet-stream';
          const range = req.headers.range;

          res.setHeader('Access-Control-Allow-Origin', '*');
          res.setHeader('Accept-Ranges', 'bytes');

          if (range) {
            const parsedRange = parseAssetRange(range, fileSize);
            if (!parsedRange) {
              res.writeHead(416, {
                'Content-Range': `bytes */${fileSize}`,
                'Content-Type': contentType,
              });
              return res.end();
            }
            const {start, end} = parsedRange;

            const chunksize = (end - start) + 1;
            res.writeHead(206, {
              'Content-Range': `bytes ${start}-${end}/${fileSize}`,
              'Content-Length': chunksize,
              'Content-Type': contentType,
            });

            if (req.method === 'HEAD') {
              return res.end();
            }

            const stream = fs.createReadStream(resolved, { start, end });
            stream.on('error', () => {
              if (!res.headersSent) res.writeHead(500);
              res.end();
            });
            res.on('close', () => {
              stream.destroy();
            });
            stream.pipe(res);
          } else {
            res.writeHead(200, {
              'Content-Length': fileSize,
              'Content-Type': contentType,
            });

            if (req.method === 'HEAD') {
              return res.end();
            }

            const stream = fs.createReadStream(resolved);
            stream.on('error', () => {
              if (!res.headersSent) res.writeHead(500);
              res.end();
            });
            res.on('close', () => {
              stream.destroy();
            });
            stream.pipe(res);
          }
        } else {
          res.writeHead(404, { 'Access-Control-Allow-Origin': '*' });
          res.end('Asset not found');
        }
      } catch (err) {
        if (!res.headersSent) {
          res.writeHead(500, { 'Access-Control-Allow-Origin': '*' });
        }
        res.end(err.message);
      }
    });

    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      const baseUrl = `http://127.0.0.1:${port}`;
      console.log(`[Remotion] Local asset server running at ${baseUrl}`);
      resolve({ server, baseUrl });
    });
  });
}

// Pre-scaled 1080p variants created by advance preparation live next to the
// original as `<stem>.scaled-1080p.<ext>`. Prefer them at compose time: a
// 1080p source needs no downscale in Chromium, which is the render's hot
// path on this VM. Strict no-op when no variant exists — the original is
// used untouched. Images and video only; audio/fonts are never swapped.
const SCALED_1080P_MEDIA_EXTENSIONS = new Set(['.jpg', '.jpeg', '.png', '.webp', '.gif', '.mp4', '.webm', '.mov', '.mkv', '.avi', '.m4v']);

function scaled1080pPathFor(filePath) {
  const ext = path.extname(filePath);
  if (!SCALED_1080P_MEDIA_EXTENSIONS.has(ext.toLowerCase())) return filePath;
  return path.join(path.dirname(filePath), `${path.basename(filePath, ext)}.scaled-1080p${ext}`);
}

function preferScaled1080p(filePath) {
  if (typeof filePath !== 'string' || !filePath) return filePath;
  const scaled = scaled1080pPathFor(filePath);
  if (scaled === filePath) return filePath;
  try {
    if (fs.existsSync(scaled) && fs.statSync(scaled).isFile()) return scaled;
  } catch (_) {}
  return filePath;
}

function asLocalUrl(value, baseUrl) {
  if (typeof value !== 'string') return value;
  let raw = value.trim();
  if (!raw || /^data:/i.test(raw)) return raw;

  const wasFileUrl = /^file:\/\//i.test(raw);
  if (wasFileUrl) {
    try { raw = fileURLToPath(raw); } catch { return ''; }
  }

  // Manifests produced by older runs may contain a URL to the previous
  // per-render asset server, including an encoded absolute Windows path. A
  // saved manifest must be portable across runs, so rebase loopback URLs and
  // leave genuine remote media URLs untouched.
  if (/^https?:\/\//i.test(raw)) {
    try {
      const parsed = new URL(raw);
      const loopback = ['127.0.0.1', 'localhost', '::1'].includes(parsed.hostname.toLowerCase());
      if (!loopback) return raw;
      // Keep the pathname as-is: on POSIX it is an absolute path
      // (/home/...), and stripping its leading slashes would make it
      // unresolvable. Candidates below handle the relative joins.
      raw = decodeURIComponent(parsed.pathname);
    } catch {
      return raw;
    }
  }

  const cleanRelative = raw.replace(/^\/+/, '').replace(/^temp[\\/]temp[\\/]/i, 'temp/').replaceAll('/', path.sep);
  const candidates = [
    raw,
    path.join(root, cleanRelative),
    path.join(root, 'public', cleanRelative),
    ...(/\.(?:ttf|otf|woff2?)$/i.test(raw) ? [path.join(root, 'public', 'font-library', path.basename(raw))] : []),
    path.join(process.resourcesPath || root, cleanRelative),
    path.join(process.resourcesPath || root, 'public', cleanRelative),
  ];

  for (const candidate of candidates) {
    try {
      const resolved = path.resolve(candidate);
      if (fs.existsSync(resolved) && fs.statSync(resolved).isFile()) {
        // Prefer a pre-scaled 1080p sibling when advance preparation made
        // one; otherwise the original resolves exactly as before.
        const normalized = preferScaled1080p(resolved).replace(/\\/g, '/');
        // Strip a POSIX leading slash: `${baseUrl}//home/...` would parse
        // as a protocol-relative URL (host "home") instead of a path.
        return `${baseUrl}/${encodeURI(normalized.replace(/^\/+/, ''))}`;
      }
    } catch {
      // Ignore invalid candidate paths
    }
  }
  // Broken local media must not reach Chromium as a file:/// URL: that turns
  // one lost still/clip/audio file into a failed film render. Blank only
  // explicit file assets here; genuine network URLs returned above are kept.
  const explicitLocalPath = wasFileUrl
    || /^[a-z]:[\\/]/i.test(raw)
    || /^\\\\/.test(raw)
    || /^\/(?:public|temp|output|artifacts|assets|audio|video|images|media|fonts|map-assets|sound-effects|remotion)[\\/]/i.test(raw)
    || /^(?:public|temp|output|artifacts|assets|audio|video|images|media|fonts|map-assets|sound-effects|remotion)[\\/]/i.test(raw)
    || /\.(?:jpe?g|png|webp|gif|mp3|wav|m4a|aac|mp4|webm|mov|mkv|avi|m4v|ttf|otf|woff2?|svg)(?:[?#].*)?$/i.test(raw);
  if (explicitLocalPath) {
    if (!warnedMissingAssets.has(raw)) {
      warnedMissingAssets.add(raw);
      console.warn(`[Remotion] Missing local asset omitted; scene recovery will continue: ${raw}`);
    }
    return '';
  }
  return raw;
}

function recursivelyNormalize(obj, baseUrl) {
  if (!obj) return obj;
  if (typeof obj === 'string') {
    // Only attempt conversion on strings that look like file paths
    if (
      obj.includes('/') ||
      obj.includes('\\') ||
      /\.(jpg|jpeg|png|webp|mp3|wav|m4a|mp4|webm|ttf|otf)$/i.test(obj)
    ) {
      return asLocalUrl(obj, baseUrl);
    }
    return obj;
  }
  if (Array.isArray(obj)) {
    return obj.map((item) => recursivelyNormalize(item, baseUrl));
  }
  if (typeof obj === 'object') {
    const res = {};
    for (const [k, v] of Object.entries(obj)) {
      res[k] = ['evidence_path', 'evidence_dir', 'report_path'].includes(k) ? v : recursivelyNormalize(v, baseUrl);
    }
    // Map textures are composition assets rather than director media. Route
    // them through this render's asset server so a cached Remotion bundle
    // never tries to fetch `/public/...` from the desktop app's origin.
    const mapStyle = String(res.map_render_style || res.map_shot?.render_style || '').toLowerCase();
    if ((res.map_shot?.enabled || mapStyle) && !res.map_background_src) {
      const texture = 'map-assets/nasa-blue-marble-topography-mercator.jpg';
      const resolvedTexture = asLocalUrl(texture, baseUrl);
      if (resolvedTexture && resolvedTexture !== texture) res.map_background_src = resolvedTexture;
    }
    return res;
  }
  return obj;
}

function normalizeManifest(manifest, baseUrl) {
  return recursivelyNormalize(manifest, baseUrl);
}

function compositionSources() {
  const roots = [path.join(root, 'remotion'), path.join(root, 'src', 'remotion')];
  const files = [path.join(root, 'package.json')];
  const accepted = /\.(?:js|jsx|ts|tsx|css|json)$/i;
  const visit = (directory) => {
    if (!fs.existsSync(directory)) return;
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const resolved = path.join(directory, entry.name);
      if (entry.isDirectory()) visit(resolved);
      else if (entry.isFile() && accepted.test(entry.name)) files.push(resolved);
    }
  };
  roots.forEach(visit);
  return [...new Set(files)].filter((file) => fs.existsSync(file)).sort();
}

// Static-import scan of the composition entry's local import graph. The
// directory walk above misses files the composition imports from OUTSIDE
// those roots — DocumentaryComposition.jsx imports ../production-timeline
// (vidnets/src/production-timeline.js) — so a change to such a file would
// leave the bundle key unchanged and serve a stale bundle. The bundle key
// therefore covers the union of the directory walk and this graph. Only
// relative specifiers are followed; package imports are pinned by
// package.json, which is already hashed.
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

function resolveCompositionImportGraph(entryPath, rootDir = root) {
  const checkout = path.resolve(rootDir);
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
      // A local import that escapes the checkout cannot be part of this
      // bundle; anything inside it is hashed even if the directory walk
      // never visits it.
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
    const pkg = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
    const deps = { ...(pkg.dependencies || {}), ...(pkg.devDependencies || {}) };
    return String(deps['@remotion/bundler'] || deps.remotion || 'unknown');
  } catch (_) {}
  return 'unknown';
}

// A cached bundle carries a manifest proving it was built from exactly
// these sources with this Remotion version. Reuse requires the manifest:
// a cache written before manifests existed, or under a different Remotion,
// rebuilds once instead of risking a stale or incompatible bundle.
const BUNDLE_MANIFEST_FILENAME = 'bundle-manifest.json';
const BUNDLE_MANIFEST_VERSION = 1;

function writeBundleManifest(cachedBundlePath, bundleHash) {
  const manifest = {
    version: BUNDLE_MANIFEST_VERSION,
    hash: bundleHash,
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

function cachedBundleIsUsable(cachedBundlePath, bundleHash) {
  if (!fs.existsSync(path.join(cachedBundlePath, 'index.html'))) return false;
  const manifest = readBundleManifest(cachedBundlePath);
  if (!manifest) return false;
  if (manifest.hash !== bundleHash) return false;
  if (manifest.remotionVersion !== remotionVersion()) return false;
  return true;
}

// The renderer needs documentary fonts, sound effects, and any saved Remotion
// media. The web app's large template-animation library is not imported by the
// composition and should not be copied into every render bundle.
function rendererPublicAssets(publicRoot) {
  const includedDirectories = ['font-library', 'sound-effects', 'remotion-manifest-media', 'map-assets'];
  const files = [];
  for (const directory of includedDirectories) {
    const start = path.join(publicRoot, directory);
    if (!fs.existsSync(start)) continue;
    const visit = (current) => {
      for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
        const resolved = path.join(current, entry.name);
        if (entry.isDirectory()) visit(resolved);
        else if (entry.isFile()) files.push({ path: resolved, key: path.relative(publicRoot, resolved).replace(/\\/g, '/') });
      }
    };
    visit(start);
  }
  return files.sort((a, b) => a.key.localeCompare(b.key));
}

function stageRendererPublicAssets(publicRoot, tempRoot) {
  const staged = fs.mkdtempSync(path.join(tempRoot, 'vidzeo-remotion-public-'));
  for (const directory of ['font-library', 'sound-effects', 'remotion-manifest-media', 'map-assets']) {
    const source = path.join(publicRoot, directory);
    if (fs.existsSync(source) && fs.statSync(source).isDirectory()) {
      fs.cpSync(source, path.join(staged, directory), { recursive: true, force: true });
    }
  }
  return staged;
}

function positiveNumber(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

// Free swap (Linux /proc/meminfo, kB) — the render's overflow headroom.
// The gate counts available + free swap as headroom because this VM's
// renders demonstrably complete with swap in use (Wright: 409 MB). Env
// override VIDZEO_REMOTION_SWAP_FREE_MB (tests/direct use) wins; an
// explicit options.swapFreeBytes wins over both. Non-Linux or unreadable
// -> 0, so the gate degrades to available-only instead of guessing.
function readSwapFreeBytes() {
  const raw = process.env.VIDZEO_REMOTION_SWAP_FREE_MB;
  if (raw !== undefined && raw !== '') {
    const parsed = Number(raw);
    if (Number.isFinite(parsed) && parsed >= 0) return parsed * 1024 ** 2;
  }
  try {
    const text = fs.readFileSync('/proc/meminfo', 'utf8');
    const match = /^SwapFree:\s*(\d+)\s*kB/m.exec(text);
    if (match) return Number(match[1]) * 1024;
  } catch (_) {}
  return 0;
}

function adaptiveConcurrency(composition, manifest = {}, { freshMemory = false, swapFreeBytes: swapFreeBytesOpt } = {}) {
  const maximumWorkers = Math.max(1, Math.min(8, Math.floor(positiveNumber(process.env.VIDZEO_REMOTION_MAX_WORKERS, 8))));
  const cpuCount = Math.max(1, Number(os.availableParallelism?.()) || os.cpus().length || 1);
  const cpuLimit = Math.max(1, Math.min(maximumWorkers, cpuCount > 2 ? cpuCount - 1 : cpuCount));
  const totalBytes = Math.max(1, os.totalmem());
  // Flask supplies psutil's fresh system-available value after releasing its
  // own idle working set. Node's freemem() can exclude reclaimable standby
  // cache on Windows, so prefer the supplied available-memory estimate while
  // retaining this fallback for direct script use.
  const availableBytes = Math.max(0, Math.min(
    totalBytes,
    (freshMemory ? os.freemem() : positiveNumber(process.env.VIDZEO_REMOTION_AVAILABLE_RAM_MB, os.freemem() / (1024 ** 2)) * 1024 ** 2),
  ));
  // Keep a dependable reserve for Windows, Flask, Node and the Remotion browser.
  // Estimate per-worker Chromium memory from a 1080p-capable baseline plus
  // decoded RGBA frames, then choose up to eight workers as the available budget
  // allows. This reserve limits parallelism; above the 512 MB admission floor
  // a single worker may still proceed using the OS's paging/reclaim facilities.
  // Steady-state tuning (Nov 16, 2026): the server/daemon RSS is already
  // excluded from "available", so the reserve only covers their growth
  // during a render plus OS headroom — 2% of RAM (128-256 MB), not the old
  // 5-7% (397-555 MB here), which double-counted memory the server
  // already holds and priced every steady-state render out of existence.
  // The Python preflight (render_resource_manager.py) splits this: it
  // keeps a 7% reserve for WORKER SCALING (the pinned scaling curve)
  // and uses the same 2% reserve only for the working-set admission
  // bar, so both gates agree on the admission decision. The JS is the
  // authoritative enforcer at render time and forces serial below 2 GB
  // available regardless of the plan's worker count.
  // Wright/Berlin proved renders complete at 526-615 MB available, and the
  // Remotion 240 MB media-cache floor is kept untouched.
  const reserveBytes = Math.min(256 * 1024 ** 2, Math.max(128 * 1024 ** 2, Math.floor(totalBytes * 0.02)));
  const configuredWorkerMb = positiveNumber(process.env.VIDZEO_REMOTION_MEMORY_PER_WORKER_MB, 192);
  const frameBytes = Math.max(1, Number(composition?.width) || 1920)
    * Math.max(1, Number(composition?.height) || 1080) * 4;
  const lowMemory = availableBytes < 3 * 1024 ** 3;
  const frameBuffers = Math.max(1, Math.min(lowMemory ? 2 : 16, Math.floor(positiveNumber(process.env.VIDZEO_REMOTION_FRAME_BUFFERS, 8))));
  const workerOverheadMb = positiveNumber(process.env.VIDZEO_REMOTION_WORKER_OVERHEAD_MB, 64);
  const workerBytes = configuredWorkerMb * 1024 ** 2 + frameBytes * frameBuffers + workerOverheadMb * 1024 ** 2;
  const sharedBytes = ((lowMemory ? 128 : 256) + 96) * 1024 ** 2;
  const rawMemoryLimit = Math.max(0, Math.floor((availableBytes - reserveBytes - sharedBytes) / (workerBytes + 64 * 1024 ** 2)));
  const singleWorkerFallback = availableBytes >= MIN_REMOTION_AVAILABLE_MB * 1024 ** 2 && rawMemoryLimit < 1;
  const memoryLimit = availableBytes >= MIN_REMOTION_AVAILABLE_MB * 1024 ** 2 ? Math.max(1, rawMemoryLimit) : 0;
  // Requested workers: VIDZEO_REMOTION_CONCURRENCY first, then the M2l
  // worker env (MUSE_RENDER_CONCURRENCY / VIDZEO_REMOTION_WORKERS /
  // MUSE_REMOTION_WORKERS). Default 2 — the tested-safe value on the
  // 4 vCPU VM (Nov 7, 2026: 2 parallel 1080p renders finished in 42s).
  // The memory and CPU caps below auto-degrade any higher request at
  // render start; a request for 5 only runs as 5 when ~2.5 GB is free.
  const requestedWorkers = Math.max(1, Math.min(maximumWorkers, Math.floor(
    positiveNumber(process.env.VIDZEO_REMOTION_CONCURRENCY,
      positiveNumber(process.env.MUSE_RENDER_CONCURRENCY,
        positiveNumber(process.env.VIDZEO_REMOTION_WORKERS,
          positiveNumber(process.env.MUSE_REMOTION_WORKERS, 2)))),
  )));
  if (memoryLimit < 1) {
    const error = new Error(`[Remotion] Waiting for memory: ${Math.round(availableBytes / (1024 ** 2))} MB available; ${MIN_REMOTION_AVAILABLE_MB} MB needed to attempt serial rendering.`);
    error.code = 'VIDZEO_LOW_MEMORY';
    throw error;
  }
  let concurrency = Math.max(1, Math.min(requestedWorkers, cpuLimit, memoryLimit));
  // Low-memory VM guard (Nov 16, 2026): the worker planning estimate was
  // cut so the admission gate can pass at ~1100 MB, but two real Chromium
  // workers need ~2.8 GB, so below 2 GB available a serial render is the
  // only safe choice. Without this, the smaller estimate would admit a
  // second worker at ~1700 MB that the VM cannot back (GTA VI ef306926).
  const encodingConcurrency = concurrency;
  if (availableBytes < 2 * 1024 ** 3) concurrency = 1;
  // The memory estimate is a hard ceiling. Do not force a second worker just
  // because the machine has multiple cores; low-RAM renders stay serial.
  const hardLimit = maximumWorkers;
  const renderBudgetBytes = Math.max(0, availableBytes - reserveBytes);
  const hasVideo = (manifest.scenes || []).some(scene => scene.media?.type === 'video'
    || (scene.media?.shots || []).some(shot => shot.type === 'video' || shot.media_type === 'video'));
  // HD source frames occupy more memory than a low-resolution output frame.
  // Reserve a shared decoder cache before allocating Chromium workers; tiny
  // caches evict freshly decoded frames and cause "No frame found" failures.
  // Keep HD decoding at the production 256 MB budget under pressure. Reduce
  // workers and frame buffers instead of evicting decoded video frames early.
  // Low-memory tuning: same 128 MB shared-decoder budget the code already
  // uses for sharedBytes below 3 GB. 3 video clips do not need the full
  // 256 MB decoder cache; the 240 MB Remotion media-cache floor is separate
  // and is not reduced.
  const decoderCacheBytes = (lowMemory ? 128 : 256) * 1024 ** 2;
  // videoCache is a real 256 MB allocation (offthreadVideoCacheSizeInBytes);
  // the worker-sizing loop below must count it, as disallow_parallel_encoding
  // already does. Audit: installed Remotion 4.0.526 validates
  // offthreadVideoCacheSizeInBytes only as a positive whole number (no
  // minimum), and the fixed 256 MB value already clears the 240 MB floor.
  // Hold it to the same floor so no cache size passed to Remotion can ever
  // silently regress below it.
  const videoCacheMb = Math.max(256, MIN_MEDIA_CACHE_MB);
  if (hasVideo) {
    while (concurrency > 1 && concurrency * workerBytes + decoderCacheBytes + videoCacheMb * 1024 ** 2 + 128 * 1024 ** 2 > renderBudgetBytes) concurrency--;
  }
  // Working-set gate (Nov 16, 2026): the media cache and decoder cache
  // are CONFIG caps, not allocations — Remotion only requires the media
  // cache CONFIG to be >= 240 MB, it does not hold 240 MB resident (a
  // too-small CONFIG is still a render guaranteed to fail validation,
  // ef306926: 64 MB -> "minimum value ... is 240MB (251658240), got:
  // 67108864" at 78%). The old check summed the caps (worker + decoder
  // + 240 floor + margin) and demanded ~1101 MB available on a VM that
  // steadies at 350-650 MB, so no render could ever start. The gate now
  // budgets the resident working set against headroom (available + free
  // swap) and passes the full 256 MB media cache CONFIG to Remotion
  // regardless. Swap is real headroom here: Wright completed with 409 MB
  // of it in use. If the working set does not fit, wait/retry via
  // VIDZEO_LOW_MEMORY instead of launching a doomed render. Completed
  // sections stay saved for resume (see waitForRenderMemory). The
  // per-section gate is this same function, so the start and section
  // thresholds cannot drift apart.
  const swapFreeBytes = swapFreeBytesOpt !== undefined
    ? Math.max(0, Number(swapFreeBytesOpt) || 0)
    : readSwapFreeBytes();
  const headroomBytes = availableBytes + swapFreeBytes;
  const workingSetBytes = reserveBytes
    + WORKER_WORKING_SET_MB * 1024 ** 2
    + CACHE_WORKING_SET_MB * 1024 ** 2;
  if (headroomBytes < workingSetBytes) {
    const error = new Error(`[Remotion] Waiting for memory: ${Math.round(availableBytes / (1024 ** 2))} MB available + ${Math.round(swapFreeBytes / (1024 ** 2))} MB swap free; a serial render needs ${Math.round(workingSetBytes / (1024 ** 2))} MB of headroom for one worker (${WORKER_WORKING_SET_MB} MB), the working-set caches (${CACHE_WORKING_SET_MB} MB) and the reserve (${Math.round(reserveBytes / (1024 ** 2))} MB).`);
    error.code = 'VIDZEO_LOW_MEMORY';
    throw error;
  }
  // The media cache CONFIG is always the full 256 MB cap: the gate above
  // already proved the working set fits, and a smaller CONFIG would only
  // reintroduce the <240 MB validation failure. Never below the floor.
  const mediaCacheBytes = 256 * 1024 ** 2;
  const offthreadVideoThreads = lowMemory ? 1 : Math.max(1, Math.min(4, Math.floor(cpuLimit / 2), concurrency));
  return {
    concurrency,
    cpu_limit: cpuLimit,
    memory_limit: memoryLimit,
    raw_memory_limit: rawMemoryLimit,
    single_worker_fallback: singleWorkerFallback,
    minimum_available_memory_mb: MIN_REMOTION_AVAILABLE_MB,
    // Headroom bar, not an available-RAM bar: reserve + worker working
    // set + cache working set (867 MB on this 7.7 GB VM). A render is
    // admitted when available + free swap reaches it (and available
    // clears the 512 MB floor).
    preferred_available_memory_mb: Math.ceil(workingSetBytes / (1024 ** 2)),
    cap: hardLimit,
    available_memory_mb: Math.round(availableBytes / (1024 ** 2)),
    swap_free_mb: Math.round(swapFreeBytes / (1024 ** 2)),
    headroom_mb: Math.round(headroomBytes / (1024 ** 2)),
    worker_working_set_mb: WORKER_WORKING_SET_MB,
    cache_working_set_mb: CACHE_WORKING_SET_MB,
    reserve_memory_mb: Math.round(reserveBytes / (1024 ** 2)),
    estimated_worker_memory_mb: Math.round(workerBytes / (1024 ** 2)),
    frame_buffers: frameBuffers,
    worker_overhead_mb: Math.round(workerOverheadMb),
    media_cache_bytes: mediaCacheBytes,
    offthread_video_threads: offthreadVideoThreads,
    video_cache_bytes: videoCacheMb * 1024 ** 2,
    disallow_parallel_encoding: cpuLimit < 3 ||
      renderBudgetBytes - encodingConcurrency * workerBytes - videoCacheMb * 1024 ** 2 - mediaCacheBytes - 96 * 1024 ** 2 < 384 * 1024 ** 2,
    browser_heap_mb: singleWorkerFallback ? 256 : (lowMemory ? 512 : 1024),
  };
}

async function waitForRenderMemory(composition, manifest, options = {}) {
  const waitMs = Math.min(1200000, positiveNumber(options.memoryWaitMs,
    positiveNumber(process.env.VIDZEO_REMOTION_MEMORY_WAIT_SECONDS, 600) * 1000));
  const deadline = Date.now() + waitMs;
  for (;;) {
    try { return adaptiveConcurrency(composition, manifest, options); }
    catch (error) {
      if (error.code !== 'VIDZEO_LOW_MEMORY') throw error;
      if (Date.now() >= deadline) {
        error.message += ' Memory wait deadline reached; completed render sections are saved for resume.';
        error.retryable = true;
        throw error;
      }
      reportStage('waiting for memory', null, error.message);
      // Selection and previous sections can leave our own Chromium resident.
      // Release it before waiting, otherwise its memory can prevent the next
      // admission indefinitely. The next section opens a fresh owned browser.
      if (typeof options.releaseOwnedResources === 'function') {
        const release = options.releaseOwnedResources;
        options = {...options, releaseOwnedResources:undefined, freshMemory:true};
        await release();
        continue;
      }
      if (global.gc) { try { global.gc(); } catch {} }
      await new Promise(resolve => setTimeout(resolve, Math.min(2000, Math.max(1, deadline - Date.now()))));
      options = { ...options, freshMemory: true };
    }
  }
}

function loadAssetDigestCache(cachePath) {
  try {
    const parsed = JSON.parse(fs.readFileSync(cachePath, 'utf8'));
    return parsed?.version === 1 && parsed?.files && typeof parsed.files === 'object'
      ? parsed.files
      : {};
  } catch {
    return {};
  }
}

function fileStatFingerprint(stat) {
  return [stat.size, stat.mtimeMs, stat.ctimeMs, stat.ino || 0].join(':');
}

async function sha256File(filePath) {
  const hash = crypto.createHash('sha256');
  for await (const chunk of fs.createReadStream(filePath)) hash.update(chunk);
  return hash.digest('hex');
}

async function getAssetDigest(filePath, cache) {
  const absolutePath = path.resolve(filePath);
  const stat = fs.statSync(absolutePath);
  const fingerprint = fileStatFingerprint(stat);
  const entry = cache[absolutePath];
  if (entry?.fingerprint === fingerprint && /^[a-f0-9]{64}$/i.test(entry.sha256 || '')) {
    return { digest: entry.sha256, cacheHit: true, fingerprint };
  }
  const digest = await sha256File(absolutePath);
  cache[absolutePath] = { fingerprint, sha256: digest };
  return { digest, cacheHit: false, fingerprint };
}

function writeAssetDigestCache(cachePath, files) {
  const parent = path.dirname(cachePath);
  const tempPath = `${cachePath}.${process.pid}.${crypto.randomBytes(4).toString('hex')}.tmp`;
  try {
    fs.mkdirSync(parent, { recursive: true });
    fs.writeFileSync(tempPath, JSON.stringify({ version: 1, files }), 'utf8');
    fs.renameSync(tempPath, cachePath);
  } catch (error) {
    try { fs.rmSync(tempPath, { force: true }); } catch (_) {}
    // Cache write failures must never block a render; the next render will hash
    // these files again and still produce a content-derived bundle key.
    console.warn(`[Remotion] SHA-256 cache could not be saved: ${error.message}`);
  }
}

function promoteBundleToCache(serveUrl, cachedBundlePath, bundleHash) {
  fs.mkdirSync(path.dirname(cachedBundlePath), { recursive: true });
  if (fs.existsSync(path.join(cachedBundlePath, 'index.html'))) {
    // A concurrent render installed this bundle first; make sure it carries
    // a manifest for this hash so the next render can reuse it. Never fatal.
    try {
      if (!cachedBundleIsUsable(cachedBundlePath, bundleHash)) writeBundleManifest(cachedBundlePath, bundleHash);
    } catch (_) {}
    return cachedBundlePath;
  }
  if (fs.existsSync(cachedBundlePath)) fs.rmSync(cachedBundlePath, { recursive: true, force: true });

  const stagedCachePath = `${cachedBundlePath}.tmp-${process.pid}-${crypto.randomBytes(4).toString('hex')}`;
  try {
    try {
      // Remotion and the cache both live under the system temp directory on a
      // normal install, so a rename avoids copying the entire webpack output.
      fs.renameSync(serveUrl, stagedCachePath);
    } catch (error) {
      if (error.code !== 'EXDEV') throw error;
      fs.cpSync(serveUrl, stagedCachePath, { recursive: true });
    }
    try {
      fs.renameSync(stagedCachePath, cachedBundlePath);
    } catch (error) {
      // A concurrent Flask render may have installed the same immutable bundle.
      if (!fs.existsSync(path.join(cachedBundlePath, 'index.html'))) throw error;
      fs.rmSync(stagedCachePath, { recursive: true, force: true });
    }
  } catch (error) {
    fs.rmSync(stagedCachePath, { recursive: true, force: true });
    if (error.code === 'EEXIST' && fs.existsSync(path.join(cachedBundlePath, 'index.html'))) {
      try {
        if (!cachedBundleIsUsable(cachedBundlePath, bundleHash)) writeBundleManifest(cachedBundlePath, bundleHash);
      } catch (_) {}
      return cachedBundlePath;
    }
    throw error;
  }
  // Stamp the manifest proving this bundle matches these sources; a
  // manifest write failure must never block the render — the next render
  // simply rebuilds instead of reusing.
  try {
    writeBundleManifest(cachedBundlePath, bundleHash);
  } catch (error) {
    console.warn(`[Remotion] Bundle manifest could not be saved: ${error.message}`);
  }
  return cachedBundlePath;
}

// Bound disposable build output without touching cached media, source assets,
// or user exports. Only direct generated JS/map/HTML leaves are removed.
function pruneGeneratedBundleFiles(cacheDir, protectedBundlePath, keep = 3) {
  if (!fs.existsSync(cacheDir)) return { files: 0, bytes: 0 };
  const root = path.resolve(cacheDir);
  const protectedPath = path.resolve(protectedBundlePath);
  const candidates = fs.readdirSync(root, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && /^bundle-[a-f0-9]{20}$/.test(entry.name))
    .map((entry) => ({ dir: path.join(root, entry.name), stat: fs.statSync(path.join(root, entry.name)) }))
    .filter((entry) => fs.existsSync(path.join(entry.dir, 'index.html')))
    .sort((a, b) => b.stat.mtimeMs - a.stat.mtimeMs);
  const retained = new Set([protectedPath, ...candidates.slice(0, Math.max(1, keep)).map((entry) => entry.dir)]);
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const marker = entry.isFile() && entry.name.match(/^active-([a-f0-9]{20})-(\d+)\.json$/);
    if (!marker) continue;
    try { process.kill(Number(marker[2]), 0); retained.add(path.join(root, `bundle-${marker[1]}`)); }
    catch (error) { if (error.code === 'EPERM') retained.add(path.join(root, `bundle-${marker[1]}`)); }
  }
  let files = 0;
  let bytes = 0;
  for (const { dir, stat } of candidates) {
    // An overlapping preview may still be serving a recently built bundle.
    if (retained.has(dir) || Date.now() - stat.mtimeMs < 15 * 60 * 1000) continue;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (!entry.isFile() || !(entry.name === 'index.html' || /\.(?:js|map)$/.test(entry.name))) continue;
      const target = path.resolve(dir, entry.name);
      if (path.dirname(target) !== dir) continue;
      try {
        const size = fs.statSync(target).size;
        fs.unlinkSync(target);
        files += 1;
        bytes += size;
      } catch (_) { /* Busy files can be reclaimed by a later run. */ }
    }
  }
  return { files, bytes };
}

function reportStage(stage, percent = null, detail = '') {
  const safeStage = String(stage || '').trim().toLowerCase().replace(/[^a-z0-9 -]/g, '');
  const hasPercent = percent !== null && percent !== undefined && String(percent).trim() !== '' && Number.isFinite(Number(percent));
  const suffix = hasPercent ? ` ${Math.max(0, Math.min(100, Math.round(Number(percent))))}%` : '';
  const extra = String(detail || '').replace(/[\r\n\[\]]/g, ' ').trim();
  console.log(`[Remotion] Stage: ${safeStage}${suffix}${extra ? ` — ${extra}` : ''}`);
}

async function acquireRenderLease(port = 47639, { waitMs = 0, retryMs = 1000, onWait = null } = {}) {
  const deadline = Date.now() + Math.max(0, Number(waitMs) || 0);
  for (;;) {
    const lease = net.createServer();
    try {
      await new Promise((resolve, reject) => {
        lease.once('error', reject);
        lease.listen({ host: '127.0.0.1', port, exclusive: true }, resolve);
      });
      return lease;
    } catch (cause) {
      if (cause.code === 'EADDRINUSE' && Date.now() < deadline) {
        if (onWait) onWait();
        await new Promise(resolve => setTimeout(resolve, Math.max(10, Math.min(retryMs, deadline - Date.now()))));
        continue;
      }
      const error = new Error(cause.code === 'EADDRINUSE' ? '[Remotion] Another video render is active. Keep this job queued and retry after it finishes.' : `[Remotion] Cannot acquire render slot: ${cause.message}`);
      error.code = cause.code === 'EADDRINUSE' ? 'VIDZEO_RENDER_BUSY' : 'VIDZEO_RENDER_LOCK_ERROR';
      throw error;
    }
  }
}

async function main() {
  let lastWaitNotice = 0;
  const lease = await acquireRenderLease(47639, {
    waitMs: Math.min(1200000, positiveNumber(process.env.VIDZEO_REMOTION_RENDER_WAIT_MS, 120000)),
    onWait: () => {
      if (Date.now() - lastWaitNotice >= 5000) {
        reportStage('waiting for render slot', null, 'another render owns the slot; this job remains queued');
        lastWaitNotice = Date.now();
      }
    },
  });
  try { await renderManifest(); } finally { lease.close(); }
}

async function renderManifest() {
  // Policy/lease callers must not load Webpack and Chromium dependencies.
  // Initialize the heavy renderer only after this job owns the render lease.
  installRemotionStabilityGuards();
  const { bundle } = require('@remotion/bundler');
  const { renderMedia, renderStill, selectComposition, openBrowser } = require('@remotion/renderer');
  const browserSession = renderBrowserSession(openBrowser);
  const { server, baseUrl } = await startLocalAssetServer();
  let bundleDir = null;
  let activeBundleMarker = null;
  let stagedPublicDir = null;
  try {
    reportStage('preparing renderer');
    // Render mode: kill disposable processes (test runners, stray Chrome /
    // ffmpeg, dockerd) BEFORE the first memory measurement, so the freed
    // RAM improves the concurrency plan. Never fatal: a guard failure
    // must not break the render.
    try {
      const renderMode = require('./render-mode');
      const renderModeReport = await renderMode.enterRenderMode();
      if (renderModeReport.enabled) {
        console.log(`[RenderMode] freed ~${renderModeReport.freedMB} MB (available ${renderModeReport.memBeforeMB} -> ${renderModeReport.memAfterMB} MB); killed ${renderModeReport.killed.length} process(es)`);
      } else {
        console.log('[RenderMode] disabled (VIDZEO_RENDER_MODE=0)');
      }
    } catch (error) {
      console.log(`[RenderMode] unavailable, continuing without it: ${error.message}`);
    }
    const payloadText = fs.readFileSync(manifestPath, 'utf8').replace(/^\uFEFF/, '');
    const payload = JSON.parse(payloadText);
    const rawManifest = payload.semantic_edit_manifest || payload.remotion_manifest || payload.manifest || payload;
    const manifest = normalizeManifest(rawManifest, baseUrl);
    // Fresh read: the Flask env snapshot predates render-mode's kills, so it
    // understates post-guard headroom; section re-plans already use freshMemory.
    const initialResources = await waitForRenderMemory({ width: manifest.width, height: manifest.height }, manifest, { freshMemory: true });

    fs.mkdirSync(path.dirname(outputPath), { recursive: true });

    const bundleCacheDir = path.join(os.tmpdir(), 'vidzeo-remotion-bundle-cache');
    const assetDigestCachePath = path.join(bundleCacheDir, 'asset-sha256-cache.json');
    const entryPath = path.join(root, 'remotion', 'entry.jsx');
    const publicRoot = fs.existsSync(path.join(root, 'public'))
      ? path.join(root, 'public')
      : path.join(process.resourcesPath || root, 'public');
    const publicAssets = rendererPublicAssets(publicRoot);
    // Cache by the actual bytes in the full import/data graph. Timestamps alone
    // can collide after restores, copies, or coarse-resolution file systems.
    const hashStartedAt = Date.now();
    const hasher = crypto.createHash('sha256');
    for (const sourcePath of compositionSources()) {
      hasher.update(path.relative(root, sourcePath).replace(/\\/g, '/'));
      hasher.update(fs.readFileSync(sourcePath));
    }
    // Files the entry imports from outside the walked roots
    // (../production-timeline): without them a source change would leave
    // the key unchanged and serve a stale bundle.
    for (const sourcePath of resolveCompositionImportGraph(entryPath)) {
      hasher.update(path.relative(root, sourcePath).replace(/\\/g, '/'));
      hasher.update(fs.readFileSync(sourcePath));
    }
    const assetDigestCache = loadAssetDigestCache(assetDigestCachePath);
    let assetHashCacheHits = 0;
    for (const asset of publicAssets) {
      hasher.update(`public/${asset.key}`);
      const { digest, cacheHit } = await getAssetDigest(asset.path, assetDigestCache);
      hasher.update(digest);
      if (cacheHit) assetHashCacheHits += 1;
    }
    writeAssetDigestCache(assetDigestCachePath, assetDigestCache);
    const bundleHash = hasher.digest('hex').slice(0, 20);
    const cachedBundlePath = path.join(bundleCacheDir, `bundle-${bundleHash}`);
    fs.mkdirSync(bundleCacheDir, { recursive: true });
    activeBundleMarker = path.join(bundleCacheDir, `active-${bundleHash}-${process.pid}.json`);
    fs.writeFileSync(activeBundleMarker, JSON.stringify({ pid: process.pid, created_at: Date.now() }));
    const reclaimed = pruneGeneratedBundleFiles(bundleCacheDir, cachedBundlePath);
    if (reclaimed.files) console.log(`[Remotion] Reclaimed ${Math.round(reclaimed.bytes / 1048576)}MB of obsolete generated build files; cached media preserved.`);
    if (typeof fs.statfsSync === 'function') {
      let availableDiskMB = null;
      try { const disk = fs.statfsSync(path.dirname(outputPath)); availableDiskMB = disk.bavail * disk.bsize / 1048576; } catch (_) {}
      const diskFloorMB = Math.max(128, Number(process.env.VIDZEO_REMOTION_MIN_FREE_DISK_MB || 512));
      if (availableDiskMB !== null && availableDiskMB < diskFloorMB) {
        throw new Error(`Insufficient free disk space for Remotion (${Math.round(availableDiskMB)}MB available; ${diskFloorMB}MB required). Free disk space and retry the saved edit plan.`);
      }
    }
    console.log(`[Remotion] Verified ${publicAssets.length} bundled assets with SHA-256 in ${((Date.now() - hashStartedAt) / 1000).toFixed(2)}s (${assetHashCacheHits} cached).`);

    let serveUrl;
    if (cachedBundleIsUsable(cachedBundlePath, bundleHash)) {
      console.log(`[Remotion] Reusing cached bundle: ${bundleHash} (manifest-verified)`);
      reportStage('bundling composition', 100, 'cached');
      serveUrl = cachedBundlePath;
      const cachedPublic = path.join(cachedBundlePath, 'public');
      for (const dir of ['font-library', 'sound-effects', 'remotion-manifest-media', 'map-assets']) {
        const src = path.join(publicRoot, dir);
        const dest = path.join(cachedPublic, dir);
        if (fs.existsSync(src) && !fs.existsSync(dest)) {
          try { fs.cpSync(src, dest, { recursive: true }); } catch (_) {}
        }
      }
    } else {
      console.log('[Remotion] Bundling composition...');
      reportStage('bundling composition', 0);
      stagedPublicDir = stageRendererPublicAssets(publicRoot, os.tmpdir());
      let lastBundlePercent = -1;
      serveUrl = await bundle({
        entryPoint: entryPath,
        rootDir: root,
        publicDir: stagedPublicDir,
        rspack: true,
        onProgress: (progress) => {
          const percent = Math.max(0, Math.min(100, Math.floor(Number(progress) * 100)));
          if (percent !== lastBundlePercent) {
            lastBundlePercent = percent;
            reportStage('bundling composition', percent);
          }
        },
        onDirectoryCreated: (dir) => { bundleDir = dir; },
        webpackOverride: (config) => config,
      });
      serveUrl = promoteBundleToCache(serveUrl, cachedBundlePath, bundleHash);
    }

    // Measure actual font-loaded text in every production preview, not only
    // hand-authored review fixtures. Final video exports do not run this hook.
    if (process.argv.includes('--review-frames')) manifest.review_layout = true;
    const inputProps = { manifest };
    console.log('[Remotion] Selecting composition...');
    reportStage('selecting composition');
    const selectionChromiumOptions = {
      gl: 'angle', disableWebSecurity: true, ignoreCertificateErrors: true,
      args: ['--disable-dev-shm-usage','--no-sandbox','--disable-background-timer-throttling',
             '--disable-backgrounding-occluded-windows','--disable-renderer-backgrounding',
             `--js-flags=--max-old-space-size=${initialResources.browser_heap_mb}`,
             '--disable-breakpad','--disable-component-update'],
    };
    const productionBrowser = process.argv.includes('--review-frames') ? undefined
      : await browserSession.get({chromiumOptions:selectionChromiumOptions});
    const composition = await selectComposition({
      serveUrl,
      id: 'VidzeoDocumentary',
      inputProps,
      timeoutInMilliseconds: 90000,
      puppeteerInstance: productionBrowser,
      chromiumOptions: {
        gl: 'angle',
        disableWebSecurity: true,
        ignoreCertificateErrors: true,
        args: [
          '--disable-dev-shm-usage',
          '--no-sandbox',
          '--disable-background-timer-throttling',
          '--disable-backgrounding-occluded-windows',
          '--disable-renderer-backgrounding',
        ],
      },
    });
    const scale = Math.max(0.1, Math.min(2, Number(process.env.VIDZEO_REMOTION_SCALE || 1)));
    const concurrencyPlan = await waitForRenderMemory(composition, manifest, {
      freshMemory: true, releaseOwnedResources:() => browserSession.close(),
    });
    const concurrency = concurrencyPlan.concurrency;
    const timeoutInMilliseconds = Math.max(30000, positiveNumber(process.env.VIDZEO_REMOTION_FRAME_TIMEOUT_MS, 90000));
    if (process.argv.includes('--review-frames')) {
      // Tiny diagnostic exports were halved again, leaving the visual critic
      // only ~200px-wide evidence. Keep review frames readable while bounding
      // normal HD review cost; never upscale beyond the requested composition.
      const reviewScale = Math.min(1, Math.max(.5, 640 / Math.max(1, composition.width)));
      const requested = [...new Set((rawManifest.review_frames || [0, Math.round(composition.durationInFrames / 2), composition.durationInFrames - 1])
        .map(Number).filter((n)=>Number.isInteger(n) && n>=0 && n<composition.durationInFrames))];
      const frames = requested;
      const directory = path.join(path.dirname(outputPath), 'review-frames');
      fs.mkdirSync(directory, { recursive: true });
      const rendered = new Array(frames.length);
      const layoutAudit = [];
      const browser = await openBrowser('chrome', {
        forceDeviceScaleFactor: reviewScale,
        chromiumOptions: {
          gl: 'angle',
          disableWebSecurity: true,
          ignoreCertificateErrors: true,
          args: [
            '--disable-dev-shm-usage',
            '--no-sandbox',
            '--disable-background-timer-throttling',
            '--disable-backgrounding-occluded-windows',
            '--disable-renderer-backgrounding',
            `--js-flags=--max-old-space-size=${concurrencyPlan.browser_heap_mb}`,
            '--disable-breakpad',
          ],
        },
      });
      let nextFrameIndex = 0;
      let completedFrames = 0;
      const reviewWorkers = Math.max(1, Math.min(2, concurrency, frames.length));
      try {
        const results = await Promise.allSettled(Array.from({ length: reviewWorkers }, async () => {
          while (nextFrameIndex < frames.length) {
            const index = nextFrameIndex++;
            const frame = frames[index];
            const output = path.join(directory, `frame-${String(frame).padStart(6,'0')}.png`);
            await renderStill({ composition, serveUrl, inputProps, output, frame, scale: reviewScale, imageFormat: 'png', timeoutInMilliseconds,
              onBrowserLog: (log) => {
                const message = String(log.text || '');
                const marker = 'VIDZEO_LAYOUT_AUDIT ';
                if (message.startsWith(marker)) {
                  try { layoutAudit.push(JSON.parse(message.slice(marker.length))); } catch (_) {}
                }
              },
              puppeteerInstance: browser,
              offthreadVideoCacheSizeInBytes: concurrencyPlan.video_cache_bytes,
              mediaCacheSizeInBytes: concurrencyPlan.media_cache_bytes,
              offthreadVideoThreads: concurrencyPlan.offthread_video_threads });
            rendered[index] = { frame, path: output };
            completedFrames++;
            reportStage('review frames', completedFrames / frames.length * 100);
          }
        }));
        const failed = results.find(result => result.status === 'rejected');
        if (failed) throw failed.reason;
      } finally { await browser.close({ silent: true }); }
      const layoutFailures = layoutAudit.flatMap(row => (row.failures || []).map(failure => ({ frame: row.frame, ...failure })));
      fs.writeFileSync(outputPath, JSON.stringify({ mode: 'frame_review', frames: rendered, coverage: 'complete', omitted_frames: [], layout_audit: layoutAudit, layout_failures: layoutFailures }, null, 2));
      if (layoutFailures.length) throw new Error(`Documentary text layout review found ${layoutFailures.length} overflow(s); see ${outputPath}`);
      return;
    }
    const videoCacheBytes = concurrencyPlan.video_cache_bytes;
    const x264Preset = String(process.env.VIDZEO_REMOTION_X264_PRESET || 'ultrafast').trim().toLowerCase();
    const allowedX264Presets = new Set(['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow', 'placebo']);
    const selectedX264Preset = allowedX264Presets.has(x264Preset) ? x264Preset : 'veryfast';
    const requestedCrf = Number(process.env.VIDZEO_REMOTION_CRF || 18);
    const selectedCrf = Number.isInteger(requestedCrf) && requestedCrf >= 0 && requestedCrf <= 51
      ? requestedCrf
      : 18;
    let frameRange = null;
    if (process.env.VIDZEO_REMOTION_FRAME_RANGE) {
      try {
        const raw = process.env.VIDZEO_REMOTION_FRAME_RANGE.trim();
        if (raw.startsWith('[') && raw.endsWith(']')) {
          frameRange = JSON.parse(raw);
        } else if (raw.includes('-')) {
          frameRange = raw.split('-').map((v) => parseInt(v.trim(), 10));
        }
        if (Array.isArray(frameRange) && frameRange.length === 2 && Number.isFinite(frameRange[0]) && Number.isFinite(frameRange[1])) {
          console.log(`[Remotion] Custom frame range active: [${frameRange[0]}, ${frameRange[1]}]`);
        } else {
          frameRange = null;
        }
      } catch (_) {
        frameRange = null;
      }
    }
    let lastLoggedPercent = -1;

    console.log(`[Remotion] Rendering ${composition.durationInFrames} frames (${composition.fps} fps, adaptive concurrency=${concurrency}/${concurrencyPlan.cap} workers, cpuLimit=${concurrencyPlan.cpu_limit}, memoryFit=${concurrencyPlan.memory_limit}, availableMemory=${concurrencyPlan.available_memory_mb}MB, reserve=${concurrencyPlan.reserve_memory_mb}MB, estimatedMemoryPerWorker=${concurrencyPlan.estimated_worker_memory_mb}MB, x264=${selectedX264Preset}, crf=${selectedCrf}, mediaCache=${Math.round(concurrencyPlan.media_cache_bytes / (1024 ** 2))}MB, videoCache=${Math.round(videoCacheBytes / (1024 ** 2))}MB, offthreadVideoThreads=${concurrencyPlan.offthread_video_threads}, timeout=${timeoutInMilliseconds}ms) to ${outputPath}...`);
    reportStage('launching render browser', null, `${composition.durationInFrames} frames`);
    const stagedOutput = path.join(path.dirname(outputPath), `.${path.basename(outputPath, path.extname(outputPath))}.rendering-${process.pid}${path.extname(outputPath)}`);
    const renderOptions = {
      composition,
      serveUrl,
      codec: 'h264',
      x264Preset: selectedX264Preset,
      crf: selectedCrf,
      imageFormat: 'jpeg',
      logLevel: 'error',
      outputLocation: stagedOutput,
      inputProps,
      audioCodec: 'aac',
      muted: false,
      concurrency,
      scale,
      timeoutInMilliseconds,
      frameRange: frameRange || undefined,
      chromiumOptions: {
        gl: 'angle',
        ignoreCertificateErrors: true,
        disableWebSecurity: true,
        args: [
          '--disable-dev-shm-usage',
          '--no-sandbox',
          '--disable-background-timer-throttling',
          '--disable-backgrounding-occluded-windows',
          '--disable-renderer-backgrounding',
          `--js-flags=--max-old-space-size=${concurrencyPlan.browser_heap_mb}`,
          '--disable-breakpad',
          '--disable-component-update',
        ],
      },
      offthreadVideoCacheSizeInBytes: videoCacheBytes,
      mediaCacheSizeInBytes: concurrencyPlan.media_cache_bytes,
      offthreadVideoThreads: concurrencyPlan.offthread_video_threads,
      disallowParallelEncoding: concurrencyPlan.disallow_parallel_encoding,
      onStart: ({ frameCount, resolvedConcurrency }) => {
        reportStage('rendering frames', null, `${frameCount} frames, ${resolvedConcurrency} workers`);
      },
      onProgress: ({ progress, renderedFrames, encodedFrames }) => {
        const pct = Math.round(progress * 100);
        if (pct !== lastLoggedPercent) {
          lastLoggedPercent = pct;
          process.stdout.write(`[Remotion] Progress: ${pct}%\n`);
        }
        if (Date.now() - lastActivityNotice >= 10000) {
          console.log(`[Remotion] Activity: rendered=${renderedFrames} encoded=${encodedFrames}`);
          lastActivityNotice = Date.now();
        }
      },
    };
    let lastActivityNotice = 0;
    try {
      const rangeStart = frameRange ? frameRange[0] : 0;
      const rangeEnd = frameRange ? frameRange[1] : composition.durationInFrames - 1;
      const totalFrames = rangeEnd - rangeStart + 1;
      if (!Number.isInteger(rangeStart) || !Number.isInteger(rangeEnd) || rangeStart < 0 || rangeEnd < rangeStart || rangeEnd >= composition.durationInFrames) {
        throw new Error('Invalid Remotion export frame range');
      }
      const chunkSeconds = Math.max(15, Math.min(60, positiveNumber(process.env.VIDZEO_REMOTION_CHUNK_SECONDS, 30)));
      const framesPerChunk = Math.max(1, Math.round(chunkSeconds * composition.fps));
      const checkpointHasher = renderSectionHasher(payloadText, bundleHash,
        { scale, selectedCrf, selectedX264Preset, frameRange, framesPerChunk });
      // Asset changes invalidate previous sections, even when their paths are unchanged.
      const addAssetVersions = value => {
        if (typeof value === 'string' && fs.existsSync(value) && fs.statSync(value).isFile()) {
          const stat = fs.statSync(value);
          checkpointHasher.update(`${value}:${stat.size}:${stat.mtimeMs}`);
        } else if (Array.isArray(value)) value.forEach(addAssetVersions);
        else if (value && typeof value === 'object') Object.values(value).forEach(addAssetVersions);
      };
      addAssetVersions(rawManifest);
      const checkpointDir = path.join(path.dirname(outputPath), `.${path.basename(outputPath)}.render-sections`, checkpointHasher.digest('hex').slice(0, 24));
      fs.mkdirSync(checkpointDir, { recursive: true });
      const exportReceipt = path.join(checkpointDir, 'export.json');
      let completed = null;
      try { completed = JSON.parse(fs.readFileSync(exportReceipt, 'utf8')); } catch (_) {}
      if (completed?.version === 1 && completed.frames === totalFrames
          && fs.existsSync(outputPath) && completed.sha256 === await sha256File(outputPath)) {
        console.log('[Remotion] Reusing fingerprint-matched completed export');
        return;
      }
      const videoFiles = [];
      const audioFiles = [];
      for (let start = rangeStart, index = 0; start <= rangeEnd; start += framesPerChunk, index++) {
        const end = Math.min(rangeEnd, start + framesPerChunk - 1);
        const chunkFrames = end - start + 1;
        const video = path.join(checkpointDir, `section-${index}.mp4`);
        const audio = path.join(checkpointDir, `section-${index}.wav`);
        const marker = path.join(checkpointDir, `section-${index}.json`);
        let saved = null;
        try { saved = JSON.parse(fs.readFileSync(marker, 'utf8')); } catch (_) {}
        const reusable = saved && saved.start === start && saved.end === end
          && fs.existsSync(video) && fs.existsSync(audio)
          && saved.videoHash === await sha256File(video) && saved.audioHash === await sha256File(audio);
        if (reusable) {
          console.log(`[Remotion] Reusing completed section ${index + 1}: frames ${start}-${end}`);
        } else {
          for (let attempt = 0; ; attempt++) {
            // Re-shed before each section: memory can degrade mid-render
            // (leaked Chrome, stray processes) after the one guard at start.
            // Never fatal: a guard failure must not break the render.
            try {
              const renderMode = require('./render-mode');
              const sectionModeReport = await renderMode.enterRenderMode();
              if (sectionModeReport.enabled && sectionModeReport.killed.length) {
                console.log(`[RenderMode] section ${index + 1}: freed ~${sectionModeReport.freedMB} MB; killed ${sectionModeReport.killed.length} process(es)`);
              }
            } catch (error) {
              console.log(`[RenderMode] unavailable, continuing without it: ${error.message}`);
            }
            const plan = await waitForRenderMemory(composition, manifest, {
              freshMemory: true, releaseOwnedResources:() => browserSession.close(),
            });
            const workers = attempt ? 1 : plan.concurrency;
            if (typeof fs.statfsSync === 'function') {
              const temporaryBytes = plan.disallow_parallel_encoding || attempt > 0
                ? composition.width * composition.height * scale * scale * chunkFrames * 0.4
                : 128 * 1024 ** 2;
              for (const directory of new Set([checkpointDir, os.tmpdir()])) {
                let disk = null;
                try { disk = fs.statfsSync(directory); } catch (_) {}
                if (disk && disk.bavail * disk.bsize < temporaryBytes + 256 * 1024 ** 2) {
                  throw new Error('Not enough temporary disk space for the next render section; completed sections are preserved for retry');
                }
              }
            }
            reportStage('rendering frames', null, `section ${index + 1}, ${workers} workers, ${plan.available_memory_mb} MB available`);
            console.log(`[Remotion] Section resources: workers=${workers}, videoCache=${Math.round(plan.video_cache_bytes / 1024 ** 2)}MB, decoderThreads=${plan.offthread_video_threads}, separateEncoding=${plan.disallow_parallel_encoding}`);
            for (const file of [video, audio, marker]) if (fs.existsSync(file)) fs.unlinkSync(file);
            try {
              const sectionStartedAt = Date.now();
              let renderedDoneIn = null, encodedDoneIn = null;
              const rendered = await renderMedia({ ...renderOptions, outputLocation: video, separateAudioTo: audio,
                puppeteerInstance: await browserSession.get({chromiumOptions:renderOptions.chromiumOptions}),
                audioCodec: 'pcm-16', enforceAudioTrack: true, sampleRate: 48000,
                frameRange: [start, end], concurrency: workers,
                timeoutInMilliseconds: Math.max(timeoutInMilliseconds, plan.disallow_parallel_encoding ? 300000 : 90000),
                disallowParallelEncoding: attempt > 0 || plan.disallow_parallel_encoding,
                offthreadVideoCacheSizeInBytes: plan.video_cache_bytes,
                mediaCacheSizeInBytes: plan.media_cache_bytes,
                offthreadVideoThreads: attempt ? 1 : plan.offthread_video_threads,
                chromiumOptions: { ...renderOptions.chromiumOptions,
                  args: renderOptions.chromiumOptions.args.filter(arg => !arg.startsWith('--js-flags='))
                    .concat(`--js-flags=--max-old-space-size=${plan.browser_heap_mb}`) },
                onProgress: ({ progress, renderedFrames, encodedFrames, renderedDoneIn: frameMs, encodedDoneIn: encodeMs }) => {
                  if (frameMs !== null && frameMs !== undefined) renderedDoneIn = frameMs;
                  if (encodeMs !== null && encodeMs !== undefined) encodedDoneIn = encodeMs;
                  const pct = Math.floor(((start - rangeStart + progress * chunkFrames) / totalFrames) * 95);
                  if (pct !== lastLoggedPercent) { console.log(`[Remotion] Progress: ${pct}%`); lastLoggedPercent = pct; }
                  if (Date.now() - lastActivityNotice >= 10000) {
                    console.log(`[Remotion] Activity: rendered=${start - rangeStart + renderedFrames} encoded=${start - rangeStart + encodedFrames}`);
                    lastActivityNotice = Date.now();
                  }
                },
              });
              const timing = {section:index+1, frames:chunkFrames, workers,
                total_ms:Date.now()-sectionStartedAt, render_ms:renderedDoneIn, encode_ms:encodedDoneIn,
                slowest_frames:(rendered.slowestFrames || []).slice(0,3)};
              console.log(`[Remotion] Section timing: ${JSON.stringify(timing)}`);
              if (!fs.existsSync(video) || fs.statSync(video).size < 4096 || !fs.existsSync(audio) || fs.statSync(audio).size < 44) {
                throw new Error('Render section output missing or empty');
              }
              const tempMarker = `${marker}.tmp`;
              fs.writeFileSync(tempMarker, JSON.stringify({ start, end, fps: composition.fps, frames: chunkFrames,
                workers, resource_plan: plan, audio_format: 'pcm-16', sample_rate: 48000,
                videoHash: await sha256File(video), audioHash: await sha256File(audio), timing }));
              fs.renameSync(tempMarker, marker);
              break;
            } catch (error) {
              const resourceError = /out of memory|oom|target closed|browser.*closed|page crashed|ENOMEM|cannot allocate memory/i.test(String(error.stack || error));
              if (!resourceError) throw error;
              if (attempt >= 2) { error.message = 'Attempt limit reached: ' + error.message; throw error; }
              // A crashed browser is replaced; completed sections stay saved.
              await browserSession.close();
              reportStage('waiting for memory', null, `section ${index + 1} resource recovery; retrying with one worker`);
              await new Promise(resolve => setTimeout(resolve, 5000));
            }
          }
        }
        videoFiles.push(video); audioFiles.push(audio);
        console.log(`[Remotion] Progress: ${Math.floor((end - rangeStart + 1) / totalFrames * 95)}%`);
      }
      reportStage('finalizing render output', null, 'joining saved video sections and continuous audio');
      if (typeof fs.statfsSync === 'function') {
        let disk = null;
        try { disk = fs.statfsSync(path.dirname(outputPath)); } catch (_) {}
        const outputReserve = videoFiles.reduce((sum, file) => sum + fs.statSync(file).size, 256 * 1024 ** 2);
        if (disk && disk.bavail * disk.bsize < outputReserve) {
          throw new Error('Not enough disk space to join the completed render sections; all sections are preserved for retry');
        }
      }
      const joined = await joinRemotionSections({outputPath:stagedOutput, videoFiles, audioFiles,
        fps:composition.fps, frames:totalFrames,
        width:Math.round(composition.width * scale), height:Math.round(composition.height * scale)});
    if (!fs.existsSync(stagedOutput) || fs.statSync(stagedOutput).size < 4096) {
      throw new Error('Remotion produced an empty staged export');
    }
    if (outputPath !== stagedOutput) {
      await promoteExport(stagedOutput, outputPath);
    }
    const receiptTemp = `${exportReceipt}.${process.pid}.tmp`;
    fs.writeFileSync(receiptTemp, JSON.stringify({version:1, ...joined, sha256:await sha256File(outputPath)}));
    await promoteExport(receiptTemp, exportReceipt);
    console.log('[Remotion] Progress: 100%');
    } finally {
      if (fs.existsSync(stagedOutput)) {
        try { fs.unlinkSync(stagedOutput); } catch (_) {}
      }
    }
    reportStage('finalizing render output');
    console.log(`[Remotion] Manifest render complete: ${outputPath}`);
  } finally {
    try {
      await browserSession.close();
    } finally {
    server.close();
    if (activeBundleMarker) { try { fs.unlinkSync(activeBundleMarker); } catch (_) {} }
    if (stagedPublicDir && fs.existsSync(stagedPublicDir)) {
      try { fs.rmSync(stagedPublicDir, { recursive: true, force: true }); } catch (_) {}
    }
    if (bundleDir && fs.existsSync(bundleDir)) {
      try {
        fs.rmSync(bundleDir, { recursive: true, force: true });
      } catch (_) {}
    }
    }
  }
}

if (require.main === module) {
  main().catch((error) => {
    console.error('[Remotion] Manifest render failed:', error);
    console.error(`[Remotion] Failure: ${JSON.stringify({code:error.code || 'VIDZEO_RENDER_FAILED', message:error.message})}`);
    process.exitCode = 1;
  });
}

module.exports = { startLocalAssetServer, parseAssetRange, renderBrowserSession, renderSectionHasher, adaptiveConcurrency, waitForRenderMemory, stageRendererPublicAssets, ensureFfmpegOutputDirectory, pruneGeneratedBundleFiles, acquireRenderLease, asLocalUrl, normalizeManifest, finalizeMp4Audio, resolveCompositionImportGraph, remotionVersion, readBundleManifest, writeBundleManifest, cachedBundleIsUsable, scaled1080pPathFor, preferScaled1080p };
