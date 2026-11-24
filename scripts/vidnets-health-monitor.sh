#!/usr/bin/env bash
# One-minute host monitor for the Muse worker and Vidnets inbox.
# Safe to run from a Muse hook or cron. It never restarts a live worker, and
# a dead worker may be restarted with a checkpoint because startup resumes the
# saved CMS job and the renderer reattaches to its saved Vidnets job ID.
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
VIDNETS_URL="${VIDMON_VIDNETS_URL:-}"
CMS_URL="${VIDMON_CMS_URL:-${MUSE_CMS_URL:-https://cms.vidnets.com}}"
DAEMON_PATTERN="${VIDMON_DAEMON_PATTERN:-node .*muse-agent.js start}"
DRY_RUN="${VIDMON_DRY_RUN:-0}"

mkdir -p "$STATE_DIR" 2>/dev/null || true
report_file="$STATE_DIR/last-report.txt"
monitor_log="$STATE_DIR/monitor.log"
needs_user=0
vidnets_ok=0
VIDNETS_STATUS=''

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
mark_problem() { needs_user=1; report "$*"; }

# Restart-loop guard: consecutive failed restarts of one service.
# A crash-looping service must not be restarted every run forever, but
# the guard must never deadlock recovery: after VIDMON_MAX_RESTART_FAILURES
# (default 3) consecutive failures, restarts pause for
# VIDMON_RESTART_COOLDOWN_SECONDS (default 1800s); once the cooldown
# passes, one retry is allowed, and a failure re-arms the guard. Any
# healthy observation resets the count.
MAX_RESTART_FAILURES="${VIDMON_MAX_RESTART_FAILURES:-3}"
RESTART_COOLDOWN_SECONDS="${VIDMON_RESTART_COOLDOWN_SECONDS:-1800}"
restart_failures_file() { printf '%s/%s-restart-failures' "$STATE_DIR" "$1"; }
restart_failure_state() {
  local file raw count last
  file="$(restart_failures_file "$1")"
  raw="$(cat "$file" 2>/dev/null || true)"
  count="${raw%% *}"
  last="${raw##* }"
  case "$count" in ''|*[!0-9]*) count=0 ;; esac
  case "$last" in ''|*[!0-9]*) last=0 ;; esac
  printf '%s %s' "$count" "$last"
}
restart_failure_count() {
  local count _last
  read -r count _last <<< "$(restart_failure_state "$1")"
  printf '%s' "$count"
}
note_restart_failure() {
  printf '%s %s' "$(( $(restart_failure_count "$1") + 1 ))" "$(date +%s)" > "$(restart_failures_file "$1")" 2>/dev/null || true
}
reset_restart_failures() { rm -f "$(restart_failures_file "$1")" 2>/dev/null || true; }
restart_loop_guarded() {
  local count last now
  read -r count last <<< "$(restart_failure_state "$1")"
  now="$(date +%s)"
  [ "$count" -ge "$MAX_RESTART_FAILURES" ] && [ $(( now - last )) -lt "$RESTART_COOLDOWN_SECONDS" ]
}

