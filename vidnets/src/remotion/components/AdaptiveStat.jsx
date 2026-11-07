import React from 'react';
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { documentaryTypography, documentaryContrastInk, fittedDocumentarySize } from '../engine/DocumentaryTypography';
import { cueStartFrame, hasApprovedDirectorCopy } from '../engine/DirectorMotion';
import { AdaptiveMetricGrid } from './AdaptiveMetricGrid';

const clean = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
const numeric = (value) => { const match = clean(value).replace(/,/g, '').match(/-?\d+(?:\.\d+)?/); return match ? Number(match[0]) : NaN; };
const clamp = (value) => Math.max(0, Math.min(1, value));

/** Responsive, grounded numbers. A brief metric never owns an entire media beat. */
export function AdaptiveStat({ scene = {}, visualDNA = {}, props = {} }) {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  if (Array.isArray(props.metrics) && props.metrics.length >= 2) return <AdaptiveMetricGrid scene={scene} visualDNA={visualDNA} props={props} />;
  const target = numeric(props.value ?? props.target);
  if (!Number.isFinite(target) || props.no_data) return null;
  const scale = width / 1920;
  const fonts = documentaryTypography(visualDNA);
  const accent = visualDNA.palette?.accent || '#c6a05a';
  const variant = clean(props.variant || scene.primary_visual?.variant || 'hero_counter_large').toLowerCase();
  const start = cueStartFrame(scene, 'data', fps, .1);
  const sceneDuration = Math.max(1, Number(scene.duration_in_frames || (scene.duration || 4) * fps));
  const brief = scene.visual_role !== 'primary_explanation';
  const duration = Math.min(sceneDuration - start, Math.round(fps * (brief ? 2.4 : 5)));
  const local = frame - start;
  if (local < 0 || local >= duration) return null;
  const enter = clamp(local / Math.max(1, .22 * fps));
  const opacity = enter * clamp((duration - local) / Math.max(1, .18 * fps));
  const count = clamp(local / Math.max(1, .4 * fps));
  const decimals = Math.max(0, Math.min(3, Number(props.decimals || 0)));
  const format = (value) => Number(value).toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  const approved = hasApprovedDirectorCopy(scene) ? clean(scene.editorial_contract?.display_text || scene.display_text || props.display_text) : '';
  const match = approved.match(/-?\d[\d,]*(?:\.\d+)?/);
  const rangeOrScore = approved.match(/\b\d[\d,]*\s*[-–—]\s*\d[\d,]*\b/);
  const prefix = clean(props.prefix) || (match && numeric(match[0]) === target ? approved.slice(0, match.index).match(/[$£€]\s*$/)?.[0] || '' : '');
  const suffix = clean(props.suffix);
  const value = rangeOrScore ? rangeOrScore[0].replace(/\s+/g, '') : `${prefix}${format(interpolate(count, [0, 1], [0, target]))}${suffix === '%' ? '%' : ''}`;
  const finalValue = rangeOrScore ? rangeOrScore[0].replace(/\s+/g, '') : `${prefix}${format(target)}${suffix === '%' ? '%' : ''}`;
  const unit = suffix === '%' ? '' : suffix;
  const rawLabel = clean(props.label);
  const label = /^(?:key metric|official record|record metric|narrated value [ab]|comparative analysis)$/i.test(rawLabel) ? '' : rawLabel;
  const context = clean(props.context || props.qualifier);
  const textSize = (text, maximum, boxWidth, family = fonts.display) => fittedDocumentarySize(text,
    { maxSize: Math.min(maximum * scale, height * .22), withinWidth: boxWidth, fontFamily: family });
  const shadow = '0 2px 3px rgba(0,0,0,.9), 0 6px 16px rgba(0,0,0,.65)';
  const position = clean(props.position || scene.text?.position || 'bottom-left');
  const centered = position === 'center' || variant === 'impact_slab' || (variant === 'hero_counter_large' && !brief) || variant === 'percentage_ring';
  const right = position.includes('right');
  const top = position.includes('top');
  const anchor = centered ? { left: '50%', top: '46%', transform: 'translate(-50%, -50%)' }
    : { ...(right ? { right: '8%' } : { left: '8%' }), ...(top ? { top: '12%' } : { bottom: '19%' }) };
  const boxWidth = width * (centered ? .76 : .60) - 32 * scale;
  const valueSize = textSize(finalValue, centered ? 160 : 108, boxWidth);
  const labelSize = textSize(label, 28, boxWidth, fonts.body);
  const noPanel = props.panel_style === 'none' || scene.text?.panel_style === 'none';
  const panel = !noPanel && ['kpi_metric_card', 'stat_with_context', 'corner_score_bug'].includes(variant);
  const ink = panel || brief ? '#F7F6F2' : documentaryContrastInk(visualDNA.palette?.background || '#111418');

  if (variant === 'percentage_ring' && suffix === '%' && target >= 0 && target <= 100) {
    const diameter = Math.min(width * .32, height * .58);
    const circumference = 2 * Math.PI * 132;
    return <AbsoluteFill style={{ opacity, justifyContent: 'center', alignItems: 'center', color: ink }}>
      <div style={{ width: diameter, height: diameter, position: 'relative' }}>
        <svg viewBox="0 0 320 320" width="100%" height="100%" style={{ transform: 'rotate(-90deg)' }}>
          <circle cx="160" cy="160" r="132" fill="none" stroke="#ffffff26" strokeWidth="8" />
          <circle cx="160" cy="160" r="132" fill="none" stroke={accent} strokeWidth="12" strokeDasharray={circumference} strokeDashoffset={circumference * (1 - count * target / 100)} />
        </svg>
        <div data-documentary-text="percentage" style={{ position: 'absolute', inset: '25% 12%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: fonts.display, fontSize: textSize(finalValue, 100, diameter * .74), whiteSpace: 'nowrap' }}>{value}</div>
      </div>
      {label && <div style={{ position: 'absolute', bottom: '16%', fontFamily: fonts.body, fontSize: labelSize, textShadow: shadow }}>{label}</div>}
    </AbsoluteFill>;
  }

  const from = numeric(props.fromVal ?? props.fromValue ?? props.before?.value);
  const to = numeric(props.toVal ?? props.toValue ?? props.after?.value);
  if (variant === 'delta_growth_badge' && Number.isFinite(from) && Number.isFinite(to)) {
    return <AbsoluteFill style={{ opacity, color: ink, justifyContent: 'center', alignItems: 'center' }}>
      <div style={{ width: '80%', display: 'grid', gridTemplateColumns: '1fr auto 1fr', gap: 32 * scale, alignItems: 'center' }}>
        {[from, to].map((number, i) => <React.Fragment key={i}>
          {i === 1 && <div style={{ color: accent, fontSize: 70 * scale }}>→</div>}
          <div data-documentary-text="delta" style={{ fontFamily: fonts.display, fontSize: textSize(format(number), 130, width * .32), whiteSpace: 'nowrap', textShadow: shadow, opacity: i ? clamp((local - .2 * fps) / (.2 * fps)) : enter }}>{format(number)}</div>
        </React.Fragment>)}
      </div>
      {label && <div style={{ position: 'absolute', bottom: '20%', fontFamily: fonts.body, fontSize: labelSize }}>{label}</div>}
    </AbsoluteFill>;
  }

  return <AbsoluteFill style={{ opacity, color: ink, pointerEvents: 'none' }}>
    <div style={{ position: 'absolute', ...anchor, maxWidth: '80%', width: 'fit-content', display: 'flex', flexDirection: 'column', alignItems: centered ? 'center' : right ? 'flex-end' : 'flex-start', gap: 6 * scale,
      ...(panel ? { padding: `${12 * scale}px ${18 * scale}px`, borderLeft: `${4 * scale}px solid ${accent}`, background: 'rgba(10,10,12,.88)' } : {}) }}>
      {label && <div style={{ fontFamily: fonts.body, fontSize: labelSize, fontWeight: 800, textShadow: shadow, lineHeight: 1.2 }}>{label}</div>}
      {props.qualifier && <div style={{ fontFamily: fonts.body, fontSize: textSize(context, 32, boxWidth, fonts.body), lineHeight: 1.3, color: ink, textShadow: shadow }}>{context}</div>}
      <div data-documentary-text="stat" style={{ fontFamily: fonts.display, fontSize: valueSize, lineHeight: 1.35, flexShrink: 0, fontWeight: 900, whiteSpace: 'nowrap', color: ink, textShadow: variant === 'impact_slab' ? undefined : shadow,
        ...(variant === 'impact_slab' ? { color: documentaryContrastInk(accent), background: accent, padding: `${4 * scale}px ${16 * scale}px ${8 * scale}px`, boxShadow: '0 4px 14px rgba(0,0,0,.45)' } : {}),
        transform: `translateY(${(1 - enter) * 12 * scale}px)` }}>{value}</div>
      {unit && <div style={{ fontFamily: fonts.display, fontSize: textSize(unit, 68, boxWidth), color: ink, lineHeight: 1.1, textShadow: shadow, whiteSpace: 'nowrap' }}>{unit}</div>}
      {context && !props.qualifier && <div style={{ fontFamily: fonts.body, fontSize: textSize(context, 22, boxWidth, fonts.body), lineHeight: 1.2, color: '#d2cfca', textShadow: shadow }}>{context}</div>}
    </div>
  </AbsoluteFill>;
}
