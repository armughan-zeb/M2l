import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { rgba } from '../theme';

/**
 * DepthTypography.jsx
 * Taxonomy 9: Depth Typography (Netflix / Vox "Behind-the-Subject" 3D Typography).
 * Giant kinetic typography rendered with 3D Z-depth, tracking expansion,
 * and high-contrast documentary framing.
 */
export function DepthTypography({
  scene = {},
  visualDNA = {},
  text = '',
  kicker = '',
  accent = null,
}) {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();

  const palette = visualDNA?.palette || {
    primary: '#FFFFFF',
    secondary: '#A0AEC0',
    accent: '#E53E3E',
    background: '#0B0D12',
    border: '#2D3748',
  };

  const displayFont = visualDNA?.typography?.display || 'Cinzel, Georgia, serif';
  const monoFont = visualDNA?.typography?.mono || 'JetBrains Mono, monospace';

  const approvedText = text || scene.editorial_contract?.display_text || scene.title || 'CRITICAL REVEAL';
  const kickerText = kicker || scene.editorial_contract?.family || 'DOCUMENTARY RECORD';

  // Smooth push progress
  const enterSpring = spring({
    frame,
    fps,
    config: { damping: 14, mass: 1 },
  });

  const pushZ = interpolate(frame, [0, 120], [0, 80]);
  const tracking = interpolate(frame, [0, 120], [0.04, 0.09]);

  return (
    <AbsoluteFill
      style={{
        justifyContent: 'center',
        alignItems: 'center',
        pointerEvents: 'none',
        perspective: 1000,
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          transform: `translateZ(${pushZ}px) scale(${0.92 + 0.08 * enterSpring})`,
          opacity: enterSpring,
          textAlign: 'center',
          maxWidth: '90%',
        }}
      >
        {/* Kicker badge */}
        <div
          style={{
            fontFamily: monoFont,
            fontSize: Math.min(width * 0.012, 16),
            fontWeight: 800,
            letterSpacing: '0.24em',
            color: accent || palette.accent,
            textTransform: 'uppercase',
            marginBottom: 16,
            background: rgba(palette.background || '#000', 0.6),
            padding: '4px 16px',
            borderRadius: 2,
            border: `1px solid ${rgba(accent || palette.accent, 0.4)}`,
          }}
        >
          {kickerText}
        </div>

        {/* Giant Hero Depth Text */}
        <div
          style={{
            fontFamily: displayFont,
            fontSize: Math.min(width * 0.065, height * 0.12, 96),
            fontWeight: 900,
            lineHeight: 1.05,
            letterSpacing: `${tracking}em`,
            color: palette.primary || '#FFFFFF',
            textTransform: 'uppercase',
            textShadow: '0 12px 35px rgba(0,0,0,0.9), 0 2px 4px rgba(0,0,0,0.8)',
            WebkitTextStroke: '1px rgba(255,255,255,0.1)',
          }}
        >
          {approvedText}
        </div>
      </div>
    </AbsoluteFill>
  );
}
