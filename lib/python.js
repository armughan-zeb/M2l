'use strict';

/**
 * Cross-platform Python 3 discovery for Muse worker and adapters.
 * Ensures robust detection on Ubuntu/Debian, CentOS, Alpine, and Windows.
 */

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

let cachedPython = null;

function executableExists(binary) {
  if (path.isAbsolute(binary)) {
    if (!fs.existsSync(binary)) return false;
    if (process.platform !== 'win32') {
      try {
        fs.accessSync(binary, fs.constants.X_OK);
      } catch {
        try {
          fs.chmodSync(binary, 0o755);
          return true;
        } catch {
          return false;
        }
      }
    }
    return true;
  }
  return true;
}

function probePython(binary) {
  return new Promise(resolve => {
    let child;
    let stdout = '';
    try {
      child = spawn(binary, ['-c', 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}")'], {
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'ignore']
      });
    } catch {
      resolve(null);
      return;
    }
    const timer = setTimeout(() => {
      try { child.kill('SIGKILL'); } catch {}
      resolve(null);
    }, 4000);
    child.stdout?.on('data', chunk => { stdout += chunk.toString(); });
    child.on('error', () => { clearTimeout(timer); resolve(null); });
    child.on('close', code => {
      clearTimeout(timer);
      if (code === 0 && stdout.trim()) {
        const ver = stdout.trim();
        if (ver.startsWith('3.')) {
          resolve({ binary, version: ver });
          return;
        }
      }
      resolve(null);
    });
  });
}

async function resolvePython() {
  if (cachedPython) return cachedPython;

  const candidates = [];

  // Environment variables have highest priority
  if (process.env.VIDNETS_PYTHON) candidates.push(process.env.VIDNETS_PYTHON);
  if (process.env.PYTHON) candidates.push(process.env.PYTHON);
  if (process.env.MUSE_PYTHON) candidates.push(process.env.MUSE_PYTHON);
  if (process.env.SCRIPTIT_PYTHON) candidates.push(process.env.SCRIPTIT_PYTHON);

  if (process.platform !== 'win32') {
    // Linux / Ubuntu standard system paths
    candidates.push(
      'python3',
      '/usr/bin/python3',
      '/usr/local/bin/python3',
      'python',
      '/usr/bin/python',
      path.join(__dirname, '..', '.venv', 'bin', 'python3'),
      path.join(__dirname, '..', 'venv', 'bin', 'python3'),
      path.join(__dirname, '..', 'vidnets', '.venv', 'bin', 'python3'),
      path.join(__dirname, '..', 'vidnets', 'venv', 'bin', 'python3'),
      path.join(__dirname, '..', 'vidnets', 'tools', 'python', 'bin', 'python3'),
      path.join(__dirname, '..', 'vidnets', 'tools', 'python', 'python')
    );
  } else {
    // Windows paths
    candidates.push(
      path.join(__dirname, '..', 'vidnets', 'tools', 'python', 'python.exe'),
      'python',
      'python3',
      'py'
    );
  }

  for (const candidate of candidates) {
    if (!candidate) continue;
    const trimmed = String(candidate).trim();
    if (!trimmed) continue;
    if (!executableExists(trimmed)) continue;
    const probe = await probePython(trimmed);
    if (probe) {
      cachedPython = probe.binary;
      return cachedPython;
    }
  }

  // Sensible default per OS
  cachedPython = process.platform === 'win32' ? 'python' : 'python3';
  return cachedPython;
}

async function availability() {
  const binary = await resolvePython();
  const probe = await probePython(binary);
  return {
    binary,
    version: probe ? probe.version : null,
    ok: Boolean(probe)
  };
}

module.exports = { resolvePython, availability, probePython };
