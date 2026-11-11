'use strict';

/**
 * CLI contract test for scriptit/cli/generate.js.
 *
 * 1. No title -> exit 1 with an {"error": ...} object on stdout.
 * 2. Full worker-contract run in agent-bridge mode with a fake operator:
 *    a job JSON on stdin (topic "Apollo 11 Moon landing", 1 minute), live
 *    web research, and every inference request answered from the bridge dir.
 *    Asserts exit 0, a publishable script with a real hook, concrete
 *    entities, and honest meta (external claims > 0, no synthetic claims).
 *
 * The fake operator returns one fixed, researched Apollo 11 narration for
 * draft calls and hook JSON for hook calls. The narration paraphrases the
 * Apollo 11 Wikipedia record so the evidence-support gate can verify it
 * against the live research ledger.
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');

const CLI = path.join(__dirname, '..', 'cli', 'generate.js');

const CANNED_SCRIPT = [
  'On July 20, 1969, two men stood on the Moon while a third circled above them alone, and the whole landing depended on a computer with less processing power than a pocket calculator.',
  'Apollo 11 launched from Kennedy Space Center on July 16, 1969, on a Saturn V rocket. Neil Armstrong commanded the mission, Buzz Aldrin piloted the Lunar Module Eagle, and Michael Collins stayed aboard the Command Module Columbia in lunar orbit.',
  'Armstrong and Aldrin descended to the Sea of Tranquility while Collins orbited the Moon alone. Armstrong stepped onto the surface first, Aldrin followed nineteen minutes later, and the two collected rock samples and set up scientific instruments before returning to Eagle.',
  'The crew splashed down in the Pacific Ocean on July 24, 1969, eight days after launch. Apollo 11 was the first crewed mission to land humans on the Moon, and its landing site in the Sea of Tranquility remains one of the most visited places in the history of spaceflight.',
].join('\n\n');

const HOOK_JSON = JSON.stringify({
  variants: [
    { type: 'result-first', validateClick: 'Two men landed on the Moon.', raiseStakes: 'A third waited alone above.', openCuriosityLoop: 'How did they get home?' },
    { type: 'bold-claim', validateClick: 'Apollo 11 almost ran out of fuel.', raiseStakes: 'Seconds decided everything that day.', openCuriosityLoop: 'What saved the landing?' },
    { type: 'relatable-problem', validateClick: 'Imagine landing with no margin.', raiseStakes: 'Apollo 11 did exactly that.', openCuriosityLoop: 'Why did it still work?' },
  ],
});

function runCli({ stdin, env, cwd, timeoutMs = 300000 }) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [CLI], {
      cwd,
      env: { ...process.env, ...env },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      reject(new Error('CLI timed out. stderr tail: ' + stderr.slice(-2000)));
    }, timeoutMs);
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.on('error', (err) => { clearTimeout(timer); reject(err); });
    child.on('close', (code) => {
      clearTimeout(timer);
      resolve({ code, stdout, stderr });
    });
    if (stdin) child.stdin.end(stdin);
    else child.stdin.end();
  });
}

function startFakeOperator(bridgeRoot) {
  const seen = new Set();
  const timer = setInterval(() => {
    if (!fs.existsSync(bridgeRoot)) return;
    for (const id of fs.readdirSync(bridgeRoot)) {
      const dir = path.join(bridgeRoot, id);
      const requestPath = path.join(dir, 'request.json');
      if (seen.has(id) || !fs.existsSync(requestPath)) continue;
      let row;
      try { row = JSON.parse(fs.readFileSync(requestPath, 'utf8')); } catch { continue; }
      if (row.state !== 'pending') continue;
      seen.add(id);
      const text = JSON.stringify(row.messages || []);
      const content = /OPENING HOOK/.test(text) ? HOOK_JSON : CANNED_SCRIPT;
      fs.writeFileSync(path.join(dir, 'response.json'), JSON.stringify({ content }), 'utf8');
      row.state = 'answered';
      fs.writeFileSync(requestPath, JSON.stringify(row), 'utf8');
    }
  }, 100);
  return () => clearInterval(timer);
}

async function main() {
  // 0. Research-mode default (unit check, no spawn): short videos default
  // to the publishable 'standard' middle ground, longer ones to thorough;
  // an explicit flag or env value always wins.
  const { resolveResearchMode } = require('../cli/generate.js');
  assert.equal(resolveResearchMode({ flag: undefined, env: undefined, duration: '1min' }), 'standard');
  assert.equal(resolveResearchMode({ flag: undefined, env: undefined, duration: '2min' }), 'standard');
  assert.equal(resolveResearchMode({ flag: undefined, env: undefined, duration: '3min' }), 'thorough');
  assert.equal(resolveResearchMode({ flag: undefined, env: undefined, duration: '10min' }), 'thorough');
  assert.equal(resolveResearchMode({ flag: 'thorough', env: undefined, duration: '1min' }), 'thorough');
  assert.equal(resolveResearchMode({ flag: undefined, env: 'quick', duration: '10min' }), 'quick');
  assert.equal(resolveResearchMode({ flag: 'bogus', env: undefined, duration: '1min' }), 'standard');

  // 1. No title.
  const noTitle = await runCli({ stdin: '', env: {}, cwd: os.tmpdir(), timeoutMs: 30000 });
  assert.notEqual(noTitle.code, 0, 'CLI without a title must fail');
  const noTitleJson = JSON.parse(noTitle.stdout.trim().split('\n').at(-1));
  assert.match(noTitleJson.error, /no title/i);

  // 2. Full run with a fake operator.
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'scriptit-cli-test-'));
  const bridgeRoot = path.join(work, 'agent-bridge');
  const stopOperator = startFakeOperator(bridgeRoot);
  try {
    const job = {
      type: 'script',
      job: {
        jobId: 'cli-test-apollo',
        title: 'Apollo 11 Moon landing',
        description: 'The first crewed mission to land humans on the Moon.',
        targetDurationMinutes: 1,
        channel: { name: 'Documentary Archive', niche: 'Documentary', scriptSettings: { tone: 'conversational' } },
        requirements: {},
      },
      workDir: path.join(work, 'job'),
      scriptPath: path.join(work, 'job', 'script.txt'),
      requirements: {},
    };
    const run = await runCli({
      stdin: JSON.stringify(job),
      env: { SCRIPTIT_AGENT_BRIDGE_DIR: bridgeRoot },
      cwd: work,
      timeoutMs: 300000,
    });
    assert.equal(run.code, 0, 'CLI must exit 0. stdout: ' + run.stdout.slice(-1000) + ' stderr tail: ' + run.stderr.slice(-2000));
    // The worker contract is exactly one JSON object on stdout; diagnostics
    // belong on stderr, so a second line here is a contract break.
    assert.equal(run.stdout.trim().split('\n').length, 1, 'stdout must be exactly one JSON line, got: ' + run.stdout.slice(0, 500));
    const out = JSON.parse(run.stdout.trim());
    assert.ok(out.scriptText && out.scriptText.length > 500, 'script text present');
    assert.equal(out.meta.publishable, true, 'script must be publishable, issues: ' + JSON.stringify(out.meta.quality?.issues));
    assert.ok(out.meta.quality.words >= 136, 'meets 1-minute minimum words, got ' + out.meta.quality.words);
    assert.equal(out.meta.quality.hook, true, 'opening hooks the viewer');
    assert.ok((out.meta.quality.specificity?.count || 0) >= 4, 'names concrete entities: ' + JSON.stringify(out.meta.quality.specificity));
    assert.ok((out.meta.evidence?.externalClaims || 0) > 0, 'grounded in external claims');
    assert.equal(out.meta.evidence?.syntheticClaims || 0, 0, 'no synthetic claims in the ledger');
    assert.ok(!/documented sequence of events|few chapters offer as much clarity|behind the familiar headlines/i.test(out.scriptText), 'no template phrasing');
    assert.ok(/Apollo 11|Armstrong/.test(out.scriptText), 'stays on the requested subject');
    assert.ok(Array.isArray(out.meta.hookVariants) && out.meta.hookVariants.length >= 1, 'hook variants in meta');
    console.log('CLI CHECKS PASSED: ' + out.meta.quality.words + ' words, score ' + out.meta.quality.score + ', external claims ' + out.meta.evidence.externalClaims);
  } finally {
    stopOperator();
    fs.rmSync(work, { recursive: true, force: true });
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
