// Cast for "Why Is It So Hard to Let Go?" — built on the toonbase Pen library.
import { Pen, bodyFront, bodyBack, bodySide, INK, SKIN, SKINS, mix, shade, fxBurst, fxPuff, fxStars } from './toonbase.js';

export const OUT = {
  leo: { jacket: '#d6403a', pants: '#34405a', boots: '#f4f1ea', hat: 'hair', hair: '#3a2a20', hairCut: 0.27, skin: SKINS[1], buttons: '#f4f1ea' },
  maya: { jacket: '#2f9a94', pants: '#3b3f52', boots: '#5a3b2a', hat: 'long', hair: '#6a3a22', hairCut: 0.28, dress: '#f2c14e', skin: SKIN, buttons: '#f6e7c1' },
  // Universal topic-specific character archetypes
  executive: { jacket: '#1e293b', pants: '#0f172a', boots: '#18181b', hat: 'hair', hair: '#1c1917', hairCut: 0.24, tie: '#dc2626', collar: '#ffffff', glasses: 'square', skin: SKINS[0] },
  techLead: { jacket: '#2563eb', pants: '#475569', boots: '#f8fafc', hat: 'hair', hair: '#44403c', hairCut: 0.26, collar: '#ffffff', glasses: 'round', skin: SKINS[1] },
  soldier: { jacket: '#4b5320', pants: '#3d441a', boots: '#1c1917', hat: 'helmet_army', helmetCol: '#3d441a', skin: SKINS[1] },
  explorer: { jacket: '#d4b996', pants: '#78350f', boots: '#3d2817', hat: 'pith', pithCol: '#d4b996', belt: '#78350f', skin: SKINS[2] },
  caveman: { jacket: '#78350f', pants: '#451a03', boots: '#292524', hat: 'fur_headband', hair: '#1c1917', hairCut: 0.42, pelt: '#92400e', skin: SKINS[2] },
  scientist: { jacket: '#f8fafc', pants: '#334155', boots: '#1e293b', hat: 'hair', hair: '#57534e', hairCut: 0.28, tie: '#0284c7', collar: '#ffffff', glasses: 'wire', skin: SKINS[1] },
  worker: { jacket: '#f97316', pants: '#1d4ed8', boots: '#b45309', hat: 'hard_hat', hardHatCol: '#facc15', skin: SKINS[3] },
};

