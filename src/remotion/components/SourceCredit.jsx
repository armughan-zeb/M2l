import React from 'react';
import { useVideoConfig } from 'remotion';
import { approvedSourceCredit } from '../engine/MediaProvenance';

/** Verified media provenance is separate from editorial headline copy. */
export function SourceCredit({ scene = {} }) {
  const { width } = useVideoConfig();
  const credit = approvedSourceCredit(scene);
  if (!credit) return null;
  const scale = width / 1920;
  return <div style={{ position: 'absolute', top: '5%', right: '5%', zIndex: 20,
    width: 'fit-content', maxWidth: '32%', padding: `${5 * scale}px ${9 * scale}px`,
    background: 'rgba(5,10,15,.72)', color: '#F8FAFC', fontFamily: 'DM Sans, sans-serif',
    fontSize: Math.max(11, 23 * scale), lineHeight: 1.3, fontWeight: 600, pointerEvents: 'none' }}>
    {credit.label}
  </div>;
}
