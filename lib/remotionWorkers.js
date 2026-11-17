'use strict';

/**
 * Remotion worker-count resolution (shared by every render path).
 *
 * The user asked for 5 parallel Remotion workers instead of one single
 * worker. The count is configurable, but this VM (4 vCPU, 7.7 GB RAM)
 * cannot always honour a high request: each 1080p Chromium worker costs
 * roughly ~400 MB (Vidnets measures ~392 MB/worker at 1080p), and a live
 * Vidnets job can leave under 1 GB available. So the requested count is
 * capped by CPU and by available memory at render start (auto-degrade),
 * never silently fails, and the chosen count is logged by the caller.
 *
 * Env vars (first set wins; the canonical name is MUSE_RENDER_CONCURRENCY,
 * already used by adapters/vidnets.js — the others are aliases):
 *   MUSE_RENDER_CONCURRENCY | VIDZEO_REMOTION_WORKERS |
 *   MUSE_REMOTION_WORKERS | VIDZEO_REMOTION_CONCURRENCY
 * Optional tuning:
 *   VIDZEO_REMOTION_MEMORY_PER_WORKER_MB (default 400) — measured estimate
 *   VIDZEO_REMOTION_MAX_WORKERS (default 8) — absolute ceiling
 *
 * DEFAULT_WORKERS = 2: the TESTED SAFE value. Nov 7, 2026: 2 parallel
 * 1080p renders finished together in 42s on this VM; 5-way 1080p has NOT
 * been proven safe here (Nov 10: with a live job running, available
 * memory was ~750-915 MB, so the memory cap degrades even a request for
 * 5 down to 1). Requesting 5 is allowed; getting 5 requires ~2.5 GB
 * actually free at render start AND enough CPUs.
 */

const os = require('os');

const DEFAULT_WORKERS = 2;
const MAX_WORKERS = 8;
const DEFAULT_MB_PER_WORKER = 400;
const MIN_AVAILABLE_MB = 384; // Vidnets MIN_REMOTION_AVAILABLE_MB: floor for 1 worker

function parsePositiveInt(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : fallback;
}

/** Requested worker count from env only (no system caps). */
function readRequestedWorkers(env = process.env) {
  const raw = env.MUSE_RENDER_CONCURRENCY
    ?? env.VIDZEO_REMOTION_WORKERS
    ?? env.MUSE_REMOTION_WORKERS
    ?? env.VIDZEO_REMOTION_CONCURRENCY;
  if (raw === undefined || raw === null || String(raw).trim() === '') return DEFAULT_WORKERS;
  return Math.max(1, Math.min(MAX_WORKERS, parsePositiveInt(raw, DEFAULT_WORKERS)));
}

/**
 * Resolve the worker count for a render starting now.
 * `sys` exists for tests: { totalMb, availableMb, cpuCount }.
 * Returns { requested, chosen, cpuLimit, memoryLimit, availableMb, totalMb,
 *   estimatedWorkerMb, reserveMb, degraded, reason }.
 * `chosen` is always >= 1: below the 384 MB floor the single worker still
 * proceeds (Vidnets single-worker fallback) rather than failing the render
 * outright — the caller logs the degraded reason.
 */
function resolveRemotionWorkers(env = process.env, sys = {}) {
  const totalMb = Math.max(1, Math.floor(sys.totalMb ?? os.totalmem() / (1024 * 1024)));
  const availableMb = Math.max(0, Math.floor(sys.availableMb ?? os.freemem() / (1024 * 1024)));
  const cpuCount = Math.max(1, Math.floor(sys.cpuCount ?? os.availableParallelism?.() ?? os.cpus().length ?? 1));
  const maxWorkers = Math.max(1, Math.min(MAX_WORKERS,
    parsePositiveInt(env.VIDZEO_REMOTION_MAX_WORKERS, MAX_WORKERS)));
  const requested = Math.max(1, Math.min(maxWorkers, readRequestedWorkers(env)));
  const estimatedWorkerMb = Math.max(1,
    parsePositiveInt(env.VIDZEO_REMOTION_MEMORY_PER_WORKER_MB, DEFAULT_MB_PER_WORKER));
  // Reserve for the OS, Vidnets/Flask, Node and the browser — mirrors
  // vidnets/scripts/render-remotion-manifest.js (7% of RAM, 384 MB–2 GB).
  const reserveMb = Math.min(2048, Math.max(MIN_AVAILABLE_MB, Math.floor(totalMb * 0.07)));
  const cpuLimit = Math.max(1, Math.min(maxWorkers, cpuCount > 2 ? cpuCount - 1 : cpuCount));
  const memoryLimit = availableMb >= MIN_AVAILABLE_MB
    ? Math.max(0, Math.floor((availableMb - reserveMb) / estimatedWorkerMb))
    : 0;

  let chosen = Math.max(1, Math.min(requested, cpuLimit, Math.max(1, memoryLimit)));
  let degraded = false;
  let reason = '';
  if (chosen < requested) {
    degraded = true;
    if (memoryLimit < requested && memoryLimit <= cpuLimit) {
      reason = `memory: ${availableMb} MB available, ${reserveMb} MB reserve, ~${estimatedWorkerMb} MB/worker`
        + ` allows ${Math.max(1, memoryLimit)} worker(s); requested ${requested}`;
    } else {
      reason = `cpu: ${cpuCount} vCPU allows ${cpuLimit} worker(s); requested ${requested}`;
    }
    if (memoryLimit < 1) {
      reason = `low-memory single-worker fallback: ${availableMb} MB available < ${MIN_AVAILABLE_MB} MB floor;`
        + ` proceeding with 1 worker instead of ${requested}`;
    }
  }
  return {
    requested, chosen, cpuLimit, memoryLimit,
    availableMb, totalMb, estimatedWorkerMb, reserveMb,
    degraded, reason,
  };
}

module.exports = {
  resolveRemotionWorkers,
  readRequestedWorkers,
  DEFAULT_WORKERS,
  MAX_WORKERS,
  DEFAULT_MB_PER_WORKER,
  MIN_AVAILABLE_MB,
};
