#!/usr/bin/env bash
# Host monitor for the Muse worker daemon and Vidnets inbox.
#
# The daemon is supervised by systemd (muse-daemon.service). This monitor
# only OBSERVES — with one narrow exception: after a VM replacement wipes
# /etc, the unit file is gone, so the daemon never auto-starts on the
# fresh VM (the unit did not exist at boot). When the self-heal below
# restores the unit, it also starts the daemon — but only when no daemon
# process is running and no render is in flight. It never stops or
# restarts a running daemon, and it never starts one while a render runs.
# If the daemon is down for any other reason, this script reports ALERT
# and the parent restarts it with:
#   systemctl start muse-daemon
#
# The retired :5010 Vidnets server is gone (92ab202 deleted vidnets/main.py),
# so there is no :5010 probe and no VIDNETS_AGENT.sh restart path anymore,
# and no restart-loop guard: nothing here restarts a running daemon, so
# nothing can loop. Daemon health is: the systemd unit is active, OR the pid recorded
# in agent-service.json is alive as `node muse-agent.js start`.
set -uo pipefail

if [ -n "${HATCH_HOOK_RUNTIME:-}" ] && [ -f "$HATCH_HOOK_RUNTIME" ]; then
  # The same script can run as a periodic Muse hook or as a host cron job.
  # Hook helpers are absent in cron and are only used when explicitly supplied.
  source "$HATCH_HOOK_RUNTIME"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO="${VIDMON_REPO:-$(cd "$SCRIPT_DIR/.." && pwd -P)}"
RUNTIME="${VIDMON_RUNTIME_ROOT:-${VIDZEO_RUNTIME_ROOT:-$HOME/workspace/vidnets-runtime}}"
BRIDGE="${VIDMON_BRIDGE_DIR:-${VIDNETS_AGENT_BRIDGE_DIR:-${VIDZEO_BRIDGE_ROOT:-$RUNTIME/temp/agent-bridge}}}"
HOOK_DEF="${VIDMON_HOOK_DEF:-${MUSE_INBOX_HOOK_DEFINITION:-$HOME/hooks/definitions/vidnets-inbox-watch.json}}"
HOOK_STATE_DIR="${VIDMON_HOOK_STATE_DIR:-${MUSE_INBOX_HOOK_STATE_DIR:-$HOME/hooks/state/vidnets-inbox-watch}}"
STATE_DIR="${VIDMON_STATE_DIR:-$HOME/hooks/state/vidnets-health-monitor}"
CMS_URL="${VIDMON_CMS_URL:-${MUSE_CMS_URL:-https://cms.vidnets.com/api/muse}}"
DRY_RUN="${VIDMON_DRY_RUN:-0}"

mkdir -p "$STATE_DIR" 2>/dev/null || true
report_file="$STATE_DIR/last-report.txt"
monitor_log="$STATE_DIR/monitor.log"
needs_user=0

if ! command -v flock >/dev/null 2>&1; then
  printf '%s\n' 'HEALTH_MONITOR result=needs_user reason=flock_unavailable' >&2
  exit 2
fi
exec 9>"$STATE_DIR/monitor.lock"
if ! flock -n 9; then
  printf '%s\n' 'HEALTH_MONITOR result=already_running'
  exit 0
fi
: > "$report_file" 2>/dev/null || true

report() {
  printf '%s\n' "$*"
  printf '%s\n' "$*" >> "$report_file" 2>/dev/null || true
}
mark_problem() { needs_user=1; report "ALERT $*"; }

# --- daemon probe: systemd unit active, or the recorded pid is alive ---
DAEMON_HEALTH_VIA=''
daemon_pid_from_service() {
  python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1])).get("pid") or 0))' "$RUNTIME/secure/agent-service.json" 2>/dev/null || printf '0'
}

daemon_process_matches() {
  local pid="$1" cmdline
  case "$pid" in ''|*[!0-9]*|0|1) return 1 ;; esac
  [ -r "/proc/$pid/cmdline" ] || return 1
  cmdline="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
  [ -n "$cmdline" ] || return 1
  case "$cmdline" in *muse-agent.js*start*) return 0 ;; *) return 1 ;; esac
}

