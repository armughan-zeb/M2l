'use strict';

const dns = require('dns');

/**
 * Determine if an error is caused by a network disconnection, socket failure,
 * DNS failure, or fetch abort/drop.
 *
 * @param {Error|any} err
 * @returns {boolean}
 */
function isNetworkError(err) {
  if (!err) return false;
  const msg = String(err && err.message || err).toLowerCase();
  const code = String(err.code || err.cause?.code || '').toLowerCase();
  const name = String(err.name || '').toLowerCase();

  return (
    code === 'enotfound' ||
    code === 'eai_again' ||
    code === 'econnrefused' ||
    code === 'econnreset' ||
    code === 'etimedout' ||
    code === 'und_err_connect_timeout' ||
    code === 'und_err_socket' ||
    code === 'socket_error' ||
    code === 'enetunreach' ||
    code === 'ehostunreach' ||
    code === 'enetdown' ||
    code === 'ehostdown' ||
    code === 'epipe' ||
    code === 'err_internet_disconnected' ||
    code === 'err_network_changed' ||
    code === 'err_connection_timed_out' ||
    code === 'err_connection_reset' ||
    code === 'err_name_not_resolved' ||
    name === 'networkerror' ||
    name === 'clientnetworkerror' ||
    msg.includes('fetch failed') ||
    msg.includes('getaddrinfo') ||
    msg.includes('network error') ||
    msg.includes('network is down') ||
    msg.includes('network unreachable') ||
    msg.includes('network request failed') ||
    msg.includes('enotfound') ||
    msg.includes('eai_again') ||
    msg.includes('econnrefused') ||
    msg.includes('econnreset') ||
    msg.includes('socket hang up') ||
    msg.includes('internet disconnected') ||
    msg.includes('not connected to internet') ||
    msg.includes('connection reset') ||
    msg.includes('premature close') ||
    msg.includes('connect timeout') ||
    msg.includes('dns lookup failed') ||
    msg.includes('dns_probe_finished_no_internet') ||
    msg.includes('net::err_') ||
    msg.includes('err_internet_disconnected') ||
    msg.includes('err_network_changed') ||
    msg.includes('connection refused') ||
    msg.includes('connection closed') ||
    msg.includes('temporary failure in name resolution')
  );
}

/**
 * Checks whether internet connectivity is currently active by probing
 * reliable global endpoints.
 *
 * @param {number} timeoutMs
 * @returns {Promise<boolean>}
 */
async function isInternetReachable(timeoutMs = 3500) {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), Math.max(500, timeoutMs));

    // Try resolving or pinging high-availability hosts
    const probe = await Promise.any([
      dns.promises.lookup('cloudflare.com').then(() => true),
      dns.promises.lookup('google.com').then(() => true),
      dns.promises.lookup('openrouter.ai').then(() => true),
      fetch('https://1.1.1.1', { method: 'HEAD', signal: controller.signal }).then(() => true),
      fetch('https://8.8.8.8', { method: 'HEAD', signal: controller.signal }).then(() => true)
    ]).finally(() => clearTimeout(timer));

    return Boolean(probe);
  } catch {
    return false;
  }
}

/**
 * Gracefully waits for internet connectivity to be restored.
 * Polls the network at regular intervals and resolves when back online.
 *
 * @param {object} options
 * @param {AbortSignal} [options.signal]
 * @param {Function} [options.onWaiting]
 * @param {Function} [options.onRestored]
 * @param {number} [options.pollIntervalMs]
 * @param {number} [options.maxWaitMs]
 * @returns {Promise<boolean>}
 */
async function waitForInternetConnectivity({
  signal,
  onWaiting,
  onRestored,
  pollIntervalMs = 3000,
  maxWaitMs = 86400000
} = {}) {
  // If already online, return immediately
  const initiallyOnline = await isInternetReachable(2000);
  if (initiallyOnline) return true;

  if (typeof onWaiting === 'function') {
    onWaiting('Internet connection lost. Waiting for network connectivity to restore before continuing…');
  }

  const start = Date.now();
  while (Date.now() - start < maxWaitMs) {
    if (signal?.aborted) throw new Error('Wait for internet cancelled by operator');
    await new Promise(resolve => setTimeout(resolve, pollIntervalMs));
    if (signal?.aborted) throw new Error('Wait for internet cancelled by operator');

    const online = await isInternetReachable(2500);
    if (online) {
      if (typeof onRestored === 'function') {
        onRestored('Internet connection restored. Resuming immediately…');
      }
      return true;
    }
  }

  return false;
}

/**
 * Execute a network operation with automatic internet wait & resume.
 *
 * @param {Function} fn
 * @param {object} options
 * @returns {Promise<any>}
 */
async function withInternetResilience(fn, { signal, onWaiting, onRestored, maxRetries = 3 } = {}) {
  let attempts = 0;
  while (true) {
    attempts++;
    try {
      if (signal?.aborted) throw new Error('Operation cancelled by operator');
      return await fn();
    } catch (err) {
      if (signal?.aborted) throw err;
      const isNet = isNetworkError(err);
      const isOffline = isNet || !(await isInternetReachable(1500));

      if (isOffline) {
        await waitForInternetConnectivity({ signal, onWaiting, onRestored });
        // After connectivity restores, retry immediately without exhausting attempts
        continue;
      }

      if (attempts >= maxRetries) throw err;
      throw err;
    }
  }
}

module.exports = {
  isNetworkError,
  isInternetReachable,
  waitForInternetConnectivity,
  withInternetResilience
};
