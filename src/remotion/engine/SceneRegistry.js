import React from 'react';
import {
  SmartImage,
  SmartVideo,
  KineticText,
  StatCard,
  QuoteCard,
  Headline,
  DocumentHighlight,
  AnimatedMap,
  Timeline,
  Chart,
  Comparison,
  Collage,
  PersonProfile,
  Diagram,
  ChapterTitle,
  TextCard,
  WordHighlighter,
  DataCard,
  CinematicHighlight,
  DeclassifiedDocument,
  IsometricMap,
  EvidencePinboard,
  TechnicalBlueprint,
  DepthTypography,
} from '../components';
import { sanitizeDocumentaryProps } from './TextSanitizer';
import { resolveMediaKind } from './MediaKind';
import { EditorialSceneCanvas } from '../components/EditorialSceneCanvas';
import { MediaOverlayCanvas } from '../components/MediaOverlayCanvas';
import { AdaptiveChart } from '../components/AdaptiveChart';
import { AdaptiveTimeline } from '../components/AdaptiveTimeline';
import { AdaptiveComparison } from '../components/AdaptiveComparison';
import { AdaptiveStat } from '../components/AdaptiveStat';
import { AdaptiveMetricGrid } from '../components/AdaptiveMetricGrid';
import { ExplanatoryDiagram } from '../components/ExplanatoryDiagram';
import { hasApprovedDirectorCopy } from './DirectorMotion';
import { GraphicTemplate } from '../templates';
import { mergeTheme } from '../theme';

const REGISTRY = {
  SmartImage,
  SmartVideo,
  KineticText,
  StatCard,
  QuoteCard,
  Headline,
  DocumentHighlight,
  AnimatedMap,
  Timeline,
  Chart,
  Comparison,
  Collage,
  PersonProfile,
  Diagram,
  ChapterTitle,
  TextCard,
  WordHighlighter,
  DataCard,
  CinematicHighlight,
  DeclassifiedDocument,
  IsometricMap,
  EvidencePinboard,
  TechnicalBlueprint,
  DepthTypography,
};

// Aliases for backward-compatibility with legacy manifests
const ALIASES = {
  'declassified-document': 'DeclassifiedDocument',
  'declassified-dossier': 'DeclassifiedDocument',
  'redaction-reveal': 'DeclassifiedDocument',
  'isometric-map': 'IsometricMap',
  'flight-tracker': 'IsometricMap',
  'evidence-pinboard': 'EvidencePinboard',
  'evidence-board': 'EvidencePinboard',
  'photo-pinboard': 'EvidencePinboard',
  'technical-blueprint': 'TechnicalBlueprint',
  'blueprint-reveal': 'TechnicalBlueprint',
  'cad-blueprint': 'TechnicalBlueprint',
  'depth-typography': 'DepthTypography',
  'behind-subject-text': 'DepthTypography',
  'data-card': 'DataCard',
  'fact-card': 'DataCard',
  'cinematic-highlight': 'CinematicHighlight',
  'cinematic-card': 'CinematicHighlight',
  'lower-third-card': 'PersonProfile',
  'kinetic-type': 'KineticText',
  'word-highlighter': 'WordHighlighter',
  'word-highlight': 'WordHighlighter',
  'keyword-highlight': 'WordHighlighter',
  'fact-callout': 'WordHighlighter',
  'text-card': 'TextCard',
  'card-overlay': 'TextCard',
  'vox-highlighter': 'TextCard',
  'cyber-hud': 'TextCard',
  'investigative-memo': 'TextCard',
  'broadsheet-callout': 'TextCard',
  'frosted-glass': 'TextCard',
  'brutalist-punch': 'TextCard',
  'field-caption': 'TextCard',
  'chapter-opener': 'ChapterTitle',
  'cold-open': 'ChapterTitle',
  'keyword-reveal': 'KineticText',
  'kinetic-statement': 'KineticText',
  'kinetic-text': 'KineticText',
  'headline-center': 'KineticText',
  'date-card': 'Timeline',
  'date-reveal': 'Timeline',
  'date-numeral': 'Timeline',
  'timeline-trace': 'Timeline',
  'timeline-strip': 'Timeline',
  'profile-dossier': 'PersonProfile',
  'person-dossier': 'PersonProfile',
  'profile-card': 'PersonProfile',
  'presenter-card': 'PersonProfile',
  'quote-card': 'QuoteCard',
  'witness-quote': 'QuoteCard',
  'stat-counter': 'StatCard',
  'money-counter': 'StatCard',
  'metric-grid': 'StatCard',
  'bar-chart': 'Chart',
  'line-chart': 'Chart',
  'donut-chart': 'Chart',
  'area-chart': 'Chart',
  'ranked-bars': 'Chart',
  'histogram': 'Chart',
  'distribution-chart': 'Chart',
  'map-route': 'AnimatedMap',
  'map-card': 'AnimatedMap',
  'route-progress': 'AnimatedMap',
  'split-screen': 'Comparison',
  'comparison-split': 'Comparison',
  'cause-effect': 'Diagram',
  'cause-effect-flow': 'Diagram',
  'decision-tree': 'Diagram',
  'gallery-stack': 'Collage',
  'paper-explainer': 'DocumentHighlight',
  'source-highlight': 'DocumentHighlight',
  'newsprint-reveal': 'Headline',
  'full-bleed': 'SmartImage',
  'image': 'SmartImage',
  'video': 'SmartVideo',
};

