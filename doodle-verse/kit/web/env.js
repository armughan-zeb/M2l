// Modular 3D Environment Engine for Doodle Verse Animation
// Supports: office/indoor, jungle/nature, battlefield/war, tundra/prehistoric, lab/tech, and custom scenes.

import * as THREE from './three.module.js';
import * as PT from './paint.js';

/**
 * Universal Environment Builder
 * @param {object} ctx - Three.js engine context { scene, toon, tbox, grp, add, texOf, glowM, noNd }
 * @param {string|object|function} spec - Environment name, config object, or custom builder function
 * @returns {object} Environment state containing references to animated elements and lights
 */
export function buildEnvironment(ctx, spec = 'office') {
  if (typeof spec === 'function') {
    return spec(ctx);
  }

  const type = typeof spec === 'string' ? spec : (spec.type || 'office');
  const opts = typeof spec === 'object' ? spec : {};

  switch (type.toLowerCase()) {
    case 'office':
    case 'indoor':
    case 'corporate':
    case 'tech':
      return buildOffice(ctx, opts);

    case 'jungle':
    case 'nature':
    case 'forest':
    case 'wilderness':
    case 'safari':
      return buildJungle(ctx, opts);

    case 'battlefield':
    case 'war':
    case 'military':
    case 'trench':
      return buildBattlefield(ctx, opts);

    case 'tundra':
    case 'ice':
    case 'prehistoric':
    case 'cave':
    case 'winter':
      return buildTundra(ctx, opts);

    case 'laboratory':
    case 'lab':
    case 'scifi':
      return buildLaboratory(ctx, opts);

    case 'park':
    case 'garden':
      return buildPark(ctx, opts);

    default:
      console.warn(`Unknown environment type "${type}", falling back to office.`);
      return buildOffice(ctx, opts);
  }
}

// ============================================================================
// 1. INDOOR / OFFICE / CORPORATE / TECH ENVIRONMENT
// ============================================================================
export function buildOffice(ctx, opts = {}) {
  const { scene, toon, tbox, grp, add, texOf, glowM } = ctx;
  const env = { type: 'office', animators: [] };

  const floorMat = toon(0, texOf(opts.woodFloor ? PT.planks('#8c6a46', { n: 8, seed: 12 }) : PT.carpet('#2c3748', 15), 12, 12));
  const wallMat = toon(0, texOf(PT.plaster('#e2e8f0', 8), 6, 6));
  const baseboardMat = toon('#1e293b');
  const ironM = toon('#334155');
  const deskTopMat = toon(0, texOf(PT.planks('#c99052', { n: 4, seed: 7 }), 2, 1));

  // Floor (indoor room bounds: 24m wide x 18m deep)
  add(tbox(28, 0.2, 22, floorMat, 4), 0, -0.1, 0, scene, false);

  // Back Wall
  add(tbox(28, 5.5, 0.3, wallMat, 4), 0, 2.75, -5.5, scene, true);
  // Baseboard
  add(tbox(28, 0.2, 0.35, baseboardMat, 4), 0, 0.1, -5.48, scene, false);

  // Left & Right Interior Walls
  add(tbox(0.3, 5.5, 22, wallMat, 4), -13.8, 2.75, 0, scene, true);
  add(tbox(0.3, 5.5, 22, wallMat, 4), 13.8, 2.75, 0, scene, true);

  // Large Panoramic Office Window on Back Wall (City Skyline)
  const winG = grp(0, 3.0, -5.3, scene);
  const skyMat = toon(0, texOf(PT.citySkylineTex(42)));
  add(new THREE.Mesh(new THREE.PlaneGeometry(12, 3.8), skyMat), 0, 0, 0.02, winG, false);
  // Window frame & mullions
  const frameM = toon('#0f172a');
  add(tbox(12.2, 0.12, 0.15, frameM), 0, 1.9, 0.05, winG, false);
  add(tbox(12.2, 0.12, 0.15, frameM), 0, -1.9, 0.05, winG, false);
  add(tbox(0.12, 3.8, 0.15, frameM), -6.0, 0, 0.05, winG, false);
  add(tbox(0.12, 3.8, 0.15, frameM), 6.0, 0, 0.05, winG, false);
  // Vertical dividers
  add(tbox(0.08, 3.8, 0.12, frameM), -2.0, 0, 0.05, winG, false);
  add(tbox(0.08, 3.8, 0.12, frameM), 2.0, 0, 0.05, winG, false);

  // Modern Office Desk
  createDesk(ctx, 0, 0, 0, { deskTopMat, ironM });

  // Executive Swivel Chair
  createChair(ctx, 0, -0.65, 0);

  // Laptop on desk
  const laptop = createLaptop(ctx, -0.1, 0.76, 0.05, { screenType: 'chart' });
  env.laptop = laptop;

  // Whiteboard with Flowchart Architecture
  createWhiteboard(ctx, 4.2, -3.2, -0.35, { title: opts.whiteboardTitle || 'AI & CLOUD PIPELINE' });

  // Potted Ficus Office Plant
  createPlant(ctx, -3.8, -3.2, 1.2);
  createPlant(ctx, 5.8, 1.2, 0.9);

  // Steel 3-Drawer Filing Cabinet
  createFilingCabinet(ctx, -4.5, -1.0, 0);

  // Water Cooler
  createWaterCooler(ctx, -5.6, -3.2);

  // Ceiling Fluorescent Lighting Panels
  for (let z = -3; z <= 4; z += 3.5) {
    for (let x = -6; x <= 6; x += 4) {
      add(tbox(2.2, 0.06, 0.9, glowM('#ffffff')), x, 5.4, z, scene, false);
    }
  }

  return env;
}

