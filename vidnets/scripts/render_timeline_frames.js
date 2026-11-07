const path = require('path');
const fs = require('fs');
const http = require('http');
const { execSync } = require('child_process');
const { bundle } = require('@remotion/bundler');
const { renderMedia, selectComposition } = require('@remotion/renderer');

const root = path.resolve(__dirname, '..');
const galleryDir = 'C:\\Users\\user\\.gemini\\antigravity\\brain\\92f3db13-954a-4b44-a08e-9383e3c2c23b\\all_templates_gallery';
const outputMp4 = path.join(root, 'output', 'timeline_showcase.mp4');

const SAMPLE_IMAGES = [
  path.join(root, 'temp/codex_validated_royal_20260916/temp/jobs/c3e128c5/c3e128c5-run-0001-0b49226283f5/render_media/web_v2_1789574320024_791c23/scene_0001.jpg'),
  path.join(root, 'temp/codex_validated_royal_20260916/temp/jobs/c3e128c5/c3e128c5-run-0001-0b49226283f5/render_media/web_v2_1789574320024_791c23/scene_0002.jpg')
];

const TIMELINE_SPECS = [
  {
    id: '24_timeline_flip_calendar',
    name: 'Timeline: Physical Date Flip Calendar (True Crime / Archival)',
    genre: 'true-crime',
    primary_visual: {
      type: 'Timeline',
      variant: 'date_flip_calendar',
      props: {
        month: 'MAY',
        day: '06',
        year: '2023',
        caption: 'OPERATION GOLDEN ORB // WESTMINSTER'
      }
    },
    imageIdx: 0,
  },
  {
    id: '25_timeline_milestone_trail',
    name: 'Timeline: Horizontal Milestone Sequence (Wall Street / Minimal)',
    genre: 'finance-explainer',
    primary_visual: {
      type: 'Timeline',
      variant: 'horizontal_milestone_trail',
      props: {
        title: 'FISCAL SUCCESSION CHRONOLOGY',
        events: [
          { year: '1952', label: 'Civil List Act Inaugurated' },
          { year: '1992', label: 'Annus Horribilis Tax Reform' },
          { year: '2012', label: 'Sovereign Grant Enacted' }
        ]
      }
    },
    imageIdx: 1,
  }
];

function startLocalAssetServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      try {
        const decoded = decodeURIComponent(req.url.replace(/^\//, ''));
        if (fs.existsSync(decoded)) {
          const stream = fs.createReadStream(decoded);
          res.writeHead(200, { 'Content-Type': 'image/jpeg', 'Access-Control-Allow-Origin': '*' });
          stream.pipe(res);
        } else {
          res.writeHead(404);
          res.end();
        }
      } catch (err) {
        res.writeHead(500);
        res.end();
      }
    });
    server.listen(9877, () => {
      resolve(server);
    });
  });
}

async function run() {
  console.log('Rendering Timeline Showcase...');
  const server = await startLocalAssetServer();

  const scenes = TIMELINE_SPECS.map((spec, i) => {
    const imgPath = SAMPLE_IMAGES[spec.imageIdx % SAMPLE_IMAGES.length];
    const webUrl = `http://localhost:9877/${encodeURIComponent(imgPath)}`;
    return {
      id: `scene_${String(i + 1).padStart(4, '0')}`,
      duration_in_frames: 45,
      genre: spec.genre,
      primary_visual: spec.primary_visual,
      media: {
        url: webUrl,
        type: 'image',
        motion: 'slow_zoom_in',
        ken_burns: { start_scale: 1.0, end_scale: 1.08, pan_x: 0, pan_y: 0 }
      },
      text: { headline: spec.name }
    };
  });

  const manifest = {
    composition: {
      fps: 30,
      width: 1920,
      height: 1080,
      duration: scenes.length * 1.5,
      duration_in_frames: scenes.length * 45
    },
    scenes
  };

  const serveUrl = await bundle({
    entryPoint: path.join(root, 'remotion', 'entry.jsx'),
    rootDir: root,
    publicDir: path.join(root, 'public'),
    webpackOverride: (config) => config,
  });

  const inputProps = { manifest };
  const composition = await selectComposition({ serveUrl, id: 'VidzeoDocumentary', inputProps });

  await renderMedia({
    composition,
    serveUrl,
    codec: 'h264',
    outputLocation: outputMp4,
    inputProps
  });

  console.log('Showcase MP4 rendered at:', outputMp4);

  // Extract frames
  for (let i = 0; i < TIMELINE_SPECS.length; i++) {
    const spec = TIMELINE_SPECS[i];
    const frameNumber = i * 45 + 25;
    const timeSec = (frameNumber / 30).toFixed(2);
    const outFrame = path.join(galleryDir, `${spec.id}.jpg`);
    execSync(`ffmpeg -y -ss ${timeSec} -i "${outputMp4}" -frames:v 1 -q:v 2 "${outFrame}"`, { stdio: 'pipe' });
    console.log(`Extracted: ${spec.id}.jpg`);
  }

  // Create timeline variety matrix
  const matrixPath = path.join(galleryDir, 'matrix_timeline_varieties.jpg');
  execSync(`ffmpeg -y -i "${path.join(galleryDir, '24_timeline_flip_calendar.jpg')}" -i "${path.join(galleryDir, '25_timeline_milestone_trail.jpg')}" -filter_complex "[0:v]scale=960:540[v0];[1:v]scale=960:540[v1];[v0][v1]hstack=inputs=2[v]" -map "[v]" -update 1 "${matrixPath}"`, { stdio: 'pipe' });
  console.log('Created timeline variety matrix!');

  server.close();
  process.exit(0);
}

run().catch(err => {
  console.error(err);
  process.exit(1);
});
