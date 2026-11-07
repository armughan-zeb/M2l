const { spawnSync } = require('child_process');
const path = require('path');
const { root } = require('./audit-utils');

const checks = [
  ['security:scan', [process.execPath, 'scripts/security-scan.js'], true],
  ['entitlements:audit', [process.execPath, 'scripts/audit-entitlements.js'], true],
  ['supabase:smoke', [process.execPath, 'scripts/supabase-auth-smoke.js'], true],
  ['admin-api:smoke', [process.execPath, 'scripts/admin-api-smoke.js'], false],
  ['package:scan', [process.execPath, 'scripts/package-scan.js'], false],
];

function runCheck(name, command, required) {
  console.log(`\n=== ${name} ===`);
  const [exe, ...args] = command;
  const result = spawnSync(exe, args, {
    cwd: root,
    stdio: 'inherit',
    env: process.env,
  });
  const ok = result.status === 0;
  if (!ok && !required) {
    console.warn(`[qa-audit] optional check failed: ${name}`);
    return { name, ok: false, required, optional_failed: true };
  }
  if (!ok) {
    console.error(`[qa-audit] required check failed: ${name}`);
  }
  return { name, ok, required };
}

function main() {
  const results = checks.map(([name, command, required]) => runCheck(name, command, required));
  const requiredFailures = results.filter((result) => result.required && !result.ok);
  const optionalFailures = results.filter((result) => !result.required && !result.ok);
  console.log('\n=== QA Summary ===');
  console.log(JSON.stringify({
    ok: requiredFailures.length === 0,
    required_failures: requiredFailures.map((item) => item.name),
    optional_failures: optionalFailures.map((item) => item.name),
    results,
  }, null, 2));
  if (requiredFailures.length) process.exit(1);
}

main();