any_muse_agent_running() {
  # Any `node muse-agent.js start` process, not just the recorded pid:
  # on a fresh VM the recorded pid is stale, and a daemon started by
  # hand has no unit to report it. pgrep never matches itself.
  [ -n "$(pgrep -f 'muse-agent\.js[[:space:]]+start' 2>/dev/null || true)" ]
}

daemon_is_running() {
  # VM replacements wipe /etc: if the unit is gone, restore it from the
  # persistent copy in the workspace, then reload + enable. A wiped unit
  # also means the daemon never auto-started on the fresh VM (the unit
  # did not exist at boot), so — and only then — start it here, when no
  # daemon process is running and no render is in flight. This is the
  # monitor's one start path; it never stops or restarts a running
  # daemon, and it never starts one while a render runs.
  local unit_restored=0
  if [ ! -f /etc/systemd/system/muse-daemon.service ] && [ -f "$RUNTIME/secure/muse-daemon.service" ]; then
    cp "$RUNTIME/secure/muse-daemon.service" /etc/systemd/system/muse-daemon.service 2>/dev/null
    systemctl daemon-reload 2>/dev/null
    systemctl enable muse-daemon >/dev/null 2>&1
    unit_restored=1
  fi
  if [ "$unit_restored" = 1 ]; then
    if any_muse_agent_running; then
      DAEMON_HEALTH_VIA='muse-agent.js process already running (unit was restored)'
      return 0
    fi
    if live_render_child >/dev/null 2>&1; then
      report 'NOTE daemon: unit was missing (fresh VM?) — restored it, but a render is still running, so not starting the daemon'
    elif [ "$DRY_RUN" = 1 ]; then
      report 'NOTE daemon: unit was missing (fresh VM?) — restored it; dry run, not starting the daemon'
    elif systemctl start muse-daemon 2>/dev/null; then
      report 'NOTE daemon: unit was missing (fresh VM?) — restored it and started the daemon'
    else
      report 'WARN daemon: unit was missing (fresh VM?) — restored it, but systemctl start failed'
    fi
  fi
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet muse-daemon 2>/dev/null; then
    DAEMON_HEALTH_VIA='systemd unit muse-daemon active'
    return 0
  fi
  local pid
  pid="$(daemon_pid_from_service)"
  if daemon_process_matches "$pid"; then
    DAEMON_HEALTH_VIA="pid $pid (agent-service.json) is node muse-agent.js start"
    return 0
  fi
  return 1
}

has_active_job() {
  local marker
  for marker in "${MUSE_WORK_DIR:-}" "$REPO/work" "$REPO/data/work"; do
    [ -n "$marker" ] || continue
    marker="$marker/active-job.json"
    [ -s "$marker" ] && return 0
  done
  return 1
}

live_render_child() {
  # A render child can outlive a dead daemon (orphaned ffmpeg/remotion).
  # Informational only: the monitor never restarts a running daemon, and
  # a daemon restart reattaches to the saved job ID, so a live child never
  # blocks. A live child DOES block the fresh-VM start in
  # daemon_is_running() — the daemon must not start while a render runs.
  local procs
  procs="$(pgrep -af -f 'ffmpeg|remotion' 2>/dev/null || true)"
  [ -n "$procs" ] || return 1
  printf '%s\n' "$procs" | grep -q -F -e "$RUNTIME" -e "$REPO"
}

daemon_ok=0
if daemon_is_running; then
  daemon_ok=1
  report "NOTE daemon: running ($DAEMON_HEALTH_VIA)"
else
  if has_active_job; then
    mark_problem 'daemon: NOT RUNNING; saved job checkpoint exists — restart with: systemctl start muse-daemon (the monitor only starts it right after restoring a wiped unit on a fresh VM)'
  else
    mark_problem 'daemon: NOT RUNNING; restart with: systemctl start muse-daemon (the monitor only starts it right after restoring a wiped unit on a fresh VM)'
  fi
  if live_render_child; then
    report 'NOTE render: a live ffmpeg/remotion render child is still running (orphaned); a daemon restart reattaches by job ID'
  fi