// ============================================================================
// 2. BATTLEFIELD / WAR / MILITARY TRENCH ENVIRONMENT
// ============================================================================
export function buildBattlefield(ctx, opts = {}) {
  const { scene, toon, tbox, grp, add, texOf, glowM } = ctx;
  const env = { type: 'battlefield', animators: [] };

  const mudMat = toon(0, texOf(PT.dirt('#36281e', 9), 20, 20));
  const plankMat = toon(0, texOf(PT.planks('#423020', { n: 5, seed: 14 }), 2, 2));
  const sandMat = toon(0, texOf(PT.sandbagTex('#96805f', 19), 3, 3));
  const brickMat = toon(0, texOf(PT.brick('#853c2f', 29), 4, 4));
  const woodPostM = toon('#3b2a1a');

  // Ground: churned dark mud
  add(tbox(140, 1.0, 120, mudMat, 15), 0, -0.5, 0, scene, false);

  // Main Trench Line (excavated depression with duckboard floor)
  const trenchG = grp(0, 0, 0, scene);
  // Duckboard planks in trench
  for (let z = -14; z <= 14; z += 1.8) {
    add(tbox(3.2, 0.08, 1.6, plankMat, 1), 0, 0.04, z, trenchG, false);
  }

  // Trench revetment wooden walls
  add(tbox(0.12, 1.4, 30, plankMat, 2), -1.65, 0.7, 0, trenchG, true);
  add(tbox(0.12, 1.4, 30, plankMat, 2), 1.65, 0.7, 0, trenchG, true);

  // Sandbag Parapets stacked along the front lip of the trench
  for (let z = -12; z <= 12; z += 2.2) {
    createSandbags(ctx, 1.9, z, 0, { sandMat });
    createSandbags(ctx, -1.9, z, 0, { sandMat });
  }

  // Barbed Wire Wooden Crosses (Chevaux de frise) in No-Man's-Land
  for (const pos of [[5.2, -6], [7.5, 2], [6.0, 9], [-6.5, -4], [-8.2, 5]]) {
    createBarbedWireCross(ctx, pos[0], pos[1], { woodPostM });
  }

  // Shattered Ruined Brick Wall Remnant
  const ruin = grp(8.0, 0, -8.0, scene);
  add(tbox(5.5, 2.6, 0.5, brickMat, 2), 0, 1.3, 0, ruin, true);
  add(tbox(2.2, 1.4, 0.5, brickMat, 1), 2.8, 0.7, 0.8, ruin, true).rotation.y = 0.5;
  // Rubble pile at base
  for (let i = 0; i < 8; i++) {
    const s = 0.3 + (i % 3) * 0.15;
    add(tbox(s, s, s, brickMat), (i - 4) * 0.7, s * 0.5, 0.6 + (i % 2) * 0.4, ruin);
  }

  // Heavy Concrete Bunker / Pillbox Observation Slit
  createBunker(ctx, 12.0, 4.0, { toon, tbox, grp, add });

  // Burnt blasted tree trunks
  for (const pos of [[-9, -10], [-12, 8], [14, -14], [10, 12]]) {
    const trunk = add(new THREE.Mesh(new THREE.CylinderGeometry(0.18, 0.28, 3.8, 7), toon('#1c1917')), pos[0], 1.9, pos[1], scene);
    trunk.rotation.z = (Math.sin(pos[0]) * 0.1);
  }

  // Ammo Crates
  for (const pos of [[-1.2, 0.25, -2], [-1.2, 0.25, -2.8], [-1.2, 0.7, -2.4]]) {
    add(tbox(0.9, 0.45, 0.6, plankMat, 1), pos[0], pos[1], pos[2], scene);
  }

  return env;
}

