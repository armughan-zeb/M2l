export function isRecoveryMedia(media = {}, source = '') {
  const path = String(source || media.src || media.path || media.asset_path || '').replace(/\\/g, '/');
  const fallback = media.graphic_fallback;
  return Boolean(media.is_graphic_fallback || (fallback && typeof fallback === 'object' && Object.keys(fallback).length > 0) || media.is_synthetic
    || /(?:^|\/)(?:graphic_fallback|emergency_render|placeholder)_/i.test(path)
    || /\/render_media\/emergency\//i.test(path)
    || /^(?:graphic|neutral)$/.test(String(media.source || media.provider || '')));
}