fi

# --- CMS probe (short timeout; CMS flaps, so track outage duration) ---
cms_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "$CMS_URL" 2>/dev/null || true)"
if [[ ! "$cms_code" =~ ^[23][0-9][0-9]$ ]]; then
  since_file="$STATE_DIR/cms-unavailable-since"
  if [ ! -s "$since_file" ]; then date +%s > "$since_file"; fi
  since="$(cat "$since_file" 2>/dev/null || date +%s)"
  elapsed=$(( $(date +%s) - ${since:-0} ))
  threshold=1800
  has_active_job && threshold=300
  if [ "$elapsed" -ge "$threshold" ] && [ ! -f "$STATE_DIR/cms-outage-alerted" ]; then
    mark_problem "cms: unavailable (HTTP ${cms_code:-no response}) for ${elapsed}s; keep worker and job state, do not restart"
    : > "$STATE_DIR/cms-outage-alerted"
  else
    report "NOTE cms: unavailable (HTTP ${cms_code:-no response}) for ${elapsed}s; keep worker and job state, do not restart"
  fi
else
  rm -f "$STATE_DIR/cms-unavailable-since" 2>/dev/null || true
  rm -f "$STATE_DIR/cms-outage-alerted" 2>/dev/null || true
fi

# `status` sends the normal worker heartbeat and returns the connection state.
# Bound it tightly so a slow CMS never blocks the monitor.
if [ "$daemon_ok" = 1 ] && [[ "$cms_code" =~ ^[23][0-9][0-9]$ ]] && command -v timeout >/dev/null 2>&1 && command -v node >/dev/null 2>&1; then
  worker_status="$(cd "$REPO" && timeout 8s node muse-agent.js status 2>/dev/null || true)"
  if [ -n "$worker_status" ]; then
    connected="$(python3 -c 'import json,sys; print("true" if json.load(sys.stdin).get("connected") is True else "false")' <<< "$worker_status" 2>/dev/null || printf 'unknown')"
    active_worker="$(python3 -c 'import json,sys; print("true" if json.load(sys.stdin).get("activeWorker") is True else "false")' <<< "$worker_status" 2>/dev/null || printf 'unknown')"
    if [ "$connected" = false ]; then mark_problem 'daemon: process is alive but its CMS heartbeat is not connected'
    elif [ "$active_worker" = false ]; then mark_problem 'daemon: connected but this worker is not the CMS active worker; it cannot claim jobs'
    fi
  else
    report 'WARN daemon: bounded CMS connection probe returned no status'
  fi
fi

resident_ready=0
if [ -f "$BRIDGE/resident-status.json" ]; then
  if python3 -c 'import json, sys, time; age = time.time() - json.load(open(sys.argv[1]))["updatedAt"] / 1000; sys.exit(0 if -5 <= age <= 15 else 1)' "$BRIDGE/resident-status.json" 2>/dev/null; then
    resident_ready=1
    report 'NOTE inbox: resident dispatcher is running'
  fi
fi
if [ "$resident_ready" = 0 ] && [ -f "$HOOK_DEF" ]; then
  hook_enabled="$(python3 -c 'import json,sys; print("true" if json.load(open(sys.argv[1])).get("enabled") is True else "false")' "$HOOK_DEF" 2>/dev/null || printf 'unknown')"
  if [ "$hook_enabled" != true ]; then
    if [ "$hook_enabled" = false ]; then hook_state=DISABLED; else hook_state=UNKNOWN; fi
    mark_problem "hook: vidnets-inbox-watch is $hook_state"
  fi
elif [ "$resident_ready" = 0 ]; then
  mark_problem 'hook: vidnets-inbox-watch definition is missing'
