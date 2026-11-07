/* Render a Remotion template animation as a frame sequence (transparent PNGs).
   Usage: node scripts/render-template-frames.js <template_id> <props_json> <duration_sec> <output_dir> [variant] [theme_json]
*/
const path = require('path');
const fs = require('fs');
const { bundle } = require('@remotion/bundler');
const { renderFrames, selectComposition } = require('@remotion/renderer');

const root = path.resolve(__dirname, '..');
const templateId = process.argv[2] || 'chapter-opener';
const propsJson = process.argv[3] || '{}';
const durationSec = parseFloat(process.argv[4] || '2');
const outputDir = path.resolve(process.argv[5] || path.join(root, 'output', 'template_frames', templateId));
const variant = parseInt(process.argv[6] || '0');
const themeJson = process.argv[7] || '{}';

async function main() {
  fs.mkdirSync(outputDir, { recursive: true });

  let props;
  try { props = JSON.parse(propsJson); } catch { props = {}; }
  let theme;
  try { theme = JSON.parse(themeJson); } catch { theme = {}; }

  const inputProps = {
    template_id: templateId,
    props: { ...props, variant },
    duration: durationSec,
    fps: 30,
    width: 1920,
    height: 1080,
    theme,
  };

  const fps = 30;
  const totalFrames = Math.round(durationSec * fps);

  // Check if already cached
  const firstFrame = path.join(outputDir, 'frame_0000.png');
  const lastFrame = path.join(outputDir, `frame_${String(totalFrames - 1).padStart(4, '0')}.png`);
  if (fs.existsSync(firstFrame) && fs.existsSync(lastFrame)) {
    console.log(`[template-frames] CACHED: ${templateId} (${totalFrames} frames) → ${outputDir}`);
    process.exit(0);
  }

  const serveUrl = await bundle({
    entryPoint: path.join(root, 'remotion', 'template-entry.jsx'),
    rootDir: root,
    publicDir: path.join(root, 'public'),
    webpackOverride: (config) => config,
  });

  const composition = await selectComposition({
    serveUrl,
    id: 'TemplateAnimation',
    inputProps,
  });

  // Reduce scale for pre-rendering — 720p is enough for overlays
  const scale = Math.max(0.5, Math.min(1, Number(process.env.VIDZEO_TEMPLATE_SCALE || 1)));

  await renderFrames({
    composition,
    serveUrl,
    outputDir,
    inputProps,
    imageFormat: 'png',
    frameRange: [0, totalFrames - 1],
    concurrency: 3,
    scale,
    onFrameUpdate: (frame) => {
      if (frame % 15 === 0) process.stdout.write('.');
    },
  });

  console.log(`\n[template-frames] DONE: ${templateId} → ${outputDir} (${totalFrames} frames)`);
}

main().catch((err) => {
  console.error(`[template-frames] FAILED: ${templateId}`, err.message);
  process.exitCode = 1;
});
