const DEFAULT_MODEL = 'nvidia/nemotron-3.5-super-vl:free';
const MODELS = [{ id: DEFAULT_MODEL, label: 'Nemotron 3.5 Super VL Free' }];
const NARRATOR_MODELS = MODELS;
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
let generationCount = 0;
let detectedType = 'general';
let classifyTimer = null;
let narratorProfile = null;
let clonedStructure = null;
let lastGeneratePayload = null;

function navigateTo(page) {
  $$('.nav-item').forEach((item) => item.classList.toggle('active', item.dataset.page === page));
  $$('.page').forEach((item) => item.classList.toggle('active', item.id === `page-${page}`));
  if (page === 'home') refreshHome();
  if (page === 'profile') refreshProfile();
  if (page === 'settings') refreshSettingsStatus();
}

function showStatus(message, kind) {
  const element = $('#status');
  element.textContent = message;
  const colors = { error: 'var(--err)', warn: 'var(--warn)', success: 'var(--green)' };
  element.style.color = colors[kind] || 'var(--text-mute)';
}

function showStatusRight(message) {
  $('#statusRight').textContent = message || '';
}

function populateModelSelect() {
  $('#model').innerHTML = MODELS.map((model) => `<option value="${model.id}"${model.id === DEFAULT_MODEL ? ' selected' : ''}>${model.label}</option>`).join('');
  $('#narratorModel').innerHTML = NARRATOR_MODELS.map((model) => `<option value="${model.id}"${model.id === DEFAULT_MODEL ? ' selected' : ''}>${model.label}</option>`).join('');
}

function renderNarratorProfileBox() {
  const box = $('#narratorProfile');
  const status = $('#narratorStatus');
  if (!narratorProfile) {
    box.hidden = true;
    status.textContent = '';
    return;
  }
  box.hidden = false;
  const video = narratorProfile.video || {};
  const profile = narratorProfile.profile || {};
  const minutes = video.durationSec ? Math.round(video.durationSec / 60) : '';
  const rows = [
    ['Video', video.title || video.url || ''],
    ['Channel', video.channel || ''],
    ['Duration', minutes ? minutes + ' min' : ''],
    ['Transcript', narratorProfile.transcriptChars ? narratorProfile.transcriptChars.toLocaleString() + ' chars' : ''],
    ['Structure', profile.structure || ''],
    ['Tonality', profile.tonality || ''],
    ['Script flow', profile.scriptFlow || ''],
    ['Audience behaviour', profile.audienceBehaviour || ''],
    ['Hook style', profile.hookStyle || ''],
    ['Pacing', profile.pacing || ''],
    ['Sentence rhythm', profile.sentenceRhythm || ''],
    ['Retention tricks', profile.retentionTricks || ''],
    ['Signature phrases', (profile.signaturePhrases || []).join(' · ')],
  ].filter(([, value]) => value);
  box.innerHTML = rows.map(([label, value]) => `<div class="np-row"><span class="np-label">${label}</span><span class="np-value">${value}</span></div>`).join('');
    status.textContent = `Profile built with ${narratorProfile.model || 'google/gemini-2.5-flash'}`;
  status.style.color = 'var(--green)';
}

async function onAnalyzeNarrator() {
  const url = $('#narratorUrl').value.trim();
  if (!url) return showStatus('Paste a YouTube video link to analyze.', 'warn');
  const settings = await window.scriptit.getSettings();
  if (!settings.apiKeySet) {
    showStatus('Set your OpenRouter API key in Settings.', 'warn');
    navigateTo('settings');
    return;
  }
  const button = $('#narratorAnalyze');
  button.disabled = true;
  button.textContent = 'Analyzing...';
  const status = $('#narratorStatus');
  status.style.color = 'var(--text-mute)';
  status.textContent = 'Fetching transcript via yt-dlp and building the style profile...';
  try {
    const result = await window.scriptit.narratorProfile({ videoUrl: url, model: $('#narratorModel').value });
    if (!result.ok) throw new Error(result.error);
    narratorProfile = result.data;
    renderNarratorProfileBox();
    showStatus('Narrator profile ready — new scripts will match this style.', 'success');
  } catch (error) {
    narratorProfile = null;
    $('#narratorProfile').hidden = true;
    status.style.color = 'var(--err)';
    status.textContent = '';
    showStatus(error.message || String(error), 'error');
  } finally {
    button.disabled = false;
    button.textContent = 'Analyze Voice';
  }
}

