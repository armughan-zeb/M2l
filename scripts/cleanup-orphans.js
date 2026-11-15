#!/usr/bin/env node
'use strict';

/**
 * cleanup-orphans.js — kill orphaned Vidzeo job processes, safely.
 *
 * RAM hygiene (Nov 10, 2026): media/render workers (ffmpeg, Chrome,
 * yt-dlp, Vidnets search workers) are sometimes reparented to PID 1 when
 * their intermediate parent exits, so "kill the stage's process group"
 * misses them and they hold RAM into the next job. This guard finds them
 * and reaps them — but it is SAFE to run while a job is live, because it
 * only ever touches a process that is provably NOT owned by a live job:
 *
 *  Candidate kinds only (ffmpeg/ffprobe/yt-dlp/Chrome, plus node/python
 *  processes whose cmdline is a Vidnets render/search worker). Nothing
 *  else is ever considered — never the daemon, never Vidnets main.py,
 *  never the CMS worker, never a user app.
 *
 *  A candidate is an orphan only when ALL of these hold:
 *   1. its parent is dead (PPID 1, or the parent PID no longer exists), AND
 *   2. its cmdline contains NO live job id (see live-job discovery below), AND
 *   3. either its cmdline names a job id that is NOT live (stale job), or
 *      it names no job at all and it is older than --min-age-minutes
 *      (default 30) — young unattributed processes are left alone.
 *  A process whose age cannot be read is left alone (fail closed).
 *
 * Live-job discovery (in order): --live-job-id flags, MUSE_LIVE_JOB_IDS
 * env (comma-separated), the daemon's active-job.json (work root), and the
 * working directories / saved vidnets-job.json of any running
 * render-vidnets-linux.js / muse-agent.js process. The live BNSF ffmpeg
 * download is the proof case: it is reparented to PID 1, but its cmdline
 * carries /jobs/<live-vidnets-job-id>/, so it is SPARED.
 *
 * DRY-RUN IS THE DEFAULT. Pass --kill to actually signal anything, and
 * --kill refuses to run with no known live job unless --allow-no-live-job
 * is also passed. Kill order is SIGTERM, 5s grace, SIGKILL, verify.
 *
 * Usage:
 *   node scripts/cleanup-orphans.js [--live-job-id ID ...] [--live-pid PID ...]
 *     [--min-age-minutes N] [--work-root DIR] [--kill] [--allow-no-live-job] [--json]
 * Exit: 0 clean (nothing to kill, or all killed), 1 survivors remain
 * after --kill, 2 usage/refusal error.
 */

const fs = require('fs');
const path = require('path');
const { readlinkSync } = require('fs');
const { execFileSync: execFile } = require('child_process');

const REPO_ROOT = path.resolve(__dirname, '..');
const DEFAULT_WORK_ROOT = process.env.MUSE_WORK_DIR
  ? path.resolve(process.env.MUSE_WORK_DIR)
  : path.join(REPO_ROOT, 'work');

// Bare executable names that are job workers on their own.
const WORKER_COMMANDS = new Set([
  'ffmpeg', 'ffprobe', 'yt-dlp', 'yt_dlp',
  'chrome', 'chromium', 'google-chrome', 'chromium-browser',
  'chrome-headless-shell', 'headless_shell',
]);
// Generic interpreters count only when the cmdline proves a worker role.
const INTERPRETER_COMMANDS = new Set(['node', 'node.exe', 'python', 'python3', 'python.exe', 'pythonw.exe']);
const INTERPRETER_TOKENS = [
  'render-vidnets', 'remotion', 'vidnets', 'VIDNETS_AGENT',
  'headless_agent', 'research_search_worker', 'youtube_pipeline',
  'youtube_pool', 'yt-dlp',
];
// Processes that are infrastructure, never candidates, even by name.
const PROTECTED_CMDLINE = ['muse-agent.js', 'vidnets/main.py', 'vidnets\\main.py'];

