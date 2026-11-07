import React from 'react';
import { AbsoluteFill, Img, OffthreadVideo, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { computeSpringEnter, computeEntranceStyle } from '../engine/MotionEngine';
import { rgba } from '../theme';

const VIDEO_EXTENSIONS = /\.(?:mp4|webm|mov|mkv|avi|m4v)(?:[?#]|$)/i;

/**
 * Comparison — Universal Comparative Analysis Engine (A vs B, Before / After).
 * Supported variants:
 * - split_slider_before_after
 * - side_by_side_cards
 * - versus_contrast_clash
 * - dual_metric_comparison
 */
export function Comparison({ scene = {}, visualDNA = {}, props = {} }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const sceneDuration = Math.max(1, scene?.duration_in_frames !== undefined && scene?.duration_in_frames !== null
    ? Math.round(Number(scene.duration_in_frames))
    : Math.round(Number(scene?.duration || 4) * fps));
  const progress = Math.min(1, Math.max(0, frame / Math.max(1, sceneDuration - 1)));

  const title = String(props.title || scene.text?.headline || 'COMPARATIVE ANALYSIS');
  const leftLabel = String(props.leftLabel || props.from || 'BEFORE');
  const rightLabel = String(props.rightLabel || props.to || 'AFTER');
  const leftValue = String(props.leftValue || props.left || '');
  const rightValue = String(props.rightValue || props.right || '');
  const variant = String(props.variant || scene.primary_visual?.variant || 'side_by_side_cards').toLowerCase();

  const hasGroundedComparison = Boolean(
    (props.leftImage || props.beforeImage || props.images?.[0]?.src)
    && (props.rightImage || props.afterImage || props.images?.[1]?.src)
  ) || Boolean(leftValue && rightValue);
  if (!hasGroundedComparison) return null;

  const palette = visualDNA.palette || {};
  const cornerRadius = visualDNA.cornerRadius ?? 4;
  const enter = computeSpringEnter(frame, fps, 0, scene?.motion?.spring_stiffness || 120, scene?.motion?.spring_damping || 18);
  const entranceStyle = computeEntranceStyle(scene?.motion?.entrance || 'fade', enter, progress);

  // Variant 1: Split Slider Before/After
  if (variant === 'split_slider_before_after') {
    const sliderX = interpolate(progress, [0.15, 0.85], [30, 70], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
    const leftImage = props.leftImage || props.beforeImage || (props.images && props.images[0]?.src);
    const rightImage = props.rightImage || props.afterImage || (props.images && props.images[1]?.src);

    return (
      <AbsoluteFill style={{ background: palette.background, overflow: 'hidden', ...entranceStyle }}>
        {/* Left pane */}
        <AbsoluteFill style={{ clipPath: `inset(0 ${100 - sliderX}% 0 0)`, background: palette.surface, overflow: 'hidden' }}>
          {leftImage ? (
            VIDEO_EXTENSIONS.test(leftImage) ? (
              <OffthreadVideo src={leftImage} muted style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
            ) : (
              <Img src={leftImage} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
            )
          ) : (
            <div style={{ position: 'absolute', left: '10%', top: '50%', transform: 'translateY(-50%)', width: '32%', textAlign: 'center' }}>
              <div style={{ color: palette.secondary, fontSize: 20, letterSpacing: '0.15em', marginBottom: 12, fontWeight: 700 }}>{leftLabel}</div>
              <div style={{ fontFamily: visualDNA.typography.display, fontSize: 38, color: palette.primary, fontWeight: 800 }}>{leftValue}</div>
            </div>
          )}
          {leftImage && (
            <div style={{ position: 'absolute', bottom: '10%', left: '6%', background: 'rgba(0,0,0,0.88)', padding: '14px 24px', borderRadius: visualDNA.cornerRadius || 4, borderLeft: `4px solid ${palette.secondary}` }}>
              <div style={{ color: palette.secondary, fontSize: 13, letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 4 }}>{leftLabel}</div>
              <div style={{ fontFamily: visualDNA.typography.display, fontSize: 24, fontWeight: 800, color: '#FFFFFF' }}>{leftValue}</div>
            </div>
          )}
        </AbsoluteFill>

        {/* Right pane */}
        <AbsoluteFill style={{ clipPath: `inset(0 0 0 ${sliderX}%)`, background: palette.background, overflow: 'hidden' }}>
          {rightImage ? (
            VIDEO_EXTENSIONS.test(rightImage) ? (
              <OffthreadVideo src={rightImage} muted style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
            ) : (
              <Img src={rightImage} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
            )
          ) : (
            <div style={{ position: 'absolute', right: '10%', top: '50%', transform: 'translateY(-50%)', width: '32%', textAlign: 'center' }}>
              <div style={{ color: palette.accent, fontSize: 20, letterSpacing: '0.15em', marginBottom: 12, fontWeight: 700 }}>{rightLabel}</div>
              <div style={{ fontFamily: visualDNA.typography.display, fontSize: 38, color: palette.primary, fontWeight: 800 }}>{rightValue}</div>
            </div>
          )}
          {rightImage && (
            <div style={{ position: 'absolute', bottom: '10%', right: '6%', background: 'rgba(0,0,0,0.88)', padding: '14px 24px', borderRadius: visualDNA.cornerRadius || 4, borderRight: `4px solid ${palette.accent}`, textAlign: 'right' }}>
              <div style={{ color: palette.accent, fontSize: 13, letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 4 }}>{rightLabel}</div>
              <div style={{ fontFamily: visualDNA.typography.display, fontSize: 24, fontWeight: 800, color: '#FFFFFF' }}>{rightValue}</div>
            </div>
          )}
        </AbsoluteFill>

        {/* Slider line & handle */}
        <div style={{ position: 'absolute', left: `${sliderX}%`, top: 0, bottom: 0, width: 4, background: palette.accent, zIndex: 10, boxShadow: '0 0 16px rgba(0,0,0,0.8)' }}>
          <div
            style={{
              position: 'absolute',
              top: '50%',
              left: '50%',
              transform: 'translate(-50%, -50%)',
              width: 48,
              height: 48,
              borderRadius: '50%',
              background: palette.accent,
              boxShadow: '0 4px 20px rgba(0,0,0,0.9)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: palette.text_contrast || '#000000',
              fontWeight: 900,
              fontSize: 20,
            }}
          >
            ↔
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 2: Versus Contrast Clash
  if (variant === 'versus_contrast_clash') {
    return (
      <AbsoluteFill style={{ background: palette.background, display: 'flex', flexDirection: 'row', ...entranceStyle }}>
        <div style={{ flex: 1, background: palette.surface, display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', padding: 40, borderRight: `2px solid ${palette.border}` }}>
          <div style={{ color: palette.secondary, fontSize: 24, letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 16 }}>{leftLabel}</div>
          <div style={{ fontFamily: visualDNA.typography.display, fontSize: 52, fontWeight: 800, color: palette.primary, textAlign: 'center' }}>{leftValue}</div>
        </div>
        <div style={{ position: 'absolute', left: '50%', top: '50%', transform: 'translate(-50%, -50%)', zIndex: 20 }}>
          <div style={{ background: palette.accent, color: '#FFF', borderRadius: '50%', width: 72, height: 72, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: visualDNA.typography.display, fontWeight: 900, fontSize: 24, boxShadow: '0 0 30px rgba(0,0,0,0.8)' }}>
            VS
          </div>
        </div>
        <div style={{ flex: 1, background: palette.background, display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', padding: 40 }}>
          <div style={{ color: palette.accent, fontSize: 24, letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 16 }}>{rightLabel}</div>
          <div style={{ fontFamily: visualDNA.typography.display, fontSize: 52, fontWeight: 800, color: palette.primary, textAlign: 'center' }}>{rightValue}</div>
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 3: Side by Side Cards (Default)
  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: 'transparent', padding: '0 8%', ...entranceStyle }}>
      <div style={{ textAlign: 'center', marginBottom: 40 }}>
        <div style={{ color: palette.accent, fontSize: 16, letterSpacing: '0.16em', textTransform: 'uppercase', marginBottom: 8, textShadow: '0 2px 10px rgba(0,0,0,0.8)' }}>COMPARISON</div>
        <div style={{ color: palette.primary, fontSize: 44, fontFamily: visualDNA.typography.display, fontWeight: 800, textShadow: '0 2px 16px rgba(0,0,0,0.8)' }}>{title}</div>
      </div>
      <div style={{ width: '100%', display: 'flex', gap: 36 }}>
        <div style={{
          flex: 1,
          background: `linear-gradient(135deg, ${rgba(palette.surface || '#161b22', 0.86)} 0%, ${rgba(palette.background || '#0d1117', 0.92)} 100%)`,
          padding: '40px',
          borderRadius: cornerRadius || 16,
          border: `1px solid ${rgba(palette.border || '#444', 0.4)}`,
          boxShadow: '0 24px 60px rgba(0,0,0,0.75)',
        }}>
          <div style={{ color: palette.secondary, fontSize: 18, textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 12 }}>{leftLabel}</div>
          <div style={{ fontFamily: visualDNA.typography.display, fontSize: 32, fontWeight: 700, color: palette.primary }}>{leftValue || 'Baseline state'}</div>
        </div>
        <div style={{
          flex: 1,
          background: `linear-gradient(135deg, ${rgba(palette.surface || '#161b22', 0.86)} 0%, ${rgba(palette.background || '#0d1117', 0.92)} 100%)`,
          padding: '40px',
          borderRadius: cornerRadius || 16,
          border: `1px solid ${palette.accent}`,
          boxShadow: `0 24px 60px rgba(0,0,0,0.75), 0 0 35px ${rgba(palette.accent, 0.25)}`,
        }}>
          <div style={{ color: palette.accent, fontSize: 18, textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 12 }}>{rightLabel}</div>
          <div style={{ fontFamily: visualDNA.typography.display, fontSize: 32, fontWeight: 700, color: palette.primary }}>{rightValue || 'Alternative outcome'}</div>
        </div>
      </div>
    </AbsoluteFill>
  );
}