function renderClonedStructureBox() {
  const box = $('#clonedStructurePreview');
  const status = $('#narratorStatus');
  if (!clonedStructure) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const struct = clonedStructure.structure || clonedStructure;
  const video = clonedStructure.video || {};
  const sequences = struct.sequences || [];
  const hook = struct.hookFormula || {};

  let html = `
    <div class="sp-header">
      <span class="sp-title">Cloned Script Structure (${sequences.length} Beats)</span>
      <button class="sp-clear" id="clearStructureBtn" type="button">Remove</button>
    </div>
    <div class="np-row"><span class="np-label">Reference</span><span class="np-value">${video.title || struct.referenceTitle || 'YouTube Video'}</span></div>
    <div class="np-row"><span class="np-label">Hook Strategy</span><span class="np-value">${hook.type || 'result-first'}: ${hook.openCuriosityLoopPattern || hook.validateClickPattern || 'Instant curiosity loop'}</span></div>
    <div style="margin-top:8px;">
  `;

  sequences.forEach((seq) => {
    const sharePct = Math.round((seq.targetShare || 0.25) * 100);
    html += `
      <div class="sp-beat">
        <div class="sp-beat-title">
          <span>${seq.index || ''}. ${seq.name || 'Sequence Beat'}</span>
          <span class="sp-beat-share">${sharePct}% runtime · ${seq.pacing || 'measured'}</span>
        </div>
        <div class="sp-beat-desc">${seq.role || ''}</div>
        ${seq.retentionDevice ? `<span class="sp-tag">Retention: ${seq.retentionDevice}</span>` : ''}
        ${seq.transitionMechanism ? `<span class="sp-tag">Bridge: ${seq.transitionMechanism}</span>` : ''}
      </div>
    `;
  });

  html += `</div>`;
  box.innerHTML = html;
  status.textContent = `Structure cloned: ${sequences.length} sequence beats reverse-engineered from reference`;
  status.style.color = 'var(--green)';

  const clearBtn = $('#clearStructureBtn');
  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      clonedStructure = null;
      box.hidden = true;
      status.textContent = '';
      showStatus('Cloned structure removed.', 'success');
    });
  }
}

async function onCloneStructure() {
  const url = $('#narratorUrl').value.trim();
  if (!url) return showStatus('Paste a YouTube video link to clone structure.', 'warn');
  const settings = await window.scriptit.getSettings();
  if (!settings.apiKeySet) {
    showStatus('Set your OpenRouter API key in Settings.', 'warn');
    navigateTo('settings');
    return;
  }
  const button = $('#structureCloneBtn');
  button.disabled = true;
  button.textContent = 'Cloning...';
  const status = $('#narratorStatus');
  status.style.color = 'var(--text-mute)';
  status.textContent = 'Extracting transcript via yt-dlp and reverse-engineering script architecture...';
  try {
    const result = await window.scriptit.extractVideoStructure({ videoUrl: url, model: $('#narratorModel').value });
    if (!result.ok) throw new Error(result.error);
    clonedStructure = result.data;
    renderClonedStructureBox();
    showStatus('Reference structure cloned — new scripts will follow this outline and pacing.', 'success');
  } catch (error) {
    clonedStructure = null;
    $('#clonedStructurePreview').hidden = true;
    status.style.color = 'var(--err)';
    status.textContent = '';
    showStatus(error.message || String(error), 'error');
  } finally {
    button.disabled = false;
    button.textContent = 'Clone Structure';
  }
}

