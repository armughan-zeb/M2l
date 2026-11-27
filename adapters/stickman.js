'use strict';

/**
 * 2D Stickman Cartoon Studio adapter for the Vidzeo Muse worker.
 *
 * Strictly isolated from Vidnets Documentary rendering: only handles jobs
 * whose resolved video style is `2d_stickman` (`engine === 'stickman'`).
 * All LLM planning (`stickman_script_json`, `stickman_shots_plan`) and QA
 * repairs (`stickman_shots_repair`) go through compulsory Muse inference
 * (bridge inbox / resident spool / HTTP inference endpoint) — never OpenRouter.
 */

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const log = require('../lib/log');
const { resolvePython } = require('../lib/python');
const { runCommand } = require('../lib/command');
const { writeJsonAtomic } = require('../lib/config');
const { assertNoOpenRouterKey, bridgeRoot } = require('../lib/museInference');
const { resolveVideoStyle, ENGINES, defaultVoiceForStyle } = require('../lib/videoStyle');
const { stampStickmanManifest } = require('../lib/masterManifestGuard');
const museInbox = require('../lib/museInbox');
const { sleep } = require('../lib/sleep');

const MAX_WALL_MS = 3600000;
const BRIDGE_POLL_MS = 250;
const REQUEST_ID_RE = /^[a-f0-9]{32}$/;

const SCRIPT_JSON_SCHEMA = {
  type: 'json_schema',
  json_schema: {
    name: 'stickman_script_json',
    schema: {
      type: 'object',
      additionalProperties: false,
      required: ['title', 'profile', 'format', 'voice', 'speed', 'captions', 'sound', 'lines'],
      properties: {
        title: { type: 'string' },
        output: { type: 'string' },
        profile: { type: 'string', enum: ['wide', 'long', 'short', 'reel'] },
        format: { type: 'string', enum: ['wide', 'vertical'] },
        voice: { type: 'string' },
        speed: { type: 'number' },
        captions: { type: 'string', enum: ['phrase', 'full', 'off'] },
        sound: {
          type: 'object',
          additionalProperties: false,
          required: ['mode', 'bed', 'transitions'],
          properties: {
            mode: { type: 'string', enum: ['auto', 'off'] },
            bed: { type: 'string', enum: ['drone', 'jungle', 'tense', 'wind'] },
            transitions: { type: 'boolean' },
          },
        },
        lines: {
          type: 'array',
          minItems: 1,
          items: {
            type: 'object',
            additionalProperties: false,
            required: ['text', 'say', 'caps'],
            properties: {
              text: { type: 'string' },
              say: { type: 'string' },
              caps: { type: 'string' },
            },
          },
        },
      },
    },
  },
};

const SHOTS_PLAN_SCHEMA = {
  type: 'json_schema',
  json_schema: {
    name: 'stickman_shots_plan',
    schema: {
      type: 'object',
      additionalProperties: false,
      required: ['summary', 'shotsJs'],
      properties: {
        summary: { type: 'string' },
        shotsJs: { type: 'string' },
      },
    },
  },
};

const SHOTS_REPAIR_SCHEMA = {
  type: 'json_schema',
  json_schema: {
    name: 'stickman_shots_repair',
    schema: {
      type: 'object',
      additionalProperties: false,
      required: ['shotsJs', 'notes'],
      properties: {
        shotsJs: { type: 'string' },
        notes: { type: 'string' },
      },
    },
  },
};

function stickmanRoot(env = process.env) {
  const explicit = String(env.MUSE_STICKMAN_ROOT || '').trim();
  if (explicit) return explicit;
  return path.join(__dirname, '..', '2d-stickman');
}

