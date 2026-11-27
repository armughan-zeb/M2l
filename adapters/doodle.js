'use strict';

/**
 * Doodle Verse Animation adapter for the Vidzeo Muse worker.
 *
 * Strictly isolated from Vidnets Documentary rendering: only handles jobs
 * whose resolved video style is `doodle_verse` (`engine === 'doodle'`).
 * All LLM planning (`doodle_verse_plan`) and WebGL repair (`doodle_verse_repair`)
 * go through compulsory Muse inference (bridge inbox / resident spool / HTTP
 * inference endpoint) — never OpenRouter or paid API fallbacks.
 */

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const log = require('../lib/log');
const { resolvePython } = require('../lib/python');
const ffmpeg = require('../lib/ffmpeg');
const { runCommand } = require('../lib/command');
const { writeJsonAtomic } = require('../lib/config');
const { assertNoOpenRouterKey, bridgeRoot } = require('../lib/museInference');
const { resolveVideoStyle, ENGINES, defaultVoiceForStyle } = require('../lib/videoStyle');
const { stampDoodleManifest } = require('../lib/masterManifestGuard');
const museInbox = require('../lib/museInbox');
const { sleep } = require('../lib/sleep');

const MAX_WALL_MS = 3600000;
const BRIDGE_POLL_MS = 250;
const DEFAULT_FRAME_BATCH = 180;
const REQUEST_ID_RE = /^[a-f0-9]{32}$/;

const PLAN_SCHEMA = {
  type: 'json_schema',
  json_schema: {
    name: 'doodle_verse_plan',
    schema: {
      type: 'object',
      additionalProperties: false,
      required: ['environment', 'planSummary', 'customOutfits', 'storyJs', 'audioPy', 'thumbSpec'],
      properties: {
        environment: { type: 'string' },
        planSummary: { type: 'string' },
        customOutfits: { type: 'object' },
        storyJs: { type: 'string' },
        audioPy: { type: 'string' },
        thumbSpec: {
          type: 'object',
          additionalProperties: true,
          properties: {
            frame: { type: 'integer' },
            lines: { type: 'array', items: { type: 'string' } },
          },
        },
      },
    },
  },
};

const REPAIR_SCHEMA = {
  type: 'json_schema',
  json_schema: {
    name: 'doodle_verse_repair',
    schema: {
      type: 'object',
      additionalProperties: false,
      required: ['storyJs', 'notes'],
      properties: {
        storyJs: { type: 'string' },
        notes: { type: 'string' },
      },
    },
  },
};

function doodleRoot(env = process.env) {
  const explicit = String(env.MUSE_DOODLE_ROOT || '').trim();
  if (explicit) return explicit;
  return path.join(__dirname, '..', 'doodle-verse');
}

function resolvePlaywrightRoot(root = doodleRoot()) {
  const candidates = [
    path.join(root, 'node_modules'),
    path.join(root, 'kit', 'node_modules'),
    path.resolve(root, '..', '..', '..', '..', 'doodle-verse-animation', '.runtime', 'node_modules'),
    path.resolve(root, '..', '..', 'doodle-verse-animation', '.runtime', 'node_modules'),
  ];
  for (const dir of candidates) {
    try {
      if (fs.existsSync(path.join(dir, 'playwright', 'package.json'))) return dir;
    } catch { /* ignore */ }
  }
  return '';
}

function doodleRuntimeStatus(env = process.env) {
  const root = doodleRoot(env);
  const kitDir = path.join(root, 'kit');
  const toolsDir = path.join(root, 'tools');
  const required = [
    path.join(kitDir, 'package.json'),
    path.join(kitDir, 'render.mjs'),
    path.join(kitDir, 'web', 'index.html'),
    path.join(toolsDir, 'new_project.py'),
    path.join(toolsDir, 'tts.py'),
    path.join(toolsDir, 'mix.py'),
    path.join(toolsDir, 'srt.py'),
  ];
  const missing = required.filter((file) => !fs.existsSync(file));
  const playwrightModules = resolvePlaywrightRoot(root);
  return {
    available: missing.length === 0,
    root,
    kitDir,
    toolsDir,
    missing,
    playwrightInstalled: Boolean(playwrightModules),
    playwrightModules,
  };
}

