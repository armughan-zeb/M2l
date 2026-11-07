import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { rgba } from '../theme';

/**
 * DeclassifiedDocument.jsx
 * Taxonomy 2: Forensic Investigation & Declassified Dossier System.
 * Aged archival parchment document, black redaction reveals,
 * SVG marker highlight sweep, and high-impact rubber stamp slam.
 */
export function DeclassifiedDocument({
  scene = {},
  visualDNA = {},
  title = '',
  stampText = 'DECLASSIFIED',
  highlightText = '',
  classification = 'TOP SECRET // DECLASSIFIED RECORD',
  reconstructionMode = false,
}) {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();

  const palette = visualDNA?.palette || {
    primary: '#FFFFFF',
    secondary: '#A0AEC0',
    accent: '#D69E2E',
    background: '#0B0D12',
    border: '#2D3748',
  };

  const monoFont = visualDNA?.typography?.mono || 'JetBrains Mono, monospace';
  const displayFont = visualDNA?.typography?.display || 'Cinzel, Georgia, serif';
  const bodyFont = visualDNA?.typography?.body || 'DM Sans, sans-serif';

  const sceneText = String(scene.narration || scene.text?.display_text || scene.title || '').trim();
  const headline = title || scene.editorial_contract?.display_text || scene.title || 'PRIMARY DOSSIER FILE';
  const highlight = highlightText || scene.editorial_contract?.emphasis_phrase || headline;

  // Entrance spring
  const enterProgress = spring({
    frame,
    fps,
    config: { damping: 14, mass: 0.9 },
  });

  // Stamp physics: slams down around frame 18 with high mass and overshoot
  const stampSpring = spring({
    frame: frame - 16,
    fps,
    config: { damping: 9, mass: 1.3, stiffness: 180 },
  });

  // Marker highlight progress: draws after stamp hits
  const markerProgress = interpolate(frame, [22, 50], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Redaction slide-away progress: dissolves/slides at frame 10
  const redactionReveal = interpolate(frame, [10, 28], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Subtle paper camera float
  const paperFloatY = interpolate(frame, [0, 150], [10, -10]);

  return (
    <AbsoluteFill
      style={{
        backgroundColor: palette.background,
        justifyContent: 'center',
        alignItems: 'center',
        overflow: 'hidden',
      }}
    >
      {/* 1. Atmospheric Ambient Backlight */}
      <AbsoluteFill
        style={{
          background: `radial-gradient(ellipse at 50% 45%, ${rgba(palette.accent, 0.12)}, transparent 70%)`,
          pointerEvents: 'none',
        }}
      />

      {/* 2. Aged Archival Document Sheet */}
      <div
        style={{
          position: 'relative',
          width: Math.min(width * 0.78, 1100),
          minHeight: Math.min(height * 0.76, 750),
          backgroundColor: '#F5EFE0',
          backgroundImage: 'radial-gradient(#E8DFC8 1px, transparent 1px)',
          backgroundSize: '24px 24px',
          borderRadius: 4,
          padding: '42px 56px',
          boxShadow: '0 30px 90px rgba(0,0,0,0.85), inset 0 0 60px rgba(180,150,110,0.3)',
          border: '1px solid #D8CCA8',
          color: '#1A202C',
          transform: `translateY(${paperFloatY}px) scale(${0.92 + 0.08 * enterProgress})`,
          opacity: enterProgress,
        }}
      >
        {/* Document Header Bar */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            borderBottom: '2px solid #2D3748',
            paddingBottom: 14,
            marginBottom: 26,
            fontFamily: monoFont,
            fontSize: 13,
            fontWeight: 700,
            letterSpacing: '0.14em',
            color: '#4A5568',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ backgroundColor: '#C53030', color: '#FFF', padding: '2px 8px', borderRadius: 2 }}>
              {classification}
            </span>
            <span>CASE ARCHIVE // REF-{String(scene.scene_index || '01').padStart(3, '0')}</span>
          </div>
          <div>{reconstructionMode ? 'SCRIPT-BASED VISUAL // NOT A PRIMARY SOURCE' : 'AUTH: DEPT-ARCHIVES // EXONERATED'}</div>
        </div>

        {/* Dossier Headline */}
        <div
          style={{
            fontFamily: displayFont,
            fontSize: Math.min(width * 0.026, 38),
            fontWeight: 900,
            letterSpacing: '0.04em',
            lineHeight: 1.2,
            color: '#1A202C',
            textTransform: 'uppercase',
            marginBottom: 20,
          }}
        >
          {headline}
        </div>

        {/* Narrative Clauses with Redaction Reveal */}
        <div
          style={{
            position: 'relative',
            fontFamily: bodyFont,
            fontSize: Math.min(width * 0.016, 22),
            lineHeight: 1.6,
            color: '#2D3748',
            maxWidth: '85%',
            marginBottom: 28,
          }}
        >
          <span style={{ position: 'relative', display: 'inline' }}>
            {sceneText.slice(0, 160) || (reconstructionMode ? 'No scene narration was supplied.' : 'Evidence harvested from primary historical record and official archives.')}
            {/* Animated SVG Marker Highlight Sweep */}
            <svg
              style={{
                position: 'absolute',
                left: 0,
                bottom: -2,
                width: '100%',
                height: 12,
                pointerEvents: 'none',
                overflow: 'visible',
              }}
            >
              <line
                x1="0"
                y1="6"
                x2="100%"
                y2="6"
                stroke={palette.accent}
                strokeWidth="10"
                strokeOpacity="0.45"
                strokeDasharray="1000"
                strokeDashoffset={1000 * (1 - markerProgress)}
                strokeLinecap="round"
              />
            </svg>
          </span>

          {/* Sliding/Dissolving Redaction Censorship Block */}
          {redactionReveal < 1 && (
            <div
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                right: 0,
                bottom: 0,
                backgroundColor: '#1A202C',
                transform: `scaleX(${1 - redactionReveal})`,
                transformOrigin: 'right center',
                opacity: 1 - redactionReveal,
              }}
            />
          )}
        </div>

        {/* Document Metadata Footer */}
        <div
          style={{
            position: 'absolute',
            bottom: 24,
            left: 56,
            right: 56,
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            borderTop: '1px solid #CBD5E0',
            paddingTop: 12,
            fontFamily: monoFont,
            fontSize: 11,
            color: '#718096',
            letterSpacing: '0.08em',
          }}
        >
          <span>{reconstructionMode ? 'VISUAL: EDITORIAL RECONSTRUCTION' : 'EVIDENCE SOURCE: VERIFIED ARCHIVAL RECORDS'}</span>
          <span>{reconstructionMode ? 'BASIS: SCENE NARRATION' : 'TIMESTAMP: HISTORICAL DOSSIER'}</span>
        </div>

        {/* Rubber Stamp Slam Overlay */}
        {frame >= 16 && (
          <div
            style={{
              position: 'absolute',
              top: '28%',
              right: '8%',
              border: '5px double #C53030',
              color: '#C53030',
              fontFamily: monoFont,
              fontWeight: 900,
              fontSize: Math.min(width * 0.032, 44),
              letterSpacing: '0.18em',
              padding: '8px 24px',
              borderRadius: 4,
              textTransform: 'uppercase',
              transform: `rotate(-14deg) scale(${1 + 0.3 * (1 - stampSpring)})`,
              opacity: Math.min(1, stampSpring * 0.95),
              boxShadow: '0 0 20px rgba(197, 48, 48, 0.3)',
              pointerEvents: 'none',
            }}
          >
            {stampText}
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
}
