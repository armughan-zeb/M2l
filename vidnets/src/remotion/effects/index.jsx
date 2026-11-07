/* Cinematic effects — particles, camera shake, light leaks, film grain. All CPU-safe (CSS/SVG only). */
import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { rgba } from '../theme';

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/** Floating particles — documentary "investigation" atmosphere */
export function Particles({ count = 20, theme, intensity = 1, color = null }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const fill = color || theme.palette.accent;

  return (
    <AbsoluteFill style={{ pointerEvents: 'none', overflow: 'hidden' }}>
      {Array.from({ length: count }, (_, i) => {
        const seed = i * 137.508;
        const x = ((seed * 7.3) % 100);
        const y = ((seed * 11.7) % 100);
        const size = 2 + (seed % 4);
        const speed = 0.3 + ((seed * 3.1) % 1);
        const driftX = Math.sin(frame * 0.02 * speed + seed) * 12 * intensity;
        const driftY = (frame * 0.3 * speed + seed * 10) % 140 - 20;
        const opacity = 0.15 + Math.sin(frame * 0.05 + seed) * 0.1 * intensity;

        return (
          <div key={i} style={{
            position: 'absolute',
            left: `${x + driftX}%`,
            top: `${driftY}%`,
            width: size, height: size,
            borderRadius: '50%',
            background: rgba(fill, opacity),
            boxShadow: `0 0 ${size * 2}px ${rgba(fill, opacity * 0.5)}`,
          }} />
        );
      })}
    </AbsoluteFill>
  );
}

/** Camera shake — tension/horror/action scenes */
export function CameraShake({ children, intensity = 0, frequency = 1.4 }) {
  const frame = useCurrentFrame();
  if (intensity <= 0) return children;

  const x = Math.sin(frame * frequency) * intensity * 3;
  const y = Math.cos(frame * frequency * 1.3) * intensity * 2;
  const rotation = Math.sin(frame * frequency * 0.7) * intensity * 0.5;

  return (
    <div style={{
      transform: `translate(${x}px, ${y}px) rotate(${rotation}deg)`,
      width: '100%', height: '100%',
    }}>
      {children}
    </div>
  );
}

/** Light leak / flare — cinematic bloom effect */
export function LightLeak({ theme, intensity = 0.5, delay = 0 }) {
  const frame = useCurrentFrame();
  const local = Math.max(0, frame - delay);
  const opacity = Math.sin(local * 0.08) * 0.15 * intensity + 0.05 * intensity;
  const pos = (local * 0.3) % 120 - 10;

  return (
    <AbsoluteFill style={{ pointerEvents: 'none', opacity: clamp(opacity, 0, 0.6) }}>
      <div style={{
        position: 'absolute',
        left: `${pos}%`, top: '-10%',
        width: '40%', height: '120%',
        background: `radial-gradient(ellipse at center, ${rgba(theme.palette.accent, 0.4 * intensity)}, transparent 70%)`,
        transform: 'rotate(-15deg)',
      }} />
    </AbsoluteFill>
  );
}

/** Film grain — adds texture */
export function FilmGrain({ intensity = 0.04 }) {
  return (
    <AbsoluteFill style={{
      pointerEvents: 'none', opacity: intensity, mixBlendMode: 'overlay',
      backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.5'/%3E%3C/svg%3E")`,
      backgroundSize: '128px 128px',
    }} />
  );
}

/** Vignette — darkens edges for focus */
export function Vignette({ intensity = 0.5 }) {
  return (
    <AbsoluteFill style={{
      pointerEvents: 'none',
      background: `radial-gradient(ellipse at center, transparent 50%, rgba(0,0,0,${intensity * 0.7}) 100%)`,
    }} />
  );
}

/** ColorGradeFilter — full-screen CSS filter layer for color grading */
export function ColorGradeFilter({ preset = 'documentary', intensity = 0.12 }) {
  const presets = {
    warm_cinematic: 'sepia(0.06) hue-rotate(-5deg)',
    cool_modern: 'hue-rotate(5deg) saturate(1.1)',
    noir: 'saturate(0.7) contrast(1.2)',
    golden_hour: 'sepia(0.12) saturate(1.15) brightness(1.04)',
    moody_teal: 'hue-rotate(10deg) saturate(0.9) contrast(1.08)',
    documentary: 'contrast(1.06) saturate(1.08)'
  };
  
  const filterStyle = presets[preset] || presets.documentary;

  return (
    <AbsoluteFill style={{
      pointerEvents: 'none',
      backdropFilter: filterStyle,
      WebkitBackdropFilter: filterStyle,
      opacity: intensity,
      mixBlendMode: 'overlay',
    }} />
  );
}

/** GlowAccent — Subtle ambient glow effect */
export function GlowAccent({ color = '#ffffff', position = 'center', intensity = 0.1 }) {
  const frame = useCurrentFrame();
  const pulse = Math.sin(frame * 0.05) * 0.05;
  const currentOpacity = clamp(intensity + pulse, 0, 0.15);
  
  const getGradientPosition = () => {
    switch (position) {
      case 'top-left': return '0% 0%';
      case 'top-right': return '100% 0%';
      case 'bottom-left': return '0% 100%';
      case 'bottom-right': return '100% 100%';
      case 'center': default: return '50% 50%';
    }
  };

  return (
    <AbsoluteFill style={{ pointerEvents: 'none' }}>
      <div style={{
        width: '100%', height: '100%',
        background: `radial-gradient(circle at ${getGradientPosition()}, ${rgba(color, currentOpacity)}, transparent 60%)`,
        mixBlendMode: 'screen'
      }} />
    </AbsoluteFill>
  );
}

/** LetterboxBars — Cinematic letterbox bars */
export function LetterboxBars({ ratio = 2.35, color = '#000000', animated = true }) {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  
  const targetHeight = width / ratio;
  const barHeight = Math.max(0, (height - targetHeight) / 2);
  const barHeightPercent = (barHeight / height) * 100;
  
  const animProgress = animated ? spring({
    frame,
    fps,
    config: { damping: 14, stiffness: 100 }
  }) : 1;
  
  const currentBarHeight = barHeightPercent * animProgress;
  
  if (barHeight <= 0) return null;

  return (
    <AbsoluteFill style={{ pointerEvents: 'none', flexDirection: 'column', justifyContent: 'space-between' }}>
      <div style={{ width: '100%', height: `${currentBarHeight}%`, backgroundColor: color }} />
      <div style={{ width: '100%', height: `${currentBarHeight}%`, backgroundColor: color }} />
    </AbsoluteFill>
  );
}

/** EdgeLight — Subtle edge lighting effect */
export function EdgeLight({ theme, side = 'left', intensity = 0.3 }) {
  const color = theme?.palette?.accent || '#ffffff';
  
  let gradient = '';
  switch (side) {
    case 'left': gradient = `linear-gradient(to right, ${rgba(color, intensity)}, transparent 30%)`; break;
    case 'right': gradient = `linear-gradient(to left, ${rgba(color, intensity)}, transparent 30%)`; break;
    case 'top': gradient = `linear-gradient(to bottom, ${rgba(color, intensity)}, transparent 30%)`; break;
    case 'bottom': gradient = `linear-gradient(to top, ${rgba(color, intensity)}, transparent 30%)`; break;
    default: gradient = `linear-gradient(to right, ${rgba(color, intensity)}, transparent 30%)`; break;
  }

  return (
    <AbsoluteFill style={{
      pointerEvents: 'none',
      background: gradient,
      mixBlendMode: 'screen',
      opacity: 0.8
    }} />
  );
}
