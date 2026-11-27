// Caption planner — turns a spoken line into reel-style caption phrases with ONE keyword each.
// Pure functions: runs in the browser (captions.js falls back to it) and in Node (tools/plan_captions.mjs).
//   planCaps("Fog covers Tenerife airport, where two Boeing 747s wait.", { maxWords: 3 })
//   -> "Fog covers | *Tenerife airport, | where two | *Boeing *747s wait."   (illustrative)
// Format = the `caps` format captions.js already reads: phrases split by " | ", keyword marked by a leading "*"
// (the marked word runs to the next space). It is a heuristic (rules, not understanding): it picks numbers,
// years, names, acronyms and "power words" first. Any line can be overridden by hand in script.json `caps`.
const STOP = new Set(('a an the and or but if so as at by for in of on to up with from into onto over than then that this these those ' +
  'is are was were be been being it its he she they them his her their we you i our your not no do did does had has have will would can ' +
  'could should may might just also very too more most some any all each both own same such what which who whom whose when where why how ' +
  'there here about after before again against between during through under until while out off down still even only one two').split(' '));
STOP.delete('only'); STOP.delete('one'); STOP.delete('two'); // "only" is a power word; numbers are handled by the number rule
const BREAK_BEFORE = new Set('and but or where which that while when because after before with in on at of to for from by as then so'.split(' '));
const POWER = new Set(('first last never only deadliest deadly worst best biggest largest smallest greatest secret hidden million billion trillion ' +
  'thousand hundred killed kill died dead death destroy destroyed crash crashed collapse collapsed war won lost survived survive shock shocking ' +
  'impossible forever oldest richest fastest failed fail mistake disaster revolution empire king queen president emperor invented discovered ' +
  'betrayed executed vanished vanish mystery banned collide collided exploded explosion fire fog storm sank sunk attack attacked invaded ' +
  'escaped trapped silence suddenly instead nobody everyone everything nothing changed history record').split(' '));
const NUMW = new Set('hundred thousand million billion trillion dozen'.split(' '));
const clean = (w) => w.replace(/^[^\w]+|[^\w]+$/g, '');
const endsPunct = (w) => /[.,;:!?…—–]$/.test(w);

function words(text) {
  const raw = String(text).trim().split(/\s+/).filter(Boolean);
  let sentStart = true;
  return raw.map((w) => { const o = { raw: w, sentStart }; sentStart = /[.!?…]$/.test(w); return o; });
}
function hardSplit(ws) {
  const out = []; let cur = [];
  for (const w of ws) { cur.push(w); if (endsPunct(w.raw)) { out.push(cur); cur = []; } }
  if (cur.length) out.push(cur);
  return out;
}
function balance(chunk, max) {
  if (chunk.length <= max) return [chunk];
  let best = 1, bestScore = -1e9;
  for (let k = 1; k < chunk.length; k++) {
    const next = clean(chunk[k].raw).toLowerCase(), prev = clean(chunk[k - 1].raw).toLowerCase();
    let s = -Math.abs(k - chunk.length / 2);
    if (BREAK_BEFORE.has(next)) s += 1.5;
    if (STOP.has(prev) && !endsPunct(chunk[k - 1].raw)) s -= 1.4;   // don't end a phrase on "the", "of" …
    if (k > max) s -= (k - max) * 0.8;                              // keep the left side short enough
    const cap = (x) => /^[A-Z]/.test(clean(x.raw)) && !x.sentStart;
    if (cap(chunk[k - 1]) && cap(chunk[k])) s -= 3;                 // never split a name: "Pan | Am"
    if ((k === 1 || k === chunk.length - 1) && !endsPunct(chunk[k - 1].raw)) s -= 0.9; // avoid one-word orphans
    if (s > bestScore) { bestScore = s; best = k; }
  }
  return [...balance(chunk.slice(0, best), max), ...balance(chunk.slice(best), max)];
}
function mergeOrphans(ph) {
  const out = [];
  for (let i = 0; i < ph.length; i++) {
    const p = ph[i];
    const lone = p.length === 1 && STOP.has(clean(p[0].raw).toLowerCase()) && !endsPunct(p[0].raw);
    if (lone && i < ph.length - 1) { ph[i + 1] = [...p, ...ph[i + 1]]; continue; }
    out.push(p);
  }
  return out;
}
function score(w) {
  const c = clean(w.raw); if (!c) return -99;
  const lo = c.toLowerCase(); let s = 0;
  if (/\d/.test(c)) s += 6;
  if (NUMW.has(lo)) s += 4;
  if (POWER.has(lo) || POWER.has(lo.replace(/(s|ed|d)$/, ''))) s += 4;
  if (/^[A-Z][A-Z0-9]+s?$/.test(c)) s += 5;                // acronyms: KLM, NASA, B52
  else if (/^[A-Z]/.test(c) && !w.sentStart) s += 4;       // names mid-sentence
  if (STOP.has(lo)) s -= 10;
  return s + Math.min(c.length, 10) / 10;
}
function pickKeyword(phrase) {
  let bi = -1, bs = 0.35;
  phrase.forEach((w, i) => { const s = score(w); if (s > bs) { bs = s; bi = i; } });
  return bi;
}
/** line text -> `caps` string. opts: { maxWords = 3, keywords = true } */
export function planCaps(text, opts = {}) {
  const max = Math.max(1, opts.maxWords || 3);
  let phrases = [];
  for (const chunk of hardSplit(words(text))) phrases.push(...balance(chunk, max));
  phrases = mergeOrphans(phrases);
  return phrases.map((p) => {
    const k = opts.keywords === false ? -1 : pickKeyword(p);
    return p.map((w, i) => (i === k ? '*' + w.raw : w.raw)).join(' ');
  }).join(' | ');
}
/** fill `caps` on every line of a script that lacks one (or all lines with force). Returns the number of lines planned. */
export function planScript(script, opts = {}) {
  let n = 0;
  for (const ln of script.lines) { if (!ln.caps || opts.force) { ln.caps = planCaps(ln.text, opts); n++; } }
  return n;
}
