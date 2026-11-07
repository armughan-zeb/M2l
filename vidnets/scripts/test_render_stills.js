const path = require('path');
const fs = require('fs');
const http = require('http');
const os = require('os');
const { bundle } = require('@remotion/bundler');
const { renderStill, selectComposition } = require('@remotion/renderer');

const root = path.resolve(__dirname, '..');
const manifestPath = path.resolve(
  path.join(root, 'output', 'Grand Theft Auto VI_20260926_233400_ce1a3004', 'Grand Theft Auto VI_remotion_edit_plan.json')
);
const outDir = path.resolve(path.join(root, 'verification_output'));
fs.mkdirSync(outDir, { recursive: true });

function startLocalAssetServer() {
  return new Promise((resolve) => {
    const mimeTypes = {
      '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.mp4': 'video/mp4',
      '.webm': 'video/webm', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
      '.png': 'image/png', '.webp': 'image/webp', '.ttf': 'font/ttf',
    };
    const server = http.createServer((req, res) => {
      res.setHeader('Access-Control-Allow-Origin', '*');
      const rawPath = decodeURIComponent(req.url.replace(/^\/+/, ''));
      let resolved = path.resolve(rawPath);
      if (!fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) {
        resolved = path.resolve(path.join(root, rawPath));
      }
      if (fs.existsSync(resolved) && fs.statSync(resolved).isFile()) {
        const ext = path.extname(resolved).toLowerCase();
        res.writeHead(200, { 'Content-Type': mimeTypes[ext] || 'application/octet-stream' });
        fs.createReadStream(resolved).pipe(res);
      } else {
        res.writeHead(404);
        res.end('Not found');
      }
    });
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      resolve({ server, baseUrl: `http://127.0.0.1:${port}` });
    });
  });
}

function asLocalUrl(value, baseUrl) {
  if (typeof value !== 'string') return value;
  const raw = value.trim();
  if (!raw || /^data:/i.test(raw) || /^http/i.test(raw)) return raw;
  const resolved = path.resolve(raw);
  if (fs.existsSync(resolved) && fs.statSync(resolved).isFile()) {
    return `${baseUrl}/${encodeURI(resolved.replace(/\\/g, '/'))}`;
  }
  return raw;
}

function recursivelyNormalize(obj, baseUrl) {
  if (!obj) return obj;
  if (typeof obj === 'string') {
    if (obj.includes('/') || obj.includes('\\') || /\.(jpg|jpeg|png|webp|mp3|wav|mp4)$/i.test(obj)) {
      return asLocalUrl(obj, baseUrl);
    }
    return obj;
  }
  if (Array.isArray(obj)) return obj.map((item) => recursivelyNormalize(item, baseUrl));
  if (typeof obj === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(obj)) out[k] = recursivelyNormalize(v, baseUrl);
    return out;
  }
  return obj;
}

async function main() {
  const { server, baseUrl } = await startLocalAssetServer();
  try {
    const manifest = recursivelyNormalize(JSON.parse(fs.readFileSync(manifestPath, 'utf8')), baseUrl);
    const entryPath = path.join(root, 'remotion', 'entry.jsx');

    console.log('[TestRender] Bundling Remotion composition...');
    const serveUrl = await bundle({
      entryPoint: entryPath,
      webpackOverride: (config) => config,
    });

    const inputProps = { manifest };
    console.log('[TestRender] Selecting composition...');
    const composition = await selectComposition({ serveUrl, id: 'VidzeoDocumentary', inputProps });

    // Frames to test:
    // Scene 0: at frame 60 (2.0s) -> ChapterTitle
    // Scene 4: at frame 690 (23.0s) -> SmartVideo (previously pip_inset)
    // Scene 5: at frame 930 (31.0s) -> SmartVideo (previously split_duo)
    const testFrames = [
      { frame: 60, name: 'frame_060_scene0_chapter.png' },
      { frame: 250, name: 'frame_250_kinetic.png' },
      { frame: 560, name: 'frame_560_kinetic.png' },
      { frame: 690, name: 'frame_690_scene4_video.png' },
      { frame: 930, name: 'frame_930_scene5_video.png' },
    ];

    for (const item of testFrames) {
      const outPath = path.join(outDir, item.name);
      console.log(`[TestRender] Rendering frame ${item.frame} to ${item.name}...`);
      await renderStill({
        composition,
        serveUrl,
        output: outPath,
        inputProps,
        frame: item.frame,
        scale: 0.5,
        chromiumOptions: {
          gl: 'angle',
          ignoreCertificateErrors: true,
          disableWebSecurity: true,
          args: ['--no-sandbox', '--disable-dev-shm-usage'],
        },
      });
      console.log(`[TestRender] Done: ${outPath} (${fs.statSync(outPath).size} bytes)`);
    }
  } finally {
    server.close();
  }
}

main().catch((err) => {
  console.error('[TestRender] Failed:', err);
  process.exit(1);
});
