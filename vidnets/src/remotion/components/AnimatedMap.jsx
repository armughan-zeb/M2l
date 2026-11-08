import React from 'react';
import { AbsoluteFill, Img, Sequence, interpolate, staticFile, useCurrentFrame, useVideoConfig } from 'remotion';
import { cueStartFrame } from '../engine/DirectorMotion';
import { computeEntranceStyle } from '../engine/MotionEngine';
import { rgba } from '../theme';
import countryGeoJSON from '../maps/ne_110m_admin_0_countries.json';
import placeGeoJSON from '../maps/ne_110m_populated_places.json';
import focusedCountryGeoJSON from '../maps/ne_10m_admin_0_focus.json';
import focusedAdmin1GeoJSON from '../maps/ne_10m_admin_1_focus.json';
import { SatelliteMap } from './SatelliteMap';
import { Globe3D } from './Globe3D';

// Local Natural Earth vectors keep renders deterministic and independent of
// live map tiles. Natural Earth 1:110m boundaries are generalized for world
// scale; attribution: Natural Earth, public domain.
const MAP_WIDTH = 1200;
const MAP_HEIGHT = 700;
const clampUnit = (value) => Math.max(0, Math.min(1, Number(value) || 0));
// A single monotonic camera ease gives the map one intentional move and a
// stable landing. Avoid back/easing keyframes here: overshoot reads as shake.
const easeMapCamera = (value) => {
  const t = clampUnit(value);
  return t * t * (3 - 2 * t);
};
const lerpCamera = (from, to, progress) => from + (to - from) * progress;
const mercatorPoint = ([lon, lat]) => {
  const safeLat = Math.max(-84, Math.min(84, Number(lat) || 0));
  const rad = safeLat * Math.PI / 180;
  return {
    x: (Number(lon) + 180) / 360 * MAP_WIDTH,
    y: (0.5 - Math.log(Math.tan(Math.PI / 4 + rad / 2)) / (2 * Math.PI)) * MAP_HEIGHT,
  };
};
const ringPath = (ring) => ring.map((point, index) => {
  const p = mercatorPoint(point);
  return `${index ? 'L' : 'M'}${p.x.toFixed(1)},${p.y.toFixed(1)}`;
}).join(' ') + ' Z';
const geometryPath = (geometry) => {
  if (!geometry) return '';
  const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.type === 'MultiPolygon' ? geometry.coordinates : [];
  return polygons.map((polygon) => polygon.map(ringPath).join(' ')).join(' ');
};
const geometryBounds = (geometry) => {
  if (!geometry) return null;
  const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.type === 'MultiPolygon' ? geometry.coordinates : [];
  const points = [];
  polygons.forEach((polygon) => polygon.forEach((ring) => ring.forEach((coordinate) => points.push(mercatorPoint(coordinate)))));
  if (!points.length) return null;
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  return { left: Math.min(...xs), right: Math.max(...xs), top: Math.min(...ys), bottom: Math.max(...ys), width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys) };
};
const FOCUSED_COUNTRIES = new Map((focusedCountryGeoJSON.features || []).map((feature) => [
  String(feature.properties?.iso_a3 || '').toUpperCase(), feature,
]));
const COUNTRY_FEATURES = (countryGeoJSON.features || []).map((baseFeature) => {
  const baseProps = baseFeature.properties || {};
  const baseCode = String(baseProps.ISO_A3_EH || baseProps.ISO_A3 || baseProps.ADM0_A3 || '').toUpperCase();
  const refined = FOCUSED_COUNTRIES.get(baseCode);
  if (!refined) return baseFeature;
  return {
    ...baseFeature,
    geometry: refined.geometry,
    properties: {
      ...baseProps,
      LABEL_X: refined.properties?.label_lon,
      LABEL_Y: refined.properties?.label_lat,
      ADMIN: refined.properties?.name || baseProps.ADMIN,
      NAME_EN: refined.properties?.name_en || baseProps.NAME_EN,
    },
  };
});
const GEOGRAPHIC_COUNTRIES = COUNTRY_FEATURES.map((feature) => {
  const p = feature.properties || {};
  const code = String(p.ISO_A3_EH || p.ISO_A3 || p.ADM0_A3 || '').toUpperCase();
  const label = mercatorPoint([p.LABEL_X, p.LABEL_Y]);
  return { feature, code, name: p.ADMIN || p.NAME_EN || p.NAME || '', center: label, bounds: geometryBounds(feature.geometry), path: geometryPath(feature.geometry) };
});
const COUNTRIES_BY_CODE = new Map(GEOGRAPHIC_COUNTRIES.filter((item) => item.code && item.code !== '-99').map((item) => [item.code, item]));
const POPULATED_PLACES = (placeGeoJSON.features || []).map((feature) => {
  const p = feature.properties || {};
  const coordinate = feature.geometry?.coordinates;
  return { name: String(p.NAME || p.NAMEASCII || p.NAME_EN || '').trim(), point: Array.isArray(coordinate) ? mercatorPoint(coordinate) : null };
}).filter((place) => place.name && place.point);
const ADMIN1_FEATURES = (focusedAdmin1GeoJSON.features || []).map((feature) => {
  const properties = feature.properties || {};
  const point = mercatorPoint([properties.label_lon, properties.label_lat]);
  return {
    feature,
    name: String(properties.name || '').trim(),
    // Match the Python director's canonical ID precedence so v4 feature IDs
    // such as IND-3261 resolve to the exact local Natural Earth polygon.
    code: String(properties.adm1_code || properties.iso_3166_2 || '').toUpperCase(),
    parentCode: String(properties.adm0_a3 || '').toUpperCase(),
    center: point,
    bounds: geometryBounds(feature.geometry),
    path: geometryPath(feature.geometry),
  };
}).filter((region) => region.name && region.path);
// World country boundaries are static during a map move. Keep them as one
// memoized vector subtree so Remotion does not reconcile hundreds of SVG paths
// on every frame; camera movement and the selected country/state remain live.
const CountryOutlines = React.memo(function CountryOutlines({
  surface,
  spotlight,
  fillOpacity: fillOverride,
  strokeColor = '#D8E2EC',
  strokeOpacity: strokeOverride,
}) {
  const fillOpacity = Number.isFinite(Number(fillOverride)) ? Number(fillOverride) : (spotlight ? 0.30 : 0.18);
  const strokeOpacity = Number.isFinite(Number(strokeOverride)) ? Number(strokeOverride) : (spotlight ? 0.58 : 0.45);
  const strokeWidth = spotlight ? 1.2 : 1;
  return (
    <g id="world-continents">
      {GEOGRAPHIC_COUNTRIES.map((country, i) => (
        <path
          key={i}
          d={country.path}
          fill={rgba(surface || '#161B22', fillOpacity)}
          stroke={rgba(strokeColor, strokeOpacity)}
          strokeWidth={strokeWidth}
          vectorEffect="non-scaling-stroke"
          strokeLinejoin="round"
          fillRule="evenodd"
        />
      ))}
    </g>
  );
});
// Curated ports, capitals, and major historical cities with gazetteer coordinates
const CURATED_PLACES = [
  // Regional locator, not a claim about an individual launch pad. NASA's
  // Cape Canaveral image catalog centers the region at 28.5 N, 80.63 W.
  { name: 'Cape Canaveral', point: mercatorPoint([-80.63, 28.5]), countryCode: 'USA' },
  { name: 'Osaka', point: mercatorPoint([135.50107, 34.69379]), countryCode: 'JPN' },
  { name: 'Southampton', point: mercatorPoint([-1.4044, 50.9097]), countryCode: 'GBR' },
  { name: 'New York', point: mercatorPoint([-74.0060, 40.7128]), countryCode: 'USA' },
  { name: 'New York City', point: mercatorPoint([-74.0060, 40.7128]), countryCode: 'USA' },
  { name: 'Cherbourg', point: mercatorPoint([-1.6221, 49.6337]), countryCode: 'FRA' },
  { name: 'Queenstown', point: mercatorPoint([-8.2944, 51.8503]), countryCode: 'IRL' },
  { name: 'Cobh', point: mercatorPoint([-8.2944, 51.8503]), countryCode: 'IRL' },
  { name: 'Belfast', point: mercatorPoint([-5.9301, 54.5973]), countryCode: 'GBR' },
  { name: 'Boston', point: mercatorPoint([-71.0589, 42.3601]), countryCode: 'USA' },
  { name: 'Halifax', point: mercatorPoint([-63.5752, 44.6488]), countryCode: 'CAN' },
  { name: 'London', point: mercatorPoint([-0.1278, 51.5074]), countryCode: 'GBR' },
  { name: 'Paris', point: mercatorPoint([2.3522, 48.8566]), countryCode: 'FRA' },
  { name: 'Berlin', point: mercatorPoint([13.4050, 52.5200]), countryCode: 'DEU' },
  { name: 'Washington', point: mercatorPoint([-77.0369, 38.9072]), countryCode: 'USA' },
];

