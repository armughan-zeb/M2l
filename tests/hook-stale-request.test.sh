#!/usr/bin/env bash
# Regression test for the vidnets-inbox-watch stale-request fix.
#
# A reboot can leave a request file marked pending whose waiter is gone
# (request 6ccea4da woke the hook every ~2 min for hours after expiring).
# The hook must skip requests past expires_at, never wake on them, and
# must defer composition to a healthy resident dispatcher.
#
# Runs the real hook script against a fake $HOME bridge dir + a stub
# HATCH_HOOK_RUNTIME. Usage: bash tests/hook-stale-request.test.sh
set -euo pipefail
cd "$(dirname "$0")/.."

fail() { echo "FAIL: $*" >&2; exit 1; }

ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT
HOOK_SCRIPT="$PWD/docker/hooks/vidnets-inbox-watch.sh"
export HOME="$ROOT/home"
export VIDNETS_AGENT_BRIDGE_DIR="$HOME/workspace/vidnets-runtime/temp/agent-bridge"
mkdir -p "$VIDNETS_AGENT_BRIDGE_DIR" "$HOME/hooks/state/vidnets-inbox-watch"
cat > "$ROOT/runtime.sh" <<'EOF'
log() { echo "HOOK_LOG:$1 $2"; }
wake() { echo "HOOK_WAKE:$1 $2"; }
silent() { echo "HOOK_SILENT:$1 $2"; }
EOF
export HATCH_HOOK_RUNTIME="$ROOT/runtime.sh"
export HATCH_HOOK_DRY_RUN=1

now=$(date +%s)
mkreq() { # id state expires_offset operation
  mkdir -p "$HOME/workspace/vidnets-runtime/temp/agent-bridge/$1"
  printf '{"id":"%s","state":"%s","kind":"text","operation":"%s","job_id":"TESTJOB","created_at":%s,"expires_at":%s}' \
    "$1" "$2" "$4" "$((now - 100))" "$((now + $3))" \
    > "$HOME/workspace/vidnets-runtime/temp/agent-bridge/$1/request.json"
}
mkreq aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa pending -3600 scriptit.completion   # expired 1h ago -> skip
mkreq bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb pending 600 vidnets_response          # live -> wake
mkreq cccccccccccccccccccccccccccccccc answered 600 vidnets_response         # answered -> skip
mkreq eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee pending 120 scriptit.completion
mkreq ffffffffffffffffffffffffffffffff pending 120 vidnets_response
printf 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee %s\nffffffffffffffffffffffffffffffff %s\n' \
  "$((now - 130))" "$((now - 90))" > "$HOME/hooks/state/vidnets-inbox-watch/seen.txt"

out=$(bash "$HOOK_SCRIPT")
echo "$out" | grep -q 'HOOK_WAKE' || fail "expected a wake, got: $out"
echo "$out" | grep -q 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' || fail "live request was not woken: $out"
echo "$out" | grep -q 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee' || fail "legacy re-wake after 120s did not fire: $out"
echo "$out" | grep -q 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' && fail "expired request woke the hook: $out"
echo "$out" | grep -q 'cccccccccccccccccccccccccccccccc' && fail "answered request woke the hook: $out"
echo "$out" | grep -q 'ffffffffffffffffffffffffffffffff' && fail "non-scriptit request re-woke before 120s: $out"
echo "$out" | grep -q '"count":2' || fail "expected exactly 2 woken requests: $out"

# A healthy dispatcher owns the pending requests.
printf '{"updatedAt":%s}\n' "$((now * 1000))" > "$VIDNETS_AGENT_BRIDGE_DIR/resident-status.json"
out=$(bash "$HOOK_SCRIPT")
[[ "$out" == *"resident Muse dispatcher"* && "$out" != *"HOOK_WAKE"* ]] || fail "resident did not own the inbox: $out"
printf '{"updatedAt":%s}\n' "$(((now - 90) * 1000))" > "$VIDNETS_AGENT_BRIDGE_DIR/resident-status.json"
out=$(bash "$HOOK_SCRIPT")
[[ "$out" == *"HOOK_WAKE"* ]] || fail "stale dispatcher suppressed the legacy wake: $out"

# Second scenario: only the expired request exists -> silent, no wake.
rm -rf "$HOME/workspace/vidnets-runtime/temp/agent-bridge"
mkdir -p "$HOME/workspace/vidnets-runtime/temp/agent-bridge"
mkreq aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa pending -3600 scriptit.completion
out=$(bash "$HOOK_SCRIPT")
echo "$out" | grep -q 'HOOK_SILENT' || fail "expired-only inbox should be silent, got: $out"

echo "PASS: hook skips expired requests, defers to a fresh resident heartbeat and wakes if it is stale"
