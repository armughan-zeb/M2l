// The active file wins over stale scene/shot metadata after media recovery.
export function resolveMediaKind(media = {}, source = '') {
  media = media || {};
  let src = String(source || media.src || media.path || media.asset_path || media.url || '');
  if (/^data:image\//i.test(src)) return 'image';
  if (/^data:video\//i.test(src)) return 'video';
  // Remove URL query/hash syntax before decoding the filename. Encoded # and
  // ? characters belong to the asset path, not to its query string.
  for (let i = 0; i < 3 && /^(?:https?|file):/i.test(src); i++) {
    try {
      const url = new URL(src);
      if (/\/proxy\/?$/i.test(url.pathname) && url.searchParams.get('src')) {
        src = url.searchParams.get('src');
        continue;
      }
      src = url.pathname;
      break;
    } catch (_) { break; }
  }
  src = src.split('?', 1)[0];
  for (let i = 0; i < 2; i++) {
    try {
      const decoded = decodeURIComponent(src);
      if (decoded === src) break;
      src = decoded;
    } catch (_) { break; }
  }
  const path = src;
  if (/\.(?:jpe?g|png|webp|gif|avif|bmp|svg)$/i.test(path)) return 'image';
  if (/\.(?:mp4|webm|mov|mkv|avi|m4v)$/i.test(path)) return 'video';
  const type = String(media.media_type || media.type || '').trim().toLowerCase();
  return type === 'video' || type.startsWith('video/') ? 'video' : 'image';
}
