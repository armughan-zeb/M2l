import React from 'react';
import { signedChartDomain } from '../engine/ChartGeometry';

export function SignedBars({ rows, progress, color, ink, fontFamily = 'DM Sans, sans-serif' }) {
  const domain = signedChartDomain(rows.map((row) => row.number));
  return <div style={{ width: '100%', height: '100%', display: 'grid', gridTemplateRows: `repeat(${rows.length}, minmax(0, 1fr))`, gap: 16, fontFamily, color: ink }}>
    {rows.map((row, index) => {
      const reveal = Math.max(0, Math.min(1, progress * 1.5 - index * .12));
      const target = domain.position(row.number);
      const end = domain.zero + (target - domain.zero) * reveal;
      return <div key={index} style={{ display: 'grid', gridTemplateColumns: '24% 1fr 18%', gap: 20, alignItems: 'center', minHeight: 0 }}>
        <div style={{ fontSize: 26, fontWeight: 750, lineHeight: 1.2, overflowWrap: 'break-word' }}>{row.label}</div>
        <div style={{ position: 'relative', height: '68%', minHeight: 16, borderBottom: `1px solid ${ink}33` }}>
          <div style={{ position: 'absolute', left: `${domain.zero * 100}%`, top: -8, bottom: -8, width: 2, background: ink, opacity: .6 }} />
          <div style={{ position: 'absolute', left: `${Math.min(domain.zero, end) * 100}%`, width: `${Math.abs(end - domain.zero) * 100}%`, top: 0, bottom: 0, background: color, opacity: row.number < 0 ? .72 : 1 }} />
        </div>
        <div style={{ fontSize: 28, fontWeight: 800, lineHeight: 1.15, textAlign: 'right', opacity: reveal }}>{row.value}</div>
      </div>;
    })}
  </div>;
}