// ============================================================================
// 3. JUNGLE / NATURE / WILDERNESS ENVIRONMENT
// ============================================================================
export function buildJungle(ctx, opts = {}) {
  const { scene, toon, tbox, grp, add, texOf } = ctx;
  const env = { type: 'jungle', animators: [] };

  const jungleGrassM = toon(0, texOf(PT.grass('#234e1b', 11), 30, 30));
  const soilM = toon(0, texOf(PT.dirt('#4a3319', 21), 15, 15));
  const waterM = toon(0, texOf(PT.water('#1d4ed8', 13)));
  const trunkM = toon('#4a2f1b');
  const leafM = [toon('#15803d'), toon('#166534'), toon('#14532d'), toon('#4ade80')];
  const stoneM = toon(0, texOf(PT.stone('#64748b', 9), 4, 4));

  // Wild organic ground
  add(tbox(160, 1.0, 140, jungleGrassM, 20), 0, -0.5, 0, scene, false);

  // Jungle River / Creek flowing through the scene
  const river = add(new THREE.Mesh(new THREE.PlaneGeometry(160, 14), waterM), 0, 0.08, -12, scene);
  river.rotation.x = -Math.PI / 2;
  env.river = river;

  // Riverbank muddy slopes & river stones
  add(tbox(160, 0.15, 3.5, soilM, 10), 0, 0.05, -5.0, scene, false);
  add(tbox(160, 0.15, 3.5, soilM, 10), 0, 0.05, -19.0, scene, false);
  for (let x = -40; x <= 40; x += 6) {
    const s = 0.5 + Math.sin(x) * 0.3;
    add(new THREE.Mesh(new THREE.SphereGeometry(s, 8, 6), stoneM), x, s * 0.4, -6.5 + Math.cos(x) * 1.5, scene);
  }

  // Dense Tropical Canopy Trees
  const treePositions = [
    [-6.5, -2, 1.3], [-4.2, 4.5, 1.1], [5.5, -1, 1.4], [7.2, 5.0, 1.2],
    [-11, -8, 1.6], [12, -9, 1.7], [-15, 2, 1.5], [16, 3, 1.5],
    [-2, 12, 1.4], [6, 14, 1.3], [-10, 16, 1.6], [12, 18, 1.8]
  ];
  for (const [tx, tz, ts] of treePositions) {
    createJungleTree(ctx, tx, tz, ts, { trunkM, leafM });
  }

  // Boulders and Mossy Rock Formations
  for (const [rx, rz, rs] of [[-3.5, -1.5, 1.1], [3.8, 1.8, 0.9], [-8.0, 3.0, 1.5], [9.5, -4.0, 1.3]]) {
    createMossyRock(ctx, rx, rz, rs, { stoneM, leafM });
  }

  // Wild Tropical Foliage & Bushes
  for (let i = 0; i < 22; i++) {
    const bx = -25 + (i * 2.3) % 50, bz = -4 + (i * 3.7) % 24;
    createFernCluster(ctx, bx, bz, 0.8 + (i % 3) * 0.3, { leafM });
  }

  return env;
}

