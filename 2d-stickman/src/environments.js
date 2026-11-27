// Vibrant 2D Cartoon Illustrated Environments (bright sunlit skies, cel-shaded palms, ocean foam, soft sand).
// Automatically adapts to full-bleed 9:16 Shorts (1080x1920) OR 16:9 Landscape (1920x1080) using W and H.
import { ctx, W, H, TAU, lerp, rr, circle, ell, line, C } from './core/lib.js';

const INK = C.ink;

function drawPalmFrond(x, y, ang, len, col = '#43A047', flip = 1) {
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(ang);
  ctx.scale(1, flip);
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.quadraticCurveTo(len * 0.45, -len * 0.28, len, 12);
  ctx.lineTo(len * 0.84, 24);
  ctx.lineTo(len * 0.78, 12);
  ctx.lineTo(len * 0.62, 26);
  ctx.lineTo(len * 0.54, 12);
  ctx.lineTo(len * 0.38, 22);
  ctx.quadraticCurveTo(len * 0.20, 4, 0, 0);
  ctx.closePath();
  ctx.fillStyle = col;
  ctx.fill();
  ctx.strokeStyle = INK;
  ctx.lineWidth = 4;
  ctx.lineJoin = 'round';
  ctx.stroke();
  // Central midrib vein
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.quadraticCurveTo(len * 0.48, -len * 0.14, len * 0.92, 10);
  ctx.strokeStyle = '#1B5E20';
  ctx.lineWidth = 3;
  ctx.stroke();
  ctx.restore();
}

function drawFluffyCloud(x, y, s = 1) {
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(s, s);
  ctx.fillStyle = 'rgba(255, 255, 255, 0.88)';
  circle(-55, 10, 36, 'rgba(255,255,255,0.88)');
  circle(0, -10, 48, 'rgba(255,255,255,0.92)');
  circle(55, 12, 34, 'rgba(255,255,255,0.88)');
  rr(-80, 5, 160, 42, 20);
  ctx.fill();
  ctx.restore();
}

/**
 * Full-bleed Tropical Island Beach background matching the Green Stick Stories Short look.
 * Returns { groundY, shoreY } for character placement.
 */
