'use strict';
/*
 * ScriptIT Bridge - exposes ScriptIT's script writer as a small HTTP API
 * so the YT CMS harness can call it over the network (port 3211).
 * Pure Node - no Electron required. Standalone requests can use ScriptIT's
 * environment variables; CMS requests mark credentialSource="cms" and use
 * only the request-scoped key supplied by the CMS credential store.
 */
const http = require('http');
const path = require('path');
const fs = require('fs');

const PORT = Number(process.env.SCRIPTIT_BRIDGE_PORT || 3211);
const HOST = process.env.SCRIPTIT_BRIDGE_HOST || '127.0.0.1';
const CORS_ORIGINS = new Set((process.env.SCRIPTIT_BRIDGE_CORS_ORIGINS || 'http://127.0.0.1:3210,http://localhost:3210,http://127.0.0.1:5173,http://localhost:5173')
  .split(',').map(origin => origin.trim()).filter(Boolean));
const ROOT = path.join(__dirname, '..');
const WRITER = path.join(ROOT, 'src', 'agents', 'scriptWriter.js');

const { generateScript } = (() => {
  if (!fs.existsSync(WRITER)) return {};
  try { return require(WRITER); } catch (err) { console.error('[ScriptIT bridge] failed to load writer:', err.message); return {}; }
})();

const RESEARCHER = path.join(ROOT, 'src', 'agents', 'researcher.js');
const { gatherResearch } = (() => {
  if (!fs.existsSync(RESEARCHER)) return {};
  try { return require(RESEARCHER); } catch (err) { console.error('[ScriptIT bridge] failed to load researcher:', err.message); return {}; }
})();

const NARRATOR = path.join(ROOT, 'src', 'agents', 'narratorVideo.js');

const { buildNarratorProfile } = (() => {
  if (!fs.existsSync(NARRATOR)) return {};
  try { return require(NARRATOR); } catch (err) { console.error('[ScriptIT bridge] failed to load narrator agent:', err.message); return {}; }
})();

const SYNTHESIZER = path.join(ROOT, 'src', 'agents', 'scriptSynthesizer.js');
const { synthesizeScript } = (() => {
  if (!fs.existsSync(SYNTHESIZER)) return {};
  try { return require(SYNTHESIZER); } catch (err) { console.error('[ScriptIT bridge] failed to load synthesizer:', err.message); return {}; }
})();

const STRUCTURE_CLONER = path.join(ROOT, 'src', 'agents', 'structureCloner.js');
const { extractStructureFromVideo, generateScriptFromStructure } = (() => {
  if (!fs.existsSync(STRUCTURE_CLONER)) return {};
  try { return require(STRUCTURE_CLONER); } catch (err) { console.error('[ScriptIT bridge] failed to load structure cloner:', err.message); return {}; }
})();

loadEnv(path.join(ROOT, '.env'));
loadEnv(path.join(ROOT, '.env.local'));

