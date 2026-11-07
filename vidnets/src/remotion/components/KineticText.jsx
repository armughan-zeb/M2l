import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { computeSpringEnter, computeEntranceStyle } from '../engine/MotionEngine';
import { rgba } from '../theme';
import { EditorialType, EDITORIAL_TYPE_STYLES } from './EditorialType';
import { hasApprovedDirectorCopy } from '../engine/DirectorMotion';
import { documentaryTypography, fittedDocumentarySize } from '../engine/DocumentaryTypography';

function markerInk(color) {
  const hex = String(color || '').replace('#', '');
  if (!/^[a-f0-9]{6}$/i.test(hex)) return '#FFFFFF';
  const values = [0, 2, 4].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4);
  return .2126 * values[0] + .7152 * values[1] + .0722 * values[2] > .179 ? '#101820' : '#FFFFFF';
}

function approvedSegments(source, spans, accentColor) {
  const segments = [];
  let cursor = 0;
  for (const span of spans) {
    if (span.start < cursor) continue;
    if (span.start > cursor) segments.push({ text: source.slice(cursor, span.start), accent: false });
    segments.push({ text: source.slice(span.start, span.end), accent: true });
    cursor = span.end;
  }
  if (cursor < source.length) segments.push({ text: source.slice(cursor), accent: false });
  return (segments.length ? segments : [{ text: source, accent: false }]).map((segment, index) => (
    <span key={`${index}-${segment.text}`} style={segment.accent ? {
      color: markerInk(accentColor), background: accentColor, padding: '0 .12em .035em',
      boxDecorationBreak: 'clone', WebkitBoxDecorationBreak: 'clone',
      boxShadow: '0 5px 18px rgba(0,0,0,.24)',
    } : undefined}>{segment.text}</span>
  ));
}

/**
 * KineticText — Universal Documentary Typography Motion Engine.
 * Supported variants:
 * - word_by_word_punch
 * - staggered_lines
 * - mask_slide_up
 * - bold_minimal_center
 * - tracking_expand
 * - boxed_headline (legacy manifests render as an open headline)
 * - split_counter_accent
 * - impact_slab (palette-colored headline slab with a firm drop shadow)
 */
