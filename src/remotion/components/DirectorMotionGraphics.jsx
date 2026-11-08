import React from 'react';
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';

const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
const clamp = (value, low = 0, high = 1) => Math.max(low, Math.min(high, value));

function luminance(color) {
  const match = String(color || '').match(/^#([\da-f]{3}|[\da-f]{6})$/i);
  if (!match) return null;
  const raw = match[1].length === 3 ? [...match[1]].map((part) => part + part).join('') : match[1];
  const rgb = [0, 2, 4].map((offset) => parseInt(raw.slice(offset, offset + 2), 16) / 255);
  const linear = rgb.map((part) => part <= .04045 ? part / 12.92 : ((part + .055) / 1.055) ** 2.4);
  return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2];
}

function contrastRatio(first, second) {
  const a = luminance(first);
  const b = luminance(second);
  if (a === null || b === null) return 0;
  const [light, dark] = [a, b].sort((left, right) => right - left);
  return (light + .05) / (dark + .05);
}

function readableColor(candidate, fallback, background, minimum = 4.5) {
  return contrastRatio(candidate, background) >= minimum ? candidate : fallback;
}

function cueOverlapsWindow(start, end, windows = []) {
  return windows.some((window) => {
    const windowStart = Number(window?.start);
    const windowEnd = Number(window?.end);
    return Number.isFinite(windowStart) && Number.isFinite(windowEnd)
      && start < windowEnd && end > windowStart;
  });
}

function comparisonPair(anchor) {
  const text = clean(anchor).replace(/[.!?;:,]+$/, '');
  const match = text.match(/^(?:the\s+)?(.+?)\s+(will\s+(?:face|meet|battle)|take\s+on|takes\s+on|face|faces|battle|battles|meet|meets|play|plays|vs\.?|versus)\s+(?:the\s+)?(.+)$/i);
  if (!match) return null;
  const tidy = (value) => clean(value).replace(/^(?:the|a|an)\s+/i, '').replace(/[.!?;:,]+$/, '');
  const left = tidy(match[1]);
  const right = tidy(match[3]);
  if (!left || !right || left.length > 32 || right.length > 32) return null;
  return { left, right };
}

