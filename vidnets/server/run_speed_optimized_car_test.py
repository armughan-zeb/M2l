"""Timed short main-pipeline car job used for retrieval/render regression."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

TITLE = "Why the Mazda MX-5 Still Matters"
SCRIPT = (
    "The Mazda MX-5 began with a clear idea: a lightweight roadster should make every corner feel alive. "
    "Its compact proportions, rear wheel drive layout, and simple controls keep the driver involved without "
    "demanding race car budgets. Engineers focused on low mass and balance, so modest power could still feel "
    "quick on a winding road. Across four generations Mazda refined the suspension, safety, and cabin while "
    "protecting the original character. Owners value the car because it rewards smooth inputs and offers real "
    "feedback at ordinary speeds. That balance explains why the MX-5 remains a reference point for accessible "
    "sports cars. Its lesson is practical: thoughtful engineering and a focused brief can outlast horsepower wars."
)
OUTPUT_DIR = ROOT / "output" / "speed_optimized_car_test"


def progress(fraction: float, message: str) -> None:
    print(f"  [{float(fraction) * 100:5.1f}%] {message}", flush=True)


def main() -> int:
    from app import _run_video_pipeline

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    config = {
        "script": SCRIPT,
        "title": TITLE,
        "name": TITLE,
        "image_source": "Mixed Media",
        "type": "short",
        "scene_mode": "Every 2 Beats",
        "vo_mode": "Use Audiogram",
        "voice": "en-US-ChristopherNeural",
        "tts_model": "audiogram",
        "whisper_mode": "local",
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "render_resolution": (1280, 720),
        "output_dir": str(OUTPUT_DIR),
        "yt_hard_stop_process": True,
    }
    started = time.monotonic()
    print("=" * 88)
    print(f"SPEED OPTIMIZED CAR TEST: {TITLE}")
    print(f"Narration words: {len(SCRIPT.split())}")
    print("=" * 88, flush=True)
    try:
        result = _run_video_pipeline(config, progress_callback=progress)
        elapsed = round(time.monotonic() - started, 1)
        video_path = Path(str((result or {}).get("video_path") or ""))
        manifest_path = Path(str((result or {}).get("manifest_path") or ""))
        report = {
            "title": TITLE,
            "narration_words": len(SCRIPT.split()),
            "elapsed_seconds": elapsed,
            "success": bool(isinstance(result, dict) and result.get("success")),
            "video_path": str(video_path),
            "manifest_path": str(manifest_path),
        }
        if video_path.is_file():
            report["size_bytes"] = video_path.stat().st_size
            report["sha256"] = hashlib.sha256(video_path.read_bytes()).hexdigest()
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_name,width,height", "-of", "json", str(video_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            try:
                report["ffprobe"] = json.loads(probe.stdout or "{}")
            except ValueError:
                report["ffprobe"] = {"raw": probe.stdout}
        report_path = OUTPUT_DIR / "speed_test_report.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("=" * 88)
        print(f"SUCCESS: {report['success']}")
        print(f"Elapsed: {elapsed}s")
        print(f"Video: {video_path}")
        print(f"Manifest: {manifest_path}")
        print(f"Report: {report_path}")
        print("=" * 88, flush=True)
        return 0 if report["success"] else 2
    except Exception as exc:
        print(f"SPEED OPTIMIZED CAR TEST FAILED after {time.monotonic() - started:.1f}s: {exc}", flush=True)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
