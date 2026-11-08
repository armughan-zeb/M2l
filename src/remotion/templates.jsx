/*
 * Documentary Motion Engine – reusable, data-driven Remotion treatments.
 */
import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { rgba } from './theme';
import { TextCard } from './components/TextCard';
import {
  ChapterOpener as ChapterOpenerEnhanced,
  KeywordReveal as KeywordRevealEnhanced,
  StatCounter as StatCounterEnhanced,
  MoneyCounter as MoneyCounterEnhanced,
  DateCard as DateCardEnhanced,
  ColdOpen as ColdOpenEnhanced,
  EndCard as EndCardEnhanced,
  CauseEffect as CauseEffectEnhanced,
  EvidenceCallout as EvidenceCalloutEnhanced,
  QuoteCard as QuoteCardEnhanced,
  LowerThird as LowerThirdEnhanced,
  BlueprintReveal as BlueprintRevealEnhanced,
  KineticStatement as KineticStatementEnhanced,
  CalendarRip as CalendarRipEnhanced,
  WordCascade as WordCascadeEnhanced,
  MapRoute as MapRouteEnhanced,
  ListicleBuild as ListicleBuildEnhanced,
  MapComparison as MapComparisonEnhanced,
  MetricGrid as MetricGridEnhanced,
  DossierCard as DossierCardEnhanced,
  ProcessFlow as ProcessFlowEnhanced,
  MediaFocusCard as MediaFocusCardEnhanced,
  DataFactCard as DataFactCardEnhanced,
  CinematicHighlight as CinematicHighlightEnhanced,
} from './templates-enhanced';

const clamp = (value, low = 0, high = 1) => Math.max(low, Math.min(high, value));
const text = (value, fallback = '') => String(value ?? fallback).replace(/\s+/g, ' ').trim();
const words = (value, count = 8) => text(value).split(' ').slice(0, count).join(' ');
const normalizeTemplateId = (value) => String(value || 'full-bleed').trim().toLowerCase().replace(/_/g, '-');
const normalizeGraphicProps = (value) => {
  const props = { ...(value || {}) };
  for (const key of ['title', 'text', 'name', 'label', 'kicker', 'headline', 'deck', 'location', 'date', 'value', 'unit', 'left', 'right', 'from', 'to', 'role', 'subtitle']) {
    if (typeof props[key] === 'string') props[key] = props[key].trim().toUpperCase();
  }
  return props;
};

const FONT_FAMILY_BY_FILE = {
  'Anton-Regular.ttf': 'Anton',
  'BebasNeue-Regular.ttf': 'Bebas Neue',
  'Montserrat-Bold.ttf': 'Montserrat',
  'Poppins-Regular.ttf': 'Poppins',
  'OpenSans-Bold.ttf': 'Open Sans',
  'Roboto-Bold.ttf': 'Roboto',
  'PlayfairDisplay-Bold.ttf': 'Playfair Display',
  'Cinzel-Bold.ttf': 'Cinzel',
  'Orbitron-Bold.ttf': 'Orbitron',
  'Caveat-Bold.ttf': 'Caveat',
  'Creepster-Regular.ttf': 'Creepster',
  'Nosifer-Regular.ttf': 'Nosifer',
  'Inter-SemiBold.ttf': 'Inter',
  'DMSans-Bold.ttf': 'DM Sans',
  'PlusJakartaSans-Bold.ttf': 'Plus Jakarta Sans',
  'ArchivoBlack-Regular.ttf': 'Archivo Black',
  'Oswald-SemiBold.ttf': 'Oswald',
  'JetBrainsMono-Bold.ttf': 'JetBrains Mono',
  'Lora-Bold.ttf': 'Lora',
};

function resolveGraphicTheme(theme, props) {
  const display = FONT_FAMILY_BY_FILE[String(props?._contract_font || '')] || theme.display_font;
  const body = FONT_FAMILY_BY_FILE[String(props?._body_font || '')] || theme.font_family;
  const accent = FONT_FAMILY_BY_FILE[String(props?._accent_font || '')] || display;
  return {
    ...theme,
    display_font: `${display}, Arial, sans-serif`,
    font_family: `${body}, Arial, sans-serif`,
    accent_font: `${accent}, Arial, sans-serif`,
  };
}

// These anchors are copied from the 472x263 placement guide used by the
// native renderer. Keep the same five green slots in the browser preview.
const TEXT_CARD_GUIDE = {
  top_left: { x: 10 / 472, y: (10 + 40) / 2 / 263, horizontal: 'left' },
  top_right: { x: 457 / 472, y: (10 + 40) / 2 / 263, horizontal: 'right' },
  center: { x: (178 + 300) / 2 / 472, y: (103 + 145) / 2 / 263, horizontal: 'center' },
  bottom_left: { x: 10 / 472, y: (225 + 255) / 2 / 263, horizontal: 'left' },
  bottom_right: { x: 457 / 472, y: (230 + 260) / 2 / 263, horizontal: 'right' },
};
const normalizeTextCardPosition = (value) => {
  const candidate = String(value || '').trim().toLowerCase().replace(/-/g, '_');
  return TEXT_CARD_GUIDE[candidate] ? candidate : 'center';
};

function useMotion({ delay = 0, duration = 16, damping = 18, mass = .65 } = {}) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = Math.max(0, frame - delay);
  return {
    frame,
    enter: spring({ frame: local, fps, config: { damping, mass, stiffness: 120 } }),
    reveal: clamp(local / Math.max(1, duration)),
    fade: interpolate(local, [0, duration], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }),
  };
}

function Texture({ theme, strength = .22 }) {
  return <AbsoluteFill style={{
    pointerEvents: 'none', opacity: strength,
    backgroundImage: `repeating-linear-gradient(0deg, transparent 0 3px, ${rgba(theme.palette.primary, .035)} 3px 4px), radial-gradient(circle at 20% 18%, ${rgba(theme.palette.accent, .18)} 0 1px, transparent 1.5px)`,
    backgroundSize: '100% 5px, 13px 13px', mixBlendMode: 'screen',
  }} />;
}

function Kicker({ children, theme, style = {} }) {
  return <div style={{ color: theme.palette.accent, fontFamily: theme.font_family, fontSize: 19, fontWeight: 800, letterSpacing: 3.8, textTransform: 'uppercase', ...style }}>{children}</div>;
}

function SourceBadge({ source, theme, style = {} }) {
  if (!source) return null;
  return <div style={{ fontFamily: theme.font_family, fontWeight: 700, color: rgba(theme.palette.primary, .82), background: rgba(theme.palette.background, .72), border: `1px solid ${rgba(theme.palette.primary, .25)}`, padding: '7px 11px', borderRadius: 2, fontSize: 12, letterSpacing: 1.2, textTransform: 'uppercase', ...style }}>SOURCE · {source}</div>;
}

function TornPaper({ theme, children, style = {} }) {
  return <div style={{
    position: 'relative', background: theme.palette.primary, color: theme.palette.background,
    clipPath: 'polygon(0 3%, 5% 0, 12% 2%, 18% 0, 29% 2%, 37% 0, 47% 2%, 56% 0, 66% 2%, 75% 0, 84% 2%, 94% 0, 100% 3%, 99% 96%, 91% 100%, 83% 98%, 72% 100%, 61% 98%, 49% 100%, 39% 98%, 27% 100%, 17% 98%, 7% 100%, 0 96%)',
    boxShadow: `0 24px 44px ${rgba(theme.palette.background, .52)}`, ...style,
  }}>{children}</div>;
}

function Tape({ theme, rotate = 0, style = {} }) {
  return <div style={{ width: 94, height: 26, opacity: .78, transform: `rotate(${rotate}deg)`, background: 'rgba(230,211,162,.55)', border: '1px solid rgba(103,86,54,.25)', ...style }} />;
}

function Chapter({ props, theme }) {
  const { enter, fade } = useMotion({ duration: 18, damping: 17 });
  return <AbsoluteFill style={{ justifyContent: 'center', padding: '7%', overflow: 'hidden' }}>
    <Texture theme={theme} strength={.36} />
    <TornPaper theme={theme} style={{ width: '78%', padding: '7.4% 8%', transform: `translateY(${(1 - enter) * 55}px) rotate(${(1 - enter) * -1.2}deg)`, opacity: fade }}>
      <Kicker theme={theme} style={{ color: theme.palette.accent }}>{props.kicker || 'CHAPTER'}</Kicker>
      <div style={{ marginTop: 22, maxWidth: '94%', fontFamily: theme.display_font, fontWeight: 900, fontSize: 76, lineHeight: .93, letterSpacing: -2.4 }}>{words(props.title || 'THE STORY', 8)}</div>
      <div style={{ width: `${Math.max(70, enter * 315)}px`, height: 9, background: theme.palette.accent, marginTop: 32 }} />
    </TornPaper>
  </AbsoluteFill>;
}

function ColdOpen({ props, theme }) {
  const { frame, enter } = useMotion({ duration: 20, damping: 16 });
  const x = interpolate(frame, [0, 24], [-110, 0], { extrapolateRight: 'clamp' });
  return <AbsoluteFill style={{ padding: '7%', justifyContent: 'end', overflow: 'hidden', background: `linear-gradient(120deg, ${rgba(theme.palette.background, .86)}, ${rgba(theme.palette.background, .14)})` }}>
    <Texture theme={theme} strength={.42} />
    <div style={{ position: 'absolute', top: '10%', right: '7%', width: 170, height: 170, borderRadius: '50%', border: `16px solid ${rgba(theme.palette.accent, .9)}`, transform: `scale(${.5 + enter * .5}) rotate(${frame * 3}deg)`, opacity: .82 }} />
    <div style={{ transform: `translateX(${x}px)`, maxWidth: '72%' }}>
      <Kicker theme={theme}>{props.kicker || 'THE QUESTION'}</Kicker>
      <div style={{ marginTop: 18, color: theme.palette.primary, fontFamily: theme.display_font, fontSize: 84, lineHeight: .9, fontWeight: 900, letterSpacing: -3 }}>{words(props.title || 'WHAT REALLY HAPPENED?', 9)}</div>
      <div style={{ width: 420, maxWidth: '100%', height: 12, marginTop: 30, background: theme.palette.accent, transform: `scaleX(${enter})`, transformOrigin: 'left' }} />
      <div style={{ marginTop: 24, color: theme.palette.primary, fontFamily: theme.font_family, fontSize: 25, lineHeight: 1.28, maxWidth: 680 }}>{words(props.subtitle, 20)}</div>
    </div>
  </AbsoluteFill>;
}

function Keyword({ props, theme }) {
  const { frame, enter } = useMotion({ duration: 16, damping: 14, mass: .52 });
  const headline = words(props.text || 'KEY IDEA', 6).toUpperCase();
  const lineWidth = clamp((frame - 5) / 18) * 100;
  return <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', padding: '8%', overflow: 'hidden' }}>
    <div style={{ width: '85%', textAlign: 'center', transform: `scale(${.86 + enter * .14}) rotate(${(1 - enter) * -1.2}deg)` }}>
      <div style={{ display: 'inline', boxDecorationBreak: 'clone', WebkitBoxDecorationBreak: 'clone', color: theme.palette.primary, fontFamily: theme.display_font, fontSize: 92, lineHeight: .93, fontWeight: 950, letterSpacing: -3, textShadow: `6px 7px 0 ${rgba(theme.palette.background, .65)}` }}>{headline}</div>
      <div style={{ width: `${lineWidth}%`, maxWidth: '82%', height: 14, margin: '30px auto 0', background: theme.palette.accent }} />
      {props.detail && <div style={{ margin: '24px auto 0', color: theme.palette.primary, fontFamily: theme.font_family, fontSize: 23, lineHeight: 1.3, maxWidth: 760 }}>{words(props.detail, 22)}</div>}
    </div>
  </AbsoluteFill>;
}

