import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { hasApprovedDirectorCopy } from '../engine/DirectorMotion';
import { EditorialType } from './EditorialType';
import { mergeTheme, rgba } from '../theme';
import { recoveryContent, recoveryVisualGrammar, recoveryVisualKind } from '../engine/RecoveryContent';
import { RecoveryIllustration } from './RecoveryIllustration';
import { DeclassifiedDocument } from './DeclassifiedDocument';
import { EvidencePinboard } from './EvidencePinboard';
import { TechnicalBlueprint } from './TechnicalBlueprint';
import { AnimatedMap } from './AnimatedMap';
import { AdaptiveChart } from './AdaptiveChart';
import { Timeline } from './Timeline';
import { Headline } from './Headline';
import { PersonProfile } from './PersonProfile';
import { Diagram } from './Diagram';

/**
 * Procedural Documentary Motion-Graphics Canvas.
 * Ensures zero blank or black frames when footage is missing or in recovery.
 * Fully styled according to active Visual DNA (palette, geometry, typography).
 * Reserves lower 38% of viewport strictly for dynamic animated subtitles.
 */
export function MissingSceneMotionGraphic({
  scene = {},
  visualDNA = {},
  theme: themeInput,
  backgroundOnly = false,
  copyOwnedElsewhere = false,
}) {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const theme = themeInput || mergeTheme(visualDNA);
  const palette = visualDNA.palette || theme.palette || {};
  const genre = String(visualDNA.genre || '').toLowerCase();
  const niche = String(visualDNA.niche || palette.niche || scene.category || '').toLowerCase();

  // Color tokens
  const background = palette.background || '#0A0C10';
  const surface = palette.surface || '#121824';
  const accent = palette.accent || '#38BDF8';
  const primaryText = palette.primary || '#FFFFFF';
  const secondaryText = palette.secondary || '#94A3B8';
  const border = palette.border || accent;

  // Geometry & Font tokens
  const fontFamily = visualDNA.typography?.display || theme.font_family || 'Montserrat, sans-serif';
  const monoFont = visualDNA.typography?.numbers || 'JetBrains Mono, monospace';

  // Smooth entrance springs
  const entrance = spring({ frame, fps, config: { damping: 14, stiffness: 90 } });
  const pulse = 0.5 + 0.5 * Math.sin((frame / fps) * 2.2);

  // Subtitle protection zone: Keep lower 38% free of intrusive elements
  const safeZoneHeight = height * 0.62;

  // Archival / Documentary Header Badge Tag
  const headerTag = React.useMemo(() => {
    if (genre.includes('archival') || niche.includes('car') || niche.includes('wheel')) {
      return 'ARCHIVAL DOSSIER // REF-HIST';
    }
    if (genre.includes('blueprint') || niche.includes('train') || niche.includes('rail')) {
      return 'TECHNICAL SCHEMATIC // CAD-SPEC';
    }
    if (genre.includes('royal') || niche.includes('royal') || niche.includes('crown')) {
      return 'ROYAL HERITAGE // ARCHIVE-DOC';
    }
    if (genre.includes('forensic') || niche.includes('death') || genre.includes('crime')) {
      return 'FORENSIC INQUIRY // CASE-FILE';
    }
    if (genre.includes('broadsheet') || niche.includes('brit')) {
      return 'HISTORICAL RECORD // PRESS-DISPATCH';
    }
    if (genre.includes('cosmic') || niche.includes('space')) {
      return 'STELLAR TELEMETRY // ASTRO-OBS';
    }
    if (genre.includes('military') || niche.includes('war')) {
      return 'TACTICAL RECORD // MISSION-DATA';
    }
    if (genre.includes('nature') || niche.includes('wild')) {
      return 'ECOLOGICAL SURVEY // FIELD-LOG';
    }
    return 'DOCUMENTARY RECORD // SYSTEM-REC';
  }, [genre, niche]);

  // Editorial Copy & Recovery Beats
  const copy = scene.editorial_contract || {};
  const from = Math.max(0, Number(scene.copy_window?.start_frame || 0));
  const duration = Math.min(Math.round(2.4 * fps), Number(scene.copy_window?.duration_frames || 2.2 * fps));
  const beats = recoveryContent(scene);
  const showCopy = !backgroundOnly && beats.length === 0 && hasApprovedDirectorCopy(scene) && frame >= from && frame < from + duration;
  const sceneFrames = Math.max(1, Number(scene.duration_in_frames || Number(scene.duration || 4) * fps));
  const beatIndex = Math.min(beats.length - 1, Math.floor((Math.max(0, frame) / sceneFrames) * beats.length));
  const beatStart = (Math.max(0, beatIndex) * sceneFrames) / Math.max(1, beats.length);
  const reveal = Math.min(1, Math.max(0, (frame - beatStart + 1) / Math.max(1, fps * 0.25)));
  const showStatement = !backgroundOnly && !showCopy && beats.length > 0;

  const spec = scene.media?.graphic_fallback || scene.graphic_fallback || {};
  const entity = String(spec.entity || scene.visual_requirement?.canonical_entity || visualDNA.topic || '').trim();
  const kind = recoveryVisualKind(scene, niche);
  const grammar = recoveryVisualGrammar(scene, niche);
  const graphicProps = {
    ...(scene.primary_visual?.props || {}),
    ...(scene.graphic?.props || {}),
    ...(spec.props || {}),
  };
  const mapProps = {
    ...(scene.map_shot || {}),
    ...graphicProps,
    map_shot: scene.map_shot || graphicProps.map_shot,
  };
  const landscape = width / height > 1.25;
  const illustrated = Boolean(kind && landscape);
  const statement = String(beats[beatIndex] || '');
  const tokens = statement.split(/\s+/);
  const emphasizedFrom = Math.max(0, tokens.length - Math.max(1, Math.min(3, Math.ceil(tokens.length * 0.25))));

  // Dynamic light telemetry coordinates
  const lightX = 35 + 10 * Math.sin((frame / fps) * 0.8);
  const lightY = 38 + 6 * Math.cos((frame / fps) * 0.6);

  // Timecode / Frame tracker
  const timeSeconds = Math.floor(frame / fps);
  const timeFrames = frame % fps;
  const sceneNum = String(scene.scene_index ?? '01').padStart(2, '0');
  const timecode = `REC \u25CF 00:00:${String(timeSeconds).padStart(2, '0')}:${String(timeFrames).padStart(2, '0')} | SCN-${sceneNum}`;

  if (!backgroundOnly) {
    const isMap = grammar === 'map';
    const isBlueprint = grammar === 'blueprint';
    const isPinboard = grammar === 'evidence';

    if (isMap) {
      return (
        <AbsoluteFill data-recovery-canvas="true">
          <DeclassifiedDocument
            scene={scene}
            visualDNA={visualDNA}
            title={statement || entity}
            stampText="RECONSTRUCTION"
            classification="EDITORIAL RECONSTRUCTION"
            reconstructionMode
          />
          <AnimatedMap scene={scene} visualDNA={visualDNA} props={mapProps} />
        </AbsoluteFill>
      );
    }
    if (isBlueprint) {
      return (
        <AbsoluteFill data-recovery-canvas="true">
          <TechnicalBlueprint scene={scene} visualDNA={visualDNA} title={statement || entity} reconstructionMode />
        </AbsoluteFill>
      );
    }
    if (isPinboard) {
      return (
        <AbsoluteFill data-recovery-canvas="true">
          <EvidencePinboard scene={scene} visualDNA={visualDNA} title={statement || entity} reconstructionMode />
        </AbsoluteFill>
      );
    }
    if (grammar === 'chart') {
      return <AbsoluteFill data-recovery-canvas="true"><AdaptiveChart scene={scene} visualDNA={visualDNA} props={graphicProps} /></AbsoluteFill>;
    }
    if (grammar === 'timeline') {
      return <AbsoluteFill data-recovery-canvas="true"><Timeline scene={scene} visualDNA={visualDNA} props={graphicProps} /></AbsoluteFill>;
    }
    if (grammar === 'diagram') {
      return <AbsoluteFill data-recovery-canvas="true"><Diagram scene={scene} visualDNA={visualDNA} props={graphicProps} /></AbsoluteFill>;
    }
    if (grammar === 'profile' && entity) {
      return <AbsoluteFill data-recovery-canvas="true"><PersonProfile scene={scene} visualDNA={visualDNA} props={{ ...graphicProps, name: entity, role: '' }} /></AbsoluteFill>;
    }
    if (grammar === 'headline') {
      const headline = statement || entity || String(scene.text?.headline || '').trim();
      if (headline) {
        return <AbsoluteFill data-recovery-canvas="true"><Headline scene={scene} visualDNA={visualDNA} props={{
          ...graphicProps,
          headline,
          publication: 'EDITORIAL RECONSTRUCTION',
          subdeck: 'Illustrative graphic based on this scene’s narration',
          variant: 'digital_article_crop',
        }} /></AbsoluteFill>;
      }
    }
    return (
      <AbsoluteFill data-recovery-canvas="true">
        <DeclassifiedDocument
          scene={scene}
          visualDNA={visualDNA}
          title={statement || entity}
          stampText="RECONSTRUCTION"
          classification="EDITORIAL RECONSTRUCTION"
          reconstructionMode
        />
      </AbsoluteFill>
    );
  }

  return (
    <AbsoluteFill data-recovery-canvas="true" style={{ backgroundColor: background, overflow: 'hidden' }}>
      {/* 1. Ambient Spotlight & Atmospheric Glow */}
      <AbsoluteFill
        style={{
          background: `radial-gradient(ellipse at ${lightX}% ${lightY}%, ${rgba(accent, 0.16 + 0.05 * pulse)}, transparent 68%)`,
        }}
      />

      {/* 2. Atmospheric Documentary Vignette Underlay */}
      <AbsoluteFill
        style={{
          opacity: 0.15,
          background: 'radial-gradient(circle at center, transparent 40%, rgba(0,0,0,0.6) 100%)',
        }}
      />

      {/* 3. Tactical HUD Viewfinder Brackets (Constrained to upper 62% safe zone) */}
      <div
        style={{
          position: 'absolute',
          top: '7%',
          left: '6%',
          right: '6%',
          height: safeZoneHeight * 0.88,
          pointerEvents: 'none',
          opacity: entrance * 0.85,
        }}
      >
        {/* Top-Left Bracket */}
        <div style={{ position: 'absolute', top: 0, left: 0, width: 36, height: 36, borderTop: `2px solid ${accent}`, borderLeft: `2px solid ${accent}` }} />
        {/* Top-Right Bracket */}
        <div style={{ position: 'absolute', top: 0, right: 0, width: 36, height: 36, borderTop: `2px solid ${accent}`, borderRight: `2px solid ${accent}` }} />
        {/* Bottom-Left Bracket */}
        <div style={{ position: 'absolute', bottom: 0, left: 0, width: 36, height: 36, borderBottom: `2px solid ${accent}`, borderLeft: `2px solid ${accent}` }} />
        {/* Bottom-Right Bracket */}
        <div style={{ position: 'absolute', bottom: 0, right: 0, width: 36, height: 36, borderBottom: `2px solid ${accent}`, borderRight: `2px solid ${accent}` }} />
      </div>

      {/* 4. Top Telemetry Header & Dossier Tag */}
      <div
        style={{
          position: 'absolute',
          top: '8.5%',
          left: '7.5%',
          right: '7.5%',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          pointerEvents: 'none',
          opacity: entrance * 0.9,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 8, height: 8, borderRadius: '50%', backgroundColor: accent, boxShadow: `0 0 10px ${accent}` }} />
          <span style={{ fontFamily: monoFont, fontSize: Math.max(11, width * 0.009), fontWeight: 700, letterSpacing: '0.12em', color: primaryText }}>
            {headerTag}
          </span>
        </div>
        <div style={{ fontFamily: monoFont, fontSize: Math.max(10, width * 0.008), letterSpacing: '0.08em', color: secondaryText, opacity: 0.75 }}>
          {timecode}
        </div>
      </div>

      {/* 5. Central Hero / Entity / Recovery Beats (Upper 62% Viewport) */}
      {showCopy && !copyOwnedElsewhere && (
        <EditorialType
          scene={scene}
          visualDNA={visualDNA}
          text={copy.display_text}
          spans={copy.emphasis_spans || []}
          style="marker_type"
          position={scene.text?.position || 'center'}
          accent={accent}
          ink={primaryText}
        />
      )}

      {showStatement && (
        <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'flex-start', padding: '12% 8% 38%' }}>
          {entity && (
            <div
              data-recovery-entity="true"
              style={{
                position: 'absolute',
                left: '8%',
                top: '16%',
                maxWidth: '80%',
                color: accent,
                fontFamily,
                fontSize: Math.min(width * 0.024, height * 0.045),
                fontWeight: 800,
                letterSpacing: '0.04em',
                lineHeight: 1.15,
                textTransform: 'uppercase',
                opacity: 0.9,
              }}
            >
              {entity}
            </div>
          )}

          {illustrated && (
            <div
              style={{
                position: 'absolute',
                right: '6%',
                top: '20%',
                width: '32%',
                height: '38%',
                transform: `translateX(${(1 - reveal) * width * 0.014}px)`,
                opacity: 0.4 + 0.6 * reveal,
              }}
            >
              <RecoveryIllustration kind={kind} accent={accent} ink={primaryText} progress={reveal} />
            </div>
          )}

          <div
            data-grounded-recovery="true"
            style={{
              width: width * (illustrated ? 0.54 : 0.8),
              marginTop: entity ? '4%' : '0',
              color: primaryText,
              fontFamily,
              fontWeight: 800,
              fontSize: Math.min(width * 0.038, height * 0.076),
              lineHeight: 1.25,
              overflowWrap: 'anywhere',
              textShadow: '0 4px 18px rgba(0,0,0,0.7)',
              opacity: 0.25 + 0.75 * reveal,
              transform: `translateY(${(1 - reveal) * height * 0.012}px)`,
            }}
          >
            {tokens.slice(0, emphasizedFrom).join(' ')}{' '}
            <span
              style={{
                background: accent,
                color: '#0A0C10',
                padding: '0.05em 0.25em',
                borderRadius: 2,
                boxDecorationBreak: 'clone',
                WebkitBoxDecorationBreak: 'clone',
              }}
            >
              {tokens.slice(emphasizedFrom).join(' ')}
            </span>
          </div>

          {illustrated && (
            <div
              style={{
                position: 'absolute',
                right: '7%',
                top: '56%',
                fontFamily: monoFont,
                fontSize: Math.max(10, width * 0.009),
                letterSpacing: '0.08em',
                color: secondaryText,
                opacity: 0.7,
              }}
            >
              SCHEMATIC ILLUSTRATION
            </div>
          )}
        </AbsoluteFill>
      )}

      {/* 6. Ambient Minimal State when no copy or statements exist (Always active and rich, never blank) */}
      {!showStatement && !showCopy && (
        <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', paddingBottom: '20%' }}>
          {entity && (
            <div
              style={{
                fontFamily,
                fontSize: Math.min(width * 0.042, height * 0.08),
                fontWeight: 900,
                letterSpacing: '0.03em',
                color: primaryText,
                textAlign: 'center',
                maxWidth: '80%',
                lineHeight: 1.2,
                textShadow: '0 4px 20px rgba(0,0,0,0.8)',
                opacity: entrance,
                transform: `scale(${0.96 + 0.04 * entrance})`,
              }}
            >
              <div
                style={{
                  display: 'inline-block',
                  borderBottom: `3px solid ${accent}`,
                  paddingBottom: 8,
                }}
              >
                {entity}
              </div>
            </div>
          )}
        </AbsoluteFill>
      )}

      {/* 7. Lower 38% Subtitle Safe Zone: Clean, dark gradient fade to guarantee subtitle contrast */}
      <div
        data-subtitle-safe-zone="true"
        style={{
          position: 'absolute',
          bottom: 0,
          left: 0,
          width: '100%',
          height: '38%',
          pointerEvents: 'none',
          background: 'linear-gradient(to bottom, transparent 0%, rgba(5, 7, 12, 0.45) 50%, rgba(5, 7, 12, 0.85) 100%)',
          zIndex: 5,
        }}
      />
    </AbsoluteFill>
  );
}
