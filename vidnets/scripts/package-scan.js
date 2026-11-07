const fs = require('fs');
const path = require('path');
const { root, loadEnv } = require('./audit-utils');

const distRoot = path.join(root, 'dist');
const cacheBuildRoot = path.join(root, 'dist-build-cache', 'builder-output');
const rootIndex = process.argv.indexOf('--root');
const requestedRoot = rootIndex >= 0 ? process.argv[rootIndex + 1] : '';
const scanRoots = (requestedRoot ? [path.resolve(root, requestedRoot)] : [distRoot, cacheBuildRoot]).filter((dir) => fs.existsSync(dir));

const ignoredDirs = new Set([
  'node_modules',
  '__pycache__',
  '.git',
  'Cache',
  'Code Cache',
]);

const textExtensions = new Set([
  '.js', '.json', '.py', '.html', '.css', '.txt', '.bat', '.ps1', '.yml', '.yaml', '.env', '.md',
]);

function walk(dir, files = []) {
  if (!fs.existsSync(dir)) return files;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (ignoredDirs.has(entry.name)) continue;
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(fullPath, files);
    } else {
      files.push(fullPath);
    }
  }
  return files;
}

function isTextFile(filePath) {
  return textExtensions.has(path.extname(filePath).toLowerCase());
}

function findFilesByName(name) {
  return scanRoots.flatMap((dir) => walk(dir)).filter((file) => path.basename(file).toLowerCase() === name.toLowerCase());
}

function mask(value) {
  if (!value) return '';
  return `${value.slice(0, 6)}...${value.slice(-4)}`;
}

function main() {
  const env = loadEnv();
  const privateValues = [
    ['SUPABASE_SERVICE_ROLE_KEY', env.SUPABASE_SERVICE_ROLE_KEY],
    ['GROQ_API_KEY', env.GROQ_API_KEY || env.VIDZEO_GROQ_API_KEY],
    ['OPENAI_API_KEY', env.OPENAI_API_KEY || env.VIDZEO_OPENAI_API_KEY],
    ['PEXELS_API_KEY', env.PEXELS_API_KEY || env.VIDZEO_PEXELS_API_KEY],
    ['PIXABAY_API_KEY', env.PIXABAY_API_KEY || env.VIDZEO_PIXABAY_API_KEY],
    ['OPENROUTER_API_KEY', env.OPENROUTER_API_KEY || env.VIDZEO_OPENROUTER_API_KEY],
  ].filter(([, value]) => value && String(value).length >= 12);

  const findings = [];
  if (!scanRoots.length) {
    findings.push({
      severity: 'yellow',
      code: 'no_dist_found',
      message: 'No dist or dist-build-cache output exists yet; package scan skipped binary output.',
    });
  }

  const allFiles = scanRoots.flatMap((dir) => walk(dir));
  for (const file of allFiles) {
    const relative = path.relative(root, file).replace(/\\/g, '/');
    if (path.basename(file).toLowerCase() === '.env') {
      findings.push({
        severity: 'red',
        code: 'private_env_in_distribution',
        file: relative,
        message: '.env must not be shipped to end users.',
      });
    }
    if (!isTextFile(file)) continue;
    let content = '';
    try {
      content = fs.readFileSync(file, 'utf8');
    } catch (_error) {
      continue;
    }
    const declaresServiceRole = /SUPABASE_SERVICE_ROLE_KEY\s*=/.test(content);
    const packagedServiceRoleGuard =
      /def\s+service_role_is_configured\s*\(/.test(content)
      && /VIDZEO_PACKAGED\s+and\s+not\s+VIDZEO_ALLOW_PACKAGED_SERVICE_ROLE/.test(content);
    if (declaresServiceRole && !packagedServiceRoleGuard) {
      findings.push({
        severity: 'yellow',
        code: 'service_role_key_name_in_distribution',
        file: relative,
        message: 'Service-role key variable name appears in distribution text; verify packaged mode disables service-role fallback.',
      });
    }
    for (const [name, value] of privateValues) {
      if (value && content.includes(value)) {
        findings.push({
          severity: 'red',
          code: 'private_secret_value_in_distribution',
          file: relative,
          secret: name,
          message: `${name} value is present in distribution output.`,
        });
      }
    }
  }

  const iconIco = findFilesByName('icon.ico');
  const iconPng = findFilesByName('icon.png');
  if (!iconIco.length) {
    findings.push({ severity: 'yellow', code: 'missing_icon_ico', message: 'No icon.ico found in distribution output.' });
  }
  if (!iconPng.length) {
    findings.push({ severity: 'yellow', code: 'missing_icon_png', message: 'No icon.png found in distribution output.' });
  }

  const summary = {
    ok: !findings.some((finding) => finding.severity === 'red'),
    scanned_roots: scanRoots.map((dir) => path.relative(root, dir).replace(/\\/g, '/')),
    scanned_files: allFiles.length,
    red: findings.filter((finding) => finding.severity === 'red').length,
    yellow: findings.filter((finding) => finding.severity === 'yellow').length,
    findings,
  };
  console.log(JSON.stringify(summary, null, 2));
  if (!summary.ok) process.exit(1);
}

main();