function loadEnv(filePath) {
  if (!fs.existsSync(filePath)) return;
  for (const line of fs.readFileSync(filePath, 'utf-8').split(/\r?\n/)) {
    const match = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
    if (!match) continue;
    const value = match[2].replace(/^["']|["']$/g, '').trim();
    if (value && !process.env[match[1]]) process.env[match[1]] = value;
  }
}

function readBody(req, limit) {
  return new Promise((resolve, reject) => {
    const max = limit || 10 * 1024 * 1024;
    const chunks = [];
    let size = 0;
    req.on('data', c => {
      size += c.length;
      if (size > max) { reject(new Error('Body too large')); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => {
      try {
        const raw = Buffer.concat(chunks).toString('utf8');
        resolve(raw ? JSON.parse(raw) : {});
      } catch (e) { reject(new Error('Invalid JSON body')); }
    });
    req.on('error', reject);
  });
}

function sendJson(res, code, obj) {
  const body = JSON.stringify(obj);
  const headers = {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'Content-Length': Buffer.byteLength(body)
  };
  res.writeHead(code, headers);
  res.end(body);
}

async function handleGenerate(body, engines = { generateScript, synthesizeScript }) {
  const topic = String(body.topic || body.title || '').trim();
  if (!topic) throw new Error('topic is required');
  const duration = String(body.duration || '').trim() || '10min';
  const model = String(body.model || '').trim() || 'qwen/qwen3-235b-a22b-2507';
  const contentType = String(body.contentType || '').trim() || 'auto';
  const voice = String(body.voice || '').trim() || 'conversational';
  const customStructure = String(body.customStructure || '').trim();
  const titleContext = String(body.titleContext || body.description || '').trim().slice(0, 12000);
  const description = String(body.description || titleContext).trim().slice(0, 12000);
  const referenceTranscript = String(body.referenceTranscript || '').trim().slice(0, 40000);

  const apiKey = body.credentialSource === 'cms'
    ? String(body.apiKey || '').trim()
    : String(body.apiKey || '').trim()
      || process.env.OPENROUTER_API_KEY
      || process.env.SCRIPTIT_OPENROUTER_API_KEY
      || '';

  let result = null;
  if (!apiKey && body.allowOfflineSynthesis !== true) throw new Error('Missing OpenRouter API key; live ScriptIT generation is blocked');

  if (engines.generateScript && apiKey) {
    try {
      result = await engines.generateScript({
        topic,
        titleContext,
        description,
        referenceTranscript,
        model,
        duration,
        customStructure,
        contentType,
        voice,
        narratorProfile: body.narratorProfile || null,
        narratorVideoUrl: String(body.narratorVideoUrl || '').trim() || '',
        referenceStructure: body.referenceStructure || null,
        researchMode: String(body.researchMode || '').trim() || 'thorough',
        auditModel: String(body.auditModel || '').trim(),
        consistencyModel: String(body.consistencyModel || '').trim(),
        initialDraft: body.initialDraft,
        generationCheckpoint: body.generationCheckpoint,
        totalDeadlineMs: Number(body.totalDeadlineMs) || 570000,
        useCache: body.useCache !== false,
        apiKey,
        timeoutMs: Math.min(120000, Math.max(30000, Number(body.timeoutMs) || 90000))
      });
    } catch (err) {
      if (body.allowOfflineSynthesis !== true) throw err;
    }
  }

  // Preserve rejected writer output and its actual verdict. Only an explicit
  // offline preview may synthesize text when the writer returned none.
  const needsRepair = !result || !result.script;
  if (needsRepair) {
    if (body.allowOfflineSynthesis === true && engines.synthesizeScript) {
      console.log(`[ScriptIT bridge] Synthesizing complete documentary script for "${topic}" (${duration})...`);
      const synthesized = engines.synthesizeScript({
        topic,
        duration,
        contentType,
        voice,
        customStructure,
        titleContext,
        description,
        referenceTranscript,
        narratorProfile: body.narratorProfile || null,
        evidence: result && result.meta && result.meta.evidence ? result.meta.evidence : null
      });
      result = synthesized;
      result.meta = { ...(result.meta || {}), publishable: false, status: 'draft_requires_review', isOfflineSynthesized: true,
        quality: { ...(result.meta?.quality || {}), passes: false, factualAudit: false, issues: ['Offline synthesis requires completed research and factual review'] } };
    } else if (!result || !result.script) {
      throw new Error(`ScriptIT failed to generate script for "${topic}" and synthesizer is unavailable.`);
    }
  }

  return {
    script: result.script,
    meta: result.meta || null,
    model: (result.meta && result.meta.modelUsed) || model,
    duration
  };
}

async function handleResearch(body) {
  if (!gatherResearch) throw new Error('ScriptIT researcher module could not be loaded');
  const topic = String(body.topic || body.query || '').trim();
  if (!topic) throw new Error('topic is required');
  const raw = await gatherResearch(topic, { includeReddit: body.includeReddit === true });
  return { ok: true, research: raw };
}

async function handleNarratorProfile(body) {
  if (!buildNarratorProfile) throw new Error('ScriptIT narrator agent could not be loaded');
  const apiKey = body.credentialSource === 'cms'
    ? String(body.apiKey || '').trim()
    : String(body.apiKey || '').trim()
      || process.env.OPENROUTER_API_KEY
      || process.env.SCRIPTIT_OPENROUTER_API_KEY
      || '';
  if (!apiKey) throw new Error('OpenRouter API key is not configured (set OPENROUTER_API_KEY in ScriptIT .env or pass apiKey)');
  const videoUrl = String(body.videoUrl || '').trim();
  if (!videoUrl) throw new Error('videoUrl is required');
  return buildNarratorProfile({
    videoUrl,
    model: String(body.model || '').trim() || 'google/gemini-2.5-flash',
    apiKey,
    useCache: body.useCache !== false,
  });
}

async function route(req, res, url) {
  const origin = String(req.headers.origin || '');
  if (origin && CORS_ORIGINS.has(origin)) {
    res.setHeader('Access-Control-Allow-Origin', origin);
    res.setHeader('Vary', 'Origin');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
    res.setHeader('Access-Control-Allow-Methods', 'GET,POST,OPTIONS');
  }
  if (req.method === 'OPTIONS') { res.writeHead(origin && CORS_ORIGINS.has(origin) ? 204 : 403); res.end(); return; }

  if (req.method === 'GET' && (url.pathname === '/' || url.pathname === '/health')) {
    return sendJson(res, 200, {
      status: 'ok',
      tool: 'scriptit',
      port: PORT,
      writerLoaded: !!generateScript,
      narratorLoaded: !!buildNarratorProfile,
      researcherLoaded: !!gatherResearch,
      structureClonerLoaded: !!extractStructureFromVideo,
      writerPath: WRITER,
      hasApiKey: !!(process.env.OPENROUTER_API_KEY || process.env.SCRIPTIT_OPENROUTER_API_KEY)
    });
  }
  if (req.method === 'GET' && url.pathname === '/api/models') {
    return sendJson(res, 200, { ok: true, models: ['qwen/qwen3-235b-a22b-2507', 'qwen/qwen3-32b', 'nvidia/nemotron-3-super-120b-a12b', 'meta-llama/llama-4-scout', 'qwen/qwen3-8b'] });
  }
  if (req.method === 'POST' && url.pathname === '/api/script/generate') {
    try {
      const body = await readBody(req);
      const result = await handleGenerate(body);
      return sendJson(res, 200, { ok: true, ...result });
    } catch (err) {
      return sendJson(res, 200, { ok: false, error: err.message });
    }
  }
  if (req.method === 'POST' && url.pathname === '/api/research') {
    try {
      const body = await readBody(req);
      const result = await handleResearch(body);
      return sendJson(res, 200, result);
    } catch (err) {
      return sendJson(res, 200, { ok: false, error: err.message });
    }
  }
  if (req.method === 'POST' && url.pathname === '/api/narrator/profile') {
    try {
      const body = await readBody(req);
      const result = await handleNarratorProfile(body);
      return sendJson(res, 200, { ok: true, ...result });
    } catch (err) {
      return sendJson(res, 200, { ok: false, error: err.message });
    }
  }
  if (req.method === 'POST' && url.pathname === '/api/structure/extract-yt') {
    try {
      const body = await readBody(req);
      if (!extractStructureFromVideo) throw new Error('ScriptIT structure cloner could not be loaded');
      const apiKey = String(body.apiKey || '').trim() || process.env.OPENROUTER_API_KEY || process.env.SCRIPTIT_OPENROUTER_API_KEY || '';
      const result = await extractStructureFromVideo({
        videoUrl: String(body.videoUrl || '').trim(),
        transcript: String(body.transcript || ''),
        apiKey,
        model: String(body.model || '').trim() || undefined,
        useCache: body.useCache !== false
      });
      return sendJson(res, 200, result);
    } catch (err) {
      return sendJson(res, 200, { ok: false, error: err.message });
    }
  }
  if (req.method === 'POST' && url.pathname === '/api/generate-from-yt') {
    try {
      const body = await readBody(req);
      if (!generateScriptFromStructure) throw new Error('ScriptIT structure cloner could not be loaded');
      const apiKey = String(body.apiKey || '').trim() || process.env.OPENROUTER_API_KEY || process.env.SCRIPTIT_OPENROUTER_API_KEY || '';
      const result = await generateScriptFromStructure({
        referenceStructure: body.referenceStructure,
        newTitle: body.newTitle || body.topic,
        duration: body.duration,
        voice: body.voice,
        evidence: body.evidence,
        apiKey,
        model: body.model
      });
      return sendJson(res, 200, { ok: true, ...result });
    } catch (err) {
      return sendJson(res, 200, { ok: false, error: err.message });
    }
  }
  sendJson(res, 404, { error: 'Not found: ' + url.pathname });
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  route(req, res, url).catch(err => sendJson(res, 500, { error: err.message }));
});

// This bridge carries provider credentials and research payloads. Bind to the
// loopback interface by default so a LAN device cannot call it accidentally.
if (require.main === module) {
server.listen(PORT, HOST, () => {
  console.log('');
  console.log('  📝  ScriptIT Bridge running at  http://' + HOST + ':' + PORT);
  console.log('      Writer loaded: ' + !!generateScript);
  console.log('      API key set:   ' + !!(process.env.OPENROUTER_API_KEY || process.env.SCRIPTIT_OPENROUTER_API_KEY));
  console.log('');
});

process.on('SIGTERM', () => server.close(() => process.exit(0)));
process.on('SIGINT', () => server.close(() => process.exit(0)));
}

module.exports = { handleGenerate };
