import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { rgba } from '../theme';
import { documentaryFontSize, documentaryTypography } from '../engine/DocumentaryTypography';
import { safeFadeOutRange } from '../engine/DirectorMotion';
import { selectDocumentaryHeadline } from '../engine/TextSanitizer';

const clamp = (value, low = 0, high = 1) => Math.max(low, Math.min(high, Number(value) || 0));
const clean = (value, fallback = '') => String(value ?? fallback).replace(/\s+/g, ' ').trim();
const upper = (value, fallback = '') => clean(value, fallback).toUpperCase();
const signatures = ['trace', 'slide', 'pin', 'scan'];

// Exact latitude/longitude anchors. Unknown places do not become fake maps.
export const LOCATION_POINTS = Object.freeze({
  amsterdam: [52.3676, 4.9041], antwerp: [51.2194, 4.4025], belgium: [50.8503, 4.3517], bel: [50.8503, 4.3517],
  beijing: [39.9042, 116.4074], berlin: [52.52, 13.405],
  brussels: [50.8503, 4.3517], cairo: [30.0444, 31.2357], delhi: [28.6139, 77.209],
  dubai: [25.2048, 55.2708], espoo: [60.2055, 24.6559], finland: [64, 26],
  florida: [27.6648, -81.5158], leonida: [25.7617, -80.1918],
  georgetown: [6.8013, -58.1551], guyana: [4.8604, -58.9302], helsinki: [60.1699, 24.9384],
  italy: [41.9028, 12.4964], ita: [41.9028, 12.4964], milan: [45.4642, 9.19], palermo: [38.1157, 13.3615],
  germany: [52.52, 13.405], deu: [52.52, 13.405], france: [48.8566, 2.3522], fra: [48.8566, 2.3522],
  switzerland: [46.948, 7.4474], che: [46.948, 7.4474], geneva: [46.2044, 6.1432], zurich: [47.3769, 8.5417],
  netherlands: [52.3676, 4.9041], nld: [52.3676, 4.9041], rotterdam: [51.9244, 4.4777],
  spain: [40.4168, -3.7038], esp: [40.4168, -3.7038], austria: [48.2082, 16.3738], aut: [48.2082, 16.3738],
  usa: [38.9072, -77.0369], 'united states': [38.9072, -77.0369], gbr: [51.5072, -0.1276],
  london: [51.5072, -0.1276], 'los angeles': [34.0522, -118.2437], madrid: [40.4168, -3.7038],
  miami: [25.7617, -80.1918], 'vice city': [25.7617, -80.1918], 'new york': [40.7128, -74.006], paris: [48.8566, 2.3522],
  rome: [41.9028, 12.4964], 'san francisco': [37.7749, -122.4194], tokyo: [35.6762, 139.6503], jpn: [35.6762, 139.6503],
  washington: [38.9072, -77.0369], 'washington dc': [38.9072, -77.0369],
  'windsor castle': [51.4839, -0.6044], windsor: [51.4839, -0.6044],
  'buckingham palace': [51.5014, -0.1419], westminster: [51.4995, -0.1248],
  'united kingdom': [55.3781, -3.436], england: [52.3555, -1.1743], commonwealth: [51.5072, -0.1276],
});

function colorsOf(visualDNA = {}) {
  const palette = visualDNA.palette || {};
  return {
    bg: palette.background || '#0b0f14',
    ink: palette.primary || '#f8fafc',
    muted: palette.secondary || '#b6c2d0',
    accent: palette.accent || '#f2c94c',
    accent2: palette.accent2 || palette.secondary || '#e07a5f',
    line: palette.border || 'rgba(255,255,255,.32)',
  };
}

function contrastColors(scene = {}, visualDNA = {}) {
  const colors = colorsOf(visualDNA);
  const tone = String(scene.media_composition?.text_tone || scene.media?.visual_decision?.text_tone || 'light').toLowerCase();
  return tone === 'dark' ? { ...colors, ink: '#f8fafc', muted: 'rgba(248,250,252,.82)' } : colors;
}

function contractOf(scene = {}, props = {}) {
  if (props.editorial_contract && typeof props.editorial_contract === 'object') return props.editorial_contract;
  return scene.editorial_contract && typeof scene.editorial_contract === 'object' ? scene.editorial_contract : {};
}

