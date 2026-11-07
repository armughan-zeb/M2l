import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { computeSpringEnter } from '../engine/MotionEngine';
import { rgba } from '../theme';
import { safeFadeOutRange } from '../engine/DirectorMotion';
import { documentaryTypography } from '../engine/DocumentaryTypography';

/**
 * CinematicHighlight — Documentary-Grade Kinetic Highlight & Lower-Third.
 * 
 * Features:
 * - Animated gold/accent highlighter sweep under key phrases or quotes
 * - Shimmer sweep lighting effect across the container
 * - Official verification badge / category indicator
 * - Dynamic lower-third layout with crisp drop shadows and glass matte
 * - Spring entrance and clean dissolve exit
 */
export function CinematicHighlight({ scene = {}, visualDNA = {}, props = {} }) {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const scale = width / 1920;
  const fonts = documentaryTypography(visualDNA);

  const sceneDuration = Math.max(1, scene?.duration_in_frames !== undefined && scene?.duration_in_frames !== null
    ? Math.round(Number(scene.duration_in_frames))
    : Math.round(Number(scene?.duration || 4) * fps));

  const text = String(props.text || props.headline || props.highlight || scene.text?.headline || scene.narration || '').trim();
  const kicker = String(props.kicker || props.category || props.source || scene.text?.kicker || 'KEY FINDING').trim().toUpperCase();
  const subtext = String(props.subtext || props.role || props.author || scene.text?.subtext || '').trim();

  const palette = visualDNA.palette || {};
  const accent = palette.accent || '#E5A93C';
  const accent2 = palette.accent2 || '#4FD1C5';
  const background = palette.background || '#07121B';
  const foreground = palette.primary || '#F8F7F2';

  // Springs
  const enter = computeSpringEnter(frame, fps, 0, 150, 15);
  // Highlight bar wipe starts slightly after card enters
  const highlightWipe = interpolate(frame, [8, 28], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Exit
  const exitP = interpolate(frame, safeFadeOutRange(sceneDuration, 12), [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const exitFade = 1 - exitP;
  const exitY = exitP * 24;

  const position = String(props.position || 'bottom_left').toLowerCase();
  const isRight = position.includes('right');
  const isTop = position.includes('top');
  const isCenter = position.includes('center');

  const cardPlacement = isCenter
    ? { left: '50%', top: '50%', transform: `translate(-50%, -50%) translateY(${exitY}px)` }
    : {
        ...(isRight ? { right: '6%' } : { left: '6%' }),
        ...(isTop ? { top: '8%' } : { bottom: '11%' }),
        transform: `translateY(${exitY}px)`,
      };

  const cardMaxWidth = Math.min(840 * scale, width * 0.65);

  return (
    <AbsoluteFill style={{ pointerEvents: 'none', zIndex: 12 }}>
      <div style={{
        position: 'absolute',
        ...cardPlacement,
        maxWidth: cardMaxWidth,
        opacity: enter * exitFade,
        background: `linear-gradient(135deg, ${rgba(background, 0.92)} 0%, ${rgba(palette.surface || '#0c1a26', 0.82)} 100%)`,
        backdropFilter: 'blur(14px)',
        WebkitBackdropFilter: 'blur(14px)',
        borderRadius: 12 * scale,
        border: `1px solid ${rgba(accent, 0.25)}`,
        borderLeft: `5px solid ${accent}`,
        boxShadow: `0 20px 50px rgba(0,0,0,0.6), 0 0 20px ${rgba(accent, 0.1)}`,
        padding: `${22 * scale}px ${28 * scale}px`,
        overflow: 'hidden',
      }}>
        {/* Shimmer sweep line */}
        <div style={{
          position: 'absolute',
          top: 0,
          bottom: 0,
          left: `${interpolate(frame, [10, 45], [-30, 130], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' })}%`,
          width: 60 * scale,
          background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.08), transparent)',
          transform: 'skewX(-20deg)',
          pointerEvents: 'none',
        }} />

        {/* Kicker badge */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8 * scale,
          marginBottom: 10 * scale,
        }}>
          <span style={{
            display: 'inline-block',
            width: 6 * scale,
            height: 6 * scale,
            borderRadius: '50%',
            background: accent,
            boxShadow: `0 0 8px ${accent}`,
          }} />
          <span style={{
            fontFamily: fonts.body,
            fontSize: 13 * scale,
            fontWeight: 800,
            letterSpacing: '0.16em',
            color: accent,
            textTransform: 'uppercase',
          }}>
            {kicker}
          </span>
        </div>

        {/* Highlighted text phrase */}
        <div style={{
          position: 'relative',
          display: 'inline-block',
          fontFamily: fonts.display,
          fontSize: 34 * scale,
          fontWeight: 900,
          lineHeight: 1.15,
          letterSpacing: '-0.02em',
          color: foreground,
          marginBottom: subtext ? 8 * scale : 0,
          textTransform: 'uppercase',
          textShadow: '0 3px 12px rgba(0,0,0,0.7)',
        }}>
          <span style={{ position: 'relative', zIndex: 2 }}>{text}</span>
          {/* Animated kinetic marker highlight underneath */}
          <span style={{
            position: 'absolute',
            left: 0,
            bottom: '2px',
            width: `${highlightWipe * 100}%`,
            height: `${12 * scale}px`,
            background: `linear-gradient(90deg, ${rgba(accent, 0.55)}, ${rgba(accent2, 0.35)})`,
            borderRadius: 3 * scale,
            zIndex: 1,
            pointerEvents: 'none',
          }} />
        </div>

        {/* Subtext / Citation */}
        {subtext && (
          <div style={{
            fontFamily: fonts.body,
            fontSize: 15 * scale,
            fontWeight: 600,
            color: rgba(foreground, 0.78),
            letterSpacing: '0.02em',
            marginTop: 4 * scale,
          }}>
            {subtext}
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
}