function DateCard({ props, theme }) {
  const { frame, enter } = useMotion({ duration: 18 });
  return <AbsoluteFill style={{ padding: '9%', justifyContent: 'center', background: `linear-gradient(125deg, ${theme.palette.background}, ${rgba(theme.palette.surface, .86)})`, overflow: 'hidden' }}>
    <Texture theme={theme} strength={.34} />
    <div style={{ position: 'absolute', left: '9%', right: '9%', top: '49%', height: 2, background: rgba(theme.palette.primary, .22) }} />
    <div style={{ transform: `translateY(${(1 - enter) * 42}px)` }}>
      <Kicker theme={theme}>{props.kicker || 'DOCUMENTARY TIMELINE'}</Kicker>
      <div style={{ marginTop: 22, color: theme.palette.primary, fontFamily: theme.display_font, fontWeight: 900, fontSize: 104, letterSpacing: -3 }}>{props.label || '1912'}</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginTop: 30 }}><div style={{ width: Math.min(420, 40 + frame * 16), height: 10, background: theme.palette.accent }} /><div style={{ width: 18, height: 18, borderRadius: '50%', background: theme.palette.primary, boxShadow: `0 0 0 7px ${rgba(theme.palette.accent, .24)}` }} /></div>
    </div>
  </AbsoluteFill>;
}

function ProfileDossier({ props, theme }) {
  const { enter } = useMotion({ duration: 18, damping: 17 });
  // This is intentionally an information plate over the real source media.
  // It does not fabricate a portrait aperture, evidence strings, or pins.
  return <AbsoluteFill style={{ justifyContent: 'end', padding: '0 7% 10%', pointerEvents: 'none' }}>
    <div style={{ alignSelf: 'flex-start', maxWidth: '52%', padding: '23px 30px 25px', borderLeft: `10px solid ${theme.palette.accent}`, background: rgba(theme.palette.background, .88), boxShadow: `0 20px 48px ${rgba(theme.palette.background, .42)}`, transform: `translateX(${(1 - enter) * -460}px)` }}>
      <Kicker theme={theme}>{props.subtitle || 'PROFILE'}</Kicker>
      <div style={{ marginTop: 11, color: theme.palette.primary, fontFamily: theme.display_font, fontWeight: 900, fontSize: 49, lineHeight: .94 }}>{words(props.title || 'SUBJECT', 5)}</div>
      {props.detail && <div style={{ marginTop: 13, color: rgba(theme.palette.primary, .72), fontFamily: theme.font_family, fontSize: 18, lineHeight: 1.3 }}>{words(props.detail, 18)}</div>}
    </div>
  </AbsoluteFill>;
}

function EvidenceBoard({ props, theme }) {
  const { frame, enter } = useMotion({ duration: 20, damping: 17 });
  const draw = clamp((frame - 5) / 22);
  return <AbsoluteFill style={{ alignItems: 'center', justifyContent: 'center', padding: '7%', overflow: 'hidden', background: rgba(theme.palette.background, .5) }}>
    <Texture theme={theme} strength={.3} />
    <svg viewBox="0 0 1000 620" style={{ position: 'absolute', width: '100%', height: '100%' }}><path d="M95 85 L500 310 L910 110 M90 530 L500 310 L910 535" stroke={theme.palette.danger || '#b33'} strokeWidth="6" fill="none" strokeDasharray="1600" strokeDashoffset={1600 - draw * 1600} /></svg>
    <TornPaper theme={theme} style={{ width: '62%', padding: '50px 58px', transform: `scale(${.82 + enter * .18}) rotate(${(1 - enter) * -1.4}deg)` }}>
      <Kicker theme={theme} style={{ color: theme.palette.danger || '#b33' }}>{props.stamp || 'EVIDENCE'}</Kicker>
      <div style={{ marginTop: 16, fontFamily: theme.display_font, fontWeight: 900, fontSize: 56, lineHeight: .96 }}>{words(props.text || 'THE RECORD DID NOT MATCH', 9)}</div>
      <div style={{ marginTop: 22, fontFamily: theme.font_family, fontSize: 22, lineHeight: 1.32, maxWidth: '88%' }}>{words(props.detail, 26)}</div>
      <SourceBadge theme={theme} source={props.source} style={{ position: 'absolute', right: 34, bottom: 28 }} />
      <Tape theme={theme} rotate={8} style={{ position: 'absolute', right: 10, top: -10 }} />
    </TornPaper>
  </AbsoluteFill>;
}

function Paper({ props, theme, highlighted = false }) {
  const { frame, enter } = useMotion({ duration: 18, damping: 18 });
  const marker = clamp((frame - 18) / 18);
  const body = words(props.body || 'A source-backed detail appears here.', 34);
  return <AbsoluteFill style={{ padding: '8%', justifyContent: 'center', alignItems: 'center', overflow: 'hidden' }}>
    <TornPaper theme={theme} style={{ width: '68%', minHeight: '50%', padding: '6% 7%', transform: `translateY(${(1 - enter) * 65}px) rotate(${(1 - enter) * -2}deg)` }}>
      <div style={{ color: theme.palette.danger || '#9e382e', fontFamily: theme.font_family, fontWeight: 900, letterSpacing: 3, fontSize: 17 }}>{props.title || 'SOURCE NOTE'}</div>
      <div style={{ width: '26%', height: 5, margin: '20px 0 28px', background: theme.palette.accent }} />
      <div style={{ position: 'relative', fontFamily: 'Georgia, serif', fontSize: 31, lineHeight: 1.38, maxWidth: '95%' }}>{body}
        {highlighted && <div style={{ position: 'absolute', left: 0, right: `${Math.max(0, 72 - marker * 72)}%`, bottom: 8, height: 28, background: rgba(theme.palette.accent, .55), mixBlendMode: 'multiply' }} />}
      </div>
      <SourceBadge theme={theme} source={props.source} style={{ marginTop: 34, display: 'inline-block' }} />
      <Tape theme={theme} rotate={-8} style={{ position: 'absolute', top: -10, left: '42%' }} />
    </TornPaper>
  </AbsoluteFill>;
}

function Quote({ props, theme }) {
  const { frame, enter } = useMotion({ duration: 18, damping: 16 });
  return <AbsoluteFill style={{ justifyContent: 'center', padding: '11%', background: `linear-gradient(120deg, ${rgba(theme.palette.background, .94)}, ${rgba(theme.palette.surface, .75)})` }}>
    <div style={{ position: 'absolute', top: '10%', left: '8%', color: rgba(theme.palette.accent, .72), fontFamily: 'Georgia, serif', fontSize: 250, lineHeight: .55, transform: `translateY(${(1 - enter) * -34}px)` }}>“</div>
    <div style={{ color: theme.palette.primary, fontFamily: theme.display_font, fontSize: 62, lineHeight: 1.08, maxWidth: '85%', transform: `translateY(${(1 - enter) * 38}px)`, opacity: clamp(frame / 16) }}>{words(props.quote || 'The record speaks for itself.', 32)}</div>
    {props.attribution && <div style={{ marginTop: 26, color: theme.palette.accent, fontFamily: theme.font_family, fontSize: 21, letterSpacing: 2 }}>— {props.attribution}</div>}
  </AbsoluteFill>;
}

function AnimatedNumber({ value, theme }) {
  const { enter } = useMotion({ duration: 24, damping: 20, mass: .58 });
  const raw = text(value, '0');
  const match = raw.match(/[\d,.]+/);
  if (!match) return raw;
  const number = Number(match[0].replace(/,/g, '')) || 0;
  const display = Math.round(number * enter).toLocaleString();
  return <>{raw.replace(match[0], display)}</>;
}

function Counter({ props, theme, money = false }) {
  const { enter } = useMotion({ duration: 20, damping: 18 });
  return <AbsoluteFill style={{ padding: '10%', justifyContent: 'center', background: `radial-gradient(circle at 77% 40%, ${rgba(theme.palette.accent, .2)}, transparent 28%), ${theme.palette.background}` }}>
    <Texture theme={theme} strength={.22} />
    <Kicker theme={theme}>{props.label || (money ? 'VALUE' : 'THE NUMBERS')}</Kicker>
    <div style={{ color: money ? theme.palette.accent : theme.palette.primary, fontFamily: theme.display_font, fontSize: 144, lineHeight: .9, fontWeight: 900, letterSpacing: -5, marginTop: 22, transform: `translateX(${(1 - enter) * -110}px)` }}><AnimatedNumber value={props.value || '0'} theme={theme} /></div>
    {props.unit && <div style={{ marginTop: 14, color: theme.palette.primary, fontFamily: theme.font_family, fontSize: 28 }}>{props.unit}</div>}
  </AbsoluteFill>;
}

function Chart({ props, theme, line = false }) {
  const frame = useCurrentFrame();
  const input = line ? (props.points || []) : (props.items || []).map((item) => Number(item.value) || 0);
  const values = input.length ? input : [22, 43, 65];
  const max = Math.max(1, ...values);
  const reveal = clamp(frame / 28);
  const points = values.map((value, index) => [95 + index * (720 / Math.max(1, values.length - 1)), 430 - value / max * 285]);
  const path = points.map(([x, y], index) => `${index ? 'L' : 'M'}${x} ${y}`).join(' ');
  return <AbsoluteFill style={{ padding: '8.5%', background: `linear-gradient(145deg, ${theme.palette.background}, ${theme.palette.surface})` }}>
    <Kicker theme={theme}>{props.label || (line ? 'TREND' : 'COMPARISON')}</Kicker>
    <svg viewBox="0 0 900 520" style={{ width: '91%', height: '72%', marginTop: 22 }}>
      {[0, 1, 2, 3].map((row) => <line key={row} x1="80" x2="850" y1={130 + row * 100} y2={130 + row * 100} stroke={rgba(theme.palette.primary, .14)} strokeWidth="2" />)}
      {line ? <><path d={path} fill="none" stroke={theme.palette.accent} strokeWidth="11" strokeLinecap="round" strokeLinejoin="round" strokeDasharray="1500" strokeDashoffset={1500 - reveal * 1500} />{points.map(([x, y], index) => <circle key={index} cx={x} cy={y} r={index / Math.max(1, points.length - 1) <= reveal ? 11 : 0} fill={theme.palette.primary} />)}</> : values.map((value, index) => <g key={index}><rect x={110 + index * (650 / values.length)} y={430 - value / max * 285 * reveal} width={Math.max(35, 480 / values.length)} height={Math.max(3, value / max * 285 * reveal)} rx="8" fill={index === values.length - 1 ? theme.palette.accent : theme.palette.secondary} /><text x={145 + index * (650 / values.length)} y="474" textAnchor="middle" fill={theme.palette.primary} fontSize="22" fontFamily={theme.font_family}>{(props.items || [])[index]?.label || index + 1}</text></g>)}
    </svg>
  </AbsoluteFill>;
}

function Timeline({ props, theme }) {
  const frame = useCurrentFrame();
  const events = Array.isArray(props.events) && props.events.length ? props.events : [{ label: 'EVENT', year: 'NOW' }];
  const reveal = clamp(frame / 26);
  return <AbsoluteFill style={{ padding: '10%', background: theme.palette.background }}>
    <Kicker theme={theme}>{props.label || 'TIMELINE'}</Kicker>
    <div style={{ position: 'relative', height: 10, marginTop: '24%', background: rgba(theme.palette.primary, .22) }}>
      <div style={{ width: `${reveal * 100}%`, height: '100%', background: theme.palette.accent }} />
      {events.slice(0, 5).map((event, index) => { const position = index / Math.max(1, events.length - 1) * 100; const visible = position / 100 <= reveal; return <div key={index} style={{ position: 'absolute', left: `${position}%`, top: -19, transform: 'translateX(-50%)', opacity: visible ? 1 : 0, transition: 'none' }}><div style={{ width: 42, height: 42, borderRadius: '50%', background: visible ? theme.palette.accent : theme.palette.surface, border: `5px solid ${theme.palette.primary}` }} /><div style={{ marginTop: 16, width: 160, transform: 'translateX(-36%)', color: theme.palette.primary, fontFamily: theme.font_family, fontSize: 18, textAlign: 'center' }}><b>{event.year || ''}</b><br />{event.label || ''}</div></div>; })}
    </div>
  </AbsoluteFill>;
}

