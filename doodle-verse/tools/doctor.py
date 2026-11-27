"""Check that everything the animation studio needs is installed.

python doctor.py          -> prints OK / MISSING for each item plus the fix, exits 1 if anything is missing
"""
import importlib.util
import platform
import re
import shutil
import subprocess
import sys

WIN = platform.system() == "Windows"
MAC = platform.system() == "Darwin"


def run(cmd):
    try:
        exe = shutil.which(cmd[0])
        if not exe:
            return None
        return subprocess.run([exe] + cmd[1:], capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return None


def fix(win, mac, linux):
    return win if WIN else mac if MAC else linux


rows, missing = [], 0


def check(name, ok, detail, how):
    global missing
    rows.append((("OK     " if ok else "MISSING"), name, detail if ok else how))
    missing += 0 if ok else 1


# Node 18+
v = run(["node", "--version"])
m = re.match(r"v(\d+)", v or "")
check("Node.js 18+", bool(m and int(m.group(1)) >= 18), v,
      fix("winget install -e --id OpenJS.NodeJS.LTS", "brew install node", "install Node 20 LTS from nodejs.org"))
check("npm", bool(run(["npm.cmd" if WIN else "npm", "--version"]) or run(["npm", "--version"])), "found",
      "comes with Node.js; reopen the terminal after installing Node")

# Python 3.10+
ok = sys.version_info >= (3, 10)
check("Python 3.10+", ok, sys.version.split()[0],
      fix("winget install -e --id Python.Python.3.12", "brew install python@3.12", "install Python 3.12"))

# ffmpeg
v = run(["ffmpeg", "-version"])
check("ffmpeg", bool(v), (v or "").splitlines()[0][:60] if v else "",
      fix("winget install -e --id Gyan.FFmpeg", "brew install ffmpeg", "sudo apt install ffmpeg"))

# Python packages
for mod, pkg in [("numpy", "numpy"), ("scipy", "scipy"), ("PIL", "pillow"), ("edge_tts", "edge-tts"), ("faster_whisper", "faster-whisper")]:
    check(f"python: {pkg}", importlib.util.find_spec(mod) is not None, "installed",
          f'"{sys.executable}" -m pip install -r requirements.txt   (in this tools folder)')

w = max(len(r[1]) for r in rows)
for status, name, info in rows:
    print(f"{status}  {name.ljust(w)}  {info}")
if missing:
    print(f"\n{missing} item(s) missing. Install them, then open a NEW terminal (so PATH refreshes) and run doctor.py again.")
    if WIN:
        print("Tip: after winget installs, Claude can refresh PATH in the same session with:")
        print('  $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")')
else:
    print("\nAll good. You're ready to animate.")
sys.exit(1 if missing else 0)
