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

# 1. Explicit bridge / headless mode
if [ "$1" = "bridge" ] || [ "$1" = "--bridge" ] || [ "$1" = "headless" ] || [ "$1" = "--headless" ]; then
    echo "Starting ScriptIT Bridge server (pure Node headless mode)..."
    echo ""
    exec node bridge/server.js
fi

# 2. Check for display environment
if [ -z "$DISPLAY" ] && [ -z "$WAYLAND_DISPLAY" ]; then
    if command -v xvfb-run >/dev/null 2>&1; then
        echo "No display detected. Launching under virtual framebuffer (xvfb-run)..."
        echo ""
        exec xvfb-run -a npm run electron
    else
        echo "No graphical display detected ($DISPLAY) and xvfb-run not installed."
        echo "Starting ScriptIT in headless Bridge API mode on port 3211..."
        echo ""
        exec node bridge/server.js
    fi
fi

echo "Starting ScriptIT..."
echo ""
exec npm run electron
