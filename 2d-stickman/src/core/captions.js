// Captions — four modes, chosen by script.json `captions` (or the profile default, see core/profiles.js):
//   "phrase" — REEL STYLE (default for reel / short / long). One short phrase at a time, big bold text, white with
//              a black outline, the keyword in yellow (#FFD700), pop-in on every phrase, no dark box. Phrases come
//              from each line's `caps` ("a b | *c d | e *f"), and if a line has no `caps` they are PLANNED
//              AUTOMATICALLY from the text by core/capplan.js (numbers, years, names, acronyms, power words).
//   "full"   — the whole spoken line, white with black outline on a translucent dark box (classic subtitle).
//   "ja"     — Japanese phrase mode, locked spec: split at 、/。, Noto Sans CJK JP Bold 64, keyword #FFD700, y=H-240.
//   "off"    — nothing.
// Phrases are timed inside their line in proportion to their character count. Everything runs on the
// measured narration clock (data/lines.json) — never on estimates.
import { ctx, clamp, lerp, seg, eob } from './lib.js';
import { T } from './timing.js';
import { planCaps } from './capplan.js';

const KEYWORD = '#FFD700';
const FULL = { font: '800 42px Inter', lh: 54, maxw: 1500 };
let mode = 'full', caps = [], LAY = { W: 1920, H: 1080, vertical: false, capY: 950, size: 76, maxw: 1700, upper: false };

/** layout comes from main.js: canvas size + where the phrase caption sits */
export function setCaptionLayout(l) { LAY = { ...LAY, ...l }; }

function wrapFull(text) {
  ctx.font = FULL.font; const ws = text.split(/\s+/), lines = []; let cur = '';
  for (const w of ws) { const t = cur ? cur + ' ' + w : w; if (ctx.measureText(t).width > FULL.maxw && cur) { lines.push(cur); cur = w; } else cur = t; }
  if (cur) lines.push(cur);
  return lines.slice(0, 3);
}
// `*` starts a keyword run that ends at the next space, `*`, or Japanese 、/。
function parsePhrase(raw) {
  const runs = []; let cur = '', kw = false;
  const flush = () => { if (cur) { runs.push({ text: cur, keyword: kw }); cur = ''; } };
  for (const ch of raw) {
    if (ch === '*') { flush(); kw = true; continue; }
    if (kw && (ch === ' ' || ch === '、' || ch === '。')) { flush(); kw = false; }
    cur += ch;
  }
  flush(); return runs;
}
const splitJaFallback = (text) => (text.match(/[^、。]+[、。]?/g) || []).map((p) => p.trim()).filter(Boolean);

export function buildCaptions(script, cfg = {}) {
  mode = cfg.captions || script.captions || 'full';
  if (!['full', 'phrase', 'ja', 'off'].includes(mode)) mode = 'full';
  if (cfg.upper != null) LAY.upper = cfg.upper;
  if (mode === 'off') { caps = []; return caps; }
  if (mode === 'full') {
    caps = script.lines.map((ln, i) => ({ line: i, s: T.lines[i].start - 0.05, text: ln.text, wrapped: wrapFull(ln.text) }));
  } else {
    caps = script.lines.map((ln, i) => {
      let raw = (ln.caps || '').trim();
      if (!raw) raw = mode === 'ja' ? '' : planCaps(ln.text, { maxWords: cfg.capWords || 3 }); // auto-plan
      const texts = raw ? raw.split('|').map((p) => p.trim()).filter(Boolean) : splitJaFallback(ln.text);
      const phrases = texts.map((p) => { const runs = parsePhrase(p); return { runs, len: runs.reduce((n, r) => n + [...r.text].length, 0) }; });
      return { line: i, s: T.lines[i].start - 0.05, text: ln.text, phrases };
    });
  }
  caps.forEach((c, k) => {
    const next = caps[k + 1], lineEnd = T.lines[c.line].end;
    c.e = next ? (next.s - lineEnd > 0.7 ? lineEnd + 0.35 : next.s) : T.dur;
  });
  if (mode !== 'full') caps.forEach((c) => {
    const total = c.phrases.reduce((n, p) => n + p.len, 0) || 1; let t = c.s;
    c.phrases.forEach((p) => { p.s = t; p.e = t + (c.e - c.s) * (p.len / total); t = p.e; });
    if (c.phrases.length) c.phrases[c.phrases.length - 1].e = c.e;
  });
  return caps;
}
export const captionList = () => caps;
export const captionInfo = () => ({
  mode, lines: caps.length, phrases: caps.reduce((n, c) => n + (c.phrases ? c.phrases.length : 0), 0),
  withKeyword: caps.reduce((n, c) => n + (c.phrases ? c.phrases.filter((p) => p.runs.some((r) => r.keyword)).length : 0), 0),
  maxWords: caps.reduce((m, c) => Math.max(m, ...(c.phrases || []).map((p) => p.runs.map((r) => r.text).join('').trim().split(/\s+/).length)), 0),
});

