// Exact 2D Cartoon Stickman Engine (Henry Stickmin / Animated Shorts look).
// Supports:
//   - 'white': pure stick torso, white cel-shaded round head, tall oval sclera eyes, oversized 4-fingered white gloves, teardrop shoes
//   - 'black': glossy black head with crisp white rim highlight, bright white sclera eyes/brows, red t-shirt + blue shorts over stick limbs
//   - 'casual': white stickman head + customizable colored t-shirt & shorts
//   - Poses: 'stand', 'sit' (legs dangling over ledge/hut), 'kneel', plus articulated elbows, knees, walk cycles, and handheld props.
import { ctx, TAU, lerp, rr, circle, ell, line, C } from './core/lib.js';

const INK = C.ink;

function drawCartoonGlove(x, y, wristRad = Math.PI / 2, thumbDir = 1, s = 1, isDark = false) {
  ctx.save();
  ctx.translate(x, y);
  // Rotate so +Y points strictly outward along the forearm tangent
  ctx.rotate(wristRad - Math.PI / 2);
  ctx.scale(s * thumbDir, s);

  if (isDark) {
    // Sleek 2D dark cartoon hand aligned cleanly with forearm
    ctx.fillStyle = '#111115';
    ctx.beginPath();
    ctx.moveTo(-4, -2);
    // Thumb flaring inward (-x)
    ctx.quadraticCurveTo(-13, 3, -14, 11);
    ctx.quadraticCurveTo(-13, 15, -8, 12);
    ctx.lineTo(-7, 15);
    // Index finger
    ctx.quadraticCurveTo(-10, 25, -6, 27);
    ctx.quadraticCurveTo(-3, 27, -2, 18);
    // Middle finger
    ctx.quadraticCurveTo(-1, 29, 2.5, 29);
    ctx.quadraticCurveTo(5.5, 29, 4.5, 18);
    // Ring / pinky finger
    ctx.quadraticCurveTo(8, 26, 10.5, 24);
    ctx.quadraticCurveTo(12.5, 21, 8.5, 11);
    // Outer palm back to wrist
    ctx.quadraticCurveTo(9, 4, 4, -2);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
    return;
  }

  // Unified classic 2D cartoon white glove (sleek cuff + thumb + 3 sculpted fingers, zero ugly bars)
  ctx.beginPath();
  ctx.moveTo(-6.5, -2);
  // Soft flared cuff edge
  ctx.quadraticCurveTo(-9, 2, -8, 5);
  // Natural thumb curving smoothly outward (-x)
  ctx.bezierCurveTo(-16, 8, -19, 17, -14, 21);
  ctx.quadraticCurveTo(-10, 22, -7.5, 16);
  // Index finger
  ctx.bezierCurveTo(-10, 26, -8, 34, -4, 34.5);
  ctx.quadraticCurveTo(-1, 34.5, -0.5, 24.5);
  // Middle finger
  ctx.bezierCurveTo(0.2, 36.5, 4.8, 36.5, 5.5, 24.5);
  // Pinky / ring finger
  ctx.bezierCurveTo(7.2, 33.5, 12.2, 32, 11.5, 21);
  // Outer palm back to cuff
  ctx.bezierCurveTo(13, 13, 11, 5, 7.5, 3);
  ctx.quadraticCurveTo(8.5, 0, 6.5, -2);
  ctx.closePath();

  const gGlove = ctx.createLinearGradient(-12, 0, 12, 32);
  gGlove.addColorStop(0, '#FFFFFF');
  gGlove.addColorStop(0.72, '#F7F9FC');
  gGlove.addColorStop(1, '#DCE2EB');
  ctx.fillStyle = gGlove;
  ctx.fill();

  ctx.strokeStyle = INK;
  ctx.lineWidth = 3.6;
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  ctx.stroke();

  // Delicate curved wrist cuff band crease
  ctx.strokeStyle = INK;
  ctx.lineWidth = 2.6;
  ctx.beginPath();
  ctx.moveTo(-7.2, 4.5);
  ctx.quadraticCurveTo(0, 7.5, 7.2, 4.5);
  ctx.stroke();

  ctx.restore();
}

function drawTeardropShoe(x, y, rot = 0, flip = 1) {
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(rot);
  ctx.scale(flip, 1);
  ctx.beginPath();
  ctx.moveTo(-14, 0);
  ctx.bezierCurveTo(-15, -14, 4, -20, 26, -9);
  ctx.bezierCurveTo(34, -5, 34, 2, 23, 2);
  ctx.lineTo(-10, 2);
  ctx.closePath();
  ctx.fillStyle = INK;
  ctx.fill();
  ctx.restore();
}

function drawLimbCurve(x0, y0, x1, y1, bend = 14, lw = 10) {
  const mx = (x0 + x1) * 0.5;
  const my = (y0 + y1) * 0.5;
  const dx = x1 - x0, dy = y1 - y0;
  const len = Math.hypot(dx, dy) || 1;
  const nx = -dy / len, ny = dx / len;
  const cpx = mx + nx * bend, cpy = my + ny * bend;
  ctx.strokeStyle = INK;
  ctx.lineWidth = lw;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.beginPath();
  ctx.moveTo(x0, y0);
  ctx.quadraticCurveTo(cpx, cpy, x1, y1);
  ctx.stroke();
  return Math.atan2(y1 - cpy, x1 - cpx);
}