export function tropicalIslandBg(t = 0) {
  const isVert = H > W;
  const horizonY = isVert ? H * 0.49 : H * 0.46;
  const shoreY = isVert ? H * 0.58 : H * 0.56;
  const groundY = isVert ? H * 0.72 : H * 0.77;

  // 1. Bright cyan-to-light-blue sky
  const sky = ctx.createLinearGradient(0, 0, 0, horizonY);
  sky.addColorStop(0, C.skyTop);
  sky.addColorStop(0.55, C.skyMid);
  sky.addColorStop(1, C.skyBot);
  ctx.fillStyle = sky;
  ctx.fillRect(-100, -100, W + 200, horizonY + 120);

  // 2. Fluffy clouds drifting lazily
  const drift = (t * 14) % (W + 400);
  drawFluffyCloud(((W * 0.22 + drift) % (W + 300)) - 150, H * 0.14, isVert ? 1.15 : 1.35);
  drawFluffyCloud(((W * 0.75 + drift * 0.6) % (W + 300)) - 150, H * 0.24, isVert ? 0.9 : 1.1);

  // 3. Large glowing yellow sun in top-right with soft rays
  const sunX = W * 0.77, sunY = H * (isVert ? 0.16 : 0.18);
  const sunGlow = ctx.createRadialGradient(sunX, sunY, 55, sunX, sunY, isVert ? 340 : 420);
  sunGlow.addColorStop(0, 'rgba(255, 245, 120, 0.95)');
  sunGlow.addColorStop(0.35, 'rgba(255, 241, 118, 0.45)');
  sunGlow.addColorStop(1, 'rgba(255, 241, 118, 0)');
  ctx.fillStyle = sunGlow;
  ctx.fillRect(sunX - 450, sunY - 450, 900, 900);

  // Radial sunburst beams
  ctx.save();
  ctx.translate(sunX, sunY);
  ctx.rotate(t * 0.04);
  ctx.fillStyle = 'rgba(255, 250, 180, 0.22)';
  for (let i = 0; i < 8; i++) {
    ctx.rotate(TAU / 8);
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.lineTo(-42, -380);
    ctx.lineTo(42, -380);
    ctx.closePath();
    ctx.fill();
  }
  ctx.restore();
  circle(sunX, sunY, isVert ? 82 : 95, '#FFF176');
  circle(sunX, sunY, isVert ? 66 : 76, '#FFEE58');

  // Distant tiny seagull silhouettes
  ctx.strokeStyle = 'rgba(255,255,255,0.85)';
  ctx.lineWidth = 3.5; ctx.lineCap = 'round';
  for (const [bx, by, bs] of [[W * 0.48, H * 0.09, 1.0], [W * 0.54, H * 0.12, 0.8], [W * 0.43, H * 0.13, 0.7]]) {
    const fl = Math.sin(t * 5 + bx) * 4;
    ctx.beginPath();
    ctx.moveTo(bx - 14 * bs, by - 4 + fl);
    ctx.quadraticCurveTo(bx - 6 * bs, by - 10, bx, by);
    ctx.quadraticCurveTo(bx + 6 * bs, by - 10, bx + 14 * bs, by - 4 + fl);
    ctx.stroke();
  }

  // 4. Turquoise-to-blue ocean
  const sea = ctx.createLinearGradient(0, horizonY, 0, shoreY + 120);
  sea.addColorStop(0, C.seaTop);
  sea.addColorStop(1, C.seaBot);
  ctx.fillStyle = sea;
  ctx.fillRect(-100, horizonY, W + 200, shoreY - horizonY + 150);

  // Distant palm island silhouette on the right horizon
  const islX = W * 0.86;
  ctx.fillStyle = '#0D5E63';
  ctx.beginPath();
  ctx.ellipse(islX, horizonY + 4, 95, 16, 0, Math.PI, 0);
  ctx.fill();
  for (const [px, ph] of [[-28, 42], [12, 54], [40, 36]]) {
    line([[islX + px, horizonY], [islX + px + 4, horizonY - ph]], '#0D5E63', 5);
    for (let a = -2; a <= 2; a++) {
      line([[islX + px + 4, horizonY - ph], [islX + px + 4 + a * 12, horizonY - ph + Math.abs(a) * 5]], '#0D5E63', 3.5);
    }
  }

  // 5. Golden beach sand area with curved shoreline + ocean seafoam at bottom/back
  const waveShift = Math.sin(t * 2.1) * 10;
  // White seafoam strip along top shoreline
  ctx.fillStyle = 'rgba(255,255,255,0.88)';
  ctx.beginPath();
  ctx.moveTo(-100, shoreY + waveShift);
  for (let x = -100; x <= W + 150; x += 90) {
    ctx.quadraticCurveTo(x + 45, shoreY - 14 + waveShift, x + 90, shoreY + waveShift);
  }
  ctx.lineTo(W + 150, H + 100);
  ctx.lineTo(-100, H + 100);
  ctx.closePath();
  ctx.fill();

  // Warm sand gradient overlapping seafoam
  const sand = ctx.createLinearGradient(0, shoreY + 12, 0, H);
  sand.addColorStop(0, '#DFB668');
  sand.addColorStop(0.2, C.sandTop);
  sand.addColorStop(0.82, C.sandBot);
  sand.addColorStop(1, '#CFA049');
  ctx.fillStyle = sand;
  ctx.beginPath();
  ctx.moveTo(-100, shoreY + 16 + waveShift * 0.7);
  for (let x = -100; x <= W + 150; x += 120) {
    ctx.quadraticCurveTo(x + 60, shoreY + 4 + waveShift * 0.7, x + 120, shoreY + 16 + waveShift * 0.7);
  }
  ctx.lineTo(W + 150, H + 100);
  ctx.lineTo(-100, H + 100);
  ctx.closePath();
  ctx.fill();

  // Foreground ocean surf lapping at the very bottom edge (just like the Short's bottom border!)
  if (isVert) {
    const botSeaY = H * 0.89 + Math.sin(t * 2.5) * 8;
    ctx.fillStyle = '#FFFFFF';
    ctx.beginPath();
    ctx.moveTo(-50, botSeaY);
    for (let x = -50; x <= W + 100; x += 110) {
      ctx.quadraticCurveTo(x + 55, botSeaY - 22, x + 110, botSeaY);
    }
    ctx.lineTo(W + 100, H + 50); ctx.lineTo(-50, H + 50); ctx.closePath(); ctx.fill();

    ctx.fillStyle = C.seaBot;
    ctx.beginPath();
    ctx.moveTo(-50, botSeaY + 20);
    for (let x = -50; x <= W + 100; x += 110) {
      ctx.quadraticCurveTo(x + 55, botSeaY - 2, x + 110, botSeaY + 20);
    }
    ctx.lineTo(W + 100, H + 50); ctx.lineTo(-50, H + 50); ctx.closePath(); ctx.fill();
  }

  // Subtle sand pebbles & footprints
  for (const [rx, ry, rrSize] of [[W * 0.14, groundY + 75, 22], [W * 0.21, groundY + 95, 13], [W * 0.82, groundY + 55, 16]]) {
    ell(rx, ry + 5, rrSize + 4, rrSize * 0.45, 'rgba(0,0,0,0.16)');
    ell(rx, ry, rrSize, rrSize * 0.65, '#78848C');
    ctx.strokeStyle = INK; ctx.lineWidth = 3; ctx.stroke();
    ell(rx - rrSize * 0.25, ry - rrSize * 0.2, rrSize * 0.4, rrSize * 0.2, '#9CA7AF');
  }

  // 6. Lush Palm Tree framing the top-left
  const palmBaseX = -20;
  const palmTopX = isVert ? W * 0.16 : W * 0.10;
  const palmTopY = isVert ? H * 0.23 : H * 0.20;
  ctx.strokeStyle = INK; ctx.lineWidth = 42; ctx.lineCap = 'round';
  ctx.beginPath(); ctx.moveTo(palmBaseX, horizonY + 90); ctx.quadraticCurveTo(W * 0.04, H * 0.36, palmTopX, palmTopY); ctx.stroke();
  ctx.strokeStyle = C.palmTrunk; ctx.lineWidth = 32;
  ctx.beginPath(); ctx.moveTo(palmBaseX, horizonY + 90); ctx.quadraticCurveTo(W * 0.04, H * 0.36, palmTopX, palmTopY); ctx.stroke();

  // Palm frond crown swaying gently
  const sway = Math.sin(t * 1.4) * 0.04;
  drawPalmFrond(palmTopX, palmTopY, -2.5 + sway, 220, '#388E3C', -1);
  drawPalmFrond(palmTopX, palmTopY, -0.5 + sway, 240, '#43A047', 1);
  drawPalmFrond(palmTopX, palmTopY, 0.2 + sway, 250, '#4CAF50', 1);
  drawPalmFrond(palmTopX, palmTopY, 1.0 + sway, 210, '#2E7D32', 1);
  drawPalmFrond(palmTopX, palmTopY, 2.4 + sway, 200, '#388E3C', -1);

  return { groundY, shoreY, horizonY };
}

