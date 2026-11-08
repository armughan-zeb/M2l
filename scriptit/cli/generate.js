#!/usr/bin/env node
'use strict';

/**
 * ScriptIT headless CLI — run the ScriptIT engine without Electron.
 *
 * This is the production script step for the Muse worker (adapters/script.js
 * calls it as MUSE_SCRIPT_COMMAND) and for any other Muse worker that needs
 * a researched, hook-first, quality-gated narration for a title.
 *
 * Input (pick one):
 *   1. Worker contract: a job JSON object on stdin, exactly what
 *      adapters/script.js sends:
 *        { "type": "script", "job": {...}, "workDir": "...",
 *          "scriptPath": "...", "requirements": {...}, "expansion": {...}? }
 *      The job supplies title, description/premise, channel scriptSettings
 *      (tone, customStructure, rules), targetDurationMinutes and jobId.
 *   2. Flags: --topic "Title" [--description "..."] [--duration 8min]
 *      [--job-id ...] [--voice conversational] [--research-mode thorough]
 *
 * Model (no key is needed for the default path):
 *   - Default: agent-bridge mode. Inference requests go to the Vidnets
 *     agent-bridge inbox (request.json under the bridge root) and the
 *     operating Muse agent answers them — the same transport Vidnets uses
 *     for vision/text. Requires the inbox watcher to be running so requests
 *     are answered (see M2l README: ScriptIT section). Override the owner
 *     with SCRIPTIT_AGENT_KEY=agent-bridge:<owner> (default local-user).
 *   - OpenRouter: set SCRIPTIT_API_KEY / SCRIPTIT_OPENROUTER_API_KEY /
 *     OPENROUTER_API_KEY, or pass --api-key. Then --model selects the model.
 *
 * Output: exactly one JSON object on stdout:
 *   { "scriptText": "...", "meta": {...} }
 * All progress/logs go to stderr. Exit code is non-zero (with an
 * {"error": "..."} object on stdout) when no publishable script could be
 * produced — the worker must fail the job rather than ship generic
 * narration. A draft that fails the editorial gate is NOT silently
 * substituted with template text.
 *
 * Environment:
 *   SCRIPTIT_AGENT_BRIDGE_DIR / VIDNETS_AGENT_BRIDGE_DIR / VIDZEO_TEMP_DIR
 *     — bridge root for agent-bridge mode (default:
 *       ~/workspace/vidnets-runtime/temp/agent-bridge).
 *   SCRIPTIT_JOB_ID — correlated into bridge requests (set by the worker).
 *   NODE_EXTRA_CA_CERTS — must include the egress proxy CA on Muse VMs
 *     (already set globally there); otherwise web research cannot fetch.
 */

const fs = require('fs');
const path = require('path');

function log(...args) {
  process.stderr.write('[scriptit-cli] ' + args.join(' ') + '\n');
}

function fail(message, code = 1) {
  process.stdout.write(JSON.stringify({ error: String(message) }) + '\n');
  // Exit explicitly: generateScript leaves long-lived timers/handles behind
  // (its 45-minute total-deadline timer, the research store), so returning
  // from main would leave this one-shot CLI hanging for up to 45 minutes.
  process.exit(code);
}

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (!arg.startsWith('--')) continue;
    const key = arg.slice(2);
    const next = argv[i + 1];
    if (next !== undefined && !next.startsWith('--')) {
      out[key] = next;
      i += 1;
    } else {
      out[key] = true;
    }
  }
  return out;
}

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (chunk) => { data += chunk; });
    process.stdin.on('end', () => resolve(data));
    process.stdin.on('error', reject);
  });
}

function durationLabel(minutes) {
  const n = Number(minutes);
  if (Number.isFinite(n) && n > 0) return `${n}min`;
  return '10min';
}

// Node 24's bundled undici crashes the whole process with
// `assert(!this.paused)` when a research fetch is aborted mid-response
// (the egress proxy closes the socket while the parser is paused). That is a
// runtime bug, not a ScriptIT bug — but it must not kill a generation.
// Swallow ONLY that assertion and let the aborted fetch settle through its
// own timeout, so the provider degrades to "failed" instead of crashing the
// CLI. Every other uncaught error still kills the process.
process.on('uncaughtException', (err) => {
  const undiciParserCrash = err && err.code === 'ERR_ASSERTION'
    && /assert\(!this\.paused\)/.test(String(err.message || ''))
    && /undici/.test(String(err.stack || ''));
  if (undiciParserCrash) {
    process.stderr.write('[scriptit-cli] Ignored a Node undici abort assertion during research; the affected provider fetch will time out on its own.\n');
    return;
  }
  throw err;
});

