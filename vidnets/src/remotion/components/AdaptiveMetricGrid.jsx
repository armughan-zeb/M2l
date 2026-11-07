import React from 'react';
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { documentaryTypography, fittedDocumentarySize, documentaryContrastInk } from '../engine/DocumentaryTypography';
import { metricIsGrounded } from '../engine/NumericEvidence';
import { spokenPhraseFrame } from '../engine/DirectorMotion';

/** Narration-grounded values, without invented dataset marks or card filler. */
export function AdaptiveMetricGrid({ scene = {}, visualDNA = {}, props = {} }) {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const fonts = documentaryTypography(visualDNA);
  const narration = String(scene.narration || '').toLowerCase();
  const input = props.metrics || props.items;
  const metrics = (Array.isArray(input) ? input : []).filter(item => metricIsGrounded(item, narration)).slice(0, 4);
  if (!metrics.length) return null;
  const accent = visualDNA.palette?.accent || '#D32F2F';
  const ink = scene.visual_role === 'primary_explanation'
    ? documentaryContrastInk(visualDNA.palette?.background || '#111418') : '#F7F6F2';
  const scale = width / 1920;
  const available = (width * .82 - 48 * scale * (metrics.length - 1)) / metrics.length;
  return <AbsoluteFill style={{ padding: '16% 9%', color: ink, justifyContent: 'center' }}>
    <div style={{ display: 'grid', gridTemplateColumns: `repeat(${metrics.length}, minmax(0, 1fr))`, gap: 48 * scale }}>
      {metrics.map((item, index) => {
        const local = frame - (spokenPhraseFrame(scene, item.source_span, fps)
          ?? Math.round(Number(item.time_local ?? .15 + index * .45) * fps));
        const reveal = Math.max(0, Math.min(1, local / (.3 * fps)));
        const suffix = String(item.suffix || '').trim();
        const unit = suffix && /^[a-z]/i.test(suffix) ? ` ${suffix}` : suffix;
        const inferredDecimals = (String(item.value).split('.')[1] || '').length;
        const decimals = Math.max(0, Math.min(6, Number(item.decimals ?? inferredDecimals) || 0));
        const format = number => number.toLocaleString('en-US', {minimumFractionDigits: decimals, maximumFractionDigits: decimals});
        const value = `${item.prefix || ''}${format(Number(item.value))}${unit}`;
        const count = `${item.prefix || ''}${format(interpolate(reveal, [0, 1], [0, Number(item.value)]))}${unit}`;
        const label = String(item.label || '').trim();
        const safeLabel = label && narration.includes(label.toLowerCase()) ? label : '';
        return <div key={index} style={{ opacity: reveal, transform: `translateY(${(1-reveal)*12*scale}px)`, minWidth: 0, paddingTop: 16 * scale, borderTop: `${3 * scale}px solid ${accent}` }}>
          <div data-documentary-text="metric-value" style={{ fontFamily: fonts.display,
            fontSize: fittedDocumentarySize(value, { fontFamily: fonts.display, maxSize: Math.min(150 * scale, height * .19), withinWidth: available }),
            lineHeight: 1.4, whiteSpace: 'nowrap', color: ink }}>{count}</div>
          {safeLabel && <div data-documentary-text="metric-label" style={{ fontFamily: fonts.body, fontSize: fittedDocumentarySize(safeLabel, {fontFamily: fonts.body, maxSize: 32 * scale, withinWidth: available}), lineHeight: 1.4 }}>{safeLabel}</div>}
        </div>;
      })}
    </div>
  </AbsoluteFill>;
}
