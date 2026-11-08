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

echo "============================================"
echo "  ScriptIT - YouTube Script Generator Pro"
echo "============================================"
echo ""

if [ ! -d "node_modules" ]; then
    echo "First run detected. Installing dependencies..."
    echo ""
    npm install
fi

echo "Starting ScriptIT..."
echo ""
npm run electron
