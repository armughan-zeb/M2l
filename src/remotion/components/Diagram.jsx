import React from 'react';
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { computeSpringEnter, computeEntranceStyle } from '../engine/MotionEngine';
import { rgba } from '../theme';
import { cueProgress } from '../engine/DirectorMotion';

const clean = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
const editorialText = (value) => /^(?:SYSTEM PROCESS|PROCESS MAP|SIGNAL LOG|GROUNDED SIGNAL)$/i.test(clean(value)) ? '' : clean(value);

/**
 * Open, source-grounded process graphics for documentary compositions.
 * Renders editorial sequence and cause-and-effect cards over the cinematic footage.
 */
export function Diagram({ scene = {}, visualDNA = {}, props = {} }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const duration = Math.max(1, Number(scene.duration_in_frames || Math.round(Number(scene.duration || 4) * fps)));
  const progress = Math.min(1, Math.max(0, frame / Math.max(1, duration - 1)));
  const enter = computeSpringEnter(frame, fps, 0, scene?.motion?.spring_stiffness || 150, scene?.motion?.spring_damping || 19);
  const stepProgress = cueProgress(scene, 'steps', frame, fps, 0.08, 0.9);
  const entranceStyle = computeEntranceStyle(scene?.motion?.entrance || 'fade', enter, progress, true);
  const palette = visualDNA.palette || {};
  const typography = visualDNA.typography || {};
  const ink = palette.primary || '#F8FAFC';
  const muted = palette.secondary || '#CBD5E1';
  const accent = palette.accent || '#E6B84A';

  const copyContract = scene.editorial_contract || scene.primary_visual?.props?.editorial_contract || {};
  const directedScene = scene.director_treatment_enabled === true
    || Object.prototype.hasOwnProperty.call(scene.text || {}, 'overlay_policy');
  const approvedTitle = copyContract.approved === true
    && Number(copyContract.version) >= 4
    && typeof copyContract.display_text === 'string'
    && scene.text?.position !== 'none'
    ? editorialText(copyContract.display_text)
    : '';
  const title = directedScene
    ? approvedTitle
    : editorialText(props.display_text)
      || editorialText(props.title)
      || editorialText(props.text)
      || clean(scene.text?.headline || scene.text?.focus || '');

  const rawSteps = Array.isArray(props.steps) ? props.steps : [];
  const steps = rawSteps.map((step) => {
    const rawStepTitle = clean(step?.title || step?.label || '');
    const phaseMatch = rawStepTitle.match(/^PHASE\s+(\d+)$/i);
    const cleanStepTitle = rawStepTitle.replace(/^PHASE\s+/i, '');
    return {
      // A generated ordinal is scaffolding, not the information viewers need.
      title: phaseMatch ? '' : cleanStepTitle,
      description: clean(step?.desc || step?.description || ''),
    };
  }).filter((step) => step.title || step.description).slice(0, 4);

  const variant = String(props.variant || scene.primary_visual?.variant || 'step_by_step_process').toLowerCase();
  const isSingleMessage = ['terminal_log', 'signal_console', 'terminal-log'].includes(variant);
  const isCause = variant.includes('cause');
  const defaultTitle = directedScene
    ? ''
    : (isCause ? 'CAUSE & EFFECT' : (clean(scene.narration) ? (clean(scene.text?.focus) || clean(scene.text?.headline) || 'SEQUENCE OF EVENTS') : 'PROCESS SEQUENCE'));
  const displayTitle = title || defaultTitle;

  if (isSingleMessage || steps.length < 2) {
    if (directedScene && !displayTitle && !clean(props.detail)) return null;
    return (
      <AbsoluteFill style={{ padding: '13% 9%', justifyContent: 'center', ...entranceStyle }}>
        <div
          style={{
            maxWidth: 1450,
            transform: `translateY(${(1 - enter) * 18}px)`,
            background: 'rgba(8, 14, 22, 0.82)',
            border: '1px solid rgba(255, 255, 255, 0.14)',
            borderRadius: 18,
            padding: '48px 56px',
            boxShadow: '0 24px 64px rgba(0, 0, 0, 0.65)',
          }}
        >
          <div style={{ width: 76 * enter, height: 3, background: accent, marginBottom: 24, transformOrigin: 'left' }} />
          <div
            style={{
              color: ink,
              fontFamily: typography.display || 'Archivo Black',
              fontSize: displayTitle.length > 68 ? 54 : 76,
              fontWeight: 800,
              lineHeight: 1.05,
              letterSpacing: '-.03em',
              textWrap: 'balance',
            }}
          >
            {displayTitle}
          </div>
          {props.detail && (
            <div style={{ marginTop: 22, maxWidth: 900, color: muted, fontFamily: typography.body || 'Inter', fontSize: 24, lineHeight: 1.3 }}>
              {clean(props.detail)}
            </div>
          )}
        </div>
      </AbsoluteFill>
    );
  }

  if (variant === 'network_node_connect' && steps.length === 2) {
    return <AbsoluteFill style={{ color: ink, ...entranceStyle, overflow: 'hidden' }}>
      {displayTitle && <div style={{ position: 'absolute', left: '9%', right: '9%', top: '12%', fontFamily: typography.display || 'Archivo Black', fontSize: 58, lineHeight: 1.05 }}>{displayTitle}</div>}
      <svg viewBox="0 0 100 10" preserveAspectRatio="none" style={{ position: 'absolute', left: '25%', top: '46%', width: '50%', height: '8%' }}>
        <line x1="0" y1="5" x2="100" y2="5" stroke={rgba(accent, .2)} strokeWidth=".4" />
        <line x1="0" y1="5" x2="100" y2="5" stroke={accent} strokeWidth=".4" pathLength="1" strokeDasharray="1" strokeDashoffset={1 - stepProgress} />
      </svg>
      {steps.map((step, index) => {
        const reveal = Math.max(0, Math.min(1, stepProgress * 1.5 - index * .3));
        const headline = step.title || step.description;
        return <div key={index} style={{ position: 'absolute', left: index ? '55%' : '9%', top: '31%', width: '36%', minHeight: '38%', display: 'flex', flexDirection: 'column', justifyContent: 'center', background: palette.background || '#101820', padding: '28px 32px', borderLeft: `4px solid ${accent}`, opacity: reveal, transform: `translateY(${(1 - reveal) * 18}px)` }}>
          <div style={{ width: 14, height: 14, borderRadius: '50%', background: accent, marginBottom: 24, boxShadow: `0 0 0 8px ${rgba(accent, .16)}` }} />
          <div style={{ fontFamily: typography.display || 'Archivo Black', fontSize: headline.length > 80 ? 38 : 48, lineHeight: 1.13, textWrap: 'balance' }}>{headline}</div>
          {step.title && step.description && <div style={{ marginTop: 18, fontFamily: typography.body || 'DM Sans', fontSize: 26, color: muted, lineHeight: 1.3 }}>{step.description}</div>}
        </div>;
      })}
    </AbsoluteFill>;
  }

  if (variant === 'network_node_connect' && steps.length >= 3) {
    const positions = [
      { x: 50, y: 50 }, { x: 22, y: 31 }, { x: 78, y: 29 }, { x: 24, y: 73 }, { x: 77, y: 72 },
    ].slice(0, steps.length);
    return <AbsoluteFill style={{ color: ink, ...entranceStyle, overflow: 'hidden' }}>
      <div style={{ position: 'absolute', left: '9%', right: '9%', top: '11%', filter: 'drop-shadow(0 3px 16px rgba(0,0,0,.65))' }}>
        {displayTitle && <div style={{ color: ink, fontFamily: typography.display || 'Archivo Black', fontSize: 54, fontWeight: 900, lineHeight: .98, textTransform: 'uppercase' }}>{displayTitle}</div>}
      </div>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ position: 'absolute', inset: '21% 8% 8%', width: '84%', height: '71%', overflow: 'visible' }}>
        {positions.slice(1).map((point, index) => {
          const draw = Math.max(0, Math.min(1, stepProgress * 1.45 - index * .16));
          return <g key={`edge-${index}`} opacity={draw}>
            <line x1="50" y1="50" x2={point.x} y2={point.y} stroke={rgba(accent, .28)} strokeWidth=".5" />
            <line x1="50" y1="50" x2={point.x} y2={point.y} stroke={accent} strokeWidth=".26" pathLength="1" strokeDasharray="1" strokeDashoffset={1 - draw} />
          </g>;
        })}
      </svg>
      {steps.map((step, index) => {
        const point = positions[index];
        const reveal = Math.max(0, Math.min(1, stepProgress * 1.5 - Math.max(0, index - 1) * .15));
        const central = index === 0;
        return <div key={`${index}-${step.title}`} style={{ position: 'absolute', left: `${8 + point.x * .84}%`, top: `${21 + point.y * .71}%`, width: central ? '27%' : '24%', transform: `translate(-50%,-50%) scale(${.94 + .06 * reveal})`, opacity: reveal, textAlign: central ? 'center' : (point.x < 40 ? 'right' : 'left'), filter: 'drop-shadow(0 3px 12px rgba(0,0,0,.7))' }}>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 9, color: central ? accent : muted, fontFamily: typography.numbers || 'JetBrains Mono', fontSize: central ? 16 : 13, fontWeight: 800, letterSpacing: '.12em', textTransform: 'uppercase' }}>
            <span style={{ width: central ? 10 : 7, height: central ? 10 : 7, borderRadius: '50%', background: central ? accent : ink, boxShadow: `0 0 0 ${central ? 6 : 4}px ${rgba(accent, .18)}` }} />{String(index + 1).padStart(2, '0')}
          </div>
          <div style={{ color: ink, fontFamily: typography.display || 'Archivo Black', fontSize: central ? 31 : 23, fontWeight: 800, lineHeight: 1.05, marginTop: 10, overflowWrap: 'anywhere' }}>{step.title}</div>
          {step.description && <div style={{ color: muted, fontFamily: typography.body || 'Inter', fontSize: 17, lineHeight: 1.15, marginTop: 8 }}>{step.description}</div>}
        </div>;
      })}
    </AbsoluteFill>;
  }

  if (variant === 'hierarchical_system_tree' && steps.length >= 3) {
    const root = steps[0];
    const branches = steps.slice(1);
    return <AbsoluteFill style={{ color: ink, ...entranceStyle, overflow: 'hidden' }}>
      <div style={{ position: 'absolute', left: '9%', right: '9%', top: '12%', filter: 'drop-shadow(0 3px 16px rgba(0,0,0,.65))' }}>
        {displayTitle && <div style={{ color: ink, fontFamily: typography.display || 'Archivo Black', fontSize: 54, fontWeight: 900, lineHeight: .98, textTransform: 'uppercase' }}>{displayTitle}</div>}
      </div>
      <svg viewBox="0 0 100 50" preserveAspectRatio="none" style={{ position: 'absolute', left: '12%', right: '12%', top: '37%', width: '76%', height: '43%', overflow: 'visible' }}>
        <path d={`M 50 4 L 50 21 M ${100 / branches.length / 2} 21 L ${100 - 100 / branches.length / 2} 21 ${branches.map((_, index) => `M ${100 / branches.length * (index + .5)} 21 L ${100 / branches.length * (index + .5)} 44`).join(' ')}`} fill="none" stroke={accent} strokeOpacity=".72" strokeWidth=".55" pathLength="1" strokeDasharray="1" strokeDashoffset={1 - stepProgress} />
      </svg>
      <div style={{ position: 'absolute', left: '38%', right: '38%', top: '32%', opacity: stepProgress, textAlign: 'center', borderBottom: `2px solid ${accent}`, padding: '0 0 16px', filter: 'drop-shadow(0 3px 12px rgba(0,0,0,.7))' }}>
        <div style={{ color: muted, fontFamily: typography.numbers || 'JetBrains Mono', fontSize: 15, fontWeight: 750, letterSpacing: '.14em' }}>ROOT</div>
        <div style={{ color: ink, fontFamily: typography.display || 'Archivo Black', fontSize: 32, fontWeight: 850, lineHeight: 1.06, marginTop: 8 }}>{root.title}</div>
        {root.description && <div style={{ color: muted, fontFamily: typography.body || 'Inter', fontSize: 17, marginTop: 7 }}>{root.description}</div>}
      </div>
      <div style={{ position: 'absolute', left: '10%', right: '10%', top: '64%', display: 'grid', gridTemplateColumns: `repeat(${branches.length}, minmax(0, 1fr))`, gap: 24 }}>
        {branches.map((step, index) => {
          const reveal = Math.max(0, Math.min(1, stepProgress * 1.5 - index * .14));
          return <div key={`${index}-${step.title}`} style={{ minWidth: 0, opacity: reveal, transform: `translateY(${(1 - reveal) * 17}px)`, textAlign: 'center', filter: 'drop-shadow(0 3px 12px rgba(0,0,0,.7))' }}>
            <div style={{ color: accent, fontFamily: typography.numbers || 'JetBrains Mono', fontSize: 15, fontWeight: 700, letterSpacing: '.1em' }}>BRANCH {String(index + 1).padStart(2, '0')}</div>
            <div style={{ color: ink, fontFamily: typography.display || 'Archivo Black', fontSize: 26, fontWeight: 800, lineHeight: 1.05, marginTop: 9, overflowWrap: 'anywhere' }}>{step.title}</div>
            {step.description && <div style={{ color: muted, fontFamily: typography.body || 'Inter', fontSize: 17, lineHeight: 1.15, marginTop: 8 }}>{step.description}</div>}
          </div>;
        })}
      </div>
    </AbsoluteFill>;
  }

  if (variant === 'cause_and_effect_flow' && steps.length >= 2) {
    return <AbsoluteFill style={{ color: ink, ...entranceStyle, overflow: 'hidden' }}>
      <div style={{ position: 'absolute', left: '9%', right: '9%', top: '13%', filter: 'drop-shadow(0 3px 16px rgba(0,0,0,.65))' }}>
        {displayTitle && <div style={{ color: ink, fontFamily: typography.display || 'Archivo Black', fontSize: 54, fontWeight: 900, lineHeight: .98, textTransform: 'uppercase' }}>{displayTitle}</div>}
      </div>
      <div style={{ position: 'absolute', left: '9%', right: '9%', top: displayTitle ? '43%' : '27%', minHeight: '31%', display: 'grid', gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))`, gap: 34, alignItems: 'stretch' }}>
        {steps.map((step, index) => {
          const reveal = Math.max(0, Math.min(1, stepProgress * 1.5 - index * .13));
          const edge = index === 0 ? 'CAUSE' : index === steps.length - 1 ? 'EFFECT' : `LINK ${String(index).padStart(2, '0')}`;
          const headline = step.title || step.description;
          const detail = step.title ? step.description : '';
          return <div key={`${index}-${step.title}-${step.description}`} style={{ position: 'relative', minWidth: 0, minHeight: '30vh', display: 'flex', flexDirection: 'column', justifyContent: 'center', opacity: reveal, transform: `translateY(${(1 - reveal) * 18}px)`, borderTop: `3px solid ${index === 0 || index === steps.length - 1 ? accent : rgba(ink, .46)}`, padding: '24px 26px 28px', background: 'linear-gradient(145deg, rgba(7,16,23,.58), rgba(7,16,23,.12))', filter: 'drop-shadow(0 3px 12px rgba(0,0,0,.7))' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 13, color: index === steps.length - 1 ? accent : muted, fontFamily: typography.numbers || 'JetBrains Mono', fontSize: 17, fontWeight: 800, letterSpacing: '.14em' }}>
              <span style={{ width: 11, height: 11, borderRadius: '50%', background: index === steps.length - 1 ? accent : ink, boxShadow: `0 0 0 6px ${rgba(accent, .16)}`, flex: '0 0 auto' }} />
              {edge}
            </div>
            <div style={{ color: ink, fontFamily: typography.display || 'Archivo Black', fontSize: headline.length > 50 ? 34 : 41, fontWeight: 850, lineHeight: 1.04, marginTop: 17, overflowWrap: 'anywhere', textWrap: 'balance' }}>{headline}</div>
            {detail && <div style={{ color: muted, fontFamily: typography.body || 'Inter', fontSize: 19, lineHeight: 1.2, marginTop: 12 }}>{detail}</div>}
            {index < steps.length - 1 && <div style={{ position: 'absolute', top: 0, left: '100%', width: 28, height: 2, background: accent, transform: `scaleX(${reveal})`, transformOrigin: 'left' }} />}
          </div>;
        })}
      </div>
    </AbsoluteFill>;
  }

  const titleSize = displayTitle.length > 50 ? 52 : 68;

  return (
    <AbsoluteFill style={{ color: ink, ...entranceStyle, overflow: 'hidden' }}>
      {/* Editorial Header */}
      {displayTitle ? (
        <div
          style={{
            position: 'absolute',
            left: '8%',
            right: '8%',
            top: '11%',
            transform: `translateY(${(1 - enter) * 18}px)`,
            filter: 'drop-shadow(0 3px 18px rgba(0,0,0,.75))',
          }}
        >
          <div
            style={{
              color: ink,
              fontFamily: typography.display || 'Archivo Black',
              fontSize: titleSize,
              fontWeight: 800,
              lineHeight: 1.0,
              letterSpacing: '-.03em',
              textTransform: 'uppercase',
              textWrap: 'balance',
            }}
          >
            {displayTitle}
          </div>
          <div style={{ width: 86 * enter, height: 3, marginTop: 18, background: accent, transformOrigin: 'left' }} />
        </div>
      ) : null}

      {/* Progress Connector Track */}
      <div
        style={{
          position: 'absolute',
          left: '8%',
          right: '8%',
          top: '44%',
          height: 2,
          background: rgba(ink, 0.16),
          transformOrigin: 'left',
        }}
      >
        <div
          style={{
            width: '100%',
            height: '100%',
            background: `linear-gradient(90deg, ${accent}, ${rgba(accent, 0.4)})`,
            transform: `scaleX(${stepProgress})`,
            transformOrigin: 'left',
          }}
        />
      </div>

      {/* Step Cards Grid */}
      <div
        style={{
          position: 'absolute',
          left: '8%',
          right: '8%',
          top: '48%',
          display: 'grid',
          gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))`,
          gap: 28,
        }}
      >
        {steps.map((step, index) => {
          const reveal = Math.max(0, Math.min(1, stepProgress * 1.55 - index * 0.15));
          return (
            <div
              key={`${index}-${step.title}-${step.description}`}
              style={{
                opacity: reveal,
                transform: `translateY(${(1 - reveal) * 16}px)`,
                background: 'rgba(8, 14, 22, 0.34)',
                border: '0',
                borderTop: `2px solid ${accent}`,
                borderRadius: 0,
                padding: '24px 28px',
                boxShadow: 'none',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: accent, fontFamily: typography.numbers || 'JetBrains Mono', fontSize: 16, fontWeight: 700 }}>
                  <span style={{ width: 8, height: 8, borderRadius: '50%', background: accent, boxShadow: `0 0 0 4px ${rgba(accent, 0.22)}` }} />
                  <span>{String(index + 1).padStart(2, '0')}</span>
                </div>
              </div>

              {step.title && (
                <div
                  style={{
                    color: ink,
                    fontFamily: typography.display || 'Archivo Black',
                    fontSize: 22,
                    fontWeight: 750,
                    lineHeight: 1.15,
                    letterSpacing: '-.02em',
                    marginBottom: 10,
                  }}
                >
                  {step.title}
                </div>
              )}

              {step.description && (
                <div
                  style={{
                    color: step.title ? muted : ink,
                    fontFamily: step.title ? (typography.body || 'Inter') : (typography.display || 'Archivo Black'),
                    fontSize: step.title ? 17 : 27,
                    fontWeight: step.title ? 500 : 780,
                    lineHeight: step.title ? 1.35 : 1.1,
                    letterSpacing: step.title ? 0 : '-.025em',
                    textWrap: 'balance',
                  }}
                >
                  {step.description}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
}

