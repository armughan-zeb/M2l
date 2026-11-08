/*
 * Documentary Templates v2 — Vox / MrBeast / Netflix editorial style.
 * - Larger fonts (100-140px) for prominence
 * - Snappy spring animations (fast entry, slight overshoot)
 * - Clean text-background contrast with subtle matte
 * - Built-in Ken Burns camera motion variants
 * - Every 3rd scene: heavy kinetic motion
 */
import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { rgba } from './theme';
import { KineticType } from './motion-graphics/KineticType';
import { NumberCounter } from './motion-graphics/NumberCounter';
import { DateReveal } from './motion-graphics/DateReveal';
import { MotionCard, AccentLine } from './motion-graphics/MotionCard';
import { TypeOn, WordReveal, ScalePop, FadeCascade } from './texts/index';

const clamp = (v, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, v));
const txt = (v, fallback = '') => String(v ?? fallback).replace(/\s+/g, ' ').trim();
const words = (v, count = 8) => txt(v).split(' ').slice(0, count).join(' ');

/* Snappier spring configs for documentary feel */
const DOC_SNAPPY = { damping: 8, mass: 0.35, stiffness: 180 };
const DOC_ENTER = { damping: 12, mass: 0.50, stiffness: 150 };
const DOC_HEAVY = { damping: 6, mass: 0.30, stiffness: 200 };

function Texture({ theme, strength = 0.14 }) {
  return (
    <AbsoluteFill style={{
      pointerEvents: 'none', opacity: strength,
      backgroundImage: `radial-gradient(circle at 20% 18%, ${rgba(theme.palette.accent, 0.12)} 0 1px, transparent 1.5px)`,
      backgroundSize: '13px 13px', mixBlendMode: 'screen',
    }} />
  );
}

function Kicker({ children, theme, style = {} }) {
  return <span style={{ fontFamily: theme.font_family, fontSize: 20, fontWeight: 800, color: theme.palette.accent, letterSpacing: 4, textTransform: 'uppercase', ...style }}>{children}</span>;
}

/* Background matte card for text readability over images */
function TextMatte({ children, theme, padding = '5% 7%' }) {
  return (
    <div style={{
      padding, borderRadius: 6,
      background: `linear-gradient(135deg, ${rgba(theme.palette.background, 0.78)}, ${rgba(theme.palette.surface, 0.65)})`,
      boxShadow: `0 12px 48px ${rgba(theme.palette.background, 0.5)}`,
      borderLeft: `6px solid ${theme.palette.accent}`,
    }}>
      {children}
    </div>
  );
}

/* ── CHAPTER OPENER ── */
export function ChapterOpener({ props, theme }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const title = props.title || 'CHAPTER';
  const kicker = props.kicker || '';
  const local = Math.max(0, frame - 2);
  const barEnter = spring({ frame: Math.max(0, local - 8), fps, config: DOC_SNAPPY });

  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', padding: '0 6%', overflow: 'hidden' }}>
      <Texture theme={theme} strength={0.1} />
      {/* Top accent bar — wider, more visible */}
      <div style={{
        position: 'absolute', top: '4%', left: '5%', right: '5%',
        height: 5, background: theme.palette.accent,
        transform: `scaleX(${barEnter})`, transformOrigin: 'left',
        borderRadius: 3,
      }} />
      {/* Content block */}
      <div style={{ textAlign: 'center' }}>
        {kicker && (
          <div style={{
            opacity: interpolate(local, [0, 6], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }),
            transform: `translateY(${(1 - clamp(local / 8)) * -12}px)`,
          }}>
            <Kicker theme={theme}>{kicker}</Kicker>
          </div>
        )}
        <div style={{ marginTop: 10 }}>
          <KineticType
            text={title}
            theme={theme}
            animation="scaleIn"
            staggerFrames={2}
            fontSize={104}
            maxWidth="88%"
            letterSpacing={-3}
          />
        </div>
        {/* Bottom bar */}
        <div style={{
          width: `${Math.min(400, barEnter * 360 + 40)}px`,
          height: 8, background: theme.palette.accent,
          margin: '22px auto 0', borderRadius: 4,
        }} />
      </div>
    </AbsoluteFill>
  );
}

/* ── KEYWORD REVEAL ── */
export function KeywordReveal({ props, theme }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - 2);
  const headline = words(props.text || 'KEY IDEA', 6).toUpperCase();
  const detail = props.detail || '';
  const isHeavy = props.motionTier === 'heavy';

  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', padding: '0 6%', overflow: 'hidden' }}>
      <div style={{ textAlign: 'center', maxWidth: '84%' }}>
        <TextMatte theme={theme} padding="36px 42px">
          <KineticType
            text={headline}
            theme={theme}
            animation={isHeavy ? 'bounce' : 'scaleIn'}
            staggerFrames={isHeavy ? 2 : 3}
            fontSize={isHeavy ? 120 : 100}
            maxWidth="95%"
          />
          <div style={{ marginTop: 16 }}>
            <AccentLine theme={theme} width={320} height={10} delay={isHeavy ? 6 : 10} />
          </div>
          {detail && (
            <div style={{
              marginTop: 18, maxWidth: 620, margin: '18px auto 0',
              fontFamily: theme.font_family, fontSize: 20, lineHeight: 1.35,
              color: rgba(theme.palette.primary, 0.82),
              opacity: interpolate(local, [14, 22], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }),
            }}>
              {words(detail, 22)}
            </div>
          )}
        </TextMatte>
      </div>
    </AbsoluteFill>
  );
}

