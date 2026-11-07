/**
 * Shared subject-preserving media framing rules for the Remotion player.
 * Documentary stills are full-bleed by default so portrait search results do
 * not create dead black side bars. Evidence cards and other intentional
 * layouts can request `contain` explicitly, while verified focal boxes keep
 * crops anchored to the subject.
 */

export function normalizeFocalBox(value) {
  if (!Array.isArray(value) || value.length !== 4) return null;
  const numbers = value.map(Number);
  if (!numbers.every(Number.isFinite)) return null;
  const [x1, y1, x2, y2] = numbers;
  if (!(0 <= x1 && x1 < x2 && x2 <= 1 && 0 <= y1 && y1 < y2 && y2 <= 1)) {
    return null;
  }
  return [x1, y1, x2, y2];
}

export function resolveFocalBox(scene = {}, media = {}, props = {}) {
  return normalizeFocalBox(
    scene?.design?.focal_bbox
      || media?.subject_bbox
      || media?.focal_bbox
      || props?.focal_bbox
  );
}

export function focalObjectPosition(focal, fallback = 'center') {
  const box = normalizeFocalBox(focal);
  if (!box) return fallback || 'center';
  return `${((box[0] + box[2]) / 2) * 100}% ${((box[1] + box[3]) / 2) * 100}%`;
}

export function resolveMediaFit(media = {}, props = {}, isVideo = false) {
  // A deliberate director crop wins over retrieval's generic contain hint.
  // Evidence preservation remains authoritative unless the asset itself
  // explicitly opts into cropping.
  const directorFit = String(props?.fit || '').trim().toLowerCase();
  const assetFit = String(media?.fit || '').trim().toLowerCase();
  if (['force_cover', 'cover', 'fill'].includes(directorFit)) return 'cover';
  if (['force_cover', 'cover', 'fill'].includes(assetFit)) return 'cover';
  const requested = String(media?.fit || props?.fit || '').trim().toLowerCase();
  const ar = Number(
    media?.aspect_ratio
    || props?.aspect_ratio
    || (media?.width && media?.height ? media.width / media.height : 0)
    || (props?.width && props?.height ? props.width / props.height : 0)
  );
  const role = String(media?.content_role || media?.role || props?.content_role || props?.role || '').trim().toLowerCase();
  const preserveFrame = media?.preserve_frame === true
    || props?.preserve_frame === true
    || ['document', 'evidence', 'archive_page', 'source_scan', 'map_plate'].includes(role);

  if (requested === 'force_cover' || requested === 'fill') return 'cover';
  if (preserveFrame) return 'contain';

  // Retrieved web media is often stamped `fit: contain` + `safe_framing: true`
  // as a generic safety hint. On wide footage this shrinks a 16:9 hero into a
  // small inset. Keep explicit contain when it is a real layout choice; for
  // auto-safe assets, contain only portrait/square sources and fill wide or
  // dimensionless cinematic sources. Documents can opt in with preserve_frame.
  const hasSafeHint = media?.safe_framing === true || props?.safe_framing === true;
  if (requested === 'contain' || requested === 'fit' || requested === 'safe' || requested === 'center') {
    if (ar > 0 && ar < 1.35) return 'contain';
    if (hasSafeHint) return 'cover';
    return 'contain';
  }
  if (isVideo) return 'cover';
  // Square, 4:3, and portrait stills are kept whole. Wide documentary stills
  // use full-bleed framing unless their editorial role requests preservation.
  if (ar > 0 && ar < 1.35) {
    return 'contain';
  }
  return 'cover';
}

export function isContainedMedia(media = {}, props = {}, isVideo = false) {
  if (isVideo) return false;
  return resolveMediaFit(media, props, isVideo) === 'contain';
}
