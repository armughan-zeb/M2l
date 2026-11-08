import React from 'react';
import { interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';

function clamp(value, min = 0, max = 1) {
  return Math.min(max, Math.max(min, Number(value) || 0));
}

/**
 * 1. Reveal: Directional clip-path mask reveal with smooth spring timing.
 * Directions: 'left_to_right', 'right_to_left', 'bottom_to_top', 'center_out'.
 */
export function Reveal({
  children,
  direction = 'left_to_right',
  delay = 0,
  durationFrames = 22,
  style = {},
}) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const localFrame = Math.max(0, frame - delay);
  const progress = spring({
    frame: localFrame,
    fps,
    config: { damping: 20, mass: 0.5, stiffness: 140 },
  });

  const p = clamp(progress);
  let clipPath = 'none';
  if (direction === 'left_to_right') {
    clipPath = `inset(0 ${(1 - p) * 100}% 0 0)`;
  } else if (direction === 'right_to_left') {
    clipPath = `inset(0 0 0 ${(1 - p) * 100}%)`;
  } else if (direction === 'bottom_to_top') {
    clipPath = `inset(${(1 - p) * 100}% 0 0 0)`;
  } else if (direction === 'center_out') {
    const half = (1 - p) * 50;
    clipPath = `inset(${half}% ${half}% ${half}% ${half}%)`;
  }

  return (
    <div
      style={{
        ...style,
        clipPath,
        WebkitClipPath: clipPath,
        opacity: clamp(p * 1.8),
      }}
    >
      {children}
    </div>
  );
}

/**
 * 2. Stagger: Staggered sequential entrance for child elements or text tokens.
 */
export function Stagger({
  children,
  staggerFrames = 4,
  baseDelay = 0,
  motion = 'slide_up',
  style = {},
}) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const items = React.Children.toArray(children);

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', ...style }}>
      {items.map((child, index) => {
        const itemDelay = baseDelay + index * staggerFrames;
        const localFrame = Math.max(0, frame - itemDelay);
        const enter = spring({
          frame: localFrame,
          fps,
          config: { damping: 18, mass: 0.45, stiffness: 150 },
        });
        const p = clamp(enter);

        let transform = 'none';
        if (motion === 'slide_up') {
          transform = `translateY(${(1 - p) * 24}px)`;
        } else if (motion === 'slide_right') {
          transform = `translateX(${(1 - p) * -28}px)`;
        } else if (motion === 'pop') {
          transform = `scale(${0.85 + p * 0.15})`;
        }

        return (
          <div
            key={index}
            style={{
              opacity: p,
              transform,
              display: 'inline-block',
            }}
          >
            {child}
          </div>
        );
      })}
    </div>
  );
}

/**
 * 3. ScaleSettle: Scale-settle spring entrance with cinematic damping.
 * Overshoots slightly then settles gracefully to scale 1.0.
 */
export function ScaleSettle({
  children,
  initialScale = 0.92,
  delay = 0,
  damping = 16,
  stiffness = 140,
  style = {},
}) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const localFrame = Math.max(0, frame - delay);
  const spr = spring({
    frame: localFrame,
    fps,
    config: { damping, mass: 0.5, stiffness },
  });
  const scale = initialScale + (1 - initialScale) * spr;
  const opacity = clamp(spr * 1.5);

  return (
    <div
      style={{
        ...style,
        transform: `scale(${scale.toFixed(4)})`,
        transformOrigin: style.transformOrigin || 'center center',
        opacity,
      }}
    >
      {children}
    </div>
  );
}

/**
 * 4. TrackingChange: Dynamic letter-spacing interpolation for bold statements.
 */
export function TrackingChange({
  children,
  fromEm = 0.18,
  toEm = 0.02,
  delay = 0,
  durationFrames = 26,
  style = {},
}) {
  const frame = useCurrentFrame();
  const localFrame = Math.max(0, frame - delay);
  const progress = clamp(localFrame / Math.max(1, durationFrames));
  const letterSpacing = `${(fromEm + (toEm - fromEm) * progress).toFixed(3)}em`;
  const opacity = clamp(localFrame / 10);

  return (
    <span
      style={{
        ...style,
        letterSpacing,
        opacity,
        display: 'inline-block',
      }}
    >
      {children}
    </span>
  );
}

/**
 * 5. LineDraw: Precision rule line or SVG stroke drawn smoothly from 0% to 100%.
 */
