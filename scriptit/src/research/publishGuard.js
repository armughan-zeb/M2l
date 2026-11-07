'use strict';

const fs = require('fs');

function validateScriptForPublish({ script, title, description, mediaPath, uploadResult, meta } = {}) {
  const errors = [];
  const warnings = [];
  const text = String(script || '').trim();
  if (!text) errors.push('Script text is empty');
  if (text.split(/\s+/).filter(Boolean).length < 60) errors.push('Script is below the 60-word minimum');
  if (!String(title || '').trim()) errors.push('Video title is missing');
  if (!String(description || '').trim()) errors.push('Video description is missing');
  if (mediaPath && !fs.existsSync(mediaPath)) errors.push('Rendered media file is missing');
  if (meta && meta.publishable === false) errors.push('Script is marked partial and is not publishable');
  if (meta?.quality?.passes === false) errors.push('Script quality checks did not pass');
  if (meta?.evidence && meta.evidence.sufficient === false) errors.push('Research evidence is incomplete');
  if (uploadResult && (!uploadResult.id || uploadResult.status === 'failed')) errors.push('Upload response could not be verified');
  if (meta?.safeFallback) warnings.push('A deterministic evidence-backed fallback was used');
  return {
    ok: errors.length === 0,
    status: errors.length ? 'blocked' : 'ready_to_publish',
    data: { title: String(title || '').trim(), words: text ? text.split(/\s+/).length : 0 },
    warnings,
    errors,
    retryable: errors.length > 0,
  };
}

module.exports = { validateScriptForPublish };