function normalizePlaceQuery(name) {
  if (!name) return '';
  return String(name)
    .trim()
    .toLowerCase()
    .replace(/\b(?:city|town|port of|port)\b/gi, '')
    .replace(/,\s*.*$/, '')
    .trim();
}

function resolvePlace(name) {
  const query = String(name || '').trim().toLowerCase();
  if (!query) return null;
  const direct = POPULATED_PLACES.find((place) => place.name.toLowerCase() === query)
    || CURATED_PLACES.find((place) => place.name.toLowerCase() === query);
  if (direct) return direct;
  const norm = normalizePlaceQuery(query);
  // Common short form used in narration and title copy. Resolve to the
  // gazetteer's canonical city point so a director-selected Rio map becomes
  // a real geographic animation instead of falling back to a text tag.
  const canonical = norm === 'rio' ? 'rio de janeiro' : norm;
  const canonicalPlace = POPULATED_PLACES.find((place) => place.name.toLowerCase() === canonical)
    || CURATED_PLACES.find((place) => place.name.toLowerCase() === canonical);
  if (canonicalPlace) return canonicalPlace;
  if (norm && norm !== query) {
    return POPULATED_PLACES.find((place) => place.name.toLowerCase() === norm)
      || CURATED_PLACES.find((place) => place.name.toLowerCase() === norm)
      || null;
  }
  return null;
}
function resolveAdmin1(nameOrCode) {
  const query = String(nameOrCode || '').trim().toLowerCase();
  if (!query) return null;
  return ADMIN1_FEATURES.find((region) => region.name.toLowerCase() === query || region.code.toLowerCase() === query) || null;
}
function featureForLegacyCode(code) {
  return COUNTRIES_BY_CODE.get(String(code || '').toUpperCase()) || null;
}

// High-fidelity vector continental landmasses (normalized to 1200 x 700 viewBox)
const WORLD_CONTINENTS = [
  // North America
  "M 110,65 L 175,55 L 245,60 L 310,95 L 340,140 L 325,185 L 290,220 L 260,285 L 220,320 L 175,270 L 140,240 L 95,175 L 85,115 Z",
  // Greenland
  "M 360,35 L 415,30 L 440,65 L 420,105 L 380,110 L 355,75 Z",
  // South America
  "M 265,335 L 310,350 L 360,390 L 375,450 L 340,540 L 310,620 L 280,630 L 260,540 L 245,430 L 255,360 Z",
  // Europe & Mediterranean
  "M 510,120 L 550,105 L 610,115 L 645,95 L 675,130 L 640,185 L 595,200 L 565,225 L 525,235 L 505,215 L 515,175 L 485,170 L 480,150 Z",
  // British Isles
  "M 470,130 L 490,120 L 495,145 L 480,165 L 470,160 Z M 455,140 L 465,135 L 465,155 L 455,150 Z",
  // Scandinavia
  "M 550,55 L 590,45 L 615,80 L 595,120 L 560,105 L 545,80 Z",
  // Africa
  "M 485,255 L 595,250 L 645,280 L 685,330 L 695,410 L 660,500 L 605,570 L 550,560 L 505,470 L 465,360 L 455,290 Z",
  // Asia Main & Russia
  "M 685,100 L 820,80 L 940,95 L 1050,110 L 1110,160 L 1080,240 L 990,260 L 940,320 L 860,340 L 780,310 L 710,260 L 675,185 Z",
  // India Subcontinent
  "M 795,315 L 850,325 L 870,385 L 840,450 L 810,430 L 785,360 Z",
  // East Asia & Japan Archipelago
  "M 975,220 L 1025,230 L 1050,290 L 1010,340 L 965,320 Z M 1060,210 L 1075,200 L 1085,230 L 1070,260 Z",
  // Australia
  "M 980,440 L 1080,430 L 1120,490 L 1110,560 L 1050,600 L 980,570 L 950,500 Z"
];