/* ── STAT COUNTER ── */
export function StatCounter({ props, theme }) {
  const value = String(props.value || '0');
  const label = (props.label || '').toUpperCase();
  const isHeavy = props.motionTier === 'heavy';



  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center' }}>
      <Texture theme={theme} strength={0.1} />
      <NumberCounter
        value={value}
        theme={theme}
        label={label}
        fontSize={isHeavy ? 160 : 140}
        countUp={true}
        duration={isHeavy ? 22 : 28}
        showAccentBar={true}
      />
    </AbsoluteFill>
  );
}

/* ── MONEY COUNTER ── */
export function MoneyCounter({ props, theme }) {
  const value = String(props.value || '0');
  const label = (props.label || '').toUpperCase();
  const unit = props.unit || '';



  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center' }}>
      <Texture theme={theme} strength={0.1} />
      <NumberCounter
        value={value}
        theme={theme}
        prefix="$"
        suffix={unit}
        label={label}
        fontSize={130}
        color={theme.palette.accent}
        countUp={true}
        duration={28}
      />
    </AbsoluteFill>
  );
}

/* ── COLD OPEN ── */
export function ColdOpen({ props, theme }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - 3);
  const enter = spring({ frame: local, fps, config: DOC_ENTER });
  const title = props.title || 'WHAT REALLY HAPPENED?';
  const kicker = props.kicker || '';
  const subtitle = props.subtitle || '';

  return (
    <AbsoluteFill style={{ padding: '6%', justifyContent: 'center', background: `linear-gradient(115deg, ${rgba(theme.palette.background, 0.88)}, ${rgba(theme.palette.background, 0.3)})` }}>
      <div style={{
        maxWidth: '72%',
        transform: `translateX(${(1 - enter) * -40}px)`,
      }}>
        {kicker && <Kicker theme={theme}>{kicker}</Kicker>}
        <div style={{ marginTop: 12, maxWidth: '95%' }}>
          <WordReveal
            text={title}
            theme={theme}
            fontSize={84}
            animation="slide"
            stagger={3}
            uppercase={true}
          />
        </div>
        <AccentLine theme={theme} width={360} height={9} delay={12} gap={8} />
        {subtitle && (
          <div style={{
            marginTop: 16, fontFamily: theme.font_family, fontSize: 22, lineHeight: 1.3,
            color: rgba(theme.palette.primary, 0.78), maxWidth: 580,
            opacity: interpolate(local, [14, 24], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }),
          }}>
            {words(subtitle, 22)}
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
}

/* ── END CARD ── */
export function EndCard({ props, theme }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - 4);
  const enter = spring({ frame: local, fps, config: DOC_ENTER });
  const title = props.title || 'FOLLOW THE STORY';
  const subtitle = props.subtitle || '';

  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: `radial-gradient(ellipse at 50% 40%, ${rgba(theme.palette.accent, 0.1)}, transparent 45%), ${theme.palette.background}` }}>
      <Texture theme={theme} strength={0.16} />
      <div style={{ textAlign: 'center', maxWidth: '72%', transform: `scale(${0.9 + enter * 0.1})` }}>
        <div style={{
          fontFamily: theme.display_font, fontSize: 84, fontWeight: 900,
          color: theme.palette.primary, lineHeight: 0.92, letterSpacing: -2,
          textShadow: `0 4px 20px ${rgba(theme.palette.background, 0.8)}`,
        }}>
          {title}
        </div>
        {subtitle && (
          <div style={{
            marginTop: 18, fontFamily: theme.font_family, fontSize: 28, fontWeight: 700,
            color: theme.palette.accent, letterSpacing: 3,
            opacity: interpolate(local, [8, 16], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }),
          }}>
            {subtitle}
          </div>
        )}
        <AccentLine theme={theme} width={220} height={7} delay={14} gap={8} />
      </div>
    </AbsoluteFill>
  );
}

/* ── CAUSE / EFFECT ── */
export function CauseEffect({ props, theme }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - 2);
  const cause = txt(props.cause || 'THE EVENT');
  const effect = txt(props.effect || 'THE CONSEQUENCE');
  const leftEnter = spring({ frame: local, fps, config: DOC_SNAPPY });
  const rightEnter = spring({ frame: Math.max(0, local - 6), fps, config: DOC_SNAPPY });
  const arrowScale = spring({ frame: Math.max(0, local - 10), fps, config: DOC_SNAPPY });

  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', padding: '5%', overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 32 }}>
        {/* Cause */}
        <div style={{
          width: 320, padding: '28px 24px', textAlign: 'center',
          background: rgba(theme.palette.surface, 0.85),
          borderRadius: 8, borderLeft: `6px solid ${rgba(theme.palette.danger, 0.7)}`,
          transform: `translateX(${(1 - leftEnter) * -80}px)`,
          opacity: Math.min(1, local / 12),
        }}>
          <div style={{ fontFamily: theme.display_font, fontSize: 38, fontWeight: 900, color: theme.palette.primary, lineHeight: 1.15 }}>
            {cause}
          </div>
        </div>
        {/* Arrow */}
        <div style={{
          fontSize: 44, color: theme.palette.accent,
          transform: `scale(${arrowScale})`, opacity: arrowScale,
        }}>→</div>
        {/* Effect */}
        <div style={{
          width: 320, padding: '28px 24px', textAlign: 'center',
          background: rgba(theme.palette.surface, 0.85),
          borderRadius: 8, borderLeft: `6px solid ${theme.palette.accent}`,
          transform: `translateX(${(1 - rightEnter) * 80}px)`,
          opacity: Math.min(1, Math.max(0, (local - 6) / 12)),
        }}>
          <div style={{ fontFamily: theme.display_font, fontSize: 38, fontWeight: 900, color: theme.palette.primary, lineHeight: 1.15 }}>
            {effect}
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
}

