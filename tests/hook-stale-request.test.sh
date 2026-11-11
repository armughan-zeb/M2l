#!/usr/bin/env bash
# Regression test for the vidnets-inbox-watch stale-request fix.
#
# A reboot can leave a request file marked pending whose waiter is gone
# (request 6ccea4da woke the hook every ~2 min for hours after expiring).
# The hook must skip requests past expires_at, never wake on them, and
# must re-wake scriptit.* requests faster (60s) than other kinds (120s).
#
# Runs the real hook script against a fake $HOME bridge dir + a stub
# HATCH_HOOK_RUNTIME. Usage: bash tests/hook-stale-request.test.sh
set -euo pipefail
cd "$(dirname "$0")/.."

fail() { echo "FAIL: $*" >&2; exit 1; }

ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT
REAL_HOME="$HOME"
HOOK_SCRIPT="$REAL_HOME/hooks/scripts/vidnets-inbox-watch.sh"
export HOME="$ROOT/home"
mkdir -p "$HOME/workspace/vidnets-runtime/temp/agent-bridge" "$HOME/hooks/state/vidnets-inbox-watch"
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
mkreq eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee pending 600 scriptit.completion       # live scriptit, woken 90s ago -> re-wake (60s)
mkreq ffffffffffffffffffffffffffffffff pending 600 vidnets_response          # live, woken 90s ago -> no re-wake (120s)
printf 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee %s\nffffffffffffffffffffffffffffffff %s\n' \
  "$((now - 90))" "$((now - 90))" > "$HOME/hooks/state/vidnets-inbox-watch/seen.txt"

out=$(bash "$HOOK_SCRIPT")
echo "$out" | grep -q 'HOOK_WAKE' || fail "expected a wake, got: $out"
echo "$out" | grep -q 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' || fail "live request was not woken: $out"
echo "$out" | grep -q 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee' || fail "scriptit re-wake at 90s did not fire: $out"
echo "$out" | grep -q 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' && fail "expired request woke the hook: $out"
echo "$out" | grep -q 'cccccccccccccccccccccccccccccccc' && fail "answered request woke the hook: $out"
echo "$out" | grep -q 'ffffffffffffffffffffffffffffffff' && fail "non-scriptit request re-woke before 120s: $out"
echo "$out" | grep -q '"count":2' || fail "expected exactly 2 woken requests: $out"

# Second scenario: only the expired request exists -> silent, no wake.
rm -rf "$HOME/workspace/vidnets-runtime/temp/agent-bridge"
mkdir -p "$HOME/workspace/vidnets-runtime/temp/agent-bridge"
mkreq aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa pending -3600 scriptit.completion
out=$(bash "$HOOK_SCRIPT")
echo "$out" | grep -q 'HOOK_SILENT' || fail "expired-only inbox should be silent, got: $out"

echo "PASS: hook skips stale/answered requests, re-wakes scriptit at 60s, others at 120s"
