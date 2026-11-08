import React from 'react';
import { AbsoluteFill, Img, staticFile, useCurrentFrame, useVideoConfig } from 'remotion';
import { cueStartFrame } from '../engine/DirectorMotion';

const clamp = (value, low = 0, high = 1) => Math.max(low, Math.min(high, value));
const smooth = (value) => {
  const t = clamp(value);
  return t * t * (3 - 2 * t);
};
const point = (value) => {
  if (Array.isArray(value) && value.length >= 2) {
    const lon = Number(value[0]);
    const lat = Number(value[1]);
    return Number.isFinite(lon) && Number.isFinite(lat) ? { lon, lat } : null;
  }
  if (!value || typeof value !== 'object') return null;
  const lon = Number(value.lon);
  const lat = Number(value.lat);
  return Number.isFinite(lon) && Number.isFinite(lat) ? { lon, lat } : null;
};
const boundsFrom = (value) => {
  const west = Number(value?.west);
  const south = Number(value?.south);
  const east = Number(value?.east);
  const north = Number(value?.north);
  return [west, south, east, north].every(Number.isFinite) && west < east && south < north
    ? { west, south, east, north }
    : null;
};
const mapPoint = (geo, bounds, width, height) => ({
  x: (geo.lon - bounds.west) / (bounds.east - bounds.west) * width,
  y: (bounds.north - geo.lat) / (bounds.north - bounds.south) * height,
});
const KNOWN_SATELLITE_MAPS = new Set([
  'map-assets/nasa-blue-marble-topography-mercator.jpg',
  'map-assets/nasa-blue-marble-july-5400.jpg',
  'map-assets/panama-canal-satellite-eox-2016.jpg'
]);
const imageSrc = (source) => {
  const value = String(source || '').trim();
  if (/^(?:https?:|data:|blob:)/i.test(value)) return value;
  const clean = value.replace(/^\/+/, '');
  const verified = KNOWN_SATELLITE_MAPS.has(clean) ? clean : 'map-assets/panama-canal-satellite-eox-2016.jpg';
  return staticFile(verified);
};
const routePosition = (points, progress) => {
  if (points.length < 2) return points[0] || { x: 0, y: 0, angle: 0 };
  const lengths = points.slice(1).map((p, index) => Math.hypot(p.x - points[index].x, p.y - points[index].y));
  const total = lengths.reduce((sum, length) => sum + length, 0);
  let remaining = clamp(progress) * total;
  for (let index = 0; index < lengths.length; index += 1) {
    const length = lengths[index];
    if (remaining <= length || index === lengths.length - 1) {
      const fraction = length > 0 ? clamp(remaining / length) : 0;
      const from = points[index];
      const to = points[index + 1];
      return {
        x: from.x + (to.x - from.x) * fraction,
        y: from.y + (to.y - from.y) * fraction,
        angle: Math.atan2(to.y - from.y, to.x - from.x) * 180 / Math.PI,
      };
    }
    remaining -= length;
  }
  return { ...points[points.length - 1], angle: 0 };
};

function AnchorLabel({ x, y, name, side, accent, opacity }) {
  const { width, height } = useVideoConfig();
  if (!name) return null;
  const labelWidth = Math.min(width * .38, Math.max(90, String(name).length * 14));
  const margin = Math.max(24, width * .032);
  const fitsLeft = x - 23 - labelWidth >= margin;
  const fitsRight = x + 23 + labelWidth <= width - margin;
  const right = side === 'right' ? (fitsRight || !fitsLeft) : !fitsLeft && fitsRight;
  const left = clamp(x + (right ? 23 : -23 - labelWidth), margin, width - margin - labelWidth);
  return <div style={{
    position: 'absolute', left, top: clamp(y - 19, 32, height - 112), width: labelWidth,
    textAlign: right ? 'left' : 'right',
    opacity, pointerEvents: 'none', overflowWrap: 'break-word',
  }}>
    <div style={{ color: '#F9F6EB', fontFamily: 'Plus Jakarta Sans, sans-serif', fontSize: 22, fontWeight: 850, letterSpacing: '.055em', textShadow: '0 2px 9px #07131C, 0 1px 3px #07131C' }}>{name}</div>
    <div style={{ width: '100%', height: 3, background: accent, marginTop: 5, transform: `scaleX(${opacity})`, transformOrigin: right ? 'left' : 'right' }} />
  </div>;
}

