#!/usr/bin/env bash
# Vidzeo Muse worker — Docker setup.
# Unzip the distribution, run ./setup.sh, paste the CMS URL + agent key, and
# the worker is paired and ready to start. No setup from scratch: the image
# already contains Ubuntu 24.04, Node 24, Python 3.12, ffmpeg, yt-dlp,
# Remotion 4.0.541 + Chrome Headless Shell 157, the scrapers and edge-tts.
#
# The agent key is the credential you create on the CMS (Muse agents page):
# either a static worker token (starts with `muse_...`) or a one-time pairing
# code (MUSE-XXXX-XXXX). It is NEVER written to .env or any file by this
# script — it is handed to `node muse-agent.js pair` (token via environment,
# code via argument), which stores it in ./data/.muse-agent.json inside the
# data volume. Keep that file private: it is the worker's only credential.
#
# Usage: ./setup.sh
#   Optional env overrides:
#     MUSE_CMS_URL            default CMS URL shown at the prompt
#     HOST_HOOKS_DIR          host Muse hook definitions dir (default
#                             $HOME/hooks/definitions)
set -euo pipefail

cd "$(dirname "$0")"

say() { printf '%s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
die() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

say "=== Vidzeo Muse Worker — Docker setup ==="
say ""

# --- 1. Docker ---------------------------------------------------------------
command -v docker >/dev/null 2>&1 || die "docker is not installed. Install Docker Engine + the compose plugin first (https://docs.docker.com/engine/install/), then re-run ./setup.sh."
docker compose version >/dev/null 2>&1 || die "'docker compose' (v2 plugin) is not available. Install the Docker compose plugin, then re-run ./setup.sh."
say "[PASS] docker + docker compose available: $(docker compose version --short 2>/dev/null || docker compose version 2>/dev/null | head -1)"

# --- 2. Prompts: CMS URL, agent name, agent key ------------------------------
DEFAULT_CMS="${MUSE_CMS_URL:-https://cms.vidnets.com}"
printf 'CMS URL [%s]: ' "$DEFAULT_CMS"
read -r CMS_URL
CMS_URL="${CMS_URL:-$DEFAULT_CMS}"
# Strip a trailing slash; pairCommand normalises anyway, this keeps .env tidy.
CMS_URL="${CMS_URL%/}"
[ -n "$CMS_URL" ] || die "CMS URL is required."

printf 'Agent name [Muse Worker]: '
read -r AGENT_NAME
AGENT_NAME="${AGENT_NAME:-Muse Worker}"

say "Create the agent on the CMS first (Muse agents page), then paste its key."
say "Accepts a static worker token (muse_...) or a one-time pairing code (MUSE-XXXX-XXXX)."
printf 'Agent key (input hidden): '
read -rs AGENT_KEY
printf '\n'
[ -n "$AGENT_KEY" ] || die "Agent key is required."

# Classify without ever printing the key.
PAIR_MODE=""
if [ "${AGENT_KEY#muse_}" != "$AGENT_KEY" ]; then
  PAIR_MODE="token"
elif [ "${AGENT_KEY#MUSE-}" != "$AGENT_KEY" ]; then
  PAIR_MODE="code"
else
  die "That key does not look like a worker token (muse_...) or a pairing code (MUSE-XXXX-XXXX). Paste the key exactly as the CMS shows it."
fi

# --- 3. Persistent dirs + .env ----------------------------------------------
mkdir -p data/runtime/temp/agent-bridge data/work

if [ ! -f .env ]; then
  cat > .env <<EOF
# Vidzeo Muse worker — written by ./setup.sh. The agent key is NOT stored
# here; it lives only in ./data/.muse-agent.json (written by pair).
# Muse inference is COMPULSORY: never add an OpenRouter key to this file.
# No API keys of any kind: no OpenRouter, no Pexels, no Pixabay — direct
# retrieval (scrapers / yt-dlp / direct downloads) only.
MUSE_INFERENCE_COMPULSORY=1
MUSE_CMS_URL=$CMS_URL
MUSE_LOG_LEVEL=info
# Nov 10, 2026 standing decisions — do not "fix" these back.
MUSE_YOUTUBE_COVERAGE_TARGET=0.30
MUSE_MEDIA_STRICT=0
VIDZEO_VISUAL_TOP_CANDIDATES_PER_SCENE=12
VIDZEO_VISUAL_FINALIST_LIMIT=3
VIDZEO_VISUAL_GATE_WORKERS=12
VIDZEO_VISUAL_RELATIVE_FLOOR=0.5
VIDZEO_YT_VISUAL_GATE_FRAMES=2
VIDZEO_MIXED_YT_DOWNLOAD_CONCURRENCY=4
VIDZEO_YT_DOWNLOAD_CONCURRENCY=4
VIDZEO_AGENT_MIN_WALL_SECONDS=1200
# Thumbnails are SKIPPED: this worker delivers script + video only.
VIDZEO_SKIP_THUMBNAILS=1
MUSE_RENDER_CONCURRENCY=1
EOF
  say "[PASS] Wrote .env (CMS URL $CMS_URL)"
else
  if grep -q '^MUSE_CMS_URL=' .env; then
    sed -i "s|^MUSE_CMS_URL=.*|MUSE_CMS_URL=$CMS_URL|" .env
  else
    printf 'MUSE_CMS_URL=%s\n' "$CMS_URL" >> .env
  fi
  # Refresh the standing-decision vars in an old .env (compose env wins
  # anyway, but a stale 0.8 here would confuse the next reader).
  for kv in MUSE_YOUTUBE_COVERAGE_TARGET=0.30 VIDZEO_VISUAL_TOP_CANDIDATES_PER_SCENE=12 VIDZEO_VISUAL_FINALIST_LIMIT=3 VIDZEO_VISUAL_GATE_WORKERS=12 VIDZEO_VISUAL_RELATIVE_FLOOR=0.5 VIDZEO_YT_VISUAL_GATE_FRAMES=2 VIDZEO_MIXED_YT_DOWNLOAD_CONCURRENCY=4 VIDZEO_YT_DOWNLOAD_CONCURRENCY=4 VIDZEO_AGENT_MIN_WALL_SECONDS=1200 VIDZEO_SKIP_THUMBNAILS=1; do
    key="${kv%%=*}"
    if grep -q "^${key}=" .env; then
      sed -i "s|^${key}=.*|${kv}|" .env
    else
      printf '%s\n' "$kv" >> .env
    fi
  done
  say "[PASS] Updated MUSE_CMS_URL in existing .env (other settings kept, standing-decision vars refreshed)"
fi

# --- 4. Host Muse hook (inbox operator) --------------------------------------
HOOK_FILE="${MUSE_INBOX_HOOK_DEFINITION:-${HOST_HOOKS_DIR:-$HOME/hooks/definitions}/vidnets-inbox-watch.json}"
if [ ! -f "$HOOK_FILE" ]; then
  warn "vidnets-inbox-watch hook definition not found at $HOOK_FILE."
  warn "The daemon start gate will refuse to run until the host Muse VM has"
  warn "the vidnets-inbox-watch hook enabled (it is the only thing that"
  warn "answers Vidnets inference requests). Pairing below can still succeed;"
  warn "fix the hook, then start the worker. See docker/SETUP.md (\"Host Muse hook\"):"
  warn "copy docker/hooks/vidnets-inbox-watch.json.template to"
  warn "\$HOME/hooks/definitions/vidnets-inbox-watch.json, replace the"
  warn "<PLACEHOLDERS>, copy docker/hooks/vidnets-inbox-watch.sh to"
  warn "\$HOME/hooks/scripts/, and enable the hook on the host Muse VM."
else
  say "[PASS] Found vidnets-inbox-watch hook definition at $HOOK_FILE"
fi

# --- 5. Already paired? Ask BEFORE the (slow) build -------------------------
# Idempotency guard: never re-pair while a job is in flight — ./data holds
# the only copy of the credential AND the in-progress work. Re-running
# setup.sh with no job running is safe: it keeps ./data and only re-pairs
# when you say yes.
if [ -f data/work/active-job.json ]; then
  die "A job is in flight (data/work/active-job.json exists). Wait for it to finish, then re-run ./setup.sh. ./data was not touched."
fi
if docker compose ps -q worker 2>/dev/null | grep -q .; then
  warn "The worker container is currently running. Stop it first if you want to re-pair:"
  warn "  docker compose down   (state stays in ./data; the job marker above must also be clear)"
fi
REPAIR_CHOICE=""
if [ -s data/.muse-agent.json ]; then
  printf 'This worker is already paired (data/.muse-agent.json exists). Re-pair with the new key? [y/N]: '
  read -r REPAIR_CHOICE
  case "$REPAIR_CHOICE" in
    y|Y|yes|YES) ;;
    *) say "Keeping the existing pairing. Start the worker with: docker compose up -d"; exit 0 ;;
  esac
fi

# --- 6. Build -----------------------------------------------------------------
say ""
say "Building the worker image (first build takes a while: Python ML wheels + npm)..."
docker compose build

# --- 7. Pair ------------------------------------------------------------------
say "Pairing \"$AGENT_NAME\" with $CMS_URL ..."
if [ "$PAIR_MODE" = "token" ]; then
  # Token via environment (not argv) so it does not show in process lists.
  docker compose run --rm -e MUSE_TOKEN="$AGENT_KEY" worker \
    node muse-agent.js pair --cms "$CMS_URL" --name "$AGENT_NAME"
else
  docker compose run --rm worker \
    node muse-agent.js pair --cms "$CMS_URL" --code "$AGENT_KEY" --name "$AGENT_NAME"
fi
unset AGENT_KEY

say ""
say "=== Paired. Start the worker: ==="
say "  docker compose up -d"
say "  docker compose logs -f"
say ""
say "If the pair output says this worker is not the Active Worker, select it"
say "as Active Worker on the CMS (Muse agents page) before it can claim jobs."
say "Check health any time with: docker compose exec worker node muse-agent.js doctor"
