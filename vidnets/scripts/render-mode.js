/* Render mode: free the machine before a Remotion render starts.
 *
 * On this 7.7 GB VM a render needs ~1.4 GB per Chrome worker, so only one
 * worker fits — and only when nothing else is eating RAM. The test suite,
 * stray Chrome/ffmpeg leftovers and dockerd routinely leave the box with
 * ~128 MB free and swap in use, which makes renders crawl (~23x real-time
 * for the Wright 1-min video: ~40 min of rendering for 103 s of video).
 *
 * enterRenderMode() runs at the start of renderManifest(), BEFORE the
 * first waitForRenderMemory() call, so the freed RAM improves the
 * concurrency plan instead of arriving too late. It kills only processes
 * whose EXECUTED binary is disposable (KILL_BINARIES, matched argv-aware
 * — see isKillable), never anything matching PROTECT_PATTERNS, never
 * itself, its ancestors, or its DESCENDANTS, and never PID 1. The
 * descendant rule is load-bearing: the render's own browser (opened for
 * selectComposition) and its own ffmpeg are children of the render
 * process, and the per-section guard runs AFTER the browser opens — a
 * guard that killed them stalled every section render at 0 frames
 * (GTA VI ef306926, Nov 16, 2026: 11 attempts in a row reached section 1
 * right after a "killed 4 process(es)" guard line and rendered nothing
 * until the 300 s watchdog killed them). Everything else is left
 * alone (default-deny: an unknown process is protected, not killed).
 * A bare kill word buried in a process's arguments (e.g. `grep -rn
 * ffmpeg ...`) never matches: only the executable, a script path under
 * an interpreter, or a launcher command (`npm test`, `node --test`,
 * `python -m pytest`) does.
 *
 * Killed processes are disposable by design: test runners re-run, dockerd
 * is already stopped on this VM, and any Chrome/ffmpeg alive at guard
 * time that is NOT a descendant of the render process is a leftover —
 * the render opens its own browser and spawns its own ffmpeg AFTER the
 * guard runs. The Vidnets server, the daemon, the health monitor, the
 * inbox watcher and the render's own process tree are all protected.
 * One video at a time is the pipeline rule, so no other job's media
 * workers are alive at render start.
 *
 * Opt out with VIDZEO_RENDER_MODE=0. The guard never throws a render:
 * callers wrap it in try/catch and continue without it.
 */
'use strict';

const fs = require('fs');
const { execFile } = require('child_process');
const { promisify } = require('util');

const execFileAsync = promisify(execFile);

// Disposable executables, matched by exact basename (extension stripped),
// never by substring. Substring matching used to kill innocent processes
// that merely MENTIONED a kill word — e.g. `grep -rn ffmpeg ...` or
// `node scripts/check-chromium-version.js` — while missing real
// disposables (rspack, esbuild, `tsc --watch`, `python -m unittest`,
// yt-dlp). Matching is argv-aware (see effectiveCommand/isKillable):
// the executable itself, a script path under an interpreter, or a
// launcher command — never a bare word buried in arguments.
const KILL_BINARIES = new Set([
  // Test runners / bundlers / compilers (re-runnable, disposable).
  'vitest', 'jest', 'jest-worker', 'pytest', 'mocha', 'playwright',
  'rspack', 'webpack', 'esbuild', 'vite', 'tsc',
  // Container runtimes (already stopped on this VM).
  'dockerd', 'containerd',
  // Browsers / media tools left over from an earlier stage or job.
  'chrome-headless-shell', 'headless_shell', 'google-chrome',
  'chromium', 'chromium-browser', 'chrome',
  'ffmpeg', 'ffprobe', 'yt-dlp',
]);
// Backwards-compatible name: the killable executable basenames.
const KILL_PATTERNS = [...KILL_BINARIES];
// Launcher commands (matched against the start of the effective command).
const KILL_LAUNCHERS = [
  /^(npm|pnpm|yarn)\s+(test|run\s+test)\b/,
  /^node\s+--test\b/,
  /^python\d*\s+-m\s+(pytest|unittest)\b/,
  /^(npx|pnpm\s+dlx|yarn\s+dlx)\s+(vitest|jest|playwright|mocha)\b/,
];

