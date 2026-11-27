// Painted canvas textures: flat colour, dark seams, a little hand-made noise.
const rnd = (s) => { const x = Math.sin(s * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };
export const shade = (hex, k) => {
  const n = parseInt(hex.slice(1), 16), c = (v) => Math.max(0, Math.min(255, Math.round(v * k))).toString(16).padStart(2, '0');
  return '#' + c((n >> 16) & 255) + c((n >> 8) & 255) + c(n & 255);
};
export const cv = (w, h) => { const c = document.createElement('canvas'); c.width = w; c.height = h; return c; };
const speck = (g, w, h, n, col, a, r0 = 1, r1 = 3, seed = 1) => {
  g.fillStyle = col; g.globalAlpha = a;
  for (let i = 0; i < n; i++) { g.beginPath(); g.arc(rnd(seed + i) * w, rnd(seed + i * 1.7 + 9) * h, r0 + rnd(seed + i * 3.1) * (r1 - r0), 0, 7); g.fill(); }
  g.globalAlpha = 1;
};

/** vertical or horizontal planks */
export function planks(base, { n = 6, vertical = true, seam = 0.55, seed = 1, worn = 0 } = {}) {
  const c = cv(256, 256), g = c.getContext('2d');
  const s = 256 / n;
  for (let i = 0; i < n; i++) {
    g.fillStyle = shade(base, 0.9 + 0.2 * rnd(seed + i));
    if (vertical) g.fillRect(i * s, 0, s, 256); else g.fillRect(0, i * s, 256, s);
    // grain streaks
    g.strokeStyle = shade(base, 0.78); g.lineWidth = 1.5; g.globalAlpha = 0.45;
    for (let k = 0; k < 3; k++) {
      const o = (0.2 + 0.6 * rnd(seed + i * 7 + k)) * s;
      g.beginPath();
      if (vertical) { g.moveTo(i * s + o, 0); g.bezierCurveTo(i * s + o + 4, 80, i * s + o - 4, 170, i * s + o, 256); }
      else { g.moveTo(0, i * s + o); g.bezierCurveTo(80, i * s + o + 4, 170, i * s + o - 4, 256, i * s + o); }
      g.stroke();
    }
    g.globalAlpha = 1;
    // nail heads
    g.fillStyle = shade(base, 0.45);
    for (const t of [0.12, 0.88]) {
      if (vertical) { g.fillRect(i * s + s / 2 - 2, t * 256 - 2, 4, 4); } else { g.fillRect(t * 256 - 2, i * s + s / 2 - 2, 4, 4); }
    }
  }
  g.fillStyle = shade(base, seam);
  for (let i = 0; i <= n; i++) { if (vertical) g.fillRect(i * s - 2, 0, 4, 256); else g.fillRect(0, i * s - 2, 256, 4); }
  if (worn) speck(g, 256, 256, 60, shade(base, 0.6), worn, 2, 6, seed);
  return c;
}
export function dirt(base = '#c49a64', seed = 3) {
  const c = cv(512, 512), g = c.getContext('2d');
  g.fillStyle = base; g.fillRect(0, 0, 512, 512);
  speck(g, 512, 512, 220, shade(base, 0.85), 0.5, 4, 14, seed);
  speck(g, 512, 512, 160, shade(base, 1.12), 0.45, 3, 10, seed + 50);
  speck(g, 512, 512, 90, shade(base, 0.62), 0.8, 2, 4, seed + 90); // pebbles
  // wheel ruts
  g.strokeStyle = shade(base, 0.8); g.lineWidth = 10; g.globalAlpha = 0.4;
  for (const x of [150, 362]) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x, 512); g.stroke(); }
  g.globalAlpha = 1;
  return c;
}
export function grass(base = '#7fae4c', seed = 5) {
  const c = cv(512, 512), g = c.getContext('2d');
  g.fillStyle = base; g.fillRect(0, 0, 512, 512);
  speck(g, 512, 512, 260, shade(base, 0.86), 0.55, 6, 18, seed);
  speck(g, 512, 512, 160, shade(base, 1.12), 0.5, 4, 12, seed + 33);
  g.strokeStyle = shade(base, 0.7); g.lineWidth = 2;
  for (let i = 0; i < 260; i++) { const x = rnd(seed + i * 2.3) * 512, y = rnd(seed + i * 5.9) * 512; g.beginPath(); g.moveTo(x, y); g.lineTo(x + 3 - rnd(i) * 6, y - 9); g.stroke(); }
  return c;
}
export function shingles(base = '#7a4a3a', seed = 7) {
  const c = cv(256, 256), g = c.getContext('2d');
  g.fillStyle = base; g.fillRect(0, 0, 256, 256);
  const rows = 8, h = 256 / rows;
  for (let r = 0; r < rows; r++) {
    const off = (r % 2) * 16;
    for (let x = -32; x < 288; x += 32) {
      g.fillStyle = shade(base, 0.88 + 0.22 * rnd(seed + r * 13 + x));
      g.fillRect(x + off + 2, r * h + 2, 28, h - 2);
    }
    g.fillStyle = shade(base, 0.5); g.fillRect(0, r * h + h - 3, 256, 3);
  }
  return c;
}
export function stone(base = '#9a948a', seed = 9) {
  const c = cv(256, 256), g = c.getContext('2d');
  g.fillStyle = shade(base, 0.6); g.fillRect(0, 0, 256, 256);
  const rows = 6, h = 256 / rows;
  for (let r = 0; r < rows; r++) {
    let x = -(r % 2) * 24;
    while (x < 256) {
      const w = 34 + 30 * rnd(seed + r * 9 + x);
      g.fillStyle = shade(base, 0.85 + 0.25 * rnd(seed + x + r));
      g.beginPath(); g.roundRect(x + 3, r * h + 3, w - 6, h - 6, 6); g.fill();
      x += w;
    }
  }
  return c;
}
export function plaster(base = '#efe3c8', seed = 11) {
  const c = cv(256, 256), g = c.getContext('2d');
  g.fillStyle = base; g.fillRect(0, 0, 256, 256);
  speck(g, 256, 256, 80, shade(base, 0.92), 0.5, 6, 20, seed);
  return c;
}
/** painted sign board with text (reads left-to-right on the +z face) */
export function sign(text, { bg = '#2f4a6a', fg = '#f6e7c1', w = 1024, h = 256, font = 'bold', size = 0.62, border = '#e8c45a', sub = null, serif = true, rot = 0, seed = 2 } = {}) {
  const c = cv(w, h), g = c.getContext('2d');
  g.fillStyle = bg; g.fillRect(0, 0, w, h);
  // wood grain on the board
  g.strokeStyle = shade(bg, 0.85); g.lineWidth = 3; g.globalAlpha = 0.5;
  for (let i = 0; i < 8; i++) { const y = (i + 0.5) * h / 8; g.beginPath(); g.moveTo(0, y); g.bezierCurveTo(w * 0.3, y + 6, w * 0.6, y - 6, w, y); g.stroke(); }
  g.globalAlpha = 1;
  if (border) { g.strokeStyle = border; g.lineWidth = h * 0.05; g.strokeRect(h * 0.06, h * 0.06, w - h * 0.12, h - h * 0.12); }
  g.fillStyle = fg; g.textAlign = 'center'; g.textBaseline = 'middle';
  const fam = serif ? 'Georgia, "DejaVu Serif", serif' : '"DejaVu Sans", Arial, sans-serif';
  let fs = h * size * (sub ? 0.78 : 1);
  g.font = `${font} ${fs}px ${fam}`;
  while (g.measureText(text).width > w * 0.88) { fs *= 0.94; g.font = `${font} ${fs}px ${fam}`; }
  g.save(); g.translate(w / 2, sub ? h * 0.4 : h * 0.53); g.rotate(rot);
  g.lineWidth = fs * 0.08; g.strokeStyle = shade(bg, 0.45); g.strokeText(text, 0, 0);
  g.fillText(text, 0, 0); g.restore();
  if (sub) { let s2 = h * 0.2; g.font = `bold ${s2}px ${fam}`; while (g.measureText(sub).width > w * 0.85) { s2 *= 0.94; g.font = `bold ${s2}px ${fam}`; } g.fillText(sub, w / 2, h * 0.78); }
  return c;
}
/** newspaper page */
export function newspaper(head = 'TOMATO PILLS!', sub = 'CURE ALL ILLS', seed = 1) {
  const c = cv(512, 680), g = c.getContext('2d');
  g.fillStyle = '#efe6cf'; g.fillRect(0, 0, 512, 680);
  g.fillStyle = '#2a2420'; g.textAlign = 'center';
  g.font = 'bold 38px Georgia, "DejaVu Serif", serif'; g.fillText('THE OHIO GAZETTE', 256, 56);
  g.fillRect(24, 72, 464, 4); g.fillRect(24, 80, 464, 1.5);
  let fs = 86; g.font = `bold ${fs}px Georgia, "DejaVu Serif", serif`;
  while (g.measureText(head).width > 470) { fs -= 3; g.font = `bold ${fs}px Georgia, "DejaVu Serif", serif`; }
  g.fillStyle = '#b8231c'; g.fillText(head, 256, 170);
  g.fillStyle = '#2a2420'; g.font = 'bold 40px Georgia, "DejaVu Serif", serif'; g.fillText(sub, 256, 228);
  // tomato woodcut
  g.fillStyle = '#d23a2a'; g.beginPath(); g.ellipse(150, 360, 80, 70, 0, 0, 7); g.fill();
  g.lineWidth = 6; g.strokeStyle = '#2a2420'; g.stroke();
  g.fillStyle = '#3f8a3a'; for (let i = 0; i < 5; i++) { g.beginPath(); g.ellipse(150 + Math.cos(i * 1.26) * 22, 295 + Math.sin(i * 1.26) * 8, 22, 8, i * 1.26, 0, 7); g.fill(); }
  // text columns
  g.fillStyle = '#6b6258';
  for (let col = 0; col < 2; col++) for (let i = 0; i < 9; i++) g.fillRect(col ? 260 : 250, 290 + i * 26 - (col ? 0 : 999), col ? 220 : 0, 9);
  for (let col = 0; col < 3; col++) for (let i = 0; i < 6; i++) g.fillRect(28 + col * 158, 470 + i * 30, 140 - (i === 5 ? 50 : 0), 10);
  return c;
}
/** paper poster on a wall */
export function poster(title = 'TOMATO PILLS', sub = 'CURES ALL!', { bg = '#f3e2b5', fg = '#b8231c' } = {}) {
  const c = cv(384, 512), g = c.getContext('2d');
  g.fillStyle = bg; g.fillRect(0, 0, 384, 512);
  g.strokeStyle = '#2a2420'; g.lineWidth = 8; g.strokeRect(14, 14, 356, 484);
  g.textAlign = 'center'; g.fillStyle = fg;
  const words = title.split(' ');
  words.forEach((wd, i) => { let fs = 74; g.font = `bold ${fs}px Georgia, "DejaVu Serif", serif`; while (g.measureText(wd).width > 330) { fs -= 3; g.font = `bold ${fs}px Georgia, serif`; } g.fillText(wd, 192, 100 + i * 78); });
  g.fillStyle = '#d23a2a'; g.beginPath(); g.ellipse(192, 330, 70, 62, 0, 0, 7); g.fill(); g.lineWidth = 6; g.strokeStyle = '#2a2420'; g.stroke();
  g.fillStyle = '#3f8a3a'; g.beginPath(); g.ellipse(192, 272, 34, 10, 0, 0, 7); g.fill();
  g.fillStyle = '#2a2420'; g.font = 'bold 44px Georgia, "DejaVu Serif", serif'; g.fillText(sub, 192, 460);
  return c;
}
/** bottle label */
export function label(text, { bg = '#f6ecd2', fg = '#b8231c', w = 512, h = 256 } = {}) {
  const c = cv(w, h), g = c.getContext('2d');
  g.fillStyle = bg; g.fillRect(0, 0, w, h);
  g.strokeStyle = '#2a2420'; g.lineWidth = 8; g.strokeRect(10, 10, w - 20, h - 20);
  g.fillStyle = fg; g.textAlign = 'center'; g.textBaseline = 'middle';
  const lines = text.split('\n');
  lines.forEach((ln, i) => { let fs = h * 0.8 / lines.length; g.font = `bold ${fs}px Georgia, "DejaVu Serif", serif`; while (g.measureText(ln).width > w * 0.84) { fs *= 0.94; g.font = `bold ${fs}px Georgia, serif`; } g.fillText(ln, w / 2, h * (i + 0.5) / lines.length + 4); });
  return c;
}
/** lake water: flat blue with lighter wavelets and dark troughs */
export function water(base = '#4f8fc2', seed = 13) {
  const c = cv(512, 512), g = c.getContext('2d');
  g.fillStyle = base; g.fillRect(0, 0, 512, 512);
  g.lineCap = 'round';
  for (let i = 0; i < 120; i++) {
    const x = rnd(seed + i) * 512, y = rnd(seed + i * 2.7) * 512, w = 14 + rnd(seed + i * 4.1) * 36;
    g.strokeStyle = i % 3 ? shade(base, 1.22) : shade(base, 0.82); g.lineWidth = 3 + (i % 2) * 2;
    g.beginPath(); g.moveTo(x - w / 2, y); g.quadraticCurveTo(x, y - 5, x + w / 2, y); g.stroke();
  }
  return c;
}
/** paved footpath: rounded stones with mortar */
export function paving(base = '#cdbb98', seed = 15) {
  const c = cv(256, 256), g = c.getContext('2d');
  g.fillStyle = shade(base, 0.7); g.fillRect(0, 0, 256, 256);
  for (let r = 0; r < 4; r++) for (let k = 0; k < 4; k++) {
    const x = k * 64 + (r % 2) * 32, y = r * 64;
    g.fillStyle = shade(base, 0.9 + 0.2 * rnd(seed + r * 5 + k));
    for (const dx of [0, -256]) { g.beginPath(); g.roundRect(x + dx + 4, y + 4, 56, 56, 10); g.fill(); }
  }
  return c;
}
/** polaroid of the couple; v picks the scene */
export function photo(v = 0) {
  const c = cv(256, 300), g = c.getContext('2d');
  g.fillStyle = '#fbf7ee'; g.fillRect(0, 0, 256, 300);
  const scenes = [['#9fd0e8', '#7fae4c'], ['#ffc68a', '#4f8fc2'], ['#c9b8f0', '#f3d9a6'], ['#ffd9a0', '#7fae4c'], ['#9fd0e8', '#f3d9a6'], ['#f6b8c8', '#7fae4c']][v % 6];
  g.fillStyle = scenes[0]; g.fillRect(16, 16, 224, 210);
  g.fillStyle = scenes[1]; g.fillRect(16, 170, 224, 56);
  const head = (x, y, skin, hair, body, long) => {
    g.fillStyle = body; g.beginPath(); g.ellipse(x, y + 62, 30, 36, 0, 0, 7); g.fill();
    if (long) { g.fillStyle = hair; g.beginPath(); g.ellipse(x, y + 10, 32, 40, 0, 0, 7); g.fill(); }
    g.fillStyle = skin; g.beginPath(); g.ellipse(x, y, 28, 26, 0, 0, 7); g.fill();
    g.fillStyle = hair; g.beginPath(); g.ellipse(x, y - 10, 29, 17, 0, Math.PI, 0); g.fill();
    g.strokeStyle = '#241726'; g.lineWidth = 4; g.beginPath(); g.arc(x - 9, y + 2, 5, Math.PI * 1.1, Math.PI * 1.9); g.stroke(); g.beginPath(); g.arc(x + 9, y + 2, 5, Math.PI * 1.1, Math.PI * 1.9); g.stroke();
    g.beginPath(); g.arc(x, y + 10, 9, 0.2, Math.PI - 0.2); g.stroke();
  };
  head(96 + (v % 3) * 4, 110, '#f5c9a0', '#3a2a20', '#d6403a', false);
  head(160 - (v % 2) * 6, 114, '#ffe2c2', '#6a3a22', '#2f9a94', true);
  g.fillStyle = '#e8344a'; g.beginPath();
  const hx = 128, hy = 52; for (let i = 0; i <= 40; i++) { const t = i / 40 * Math.PI * 2, x = hx + 1.1 * 16 * Math.sin(t) ** 3, y = hy - 1.1 * (13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t)); i ? g.lineTo(x, y) : g.moveTo(x, y); } g.fill();
  g.strokeStyle = '#241726'; g.lineWidth = 6; g.strokeRect(3, 3, 250, 294);
  return c;
}
/** sky gradient + soft hills haze painted into a vertical strip */
export function cloud(seed = 1) {
  const c = cv(512, 256), g = c.getContext('2d');
  const puffs = 6 + Math.floor(rnd(seed) * 4);
  for (let pass = 0; pass < 2; pass++) {
    g.fillStyle = pass ? '#ffffff' : '#c9d8ec';
    for (let i = 0; i < puffs; i++) {
      const x = 80 + (i / (puffs - 1)) * 352, r = 45 + 40 * rnd(seed + i) * Math.sin(Math.PI * i / (puffs - 1) + 0.3);
      g.beginPath(); g.arc(x, 170 - r * 0.55 - (pass ? 6 : 0), r, 0, 7); g.fill();
    }
    g.fillRect(70, 150 - (pass ? 6 : 0), 372, 40);
  }
  return c;
}

