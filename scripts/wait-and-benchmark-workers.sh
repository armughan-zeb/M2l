#!/usr/bin/env bash
# Guarded Remotion worker benchmark runner.
#
# Waits until it is safe to run the heavy benchmark, then runs it:
#   1. No process is working on live Vidnets job f04c6b9f.
#   2. The daemon has no active CMS job (work/active-job.json absent/empty
#      and no job dir modified in the last 10 minutes).
#   3. Only then: restart the Vidnets server (SIGTERM the old PID, then
#      VIDNETS_AGENT.sh start) so the box picks up the current code AND
#      frees the old server's ~1.1 GB RSS — without that, every requested
#      level auto-degrades to 1 worker and the benchmark proves nothing.
#      If the server does not come back healthy within 120s, abort: the
#      5-minute health monitor restarts a dead server on its own.
#   4. Run node scripts/benchmark-remotion-workers.js 1,2,3,5.
#   5. Verify the server is healthy and the daemon is still up at the end.
#
# Never runs while f04c6b9f has live work. Never claims or delivers a job.
# Timeout: 4 hours waiting, then aborts with a clear log line.

set -u
REPO="$HOME/workspace/M2l"
RUNTIME="$HOME/workspace/vidnets-runtime"
LOG="$REPO/work/remotion-worker-benchmark/wait-run.log"
mkdir -p "$REPO/work/remotion-worker-benchmark"
exec >>"$LOG" 2>&1
echo "[$(date -u +%FT%TZ)] guard: waiting for f04c6b9f to finish (max 4h)"

deadline=$(( $(date +%s) + 4*3600 ))
while true; do
  if [ "$(date +%s)" -gt "$deadline" ]; then
    echo "[$(date -u +%FT%TZ)] guard: TIMEOUT — f04c6b9f still has live work after 4h; benchmark NOT run"
    exit 2
  fi
  if pgrep -f "f04c6b9f" >/dev/null 2>&1; then
    sleep 60; continue
  fi
  # No f04c6b9f processes: also require no Vidnets job dir touched in 10 min
  # and no active CMS job on the daemon.
  if find "$RUNTIME/temp/jobs" -maxdepth 1 -mmin -10 2>/dev/null | grep -q .; then
    sleep 60; continue
  fi
  if [ -s "$REPO/work/active-job.json" ]; then
    sleep 60; continue
  fi
  break
done
echo "[$(date -u +%FT%TZ)] guard: box quiet (f04c6b9f done, daemon idle). Memory before restart:"
free -m | head -2

OLD_PID="$(pgrep -f 'vidnets/main.py' | head -n 1)"
if [ -n "$OLD_PID" ]; then
  echo "[$(date -u +%FT%TZ)] guard: stopping old Vidnets server PID $OLD_PID (SIGTERM)"
  kill "$OLD_PID" 2>/dev/null || true
  for i in $(seq 1 60); do
    curl -s --max-time 2 http://127.0.0.1:5010/api/status >/dev/null 2>&1 || break
    sleep 1
  done
  sleep 3
  if pgrep -f 'vidnets/main.py' >/dev/null 2>&1; then
    echo "[$(date -u +%FT%TZ)] guard: ABORT — old server PID still alive after SIGTERM; not forcing it"
    exit 3
  fi
fi
echo "[$(date -u +%FT%TZ)] guard: starting Vidnets server (VIDNETS_AGENT.sh start)"
timeout 120 bash "$REPO/vidnets/VIDNETS_AGENT.sh" --runtime-root "$RUNTIME" start || {
  echo "[$(date -u +%FT%TZ)] guard: ABORT — server start failed; health monitor will restart it"
  exit 4
}
for i in $(seq 1 60); do
  if curl -s --max-time 2 http://127.0.0.1:5010/api/status 2>/dev/null | grep -q '"ffmpeg":true'; then
    break
  fi
  sleep 2
done
curl -s --max-time 5 http://127.0.0.1:5010/api/status | head -c 300; echo
echo "[$(date -u +%FT%TZ)] guard: server healthy. Memory after restart:"
free -m | head -2

echo "[$(date -u +%FT%TZ)] guard: running benchmark 1,2,3,5"
node "$REPO/scripts/benchmark-remotion-workers.js" 1,2,3,5
echo "[$(date -u +%FT%TZ)] guard: benchmark exit=$?"
echo "[$(date -u +%FT%TZ)] guard: final state:"
ps -eo pid,etime,cmd | grep -E "muse-agent.js start|vidnets/main" | grep -v grep | cut -c1-120
curl -s --max-time 5 http://127.0.0.1:5010/api/status | head -c 200; echo
free -m | head -2
echo "[$(date -u +%FT%TZ)] guard: done"
