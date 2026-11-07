const path = require('path');
const fs = require('fs');
const esbuild = require('esbuild');

const root = path.resolve(__dirname, '..');
const outfile = path.join(root, 'src', 'remotion-player.js');

esbuild.build({
  entryPoints: [path.join(root, 'src', 'remotion', 'player-bootstrap.jsx')],
  outfile,
  bundle: true,
  format: 'iife',
  globalName: 'VidzeoRemotionBundle',
  platform: 'browser',
  target: ['es2020'],
  minify: false,
  sourcemap: false,
  legalComments: 'none',
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  loader: { '.js': 'jsx' },
  logLevel: 'info'
}).then(() => {
  const stat = fs.statSync(outfile);
  console.log(`[Remotion] Player bundle written: ${outfile} (${stat.size} bytes)`);
}).catch((error) => {
  console.error('[Remotion] Player bundle failed:', error);
  process.exitCode = 1;
});
