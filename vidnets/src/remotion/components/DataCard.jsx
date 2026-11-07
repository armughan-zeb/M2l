import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { computeSpringEnter, computeEntranceStyle } from '../engine/MotionEngine';
import { resolveCardPlacement } from '../engine/LayoutEngine';
import { rgba } from '../theme';
import { safeFadeOutRange } from '../engine/DirectorMotion';
import { documentaryTypography, fittedDocumentarySize } from '../engine/DocumentaryTypography';

/**
 * DataCard — High-Impact Modern Documentary Fact & Metric Card.
 * 
 * Features:
 * - Ultra-clean glassmorphism panel with accent border indicator
 * - Animated numeric rolling counter with prefix/suffix/unit
 * - Category/kicker badge with verification indicator
 * - Dynamic context typography with high contrast
 * - Seamless spring entry and safe exit transition before scene cut
 */
export function DataCard({ scene = {}, visualDNA = {}, props = {} }) {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const scale = width / 1920;
  const fonts = documentaryTypography(visualDNA);

  const sceneDuration = Math.max(1, scene?.duration_in_frames !== undefined && scene?.duration_in_frames !== null
    ? Math.round(Number(scene.duration_in_frames))
    : Math.round(Number(scene?.duration || 4) * fps));
  const progress = Math.min(1, Math.max(0, frame / Math.max(1, sceneDuration - 1)));

  // Data fields
  const kicker = String(props.kicker || props.category || props.source || scene.text?.kicker || 'DATA SPOTLIGHT').trim().toUpperCase();
  const rawValue = props.value ?? props.target ?? props.stat ?? props.number;
  const parsedValue = typeof rawValue === 'number'
    ? rawValue
    : Number(String(rawValue ?? '').replace(/[^0-9.-]/g, ''));
  const hasNumericValue = rawValue != null && Number.isFinite(parsedValue);

  const prefix = String(props.prefix ?? '');
  const suffix = String(props.suffix ?? props.unit ?? '');
  const title = String(props.title || props.headline || props.label || scene.text?.headline || '').trim();
  const body = String(props.body || props.text || props.description || props.detail || scene.text?.subtext || '').trim();

  const palette = visualDNA.palette || {};
  const accent = palette.accent || '#E5A93C';
  const accent2 = palette.accent2 || palette.secondary || '#63B3ED';
  const background = palette.background || '#07121B';
  const foreground = palette.primary || '#F8F7F2';

  // Animation: Spring entry
  const enter = computeSpringEnter(frame, fps, 0, 160, 16);
  const entranceStyle = computeEntranceStyle(scene?.motion?.entrance || 'fade', enter, progress, true);

  // Counter roll over first 55% of the scene duration
  const rollProgress = Math.min(1, Math.max(0, frame / Math.max(1, sceneDuration * 0.55)));
  const easeOutExpo = (t) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t));
  const currentNumeric = hasNumericValue
    ? interpolate(easeOutExpo(rollProgress), [0, 1], [0, parsedValue])
    : null;
  const formattedNumber = hasNumericValue
    ? (props.decimals ? currentNumeric.toFixed(Number(props.decimals)) : Math.round(currentNumeric).toLocaleString())
    : String(rawValue || '');

  // Exit Animation
  const exitP = interpolate(frame, safeFadeOutRange(sceneDuration, 12), [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const exitFade = 1 - exitP;
  const exitY = exitP * 28;
  const finalOpacity = (entranceStyle.opacity !== undefined ? Number(entranceStyle.opacity) : 1) * exitFade;

  // Placement: defaults to left-third documentary standard
  const position = String(props.position || scene.placement?.position || 'bottom_left').toLowerCase();
  const isRight = position.includes('right');
  const isTop = position.includes('top');
  const isCenter = position.includes('center') || position.includes('middle');

  const containerPlacement = isCenter
    ? { left: '50%', top: '50%', transform: `translate(-50%, -50%) translateY(${exitY}px)` }
    : {
        ...(isRight ? { right: '6%' } : { left: '6%' }),
        ...(isTop ? { top: '8%' } : { bottom: '10%' }),
        transform: `translateY(${exitY}px)`,
      };

  const cardWidth = Math.min(680 * scale, width * 0.44);

  return (
    <AbsoluteFill style={{ pointerEvents: 'none', zIndex: 12 }}>
      <div style={{
        position: 'absolute',
        ...containerPlacement,
        width: cardWidth,
        opacity: finalOpacity,
        background: `linear-gradient(135deg, ${rgba(background, 0.94)} 0%, ${rgba(palette.surface || '#0c1a26', 0.88)} 100%)`,
        backdropFilter: 'blur(16px)',
        WebkitBackdropFilter: 'blur(16px)',
        borderRadius: 14 * scale,
        border: `1px solid ${rgba(accent, 0.28)}`,
        borderLeft: `5px solid ${accent}`,
        boxShadow: `0 24px 60px rgba(0,0,0,0.65), 0 0 25px ${rgba(accent, 0.12)}`,
        padding: `${26 * scale}px ${32 * scale}px`,
        overflow: 'hidden',
      }}>
        {/* Subtle glass reflection sweep */}
        <div style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          height: '40%',
          background: 'linear-gradient(180deg, rgba(255,255,255,0.06) 0%, transparent 100%)',
          pointerEvents: 'none',
        }} />

        {/* Kicker Badge */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10 * scale,
          marginBottom: 14 * scale,
        }}>
          <span style={{
            width: 8 * scale,
            height: 8 * scale,
            borderRadius: '50%',
            background: accent,
            boxShadow: `0 0 10px ${accent}`,
            display: 'inline-block',
          }} />
          <span style={{
            fontFamily: fonts.body,
            fontSize: 14 * scale,
            fontWeight: 800,
            letterSpacing: '0.14em',
            color: accent,
            textTransform: 'uppercase',
          }}>
            {kicker}
          </span>
          <div style={{
            flex: 1,
            height: 1,
            background: `linear-gradient(90deg, ${rgba(accent, 0.4)}, transparent)`,
          }} />
        </div>

        {/* Big Numeric Stat */}
        {(hasNumericValue || rawValue) && (
          <div style={{
            fontFamily: fonts.display,
            fontSize: 58 * scale,
            fontWeight: 900,
            lineHeight: 1.0,
            letterSpacing: '-0.03em',
            color: foreground,
            marginBottom: 12 * scale,
            display: 'flex',
            alignItems: 'baseline',
            gap: 4 * scale,
            textShadow: '0 4px 18px rgba(0,0,0,0.5)',
          }}>
            {prefix && <span style={{ color: accent, fontSize: '0.7em', fontWeight: 800 }}>{prefix}</span>}
            <span style={{
              background: `linear-gradient(180deg, #FFFFFF 20%, ${rgba(foreground, 0.85)} 100%)`,
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
            }}>
              {formattedNumber}
            </span>
            {suffix && <span style={{ color: accent2, fontSize: '0.65em', fontWeight: 750, marginLeft: 3 * scale }}>{suffix}</span>}
          </div>
        )}

        {/* Title / Headline */}
        {title && (
          <div style={{
            fontFamily: fonts.display,
            fontSize: 24 * scale,
            fontWeight: 850,
            lineHeight: 1.15,
            letterSpacing: '-0.02em',
            color: foreground,
            marginBottom: body ? 10 * scale : 0,
            textTransform: 'uppercase',
          }}>
            {title}
          </div>
        )}

        {/* Detail Context Body */}
        {body && (
          <div style={{
            fontFamily: fonts.body,
            fontSize: 16 * scale,
            fontWeight: 500,
            lineHeight: 1.35,
            color: rgba(foreground, 0.82),
            borderTop: `1px solid ${rgba(foreground, 0.12)}`,
            paddingTop: 10 * scale,
          }}>
            {body}
          </div>
        )}

        {/* Animated bottom progress line */}
        <div style={{
          position: 'absolute',
          bottom: 0,
          left: 0,
          width: `${Math.min(100, enter * 100)}%`,
          height: 3,
          background: `linear-gradient(90deg, ${accent}, ${accent2})`,
        }} />
      </div>
    </AbsoluteFill>
  );
}
