#!/usr/bin/env bash
# Pool-supervisor watch hook: wakes the main agent when the resident
# inference pool supervisor is dead, so a fresh supervisor can be spawned.
#
# A shell/cron job cannot spawn a subagent — only an agent can. So this
# hook does the liveness DECISION (cheap, every 60s) and wakes the main
# agent only when a respawn is actually needed; the woken agent re-verifies
# and spawns exactly one supervisor with the full brief + state note.
#
# Decision core: pool_supervisor_check.py next to this script (same dir).
# It reads pool-status.json + supervisor-alive.json + worker heartbeats,
# checks the render gate, and prints one JSON decision. This wrapper only
# translates that decision into wake/silent. It never spawns anything and
# never touches the spool.
#
# Wake conditions (all must hold):
#   - pool-status.json AND supervisor-alive.json both stale (>120s) or
#     missing (supervisor writes status every 30s; either fresh = alive)
#     — OR a fresh scale-up signal (supervisor-scaleup-needed.json,
#     <120s) names owed workerIds while live workers are below target
#     (2026-11-21 fast path: the loop summons the parent the same tick
#     it queues scale-up spawns, no 300s shortfall wait)
#   - no render ACTIVE (render-gate.js status exit 2 -> silent, retry later)
#   - no supervisor-parked.json (deliberate stop by the parent)
#   - no wake in the last 600s (cooldown — a slow spawn must not storm)
# The wake payload lists live workers (fresh heartbeats <90s) so the
# respawned supervisor ADOPTS them instead of spawning duplicates, and
# for a scale-up wake it lists the owed spawn workerIds verbatim.
set -euo pipefail
source "$HATCH_HOOK_RUNTIME"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
CHECK="$HERE/pool_supervisor_check.py"
if [ ! -f "$CHECK" ]; then
  CHECK="$HOME/workspace/M2l/scripts/resident-inference-worker/pool_supervisor_check.py"
fi

if ! command -v python3 >/dev/null 2>&1 || ! command -v jq >/dev/null 2>&1; then
  log "pool-supervisor-watch: python3 or jq missing; cannot check" "{}"
  silent "check_unavailable" "{}"
  exit 0
fi

decision_json="$(python3 "$CHECK" 2>/dev/null || true)"
if [ -z "$decision_json" ] || ! echo "$decision_json" | jq -e . >/dev/null 2>&1; then
  log "pool-supervisor-watch: checker produced no usable decision" "{}"
  silent "check_failed" "{}"
  exit 0
fi

decision="$(echo "$decision_json" | jq -r '.decision // "silent"')"
reason="$(echo "$decision_json" | jq -r '.reason // "unknown"')"
payload="$(echo "$decision_json" | jq -c '.payload // {}')"

if [ "$decision" = "wake" ]; then
  log "pool supervisor dead — requesting respawn" "$payload"
  wake "Resident inference pool supervisor is dead — respawn needed" "$payload"
else
  silent "pool supervisor watch: $reason" "{}"
fi