/* ── EVIDENCE CALLOUT ── */
export function EvidenceCallout({ props, theme }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - 2);
  const enter = spring({ frame: local, fps, config: DOC_SNAPPY });
  const text = props.text || 'EVIDENCE';
  const detail = props.detail || '';

  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', padding: '5%' }}>
      <div style={{
        maxWidth: '64%', padding: '38px 44px',
        background: rgba(theme.palette.background, 0.92),
        borderRadius: 8, borderLeft: `10px solid ${theme.palette.danger || '#c0392b'}`,
        boxShadow: `0 24px 56px ${rgba(theme.palette.background, 0.55)}`,
        transform: `scale(${0.88 + enter * 0.12})`,
      }}>
        <ScalePop text={txt(text, 'EVIDENCE')} theme={theme} fontSize={62} />
        {detail && (
          <div style={{
            marginTop: 14, fontFamily: theme.font_family, fontSize: 20, lineHeight: 1.35,
            color: rgba(theme.palette.primary, 0.78), maxWidth: '92%',
            opacity: interpolate(local, [12, 20], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }),
          }}>
            {words(detail, 26)}
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
}

/* ── QUOTE CARD ── */
export function QuoteCard({ props, theme }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - 3);
  const enter = spring({ frame: local, fps, config: DOC_ENTER });
  const quote = props.quote || props.text || 'A notable quote.';
  const attribution = props.attribution || '';

  return (
    <AbsoluteFill style={{ justifyContent: 'center', padding: '8%' }}>
      <div style={{ position: 'absolute', top: '5%', left: '3%', color: rgba(theme.palette.accent, 0.5), fontFamily: 'Georgia, serif', fontSize: 220, lineHeight: 0.5 }}>"</div>
      <div style={{ maxWidth: '78%', transform: `translateY(${(1 - enter) * 30}px)`, opacity: Math.min(1, local / 10) }}>
        <div style={{ fontFamily: theme.display_font, fontSize: 60, fontWeight: 800, lineHeight: 1.06, color: theme.palette.primary, letterSpacing: -1 }}>
          {words(quote, 34)}
        </div>
        {attribution && (
          <div style={{ marginTop: 18, fontFamily: theme.font_family, fontSize: 20, color: theme.palette.accent, letterSpacing: 2 }}>
            — {attribution}
          </div>
        )}
        <AccentLine theme={theme} width={160} height={5} delay={16} gap={8} />
      </div>
    </AbsoluteFill>
  );
}

/* ── LOWER THIRD ── */
export function LowerThird({ props, theme }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - 2);
  const enter = spring({ frame: local, fps, config: DOC_SNAPPY });
  const isV2 = Number(props._cue_schema_version || 0) >= 2;
  const title = txt(props.title || (isV2 ? '' : 'NAME'));
  const subtitle = txt(props.subtitle || (isV2 ? '' : 'TITLE'));

  return (
    <AbsoluteFill style={{ justifyContent: 'end', padding: '0 5% 4%', pointerEvents: 'none' }}>
      <div style={{
        alignSelf: 'flex-start', padding: '18px 24px',
        background: rgba(theme.palette.background, 0.88),
        borderLeft: `6px solid ${theme.palette.accent}`,
        transform: `translateX(${(1 - enter) * -80}px)`,
        opacity: Math.min(1, local / 8),
        boxShadow: `0 12px 32px ${rgba(theme.palette.background, 0.4)}`,
        maxWidth: '44%',
      }}>
        <div style={{ fontFamily: theme.display_font, fontSize: 32, fontWeight: 900, color: theme.palette.primary }}>{title}</div>
        {subtitle && <div style={{ marginTop: 2, fontFamily: theme.font_family, fontSize: 16, color: rgba(theme.palette.primary, 0.7) }}>{subtitle}</div>}
      </div>
    </AbsoluteFill>
  );
}

