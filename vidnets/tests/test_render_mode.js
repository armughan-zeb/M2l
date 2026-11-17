const { test } = require('node:test');
const assert = require('node:assert/strict');
const { classifyProcesses, enterRenderMode, parsePsOutput } = require('../scripts/render-mode');

// Synthetic process table: self pid 100, parent chain 100 -> 90 -> 1.
function table() {
  return [
    { pid: 1, ppid: 0, rssKB: 1024, args: '/sbin/init' },
    { pid: 90, ppid: 1, rssKB: 2048, args: 'python3 -u /home/hatch/workspace/M2l/vidnets/main.py' },
    { pid: 100, ppid: 90, rssKB: 4096, args: 'node vidnets/scripts/render-remotion-manifest.js' },
    { pid: 200, ppid: 1, rssKB: 512, args: 'node muse-agent.js start' },
    { pid: 201, ppid: 1, rssKB: 1024, args: '/opt/hatch/bin/hatch daemon --runtime-cell-leader=1' },
    { pid: 202, ppid: 1, rssKB: 256, args: 'tmux: server' },
    { pid: 203, ppid: 1, rssKB: 256, args: 'bash /home/hatch/hooks/scripts/vidnets-inbox-watch.sh' },
    { pid: 204, ppid: 1, rssKB: 256, args: 'bash /home/hatch/hooks/scripts/vidnets-health-monitor.sh' },
    { pid: 205, ppid: 1, rssKB: 1024, args: 'node /home/hatch/workspace/M2l/node_modules/vitest/vitest.mjs run' },
    { pid: 206, ppid: 1, rssKB: 1024, args: 'node /home/hatch/workspace/M2l/node_modules/.bin/jest tests' },
    { pid: 207, ppid: 1, rssKB: 512, args: 'python3 -m pytest vidnets/tests/test_x.py' },
    { pid: 208, ppid: 1, rssKB: 512, args: '/usr/bin/dockerd --config-file /etc/docker/daemon.json' },
    { pid: 209, ppid: 1, rssKB: 1024, args: '/home/hatch/chrome-headless-shell-linux64/chrome-headless-shell --headless' },
    { pid: 210, ppid: 1, rssKB: 512, args: 'ffmpeg -y -i /tmp/clip.mp4 -vf scale=1280:720 /tmp/out.mp4' },
    { pid: 211, ppid: 1, rssKB: 512, args: 'ffmpeg -y -i /home/hatch/workspace/vidnets-runtime/temp/jobs/abc/scene.mp4 -vf scale=1280:720 /tmp/out.mp4' },
    { pid: 212, ppid: 1, rssKB: 512, args: 'node /home/hatch/workspace/M2l/scripts/some-worker.js' },
    { pid: 213, ppid: 1, rssKB: 128, args: '[kworker/0:1-events]' },
  ];
}

test('render mode kills test runners, dockerd and stray chrome/ffmpeg', () => {
  const { kill } = classifyProcesses(table(), 100);
  const killed = kill.map((p) => p.pid).sort((a, b) => a - b);
  assert.deepEqual(killed, [205, 206, 207, 208, 209, 210]);
});

test('render mode protects the server, daemon, ancestors and unknown processes', () => {
  const { kill, protect } = classifyProcesses(table(), 100);
  const killed = new Set(kill.map((p) => p.pid));
  // Server (90), self (100), PID 1, daemon (200), hatch daemon (201),
  // tmux (202), inbox watcher (203), health monitor (204) and the unknown
  // worker (212) plus the kernel thread (213) must never be killed.
  for (const pid of [1, 90, 100, 200, 201, 202, 203, 204, 212, 213]) {
    assert.equal(killed.has(pid), false, `pid ${pid} must be protected`);
    assert.equal(protect.some((p) => p.pid === pid), true, `pid ${pid} must be listed as protected`);
  }
});

test('render mode spares ffmpeg working on current-job media', () => {
  const { kill } = classifyProcesses(table(), 100);
  const killed = kill.map((p) => p.pid);
  assert.equal(killed.includes(211), false);
});

