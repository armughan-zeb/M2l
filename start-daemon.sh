#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
REPO_ROOT="$(pwd -P)"

export MUSE_AUTO_LOOP=1
export MUSE_INFERENCE_COMPULSORY=1
export MUSE_INFERENCE_COMMAND="${MUSE_INFERENCE_COMMAND:-node $(printf '%q' "$REPO_ROOT/scripts/muse-inference-spool.cjs")}"
export MUSE_REPO_ROOT="${MUSE_REPO_ROOT:-$REPO_ROOT}"
export VIDZEO_TEMP_DIR="${VIDZEO_TEMP_DIR:-${VIDZEO_RUNTIME_ROOT:-$HOME/workspace/vidnets-runtime}/temp}"
export TMPDIR="${TMPDIR:-$VIDZEO_TEMP_DIR}"
mkdir -p "$TMPDIR" "$TMPDIR/agent-bridge"

exec node muse-agent.js start "$@"