function MapRoute({ props, theme }) {
  const frame = useCurrentFrame();
  const draw = clamp(frame / 30);
  return <AbsoluteFill style={{ padding: '7%', background: `radial-gradient(circle at 65% 35%, ${rgba(theme.palette.secondary, .22)}, transparent 33%), ${theme.palette.background}` }}>
    <Texture theme={theme} strength={.18} /><Kicker theme={theme}>GEOGRAPHY · {props.label || 'LOCATION'}</Kicker>
    <div style={{ position: 'relative', flex: 1, marginTop: 24, overflow: 'hidden', border: `1px solid ${rgba(theme.palette.primary, .22)}`, background: rgba(theme.palette.surface, .74) }}>
      <svg viewBox="0 0 1000 600" style={{ width: '100%', height: '100%' }}>
        {[120,240,360,480].map((y) => <path key={y} d={`M0 ${y} C250 ${y-40} 600 ${y+55} 1000 ${y-20}`} fill="none" stroke={rgba(theme.palette.primary, .1)} strokeWidth="2" />)}
        <path d="M80 470 C220 365 295 485 470 310 S700 150 900 95" fill="none" stroke={rgba(theme.palette.primary, .12)} strokeWidth="54" />
        <path d="M80 470 C220 365 295 485 470 310 S700 150 900 95" fill="none" stroke={theme.palette.accent} strokeWidth="8" strokeDasharray="1300" strokeDashoffset={1300 - draw * 1300} strokeLinecap="round" />
        <circle cx="80" cy="470" r="18" fill={theme.palette.primary} /><circle cx="900" cy="95" r={18 + Math.sin(frame / 4) * 3} fill={theme.palette.accent} />
      </svg>
      <div style={{ position: 'absolute', left: '7%', bottom: '9%', color: theme.palette.primary, fontFamily: theme.display_font, fontSize: 32 }}>{props.from || 'START'} <span style={{ color: theme.palette.accent }}>→</span> {props.to || 'DESTINATION'}</div>
    </div>
  </AbsoluteFill>;
}

function Listicle({ props, theme }) {
  const frame = useCurrentFrame();
  const items = Array.isArray(props.items) && props.items.length ? props.items : ['THE STORY', 'THE EVIDENCE', 'THE CONSEQUENCE'];
  return <AbsoluteFill style={{ padding: '8%', justifyContent: 'center', background: rgba(theme.palette.background, .42) }}>
    <TornPaper theme={theme} style={{ width: '70%', padding: '5% 7%' }}><Kicker theme={theme} style={{ color: theme.palette.danger || '#b33' }}>{props.title || 'KEY POINTS'}</Kicker>{items.slice(0, 4).map((item, index) => <div key={index} style={{ display: 'flex', alignItems: 'center', gap: 18, marginTop: 25, opacity: clamp((frame - index * 9) / 12), transform: `translateX(${Math.max(0, 1 - clamp((frame - index * 9) / 12)) * -60}px)` }}><div style={{ width: 42, height: 42, borderRadius: '50%', flexShrink: 0, display: 'grid', placeItems: 'center', background: theme.palette.accent, color: theme.palette.background, fontFamily: theme.display_font, fontWeight: 900 }}>{index + 1}</div><div style={{ fontFamily: theme.display_font, fontWeight: 900, fontSize: 33 }}>{words(item, 7)}</div></div>)}</TornPaper>
  </AbsoluteFill>;
}

function CauseEffect({ props, theme }) {
  const { enter } = useMotion({ duration: 18, damping: 15 });
  return <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', padding: '9%', background: `linear-gradient(135deg, ${theme.palette.background}, ${theme.palette.surface})` }}>
    <Kicker theme={theme} style={{ position: 'absolute', top: '12%', left: '10%' }}>{props.label || 'CAUSE → EFFECT'}</Kicker>
    <div style={{ display: 'flex', alignItems: 'center', gap: 28, width: '88%' }}><div style={{ flex: 1, minHeight: 190, padding: 32, display: 'grid', placeItems: 'center', background: rgba(theme.palette.surface, .92), border: `2px solid ${rgba(theme.palette.primary, .25)}`, color: theme.palette.primary, fontFamily: theme.display_font, fontWeight: 900, fontSize: 36, textAlign: 'center', transform: `translateX(${(1 - enter) * -90}px)` }}>{words(props.cause || 'THE EVENT', 9)}</div><div style={{ color: theme.palette.accent, fontSize: 80, transform: `scale(${enter})` }}>→</div><div style={{ flex: 1, minHeight: 190, padding: 32, display: 'grid', placeItems: 'center', background: theme.palette.primary, color: theme.palette.background, fontFamily: theme.display_font, fontWeight: 900, fontSize: 36, textAlign: 'center', transform: `translateX(${(1 - enter) * 90}px)` }}>{words(props.effect || 'THE OUTCOME', 9)}</div></div>
  </AbsoluteFill>;
}

function KineticStatement({ props, theme }) {
  const { frame, enter } = useMotion({ duration: 18, damping: 15, mass: .56 });
  const chunks = words(props.text || 'THE STORY CHANGED', 8).toUpperCase().split(' ');
  const pivot = Math.max(0, Math.min(chunks.length - 1, Math.floor(chunks.length / 2)));
  return <AbsoluteFill style={{ justifyContent: 'center', padding: '8%', overflow: 'hidden' }}>
    <Texture theme={theme} strength={.28} />
    <Kicker theme={theme} style={{ position: 'absolute', top: '12%', left: '9%' }}>{props.accent || 'EDITORIAL NOTE'}</Kicker>
    <div style={{ display: 'flex', flexWrap: 'wrap', columnGap: 20, rowGap: 8, maxWidth: '84%', alignItems: 'baseline' }}>
      {chunks.map((word, index) => {
        const local = clamp((frame - index * 4) / 13);
        const accent = index === pivot;
        return <span key={`${word}-${index}`} style={{ display: 'inline-block', color: accent ? theme.palette.background : theme.palette.primary, background: accent ? theme.palette.accent : 'transparent', padding: accent ? '5px 13px 8px' : 0, fontFamily: theme.display_font, fontWeight: 950, fontSize: 88, letterSpacing: -3.3, lineHeight: .88, transform: `translateY(${(1 - local) * (index % 2 ? 50 : -45)}px) rotate(${(1 - local) * (index % 2 ? 2.4 : -2.4)}deg)`, opacity: local }}>{word}</span>;
      })}
    </div>
    <div style={{ marginTop: 32, width: `${enter * 42}%`, minWidth: 100, height: 7, background: theme.palette.accent }} />
    {props.detail && <div style={{ marginTop: 20, maxWidth: 710, color: rgba(theme.palette.primary, .86), fontFamily: theme.font_family, fontSize: 23, lineHeight: 1.33 }}>{words(props.detail, 23)}</div>}
  </AbsoluteFill>;
}

