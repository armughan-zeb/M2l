'use strict';

const path = require('path');
const fs = require('fs');
const { render } = require('../adapters/vidnets');

const scriptText = `At 4:30 AM in a frozen Nebraska rail yard, a Union Pacific SD70ACe sits dead cold. Snow covers the walkway, and the cab temperature is four degrees. Two crew members climb aboard to begin the 45-minute cold-start procedure.
Throws the primary battery knife switch, energizing the high-voltage auxiliary cabinet and waking the central computer.
The mechanical fuel pump primes, building 35 PSI of oil pressure before cranking begins.
Two 64-volt starter motors draw 1,200 amps to slowly roll the massive 16-cylinder diesel.
Thick white exhaust clears to a steady gray haze as the 710 prime mover catches and stabilizes.
Gauges sweep: oil pressure 45 PSI, water temp climbing through 120 degrees, air tanks charging to 140.
The engineer watches the telemetry, knowing this locomotive is ready to pull 14,000 tons across the Rockies.`;

const job = {
  jobId: `query-test-${Date.now()}`,
  title: 'The 3,000-Horsepower Cold Start Starting A Freight Locomotive',
  channel: {
    name: 'Train Crew',
    channelId: 'c_mtg71ara9djzzm',
    niche: 'railway'
  },
  targetDurationMinutes: 1,
  options: {
    quality: 'fast'
  }
};

const workDir = path.join(__dirname, '..', 'work', job.jobId);
fs.mkdirSync(workDir, { recursive: true });

process.env.MUSE_INFERENCE_COMMAND = `node "${path.resolve(__dirname, '..', 'muse-inference-engine.cjs')}"`;
process.env.VIDZEO_PORT = '5010';
process.env.MUSE_VIDNETS_URL = 'http://127.0.0.1:5010';
delete process.env.OPENROUTER_API_KEY;
delete process.env.VIDZEO_OPENROUTER_API_KEY;

console.log('Testing Vidnets scene query generation on workDir:', workDir);

async function main() {
  const abort = new AbortController();
  try {
    const res = await render(job, scriptText, workDir, {
      signal: abort.signal,
      onProgress: (pct, detail) => {
        console.log(`[Progress ${pct}%] ${detail}`);
      }
    });
    console.log('Render finished successfully:', res);
  } catch (err) {
    console.error('Render error:', err.message);
  }
}

main();