/** A local, geographically registered satellite plate with one settled camera move. */
export function SatelliteMap({ scene = {}, visualDNA = {}, props = {} }) {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const bounds = boundsFrom(props.satellite_bounds);
  const source = String(props.satellite_image || '').trim();
  const routeInput = Array.isArray(props.route_points) && props.route_points.length >= 2
    ? props.route_points
    : [props.p1, props.p2];
  const routeGeo = routeInput.map(point).filter(Boolean);
  if (!bounds || !source || routeGeo.length < 2) return null;

  const route = routeGeo.map((geo) => mapPoint(geo, bounds, width, height));
  const routePath = route.map((p, index) => `${index ? 'L' : 'M'} ${p.x.toFixed(2)} ${p.y.toFixed(2)}`).join(' ');
  const focusGeo = point(props.satellite_focus) || routeGeo[Math.floor(routeGeo.length / 2)];
  const focus = mapPoint(focusGeo, bounds, width, height);
  const cameraOptions = props.satellite_camera || {};
  const startZoom = clamp(Number(cameraOptions.start_zoom) || 1.025, 1, 1.25);
  const endZoom = clamp(Number(cameraOptions.end_zoom) || 1.16, startZoom, 1.35);
  const cameraProgress = smooth(frame / Math.max(1, Number(cameraOptions.move_seconds || .95) * fps));
  const zoom = startZoom + (endZoom - startZoom) * cameraProgress;
  const targetX = width * clamp(Number(cameraOptions.focus_x) || .50, .25, .75);
  const targetY = height * clamp(Number(cameraOptions.focus_y) || .52, .25, .75);
  const moveX = Math.max(width - width * zoom, Math.min(0, targetX - focus.x * zoom));
  const moveY = Math.max(height - height * zoom, Math.min(0, targetY - focus.y * zoom));
  const anchored = (p) => ({ x: moveX + p.x * zoom, y: moveY + p.y * zoom });

  const mapStart = cueStartFrame(scene, 'map', fps, 0);
  const drawStart = Math.max(mapStart, Math.round(fps * .42));
  const drawFrames = Math.max(1, Math.round(Number(props.route_draw_seconds || 1.65) * fps));
  const routeDraw = smooth((frame - drawStart) / drawFrames);
  const cursor = routePosition(route, routeDraw);
  const entrance = smooth(frame / Math.max(1, fps * .16));
  const firstLabel = smooth((frame - Math.round(fps * .30)) / Math.max(1, fps * .24));
  const lastLabel = smooth((frame - drawStart - drawFrames * .70) / Math.max(1, fps * .28));
  const accent = props.route_color || visualDNA.palette?.accent || '#E8B94F';
  const heading = String(props.map_label || '').trim().toUpperCase();
  const fromLabel = String(props.from || props.start || '').trim().toUpperCase();
  const toLabel = String(props.to || props.end || '').trim().toUpperCase();
  const sourceLine = String(props.satellite_attribution || '').trim();
  const start = anchored(route[0]);
  const end = anchored(route[route.length - 1]);
  const image = imageSrc(source);

  return <AbsoluteFill style={{ background: '#07131C', overflow: 'hidden', opacity: entrance }}>
    <div style={{ position: 'absolute', width, height, transformOrigin: '0 0', transform: `translate3d(${moveX}px, ${moveY}px, 0) scale(${zoom})`, willChange: 'transform' }}>
      <Img src={image} style={{ position: 'absolute', width, height, objectFit: 'fill', filter: 'saturate(1.03) contrast(1.08) brightness(1.02)' }} />
      <AbsoluteFill style={{ background: 'rgba(4, 19, 26, .07)' }} />
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ position: 'absolute', inset: 0, overflow: 'visible' }}>
        <path d={routePath} fill="none" stroke="rgba(0, 0, 0, .80)" strokeWidth={15} strokeLinecap="round" strokeLinejoin="round" strokeDasharray="1" strokeDashoffset={1 - routeDraw} pathLength="1" vectorEffect="non-scaling-stroke" />
        <path d={routePath} fill="none" stroke={accent} strokeOpacity={.28} strokeWidth={13} strokeLinecap="round" strokeLinejoin="round" strokeDasharray="1" strokeDashoffset={1 - routeDraw} pathLength="1" vectorEffect="non-scaling-stroke" />
        <path d={routePath} fill="none" stroke={accent} strokeWidth={4.5} strokeLinecap="round" strokeLinejoin="round" strokeDasharray="1" strokeDashoffset={1 - routeDraw} pathLength="1" vectorEffect="non-scaling-stroke" />
        {routeDraw > .01 && routeDraw < .99 && <g transform={`translate(${cursor.x} ${cursor.y}) rotate(${cursor.angle})`}>
          <circle r={12} fill="rgba(7,19,28,.85)" stroke={accent} strokeWidth={3} vectorEffect="non-scaling-stroke" />
          <path d="M-4,-4 L6,0 L-4,4 Z" fill="#F8F3E7" />
        </g>}
        {[route[0], route[route.length - 1]].map((p, index) => {
          const reveal = index ? lastLabel : firstLabel;
          return <g key={`terminus-${index}`} opacity={reveal}>
            <circle cx={p.x} cy={p.y} r={15} fill="rgba(7,19,28,.82)" stroke={accent} strokeWidth={2.5} vectorEffect="non-scaling-stroke" />
            <circle cx={p.x} cy={p.y} r={4.6} fill="#F8F3E7" />
          </g>;
        })}
      </svg>
    </div>

    <AbsoluteFill style={{ background: 'linear-gradient(90deg, rgba(2,13,20,.46), transparent 27%, transparent 74%, rgba(2,13,20,.13)), linear-gradient(180deg, rgba(2,13,20,.29), transparent 20%, transparent 76%, rgba(2,13,20,.40))', pointerEvents: 'none' }} />
    <AnchorLabel {...start} name={fromLabel} side={props.from_label_side || 'left'} accent={accent} opacity={firstLabel} />
    <AnchorLabel {...end} name={toLabel} side="left" accent={accent} opacity={lastLabel} />

    {heading && <div style={{ position: 'absolute', left: '5.2%', top: '7.5%', maxWidth: '38%', pointerEvents: 'none', textShadow: '0 2px 12px rgba(0,0,0,.85)' }}>
      <div style={{ width: 58, height: 4, background: accent, marginBottom: 13, transform: `scaleX(${entrance})`, transformOrigin: 'left' }} />
      <div style={{ color: '#F8F3E7', fontFamily: 'Archivo Black, sans-serif', fontSize: 27, lineHeight: 1.08, letterSpacing: '-.028em' }}>{heading}</div>
      <div style={{ color: '#D3E1E0', fontFamily: 'JetBrains Mono, monospace', fontSize: 13, fontWeight: 600, marginTop: 8, letterSpacing: '.12em' }}>{String(props.satellite_caption || 'SATELLITE VIEW / SCHEMATIC ROUTE')}</div>
    </div>}
    {sourceLine && <div style={{ position: 'absolute', bottom: 24, left: 40, maxWidth: '75%', padding: '6px 9px', background: 'rgba(3,15,20,.77)', color: '#EEF1EA', fontFamily: 'DM Sans, sans-serif', fontSize: 15, fontWeight: 650, lineHeight: 1.25, letterSpacing: '.005em', pointerEvents: 'none' }}>{sourceLine}</div>}
  </AbsoluteFill>;
}