function NewsprintReveal({ props, theme }) {
  const { frame, enter } = useMotion({ duration: 20, damping: 17 });
  const wipe = clamp((frame - 11) / 18);
  return <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', padding: '7%', overflow: 'hidden' }}>
    <Texture theme={theme} strength={.42} />
    <div style={{ position: 'absolute', width: '78%', height: '74%', background: '#e9dfc8', color: '#17140f', padding: '5% 6%', transform: `rotate(${(1 - enter) * -3.2}deg) translateY(${(1 - enter) * 85}px)`, boxShadow: `0 28px 60px ${rgba(theme.palette.background, .56)}`, clipPath: 'polygon(1% 2%, 14% 0, 31% 2%, 49% 0, 64% 2%, 83% 0, 99% 3%, 97% 98%, 82% 100%, 59% 98%, 43% 100%, 23% 98%, 2% 100%)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '3px solid #17140f', paddingBottom: 12, fontFamily: 'Georgia, serif', fontWeight: 900, fontSize: 16, letterSpacing: 2 }}><span>{props.label || 'ARCHIVE REPORT'}</span><span>ISSUE 01</span></div>
      <div style={{ marginTop: 26, fontFamily: 'Georgia, serif', fontWeight: 900, fontSize: 64, lineHeight: .9, letterSpacing: -2.4 }}>{words(props.headline || 'THE WARNING WAS ALREADY THERE', 10)}</div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.5fr', gap: 26, marginTop: 26, fontFamily: 'Georgia, serif', fontSize: 20, lineHeight: 1.38 }}><div style={{ columnCount: 1 }}>{words(props.deck || 'The report gives the central detail.', 24)}</div><div style={{ height: 144, background: 'repeating-linear-gradient(0deg, rgba(23,20,15,.15) 0 1px, transparent 1px 7px)', overflow: 'hidden' }}><div style={{ width: `${wipe * 95}%`, height: 33, marginTop: 46, background: rgba(theme.palette.accent, .72), mixBlendMode: 'multiply' }} /></div></div>
      <SourceBadge theme={theme} source={props.source} style={{ position: 'absolute', right: 38, bottom: 30, color: '#17140f', background: 'rgba(255,255,255,.58)' }} />
    </div>
  </AbsoluteFill>;
}

function SignalScan({ props, theme }) {
  const frame = useCurrentFrame();
  const scan = (frame % 72) / 72;
  const wave = Array.from({ length: 55 }, (_, index) => `${index ? 'L' : 'M'}${70 + index * 16} ${300 + Math.sin(index * .8 + frame * .27) * (22 + (index % 7) * 5)}`).join(' ');
  return <AbsoluteFill style={{ padding: '7%', background: `radial-gradient(circle at 68% 38%, ${rgba(theme.palette.secondary, .2)}, transparent 33%), ${theme.palette.background}`, overflow: 'hidden' }}>
    <Texture theme={theme} strength={.16} /><Kicker theme={theme}>{props.label || 'WIRELESS LOG'}</Kicker>
    <svg viewBox="0 0 1000 600" style={{ width: '100%', height: '76%', marginTop: 18 }}>
      {[100, 200, 300, 400, 500].map((y) => <line key={y} x1="45" x2="955" y1={y} y2={y} stroke={rgba(theme.palette.primary, .11)} strokeWidth="2" />)}
      {[100, 250, 400, 550, 700, 850].map((x) => <line key={x} x1={x} x2={x} y1="55" y2="545" stroke={rgba(theme.palette.primary, .08)} strokeWidth="2" />)}
      <path d={wave} fill="none" stroke={theme.palette.accent} strokeWidth="7" strokeLinecap="round" />
      <line x1={45 + scan * 910} x2={45 + scan * 910} y1="55" y2="545" stroke={theme.palette.primary} strokeOpacity=".72" strokeWidth="4" />
      <circle cx="850" cy="130" r={34 + Math.sin(frame / 3) * 3} fill="none" stroke={theme.palette.accent} strokeWidth="5" /><circle cx="850" cy="130" r="7" fill={theme.palette.primary} />
    </svg>
    <div style={{ position: 'absolute', left: '8%', right: '14%', bottom: '10%', color: theme.palette.primary, fontFamily: theme.display_font, fontWeight: 900, fontSize: 48, letterSpacing: -1.5 }}>{words(props.message || 'SIGNAL RECEIVED', 8)}</div>
    {props.detail && <div style={{ position: 'absolute', right: '8%', bottom: '10%', maxWidth: '29%', color: rgba(theme.palette.primary, .72), fontFamily: theme.font_family, fontSize: 17, lineHeight: 1.32, textAlign: 'right' }}>{words(props.detail, 17)}</div>}
  </AbsoluteFill>;
}

function BlueprintReveal({ props, theme }) {
  const frame = useCurrentFrame();
  const draw = clamp(frame / 32);
  return <AbsoluteFill style={{ padding: '7%', background: '#0f2d3a', overflow: 'hidden' }}>
    <div style={{ position: 'absolute', inset: 0, opacity: .38, backgroundImage: 'linear-gradient(rgba(224,242,238,.13) 1px, transparent 1px), linear-gradient(90deg, rgba(224,242,238,.13) 1px, transparent 1px)', backgroundSize: '42px 42px' }} />
    <Kicker theme={{ ...theme, palette: { ...theme.palette, accent: '#85d8d0' } }}>{props.label || 'TECHNICAL EXPLAINER'}</Kicker>
    <svg viewBox="0 0 1000 600" style={{ position: 'absolute', left: '6%', right: '6%', top: '18%', width: '88%', height: '65%' }}>
      <path d="M90 390 L210 255 L405 255 L500 170 L675 255 L855 255 L930 390 L855 438 L210 438 Z" fill="rgba(133,216,208,.08)" stroke="#d8f2ef" strokeWidth="5" strokeDasharray="1800" strokeDashoffset={1800 - draw * 1800} />
      <path d="M210 255 L210 438 M405 255 L405 438 M675 255 L675 438 M855 255 L855 438" stroke="rgba(216,242,239,.74)" strokeWidth="3" strokeDasharray="700" strokeDashoffset={700 - draw * 700} />
      <path d="M100 485 L900 485 M100 475 L100 500 M900 475 L900 500" stroke="#85d8d0" strokeWidth="3" />
      <text x="450" y="530" textAnchor="middle" fill="#d8f2ef" fontSize="22" fontFamily="monospace">SYSTEM DIMENSION / NOT TO SCALE</text>
    </svg>
    <div style={{ position: 'absolute', left: '8%', bottom: '9%', color: '#e9f5ef', fontFamily: theme.display_font, fontWeight: 900, fontSize: 56, lineHeight: .92, maxWidth: '68%' }}>{words(props.title || 'THE SYSTEM', 8)}</div>
    <div style={{ position: 'absolute', right: '8%', bottom: '11%', color: '#85d8d0', fontFamily: theme.font_family, fontSize: 18, maxWidth: '25%', textAlign: 'right' }}>{words(props.detail, 18)}</div>
  </AbsoluteFill>;
}

function DecisionTree({ props, theme }) {
  const { frame, enter } = useMotion({ duration: 22, damping: 16 });
  const node = (delay) => clamp((frame - delay) / 13);
  return <AbsoluteFill style={{ padding: '7%', overflow: 'hidden', background: `radial-gradient(circle at 50% 46%, ${rgba(theme.palette.secondary, .16)}, transparent 36%), ${theme.palette.background}` }}>
    <Kicker theme={theme}>{props.label || 'THE TURNING POINT'}</Kicker>
    <svg viewBox="0 0 1000 560" style={{ width: '100%', height: '80%', marginTop: 5 }}>
      <path d="M500 180 L250 390 M500 180 L750 390" stroke={theme.palette.accent} strokeWidth="8" fill="none" strokeDasharray="800" strokeDashoffset={800 - enter * 800} />
      {[[500, 150, props.root || 'THE DECISION', 0], [250, 405, props.left || 'WAIT', 9], [750, 405, props.right || 'ACT', 15]].map(([x, y, label, delay], index) => { const visible = node(delay); return <g key={index} opacity={visible} transform={`translate(${x} ${y}) scale(${.7 + visible * .3})`}><rect x="-152" y="-52" width="304" height="104" rx="52" fill={index === 0 ? theme.palette.accent : theme.palette.surface} stroke={index === 0 ? theme.palette.accent : theme.palette.primary} strokeWidth="4" /><text textAnchor="middle" dominantBaseline="middle" fill={index === 0 ? theme.palette.background : theme.palette.primary} fontFamily={theme.display_font} fontWeight="900" fontSize="30">{words(label, 4)}</text></g>; })}
    </svg>
  </AbsoluteFill>;
}

function RedactionReveal({ props, theme }) {
  const frame = useCurrentFrame();
  const reveal = clamp((frame - 16) / 20);
  return <AbsoluteFill style={{ justifyContent: 'center', padding: '9%', background: rgba(theme.palette.background, .74), overflow: 'hidden' }}>
    <Texture theme={theme} strength={.28} />
    <div style={{ width: '79%', padding: '7% 8%', background: '#ddd3bc', color: '#16130f', transform: `rotate(${(1 - reveal) * -1.3}deg)`, boxShadow: `0 30px 55px ${rgba(theme.palette.background, .55)}` }}>
      <div style={{ fontFamily: theme.font_family, fontSize: 16, letterSpacing: 3, fontWeight: 900, color: theme.palette.danger || '#a32f25' }}>{props.label || 'RESTRICTED FILE'}</div>
      <div style={{ marginTop: 28, fontFamily: 'Georgia, serif', fontWeight: 900, fontSize: 55, lineHeight: .98 }}>{words(props.text || 'THE DETAIL THAT CHANGED THE STORY', 10)}</div>
      <div style={{ marginTop: 28, fontFamily: 'Georgia, serif', fontSize: 22, lineHeight: 1.5, maxWidth: '85%' }}>{words(props.detail, 32)}</div>
      {[0, 1, 2].map((index) => <div key={index} style={{ position: 'absolute', left: `${12 + index * 12}%`, top: `${49 + index * 11}%`, width: `${58 - index * 8}%`, height: 24, background: '#151515', transformOrigin: 'left', transform: `scaleX(${Math.max(0, 1 - reveal * (index === 1 ? 1 : .72))})` }} />)}
    </div>
  </AbsoluteFill>;
}

function CountdownClock({ props, theme }) {
  const { frame, enter } = useMotion({ duration: 18, damping: 15 });
  const spin = frame * 6.2;
  return <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: theme.palette.background, overflow: 'hidden' }}>
    <Texture theme={theme} strength={.24} /><Kicker theme={theme} style={{ position: 'absolute', top: '12%', left: '9%' }}>{props.label || 'TIME PRESSURE'}</Kicker>
    <div style={{ position: 'relative', width: 360, height: 360, borderRadius: '50%', border: `18px solid ${rgba(theme.palette.primary, .18)}`, boxShadow: `0 0 0 17px ${rgba(theme.palette.accent, .16)}, 0 28px 70px ${rgba(theme.palette.background, .6)}`, transform: `scale(${.72 + enter * .28})` }}>
      {[0, 90, 180, 270].map((deg) => <div key={deg} style={{ position: 'absolute', left: '50%', top: 13, width: 5, height: 20, background: theme.palette.primary, transformOrigin: '50% 167px', transform: `translateX(-50%) rotate(${deg}deg)` }} />)}
      <div style={{ position: 'absolute', left: '50%', top: '50%', width: 8, height: 130, background: theme.palette.accent, transformOrigin: '50% 0', transform: `translate(-50%, 0) rotate(${spin}deg)` }} />
      <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', color: theme.palette.primary, fontFamily: theme.display_font, fontWeight: 900, fontSize: 60 }}>{props.value || '00:40'}</div>
    </div>
    {props.detail && <div style={{ position: 'absolute', bottom: '10%', maxWidth: '62%', textAlign: 'center', color: rgba(theme.palette.primary, .8), fontFamily: theme.font_family, fontSize: 21 }}>{words(props.detail, 22)}</div>}
  </AbsoluteFill>;
}

function PhotoPinboard({ props, theme }) {
  const { frame, enter } = useMotion({ duration: 19, damping: 16 });
  const cards = [[-14, -255, 52], [10, 245, 54], [-4, 0, -76]];
  return <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', overflow: 'hidden', background: `radial-gradient(circle at 50% 50%, ${rgba(theme.palette.surface, .84)}, ${theme.palette.background})` }}>
    <Texture theme={theme} strength={.22} />
    {cards.map(([rotate, x, y], index) => { const local = clamp((frame - index * 7) / 15); return <div key={index} style={{ position: 'absolute', width: '34%', height: '56%', padding: 15, background: '#e7deca', boxShadow: `0 20px 45px ${rgba(theme.palette.background, .64)}`, transform: `translate(${x * local}px, ${y * local}px) rotate(${rotate}deg) scale(${.7 + local * .3})`, opacity: .72 + local * .28 }}><div style={{ height: '79%', background: `linear-gradient(150deg, ${theme.palette.surface}, ${rgba(theme.palette.background, .25)})`, filter: 'grayscale(.86) contrast(1.18)' }} />{index === 2 && <div style={{ marginTop: 15, color: theme.palette.background, fontFamily: theme.display_font, fontSize: 22, fontWeight: 900 }}>{words(props.title || 'ARCHIVE COLLECTION', 5)}</div>}<Tape theme={theme} rotate={index ? 9 : -9} style={{ position: 'absolute', top: -9, left: '36%' }} /></div>; })}
    <div style={{ position: 'absolute', bottom: '8%', color: theme.palette.primary, fontFamily: theme.font_family, fontSize: 19, letterSpacing: 2.5 }}>{props.label || 'FIELD NOTES'} {props.detail ? `· ${words(props.detail, 8)}` : ''}</div>
  </AbsoluteFill>;
}