async function main() {
  const flags = parseArgs(process.argv.slice(2));
  let job = null;
  let expansion = null;
  let workDir = String(flags['work-dir'] || '').trim();
  let scriptPath = String(flags['script-path'] || '').trim();

  const hasStdin = !process.stdin.isTTY;
  if (hasStdin) {
    const raw = (await readStdin()).trim();
    if (raw) {
      let payload;
      try {
        payload = JSON.parse(raw);
      } catch {
        fail('ScriptIT CLI: stdin is not valid JSON');
        return;
      }
      job = payload.job || payload;
      expansion = payload.expansion || null;
      workDir = workDir || String(payload.workDir || '').trim();
      scriptPath = scriptPath || String(payload.scriptPath || '').trim();
    }
  }

  const title = String(flags.topic || flags.title || job?.title || '').trim();
  if (!title) {
    fail('ScriptIT CLI: no title — pass --topic "..." or a job JSON on stdin with job.title');
    return;
  }
  const jobId = String(flags['job-id'] || job?.jobId || job?.id || '').trim();
  if (jobId) process.env.SCRIPTIT_JOB_ID = jobId;

  const description = String(
    flags.description
    || job?.description
    || job?.premise
    || job?.titlePackage?.description
    || '',
  ).trim();
  const scriptSettings = job?.channel?.scriptSettings || {};
  const voice = String(flags.voice || scriptSettings.tone || 'conversational').trim();
  const customStructure = String(flags.structure || scriptSettings.customStructure || job?.requirements?.sceneGuidelines?.structure || '').trim();
  const duration = String(flags.duration || durationLabel(job?.targetDurationMinutes) || '10min').trim();
  const researchMode = String(flags['research-mode'] || process.env.SCRIPTIT_RESEARCH_MODE || 'thorough').trim().toLowerCase();
  const model = String(flags.model || process.env.SCRIPTIT_MODEL || 'nvidia/nemotron-3.5-super-vl:free').trim();

  // Model selection: an explicit OpenRouter key wins; otherwise the
  // agent-bridge handle (no key, Muse answers through the Vidnets inbox).
  const openRouterKey = String(flags['api-key'] || process.env.SCRIPTIT_API_KEY
    || process.env.SCRIPTIT_OPENROUTER_API_KEY || process.env.OPENROUTER_API_KEY || '').trim();
  const bridgeMode = !openRouterKey;
  const apiKey = openRouterKey
    || String(process.env.SCRIPTIT_AGENT_KEY || 'agent-bridge:local-user').trim();
  if (bridgeMode) {
    const { bridgeRoot } = require('../src/providers/agentBridge');
    log(`mode=agent-bridge bridge=${bridgeRoot()} job=${jobId || 'none'}`);
  } else {
    log(`mode=openrouter model=${model} job=${jobId || 'none'}`);
  }
  log(`topic="${title}" duration=${duration} research=${researchMode}`);

  const { generateScript } = require('../src/agents/scriptWriter');
  const totalDeadlineMs = bridgeMode
    ? Number(process.env.SCRIPTIT_TOTAL_DEADLINE_MS || 2700000)
    : Number(process.env.SCRIPTIT_TOTAL_DEADLINE_MS || 600000);
  const timeoutMs = bridgeMode ? 300000 : 120000;

  let result;
  try {
    result = await generateScript({
      topic: title,
      titleContext: description,
      description,
      model,
      duration,
      contentType: 'documentary',
      voice,
      customStructure,
      researchMode,
      apiKey,
      timeoutMs,
      totalDeadlineMs,
      useCache: true,
      recordHistory: true,
      initialDraft: expansion?.previousScript || '',
      onProgress: (progress, message) => log(`${progress}% ${message}`),
    });
  } catch (err) {
    fail(`ScriptIT generation failed for "${title}": ${err.message || err}`);
    return;
  }

  const scriptText = String(result?.script || '').trim();
  const meta = result?.meta || {};
  if (!scriptText) {
    fail(`ScriptIT returned no narration for "${title}" (publishable=${meta.publishable === true})`);
    return;
  }
  if (meta.publishable !== true) {
    const issues = [...(meta.quality?.issues || [])].slice(0, 5).join(' ') || 'editorial gate did not pass';
    log(`WARNING: script is a non-publishable draft (${meta.status || 'draft'}): ${issues}`);
    // Leave the draft beside script.txt for the operator to inspect or
    // promote by hand. Never write it to script.txt: the worker reuses a
    // saved script.txt on retry, and a failed-gate draft must not silently
    // become the narration.
    const draftFile = workDir ? path.join(workDir, 'script-draft.json') : '';
    if (draftFile) {
      try {
        fs.mkdirSync(workDir, { recursive: true });
        fs.writeFileSync(draftFile, JSON.stringify({ scriptText, meta }, null, 2) + '\n', 'utf8');
        log(`Draft saved for review: ${draftFile}`);
      } catch (err) {
        log(`Could not save draft file: ${err.message}`);
      }
    }
    log('Refusing to present a failed draft as a finished script. Returning it with publishable=false so the worker can decide.');
    process.stdout.write(JSON.stringify({
      scriptText,
      meta: { ...meta, publishable: false },
    }) + '\n');
    process.exit(2);
  }
  log(`done: ${meta.quality?.words || scriptText.split(/\s+/).length} words, quality score ${meta.quality?.score ?? 'n/a'}, hook=${meta.quality?.hook === true}`);
  process.stdout.write(JSON.stringify({ scriptText, meta }) + '\n');
  process.exit(0);
}

main().catch((err) => {
  fail(`ScriptIT CLI crashed: ${err && err.message ? err.message : err}`);
});