/* ── BLUEPRINT REVEAL ── */
export function BlueprintReveal({ props, theme }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - 2);
  const title = txt(props.title || props.text || 'THE MECHANISM');
  const detail = txt(props.detail || '');
  const gridOpacity = interpolate(local, [0, 16], [0.04, 0.1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });

  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', padding: '4%' }}>
      <div style={{ position: 'absolute', inset: 0, opacity: gridOpacity,
        backgroundImage: `linear-gradient(${rgba(theme.palette.accent, 0.3)} 1px, transparent 1px), linear-gradient(90deg, ${rgba(theme.palette.accent, 0.3)} 1px, transparent 1px)`,
        backgroundSize: '50px 50px' }} />
      <div style={{
        maxWidth: '60%', padding: '40px 44px',
        background: rgba(theme.palette.background, 0.9),
        borderRadius: 8, borderTop: `5px solid #5dade2`,
        boxShadow: `0 20px 48px ${rgba(theme.palette.background, 0.5)}`,
      }}>
        <Kicker theme={theme} style={{ color: '#5dade2' }}>SCHEMATIC</Kicker>
        <div style={{ marginTop: 10 }}>
          <WordReveal text={title} theme={theme} fontSize={52} animation="slide" stagger={3} />
        </div>
        {detail && (
          <div style={{
            marginTop: 14, fontFamily: theme.font_family, fontSize: 19,
            color: rgba(theme.palette.primary, 0.7), lineHeight: 1.35,
            opacity: interpolate(local, [14, 22], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }),
          }}>
            {words(detail, 22)}
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
}

/* ── KINETIC STATEMENT ── */
export function KineticStatement({ props, theme }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const text = txt(props.text || 'STATEMENT');
  const detail = txt(props.detail || '');

  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', padding: '4%' }}>
      <div style={{ maxWidth: '80%', textAlign: 'center' }}>
        <KineticType
          text={text}
          theme={theme}
          mode="word"
          animation="scaleIn"
          staggerFrames={2}
          fontSize={88}
          maxWidth="92%"
        />
        <AccentLine theme={theme} width={280} height={8} delay={14} gap={8} />
        {detail && (
          <div style={{
            marginTop: 16, fontFamily: theme.font_family, fontSize: 22, lineHeight: 1.3,
            color: rgba(theme.palette.primary, 0.78), maxWidth: 600,
            opacity: interpolate(frame, [14, 22], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }),
          }}>
            {words(detail, 22)}
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
}

/* ── DATE CARD ── */
export function DateCard({ props, theme }) {

  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', padding: '4%' }}>
      <DateReveal
        label={props.label || '1912'}
        kicker={props.kicker || 'TIMELINE'}
        theme={theme}
        variant="cinematic"
      />
    </AbsoluteFill>
  );
}

/* ── CALENDAR RIP ── */
export function CalendarRip({ props, theme }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - 2);
  const date = txt(props.date || props.label || 'JULY 20');
  const event = txt(props.event || props.title || '');
  const enter = spring({ frame: local, fps, config: DOC_SNAPPY });

  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', padding: '4%' }}>
      <div style={{
        padding: '36px 48px', background: '#14181d', borderRadius: 4,
        boxShadow: `0 24px 48px ${rgba(theme.palette.background, 0.55)}`,
        transform: `scale(${0.88 + enter * 0.12}) rotate(${(1 - enter) * -2}deg)`,
      }}>
        <div style={{ fontFamily: 'Georgia, serif', fontSize: 90, fontWeight: 700, color: '#f5f1e8', lineHeight: 1, textAlign: 'center' }}>
          {date}
        </div>
        {event && <div style={{ marginTop: 10, fontFamily: theme.font_family, fontSize: 22, color: '#cfd4da', textAlign: 'center' }}>{event}</div>}
        <div style={{ width: '60%', height: 3, background: theme.palette.accent, margin: '12px auto 0' }} />
      </div>
    </AbsoluteFill>
  );
}

/* ── MAP ROUTE ── */
export function MapRoute({ props, theme }) {
  const frame = useCurrentFrame();

  const from = txt(props.from || 'A');
  const to = txt(props.to || 'B');
  const label = txt(props.label || '');
  const progress = clamp(frame / 22);
  const pulse = Math.sin(frame * 0.2) * 0.3 + 0.7;



  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 36 }}>
        <div style={{ width: 44, height: 44, borderRadius: '50%', background: theme.palette.primary,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontFamily: theme.font_family, fontWeight: 900, fontSize: 16, color: theme.palette.background }}>
          {from.slice(0, 2)}
        </div>
        <div style={{ position: 'relative', width: 180, height: 4 }}>
          <div style={{ position: 'absolute', inset: 0, background: rgba(theme.palette.primary, 0.2) }} />
          <div style={{ position: 'absolute', left: 0, top: 0, width: `${progress * 180}px`, height: '100%', background: theme.palette.accent }} />
          <div style={{ position: 'absolute', left: `${progress * 180 - 6}px`, top: -8, width: 16, height: 16, borderRadius: '50%',
            background: theme.palette.accent, boxShadow: `0 0 8px ${rgba(theme.palette.accent, 0.5)}` }} />
        </div>
        <div style={{ width: 44, height: 44, borderRadius: '50%', background: theme.palette.surface,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontFamily: theme.font_family, fontWeight: 900, fontSize: 16, color: theme.palette.primary }}>
          {to.slice(0, 2)}
        </div>
      </div>
      {label && <div style={{ marginTop: 14, fontFamily: theme.font_family, fontSize: 16, color: rgba(theme.palette.primary, 0.7), letterSpacing: 2, textTransform: 'uppercase' }}>{label}</div>}
    </AbsoluteFill>
  );
}