// ---------- arm poses (side -1 = canvas left, +1 = canvas right) ----------
const L2 = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
function poseP(name, side, c) {
  const { S, T, HH, u, tcx, chin, torsoBot } = c, X = (k, d = 0) => tcx + side * (k * T.a + d * u);
  switch (name) {
    case 'hang': return { pts: [S, [X(1.12, 4), S[1] + 0.48 * HH], [X(1.12, 7), chin + 1.04 * HH]], front: false };
    case 'string': return { pts: [S, [X(1.15, 10), S[1] + 0.32 * HH], [X(1.1, 16), S[1] + 0.06 * HH]], front: false };
    case 'stringLow': return { pts: [S, [X(1.12, 6), S[1] + 0.44 * HH], [X(1.05, 10), S[1] + 0.56 * HH]], front: false };
    case 'clutch': return { pts: [S, [X(1.0, 4), S[1] + 0.42 * HH], [X(0.1), S[1] + 0.2 * HH]], front: true };
    case 'heart': return { pts: [S, [X(1.0, 2), S[1] + 0.4 * HH], [X(-0.2), S[1] + 0.2 * HH]], front: true };
    case 'reach': return { pts: [S, [X(1.15, 14), S[1] + 0.22 * HH], [X(1.25, 42), S[1] + 0.16 * HH]], front: false };
    case 'photo': return { pts: [S, [X(1.05, 6), S[1] + 0.46 * HH], [X(0.3), S[1] + 0.3 * HH]], front: true };
    case 'open': return { pts: [S, [X(1.15, 12), S[1] + 0.05 * HH], [X(1.25, 22), S[1] - 0.24 * HH]], front: false };
    case 'seat': return { pts: [S, [X(1.15, 6), S[1] + 0.4 * HH], [X(1.35, 14), torsoBot + 0.04 * HH]], front: false };
    case 'up': return { pts: [S, [X(1.12, 12), S[1] - 0.22 * HH], [X(1.15, 16), S[1] - 0.66 * HH]], front: false };
    case 'wave': return { pts: [S, [X(1.12, 14), S[1] - 0.1 * HH], [X(1.25, 22), S[1] - 0.5 * HH]], front: false };
    case 'hip': return { pts: [S, [X(1.25, 12), S[1] + 0.3 * HH], [X(0.95, 2), torsoBot - 0.18 * HH]], front: false };
    case 'cheek': return { pts: [S, [X(1.1, 6), S[1] + 0.3 * HH], [X(0.55), chin - 0.32 * HH]], front: true };
    case 'sad': return { pts: [S, [X(1.0, 2), S[1] + 0.5 * HH], [X(0.85), chin + 1.1 * HH]], front: false };
    case 'guard': return { pts: [S, [X(1.05, 4), S[1] + 0.32 * HH], [X(0.2), chin + 0.25 * HH]], front: true };
    case 'hero': return { pts: [S, [X(1.25, 18), S[1] - 0.15 * HH], [X(1.35, 30), S[1] - 0.72 * HH]], front: false };
    // New action poses
    case 'typing': return { pts: [S, [X(1.08, 4), S[1] + 0.38 * HH], [X(0.35, 2), chin + 0.72 * HH]], front: true };
    case 'phone': return { pts: [S, [X(1.18, 8), S[1] + 0.35 * HH], [X(0.68, 4), chin - 0.18 * HH]], front: true };
    case 'mug': return { pts: [S, [X(1.12, 4), S[1] + 0.38 * HH], [X(0.32), chin + 0.42 * HH]], front: true };
    case 'point': return { pts: [S, [X(1.15, 12), S[1] + 0.12 * HH], [X(1.5, 36), S[1] + 0.05 * HH]], front: false };
    case 'aim': return { pts: [S, [X(1.12, 10), S[1] + 0.18 * HH], [X(1.42, 32), chin + 0.15 * HH]], front: true };
    case 'salute': return { pts: [S, [X(1.22, 14), S[1] + 0.22 * HH], [X(0.82), chin - 0.52 * HH]], front: true };
    case 'shiver': return { pts: [S, [X(0.82, 2), S[1] + 0.35 * HH], [X(0.0), S[1] + 0.28 * HH]], front: true };
    case 'torch': return { pts: [S, [X(1.18, 12), S[1] - 0.1 * HH], [X(1.22, 20), S[1] - 0.75 * HH]], front: false };
    case 'binoculars': return { pts: [S, [X(1.1, 6), S[1] + 0.28 * HH], [X(0.38), chin - 0.22 * HH]], front: true };
    default: return null;
  }
}
/** a pose is a name or [nameA, nameB, t] */
function pose(spec, side, c) {
  if (!spec) return null;
  if (typeof spec === 'string') return poseP(spec, side, c);
  const a = poseP(spec[0], side, c), b = poseP(spec[1], side, c), t = spec[2];
  return { pts: a.pts.map((q, i) => L2(q, b.pts[i], t)), front: t < 0.5 ? a.front : b.front };
}