test('render mode dry-run kills nothing and reports the would-kill list', async () => {
  const report = await enterRenderMode({ dryRun: true });
  assert.equal(report.enabled, true);
  assert.deepEqual(report.killed, []);
  assert.equal(Array.isArray(report.wouldKill), true);
  assert.equal(typeof report.memBeforeMB, 'number');
});

test('render mode honours VIDZEO_RENDER_MODE=0', async () => {
  const previous = process.env.VIDZEO_RENDER_MODE;
  process.env.VIDZEO_RENDER_MODE = '0';
  try {
    const report = await enterRenderMode({ dryRun: true });
    assert.equal(report.enabled, false);
    assert.deepEqual(report.killed, []);
    assert.deepEqual(report.wouldKill, []);
  } finally {
    if (previous === undefined) delete process.env.VIDZEO_RENDER_MODE;
    else process.env.VIDZEO_RENDER_MODE = previous;
  }
});

test('ps output parsing keeps the full command line', () => {
  const parsed = parsePsOutput('  123    1  51200 node muse-agent.js start\n  456  123  1024 /usr/bin/dockerd --config-file /etc/docker/daemon.json\n');
  assert.deepEqual(parsed, [
    { pid: 123, ppid: 1, rssKB: 51200, args: 'node muse-agent.js start' },
    { pid: 456, ppid: 123, rssKB: 1024, args: '/usr/bin/dockerd --config-file /etc/docker/daemon.json' },
  ]);
});

// Regression: substring matching killed innocent processes that merely
// MENTIONED a kill word (a sibling worker's `grep -rn ffmpeg ...`, a
// `check-chromium-version.js` checker, a `--out jest-report.html` build
// flag). Only the executed binary, a script path, or a launcher matches.
test('classifier ignores kill words buried in arguments', () => {
  const procs = [
    { pid: 1, ppid: 0, rssKB: 1024, args: '/sbin/init' },
    { pid: 100, ppid: 1, rssKB: 4096, args: 'node vidnets/scripts/render-remotion-manifest.js' },
    { pid: 301, ppid: 1, rssKB: 3000, args: 'grep -rn ffmpeg --include=*.js vidnets/' },
    { pid: 302, ppid: 1, rssKB: 3000, args: 'grep -rn chromium vidnets/ --include=*.md' },
    { pid: 303, ppid: 1, rssKB: 3000, args: 'node /home/hatch/workspace/M2l/scripts/check-chromium-version.js' },
    { pid: 304, ppid: 1, rssKB: 3000, args: 'node scripts/build.js --out jest-report.html' },
    { pid: 305, ppid: 1, rssKB: 3000, args: '/bin/bash --norc --noprofile -c cd ~/workspace/M2l && grep -rn ffmpeg --include=*.js .' },
    // ...but a real ffmpeg wrapped in `bash -c` is still caught.
    { pid: 306, ppid: 1, rssKB: 90000, args: '/bin/bash -c ffmpeg -y -i /tmp/old.mp4 /tmp/old-out.mp4' },
  ];
  const { kill } = classifyProcesses(procs, 100);
  assert.deepEqual(kill.map((p) => p.pid), [306]);
});

