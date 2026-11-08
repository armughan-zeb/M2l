import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { rgba } from '../theme';
import { EASING } from '../motion-graphics/useMotion';
import { safeFadeOutRange } from '../engine/DirectorMotion';

/**
 * WordHighlighter — Broadcast-Grade Cinematic Keyword & Fact Highlighting.
 * 
 * Supports:
 * - Vox canary-yellow marker sweep
 * - MagnatesMedia luxury gold glow
 * - Dhruv Rathee verified fact pill
 * - Classified redaction reveal
 * - Animated bracket framing [ 1962 ] or [ $50 BILLION ]
 */
export function WordHighlighter({
  scene = {},
  visualDNA = {},
  props = {},
  theme = {},
}) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const combinedProps = {
    ...(scene.graphic?.props || {}),
    ...(scene.primary_visual?.props || {}),
    ...(props || {}),
  };

  const text = combinedProps.text || combinedProps.title || combinedProps.headline || scene.narration || '';
  const kicker = combinedProps.kicker || combinedProps.label || 'KEY FACT';
  const highlightWords = combinedProps.highlight_words || combinedProps.highlightWords || combinedProps.keywords || [];
  const styleMode = String(combinedProps.style || combinedProps.variant || visualDNA.genre || 'vox-marker').toLowerCase();
  
  const palette = visualDNA.palette || theme.palette || {
    primary: '#FFFFFF',
    accent: '#FACC15',
    background: '#0F172A',
    surface: '#1E293B',
  };

  const displayFont = visualDNA.typography?.display || 'Bebas Neue, Montserrat, sans-serif';
  const bodyFont = visualDNA.typography?.body || 'Inter, Open Sans, sans-serif';

  // Entry & Exit physics
  const genrePhysics = EASING[visualDNA.genre] || EASING.vox;
  const enter = spring({ frame, fps, config: genrePhysics });
  const fadeIn = Math.min(1, frame / 8);

  const sceneDurationSec = Number(scene.duration || props.duration || 4);
  const totalFrames = Math.max(1, Math.round(Number(scene.duration_in_frames || sceneDurationSec * fps)));
  const exitDuration = 12;
  const exitP = interpolate(frame, safeFadeOutRange(totalFrames, exitDuration), [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const cardOpacity = fadeIn * (1 - exitP);
  const exitY = exitP * 30;

  // Split into chunks
  const words = text.split(/\s+/);
  const isKeyword = (w) => {
    const clean = w.toLowerCase().replace(/[^a-z0-9$%]/g, '');
    if (!clean) return false;
    if (highlightWords.some(kw => String(kw).toLowerCase().includes(clean) || clean.includes(String(kw).toLowerCase()))) {
      return true;
    }
    // Auto-detect numbers, currency, dates, percentages
    return /[$€£¥]\d+|\d+([.,]\d+)?%|\b(19\d\d|20\d\d)\b|\b\d+(?:,\d{3})*\b/.test(clean);
  };

  return (
    <AbsoluteFill style={{ pointerEvents: 'none', justifyContent: 'center', alignItems: 'center' }}>
      <div
        style={{
          maxWidth: '84%',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 16,
          opacity: cardOpacity,
          transform: `translateY(${(1 - enter) * 40 + exitY}px) scale(${0.92 + enter * 0.08})`,
        }}
      >
        {/* Kicker badge */}
        {kicker && (
          <div
            style={{
              padding: '6px 16px',
              borderRadius: 4,
              backgroundColor: rgba(palette.background || '#000', 0.75),
              border: `1px solid ${palette.accent}`,
              color: palette.accent,
              fontFamily: bodyFont,
              fontSize: 14,
              fontWeight: 800,
              letterSpacing: '0.18em',
              textTransform: 'uppercase',
              boxShadow: `0 4px 18px ${rgba(palette.accent, 0.25)}`,
            }}
          >
            {kicker}
          </div>
        )}

        {/* Narrative Words with Synchronized Highlighting */}
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            justifyContent: 'center',
            alignItems: 'center',
            gap: '0.35em',
            padding: '24px 36px',
            backgroundColor: rgba(palette.background || '#08080A', 0.88),
            borderRadius: visualDNA.cornerRadius ?? 8,
            border: `1px solid rgba(255,255,255,0.12)`,
            boxShadow: '0 20px 60px rgba(0,0,0,0.7)',
          }}
        >
          {words.map((word, i) => {
            const isTarget = isKeyword(word);
            if (!isTarget) {
              return (
                <span
                  key={i}
                  style={{
                    fontFamily: displayFont,
                    fontSize: 48,
                    fontWeight: 800,
                    color: palette.primary || '#FFFFFF',
                    lineHeight: 1.15,
                  }}
                >
                  {word}
                </span>
              );
            }

            // Target highlighted keyword
            const sweepDelay = 6 + i * 2;
            const sweep = spring({
              frame: Math.max(0, frame - sweepDelay),
              fps,
              config: { damping: 12, mass: 0.45, stiffness: 160 },
            });

            if (styleMode.includes('magnates') || styleMode.includes('gold')) {
              return (
                <span
                  key={i}
                  style={{
                    position: 'relative',
                    display: 'inline-block',
                    padding: '2px 10px',
                    fontFamily: displayFont,
                    fontSize: 52,
                    fontWeight: 900,
                    color: '#FFFFFF',
                    textShadow: `0 0 20px ${palette.accent || '#D4AF37'}`,
                    transform: `scale(${0.9 + sweep * 0.15})`,
                  }}
                >
                  <span
                    style={{
                      position: 'absolute',
                      inset: 0,
                      borderBottom: `4px solid ${palette.accent || '#D4AF37'}`,
                      boxShadow: `0 8px 24px ${rgba(palette.accent || '#D4AF37', sweep * 0.6)}`,
                      transform: `scaleX(${sweep})`,
                      transformOrigin: 'left',
                    }}
                  />
                  {word}
                </span>
              );
            }

            if (styleMode.includes('bracket')) {
              return (
                <span
                  key={i}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 4,
                    padding: '0 6px',
                    color: palette.accent || '#FACC15',
                    fontFamily: displayFont,
                    fontSize: 52,
                    fontWeight: 900,
                    transform: `scale(${0.85 + sweep * 0.15})`,
                  }}
                >
                  <span style={{ opacity: sweep }}>[</span>
                  <span>{word}</span>
                  <span style={{ opacity: sweep }}>]</span>
                </span>
              );
            }

            // Default: Vox Highlighter Sweep
            return (
              <span
                key={i}
                style={{
                  position: 'relative',
                  display: 'inline-block',
                  margin: '0 4px',
                  padding: '2px 10px',
                  fontFamily: displayFont,
                  fontSize: 50,
                  fontWeight: 900,
                  color: sweep > 0.4 ? (palette.text_contrast || '#0F172A') : '#FFFFFF',
                  zIndex: 1,
                  transition: 'color 0.12s ease',
                }}
              >
                <span
                  style={{
                    position: 'absolute',
                    inset: 0,
                    backgroundColor: palette.accent || '#FACC15',
                    borderRadius: 3,
                    zIndex: -1,
                    transform: `scaleX(${sweep})`,
                    transformOrigin: 'left',
                    boxShadow: `0 4px 16px ${rgba(palette.accent || '#FACC15', 0.45)}`,
                  }}
                />
                {word}
              </span>
            );
          })}
        </div>
      </div>
    </AbsoluteFill>
  );
}

