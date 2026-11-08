import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { documentaryTypography, fittedDocumentarySize } from '../engine/DocumentaryTypography';
import { cueProgress, cueStartFrame, safeFadeOutRange } from '../engine/DirectorMotion';
import { SignedBars } from './SignedBars';

const clamp = (value) => Math.max(0, Math.min(1, value));
const clean = (value) => String(value ?? '').trim();
const numeric = (value) => {
  if (typeof value === 'number') return Number.isFinite(value) ? value : NaN;
  const text = clean(value).replace(/,/g, '');
  const match = text.match(/-?\d+(?:\.\d+)?/);
  if (!match) return NaN;
  const multiplier = /\btrillion\b|\d\s*T\b/i.test(text) ? 1e12
    : /\bbillion\b|\d\s*B\b/i.test(text) ? 1e9
      : /\bmillion\b|\d\s*M\b/i.test(text) ? 1e6
        : /\bthousand\b|\d\s*K\b/i.test(text) ? 1e3 : 1;
  return Number(match[0]) * multiplier;
};

const counted = (value, progress) => {
  const text = clean(value);
  const match = text.match(/^(.*?)(-?\d[\d,]*(?:\.\d+)?)(.*)$/);
  if (!match) return progress > .7 ? text : '';
  const raw = Number(match[2].replace(/,/g, ''));
  if (!Number.isFinite(raw)) return text;
  const decimals = (match[2].split('.')[1] || '').length;
  const current = raw * clamp(progress);
  const formatted = current.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  return `${match[1]}${formatted}${match[3]}`;
};

