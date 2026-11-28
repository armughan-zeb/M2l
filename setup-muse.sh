#!/usr/bin/env bash
# setup-muse.sh — one-shot setup for a fresh Muse account on a fresh device.
#
# Clones are cheap; old job state is not. This script installs everything the
# Vidzeo/M2l daemon needs, in order, and refuses to start the daemon on top
# of stale work. It never deletes job state: when it finds any, it aborts and
# tells the operator how to archive it first.
#
# Usage:
#   ./setup-muse.sh           Full setup: verify, install, enable, start.
#   ./setup-muse.sh --check   Dry run: verify everything, install/start
#                             nothing. Exit 0 = a full run would succeed.
#                             Exit 1 = list of what would fail, and why.
#
# Run as root (the systemd unit runs as User=root; there is no `hatch`
# user on the VM). Do NOT run this while the live daemon is mid-job unless
# you mean to restart it — a full run starts (or leaves running) the daemon.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
TEMPLATE_DIR="$REPO_ROOT/one_minute_documentary/templates/remotion-project"
VENV_DIR="$REPO_ROOT/vidnets/.venv"
VENV_PY="$VENV_DIR/bin/python"
UNIT_SRC="$HOME/workspace/vidnets-runtime/secure/muse-daemon.service"
UNIT_DST="/etc/systemd/system/muse-daemon.service"
CMS_URL="https://cms.vidnets.com"
CMS_PROBE="$CMS_URL/api/muse"
DAEMON_LOG="$REPO_ROOT/daemon.log"
ACTIVE_JOB="$REPO_ROOT/work/active-job.json"
LEGACY_QUEUE_DIR="$HOME/workspace/vidnets-runtime/server"
ARCHIVE_DIR="$HOME/workspace/vidnets-runtime/server-archive-20261128"

CHECK_MODE=0
if [[ "${1:-}" == "--check" ]]; then
  CHECK_MODE=1
elif [[ -n "${1:-}" ]]; then
  echo "Usage: $0 [--check]" >&2
  exit 2
fi

FAILURES=()
WARNINGS=()

log()  { printf '%s\n' "[setup-muse] $*"; }
ok()   { printf '%s\n' "[setup-muse] [OK]   $*"; }
warn() { printf '%s\n' "[setup-muse] [WARN] $*"; WARNINGS+=("$*"); }
fail() {
  printf '%s\n' "[setup-muse] [FAIL] $*"
  FAILURES+=("$*")
  return 1
}

# In full mode a hard failure stops the run at once. In --check mode every
# verification still runs so the operator gets the complete failure list.
hard_fail() {
  fail "$1" || true
  if [[ "$CHECK_MODE" -eq 0 ]]; then
    log "Aborting: $1"
    exit 1
  fi
}

