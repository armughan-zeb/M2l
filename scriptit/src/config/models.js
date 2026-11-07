const MODELS = { cheap: [{ id: 'nvidia/nemotron-3.5-super-vl:free', label: 'Nemotron 3.5 Super VL Free', tier: 'cheap', note: 'Operator allowed model' }], better: [], premium: [], long: [], free: [] };
function allModels() { return MODELS.cheap; }
function defaultModel() { return 'nvidia/nemotron-3.5-super-vl:free'; }
module.exports = { MODELS, allModels, defaultModel };
