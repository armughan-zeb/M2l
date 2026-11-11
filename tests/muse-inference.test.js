'use strict';

// Muse inference is compulsory: every test here proves a fail-closed
// behaviour — no bridge, no OpenRouter key, no unverified media.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const {
  openRouterKeysPresent,
  checkMuseInferenceBridge,
  verifyMuseInferenceBridge
} = require('../lib/museInference');

function bridgeFixture({ hookEnabled = true } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-inference-'));
  const bridgeRoot = path.join(dir, 'agent-bridge');
  fs.mkdirSync(bridgeRoot, { recursive: true });
  const vidnetsRoot = path.join(dir, 'vidnets');
  fs.mkdirSync(vidnetsRoot, { recursive: true });
  fs.writeFileSync(path.join(vidnetsRoot, 'VIDNETS_AGENT.sh'), '#!/bin/sh\n');
  const hookPath = path.join(dir, 'hook.json');
  fs.writeFileSync(hookPath, JSON.stringify({ enabled: hookEnabled, id: 'vidnets-inbox-watch' }));
  return {
    dir,
    env: {
      MUSE_RENDER_COMMAND: 'node /home/hatch/workspace/M2l/render-vidnets-linux.js',
      MUSE_VIDNETS_ROOT: vidnetsRoot,
      MUSE_INBOX_HOOK_DEFINITION: hookPath,
      VIDZEO_BRIDGE_ROOT: bridgeRoot
    }
  };
}

test('OpenRouter keys are detected, external-agent handles are not', () => {
  assert.deepEqual(openRouterKeysPresent({ OPENROUTER_API_KEY: 'sk-or-x' }), ['OPENROUTER_API_KEY']);
  assert.deepEqual(openRouterKeysPresent({ VIDZEO_OPENROUTER_API_KEY: 'sk-or-x', SCRIPTIT_API_KEY: '' }), ['VIDZEO_OPENROUTER_API_KEY']);
  assert.deepEqual(openRouterKeysPresent({ OPENROUTER_API_KEY: 'external-agent:local-user' }), []);
  assert.deepEqual(openRouterKeysPresent({}), []);
});

test('bridge check passes for the production wiring and fails without it', () => {
  const { env } = bridgeFixture();
  assert.equal(checkMuseInferenceBridge(env).ok, true);

  const noRender = checkMuseInferenceBridge({
    MUSE_VIDNETS_ROOT: env.MUSE_VIDNETS_ROOT,
    MUSE_INBOX_HOOK_DEFINITION: env.MUSE_INBOX_HOOK_DEFINITION,
    VIDZEO_BRIDGE_ROOT: env.VIDZEO_BRIDGE_ROOT
  });
  assert.equal(noRender.ok, false);

  const noInbox = checkMuseInferenceBridge({
    ...env,
    VIDZEO_BRIDGE_ROOT: path.join(env.VIDZEO_BRIDGE_ROOT, 'does-not-exist')
  });
  assert.equal(noInbox.ok, false);
  assert.ok(noInbox.failures.some(failure => /inbox/i.test(failure)));

  const keySet = checkMuseInferenceBridge({ ...env, OPENROUTER_API_KEY: 'sk-or-x' });
  assert.equal(keySet.ok, false);
  assert.ok(keySet.failures.some(failure => /OpenRouter/i.test(failure)));
});

test('bridge verification probes the live Vidnets server', async () => {
  const { env } = bridgeFixture();
  const server = http.createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, ffmpeg: true }));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  try {
    const up = await verifyMuseInferenceBridge({ ...env, MUSE_VIDNETS_URL: `http://127.0.0.1:${port}` });
    assert.equal(up.ok, true, JSON.stringify(up.failures));
  } finally {
    await new Promise(resolve => server.close(resolve));
  }
  const down = await verifyMuseInferenceBridge({ ...env, MUSE_VIDNETS_URL: 'http://127.0.0.1:1' });
  assert.equal(down.ok, false);
  assert.ok(down.failures.some(failure => /unreachable/i.test(failure)));
});

test('Vidnets buildRequest enforces external_agent and refuses an OpenRouter key', () => {
  const { buildRequest } = require('../adapters/vidnets');
  const job = { jobId: 'test-job', title: 'Test Title', channel: { name: 'Test', niche: 'Test' }, targetDurationMinutes: 1 };
  const previous = process.env.OPENROUTER_API_KEY;
  try {
    process.env.OPENROUTER_API_KEY = 'sk-or-test-key';
    assert.throws(() => buildRequest(job, 'Some narration.', '/tmp'), /Muse inference is compulsory/i);
  } finally {
    if (previous === undefined) delete process.env.OPENROUTER_API_KEY;
    else process.env.OPENROUTER_API_KEY = previous;
  }
  const request = buildRequest(job, 'Some narration.', '/tmp');
  assert.equal(request.llm_provider, 'external_agent');
  assert.equal(request.openrouter_api_key, '');
  // Filter-best (Nov 9, 2026): vision is advisory by default — the two
  // strict flags are OFF, so an uncertain verdict never blocks delivery.
  // Named-entity scenes still require a live verdict inside the server
  // (strict_identity), and the delivery rule lives in the wrapper.
  assert.equal(request.visual_required, false);
  assert.equal(request.strict_retrieval_accuracy, false);
  assert.equal(request.visual_understanding, true);
  // MUSE_MEDIA_STRICT=1 restores the old hard-required flags.
  const savedStrict = process.env.MUSE_MEDIA_STRICT;
  try {
    process.env.MUSE_MEDIA_STRICT = '1';
    const strict = buildRequest(job, 'Some narration.', '/tmp');
    assert.equal(strict.visual_required, true);
    assert.equal(strict.strict_retrieval_accuracy, true);
  } finally {
    if (savedStrict === undefined) delete process.env.MUSE_MEDIA_STRICT;
    else process.env.MUSE_MEDIA_STRICT = savedStrict;
  }
});