# --- Step 1: prerequisites -------------------------------------------------
step_prereqs() {
  log "Step 1/7: prerequisites (node, python3, ffmpeg, curl, systemctl)"

  if [[ "$(id -u)" -ne 0 ]]; then
    hard_fail "must run as root (the daemon unit runs as User=root; there is no 'hatch' user on this VM). Re-run with sudo or as root."
    return
  fi
  ok "running as root"

  if ! command -v node >/dev/null 2>&1; then
    hard_fail "node is not installed. Install Node 18.17+ (this VM runs Node 24) before re-running."
    return
  fi
  local node_ver node_major node_minor
  node_ver="$(node --version | sed 's/^v//')"
  node_major="${node_ver%%.*}"
  node_minor="${node_ver#*.}"; node_minor="${node_minor%%.*}"
  if [[ "$node_major" -lt 18 || ( "$node_major" -eq 18 && "$node_minor" -lt 17 ) ]]; then
    hard_fail "node $node_ver is too old; Node 18.17+ is required."
  else
    ok "node $node_ver"
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    hard_fail "python3 is not installed. Install Python 3.12+ before re-running."
  else
    ok "python3 $(python3 --version 2>&1 | awk '{print $2}')"
  fi

  if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
    hard_fail "ffmpeg/ffprobe not found. Install ffmpeg before re-running."
  else
    ok "ffmpeg $(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')"
  fi

  if ! command -v npm >/dev/null 2>&1; then
    hard_fail "npm is not installed (ships with Node). Install Node 18.17+ before re-running."
  else
    ok "npm $(npm --version)"
  fi

  if ! command -v curl >/dev/null 2>&1; then
    hard_fail "curl is not installed. Install curl before re-running (used for the CMS probe)."
  else
    ok "curl present"
  fi

  if ! command -v systemctl >/dev/null 2>&1; then
    hard_fail "systemctl is not available (no systemd). The daemon runs as a systemd unit; install it on a systemd host."
  else
    ok "systemctl present"
  fi

  if [[ ! -x "$REPO_ROOT/start-daemon.sh" ]]; then
    hard_fail "start-daemon.sh is missing or not executable at $REPO_ROOT/start-daemon.sh"
  else
    ok "start-daemon.sh present and executable"
  fi

  # yt-dlp availability: the repo venv python is the ONLY python with it;
  # system python3 lacks it and kills every YouTube search instantly.
  if [[ -x "$VENV_PY" ]] && "$VENV_PY" -c "import yt_dlp" 2>/dev/null; then
    ok "yt-dlp available in repo venv ($VENV_PY)"
  elif python3 -c "import yt_dlp" 2>/dev/null; then
    ok "yt-dlp available in system python3 (venv still preferred; step 3 verifies the venv)"
  else
    # Not fatal here: step 3 creates/repairs the venv and installs yt-dlp.
    # In --check mode it is a real failure — a full run would try to fix it.
    if [[ "$CHECK_MODE" -eq 1 ]]; then
      fail "yt-dlp is not importable from the repo venv or system python3 (step 3 would install it into vidnets/.venv)"
    else
      warn "yt-dlp not importable yet; step 3 will install it into vidnets/.venv"
    fi
  fi
}

# --- Step 2: Remotion template node_modules, OFFLINE only -------------------
step_template_deps() {
  log "Step 2/7: Remotion template deps (OFFLINE npm install)"
  local marker_cli="$TEMPLATE_DIR/node_modules/@remotion/cli"
  local marker_bin="$TEMPLATE_DIR/node_modules/.bin/remotion"

  if [[ -d "$marker_cli" && -x "$marker_bin" ]]; then
    ok "template node_modules already installed ($(find "$TEMPLATE_DIR/node_modules" -maxdepth 1 -mindepth 1 2>/dev/null | wc -l) top-level packages)"
    return
  fi

  if [[ "$CHECK_MODE" -eq 1 ]]; then
    fail "template node_modules missing ($marker_cli not found). A full run would install them offline; the npm cache must hold the tarballs first (see below)."
    return
  fi

  # The npm registry is blocked from this VM. Never fall back to a network
  # install silently — that would hang or fail and hide the real problem.
  local cache_dir
  cache_dir="$(npm config get cache 2>/dev/null || echo "$HOME/.npm")"
  if [[ ! -d "$cache_dir" ]] || [[ -z "$(find "$cache_dir" -type f 2>/dev/null | head -1)" ]]; then
    log "ERROR: npm cache is empty (checked $cache_dir)."
    log "  To populate it, run this once on a machine where the npm registry answers:"
    log "    cd $TEMPLATE_DIR && npm install --no-audit --no-fund"
    log "  Then copy that machine's npm cache (~/.npm) to this device and re-run $0."
    log "  (On this VM the populated cache is $HOME/.npm, ~700 MB, 1700+ entries.)"
    exit 1
  fi
  ok "npm cache populated at $cache_dir"

  log "Installing template deps offline (npm install --offline)..."
  if (cd "$TEMPLATE_DIR" && npm install --offline --no-audit --no-fund); then
    ok "template deps installed offline"
  else
    log "ERROR: 'npm install --offline' failed in $TEMPLATE_DIR."
    log "  The cache may be missing tarballs for this template's lockfile."
    log "  Repopulate the cache on a registry-reachable machine (see above) and re-run."
    exit 1
  fi

  if [[ -d "$marker_cli" && -x "$marker_bin" ]]; then
    ok "verified node_modules/@remotion/cli and node_modules/.bin/remotion"
  else
    log "ERROR: offline install finished but the Remotion markers are still missing."
    exit 1
  fi
}

