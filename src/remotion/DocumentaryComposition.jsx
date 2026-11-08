import React from 'react';
import { AbsoluteFill, Audio, Img, OffthreadVideo, Sequence, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from 'remotion';
import { GraphicTemplate } from './templates';
import { mergeTheme, rgba } from './theme';
import { DocumentaryFontFaces, useDocumentaryFontsReady } from './fonts';
import { OverlayStack } from './overlays';
import { sanitizeDocumentaryProps } from './engine/TextSanitizer';
import { TransitionOverlay } from './transitions';
import { Particles, CameraShake, LightLeak, FilmGrain, Vignette } from './effects/index';
import { resolveVisualDNA, VisualTexture } from './engine/StyleEngine';
import { renderSceneSystem } from './engine/SceneRegistry';
import { MediaOverlayCanvas } from './components/MediaOverlayCanvas';
import { DeclarativeRecipe } from './components/DeclarativeRecipe';
import { TextCard } from './components/TextCard';
import { DirectorMotionGraphics } from './components/DirectorMotionGraphics';
import { resolveCardPlacement } from './engine/LayoutEngine';
import { cueStartFrame, directorCue, spokenPhraseFrame, hasApprovedDirectorCopy } from './engine/DirectorMotion';
import { focalObjectPosition, resolveFocalBox, resolveMediaFit, isContainedMedia } from './engine/FocalFrame';
import { MediaShotSequence } from './components/MediaShotSequence';
import { resolveColorGrade } from './engine/ColorGrade';
import Timeline from '../production-timeline';
import { MissingSceneMotionGraphic } from './components/MissingSceneMotionGraphic';
import { isRecoveryMedia } from './engine/RecoveryMedia';
import { resolveMediaKind } from './engine/MediaKind';
import { recoveryIsBackdrop, timedRecoveryNarration } from './engine/RecoveryContent';
import { SceneRenderBoundary } from './components/SceneRenderBoundary';
import { DocumentaryLayoutAudit } from './engine/DocumentaryLayoutAudit';
import { SourceCredit } from './components/SourceCredit';
import { activeSceneEntries, sceneFrameWindow } from './engine/SceneSchedule';

// Semantic templates chosen by the director must reach their specialist
// Remotion renderer even when the underlying footage remains the primary
// visual. Legacy behavior treated these as generic media cards and silently
// dropped the chart/map/diagram grammar from media-first scenes.
const DIRECTOR_GRAPHIC_TYPES = {
  'map-route': 'AnimatedMap', 'map-card': 'AnimatedMap', 'map-comparison': 'AnimatedMap',
  'bar-chart': 'Chart', 'ranked-bars': 'Chart', 'line-chart': 'Chart', 'slope-chart': 'Chart',
  'area-chart': 'Chart', 'donut-chart': 'Chart',
  'date-card': 'Timeline', 'date-reveal': 'Timeline', 'date-numeral': 'Timeline',
  'timeline-trace': 'Timeline', 'timeline-strip': 'Timeline',
  'stat-card': 'StatCard', 'stat-counter': 'StatCard', 'money-counter': 'StatCard', 'metric-grid-enhanced': 'StatCard',
  'metric-grid': 'MetricGrid',
  'cause-effect': 'Diagram', 'cause-effect-flow': 'Diagram', 'process-flow': 'Diagram', 'decision-tree': 'Diagram',
  'split-screen': 'Comparison', 'comparison-split': 'Comparison',
  'paper-explainer': 'DocumentHighlight', 'source-highlight': 'DocumentHighlight',
  'quote-card': 'QuoteCard', 'witness-quote': 'QuoteCard',
  'person-profile': 'PersonProfile', 'profile-dossier': 'PersonProfile', 'dossier-card': 'PersonProfile', 'lower-third': 'PersonProfile', 'lower-third-card': 'PersonProfile',
  'gallery-stack': 'Collage',
  'media-focus-card': 'MediaFocusCard',
  'data-card': 'DataCard', 'fact-card': 'DataCard',
  'cinematic-highlight': 'CinematicHighlight', 'cinematic-card': 'CinematicHighlight',
  'declassified-document': 'DeclassifiedDocument', 'declassified-dossier': 'DeclassifiedDocument', 'redaction-reveal': 'DeclassifiedDocument',
  'isometric-map': 'IsometricMap', 'flight-tracker': 'IsometricMap',
  'evidence-pinboard': 'EvidencePinboard', 'evidence-board': 'EvidencePinboard', 'photo-pinboard': 'EvidencePinboard',
  'technical-blueprint': 'TechnicalBlueprint', 'blueprint-reveal': 'TechnicalBlueprint', 'cad-blueprint': 'TechnicalBlueprint',
  'depth-typography': 'DepthTypography', 'behind-subject-text': 'DepthTypography',
};

// These templates are director-selected type systems, not generic cards.
// Their approved copy is rendered by the dedicated Remotion typographic
// components while the original media remains the base layer.
const DIRECTOR_COPY_TEMPLATE_TYPES = {
  'kinetic-statement': { component: 'KineticText', variant: 'staggered_lines' },
  'headline-center': { component: 'KineticText', variant: 'impact_slab' },
  'keyword-reveal': { component: 'KineticText', variant: 'word_by_word_punch' },
  'chapter-opener': { component: 'ChapterTitle', variant: 'cinematic_minimal_act' },
};
const DIRECTOR_COPY_VARIANTS = {
  KineticText: new Set(['word_by_word_punch', 'staggered_lines', 'mask_slide_up', 'bold_minimal_center', 'tracking_expand', 'split_counter_accent', 'impact_slab']),
  ChapterTitle: new Set(['cinematic_minimal_act', 'archival_slate_divider', 'bold_editorial_kicker', 'technical_timecode_chapter']),
};

function recipeHasGroundedVisual(recipe, scene) {
  const layers = Array.isArray(recipe?.layers) ? recipe.layers : [];
  const narration = String(scene?.narration || '').toLocaleLowerCase();
  const mediaShots = Array.isArray(scene?.media?.shots) ? scene.media.shots : [];
  return layers.some((layer) => {
    const kind = String(layer?.kind || '').toLowerCase();
    const text = String(layer?.text || '').trim();
    const actor = String(layer?.semantic_actor || '').trim();
    const rows = Array.isArray(layer?.data) ? layer.data : [];
    if (text && narration.includes(text.toLocaleLowerCase())) return true;
    if (actor && narration.includes(actor.toLocaleLowerCase())
      && ['line', 'path', 'circle', 'sphere', 'cone', 'arrow', 'rect'].includes(kind)) return true;
    if (kind === 'chart') return rows.length >= 2 && rows.every((row) => row?.value !== null && row?.value !== undefined);
    if (kind === 'diagram') return rows.length >= 2 && Array.isArray(layer?.links) && layer.links.length > 0;
    if (kind === 'timeline' || kind === 'comparison') return rows.length >= 2;
    if (kind === 'map') return scene?.map_shot?.enabled === true && rows.length > 0;
    if (kind === 'counter') return rows.some((row) => row?.value !== null && row?.value !== undefined);
    if (kind === 'image') return Boolean(layer?.asset_id || (Number.isInteger(layer?.image_index) && layer.image_index < mediaShots.length));
    if (kind === 'media_montage') return rows.length >= 2 || mediaShots.length >= 2;
    return false;
  });
}

function approvedCopyTemplate(scene = {}) {
  if (!hasApprovedDirectorCopy(scene)) return null;
  const templateId = String(scene.text?.template_id || '').trim().toLowerCase().replace(/_/g, '-');
  const selected = DIRECTOR_COPY_TEMPLATE_TYPES[templateId];
  if (!selected) return null;
  const requestedVariant = String(scene.text?.variant || '').trim().toLowerCase().replace(/-/g, '_');
  return {
    templateId,
    component: selected.component,
    variant: DIRECTOR_COPY_VARIANTS[selected.component].has(requestedVariant) ? requestedVariant : selected.variant,
  };
}

function MediaLayer({ media, theme, scene, visualDNA = {}, isBlurred = false }) {
  const [mediaFailed, setMediaFailed] = React.useState(false);
  const rawSrc = media?.src || media?.path || media?.asset_path || media?.url;
  const src = resolveStaticAsset(rawSrc);
  const isVideo = resolveMediaKind(media, rawSrc) === 'video';
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const isEmergencyPlate = String(rawSrc || '').includes('emergency_render')
    || String(rawSrc || '').includes('graphic_fallback')
    || media?.provider === 'neutral'
    || isRecoveryMedia(media, rawSrc);
  if (!src || mediaFailed) return <MissingSceneMotionGraphic scene={scene} visualDNA={visualDNA} theme={theme} backgroundOnly={recoveryIsBackdrop(scene)} copyOwnedElsewhere={true} />;
  const motion = scene?.camera?.disabled ? 'hold' : String(scene?.camera?.motion || '').toLowerCase();
  const sourceStartSeconds = Math.max(0, Number(media?.source_start ?? media?.in_point ?? 0) || 0);
  const sourceStartFrames = Math.round(sourceStartSeconds * fps);
  const videoTiming = isVideo && sourceStartFrames > 0 ? { startFrom: sourceStartFrames } : {};
  const rawTreatment = scene?.design?.image_treatment || scene?.media_composition?.image_treatment || scene?.treatment || 'hold';
  const treatment = String(rawTreatment || 'hold').trim().toLowerCase().replace(/-/g, '_');
  const geographicWindow = scene?.design?.geographic_window === true
    || ['AnimatedMap', 'SatelliteMap'].includes(scene?.primary_visual?.type);
  const contextualTreatment = treatment === 'map_window' && !geographicWindow ? 'hold' : treatment;
  const videoSupportedTreatments = [
    'film_gate', 'spotlight', 'iris_focus',
    'film_matte_16mm', 'archival_white_border',
    'side_by_side_collage', 'depth_drift_25d'
  ];
  const effectiveTreatment = isVideo
    ? (videoSupportedTreatments.includes(treatment) ? treatment : 'hold')
    : contextualTreatment;
  const focal = resolveFocalBox(scene, media);
  // The resolver stamps ordinary web imagery with ``safe_framing`` and a
  // conservative ``contain`` hint. The shared policy keeps portrait imagery
  // whole while allowing wide hero media to fill the documentary canvas.
  const objectFit = isEmergencyPlate ? 'cover' : resolveMediaFit(media, {}, isVideo);
  const safeFraming = !isEmergencyPlate && !isVideo && isContainedMedia(media, {}, false);
  const focalPosition = focalObjectPosition(focal, media?.position || 'center');
  const reveal = interpolate(frame, [0, 18], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const sceneDuration = Math.max(1, scene?.duration_in_frames !== undefined && scene?.duration_in_frames !== null
    ? Math.round(Number(scene.duration_in_frames))
    : Math.round(Number(scene?.duration || 4) * fps));
  const cameraStart = cueStartFrame(scene, 'image', fps, 0);
  const progress = Math.min(1, Math.max(0, (frame - cameraStart) / Math.max(1, sceneDuration - cameraStart)));
  let transform = '';
  if (scene?.camera?.disabled) { transform = 'none'; } else if (isVideo) {
    const drift = motion.includes('left') ? -1 : motion.includes('right') ? 1 : 0;
    const pan = motion.includes('up') ? -1 : motion.includes('down') ? 1 : 0;
    const scale = motion.includes('pull') ? 1.04 - progress * 0.04 : (1 + progress * 0.04);
    const translate = drift * progress * 1.0;
    const vertical = pan * progress * 1.0;
    const rotate = motion === 'flip' ? ` rotateY(${interpolate(frame, [0, 24], [90, 0], { extrapolateRight: 'clamp' })}deg)` : '';
    transform = `translate(${translate.toFixed(3)}%, ${vertical.toFixed(3)}%) scale(${scale.toFixed(4)})${rotate}`;
  } else {
    const sceneIdx = Number(scene?.scene_index || 0);
    const rotate = motion === 'flip' ? ` rotateY(${interpolate(frame, [0, 24], [90, 0], { extrapolateRight: 'clamp' })}deg)` : '';
    const ease = (1 - Math.cos(Math.PI * progress)) / 2;
    const focalXOffset = (0.5 - (focal?.cx ?? 0.5)) * 3.6;
    const focalYOffset = (0.5 - (focal?.cy ?? 0.5)) * 2.8;

    if (motion.includes('zoom_combo') || motion.includes('ken_burns')) {
      // 2-Phase Documentary Ken Burns Zoom Combo
      let scale, transX, transY;
      if (progress < 0.5) {
        const p1 = (1 - Math.cos(Math.PI * progress * 2)) / 2;
        scale = 1.0 + p1 * 0.12;
        transX = focalXOffset * p1;
        transY = focalYOffset * p1;
      } else {
        const p2 = (1 - Math.cos(Math.PI * (progress - 0.5) * 2)) / 2;
        scale = 1.12 - p2 * 0.03;
        transX = focalXOffset + 1.8 * p2;
        transY = focalYOffset - 0.8 * p2;
      }
      transform = `translate(${transX.toFixed(3)}%, ${transY.toFixed(3)}%) scale(${scale.toFixed(4)})${rotate}`;
    } else if (motion.includes('zoom_pan') || motion.includes('ken_burns_pro') || motion.includes('diagonal')) {
      const scale = 1.0 + ease * 0.12;
      const dirX = motion.includes('left') ? -1 : 1;
      const dirY = motion.includes('up') ? -1 : 1;
      const transX = dirX * 2.4 * ease + focalXOffset * 0.5;
      const transY = dirY * 1.6 * ease + focalYOffset * 0.5;
      transform = `translate(${transX.toFixed(3)}%, ${transY.toFixed(3)}%) scale(${scale.toFixed(4)})${rotate}`;
    } else if (motion.includes('pan_vertical')) {
      const scale = 1.08;
      const transY = Math.sin(Math.PI * progress) * -2.5;
      transform = `translate(0%, ${transY.toFixed(3)}%) scale(${scale.toFixed(4)})${rotate}`;
    } else if (motion.includes('pan_horizontal')) {
      const scale = 1.08;
      const transX = Math.sin(Math.PI * progress) * 2.8;
      transform = `translate(${transX.toFixed(3)}%, 0%) scale(${scale.toFixed(4)})${rotate}`;
    } else if (motion.includes('pull') || motion.includes('zoom_out')) {
      const scale = 1.12 - ease * 0.12;
      const transX = focalXOffset * (1 - ease);
      const transY = focalYOffset * (1 - ease);
      transform = `translate(${transX.toFixed(3)}%, ${transY.toFixed(3)}%) scale(${scale.toFixed(4)})${rotate}`;
    } else if (motion.includes('zoom') || motion.includes('push')) {
      const scale = 1.0 + ease * 0.12;
      const transX = focalXOffset * ease;
      const transY = focalYOffset * ease;
      transform = `translate(${transX.toFixed(3)}%, ${transY.toFixed(3)}%) scale(${scale.toFixed(4)})${rotate}`;
    } else if (motion.includes('left')) {
      const scale = 1.04 + ease * 0.05;
      const translate = -2.8 * ease;
      transform = `translate(${translate.toFixed(3)}%, 0%) scale(${scale.toFixed(4)})${rotate}`;
    } else if (motion.includes('right')) {
      const scale = 1.04 + ease * 0.05;
      const translate = 2.8 * ease;
      transform = `translate(${translate.toFixed(3)}%, 0%) scale(${scale.toFixed(4)})${rotate}`;
    } else {
      const pattern = sceneIdx % 5;
      if (pattern === 0) {
        const scale = 1.0 + ease * 0.10;
        const transX = focalXOffset * ease * 0.5;
        const transY = focalYOffset * ease * 0.5;
        transform = `translate(${transX.toFixed(3)}%, ${transY.toFixed(3)}%) scale(${scale.toFixed(4)})${rotate}`;
      } else if (pattern === 1) {
        const scale = 1.04 + ease * 0.05;
        const translate = -2.4 * ease;
        transform = `translate(${translate.toFixed(3)}%, 0%) scale(${scale.toFixed(4)})${rotate}`;
      } else if (pattern === 2) {
        // Alternating Ken Burns combo
        const p1 = (1 - Math.cos(Math.PI * Math.min(1, progress * 1.6))) / 2;
        const scale = 1.0 + p1 * 0.11;
        const transX = focalXOffset * p1 + 1.2 * ease;
        const transY = focalYOffset * p1 - 0.6 * ease;
        transform = `translate(${transX.toFixed(3)}%, ${transY.toFixed(3)}%) scale(${scale.toFixed(4)})${rotate}`;
      } else if (pattern === 3) {
        const scale = 1.04 + ease * 0.05;
        const translate = 2.4 * ease;
        transform = `translate(${translate.toFixed(3)}%, 0%) scale(${scale.toFixed(4)})${rotate}`;
      } else {
        const scale = 1.04 + ease * 0.06;
        const transX = 1.6 * ease;
        const transY = 1.0 * ease;
        transform = `translate(${transX.toFixed(3)}%, ${transY.toFixed(3)}%) scale(${scale.toFixed(4)})${rotate}`;
      }
    }
  }
  const archival = scene?.graphic?.template_id === 'archival-frame' || (scene?.overlays || []).some((item) => item?.template_id === 'archival-frame');
  const explicitFocusDim = Boolean(
    scene?.design?.focus_dim === true
    || scene?.design?.allow_focus_dim === true
    || scene?.media_composition?.focus_dim === true
  );
  
  // High-end documentary color grading: rich contrast, vibrant saturation, deep blacks, pristine clarity
  const baseGrade = resolveColorGrade(scene, visualDNA, { archival }).filter;
  // Blurring is reserved for chart underlays.  Generic editorial media must
  // stay bright and sharp enough to carry the story.
  // Charts may request a softened underlay, but the media-first contract does
  // not permit a generic blur pass. Keep the source readable and use the
  // chart's own geometry for separation.
  // Cinematic frosted bokeh depth blur for underlays and softened stages
  const dimFilter = isBlurred ? ' brightness(.92)' : '';
  const blurFilter = isBlurred ? ' blur(28px) brightness(0.35) saturate(1.2)' : '';
  const baseStyle = {
    width: '100%',
    height: '100%',
    objectFit,
    objectPosition: focalPosition,
    transform,
    transformOrigin: focal
      ? `${((Number(focal[0]) + Number(focal[2])) / 2) * 100}% ${((Number(focal[1]) + Number(focal[3])) / 2) * 100}%`
      : 'center',
    filter: baseGrade + dimFilter + blurFilter,
  };

  if (!src) return <AbsoluteFill style={{ background: `radial-gradient(circle at 40% 30%, ${rgba(theme.palette.secondary, .3)}, transparent 55%), ${theme.palette.background}` }} />;
  const visual = (style = {}) => isVideo
    ? <OffthreadVideo src={src} muted {...videoTiming} onError={(error) => { console.warn(`[Remotion] Scene ${scene?.scene_index ?? 'unknown'} video asset failed; switching to its graphic recovery treatment.`, error?.message || error); setMediaFailed(true); }} style={{ ...baseStyle, ...style }} />
    : <Img src={src} onImageError={(error) => { console.warn(`[Remotion] Scene ${scene?.scene_index ?? 'unknown'} image asset failed; switching to its graphic recovery treatment.`, error?.message || error); setMediaFailed(true); }} style={{ ...baseStyle, ...style }} />;
  if (safeFraming && (effectiveTreatment === 'hold' || effectiveTreatment === 'cinematic_grade')) {
    return <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', overflow: 'hidden' }}>
      {visual({ position: 'absolute', inset: '-10%', width: '120%', height: '120%', objectFit: 'cover', filter: 'blur(28px) brightness(0.35) saturate(1.2)' })}
      {visual({ position: 'relative', width: '92%', height: '90%', objectFit: 'contain', filter: baseGrade + dimFilter, transform: `translateY(${(1 - reveal) * 4}%)`, borderRadius: 8, boxShadow: '0 20px 50px rgba(0,0,0,.55)' })}
    </AbsoluteFill>;
  }
  if (effectiveTreatment === 'blurred_stage') {
    return <AbsoluteFill style={{ padding: '2% 3%', justifyContent: 'center', alignItems: 'center', overflow: 'hidden' }}>
      {visual({ position: 'absolute', inset: '-10%', width: '120%', height: '120%', objectFit: 'cover', filter: 'blur(28px) brightness(0.35) saturate(1.2)' })}
      {visual({ position: 'relative', width: '94%', height: '94%', objectFit: 'contain', filter: baseGrade + dimFilter, transform: `translateY(${(1 - reveal) * 5}%)`, borderRadius: 6, boxShadow: '0 16px 34px rgba(0,0,0,.35)' })}
    </AbsoluteFill>;
  }
  if (effectiveTreatment === 'split_mask') {
    return <AbsoluteFill style={{ overflow: 'hidden' }}>
      {visual({ position: 'absolute', inset: '-10%', width: '120%', height: '120%', objectFit: 'cover', filter: 'blur(28px) brightness(0.35) saturate(1.2)' })}
      <AbsoluteFill style={{ clipPath: 'inset(0 50% 0 0)', transform: `translateX(${(1 - reveal) * -6}%)` }}>{visual()}</AbsoluteFill>
      <AbsoluteFill style={{ clipPath: 'inset(0 0 0 50%)', transform: `translateX(${(1 - reveal) * 6}%)` }}>{visual()}</AbsoluteFill>
      <div style={{ position: 'absolute', left: '50%', top: '7%', bottom: '7%', width: 2, background: rgba(theme.palette.accent, .6), transform: `scaleY(${reveal})` }} />
    </AbsoluteFill>;
  }
  if (effectiveTreatment === 'paper_reveal') {
    return <AbsoluteFill style={{ overflow: 'hidden' }}>
      {visual({ position: 'absolute', inset: '-10%', width: '120%', height: '120%', objectFit: 'cover', filter: 'blur(28px) brightness(0.35) saturate(1.2)' })}
      <AbsoluteFill style={{ clipPath: `polygon(0 0,${reveal * 96}% 0,${reveal * 100}% 12%,${reveal * 96}% 25%,${reveal * 100}% 40%,${reveal * 95}% 57%,${reveal * 100}% 74%,${reveal * 96}% 88%,${reveal * 100}% 100%,0 100%)`, filter: 'drop-shadow(14px 0 16px rgba(0,0,0,.55))' }}>
        {visual()}
      </AbsoluteFill>
    </AbsoluteFill>;
  }
  if (effectiveTreatment === 'editorial_crop' || effectiveTreatment === 'subject_slide') {
    const direction = motion.includes('right') ? 1 : -1;
    return <AbsoluteFill style={{ padding: '2.5% 3.5%', justifyContent: 'center', overflow: 'hidden' }}>
      {visual({ position: 'absolute', inset: '-10%', width: '120%', height: '120%', objectFit: 'cover', filter: 'blur(28px) brightness(0.35) saturate(1.2)' })}
      {visual({ position: 'relative', height: '95%', clipPath: `inset(0 ${(1 - reveal) * 70}% 0 0)`, boxShadow: `8px 10px 0 ${rgba(theme.palette.accent, .24)}`, transform: `translateX(${direction * (1 - reveal) * 5}%)` })}
    </AbsoluteFill>;
  }
  if (effectiveTreatment === 'film_gate') {
    return <AbsoluteFill style={{ padding: '2% 2.5%', background: rgba(theme.palette.background, .06) }}>
      {visual({ borderRadius: 3, filter: baseStyle.filter, boxShadow: 'inset 0 0 14px rgba(0,0,0,.12)' })}
      <div style={{ position: 'absolute', inset: '2% 3.2%', border: '1px solid rgba(255,244,215,.22)', opacity: reveal }} />
    </AbsoluteFill>;
  }
  if (effectiveTreatment === 'map_window') {
    return <AbsoluteFill style={{ overflow: 'hidden' }}>
      {visual({ position: 'absolute', inset: '-10%', width: '120%', height: '120%', objectFit: 'cover', filter: 'blur(28px) brightness(0.35) saturate(1.2)' })}
      <AbsoluteFill style={{ clipPath: `ellipse(${30 + reveal * 42}% ${38 + reveal * 28}% at 50% 50%)` }}>{visual()}</AbsoluteFill>
    </AbsoluteFill>;
  }
  if (effectiveTreatment === 'archival_white_border' || effectiveTreatment === 'archival_frame') {
    const tilt = Number(scene?.design?.seed || scene?.scene_index || 1) % 2 === 0 ? -1.2 : 1.2;
    return <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', overflow: 'hidden' }}>
      {visual({ position: 'absolute', inset: '-10%', width: '120%', height: '120%', objectFit: 'cover', filter: 'blur(28px) brightness(0.35) saturate(1.2)' })}
      <div style={{
        position: 'relative',
        width: '85%',
        height: '84%',
        background: '#f5f5f7',
        padding: '16px 16px 28px 16px',
        borderRadius: 4,
        boxShadow: '0 24px 60px rgba(0,0,0,0.7), 0 2px 8px rgba(0,0,0,0.4)',
        transform: `rotate(${tilt}deg) translateY(${(1 - reveal) * 4}%) scale(${0.96 + reveal * 0.04})`,
        display: 'flex',
        flexDirection: 'column',
      }}>
        <div style={{ flex: 1, width: '100%', overflow: 'hidden', borderRadius: 2 }}>
          {visual({ width: '100%', height: '100%', objectFit: 'cover', filter: baseGrade })}
        </div>
      </div>
    </AbsoluteFill>;
  }
  if (effectiveTreatment === 'film_matte_16mm' || effectiveTreatment === '16mm_matte') {
    return <AbsoluteFill style={{ background: '#08080a', padding: '3.5% 5%', justifyContent: 'center', alignItems: 'center', overflow: 'hidden' }}>
      <div style={{
        position: 'relative',
        width: '100%',
        height: '100%',
        borderRadius: 24,
        overflow: 'hidden',
        boxShadow: '0 0 40px rgba(0,0,0,0.9), inset 0 0 30px rgba(0,0,0,0.85)',
      }}>
        {visual({ width: '100%', height: '100%', objectFit: 'cover', filter: baseGrade + ' contrast(1.06)' })}
        <AbsoluteFill style={{
          pointerEvents: 'none',
          boxShadow: 'inset 0 0 60px rgba(0,0,0,0.8)',
          borderRadius: 24,
        }} />
      </div>
    </AbsoluteFill>;
  }
  if (effectiveTreatment === 'side_by_side_collage' || effectiveTreatment === 'side_by_side') {
    const accentCol = theme?.palette?.accent || '#ffd166';
    return <AbsoluteFill style={{ overflow: 'hidden', background: theme?.palette?.background || '#000' }}>
      <div style={{
        position: 'absolute',
        left: 0,
        top: 0,
        bottom: 0,
        width: '49.8%',
        overflow: 'hidden',
        transform: `translateX(${(1 - reveal) * -4}%)`,
      }}>
        {visual({ width: '100%', height: '100%', objectFit: 'cover', objectPosition: '30% center', transform: 'scale(1.06)' })}
      </div>
      <div style={{
        position: 'absolute',
        right: 0,
        top: 0,
        bottom: 0,
        width: '49.8%',
        overflow: 'hidden',
        transform: `translateX(${(1 - reveal) * 4}%)`,
      }}>
        {visual({ width: '100%', height: '100%', objectFit: 'cover', objectPosition: '70% center', filter: baseGrade + ' brightness(0.92) contrast(1.04)', transform: 'scale(1.06)' })}
      </div>
      <div style={{
        position: 'absolute',
        left: '50%',
        top: '4%',
        bottom: '4%',
        width: 3,
        background: `linear-gradient(180deg, transparent 0%, ${accentCol} 25%, #FFFFFF 50%, ${accentCol} 75%, transparent 100%)`,
        transform: 'translateX(-50%)',
        boxShadow: `0 0 16px ${rgba(accentCol, 0.85)}, 0 0 32px ${rgba('#ffffff', 0.5)}`,
        zIndex: 2,
      }} />
    </AbsoluteFill>;
  }
  if (effectiveTreatment === 'depth_drift_25d' || effectiveTreatment === '25d_drift') {
    return <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', overflow: 'hidden' }}>
      {visual({
        position: 'absolute',
        inset: '-15%',
        width: '130%',
        height: '130%',
        objectFit: 'cover',
        filter: 'blur(32px) brightness(0.30) saturate(1.25)',
        transform: `scale(${1.04 + progress * 0.03}) translate(${(progress * 1.6).toFixed(2)}%, ${(progress * 0.8).toFixed(2)}%)`,
      })}
      <div style={{
        position: 'relative',
        width: '88%',
        height: '88%',
        borderRadius: 10,
        overflow: 'hidden',
        boxShadow: '0 28px 70px rgba(0,0,0,0.75), 0 0 1px rgba(255,255,255,0.2)',
        transform: `scale(${1.0 + progress * 0.07}) translate(${(-progress * 2.2).toFixed(2)}%, ${(-progress * 1.2).toFixed(2)}%) translateY(${(1 - reveal) * 4}%)`,
      }}>
        {visual({ width: '100%', height: '100%', objectFit: 'cover', filter: baseGrade })}
      </div>
    </AbsoluteFill>;
  }
  if (effectiveTreatment === 'spotlight' || effectiveTreatment === 'iris_focus') {
    const x = focal ? ((Number(focal[0]) + Number(focal[2])) / 2) * 100 : 50;
    const y = focal ? ((Number(focal[1]) + Number(focal[3])) / 2) * 100 : 45;
    if (isVideo) {
      return <AbsoluteFill>
        {visual()}
        {explicitFocusDim && <AbsoluteFill style={{ background: `radial-gradient(circle at ${x}% ${y}%,transparent 0 ${12 + reveal * 16}%,rgba(0,0,0,.12) ${26 + reveal * 20}%)` }} />}
      </AbsoluteFill>;
    }
    return <AbsoluteFill>
      {visual({ filter: explicitFocusDim ? `${baseStyle.filter} brightness(.96) saturate(.96)` : baseStyle.filter })}
      <AbsoluteFill style={{ clipPath: `circle(${8 + reveal * 20}% at ${x}% ${y}%)` }}>{visual()}</AbsoluteFill>
    </AbsoluteFill>;
  }
  return visual();
}

function SceneMediaLayer({ media, theme, scene, visualDNA = {}, isBlurred = false }) {
  const window = scene.editor_media_window;
  return (
    <Sequence from={window?.start_frame || 0} durationInFrames={window?.duration_frames || scene.duration_in_frames}>
    <MediaShotSequence scene={scene} media={media || {}}>
      {({ scene: shotScene, media: shotMedia }) => (
        <MediaLayer media={shotMedia} theme={theme} scene={shotScene} visualDNA={visualDNA} isBlurred={isBlurred} />
      )}
    </MediaShotSequence>
    </Sequence>
  );
}

const KNOWN_SFX_FILES = new Set([
  // Edit directory sound effects (27 files)
  'sound-effects/edit/edit_book_close.wav',
  'sound-effects/edit/edit_check_tick.wav',
  'sound-effects/edit/edit_cursor_blink.wav',
  'sound-effects/edit/edit_flash_bulb.wav',
  'sound-effects/edit/edit_hit_boomed.wav',
  'sound-effects/edit/edit_hit_dry.wav',
  'sound-effects/edit/edit_hit_tight.wav',
  'sound-effects/edit/edit_marker_underline.wav',
  'sound-effects/edit/edit_paper_shuffle.wav',
  'sound-effects/edit/edit_paper_tear_subtle.wav',
  'sound-effects/edit/edit_reveal_chime.wav',
  'sound-effects/edit/edit_reveal_soft.wav',
  'sound-effects/edit/edit_room_shift.wav',
  'sound-effects/edit/edit_shutter_snap.wav',
  'sound-effects/edit/edit_slider_move.wav',
  'sound-effects/edit/edit_staple.wav',
  'sound-effects/edit/edit_sting_major.wav',
  'sound-effects/edit/edit_sting_minor.wav',
  'sound-effects/edit/edit_tape_stop.wav',
  'sound-effects/edit/edit_type_burst_short.wav',
  'sound-effects/edit/edit_type_click.wav',
  'sound-effects/edit/edit_vinyl_pop.wav',
  'sound-effects/edit/edit_whoosh_air.wav',
  'sound-effects/edit/edit_whoosh_deep.wav',
  'sound-effects/edit/edit_whoosh_light.wav',
  'sound-effects/edit/edit_whoosh_sucked.wav',
  'sound-effects/edit/edit_zoom_whirr.wav',
  // Vidzeo directory sound effects (61 files)
  'sound-effects/vidzeo/archive_drone.wav',
  'sound-effects/vidzeo/bass_drop.wav',
  'sound-effects/vidzeo/calendar_flip.wav',
  'sound-effects/vidzeo/camera_shutter.wav',
  'sound-effects/vidzeo/camera_wind.wav',
  'sound-effects/vidzeo/cash_counter.wav',
  'sound-effects/vidzeo/clock_tick.wav',
  'sound-effects/vidzeo/coin_tick.wav',
  'sound-effects/vidzeo/crowd_murmur.wav',
  'sound-effects/vidzeo/data_tick.wav',
  'sound-effects/vidzeo/decision_tick.wav',
  'sound-effects/vidzeo/film_splice.wav',
  'sound-effects/vidzeo/heartbeat.wav',
  'sound-effects/vidzeo/impact.wav',
  'sound-effects/vidzeo/impact_glass.wav',
  'sound-effects/vidzeo/impact_metal.wav',
  'sound-effects/vidzeo/impact_soft.wav',
  'sound-effects/vidzeo/impact_wood.wav',
  'sound-effects/vidzeo/keyboard_single.wav',
  'sound-effects/vidzeo/lens_click.wav',
  'sound-effects/vidzeo/low_rumble.wav',
  'sound-effects/vidzeo/map_draw.wav',
  'sound-effects/vidzeo/map_pulse.wav',
  'sound-effects/vidzeo/marker_scratch.wav',
  'sound-effects/vidzeo/mouse_click.wav',
  'sound-effects/vidzeo/news_sting.wav',
  'sound-effects/vidzeo/notification_down.wav',
  'sound-effects/vidzeo/notification_up.wav',
  'sound-effects/vidzeo/page_stack.wav',
  'sound-effects/vidzeo/paper_flip.wav',
  'sound-effects/vidzeo/paper_rustle.wav',
  'sound-effects/vidzeo/paper_tear.wav',
  'sound-effects/vidzeo/pencil_mark.wav',
  'sound-effects/vidzeo/photo_slide.wav',
  'sound-effects/vidzeo/projector.wav',
  'sound-effects/vidzeo/radio_static.wav',
  'sound-effects/vidzeo/reveal_chime.wav',
  'sound-effects/vidzeo/reverse_swell.wav',
  'sound-effects/vidzeo/riser.wav',
  'sound-effects/vidzeo/room_tone.wav',
  'sound-effects/vidzeo/route_ping.wav',
  'sound-effects/vidzeo/signal_ping.wav',
  'sound-effects/vidzeo/sonar.wav',
  'sound-effects/vidzeo/stamp_hit.wav',
  'sound-effects/vidzeo/sub_boom.wav',
  'sound-effects/vidzeo/suspense_pulse.wav',
  'sound-effects/vidzeo/technical_tick.wav',
  'sound-effects/vidzeo/telegraph.wav',
  'sound-effects/vidzeo/tension_string.wav',
  'sound-effects/vidzeo/transition_suck.wav',
  'sound-effects/vidzeo/typewriter.wav',
  'sound-effects/vidzeo/typing_burst.wav',
  'sound-effects/vidzeo/ui_tick.wav',
  'sound-effects/vidzeo/vinyl_crackle.wav',
  'sound-effects/vidzeo/water_pulse.wav',
  'sound-effects/vidzeo/whoosh.wav',
  'sound-effects/vidzeo/whoosh_air.wav',
  'sound-effects/vidzeo/whoosh_dark.wav',
  'sound-effects/vidzeo/whoosh_snap.wav',
  'sound-effects/vidzeo/whoosh_soft.wav',
  'sound-effects/vidzeo/wind_room.wav'
]);

const SFX_REGISTRY_MAP = {
  // Whooshes & transitions — bare 'whoosh' maps to edit_whoosh_light.wav
  'whoosh': 'sound-effects/edit/edit_whoosh_light.wav',
  'whoosh.light': 'sound-effects/edit/edit_whoosh_light.wav',
  'whoosh_light': 'sound-effects/edit/edit_whoosh_light.wav',
  'edit_whoosh_light': 'sound-effects/edit/edit_whoosh_light.wav',
  'whoosh.deep': 'sound-effects/edit/edit_whoosh_deep.wav',
  'whoosh_deep': 'sound-effects/edit/edit_whoosh_deep.wav',
  'edit_whoosh_deep': 'sound-effects/edit/edit_whoosh_deep.wav',
  'whoosh.air': 'sound-effects/edit/edit_whoosh_air.wav',
  'whoosh_air': 'sound-effects/edit/edit_whoosh_air.wav',
  'edit_whoosh_air': 'sound-effects/edit/edit_whoosh_air.wav',
  'whoosh.soft': 'sound-effects/vidzeo/whoosh_soft.wav',
  'whoosh_soft': 'sound-effects/vidzeo/whoosh_soft.wav',
  'whoosh.snap': 'sound-effects/vidzeo/whoosh_snap.wav',
  'whoosh_snap': 'sound-effects/vidzeo/whoosh_snap.wav',
  'whoosh.dark': 'sound-effects/vidzeo/whoosh_dark.wav',
  'whoosh_dark': 'sound-effects/vidzeo/whoosh_dark.wav',
  'whoosh.vidzeo': 'sound-effects/vidzeo/whoosh.wav',
  'whoosh_vidzeo': 'sound-effects/vidzeo/whoosh.wav',
  'transition.suck': 'sound-effects/vidzeo/transition_suck.wav',
  'transition_suck': 'sound-effects/vidzeo/transition_suck.wav',
  'edit.whoosh.sucked': 'sound-effects/edit/edit_whoosh_sucked.wav',
  'edit_whoosh_sucked': 'sound-effects/edit/edit_whoosh_sucked.wav',
  'edit.slider.move': 'sound-effects/edit/edit_slider_move.wav',
  'edit_slider_move': 'sound-effects/edit/edit_slider_move.wav',
  'edit.zoom.whirr': 'sound-effects/edit/edit_zoom_whirr.wav',
  'edit_zoom_whirr': 'sound-effects/edit/edit_zoom_whirr.wav',
  'edit.hit.tight': 'sound-effects/edit/edit_hit_tight.wav',

  // Paper & documents
  'paper.shuffle': 'sound-effects/edit/edit_paper_shuffle.wav',
  'paper_shuffle': 'sound-effects/edit/edit_paper_shuffle.wav',
  'edit_paper_shuffle': 'sound-effects/edit/edit_paper_shuffle.wav',
  'paper.tear': 'sound-effects/vidzeo/paper_tear.wav',
  'paper_tear': 'sound-effects/vidzeo/paper_tear.wav',
  'edit_paper_tear_subtle': 'sound-effects/edit/edit_paper_tear_subtle.wav',
  'paper.flip': 'sound-effects/vidzeo/paper_flip.wav',
  'paper_flip': 'sound-effects/vidzeo/paper_flip.wav',
  'paper.rustle': 'sound-effects/vidzeo/paper_rustle.wav',
  'paper_rustle': 'sound-effects/vidzeo/paper_rustle.wav',
  'marker.scratch': 'sound-effects/vidzeo/marker_scratch.wav',
  'marker_scratch': 'sound-effects/vidzeo/marker_scratch.wav',
  'pencil.mark': 'sound-effects/vidzeo/pencil_mark.wav',
  'pencil_mark': 'sound-effects/vidzeo/pencil_mark.wav',
  'stamp.hit': 'sound-effects/vidzeo/stamp_hit.wav',
  'stamp_hit': 'sound-effects/vidzeo/stamp_hit.wav',
  'pin.click': 'sound-effects/edit/edit_staple.wav',
  'pin_click': 'sound-effects/edit/edit_staple.wav',
  'staple': 'sound-effects/edit/edit_staple.wav',
  'marker.underline': 'sound-effects/edit/edit_marker_underline.wav',
  'marker_underline': 'sound-effects/edit/edit_marker_underline.wav',
  'edit_staple': 'sound-effects/edit/edit_staple.wav',
  'page.stack': 'sound-effects/vidzeo/page_stack.wav',
  'page_stack': 'sound-effects/vidzeo/page_stack.wav',

  // Impacts, stings & risers
  'impact.soft': 'sound-effects/vidzeo/impact_soft.wav',
  'impact_soft': 'sound-effects/vidzeo/impact_soft.wav',
  'impact.heavy': 'sound-effects/vidzeo/impact.wav',
  'impact': 'sound-effects/vidzeo/impact.wav',
  'sub.boom': 'sound-effects/vidzeo/sub_boom.wav',
  'sub_boom': 'sound-effects/vidzeo/sub_boom.wav',
  'bass.drop': 'sound-effects/vidzeo/bass_drop.wav',
  'bass_drop': 'sound-effects/vidzeo/bass_drop.wav',
  'hit.dry': 'sound-effects/edit/edit_hit_dry.wav',
  'hit_dry': 'sound-effects/edit/edit_hit_dry.wav',
  'edit_hit_dry': 'sound-effects/edit/edit_hit_dry.wav',
  'edit_hit_tight': 'sound-effects/edit/edit_hit_tight.wav',
  'sting.major': 'sound-effects/edit/edit_sting_major.wav',
  'sting_major': 'sound-effects/edit/edit_sting_major.wav',
  'sting.minor': 'sound-effects/edit/edit_sting_minor.wav',
  'sting_minor': 'sound-effects/edit/edit_sting_minor.wav',
  'edit_sting_minor': 'sound-effects/edit/edit_sting_minor.wav',
  'riser.tension': 'sound-effects/vidzeo/riser.wav',
  'riser': 'sound-effects/vidzeo/riser.wav',
  'reveal.chime': 'sound-effects/vidzeo/reveal_chime.wav',
  'reveal_chime': 'sound-effects/vidzeo/reveal_chime.wav',
  'reveal.soft': 'sound-effects/edit/edit_reveal_soft.wav',
  'reveal_soft': 'sound-effects/edit/edit_reveal_soft.wav',
  'edit_reveal_soft': 'sound-effects/edit/edit_reveal_soft.wav',
  'reverse.swell': 'sound-effects/vidzeo/reverse_swell.wav',
  'reverse_swell': 'sound-effects/vidzeo/reverse_swell.wav',
  'news.sting': 'sound-effects/vidzeo/news_sting.wav',
  'news_sting': 'sound-effects/vidzeo/news_sting.wav',

  // Telemetry & metrics
  'data.tick': 'sound-effects/vidzeo/data_tick.wav',
  'data_tick': 'sound-effects/vidzeo/data_tick.wav',
  'technical.tick': 'sound-effects/vidzeo/technical_tick.wav',
  'technical_tick': 'sound-effects/vidzeo/technical_tick.wav',
  'coin.tick': 'sound-effects/vidzeo/coin_tick.wav',
  'coin_tick': 'sound-effects/vidzeo/coin_tick.wav',
  'cash.counter': 'sound-effects/vidzeo/cash_counter.wav',
  'cash_counter': 'sound-effects/vidzeo/cash_counter.wav',
  'clock.tick': 'sound-effects/vidzeo/clock_tick.wav',
  'clock_tick': 'sound-effects/vidzeo/clock_tick.wav',
  'ui_tick': 'sound-effects/vidzeo/ui_tick.wav',
  'ui.tick': 'sound-effects/vidzeo/ui_tick.wav',
  'decision_tick': 'sound-effects/vidzeo/decision_tick.wav',
  'decision.tick': 'sound-effects/vidzeo/decision_tick.wav',

  // Maps & radar
  'map.pulse': 'sound-effects/vidzeo/map_pulse.wav',
  'map_pulse': 'sound-effects/vidzeo/map_pulse.wav',
  'map.ping': 'sound-effects/vidzeo/map_pulse.wav',
  'map_ping': 'sound-effects/vidzeo/map_pulse.wav',
  'map.draw': 'sound-effects/vidzeo/map_draw.wav',
  'map_draw': 'sound-effects/vidzeo/map_draw.wav',
  'sonar.ping': 'sound-effects/vidzeo/sonar.wav',
  'sonar': 'sound-effects/vidzeo/sonar.wav',
  'route.ping': 'sound-effects/vidzeo/route_ping.wav',
  'route_ping': 'sound-effects/vidzeo/route_ping.wav',
  'signal.ping': 'sound-effects/vidzeo/signal_ping.wav',
  'signal_ping': 'sound-effects/vidzeo/signal_ping.wav',

  // Camera & profile
  'camera.shutter': 'sound-effects/vidzeo/camera_shutter.wav',
  'camera_shutter': 'sound-effects/vidzeo/camera_shutter.wav',
  'camera.wind': 'sound-effects/vidzeo/camera_wind.wav',
  'camera_wind': 'sound-effects/vidzeo/camera_wind.wav',
  'photo.slide': 'sound-effects/vidzeo/photo_slide.wav',
  'photo_slide': 'sound-effects/vidzeo/photo_slide.wav',

  // Typing & text
  'type.click': 'sound-effects/vidzeo/typewriter.wav',
  'typewriter': 'sound-effects/vidzeo/typewriter.wav',
  'type.burst': 'sound-effects/vidzeo/typing_burst.wav',
  'typing_burst': 'sound-effects/vidzeo/typing_burst.wav',
  'edit_type_click': 'sound-effects/edit/edit_type_click.wav',
  'edit_type_burst_short': 'sound-effects/edit/edit_type_burst_short.wav',

  // Ambient & background
  'room_tone': 'sound-effects/vidzeo/room_tone.wav',
  'low_rumble': 'sound-effects/vidzeo/low_rumble.wav',
  'suspense_pulse': 'sound-effects/vidzeo/suspense_pulse.wav',
  'archive_drone': 'sound-effects/vidzeo/archive_drone.wav',
  'wind_room': 'sound-effects/vidzeo/wind_room.wav',
  'vinyl_crackle': 'sound-effects/vidzeo/vinyl_crackle.wav',
  'heartbeat': 'sound-effects/vidzeo/heartbeat.wav',
  'water_pulse': 'sound-effects/vidzeo/water_pulse.wav'
};

const KNOWN_MUSIC_FILES = new Set([
  'music/after-death.mp3',
  'music/ambient_dark.mp3',
  'music/british_orchestral.mp3',
  'music/cinematic.mp3',
  'music/classic-wheels.mp3',
  'music/crown-royal.mp3',
  'music/crown-zone.mp3',
  'music/documentary.mp3',
  'music/industrial_acoustic.mp3',
  'music/investigative_tension.mp3',
  'music/modern_cinematic.mp3',
  'music/nostalgic-brit.mp3',
  'music/retro_groove.mp3',
  'music/royal-celeb.mp3',
  'music/royal_orchestral.mp3',
  'music/suspense.mp3',
  'music/train-crew.mp3'
]);

const KNOWN_MAP_FILES = new Set([
  'map-assets/nasa-blue-marble-topography-mercator.jpg',
  'map-assets/nasa-blue-marble-july-5400.jpg',
  'map-assets/panama-canal-satellite-eox-2016.jpg'
]);

const MUSIC_REGISTRY_MAP = {
  'classic-wheels': 'music/classic-wheels.mp3',
  'classic_wheels': 'music/classic-wheels.mp3',
  'nostalgic-brit': 'music/nostalgic-brit.mp3',
  'nostalgic_brit': 'music/nostalgic-brit.mp3',
  'royal-celeb': 'music/royal-celeb.mp3',
  'royal_celeb': 'music/royal-celeb.mp3',
  'crown-royal': 'music/crown-royal.mp3',
  'crown_royal': 'music/crown-royal.mp3',
  'crown-zone': 'music/crown-zone.mp3',
  'crown_zone': 'music/crown-zone.mp3',
  'after-death': 'music/after-death.mp3',
  'after_death': 'music/after-death.mp3',
  'train-crew': 'music/train-crew.mp3',
  'train_crew': 'music/train-crew.mp3',
  'documentary': 'music/documentary.mp3',
  'cinematic': 'music/cinematic.mp3',
  'ambient_dark': 'music/ambient_dark.mp3',
  'ambient-dark': 'music/ambient_dark.mp3',
  'investigative_tension': 'music/investigative_tension.mp3',
  'investigative-tension': 'music/investigative_tension.mp3',
  'british_orchestral': 'music/british_orchestral.mp3',
  'british-orchestral': 'music/british_orchestral.mp3',
  'royal_orchestral': 'music/royal_orchestral.mp3',
  'royal-orchestral': 'music/royal_orchestral.mp3',
  'industrial_acoustic': 'music/industrial_acoustic.mp3',
  'industrial-acoustic': 'music/industrial_acoustic.mp3',
  'modern_cinematic': 'music/modern_cinematic.mp3',
  'modern-cinematic': 'music/modern_cinematic.mp3',
  'retro_groove': 'music/retro_groove.mp3',
  'retro-groove': 'music/retro_groove.mp3',
  'suspense': 'music/suspense.mp3'
};

function resolveSfxRelativePath(raw) {
  if (!raw || typeof raw !== 'string') return null;
  const trimmed = raw.trim();
  if (!trimmed || trimmed === 'none' || trimmed === 'null' || trimmed === 'undefined') return null;

  if (trimmed.startsWith('http://') || trimmed.startsWith('https://') || trimmed.startsWith('data:') || trimmed.startsWith('blob:') || trimmed.startsWith('file://')) {
    return trimmed;
  }

  const normalized = trimmed.replace(/\\/g, '/').replace(/^\/+/, '');

  // 1. Exact path containing sound-effects/ that matches verified existing file
  const sfxIdx = normalized.indexOf('sound-effects/');
  if (sfxIdx !== -1) {
    const sfxRel = normalized.slice(sfxIdx);
    if (KNOWN_SFX_FILES.has(sfxRel)) {
      return sfxRel;
    }
  }

  // 2. Extract base identifier
  let baseId = normalized;
  if (baseId.includes('?')) {
    baseId = baseId.split('?')[0];
  }
  if (baseId.endsWith('.wav') || baseId.endsWith('.mp3') || baseId.endsWith('.ogg') || baseId.endsWith('.aac') || baseId.endsWith('.m4a')) {
    baseId = baseId.slice(0, baseId.lastIndexOf('.'));
  }
  if (baseId.includes('/')) {
    baseId = baseId.split('/').pop();
  }

  const cleanId = baseId.toLowerCase().trim();
  if (!cleanId) return null;

  const dotId = cleanId.replace(/_/g, '.');
  const underscoreId = cleanId.replace(/\./g, '_');

  // 3. Registry map lookup
  const mapped = SFX_REGISTRY_MAP[cleanId]
    || SFX_REGISTRY_MAP[dotId]
    || SFX_REGISTRY_MAP[underscoreId]
    || SFX_REGISTRY_MAP['edit_' + underscoreId];
  if (mapped && KNOWN_SFX_FILES.has(mapped)) {
    return mapped;
  }

  // 4. Edit folder prefix check (edit_*.wav)
  if (cleanId.startsWith('edit_')) {
    const editCandidate = `sound-effects/edit/${cleanId}.wav`;
    if (KNOWN_SFX_FILES.has(editCandidate)) {
      return editCandidate;
    }
  }

  // 5. Vidzeo folder direct check
  const vidzeoCandidate = `sound-effects/vidzeo/${underscoreId}.wav`;
  if (KNOWN_SFX_FILES.has(vidzeoCandidate)) {
    return vidzeoCandidate;
  }

  // 6. Edit folder fallback check (edit_<name>.wav)
  const editFallback = `sound-effects/edit/edit_${underscoreId}.wav`;
  if (KNOWN_SFX_FILES.has(editFallback)) {
    return editFallback;
  }

  // 7. Edit folder fallback without edit_ prefix
  const editDirect = `sound-effects/edit/${underscoreId}.wav`;
  if (KNOWN_SFX_FILES.has(editDirect)) {
    return editDirect;
  }

  // 8. Whoosh & Transition family fallback (bare whoosh, variants, trans_whoosh, etc.)
  if (cleanId === 'whoosh' || cleanId.startsWith('whoosh') || cleanId.endsWith('whoosh') || cleanId === 'transition' || cleanId.startsWith('trans_whoosh') || cleanId.startsWith('edit_whoosh')) {
    if (cleanId.includes('deep')) return 'sound-effects/edit/edit_whoosh_deep.wav';
    if (cleanId.includes('air')) return 'sound-effects/edit/edit_whoosh_air.wav';
    if (cleanId.includes('soft')) return 'sound-effects/vidzeo/whoosh_soft.wav';
    if (cleanId.includes('snap')) return 'sound-effects/vidzeo/whoosh_snap.wav';
    if (cleanId.includes('dark')) return 'sound-effects/vidzeo/whoosh_dark.wav';
    return 'sound-effects/edit/edit_whoosh_light.wav';
  }

  return null;
}


function resolveSfxPath(raw) {
  const relPath = resolveSfxRelativePath(raw);
  if (!relPath) return null;
  if (relPath.startsWith('http://') || relPath.startsWith('https://') || relPath.startsWith('data:') || relPath.startsWith('blob:') || relPath.startsWith('file://')) {
    return relPath;
  }
  return staticFile(relPath);
}

function resolveSfxSource(cue) {
  if (!cue) return null;
  if (typeof cue === 'string') {
    return resolveSfxPath(cue);
  }

  // 1. Explicit candidate properties
  const candidate = cue.sfx
    || cue.sfx_id
    || cue.sound_id
    || cue.asset_id
    || cue.token
    || cue.file
    || cue.sound
    || cue.effect
    || cue.effect_id
    || (cue.kind && cue.kind !== 'transition' && cue.kind !== 'feature' ? cue.kind : null)
    || (cue.type && cue.type !== 'transition' && cue.type !== 'feature' ? cue.type : null)
    || cue.asset
    || cue.src
    || cue.path
    || (cue.id && !String(cue.id).startsWith('sfx-') ? cue.id : null)
    || cue.name;

  if (candidate && typeof candidate === 'string') {
    const resolved = resolveSfxPath(candidate);
    if (resolved) return resolved;
  }

  // 2. Default transition sound fallback
  if (cue.kind === 'transition' || cue.type === 'transition' || String(cue.id || '').includes('trans')) {
    return staticFile('sound-effects/edit/edit_whoosh_light.wav');
  }

  return null;
}

function resolveMusicRelativePath(raw) {
  if (!raw || typeof raw !== 'string') return null;
  const trimmed = raw.trim();
  if (!trimmed || trimmed === 'none' || trimmed === 'null' || trimmed === 'undefined') return null;

  if (trimmed.startsWith('http://') || trimmed.startsWith('https://') || trimmed.startsWith('data:') || trimmed.startsWith('blob:') || trimmed.startsWith('file://')) {
    return trimmed;
  }

  const normalized = trimmed.replace(/\\/g, '/').replace(/^\/+/, '');

  const musicIdx = normalized.indexOf('music/');
  if (musicIdx !== -1) {
    const musicRel = normalized.slice(musicIdx);
    if (KNOWN_MUSIC_FILES.has(musicRel)) return musicRel;
  }

  let baseId = normalized;
  if (baseId.endsWith('.mp3') || baseId.endsWith('.wav')) {
    baseId = baseId.slice(0, baseId.lastIndexOf('.'));
  }
  if (baseId.includes('/')) {
    baseId = baseId.split('/').pop();
  }

  const cleanId = baseId.toLowerCase().trim();
  const hyphenId = cleanId.replace(/_/g, '-');
  const underscoreId = cleanId.replace(/-/g, '_');

  const mapped = MUSIC_REGISTRY_MAP[cleanId] || MUSIC_REGISTRY_MAP[hyphenId] || MUSIC_REGISTRY_MAP[underscoreId];
  if (mapped && KNOWN_MUSIC_FILES.has(mapped)) return mapped;

  const candidateHyphen = `music/${hyphenId}.mp3`;
  if (KNOWN_MUSIC_FILES.has(candidateHyphen)) return candidateHyphen;

  const candidateUnderscore = `music/${underscoreId}.mp3`;
  if (KNOWN_MUSIC_FILES.has(candidateUnderscore)) return candidateUnderscore;

  if (cleanId.includes('doc') || cleanId.includes('track') || cleanId.includes('soundtrack')) {
    return 'music/documentary.mp3';
  }

  return null;
}

function resolveMusicPath(raw) {
  const relPath = resolveMusicRelativePath(raw);
  if (!relPath) return null;
  if (relPath.startsWith('http://') || relPath.startsWith('https://') || relPath.startsWith('data:') || relPath.startsWith('blob:') || relPath.startsWith('file://')) {
    return relPath;
  }
  return staticFile(relPath);
}

function resolveStaticAsset(value) {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  if (!trimmed || trimmed === 'none' || trimmed === 'null' || trimmed === 'undefined') return null;
  if (trimmed.startsWith('http://') || trimmed.startsWith('https://') || trimmed.startsWith('data:') || trimmed.startsWith('blob:') || trimmed.startsWith('file://')) {
    return trimmed;
  }
  const normalized = trimmed.replace(/\\/g, '/');

  // 1. Check SFX registry
  const sfxResolved = resolveSfxPath(trimmed);
  if (sfxResolved) {
    return sfxResolved;
  }
  // If it was explicitly a sound-effects/ path but could not be resolved, do not request it (prevent 404)
  if (normalized.includes('sound-effects/')) {
    return null;
  }

  // 2. Check Music registry
  const musicResolved = resolveMusicPath(trimmed);
  if (musicResolved) {
    return musicResolved;
  }
  if (normalized.includes('music/') && (normalized.endsWith('.mp3') || normalized.endsWith('.wav'))) {
    return null;
  }

  // Any audio file that cannot be resolved in SFX or Music registry MUST return null.
  // Never allow Remotion to request a non-existent audio file from the webpack server (prevents fatal 404).
  const isAudioFile = /\.(?:wav|mp3|ogg|aac|m4a|flac)(?:[?#]|$)/i.test(trimmed);
  if (isAudioFile) {
    return null;
  }

  // 3. Map assets
  if (normalized.includes('map-assets/') || normalized.includes('blue-marble') || normalized.includes('panama-canal') || normalized.includes('satellite-eox')) {
    const mapRel = normalized.includes('map-assets/')
      ? normalized.slice(normalized.indexOf('map-assets/'))
      : `map-assets/${normalized.split('/').pop()}`;
    const verifiedMap = KNOWN_MAP_FILES.has(mapRel) ? mapRel : 'map-assets/nasa-blue-marble-topography-mercator.jpg';
    return staticFile(verifiedMap);
  }

  // 4. Remotion assets
  const remotionIdx = normalized.indexOf('remotion-');
  if (remotionIdx !== -1) {
    return staticFile(normalized.slice(remotionIdx));
  }

  // 5. Font library
  if (normalized.includes('font-library/')) {
    return staticFile(normalized.slice(normalized.indexOf('font-library/')));
  }

  // 6. Bare identifier without path or extension: return null to prevent 404
  if (!trimmed.includes('/') && !trimmed.includes('\\') && !trimmed.includes('.')) {
    return null;
  }

  // Otherwise, treat as relative static file
  return staticFile(trimmed.replace(/^\/+/, ''));
}


function TimedCaptionTrack({ scene, fps, theme }) {
  const frame = useCurrentFrame();
  const { width } = useVideoConfig();
  if (!scene.captions_enabled || scene.editor_track_visibility?.captions === false) return null;
  const settings = scene.caption_settings || {};
  // Captions have their own reserved band. Never drop spoken words merely
  // because the director is showing a heading or explanatory graphic.
  const hideDuringGraphics = settings.hide_during_graphics === true;
  const copy = scene.copy_window;
  if (hideDuringGraphics && scene.editor_track_visibility?.copy !== false && copy && frame >= copy.start_frame && frame < copy.start_frame + copy.duration_frames) return null;
  const time = scene.editor_caption_source_seconds
    ?? (frame / fps + (scene.caption_clock === 'scene' ? 0 : Number(scene.start || 0)));
  // Optional editorial suppression is explicit; normal graphics retain the
  // separate caption band so every spoken beat stays available.
  const graphic = scene.graphic || {};
  const graphicId = String(graphic.template_id || graphic.type || '').toLowerCase().replace(/_/g, '-');
  const graphicIsTextBearing = Boolean(graphicId && !['full-bleed', 'image', 'video', 'source-media'].includes(graphicId));
  const graphicFrom = scene.editor_graphic_window
    ? Number(scene.editor_graphic_window.start_frame || 0)
    : Math.round(Math.max(0, Number(graphic.scene_local_start || 0)) * fps);
  const graphicDuration = scene.editor_graphic_window
    ? Math.max(1, Number(scene.editor_graphic_window.duration_frames || 1))
    : Math.max(1, Math.round((Number(graphic.scene_local_end ?? scene.duration ?? 0) - Number(graphic.scene_local_start || 0)) * fps));
  if (hideDuringGraphics && graphicIsTextBearing && frame >= graphicFrom && frame < graphicFrom + graphicDuration) return null;
  const textOverlays = Array.isArray(scene.overlays) ? scene.overlays : [];
  const activeTextOverlay = textOverlays.some(overlay => {
    const props = overlay?.props || {};
    const hasText = [overlay?.text, overlay?.title, overlay?.label, props.text, props.title, props.label]
      .some(value => String(value || '').trim());
    const start = Math.max(0, Number(overlay?.scene_local_start || 0) * fps);
    const end = overlay?.scene_local_end !== undefined
      ? Number(overlay.scene_local_end) * fps
      : start + Number(overlay?.duration || scene.duration || 0) * fps;
    return hasText && frame >= start && frame < end;
  });
  if (hideDuringGraphics && activeTextOverlay) return null;
  const primaryType = String(scene.primary_visual?.type || '');
  const explanatory = ['Chart', 'Diagram', 'Timeline', 'Comparison', 'AnimatedMap'].includes(primaryType);
  const ownsGraphic = ['primary_explanation', 'map_canvas'].includes(String(scene.visual_role || ''));
  if (hideDuringGraphics && explanatory && ownsGraphic && !scene.editor_graphic_window) return null;
  if (hideDuringGraphics && primaryType === 'StatCard') {
    const start = cueStartFrame(scene, 'data', fps, .1);
    const hold = Math.min(Number(scene.duration || 4) * fps - start, fps * (ownsGraphic ? 5 : 2.4));
    if (frame >= start && frame < start + hold) return null;
  }
  const rawWords = Array.isArray(scene.caption_words) && scene.caption_words.length
    ? scene.caption_words
    : (Array.isArray(scene.word_cues) ? scene.word_cues.flatMap(cue => Array.isArray(cue.words) ? cue.words : [cue]) : []);
  const words = rawWords.map((word, index) => ({
    text: String(word.text || word.word || '').trim(),
    start: Number(word.start ?? word.start_seconds),
    end: Number(word.end ?? word.end_seconds),
    index,
  })).filter(word => word.text && Number.isFinite(word.start) && Number.isFinite(word.end) && word.end > word.start);
  if (!words.length) return null;

  const mode = String(settings.mode || 'word').toLowerCase();
  const styleName = ['documentary', 'impact', 'clean', 'archive'].includes(String(settings.style || '').toLowerCase())
    ? String(settings.style).toLowerCase() : 'documentary';
  // Keep captions to a short spoken beat. Impact captions deliberately use
  // fewer words so the selected word can carry the visual emphasis cleanly.
  const phraseLimit = styleName === 'impact' ? 3 : mode === 'phrase' ? 5 : 4;
  const segments = [];
  let segment = [];
  for (const word of words) {
    const previous = segment[segment.length - 1];
    const nextText = segment.concat(word).map(row => row.text).join(' ');
    const sentenceEnds = previous && /[.!?]["')\]]?$/.test(previous.text);
    const largePause = previous && word.start - previous.end > .62;
    const characterLimit = styleName === 'impact' ? 27 : 36;
    if (segment.length && (segment.length >= phraseLimit || nextText.length > characterLimit || sentenceEnds || largePause)) {
      segments.push(segment);
      segment = [];
    }
    segment.push(word);
  }
  if (segment.length) segments.push(segment);
  const activeSegment = segments.find(rows => time >= rows[0].start && time < rows[rows.length - 1].end);
  if (!activeSegment) return null;
  const activeWord = activeSegment.find(word => time >= word.start && time < word.end);
  const fontName = ['DM Sans', 'Archivo Black', 'Barlow Condensed', 'Plus Jakarta Sans', 'JetBrains Mono'].includes(settings.font)
    ? settings.font : 'DM Sans';
  const palette = theme?.palette || {};
  const accent = palette.accent || '#E5B24B';
  const accentHex = /^#([0-9a-f]{6})$/i.exec(String(accent).trim());
  const accentChannels = accentHex
    ? [0, 2, 4].map(offset => parseInt(accentHex[1].slice(offset, offset + 2), 16) / 255)
      .map(channel => channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4)
    : [0.78, 0.58, 0.25];
  const accentLuminance = .2126 * accentChannels[0] + .7152 * accentChannels[1] + .0722 * accentChannels[2];
  const accentInk = accentLuminance > .46 ? '#071018' : '#fff';
  const placement = String(settings.position || 'bottom').toLowerCase();
  const anchor = placement === 'top'
    ? { top: '7%' }
    : placement === 'middle'
      ? { top: '50%', transform: 'translateY(-50%)' }
      : { bottom: '7%' };
  const rawSafeRegion = Array.isArray(scene.media_composition?.caption_region)
    ? scene.media_composition.caption_region.map(Number)
    : [.08, .83, .92, .97];
  const safeRegion = rawSafeRegion && rawSafeRegion.length === 4
    && rawSafeRegion.every(Number.isFinite)
    && rawSafeRegion[2] - rawSafeRegion[0] >= .24
    && rawSafeRegion[3] - rawSafeRegion[1] >= .08
    ? rawSafeRegion.map(value => Math.max(0, Math.min(1, value)))
    : null;
  const useSafeRegion = placement === 'auto' && safeRegion;
  const safeWidth = useSafeRegion ? safeRegion[2] - safeRegion[0] : 1;
  const horizontalAnchor = useSafeRegion
    ? (safeRegion[0] + safeRegion[2]) / 2 < .38 ? 'flex-start'
      : (safeRegion[0] + safeRegion[2]) / 2 > .62 ? 'flex-end' : 'center'
    : 'center';
  const captionContainer = useSafeRegion
    ? { position: 'absolute', left: `${safeRegion[0] * 100}%`, top: `${safeRegion[1] * 100}%`, width: `${safeWidth * 100}%`, height: `${(safeRegion[3] - safeRegion[1]) * 100}%`, alignItems: 'center', justifyContent: horizontalAnchor }
    : { position: 'absolute', left: '6%', right: '6%', ...anchor, justifyContent: 'center' };
  const elapsedFrames = Math.max(0, Math.round((time - activeSegment[0].start) * fps));
  const enter = interpolate(elapsedFrames, [0, 8], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const activeEmphasis = ['word', 'karaoke', 'smart_emphasis'].includes(mode) && activeWord;
  const baseTextSize = width * (styleName === 'impact' ? 0.038 : styleName === 'clean' ? 0.029 : styleName === 'archive' ? 0.027 : 0.03);
  const textSize = Math.round(useSafeRegion ? Math.min(baseTextSize, width * safeWidth * 0.105) : baseTextSize);
  const fontWeight = styleName === 'impact' ? 900 : styleName === 'archive' ? 800 : 750;
  const face = fontName;
  const textShadow = styleName === 'impact'
    ? '0 3px 2px rgba(0,0,0,.9), 0 5px 16px rgba(0,0,0,.9), 0 1px 1px #000'
    : '0 2px 3px rgba(0,0,0,.96), 0 4px 12px rgba(0,0,0,.85)';
  const wordsView = activeSegment.map((word, index) => {
    const emphasize = activeEmphasis && word.index === activeWord.index;
    const punctuation = /[,.;:!?]["')\]]?$/.test(word.text);
    return <React.Fragment key={`${word.index}-${word.start}`}>
      <span style={{
        display: emphasize && styleName === 'impact' ? 'inline-block' : 'inline',
        marginRight: punctuation ? 0 : '.2em',
        padding: emphasize && styleName === 'impact' ? '.015em .09em .045em' : '0',
        color: emphasize && styleName === 'impact' ? accentInk : emphasize ? accent : '#fff',
        background: emphasize && styleName === 'impact' ? accent : 'transparent',
        boxDecorationBreak: 'clone',
        WebkitBoxDecorationBreak: 'clone',
        textShadow: emphasize && styleName === 'impact' ? '0 1px 2px rgba(0,0,0,.68)' : textShadow,
      }}>{word.text}</span>
      {index < activeSegment.length - 1 ? ' ' : ''}
    </React.Fragment>;
  });
  return <div style={{ ...captionContainer, zIndex: 30, pointerEvents: 'none', display: 'flex' }}>
    <div style={{ maxWidth: useSafeRegion ? '100%' : '88%', width: 'fit-content', textAlign: horizontalAnchor === 'flex-start' ? 'left' : horizontalAnchor === 'flex-end' ? 'right' : 'center', opacity: enter, transform: `translateY(${(1 - enter) * 6}px)`, color: '#fff', fontFamily: `${face}, sans-serif`, fontWeight, fontSize: textSize, lineHeight: 1.12, letterSpacing: styleName === 'archive' ? '.035em' : styleName === 'impact' ? '-.025em' : '-.012em', textTransform: styleName === 'impact' || styleName === 'archive' ? 'uppercase' : 'none', textShadow, borderBottom: styleName === 'documentary' ? `2px solid ${accent}` : 'none', padding: styleName === 'documentary' ? '0 0 .1em' : 0, filter: styleName === 'impact' ? 'drop-shadow(0 4px 9px rgba(0,0,0,.42))' : 'none' }}>
      {wordsView}
    </div>
  </div>;
}

function mediaOverlayTypeForTemplate(templateId = '') {
  const id = String(templateId || '').toLowerCase().replace(/_/g, '-');
  if (id.includes('chart') || id === 'ranked-bars' || id === 'area-chart' || id === 'donut-chart') return 'Chart';
  if (id.includes('map') || id.includes('route')) return 'AnimatedMap';
  if (id.includes('timeline') || id.includes('date') || id.includes('chronology')) return 'Timeline';
  if (id.includes('quote') || id.includes('witness')) return 'QuoteCard';
  if (id.includes('profile') || id.includes('person') || id.includes('lower-third')) return 'PersonProfile';
  if (id.includes('diagram') || id.includes('comparison') || id.includes('signal') || id.includes('terminal')) return 'Diagram';
  if (id.includes('archive') || id.includes('gallery') || id.includes('collage')) return 'Collage';
  if (id.includes('chapter') || id.includes('slate') || id.includes('headline')) return 'ChapterTitle';
  // Headline, chapter, evidence, field-caption, keyword, and legacy contract
  // ids all resolve to the same restrained media-safe headline grammar.
  return 'Headline';
}

function mediaOverlayScene(scene, templateId, graphicProps) {
  const existing = scene.media_composition || {};
  const cuePlacement = scene.graphic?.cue?.placement || scene.cue?.placement || scene.placement || {};
  const position = String(graphicProps?._position || graphicProps?.position || cuePlacement.position || '').toLowerCase();
  const centered = position.includes('center') || position.includes('hero') || templateId === 'headline-center' || templateId === 'kinetic-statement';
  const type = mediaOverlayTypeForTemplate(templateId);
  return {
    ...scene,
    primary_visual: {
      ...(scene.primary_visual || {}),
      type,
      variant: graphicProps?.variant || templateId,
      props: graphicProps,
    },
    media_composition: {
      enabled: existing.enabled !== false,
      ...existing,
      placement: centered ? 'center' : (position || existing.placement || 'top_left'),
      center_data_visual: centered,
    },
    placement: {
      ...cuePlacement,
      position: centered ? 'center' : (position || cuePlacement.position || 'top_left'),
    },
  };
}

function renderMediaSafeOverlay(scene, overlay, visualDNA, theme) {
  const templateId = overlay?.template_id || overlay?.type || 'field-caption';
  const overlayProps = sanitizeDocumentaryProps({ ...(overlay?.props || {}) }, scene);
  const overlayScene = mediaOverlayScene(scene, templateId, overlayProps);
  return <MediaOverlayCanvas scene={overlayScene} visualDNA={visualDNA} props={overlayProps} theme={theme} />;
}

function Scene({ scene: rawScene, theme, visualDNA: compositionDNA = {}, fps = 30 }) {
  const filmFrame = useCurrentFrame();
  // Normalize Director Lego syntax (primary, background, overlay, camera, transitionOut)
  const normalizedPrimary = rawScene.primary_visual || rawScene.primary || null;
  let normalizedMedia = rawScene.media || rawScene.background || (normalizedPrimary?.asset) || null;
  if (typeof normalizedMedia === 'string') {
    normalizedMedia = { type: 'image', src: normalizedMedia };
  }
  const rawOverlays = Array.isArray(rawScene.overlays) ? [...rawScene.overlays] : [];
  if (rawScene.overlay && typeof rawScene.overlay === 'object') {
    rawOverlays.push(rawScene.overlay);
  }
  const scene = {
    ...rawScene,
    primary_visual: normalizedPrimary,
    media: normalizedMedia,
    overlays: rawOverlays,
    transition_out: rawScene.transition_out || rawScene.transitionOut || rawScene.transition?.template_id,
    camera: rawScene.camera || rawScene.motion,
  };

  const sceneDNA = scene.visual_dna || scene.visualDNA || (scene.genre ? { genre: scene.genre } : null);
  const resolvedDNA = sceneDNA ? resolveVisualDNA(sceneDNA) : compositionDNA;
  // A scene-level palette is part of the director's visual grammar. The
  // composition palette is only a film-wide default; ignoring this field made
  // specialist components fall back to generic yellow/teal colors.
  const visualDNA = scene.palette && typeof scene.palette === 'object'
    ? { ...resolvedDNA, palette: { ...(resolvedDNA.palette || {}), ...scene.palette } }
    : resolvedDNA;

  // Prefer scene.from (pre-computed exact integer frame offset set by native_render_policy)
  // over scene.start × fps which accumulates floating-point rounding drift across scenes
  // and causes the last frame to stick when the total drifts below the actual duration.
  const {from, duration} = sceneFrameWindow(scene, fps);
  const frame = filmFrame - from;
  const graphic = scene.graphic || {};
  const primaryType = scene.primary_visual?.type || '';
  const editorialTiming = scene.editorial_contract?.timing || scene.primary_visual?.props?.editorial_contract?.timing || {};
  const sceneTemplateId = String(scene.template_id || '').trim();
  const graphicTemplateId = String(graphic.template_id || graphic.vox_template || '').trim();
  // A scene's generic `full-bleed` media label must not mask the director's
  // selected specialist treatment. Keep explicit scene templates authoritative,
  // while letting the semantic graphic template replace generic media routing.
  const templateId = (!sceneTemplateId || ['full-bleed', 'media-only', 'image', 'video'].includes(sceneTemplateId.toLowerCase()))
    ? (graphicTemplateId || sceneTemplateId || 'full-bleed')
    : sceneTemplateId;
  const recipe = scene.template_recipe || graphic.props?.template_recipe || {};
  const recipeTheme = {
    ...theme,
    palette: { ...theme.palette, ...(visualDNA.palette || {}) },
    display_font: visualDNA.typography?.display || theme.display_font,
    font_family: visualDNA.typography?.body || theme.font_family,
  };
  const hasGeneratedRecipe = templateId === 'generated-recipe' && recipeHasGroundedVisual(recipe, scene);
  const recipeHasChart = hasGeneratedRecipe && recipe.layers.some((layer) => layer?.kind === 'chart' && Array.isArray(layer.data) && layer.data.length >= 2);
  const recipeHasMap = hasGeneratedRecipe && recipe.layers.some((layer) => layer?.kind === 'map') && scene.map_shot?.enabled === true;
  const recipeHasTimeline = hasGeneratedRecipe && recipe.layers.some((layer) => layer?.kind === 'timeline' && Array.isArray(layer.data) && layer.data.length >= 2);
  const recipeHasDiagram = hasGeneratedRecipe && recipe.layers.some((layer) => layer?.kind === 'diagram' && Array.isArray(layer.data) && layer.data.length >= 2);
  const recipeHasComparison = hasGeneratedRecipe && recipe.layers.some((layer) => layer?.kind === 'comparison' && Array.isArray(layer.data) && layer.data.length === 2);
  // Profiles and sourced quotations contain essential semantic copy. A free
  // form recipe cannot replace or cover those components, so their validated
  // native Remotion variants own the scene when one of these is selected.
  const preserveSemanticCopy = hasGeneratedRecipe && ['PersonProfile', 'QuoteCard'].includes(primaryType);
  const recipeSpecialistType = ['AnimatedMap', 'Chart', 'Timeline', 'Diagram', 'Comparison', 'DocumentHighlight'].includes(primaryType);
  const recipeMediaOverlay = hasGeneratedRecipe
    && ['SmartImage', 'SmartVideo', 'BrollImage', 'BrollVideo', 'image', 'video'].includes(primaryType)
    && ['src', 'path', 'url', 'asset_path'].some((key) => Boolean(scene.media?.[key]))
    && String(scene.visual_role || '').toLowerCase() === 'media_overlay';
  const recipeOwnsGraphic = hasGeneratedRecipe && !preserveSemanticCopy && (
    !recipeMediaOverlay && (
      !recipeSpecialistType
      || (primaryType === 'Chart' && recipeHasChart)
      || (primaryType === 'AnimatedMap' && recipeHasMap)
      || (primaryType === 'Timeline' && recipeHasTimeline)
      || (primaryType === 'Diagram' && recipeHasDiagram)
      || (primaryType === 'Comparison' && recipeHasComparison)
    )
  );
  const recipeAugmentsSpecialist = hasGeneratedRecipe && !preserveSemanticCopy && !recipeOwnsGraphic
    && (recipeSpecialistType || recipeMediaOverlay);
  const transition = scene.transition?.template_id || scene.transition_out || 'cut';
  const cues = React.useMemo(() => {
    const list = Array.isArray(scene.sound_cues) ? [...scene.sound_cues] : [];
    const hasSfx = (id) => list.some(c => c.kind === id || c.sfx_id === id);
    const activeType = String(primaryType || templateId || '').toLowerCase();

    if ((activeType.includes('declass') || activeType.includes('redaction')) && !hasSfx('stamp.hit')) {
      list.push({ kind: 'paper.shuffle', time_local: 0, duration: 0.8, volume: 0.28 });
      list.push({ kind: 'stamp.hit', time_local: 16 / fps, duration: 0.7, volume: 0.42 });
      list.push({ kind: 'marker.scratch', time_local: 24 / fps, duration: 0.9, volume: 0.25 });
    } else if ((activeType.includes('map') || activeType.includes('route') || activeType.includes('flight')) && !hasSfx('map.ping')) {
      list.push({ kind: 'map.ping', time_local: 50 / fps, duration: 0.8, volume: 0.32 });
    } else if ((activeType.includes('pinboard') || activeType.includes('evidence')) && !hasSfx('pin.click')) {
      list.push({ kind: 'pin.click', time_local: 18 / fps, duration: 0.5, volume: 0.35 });
      list.push({ kind: 'pin.click', time_local: 32 / fps, duration: 0.5, volume: 0.35 });
    } else if (activeType.includes('blueprint') && !hasSfx('technical.tick')) {
      list.push({ kind: 'technical.tick', time_local: 12 / fps, duration: 0.6, volume: 0.25 });
    } else if (activeType.includes('depth') && !hasSfx('sub.boom')) {
      list.push({ kind: 'sub.boom', time_local: 6 / fps, duration: 1.2, volume: 0.38 });
    }
    return list;
  }, [scene.sound_cues, primaryType, templateId, fps]);
  const overlays = Array.isArray(scene.overlays) ? scene.overlays : [];
  const captionsEnabled = Boolean(scene.captions_enabled);
  const motionLevel = String(scene.motion_level || 'supporting').toLowerCase();
  const atmosphere = scene.atmosphere || {};
  const motionProps = {
    // primary_visual.props carries enriched semantic data for V2 scenes
    // (data arrays, events, quote, name, etc. set by the Python director layer).
    // graphic.props are explicit editorial overrides and win on conflict.
    ...(scene.primary_visual?.props || {}),
    ...(graphic.props || {}),
    motionTier: motionLevel,
    // Keep the director's selected grammar attached to the specialist
    // component. Replacing it with a generic numeric seed collapsed distinct
    // layouts into the same rendering path.
    variant: scene.primary_visual?.variant ?? graphic.variant ?? (Number(graphic.seed || 0) % 6),
  };
  const sanitizedMotionProps = sanitizeDocumentaryProps(motionProps, scene);
  const isVideoMedia = Boolean(scene.media && (scene.media.type === 'video' || String(scene.media.type || '').startsWith('video/')));
  const shakeIntensity = isVideoMedia && motionLevel === 'hero' ? 0.16 : 0;
  const particleCount = atmosphere.dust && Number(atmosphere.dust) > 0 ? Math.round(Number(atmosphere.dust) * 30) : 0;
  const flipFrame = String(scene?.camera?.motion || '').toLowerCase() === 'flip';
  const flipBorder = flipFrame && <AbsoluteFill style={{ pointerEvents: 'none', padding: '4.5%', border: `10px solid ${theme.palette.primary}`, opacity: 0.88 }} />;
  const cue = graphic?.cue || {};
  const legacyGraphicStart = Number(graphic.start ?? scene.start ?? 0) - Number(scene.start || 0);
  const graphicLocalStart = Math.max(0, Math.min(Number(scene.duration || 0), Number(
    editorialTiming.scene_local_start ?? graphic.scene_local_start ?? cue.scene_local_start ?? legacyGraphicStart,
  ) || 0));
  const graphicLocalEnd = Math.max(graphicLocalStart, Math.min(Number(scene.duration || 0), Number(
    editorialTiming.scene_local_end ?? (editorialTiming.total_seconds ? graphicLocalStart + Number(editorialTiming.total_seconds) : null) ?? graphic.scene_local_end ?? cue.scene_local_end ?? (graphicLocalStart + Number(graphic.duration || scene.duration || 0)),
  ) || Number(scene.duration || 0)));
  const graphicFrom = Math.max(0, Number(scene.editor_graphic_window?.start_frame ?? Math.round(graphicLocalStart * fps)));
  const graphicDuration = Math.max(1, Number(scene.editor_graphic_window?.duration_frames ?? Math.round((graphicLocalEnd - graphicLocalStart) * fps)));
  const scopedPrimaryScene = scene.editor_graphic_window ? {
    ...scene,
    narration_frame_offset: graphicFrom,
    duration: graphicDuration / fps,
    duration_in_frames: graphicDuration,
    motion_cues: (Array.isArray(scene.motion_cues) ? scene.motion_cues : []).map((motionCue) => ({
      ...motionCue,
      time_local: Math.max(0, (Number(motionCue.time_local) || 0) - graphicFrom / fps),
    })),
  } : scene;
  const overlayTiming = (overlay = {}) => {
    const start = Math.max(0, Number(overlay.scene_local_start ?? graphicLocalStart) || 0);
    const end = Math.max(start, Math.min(Number(scene.duration || 0), Number(
      overlay.scene_local_end ?? (start + Number(overlay.duration ?? (graphicLocalEnd - graphicLocalStart))),
    ) || graphicLocalEnd));
    return { from: Math.round(start * fps), duration: Math.max(1, Math.round((end - start) * fps)) };
  };

  const sceneEditorialContract = scene.editorial_contract || {};
  const primaryEditorialContract = scene.primary_visual?.props?.editorial_contract || {};
  const directedScene = scene.director_treatment_enabled === true
    || ['llm_director', 'llm_pixel_director', 'system_grounded_mapping'].includes(String(scene.visual_role_source || '').toLowerCase())
    || Object.prototype.hasOwnProperty.call(scene.text || {}, 'overlay_policy');
  const approvedCopyContract = Boolean(
    hasApprovedDirectorCopy(scene) &&
    scene.text?.position !== 'none'
  );
  const approvedCopyTemplateChoice = approvedCopyContract ? approvedCopyTemplate(scene) : null;
  const hasEditorialContract = Boolean(
    !directedScene && !approvedCopyContract && (
    (scene.editorial_contract && scene.editorial_contract.enabled !== false && scene.editorial_contract.family !== 'media_hold') ||
    (scene.primary_visual?.props?.editorial_contract && scene.primary_visual?.props?.editorial_contract?.enabled !== false && scene.primary_visual?.props?.editorial_contract?.family !== 'media_hold')
    )
  );
  const hasPrimaryVisual = Boolean(scene.primary_visual && scene.primary_visual.type);
  // Only components that actually render the approved contract internally
  // own its copy. Data graphics still need the short, separately timed
  // director overlay; otherwise AdaptiveChart can hide a matching value
  // during its cue window without displaying the approved phrase anywhere.
  const approvedCopyPrimaryText = approvedCopyContract && [
    'KineticText', 'ChapterTitle', 'Headline', 'StatCard',
  ].includes(primaryType);
  const copyWords = String(sceneEditorialContract.display_text || '').trim().split(/\s+/).filter(Boolean).length;
  const copySeconds = primaryType === 'ChapterTitle' || approvedCopyTemplateChoice?.component === 'ChapterTitle'
    ? Math.min(2.6, Math.max(1.6, 1.35 + copyWords * .22))
    : Math.min(copyWords > 3 ? 2.4 : 2.2, Math.max(1.3, .92 + copyWords * .32));
  const copyDuration = Math.min(duration, Math.max(1, Number(scene.copy_window?.duration_frames) || Math.round(copySeconds * fps)));
  const copyCue = directorCue(scene, 'copy');
  const copyInNarration = String(scene.narration || '').toLowerCase().indexOf(String(sceneEditorialContract.display_text || '').toLowerCase());
  const estimatedCopyStart = copyInNarration >= 0
    ? (copyInNarration / Math.max(1, String(scene.narration || '').length)) * Number(scene.duration || duration / fps)
    : .08;
  const copyFrom = Math.min(Math.max(0, duration - 1), Math.max(0, scene.copy_window?.start_frame ?? Math.round(Number(copyCue?.time_local ?? estimatedCopyStart) * fps)));
  const mediaHasSource = Boolean(scene.media && (
    scene.media.src || scene.media.path || scene.media.url || scene.media.asset_path
    || (Array.isArray(scene.media.shots) && scene.media.shots.length)
  ));
  const frameMode = String(scene.text?.frame_mode || scene.frame_mode || '').trim().toLowerCase();
  const cueProps = cue?.props && typeof cue.props === 'object' ? cue.props : {};
  const cueText = String(cue.text || cueProps._contract_text || '').replace(/\s+/g, ' ').trim();
  const cueStartSeconds = Math.max(0, Number(cue.scene_local_start_seconds ?? cue.scene_local_start ?? 0) || 0);
  const cueEndSeconds = Math.min(Number(scene.duration || 0), Number(cue.scene_local_end_seconds ?? cue.scene_local_end ?? 0) || 0);
  const cueGraphicEligible = Number(cue.schema_version) >= 2
    && cueProps._editorial_contract === true
    && String(cue.cue_type || '').toLowerCase() === 'location'
    && cueText.length > 0
    && mediaHasSource
    && ['SmartImage', 'SmartVideo', 'image', 'video'].includes(String(primaryType));
  const cueGraphicFrom = Math.max(0, Math.min(duration - 1, Math.round(cueStartSeconds * fps)));
  const cueGraphicDuration = Math.max(1, Math.min(duration - cueGraphicFrom, Math.round(Math.max(.45, cueEndSeconds - cueStartSeconds) * fps)));
  const cueGraphicScene = cueGraphicEligible ? {
    ...scene,
    placement: cue.placement || scene.placement,
    editorial_contract: {
      version: 4,
      approved: true,
      display_text: cueText,
      source_span: cueText,
      emphasis_spans: [],
      family: 'corner_identification',
      position: String(cue.placement?.position || cueProps._position || 'top_left'),
      motion: cueProps._entry_motion === 'slide_left' ? 'side_settle' : 'trace',
      timing: { total_seconds: cueGraphicDuration / fps, exit_seconds: .22 },
    },
  } : null;

  const directorGraphicType = DIRECTOR_GRAPHIC_TYPES[String(templateId || '').trim().toLowerCase()];
  const directorGraphicProps = directorGraphicType ? sanitizeDocumentaryProps({
    ...(scene.primary_visual?.props || {}),
    ...(graphic.props || {}),
    ...(graphic.vox_props || {}),
    variant: graphic.variant ?? graphic.props?.variant,
  }, scene) : null;
  const numericGraphicData = Array.isArray(directorGraphicProps?.data)
    ? directorGraphicProps.data
    : Array.isArray(directorGraphicProps?.values) ? directorGraphicProps.values : [];
  const hasGroundedGraphicData = numericGraphicData.length >= 2
    || (directorGraphicProps?.before?.value != null && directorGraphicProps?.after?.value != null)
    || (Array.isArray(directorGraphicProps?.steps) && directorGraphicProps.steps.filter(Boolean).length >= 2)
    || (Array.isArray(directorGraphicProps?.events) && directorGraphicProps.events.filter(Boolean).length >= 2)
    || Boolean(directorGraphicProps?.source_capture?.status === 'exact_passage_verified');
  const directorGraphicEligible = Boolean(
    directorGraphicType && mediaHasSource
    && ['SmartImage', 'SmartVideo', 'image', 'video'].includes(String(primaryType))
    && (directorGraphicType === 'AnimatedMap'
      ? (cueGraphicEligible || directorGraphicProps?.location || directorGraphicProps?.place || directorGraphicProps?.country || (directorGraphicProps?.from && directorGraphicProps?.to) || scene.map_shot?.location_names?.length)
      : directorGraphicType === 'StatCard'
        ? (directorGraphicProps?.value != null || directorGraphicProps?.stat != null || directorGraphicProps?.number != null)
        : directorGraphicType === 'Comparison'
          ? ((directorGraphicProps?.left || directorGraphicProps?.leftValue) && (directorGraphicProps?.right || directorGraphicProps?.rightValue))
          : directorGraphicType === 'Chart'
            ? (hasGroundedGraphicData || directorGraphicProps?.seriesA?.points?.length >= 2)
            : directorGraphicType === 'QuoteCard'
              ? Boolean(directorGraphicProps?.quote && (directorGraphicProps?.attribution || directorGraphicProps?.speaker))
              : directorGraphicType === 'PersonProfile'
                ? Boolean(directorGraphicProps?.name || directorGraphicProps?.person)
                : directorGraphicType === 'Collage'
                  ? (directorGraphicProps?.images?.length >= 2 || scene.media?.shots?.length >= 2)
                  : directorGraphicType === 'MediaFocusCard'
                    ? approvedCopyContract
                  : directorGraphicType === 'MetricGrid'
                    ? ((directorGraphicProps?.metrics || directorGraphicProps?.items || []).length >= 2)
                  : ['DeclassifiedDocument', 'IsometricMap', 'EvidencePinboard', 'TechnicalBlueprint', 'DepthTypography'].includes(directorGraphicType)
                    ? true
                  : hasGroundedGraphicData),
  );
  const approvedCopyGraphicOwned = Boolean(
    approvedCopyContract && ((primaryType === 'PersonProfile'
      && String(scene.primary_visual?.props?.name || '').trim().toLowerCase() === String(sceneEditorialContract.display_text || '').trim().toLowerCase())
    || (directorGraphicEligible && (
      (directorGraphicType === 'QuoteCard' && String(directorGraphicProps?.quote || '').trim().toLowerCase() === String(sceneEditorialContract.display_text || '').trim().toLowerCase())
      || (directorGraphicType === 'PersonProfile' && String(directorGraphicProps?.name || '').trim().toLowerCase() === String(sceneEditorialContract.display_text || '').trim().toLowerCase())
      || directorGraphicType === 'MediaFocusCard'
      || ['Timeline', 'StatCard'].includes(directorGraphicType)
    )))
  );
  // Specialist diagrams, timelines, and comparisons already animate their
  // own ordered data from these exact narration cues. Do not stack the
  // compact cue slate over the full graphic and repeat the same words.
  const activeDirectorGraphicType = directorGraphicEligible
    ? directorGraphicType
    : primaryType;
  const suppressedDirectorCueElements = [
    ...(activeDirectorGraphicType === 'Comparison' ? ['comparison'] : []),
    ...(['Diagram', 'Timeline'].includes(activeDirectorGraphicType) ? ['steps'] : []),
    ...(activeDirectorGraphicType === 'StatCard' ? ['steps', 'data'] : []),
  ];
  const directorGraphicFrom = cueGraphicEligible && directorGraphicType === 'AnimatedMap' ? cueGraphicFrom : graphicFrom;
  const directorGraphicDuration = cueGraphicEligible && directorGraphicType === 'AnimatedMap' ? cueGraphicDuration : graphicDuration;
  const directorGraphicScene = directorGraphicEligible ? {
    ...scene,
    duration_in_frames: directorGraphicDuration,
    duration: directorGraphicDuration / fps,
    primary_visual: {
      ...(scene.primary_visual || {}),
      type: directorGraphicType,
      variant: graphic.variant || scene.primary_visual?.variant || (templateId === 'map-comparison' ? 'city_to_city_transit' : undefined),
      props: {
        ...directorGraphicProps,
        ...(directorGraphicType === 'AnimatedMap' ? {
          location: directorGraphicProps?.location || directorGraphicProps?.place || cueText,
          map_shot: {
            ...(scene.map_shot || {}),
            ...(directorGraphicProps?.map_shot || {}),
            location_names: scene.map_shot?.location_names?.length
              ? scene.map_shot.location_names
              : [directorGraphicProps?.from, directorGraphicProps?.to, directorGraphicProps?.location || directorGraphicProps?.place || cueText].filter(Boolean),
            motion_profile: scene.map_shot?.motion_profile || 'city_reveal',
          },
        } : {}),
      },
    },
    graphic: { ...graphic, props: directorGraphicProps },
    director_treatment_enabled: true,
  } : null;
  // Overlays are scheduled in this scene's local frame clock. The compact
  // cue slate lives in the lower third, so keep it out of approved-copy and
  // full-canvas specialist-graphic windows rather than letting z-order decide
  // which message wins.
  const directorCueBlockedWindows = [
    ...(approvedCopyContract ? [{ start: copyFrom, end: copyFrom + copyDuration }] : []),
    ...(directorGraphicEligible ? [{ start: directorGraphicFrom, end: directorGraphicFrom + directorGraphicDuration }] : []),
    ...(recipeOwnsGraphic ? [{ start: graphicFrom, end: graphicFrom + graphicDuration }] : []),
    ...(hasPrimaryVisual && !['SmartImage', 'SmartVideo', 'image', 'video'].includes(String(primaryType))
      ? [scene.editor_graphic_window
        ? { start: graphicFrom, end: graphicFrom + graphicDuration }
        : { start: 0, end: duration }]
      : []),
  ];

  const renderPrimarySystem = (targetScene) => {
    if (!isStandaloneMedia) return renderSceneSystem(targetScene, visualDNA, { mediaFirst });
    // A profile owns its verified portrait and approved identities. Dispatching
    // it through the ordinary shot switcher would erase the specialist and
    // replace its labels with SmartImage / SmartVideo treatments.
    if (primaryType === 'PersonProfile') return renderSceneSystem(targetScene, visualDNA, { mediaFirst: false });
    // When a specialist graphic is directed over footage, render the source
    // through the shared cinematic media layer. SmartImage can intentionally
    // frame a standalone still like an archive plate; that treatment must not
    // box in a media_overlay scene whose graphic belongs over full-frame media.
    if (directorGraphicEligible && visualRoleVersion >= 2 && String(scene.visual_role || '').toLowerCase() === 'media_overlay') {
      return <SceneMediaLayer media={targetScene.media || {}} theme={theme} scene={targetScene} visualDNA={visualDNA} />;
    }
    const primaryAsset = targetScene.primary_visual?.asset || {};
    const shotMedia = Array.isArray(targetScene.media?.shots)
      ? targetScene.media
      : Array.isArray(primaryAsset.shots)
        ? primaryAsset
        : (targetScene.media || primaryAsset);
    return <Sequence from={targetScene.editor_media_window?.start_frame || 0} durationInFrames={targetScene.editor_media_window?.duration_frames || targetScene.duration_in_frames}><MediaShotSequence scene={targetScene} media={shotMedia}>
      {({ scene: activeScene, media: activeMedia }) => renderSceneSystem({
        ...activeScene,
        media: activeMedia,
        primary_visual: {
          ...(activeScene.primary_visual || {}),
          // A verified recovery may replace a repeated clip with a still.
          // Dispatch each shot by its own media type, not the original scene.
          type: resolveMediaKind(activeMedia) === 'video' ? 'SmartVideo' : 'SmartImage',
          asset: activeMedia,
        },
      }, visualDNA, { mediaFirst: false })}
    </MediaShotSequence></Sequence>;
  };

  // Detect if scene has a centered data chart or explicit background blur request
  const isChartScene = Boolean(
    primaryType === 'Chart' ||
    primaryType === 'Diagram' ||
    templateId.includes('chart') ||
    scene.primary_visual?.props?.background_blur ||
    scene.primary_visual?.props?.backgroundBlur
  );

  const mediaComposition = scene.media_composition || {};
  const centerDataVisual = Boolean(mediaComposition.center_data_visual || mediaComposition.placement === 'center');
  const placement = hasPrimaryVisual
    ? resolveCardPlacement(scene, scene.primary_visual?.props || {})
    : null;

  // Ambient gradient: only for centered full-screen data charts/diagrams where high contrast against dense numbers is mandatory.
  // Never inject screen-covering radial blur smudges over corner editorial cards (ChapterTitle, CalloutBox, etc.)
  let ambientGradient = null;
  if (isChartScene && mediaComposition.blur_media_underlay === true) {
    ambientGradient = centerDataVisual
      ? `radial-gradient(ellipse at center, rgba(0,0,0,0.08) 0%, rgba(0,0,0,0.34) 100%)`
      : `radial-gradient(ellipse at center, rgba(0,0,0,0.12) 0%, rgba(0,0,0,0.38) 100%)`;
  }

  const isStandaloneMedia = Boolean(
    !hasEditorialContract && (
      primaryType === 'SmartVideo' ||
      primaryType === 'SmartImage' ||
      primaryType === 'video' ||
      primaryType === 'image' ||
      (primaryType === 'PersonProfile' && mediaHasSource)
    )
  );
  // The v2 visual role decides which layer owns this scene. Maps own a clean
  // specialist canvas; other graphics leave verified footage visible unless
  // the director explicitly requested a primary explanation.
  const visualRoleVersion = Number(scene.visual_role_contract_version || 0);
  const mapOwnsCanvas = ['AnimatedMap', 'SatelliteMap', 'IsometricMap'].includes(primaryType)
    || String(scene.visual_role || '').toLowerCase() === 'map_canvas';
  const directorOwnsCanvas = visualRoleVersion >= 2
    && String(scene.visual_role || '').toLowerCase() === 'primary_explanation';
  const legacyExplicitCanvas = visualRoleVersion >= 2
    && mediaComposition.render_mode === 'explanatory_canvas'
    && mediaComposition.hero_layer === 'graphic';
  const beforeDataReveal = primaryType === 'StatCard' && frame < cueStartFrame(scene, 'data', fps, .1);
  const transparentMediaOverlay = mediaHasSource && scene.primary_visual?.props?.transparent_canvas === true;
  const graphicOwnsCanvas = mapOwnsCanvas || ((directorOwnsCanvas || legacyExplicitCanvas) && !beforeDataReveal && !transparentMediaOverlay);
  const showMediaBase = Boolean(!graphicOwnsCanvas && !isStandaloneMedia && scene.editor_track_visibility?.media !== false && scene.media && (
    scene.media.src || scene.media.path || scene.media.url || scene.media.asset_path
    || (Array.isArray(scene.media.shots) && scene.media.shots.length)
  ));
  const mediaFirst = Boolean(mediaHasSource && !isStandaloneMedia);
  const mediaSafeOverlays = (hasEditorialContract || hasPrimaryVisual)
    ? []
    : (mediaFirst ? overlays.filter((overlay) => overlay && overlay.media_safe === true) : overlays);

  // Diagnostics follow the actual mounted render branches/windows. Empty
  // selectors and decorative recipes have no explanatory category.
  const explanatoryAudit = {
    'data-documentary-graphic': scene._graphic_audit?.family ? 'explanatory' : 'unclassified',
    'data-graphic-family': scene._graphic_audit?.family || '',
    'data-graphic-variant': String(scene.primary_visual?.variant || graphic.variant || 'default'),
    'data-graphic-scene': String(scene.scene_index ?? 0),
  };
  const emphasisAudit = {
    'data-documentary-graphic': approvedCopyContract
      && String(scene.narration || '').toLowerCase().includes(String(sceneEditorialContract.display_text || '').toLowerCase())
      ? 'emphasis' : 'unclassified',
    'data-graphic-family': 'script_emphasis',
    'data-graphic-start': (from + copyFrom) / fps,
    'data-graphic-end': Math.min(from + duration, from + copyFrom + copyDuration) / fps,
    'data-graphic-variant': String(approvedCopyTemplateChoice?.variant || scene.primary_visual?.variant || 'default'),
    'data-graphic-scene': String(scene.scene_index ?? 0),
  };
  return <Sequence from={from} durationInFrames={duration} name={scene.id || `scene-${scene.scene_index}`}>
    <SceneRenderBoundary scene={scene} visualDNA={visualDNA} theme={theme}>
    <AbsoluteFill style={{ background: visualDNA?.palette?.background || theme.palette.background, overflow: 'hidden' }}>
      {/* Fallback ambient studio texture when a scene genuinely has no media */}
      {!showMediaBase && !isStandaloneMedia && (
        <MissingSceneMotionGraphic scene={scene} visualDNA={visualDNA} theme={theme} backgroundOnly={graphicOwnsCanvas || recipeOwnsGraphic || recoveryIsBackdrop(scene) || isStandaloneMedia} copyOwnedElsewhere={true} />
      )}

      {/* 1. Cinematic Media Base Layer (Rendered as backdrop for editorial cards, charts, and quotes) */}
      {scene.editor_track_visibility?.media !== false && showMediaBase && (
        <>
          <CameraShake intensity={shakeIntensity * ((hasPrimaryVisual || hasEditorialContract) ? 0.35 : 1)}>
            <SceneMediaLayer media={scene.media || {}} theme={theme} scene={scene} visualDNA={visualDNA} isBlurred={Boolean(mediaComposition.blur_media_underlay)} />
          </CameraShake>
          {ambientGradient && mediaComposition.blur_media_underlay === true && (
            <AbsoluteFill style={{ background: ambientGradient, pointerEvents: 'none' }} />
          )}
          {(hasPrimaryVisual || hasEditorialContract) && (
            <AbsoluteFill
              style={{
                background: directedScene
                  ? (approvedCopyContract ? 'linear-gradient(90deg, rgba(0,0,0,.42), rgba(0,0,0,.08) 58%, transparent)' : 'rgba(0,0,0,.08)')
                  : 'radial-gradient(ellipse at center, rgba(0,0,0,0.25) 0%, rgba(0,0,0,0.68) 100%)',
                pointerEvents: 'none',
              }}
            />
          )}
        </>
      )}

      {/* 2. Scene Graphics & Standalone Media */}
      {(['SmartImage','SmartVideo','BrollImage','BrollVideo'].includes(primaryType) ? scene.editor_track_visibility?.media !== false : ['KineticText','ChapterTitle','Headline'].includes(primaryType) ? scene.editor_track_visibility?.copy !== false : scene.editor_track_visibility?.graphics !== false) && (recipeOwnsGraphic ? (
        <Sequence from={graphicFrom} durationInFrames={graphicDuration} name={`recipe-${scene.scene_index || 0}`}>
          <AbsoluteFill {...explanatoryAudit} style={{ zIndex: 6 }}><DeclarativeRecipe recipe={recipe} scene={scene} theme={recipeTheme} /></AbsoluteFill>
        </Sequence>
      ) : hasEditorialContract ? (
        <Sequence from={graphicFrom} durationInFrames={graphicDuration} name={`editorial-${scene.scene_index || 0}`}>
          <AbsoluteFill {...explanatoryAudit} style={{ zIndex: 5 }}>
            <MediaOverlayCanvas scene={scene} visualDNA={visualDNA} props={sanitizedMotionProps} />
          </AbsoluteFill>
        </Sequence>
      ) : hasPrimaryVisual ? (
        scene.editor_track_visibility?.copy !== false && approvedCopyPrimaryText && ['KineticText', 'ChapterTitle', 'Headline'].includes(primaryType) ? (
          <Sequence from={copyFrom} durationInFrames={copyDuration} name={`heading-${scene.scene_index || 0}`}>
            <AbsoluteFill {...emphasisAudit} style={{ zIndex: 5 }}>{renderPrimarySystem({
              ...scene,
              narration_frame_offset: copyFrom,
              duration: copyDuration / fps,
              duration_in_frames: copyDuration,
              motion_cues: (Array.isArray(scene.motion_cues) ? scene.motion_cues : []).map((motionCue) => ({
                ...motionCue,
                time_local: Math.max(0, (Number(motionCue.time_local) || 0) - copyFrom / fps),
              })),
            })}</AbsoluteFill>
          </Sequence>
        ) :
        mediaFirst && editorialTiming.enabled ? (
          <Sequence from={graphicFrom} durationInFrames={graphicDuration} name={`editorial-${scene.scene_index || 0}`}>
            <AbsoluteFill {...explanatoryAudit} style={{ zIndex: 5 }}>{renderPrimarySystem(scene)}</AbsoluteFill>
          </Sequence>
        ) : <Sequence from={!isStandaloneMedia && scene.editor_graphic_window ? graphicFrom : 0} durationInFrames={!isStandaloneMedia && scene.editor_graphic_window ? graphicDuration : duration}><AbsoluteFill {...(['KineticText', 'ChapterTitle', 'Headline'].includes(primaryType) ? emphasisAudit : explanatoryAudit)} style={{ zIndex: 5 }}>{renderPrimarySystem(scopedPrimaryScene)}</AbsoluteFill></Sequence>
      ) : (
        <>
          {templateId !== 'full-bleed' && <Sequence from={graphicFrom} durationInFrames={graphicDuration} name={`cue-${scene.scene_index || 0}`}>
          <AbsoluteFill {...explanatoryAudit}>
            {mediaHasSource
              ? <MediaOverlayCanvas scene={mediaOverlayScene(scene, templateId, sanitizedMotionProps)} visualDNA={visualDNA} props={sanitizedMotionProps} />
              : <GraphicTemplate id={templateId} props={sanitizedMotionProps} theme={theme} />}
          </AbsoluteFill>
          </Sequence>}
        </>
      ))}

      {scene.editor_track_visibility?.graphics !== false && recipeAugmentsSpecialist && <Sequence from={graphicFrom} durationInFrames={graphicDuration} name={`recipe-detail-${scene.scene_index || 0}`}>
        <AbsoluteFill {...explanatoryAudit} style={{ zIndex: 6 }}><DeclarativeRecipe recipe={recipe} scene={scene} theme={recipeTheme} /></AbsoluteFill>
      </Sequence>}

      {/* A v4 approved copy contract is an overlay. It must not replace the
          scene's map, footage, chart, or other primary Remotion treatment. */}
      <SourceCredit scene={scene} />
      {scene.editor_track_visibility?.copy !== false && approvedCopyContract && !approvedCopyPrimaryText && !approvedCopyGraphicOwned && (approvedCopyTemplateChoice || scene.text?.panel_style !== 'none') && <Sequence from={copyFrom} durationInFrames={copyDuration} name={`approved-copy-${scene.scene_index || 0}`}>
        <AbsoluteFill {...emphasisAudit} style={{ zIndex: 12 }}>
        {approvedCopyTemplateChoice
          ? renderSceneSystem({
            ...scene,
            narration_frame_offset: copyFrom,
            duration: copyDuration / fps,
            duration_in_frames: copyDuration,
            text: {
              ...(scene.text || {}),
              position: scene.text?.position || scene.editorial_contract?.position || scene.design?.position || 'center',
              variant: approvedCopyTemplateChoice.variant,
            },
            primary_visual: {
              ...(scene.primary_visual || {}),
              type: approvedCopyTemplateChoice.component,
              variant: approvedCopyTemplateChoice.variant,
              props: {
                ...(scene.primary_visual?.props || {}),
                ...(graphic.props || {}),
                ...(scene.text || {}),
                display_text: scene.editorial_contract.display_text,
                text: scene.editorial_contract.display_text,
                variant: approvedCopyTemplateChoice.variant,
              },
            },
          }, visualDNA)
          : <TextCard
            scene={{ ...scene, duration_in_frames: copyDuration, graphic: { ...graphic, props: { ...(graphic.props || {}), ...scene.text, position: scene.text?.position || scene.design?.position } } }}
            visualDNA={visualDNA}
            props={sanitizedMotionProps}
            theme={theme}
          />}
        </AbsoluteFill>
      </Sequence>}

      {/* Keep the separate, word-aligned location cue visible over the footage. */}
      {scene.editor_track_visibility?.graphics !== false && cueGraphicEligible && !(directorGraphicEligible && directorGraphicType === 'AnimatedMap') && <Sequence from={cueGraphicFrom} durationInFrames={cueGraphicDuration} name={`location-cue-${scene.scene_index || 0}`}>
        <AbsoluteFill style={{ zIndex: 8 }}>
          <MediaOverlayCanvas scene={cueGraphicScene} visualDNA={visualDNA} props={cueProps} />
        </AbsoluteFill>
      </Sequence>}

      {/* Honor a selected semantic graphic on a media-first beat. The director
          can briefly replace footage with an explanatory map, or lay a
          grounded data/diagram treatment over it, at its authored timing. */}
      {scene.editor_track_visibility?.graphics !== false && directorGraphicEligible && <Sequence from={directorGraphicFrom} durationInFrames={directorGraphicDuration} name={`director-graphic-${scene.scene_index || 0}`}>
        <AbsoluteFill {...explanatoryAudit} style={{ zIndex: 11 }}>
          {directorGraphicType === 'MediaFocusCard'
            ? <GraphicTemplate id="media-focus" props={{ ...directorGraphicProps, title: String(scene.editorial_contract?.display_text || '') }} theme={theme} />
            : renderSceneSystem(directorGraphicScene, visualDNA, { mediaFirst: directorGraphicType !== 'AnimatedMap' })}
        </AbsoluteFill>
      </Sequence>}

      {/* Explicit comparison cues become staged, readable motion graphics over footage. */}
      {scene.editor_track_visibility?.graphics !== false && <DirectorMotionGraphics
        scene={scene}
        visualDNA={visualDNA}
        suppressedElements={suppressedDirectorCueElements}
        blockedWindows={directorCueBlockedWindows}
      />}

      {/* 3. Subtle Cinematic Atmosphere Overlays */}
      <OverlayStack scene={scene} theme={theme} />
      {!mediaFirst && particleCount > 0 && <Particles count={particleCount} theme={theme} intensity={0.5} />}
      {!mediaFirst && atmosphere.layers?.includes('light_leak') && <LightLeak theme={theme} intensity={Number(atmosphere.intensity || 0.2)} delay={0} />}
      {scene.editor_track_visibility?.transitions !== false && <TransitionOverlay id={transition} durationFrames={Math.max(3, Math.round(Number(scene.transition?.duration || scene.transition_duration || .3) * fps))} theme={theme} seed={Number(scene.design?.seed || scene.scene_index || 1)} />}
      {scene.editor_track_visibility?.graphics !== false && mediaSafeOverlays.map((overlay, index) => {
        const timing = overlayTiming(overlay);
        const templateId = overlay.template_id || overlay.type;
        return <Sequence key={`${templateId}-${index}`} from={timing.from} durationInFrames={timing.duration} name={`overlay-${scene.scene_index || 0}-${index}`}>
          {mediaHasSource
            ? renderMediaSafeOverlay(scene, overlay, visualDNA, theme)
            : <GraphicTemplate id={templateId} props={sanitizeDocumentaryProps(overlay.props || {}, scene)} theme={theme} />}
        </Sequence>;
      })}
      <TimedCaptionTrack scene={scene} fps={fps} theme={theme} />
      {cues.map((soundCue, index) => {
        const sfxSrc = resolveSfxSource(soundCue);
        if (!sfxSrc || typeof sfxSrc !== 'string' || !sfxSrc.trim()) return null;
        const rawLocalTime = (soundCue.time_local !== undefined && soundCue.time_local !== null)
          ? Number(soundCue.time_local)
          : (soundCue.start_offset_seconds !== undefined && soundCue.start_offset_seconds !== null)
            ? Math.max(0, Number(soundCue.start_offset_seconds))
            : Math.max(0, Number(soundCue.start || 0) - Number(scene.start || 0));
        const localTime = Number.isFinite(rawLocalTime) ? Math.max(0, rawLocalTime) : 0;
        const soundFrom = Math.max(0, Math.round(localTime * fps));
        const rawDuration = Number(soundCue.duration);
        const safeDuration = Number.isFinite(rawDuration) && rawDuration > 0 ? rawDuration : 0.6;
        const soundDuration = Math.max(1, Math.round(safeDuration * fps));
        // The soundtrack planner already measures/levels each asset; preserve
        // its gain and editor mutes instead of attenuating quiet ticks again.
        const rawSoundVolume = Number(soundCue.volume ?? 0.28);
        const soundVol = Math.min(1, Math.max(0, Number.isFinite(rawSoundVolume) ? rawSoundVolume : 0.28));
        const rawFadeIn = Number(soundCue.fade_in);
        const fadeIn = Math.max(1, Math.round((Number.isFinite(rawFadeIn) && rawFadeIn > 0 ? rawFadeIn : 0.012) * fps));
        const rawFadeOut = Number(soundCue.fade_out);
        const fadeOut = Math.max(1, Math.round((Number.isFinite(rawFadeOut) && rawFadeOut > 0 ? rawFadeOut : 0.08) * fps));
        const rawTrim = Number(soundCue.trim_start_seconds || 0);
        const startFromFrames = Math.max(0, Math.round((Number.isFinite(rawTrim) && rawTrim > 0 ? rawTrim : 0) * fps));
        return <Sequence key={`${soundCue.kind || soundCue.sfx_id || 'sfx'}-${index}`} from={soundFrom} durationInFrames={soundDuration} name={`sfx-${soundCue.kind || soundCue.sfx_id || index}`}>
          <Audio src={sfxSrc} startFrom={startFromFrames} volume={(frame) => soundVol * Math.min(1, Math.max(0, frame / fadeIn), Math.max(0, (soundDuration - frame) / fadeOut))} onError={(error) => console.warn(`[Remotion] Scene ${scene.scene_index ?? 'unknown'} sound effect skipped after load error.`, error?.message || error)} />
        </Sequence>;
      })}
    </AbsoluteFill>
    </SceneRenderBoundary>
  </Sequence>;
}

export function DocumentaryComposition({ manifest = {} }) {
  const fontsReady = useDocumentaryFontsReady();
  const visualDNA = React.useMemo(() => resolveVisualDNA(manifest.visual_dna || manifest.theme), [manifest.visual_dna, manifest.theme]);
  const theme = React.useMemo(() => mergeTheme(manifest.theme), [manifest.theme]);
  const fps = Number(manifest.fps || 30);
  const scenes = React.useMemo(() => buildRenderableScenes(manifest, fps), [manifest, fps]);
  const voiceover = manifest.audio?.voiceover || manifest.audio_plan?.voiceover || {};
  const music = manifest.audio?.music || manifest.audio_plan?.music || {};
  const captionsEnabled = Boolean(manifest.captions?.enabled);
  const frame = useCurrentFrame();
  const preparedScenes = activeSceneEntries(scenes, frame, fps).map(({scene,index}) => {
    const prepared = Timeline.sceneAtFrame({
      ...scene,
      captions_enabled: captionsEnabled && scene.caption_suppressed !== true,
      caption_settings: manifest.captions || scene.caption_settings || {},
    },index,frame,manifest);
    // A timeline track can hide its ordinary clips, but it cannot hide the
    // recovery scene inserted to fill a missing or broken part of the film.
    return scene.scene_missing ? {
      ...prepared,
      editor_track_visibility: { media: true, graphics: true, copy: true, camera: true, transitions: true, captions: true },
    } : prepared;
  });
  const voiceSrc = resolveStaticAsset(voiceover.src || voiceover.path || '');
  const musicSrc = resolveStaticAsset(music.src || music.path || '');
  if (!fontsReady) return <DocumentaryFontFaces />;
  return <AbsoluteFill data-documentary-canvas="true" data-graphic-review-signature={manifest.metrics?.rendered_graphics_audit?.review_signature || ""} style={{ background: visualDNA.palette.background || theme.palette.background, color: visualDNA.palette.primary || theme.palette.primary, fontFamily: visualDNA.typography.body || theme.font_family }}>
    <DocumentaryFontFaces />
    <DocumentaryLayoutAudit enabled={manifest.review_layout === true} />
    <VisualTexture texture={visualDNA.texture} color={visualDNA.palette.primary} />
    {preparedScenes.map(scene => <Scene key={scene.id || scene.scene_index} scene={scene} theme={theme} visualDNA={visualDNA} fps={fps} />)}
    <FilmGrain intensity={0.035} />
    <Vignette intensity={0.35} />
    {['narration','music'].map(id => {
      const track = manifest.editor_timeline?.tracks?.[id], src = id === 'narration' ? voiceSrc : musicSrc;
      if (track?.enabled === false || (!src && !track?.clips?.some(clip => clip.asset))) return null;
      const volume = id === 'narration' ? 1 : Number(music.volume ?? .08);
      const onTrackError = (error) => console.warn(`[Remotion] ${id} audio track skipped after load error; the video render continues.`, error?.message || error);
      if (!track) {
        if (!src || typeof src !== 'string' || !src.trim()) return null;
        return <Audio key={id} src={src} volume={volume} onError={onTrackError} />;
      }
      return track.clips.map((clip,index) => {
        const clipSrc = clip.asset ? resolveStaticAsset(clip.asset) : src;
        if (!clipSrc || typeof clipSrc !== 'string' || !clipSrc.trim()) return null;
        const clipFrom = Math.max(0, Number.isFinite(Number(clip.start_frame)) ? Math.round(Number(clip.start_frame)) : 0);
        const clipDuration = Math.max(1, Number.isFinite(Number(clip.duration_frames)) ? Math.round(Number(clip.duration_frames)) : 30);
        const rawOffset = id === 'narration' ? Number(scenes[clip.scene_index]?.from || 0) + Number(clip.source_start_frame || 0) : Number(clip.source_start_frame || 0);
        const startFromFrames = Math.max(0, Number.isFinite(rawOffset) ? Math.round(rawOffset) : 0);
        return <Sequence key={`${id}-${index}`} from={clipFrom} durationInFrames={clipDuration}><Audio src={clipSrc} startFrom={startFromFrames} volume={volume} onError={onTrackError} /></Sequence>;
      });
    })}
  </AbsoluteFill>;
}

function buildRenderableScenes(manifest = {}, fps = 30) {
  const rawScenes = Array.isArray(manifest.scenes) ? manifest.scenes : [];
  const filmFrames = Math.max(1, Number(manifest.duration_in_frames || Math.round(Number(manifest.duration || 1) * fps)));
  if (!rawScenes.length) return [{
    id: 'missing-scene-0', scene_index: 0, from: 0, start: 0,
    duration_in_frames: filmFrames,
    duration: filmFrames / fps,
    scene_missing: true, visual_intent: 'Scene visual recovery',
    narration: timedRecoveryNarration(manifest, 0, filmFrames, fps) || (typeof manifest.script === 'string' ? manifest.script : ''),
    primary_visual: { type: 'SmartImage', props: {} }, media: { type: 'image', src: '' },
    text: { frame_mode: 'media_only', overlay_policy: 'none' },
  }];

  const entries = [];
  for (let index = 0; index < rawScenes.length; index += 1) {
    const scene = rawScenes[index];
    if (scene && typeof scene === 'object') {
      entries.push({ ...scene, scene_index: Number.isFinite(Number(scene.scene_index)) ? Number(scene.scene_index) : index });
      continue;
    }
    // Collapse consecutive missing entries into one recovery scene covering
    // exactly the interval between the surrounding authored scenes.
    let nextIndex = index + 1;
    while (nextIndex < rawScenes.length && (!rawScenes[nextIndex] || typeof rawScenes[nextIndex] !== 'object')) nextIndex += 1;
    const previous = entries[entries.length - 1];
    const priorEnd = previous
      ? Number(previous.from ?? Math.round(Number(previous.start || 0) * fps)) + Number(previous.duration_in_frames || Math.round(Number(previous.duration || 1) * fps))
      : 0;
    const next = rawScenes[nextIndex];
    const nextStart = next
      ? Number(next.from ?? Math.round(Number(next.start || 0) * fps))
      : filmFrames;
    const from = Math.max(0, priorEnd);
    const durationInFrames = Math.max(1, nextStart > from ? nextStart - from : (next ? 1 : Math.max(1, filmFrames - from)));
    entries.push({
      id: `missing-scene-${index}`, scene_index: index, from, start: from / fps,
      duration_in_frames: durationInFrames, duration: durationInFrames / fps,
      scene_missing: true, visual_intent: 'Scene visual recovery',
      narration: timedRecoveryNarration(manifest, from, durationInFrames, fps)
        || (Array.isArray(manifest.scene_texts) ? manifest.scene_texts.slice(index, nextIndex).filter(text => typeof text === 'string').join(' ') : ''),
      recovery_context: [previous?.narration, previous?.visual_intent, next?.narration, next?.visual_intent].filter(Boolean).join(' ').slice(0, 240),
      topic: previous?.topic || next?.topic,
      category: previous?.category || next?.category,
      primary_visual: { type: 'SmartImage', props: {} }, media: { type: 'image', src: '' },
      text: { frame_mode: 'media_only', overlay_policy: 'none' },
    });
    index = nextIndex - 1;
  }

  const scheduled = entries;
  const withGapRecovery = [];
  for (const scene of scheduled) {
    const sceneStart = Math.max(0, Number(scene.from ?? Math.round(Number(scene.start || 0) * fps)));
    const prior = withGapRecovery[withGapRecovery.length - 1];
    const priorEnd = prior
      ? Number(prior.from ?? Math.round(Number(prior.start || 0) * fps)) + Number(prior.duration_in_frames || Math.round(Number(prior.duration || 1) * fps))
      : 0;
    if (sceneStart > priorEnd) {
      const gap = sceneStart - priorEnd;
      withGapRecovery.push({
        id: `missing-scene-gap-${withGapRecovery.length}`, scene_index: `gap-${withGapRecovery.length}`,
        from: priorEnd, start: priorEnd / fps, duration_in_frames: gap, duration: gap / fps,
        scene_missing: true, visual_intent: 'Scene visual recovery',
        narration: timedRecoveryNarration(manifest, priorEnd, gap, fps),
        recovery_context: [prior?.narration, prior?.visual_intent, scene?.narration, scene?.visual_intent].filter(Boolean).join(' ').slice(0, 240),
        topic: prior?.topic || scene?.topic,
        category: prior?.category || scene?.category,
        primary_visual: { type: 'SmartImage', props: {} }, media: { type: 'image', src: '' },
        text: { frame_mode: 'media_only', overlay_policy: 'none' },
      });
    }
    withGapRecovery.push(scene);
  }
  const filmEnd = Number(manifest.duration_in_frames || Math.round(Number(manifest.duration || 0) * fps));
  const last = withGapRecovery[withGapRecovery.length - 1];
  const lastEnd = last
    ? Number(last.from ?? Math.round(Number(last.start || 0) * fps)) + Number(last.duration_in_frames || Math.round(Number(last.duration || 1) * fps))
    : 0;
  if (filmEnd > lastEnd) withGapRecovery.push({
    id: 'missing-scene-tail', scene_index: 'tail', from: lastEnd, start: lastEnd / fps,
    duration_in_frames: filmEnd - lastEnd, duration: (filmEnd - lastEnd) / fps,
    scene_missing: true, visual_intent: 'Scene visual recovery',
    narration: timedRecoveryNarration(manifest, lastEnd, filmEnd - lastEnd, fps),
    recovery_context: [last?.narration, last?.visual_intent].filter(Boolean).join(' ').slice(0, 240),
    topic: last?.topic,
    category: last?.category,
    primary_visual: { type: 'SmartImage', props: {} }, media: { type: 'image', src: '' },
    text: { frame_mode: 'media_only', overlay_policy: 'none' },
  });
  return withGapRecovery;
}

export function getCompositionConfig(manifest = {}) {
  return { id: 'VidzeoDocumentary', component: DocumentaryComposition, durationInFrames: Math.max(1, Number(manifest.duration_in_frames || Math.round(Number(manifest.duration || 1) * Number(manifest.fps || 30)))), fps: Number(manifest.fps || 30), width: Number(manifest.width || 1920), height: Number(manifest.height || 1080) };
}

export {
  resolveSfxRelativePath,
  resolveSfxPath,
  resolveSfxSource,
  resolveMusicRelativePath,
  resolveMusicPath,
  resolveStaticAsset,
  SFX_REGISTRY_MAP,
  MUSIC_REGISTRY_MAP,
  KNOWN_SFX_FILES,
  KNOWN_MUSIC_FILES
};


