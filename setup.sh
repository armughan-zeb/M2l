#!/usr/bin/env bash
# Vidzeo Muse worker — Docker setup.
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '%s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
die() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

say "=== Vidzeo Muse Worker — Docker setup ==="

command -v docker >/dev/null 2>&1 || die "docker is not installed."
docker compose version >/dev/null 2>&1 || die "'docker compose' (v2 plugin) is not available."

DEFAULT_CMS="${MUSE_CMS_URL:-http://127.0.0.1:3210}"
printf 'CMS URL [%s]: ' "$DEFAULT_CMS"
read -r CMS_URL
CMS_URL="${CMS_URL:-$DEFAULT_CMS}"
CMS_URL="${CMS_URL%/}"
[ -n "$CMS_URL" ] || die "CMS URL is required."

printf 'Agent name [Muse Worker]: '
read -r AGENT_NAME
AGENT_NAME="${AGENT_NAME:-Muse Worker}"

printf 'Agent key (muse_... or MUSE-XXXX-XXXX, input hidden): '
read -rs AGENT_KEY
printf '\n'
[ -n "$AGENT_KEY" ] || die "Agent key is required."

PAIR_MODE=""
if [ "${AGENT_KEY#muse_}" != "$AGENT_KEY" ]; then
  PAIR_MODE="token"
elif [ "${AGENT_KEY#MUSE-}" != "$AGENT_KEY" ]; then
  PAIR_MODE="code"
else
  die "Key must start with muse_ or MUSE-."
fi

mkdir -p data/runtime/temp/agent-bridge data/work

if [ ! -f .env ]; then
  cat > .env <<EOF
MUSE_INFERENCE_COMPULSORY=1
MUSE_CMS_URL=$CMS_URL
MUSE_LOG_LEVEL=info
VIDZEO_SKIP_THUMBNAILS=1
EOF
fi

docker compose build

if [ "$PAIR_MODE" = "token" ]; then
  docker compose run --rm -e MUSE_TOKEN="$AGENT_KEY" worker \
    node muse-agent.js pair --cms "$CMS_URL" --name "$AGENT_NAME"
else
  docker compose run --rm worker \
    node muse-agent.js pair --cms "$CMS_URL" --code "$AGENT_KEY" --name "$AGENT_NAME"
fi
unset AGENT_KEY

say "Paired. Start with: docker compose up -d"