# --- Step 3: repo venv with yt-dlp ------------------------------------------
step_venv() {
  log "Step 3/7: repo venv with yt-dlp (vidnets/.venv)"

  if [[ ! -x "$VENV_PY" ]]; then
    if [[ "$CHECK_MODE" -eq 1 ]]; then
      fail "repo venv missing ($VENV_PY not found). A full run would create it with 'python3 -m venv vidnets/.venv' and install yt-dlp into it."
      return
    fi
    log "Creating repo venv: python3 -m venv vidnets/.venv"
    if ! python3 -m venv "$VENV_DIR"; then
      log "ERROR: failed to create the venv at $VENV_DIR"
      exit 1
    fi
    ok "venv created"
  else
    ok "venv exists ($VENV_PY)"
  fi

  if "$VENV_PY" -c "import yt_dlp" 2>/dev/null; then
    ok "yt-dlp importable from the repo venv"
    return
  fi

  if [[ "$CHECK_MODE" -eq 1 ]]; then
    fail "yt-dlp is not importable from the repo venv. A full run would install it with '$VENV_PY -m pip install yt-dlp'."
    return
  fi

  log "Installing yt-dlp into the repo venv..."
  if ! "$VENV_PY" -m pip install --quiet yt-dlp; then
    log "ERROR: pip failed to install yt-dlp into $VENV_DIR."
    log "  Check network/proxy access, then re-run. System python3 has no yt-dlp and cannot substitute."
    exit 1
  fi
  if "$VENV_PY" -c "import yt_dlp" 2>/dev/null; then
    ok "yt-dlp installed and importable from the repo venv"
  else
    log "ERROR: yt-dlp install reported success but 'import yt_dlp' still fails in the venv."
    exit 1
  fi
}

# --- Step 4: systemd unit ----------------------------------------------------
step_unit() {
  log "Step 4/7: systemd unit (from the persistent copy)"

  if [[ ! -f "$UNIT_SRC" ]]; then
    hard_fail "persistent unit copy missing: $UNIT_SRC. Without it the unit cannot be reinstalled after a VM replacement (which wipes /etc). Restore the copy first."
    return
  fi
  ok "persistent unit copy present: $UNIT_SRC"

  if ! grep -q '^User=root$' "$UNIT_SRC"; then
    hard_fail "unit copy does not run as User=root ($UNIT_SRC). The VM has no 'hatch' user; fix the copy before installing."
    return
  fi
  if ! grep -q 'ExecStart=/home/hatch/workspace/M2l/start-daemon.sh' "$UNIT_SRC"; then
    hard_fail "unit copy ExecStart is not start-daemon.sh ($UNIT_SRC). The daemon must run via start-daemon.sh, never bare 'node muse-agent.js start'."
    return
  fi
  ok "unit copy runs as User=root via start-daemon.sh"

  if [[ "$CHECK_MODE" -eq 1 ]]; then
    if [[ -f "$UNIT_DST" ]] && cmp -s "$UNIT_SRC" "$UNIT_DST"; then
      ok "unit already installed in /etc and identical to the persistent copy"
    elif [[ -f "$UNIT_DST" ]]; then
      warn "installed unit at $UNIT_DST differs from the persistent copy; a full run would overwrite it"
    else
      warn "unit not yet installed in /etc; a full run would copy it from the persistent copy"
    fi
    if systemctl is-enabled muse-daemon >/dev/null 2>&1; then
      ok "muse-daemon is enabled"
    else
      warn "muse-daemon is not enabled; a full run would enable it"
    fi
    return
  fi

  log "Installing unit: cp $UNIT_SRC $UNIT_DST"
  if ! cp "$UNIT_SRC" "$UNIT_DST"; then
    log "ERROR: could not copy the unit to /etc/systemd/system/ (are you root?)"
    exit 1
  fi
  systemctl daemon-reload
  systemctl enable muse-daemon >/dev/null
  ok "unit installed, daemon-reloaded, enabled"
}