function drawStickHead(x, y, rx, ry, isBlack, mood = 'flat', mouthKind = 'smile', lookX = 0.25, lookY = 0, t = 0) {
  ctx.save();
  ctx.translate(x, y);

  // 1. Head fill + cel shading
  if (isBlack) {
    ctx.beginPath(); ctx.ellipse(0, 0, rx, ry, 0, 0, TAU);
    ctx.fillStyle = '#0F0F13'; ctx.fill();
    // Crisp white specular rim arc along top-right/top curve
    ctx.save();
    ctx.strokeStyle = 'rgba(255,255,255,0.78)'; ctx.lineWidth = 3.5; ctx.lineCap = 'round';
    ctx.beginPath(); ctx.ellipse(0, 0, rx * 0.87, ry * 0.87, 0, -Math.PI * 0.82, -Math.PI * 0.16); ctx.stroke();
    ctx.restore();
  } else {
    ctx.beginPath(); ctx.ellipse(0, 0, rx, ry, 0, 0, TAU);
    ctx.fillStyle = '#FFFFFF'; ctx.fill();
    // Soft cel-shaded crescent along bottom-left edge
    ctx.save();
    ctx.beginPath(); ctx.ellipse(0, 0, rx, ry, 0, 0, TAU); ctx.clip();
    ctx.fillStyle = 'rgba(175, 185, 200, 0.34)';
    ctx.beginPath();
    ctx.ellipse(rx * 0.16, -ry * 0.14, rx * 1.02, ry * 1.02, 0, 0, TAU);
    ctx.rect(rx * 1.5, -ry * 1.5, -rx * 3, ry * 3);
    ctx.fill('evenodd');
    ctx.restore();
    // Clean outline
    ctx.beginPath(); ctx.ellipse(0, 0, rx, ry, 0, 0, TAU);
    ctx.strokeStyle = INK; ctx.lineWidth = 5.5; ctx.stroke();
  }

  // 2. Expressive big oval white-sclera eyes (side-by-side)
  const isBlink = mood === 'blink' || (t > 0 && (t % 4.1 > 3.98));
  const strokeCol = isBlack ? '#FFFFFF' : INK;
  for (const s of [-1, 1]) {
    const ex = s * rx * 0.24 + lookX * rx * 0.10;
    const ey = -ry * 0.06;
    if (isBlink || mood === 'closed') {
      ctx.strokeStyle = strokeCol; ctx.lineWidth = 4.2; ctx.lineCap = 'round';
      ctx.beginPath(); ctx.arc(ex, ey + ry * 0.05, rx * 0.14, 0.2, Math.PI - 0.2); ctx.stroke();
    } else if (mood === 'happy' || mood === 'laugh') {
      ctx.strokeStyle = strokeCol; ctx.lineWidth = 4.5; ctx.lineCap = 'round';
      ctx.beginPath(); ctx.arc(ex, ey + ry * 0.08, rx * 0.15, Math.PI * 1.15, Math.PI * 1.85); ctx.stroke();
    } else if (mood === 'squint') {
      ctx.strokeStyle = strokeCol; ctx.lineWidth = 4.5; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
      ctx.beginPath();
      ctx.moveTo(ex - s * rx * 0.11, ey - ry * 0.10);
      ctx.lineTo(ex + s * rx * 0.10, ey);
      ctx.lineTo(ex - s * rx * 0.11, ey + ry * 0.10);
      ctx.stroke();
    } else {
      const shocked = mood === 'shocked' || mood === 'surprised';
      const erx = rx * (shocked ? 0.25 : 0.23);
      const ery = ry * (shocked ? 0.34 : 0.29);
      ctx.save();
      ctx.beginPath(); ctx.ellipse(ex, ey, erx, ery, 0, 0, TAU);
      ctx.fillStyle = '#FFFFFF'; ctx.fill();
      ctx.clip();

      // Solid black pupil tracking lookX / lookY
      const pr = rx * (shocked ? 0.068 : 0.088);
      const px = ex + lookX * erx * 0.42;
      const py = ey + lookY * ery * 0.35;
      circle(px, py, pr, INK);

      // Half-lidded smug / determined lid
      if (mood === 'smug' || mood === 'angry' || mood === 'determined') {
        ctx.fillStyle = isBlack ? '#0F0F13' : '#FFFFFF';
        ctx.fillRect(ex - erx, ey - ery, erx * 2, ery * (mood === 'smug' ? 0.55 : 0.36));
      }
      ctx.restore();

      ctx.strokeStyle = isBlack ? 'rgba(255,255,255,0.22)' : INK;
      ctx.lineWidth = isBlack ? 1.5 : 3.0;
      ctx.beginPath(); ctx.ellipse(ex, ey, erx, ery, 0, 0, TAU); ctx.stroke();
    }
  }

  // 3. Curved floating eyebrows
  ctx.strokeStyle = strokeCol; ctx.lineWidth = 3.8; ctx.lineCap = 'round';
  for (const s of [-1, 1]) {
    const bx0 = s * rx * 0.10 + lookX * rx * 0.08;
    const bx1 = s * rx * 0.42 + lookX * rx * 0.08;
    let y0 = -ry * 0.45, y1 = -ry * 0.42, cy = -ry * 0.54;
    if (mood === 'worried' || mood === 'shocked' || mood === 'surprised') {
      y0 = -ry * 0.56; y1 = -ry * 0.44; cy = -ry * 0.62;
    } else if (mood === 'angry' || mood === 'determined') {
      y0 = -ry * 0.34; y1 = -ry * 0.50; cy = -ry * 0.44;
    } else if (mood === 'smug' || mood === 'happy') {
      y0 = -ry * 0.48; y1 = -ry * 0.42; cy = -ry * 0.58;
    }
    ctx.beginPath(); ctx.moveTo(bx0, y0); ctx.quadraticCurveTo((bx0 + bx1) * 0.5, cy, bx1, y1); ctx.stroke();
  }

  // 4. Expressive cartoon mouth
  ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  const mx = lookX * rx * 0.10;
  if (mouthKind === 'open' || mouthKind === 'yell' || mouthKind === 'gasp') {
    ctx.save();
    ctx.beginPath(); ctx.ellipse(mx, ry * 0.44, rx * 0.24, ry * 0.17, 0, 0, TAU);
    ctx.fillStyle = '#3B0D11'; ctx.fill();
    ctx.clip();
    // White upper teeth
    ctx.fillStyle = '#FFFFFF';
    ctx.fillRect(mx - rx * 0.24, ry * 0.26, rx * 0.48, ry * 0.10);
    // Red tongue
    ctx.beginPath(); ctx.ellipse(mx, ry * 0.56, rx * 0.15, ry * 0.11, 0, 0, TAU);
    ctx.fillStyle = '#E53935'; ctx.fill();
    ctx.restore();
    ctx.strokeStyle = strokeCol; ctx.lineWidth = 3.2;
    ctx.beginPath(); ctx.ellipse(mx, ry * 0.44, rx * 0.24, ry * 0.17, 0, 0, TAU); ctx.stroke();
  } else if (mouthKind === 'frown' || mouthKind === 'worried') {
    ctx.strokeStyle = strokeCol; ctx.lineWidth = 4.2;
    ctx.beginPath(); ctx.arc(mx, ry * 0.62, rx * 0.22, Math.PI * 1.15, Math.PI * 1.85); ctx.stroke();
  } else if (mouthKind === 'bigsmile' || mouthKind === 'laugh') {
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(mx - rx * 0.26, ry * 0.30);
    ctx.quadraticCurveTo(mx, ry * 0.33, mx + rx * 0.26, ry * 0.30);
    ctx.quadraticCurveTo(mx, ry * 0.70, mx - rx * 0.26, ry * 0.30);
    ctx.closePath();
    ctx.fillStyle = '#3B0D11'; ctx.fill(); ctx.clip();
    ctx.fillStyle = '#FFFFFF'; ctx.fillRect(mx - rx * 0.26, ry * 0.27, rx * 0.52, ry * 0.11);
    ctx.beginPath(); ctx.ellipse(mx, ry * 0.60, rx * 0.16, ry * 0.11, 0, 0, TAU);
    ctx.fillStyle = '#E53935'; ctx.fill();
    ctx.restore();
    ctx.strokeStyle = strokeCol; ctx.lineWidth = 3.5;
    ctx.beginPath();
    ctx.moveTo(mx - rx * 0.26, ry * 0.30);
    ctx.quadraticCurveTo(mx, ry * 0.33, mx + rx * 0.26, ry * 0.30);
    ctx.quadraticCurveTo(mx, ry * 0.70, mx - rx * 0.26, ry * 0.30);
    ctx.stroke();
  } else {
    // Clean cartoon smirk / smile
    ctx.strokeStyle = strokeCol; ctx.lineWidth = 4.2;
    ctx.beginPath();
    ctx.moveTo(mx - rx * 0.18, ry * 0.39);
    ctx.quadraticCurveTo(mx + rx * 0.04, ry * 0.48, mx + rx * 0.24, ry * 0.34);
    ctx.stroke();
  }

  ctx.restore();
}