function cleanGeneratedCode(code) {
  const text = String(code || '').trim();
  const match = text.match(/^```(?:javascript|js|python|py)?\s*\r?\n([\s\S]*?)\r?\n```\s*$/);
  return match ? match[1].trim() : text;
}

function groupWordBeats(words, totalDuration) {
  if (!Array.isArray(words) || !words.length) {
    return [{ startSec: 0, endSec: Math.round(totalDuration * 100) / 100, startFrame: 0, endFrame: Math.round(totalDuration * 30), text: '(Full narration)' }];
  }
  const beats = [];
  let bucket = [];
  let bucketStart = Number(words[0].s) || 0;
  for (const entry of words) {
    bucket.push(String(entry.w || '').trim());
    const end = Number(entry.e) || bucketStart;
    if ((end - bucketStart >= 4.0 && bucket.length >= 6) || bucket.length >= 16) {
      beats.push({
        startSec: Math.round(bucketStart * 100) / 100,
        endSec: Math.round(end * 100) / 100,
        startFrame: Math.max(0, Math.round(bucketStart * 30)),
        endFrame: Math.max(1, Math.round(end * 30)),
        text: bucket.join(' '),
      });
      bucket = [];
      bucketStart = end;
    }
  }
  if (bucket.length) {
    const end = Number(words[words.length - 1].e) || totalDuration;
    beats.push({
      startSec: Math.round(bucketStart * 100) / 100,
      endSec: Math.round(end * 100) / 100,
      startFrame: Math.max(0, Math.round(bucketStart * 30)),
      endFrame: Math.max(1, Math.round(end * 30)),
      text: bucket.join(' '),
    });
  }
  return beats;
}

function mergeCustomOutfitsIntoCast(castPath, customOutfits) {
  if (!customOutfits || typeof customOutfits !== 'object' || !Object.keys(customOutfits).length) return;
  let content = fs.readFileSync(castPath, 'utf8');
  const exportIdx = content.indexOf('export const OUT = {');
  if (exportIdx < 0) return;
  const openBraceIdx = content.indexOf('{', exportIdx);
  let depth = 0;
  let endIdx = -1;
  for (let i = openBraceIdx; i < content.length; i += 1) {
    if (content[i] === '{') depth += 1;
    else if (content[i] === '}') {
      depth -= 1;
      if (depth === 0) {
        endIdx = i;
        break;
      }
    }
  }
  if (endIdx < 0) return;

  const entries = [];
  for (const [id, spec] of Object.entries(customOutfits)) {
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(id)) continue;
    if (!spec || typeof spec !== 'object') continue;
    const jsonSpec = JSON.stringify(spec).replace(/\"SKINS\[(\d+)\]\"/g, 'SKINS[$1]').replace(/\"SKIN\"/g, 'SKIN');
    entries.push(`  ${id}: ${jsonSpec},`);
  }
  if (!entries.length) return;
  content = content.slice(0, endIdx) + '\n  // Muse job-specific outfits\n' + entries.join('\n') + '\n' + content.slice(endIdx);
  fs.writeFileSync(castPath, content, 'utf8');
}

