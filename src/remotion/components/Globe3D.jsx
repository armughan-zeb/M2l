import React from 'react';
import { AbsoluteFill, Img, staticFile, useCurrentFrame, useVideoConfig } from 'remotion';

const clamp = (value, low = 0, high = 1) => Math.max(low, Math.min(high, value));
const smooth = (value) => {
  const t = clamp(value);
  return t * t * (3 - 2 * t);
};
const radians = (degrees) => degrees * Math.PI / 180;
const coordinates = (value) => {
  const lon = Number(Array.isArray(value) ? value[0] : value?.lon);
  const lat = Number(Array.isArray(value) ? value[1] : value?.lat);
  return Number.isFinite(lon) && Number.isFinite(lat) ? { lon, lat } : null;
};
const KNOWN_GLOBE_MAPS = new Set([
  'map-assets/nasa-blue-marble-topography-mercator.jpg',
  'map-assets/nasa-blue-marble-july-5400.jpg',
  'map-assets/panama-canal-satellite-eox-2016.jpg'
]);
const srcFor = (source) => {
  const value = String(source || '').trim();
  if (/^(?:https?:|data:|blob:)/i.test(value)) return value;
  const clean = value.replace(/^\/+/, '');
  const verified = KNOWN_GLOBE_MAPS.has(clean) ? clean : 'map-assets/nasa-blue-marble-july-5400.jpg';
  return staticFile(verified);
};
const unitVector = ({ lon, lat }) => {
  const phi = radians(lat);
  const lambda = radians(lon);
  return { x: Math.cos(phi) * Math.cos(lambda), y: Math.cos(phi) * Math.sin(lambda), z: Math.sin(phi) };
};
const project = (geo, center, radius) => {
  const latitude = radians(geo.lat);
  const centerLatitude = radians(center.lat);
  const delta = radians(geo.lon - center.lon);
  const depth = Math.sin(centerLatitude) * Math.sin(latitude) + Math.cos(centerLatitude) * Math.cos(latitude) * Math.cos(delta);
  return {
    x: radius + radius * Math.cos(latitude) * Math.sin(delta),
    y: radius - radius * (Math.cos(centerLatitude) * Math.sin(latitude) - Math.sin(centerLatitude) * Math.cos(latitude) * Math.cos(delta)),
    visible: depth > .02,
  };
};
const greatCircle = (from, to, center, radius) => {
  const a = unitVector(from);
  const b = unitVector(to);
  const omega = Math.acos(clamp(a.x * b.x + a.y * b.y + a.z * b.z, -1, 1));
  if (omega < .001) return [];
  const denominator = Math.sin(omega);
  if (Math.abs(denominator) < .00001) return [];
  return Array.from({ length: 65 }, (_, index) => {
    const t = index / 64;
    const wa = Math.sin((1 - t) * omega) / denominator;
    const wb = Math.sin(t * omega) / denominator;
    const vector = { x: wa * a.x + wb * b.x, y: wa * a.y + wb * b.y, z: wa * a.z + wb * b.z };
    return project({ lon: Math.atan2(vector.y, vector.x) * 180 / Math.PI, lat: Math.asin(clamp(vector.z, -1, 1)) * 180 / Math.PI }, center, radius);
  });
};

