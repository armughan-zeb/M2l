// Shared typography rules for preview and export.  Matching exact family
// names avoids the old /serif/ regex bug, which accidentally rejected the
// valid "sans-serif" fallback stack.

const FAMILY_ALIASES = new Map([
  ['archivo black', 'Archivo Black'],
  ['plus jakarta sans', 'Plus Jakarta Sans'],
  ['inter', 'Inter'],
  ['dm sans', 'DM Sans'],
  ['barlow condensed', 'Barlow Condensed'],
  ['jetbrains mono', 'JetBrains Mono'],
  ['oswald', 'Oswald'],
]);

export const DOCUMENTARY_FONT_STACKS = Object.freeze({
  display: 'Archivo Black, Inter, sans-serif',
  editorial: 'Plus Jakarta Sans, Inter, sans-serif',
  body: 'DM Sans, Inter, sans-serif',
  compact: 'Barlow Condensed, Inter, sans-serif',
  numbers: 'JetBrains Mono, Inter, monospace',
});

function clean(value) {
  return String(value ?? '').replace(/\s+/g, ' ').trim();
}

function firstApprovedFamily(value) {
  for (const entry of clean(value).split(',')) {
    const normalized = entry.trim().replace(/^['"]|['"]$/g, '').toLowerCase();
    const approved = FAMILY_ALIASES.get(normalized);
    if (approved) return approved;
  }
  return '';
}

export function approvedFontStack(requested, fallback = DOCUMENTARY_FONT_STACKS.body) {
  const approved = firstApprovedFamily(requested);
  if (!approved) return fallback;
  const fallbackFamily = fallback.includes('mono') ? 'monospace' : 'sans-serif';
  return `${approved}, ${fallbackFamily}`;
}

export function documentaryTypography(visualDNA = {}) {
  const typography = visualDNA.typography || {};
  return {
    display: approvedFontStack(typography.display, DOCUMENTARY_FONT_STACKS.display),
    editorial: approvedFontStack(typography.editorial || typography.body, DOCUMENTARY_FONT_STACKS.editorial),
    body: approvedFontStack(typography.body, DOCUMENTARY_FONT_STACKS.body),
    compact: approvedFontStack(typography.compact, DOCUMENTARY_FONT_STACKS.compact),
    numbers: approvedFontStack(typography.numbers, DOCUMENTARY_FONT_STACKS.numbers),
  };
}

export function documentaryFontSize(text, { role = 'corner', width = 1920 } = {}) {
  const chars = clean(text).length;
  const scale = Math.max(0.5, Number(width || 1920) / 1920);
  const baseline = role === 'hero' ? 108 : role === 'statement' ? 86 : role === 'quote' ? 46 : 52;
  const min = role === 'hero' ? 80 : role === 'statement' ? 64 : role === 'quote' ? 36 : 42;
  const penalty = chars > 32 ? Math.min(baseline - min, Math.ceil((chars - 32) * 0.9)) : 0;
  return Math.round(Math.max(min, baseline - penalty) * scale);
}

// Measure the complete phrase. A minimum font size must never force clipping.
export function fittedDocumentarySize(text, { fontFamily = DOCUMENTARY_FONT_STACKS.display, maxSize = 80, withinWidth = 600, fontWeight = 800 } = {}) {
  const phrase = clean(text);
  if (!phrase) return maxSize;
  let measured = phrase.length * maxSize * .72;
  if (typeof document !== 'undefined') {
    const context = document.createElement('canvas').getContext('2d');
    if (context) {
      context.font = `${fontWeight} ${maxSize}px ${fontFamily}`;
      measured = context.measureText(phrase).width;
    }
  }
  return Math.min(maxSize, maxSize * Math.max(1, withinWidth) / Math.max(1, measured) * .96);
}

export function documentaryContrastInk(color) {
  const hex = String(color || '').replace('#', '');
  if (!/^[a-f0-9]{6}$/i.test(hex)) return '#FFFFFF';
  const values = [0, 2, 4].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4);
  return .2126 * values[0] + .7152 * values[1] + .0722 * values[2] > .179 ? '#101820' : '#FFFFFF';
}

// Footage changes beneath an unboxed overlay. Contrast with a hypothetical
// panel cannot justify dark text when that panel is not actually rendered.
export function documentaryOverlayInk(requested, background = null) {
  if (background && /^#[a-f0-9]{6}$/i.test(String(background))) return documentaryContrastInk(background);
  if (!/^#[a-f0-9]{6}$/i.test(String(requested || '')) || documentaryContrastInk(requested) === '#FFFFFF') return '#FFFFFF';
  return requested;
}
