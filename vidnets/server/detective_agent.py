"""
Detective Agent: runs the FFmpeg production harness, diagnoses failures,
applies known fixes from FIX_REGISTRY, and re-runs until success.

Usage:
  python detective_agent.py [--max-iterations 5]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LOG_DIR = ROOT / "output" / "harness_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

MAX_ITERATIONS = int(sys.argv[sys.argv.index("--max-iterations") + 1]) if "--max-iterations" in sys.argv else 5

HARNESS_SCRIPT = str(HERE / "run_production_harness.py")

FIXES: dict[str, list[dict]] = {
    "nvenc_not_available": [
        {"file": "server/services/semantic_ffmpeg_renderer.py",
         "old": "if _NVENC_AVAILABLE is None:\n        try:",
         "new": "if _NVENC_AVAILABLE is None:\n        _NVENC_AVAILABLE = False\n        try:"},
    ],
    "nvenc_preset": [
        {"file": "server/services/semantic_ffmpeg_renderer.py",
         "old": "PRESET = \"veryfast\"",
         "new": "def _preset():\n    return 'veryfast'\nPRESET = _preset()"},
    ],
    "ffmpeg_not_found": [
        {"file": "server/services/semantic_ffmpeg_renderer.py",
         "old": "shutil.which('ffmpeg') or 'ffmpeg'",
         "new": "shutil.which('ffmpeg') or shutil.which('ffmpeg.exe') or 'ffmpeg'"},
    ],
    "font_not_found": [
        {"file": "server/services/semantic_ffmpeg_renderer.py",
         "old": "def _font(size=48, bold=True, template_id=''):\n    tid = template_id or getattr(_TLS, 'current_template_id', '')",
         "new": "def _font(size=48, bold=True, template_id=''):\n    try:\n        from PIL import ImageFont\n    except ImportError:\n        return None\n    tid = template_id or getattr(_TLS, 'current_template_id', '')"},
    ],
    "timeout": [
        {"file": "server/services/semantic_ffmpeg_renderer.py",
         "old": "timeout=120",
         "new": "timeout=300"},
    ],
}

def load_latest_metrics() -> dict | None:
    files = sorted(LOG_DIR.glob("metrics_*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None

def load_latest_log() -> str | None:
    files = sorted(LOG_DIR.glob("run_*.log"))
    return files[-1].read_text(encoding="utf-8") if files else None

def diagnose(metrics: dict, log_text: str) -> list[str]:
    out = []
    log_lower = log_text.lower()
    if metrics.get("success"):
        return out
    checks = [
        ("nvenc_not_available", "nvenc" in log_lower and ("cannot load" in log_lower or "nvcuda" in log_lower)),
        ("nvenc_preset", "preset" in log_lower and "veryfast" in log_lower and "nvenc" in log_lower),
        ("ffmpeg_not_found", "ffmpeg" in log_lower and "not found" in log_lower),
        ("font_not_found", "font" in log_lower and "not found" in log_lower),
        ("timeout", "timeout" in log_lower),
    ]
    for key, hit in checks:
        if hit:
            out.append(key)
    return out

def apply_fix(diagnosis: str) -> bool:
    fixes = FIXES.get(diagnosis, [])
    if not fixes:
        return False
    applied = 0
    for fix in fixes:
        fp = (ROOT / fix["file"]).resolve()
        if not fp.is_file():
            continue
        content = fp.read_text(encoding="utf-8")
        if fix["old"] not in content:
            continue
        content = content.replace(fix["old"], fix["new"])
        fp.write_text(content, encoding="utf-8")
        applied += 1
    return applied > 0

def run_detective():
    log_path = LOG_DIR / f"detective_{int(time.time())}.log"
    log_file = open(log_path, "w", encoding="utf-8")

    def log(msg: str):
        print(msg, flush=True)
        log_file.write(msg + "\n")

    log("=" * 60)
    log("DETECTIVE AGENT")
    log(f"Iterations: {MAX_ITERATIONS}  Harness: {HARNESS_SCRIPT}")
    log("=" * 60)

    for iteration in range(1, MAX_ITERATIONS + 1):
        log(f"\n--- Iteration {iteration}/{MAX_ITERATIONS} ---")
        log("Running harness...")

        try:
            result = subprocess.run(
                [sys.executable, HARNESS_SCRIPT],
                capture_output=True, text=True, timeout=600, cwd=str(ROOT),
            )
            log(result.stdout)
            if result.stderr:
                log("STDERR:\n" + result.stderr)
        except subprocess.TimeoutExpired:
            log("Harness timed out")
        except Exception as e:
            log(f"Harness error: {e}")

        metrics = load_latest_metrics()
        log_text = load_latest_log()

        if not metrics:
            log("No metrics found")
            continue

        log(f"success={metrics.get('success')}  time={metrics.get('elapsed_seconds')}s  "
            f"size={metrics.get('output_size_bytes', 0)}B")

        if metrics.get("success"):
            log("\n" + "=" * 60)
            log("PRODUCTION SUCCESSFUL")
            log(f"Video: {metrics.get('output_path')}")
            log("=" * 60)
            log_file.close()
            return True

        if not log_text:
            log("No log for diagnosis")
            continue

        diagnoses = diagnose(metrics, log_text)
        if not diagnoses:
            log("Unknown failure — showing last 20 log lines:")
            for line in log_text.strip().splitlines()[-20:]:
                log(f"  | {line}")
            log_file.close()
            return False

        log(f"Diagnoses: {', '.join(diagnoses)}")
        applied = sum(1 for d in diagnoses if apply_fix(d))
        log(f"Applied {applied} fix(es), re-running...")

        if applied == 0:
            log("No fix matched — stopping")
            log_file.close()
            return False

    log(f"\nExceeded {MAX_ITERATIONS} iterations")
    log_file.close()
    return False

if __name__ == "__main__":
    sys.exit(0 if run_detective() else 1)