fi
if [ "$resident_ready" = 0 ] && [ "${hook_enabled:-unknown}" = true ]; then
  last_run="$(cat "$HOOK_STATE_DIR/last-run" 2>/dev/null || true)"
  if [[ "$last_run" =~ ^[0-9]+$ ]]; then
    hook_age=$(( $(date +%s) - last_run ))
    if [ "$hook_age" -gt 120 ] || [ "$hook_age" -lt -30 ]; then
      mark_problem "hook: enabled but last execution proof is ${hook_age}s old (120s maximum)"
    fi
  else
    mark_problem "hook: enabled but execution proof is missing at $HOOK_STATE_DIR/last-run"
  fi
fi

if [ -d "$BRIDGE" ]; then
  bridge_report="$(python3 "$SCRIPT_DIR/bridge-health.py" "$BRIDGE" "$STATE_DIR" "$DRY_RUN" 2>&1 || true)"
  bridge_metrics_seen=0
  while IFS= read -r line; do
    case "$line" in
      METRICS_JSON:*) bridge_metrics_seen=1; report "bridge metrics ${line#METRICS_JSON:}" ;;
      WARN:*) report "WARN ${line#WARN:}" ;;
      ALERT:*) mark_problem "${line#ALERT:}" ;;
      *) [ -z "$line" ] || report "$line" ;;
    esac
  done <<< "$bridge_report"
  if [ "$bridge_metrics_seen" = 0 ]; then
    mark_problem "inbox metrics helper failed: ${bridge_report:0:200}"
  fi
elif [ -n "${MUSE_INFERENCE_COMMAND:-}" ]; then
  report 'NOTE inbox: resident Muse command transport is configured; filesystem inbox metrics are unavailable'
elif [ -n "${MUSE_INFERENCE_URL:-}" ]; then
  report 'NOTE inbox: resident Muse HTTP transport is configured; filesystem inbox metrics are unavailable'
else
  mark_problem "inbox: bridge directory is missing at $BRIDGE"
fi

temp_path="${VIDZEO_TEMP_DIR:-${TMPDIR:-$RUNTIME/temp}}"
for disk_path in "$temp_path" /tmp; do
  [ -d "$disk_path" ] || continue
  free_mb="$(df -Pm "$disk_path" 2>/dev/null | awk 'NR==2 {print $4}')"
  # /tmp is a 512M tmpfs and can never have 512MB free (~7MB always used);
  # use a reachable 256MB threshold there so the alert can actually clear.
  need_mb=512
  [ "$disk_path" = /tmp ] && need_mb=256
  if [[ "$free_mb" =~ ^[0-9]+$ ]] && [ "$free_mb" -lt "$need_mb" ]; then
    mark_problem "disk: $disk_path has only ${free_mb}MB free (need ${need_mb}MB)"
  fi
done

if [ "$needs_user" = 1 ]; then
  report 'HEALTH_MONITOR result=needs_user'
  if command -v wake >/dev/null 2>&1 && [ "$DRY_RUN" != 1 ]; then
    payload="$(python3 -c 'import json,sys; print(json.dumps({"report":sys.stdin.read()}))' < "$report_file" 2>/dev/null || printf '{}')"
    wake 'Vidzeo Muse health monitor needs attention' "${payload:-{}}"
  fi
  if [ -s "$report_file" ]; then
    { printf '\n[%s]\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"; cat "$report_file"; } >> "$monitor_log" 2>/dev/null || true
    if command -v tail >/dev/null 2>&1 && [ -f "$monitor_log" ]; then
      tail -n 500 "$monitor_log" > "$monitor_log.tmp" 2>/dev/null && mv "$monitor_log.tmp" "$monitor_log" || true
    fi
  fi
  exit 2
fi
report 'HEALTH_MONITOR result=ok'
if [ -s "$report_file" ]; then
  { printf '\n[%s]\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"; cat "$report_file"; } >> "$monitor_log" 2>/dev/null || true
  if command -v tail >/dev/null 2>&1 && [ -f "$monitor_log" ]; then
    tail -n 500 "$monitor_log" > "$monitor_log.tmp" 2>/dev/null && mv "$monitor_log.tmp" "$monitor_log" || true
  fi
fi
exit 0
