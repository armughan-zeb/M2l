import React, { useState } from 'react';
import { AbsoluteFill, OffthreadVideo as RemotionOffthreadVideo, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { calculateCameraTransform, computeSpringEnter, computeEntranceStyle } from '../engine/MotionEngine';
import { rgba } from '../theme';
import { focalObjectPosition, resolveFocalBox, resolveMediaFit } from '../engine/FocalFrame';
import { resolveColorGrade } from '../engine/ColorGrade';
import { MissingSceneMotionGraphic } from './MissingSceneMotionGraphic';
import { resolveMediaKind } from '../engine/MediaKind';
import { isRecoveryMedia } from '../engine/RecoveryMedia';
import { SmartImage } from './SmartImage';

/**
 * SmartVideo — Universal Documentary B-roll & Footage Presentation Engine.
 * Supported variants:
 * - full_bleed / full_bleed_cinematic
 * - pip_inset
 * - split_duo
 * - blurred_backdrop
 * - film_gate
 * - browser_frame
 * - evidence_clip
 * - device_mockup
 */
export function SmartVideo({ scene = {}, visualDNA = {}, props = {}, asset = {} }) {
  const [mediaFailed, setMediaFailed] = useState(false);
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const sourceStartSeconds = Math.max(0, Number(
    asset?.source_start ?? asset?.in_point ?? props?.source_start ?? scene?.media?.source_start ?? 0
  ) || 0);
  const sourceStartFrames = Math.round(sourceStartSeconds * fps);
  const videoTiming = sourceStartFrames > 0 ? { startFrom: sourceStartFrames } : {};
  const sceneDuration = Math.max(1, scene?.duration_in_frames !== undefined && scene?.duration_in_frames !== null
    ? Math.round(Number(scene.duration_in_frames))
    : Math.round(Number(scene?.duration || 4) * fps));
  const progress = Math.min(1, Math.max(0, frame / Math.max(1, sceneDuration - 1)));

  const src = asset?.src || asset?.path || props?.src || scene?.media?.src || scene?.media?.path || scene?.primary_visual?.asset?.src || '';
  // Final decoder boundary: callers outside the scene registry may still
  // supply a recovered still with stale video metadata.
  if (isRecoveryMedia(asset, src) || isRecoveryMedia(scene.media, src)) {
    return <MissingSceneMotionGraphic scene={scene} visualDNA={visualDNA} />;
  }
  if (src && resolveMediaKind({ ...(scene.media || {}), ...asset }, src) !== 'video') {
    return <SmartImage scene={scene} visualDNA={visualDNA} props={props} asset={{ ...(scene.media || {}), ...asset, src }} />;
  }
  const GuardedOffthreadVideo = (videoProps) => <RemotionOffthreadVideo {...videoProps} onError={(error) => {
    videoProps.onError?.(error);
    setMediaFailed(true);
  }} />;
  if (mediaFailed) return <MissingSceneMotionGraphic scene={scene} visualDNA={visualDNA} />;
  if (!src) return <MissingSceneMotionGraphic scene={scene} visualDNA={visualDNA} />;
  const requestedVariant = String(props?.variant || scene?.primary_visual?.variant || 'full_bleed_cinematic').toLowerCase();
  const sparseMediaOnly = Boolean(
    (scene?.text && Object.prototype.hasOwnProperty.call(scene.text, 'overlay_policy') && String(scene.text.overlay_policy || 'none').toLowerCase() === 'none')
    || String(scene?.text?.frame_mode || '').toLowerCase() === 'media_only'
    || String(scene?.frame_mode || '').toLowerCase() === 'media_only'
  );
  // Sparse documentary beats and media-only scenes must fill the frame with footage,
  // never shrinking down into a PIP corner box or half-screen void.
  const variant = sparseMediaOnly && ['pip_inset', 'split_duo', 'film_gate', 'browser_frame', 'device_mockup', 'phone_mockup'].includes(requestedVariant)
    ? 'full_bleed_cinematic'
    : requestedVariant;
  const focal = resolveFocalBox(scene, asset, props);
  const videoFit = resolveMediaFit(asset, props, true);
  const videoPosition = focalObjectPosition(focal, asset?.position || props?.position || 'center');
  const videoStyle = (extra = {}) => ({
    width: '100%',
    height: '100%',
    objectFit: videoFit,
    objectPosition: videoPosition,
    ...extra,
  });
  const camera = scene?.motion?.camera || props?.camera || 'static';
  const transform = calculateCameraTransform(camera, progress);
  const enter = computeSpringEnter(frame, fps, 0, scene?.motion?.spring_stiffness || 120, scene?.motion?.spring_damping || 18);
  const entranceStyle = computeEntranceStyle(scene?.motion?.entrance || 'fade', enter, progress);

  const palette = visualDNA.palette || {};
  const cornerRadius = visualDNA.cornerRadius ?? 4;

  // Professional documentary color grading: crisp contrast, rich saturation, deep blacks
  const videoFilter = resolveColorGrade(scene, visualDNA).filter;

  // Variant 1: Full Bleed Cinematic
  if (variant === 'full_bleed' || variant === 'full_bleed_cinematic' || variant === 'default' || variant === 'cinematic') {
    return (
      <AbsoluteFill style={{ overflow: 'hidden', ...entranceStyle }}>
        <GuardedOffthreadVideo src={src} muted {...videoTiming} style={videoStyle({ transform, filter: videoFilter })} />
        <AbsoluteFill
          style={{
            background: `linear-gradient(180deg, transparent 70%, ${rgba(palette.background, 0.3)} 100%)`,
            pointerEvents: 'none',
          }}
        />
      </AbsoluteFill>
    );
  }

  // Variant 2: Blurred Backdrop
  if (variant === 'blurred_backdrop') {
    const allowBackdropDim = Boolean(
      props?.allow_backdrop_dim === true
      || scene?.design?.allow_backdrop_dim === true
      || scene?.media_composition?.allow_backdrop_dim === true
    );
    return (
      <AbsoluteFill style={{ overflow: 'hidden', ...entranceStyle }}>
        <GuardedOffthreadVideo
          src={src}
          muted
          {...videoTiming}
          style={{ position: 'absolute', inset: '-10%', width: '120%', height: '120%', objectFit: 'cover', filter: `${allowBackdropDim ? 'brightness(0.72)' : 'brightness(0.88)'} contrast(1.12) saturate(0.85)` }}
        />
        <div
          style={{
            position: 'absolute',
            inset: '5% 8%',
            borderRadius: cornerRadius,
            overflow: 'hidden',
            boxShadow: '0 14px 34px rgba(0,0,0,0.28)',
            border: `1px solid ${palette.border}`,
          }}
        >
          <GuardedOffthreadVideo src={src} muted {...videoTiming} style={videoStyle({ objectFit: 'contain', filter: videoFilter })} />
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 3: PiP Inset (Cinematic backdrop with framed inset and editorial typography)
  if (variant === 'pip_inset') {
    const isRight = props.position !== 'left';
    return (
      <AbsoluteFill style={{ overflow: 'hidden', ...entranceStyle }}>
        {/* Full-bleed blurred cinematic backdrop - NEVER a black void */}
        <GuardedOffthreadVideo
          src={src}
          muted
          {...videoTiming}
          style={{
            position: 'absolute',
            inset: '-10%',
            width: '120%',
            height: '120%',
            objectFit: 'cover',
            filter: 'brightness(0.2) contrast(1.15) saturate(0.6)',
          }}
        />
        <AbsoluteFill
          style={{
            background: `radial-gradient(circle at ${isRight ? '80% 70%' : '20% 70%'}, rgba(0,0,0,0.2) 0%, rgba(0,0,0,0.65) 100%)`,
            pointerEvents: 'none',
          }}
        />

        {/* Editorial Content Side */}
        <div
          style={{
            position: 'absolute',
            [isRight ? 'left' : 'right']: '7%',
            top: '20%',
            width: '46%',
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'center',
            zIndex: 2,
          }}
        >
          <div
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              color: palette.accent || '#FACC15',
              fontFamily: visualDNA.typography?.numbers || visualDNA.typography?.body,
              fontSize: 14,
              fontWeight: 800,
              letterSpacing: '0.18em',
              textTransform: 'uppercase',
              marginBottom: 16,
            }}
          >
            <span style={{ width: 10, height: 10, borderRadius: '50%', background: palette.accent || '#FACC15' }} />
            {props.kicker || 'REPORTED FOOTAGE'}
          </div>
          <div
            style={{
              fontFamily: visualDNA.typography?.display || 'Archivo Black, sans-serif',
              color: palette.primary || '#FFFFFF',
              fontSize: 48,
              fontWeight: 900,
              lineHeight: 1.05,
              letterSpacing: '-0.03em',
              textShadow: '0 4px 20px rgba(0,0,0,0.8)',
            }}
          >
            {props.title || props.headline || scene.text?.headline || 'DOCUMENTARY ARCHIVE'}
          </div>
          {(props.subtitle || props.description || scene.narration) && (
            <div
              style={{
                marginTop: 18,
                color: palette.secondary || '#CBD5E1',
                fontFamily: visualDNA.typography?.body,
                fontSize: 18,
                lineHeight: 1.35,
                fontWeight: 500,
                maxWidth: '90%',
              }}
            >
              {props.subtitle || props.description || (scene.narration?.length > 120 ? scene.narration.slice(0, 117) + '...' : scene.narration)}
            </div>
          )}
        </div>

        {/* Crisp Framed PiP Box */}
        <div
          style={{
            position: 'absolute',
            [isRight ? 'right' : 'left']: '6%',
            bottom: '10%',
            width: '42%',
            height: '48%',
            borderRadius: cornerRadius + 2,
            overflow: 'hidden',
            boxShadow: '0 24px 60px rgba(0,0,0,0.85), 0 0 0 1px rgba(255,255,255,0.2)',
            zIndex: 3,
          }}
        >
          <GuardedOffthreadVideo src={src} muted {...videoTiming} style={videoStyle({ filter: videoFilter })} />
          <div
            style={{
              position: 'absolute',
              top: 10,
              right: 12,
              background: 'rgba(0,0,0,0.7)',
              padding: '4px 10px',
              borderRadius: 4,
              color: '#FFFFFF',
              fontFamily: visualDNA.typography?.numbers || visualDNA.typography?.body,
              fontSize: 12,
              fontWeight: 700,
              letterSpacing: '0.1em',
            }}
          >
            INSET FEED
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 4: Film Gate (Full bleed with archival 35mm matte frame)
  if (variant === 'film_gate') {
    return (
      <AbsoluteFill style={{ overflow: 'hidden', ...entranceStyle }}>
        <GuardedOffthreadVideo src={src} muted {...videoTiming} style={videoStyle({ transform, filter: `${videoFilter} brightness(1)` })} />
        <div
          style={{
            position: 'absolute',
            inset: 0,
            border: '24px solid rgba(11, 15, 20, 0.75)',
            boxShadow: 'inset 0 0 40px rgba(0,0,0,0.8)',
            pointerEvents: 'none',
          }}
        >
          <div
            style={{
              position: 'absolute',
              top: 12,
              left: 16,
              fontFamily: visualDNA.typography?.numbers || visualDNA.typography?.body,
              color: 'rgba(255, 255, 255, 0.65)',
              fontSize: 13,
              letterSpacing: '0.18em',
            }}
          >
            [35MM ARCHIVAL MASTER]
          </div>
          <div
            style={{
              position: 'absolute',
              bottom: 12,
              right: 16,
              fontFamily: visualDNA.typography?.numbers || visualDNA.typography?.body,
              color: palette.accent || 'rgba(255, 244, 215, 0.65)',
              fontSize: 13,
              letterSpacing: '0.15em',
            }}
          >
            FRAME {frame}
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 5: Evidence Clip
  if (variant === 'evidence_clip') {
    return (
      <AbsoluteFill style={{ overflow: 'hidden', ...entranceStyle }}>
        <GuardedOffthreadVideo src={src} muted {...videoTiming} style={videoStyle({ filter: `${videoFilter} saturate(0.85)` })} />
        <div style={{ position: 'absolute', inset: '5%', border: `1px solid ${rgba(palette.accent, 0.6)}`, pointerEvents: 'none' }}>
          <div
            style={{
              position: 'absolute',
              top: 14,
              left: 18,
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              background: 'rgba(0,0,0,0.7)',
              padding: '6px 12px',
              fontFamily: visualDNA.typography?.numbers || visualDNA.typography?.body,
              color: '#FFFFFF',
              fontSize: 14,
              letterSpacing: '0.1em',
            }}
          >
            <div style={{ width: 10, height: 10, borderRadius: '50%', background: '#EF4444' }} />
            <span>REC // EVIDENCE EXH-B</span>
          </div>
          <div
            style={{
              position: 'absolute',
              bottom: 14,
              right: 18,
              background: 'rgba(0,0,0,0.7)',
              padding: '6px 12px',
              fontFamily: visualDNA.typography?.numbers || visualDNA.typography?.body,
              color: '#FFFFFF',
              fontSize: 14,
            }}
          >
            TC: {Math.floor(frame / fps)}:{(frame % fps).toString().padStart(2, '0')}
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 6: Browser Frame
  if (variant === 'browser_frame') {
    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', overflow: 'hidden', ...entranceStyle }}>
        {/* Softly blurred backdrop of the video */}
        <GuardedOffthreadVideo
          src={src}
          muted
          {...videoTiming}
          style={{ position: 'absolute', inset: '-10%', width: '120%', height: '120%', objectFit: 'cover', filter: 'brightness(0.18) contrast(1.12) saturate(0.55)' }}
        />
        <div
          style={{
            width: '88%',
            height: '84%',
            background: palette.surface,
            borderRadius: 8,
            overflow: 'hidden',
            boxShadow: '0 30px 80px rgba(0,0,0,0.85)',
            border: `1px solid ${palette.border}`,
            display: 'flex',
            flexDirection: 'column',
            zIndex: 2,
          }}
        >
          <div
            style={{
              height: 38,
              background: rgba(palette.background, 0.8),
              borderBottom: `1px solid ${palette.border}`,
              display: 'flex',
              alignItems: 'center',
              padding: '0 16px',
              gap: 8,
            }}
          >
            <div style={{ width: 10, height: 10, borderRadius: '50%', background: '#EF4444' }} />
            <div style={{ width: 10, height: 10, borderRadius: '50%', background: '#F59E0B' }} />
            <div style={{ width: 10, height: 10, borderRadius: '50%', background: '#10B981' }} />
            <div
              style={{
                marginLeft: 18,
                flex: 1,
                height: 22,
                background: palette.surface,
                borderRadius: 4,
                padding: '0 10px',
                display: 'flex',
                alignItems: 'center',
                color: palette.secondary,
                fontSize: 12,
                fontFamily: visualDNA.typography?.body,
              }}
            >
              {props.url || 'https://archive.org/documentary/stream'}
            </div>
          </div>
          <div style={{ flex: 1, overflow: 'hidden' }}>
            <GuardedOffthreadVideo src={src} muted {...videoTiming} style={videoStyle({ filter: videoFilter })} />
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 7: Split Duo (Rich documentary panel, NEVER a black void)
  if (variant === 'split_duo') {
    return (
      <AbsoluteFill style={{ overflow: 'hidden', display: 'flex', flexDirection: 'row', ...entranceStyle }}>
        {/* Left Side: Video */}
        <div style={{ flex: 1, height: '100%', overflow: 'hidden', position: 'relative', borderRight: `2px solid ${palette.border || 'rgba(255,255,255,0.2)'}` }}>
          <GuardedOffthreadVideo src={src} muted {...videoTiming} style={videoStyle({ filter: videoFilter })} />
        </div>

        {/* Right Side: Editorial Panel - NEVER a flat black void */}
        <div
          style={{
            flex: 1,
            height: '100%',
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'center',
            padding: '0 8%',
            background: `linear-gradient(135deg, ${palette.surface || '#151b22'} 0%, ${palette.background || '#0b0f14'} 100%)`,
            position: 'relative',
          }}
        >
          <div
            style={{
              position: 'absolute',
              inset: 0,
              opacity: 0.1,
              backgroundImage: `linear-gradient(${palette.border || '#333'} 1px, transparent 1px), linear-gradient(90deg, ${palette.border || '#333'} 1px, transparent 1px)`,
              backgroundSize: '40px 40px',
              pointerEvents: 'none',
            }}
          />
          <div
            style={{
              color: palette.accent || '#FACC15',
              fontSize: 14,
              fontFamily: visualDNA.typography?.numbers || visualDNA.typography?.body,
              fontWeight: 800,
              letterSpacing: '0.18em',
              textTransform: 'uppercase',
              marginBottom: 16,
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              zIndex: 1,
            }}
          >
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: palette.accent || '#FACC15' }} />
            {props.kicker || 'KEY DISCOVERY'}
          </div>
          <div
            style={{
              color: palette.primary || '#FFFFFF',
              fontSize: 44,
              fontWeight: 900,
              fontFamily: visualDNA.typography?.display || 'Archivo Black, sans-serif',
              lineHeight: 1.1,
              letterSpacing: '-0.03em',
              zIndex: 1,
            }}
          >
            {props.title || props.headline || scene.text?.headline || scene.narration}
          </div>
          <div
            style={{
              marginTop: 22,
              width: 80,
              height: 3,
              background: palette.accent || '#FACC15',
              zIndex: 1,
            }}
          />
          {(props.subtitle || props.description) && (
            <div
              style={{
                marginTop: 20,
                color: palette.secondary || '#CBD5E1',
                fontFamily: visualDNA.typography?.body,
                fontSize: 18,
                lineHeight: 1.4,
                zIndex: 1,
              }}
            >
              {props.subtitle || props.description}
            </div>
          )}
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 8: Device Mockup (Phone / Monitor with blurred ambient backdrop)
  if (variant === 'device_mockup' || variant === 'phone_mockup') {
    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', overflow: 'hidden', ...entranceStyle }}>
        <GuardedOffthreadVideo
          src={src}
          muted
          {...videoTiming}
          style={{ position: 'absolute', inset: '-10%', width: '120%', height: '120%', objectFit: 'cover', filter: 'brightness(0.18) contrast(1.12) saturate(0.55)' }}
        />
        <div
          style={{
            width: '380px',
            height: '740px',
            borderRadius: 36,
            border: '8px solid #2D3748',
            boxShadow: '0 32px 80px rgba(0,0,0,0.85)',
            overflow: 'hidden',
            position: 'relative',
            background: '#000',
            zIndex: 2,
          }}
        >
          <GuardedOffthreadVideo src={src} muted {...videoTiming} style={videoStyle({ filter: videoFilter })} />
        </div>
      </AbsoluteFill>
    );
  }

  // Default fallback: Full Bleed Cinematic
  return (
    <AbsoluteFill style={{ overflow: 'hidden', ...entranceStyle }}>
      <GuardedOffthreadVideo src={src} muted {...videoTiming} style={videoStyle({ transform, filter: videoFilter })} />
      <AbsoluteFill
        style={{
          background: `linear-gradient(180deg, transparent 70%, ${rgba(palette.background, 0.3)} 100%)`,
          pointerEvents: 'none',
        }}
      />
    </AbsoluteFill>
  );
}