function DataRace({ props, theme }) {
  const { frame, enter } = useMotion({ duration: 20, damping: 17 });
  const fill = clamp((frame - 4) / 22);
  return <AbsoluteFill style={{ justifyContent: 'center', padding: '9%', background: `linear-gradient(135deg, ${theme.palette.background}, ${theme.palette.surface})` }}>
    <Kicker theme={theme}>{props.label || 'THE RACE'}</Kicker>
    <div style={{ marginTop: 35, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
      {[{ label: props.left || 'TIME', value: .85, color: theme.palette.accent }, { label: props.right || 'DISTANCE', value: .62, color: theme.palette.secondary }].map((item, index) => <div key={index} style={{ padding: '34px 30px', background: rgba(theme.palette.background, .38) }}><div style={{ color: theme.palette.primary, fontFamily: theme.font_family, letterSpacing: 3, fontWeight: 800 }}>{item.label}</div><div style={{ height: 22, marginTop: 28, background: rgba(theme.palette.primary, .13) }}><div style={{ width: `${item.value * fill * 100}%`, height: '100%', background: item.color }} /></div><div style={{ color: item.color, fontFamily: theme.display_font, fontSize: 66, fontWeight: 900, marginTop: 18 }}>{index === 0 ? props.value || '02:40' : `${Math.round(item.value * fill * 100)}%`}</div></div>)}
    </div>
    <div style={{ marginTop: 30, color: rgba(theme.palette.primary, .68), fontFamily: theme.font_family, fontSize: 20 }}>LIVE COMPARISON / SCENE DATA</div>
  </AbsoluteFill>;
}

function FieldCaption({ props, theme }) {
  const { frame, enter } = useMotion({ duration: 15, damping: 17 });
  const variant = Number(props.variant || 0);
  const left = variant % 2 ? 'auto' : '7%';
  const right = variant % 2 ? '7%' : 'auto';
  return <AbsoluteFill style={{ pointerEvents: 'none', overflow: 'hidden' }}>
    <div style={{ position: 'absolute', left, right, bottom: '10%', width: '48%', textAlign: variant % 2 ? 'right' : 'left', transform: `translateY(${(1 - enter) * 62}px)` }}>
      <div style={{ display: 'inline-block', padding: '8px 12px', background: theme.palette.accent, color: theme.palette.background, fontFamily: theme.font_family, letterSpacing: 2.7, fontWeight: 900, fontSize: 14 }}>{props.subtitle || 'FIELD NOTE'}</div>
      <div style={{ marginTop: 13, color: theme.palette.primary, fontFamily: theme.display_font, fontWeight: 900, fontSize: 54, lineHeight: .9, textShadow: `0 3px 16px ${rgba(theme.palette.background, .88)}` }}>{words(props.title || 'LOCATION', 6)}</div>
      {props.detail && <div style={{ marginTop: 14, color: rgba(theme.palette.primary, .78), fontFamily: theme.font_family, fontSize: 19, lineHeight: 1.3 }}>{words(props.detail, 16)}</div>}
    </div>
    <div style={{ position: 'absolute', left: '7%', right: '7%', bottom: `${7 + Math.sin(frame / 11) * .3}%`, height: 2, background: rgba(theme.palette.primary, .34), transform: `scaleX(${enter})`, transformOrigin: variant % 2 ? 'right' : 'left' }} />
  </AbsoluteFill>;
}

function SpotlightLens({ props, theme }) {
  const { frame, enter } = useMotion({ duration: 17, damping: 15 });
  const variant = Number(props.variant || 0);
  const bbox = Array.isArray(props.bbox) && props.bbox.length === 4 ? props.bbox.map(Number) : null;
  if (!bbox || bbox.some(value => !Number.isFinite(value)) || bbox[0] < 0 || bbox[1] < 0 || bbox[2] > 1 || bbox[3] > 1 || bbox[0] >= bbox[2] || bbox[1] >= bbox[3]) return null;
  const cx = (bbox[0] + bbox[2]) * 50;
  const cy = (bbox[1] + bbox[3]) * 50;
  const radius = Math.max(52, Math.min(190, Math.max((bbox[2] - bbox[0]) * 500, (bbox[3] - bbox[1]) * 300) * 1.18)) + Math.sin(frame / 8) * 2;
  return <AbsoluteFill style={{ pointerEvents: 'none', overflow: 'hidden' }}>
    <svg viewBox="0 0 1000 600" style={{ width: '100%', height: '100%' }}><defs><mask id={`spot-${variant}`}><rect width="1000" height="600" fill="white" /><circle cx={cx * 10} cy={cy * 6} r={radius} fill="black" /></mask></defs><rect width="1000" height="600" fill={rgba(theme.palette.background, .54)} mask={`url(#spot-${variant})`} /><circle cx={cx * 10} cy={cy * 6} r={radius * enter} fill="none" stroke={theme.palette.accent} strokeWidth="7" /><circle cx={cx * 10} cy={cy * 6} r={radius + 12} fill="none" stroke={rgba(theme.palette.primary, .45)} strokeWidth="2" strokeDasharray="7 12" /></svg>
    <div style={{ position: 'absolute', left: variant % 2 ? '8%' : '52%', right: variant % 2 ? '50%' : '8%', bottom: '12%', color: theme.palette.primary, fontFamily: theme.display_font, fontSize: 45, lineHeight: .95, fontWeight: 900, transform: `translateY(${(1 - enter) * 42}px)` }}>{words(props.title || 'THE DETAIL', 7)}<div style={{ marginTop: 12, color: theme.palette.accent, fontFamily: theme.font_family, letterSpacing: 2.4, fontSize: 15 }}>{props.label || 'FOCUS'}</div></div>
  </AbsoluteFill>;
}

function ExhibitLabel({ props, theme }) {
  const { enter } = useMotion({ duration: 16, damping: 17 });
  const variant = Number(props.variant || 0);
  const alignRight = variant % 2;
  return <AbsoluteFill style={{ pointerEvents: 'none', justifyContent: 'center', padding: '7%' }}>
    <div style={{ alignSelf: alignRight ? 'flex-end' : 'flex-start', width: '37%', minWidth: 340, padding: '28px 30px', background: rgba(theme.palette.primary, .93), color: theme.palette.background, boxShadow: `0 18px 44px ${rgba(theme.palette.background, .52)}`, transform: `translateX(${(1 - enter) * (alignRight ? 360 : -360)}px)` }}>
      <div style={{ fontFamily: theme.font_family, fontSize: 13, letterSpacing: 3, fontWeight: 900, color: theme.palette.danger || '#a53' }}>EXHIBIT / {String(variant + 1).padStart(2, '0')}</div>
      <div style={{ marginTop: 22, fontFamily: theme.display_font, fontSize: 45, lineHeight: .93, fontWeight: 900 }}>{words(props.title || 'ARCHIVE OBJECT', 6)}</div>
      <div style={{ marginTop: 18, paddingTop: 13, borderTop: `2px solid ${rgba(theme.palette.background, .24)}`, fontFamily: theme.font_family, fontSize: 15, letterSpacing: 2, fontWeight: 800 }}>{props.subtitle || 'DOCUMENTARY'}</div>
      {props.detail && <div style={{ marginTop: 13, fontFamily: theme.font_family, fontSize: 16, lineHeight: 1.35 }}>{words(props.detail, 18)}</div>}
    </div>
  </AbsoluteFill>;
}

function WitnessQuote({ props, theme }) {
  const { frame, enter } = useMotion({ duration: 20, damping: 16 });
  const bars = Array.from({ length: 38 }, (_, index) => 22 + Math.abs(Math.sin(index * .73 + frame * .16)) * 62);
  return <AbsoluteFill style={{ justifyContent: 'center', padding: '9%', background: rgba(theme.palette.background, .4), overflow: 'hidden' }}>
    <Texture theme={theme} strength={.22} /><div style={{ maxWidth: '79%', transform: `translateY(${(1 - enter) * 48}px)` }}><div style={{ color: theme.palette.accent, fontFamily: theme.display_font, fontSize: 110, lineHeight: .5 }}>“</div><div style={{ color: theme.palette.primary, fontFamily: theme.display_font, fontSize: 62, lineHeight: .98, fontWeight: 900 }}>{words(props.quote || 'The witness account changed the story.', 19)}</div><div style={{ display: 'flex', alignItems: 'end', gap: 4, height: 70, marginTop: 32 }}>{bars.map((height, index) => <span key={index} style={{ width: 6, height: height * enter, background: index % 5 ? rgba(theme.palette.primary, .52) : theme.palette.accent }} />)}</div><div style={{ marginTop: 19, color: theme.palette.accent, fontFamily: theme.font_family, letterSpacing: 3, fontWeight: 800, fontSize: 15 }}>{props.attribution || 'ARCHIVE ACCOUNT'}</div></div>
  </AbsoluteFill>;
}

function CalendarRip({ props, theme }) {
  const { frame, enter } = useMotion({ duration: 18, damping: 17 });
  const pages = [2, 1, 0];
  return <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', overflow: 'hidden', background: `radial-gradient(circle at 50% 45%, ${rgba(theme.palette.surface, .8)}, ${theme.palette.background})` }}>
    {pages.map((layer) => <div key={layer} style={{ position: 'absolute', width: '58%', height: '61%', padding: '7% 8%', background: '#ede2c9', color: '#1d1914', transform: `translate(${layer * 16 - 16}px, ${layer * 13 - 15}px) rotate(${(layer - 1) * 2.1}deg) scale(${.9 + enter * .1})`, boxShadow: `0 18px 35px ${rgba(theme.palette.background, .42)}`, opacity: .38 + (2 - layer) * .28 }}><div style={{ fontFamily: theme.font_family, fontWeight: 900, letterSpacing: 3, fontSize: 14 }}>{props.kicker || 'THE NIGHT'}</div>{layer === 0 && <><div style={{ marginTop: 33, fontFamily: theme.display_font, fontWeight: 900, fontSize: 75, lineHeight: .9 }}>{props.label || 'APRIL 15, 1912'}</div><div style={{ position: 'absolute', left: '9%', right: '9%', bottom: '15%', height: 14, background: theme.palette.accent, transform: `scaleX(${clamp((frame - 8) / 18)})`, transformOrigin: 'left' }} /><div style={{ marginTop: 30, fontFamily: theme.font_family, fontSize: 19, lineHeight: 1.3 }}>{words(props.detail, 17)}</div></>}</div>)}
  </AbsoluteFill>;
}

function TerminalLog({ props, theme }) {
  const { frame, enter } = useMotion({ duration: 17, damping: 18 });
  const message = words(props.message || '', 8);
  const cursor = frame % 16 < 8 ? '█' : ' ';
  return <AbsoluteFill style={{ padding: '8%', background: '#0d1412', color: '#b9f0ca', overflow: 'hidden' }}>
    <div style={{ borderTop: '2px solid rgba(185,240,202,.55)', paddingTop: 17, fontFamily: 'Consolas, monospace', fontSize: 17, letterSpacing: 1.3 }}>VIDZEO ARCHIVE TERMINAL // {props.label || ''}</div>
    <div style={{ marginTop: 43, fontFamily: 'Consolas, monospace', fontSize: 23, lineHeight: 1.9, color: 'rgba(185,240,202,.72)' }}>{props.source && <div>&gt; source: {props.source}</div>}</div>
    <div style={{ marginTop: 28, maxWidth: '84%', color: '#e4ffe9', fontFamily: 'Consolas, monospace', fontSize: 52, lineHeight: 1.06, fontWeight: 800, opacity: enter }}>{message}{cursor}</div>
    {props.detail && <div style={{ position: 'absolute', right: '9%', bottom: '10%', maxWidth: '30%', textAlign: 'right', color: 'rgba(185,240,202,.55)', fontFamily: 'Consolas, monospace', fontSize: 15 }}>{words(props.detail, 18)}</div>}
  </AbsoluteFill>;
}

function MetricGrid({ props, theme }) {
  const { enter } = useMotion({ duration: 18, damping: 20 });
  const input = props.metrics || props.items;
  const values = Array.isArray(input) ? input : props.value != null ? [{ value: props.value, label: props.label, prefix: props.prefix, suffix: props.suffix }] : [];
  const metrics = values.filter(item => item && Number.isFinite(Number(item.value))).slice(0, 4);
  if (!metrics.length) return null;
  return <AbsoluteFill style={{ padding: '14% 9%', justifyContent: 'center', background: theme.palette.background }}>
    <div style={{ display: 'grid', gridTemplateColumns: `repeat(${metrics.length}, minmax(0, 1fr))`, gap: 28 }}>
      {metrics.map((item, index) => <div key={index} style={{ minWidth: 0, borderTop: `2px solid ${theme.palette.accent}`, paddingTop: 20, opacity: enter, transform: `translateY(${(1-enter)*12}px)` }}>
        <div style={{ fontFamily: theme.display_font, color: theme.palette.primary, fontSize: 'clamp(28px, 6vw, 100px)', overflowWrap: 'anywhere', lineHeight: 1.25 }}>{item.prefix || ''}{Number(item.value).toLocaleString('en-US')}{item.suffix || ''}</div>
        {item.label && <div style={{ fontFamily: theme.font_family, color: theme.palette.primary, fontSize: 26 }}>{item.label}</div>}
      </div>)}
    </div>
  </AbsoluteFill>;
}

function RouteProgress({ props, theme }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const progress = clamp(frame / Math.max(1, fps * .75));
  // This is a schematic relationship, not an invented geographic flight trace.
  if (!props.from || !props.to || props.route_supported !== true) return null;
  return <AbsoluteFill style={{ padding: '10%', justifyContent: 'center', background: theme.palette.background }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 32, fontFamily: theme.display_font, color: theme.palette.primary, fontSize: 42 }}>
      <span>{props.from}</span><div style={{ flex: 1, borderTop: `3px solid ${theme.palette.accent}`, transform: `scaleX(${progress})`, transformOrigin: 'left' }} /><span>{props.to}</span>
    </div>
  </AbsoluteFill>;
}

function IndexCard({ props, theme }) {
  const { enter } = useMotion({ duration: 16, damping: 17 });
  const variant = Number(props.variant || 0);
  return <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', padding: '8%', overflow: 'hidden' }}><div style={{ width: '67%', padding: '5% 6%', background: '#eee1c6', color: '#1d1812', boxShadow: `0 24px 55px ${rgba(theme.palette.background, .58)}`, transform: `rotate(${(variant % 5 - 2) * .8}deg) scale(${.84 + enter * .16})` }}><div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'Consolas, monospace', fontWeight: 800, letterSpacing: 2, fontSize: 15 }}><span>{props.label || 'CATALOGUE NOTE'}</span><span>REF-{String(variant + 101).padStart(3, '0')}</span></div><div style={{ marginTop: 26, borderTop: '2px solid rgba(29,24,18,.55)', paddingTop: 22, fontFamily: theme.display_font, fontWeight: 900, fontSize: 53, lineHeight: .94 }}>{words(props.title || 'ARCHIVE ENTRY', 8)}</div><div style={{ marginTop: 23, fontFamily: 'Georgia, serif', fontSize: 23, lineHeight: 1.4 }}>{words(props.detail, 30)}</div><div style={{ marginTop: 31, height: 6, width: '42%', background: theme.palette.danger || '#a33' }} /></div></AbsoluteFill>;
}

function StampMark({ props, theme }) {
  const { enter } = useMotion({ duration: 13, damping: 12, mass: .8 });
  const variant = Number(props.variant || 0);
  return <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', pointerEvents: 'none' }}><div style={{ padding: '27px 38px', color: theme.palette.danger || '#bb4a37', border: `8px solid ${theme.palette.danger || '#bb4a37'}`, fontFamily: theme.display_font, fontWeight: 900, fontSize: 72, letterSpacing: 3, transform: `rotate(${variant % 2 ? -8 : 7}deg) scale(${.4 + enter * .6})`, opacity: .18 + enter * .64, mixBlendMode: 'screen' }}>{words(props.title || '', 3)}<div style={{ fontFamily: theme.font_family, fontSize: 16, letterSpacing: 3, textAlign: 'center', marginTop: 8 }}>{props.subtitle || ''}</div></div></AbsoluteFill>;
}

function WordCascade({ props, theme }) {
  const frame = useCurrentFrame();
  const parts = words(props.text || 'TOO FAST TOO LATE', 9).toUpperCase().split(' ');
  return <AbsoluteFill style={{ justifyContent: 'center', padding: '8%', overflow: 'hidden' }}><Texture theme={theme} strength={.2} /><div>{parts.map((part, index) => { const visible = clamp((frame - index * 7) / 13); return <div key={`${part}-${index}`} style={{ marginLeft: `${(index % 3) * 10}%`, color: index % 3 === 1 ? theme.palette.accent : theme.palette.primary, fontFamily: theme.display_font, fontWeight: 900, fontSize: 80 - index * 4, lineHeight: .83, transform: `translateX(${(1 - visible) * (index % 2 ? 260 : -260)}px)`, opacity: visible }}>{part}</div>; })}</div>{props.detail && <div style={{ position: 'absolute', right: '9%', bottom: '11%', maxWidth: '34%', textAlign: 'right', color: rgba(theme.palette.primary, .75), fontFamily: theme.font_family, fontSize: 19 }}>{words(props.detail, 19)}</div>}</AbsoluteFill>;
}

function CaseFile({ props, theme }) {
  const { frame, enter } = useMotion({ duration: 19, damping: 16 });
  return <AbsoluteFill style={{ padding: '7%', justifyContent: 'center', overflow: 'hidden' }}><Texture theme={theme} strength={.25} /><div style={{ width: '72%', padding: '5% 6%', background: rgba(theme.palette.surface, .94), color: theme.palette.primary, borderLeft: `13px solid ${theme.palette.danger || '#b44'}`, transform: `translateX(${(1 - enter) * -420}px)` }}><div style={{ display: 'flex', justifyContent: 'space-between', color: theme.palette.accent, fontFamily: theme.font_family, fontWeight: 900, fontSize: 15, letterSpacing: 3 }}><span>{props.label || 'CASE FILE 04'}</span><span>STATUS: OPEN</span></div><div style={{ marginTop: 25, fontFamily: theme.display_font, fontWeight: 900, fontSize: 62, lineHeight: .9 }}>{words(props.title || 'THE MISSING WARNING', 8)}</div><div style={{ marginTop: 24, display: 'grid', gridTemplateColumns: '1.2fr .8fr', gap: 20 }}><div style={{ fontFamily: theme.font_family, fontSize: 21, lineHeight: 1.4 }}>{words(props.detail, 31)}</div><div style={{ background: `repeating-linear-gradient(0deg, ${rgba(theme.palette.primary, .16)} 0 2px, transparent 2px 12px)`, minHeight: 116 }} /></div><div style={{ marginTop: 23, width: `${30 + (frame % 45)}%`, height: 8, background: theme.palette.danger || '#b44' }} /></div></AbsoluteFill>;
}

function Split({ props, theme }) {
  const frame = useCurrentFrame();
  const wipe = clamp(frame / 24) * 50;
  return <AbsoluteFill style={{ display: 'flex', overflow: 'hidden' }}><div style={{ width: `${wipe}%`, background: theme.palette.surface, color: theme.palette.primary, display: 'grid', placeItems: 'center', padding: 40, fontFamily: theme.display_font, fontWeight: 900, fontSize: 52, textAlign: 'center' }}>{words(props.left || 'BEFORE', 6)}</div><div style={{ flex: 1, background: theme.palette.accent, color: theme.palette.background, display: 'grid', placeItems: 'center', padding: 40, fontFamily: theme.display_font, fontWeight: 900, fontSize: 52, textAlign: 'center' }}>{words(props.right || 'AFTER', 6)}</div></AbsoluteFill>;
}

function ArchivalFrame({ props, theme }) {
  const { enter } = useMotion({ duration: 14 });
  return <AbsoluteFill style={{ pointerEvents: 'none', opacity: .58 + enter * .24 }}>
    <Texture theme={theme} strength={.38} />
    <div style={{ position: 'absolute', left: '6.5%', top: '8%', padding: '9px 13px', background: rgba(theme.palette.background, .76), color: theme.palette.primary, fontFamily: theme.font_family, fontSize: 14, letterSpacing: 2.2, fontWeight: 800 }}>{props.label || 'ARCHIVE'}</div>
  </AbsoluteFill>;
}

function Gallery({ props, theme }) {
  const { frame, enter } = useMotion({ duration: 18 });
  return <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', pointerEvents: 'none' }}>
    {[[-12, -190, 80], [8, 125, -65], [-3, 0, 0]].map(([rotate, x, y], index) => <div key={index} style={{ position: 'absolute', width: '42%', height: '58%', padding: 14, background: theme.palette.primary, boxShadow: `0 22px 38px ${rgba(theme.palette.background, .55)}`, transform: `translate(${x * enter}px, ${y * enter}px) rotate(${rotate}deg) scale(${.76 + enter * .24})`, opacity: index === 2 ? .94 : .72, filter: `contrast(${1 + index * .03})` }}><div style={{ width: '100%', height: '82%', background: rgba(theme.palette.background, .55) }} />{index === 2 && <div style={{ color: theme.palette.background, fontFamily: theme.display_font, fontSize: 24, marginTop: 15 }}>{words(props.title || 'THE EVIDENCE', 6)}</div>}</div>)}
  </AbsoluteFill>;
}

function LowerThird({ props, theme }) {
  const { enter } = useMotion({ duration: 16, damping: 17 });
  return <AbsoluteFill style={{ justifyContent: 'end', padding: '0 7% 11%', pointerEvents: 'none' }}><div style={{ alignSelf: 'flex-start', minWidth: 350, padding: '18px 26px 20px', borderLeft: `9px solid ${theme.palette.accent}`, background: rgba(theme.palette.background, .9), transform: `translateX(${(1 - enter) * -520}px)` }}><div style={{ color: theme.palette.primary, fontFamily: theme.display_font, fontSize: 39, lineHeight: 1, fontWeight: 900 }}>{words(props.title || 'SUBJECT', 5)}</div><div style={{ color: theme.palette.accent, fontFamily: theme.font_family, fontSize: 16, marginTop: 9, letterSpacing: 2.6, fontWeight: 800 }}>{props.subtitle || 'DOCUMENTARY'}</div></div></AbsoluteFill>;
}

function Callout({ props, theme }) {
  const { enter } = useMotion({ duration: 15, damping: 14, mass: .52 });
  return <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', padding: '8%', background: rgba(theme.palette.background, .32) }}><div style={{ width: '68%', padding: '28px 36px', border: `3px solid ${theme.palette.accent}`, background: rgba(theme.palette.surface, .94), transform: `scale(${.8 + enter * .2})` }}><Kicker theme={theme}>EVIDENCE</Kicker><div style={{ color: theme.palette.primary, fontFamily: theme.display_font, fontSize: 47, lineHeight: 1, fontWeight: 900, marginTop: 14 }}>{words(props.text || 'KEY DETAIL', 10)}</div>{props.detail && <div style={{ color: theme.palette.secondary, fontFamily: theme.font_family, fontSize: 20, marginTop: 14 }}>{words(props.detail, 20)}</div>}</div></AbsoluteFill>;
}

function Arrow({ props, theme }) {
  const frame = useCurrentFrame();
  const bbox = Array.isArray(props.bbox) && props.bbox.length === 4 ? props.bbox.map(Number) : null;
  if (!bbox || bbox.some(value => !Number.isFinite(value)) || bbox[0] < 0 || bbox[1] < 0 || bbox[2] > 1 || bbox[3] > 1 || bbox[0] >= bbox[2] || bbox[1] >= bbox[3]) return null;
  const [x1, y1, x2, y2] = bbox.map((item) => clamp(Number(item)));
  const x = (x1 + x2) / 2 * 100;
  const y = (y1 + y2) / 2 * 100;
  const reveal = clamp(frame / 16);
  const left = Math.max(7, x - 27), top = Math.max(8, y - 24);
  return <AbsoluteFill style={{ pointerEvents: 'none' }}><svg viewBox="0 0 1000 600" style={{ width: '100%', height: '100%' }}><path d={`M${left * 10} ${top * 6} C${(left + 9) * 10} ${(top - 11) * 6}, ${(x - 7) * 10} ${(y - 6) * 6}, ${x * 10} ${y * 6}`} stroke={theme.palette.accent} strokeWidth="8" fill="none" strokeLinecap="round" strokeDasharray="700" strokeDashoffset={700 - reveal * 700} /><path d={`M${x * 10} ${y * 6} l-27 -12 l10 30 z`} fill={theme.palette.accent} /></svg><div style={{ position: 'absolute', left: `${left}%`, top: `${top}%`, padding: '9px 13px', background: theme.palette.accent, color: theme.palette.background, fontFamily: theme.font_family, fontWeight: 900, fontSize: 15, letterSpacing: 1.6, transform: `scale(${.7 + reveal * .3})` }}>{props.label || 'LOOK HERE'}</div></AbsoluteFill>;
}

function EndCard({ props, theme }) {
  const { enter } = useMotion({ duration: 22, damping: 17 });
  return <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: theme.palette.background, textAlign: 'center', padding: '10%' }}><Texture theme={theme} strength={.35} /><div style={{ color: theme.palette.primary, fontFamily: theme.display_font, fontWeight: 900, fontSize: 70, lineHeight: .98, transform: `translateY(${(1 - enter) * 40}px)`, maxWidth: '84%' }}>{words(props.title || 'THE END', 10)}</div><div style={{ width: 250 * enter, height: 9, background: theme.palette.accent, marginTop: 30 }} />{props.subtitle && <div style={{ color: theme.palette.secondary, fontFamily: theme.font_family, fontSize: 21, letterSpacing: 3, marginTop: 24 }}>{props.subtitle}</div>}</AbsoluteFill>;
}

