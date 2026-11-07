const fs = require('fs');
const path = require('path');

const projectRoot = path.resolve(__dirname, '..');
const rootArgIndex = process.argv.indexOf('--root');
const root = rootArgIndex >= 0 && process.argv[rootArgIndex + 1]
  ? path.resolve(process.argv[rootArgIndex + 1])
  : projectRoot;
const productionMode = root !== projectRoot || process.argv.includes('--production');

if (!fs.existsSync(root) || !fs.statSync(root).isDirectory()) {
  console.error(`Security scan root does not exist: ${root}`);
  process.exit(2);
}

const ignoredDirs = new Set([
  '.git', '.vscode', '.idea', '.commandcode', '.opencode', 'node_modules',
  'venv', 'venv310', '.venv', 'tools', 'cache', 'temp', 'temp_test', 'tmp',
  'output', 'logs', 'dist', 'dist-build-cache', 'build', '__pycache__',
  '.pytest_cache', '.mypy_cache', '.ruff_cache', 'tests', 'AudiogramTTS-Consumer',
  'Youtube Scraper', 'artifacts',
]);

const allowedSecretFiles = new Set(['.env', '.env.public', 'SECURITY.md', 'scripts/security-scan.js']);
const ignoredFilePatterns = [
  /^step_\d+_content\.txt$/i,
  /^.*\.bak$/i,
  // Legacy Remotion player is explicitly excluded from the FFmpeg package;
  // do not let its generated fixture strings block an otherwise clean build.
  /^src\/remotion-player\.js$/i,
  // A developer may keep a cold Remotion bundle cache under scratch while
  // validating the packaged renderer. Compiled framework form metadata can
  // resemble literal password assignments; this exact generated cache file
  // is never a production/package input. Continue scanning every other file.
  /^scratch\/[^/]+\/(?:render-temp\/)?vidzeo-remotion-bundle-cache\/bundle-[^/]+\/bundle\.js$/i,
];
const localPrivateFilePatterns = [
  /^auth-session.*\.json$/i,
  /^secret-settings.*\.json$/i,
  /^.*\.enc\.json$/i,
  /^\.vidzeo_session_secret$/i,
];
const patterns = [
  ['jwt', /eyJhbGci[A-Za-z0-9._-]+/g],
  ['openai-style-api-key', /sk-[A-Za-z0-9_-]{20,}/g],
  ['groq-api-key', /gsk_[A-Za-z0-9_-]{20,}/g],
  ['google-api-key', /AIza[A-Za-z0-9_-]{25,}/g],
  ['github-token', /gh[oprsu]_[A-Za-z0-9_]{20,}/g],
  ['private-key', /-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----/g],
  // Include prefixed credential names such as API_TOKEN. Only exact known
  // harmless framework name/value pairs are exempted below.
  ['password-assignment', /\b((?:[A-Za-z_$][\w$]*)?(?:password|secret|token))\b['"]?\s*[:=]\s*['"]([^'"]{8,})['"]/gi],
];

const forbiddenProductionPaths = [
  /^\.env(?:\..+)?$/i,
  /^admin-panel(?:\/|$)/i,
  /^START_ADMIN_PANEL\.bat$/i,
  /^scripts\/admin-api-smoke\.js$/i,
  /^auth-session.*\.json$/i,
  /^secret-settings.*\.json$/i,
  /^.*\.enc\.json$/i,
  /^\.vidzeo_session_secret$/i,
  /(?:^|\/)queue\.json(?:\..+)?$/i,
  /^supabase\/\.temp(?:\/|$)/i,
];

const textExtensions = new Set([
  '.js', '.json', '.py', '.sql', '.html', '.css', '.md', '.txt', '.bat', '.ps1',
  '.yml', '.yaml', '.toml', '.ini', '.env', '',
]);

function walk(dir, files = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (ignoredDirs.has(entry.name)) continue;
    const fullPath = path.join(dir, entry.name);
    if (entry.isSymbolicLink()) {
      files.push(fullPath);
    } else if (entry.isDirectory()) {
      walk(fullPath, files);
    } else if (textExtensions.has(path.extname(entry.name).toLowerCase())) {
      files.push(fullPath);
    }
  }
  return files;
}

const findings = [];

for (const file of walk(root)) {
  const relative = path.relative(root, file).replace(/\\/g, '/');
  if (ignoredFilePatterns.some((pattern) => pattern.test(relative))) continue;
  if (!productionMode && localPrivateFilePatterns.some((pattern) => pattern.test(relative))) continue;

  if (productionMode && forbiddenProductionPaths.some((pattern) => pattern.test(relative))) {
    findings.push({ file: relative, line: 1, label: 'forbidden-production-path', allowed: false });
    continue;
  }

  let stat;
  try {
    stat = fs.lstatSync(file);
  } catch (_error) {
    continue;
  }
  if (stat.isSymbolicLink()) {
    findings.push({ file: relative, line: 1, label: 'symbolic-link', allowed: false });
    continue;
  }

  let content = '';
  try {
    content = fs.readFileSync(file, 'utf8');
  } catch (_error) {
    continue;
  }

  const lines = content.split(/\r?\n/);
  for (const [label, pattern] of patterns) {
    pattern.lastIndex = 0;
    for (const match of content.matchAll(pattern)) {
      if (label === 'password-assignment' && match[0].includes("'local-bypass'")) continue;
      if (label === 'password-assignment') {
        // An exact interpolation placeholder in a diagnostic/template is
        // not a literal credential. Keep mixed literal/template values
        // subject to the scan.
        if (/^\$\{[A-Za-z_$][\w$]*\}$/.test(match[2])) continue;
        const frameworkMessages = {
          DATE_TOKEN: 'remotion-date:', FILE_TOKEN: 'remotion-file:',
          DELAY_RENDER_CALLSTACK_TOKEN: 'The delayRender was called:',
          DELAY_RENDER_RETRY_TOKEN: '- Rendering the frame will be retried.',
          DELAY_RENDER_CLEAR_TOKEN: 'handle was cleared after',
        };
        if (frameworkMessages[match[1]] === match[2]) continue;
      }
      const line = content.slice(0, match.index).split(/\r?\n/).length;
      findings.push({
        file: relative,
        line,
        label,
        allowed: allowedSecretFiles.has(relative),
      });
    }
  }

  if (relative === 'package.json') {
    const packageJson = JSON.parse(content);
    if ((packageJson.build?.asarUnpack || []).some((item) => item.includes('node_modules'))) {
      findings.push({ file: relative, line: 1, label: 'broad-node-modules-asar-unpack', allowed: false });
    }
    const packageText = JSON.stringify(packageJson);
    if (productionMode && /admin-panel|admin-api-smoke/i.test(packageText)) {
      findings.push({ file: relative, line: 1, label: 'private-admin-package-reference', allowed: false });
    }
  }

  if (relative === '.gitignore' && !lines.some((line) => line.trim() === '.env')) {
    findings.push({ file: relative, line: 1, label: 'missing-env-ignore', allowed: false });
  }
}

const blocking = findings.filter((item) => !item.allowed);
for (const finding of findings) {
  const level = finding.allowed ? 'warn' : 'fail';
  console.log(`${level}: ${finding.file}:${finding.line} ${finding.label}`);
}

if (blocking.length) {
  console.error(`Security scan failed with ${blocking.length} blocking finding(s).`);
  process.exit(1);
}

console.log(`Security scan passed (${productionMode ? 'production' : 'development'} root: ${root}).`);
