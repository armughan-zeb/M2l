"""Produce a 1-minute documentary using Stock Media (Pexels/Pixabay videos).

Demonstrates the full editing system:
- LLM-driven editorial beats
- Smart router with VOX template selection (51 templates)
- Rich camera motions (19 types: zoom, pan, rotate, drift, etc.)
- 13 VOX animations
- 6 transition types
- 6 image treatments
- Anti-repetition with variation seed
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

os.environ.setdefault("VIDZEO_REMOTION_RENDER", "0")
os.environ.setdefault("VIDZEO_SEMANTIC_RENDER_REQUIRED", "1")
os.environ.setdefault("VIDZEO_ALLOW_LEGACY_RENDER_FALLBACK", "0")
os.environ.setdefault("VIDZEO_QWEN_VISION_MODEL", "qwen/qwen3.5-flash-02-23")
os.environ.setdefault("VIDZEO_QWEN_VISION_DEEP_MODEL", "qwen/qwen3.5-flash-02-23")
os.environ.setdefault("VIDZEO_QWEN_VISION_FALLBACK_MODEL", "qwen/qwen3.5-flash-02-23")
os.environ.setdefault("VIDZEO_LLM_MODEL", "qwen/qwen-2.5-7b-instruct")
os.environ.setdefault("VIDZEO_QUERY_LLM_MODEL", "qwen/qwen-2.5-7b-instruct")
os.environ.setdefault("VIDZEO_OPENROUTER_QUERY_MODEL", "qwen/qwen-2.5-7b-instruct")
os.environ.setdefault("VIDZEO_WEB_VISION_BUDGET", "10")

import time as _time
import random as _random
_RANDOM_SEED = int(_time.time()) + _random.randint(0, 100000)

SCRIPT = """In July 1969, the world watched as Apollo 11 carried three astronauts toward the Moon. Neil Armstrong, Buzz Aldrin, and Michael Collins launched from Kennedy Space Center atop a Saturn V rocket, the most powerful machine ever built.

The journey covered nearly a quarter million miles. While Collins orbited alone in the command module, Armstrong and Aldrin descended to the surface in the lunar module named Eagle.

On July 20, with just seconds of fuel remaining, Armstrong guided Eagle to a safe landing. He radioed back to Earth, The Eagle has landed. Hours later, he became the first human to step onto another world.

Armstrong's words, one small step for a man, one giant leap for mankind, were heard by half a billion people across the planet. The astronauts planted an American flag and collected lunar samples before returning to Collins.

Apollo 11 was not just a technical achievement. It was a moment when humanity saw itself from a distance for the first time, a small blue planet suspended in the blackness of space."""


def main() -> int:
    import app as vidzeo_app

    words = len(SCRIPT.split())
    print(f"[1min-stock] words={words} (target ~60s at ~3 wps)", flush=True)

    config = {
        "type": "long",
        "name": "apollo_11_1min_stock",
        "title": "Apollo 11: One Small Step",
        "script": SCRIPT,
        "image_source": "Stock Media",
        "scene_mode": "Every 2 Beats",
        "vo_mode": "Use Audiogram",
        "tts_model": "audiogram",
        "voice": "Adam (Male US)",
        "whisper_mode": "local",
        "captions_enabled": True,
        "caption_mode": "phrase",
        "words_per_group": 4,
        "quality": "standard",
        "resolution": "1080p",
        "visual_understanding": False,
        "strict_mixed_percentages": False,
        "strict_retrieval_accuracy": False,
        "fail_on_unresolved_media": False,
        "auto_export": True,
        "semantic_edit_enable": True,
        "semantic_render_enabled": True,
        "semantic_theme": "history_archive",
        "semantic_variation_seed": _RANDOM_SEED,
    }

    def progress(fraction: float, message: str):
        pct = int(fraction * 100)
        bar = "\u2588" * (pct // 5) + "\u2591" * (20 - pct // 5)
        print(f"\r  [{bar}] {pct:3d}% {message[:60]:60s}", end="", flush=True)
        if fraction >= 1.0:
            print()

    print(f"[1min-stock] Starting pipeline at {time.strftime('%H:%M:%S')}", flush=True)
    started = time.monotonic()

    result = vidzeo_app._run_video_pipeline(config, progress_callback=progress)

    elapsed = time.monotonic() - started
    print(f"\n[1min-stock] Pipeline complete in {elapsed:.0f}s", flush=True)

    manifest = result.get("semantic_edit_manifest") or {}
    scenes = manifest.get("scenes") or []
    video_path = result.get("video_path") or result.get("output_path") or ""
    print(f"[1min-stock] Video: {video_path}", flush=True)
    print(f"[1min-stock] Scenes: {len(scenes)}", flush=True)

    if scenes:
        print("\n[1min-stock] Editing breakdown:")
        print("  " + "-" * 70)
        for i, s in enumerate(scenes):
            g = s.get("graphic", {})
            vt = g.get("vox_template", "n/a")
            cam = s.get("camera", {}).get("motion", "n/a")
            tr = s.get("transition", {}).get("template_id", "cut")
            treat = s.get("design", {}).get("image_treatment", "hold")
            media_src = (s.get("media", {}).get("src") or "n/a")[-40:]
            print(f"  [{i+1:2d}] VOX={vt:<22} cam={cam:<18} trans={tr:<12} treat={treat:<16} {media_src}")
        print("  " + "-" * 70)

    output = ROOT / "output" / "apollo_11_1min_stock_manifest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"\n[1min-stock] Manifest saved: {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
