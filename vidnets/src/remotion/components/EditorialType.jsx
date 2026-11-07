import React from 'react';
import { AbsoluteFill, Easing, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { documentaryTypography, documentaryContrastInk, documentaryOverlayInk } from '../engine/DocumentaryTypography';
import { safeFadeOutRange } from '../engine/DirectorMotion';

export const EDITORIAL_TYPE_STYLES = new Set(['hero_type', 'stacked_type', 'outline_type', 'marker_type', 'editorial_caption', 'minimal_rule', 'impact_slab']);

/** Exact approved copy, staged per word; no transcript extraction or invented labels. */
export function EditorialType({ scene = {}, visualDNA = {}, text, spans = [], style = 'hero_type', position = 'center', accent = '#E8B75C', ink = '#FFFFFF', panelBackground }) {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const fonts = documentaryTypography(visualDNA);
  const panelStyle = ['hero_type', 'editorial_caption'].includes(style);
  ink = documentaryOverlayInk(ink, panelStyle ? panelBackground || visualDNA.palette?.surface : null);
  const duration = Math.max(1, Number(scene.duration_in_frames || Number(scene.duration || 2) * fps));
  const words = [...String(text || '').matchAll(/\S+/g)];
  if (!words.length) return null;
  const center = /^(center|hero|middle)$/.test(position);
  const right = String(position).includes('right');
  const top = String(position).includes('top');
  const middle = String(position).includes('middle') || String(position).startsWith('center-');
  const stacked = style === 'stacked_type';
  const compact = style === 'editorial_caption';
  const scale = width / 1920;
  const baseSize = (compact ? 34 : stacked ? 48 : words.length >= 5 ? 42 : words.length >= 3 ? 48 : 56) * scale;
  const longest = Math.max(...words.map((w)=>w[0].length));
  const region = scene.media_composition?.safe_region;
  const safeRegion = Array.isArray(region) && region.length === 4 && region.every((v) => Number.isFinite(v) && v >= 0 && v <= 1)
    && region[2] - region[0] >= .24 && region[3] - region[1] >= .08 ? region : null;
  const safeWidth = safeRegion ? width * (safeRegion[2] - safeRegion[0] - .025) : width * .82;
  const safeHeight = safeRegion ? height * (safeRegion[3] - safeRegion[1] - .025) : height * .78;
  // Honor an actual media-safe rectangle without cutting whole words or
  // allowing the underline/panel padding to consume the available height.
  const size = Math.min(baseSize, width * .70 / Math.max(1, longest * .68),
    safeRegion ? safeWidth / Math.max(1, String(text).length * .64) : baseSize,
    safeRegion ? Math.max(1, safeHeight - (compact ? 16 : panelStyle ? 20 : 0) * scale) / (stacked ? words.length * 1.16 : 1.3) : baseSize);
  const hex = /^#[0-9a-f]{6}$/i.test(accent) ? accent.slice(1) : 'E8B75C';
  const level = .2126*parseInt(hex.slice(0,2),16)+.7152*parseInt(hex.slice(2,4),16)+.0722*parseInt(hex.slice(4,6),16);
  const markerInk = documentaryContrastInk(accent);
  const exit = interpolate(frame, safeFadeOutRange(duration, Math.round(.18 * fps)), [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const ease = Easing.out(Easing.cubic);
  const containerEnter = interpolate(frame, [0, Math.max(1, Math.round(.16 * fps))], [0, 1], { easing: ease, extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const location = safeRegion ? { left: `${(safeRegion[0] + .0125) * 100}%`, top: `${(safeRegion[1] + .0125) * 100}%`, maxWidth: safeWidth }
    : center ? { left: '50%', top: '46%', transform: 'translate(-50%, -50%)', width: 'fit-content' }
    : { ...(right ? { right: '6%' } : { left: '6%' }), ...(middle ? { top: '50%', transform: 'translateY(-50%)' } : top ? { top: '10%' } : { bottom: '17%' }), width: 'fit-content', maxWidth: '82%' };
  const segments = (word, start) => {
    const points = [start, start + word.length, ...spans.flatMap((s) => [s.start, s.end]).filter((n) => n > start && n < start + word.length)].sort((a,b) => a-b);
    return points.slice(0, -1).map((a, i) => {
      const b = points[i+1];
      const highlighted = spans.some((s) => a >= s.start && b <= s.end);
      const marker = highlighted && (style === 'marker_type');
      return <span key={`${a}-${b}`} style={{
        color: marker ? markerInk : highlighted ? documentaryOverlayInk(accent) : ink,
        background: marker ? accent : undefined,
        padding: marker ? '0.04em 0.16em 0.08em' : 0,
        borderRadius: marker ? '4px' : undefined,
        boxDecorationBreak: 'clone',
        WebkitBoxDecorationBreak: 'clone',
        WebkitTextStroke: style === 'outline_type' && !highlighted ? `${1.5 * scale}px ${ink}` : undefined,
        WebkitTextFillColor: style === 'outline_type' && !highlighted ? 'transparent' : undefined,
      }}>{word.slice(a-start,b-start)}</span>;
    });
  };

  // hero_type and compact get a clean, tightly-bound panel background with an accent left border.
  const isHero = style === 'hero_type';
  const effectiveBg = panelBackground || visualDNA.palette?.surface || 'rgba(10,10,12,.88)';
  const hasCardPanel = (compact || isHero) && Boolean(effectiveBg);
  const textShadowStyle = '0 2px 4px rgba(0,0,0,0.95), 0 4px 14px rgba(0,0,0,0.85)';

  // High-impact documentary typography: a tightly fitted, palette-colored
  // slab for the director's exact emphasis span, with the remaining approved
  // words stacked beneath it. The reveal is a single clean wipe and settle;
  // there is no shake or repeated pulse.
  if (style === 'impact_slab') {
    const validSpan = spans.find((span) => Number.isInteger(span.start) && Number.isInteger(span.end)
      && span.start >= 0 && span.end > span.start && span.end <= String(text || '').length);
    const focusText = validSpan ? String(text).slice(validSpan.start, validSpan.end) : '';
    const parts = (validSpan
      ? [
        { text: String(text).slice(0, validSpan.start), focused: false },
        { text: focusText, focused: true },
        { text: String(text).slice(validSpan.end), focused: false },
      ]
      : [{ text: String(text || ''), focused: true }])
      .filter((part) => part.text.trim());
    const scale = width / 1920;
    const maxChars = Math.max(1, ...parts.map((part) => part.text.trim().length));
    const rawSafeRegion = Array.isArray(scene.media_composition?.safe_region)
      ? scene.media_composition.safe_region.map(Number) : null;
    const safeRegion = rawSafeRegion && rawSafeRegion.length === 4
      && rawSafeRegion.every(Number.isFinite)
      && rawSafeRegion.every((value) => value >= 0 && value <= 1)
      && rawSafeRegion[2] - rawSafeRegion[0] >= .24
      && rawSafeRegion[3] - rawSafeRegion[1] >= .08
      ? rawSafeRegion : null;
    const safeWidth = safeRegion ? Math.max(.12, safeRegion[2] - safeRegion[0] - .025) : .82;
    const safeHeight = safeRegion ? Math.max(.07, safeRegion[3] - safeRegion[1] - .025) : .78;
    const impactSize = Math.min(
      146 * scale,
      width * safeWidth / Math.max(1, maxChars * .61),
      height * safeHeight * .78 / Math.max(1, parts.length * .91),
    );
    const enter = interpolate(frame, [0, Math.max(1, Math.round(.24 * fps))], [0, 1], { easing: ease, extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
    const slabReveal = interpolate(frame, [Math.max(0, Math.round(.03 * fps)), Math.max(1, Math.round(.25 * fps))], [0, 1], { easing: ease, extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
    const safeCenter = safeRegion ? (safeRegion[0] + safeRegion[2]) / 2 : null;
    const impactCentered = safeRegion ? safeCenter > .41 && safeCenter < .59 : center;
    const impactRight = safeRegion ? safeCenter >= .59 : right;
    const impactLocation = safeRegion
      ? { left: `${safeRegion[0] * 100}%`, top: `${safeRegion[1] * 100}%`, transform: 'none' }
      : center
      ? { left: '50%', top: '46%', transform: 'translate(-50%, -50%)' }
      : { ...(right ? { right: '7%' } : { left: '7%' }), ...(middle ? { top: '50%', transform: 'translateY(-50%)' } : top ? { top: '10%' } : { bottom: '15%' }) };
    return <AbsoluteFill style={{ pointerEvents: 'none', zIndex: 12, overflow: 'hidden' }}>
      <div style={{
        position: 'absolute', ...impactLocation, width: 'fit-content', maxWidth: `${safeWidth * 100}%`,
        display: 'flex', flexDirection: 'column', alignItems: impactCentered ? 'center' : impactRight ? 'flex-end' : 'flex-start',
        gap: `${Math.max(2, 3 * scale)}px`, textAlign: impactCentered ? 'center' : impactRight ? 'right' : 'left',
        opacity: exit * enter, transform: `${impactLocation.transform || ''} translateY(${(1 - enter) * 16 * scale}px)`,
      }}>
        {parts.map((part, index) => {
          const focused = part.focused;
          const delay = Math.max(0, index - (validSpan ? 0 : 1)) * Math.round(.07 * fps);
          const lineEnter = interpolate(frame, [delay, delay + Math.max(1, Math.round(.18 * fps))], [0, 1], { easing: ease, extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
          return <div key={`${index}-${part}`} style={{
            position: 'relative', boxSizing: 'border-box', width: 'fit-content', maxWidth: '100%',
            padding: focused ? `${Math.max(1, 2 * scale)}px ${Math.max(8, 13 * scale)}px ${Math.max(3, 6 * scale)}px` : `${Math.max(1, 1 * scale)}px ${Math.max(5, 9 * scale)}px`,
            background: focused ? accent : 'transparent', color: focused ? markerInk : ink,
            fontFamily: fonts.display, fontSize: impactSize, fontWeight: 950, lineHeight: .91,
            letterSpacing: '-.06em', textTransform: 'uppercase', overflowWrap: 'anywhere',
            textShadow: focused ? '0 3px 2px rgba(0,0,0,.72), 0 7px 16px rgba(0,0,0,.52)' : '0 4px 3px rgba(0,0,0,.98), 0 8px 20px rgba(0,0,0,.72)',
            boxShadow: focused ? '0 5px 0 rgba(0,0,0,.70), 0 13px 28px rgba(0,0,0,.5)' : 'none',
            clipPath: focused ? `inset(0 ${Math.max(0, (1 - slabReveal) * 100)}% 0 0)` : undefined,
            opacity: lineEnter, transform: `translateY(${(1 - lineEnter) * 10 * scale}px) scale(${focused ? .985 + .015 * slabReveal : 1})`,
            transformOrigin: impactRight ? 'right center' : impactCentered ? 'center' : 'left center',
          }}>{part.text.trim()}</div>;
        })}
      </div>
    </AbsoluteFill>;
  }

  return <AbsoluteFill style={{ pointerEvents: 'none', zIndex: 12, overflow: 'hidden' }}>
    <div data-documentary-text="editorial-copy" style={{
      position: 'absolute',
      ...location,
      width: 'fit-content',
      maxWidth: safeRegion ? safeWidth : center ? '84%' : '82%',
      boxSizing: 'border-box',
      padding: hasCardPanel ? `${(compact ? 8 : 10) * scale}px ${(compact ? 14 : 18) * scale}px` : 0,
      borderLeft: hasCardPanel ? `${Math.max(.5, 4 * scale)}px solid ${accent}` : undefined,
      borderRadius: hasCardPanel ? `${Math.max(.5, 3 * scale)}px` : undefined,
      background: hasCardPanel ? effectiveBg : undefined,
      boxShadow: hasCardPanel ? '0 8px 24px rgba(0,0,0,0.45)' : undefined,
      opacity: exit * containerEnter,
      display: 'inline-flex',
      flexDirection: stacked ? 'column' : 'row',
      flexWrap: 'wrap',
      alignItems: right ? 'flex-end' : center ? 'center' : 'flex-start',
      justifyContent: right ? 'flex-end' : center ? 'center' : 'flex-start',
      gap: stacked ? '0px' : '.14em .21em',
      fontFamily: compact ? fonts.editorial : fonts.display,
      fontSize: size,
      fontWeight: 900,
      lineHeight: .98,
      letterSpacing: '-.055em',
      textTransform: compact ? 'none' : 'uppercase',
      textShadow: textShadowStyle,
      WebkitTextStroke: hasCardPanel ? undefined : `${Math.max(.6, 1.2 * scale)}px rgba(0,0,0,.8)`,
      paintOrder: 'stroke fill',
      filter: 'drop-shadow(0 18px 28px rgba(0,0,0,.35))',
    }}>
      {words.map((match, index) => {
        const delay = index * Math.max(2, Math.round((stacked ? .075 : .045) * fps));
        const enter = interpolate(frame, [delay, delay + Math.max(1, Math.round(.28 * fps))], [0, 1], { easing: ease, extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
        const move = compact ? `translateY(${(1-enter)*12}px)`
          : stacked ? `translateX(${(1-enter)*(index%2 ? 44 : -44)}px)`
            : style === 'outline_type' ? `translateX(${(1-enter)*24}px)`
              : style === 'marker_type' ? 'none' : `translateY(${(1-enter)*105}%) rotate(${(1-enter)*(index%2 ? 2 : -2)}deg)`;
        return <div key={`${match.index}-${match[0]}`} style={{ overflow: 'hidden', padding: '.025em .025em .1em', marginBottom: '-.1em', maxWidth: '100%', width: 'fit-content' }}>
          <span style={{ display: 'inline-block', opacity: enter, transform: move, clipPath: style === 'marker_type' ? `inset(0 ${(1-enter)*100}% 0 0)` : undefined, whiteSpace: 'nowrap' }}>
            {segments(match[0], match.index)}
          </span>
        </div>;
      })}
      {style === 'minimal_rule' && (
        <div style={{
          width: `${containerEnter * 100}%`,
          maxWidth: '100%',
          height: `${Math.max(3, Math.round(4 * scale))}px`,
          background: accent,
          marginTop: '0.12em',
          borderRadius: '2px',
          boxShadow: `0 2px 8px rgba(0,0,0,0.85), 0 0 12px ${accent}88`,
          transformOrigin: right ? 'right center' : center ? 'center' : 'left center',
        }} />
      )}
    </div>
  </AbsoluteFill>;
}
