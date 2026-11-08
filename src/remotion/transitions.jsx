import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { rgba } from './theme';

export function normalizeTransitionId(value = 'cut') {
  const id = String(value || 'cut').trim().toLowerCase().replace(/[-\s]+/g, '_');
  const aliases = {
    hard_cut: 'cut',
    straight_cut: 'cut',
    none: 'cut',
    crossfade: 'cross_dissolve',
    soft_dissolve: 'cross_dissolve',
    luma_dissolve: 'cross_dissolve',
    zoom_cut: 'snap_zoom',
    map_sweep: 'wipe',
    wipe_left: 'wipe',
    wipe_right: 'wipe',
    paper_tear: 'ink_bleed',
    directional_slide_left: 'slide',
    directional_slide_right: 'slide',
    directional_wipe: 'wipe',
    dip_to_black: 'dip_black',
    dip_to_white: 'dip_white',
  };
  return aliases[id] || id;
}

/* ── Enhanced cinematic transitions ── */
export function TransitionOverlay({ id = 'cut', durationFrames = 12, theme, seed = 1, intensity = 1 }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  id = normalizeTransitionId(id);
  if (id === 'cut') return null;

  const p = interpolate(frame, [0, Math.max(3, durationFrames)], [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  if (p <= 0) return null;

  // ── Glitch burst ──
  if (id === 'glitch_burst' || id === 'glitch_slice') {
    const shake = Math.sin(frame * 3.2 + seed) * p * 28;
    const sliceCount = 5 + Math.floor(p * 8);
    return (
      <AbsoluteFill style={{ pointerEvents: 'none' }}>
        {Array.from({ length: sliceCount }, (_, i) => (
          <div key={i} style={{
            position: 'absolute', left: 0, right: shake,
            top: `${(i / sliceCount) * 100}%`,
            height: `${100 / sliceCount}%`,
            background: i % 2 === 0 ? theme.palette.accent : theme.palette.danger || '#ef6f6c',
            opacity: p * 0.7 * (1 - i / sliceCount),
            transform: `translateX(${(i % 2 ? -1 : 1) * shake * 0.5}px)`,
          }} />
        ))}
      </AbsoluteFill>
    );
  }

  // ── Zoom burst ──
  if (id === 'zoom_burst' || id === 'scale_transition') {
    const flash = interpolate(frame, [0, durationFrames * 0.3, durationFrames], [0, 0.9, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
    return (
      <AbsoluteFill style={{
        transform: `scale(${1 + (1 - p) * 0.3})`,
        opacity: p,
        background: `radial-gradient(circle, ${rgba('#fff', flash)}, ${rgba(theme.palette.accent, flash * 0.5)}, transparent 60%)`,
        filter: 'none',
        pointerEvents: 'none',
      }} />
    );
  }

  // ── Light leak bloom ──
  if (id === 'light_leak' || id === 'film_burn') {
    const bloom = spring({ frame, fps, config: { damping: 12, mass: 0.4, stiffness: 150 } });
    return (
      <AbsoluteFill style={{
        opacity: p * 0.8,
        mixBlendMode: 'screen',
        background: `
          radial-gradient(circle at ${seed % 2 ? '85%' : '15%'} 40%, ${rgba('#fff', bloom)} 0 3%, ${rgba(theme.palette.accent, bloom * 0.6)} 8%, transparent 50%),
          radial-gradient(circle at ${seed % 2 ? '15%' : '85%'} 60%, ${rgba(theme.palette.danger || '#ef6f6c', bloom * 0.3)} 0 5%, transparent 40%)
        `,
        filter: 'saturate(1.35)',
        pointerEvents: 'none',
      }} />
    );
  }

  // ── Directional slide / wipe ──
  if (id === 'slide' || id === 'directional_wipe' || id === 'wipe') {
    const dir = seed % 4; // 0: L->R, 1: R->L, 2: T->B, 3: B->T
    const isHorizontal = dir < 2;
    const isReverse = dir === 1 || dir === 3;
    const progress = 1 - p;
    const edgePos = isReverse ? (100 - progress * 100) : progress * 100;
    const accentCol = theme?.palette?.accent || '#ffd166';
    return (
      <AbsoluteFill style={{ pointerEvents: 'none', overflow: 'hidden' }}>
        {/* Luminous trailing light sweep (mixBlendMode screen ensures media is never obscured by opaque blocks) */}
        <div style={{
          position: 'absolute',
          inset: 0,
          background: isHorizontal
            ? (isReverse
                ? `linear-gradient(to left, transparent ${100 - edgePos}%, ${rgba(accentCol, p * 0.28)} ${Math.min(100, 100 - edgePos + 10)}%, transparent ${Math.min(100, 100 - edgePos + 22)}%)`
                : `linear-gradient(to right, transparent ${Math.max(0, edgePos - 22)}%, ${rgba(accentCol, p * 0.28)} ${Math.max(0, edgePos - 10)}%, transparent ${edgePos}%)`)
            : (isReverse
                ? `linear-gradient(to top, transparent ${100 - edgePos}%, ${rgba(accentCol, p * 0.28)} ${Math.min(100, 100 - edgePos + 10)}%, transparent ${Math.min(100, 100 - edgePos + 22)}%)`
                : `linear-gradient(to bottom, transparent ${Math.max(0, edgePos - 22)}%, ${rgba(accentCol, p * 0.28)} ${Math.max(0, edgePos - 10)}%, transparent ${edgePos}%)`),
          mixBlendMode: 'screen',
        }} />
        {/* Sleek luminous cutting edge streak */}
        <div style={{
          position: 'absolute',
          [isHorizontal ? 'left' : 'top']: `${edgePos}%`,
          [isHorizontal ? 'top' : 'left']: 0,
          [isHorizontal ? 'bottom' : 'right']: 0,
          width: isHorizontal ? 3 : '100%',
          height: isHorizontal ? '100%' : 3,
          background: `linear-gradient(${isHorizontal ? '180deg' : '90deg'}, transparent 0%, #FFFFFF 25%, ${accentCol} 50%, #FFFFFF 75%, transparent 100%)`,
          opacity: p,
          boxShadow: `0 0 16px ${rgba(accentCol, 0.9)}, 0 0 32px ${rgba('#ffffff', 0.6)}`,
          transform: isHorizontal ? 'translateX(-50%)' : 'translateY(-50%)',
        }} />
      </AbsoluteFill>
    );
  }

  // ── Ink Bleed (MagnatesMedia noir organic ink expansion) ──
  if (id === 'ink_bleed' || id === 'ink_wipe') {
    return (
      <AbsoluteFill style={{
        background: `radial-gradient(circle at 50% 50%, #000000 0%, ${rgba(theme?.palette?.background || '#08080A', 0.95)} 60%, transparent 100%)`,
        opacity: p,
        mixBlendMode: 'multiply',
        pointerEvents: 'none',
      }} />
    );
  }

  // ── Push Blur / Whip Pan (high-speed camera whip streaks) ──
  if (id === 'push_blur' || id === 'whip_pan') {
    const blurPx = (p * 14).toFixed(1);
    const dirSign = seed % 2 === 0 ? 1 : -1;
    const offsetPx = dirSign * (1 - p) * 160;
    const accentCol = theme?.palette?.accent || '#ffd166';
    return (
      <AbsoluteFill style={{
        opacity: p,
        backdropFilter: `blur(${blurPx}px)`,
        WebkitBackdropFilter: `blur(${blurPx}px)`,
        pointerEvents: 'none',
        overflow: 'hidden',
      }}>
        {/* Dynamic high-speed camera motion streaks */}
        <div style={{
          position: 'absolute',
          inset: '-20px',
          background: `
            linear-gradient(90deg, transparent 0%, ${rgba(accentCol, p * 0.35)} 35%, ${rgba('#ffffff', p * 0.45)} 50%, ${rgba(accentCol, p * 0.35)} 65%, transparent 100%),
            repeating-linear-gradient(90deg, transparent, transparent 80px, ${rgba('#ffffff', p * 0.12)} 80px, ${rgba('#ffffff', p * 0.12)} 84px, transparent 90px)
          `,
          transform: `translateX(${offsetPx.toFixed(1)}px) scaleX(${1 + p * 0.4})`,
          mixBlendMode: 'screen',
        }} />
      </AbsoluteFill>
    );
  }

  // ── Snap Zoom (MrBeast instantaneous scale jump + punch) ──
  if (id === 'snap_zoom') {
    const flash = frame < 2 ? 0.9 : 0;
    return (
      <AbsoluteFill style={{
        transform: `scale(${1 + p * 0.22})`,
        opacity: p,
        background: flash ? '#FFFFFF' : 'transparent',
        pointerEvents: 'none',
      }} />
    );
  }

  // ── Cinematic Cross-Dissolve / Luma Blooming Fade ──
  if (id === 'cross_dissolve' || id === 'luma_dissolve') {
    const blurPx = (p * 12).toFixed(1);
    const accentCol = theme?.palette?.accent || '#ffd166';
    return (
      <AbsoluteFill style={{
        opacity: p,
        backdropFilter: `blur(${blurPx}px)`,
        WebkitBackdropFilter: `blur(${blurPx}px)`,
        background: `radial-gradient(circle at 50% 50%, ${rgba('#ffffff', p * 0.22)}, ${rgba(accentCol, p * 0.12)}, transparent 75%)`,
        mixBlendMode: 'screen',
        pointerEvents: 'none',
      }} />
    );
  }

  // ── Dip to black / white ──
  const color = id === 'dip_white' || id === 'white_flash' || id === 'luma_flash' ? '#fff' : (theme?.palette?.background || '#000');
  return (
    <AbsoluteFill style={{
      opacity: id === 'dip_black' || id === 'dip_white'
        ? (1 - Math.abs(frame - durationFrames / 2) / (durationFrames / 2))
        : p,
      background: color,
      pointerEvents: 'none',
    }} />
  );
}
