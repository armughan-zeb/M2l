#!/usr/bin/env bash
# Install the repository's health monitor in the host user's hooks/scripts
# folder and schedule it every minute so inbox requests cannot age past their
# 60-second operator SLO without detection. Re-running updates one cron row.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SCRIPT_DIR="$HOME/hooks/scripts"
LOG_DIR="$HOME/hooks/logs"
STATE_DIR="$HOME/hooks/state/vidnets-health-monitor"
DEST="$SCRIPT_DIR/vidnets-health-monitor.sh"
RUNNER="$SCRIPT_DIR/vidnets-health-monitor-run.sh"
RUNTIME="${VIDMON_RUNTIME_ROOT:-${VIDZEO_RUNTIME_ROOT:-$REPO/data/runtime}}"
BRIDGE="${VIDMON_BRIDGE_DIR:-${VIDNETS_AGENT_BRIDGE_DIR:-${VIDZEO_BRIDGE_ROOT:-$RUNTIME/temp/agent-bridge}}}"
HOOK_DEF="${VIDMON_HOOK_DEF:-${MUSE_INBOX_HOOK_DEFINITION:-$HOME/hooks/definitions/vidnets-inbox-watch.json}}"
HOOK_STATE_DIR="${HOST_HOOK_STATE_DIR:-$HOME/hooks/state/vidnets-inbox-watch}"

say() { printf '%s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
quote_for_bash() { printf '%q' "$1"; }

if ! command -v flock >/dev/null 2>&1; then
  warn 'Cannot install the monitor schedule safely: flock is required to prevent overlapping checks.'
  exit 1
fi

mkdir -p "$SCRIPT_DIR" "$LOG_DIR" "$STATE_DIR"
install -m 0755 "$REPO/scripts/vidnets-health-monitor.sh" "$DEST"
cat > "$RUNNER" <<EOF
#!/usr/bin/env bash
export VIDMON_REPO=$(quote_for_bash "$REPO")
export VIDMON_RUNTIME_ROOT=$(quote_for_bash "$RUNTIME")
export VIDMON_BRIDGE_DIR=$(quote_for_bash "$BRIDGE")
export VIDMON_HOOK_DEF=$(quote_for_bash "$HOOK_DEF")
export VIDMON_HOOK_STATE_DIR=$(quote_for_bash "$HOOK_STATE_DIR")
exec bash $(quote_for_bash "$DEST")
EOF
chmod 0755 "$RUNNER"

if ! command -v crontab >/dev/null 2>&1; then
  warn 'Health monitor installed, but crontab is unavailable. Schedule it with: * * * * * bash "$HOME/hooks/scripts/vidnets-health-monitor-run.sh"'
  exit 0
fi

CRON_LINE='* * * * * bash "$HOME/hooks/scripts/vidnets-health-monitor-run.sh" >/dev/null 2>&1'
CRON_TMP="$(mktemp)"
trap 'rm -f "$CRON_TMP"' EXIT
crontab -l 2>/dev/null | grep -Fv 'vidnets-health-monitor-run.sh' > "$CRON_TMP" || true
printf '%s\n' "$CRON_LINE" >> "$CRON_TMP"
crontab "$CRON_TMP"
say "[PASS] Installed health monitor at $DEST and scheduled it every minute (60-second inbox SLO)."
