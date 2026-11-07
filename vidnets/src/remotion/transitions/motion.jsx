import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { rgba } from '../theme';

/**
 * SlideReveal – slide the B scene in over A, or push A out.
 * direction: 'left' | 'right' | 'up' | 'down'
 */
export function SlideReveal({
  children,
  theme,
  durationFrames = 18,
  direction = 'left',
  mode = 'push',
  style = {},
}) {
  const frame = useCurrentFrame();
  const enter = spring({ frame, fps: 30, config: { damping: 18, mass: 0.6, stiffness: 140 } });

  const getTransform = () => {
    const pct = (1 - enter) * 100;
    switch (direction) {
      case 'left': return `translateX(${mode === 'push' ? -pct : pct}%)`;
      case 'right': return `translateX(${mode === 'push' ? pct : -pct}%)`;
      case 'up': return `translateY(${mode === 'push' ? -pct : pct}%)`;
      case 'down': return `translateY(${mode === 'push' ? pct : -pct}%)`;
      default: return `translateX(${-pct}%)`;
    }
  };

  return (
    <AbsoluteFill style={{
      transform: getTransform(),
      boxShadow: mode === 'push' ? `0 0 60px ${rgba('#000', 0.4)}` : 'none',
      ...style,
    }}>
      {children}
    </AbsoluteFill>
  );
}

/**
 * ZoomBurst – B scene zooms in from center with a dark flash.
 */
export function ZoomBurst({
  children,
  theme,
  durationFrames = 16,
}) {
  const frame = useCurrentFrame();
  const scale = spring({ frame, fps: 30, config: { damping: 10, mass: 0.4, stiffness: 160 } });
  const flash = interpolate(frame, [0, 4, 8], [0, 0.9, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });

  return (
    <AbsoluteFill style={{ overflow: 'hidden' }}>
      <div style={{
        position: 'absolute', inset: 0,
        transform: `scale(${0.4 + scale * 0.6})`,
        opacity: spring({ frame, fps: 30, config: { damping: 14, mass: 0.5, stiffness: 130 } }),
      }}>
        {children}
      </div>
      {flash > 0 && (
        <AbsoluteFill style={{
          background: '#fff',
          opacity: flash,
          pointerEvents: 'none',
        }} />
      )}
    </AbsoluteFill>
  );
}

/**
 * WipeTransition – directional wipe reveals B scene.
 */
export function WipeTransition({
  children,
  theme,
  durationFrames = 14,
  direction = 'left',
}) {
  const frame = useCurrentFrame();
  const progress = spring({ frame, fps: 30, config: { damping: 16, mass: 0.5, stiffness: 140 } });
  const pct = progress * 100;
  const accentWidth = 3;

  const clipPath = () => {
    switch (direction) {
      case 'left': return `inset(0 ${100 - pct}% 0 0)`;
      case 'right': return `inset(0 0 0 ${100 - pct}%)`;
      case 'up': return `inset(${100 - pct}% 0 0 0)`;
      case 'down': return `inset(0 0 ${100 - pct}% 0)`;
      default: return `inset(0 ${100 - pct}% 0 0)`;
    }
  };

  const accentStyle = {
    position: 'absolute',
    background: theme.palette.accent,
    zIndex: 10,
  };

  let accent = null;
  if (direction === 'left') {
    accent = <div style={{ ...accentStyle, right: `${100 - pct}%`, top: 0, bottom: 0, width: accentWidth }} />;
  } else if (direction === 'right') {
    accent = <div style={{ ...accentStyle, left: `${100 - pct}%`, top: 0, bottom: 0, width: accentWidth }} />;
  } else if (direction === 'up') {
    accent = <div style={{ ...accentStyle, bottom: `${100 - pct}%`, left: 0, right: 0, height: accentWidth }} />;
  } else if (direction === 'down') {
    accent = <div style={{ ...accentStyle, top: `${100 - pct}%`, left: 0, right: 0, height: accentWidth }} />;
  }

  return (
    <AbsoluteFill style={{ clipPath: clipPath(), overflow: 'hidden' }}>
      {children}
      {accent}
    </AbsoluteFill>
  );
}

/**
 * DipBlack – fade through black/white.
 */
export function DipTransition({
  children,
  theme,
  durationFrames = 12,
  color = null,
}) {
  const frame = useCurrentFrame();
  const mid = durationFrames / 2;
  const fadeIn = spring({ frame: Math.max(0, frame - mid), fps: 30, config: { damping: 18, mass: 0.6, stiffness: 120 } });

  return (
    <div style={{ opacity: fadeIn, width: '100%', height: '100%' }}>
      {children}
    </div>
  );
}
