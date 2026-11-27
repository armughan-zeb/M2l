// Video profiles — full-bleed 9:16 Shorts ('short' / 'reel') or 16:9 Landscape ('wide').
export const PROFILES = {
  short: {
    format: 'vertical', captions: 'off', capWords: 4, upper: false,
    sceneTarget: [1.5, 4.5], sceneMax: 7, maxDur: 180,
    sound: { mode: 'auto', transitions: true, level: 1 },
    pacing: { lead: 0.05, gap: 0.02, outro: 0.25 },
  },
  reel: {
    format: 'vertical', captions: 'phrase', capWords: 3, upper: true,
    sceneTarget: [1.5, 4.5], sceneMax: 6, maxDur: 90,
    sound: { mode: 'auto', transitions: true, level: 1 },
    pacing: { lead: 0.05, gap: 0.02, outro: 0.25 },
  },
  wide: {
    format: 'wide', captions: 'phrase', capWords: 5, upper: false,
    sceneTarget: [2, 5], sceneMax: 7, maxDur: 7200,
    sound: { mode: 'auto', transitions: true, level: 1 },
    pacing: { lead: 0.05, gap: 0.02, outro: 0.25 },
  },
  long: {
    format: 'wide', captions: 'phrase', capWords: 6, upper: false,
    sceneTarget: [3, 8], sceneMax: 12, maxDur: 7200,
    sound: { mode: 'auto', transitions: true, level: 1 },
    pacing: { lead: 0.06, gap: 0.03, outro: 0.3 },
  },
};

export function resolveProfile(script, dur) {
  const req = script.profile || (script.format === 'vertical' ? 'short' : 'wide');
  const name = PROFILES[req] ? req : 'short';
  const base = PROFILES[name], ov = script.profileOverrides || {};
  const cfg = {
    ...base,
    ...ov,
    sound: { ...base.sound, ...(ov.sound || {}), ...(script.sound || {}) },
    pacing: { ...base.pacing, ...(ov.pacing || {}) },
  };
  if (script.format) cfg.format = script.format;
  if (script.captions) cfg.captions = script.captions;
  return { name, ...cfg };
}