function DonutChart({ props, theme }) {
  const { frame, enter } = useMotion({ duration: 26, damping: 18 });
  const target = clamp(Number(props.value || 50) / 100);
  const progress = target * enter;
  const radius = 142;
  const circumference = 2 * Math.PI * radius;
  return <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: rgba(theme.palette.background, .78) }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 86 }}>
      <svg width="390" height="390" viewBox="0 0 390 390" style={{ transform: `rotate(${(1 - enter) * -22}deg)` }}>
        <circle cx="195" cy="195" r={radius} fill="none" stroke={rgba(theme.palette.primary, .14)} strokeWidth="38" />
        <circle cx="195" cy="195" r={radius} fill="none" stroke={theme.palette.accent} strokeWidth="38" strokeLinecap="round" strokeDasharray={circumference} strokeDashoffset={circumference * (1 - progress)} transform="rotate(-90 195 195)" />
        <text x="195" y="208" textAnchor="middle" fill={theme.palette.primary} fontFamily={theme.display_font} fontSize="74" fontWeight="900">{Math.round(target * 100 * enter)}%</text>
      </svg>
      <div style={{ maxWidth: 440 }}><Kicker theme={theme}>THE SHARE</Kicker><div style={{ marginTop: 18, color: theme.palette.primary, fontFamily: theme.display_font, fontSize: 56, lineHeight: .96, fontWeight: 900 }}>{words(props.label || 'OF THE TOTAL', 8)}</div><div style={{ width: 210 * enter, height: 9, marginTop: 28, background: theme.palette.accent }} /></div>
    </div>
  </AbsoluteFill>;
}

