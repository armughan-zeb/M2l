const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const Store = require('electron-store');
const { generateScript } = require('../src/agents/scriptWriter');
const { classifyTopic } = require('../src/agents/classifier');
const { buildNarratorProfile, NARRATOR_MODEL } = require('../src/agents/narratorVideo');
const { getResearchStore } = require('../src/research/store');
const { runResearchJob, resumeResearchJob } = require('../src/research/pipeline');
const { providerHealth } = require('../src/research/resilience');
const { validateScriptForPublish } = require('../src/research/publishGuard');
const { extractStructureFromVideo, generateScriptFromStructure } = require('../src/agents/structureCloner');

const store = new Store({
  name: 'scriptit-config',
  encryptionKey: 'scriptit-local-encryption-v1',
});

console.log('[main] store initialized');

/* ─── CHANNELS ─── */

let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 980,
    minHeight: 640,
    backgroundColor: '#0f1115',
    title: 'ScriptIT - YouTube Script Generator Pro',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  mainWindow.loadFile(path.join(__dirname, '..', 'src', 'ui', 'index.html'));

  if (process.argv.includes('--dev')) {
    mainWindow.webContents.openDevTools({ mode: 'detach' });
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

ipcMain.handle('settings:get', () => {
  return {
    apiKeySet: !!store.get('apiKey'),
  };
});

ipcMain.handle('settings:setApiKey', async (_evt, key) => {
  try {
    if (typeof key !== 'string') {
      return { ok: false, error: 'Key must be a string (got ' + typeof key + ')' };
    }
    const trimmed = key.trim();
    if (trimmed.length === 0) {
      return { ok: false, error: 'Key is empty' };
    }
    if (trimmed.length < 20) {
      return { ok: false, error: 'Key is too short (' + trimmed.length + ' chars). OpenRouter keys are 50+ chars and start with sk-or-v1-' };
    }
    if (!trimmed.startsWith('sk-or-')) {
      return { ok: false, error: 'Key does not start with "sk-or-". Are you sure this is an OpenRouter key?' };
    }
    store.set('apiKey', trimmed);
    const back = store.get('apiKey');
    if (back !== trimmed) {
      return { ok: false, error: 'Storage verification failed' };
    }
    return { ok: true };
  } catch (err) {
    console.error('[main] settings:setApiKey error:', err);
    return { ok: false, error: err.message || String(err) };
  }
});

ipcMain.handle('settings:clearApiKey', () => {
  store.delete('apiKey');
  return { ok: true };
});

ipcMain.handle('script:generate', async (_evt, payload) => {
  try {
    const apiKey = store.get('apiKey');
    const result = await generateScript({ ...(payload || {}), apiKey });
    try {
      const store = await getResearchStore();
      await store.saveScriptRun({
        jobId: result.meta?.jobId || null,
        topic: result.meta?.topic || payload?.topic || '',
        status: result.meta?.status || 'draft',
        script: result.script || '',
        metadata: result.meta || {},
      });
    } catch (storeErr) {
      console.warn('[generate] script run persistence skipped:', storeErr.message);
    }
    return {
      ok: true,
      status: result.meta?.status || 'ready_to_publish',
      data: result,
      warnings: result.meta?.quality?.issues || [],
      errors: [],
      retryable: result.meta?.evidence?.sufficient === false || result.meta?.quality?.passes === false,
    };
  } catch (err) {
    console.error('[generate] error:', err);
    return {
      ok: false,
      status: 'failed',
      data: null,
      warnings: [],
      errors: [err.message || String(err)],
      retryable: true,
      error: err.message || String(err),
    };
  }
});

ipcMain.handle('research:get', async (_evt, jobId) => {
  try {
    const store = await getResearchStore();
    const job = await store.getJob(String(jobId || '').trim());
    return job ? { ok: true, status: job.status, data: job, warnings: job.warnings, errors: job.errors, retryable: job.retryable } : { ok: false, status: 'not_found', data: null, warnings: [], errors: ['Research job not found'], retryable: false };
  } catch (err) {
    return { ok: false, status: 'failed', data: null, warnings: [], errors: [err.message || String(err)], retryable: true };
  }
});

ipcMain.handle('research:start', async (_evt, payload) => {
  try {
    const input = payload || {};
    const result = await runResearchJob({
      topic: String(input.topic || '').trim(),
      input: { ...input, apiKey: undefined },
      backgroundDeep: input.backgroundDeep !== false,
      includeReddit: input.includeReddit !== false,
    });
    return { ok: true, status: result.partial ? 'partial' : 'fast_ready', data: result, warnings: result.warnings || [], errors: [], retryable: Boolean(result.partial) };
  } catch (err) {
    return { ok: false, status: 'failed', data: null, warnings: [], errors: [err.message || String(err)], retryable: true };
  }
});

ipcMain.handle('research:resume', async (_evt, jobId) => {
  try {
    const result = await resumeResearchJob(String(jobId || '').trim());
    return { ok: true, status: result.partial ? 'partial' : 'context_ready', data: result, warnings: result.warnings || [], errors: [], retryable: Boolean(result.partial) };
  } catch (err) {
    return { ok: false, status: 'failed', data: null, warnings: [], errors: [err.message || String(err)], retryable: true };
  }
});

ipcMain.handle('research:claims', async (_evt, query) => {
  try {
    const store = await getResearchStore();
    const claims = await store.searchClaims(query);
    return { ok: true, status: 'ready', data: claims, warnings: [], errors: [], retryable: false };
  } catch (err) {
    return { ok: false, status: 'failed', data: [], warnings: [], errors: [err.message || String(err)], retryable: true };
  }
});

ipcMain.handle('research:brief', async (_evt, jobId) => {
  try {
    const store = await getResearchStore();
    const job = await store.getJob(String(jobId || '').trim());
    return job?.result?.brief ? { ok: true, status: job.status, data: job.result.brief, warnings: job.warnings, errors: job.errors, retryable: job.retryable } : { ok: false, status: 'not_found', data: null, warnings: [], errors: ['Research brief not found'], retryable: true };
  } catch (err) {
    return { ok: false, status: 'failed', data: null, warnings: [], errors: [err.message || String(err)], retryable: true };
  }
});

ipcMain.handle('research:evidence', async (_evt, jobId) => {
  try {
    const store = await getResearchStore();
    const evidence = await store.getEvidenceMap(String(jobId || '').trim());
    return evidence ? { ok: true, status: 'ready', data: evidence, warnings: [], errors: [], retryable: false } : { ok: false, status: 'not_found', data: null, warnings: [], errors: ['Evidence map not found'], retryable: true };
  } catch (err) {
    return { ok: false, status: 'failed', data: null, warnings: [], errors: [err.message || String(err)], retryable: true };
  }
});

ipcMain.handle('research:health', async () => {
  try {
    const store = await getResearchStore();
    return { ok: true, status: 'ready', data: { providers: providerHealth(), database: await store.integrityCheck(), recovered: store.recovered }, warnings: [], errors: [], retryable: false };
  } catch (err) {
    return { ok: false, status: 'degraded', data: { providers: providerHealth(), database: false }, warnings: [], errors: [err.message || String(err)], retryable: true };
  }
});

ipcMain.handle('script:validatePublish', (_evt, payload) => validateScriptForPublish(payload || {}));

ipcMain.handle('export:markdown', async (_evt, payload) => {
  try {
    const { defaultName, content } = payload || {};
    const safe = (defaultName || 'script').replace(/[^a-z0-9-_ ]/gi, '').slice(0, 80).trim() || 'script';
    const res = await dialog.showSaveDialog(mainWindow, {
      title: 'Save Script',
      defaultPath: `${safe}.md`,
      filters: [{ name: 'Markdown', extensions: ['md'] }, { name: 'Text', extensions: ['txt'] }],
    });
    if (res.canceled || !res.filePath) return { ok: false, status: 'cancelled', canceled: true, warnings: [], errors: [], retryable: false };
    if (typeof content !== 'string' || !content.trim()) return { ok: false, status: 'invalid', error: 'Nothing to export', warnings: [], errors: ['Nothing to export'], retryable: false };
    fs.writeFileSync(res.filePath, content, 'utf8');
    return { ok: true, status: 'saved', path: res.filePath, warnings: [], errors: [], retryable: false };
  } catch (err) {
    return { ok: false, status: 'failed', warnings: [], errors: [err.message || String(err)], retryable: true, error: err.message || String(err) };
  }
});

ipcMain.handle('classifier:classify', (_evt, topic) => {
  return classifyTopic(topic);
});

ipcMain.handle('narrator:profile', async (_evt, payload) => {
  try {
    const apiKey = store.get('apiKey');
    if (!apiKey) {
      return { ok: false, error: 'OpenRouter API key not set. Open settings to add one.' };
    }
    const result = await buildNarratorProfile({
      videoUrl: String(payload && payload.videoUrl || '').trim(),
      model: String(payload && payload.model || '').trim() || NARRATOR_MODEL,
      apiKey,
      useCache: !payload || payload.useCache !== false,
    });
    return { ok: true, data: result };
  } catch (err) {
    console.error('[narrator] error:', err);
    return { ok: false, error: err.message || String(err) };
  }
});

ipcMain.handle('structure:extractFromYoutube', async (_evt, payload) => {
  try {
    const apiKey = store.get('apiKey');
    const result = await extractStructureFromVideo({
      videoUrl: String(payload && payload.videoUrl || '').trim(),
      apiKey,
      model: String(payload && payload.model || '').trim() || undefined,
      useCache: !payload || payload.useCache !== false,
    });
    return { ok: true, data: result };
  } catch (err) {
    console.error('[structure:extractFromYoutube] error:', err);
    return { ok: false, error: err.message || String(err) };
  }
});

ipcMain.handle('script:generateFromYoutubeReference', async (_evt, payload) => {
  try {
    const apiKey = store.get('apiKey');
    const result = await generateScriptFromStructure({
      referenceStructure: payload && payload.referenceStructure,
      newTitle: payload && (payload.newTitle || payload.topic),
      duration: payload && payload.duration,
      voice: payload && payload.voice,
      evidence: payload && payload.evidence,
      apiKey,
      model: payload && payload.model,
    });
    return { ok: true, data: result };
  } catch (err) {
    console.error('[script:generateFromYoutubeReference] error:', err);
    return { ok: false, error: err.message || String(err) };
  }
});

ipcMain.handle('shell:openExternal', async (_evt, url) => {
  await shell.openExternal(url);
  return { ok: true };
});
