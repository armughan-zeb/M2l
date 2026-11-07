/* Test: pre-render chapter-opener template frames */
const path = require('path');
const { execSync } = require('child_process');

const root = path.resolve(__dirname, '..');
const props = JSON.stringify({ title: 'THE CRIME', kicker: 'CHAPTER ONE' });
const script = path.join(root, 'scripts', 'render-template-frames.js');
const outputDir = path.join(root, 'public', 'template-animations', 'chapter-opener', 'v0');

const cmd = `node "${script}" "chapter-opener" '${props.replace(/'/g, "'\\''")}' "2.0" "${outputDir}" "0" "{}"`;
console.log('Running:', cmd.substring(0, 200));
try {
  execSync(cmd, { cwd: root, stdio: 'inherit', timeout: 300000 });
} catch (e) {
  console.error('FAILED:', e.message);
}
