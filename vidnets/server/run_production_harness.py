"""
Production harness: tests the FFmpeg renderer end-to-end.

Generates synthetic audio + scenes, calls render_semantic_documentary()
directly. No external dependencies (TTS, web search, YouTube, LLM).
"""

from __future__ import annotations

import json
import os
import struct
import sys
import time
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

LOG_DIR = ROOT / "output" / "harness_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

os.environ["VIDZEO_REMOTION_RENDER"] = "0"
os.environ["VIDZEO_SEMANTIC_RENDER_REQUIRED"] = "0"
os.environ["VIDZEO_ALLOW_LEGACY_RENDER_FALLBACK"] = "1"

FPS = 30
WIDTH = 1920
HEIGHT = 1080


def _make_test_audio(path: str, duration: float = 12.0, sample_rate: int = 24000):
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = []
        for i in range(int(sample_rate * duration)):
            t = i / sample_rate
            val = int(16000 * __import__("math").sin(2 * __import__("math").pi * 220 * t))
            frames.append(struct.pack("<h", val))
        wf.writeframes(b"".join(frames))


def _make_silent_audio(path: str, duration: float = 2.0, sample_rate: int = 24000):
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * int(sample_rate * duration))


def build_manifest(audio_path: str) -> dict:
    scenes = []
    overlay_templates = [
        ("chapter-opener", {"title": "THE TEST", "kicker": "HARNESS"}),
        ("keyword-reveal", {"text": "FFMPEG RENDER", "detail": "Verification pipeline"}),
        ("quote-card", {"quote": "It worked on the first try.", "attribution": "ENGINEER"}),
        ("stat-counter", {"value": "42", "label": "OVERLAYS"}),
        ("lower-third", {"title": "TEST SUBJECT", "subtitle": "PASSED"}),
        ("date-card", {"label": "JULY 2026", "kicker": "TIMELINE"}),
    ]
    for idx, (tid, props) in enumerate(overlay_templates):
        scenes.append({
            "duration": 2.0,
            "media": {"type": "image", "path": "", "src": ""},
            "graphic": {"template_id": tid, "props": props},
            "camera": {"motion": "static"},
            "narration": f"Scene {idx + 1} testing {tid}.",
        })
    return {
        "fps": FPS,
        "width": WIDTH,
        "height": HEIGHT,
        "duration": len(scenes) * 2.0,
        "theme": {"id": "minimal"},
        "scenes": scenes,
        "audio": {
            "voiceover": {"path": audio_path, "src": audio_path, "rights_status": "user_generated"},
        },
    }


def run_harness() -> dict:
    started = time.monotonic()
    log_path = LOG_DIR / f"run_{int(started)}.log"
    log_file = open(log_path, "w", buffering=1)

    def log(msg: str):
        print(msg, flush=True)
        log_file.write(msg + "\n")

    log(f"{'='*60}")
    log(f"PRODUCTION HARNESS")
    log(f"Python: {sys.executable}")
    log(f"WD: {ROOT}")
    log(f"Remotion: {os.environ.get('VIDZEO_REMOTION_RENDER', '0')}")
    log(f"{'='*60}")

    # Generate test assets
    assets_dir = LOG_DIR / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    audio_path = str(assets_dir / "voiceover.wav")
    _make_test_audio(audio_path)
    log(f"Generated test audio: {audio_path}")

    manifest = build_manifest(audio_path)
    manifest_json = json.dumps(manifest, indent=2)
    log(f"Manifest: {len(manifest['scenes'])} scenes, {manifest['duration']}s")

    output_path = str(LOG_DIR / "harness_output.mp4")

    try:
        from services.native_render_policy import render_semantic_documentary

        log("Calling render_semantic_documentary()...")
        result = render_semantic_documentary(
            manifest,
            output_path,
            {
                "name": "harness_test",
                "semantic_render_quality": "standard",
                "semantic_render_timeout_seconds": 120,
            },
        )
        elapsed = time.monotonic() - started
        output_size = Path(output_path).stat().st_size if Path(output_path).is_file() else 0

        metrics = {
            "success": True,
            "elapsed_seconds": round(elapsed, 1),
            "output_path": str(Path(output_path).resolve()),
            "output_size_bytes": output_size,
            "log_path": str(log_path),
            "scenes": len(manifest["scenes"]),
            "render_seconds": result.get("render_seconds"),
        }

        log(f"\n{'='*60}")
        log(f"SUCCESS")
        log(f"Duration: {elapsed:.1f}s")
        log(f"Output: {output_size / 1024:.0f} KB")
        log(f"Output: {output_path}")
        log(f"{'='*60}")

    except Exception as e:
        elapsed = time.monotonic() - started
        log(f"\nFAILED after {elapsed:.1f}s: {e}")
        import traceback
        traceback.print_exc(file=log_file)
        traceback.print_exc()
        metrics = {
            "success": False,
            "elapsed_seconds": round(elapsed, 1),
            "error": str(e),
            "log_path": str(log_path),
        }

    metrics_path = LOG_DIR / f"metrics_{int(started)}.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)
    log(f"Metrics: {metrics_path}")
    log_file.close()
    return metrics


if __name__ == "__main__":
    run_harness()