/** office carpet or woven floor tiles */
export function carpet(base = '#2b3648', seed = 17) {
  const c = cv(256, 256), g = c.getContext('2d');
  g.fillStyle = base; g.fillRect(0, 0, 256, 256);
  // subtle woven tile grid
  const s = 64;
  g.strokeStyle = shade(base, 0.82); g.lineWidth = 1.5;
  for (let i = 0; i <= 4; i++) {
    g.beginPath(); g.moveTo(i * s, 0); g.lineTo(i * s, 256); g.stroke();
    g.beginPath(); g.moveTo(0, i * s); g.lineTo(256, i * s); g.stroke();
  }
  speck(g, 256, 256, 300, shade(base, 1.18), 0.35, 1, 2, seed);
  speck(g, 256, 256, 200, shade(base, 0.75), 0.4, 1, 2, seed + 11);
  return c;
}

/** burlap sandbag pattern with weave and seams */
export function sandbagTex(base = '#ad9574', seed = 19) {
  const c = cv(256, 128), g = c.getContext('2d');
  g.fillStyle = base; g.fillRect(0, 0, 256, 128);
  speck(g, 256, 128, 180, shade(base, 0.82), 0.5, 2, 5, seed);
  speck(g, 256, 128, 120, shade(base, 1.2), 0.4, 2, 4, seed + 20);
  // seam around edges
  g.strokeStyle = shade(base, 0.6); g.lineWidth = 3; g.strokeRect(4, 4, 248, 120);
  // stitch marks
  g.fillStyle = shade(base, 0.4);
  for (let x = 8; x < 248; x += 12) { g.fillRect(x, 2, 6, 3); g.fillRect(x, 123, 6, 3); }
  return c;
}

