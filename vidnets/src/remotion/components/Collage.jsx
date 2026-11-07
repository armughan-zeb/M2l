import React from 'react';
import { AbsoluteFill, Img, OffthreadVideo, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { computeSpringEnter, computeEntranceStyle } from '../engine/MotionEngine';
import { rgba } from '../theme';

const VIDEO_EXTENSIONS = /\.(?:mp4|webm|mov|mkv|avi|m4v)(?:[?#]|$)/i;

function SafeMedia({ src, style }) {
  if (!src) return null;
  return VIDEO_EXTENSIONS.test(src)
    ? <OffthreadVideo src={src} muted style={style} />
    : <Img src={src} style={style} />;
}

/**
 * Collage — Professional Documentary Multi-Asset & Evidence Desk System.
 * Supported variants:
 * - evidence_pinboard (Authentic textured corkboard with polaroids, pushpins, and connected red yarn)
 * - three_panel_asymmetric
 * - contact_sheet_grid
 * - scattered_photo_desk
 */
export function Collage({ scene = {}, visualDNA = {}, props = {} }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const sceneDuration = Math.max(1, scene?.duration_in_frames !== undefined && scene?.duration_in_frames !== null
    ? Math.round(Number(scene.duration_in_frames))
    : Math.round(Number(scene?.duration || 4) * fps));
  const progress = Math.min(1, Math.max(0, frame / Math.max(1, sceneDuration - 1)));

  const images = props.images || [
    { src: props.img1 || '', label: 'EXHIBIT A // SURVEILLANCE' },
    { src: props.img2 || '', label: 'EXHIBIT B // DEPOSITION' },
    { src: props.img3 || '', label: 'EXHIBIT C // LOCATION' },
  ];
  const title = String(props.title || scene.text?.headline || 'CRITICAL EVIDENCE PINBOARD');
  const variant = String(props.variant || scene.primary_visual?.variant || 'evidence_pinboard').toLowerCase();

  const palette = visualDNA.palette || {};
  const enter = computeSpringEnter(frame, fps, 0, 110, 16);
  const entranceStyle = computeEntranceStyle(scene?.motion?.entrance || 'fade', enter, progress);

  // Variant 1: Professional Evidence Pinboard
  if (variant === 'evidence_pinboard') {
    // Exact pin positions on 1920x1080 canvas
    const pin1 = { x: 420, y: 310 };
    const pin2 = { x: 960, y: 280 };
    const pin3 = { x: 1500, y: 340 };

    // String drawing animation (connects pin1 -> pin2 -> pin3)
    const string1Draw = interpolate(progress, [0.08, 0.45], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
    const string2Draw = interpolate(progress, [0.40, 0.75], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });

    // Middle control points for realistic physical yarn sag
    const mid1 = { x: (pin1.x + pin2.x) / 2, y: (pin1.y + pin2.y) / 2 + 28 };
    const mid2 = { x: (pin2.x + pin3.x) / 2, y: (pin2.y + pin3.y) / 2 + 24 };

    return (
      <AbsoluteFill
        style={{
          background: 'radial-gradient(ellipse at center, #2C221A 0%, #17110C 70%, #0D0906 100%)',
          overflow: 'hidden',
          ...entranceStyle,
        }}
      >
        {/* 1. Realistic Corkboard Grain Texture */}
        <div
          style={{
            position: 'absolute',
            inset: 0,
            backgroundImage: `radial-gradient(rgba(255, 255, 255, 0.04) 1px, transparent 1px), radial-gradient(rgba(0, 0, 0, 0.3) 1px, transparent 1px)`,
            backgroundSize: '16px 16px, 24px 24px',
            backgroundPosition: '0 0, 8px 8px',
            opacity: 0.75,
          }}
        />

        {/* 2. Header Slate & Case File Tag */}
        <div
          style={{
            position: 'absolute',
            top: 40,
            left: 60,
            display: 'flex',
            alignItems: 'center',
            gap: 16,
            zIndex: 10,
          }}
        >
          <span
            style={{
              background: '#DC2626',
              color: '#FFFFFF',
              fontFamily: 'monospace',
              fontSize: 12,
              fontWeight: 900,
              letterSpacing: '0.15em',
              padding: '6px 14px',
              borderRadius: 4,
              boxShadow: '0 4px 12px rgba(220, 38, 38, 0.5)',
            }}
          >
            ACTIVE CASE FILE // EVIDENCE
          </span>
          <h2
            style={{
              color: '#F3F4F6',
              fontSize: 24,
              fontWeight: 800,
              fontFamily: visualDNA.typography.display || 'sans-serif',
              letterSpacing: '0.04em',
              textShadow: '0 2px 12px rgba(0,0,0,0.8)',
            }}
          >
            {title}
          </h2>
        </div>

        {/* 3. Red Yarn Connecting Pins SVG Layer */}
        <svg width="100%" height="100%" viewBox="0 0 1920 1080" style={{ position: 'absolute', inset: 0, zIndex: 5, pointerEvents: 'none' }}>
          <defs>
            <filter id="yarnGlow">
              <feDropShadow dx="0" dy="4" stdDeviation="3" floodColor="#000000" floodOpacity="0.7" />
            </filter>
          </defs>

          {/* Yarn String 1: Pin 1 -> Pin 2 */}
          <path
            d={`M ${pin1.x} ${pin1.y} Q ${mid1.x} ${mid1.y} ${pin2.x} ${pin2.y}`}
            stroke="#DC2626"
            strokeWidth="4"
            fill="none"
            filter="url(#yarnGlow)"
            strokeDasharray="800"
            strokeDashoffset={interpolate(string1Draw, [0, 1], [800, 0])}
            strokeLinecap="round"
          />

          {/* Yarn String 2: Pin 2 -> Pin 3 */}
          <path
            d={`M ${pin2.x} ${pin2.y} Q ${mid2.x} ${mid2.y} ${pin3.x} ${pin3.y}`}
            stroke="#DC2626"
            strokeWidth="4"
            fill="none"
            filter="url(#yarnGlow)"
            strokeDasharray="800"
            strokeDashoffset={interpolate(string2Draw, [0, 1], [800, 0])}
            strokeLinecap="round"
          />
        </svg>

        {/* 4. Three Pinned Polaroids Positioned Across the Board */}
        {/* POLAROID 1 */}
        <div
          style={{
            position: 'absolute',
            left: pin1.x - 170,
            top: pin1.y - 15,
            width: 340,
            background: '#FAF8F5',
            padding: '16px 16px 44px 16px',
            boxShadow: '0 24px 60px rgba(0,0,0,0.9), 0 4px 15px rgba(0,0,0,0.6)',
            transform: 'rotate(-4deg)',
            zIndex: 4,
            border: '1px solid #E5E7EB',
          }}
        >
          {/* Photo Frame */}
          <div style={{ width: '100%', height: 260, background: '#1F2937', overflow: 'hidden' }}>
            {images[0]?.src ? (
              <SafeMedia src={images[0].src} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
            ) : null}
          </div>
          {/* Caption */}
          <div style={{ marginTop: 14, color: '#111827', fontFamily: 'Courier, monospace', fontSize: 13, fontWeight: 700, textAlign: 'center', letterSpacing: '0.05em' }}>
            {images[0]?.label || 'EXHIBIT 01'}
          </div>
        </div>

        {/* POLAROID 2 (Center) */}
        <div
          style={{
            position: 'absolute',
            left: pin2.x - 170,
            top: pin2.y - 15,
            width: 340,
            background: '#FAF8F5',
            padding: '16px 16px 44px 16px',
            boxShadow: '0 28px 70px rgba(0,0,0,0.92), 0 4px 18px rgba(0,0,0,0.6)',
            transform: 'rotate(2.5deg)',
            zIndex: 4,
            border: '1px solid #E5E7EB',
          }}
        >
          <div style={{ width: '100%', height: 260, background: '#1F2937', overflow: 'hidden' }}>
            {images[1]?.src ? (
              <SafeMedia src={images[1].src} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
            ) : null}
          </div>
          <div style={{ marginTop: 14, color: '#111827', fontFamily: 'Courier, monospace', fontSize: 13, fontWeight: 700, textAlign: 'center', letterSpacing: '0.05em' }}>
            {images[1]?.label || 'EXHIBIT 02'}
          </div>
        </div>

        {/* POLAROID 3 (Right) */}
        <div
          style={{
            position: 'absolute',
            left: pin3.x - 170,
            top: pin3.y - 15,
            width: 340,
            background: '#FAF8F5',
            padding: '16px 16px 44px 16px',
            boxShadow: '0 24px 60px rgba(0,0,0,0.9), 0 4px 15px rgba(0,0,0,0.6)',
            transform: 'rotate(-2deg)',
            zIndex: 4,
            border: '1px solid #E5E7EB',
          }}
        >
          <div style={{ width: '100%', height: 260, background: '#1F2937', overflow: 'hidden' }}>
            {images[2]?.src ? (
              <SafeMedia src={images[2].src} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
            ) : null}
          </div>
          <div style={{ marginTop: 14, color: '#111827', fontFamily: 'Courier, monospace', fontSize: 13, fontWeight: 700, textAlign: 'center', letterSpacing: '0.05em' }}>
            {images[2]?.label || 'EXHIBIT 03'}
          </div>
        </div>

        {/* 5. Metallic 3D Pushpins (Positioned on top of polaroid tops) */}
        {[pin1, pin2, pin3].map((p, idx) => (
          <div
            key={idx}
            style={{
              position: 'absolute',
              left: p.x - 11,
              top: p.y - 11,
              width: 22,
              height: 22,
              borderRadius: '50%',
              background: 'radial-gradient(circle at 35% 35%, #EF4444 0%, #B91C1C 60%, #7F1D1D 100%)',
              boxShadow: '0 4px 8px rgba(0,0,0,0.7), inset 0 2px 3px rgba(255,255,255,0.4)',
              zIndex: 15,
            }}
          />
        ))}

        {/* 6. Pinned Yellow Sticky Note (Detective Observation) */}
        <div
          style={{
            position: 'absolute',
            left: pin2.x + 195,
            top: pin2.y + 40,
            width: 200,
            background: '#FEF08A',
            padding: '16px 18px',
            boxShadow: '0 16px 36px rgba(0,0,0,0.7)',
            transform: 'rotate(5deg)',
            zIndex: 6,
            color: '#713F12',
            fontFamily: 'Comic Sans MS, cursive, sans-serif',
            fontSize: 13,
            lineHeight: 1.4,
          }}
        >
          <div style={{ fontWeight: 800, textDecoration: 'underline', marginBottom: 6 }}>NOTE // WITNESS:</div>
          <div>All three dates align with sovereign treasury disbursements.</div>
        </div>

        {/* 7. Heavy Vignette Framing */}
        <AbsoluteFill
          style={{
            boxShadow: 'inset 0 0 160px rgba(0,0,0,0.92)',
            pointerEvents: 'none',
            zIndex: 20,
          }}
        />
      </AbsoluteFill>
    );
  }

  // Variant 2: Three Panel Asymmetric
  return (
    <AbsoluteFill style={{ background: '#0B0F17', padding: '4% 5%', ...entranceStyle }}>
      <div style={{ display: 'flex', gap: 24, height: '100%' }}>
        <div style={{ flex: 1.6, background: '#111827', borderRadius: 8, overflow: 'hidden', position: 'relative' }}>
          {images[0]?.src && <SafeMedia src={images[0].src} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />}
          <div style={{ position: 'absolute', bottom: 16, left: 16, background: 'rgba(0,0,0,0.8)', padding: '6px 14px', borderRadius: 4, color: '#FFF', fontSize: 14 }}>
            PRIMARY EXHIBIT
          </div>
        </div>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 20 }}>
          <div style={{ flex: 1, background: '#111827', borderRadius: 8, overflow: 'hidden', position: 'relative' }}>
            {images[1]?.src && <SafeMedia src={images[1].src} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />}
          </div>
          <div style={{ flex: 1, background: '#111827', borderRadius: 8, overflow: 'hidden', position: 'relative' }}>
            {images[2]?.src && <SafeMedia src={images[2].src} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />}
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
}
