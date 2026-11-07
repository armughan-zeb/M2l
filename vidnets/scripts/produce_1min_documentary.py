"""Exercise Flask's local /api/generate route with a one-minute Remotion job."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

os.environ.setdefault("VIDZEO_REMOTION_RENDER", "1")
os.environ.setdefault("VIDZEO_SEMANTIC_RENDER_REQUIRED", "1")
os.environ.setdefault("VIDZEO_ALLOW_LEGACY_RENDER_FALLBACK", "0")

SCRIPT = """In July 1969, the world watched as Apollo 11 carried three astronauts toward the Moon. Neil Armstrong, Buzz Aldrin, and Michael Collins launched from Kennedy Space Center atop a Saturn V rocket, the most powerful machine ever built.

The journey covered nearly a quarter million miles. While Collins orbited alone in the command module, Armstrong and Aldrin descended to the surface in the lunar module named Eagle.

On July 20, with just seconds of fuel remaining, Armstrong guided Eagle to a safe landing. He radioed back to Earth, "The Eagle has landed." Hours later, he became the first human to step onto another world.

Armstrong's words, "one small step for a man, one giant leap for mankind," were heard by half a billion people across the planet. The astronauts planted an American flag and collected lunar samples before returning to Collins.

Apollo 11 was not just a technical achievement. It was a moment when humanity saw itself from a distance for the first time, a small blue planet suspended in the blackness of space."""


def main() -> int:
    import app as vidzeo_app

    words = len(SCRIPT.split())
    print(f"[1min] words={words} (target ~60s at ~3 wps)", flush=True)

    config = {
        "type": "long",
        "name": "apollo_11_1min_flask_remotion_fast",
        "title": "Apollo 11: One Small Step",
        "script": SCRIPT,
        "image_source": "Web Images",
        "media_mode": "web_images",
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
        "render_backend": "remotion",
        "semantic_render_scale": 1.0,
        "semantic_render_crf": 18,
        "visual_understanding": False,
        "strict_mixed_percentages": False,
        "strict_retrieval_accuracy": True,
        "fail_on_unresolved_media": False,
        "auto_export": True,
        "semantic_edit_enable": True,
        "semantic_render_enabled": True,
        "semantic_theme": "history_archive",
        # Fast mode accelerates only H.264 encoding (ultrafast preset). Remotion
        # keeps 1080p and CRF 18, so source imagery and output dimensions remain
        # at the normal quality target.
        "fast_mode": True,
        "semantic_fast_render": True,
        "semantic_render_concurrency": 2,
    }

    print(f"[1min] Starting pipeline at {time.strftime('%H:%M:%S')}", flush=True)
    started = time.monotonic()

    # Use Flask's real generation route. Its local-development auth path is
    # active for Flask's loopback test client, so this exercises request
    # normalization, API-key defaults, media retrieval, and the Flask renderer
    # bridge together without creating a user session.
    response = vidzeo_app.app.test_client().post("/api/generate", json=config)
    result = response.get_json(silent=True) or {}
    if response.status_code != 200 or not result.get("success"):
        raise RuntimeError(
            f"Flask generation failed ({response.status_code}): "
            f"{result.get('error') or result.get('message') or 'no error details returned'}"
        )

    elapsed = time.monotonic() - started
    print(f"\n[1min] Pipeline complete in {elapsed:.0f}s", flush=True)

    render_metadata = result.get("render_metadata") or {}
    edit_validation = render_metadata.get("edit_plan_validation") or {}
    component_manifest = result.get("component_manifest") or {}
    remotion_manifest = component_manifest.get("remotion_manifest") or {}
    scenes = remotion_manifest.get("scenes") or []
    scene_count = edit_validation.get("scene_count") or len(scenes)
    video_path = result.get("video_path") or result.get("output_path") or ""
    print(f"[1min] Video: {video_path}", flush=True)
    print(f"[1min] Scenes: {scene_count}", flush=True)
    print(f"[1min] Renderer: {render_metadata.get('selected_backend', 'unknown')}", flush=True)
    print(
        f"[1min] Quality check: "
        f"{(render_metadata.get('quality_checks') or {}).get('status', 'unavailable')}",
        flush=True,
    )

    output = ROOT / "output" / "apollo_11_1min_manifest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"[1min] Manifest saved: {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