function stickmanRuntimeStatus(env = process.env) {
  const root = stickmanRoot(env);
  const required = [
    path.join(root, 'package.json'),
    path.join(root, 'index.html'),
    path.join(root, 'src', 'main.js'),
    path.join(root, 'src', 'stickman.js'),
    path.join(root, 'src', 'environments.js'),
    path.join(root, 'src', 'core', 'lib.js'),
    path.join(root, 'src', 'core', 'timing.js'),
    path.join(root, 'src', 'core', 'profiles.js'),
    path.join(root, 'src', 'core', 'sound.js'),
    path.join(root, 'tools', 'capture.mjs'),
    path.join(root, 'tools', 'lint.mjs'),
    path.join(root, 'tools', 'plan_captions.mjs'),
    path.join(root, 'tools', 'voice_edge.py'),
    path.join(root, 'tools', 'mix_audio.py'),
    path.join(root, 'sounds', 'whoosh.wav'),
    path.join(root, 'fonts', 'Inter-Black.otf'),
  ];
  const missing = required.filter((file) => !fs.existsSync(file));
  return {
    available: missing.length === 0,
    root,
    missing,
  };
}

function cleanGeneratedCode(code) {
  const text = String(code || '').trim();
  const match = text.match(/^```(?:javascript|js|mjs|json)?\s*\r?\n([\s\S]*?)\r?\n```\s*$/);
  return match ? match[1].trim() : text;
}

function parseJsonAnswer(rawContent, operation) {
  let content = rawContent;
  if (content && typeof content === 'object') return content;
  let text = String(content || '').trim();
  const fenced = text.match(/^```(?:json)?\s*\r?\n([\s\S]*?)\r?\n```\s*$/);
  if (fenced) text = fenced[1].trim();
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error(`Muse ${operation} returned invalid JSON: ${error.message}`);
  }
}

function buildScriptJsonMessages({ title, scriptText, voice, profile, format, captions }) {
  const sys = [
    'You convert spoken narration into `script.json` for 2D Stickman Cartoon Studio.',
    'Rules:',
    '1. Split the narration faithfully into punchy sentences (`lines` array), ~8-15 words per line, preserving EVERY fact cleanly without adding filler.',
    '2. In `say`, spell out years/numbers/acronyms clearly for Edge TTS.',
    '3. In `caps`, split each line into 2-5 short phrases separated by ` | `, marking 1-2 important keywords with a leading `*` (e.g. "Rule one: | *never run, | it triggers *pursuit.").',
    `4. Use exact fields: profile="${profile}", format="${format}", voice="${voice}", captions="${captions}", speed=1.0.`,
    '5. Choose the most fitting ambience `sound.bed` from `drone` | `jungle` | `tense` | `wind`, with `mode: "auto"`, `transitions: true`.',
    '6. Output valid JSON matching the schema ONLY.',
  ].join('\n');

  return [
    { role: 'system', content: sys },
    { role: 'user', content: `TITLE: ${title}\n\nNARRATION:\n${scriptText}` },
  ];
}

