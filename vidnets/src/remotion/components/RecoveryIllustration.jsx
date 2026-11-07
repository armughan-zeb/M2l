import React from 'react';

/**
 * RecoveryIllustration.jsx (Deprecated wireframes replaced with archival seal).
 * Replaced crude car/brake/battery wireframes with high-end documentary archival emblem.
 */
export function RecoveryIllustration({ kind, accent = '#D69E2E', ink = '#FFFFFF', progress = 1 }) {
  return (
    <svg data-recovery-illustration={kind} viewBox="0 0 300 220" style={{ width: '100%', height: '100%', overflow: 'visible' }}>
      <g opacity={0.25 + 0.75 * progress}>
        {/* Subtle documentary archival crest / emblem */}
        <circle cx="150" cy="110" r="70" fill="none" stroke={ink} strokeWidth="1.5" strokeDasharray="4 4" opacity="0.4" />
        <circle cx="150" cy="110" r="54" fill="none" stroke={accent} strokeWidth="2" opacity="0.7" />
        <circle cx="150" cy="110" r="48" fill="none" stroke={ink} strokeWidth="1" opacity="0.3" />
        {/* Cardinal crosshairs */}
        <line x1="150" y1="28" x2="150" y2="44" stroke={accent} strokeWidth="2" />
        <line x1="150" y1="176" x2="150" y2="192" stroke={accent} strokeWidth="2" />
        <line x1="68" y1="110" x2="84" y2="110" stroke={accent} strokeWidth="2" />
        <line x1="216" y1="110" x2="232" y2="110" stroke={accent} strokeWidth="2" />
        {/* Subtle document/evidence glyph */}
        <rect x="135" y="92" width="30" height="36" rx="2" fill="none" stroke={ink} strokeWidth="2" />
        <line x1="141" y1="102" x2="159" y2="102" stroke={accent} strokeWidth="2" />
        <line x1="141" y1="110" x2="159" y2="110" stroke={ink} strokeWidth="1.5" opacity="0.6" />
        <line x1="141" y1="118" x2="153" y2="118" stroke={ink} strokeWidth="1.5" opacity="0.6" />
      </g>
    </svg>
  );
}
