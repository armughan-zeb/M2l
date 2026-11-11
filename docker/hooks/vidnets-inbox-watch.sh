#!/usr/bin/env bash
# Watches the Vidnets agent-bridge inbox for new pending requests (text or
# vision) from the running jobs, so Muse can answer them as the operator
# before their TTL expires. Re-wakes for a still-pending request if it has
# not been woken recently (240s normally; 120s for scriptit.* requests,
# whose file-based bridge wall is the tightest. The hook polls every 20s,
# so a 120s re-wake nudges a still-pending draft every sixth poll. The
# longer re-wake matters because composing a full answer takes minutes —
# re-waking at 120s used to launch a second worker for the same request
# while the first was still writing, and the loser got a 400.
# Agent-request TTLs are floored at 600s server-side
# (VIDZEO_AGENT_MIN_WALL_SECONDS) and 600s on the ScriptIT bridge.
#
# INSTALL (host Muse VM, not the container):
#   1. Edit BRIDGE below: the extracted repo dir + /data/runtime/temp/agent-bridge.
#   2. Copy this file to ~/hooks/scripts/vidnets-inbox-watch.sh and chmod +x it.
#   3. Copy docker/hooks/vidnets-inbox-watch.json.template to
#      ~/hooks/definitions/vidnets-inbox-watch.json, replace the
#      <PLACEHOLDERS>, and enable the hook.
set -euo pipefail
source "$HATCH_HOOK_RUNTIME"

# --- EDIT ME: where setup.sh extracted the zip on this host. ---
BRIDGE="<EXTRACTED_DIR>/data/runtime/temp/agent-bridge"
STATE_DIR="$HOME/hooks/state/vidnets-inbox-watch"
SEEN_FILE="$STATE_DIR/seen.txt"
mkdir -p "$STATE_DIR"
touch "$SEEN_FILE"

now=$(date +%s)
rewake_after=240
rewake_after_scriptit=120
pending_json="[]"
changed=0
tmp_seen=$(mktemp)
cp "$SEEN_FILE" "$tmp_seen" || true

for req in "$BRIDGE"/*/request.json; do
  [ -f "$req" ] || continue
  id=$(jq -r '.id // empty' "$req" 2>/dev/null || true)
  state=$(jq -r '.state // empty' "$req" 2>/dev/null || true)
  [ -n "$id" ] || continue
  [ "$state" = "pending" ] || continue
  # Skip requests past their expiry: a reboot can leave a pending file
  # whose waiter is gone, and it would otherwise wake the hook forever.
  exp=$(jq -r '.expires_at // 0' "$req" 2>/dev/null || true)
  [ "${exp%.*}" -gt "$now" ] || continue
  # ScriptIT draft/synthesis requests have the tightest compose window, so
  # re-wake them sooner (120s); other kinds keep the 240s cadence.
  op=$(jq -r '.operation // empty' "$req" 2>/dev/null || true)
  rewake=$rewake_after
  case "$op" in scriptit.*) rewake=$rewake_after_scriptit;; esac
  last=$(grep "^$id " "$tmp_seen" 2>/dev/null | awk '{print $2}' | tail -n 1 || true)
  if [ -z "$last" ] || [ $((now - last)) -ge $rewake ]; then
    kind=$(jq -r '.kind // empty' "$req" 2>/dev/null || true)
    job=$(jq -r '.job_id // empty' "$req" 2>/dev/null || true)
    pending_json=$(echo "$pending_json" | jq --arg id "$id" --arg kind "$kind" --arg op "$op" --arg job "$job" '. + [{id:$id, kind:$kind, operation:$op, job_id:$job}]')
    if [ "${HATCH_HOOK_DRY_RUN:-0}" != "1" ]; then
      grep -v "^$id " "$tmp_seen" > "$tmp_seen.new" || true
      mv "$tmp_seen.new" "$tmp_seen"
      echo "$id $now" >> "$tmp_seen"
      changed=1
    fi
  fi
done

if [ "${HATCH_HOOK_DRY_RUN:-0}" != "1" ] && [ "$changed" = "1" ]; then
  cp "$tmp_seen" "$SEEN_FILE"
fi
rm -f "$tmp_seen"

count=$(echo "$pending_json" | jq 'length')
if [ "$count" -gt 0 ]; then
  log "pending inbox requests" "{\"count\":$count}"
  wake "Vidnets inbox has $count pending request(s)" "{\"pending\":$pending_json}"
else
  silent "no new pending inbox requests" "{}"
fi
