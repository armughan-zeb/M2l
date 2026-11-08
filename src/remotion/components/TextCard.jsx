import React from 'react';
import { AbsoluteFill, Easing, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { cleanDocumentaryHighlight, selectDocumentaryHeadline } from '../engine/TextSanitizer';
import { safeFadeOutRange, safeRevealRange, hasApprovedDirectorCopy } from '../engine/DirectorMotion';
import { EditorialType, EDITORIAL_TYPE_STYLES } from './EditorialType';

const clamp = (value, low = 0, high = 1) => Math.max(low, Math.min(high, value));
const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
const STYLES = {
  hero_type: 'hero_type', stacked_type: 'stacked_type', outline_type: 'outline_type',
  marker_type: 'marker_type', editorial_caption: 'editorial_caption', impact_slab: 'impact_slab',
  'editorial-plate': 'editorial-plaque', 'editorial-plaque': 'editorial-plaque',
  editorial_banner: 'editorial-plaque', 'editorial-banner': 'editorial-plaque',
  'sharp-plate': 'editorial-plaque', 'color-band': 'color-band', 'accent-band': 'color-band',
  highlight_band: 'color-band', 'highlight-band': 'color-band',
  'map-locator': 'locator', locator: 'locator', 'minimal-locator': 'locator',
  'chapter-lockup': 'chapter-lockup', chapter: 'chapter-lockup',
  'minimal-outline': 'editorial_caption', minimal_rule: 'minimal_rule', 'minimal-rule': 'minimal_rule',
  none: 'none', off: 'none',
};
const TEMPLATE_TEXT_STYLES = {
  'kinetic-statement': 'hero_type', 'headline-center': 'hero_type',
  'keyword-reveal': 'color-band', 'chapter-opener': 'chapter-lockup',
  'lower-third': 'editorial_caption',
};

function documentaryFont(value, fallback) {
  const requested = clean(value);
  return requested && !/playfair|cinzel|lora|georgia|times|serif|courier|typewriter|impact/i.test(requested)
    ? requested : fallback;
}

function safeColor(value, fallback) {
  const color = clean(value);
  return /^(?:#[0-9a-f]{3,8}|(?:rgb|hsl)a?\([\d.%\s,/-]+\)|transparent|white|black)$/i.test(color)
    ? color : fallback;
}

function luminance(value) {
  const hex = clean(value).replace('#', '');
  if (!/^(?:[0-9a-f]{3}|[0-9a-f]{6})$/i.test(hex)) return null;
  const expanded = hex.length === 3 ? [...hex].map((part) => part + part).join('') : hex;
  const channels = [0, 2, 4].map((index) => parseInt(expanded.slice(index, index + 2), 16) / 255)
    .map((part) => part <= .04045 ? part / 12.92 : ((part + .055) / 1.055) ** 2.4);
  return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
}

function highContrastInk(background) {
  const light = luminance(background);
  return light !== null && light > .44 ? '#14202A' : '#F8F7F2';
}

export function readableInk(requested, background) {
  const fallback = highContrastInk(background);
  const proposed = safeColor(requested, '');
  const textLevel = luminance(proposed);
  const backgroundLevel = luminance(background);
  if (textLevel === null || backgroundLevel === null) return proposed || fallback;
  const ratio = (Math.max(textLevel, backgroundLevel) + .05) / (Math.min(textLevel, backgroundLevel) + .05);
  return ratio >= 4.5 ? proposed : fallback;
}

function focalX(scene) {
  const point = scene?.media?.focal_point || scene?.media?.focus_point || scene?.design?.focal_point;
  const candidate = Array.isArray(point) ? point[0] : point && typeof point === 'object' ? (point.x ?? point.cx) : null;
  if (candidate === null || candidate === undefined || candidate === '') return null;
  const number = Number(candidate);
  return Number.isFinite(number) ? (number > 1 ? clamp(number / 100) : clamp(number)) : null;
}

function resolvePlacement(value, scene) {
  let position = clean(value).toLowerCase().replace(/[ _]+/g, '-');
  if (!position) position = focalX(scene) !== null && focalX(scene) < .43 ? 'bottom-right' : 'bottom-left';
  const centered = /^(?:center|hero|middle)$/.test(position);
  const right = position.includes('right');
  const top = position.includes('top');
  const centerSide = position.startsWith('center-');
  return {
    centered, right,
    style: centered
      ? { left: '50%', top: '50%', width: 'fit-content', maxWidth: 'min(82vw, 1100px)', transform: 'translate(-50%, -50%)', textAlign: 'center' }
      : {
          ...(right ? { right: '6%' } : { left: '6%' }),
          ...(centerSide ? { top: '50%', transform: 'translateY(-50%)' } : top ? { top: '7%' } : { bottom: '8%' }),
          width: 'fit-content', maxWidth: 'min(57vw, 950px)', textAlign: right ? 'right' : 'left',
        },
  };
}

function chooseStyle(combined, scene, approvedCopy, headline) {
  const requested = clean(combined.panel_style || combined.panelStyle || combined.text_style || combined.textStyle).toLowerCase();
  if (requested) return STYLES[requested] || 'editorial-plaque';
  if (!approvedCopy) return 'none';
  const selectedTemplate = clean(combined.template_id || scene?.text?.template_id || scene?.template_id || scene?.graphic?.template_id)
    .toLowerCase().replace(/_/g, '-');
  if (TEMPLATE_TEXT_STYLES[selectedTemplate]) return TEMPLATE_TEXT_STYLES[selectedTemplate];
  const primary = clean(scene?.primary_visual?.type || scene?.graphic?.type).toLowerCase();
  if (primary.includes('chapter') || scene?.text?.frame_mode === 'chapter') return 'chapter-lockup';
  if (/map|globe|satellite/.test(primary)) return 'locator';
  if (/chart|diagram|timeline|comparison|document/.test(primary)) return 'minimal-outline';
  if (headline.split(/\s+/).length <= 3) return 'color-band';
  return 'editorial-plaque';
}

/** Approved copy, with a text treatment selected by the director or the scene grammar. */
export function TextCard({ scene = {}, visualDNA = {}, props = {}, theme = {} }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const duration = Math.max(1, Math.round(Number(scene?.duration_in_frames ?? Number(scene?.duration || 4) * fps)));
  const combined = {
    ...(scene.graphic?.props || {}), ...(scene.primary_visual?.props || {}),
    ...(props || {}), ...(scene.text || {}),
  };
  const contract = scene?.editorial_contract || combined?.editorial_contract || {};
  const approvedCopy = hasApprovedDirectorCopy({...scene,editorial_contract:contract});
  const directedPosition = clean(scene.text?.position || contract.position || scene.design?.position);
  // An explicit LLM placement is a production decision. Keep it as the
  // highest-priority anchor instead of letting legacy image defaults move it.
  if (approvedCopy && directedPosition) combined.position = directedPosition;
  // Diagram labels occupy the explanatory canvas. Reserve the band above
  // subtitles for approved emphasis rather than covering its actors.
  if (approvedCopy && !directedPosition && ['Diagram', 'ProcessFlow'].includes(scene.primary_visual?.type)) {
    combined.panel_style = 'editorial_caption';
    combined.position = 'bottom-left';
  }
  // A retrieved image may already contain explanatory labels. Without a
  // verified empty region, keep additional copy in the lower caption band.
  const imageDecision = scene.primary_visual?.asset?.visual_decision || {};
  if (approvedCopy && !directedPosition && scene.primary_visual?.type === 'SmartImage'
      && !(Array.isArray(imageDecision.safe_text_regions) && imageDecision.safe_text_regions.length)) {
    combined.panel_style = 'editorial_caption';
    combined.position = 'bottom-left';
  }
  const directedScene = scene.director_treatment_enabled === true
    || Object.prototype.hasOwnProperty.call(scene.text || {}, 'overlay_policy');
  // Keep the approved-copy boundary even when a future caller mounts this
  // component directly instead of going through DocumentaryComposition.
  if (directedScene && !approvedCopy) return null;
  const rawText = clean(combined.highlight || combined._contract_text || combined.title || combined.headline || combined.text || combined.label || scene.narration);
  if (!approvedCopy && !rawText) return null;
  const headline = approvedCopy
    ? contract.display_text
    : (selectDocumentaryHeadline({ ...combined, highlight: rawText }, scene, 4) || cleanDocumentaryHighlight(rawText, 5)).toUpperCase();
  if (!headline) return null;

  const palette = visualDNA.palette || theme.palette || {};
  const accent = safeColor(combined.accent_color || combined.accentColor || palette.accent, '#E8B75C');
  const requestedStyle = chooseStyle(combined, scene, approvedCopy, headline);
  if (requestedStyle === 'none') return null;
  const wordCount = headline.trim().split(/\s+/).length;
  const treatment = requestedStyle === 'color-band' && (wordCount > 5 || headline.length > 42) ? 'editorial-plaque' : requestedStyle;
  if (treatment === 'none') return null;
  const requestedPanelBackground = combined.panel_background || combined.panelBackground;
  const panelBackground = approvedCopy && !requestedPanelBackground
    ? undefined
    : safeColor(requestedPanelBackground, '#10242C');
  const ink = readableInk(combined.text_color || combined.textColor, treatment === 'color-band' ? accent : panelBackground);
  const emphasisInk = safeColor(combined.emphasis_text_color || combined.emphasisTextColor, highContrastInk(accent));
  const display = documentaryFont(visualDNA.typography?.display || theme.display_font, 'Archivo Black, Plus Jakarta Sans, sans-serif');
  const body = documentaryFont(visualDNA.typography?.body || theme.font_family, 'DM Sans, Plus Jakarta Sans, sans-serif');
  const placement = resolvePlacement(combined.position || combined._position || scene.design?.position, scene);
  const ease = Easing.out(Easing.cubic);
  const enter = interpolate(frame, safeRevealRange(duration, Math.round(fps * .24)), [0, 1], { easing: ease, extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const rule = interpolate(frame, safeRevealRange(duration, Math.round(fps * .36)), [0, 1], { easing: ease, extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const exit = interpolate(frame, safeFadeOutRange(duration, Math.round(fps * .13)), [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const exactSpans = approvedCopy && Array.isArray(contract.emphasis_spans)
    ? contract.emphasis_spans
      .map((span) => ({ start: Number(span?.start), end: Number(span?.end) }))
      .filter((span) => Number.isInteger(span.start) && Number.isInteger(span.end) && span.start >= 0 && span.end > span.start && span.end <= headline.length)
      .sort((a, b) => a.start - b.start)
      .filter((span, index, all) => index === 0 || span.start >= all[index - 1].end)
      .slice(0, 2)
    : [];
  const legacyRequest = clean(combined.emphasis || (Array.isArray(combined.highlight_words) ? combined.highlight_words[0] : '') || combined.highlightWord);
  const legacyHighlighted = legacyRequest && headline.toLowerCase().includes(legacyRequest.toLowerCase())
    ? legacyRequest : selectDocumentaryHeadline({ highlight: headline }, scene, 1);
  const legacyStart = Math.max(0, headline.toLowerCase().indexOf(String(legacyHighlighted || '').toLowerCase()));
  const spans = approvedCopy ? exactSpans : [{ start: legacyStart, end: legacyStart + String(legacyHighlighted || '').length }];
  if (approvedCopy && EDITORIAL_TYPE_STYLES.has(treatment)) {
    return <EditorialType scene={scene} visualDNA={visualDNA} text={headline} spans={spans} style={treatment} position={clean(combined.position || combined._position || scene.design?.position).replace(/_/g, '-') || 'bottom-left'} accent={accent} ink={safeColor(combined.text_color || combined.textColor, '#FFFFFF')} panelBackground={panelBackground} />;
  }
  const requestedEmphasis = clean(combined.emphasis_style || combined.emphasisStyle).toLowerCase();
  const emphasisMode = ['marker', 'underline', 'tint', 'none'].includes(requestedEmphasis)
    ? requestedEmphasis
    : (treatment === 'color-band' ? 'underline' : treatment === 'locator' || spans.some((part) => part.end - part.start > headline.length * .7) ? 'tint' : 'marker');
  const renderEmphasis = (value, key) => {
    if (emphasisMode === 'none') return <React.Fragment key={key}>{value}</React.Fragment>;
    if (emphasisMode === 'marker') return <span key={key} style={{ background: accent, color: emphasisInk, padding: '0 .035em .005em', boxDecorationBreak: 'clone', WebkitBoxDecorationBreak: 'clone' }}>{value}</span>;
    if (emphasisMode === 'underline') return <span key={key} style={{ borderBottom: `max(3px, .065em) solid ${accent}`, color: ink }}>{value}</span>;
    return <span key={key} style={{ color: treatment === 'color-band' ? ink : accent }}>{value}</span>;
  };
  let cursor = 0;
  const textContent = spans.length
    ? <>{spans.map((span, index) => {
        const prefix = headline.slice(cursor, span.start);
        cursor = span.end;
        return <React.Fragment key={`copy-${index}`}>{prefix}{renderEmphasis(headline.slice(span.start, span.end), `emphasis-${index}`)}</React.Fragment>;
      })}{headline.slice(cursor)}</>
    : headline;
  // Director-led scenes never add a second line of unapproved text.
  const kicker = approvedCopy ? '' : clean(combined.kicker || combined.category || combined.topic);
  const subtitle = approvedCopy ? '' : clean(combined.subtitle || combined.detail);
  const fontSize = placement.centered
    ? (headline.length > 68 ? 44 : headline.length > 42 ? 50 : 58)
    : treatment === 'locator' ? (headline.length > 54 ? 30 : headline.length > 32 ? 34 : 40)
      : treatment === 'editorial-plaque'
        ? (headline.length > 58 ? 36 : headline.length > 40 ? 42 : headline.length > 24 ? 46 : 52)
        : (headline.length > 66 ? 36 : headline.length > 42 ? 44 : 52);
  const shape = {
    'editorial-plaque': { background: panelBackground, borderLeft: `4px solid ${accent}`, padding: '10px 16px 12px', boxShadow: '0 8px 24px rgba(0,0,0,.35)', borderRadius: 2 },
    'color-band': { background: accent, padding: '8px 14px 9px', borderRadius: 3, boxShadow: '0 5px 18px rgba(0,0,0,.20)' },
    locator: { background: panelBackground, borderLeft: `3px solid ${accent}`, padding: '8px 14px 10px', boxShadow: '0 6px 20px rgba(0,0,0,.30)', borderRadius: 2 },
    'chapter-lockup': { background: panelBackground, borderBottom: `5px solid ${accent}`, padding: '24px 32px 26px', boxShadow: '0 14px 40px rgba(0,0,0,.30)' },
    'minimal-outline': { background: `linear-gradient(${placement.right ? '270deg' : '90deg'}, ${panelBackground}, rgba(7,18,24,.68))`, borderBottom: `2px solid ${accent}`, padding: '14px 20px 15px', boxShadow: '0 10px 30px rgba(0,0,0,.23)' },
    none: { padding: 0 },
  }[treatment] || {};
  const placementStyle = { ...placement.style, ...(treatment === 'chapter-lockup' && !placement.centered ? { width: 'min(68vw, 1130px)' } : {}) };
  if (['color-band', 'minimal-outline'].includes(treatment) || treatment === 'editorial-plaque') {
    placementStyle.width = 'fit-content';
    placementStyle.maxWidth = 'min(57vw, 950px)';
    if (placement.centered) placementStyle.maxWidth = 'min(80vw, 1100px)';
  }
  if (treatment === 'locator') {
    placementStyle.width = 'fit-content';
    placementStyle.maxWidth = 'min(45vw, 720px)';
  }
  return <AbsoluteFill style={{ pointerEvents: 'none', overflow: 'hidden' }}>
    <div style={{ position: 'absolute', ...placementStyle, boxSizing: 'border-box', maxWidth: placementStyle.maxWidth || '88vw', ...shape, opacity: enter * exit, transform: `${placementStyle.transform || ''} translateY(${(1 - enter) * 18}px)`, transformOrigin: placement.right ? 'right center' : 'left center' }}>
      {treatment === 'chapter-lockup' && <div style={{ position: 'absolute', left: 0, top: 0, width: `${rule * 100}%`, height: 5, background: accent }} />}
      <div style={{ position: 'relative', color: ink, fontFamily: treatment === 'locator' ? body : display, fontSize, fontWeight: treatment === 'locator' ? 800 : 900, lineHeight: treatment === 'chapter-lockup' ? .98 : 1.03, letterSpacing: treatment === 'locator' ? '-.025em' : '-.045em', overflowWrap: 'break-word', textAlign: placement.style.textAlign, whiteSpace: 'pre-wrap' }}>
        {kicker && <div style={{ color: accent, fontFamily: body, fontSize: 15, fontWeight: 800, letterSpacing: '.12em', lineHeight: 1.1, marginBottom: 12, textTransform: 'uppercase' }}>{kicker}</div>}
        {textContent}
      </div>
      {treatment === 'editorial-plaque' && <div style={{ width: `${rule * 35}%`, height: 3, marginTop: 14, marginLeft: placement.right ? 'auto' : 0, background: accent }} />}
      {subtitle && <div style={{ maxWidth: 670, marginTop: 10, color: ink, opacity: .83, fontFamily: body, fontSize: 18, fontWeight: 550, lineHeight: 1.28, textAlign: placement.style.textAlign }}>{subtitle}</div>}
    </div>
  </AbsoluteFill>;
}
