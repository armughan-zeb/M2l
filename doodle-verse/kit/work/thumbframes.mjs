// node work/thumbframes.mjs  -> thumb/bg_A.jpg, thumb/bg_B.jpg (story frames re-shot with thumbnail cameras)
import { chromium } from 'playwright';
import http from 'http'; import fs from 'fs'; import path from 'path';
const root = path.resolve('web');
const types = { '.js': 'text/javascript', '.html': 'text/html' };
const srv = http.createServer((q, r) => { const f = path.join(root, decodeURIComponent(q.url.split('?')[0])); fs.readFile(f, (e, d) => { if (e) { r.writeHead(404); r.end(); return; } r.writeHead(200, { 'Content-Type': types[path.extname(f)] || 'application/octet-stream' }); r.end(d); }); }).listen(0);
const b = await chromium.launch({ args: ['--ignore-gpu-blocklist', '--enable-gpu'] });
const pg = await b.newPage({ viewport: { width: 1920, height: 1080 } });
pg.on('pageerror', (e) => console.log('PAGEERR', e.message));
await pg.goto(`http://localhost:${srv.address().port}/index.html`);
await pg.waitForFunction('window.ready', null, { timeout: 120000 });
const SHOTS = {
  A: { f: 1256, cam: { pos: [-0.25, 1.25, 2.55], look: [-0.42, 1.42, 0.3], fov: 36, roll: 0 } },  // tear + photo, room above for text
  B: { f: 205, cam: { pos: [1.55, 1.1, 3.0], look: [0.05, 1.5, 0.3], fov: 38, roll: 0 } },         // Leo with Maya's memory on the bench
};
for (const [k, s] of Object.entries(SHOTS)) {
  const data = await pg.evaluate(({ f, cam }) => { window.camOverride = cam; window.renderFrame(f); window.renderFrame(f); return document.querySelector('canvas').toDataURL('image/jpeg', 0.95); }, s);
  fs.writeFileSync(`thumb/bg_${k}.jpg`, Buffer.from(data.split(',')[1], 'base64'));
  console.log('wrote', k);
}
await b.close(); srv.close();
