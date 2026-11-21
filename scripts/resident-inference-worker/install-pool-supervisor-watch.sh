#!/usr/bin/env bash
# Install the pool-supervisor-watch hook script into ~/hooks/scripts/ and
# print the exact registration the parent must run (a hook is registered
# with the hooks tool, not from the shell — registration also decides the
# delivery chat and whether the hook is enabled).
#
# The hook starts DISABLED: enable it only once the live supervisor is
# writing fresh pool-status.json (< 60s old), otherwise the first poll
# wakes an immediate (correct, but surprising) respawn. Check first:
#   python3 -c 'import json,time; print(time.time()-json.load(open(
#     "/home/hatch/workspace/vidnets-runtime/temp/inference-spool/"
#     "pool-status.json"))["updatedAt"]/1000)'
# Enable with the hooks tool: hooks.enable id "pool-supervisor-watch".
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SCRIPT_DIR="$HOME/hooks/scripts"
SRC_SH="$REPO/scripts/resident-inference-worker/pool-supervisor-watch.sh"
SRC_PY="$REPO/scripts/resident-inference-worker/pool_supervisor_check.py"

mkdir -p "$SCRIPT_DIR"
install -m 0755 "$SRC_SH" "$SCRIPT_DIR/pool-supervisor-watch.sh"
install -m 0644 "$SRC_PY" "$SCRIPT_DIR/pool_supervisor_check.py"
printf '[PASS] Installed pool-supervisor-watch.sh + pool_supervisor_check.py to %s\n' "$SCRIPT_DIR"
cat <<'EOF'

Register the hook (hooks tool — new hooks start disabled):
  hooks.add id "pool-supervisor-watch"
            script_path "~/hooks/scripts/pool-supervisor-watch.sh"
            poll_interval_secs 60
            script_timeout_secs 60
            prompt "<see POOL_SUPERVISOR_WATCH_HOOK_PROMPT.md — the woken
              agent re-verifies liveness, then spawns exactly one
              supervisor with the full POOL_SUPERVISOR_BRIEF.md plus the
              filled POOL_SUPERVISOR_RESPAWN_NOTE.md state note>"

Then test without waking anyone, then enable:
  hooks.dry_run id "pool-supervisor-watch"
  hooks.enable  id "pool-supervisor-watch"
EOF
