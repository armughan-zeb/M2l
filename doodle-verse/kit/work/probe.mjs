// node work/probe.mjs "<js expression run after window.ready>"  -> prints the result (debug helper)
import { chromium } from 'playwright';
import http from 'http';
import fs from 'fs';
import path from 'path';

function resolveChromiumExecutable() {
  const candidates = [
    process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
    process.env.MUSE_CHROME_PATH,
    process.env.PUPPETEER_EXECUTABLE_PATH,
    '/opt/chrome-headless-shell-linux64/chrome-headless-shell',
    '/home/hatch/chrome-headless-shell-linux64/chrome-headless-shell',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/usr/bin/google-chrome',
  ].map((item) => String(item || '').trim()).filter(Boolean);
  for (const candidate of candidates) {
    try {
      if (fs.existsSync(candidate)) return candidate;
    } catch { /* ignore */ }
  }
  return undefined;
}

function shouldUseSwiftShader() {
  if (process.env.SWIFTSHADER === '1' || process.env.MUSE_DOODLE_SWIFTSHADER === '1') return true;
  if (process.env.SWIFTSHADER === '0' || process.env.MUSE_DOODLE_GPU === '1') return false;
  if (process.platform === 'linux') {
    try {
      if (!fs.existsSync('/dev/dri/renderD128')) return true;
    } catch {
      return true;
    }
  }
  return false;
}

const root = path.resolve('web');
const types = { '.js': 'text/javascript', '.html': 'text/html' };
const srv = http.createServer((q, r) => {
  const f = path.join(root, decodeURIComponent(q.url.split('?')[0]));
  fs.readFile(f, (e, d) => {
    if (e) { r.writeHead(404); r.end(); return; }
    r.writeHead(200, { 'Content-Type': types[path.extname(f)] || 'application/octet-stream' });
    r.end(d);
  });
}).listen(0);

const args = shouldUseSwiftShader()
  ? ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--no-sandbox', '--disable-dev-shm-usage']
  : ['--ignore-gpu-blocklist', '--enable-gpu', '--no-sandbox', '--disable-dev-shm-usage'];
const executablePath = resolveChromiumExecutable();
const b = await chromium.launch({ ...(executablePath ? { executablePath } : {}), headless: true, args });
const pg = await b.newPage({ viewport: { width: 1920, height: 1080 } });
pg.on('pageerror', (e) => console.log('PAGEERR', e.message));
await pg.goto(`http://localhost:${srv.address().port}/index.html`);
await pg.waitForFunction('window.ready', null, { timeout: 120000 });
console.log(JSON.stringify(await pg.evaluate(process.argv[2])));
await b.close();
srv.close();