function buildPlanMessages({ title, scriptText, durationSeconds, frameCount, beats, aspect = '16:9' }) {
  const sys = [
    'You are the master director and Three.js / Python audio choreographer for Doodle Verse Animation.',
    'Generate a complete, bespoke, bug-free ES module `web/story.js` and Python audio synthesizer `audio/audio.py` synchronized precisely to 30 fps frame boundaries.',
    '',
    'CRITICAL RULES:',
    `1. Target duration = ${durationSeconds.toFixed(2)}s, Total Frames N = ${frameCount}, Aspect Ratio = ${aspect}.`,
    '2. Set `export const ENVIRONMENT` explicitly to the exact physical setting of the script (`\'office\'` | `\'jungle\'` | `\'battlefield\'` | `\'tundra\'` | `\'lab\'` | `\'park\'`). Reusing park/bench for non-park stories is strictly forbidden.',
    '3. Set `export const N = ' + frameCount + ';` and export `SHOTS` covering 0..N contiguously with no gaps.',
    '4. Available exported helpers in story.js context:',
    '   - `actorsAt(f)` returns array of actor specs: `{ id, outfit, pos:[x,y,z], facing:-1..1, look:-1..1, view:\'front\'|\'back\'|\'side\', target:\'other_id\'|[x,y,z], expr:\'calm\'|\'smile\'|\'sad\'|\'worry\'|\'angry\'|\'surprised\'|\'focus\', L:\'hang\'|\'reach\'|\'open\'|\'typing\'|\'phone\'|\'mug\'|\'point\'|\'aim\'|\'salute\'|\'shiver\'|\'torch\'|\'binoculars\'|\'wave\'|\'hero\', R:..., prop:\'phone\'|\'mug\'|\'spear\'|\'torch\'|\'rifle\'|\'binoculars\'|\'clipboard\', scale:1, contactY:975, grounded:true }`',
    '   - `camAt(f)` returns `{ pos:[x,y,z], look:[x,y,z], fov, roll }`. Mix `camCloseup`, `camMedium`, `camWide`, and `camOTS` using helper formulas directly inside story.js (do not import missing functions). Ensure cameras never clip inside walls or obstacles.',
    '   - `lightFor(f)` returns `{ preset:\'day\'|\'golden\'|\'dusk\'|\'night\'|\'dawn\' }`, `flashAt(f)` returns 0..1, `dofAt(f)` returns `{ focus, cInf }` or null, `fxAt(f)` returns array of FX specs.',
    '   - You may export `initWorld(scene, env)` if custom static meshes help (`envCtx.tbox`, `envCtx.add`, `envCtx.toon`). Imports allowed in `story.js` ONLY from `./toonbase.js`, `./three.module.js`, `./paint.js`, `./env.js`. Never import Node built-ins or external URLs.',
    '5. In `customOutfits`, supply tailored archetype keys referenced in `story.js` using `{ jacket, pants, boots, hat, hair, hairCut, skin, tie, collar, glasses, pelt }` (valid CSS hex strings).',
    '6. In `audio.py`, read `voice48.wav` via `dsp.read_wav`, write BOTH `music.wav` and `sfx.wav` at exact length `N = V.shape[1]` using `dsp` + `instruments` (`I.pad`, `I.pluck`, `I.vibes`, `I.musicbox`, `I.glock`). Follow sample-exact Python syntax with zero syntax errors.',
    '7. Output MUST be strictly valid JSON matching the schema. Return raw JS/Python code inside `storyJs` and `audioPy`. Never call OpenRouter or invent fake facts.',
  ].join('\n');

  const user = [
    `TITLE: ${title}`,
    `DURATION_SECONDS: ${durationSeconds.toFixed(2)}`,
    `FRAME_COUNT_N: ${frameCount}`,
    `ASPECT_RATIO: ${aspect}`,
    '',
    'FULL SPOKEN SCRIPT:',
    scriptText,
    '',
    'TIMED WORD BEATS (30 fps):',
    beats.map((b) => `[${b.startFrame}-${b.endFrame} | ${b.startSec}s-${b.endSec}s] ${b.text}`).join('\n'),
  ].join('\n');

  return [
    { role: 'system', content: sys },
    { role: 'user', content: user },
  ];
}

function buildRepairMessages({ storyJs, errorText, frameCount }) {
  return [
    {
      role: 'system',
      content: [
        'You are debugging a Doodle Verse Three.js `web/story.js` module that failed browser WebGL verification (`probe.mjs`).',
        'Fix the syntax, runtime, reference, or framing issue and return the complete fixed ES module `storyJs`. Keep `N = ' + frameCount + '`. Return strict JSON.',
      ].join('\n'),
    },
    {
      role: 'user',
      content: `PROBE ERROR:\n${errorText}\n\nBROKEN web/story.js:\n${storyJs}`,
    },
  ];
}

