#!/usr/bin/env bash
set -euo pipefail

export MUSE_AUTO_LOOP="${MUSE_AUTO_LOOP:-1}"
export MUSE_INFERENCE_COMPULSORY=1
export MUSE_REPO_ROOT="${MUSE_REPO_ROOT:-/app}"
export MUSE_INFERENCE_COMMAND="${MUSE_INFERENCE_COMMAND:-node /app/scripts/muse-inference-spool.cjs}"
export VIDZEO_SKIP_THUMBNAILS="${VIDZEO_SKIP_THUMBNAILS:-1}"
export NODE_EXTRA_CA_CERTS="${NODE_EXTRA_CA_CERTS:-/etc/ssl/certs/ca-certificates.crt}"
export SSL_CERT_FILE="${SSL_CERT_FILE:-/etc/ssl/certs/ca-certificates.crt}"
export VIDZEO_RUNTIME_ROOT="${VIDZEO_RUNTIME_ROOT:-/data/runtime}"
export VIDZEO_TEMP_DIR="${VIDZEO_TEMP_DIR:-/data/runtime/temp}"
export TMPDIR="${TMPDIR:-/data/runtime/temp}"
export MUSE_WORK_DIR="${MUSE_WORK_DIR:-/data/work}"
export MUSE_CONFIG_FILE="${MUSE_CONFIG_FILE:-/data/.muse-agent.json}"
export MUSE_INBOX_HOOK_STATE_DIR="${MUSE_INBOX_HOOK_STATE_DIR:-/host-hook-state}"

mkdir -p /data/runtime/temp/agent-bridge /data/work

for var in OPENROUTER_API_KEY VIDZEO_OPENROUTER_API_KEY SCRIPTIT_API_KEY SCRIPTIT_OPENROUTER_API_KEY; do
  value="${!var:-}"
  if [ -n "$value" ] && [ "${value#external-agent:}" = "$value" ]; then
    echo "[muse] FATAL: $var is set. Muse inference is compulsory — remove it." >&2
    exit 1
  fi
done

exec node /app/muse-agent.js "$@"