function AreaChart({ props, theme }) {
  const { frame } = useMotion();
  const values = (props.points || [18, 27, 24, 53, 78]).map(Number);
  const max = Math.max(1, ...values);
  const reveal = clamp(frame / 30);
  const points = values.map((value, index) => [80 + index * (760 / Math.max(1, values.length - 1)), 430 - value / max * 300]);
  const line = points.map(([x, y], index) => `${index ? 'L' : 'M'}${x} ${y}`).join(' ');
  const area = `${line} L${points.at(-1)[0]} 470 L${points[0][0]} 470 Z`;
  return <AbsoluteFill style={{ padding: '7.5%', background: `linear-gradient(145deg,${theme.palette.background},${theme.palette.surface})` }}>
    <Kicker theme={theme}>{props.label || 'CHANGE OVER TIME'}</Kicker>
    <svg viewBox="0 0 920 520" style={{ width: '92%', height: '76%', marginTop: 16 }}>
      <defs><linearGradient id="areaFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={theme.palette.accent} stopOpacity=".62" /><stop offset="1" stopColor={theme.palette.accent} stopOpacity=".03" /></linearGradient><clipPath id="areaReveal"><rect x="0" y="0" width={920 * reveal} height="520" /></clipPath></defs>
      {[0, 1, 2, 3].map(index => <line key={index} x1="70" x2="870" y1={120 + index * 95} y2={120 + index * 95} stroke={rgba(theme.palette.primary, .12)} strokeWidth="2" />)}
      <g clipPath="url(#areaReveal)"><path d={area} fill="url(#areaFill)" /><path d={line} fill="none" stroke={theme.palette.accent} strokeWidth="10" strokeLinecap="round" strokeLinejoin="round" />{points.map(([x, y], index) => <circle key={index} cx={x} cy={y} r="9" fill={theme.palette.primary} stroke={theme.palette.accent} strokeWidth="5" />)}</g>
    </svg>
  </AbsoluteFill>;
}

function SlopeChart({ props, theme }) {
  const { enter } = useMotion({ duration: 26 });
  const left = Number(props.left || 32), right = Number(props.right || 76);
  const max = Math.max(1, left, right);
  const y1 = 430 - left / max * 280, y2 = 430 - right / max * 280;
  const endX = 150 + 600 * enter;
  const endY = y1 + (y2 - y1) * enter;
  return <AbsoluteFill style={{ padding: '8%', background: rgba(theme.palette.background, .78) }}>
    <Kicker theme={theme}>{props.label || 'BEFORE / AFTER'}</Kicker>
    <svg viewBox="0 0 900 520" style={{ width: '90%', height: '72%', marginTop: 12 }}>
      <line x1="150" y1={y1} x2={endX} y2={endY} stroke={theme.palette.accent} strokeWidth="12" strokeLinecap="round" />
      <circle cx="150" cy={y1} r="18" fill={theme.palette.primary} /><circle cx={endX} cy={endY} r="18" fill={theme.palette.accent} />
      <text x="150" y={y1 - 38} textAnchor="middle" fill={theme.palette.primary} fontFamily={theme.display_font} fontSize="46" fontWeight="900">{left}</text>
      <text x={endX} y={endY - 38} textAnchor="middle" fill={theme.palette.accent} fontFamily={theme.display_font} fontSize="46" fontWeight="900">{Math.round(right * enter)}</text>
      <text x="150" y="485" textAnchor="middle" fill={theme.palette.secondary} fontFamily={theme.font_family} fontSize="20" fontWeight="800">{props.leftLabel || 'BEFORE'}</text>
      <text x="750" y="485" textAnchor="middle" fill={theme.palette.secondary} fontFamily={theme.font_family} fontSize="20" fontWeight="800">{props.rightLabel || 'AFTER'}</text>
    </svg>
  </AbsoluteFill>;
}

function RankedBars({ props, theme }) {
  const { frame } = useMotion();
  const items = (props.items || [{ label: 'FIRST', value: 82 }, { label: 'SECOND', value: 61 }, { label: 'THIRD', value: 39 }]).slice(0, 5);
  const max = Math.max(1, ...items.map(item => Number(item.value) || 0));
  return <AbsoluteFill style={{ justifyContent: 'center', padding: '8%', background: rgba(theme.palette.background, .8) }}>
    <Kicker theme={theme}>{props.label || 'RANKED COMPARISON'}</Kicker>
    <div style={{ marginTop: 30, display: 'flex', flexDirection: 'column', gap: 18 }}>{items.map((item, index) => {
      const reveal = clamp((frame - index * 5) / 24);
      return <div key={index} style={{ display: 'grid', gridTemplateColumns: '52px 180px 1fr 70px', alignItems: 'center', gap: 18 }}><div style={{ color: theme.palette.accent, fontFamily: theme.display_font, fontSize: 34, fontWeight: 900 }}>{String(index + 1).padStart(2, '0')}</div><div style={{ color: theme.palette.primary, fontFamily: theme.font_family, fontSize: 20, fontWeight: 800 }}>{item.label}</div><div style={{ height: 24, background: rgba(theme.palette.primary, .12) }}><div style={{ width: `${(Number(item.value) || 0) / max * 100 * reveal}%`, height: '100%', background: index === 0 ? theme.palette.accent : theme.palette.secondary }} /></div><div style={{ color: theme.palette.primary, fontFamily: theme.display_font, fontSize: 26 }}>{Math.round((Number(item.value) || 0) * reveal)}</div></div>;
    })}</div>
  </AbsoluteFill>;
}