// ---- phrase / ja drawing: one phrase at a time, token by token, wrapped to at most 2 lines ----
function drawPhrase(t, c) {
  const p = c.phrases.find((p) => t >= p.s && t < p.e) || c.phrases[c.phrases.length - 1]; if (!p) return;
  const ja = mode === 'ja';
  const fam = ja ? '"Noto Sans CJK JP", sans-serif' : 'Inter, sans-serif', wt = ja ? 700 : 900;
  const up = !ja && LAY.upper;
  const toks = [];
  for (const r of p.runs) for (const piece of (ja ? [r.text] : r.text.split(/(\s+)/))) { if (piece === '') continue; toks.push({ text: up ? piece.toUpperCase() : piece, keyword: r.keyword }); }
  let size = ja ? 64 : LAY.size; const maxw = ja ? 1800 : LAY.maxw;
  const width = (px) => { ctx.font = `${wt} ${px}px ${fam}`; return toks.reduce((w, k) => w + ctx.measureText(k.text).width, 0); };
  const minSize = Math.round(size * 0.72);
  while (width(size) > maxw && size > minSize) size -= 2;
  // wrap into ≤ 2 lines if it still doesn't fit
  ctx.font = `${wt} ${size}px ${fam}`;
  const lines = [[]]; let lw = 0;
  for (const k of toks) {
    const w = ctx.measureText(k.text).width;
    if (lw + w > maxw && lines[lines.length - 1].length && k.text.trim()) { lines.push([]); lw = 0; }
    if (!(lw === 0 && !k.text.trim())) { lines[lines.length - 1].push(k); lw += w; }
  }
  const lt = t - p.s, sc = ja ? (t - c.s < 0.18 ? lerp(0.7, 1, eob(seg(t - c.s, 0, 0.18))) : 1) : lerp(0.78, 1, eob(seg(lt, 0, 0.14)));
  const y0 = ja ? LAY.H - 240 : LAY.capY, lh = size * 1.12;
  ctx.save(); ctx.translate(LAY.W / 2, y0); ctx.scale(sc, sc);
  ctx.font = `${wt} ${size}px ${fam}`; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.lineJoin = 'round'; ctx.miterLimit = 2;
  ctx.lineWidth = ja ? 4 : Math.max(8, size * 0.15); ctx.strokeStyle = '#000';
  lines.slice(0, 2).forEach((ln, li) => {
    const w = ln.reduce((a, k) => a + ctx.measureText(k.text).width, 0); let x = -w / 2; const y = (li - (Math.min(lines.length, 2) - 1) / 2) * lh;
    for (const k of ln) { ctx.strokeText(k.text, x, y); ctx.fillStyle = k.keyword ? KEYWORD : '#FFFFFF'; ctx.fillText(k.text, x, y); x += ctx.measureText(k.text).width; }
  });
  ctx.restore();
}

export function drawCaption(t) {
  if (mode === 'off') return;
  const c = caps.find((c) => t >= c.s && t < c.e); if (!c) return;
  if (mode === 'ja' || mode === 'phrase') return drawPhrase(t, c);
  const lt = t - c.s, sc = lt < 0.18 ? lerp(0.7, 1, eob(seg(lt, 0, 0.18))) : 1;
  ctx.save(); ctx.translate(LAY.W / 2, LAY.vertical ? LAY.capY : 950); ctx.scale(sc, sc);
  ctx.font = FULL.font; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.lineJoin = 'round';
  const bw = Math.max(...c.wrapped.map((l) => ctx.measureText(l).width)) + 56, bh = c.wrapped.length * FULL.lh + 28;
  ctx.fillStyle = 'rgba(0,0,0,0.45)'; ctx.beginPath(); ctx.roundRect(-bw / 2, -bh / 2, bw, bh, 8); ctx.fill();
  c.wrapped.forEach((l, i) => { const y = (i - (c.wrapped.length - 1) / 2) * FULL.lh; ctx.lineWidth = 5; ctx.strokeStyle = '#000'; ctx.strokeText(l, 0, y); ctx.fillStyle = '#FFFFFF'; ctx.fillText(l, 0, y); });
  ctx.restore();
}
