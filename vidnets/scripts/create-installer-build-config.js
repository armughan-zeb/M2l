const fs = require('fs');
const path = require('path');

const outputFile = process.argv[2];
const outputDir = process.argv[3] || 'dist-build-cache/installer-output';

if (!outputFile) {
  console.error('Usage: node scripts/create-installer-build-config.js <output-config> [output-dir]');
  process.exit(1);
}

const root = path.resolve(__dirname, '..');
const pkg = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
const build = JSON.parse(JSON.stringify(pkg.build || {}));

build.directories = Object.assign({}, build.directories, { output: outputDir.replace(/\\/g, '/') });
build.win = Object.assign({}, build.win, {
  icon: 'public/icon.ico',
  target: [{ target: 'nsis', arch: ['x64'] }],
  // The build script applies the icon with the bundled rcedit binary before
  // creating the NSIS package. Keeping electron-builder's signing/edit pass
  // off avoids downloading winCodeSign (whose archive contains macOS
  // symlinks that fail on locked-down Windows builders).
  signAndEditExecutable: false,
});
build.nsis = Object.assign(
  {
    oneClick: false,
    differentialPackage: false,
    perMachine: false,
    allowToChangeInstallationDirectory: true,
    createDesktopShortcut: true,
    createStartMenuShortcut: true,
    shortcutName: 'Vidnets Studio v1',
    installerIcon: 'public/icon.ico',

    uninstallerIcon: 'public/icon.ico',
    installerHeaderIcon: 'public/icon.ico',
  },
  build.nsis || {}
);

fs.mkdirSync(path.dirname(path.resolve(root, outputFile)), { recursive: true });
fs.writeFileSync(path.resolve(root, outputFile), JSON.stringify(build, null, 2));
