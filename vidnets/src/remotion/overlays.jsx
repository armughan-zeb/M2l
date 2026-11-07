import React from 'react';
import { AbsoluteFill, interpolate, useCurrentFrame, spring, useVideoConfig } from 'remotion';
import { rgba } from './theme';

const clamp = (value, min = 0, max = 1) => Math.max(min, Math.min(max, value));

// Suppressed overlays - we avoid effects that degrade the clean footage or use unnecessary blur
function Dust() { return null; }
function FilmScratches() { return null; }
function Scanlines() { return null; }
function PaperFibers() { return null; }
function ProjectorFlicker() { return null; }
function SignalNoise() { return null; }
function ChromaticEdge() { return null; }
function Halation() { return null; }
function Bokeh() { return null; }
function RainGlass() { return null; }

function Grain({ intensity }) {
  // Ultra-subtle cinematic texture (re-enabled for professional documentary feel)
  return (
    <AbsoluteFill
      style={{
        backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.65' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)'/%3E%3C/svg%3E")`,
        opacity: clamp(intensity * 0.05, 0.02, 0.03),
        mixBlendMode: 'overlay',
        pointerEvents: 'none',
      }}
    />
  );
}

function LightLeak({ seed, intensity, theme }) {
  // Ultra-subtle cinematic edge warm glow only if explicitly requested, never obstructing subject
  const frame = useCurrentFrame();
  const side = seed % 2 ? 88 : 12;
  const pulse = 0.5 + Math.sin(frame / 30 + seed) * 0.1;
  return (
    <AbsoluteFill
      style={{
        opacity: clamp(intensity * 0.4, 0, 0.2) * pulse,
        mixBlendMode: 'screen',
        background: `radial-gradient(ellipse at ${side}% 4%, ${rgba(theme.palette.accent, 0.45)} 0%, transparent 40%)`,
        pointerEvents: 'none'
      }}
    />
  );
}

export function FocusOverlay({ position }) {
  // Dark semi-transparent overlay to focus attention on graphics
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  
  const entry = spring({
    frame,
    fps,
    config: {
      damping: 14,
      mass: 1,
      stiffness: 100,
    },
  });

  const getGradient = () => {
    // We use a dark overlay with low opacity (0.4) for focus
    const color = 'rgba(0, 0, 0, 0.4)'; 
    const transparent = 'rgba(0, 0, 0, 0)';
    
    switch (position) {
      case 'center':
        return `radial-gradient(circle, ${color} 0%, ${transparent} 60%)`;
      case 'top_left':
        return `radial-gradient(circle at 0% 0%, ${color} 0%, ${transparent} 70%)`;
      case 'top_right':
        return `radial-gradient(circle at 100% 0%, ${color} 0%, ${transparent} 70%)`;
      case 'bottom_left':
        return `radial-gradient(circle at 0% 100%, ${color} 0%, ${transparent} 70%)`;
      case 'bottom_right':
        return `radial-gradient(circle at 100% 100%, ${color} 0%, ${transparent} 70%)`;
      default:
        return `radial-gradient(circle, ${color} 0%, ${transparent} 60%)`;
    }
  };

  return (
    <AbsoluteFill
      style={{
        background: getGradient(),
        opacity: entry,
        pointerEvents: 'none',
      }}
    />
  );
}

export function ColorGrade({ preset, intensity = 0.15 }) {
  // Subtle color grading presets via CSS filters
  let filter = '';
  let background = 'transparent';
  let mixBlendMode = 'normal';

  switch (preset) {
    case 'cinematic_warm':
      filter = 'sepia(0.3) contrast(1.1) brightness(0.95)';
      background = 'rgba(255, 200, 150, 0.1)';
      mixBlendMode = 'multiply';
      break;
    case 'cinematic_cool':
      filter = 'contrast(1.1) brightness(0.95) saturate(0.9)';
      background = 'rgba(100, 150, 255, 0.1)';
      mixBlendMode = 'multiply';
      break;
    case 'high_contrast':
      filter = 'contrast(1.3) saturate(1.1)';
      break;
    case 'desaturated':
      filter = 'saturate(0.6) contrast(1.05)';
      break;
    case 'vibrant':
      filter = 'saturate(1.3) contrast(1.05)';
      break;
    default:
      return null;
  }

  return (
    <AbsoluteFill
      style={{
        filter,
        background,
        mixBlendMode,
        opacity: clamp(intensity, 0.08, 0.15),
        pointerEvents: 'none',
      }}
    />
  );
}

export function CinematicVignette() {
  // Subtle edge darkening
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  
  const opacity = interpolate(
    frame,
    [0, fps],
    [0, 0.25],
    { extrapolateRight: 'clamp' }
  );

  return (
    <AbsoluteFill
      style={{
        background: 'radial-gradient(circle, transparent 50%, rgba(0, 0, 0, 1) 150%)',
        opacity,
        pointerEvents: 'none',
      }}
    />
  );
}

export function OverlayStack({ scene, theme }) {
  const config = scene?.atmosphere || {};
  const layers = Array.isArray(config.layers) ? config.layers : [];
  const intensity = clamp(Number(config.intensity ?? 0.15), 0, 0.65);
  const seed = Number(config.seed || scene?.scene_index || 1);

  return (
    <AbsoluteFill style={{ pointerEvents: 'none', overflow: 'hidden' }}>
      {/* Background color grading */}
      {config.color_grade && (
        <ColorGrade preset={config.color_grade} intensity={intensity} />
      )}

      {/* Standard layers */}
      {layers.includes('dust') && <Dust />}
      {layers.includes('film_scratches') && <FilmScratches />}
      {layers.includes('grain') && <Grain intensity={intensity} />}
      {layers.includes('light_leak') && <LightLeak seed={seed} intensity={intensity} theme={theme} />}
      {layers.includes('scanlines') && <Scanlines />}
      {layers.includes('paper_fibers') && <PaperFibers />}
      {layers.includes('projector_flicker') && <ProjectorFlicker />}
      {layers.includes('signal_noise') && <SignalNoise />}
      {layers.includes('chromatic_edge') && <ChromaticEdge />}
      {layers.includes('halation') && <Halation />}
      {layers.includes('bokeh') && <Bokeh />}
      {layers.includes('rain_glass') && <RainGlass />}
      
      {/* Focus overlay for graphics */}
      {config.focus_overlay && (
        <FocusOverlay position={config.focus_overlay} />
      )}

      {/* Cinematic vignette */}
      {config.vignette && <CinematicVignette />}
    </AbsoluteFill>
  );
}
