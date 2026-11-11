'use strict';

/**
 * Automated-run gate. Muse inference is COMPULSORY: the daemon may only
 * start when a Muse inference bridge is present and verified.
 *
 * MUSE_AUTO_LOOP=1 is a claim mode only — it lets the daemon claim jobs on
 * its own. It is NOT an inference exemption and never has been: an earlier
 * version returned [] for MUSE_AUTO_LOOP=1, which is how the daemon ran
 * for hours with no inference bridge at all and delivered graphics-only
 * videos. That bypass is removed.
 *
 * The bridge is checked by lib/museInference.js: no OpenRouter key in the
 * environment, a Vidnets render path (render-vidnets-linux.js) or an
 * explicit inference transport, the vidnets-inbox-watch hook enabled, and
 * the agent-bridge inbox directory writable.
 */

const { checkMuseInferenceBridge } = require('./museInference');

function automatedRuntimeBlockers(env = process.env) {
  const bridge = checkMuseInferenceBridge(env);
  if (bridge.ok) return [];
  return bridge.failures.map(failure => 'Muse inference is compulsory: ' + failure);
}

module.exports = { automatedRuntimeBlockers };
