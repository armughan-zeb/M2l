import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { resolveCardPlacement } from '../engine/LayoutEngine';
import { safeFadeOutRange, safeRevealRange, hasApprovedDirectorCopy } from '../engine/DirectorMotion';
import { rgba } from '../theme';
import { documentaryTypography } from '../engine/DocumentaryTypography';

const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();

/** Brief chapter typography with a tightly fitted backing over footage. */
export function ChapterTitle({ scene = {}, visualDNA = {}, props = {} }) {
  const frame = useCurrentFrame();
  const { fps, width } = useVideoConfig();
  const scale = width / 1920;
  const sceneDuration = Math.max(1, scene?.duration_in_frames !== undefined && scene?.duration_in_frames !== null
    ? Math.round(Number(scene.duration_in_frames))
    : Math.round(Number(scene?.duration || 4) * fps));
  const hasExplicitTitle = ['title', 'headline', 'short_title', 'highlight', 'label'].some((key) => Object.prototype.hasOwnProperty.call(props, key));
  const directed = scene.director_treatment_enabled === true || Object.prototype.hasOwnProperty.call(scene.text || {}, 'overlay_policy');
  const approval = scene.editorial_contract || {};
  const approvedTitle = hasApprovedDirectorCopy(scene) ? approval.display_text : '';
  const title = clean(directed ? approvedTitle : (props.title || props.headline || props.short_title || props.highlight || props.label || scene.text?.headline || (hasExplicitTitle ? '' : 'PROLOGUE'))).toUpperCase();
  const chapter = directed ? '' : clean(props.act || props.kicker || scene.text?.kicker || 'CHAPTER').toUpperCase();
  const subtitle = directed ? '' : clean(props.subtitle || props.subtext);
  const palette = visualDNA.palette || {};
  const treatment = directed ? (scene.text || {}) : {};
  const accent = treatment.accent_color || palette.accent || '#FACC15';
  const ink = treatment.text_color || palette.primary || '#FFFFFF';
  const backing = directed && !['none', 'minimal_rule'].includes(treatment.panel_style)
    ? rgba(treatment.panel_background || palette.surface || '#071015', .92) : undefined;
  const fonts = documentaryTypography(visualDNA);
  const display = fonts.display;
  const body = fonts.body;
  const placement = resolveCardPlacement(scene, props, 'bottom_left');
  const variant = String(props.variant || scene.primary_visual?.variant || 'cinematic_minimal_act').toLowerCase();
  const slate = variant === 'archival_slate_divider';
  const boldKicker = variant === 'bold_editorial_kicker';
  const technical = variant === 'technical_timecode_chapter';
  const enter = spring({ frame, fps, config: { damping: 15, mass: .36, stiffness: 175 } });
  const exit = interpolate(frame, safeFadeOutRange(sceneDuration, Math.round(fps * .32)), [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const reveal = interpolate(frame, safeRevealRange(sceneDuration, Math.round(fps * .36)), [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const centered = /center|hero|middle/i.test(String(props.position || props._position || ''));

  if (!title) return null;

  return (
    <AbsoluteFill style={{ ...placement.containerStyle, background: slate ? (palette.background || '#101820') : 'transparent', pointerEvents: 'none', opacity: enter * exit, justifyContent: slate ? 'center' : placement.containerStyle?.justifyContent }}>
      <div style={{ width: 'fit-content', maxWidth: slate ? 'min(76vw, 1160px)' : 'min(72vw, 1120px)', transform: `translateY(${(1 - enter) * 22}px)`, filter: 'drop-shadow(0 4px 15px rgba(0,0,0,.42))', textAlign: centered || slate ? 'center' : placement.cardStyle.textAlign, borderLeft: technical ? `3px solid ${accent}` : undefined, padding: directed && !slate ? `${12*scale}px ${16*scale}px ${15*scale}px` : undefined, background: !slate ? backing : undefined, borderRadius: directed && !slate ? 5 : undefined, paddingLeft: technical ? 28*scale : directed && !slate ? 16*scale : 0 }}>
        {chapter && <div style={{ display: 'flex', alignItems: 'center', justifyContent: centered || slate ? 'center' : placement.isRight ? 'flex-end' : 'flex-start', gap: 10, color: accent, fontFamily: technical ? 'JetBrains Mono, monospace' : body, fontSize: boldKicker ? 15 : 12, fontWeight: 800, letterSpacing: technical ? '.14em' : '.2em', textTransform: 'uppercase' }}>
          <span style={{ width: boldKicker ? 58 : 42, height: boldKicker ? 4 : 2, background: accent, transformOrigin: 'left', transform: `scaleX(${reveal})` }} />{chapter}
        </div>}
        <div style={{ position: 'relative', display: 'inline-block', maxWidth: '100%', marginTop: chapter ? (slate ? 24 : 14) : 0, color: ink, fontFamily: display, fontSize: (directed ? (title.length > 26 ? 74 : 104) : slate ? (title.length > 26 ? 66 : 88) : boldKicker ? (title.length > 26 ? 56 : 78) : scene.media ? (centered ? 70 : 58) : 86)*scale, textShadow:'0 2px 5px rgba(0,0,0,.65), 0 8px 24px rgba(0,0,0,.45)',fontWeight: 900, lineHeight: slate ? 1.02 : .98, letterSpacing: '-.035em', overflowWrap: 'anywhere', textTransform: slate ? 'none' : undefined }}>
          <span style={{ position: 'relative', zIndex: 1 }}>{title}</span>
          {boldKicker
            ? <span style={{ position: 'absolute', left: 0, bottom: '-.12em', width: `${Math.min(100, Math.max(24, title.length * 2.2))}%`, height: 5, background: accent, transformOrigin: 'left', transform: `scaleX(${reveal})`, opacity: .92 }} />
            : <span style={{ position: 'absolute', left: 0, right: 0, bottom: '-.06em', height: slate ? 2 : 6, background: accent, transformOrigin: 'left', transform: `scaleX(${reveal})`, opacity: .92 }} />}
        </div>
        {subtitle && <div style={{ marginTop: 13, color: palette.secondary || 'rgba(255,255,255,.78)', fontFamily: body, fontSize: 18, fontWeight: 600, lineHeight: 1.25 }}>{subtitle}</div>}
      </div>
    </AbsoluteFill>
  );
}