function ChartFrame({ children, scene, visualDNA, frame, fps }) {
  const palette = visualDNA.palette || {};
  const fonts = documentaryTypography(visualDNA);
  const duration = Math.max(1, Number(scene.duration_in_frames || Number(scene.duration || 4) * fps));
  const intro = spring({ frame, fps, config: { damping: 17, stiffness: 190, mass: .42 } });
  const outro = interpolate(frame, safeFadeOutRange(duration, Math.round(.2 * fps)), [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const topRule = scene.editorial_contract?.approved === true ? '22%' : '9%';
  return <AbsoluteFill style={{ color: palette.primary || '#f7f7f2', fontFamily: fonts.body, opacity: clamp(intro) * outro, overflow: 'hidden' }}>
    <div style={{ position: 'absolute', top: topRule, right: '7%', bottom: '10%', left: '7%', borderTop: `1px solid ${palette.accent || '#f2c94c'}`, borderBottom: `1px solid ${palette.border || 'rgba(255,255,255,.24)'}`, opacity: .5 }} />
    <div style={{ position: 'absolute', left: '7%', top: topRule, width: 56, height: 5, background: palette.accent || '#f2c94c' }} />
    {children({ palette, fonts })}
  </AbsoluteFill>;
}

/** Source-bound, open-canvas chart grammar used by director-led documentary beats. */
export function AdaptiveChart({ scene = {}, visualDNA = {}, props = {} }) {
  const frame = useCurrentFrame();
  const { fps, width } = useVideoConfig();
  const scale = width / 1920;
  if (props.no_data === true) return null;
  const variant = clean(props.variant || scene.primary_visual?.variant || 'editorial_bar').toLowerCase();
  const rawData = Array.isArray(props.data) ? props.data : (Array.isArray(props.values) ? props.values : []);
  const data = rawData.slice(0, 6).map((row) => ({
    label: clean(row?.label), value: clean(row?.value), number: numeric(row?.value),
  })).filter((row) => row.value && Number.isFinite(row.number));
  const before = props.before?.value != null ? { label: clean(props.before.label), value: clean(props.before.value), number: numeric(props.before.value) } : null;
  const after = props.after?.value != null ? { label: clean(props.after.label), value: clean(props.after.value), number: numeric(props.after.value) } : null;
  if (data.length >= 2 && data.some((row) => row.number < 0)) {
    const progress = cueProgress(scene, 'data', frame, fps, .08, .9);
    return <ChartFrame scene={scene} visualDNA={visualDNA} frame={frame} fps={fps}>
      {({ palette, fonts }) => <div style={{ position: 'absolute', left: '8%', right: '8%', top: '25%', bottom: '18%' }}>
        <SignedBars rows={data} progress={progress} color={palette.accent || '#F2C14E'} ink={palette.primary || '#F8F7F2'} fontFamily={fonts.body} />
      </div>}
    </ChartFrame>;
  }
  const seriesA = props.seriesA?.points || props.seriesA?.data || props.series1 || [];
  const seriesB = props.seriesB?.points || props.seriesB?.data || props.series2 || [];
  const hasDual = Array.isArray(seriesA) && Array.isArray(seriesB) && seriesA.length >= 2 && seriesB.length >= 2;
  if (data.length < 2 && !(before && after && Number.isFinite(before.number) && Number.isFinite(after.number)) && !hasDual) return null;

  const reveal = cueProgress(scene, 'data', frame, fps, .08, .85);
  const accent = visualDNA.palette?.accent || '#f2c94c';
  const ink = visualDNA.palette?.primary || '#f7f7f2';
  const muted = visualDNA.palette?.secondary || '#a8b0bb';
  const max = Math.max(1, ...data.map((row) => Math.max(0, row.number)));
  const chartVariant = hasDual ? 'multi_line_trend' : variant;
  const contentTop = scene.editorial_contract?.approved === true ? '28%' : '16%';
  const approvedValue = clean(scene.editorial_contract?.display_text).toLowerCase();
  const copyStart = cueStartFrame(scene, 'copy', fps, 0);
  const copyWords = approvedValue.split(/\s+/).filter(Boolean).length;
  const copyEnd = copyStart + Math.round(Math.min(copyWords > 3 ? 2.4 : 2.2, Math.max(1.3, .92 + copyWords * .32)) * fps);
  const valueOpacity = (value) => approvedValue && clean(value).toLowerCase() === approvedValue
    ? clamp((frame - copyEnd + 5) / 5)
    : 1;

  if (['before_after_delta', 'delta_comparison', 'metric_comparison'].includes(chartVariant) && before && after) {
    return <ChartFrame scene={scene} visualDNA={visualDNA} frame={frame} fps={fps}>{({ fonts }) => <>
      <div style={{ position: 'absolute', top: contentTop, right: '8%', bottom: '19%', left: '8%', display: 'grid', gridTemplateColumns: `1fr ${90 * scale}px 1fr`, alignItems: 'center', gap: chartVariant === 'distribution_histogram' ? 0 : 36 * scale }}>
        {[before, after].map((item, index) => {
          const itemReveal = clamp((reveal - index * .22) / .78);
          return <div key={index} style={{ minWidth: 0, opacity: itemReveal, transform: `translateY(${(1 - itemReveal) * 28}px)`, borderBottom: `4px solid ${index ? accent : muted}`, paddingBottom: 28 * scale }}>
            <div style={{ fontFamily: fonts.body, fontSize: 26 * scale, color: muted, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.08em' }}>{item.label}</div>
            <div style={{ fontFamily: fonts.display, fontSize: fittedDocumentarySize(item.value, {fontFamily: fonts.display, maxSize: 106 * scale, withinWidth: width * .30}), color: index ? accent : ink, lineHeight: 1.15, overflowWrap: 'break-word' }}>{counted(item.value, itemReveal)}</div>
          </div>;
        }).reduce((items, item, index) => index ? [...items, <div key="divider" style={{ height: 2, background: accent, transform: `scaleX(${reveal})` }} />, item] : [item], [])}
      </div>
    </>}</ChartFrame>;
  }

  if (['ranked_horizontal_bars', 'ranked-bars'].includes(chartVariant)) {
    return <ChartFrame scene={scene} visualDNA={visualDNA} frame={frame} fps={fps}>{({ fonts }) => <div style={{ position: 'absolute', top: contentTop, right: '9%', bottom: '16%', left: '9%', display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 23 * scale }}>
      {data.map((item, index) => {
        const itemReveal = clamp((reveal - index * .12) / .75);
        return <div key={`${item.label}-${index}`} style={{ display: 'grid', gridTemplateColumns: `${250 * scale}px 1fr ${150 * scale}px`, alignItems: 'center', gap: 26 * scale, minHeight: 74 * scale }}>
          <div style={{ color: ink, fontFamily: fonts.body, fontSize: 23 * scale, fontWeight: 700, overflowWrap: 'break-word' }}>{item.label}</div>
          <div style={{ height: 30 * scale, background: 'rgba(255,255,255,.09)' }}><div style={{ height: '100%', width: `${Math.max(2, item.number / max * 100 * itemReveal)}%`, background: index === 0 ? accent : ink, transformOrigin: 'left' }} /></div>
          <div style={{ color: index === 0 ? accent : ink, fontFamily: fonts.numbers, fontSize: fittedDocumentarySize(item.value, {fontFamily: fonts.numbers, maxSize: 28 * scale, withinWidth: 150 * scale}), fontWeight: 800, whiteSpace: 'nowrap', textAlign: 'right', opacity: valueOpacity(item.value) }}>{counted(item.value, itemReveal)}</div>
        </div>;
      })}
    </div>}</ChartFrame>;
  }

  if (['donut_distribution', 'donut_chart', 'donut'].includes(chartVariant)) {
    const total = data.reduce((sum, row) => sum + Math.max(0, row.number), 0);
    if (total <= 0) return null;
    const circumference = 2 * Math.PI * 150;
    let offset = 0;
    return <ChartFrame scene={scene} visualDNA={visualDNA} frame={frame} fps={fps}>{({ fonts }) => <div style={{ position: 'absolute', top: contentTop, right: '10%', bottom: '16%', left: '10%', display: 'flex', alignItems: 'center', justifyContent: 'space-around', gap: 70 }}>
      <svg viewBox="0 0 440 440" style={{ width: 550, height: 550, transform: 'rotate(-90deg)' }}>
        <circle cx="220" cy="220" r="150" fill="none" stroke="rgba(255,255,255,.1)" strokeWidth="54" />
        {data.map((item, index) => {
          const length = Math.max(0, item.number) / total * circumference;
          const start = offset;
          offset += length;
          return <circle key={index} cx="220" cy="220" r="150" fill="none" stroke={index === 0 ? accent : `rgba(247,247,242,${Math.max(.2, .82 - index * .13)})`} strokeWidth="54" strokeDasharray={`${length * reveal} ${circumference}`} strokeDashoffset={-start} />;
        })}
      </svg>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 22 }}>
        {data.map((item, index) => <div key={index} style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 26 * scale, borderBottom: '1px solid rgba(255,255,255,.18)', paddingBottom: 12, opacity: clamp((reveal - index * .1) / .8) }}>
          <span style={{ color: ink, fontFamily: fonts.body, fontSize: 23 * scale, fontWeight: 700 }}>{item.label}</span>
          <span style={{ color: index === 0 ? accent : ink, fontFamily: fonts.numbers, fontSize: 28 * scale, fontWeight: 800, opacity: valueOpacity(item.value) }}>{item.value}</span>
        </div>)}
      </div>
    </div>}</ChartFrame>;
  }

  if (['trend_line_with_markers', 'line', 'area_growth_fill', 'area_chart', 'area', 'multi_line_trend', 'comparative_lines', 'dual_line'].includes(chartVariant)) {
    const useAreaFill = ['area_growth_fill', 'area_chart', 'area'].includes(chartVariant) && !hasDual;
    const sources = hasDual ? [seriesA, seriesB] : [data];
    const lines = sources.map((source) => source.slice(0, 6).map((row, index, all) => ({
      x: 90 + index * 1070 / Math.max(1, all.length - 1),
      y: 490 - Math.max(0, numeric(row?.value)) / Math.max(1, ...sources.flat().map((entry) => Math.max(0, numeric(entry?.value) || 0))) * 390,
      label: clean(row?.label), value: clean(row?.value),
    }))).filter((line) => line.length >= 2 && line.every((point) => Number.isFinite(point.y)));
    if (!lines.length) return null;
    return <ChartFrame scene={scene} visualDNA={visualDNA} frame={frame} fps={fps}>{({ fonts }) => <svg viewBox="0 0 1260 650" style={{ position: 'absolute', top: contentTop, right: '10%', bottom: '12%', left: '10%', width: '80%', height: scene.editorial_contract?.approved === true ? '59%' : '70%', overflow: 'visible' }}>
      {[0, 1, 2, 3].map((index) => <line key={index} x1="70" x2="1190" y1={100 + index * 130} y2={100 + index * 130} stroke="rgba(255,255,255,.16)" strokeWidth="2" />)}
      {lines.map((line, seriesIndex) => {
        const path = line.map((point, index) => `${index ? 'L' : 'M'} ${point.x} ${point.y}`).join(' ');
        const areaPath = `${path} L ${line[line.length - 1].x} 560 L ${line[0].x} 560 Z`;
        return <g key={seriesIndex}>
          <defs><clipPath id={`director-chart-${scene.scene_index}-${seriesIndex}`}><rect x="0" y="0" width={1250 * reveal} height="650" /></clipPath></defs>
          {useAreaFill && <path d={areaPath} fill={accent} fillOpacity=".22" clipPath={`url(#director-chart-${scene.scene_index}-${seriesIndex})`} />}
          <path d={path} fill="none" stroke={seriesIndex ? ink : accent} strokeWidth="7" strokeLinecap="round" strokeLinejoin="round" clipPath={`url(#director-chart-${scene.scene_index}-${seriesIndex})`} />
          {line.map((point, index) => <g key={index} opacity={clamp((reveal - index / Math.max(1, line.length - 1) * .7) * 4)}>
            <circle cx={point.x} cy={point.y} r="10" fill={seriesIndex ? ink : accent} />
            {point.value && <text x={point.x} y={point.y - 26} fill={ink} textAnchor="middle" fontFamily={fonts.numbers} fontSize="24" fontWeight="700" opacity={valueOpacity(point.value)}>{point.value}</text>}
            {seriesIndex === 0 && point.label && <text x={point.x} y="550" fill={muted} textAnchor="middle" fontFamily={fonts.body} fontSize="22">{point.label}</text>}
          </g>)}
        </g>;
      })}
    </svg>}</ChartFrame>;
  }

  if (data.length < 2) return null;
  return <ChartFrame scene={scene} visualDNA={visualDNA} frame={frame} fps={fps}>{({ fonts }) => <div style={{ position: 'absolute', top: contentTop, right: '10%', bottom: '15%', left: '10%', display: 'flex', alignItems: 'end', justifyContent: 'space-evenly', borderBottom: '2px solid rgba(255,255,255,.42)', gap: chartVariant === 'distribution_histogram' ? 0 : 36 * scale }}>
    {data.map((item, index) => {
      const itemReveal = clamp((reveal - index * .1) / .72);
      return <div key={`${item.label}-${index}`} style={{ flex: 1, height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'end', alignItems: 'center', minWidth: 0 }}>
        <div style={{ color: index === data.length - 1 ? accent : ink, fontFamily: fonts.numbers, fontSize: 30 * scale, fontWeight: 800, marginBottom: 16, opacity: itemReveal * valueOpacity(item.value) }}>{counted(item.value, itemReveal)}</div>
        <div style={{ height: `${Math.max(2, item.number / max * 77 * itemReveal)}%`, width: chartVariant === 'distribution_histogram' ? '100%' : '67%', background: index === data.length - 1 ? accent : 'rgba(247,247,242,.78)', borderTop: `5px solid ${ink}`, transformOrigin: 'bottom' }} />
        <div style={{ color: muted, fontFamily: fonts.body, fontSize: 21 * scale, lineHeight: 1.15, textAlign: 'center', marginTop: 16, minHeight: 52 * scale, overflowWrap: 'break-word' }}>{item.label}</div>
      </div>;
    })}
  </div>}</ChartFrame>;
}
