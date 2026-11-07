'use strict';

// OpenRouter policy shared by CMS, ScriptIT, Pikgen, and Vidnets. Operators
// may select Qwen 3.5 Flash, NVIDIA Nemotron 3.5 Super VL, or a catalog-confirmed
// free model. The `:free` suffix is required for other models so a stale or edited
// setting can never route to a paid model. Model selection is per request;
// no provider-side fallback is added.
const MODEL = 'nvidia/nemotron-3.5-super-vl:free';
const QWEN_MODEL = 'qwen/qwen3.5-flash-02-23';
const NEMOTRON_MODEL = 'nvidia/nemotron-3.5-super-vl:free';
const SUPPORTED_MODELS = new Set([MODEL, NEMOTRON_MODEL, QWEN_MODEL]);
const FREE_MODEL_PATTERN = /^[a-z0-9][a-z0-9._-]*\/[a-z0-9][a-z0-9._-]*:free$/i;
const AUTO_MODELS = new Set();
let catalog = { expiresAt:0, ids:[] };
async function confirmModel(value) {
  const model=normalizeModel(value);
  if(SUPPORTED_MODELS.has(model))return model;
  if(Date.now()>=catalog.expiresAt){
    const response=await fetch('https://openrouter.ai/api/v1/models',{signal:AbortSignal.timeout(10000)});
    if(!response.ok)throw new Error('Free image-model catalog is unavailable; retry the same saved model');
    const data=await response.json();
    catalog={expiresAt:Date.now()+3600000,ids:(data.data||[]).filter(row=>FREE_MODEL_PATTERN.test(row.id||'') && row.architecture?.input_modalities?.includes('image') && row.pricing?.prompt!=null && row.pricing?.completion!=null && Number(row.pricing.prompt)===0 && Number(row.pricing.completion)===0).map(row=>row.id)};
  }
  if(!catalog.ids.includes(model))throw new Error('Selected model is not catalog-confirmed free with image input; saved work is retained');
  return model;
}

function isAllowedModel(value) {
  const model = String(value || '').trim();
  return SUPPORTED_MODELS.has(model) || FREE_MODEL_PATTERN.test(model);
}

function normalizeModel(value) {
  const model = String(value || MODEL).trim();
  if (!isAllowedModel(model)) {
    throw new Error('OpenRouter model must be Qwen 3.5 Flash or a free model ID ending in :free');
  }
  return model;
}

function lockPayload(payload) {
  const body = { ...payload, model: normalizeModel(payload && payload.model) };
  delete body.models;
  return body;
}

module.exports = { MODEL, NEMOTRON_MODEL, SUPPORTED_MODELS, FREE_MODEL_PATTERN, AUTO_MODELS, isAllowedModel, normalizeModel, lockPayload, confirmModel };