function buildShotsMessages({ title, scriptJson, linesData, profileCfg }) {
  const dur = Number(linesData.duration) || 30;
  const sys = [
    'You are the director and HTML5 Canvas 2D choreographer for 2D Stickman Cartoon Studio (`src/shots.js`).',
    'Write a complete, zero-error ES module `src/shots.js` exporting `export function buildShots()` that returns `{ S, CUES }`.',
    '',
    'EXACT IMPORTS AVAILABLE FROM `src/shots.js` (`import ... from "./..."`):',
    '- `import { ctx, W, H, TAU, clamp, lerp, eo, eio, eob, seg, rnd, rr, circle, ell, line, fillAll, bigText, C } from "./core/lib.js";`',
    '- `import { L, Le, B, Wd, lineWords } from "./core/timing.js";`',
    '- `import { stickman, drawBackwardHeadMask, soundShockwaves, cartoonTiger, cartoonTigerLunge, cartoonTigerFace, cartoonTigerStrikeDiagram } from "./stickman.js";`',
    '- `import { tropicalIslandBg, drawPalmHut, cartoonJungleBg, cartoonBambooAmbushBg, cartoonMangroveRiverBg } from "./environments.js";`',
    '',
    'CRITICAL QA & VISUAL RULES (enforced by `tools/capture.mjs check` and `lint`):',
    `1. Total audio duration = ${dur.toFixed(2)}s across ${linesData.lines.length} lines (indices 0..${linesData.lines.length - 1}). Profile="${profileCfg.name}" (${profileCfg.format}), target cut every ${profileCfg.sceneTarget[0]}-${profileCfg.sceneTarget[1]}s (HARD max ${profileCfg.sceneMax}s per scene).`,
    '2. Tag EVERY line index `0..N-1` with at least one shot (`line: i`). Split long lines (> 5s) with `B(i, frac)` into multiple shots so NO shot exceeds max scene duration.',
    '3. Shot 0 MUST have `at: 0`. Shots MUST be in strictly increasing `at` order with `id`, `line`, `motion` (one of: `draw`, `sweep`, `impact`, `zoom`, `rise`, `glow`, `stamp`), `note`, and `draw(lt, d, t)`.',
    '4. Always draw a full-bleed sky/background (using `environments.js` helpers OR custom full-bleed `ctx` cartoon art matching the bright cel-shaded aesthetic with thick `#121216` outlines) BEFORE drawing `stickman({ x, y, s, dir, kind: "white"|"black"|"casual", shirtCol, shortsCol, pose: "stand"|"sit"|"kneel", mood: "flat"|"happy"|"squint"|"shocked"|"determined"|"angry"|"smug"|"worried"|"blink", mouth: "smile"|"bigsmile"|"open"|"frown", walk, lookX, armF, elbowF, armB, elbowB, t })`.',
    '5. Animate continuously using `lt`, `d`, `t`, `eo(seg(...))`, `eob(...)` so nothing is frozen.',
    '6. Push matching cues to `CUES.push({ name, at })` using ONLY valid sound names (`pop`, `whoosh`, `stamp`, `boom`, `riser`, `tick`, `ding`, `click`, `swish`, `rumble`) within `[0, dur]`.',
    '7. Output strictly valid JSON matching the schema (`shotsJs` must contain clean raw JS module code, no Node imports).',
  ].join('\n');

  const linesSummary = linesData.lines
    .map((ln) => `L${ln.i} [${ln.start.toFixed(2)}s -> ${ln.end.toFixed(2)}s, dur=${(ln.end - ln.start).toFixed(2)}s]: ${ln.text}`)
    .join('\n');

  return [
    { role: 'system', content: sys },
    {
      role: 'user',
      content: `TITLE: ${title}\nDURATION: ${dur.toFixed(2)}s\nPROFILE: ${scriptJson.profile} (${scriptJson.format})\nBED: ${scriptJson.sound?.bed || 'drone'}\n\nMEASURED NARRATION LINES:\n${linesSummary}`,
    },
  ];
}

function buildShotsRepairMessages({ shotsJs, errorText, duration }) {
  return [
    {
      role: 'system',
      content: [
        'You are fixing `src/shots.js` for 2D Stickman Cartoon Studio after `capture.mjs check` or `lint` failed.',
        `Audio duration is ${duration.toFixed(2)}s. Fix all listed errors and return valid JSON { shotsJs, notes }.`,
      ].join('\n'),
    },
    {
      role: 'user',
      content: `CHECK / LINT ERRORS:\n${errorText}\n\nCURRENT src/shots.js:\n${shotsJs}`,
    },
  ];
}

