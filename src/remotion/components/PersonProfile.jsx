import React from 'react';
import { AbsoluteFill, Img, OffthreadVideo, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { computeSpringEnter, computeEntranceStyle } from '../engine/MotionEngine';
import { documentaryTypography, fittedDocumentarySize } from '../engine/DocumentaryTypography';
import { spokenPhraseFrame } from '../engine/DirectorMotion';

const VIDEO_EXTENSIONS = /\.(?:mp4|webm|mov|mkv|avi|m4v)(?:[?#]|$)/i;

/**
 * Open, source-led subject identification. Older manifests may still name a
 * dossier variant, but the renderer deliberately resolves every variant to
 * this uncluttered portrait treatment instead of recreating fake case files.
 */
export function PersonProfile({ scene = {}, visualDNA = {}, props = {}, asset = {} }) {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const scale = width / 1920;
  const sceneDuration = Math.max(1, scene?.duration_in_frames != null
    ? Math.round(Number(scene.duration_in_frames))
    : Math.round(Number(scene?.duration || 4) * fps));
  const progress = Math.min(1, Math.max(0, frame / Math.max(1, sceneDuration - 1)));
  const enter = computeSpringEnter(frame, fps, 0, scene?.motion?.spring_stiffness || 150, scene?.motion?.spring_damping || 19);
  const entranceStyle = computeEntranceStyle(scene?.motion?.entrance || 'fade', enter, progress);
  const palette = visualDNA.palette || {};
  const typography = documentaryTypography(visualDNA);
  const photo = asset?.src || asset?.path || props?.photo || props?.src || '';
  // Portrait cutouts are optional alpha assets supplied by the media stage.
  // Never fake background removal by cropping an ordinary opaque photograph.
  const cutoutSource = props?.cutout_src || props?.foreground_cutout || asset?.cutout_src || asset?.foreground_cutout || '';
  const hasCutout = Boolean(cutoutSource && !VIDEO_EXTENSIONS.test(String(cutoutSource)));
  const peopleContract = props.people_copy_contract || {};
  const narrated = String(scene.narration || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const people = peopleContract.approved === true && peopleContract.source === 'llm_scene_repair'
    ? (Array.isArray(props.people) ? props.people : []).filter(person => person.label && person.source_span
      && narrated.includes(String(person.source_span).toLowerCase())
      && String(person.source_span).toLowerCase().includes(String(person.label).toLowerCase())) : [];
  const entries = people.map(person => ({ ...person, at: spokenPhraseFrame(scene, person.label, fps)
    ?? Math.round(Math.max(0, narrated.indexOf(String(person.label).toLowerCase())) / Math.max(1, narrated.length) * sceneDuration) }));
  const activePerson = entries.filter(person => frame >= person.at).at(-1) || entries[0];
  const name = String(activePerson?.label || props.name || scene.text?.headline || '').trim();
  const role = String(props.role || scene.text?.subtext || '').trim();
  const safeDetails = Array.isArray(props.details)
    ? props.details.filter((item) => item && String(item.value || '').trim()).slice(0, 3)
    : [];
  const placement = String(props.position || scene.placement?.position || 'bottom_left').toLowerCase();
  const variant = String(props.variant || scene.primary_visual?.variant || 'cinematic_lower_third_card').toLowerCase();
  // A verified group photograph identifies several people. Keep the entire
  // group in one frame; a split portrait crop can remove the active person.
  const fullBleedPortrait = variant === 'portrait_full_bleed' || people.length > 1;
  const minimalSplit = variant === 'minimal_profile_split';
  const textSide = fullBleedPortrait ? 'left' : minimalSplit ? 'right' : (placement.includes('right') ? 'right' : 'left');
  const photoSide = fullBleedPortrait ? 'right' : minimalSplit ? 'left' : textSide === 'left' ? 'right' : 'left';
  const top = fullBleedPortrait ? 'auto' : placement.includes('top') ? '13%' : 'auto';
  const bottom = fullBleedPortrait ? '10%' : placement.includes('top') ? 'auto' : '13%';

  if (!name) return null;

  const textWidth = fullBleedPortrait ? (hasCutout ? .54 : .78) : hasCutout ? (minimalSplit ? .42 : .53) : photo ? (minimalSplit ? .42 : .61) : .78;
  const titleSize = Math.min(height * .15, ...name.toUpperCase().split(/\s+/).map(word => fittedDocumentarySize(word,
    {fontFamily: typography.display, maxSize: (name.length > 28 ? 54 : name.length > 18 ? 68 : 82) * scale, withinWidth: width * textWidth - 16 * scale})));
  const reveal = interpolate(enter, [0, 1], [28 * scale, 0]);

  return (
    <AbsoluteFill style={{ overflow: 'hidden', ...entranceStyle }}>
      {hasCutout && (
        <>
          <AbsoluteFill style={{ background: `radial-gradient(ellipse at ${photoSide === 'left' ? '28%' : '72%'} 54%, ${palette.accent || '#D9A441'}24 0%, transparent 43%), linear-gradient(135deg, #07121B, #101A20 62%, #080D12)` }} />
          <div style={{ position: 'absolute', [photoSide]: '2%', bottom: 0, width: fullBleedPortrait ? '64%' : '56%', height: '94%', display: 'flex', alignItems: 'flex-end', justifyContent: 'center', pointerEvents: 'none', overflow: 'hidden' }}>
            <Img
              src={cutoutSource}
              style={{
                width: '100%',
                height: '100%',
                objectFit: 'contain',
                objectPosition: 'center bottom',
                opacity: enter,
                transform: `translateY(${((1 - enter) * 28).toFixed(2)}px) scale(${(.985 + enter * .015).toFixed(4)})`,
                clipPath: `inset(0 0 ${interpolate(enter, [0, 1], [100, 0]).toFixed(2)}% 0)`,
                filter: `drop-shadow(0 18px 30px rgba(0,0,0,.42)) drop-shadow(0 0 16px ${palette.accent || '#D9A441'}35)`,
              }}
            />
          </div>
        </>
      )}
      {photo && !hasCutout && (
        <div style={{ position: 'absolute', inset: fullBleedPortrait ? 0 : photoSide === 'right' ? '0 0 0 auto' : '0 auto 0 0', width: fullBleedPortrait ? '100%' : minimalSplit ? '50%' : '43%', overflow: 'hidden', opacity: 0.96 }}>
          {VIDEO_EXTENSIONS.test(photo) ? (
            <OffthreadVideo
              src={photo}
              muted
              style={{
                width: '100%',
                height: '100%',
                objectFit: 'cover',
                objectPosition: 'center 35%',
                filter: 'saturate(.78) contrast(1.04)',
                maskImage: fullBleedPortrait ? undefined : (photoSide === 'left' ? 'linear-gradient(90deg, black 75%, transparent 100%)' : 'linear-gradient(90deg, transparent 0%, black 25%)'),
                WebkitMaskImage: fullBleedPortrait ? undefined : (photoSide === 'left' ? 'linear-gradient(90deg, black 75%, transparent 100%)' : 'linear-gradient(90deg, transparent 0%, black 25%)'),
                transform: `scale(${(1.04 - enter * 0.04).toFixed(4)})`,
              }}
            />
          ) : (
            <Img
              src={photo}
              style={{
                width: '100%',
                height: '100%',
                objectFit: 'cover',
                objectPosition: 'center 35%',
                filter: 'saturate(.78) contrast(1.04)',
                maskImage: fullBleedPortrait ? undefined : (photoSide === 'left' ? 'linear-gradient(90deg, black 75%, transparent 100%)' : 'linear-gradient(90deg, transparent 0%, black 25%)'),
                WebkitMaskImage: fullBleedPortrait ? undefined : (photoSide === 'left' ? 'linear-gradient(90deg, black 75%, transparent 100%)' : 'linear-gradient(90deg, transparent 0%, black 25%)'),
                transform: `scale(${(1.04 - enter * 0.04).toFixed(4)})`,
              }}
            />
          )}
        </div>
      )}
      {fullBleedPortrait && <AbsoluteFill style={{ background: 'linear-gradient(0deg, rgba(4,9,15,.84), rgba(4,9,15,.08) 66%, rgba(4,9,15,.10))', pointerEvents: 'none' }} />}
      <div
        style={{
          position: 'absolute',
          [textSide]: '8%',
          top,
          bottom,
          width: `${textWidth * 100}%`,
          maxWidth: 1320 * scale,
          transform: `translateY(${reveal}px)`,
          color: palette.primary || '#F8FAFC',
          textAlign: textSide,
          filter: 'drop-shadow(0 3px 16px rgba(0,0,0,.72))',
        }}
      >
        {role && (
          <div style={{ fontFamily: typography.body, color: palette.accent || '#E6B84A', fontSize: 18 * scale, fontWeight: 700, letterSpacing: '.12em', lineHeight: 1.2, textTransform: 'uppercase', marginBottom: 14 * scale }}>
            {role}
          </div>
        )}
        <div style={{ width: 58 * scale, height: 3 * scale, background: palette.accent || '#E6B84A', marginBottom: 16 * scale, marginLeft: textSide === 'right' ? 'auto' : 0, transformOrigin: textSide === 'right' ? 'right' : 'left', transform: `scaleX(${enter.toFixed(3)})` }} />
        <div style={{ fontFamily: typography.display, fontSize: titleSize, fontWeight: 800, lineHeight: 1.06, letterSpacing: '-.025em', textTransform: 'uppercase', overflowWrap: 'normal', wordBreak: 'normal' }}>
          {name}
        </div>
        {safeDetails.length > 0 && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: `${10 * scale}px ${26 * scale}px`, marginTop: 20 * scale, fontFamily: typography.body, fontSize: 15 * scale, color: palette.secondary || '#CBD5E1' }}>
            {safeDetails.map((detail, index) => (
              <span key={`${detail.label || 'detail'}-${index}`}>
                {detail.label && <strong style={{ color: palette.primary || '#F8FAFC', marginRight: 7 }}>{String(detail.label).toUpperCase()}</strong>}
                {detail.value}
              </span>
            ))}
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
}