function drawTshirtAndShorts(shirtCol = C.shirtRed, shortsCol = C.shortsBlue) {
  const top = -312, shirtH = 104, w = 88;

  // Tapered T-shirt with angled short sleeves
  ctx.beginPath();
  ctx.moveTo(-w * 0.34, top);
  ctx.lineTo(-w * 0.66, top + 34);
  ctx.lineTo(-w * 0.48, top + 52);
  ctx.lineTo(-w * 0.38, top + 40);
  ctx.lineTo(-w * 0.42, top + shirtH);
  ctx.quadraticCurveTo(0, top + shirtH + 8, w * 0.42, top + shirtH);
  ctx.lineTo(w * 0.38, top + 40);
  ctx.lineTo(w * 0.48, top + 52);
  ctx.lineTo(w * 0.66, top + 34);
  ctx.lineTo(w * 0.34, top);
  ctx.quadraticCurveTo(0, top + 5, -w * 0.34, top);
  ctx.closePath();

  const gs = ctx.createLinearGradient(-w * 0.5, top, w * 0.5, top + shirtH);
  gs.addColorStop(0, shirtCol);
  gs.addColorStop(0.65, shirtCol);
  gs.addColorStop(1, 'rgba(0,0,0,0.28)');
  ctx.fillStyle = shirtCol; ctx.fill();
  ctx.fillStyle = gs; ctx.fill();
  ctx.strokeStyle = INK; ctx.lineWidth = 5.2; ctx.lineJoin = 'round'; ctx.stroke();

  // Crew neck shadow curve
  ctx.beginPath();
  ctx.arc(0, top + 4, w * 0.16, 0.1, Math.PI - 0.1);
  ctx.strokeStyle = INK; ctx.lineWidth = 3.5; ctx.stroke();

  // Medium-blue fitted shorts
  const shTop = top + shirtH - 4, shH = 52;
  ctx.beginPath();
  ctx.moveTo(-w * 0.40, shTop);
  ctx.lineTo(w * 0.40, shTop);
  ctx.lineTo(w * 0.52, shTop + shH);
  ctx.lineTo(w * 0.10, shTop + shH);
  ctx.lineTo(0, shTop + shH * 0.48);
  ctx.lineTo(-w * 0.10, shTop + shH);
  ctx.lineTo(-w * 0.52, shTop + shH);
  ctx.closePath();
  const gb = ctx.createLinearGradient(-w * 0.4, shTop, w * 0.4, shTop + shH);
  gb.addColorStop(0, shortsCol);
  gb.addColorStop(0.7, shortsCol);
  gb.addColorStop(1, 'rgba(0,0,0,0.28)');
  ctx.fillStyle = shortsCol; ctx.fill();
  ctx.fillStyle = gb; ctx.fill();
  ctx.strokeStyle = INK; ctx.lineWidth = 5.2; ctx.lineJoin = 'round'; ctx.stroke();
}