/* ── LISTICLE BUILD ── */
export function ListicleBuild({ props, theme }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const title = txt(props.title || 'KEY POINTS');
  const items = Array.isArray(props.items) ? props.items.slice(0, 4) : ['Item 1', 'Item 2', 'Item 3'];



  return (
    <AbsoluteFill style={{ justifyContent: 'center', padding: '5% 8%' }}>
      <div style={{ fontFamily: theme.font_family, fontSize: 22, fontWeight: 800, color: theme.palette.accent, letterSpacing: 3, textTransform: 'uppercase', marginBottom: 18, opacity: clamp(frame / 8) }}>
        {title}
      </div>
      {items.map((item, i) => {
        const delay = frame - i * 10;
        const enter = spring({ frame: Math.max(0, delay), fps, config: DOC_SNAPPY });
        const label = typeof item === 'string' ? item : (item.label || item.text || String(i + 1));
        return (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 12, transform: `translateX(${(1 - enter) * -30}px)`, opacity: enter }}>
            <div style={{ width: 32, height: 32, borderRadius: '50%', background: theme.palette.accent,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontFamily: theme.font_family, fontWeight: 900, fontSize: 16, color: theme.palette.background }}>
              {i + 1}
            </div>
            <div style={{ fontFamily: theme.font_family, fontSize: 26, fontWeight: 600, color: theme.palette.primary, lineHeight: 1.2 }}>
              {txt(label)}
            </div>
          </div>
        );
      })}
    </AbsoluteFill>
  );
}

/* ── WITNESS QUOTE / WORD CASCADE ── */
export function WordCascade({ props, theme }) { return <KineticStatement props={props} theme={theme} />; }


/* ── MAP COMPARISON (Vox-style Geographic Route) ── */
export function MapComparison({ props, theme }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - 2);
  const fromCity = txt(props.from || props.origin || 'POINT A');
  const toCity = txt(props.to || props.destination || 'POINT B');
  const distance = txt(props.distance || props.stat || '');
  const title = txt(props.title || props.headline || 'GEOGRAPHIC TRANSIT');
  const routeProgress = clamp(local / 28);
  const enter = spring({ frame: local, fps, config: DOC_ENTER });

  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', padding: '5%' }}>
      {/* Dark overlay shadow to keep graphic in focus */}
      <AbsoluteFill style={{ background: 'radial-gradient(ellipse at center, rgba(0,0,0,0.5) 0%, rgba(0,0,0,0.75) 100%)', pointerEvents: 'none' }} />
      <div style={{
        position: 'relative', width: '80%', padding: '36px 44px',
        background: rgba(theme.palette.background, 0.92),
        borderRadius: 8, border: `1px solid ${rgba(theme.palette.accent, 0.4)}`,
        boxShadow: `0 24px 60px rgba(0,0,0,0.8), 0 0 40px ${rgba(theme.palette.accent, 0.1)}`,
        transform: `scale(${0.92 + enter * 0.08})`,
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24, borderBottom: `1px solid ${rgba(theme.palette.primary, 0.15)}`, paddingBottom: 14 }}>
          <Kicker theme={theme}>{title}</Kicker>
          {distance && <span style={{ fontFamily: theme.font_family, fontSize: 16, color: theme.palette.accent, fontWeight: 700 }}>{distance}</span>}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '20px 10px' }}>
          <div style={{ textAlign: 'center', minWidth: 140 }}>
            <div style={{
              width: 52, height: 52, borderRadius: '50%',
              background: rgba(theme.palette.surface, 0.9),
              border: `3px solid ${theme.palette.primary}`,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              margin: '0 auto 10px',
              boxShadow: `0 0 16px ${rgba(theme.palette.primary, 0.3)}`,
            }}>
              <span style={{ fontFamily: theme.font_family, fontWeight: 900, fontSize: 18, color: theme.palette.primary }}>A</span>
            </div>
            <div style={{ fontFamily: theme.display_font, fontSize: 24, fontWeight: 800, color: theme.palette.primary }}>{fromCity}</div>
            <div style={{ fontFamily: theme.font_family, fontSize: 13, color: rgba(theme.palette.primary, 0.6), textTransform: 'uppercase', letterSpacing: 2 }}>ORIGIN</div>
          </div>
          <div style={{ flex: 1, margin: '0 24px', position: 'relative' }}>
            <div style={{ height: 4, background: rgba(theme.palette.primary, 0.2), borderRadius: 2 }} />
            <div style={{
              position: 'absolute', top: 0, left: 0,
              width: `${routeProgress * 100}%`, height: 4,
              background: `linear-gradient(90deg, ${theme.palette.primary}, ${theme.palette.accent})`,
              borderRadius: 2,
              boxShadow: `0 0 10px ${rgba(theme.palette.accent, 0.6)}`,
            }} />
            <div style={{
              position: 'absolute', top: -7, left: `${Math.max(0, routeProgress * 100 - 2)}%`,
              width: 18, height: 18, borderRadius: '50%',
              background: theme.palette.accent,
              boxShadow: `0 0 14px ${rgba(theme.palette.accent, 0.9)}`,
              transform: 'scale(1)',
            }} />
          </div>
          <div style={{ textAlign: 'center', minWidth: 140 }}>
            <div style={{
              width: 52, height: 52, borderRadius: '50%',
              background: rgba(theme.palette.surface, 0.9),
              border: `3px solid ${theme.palette.accent}`,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              margin: '0 auto 10px',
              boxShadow: `0 0 16px ${rgba(theme.palette.accent, 0.4)}`,
            }}>
              <span style={{ fontFamily: theme.font_family, fontWeight: 900, fontSize: 18, color: theme.palette.accent }}>B</span>
            </div>
            <div style={{ fontFamily: theme.display_font, fontSize: 24, fontWeight: 800, color: theme.palette.accent }}>{toCity}</div>
            <div style={{ fontFamily: theme.font_family, fontSize: 13, color: rgba(theme.palette.primary, 0.6), textTransform: 'uppercase', letterSpacing: 2 }}>DESTINATION</div>
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
}

