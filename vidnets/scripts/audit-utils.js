const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');

function parseEnv(filePath) {
  const values = {};
  if (!fs.existsSync(filePath)) return values;
  for (const rawLine of fs.readFileSync(filePath, 'utf8').split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#') || !line.includes('=')) continue;
    const index = line.indexOf('=');
    const key = line.slice(0, index).trim();
    let value = line.slice(index + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    values[key] = value;
  }
  return values;
}

function loadEnv() {
  return {
    ...parseEnv(path.join(root, '.env.public')),
    ...parseEnv(path.join(root, '.env')),
    ...process.env,
  };
}

function fail(message) {
  throw new Error(message);
}

function requireSupabaseAdmin() {
  const env = loadEnv();
  const url = String(env.SUPABASE_URL || '').replace(/\/$/, '');
  const serviceKey = String(env.SUPABASE_SERVICE_ROLE_KEY || '');
  if (!url) fail('SUPABASE_URL is missing.');
  if (!serviceKey) fail('SUPABASE_SERVICE_ROLE_KEY is required.');
  return { url, serviceKey };
}

async function supabaseFetch(route, options = {}) {
  const { url, serviceKey } = requireSupabaseAdmin();
  const response = await fetch(`${url}/rest/v1/${route}`, {
    ...options,
    headers: {
      apikey: serviceKey,
      Authorization: `Bearer ${serviceKey}`,
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let payload = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch (_error) {
    payload = text;
  }
  if (!response.ok) {
    const body = typeof payload === 'string' ? payload : JSON.stringify(payload);
    fail(`${route} failed (${response.status}): ${body}`);
  }
  return payload;
}

function groupBy(items, keyFn) {
  const groups = new Map();
  for (const item of items) {
    const key = keyFn(item);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  return groups;
}

function makeReporter() {
  const findings = [];
  return {
    red(code, message, details = {}) {
      findings.push({ severity: 'red', code, message, details });
    },
    yellow(code, message, details = {}) {
      findings.push({ severity: 'yellow', code, message, details });
    },
    green(code, message, details = {}) {
      findings.push({ severity: 'green', code, message, details });
    },
    findings,
    hasRed() {
      return findings.some((item) => item.severity === 'red');
    },
    summary(extra = {}) {
      return {
        ok: !this.hasRed(),
        red: findings.filter((item) => item.severity === 'red').length,
        yellow: findings.filter((item) => item.severity === 'yellow').length,
        green: findings.filter((item) => item.severity === 'green').length,
        ...extra,
        findings,
      };
    },
  };
}

module.exports = {
  root,
  loadEnv,
  requireSupabaseAdmin,
  supabaseFetch,
  groupBy,
  makeReporter,
};