// ============================================================================
// 4. TUNDRA / PREHISTORIC / EXTREME COLD ENVIRONMENT
// ============================================================================
export function buildTundra(ctx, opts = {}) {
  const { scene, toon, tbox, grp, add, texOf, glowM } = ctx;
  const env = { type: 'tundra', animators: [] };

  const snowMat = toon(0, texOf(PT.snow('#f1f5f9', 23), 20, 20));
  const cliffMat = toon(0, texOf(PT.stone('#475569', 31), 6, 6));
  const woodM = toon('#3b2718');
  const fireGlowM = glowM('#f97316');
  const flameM = glowM('#facc15');

  // Vast Snowfield
  add(tbox(180, 1.0, 160, snowMat, 20), 0, -0.5, 0, scene, false);

  // Snow Drifts and Mounds
  for (const [dx, dz, sx, sz] of [[-8, 2, 6, 3], [7, 4, 8, 4], [-12, -6, 10, 5], [14, -8, 12, 6], [0, 8, 10, 4]]) {
    const mound = add(new THREE.Mesh(new THREE.SphereGeometry(1, 10, 8), snowMat), dx, 0.2, dz, scene);
    mound.scale.set(sx, 0.6, sz);
  }

  // Prehistoric Stone Campfire Pit with Animated Flames
  const firePit = grp(0, 0, 0.8, scene);
  // Stone ring
  for (let i = 0; i < 9; i++) {
    const a = (i / 9) * Math.PI * 2;
    add(new THREE.Mesh(new THREE.SphereGeometry(0.22, 6, 5), cliffMat), Math.cos(a) * 0.75, 0.12, Math.sin(a) * 0.75, firePit);
  }
  // Charred logs
  for (let i = 0; i < 4; i++) {
    const log = add(new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, 0.9, 6), woodM), 0, 0.15, 0, firePit);
    log.rotation.y = (i * Math.PI) / 4; log.rotation.x = 0.25;
  }
  // Glowing ember bed
  add(new THREE.Mesh(new THREE.CylinderGeometry(0.5, 0.5, 0.1, 8), fireGlowM), 0, 0.1, 0, firePit, false);
  // Stylized Flame Cones
  const flames = [];
  for (let i = 0; i < 3; i++) {
    const fl = add(new THREE.Mesh(new THREE.ConeGeometry(0.18 + i * 0.04, 0.65 + i * 0.15, 6), flameM), (i - 1) * 0.15, 0.45 + i * 0.05, 0, firePit, false);
    flames.push(fl);
  }
  env.flames = flames;

  // Primitive Fur/Hide Lean-To Shelter
  createPrehistoricShelter(ctx, -3.8, 1.2, { woodM, cliffMat });

  // Snowy Conical Pine Trees
  for (const [px, pz, ps] of [[-7, -4, 1.2], [-9, 5, 1.4], [6, -3, 1.1], [8, 6, 1.5], [-14, 0, 1.8], [15, 2, 1.7]]) {
    createSnowyPine(ctx, px, pz, ps, { woodM, snowMat, toon });
  }

  // Rocky Mountain Cliff / Cave Face in Background
  const cliff = grp(0, 0, -18, scene);
  add(tbox(80, 18, 12, cliffMat, 4), 0, 9, 0, cliff, true);
  // Dark Cave Mouth Opening
  add(new THREE.Mesh(new THREE.CylinderGeometry(3.5, 4.0, 5.0, 12, 1, false, 0, Math.PI), toon('#09090b')), 0, 2.5, 6.1, cliff);

  return env;
}