function ComparisonTile({ cue, frame, fps, total, palette }) {
  const start = Math.max(0, Math.round(Number(cue.time_local || 0) * fps));
  const local = frame - start;
  const reveal = interpolate(local, [0, 13], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const underline = interpolate(local, [5, 22], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const action = String(cue.action || 'reveal').toLowerCase();
  const displayFrames = Math.max(1, Math.round(displaySeconds(cue) * fps));
  const exit = interpolate(frame, [start + displayFrames - Math.round(.18 * fps), start + displayFrames], [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const pair = comparisonPair(cue.anchor_text);
  const panel = '#091B24';
  const accent = readableColor(palette.accent, '#E7B84E', panel, 3.2);
  const accent2 = readableColor(palette.accent2 || palette.secondary, '#79C7D2', panel, 3.2);
  const foreground = readableColor(palette.primary, '#F7F5EF', panel, 4.5);
  const revealMask = action === 'split'
    ? `inset(0 ${(1 - reveal) * 50}% 0 ${(1 - reveal) * 50}%)`
    : action === 'wipe' ? `inset(0 ${(1 - reveal) * 100}% 0 0)` : 'inset(0 0 0 0)';
  const transform = action === 'split'
    ? `scale(${.98 + .02 * reveal})`
    : action === 'wipe' ? 'translateY(0)' : `translateY(${(1 - reveal) * 20}px) scale(${.97 + .03 * reveal})`;
  return <div style={{ minWidth: 0, position: 'relative', overflow: 'hidden', padding: '19px 21px 21px', background: 'linear-gradient(135deg, rgba(5,15,23,.94), rgba(9,27,36,.88))', borderTop: `3px solid ${accent}`, boxShadow: '0 12px 32px rgba(0,0,0,.34)', opacity: reveal * exit, clipPath: revealMask, transform, transformOrigin: 'center bottom' }}>
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 18, minHeight: 47, color: foreground, fontFamily: 'Archivo Black, Plus Jakarta Sans, sans-serif', fontSize: total === 3 ? 25 : 31, fontWeight: 900, letterSpacing: '-.035em', lineHeight: 1.02, textAlign: 'center', textTransform: 'uppercase' }}>
      {pair ? <>
        <span style={{ flex: 1, minWidth: 0, textAlign: 'right', overflowWrap: 'anywhere' }}>{pair.left}</span>
        <span style={{ color: accent, flex: '0 0 auto', fontFamily: 'JetBrains Mono, monospace', fontSize: total === 3 ? 18 : 21, letterSpacing: '.04em' }}>VS</span>
        <span style={{ flex: 1, minWidth: 0, textAlign: 'left', color: accent2, overflowWrap: 'anywhere' }}>{pair.right}</span>
      </> : <span style={{ width: '100%', fontFamily: 'Plus Jakarta Sans, sans-serif', fontSize: 24, fontWeight: 750, letterSpacing: '-.015em' }}>{clean(cue.anchor_text)}</span>}
    </div>
    <div style={{ position: 'absolute', bottom: 0, left: 0, width: `${underline * 100}%`, height: 2, background: `linear-gradient(90deg, ${accent}, ${accent2})` }} />
    <div style={{ position: 'absolute', top: 0, right: 0, width: 56, height: 56, background: `radial-gradient(circle at top right, ${accent}22, transparent 72%)`, pointerEvents: 'none' }} />
  </div>;
}

function StepTile({ cue, frame, fps, index, total, palette }) {
  const start = Math.max(0, Math.round(Number(cue.time_local || 0) * fps));
  const local = frame - start;
  const reveal = interpolate(local, [0, 14], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const action = String(cue.action || 'reveal').toLowerCase();
  const displayFrames = Math.max(1, Math.round(displaySeconds(cue) * fps));
  const exit = interpolate(frame, [start + displayFrames - Math.round(.18 * fps), start + displayFrames], [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const panel = '#091B24';
  const accent = readableColor(palette.accent, '#E7B84E', panel, 3.2);
  const accent2 = readableColor(palette.accent2 || palette.secondary, '#79C7D2', panel, 3.2);
  const foreground = readableColor(palette.primary, '#F7F5EF', panel, 4.5);
  const revealMask = action === 'wipe' ? `inset(0 ${(1 - reveal) * 100}% 0 0)` : 'inset(0 0 0 0)';
  const transform = action === 'trace'
    ? `translateX(${(1 - reveal) * -24}px)`
    : action === 'reveal' ? `translateY(${(1 - reveal) * 18}px) scale(${.96 + .04 * reveal})` : 'translateY(0)';
  return <div style={{ minWidth: 0, position: 'relative', overflow: 'hidden', display: 'flex', alignItems: 'center', gap: 15, padding: '19px 20px 22px', background: 'linear-gradient(135deg, rgba(5,15,23,.94), rgba(9,27,36,.88))', borderTop: `3px solid ${index === total - 1 ? accent2 : accent}`, boxShadow: '0 12px 32px rgba(0,0,0,.34)', opacity: reveal * exit, clipPath: revealMask, transform, transformOrigin: 'center bottom' }}>
    <span style={{ flex: '0 0 auto', color: index === total - 1 ? accent2 : accent, fontFamily: 'JetBrains Mono, monospace', fontSize: 18, fontWeight: 700, letterSpacing: '.08em' }}>{String(index + 1).padStart(2, '0')}</span>
    <span style={{ color: foreground, fontFamily: 'Plus Jakarta Sans, sans-serif', fontSize: total === 3 ? 24 : 28, fontWeight: 800, letterSpacing: '-.025em', lineHeight: 1.05, textTransform: 'uppercase', overflowWrap: 'anywhere' }}>{clean(cue.anchor_text)}</span>
    <div style={{ position: 'absolute', bottom: 0, left: 0, width: `${interpolate(local, [7, 24], [0, 100], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' })}%`, height: 2, background: `linear-gradient(90deg, ${accent}, ${accent2})` }} />
  </div>;
}

function PersonNameTag({ cue, frame, fps, palette }) {
  const start = Math.max(0, Math.round(Number(cue.time_local || 0) * fps));
  const local = frame - start;
  const durationFrames = Math.max(1, Math.round(cueDurationSeconds(cue) * fps));
  const enter = interpolate(local, [0, 10], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const exit = interpolate(frame, [start + durationFrames - 7, start + durationFrames], [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const panel = '#07131B';
  const accent = readableColor(palette.accent, '#E7B84E', panel, 3.2);
  const foreground = readableColor(palette.primary, '#F7F5EF', panel, 4.5);
  const name = clean(cue.display_text || cue.anchor_text);
  const underline = interpolate(local, [3, 15], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  return <div style={{
    position: 'absolute', left: '6%', top: '10%', maxWidth: '46%', minWidth: 0,
    padding: '8px 15px 10px 13px', overflow: 'hidden',
    color: foreground, background: 'linear-gradient(105deg, rgba(4,13,20,.88), rgba(4,13,20,.60))',
    borderLeft: `3px solid ${accent}`, boxShadow: '0 4px 18px rgba(0,0,0,.24)',
    opacity: enter * exit,
    transform: `translate3d(0,${(1 - enter) * -9}px,0)`,
  }}>
    <div style={{ fontFamily: 'Plus Jakarta Sans, sans-serif', fontSize: 'clamp(18px, 1.55vw, 29px)', fontWeight: 850,
      letterSpacing: '.015em', lineHeight: 1.05, textTransform: 'uppercase', overflowWrap: 'anywhere',
      textShadow: '0 2px 8px rgba(0,0,0,.78)' }}>{name}</div>
    <div style={{ position: 'absolute', left: 0, bottom: 0, width: `${underline * 100}%`, height: 2, background: accent }} />
  </div>;
}

function DataCallout({ cue, frame, fps, palette }) {
  const start = Math.max(0, Math.round(Number(cue.time_local || 0) * fps));
  const local = frame - start;
  const durationFrames = Math.max(1, Math.round(cueDurationSeconds(cue) * fps));
  const reveal = interpolate(local, [0, 11], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const exit = interpolate(frame, [start + durationFrames - 7, start + durationFrames], [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const accent = readableColor(palette.accent, '#E7B84E', '#07131B', 3.2);
  const accentFill = contrastRatio('#07131B', accent) >= 4.5;
  const ink = accentFill ? '#07131B' : '#F8F7F2';
  const phrase = clean(cue.anchor_text);
  const words = phrase.split(/\s+/).filter(Boolean).length;
  return <div style={{
    position: 'absolute', left: '6%', top: '10%', maxWidth: '68%', width: 'fit-content',
    boxSizing: 'border-box', padding: words > 4 ? '8px 13px 9px' : '8px 15px 10px',
    color: ink, background: accentFill ? accent : 'rgba(4,13,20,.88)',
    borderLeft: accentFill ? 'none' : `3px solid ${accent}`,
    boxShadow: '0 4px 18px rgba(0,0,0,.30)',
    fontFamily: 'Archivo Black, Plus Jakarta Sans, sans-serif',
    fontSize: words > 4 ? 'clamp(20px, 2.15vw, 39px)' : 'clamp(22px, 2.55vw, 48px)',
    fontWeight: 900, letterSpacing: '-.025em', lineHeight: 1.02,
    textTransform: 'uppercase', overflowWrap: 'anywhere',
    textShadow: accentFill ? 'none' : '0 2px 8px rgba(0,0,0,.86)',
    opacity: reveal * exit,
    clipPath: `inset(0 ${(1 - reveal) * 100}% 0 0)`,
    transform: `translate3d(${(1 - reveal) * -8}px,0,0)`,
  }}>{phrase}</div>;
}

function displaySeconds(cue) {
  const words = clean(cue.display_text || cue.anchor_text).split(/\s+/).filter(Boolean).length;
  return Math.max(1.35, Math.min(2.2, 1.1 + words * .12));
}

function cueDurationSeconds(cue) {
  if (cue?.element === 'person') return clamp(Number(cue.duration_seconds) || 1.75, 1.35, 1.9);
  if (cue?.element === 'data') {
    const words = clean(cue.anchor_text).split(/\s+/).filter(Boolean).length;
    return Math.min(2.2, Math.max(1.35, .95 + words * .2));
  }
  return displaySeconds(cue);
}

/**
 * Renders the director's explicitly grounded comparison beats over footage.
 * Each exact narration anchor builds into a compact matchup slate; no logos,
 * scores, labels, or team colors are invented.
 */
export function DirectorMotionGraphics({ scene = {}, visualDNA = {}, suppressedElements = [], blockedWindows = [] }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  // Directed scenes may use exact-script person IDs as extractive labels. They
  // remain separate from generated headings/highlights and are suppressed by
  // any approved-copy or specialist-graphic window supplied by the composer.
  const directed = scene.director_treatment_enabled === true
    || Object.prototype.hasOwnProperty.call(scene.text || {}, 'overlay_policy');
  const frameLimit = Math.max(1, Number(scene.duration_in_frames || Math.round(Number(scene.duration || 1) * fps)));
  const suppressed = new Set(Array.isArray(suppressedElements) ? suppressedElements : []);
  const narration = clean(scene.narration);
  const knownPeople = new Set((Array.isArray(scene.film_person_entities) ? scene.film_person_entities : [])
    .map((name) => clean(name).toLocaleLowerCase()).filter(Boolean));
  const cues = (Array.isArray(scene.motion_cues) ? scene.motion_cues : [])
    .filter((cue) => {
      const element = String(cue?.element || '').toLowerCase();
      const action = String(cue?.action || '').toLowerCase();
      const anchor = clean(cue?.anchor_text);
      if (element === 'person') {
        const display = clean(cue?.display_text || anchor);
        const exactAnchor = anchor && narration.toLocaleLowerCase().includes(anchor.toLocaleLowerCase());
        const groundedDisplay = display && (knownPeople.has(display.toLocaleLowerCase())
          || display.toLocaleLowerCase() === anchor.toLocaleLowerCase());
        return !suppressed.has('person') && action === 'reveal' && exactAnchor && groundedDisplay;
      }
      if (element === 'data') {
        const exactAnchor = anchor && narration.toLocaleLowerCase().includes(anchor.toLocaleLowerCase());
        return !suppressed.has('data') && exactAnchor && ['count', 'wipe', 'reveal', 'hold'].includes(action);
      }
      if (directed) return false;
      return (element === 'comparison' && !suppressed.has('comparison') && ['split', 'wipe', 'reveal'].includes(action))
        || (element === 'steps' && !suppressed.has('steps') && ['trace', 'wipe', 'reveal'].includes(action));
    })
    .filter((cue) => clean(cue.anchor_text) && Number.isFinite(Number(cue.time_local)))
    .sort((a, b) => Number(a.time_local) - Number(b.time_local))
    .slice(0, 3)
    .filter((cue) => Number(cue.time_local) * fps < frameLimit);
  if (!cues.length) return null;
  // Show one dominant, timely cue at once so two labels never collide.
  const activeCue = cues.find((cue) => {
    const start = Math.max(0, Math.round(Number(cue.time_local || 0) * fps));
    const end = start + Math.round(cueDurationSeconds(cue) * fps);
    return frame >= start && frame < end && !cueOverlapsWindow(start, end, blockedWindows);
  });
  if (!activeCue) return null;
  const palette = visualDNA.palette || {};
  return <AbsoluteFill style={{ pointerEvents: 'none', overflow: 'hidden', zIndex: 10 }}>
    {activeCue.element === 'person'
      ? <PersonNameTag cue={activeCue} frame={frame} fps={fps} palette={palette} />
      : activeCue.element === 'data'
        ? <DataCallout cue={activeCue} frame={frame} fps={fps} palette={palette} />
      : <div style={{ position: 'absolute', left: '50%', bottom: '13%', transform: 'translateX(-50%)', width: '58%' }}>
        {activeCue.element === 'steps'
          ? <StepTile cue={activeCue} frame={frame} fps={fps} index={0} total={1} palette={palette} />
          : <ComparisonTile cue={activeCue} frame={frame} fps={fps} total={1} palette={palette} />}
      </div>}
  </AbsoluteFill>;
}

export { comparisonPair, contrastRatio, cueOverlapsWindow, readableColor };