/** snow surface with crystal shimmer and drift soft shadows */
export function snow(base = '#f0f5fa', seed = 23) {
  const c = cv(512, 512), g = c.getContext('2d');
  g.fillStyle = base; g.fillRect(0, 0, 512, 512);
  speck(g, 512, 512, 300, '#d2e3f5', 0.45, 4, 16, seed);
  speck(g, 512, 512, 180, '#ffffff', 0.8, 2, 5, seed + 10);
  // soft drift wind streaks
  g.strokeStyle = '#dbe7f4'; g.lineWidth = 14; g.globalAlpha = 0.4;
  for (let i = 0; i < 6; i++) {
    const y = 40 + i * 80;
    g.beginPath(); g.moveTo(0, y); g.bezierCurveTo(180, y - 15, 340, y + 20, 512, y); g.stroke();
  }
  g.globalAlpha = 1;
  return c;
}

/** running bond brick wall with chipped edges */
export function brick(base = '#964434', seed = 29) {
  const c = cv(256, 256), g = c.getContext('2d');
  g.fillStyle = '#4a3d38'; g.fillRect(0, 0, 256, 256); // mortar
  const rows = 8, rh = 256 / rows;
  for (let r = 0; r < rows; r++) {
    const off = (r % 2) * 28;
    for (let x = -32; x < 288; x += 56) {
      g.fillStyle = shade(base, 0.85 + 0.3 * rnd(seed + r * 11 + x));
      g.beginPath(); g.roundRect(x + off + 2, r * rh + 2, 52, rh - 4, 3); g.fill();
    }
  }
  speck(g, 256, 256, 120, '#2e1c16', 0.5, 2, 5, seed + 7);
  return c;
}