// ============================================================================
// 5. LABORATORY / TECH / DATA-CENTER ENVIRONMENT
// ============================================================================
export function buildLaboratory(ctx, opts = {}) {
  const { scene, toon, tbox, grp, add, texOf, glowM } = ctx;
  const env = { type: 'lab', animators: [] };

  const floorM = toon(0, texOf(PT.carpet('#1e293b', 4), 8, 8));
  const wallM = toon(0, texOf(PT.plaster('#334155', 5), 4, 4));
  const rackM = toon('#0f172a');

  // Cleanroom Floor & Walls
  add(tbox(30, 0.2, 26, floorM, 4), 0, -0.1, 0, scene, false);
  add(tbox(30, 6.0, 0.3, wallM, 4), 0, 3.0, -6.0, scene, true);

  // Server Racks with Blinking LED Arrays
  const racks = [];
  for (let x = -8; x <= 8; x += 3.2) {
    const rack = createServerRack(ctx, x, -4.5, { rackM, glowM });
    racks.push(rack);
  }
  env.racks = racks;

  // Futuristic Curved Tech Desk with Terminal
  createDesk(ctx, 0, 0, 0, { deskTopMat: toon('#1e293b'), ironM: toon('#475569') });
  createChair(ctx, 0, -0.65, 0);
  createLaptop(ctx, 0, 0.76, 0.05, { screenType: 'code' });

  // Hologram / Big Diagnostic Screen on Wall
  const screenM = toon(0, texOf(PT.screenTex('chart', 99)));
  add(new THREE.Mesh(new THREE.PlaneGeometry(6.4, 3.2), screenM), 0, 3.2, -5.8, scene, false);

  return env;
}

// ============================================================================
// 6. PARK / OUTDOOR ENVIRONMENT (Explicit public park only)
// ============================================================================
export function buildPark(ctx, opts = {}) {
  const { scene, toon, tbox, grp, add, texOf } = ctx;
  const env = { type: 'park', animators: [] };

  const grassM = toon(0, texOf(PT.grass('#79a84a'), 50, 50));
  const paveM = toon(0, texOf(PT.paving()));
  const slatM = toon(0, texOf(PT.planks('#b5763e', { n: 3, vertical: false, seed: 6 })));
  const ironM = toon('#2e2a36');
  const treeM = [toon('#3f7a3a'), toon('#4f8a3f'), toon('#5a9a44')], trunkM = toon('#6a4a2a');

  add(tbox(200, 1, 200, grassM, 40), 0, -0.5, 0, scene, false);
  add(tbox(90, 0.04, 2.0, paveM, 1.4), -25, 0.02, 2.2, scene, false);

  // Park Bench
  const bench = grp(0, 0, 0, scene);
  for (let i = 0; i < 3; i++) add(tbox(1.95, 0.05, 0.14, slatM, 1), 0, 0.45, 0.17 - i * 0.17, bench);
  for (let i = 0; i < 3; i++) {
    const s = add(tbox(1.95, 0.12, 0.04, slatM, 1), 0, 0.62 + i * 0.16, -0.3 - i * 0.02, bench);
    s.rotation.x = -0.12;
  }
  for (const sx of [-0.88, 0.88]) {
    add(tbox(0.06, 0.45, 0.06, ironM), sx, 0.225, 0.2, bench);
    add(tbox(0.06, 0.95, 0.06, ironM), sx, 0.47, -0.3, bench).rotation.x = -0.1;
  }

  // Trees and Bushes
  for (const [x, z, s] of [[-4, -3, 1], [4.5, -4, 1.2], [-8, 2, 1.3], [9, 3, 1.1]]) {
    const tg = grp(x, 0, z, scene);
    add(new THREE.Mesh(new THREE.CylinderGeometry(0.15 * s, 0.22 * s, 2 * s, 8), trunkM), 0, s, 0, tg);
    add(new THREE.Mesh(new THREE.SphereGeometry(1.5 * s, 14, 10), treeM[0]), 0, 3.0 * s, 0, tg);
  }

  return env;
}

// ============================================================================
// REUSABLE 3D DOODLE PRIMITIVES & PROPS
// ============================================================================