function baseName(cmd) {
  return String(cmd || '').split(/[\\/]/).pop().toLowerCase();
}

/** Job ids named in a cmdline: /jobs/<id>/, /work/<id>/, jobId=<id>. */
function extractJobTokens(cmdline) {
  const tokens = new Set();
  const text = String(cmdline || '');
  for (const match of text.matchAll(/(?:\/jobs?\/|\/work\/)([A-Za-z0-9][A-Za-z0-9_-]{5,})/g)) tokens.add(match[1]);
  for (const match of text.matchAll(/job[_-]?id[=:]([A-Za-z0-9][A-Za-z0-9_-]{5,})/g)) tokens.add(match[1]);
  return tokens;
}

function isCandidate(row) {
  const cmd = baseName((String(row.args || '').trim().split(/\s+/)[0] || row.comm || ''));
  const args = String(row.args || '');
  const comm = String(row.comm || '').toLowerCase();
  if (PROTECTED_CMDLINE.some(token => args.includes(token))) return false;
  if (WORKER_COMMANDS.has(cmd) || WORKER_COMMANDS.has(comm)) return true;
  if (INTERPRETER_COMMANDS.has(cmd) || INTERPRETER_COMMANDS.has(comm)) {
    return INTERPRETER_TOKENS.some(token => args.includes(token));
  }
  return false;
}

/**
 * Pure decision: 'live' | 'orphan' | 'leave', with a reason. `liveJobIds`
 * is a Set of known-live CMS + Vidnets job ids. `pids` is the set of all
 * live PIDs (for the parent-exists check).
 */
function classifyProcess(row, { liveJobIds, minAgeSeconds = 1800, pids = new Set() }) {
  if (!isCandidate(row)) return { verdict: 'leave', reason: 'not-a-job-worker' };
  const tokens = extractJobTokens(row.args);
  for (const token of tokens) {
    if (liveJobIds.has(token)) return { verdict: 'live', reason: `live-job-id:${token}` };
  }
  const parentDead = Number(row.ppid) === 1 || (Number(row.ppid) > 1 && !pids.has(Number(row.ppid)));
  if (!parentDead) return { verdict: 'leave', reason: 'parent-alive' };
  const age = Number(row.etimes);
  if (!Number.isFinite(age)) return { verdict: 'leave', reason: 'age-unknown' };
  if (tokens.size > 0) return { verdict: 'orphan', reason: `stale-job:${[...tokens].join(',')}` };
  if (age >= minAgeSeconds) return { verdict: 'orphan', reason: `unattributed-age-${age}s` };
  return { verdict: 'leave', reason: `young-unattributed-${age}s` };
}

function listProcesses() {
  try {
    const out = execFile('ps', ['-eo', 'pid,ppid,pgid,etimes,rss,comm,args'], { encoding: 'utf8', timeout: 15000 });
    const rows = [];
    for (const line of out.split('\n').slice(1)) {
      if (!line.trim()) continue;
      // pid ppid pgid etimes rss comm args...
      const match = line.trim().match(/^(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s?(.*)$/);
      if (!match) continue;
      rows.push({
        pid: Number(match[1]), ppid: Number(match[2]), pgid: Number(match[3]),
        etimes: Number(match[4]), rssKb: Number(match[5]), comm: match[6], args: match[7] || '',
      });
    }
    return rows;
  } catch {
    return [];
  }
}

function readJsonFile(file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; }
}

function processCwd(pid) {
  try { return readlinkSync(`/proc/${pid}/cwd`); } catch { return ''; }
}

