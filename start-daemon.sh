#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
REPO_ROOT="$(pwd -P)"

export MUSE_AUTO_LOOP=1
export MUSE_INFERENCE_COMPULSORY=1
# npx lives in /opt/hatch-image/bin, which is NOT on systemd's default
# PATH: without this the documentary render's `npx remotion render`
# spawn fails with ENOENT on every daemon run (Nov 28, 2026 — the
# t_rebuild_20261128 resume looped on it, the real error masked by the
# CMS-fail report). Prepend it when present.
if [ -d /opt/hatch-image/bin ]; then
  case ":$PATH:" in *:/opt/hatch-image/bin:*) ;; *) export PATH="/opt/hatch-image/bin:$PATH" ;; esac
fi
export MUSE_INFERENCE_COMMAND="${MUSE_INFERENCE_COMMAND:-node $(printf '%q' "$REPO_ROOT/scripts/muse-inference-spool.cjs")}"
export MUSE_REPO_ROOT="${MUSE_REPO_ROOT:-$REPO_ROOT}"
case "${MUSE_RENDER_COMMAND:-}" in *render-vidnets-linux*|*render-linux*) unset MUSE_RENDER_COMMAND ;; esac
case "${MUSE_SCRIPT_COMMAND:-}" in *scriptit*) unset MUSE_SCRIPT_COMMAND ;; esac
export VIDZEO_TEMP_DIR="${VIDZEO_TEMP_DIR:-${VIDZEO_RUNTIME_ROOT:-$HOME/workspace/vidnets-runtime}/temp}"
export TMPDIR="${TMPDIR:-$VIDZEO_TEMP_DIR}"
mkdir -p "$TMPDIR" "$TMPDIR/agent-bridge"

exec node muse-agent.js start "$@"