/**
 * Wooden-pole & palm-frond A-frame shelter hut (matching the hut in 2 Stickmen Stranded on an Island).
 * Returns { roofY } so a stickman can sit right on top of the hut!
 */
export function drawPalmHut(cx, groundY, s = 1, t = 0) {
  ctx.save();
  ctx.translate(cx, groundY);
  ctx.scale(s, s);

  // Soft ground shadow inside/underneath hut
  ell(0, 6, 260, 26, 'rgba(0,0,0,0.22)');

  // Dark interior recess
  ctx.beginPath();
  ctx.moveTo(-210, 0);
  ctx.lineTo(-40, -210);
  ctx.lineTo(140, -210);
  ctx.lineTo(220, 0);
  ctx.closePath();
  ctx.fillStyle = 'rgba(20, 42, 22, 0.72)';
  ctx.fill();

  // Wooden support poles lashed together
  const poles = [
    [[-210, 10], [-190, -170]],
    [[-130, 8], [-150, -180]],
    [[210, 10], [180, -165]],
    [[-230, -155], [215, -155]],
  ];
  for (const [p0, p1] of poles) {
    line([p0, p1], INK, 22);
    line([p0, p1], '#795548', 14);
  }
  // Rope lashings at joints
  for (const [lx, ly] of [[-194, -155], [182, -155]]) {
    rr(lx - 10, ly - 10, 20, 20, 4);
    ctx.fillStyle = '#D7CCC8'; ctx.fill();
    ctx.strokeStyle = INK; ctx.lineWidth = 3; ctx.stroke();
  }

  // Lush layered green palm fronds forming thatched roof
  const sway = Math.sin(t * 2) * 0.02;
  for (let i = -3; i <= 3; i++) {
    drawPalmFrond(i * 46, -215, Math.PI * 0.68 + i * 0.08 + sway, 220, i % 2 === 0 ? '#2E7D32' : '#43A047', -1);
    drawPalmFrond(i * 42, -220, Math.PI * 0.25 + i * 0.06 + sway, 210, i % 2 === 0 ? '#4CAF50' : '#388E3C', 1);
  }

  // Scattered firewood logs on sand near hut left
  for (const [lx, ly, ang] of [[-240, 28, -0.08], [-210, 44, 0.12]]) {
    ctx.save(); ctx.translate(lx, ly); ctx.rotate(ang);
    rr(-70, -10, 140, 20, 8);
    ctx.fillStyle = '#795548'; ctx.fill();
    ctx.strokeStyle = INK; ctx.lineWidth = 4.5; ctx.stroke();
    circle(70, 0, 10, '#D7CCC8');
    ctx.stroke();
    ctx.restore();
  }

  ctx.restore();
  return { roofY: groundY - 210 * s };
}