/** whiteboard with flowcharts, diagrams, and sticky notes */
export function whiteboardTex(title = 'TARGET ARCHITECTURE', seed = 25) {
  const c = cv(512, 384), g = c.getContext('2d');
  g.fillStyle = '#f8fafc'; g.fillRect(0, 0, 512, 384);
  // aluminum frame
  g.strokeStyle = '#94a3b8'; g.lineWidth = 10; g.strokeRect(5, 5, 502, 374);
  // header
  g.fillStyle = '#0f172a'; g.font = 'bold 24px Arial, sans-serif';
  g.fillText(title, 24, 42);
  g.strokeStyle = '#2563eb'; g.lineWidth = 3; g.beginPath(); g.moveTo(24, 48); g.lineTo(260, 48); g.stroke();
  // boxes and flowchart
  g.strokeStyle = '#0284c7'; g.lineWidth = 3;
  g.strokeRect(32, 70, 110, 60); g.strokeRect(190, 70, 120, 60); g.strokeRect(360, 70, 120, 60);
  g.fillStyle = '#0369a1'; g.font = '14px Arial, sans-serif';
  g.fillText('INPUT / USER', 42, 105); g.fillText('AI ENGINE', 215, 105); g.fillText('RENDER 3D', 380, 105);
  // arrows
  g.beginPath(); g.moveTo(142, 100); g.lineTo(190, 100); g.moveTo(310, 100); g.lineTo(360, 100); g.stroke();
  // bar chart
  g.strokeStyle = '#16a34a'; g.lineWidth = 2;
  g.beginPath(); g.moveTo(40, 320); g.lineTo(240, 320); g.lineTo(240, 200); g.stroke();
  g.fillStyle = '#22c55e';
  g.fillRect(60, 260, 24, 60); g.fillRect(100, 230, 24, 90); g.fillRect(140, 210, 24, 110); g.fillRect(180, 180, 24, 140);
  // colorful sticky notes
  const notes = [{ x: 280, y: 180, c: '#fef08a', t: 'SHIP IT!' }, { x: 380, y: 190, c: '#fbcfe8', t: 'FIX BUG' }, { x: 330, y: 260, c: '#bbf7d0', t: 'PASS QA' }];
  for (const n of notes) {
    g.fillStyle = n.c; g.fillRect(n.x, n.y, 75, 75);
    g.fillStyle = '#1e293b'; g.font = 'bold 13px Arial, sans-serif'; g.fillText(n.t, n.x + 8, n.y + 42);
    g.strokeStyle = '#cbd5e1'; g.lineWidth = 1; g.strokeRect(n.x, n.y, 75, 75);
  }
  return c;
}

