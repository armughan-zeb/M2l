import React from 'react';
import { spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { rgba } from '../theme';

export function MotionCard({
  children,
  theme,
  width = 'auto',
  maxWidth = '74%',
  animation = 'slideUp',
  delay = 0,
  duration = 18,
  showAccent = true,
  accentSide = 'left',
  padding = '28px 34px',
  background = null,
}) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - delay);

  const enter = spring({ frame: local, fps, config: { damping: 16, mass: 0.6, stiffness: 120 } });

  const getTransform = () => {
    switch (animation) {
      case 'slideUp': return `translateY(${(1 - enter) * 60}px)`;
      case 'slideLeft': return `translateX(${(1 - enter) * -120}px)`;
      case 'scaleIn': return `scale(${0.7 + enter * 0.3})`;
      case 'slideRight': return `translateX(${(1 - enter) * 120}px)`;
      default: return `translateY(${(1 - enter) * 50}px)`;
    }
  };

  const bg = background || rgba(theme.palette.background, 0.88);
  const opacity = Math.min(1, local / Math.max(1, duration * 0.6));

  return (
    <div style={{
      alignSelf: animation.includes('Left') ? 'flex-start' : animation.includes('Right') ? 'flex-end' : 'center',
      maxWidth, width, padding,
      transform: getTransform(),
      opacity,
      background: bg,
      borderRadius: 8,
      position: 'relative',
      boxShadow: `0 24px 56px ${rgba(theme.palette.background, 0.45)}`,
      borderLeft: showAccent && accentSide === 'left' ? `8px solid ${theme.palette.accent}` : 'none',
      borderRight: showAccent && accentSide === 'right' ? `8px solid ${theme.palette.accent}` : 'none',
      borderTop: showAccent && accentSide === 'top' ? `6px solid ${theme.palette.accent}` : 'none',
    }}>
      {children}
    </div>
  );
}

export function AccentLine({
  theme,
  width = 240,
  height = 8,
  animation = 'grow',
  delay = 0,
  vertical = false,
  gap = 0,
}) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - delay);

  const scale = spring({ frame: local, fps, config: { damping: 16, mass: 0.5, stiffness: 140 } });
  const opacity = Math.min(1, local / 10);

  const baseStyle = {
    background: theme.palette.accent,
    opacity,
    marginTop: vertical ? 0 : gap,
    marginBottom: vertical ? 0 : gap,
  };

  if (vertical) {
    return (
      <div style={{
        ...baseStyle,
        width: height, height: `${scale * width}px`,
        borderRadius: height / 2,
        alignSelf: 'center',
        transformOrigin: 'top',
      }} />
    );
  }

  if (animation === 'wipe') {
    return (
      <div style={{ width, height, overflow: 'hidden' }}>
        <div style={{
          ...baseStyle,
          width: '100%', height: '100%',
          borderRadius: height / 2,
          transform: `translateX(${(1 - scale) * -width}px)`,
        }} />
      </div>
    );
  }

  // default: grow
  return (
    <div style={{
      ...baseStyle,
      width: `${Math.min(width, width * scale)}px`,
      height, borderRadius: height / 2,
    }} />
  );
}
