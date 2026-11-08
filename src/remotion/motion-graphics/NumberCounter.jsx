import React from 'react';
import { interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { rgba } from '../theme';

const EASE_OUT_EXPO = (t) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t));

export function NumberCounter({
  value,
  theme,
  prefix = '',
  suffix = '',
  fontSize = 140,
  fontWeight = 900,
  letterSpacing = -5,
  color = null,
  label = null,
  labelSize = 28,
  showAccentBar = true,
  decimals = 0,
  countUp = true,
  delay = 0,
  duration = 30,
}) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - delay);

  const raw = String(value ?? '0').replace(/[^0-9.]/g, '');
  const num = parseFloat(raw) || 0;

  const progress = Math.min(1, local / Math.max(1, duration));
  const easedProgress = countUp ? EASE_OUT_EXPO(progress) : spring({
    frame: local, fps,
    config: { damping: 16, mass: 0.6, stiffness: 130 },
  });

  const display = countUp
    ? (Number.isInteger(num) ? Math.round(num * easedProgress).toLocaleString()
      : (num * easedProgress).toFixed(decimals))
    : num.toLocaleString(undefined, decimals > 0 ? { minimumFractionDigits: decimals, maximumFractionDigits: decimals } : undefined);

  const fill = color || theme.palette.accent;
  const scale = spring({ frame: local, fps, config: { damping: 12, mass: 0.5, stiffness: 150 } });

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      padding: '0 8%', gap: 8,
    }}>
      {label && (
        <span style={{
          fontFamily: theme.font_family, fontSize: labelSize, fontWeight: 800,
          color: theme.palette.accent, letterSpacing: 3.2, textTransform: 'uppercase',
          opacity: interpolate(local, [0, 12], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }),
          transform: `translateY(${(1 - scale) * 20}px)`,
        }}>
          {label}
        </span>
      )}
      <span style={{
        fontFamily: theme.display_font, fontSize, fontWeight, letterSpacing,
        color: fill, lineHeight: 0.88,
        transform: `scale(${0.82 + scale * 0.18})`,
        textShadow: `0 6px 24px ${rgba(theme.palette.background, 0.6)}`,
      }}>
        {prefix}{display}{suffix}
      </span>
      {showAccentBar && (
        <div style={{
          width: `${Math.min(300, scale * 280 + 20)}px`,
          height: 10, background: theme.palette.accent, marginTop: 14,
          transform: `scaleX(${scale})`,
          transformOrigin: 'center',
        }} />
      )}
    </div>
  );
}