/**
 * Draw an exact 2D Cartoon Stickman character.
 * @param {object} o
 *  x, y (ground contact or seat anchor), s (scale), dir (1 | -1),
 *  kind: 'white' | 'black' | 'casual',
 *  pose: 'stand' | 'sit' | 'kneel' | 'recline',
 *  walk: phase rad | null,
 *  armF / armB: deg from straight down (+ = forward), elbowF / elbowB: px bend,
 *  mood: 'flat'|'happy'|'shocked'|'squint'|'smug'|'worried'|'angry'|'blink',
 *  mouth: 'smile'|'bigsmile'|'open'|'frown',
 *  lookX: -1..1, lookY: -1..1,
 *  shirtCol, shortsCol, gloves (bool),
 *  palmLeaf, woodStick, t
 */
export function stickman(o) {
  const s = o.s || 1;
  const dir = o.dir || 1;
  const kind = o.kind || 'white';
  const pose = o.pose || 'stand';
  const isBlack = kind === 'black';
  const clothed = kind === 'black' || kind === 'casual';
  const useDarkHands = o.gloves === false || isBlack;
  const ph = o.walk;
  const swing = ph != null ? Math.sin(ph) : 0;
  const t = o.t || 0;

  const bob = pose === 'stand'
    ? (ph != null ? -Math.abs(Math.sin(ph)) * 11 : Math.sin(t * 2.4 + (o.x || 0)) * 2.5)
    : 0;

  ctx.save();
  ctx.translate(o.x, o.y + bob * s);
  ctx.scale(s * dir, s);

  if (pose === 'recline') {
    ctx.rotate(-1.25);
  }

  // Ground oval shadow
  if (pose === 'stand') {
    ell(4, 5, 62, 10, 'rgba(0,0,0,0.22)');
  }

  const hipY = -170;
  const shoY = -305;
  const headY = -376;
  const armShX = clothed ? 38 : 4;

  // 1. Legs
  for (const k of [0, 1]) {
    if (pose === 'sit') {
      // Sitting on hut/ledge: thigh extends forward (+x) and shin dangles downward
      const hx = clothed ? (k === 0 ? -16 : 16) : (k === 0 ? -6 : 6);
      const kick = Math.sin(t * 3.8 + k * 1.5) * 10;
      const kx = hx + (k === 0 ? 32 : 52);
      const ky = hipY + 16;
      const fx = kx + (k === 0 ? -4 : 10) + kick;
      const fy = ky + 76;
      drawLimbCurve(hx, hipY, kx, ky, -8, 10);
      drawLimbCurve(kx, ky, fx, fy, 6, 10);
      drawTeardropShoe(fx, fy, 0.45, 1);
    } else if (pose === 'kneel') {
      const hx = clothed ? (k === 0 ? -16 : 16) : (k === 0 ? -6 : 6);
      const kx = hx + (k === 0 ? 34 : -26);
      const ky = -46;
      const fx = kx - 28;
      const fy = 0;
      drawLimbCurve(hx, hipY, kx, ky, 10, 10);
      drawLimbCurve(kx, ky, fx, fy, -6, 10);
      drawTeardropShoe(fx, fy, 0.2, -1);
    } else {
      const p2 = k === 0 ? ph : (ph != null ? ph + Math.PI : null);
      const sw = p2 != null ? Math.sin(p2) : 0;
      const lf = p2 != null ? Math.max(0, -Math.cos(p2)) : 0;
      const hx = clothed ? (k === 0 ? -18 : 18) : (k === 0 ? -4 : 4);
      const baseFootX = k === 0 ? -24 : 24;
      const fx = baseFootX + sw * 48;
      const fy = -lf * 24;
      const kneeBend = p2 != null ? (sw * 14 + lf * 20) : (k === 0 ? 6 : -6);
      drawLimbCurve(hx, hipY, fx, fy, kneeBend, 10);
      drawTeardropShoe(fx, fy, sw * 0.14, 1);
    }
  }

  // 2. Back arm (compute both endpoints before torso so flared coat can span both hands)
  const armLen = 132;
  const armPos = (shX, deg) => {
    const a = ((deg || 0) * Math.PI) / 180;
    return [shX + Math.sin(a) * armLen, shoY + 12 + Math.cos(a) * armLen];
  };
  const bDeg = o.armB ?? (-18 - swing * 16);
  const bEnd = armPos(-armShX, bDeg);
  const fDeg = o.armF ?? (18 + swing * 16);
  const fEnd = armPos(armShX, fDeg);
  const bWristRad = drawLimbCurve(-armShX, shoY + 12, bEnd[0], bEnd[1], o.elbowB ?? -18, 9.5);
  drawCartoonGlove(bEnd[0], bEnd[1], bWristRad, -1, 1.05, useDarkHands);

  // 3. Torso (pure curved stick OR tapered t-shirt + shorts, plus optional flared coat held wide)
  if (o.spreadCoat) {
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(bEnd[0], bEnd[1] - 10);
    ctx.quadraticCurveTo(0, shoY - 35, fEnd[0], fEnd[1] - 10);
    ctx.lineTo(fEnd[0] * 0.78, hipY + 18);
    ctx.quadraticCurveTo(0, hipY + 28, bEnd[0] * 0.78, hipY + 18);
    ctx.closePath();
    const gCoat = ctx.createLinearGradient(-120, shoY, 120, hipY);
    gCoat.addColorStop(0, '#D7B98E'); gCoat.addColorStop(0.5, '#C29B61'); gCoat.addColorStop(1, '#9E7943');
    ctx.fillStyle = gCoat; ctx.fill();
    ctx.strokeStyle = INK; ctx.lineWidth = 5; ctx.lineJoin = 'round'; ctx.stroke();
    ctx.restore();
  }
  if (clothed) {
    drawTshirtAndShorts(o.shirtCol || C.shirtRed, o.shortsCol || C.shortsBlue);
  } else {
    ctx.strokeStyle = INK; ctx.lineWidth = 11; ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(0, shoY);
    ctx.quadraticCurveTo(-5, (shoY + hipY) * 0.5, 0, hipY);
    ctx.stroke();
  }

  // 4. Front arm
  const fWristRad = drawLimbCurve(armShX, shoY + 12, fEnd[0], fEnd[1], o.elbowF ?? 18, 9.5);
  drawCartoonGlove(fEnd[0], fEnd[1], fWristRad, 1, 1.05, useDarkHands);

  // Optional held props (wood stick / palm leaf / clanging steel pan)
  if (o.woodStick) {
    ctx.save();
    ctx.translate(fEnd[0], fEnd[1]);
    ctx.rotate(((o.stickAngle ?? -35) * Math.PI) / 180);
    line([[0, 20], [0, -145]], '#795548', 13);
    line([[0, 20], [0, -145]], INK, 16);
    line([[0, 20], [0, -145]], '#8D6E63', 9);
    ctx.restore();
  }
  if (o.palmLeaf) {
    ctx.save();
    ctx.translate(fEnd[0], fEnd[1]);
    ctx.rotate(((o.leafAngle ?? -20) * Math.PI) / 180);
    line([[0, 15], [-20, -110]], '#558B2F', 7);
    ctx.beginPath();
    ctx.moveTo(-20, -110);
    ctx.quadraticCurveTo(-95, -130, -145, -75);
    ctx.quadraticCurveTo(-75, -40, -20, -110);
    ctx.closePath();
    ctx.fillStyle = '#43A047'; ctx.fill();
    ctx.strokeStyle = INK; ctx.lineWidth = 4; ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(-20, -110);
    ctx.quadraticCurveTo(45, -135, 95, -80);
    ctx.quadraticCurveTo(30, -45, -20, -110);
    ctx.closePath();
    ctx.fillStyle = '#4CAF50'; ctx.fill();
    ctx.strokeStyle = INK; ctx.lineWidth = 4; ctx.stroke();
    ctx.restore();
  }
  if (o.clanger) {
    ctx.save();
    ctx.translate(fEnd[0], fEnd[1]);
    const hit = Math.sin(t * 22);
    circle(0, -8, 28, '#CFD8DC');
    ctx.strokeStyle = INK; ctx.lineWidth = 4.5; ctx.stroke();
    circle(0, -8, 16, '#90A4AE');
    line([[0, 20], [0, 50]], '#455A64', 9);
    ctx.save(); ctx.rotate(hit * 0.35);
    line([[-44, -28], [-6, -10]], '#ECEFF1', 6.5);
    ctx.restore();
    if (Math.abs(hit) < 0.35) {
      for (let i = 0; i < 6; i++) {
        const a = i * (TAU / 6);
        line([[Math.cos(a) * 32, -8 + Math.sin(a) * 32], [Math.cos(a) * 46, -8 + Math.sin(a) * 46]], '#FFEE58', 4);
      }
    }
    ctx.restore();
  }

  // 5. Head (or back of head wearing a backward painted face mask)
  if (o.backwardMask) {
    drawBackwardHeadMask(0, headY, 64, 68);
  } else {
    drawStickHead(
      0, headY,
      isBlack ? 67 : 64,
      isBlack ? 70 : 68,
      isBlack,
      o.mood || 'flat',
      o.mouth || 'smile',
      o.lookX ?? 0.28,
      o.lookY ?? 0,
      t
    );
    if (o.safariHat) {
      drawSafariHat(0, headY - 40, 64);
    }
  }

  ctx.restore();
}

