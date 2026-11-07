const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../src/renderer.js'), 'utf8');
const helpers = source.slice(source.indexOf('function withUiTimeout('), source.indexOf('async function api('));

(async () => {
  const context = {setTimeout,clearTimeout,AbortController,AbortSignal,Error,
    fetch:(_url, options) => new Promise((_, reject) => options.signal.addEventListener('abort', () => reject(new Error('aborted'))))};
  vm.createContext(context); vm.runInContext(helpers, context);
  await assert.rejects(context.fetchJsonWithDeadline('/api/fonts', {timeoutMs:10}), /taking too long/);
  await assert.rejects(context.withUiTimeout(new Promise(()=>{}), 10, 'Saving settings'), /Saving settings took too long/);
  context.fetch = async () => ({ok:true,json:async()=>({saved:true})});
  assert.equal((await context.fetchJsonWithDeadline('/api/status', {timeoutMs:100})).payload.saved,true);

  const settingsCode = source.slice(source.indexOf('const SettingsController = {'), source.indexOf('function saveSettings()'));
  let removed = false;
  const legacy = JSON.stringify({apiKeys:{openrouter:'test-only-key'}});
  Object.assign(context, {window:{electronAPI:{loadSecretSettings:async()=>({}),saveSecretSettings:async()=>{throw new Error('disk unavailable');}}},
    localStorage:{getItem:()=>legacy,removeItem:()=>{removed=true;}},console:{log(){},error(){},warn(){}},
    AppState:{apiKeys:{},llmProvider:'openrouter'},$:()=>null,
    normalizeLlmModelForProvider:()=> 'qwen/qwen3.5-flash-02-23',updateGenerationCostProfile(){},
    applyBrainEngineDefaults(){},syncLlmModelSelects(){},providerDefaultModel:()=> 'qwen/qwen3.5-flash-02-23',
    checkOpenRouterAccess:async()=>({}),renderProviderPanels(){},LocalAgent:{refresh:async()=>{}}});
  vm.runInContext(settingsCode + '\nthis.controller=SettingsController;',context);
  context.controller.updateApiStatus=()=>{};context.controller.loadYouTubeSessionStatus=async()=>{};
  await context.controller.load();
  assert.equal(removed,false,'Legacy settings must survive failed secure migration');
  context.window.electronAPI.saveSecretSettings=async()=>({saved:true});
  await context.controller.load(); assert.equal(removed,true);
  assert(source.includes('response.audio_url || response.audio'), 'Preview must accept the API audio result');
  assert(!source.includes('FormPersistence?.save'), 'Voice selection must use the real persistence controller');
  console.log('Settings checks passed: deadlines, secure migration failure/recovery, preview response, voice persistence.');
})().catch(error=>{console.error(error);process.exitCode=1;});