// ---------- held props (drawn at the hand, design units) ----------
export function drawProp(p, kind, hx, hy, u, side, ang = 0) {
  const g = p.g;
  g.save();
  const [x, y] = p.P(hx, hy);
  g.translate(x, y); g.rotate(ang); g.translate(-x, -y);
  const keep = p.rot; p.rot = null;
  if (kind === 'photo') { // polaroid: the two of them under a heart
    const w = 30 * u, h = 36 * u;
    p.poly([[x - w, y - h - 6 * u], [x + w, y - h - 6 * u], [x + w, y + 10 * u], [x - w, y + 10 * u]], '#fbf7ee', 2 * u);
    p.poly([[x - w + 5 * u, y - h - u], [x + w - 5 * u, y - h - u], [x + w - 5 * u, y - 4 * u], [x - w + 5 * u, y - 4 * u]], '#ffd9a0', 1.5 * u);
    for (const [sx, skin, hair, body] of [[-11, '#f5c9a0', '#3a2a20', '#d6403a'], [11, '#ffe2c2', '#6a3a22', '#2f9a94']]) {
      const cx = x + sx * u;
      p.clip([[x - w + 5 * u, y - h - u], [x + w - 5 * u, y - h - u], [x + w - 5 * u, y - 4 * u], [x - w + 5 * u, y - 4 * u]], () => p.ell(cx, y - 2 * u, 9 * u, 9 * u, body, 1.2 * u));
      p.ell(cx, y - 19 * u, 8 * u, 7.5 * u, skin, 1.2 * u);
      p.ell(cx, y - 22 * u, 8.4 * u, 4.6 * u, hair, 0, 180, 360);
      p.arc(cx - 3 * u, y - 18 * u, 1.6 * u, 1.2 * u, 200, 340, 0.9 * u); p.arc(cx + 3 * u, y - 18 * u, 1.6 * u, 1.2 * u, 200, 340, 0.9 * u);
      p.arc(cx, y - 15 * u, 3 * u, 2 * u, 20, 160, 0.9 * u);
    }
    heart(p, x, y - 33 * u, 4.5 * u, '#e8344a', 1.2 * u);
  } else if (kind === 'phone') {
    p.poly([[x - 5 * u, y - 10 * u], [x + 5 * u, y - 10 * u], [x + 5 * u, y + 10 * u], [x - 5 * u, y + 10 * u]], '#09090b', 1.5 * u);
    p.poly([[x - 4 * u, y - 8 * u], [x + 4 * u, y - 8 * u], [x + 4 * u, y + 8 * u], [x - 4 * u, y + 8 * u]], '#38bdf8', 0);
  } else if (kind === 'mug') {
    p.poly([[x - 7 * u, y - 9 * u], [x + 7 * u, y - 9 * u], [x + 6 * u, y + 9 * u], [x - 6 * u, y + 9 * u]], '#f8fafc', 1.8 * u);
    p.arc(x + 7.5 * u, y, 4 * u, 5 * u, 270, 90, 1.5 * u);
  } else if (kind === 'laptop') {
    p.poly([[x - 18 * u, y - 2 * u], [x + 18 * u, y - 2 * u], [x + 20 * u, y + 8 * u], [x - 20 * u, y + 8 * u]], '#94a3b8', 1.8 * u);
    p.poly([[x - 18 * u, y - 24 * u], [x + 18 * u, y - 24 * u], [x + 18 * u, y - 2 * u], [x - 18 * u, y - 2 * u]], '#0f172a', 1.8 * u);
    p.poly([[x - 16 * u, y - 22 * u], [x + 16 * u, y - 22 * u], [x + 16 * u, y - 4 * u], [x - 16 * u, y - 4 * u]], '#38bdf8', 0);
  } else if (kind === 'torch') {
    p.band([[x, y + 22 * u], [x, y - 20 * u]], '#78350f', 5 * u, 1.5 * u);
    p.ell(x, y - 28 * u, 9 * u, 14 * u, '#facc15', 1.5 * u);
    p.ell(x, y - 26 * u, 5 * u, 8 * u, '#f97316', 0);
  } else if (kind === 'spear') {
    p.band([[x, y + 40 * u], [x, y - 45 * u]], '#854d0e', 4.5 * u, 1.5 * u);
    p.poly([[x - 5 * u, y - 45 * u], [x + 5 * u, y - 45 * u], [x, y - 65 * u]], '#cbd5e1', 1.5 * u);
  } else if (kind === 'binoculars') {
    for (const sx of [-7, 7]) {
      p.poly([[x + (sx - 4.5) * u, y - 9 * u], [x + (sx + 4.5) * u, y - 9 * u], [x + (sx + 5) * u, y + 9 * u], [x + (sx - 5) * u, y + 9 * u]], '#18181b', 1.5 * u);
      p.ell(x + sx * u, y - 9 * u, 4.5 * u, 2.5 * u, '#38bdf8', 1 * u);
    }
  } else if (kind === 'folder') {
    p.poly([[x - 14 * u, y - 16 * u], [x + 14 * u, y - 16 * u], [x + 14 * u, y + 16 * u], [x - 14 * u, y + 16 * u]], '#ca8a04', 1.6 * u);
  }
  p.rot = keep;
  g.restore();
}