export function drawBackwardHeadMask(x, y, rx, ry) {
  ctx.save();
  ctx.translate(x, y);
  // Dark hair covering back of stickman's head
  ctx.beginPath(); ctx.ellipse(0, 0, rx + 3, ry + 3, 0, 0, TAU);
  ctx.fillStyle = '#1C1B20'; ctx.fill();
  ctx.strokeStyle = INK; ctx.lineWidth = 5.5; ctx.stroke();
  // Elastic band straps wrapping around head
  line([[-rx * 1.05, -2], [rx * 1.05, -2]], '#ECEFF1', 6.5);
  // Painted face mask strapped on back of head
  ctx.beginPath(); ctx.ellipse(0, 4, rx * 0.78, ry * 0.84, 0, 0, TAU);
  ctx.fillStyle = '#FFF8E1'; ctx.fill();
  ctx.strokeStyle = INK; ctx.lineWidth = 5; ctx.stroke();
  for (const s of [-1, 1]) {
    const ex = s * rx * 0.25, ey = -ry * 0.04;
    ctx.beginPath(); ctx.ellipse(ex, ey, rx * 0.20, ry * 0.24, 0, 0, TAU);
    ctx.fillStyle = '#FFFFFF'; ctx.fill();
    ctx.strokeStyle = INK; ctx.lineWidth = 3.2; ctx.stroke();
    circle(ex, ey, rx * 0.08, INK);
    ctx.strokeStyle = INK; ctx.lineWidth = 3.8;
    ctx.beginPath(); ctx.arc(ex, ey - ry * 0.22, rx * 0.20, Math.PI * 1.15, Math.PI * 1.85); ctx.stroke();
  }
  ctx.fillStyle = '#D32F2F';
  ctx.beginPath(); ctx.ellipse(0, ry * 0.36, rx * 0.22, ry * 0.09, 0, 0, TAU); ctx.fill();
  ctx.strokeStyle = INK; ctx.lineWidth = 3; ctx.stroke();
  ctx.restore();
}

