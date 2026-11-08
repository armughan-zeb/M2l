'use strict';

const { spawn } = require('child_process');

const MAX_STDOUT_BYTES = 8 * 1024 * 1024;
const MAX_STDERR_BYTES = 2 * 1024 * 1024;

function quoteArg(value) {
  const text = String(value);
  if (!text) return '""';
  if (process.platform === 'win32') {
    if (!/[\s"&|<>^]/.test(text)) return text;
    return '"' + text.replace(/"/g, '""') + '"';
  }
  if (!/[\s"'$`\\!*?[\]{}()<>|&;#~]/.test(text)) return text;
  return "'" + text.replace(/'/g, "'\\''") + "'";
}

function commandLine(executable, args = []) {
  return [executable, ...args].map(quoteArg).join(' ');
}

function stopTree(child) {
  if (!child || child.killed) return;
  try {
    if (process.platform === 'win32' && child.pid) {
      spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    } else if (child.pid) {
      try {
        process.kill(-child.pid, 'SIGTERM');
      } catch {
        try { child.kill('SIGTERM'); } catch { /* already gone */ }
      }
      try {
        spawn('pkill', ['-TERM', '-P', String(child.pid)], { stdio: 'ignore' });
      } catch { /* best effort */ }
      setTimeout(() => {
        try { process.kill(-child.pid, 'SIGKILL'); } catch {
          try { child.kill('SIGKILL'); } catch { /* already gone */ }
        }
        try {
          spawn('pkill', ['-KILL', '-P', String(child.pid)], { stdio: 'ignore' });
        } catch { /* best effort */ }
      }, 5000).unref?.();
    }
  } catch { /* best effort */ }
}

function runCommand(command, { input, env, cwd, timeoutMs = 30 * 60 * 1000, signal, label = 'command' } = {}) {
  return new Promise((resolve, reject) => {
    let settled = false;
    let stdout = '';
    let stderr = '';
    let stdoutBytes = 0;
    let stderrBytes = 0;
    let child;
    try {
      child = spawn(command, {
        shell: true,
        cwd,
        env: { ...process.env, ...(env || {}) },
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
        detached: process.platform !== 'win32'
      });
    } catch (error) {
      reject(new Error(`Could not start ${label}: ${error.message}`));
      return;
    }

    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener?.('abort', abort);
      if (error) reject(error instanceof Error ? error : new Error(String(error)));
      else resolve(value);
    };

    const abort = () => {
      stopTree(child);
      finish(new Error(`${label} was cancelled`));
    };
    if (signal?.aborted) {
      stopTree(child);
      finish(new Error(`${label} was cancelled`));
      return;
    }
    signal?.addEventListener?.('abort', abort, { once: true });

    const timer = setTimeout(() => {
      stopTree(child);
      finish(new Error(`${label} exceeded its ${Math.round(timeoutMs / 1000)}s budget`));
    }, Math.max(1000, Number(timeoutMs) || 0));

    child.stdout.on('data', chunk => {
      stdoutBytes += chunk.length;
      if (stdoutBytes > MAX_STDOUT_BYTES) {
        stopTree(child);
        finish(new Error(`${label} produced more than ${Math.round(MAX_STDOUT_BYTES / 1024 / 1024)} MB of output`));
        return;
      }
      stdout += chunk.toString('utf8');
    });
    child.stderr.on('data', chunk => {
      stderrBytes += chunk.length;
      if (stderrBytes > MAX_STDERR_BYTES) {
        stopTree(child);
        finish(new Error(`${label} produced too much error output`));
        return;
      }
      stderr += chunk.toString('utf8');
    });
    child.on('error', error => finish(new Error(`${label} could not run: ${error.message}`)));
    child.on('close', code => {
      if (settled) return;
      if (code === 0) finish(null, { stdout: stdout.trim(), stderr: stderr.trim(), code });
      else finish(Object.assign(new Error(
        `${label} exited with code ${code}${stderr.trim() ? ': ' + stderr.trim().slice(-1200) : ''}`
      ), { code, stdout, stderr }));
    });

    if (input !== undefined && input !== null) child.stdin.end(input, 'utf8');
    else child.stdin.end();
  });
}

function parseJsonOutput(text) {
  const raw = String(text || '').trim();
  if (!raw) return null;
  const candidates = [];
  candidates.push(raw);
  // Commands often print a log line before their JSON result.
  for (let index = raw.lastIndexOf('\n{'); index !== -1; index = raw.lastIndexOf('\n{', index - 1)) {
    candidates.push(raw.slice(index + 1).trim());
  }
  for (let index = raw.lastIndexOf('\n['); index !== -1; index = raw.lastIndexOf('\n[', index - 1)) {
    candidates.push(raw.slice(index + 1).trim());
  }
  const firstObject = raw.indexOf('{');
  if (firstObject !== -1) candidates.push(raw.slice(firstObject).trim());
  const firstArray = raw.indexOf('[');
  if (firstArray !== -1) candidates.push(raw.slice(firstArray).trim());
  for (const candidate of candidates) {
    try { return JSON.parse(candidate); } catch { /* try the next candidate */ }
  }
  return null;
}

async function runJsonCommand(command, options = {}) {
  const result = await runCommand(command, options);
  const parsed = parseJsonOutput(result.stdout);
  if (parsed === null) {
    throw new Error(`${options.label || 'command'} returned no JSON result${result.stderr ? ': ' + result.stderr.slice(-800) : ''}`);
  }
  if (parsed && typeof parsed === 'object' && parsed.error) throw new Error(String(parsed.error));
  return parsed;
}

module.exports = { runCommand, runJsonCommand, parseJsonOutput, quoteArg, commandLine, stopTree };
