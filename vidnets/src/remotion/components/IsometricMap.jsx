import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { rgba } from '../theme';

/**
 * IsometricMap.jsx
 * Taxonomy 1: 2.5D Isometric Terrain & Flight/Movement Trackers.
 * Rotated 3D spatial terrain, progressive SVG vector route trace,
 * pulsing GPS target rings, and rolling live telemetry HUD.
 */
export function IsometricMap({
  scene = {},
  visualDNA = {},
  origin = 'DEPARTURE POINT',
  destination = 'TARGET DESTINATION',
  title = '',
}) {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();

  const palette = visualDNA?.palette || {
    primary: '#FFFFFF',
    secondary: '#A0AEC0',
    accent: '#3182CE',
    background: '#07090E',
    border: '#1A202C',
  };

  const monoFont = visualDNA?.typography?.mono || 'JetBrains Mono, monospace';
  const displayFont = visualDNA?.typography?.display || 'Cinzel, sans-serif';

  const headline = title || scene.editorial_contract?.display_text || scene.title || 'TACTICAL FLIGHT & ROUTE TRACKER';

  // Smooth progress across 90 frames
  const routeProgress = interpolate(frame, [10, 80], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Target acquisition spring
  const targetAcquired = spring({
    frame: frame - 65,
    fps,
    config: { damping: 10, mass: 1 },
  });

  // Rolling telemetry coordinates
  const latRoll = (34.0522 + (frame * 0.0034)).toFixed(4);
  const lonRoll = (-118.2437 - (frame * 0.0041)).toFixed(4);
  const altRoll = Math.floor(18500 + frame * 42);

  // Path definition for route
  const pathD = 'M 80,480 C 240,420 380,260 540,240 S 760,180 920,120';
  const pathLength = 1000;

  return (
    <AbsoluteFill
      style={{
        backgroundColor: palette.background,
        overflow: 'hidden',
      }}
    >
      {/* 1. Tactical HUD Header & Live Telemetry */}
      <div
        style={{
          position: 'absolute',
          top: 36,
          left: 48,
          right: 48,
          zIndex: 10,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
        }}
      >
        <div>
          <div
            style={{
              fontFamily: monoFont,
              fontSize: 12,
              fontWeight: 700,
              letterSpacing: '0.16em',
              color: palette.accent,
              marginBottom: 6,
            }}
          >
            [ GPS SATELLITE TELEMETRY // VECTOR TRACKING ]
          </div>
          <div
            style={{
              fontFamily: displayFont,
              fontSize: Math.min(width * 0.024, 34),
              fontWeight: 800,
              color: palette.primary,
              textTransform: 'uppercase',
              letterSpacing: '0.04em',
            }}
          >
            {headline}
          </div>
        </div>

        {/* Live Rolling HUD Telemetry Box */}
        <div
          style={{
            backgroundColor: rgba(palette.border, 0.65),
            border: `1px solid ${rgba(palette.accent, 0.4)}`,
            borderRadius: 4,
            padding: '10px 18px',
            fontFamily: monoFont,
            fontSize: 12,
            color: '#E2E8F0',
            lineHeight: 1.6,
            backdropFilter: 'blur(8px)',
          }}
        >
          <div>LAT: <span style={{ color: palette.accent }}>{latRoll}° N</span></div>
          <div>LON: <span style={{ color: palette.accent }}>{lonRoll}° W</span></div>
          <div>ALT: <span style={{ color: '#48BB78' }}>{altRoll.toLocaleString()} FT</span></div>
        </div>
      </div>

      {/* 2. 2.5D Isometric 3D Space Container */}
      <AbsoluteFill
        style={{
          perspective: 1200,
          justifyContent: 'center',
          alignItems: 'center',
        }}
      >
        <div
          style={{
            width: 1050,
            height: 620,
            transform: 'rotateX(42deg) rotateZ(-14deg)',
            transformStyle: 'preserve-3d',
            position: 'relative',
            border: `1px solid ${rgba(palette.accent, 0.25)}`,
            backgroundColor: 'rgba(10, 16, 28, 0.6)',
            boxShadow: `0 35px 80px rgba(0,0,0,0.85), inset 0 0 100px ${rgba(palette.accent, 0.08)}`,
          }}
        >
          {/* Isometric Grid Matrix */}
          <div
            style={{
              position: 'absolute',
              inset: 0,
              backgroundImage: `linear-gradient(${rgba(palette.accent, 0.15)} 1px, transparent 1px), linear-gradient(90deg, ${rgba(palette.accent, 0.15)} 1px, transparent 1px)`,
              backgroundSize: '50px 50px',
              opacity: 0.8,
            }}
          />

          {/* Topographical Contour Circles */}
          <svg style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}>
            <ellipse cx="320" cy="360" rx="180" ry="110" fill="none" stroke={rgba(palette.accent, 0.18)} strokeWidth="1.5" strokeDasharray="6 4" />
            <ellipse cx="320" cy="360" rx="120" ry="70" fill="none" stroke={rgba(palette.accent, 0.25)} strokeWidth="1.5" />
            <ellipse cx="780" cy="200" rx="160" ry="90" fill="none" stroke={rgba(palette.accent, 0.18)} strokeWidth="1.5" strokeDasharray="6 4" />

            {/* Progressive Vector Route Path */}
            <path
              d={pathD}
              fill="none"
              stroke={palette.accent}
              strokeWidth="4"
              strokeDasharray={pathLength}
              strokeDashoffset={pathLength * (1 - routeProgress)}
              strokeLinecap="round"
            />
            {/* Glowing path underlay */}
            <path
              d={pathD}
              fill="none"
              stroke={palette.accent}
              strokeWidth="10"
              strokeOpacity="0.3"
              strokeDasharray={pathLength}
              strokeDashoffset={pathLength * (1 - routeProgress)}
              strokeLinecap="round"
            />
          </svg>

          {/* Origin Pin & Label */}
          <div style={{ position: 'absolute', left: 60, top: 460, color: '#FFF', fontFamily: monoFont }}>
            <div style={{ width: 14, height: 14, borderRadius: '50%', backgroundColor: palette.accent, boxShadow: `0 0 16px ${palette.accent}` }} />
            <div style={{ fontSize: 11, fontWeight: 700, marginTop: 4, letterSpacing: '0.1em' }}>
              {origin}
            </div>
          </div>

          {/* Destination Pulsing Target Ring */}
          {frame >= 60 && (
            <div style={{ position: 'absolute', left: 900, top: 105, color: '#FFF', fontFamily: monoFont }}>
              <div
                style={{
                  width: 24,
                  height: 24,
                  borderRadius: '50%',
                  border: `2px solid #E53E3E`,
                  transform: `scale(${1 + 0.4 * (1 - targetAcquired)})`,
                  boxShadow: '0 0 20px rgba(229, 62, 62, 0.7)',
                }}
              />
              <div style={{ fontSize: 11, fontWeight: 700, marginTop: 4, letterSpacing: '0.1em', color: '#FEB2B2' }}>
                {destination}
              </div>
            </div>
          )}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
}