function motionOf(contract = {}, scene = {}) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const timing = contract.timing || {};
  const sceneDuration = Math.max(1, scene?.duration_in_frames !== undefined && scene?.duration_in_frames !== null
    ? Math.round(Number(scene.duration_in_frames))
    : Math.round(Number(scene?.duration || 4) * fps));
  const requested = Math.round(Number(timing.total_seconds || 0) * fps);
  const duration = Math.max(1, requested ? Math.min(sceneDuration, requested) : sceneDuration);
  const enter = spring({ frame, fps, config: { damping: 15, mass: .36, stiffness: 175 } });
  const exitFrames = Math.max(6, Math.round(Number(timing.exit_seconds || .24) * fps));
  const exit = interpolate(frame, safeFadeOutRange(duration, exitFrames), [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  return { frame, fps, enter: clamp(enter), opacity: clamp(enter) * clamp(exit), exit: clamp(exit), progress: clamp(frame / Math.max(1, duration - 1)) };
}

function safeRegion(scene = {}, contract = {}, props = {}) {
  if (contract.anchor === 'center') return [.08, .22, .92, .78];

  const customBox = scene.placement?.normalized_box || scene.graphic?.cue?.placement?.normalized_box || scene.media_composition?.safe_region;
  if (Array.isArray(customBox) && customBox.length === 4) return customBox.map(Number);

  const anchor = (
    contract.position ||
    props._position ||
    props.position ||
    scene.placement?.position ||
    scene.graphic?.cue?.placement?.position ||
    scene.media_composition?.placement ||
    'top_left'
  ).toLowerCase();

  if (anchor === 'center') return [.08, .22, .92, .78];
  if (anchor === 'top_right') return [.50, .05, .95, .32];
  if (anchor === 'top_left' || anchor === 'top') return [.05, .05, .50, .32];
  if (anchor === 'bottom_right') return [.50, .54, .95, .74];
  if (anchor === 'bottom_left' || anchor === 'bottom') return [.05, .54, .50, .74];
  return [.05, .05, .50, .32];
}

function regionStyle(region) {
  const [x1, y1, x2, y2] = region.map((value) => clamp(value));
  return { left: (x1 * 100) + '%', top: (y1 * 100) + '%', width: Math.max(12, (x2 - x1) * 100) + '%', maxWidth: Math.max(12, (x2 - x1) * 100) + '%' };
}

function sourceLabel(props = {}, contract = {}) {
  return upper(contract.source || props.source || props.attribution || props.citation || props.publication || props.kicker || '');
}

function displayText(props, scene, contract, maxWords = 5) {
  return clean(contract.display_text || props.display_text || selectDocumentaryHeadline(props, scene, maxWords));
}

function emphasis(props, contract, text) {
  const spans = Array.isArray(contract.emphasis_spans) ? contract.emphasis_spans : [];
  const exact = spans.find((span) => Number.isInteger(Number(span?.start)) && Number.isInteger(Number(span?.end))
    && Number(span.start) >= 0 && Number(span.end) > Number(span.start) && Number(span.end) <= text.length);
  if (exact) return text.slice(Number(exact.start), Number(exact.end));
  const phrase = clean(contract.emphasis_phrase || props.emphasis_phrase || props.emphasis || '');
  return phrase && text.toLowerCase().includes(phrase.toLowerCase()) ? phrase : '';
}

function highlightText(text, phrase, colors) {
  const value = clean(text);
  const needle = clean(phrase);
  const start = needle ? value.toLowerCase().indexOf(needle.toLowerCase()) : -1;
  if (start < 0) return value;
  const end = start + needle.length;
  return <>{value.slice(0, start)}<span style={{ color: colors.accent, fontWeight: 900 }}>{value.slice(start, end)}</span>{value.slice(end)}</>;
}

function OpenCluster({ children, region, colors, fonts, motion, label = '', compact = false, marker = 'rule', motionMode = 'trace', sceneIndex = 0, centered = false }) {
  const direction = Number(sceneIndex || 0) % 2 === 0 ? -1 : 1;
  const slide = motionMode === 'slide' ? (1 - motion.enter) * direction * 34 : 0;
  const scale = motionMode === 'pin' ? .965 + motion.enter * .035 : 1;
  const mark = marker === 'bracket'
    ? { width: 3, height: 32, background: colors.accent, flex: '0 0 auto' }
    : marker === 'dot'
      ? { width: 8, height: 8, borderRadius: '50%', background: colors.accent, flex: '0 0 auto', boxShadow: '0 0 12px ' + rgba(colors.accent, .55) }
      : { width: compact ? 34 : 56, height: 3, background: colors.accent, flex: '0 0 auto' };
  const contentStyle = centered ? { width: '100%' } : { width: 'fit-content' };
  return <div style={{ position: 'absolute', ...regionStyle(region), opacity: motion.opacity, transform: 'translate(' + slide + 'px,' + ((1 - motion.enter) * 14) + 'px) scale(' + scale + ')', transformOrigin: centered ? 'center' : 'left top', pointerEvents: 'none' }}>
    <div style={{ display: centered ? 'flex' : 'inline-flex', flexDirection: 'column', alignItems: centered ? 'center' : 'flex-start', ...contentStyle, maxWidth: '100%', color: colors.ink, filter: 'drop-shadow(0 3px 12px ' + rgba(colors.bg, .66) + ')', clipPath: motionMode === 'scan' ? 'inset(0 ' + ((1 - motion.enter) * 100) + '% 0 0)' : 'none' }}>
      {label && <div style={{ display: 'inline-flex', alignItems: 'center', gap: compact ? 8 : 10, maxWidth: '100%' }}>
        <span style={mark} /><span style={{ color: colors.accent, fontFamily: fonts.numbers, fontSize: compact ? 24 : 26, fontWeight: 700, letterSpacing: '.14em', lineHeight: 1.05, textTransform: 'uppercase', whiteSpace: 'nowrap', maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis' }}>{label}</span>
      </div>}
      {children}
    </div>
  </div>;
}

function Rule({ colors, motion, width = 150, centered = false }) {
  return <div style={{ marginTop: centered ? 18 : 14, width: (width * clamp(motion.enter * 1.1)) + 'px', maxWidth: '100%', height: 3, background: 'linear-gradient(90deg, ' + colors.accent + ',' + rgba(colors.accent, .12) + ')', transformOrigin: centered ? 'center' : 'left' }} />;
}

function CornerIdentification({ scene, props, contract, colors, fonts, motion }) {
  const text = displayText(props, scene, contract, 5);
  if (!text) return null;
  const isLower = contract.variant === 'lower_slug' || contract.anchor === 'bottom_left' || contract.anchor === 'bottom_right' || props._position === 'bottom_left' || props._position === 'bottom_right';
  const region = isLower
    ? ((contract.anchor === 'bottom_right' || props._position === 'bottom_right') ? [.50, .54, .95, .74] : [.05, .54, .50, .74])
    : safeRegion(scene, contract, props);
  const rawLabel = sourceLabel(props, contract);
  const label = upper(rawLabel) === upper(text) ? '' : rawLabel;
  return <OpenCluster region={region} colors={colors} fonts={fonts} motion={motion} label={label} compact marker="rule" motionMode={contract.motion === 'side_settle' ? 'slide' : 'trace'} sceneIndex={scene.scene_index}>
    <div style={{ marginTop: label ? 11 : 0, color: colors.ink, fontFamily: fonts.editorial, fontSize: documentaryFontSize(text, { role: 'corner' }), fontWeight: 700, lineHeight: .96, letterSpacing: '-.035em', textTransform: 'uppercase' }}>{highlightText(text, emphasis(props, contract, text), colors)}</div>
    <Rule colors={colors} motion={motion} width={150} />
  </OpenCluster>;
}

function KeywordEmphasis({ scene, props, contract, colors, fonts, motion }) {
  const text = displayText(props, scene, contract, 4);
  if (!text) return null;
  const isTracking = contract.variant === 'tracking_expand';
  const letterSpacing = isTracking
    ? `${interpolate(motion.progress, [0, 1], [0.14, 0.02], { extrapolateRight: 'clamp' })}em`
    : '-.065em';
  return <OpenCluster region={safeRegion(scene, { ...contract, anchor: 'center' }, props)} colors={colors} fonts={fonts} motion={motion} centered motionMode={contract.motion === 'mask_reveal' ? 'scan' : 'trace'} sceneIndex={scene.scene_index}>
    <div style={{ color: colors.ink, fontFamily: fonts.display, fontSize: documentaryFontSize(text, { role: 'hero' }), fontWeight: 900, lineHeight: .86, letterSpacing, textAlign: 'center', textTransform: 'uppercase', transform: 'scale(' + (.9 + motion.enter * .1) + ')', transformOrigin: 'center' }}>{highlightText(text, emphasis(props, contract, text) || text, colors)}</div>
    <Rule colors={colors} motion={motion} width={220} centered />
  </OpenCluster>;
}

function EditorialStatement({ scene, props, contract, colors, fonts, motion }) {
  const text = displayText(props, scene, contract, 7);
  if (!text) return null;
  const words = text.split(/\s+/);
  const pivot = Math.max(1, Math.ceil(words.length / 2));
  const first = words.slice(0, pivot).join(' ');
  const second = words.slice(pivot).join(' ');
  const left = contract.variant === 'offset_thesis';
  return <OpenCluster region={safeRegion(scene, { ...contract, anchor: 'center' }, props)} colors={colors} fonts={fonts} motion={motion} centered={!left} motionMode={contract.motion === 'mask_reveal' ? 'scan' : 'trace'} sceneIndex={scene.scene_index}>
    <div style={{ width: '100%', color: colors.ink, fontFamily: fonts.editorial, fontSize: documentaryFontSize(text, { role: 'statement' }), fontWeight: 700, lineHeight: .9, letterSpacing: '-.055em', textAlign: left ? 'left' : 'center', textTransform: 'uppercase' }}>
      <div style={{ transform: 'translateY(' + ((1 - motion.enter) * 18) + 'px)' }}>{highlightText(first, emphasis(props, contract, text), colors)}</div>
      {second && <div style={{ color: colors.muted, marginTop: 6, transform: 'translateY(' + ((1 - motion.enter) * 30) + 'px)', opacity: clamp(motion.enter * 1.25) }}>{highlightText(second, emphasis(props, contract, text), colors)}</div>}
    </div>
    <Rule colors={colors} motion={motion} width={200} centered={!left} />
  </OpenCluster>;
}

function QuestionReveal({ scene, props, contract, colors, fonts, motion }) {
  const question = displayText(props, scene, contract, 7);
  if (!question) return null;
  const answer = clean(props.answer || props.subtitle || props.detail || '');
  const answerOpacity = clamp((motion.progress - .24) / .28) * motion.exit;
  return <OpenCluster region={safeRegion(scene, { ...contract, anchor: 'center' }, props)} colors={colors} fonts={fonts} motion={motion} centered motionMode={contract.motion === 'question_wipe' ? 'scan' : 'trace'} sceneIndex={scene.scene_index}>
    <div style={{ color: colors.muted, fontFamily: fonts.compact, fontSize: 28, fontWeight: 700, letterSpacing: '.12em', textAlign: 'center', textTransform: 'uppercase' }}>QUESTION</div>
    <div style={{ marginTop: 16, color: colors.ink, fontFamily: fonts.display, fontSize: documentaryFontSize(question, { role: 'statement' }), fontWeight: 900, lineHeight: .9, letterSpacing: '-.055em', textAlign: 'center', textTransform: 'uppercase' }}>{question}</div>
    {answer && <div style={{ marginTop: 18, color: colors.accent, fontFamily: fonts.editorial, fontSize: 34, fontWeight: 700, textAlign: 'center', opacity: answerOpacity, transform: 'translateY(' + ((1 - answerOpacity) * 10) + 'px)' }}>{answer}</div>}
  </OpenCluster>;
}

function ContrastPair({ scene, props, contract, colors, fonts, motion }) {
  const text = displayText(props, scene, contract, 6);
  const chunks = text.split(/\b(?:but|versus|vs\.?|rather than)\b/i);
  const left = clean(props.left || props.before || props.leftValue || chunks[0]);
  const right = clean(props.right || props.after || props.rightValue || chunks[1]);
  if (!left || !right) return <KeywordEmphasis scene={scene} props={props} contract={contract} colors={colors} fonts={fonts} motion={motion} />;
  return <OpenCluster region={safeRegion(scene, { ...contract, anchor: 'center' }, props)} colors={colors} fonts={fonts} motion={motion} centered motionMode={contract.motion === 'split_reveal' ? 'scan' : 'trace'} sceneIndex={scene.scene_index}>
    <div style={{ width: '100%', display: 'grid', gridTemplateColumns: '1fr auto 1fr', gap: 22, alignItems: 'center', textAlign: 'center' }}>
      <div style={{ color: colors.ink, fontFamily: fonts.display, fontSize: documentaryFontSize(left, { role: 'statement' }), fontWeight: 900, lineHeight: .9, textTransform: 'uppercase', transform: 'translateX(' + ((1 - motion.enter) * -26) + 'px)' }}>{left}</div>
      <div style={{ color: colors.accent, fontFamily: fonts.numbers, fontSize: 26, fontWeight: 700, opacity: motion.enter }}>VS</div>
      <div style={{ color: colors.accent2, fontFamily: fonts.display, fontSize: documentaryFontSize(right, { role: 'statement' }), fontWeight: 900, lineHeight: .9, textTransform: 'uppercase', transform: 'translateX(' + ((1 - motion.enter) * 26) + 'px)' }}>{right}</div>
    </div><Rule colors={colors} motion={motion} width={250} centered />
  </OpenCluster>;
}

function NumericEmphasis({ scene, props, contract, colors, fonts, motion }) {
  const value = clean(props.display_value || props.value || props.stat || props.number || contract.display_text);
  if (!value) return null;
  const label = clean(props.label || props.subtitle || '');
  return <OpenCluster region={safeRegion(scene, contract, props)} colors={colors} fonts={fonts} motion={motion} label={sourceLabel(props, contract)} compact marker="dot" motionMode={contract.motion === 'number_count' ? 'pin' : 'trace'} sceneIndex={scene.scene_index}>
    <div style={{ marginTop: 8, color: colors.accent, fontFamily: fonts.display, fontSize: 88, fontWeight: 900, lineHeight: .82, letterSpacing: '-.07em', transform: 'translateY(' + ((1 - motion.enter) * 14) + 'px) scale(' + (.9 + motion.enter * .1) + ')', transformOrigin: 'left bottom' }}>{value}</div>
    {label && <div style={{ marginTop: 12, color: colors.ink, fontFamily: fonts.editorial, fontSize: 26, fontWeight: 700, lineHeight: 1.02, textTransform: 'uppercase', maxWidth: 520 }}>{label}</div>}
  </OpenCluster>;
}

function QuotePull({ scene, props, contract, colors, fonts, motion }) {
  const quote = clean(props.quote || contract.display_text || props.display_text);
  if (!quote) return null;
  const label = sourceLabel(props, contract);
  return <OpenCluster region={safeRegion(scene, contract, props)} colors={colors} fonts={fonts} motion={motion} label={label} compact marker="bracket" motionMode={contract.motion === 'scan_reveal' ? 'scan' : 'trace'} sceneIndex={scene.scene_index}>
    <div style={{ marginTop: label ? 12 : 0, color: colors.ink, fontFamily: fonts.editorial, fontSize: documentaryFontSize(quote, { role: 'quote' }), fontWeight: 700, fontStyle: 'italic', lineHeight: 1.03, letterSpacing: '-.025em' }}>“{quote}”</div><Rule colors={colors} motion={motion} width={90} />
  </OpenCluster>;
}

function ChapterSlate({ scene, props, contract, colors, fonts, motion }) {
  const title = displayText(props, scene, contract, 7);
  if (!title) return null;
  const chapter = clean(props.chapter || props.kicker || props.number || '');
  const effectiveChapter = upper(chapter) === upper(title) ? '' : chapter;
  return <OpenCluster region={safeRegion(scene, { ...contract, anchor: 'center' }, props)} colors={colors} fonts={fonts} motion={motion} centered motionMode={contract.motion === 'chapter_wipe' ? 'scan' : 'trace'} sceneIndex={scene.scene_index}>
    {effectiveChapter && <div style={{ color: colors.accent, fontFamily: fonts.numbers, fontSize: 25, fontWeight: 700, letterSpacing: '.18em', textTransform: 'uppercase' }}>{effectiveChapter}</div>}
    <div style={{ marginTop: effectiveChapter ? 16 : 0, color: colors.ink, fontFamily: fonts.display, fontSize: documentaryFontSize(title, { role: 'hero' }), fontWeight: 900, lineHeight: .86, letterSpacing: '-.07em', textAlign: 'center', textTransform: 'uppercase' }}>{title}</div><Rule colors={colors} motion={motion} width={260} centered />
  </OpenCluster>;
}

function coordinatePoint(label, fallback = {}) {
  if (Number.isFinite(Number(fallback.lat)) && Number.isFinite(Number(fallback.lon ?? fallback.lng))) return { label, lat: Number(fallback.lat), lon: Number(fallback.lon ?? fallback.lng) };
  if (Number.isFinite(Number(fallback.x)) && Number.isFinite(Number(fallback.y))) return { label, x: Number(fallback.x), y: Number(fallback.y), fictional: true };
  const point = LOCATION_POINTS[clean(label).toLowerCase()];
  return point ? { label, lat: point[0], lon: point[1] } : null;
}

function project(point) {
  return point?.fictional ? { x: clamp(point.x) * 100, y: clamp(point.y) * 48 } : { x: ((Number(point?.lon) + 180) / 360) * 100, y: ((90 - Number(point?.lat)) / 180) * 48 };
}

function mapEntries(props, contract) {
  const location = contract.location || {};
  if (Array.isArray(location.points)) return location.points.map((point) => coordinatePoint(point.label, point)).filter(Boolean);
  if (location.kind === 'route') {
    const from = coordinatePoint(location.from || props.from || props.start);
    const to = coordinatePoint(location.to || props.to || props.end);
    return from && to && clean(from.label).toLowerCase() !== clean(to.label).toLowerCase() ? [from, to] : [];
  }
  if (location.kind === 'locator') {
    const point = coordinatePoint(location.location || props.location || props.country, location.coordinates || {});
    return point ? [point] : [];
  }
  const from = coordinatePoint(props.from || props.start);
  const to = coordinatePoint(props.to || props.end);
  if (from && to && clean(from.label).toLowerCase() !== clean(to.label).toLowerCase()) return [from, to];
  const point = coordinatePoint(props.location || props.country);
  return point ? [point] : [];
}

function LocatorInset({ scene, props, contract, colors, fonts, motion }) {
  const points = mapEntries(props, contract);
  if (!points.length) return null;
  const projected = points.map(project);
  const route = points.length > 1;
  const title = route ? upper(points[0].label) + ' → ' + upper(points[1].label) : upper(points[0].label);
  return <OpenCluster region={safeRegion(scene, contract, props)} colors={colors} fonts={fonts} motion={motion} label={sourceLabel(props, contract)} compact marker="dot" motionMode={contract.motion === 'route_draw' ? 'trace' : 'pin'} sceneIndex={scene.scene_index}>
    <div style={{ marginTop: 10, color: colors.ink, fontFamily: fonts.editorial, fontSize: 28, fontWeight: 700, textTransform: 'uppercase' }}>{title}</div>
    <svg viewBox="0 0 100 48" width="100%" height="126" style={{ marginTop: 10, overflow: 'visible' }} aria-label="Geographic locator">
      {[20, 40, 60, 80].map((x) => <line key={'v-' + x} x1={x} y1="0" x2={x} y2="48" stroke={rgba(colors.line, .45)} strokeWidth=".35" />)}
      {[12, 24, 36].map((y) => <line key={'h-' + y} x1="0" y1={y} x2="100" y2={y} stroke={rgba(colors.line, .45)} strokeWidth=".35" />)}
      {route && <path d={'M ' + projected[0].x + ' ' + projected[0].y + ' Q ' + ((projected[0].x + projected[1].x) / 2) + ' ' + Math.min(projected[0].y, projected[1].y) * .32 + ' ' + projected[1].x + ' ' + projected[1].y} fill="none" stroke={colors.accent} strokeWidth="1.35" strokeLinecap="round" pathLength="1" strokeDasharray="1" strokeDashoffset={1 - motion.enter} />}
      {projected.map((point, index) => <g key={points[index].label + '-' + index} opacity={clamp(motion.enter * 1.3 - index * .12)}>
        <circle cx={point.x} cy={point.y} r="2.45" fill={colors.bg} stroke={colors.accent} strokeWidth="1.1" /><circle cx={point.x} cy={point.y} r={2.45 + (1 - motion.enter) * 5} fill="none" stroke={rgba(colors.accent, .55)} strokeWidth=".45" />
        <text x={point.x} y={Math.max(4, point.y - 4)} textAnchor="middle" fill={colors.ink} fontFamily={fonts.numbers} fontSize="4.2" fontWeight="700">{upper(points[index].label)}</text>
      </g>)}
    </svg>
  </OpenCluster>;
}

function ChronologyStrip({ scene, props, contract, colors, fonts, motion }) {
  // Read events from props first, then fall through to contract (which carries
  // the enriched payload for V2 storyboard scenes).
  const rawEvents = Array.isArray(props.events) ? props.events : Array.isArray(contract.events) ? contract.events : [];
  const events = rawEvents.filter((item) => item && (item.year || item.date || item.label));
  const date = clean(props.date || props.year || props.eraSpan || contract.date || contract.year || '');
  const rawLabel = contract.display_text || props.title || '';
  const deduplicatedLabel = clean(rawLabel).toUpperCase() === clean(date).toUpperCase() ? '' : rawLabel;
  const milestones = (events.length ? events : date ? [{ year: date, label: deduplicatedLabel }] : []).slice(0, 4);
  if (!milestones.length) return null;
  return <OpenCluster region={safeRegion(scene, contract, props)} colors={colors} fonts={fonts} motion={motion} label={sourceLabel(props, contract)} compact marker="rule" motionMode={contract.motion === 'timeline_draw' ? 'trace' : 'pin'} sceneIndex={scene.scene_index}>
    <div style={{ position: 'relative', height: 88, marginTop: 12, minWidth: 300, width: '100%' }}><div style={{ position: 'absolute', top: 34, left: 0, right: 0, height: 2, background: rgba(colors.line, .58) }} /><div style={{ position: 'absolute', top: 34, left: 0, width: (100 * motion.enter) + '%', height: 2, background: colors.accent }} />
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>{milestones.map((item, index) => {
        const itemDate = upper(item.year || item.date);
        const itemLabel = clean(item.label || '');
        const showLabel = itemLabel && itemLabel.toUpperCase() !== itemDate;
        return <div key={(item.year || item.date) + '-' + index} style={{ width: (100 / milestones.length) + '%', minWidth: 0, opacity: clamp(motion.enter * 1.35 - index * .12) }}><div style={{ width: 10, height: 10, borderRadius: '50%', background: colors.bg, border: '2px solid ' + colors.accent, marginTop: 30 }} /><div style={{ marginTop: 10, color: colors.accent, fontFamily: fonts.numbers, fontSize: 17, fontWeight: 700 }}>{itemDate}</div>{showLabel && <div style={{ marginTop: 3, color: colors.ink, fontFamily: fonts.body, fontSize: 17, fontWeight: 700, lineHeight: 1.02, textTransform: 'uppercase' }}>{itemLabel.slice(0, 24)}</div>}</div>;
      })}</div>
    </div>
  </OpenCluster>;
}

function chartData(propsOrData = {}) {
  const src = Array.isArray(propsOrData) ? propsOrData : (Array.isArray(propsOrData?.data) ? propsOrData.data : Array.isArray(propsOrData?.values) ? propsOrData.values : []);
  return src.map((row, index) => {
    const raw = row && typeof row === 'object' ? row.value : row;
    const value = Number(String(raw ?? '').replace(/[^0-9.-]/g, ''));
    return { label: clean(row?.label || 'POINT ' + (index + 1)), value, display: clean(raw) };
  }).filter((row) => Number.isFinite(row.value)).slice(0, 6);
}

function ChartCenter({ scene, props, contract, colors, fonts, motion }) {
  // Prefer props.data (explicit editorial data); fall back to contract.data
  // (the enriched semantic payload injected for V2 storyboard scenes).
  const data = chartData(props).length >= 2 ? chartData(props) : chartData(contract);
  if (data.length < 2) return null;
  const title = clean(contract.display_text || props.title || '');
  const max = Math.max(1, ...data.map((item) => Math.abs(item.value)));
  const line = contract.variant === 'editorial_line';
  const coords = data.map((item, index) => (8 + index * (84 / Math.max(1, data.length - 1))) + ',' + (82 - (Math.max(0, item.value) / max) * 68)).join(' ');
  return <OpenCluster region={safeRegion(scene, { ...contract, anchor: 'center' }, props)} colors={colors} fonts={fonts} motion={motion} label={sourceLabel(props, contract)} centered marker="rule" motionMode={contract.motion === 'line_trace' ? 'trace' : 'pin'} sceneIndex={scene.scene_index}>
    {title && <div style={{ color: colors.ink, fontFamily: fonts.editorial, fontSize: 38, fontWeight: 700, lineHeight: .96, textAlign: 'center', textTransform: 'uppercase', marginBottom: 20 }}>{title}</div>}
    <svg viewBox="0 0 100 100" width="86%" height="310" style={{ overflow: 'visible' }} aria-label="Grounded data chart">
      {[20, 40, 60, 80].map((y) => <line key={y} x1="6" y1={y} x2="94" y2={y} stroke={rgba(colors.line, .38)} strokeWidth=".45" strokeDasharray="1.6 1.6" />)}
      {line ? <><polyline points={coords} fill="none" stroke={colors.accent} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" pathLength="1" strokeDasharray="1" strokeDashoffset={1 - motion.enter} />{data.map((item, index) => { const pair = coords.split(' ')[index].split(',').map(Number); return <g key={item.label + '-' + index} opacity={clamp(motion.enter * 1.4 - index * .09)}><circle cx={pair[0]} cy={pair[1]} r="2.1" fill={colors.bg} stroke={colors.accent} strokeWidth="1" /><text x={pair[0]} y="96" textAnchor="middle" fill={colors.muted} fontFamily={fonts.numbers} fontSize="3.8">{upper(item.label).slice(0, 10)}</text><text x={pair[0]} y={Math.max(7, pair[1] - 4)} textAnchor="middle" fill={colors.ink} fontFamily={fonts.numbers} fontSize="4.2">{item.display}</text></g>; })}</> : data.map((item, index) => { const width = 74 / data.length; const x = 10 + index * (80 / data.length); const height = Math.max(5, (Math.max(0, item.value) / max) * 68 * motion.enter); return <g key={item.label + '-' + index} opacity={clamp(motion.enter * 1.3 - index * .08)}><rect x={x} y={84 - height} width={width} height={height} fill={index === data.length - 1 ? colors.accent : rgba(colors.ink, .32)} /><text x={x + width / 2} y={Math.max(8, 79 - height)} textAnchor="middle" fill={colors.ink} fontFamily={fonts.numbers} fontSize="4.2">{item.display}</text><text x={x + width / 2} y="96" textAnchor="middle" fill={colors.muted} fontFamily={fonts.numbers} fontSize="3.8">{upper(item.label).slice(0, 10)}</text></g>; })}
    </svg>
  </OpenCluster>;
}

function EvidenceAnnotation({ scene, props, contract, colors, fonts, motion }) {
  const text = displayText(props, scene, contract, 5);
  if (!text) return null;
  const label = sourceLabel(props, contract);
  const phrase = emphasis(props, contract, text) || text;
  return <OpenCluster region={safeRegion(scene, contract, props)} colors={colors} fonts={fonts} motion={motion} label={label} compact marker={contract.variant === 'evidence_bracket' ? 'bracket' : 'rule'} motionMode={contract.motion === 'scan_reveal' ? 'scan' : 'trace'} sceneIndex={scene.scene_index}>
    <div style={{ position: 'relative', marginTop: label ? 12 : 0, color: colors.ink, fontFamily: fonts.editorial, fontSize: documentaryFontSize(text, { role: 'corner' }), fontWeight: 700, lineHeight: .96, letterSpacing: '-.035em', textTransform: 'uppercase' }}>{highlightText(text, phrase, colors)}<div style={{ marginTop: 10, height: 3, width: Math.min(100, Math.max(28, phrase.length * 7)) * motion.enter + '%', background: colors.accent, transformOrigin: 'left' }} /></div>
  </OpenCluster>;
}

function BeforeAfter({ scene, props, contract, colors, fonts, motion }) {
  const before = clean(props.before?.value || props.leftValue || props.before || '');
  const after = clean(props.after?.value || props.rightValue || props.after || '');
  if (!before || !after) return null;
  const beforeLabel = clean(props.before?.label || props.leftLabel || 'BEFORE');
  const afterLabel = clean(props.after?.label || props.rightLabel || 'AFTER');
  return <OpenCluster region={safeRegion(scene, { ...contract, anchor: 'center' }, props)} colors={colors} fonts={fonts} motion={motion} centered motionMode={contract.motion === 'split_reveal' ? 'scan' : 'trace'} sceneIndex={scene.scene_index}>
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', width: '100%', borderTop: '2px solid ' + rgba(colors.line, .7), borderBottom: '2px solid ' + rgba(colors.line, .7) }}><div style={{ padding: '22px 30px 26px 0', borderRight: '1px solid ' + rgba(colors.line, .62), opacity: motion.enter }}><div style={{ color: colors.muted, fontFamily: fonts.numbers, fontSize: 20, fontWeight: 700, letterSpacing: '.13em' }}>{upper(beforeLabel)}</div><div style={{ marginTop: 12, color: colors.ink, fontFamily: fonts.display, fontSize: 70, fontWeight: 900, letterSpacing: '-.06em' }}>{before}</div></div><div style={{ padding: '22px 0 26px 30px', opacity: clamp(motion.enter * 1.25) }}><div style={{ color: colors.accent, fontFamily: fonts.numbers, fontSize: 20, fontWeight: 700, letterSpacing: '.13em' }}>{upper(afterLabel)}</div><div style={{ marginTop: 12, color: colors.accent, fontFamily: fonts.display, fontSize: 70, fontWeight: 900, letterSpacing: '-.06em' }}>{after}</div></div></div>
  </OpenCluster>;
}

function ProcessDiagram({ scene, props, contract, colors, fonts, motion }) {
  const steps = Array.isArray(props.steps) ? props.steps.filter(Boolean).slice(0, 3) : [];
  if (steps.length < 2) return null;
  return <OpenCluster region={safeRegion(scene, { ...contract, anchor: 'center' }, props)} colors={colors} fonts={fonts} motion={motion} centered motionMode={contract.motion === 'path_travel' ? 'trace' : 'pin'} sceneIndex={scene.scene_index}>
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(' + steps.length + ', 1fr)', width: '100%', gap: 14, alignItems: 'start' }}>{steps.map((step, index) => <React.Fragment key={index}><div style={{ paddingTop: 8, opacity: clamp(motion.enter * 1.4 - index * .13), transform: 'translateY(' + ((1 - motion.enter) * 18) + 'px)' }}><div style={{ width: 24, height: 24, borderRadius: '50%', border: '2px solid ' + colors.accent, color: colors.accent, fontFamily: fonts.numbers, fontSize: 13, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{String(index + 1).padStart(2, '0')}</div><div style={{ marginTop: 12, color: colors.ink, fontFamily: fonts.editorial, fontSize: 28, fontWeight: 700, lineHeight: .96, textTransform: 'uppercase' }}>{clean(step.title || step.label || 'STEP ' + (index + 1))}</div>{step.desc && <div style={{ marginTop: 8, color: colors.muted, fontFamily: fonts.body, fontSize: 19, lineHeight: 1.08 }}>{clean(step.desc)}</div>}</div>{index < steps.length - 1 && <div style={{ alignSelf: 'start', marginTop: 20, height: 2, background: 'linear-gradient(90deg, ' + colors.accent + ',' + rgba(colors.accent, .12) + ')', transform: 'scaleX(' + motion.enter + ')', transformOrigin: 'left' }} />}</React.Fragment>)}</div>
  </OpenCluster>;
}

function PortraitProfile({ scene, props, contract, colors, fonts, motion }) {
  const name = clean(props.name || contract.display_text || props.title || '');
  if (!name) return null;
  const rawRole = clean(props.role || props.subtitle || '');
  const role = upper(rawRole) === upper(name) ? '' : rawRole;
  const rawLabel = sourceLabel(props, contract);
  const label = (upper(rawLabel) === upper(name) || upper(rawLabel) === upper(role)) ? '' : rawLabel;
  return <OpenCluster region={safeRegion(scene, contract, props)} colors={colors} fonts={fonts} motion={motion} label={label} compact marker="dot" motionMode={contract.motion === 'lower_third_slide' ? 'slide' : 'pin'} sceneIndex={scene.scene_index}><div style={{ marginTop: 8, color: colors.ink, fontFamily: fonts.display, fontSize: 52, fontWeight: 900, letterSpacing: '-.05em', textTransform: 'uppercase' }}>{name}</div>{role && <div style={{ marginTop: 8, color: colors.accent, fontFamily: fonts.editorial, fontSize: 24, fontWeight: 700, letterSpacing: '.04em', textTransform: 'uppercase' }}>{role}</div>}<Rule colors={colors} motion={motion} width={120} /></OpenCluster>;
}

function AnnotatedMontage({ scene, props, contract, colors, fonts, motion }) {
  const raw = Array.isArray(props.items) ? props.items : Array.isArray(props.fragments) ? props.fragments : [];
  const items = raw.map((item) => clean(item?.label || item?.title || item)).filter(Boolean).slice(0, 3);
  if (items.length < 2) return null;
  return <OpenCluster region={safeRegion(scene, contract, props)} colors={colors} fonts={fonts} motion={motion} label={sourceLabel(props, contract)} compact marker="rule" motionMode={contract.motion === 'contact_sheet_slide' ? 'slide' : 'scan'} sceneIndex={scene.scene_index}>
    <div style={{ marginTop: 13, display: 'flex', alignItems: 'flex-start', gap: 15, maxWidth: '100%' }}>{items.map((item, index) => <div key={item + '-' + index} style={{ minWidth: 0, flex: 1, opacity: clamp(motion.enter * 1.45 - index * .13), transform: 'translateY(' + ((1 - motion.enter) * (index + 1) * 9) + 'px)' }}><div style={{ color: index === items.length - 1 ? colors.accent : colors.muted, fontFamily: fonts.numbers, fontSize: 16, fontWeight: 700 }}>{String(index + 1).padStart(2, '0')}</div><div style={{ marginTop: 6, paddingTop: 8, borderTop: '2px solid ' + (index === items.length - 1 ? colors.accent : rgba(colors.line, .62)), color: colors.ink, fontFamily: fonts.editorial, fontSize: 25, fontWeight: 700, lineHeight: .96, textTransform: 'uppercase', overflowWrap: 'anywhere' }}>{item}</div></div>)}</div>
  </OpenCluster>;
}

const CANONICAL_FAMILY_MAP = Object.freeze({
  corner_identification: 'corner_identification',
  field_caption: 'corner_identification',
  lower_slug: 'corner_identification',
  keyword_emphasis: 'keyword_emphasis',
  word_scale: 'keyword_emphasis',
  editorial_statement: 'editorial_statement',
  thesis_stack: 'editorial_statement',
  question_reveal: 'question_reveal',
  question_slate: 'question_reveal',
  contrast_pair: 'contrast_pair',
  contrast_comparison: 'contrast_pair',
  split_compare: 'contrast_pair',
  before_after: 'before_after',
  numeric_emphasis: 'numeric_emphasis',
  metric_statistic: 'numeric_emphasis',
  attributed_quote: 'attributed_quote',
  quote_pull: 'attributed_quote',
  chapter_transition: 'chapter_transition',
  chapter_slate: 'chapter_transition',
  locator_route: 'locator_route',
  geographic_locator: 'locator_route',
  chronology_timeline: 'chronology_timeline',
  timeline_trace: 'chronology_timeline',
  data_chart: 'data_chart',
  data_visualization: 'data_chart',
  chart_center: 'data_chart',
  evidence_annotation: 'evidence_annotation',
  evidence_bracket: 'evidence_annotation',
  process_diagram: 'process_diagram',
  process_flow: 'process_diagram',
  portrait_profile: 'portrait_profile',
  identity_profile: 'portrait_profile',
  portrait_id: 'portrait_profile',
  annotated_montage: 'annotated_montage',
  montage_cluster: 'annotated_montage',
  archive_contact_sheet: 'annotated_montage',
  locator_inset: 'locator_route',
  media_hold: 'media_hold',
  media_only_hold: 'media_hold',
});

function familyFor(scene, props, contract) {
  const rawFamily = clean(contract.family || '').toLowerCase().replace(/-/g, '_');
  if (rawFamily && CANONICAL_FAMILY_MAP[rawFamily]) {
    return CANONICAL_FAMILY_MAP[rawFamily];
  }
  const grammar = clean(scene.media_composition?.overlay_grammar).toLowerCase().replace(/-/g, '_');
  if (grammar && CANONICAL_FAMILY_MAP[grammar]) {
    return CANONICAL_FAMILY_MAP[grammar];
  }
  const type = clean(scene.primary_visual?.type).toLowerCase().replace(/-/g, '_');
  if (type === 'chart') return 'data_chart';
  if (type === 'quotecard') return 'attributed_quote';
  if (type === 'chaptertitle') return 'chapter_transition';
  if (type === 'collage') return 'annotated_montage';
  if (type === 'personprofile') return 'portrait_profile';
  if (type === 'statcard') return 'numeric_emphasis';
  if (type === 'animatedmap') return 'locator_route';
  if (type === 'timeline') return 'chronology_timeline';
  if (type === 'documenthighlight') return 'evidence_annotation';
  if (type === 'comparison') return 'before_after';
  if (type === 'diagram') return 'process_diagram';
  if (type === 'kinetictext') return 'keyword_emphasis';
  return 'corner_identification';
}

export function MediaOverlayCanvas({ scene = {}, visualDNA = {}, props = {} }) {
  const contract = contractOf(scene, props);
  if (contract.enabled === false || contract.family === 'media_hold') return null;
  if (!contract.family && scene.media_composition?.overlay_grammar === 'none') return null;
  const colors = contrastColors(scene, visualDNA);
  const fonts = documentaryTypography(visualDNA);
  const motion = motionOf(contract, scene);
  const family = familyFor(scene, props, contract);
  const shared = { scene, props, contract, colors, fonts, motion };
  let graphic = null;
  if (family === 'keyword_emphasis') graphic = <KeywordEmphasis {...shared} />;
  else if (family === 'editorial_statement') graphic = <EditorialStatement {...shared} />;
  else if (family === 'question_reveal') graphic = <QuestionReveal {...shared} />;
  else if (family === 'contrast_pair') graphic = <ContrastPair {...shared} />;
  else if (family === 'numeric_emphasis') graphic = <NumericEmphasis {...shared} />;
  else if (family === 'attributed_quote') graphic = <QuotePull {...shared} />;
  else if (family === 'chapter_transition') graphic = <ChapterSlate {...shared} />;
  else if (family === 'locator_route') graphic = <LocatorInset {...shared} />;
  else if (family === 'chronology_timeline') graphic = <ChronologyStrip {...shared} />;
  else if (family === 'data_chart') graphic = <ChartCenter {...shared} />;
  else if (family === 'evidence_annotation') graphic = <EvidenceAnnotation {...shared} />;
  else if (family === 'before_after') graphic = <BeforeAfter {...shared} />;
  else if (family === 'process_diagram') graphic = <ProcessDiagram {...shared} />;
  else if (family === 'portrait_profile') graphic = <PortraitProfile {...shared} />;
  else if (family === 'annotated_montage') graphic = <AnnotatedMontage {...shared} />;
  else graphic = <CornerIdentification {...shared} />;
  return <AbsoluteFill style={{ pointerEvents: 'none', overflow: 'hidden' }}>{graphic}</AbsoluteFill>;
}