// Regression: disposables the old substring list missed outright.
test('classifier kills rspack, esbuild, tsc, unittest, yt-dlp and friends', () => {
  const procs = [
    { pid: 1, ppid: 0, rssKB: 1024, args: '/sbin/init' },
    { pid: 100, ppid: 1, rssKB: 4096, args: 'node vidnets/scripts/render-remotion-manifest.js' },
    { pid: 401, ppid: 1, rssKB: 200000, args: 'node /home/hatch/workspace/M2l/node_modules/rspack/dist/index.js build' },
    { pid: 402, ppid: 1, rssKB: 60000, args: 'node /home/hatch/workspace/M2l/node_modules/.bin/esbuild --bundle entry.jsx' },
    { pid: 403, ppid: 1, rssKB: 40000, args: '/usr/bin/tsc --watch -p vidnets/tsconfig.json' },
    { pid: 404, ppid: 1, rssKB: 50000, args: 'python3 -m unittest vidnets.tests.test_x' },
    { pid: 405, ppid: 1, rssKB: 80000, args: 'yt-dlp -f bestvideo https://example.com/v' },
    { pid: 406, ppid: 1, rssKB: 30000, args: '/usr/bin/ffprobe -show_streams /tmp/clip.mp4' },
    { pid: 407, ppid: 1, rssKB: 90000, args: '/usr/bin/headless_shell --headless --dump-dom http://example.com' },
    { pid: 408, ppid: 1, rssKB: 90000, args: '/usr/bin/chrome --headless --dump-dom http://example.com' },
    { pid: 409, ppid: 1, rssKB: 30000, args: 'npm test' },
    { pid: 410, ppid: 1, rssKB: 30000, args: 'node --test vidnets/tests/test_x.js' },
  ];
  const { kill } = classifyProcesses(procs, 100);
  assert.deepEqual(kill.map((p) => p.pid).sort((a, b) => a - b),
    [401, 402, 403, 404, 405, 406, 407, 408, 409, 410]);
});

// The ffmpeg exemption is for the CURRENT job's media, not every ffmpeg
// under vidnets-runtime: a stale ffmpeg from a dead job must be killable
// once the caller narrows the exemption to the live job's output dir.
test('ffmpeg exemption narrows to the current job dir when given', () => {
  const procs = [
    { pid: 1, ppid: 0, rssKB: 1024, args: '/sbin/init' },
    { pid: 100, ppid: 1, rssKB: 4096, args: 'node vidnets/scripts/render-remotion-manifest.js' },
    { pid: 501, ppid: 1, rssKB: 90000, args: 'ffmpeg -y -i /tmp/x.mp4 /home/hatch/workspace/vidnets-runtime/temp/jobs/dead-job/scene.mp4' },
    { pid: 502, ppid: 1, rssKB: 90000, args: 'ffmpeg -y -i /tmp/x.mp4 /home/hatch/workspace/vidnets-runtime/temp/jobs/live-job-123/scene.mp4' },
  ];
  const narrowed = classifyProcesses(procs, 100, { protectedMediaSubstrings: ['live-job-123'] });
  assert.deepEqual(narrowed.kill.map((p) => p.pid), [501]);
  assert.deepEqual(narrowed.protect.map((p) => p.pid).sort((a, b) => a - b), [1, 100, 502]);
  // Default behaviour is unchanged: both are spared without narrowing.
  const { kill } = classifyProcesses(procs, 100);
  assert.deepEqual(kill, []);
});

test('inference spool workers are protected, never shed', () => {
  const procs = [
    { pid: 1, ppid: 0, rssKB: 1024, args: '/sbin/init' },
    { pid: 100, ppid: 1, rssKB: 4096, args: 'node vidnets/scripts/render-remotion-manifest.js' },
    { pid: 601, ppid: 1, rssKB: 35000, args: 'node /home/hatch/workspace/M2l/scripts/muse-inference-spool.cjs' },
  ];
  const { kill, protect } = classifyProcesses(procs, 100);
  assert.deepEqual(kill, []);
  assert.equal(protect.some((p) => p.pid === 601), true);
});

