import React from 'react';
import { Sequence, useCurrentFrame, useVideoConfig } from 'remotion';
import { normalizeMediaShotTiming } from '../engine/MediaShotTiming';

const shotSource = (shot = {}) => shot.src || shot.path || shot.asset_path || shot.url || '';

/**
 * Render a media planner's scene-local shot plan as real cuts in Remotion.
 * The shot planner has already screened and timed these sources; this layer
 * only selects the active shot and resets its local frame clock so video
 * source offsets and camera moves begin at the cut.
 */
export function MediaShotSequence({ scene = {}, media = {}, children }) {
  const localFrame = useCurrentFrame();
  const sourceOffset = Math.max(0, Number(scene.editor_media_window?.source_start_frame || 0));
  const frame = localFrame + sourceOffset;
  const { fps } = useVideoConfig();
  const sourceShots = Array.isArray(media?.shots) ? media.shots : [];
  const sceneFrames = Math.max(
    1,
    Math.round(Number(scene?.duration_in_frames || Number(scene?.duration || 1) * fps)),
  );
  const shots = normalizeMediaShotTiming(sourceShots, sceneFrames, fps);

  if (!shots.length) {
    const shifted = sourceOffset ? {...media,source_start:Number(media.source_start || media.in_point || 0)+sourceOffset/fps} : media;
    return children({scene,media:shifted});
  }

  const active = shots.find(({ from, duration }) => frame >= from && frame < from + duration)
    || shots.find(({ from }) => frame < from)
    || shots[shots.length - 1];
  const { shot, from, duration } = active;
  const shotDurationSeconds = duration / fps;
  const activeMedia = {
    ...media,
    ...shot,
    src: shotSource(shot),
    shots: undefined,
  };
  // Shot files are generally already cut to their source timestamps. Only an
  // explicit local seek may move inside that file; video_start/clip_start are
  // source provenance and must never become a local render seek. Apply zero
  // too, so a short downloaded cut cannot inherit a stale seven-second offset
  // from the scene-level media object.
  const sourceStartRaw = [shot.render_clip_start, shot.source_start, shot.in_point]
    .find((value) => value !== undefined && value !== null && value !== '');
  const sourceStart = Number(sourceStartRaw);
  if (Number.isFinite(sourceStart) && sourceStart >= 0) {
    activeMedia.source_start = sourceStart;
    activeMedia.in_point = sourceStart;
  }
  const shotCamera = shot.camera && typeof shot.camera === 'object' ? shot.camera : {};
  const shotCameraMove = String(
    scene.camera?.disabled ? 'hold' : shotCamera.motion
      || (typeof shot.camera === 'string' ? shot.camera : '')
      || (typeof shot.motion === 'string' ? shot.motion : '')
      || scene?.camera?.motion
      || scene?.motion?.camera
      || 'slow_push',
  );
  const activeScene = {
    ...scene,
    duration: shotDurationSeconds,
    duration_in_frames: duration,
    media: activeMedia,
    // Keep camera directives in the object shape expected by both SmartImage
    // and MediaLayer; shot planners may provide either a string or object.
    motion: {
      ...(scene?.motion && typeof scene.motion === 'object' ? scene.motion : {}),
      camera: shotCameraMove,
      // The scene transition owns the cut. Reset only the camera clock;
      // fading each shot from zero briefly exposed an empty background.
      entrance: 'none',
    },
    camera: {
      ...(scene?.camera && typeof scene.camera === 'object' ? scene.camera : {}),
      ...shotCamera,
      motion: shotCameraMove,
    },
  };

  return (
    <Sequence
      from={from - sourceOffset}
      durationInFrames={duration}
      name={`media-shot-${scene.scene_index ?? 0}-${active.originalIndex}`}
    >
      {children({ scene: activeScene, media: activeMedia, shot })}
    </Sequence>
  );
}

export { shotSource as resolveMediaShotSource };