function runProcess(binary, args, { cwd, env, timeoutMs = 15 * 60 * 1000, signal, label = 'doodle-step', onStdoutLine, onStderrLine } = {}) {
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
        const lines = outBuf.split('\n');
        outBuf = lines.pop();
        for (const line of lines) if (line.trim()) onStdoutLine(line.trim());
      }
    });
    child.stderr.on('data', (chunk) => {
      const text = chunk.toString('utf8');
      stderr = (stderr + text).slice(-2 * 1024 * 1024);
      if (onStderrLine) {
        errBuf += text;
        const lines = errBuf.split('\n');
        errBuf = lines.pop();
        for (const line of lines) if (line.trim()) onStderrLine(line.trim());
      }
    });
    child.on('error', (error) => finish(error));
    child.on('close', (code) => {
      if (outBuf.trim() && onStdoutLine) onStdoutLine(outBuf.trim());
      if (errBuf.trim() && onStderrLine) onStderrLine(errBuf.trim());
      if (code !== 0) {
        finish(new Error(`${label} exited with code ${code}: ${String(stderr || stdout).trim().slice(-700)}`));
      } else {
        finish(null, { code, stdout, stderr });
      }
    });
  });
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

async function postBridgeInference({ request, workDir, timeoutMs, signal }) {
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
      if (signal?.aborted) throw new Error('Muse Doodle inference cancelled');
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

  const reply = await postBridgeInference({ request, workDir, timeoutMs, signal });
  return { id, data: parseJsonAnswer(reply.content, operation) };
}

function linkOrPrepareNodeModules(projectDir, runtime) {
  const dest = path.join(projectDir, 'node_modules');
  if (fs.existsSync(path.join(dest, 'playwright', 'package.json'))) return;
  if (runtime.playwrightModules && fs.existsSync(runtime.playwrightModules)) {
    try {
      fs.symlinkSync(runtime.playwrightModules, dest, process.platform === 'win32' ? 'junction' : 'dir');
      return;
    } catch { /* fall through */ }
  }
  const pkgPath = path.join(projectDir, 'package.json');
  if (!fs.existsSync(pkgPath)) return;
  throw new Error(
    `Playwright runtime for Doodle Verse is not installed under ${runtime.root}. ` +
    'Run `npm ci` inside doodle-verse/ or re-run setup-linux.sh.'
  );
}

function probeDurationSeconds(ffprobePath, filePath) {
  return new Promise((resolve, reject) => {
    const child = spawn(ffprobePath, [
      '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', filePath
    ], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (c) => { stdout += c.toString('utf8'); });
    child.stderr.on('data', (c) => { stderr += c.toString('utf8'); });
    child.on('error', reject);
    child.on('close', (code) => {
      const value = Number.parseFloat(String(stdout || '').trim());
      if (code === 0 && Number.isFinite(value) && value > 0) resolve(value);
      else reject(new Error(`ffprobe could not read Doodle voice duration: ${stderr || stdout}`));
    });
  });
}

async function runWebProbe(projectDir, { timeoutMs = 120000, signal, useSwiftShader = false } = {}) {
  const expr = 'Boolean(window.ready && Array.isArray(window.SHOTS) && window.SHOTS.length > 0)';
  const res = await runProcess('node', ['work/probe.mjs', expr], {
    cwd: projectDir,
    env: useSwiftShader ? { SWIFTSHADER: '1' } : {},
    timeoutMs,
    signal,
    label: 'Doodle Verse WebGL probe',
  });
  const lines = String(res.stdout || '').trim().split(/\r?\n/).filter(Boolean);
  const last = lines[lines.length - 1] || '';
  if (lines.some((line) => line.startsWith('PAGEERR')) || last !== 'true') {
    throw new Error(`Probe check returned "${last}": ${res.stdout} ${res.stderr}`);
  }
}

