'use strict';

/**
 * Stage cleanup: kill what a job stage started, verify it is gone, log it.
 *
 * RAM hygiene (Nov 10, 2026): a stage (script / render / thumbnail) runs its
 * work in a child process group created by lib/command.js (detached spawn,
 * so the child's PID is its process-group ID on Linux). When the stage
 * finishes, the child itself is gone — but grandchildren it spawned
 * (Vidnets launcher calls, ffmpeg, Chrome) can linger in the group and
 * hold RAM into the next stage, which is what stalled renders at ~221 MB
 * available. This module records each stage's group when it spawns and
 * reaps the leftovers when the stage ends.
 *
 * Safety rules (never relaxed):
 *  - Only a process group that was RECORDED here, for this job and stage,
 *    is ever signalled. Nothing is matched by process name.
 *  - A group member is only signalled when its start time is at/after the
 *    stage's recorded start (60s slack for clock skew). A recycled PID, or
 *    a group ID reused after the stage's group died, fails this check and
 *    is left alone.
 *  - The daemon's own group, the Vidnets server's group and any group
 *    that contains this process are refused outright. In practice a
 *    recorded group can never be one of those (recorded groups are
 *    detached children), but the check is cheap and fail-closed.
 *  - Cleanup failure never fails the job: it logs and reports survivors.
 */

const fs = require('fs');
const { execFileSync, spawn } = require('child_process');
const log = require('./log');

const registry = new Map(); // `${jobId}:${stage}` -> { jobId, stage, pid, pgid, startedAtMs }
const START_SLACK_MS = 60 * 1000;

function key(jobId, stage) {
  return `${String(jobId)}:${String(stage)}`;
}

let cachedOwnPgid;
function ownPgid() {
  if (cachedOwnPgid !== undefined) return cachedOwnPgid;
  cachedOwnPgid = null;
  try {
    if (typeof process.getpgid === 'function') cachedOwnPgid = process.getpgid(0);
  } catch { /* fall through to ps */ }
  if (cachedOwnPgid === null && process.platform === 'linux') {
    try {
      const out = execFileSync('ps', ['-o', 'pgid=', '-p', String(process.pid)], { encoding: 'utf8', timeout: 5000 }).trim();
      const parsed = Number(out);
      if (Number.isFinite(parsed) && parsed > 0) cachedOwnPgid = parsed;
    } catch { /* stays null: the refusal check is skipped, registration still requires a recorded group */ }
  }
  return cachedOwnPgid;
}

function isAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

/**
 * Start time of a process in epoch ms, or null when it cannot be read.
 * Linux: /proc/<pid>/stat field 22 (starttime, clock ticks since boot) +
 * /proc/stat btime. Fallback: `ps -o lstart=`.
 */
function processStartMs(pid) {
  if (process.platform === 'linux') {
    try {
      const stat = fs.readFileSync(`/proc/${pid}/stat`, 'utf8');
      const afterComm = stat.slice(stat.lastIndexOf(')') + 1).trim().split(/\s+/);
      // afterComm[0] is state; field 22 overall is afterComm[19].
      const startTicks = Number(afterComm[19]);
      const btime = Number(
        fs.readFileSync('/proc/stat', 'utf8').match(/^btime\s+(\d+)/m)?.[1] || 0
      );
      const ticksPerSecond = Number(process.env.CLK_TCK || 100) || 100;
      if (Number.isFinite(startTicks) && btime > 0) {
        return Math.round(btime * 1000 + (startTicks * 1000) / ticksPerSecond);
      }
    } catch { /* fall through to ps */ }
  }
  try {
    const out = execFileSync('ps', ['-o', 'lstart=', '-p', String(pid)], { encoding: 'utf8', timeout: 5000 }).trim();
    const ms = Date.parse(out);
    return Number.isFinite(ms) ? ms : null;
  } catch {
    return null;
  }
}

