export const FALLBACK_THEME = {
  id: 'vox_clarity',
  label: 'Clarity Explainer',
  palette: {
    background: '#0b1320',
    surface: '#16263b',
    primary: '#ffffff',
    accent: '#f2c14e',
    secondary: '#e7ebef',
    danger: '#ef6f6c'
  },
  font_family: 'Open Sans, Arial, sans-serif',
  display_font: 'Bebas Neue, Anton, Arial, sans-serif',
  caption: { color: '#ffffff', background: 'rgba(11,19,32,.82)', position: 'bottom', weight: 650, shadow: '0 2px 8px rgba(0,0,0,.78)' },
  safe_area: { x: 0.08, y: 0.08, width: 0.84, height: 0.84 }
};

export function mergeTheme(theme) {
  const source = theme || {};
  return {
    ...FALLBACK_THEME,
    ...source,
    palette: { ...FALLBACK_THEME.palette, ...(source.palette || {}) },
    caption: { ...FALLBACK_THEME.caption, ...(source.caption || {}) },
    safe_area: { ...FALLBACK_THEME.safe_area, ...(source.safe_area || {}) }
  };
}

export function rgba(hex, alpha = 1) {
  const value = String(hex || '').replace('#', '');
  if (!/^[0-9a-f]{6}$/i.test(value)) return hex || '#ffffff';
  const n = parseInt(value, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
}