async function render(job, scriptText, workDir, options = {}) {
  const resolved = resolveVideoStyle(job);
  if (resolved.engine !== ENGINES.DOODLE) {
    throw new Error(
      `Style isolation violation: Doodle Verse adapter refused job with resolved engine=${resolved.engine} (${resolved.style})`
    );
  }
  assertNoOpenRouterKey();

  const narration = String(scriptText || '').trim();
  if (!narration) throw new Error('Doodle Verse render requires non-empty narration scriptText');

  const runtime = doodleRuntimeStatus();
  if (!runtime.available) {
    throw new Error(`Doodle Verse kit is missing required files under ${runtime.root}: ${runtime.missing.join(', ')}`);
  }

  const onProgress = typeof options.onProgress === 'function' ? options.onProgress : () => {};
  const signal = options.signal;
  const pythonBin = options.pythonBin || await resolvePython();
  const [ffmpegBin, ffprobeBin] = await Promise.all([
    options.ffmpegBin || ffmpeg.resolveBinary('ffmpeg'),
    options.ffprobeBin || ffmpeg.resolveBinary('ffprobe'),
  ]);
  if (!ffmpegBin || !ffprobeBin) {
    throw new Error('Doodle Verse requires ffmpeg and ffprobe installed on the Muse worker');
  }

  const projectDir = path.join(workDir, 'doodle-project');
  const voice = defaultVoiceForStyle(job);
  const aspect = String(
    job?.videoOptions?.aspect || job?.doodleOptions?.aspect || job?.channel?.videoOptions?.aspect || '16:9'
  ).trim();
  const inputDigest = crypto.createHash('sha256').update(JSON.stringify({
    title: job.title || '',
    narration,
    voice,
    aspect,
    engine: ENGINES.DOODLE,
  })).digest('hex');

  const inputMarkerPath = path.join(projectDir, 'work', 'doodle-input.json');
  let previousInput = null;
  try { previousInput = JSON.parse(fs.readFileSync(inputMarkerPath, 'utf8')); } catch { /* none */ }

  if (!fs.existsSync(path.join(projectDir, 'render.mjs'))) {
    await runProcess(pythonBin, [path.join(runtime.toolsDir, 'new_project.py'), 'doodle-verse', projectDir], {
      cwd: workDir,
      signal,
      label: 'Doodle Verse scaffold',
    });
  } else if (previousInput && previousInput.digest !== inputDigest) {
    // Input narration or voice changed cleanly: invalidate stale frames and plan so nothing mixes.
    for (const stale of ['voice.mp3', path.join('work', 'words.json'), path.join('work', 'agent-plan.json'), path.join('out', 'video.mp4')]) {
      try { fs.rmSync(path.join(projectDir, stale), { force: true }); } catch { /* ignore */ }
    }
    try { fs.rmSync(path.join(projectDir, 'frames'), { recursive: true, force: true }); } catch { /* ignore */ }
    fs.mkdirSync(path.join(projectDir, 'frames'), { recursive: true });
  }

  linkOrPrepareNodeModules(projectDir, runtime);
  fs.writeFileSync(path.join(projectDir, 'script.txt'), narration, 'utf8');
  writeJsonAtomic(inputMarkerPath, { digest: inputDigest, voice, aspect, jobId: job.jobId, updatedAt: new Date().toISOString() });

  const voiceMp3 = path.join(projectDir, 'voice.mp3');
  const wordsJsonPath = path.join(projectDir, 'work', 'words.json');
  const voice48Wav = path.join(projectDir, 'audio', 'voice48.wav');

  if (!fs.existsSync(voiceMp3) || !fs.existsSync(wordsJsonPath)) {
    onProgress(8, `Synthesizing Doodle Verse neural narration (${voice})`, true);
    await runProcess(pythonBin, [
      path.join(projectDir, 'tools', 'tts.py'),
      path.join(projectDir, 'script.txt'),
      voiceMp3,
      '--voice', voice,
      '--words', wordsJsonPath,
    ], { cwd: projectDir, signal, label: 'Doodle Verse TTS' });
  }

  const durationSeconds = await probeDurationSeconds(ffprobeBin, voiceMp3);
  const frameCount = Math.max(30, Math.round(durationSeconds * 30));

  if (!fs.existsSync(voice48Wav)) {
    await runProcess(ffmpegBin, ['-y', '-i', voiceMp3, '-ar', '48000', '-ac', '2', voice48Wav], {
      cwd: projectDir,
      signal,
      label: 'Doodle Verse voice 48kHz convert',
    });
  }

  const words = JSON.parse(fs.readFileSync(wordsJsonPath, 'utf8'));
  const beats = groupWordBeats(words, durationSeconds);

  const planFile = path.join(projectDir, 'work', 'agent-plan.json');
  const inferRequests = [];
  let plan;
  if (fs.existsSync(planFile)) {
    try { plan = JSON.parse(fs.readFileSync(planFile, 'utf8')); } catch { plan = null; }
  }
  if (!plan || !plan.storyJs || !plan.audioPy) {
    onProgress(18, `Requesting Muse story choreography (${frameCount} frames, ${beats.length} beats)`, true);
    const messages = buildPlanMessages({ title: job.title || 'Animated Story', scriptText: narration, durationSeconds, frameCount, beats, aspect });
    const reply = await inferWithMuse({
      operation: 'doodle_verse_plan',
      kind: 'text',
      messages,
      responseFormat: PLAN_SCHEMA,
      jobId: job.jobId,
      timeoutMs: Number(process.env.MUSE_DOODLE_PLAN_TIMEOUT_MS) || 15 * 60 * 1000,
      signal,
      workDir,
      inferFn: options.inferFn,
    });
    inferRequests.push({ id: reply.id, operation: 'doodle_verse_plan' });
    plan = reply.data;
    writeJsonAtomic(planFile, { ...plan, inferRequests });
  } else if (Array.isArray(plan.inferRequests)) {
    inferRequests.push(...plan.inferRequests);
  }

  let storyJs = cleanGeneratedCode(plan.storyJs);
  const audioPy = cleanGeneratedCode(plan.audioPy);
  if (!storyJs || !audioPy) throw new Error('Muse doodle_verse_plan returned empty storyJs or audioPy');

  mergeCustomOutfitsIntoCast(path.join(projectDir, 'web', 'cast.js'), plan.customOutfits);
  fs.writeFileSync(path.join(projectDir, 'web', 'story.js'), storyJs, 'utf8');
  fs.writeFileSync(path.join(projectDir, 'audio', 'audio.py'), audioPy, 'utf8');

  onProgress(28, 'Probing WebGL scene in headless Chromium', true);
  let useSwiftShader = process.env.SWIFTSHADER === '1';
  try {
    await runWebProbe(projectDir, { signal, useSwiftShader });
  } catch (probeErr) {
    // If GPU context failed on headless host without explicit SWIFTSHADER flag, try SwiftShader once before asking Muse for script repair.
    if (!useSwiftShader && /webgl|gpu|browser|launch|target closed|context/i.test(String(probeErr.message || ''))) {
      try {
        await runWebProbe(projectDir, { signal, useSwiftShader: true });
        useSwiftShader = true;
      } catch (swiftErr) {
        probeErr = swiftErr;
      }
    }
    if (!useSwiftShader) {
      log.warn(`Doodle Verse WebGL probe failed; requesting bounded Muse repair: ${probeErr.message}`);
      onProgress(32, 'Requesting bounded WebGL scene repair from Muse', true);
      const repair = await inferWithMuse({
        operation: 'doodle_verse_repair',
        kind: 'text',
        messages: buildRepairMessages({ storyJs, errorText: probeErr.message, frameCount }),
        responseFormat: REPAIR_SCHEMA,
        jobId: job.jobId,
        timeoutMs: 10 * 60 * 1000,
        signal,
        workDir,
        inferFn: options.inferFn,
      });
      inferRequests.push({ id: repair.id, operation: 'doodle_verse_repair' });
      const repairedCode = cleanGeneratedCode(repair.data?.storyJs);
      if (!repairedCode) throw new Error('Muse doodle_verse_repair returned empty storyJs');
      storyJs = repairedCode;
      fs.writeFileSync(path.join(projectDir, 'web', 'story.js'), storyJs, 'utf8');
      writeJsonAtomic(planFile, { ...plan, storyJs, inferRequests });
      await runWebProbe(projectDir, { signal, useSwiftShader: process.env.SWIFTSHADER === '1' });
    }
  }

  onProgress(38, 'Synthesizing music & SFX audio stems', true);
  const audioDir = path.join(projectDir, 'audio');
  const mixWav = path.join(audioDir, 'mix.wav');
  if (!fs.existsSync(mixWav)) {
    await runProcess(pythonBin, ['audio.py'], { cwd: audioDir, signal, label: 'Doodle Verse music & SFX synthesis' });
    await runProcess(pythonBin, ['mix.py', 'voice48.wav', 'music.wav', 'sfx.wav'], {
      cwd: audioDir,
      signal,
      label: 'Doodle Verse audio mix',
    });
  }

  const framesDir = path.join(projectDir, 'frames');
  fs.mkdirSync(framesDir, { recursive: true });
  const batchSize = Math.max(30, Number(process.env.MUSE_DOODLE_FRAME_BATCH) || DEFAULT_FRAME_BATCH);
  for (let start = 0; start < frameCount; start += batchSize) {
    const end = Math.min(frameCount - 1, start + batchSize - 1);
    const pct = 42 + Math.round((start / frameCount) * 46);
    onProgress(pct, `[doodle-verse] Rendering frames ${start}-${end} of ${frameCount - 1}`, true);
    await runProcess('node', ['render.mjs', 'frames', `${start}-${end}`], {
      cwd: projectDir,
      env: { RESUME: '1', ...(useSwiftShader ? { SWIFTSHADER: '1' } : {}) },
      timeoutMs: Math.max(15 * 60 * 1000, frameCount * 2000),
      signal,
      label: `Doodle Verse render (${start}-${end})`,
      onStdoutLine: (line) => {
        if (/frame \d+ done/i.test(line)) {
          onProgress(Math.min(89, pct + 2), `[doodle-verse] ${line}`, true);
        }
      },
    });
  }

  const srtOut = path.join(projectDir, 'out', 'subtitles_en.srt');
  await runProcess(pythonBin, [path.join(projectDir, 'tools', 'srt.py'), wordsJsonPath, srtOut], {
    cwd: projectDir,
    signal,
    label: 'Doodle Verse subtitles',
  });

  onProgress(91, 'Encoding 1080p Doodle Verse master', true);
  const rawMaster = path.join(projectDir, 'out', 'video.mp4');
  fs.mkdirSync(path.dirname(rawMaster), { recursive: true });
  await runProcess(ffmpegBin, [
    '-y',
    '-framerate', '30',
    '-i', path.join(framesDir, 'f%04d.jpg'),
    '-i', mixWav,
    '-c:v', 'libx264',
    '-preset', 'medium',
    '-crf', '18',
    '-pix_fmt', 'yuv420p',
    '-c:a', 'aac',
    '-b:a', '192k',
    '-shortest',
    rawMaster,
  ], { cwd: projectDir, timeoutMs: 30 * 60 * 1000, signal, label: 'Doodle Verse FFmpeg encode' });

  const finalVideoPath = path.join(workDir, 'video.mp4');
  fs.copyFileSync(rawMaster, finalVideoPath);
  let finalSrtPath = '';
  if (fs.existsSync(srtOut)) {
    finalSrtPath = path.join(workDir, 'subtitles.srt');
    fs.copyFileSync(srtOut, finalSrtPath);
  }

  const renderedCount = fs.readdirSync(framesDir).filter((name) => /^f\d+\.jpg$/i.test(name)).length;
  const manifestPath = stampDoodleManifest(finalVideoPath, {
    jobId: job.jobId,
    environment: String(plan.environment || 'custom'),
    voice,
    aspect,
    durationSeconds,
    framesExpected: frameCount,
    framesRendered: renderedCount,
    inferRequests,
  });

  return {
    videoPath: finalVideoPath,
    thumbnailPath: '',
    subtitlesPath: finalSrtPath,
    meta: {
      engine: ENGINES.DOODLE,
      videoStyle: resolved.style,
      environment: String(plan.environment || ''),
      voice,
      durationSeconds,
      frameCount,
      renderedCount,
      doodleManifestPath: manifestPath,
      inferRequests,
    },
  };
}

module.exports = {
  PLAN_SCHEMA,
  REPAIR_SCHEMA,
  doodleRoot,
  resolvePlaywrightRoot,
  doodleRuntimeStatus,
  cleanGeneratedCode,
  groupWordBeats,
  mergeCustomOutfitsIntoCast,
  buildPlanMessages,
  inferWithMuse,
  render,
};
