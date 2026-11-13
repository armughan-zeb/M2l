#!/usr/bin/env bash
# Install the repository hook logic into the host Muse hook directory while
# preserving the operator's existing delivery target and prompt definition.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SCRIPT_DIR="${HOST_HOOK_SCRIPTS_DIR:-$HOME/hooks/scripts}"
HOOK_DIR="${HOST_HOOKS_DIR:-$HOME/hooks/definitions}"
STATE_DIR="${HOST_HOOK_STATE_DIR:-$HOME/hooks/state/vidnets-inbox-watch}"
HOOK_FILE="${MUSE_INBOX_HOOK_DEFINITION:-$HOOK_DIR/vidnets-inbox-watch.json}"
RUNTIME="${VIDZEO_RUNTIME_ROOT:-$REPO/data/runtime}"
BRIDGE="${VIDNETS_AGENT_BRIDGE_DIR:-${VIDZEO_BRIDGE_ROOT:-$RUNTIME/temp/agent-bridge}}"
DEST="$SCRIPT_DIR/vidnets-inbox-watch.sh"

mkdir -p "$SCRIPT_DIR" "$STATE_DIR"
python3 - "$REPO/docker/hooks/vidnets-inbox-watch.sh" "$DEST" "$REPO" "$BRIDGE" "$STATE_DIR" <<'PY'
import os, shlex, sys, tempfile
source, destination, repo, bridge, state_dir = sys.argv[1:]
with open(source, encoding='utf-8') as stream:
    body = stream.read()
body = body.replace('<EXTRACTED_DIR>/data/runtime/temp/agent-bridge', bridge)
body = body.replace('<EXTRACTED_DIR>', repo)
body = body.replace('STATE_DIR="$HOME/hooks/state/vidnets-inbox-watch"', 'STATE_DIR=' + shlex.quote(state_dir))
fd, temporary = tempfile.mkstemp(prefix='.vidnets-inbox-watch-', dir=os.path.dirname(destination))
try:
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, destination)
finally:
    if os.path.exists(temporary): os.unlink(temporary)
os.chmod(destination, 0o755)
PY

if [ ! -f "$HOOK_FILE" ]; then
  printf '[WARN] Installed hook script at %s; create the hook definition with a valid Muse delivery target before enabling jobs.\n' "$DEST" >&2
  exit 0
fi

python3 - "$HOOK_FILE" "$DEST" "$REPO" "$RUNTIME" <<'PY'
import json, os, sys, tempfile
definition_path, script_path, repo, runtime = sys.argv[1:]
with open(definition_path, encoding='utf-8') as stream:
    definition = json.load(stream)
definition['script_path'] = script_path
definition['script_timeout_secs'] = 120
definition['poll_interval_secs'] = 20
definition['prompt'] = str(definition.get('prompt', '')).replace('<EXTRACTED_DIR>', repo).replace('<DATA_RUNTIME_DIR>', runtime)
fd, temporary = tempfile.mkstemp(prefix='.vidnets-hook-definition-', dir=os.path.dirname(definition_path))
try:
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(definition, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, definition_path)
finally:
    if os.path.exists(temporary): os.unlink(temporary)
PY

printf '[PASS] Installed hook implementation: %s\n' "$DEST"
printf '[PASS] Preserved delivery and prompt settings; set script path, 20s poll, and 120s cap.\n'
