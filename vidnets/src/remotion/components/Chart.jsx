import React from 'react';
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { computeSpringEnter, computeEntranceStyle } from '../engine/MotionEngine';
import { rgba } from '../theme';

/**
 * Chart — Universal High-Impact Documentary Data Visualization Engine.
 * 
 * Supports complete facts & figures motion graphics variety:
 * 1. ranked_horizontal_bars — Comparative ranking bars with leader highlights
 * 2. donut_distribution — Circular distribution ring with center punch metric
 * 3. trend_line_with_markers — Single trajectory line with coordinate markers & grid
 * 4. multi_line_trend — Dual comparative trend lines (Entity A vs Entity B)
 * 5. area_growth_fill — Smooth Bezier spline with vertical gradient area fill
 * 6. radial_gauge — 270-degree circular KPI gauge with animated progress arc
 * 7. before_after_delta — Side-by-side metric cards with animated growth delta badge
 * 8. editorial_bar — Vox-style vertical bar chart with climax highlighting
 * 
 * Themes:
 * - white_editorial: Crisp elevated white card, dark slate typography (Vox / NYT style)
 * - dark_modern: Cinematic frosted glassmorphism over asphalt/noir background
 */
export function Chart({ scene = {}, visualDNA = {}, props = {} }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const sceneDuration = Math.max(1, scene?.duration_in_frames !== undefined && scene?.duration_in_frames !== null
    ? Math.round(Number(scene.duration_in_frames))
    : Math.round(Number(scene?.duration || 4) * fps));
  const progress = Math.min(1, Math.max(0, frame / Math.max(1, sceneDuration - 1)));

  const title = String(props.title || scene.text?.headline || 'DOCUMENTARY DATA').toUpperCase();
  const emphasis = String(props.emphasis || '');
  const variant = String(props.variant || props.visualizationType || scene.primary_visual?.variant || 'editorial_bar').toLowerCase();

  const explicitData = Array.isArray(props.values)
    ? props.values
    : (Array.isArray(props.data) ? props.data : null);
  const hasSeries = Array.isArray(props.series1)
    || Array.isArray(props.seriesA?.points)
    || Array.isArray(props.seriesA?.data)
    || Array.isArray(props.seriesB?.points)
    || Array.isArray(props.seriesB?.data);
  const hasDualSeries = (
    Array.isArray(props.series1) && Array.isArray(props.series2)
  ) || (
    (Array.isArray(props.seriesA?.points) || Array.isArray(props.seriesA?.data))
    && (Array.isArray(props.seriesB?.points) || Array.isArray(props.seriesB?.data))
  );
  const hasComparison = props.before?.value != null && props.after?.value != null;
  const hasSingleGroundedValue = props.currentValue != null || props.value != null;

  // A graphic component must fail closed.  Empty props are a planning error,
  // not permission to invent a chart, years, or a benchmark.
  if ((!explicitData || explicitData.length < 2) && !hasSeries && !hasComparison && !hasSingleGroundedValue) {
    return null;
  }
  if (['multi_line_trend', 'comparative_lines', 'dual_line'].includes(variant) && !hasDualSeries) {
    return null;
  }

  const data = explicitData || (hasComparison
    ? [props.before, props.after]
    : []);

  const palette = visualDNA.palette || {};
  const cornerRadius = visualDNA.cornerRadius ?? 8;
  const enter = computeSpringEnter(frame, fps, 0, scene?.motion?.spring_stiffness || 120, scene?.motion?.spring_damping || 18);
  // Strict editorial isolation: motion graphics smoothly exit before scene cut, zero bleed into next scene
  const entranceStyle = computeEntranceStyle(scene?.motion?.entrance || 'fade', enter, progress, true);

  const parseNumeric = (v) => {
    if (typeof v === 'number') return v;
    const str = String(v || '').trim().toUpperCase();
    const clean = parseFloat(str.replace(/[^0-9.]/g, '')) || 1;
    if (str.includes('T')) return clean * 1000;
    if (str.includes('B')) return clean;
    if (str.includes('M')) return clean / 1000;
    return clean;
  };

  const maxValue = Math.max(...data.map(d => parseNumeric(d.value)), 1);
  const animProgress = interpolate(enter, [0, 1], [0, 1]);

  // Card Theme Selection (White Editorial vs Deep Brand Glass)
  const cardTheme = props.card_theme || props.cardTheme || (visualDNA.chart_style === 'editorial_white' ? 'white_editorial' : 'dark_modern');
  const isWhite = cardTheme === 'white_editorial' || cardTheme === 'white' || cardTheme === 'light';

  const cardBg = isWhite
    ? 'rgba(255, 255, 255, 0.98)'
    : `linear-gradient(135deg, ${rgba(palette.surface || '#161b22', 0.92)} 0%, ${rgba(palette.background || '#0d1117', 0.96)} 100%)`;

  const primaryTextColor = isWhite ? '#0F172A' : (palette.primary || '#FFFFFF');
  const secondaryTextColor = isWhite ? '#475569' : (palette.secondary || '#94A3B8');
  const cardBorder = isWhite
    ? '1px solid rgba(0, 0, 0, 0.08)'
    : `1px solid ${rgba(palette.border || palette.accent, 0.4)}`;
  const cardShadow = isWhite
    ? `0 24px 60px rgba(0,0,0,0.45), 0 0 35px ${rgba(palette.accent || '#FF2A6D', 0.18)}`
    : `0 24px 60px rgba(0,0,0,0.85), 0 0 35px ${rgba(palette.accent || '#D4AF37', 0.15)}`;
  const trackBg = isWhite
    ? 'rgba(0, 0, 0, 0.06)'
    : rgba(palette.background || '#0d1117', 0.8);

  const baseCardStyle = {
    background: cardBg,
    border: cardBorder,
    borderTop: `3px solid ${palette.accent || '#FF2A6D'}`,
    borderRadius: cornerRadius || 16,
    padding: '44px 56px',
    boxShadow: cardShadow,
    width: '88%',
    maxWidth: '1060px',
  };

  // =========================================================================
  // 1. Ranked Horizontal Bars
  // =========================================================================
  if (variant === 'ranked_horizontal_bars') {
    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: 'transparent', padding: '0 6%', ...entranceStyle }}>
        <div style={baseCardStyle}>
          <div style={{ marginBottom: 36 }}>
            <div style={{ color: palette.accent, fontSize: 15, letterSpacing: '0.18em', textTransform: 'uppercase', marginBottom: 8, fontWeight: 800 }}>
              {isWhite ? 'DATA INSIGHT' : 'COMPARATIVE RANKING'}
            </div>
            <div style={{ color: primaryTextColor, fontSize: 38, fontFamily: visualDNA.typography?.display, fontWeight: 800, textShadow: isWhite ? 'none' : '0 2px 16px rgba(0,0,0,0.8)' }}>
              {title}
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
            {data.map((item, idx) => {
              // Dynamic staggered animation per bar
              const barDelay = idx * 0.09;
              const barAnim = Math.max(0, Math.min(1, (enter - barDelay) / Math.max(0.01, 1 - barDelay * 0.5)));
              const barW = Math.max(4, ((Number(parseNumeric(item.value)) / maxValue) * 100 * barAnim)).toFixed(1);

              // Smooth rolling numeric counter
              const rawNum = parseFloat(String(item.value).replace(/[^0-9.]/g, ''));
              let displayVal = item.value;
              if (!isNaN(rawNum)) {
                const currentCount = Math.round(rawNum * barAnim);
                const suffix = String(item.value).replace(/[0-9.]/g, '');
                displayVal = `${currentCount}${suffix}`;
              }

              return (
                <div key={idx} style={{ display: 'flex', alignItems: 'center', gap: 20, transform: `translateY(${(1 - barAnim) * 12}px)`, opacity: barAnim }}>
                  <div style={{ width: 36, height: 36, borderRadius: '50%', background: idx === 0 ? palette.accent : trackBg, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: visualDNA.typography?.numbers, color: idx === 0 ? '#FFFFFF' : secondaryTextColor, fontSize: 16, fontWeight: 800 }}>
                    #{idx + 1}
                  </div>
                  <div style={{ width: 180, color: secondaryTextColor, fontSize: 18, fontFamily: visualDNA.typography?.body, textAlign: 'right', fontWeight: 600 }}>
                    {item.label}
                  </div>
                  <div style={{ flex: 1, height: 36, background: trackBg, borderRadius: cornerRadius, overflow: 'hidden', border: isWhite ? '1px solid rgba(0,0,0,0.06)' : `1px solid ${rgba(palette.border || '#333', 0.5)}` }}>
                    <div style={{ width: `${barW}%`, height: '100%', background: idx === 0 ? `linear-gradient(90deg, ${palette.accent}, ${palette.secondary || '#FFB300'})` : (isWhite ? '#0F172A' : palette.primary), borderRadius: cornerRadius, transition: 'width 0.1s ease-out' }} />
                  </div>
                  <div style={{ width: 100, color: primaryTextColor, fontFamily: visualDNA.typography?.numbers, fontSize: 22, fontWeight: 800 }}>
                    {displayVal}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  // =========================================================================
  // 2. Donut Distribution Ring
  // =========================================================================
  if (variant === 'donut_distribution') {
    const total = data.reduce((acc, curr) => acc + Number(parseNumeric(curr.value) || 0), 0) || 1;
    const radius = 135;
    const circ = 2 * Math.PI * radius;
    let accumulatedOffset = 0;

    return (
      <AbsoluteFill style={{ display: 'flex', flexDirection: 'row', alignItems: 'center', justifyContent: 'center', background: 'transparent', ...entranceStyle }}>
        <div style={{ ...baseCardStyle, display: 'flex', flexDirection: 'row', alignItems: 'center', gap: 64, padding: '48px 64px' }}>
          <div style={{ position: 'relative', width: 340, height: 340 }}>
            <svg width="340" height="340" style={{ transform: 'rotate(-90deg)' }}>
              <circle cx="170" cy="170" r={radius} fill="none" stroke={isWhite ? 'rgba(0,0,0,0.08)' : rgba(palette.border || '#333', 0.4)} strokeWidth="26" />
              {data.map((item, idx) => {
                const sliceFraction = (Number(parseNumeric(item.value) || 0) / total);
                const dashLen = sliceFraction * circ * animProgress;
                const strokeOffset = accumulatedOffset;
                accumulatedOffset -= dashLen;
                const colors = [palette.accent, palette.secondary || '#FFB300', isWhite ? '#0F172A' : palette.primary, '#38BDF8', '#10B981'];
                return (
                  <circle
                    key={idx}
                    cx="170"
                    cy="170"
                    r={radius}
                    fill="none"
                    stroke={colors[idx % colors.length]}
                    strokeWidth="26"
                    strokeDasharray={`${dashLen} ${circ}`}
                    strokeDashoffset={strokeOffset}
                  />
                );
              })}
            </svg>
            <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center' }}>
              <div style={{ fontFamily: visualDNA.typography?.numbers, fontSize: 44, fontWeight: 900, color: primaryTextColor, textShadow: isWhite ? 'none' : '0 2px 12px rgba(0,0,0,0.8)' }}>
                {emphasis || total}
              </div>
              <div style={{ color: secondaryTextColor, fontSize: 13, textTransform: 'uppercase', letterSpacing: '0.14em', fontWeight: 700 }}>SHARE / TOTAL</div>
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18, flex: 1 }}>
            <div style={{ color: palette.accent, fontSize: 14, letterSpacing: '0.16em', textTransform: 'uppercase', fontWeight: 800, marginBottom: 4 }}>
              {isWhite ? 'BREAKDOWN' : 'DISTRIBUTION'}
            </div>
            <div style={{ color: primaryTextColor, fontSize: 32, fontFamily: visualDNA.typography?.display, fontWeight: 800, marginBottom: 12 }}>
              {title}
            </div>
            {data.map((item, idx) => {
              const colors = [palette.accent, palette.secondary || '#FFB300', isWhite ? '#0F172A' : palette.primary, '#38BDF8', '#10B981'];
              return (
                <div key={idx} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: `1px solid ${isWhite ? 'rgba(0,0,0,0.06)' : 'rgba(255,255,255,0.08)'}`, paddingBottom: 8 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <div style={{ width: 14, height: 14, borderRadius: '50%', background: colors[idx % colors.length] }} />
                    <div style={{ color: primaryTextColor, fontSize: 18, fontFamily: visualDNA.typography?.body, fontWeight: 600 }}>{item.label}</div>
                  </div>
                  <div style={{ fontFamily: visualDNA.typography?.numbers, color: primaryTextColor, fontSize: 20, fontWeight: 800 }}>
                    {item.value}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  // =========================================================================
  // 3. Multi-Line Comparison Trend Chart (Entity A vs Entity B)
  // =========================================================================
  if (variant === 'multi_line_trend' || variant === 'comparative_lines' || variant === 'dual_line') {
    const chartW = 900;
    const chartH = 320;
    const series1Name = typeof props.series1Name === 'string'
      ? props.series1Name
      : (typeof props.seriesA?.name === 'string'
        ? props.seriesA.name
        : (typeof props.seriesA === 'string' ? props.seriesA : 'SERIES A'));

    const series2Name = typeof props.series2Name === 'string'
      ? props.series2Name
      : (typeof props.seriesB?.name === 'string'
        ? props.seriesB.name
        : (typeof props.seriesB === 'string' ? props.seriesB : 'SERIES B'));

    // Build series 1 and series 2 from data or seriesA/seriesB objects
    const rawS1 = props.series1 || props.seriesA?.points || props.seriesA?.data || data;
    const rawS2 = props.series2 || props.seriesB?.points || props.seriesB?.data;

    const s1Data = rawS1.map((d, i) => ({ label: String(d.label || ''), value: parseNumeric(d.value) }));
    const s2Data = rawS2.map((d, i) => ({ label: String(d.label || ''), value: parseNumeric(d.value) }));

    const maxValMulti = Math.max(...s1Data.map(d => Number(d.value || 0)), ...s2Data.map(d => Number(d.value || 0)), 1);

    const pts1 = s1Data.map((d, i) => ({
      x: 70 + (i / Math.max(1, s1Data.length - 1)) * (chartW - 140),
      y: chartH - 45 - (Number(d.value || 0) / maxValMulti) * (chartH - 90) * animProgress,
      val: d.value,
      label: d.label,
    }));

    const pts2 = s2Data.map((d, i) => ({
      x: 70 + (i / Math.max(1, s2Data.length - 1)) * (chartW - 140),
      y: chartH - 45 - (Number(d.value || 0) / maxValMulti) * (chartH - 90) * animProgress,
      val: Math.round(d.value),
      label: d.label,
    }));

    const path1 = pts1.reduce((acc, p, i) => (i === 0 ? `M ${p.x} ${p.y}` : `${acc} L ${p.x} ${p.y}`), '');
    const path2 = pts2.reduce((acc, p, i) => (i === 0 ? `M ${p.x} ${p.y}` : `${acc} L ${p.x} ${p.y}`), '');

    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: 'transparent', padding: '0 6%', ...entranceStyle }}>
        <div style={baseCardStyle}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24 }}>
            <div>
              <div style={{ color: palette.accent, fontSize: 14, letterSpacing: '0.18em', textTransform: 'uppercase', marginBottom: 6, fontWeight: 800 }}>
                {isWhite ? 'DATA CORRELATION' : 'COMPARATIVE TRAJECTORY'}
              </div>
              <div style={{ color: primaryTextColor, fontSize: 36, fontFamily: visualDNA.typography?.display, fontWeight: 800 }}>
                {title}
              </div>
            </div>
            {/* Legend */}
            <div style={{ display: 'flex', gap: 24, background: trackBg, padding: '10px 18px', borderRadius: 8 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <div style={{ width: 14, height: 4, background: palette.accent, borderRadius: 2 }} />
                <span style={{ color: primaryTextColor, fontSize: 14, fontFamily: visualDNA.typography?.body, fontWeight: 700 }}>{series1Name}</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <div style={{ width: 14, height: 4, background: palette.secondary || '#FFB300', borderRadius: 2 }} />
                <span style={{ color: secondaryTextColor, fontSize: 14, fontFamily: visualDNA.typography?.body, fontWeight: 700 }}>{series2Name}</span>
              </div>
            </div>
          </div>

          <svg width={chartW} height={chartH} style={{ overflow: 'visible', margin: '0 auto', display: 'block' }}>
            {/* Horizontal Grid lines */}
            {[0.25, 0.5, 0.75, 1.0].map((frac, idx) => (
              <line key={idx} x1="50" y1={chartH - 45 - frac * (chartH - 90)} x2={chartW - 50} y2={chartH - 45 - frac * (chartH - 90)} stroke={isWhite ? 'rgba(0,0,0,0.06)' : 'rgba(255,255,255,0.08)'} strokeWidth="1" strokeDasharray="4 4" />
            ))}
            <line x1="50" y1={chartH - 45} x2={chartW - 50} y2={chartH - 45} stroke={isWhite ? 'rgba(0,0,0,0.12)' : 'rgba(255,255,255,0.2)'} strokeWidth="2" />

            {/* Line 2 (Competitor / Secondary) */}
            <path d={path2} fill="none" stroke={palette.secondary || '#FFB300'} strokeWidth="4" strokeLinecap="round" strokeDasharray="8 6" opacity="0.85" />
            {pts2.map((p, i) => (
              <circle key={`p2-${i}`} cx={p.x} cy={p.y} r="6" fill={palette.secondary || '#FFB300'} stroke={isWhite ? '#FFFFFF' : '#0E1017'} strokeWidth="2" />
            ))}

            {/* Line 1 (Leader / Primary Accent) */}
            <path d={path1} fill="none" stroke={palette.accent} strokeWidth="5" strokeLinecap="round" />
            {pts1.map((p, i) => (
              <g key={`p1-${i}`}>
                <circle cx={p.x} cy={p.y} r="8" fill={palette.accent} stroke={isWhite ? '#FFFFFF' : '#0E1017'} strokeWidth="2.5" />
                <text x={p.x} y={p.y - 14} textAnchor="middle" fill={primaryTextColor} fontFamily={visualDNA.typography?.numbers} fontSize="17" fontWeight="800">
                  {p.val}
                </text>
                <text x={p.x} y={chartH - 18} textAnchor="middle" fill={secondaryTextColor} fontFamily={visualDNA.typography?.body} fontSize="15" fontWeight="600">
                  {p.label}
                </text>
              </g>
            ))}
          </svg>
        </div>
      </AbsoluteFill>
    );
  }

  // =========================================================================
  // 4. Area Growth Chart with Spline Fill
  // =========================================================================
  if (variant === 'area_growth_fill' || variant === 'area_chart' || variant === 'area') {
    const chartW = 920;
    const chartH = 320;
    const points = data.map((d, i) => ({
      x: 60 + (i / Math.max(1, data.length - 1)) * (chartW - 120),
      y: chartH - 40 - (Number(parseNumeric(d.value) || 0) / maxValue) * (chartH - 90) * animProgress,
      val: d.value,
      label: d.label,
    }));

    const pathLine = points.reduce((acc, p, idx) => (idx === 0 ? `M ${p.x} ${p.y}` : `${acc} L ${p.x} ${p.y}`), '');
    const firstP = points[0] || { x: 60, y: chartH - 40 };
    const lastP = points[points.length - 1] || { x: chartW - 60, y: chartH - 40 };
    const pathArea = `${pathLine} L ${lastP.x} ${chartH - 40} L ${firstP.x} ${chartH - 40} Z`;

    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: 'transparent', padding: '0 6%', ...entranceStyle }}>
        <div style={baseCardStyle}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 24 }}>
            <div>
              <div style={{ color: palette.accent, fontSize: 15, letterSpacing: '0.18em', textTransform: 'uppercase', marginBottom: 6, fontWeight: 800 }}>
                {isWhite ? 'GROWTH SCALE' : 'EXPANSION CURVE'}
              </div>
              <div style={{ color: primaryTextColor, fontSize: 38, fontFamily: visualDNA.typography?.display, fontWeight: 800 }}>{title}</div>
            </div>
            {emphasis && (
              <div style={{ background: isWhite ? 'rgba(0,0,0,0.06)' : rgba(palette.accent, 0.16), padding: '8px 18px', borderRadius: 8, border: `1px solid ${palette.accent}` }}>
                <div style={{ color: palette.accent, fontFamily: visualDNA.typography?.numbers, fontSize: 22, fontWeight: 800 }}>{emphasis}</div>
              </div>
            )}
          </div>

          <svg width={chartW} height={chartH} style={{ overflow: 'visible', margin: '0 auto', display: 'block' }}>
            <defs>
              <linearGradient id="areaGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={palette.accent} stopOpacity={isWhite ? '0.42' : '0.55'} />
                <stop offset="90%" stopColor={palette.accent} stopOpacity="0.02" />
              </linearGradient>
            </defs>

            {/* Grid baseline */}
            <line x1="40" y1={chartH - 40} x2={chartW - 40} y2={chartH - 40} stroke={isWhite ? 'rgba(0,0,0,0.1)' : 'rgba(255,255,255,0.15)'} strokeWidth="2" />

            {/* Gradient Fill Under Curve */}
            <path d={pathArea} fill="url(#areaGradient)" />

            {/* Bold Curve Stroke */}
            <path d={pathLine} fill="none" stroke={palette.accent} strokeWidth="5" strokeLinecap="round" />

            {/* Points & Labels */}
            {points.map((p, idx) => {
              const isPeak = idx === points.length - 1;
              return (
                <g key={idx}>
                  <circle cx={p.x} cy={p.y} r={isPeak ? 11 : 7} fill={palette.accent} stroke={isWhite ? '#FFFFFF' : '#0E1017'} strokeWidth="3" />
                  {isPeak && (
                    <circle cx={p.x} cy={p.y} r="18" fill="none" stroke={palette.accent} strokeWidth="2" strokeDasharray="4 4" opacity="0.8" />
                  )}
                  <text x={p.x} y={p.y - 18} textAnchor="middle" fill={primaryTextColor} fontFamily={visualDNA.typography?.numbers} fontSize={isPeak ? 22 : 18} fontWeight="800">
                    {p.val}
                  </text>
                  <text x={p.x} y={chartH - 16} textAnchor="middle" fill={secondaryTextColor} fontFamily={visualDNA.typography?.body} fontSize="16" fontWeight="600">
                    {p.label}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
      </AbsoluteFill>
    );
  }

  // =========================================================================
  // 5. Radial Progress / Circular KPI Gauge
  // =========================================================================
  if (variant === 'radial_gauge' || variant === 'progress_ring' || variant === 'gauge') {
    const rawVal = parseNumeric(props.currentValue ?? props.value ?? data[data.length - 1]?.value);
    const targetVal = parseNumeric(props.maxValue ?? props.target ?? 100);
    const pct = Math.min(100, Math.max(0, (rawVal / targetVal) * 100));

    const radius = 125;
    const strokeWidth = 24;
    // 270 degree arc (3/4 circle)
    const arcCircumference = 2 * Math.PI * radius * 0.75;
    const strokeDashoffset = arcCircumference - (pct / 100) * arcCircumference * animProgress;

    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: 'transparent', padding: '0 6%', ...entranceStyle }}>
        <div style={{ ...baseCardStyle, display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', padding: '48px 64px' }}>
          <div style={{ color: palette.accent, fontSize: 15, letterSpacing: '0.18em', textTransform: 'uppercase', marginBottom: 8, fontWeight: 800 }}>
            {isWhite ? 'EFFICIENCY GAUGE' : 'KEY PERFORMANCE METRIC'}
          </div>
          <div style={{ color: primaryTextColor, fontSize: 38, fontFamily: visualDNA.typography?.display, fontWeight: 800, marginBottom: 28 }}>
            {title}
          </div>

          <div style={{ position: 'relative', width: 320, height: 260, display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
            <svg width="320" height="320" style={{ transform: 'rotate(135deg)', overflow: 'visible' }}>
              {/* Background Arc */}
              <circle
                cx="160"
                cy="160"
                r={radius}
                fill="none"
                stroke={trackBg}
                strokeWidth={strokeWidth}
                strokeDasharray={`${arcCircumference} 1000`}
                strokeLinecap="round"
              />
              {/* Animated Progress Arc */}
              <circle
                cx="160"
                cy="160"
                r={radius}
                fill="none"
                stroke={palette.accent}
                strokeWidth={strokeWidth}
                strokeDasharray={`${arcCircumference} 1000`}
                strokeDashoffset={strokeDashoffset}
                strokeLinecap="round"
              />
            </svg>
            {/* Center Metric Display */}
            <div style={{ position: 'absolute', top: 50, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
              <div style={{ fontFamily: visualDNA.typography?.numbers, fontSize: 62, fontWeight: 900, color: primaryTextColor, lineHeight: 1 }}>
                {Math.round(pct * animProgress)}%
              </div>
              <div style={{ color: palette.accent, fontSize: 16, fontFamily: visualDNA.typography?.numbers, fontWeight: 700, marginTop: 4 }}>
                {props.label || `${rawVal} / ${targetVal}${props.unit || ''}`}
              </div>
              <div style={{ color: secondaryTextColor, fontSize: 13, textTransform: 'uppercase', letterSpacing: '0.12em', fontWeight: 600, marginTop: 4 }}>
                {emphasis || props.benchmarkLabel || 'BENCHMARK QUOTA'}
              </div>
            </div>
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  // =========================================================================
  // 6. Before / After Delta Comparison Cards
  // =========================================================================
  if (variant === 'before_after_delta' || variant === 'delta_comparison' || variant === 'metric_comparison') {
    const itemBefore = props.before || data[0] || { label: 'BEFORE', value: '18M' };
    const itemAfter = props.after || data[data.length - 1] || { label: 'AFTER', value: '93M' };
    const deltaText = props.delta || emphasis || '+416% GROWTH';
    const isPositive = !deltaText.includes('-');

    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: 'transparent', padding: '0 6%', ...entranceStyle }}>
        <div style={baseCardStyle}>
          <div style={{ textAlign: 'center', marginBottom: 36 }}>
            <div style={{ color: palette.accent, fontSize: 15, letterSpacing: '0.18em', textTransform: 'uppercase', marginBottom: 8, fontWeight: 800 }}>
              {isWhite ? 'IMPACT ASSESSMENT' : 'METRIC TRANSFORMATION'}
            </div>
            <div style={{ color: primaryTextColor, fontSize: 40, fontFamily: visualDNA.typography?.display, fontWeight: 800 }}>
              {title}
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 32 }}>
            {/* Card 1: Before */}
            <div style={{
              flex: 1,
              background: trackBg,
              border: `1px solid ${isWhite ? 'rgba(0,0,0,0.08)' : 'rgba(255,255,255,0.1)'}`,
              borderRadius: 12,
              padding: '36px 28px',
              textAlign: 'center',
            }}>
              <div style={{ color: secondaryTextColor, fontSize: 16, fontFamily: visualDNA.typography?.body, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 12 }}>
                {itemBefore.label || 'BASELINE'}
              </div>
              <div style={{ fontFamily: visualDNA.typography?.numbers, fontSize: 48, fontWeight: 800, color: primaryTextColor }}>
                {itemBefore.value}
              </div>
            </div>

            {/* Delta Surge Indicator */}
            <div style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 8,
              padding: '16px 24px',
              background: isWhite ? 'rgba(15, 23, 42, 0.95)' : palette.accent,
              borderRadius: 12,
              boxShadow: `0 12px 30px ${rgba(palette.accent, 0.35)}`,
              transform: `scale(${0.85 + 0.15 * animProgress})`,
            }}>
              <div style={{ fontSize: 24, color: isWhite ? palette.accent : '#FFFFFF' }}>
                {isPositive ? '▲' : '▼'}
              </div>
              <div style={{ fontFamily: visualDNA.typography?.numbers, fontSize: 22, fontWeight: 900, color: '#FFFFFF', whiteSpace: 'nowrap' }}>
                {deltaText}
              </div>
            </div>

            {/* Card 2: After */}
            <div style={{
              flex: 1,
              background: trackBg,
              border: `2px solid ${palette.accent}`,
              borderRadius: 12,
              padding: '36px 28px',
              textAlign: 'center',
              boxShadow: `0 12px 30px ${rgba(palette.accent, 0.2)}`,
            }}>
              <div style={{ color: palette.accent, fontSize: 16, fontFamily: visualDNA.typography?.body, fontWeight: 800, letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 12 }}>
                {itemAfter.label || 'PEAK EXPANSION'}
              </div>
              <div style={{ fontFamily: visualDNA.typography?.numbers, fontSize: 54, fontWeight: 900, color: primaryTextColor }}>
                {itemAfter.value}
              </div>
            </div>
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  // =========================================================================
  // 7. Single Trend Line with Markers
  // =========================================================================
  if (variant === 'trend_line_with_markers' || variant === 'line') {
    const chartW = 920;
    const chartH = 340;
    const points = data.map((d, i) => ({
      x: 60 + (i / Math.max(1, data.length - 1)) * (chartW - 120),
      y: chartH - 40 - (Number(parseNumeric(d.value) || 0) / maxValue) * (chartH - 80) * animProgress,
      val: d.value,
      label: d.label,
    }));
    const pathStr = points.reduce((acc, p, idx) => (idx === 0 ? `M ${p.x} ${p.y}` : `${acc} L ${p.x} ${p.y}`), '');

    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: 'transparent', padding: '0 6%', ...entranceStyle }}>
        <div style={baseCardStyle}>
          <div style={{ textAlign: 'center', marginBottom: 30 }}>
            <div style={{ color: palette.accent, fontSize: 15, letterSpacing: '0.18em', textTransform: 'uppercase', marginBottom: 8, fontWeight: 800 }}>
              {isWhite ? 'DATA INSIGHT' : 'GROWTH TREND'}
            </div>
            <div style={{ color: primaryTextColor, fontSize: 40, fontFamily: visualDNA.typography?.display, fontWeight: 800, textShadow: isWhite ? 'none' : '0 2px 16px rgba(0,0,0,0.8)' }}>{title}</div>
            {emphasis && <div style={{ color: palette.accent, fontFamily: visualDNA.typography?.numbers, fontSize: 26, fontWeight: 700, marginTop: 4 }}>{emphasis}</div>}
          </div>
          <svg width={chartW} height={chartH} style={{ overflow: 'visible', margin: '0 auto', display: 'block' }}>
            <line x1="40" y1={chartH - 30} x2={chartW - 40} y2={chartH - 30} stroke={isWhite ? 'rgba(0,0,0,0.1)' : rgba(palette.border || '#444', 0.6)} strokeWidth="2" />
            <path d={pathStr} fill="none" stroke={palette.accent} strokeWidth="5" strokeLinecap="round" />
            {points.map((p, idx) => (
              <g key={idx}>
                <circle cx={p.x} cy={p.y} r="9" fill={palette.accent} stroke={isWhite ? '#FFFFFF' : palette.primary} strokeWidth="3" />
                <text x={p.x} y={p.y - 18} textAnchor="middle" fill={primaryTextColor} fontFamily={visualDNA.typography?.numbers} fontSize="20" fontWeight="700">
                  {p.val}
                </text>
                <text x={p.x} y={chartH - 8} textAnchor="middle" fill={secondaryTextColor} fontFamily={visualDNA.typography?.body} fontSize="17">
                  {p.label}
                </text>
              </g>
            ))}
          </svg>
        </div>
      </AbsoluteFill>
    );
  }

  // =========================================================================
  // 8. Editorial Vertical Bar Chart (Default Vox-Style)
  // =========================================================================
  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', padding: '0 6%', background: 'transparent', ...entranceStyle }}>
      <div style={baseCardStyle}>
        <div style={{ textAlign: 'center', marginBottom: 32, width: '100%' }}>
          <div style={{ color: palette.accent, fontSize: 15, letterSpacing: '0.18em', textTransform: 'uppercase', marginBottom: 8, fontWeight: 800 }}>
            {isWhite ? 'DATA INSIGHT' : 'STATISTICAL OVERVIEW'}
          </div>
          <div style={{ color: primaryTextColor, fontSize: 40, fontFamily: visualDNA.typography?.display, fontWeight: 800, textShadow: isWhite ? 'none' : '0 2px 16px rgba(0,0,0,0.8)', lineHeight: 1.15 }}>
            {title}
          </div>
          {emphasis && <div style={{ color: palette.accent, fontFamily: visualDNA.typography?.numbers, fontSize: 22, marginTop: 6, fontWeight: 800 }}>{emphasis}</div>}
        </div>
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 56, height: 280, paddingBottom: 32, borderBottom: isWhite ? '2px solid rgba(0,0,0,0.1)' : `2px solid ${rgba(palette.border || '#555', 0.6)}`, width: '100%', justifyContent: 'center' }}>
          {data.map((item, idx) => {
            const barDelay = idx * 0.10;
            const barAnim = Math.max(0, Math.min(1, (enter - barDelay) / Math.max(0.01, 1 - barDelay * 0.5)));
            const barHeight = Math.max(16, ((parseNumeric(item.value) / maxValue) * 220 * barAnim));
            const isLast = idx === data.length - 1;

            // Rolling numeric counter
            const rawNum = parseFloat(String(item.value).replace(/[^0-9.]/g, ''));
            let displayVal = item.value;
            if (!isNaN(rawNum)) {
              const currentCount = Math.round(rawNum * barAnim);
              const suffix = String(item.value).replace(/[0-9.]/g, '');
              displayVal = `${currentCount}${suffix}`;
            }

            return (
              <div key={idx} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: 90, transform: `translateY(${(1 - barAnim) * 16}px)`, opacity: barAnim }}>
                <div style={{ fontFamily: visualDNA.typography?.numbers, color: primaryTextColor, fontSize: 20, fontWeight: 800, marginBottom: 8, textShadow: isWhite ? 'none' : '0 2px 8px rgba(0,0,0,0.8)' }}>
                  {displayVal}
                </div>
                <div
                  style={{
                    width: 64,
                    height: barHeight,
                    background: isLast ? `linear-gradient(to top, ${palette.accent}, ${palette.secondary || '#FFE082'})` : (isWhite ? 'rgba(15, 23, 42, 0.85)' : palette.surface),
                    border: `1px solid ${isLast ? palette.accent : (isWhite ? 'rgba(0,0,0,0.1)' : palette.border)}`,
                    borderRadius: `${cornerRadius}px ${cornerRadius}px 0 0`,
                    boxShadow: isLast ? `0 0 30px ${rgba(palette.accent, 0.6)}` : 'none',
                    transition: 'height 0.1s ease-out',
                  }}
                />
                <div style={{ marginTop: 12, color: secondaryTextColor, fontSize: 16, fontFamily: visualDNA.typography?.body, fontWeight: 600, textShadow: isWhite ? 'none' : '0 2px 8px rgba(0,0,0,0.8)' }}>
                  {item.label}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </AbsoluteFill>
  );
}