// Basenames (argv[0]) that are always protected, plus substrings that
// always protect a process. Protect wins over kill.
const PROTECT_BINARIES = new Set([
  'systemd', 'sshd', 'tmux', 'bash', 'sh', 'dash', 'cron', 'dbus-daemon',
  'agetty', 'systemd-journald', 'systemd-udevd', 'hatch', 'hatch-execd',
]);
const PROTECT_SUBSTRINGS = [
  'muse-agent.js',
  'main.py',
  'VIDNETS_AGENT.sh',
  'render-remotion-manifest.js',
  'render-mode.js',
  'vidnets-inbox-watch',
  'vidnets-health-monitor',
  'muse-inference-spool',
  '/opt/hatch/bin/hatch',
  'hatch daemon',
  'hatch-execd',
];

function readAvailableMB() {
  try {
    const meminfo = fs.readFileSync('/proc/meminfo', 'utf8');
    const match = meminfo.match(/^MemAvailable:\s+(\d+)\s+kB$/m);
    if (match) return Math.round(Number(match[1]) / 1024);
  } catch (_) { /* /proc unavailable (non-Linux): fall through */ }
  try {
    const os = require('os');
    return Math.round(os.freemem() / 1024 / 1024);
  } catch (_) { return -1; }
}

function parsePsOutput(stdout) {
  const processes = [];
  for (const line of String(stdout).split('\n')) {
    const match = line.match(/^\s*(\d+)\s+(\d+)\s+(\d+)\s+(.*)$/);
    if (!match) continue;
    processes.push({
      pid: Number(match[1]),
      ppid: Number(match[2]),
      rssKB: Number(match[3]),
      args: (match[4] || '').trim(),
    });
  }
  return processes;
}

async function listProcesses() {
  const { stdout } = await execFileAsync('ps', ['-eo', 'pid=,ppid=,rss=,args='], { timeout: 15000 });
  return parsePsOutput(stdout);
}

function ancestorPids(processes, selfPid) {
  const byPid = new Map(processes.map((p) => [p.pid, p]));
  const ancestors = new Set();
  let pid = selfPid;
  while (pid && pid !== 1 && byPid.has(pid)) {
    const parent = byPid.get(pid).ppid;
    if (!parent || parent === pid) break;
    ancestors.add(parent);
    pid = parent;
  }
  ancestors.add(1);
  return ancestors;
}

// Processes descended from the render process itself: its browser tree
// (opened for selectComposition, reused for the section renders), its
// ffmpeg, its rspack/bundler workers. The per-section guard runs after
// the browser opens, so without this the guard kills the very browser
// the section render is about to use — and the render then hangs at
// 0 frames instead of erroring (GTA VI ef306926). Orphans from dead
// attempts are NOT descendants (reparented to PID 1 or to a pid outside
// this table), so they are still shed as leftovers.
function descendantPids(processes, selfPid) {
  const byPid = new Map(processes.map((p) => [p.pid, p]));
  const descendants = new Set();
  for (const proc of processes) {
    if (proc.pid === selfPid) continue;
    let pid = proc.ppid;
    while (pid && pid !== 1 && byPid.has(pid)) {
      if (pid === selfPid) { descendants.add(proc.pid); break; }
      pid = byPid.get(pid).ppid;
    }
  }
  return descendants;
}

function processBasename(args) {
  const first = String(args || '').trim().split(/\s+/)[0] || '';
  return first.split('/').pop();
}

function isProtected(proc, selfPid, ancestors, descendants = new Set()) {
  if (proc.pid === 1 || proc.pid === selfPid) return true;
  if (ancestors.has(proc.pid)) return true;
  if (descendants.has(proc.pid)) return true;
  const args = proc.args || '';
  if (args.startsWith('[')) return true; // kernel thread
  // A shell basename protects the shell itself — but not a disposable
  // payload wrapped in it (`bash -c "ffmpeg ..."` IS that ffmpeg).
  // Ancestors and PROTECT_SUBSTRINGS below still win for the real hosts.
  if (PROTECT_BINARIES.has(processBasename(args)) && !isKillable(proc)) return true;
  const lower = args.toLowerCase();
  for (const needle of PROTECT_SUBSTRINGS) {
    if (lower.includes(needle)) return true;
  }
  return false;
}