/* ── METRIC GRID (4-Up Dashboard KPI Stats) ── */
export function MetricGrid({ props, theme }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - 2);
  const title = txt(props.title || 'KEY METRICS');
  const items = Array.isArray(props.metrics || props.items) ? (props.metrics || props.items).slice(0, 4) : [
    { value: props.val1 || '10x', label: props.lbl1 || 'GROWTH' },
    { value: props.val2 || '94%', label: props.lbl2 || 'ACCURACY' },
    { value: props.val3 || '$1.2M', label: props.lbl3 || 'REVENUE' },
    { value: props.val4 || '48h', label: props.lbl4 || 'TIMELINE' },
  ];

  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', padding: '5%' }}>
      {/* Dark overlay shadow to keep graphic in focus */}
      <AbsoluteFill style={{ background: 'radial-gradient(ellipse at center, rgba(0,0,0,0.45) 0%, rgba(0,0,0,0.7) 100%)', pointerEvents: 'none' }} />
      <div style={{ width: '84%', maxWidth: 960 }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <Kicker theme={theme}>{title}</Kicker>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 18 }}>
          {items.map((item, i) => {
            const delay = i * 4;
            const enter = spring({ frame: Math.max(0, local - delay), fps, config: DOC_SNAPPY });
            return (
              <div key={i} style={{
                padding: '24px 28px',
                background: rgba(theme.palette.background, 0.9),
                borderRadius: 8,
                borderLeft: `5px solid ${i % 2 === 0 ? theme.palette.accent : theme.palette.primary}`,
                boxShadow: `0 14px 36px rgba(0,0,0,0.5)`,
                transform: `scale(${0.9 + enter * 0.1})`,
                opacity: enter,
              }}>
                <div style={{
                  fontFamily: theme.display_font, fontSize: 44, fontWeight: 900,
                  color: i % 2 === 0 ? theme.palette.accent : theme.palette.primary,
                  lineHeight: 1, letterSpacing: -1,
                }}>
                  {item.value || item.stat || '0'}
                </div>
                <div style={{
                  marginTop: 8, fontFamily: theme.font_family, fontSize: 15,
                  fontWeight: 700, color: rgba(theme.palette.primary, 0.7),
                  textTransform: 'uppercase', letterSpacing: 2,
                }}>
                  {item.label || item.name || `METRIC ${i + 1}`}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </AbsoluteFill>
  );
}

/* ── DOSSIER CARD (Investigative Classified Case File) ── */
export function DossierCard({ props, theme }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - 3);
  const enter = spring({ frame: local, fps, config: DOC_ENTER });
  const subject = txt(props.subject || props.title || 'SUBJECT PROFILE');
  const details = Array.isArray(props.fields) ? props.fields : [
    { label: 'CLASSIFICATION', value: props.classification || 'TOP SECRET // DECLASSIFIED' },
    { label: 'STATUS', value: props.status || 'CONFIRMED' },
    { label: 'DATE OF RECORD', value: props.date || 'OCTOBER 2024' },
    { label: 'SOURCE', value: props.source || 'OFFICIAL ARCHIVE' },
  ];

  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', padding: '5%' }}>
      {/* Dark overlay shadow to keep graphic in focus */}
      <AbsoluteFill style={{ background: 'radial-gradient(ellipse at center, rgba(0,0,0,0.5) 0%, rgba(0,0,0,0.8) 100%)', pointerEvents: 'none' }} />
      <div style={{
        position: 'relative', width: '72%', maxWidth: 740,
        padding: '38px 46px',
        background: '#16191f',
        borderRadius: 4,
        border: '1px solid rgba(255,255,255,0.12)',
        boxShadow: '0 30px 70px rgba(0,0,0,0.9), inset 0 0 30px rgba(0,0,0,0.5)',
        transform: `scale(${0.92 + enter * 0.08}) rotate(${(1 - enter) * -1.5}deg)`,
      }}>
        <div style={{
          position: 'absolute', top: 20, right: 30,
          border: '3px solid #ef4444', borderRadius: 4,
          padding: '4px 12px',
          color: '#ef4444', fontFamily: theme.display_font, fontSize: 16,
          fontWeight: 900, letterSpacing: 3, textTransform: 'uppercase',
          transform: 'rotate(-8deg)', opacity: clamp(local / 6),
        }}>
          CONFIDENTIAL
        </div>
        <div style={{ fontFamily: theme.font_family, fontSize: 13, letterSpacing: 4, color: theme.palette.accent, textTransform: 'uppercase', marginBottom: 8 }}>
          INTELLIGENCE BRIEFING
        </div>
        <div style={{ fontFamily: theme.display_font, fontSize: 38, fontWeight: 900, color: '#f8fafc', lineHeight: 1.1, marginBottom: 20, borderBottom: '2px solid rgba(255,255,255,0.15)', paddingBottom: 16 }}>
          {subject}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {details.map((field, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
              <span style={{ fontFamily: theme.font_family, fontSize: 14, color: 'rgba(255,255,255,0.5)', textTransform: 'uppercase', letterSpacing: 1 }}>{field.label}</span>
              <span style={{ fontFamily: theme.font_family, fontSize: 14, fontWeight: 700, color: '#f8fafc' }}>{field.value}</span>
            </div>
          ))}
        </div>
      </div>
    </AbsoluteFill>
  );
}

