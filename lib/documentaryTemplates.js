'use strict';

/**
 * Documentary template registry + selection.
 *
 * Three Remotion template variants live under one_minute_documentary/templates/:
 *   standard — remotion-project (the default documentary look)
 *   crime    — remotion-crime   (Black Box / Vanished at Sea: dark, tense)
 *   history  — remotion-history (Iron Road / Lost Expeditions: warm archival)
 *
 * adapters/documentary.js wires selection in: it picks the template via
 * resolveDocumentaryTemplate(job) and copies that template's dir as the
 * project base. Each variant also ships a template-config.json with its
 * full style tokens + SFX profile; the registry below mirrors the
 * selection-relevant fields (id, dir, label, genres, keywords) so this
 * module stays pure and easily testable — no disk reads, only path
 * resolution. Keep the two in sync when a variant's keywords change.
 */

const path = require('node:path');

const TEMPLATES = Object.freeze([
  Object.freeze({
    id: 'standard',
    dirName: 'remotion-project',
    label: 'Documentary — Standard',
    genres: ['documentary', 'general'],
    keywords: [],
  }),
  Object.freeze({
    id: 'crime',
    dirName: 'remotion-crime',
    label: 'Crime / Mystery — Black Box',
    genres: ['crime', 'mystery', 'true crime'],
    keywords: [
      'crime',
      'mystery',
      'murder',
      'disappearance',
      'disappeared',
      'vanished',
      'vanishing',
      'missing',
      'black box',
      'detective',
      'cold case',
      'unsolved',
      'killer',
      'heist',
      'true crime',
      'conspiracy',
      'scandal',
      'kidnap',
      'abduct',
    ],
  }),
  Object.freeze({
    id: 'history',
    dirName: 'remotion-history',
    label: 'History / Expedition — Iron Road',
    genres: ['history', 'historical', 'expedition'],
    keywords: [
      'history',
      'historical',
      'expedition',
      'lost expedition',
      'lost expeditions',
      'railway',
      'railroad',
      'iron road',
      'journey',
      'voyage',
      'ancient',
      'empire',
      'pioneer',
      'exploration',
      'explorer',
      'trail',
      'century',
      'archives',
      'forgotten',
    ],
  }),
]);

const BY_ID = new Map(TEMPLATES.map((t) => [t.id, t]));

function templateDir(template) {
  return path.join(__dirname, '..', 'one_minute_documentary', 'templates', template.dirName);
}

function toTemplate(template) {
  return { id: template.id, dir: templateDir(template), label: template.label };
}

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function matchesKeyword(haystack, keyword) {
  return new RegExp(`\\b${escapeRegExp(keyword)}\\b`, 'i').test(haystack);
}

function normalizedGenre(job) {
  const raw = job?.documentaryOptions?.genre ?? job?.genre ?? '';
  return String(raw).trim().toLowerCase();
}

function collectHaystack(job) {
  if (!job || typeof job !== 'object') return '';
  const parts = [];
  const push = (v) => {
    const s = String(v ?? '').trim();
    if (s) parts.push(s);
  };
  push(job.documentaryOptions?.genre);
  push(job.genre);
  push(job.videoStyle);
  push(job.title);
  push(job.channel?.name);
  push(job.channel?.title);
  return parts.join(' ');
}

/**
 * Resolve which documentary template a job should render with.
 * Pure: no I/O except path resolution; same job always resolves the same way.
 *
 * Priority:
 *   1. Explicit genre (job.documentaryOptions.genre, else job.genre):
 *      'crime' | 'mystery' | 'true crime' -> crime;
 *      'history' | 'historical' | 'expedition' -> history.
 *   2. Keyword scan over genre + videoStyle + title + channel name:
 *      crime keywords first, then history keywords (so a title matching
 *      both, e.g. "The Vanished Expedition", lands on crime/mystery).
 *   3. Everything else (including unknown genres) -> standard.
 */
function resolveDocumentaryTemplate(job) {
  const genre = normalizedGenre(job);
  for (const template of TEMPLATES) {
    if (template.id !== 'standard' && template.genres.includes(genre)) {
      return toTemplate(template);
    }
  }

  const haystack = collectHaystack(job);
  if (haystack) {
    for (const template of TEMPLATES) {
      if (template.id === 'standard') continue;
      if (template.keywords.some((kw) => matchesKeyword(haystack, kw))) {
        return toTemplate(template);
      }
    }
  }

  return toTemplate(BY_ID.get('standard'));
}

/** All available templates, in default-first order. */
function listTemplates() {
  return TEMPLATES.map(toTemplate);
}

module.exports = {
  TEMPLATES,
  listTemplates,
  resolveDocumentaryTemplate,
};
