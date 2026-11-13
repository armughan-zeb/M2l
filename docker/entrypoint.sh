#!/usr/bin/env bash
# Container entrypoint for the Vidzeo Muse worker.
# Mirrors start-daemon.sh, but with container paths (/app + /data) instead of
# the host paths hardcoded there (/home/hatch/workspace/M2l,
# /home/hatch/workspace/vidnets-runtime). Do NOT call start-daemon.sh here:
# its exports point at host paths that do not exist in the container.
# This script only prepares directories and fail-closed guards, then execs
# the daemon; the daemon's own start gate (lib/museInference.js) verifies
# the Muse inference bridge (writable inbox + enabled, recently-run host
# hook + Vidnets launcher) and refuses to claim work when it is unproven.
set -euo pipefail

# --- Production wiring (same values as start-daemon.sh, container paths) ---
export MUSE_AUTO_LOOP="${MUSE_AUTO_LOOP:-1}"
export MUSE_INFERENCE_COMPULSORY=1
export MUSE_RENDER_COMMAND="${MUSE_RENDER_COMMAND:-node /app/render-vidnets-linux.js}"
export MUSE_VIDNETS_ROOT="${MUSE_VIDNETS_ROOT:-/app/vidnets}"
export MUSE_REPO_ROOT="${MUSE_REPO_ROOT:-/app}"
# Vidnets data root (output, temp, secure/agent-service.json, queue.json).
# Passed to the launcher so Vidnets state lives on the /data volume instead
# of inside the image. headless_agent.py accepts --runtime-root before the
# subcommand: `VIDNETS_AGENT.sh --runtime-root /data/runtime start`.
export MUSE_VIDNETS_COMMAND="${MUSE_VIDNETS_COMMAND:-bash /app/vidnets/VIDNETS_AGENT.sh --runtime-root /data/runtime}"
export MUSE_YOUTUBE_COVERAGE_TARGET="${MUSE_YOUTUBE_COVERAGE_TARGET:-0.8}"
export MUSE_MEDIA_STRICT="${MUSE_MEDIA_STRICT:-0}"
export VIDZEO_VISUAL_TOP_CANDIDATES_PER_SCENE="${VIDZEO_VISUAL_TOP_CANDIDATES_PER_SCENE:-12}"
export VIDZEO_VISUAL_FINALIST_LIMIT="${VIDZEO_VISUAL_FINALIST_LIMIT:-3}"
export VIDZEO_VISUAL_GATE_WORKERS="${VIDZEO_VISUAL_GATE_WORKERS:-12}"
export VIDZEO_VISUAL_RELATIVE_FLOOR="${VIDZEO_VISUAL_RELATIVE_FLOOR:-0.5}"
export VIDZEO_YT_VISUAL_GATE_FRAMES="${VIDZEO_YT_VISUAL_GATE_FRAMES:-2}"
export VIDZEO_MIXED_YT_DOWNLOAD_CONCURRENCY="${VIDZEO_MIXED_YT_DOWNLOAD_CONCURRENCY:-4}"
export VIDZEO_YT_DOWNLOAD_CONCURRENCY="${VIDZEO_YT_DOWNLOAD_CONCURRENCY:-4}"
# Thumbnails are SKIPPED: this worker delivers script + video only.
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

# --- Fail closed: Muse inference is compulsory, no OpenRouter, ever --------
# A key present here would let a provider path be selected instead of Muse.
# (A bare `external-agent:` value does not count; lib/museInference.js
# applies the same rule and the daemon start gate re-checks it.)
for var in OPENROUTER_API_KEY VIDZEO_OPENROUTER_API_KEY SCRIPTIT_API_KEY SCRIPTIT_OPENROUTER_API_KEY; do
  value="${!var:-}"
  if [ -n "$value" ] && [ "${value#external-agent:}" = "$value" ]; then
    echo "[vidzeo] FATAL: $var is set. Muse inference is compulsory — remove it; every vision/script/brain call must go through Muse via the agent-bridge inbox." >&2
    exit 1
  fi
done

# --- Warn (do not fail here: the daemon start gate fails with the detail) ---
HOOK_DEF="${MUSE_INBOX_HOOK_DEFINITION:-/host-hooks/vidnets-inbox-watch.json}"
if [ -z "${MUSE_INFERENCE_COMMAND:-}${MUSE_INFERENCE_URL:-}" ] && [ ! -f "$HOOK_DEF" ]; then
  echo "[vidzeo] WARNING: vidnets-inbox-watch hook definition not found at $HOOK_DEF." >&2
  echo "[vidzeo] WARNING: the host Muse VM must have the vidnets-inbox-watch definition mounted (see docker-compose.yml). The worker probes a real inbox answer before claiming jobs." >&2
  echo "[vidzeo] HOW TO INSTALL IT:" >&2
  echo "[vidzeo]   1. Create the hook definition from docker/hooks/vidnets-inbox-watch.json.template." >&2
  echo "[vidzeo]   2. Set a real delivery target and enable the hook on the host Muse VM." >&2
  echo "[vidzeo]   3. Run ./setup.sh to install the script, then run doctor to check live answers." >&2
  echo "[vidzeo]   Full steps: docker/SETUP.md (\"Host Muse hook\")." >&2
  echo "[vidzeo]   Target is <60s per answer; the hard request expiry is 120s. Multi-minute waits are a fault." >&2
fi
if [ ! -f /data/.muse-agent.json ]; then
  echo "[vidzeo] WARNING: no worker credential at /data/.muse-agent.json — run ./setup.sh (pairing) first. The daemon will exit until paired." >&2
fi

exec node /app/muse-agent.js "$@"