/* ── PROCESS FLOW (Step-by-Step Pipeline) ── */
export function ProcessFlow({ props, theme }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - 2);
  const title = txt(props.title || 'HOW IT WORKS');
  const steps = Array.isArray(props.steps) ? props.steps.slice(0, 4) : [
    { title: 'CAPTURE', desc: 'Raw event data ingestion' },
    { title: 'ANALYZE', desc: 'Deep pattern recognition' },
    { title: 'EXECUTE', desc: 'Precision automated action' },
  ];

  return (
    <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', padding: '5%' }}>
      {/* Dark overlay shadow to keep graphic in focus */}
      <AbsoluteFill style={{ background: 'radial-gradient(ellipse at center, rgba(0,0,0,0.5) 0%, rgba(0,0,0,0.75) 100%)', pointerEvents: 'none' }} />
      <div style={{ width: '88%', maxWidth: 1000 }}>
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <Kicker theme={theme}>{title}</Kicker>
        </div>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'center', gap: 16 }}>
          {steps.map((step, i) => {
            const enter = spring({ frame: Math.max(0, local - i * 6), fps, config: DOC_SNAPPY });
            return (
              <React.Fragment key={i}>
                <div style={{
                  flex: 1, padding: '24px 20px',
                  background: rgba(theme.palette.background, 0.92),
                  borderRadius: 8,
                  borderTop: `4px solid ${theme.palette.accent}`,
                  boxShadow: '0 16px 40px rgba(0,0,0,0.6)',
                  transform: `translateY(${(1 - enter) * 30}px)`,
                  opacity: enter,
                  textAlign: 'center',
                }}>
                  <div style={{
                    width: 36, height: 36, borderRadius: '50%',
                    background: theme.palette.accent,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    margin: '0 auto 14px',
                    fontFamily: theme.font_family, fontWeight: 900, fontSize: 16,
                    color: theme.palette.background,
                  }}>
                    {i + 1}
                  </div>
                  <div style={{ fontFamily: theme.display_font, fontSize: 20, fontWeight: 900, color: theme.palette.primary, marginBottom: 8 }}>
                    {txt(step.title || `STEP ${i + 1}`)}
                  </div>
                  <div style={{ fontFamily: theme.font_family, fontSize: 14, color: rgba(theme.palette.primary, 0.7), lineHeight: 1.3 }}>
                    {txt(step.desc || '')}
                  </div>
                </div>
                {i < steps.length - 1 && (
                  <div style={{
                    alignSelf: 'center', fontSize: 24, color: theme.palette.accent,
                    opacity: clamp((local - i * 6) / 8),
                    transform: 'translateY(-10px)',
                  }}>→</div>
                )}
              </React.Fragment>
            );
          })}
        </div>
      </div>
    </AbsoluteFill>
  );
}

/* ── MEDIA FOCUS CARD (Centered Focus with Dark Shadow Vignette) ── */
export function MediaFocusCard({ props, theme }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - 2);
  const enter = spring({ frame: local, fps, config: DOC_ENTER });
  const title = txt(props.title || props.headline || '');
  const subtitle = txt(props.subtitle || props.description || '');
  if (!title) return null;

  return (
    <AbsoluteFill style={{ justifyContent: 'flex-end', alignItems: 'flex-start', padding: '0 7% 11%', pointerEvents: 'none' }}>
      <div style={{
        maxWidth: '78%', padding: '13px 23px 15px', textAlign: 'left',
        borderLeft: `7px solid ${theme.palette.accent}`,
        background: 'linear-gradient(90deg, rgba(3,10,17,.88), rgba(3,10,17,.48) 78%, rgba(3,10,17,0))',
        transform: `translateX(${(1 - enter) * -24}px)`, opacity: enter,
        zIndex: 2,
      }}>
        <div style={{
          fontFamily: 'Archivo Black, sans-serif', fontSize: title.length > 23 ? 50 : 68, fontWeight: 900,
          color: '#fff', lineHeight: 0.96, letterSpacing: '-.035em',
          textShadow: '0 3px 12px rgba(0,0,0,.92), 0 1px 2px #000',
        }}>{title}</div>
        {subtitle && <div style={{ marginTop: 9, fontFamily: 'JetBrains Mono, monospace', fontSize: 16, color: theme.palette.accent, letterSpacing: '.08em', textTransform: 'uppercase', fontWeight: 700 }}>{subtitle}</div>}
      </div>
    </AbsoluteFill>
  );
}