function Pictogram({ props, theme }) {
  const { frame } = useMotion();
  const total = Math.max(1, Math.min(20, Number(props.total || 10)));
  const active = Math.max(0, Math.min(total, Number(props.value || 7)));
  return <AbsoluteFill style={{ justifyContent: 'center', alignItems: 'center', background: rgba(theme.palette.background, .8) }}>
    <Kicker theme={theme}>{props.label || 'HUMAN SCALE'}</Kicker>
    <div style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.min(10, total)}, 54px)`, gap: 18, marginTop: 42 }}>{Array.from({ length: total }, (_, index) => {
      const reveal = clamp((frame - index * 2) / 12);
      const on = index < active;
      return <div key={index} style={{ width: 54, height: 82, opacity: reveal, transform: `translateY(${(1 - reveal) * 22}px)` }}><div style={{ width: 25, height: 25, borderRadius: '50%', margin: '0 auto', background: on ? theme.palette.accent : rgba(theme.palette.primary, .2) }} /><div style={{ width: 36, height: 49, borderRadius: '18px 18px 7px 7px', margin: '7px auto 0', background: on ? theme.palette.accent : rgba(theme.palette.primary, .2) }} /></div>;
    })}</div>
  </AbsoluteFill>;
}

function EditorialContract({ props, theme }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame, fps, config: { damping: 18, mass: 0.62, stiffness: 150 } });
  const lineGrow = spring({ frame: Math.max(0, frame - 7), fps, config: { damping: 16, mass: 0.48, stiffness: 170 } });
  const transform = String(props._text_transform || 'uppercase').toLowerCase();
  const value = text(props._contract_text || props.text || props.title).slice(0, 48);
  if (!value) return null;

  const position = normalizeTextCardPosition(props._position);
  const anchor = TEXT_CARD_GUIDE[position];
  const mode = String(props._card_mode || 'none').toLowerCase();
  const fill = mode === 'surface'
    ? rgba(theme.palette.primary, .18)
    : mode === 'accent'
      ? theme.palette.accent
      : mode === 'danger'
        ? theme.palette.danger
        : 'transparent';
  const ink = mode === 'accent' || mode === 'danger' ? theme.palette.background : theme.palette.primary;
  const size = Math.max(48, Math.min(position === 'center' ? 118 : 92, Number(props._contract_size || 68)));
  const horizontalPad = Math.max(24, Math.round(size * .36));
  const verticalPad = Math.max(12, Math.round(size * .16));
  const motion = props._entry_motion === 'slide_right' ? 180 : props._entry_motion === 'pop' ? 0 : -180;
  const translate = motion * (1 - enter);
  const wordCues = Array.isArray(props._word_cues) ? props._word_cues : [];
  const highlightTokens = new Set((Array.isArray(props._highlight_tokens) ? props._highlight_tokens : [])
    .map((token) => String(token || '').toLowerCase().replace(/^[.,!?;:\"'()[\]]+|[.,!?;:\"'()[\]]+$/g, ''))
    .filter(Boolean));
  const seconds = frame / Math.max(1, fps);
  const activeWord = wordCues.findIndex((cue) => seconds >= Number(cue.start || 0) && seconds <= Number(cue.end || 0));
  const valueWords = value.split(/\s+/).filter(Boolean);

  return <AbsoluteFill style={{ pointerEvents: 'none' }}>
    {props._micro_label && <div style={{
      position: 'absolute', left: '8%', top: '10%', color: theme.palette.accent,
      fontFamily: 'Roboto, sans-serif', fontSize: 18, fontWeight: 900, letterSpacing: 2.4,
    }}>{text(props._micro_label).toUpperCase().slice(0, 16)}</div>}
    <div style={{
      position: 'absolute', left: `${anchor.x * 100}%`, top: `${anchor.y * 100}%`,
      transform: `translateX(calc(${anchor.horizontal === 'left' ? '0%' : anchor.horizontal === 'right' ? '-100%' : '-50%'} + ${translate}px)) translateY(-50%) scale(${.96 + enter * .04})`,
      transformOrigin: `${anchor.horizontal} center`, opacity: Math.min(1, frame / 8 + .15),
    }}>
      <div style={{
        position: 'relative', display: 'inline-block', width: 'max-content', maxWidth: '84%',
        whiteSpace: 'normal', wordBreak: 'keep-all', textAlign: 'left',
      }}>
        {mode !== 'none' && <div style={{
          position: 'absolute', left: -Math.round(horizontalPad * .16), right: -Math.round(horizontalPad * .16),
          bottom: -Math.max(4, Math.round(verticalPad * .22)), height: Math.max(10, Math.round(size * .2)),
          background: fill, transformOrigin: 'left center', transform: `scaleX(${lineGrow})`,
        }} />}
        <div style={{
          position: 'relative', color: ink, fontFamily: theme.display_font,
          fontSize: size, fontWeight: 900, lineHeight: .92, letterSpacing: -.8,
          fontStyle: props._font_style === 'italic' ? 'italic' : 'normal',
          textTransform: transform === 'uppercase' ? 'uppercase' : 'none',
          WebkitTextStroke: '.35px currentColor', textShadow: `0 3px 12px ${rgba(theme.palette.background, .72)}`,
        }}>{wordCues.length ? valueWords.map((word, index) => {
          const cue = wordCues[index];
          const normalizedWord = word.toLowerCase().replace(/^[.,!?;:\"'()[\]]+|[.,!?;:\"'()[\]]+$/g, '');
          const isActive = index === activeWord && (!highlightTokens.size || highlightTokens.has(normalizedWord));
          return <span key={`${word}-${index}`} style={{
            display: 'inline-block', marginRight: index < valueWords.length - 1 ? '.24em' : 0,
            padding: isActive ? '0 .12em' : 0,
            background: isActive ? rgba(theme.palette.accent, .92) : 'transparent',
            color: isActive ? theme.palette.background : ink,
            transition: 'background-color 90ms linear, color 90ms linear',
          }}>{word}</span>;
        }) : value}</div>
        {props._show_rule !== false && <div style={{
          width: `${lineGrow * 100}%`, height: 4, marginTop: 14,
          background: theme.palette.accent, transformOrigin: 'left center',
        }} />}
      </div>
    </div>
  </AbsoluteFill>;
}

export function GraphicTemplate({ id, props = {}, theme }) {
  id = normalizeTemplateId(id);
  props = normalizeGraphicProps(props);
  theme = resolveGraphicTheme(theme, props);
  if (props.no_data) return null;

  // Legacy templates are still supported for old manifests, but they must
  // not be allowed to fill missing factual props with their demo datasets.
  const groundedGraphic = {
    'stat-counter': props.value != null || props.number != null || props.stat != null,
    'money-counter': props.value != null,
    'bar-chart': Array.isArray(props.items || props.data || props.values) && (props.items || props.data || props.values).length >= 2,
    'line-chart': Array.isArray(props.points || props.data || props.values) && (props.points || props.data || props.values).length >= 2,
    'area-chart': Array.isArray(props.points || props.data || props.values) && (props.points || props.data || props.values).length >= 2,
    'ranked-bars': Array.isArray(props.items || props.data || props.values) && (props.items || props.data || props.values).length >= 2,
    'donut-chart': props.value != null,
    'slope-chart': props.left != null && props.right != null,
    'pictogram-stat': props.value != null,
    'timeline-trace': Boolean((props.events && props.events.length) || props.date || props.year || props.eraSpan),
    'date-card': Boolean(props.label || props.date || props.year),
    'date-numeral': Boolean(props.label || props.date || props.year),
    'date-reveal': Boolean(props.label || props.date || props.year),
    'calendar-rip': Boolean(props.label || props.date || props.year),
    'map-route': Boolean((props.from && props.to) || (props.start && props.end)),
    'map-card': Boolean((props.from && props.to) || (props.start && props.end) || props.country),
    'route-progress': Boolean(props.from && props.to),
    'data-race': Boolean(props.left && props.right && props.value != null),
    'quote-card': Boolean(props.quote),
    'witness-quote': Boolean(props.quote),
    'paper-explainer': Boolean(props.title && props.body),
    'source-highlight': Boolean(props.body),
    'cause-effect': Boolean(props.cause && props.effect),
    'split-screen': Boolean(props.left && props.right),
    'metric-grid': props.value != null || Array.isArray(props.metrics || props.items) && (props.metrics || props.items).length >= 2,
  };
  if (Object.prototype.hasOwnProperty.call(groundedGraphic, id) && !groundedGraphic[id]) return null;
  const useGenericContract = props._use_generic_contract ?? Number(props._cue_schema_version || 0) < 2;
  if (props._editorial_contract && useGenericContract) return <EditorialContract props={props} theme={theme} />;
  switch (id) {
    case 'text-card': return <TextCard props={props} theme={theme} />;
    case 'vox-highlighter': return <TextCard props={{ ...props, style: 'vox-highlighter' }} theme={theme} />;
    case 'cyber-hud': return <TextCard props={{ ...props, style: 'cyber-hud' }} theme={theme} />;
    case 'investigative-memo': return <TextCard props={{ ...props, style: 'investigative-memo' }} theme={theme} />;
    case 'broadsheet-callout': return <TextCard props={{ ...props, style: 'broadsheet-callout' }} theme={theme} />;
    case 'frosted-glass': return <TextCard props={{ ...props, style: 'frosted-glass' }} theme={theme} />;
    case 'brutalist-punch': return <TextCard props={{ ...props, style: 'brutalist-punch' }} theme={theme} />;
    case 'cold-open': return <ColdOpenEnhanced props={props} theme={theme} />;
    case 'chapter-opener': return <ChapterOpenerEnhanced props={props} theme={theme} />;
    case 'keyword-reveal':
      if (props.highlight_words || props.highlightWords || props.cardStyle || props.style) {
        return <TextCard props={props} theme={theme} />;
      }
      return <KeywordRevealEnhanced props={props} theme={theme} />;
    case 'date-card': return <DateCardEnhanced props={props} theme={theme} />;
    case 'date-numeral': return <DateCardEnhanced props={props} theme={theme} />;
    case 'date-reveal': return <DateCardEnhanced props={props} theme={theme} />;
    case 'profile-dossier': return <ProfileDossier props={props} theme={theme} />;
    case 'profile-card': return <LowerThirdEnhanced props={props} theme={theme} />;
    case 'presenter-card': return <LowerThirdEnhanced props={props} theme={theme} />;
    case 'subject-grid': return <ProfileDossier props={props} theme={theme} />;
    case 'evidence-board': return <EvidenceBoard props={props} theme={theme} />;
    case 'paper-explainer': return <Paper props={props} theme={theme} />;
    case 'source-highlight': return <Paper props={props} theme={theme} highlighted />;
    case 'quote-card': return <QuoteCardEnhanced props={props} theme={theme} />;
    case 'money-counter': return <MoneyCounterEnhanced props={props} theme={theme} />;
    case 'stat-counter': return <StatCounterEnhanced props={props} theme={theme} />;
    case 'bar-chart': return <Chart props={props} theme={theme} />;
    case 'line-chart': return <Chart props={props} theme={theme} line />;
    case 'donut-chart': return <DonutChart props={props} theme={theme} />;
    case 'area-chart': return <AreaChart props={props} theme={theme} />;
    case 'slope-chart': return <SlopeChart props={props} theme={theme} />;
    case 'ranked-bars': return <RankedBars props={props} theme={theme} />;
    case 'pictogram-stat': return <Pictogram props={props} theme={theme} />;
    case 'timeline-trace': return <Timeline props={props} theme={theme} />;
    case 'timeline-strip': return <Timeline props={props} theme={theme} />;
    case 'map-route': return <MapRouteEnhanced props={props} theme={theme} />;
    case 'map-card': return <MapRouteEnhanced props={props} theme={theme} />;
    case 'listicle-build': return <ListicleBuildEnhanced props={props} theme={theme} />;
    case 'listicle-steps': return <ListicleBuildEnhanced props={props} theme={theme} />;
    case 'step-progress': return <ListicleBuildEnhanced props={props} theme={theme} />;
    case 'cause-effect': return <CauseEffectEnhanced props={props} theme={theme} />;
    case 'split-screen': return <Split props={props} theme={theme} />;
    case 'archival-frame': return <ArchivalFrame props={props} theme={theme} />;
    case 'archival-caption': return <FieldCaption props={props} theme={theme} />;
    case 'photo-frame': return <ArchivalFrame props={props} theme={theme} />;
    case 'polaroid-card': return <ArchivalFrame props={props} theme={theme} />;
    case 'gallery-stack': return <Gallery props={props} theme={theme} />;
    case 'kinetic-statement': return <KineticStatementEnhanced props={props} theme={theme} />;
    case 'newsprint-reveal': return <NewsprintReveal props={props} theme={theme} />;
    case 'signal-scan': return <SignalScan props={props} theme={theme} />;
    case 'blueprint-reveal': return <BlueprintRevealEnhanced props={props} theme={theme} />;
    case 'decision-tree': return <DecisionTree props={props} theme={theme} />;
    case 'redaction-reveal': return <RedactionReveal props={props} theme={theme} />;
    case 'countdown-clock': return <CountdownClock props={props} theme={theme} />;
    case 'photo-pinboard': return <PhotoPinboard props={props} theme={theme} />;
    case 'data-race': return <DataRace props={props} theme={theme} />;
    case 'field-caption': return <FieldCaption props={props} theme={theme} />;
    case 'headline-center': return <KineticStatementEnhanced props={props} theme={theme} />;
    case 'evidence-highlight': return <EvidenceCalloutEnhanced props={props} theme={theme} />;
    case 'spotlight-lens': return <SpotlightLens props={props} theme={theme} />;
    case 'exhibit-label': return <ExhibitLabel props={props} theme={theme} />;
    case 'archive-canvas': return <Paper props={props} theme={theme} />;
    case 'card-overlay': return <Paper props={props} theme={theme} />;
    case 'witness-quote': return <WitnessQuote props={props} theme={theme} />;
    case 'calendar-rip': return <CalendarRipEnhanced props={props} theme={theme} />;
    case 'terminal-log': return <TerminalLog props={props} theme={theme} />;
    case 'metric-grid': return <MetricGrid props={props} theme={theme} />;
    case 'route-progress': return <RouteProgress props={props} theme={theme} />;
    case 'index-card': return <IndexCard props={props} theme={theme} />;
    case 'stamp-mark': return <StampMark props={props} theme={theme} />;
    case 'word-cascade': return <WordCascadeEnhanced props={props} theme={theme} />;
    case 'map-comparison': return <MapComparisonEnhanced props={props} theme={theme} />;
    case 'metric-grid-enhanced': return <MetricGridEnhanced props={props} theme={theme} />;
    case 'dossier-card': return <DossierCardEnhanced props={props} theme={theme} />;
    case 'process-flow': return <ProcessFlowEnhanced props={props} theme={theme} />;
    case 'media-focus': return <MediaFocusCardEnhanced props={props} theme={theme} />;
    case 'case-file': return <CaseFile props={props} theme={theme} />;
    case 'callout-arrow': return <Arrow props={props} theme={theme} />;
    case 'lower-third': return <LowerThirdEnhanced props={props} theme={theme} />;
    case 'lower-third-card': return <LowerThirdEnhanced props={props} theme={theme} />;
    case 'evidence-callout': return <EvidenceCalloutEnhanced props={props} theme={theme} />;
    case 'data-card': return <DataFactCardEnhanced props={props} theme={theme} />;
    case 'fact-card': return <DataFactCardEnhanced props={props} theme={theme} />;
    case 'cinematic-highlight': return <CinematicHighlightEnhanced props={props} theme={theme} />;
    case 'cinematic-card': return <CinematicHighlightEnhanced props={props} theme={theme} />;
    case 'kinetic-type': return <KineticStatementEnhanced props={props} theme={theme} />;
    case 'end-card': return <EndCardEnhanced props={props} theme={theme} />;
    default: return null;
  }
}
