#!/usr/bin/env bash
set -e

SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
  DIR="$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )"
  SOURCE="$(readlink "$SOURCE")"
  [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
VIDNETS_AGENT_ROOT="$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )"

if [ -f "$VIDNETS_AGENT_ROOT/resources/server/headless_agent.py" ]; then
  VIDNETS_AGENT_ROOT="$VIDNETS_AGENT_ROOT/resources"
fi

PYTHON_EXE=""
if [ -n "$VIDNETS_PYTHON" ] && command -v "$VIDNETS_PYTHON" >/dev/null 2>&1; then
  PYTHON_EXE="$VIDNETS_PYTHON"
elif [ -x "$VIDNETS_AGENT_ROOT/tools/python/bin/python3" ]; then
  PYTHON_EXE="$VIDNETS_AGENT_ROOT/tools/python/bin/python3"
elif [ -x "$VIDNETS_AGENT_ROOT/tools/python/python" ]; then
  PYTHON_EXE="$VIDNETS_AGENT_ROOT/tools/python/python"
elif [ -x "$VIDNETS_AGENT_ROOT/.venv/bin/python3" ]; then
  PYTHON_EXE="$VIDNETS_AGENT_ROOT/.venv/bin/python3"
elif [ -x "$VIDNETS_AGENT_ROOT/venv/bin/python3" ]; then
  PYTHON_EXE="$VIDNETS_AGENT_ROOT/venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_EXE="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_EXE="python"
fi

if [ -z "$PYTHON_EXE" ]; then
  echo "Python 3 was not found. Please install Python 3 (e.g. 'sudo apt install python3') or set VIDNETS_PYTHON." >&2
  exit 1
fi

exec "$PYTHON_EXE" "$VIDNETS_AGENT_ROOT/server/headless_agent.py" "$@"