/**
 * Bright, illustrated 2D cartoon jungle clearing (matching stickman storybook look).
 */
export function cartoonJungleBg(t = 0, mode = 'sunny') {
  const groundY = H * 0.76;

  // Sky / upper canopy glow
  const sky = ctx.createLinearGradient(0, 0, 0, groundY);
  if (mode === 'dusk') {
    sky.addColorStop(0, '#FF8F00');
    sky.addColorStop(0.5, '#FFB74D');
    sky.addColorStop(1, '#FFE082');
  } else {
    sky.addColorStop(0, '#4FC3F7');
    sky.addColorStop(0.5, '#81D4FA');
    sky.addColorStop(1, '#C8E6C9');
  }
  ctx.fillStyle = sky;
  ctx.fillRect(-100, -100, W + 200, groundY + 120);

  // Soft distant teal-green hills
  ctx.fillStyle = mode === 'dusk' ? '#5D4037' : '#66BB6A';
  ctx.beginPath();
  ctx.moveTo(-100, groundY);
  for (let x = -100; x <= W + 200; x += 280) {
    ctx.quadraticCurveTo(x + 140, groundY - 190, x + 280, groundY - 40);
  }
  ctx.lineTo(W + 200, groundY + 50);
  ctx.lineTo(-100, groundY + 50);
  ctx.closePath();
  ctx.fill();

  // Midground rounded tree trunks & lush cel-shaded canopies
  for (let i = 0; i < 6; i++) {
    const tx = W * (0.08 + i * 0.18);
    const ty = groundY;
    const trW = 36 + (i % 2) * 10;
    line([[tx, ty], [tx + (i % 2 ? 18 : -18), ty - 420]], INK, trW + 10);
    line([[tx, ty], [tx + (i % 2 ? 18 : -18), ty - 420]], '#6D4C41', trW);
  }

  // Sunlit green canopy puffs
  for (let i = 0; i < 8; i++) {
    const cx = W * (-0.02 + i * 0.15);
    const cy = H * 0.16 + (i % 2) * 40;
    circle(cx, cy, 130, '#2E7D32');
    circle(cx - 35, cy - 25, 105, '#43A047');
    circle(cx + 35, cy - 20, 95, '#66BB6A');
  }

  // Golden sunbeams filtering down
  ctx.save();
  ctx.globalAlpha = 0.16;
  for (let i = 0; i < 5; i++) {
    const rx = W * (0.12 + i * 0.20);
    ctx.beginPath();
    ctx.moveTo(rx, 0);
    ctx.lineTo(rx + 220, groundY);
    ctx.lineTo(rx + 360, groundY);
    ctx.lineTo(rx + 90, 0);
    ctx.closePath();
    ctx.fillStyle = '#FFF9C4';
    ctx.fill();
  }
  ctx.restore();

  // Bright grassy meadow & warm dirt trail
  const floorG = ctx.createLinearGradient(0, groundY, 0, H);
  floorG.addColorStop(0, '#7CB342');
  floorG.addColorStop(0.22, '#558B2F');
  floorG.addColorStop(1, '#33691E');
  ctx.fillStyle = floorG;
  ctx.fillRect(-100, groundY, W + 200, H - groundY + 100);

  // Warm dirt footpath across center
  ctx.fillStyle = '#E0B973';
  ctx.beginPath();
  ctx.ellipse(W * 0.5, groundY + 75, W * 0.55, 65, 0, 0, TAU);
  ctx.fill();
  line([[-100, groundY], [W + 100, groundY]], INK, 6);

  // Rounded foreground bushes & cel-shaded rocks
  for (const [bx, r] of [[W * 0.06, 75], [W * 0.94, 82]]) {
    circle(bx, groundY + 10, r, '#2E7D32');
    ctx.strokeStyle = INK; ctx.lineWidth = 5; ctx.stroke();
    circle(bx - 18, groundY - 12, r * 0.65, '#4CAF50');
  }
  return { groundY };
}