function drawSafariHat(x, y, r) {
  ctx.save();
  ctx.translate(x, y);
  ctx.beginPath(); ctx.arc(0, 0, r * 0.82, Math.PI, 0); ctx.closePath();
  ctx.fillStyle = '#D7BE82'; ctx.fill(); ctx.strokeStyle = INK; ctx.lineWidth = 5; ctx.stroke();
  rr(-r * 0.84, -r * 0.14, r * 1.68, r * 0.18, 4);
  ctx.fillStyle = '#558B2F'; ctx.fill(); ctx.strokeStyle = INK; ctx.lineWidth = 3.5; ctx.stroke();
  ctx.beginPath(); ctx.ellipse(0, 0, r * 1.32, r * 0.24, 0, 0, TAU);
  ctx.fillStyle = '#C8AD6E'; ctx.fill(); ctx.strokeStyle = INK; ctx.lineWidth = 5; ctx.stroke();
  ctx.restore();
}

export function soundShockwaves(x, y, t, col = '#FFEE58') {
  ctx.save();
  for (let i = 0; i < 4; i++) {
    const ph = ((t * 1.9) + i * 0.25) % 1;
    const rad = Math.max(4, 28 + ph * 210);
    ctx.strokeStyle = col;
    ctx.globalAlpha = Math.max(0.02, 0.78 * (1 - ph));
    ctx.lineWidth = 7 * (1 - ph * 0.55);
    ctx.beginPath(); ctx.arc(x, y, rad, -Math.PI * 0.36, Math.PI * 0.36); ctx.stroke();
    ctx.beginPath(); ctx.arc(x, y, rad, Math.PI - Math.PI * 0.36, Math.PI + Math.PI * 0.36); ctx.stroke();
  }
  ctx.restore();
}

