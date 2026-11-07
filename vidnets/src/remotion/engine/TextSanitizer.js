/**
 * TextSanitizer.js
 *
 * Remotion is a layout layer, not a second editorial director. New manifests
 * carry an editorial_contract produced by Python; this module preserves that
 * wording exactly. The compact legacy fallback exists only for projects saved
 * before the media-first contract.
 */

import { hasApprovedDirectorCopy } from './DirectorMotion.js';

const LEADING_GLUE = new Set(['a', 'an', 'the', 'and', 'or', 'but', 'of', 'in', 'on', 'at', 'to', 'for', 'with', 'by', 'from']);
const GENERIC_WORDS = new Set(['scene', 'visual', 'image', 'photo', 'photograph', 'footage', 'video', 'moment', 'story', 'chapter', 'detail', 'thing', 'things']);

function clean(value) {
  return String(value ?? '').replace(/\s+/g, ' ').trim();
}

function tokens(value) {
  return clean(value)
    .replace(/[“”]/g, '"')
    .split(/\s+/)
    .map((token) => token.replace(/^[^\p{L}\p{N}%$£€]+|[^\p{L}\p{N}%$£€/'-]+$/gu, ''))
    .filter(Boolean);
}

function approvedContract(props = {}) {
  const contract = props?.editorial_contract;
  return contract && typeof contract === 'object' ? contract : {};
}

function compactLegacyText(value, maxWords = 12) {
  const phrase = clean(value).replace(/[\r\n]+/g, ' ');
  if (!phrase) return '';
  const rawWords = phrase.split(/\s+/).filter(Boolean);
  if (rawWords.length <= maxWords) {
    return phrase.replace(/[ ,;:—–-]+$/, '');
  }
  const words = tokens(phrase);
  while (words.length > 1 && LEADING_GLUE.has(words[0].toLowerCase())) words.shift();
  while (words.length > 1 && LEADING_GLUE.has(words[words.length - 1].toLowerCase())) words.pop();
  const meaningful = words.filter((word) => !GENERIC_WORDS.has(word.toLowerCase()));
  return (meaningful.length ? meaningful : words).slice(0, Math.max(1, maxWords)).join(' ').replace(/[ ,;:—–-]+$/, '');
}

/**
 * Return a concise display phrase. Contract text wins and is never rebuilt
 * from narration, protecting negation, proper names, quoted language, money,
 * and units from client-side cleanup.
 */
export function selectDocumentaryHeadline(props = {}, scene = {}, maxWords = 12) {
  const p = props || {};
  const s = scene || {};
  const contract = approvedContract(p);
  const approved = clean(contract.display_text || p.display_text);
  if (approved) return approved;

  const candidates = [
    p._contract_text,
    p.title,
    p.headline,
    p.text,
    p.label,
    s.semantic_beat?.display_text,
    s.semantic_beat?.emphasis,
    p.highlight,
  ];
  for (const candidate of candidates) {
    const value = clean(candidate);
    if (!value) continue;
    return compactLegacyText(value, maxWords);
  }
  return compactLegacyText(s.narration || '', maxWords);
}

/** Backward-compatible export used by older component files. */
export function cleanDocumentaryHighlight(text, maxWords = 12) {
  return compactLegacyText(text, maxWords);
}

/**
 * Normalize transport fields without changing their approved semantic value.
 * Legacy manifests receive a concise fallback, but contract manifests pass
 * through unchanged for the renderer to lay out.
 */
export function sanitizeDocumentaryProps(props = {}, scene = {}) {
  const cleanProps = { ...(props || {}) };
  const s = scene || {};
  const sceneContract = s?.editorial_contract || {};
  const contract = sceneContract.approved === true && Number(sceneContract.version) >= 4
    ? sceneContract
    : approvedContract(cleanProps);
  const approved = clean(contract.display_text || cleanProps.display_text);
  const directorScene = scene?.director_treatment_enabled === true
    || Object.prototype.hasOwnProperty.call(scene?.text || {}, 'overlay_policy');
  if (directorScene && !hasApprovedDirectorCopy(scene)) {
    // Grounded data labels remain in their semantic fields. Narration and
    // legacy props must never become an unapproved on-screen heading.
    // Fall back gracefully to available text rather than deleting all keys,
    // ensuring no blank text cards or "adhoora" copy appear on screen.
    const fallback = clean(
      cleanProps.display_text ||
      cleanProps.title ||
      cleanProps.headline ||
      cleanProps.text ||
      scene?.display_text ||
      scene?.title ||
      scene?.narration ||
      ''
    );
    if (fallback) {
      const fallbackTitle = compactLegacyText(fallback, 12);
      cleanProps.title = fallbackTitle;
      cleanProps.headline = fallbackTitle;
      cleanProps.display_text = fallbackTitle;
    } else {
      for (const key of ['title', 'headline', 'display_text', 'text', '_contract_text',
        'highlight', 'emphasis', 'emphasis_phrase', 'highlight_words', 'kicker', 'subtitle']) {
        delete cleanProps[key];
      }
    }
    return cleanProps;
  }
  const title = approved || selectDocumentaryHeadline(cleanProps, scene, 12);

  if (title) {
    cleanProps.title = title;
    cleanProps.headline = title;
    cleanProps.display_text = title;
  }
  const emphasis = clean(contract.emphasis_phrase || cleanProps.emphasis_phrase || cleanProps.emphasis);
  if (emphasis) cleanProps.emphasis_phrase = emphasis;
  if (Array.isArray(contract.emphasis_words)) cleanProps.emphasis_words = [...contract.emphasis_words];
  if (cleanProps._contract_text && !approved) cleanProps._contract_text = compactLegacyText(cleanProps._contract_text, 12);

  // Supporting copy is permitted, but never converted into a fabricated
  // source label or used as a replacement headline.
  for (const key of ['subtitle', 'detail', 'kicker', 'label']) {
    if (typeof cleanProps[key] === 'string') cleanProps[key] = clean(cleanProps[key]);
  }
  return cleanProps;
}