/** glowing computer / tech screen texture */
export function screenTex(type = 'chart', seed = 27) {
  const c = cv(512, 320), g = c.getContext('2d');
  g.fillStyle = '#090d16'; g.fillRect(0, 0, 512, 320);
  // header bar
  g.fillStyle = '#1e293b'; g.fillRect(0, 0, 512, 28);
  g.fillStyle = '#ef4444'; g.beginPath(); g.arc(16, 14, 5, 0, 7); g.fill();
  g.fillStyle = '#eab308'; g.beginPath(); g.arc(32, 14, 5, 0, 7); g.fill();
  g.fillStyle = '#22c55e'; g.beginPath(); g.arc(48, 14, 5, 0, 7); g.fill();
  if (type === 'code') {
    g.font = '16px monospace';
    const lines = [
      ['#38bdf8', 'async function deploySystem() {'],
      ['#a855f7', '  const models = await loadAI();'],
      ['#22c55e', '  console.log("INITIALIZED", status);'],
      ['#f97316', '  return executeWorkflow(params);'],
      ['#38bdf8', '}'],
      ['#64748b', '// Continuous verification running...'],
    ];
    lines.forEach((ln, idx) => { g.fillStyle = ln[0]; g.fillText(ln[1], 24, 64 + idx * 32); });
  } else {
    // line graphs
    g.strokeStyle = '#0284c7'; g.lineWidth = 3;
    g.beginPath();
    for (let x = 30; x < 480; x += 30) {
      const y = 240 - Math.sin(x * 0.02 + seed) * 80 - rnd(x + seed) * 40;
      x === 30 ? g.moveTo(x, y) : g.lineTo(x, y);
    }
    g.stroke();
    // green accents
    g.strokeStyle = '#22c55e'; g.lineWidth = 2;
    g.beginPath();
    for (let x = 30; x < 480; x += 40) {
      const y = 210 - Math.cos(x * 0.025) * 50;
      x === 30 ? g.moveTo(x, y) : g.lineTo(x, y);
    }
    g.stroke();
  }
  return c;
}