/**
 * Bright sunny Bamboo & Tall Grass ambush corridor.
 */
export function cartoonBambooAmbushBg(t = 0) {
  const groundY = H * 0.76;
  const sky = ctx.createLinearGradient(0, 0, 0, groundY);
  sky.addColorStop(0, '#81D4FA');
  sky.addColorStop(0.6, '#DCEDC8');
  sky.addColorStop(1, '#AED581');
  ctx.fillStyle = sky;
  ctx.fillRect(-100, -100, W + 200, groundY + 120);

  // Sunlit emerald bamboo stalks with crisp dark ink nodes
  for (let i = 0; i < 16; i++) {
    const bx = W * (-0.02 + i * 0.072);
    const sway = Math.sin(t * 1.2 + i * 0.6) * 10;
    const bw = 20 + (i % 3) * 6;
    ctx.strokeStyle = INK; ctx.lineWidth = bw + 8; ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(bx, groundY + 20); ctx.quadraticCurveTo(bx + sway * 0.5, groundY * 0.5, bx + sway, -40); ctx.stroke();
    ctx.strokeStyle = i % 2 === 0 ? '#689F38' : '#8BC34A'; ctx.lineWidth = bw;
    ctx.beginPath(); ctx.moveTo(bx, groundY + 20); ctx.quadraticCurveTo(bx + sway * 0.5, groundY * 0.5, bx + sway, -40); ctx.stroke();
    for (let ny = 80; ny < groundY; ny += 130) {
      const nx = bx + sway * (1 - ny / groundY);
      line([[nx - bw * 0.6, ny], [nx + bw * 0.6, ny]], INK, 4.5);
    }
  }

  // Sunlit meadow floor
  const floorG = ctx.createLinearGradient(0, groundY, 0, H);
  floorG.addColorStop(0, '#9CCC65'); floorG.addColorStop(1, '#558B2F');
  ctx.fillStyle = floorG; ctx.fillRect(-100, groundY, W + 200, H - groundY + 100);
  line([[-100, groundY], [W + 100, groundY]], INK, 6);

  // Tall swaying golden grass stalks
  for (let i = 0; i < 36; i++) {
    const gx = W * (-0.02 + i * 0.03);
    const gh = 110 + (i % 4) * 35;
    const sw = Math.sin(t * 2.2 + i * 0.5) * 18;
    ctx.strokeStyle = i % 2 === 0 ? '#AFB42B' : '#7CB342';
    ctx.lineWidth = 7; ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(gx, groundY + 10); ctx.quadraticCurveTo(gx + sw * 0.5, groundY - gh * 0.5, gx + sw, groundY - gh); ctx.stroke();
  }
  return { groundY };
}