export function resolveSceneComponent(typeOrId) {
  if (!typeOrId) return SmartImage;
  if (REGISTRY[typeOrId]) return REGISTRY[typeOrId];
  const normalized = String(typeOrId).trim().toLowerCase().replace(/_/g, '-');
  const mapped = ALIASES[normalized];
  if (mapped && REGISTRY[mapped]) return REGISTRY[mapped];
  return SmartImage;
}

export function renderSceneSystem(scene = {}, visualDNA = {}, options = {}) {
  const primary = scene.primary_visual || {};
  const graphic = scene.graphic || {};
  const media = scene.media || {};
  const primaryType = primary.type || (media.type === 'video' ? 'SmartVideo' : 'SmartImage');
  // ── Media/type mismatch guard ──────────────────────────────────────
  // Dispatch from the active file in both directions. Recovery can replace
  // a video with a JPEG while retaining old scene/shot type labels.
  const mediaSrc = String(media.src || media.path || primary.asset?.src || primary.asset?.path || '');
  const correctedPrimaryType = ['SmartImage', 'SmartVideo', 'BrollImage', 'BrollVideo', 'image', 'video'].includes(primaryType)
    ? (resolveMediaKind({ ...(primary.asset || {}), ...media }, mediaSrc) === 'video' ? 'SmartVideo' : 'SmartImage')
    : primaryType;
  // ──────────────────────────────────────────────────────────────────
  const primaryComponent = resolveSceneComponent(correctedPrimaryType);
  const selectedTemplate = String(graphic.template_id || '').trim();
  // MetricGrid is a real catalog layout with its own multi-value renderer;
  // do not collapse it into the single-number AdaptiveStat component.
  if (selectedTemplate.toLowerCase().replace(/_/g, '-') === 'metric-grid' && scene.director_treatment_enabled === true) {
    const metricProps = sanitizeDocumentaryProps({ ...(primary.props || {}), ...(graphic.props || {}), variant: graphic.variant || primary.variant }, scene);
    return Array.isArray(metricProps.metrics || metricProps.items) && (metricProps.metrics || metricProps.items).length >= 2
      ? <AdaptiveMetricGrid scene={scene} visualDNA={visualDNA} props={metricProps} />
      : <AdaptiveStat scene={scene} visualDNA={visualDNA} props={metricProps} />;
  }
  const templateComponent = selectedTemplate && !['full-bleed', 'generated-recipe'].includes(selectedTemplate)
    ? resolveSceneComponent(selectedTemplate)
    : null;
  // The shared catalog's template choice reaches the Remotion renderer when
  // it is a valid treatment for the scene's semantic visual. A mismatch keeps
  // the grounded scene renderer rather than swapping in an unrelated card.
  const Component = templateComponent && templateComponent === primaryComponent
    ? templateComponent
    : primaryComponent;
  const primaryAssetSource = primary.asset?.src || primary.asset?.path || '';
  const replacedAsset = primaryAssetSource && mediaSrc && primaryAssetSource !== mediaSrc;
  const combinedAsset = ['SmartImage', 'SmartVideo'].includes(correctedPrimaryType) && mediaSrc
    ? { ...(replacedAsset ? {} : (primary.asset || {})), ...media, src: mediaSrc }
    : primary.asset || media;
  const combinedProps = {
    ...(graphic.props || {}),
    ...(primary.props || {}),
    variant: primary.variant || graphic.props?.variant,
  };

  const sanitizedProps = sanitizeDocumentaryProps(combinedProps, scene);
  const directed = scene.director_treatment_enabled === true
    || ['llm_director', 'llm_pixel_director', 'system_grounded_mapping'].includes(String(scene.visual_role_source || '').toLowerCase())
    || Object.prototype.hasOwnProperty.call(scene.text || {}, 'overlay_policy');
  const visualRole = String(scene.visual_role || '').trim().toLowerCase();
  const mediaHasSource = Boolean(media.src || media.path || media.url || media.asset_path
    || (Array.isArray(media.shots) && media.shots.length));
  // Media-first specialist graphics must retain the footage beneath them when
  // the director assigned an overlay role. Primary explanations and maps own
  // their canvas and keep their opaque, full-frame composition.
  const transparentSpecialistOverlay = mediaHasSource
    && visualRole !== 'primary_explanation'
    && visualRole !== 'map_canvas'
    && (visualRole === 'media_overlay' || options.mediaFirst === true);
  const approvedCopy = hasApprovedDirectorCopy(scene);
  const approvedCopyText = approvedCopy ? String(scene.editorial_contract.display_text).trim() : '';
  const approvedSpan = approvedCopy && Array.isArray(scene.editorial_contract.emphasis_spans)
    ? scene.editorial_contract.emphasis_spans.find((span) => Number.isInteger(span.start) && Number.isInteger(span.end) && span.start >= 0 && span.end > span.start && span.end <= approvedCopyText.length)
    : null;
  const specialistProps = directed
    ? {
      ...sanitizedProps,
      title: '', headline: '', text: '', display_text: '', kicker: '', subtitle: '', emphasis: '',
      ...(approvedCopy && Component === KineticText ? { text: approvedCopyText, display_text: approvedCopyText, emphasis: approvedSpan ? approvedCopyText.slice(approvedSpan.start, approvedSpan.end) : '', emphasis_spans: scene.editorial_contract.emphasis_spans || [] } : {}),
      ...(approvedCopy && Component === StatCard ? { display_text: approvedCopyText, emphasis_spans: scene.editorial_contract.emphasis_spans || [] } : {}),
      ...(approvedCopy && Component === ChapterTitle ? { title: approvedCopyText } : {}),
      ...(approvedCopy && Component === Headline ? { headline: approvedCopyText } : {}),
    }
    : sanitizedProps;

  // Maps are specialized geographic scenes, not decorative locator cards.
  // Route them to the local Natural Earth renderer before a generic media-first
  // editorial contract can intercept them.
  if (Component === AnimatedMap) {
    return (
      <AnimatedMap
        scene={scene}
        visualDNA={visualDNA}
        props={sanitizedProps}
        asset={combinedAsset}
      />
    );
  }

  // A grounded primary graphic owns its canvas. The shared editorial canvas
  // is reserved for legacy scenes and genuinely non-specialist treatments.
  if (directed && Component === Chart) {
    return <AdaptiveChart scene={scene} visualDNA={visualDNA} props={specialistProps} />;
  }
  if (directed && Component === StatCard) {
    return <AdaptiveStat scene={scene} visualDNA={visualDNA} props={specialistProps} />;
  }
  if (directed && Component === Timeline) {
    return <AdaptiveTimeline scene={scene} visualDNA={visualDNA} props={specialistProps} />;
  }
  if (directed && Component === Comparison) {
    return <AdaptiveComparison scene={scene} visualDNA={visualDNA} props={{ ...specialistProps, ...(transparentSpecialistOverlay ? { transparent_canvas: true } : {}) }} />;
  }
  if (directed && Component === Diagram) {
    return <ExplanatoryDiagram scene={scene} visualDNA={visualDNA} props={{ ...sanitizedProps, ...(transparentSpecialistOverlay ? { transparent_canvas: true } : {}) }} />;
  }
  if (directed && Component === DocumentHighlight) {
    return <Component scene={scene} visualDNA={visualDNA} props={sanitizedProps} asset={combinedAsset} />;
  }
  if (directed && [KineticText, ChapterTitle, Headline].includes(Component) && !approvedCopy) {
    return null;
  }
  if (directed && [KineticText, ChapterTitle, Headline].includes(Component)) {
    return <Component scene={scene} visualDNA={visualDNA} props={specialistProps} asset={combinedAsset} />;
  }
  if (directed && [QuoteCard, PersonProfile, Collage].includes(Component)) {
    return <Component scene={scene} visualDNA={visualDNA} props={sanitizedProps} asset={combinedAsset} />;
  }

  const contract = scene.editorial_contract || primary.props?.editorial_contract;

  const hasEditorialContract = Boolean(
    contract &&
    contract.enabled !== false &&
    contract.family &&
    contract.family !== 'media_hold'
  );

  if (hasEditorialContract && !directed) {
    return <MediaOverlayCanvas scene={scene} visualDNA={visualDNA} props={sanitizedProps} />;
  }

  // The advanced documentary system is the default for semantic scenes. It
  // composes a visual grammar over the footage instead of routing every beat
  // through a centered card. Smart media remains on its dedicated renderer.
  const advanced = visualDNA?.render_mode !== 'legacy';
  const mediaOnly = Component === SmartImage || Component === SmartVideo;
  const mediaFirst = Boolean(options.mediaFirst && !mediaOnly);
  if (mediaFirst) {
    // Keep the LLM-selected primary visual in control. Legacy asset-planner
    // metadata such as `overlay_grammar: none` must not hide directed scenes.
    // Specialized graphics validate their own data; invalid maps remain blank.
    return <EditorialSceneCanvas scene={scene} visualDNA={visualDNA} props={sanitizedProps} />;
  }
  if (advanced && !mediaOnly) {
    return <EditorialSceneCanvas scene={scene} visualDNA={visualDNA} props={sanitizedProps} />;
  }

  return (
    <Component
      scene={scene}
      visualDNA={visualDNA}
      props={sanitizedProps}
      asset={combinedAsset}
    />
  );
}
