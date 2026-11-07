import React from 'react';
import { AbsoluteFill, Img, OffthreadVideo, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { rgba } from '../theme';

const VIDEO_EXTENSIONS = /\.(?:mp4|webm|mov|mkv|avi|m4v)(?:[?#]|$)/i;
import { documentaryTypography } from '../engine/DocumentaryTypography';
import { safeFadeOutRange } from '../engine/DirectorMotion';
import { MediaOverlayCanvas } from './MediaOverlayCanvas';
import { DocumentHighlight } from './DocumentHighlight';
import { PersonProfile } from './PersonProfile';
import { QuoteCard } from './QuoteCard';
import { ChapterTitle } from './ChapterTitle';
import { KineticText } from './KineticText';
import { StatCard } from './StatCard';

// Modern bundled display default: Archivo Black. The shared typography
// resolver verifies this exact family before layout rather than relying on a
// broad serif/sans-serif regular expression.

const clamp = (value, low = 0, high = 1) => Math.max(low, Math.min(high, value));
const clean = (value, fallback = '') => String(value ?? fallback).replace(/\s+/g, ' ').trim();
const upper = (value, fallback = '') => clean(value, fallback).toUpperCase();
const editorialLabel = (value) => /^(?:KEY METRIC|OFFICIAL RECORD|HISTORICAL DISPATCH|SOURCE MATERIAL|SOURCE RECORD|SYSTEM PROCESS|PROCESS MAP|SIGNAL LOG|DOCUMENTARY ARCHIVE)$/i.test(clean(value)) ? '' : clean(value);
const hasSparseDirectorContract = (scene = {}) => Boolean(
  scene?.text && Object.prototype.hasOwnProperty.call(scene.text, 'overlay_policy')
);
const directorNarrativeFallback = (scene = {}) => {
  if (hasSparseDirectorContract(scene) && String(scene.text?.overlay_policy || 'none').toLowerCase() !== 'single_short_highlight') {
    return '';
  }
  return clean(scene.text?.focus || scene.narration || '');
};

function paletteOf(visualDNA = {}) {
  const palette = visualDNA.palette || {};
  return {
    bg: palette.background || '#0b0f14',
    surface: palette.surface || '#151b22',
    ink: palette.primary || '#f8fafc',
    muted: palette.secondary || '#a7b0bc',
    accent: palette.accent || '#f2c94c',
    accent2: palette.accent2 || palette.secondary || '#e07a5f',
    line: palette.border || 'rgba(255,255,255,.22)',
    danger: palette.danger || '#e05d5d',
  };
}

function fontsOf(visualDNA = {}) {
  return documentaryTypography(visualDNA);
}

function useEntrance(scene, mode = 'trace') {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const sceneDuration = Math.max(1, scene?.duration_in_frames !== undefined && scene?.duration_in_frames !== null
    ? Math.round(Number(scene.duration_in_frames))
    : Math.round(Number(scene?.duration || 4) * fps));
  const speed = mode === 'trace' ? 1.1 : mode === 'snap' ? 1.35 : 0.9;
  const enter = spring({ frame, fps, config: { damping: 15, mass: 0.36, stiffness: 175 * speed } });
  const exit = interpolate(frame, safeFadeOutRange(sceneDuration, Math.round(fps * 0.32)), [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  return { frame, fps, progress: clamp(frame / Math.max(1, sceneDuration - 1)), enter, opacity: enter * exit };
}

function Grid({ colors, opacity = 0.18, dense = false }) {
  const size = dense ? 54 : 86;
  return (
    <div style={{ position: 'absolute', inset: 0, opacity, pointerEvents: 'none', backgroundImage: `linear-gradient(${rgba(colors.line, .65)} 1px, transparent 1px), linear-gradient(90deg, ${rgba(colors.line, .65)} 1px, transparent 1px)`, backgroundSize: `${size}px ${size}px`, maskImage: 'linear-gradient(to bottom, rgba(0,0,0,.8), transparent 82%)' }} />
  );
}

function Grain({ colors, opacity = 0.12 }) {
  return <div style={{ position: 'absolute', inset: 0, opacity, pointerEvents: 'none', backgroundImage: `radial-gradient(${rgba(colors.ink, .35)} 0.7px, transparent .9px)`, backgroundSize: '7px 7px', mixBlendMode: 'screen' }} />;
}

function CornerMeta({ colors, fonts, scene, label = 'EDITORIAL CUT' }) {
  const index = String(Number(scene.scene_index ?? 0) + 1).padStart(2, '0');
  return (
    <div style={{ position: 'absolute', left: '5.5%', right: '5.5%', top: '5%', display: 'flex', justifyContent: 'space-between', alignItems: 'center', color: colors.muted, fontFamily: fonts.numbers, fontSize: 13, letterSpacing: '.16em', textTransform: 'uppercase', opacity: .78 }}>
      <span><b style={{ color: colors.accent }}>{index}</b> / {upper(label)}</span>
      <span>{clean(scene.semantic_beat?.role || scene.design?.visual_structure || 'DOCUMENTARY')}</span>
    </div>
  );
}

function AccentRule({ colors, width = 180, progress = 1, vertical = false }) {
  return <div style={{ width: vertical ? 3 : width * progress, height: vertical ? width * progress : 3, background: colors.accent, boxShadow: `0 0 22px ${rgba(colors.accent, .38)}`, transformOrigin: vertical ? 'top' : 'left' }} />;
}

function MainText({ children, colors, fonts, size = 86, maxWidth = '70%', align = 'left', italic = false }) {
  return <div style={{ maxWidth, color: colors.ink, fontFamily: fonts.display, fontSize: size, fontWeight: 850, lineHeight: .94, letterSpacing: '-.035em', textAlign: align, textWrap: 'balance', textShadow: `0 5px 30px ${rgba(colors.bg, .62)}`, fontStyle: italic ? 'italic' : 'normal' }}>{children}</div>;
}

function Kicker({ children, colors, fonts }) {
  return <div style={{ color: colors.accent, fontFamily: fonts.numbers, fontSize: 14, fontWeight: 800, letterSpacing: '.2em', textTransform: 'uppercase', marginBottom: 18 }}>{children}</div>;
}

function CinematicType({ scene, props, visualDNA }) {
  const colors = paletteOf(visualDNA); const fonts = fontsOf(visualDNA); const motion = useEntrance(scene, 'snap');
  const narrative = clean(editorialLabel(props.display_text) || editorialLabel(props.text) || editorialLabel(props.title) || editorialLabel(props.headline) || directorNarrativeFallback(scene));
  const kicker = upper(editorialLabel(props.kicker || props.label || ''));
  const emphasis = clean(props.highlight_words?.[0] || props.emphasis || props._contract_text || '');
  const position = String(props.position || scene.placement?.position || 'top_left').toLowerCase();
  const side = position.includes('right') ? 'right' : 'left';
  const vertical = position.includes('bottom') ? 'bottom' : 'top';
  const slide = side === 'left' ? -1 : 1;
  if (!narrative && hasSparseDirectorContract(scene) && String(scene.text?.overlay_policy || 'none').toLowerCase() !== 'single_short_highlight') return null;
  return (
    <AbsoluteFill style={{ color: colors.ink, opacity: motion.opacity, overflow: 'hidden' }}>
      <div style={{ position: 'absolute', [vertical]: vertical === 'top' ? '17%' : '16%', [side]: '8%', width: '76%', textAlign: side === 'right' ? 'right' : 'left', transform: `translateX(${(1 - motion.enter) * slide * 54}px)` }}>
        {kicker && <Kicker colors={colors} fonts={fonts}>{kicker}</Kicker>}
        <AccentRule colors={colors} width={140} progress={clamp(motion.enter * 1.25)} />
        <div style={{ marginTop: 22 }}><MainText colors={colors} fonts={fonts} size={clean(narrative).length > 64 ? 68 : 92} maxWidth="100%" align={side === 'right' ? 'right' : 'left'}>{narrative}</MainText></div>
        {emphasis && <div style={{ marginTop: 34, display: 'flex', alignItems: 'center', gap: 14, color: colors.accent2, fontFamily: fonts.body, fontSize: 18, fontWeight: 700, letterSpacing: '.04em' }}><span style={{ width: 22, height: 3, background: colors.accent2 }} />{upper(emphasis)}</div>}
      </div>
    </AbsoluteFill>
  );
}

function DataStory({ scene, props, visualDNA }) {
  const colors = paletteOf(visualDNA); const fonts = fontsOf(visualDNA); const motion = useEntrance(scene, 'trace');
  const value = clean(props.value || props.stat || props.number || props.emphasis || '');
  const proposedTitle = clean(editorialLabel(props.display_text) || editorialLabel(props.title) || directorNarrativeFallback(scene) || editorialLabel(props.label));
  const title = value && proposedTitle.toLowerCase().replace(/[.,\s]/g, '') === value.toLowerCase().replace(/[.,\s]/g, '')
    ? directorNarrativeFallback(scene)
    : proposedTitle;
  const kicker = editorialLabel(props.kicker);
  const items = Array.isArray(props.data) ? props.data : Array.isArray(props.values) ? props.values : [];
  const chart = String(scene.primary_visual?.type || '').toLowerCase() === 'chart';
  const entries = items.map((item) => ({
    label: clean(item?.label || ''),
    display: clean(item?.value ?? item),
    numeric: Number(String(item?.value ?? item).replace(/[^0-9.-]/g, '')),
  })).slice(0, 6);
  const validChart = entries.length >= 2 && entries.every((entry) => Number.isFinite(entry.numeric) && entry.numeric >= 0);
  if (chart && !validChart) return <CinematicType scene={scene} props={props} visualDNA={visualDNA} />;
  if (!chart && !value) return <CinematicType scene={scene} props={props} visualDNA={visualDNA} />;
  const max = Math.max(1, ...entries.map((entry) => entry.numeric));
  return (
    <AbsoluteFill style={{ opacity: motion.opacity, color: colors.ink, overflow: 'hidden' }}>
      <div style={{ position: 'absolute', left: '8%', right: '8%', top: '17%', transform: `translateY(${(1 - motion.enter) * 22}px)` }}>
        {kicker && <Kicker colors={colors} fonts={fonts}>{upper(kicker)}</Kicker>}
        <MainText colors={colors} fonts={fonts} size={title.length > 52 ? 58 : 74} maxWidth={chart ? '84%' : '72%'}>{title}</MainText>
      </div>
      {chart ? (
        <div style={{ position: 'absolute', left: '11%', right: '11%', bottom: '12%', height: '38%', display: 'flex', alignItems: 'end', gap: '5%', borderBottom: `1px solid ${rgba(colors.line, .7)}` }}>
          {entries.map((entry, index) => {
            const reveal = clamp(motion.enter * 1.35 - index * .09);
            const height = Math.max(5, (entry.numeric / max) * 88 * reveal);
            return <div key={`${entry.label}-${index}`} style={{ flex: 1, minWidth: 32, height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', alignItems: 'stretch', opacity: reveal }}>
              <div style={{ marginBottom: 9, color: colors.ink, fontFamily: fonts.numbers, fontSize: 16, fontWeight: 700, textAlign: 'center' }}>{entry.display}</div>
              <div style={{ height: `${height}%`, minHeight: 3, background: index === entries.length - 1 ? colors.accent : rgba(colors.ink, .3), transformOrigin: 'bottom', borderTop: `2px solid ${colors.ink}` }} />
              {entry.label && <div style={{ marginTop: 10, color: colors.muted, fontFamily: fonts.body, fontSize: 14, lineHeight: 1.15, textAlign: 'center' }}>{entry.label}</div>}
            </div>;
          })}
        </div>
      ) : (
        <div style={{ position: 'absolute', left: '8%', bottom: '13%', color: colors.accent, fontFamily: fonts.display, fontSize: value.length > 15 ? 64 : 108, fontWeight: 800, lineHeight: .9, letterSpacing: '-.045em', transform: `translateY(${(1 - motion.enter) * 18}px)` }}>{value}</div>
      )}
    </AbsoluteFill>
  );
}

function SpatialStory({ scene, props, visualDNA, chronology = false }) {
  const colors = paletteOf(visualDNA); const fonts = fontsOf(visualDNA); const motion = useEntrance(scene, 'trace');
  const title = clean(props.display_text || props.title || directorNarrativeFallback(scene));
  const events = Array.isArray(props.events)
    ? props.events.filter((event) => event && (event.year || event.date) && (event.label || event.title)).slice(0, 5)
    : [];
  if (!chronology || events.length < 2) return <CinematicType scene={scene} props={props} visualDNA={visualDNA} />;
  return (
    <AbsoluteFill style={{ opacity: motion.opacity, color: colors.ink, overflow: 'hidden' }}>
      <div style={{ position: 'absolute', left: '8%', right: '8%', top: '18%', transform: `translateY(${(1 - motion.enter) * 24}px)` }}>
        {props.kicker && <Kicker colors={colors} fonts={fonts}>{upper(props.kicker)}</Kicker>}
        <MainText colors={colors} fonts={fonts} size={title.length > 54 ? 58 : 76} maxWidth="82%">{title}</MainText>
      </div>
      <div style={{ position: 'absolute', left: '9%', right: '9%', bottom: '19%', height: 2, background: rgba(colors.line, .62), transformOrigin: 'left', transform: `scaleX(${motion.enter})` }} />
      <div style={{ position: 'absolute', left: '8%', right: '8%', bottom: '10%', display: 'grid', gridTemplateColumns: `repeat(${events.length}, minmax(0, 1fr))`, gap: 22 }}>
        {events.map((event, index) => {
          const reveal = clamp(motion.enter * 1.7 - index * .16);
          return <div key={`${event.year || event.date}-${index}`} style={{ opacity: reveal, transform: `translateY(${(1 - reveal) * 14}px)` }}>
            <div style={{ width: 11, height: 11, borderRadius: '50%', background: colors.accent, boxShadow: `0 0 0 5px ${rgba(colors.accent, .15)}`, marginBottom: 18 }} />
            <div style={{ color: colors.accent, fontFamily: fonts.numbers, fontSize: 20, fontWeight: 800 }}>{upper(event.year || event.date)}</div>
            <div style={{ marginTop: 8, color: colors.ink, fontFamily: fonts.body, fontSize: 18, fontWeight: 600, lineHeight: 1.2 }}>{clean(event.label || event.title)}</div>
          </div>;
        })}
      </div>
    </AbsoluteFill>
  );
}

function DiagramStory({ scene, props, visualDNA }) {
  const colors = paletteOf(visualDNA); const fonts = fontsOf(visualDNA); const motion = useEntrance(scene, 'trace');
  const sparsePlan = Boolean(scene?.text && Object.prototype.hasOwnProperty.call(scene.text, 'overlay_policy'));
  const overlayAllowed = !sparsePlan || String(scene.text?.overlay_policy || 'none').toLowerCase() === 'single_short_highlight';
  const steps = Array.isArray(props.steps)
    ? props.steps.map((step) => clean(step?.desc || step?.label || step?.title || '')).filter(Boolean).slice(0, 4)
    : [];
  if (steps.length < 2) return <CinematicType scene={scene} props={props} visualDNA={visualDNA} />;
  const rawTitle = clean(editorialLabel(props.display_text) || editorialLabel(props.title) || directorNarrativeFallback(scene));
  const title = rawTitle || (steps.length >= 2 ? 'PROCESS SEQUENCE' : '');
  return (
    <AbsoluteFill style={{ opacity: motion.opacity, color: colors.ink, overflow: 'hidden' }}>
      {title ? (
        <div style={{ position: 'absolute', left: '8%', right: '8%', top: '18%' }}>
          <MainText colors={colors} fonts={fonts} size={title.length > 54 ? 56 : 74} maxWidth="84%">{title}</MainText>
        </div>
      ) : null}
      <div style={{ position: 'absolute', left: '10%', right: '10%', bottom: '19%', height: 2, background: rgba(colors.line, .54), transformOrigin: 'left', transform: `scaleX(${motion.enter})` }} />
      <div style={{ position: 'absolute', left: '8%', right: '8%', bottom: '10%', display: 'grid', gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))`, gap: 28 }}>
        {steps.map((step, index) => {
          const reveal = clamp(motion.enter * 1.7 - index * .14);
          return <div key={`${index}-${step}`} style={{ opacity: reveal, transform: `translateY(${(1 - reveal) * 16}px)` }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, color: colors.accent, fontFamily: fonts.numbers, fontSize: 15, fontWeight: 800 }}>
              <span style={{ width: 10, height: 10, borderRadius: '50%', background: colors.accent, boxShadow: `0 0 0 5px ${rgba(colors.accent, .15)}` }} />{String(index + 1).padStart(2, '0')}
            </div>
            <div style={{ marginTop: 13, color: colors.ink, fontFamily: fonts.body, fontSize: 21, fontWeight: 600, lineHeight: 1.16 }}>{step}</div>
          </div>;
        })}
      </div>
    </AbsoluteFill>
  );
}

function EvidenceDesk({ scene, props, visualDNA }) {
  const colors = paletteOf(visualDNA); const fonts = fontsOf(visualDNA); const motion = useEntrance(scene, 'snap');
  const title = clean(props.display_text || props.title || props.headline || directorNarrativeFallback(scene));
  const detail = clean(props.highlight || props.highlightedSentence || props.text || props.subtitle || '');
  const source = upper(editorialLabel(props.source || props.attribution || props.publication || ''));
  return (
    <AbsoluteFill style={{ opacity: motion.opacity, color: colors.ink, overflow: 'hidden' }}>
      <div style={{ position: 'absolute', left: '8%', top: '18%', width: '82%', maxWidth: 1200, transform: `translateY(${(1 - motion.enter) * 26}px)`, filter: `drop-shadow(0 4px 16px ${rgba(colors.bg, .62)})` }}>
        {source && <div style={{ marginBottom: 15, color: colors.accent, fontFamily: fonts.body, fontSize: 14, fontWeight: 700, letterSpacing: '.08em', textTransform: 'uppercase' }}>{source}</div>}
        <div style={{ display: 'inline-block', color: colors.ink, fontFamily: fonts.display, fontSize: clean(title).length > 34 ? 54 : 72, fontWeight: 900, lineHeight: .96, letterSpacing: '-.055em', maxWidth: '92%', overflowWrap: 'anywhere' }}>{title}</div>
        <div style={{ marginTop: 13, width: 74 * clamp(motion.enter), height: 3, background: colors.accent, transformOrigin: 'left' }} />
        {detail && <div style={{ marginTop: 20, maxWidth: 700, color: colors.muted, fontFamily: fonts.body, fontSize: 20, fontWeight: 600, lineHeight: 1.22 }}>{detail}</div>}
      </div>
    </AbsoluteFill>
  );
}

function PressClipping({ scene, props, visualDNA }) {
  const colors = paletteOf(visualDNA); const fonts = fontsOf(visualDNA); const motion = useEntrance(scene, 'snap');
  const title = upper(props.display_text || props.title || props.headline || directorNarrativeFallback(scene));
  const source = editorialLabel(props.publication || props.source || props.attribution || '');
  const position = String(props.position || scene.placement?.position || 'top_left').toLowerCase();
  const side = position.includes('right') ? 'right' : 'left';
  const vertical = position.includes('bottom') ? 'bottom' : 'top';
  return (
    <AbsoluteFill style={{ opacity: motion.opacity, overflow: 'hidden' }}>
      <div style={{ position: 'absolute', [side]: '8%', [vertical]: vertical === 'top' ? '22%' : '16%', width: '76%', textAlign: side === 'right' ? 'right' : 'left', transform: `translateX(${(1 - motion.enter) * (side === 'left' ? -44 : 44)}px)` }}>
        {source && <div style={{ color: colors.accent, fontFamily: fonts.body, fontSize: 15, letterSpacing: '.06em', marginBottom: 16 }}>{source}{props.date ? ` · ${clean(props.date)}` : ''}</div>}
        <div style={{ borderTop: `1px solid ${rgba(colors.ink, .62)}`, paddingTop: 24, color: colors.ink, fontFamily: fonts.display, fontSize: title.length > 52 ? 58 : 82, fontWeight: 850, lineHeight: .94, letterSpacing: '-.04em', maxWidth: '100%' }}>{title}</div>
        <div style={{ marginTop: 18, width: 88, height: 3, background: colors.accent, marginLeft: side === 'right' ? 'auto' : 0, transformOrigin: side === 'right' ? 'right' : 'left', transform: `scaleX(${motion.enter})` }} />
      </div>
    </AbsoluteFill>
  );
}

function SplitEvidence({ scene, props, visualDNA }) {
  const colors = paletteOf(visualDNA); const fonts = fontsOf(visualDNA); const motion = useEntrance(scene, 'trace');
  const title = clean(editorialLabel(props.title) || directorNarrativeFallback(scene));
  const left = clean(props.left || props.before || props.leftValue || ''); const right = clean(props.right || props.after || props.rightValue || '');
  if (!left || !right) return <CinematicType scene={scene} props={props} visualDNA={visualDNA} />;
  return (
    <AbsoluteFill style={{ opacity: motion.opacity, overflow: 'hidden' }}>
      <Grid colors={colors} opacity={.12} />
      <CornerMeta colors={colors} fonts={fonts} scene={scene} label="COMPARISON / EVIDENCE" />
      <div style={{ position: 'absolute', left: '8%', right: '8%', top: '22%', bottom: '15%', transform: `translateY(${(1 - motion.enter) * 26}px)` }}>
        {editorialLabel(props.kicker) && <Kicker colors={colors} fonts={fonts}>{upper(editorialLabel(props.kicker))}</Kicker>}
        <MainText colors={colors} fonts={fonts} size={title.length > 46 ? 54 : 72} maxWidth="78%">{title}</MainText>
        <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 0, borderTop: `1px solid ${colors.ink}`, borderBottom: `1px solid ${colors.ink}` }}>
          <div style={{ padding: '28px 34px 34px 0', borderRight: `1px solid ${rgba(colors.line, .7)}` }}><div style={{ color: colors.muted, fontFamily: fonts.numbers, fontSize: 13, letterSpacing: '.18em' }}>01 / {left}</div><div style={{ marginTop: 22, color: colors.ink, fontFamily: fonts.display, fontSize: 52, lineHeight: .95 }}>{upper(props.leftValue || left)}</div></div>
          <div style={{ padding: '28px 0 34px 34px' }}><div style={{ color: colors.accent, fontFamily: fonts.numbers, fontSize: 13, letterSpacing: '.18em' }}>02 / {right}</div><div style={{ marginTop: 22, color: colors.accent, fontFamily: fonts.display, fontSize: 52, lineHeight: .95 }}>{upper(props.rightValue || right)}</div></div>
        </div>
      </div>
    </AbsoluteFill>
  );
}

function CollageDesk({ scene, props, visualDNA }) {
  const colors = paletteOf(visualDNA); const fonts = fontsOf(visualDNA); const motion = useEntrance(scene, 'snap');
  const title = clean(props.display_text || directorNarrativeFallback(scene));
  const images = (Array.isArray(props.images) ? props.images : []).filter((image) => image && (image.src || image.path)).slice(0, 3);
  if (images.length < 2) return <CinematicType scene={scene} props={props} visualDNA={visualDNA} />;
  return (
    <AbsoluteFill style={{ opacity: motion.opacity, overflow: 'hidden' }}>
      <div style={{ position: 'absolute', left: '7%', right: '7%', top: '9%', bottom: '13%', display: 'grid', gridTemplateColumns: `repeat(${images.length}, minmax(0, 1fr))`, gap: 12, alignItems: 'stretch', transform: `translateY(${(1 - motion.enter) * 18}px)` }}>
        {images.map((image, index) => {
          const imgSrc = image.src || image.path || '';
          const isVid = VIDEO_EXTENSIONS.test(imgSrc);
          return (
            <div key={`${imgSrc}-${index}`} style={{ overflow: 'hidden', opacity: clamp(motion.enter * 1.35 - index * .08), transform: `scale(${(.985 + motion.enter * .015).toFixed(3)})` }}>
              {isVid ? (
                <OffthreadVideo src={imgSrc} muted style={{ width: '100%', height: '100%', objectFit: 'cover', filter: 'saturate(.86) contrast(1.04)' }} />
              ) : (
                <Img src={imgSrc} style={{ width: '100%', height: '100%', objectFit: 'cover', filter: 'saturate(.86) contrast(1.04)' }} />
              )}
            </div>
          );
        })}
      </div>
      {title && <div style={{ position: 'absolute', left: '8%', right: '8%', bottom: '6%', color: colors.ink, fontFamily: fonts.display, fontSize: title.length > 52 ? 34 : 46, fontWeight: 800, lineHeight: 1, letterSpacing: '-.04em', textShadow: `0 3px 22px ${rgba(colors.bg, .8)}` }}>{title}</div>}
    </AbsoluteFill>
  );
}

export function EditorialSceneCanvas({ scene = {}, visualDNA = {}, props = {} }) {
  // New manifests use the same semantic composition library whether they are
  // layered over source media or deliberately rendered as a graphic-only
  // explanatory beat.  Legacy paths below remain for saved projects.
  const declaredType = clean(scene.primary_visual?.type || '').toLowerCase();
  const sourceCapture = props.source_capture || props.sourceCapture
    || scene.primary_visual?.props?.source_capture || scene.primary_visual?.props?.sourceCapture
    || scene.graphic?.props?.source_capture || scene.graphic?.props?.sourceCapture;
  if (declaredType === 'documenthighlight' || sourceCapture?.status || sourceCapture?.url || sourceCapture?.domain) {
    // A verified source capture must reach the screenshot/highlight renderer
    // before a generic overlay contract can route it to EvidenceDesk.
    return <DocumentHighlight scene={scene} visualDNA={visualDNA} props={{ ...props, source_capture: sourceCapture || props.source_capture }} />;
  }
  const editorialContract = props.editorial_contract || scene.editorial_contract;
  if (
    editorialContract &&
    typeof editorialContract === 'object' &&
    editorialContract.enabled !== false &&
    editorialContract.family &&
    editorialContract.family !== 'media_hold'
  ) {
    return <MediaOverlayCanvas scene={scene} visualDNA={visualDNA} props={props} />;
  }
  const type = clean(scene.primary_visual?.type || '').toLowerCase();
  const variant = clean(scene.primary_visual?.variant || props.variant || '').toLowerCase().replace(/_/g, '-');
  if (type === 'chaptertitle') return <ChapterTitle scene={scene} visualDNA={visualDNA} props={props} />;
  if (type === 'kinetictext') return <KineticText scene={scene} visualDNA={visualDNA} props={props} />;
  if (type === 'personprofile') {
    const name = clean(props.name || scene.text?.headline || '');
    return name
      ? <PersonProfile scene={scene} visualDNA={visualDNA} props={props} asset={scene.media || {}} />
      : <CinematicType scene={scene} props={props} visualDNA={visualDNA} />;
  }
  if (type === 'quotecard') return <QuoteCard scene={scene} visualDNA={visualDNA} props={props} />;
  if (type === 'statcard') {
    const rawVal = props.value ?? props.target;
    if (rawVal != null && Number.isFinite(Number(String(rawVal).replace(/[^0-9.-]/g, '')))) {
      return <StatCard scene={scene} visualDNA={visualDNA} props={props} />;
    }
    return <DataStory scene={scene} props={props} visualDNA={visualDNA} />;
  }
  if (type === 'chart') return <DataStory scene={scene} props={props} visualDNA={visualDNA} />;
  if (type === 'diagram') return <DiagramStory scene={scene} props={props} visualDNA={visualDNA} />;
  if (type === 'animatedmap') return null;
  if (type === 'timeline') return <SpatialStory scene={scene} props={props} visualDNA={visualDNA} chronology />;
  if (type === 'headline' || variant.includes('newspaper')) return <PressClipping scene={scene} props={props} visualDNA={visualDNA} />;
  if (type === 'comparison') return <SplitEvidence scene={scene} props={props} visualDNA={visualDNA} />;
  if (type === 'collage') return <CollageDesk scene={scene} props={props} visualDNA={visualDNA} />;
  if (type === 'documenthighlight' || variant.includes('dossier') || variant.includes('evidence')) return <EvidenceDesk scene={scene} props={props} visualDNA={visualDNA} />;
  return <CinematicType scene={scene} props={props} visualDNA={visualDNA} />;
}