/**
 * Bright Sundarbans Mangrove Riverbank (cyan sky, emerald river, arching prop roots).
 */
export function cartoonMangroveRiverBg(t = 0) {
  const horizonY = H * 0.48;
  const shoreY = H * 0.60;
  const groundY = H * 0.76;

  // Bright tropical sky
  const sky = ctx.createLinearGradient(0, 0, 0, horizonY);
  sky.addColorStop(0, '#4FC3F7'); sky.addColorStop(1, '#E0F7FA');
  ctx.fillStyle = sky; ctx.fillRect(-100, -100, W + 200, horizonY + 120);
  drawFluffyCloud(W * 0.25, H * 0.16, 1.15);
  drawFluffyCloud(W * 0.72, H * 0.20, 1.0);

  // Distant emerald mangrove treeline
  ctx.fillStyle = '#2E7D32';
  for (let i = 0; i < 12; i++) {
    circle(W * (i * 0.1), horizonY - 20, 110, '#2E7D32');
  }

  // Sparkling teal river water
  const river = ctx.createLinearGradient(0, horizonY, 0, shoreY + 40);
  river.addColorStop(0, '#00ACC1'); river.addColorStop(1, '#26C6DA');
  ctx.fillStyle = river; ctx.fillRect(-100, horizonY, W + 200, shoreY - horizonY + 80);
  for (let i = 0; i < 14; i++) {
    const wx = ((i * 170 + t * 25) % W);
    const wy = horizonY + 25 + (i % 4) * 20;
    line([[wx, wy], [wx + 65, wy]], 'rgba(255,255,255,0.45)', 3.5);
  }

  // Muddy-green riverbank
  const bank = ctx.createLinearGradient(0, shoreY, 0, H);
  bank.addColorStop(0, '#8D6E63'); bank.addColorStop(0.25, '#6D4C41'); bank.addColorStop(1, '#4E342E');
  ctx.fillStyle = bank;
  ctx.fillRect(-100, shoreY, W + 200, H - shoreY + 100);
  line([[-100, shoreY], [W + 100, shoreY]], INK, 5.5);

  // Arching mangrove prop roots on both sides
  for (const sx of [W * 0.08, W * 0.92]) {
    line([[sx, H * 0.12], [sx, groundY]], INK, 32);
    line([[sx, H * 0.12], [sx, groundY]], '#6D4C41', 22);
    for (const r of [-2, -1, 1, 2]) {
      ctx.strokeStyle = INK; ctx.lineWidth = 16; ctx.lineCap = 'round';
      ctx.beginPath(); ctx.moveTo(sx, groundY - 240); ctx.quadraticCurveTo(sx + r * 55, groundY - 120, sx + r * 95, groundY + 25); ctx.stroke();
      ctx.strokeStyle = '#795548'; ctx.lineWidth = 10; ctx.stroke();
    }
    circle(sx, H * 0.14, 120, '#388E3C');
    circle(sx - 55, H * 0.17, 90, '#4CAF50');
    circle(sx + 55, H * 0.17, 90, '#43A047');
  }
  return { groundY, shoreY };
}
