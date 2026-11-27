// node thumbnail.mjs spec.json  -> 1080x1920 Shorts thumbnail (JPG)
// spec: { bg, out, zoom?, focus?: [x,y] (bg px), lines: [{text, color?, size?}], top?,
//         arrow?: {from:[x,y], to:[x,y], bend?}, ring?: {x,y,r}, badge?: "text" }
// Arrow/ring coordinates are in the 1080x1920 output. Lines auto-shrink to fit 980 px.
// Keep text in the top ~45 %: Shorts covers the bottom with the title and the right edge with buttons.
import { chromium } from 'playwright';
import fs from 'fs'; import path from 'path';
const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
// optional: w/h for other canvases (e.g. a 1640x624 cover), x + textW to centre the text block elsewhere, vig: false
const W = spec.w || 1920, H = spec.h || 1080, dir = path.dirname(path.resolve(process.argv[2]));
const TW = spec.textW || W - 100, TX = spec.x ?? W / 2;
const bg = fs.readFileSync(path.resolve(dir, spec.bg)).toString('base64');
const z = spec.zoom || 1, [fx, fy] = spec.focus || [W / 2, H / 2];
const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');

const lines = spec.lines.map(l => `<div class="ln" style="color:${l.color || '#fff'};font-size:${l.size || 150}px;${l.stroke ? `-webkit-text-stroke:${l.stroke}px #000;` : ''}">${esc(l.text)}</div>`).join('');
let svg = '';
if (spec.ring) { const { x, y, r } = spec.ring;
  svg += `<circle cx="${x}" cy="${y}" r="${r}" fill="none" stroke="#000" stroke-width="26"/><circle cx="${x}" cy="${y}" r="${r}" fill="none" stroke="#FF2A2A" stroke-width="14"/>`; }
if (spec.arrow) { const { from: [x1, y1], to: [x2, y2], bend = 0.25 } = spec.arrow;
  const mx = (x1 + x2) / 2 - (y2 - y1) * bend, my = (y1 + y2) / 2 + (x2 - x1) * bend;
  const a = Math.atan2(y2 - my, x2 - mx), L = 70, s = 0.5;
  const head = `${x2},${y2} ${x2 - L * Math.cos(a - s)},${y2 - L * Math.sin(a - s)} ${x2 - L * Math.cos(a + s)},${y2 - L * Math.sin(a + s)}`;
  const ex = x2 - 40 * Math.cos(a), ey = y2 - 40 * Math.sin(a);
  for (const [c, w] of [['#000', 40], ['#FF2A2A', 24]]) svg +=
    `<path d="M${x1},${y1} Q${mx},${my} ${ex},${ey}" fill="none" stroke="${c}" stroke-width="${w}" stroke-linecap="round"/>` +
    `<polygon points="${head}" fill="${c}" stroke="${c}" stroke-width="${w - 18}" stroke-linejoin="round"/>`; }

const html = `<!doctype html><html><head><style>
html,body{margin:0;width:${W}px;height:${H}px;overflow:hidden;background:#000}
.bg{position:absolute;inset:0;background:url(data:image/jpeg;base64,${bg}) no-repeat;background-size:${W * z}px ${H * z}px;
  background-position:${W / 2 - fx * z}px ${H / 2 - fy * z}px;filter:saturate(1.3) contrast(1.1) brightness(1.04)}
.vig{position:absolute;inset:0;background:radial-gradient(ellipse 75% 60% at 50% 45%,transparent 55%,rgba(0,0,0,.55) 100%),
  linear-gradient(180deg,rgba(0,0,0,.55) 0%,rgba(0,0,0,0) 32%)}
svg{position:absolute;inset:0}
.txt{position:absolute;left:${TX - TW / 2}px;width:${TW}px;top:${spec.top ?? 110}px;text-align:center}
${spec.vig === false ? '.vig{display:none}' : ''}
.ln{display:table;margin:0 auto;font-family:'Arial Black',Impact,sans-serif;font-weight:900;line-height:1.02;letter-spacing:-1px;white-space:nowrap;
  -webkit-text-stroke:18px #000;paint-order:stroke fill;text-shadow:0 10px 0 rgba(0,0,0,.55),0 0 40px rgba(0,0,0,.5);transform:rotate(-2deg)}
.badge{display:inline-block;margin-bottom:18px;padding:10px 26px;background:#FF2A2A;color:#fff;border:6px solid #000;border-radius:16px;
  font:900 54px 'Arial Black',sans-serif;transform:rotate(-3deg);box-shadow:0 8px 0 rgba(0,0,0,.5)}
</style></head><body><div class="bg"></div><div class="vig"></div><svg width="${W}" height="${H}">${svg}</svg>
<div class="txt">${spec.badge ? `<div class="badge">${esc(spec.badge)}</div>` : ''}${lines}</div>
<script>for(const e of document.querySelectorAll('.ln')){let s=parseFloat(e.style.fontSize);while(e.scrollWidth>${TW}&&s>40){s-=4;e.style.fontSize=s+'px'}}</script>
</body></html>`;

const b = await chromium.launch();
const pg = await b.newPage({ viewport: { width: W, height: H } });
await pg.setContent(html); await pg.evaluate(() => document.fonts.ready);
await pg.screenshot({ path: path.resolve(dir, spec.out), type: 'jpeg', quality: 92 });
await b.close();
console.log('wrote', path.resolve(dir, spec.out));
