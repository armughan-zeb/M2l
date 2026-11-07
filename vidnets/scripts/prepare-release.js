/*
 * Prepare the small, signed-by-checksum update channel used by Vidnets Studio.
 *
 * GitHub Release assets are intentionally not committed to git (the installer
 * is hundreds of megabytes). This script stages the installer under
 * Vidnets Releases\artifacts and writes latest.json/checksums.txt for the
 * release repository. Run it after the Windows installer build.
 */
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const ROOT = path.resolve(__dirname, '..');
const RELEASE_DIR = path.resolve(process.env.VIDNETS_RELEASE_DIR || path.join(ROOT, '..', '..', 'Vidnets Releases'));
const PACKAGE = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));
const REPO = 'armughan-zeb/Vidnets-Studio-Releases';

function sha256(filePath) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash('sha256');
    const stream = fs.createReadStream(filePath);
    stream.on('data', (chunk) => hash.update(chunk));
    stream.on('error', reject);
    stream.on('end', () => resolve(hash.digest('hex')));
  });
}

function findInstaller() {
  const configuredArtifact = String(PACKAGE.build?.nsis?.artifactName || 'Vidnets v1.${ext}')
    .replace('${ext}', 'exe').replace('${version}', PACKAGE.version);
  const candidates = [
    path.join(ROOT, 'dist', configuredArtifact),
    path.join(ROOT, 'dist', 'Vidnets', 'Vidnets Setup.exe'),
    path.join(ROOT, 'dist', 'Vidzeo-Installer.exe'),
    path.join(ROOT, 'dist-build-cache', 'installer-output', `Vidnets Studio v1 Setup ${PACKAGE.version}.exe`),
    path.join(ROOT, 'dist-build-cache-fresh', 'installer-output', `Vidnets Studio v1 Setup ${PACKAGE.version}.exe`),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate) && fs.statSync(candidate).isFile()) || '';
}

async function main() {
  const version = String(PACKAGE.version || '').trim();
  if (!/^\d+\.\d+\.\d+$/.test(version)) throw new Error(`Invalid package version: ${version}`);
  const installer = findInstaller();
  if (!installer) throw new Error('No Windows installer found. Run BUILD.bat first.');
  // Existing desktop updaters validate this legacy artifact prefix.
  const artifactName = `Vidzeo-${version}-Setup-x64.exe`;
  const artifactDir = path.join(RELEASE_DIR, 'artifacts');
  const artifactPath = path.join(artifactDir, artifactName);
  fs.mkdirSync(artifactDir, { recursive: true });
  fs.copyFileSync(installer, artifactPath);
  const stats = fs.statSync(artifactPath);
  if (stats.size < 10 * 1024 * 1024) throw new Error('Installer is unexpectedly small; refusing to publish it.');
  const hash = await sha256(artifactPath);
  let buildId = '';
  try {
    const stamp = JSON.parse(fs.readFileSync(path.join(ROOT, 'public', 'update-build.json'), 'utf8'));
    buildId = String(stamp.buildId || '').trim().toLowerCase();
  } catch (_error) {
    // Older local builds can still publish; the updater will compare version
    // and artifact checksum, while new builds get same-version fingerprints.
  }
  const integrityPath = [path.join(ROOT, 'dist', 'distribution-integrity.json'),
    path.join(ROOT, 'dist', 'Vidnets', 'checksums.json')].find((file) => fs.existsSync(file));
  if (!integrityPath) throw new Error('Verified distribution metadata is missing.');
  const integrity = JSON.parse(fs.readFileSync(integrityPath, 'utf8').replace(/^\uFEFF/, ''));
  if (!buildId || integrity.buildId !== buildId || integrity.version !== version
      || !integrity.files?.some((file) => file.sha256 === hash && (file.size || file.bytes) === stats.size)) {
    throw new Error('Installer does not match the current verified build; refusing to publish stale bytes.');
  }
  const releaseTag = String(process.env.VIDNETS_RELEASE_TAG || (buildId ? `v${version}-build.${buildId.slice(0, 12)}` : `v${version}`));
  if (!/^v[0-9A-Za-z._-]+$/.test(releaseTag)) throw new Error('Invalid release tag');
  const releaseUrl = `https://github.com/${REPO}/releases/download/${releaseTag}/${encodeURIComponent(artifactName)}`;
  const manifest = {
    schemaVersion: 1,
    product: 'Vidnets Studio',
    version,
    channel: 'stable',
    platform: 'win32',
    arch: 'x64',
    releaseDate: new Date().toISOString(),
    releasePage: `https://github.com/${REPO}/releases/tag/${releaseTag}`,
    releaseNotes: `Vidnets Studio ${version} for Windows.`,
    ...(buildId ? { buildId } : {}),
    artifact: {
      name: artifactName,
      url: releaseUrl,
      size: stats.size,
      sha256: hash,
    },
  };

  const toolsCandidate = path.join(ROOT, 'dist', 'vidnets-tools.zip');
  let toolsHash = '';
  if (fs.existsSync(toolsCandidate)) {
    const targetTools = path.join(artifactDir, 'vidnets-tools.zip');
    fs.copyFileSync(toolsCandidate, targetTools);
    toolsHash = await sha256(targetTools);
    const toolsStats = fs.statSync(targetTools);
    manifest.toolsArchive = {
      name: 'vidnets-tools.zip',
      url: `https://github.com/${REPO}/releases/download/${releaseTag}/vidnets-tools.zip`,
      size: toolsStats.size,
      sha256: toolsHash,
    };
    console.log(`  Staged runtime pack: ${targetTools} (${toolsStats.size} bytes)`);
  }

  fs.writeFileSync(path.join(RELEASE_DIR, 'latest.json'), `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
  let checksumsContent = `${hash}  ${artifactName}\n`;
  if (toolsHash) checksumsContent += `${toolsHash}  vidnets-tools.zip\n`;
  fs.writeFileSync(path.join(RELEASE_DIR, 'checksums.txt'), checksumsContent, 'utf8');
  fs.writeFileSync(path.join(RELEASE_DIR, 'RELEASE_NOTES.md'), `# Vidnets Studio ${version}\n\nWindows x64 installer for the stable channel.\n\nSHA-256: \`${hash}\`\n`, 'utf8');
  console.log(`Prepared ${artifactName}`);
  console.log(`  Installer: ${artifactPath}`);
  console.log(`  Manifest:  ${path.join(RELEASE_DIR, 'latest.json')}`);
  console.log(`  SHA-256:   ${hash}`);
  console.log('Next: upload the staged EXE to ' + releaseTag + ', then publish latest.json/checksums.txt/RELEASE_NOTES.md.');
}

if (require.main === module) {
  main().catch((error) => {
    console.error(`[release] ${error.message}`);
    process.exitCode = 1;
  });
}
module.exports = { findInstaller };
