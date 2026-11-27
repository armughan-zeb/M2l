// Story: every function is a pure function of the frame number f (30 fps).
// Set ENVIRONMENT to 'office' | 'jungle' | 'battlefield' | 'tundra' | 'lab' | 'park' | custom builder fn
export const ENVIRONMENT = 'park';
export const N = 1963;
const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const seg = (f, a, b) => clamp((f - a) / (b - a));
const lerp = (a, b, t) => a + (b - a) * t;
const L3 = (a, b, t) => [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];
const ADD = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const ss = (t) => t * t * t * (t * (t * 6 - 15) + 10);
const easeOut = (t) => 1 - (1 - t) ** 3;
const easeIn = (t) => t * t * t;
const back = (t, s = 1.8) => { const u = t - 1; return 1 + u * u * ((s + 1) * u + s); };
const pop = (f, a, d = 10) => (f < a ? 0 : back(seg(f, a, a + d)));
const rnd = (s) => { const x = Math.sin(s * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };
const E = { ss, out: easeOut, in: easeIn, lin: (t) => t };

// ---------- places ----------
const LEO_SEAT = [-0.42, 0, 0.3], MAYA_SEAT = [0.42, 0, 0.3], LEO_STAND = [-0.42, 0, 0.62];
const PIER_Y = 0.4, PIER_END = [3.2, PIER_Y, -16.4], PIER_MEM = [2.88, PIER_Y, -10.5];
export const BRAIN = [0, 24.9, -34];
export const HEARTP = [0, 24.8, -32.4];
// thread ends: Maya's portrait (below), comfort (left), happiness (top), security (right)
export const NODES = [[0, 21.35, -32.6], [-1.75, 27.6, -33.0], [0, 28.45, -33.0], [1.75, 27.6, -33.0]];
const BAL_BASE = [2.81, 2.68, -16.36]; // knot position at the release (measured from the hand)

export const SHOTS = [
  { name: 'A hook', a: 0, b: 100 }, { name: 'B memory', a: 100, b: 305 }, { name: 'C walk away', a: 305, b: 436 },
  { name: 'D losing', a: 436, b: 632 }, { name: 'E mind', a: 632, b: 752 }, { name: 'F associate', a: 752, b: 952 },
  { name: 'G disappears', a: 952, b: 1176 }, { name: 'H miss', a: 1176, b: 1352 }, { name: 'I hold on', a: 1352, b: 1512 },
  { name: 'J healing', a: 1512, b: 1700 }, { name: 'K let go', a: 1700, b: 1850 }, { name: 'L forward', a: 1850, b: 1963 },
];
export const shotAt = (f) => SHOTS.find((s) => f >= s.a && f < s.b) || SHOTS[SHOTS.length - 1];

// ---------- camera ----------
// keys: [f, pos, look, fov, roll?, ease?]; ease applies to the move INTO that key.
function camFromKeys(keys, f, creep = 0.012) {
  let k0 = keys[0], k1 = keys[0];
  for (let i = 0; i < keys.length; i++) { if (keys[i][0] <= f) { k0 = keys[i]; k1 = keys[i + 1] || keys[i]; } }
  if (f < keys[0][0]) k1 = k0;
  const t = k1 === k0 ? 0 : (E[k1[5] || 'ss'])(seg(f, k0[0], k1[0]));
  const pos = L3(k0[1], k1[1], t), look = L3(k0[2], k1[2], t);
  pos[0] += creep * Math.sin(f * 0.031); pos[1] += creep * 0.7 * Math.sin(f * 0.043 + 1); pos[2] += creep * Math.cos(f * 0.027);
  return { pos, look, fov: lerp(k0[3], k1[3], t), roll: lerp(k0[4] || 0, k1[4] || 0, t) };
}

// ---------- cinematic camera angle helpers ----------
/** Closeup camera framing targeted on an actor's face/chest with natural headroom */
export function camCloseup(actorPos, opts = {}) {
  const dist = opts.dist ?? 1.55;
  const ang = opts.angle ?? 0;
  const headY = actorPos[1] + (opts.headHeight ?? 1.62);
  const elev = opts.elev ?? 0.05;
  const pos = [actorPos[0] + Math.sin(ang) * dist, headY + elev, actorPos[2] + Math.cos(ang) * dist];
  const look = [actorPos[0], headY - 0.06, actorPos[2]];
  return { pos, look, fov: opts.fov ?? 34, roll: opts.roll || 0 };
}

/** Over-the-shoulder (OTS) camera placed behind foreground actor's shoulder looking at subject */
export function camOTS(subjectPos, foregroundPos, opts = {}) {
  const dx = subjectPos[0] - foregroundPos[0], dz = subjectPos[2] - foregroundPos[2];
  const dist = Math.hypot(dx, dz) || 1;
  const nx = dx / dist, nz = dz / dist;
  const shoulderOffset = opts.shoulder === 'left' ? -0.28 : 0.28;
  const behindDist = opts.behindDist ?? 0.55;
  const pos = [
    foregroundPos[0] - nx * behindDist - nz * shoulderOffset,
    foregroundPos[1] + 1.60 + (opts.elev ?? 0.08),
    foregroundPos[2] - nz * behindDist + nx * shoulderOffset
  ];
  const look = [subjectPos[0], subjectPos[1] + 1.52, subjectPos[2]];
  return { pos, look, fov: opts.fov ?? 32, roll: opts.roll || 0 };
}

/** Medium shot (waist up, dist ~ 2.6 - 3.2 m) */
export function camMedium(targetPos, opts = {}) {
  const dist = opts.dist ?? 2.8;
  const ang = opts.angle ?? 0;
  const pos = [targetPos[0] + Math.sin(ang) * dist, targetPos[1] + (opts.camY ?? 1.35), targetPos[2] + Math.cos(ang) * dist];
  const look = [targetPos[0], targetPos[1] + (opts.lookY ?? 1.25), targetPos[2]];
  return { pos, look, fov: opts.fov ?? 36, roll: opts.roll || 0 };
}

/** Wide environment reveal shot (dist ~ 5 - 8 m) */
export function camWide(centerPos, opts = {}) {
  const dist = opts.dist ?? 6.0;
  const ang = opts.angle ?? 0;
  const pos = [centerPos[0] + Math.sin(ang) * dist, centerPos[1] + (opts.camY ?? 1.9), centerPos[2] + Math.cos(ang) * dist];
  const look = [centerPos[0], centerPos[1] + (opts.lookY ?? 1.1), centerPos[2]];
  return { pos, look, fov: opts.fov ?? 42, roll: opts.roll || 0 };
}

function shake(c, f, at, amp = 0.05, dur = 7) {
  if (f < at || f >= at + dur) return c;
  const k = (1 - (f - at) / dur) * amp * ((f - at) % 2 ? -1 : 1);
  c.pos[1] += k; c.look[1] += k * 0.6; c.roll += k * 40;
  return c;
}
// the released balloon (K): rises and drifts out over the lake
export function balloonFree(f) {
  const t = Math.max(0, f - 1748);
  return [BAL_BASE[0] + 0.012 * t + 0.15 * Math.sin(t * 0.05), BAL_BASE[1] + 16 * (1 - Math.exp(-t / 75)) + 0.025 * t, BAL_BASE[2] - 0.07 * t];
}
const leoWalkZ = (f) => lerp(-16.3, -12.5, seg(f, 1856, 1963));

export function camAt(f) {
  const s = shotAt(f).name[0];
  let c;
  if (s === 'A') c = camFromKeys([
    [0, [-0.5, 1.42, 2.25], [-0.42, 1.48, 0.3], 34],
    [100, [0.15, 1.3, 3.7], [0.0, 1.25, 0.2], 38]], f);
  else if (s === 'B') c = camFromKeys([
    [100, [1.95, 1.25, 2.5], [0.0, 1.2, 0.3], 36],
    [305, [1.35, 1.28, 1.85], [-0.12, 1.22, 0.3], 33]], f);
  else if (s === 'C') c = camFromKeys([ // along the shore path: Leo foreground right, Maya walking away into the distance
    [305, [3.6, 1.5, 1.25], [-3.0, 1.15, 1.2], 40],
    [436, [3.75, 1.62, 1.2], [-3.5, 1.1, 1.3], 39]], f);
  else if (s === 'D') { c = camFromKeys([
    [436, [0.55, 1.5, 3.05], [-0.42, 1.48, 0.62], 36],
    [632, [-1.55, 1.56, 2.75], [-0.42, 1.52, 0.62], 36]], f); shake(c, f, 540, 0.03, 6); }
  else if (s === 'E') c = camFromKeys([
    [632, [-1.55, 1.56, 2.75], [-0.42, 1.52, 0.62], 36],
    [646, [-1.5, 1.6, 2.7], [-0.42, 1.9, 0.5], 36],
    [700, [0, 9, 3.5], [0, 20, -30], 40, 0, 'ss'],
    [752, [0, 24.6, -20.2], [0, 24.6, -33], 40, 0, 'ss']], f);
  else if (s === 'F') c = camFromKeys([
    [752, [0, 24.6, -20.2], [0, 24.6, -33], 40],
    [952, [1.5, 24.8, -21.2], [0, 24.6, -33], 38]], f);
  else if (s === 'G') {
    c = camFromKeys([
      [952, [1.5, 24.8, -21.2], [0, 24.6, -33], 38],
      [1008, [1.2, 24.7, -20.0], [0, 24.4, -33], 41],
      [1048, [3.6, 5.4, 8.2], [-0.2, 0.8, 0.0], 40, 0, 'ss'],
      [1108, [3.0, 4.9, 7.2], [-0.2, 0.8, 0.0], 40],
      [1160, [0.2, 1.35, 3.3], [0.0, 0.95, 0.3], 42, 0, 'ss'],
      [1176, [0.18, 1.34, 3.2], [0.0, 0.95, 0.3], 42]], f);
    shake(c, f, 982, 0.05, 8);
  } else if (s === 'H') {
    if (f < 1262) c = camFromKeys([[1176, [-0.1, 1.3, 1.95], [-0.42, 1.12, 0.3], 32], [1262, [-0.2, 1.27, 1.7], [-0.42, 1.12, 0.3], 31]], f);
    else c = camFromKeys([[1262, [3.3, 1.45, -6.6], [3.15, 1.35, -10.6], 38], [1340, [2.98, 1.6, -8.7], [2.9, 1.6, -10.5], 33, 0, 'ss'], [1352, [2.97, 1.6, -8.75], [2.9, 1.6, -10.5], 33]], f);
  } else if (s === 'I') c = camFromKeys([
    [1352, [3.6, 1.0, -14.5], [3.2, 1.55, -16.4], 36],
    [1512, [2.65, 1.05, -14.6], [3.2, 1.6, -16.4], 35]], f);
  else if (s === 'J') c = camFromKeys([
    [1512, [3.25, 1.15, -13.0], [3.2, 1.45, -17], 40],
    [1700, [3.3, 1.15, -14.0], [3.2, 1.45, -16.6], 38]], f);
  else if (s === 'K') {
    const bp = balloonFree(Math.max(f, 1748));
    const up = ss(seg(f, 1752, 1780)) * (1 - ss(seg(f, 1784, 1806)));
    const look = L3([3.2, 1.55, -16.4], [bp[0], bp[1] + 0.3, bp[2]], up);
    const pos = L3([3.3, 1.15, -14.0], [3.3, 1.5, -13.5], up);
    const fov = lerp(lerp(38, 35, seg(f, 1700, 1752)), 44, up) - 2 * seg(f, 1806, 1850);
    c = { pos, look, fov, roll: 0 };
    pos[0] += 0.012 * Math.sin(f * 0.031); pos[1] += 0.008 * Math.sin(f * 0.043 + 1);
  } else { // L
    const z = leoWalkZ(f);
    const track = { pos: [3.25, 1.12, z + 2.8], look: [3.2, 1.42, z - 1.0] };
    const t = ss(seg(f, 1928, 1963));
    c = { pos: L3(track.pos, [3.9, 2.3, -7.4], t), look: L3(track.look, [3.2, 2.5, -18], t), fov: lerp(38, 46, t), roll: 0 };
  }
  return c;
}

// ---------- light / grade / lens ----------
export function lightFor(f) {
  const s = shotAt(f).name[0];
  if ('ABCD'.includes(s)) return { preset: 'golden', target: [0, 0, 0.5] };
  if (s === 'E') return { preset: 'golden', to: 'night', mix: ss(seg(f, 640, 694)), target: [0, 0, 0.5] };
  if (s === 'F' || s === 'G' || (s === 'H' && f < 1262)) return { preset: 'night', target: [0, 0, 0] };
  if (s === 'H') return { preset: 'day', target: [3.2, 0, -10.5] };
  if (s === 'I') return { preset: 'dusk', target: [3.2, 0, -15] };
  const el = f < 1700 ? lerp(-4, 4, ss(seg(f, 1515, 1640))) : lerp(4, 9, seg(f, 1700, 1963));
  if (s === 'J') return { preset: 'dusk', to: 'dawn', mix: ss(seg(f, 1518, 1612)), el, target: [3.2, 0, -15] };
  return { preset: 'dawn', el, target: [3.2, 0, -15] };
}
export const flashAt = (f) => (f >= 1262 && f < 1268 ? 1 - (f - 1262) / 6 : f >= 1352 && f < 1358 ? 1 - (f - 1352) / 6 : 0);
export function dofAt(f, cam) {
  const s = shotAt(f).name[0], p = cam.position;
  const dist = (q) => Math.hypot(q[0] - p.x, q[1] - p.y, q[2] - p.z);
  if (s === 'A') return { focus: dist([-0.42, 1.3, 0.3]), cInf: lerp(10, 6, seg(f, 0, 100)) };
  if (s === 'B') return { focus: dist(f < 165 ? [-0.42, 1.3, 0.3] : [0, 1.3, 0.3]), cInf: 7 };
  if (s === 'D') return { focus: dist([-0.42, 1.45, 0.62]), cInf: 8 };
  if (s === 'E' && f < 660) return { focus: dist([-0.42, 1.45, 0.62]), cInf: 8 * (1 - seg(f, 640, 660)) };
  if (s === 'G' && f >= 1130) return { focus: dist([-0.42, 1.25, 0.3]), cInf: 8 * seg(f, 1130, 1160) };
  if (s === 'H') return f < 1262 ? { focus: dist([-0.42, 1.15, 0.3]), cInf: 10 } : { focus: dist([2.95, 1.5, -10.5]), cInf: lerp(6, 10, seg(f, 1262, 1340)) };
  if (s === 'I' || s === 'J') return { focus: dist([3.2, 1.5, -16.4]), cInf: 8 };
  if (s === 'K') return { focus: dist([3.2, 1.5, -16.4]), cInf: 8 * (1 - ss(seg(f, 1752, 1770)) * (1 - ss(seg(f, 1786, 1806)))) };
  if (s === 'L' && f < 1935) return { focus: dist([3.2, 1.45, leoWalkZ(f)]), cInf: 7 };
  return { focus: 10, cInf: 0 };
}

// ---------- world state ----------
const sway = (f, k = 1) => [0.07 * k * Math.sin(f * 0.045), 0.04 * k * Math.sin(f * 0.033 + 1)];
export function worldAt(f) {
  const s = shotAt(f).name[0];
  const w = { balloon: null, photos: [], thread: null, mind: null };
  // ---- balloon ----
  if ('ABCD'.includes(s) || (s === 'E' && f < 668)) w.balloon = { mode: 'held', actor: 'leo', side: 'L', lift: 0.78, sway: sway(f) };
  if (s === 'G' && f >= 1030) w.balloon = { mode: 'held', actor: 'leo', side: 'L', lift: 0.55, sway: sway(f, 0.5), tilt: 0.25 };
  if (s === 'H' && f < 1262) w.balloon = { mode: 'held', actor: 'leo', side: 'L', lift: 0.55, sway: sway(f, 0.5), tilt: 0.25 };
  if (s === 'H' && f >= 1262) w.balloon = { mode: 'held', actor: 'pleo', side: 'L', lift: 0.85, sway: sway(f * 1.6, 1.4) };
  if (s === 'I' || s === 'J') { // hands at the chest: lean the balloon off to the side so the string clears his face
    const k = 1 - ss(seg(f, 1560, 1580)), sw = sway(f);
    w.balloon = { mode: 'held', actor: 'leo', side: 'L', lift: 0.95 - 0.25 * k, sway: [sw[0] - 1.1 * k, sw[1]] };
  }
  if (s === 'K') w.balloon = f < 1748 ? { mode: 'held', actor: 'leo', side: 'L', lift: 0.95, sway: sway(f) } : { mode: 'free', base: BAL_BASE, rise: balloonFree(f)[1] - BAL_BASE[1], drift: [balloonFree(f)[0] - BAL_BASE[0], balloonFree(f)[2] - BAL_BASE[2]], tilt: 0.12 * Math.sin(f * 0.07) };
  // ---- polaroids ----
  if (s === 'D' && f >= 480) for (let i = 0; i < 6; i++) { // burst out of his chest and drift away over the lake
    const t = seg(f, 482 + i * 2, 600 + i * 4), e = easeOut(clamp(t * 2.2));
    const a = (i / 6) * Math.PI * 2 + 0.4;
    const p = ADD([-0.42, 1.35, 0.7], [Math.cos(a) * 0.9 * e - 1.2 * t * t, Math.sin(a) * 0.55 * e + 1.6 * t * t, -0.3 * e - 3 * t * t]);
    if (t < 1) w.photos.push([...p, Math.sin(f * 0.1 + i) * 0.4, 0.8 * (1 - seg(t, 0.75, 1))]);
  }
  if (s === 'I' && f >= 1388 || s === 'J') for (let i = 0; i < 6; i++) { // circle him, then float up and away
    const a0 = (i / 6) * Math.PI * 2 + f * 0.022, r = 1.15 * easeOut(seg(f, 1388 + i * 3, 1408 + i * 3));
    let p = [PIER_END[0] + Math.cos(a0) * r, PIER_END[1] + 0.85 + 0.22 * Math.sin(i * 1.7 + f * 0.04), PIER_END[2] + Math.sin(a0) * r * 0.9];
    const up = seg(f, 1600 + i * 12, 1660 + i * 12);
    p = ADD(p, [0.6 * up * (i % 2 ? 1 : -1), 4.5 * easeIn(up) + up, -5 * up]);
    if (r > 0.01 && up < 1) w.photos.push([...p, 0.3 * Math.sin(f * 0.07 + i), 0.75 * (1 - seg(up, 0.6, 1))]);
  }
  // ---- connection thread (D) ----
  if (s === 'D' && f >= 512 && f < 560) w.thread = { from: [-0.4, 1.28, 0.66], to: [-8, 1.15, 2.2], grow: easeOut(seg(f, 512, 524)), snap: f < 540 ? 0 : easeOut(seg(f, 540, 556)) };
  // ---- the mind (E, F, G) ----
  if ((s === 'E' && f >= 650) || s === 'F' || (s === 'G' && f < 1035)) {
    const grow = [0, 1, 2, 3].map((i) => ss(seg(f, 736 + i * 5, 760 + i * 5)));
    const snap = seg(f, 982, 992);
    w.mind = { links: grow.map((g) => g * (1 - snap)), dots: f < 982, glow: f < 982 ? 1 : lerp(1, 0.25, seg(f, 982, 1000)) };
  }
  return w;
}

// ---------- cast ----------
const blinkAt = (f, seed) => { const per = 85 + (seed * 17) % 40, ph = (f + seed * 31) % per; return ph < 1 ? 0.6 : ph < 3 ? 1 : ph < 4 ? 0.6 : 0; };
function ch(id, outfit, pos, spec, extra = {}) {
  const seed = spec.seed ?? 1;
  return { id, kind: 'char', pos, res: extra.res || 'hi', seed, ...extra, spec: { outfit, blink: spec.noBlink ? 0 : blinkAt(extra.f ?? 0, seed), ...spec } };
}
function fx(id, fxName, pos, t, s = 1, extra = {}) { return { id, kind: 'fx', pos, s, res: extra.res || 'lo', ...extra, spec: { fx: fxName, t, ...(extra.spec || {}) } }; }
const LEO = { seed: 3 }, MAYA = { seed: 9 };
const centred = (P, s) => [P[0], P[1] - 1.07 * s, P[2]]; // fx card origin so its centre sits on P

export function actorsAt(f) {
  const s = shotAt(f).name[0], A = [];
  const C = (id, outfit, pos, spec, extra = {}) => A.push(ch(id, outfit, pos, spec, { f, ...extra }));
  if (s === 'A' || s === 'B') {
    const look = f < 70 ? -0.2 : 1;
    let expr = f < 70 ? 'sad' : 'sadlook', armR = 'seat';
    if (s === 'B') { expr = f < 172 ? 'sadlook' : f < 236 ? 'soft' : 'sad'; if (f >= 240) armR = f < 286 ? ['seat', 'reach', back(seg(f, 240, 252))] : ['reach', 'seat', ss(seg(f, 286, 300))]; }
    C('leo', 'leo', LEO_SEAT, { ...LEO, mode: 'bench', facing: f < 70 ? 0 : 0.5, look, expr, armL: 'string', armR, headTilt: s === 'B' && f >= 172 && f < 236 ? 4 : -2 }, { ground: 0 });
    if (s === 'B' && f >= 166 && f < 256) {
      const sc = f < 240 ? pop(f, 166, 12) : 1 - easeIn(seg(f, 240, 254));
      if (sc > 0.02) C('maya', 'maya', MAYA_SEAT, { ...MAYA, mode: 'bench', facing: -0.5, look: -1, expr: 'smile_closed', armL: 'seat', armR: 'seat', blush: 1 }, { ground: 0, s: sc, tint: '#fff4e6', blob: sc > 0.5 });
    }
  } else if (s === 'C') {
    C('leo', 'leo', LEO_STAND, { ...LEO, facing: -0.6, look: -1, expr: 'sadlook', armL: 'string', armR: f < 380 ? 'hang' : ['hang', 'reach', ss(seg(f, 380, 400)) * 0.6] }, { ground: 0 });
    const t = seg(f, 305, 436), x = lerp(-0.9, -7.8, t), z = lerp(2.0, 2.3, t);
    C('maya', 'maya', [x, 0, z], { ...MAYA, side: 1, mode: 'march', phase: f * 0.045, expr: 'sad', stride: 0.6 }, { ground: 0 });
  } else if (s === 'D' || (s === 'E' && f < 668)) {
    let expr = 'sad', armR = 'hang', look = 0;
    if (f >= 482 && f < 520) { expr = 'sadlook'; look = -0.6; }
    if (f >= 520 && f < 540) { expr = 'sadlook'; look = -1; armR = ['hang', 'reach', ss(seg(f, 520, 532)) * 0.5]; }
    if (f >= 540 && f < 552) expr = 'shock';
    if (f >= 561 && f < 610) { expr = 'hope'; look = 0.5; }
    if (f >= 610) { expr = 'closed'; }
    C('leo', 'leo', LEO_STAND, { ...LEO, facing: 0, look, expr, armL: 'string', armR, headTilt: f >= 561 && f < 610 ? -4 : 2 }, { ground: 0 });
  } else if (s === 'F' && f >= 829 || s === 'G' && f < 995) {
    const sc = f < 982 ? pop(f, 829, 12) : 1 - easeIn(seg(f, 982, 994));
    if (sc > 0.02) C('maya', 'maya', [NODES[0][0], NODES[0][1] - 1.15 * sc, NODES[0][2] + 0.05], { ...MAYA, facing: 0, expr: f < 982 ? 'grin' : 'shock', armL: 'hang', armR: 'wave' }, { s: 1.0 * sc, blob: false, noShadow: true, tint: '#fff4e6' });
  }
  if (s === 'G' && f >= 1030 || s === 'H' && f < 1262) {
    const H = s === 'H';
    C('leo', 'leo', LEO_SEAT, { ...LEO, mode: 'bench', facing: 0, look: H ? 0 : -0.3, expr: H ? (f < 1236 ? 'sadlook' : 'sad') : 'sad', armL: 'stringLow', armR: H ? 'photo' : 'seat', holdR: H ? 'photo' : null,
      headTilt: H ? -7 : -3, tear: H && f >= 1238 ? seg(f, 1238, 1262) : 0 }, { ground: 0 });
  } else if (s === 'H') {
    const laugh = Math.floor(f / 5) % 2;
    C('pleo', 'leo', PIER_MEM, { ...LEO, facing: 0.4, look: 0.6, expr: laugh ? 'laugh' : 'grin', armL: 'string', armR: ['hang', 'wave', back(seg(f, 1270, 1282))], headTilt: 4 * Math.sin(f * 0.4) }, { ground: PIER_Y, tint: '#ffffff' });
    C('pmaya', 'maya', [3.48, PIER_Y, -10.7], { ...MAYA, facing: -0.5, look: -1, expr: laugh ? 'grin' : 'laugh', armL: 'cheek', armR: 'hang', headTilt: -3 * Math.sin(f * 0.4) }, { ground: PIER_Y, tint: '#ffffff' });
  } else if (s === 'I') {
    const hope = f >= 1420;
    C('leo', 'leo', PIER_END, { ...LEO, facing: 0, look: 0, expr: hope ? 'hope' : f < 1392 ? 'closed' : 'sadlook', armL: 'clutch', armR: 'clutch', headTilt: hope ? -6 : 3 }, { ground: PIER_Y });
  } else if (s === 'J') {
    const expr = f < 1526 ? 'hope' : f < 1573 ? 'soft' : f < 1600 ? 'closed' : 'soft';
    C('leo', 'leo', PIER_END, { ...LEO, facing: 0, look: f >= 1600 ? -0.4 : 0, expr, armL: f < 1560 ? 'clutch' : ['clutch', 'string', ss(seg(f, 1560, 1580))], armR: f < 1560 ? 'clutch' : ['clutch', 'hang', ss(seg(f, 1560, 1580))], headTilt: f >= 1600 ? -5 : 0, blush: f >= 1573 ? 1 : 0 }, { ground: PIER_Y });
  } else if (s === 'K') {
    const rel = seg(f, 1743, 1750);
    const expr = f < 1746 ? 'soft' : f < 1793 ? 'sadlook' : 'smile_closed';
    C('leo', 'leo', PIER_END, { ...LEO, facing: 0, look: 0, expr, armL: f < 1743 ? 'string' : f < 1780 ? ['string', 'open', back(rel)] : ['open', 'hang', ss(seg(f, 1780, 1798))],
      armR: f < 1793 ? 'hang' : ['hang', 'heart', back(seg(f, 1793, 1806))], headTilt: f >= 1750 && f < 1800 ? -8 : 0, blush: f >= 1806 ? 1 : 0 }, { ground: PIER_Y });
  } else if (s === 'L') {
    const z = leoWalkZ(f), big = f >= 1936 && f < 1950;
    C('leo', 'leo', [3.2, PIER_Y, z], { ...LEO, mode: 'stroll', phase: f * 0.038, facing: 0, expr: f < 1904 ? 'soft' : 'grin', armL: 'hang', armR: 'hang', headTilt: big ? -3 : 0 }, { ground: PIER_Y });
  }
  return A;
}

export function fxAt(f, cam) {
  const s = shotAt(f).name[0], X = [];
  // drifting leaves at sunset
  if ('ABCD'.includes(s)) for (let i = 0; i < 3; i++) {
    const t = ((f + i * 70) % 210) / 210, x = lerp(1.6, -1.8, t) + i * 0.4, y = lerp(2.6, 0.2, t) + 0.15 * Math.sin(f * 0.1 + i);
    X.push(fx('leaf' + i, 'leaf', [x, y, 1.2 - i * 0.7], t + i, 0.12, { spec: { col: ['#e9893a', '#d9a441', '#c8502a'][i] } }));
  }
  if (s === 'B') {
    if (f >= 164 && f < 196) X.push(fx('mspark', 'sparkle', [0.42, 0.55, 0.42], seg(f, 164, 196), 0.75, { glow: 1 }));
    if (f >= 238 && f < 270) X.push(fx('mgone', 'sparkle', [0.42, 0.45, 0.42], seg(f, 238, 270), 0.85, { glow: 1 }));
  }
  if (s === 'D') {
    if (f >= 480 && f < 494) X.push(fx('pburst', 'burst', [-0.42, 0.75, 0.75], seg(f, 480, 494), 0.7, { glow: 1, spec: { col: '#fff3c8', rays: 10 } }));
    if (f >= 540 && f < 566) X.push(fx('snap', 'sparkle', [-4.2, 0.4, 1.45], seg(f, 540, 566), 0.9, { glow: 1, spec: { col: '#ffd86a' } }));
    // dream bubble beside his head, on the screen-right side whatever the orbit angle
    const c = camAt(f), dx = c.look[0] - c.pos[0], dz = c.look[2] - c.pos[2], dl = Math.hypot(dx, dz);
    const DP = [-0.42 - (dz / dl) * 0.36 - (dx / dl) * 0.15, 1.8, 0.62 + (dx / dl) * 0.36 - (dz / dl) * 0.15];
    if (f >= 561 && f < 618) {
      const sc = f < 610 ? pop(f, 561, 12) : 1 - easeIn(seg(f, 610, 618));
      X.push(fx('dream', 'dream', centred([DP[0], DP[1] + 0.02 * Math.sin(f * 0.15), DP[2]], 0.4 * sc), 0, 0.4 * sc, { res: 'hi', glow: 1 }));
    }
    if (f >= 610 && f < 626) X.push(fx('dpuff', 'puff', centred(DP, 0.5), seg(f, 610, 626), 0.5));
  }
  // the mind
  if ((s === 'E' && f >= 650) || s === 'F' || (s === 'G' && f < 1035)) {
    const hs = 1.15;
    if (f >= 673 && f < 984) X.push(fx('heart', 'heart', centred(HEARTP, hs), 0, hs * pop(f, 673, 12) * (1 + 0.04 * Math.sin(f * 0.25)), { glow: 1, res: 'hi' }));
    if (f >= 984) X.push(fx('crack', 'crack', centred([HEARTP[0], HEARTP[1] - 0.4 * easeIn(seg(f, 990, 1030)), HEARTP[2]], hs), easeOut(seg(f, 984, 1004)), hs, { glow: 1, res: 'hi' }));
    if (f >= 673 && f < 700) X.push(fx('hspark', 'sparkle', centred(HEARTP, 1.4), seg(f, 673, 700), 1.4, { glow: 1 }));
    [['mug', 853, 1], ['sunny', 884, 2], ['shield', 916, 3]].forEach(([k, t0, n]) => {
      if (f < t0) return;
      const sc = f < 982 ? pop(f, t0, 12) : 1 - easeIn(seg(f, 982, 994));
      if (sc > 0.02) X.push(fx('ic' + n, k, centred(NODES[n], 1.35 * sc), (f % 60) / 60, 1.35 * sc, { glow: 1, res: 'hi' }));
      if (f < t0 + 24) X.push(fx('isp' + n, 'sparkle', centred(NODES[n], 1.1), seg(f, t0, t0 + 24), 1.1, { glow: 1 }));
    });
    if (f >= 829 && f < 853) X.push(fx('psp', 'sparkle', centred(NODES[0], 1.2), seg(f, 829, 853), 1.2, { glow: 1 }));
    for (let n = 0; n < 4; n++) if (f >= 982 && f < 998) X.push(fx('npuff' + n, 'puff', centred(NODES[n], 1.0), seg(f, 982, 998), 1.0));
  }
  // rain + a leaf on the empty seat
  if ((s === 'G' && f >= 1036) || (s === 'H' && f < 1262)) {
    const c = camAt(f), d0 = [c.look[0] - c.pos[0], c.look[1] - c.pos[1], c.look[2] - c.pos[2]], L = Math.hypot(...d0), dir = d0.map((v) => v / L);
    [1.6, 2.8, 4.6].forEach((d, i) => { const sc = 0.5 * d; X.push(fx('rain' + i, 'rain', [c.pos[0] + dir[0] * d, c.pos[1] + dir[1] * d - 1.07 * sc, c.pos[2] + dir[2] * d], (f * 0.11 + i * 0.37) % 1, sc, { spec: { seed: i * 5 } })); });
    if (f >= 1112) { const t = seg(f, 1112, 1160); X.push(fx('seatleaf', 'leaf', [lerp(0.7, 0.42, t), lerp(1.6, 0.45, easeOut(t)), 0.25], t < 1 ? t * 0.6 : 0.6, 0.12, { spec: { col: '#c8502a' } })); }
  }
  if (s === 'H' && f >= 1262 && f < 1300) X.push(fx('memsp', 'sparkle', [3.25, 0.9, -10.3], seg(f, 1262, 1300), 1.0, { glow: 1 }));
  if (s === 'I' && f >= 1484 && f < 1512) X.push(fx('isp', 'sparkle', [3.2, 0.7, -16.2], seg(f, 1484, 1512), 1.0, { glow: 1 }));
  if (s === 'J' && f >= 1524 && f < 1560) X.push(fx('dawnsp', 'sparkle', [3.2, 0.8, -16.6], seg(f, 1524, 1560), 1.1, { glow: 1, spec: { col: '#ffe6b0' } }));
  if (s === 'K' && f >= 1806 && f < 1836) X.push(fx('hsp', 'sparkle', [3.3, 0.85, -16.25], seg(f, 1806, 1836), 0.55, { glow: 1, spec: { col: '#ffd0d6' } }));
  if (s === 'L' && f >= 1922) {
    X.push(fx('skyheart', 'heart', [2.3, 7.2, -40], 0, 0.9, { glow: 1 }));
    X.push(fx('skysp', 'sparkle', [2.3, 6.4, -39.8], ((f - 1922) % 40) / 40, 2.0, { glow: 1 }));
  }
  return X;
}
