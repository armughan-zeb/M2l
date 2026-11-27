// Sound planner — decides which sound effect goes where, from what each scene DOES.
// A shot declares `motion` (what moves in it); this file maps motion -> sound and timing.
// Override per shot with `sound: {name, at}` or `sounds: [{name, at, vol}]` (`at` = fraction of the shot), or
// set `hit: 0.4` on a shot to say where its main moment (impact / stamp / reveal) happens.
//
// script.json "sound": { mode: "auto" | "manual" | "off", transitions: true|false, level: 1, bed: "drone"|"wind"|null }
//   auto   (default) every scene gets sound from its motion, plus a soft whoosh on cuts when transitions is on
//   manual only the cues you wrote on shots
//   off    silent effects (narration still plays)
// Result: cues [{ t, name, vol }] -> data/cues.json -> tools/mix_audio.sh (under the narration, never over it).
export const SOUND_NAMES = ['pop', 'whoosh', 'stamp', 'boom', 'riser', 'tick', 'ding', 'click', 'swish', 'rumble'];
export const VOL = { pop: 0.25, whoosh: 0.22, stamp: 0.30, boom: 0.38, riser: 0.20, tick: 0.18, ding: 0.22, click: 0.20, swish: 0.16, rumble: 0.20 };
const PRIORITY = { boom: 5, stamp: 5, ding: 4, riser: 3, pop: 3, whoosh: 2, rumble: 2, tick: 1, click: 1, swish: 1 };

// motion -> [ { name, at } | { name, hit: defaultFraction } ]
export const MOTIONS = {
  walk:    [{ name: 'tick', at: 0.12 }],
  slide:   [{ name: 'whoosh', at: 0.08 }],
  sweep:   [{ name: 'whoosh', at: 0.10 }],
  walkout: [{ name: 'whoosh', at: 0.20 }],
  draw:    [{ name: 'swish', at: 0.08 }],
  zoom:    [{ name: 'swish', at: 0.05 }],
  glow:    [{ name: 'riser', at: 0.05 }],
  rise:    [{ name: 'riser', at: 0.05 }],
  flicker: [{ name: 'click', at: 0.10 }],
  nod:     [{ name: 'pop', at: 0.45 }],
  reveal:  [{ name: 'ding', hit: 0.40 }],
  stamp:   [{ name: 'stamp', hit: 0.40 }],
  impact:  [{ name: 'boom', hit: 0.30 }, { name: 'rumble', hit: 0.34 }],
};
export const MOTION_NAMES = Object.keys(MOTIONS);

export function planSounds(shots, dur, cfg, script = {}) {
  const sc = cfg.sound || { mode: 'auto' };
  const mode = sc.mode || 'auto';
  const out = { cues: [], bed: null };
  if (mode === 'off') return out;
  const level = sc.level ?? 1;
  const add = (t, name, vol) => out.cues.push({ t: +Math.max(0, Math.min(dur - 0.05, t)).toFixed(3), name, vol: +((vol ?? VOL[name] ?? 0.2) * level).toFixed(3) });
  shots.forEach((s, i) => {
    const end = i < shots.length - 1 ? shots[i + 1].at : dur, d = end - s.at;
    const explicit = s.sounds || (s.sound ? [s.sound] : null);
    if (explicit) { explicit.forEach((e) => add(s.at + (e.at ?? 0.1) * d, e.name, e.vol)); }
    else if (mode === 'auto' && s.motion && MOTIONS[s.motion]) {
      for (const e of MOTIONS[s.motion]) {
        const at = e.at ?? (s.hit ?? e.hit);
        // a whoosh/swish right at the start is redundant when the cut already has a transition whoosh
        if (sc.transitions && i > 0 && (e.name === 'whoosh' || e.name === 'swish') && at * d < 0.35) continue;
        add(s.at + at * d, e.name);
      }
    }
    if (mode === 'auto' && sc.transitions && i > 0) add(s.at - 0.03, 'swish', 0.12);
  });
  // de-clutter: two cues within 0.2 s -> keep the higher-priority one
  out.cues.sort((a, b) => a.t - b.t);
  const kept = [];
  for (const c of out.cues) {
    const p = kept[kept.length - 1];
    if (p && c.t - p.t < 0.2) { if ((PRIORITY[c.name] || 0) > (PRIORITY[p.name] || 0)) kept[kept.length - 1] = c; continue; }
    kept.push(c);
  }
  out.cues = kept;
  if (sc.bed && sc.bed !== 'none') out.bed = { name: 'bed_' + sc.bed, vol: sc.bedVol ?? 0.07 };
  return out;
}