// ============================================================================
// 2D CARTOON BENGAL TIGER (cel-shaded smooth cat anatomy matching stickman look)
// ============================================================================
export function cartoonTiger(o) {
  const x = o.x || 960, y = o.y || 820, s = o.s || 1, dir = o.dir || 1, t = o.t || 0;
  const pose = o.pose || 'stalk';
  ctx.save(); ctx.translate(x, y); ctx.scale(s * dir, s);

  ell(0, 8, 195, 22, 'rgba(0,0,0,0.22)');

  // Sinuous striped tail
  const tailSway = Math.sin(t * 4.5) * 0.32;
  ctx.save(); ctx.translate(-155, -120); ctx.rotate(-0.35 + tailSway);
  ctx.strokeStyle = INK; ctx.lineWidth = 22; ctx.lineCap = 'round';
  ctx.beginPath(); ctx.moveTo(0, 0); ctx.quadraticCurveTo(-65, -60, -108, -15); ctx.stroke();
  ctx.strokeStyle = '#FB8C00'; ctx.lineWidth = 15; ctx.stroke();
  circle(-108, -15, 9, INK);
  ctx.restore();

  const crouchY = pose === 'crouch' ? 28 : 0;
  const sw = pose === 'stalk' ? Math.sin(t * 6) * 24 : 0;

  // Back legs
  for (const [lx, k] of [[-125, 1], [-85, -1]]) {
    const fx = lx + sw * k;
    ctx.strokeStyle = INK; ctx.lineWidth = 22; ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(lx, -90 + crouchY); ctx.quadraticCurveTo(lx - 10, -45, fx, -4); ctx.stroke();
    ctx.strokeStyle = '#F57C00'; ctx.lineWidth = 14; ctx.stroke();
    ell(fx + 10, -3, 22, 11, '#FF9800');
    ctx.strokeStyle = INK; ctx.lineWidth = 4.5; ctx.stroke();
  }

  // Muscular curved feline torso
  ctx.save(); ctx.translate(0, crouchY);
  ctx.beginPath();
  ctx.moveTo(-150, -120);
  ctx.bezierCurveTo(-110, -180, 40, -175, 130, -145);
  ctx.bezierCurveTo(170, -125, 165, -60, 120, -48);
  ctx.bezierCurveTo(30, -28, -100, -35, -145, -65);
  ctx.closePath();
  const tg = ctx.createLinearGradient(0, -175, 0, -35);
  tg.addColorStop(0, '#FFA726'); tg.addColorStop(0.65, '#FB8C00'); tg.addColorStop(1, '#FFF8E1');
  ctx.fillStyle = tg; ctx.fill();
  ctx.strokeStyle = INK; ctx.lineWidth = 6.5; ctx.stroke();

  // Organic tapered Bengal stripes
  ctx.fillStyle = INK;
  for (let i = 0; i < 6; i++) {
    const sx = -105 + i * 38;
    ctx.beginPath();
    ctx.moveTo(sx - 5, -155 + (i % 2) * 10);
    ctx.quadraticCurveTo(sx - 12, -105, sx - 6, -68);
    ctx.quadraticCurveTo(sx + 2, -105, sx + 5, -155 + (i % 2) * 10);
    ctx.closePath(); ctx.fill();
  }
  ctx.restore();

  // Front legs
  for (const [lx, k] of [[75, -1], [118, 1]]) {
    const fx = lx + sw * k;
    ctx.strokeStyle = INK; ctx.lineWidth = 22; ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(lx, -85 + crouchY); ctx.quadraticCurveTo(lx + 10, -42, fx, -4); ctx.stroke();
    ctx.strokeStyle = '#FFA726'; ctx.lineWidth = 14; ctx.stroke();
    ell(fx + 10, -3, 24, 12, '#FFA726');
    ctx.strokeStyle = INK; ctx.lineWidth = 4.5; ctx.stroke();
  }

  // Head
  cartoonTigerFace(155, -140 + crouchY, 0.25, t, o.mouth || 'growl');
  ctx.restore();
}

export function cartoonTigerLunge(x, y, s = 1, dir = 1, t = 0) {
  ctx.save(); ctx.translate(x, y); ctx.scale(s * dir, s);
  ell(0, 125, 195, 22, 'rgba(0,0,0,0.22)');
  ctx.rotate(-0.22 + Math.sin(t * 10) * 0.03);

  ctx.strokeStyle = INK; ctx.lineWidth = 20; ctx.lineCap = 'round';
  ctx.beginPath(); ctx.moveTo(-155, -15); ctx.bezierCurveTo(-225, -55, -275, 15, -320, -10); ctx.stroke();
  ctx.strokeStyle = '#FB8C00'; ctx.lineWidth = 13; ctx.stroke();

  for (const hy of [-8, 18]) {
    line([[-130, hy], [-220, hy + 62]], INK, 20);
    line([[-130, hy], [-220, hy + 62]], '#F57C00', 13);
  }

  ctx.beginPath();
  ctx.moveTo(-155, -42);
  ctx.quadraticCurveTo(-25, -90, 110, -60);
  ctx.quadraticCurveTo(175, -30, 155, 18);
  ctx.quadraticCurveTo(35, 60, -130, 30);
  ctx.closePath();
  const tg = ctx.createLinearGradient(0, -85, 0, 50);
  tg.addColorStop(0, '#FFA726'); tg.addColorStop(0.65, '#FB8C00'); tg.addColorStop(1, '#FFF8E1');
  ctx.fillStyle = tg; ctx.fill(); ctx.strokeStyle = INK; ctx.lineWidth = 6.5; ctx.stroke();

  for (let i = 0; i < 6; i++) {
    const sx = -100 + i * 38;
    line([[sx, -62 + (i % 2) * 10], [sx - 12, 22]], INK, 8);
  }

  for (const fy of [-18, 14]) {
    line([[105, fy], [220, fy - 18]], INK, 20);
    line([[105, fy], [220, fy - 18]], '#FFA726', 13);
    ell(230, fy - 18, 22, 13, '#FFA726');
    ctx.strokeStyle = INK; ctx.lineWidth = 4; ctx.stroke();
    for (let c = -1; c <= 1; c++) {
      line([[245, fy - 18 + c * 7], [268, fy - 12 + c * 7]], '#FFFFFF', 4);
    }
  }

  cartoonTigerFace(155, -48, 0.23, t, 'roar');
  ctx.restore();
}

