import subprocess
import sys
import time

t0 = time.time()
proc = subprocess.Popen(
    [sys.executable, "-u", "server/run_titanic_mixed_production.py"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    encoding="utf-8",
    errors="replace",
    bufsize=1,
)
with open("temp/mixed_run2_timed.log", "w", encoding="utf-8") as out:
    for line in proc.stdout:
        elapsed = time.time() - t0
        out.write(f"[{elapsed:8.1f}s] {line}")
        out.flush()
    rc = proc.wait()
print(f"EXIT_CODE={rc} TOTAL={time.time() - t0:.1f}s", flush=True)