# --- Step 5: CMS connectivity (warn, never fail) ------------------------------
step_cms() {
  log "Step 5/7: CMS connectivity (warn-only: the daemon runs jobs locally anyway)"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$CMS_PROBE" 2>/dev/null || echo 000)"
  case "$code" in
    2*|3*|4*)
      ok "CMS reachable: GET $CMS_PROBE -> HTTP $code"
      ;;
    5*)
      warn "CMS answered HTTP $code (often down / 530). Daemon runs jobs locally and syncs best-effort; no action needed."
      ;;
    *)
      warn "CMS unreachable (curl -> $code). Daemon runs jobs locally and syncs best-effort; no action needed."
      ;;
  esac
}

# --- Step 6: refuse to start on old job state ---------------------------------
# "Current job" = the job the live daemon is actually working right now:
# active-job.json names it AND the daemon is active AND the job id shows up
# in the daemon log. Anything else is stale state from a previous device/run.
is_current_job() {
  local job_id="$1"
  [[ -n "$job_id" ]] || return 1
  systemctl is-active --quiet muse-daemon 2>/dev/null || return 1
  [[ -f "$DAEMON_LOG" ]] || return 1
  tail -n 5000 "$DAEMON_LOG" 2>/dev/null | grep -qF "$job_id"
}

step_no_old_jobs() {
  log "Step 6/7: no old job state (never auto-deleted)"

  if [[ -f "$ACTIVE_JOB" ]]; then
    local job_id
    job_id="$(python3 -c "import json,sys; print(json.load(open('$ACTIVE_JOB')).get('jobId',''))" 2>/dev/null || true)"
    if [[ -z "$job_id" ]]; then
      job_id="$(grep -o '"jobId"[[:space:]]*:[[:space:]]*"[^"]*"' "$ACTIVE_JOB" 2>/dev/null | head -1 | sed 's/.*"\([^"]*\)"$/\1/')"
    fi
    if is_current_job "$job_id"; then
      ok "work/active-job.json names the CURRENT job ($job_id) the live daemon is working; setup may proceed, the daemon will resume it"
    else
      hard_fail "stale job state: work/active-job.json exists (job ${job_id:-unknown}) and it is NOT the job the live daemon is working. Archive it first, then re-run: mkdir -p work/archive && mv work/active-job.json work/archive/active-job-${job_id:-unknown}-$(date +%Y%m%d).json — never delete job state."
      return
    fi
  else
    ok "no work/active-job.json (fresh start, no saved job)"
  fi

  # The retired Vidnets server queue is dead state, not a job source.
  local legacy_found=0
  local legacy_path
  for legacy_path in "$LEGACY_QUEUE_DIR/queue.json" "$LEGACY_QUEUE_DIR/queue.json.bak" "$LEGACY_QUEUE_DIR"/.queue-*.tmp; do
    [[ -e "$legacy_path" ]] || continue
    legacy_found=1
    break
  done
  if [[ "$legacy_found" -eq 1 ]]; then
    hard_fail "legacy server queue state still in the live path: $legacy_path (and possibly queue.json / queue.json.bak / .queue-*.tmp under $LEGACY_QUEUE_DIR). Archive it out first, e.g.: mv $LEGACY_QUEUE_DIR/queue.json* $ARCHIVE_DIR/ — the daemon must never be pointed at the retired queue."
  else
    ok "no legacy server queue in $LEGACY_QUEUE_DIR"
  fi
}

