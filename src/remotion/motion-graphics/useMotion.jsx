import { interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';

const clamp = (v, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, v));

export const EASING = {
  smooth: { damping: 18, mass: 0.65, stiffness: 120 },
  snappy: { damping: 14, mass: 0.52, stiffness: 140 },
  gentle: { damping: 22, mass: 0.80, stiffness: 100 },
  bounce: { damping: 10, mass: 0.45, stiffness: 160 },
  heavy: { damping: 24, mass: 1.0, stiffness: 80 },
  // Documentary channel-specific physics
  vox: { damping: 14, mass: 0.45, stiffness: 150, overshootClamping: false },
  'vox-editorial': { damping: 14, mass: 0.45, stiffness: 150, overshootClamping: false },
  'vox-explainer': { damping: 14, mass: 0.45, stiffness: 150, overshootClamping: false },
  magnates: { damping: 18, mass: 0.90, stiffness: 110, overshootClamping: false },
  'magnates-noir': { damping: 18, mass: 0.90, stiffness: 110, overshootClamping: false },
  rathee: { damping: 16, mass: 0.60, stiffness: 130, overshootClamping: true },
  'dhruv-clarity': { damping: 16, mass: 0.60, stiffness: 130, overshootClamping: true },
  netflix: { damping: 26, mass: 1.20, stiffness: 75, overshootClamping: true },
  'netflix-prestige': { damping: 26, mass: 1.20, stiffness: 75, overshootClamping: true },
  mrbeast: { damping: 7, mass: 0.30, stiffness: 220, overshootClamping: false },
  'mrbeast-retention': { damping: 7, mass: 0.30, stiffness: 220, overshootClamping: false },
};

export function posterizeFrame(frame, fps = 30, stepFps = 12) {
  const factor = fps / stepFps;
  return Math.floor(frame / factor) * factor;
}

export function useMotion({
  delay = 0,
  duration = 16,
  easing = 'smooth',
  genre = null,
  posterize = false,
  customConfig = null,
} = {}) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const effectiveEasing = genre && EASING[genre] ? genre : easing;
  const config = customConfig || EASING[effectiveEasing] || EASING.smooth;
  
  const rawLocal = Math.max(0, frame - delay);
  const local = posterize ? posterizeFrame(rawLocal, fps, 12) : rawLocal;

  return {
    frame,
    /** 0→1 spring-based entry */
    enter: spring({ frame: local, fps, config }),
    /** 0→1 linear progress */
    progress: clamp(local / Math.max(1, duration)),
    /** 0→1 fade */
    fade: interpolate(local, [0, duration], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }),
    /** oscillating pulse 0→1→0 (for attention effects) */
    pulse: Math.sin(local * 0.25) * 0.5 + 0.5,
    /** stagger offset for Nth child (0-indexed) */
    stagger(n = 0, gap = 4) {
      return clamp((local - n * gap) / Math.max(1, duration));
    },
    /** exit opacity: starts fading at exitFrame, goes 1 -> 0 */
    exit(exitFrame, exitDuration = 10) {
      if (frame < exitFrame) return 1;
      return interpolate(frame, [exitFrame, exitFrame + exitDuration], [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
    },
    /** exit progress: goes 0 -> 1 during exit window */
    exitProgress(exitFrame, exitDuration = 10) {
      if (frame < exitFrame) return 0;
      return interpolate(frame, [exitFrame, exitFrame + exitDuration], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
    },
    /** exit transform string */
    exitTransform(exitFrame, type = 'slideDown', exitDuration = 10) {
      if (frame < exitFrame) return '';
      const p = interpolate(frame, [exitFrame, exitFrame + exitDuration], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
      switch (type) {
        case 'slideDown':
          return `translateY(${p * 45}px)`;
        case 'slideUp':
          return `translateY(${-p * 45}px)`;
        case 'scaleDown':
          return `scale(${1 - p * 0.15})`;
        case 'blur':
          return `scale(${1 - p * 0.05})`;
        default:
          return `translateY(${p * 35}px)`;
      }
    },
  };
}