// ---------- small shared shapes ----------
export function heartPts(cx, cy, r, n = 40) {
  const pts = [];
  for (let i = 0; i < n; i++) { const t = (i / n) * Math.PI * 2; pts.push([cx + r * 16 * Math.sin(t) ** 3 / 16, cy - r * (13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t)) / 16]); }
  return pts;
}
function heart(p, cx, cy, r, fill, ol) { p.poly(heartPts(cx, cy, r), fill, ol); }

/** draw a character card. spec: {outfit, expr, mode, phase, facing, look, blink, tilt, headTilt, armL, armR, holdL, holdR, angL, angR, seed, view, side, flip, tear, blush} */
export function drawChar(canvas, s) {
  const g = canvas.getContext('2d');
  g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, canvas.width, canvas.height);
  const k = canvas.width / 600; g.setTransform(k, 0, 0, k, 0, 0);
  
  // View angle determination (front, back, side-left, side-right)
  const isBack = s.view === 'back' || s.facingBack === true;
  const isSide = s.view === 'side' || !!s.side;
  const isLeft = s.side === 'left' || s.side === -1 || (isSide && (s.facing < -0.15 || s.flip));
  
  if (s.flip || isLeft) {
    g.translate(600, 0);
    g.scale(-1, 1);
  }
  
  const p = new Pen(g);
  const o = typeof s.outfit === 'string' ? OUT[s.outfit] || OUT.leo : s.outfit;
  canvas.hands = {};

  if (isBack) {
    bodyBack(p, o, {
      phase: s.phase || 0, mode: s.mode || 'stand', lift: s.lift || 60, tilt: s.tilt || 0, headTilt: s.headTilt || 0,
      facing: s.facing ?? 0, seed: s.seed || 1, light: s.light ?? (-120 * Math.PI / 180),
      armFn: (side, c) => pose(side < 0 ? s.armL : s.armR, side, c),
      onHand: (pp, side, hx, hy, u) => {
        canvas.hands[side < 0 ? 'L' : 'R'] = pp.P(hx, hy);
        const it = side < 0 ? s.holdL : s.holdR; if (it) drawProp(pp, it, hx, hy, u, side, (side < 0 ? s.angL : s.angR) || 0);
      },
      onBackHead: s.onBackHead,
      onFace: s.onFace,
    });
    return;
  }

  if (isSide) {
    bodySide(p, o, {
      phase: s.phase || 0, expr: s.expr || 'neutral', mode: s.mode || 'march',
      blink: s.blink || 0, seed: s.seed || 1, stride: s.stride ?? 0.7, stomp: s.stomp ?? 0.6,
      light: s.light ?? (-120 * Math.PI / 180)
    });
    return;
  }

  bodyFront(p, o, {
    phase: s.phase || 0, expr: s.expr || 'neutral', mode: s.mode || 'stand', look: s.look || 0, blink: s.blink || 0,
    tilt: s.tilt || 0, headTilt: s.headTilt || 0, facing: s.facing ?? 0, seed: s.seed || 1, light: s.light ?? (-120 * Math.PI / 180),
    armFn: (side, c) => pose(side < 0 ? s.armL : s.armR, side, c),
    onHand: (pp, side, hx, hy, u) => {
      canvas.hands[side < 0 ? 'L' : 'R'] = pp.P(hx, hy);
      const it = side < 0 ? s.holdL : s.holdR; if (it) drawProp(pp, it, hx, hy, u, side, (side < 0 ? s.angL : s.angR) || 0);
    },
    onFace: (pp, cx, hc, W, u, facing) => {
      if (s.onFace) s.onFace(pp, cx, hc, W, u, facing);
      if (s.blush) for (const sd of [-1, 1]) { pp.g.globalAlpha = 0.55; pp.ell(cx + 7 * u * facing + sd * 24 * u, hc + 11 * u, 8 * u, 4.5 * u, '#ff8a8a', 0); pp.g.globalAlpha = 1; }
      if (s.tear) { const ty = hc + 6 * u + s.tear * 22 * u; pp.ell(cx + 7 * u * facing - 15 * u, ty, 3.6 * u, 5.2 * u, '#9fd4ff', 1.8 * u); }
    },
  });
}

