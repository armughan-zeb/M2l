import os
import sys
import glob
import time

OUTPUT_DIR = r"c:\Users\user\Documents\Side Projects\Vidzeo Local\Vidzeo App\output"
files = glob.glob(os.path.join(OUTPUT_DIR, "*.mp4"))
if not files:
    print("No .mp4 files found in output directory")
    sys.exit(0)

files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
for idx, f in enumerate(files[:5]):
    size_mb = os.path.getsize(f) / (1024 * 1024)
    mtime = time.ctime(os.path.getmtime(f))
    print(f"[{idx+1}] Path: {os.path.basename(f)} | Size: {size_mb:.2f} MB | Modified: {mtime}")