export function createDesk(ctx, x, z, rot = 0, { deskTopMat, ironM } = {}) {
  const { toon, tbox, grp, add } = ctx;
  const desk = grp(x, 0, z, ctx.scene);
  desk.rotation.y = rot;

  const topM = deskTopMat || toon('#d97706');
  const legM = ironM || toon('#1e293b');

  // Desktop plank
  add(tbox(2.2, 0.08, 1.1, topM, 1), 0, 0.74, 0, desk, true);
  // 4 steel legs
  for (const dx of [-0.98, 0.98]) {
    for (const dz of [-0.44, 0.44]) {
      add(tbox(0.07, 0.74, 0.07, legM), dx, 0.37, dz, desk, false);
    }
  }
  return desk;
}

export function createChair(ctx, x, z, rot = 0) {
  const { toon, tbox, grp, add } = ctx;
  const chair = grp(x, 0, z, ctx.scene);
  chair.rotation.y = rot;

  const darkM = toon('#0f172a');
  const chromeM = toon('#94a3b8');

  // Central stem
  add(new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.04, 0.45, 8), chromeM), 0, 0.225, 0, chair);
  // Padded seat cushion
  add(tbox(0.65, 0.1, 0.62, darkM), 0, 0.48, 0, chair, true);
  // Curved ergonomic backrest
  const back = add(tbox(0.62, 0.65, 0.08, darkM), 0, 0.85, -0.28, chair, true);
  back.rotation.x = -0.1;
  return chair;
}

export function createLaptop(ctx, x, y, z, { screenType = 'chart' } = {}) {
  const { toon, tbox, grp, add, texOf } = ctx;
  const laptop = grp(x, y, z, ctx.scene);

  const silverM = toon('#cbd5e1');
  const scrMat = toon(0, texOf(PT.screenTex(screenType, 17)));

  // Base with keyboard
  add(tbox(0.52, 0.02, 0.36, silverM), 0, 0.01, 0, laptop);
  // Screen lid angled upwards 110 deg
  const lid = grp(0, 0.02, -0.17, laptop);
  lid.rotation.x = 0.35;
  add(tbox(0.52, 0.34, 0.02, silverM), 0, 0.17, 0, lid);
  add(new THREE.Mesh(new THREE.PlaneGeometry(0.48, 0.3), scrMat), 0, 0.17, 0.012, lid, false);

  return laptop;
}

export function createWhiteboard(ctx, x, z, rot = 0, { title = 'STRATEGY' } = {}) {
  const { toon, tbox, grp, add, texOf } = ctx;
  const wb = grp(x, 0, z, ctx.scene);
  wb.rotation.y = rot;

  const frameM = toon('#64748b');
  const boardM = toon(0, texOf(PT.whiteboardTex(title, 22)));

  // Two vertical legs
  for (const lx of [-1.35, 1.35]) {
    add(tbox(0.08, 2.2, 0.08, frameM), lx, 1.1, 0, wb);
    // Base feet
    add(tbox(0.08, 0.06, 0.7, frameM), lx, 0.03, 0, wb);
  }
  // The whiteboard surface
  add(new THREE.Mesh(new THREE.PlaneGeometry(2.6, 1.5), boardM), 0, 1.45, 0.045, wb, false);
  return wb;
}

export function createPlant(ctx, x, z, s = 1) {
  const { toon, grp, add } = ctx;
  const plant = grp(x, 0, z, ctx.scene);

  const potM = toon('#f8fafc');
  const soilM = toon('#3f2e1e');
  const leafM = toon('#16a34a');

  // Ceramic pot
  add(new THREE.Mesh(new THREE.CylinderGeometry(0.35 * s, 0.25 * s, 0.7 * s, 10), potM), 0, 0.35 * s, 0, plant);
  add(new THREE.Mesh(new THREE.CylinderGeometry(0.33 * s, 0.33 * s, 0.05 * s, 10), soilM), 0, 0.68 * s, 0, plant);

  // Arching ficus leaves
  for (let i = 0; i < 7; i++) {
    const a = (i / 7) * Math.PI * 2;
    const leaf = add(new THREE.Mesh(new THREE.SphereGeometry(0.25 * s, 6, 5), leafM), Math.cos(a) * 0.3 * s, (0.8 + (i % 2) * 0.2) * s, Math.sin(a) * 0.3 * s, plant);
    leaf.scale.set(1.2, 0.2, 0.7);
    leaf.rotation.y = a;
    leaf.rotation.z = 0.4;
  }
  return plant;
}