// Normalized Country Vector Boundary Library (1200 x 700 coordinate space)
export const WORLD_COUNTRIES = {
  USA: {
    name: 'UNITED STATES',
    code: 'USA',
    capital: 'WASHINGTON D.C.',
    coords: '38.9072° N, 77.0369° W',
    center: { x: 230, y: 220 },
    // Continental US polygon
    polygon: '140,175 190,165 270,165 295,190 280,245 255,255 220,285 175,255 140,240 130,195',
    aliases: ['united states', 'usa', 'america', 'us', 'california', 'washington', 'florida', 'texas'],
  },
  GBR: {
    name: 'UNITED KINGDOM',
    code: 'GBR',
    capital: 'LONDON',
    coords: '51.5074° N, 0.1278° W',
    center: { x: 480, y: 145 },
    // British Isles polygon
    polygon: '470,125 490,118 495,145 480,165 468,160 460,140',
    aliases: ['united kingdom', 'uk', 'britain', 'great britain', 'england', 'london'],
  },
  DEU: {
    name: 'GERMANY',
    code: 'DEU',
    capital: 'BERLIN',
    coords: '52.5200° N, 13.4050° E',
    center: { x: 560, y: 148 },
    polygon: '542,130 575,128 580,158 565,168 540,162 538,142',
    aliases: ['germany', 'berlin', 'deutschland'],
  },
  FRA: {
    name: 'FRANCE',
    code: 'FRA',
    capital: 'PARIS',
    coords: '48.8566° N, 2.3522° E',
    center: { x: 520, y: 175 },
    polygon: '502,158 535,152 542,185 528,205 498,198 495,175',
    aliases: ['france', 'paris', 'french'],
  },
  JPN: {
    name: 'JAPAN',
    code: 'JPN',
    capital: 'TOKYO',
    coords: '35.6762° N, 139.6503° E',
    center: { x: 1055, y: 235 },
    // Japanese Archipelago polygon
    polygon: '1040,195 1060,190 1085,225 1075,255 1050,250 1038,220',
    aliases: ['japan', 'tokyo', 'japanese'],
  },
  CHN: {
    name: 'CHINA',
    code: 'CHN',
    capital: 'BEIJING',
    coords: '39.9042° N, 116.4074° E',
    center: { x: 920, y: 245 },
    // China territory polygon
    polygon: '860,180 950,170 1010,210 990,285 930,305 870,280 850,230',
    aliases: ['china', 'beijing', 'shanghai', 'chinese'],
  },
  IND: {
    name: 'INDIA',
    code: 'IND',
    capital: 'NEW DELHI',
    coords: '28.6139° N, 77.2090° E',
    center: { x: 815, y: 350 },
    // Indian subcontinent polygon
    polygon: '785,305 845,310 865,365 835,435 810,420 780,360',
    aliases: ['india', 'new delhi', 'mumbai', 'indian'],
  },
  RUS: {
    name: 'RUSSIA',
    code: 'RUS',
    capital: 'MOSCOW',
    coords: '55.7558° N, 37.6173° E',
    center: { x: 740, y: 125 },
    // Russian landmass polygon
    polygon: '620,80 820,70 1020,85 1100,140 1030,175 850,165 670,160 610,120',
    aliases: ['russia', 'moscow', 'russian', 'soviet'],
  },
  BRA: {
    name: 'BRAZIL',
    code: 'BRA',
    capital: 'BRASILIA',
    coords: '15.8267° S, 47.9218° W',
    center: { x: 320, y: 450 },
    polygon: '275,345 350,375 365,440 335,520 295,490 265,410',
    aliases: ['brazil', 'brasilia', 'rio', 'brazilian'],
  },
  AUS: {
    name: 'AUSTRALIA',
    code: 'AUS',
    capital: 'CANBERRA',
    coords: '35.2809° S, 149.1300° E',
    center: { x: 1030, y: 510 },
    polygon: '975,445 1075,435 1115,490 1095,560 1035,595 975,565 945,500',
    aliases: ['australia', 'canberra', 'sydney', 'melbourne'],
  },
  CAN: {
    name: 'CANADA',
    code: 'CAN',
    capital: 'OTTAWA',
    coords: '45.4215° N, 75.6972° W',
    center: { x: 235, y: 110 },
    polygon: '115,70 180,60 250,65 315,95 330,135 265,155 185,155 130,165 95,120',
    aliases: ['canada', 'ottawa', 'canadian', 'toronto'],
  },
  SAU: {
    name: 'SAUDI ARABIA',
    code: 'SAU',
    capital: 'RIYADH',
    coords: '24.7136° N, 46.6753° E',
    center: { x: 690, y: 295 },
    polygon: '665,265 725,260 740,305 710,340 670,320 655,285',
    aliases: ['saudi arabia', 'saudi', 'riyadh', 'arabia'],
  },
  KOR: {
    name: 'SOUTH KOREA',
    code: 'KOR',
    capital: 'SEOUL',
    coords: '37.5665° N, 126.9780° E',
    center: { x: 1015, y: 235 },
    polygon: '1008,225 1025,225 1022,248 1005,245',
    aliases: ['south korea', 'korea', 'seoul'],
  },
  ITA: {
    name: 'ITALY',
    code: 'ITA',
    capital: 'ROME',
    coords: '41.9028° N, 12.4964° E',
    center: { x: 565, y: 205 },
    polygon: '545,188 565,185 585,215 575,235 558,225 550,200',
    aliases: ['italy', 'rome', 'italian'],
  },
  BEL: {
    name: 'BELGIUM',
    code: 'BEL',
    capital: 'BRUSSELS',
    coords: '50.8503° N, 4.3517° E',
    center: { x: 535, y: 155 },
    polygon: '528,150 545,148 546,162 533,165 526,156',
    aliases: ['belgium', 'brussels', 'antwerp', 'belgian'],
  },
  NLD: {
    name: 'NETHERLANDS',
    code: 'NLD',
    capital: 'AMSTERDAM',
    coords: '52.3676° N, 4.9041° E',
    center: { x: 540, y: 140 },
    polygon: '532,132 548,130 550,146 534,148 530,138',
    aliases: ['netherlands', 'amsterdam', 'holland', 'dutch', 'rotterdam'],
  },
  CHE: {
    name: 'SWITZERLAND',
    code: 'CHE',
    capital: 'BERN',
    coords: '46.9480° N, 7.4474° E',
    center: { x: 545, y: 182 },
    polygon: '535,178 555,176 558,188 538,190',
    aliases: ['switzerland', 'bern', 'geneva', 'zurich', 'swiss'],
  },
  AUT: {
    name: 'AUSTRIA',
    code: 'AUT',
    capital: 'VIENNA',
    coords: '48.2082° N, 16.3738° E',
    center: { x: 575, y: 175 },
    polygon: '562,170 590,168 592,182 565,185',
    aliases: ['austria', 'vienna', 'austrian'],
  },
  ESP: {
    name: 'SPAIN',
    code: 'ESP',
    capital: 'MADRID',
    coords: '40.4168° N, 3.7038° W',
    center: { x: 485, y: 215 },
    polygon: '465,198 505,195 510,230 470,235',
    aliases: ['spain', 'madrid', 'spanish', 'barcelona'],
  },
};

function resolveCountry(nameOrCode) {
  if (!nameOrCode) return null;
  const clean = String(nameOrCode).trim().toUpperCase();
  const lower = clean.toLowerCase();
  const direct = COUNTRIES_BY_CODE.get(clean) || GEOGRAPHIC_COUNTRIES.find((country) => country.name.toUpperCase() === clean);
  if (direct) return direct;
  let legacy = WORLD_COUNTRIES[clean] || null;
  for (const c of Object.values(WORLD_COUNTRIES)) {
    if (c.name === clean || c.code === clean || (c.aliases && c.aliases.includes(lower))) {
      legacy = c;
      break;
    }
  }
  if (!legacy) return null;
  const geography = featureForLegacyCode(legacy.code);
  return geography ? { ...legacy, ...geography } : legacy;
}

/**
 * AnimatedMap — script-grounded world and regional cartography.
 * 
 * Supported Modes & Variants:
 * 1. country_spotlight: script-grounded country highlight and restrained camera move
 * 2. country_corridor / route_flight_arc: accurate 2D route with a drawn, narrated path
 * 3. global_network / multi_country: multiple countries with restrained connecting lines
 */