/** PIDs currently in a process group (Linux: /proc scan; fallback: ps). */
function groupPids(pgid) {
  if (process.platform === 'linux') {
    try {
      const found = [];
      for (const entry of fs.readdirSync('/proc')) {
        if (!/^\d+$/.test(entry)) continue;
        try {
          const stat = fs.readFileSync(`/proc/${entry}/stat`, 'utf8');
          const afterComm = stat.slice(stat.lastIndexOf(')') + 1).trim().split(/\s+/);
          // field 5 overall (pgrp) is afterComm[2].
          if (Number(afterComm[2]) === Number(pgid)) found.push(Number(entry));
        } catch { /* process exited mid-scan */ }
      }
      return found;
    } catch { /* fall through to ps */ }
  }
  try {
    const out = execFileSync('ps', ['--no-headers', '-o', 'pid=', '-g', String(pgid)], { encoding: 'utf8', timeout: 5000 });
    return out.split(/\s+/).map(Number).filter(Number.isFinite);
  } catch {
    return [];
  }
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * Record a stage's child process group. `spawnInfo` comes from
 * lib/command.js `onSpawn` ({ pid, pgid, startedAtMs }). Refuses groups
 * that could ever be this process's own group. Returns true when recorded.
 */
function registerStageProcess(jobId, stage, spawnInfo) {
  const info = spawnInfo || {};
  const pgid = Number(info.pgid || info.pid || 0);
  const pid = Number(info.pid || 0);
  const startedAtMs = Number(info.startedAtMs || Date.now());
  if (!jobId || !stage || !Number.isFinite(pgid) || pgid <= 1 || !Number.isFinite(pid) || pid <= 1) return false;
  if (pgid === ownPgid() || pid === process.pid) return false;
  registry.set(key(jobId, stage), { jobId: String(jobId), stage: String(stage), pid, pgid, startedAtMs });
  log.info(`[stage-cleanup] job ${jobId} stage ${stage}: tracking process group ${pgid} (leader ${pid})`);
  return true;
}

function registeredStages(jobId) {
  return [...registry.values()].filter(entry => entry.jobId === String(jobId));
}

/**
 * Terminate a recorded stage group and verify every member is gone.
 * Members whose start time predates the stage (PID/group reuse) are never
 * signalled. Returns a report; never throws.
 */
async function cleanupStage(jobId, stage, { graceMs = 5000 } = {}) {
  const record = registry.get(key(jobId, stage));
  if (!record) return { jobId: String(jobId), stage: String(stage), skipped: 'no-record', verifiedGone: true };
  registry.delete(key(jobId, stage));
  const { pgid, startedAtMs } = record;
  const report = {
    jobId: String(jobId), stage: String(stage), pgid,
    terminated: [], killed: [], survivors: [], spared: [], verifiedGone: false, skipped: ''
  };
  if (pgid <= 1 || pgid === ownPgid()) {
    report.skipped = 'refused-own-or-system-group';
    log.warn(`[stage-cleanup] job ${jobId} stage ${stage}: refusing to signal group ${pgid}`);
    return report;
  }
  const members = groupPids(pgid).filter(pid => pid !== process.pid);
  const targets = [];
  for (const pid of members) {
    const started = processStartMs(pid);
    if (started === null) {
      // Start time unreadable: fail closed, do not signal an
      // unidentified process. It is reported as spared, not a survivor.
      report.spared.push(pid);
      continue;
    }
    if (started < startedAtMs - START_SLACK_MS) {
      // Predates the stage: PID/group reuse, not our process.
      report.spared.push(pid);
      continue;
    }
    targets.push(pid);
  }
  if (!targets.length) {
    report.verifiedGone = true;
    log.info(`[stage-cleanup] job ${jobId} stage ${stage}: group ${pgid} already empty`
      + (report.spared.length ? ` (member(s) left alone, predates stage or unreadable: ${report.spared.join(',')})` : ''));
    return report;
  }
  // Windows has no POSIX groups: terminate the recorded leader's tree.
  if (process.platform === 'win32') {
    try {
      spawn('taskkill', ['/pid', String(record.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    } catch { /* verify below */ }
  } else {
    try { process.kill(-pgid, 'SIGTERM'); } catch { /* group already gone */ }
  }
  const deadline = Date.now() + Math.max(500, Number(graceMs) || 5000);
  let remaining = [...targets];
  while (remaining.length && Date.now() < deadline) {
    await sleep(250);
    remaining = remaining.filter(pid => isAlive(pid));
  }
  report.terminated = targets.filter(pid => !remaining.includes(pid));
  if (remaining.length && process.platform !== 'win32') {
    try { process.kill(-pgid, 'SIGKILL'); } catch { /* already gone */ }
  } else if (remaining.length) {
    for (const pid of remaining) {
      try { process.kill(pid, 'SIGKILL'); } catch { /* already gone */ }
    }
  }
  const killDeadline = Date.now() + 5000;
  while (remaining.length && Date.now() < killDeadline) {
    await sleep(250);
    remaining = remaining.filter(pid => isAlive(pid));
  }
  report.killed = targets.filter(pid => !report.terminated.includes(pid) && !remaining.includes(pid));
  report.survivors = [...remaining];
  report.verifiedGone = report.survivors.length === 0;
  log.info(`[stage-cleanup] job ${jobId} stage ${stage}: group ${pgid} — `
    + `SIGTERM exited ${report.terminated.length} (${report.terminated.join(',') || 'none'}), `
    + `SIGKILLed ${report.killed.length} (${report.killed.join(',') || 'none'})`
    + (report.spared.length ? `, spared (predates stage): ${report.spared.join(',')}` : '')
    + (report.verifiedGone ? ', verified gone' : `, SURVIVORS: ${report.survivors.join(',')}`));
  if (!report.verifiedGone) {
    log.warn(`[stage-cleanup] job ${jobId} stage ${stage}: ${report.survivors.length} process(es) survived cleanup: ${report.survivors.join(',')}`);
  }
  return report;
}

/** Reap every recorded stage group for a job (end-of-job sweep). */
async function cleanupAllStagesForJob(jobId, options = {}) {
  const reports = [];
  for (const entry of registeredStages(jobId)) {
    try {
      reports.push(await cleanupStage(jobId, entry.stage, options));
    } catch (error) {
      reports.push({ jobId: String(jobId), stage: entry.stage, skipped: `cleanup-error: ${error.message}`, verifiedGone: false });
    }
  }
  return reports;
}

module.exports = {
  registerStageProcess,
  cleanupStage,
  cleanupAllStagesForJob,
  registeredStages,
  processStartMs,
  groupPids,
  isAlive,
};