export function createFilingCabinet(ctx, x, z, rot = 0) {
  const { toon, tbox, grp, add } = ctx;
  const cab = grp(x, 0, z, ctx.scene);
  cab.rotation.y = rot;

  const bodyM = toon('#334155');
  const chromeM = toon('#e2e8f0');

  add(tbox(0.65, 1.35, 0.75, bodyM), 0, 0.675, 0, cab, true);
  for (let i = 0; i < 3; i++) {
    add(tbox(0.12, 0.03, 0.04, chromeM), 0, 0.35 + i * 0.42, 0.39, cab, false);
  }
  return cab;
}

export function createWaterCooler(ctx, x, z) {
  const { toon, grp, add, glowM } = ctx;
  const wc = grp(x, 0, z, ctx.scene);

  const whiteM = toon('#f1f5f9');
  const jugM = glowM('#38bdf8');

  // Dispenser stand
  add(new THREE.Mesh(new THREE.CylinderGeometry(0.24, 0.26, 1.1, 10), whiteM), 0, 0.55, 0, wc);
  // Inverted water bottle
  add(new THREE.Mesh(new THREE.CylinderGeometry(0.22, 0.18, 0.55, 10), jugM), 0, 1.35, 0, wc, false);
  return wc;
}

export function createSandbags(ctx, x, z, rot = 0, { sandMat } = {}) {
  const { toon, tbox, grp, add, texOf } = ctx;
  const g = grp(x, 0, z, ctx.scene);
  g.rotation.y = rot;

  const mat = sandMat || toon(0, texOf(PT.sandbagTex('#ad9574', 19), 2, 2));
  // 3 stacked sandbags in pyramid
  add(tbox(1.2, 0.3, 0.5, mat), -0.55, 0.15, 0, g, true);
  add(tbox(1.2, 0.3, 0.5, mat), 0.55, 0.15, 0, g, true);
  add(tbox(1.2, 0.3, 0.5, mat), 0, 0.42, 0, g, true);
  return g;
}

export function createBarbedWireCross(ctx, x, z, { woodPostM } = {}) {
  const { toon, grp, add } = ctx;
  const g = grp(x, 0, z, ctx.scene);
  const mat = woodPostM || toon('#3b2a1a');

  const p1 = add(new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 1.6, 6), mat), 0, 0.7, 0, g);
  p1.rotation.z = 0.55;
  const p2 = add(new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 1.6, 6), mat), 0, 0.7, 0, g);
  p2.rotation.z = -0.55;

  return g;
}

export function createBunker(ctx, x, z) {
  const { toon, tbox, grp, add } = ctx;
  const bunker = grp(x, 0, z, ctx.scene);
  const concM = toon('#52525b');
  const darkM = toon('#09090b');

  // Heavy concrete pillbox block
  add(tbox(6.0, 2.8, 5.0, concM, 2), 0, 1.4, 0, bunker, true);
  // Dark vision slit
  add(tbox(3.2, 0.3, 0.2, darkM), 0, 1.7, 2.52, bunker, false);
  return bunker;
}

export function createJungleTree(ctx, x, z, s = 1, { trunkM, leafM }) {
  const { grp, add } = ctx;
  const tree = grp(x, 0, z, ctx.scene);

  // Main trunk
  add(new THREE.Mesh(new THREE.CylinderGeometry(0.22 * s, 0.38 * s, 3.5 * s, 8), trunkM), 0, 1.75 * s, 0, tree);

  // Layered tropical foliage canopy
  for (let i = 0; i < 3; i++) {
    const mat = leafM[i % leafM.length];
    const cy = (3.2 + i * 1.1) * s;
    const foliage = add(new THREE.Mesh(new THREE.SphereGeometry((1.8 - i * 0.3) * s, 10, 8), mat), 0, cy, 0, tree);
    foliage.scale.set(1.4, 0.8, 1.4);
  }
  return tree;
}