export function AnimatedMap({ scene = {}, visualDNA = {}, props = {} }) {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const sceneDuration = Math.max(1, scene?.duration_in_frames !== undefined && scene?.duration_in_frames !== null
    ? Math.round(Number(scene.duration_in_frames))
    : Math.round(Number(scene?.duration || 4) * fps));
  const requestedMapStyle = String(
    props.map_shot?.render_style || scene.map_shot?.render_style || props.map_render_style || '',
  ).toLowerCase();
  const hasGlobe = Array.isArray(props.globe_frames) && props.globe_frames.length > 0;
  const hasSatellite = Boolean(props.satellite_image && props.satellite_bounds);
  if (requestedMapStyle === 'globe_3d' && hasGlobe) {
    if (hasSatellite && sceneDuration > fps * 3.5 && props.globe_to_local !== false) {
      const transitionAt = Math.max(1, Math.min(sceneDuration - Math.round(fps * 2.1), Math.round(Number(props.globe_to_local_seconds || 1.7) * fps)));
      const dissolveFrames = Math.max(1, Math.round(fps * .30));
      const dissolve = Math.max(0, Math.min(1, (frame - transitionAt) / dissolveFrames));
      const easedDissolve = dissolve * dissolve * (3 - 2 * dissolve);
      return <AbsoluteFill>
        <AbsoluteFill style={{ opacity: 1 - easedDissolve }}><Globe3D scene={scene} visualDNA={visualDNA} props={props} /></AbsoluteFill>
        <Sequence from={transitionAt} durationInFrames={sceneDuration - transitionAt} name="globe-to-local-satellite">
          <AbsoluteFill style={{ opacity: easedDissolve }}><SatelliteMap scene={scene} visualDNA={visualDNA} props={props} /></AbsoluteFill>
        </Sequence>
      </AbsoluteFill>;
    }
    // A short world-scale reveal should not end on a globe too small to show
    // the actual route. When no satellite plate is available, dissolve into
    // the deterministic local vector map and let its grounded route geometry
    // own the remaining beat. This makes globe_3d a useful opening grammar,
    // not a dead-end mode that silently omits a short route.
    if (!hasSatellite && props.globe_to_local !== false && sceneDuration > fps * 1.35) {
      const transitionAt = Math.max(
        1,
        Math.min(
          sceneDuration - Math.round(fps * .72),
          Math.round(Number(props.globe_to_local_seconds || 1.0) * fps),
        ),
      );
      const dissolveFrames = Math.max(1, Math.round(fps * .24));
      const dissolve = Math.max(0, Math.min(1, (frame - transitionAt) / dissolveFrames));
      const easedDissolve = dissolve * dissolve * (3 - 2 * dissolve);
      const localFrames = Math.max(1, sceneDuration - transitionAt);
      const localScene = {
        ...scene,
        duration_in_frames: localFrames,
        duration: localFrames / fps,
        motion: { ...(scene.motion || {}), entrance: 'none' },
      };
      const localProps = {
        ...props,
        map_render_style: 'vector_editorial',
        map_shot: { ...(props.map_shot || {}), render_style: 'vector_editorial' },
      };
      return <AbsoluteFill>
        <AbsoluteFill style={{ opacity: 1 - easedDissolve }}>
          <Globe3D scene={scene} visualDNA={visualDNA} props={{ ...props, map_label: '' }} />
        </AbsoluteFill>
        <Sequence from={transitionAt} durationInFrames={localFrames} name="globe-to-grounded-vector-route">
          <AbsoluteFill style={{ opacity: easedDissolve }}>
            <AnimatedMap scene={localScene} visualDNA={visualDNA} props={localProps} />
          </AbsoluteFill>
        </Sequence>
      </AbsoluteFill>;
    }
    return <Globe3D scene={scene} visualDNA={visualDNA} props={props} />;
  }
  if (hasSatellite && requestedMapStyle !== 'vector_editorial') {
    return <SatelliteMap scene={scene} visualDNA={visualDNA} props={props} />;
  }
  const mapStart = cueStartFrame(scene, 'map', fps, 0);
  const mapMotionFrames = Math.max(1, Math.min(sceneDuration - mapStart, Math.round(fps * 2.8)));
  const progress = Math.min(1, Math.max(0, (frame - mapStart) / mapMotionFrames));

  const palette = { ...(visualDNA.palette || {}), ...(props.map_palette || {}) };
  const accentColor = palette.accent || '#E8B94F';
  const secondaryColor = palette.secondary || '#D7E2E8';
  const displayFont = visualDNA.typography?.display || 'Plus Jakarta Sans';
  const mapStyle = String(props.map_style || visualDNA.map_style || 'dark_desaturated').toLowerCase();
  // The standalone renderer stages public files behind its per-render asset
  // server. Allow the director/manifest to provide that resolved URL so maps
  // do not accidentally request /public/... from the app shell's origin.
  const requestedMapRaster = typeof (props.map_background_src || scene.map_background_src) === 'string'
    ? String(props.map_background_src || scene.map_background_src).trim()
    : '';
  const knownMaps = new Set([
    'map-assets/nasa-blue-marble-topography-mercator.jpg',
    'map-assets/nasa-blue-marble-july-5400.jpg',
    'map-assets/panama-canal-satellite-eox-2016.jpg'
  ]);
  const cleanRaster = requestedMapRaster.replace(/^\/+/, '');
  const verifiedRaster = knownMaps.has(cleanRaster) ? cleanRaster : 'map-assets/nasa-blue-marble-topography-mercator.jpg';
  const mapRasterSrc = /^(?:https?:|data:|blob:)/i.test(requestedMapRaster)
    ? requestedMapRaster
    : staticFile(verifiedRaster);

  const vectorOnlyMap = ['minimal_monochrome', 'blueprint_slate'].includes(mapStyle);
  const mapBackground = mapStyle === 'minimal_monochrome'
    ? '#171B1D'
    : mapStyle === 'blueprint_slate'
      ? '#0C1B2B'
      : (palette.background || '#080C12');
  const mapLand = mapStyle === 'minimal_monochrome'
    ? '#697277'
    : mapStyle === 'blueprint_slate'
      ? '#17334A'
      : (palette.surface || '#161B22');
  const boundaryColor = mapStyle === 'blueprint_slate' ? '#73A5C8' : '#D8E2EC';
  const mapRasterFilter = mapStyle === 'satellite_tactical'
    ? 'saturate(.92) brightness(.84) contrast(1.12)'
    : mapStyle === 'archival_sepia'
      ? 'sepia(.22) saturate(.78) brightness(.82) contrast(1.10)'
      : 'saturate(.82) brightness(.80) contrast(1.10)';

  // Do not spring or translate the whole map while its cartographic camera is
  // already moving. A restrained opacity/mask entrance keeps those motions
  // from stacking into a visible wobble.
  const enter = easeMapCamera(frame / Math.max(1, fps * 0.32));
  const requestedMapEntrance = String(scene?.motion?.entrance || 'fade').toLowerCase();
  const mapEntrance = ['mask_reveal', 'fade', 'cut', 'hard_cut', 'none'].includes(requestedMapEntrance)
    ? requestedMapEntrance
    : 'fade';
  const entranceStyle = computeEntranceStyle(mapEntrance, enter, progress, true);

  // Variant resolution
  const mapShot = props.map_shot || scene.map_shot || {};
  const mapLocations = Array.isArray(mapShot.location_names) ? mapShot.location_names : [];
  const approvedMapCopy = scene.editorial_contract?.approved === true
    ? String(scene.editorial_contract.display_text || '').trim() : '';
  const approvedPlace = approvedMapCopy && String(scene.narration || '').toLowerCase().includes(approvedMapCopy.toLowerCase())
    ? resolvePlace(approvedMapCopy) : null;
  // Try each candidate until one resolves. A canonical feature id can be
  // present but stale for this Natural Earth snapshot while the spoken place
  // name still resolves correctly (for example, IND-3261 and Madhya Pradesh).
  const targetAdmin1 = [
    props.admin1,
    props.state,
    props.region,
    ...(Array.isArray(mapShot.feature_ids) ? mapShot.feature_ids : []),
    ...mapLocations.filter((name) => !resolvePlace(name) && !resolveCountry(name)),
  ].map(resolveAdmin1).find(Boolean) || null;
  const variant = String(props.variant || scene.primary_visual?.variant || 'route_flight_arc').toLowerCase();
  const hasCorridorEndpoints = Boolean((props.from || props.start || props.startCity) && (props.to || props.end || props.endCity));
  // Narration can establish two places without establishing a path between
  // them. Keep the geographic context and camera fit, but omit the transit
  // trace and moving vehicle for those uncertain or comparative locations.
  const contextOnly = props.route_supported === false || props.connection_kind === 'context_only';
  const isCorridor = hasCorridorEndpoints || Boolean(props.p1 && props.p2);
  const hasCountryShot = Boolean(props.country || props.target_country || targetAdmin1
    || resolvePlace(props.location || props.place)
    || mapLocations.some((name) => resolveCountry(name) || resolvePlace(name)));
  const isCountrySpotlight = !isCorridor && (variant === 'country_spotlight' || hasCountryShot);
  const isGlobalNetwork = variant === 'global_network' || variant === 'multi_country' || Array.isArray(props.highlight_countries);
  const requestedMotionProfile = String(mapShot.motion_profile || props.motion_profile || 'auto').toLowerCase();
  const motionProfile = requestedMotionProfile !== 'auto'
    ? requestedMotionProfile
    : (isCorridor ? 'route_trace' : (variant === 'territory_zoom_highlight' || targetAdmin1 ? 'boundary_trace' : (isGlobalNetwork ? 'wide_context' : (mapLocations.some((name) => resolvePlace(name)) ? 'city_reveal' : 'world_to_region'))));

  const explicitCountries = props.highlight_countries || props.countries;
  const hasGroundedMap = Boolean(
    props.country || props.target_country
    || (Array.isArray(explicitCountries) && explicitCountries.length >= 2)
    || hasCorridorEndpoints
    || Boolean(targetAdmin1)
    // Deterministic map plans can carry a catalog-resolved city in `location`
    // without the optional LLM map_shot object. Recognize that grounded place
    // here too; otherwise the full-frame map scene hides its media base and
    // renders only the near-black canvas.
    || Boolean(resolvePlace(props.location || props.place))
    || mapLocations.length > 0
    || (props.p1 && props.p2)
  );
  if (!hasGroundedMap) return null;

  // Country targets
  const rawOrigin = props.from || props.start || props.startCity || '';
  const rawDestination = props.to || props.end || props.endCity || '';
  let targetPlace = approvedPlace || [props.location, props.place, ...mapLocations]
    .map((candidate) => resolvePlace(candidate))
    .find(Boolean) || null;
  const originPlace = resolvePlace(rawOrigin);
  const destPlace = resolvePlace(rawDestination);
  const targetCountry = resolveCountry(props.country || props.target_country || targetAdmin1?.parentCode || mapLocations.find((name) => resolveCountry(name)));
  const originCountry = resolveCountry(props.from_country || rawOrigin || originPlace?.countryCode);
  const destCountry = resolveCountry(props.to_country || rawDestination || destPlace?.countryCode);
  const sameCountry = Boolean(originCountry && destCountry && originCountry.code === destCountry.code);

  // Multi-countries for global network mode
  const rawHighlightList = props.highlight_countries || props.countries || [];
  const highlightCountryObjects = (Array.isArray(rawHighlightList) ? rawHighlightList : [rawHighlightList])
    .map(resolveCountry)
    .filter(Boolean);
  if (isCountrySpotlight && !targetCountry && !targetPlace) {
    targetPlace = originPlace || destPlace;
    if (!targetPlace) return null;
  }
  if (isGlobalNetwork && highlightCountryObjects.length < 2) return null;

  // Draw progress for a route explicitly supported by the scene.
  const routeDraw = interpolate(progress, motionProfile === 'route_trace' ? [0.06, 0.70] : [0.14, 0.88], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Coordinates
  const explicitCoordinate = (point) => {
    if (!point || typeof point !== 'object') return null;
    if (Number.isFinite(Number(point.x)) && Number.isFinite(Number(point.y))) return point;
    if (Number.isFinite(Number(point.lon)) && Number.isFinite(Number(point.lat))) return mercatorPoint([Number(point.lon), Number(point.lat)]);
    return null;
  };
  const spotlightCenter = targetAdmin1?.center || targetPlace?.point || targetCountry?.center || null;
  const mapAnchor = spotlightCenter || highlightCountryObjects[0]?.center || null;
  const p1 = explicitCoordinate(props.p1) || explicitCoordinate(props.from_coordinates) || originPlace?.point || originCountry?.center || mapAnchor;
  const p2 = explicitCoordinate(props.p2) || explicitCoordinate(props.to_coordinates) || destPlace?.point || destCountry?.center || targetCountry?.center || highlightCountryObjects[1]?.center || p1;
  if (!isCountrySpotlight && !isGlobalNetwork && (!p1 || !p2)) return null;
  const requestedCameraZoom = Number(props.camera_zoom);
  const cameraZoomOverride = props.camera_zoom !== undefined && props.camera_zoom !== null && Number.isFinite(requestedCameraZoom)
    ? Math.max(1, Math.min(4.2, requestedCameraZoom))
    : null;
  const startName = String(originPlace?.name || rawOrigin || originCountry?.name || '').toUpperCase();
  const endName = String(destPlace?.name || rawDestination || destCountry?.name || '').toUpperCase();
  const rawMapTitle = String(props.map_label || targetAdmin1?.name || targetCountry?.name || targetPlace?.name || props.title || '');
  const mapTitle = rawMapTitle
    .replace(/^(?:geographic reference|map reference|map|country|territory|location|nation|place)\s*[:—-]\s*/i, '')
    .trim()
    .toUpperCase();
  const mapDate = String(props.date || props.year || '').trim().toUpperCase();

  // Camera Motion Dynamics. Every profile has a distinct start/end framing,
  // but each camera move eases once in one direction and then holds.
  const distPx = Math.hypot(p2.x - p1.x, p2.y - p1.y);
  const cameraProgress = easeMapCamera(progress);
  const mapUnitScale = Math.min((Number(width || 1920) + 240) / MAP_WIDTH, (Number(height || 1080) + 240) / MAP_HEIGHT);
  let camPanX = 0;
  let camPanY = 0;
  let camZoom = 1.05;
  let camTilt = 0;
  let camRotate = 0;

  if (isCountrySpotlight && spotlightCenter) {
    // Fit a useful regional view around the highlighted country. A fixed
    // world-scale zoom leaves small countries like Belgium as an unreadable dot.
    const targetCenterX = spotlightCenter.x;
    const targetCenterY = spotlightCenter.y;
    const bounds = targetAdmin1?.bounds || (targetPlace ? null : targetCountry?.bounds);
    const fitZoom = bounds?.width && bounds?.height
      ? Math.min((MAP_WIDTH * 0.38) / bounds.width, (MAP_HEIGHT * 0.48) / bounds.height)
      : 4.5;
    const fittedZoom = Math.max(1.25, Math.min(3.05, fitZoom));
    const targetZoom = motionProfile === 'wide_context' ? Math.min(1.65, fittedZoom) : fittedZoom;
    const targetPanX = (600 - targetCenterX) * mapUnitScale * targetZoom;
    const targetPanY = (350 - targetCenterY) * mapUnitScale * targetZoom;
    const endPanScale = motionProfile === 'wide_context' ? 0.44 : 1;
    const startPanScale = motionProfile === 'city_reveal' ? 0.34 : motionProfile === 'boundary_trace' ? 0.12 : 0;
    const startZoom = motionProfile === 'wide_context' ? 1.03
      : motionProfile === 'city_reveal' ? Math.max(1.05, targetZoom * 0.68)
        : motionProfile === 'boundary_trace' ? Math.max(1.05, targetZoom * 0.78)
          : 1.03;
    camPanX = lerpCamera(targetPanX * startPanScale, targetPanX * endPanScale, cameraProgress);
    camPanY = lerpCamera(targetPanY * startPanScale, targetPanY * endPanScale, cameraProgress);
    camZoom = lerpCamera(startZoom, targetZoom, cameraProgress);
    camTilt = 0;
    camRotate = 0;
  } else if (isGlobalNetwork) {
    // A restrained one-way drift keeps the globe-oriented overview alive
    // without oscillating around its final frame.
    camPanX = lerpCamera(-10, 10, cameraProgress);
    camPanY = lerpCamera(5, -5, cameraProgress);
    camZoom = lerpCamera(1.025, 1.055, cameraProgress);
    camTilt = 0;
  } else {
    // Route framing is resolved below from its semantic motion profile.
    camTilt = 0;
    camRotate = 0;
  }

  // Route scenes use distinct framing targets, but a single eased camera move.
  if (!isCountrySpotlight && !isGlobalNetwork && p1 && p2) {
    const midpoint = { x: (p1.x + p2.x) / 2, y: (p1.y + p2.y) / 2 };
    const focus = motionProfile === 'city_reveal' ? p2 : midpoint;
    const fittedRouteZoom = distPx < 180
      ? Math.max(1.35, Math.min(3.05, (MAP_WIDTH * 0.46) / Math.max(24, Math.abs(p2.x - p1.x) * 2.4), (MAP_HEIGHT * 0.34) / Math.max(24, Math.abs(p2.y - p1.y) * 2.4)))
      : 1.35;
    const endZoom = motionProfile === 'wide_context' ? 1.055 : fittedRouteZoom;
    const startZoom = motionProfile === 'city_reveal' ? Math.max(1.04, Math.min(1.16, fittedRouteZoom * 0.62))
      : motionProfile === 'boundary_trace' ? 1.12
        : motionProfile === 'route_trace' ? 1.08
          : 1.025;
    const targetPanX = (600 - focus.x) * mapUnitScale * endZoom;
    const targetPanY = (350 - focus.y) * mapUnitScale * endZoom;
    const startFocus = motionProfile === 'route_trace' ? p1 : { x: 600, y: 350 };
    const startPanX = (600 - startFocus.x) * mapUnitScale * startZoom;
    const startPanY = (350 - startFocus.y) * mapUnitScale * startZoom;
    const endPanScale = motionProfile === 'wide_context' ? 0.34 : 1;
    camZoom = lerpCamera(startZoom, endZoom, cameraProgress);
    camPanX = lerpCamera(startPanX, targetPanX * endPanScale, cameraProgress);
    camPanY = lerpCamera(startPanY, targetPanY * endPanScale, cameraProgress);
  }

  // A directed close-up is useful for compact infrastructure such as a canal
  // or border crossing, where the automatic regional cap leaves endpoints
  // nearly on top of each other. Keep the world-to-site move smooth and center
  // the final view on the verified route coordinates.
  if (cameraZoomOverride !== null && !isCountrySpotlight && !isGlobalNetwork && p1 && p2) {
    const midpoint = { x: (p1.x + p2.x) / 2, y: (p1.y + p2.y) / 2 };
    const targetPanX = (600 - midpoint.x) * mapUnitScale * cameraZoomOverride;
    const targetPanY = (350 - midpoint.y) * mapUnitScale * cameraZoomOverride;
    const startZoom = Math.max(1.04, Math.min(1.18, cameraZoomOverride * 0.48));
    camZoom = lerpCamera(startZoom, cameraZoomOverride, cameraProgress);
    camPanX = lerpCamera(0, targetPanX, cameraProgress);
    camPanY = lerpCamera(0, targetPanY, cameraProgress);
  }

  // The map canvas has a 120 px overscan on each side. Large pans during the
  // opening zoom used to move that canvas past the composition edge and expose
  // the plain background as a hard vertical band. Clamp only the camera move
  // to the coverage the overscanned canvas can actually provide.
  const canvasWidth = Number(width || 1920) + 240;
  const canvasHeight = Number(height || 1080) + 240;
  const maxPanX = Math.max(0, (canvasWidth * camZoom - Number(width || 1920)) / 2);
  const maxPanY = Math.max(0, (canvasHeight * camZoom - Number(height || 1080)) / 2);
  camPanX = Math.max(-maxPanX, Math.min(maxPanX, camPanX));
  camPanY = Math.max(-maxPanY, Math.min(maxPanY, camPanY));

  // Keep labels, strokes, and pins at a constant screen size during closeups.
  const arcHeight = (distPx < 150 ? Math.max(12, distPx * 0.18) : 130) / Math.max(1, camZoom);
  const flightArcHeight = variant === 'route_flight_arc' ? Math.max(24, arcHeight * 1.4) : 0;
  const control = { x: (p1.x + p2.x) / 2, y: Math.min(p1.y, p2.y) - flightArcHeight };
  const destArrived = routeDraw > 0.85;
  const routeT = Math.max(0, Math.min(1, routeDraw));
  const inverseT = 1 - routeT;
  const vesselPoint = variant === 'city_to_city_transit'
    ? { x: p1.x + (p2.x - p1.x) * routeT, y: p1.y + (p2.y - p1.y) * routeT }
    : {
      x: inverseT * inverseT * p1.x + 2 * inverseT * routeT * control.x + routeT * routeT * p2.x,
      y: inverseT * inverseT * p1.y + 2 * inverseT * routeT * control.y + routeT * routeT * p2.y,
    };
  const vesselHeading = Math.atan2(
    2 * inverseT * (control.y - p1.y) + 2 * routeT * (p2.y - control.y),
    2 * inverseT * (control.x - p1.x) + 2 * routeT * (p2.x - control.x),
  ) * 180 / Math.PI;

  const mapFontSize = 14 / Math.max(1, camZoom);
  const labelOffset = 12 / Math.max(1, camZoom);
  const labelText = (name, point, other, color, key) => {
    if (!name) return null;
    const toRight = point.x <= other.x;
    const labelX = Math.max(28, Math.min(MAP_WIDTH - 28, point.x + (toRight ? labelOffset : -labelOffset)));
    const labelY = Math.max(30, Math.min(MAP_HEIGHT - 22, point.y + (point.y <= other.y ? -labelOffset * 1.25 : labelOffset * 1.75)));
    return (
      <g key={key} pointerEvents="none">
        <path d={`M ${point.x} ${point.y} L ${labelX + (toRight ? -4 : 4)} ${labelY - 4}`} stroke={rgba(color, 0.9)} strokeWidth={1.2 / Math.max(1, camZoom)} fill="none" />
        <text
          x={labelX}
          y={labelY}
          textAnchor={toRight ? 'start' : 'end'}
          fill="#F8FAFC"
          fontFamily={displayFont}
          fontSize={mapFontSize}
          fontWeight="700"
          letterSpacing=".04em"
          paintOrder="stroke"
          stroke={rgba(palette.background || '#071018', 0.9)}
          strokeWidth={3 / Math.max(1, camZoom)}
        >{name}</text>
      </g>
    );
  };

  return (
    <AbsoluteFill style={{ background: mapBackground, overflow: 'hidden', ...entranceStyle }}>
      {/* 2D cartographic camera: projection remains true and undistorted. */}
      <div
        style={{
          position: 'absolute',
          inset: -120,
          transformStyle: 'flat',
        }}
      >
        <div
          style={{
            width: '100%',
            height: '100%',
            transform: `translate3d(${camPanX}px, ${camPanY}px, 0) scale(${camZoom}) rotateX(${camTilt}deg) rotateZ(${camRotate}deg)`,
            transformOrigin: '50% 50%',
            willChange: 'transform',
          }}
        >
          {!vectorOnlyMap && <Img src={mapRasterSrc} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', opacity: mapStyle === 'archival_sepia' ? 0.70 : 0.86, filter: mapRasterFilter }} />}
          {mapStyle === 'blueprint_slate' && <div style={{ position: 'absolute', inset: 0, opacity: 0.20, backgroundImage: 'linear-gradient(rgba(126,180,214,.35) 1px, transparent 1px), linear-gradient(90deg, rgba(126,180,214,.35) 1px, transparent 1px)', backgroundSize: '72px 72px' }} />}
          {/* A quiet atmospheric field keeps the local map readable. */}
          <div
            style={{
              position: 'absolute',
              inset: 0,
              background: `radial-gradient(ellipse at 50% 46%, ${rgba(accentColor, mapStyle === 'minimal_monochrome' ? 0.025 : 0.055)}, transparent 68%)`,
            }}
          />

          {/* Master Map SVG Rendering */}
          <svg width="100%" height="100%" viewBox="0 0 1200 700" style={{ position: 'absolute', inset: 0 }}>
            {/* Base Vector Continents */}
            <CountryOutlines
              surface={mapLand}
              spotlight={isCountrySpotlight}
              fillOpacity={vectorOnlyMap ? 0.48 : undefined}
              strokeColor={boundaryColor}
              strokeOpacity={vectorOnlyMap ? 0.75 : undefined}
            />

            {/* =========================================================================
                COUNTRY SPOTLIGHT / HIGHLIGHTED TERRITORIES
               ========================================================================= */}
            {/* Mode A: Single Country Spotlight */}
            {isCountrySpotlight && targetCountry && (
              <g id="spotlight-country">
                <path
                  d={targetCountry.path}
                  fill={rgba(accentColor, targetAdmin1 ? 0.07 : 0.30)}
                  stroke={rgba(accentColor, targetAdmin1 ? 0.52 : 1)}
                  strokeWidth={2 / Math.max(1, camZoom)}
                  strokeLinejoin="round"
                  fillRule="evenodd"
                />
                {motionProfile === 'boundary_trace' && !targetAdmin1 && <path
                  d={targetCountry.path}
                  pathLength={1000}
                  fill="none"
                  stroke="#FFFFFF"
                  strokeWidth={3 / Math.max(1, camZoom)}
                  strokeLinejoin="round"
                  strokeDasharray="1000 1000"
                  strokeDashoffset={interpolate(progress, [0.08, 0.7], [1000, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' })}
                  fillRule="evenodd"
                />}
                {!targetAdmin1 && <path
                  d={targetCountry.path}
                  fill="none"
                  stroke="rgba(255,255,255,.84)"
                  strokeWidth={0.8 / Math.max(1, camZoom)}
                  fillRule="evenodd"
                />}
                {/* Natural Earth label point: a geographic anchor, not a capital marker. */}
                {!targetAdmin1 && !targetPlace && <circle cx={targetCountry.center.x} cy={targetCountry.center.y} r={3.2 / Math.max(1, camZoom)} fill={accentColor} stroke="#F8FAFC" strokeWidth={1.2 / Math.max(1, camZoom)} />}
                {!targetAdmin1 && !targetPlace && mapTitle && <g pointerEvents="none">
                  <path d={`M ${targetCountry.center.x + 7 / camZoom} ${targetCountry.center.y - 5 / camZoom} L ${targetCountry.center.x + 22 / camZoom} ${targetCountry.center.y - 21 / camZoom}`} fill="none" stroke={rgba(accentColor, 0.9)} strokeWidth={1.2 / Math.max(1, camZoom)} />
                  <text x={targetCountry.center.x + 26 / camZoom} y={targetCountry.center.y - 24 / camZoom} fill="#FFFFFF" fontFamily={displayFont} fontSize={mapFontSize} fontWeight="700" letterSpacing=".08em" paintOrder="stroke" stroke={rgba(palette.background || '#071018', 0.88)} strokeWidth={3 / Math.max(1, camZoom)}>{mapTitle}</text>
                </g>}
              </g>
            )}
            {isCountrySpotlight && targetAdmin1 && (
              <g id="spotlight-admin1">
                <path d={targetAdmin1.path} fill={rgba(accentColor, 0.44)} stroke={accentColor} strokeWidth={2.3 / Math.max(1, camZoom)} strokeLinejoin="round" fillRule="evenodd" />
                {motionProfile === 'boundary_trace' && <path
                  d={targetAdmin1.path}
                  pathLength={1000}
                  fill="none"
                  stroke="#FFFFFF"
                  strokeWidth={3.1 / Math.max(1, camZoom)}
                  strokeLinejoin="round"
                  strokeDasharray="1000 1000"
                  strokeDashoffset={interpolate(progress, [0.10, 0.66], [1000, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' })}
                  fillRule="evenodd"
                />}
                <path d={targetAdmin1.path} fill="none" stroke="rgba(255,255,255,.9)" strokeWidth={0.85 / Math.max(1, camZoom)} fillRule="evenodd" />
                <circle cx={targetAdmin1.center.x} cy={targetAdmin1.center.y} r={3.2 / Math.max(1, camZoom)} fill={accentColor} stroke="#F8FAFC" strokeWidth={1.2 / Math.max(1, camZoom)} />
                <g pointerEvents="none"><path d={`M ${targetAdmin1.center.x + 7 / camZoom} ${targetAdmin1.center.y - 5 / camZoom} L ${targetAdmin1.center.x + 22 / camZoom} ${targetAdmin1.center.y - 21 / camZoom}`} fill="none" stroke={rgba(accentColor, .95)} strokeWidth={1.2 / Math.max(1, camZoom)} /><text x={targetAdmin1.center.x + 26 / camZoom} y={targetAdmin1.center.y - 24 / camZoom} fill="#FFFFFF" fontFamily={displayFont} fontSize={mapFontSize} fontWeight="750" letterSpacing=".06em" paintOrder="stroke" stroke={rgba(palette.background || '#071018', .95)} strokeWidth={3 / Math.max(1, camZoom)}>{targetAdmin1.name.toUpperCase()}</text></g>
              </g>
            )}
            {isCountrySpotlight && targetPlace && targetCountry && (
              <g id="spotlight-city">
                <circle cx={targetPlace.point.x} cy={targetPlace.point.y} r={4.6 / Math.max(1, camZoom)} fill={secondaryColor} stroke="#FFFFFF" strokeWidth={1.6 / Math.max(1, camZoom)} />
                <circle cx={targetPlace.point.x} cy={targetPlace.point.y} r={9 / Math.max(1, camZoom)} fill="none" stroke={rgba(secondaryColor, .84)} strokeWidth={1.2 / Math.max(1, camZoom)} opacity={interpolate(progress, [0, .7, 1], [.2, .9, .2], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' })} />
                {motionProfile === 'city_reveal' && <circle cx={targetPlace.point.x} cy={targetPlace.point.y} r={interpolate(progress, [0, 0.72, 1], [5, 18, 26], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }) / Math.max(1, camZoom)} fill="none" stroke={rgba(secondaryColor, .7)} strokeWidth={1 / Math.max(1, camZoom)} opacity={interpolate(progress, [0, 0.5, 1], [0, 0.82, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' })} />}
                <path d={`M ${targetPlace.point.x + 8 / camZoom} ${targetPlace.point.y - 4 / camZoom} L ${targetPlace.point.x + 22 / camZoom} ${targetPlace.point.y - 20 / camZoom}`} fill="none" stroke={secondaryColor} strokeWidth={1.2 / Math.max(1, camZoom)} />
                <text x={targetPlace.point.x + 27 / camZoom} y={targetPlace.point.y - 23 / camZoom} fill="#FFFFFF" fontFamily={displayFont} fontSize={mapFontSize} fontWeight="750" letterSpacing=".07em" paintOrder="stroke" stroke={rgba(palette.background || '#071018', .95)} strokeWidth={3 / Math.max(1, camZoom)}>{targetPlace.name.toUpperCase()}</text>
              </g>
            )}
            {isCountrySpotlight && targetPlace && !targetCountry && (
              <g id="spotlight-place">
                <circle cx={targetPlace.point.x} cy={targetPlace.point.y} r={4 / Math.max(1, camZoom)} fill={accentColor} stroke="#F8FAFC" strokeWidth={1.4 / Math.max(1, camZoom)} />
                {mapTitle && <g pointerEvents="none">
                  <path d={`M ${targetPlace.point.x + 8 / camZoom} ${targetPlace.point.y - 4 / camZoom} L ${targetPlace.point.x + 22 / camZoom} ${targetPlace.point.y - 19 / camZoom}`} fill="none" stroke={rgba(accentColor, 0.9)} strokeWidth={1.2 / Math.max(1, camZoom)} />
                  <text x={targetPlace.point.x + 26 / camZoom} y={targetPlace.point.y - 22 / camZoom} fill="#FFFFFF" fontFamily={displayFont} fontSize={mapFontSize} fontWeight="700" letterSpacing=".08em" paintOrder="stroke" stroke={rgba(palette.background || '#071018', 0.88)} strokeWidth={3 / Math.max(1, camZoom)}>{mapTitle}</text>
                </g>}
              </g>
            )}

            {/* Mode B: Narrated origin and destination regions */}
            {!isCountrySpotlight && !isGlobalNetwork && (
              <>
                {originCountry && !sameCountry && (
                  <path
                    d={originCountry.path}
                    fill={rgba(accentColor, 0.12)}
                    stroke={accentColor}
                    strokeWidth={1.3 / Math.max(1, camZoom)}
                    strokeLinejoin="round"
                    fillRule="evenodd"
                  />
                )}
                {sameCountry && originCountry && (
                  <path
                    d={originCountry.path}
                    fill="none"
                    stroke={rgba(accentColor, 0.72)}
                    strokeWidth={1.1 / Math.max(1, camZoom)}
                    strokeLinejoin="round"
                    fillRule="evenodd"
                  />
                )}
                {destCountry && !sameCountry && (
                  <path
                    d={destCountry.path}
                    fill={rgba(secondaryColor, destArrived ? 0.20 : 0.10)}
                    stroke={secondaryColor}
                    strokeWidth={(destArrived ? 1.6 : 1.2) / Math.max(1, camZoom)}
                    strokeLinejoin="round"
                    fillRule="evenodd"
                  />
                )}
              </>
            )}

            {/* Mode C: Global Network Mode */}
            {isGlobalNetwork && highlightCountryObjects.map((c, idx) => (
              <g key={`net-${c.code}-${idx}`}>
                <path
                  d={c.path}
                  fill={rgba(accentColor, 0.20)}
                  stroke={accentColor}
                  strokeWidth="1.5"
                  strokeLinejoin="round"
                  fillRule="evenodd"
                />
                <circle cx={c.center.x} cy={c.center.y} r="3" fill={accentColor} stroke="#F8FAFC" strokeWidth="1" />
                <text x={c.center.x + 7} y={c.center.y - 7} fill="#FFFFFF" fontFamily={displayFont} fontSize={mapFontSize} fontWeight="650" letterSpacing=".05em" paintOrder="stroke" stroke={rgba(palette.background || '#071018', 0.9)} strokeWidth={3 / Math.max(1, camZoom)}>{c.name}</text>
              </g>
            ))}

            {/* Route line (Corridor Mode) */}
            {!isCountrySpotlight && !isGlobalNetwork && !contextOnly && (
              <>
                {/* A transit connection is a direct trace; only narrated flight gets an arc. */}
                <path
                  d={variant === 'city_to_city_transit'
                    ? `M ${p1.x} ${p1.y} L ${p2.x} ${p2.y}`
                    : `M ${p1.x} ${p1.y} Q ${control.x} ${control.y} ${p2.x} ${p2.y}`}
                  pathLength="1"
                  stroke={accentColor}
                  strokeWidth={1.8 / Math.max(1, camZoom)}
                  fill="none"
                  strokeDasharray="1"
                  strokeDashoffset={interpolate(routeDraw, [0, 1], [1, 0])}
                  strokeLinecap="round"
                  opacity="0.86"
                />
                <circle cx={p1.x} cy={p1.y} r={3.5 / Math.max(1, camZoom)} fill={accentColor} stroke="#F8FAFC" strokeWidth={1.2 / Math.max(1, camZoom)} />
                <circle cx={p2.x} cy={p2.y} r={3.5 / Math.max(1, camZoom)} fill={destArrived ? secondaryColor : '#F8FAFC'} stroke={secondaryColor} strokeWidth={1.2 / Math.max(1, camZoom)} />
                {routeDraw > 0.02 && variant === 'route_flight_arc' && <g
                  transform={`translate(${vesselPoint.x} ${vesselPoint.y}) rotate(${vesselHeading}) scale(${1 / Math.max(1, camZoom)})`}
                  opacity={interpolate(routeDraw, [0.02, 0.12, 0.92, 1], [0, 1, 1, 0.9], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' })}
                  pointerEvents="none"
                >
                  <path d="M 12 0 L 2 3 L 1 10 L -2 10 L -3 3 L -12 5 L -12 2 L -3 0 L -2 -2 L -1 -9 L 2 -9 L 3 -2 Z" fill={accentColor} stroke="#F8FAFC" strokeWidth=".8" strokeLinejoin="round" />
                </g>}
                {routeDraw > 0.02 && variant === 'city_to_city_transit' && <g transform={`translate(${vesselPoint.x} ${vesselPoint.y}) scale(${1 / Math.max(1, camZoom)})`} opacity={interpolate(routeDraw, [0.02, 0.12, 0.92, 1], [0, 1, 1, 0.9], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' })} pointerEvents="none">
                  <circle r="5" fill={accentColor} stroke="#F8FAFC" strokeWidth="1.3" />
                  <circle r="10" fill="none" stroke={accentColor} strokeWidth="1.1" opacity=".72" />
                </g>}
              </>
            )}
            {!isCountrySpotlight && !isGlobalNetwork && contextOnly && [
              { point: p1, name: startName }, { point: p2, name: endName },
            ].filter(item => item.point && item.name).map((item, index) => <g key={`context-place-${index}`}>
              <circle cx={item.point.x} cy={item.point.y} r={4 / Math.max(1, camZoom)} fill={accentColor} stroke="#FFFFFF" strokeWidth={1.2 / Math.max(1, camZoom)} />
            </g>)}
          </svg>

          {!isCountrySpotlight && !isGlobalNetwork && (
            <svg width="100%" height="100%" viewBox={`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`} preserveAspectRatio="xMidYMid meet" style={{ position: 'absolute', inset: 0, overflow: 'visible', pointerEvents: 'none' }}>
              {labelText(startName, p1, p2, accentColor, 'origin-label')}
              {labelText(endName, p2, p1, secondaryColor, 'destination-label')}
            </svg>
          )}
        </div>
      </div>

      {!isCountrySpotlight && !isGlobalNetwork && mapTitle && (
        <div style={{ position: 'absolute', zIndex: 6, left: '7.5%', top: '8%', maxWidth: '48%', pointerEvents: 'none' }}>
          <div style={{ width: 46, height: 3, background: accentColor, marginBottom: 12, transform: `scaleX(${enter})`, transformOrigin: 'left' }} />
          <div style={{ color: '#F8FAFC', fontFamily: displayFont, fontSize: 22, fontWeight: 800, letterSpacing: '.10em', textTransform: 'uppercase', textShadow: `0 2px 14px ${rgba(palette.background || '#071018', .9)}` }}>{mapTitle}</div>
          <div style={{ marginTop: 7, color: rgba('#F8FAFC', .74), fontFamily: 'JetBrains Mono, monospace', fontSize: 12, fontWeight: 650, letterSpacing: '.12em', textTransform: 'uppercase' }}>{startName} / {endName}</div>
          {mapDate && <div style={{ marginTop: 7, color: accentColor, fontFamily: 'JetBrains Mono, monospace', fontSize: 13, fontWeight: 750, letterSpacing: '.14em' }}>{mapDate}</div>}
        </div>
      )}
      {(isCountrySpotlight || isGlobalNetwork) && mapDate && (
        <div style={{ position: 'absolute', zIndex: 6, left: '7.5%', top: '8%', pointerEvents: 'none', color: accentColor, fontFamily: 'JetBrains Mono, monospace', fontSize: 13, fontWeight: 750, letterSpacing: '.14em', textShadow: `0 2px 12px ${rgba(palette.background || '#071018', .95)}` }}>{mapDate}</div>
      )}

      {/* 3. Cinematic Vignette & Atmospheric Contrast */}
      <AbsoluteFill
        style={{
          boxShadow: mapStyle === 'minimal_monochrome'
            ? 'inset 0 0 84px rgba(0,0,0,0.28)'
            : 'inset 0 0 96px rgba(0,0,0,0.32)',
          pointerEvents: 'none',
          zIndex: 5,
        }}
      />
    </AbsoluteFill>
  );
}
