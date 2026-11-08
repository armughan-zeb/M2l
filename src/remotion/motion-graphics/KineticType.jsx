import React from 'react';
import { interpolate, spring } from 'remotion';
import { useMotion, EASING } from './useMotion';
import { rgba } from '../theme';

/**
 * KineticType – word-by-word or character-by-character typography animation.
 * MrBeast/Vox style: words slam in with scale, slide, or bounce.
 */
export function KineticType({
  text,
  theme,
  mode = 'word',
  animation = 'scaleIn',
  fontSize = 72,
  fontWeight = 900,
  lineHeight = 1.0,
  letterSpacing = -2.4,
  color = null,
  maxWidth = '85%',
  delay = 0,
  staggerFrames = 4,
  align = 'center',
  shadow = true,
  uppercase = true,
  // Facts such as "$100 MILLION" and "123 OF 160" must arrive as one
  // readable unit. Word staggering is expressive for headlines, but it makes
  // factual values look fragmented or incomplete for several frames.
  atomic = false,
}) {
  const { frame } = useMotion({ delay });
  const display = (uppercase ? String(text || '').toUpperCase() : String(text || '')).trim();
  const chunks = atomic ? [display] : (mode === 'char' ? display.split('') : display.split(' '));
  const fill = color || theme.palette.primary;

  const getTransform = (i) => {
    const t = Math.max(0, frame - i * staggerFrames);
    const enter = spring({ frame: t, fps: 30, config: EASING.snappy });
    switch (animation) {
      case 'blurToSharp':
        return `scale(${0.8 + enter * 0.2})`;
      case 'maskedSlide':
        return `translateY(${(1 - enter) * 100}%)`;
      case 'scaleIn':
        return `scale(${0.3 + enter * 0.7})`;
      case 'slideUp':
        return `translateY(${(1 - enter) * 60}px)`;
      case 'slideLeft':
        return `translateX(${(1 - enter) * -80}px)`;
      case 'bounce':
        return `scale(${0.5 + enter * 0.5}) translateY(${(1 - enter) * -30}px)`;
      case 'rotateIn':
        return `rotate(${(1 - enter) * 15}deg) scale(${0.4 + enter * 0.6})`;
      default:
        return `scale(${0.3 + enter * 0.7})`;
    }
  };
  const getOpacity = (i) => {
    const t = Math.max(0, frame - i * staggerFrames);
    return interpolate(t, [0, staggerFrames * 2], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  };

  return (
    <div style={{
      display: 'flex', flexWrap: 'wrap', justifyContent: align === 'center' ? 'center' : 'flex-start',
      maxWidth, gap: atomic || mode === 'char' ? 0 : '0.25em',
    }}>
      {chunks.map((chunk, i) => (
        <span key={i} style={{
          display: 'inline-block',
          fontFamily: theme.display_font,
          fontSize, fontWeight, lineHeight, letterSpacing,
          color: fill,
          transform: getTransform(i),
          opacity: getOpacity(i),
          textShadow: shadow && animation !== 'slideUp'
            ? `0 4px 12px ${rgba(theme.palette.background, 0.75)}, 2px 3px 0 ${rgba(theme.palette.background, 0.5)}`
            : 'none',
          marginRight: !atomic && mode === 'word' ? '0.28em' : '0',
        }}>
          {chunk}
        </span>
      ))}
    </div>
  );
}

