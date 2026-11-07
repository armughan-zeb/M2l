import React from 'react';
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { computeSpringEnter, computeEntranceStyle } from '../engine/MotionEngine';
import { rgba } from '../theme';

/**
 * Timeline — Universal Chronology & Milestone Engine.
 * Supported variants:
 * - vertical_chronology
 * - horizontal_milestone_trail
 * - date_flip_calendar
 * - era_bracket_card
 * - stepped_event_flow
 */
export function Timeline({ scene = {}, visualDNA = {}, props = {} }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const sceneDuration = Math.max(1, scene?.duration_in_frames !== undefined && scene?.duration_in_frames !== null
    ? Math.round(Number(scene.duration_in_frames))
    : Math.round(Number(scene?.duration || 4) * fps));
  const progress = Math.min(1, Math.max(0, frame / Math.max(1, sceneDuration - 1)));

  const events = Array.isArray(props.events) ? props.events : [];
  const date = String(props.date || props.year || '').toUpperCase();
  const title = String(props.title || scene.text?.headline || 'CHRONOLOGY');
  const variant = String(props.variant || scene.primary_visual?.variant || 'horizontal_milestone_trail').toLowerCase();

  if (!events.length && !date && !props.eraSpan) return null;

  const palette = visualDNA.palette || {};
  const cornerRadius = visualDNA.cornerRadius ?? 4;
  const enter = computeSpringEnter(frame, fps, 0, scene?.motion?.spring_stiffness || 120, scene?.motion?.spring_damping || 18);
  // Strict editorial isolation: motion graphics smoothly exit before scene cut, zero bleed into next scene
  const entranceStyle = computeEntranceStyle(scene?.motion?.entrance || 'fade', enter, progress, true);

  // Variant 1: Date Flip Calendar
  if (variant === 'date_flip_calendar') {
    const day = props.day || '';
    const month = props.month || '';
    const year = props.year || props.date || '';
    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: 'transparent', ...entranceStyle }}>
        <div
          style={{
            background: '#F8F6F0',
            borderRadius: 16,
            width: 380,
            boxShadow: '0 28px 70px rgba(0,0,0,0.9), 0 0 40px rgba(0,0,0,0.5)',
            overflow: 'hidden',
            textAlign: 'center',
            border: '2px solid #334155',
          }}
        >
          <div style={{ background: palette.accent, color: '#FFFFFF', padding: '16px 0', fontSize: 24, fontWeight: 800, letterSpacing: '0.12em' }}>
            {month}
          </div>
          <div style={{ padding: '36px 0 20px', fontFamily: visualDNA.typography.numbers, fontSize: 120, fontWeight: 900, color: '#1E293B', lineHeight: 1 }}>
            {day}
          </div>
          <div style={{ paddingBottom: 24, fontFamily: visualDNA.typography.numbers, fontSize: 26, color: '#64748B', fontWeight: 700 }}>
            {year}
          </div>
        </div>
        {props.caption && (
          <div style={{ marginTop: 28, color: palette.primary, fontSize: 28, fontFamily: visualDNA.typography.display, textAlign: 'center', textShadow: '0 2px 12px rgba(0,0,0,0.9)' }}>
            {props.caption}
          </div>
        )}
      </AbsoluteFill>
    );
  }

  // Variant 2: Era Bracket Card
  if (variant === 'era_bracket_card') {
    const span = props.eraSpan || date;
    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: 'transparent', ...entranceStyle }}>
        <div style={{
          textAlign: 'center',
          padding: '48px 64px',
          background: `radial-gradient(ellipse at center, ${rgba(palette.surface || palette.background || '#0d1216', 0.86)} 0%, ${rgba(palette.background || '#0d1216', 0.5)} 70%, transparent 100%)`,
          borderRadius: 24,
        }}>
          <div style={{ color: palette.accent, fontSize: 20, letterSpacing: '0.2em', textTransform: 'uppercase', marginBottom: 16, textShadow: '0 2px 10px rgba(0,0,0,0.8)' }}>
            HISTORICAL ERA
          </div>
          <div style={{ fontFamily: visualDNA.typography.numbers, fontSize: 100, fontWeight: 900, color: palette.primary, letterSpacing: '0.04em', lineHeight: 1, textShadow: '0 4px 30px rgba(0,0,0,0.95)' }}>
            [ {span} ]
          </div>
          <div style={{ marginTop: 24, fontFamily: visualDNA.typography.display, fontSize: 38, color: palette.secondary, textShadow: '0 2px 14px rgba(0,0,0,0.9)' }}>
            {title}
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 3: Vertical Chronology
  if (variant === 'vertical_chronology') {
    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', padding: '0 12%', background: 'transparent', ...entranceStyle }}>
        <div style={{
          width: '80%',
          background: `linear-gradient(135deg, ${rgba(palette.surface || '#161b22', 0.86)} 0%, ${rgba(palette.background || '#0d1117', 0.92)} 100%)`,
          border: `1px solid ${rgba(palette.border || palette.accent, 0.35)}`,
          borderRadius: cornerRadius || 16,
          padding: '48px 56px',
          boxShadow: `0 24px 60px rgba(0,0,0,0.75), 0 0 35px ${rgba(palette.accent || '#e15444', 0.15)}`,
        }}>
          <div style={{ position: 'relative', borderLeft: `3px solid ${palette.border}`, paddingLeft: 36, display: 'flex', flexDirection: 'column', gap: 40 }}>
            {events.map((ev, idx) => {
              const evActive = progress > (idx / events.length);
              return (
                <div key={idx} style={{ position: 'relative' }}>
                  <div
                    style={{
                      position: 'absolute',
                      left: -43,
                      top: 4,
                      width: 15,
                      height: 15,
                      borderRadius: '50%',
                      background: evActive ? palette.accent : palette.border,
                      boxShadow: evActive ? `0 0 16px ${palette.accent}` : 'none',
                    }}
                  />
                  <div style={{ fontFamily: visualDNA.typography.numbers, fontSize: 24, fontWeight: 800, color: evActive ? palette.accent : palette.secondary }}>
                    {ev.year}
                  </div>
                  <div style={{ fontFamily: visualDNA.typography.display, fontSize: 28, color: palette.primary, marginTop: 4 }}>
                    {ev.label}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 4: Stepped Event Flow
  if (variant === 'stepped_event_flow') {
    return (
      <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: 'transparent', padding: '0 10%', ...entranceStyle }}>
        <div style={{ width: '100%', display: 'flex', gap: 24 }}>
          {events.map((ev, idx) => (
            <div
              key={idx}
              style={{
                flex: 1,
                background: `linear-gradient(135deg, ${rgba(palette.surface || '#161b22', 0.86)} 0%, ${rgba(palette.background || '#0d1117', 0.92)} 100%)`,
                border: `1px solid ${rgba(palette.border || palette.accent, 0.35)}`,
                padding: '32px 24px',
                borderRadius: cornerRadius || 16,
                borderTop: `4px solid ${idx === 0 ? palette.accent : palette.border}`,
                boxShadow: `0 24px 60px rgba(0,0,0,0.75), 0 0 25px ${rgba(palette.accent, 0.1)}`,
              }}
            >
              <div style={{ color: palette.accent, fontSize: 14, fontFamily: 'monospace', marginBottom: 8 }}>STEP 0{idx + 1}</div>
              <div style={{ fontFamily: visualDNA.typography.numbers, fontSize: 28, fontWeight: 800, color: palette.primary, marginBottom: 12 }}>{ev.year}</div>
              <div style={{ fontFamily: visualDNA.typography.body, fontSize: 18, color: palette.secondary }}>{ev.label}</div>
            </div>
          ))}
        </div>
      </AbsoluteFill>
    );
  }

  // Variant 5: Horizontal Milestone Trail (Default)
  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: 'transparent', padding: '0 8%', ...entranceStyle }}>
      <div style={{
        width: '92%',
        background: `linear-gradient(135deg, ${rgba(palette.surface || '#161b22', 0.86)} 0%, ${rgba(palette.background || '#0d1117', 0.92)} 100%)`,
        border: `1px solid ${rgba(palette.border || palette.accent, 0.35)}`,
        borderRadius: cornerRadius || 16,
        padding: '48px 56px',
        boxShadow: `0 24px 60px rgba(0,0,0,0.75), 0 0 35px ${rgba(palette.accent || '#e15444', 0.15)}`,
      }}>
        <div style={{ textAlign: 'center', marginBottom: 48 }}>
          <div style={{ color: palette.accent, fontSize: 16, letterSpacing: '0.18em', textTransform: 'uppercase', marginBottom: 8, textShadow: '0 2px 10px rgba(0,0,0,0.8)' }}>TIMELINE SEQUENCE</div>
          <div style={{ color: palette.primary, fontSize: 44, fontFamily: visualDNA.typography.display, fontWeight: 800, textShadow: '0 2px 16px rgba(0,0,0,0.8)' }}>{title}</div>
        </div>
        <div style={{ width: '100%', position: 'relative', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ position: 'absolute', top: 20, left: 40, right: 40, height: 3, background: rgba(palette.border || '#444', 0.5), zIndex: 1 }} />
          {events.map((ev, idx) => {
            const evActive = progress > (idx / events.length);
            return (
              <div key={idx} style={{ position: 'relative', zIndex: 2, textAlign: 'center', width: 240 }}>
                <div
                  style={{
                    width: 42,
                    height: 42,
                    borderRadius: '50%',
                    background: evActive ? palette.accent : palette.surface,
                    border: `3px solid ${evActive ? '#FFFFFF' : palette.border}`,
                    margin: '0 auto 16px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: '#FFFFFF',
                    fontWeight: 800,
                    fontSize: 16,
                    boxShadow: evActive ? `0 0 16px ${palette.accent}` : 'none',
                  }}
                >
                  {idx + 1}
                </div>
                <div style={{ fontFamily: visualDNA.typography.numbers, fontSize: 24, fontWeight: 800, color: evActive ? palette.accent : palette.primary, marginBottom: 6 }}>
                  {ev.year}
                </div>
                <div style={{ fontFamily: visualDNA.typography.body, fontSize: 17, color: palette.secondary, lineHeight: 1.3 }}>
                  {ev.label}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </AbsoluteFill>
  );
}