/** Frame-clocked, texture-mapped Earth with coordinate-registered routes. */
export function Globe3D({ scene = {}, visualDNA = {}, props = {} }) {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const frames = Array.isArray(props.globe_frames) ? props.globe_frames : [];
  const finalCenter = coordinates(props.globe_center);
  if (!frames.length || !finalCenter) return null;
  // A global overview needs a legible, deliberate globe turn even when the
  // script does not establish a route. Keep the camera motion broad and eased;
  // never imply a location with a fallback pin.
  const startCenter = coordinates(props.globe_start_center) || { lon: finalCenter.lon - 34, lat: finalCenter.lat + 8 };
  const sceneSeconds = Math.max(.1, Number(scene.duration_in_frames || 0) / fps || Number(scene.duration || 4));
  const turnSeconds = Math.max(.45, Math.min(4, Number(props.globe_turn_seconds) || Math.min(3.5, Math.max(1.6, sceneSeconds * .65))));
  const phase = smooth(clamp(frame / Math.max(1, fps * turnSeconds)));
  const sourcePosition = phase * Math.max(0, frames.length - 1);
  const index = Math.min(frames.length - 1, Math.floor(sourcePosition));
  const nextIndex = Math.min(frames.length - 1, index + 1);
  const frameBlend = smooth(sourcePosition - index);
  const center = {
    lon: startCenter.lon + (finalCenter.lon - startCenter.lon) * phase,
    lat: startCenter.lat + (finalCenter.lat - startCenter.lat) * phase,
  };
  const diameter = Math.min(height * .87, width * .52);
  const radius = diameter / 2;
  const left = width * .5 - radius;
  const top = height * .505 - radius;
  const accent = props.route_color || visualDNA.palette?.accent || '#E8B94F';
  const routeInput = Array.isArray(props.route_points) && props.route_points.length >= 2
    ? props.route_points
    : [props.p1, props.p2];
  const route = routeInput.map(coordinates).filter(Boolean);
  const mapShot = props.map_shot || scene.map_shot || {};
  const hasGroundedFocus = route.length > 0
    || (Array.isArray(mapShot.location_names) && mapShot.location_names.length > 0)
    || (Array.isArray(mapShot.feature_ids) && mapShot.feature_ids.length > 0)
    || Boolean(props.location || props.place || props.geographic_focus);
  const from = route[0] || finalCenter;
  const to = route[route.length - 1] || finalCenter;
  const spread = Math.hypot(from.lon - to.lon, from.lat - to.lat);
  const compact = hasGroundedFocus && spread < 9;
  const highlight = compact ? project(finalCenter, center, radius) : null;
  const arc = !compact && route.length >= 2 ? greatCircle(from, to, center, radius) : [];
  const path = arc.map((p, i) => `${i && arc[i - 1].visible && p.visible ? 'L' : 'M'}${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(' ');
  const reveal = smooth((frame - fps * .34) / Math.max(1, fps * 1.15));
  const pinReveal = smooth((frame - fps * .55) / Math.max(1, fps * .36));
  const ring = smooth((frame - fps * .55) / Math.max(1, fps * .58));
  const sphereScale = .975 + .05 * smooth(frame / Math.max(1, fps * Math.min(2.2, turnSeconds * .65)));
  const heading = String(props.map_label || props.title || '').trim().toUpperCase();
  const pinLabel = String(props.globe_pin_label || '').trim().toUpperCase();
  const credit = String(props.globe_attribution || 'EARTH IMAGERY: NASA EARTH OBSERVATORY').trim();
  const fromName = String(props.from || '').trim().toUpperCase();
  const toName = String(props.to || '').trim().toUpperCase();

  return <AbsoluteFill style={{ background: 'radial-gradient(ellipse at 50% 52%, #102A38 0%, #071721 45%, #041018 100%)', overflow: 'hidden' }}>
    <AbsoluteFill style={{ opacity: .38, backgroundImage: 'radial-gradient(rgba(169,203,218,.34) .8px, transparent 1px)', backgroundSize: '89px 89px', backgroundPosition: '16px 21px' }} />
    <div style={{ position: 'absolute', width: diameter * 1.42, height: diameter * 1.42, left: width * .5 - diameter * .71, top: height * .505 - diameter * .71, borderRadius: '50%', border: '1px solid rgba(121,171,194,.16)', boxShadow: '0 0 0 42px rgba(121,171,194,.025), 0 0 0 90px rgba(121,171,194,.017)' }} />
    <div style={{ position: 'absolute', width: diameter, height: diameter, left, top, transform: `scale(${sphereScale})`, transformOrigin: '50% 50%', filter: 'drop-shadow(0 24px 68px rgba(0,0,0,.74))' }}>
      <Img src={srcFor(frames[index])} style={{ width: diameter, height: diameter, objectFit: 'contain' }} />
      {nextIndex !== index && frameBlend > 0 && <Img src={srcFor(frames[nextIndex])} style={{ position: 'absolute', inset: 0, width: diameter, height: diameter, objectFit: 'contain', opacity: frameBlend }} />}
      <div style={{ position: 'absolute', inset: 0, borderRadius: '50%', boxShadow: 'inset -24px -16px 44px rgba(1,11,20,.32), inset 6px 7px 18px rgba(112,207,250,.13), 0 0 19px rgba(93,168,211,.28)', pointerEvents: 'none' }} />
      <svg width={diameter} height={diameter} viewBox={`0 0 ${diameter} ${diameter}`} style={{ position: 'absolute', inset: 0, overflow: 'visible' }}>
        {!compact && path && <>
          <path d={path} fill="none" stroke="rgba(4,17,25,.85)" strokeWidth={12} pathLength="1" strokeDasharray="1" strokeDashoffset={1 - reveal} strokeLinecap="round" />
          <path d={path} fill="none" stroke={accent} strokeWidth={3.8} pathLength="1" strokeDasharray="1" strokeDashoffset={1 - reveal} strokeLinecap="round" />
        </>}
        {compact && highlight?.visible && <g opacity={pinReveal}>
          <circle cx={highlight.x} cy={highlight.y} r={16 + ring * 32} fill="none" stroke={accent} strokeWidth={2.2} opacity={1 - ring} />
          <circle cx={highlight.x} cy={highlight.y} r={15} fill="rgba(5,19,27,.76)" stroke={accent} strokeWidth={2.6} />
          <circle cx={highlight.x} cy={highlight.y} r={5} fill="#F9F4E8" />
        </g>}
        {hasGroundedFocus && !compact && [from, to].map((geo, item) => {
          const pin = project(geo, center, radius);
          return pin.visible && <g key={`globe-pin-${item}`} opacity={item ? reveal : pinReveal}>
            <circle cx={pin.x} cy={pin.y} r={10} fill="rgba(5,19,27,.82)" stroke={accent} strokeWidth={2.5} />
            <circle cx={pin.x} cy={pin.y} r={3.5} fill="#F9F4E8" />
          </g>;
        })}
      </svg>
    </div>

    {(heading || (!compact && fromName && toName)) && <div style={{ position: 'absolute', zIndex: 10, top: '8%', left: '4%', maxWidth: '20%', padding: '10px 16px 12px 14px', borderLeft: `3px solid ${accent}`, background: 'linear-gradient(90deg, rgba(4,15,22,.78), rgba(4,15,22,.22) 78%, transparent)', color: '#F7F4EB', textShadow: '0 3px 14px rgba(0,0,0,.88)', overflow: 'visible' }}>
      {heading && <div style={{ fontFamily: 'Archivo Black, sans-serif', fontSize: 34, lineHeight: 1.06, letterSpacing: '-.035em' }}>{heading}</div>}
      {!compact && fromName && toName && <div style={{ color: '#CAD9DD', fontFamily: 'Plus Jakarta Sans, sans-serif', fontSize: 16, fontWeight: 700, lineHeight: 1.3, marginTop: 14 }}>{fromName} <span style={{ color: accent }}>→</span> {toName}</div>}
    </div>}
    {compact && pinLabel && highlight?.visible && <div style={{ position: 'absolute', left: left + highlight.x * sphereScale + 21, top: top + highlight.y * sphereScale - 19, color: '#F7F4EB', fontFamily: 'Plus Jakarta Sans, sans-serif', fontSize: 20, fontWeight: 850, letterSpacing: '.045em', textShadow: '0 2px 11px #06131B, 0 2px 4px #06131B', opacity: pinReveal }}>{pinLabel}</div>}
    <div style={{ position: 'absolute', bottom: 27, left: 42, color: '#AFC6CF', background: 'rgba(3,15,21,.64)', padding: '5px 8px', fontFamily: 'DM Sans, sans-serif', fontSize: 14, fontWeight: 600, letterSpacing: '.025em' }}>{credit}</div>
  </AbsoluteFill>;
}
