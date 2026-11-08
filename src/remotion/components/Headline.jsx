import React from 'react';
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { computeSpringEnter, computeEntranceStyle } from '../engine/MotionEngine';
import { rgba } from '../theme';

/**
 * Headline — Universal Newspaper & Press Evidence Engine.
 * Supported variants:
 * - newspaper_front_page
 * - digital_article_crop
 * - floating_press_clipping
 * - headline_stack_carousel
 * - magnified_press_quote
 */
export function Headline({ scene = {}, visualDNA = {}, props = {} }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const sceneDuration = Math.max(1, scene?.duration_in_frames !== undefined && scene?.duration_in_frames !== null
    ? Math.round(Number(scene.duration_in_frames))
    : Math.round(Number(scene?.duration || 4) * fps));
  const progress = Math.min(1, Math.max(0, frame / Math.max(1, sceneDuration - 1)));

  const publication = String(props.publication || props.source || 'THE DAILY CHRONICLE').toUpperCase();
  const hasExplicitCopy = ['display_text', '_display_text', 'headline', 'title'].some((key) => Object.prototype.hasOwnProperty.call(props, key));
  const headline = String(props.headline || props.title || scene.text?.headline || (hasExplicitCopy ? props.display_text : scene.narration) || '').trim();
  const subdeck = String(props.subdeck || props.subtext || scene.text?.subtext || '').trim();
  const date = String(props.date || props.timestamp || '').toUpperCase();
  const variant = String(props.variant || scene.primary_visual?.variant || 'newspaper_front_page').toLowerCase();

  if (!headline && hasExplicitCopy && !props.source_capture && !props.sourceCapture) return null;

  const palette = visualDNA.palette || {};
  const cornerRadius = visualDNA.cornerRadius ?? 4;
  const enter = computeSpringEnter(frame, fps, 0, scene?.motion?.spring_stiffness || 120, scene?.motion?.spring_damping || 18);
  // Strict editorial isolation: motion graphics smoothly exit before scene cut, zero bleed into next scene
  const entranceStyle = computeEntranceStyle(scene?.motion?.entrance || 'fade', enter, progress, true);

  // Variant 1: Breaking News Broadcast Ticker (Cable / TV investigative ribbon)
  if (variant === 'breaking_news_ticker' || variant === 'broadcast_ribbon') {
    return (
      <AbsoluteFill style={{ justifyContent: 'flex-end', background: 'transparent', paddingBottom: '5%', ...entranceStyle }}>
        <div style={{
          width: '92%',
          margin: '0 auto',
          background: `linear-gradient(90deg, rgba(15, 23, 42, 0.96) 0%, rgba(10, 15, 25, 0.92) 100%)`,
          borderTop: `4px solid ${palette.accent || '#E11D48'}`,
          borderRadius: visualDNA.cornerRadius > 8 ? 16 : 2,
          boxShadow: '0 24px 60px rgba(0,0,0,0.9)',
          display: 'flex',
          alignItems: 'stretch',
          overflow: 'hidden',
        }}>
          <div style={{
            background: palette.accent || '#E11D48',
            color: palette.text_contrast || '#FFFFFF',
            padding: '16px 26px',
            fontFamily: visualDNA.typography.display,
            fontWeight: 900,
            fontSize: 22,
            letterSpacing: '0.12em',
            display: 'flex',
            alignItems: 'center',
            textTransform: 'uppercase',
            whiteSpace: 'nowrap',
          }}>
            BREAKING NEWS
          </div>
          <div style={{ padding: '14px 28px', flex: 1 }}>
            <div style={{ color: palette.primary, fontFamily: visualDNA.typography.display, fontSize: 26, fontWeight: 800, textTransform: 'uppercase', lineHeight: 1.2 }}>
              {headline}
            </div>
            {subdeck && (
              <div style={{ color: palette.secondary, fontFamily: visualDNA.typography.body, fontSize: 16, marginTop: 4 }}>
                {subdeck}
              </div>
            )}
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 2: Digital Article Crop (Modern web news)
  if (variant === 'digital_article_crop') {
    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: 'transparent', ...entranceStyle }}>
        <div
          style={{
            width: '78%',
            background: `linear-gradient(135deg, ${rgba(palette.surface || '#161b22', 0.88)} 0%, ${rgba(palette.background || '#0d1117', 0.94)} 100%)`,
            border: `1px solid ${rgba(palette.border || palette.accent, 0.35)}`,
            borderRadius: cornerRadius || 16,
            padding: '48px 56px',
            boxShadow: `0 28px 70px rgba(0,0,0,0.8), 0 0 35px ${rgba(palette.accent || '#e15444', 0.15)}`,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: `1px solid ${rgba(palette.border || '#333', 0.5)}`, paddingBottom: 18, marginBottom: 24 }}>
            <div style={{ fontFamily: visualDNA.typography.display, fontSize: 22, fontWeight: 800, color: palette.accent, letterSpacing: '0.04em' }}>
              {publication}
            </div>
            <div style={{ fontFamily: visualDNA.typography.body, fontSize: 14, color: palette.secondary }}>
              {date} • 4 MIN READ
            </div>
          </div>
          <div style={{ fontFamily: visualDNA.typography.display, fontSize: 44, fontWeight: 800, color: palette.primary, lineHeight: 1.25, marginBottom: 20, textShadow: '0 2px 14px rgba(0,0,0,0.8)' }}>
            {headline}
          </div>
          {subdeck && (
            <div style={{ fontFamily: visualDNA.typography.body, fontSize: 20, color: palette.secondary, lineHeight: 1.45 }}>
              {subdeck}
            </div>
          )}
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 2: Floating Press Clipping (Angled archival snippet)
  if (variant === 'floating_press_clipping') {
    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: 'transparent', ...entranceStyle }}>
        <div
          style={{
            width: '68%',
            background: '#F6F3E9',
            color: '#1A202C',
            padding: '44px 50px',
            borderRadius: 4,
            transform: `rotate(-2deg) translateY(${interpolate(progress, [0, 1], [15, -15])}px)`,
            boxShadow: '0 28px 70px rgba(0,0,0,0.9), 0 2px 10px rgba(0,0,0,0.5)',
            borderTop: '6px solid #2D3748',
          }}
        >
          <div style={{ fontSize: 13, fontFamily: 'monospace', letterSpacing: '0.18em', color: '#718096', marginBottom: 14 }}>
            {publication} — {date}
          </div>
          <div style={{ fontFamily: 'Georgia, serif', fontSize: 38, fontWeight: 800, lineHeight: 1.2, color: '#1A202C' }}>
            {headline}
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 3: Headline Stack Carousel (3 sequential headlines)
  if (variant === 'headline_stack_carousel') {
    const headlines = props.headlines || [
      { pub: 'FINANCIAL TIMES', text: 'MARKETS TUMBLE AMID MOUNTING LIQUIDITY FEARS' },
      { pub: 'THE WALL STREET JOURNAL', text: headline },
      { pub: 'REUTERS', text: 'GLOBAL CENTRAL BANKS PLEDGE EMERGENCY INTERVENTION' },
    ];
    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: 'transparent', ...entranceStyle }}>
        <div style={{ width: '80%', display: 'flex', flexDirection: 'column', gap: 24 }}>
          {headlines.map((item, idx) => (
            <div
              key={idx}
              style={{
                background: idx === 1 ? `linear-gradient(135deg, ${rgba(palette.surface || '#161b22', 0.9)} 0%, ${rgba(palette.background || '#0d1117', 0.94)} 100%)` : rgba(palette.surface || '#161b22', 0.6),
                border: `1px solid ${idx === 1 ? palette.accent : rgba(palette.border || '#444', 0.4)}`,
                padding: '24px 32px',
                borderRadius: cornerRadius || 16,
                boxShadow: idx === 1 ? `0 16px 40px rgba(0,0,0,0.7), 0 0 25px ${rgba(palette.accent, 0.25)}` : '0 8px 24px rgba(0,0,0,0.4)',
              }}
            >
              <div style={{ color: idx === 1 ? palette.accent : palette.secondary, fontSize: 13, fontFamily: visualDNA.typography.body, letterSpacing: '0.12em', marginBottom: 8 }}>
                {item.pub}
              </div>
              <div style={{ color: palette.primary, fontSize: idx === 1 ? 30 : 22, fontWeight: 700, fontFamily: visualDNA.typography.display, textShadow: '0 2px 10px rgba(0,0,0,0.8)' }}>
                {item.text}
              </div>
            </div>
          ))}
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 4: Magnified Press Quote
  if (variant === 'magnified_press_quote') {
    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: 'transparent', ...entranceStyle }}>
        <div style={{
          width: '82%',
          textAlign: 'center',
          padding: '48px 56px',
          background: `radial-gradient(ellipse at center, ${rgba(palette.surface || palette.background || '#0d1216', 0.86)} 0%, ${rgba(palette.background || '#0d1216', 0.5)} 70%, transparent 100%)`,
          borderRadius: 24,
        }}>
          <div style={{ color: palette.accent, fontSize: 18, fontFamily: visualDNA.typography.body, letterSpacing: '0.18em', textTransform: 'uppercase', marginBottom: 16, textShadow: '0 2px 10px rgba(0,0,0,0.8)' }}>
            {publication} // INVESTIGATIVE REPORT
          </div>
          <div
            style={{
              fontFamily: visualDNA.typography.display,
              fontSize: 54,
              fontWeight: 800,
              color: palette.primary,
              lineHeight: 1.25,
              textShadow: '0 4px 25px rgba(0,0,0,0.95)',
            }}
          >
            "{headline}"
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 5: Newspaper Front Page (Classic Archival Broadsheet)
  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: 'transparent', ...entranceStyle }}>
      <div
        style={{
          width: '82%',
          background: '#F5F1E4',
          color: '#111827',
          padding: '44px 52px',
          borderRadius: 4,
          boxShadow: '0 32px 80px rgba(0,0,0,0.9), 0 0 40px rgba(0,0,0,0.4)',
          border: '2px solid #1F2937',
        }}
      >
        <div style={{ textAlign: 'center', borderBottom: '3px double #1F2937', paddingBottom: 14, marginBottom: 16 }}>
          <div style={{ fontFamily: 'Georgia, serif', fontSize: 44, fontWeight: 900, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
            {publication}
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, fontFamily: 'monospace', marginTop: 8, color: '#4B5563' }}>
            <span>VOL. CXXIV NO. 42</span>
            <span>{date}</span>
            <span>PRICE FIVE CENTS</span>
          </div>
        </div>
        <div style={{ fontFamily: 'Georgia, serif', fontSize: 48, fontWeight: 900, lineHeight: 1.15, textAlign: 'center', margin: '20px 0', textTransform: 'uppercase' }}>
          {headline}
        </div>
        {subdeck && (
          <div style={{ fontFamily: 'sans-serif', fontSize: 18, textAlign: 'center', color: '#374151', borderTop: '1px solid #9CA3AF', paddingTop: 12 }}>
            {subdeck}
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
}

