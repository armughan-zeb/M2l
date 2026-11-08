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
  for candidate_dir in /usr/local/bin /usr/bin /snap/bin "$HOME/.nvm/versions/node/$(ls -1 "$HOME/.nvm/versions/node" 2>/dev/null | tail -n 1)/bin"; do
    if [ -x "$candidate_dir/node" ]; then
      export PATH="$candidate_dir:$PATH"
      break
    fi
  done
fi

if ! command -v node >/dev/null 2>&1; then
  echo "Error: Node.js 18.17+ is required. Please install Node.js." >&2
  exit 1
fi

exec node "$ROOT/muse-agent.js" start "$@"
