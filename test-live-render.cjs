'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

// Ensure external_agent inference is compulsory and points to our Muse inference engine
delete process.env.OPENROUTER_API_KEY;
delete process.env.VIDZEO_OPENROUTER_API_KEY;
delete process.env.SCRIPTIT_API_KEY;
delete process.env.SCRIPTIT_OPENROUTER_API_KEY;

const inferenceScript = path.join(__dirname, 'muse-inference-engine.cjs');
process.env.MUSE_INFERENCE_COMMAND = `node "${inferenceScript}"`;
process.env.MUSE_MEDIA_FALLBACK_POLICY = 'grounded_graphic';
process.env.VIDZEO_AUTH_MODE = 'local';
process.env.VIDZEO_ALLOW_PACKAGED_LOCAL_AUTH = '1';

const { render } = require('./adapters/vidnets');

async function main() {
  console.log('================================================================');
  console.log('  LIVE VIDEO GENERATION TEST: TRAIN CREW WITH MUSE INFERENCE   ');
  console.log('================================================================');
  console.log(`Inference engine: ${inferenceScript}`);
  console.log(`Fallback policy:  ${process.env.MUSE_MEDIA_FALLBACK_POLICY}`);

  const workDir = path.join(__dirname, 'work', `live-test-${Date.now()}`);
  fs.mkdirSync(workDir, { recursive: true });

  const job = {
    jobId: `live-render-${Date.now()}`,
    title: 'The 3,000-Horsepower Cold Start: Starting A Freight Locomotive',
    targetDurationMinutes: 1,
    channel: {
      id: 'c_mtg71ara9djzzm',
      name: 'Train Crew',
      niche: 'Trains & Railways',
      videoOptions: {
        voice: 'en-US-ChristopherNeural',
        captionsEnabled: true,
        quality: 'medium',
        musicVolume: 15
      }
    }
  };

  const scriptText = `Deep inside the cab of a three-thousand-horsepower freight locomotive, starting the engine is a precise, high-voltage mechanical sequence that demands complete precision.
First, the engineer steps up to the control stand and throws the primary battery knife switch, energizing the high-voltage auxiliary cabinet and waking the central computer.
Next, the fuel pump prime switch is engaged, rapidly building forty-five pounds of pressure across the heavy diesel injection rails to purge any trapped air.
With fuel pressure confirmed, the engineer sets the isolation switch to start and firmly depresses the dual starter buttons.
Two massive electric starter motors draw hundreds of amps, turning the sixteen-cylinder turbocharged prime mover against immense compression.
One by one, the massive cylinders fire in exact firing order, sending plumes of dark exhaust through the roof manifold until the mechanical governor stabilizes idle speed.
Every bearing, oil pump, and turbocharger compressor must synchronize perfectly before this three-hundred-thousand-pound locomotive can couple to fifty freight cars and haul thousands of tons of cargo down the line.`;

  console.log(`Job ID:       ${job.jobId}`);
  console.log(`Title:        "${job.title}"`);
  console.log(`Channel:      Train Crew (c_mtg71ara9djzzm)`);
  console.log(`Work dir:     ${workDir}`);
  console.log(`Script words: ${scriptText.split(/\s+/).length} words\n`);

  const startTime = Date.now();
  let lastReportedPct = -1;

  const result = await render(job, scriptText, workDir, {
    onProgress: (percent, detail) => {
      if (percent !== lastReportedPct) {
        lastReportedPct = percent;
        const elapsed = Math.round((Date.now() - startTime) / 1000);
        console.log(`[${new Date().toLocaleTimeString()}] [${percent}%] (${elapsed}s) ${detail}`);
      }
    }
  });

  const totalElapsed = Math.round((Date.now() - startTime) / 1000);
  console.log('\n================================================================');
  console.log('                 LIVE VIDEO GENERATION SUCCESS                  ');
  console.log('================================================================');
  console.log(`Output Video:     ${result.videoPath}`);
  console.log(`Vidnets Job ID:   ${result.vidnetsJobId}`);
  console.log(`Total Time:       ${totalElapsed}s`);
  console.log(`Meta:             ${JSON.stringify(result.meta, null, 2)}`);

  // Probe with FFprobe
  const ffprobeBin = path.join(__dirname, 'vidnets', 'tools', 'ffmpeg', 'ffprobe.exe');
  if (fs.existsSync(ffprobeBin) && fs.existsSync(result.videoPath)) {
    const probe = spawnSync(ffprobeBin, [
      '-v', 'quiet',
      '-print_format', 'json',
      '-show_format',
      '-show_streams',
      result.videoPath
    ], { encoding: 'utf8' });
    try {
      const probeData = JSON.parse(probe.stdout);
      const vStream = (probeData.streams || []).find(s => s.codec_type === 'video');
      const aStream = (probeData.streams || []).find(s => s.codec_type === 'audio');
      console.log('\n--- Video Probe Verification ---');
      console.log(`Resolution:       ${vStream?.width}x${vStream?.height}`);
      console.log(`Codec:            ${vStream?.codec_name}`);
      console.log(`Duration:         ${parseFloat(probeData.format?.duration || 0).toFixed(1)}s`);
      console.log(`File Size:        ${(parseInt(probeData.format?.size || 0) / (1024 * 1024)).toFixed(2)} MB`);
      console.log(`Audio Codec:      ${aStream?.codec_name}`);
    } catch {}
  }
}

main().catch(err => {
  console.error('\n[FATAL ERROR]', err.message);
  process.exit(1);
});