# --- Step 7: start the daemon --------------------------------------------------
step_start() {
  log "Step 7/7: start the daemon (systemctl)"

  if [[ "$CHECK_MODE" -eq 1 ]]; then
    if systemctl is-active --quiet muse-daemon 2>/dev/null; then
      ok "daemon already active (a full run would leave it running and verify it)"
    else
      ok "daemon not active; a full run would start it with 'systemctl start muse-daemon'"
    fi
    return
  fi

  if systemctl is-active --quiet muse-daemon 2>/dev/null; then
    log "Daemon already active; leaving it running (a restart would interrupt a live job)."
  else
    log "Starting daemon: systemctl start muse-daemon"
    if ! systemctl start muse-daemon; then
      log "ERROR: 'systemctl start muse-daemon' failed. Check 'systemctl status muse-daemon' and $DAEMON_LOG."
      exit 1
    fi
  fi

  local waited=0
  while [[ "$waited" -lt 30 ]]; do
    systemctl is-active --quiet muse-daemon 2>/dev/null && break
    sleep 2
    waited=$((waited + 2))
  done
  if ! systemctl is-active --quiet muse-daemon 2>/dev/null; then
    log "ERROR: muse-daemon is not active 30s after start. Check 'systemctl status muse-daemon' and $DAEMON_LOG."
    exit 1
  fi
  ok "muse-daemon is active"

  local main_pid
  main_pid="$(systemctl show -p MainPID --value muse-daemon 2>/dev/null || echo 0)"
  if [[ "$main_pid" != "0" && -n "$main_pid" ]] && ps -p "$main_pid" -o args= 2>/dev/null | grep -q "muse-agent.js start"; then
    ok "daemon pid $main_pid is running 'node muse-agent.js start'"
  else
    log "ERROR: daemon is active but pid $main_pid is not running 'node muse-agent.js start'."
    exit 1
  fi

  if [[ -f "$DAEMON_LOG" ]]; then
    local log_age
    log_age=$(( $(date +%s) - $(stat -c %Y "$DAEMON_LOG" 2>/dev/null || echo 0) ))
    if [[ "$log_age" -le 300 ]]; then
      ok "daemon.log is being written (modified ${log_age}s ago)"
    else
      warn "daemon.log was last modified ${log_age}s ago; confirm the daemon is logging."
    fi
  else
    warn "daemon.log not found at $DAEMON_LOG yet."
  fi

  # The daemon records its own pid/startedAt; flag a stale record, don't edit it.
  local service_file="$HOME/workspace/vidnets-runtime/secure/agent-service.json"
  if [[ -f "$service_file" ]]; then
    local recorded_pid
    recorded_pid="$(python3 -c "import json; print(json.load(open('$service_file')).get('daemon_pid',''))" 2>/dev/null || echo '')"
    if [[ -n "$recorded_pid" && "$recorded_pid" != "$main_pid" ]]; then
      warn "agent-service.json still records daemon_pid $recorded_pid, live pid is $main_pid — update it (pid + startedAt) after this restart."
    else
      ok "agent-service.json matches live daemon pid $main_pid"
    fi
  fi
}

main() {
  log "Vidzeo/M2l one-shot setup — repo $REPO_ROOT"
  if [[ "$CHECK_MODE" -eq 1 ]]; then
    log "Mode: --check (dry run: verifies only, installs/enables/starts nothing)"
  else
    log "Mode: full setup"
  fi

  step_prereqs
  step_template_deps
  step_venv
  step_unit
  step_cms
  step_no_old_jobs
  step_start

  if [[ "${#FAILURES[@]}" -gt 0 ]]; then
    echo
    log "CHECK FAILED — ${#FAILURES[@]} problem(s) would block a full setup:"
    local i=1
    for f in "${FAILURES[@]}"; do
      printf '  %d. %s\n' "$i" "$f"
      i=$((i + 1))
    done
    echo
    log "Fix the items above, then re-run: $0 --check"
    exit 1
  fi

  if [[ "${#WARNINGS[@]}" -gt 0 ]]; then
    echo
    log "Completed with ${#WARNINGS[@]} warning(s) (non-blocking):"
    for w in "${WARNINGS[@]}"; do
      printf '  - %s\n' "$w"
    done
  fi

  if [[ "$CHECK_MODE" -eq 1 ]]; then
    echo
    log "CHECK PASSED — a full run of $0 would succeed on this machine."
  else
    echo
    log "SETUP COMPLETE — muse-daemon is active. Verify with: systemctl status muse-daemon && tail -f $DAEMON_LOG"
  fi
  exit 0
}

main "$@"
