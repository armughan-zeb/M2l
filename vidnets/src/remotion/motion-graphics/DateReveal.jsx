import React from 'react';
import { spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { rgba } from '../theme';

export function DateReveal({
  label,
  kicker,
  theme,
  variant = 'simple',
  delay = 0,
}) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - delay);

  const enter = spring({ frame: local, fps, config: { damping: 18, mass: 0.7, stiffness: 110 } });
  const lineGrow = spring({ frame: Math.max(0, local - 8), fps, config: { damping: 16, mass: 0.5, stiffness: 140 } });
  const dotScale = spring({ frame: Math.max(0, local - 14), fps, config: { damping: 10, mass: 0.4, stiffness: 160 } });

  if (variant === 'minimal') {
    return (
      <div style={{
        display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 20,
        transform: `translateY(${(1 - enter) * 35}px)`, opacity: Math.min(1, local / 12),
      }}>
        {kicker && <span style={{ fontFamily: theme.font_family, fontSize: 22, fontWeight: 700, color: theme.palette.accent, letterSpacing: 4, textTransform: 'uppercase' }}>{kicker}</span>}
        <span style={{ fontFamily: theme.display_font, fontSize: 110, fontWeight: 900, color: theme.palette.primary, letterSpacing: -3, lineHeight: 0.9 }}>{label}</span>
        <div style={{ width: `${lineGrow * 240}px`, height: 6, background: theme.palette.accent }} />
      </div>
    );
  }

  // Default: cinematic timeline-style
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16,
      padding: '6%', position: 'relative',
    }}>
      {kicker && (
        <span style={{
          fontFamily: theme.font_family, fontSize: 19, fontWeight: 800,
          color: theme.palette.accent, letterSpacing: 3.6, textTransform: 'uppercase',
          opacity: Math.min(1, local / 10),
          transform: `translateY(${(1 - enter) * -18}px)`,
        }}>
          {kicker}
        </span>
      )}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 24,
        transform: `scale(${0.85 + enter * 0.15})`,
      }}>
        <div style={{
          width: `${lineGrow * 180}px`, height: 2,
          background: rgba(theme.palette.primary, 0.3),
        }} />
        <div style={{
          width: 14, height: 14, borderRadius: '50%',
          background: theme.palette.accent,
          transform: `scale(${dotScale})`,
          boxShadow: `0 0 0 ${dotScale * 8}px ${rgba(theme.palette.accent, 0.2)}`,
        }} />
        <div style={{
          width: `${lineGrow * 180}px`, height: 2,
          background: rgba(theme.palette.primary, 0.3),
        }} />
      </div>
      <span style={{
        fontFamily: theme.display_font, fontSize: 96, fontWeight: 900,
        color: theme.palette.primary, letterSpacing: -2.5, lineHeight: 0.92,
        textShadow: `0 3px 16px ${rgba(theme.palette.background, 0.6)}`,
        opacity: Math.min(1, Math.max(0, (local - 10) / 14)),
      }}>
        {label}
      </span>
    </div>
  );
}
