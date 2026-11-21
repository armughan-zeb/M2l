#!/usr/bin/env bash
# Install the acting pool supervisor loop (pool_supervisor.py) into the
# runtime dir. Install only: it copies the repo file, byte-compiles it,
# and prints the cutover commands. It NEVER stops the old loop, NEVER
# starts the new one, and NEVER touches the spool — the cutover is done
# by the supervisor agent (or the parent) as a tracked background exec,
# so the loop is owned by a live agent session that can drain the action
# queue. See POOL_SUPERVISOR_BRIEF.md ("Loop handover", "The action
# queue") and the cutover note printed at the end.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SRC="$REPO/scripts/resident-inference-worker/pool_supervisor.py"
RUNTIME="${POOL_SUPERVISOR_RUNTIME:-$HOME/workspace/vidnets-runtime}"
DEST="$RUNTIME/pool_supervisor.py"

[ -f "$SRC" ] || { echo "[FAIL] $SRC missing" >&2; exit 1; }
python3 -m py_compile "$SRC" && echo "[PASS] $SRC compiles"
install -m 0644 "$SRC" "$DEST"
echo "[PASS] Installed pool_supervisor.py to $DEST"

cat <<EOF

Cutover (supervisor agent or parent only — the loop must be a TRACKED
background exec owned by the agent that drains the queue):
  1. Confirm the queue is idle: no unclaimed *.request.json in
     $HOME/workspace/vidnets-runtime/temp/inference-spool
     and the render gate is idle:
       node $REPO/vidnets/scripts/render-gate.js status   # want exit 0
  2. Start the new loop as a background exec (from the supervisor agent):
       cd $RUNTIME && POOL_SUPERVISOR_AGENT_ID=<your-agent-id> \\
         python3 pool_supervisor.py
     The new loop overwrites supervisor-loop.pid; the old loop
     (supervisor_tick.py) sees the foreign live pid on its next tick
     and exits by itself. Do NOT kill the old loop first: if the new
     loop fails to start, the old one keeps writing liveness.
  3. Verify takeover (within ~30s):
       python3 -c 'import json,time; d=json.load(open(
         "$HOME/workspace/vidnets-runtime/temp/inference-spool/"
         "pool-status.json")); print("age_s", round(time.time() -
         d["updatedAt"]/1000, 1), "workers", d["workers"], "target",
         d["target"], "ownerResponsive", d.get("ownerResponsive"))'
     and confirm supervisor-loop.log shows "pool supervisor starting"
     with the new supervisor id, and supervisor-actions.json exists.
  4. Rollback: stop the new loop (its pid is in supervisor-loop.pid);
     restart the old one:
       cd $RUNTIME && python3 supervisor_tick.py
     (old state file supervisor-state.json is shared and compatible).
EOF