test('video render with no render path throws instead of falling back to Remotion native', async () => {
  const video = require('../adapters/video');
  const saved = {};
  for (const name of ['MUSE_RENDER_COMMAND', 'MUSE_VIDNETS_COMMAND', 'MUSE_VIDNETS_ROOT', 'MUSE_INFERENCE_COMMAND', 'MUSE_INFERENCE_URL']) {
    saved[name] = process.env[name];
    delete process.env[name];
  }
  try {
    await assert.rejects(
      () => video.render({ jobId: 'test-job', title: 'Test' }, 'Some narration.', '/tmp'),
      /No Muse-inference render path/i
    );
  } finally {
    for (const [name, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  }
});

test('real scenes count adapter-accepted media; Muse rejection always wins', () => {
  const { countRealScenes } = require('../render-vidnets-linux');
  // The proven 0bb3f900 edit plan: 1 real YouTube clip, Muse vision
  // accepted (visual_checked=true), the other 14 scenes are graphics.
  const planDir = '/home/hatch/workspace/vidnets-runtime/output/' +
    "Amtrak's Long Run Through the Crew's Day_20261108_141818_71f36cd9";
  const counted = countRealScenes(planDir);
  assert.ok(counted, 'edit plan should be found');
  assert.equal(counted.total, 15);
  assert.equal(counted.real, 1);
  assert.equal(counted.verified, 1);

  // Filter-best: a real-looking image with no Muse verdict and no
  // adapter acceptance still does not count.
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-plan-'));
  fs.writeFileSync(path.join(dir, 'x_remotion_edit_plan.json'), JSON.stringify({
    scenes: [
      { media: { type: 'image', src: '/tmp/real.jpg', visual_decision: { visual_checked: false, accepted: true } } },
      { media: { type: 'graphic', src: '/tmp/graphic_fallback_1.png' } }
    ]
  }));
  const unverified = countRealScenes(dir);
  assert.equal(unverified.real, 0);
  assert.equal(unverified.verified, 0);
  assert.equal(unverified.graphic, 2);

  // A clip the adapter accepted (verified flag, no Muse verdict — the
  // fail-open path) counts as real, but is not Muse-verified.
  // An explicitly Muse-rejected clip never counts, even if the flag says
  // verified.
  const dir2 = fs.mkdtempSync(path.join(os.tmpdir(), 'muse-plan-'));
  fs.writeFileSync(path.join(dir2, 'x_remotion_edit_plan.json'), JSON.stringify({
    scenes: [
      { media: { type: 'video', src: '/tmp/clip.mp4', verified: true, visual_decision: {} } },
      { media: { type: 'video', src: '/tmp/clip2.mp4', verified: true, visual_decision: { accepted: false } } },
      { media: { type: 'graphic', src: '/tmp/graphic_fallback_1.png' } }
    ]
  }));
  const mixed = countRealScenes(dir2);
  assert.equal(mixed.real, 1);
  assert.equal(mixed.verified, 0);
  assert.equal(mixed.graphic, 2);

  assert.equal(countRealScenes(path.join(dir, 'missing')), null);
});

test('ScriptIT CLI refuses OpenRouter when Muse inference is compulsory', async () => {
  const cli = path.join(__dirname, '..', 'scriptit', 'cli', 'generate.js');
  const result = await new Promise(resolve => {
    // spawn (not execFile): the CLI reads stdin when it is not a TTY, and
    // an empty execFile `input` does not close stdin on this Node version.
    const { spawn } = require('node:child_process');
    const child = spawn(process.execPath, [cli, '--topic', 'Test title'], {
      env: {
        ...process.env,
        MUSE_INFERENCE_COMPULSORY: '1',
        OPENROUTER_API_KEY: 'sk-or-test-key'
      }
    });
    let stdout = '';
    child.stdout.on('data', chunk => { stdout += chunk.toString(); });
    child.stdin.end(); // no stdin payload: the CLI must not block on it
    const timer = setTimeout(() => child.kill('SIGKILL'), 15000);
    child.on('close', code => {
      clearTimeout(timer);
      resolve({ code, stdout });
    });
  });
  assert.notEqual(result.code, 0);
  assert.match(result.stdout, /Muse inference is compulsory/i);
});