export function cartoonTigerFace(cx, cy, s = 1, t = 0, mouthMode = 'roar') {
  ctx.save(); ctx.translate(cx, cy); ctx.scale(s, s);
  const breath = 1 + 0.03 * Math.sin(t * 7);
  ctx.scale(breath, breath);

  for (const es of [-1, 1]) {
    circle(es * 225, -260, 86, INK);
    circle(es * 225, -260, 68, '#FB8C00');
    circle(es * 225 - es * 14, -270, 28, '#FFFFFF');
  }

  ctx.beginPath(); ctx.ellipse(0, 0, 275, 255, 0, 0, TAU);
  const hg = ctx.createRadialGradient(-45, -50, 35, 0, 0, 290);
  hg.addColorStop(0, '#FFB74D'); hg.addColorStop(0.65, '#FB8C00'); hg.addColorStop(1, '#E65100');
  ctx.fillStyle = hg; ctx.fill(); ctx.strokeStyle = INK; ctx.lineWidth = 14; ctx.stroke();

  // Cheek ruffs
  for (const es of [-1, 1]) {
    ctx.fillStyle = '#FFF8E1';
    ctx.beginPath(); ctx.moveTo(es * 170, -15); ctx.quadraticCurveTo(es * 320, 40, es * 290, 130); ctx.lineTo(es * 195, 175); ctx.closePath();
    ctx.fill(); ctx.strokeStyle = INK; ctx.lineWidth = 9; ctx.stroke();
  }

  // Forehead stripes
  ctx.strokeStyle = INK; ctx.lineWidth = 14; ctx.lineCap = 'round';
  ctx.beginPath(); ctx.moveTo(0, -245); ctx.lineTo(0, -105); ctx.stroke();
  for (const es of [-1, 1]) {
    ctx.beginPath(); ctx.moveTo(es * 75, -225); ctx.lineTo(es * 38, -105); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(es * 165, -175); ctx.lineTo(es * 88, -68); ctx.stroke();
  }

  // Intense yellow cat eyes
  for (const es of [-1, 1]) {
    const ex = es * 110, ey = -55;
    ctx.beginPath(); ctx.ellipse(ex, ey, 48, 36, es * 0.18, 0, TAU);
    ctx.fillStyle = '#FFEE58'; ctx.fill(); ctx.strokeStyle = INK; ctx.lineWidth = 8; ctx.stroke();
    ctx.beginPath(); ctx.ellipse(ex, ey, 14, 24, 0, 0, TAU); ctx.fillStyle = INK; ctx.fill();
  }

  // Muzzle & pink nose
  ctx.beginPath(); ctx.ellipse(0, 88, 114, 80, 0, 0, TAU);
  ctx.fillStyle = '#FFFDE7'; ctx.fill(); ctx.strokeStyle = INK; ctx.lineWidth = 7; ctx.stroke();
  ctx.fillStyle = '#F06292';
  ctx.beginPath(); ctx.moveTo(-36, 36); ctx.lineTo(36, 36); ctx.lineTo(0, 72); ctx.closePath(); ctx.fill();
  ctx.strokeStyle = INK; ctx.lineWidth = 6; ctx.stroke();

  if (mouthMode === 'roar' || mouthMode === 'growl') {
    ctx.beginPath(); ctx.ellipse(0, 152, 86, 66, 0, 0, TAU);
    ctx.fillStyle = '#B71C1C'; ctx.fill(); ctx.strokeStyle = INK; ctx.lineWidth = 7; ctx.stroke();
    ctx.fillStyle = '#EF5350'; ctx.beginPath(); ctx.ellipse(0, 188, 44, 26, 0, 0, TAU); ctx.fill();
    for (const fs of [-1, 1]) {
      ctx.fillStyle = '#FFFFFF';
      ctx.beginPath(); ctx.moveTo(fs * 54 - 14, 112); ctx.lineTo(fs * 54 + 14, 112); ctx.lineTo(fs * 54, 178); ctx.closePath();
      ctx.fill(); ctx.strokeStyle = INK; ctx.lineWidth = 5; ctx.stroke();
    }
  }
  ctx.restore();
}

export function cartoonTigerStrikeDiagram(cx, cy, s = 1, t = 0) {
  cartoonTigerFace(cx, cy, s, t, 'roar');
  const pulse = 1 + 0.12 * Math.sin(t * 10);
  const targets = [
    { label: '1. STRIKE EYES', x: cx - 110 * s, y: cy - 55 * s, lx: cx - 440 * s, ly: cy - 105 * s },
    { label: '2. STRIKE NOSE BRIDGE', x: cx, y: cy + 54 * s, lx: cx + 430 * s, ly: cy + 50 * s },
    { label: '3. STRIKE EARS', x: cx + 225 * s, y: cy - 260 * s, lx: cx + 440 * s, ly: cy - 250 * s }
  ];

  ctx.save();
  targets.forEach((tg) => {
    const r = Math.max(8, 34 * s * pulse);
    ctx.strokeStyle = '#FF1744'; ctx.lineWidth = 5;
    ctx.beginPath(); ctx.arc(tg.x, tg.y, r, 0, TAU); ctx.stroke();
    ctx.strokeStyle = '#FFEE58'; ctx.lineWidth = 2.5;
    ctx.beginPath(); ctx.arc(tg.x, tg.y, Math.max(4, r * 0.55), 0, TAU); ctx.stroke();
    line([[tg.x - r * 1.3, tg.y], [tg.x + r * 1.3, tg.y]], '#FF1744', 3);
    line([[tg.x, tg.y - r * 1.3], [tg.x, tg.y + r * 1.3]], '#FF1744', 3);

    line([[tg.x, tg.y], [tg.lx, tg.ly]], '#FFEE58', 4.5);
    ctx.font = '800 24px Inter';
    const tw = ctx.measureText(tg.label).width + 32;
    const alignLeft = tg.lx < cx;
    const bx = alignLeft ? tg.lx - tw : tg.lx;
    rr(bx, tg.ly - 24, tw, 48, 10);
    ctx.fillStyle = '#D32F2F'; ctx.fill();
    ctx.strokeStyle = INK; ctx.lineWidth = 4; ctx.stroke();
    ctx.fillStyle = '#FFFFFF'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(tg.label, bx + tw / 2, tg.ly);
  });
  ctx.restore();
}