export function LineDraw({
  width = 160,
  height = 3,
  color = '#f2c94c',
  delay = 4,
  durationFrames = 20,
  centered = false,
  style = {},
}) {
  const frame = useCurrentFrame();
  const localFrame = Math.max(0, frame - delay);
  const progress = clamp(localFrame / Math.max(1, durationFrames));
  const currentWidth = width * progress;

  return (
    <div
      style={{
        width: `${currentWidth.toFixed(1)}px`,
        maxWidth: '100%',
        height: `${height}px`,
        background: `linear-gradient(90deg, ${color}, rgba(255,255,255,0.08))`,
        transformOrigin: centered ? 'center' : 'left',
        opacity: clamp(progress * 2),
        ...style,
      }}
    />
  );
}

/**
 * 6. MaskedMediaReveal: Footage or photographic cutout reveal with subtle push.
 */
export function MaskedMediaReveal({
  children,
  delay = 0,
  shape = 'rounded_rect', // 'circle', 'rounded_rect', 'diagonal_cut'
  style = {},
}) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const localFrame = Math.max(0, frame - delay);
  const spr = spring({
    frame: localFrame,
    fps,
    config: { damping: 19, mass: 0.55, stiffness: 130 },
  });
  const p = clamp(spr);

  let clipPath = 'none';
  if (shape === 'rounded_rect') {
    const inset = (1 - p) * 15;
    clipPath = `inset(${inset}% round ${Math.round(p * 8)}px)`;
  } else if (shape === 'circle') {
    clipPath = `circle(${p * 50}% at 50% 50%)`;
  } else if (shape === 'diagonal_cut') {
    clipPath = `polygon(0 0, ${p * 100}% 0, ${(p * 100) - 10}% 100%, 0 100%)`;
  }

  return (
    <div
      style={{
        ...style,
        clipPath,
        WebkitClipPath: clipPath,
        opacity: clamp(p * 1.6),
        transform: `scale(${0.96 + p * 0.04})`,
      }}
    >
      {children}
    </div>
  );
}

/**
 * 7. Counter: Grounded numeric counter interpolating from start to end with formatting.
 */
export function Counter({
  start = 0,
  end = 100,
  prefix = '',
  suffix = '',
  decimals = 0,
  delay = 6,
  durationFrames = 34,
  fontFamily = 'JetBrains Mono, monospace',
  fontSize = 88,
  color = '#f2c94c',
  style = {},
}) {
  const frame = useCurrentFrame();
  const localFrame = Math.max(0, frame - delay);
  const progress = clamp(localFrame / Math.max(1, durationFrames));
  // Ease-out cubic for realistic metric counter
  const easeOut = 1 - Math.pow(1 - progress, 3);
  const currentValue = start + (end - start) * easeOut;
  const formatted = decimals > 0
    ? currentValue.toFixed(decimals)
    : Math.round(currentValue).toLocaleString();

  return (
    <span
      style={{
        fontFamily,
        fontSize: `${fontSize}px`,
        fontWeight: 900,
        color,
        letterSpacing: '-0.05em',
        lineHeight: 0.9,
        display: 'inline-block',
        ...style,
      }}
    >
      {prefix}{formatted}{suffix}
    </span>
  );
}

/**
 * 8. PathTravel: An animated beacon or vector point traveling along a defined trajectory.
 */
export function PathTravel({
  points = [{ x: 10, y: 10 }, { x: 90, y: 90 }],
  delay = 5,
  durationFrames = 40,
  color = '#f2c94c',
  showTrail = true,
  viewBox = '0 0 100 100',
  width = '100%',
  height = '100%',
  style = {},
}) {
  const frame = useCurrentFrame();
  const localFrame = Math.max(0, frame - delay);
  const progress = clamp(localFrame / Math.max(1, durationFrames));

  if (!points || points.length < 2) return null;
  const p0 = points[0];
  const p1 = points[1];
  const currX = p0.x + (p1.x - p0.x) * progress;
  const currY = p0.y + (p1.y - p0.y) * progress;

  return (
    <svg viewBox={viewBox} width={width} height={height} style={{ overflow: 'visible', ...style }}>
      {showTrail && (
        <line
          x1={p0.x}
          y1={p0.y}
          x2={currX}
          y2={currY}
          stroke={color}
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeDasharray="2 2"
        />
      )}
      <circle cx={currX} cy={currY} r="3" fill="#0b0f14" stroke={color} strokeWidth="1.4" />
      <circle
        cx={currX}
        cy={currY}
        r={3 + (1 - progress) * 6}
        fill="none"
        stroke={color}
        strokeWidth="0.6"
        opacity={1 - progress}
      />
    </svg>
  );
}