/** Live CMS + Vidnets job ids from flags, env, active-job.json, live wrappers. */
function discoverLiveJobIds({ extraIds = [], workRoot = DEFAULT_WORK_ROOT, processes = null } = {}) {
  const ids = new Set(extraIds.map(String).filter(Boolean));
  for (const token of String(process.env.MUSE_LIVE_JOB_IDS || '').split(',')) {
    if (token.trim()) ids.add(token.trim());
  }
  const active = readJsonFile(path.join(workRoot, 'active-job.json'));
  if (active && active.jobId) ids.add(String(active.jobId));
  const rows = processes || listProcesses();
  for (const row of rows) {
    const args = String(row.args || '');
    if (!args.includes('render-vidnets-linux.js') && !args.includes('muse-agent.js')) continue;
    const cwd = processCwd(row.pid);
    const workMatch = cwd.match(/\/work\/([A-Za-z0-9][A-Za-z0-9_-]{5,})\/?$/);
    if (workMatch) ids.add(workMatch[1]);
    const saved = readJsonFile(path.join(cwd, 'vidnets-job.json'));
    if (saved && saved.id) ids.add(String(saved.id));
    if (saved && saved.jobId) ids.add(String(saved.jobId));
  }
  return ids;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function pidAlive(pid) {
  try { process.kill(pid, 0); return true; } catch { return false; }
}

async function killOrphans(orphans) {
  const killed = [];
  const survivors = [];
  if (!orphans.length) return { killed, survivors };
  for (const row of orphans) {
    try { process.kill(row.pid, 'SIGTERM'); } catch { /* already gone */ }
  }
  const deadline = Date.now() + 5000;
  let remaining = orphans.filter(row => pidAlive(row.pid));
  while (remaining.length && Date.now() < deadline) {
    await sleep(250);
    remaining = remaining.filter(row => pidAlive(row.pid));
  }
  for (const row of remaining) {
    try { process.kill(row.pid, 'SIGKILL'); } catch { /* already gone */ }
  }
  const killDeadline = Date.now() + 5000;
  while (remaining.length && Date.now() < killDeadline) {
    await sleep(250);
    remaining = remaining.filter(row => pidAlive(row.pid));
  }
  for (const row of orphans) {
    if (pidAlive(row.pid)) survivors.push(row);
    else killed.push(row);
  }
  return { killed, survivors };
}

function parseArgs(argv) {
  const opts = { liveJobIds: [], livePids: [], minAgeMinutes: 30, kill: false, allowNoLiveJob: false, json: false, workRoot: DEFAULT_WORK_ROOT };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--live-job-id') opts.liveJobIds.push(argv[++i]);
    else if (arg.startsWith('--live-job-id=')) opts.liveJobIds.push(arg.slice('--live-job-id='.length));
    else if (arg === '--live-pid') opts.livePids.push(Number(argv[++i]));
    else if (arg.startsWith('--live-pid=')) opts.livePids.push(Number(arg.slice('--live-pid='.length)));
    else if (arg === '--min-age-minutes') opts.minAgeMinutes = Number(argv[++i]);
    else if (arg.startsWith('--min-age-minutes=')) opts.minAgeMinutes = Number(arg.slice('--min-age-minutes='.length));
    else if (arg === '--work-root') opts.workRoot = path.resolve(argv[++i]);
    else if (arg.startsWith('--work-root=')) opts.workRoot = path.resolve(arg.slice('--work-root='.length));
    else if (arg === '--kill') opts.kill = true;
    else if (arg === '--allow-no-live-job') opts.allowNoLiveJob = true;
    else if (arg === '--json') opts.json = true;
    else if (arg === '--help' || arg === '-h') { opts.help = true; }
    else throw new Error(`Unknown argument: ${arg}`);
  }
  return opts;
}

