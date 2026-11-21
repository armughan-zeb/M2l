#!/usr/bin/env bash
# Print the pool-selfheal cron registration (job id `pool-selfheal`).
# Install only: this script NEVER registers the cron itself — creating a
# live scheduled job that spawns workers is a parent/coordinator
# decision. It prints the exact `cron.add` parameters from
# POOL_SELFHEAL_CRON.md so registration is a copy-paste, not a guess.
set -euo pipefail

cat <<'EOF'
Register the pool self-heal cron (parent/coordinator only):

  cron.add with:
    id:        pool-selfheal
    title:     Pool self-heal (inference pool executor)
    owner:     goal:documentary-video-render-pipeline
    mode:      task
    schedule:  interval, every 2m, timezone Asia/Karachi
    body:      the "Run body" section of
               scripts/resident-inference-worker/POOL_SELFHEAL_CRON.md
               (verbatim — it is the job's instructions)

  Then verify the first run (within ~2 min):
    - it runs pool_selfheal.py --check and reports the plan
    - with a healthy pool it spawns nothing and does nothing
    - do NOT test it by killing a worker during a render: the render
      gate forbids spawns while a render is ACTIVE, and the run must
      exit after the loop check.

  To stop healing (deliberate parent stop): disable/remove the cron
  AND write supervisor-parked.json in the spool; the planner treats
  parked as a hard no-heal.
EOF
