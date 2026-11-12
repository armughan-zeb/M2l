#!/usr/bin/env bash
# Start the CMS daemon with the hybrid Vidnets wiring.
# The same variables live in .env (loaded by lib/config.js); they are
# exported here too so a restart picks them up even if .env is missing.
# Usage: ./start-daemon.sh        (foreground, logs to stdout)
#        nohup ./start-daemon.sh >> daemon.log 2>&1 &
# This script is NOT a supervisor: if the daemon exits, nothing here
# restarts it. Post-reboot / crash recovery is the health monitor
# (~/hooks/scripts/vidnets-health-monitor.sh, every 5 min via the
# vidnets-health-monitor cron) — a systemd unit was ruled out because
# /etc does not survive a VM reboot here, while the cron + this script
# live under $HOME, which does. The daemon itself now survives CMS
# 502/530 outages in-process (lib/cmsRetry.js); it only exits on a
# revoked credential (401), a fatal start-gate failure, or SIGTERM/SIGINT.
set -euo pipefail
cd "$(dirname "$0")"
REPO_ROOT="$(pwd -P)"

export MUSE_AUTO_LOOP=1
export MUSE_INFERENCE_COMPULSORY=1
export MUSE_RENDER_COMMAND="${MUSE_RENDER_COMMAND:-node $(printf '%q' "$REPO_ROOT/render-vidnets-linux.js")}"
export MUSE_VIDNETS_ROOT="${MUSE_VIDNETS_ROOT:-$REPO_ROOT/vidnets}"
export MUSE_YOUTUBE_COVERAGE_TARGET="${MUSE_YOUTUBE_COVERAGE_TARGET:-0.30}"
export MUSE_MEDIA_STRICT="${MUSE_MEDIA_STRICT:-0}"
export MUSE_RENDER_CONCURRENCY="${MUSE_RENDER_CONCURRENCY:-1}"
export VIDZEO_TEMP_DIR="${VIDZEO_TEMP_DIR:-${VIDZEO_RUNTIME_ROOT:-$HOME/workspace/vidnets-runtime}/temp}"
export TMPDIR="${TMPDIR:-$VIDZEO_TEMP_DIR}"
mkdir -p "$TMPDIR"

# /tmp hygiene: /tmp is a 512 MB tmpfs and it has filled up before (stale
# scratch blocked bundle verification). The Vidnets runtime temp dir also
# accumulates render scratch (remotion-*/puppeteer_*/muse-plan-*/edge_*
# files, answered/expired inbox dirs) that no job ever re-reads. Prune
# only entries older than 7 days, and only the known scratch patterns —
# never temp/jobs (active Vidnets job work), media, caches, manifests, or
# any bridge request newer than 7 days (every live TTL is <= 30 min).
# Cleanup must never block the daemon start: failures are logged, not fatal.
cleanup_stale_scratch() {
  local temp_root="$VIDZEO_TEMP_DIR" removed=0
  if [ -d "$temp_root/agent-bridge" ]; then
    removed=$((removed + $(find "$temp_root/agent-bridge" -mindepth 1 -maxdepth 1 -type d -mtime +7 -print 2>/dev/null | wc -l)))
    find "$temp_root/agent-bridge" -mindepth 1 -maxdepth 1 -type d -mtime +7 -exec rm -rf {} + 2>/dev/null || true
  fi
  if [ -d "$temp_root" ]; then
    local n=0
    n=$(find "$temp_root" -mindepth 1 -maxdepth 1 -mtime +7 \
      \( -name 'remotion-v4*-assets*' -o -name 'puppeteer_dev_chrome_profile-*' \
         -o -name 'react-motion-render*' -o -name 'muse-plan-*' \
         -o -name 'web_images_simple_*' -o -name 'edge_chunk_*.mp3' \
         -o -name 'edge_tts_*.mp3' -o -name 'tmp*.json' -o -name '*.tmp' \) \
      -print 2>/dev/null | wc -l)
    removed=$((removed + n))
    find "$temp_root" -mindepth 1 -maxdepth 1 -mtime +7 \
      \( -name 'remotion-v4*-assets*' -o -name 'puppeteer_dev_chrome_profile-*' \
         -o -name 'react-motion-render*' -o -name 'muse-plan-*' \
         -o -name 'web_images_simple_*' -o -name 'edge_chunk_*.mp3' \
         -o -name 'edge_tts_*.mp3' -o -name 'tmp*.json' -o -name '*.tmp' \) \
      -exec rm -rf {} + 2>/dev/null || true
  fi
  # /tmp itself (tmpfs, cleared on reboot): only our own narrow patterns,
  # older than 1 day, so a hand-run bundle verification cannot refill it.
  local tmp_n=0
  tmp_n=$(find /tmp -mindepth 1 -maxdepth 1 -mtime +1 \
    \( -name 'vidnets-*' -o -name 'remotion-*' -o -name 'react-motion-render*' \
       -o -name '*bundle-test*' \) -print 2>/dev/null | wc -l)
  removed=$((removed + tmp_n))
  find /tmp -mindepth 1 -maxdepth 1 -mtime +1 \
    \( -name 'vidnets-*' -o -name 'remotion-*' -o -name 'react-motion-render*' \
       -o -name '*bundle-test*' \) -exec rm -rf {} + 2>/dev/null || true
  echo "[start-daemon] stale scratch cleanup: removed $removed entr(ies) older than 7 days (runtime temp) / 1 day (/tmp)" >&2
}
cleanup_stale_scratch || echo "[start-daemon] stale scratch cleanup failed (non-fatal)" >&2

exec node muse-agent.js start "$@"