export function createMossyRock(ctx, x, z, s = 1, { stoneM, leafM }) {
  const { grp, add } = ctx;
  const rock = grp(x, 0, z, ctx.scene);

  const main = add(new THREE.Mesh(new THREE.DodecahedronGeometry(1.0 * s), stoneM), 0, 0.6 * s, 0, rock);
  main.scale.set(1.4, 0.8, 1.1);

  // Moss patch on top
  const moss = add(new THREE.Mesh(new THREE.SphereGeometry(0.7 * s, 6, 5), leafM[0]), 0, 1.05 * s, 0, rock);
  moss.scale.set(1.2, 0.3, 1.0);
  return rock;
}

export function createFernCluster(ctx, x, z, s = 1, { leafM }) {
  const { grp, add } = ctx;
  const fern = grp(x, 0, z, ctx.scene);
  const mat = leafM[1 % leafM.length];

  for (let i = 0; i < 5; i++) {
    const a = (i / 5) * Math.PI * 2;
    const leaf = add(new THREE.Mesh(new THREE.SphereGeometry(0.4 * s, 6, 4), mat), Math.cos(a) * 0.35 * s, 0.25 * s, Math.sin(a) * 0.35 * s, fern);
    leaf.scale.set(1.6, 0.15, 0.5);
    leaf.rotation.y = a;
    leaf.rotation.z = 0.35;
  }
  return fern;
}

export function createSnowyPine(ctx, x, z, s = 1, { woodM, snowMat, toon }) {
  const { grp, add } = ctx;
  const pine = grp(x, 0, z, ctx.scene);
  const pineGreenM = toon('#1e3a29');

  // Trunk
  add(new THREE.Mesh(new THREE.CylinderGeometry(0.12 * s, 0.2 * s, 2.5 * s, 7), woodM), 0, 1.25 * s, 0, pine);

  // 3 conical evergreen tiers
  for (let i = 0; i < 3; i++) {
    const cy = (2.2 + i * 1.1) * s;
    const rad = (1.5 - i * 0.35) * s;
    add(new THREE.Mesh(new THREE.ConeGeometry(rad, 1.8 * s, 8), pineGreenM), 0, cy, 0, pine);
    // Snow cap on tier
    add(new THREE.Mesh(new THREE.ConeGeometry(rad * 0.95, 0.7 * s, 8), snowMat), 0, cy + 0.55 * s, 0, pine, false);
  }
  return pine;
}

export function createPrehistoricShelter(ctx, x, z, { woodM, cliffMat }) {
  const { toon, tbox, grp, add } = ctx;
  const shelter = grp(x, 0, z, ctx.scene);
  const hideM = toon('#854d0e');

  // Wooden angled poles
  for (const sx of [-1.5, 1.5]) {
    const pole = add(new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.1, 2.8, 6), woodM), sx, 1.2, 0, shelter);
    pole.rotation.x = 0.45;
  }
  // Animal hide drape
  const hide = add(tbox(3.4, 2.6, 0.08, hideM, 1), 0, 1.1, -0.4, shelter, true);
  hide.rotation.x = 0.45;
  return shelter;
}

export function createServerRack(ctx, x, z, { rackM, glowM }) {
  const { tbox, grp, add } = ctx;
  const rack = grp(x, 0, z, ctx.scene);

  // Server tower body
  add(tbox(1.4, 3.2, 1.1, rackM), 0, 1.6, 0, rack, true);

  // Blinking green/blue LED strips
  const ledColors = ['#22c55e', '#38bdf8', '#eab308'];
  for (let i = 0; i < 6; i++) {
    const col = ledColors[i % ledColors.length];
    add(tbox(1.1, 0.05, 0.04, glowM(col)), 0, 0.6 + i * 0.45, 0.56, rack, false);
  }
  return rack;
}
