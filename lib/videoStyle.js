'use strict';

/**
 * Single source of truth for video style / production engine routing in Muse.
 *
 * Styles are strictly isolated:
 *   - documentary (engine: 'documentary') -> One-Minute Documentary (Remotion + yt-dlp + TTS)
 *   - 2d_stickman (engine: 'stickman')    -> 2D Stickman Cartoon Studio (Canvas + Chrome CDP)
 *
 * Fail closed on conflicting, removed (doodle), or unknown explicit styles so one
 * engine's payload can never silently render under another engine.
 */

const ENGINES = Object.freeze({
  DOCUMENTARY: 'documentary',
  STICKMAN: 'stickman',
});

const STYLES = Object.freeze({
  DOCUMENTARY: 'documentary',
  STICKMAN: '2d_stickman',
});

const STYLE_META = Object.freeze({
  [ENGINES.DOCUMENTARY]: Object.freeze({
    engine: ENGINES.DOCUMENTARY,
    style: STYLES.DOCUMENTARY,
    label: 'One-Minute Documentary (Remotion)',
  }),
  [ENGINES.STICKMAN]: Object.freeze({
    engine: ENGINES.STICKMAN,
    style: STYLES.STICKMAN,
    label: '2D Stickman',
  }),
});

const ALIAS_TO_ENGINE = new Map([
  ['documentary', ENGINES.DOCUMENTARY],
  ['one_minute_documentary', ENGINES.DOCUMENTARY],
  ['one-minute-documentary', ENGINES.DOCUMENTARY],
  ['remotion-documentary', ENGINES.DOCUMENTARY],
  ['remotion_documentary', ENGINES.DOCUMENTARY],
  ['vidzeo', ENGINES.DOCUMENTARY],
  ['vidnets', ENGINES.DOCUMENTARY],
  ['vidnets_documentary', ENGINES.DOCUMENTARY],
  ['vidnets-documentary', ENGINES.DOCUMENTARY],
  ['stickman', ENGINES.STICKMAN],
  ['2d-stickman', ENGINES.STICKMAN],
  ['2d_stickman', ENGINES.STICKMAN],
  ['2d stickman', ENGINES.STICKMAN],
  ['stickman-cartoon-studio', ENGINES.STICKMAN],
  ['stickman_cartoon', ENGINES.STICKMAN],
]);

function normalizeStyleValue(raw) {
  const value = String(raw || '').trim().toLowerCase();
  if (!value) return '';
  return ALIAS_TO_ENGINE.get(value) || null;
}

function collectStyleSignals(job) {
  if (!job || typeof job !== 'object') return [];
  const candidates = [
    ['job.productionEngine', job.productionEngine],
    ['job.videoStyle', job.videoStyle],
    ['job.styleProfile.engine', job.styleProfile && job.styleProfile.engine],
    ['job.styleProfile.style', job.styleProfile && job.styleProfile.style],
    ['job.channel.productionEngine', job.channel && job.channel.productionEngine],
    ['job.channel.videoStyle', job.channel && job.channel.videoStyle],
    ['job.videoOptions.productionEngine', job.videoOptions && job.videoOptions.productionEngine],
    ['job.videoOptions.style', job.videoOptions && job.videoOptions.style],
  ];
  return candidates.filter(([, value]) => String(value || '').trim() !== '');
}

function resolveVideoStyle(job = {}) {
  const signals = collectStyleSignals(job);
  if (!signals.length) {
    return { ...STYLE_META[ENGINES.DOCUMENTARY], explicit: false };
  }

  const resolved = [];
  for (const [field, raw] of signals) {
    const normalized = normalizeStyleValue(raw);
    if (!normalized) {
      throw new Error(
        `Unsupported video style/engine "${raw}" at ${field}; Muse worker supports documentary (one_minute_documentary) and 2d_stickman (stickman).`
      );
    }
    resolved.push({ field, raw: String(raw), engine: normalized });
  }

  const distinctEngines = [...new Set(resolved.map((item) => item.engine))];
  if (distinctEngines.length > 1) {
    const summary = resolved.map((item) => `${item.field}=${item.raw}`).join(', ');
    throw new Error(`Conflicting video styles in Muse job payload (${summary}); refusing to mix engines.`);
  }

  return { ...STYLE_META[distinctEngines[0]], explicit: true };
}

function isStickmanJob(job = {}) {
  return resolveVideoStyle(job).engine === ENGINES.STICKMAN;
}

function isDocumentaryJob(job = {}) {
  return resolveVideoStyle(job).engine === ENGINES.DOCUMENTARY;
}

function defaultVoiceForStyle(job = {}) {
  const style = resolveVideoStyle(job);
  if (style.engine === ENGINES.STICKMAN) {
    return (
      String(
        (job.stickmanOptions && job.stickmanOptions.voice) ||
        (job.picgenVideoOptions && job.picgenVideoOptions.voice) ||
        (job.channel && job.channel.picgenVideoOptions && job.channel.picgenVideoOptions.voice) ||
        (job.videoOptions && job.videoOptions.voice) ||
        (job.channel && job.channel.videoOptions && job.channel.videoOptions.voice) ||
        ''
      ).trim() || 'en-US-ChristopherNeural'
    );
  }
  return (
    String(
      (job.documentaryOptions && job.documentaryOptions.voice) ||
      (job.videoOptions && job.videoOptions.voice) ||
      (job.channel && job.channel.videoOptions && job.channel.videoOptions.voice) ||
      ''
    ).trim() || 'en-US-ChristopherNeural'
  );
}

module.exports = {
  ENGINES,
  STYLES,
  STYLE_META,
  normalizeStyleValue,
  resolveVideoStyle,
  isStickmanJob,
  isDocumentaryJob,
  defaultVoiceForStyle,
};
