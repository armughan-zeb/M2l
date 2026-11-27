'use strict';

/**
 * Single source of truth for video style / production engine routing in Muse.
 *
 * Styles are strictly isolated:
 *   - documentary (engine: 'vidzeo')  -> Vidnets / render-vidnets-linux.js
 *   - doodle_verse (engine: 'doodle') -> Doodle Verse Animation (Three.js + Playwright)
 *
 * Fail closed on conflicting or unknown explicit styles so one engine's
 * payload can never silently render under the other engine.
 */

const ENGINES = Object.freeze({
  VIDZEO: 'vidzeo',
  DOODLE: 'doodle',
});

const STYLES = Object.freeze({
  DOCUMENTARY: 'documentary',
  DOODLE_VERSE: 'doodle_verse',
});

const STYLE_META = Object.freeze({
  [ENGINES.VIDZEO]: Object.freeze({
    engine: ENGINES.VIDZEO,
    style: STYLES.DOCUMENTARY,
    label: 'Documentary (Vidnets)',
  }),
  [ENGINES.DOODLE]: Object.freeze({
    engine: ENGINES.DOODLE,
    style: STYLES.DOODLE_VERSE,
    label: 'Doodle Verse Animation',
  }),
});

const ALIAS_TO_ENGINE = new Map([
  ['vidzeo', ENGINES.VIDZEO],
  ['vidnets', ENGINES.VIDZEO],
  ['documentary', ENGINES.VIDZEO],
  ['vidnets_documentary', ENGINES.VIDZEO],
  ['vidnets-documentary', ENGINES.VIDZEO],
  ['doodle', ENGINES.DOODLE],
  ['doodle_verse', ENGINES.DOODLE],
  ['doodle-verse', ENGINES.DOODLE],
  ['doodle_animation', ENGINES.DOODLE],
  ['doodle-animation', ENGINES.DOODLE],
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
    return { ...STYLE_META[ENGINES.VIDZEO], explicit: false };
  }

  const resolved = [];
  for (const [field, raw] of signals) {
    const normalized = normalizeStyleValue(raw);
    if (!normalized) {
      throw new Error(
        `Unsupported video style/engine "${raw}" at ${field}; Muse worker supports documentary (vidzeo) and doodle_verse (doodle).`
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

function isDoodleJob(job = {}) {
  return resolveVideoStyle(job).engine === ENGINES.DOODLE;
}

function isDocumentaryJob(job = {}) {
  return resolveVideoStyle(job).engine === ENGINES.VIDZEO;
}

function defaultVoiceForStyle(job = {}) {
  const style = resolveVideoStyle(job);
  if (style.engine === ENGINES.DOODLE) {
    return (
      String(
        (job.picgenVideoOptions && job.picgenVideoOptions.voice) ||
        (job.channel && job.channel.picgenVideoOptions && job.channel.picgenVideoOptions.voice) ||
        (job.videoOptions && job.videoOptions.voice) ||
        (job.channel && job.channel.videoOptions && job.channel.videoOptions.voice) ||
        ''
      ).trim() || 'en-US-AndrewNeural'
    );
  }
  return (
    String(
      (job.videoOptions && job.videoOptions.voice) ||
      (job.channel && job.channel.videoOptions && job.channel.videoOptions.voice) ||
      ''
    ).trim() || 'default'
  );
}

module.exports = {
  ENGINES,
  STYLES,
  STYLE_META,
  normalizeStyleValue,
  resolveVideoStyle,
  isDoodleJob,
  isDocumentaryJob,
  defaultVoiceForStyle,
};
