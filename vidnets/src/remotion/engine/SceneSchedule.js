/* Match Sequence's exact scene interval before expensive layout preparation. */
export function sceneFrameWindow(scene, fps = 30) {
  const from = scene.from !== undefined && scene.from !== null
    ? Math.max(0, Math.round(Number(scene.from)))
    : Math.max(0, Math.round(Number(scene.start || 0) * fps));
  const duration = Math.max(1, scene.duration_in_frames !== undefined && scene.duration_in_frames !== null
    ? Math.round(Number(scene.duration_in_frames))
    : Math.round(Number(scene.duration || 1) * fps));
  return {from, duration};
}

export function activeSceneEntries(scenes, frame, fps = 30) {
  const result = [];
  for (let index = 0; index < scenes.length; index++) {
    const scene = scenes[index];
    const {from, duration} = sceneFrameWindow(scene, fps);
    // Invalid contracts still reach the renderer's existing validation path.
    if (!Number.isFinite(from) || !Number.isFinite(duration)
        || (frame >= from && frame < from + duration)) result.push({scene, index});
  }
  return result;
}
