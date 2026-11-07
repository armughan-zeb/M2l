import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { rgba } from '../theme';
import { safeFadeOutRange } from '../engine/DirectorMotion';

/** Open, source-led quotation typography; legacy card variants resolve here. */
export function QuoteCard({ scene = {}, visualDNA = {}, props = {} }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const duration = Math.max(1, Number(scene.duration_in_frames || Math.round(Number(scene.duration || 4) * fps)));
  const enter = spring({ frame, fps, config: { damping: 17, mass: 0.38, stiffness: 160 } });
  const exit = interpolate(frame, safeFadeOutRange(duration, Math.round(fps * 0.28)), [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const quote = String(props.quote || props.text || scene.text?.primary || scene.narration || '').trim();
  const author = String(props.author || props.speaker || scene.text?.headline || '').trim();
  const role = String(props.role || props.attribution || scene.text?.subtext || '').trim();
  const palette = visualDNA.palette || {};
  const typography = visualDNA.typography || {};
  const position = String(props.position || scene.placement?.position || 'center').toLowerCase();
  const horizontal = position.includes('right') ? 'right' : position.includes('left') ? 'left' : 'center';
  const vertical = position.includes('top') ? 'flex-start' : position.includes('bottom') ? 'flex-end' : 'center';
  const alignment = horizontal === 'center' ? 'center' : horizontal;
  const reveal = Math.max(0, Math.min(1, Number(enter)));
  const variant = String(props.variant || scene.primary_visual?.variant || 'editorial_large_quote').toLowerCase();
  const punch = variant === 'quote_fragment_punch';

  if (!quote) return null;

  return (
    <AbsoluteFill style={{ justifyContent: punch ? 'center' : vertical, alignItems: punch ? 'flex-start' : horizontal === 'center' ? 'center' : horizontal === 'right' ? 'flex-end' : 'flex-start', padding: punch ? '10% 12%' : '8% 9%', color: palette.primary || '#F8FAFC', opacity: reveal * exit }}>
      <div style={{ width: '100%', maxWidth: punch ? 1320 : 1420, textAlign: punch ? 'left' : alignment, transform: `translateY(${(1 - reveal) * 22}px)`, borderLeft: punch ? `6px solid ${palette.accent || '#E6B84A'}` : undefined, paddingLeft: punch ? 32 : 0 }}>
        <div style={{ width: punch ? 148 * reveal : 76 * reveal, height: punch ? 5 : 3, background: palette.accent || '#E6B84A', margin: punch ? '0 0 30px' : horizontal === 'center' ? '0 auto 25px' : horizontal === 'right' ? '0 0 25px auto' : '0 0 25px' }} />
        <div style={{ fontFamily: typography.display || 'Archivo Black', fontSize: punch ? (quote.length > 130 ? 54 : quote.length > 75 ? 68 : 88) : quote.length > 180 ? 44 : quote.length > 105 ? 54 : 68, fontWeight: 700, lineHeight: punch ? 1.02 : 1.08, letterSpacing: '-.04em', textWrap: 'balance', textShadow: `0 4px 28px ${rgba(palette.background || '#05070E', .78)}` }}>
          “{quote}”
        </div>
        {(author || role) && (
          <div style={{ display: 'flex', flexDirection: horizontal === 'right' ? 'row-reverse' : 'row', justifyContent: horizontal === 'center' ? 'center' : horizontal === 'right' ? 'flex-start' : 'flex-end', flexWrap: 'wrap', gap: '6px 12px', marginTop: 24, color: palette.secondary || '#CBD5E1', fontFamily: typography.body || 'Inter', fontSize: 18, fontWeight: 600 }}>
            {author && <span style={{ color: palette.primary || '#F8FAFC' }}>{author}</span>}
            {role && <span>{role}</span>}
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
}