export function KineticText({ scene = {}, visualDNA = {}, props = {} }) {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const scale = width / 1920;
  const fonts = documentaryTypography(visualDNA);
  const sceneDuration = Math.max(1, scene?.duration_in_frames !== undefined && scene?.duration_in_frames !== null
    ? Math.round(Number(scene.duration_in_frames))
    : Math.round(Number(scene?.duration || 4) * fps));
  const progress = Math.min(1, Math.max(0, frame / Math.max(1, sceneDuration - 1)));

  // An explicit empty display_text means the director intentionally omitted
  // an overlay. Do not fall back to the whole narration and turn it into a
  // sentence-sized headline.
  const hasExplicitCopy = Object.prototype.hasOwnProperty.call(props, 'display_text')
    || Object.prototype.hasOwnProperty.call(props, '_display_text')
    || Object.prototype.hasOwnProperty.call(scene?.text || {}, 'display_text');
  const text = String(props.text ?? (hasExplicitCopy ? props.display_text : (scene.text?.primary || scene.narration)) ?? '').trim();
  const kicker = String(props.kicker || scene.text?.kicker || '').trim();
  const emphasis = String(props.emphasis || scene.text?.emphasis || '').trim();
  const requestedVariant = String(props.variant || scene.primary_visual?.variant || 'bold_minimal_center').toLowerCase();
  const variant = requestedVariant === 'boxed_headline' ? 'bold_minimal_center' : requestedVariant;

  const palette = visualDNA.palette || {};
  const enter = computeSpringEnter(frame, fps, 0, scene?.motion?.spring_stiffness || 175, scene?.motion?.spring_damping || 15);
  // Strict editorial isolation: motion graphics smoothly exit before scene cut, zero bleed into next scene
  const entranceStyle = computeEntranceStyle(scene?.motion?.entrance || 'fade', enter, progress, true);

  const words = text.split(/\s+/).filter(Boolean);
  const copy = scene.editorial_contract || {};
  const position = String(scene.text?.position || props.position || 'center').replace(/_/g, '-');
  const rawRegion = scene.media_composition?.safe_region;
  const safeRegion = Array.isArray(rawRegion) && rawRegion.length === 4
    && rawRegion.every(v => Number.isFinite(v) && v >= 0 && v <= 1)
    && rawRegion[2] - rawRegion[0] >= .24 && rawRegion[3] - rawRegion[1] >= .08
    ? rawRegion : null;
  const copyWidth = width * (safeRegion ? safeRegion[2] - safeRegion[0] : .72);
  const copyHeight = height * (safeRegion ? safeRegion[3] - safeRegion[1] : .78);
  // Every animation variant uses the same subject-safe box. Relative padding
  // previously moved mask-slide copy into the independent subtitle band.
  const placementStyle = safeRegion ? {
    left: `${safeRegion[0] * 100}%`, top: `${safeRegion[1] * 100}%`,
    right: 'auto', bottom: 'auto', width: copyWidth, height: copyHeight,
    justifyContent: 'center', alignItems: position.includes('right') ? 'flex-end' : position.includes('left') ? 'flex-start' : 'center',
    padding: 0,
  } : {
    justifyContent: position.includes('top') ? 'flex-start' : position.includes('bottom') ? 'flex-end' : 'center',
    alignItems: position.includes('left') ? 'flex-start' : position.includes('right') ? 'flex-end' : 'center',
    padding: position.includes('bottom') ? '10% 8% 18%' : '12% 8%',
  };
  if (hasApprovedDirectorCopy(scene)) {
    const fit = (maximum) => fittedDocumentarySize(copy.display_text, { fontFamily: fonts.display,
      maxSize: Math.min(maximum * scale, height * .18, copyHeight * .7), withinWidth: copyWidth * .76 });
    const spans = (Array.isArray(copy.emphasis_spans) ? copy.emphasis_spans : [])
      .filter((s) => Number.isInteger(s.start) && Number.isInteger(s.end) && s.start >= 0 && s.end > s.start && s.end <= copy.display_text.length);
    const accentColor = scene.text?.accent_color || palette.accent || '#f2b544';
    const inkColor = scene.text?.text_color || palette.primary || '#FFFFFF';
    const approvedRun = () => approvedSegments(copy.display_text, spans, accentColor);
    // Director-selected grammar is honored while every glyph still comes
    // exclusively from the validated copy contract.
    if (variant === 'split_counter_accent') return (
      <AbsoluteFill style={{ background: 'transparent', ...entranceStyle, ...placementStyle }}>
        <div data-documentary-text="kinetic-copy" style={{ maxWidth: '82%', fontFamily: fonts.display, fontSize: fit(86), fontWeight: 900, color: inkColor, lineHeight: 1.04, letterSpacing: '-.045em', textAlign: 'center', textShadow: '0 4px 26px rgba(0,0,0,.92)', overflowWrap: 'break-word' }}>{approvedRun()}</div>
        <div style={{ width: 'min(380px, 42vw)', height: 4, marginTop: 22, background: `linear-gradient(90deg, transparent, ${accentColor}, transparent)`, transform: `scaleX(${Math.min(1, Math.max(0, frame / Math.max(1, .24 * fps)))})`, transformOrigin: 'center' }} />
      </AbsoluteFill>
    );
    if (variant === 'word_by_word_punch') {
      const tokens = [...copy.display_text.matchAll(/\S+/g)];
      const framesPerWord = Math.max(1, Math.min(Math.round(.12 * fps), Math.floor(.35 * fps / Math.max(1, tokens.length - 1))));
      return <AbsoluteFill style={placementStyle}>
        <div data-documentary-text="kinetic-copy" style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', alignItems: 'center', gap: `${8 * scale}px ${12 * scale}px`, maxWidth: '88%', lineHeight: 1.28, padding: '.22em 0', fontSize: fit(76) }}>
          {tokens.map((token, index) => {
            const wordEnter = spring({ frame: Math.max(0, frame - index * framesPerWord), fps, config: { damping: 14, stiffness: 190 } });
            const localSpans = spans.filter((span) => span.start < token.index + token[0].length && span.end > token.index)
              .map((span) => ({ start: Math.max(0, span.start - token.index), end: Math.min(token[0].length, span.end - token.index) }));
            return <span key={`${token.index}-${token[0]}`} style={{ fontFamily: fonts.display, fontSize: fit(76), fontWeight: 900, color: inkColor, opacity: wordEnter, transform: `translateY(${(1 - wordEnter) * 18}px)`, textShadow: '0 4px 24px rgba(0,0,0,.94)' }}>
              {approvedSegments(token[0], localSpans, accentColor)}
            </span>;
          })}
        </div>
      </AbsoluteFill>;
    }
    if (variant === 'staggered_lines') return (
      <AbsoluteFill style={{ background: 'transparent', ...entranceStyle, ...placementStyle }}>
        {kicker && <div style={{ color: accentColor, fontFamily: fonts.body, fontSize: 20, fontWeight: 800, letterSpacing: '.16em', textTransform: 'uppercase', marginBottom: 18 }}>{kicker}</div>}
        <div data-documentary-text="kinetic-copy" style={{ maxWidth: '76%', fontFamily: fonts.display, fontSize: fit(76), fontWeight: 900, color: inkColor, lineHeight: 1.06, letterSpacing: '-.04em', textShadow: '0 4px 24px rgba(0,0,0,.94)', overflowWrap: 'break-word' }}>{approvedRun()}</div>
      </AbsoluteFill>
    );
    if (variant === 'mask_slide_up') {
      const slide = interpolate(enter, [0, 1], [100, 0]);
      return <AbsoluteFill style={placementStyle}>
        <div data-documentary-text="kinetic-copy" style={{ overflow: 'hidden', maxWidth: '82%', textAlign: 'center', transform: `translateY(${slide}%)`, fontFamily: fonts.display, fontSize: fit(82), fontWeight: 900, lineHeight: 1.03, letterSpacing: '-.045em', color: inkColor, textShadow: '0 4px 26px rgba(0,0,0,.94)' }}>{approvedRun()}</div>
      </AbsoluteFill>;
    }
    if (variant === 'tracking_expand') {
      const letterSpacing = interpolate(progress, [0, 1], [0.005, 0.045]);
      return <AbsoluteFill style={{ ...entranceStyle, ...placementStyle }}>
        {kicker && <div style={{ color: palette.secondary, fontFamily: fonts.body, fontSize: 18, letterSpacing: '.18em', textTransform: 'uppercase', marginBottom: 18 }}>{kicker}</div>}
        <div data-documentary-text="kinetic-copy" style={{ maxWidth: '80%', textAlign: 'center', fontFamily: fonts.display, fontSize: fit(70), fontWeight: 850, lineHeight: 1.1, letterSpacing: `${letterSpacing}em`, color: inkColor, textShadow: '0 4px 24px rgba(0,0,0,.94)' }}>{approvedRun()}</div>
      </AbsoluteFill>;
    }
    // The approved copy uses the chosen type system and exact emphasis spans;
    // panel style controls the tight high-contrast highlight treatment.
    const panel = scene.text?.panel_style;
    const requested = panel && panel !== 'none' ? panel : variant === 'impact_slab' ? 'impact_slab' : 'hero_type';
    const style = EDITORIAL_TYPE_STYLES.has(requested) ? requested : 'hero_type';
    return <EditorialType scene={scene} visualDNA={visualDNA} text={copy.display_text} spans={spans} style={style} position={scene.text?.position || 'center'} accent={accentColor} ink={inkColor} panelBackground={scene.text?.panel_background || scene.text?.panelBackground} />;
  }

  if (scene.director_treatment_enabled === true || Object.prototype.hasOwnProperty.call(scene.text || {}, 'overlay_policy')) return null;

  // Variant 1: Word by Word Punch
  if (variant === 'word_by_word_punch') {
    const framesPerWord = Math.max(4, Math.floor((sceneDuration * 0.7) / Math.max(1, words.length)));
    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: 'transparent', padding: '0 10%' }}>
        {kicker && (
          <div style={{ color: palette.accent, fontSize: 18, fontFamily: visualDNA.typography.body, letterSpacing: '0.15em', textTransform: 'uppercase', marginBottom: 24, textShadow: '0 2px 10px rgba(0,0,0,0.9)' }}>
            {kicker}
          </div>
        )}
        <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: '14px 22px', maxWidth: '1400px' }}>
          {words.map((word, i) => {
            const wordDelay = i * framesPerWord;
            const wordEnter = spring({ frame: Math.max(0, frame - wordDelay), fps, config: { damping: 14, stiffness: 180 } });
            const isEmphasized = emphasis && word.toLowerCase().includes(emphasis.toLowerCase());
            return (
              <span
                key={i}
                style={{
                  fontFamily: visualDNA.typography.display,
                  fontSize: 54,
                  fontWeight: 800,
                  color: isEmphasized ? palette.accent : palette.primary,
                  opacity: wordEnter,
                  transform: `scale(${(0.8 + 0.2 * wordEnter).toFixed(3)}) translateY(${((1 - wordEnter) * 20).toFixed(1)}px)`,
                  textShadow: isEmphasized ? `0 0 25px ${rgba(palette.accent, 0.6)}` : '0 4px 20px rgba(0,0,0,0.9)',
                }}
              >
                {word}
              </span>
            );
          })}
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 2: Staggered Lines
  if (variant === 'staggered_lines') {
    const lines = props.lines || [kicker, text].filter(Boolean);
    return (
      <AbsoluteFill style={{ justifyContent: 'center', padding: '0 12%', background: 'transparent' }}>
        {lines.map((line, idx) => {
          const lineEnter = spring({ frame: Math.max(0, frame - idx * 8), fps, config: { damping: 16, stiffness: 130 } });
          const isAccent = idx === 0 && kicker;
          return (
            <div
              key={idx}
              style={{
                fontFamily: isAccent ? visualDNA.typography.body : visualDNA.typography.display,
                fontSize: isAccent ? 24 : 64,
                fontWeight: isAccent ? 600 : 800,
                color: isAccent ? palette.accent : palette.primary,
                letterSpacing: isAccent ? '0.12em' : '-0.01em',
                textTransform: isAccent ? 'uppercase' : 'none',
                opacity: lineEnter,
                transform: `translateY(${((1 - lineEnter) * 40).toFixed(1)}px)`,
                marginBottom: isAccent ? 16 : 8,
                lineHeight: 1.15,
                textShadow: '0 4px 24px rgba(0,0,0,0.95)',
              }}
            >
              {line}
            </div>
          );
        })}
      </AbsoluteFill>
    );
  }

  // Variant 3: Mask Slide Up
  if (variant === 'mask_slide_up') {
    const slide = interpolate(enter, [0, 1], [100, 0]);
    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: 'transparent', padding: '0 8%' }}>
        <div style={{ overflow: 'hidden', padding: '10px 0' }}>
          {kicker && (
            <div
              style={{
                color: palette.accent,
                fontSize: 20,
                fontFamily: visualDNA.typography.body,
                letterSpacing: '0.18em',
                textTransform: 'uppercase',
                textAlign: 'center',
                marginBottom: 16,
                transform: `translateY(${slide}%)`,
                textShadow: '0 2px 10px rgba(0,0,0,0.9)',
              }}
            >
              {kicker}
            </div>
          )}
          <div
            style={{
              fontFamily: visualDNA.typography.display,
              fontSize: 68,
              fontWeight: 800,
              color: palette.primary,
              textAlign: 'center',
              lineHeight: 1.15,
              transform: `translateY(${slide}%)`,
              textShadow: '0 4px 24px rgba(0,0,0,0.95)',
            }}
          >
            {text}
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 4: Tracking Expand
  if (variant === 'tracking_expand') {
    const letterSpacing = interpolate(progress, [0, 1], [0.02, 0.16]);
    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: 'transparent', ...entranceStyle }}>
        {kicker && (
          <div style={{ color: palette.secondary, fontSize: 16, fontFamily: visualDNA.typography.body, letterSpacing: '0.2em', textTransform: 'uppercase', marginBottom: 20, textShadow: '0 2px 10px rgba(0,0,0,0.9)' }}>
            {kicker}
          </div>
        )}
        <div
          style={{
            fontFamily: visualDNA.typography.display,
            fontSize: 60,
            fontWeight: 700,
            color: palette.primary,
            textAlign: 'center',
            letterSpacing: `${letterSpacing.toFixed(3)}em`,
            maxWidth: '1200px',
            lineHeight: 1.25,
            textShadow: '0 4px 24px rgba(0,0,0,0.95)',
          }}
        >
          {text}
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 6: Split Counter Accent
  if (variant === 'split_counter_accent') {
    return (
      <AbsoluteFill style={{ justifyContent: 'center', padding: '0 12%', background: 'transparent', ...entranceStyle }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 20, flexWrap: 'wrap' }}>
          <span style={{ fontFamily: visualDNA.typography.display, fontSize: 72, fontWeight: 900, color: palette.primary, textShadow: '0 4px 24px rgba(0,0,0,0.95)' }}>
            {text}
          </span>
          {emphasis && (
            <span style={{ fontFamily: visualDNA.typography.numbers || visualDNA.typography.display, fontSize: 72, fontWeight: 900, color: palette.accent, textShadow: `0 0 30px ${rgba(palette.accent, 0.6)}` }}>
              {emphasis}
            </span>
          )}
        </div>
        {kicker && (
          <div style={{ marginTop: 18, color: palette.secondary, fontSize: 20, fontFamily: visualDNA.typography.body, letterSpacing: '0.06em', textShadow: '0 2px 10px rgba(0,0,0,0.9)' }}>
            {kicker}
          </div>
        )}
      </AbsoluteFill>
    );
  }

  // Variant 7: Bold Minimal Center (Default)
  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: 'transparent', padding: '0 10%', ...entranceStyle }}>
      {kicker && (
        <div style={{ color: palette.accent, fontSize: 18, fontFamily: visualDNA.typography.body, letterSpacing: '0.16em', textTransform: 'uppercase', marginBottom: 20, textShadow: '0 2px 10px rgba(0,0,0,0.9)' }}>
          {kicker}
        </div>
      )}
      <div
        style={{
          fontFamily: visualDNA.typography.display,
          fontSize: 64,
          fontWeight: 800,
          color: palette.primary,
          textAlign: 'center',
          maxWidth: '1300px',
          lineHeight: 1.2,
          textShadow: '0 4px 24px rgba(0,0,0,0.95)',
        }}
      >
        {text}
      </div>
    </AbsoluteFill>
  );
}
