import React from 'react';
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { documentaryTypography, fittedDocumentarySize } from '../engine/DocumentaryTypography';
import { cueStartFrame, safeFadeOutRange, spokenPhraseFrame } from '../engine/DirectorMotion';

const clean = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
const clamp = (value) => Math.max(0, Math.min(1, value));

export function AdaptiveTimeline({ scene = {}, visualDNA = {}, props = {} }) {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const scale = width / 1920;
  const fonts = documentaryTypography(visualDNA);
  const palette = visualDNA.palette || {};
  const accent = palette.accent || '#d2a854';
  const ink = '#F7F6F2';
  const variant = clean(props.variant || scene.primary_visual?.variant).toLowerCase();
  const events = (Array.isArray(props.events) ? props.events : []).slice(0, 5)
    .map((event) => ({ date: clean(event?.year || event?.date), label: clean(event?.label || event?.title) }))
    .filter((event) => event.date && event.label);
  const singleDate = clean(props.date || props.year || props.eraSpan);
  if (!events.length && !singleDate) return null;
  const duration = Math.max(1, Number(scene.duration_in_frames || Number(scene.duration || 4) * fps));
  const fit = (text, maxSize, availableWidth, family = fonts.display) => fittedDocumentarySize(text,
    { fontFamily: family, maxSize: Math.min(maxSize * scale, height * .18), withinWidth: availableWidth });
  const progress = (event, index) => {
    const start = spokenPhraseFrame(scene, event.date, fps) ?? spokenPhraseFrame(scene, event.label, fps) ?? Math.round((.12 + index * .55) * fps);
    return clamp((frame - start) / Math.max(1, fps * .32));
  };
  const exit = interpolate(frame, safeFadeOutRange(duration, Math.round(.2 * fps)), [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });

  // A single date is a short stamp, never a fabricated chronology.
  if (events.length < 2) {
    const date = singleDate || events[0]?.date;
    const label = events[0]?.label || '';
    const start = scene.recovery?.status === 'grounded_explanation'
      ? cueStartFrame(scene, 'timeline', fps, .1)
      : spokenPhraseFrame(scene, date, fps) ?? Math.round(.1 * fps);
    const window = Math.min(duration - start, Math.round((scene.recovery?.status === 'grounded_explanation' ? 5 : 2.6) * fps));
    const local = frame - start;
    if (local < 0 || local >= window) return null;
    const enter = clamp(local / Math.max(1, fps * .18));
    const opacity = enter * clamp((window - local) / Math.max(1, fps * .18));
    const overlay = scene.visual_role === 'media_overlay';
    return <AbsoluteFill style={{ color: ink, opacity, pointerEvents: 'none' }}>
      <div data-documentary-text="date" style={{ position: 'absolute', left: '8%', top: overlay ? '12%' : '38%', maxWidth: '84%', width: 'fit-content',
        padding: `${12 * scale}px ${20 * scale}px`, borderLeft: `${5 * scale}px solid ${accent}`, background: 'rgba(10,10,12,.88)',
        transform: `translateY(${(1 - enter) * 12 * scale}px)` }}>
        <div style={{ fontFamily: fonts.display, fontWeight: 800, fontSize: fit(date, 110, width * .82 - 40 * scale), lineHeight: 1.12, whiteSpace: 'nowrap' }}>{date}</div>
        {label && <div style={{ fontFamily: fonts.body, fontSize: fit(label, 29, width * .8, fonts.body), marginTop: 10 * scale, lineHeight: 1.2, color: '#d1ceca' }}>{label}</div>}
      </div>
    </AbsoluteFill>;
  }

  const vertical = variant === 'vertical_chronology';
  const calendar = variant === 'date_flip_calendar';
  const columnWidth = width * .80 / events.length - 30 * scale;
  return <AbsoluteFill style={{ color: ink, opacity: exit, pointerEvents: 'none' }}>
    <div style={{ position: 'absolute', inset: '18% 10%', display: 'grid', gap: 30 * scale,
      gridTemplateColumns: vertical ? '1fr' : `repeat(${events.length}, minmax(0, 1fr))`, alignContent: 'center' }}>
      {events.map((event, index) => {
        const reveal = progress(event, index);
        const itemWidth = vertical ? width * .70 : columnWidth;
        return <div key={`${event.date}-${index}`} data-documentary-text="timeline-event" style={{ minWidth: 0, opacity: reveal,
          transform: calendar ? `perspective(${700 * scale}px) rotateX(${(1 - reveal) * 12}deg)` : `translateY(${(1 - reveal) * 16 * scale}px)`,
          display: vertical ? 'grid' : 'block', gridTemplateColumns: '30% 1fr', gap: 32 * scale,
          borderTop: vertical ? undefined : `${3 * scale}px solid ${accent}`, borderLeft: vertical ? `${3 * scale}px solid ${accent}` : undefined,
          padding: `${18 * scale}px ${vertical ? 22 * scale : 0}px` }}>
          <div style={{ fontFamily: fonts.numbers, color: accent, fontSize: fit(event.date, vertical ? 55 : 92, vertical ? itemWidth * .3 : itemWidth, fonts.numbers), whiteSpace: 'nowrap', fontWeight: 800, lineHeight: 1.15 }}>{event.date}</div>
          <div style={{ fontFamily: fonts.body, marginTop: vertical ? 0 : 18 * scale, fontSize: 30 * scale, lineHeight: 1.3, overflowWrap: 'break-word', color: '#e7e4df' }}>{event.label}</div>
        </div>;
      })}
    </div>
  </AbsoluteFill>;
}