function refreshHome() {
  $('#statScripts').textContent = generationCount;
  $('#statModel').textContent = ($('#model').value || DEFAULT_MODEL).split('/').pop();
  window.scriptit.getSettings().then((settings) => {
    $('#statKey').textContent = settings.apiKeySet ? 'Set' : 'Not set';
    $('#statKey').style.color = settings.apiKeySet ? 'var(--green)' : 'var(--err)';
  }).catch(console.error);
}

function refreshProfile() {
  $('#profModel').textContent = ($('#model').value || DEFAULT_MODEL).split('/').pop();
  window.scriptit.getSettings().then((settings) => {
    $('#profKey').textContent = settings.apiKeySet ? 'Set' : 'Not set';
    $('#profKey').style.color = settings.apiKeySet ? 'var(--green)' : 'var(--err)';
  }).catch(console.error);
}

function setLoading(active) {
  const button = $('#generate');
  button.disabled = active;
  button.querySelector('.btn-label').textContent = active ? 'Generating...' : 'Generate Script';
  button.querySelector('.btn-spinner').hidden = !active;
  const retryButton = $('#retryResearchBtn');
  if (retryButton) retryButton.disabled = active;
}

function switchGenTab(name) {
  $$('.gen-output-tabs .tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.tab === name));
  const hasContent = $('#rawView').textContent.trim().length > 0;
  $('#empty').hidden = hasContent;
  $('#scriptView').hidden = name !== 'script' || !hasContent;
  $('#rawView').hidden = name !== 'raw' || !hasContent;
}

function renderScript(raw) {
  const view = $('#scriptView');
  view.innerHTML = '';
  for (const paragraph of raw.split(/\n\s*\n/).map((item) => item.trim()).filter(Boolean)) {
    const element = document.createElement('p');
    element.textContent = paragraph;
    view.appendChild(element);
  }
}

function buildMarkdown({ topic, raw }) {
  return `# ${topic}\n\n${raw}\n`;
}

function activeContentType() {
  return $('#contentType').value === 'auto' ? detectedType : $('#contentType').value;
}

function onTopicChange() {
  clearTimeout(classifyTimer);
  const topic = $('#topic').value.trim();
  if (lastGeneratePayload && topic !== lastGeneratePayload.topic) {
    lastGeneratePayload = null;
    $('#retryResearchBtn').hidden = true;
  }
  if (!topic) {
    $('#ctClassify').hidden = true;
    return;
  }
  classifyTimer = setTimeout(async () => {
    try {
      const result = await window.scriptit.classify(topic);
      detectedType = result.type || 'general';
      $('#ctBadge').textContent = detectedType.toUpperCase();
      $('#ctEngine').textContent = result.engine || '';
      $('#ctClassify').hidden = false;
    } catch (error) {
      console.error('[classify]', error);
    }
  }, 250);
}

function renderGenerationResult(result, startedAt) {
  if (!result || !result.ok) throw new Error(result?.error || 'Script generation did not complete.');
  const data = result.data || {};
  const script = String(data.script || '').trim();
  const meta = data.meta || {};
  if (!script) throw new Error('Script generation returned an empty result. Retry research and try again.');

  const limited = meta.evidence?.sufficient === false || meta.quality?.passes === false;
  generationCount += 1;
  renderScript(script);
  $('#rawView').textContent = script;
  $('#researchPreview').hidden = false;
  const timing = meta.timing || {};
  const researchSeconds = Number(timing.researchMs || 0) / 1000;
  const writingSeconds = Number(timing.writingMs || 0) / 1000;
  $('#rpStatus').textContent = limited ? 'Status: Script ready — limited source coverage' : 'Status: Evidence-backed script ready';
  $('#rpStatus').style.color = limited ? 'var(--warn)' : 'var(--green)';
  $('#rpEngine').textContent = `Strategy: ${meta.engineName || 'research and writing'}${timing.totalMs ? ` · Research ${researchSeconds.toFixed(1)}s · Writing ${writingSeconds.toFixed(1)}s` : ''}`;
  $('#rpSources').textContent = `${meta.evidence?.sourceCount || 0} sources reviewed`;
  $('#rpFacts').textContent = `${meta.evidence?.claims || 0} evidence claims`;
  const coverage = meta.evidence?.coverage;
  $('#rpCoverage').textContent = coverage
    ? `Question coverage: ${Math.round((coverage.score || 0) * 100)}% (${coverage.answered || 0} answered, ${coverage.missing || 0} missing)`
    : 'Question coverage: —';
  $('#rpCoverage').style.color = coverage && coverage.score < 0.5 ? 'var(--warn)' : 'var(--text-mute)';
  const conflictCount = meta.evidence?.conflicts || 0;
  $('#rpConflicts').textContent = conflictCount
    ? `Conflicts: ${conflictCount} claims need qualification`
    : 'Conflicts: none detected';
  $('#rpConflicts').style.color = conflictCount ? 'var(--warn)' : 'var(--text-mute)';
  $('#rpFresh').textContent = `Freshest data: ${meta.evidence?.latestClaim || 'no dated sources found'}`;
  $('#rpCache').textContent = `Novelty similarity: ${Math.round((meta.quality?.similarity || 0) * 100)} percent`;
  const narratorMeta = meta.narrator;
  $('#rpNarrator').textContent = narratorMeta
    ? `Narrator: matched to ${(narratorMeta.videoTitle || narratorMeta.videoUrl || 'reference video')}${narratorMeta.error ? ' (profile failed: ' + narratorMeta.error + ')' : ''}`
    : 'Narrator: —';
  if ($('#rpClonedStructure')) {
    const cloned = meta.clonedFrom || meta.clonedStructure;
    $('#rpClonedStructure').textContent = cloned
      ? `Structure: cloned from ${meta.clonedFrom || 'reference video'}`
      : 'Structure: standard engine template';
  }
  const retryButton = $('#retryResearchBtn');
  retryButton.hidden = !limited;
  retryButton.disabled = false;
  const generatedMessage = limited
    ? `Script generated from the best available web research in ${((performance.now() - startedAt) / 1000).toFixed(1)} seconds. Weak details were qualified or omitted.`
    : meta.safeFallback
      ? 'Generated a conservative evidence-only script because the creative draft could not be verified.'
      : `Generated in ${((performance.now() - startedAt) / 1000).toFixed(1)} seconds`;
  showStatus(generatedMessage, limited || meta.safeFallback ? 'warn' : 'success');
  showStatusRight(`${meta.model || 'model'} | ${meta.researchMode || 'quick'} research | ${meta.engineName || 'research and writing'}`);
  refreshHome();
}

async function performGeneration(payload, loadingMessage) {
  setLoading(true);
  $('#retryResearchBtn').hidden = true;
  showStatus(loadingMessage || 'Searching the web and writing your script...');
  showStatusRight('');
  switchGenTab('script');
  const startedAt = performance.now();
  try {
    const result = await window.scriptit.generate(payload);
    renderGenerationResult(result, startedAt);
  } catch (error) {
    showStatus(error.message || String(error), 'error');
    if (lastGeneratePayload) $('#retryResearchBtn').hidden = false;
  } finally {
    setLoading(false);
  }
}

async function onGenerate() {
  const topic = $('#topic').value.trim();
  if (!topic) return showStatus('Enter a topic first.', 'warn');
  lastGeneratePayload = {
    topic,
    model: $('#model').value || DEFAULT_MODEL,
    duration: $('#duration').value,
    researchMode: $('#researchMode').value,
    contentType: activeContentType(),
    voice: $('#voice').value,
    narratorProfile,
    narratorVideoUrl: $('#narratorUrl').value.trim(),
    referenceStructure: clonedStructure,
  };
  await performGeneration(lastGeneratePayload);
}

async function onRetryResearch() {
  if (!lastGeneratePayload) return showStatus('Generate a script first.', 'warn');
  await performGeneration({
    ...lastGeneratePayload,
    useCache: false,
    forceFreshResearch: true,
  }, 'Refreshing web sources and rewriting the script...');
}

async function onExport() {
  const raw = $('#rawView').textContent;
  if (!raw) return showStatus('Generate a script first.', 'warn');
  const result = await window.scriptit.exportMarkdown({
    defaultName: $('#topic').value.trim() || 'script',
    content: buildMarkdown({ topic: $('#topic').value.trim() || 'Script', raw }),
  });
  if (result?.ok) showStatus(`Saved to ${result.path}`, 'success');
}

function refreshSettingsStatus() {
  window.scriptit.getSettings().then((settings) => {
    $('#keyStatus').textContent = settings.apiKeySet ? 'Key is set and encrypted locally' : 'No key set';
    $('#keyStatus').style.color = settings.apiKeySet ? 'var(--green)' : 'var(--err)';
    $('#keyBadge').textContent = settings.apiKeySet ? 'Key set' : 'Key not set';
  }).catch(console.error);
}

async function onSaveKey() {
  const key = $('#apiKey').value.trim();
  if (!key) return showStatus('Enter a key first.', 'warn');
  const result = await window.scriptit.setApiKey(key);
  if (!result?.ok) return showStatus(result?.error || 'Unable to save key.', 'error');
  $('#apiKey').value = '';
  refreshSettingsStatus();
  refreshHome();
  showStatus('API key saved.', 'success');
}

async function onClearKey() {
  await window.scriptit.clearApiKey();
  refreshSettingsStatus();
  refreshHome();
  showStatus('API key cleared.');
}

function bindEvents() {
  $$('.nav-item, [data-page]').forEach((item) => item.addEventListener('click', () => navigateTo(item.dataset.page)));
  $('#topic').addEventListener('input', onTopicChange);
  $('#generate').addEventListener('click', onGenerate);
  $('#retryResearchBtn').addEventListener('click', onRetryResearch);
  $$('.gen-output-tabs .tab').forEach((tab) => tab.addEventListener('click', () => switchGenTab(tab.dataset.tab)));
  $('#copyBtn').addEventListener('click', () => navigator.clipboard.writeText($('#rawView').textContent).then(() => showStatus('Copied', 'success')));
  $('#exportBtn').addEventListener('click', onExport);
  $('#clearTopicBtn').addEventListener('click', () => {
    $('#topic').value = '';
    $('#ctClassify').hidden = true;
    $('#narratorUrl').value = '';
    narratorProfile = null;
    clonedStructure = null;
    $('#narratorProfile').hidden = true;
    $('#clonedStructurePreview').hidden = true;
    $('#narratorStatus').textContent = '';
    $('#researchPreview').hidden = true;
    $('#retryResearchBtn').hidden = true;
    lastGeneratePayload = null;
    showStatus('Inputs cleared.');
  });
  $('#saveKey').addEventListener('click', onSaveKey);
  $('#clearKey').addEventListener('click', onClearKey);
  $('#narratorAnalyze').addEventListener('click', onAnalyzeNarrator);
  const cloneBtn = $('#structureCloneBtn');
  if (cloneBtn) cloneBtn.addEventListener('click', onCloneStructure);
  $('#narratorUrl').addEventListener('input', () => {
    narratorProfile = null;
    clonedStructure = null;
    $('#narratorProfile').hidden = true;
    $('#clonedStructurePreview').hidden = true;
    $('#narratorStatus').textContent = '';
  });
  $('#getKey').addEventListener('click', (event) => {
    event.preventDefault();
    window.scriptit.openExternal('https://openrouter.ai/keys');
  });
}

async function init() {
  populateModelSelect();
  bindEvents();
  const settings = await window.scriptit.getSettings();
  showStatus(settings.apiKeySet ? 'Ready to research and write.' : 'Add your OpenRouter API key in Settings to start.', settings.apiKeySet ? 'success' : 'warn');
  refreshHome();
  refreshProfile();
  refreshSettingsStatus();
}

document.addEventListener('DOMContentLoaded', init);
