import React from 'react';
import { AbsoluteFill, Img, OffthreadVideo, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { documentaryTypography } from '../engine/DocumentaryTypography';
import { cueProgress, safeFadeOutRange } from '../engine/DirectorMotion';

const VIDEO_EXTENSIONS = /\.(?:mp4|webm|mov|mkv|avi|m4v)(?:[?#]|$)/i;

const clean = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
const factualLabel = (value) => /^(?:NARRATED VALUE [AB]|ORIGINAL|CONTENDER|COMPARATIVE ANALYSIS)$/i.test(clean(value)) ? '' : clean(value);
const numericValue = (value) => {
  const match = clean(value).replace(/,/g, '').match(/-?\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : NaN;
};

export function AdaptiveComparison({ scene = {}, visualDNA = {}, props = {} }) {
  const frame = useCurrentFrame();
  const { fps, width } = useVideoConfig();
  const scale = width / 1920;
  const fonts = documentaryTypography(visualDNA);
  const palette = visualDNA.palette || {};
  const accent = props.divider_color || props.dividerColor || palette.accent || '#f2c94c';
  const ink = palette.primary || '#f7f7f2';
  const muted = palette.secondary || '#a8b0bb';
  const leftAccent = props.leftColor || palette.leftTeam || muted;
  const rightAccent = props.rightColor || palette.rightTeam || accent;
  const leftImage = props.leftImage || props.beforeImage || props.images?.[0]?.src || '';
  const rightImage = props.rightImage || props.afterImage || props.images?.[1]?.src || '';
  const leftValue = clean(props.leftValue || props.left);
  const rightValue = clean(props.rightValue || props.right);
  const transparentCanvas = props.transparent_canvas === true;
  if (!(leftValue && rightValue) && !(leftImage && rightImage)) return null;
  const reveal = cueProgress(scene, 'comparison', frame, fps, .05, .7);
  const duration = Math.max(1, Number(scene.duration_in_frames || Number(scene.duration || 4) * fps));
  const exit = interpolate(frame, safeFadeOutRange(duration, Math.round(.2 * fps)), [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const variant = clean(props.variant || scene.primary_visual?.variant || 'side_by_side_cards').toLowerCase();
  const items = [
    { image: leftImage, label: factualLabel(props.leftLabel || props.from), value: leftValue },
    { image: rightImage, label: factualLabel(props.rightLabel || props.to), value: rightValue },
  ];

  if (variant === 'split_slider_before_after') {
    const requestedSplit = Number(props.split_position ?? props.splitPosition ?? 50);
    const finalSplit = Number.isFinite(requestedSplit) ? Math.max(35, Math.min(70, requestedSplit)) : 50;
    const split = interpolate(reveal, [0, 1], [100, finalSplit], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
    const photoBottom = '15%';
    const dividerWidth = Math.max(4, Math.min(10, Number(props.divider_width || 6)));
    const labelInk = props.label_text_color || props.labelTextColor || '#F5F2EA';
    return <AbsoluteFill style={{ overflow: 'hidden', opacity: exit, color: ink, background: transparentCanvas ? 'transparent' : '#07121a' }}>
      {leftImage && (VIDEO_EXTENSIONS.test(leftImage)
        ? <OffthreadVideo src={leftImage} muted style={{ position: 'absolute', inset: `0 0 ${photoBottom}`, width: '100%', height: '85%', objectFit: 'cover' }} />
        : <Img src={leftImage} style={{ position: 'absolute', inset: `0 0 ${photoBottom}`, width: '100%', height: '85%', objectFit: 'cover' }} />)}
      {rightImage && <div style={{ position: 'absolute', top: 0, right: 0, bottom: photoBottom, left: `${split}%`, overflow: 'hidden' }}>
        {VIDEO_EXTENSIONS.test(rightImage)
          ? <OffthreadVideo src={rightImage} muted style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
          : <Img src={rightImage} style={{ width: '100%', height: '100%', objectFit: 'cover', objectPosition: props.right_position || props.rightPosition || 'left center' }} />}
      </div>}
      {(leftImage || rightImage) && <div style={{ position: 'absolute', left: 0, right: 0, bottom: photoBottom, height: '14%', background: 'linear-gradient(0deg,rgba(0,0,0,.38),transparent)' }} />}
      {!leftImage && !rightImage && !transparentCanvas && <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(115deg,#0b1822 0 49.8%,#101f29 50% 100%)' }} />}
      <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: photoBottom, display: 'flex', alignItems: 'stretch', background: 'rgba(5,13,19,.97)', borderTop: `2px solid ${accent}` }}>
        {items.map((item, index) => <div key={index} style={{ boxSizing: 'border-box', minWidth: 0, flex: index === 0 ? `0 0 ${split}%` : '1 1 0', display: 'flex', alignItems: 'center', justifyContent: index ? 'flex-end' : 'flex-start', padding: index ? '0 6% 0 4%' : '0 4% 0 6%', opacity: index ? reveal : 1, borderRight: index ? undefined : '1px solid rgba(255,255,255,.15)', transform: `translateY(${index ? (1 - reveal) * -8 : 0}px)` }}>
          {item.label && <div style={{ display: 'inline-flex', alignItems: 'center', maxWidth: '100%', color: labelInk, fontFamily: fonts.body, fontSize: 28 * scale, fontWeight: 750, letterSpacing: '.025em', lineHeight: 1.1, textTransform: props.preserve_label_case === true ? 'none' : 'uppercase', textShadow: '0 2px 12px rgba(0,0,0,.5)' }}>{item.label}</div>}
          {item.value && <div style={{ color: ink, fontFamily: fonts.display, fontSize: item.value.length > 18 ? 32 : 48, fontWeight: 900, lineHeight: 1.02, marginLeft: 18, overflowWrap: 'break-word' }}>{item.value}</div>}
        </div>)}
      </div>
      <div style={{ position: 'absolute', left: `${split}%`, top: 0, bottom: photoBottom, width: dividerWidth, background: accent, transform: 'translateX(-50%)', boxShadow: `0 0 18px ${accent}70` }} />
      <div style={{ position: 'absolute', left: `${split}%`, top: '42.5%', width: 24, height: 24, borderRadius: '50%', background: accent, border: '5px solid #07121a', transform: 'translate(-50%,-50%)', boxShadow: `0 0 18px ${accent}66` }} />
    </AbsoluteFill>;
  }

  if (variant === 'versus_contrast_clash') {
    const textOnly = !leftImage && !rightImage;
    return <AbsoluteFill style={{ overflow: 'hidden', opacity: exit, color: ink, background: transparentCanvas ? 'transparent' : 'linear-gradient(100deg,#08131c 0 49.8%,#15232c 50% 100%)' }}>
      {/* Keep both teams in the two outer columns; the center divider/VS is
          absolutely positioned. A phantom middle grid column had assigned
          Dallas to a narrow 120px track and wrapped 17 onto two lines. */}
      <div style={{ position: 'absolute', inset: textOnly ? '18% 10% 18%' : '18% 8% 18%', display: 'grid', gridTemplateColumns: '1fr 1fr', alignItems: 'center', gap: textOnly ? 240 : 200 }}>
        {items.map((item, index) => {
          const progress = Math.max(0, Math.min(1, (reveal - index * .13) / .78));
          return <div key={index} style={{ minWidth: 0, opacity: progress, transform: `translateX(${(1 - progress) * (index ? 48 : -48)}px)` }}>
            {item.image && (VIDEO_EXTENSIONS.test(item.image)
              ? <OffthreadVideo src={item.image} muted style={{ width: '100%', height: '35vh', objectFit: 'cover', borderBottom: `3px solid ${index ? accent : muted}` }} />
              : <Img src={item.image} style={{ width: '100%', height: '35vh', objectFit: 'cover', borderBottom: `3px solid ${index ? accent : muted}` }} />)}
            {item.label && <div style={{ color: transparentCanvas ? ink : (index ? rightAccent : leftAccent), fontFamily: fonts.body, fontSize: textOnly ? 32 : 22, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '.09em', marginTop: textOnly ? 0 : 20, textAlign: index ? 'left' : 'right', textShadow: transparentCanvas ? '0 2px 12px rgba(0,0,0,.96), 0 1px 2px rgba(0,0,0,.96)' : undefined }}>{item.label}</div>}
            {item.value && <div style={{ color: ink, fontFamily: fonts.display, fontSize: textOnly ? (item.value.length > 10 ? 96 : 128) : (item.value.length > 16 ? 42 : 66), lineHeight: 1.02, fontWeight: 900, marginTop: textOnly ? 20 : 10, whiteSpace: 'nowrap', textAlign: index ? 'left' : 'right', textShadow: '0 6px 28px rgba(0,0,0,.5)' }}>{item.value}</div>}
          </div>;
        })}
        <div style={{ position: 'absolute', left: '50%', top: '50%', transform: `translate(-50%,-50%) scale(${.75 + .25 * reveal})`, color: accent, fontFamily: fonts.display, fontSize: textOnly ? 42 : 36, fontWeight: 900, letterSpacing: '.04em', textShadow: '0 4px 22px rgba(0,0,0,.8)' }}>VS</div>
      </div>
      <div style={{ position: 'absolute', left: '50%', top: '15%', bottom: '15%', width: 1, background: `linear-gradient(transparent,${accent},transparent)`, opacity: .8 }} />
    </AbsoluteFill>;
  }

  const metricNumbers = items.map((item) => numericValue(item.value));
  if (variant === 'dual_metric_comparison' && metricNumbers.every(Number.isFinite)) {
    const numbers = metricNumbers;
    const max = Math.max(1, ...numbers.filter(Number.isFinite).map((number) => Math.abs(number)));
    return <AbsoluteFill style={{ overflow: 'hidden', opacity: exit, color: ink }}>
      <div style={{ position: 'absolute', left: '9%', right: '9%', top: '23%', bottom: '20%', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 48 * scale, alignItems: 'end' }}>
        {items.map((item, index) => {
          const number = numbers[index];
          const progress = Math.max(0, Math.min(1, (reveal - index * .14) / .78));
          const bar = Number.isFinite(number) ? Math.max(3, Math.abs(number) / max * 100 * progress) : 0;
          return <div key={index} style={{ position: 'relative', height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'end', minWidth: 0, opacity: progress }}>
            {item.label && <div style={{ color: index ? rightAccent : leftAccent, fontFamily: fonts.body, fontSize: 22 * scale, fontWeight: 750, letterSpacing: '.08em', textTransform: 'uppercase', marginBottom: 12 * scale }}>{item.label}</div>}
            {item.value && <div style={{ fontFamily: fonts.display, fontSize: item.value.length > 12 ? 44 : 64, fontWeight: 900, lineHeight: 1, marginBottom: 20 * scale, overflowWrap: 'break-word' }}>{item.value}</div>}
            <div style={{ height: '40%', minHeight: 24, width: `${bar || 100 * progress}%`, background: index ? rightAccent : leftAccent, transformOrigin: 'left bottom', borderTop: `4px solid ${index ? ink : rightAccent}` }} />
            <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: 1, background: 'rgba(255,255,255,.3)' }} />
          </div>;
        })}
      </div>
    </AbsoluteFill>;
  }

  return <AbsoluteFill style={{ overflow: 'hidden', opacity: exit, color: ink }}>
    <div style={{ position: 'absolute', inset: '12% 8% 14%', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 * scale }}>
      {items.map((item, index) => {
        const itemReveal = Math.max(0, Math.min(1, (reveal - index * .22) / .78));
        return <div key={index} style={{ minWidth: 0, position: 'relative', overflow: 'hidden', borderBottom: `5px solid ${index ? accent : muted}`, opacity: itemReveal, transform: `translateX(${(1 - itemReveal) * (index ? 38 : -38)}px)` }}>
          {item.image && (
            VIDEO_EXTENSIONS.test(item.image) ? (
              <OffthreadVideo src={item.image} muted style={{ width: '100%', height: '100%', objectFit: 'cover', filter: 'contrast(1.08) saturate(.95)' }} />
            ) : (
              <Img src={item.image} style={{ width: '100%', height: '100%', objectFit: 'cover', filter: 'contrast(1.08) saturate(.95)' }} />
            )
          )}
          {item.image && <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(0deg,rgba(0,0,0,.72),transparent 60%)' }} />}
          <div style={{ position: 'absolute', left: '7%', right: '7%', bottom: item.image ? '7%' : '28%', paddingTop: 18 * scale, borderTop: `2px solid ${index ? accent : muted}` }}>
            {item.label && <div style={{ color: index ? rightAccent : leftAccent, fontFamily: fonts.body, fontSize: 23 * scale, fontWeight: 700, letterSpacing: '.06em', textTransform: 'uppercase' }}>{item.label}</div>}
            {item.value && <div style={{ color: ink, fontFamily: fonts.display, fontSize: item.value.length > 18 ? 51 : 82, fontWeight: 900, lineHeight: 1.05, marginTop: 18 * scale, overflowWrap: 'break-word' }}>{item.value}</div>}
          </div>
        </div>;
      })}
      <div style={{ position: 'absolute', left: '50%', top: '4%', bottom: '4%', width: 2, background: accent, transform: `translateX(-50%) scaleY(${reveal})`, transformOrigin: 'top' }} />
    </div>
  </AbsoluteFill>;
}
