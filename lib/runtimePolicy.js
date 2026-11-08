'use strict';

function automatedRuntimeBlockers(env = process.env) {
  if (String(env.MUSE_AUTO_LOOP || env.MUSE_AUTONOMOUS || '').trim() === '1') {
    return [];
  }
  const blockers = [];
  const scriptCommand = String(env.MUSE_SCRIPT_COMMAND || '').trim();
  const renderCommand = String(env.MUSE_RENDER_COMMAND || '').trim();
  const inference = String(env.MUSE_INFERENCE_COMMAND || env.MUSE_INFERENCE_URL || '').trim();

  if (!scriptCommand) {
    blockers.push('MUSE_SCRIPT_COMMAND is missing. Use the Muse agent-driven claim/write/render/deliver flow, configure the Muse script command, or start with MUSE_AUTO_LOOP=1.');
  }
  if (!renderCommand && !inference) {
    blockers.push('Vidnets has no Muse inference bridge. Configure MUSE_INFERENCE_COMMAND or MUSE_INFERENCE_URL, provide MUSE_RENDER_COMMAND, or start with MUSE_AUTO_LOOP=1.');
  }
  return blockers;
}

module.exports = { automatedRuntimeBlockers };