live_render_child() {
  # A render child can outlive a dead server (orphaned ffmpeg/remotion)
  # and has no active-job marker when the job was submitted locally, so
  # the marker alone cannot prove no render is running. Same definition
  # as the debugger's has_live_render_child (ffmpeg / remotion in the
  # command line), scoped to this worker's repo/runtime paths so an
  # unrelated ffmpeg on the host does not block recovery.
  local procs
  procs="$(pgrep -af -f 'ffmpeg|remotion' 2>/dev/null || true)"
  [ -n "$procs" ] || return 1
  # Path-scoped on purpose: on a shared host another worker's render
  # must not block this worker's recovery, so only a render child
  # working under this repo/runtime counts.
  printf '%s\n' "$procs" | grep -q -F -e "$RUNTIME" -e "$REPO"
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

docker_worker_managed() {
  [ "${VIDMON_INSTALL_MODE:-auto}" != native ] || return 1
  [ -f "$REPO/docker-compose.yml" ] && command -v docker >/dev/null 2>&1 \
    && docker compose version >/dev/null 2>&1
}

docker_worker_running() {
  docker_worker_managed || return 1
  docker compose -f "$REPO/docker-compose.yml" ps --status running -q worker 2>/dev/null | grep -q .
}

daemon_is_running() {
  if docker_worker_managed; then
    if ! docker_worker_running; then return 1; fi
    # The host cannot see container PIDs. Check the worker's own durable lock
    # and verify its command line so a recycled PID does not look healthy.
    docker compose -f "$REPO/docker-compose.yml" exec -T worker node -e '
      const fs = require("fs"), path = require("path");
      try {
        const root = process.env.MUSE_WORK_DIR || path.join(process.cwd(), "work");
        const row = JSON.parse(fs.readFileSync(path.join(root, "worker.lock"), "utf8"));
        const pid = Number(row.pid);
        if (!Number.isSafeInteger(pid) || pid <= 1) process.exit(1);
        process.kill(pid, 0);
        const cmd = fs.readFileSync(`/proc/${pid}/cmdline`, "utf8").replace(/\0/g, " ");
        if (!/muse-agent\.js\s+start/.test(cmd)) process.exit(1);
      } catch { process.exit(1); }
    ' >/dev/null 2>&1
    return $?
  fi
  pgrep -af -- "$DAEMON_PATTERN" >/dev/null 2>&1
}

vidnets_service_state() {
  local descriptor="$RUNTIME/secure/agent-service.json"
  if docker_worker_managed; then
    if ! docker_worker_running; then
      # A stopped single-service container has no Vidnets process. Compose's
      # restart path brings back both services from the same /data queue.
      printf 'dead\n'
      return 0
    fi
    docker compose -f "$REPO/docker-compose.yml" exec -T worker python3 -c '
import json, os, sys
try:
    row = json.load(open("/data/runtime/secure/agent-service.json", encoding="utf-8"))
    root = os.path.realpath(str(row.get("resource_root") or ""))
    pid = int(row.get("pid") or 0)
    if not root or pid <= 1:
        print("unknown"); raise SystemExit(0)
except Exception:
    print("unknown"); raise SystemExit(0)
expected = os.path.join(root, "main.py")
uncertain = False
for name in os.listdir("/proc"):
    if not name.isdigit():
        continue
    try:
        stat = open(f"/proc/{name}/stat", encoding="utf-8").read()
        if stat[stat.rfind(")") + 1:].lstrip().startswith("Z "):
            continue
        command = open(f"/proc/{name}/cmdline", "rb").read().split(b"\0")
        if os.fsencode(expected) in command:
            print("alive"); raise SystemExit(0)
    except FileNotFoundError:
        continue
    except PermissionError:
        uncertain = True
if uncertain:
    print("unknown")
else:
    print("dead")
' 2>/dev/null || printf 'unknown\n'
    return 0
  fi
  python3 -c '
import json, os, sys
try:
    row = json.load(open(sys.argv[1], encoding="utf-8"))
    root = os.path.realpath(str(row.get("resource_root") or ""))
    pid = int(row.get("pid") or 0)
    if not root or pid <= 1:
        print("unknown"); raise SystemExit(0)
except FileNotFoundError:
    print("unknown"); raise SystemExit(0)
except Exception:
    print("unknown"); raise SystemExit(0)
expected = os.path.join(root, "main.py")
uncertain = False
for name in os.listdir("/proc"):
    if not name.isdigit():
        continue
    try:
        stat = open(f"/proc/{name}/stat", encoding="utf-8").read()
        if stat[stat.rfind(")") + 1:].lstrip().startswith("Z "):
            continue
        command = open(f"/proc/{name}/cmdline", "rb").read().split(b"\0")
        if os.fsencode(expected) in command:
            print("alive"); raise SystemExit(0)
    except FileNotFoundError:
        continue
    except PermissionError:
        uncertain = True
if uncertain:
    print("unknown")
else:
    print("dead")
' "$descriptor" 2>/dev/null || printf 'unknown\n'
}

probe_vidnets() {
  local status url port
  url="$VIDNETS_URL"
  if [ -z "$url" ] && [ -s "$RUNTIME/secure/agent-service.json" ]; then
    port="$(python3 -c 'import json,sys; p=int(json.load(open(sys.argv[1])).get("port",0)); print(p if 1 <= p <= 65535 else "")' "$RUNTIME/secure/agent-service.json" 2>/dev/null || true)"
    [ -z "$port" ] || url="http://127.0.0.1:$port"
  fi
  [ -n "$url" ] || url='http://127.0.0.1:5010'
  status="$(curl -fsS --max-time 4 "$url/api/status" 2>/dev/null || true)"
  if [ -z "$status" ] && docker_worker_managed; then
    port="$(docker compose -f "$REPO/docker-compose.yml" exec -T worker python3 -c 'import json; print(json.load(open("/data/runtime/secure/agent-service.json"))["port"])' 2>/dev/null || true)"
    if [[ "$port" =~ ^[0-9]{1,5}$ ]] && [ "$port" -ge 1 ] && [ "$port" -le 65535 ]; then
      status="$(docker compose -f "$REPO/docker-compose.yml" exec -T worker curl -fsS --max-time 4 "http://127.0.0.1:$port/api/status" 2>/dev/null || true)"
    fi
  fi
  [ -n "$status" ] || return 1
  python3 -c 'import json,sys; row=json.load(sys.stdin); raise SystemExit(0 if row.get("ready") is True else 1)' <<< "$status" >/dev/null 2>&1 || return 1
  VIDNETS_STATUS="$status"
  return 0
}

restart_vidnets_if_safe() {
  local service_state
  service_state="$(vidnets_service_state | tail -n 1)"
  # The server process being alive blocks a restart even with no active
  # job: starting a second server while the first still holds :5010 makes
  # the new one bind the next port and split the queue. (Before, this
  # check only ran when an active-job marker existed, so a hung server
  # mid-render on a locally-submitted job could be restarted.)
  if [ "$service_state" != dead ] && { [ "$service_state" = alive ] || has_active_job; }; then
    if has_active_job; then
      mark_problem "vidnets: DOWN; active job checkpoint exists and service process state is ${service_state}; restart withheld because a live render cannot be ruled out"
    else
      mark_problem "vidnets: DOWN; server process is still alive but not answering; restart withheld (starting a second server would split the queue)"
    fi
    return
  fi
  # A live render child with no active-job marker (locally-submitted job,
  # orphaned ffmpeg after a server crash) must also block the restart.
  if live_render_child; then
    mark_problem 'vidnets: DOWN; a live ffmpeg/remotion render child is still running; restart withheld so the render is not killed'
    return
  fi
  if restart_loop_guarded vidnets; then
    mark_problem "vidnets: DOWN; restart loop guard: $(restart_failure_count vidnets) consecutive restarts failed; not retrying until the cooldown passes or Vidnets is healthy once"
    return
  fi
  if [ "$DRY_RUN" = 1 ]; then
    if has_active_job; then
      mark_problem 'vidnets: DOWN; service process is confirmed dead; WOULD restart and reattach the saved Vidnets job'
    else
      mark_problem 'vidnets: DOWN; WOULD restart'
    fi
    return
  fi
  local restarted=0
  if docker_worker_managed; then
    if (cd "$REPO" && docker compose exec -T worker bash /app/vidnets/VIDNETS_AGENT.sh --runtime-root /data/runtime start >/dev/null 2>&1); then
      restarted=1
    elif (cd "$REPO" && docker compose up -d worker >/dev/null 2>&1); then
      restarted=1
    fi
  elif [ -f "$REPO/vidnets/VIDNETS_AGENT.sh" ]; then
    bash "$REPO/vidnets/VIDNETS_AGENT.sh" --runtime-root "$RUNTIME" start >/dev/null 2>&1 && restarted=1
  fi
  if [ "$restarted" = 1 ] && probe_vidnets; then
    reset_restart_failures vidnets
    if has_active_job; then
      report 'vidnets: service recovered; saved queue job will reattach by its existing Vidnets job ID'
    else
      report 'vidnets: service recovered; restart succeeded'
    fi
    vidnets_ok=1
  else
    note_restart_failure vidnets
    mark_problem 'vidnets: DOWN; restart failed; inspect Vidnets logs'
  fi
}

if probe_vidnets; then
  vidnets_ok=1
  reset_restart_failures vidnets
else
  restart_vidnets_if_safe
fi

if [ "$vidnets_ok" = 1 ]; then
  memory_state="$(python3 -c 'import json,sys; print((json.load(sys.stdin).get("memory") or {}).get("admission_state", "unknown"))' <<< "$VIDNETS_STATUS" 2>/dev/null || printf 'unknown')"
  available_mb="$(python3 -c 'import json,sys; print(int((json.load(sys.stdin).get("memory") or {}).get("available_mb") or 0))' <<< "$VIDNETS_STATUS" 2>/dev/null || printf '0')"
  minimum_mb="$(python3 -c 'import json,sys; print(int((json.load(sys.stdin).get("memory") or {}).get("minimum_available_mb") or 384))' <<< "$VIDNETS_STATUS" 2>/dev/null || printf '384')"
  if [ "$memory_state" = waiting ] && has_active_job; then
    mark_problem "vidnets: active render waiting for memory (${available_mb}MB available; ${minimum_mb}MB minimum)"
  elif [ "$memory_state" = low_memory ]; then
    report "NOTE vidnets: low-memory mode (${available_mb}MB available); render concurrency remains bounded"
  elif [ "$memory_state" = unknown ]; then
    report 'WARN vidnets: memory admission state is unknown'
  fi
fi

if daemon_is_running; then
  :
elif [ "$vidnets_ok" = 0 ]; then
  mark_problem 'daemon: NOT RUNNING and Vidnets is down'
elif restart_loop_guarded daemon; then
  mark_problem "daemon: NOT RUNNING; restart loop guard: $(restart_failure_count daemon) consecutive restarts failed; not retrying until the cooldown passes or the daemon is healthy once"
elif [ "$DRY_RUN" = 1 ]; then
  if has_active_job; then
    mark_problem 'daemon: NOT RUNNING; WOULD restart and resume the saved job checkpoint'
  else
    mark_problem 'daemon: NOT RUNNING; WOULD restart'
  fi
elif docker_worker_managed; then
  if docker_worker_running; then
    # The container can remain up while its daemon is missing. Exec the normal
    # entrypoint in that container; its worker lock prevents a duplicate start.
    if ! docker compose -f "$REPO/docker-compose.yml" exec -T -d worker bash -lc \
      'cd /app && ./docker/entrypoint.sh start >> /data/daemon.log 2>&1 </dev/null'; then
      note_restart_failure daemon
      mark_problem 'daemon: restart in running worker container failed; inspect /data/daemon.log'
    elif has_active_job; then
      report 'daemon: restarted inside worker container; saved job checkpoint will resume'
    else
      report 'daemon: restarted inside worker container'
    fi
  elif ! docker compose -f "$REPO/docker-compose.yml" up -d worker >/dev/null 2>&1; then
    note_restart_failure daemon
    mark_problem 'daemon: container restart failed; inspect docker compose logs worker'
  elif has_active_job; then
    report 'daemon: worker container restarted; saved job checkpoint will resume'
  else
    report 'daemon: worker container restarted'
  fi
elif [ -x "$REPO/start-daemon.sh" ]; then
  if ! (cd "$REPO" && nohup ./start-daemon.sh >> "$REPO/daemon.log" 2>&1 </dev/null &); then
    note_restart_failure daemon
    mark_problem 'daemon: restart failed; inspect daemon.log'
  elif has_active_job; then
    report 'daemon: restarted; saved job checkpoint will resume'
  else
    report 'daemon: restarted'
  fi
else
  mark_problem 'daemon: NOT RUNNING; no worker start command is available'
fi

# A daemon alive after this block means the last restart (if any) worked:
# clear the loop-guard count. A failed attempt is counted at its own
# failure branch above, so a run that never attempted a restart counts
# nothing, and a healthy daemon always resets the guard.
if daemon_is_running; then
  reset_restart_failures daemon
fi

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
# Bound it tightly so a slow CMS never blocks the monitor or triggers a restart.
if [ "$vidnets_ok" = 1 ] && [[ "$cms_code" =~ ^[23][0-9][0-9]$ ]] && command -v timeout >/dev/null 2>&1 && command -v node >/dev/null 2>&1; then
  if docker_worker_managed && docker_worker_running; then
    worker_status="$(docker compose -f "$REPO/docker-compose.yml" exec -T worker node /app/muse-agent.js status 2>/dev/null || true)"
  else
    worker_status="$(cd "$REPO" && timeout 8s node muse-agent.js status 2>/dev/null || true)"
  fi
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
  if [[ "$free_mb" =~ ^[0-9]+$ ]] && [ "$free_mb" -lt 512 ]; then
    mark_problem "disk: $disk_path has only ${free_mb}MB free (need 512MB)"
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
if [ "$DRY_RUN" = 1 ]; then report 'HEALTH_MONITOR result=healthy'; fi
if [ -s "$report_file" ]; then
  { printf '\n[%s]\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"; cat "$report_file"; } >> "$monitor_log" 2>/dev/null || true
  if command -v tail >/dev/null 2>&1 && [ -f "$monitor_log" ]; then
    tail -n 500 "$monitor_log" > "$monitor_log.tmp" 2>/dev/null && mv "$monitor_log.tmp" "$monitor_log" || true
  fi
fi
exit 0