/* ── DATA FACT CARD ── */
export function DataFactCard({ props, theme }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - 2);
  const enter = spring({ frame: local, fps, config: DOC_ENTER });
  const kicker = txt(props.kicker || props.category || props.source || 'KEY STATISTIC').toUpperCase();
  const val = props.value ?? props.target ?? props.stat ?? props.number;
  const num = typeof val === 'number' ? val : Number(String(val ?? '').replace(/[^0-9.-]/g, ''));
  const hasNum = val != null && Number.isFinite(num);
  const title = txt(props.title || props.headline || props.label || '');
  const body = txt(props.body || props.text || props.description || '');

  return (
    <AbsoluteFill style={{ justifyContent: 'flex-end', alignItems: 'flex-start', padding: '0 6% 9%', pointerEvents: 'none' }}>
      <div style={{
        maxWidth: 620, padding: '22px 28px',
        borderRadius: 12,
        background: `linear-gradient(135deg, ${rgba(theme.palette.background, 0.94)} 0%, ${rgba(theme.palette.surface, 0.88)} 100%)`,
        border: `1px solid ${rgba(theme.palette.accent, 0.3)}`,
        borderLeft: `6px solid ${theme.palette.accent}`,
        boxShadow: `0 24px 60px rgba(0,0,0,0.65), 0 0 25px ${rgba(theme.palette.accent, 0.12)}`,
        transform: `translateY(${(1 - enter) * 24}px)`,
        opacity: enter,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: theme.palette.accent, boxShadow: `0 0 8px ${theme.palette.accent}` }} />
          <span style={{ fontFamily: theme.font_family, fontSize: 13, fontWeight: 800, color: theme.palette.accent, letterSpacing: 2 }}>{kicker}</span>
        </div>
        {hasNum && (
          <div style={{
            fontFamily: theme.display_font, fontSize: 54, fontWeight: 900,
            color: theme.palette.primary, lineHeight: 1.0, letterSpacing: -1.5,
            marginBottom: 8, textShadow: '0 3px 12px rgba(0,0,0,0.5)',
          }}>
            <NumberCounter target={num} decimals={props.decimals ? Number(props.decimals) : 0} prefix={props.prefix || ''} suffix={props.suffix || props.unit || ''} theme={theme} />
          </div>
        )}
        {title && (
          <div style={{ fontFamily: theme.display_font, fontSize: 24, fontWeight: 850, color: theme.palette.primary, lineHeight: 1.15, marginBottom: body ? 6 : 0 }}>
            {title}
          </div>
        )}
        {body && (
          <div style={{ fontFamily: theme.font_family, fontSize: 16, fontWeight: 500, color: rgba(theme.palette.primary, 0.82), lineHeight: 1.35, borderTop: `1px solid ${rgba(theme.palette.primary, 0.12)}`, paddingTop: 8 }}>
            {body}
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
}

/* ── CINEMATIC HIGHLIGHT ── */
export function CinematicHighlight({ props, theme }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - 2);
  const enter = spring({ frame: local, fps, config: DOC_ENTER });
  const wipe = interpolate(frame, [6, 24], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const text = txt(props.text || props.headline || props.highlight || props.title || '');
  const kicker = txt(props.kicker || props.category || props.source || 'VERIFIED INTEL').toUpperCase();
  const subtext = txt(props.subtext || props.subtitle || props.role || '');

  return (
    <AbsoluteFill style={{ justifyContent: 'flex-end', alignItems: 'flex-start', padding: '0 6% 10%', pointerEvents: 'none' }}>
      <div style={{
        maxWidth: 780, padding: '20px 26px',
        borderRadius: 10,
        background: `linear-gradient(135deg, ${rgba(theme.palette.background, 0.92)} 0%, ${rgba(theme.palette.surface, 0.82)} 100%)`,
        borderLeft: `5px solid ${theme.palette.accent}`,
        borderTop: `1px solid ${rgba(theme.palette.accent, 0.25)}`,
        boxShadow: `0 20px 50px rgba(0,0,0,0.6), 0 0 20px ${rgba(theme.palette.accent, 0.1)}`,
        transform: `translateY(${(1 - enter) * 20}px)`,
        opacity: enter,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: theme.palette.accent }} />
          <span style={{ fontFamily: theme.font_family, fontSize: 13, fontWeight: 800, color: theme.palette.accent, letterSpacing: 2 }}>{kicker}</span>
        </div>
        <div style={{ position: 'relative', display: 'inline-block', fontFamily: theme.display_font, fontSize: 36, fontWeight: 900, color: theme.palette.primary, lineHeight: 1.15, textShadow: '0 3px 12px rgba(0,0,0,0.7)' }}>
          <span style={{ position: 'relative', zIndex: 2 }}>{text}</span>
          <span style={{ position: 'absolute', left: 0, bottom: 2, width: `${wipe * 100}%`, height: 10, background: rgba(theme.palette.accent, 0.5), borderRadius: 2, zIndex: 1 }} />
        </div>
        {subtext && (
          <div style={{ marginTop: 6, fontFamily: theme.font_family, fontSize: 15, fontWeight: 600, color: rgba(theme.palette.primary, 0.78), letterSpacing: 0.5 }}>
            {subtext}
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
}