function runProcess(binary, args, { cwd, env, timeoutMs = 15 * 60 * 1000, signal, label = 'stickman-step', onStdoutLine, onStderrLine } = {}) {
  if (signal?.aborted) return Promise.reject(new Error(`${label} was cancelled`));
  return new Promise((resolve, reject) => {
    let settled = false;
    let stdout = '';
    let stderr = '';
    let child;
    const finish = (err, result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener('abort', onAbort);
      if (err) reject(err);
      else resolve(result);
    };
    const onAbort = () => {
      if (child && !child.killed) {
        try { child.kill('SIGTERM'); } catch { /* ignore */ }
      }
      finish(new Error(`${label} was cancelled`));
    };
    try {
      child = spawn(binary, args, { cwd, env: { ...process.env, ...env }, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    } catch (error) {
      reject(error);
      return;
    }
    const timer = setTimeout(() => {
      try { child.kill('SIGKILL'); } catch { /* ignore */ }
      finish(new Error(`${label} timed out after ${Math.round(timeoutMs / 1000)}s`));
    }, Math.max(1000, Number(timeoutMs) || 600000));
    signal?.addEventListener('abort', onAbort, { once: true });

    let outBuf = '';
    let errBuf = '';
    child.stdout.on('data', (chunk) => {
      const text = chunk.toString('utf8');
      stdout = (stdout + text).slice(-2 * 1024 * 1024);
      if (onStdoutLine) {
        outBuf += text;
        const lines = outBuf.split(/[\r\n]+/);
        outBuf = lines.pop();
        for (const line of lines) if (line.trim()) onStdoutLine(line.trim());
      }
    });
    child.stderr.on('data', (chunk) => {
      const text = chunk.toString('utf8');
      stderr = (stderr + text).slice(-2 * 1024 * 1024);
      if (onStderrLine) {
        errBuf += text;
        const lines = errBuf.split(/[\r\n]+/);
        errBuf = lines.pop();
        for (const line of lines) if (line.trim()) onStderrLine(line.trim());
      }
    });
    child.on('error', (error) => finish(error));
    child.on('close', (code) => {
      if (outBuf.trim() && onStdoutLine) onStdoutLine(outBuf.trim());
      if (errBuf.trim() && onStderrLine) onStderrLine(errBuf.trim());
      if (code !== 0) {
        finish(new Error(`${label} exited with code ${code}: ${String(stderr || stdout).trim().slice(-800)}`));
      } else {
        finish(null, { code, stdout, stderr });
      }
    });
  });
}

async function postBridgeInference({ request, timeoutMs, signal }) {
  const root = bridgeRoot();
  fs.mkdirSync(root, { recursive: true });
  const id = REQUEST_ID_RE.test(String(request.id || '')) ? request.id : crypto.randomBytes(16).toString('hex');
  const dir = path.join(root, id);
  const requestPath = path.join(dir, 'request.json');
  const replyPath = path.join(dir, 'response.json');
  const startedAt = Date.now();
  const wallMs = Math.max(5000, Math.min(MAX_WALL_MS, Number(timeoutMs) || 15 * 60 * 1000));
  const row = {
    ...request,
    id,
    state: 'pending',
    created_at: startedAt / 1000,
    expires_at: (startedAt + wallMs) / 1000,
  };
  writeJsonAtomic(requestPath, row, { mode: 0o600 });
  const deadline = startedAt + wallMs;
  try {
    while (true) {
      if (signal?.aborted) throw new Error('Muse 2D Stickman inference cancelled');
      if (fs.existsSync(replyPath)) {
        let reply = null;
        try { reply = JSON.parse(fs.readFileSync(replyPath, 'utf8')); } catch { reply = null; }
        if (reply) {
          if (reply.error) {
            throw new Error(`Muse ${row.operation} request ${id} failed: ${String(reply.error).slice(0, 300)}`);
          }
          const content = reply.content ?? reply.text ?? reply.answer;
          if (content === undefined || content === null || String(content).trim() === '') {
            throw new Error(`Muse ${row.operation} request ${id} returned an empty response`);
          }
          return { id, content };
        }
      }
      if (Date.now() >= deadline) {
        throw new Error(`Muse ${row.operation} request ${id} timed out after ${Math.round(wallMs / 1000)}s`);
      }
      await sleep(Math.min(BRIDGE_POLL_MS, Math.max(10, deadline - Date.now())), signal);
    }
  } finally {
    if (!fs.existsSync(replyPath)) {
      try {
        const cur = JSON.parse(fs.readFileSync(requestPath, 'utf8'));
        if (cur && cur.state === 'pending') writeJsonAtomic(requestPath, { ...cur, state: 'expired' }, { mode: 0o600 });
      } catch { /* ignore */ }
    }
  }
}

async function inferWithMuse({ operation, kind = 'text', messages, responseFormat, jobId = '', workDir, timeoutMs = 15 * 60 * 1000, signal, inferFn }) {
  assertNoOpenRouterKey();
  const id = crypto.randomBytes(16).toString('hex');
  const nowSec = Date.now() / 1000;
  const wallSec = Math.max(30, Math.ceil((Number(timeoutMs) || 900000) / 1000));
  const request = {
    id,
    owner_id: String(process.env.MUSE_INBOX_OWNER || 'local-user').trim(),
    job_id: String(jobId || ''),
    attempt_id: '',
    kind,
    operation,
    messages,
    response_format: responseFormat,
    created_at: nowSec,
    expires_at: nowSec + wallSec,
    instructions: 'Compose a schema-valid JSON response using your own reasoning. Never call OpenRouter.',
  };

  if (typeof inferFn === 'function') {
    const raw = await inferFn(request, { timeoutMs, signal });
    return { id, data: parseJsonAnswer(raw?.content ?? raw, operation) };
  }

  const url = String(process.env.MUSE_INFERENCE_URL || '').trim();
  if (url) {
    const timeout = AbortSignal.timeout(timeoutMs);
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ type: 'inference', request, instructions: request.instructions, responseFormat }),
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
      redirect: 'error',
    });
    if (!res.ok) throw new Error(`Muse inference endpoint returned HTTP ${res.status} for ${operation}`);
    const payload = await res.json();
    if (payload?.error) throw new Error(`Muse inference endpoint failed ${operation}: ${payload.error}`);
    return { id, data: parseJsonAnswer(payload?.content ?? payload?.text ?? payload, operation) };
  }

  const command = String(process.env.MUSE_INFERENCE_COMMAND || '').trim();
  if (command && !museInbox.isResidentInboxRunning()) {
    try {
      const result = await runCommand(command, {
        input: JSON.stringify({ type: 'inference', request, instructions: request.instructions, responseFormat }),
        cwd: workDir,
        timeoutMs,
        signal,
        label: `Muse ${operation} command`,
        env: {
          MUSE_INFERENCE_REQUEST_ID: id,
          MUSE_INFERENCE_KIND: kind,
          MUSE_INFERENCE_OPERATION: operation,
        },
      });
      return { id, data: parseJsonAnswer(result.stdout, operation) };
    } catch (error) {
      if (!String(error?.message || '').includes('MUSE_TRANSPORT_UNAVAILABLE')) {
        throw error;
      }
      log.warn(`Resident inference transport unavailable for ${operation}; falling back to bridge inbox`);
    }
  }

  const reply = await postBridgeInference({ request, timeoutMs, signal });
  return { id, data: parseJsonAnswer(reply.content, operation) };
}

