'use strict';

function automatedRuntimeBlockers(env = process.env) {
  const blockers = [];
  const scriptCommand = String(env.MUSE_SCRIPT_COMMAND || '').trim();
  const renderCommand = String(env.MUSE_RENDER_COMMAND || '').trim();
  const inference = String(env.MUSE_INFERENCE_COMMAND || env.MUSE_INFERENCE_URL || '').trim();

  if (!scriptCommand) {
    blockers.push('MUSE_SCRIPT_COMMAND is missing. Use the Muse agent-driven claim/write/render/deliver flow, or configure the Muse script command before starting the automated worker.');
  }
  if (!renderCommand && !inference) {
    blockers.push('Vidnets has no Muse inference bridge. Configure MUSE_INFERENCE_COMMAND or MUSE_INFERENCE_URL, or provide MUSE_RENDER_COMMAND.');
  }
  return blockers;
}

module.exports = { automatedRuntimeBlockers };
