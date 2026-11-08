import React, { useState } from 'react';
import { AbsoluteFill, Img as RemotionImg, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { isRecoveryMedia } from '../engine/RecoveryMedia';
import { resolveMediaKind } from '../engine/MediaKind';
import { hasApprovedDirectorCopy } from '../engine/DirectorMotion';
import { calculateCameraTransform, computeSpringEnter, computeMediaEntranceStyle } from '../engine/MotionEngine';
import { rgba } from '../theme';
import { focalObjectPosition, resolveFocalBox, resolveMediaFit } from '../engine/FocalFrame';
import { SmartVideo } from './SmartVideo';
import { MediaShotSequence } from './MediaShotSequence';
import { resolveColorGrade } from '../engine/ColorGrade';
import { MissingSceneMotionGraphic } from './MissingSceneMotionGraphic';
import { Particles } from '../effects/index';

/**
 * SmartImage — Universal Documentary Cinematography System.
 * Supported variants:
 * - full_bleed_cinematic
 * - editorial_crop
 * - framed_print
 * - split_stage
 * - spotlight_reveal
 * - parallax_float
 * - archival_polaroid
 * - photo_stack
 */
export function SmartImage({ scene = {}, visualDNA = {}, props = {}, asset = {} }) {
  const media = scene?.media && typeof scene.media === 'object' ? scene.media : {};
  return (
    <MediaShotSequence scene={scene} media={media}>
      {({ scene: shotScene, media: shotMedia }) => (
        <SmartImageFrame
          scene={shotScene}
          visualDNA={visualDNA}
          props={props}
          asset={{
            ...asset,
            ...shotMedia,
            src: shotMedia?.src || shotMedia?.path || shotMedia?.asset_path || asset?.src || asset?.path || '',
          }}
        />
      )}
    </MediaShotSequence>
  );
}

function SmartImageFrame({ scene = {}, visualDNA = {}, props = {}, asset = {} }) {
  const [mediaFailed, setMediaFailed] = useState(false);
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  // ── Video safety guard ─────────────────────────────────────────────
  // If the source is a video file (e.g. a YouTube .mp4 clip that was
  // incorrectly classified as SmartImage in the edit plan), delegate to
  // SmartVideo so Remotion uses its video decoder instead of an image tag.
  const guardSrc = asset?.src || asset?.path || props?.src || scene?.media?.src || scene?.media?.path || '';
  const isActuallyVideo = resolveMediaKind({ ...(scene.media || {}), ...asset }, guardSrc) === 'video';
  if (isActuallyVideo && guardSrc) {
    const delegateAsset = { ...(scene?.media || {}), ...(asset || {}), src: guardSrc };
    return <SmartVideo scene={scene} visualDNA={visualDNA} props={props} asset={delegateAsset} />;
  }
  // ──────────────────────────────────────────────────────────────────

  const sceneDuration = Math.max(1, scene?.duration_in_frames !== undefined && scene?.duration_in_frames !== null
    ? Math.round(Number(scene.duration_in_frames))
    : Math.round(Number(scene?.duration || 4) * fps));
  const progress = Math.min(1, Math.max(0, frame / Math.max(1, sceneDuration - 1)));

  const src = asset?.src || asset?.path || props?.src || scene?.media?.src || scene?.media?.path || '';
  const GuardedImg = (imageProps) => <RemotionImg {...imageProps} onImageError={(error) => {
    imageProps.onImageError?.(error);
    setMediaFailed(true);
  }} />;
  if (mediaFailed || isRecoveryMedia(asset, src) || isRecoveryMedia(scene.media, src)) return <MissingSceneMotionGraphic scene={scene} visualDNA={visualDNA} />;
  if (!src) return <MissingSceneMotionGraphic scene={scene} visualDNA={visualDNA} />;
  const directed = scene.director_treatment_enabled === true || Object.prototype.hasOwnProperty.call(scene.text || {}, 'overlay_policy');
  const copyFrom = Math.max(0, Number(scene.copy_window?.start_frame || 0));
  const copyDuration = Math.min(2.4 * fps, Number(scene.copy_window?.duration_frames || 2.2 * fps));
  const approvedCaption = hasApprovedDirectorCopy(scene) && frame >= copyFrom && frame < copyFrom + copyDuration
    ? scene.editorial_contract.display_text : '';
  const cutoutSrc = props?.cutout_src || asset?.cutout_src || scene?.media?.cutout_src || scene?.primary_visual?.props?.cutout_src || '';
  const hasCutout = Boolean(cutoutSrc && cutoutSrc !== src);
  const requestedVariant = String(props?.variant || scene?.primary_visual?.variant || 'full_bleed_cinematic').toLowerCase();
  // A text-free scene can still have a director-selected photographic layout.
  const variant = requestedVariant;
  const focal = resolveFocalBox(scene, asset, props);
  const imageFit = resolveMediaFit(asset, props, false);
  const imagePosition = focalObjectPosition(focal, asset?.position || props?.position || 'center');
  // The active shot already includes the scene's asset metadata. Consulting
  // another shot can undo a deliberate crop or use the wrong aspect ratio.
  const isContained = imageFit === 'contain';
  const effectiveFit = isContained ? 'contain' : imageFit;
  const imageStyle = (extra = {}) => ({
    width: '100%',
    height: '100%',
    objectFit: effectiveFit,
    objectPosition: imagePosition,
    ...extra,
  });
  const camera = scene?.motion?.camera || props?.camera || 'slow_push';
  const transform = calculateCameraTransform(camera, progress);
  const enter = computeSpringEnter(frame, fps, 0, scene?.motion?.spring_stiffness || 120, scene?.motion?.spring_damping || 18);
  const entranceStyle = computeMediaEntranceStyle(scene?.motion?.entrance || 'none', enter);

  const palette = visualDNA.palette || {};
  const cornerRadius = visualDNA.cornerRadius ?? 4;

  // Professional documentary color grading with a scene-level exposure
  // control for deliberately dark stills that need readable shadow detail.
  const requestedBrightness = Number(props?.image_brightness ?? props?.brightness);
  const imageBrightness = Number.isFinite(requestedBrightness)
    ? Math.min(3.2, Math.max(0.7, requestedBrightness))
    : visualDNA.image_treatment === 'warm_archival' || visualDNA.image_treatment === 'desaturated_contrast' ? 1.01 : 1.02;
  const imageFilter = resolveColorGrade(scene, visualDNA, { brightness: Number.isFinite(requestedBrightness) ? imageBrightness : undefined }).filter;

  // Variant 1: Full Bleed Cinematic (or Contained for square / portrait)
  // Each advertised photographic treatment owns its distinct layout.
  if (
    !hasCutout && (
      variant === 'full_bleed_cinematic'
      || variant === 'default'
    )
  ) {
    if (isContained) {
      return (
        <AbsoluteFill style={{ overflow: 'hidden', ...entranceStyle, background: palette.background || '#0a0d12' }}>
          {/* Ambient blurred backdrop fills the 16:9 canvas so there are zero dead black bars */}
          <GuardedImg
            src={src}
            style={{
              position: 'absolute',
              inset: '-10%',
              width: '120%',
              height: '120%',
              objectFit: 'cover',
              objectPosition: imagePosition,
              filter: 'brightness(0.22) contrast(1.18) saturate(0.7)',
              transform: 'scale(1.05)',
            }}
          />
          {/* Subtle dark vignette over blur */}
          <AbsoluteFill
            style={{
              background: 'radial-gradient(ellipse at center, transparent 40%, rgba(0,0,0,0.55) 100%)',
              pointerEvents: 'none',
            }}
          />
          {/* Main contained image: preserves full subject, head, text, and aspect ratio */}
          <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center' }}>
            <div
              style={{
                position: 'relative',
                width: '92%',
                height: '90%',
                display: 'flex',
                justifyContent: 'center',
                alignItems: 'center',
                transform,
              }}
            >
              <GuardedImg
                src={src}
                style={{
                  maxWidth: '100%',
                  maxHeight: '100%',
                  objectFit: 'contain',
                  objectPosition: imagePosition,
                  filter: imageFilter,
                  borderRadius: Math.max(6, cornerRadius),
                  boxShadow: '0 24px 60px rgba(0,0,0,0.75), 0 2px 10px rgba(0,0,0,0.5)',
                  border: `1px solid ${rgba(palette.border || '#ffffff', 0.12)}`,
                  display: 'block',
                }}
              />
            </div>
          </AbsoluteFill>
          {/* Bottom subtle shadow gradient */}
          <AbsoluteFill
            style={{
              background: `linear-gradient(180deg, transparent 75%, ${rgba(palette.background || '#000000', 0.45)} 100%)`,
              pointerEvents: 'none',
            }}
          />
        </AbsoluteFill>
      );
    }

    return (
      <AbsoluteFill style={{ overflow: 'hidden', ...entranceStyle }}>
        <GuardedImg
          src={src}
          style={imageStyle({ transform, filter: imageFilter })}
        />
        <AbsoluteFill
          style={{
            background: `linear-gradient(180deg, transparent 70%, ${rgba(palette.background, 0.3)} 100%)`,
            pointerEvents: 'none',
          }}
        />
      </AbsoluteFill>
    );
  }

  // Variant 2: Editorial Crop (matted with accent line over blurred backdrop)
  if (variant === 'editorial_crop') {
    return (
      <AbsoluteFill style={{ overflow: 'hidden', ...entranceStyle }}>
        <GuardedImg
          src={src}
          style={{
            position: 'absolute',
            inset: '-10%',
            width: '120%',
            height: '120%',
            objectFit: 'cover',
            filter: 'brightness(0.2) contrast(1.15) saturate(0.6)',
          }}
        />
        <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center' }}>
          <div
            style={{
              width: '84%',
              height: '84%',
              position: 'relative',
              borderRadius: cornerRadius,
              overflow: 'hidden',
              boxShadow: `0 24px 60px rgba(0,0,0,0.85), 0 0 0 ${visualDNA.strokeWidth || 2}px ${rgba(palette.accent, 0.5)}`,
              display: 'flex',
              justifyContent: 'center',
              alignItems: 'center',
            }}
          >
            <GuardedImg
              src={src}
              style={{
                maxWidth: '100%',
                maxHeight: '100%',
                width: isContained ? 'auto' : '100%',
                height: isContained ? 'auto' : '100%',
                objectFit: effectiveFit,
                objectPosition: imagePosition,
                transform,
                filter: imageFilter,
              }}
            />
          </div>
        </AbsoluteFill>
      </AbsoluteFill>
    );
  }

  // Variant 3: Framed Print (museum / archival passepartout over blurred backdrop)
  if (variant === 'framed_print') {
    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', overflow: 'hidden', ...entranceStyle }}>
        <GuardedImg
          src={src}
          style={{
            position: 'absolute',
            inset: '-10%',
            width: '120%',
            height: '120%',
            objectFit: 'cover',
            filter: 'brightness(0.18) contrast(1.15) saturate(0.55)',
          }}
        />
        <div
          style={{
            width: '82%',
            height: '84%',
            background: palette.surface,
            padding: '24px',
            border: `1px solid ${palette.border}`,
            boxShadow: '0 30px 70px rgba(0,0,0,0.85)',
            display: 'flex',
            flexDirection: 'column',
            zIndex: 2,
          }}
        >
          <div style={{ flex: 1, overflow: 'hidden', border: `1px solid ${rgba(palette.border, 0.6)}` }}>
            <GuardedImg src={src} style={imageStyle({ transform, filter: imageFilter })} />
          </div>
          {(directed ? approvedCaption : props.caption) && (
            <div
              style={{
                marginTop: '12px',
                fontFamily: visualDNA.typography?.body,
                color: palette.secondary,
                fontSize: 16,
                letterSpacing: '0.08em',
                textTransform: 'uppercase',
                textAlign: 'center',
              }}
            >
              {directed ? approvedCaption : props.caption}
            </div>
          )}
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 4: Split Stage (sharp crop with blur background)
  if (variant === 'split_stage') {
    const allowStageDim = Boolean(
      props?.allow_stage_dim === true
      || scene?.design?.allow_stage_dim === true
      || scene?.media_composition?.allow_stage_dim === true
    );
    return (
      <AbsoluteFill style={{ overflow: 'hidden', ...entranceStyle }}>
        <GuardedImg
          src={src}
          style={{
            position: 'absolute',
            inset: '-10%',
            width: '120%',
            height: '120%',
            objectFit: 'cover',
            filter: `${allowStageDim ? 'brightness(0.82)' : 'brightness(0.96)'} contrast(1.08) saturate(1.04)`,
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: '6%',
            top: '8%',
            width: '56%',
            height: '84%',
            borderRadius: cornerRadius,
            overflow: 'hidden',
            boxShadow: '0 14px 34px rgba(0,0,0,0.28)',
            border: `1px solid ${rgba(palette.border, 0.8)}`,
          }}
        >
          <GuardedImg src={src} style={imageStyle({ transform, filter: imageFilter })} />
        </div>
        {props.title && (
          <div
            style={{
              position: 'absolute',
              right: '6%',
              top: '25%',
              width: '30%',
              fontFamily: visualDNA.typography.display,
              color: palette.primary,
              fontSize: 36,
              lineHeight: 1.2,
            }}
          >
            <div style={{ color: palette.accent, fontSize: 16, letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 12 }}>
              {props.kicker || 'EVIDENCE'}
            </div>
            {props.title}
          </div>
        )}
      </AbsoluteFill>
    );
  }

  // Variant 5: Spotlight Reveal
  if (variant === 'spotlight_reveal') {
    const allowFocusDim = Boolean(
      props?.allow_focus_dim === true
      || scene?.design?.allow_focus_dim === true
      || scene?.media_composition?.focus_dim === true
    );
    const spotX = props.focalX || 50;
    const spotY = props.focalY || 45;
    const spotRadius = interpolate(enter, [0, 1], [15, 38]);
    return (
      <AbsoluteFill style={{ overflow: 'hidden', ...entranceStyle }}>
        <GuardedImg src={src} style={imageStyle({ transform, filter: allowFocusDim ? 'brightness(0.86) saturate(0.92)' : 'brightness(0.96) saturate(1.0)' })} />
        <AbsoluteFill
          style={{
            clipPath: `circle(${spotRadius}% at ${spotX}% ${spotY}%)`,
          }}
        >
          <GuardedImg src={src} style={imageStyle({ transform, filter: imageFilter })} />
        </AbsoluteFill>
        <div
          style={{
            position: 'absolute',
            left: `${spotX}%`,
            top: `${spotY}%`,
            transform: 'translate(-50%, -50%)',
            width: `${spotRadius * 2}%`,
            height: `${spotRadius * 2}%`,
            borderRadius: '50%',
            boxShadow: `0 0 20px 5px rgba(0,0,0,0.16), inset 0 0 20px ${rgba(palette.accent, 0.12)}`,
            pointerEvents: 'none',
          }}
        />
      </AbsoluteFill>
    );
  }

  // Variant 6: Parallax Float / 2.5D Parallax Camera Rig ("Ken Burns 2.0")
  if (hasCutout || variant === 'parallax_float' || variant === 'parallax_2_5d') {
    const bgScale = interpolate(progress, [0, 1], [1.14, 1.08]);
    const bgTranslateX = interpolate(progress, [0, 1], [-12, 12]);
    const bgTranslateY = interpolate(progress, [0, 1], [8, -8]);
    const fgScale = interpolate(progress, [0, 1], [1.0, 1.09]);
    const fgTranslateX = interpolate(progress, [0, 1], [10, -10]);
    const fgTranslateY = interpolate(progress, [0, 1], [-10, 10]);

    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: palette.background, overflow: 'hidden', ...entranceStyle }}>
        {/* Layer 1: Background Drifting Plane */}
        <AbsoluteFill
          style={{
            transform: `scale(${bgScale}) translate(${bgTranslateX}px, ${bgTranslateY}px)`,
            filter: hasCutout ? 'blur(3px) brightness(0.85) contrast(1.05)' : imageFilter,
          }}
        >
          <GuardedImg src={src} style={imageStyle({ objectFit: 'cover' })} />
        </AbsoluteFill>

        {/* Layer 2: Floating Atmospheric Dust Particles */}
        <Particles count={24} theme={{ palette }} intensity={0.6} color={palette.accent} />

        {/* Layer 3: Depth Typography */}
        {approvedCaption && (
          <div
            style={{
              position: 'absolute',
              top: '25%',
              left: '8%',
              right: '8%',
              zIndex: 3,
              fontFamily: visualDNA.typography?.display || 'Cinzel, serif',
              fontSize: Math.min(width * 0.05, 56),
              fontWeight: 800,
              color: rgba(palette.primary || '#FFFFFF', 0.88),
              textTransform: 'uppercase',
              letterSpacing: '0.08em',
              textShadow: '0 8px 30px rgba(0,0,0,0.85)',
              transform: `translateY(${interpolate(progress, [0, 1], [15, -15])}px)`,
              pointerEvents: 'none',
            }}
          >
            {approvedCaption}
          </div>
        )}

        {/* Layer 4: Foreground Subject Cutout Plane */}
        {hasCutout && (
          <AbsoluteFill
            style={{
              zIndex: 4,
              transform: `scale(${fgScale}) translate(${fgTranslateX}px, ${fgTranslateY}px)`,
              filter: 'drop-shadow(0 24px 45px rgba(0,0,0,0.75))',
            }}
          >
            <GuardedImg src={cutoutSrc} style={imageStyle({ objectFit: 'contain' })} />
          </AbsoluteFill>
        )}

        {/* Layer 5: Cinematic 35mm Optical Vignette */}
        <AbsoluteFill
          style={{
            zIndex: 5,
            pointerEvents: 'none',
            background: 'radial-gradient(circle at center, transparent 55%, rgba(0,0,0,0.45) 100%)',
          }}
        />
      </AbsoluteFill>
    );
  }

  // Variant 7: Archival Polaroid
  if (variant === 'archival_polaroid') {
    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: palette.background, ...entranceStyle }}>
        <div
          style={{
            width: Math.min(width * .72, height * .84),
            background: '#F7F4EA',
            padding: `${height * .02}px ${height * .02}px ${height * .04}px`,
            boxShadow: '0 28px 60px rgba(0,0,0,0.75), 0 4px 12px rgba(0,0,0,0.4)',
            transform: `rotate(-2deg) ${transform}`,
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          <div style={{ width: '100%', height: height * .58, overflow: 'hidden', background: '#222' }}>
            <GuardedImg src={src} style={imageStyle({ filter: 'sepia(0.3) contrast(1.1) saturate(0.9)' })} />
          </div>
          <div
            style={{
              marginTop: '22px',
              fontFamily: visualDNA.typography?.numbers || visualDNA.typography?.body || 'DM Sans',
              color: '#2D3748',
              fontSize: 18,
              textAlign: 'center',
              fontWeight: 600,
            }}
          >
            {directed ? approvedCaption : props.caption || props.date || ''}
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 8: Photo Stack
  const offset = interpolate(enter, [0, 1], [50, 0]);
  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: palette.background, ...entranceStyle }}>
      <div
        style={{
          position: 'absolute',
          width: '70%',
          height: '74%',
          background: palette.surface,
          transform: `rotate(-3deg) translateY(${offset * 0.4}px)`,
          borderRadius: cornerRadius,
          opacity: 0.5,
          border: `1px solid ${palette.border}`,
        }}
      />
      <div
        style={{
          position: 'absolute',
          width: '74%',
          height: '76%',
          borderRadius: cornerRadius,
          overflow: 'hidden',
          boxShadow: '0 30px 75px rgba(0,0,0,0.8)',
          border: `2px solid ${palette.border}`,
          transform: `rotate(1.5deg) translateY(${offset * 0.2}px)`,
        }}
      >
        <GuardedImg src={src} style={imageStyle({ transform, filter: imageFilter })} />
      </div>
    </AbsoluteFill>
  );
}