// Regression (GTA VI ef306926, Nov 16, 2026): the per-section guard ran
// AFTER selectComposition opened the render's own browser, and the
// classifier protected only ancestors — so it SIGTERM/SIGKILLed the
// selection browser's Chrome tree (4 processes) seconds before the
// section render reused it. Every attempt that reached section 1 then
// sat at 0 frames until the 300 s watchdog killed it; renders from
// before render-mode existed (Wright, Berlin) completed fine. The
// render's own descendants must be protected; orphans from dead
// attempts (reparented to PID 1) are still shed as leftovers.
test('render mode protects its own descendant browser/ffmpeg, kills orphan chrome', () => {
  const procs = [
    { pid: 1, ppid: 0, rssKB: 1024, args: '/sbin/init' },
    { pid: 90, ppid: 1, rssKB: 2048, args: 'python3 -u /home/hatch/workspace/M2l/vidnets/main.py' },
    { pid: 100, ppid: 90, rssKB: 4096, args: 'node vidnets/scripts/render-remotion-manifest.js' },
    // The selection browser tree: children of the render process.
    { pid: 701, ppid: 100, rssKB: 90000, args: '/home/hatch/chrome-headless-shell-linux64/chrome-headless-shell --headless --remote-debugging-port=9222' },
    { pid: 702, ppid: 701, rssKB: 60000, args: '/home/hatch/chrome-headless-shell-linux64/chrome-headless-shell --type=renderer --headless' },
    { pid: 703, ppid: 701, rssKB: 40000, args: '/home/hatch/chrome-headless-shell-linux64/chrome-headless-shell --type=gpu-process --headless' },
    // The render's own ffmpeg (section finalize), even on a /tmp path.
    { pid: 704, ppid: 100, rssKB: 50000, args: 'ffmpeg -y -i /tmp/section-0.mp4 /tmp/section-0.compatible.mp4' },
    // An orphaned Chrome tree from a dead attempt (reparented to PID 1).
    { pid: 705, ppid: 1, rssKB: 90000, args: '/home/hatch/chrome-headless-shell-linux64/chrome-headless-shell --headless --remote-debugging-port=9223' },
    { pid: 706, ppid: 705, rssKB: 60000, args: '/home/hatch/chrome-headless-shell-linux64/chrome-headless-shell --type=renderer --headless' },
  ];
  const { kill, protect } = classifyProcesses(procs, 100);
  assert.deepEqual(kill.map((p) => p.pid).sort((a, b) => a - b), [705, 706]);
  for (const pid of [701, 702, 703, 704]) {
    assert.equal(protect.some((p) => p.pid === pid), true, `descendant pid ${pid} must be protected`);
  }
});

test('dry-run reports swap in use alongside available memory', async () => {
  const report = await enterRenderMode({ dryRun: true });
  assert.equal(report.enabled, true);
  assert.equal(typeof report.swapUsedMB, 'number');
  assert.ok(report.swapUsedMB >= 0, `swapUsedMB should be >= 0 on Linux, got ${report.swapUsedMB}`);
});

// Regression: signalAndWait treated zombies as survivors (kill(pid, 0)
// succeeds on a zombie), so a reaped kill was reported as surviving.
// A real zombie — a forked child that exited while its parent sleeps
// without waitpid — must read as dead while kill(pid, 0) still succeeds.
test('zombie processes count as dead, not survivors', async () => {
  const { spawn } = require('child_process');
  const { isProcessAlive } = require('../scripts/render-mode');
  assert.equal(isProcessAlive(process.pid), true);
  assert.equal(isProcessAlive(2147483647), false);
  const python = spawn('python3', ['-c', [
    'import os, time',
    'pid = os.fork()',
    'if pid == 0:',
    '    os._exit(0)',
    // Wait until the child really is a zombie before announcing it:
    // printing right after fork races the child still being alive.
    'while True:',
    '    with open(f"/proc/{pid}/stat") as f:',
    '        state = f.read().rsplit(")", 1)[-1].split()[0]',
    '    if state == "Z":',
    '        break',
    '    time.sleep(0.01)',
    'print(pid, flush=True)',
    'time.sleep(10)',
  ].join('\n')]);
  const zombiePid = await new Promise((resolve, reject) => {
    let out = '';
    python.stdout.on('data', (chunk) => {
      out += chunk;
      const match = out.match(/(\d+)/);
      if (match) resolve(Number(match[1]));
    });
    python.on('error', reject);
  });
  try {
    // Prove it really is a zombie: signal 0 succeeds (it exists)...
    process.kill(zombiePid, 0);
    // ...yet it holds nothing and must read as dead.
    assert.equal(isProcessAlive(zombiePid), false);
  } finally {
    python.kill('SIGKILL');
  }
});
