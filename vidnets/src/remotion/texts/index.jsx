import React from 'react';
import { interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { rgba } from '../theme';

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/** Typewriter text reveal – character by character */
export function TypeOn({
  text,
  theme,
  fontSize = 32,
  speed = 2.5,
  delay = 0,
  color = null,
  cursor = true,
  maxWidth = '90%',
}) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - delay);
  const chars = Math.floor(local * speed);
  const display = String(text || '').slice(0, Math.min(chars, text.length));
  const showCursor = cursor && chars < (text || '').length && Math.floor(frame / 12) % 2 === 0;

  return (
    <div style={{ position: 'relative', maxWidth, fontFamily: theme.font_family, fontSize, color: color || theme.palette.primary, lineHeight: 1.35 }}>
      {display}
      {showCursor && <span style={{ color: theme.palette.accent, fontWeight: 100 }}>|</span>}
    </div>
  );
}

/** Word-by-word reveal with stagger */
export function WordReveal({
  text,
  theme,
  fontSize = 64,
  fontWeight = 900,
  stagger = 4,
  delay = 0,
  color = null,
  maxWidth = '85%',
  animation = 'fade',
  uppercase = true,
}) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - delay);
  const display = uppercase ? String(text || '').toUpperCase() : String(text || '');
  const chunks = display.split(' ');

  const getStyle = (i) => {
    const t = Math.max(0, local - i * stagger);
    const enter = spring({ frame: t, fps, config: { damping: 14, mass: 0.5, stiffness: 140 } });
    switch (animation) {
      case 'scale':
        return { transform: `scale(${0.2 + enter * 0.8})`, opacity: enter };
      case 'slide':
        return { transform: `translateY(${(1 - enter) * 40}px)`, opacity: enter };
      default:
        return { opacity: clamp(t / 8) };
    }
  };

  return (
    <div style={{
      display: 'flex', flexWrap: 'wrap', justifyContent: 'center',
      maxWidth, gap: '0.3em',
    }}>
      {chunks.map((word, i) => (
        <span key={i} style={{
          fontFamily: theme.display_font, fontSize, fontWeight, lineHeight: 0.95,
          color: color || theme.palette.primary, letterSpacing: -1.5,
          textShadow: `0 3px 10px ${rgba(theme.palette.background, 0.5)}`,
          ...getStyle(i),
        }}>
          {word}
        </span>
      ))}
    </div>
  );
}

/** Scale-pop text entry */
export function ScalePop({
  text,
  theme,
  fontSize = 88,
  fontWeight = 950,
  delay = 0,
  color = null,
  shadow = true,
  maxWidth = '85%',
}) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - delay);
  const scale = spring({ frame: local, fps, config: { damping: 10, mass: 0.45, stiffness: 160 } });
  const overshoot = interpolate(local, [0, 16, 24], [0.3, 1.08, 1.0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });

  return (
    <div style={{
      transform: `scale(${overshoot})`, maxWidth,
      fontFamily: theme.display_font, fontSize, fontWeight, lineHeight: 0.92,
      color: color || theme.palette.primary, letterSpacing: -2,
      textAlign: 'center',
      textShadow: shadow ? `0 8px 24px ${rgba(theme.palette.background, 0.7)}` : 'none',
    }}>
      {String(text || '')}
    </div>
  );
}

/** Fade-cascade: line by line staggered reveal */
export function FadeCascade({
  lines,
  theme,
  fontSize = 30,
  stagger = 5,
  delay = 0,
  color = null,
  maxWidth = '80%',
  align = 'left',
}) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const items = Array.isArray(lines) ? lines : String(lines || '').split('\n');

  return (
    <div style={{ maxWidth, textAlign: align }}>
      {items.map((line, i) => {
        const local = Math.max(0, frame - delay - i * stagger);
        const fade = interpolate(local, [0, 10], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
        const slide = spring({ frame: local, fps, config: { damping: 20, mass: 0.7, stiffness: 100 } });
        return (
          <div key={i} style={{
            fontFamily: theme.font_family, fontSize, lineHeight: 1.5,
            color: color || theme.palette.primary,
            opacity: fade, transform: `translateX(${(1 - slide) * -16}px)`,
          }}>
            {line}
          </div>
        );
      })}
    </div>
  );
}

/** Glitch-style text with brief color offset */
export function GlitchText({
  text,
  theme,
  fontSize = 72,
  delay = 0,
  intensity = 0.5,
  maxWidth = '85%',
}) {
  const frame = useCurrentFrame();
  const local = Math.max(0, frame - delay);
  const phase = local % 30 < 3 ? 1 : 0;
  const offset = phase * Math.sin(local * 0.7) * intensity * 12;

  const r = null;
  return (
    <div style={{ position: 'relative', maxWidth }}>
      {/* Cyan ghost */}
      <span style={{
        position: 'absolute', left: offset, top: 0,
        fontFamily: theme.display_font, fontSize, fontWeight: 900, lineHeight: 0.9,
        color: phase ? '#0ff' : 'transparent', opacity: phase * 0.6,
        textShadow: 'none', letterSpacing: -1.5,
      }}>
        {String(text || '')}
      </span>
      {/* Magenta ghost */}
      <span style={{
        position: 'absolute', left: -offset, top: 0,
        fontFamily: theme.display_font, fontSize, fontWeight: 900, lineHeight: 0.9,
        color: phase ? '#f0f' : 'transparent', opacity: phase * 0.6,
        textShadow: 'none', letterSpacing: -1.5,
      }}>
        {String(text || '')}
      </span>
      {/* Main text */}
      <span style={{
        position: 'relative',
        fontFamily: theme.display_font, fontSize, fontWeight: 900, lineHeight: 0.9,
        color: theme.palette.primary, letterSpacing: -1.5,
        textShadow: `0 3px 12px ${rgba(theme.palette.background, 0.6)}`,
      }}>
        {String(text || '')}
      </span>
    </div>
  );
}