// Basename of a path token, with a trailing file extension stripped
// ('vitest.mjs' -> 'vitest', 'check-chromium-version.js' -> 'check-chromium-version').
function basenameNoExt(token) {
  const base = String(token || '').split('/').pop() || '';
  return base.replace(/\.[^.]+$/, '');
}

// Strip a leading `sh|bash|dash ... -c <script>` wrapper and return the
// script's first real command. A bare word inside the script text (e.g.
// `grep -rn ffmpeg ...`) is NOT the executed binary, so it must not
// match; the script's first word is (`bash -c "ffmpeg ..."` IS ffmpeg).
// Only the first command is classified: `cd ... && ffmpeg ...` is missed
// by design — misclassifying chained commands is worse than missing one.
function effectiveCommand(args) {
  const text = String(args || '').trim();
  const wrapper = text.match(/^\S*(?:bash|sh|dash)\b(?:\s+--?[a-zA-Z][\w-]*)*\s+-c\s+/);
  if (!wrapper) return text;
  return text.slice(wrapper[0].length).replace(/^["']/, '').trim();
}

function isKillable(proc, protectedMediaSubstrings = ['vidnets-runtime']) {
  const cmd = effectiveCommand(proc.args || '');
  const lower = cmd.toLowerCase();
  // ffmpeg working on job media is spared: at guard time the render has
  // not spawned its own ffmpeg yet, so a vidnets-runtime ffmpeg belongs
  // to media prep or a stale job. Callers may narrow the exemption to
  // the current job's output dir via protectedMediaSubstrings.
  if (lower.includes('ffmpeg')) {
    for (const needle of protectedMediaSubstrings) {
      if (needle && lower.includes(String(needle).toLowerCase())) return false;
    }
  }
  const tokens = cmd.split(/\s+/).filter(Boolean);
  if (!tokens.length) return false;
  // The executed binary itself.
  if (KILL_BINARIES.has(basenameNoExt(tokens[0]).toLowerCase())) return true;
  // A script path under an interpreter: only tokens that LOOK like paths
  // (contain '/') are inspected, component by component. A bare word such
  // as the `ffmpeg` in `grep -rn ffmpeg` is an argument, not a binary.
  for (const token of tokens.slice(0, 3)) {
    if (!token.includes('/')) continue;
    for (const part of token.split('/')) {
      if (KILL_BINARIES.has(basenameNoExt(part).toLowerCase())) return true;
    }
  }
  // Launcher commands: `npm test`, `node --test`, `python -m pytest`, ...
  for (const launcher of KILL_LAUNCHERS) {
    if (launcher.test(lower)) return true;
  }
  return false;
}

// Pure classifier: safe to unit-test without touching the system.
// Returns {kill: [...], protect: [...]}.
// options.protectedMediaSubstrings narrows the ffmpeg exemption from the
// default 'vidnets-runtime' (any job) to e.g. the current job's output
// dir, so a stale ffmpeg from a dead job is no longer spared.
function classifyProcesses(processes, selfPid, options = {}) {
  const ancestors = ancestorPids(processes, selfPid);
  const descendants = descendantPids(processes, selfPid);
  const kill = [];
  const protect = [];
  for (const proc of processes) {
    if (isProtected(proc, selfPid, ancestors, descendants) || !isKillable(proc, options.protectedMediaSubstrings)) {
      protect.push(proc);
    } else {
      kill.push(proc);
    }
  }
  return { kill, protect };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// A zombie still answers kill(pid, 0) until its parent reaps it, but it
// holds no memory. Treat state 'Z' (from /proc/<pid>/stat) as dead so a
// reaped-but-uncollected kill is not reported as a survivor.
function isProcessAlive(pid) {
  try { process.kill(pid, 0); } catch (_) { return false; }
  try {
    const stat = fs.readFileSync(`/proc/${pid}/stat`, 'utf8');
    const state = stat.slice(stat.lastIndexOf(')') + 2).split(/\s+/)[0];
    return state !== 'Z';
  } catch (_) { return true; }
}

async function signalAndWait(pids, signal, waitMs) {
  for (const pid of pids) {
    try { process.kill(pid, signal); } catch (_) { /* already gone */ }
  }
  const deadline = Date.now() + waitMs;
  let remaining = [...pids];
  while (remaining.length && Date.now() < deadline) {
    await sleep(250);
    remaining = remaining.filter((pid) => isProcessAlive(pid));
  }
  return remaining;
}

function dropCaches() {
  try {
    fs.writeFileSync('/proc/sys/vm/drop_caches', '3');
    return true;
  } catch (_) {
    return false;
  }
}

function readSwapUsedMB() {
  try {
    const meminfo = fs.readFileSync('/proc/meminfo', 'utf8');
    const total = meminfo.match(/^SwapTotal:\s+(\d+)\s+kB$/m);
    const free = meminfo.match(/^SwapFree:\s+(\d+)\s+kB$/m);
    if (total && free) return Math.round((Number(total[1]) - Number(free[1])) / 1024);
  } catch (_) { /* /proc unavailable (non-Linux) */ }
  return -1;
}

async function enterRenderMode(options = {}) {
  const dryRun = Boolean(options.dryRun);
  if (process.env.VIDZEO_RENDER_MODE === '0') {
    return { enabled: false, dryRun, killed: [], wouldKill: [], memBeforeMB: readAvailableMB(), memAfterMB: -1, freedMB: 0, dropCaches: false, swapUsedMB: readSwapUsedMB() };
  }
  const memBeforeMB = readAvailableMB();
  const processes = await listProcesses();
  const { kill } = classifyProcesses(processes, process.pid, { protectedMediaSubstrings: options.protectedMediaSubstrings });
  const wouldKill = kill.map((p) => ({ pid: p.pid, rssMB: Math.round(p.rssKB / 1024), args: p.args }));
  if (dryRun) {
    return { enabled: true, dryRun, killed: [], wouldKill, memBeforeMB, memAfterMB: memBeforeMB, freedMB: 0, dropCaches: false, swapUsedMB: readSwapUsedMB() };
  }
  const pids = kill.map((p) => p.pid);
  let survivors = [];
  if (pids.length) {
    survivors = await signalAndWait(pids, 'SIGTERM', 5000);
    if (survivors.length) {
      survivors = await signalAndWait(survivors, 'SIGKILL', 2000);
    }
  }
  const killed = wouldKill.filter((p) => !survivors.includes(p.pid));
  const dropCachesDone = dropCaches();
  const memAfterMB = readAvailableMB();
  return {
    enabled: true,
    dryRun,
    killed,
    wouldKill,
    survivors,
    memBeforeMB,
    memAfterMB,
    freedMB: memAfterMB >= 0 && memBeforeMB >= 0 ? memAfterMB - memBeforeMB : 0,
    dropCaches: dropCachesDone,
    // Swap in use is pressure the guard cannot shed without root
    // (swapoff); report it so the render log shows the thrash risk
    // instead of only the MemAvailable figure.
    swapUsedMB: readSwapUsedMB(),
  };
}

async function main() {
  const mode = (process.argv[2] || 'status').toLowerCase();
  if (mode === 'on') {
    const report = await enterRenderMode();
    console.log(JSON.stringify(report, null, 2));
    return;
  }
  // status / dry-run: show what WOULD be killed, kill nothing.
  const report = await enterRenderMode({ dryRun: true });
  if (!report.enabled) {
    console.log('render mode disabled (VIDZEO_RENDER_MODE=0)');
    return;
  }
  console.log(`available memory: ${report.memBeforeMB} MB`);
  if (report.swapUsedMB > 0) {
    console.log(`swap in use: ${report.swapUsedMB} MB (pressure render-mode cannot shed without root)`);
  }
  if (!report.wouldKill.length) {
    console.log('nothing killable running right now');
    return;
  }
  console.log(`would kill ${report.wouldKill.length} process(es):`);
  for (const p of report.wouldKill) {
    console.log(`  pid ${p.pid} (${p.rssMB} MB) ${p.args.slice(0, 120)}`);
  }
}

if (require.main === module) {
  main().catch((error) => {
    console.error(`[RenderMode] failed: ${error.message}`);
    process.exitCode = 1;
  });
}

module.exports = {
  enterRenderMode,
  classifyProcesses,
  listProcesses,
  readAvailableMB,
  readSwapUsedMB,
  parsePsOutput,
  ancestorPids,
  descendantPids,
  isKillable,
  isProcessAlive,
  effectiveCommand,
  KILL_PATTERNS,
  KILL_BINARIES,
  KILL_LAUNCHERS,
  PROTECT_SUBSTRINGS,
};