/** city skyline seen through office window */
export function citySkylineTex(seed = 31) {
  const c = cv(512, 256), g = c.getContext('2d');
  // sky gradient
  const grad = g.createLinearGradient(0, 0, 0, 256);
  grad.addColorStop(0, '#60a5fa'); grad.addColorStop(1, '#bae6fd');
  g.fillStyle = grad; g.fillRect(0, 0, 512, 256);
  // far buildings (silhouette)
  g.fillStyle = '#93c5fd';
  for (let x = 0; x < 512; x += 32) {
    const h = 70 + rnd(seed + x) * 110;
    g.fillRect(x, 256 - h, 30, h);
  }
  // near skyscrapers with lit windows
  g.fillStyle = '#3b82f6';
  for (let x = 12; x < 512; x += 54) {
    const h = 100 + rnd(seed + x * 2) * 120;
    g.fillRect(x, 256 - h, 46, h);
    // windows
    g.fillStyle = '#fef08a';
    for (let wy = 256 - h + 12; wy < 240; wy += 18) {
      for (let wx = x + 6; wx < x + 40; wx += 10) {
        if (rnd(wx * wy) > 0.3) g.fillRect(wx, wy, 6, 8);
      }
    }
    g.fillStyle = '#3b82f6';
  }
  return c;
}
