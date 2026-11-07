const path = require('path');
const fs = require('fs');
const { bundle } = require('@remotion/bundler');
const { renderMedia, selectComposition } = require('@remotion/renderer');

const root = path.resolve(__dirname, '..');
const outputDir = path.join(root, 'temp');
const outputPath = path.join(outputDir, 'remotion-semantic-demo.mp4');

async function main() {
  fs.mkdirSync(outputDir, { recursive: true });
  const serveUrl = await bundle({ entryPoint: path.join(root, 'remotion', 'entry.jsx'), webpackOverride: (config) => config });
  const inputProps = {};
  const composition = await selectComposition({ serveUrl, id: 'VidzeoDocumentary', inputProps });
  await renderMedia({ composition, serveUrl, codec: 'h264', outputLocation: outputPath, inputProps, audioCodec: 'aac', muted: false });
  console.log(`[Remotion] Demo rendered: ${outputPath}`);
}

main().catch((error) => {
  console.error('[Remotion] Demo render failed:', error);
  process.exitCode = 1;
});
