/** Cover the entire local scene clock with ordered, half-open media cuts. */
export function normalizeMediaShotTiming(rows, sceneFrames, fps) {
  const limit = Math.max(1, Math.round(Number(sceneFrames) || 1));
  const rate = Math.max(1, Number(fps) || 30);
  const ordered = (Array.isArray(rows) ? rows : []).map((shot, originalIndex) => {
    if (!shot || !(shot.src || shot.path || shot.asset_path || shot.url)) return null;
    const seconds = Number(shot.shot_start ?? shot.start ?? 0);
    const from = Math.min(limit - 1, Math.max(0, Math.round((Number.isFinite(seconds) ? seconds : 0) * rate)));
    return { shot, originalIndex, from };
  }).filter(Boolean).sort((a, b) => a.from - b.from || a.originalIndex - b.originalIndex);
  // Rounded duplicate boundaries have no duration. Keep the final authored
  // shot at that boundary, then extend each cut to the next actual cut.
  const cuts = ordered.filter((item, index) => index === ordered.length - 1 || item.from !== ordered[index + 1].from);
  if (!cuts.length) return [];
  cuts[0].from = 0;
  return cuts.map((item, index) => ({
    ...item,
    duration: Math.max(1, (cuts[index + 1]?.from ?? limit) - item.from),
  }));
}
