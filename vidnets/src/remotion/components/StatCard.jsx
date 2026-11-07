import React from 'react';
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { computeSpringEnter, computeEntranceStyle } from '../engine/MotionEngine';
import { resolveCardPlacement } from '../engine/LayoutEngine';
import { rgba } from '../theme';
import { safeFadeOutRange } from '../engine/DirectorMotion';

/**
 * StatCard — Universal Sleek Documentary Numeric Counter & KPI Visualization Engine.
 * Supports compact 4-corner placement (top-left, top-right, bottom-left, bottom-right).
 * Supported variants:
 * - hero_counter_large
 * - percentage_ring
 * - kpi_metric_card
 * - delta_growth_badge
 * - multi_stat_grid
 * - stat_with_context
 */
export function StatCard({ scene = {}, visualDNA = {}, props = {} }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const sceneDuration = Math.max(1, scene?.duration_in_frames !== undefined && scene?.duration_in_frames !== null
    ? Math.round(Number(scene.duration_in_frames))
    : Math.round(Number(scene?.duration || 4) * fps));
  const progress = Math.min(1, Math.max(0, frame / Math.max(1, sceneDuration - 1)));

  const rawTargetValue = props.value ?? props.target;
  const parsedTargetValue = typeof rawTargetValue === 'number'
    ? rawTargetValue
    : Number(String(rawTargetValue ?? '').replace(/[^0-9.-]/g, ''));
  if (rawTargetValue == null || !Number.isFinite(parsedTargetValue)) return null;
  const targetValue = parsedTargetValue;
  const startValue = Number(props.startValue ?? props.start ?? 0);
  const prefix = String(props.prefix ?? '');
  const suffix = String(props.suffix ?? props.unit ?? '');
  const rawLabel = String(props.label ?? props.title ?? scene.text?.headline ?? 'KEY METRIC');
  const label = (rawLabel === String(targetValue) || rawLabel === String(props.value))
    ? (props.category || props.kicker || 'OFFICIAL RECORD')
    : rawLabel;
  const kicker = String(props.kicker ?? scene.text?.kicker ?? '');
  const variant = String(props.variant || scene.primary_visual?.variant || 'hero_counter_large').toLowerCase();

  const palette = visualDNA.palette || {};
  const cornerRadius = visualDNA.cornerRadius ?? 8;
  const enter = computeSpringEnter(frame, fps, 0, scene?.motion?.spring_stiffness || 175, scene?.motion?.spring_damping || 15);
  // Strict editorial isolation: motion graphics smoothly exit before scene cut, zero bleed into next scene
  const entranceStyle = computeEntranceStyle(scene?.motion?.entrance || 'fade', enter, progress, true);

  const hasMedia = Boolean(scene?.media?.src || scene?.media?.path);
  const placement = resolveCardPlacement(scene, props, 'bottom_left');
  const accentColor = palette.accent || '#D4AF37';
  const cardMaxWidth = hasMedia ? '400px' : '520px';

  // Animate counter roll over first 65% of scene with exponential deceleration
  const rollProgress = Math.min(1, Math.max(0, frame / Math.max(1, sceneDuration * 0.65)));
  const easeOutExpo = (t) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t));
  const currentNumeric = interpolate(easeOutExpo(rollProgress), [0, 1], [startValue, targetValue]);
  const formattedNumber = props.decimals
    ? currentNumeric.toFixed(Number(props.decimals))
    : Math.round(currentNumeric).toLocaleString();

  // Exit Animation (12 frames prior to scene end)
  const exitP = interpolate(frame, safeFadeOutRange(sceneDuration, 12), [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const exitFade = 1 - exitP;
  const exitY = exitP * 35;
  const cardExitTransform = `translateY(${exitY}px)`;
  const finalOpacity = (entranceStyle.opacity !== undefined ? Number(entranceStyle.opacity) : 1) * exitFade;

  const borderAccentStyle = hasMedia
    ? (placement.isRight ? { borderRight: `4px solid ${accentColor}` } : { borderLeft: `4px solid ${accentColor}` })
    : {};

  // Variant 1: Percentage Ring
  if (variant === 'percentage_ring') {
    const radius = 130;
    const circumference = 2 * Math.PI * radius;
    const strokeDashoffset = circumference - (Math.min(100, Math.max(0, currentNumeric)) / 100) * circumference;
    return (
      <AbsoluteFill style={{ ...placement.containerStyle, background: 'transparent', ...entranceStyle, opacity: finalOpacity, transform: `${entranceStyle.transform || ''} ${cardExitTransform}` }}>
        <div style={{
          position: 'relative',
          width: 320,
          height: 320,
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          background: `linear-gradient(135deg, ${rgba(palette.surface || '#161b22', 0.88)} 0%, ${rgba(palette.background || '#0d1117', 0.94)} 100%)`,
          borderRadius: 20,
          border: `1px solid ${rgba(palette.border || accentColor, 0.35)}`,
          boxShadow: `0 20px 50px rgba(0,0,0,0.8), 0 0 30px ${rgba(accentColor, 0.12)}`,
        }}>
          <svg width="290" height="290" style={{ transform: 'rotate(-90deg)' }}>
            <circle cx="145" cy="145" r={radius} fill="none" stroke={rgba(palette.border || '#333', 0.5)} strokeWidth="14" />
            <circle
              cx="145"
              cy="145"
              r={radius}
              fill="none"
              stroke={accentColor}
              strokeWidth="14"
              strokeDasharray={circumference}
              strokeDashoffset={strokeDashoffset}
              strokeLinecap="round"
            />
          </svg>
          <div style={{ position: 'absolute', textAlign: 'center' }}>
            <div style={{ fontFamily: visualDNA.typography.numbers, fontSize: 60, fontWeight: 900, color: palette.primary, textShadow: '0 2px 16px rgba(0,0,0,0.8)' }}>
              {Math.round(currentNumeric)}%
            </div>
            <div style={{ fontFamily: visualDNA.typography.body, fontSize: 14, color: palette.secondary, textTransform: 'uppercase', letterSpacing: '0.1em', marginTop: 4, textShadow: '0 2px 8px rgba(0,0,0,0.8)' }}>
              {label}
            </div>
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 2: KPI Metric Card
  if (variant === 'kpi_metric_card') {
    const delta = props.delta == null ? '' : String(props.delta);
    const isPositive = !delta.startsWith('-');
    const isBrutalist = visualDNA.geometry?.includes('brutalist');
    const isPill = visualDNA.geometry?.includes('pill') || visualDNA.cornerRadius > 12;

    return (
      <AbsoluteFill style={{ ...placement.containerStyle, background: 'transparent', ...entranceStyle, opacity: finalOpacity, transform: `${entranceStyle.transform || ''} ${cardExitTransform}` }}>
        <div
          style={{
            background: `linear-gradient(135deg, ${rgba(palette.surface || '#161b22', 0.92)} 0%, ${rgba(palette.background || '#0d1117', 0.96)} 100%)`,
            border: isBrutalist ? `3px solid ${accentColor}` : `1px solid ${rgba(palette.border || accentColor, 0.35)}`,
            borderLeft: isBrutalist ? `8px solid ${accentColor}` : (hasMedia && !placement.isRight ? `4px solid ${accentColor}` : undefined),
            borderRight: hasMedia && placement.isRight ? `4px solid ${accentColor}` : undefined,
            padding: hasMedia ? '18px 24px' : '32px 40px',
            borderRadius: isPill ? 24 : (isBrutalist ? 0 : (visualDNA.cornerRadius || 8)),
            boxShadow: `0 20px 50px rgba(0,0,0,0.85), 0 0 25px ${rgba(accentColor, 0.15)}`,
            textAlign: placement.cardStyle.textAlign,
            maxWidth: hasMedia ? '380px' : '500px',
          }}
        >
          {kicker && (
            <div style={{ color: accentColor, fontSize: 12, fontFamily: visualDNA.typography.body, letterSpacing: '0.16em', textTransform: 'uppercase', marginBottom: 6, fontWeight: 700 }}>
              {kicker}
            </div>
          )}
          <div style={{ color: palette.secondary, fontSize: 14, fontFamily: visualDNA.typography.body, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>
            {label}
          </div>
          <div style={{ fontFamily: visualDNA.typography.numbers, fontSize: hasMedia ? 58 : 76, fontWeight: 900, color: palette.primary, lineHeight: 1.05 }}>
            {prefix}{formattedNumber}{suffix}
          </div>
          {delta && (
            <div
              style={{
                display: 'inline-block',
                marginTop: 10,
                padding: '4px 12px',
                borderRadius: isBrutalist ? 0 : 16,
                background: isPositive ? 'rgba(16, 185, 129, 0.2)' : 'rgba(239, 68, 68, 0.2)',
                border: `1px solid ${isPositive ? 'rgba(16, 185, 129, 0.5)' : 'rgba(239, 68, 68, 0.5)'}`,
                color: isPositive ? '#10B981' : '#EF4444',
                fontFamily: visualDNA.typography.numbers,
                fontWeight: 800,
                fontSize: 13,
              }}
            >
              {isPositive ? '▲ ' : '▼ '}{delta} {props.deltaLabel || 'vs prev period'}
            </div>
          )}
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 3: Vox Solid Stat Block (High-contrast Canary Yellow block with black typography)
  if (variant === 'vox_stat_block' || variant === 'solid_block') {
    return (
      <AbsoluteFill style={{ ...placement.containerStyle, background: 'transparent', ...entranceStyle, opacity: finalOpacity, transform: `${entranceStyle.transform || ''} ${cardExitTransform}` }}>
        <div
          style={{
            background: accentColor,
            color: palette.text_contrast || '#000000',
            padding: hasMedia ? '20px 28px' : '36px 48px',
            borderRadius: visualDNA.cornerRadius > 8 ? 20 : 0,
            boxShadow: '0 20px 50px rgba(0,0,0,0.85)',
            textAlign: placement.cardStyle.textAlign,
            maxWidth: hasMedia ? '400px' : '520px',
          }}
        >
          {kicker && (
            <div style={{ color: 'rgba(0,0,0,0.7)', fontSize: 13, fontFamily: visualDNA.typography.display, letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 4, fontWeight: 800 }}>
              {kicker}
            </div>
          )}
          <div
            style={{
              fontFamily: visualDNA.typography.numbers,
              fontSize: hasMedia ? 72 : 96,
              fontWeight: 900,
              color: '#000000',
              lineHeight: 1,
              letterSpacing: '-0.02em',
            }}
          >
            {prefix}{formattedNumber}{suffix}
          </div>
          <div
            style={{
              marginTop: 8,
              fontFamily: visualDNA.typography.display,
              fontSize: 16,
              color: 'rgba(0,0,0,0.85)',
              letterSpacing: '0.1em',
              textTransform: 'uppercase',
              fontWeight: 800,
            }}
          >
            {label}
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 4: Floating Hero Counter (NO box! Just clean, massive typography over footage)
  if (variant === 'floating_hero_counter' || variant === 'minimal_floating') {
    return (
      <AbsoluteFill style={{ ...placement.containerStyle, background: 'transparent', ...entranceStyle, opacity: finalOpacity, transform: `${entranceStyle.transform || ''} ${cardExitTransform}` }}>
        <div
          style={{
            padding: '12px 16px',
            textAlign: placement.cardStyle.textAlign,
            maxWidth: cardMaxWidth,
          }}
        >
          {kicker && (
            <div style={{ color: accentColor, fontSize: 13, fontFamily: visualDNA.typography.body, letterSpacing: '0.2em', textTransform: 'uppercase', marginBottom: 6, fontWeight: 700, textShadow: '0 2px 10px rgba(0,0,0,0.95)' }}>
              {kicker}
            </div>
          )}
          <div
            style={{
              fontFamily: visualDNA.typography.numbers,
              fontSize: hasMedia ? 84 : 130,
              fontWeight: 900,
              color: accentColor,
              lineHeight: 0.95,
              textShadow: '0 4px 24px rgba(0,0,0,0.95), 0 2px 8px rgba(0,0,0,0.9)',
            }}
          >
            {prefix}{formattedNumber}{suffix}
          </div>
          <div
            style={{
              marginTop: 8,
              fontFamily: visualDNA.typography.body,
              fontSize: 16,
              color: palette.primary,
              letterSpacing: '0.12em',
              textTransform: 'uppercase',
              fontWeight: 600,
              textShadow: '0 2px 10px rgba(0,0,0,0.95)',
            }}
          >
            {label}
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 5: Delta Growth Badge (Before vs After)
  if (variant === 'delta_growth_badge') {
    const fromVal = props.fromVal ?? '$8B';
    const toVal = props.toVal ?? '$394B';
    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: 'transparent', ...entranceStyle, opacity: finalOpacity, transform: `${entranceStyle.transform || ''} ${cardExitTransform}` }}>
        <div style={{
          background: `linear-gradient(135deg, ${rgba(palette.surface || '#161b22', 0.9)} 0%, ${rgba(palette.background || '#0d1117', 0.95)} 100%)`,
          border: `1px solid ${rgba(palette.border || accentColor, 0.35)}`,
          borderTop: `3px solid ${accentColor}`,
          padding: '36px 52px',
          borderRadius: visualDNA.cornerRadius || 12,
          boxShadow: `0 24px 60px rgba(0,0,0,0.85), 0 0 30px ${rgba(accentColor, 0.15)}`,
        }}>
          <div style={{ textAlign: 'center', marginBottom: 24 }}>
            {kicker && <div style={{ color: accentColor, fontSize: 13, letterSpacing: '0.15em', textTransform: 'uppercase', marginBottom: 6 }}>{kicker}</div>}
            <div style={{ color: palette.primary, fontSize: 32, fontFamily: visualDNA.typography.display }}>{label}</div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 36 }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{ color: palette.secondary, fontSize: 15, fontFamily: visualDNA.typography.body }}>{props.fromLabel || 'START'}</div>
              <div style={{ fontFamily: visualDNA.typography.numbers, fontSize: 52, color: palette.secondary, fontWeight: 700 }}>{fromVal}</div>
            </div>
            <div style={{ fontSize: 32, color: accentColor }}>➔</div>
            <div style={{ textAlign: 'center' }}>
              <div style={{ color: accentColor, fontSize: 15, fontFamily: visualDNA.typography.body }}>{props.toLabel || 'GROWTH'}</div>
              <div style={{ fontFamily: visualDNA.typography.numbers, fontSize: 72, color: palette.primary, fontWeight: 900 }}>
                {toVal}
              </div>
            </div>
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 6: Hero Counter Large (Default Sleek Card)
  return (
    <AbsoluteFill style={{ ...placement.containerStyle, background: 'transparent', ...entranceStyle, opacity: finalOpacity, transform: `${entranceStyle.transform || ''} ${cardExitTransform}` }}>
      <div
        style={{
          background: `linear-gradient(135deg, ${rgba(palette.surface || '#161b22', 0.88)} 0%, ${rgba(palette.background || '#0d1117', 0.94)} 100%)`,
          borderTop: `1px solid ${rgba(palette.border || accentColor, 0.35)}`,
          borderBottom: `1px solid ${rgba(palette.border || accentColor, 0.35)}`,
          padding: hasMedia ? '20px 28px' : '36px 48px',
          borderRadius: visualDNA.cornerRadius || 8,
          boxShadow: `0 20px 50px rgba(0,0,0,0.85), 0 0 25px ${rgba(accentColor, 0.15)}`,
          textAlign: placement.cardStyle.textAlign,
          maxWidth: hasMedia ? '400px' : '80%',
          ...borderAccentStyle,
        }}
      >
        {kicker && (
          <div style={{ color: accentColor, fontSize: 13, fontFamily: visualDNA.typography.body, letterSpacing: '0.16em', textTransform: 'uppercase', marginBottom: 6 }}>
            {kicker}
          </div>
        )}
        <div
          style={{
            fontFamily: visualDNA.typography.numbers,
            fontSize: hasMedia ? 72 : 110,
            fontWeight: 900,
            color: palette.primary,
            letterSpacing: '-0.02em',
            lineHeight: 1,
          }}
        >
          {prefix}{formattedNumber}{suffix}
        </div>
        <div
          style={{
            marginTop: 10,
            fontFamily: visualDNA.typography.body,
            fontSize: 15,
            color: palette.secondary,
            letterSpacing: '0.1em',
            textTransform: 'uppercase',
          }}
        >
          {label}
        </div>
      </div>
    </AbsoluteFill>
  );
}