function copyStickmanTemplate(srcRoot, destDir) {
  fs.mkdirSync(destDir, { recursive: true });
  for (const file of ['index.html', 'package.json']) {
    fs.copyFileSync(path.join(srcRoot, file), path.join(destDir, file));
  }
  for (const sub of ['src', 'tools', 'fonts', 'sounds']) {
    fs.cpSync(path.join(srcRoot, sub), path.join(destDir, sub), {
      recursive: true,
      filter: (src) => !src.includes(`${path.sep}node_modules`) && !src.includes(`${path.sep}__pycache__`),
    });
  }
  fs.mkdirSync(path.join(destDir, 'data'), { recursive: true });
  fs.mkdirSync(path.join(destDir, 'renders'), { recursive: true });
}

async function runQaChecks(projectDir, signal) {
  await runProcess(nodeExecutable(), ['tools/capture.mjs', 'check'], {
    cwd: projectDir,
    signal,
    label: '2D Stickman browser check',
  });
  await runProcess(nodeExecutable(), ['tools/capture.mjs', 'lint'], {
    cwd: projectDir,
    signal,
    label: '2D Stickman lint gate',
  });
}

async function render(job, scriptText, workDir, options = {}) {
  const resolved = resolveVideoStyle(job);
  if (resolved.engine !== ENGINES.STICKMAN) {
    throw new Error(
      `Style isolation violation: 2D Stickman adapter refused job with resolved engine=${resolved.engine} (${resolved.style})`
    );
  }
  assertNoOpenRouterKey();

  const narration = String(scriptText || '').trim();
  if (!narration) throw new Error('2D Stickman render requires non-empty narration scriptText');

  const runtime = stickmanRuntimeStatus();
  if (!runtime.available) {
    throw new Error(`2D Stickman runtime is missing required files under ${runtime.root}: ${runtime.missing.join(', ')}`);
  }

  const onProgress = typeof options.onProgress === 'function' ? options.onProgress : () => {};
  const signal = options.signal;
  const pythonBin = options.pythonBin || await resolvePython();
  const aspect = String(
    job?.stickmanOptions?.aspect || job?.videoOptions?.aspect || job?.channel?.videoOptions?.aspect || '16:9'
  ).trim();
  const isVertical = aspect === '9:16';
  const format = isVertical ? 'vertical' : 'wide';
  const targetMinutes = Math.max(1, Number(job?.targetDurationMinutes) || 1);
  const profile = isVertical ? 'reel' : (targetMinutes >= 3 ? 'long' : 'wide');
  const captions = String(job?.stickmanOptions?.captions || 'phrase');
  const voice = defaultVoiceForStyle(job);

  const projectDir = path.join(workDir, 'stickman-project');
  const inputDigest = crypto.createHash('sha256').update(JSON.stringify({
    title: job.title || '',
    narration,
    voice,
    profile,
    format,
    captions,
    engine: ENGINES.STICKMAN,
  })).digest('hex');

  const markerPath = path.join(projectDir, 'data', 'stickman-input.json');
  let prevMarker = null;
  try { prevMarker = JSON.parse(fs.readFileSync(markerPath, 'utf8')); } catch { /* ignore */ }

  if (!fs.existsSync(path.join(projectDir, 'index.html'))) {
    copyStickmanTemplate(runtime.root, projectDir);
  } else if (prevMarker && prevMarker.digest !== inputDigest) {
    for (const stale of ['script.json', path.join('data', 'mix.wav'), path.join('data', 'mixed.wav'), path.join('data', 'lines.json'), path.join('data', 'words.json'), path.join('data', 'cues.json'), path.join('renders', 'video.mp4')]) {
      try { fs.rmSync(path.join(projectDir, stale), { force: true }); } catch { /* ignore */ }
    }
  }
  writeJsonAtomic(markerPath, { digest: inputDigest, voice, profile, format, jobId: job.jobId, updatedAt: new Date().toISOString() });

  const inferRequests = [];
  const scriptJsonPath = path.join(projectDir, 'script.json');
  let scriptJson = null;
  if (fs.existsSync(scriptJsonPath) && prevMarker?.digest === inputDigest) {
    try { scriptJson = JSON.parse(fs.readFileSync(scriptJsonPath, 'utf8')); } catch { scriptJson = null; }
  }
  if (!scriptJson || !Array.isArray(scriptJson.lines) || !scriptJson.lines.length) {
    onProgress(8, '[2d-stickman] Structuring narration into script.json via Muse', true);
    const reply = await inferWithMuse({
      operation: 'stickman_script_json',
      kind: 'text',
      messages: buildScriptJsonMessages({ title: job.title || '2D Stickman Story', scriptText: narration, voice, profile, format, captions }),
      responseFormat: SCRIPT_JSON_SCHEMA,
      jobId: job.jobId,
      timeoutMs: Number(process.env.MUSE_STICKMAN_PLAN_TIMEOUT_MS) || 15 * 60 * 1000,
      signal,
      workDir,
      inferFn: options.inferFn,
    });
    inferRequests.push({ id: reply.id, operation: 'stickman_script_json' });
    scriptJson = { ...reply.data, output: 'video', voice, profile, format, captions };
    writeJsonAtomic(scriptJsonPath, scriptJson);
  }

  const mixWavPath = path.join(projectDir, 'data', 'mix.wav');
  const linesJsonPath = path.join(projectDir, 'data', 'lines.json');
  if (!fs.existsSync(mixWavPath) || !fs.existsSync(linesJsonPath)) {
    onProgress(16, `[2d-stickman] Synthesizing Edge TTS narration (${voice})`, true);
    await runProcess(pythonBin, [path.join('tools', 'voice_edge.py'), '--force'], { cwd: projectDir, signal, label: '2D Stickman TTS' });
  }
  await runProcess(nodeExecutable(), [path.join('tools', 'plan_captions.mjs')], { cwd: projectDir, signal, label: '2D Stickman captions plan' });

  const linesData = JSON.parse(fs.readFileSync(linesJsonPath, 'utf8'));
  const durationSeconds = Number(linesData.duration) || 0;
  const profileCfg = {
    name: profile,
    format,
    sceneTarget: profile === 'long' ? [3, 8] : profile === 'wide' ? [2, 5] : [1.5, 4.5],
    sceneMax: profile === 'long' ? 12 : 7,
  };

  const shotsPath = path.join(projectDir, 'src', 'shots.js');
  const shotsPlanFile = path.join(projectDir, 'data', 'shots-plan.json');
  let shotsJs = '';
  if (fs.existsSync(shotsPlanFile) && prevMarker?.digest === inputDigest) {
    try {
      const savedPlan = JSON.parse(fs.readFileSync(shotsPlanFile, 'utf8'));
      if (savedPlan?.shotsJs) shotsJs = cleanGeneratedCode(savedPlan.shotsJs);
    } catch { /* ignore */ }
  }
  if (!shotsJs) {
    onProgress(26, `[2d-stickman] Choreographing scenes via Muse (${linesData.lines.length} lines, ${durationSeconds.toFixed(1)}s)`, true);
    const reply = await inferWithMuse({
      operation: 'stickman_shots_plan',
      kind: 'text',
      messages: buildShotsMessages({ title: job.title || '2D Stickman Story', scriptJson, linesData, profileCfg }),
      responseFormat: SHOTS_PLAN_SCHEMA,
      jobId: job.jobId,
      timeoutMs: Number(process.env.MUSE_STICKMAN_PLAN_TIMEOUT_MS) || 15 * 60 * 1000,
      signal,
      workDir,
      inferFn: options.inferFn,
    });
    inferRequests.push({ id: reply.id, operation: 'stickman_shots_plan' });
    shotsJs = cleanGeneratedCode(reply.data?.shotsJs);
    if (!shotsJs) throw new Error('Muse stickman_shots_plan returned empty shotsJs');
    writeJsonAtomic(shotsPlanFile, { shotsJs, summary: reply.data?.summary || '', inferRequests });
  }
  fs.writeFileSync(shotsPath, shotsJs, 'utf8');

  onProgress(36, '[2d-stickman] Running Canvas scene check and QA lint gate', true);
  try {
    await runQaChecks(projectDir, signal);
  } catch (qaErr) {
    log.warn(`2D Stickman QA check/lint failed; requesting bounded Muse repair: ${qaErr.message}`);
    onProgress(40, '[2d-stickman] Repairing src/shots.js via Muse after QA failure', true);
    const repair = await inferWithMuse({
      operation: 'stickman_shots_repair',
      kind: 'text',
      messages: buildShotsRepairMessages({ shotsJs, errorText: qaErr.message, duration: durationSeconds }),
      responseFormat: SHOTS_REPAIR_SCHEMA,
      jobId: job.jobId,
      timeoutMs: 10 * 60 * 1000,
      signal,
      workDir,
      inferFn: options.inferFn,
    });
    inferRequests.push({ id: repair.id, operation: 'stickman_shots_repair' });
    shotsJs = cleanGeneratedCode(repair.data?.shotsJs);
    if (!shotsJs) throw new Error('Muse stickman_shots_repair returned empty shotsJs');
    fs.writeFileSync(shotsPath, shotsJs, 'utf8');
    writeJsonAtomic(shotsPlanFile, { shotsJs, repaired: true, inferRequests });
    await runQaChecks(projectDir, signal);
  }

  onProgress(45, '[2d-stickman] Generating SFX cues and mixing audio bed', true);
  await runProcess(nodeExecutable(), ['tools/capture.mjs', 'cues'], { cwd: projectDir, signal, label: '2D Stickman cues export' });
  await runProcess(pythonBin, [path.join('tools', 'mix_audio.py')], { cwd: projectDir, signal, label: '2D Stickman audio mix' });

  onProgress(52, '[2d-stickman] Rendering 1080p Canvas frames through Chrome CDP', true);
  const rawVideo = path.join(projectDir, 'renders', 'video.mp4');
  await runProcess(nodeExecutable(), ['tools/capture.mjs', 'video', path.join('renders', 'video.mp4')], {
    cwd: projectDir,
    timeoutMs: Math.max(30 * 60 * 1000, Math.round(durationSeconds * 8000)),
    signal,
    label: '2D Stickman video render',
    onStdoutLine: (line) => {
      const m = line.match(/^(\d+)\/(\d+)\s+frames/);
      if (m) {
        const done = Number(m[1]);
        const total = Math.max(1, Number(m[2]));
        const pct = 52 + Math.min(42, Math.round((done / total) * 42));
        onProgress(pct, `[2d-stickman] Rendered ${done}/${total} frames`, true);
      }
    },
  });

  if (!fs.existsSync(rawVideo)) {
    throw new Error('2D Stickman capture finished without producing renders/video.mp4');
  }
  const finalVideoPath = path.join(workDir, 'video.mp4');
  fs.copyFileSync(rawVideo, finalVideoPath);

  const cuesData = (() => { try { return JSON.parse(fs.readFileSync(path.join(projectDir, 'data', 'cues.json'), 'utf8')); } catch { return {}; } })();
  const manifestPath = stampStickmanManifest(finalVideoPath, {
    jobId: job.jobId,
    profile,
    format,
    voice,
    soundBed: String(scriptJson.sound?.bed || ''),
    durationSeconds,
    lineCount: linesData.lines.length,
    cueCount: Array.isArray(cuesData.cues) ? cuesData.cues.length : 0,
    inferRequests,
  });

  return {
    videoPath: finalVideoPath,
    thumbnailPath: '',
    meta: {
      engine: ENGINES.STICKMAN,
      videoStyle: resolved.style,
      profile,
      format,
      voice,
      durationSeconds,
      lineCount: linesData.lines.length,
      stickmanManifestPath: manifestPath,
      inferRequests,
    },
  };
}

function nodeExecutable() {
  return process.execPath || 'node';
}

module.exports = {
  SCRIPT_JSON_SCHEMA,
  SHOTS_PLAN_SCHEMA,
  SHOTS_REPAIR_SCHEMA,
  stickmanRoot,
  stickmanRuntimeStatus,
  cleanGeneratedCode,
  buildScriptJsonMessages,
  buildShotsMessages,
  inferWithMuse,
  render,
};
