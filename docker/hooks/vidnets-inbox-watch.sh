#!/usr/bin/env bash
# Legacy wake-up path when a resident Muse inference transport is unavailable.
# INSTALL (host Muse VM, not the container):
#   1. Edit BRIDGE below: the extracted repo dir + /data/runtime/temp/agent-bridge.
#   2. Copy this file to ~/hooks/scripts/vidnets-inbox-watch.sh and chmod +x it.
#   3. Copy docker/hooks/vidnets-inbox-watch.json.template to
#      ~/hooks/definitions/vidnets-inbox-watch.json, replace the
#      <PLACEHOLDERS>, and enable the hook.
set -euo pipefail
source "$HATCH_HOOK_RUNTIME"

# --- EDIT ME: where setup.sh extracted the zip on this host. ---
BRIDGE="${VIDNETS_AGENT_BRIDGE_DIR:-${VIDZEO_BRIDGE_ROOT:-<EXTRACTED_DIR>/data/runtime/temp/agent-bridge}}"
STATE_DIR="$HOME/hooks/state/vidnets-inbox-watch"
SEEN_FILE="$STATE_DIR/seen.txt"
mkdir -p "$STATE_DIR"
touch "$SEEN_FILE"

now=$(date +%s)
run_tmp="$STATE_DIR/.last-run.$$"
printf '%s\n' "$now" > "$run_tmp"
mv -f "$run_tmp" "$STATE_DIR/last-run"
resident=$(jq -r '.updatedAt // 0 | floor' "$BRIDGE/resident-status.json" 2>/dev/null || true)
if [[ "$resident" =~ ^[0-9]+$ ]] && [ "$((now - resident / 1000))" -le 15 ] && [ "$((now - resident / 1000))" -ge -5 ]; then
  silent "resident Muse dispatcher is handling the inbox" "{}"
  exit 0
fi
rewake_after=120
pending_json="[]"
changed=0
tmp_seen=$(mktemp)
awk -v cutoff="$((now - rewake_after))" '$2 >= cutoff' "$SEEN_FILE" > "$tmp_seen"

for req in "$BRIDGE"/*/request.json; do
  [ -f "$req" ] || continue
  id=$(jq -r '.id // empty' "$req" 2>/dev/null || true)
  state=$(jq -r '.state // empty' "$req" 2>/dev/null || true)
  [[ "$id" =~ ^[a-f0-9]{32}$ ]] || continue
  [ "$state" = "pending" ] || continue
  # Skip requests past their expiry: a reboot can leave a pending file
  # whose waiter is gone, and it would otherwise wake the hook forever.
  exp=$(jq -r '.expires_at // 0' "$req" 2>/dev/null || true)
  [ "${exp%.*}" -gt "$now" ] || continue
  [ ! -f "$(dirname "$req")/response.json" ] || continue
  op=$(jq -r '.operation // empty' "$req" 2>/dev/null || true)
  last=$(grep "^$id " "$tmp_seen" 2>/dev/null | awk '{print $2}' | tail -n 1 || true)
  if [ -z "$last" ] || [ $((now - last)) -ge $rewake_after ]; then
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
