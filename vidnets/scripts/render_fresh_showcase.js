const path = require('path');
const fs = require('fs');
const { bundle } = require('@remotion/bundler');
const { renderMedia, selectComposition } = require('@remotion/renderer');

const root = path.resolve(__dirname, '..');
const artifactDir = path.resolve('C:/Users/user/.gemini/antigravity/brain/d385f0b6-0513-4e80-9f01-570afe68dea0');
const outputPath = path.join(artifactDir, 'fresh_documentary_showcase.mp4');

const freshManifest = {
  version: 1,
  fps: 30,
  width: 1920,
  height: 1080,
  duration: 16,
  duration_in_frames: 480,
  visual_dna: {
    genre: 'archival-dossier',
  },
  scenes: [
    {
      id: 'scene-1',
      scene_index: 0,
      start: 0,
      duration: 4,
      duration_in_frames: 120,
      narration: 'Classic motoring archives hold the engineering secrets of an industrial golden era.',
      category: 'Classic Cars',
      visual_dna: { genre: 'archival-dossier' },
      media: { type: 'image', src: '', is_graphic_fallback: true },
      graphic: {
        template_id: 'chapter-opener',
        props: { kicker: 'ARCHIVAL DOSSIER // DECLASSIFIED', title: 'CLASSIC WHEELS' },
      },
      camera: { motion: 'slow_push', scale: 1 },
      sound_cues: [],
    },
    {
      id: 'scene-2',
      scene_index: 1,
      start: 4,
      duration: 4,
      duration_in_frames: 120,
      narration: 'Heavy diesel locomotives harness four thousand horsepower across mountain gradients.',
      category: 'Trains & Railways',
      visual_dna: { genre: 'engineering-blueprint' },
      media: { type: 'image', src: '', is_graphic_fallback: true },
      graphic: {
        template_id: 'stat-card',
        props: { stat: '4,300 HP', label: 'DIESEL-ELECTRIC TRACTION', detail: 'Twin turbocharged power assembly' },
      },
      camera: { motion: 'pan_left', scale: 1 },
      sound_cues: [],
    },
    {
      id: 'scene-3',
      scene_index: 2,
      start: 8,
      duration: 4,
      duration_in_frames: 120,
      narration: 'Forensic pathology reveals the microscopic timeline of cellular decomposition.',
      category: 'Death & Afterlife',
      visual_dna: { genre: 'forensic-noir' },
      media: { type: 'image', src: '', is_graphic_fallback: true },
      graphic: {
        template_id: 'quote-card',
        props: { quote: 'The timeline of cellular breakdown begins within minutes of clinical death.', attribution: 'FORENSIC INQUIRY REPORT' },
      },
      camera: { motion: 'slow_pull', scale: 1 },
      sound_cues: [],
    },
    {
      id: 'scene-4',
      scene_index: 3,
      start: 12,
      duration: 4,
      duration_in_frames: 120,
      narration: 'Generations of dynastic wealth and palace diplomacy span centuries of royal heritage.',
      category: 'Royal Family',
      visual_dna: { genre: 'royal-prestige' },
      media: { type: 'image', src: '', is_graphic_fallback: true },
      graphic: {
        template_id: 'chapter-opener',
        props: { kicker: 'ROYAL HERITAGE // CROWN ARCHIVES', title: 'THE WINDSOR DYNASTY' },
      },
      camera: { motion: 'pan_right', scale: 1 },
      sound_cues: [],
    },
  ],
};

async function main() {
  console.log(`[FreshRender] Bundling entry point: ${path.join(root, 'remotion', 'entry.jsx')}`);
  const startTime = Date.now();

  const serveUrl = await bundle({
    entryPoint: path.join(root, 'remotion', 'entry.jsx'),
    rootDir: root,
    publicDir: path.join(root, 'public'),
    webpackOverride: (config) => config,
  });

  const inputProps = { manifest: freshManifest };
  console.log(`[FreshRender] Selecting composition with 4 fresh documentary scenes (16s)...`);
  const composition = await selectComposition({
    serveUrl,
    id: 'VidzeoDocumentary',
    inputProps,
  });

  console.log(`[FreshRender] Rendering 1080p MP4 to: ${outputPath}...`);
  await renderMedia({
    composition,
    serveUrl,
    codec: 'h264',
    outputLocation: outputPath,
    inputProps,
    concurrency: 4,
    chromiumOptions: {
      gl: 'angle',
      ignoreCertificateErrors: true,
      disableWebSecurity: true,
      args: ['--no-sandbox', '--disable-dev-shm-usage'],
    },
  });

  const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
  const stat = fs.statSync(outputPath);
  console.log(`[FreshRender] SUCCESS! Rendered in ${elapsed}s: ${outputPath} (${(stat.size / 1024 / 1024).toFixed(2)} MB)`);
}

main().catch((err) => {
  console.error('[FreshRender] ERROR:', err);
  process.exit(1);
});
