#!/usr/bin/env bash
# Persist the stuck-job debugger setting in the LIVE runtime .env.
#
#   persist-job-debugger-setting.sh [0|1] [runtime-root]
#
# Upserts VIDZEO_JOB_DEBUGGER_ENABLED=<value> (default 1) in
# <runtime-root>/.env (default ~/workspace/vidnets-runtime/.env), leaving
# every other line untouched. Idempotent: running it twice changes
# nothing the second time.
#
# Background (Nov 21, 2026): the server was hand-restarted with
# VIDZEO_JOB_DEBUGGER_ENABLED=0 in its process environment only, after
# the debugger's broken restarter killed :5010 five times without
# bringing it back. That env var dies with the process: a health-monitor
# restart or VM reboot would bring the debugger back. The restarter is
# fixed (see services/job_debugger.py restart_server), so the debugger
# stays ENABLED (1) and this file is where that survives restarts.
#
# This script does NOT restart anything: the setting is read at server
# start (main.py load_local_env reads <runtime-root>/.env), so it takes
# effect on the NEXT server restart. Run it while jobs are running --
# it only edits the file -- then restart the server in the next window.
# Before that restart, also check no =0 lingers in vidnets/.env or the
# launcher shell environment, which would override this file per key
# (earlier .env wins in load_local_env).
set -euo pipefail

VALUE="${1:-1}"
if [ "$VALUE" != "0" ] && [ "$VALUE" != "1" ]; then
  echo "usage: $0 [0|1] [runtime-root]" >&2
  exit 2
fi
RUNTIME_ROOT="${2:-${VIDZEO_RUNTIME_ROOT:-$HOME/workspace/vidnets-runtime}}"
ENV_FILE="$RUNTIME_ROOT/.env"

mkdir -p "$RUNTIME_ROOT"
if [ ! -f "$ENV_FILE" ]; then
  touch "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi

if grep -q '^VIDZEO_JOB_DEBUGGER_ENABLED=' "$ENV_FILE"; then
  # Replace the existing line in place (GNU + BSD sed).
  if sed -i '' "s/^VIDZEO_JOB_DEBUGGER_ENABLED=.*/VIDZEO_JOB_DEBUGGER_ENABLED=$VALUE/" "$ENV_FILE" 2>/dev/null; then
    :
  else
    sed -i "s/^VIDZEO_JOB_DEBUGGER_ENABLED=.*/VIDZEO_JOB_DEBUGGER_ENABLED=$VALUE/" "$ENV_FILE"
  fi
  echo "updated $ENV_FILE: VIDZEO_JOB_DEBUGGER_ENABLED=$VALUE"
else
  printf '%s\n' "VIDZEO_JOB_DEBUGGER_ENABLED=$VALUE" >> "$ENV_FILE"
  echo "added to $ENV_FILE: VIDZEO_JOB_DEBUGGER_ENABLED=$VALUE"
fi
echo "Takes effect on the next server restart (no restart performed by this script)."