async function main(argv) {
  const opts = parseArgs(argv);
  if (opts.help) {
    process.stdout.write('Usage: node scripts/cleanup-orphans.js [--live-job-id ID ...] [--live-pid PID ...] [--min-age-minutes N] [--work-root DIR] [--kill] [--allow-no-live-job] [--json]\nDry-run is the default; --kill actually signals orphans.\n');
    return 0;
  }
  if (process.platform !== 'linux') {
    process.stderr.write('[cleanup-orphans] Only Linux is supported (uses ps + /proc).\n');
    return 2;
  }
  const processes = listProcesses();
  if (!processes.length) {
    process.stderr.write('[cleanup-orphans] Could not list processes.\n');
    return 2;
  }
  const pids = new Set(processes.map(row => row.pid));
  const liveJobIds = discoverLiveJobIds({ extraIds: opts.liveJobIds, workRoot: opts.workRoot, processes });
  const protectedPids = new Set([...opts.livePids.filter(Number.isFinite), process.pid, 1]);
  for (const row of processes) {
    if (PROTECTED_CMDLINE.some(token => String(row.args || '').includes(token))) protectedPids.add(row.pid);
  }
  const minAgeSeconds = Math.max(0, Math.floor(Number(opts.minAgeMinutes) || 0) * 60);
  const classified = processes.map(row => ({ row, result: classifyProcess(row, { liveJobIds, minAgeSeconds, pids }) }));
  const orphans = classified
    .filter(({ row, result }) => result.verdict === 'orphan' && !protectedPids.has(row.pid))
    .map(({ row, result }) => ({ ...row, reason: result.reason }));
  const sparedLive = classified.filter(({ result }) => result.verdict === 'live');

  if (opts.kill && liveJobIds.size === 0 && !opts.allowNoLiveJob) {
    process.stderr.write('[cleanup-orphans] REFUSED --kill with no known live job (no --live-job-id, no MUSE_LIVE_JOB_IDS, no active-job.json, no live wrapper found). Pass --allow-no-live-job to override.\n');
    return 2;
  }

  const report = {
    dryRun: !opts.kill,
    liveJobIds: [...liveJobIds].sort(),
    liveWorkersSpared: sparedLive.length,
    orphans: orphans.map(row => ({ pid: row.pid, ppid: row.ppid, ageSeconds: row.etimes, rssMb: Math.round(row.rssKb / 1024), reason: row.reason, cmd: String(row.args || row.comm).slice(0, 200) })),
    killed: [],
    survivors: [],
  };

  if (opts.kill && orphans.length) {
    const { killed, survivors } = await killOrphans(orphans);
    report.killed = killed.map(row => row.pid);
    report.survivors = survivors.map(row => row.pid);
  }

  if (opts.json) {
    process.stdout.write(JSON.stringify(report, null, 2) + '\n');
  } else {
    process.stdout.write(`[cleanup-orphans] ${opts.kill ? 'KILL' : 'DRY-RUN'} — live jobs: ${report.liveJobIds.join(', ') || '(none known)'}; live workers spared: ${report.liveWorkersSpared}\n`);
    if (!report.orphans.length) {
      process.stdout.write('[cleanup-orphans] No orphans found.\n');
    } else {
      for (const orphan of report.orphans) {
        process.stdout.write(`[cleanup-orphans] ${orphan.reason} pid=${orphan.pid} ppid=${orphan.ppid} age=${orphan.ageSeconds}s rss=${orphan.rssMb}MB :: ${orphan.cmd}\n`);
      }
      if (opts.kill) {
        process.stdout.write(`[cleanup-orphans] Killed: ${report.killed.join(', ') || 'none'}; survivors: ${report.survivors.join(', ') || 'none'}\n`);
      } else {
        process.stdout.write(`[cleanup-orphans] Dry run: would kill ${report.orphans.length} process(es). Re-run with --kill to signal them.\n`);
      }
    }
  }
  if (opts.kill && report.survivors.length) return 1;
  return 0;
}

if (require.main === module) {
  main(process.argv.slice(2))
    .then(code => process.exit(code))
    .catch(error => { process.stderr.write(`[cleanup-orphans] ${error.message}\n`); process.exit(2); });
}

module.exports = { classifyProcess, extractJobTokens, isCandidate, discoverLiveJobIds, listProcesses };
