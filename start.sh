#!/usr/bin/env bash
set -e

SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
  DIR="$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )"
  SOURCE="$(readlink "$SOURCE")"
  [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
ROOT="$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )"
cd "$ROOT"

if ! command -v node >/dev/null 2>&1; then
  echo "Error: Node.js 18.17+ is required. Please install Node.js." >&2
  exit 1
fi

exec node "$ROOT/muse-agent.js" start "$@"
