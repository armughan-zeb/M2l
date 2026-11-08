import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { rgba } from '../theme';

/**
 * TechnicalBlueprint.jsx
 * Taxonomy 5: Technical Blueprint & CAD Schematic Systems.
 * Engineering grid background, progressive SVG vector trace-in,
 * live animated dimension measurement callouts, and technical tolerances.
 */
export function TechnicalBlueprint({
  scene = {},
  visualDNA = {},
  title = '',
  specNumber = 'SPEC-DWG-1965',
  reconstructionMode = false,
}) {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();

  const palette = visualDNA?.palette || {
    primary: '#EBF8FF',
    secondary: '#90CDF4',
    accent: '#3182CE',
    background: '#0B1E36',
    border: '#2B6CB0',
  };

  const monoFont = visualDNA?.typography?.mono || 'JetBrains Mono, monospace';
  const displayFont = visualDNA?.typography?.display || 'Cinzel, sans-serif';

  const headline = title || scene.editorial_contract?.display_text || scene.title || 'ARCHITECTURAL SCHEMATIC & BLUEPRINT';

  // SVG trace progress
  const traceProgress = interpolate(frame, [8, 70], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Animated dimension calculations
  const dynamicWidthMM = Math.floor(interpolate(frame, [15, 60], [1200, 4280], { extrapolateRight: 'clamp' }));
  const dynamicHeightMM = Math.floor(interpolate(frame, [25, 70], [400, 1420], { extrapolateRight: 'clamp' }));

  const pathLength = 1200;

  return (
    <AbsoluteFill
      style={{
        backgroundColor: '#07162C',
        overflow: 'hidden',
      }}
    >
      {/* 1. Engineering Drafting Grid Background */}
      <AbsoluteFill
        style={{
          backgroundImage: `
            linear-gradient(rgba(49, 130, 206, 0.22) 1px, transparent 1px),
            linear-gradient(90deg, rgba(49, 130, 206, 0.22) 1px, transparent 1px),
            linear-gradient(rgba(49, 130, 206, 0.08) 1px, transparent 1px),
            linear-gradient(90deg, rgba(49, 130, 206, 0.08) 1px, transparent 1px)
          `,
          backgroundSize: '100px 100px, 100px 100px, 20px 20px, 20px 20px',
        }}
      />

      {/* 2. Drafting Header & Title Block */}
      <div
        style={{
          position: 'absolute',
          top: 36,
          left: 56,
          right: 56,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          zIndex: 10,
        }}
      >
        <div>
          <div style={{ fontFamily: monoFont, fontSize: 12, fontWeight: 700, color: '#63B3ED', letterSpacing: '0.14em' }}>
            {reconstructionMode ? 'CONCEPTUAL SCHEMATIC // NOT TO SCALE' : `ENGINEERING CAD SPECIFICATION // ${specNumber}`}
          </div>
          <div style={{ fontFamily: displayFont, fontSize: Math.min(width * 0.024, 34), fontWeight: 900, color: '#EBF8FF', textTransform: 'uppercase' }}>
            {headline}
          </div>
        </div>

        {/* Blueprint Stamp Tag */}
        <div
          style={{
            border: '2px solid #63B3ED',
            padding: '8px 16px',
            fontFamily: monoFont,
            fontSize: 11,
            color: '#90CDF4',
            letterSpacing: '0.1em',
            textTransform: 'uppercase',
          }}
        >
          {reconstructionMode ? 'ILLUSTRATIVE' : 'APPROVED FOR MANUFACTURE'}
        </div>
      </div>

      {/* 3. Central Technical Vector Schematic (Tracing in) */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
        }}
      >
        <svg viewBox="0 0 900 450" style={{ width: '70%', height: '60%', overflow: 'visible' }}>
          {/* Machine / Vehicle Silhouette Technical Vectors */}
          <path
            d="M 120,320 L 120,240 L 220,210 L 320,120 L 580,120 L 700,210 L 820,240 L 820,320 Z"
            fill="none"
            stroke="#90CDF4"
            strokeWidth="3"
            strokeDasharray={pathLength}
            strokeDashoffset={pathLength * (1 - traceProgress)}
            strokeLinecap="round"
          />
          {/* Wheel / Assembly Circles */}
          <circle cx="260" cy="320" r="55" fill="none" stroke="#63B3ED" strokeWidth="2.5" strokeDasharray="350" strokeDashoffset={350 * (1 - traceProgress)} />
          <circle cx="260" cy="320" r="22" fill="none" stroke="#63B3ED" strokeWidth="2" />
          <circle cx="680" cy="320" r="55" fill="none" stroke="#63B3ED" strokeWidth="2.5" strokeDasharray="350" strokeDashoffset={350 * (1 - traceProgress)} />
          <circle cx="680" cy="320" r="22" fill="none" stroke="#63B3ED" strokeWidth="2" />

          {/* Internal Assembly Ribs */}
          <line x1="320" y1="120" x2="320" y2="320" stroke="rgba(99, 179, 237, 0.4)" strokeWidth="1.5" strokeDasharray="6 4" />
          <line x1="580" y1="120" x2="580" y2="320" stroke="rgba(99, 179, 237, 0.4)" strokeWidth="1.5" strokeDasharray="6 4" />

          {/* Dimension Measurement Callout (Horizontal Width) */}
          <line x1="120" y1="390" x2="820" y2="390" stroke="#63B3ED" strokeWidth="2" />
          <line x1="120" y1="375" x2="120" y2="405" stroke="#63B3ED" strokeWidth="2" />
          <line x1="820" y1="375" x2="820" y2="405" stroke="#63B3ED" strokeWidth="2" />
          <text x="470" y="415" fill="#EBF8FF" fontFamily={monoFont} fontSize="14" textAnchor="middle" fontWeight="bold">
            {reconstructionMode ? 'CONCEPTUAL FORM // NO DIMENSION DATA' : `${dynamicWidthMM} mm (TOLERANCE ±0.05)`}
          </text>

          {/* Dimension Measurement Callout (Vertical Height) */}
          <line x1="60" y1="120" x2="60" y2="320" stroke="#63B3ED" strokeWidth="2" />
          <line x1="45" y1="120" x2="75" y2="120" stroke="#63B3ED" strokeWidth="2" />
          <line x1="45" y1="320" x2="75" y2="320" stroke="#63B3ED" strokeWidth="2" />
          <text x="35" y="225" fill="#EBF8FF" fontFamily={monoFont} fontSize="14" textAnchor="middle" fontWeight="bold" transform="rotate(-90 35,225)">
            {reconstructionMode ? 'NOT TO SCALE' : `${dynamicHeightMM} mm`}
          </text>
        </svg>
      </div>

      {/* 4. Drafting Scale Footer */}
      <div
        style={{
          position: 'absolute',
          bottom: 28,
          left: 56,
          right: 56,
          display: 'flex',
          justifyContent: 'space-between',
          borderTop: '1px solid rgba(99, 179, 237, 0.3)',
          paddingTop: 12,
          fontFamily: monoFont,
          fontSize: 11,
          color: '#90CDF4',
          letterSpacing: '0.08em',
        }}
      >
        <span>{reconstructionMode ? 'SCHEMATIC ILLUSTRATION // NOT A MANUFACTURING DRAWING' : 'CAD SCALE 1:25 // PROJECTION THIRD-ANGLE'}</span>
        <span>{reconstructionMode ? 'DETAILS SHOWN ONLY WHEN GROUNDED IN SCRIPT' : 'SECURITY LEVEL: ARCHITECTURAL RECORD'}</span>
      </div>
    </AbsoluteFill>
  );
}
