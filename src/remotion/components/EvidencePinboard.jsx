import React from 'react';
import { AbsoluteFill, Img, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { rgba } from '../theme';

/**
 * EvidencePinboard.jsx
 * Taxonomy 3: Physical Red String Pinboard (Crime / Conspiracy / Investigation Network).
 * Corkboard canvas, pinned polaroid/memo nodes with bounce recoil,
 * dynamic elastic connecting red threads, and metallic pins.
 * Fully responsive across 16:9, 4:3, and vertical 9:16 aspect ratios.
 */
export function EvidencePinboard({
  scene = {},
  visualDNA = {},
  title = '',
  nodes = [],
  reconstructionMode = false,
}) {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();

  const palette = visualDNA?.palette || {
    primary: '#FFFFFF',
    secondary: '#A0AEC0',
    accent: '#E53E3E',
    background: '#140E0A',
    border: '#2D3748',
  };

  const monoFont = visualDNA?.typography?.mono || 'JetBrains Mono, monospace';
  const displayFont = visualDNA?.typography?.display || 'Cinzel, Georgia, serif';
  const bodyFont = visualDNA?.typography?.body || 'DM Sans, sans-serif';

  const headline = title || scene.editorial_contract?.display_text || scene.title || 'FORENSIC INVESTIGATION BOARD';

  // Node entrance springs
  const node1Spring = spring({ frame: frame - 4, fps, config: { damping: 10, mass: 1.1 } });
  const node2Spring = spring({ frame: frame - 18, fps, config: { damping: 10, mass: 1.1 } });
  const node3Spring = spring({ frame: frame - 32, fps, config: { damping: 10, mass: 1.1 } });

  // String elasticity / sag interpolation
  const stringSag = interpolate(frame, [25, 60], [35, 12], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Responsive layout calculations
  const node1Width = Math.min(width * 0.23, 290);
  const node1Left = width * 0.08;
  const node1Top = height * 0.26;
  const p1X = node1Left + node1Width / 2;
  const p1Y = node1Top;

  const node2Width = Math.min(width * 0.26, 310);
  const node2Left = (width - node2Width) / 2;
  const node2Top = height * 0.20;
  const p2X = node2Left + node2Width / 2;
  const p2Y = node2Top;

  const node3Width = Math.min(width * 0.23, 290);
  const node3Left = width * 0.92 - node3Width;
  const node3Top = height * 0.34;
  const p3X = node3Left + node3Width / 2;
  const p3Y = node3Top;

  const cp1X = (p1X + p2X) / 2;
  const cp1Y = Math.max(p1Y, p2Y) + stringSag * (height / 1080);
  const cp2X = (p2X + p3X) / 2;
  const cp2Y = Math.max(p2Y, p3Y) + stringSag * (height / 1080);

  return (
    <AbsoluteFill
      style={{
        backgroundColor: '#1E1510',
        backgroundImage: 'radial-gradient(circle at center, #2C1E16 0%, #150E0A 100%)',
        overflow: 'hidden',
      }}
    >
      {/* Cork Texture Grain */}
      <AbsoluteFill
        style={{
          opacity: 0.18,
          backgroundImage: 'radial-gradient(#8C5935 1px, transparent 1px)',
          backgroundSize: '16px 16px',
          pointerEvents: 'none',
        }}
      />

      {/* Header Case Tag */}
      <div
        style={{
          position: 'absolute',
          top: 32,
          left: 48,
          zIndex: 10,
        }}
      >
        <div style={{ fontFamily: monoFont, fontSize: 12, fontWeight: 700, color: '#E53E3E', letterSpacing: '0.14em' }}>
          {reconstructionMode ? 'EDITORIAL RECONSTRUCTION // SCENE BOARD' : 'CASE INVESTIGATION // EXHIBIT MAP'}
        </div>
        <div style={{ fontFamily: displayFont, fontSize: Math.min(width * 0.024, 34), fontWeight: 900, color: '#FFF', textTransform: 'uppercase' }}>
          {headline}
        </div>
      </div>

      {/* Dynamic Elastic Connecting Red Strings (SVG Quadratic Curves) */}
      <svg style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', zIndex: 4, pointerEvents: 'none' }}>
        {/* String 1: Node 1 (left) to Node 2 (center) */}
        {frame >= 20 && (
          <path
            d={`M ${p1X.toFixed(1)},${p1Y.toFixed(1)} Q ${cp1X.toFixed(1)},${cp1Y.toFixed(1)} ${p2X.toFixed(1)},${p2Y.toFixed(1)}`}
            fill="none"
            stroke="#E53E3E"
            strokeWidth="3.5"
            strokeLinecap="round"
            filter="drop-shadow(0 8px 10px rgba(0,0,0,0.7))"
          />
        )}
        {/* String 2: Node 2 (center) to Node 3 (right) */}
        {frame >= 35 && (
          <path
            d={`M ${p2X.toFixed(1)},${p2Y.toFixed(1)} Q ${cp2X.toFixed(1)},${cp2Y.toFixed(1)} ${p3X.toFixed(1)},${p3Y.toFixed(1)}`}
            fill="none"
            stroke="#E53E3E"
            strokeWidth="3.5"
            strokeLinecap="round"
            filter="drop-shadow(0 8px 10px rgba(0,0,0,0.7))"
          />
        )}
      </svg>

      {/* Node 1: Pinned Document / Memo (Left) */}
      <div
        style={{
          position: 'absolute',
          left: node1Left,
          top: node1Top,
          width: node1Width,
          minHeight: 220,
          backgroundColor: '#F7F4EA',
          borderRadius: 2,
          padding: '18px 20px',
          boxShadow: '0 20px 40px rgba(0,0,0,0.75)',
          transform: `rotate(-4deg) scale(${node1Spring})`,
          opacity: node1Spring,
          zIndex: 5,
        }}
      >
        {/* Metallic Thumbtack */}
        <div
          style={{
            position: 'absolute',
            top: -10,
            left: '50%',
            transform: 'translateX(-50%)',
            width: 20,
            height: 20,
            borderRadius: '50%',
            backgroundColor: '#C53030',
            boxShadow: '0 4px 10px rgba(0,0,0,0.8), inset 0 2px 4px rgba(255,255,255,0.6)',
          }}
        />
        <div style={{ fontFamily: monoFont, fontSize: 10, color: '#718096', fontWeight: 700, marginBottom: 8 }}>
          {reconstructionMode ? 'NARRATION POINT // 01' : 'FILE EXHIBIT #01 // DOSSIER'}
        </div>
        <div style={{ fontFamily: bodyFont, fontSize: 15, fontWeight: 700, color: '#1A202C', lineHeight: 1.3 }}>
          {scene.editorial_contract?.display_text || (reconstructionMode ? String(scene.narration || 'Scene summary').slice(0, 110) : 'PRIMARY INCIDENT RECORD')}
        </div>
      </div>

      {/* Node 2: Central Polaroid Photo Snapshot */}
      <div
        style={{
          position: 'absolute',
          left: node2Left,
          top: node2Top,
          width: node2Width,
          backgroundColor: '#FFFFFF',
          borderRadius: 2,
          padding: '16px 16px 40px',
          boxShadow: '0 25px 50px rgba(0,0,0,0.85)',
          transform: `rotate(3deg) scale(${node2Spring})`,
          opacity: node2Spring,
          zIndex: 6,
        }}
      >
        {/* Metallic Thumbtack */}
        <div
          style={{
            position: 'absolute',
            top: -10,
            left: '50%',
            transform: 'translateX(-50%)',
            width: 20,
            height: 20,
            borderRadius: '50%',
            backgroundColor: '#C53030',
            boxShadow: '0 4px 10px rgba(0,0,0,0.8), inset 0 2px 4px rgba(255,255,255,0.6)',
          }}
        />
        <div
          style={{
            width: '100%',
            height: 200,
            backgroundColor: '#2D3748',
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            color: '#A0AEC0',
            fontFamily: monoFont,
            fontSize: 12,
            overflow: 'hidden',
          }}
        >
          {!reconstructionMode && scene.media?.src ? (
            <Img src={scene.media.src} alt="Evidence" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
          ) : (
            <div style={{ padding: 18, textAlign: 'center', color: '#E2E8F0', fontFamily: monoFont, lineHeight: 1.5 }}>
              <div style={{ fontSize: 11, letterSpacing: '0.12em', color: reconstructionMode ? '#F87171' : '#A0AEC0' }}>
                {reconstructionMode ? 'ILLUSTRATIVE RECONSTRUCTION' : 'SCENE VISUAL'}
              </div>
              {reconstructionMode && <div style={{ marginTop: 14, fontSize: 17, fontWeight: 800 }}>{String(scene.visual_requirement?.canonical_entity || scene.title || 'SCENE CONTEXT').toUpperCase()}</div>}
            </div>
          )}
        </div>
        <div style={{ fontFamily: monoFont, fontSize: 11, color: '#4A5568', marginTop: 12, textAlign: 'center', fontWeight: 700 }}>
          {reconstructionMode ? 'BASED ON SCENE NARRATION' : 'EVIDENCE REF // SUBJECT KEY'}
        </div>
      </div>

      {/* Node 3: Index Card / Timestamp Note (Right) */}
      <div
        style={{
          position: 'absolute',
          left: node3Left,
          top: node3Top,
          width: node3Width,
          minHeight: 180,
          backgroundColor: '#FEFCBF',
          borderRadius: 2,
          padding: '18px 20px',
          boxShadow: '0 20px 40px rgba(0,0,0,0.75)',
          transform: `rotate(-2deg) scale(${node3Spring})`,
          opacity: node3Spring,
          zIndex: 5,
        }}
      >
        {/* Metallic Thumbtack */}
        <div
          style={{
            position: 'absolute',
            top: -10,
            left: '50%',
            transform: 'translateX(-50%)',
            width: 20,
            height: 20,
            borderRadius: '50%',
            backgroundColor: '#C53030',
            boxShadow: '0 4px 10px rgba(0,0,0,0.8), inset 0 2px 4px rgba(255,255,255,0.6)',
          }}
        />
        <div style={{ fontFamily: monoFont, fontSize: 10, color: '#744210', fontWeight: 700, marginBottom: 8 }}>
          {reconstructionMode ? 'NARRATION NOTE' : 'CONFIDENTIAL WITNESS NOTE'}
        </div>
        <div style={{ fontFamily: bodyFont, fontSize: 14, color: '#744210', lineHeight: 1.4 }}>
          {scene.narration ? scene.narration.slice(0, 100) + (scene.narration.length > 100 ? '…' : '') : (reconstructionMode ? 'No scene narration was supplied.' : 'Chronology note')}
        </div>
      </div>
    </AbsoluteFill>
  );
}