// ---------- 2D effect cards (600 x 1000 design space, centred around (300, 500)) ----------
const rnd = (s) => { const x = Math.sin(s * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };
function bean(p, x, y, r, skin, hair, body) { // tiny person for the dream bubble
  p.ell(x, y + r * 2.1, r * 0.85, r * 1.15, body, 4);
  p.ell(x, y, r, r * 0.94, skin, 4);
  p.ell(x, y - r * 0.35, r * 1.02, r * 0.62, hair, 0, 180, 360);
}
export function drawFx(canvas, s) {
  const g = canvas.getContext('2d');
  g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, canvas.width, canvas.height);
  const k = canvas.width / 600; g.setTransform(k, 0, 0, k, 0, 0);
  const p = new Pen(g); p.ol = 8;
  const t = s.t ?? 0;
  g.translate(300, 500); const kk = s.scale ?? 1; g.scale(kk, kk); g.translate(-300, -500);
  switch (s.fx) {
    case 'burst': fxBurst(p, t, s.col || '#fff3b0', s.rays || 12, 60, 280); break;
    case 'puff': fxPuff(p, t); break;
    case 'stars': fxStars(p, t); break;
    case 'sparkle': {
      for (let i = 0; i < 5; i++) {
        const ph = (t + i * 0.21) % 1, x = 120 + ((i * 97) % 360), y = 300 + ((i * 173) % 400), r = 34 * Math.sin(ph * Math.PI);
        if (r > 2) p.poly([[x, y - r], [x + r * 0.28, y - r * 0.28], [x + r, y], [x + r * 0.28, y + r * 0.28], [x, y + r], [x - r * 0.28, y + r * 0.28], [x - r, y], [x - r * 0.28, y - r * 0.28]], s.col || '#fff3b0', 5);
      }
      break;
    }
    case 'dream': { // thought cloud: their imagined future (house, the two of them, a heart)
      const C = [[300, 470, 190, 150], [170, 430, 90, 80], [430, 420, 95, 85], [220, 330, 90, 80], [380, 320, 100, 85], [300, 300, 90, 70], [180, 560, 80, 60], [420, 560, 85, 65]];
      for (const [x, y, rx, ry] of C) p.ell(x, y, rx, ry, null, 14);
      for (const [x, y, rx, ry] of C) p.ell(x, y, rx, ry, '#fdfbff', 0);
      for (const [x, y, r] of [[200, 700, 34], [160, 790, 22], [135, 860, 13]]) p.ell(x, y, r, r, '#fdfbff', 7);
      // house
      p.poly([[330, 430], [450, 430], [450, 560], [330, 560]], '#f3d9a6', 6);
      p.poly([[315, 440], [390, 365], [465, 440]], '#d6403a', 6);
      p.poly([[372, 500], [408, 500], [408, 560], [372, 560]], '#8a5a34', 5);
      p.poly([[340, 455], [365, 455], [365, 480], [340, 480]], '#9fd0e8', 4);
      p.stroke([[150, 562], [470, 562]], 7, '#6aa84a');
      // the two of them, hand in hand
      bean(p, 205, 470, 30, '#f5c9a0', '#3a2a20', '#d6403a');
      bean(p, 275, 474, 28, '#ffe2c2', '#6a3a22', '#2f9a94');
      p.stroke([[228, 538], [252, 538]], 7);
      heart(p, 240, 395, 26, '#e8344a', 5);
      break;
    }
    case 'rain': {
      g.lineCap = 'round';
      for (let i = 0; i < 46; i++) {
        const x = rnd(i * 3.1 + (s.seed || 0)) * 640 - 20, y = ((rnd(i * 7.7 + (s.seed || 0)) + t) % 1) * 1150 - 100;
        g.strokeStyle = i % 3 ? '#cfe2ff' : '#a9c4ef'; g.lineWidth = 3.5;
        g.beginPath(); g.moveTo(x, y); g.lineTo(x - 10, y + 70); g.stroke();
      }
      break;
    }
    case 'heart': heart(p, 300, 500, s.r || 160, s.col || '#e8344a', 10); if (!s.flat) p.ell(240, 420, 30, 20, '#ff9aa6', 0, 0, 360, -30); break;
    case 'crack': { // the heart breaks in two halves drifting apart (t)
      const d = 60 * t, r = 160;
      for (const sd of [-1, 1]) {
        g.save(); g.translate(sd * d, 20 * t); g.translate(300, 500); g.rotate(sd * 0.25 * t); g.translate(-300, -500);
        const half = [...heartPts(300, 500, r, 60).filter(([x]) => (sd < 0 ? x <= 300 : x >= 300))];
        const zz = [[300, 395], [280, 450], [318, 500], [282, 560], [300, 640]];
        p.poly(sd < 0 ? [...half, ...zz.slice().reverse()] : [...half, ...zz], s.col || '#e8344a', 10);
        g.restore();
      }
      break;
    }
    case 'mug': { // comfort: a warm mug with steam
      p.ell(300, 500, 230, 230, '#ffe8b8', 0);
      p.arc(380, 530, 55, 62, -80, 80, 40, INK); p.arc(380, 530, 55, 62, -80, 80, 22, '#e98a5a');
      p.poly([[200, 430], [380, 430], [365, 640], [215, 640]], '#e98a5a', 10);
      p.ell(290, 430, 90, 18, '#7a4a2a', 8);
      heart(p, 290, 545, 32, '#fff3e0', 0);
      for (let i = 0; i < 3; i++) { const x = 240 + i * 50, ph = (t * 1.5 + i * 0.33) % 1; p.line([[x, 400 - ph * 40], [x + 15, 360 - ph * 40], [x - 5, 320 - ph * 40], [x + 10, 280 - ph * 40]], '#ffffff', 10); }
      break;
    }
    case 'sunny': { // happiness: a smiling sun
      p.ell(300, 500, 230, 230, '#fff2b0', 0);
      for (let i = 0; i < 10; i++) { const a = i * Math.PI / 5 + t * 0.6; p.poly([[300 + Math.cos(a - 0.12) * 125, 500 + Math.sin(a - 0.12) * 125], [300 + Math.cos(a) * 205, 500 + Math.sin(a) * 205], [300 + Math.cos(a + 0.12) * 125, 500 + Math.sin(a + 0.12) * 125]], '#ffb43a', 8); }
      p.ell(300, 500, 115, 115, '#ffd23a', 10);
      p.arc(255, 485, 18, 13, 200, 340, 9); p.arc(345, 485, 18, 13, 200, 340, 9);
      p.arc(300, 520, 42, 30, 20, 160, 10);
      break;
    }
    case 'shield': { // security: a shield with a heart
      p.ell(300, 500, 230, 230, '#d8ecff', 0);
      p.poly([[300, 320], [440, 370], [430, 520], [300, 680], [170, 520], [160, 370]], '#4f8ad8', 11);
      p.poly([[300, 355], [405, 393], [397, 510], [300, 635], [203, 510], [195, 393]], '#6fa8ee', 0);
      heart(p, 300, 490, 62, '#ffffff', 8);
      break;
    }
    case 'leaf': { // a falling leaf, spinning (t)
      g.translate(300, 500); g.rotate(t * 9); g.scale(1, 0.6 + 0.4 * Math.cos(t * 13)); g.translate(-300, -500);
      p.poly([[300, 380], [370, 470], [300, 640], [230, 470]], s.col || '#e9893a', 8);
      p.stroke([[300, 400], [300, 630]], 6, shade(s.col || '#e9893a', 0.6));
      break;
    }
    case 'glow': { // concentric halo rings (alpha-tested cards cannot fade, so step the rings)
      const c = s.col || '#ffe9a0';
      for (let i = 0; i < 4; i++) { g.globalAlpha = 1; p.ell(300, 500, 260 - i * 50, 260 - i * 50, mix(c, '#ffffff', i * 0.25), 0); }
      break;
    }
  }
}
